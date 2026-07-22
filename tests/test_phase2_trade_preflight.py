from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, futu_client_dependency
from alphapilot.db.migrate import run_migrations
from alphapilot.db.models import (
    AlertRecord,
    Base,
    ForecastSnapshot,
    Security,
    TradeProposalRecord,
)
from alphapilot.main import app


def _session_override(engine: Any) -> Any:
    def dependency() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    return dependency


def _alert(action: str) -> AlertRecord:
    now = datetime.now(UTC)
    return AlertRecord(
        symbol="600519",
        action=action,
        urgency="MEDIUM",
        confidence=0.8,
        suggested_position_change=0.1 if action in {"BUY_CANDIDATE", "ADD"} else -0.1,
        target_low=1_200,
        target_high=1_400,
        suggested_notional=(
            10_000
            if action in {"BUY_CANDIDATE", "ADD"}
            else -10_000
            if action in {"REDUCE", "EXIT", "STOP"}
            else 0
        ),
        reasons=["方向测试"],
        invalidation="方向失效",
        model_version="test-v1.0.0",
        as_of=now,
        expires_at=now + timedelta(days=2),
        created_at=now,
    )


def _forecast_for(alert: AlertRecord, *, provider: str = "baostock") -> ForecastSnapshot:
    assert alert.as_of is not None
    assert alert.model_version is not None
    return ForecastSnapshot(
        symbol=alert.symbol,
        as_of=alert.as_of,
        provider=provider,
        model_version=alert.model_version,
        horizons={},
        features={},
        created_at=alert.created_at,
    )


def _proposal(proposal_id: str, source_alert_id: int, side: str = "BUY") -> dict[str, Any]:
    return {
        "proposal_id": proposal_id,
        "idempotency_key": f"key-{proposal_id}",
        "symbol": "600519",
        "side": side,
        "quantity": 100,
        "estimated_notional": 10_000,
        "confidence": 0.8,
        "market_data_as_of": datetime.now(UTC).isoformat(),
        "model_version": "test-v1.0.0",
        "mode": "confirm_to_trade",
        "source_alert_id": source_alert_id,
    }


def _portfolio(*, current_position_pct: float = 0.0) -> dict[str, Any]:
    return {
        "equity": 1_000_000,
        "cash": 1_000_000,
        "daily_pnl_pct": 0.0,
        "current_position_pct": current_position_pct,
        "sector_position_pct": current_position_pct,
        "open_orders_for_symbol": 0,
    }


class ReadOnlySimulateClient:
    def __init__(self, *, positions: list[dict[str, Any]] | None = None) -> None:
        self.methods: list[str] = []
        self.positions = positions or []

    def trade_call(
        self,
        surface: str,
        method: str,
        args: Any = None,
        kwargs: Any = None,
        *,
        market: str | None = None,
        environment: str | None = None,
    ) -> dict[str, Any]:
        del args, kwargs
        assert surface == "security"
        assert market == "CN"
        assert environment == "SIMULATE"
        self.methods.append(method)
        if method == "get_acc_list":
            records = [
                {
                    "acc_id": 123,
                    "trd_env": "SIMULATE",
                    "sim_acc_type": "STOCK",
                    "acc_status": "ACTIVE",
                    "trdmarket_auth": ["CN"],
                }
            ]
        elif method == "accinfo_query":
            records = [{"total_assets": 1_000_000, "cash": 1_000_000, "market_val": 0}]
        elif method == "position_list_query":
            records = self.positions
        elif method == "order_list_query":
            records = []
        else:
            raise AssertionError(f"mutation or unexpected broker method: {method}")
        return {"data": {"records": records}}

    def quote_call_raw(self, method: str, args: Any = None, kwargs: Any = None) -> Any:
        raise AssertionError(f"preflight must not call quote or mutation method: {method}")


def test_evaluate_and_create_without_portfolio_use_only_read_queries(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'preflight.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        alert = _alert("BUY_CANDIDATE")
        session.add_all(
            [
                Security(symbol="600519", industry_csrc="白酒"),
                _forecast_for(alert),
                alert,
            ]
        )
        session.commit()
        alert_id = alert.id

    broker = ReadOnlySimulateClient()
    app.dependency_overrides[db_session_dependency] = _session_override(engine)
    app.dependency_overrides[futu_client_dependency] = lambda: broker
    try:
        with TestClient(app) as client:
            proposal = _proposal("stock-detail-buy-1", alert_id)
            evaluated = client.post(
                "/v1/trades/evaluate",
                json={
                    "proposal": proposal,
                    "portfolio": _portfolio(current_position_pct=0.99),
                },
            )
            created = client.post("/v1/trades/proposals", json={"proposal": proposal})
            duplicate = client.post("/v1/trades/proposals", json={"proposal": proposal})
            same_key = _proposal("stock-detail-buy-2", alert_id)
            same_key["idempotency_key"] = proposal["idempotency_key"]
            duplicate_key = client.post(
                "/v1/trades/proposals",
                json={"proposal": same_key},
            )

        assert evaluated.status_code == 200
        assert evaluated.json()["approved"] is True
        assert created.status_code == 200
        assert created.json()["proposal"]["status"] == "pending"
        assert duplicate.status_code == 409
        assert duplicate_key.status_code == 409
        assert "place_order" not in broker.methods
        assert set(broker.methods) <= {
            "get_acc_list",
            "accinfo_query",
            "position_list_query",
            "order_list_query",
        }
        with Session(engine) as session:
            records = session.scalars(select(TradeProposalRecord)).all()
            assert [record.proposal_id for record in records] == ["stock-detail-buy-1"]
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)
        app.dependency_overrides.pop(futu_client_dependency, None)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("mock", "行情来源 mock 不可用于交易提案"),
        ("expired", "已过期或缺少有效期"),
        ("confidence", "置信度与提案不一致"),
        ("model", "模型版本与提案不一致"),
        ("notional", "建议金额上限"),
        ("notional_direction", "建议金额方向与动作不一致"),
        ("target_range", "缺少有效目标区间"),
        ("missing_source", "必须绑定一条可审计的方向性提醒"),
    ],
)
def test_untrusted_or_tampered_alert_is_rejected_before_broker_queries(
    tmp_path: Path,
    case: str,
    expected: str,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / f'{case}.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        alert = _alert("BUY_CANDIDATE")
        if case == "expired":
            alert.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif case == "notional_direction":
            alert.suggested_notional = -10_000
        elif case == "target_range":
            alert.target_high = alert.target_low
        session.add_all(
            [
                Security(symbol="600519", industry_csrc="白酒"),
                _forecast_for(alert, provider="mock" if case == "mock" else "baostock"),
                alert,
            ]
        )
        session.commit()
        alert_id = alert.id

    proposal = _proposal(f"tamper-{case}", alert_id)
    if case == "confidence":
        proposal["confidence"] = 0.7
    elif case == "model":
        proposal["model_version"] = "tampered-model"
    elif case == "notional":
        proposal["estimated_notional"] = 11_000
    elif case == "missing_source":
        proposal.pop("source_alert_id")

    broker = ReadOnlySimulateClient()
    app.dependency_overrides[db_session_dependency] = _session_override(engine)
    app.dependency_overrides[futu_client_dependency] = lambda: broker
    try:
        with TestClient(app) as client:
            evaluated = client.post("/v1/trades/evaluate", json={"proposal": proposal})
            created = client.post("/v1/trades/proposals", json={"proposal": proposal})

        assert evaluated.status_code == 422
        assert expected in evaluated.json()["detail"]
        assert created.status_code == 422
        assert expected in created.json()["detail"]
        assert broker.methods == []
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)
        app.dependency_overrides.pop(futu_client_dependency, None)


def test_trade_proposal_idempotency_migration_backfills_and_enforces_unique_key(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-idempotency.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE securities (symbol TEXT PRIMARY KEY)"))
        connection.execute(
            text("CREATE TABLE screening_runs (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        )
        connection.execute(text("CREATE TABLE style_daily (trade_date DATE PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE alerts (id INTEGER PRIMARY KEY AUTOINCREMENT)"))
        connection.execute(
            text(
                "CREATE TABLE trade_proposals ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "proposal_id TEXT UNIQUE NOT NULL, proposal JSON NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO trade_proposals (proposal_id, proposal) "
                "VALUES (:first_id, :first_payload), (:second_id, :second_payload)"
            ),
            {
                "first_id": "legacy-first",
                "first_payload": json.dumps({"idempotency_key": "shared-key"}),
                "second_id": "legacy-second",
                "second_payload": json.dumps({"idempotency_key": "shared-key"}),
            },
        )

    applied = run_migrations(engine)
    assert "trade_proposals.idempotency_key" in applied
    assert "trade_proposals.idempotency_unique" in applied
    assert "idempotency_key" in {
        str(column["name"]) for column in inspect(engine).get_columns("trade_proposals")
    }
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT proposal_id, idempotency_key FROM trade_proposals ORDER BY id"
            )
        ).all()
    assert rows == [("legacy-first", "shared-key"), ("legacy-second", "legacy-second")]
    assert run_migrations(engine) == []
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO trade_proposals "
                "(proposal_id, proposal, idempotency_key) "
                "VALUES ('third', '{}', 'shared-key')"
            )
        )


def test_source_alert_direction_is_enforced_before_any_broker_query(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'direction.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        reduce_alert = _alert("REDUCE")
        hold_alert = _alert("HOLD")
        session.add_all(
            [
                Security(symbol="600519", industry_csrc="白酒"),
                _forecast_for(reduce_alert),
                reduce_alert,
                hold_alert,
            ]
        )
        session.commit()
        reduce_id = reduce_alert.id
        hold_id = hold_alert.id

    broker = ReadOnlySimulateClient(
        positions=[
            {
                "code": "SH.600519",
                "qty": 100,
                "market_val": 10_000,
                "today_pl_val": 0,
            }
        ]
    )

    app.dependency_overrides[db_session_dependency] = _session_override(engine)
    app.dependency_overrides[futu_client_dependency] = lambda: broker
    try:
        with TestClient(app) as client:
            mismatch = client.post(
                "/v1/trades/proposals",
                json={
                    "proposal": _proposal("direction-mismatch", reduce_id, "BUY"),
                    "portfolio": _portfolio(current_position_pct=0.1),
                },
            )
            nondirectional = client.post(
                "/v1/trades/proposals",
                json={
                    "proposal": _proposal("hold-not-actionable", hold_id, "BUY"),
                    "portfolio": _portfolio(current_position_pct=0.1),
                },
            )
            assert broker.methods == []
            matched = client.post(
                "/v1/trades/proposals",
                json={
                    "proposal": _proposal("direction-sell", reduce_id, "SELL"),
                    "portfolio": _portfolio(current_position_pct=0.1),
                },
            )

        assert mismatch.status_code == 422
        assert "要求 SELL" in mismatch.json()["detail"]
        assert nondirectional.status_code == 422
        assert "不是可生成交易提案" in nondirectional.json()["detail"]
        assert matched.status_code == 200
        assert matched.json()["proposal"]["side"] == "SELL"
        assert "position_list_query" in broker.methods
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)
        app.dependency_overrides.pop(futu_client_dependency, None)
