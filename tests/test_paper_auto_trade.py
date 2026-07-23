from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.db.models import (
    AlertRecord,
    Base,
    BrokerOrder,
    ForecastSnapshot,
    Security,
    TradeProposalRecord,
)
from alphapilot.futu.client import FutuClient
from alphapilot.jobs import paper_auto_trade as auto_job
from alphapilot.jobs.registry import JOBS
from alphapilot.services import executor
from alphapilot.services import watchlist as watchlist_service

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
MARKET_NOW = datetime(2026, 7, 23, 9, 35, tzinfo=MARKET_TIMEZONE)


class AutoPaperFutuStub:
    def __init__(
        self,
        *,
        last: float = 10.0,
        quote_time: datetime = MARKET_NOW,
    ) -> None:
        self.last = last
        self.quote_time = quote_time
        self.calls: list[dict[str, Any]] = []
        self.place_count = 0

    def quote_call_raw(
        self,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> object:
        del kwargs
        if method == "request_trading_days":
            assert args is not None and len(args) == 3
            value = str(args[1])
            assert args == ["CN", value, value]
            return [{"time": value}]
        assert method == "get_market_snapshot"
        assert args is not None
        return pd.DataFrame(
            [
                {
                    "code": code,
                    "last_price": self.last,
                    "prev_close_price": self.last,
                    "turnover": 0,
                    "is_suspended": False,
                    "update_time": self.quote_time.isoformat(),
                }
                for code in args[0]
            ]
        )

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
        self.calls.append(
            {
                "method": method,
                "args": args,
                "kwargs": dict(kwargs or {}),
                "environment": environment,
                "confirmation": confirmation,
            }
        )
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
        elif method in {"position_list_query", "order_list_query"}:
            records = []
        elif method == "place_order":
            assert kwargs is not None
            self.place_count += 1
            records = [
                {
                    "order_id": f"auto-paper-{self.place_count}",
                    "trd_env": environment,
                    "code": kwargs["code"],
                    "trd_side": kwargs["trd_side"],
                }
            ]
        else:
            raise AssertionError(f"unexpected Futu method: {method}")
        return {"ok": True, "data": {"records": records}}


def _as_futu(client: AutoPaperFutuStub) -> FutuClient:
    return cast(FutuClient, client)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    database = create_engine(
        f"sqlite:///{tmp_path / 'paper-auto.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(database)
    with Session(database) as session:
        session.add(Security(symbol="000333", industry_csrc="家用电器"))
        session.commit()
    return database


@pytest.fixture
def session_scope(engine: Engine) -> Any:
    @contextmanager
    def scope() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    return scope


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "default_data_provider": "mock",
        "futu_enable_trade_query": True,
        "futu_enable_trade": True,
        "paper_trading_enabled": True,
        "paper_auto_trading_enabled": True,
        "trading_mode": "paper_auto",
        "trading_halted": False,
        "live_trading_enabled": False,
        "min_trade_confidence": 0.68,
        "paper_auto_max_orders_per_day": 3,
        "paper_auto_max_order_notional_pct": 0.02,
        "max_market_data_age_seconds": 120,
    }
    values.update(overrides)
    return Settings(**values)


def _refresh_factory(now: datetime) -> Any:
    counter = 0

    def refresh(session: Session, provider: object) -> list[AlertRecord]:
        nonlocal counter
        del provider
        counter += 1
        source_time = now.astimezone(UTC)
        model_version = f"auto-paper-v{counter}"
        alert = AlertRecord(
            symbol="000333",
            action="BUY_CANDIDATE",
            urgency="HIGH",
            confidence=0.90,
            suggested_position_change=0.05,
            target_low=9.0,
            target_high=12.0,
            suggested_notional=50_000.0,
            reasons=["测试自动模拟交易"],
            model_version=model_version,
            as_of=source_time,
            expires_at=source_time + timedelta(days=1),
            created_at=source_time,
        )
        session.add_all(
            [
                ForecastSnapshot(
                    symbol="000333",
                    as_of=source_time,
                    provider="futu",
                    model_version=model_version,
                    horizons={},
                    features={},
                    created_at=source_time,
                ),
                alert,
            ]
        )
        session.flush()
        return [alert]

    return refresh


def test_auto_paper_trade_executes_once_per_symbol_and_never_uses_real(
    monkeypatch: pytest.MonkeyPatch,
    session_scope: Any,
) -> None:
    settings = _settings()
    client = AutoPaperFutuStub()
    monkeypatch.setattr(auto_job, "get_settings", lambda: settings)
    monkeypatch.setattr(auto_job, "get_session", session_scope)
    monkeypatch.setattr(watchlist_service, "refresh_alerts", _refresh_factory(MARKET_NOW))
    monkeypatch.setattr(executor, "get_settings", lambda: settings)

    first = auto_job.paper_auto_trade(
        client=_as_futu(client),
        provider=cast(Any, object()),
        now=MARKET_NOW,
    )
    second = auto_job.paper_auto_trade(
        client=_as_futu(client),
        provider=cast(Any, object()),
        now=MARKET_NOW + timedelta(minutes=5),
    )

    assert first["proposals_created"] == 1
    assert first["orders_submitted"] == 1, first["rejected"]
    assert first["skipped"] is None
    assert second["orders_submitted"] == 0
    assert second["skipped"] == "no_eligible_alerts"
    assert client.place_count == 1

    mutation = next(call for call in client.calls if call["method"] == "place_order")
    assert mutation["environment"] == "SIMULATE"
    assert mutation["confirmation"] is None
    assert all(call["environment"] == "SIMULATE" for call in client.calls)
    assert all(call["method"] != "unlock_trade" for call in client.calls)

    with session_scope() as session:
        proposal = session.scalar(
            select(TradeProposalRecord).where(
                TradeProposalRecord.mode == "paper_auto"
            )
        )
        order = session.scalar(select(BrokerOrder))
        assert proposal is not None
        assert proposal.source_alert_id is not None
        assert proposal.status == "executing"
        assert proposal.created_at == MARKET_NOW.astimezone(UTC).replace(tzinfo=None)
        assert proposal.risk_decision["requires_human_confirmation"] is False
        assert proposal.quantity == pytest.approx(1_900)
        assert order is not None
        assert order.environment == "SIMULATE"
        assert order.qty == pytest.approx(1_900)


@pytest.mark.parametrize(
    ("settings", "now", "expected"),
    [
        (_settings(paper_auto_trading_enabled=False), MARKET_NOW, "paper_auto_disabled"),
        (_settings(trading_mode="confirm_to_trade"), MARKET_NOW, "trading_mode_not_paper_auto"),
        (_settings(live_trading_enabled=True), MARKET_NOW, "live_trading_must_be_disabled"),
        (_settings(), MARKET_NOW.replace(hour=21), "outside_trading_session"),
    ],
)
def test_auto_paper_trade_fails_closed_before_network(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    now: datetime,
    expected: str,
) -> None:
    monkeypatch.setattr(auto_job, "get_settings", lambda: settings)

    result = auto_job.paper_auto_trade(
        client=cast(Any, object()),
        provider=cast(Any, object()),
        now=now,
    )

    assert result["skipped"] == expected
    assert result["alerts_created"] == 0
    assert result["orders_submitted"] == 0


def test_auto_paper_trade_rejects_stale_quote_without_order(
    monkeypatch: pytest.MonkeyPatch,
    session_scope: Any,
) -> None:
    settings = _settings(max_market_data_age_seconds=120)
    client = AutoPaperFutuStub(quote_time=MARKET_NOW - timedelta(minutes=10))
    monkeypatch.setattr(auto_job, "get_settings", lambda: settings)
    monkeypatch.setattr(auto_job, "get_session", session_scope)
    monkeypatch.setattr(watchlist_service, "refresh_alerts", _refresh_factory(MARKET_NOW))
    monkeypatch.setattr(executor, "get_settings", lambda: settings)

    result = auto_job.paper_auto_trade(
        client=_as_futu(client),
        provider=cast(Any, object()),
        now=MARKET_NOW,
    )

    assert result["orders_submitted"] == 0
    assert result["skipped"] == "no_executable_alerts"
    assert any("时差 600s" in warning for warning in result["warnings"])
    assert client.place_count == 0


def test_paper_auto_job_is_scheduler_gated() -> None:
    auto_job.register_paper_auto_trade_job()

    spec = JOBS["paper_auto_trade"]
    assert spec.enabled_key == "paper_auto_trading_enabled"
    assert spec.trigger is not None
