from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, futu_client_dependency
from alphapilot.api.routes import portfolio as portfolio_routes
from alphapilot.api.routes import trades
from alphapilot.core.config import Settings
from alphapilot.db.models import (
    AlertRecord,
    Base,
    BrokerOrder,
    ForecastSnapshot,
    PortfolioSnapshot,
    Security,
    TradeProposalRecord,
)
from alphapilot.futu.client import FutuClient
from alphapilot.jobs import order_sync
from alphapilot.services import executor
from alphapilot.services.executor import proposal_remark
from alphapilot.services.portfolio import (
    upsert_benchmark_close_bar,
    upsert_portfolio_snapshot,
)

FIRST_DAY = date(2026, 7, 21)
SECOND_DAY = date(2026, 7, 22)


class PaperFutuStub:
    """In-memory SIMULATE broker; it never opens a socket or calls OpenD."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.placed_order: dict[str, Any] | None = None

    def quote_call_raw(
        self,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        del kwargs
        assert method == "get_market_snapshot"
        assert args == [["SH.600000"]]
        return pd.DataFrame([{"code": "SH.600000", "last_price": 10.0, "is_suspended": False}])

    def trade_call(
        self,
        context_kind: str,
        method: str,
        *,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        market: str = "HK",
        environment: str = "SIMULATE",
        confirmation: str | None = None,
    ) -> dict[str, Any]:
        assert context_kind == "security"
        assert market == "CN"
        assert environment == "SIMULATE"
        assert confirmation is None
        call = {
            "method": method,
            "args": args,
            "kwargs": dict(kwargs or {}),
            "environment": environment,
            "confirmation": confirmation,
        }
        self.calls.append(call)

        if method == "get_acc_list":
            records = [
                {
                    "acc_id": 101,
                    "trd_env": "SIMULATE",
                    "sim_acc_type": "STOCK",
                    "acc_status": "ACTIVE",
                    "trdmarket_auth": ["CN"],
                }
            ]
        elif method == "accinfo_query":
            records = [{"total_assets": 1_000_000, "cash": 1_000_000, "market_val": 0}]
        elif method == "position_list_query":
            records = []
        elif method == "order_list_query":
            records = [] if self.placed_order is None else [dict(self.placed_order)]
        elif method == "place_order":
            assert kwargs is not None
            self.placed_order = {
                "order_id": "paper-e2e-1",
                "trd_env": "SIMULATE",
                "code": kwargs["code"],
                "trd_side": kwargs["trd_side"],
                "qty": kwargs["qty"],
                "remark": kwargs["remark"],
                "order_status": "FILLED_ALL",
                "dealt_qty": kwargs["qty"],
                "dealt_avg_price": kwargs["price"],
            }
            records = [dict(self.placed_order)]
        else:
            raise AssertionError(f"unexpected Futu method: {method}")
        return {"ok": True, "data": {"records": records}}


def _as_futu(client: PaperFutuStub) -> FutuClient:
    return cast(FutuClient, client)


def _benchmark(day: date, close: float) -> dict[str, Any]:
    return {
        "date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1_000.0,
        "amount": close * 1_000.0,
    }


def test_alert_to_filled_paper_order_and_next_day_attribution_is_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine: Engine = create_engine(
        f"sqlite:///{tmp_path / 'paper-e2e.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)

    @contextmanager
    def session_scope() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def api_session() -> Iterator[Session]:
        with session_scope() as session:
            yield session

    settings = Settings(
        futu_enable_trade_query=True,
        futu_enable_trade=True,
        paper_trading_enabled=True,
        trading_halted=False,
        live_trading_enabled=False,
    )
    broker = PaperFutuStub()
    monkeypatch.setattr(trades, "get_settings", lambda: settings)
    monkeypatch.setattr(executor, "get_settings", lambda: settings)
    monkeypatch.setattr(order_sync, "get_session", session_scope)

    app = FastAPI()
    app.include_router(trades.router)
    app.include_router(trades.orders_router)
    app.include_router(portfolio_routes.router)
    app.dependency_overrides[db_session_dependency] = api_session
    app.dependency_overrides[futu_client_dependency] = lambda: _as_futu(broker)

    with session_scope() as session:
        source_time = datetime.now(UTC)
        alert = AlertRecord(
            symbol="600000",
            action="BUY_CANDIDATE",
            urgency="HIGH",
                confidence=0.90,
                suggested_position_change=0.01,
                target_low=9.0,
                target_high=11.0,
                suggested_notional=1_010.0,
            reasons=["离线端到端提醒"],
            model_version="paper-e2e-v1",
            as_of=source_time,
            expires_at=source_time + timedelta(days=2),
            created_at=source_time,
        )
        session.add_all(
            [
                Security(symbol="600000", industry_csrc="J66货币金融服务"),
                ForecastSnapshot(
                    symbol="600000",
                    as_of=source_time,
                    provider="baostock",
                    model_version="paper-e2e-v1",
                    horizons={},
                    features={},
                    created_at=source_time,
                ),
                alert,
            ]
        )
        session.flush()
        alert_id = alert.id

    proposal_id = "paper-e2e-proposal"
    request = {
        "proposal": {
            "proposal_id": proposal_id,
            "idempotency_key": "paper-e2e-key",
            "symbol": "600000",
            "side": "BUY",
            "quantity": 100,
            "estimated_notional": 1_010,
            "confidence": 0.90,
            "market_data_as_of": datetime.now(UTC).isoformat(),
            "model_version": "paper-e2e-v1",
            "mode": "confirm_to_trade",
            "source_alert_id": alert_id,
        },
        "portfolio": {
            "equity": 1_000_000,
            "cash": 1_000_000,
            "daily_pnl_pct": 0,
            "current_position_pct": 0,
            "sector_position_pct": 0,
            "open_orders_for_symbol": 0,
        },
    }

    with TestClient(app) as api:
        missing_alert_request = {
            **request,
            "proposal": {
                **request["proposal"],
                "proposal_id": "paper-e2e-missing-alert",
                "idempotency_key": "paper-e2e-missing-alert",
                "source_alert_id": 999_999,
            },
        }
        missing_alert = api.post("/v1/trades/proposals", json=missing_alert_request)
        assert missing_alert.status_code == 422
        assert missing_alert.json()["detail"] == "来源提醒 999999 不存在。"

        wrong_symbol_request = {
            **request,
            "proposal": {
                **request["proposal"],
                "proposal_id": "paper-e2e-wrong-symbol",
                "idempotency_key": "paper-e2e-wrong-symbol",
                "symbol": "000001",
            },
        }
        wrong_symbol = api.post("/v1/trades/proposals", json=wrong_symbol_request)
        assert wrong_symbol.status_code == 422
        assert wrong_symbol.json()["detail"] == f"来源提醒 {alert_id} 与提案股票不一致。"

        created = api.post("/v1/trades/proposals", json=request)
        assert created.status_code == 200
        record_id = created.json()["proposal"]["id"]
        assert created.json()["proposal"]["source_alert_id"] == alert_id

        with session_scope() as session:
            record = session.get(TradeProposalRecord, record_id)
            assert record is not None
            assert record.proposal["source_alert_id"] == alert_id
            assert record.source_alert_id == alert_id

        approved = api.post(f"/v1/trades/proposals/{record_id}/approve")
        assert approved.status_code == 200
        assert approved.json()["proposal"]["status"] == "approved"

        executed = api.post(f"/v1/trades/proposals/{record_id}/execute")
        assert executed.status_code == 200
        assert executed.json()["order"]["status"] == "submitted"
        assert executed.json()["order"]["environment"] == "SIMULATE"

        sync_stats = order_sync.sync_orders(_as_futu(broker))
        assert sync_stats["filled"] == 1
        assert sync_stats["query_errors"] == 0

        with session_scope() as session:
            order = session.scalar(
                select(BrokerOrder).where(BrokerOrder.proposal_id == proposal_id)
            )
            proposal = session.get(TradeProposalRecord, record_id)
            assert order is not None
            assert proposal is not None
            assert order.status == "filled"
            assert order.filled_qty == pytest.approx(100)
            assert order.avg_fill_price == pytest.approx(10.10)
            assert proposal.status == "executed"
            assert proposal.source_alert_id == alert_id
            assert session.get(AlertRecord, proposal.source_alert_id) is not None

            upsert_benchmark_close_bar(
                session,
                FIRST_DAY,
                _benchmark(FIRST_DAY, 100.0),
                source="offline-e2e",
            )
            first, _ = upsert_portfolio_snapshot(
                session,
                FIRST_DAY,
                {"total_assets": 1_000_000.0, "cash": 998_990.0, "market_val": 1_010.0},
                [{"symbol": "600000", "qty": 100.0, "market_val": 1_010.0}],
            )
            upsert_benchmark_close_bar(
                session,
                SECOND_DAY,
                _benchmark(SECOND_DAY, 101.0),
                source="offline-e2e",
            )
            second, _ = upsert_portfolio_snapshot(
                session,
                SECOND_DAY,
                {"total_assets": 1_000_020.0, "cash": 998_990.0, "market_val": 1_030.0},
                [{"symbol": "600000", "qty": 100.0, "market_val": 1_030.0}],
            )
            assert first.positions[0]["mv"] == pytest.approx(1_010.0)
            assert second.positions[0]["mv"] == pytest.approx(1_030.0)
            assert second.daily_return == pytest.approx(0.00002)

        attribution = api.get("/v1/portfolio/attribution?days=2")

    assert attribution.status_code == 200
    body = attribution.json()
    assert body["dates"] == [FIRST_DAY.isoformat(), SECOND_DAY.isoformat()]
    assert body["nav"] == pytest.approx([1.0, 1.00002])
    assert body["benchmark_nav"] == pytest.approx([1.0, 1.01])
    assert body["excess_cum"] == pytest.approx(1.00002 / 1.01 - 1.0)

    mutation_calls = [call for call in broker.calls if call["method"] == "place_order"]
    assert len(mutation_calls) == 1
    assert mutation_calls[0]["environment"] == "SIMULATE"
    assert mutation_calls[0]["confirmation"] is None
    assert mutation_calls[0]["kwargs"]["remark"] == proposal_remark(proposal_id)
    assert all(call["environment"] == "SIMULATE" for call in broker.calls)
    assert all(call["method"] != "unlock_trade" for call in broker.calls)

    with Session(engine) as session:
        snapshots = session.scalars(
            select(PortfolioSnapshot).order_by(PortfolioSnapshot.trade_date)
        ).all()
        assert [snapshot.trade_date for snapshot in snapshots] == [FIRST_DAY, SECOND_DAY]
