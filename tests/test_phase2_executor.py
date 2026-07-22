from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, futu_client_dependency
from alphapilot.api.routes import trades
from alphapilot.core.config import Settings
from alphapilot.db.models import Base, BrokerOrder, Security, TradeProposalRecord
from alphapilot.futu.client import FutuClient, FutuSDKError, FutuUnavailableError
from alphapilot.services import executor
from alphapilot.services.runtime_flags import set_trading_halted


class StubExecutionClient:
    def __init__(
        self,
        *,
        last: float = 10.0,
        positions: list[dict[str, Any]] | None = None,
        orders: list[dict[str, Any]] | None = None,
        place_error: Exception | None = None,
        place_record: dict[str, Any] | None = None,
    ) -> None:
        self.last = last
        self.positions = positions or []
        self.orders = orders or []
        self.place_error = place_error
        self.place_record = place_record
        self.calls: list[dict[str, Any]] = []
        self.place_count = 0

    def quote_call_raw(
        self,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        del kwargs
        assert method == "get_market_snapshot"
        assert args is not None
        return pd.DataFrame([{"code": args[0][0], "last_price": self.last, "is_suspended": False}])

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
        call = {
            "context_kind": context_kind,
            "method": method,
            "args": args,
            "kwargs": kwargs,
            "market": market,
            "environment": environment,
            "confirmation": confirmation,
        }
        self.calls.append(call)
        if method == "place_order":
            self.place_count += 1
            if self.place_error is not None:
                raise self.place_error
            assert kwargs is not None
            records = [
                self.place_record
                or {
                    "order_id": "paper-order-1",
                    "trd_env": environment,
                    "code": kwargs["code"],
                    "trd_side": kwargs["trd_side"],
                }
            ]
        elif method == "get_acc_list":
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
            records = self.positions
        elif method == "order_list_query":
            records = self.orders
        else:
            raise AssertionError(f"unexpected method: {method}")
        return {"ok": True, "data": {"records": records}}


class ReconcilesDuringPlaceClient(StubExecutionClient):
    def __init__(self, engine: Engine, proposal_id: str) -> None:
        super().__init__()
        self.engine = engine
        self.proposal_id = proposal_id

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
        response = super().trade_call(
            context_kind,
            method,
            args=args,
            kwargs=kwargs,
            market=market,
            environment=environment,
            confirmation=confirmation,
        )
        if method == "place_order":
            with Session(self.engine) as reconciliation:
                order = reconciliation.scalar(
                    select(BrokerOrder).where(BrokerOrder.proposal_id == self.proposal_id)
                )
                proposal = reconciliation.scalar(
                    select(TradeProposalRecord).where(
                        TradeProposalRecord.proposal_id == self.proposal_id
                    )
                )
                assert order is not None and proposal is not None
                order.futu_order_id = "paper-order-1"
                order.status = "filled"
                order.filled_qty = order.qty
                order.avg_fill_price = 10.05
                proposal.status = "executed"
                reconciliation.commit()
        return response


def _as_futu(client: StubExecutionClient) -> FutuClient:
    return cast(FutuClient, client)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    database = create_engine(
        f"sqlite:///{tmp_path / 'executor.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(database)
    with Session(database) as session:
        session.add(Security(symbol="600000", industry_csrc="银行"))
        session.commit()
    return database


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "futu_enable_trade_query": True,
        "futu_enable_trade": True,
        "paper_trading_enabled": True,
        "trading_halted": False,
        "live_trading_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def _record(
    session: Session,
    *,
    proposal_id: str = "proposal-1",
    status: str = "approved",
    side: str = "BUY",
    quantity: float = 100.0,
    estimated_notional: float = 1_000.0,
    mode: str = "confirm_to_trade",
    as_of: datetime | None = None,
) -> TradeProposalRecord:
    payload = {
        "proposal_id": proposal_id,
        "idempotency_key": f"key-{proposal_id}",
        "symbol": "600000",
        "side": side,
        "quantity": quantity,
        "estimated_notional": estimated_notional,
        "confidence": 0.9,
        "market_data_as_of": (as_of or datetime.now(UTC)).isoformat(),
        "model_version": "fixture-v1.0.0",
        "mode": mode,
    }
    record = TradeProposalRecord(
        proposal_id=proposal_id,
        symbol="600000",
        side=side,
        quantity=quantity,
        estimated_notional=estimated_notional,
        confidence=0.9,
        mode=mode,
        status=status,
        proposal=payload,
        risk_decision={"approved": True},
    )
    session.add(record)
    session.commit()
    return record


def test_execute_submits_simulate_order_with_live_risk_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    settings = _settings(live_trading_enabled=True)
    monkeypatch.setattr(executor, "get_settings", lambda: settings)
    record = _record(session, quantity=250.0, estimated_notional=1.0)
    client = StubExecutionClient(last=10.0)

    first = executor.execute_proposal(session, _as_futu(client), record)
    second = executor.execute_proposal(session, _as_futu(client), record)

    assert first.id == second.id
    assert first.status == "submitted"
    assert first.environment == "SIMULATE"
    assert first.qty == pytest.approx(200.0)
    assert first.price == pytest.approx(10.10)
    assert first.futu_order_id == "paper-order-1"
    assert record.status == "executing"
    assert client.place_count == 1
    place_call = next(call for call in client.calls if call["method"] == "place_order")
    assert place_call["environment"] == "SIMULATE"
    assert place_call["confirmation"] is None
    assert place_call["kwargs"]["order_type"] == "NORMAL"
    assert place_call["kwargs"]["qty"] == pytest.approx(200.0)
    assert len(str(place_call["kwargs"]["remark"]).encode("utf-8")) <= 64
    assert all(call["environment"] == "SIMULATE" for call in client.calls)


def test_reconciliation_during_place_response_never_regresses_terminal_order(
    monkeypatch: pytest.MonkeyPatch,
    engine: Engine,
) -> None:
    monkeypatch.setattr(executor, "get_settings", lambda: _settings())
    proposal_id = "reconciled-during-place"
    with Session(engine, expire_on_commit=False) as session:
        record = _record(session, proposal_id=proposal_id)
        client = ReconcilesDuringPlaceClient(engine, proposal_id)

        order = executor.execute_proposal(session, _as_futu(client), record)

        assert order.status == "filled"
        assert order.futu_order_id == "paper-order-1"
        assert order.filled_qty == pytest.approx(order.qty)
        assert order.avg_fill_price == pytest.approx(10.05)
        assert record.status == "executed"
        assert client.place_count == 1


@pytest.mark.parametrize(
    "override",
    [
        {"paper_trading_enabled": False},
        {"futu_enable_trade": False},
        {"futu_enable_trade_query": False},
    ],
)
def test_execute_requires_all_paper_switches_before_any_client_call(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    override: dict[str, bool],
) -> None:
    monkeypatch.setattr(executor, "get_settings", lambda: _settings(**override))
    record = _record(session, proposal_id=f"switch-{next(iter(override))}")
    client = StubExecutionClient()

    with pytest.raises(executor.ExecutionBlocked):
        executor.execute_proposal(session, _as_futu(client), record)

    assert client.calls == []


def test_execute_halt_is_a_distinct_block_and_never_returns_existing_order(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    monkeypatch.setattr(executor, "get_settings", lambda: _settings())
    set_trading_halted(session, True)
    session.commit()
    record = _record(session, proposal_id="halted", status="executing")
    session.add(
        BrokerOrder(
            proposal_id=record.proposal_id,
            symbol="600000",
            side="BUY",
            price=10.0,
            qty=100.0,
            status="submitted",
            futu_order_id="existing",
        )
    )
    session.commit()
    client = StubExecutionClient()

    with pytest.raises(executor.ExecutionBlocked) as captured:
        executor.execute_proposal(session, _as_futu(client), record)

    assert captured.value.halted is True
    assert client.calls == []


@pytest.mark.parametrize(
    ("status", "mode"),
    [("pending", "confirm_to_trade"), ("approved", "limited_live_auto")],
)
def test_execute_rejects_invalid_state_or_live_mode_before_order(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
    status: str,
    mode: str,
) -> None:
    monkeypatch.setattr(executor, "get_settings", lambda: _settings())
    record = _record(session, proposal_id=f"invalid-{status}-{mode}", status=status, mode=mode)
    client = StubExecutionClient()

    with pytest.raises(executor.ExecutionConflict):
        executor.execute_proposal(session, _as_futu(client), record)

    assert client.place_count == 0


def test_execution_risk_uses_live_limit_not_reported_notional_and_persists_failure(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    monkeypatch.setattr(executor, "get_settings", lambda: _settings())
    record = _record(session, proposal_id="understated", estimated_notional=1.0)
    client = StubExecutionClient(last=2_000.0)

    with pytest.raises(executor.ExecutionConflict, match="执行前风控未通过"):
        executor.execute_proposal(session, _as_futu(client), record)

    order = session.scalar(select(BrokerOrder).where(BrokerOrder.proposal_id == "understated"))
    assert order is not None
    assert order.status == "failed"
    assert order.price == pytest.approx(2_020.0)
    assert record.status == "exec_failed"
    assert client.place_count == 0


def test_execute_rejects_stale_signal_and_existing_futu_order(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    monkeypatch.setattr(executor, "get_settings", lambda: _settings())
    stale = _record(
        session,
        proposal_id="stale",
        as_of=datetime.now(UTC) - timedelta(minutes=10),
    )
    client = StubExecutionClient(orders=[{"code": "SH.600000", "order_status": "SUBMITTED"}])

    with pytest.raises(executor.ExecutionConflict, match="执行前风控未通过"):
        executor.execute_proposal(session, _as_futu(client), stale)

    assert client.place_count == 0
    assert "行情数据过期" in str(
        session.scalar(select(BrokerOrder.error).where(BrokerOrder.proposal_id == "stale"))
    )


def test_fill_cancelled_futu_order_is_terminal_and_does_not_block_new_proposal(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    monkeypatch.setattr(executor, "get_settings", lambda: _settings())
    record = _record(session, proposal_id="after-fill-cancelled")
    client = StubExecutionClient(orders=[{"code": "SH.600000", "order_status": "FILL_CANCELLED"}])

    order = executor.execute_proposal(session, _as_futu(client), record)

    assert order.status == "submitted"
    assert client.place_count == 1


def test_sell_rounding_uses_bid_side_limit_and_checks_available_quantity(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    monkeypatch.setattr(executor, "get_settings", lambda: _settings())
    position = {
        "code": "SH.600000",
        "qty": 300,
        "market_val": 3_000,
        "today_pl_val": 0,
    }
    record = _record(session, proposal_id="sell", side="SELL", quantity=250)
    client = StubExecutionClient(last=10.0, positions=[position])

    order = executor.execute_proposal(session, _as_futu(client), record)

    assert order.qty == pytest.approx(200.0)
    assert order.price == pytest.approx(9.90)
    place_call = next(call for call in client.calls if call["method"] == "place_order")
    assert place_call["kwargs"]["trd_side"] == "SELL"


def test_uncertain_place_result_stays_submitting_and_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    monkeypatch.setattr(executor, "get_settings", lambda: _settings())
    record = _record(session, proposal_id="uncertain")
    client = StubExecutionClient(place_error=FutuUnavailableError("connection lost"))

    with pytest.raises(executor.ExecutionUnavailable, match="禁止自动重试"):
        executor.execute_proposal(session, _as_futu(client), record)
    existing = session.scalar(select(BrokerOrder).where(BrokerOrder.proposal_id == "uncertain"))
    assert existing is not None
    assert existing.status == "submitting"
    assert record.status == "executing"

    replay = executor.execute_proposal(session, _as_futu(client), record)
    assert replay.id == existing.id
    assert client.place_count == 1


def test_definite_futu_rejection_is_persisted_as_failed(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    monkeypatch.setattr(executor, "get_settings", lambda: _settings())
    record = _record(session, proposal_id="rejected")
    client = StubExecutionClient(place_error=FutuSDKError("price rejected"))

    with pytest.raises(executor.ExecutionRejected, match="富途拒绝"):
        executor.execute_proposal(session, _as_futu(client), record)

    order = session.scalar(select(BrokerOrder).where(BrokerOrder.proposal_id == "rejected"))
    assert order is not None
    assert order.status == "failed"
    assert record.status == "exec_failed"


def test_concurrent_execute_uses_one_reservation_and_one_place_call(
    monkeypatch: pytest.MonkeyPatch,
    engine: Engine,
) -> None:
    monkeypatch.setattr(executor, "get_settings", lambda: _settings())
    with Session(engine, expire_on_commit=False) as setup:
        record = _record(setup, proposal_id="concurrent")
        record_id = record.id
    client = StubExecutionClient()

    def run() -> int:
        with Session(engine, expire_on_commit=False) as thread_session:
            thread_record = thread_session.get(TradeProposalRecord, record_id)
            assert thread_record is not None
            return executor.execute_proposal(
                thread_session,
                _as_futu(client),
                thread_record,
            ).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        order_ids = list(pool.map(lambda _: run(), range(2)))

    assert order_ids[0] == order_ids[1]
    assert client.place_count == 1
    with Session(engine) as verify:
        assert verify.scalar(select(func.count(BrokerOrder.id))) == 1


def test_execute_and_orders_http_endpoints_return_persisted_simulate_order(
    monkeypatch: pytest.MonkeyPatch,
    engine: Engine,
) -> None:
    settings = _settings()
    monkeypatch.setattr(executor, "get_settings", lambda: settings)
    monkeypatch.setattr(trades, "get_settings", lambda: settings)
    broker_client = StubExecutionClient()
    app = FastAPI()
    app.include_router(trades.router)
    app.include_router(trades.orders_router)

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as database_session:
            try:
                yield database_session
                database_session.commit()
            except Exception:
                database_session.rollback()
                raise

    app.dependency_overrides[db_session_dependency] = override_session
    app.dependency_overrides[futu_client_dependency] = lambda: _as_futu(broker_client)
    proposal = {
        "proposal": {
            "proposal_id": "api-proposal",
            "idempotency_key": "api-key",
            "symbol": "600000",
            "side": "BUY",
            "quantity": 100,
            "estimated_notional": 1_010,
            "confidence": 0.9,
            "market_data_as_of": datetime.now(UTC).isoformat(),
            "model_version": "fixture-v1.0.0",
            "mode": "confirm_to_trade",
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
        created = api.post("/v1/trades/proposals", json=proposal)
        assert created.status_code == 200
        record_id = created.json()["proposal"]["id"]
        pending_execute = api.post(f"/v1/trades/proposals/{record_id}/execute")
        assert pending_execute.status_code == 409
        approved = api.post(f"/v1/trades/proposals/{record_id}/approve")
        assert approved.status_code == 200
        assert approved.json()["proposal"]["status"] == "approved"

        executed = api.post(f"/v1/trades/proposals/{record_id}/execute")
        assert executed.status_code == 200
        body = executed.json()["order"]
        assert body["status"] == "submitted"
        assert body["environment"] == "SIMULATE"
        assert body["futu_order_id"] == "paper-order-1"
        assert body["created_at"].endswith("+00:00")

        replay = api.post(f"/v1/trades/proposals/{record_id}/execute")
        assert replay.status_code == 200
        assert replay.json()["order"]["id"] == body["id"]
        late_reject = api.post(f"/v1/trades/proposals/{record_id}/reject")
        assert late_reject.status_code == 409
        assert "executing" in late_reject.json()["detail"]
        halt_response = api.post("/v1/trades/halt")
        assert halt_response.status_code == 200
        assert halt_response.json()["trading_halted"] is True
        halted = api.post(f"/v1/trades/proposals/{record_id}/execute")
        assert halted.status_code == 423
        resume_response = api.post("/v1/trades/resume")
        assert resume_response.status_code == 200
        assert resume_response.json()["trading_halted"] is False
        settings.paper_trading_enabled = False
        disabled = api.post(f"/v1/trades/proposals/{record_id}/execute")
        assert disabled.status_code == 403
        settings.paper_trading_enabled = True
        listed = api.get("/v1/orders")
        detail = api.get(f"/v1/orders/{body['id']}")

    assert listed.status_code == 200
    assert listed.json()["orders"][0]["id"] == body["id"]
    assert detail.status_code == 200
    assert detail.json()["order"]["proposal_id"] == "api-proposal"
    assert broker_client.place_count == 1
