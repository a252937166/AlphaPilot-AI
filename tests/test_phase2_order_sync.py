from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from alphapilot.db.models import Base, BrokerOrder, TradeProposalRecord
from alphapilot.futu.client import FutuClient, FutuUnavailableError
from alphapilot.jobs import order_sync
from alphapilot.jobs.registry import JOBS, JobExecutionError
from alphapilot.services.executor import proposal_remark


class TrackedSessionFactory:
    def __init__(self, path: Path) -> None:
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(self.engine)
        self.active = 0

    @contextmanager
    def __call__(self) -> Iterator[Session]:
        with Session(self.engine, expire_on_commit=False) as session:
            self.active += 1
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                self.active -= 1


class StubOrderClient:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        session_factory: TrackedSessionFactory,
        error_order_ids: set[str] | None = None,
    ) -> None:
        self.rows = rows
        self.session_factory = session_factory
        self.error_order_ids = error_order_ids or set()
        self.calls: list[dict[str, Any]] = []

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
        assert self.session_factory.active == 0, "network call must not hold a DB session"
        assert method == "order_list_query"
        assert kwargs is not None
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
        order_id = str(kwargs.get("order_id") or "")
        if order_id in self.error_order_ids:
            raise FutuUnavailableError("fixture timeout")
        rows = (
            self.rows
            if not order_id
            else [row for row in self.rows if str(row.get("order_id")) == order_id]
        )
        return {"ok": True, "data": {"records": rows}}


def _as_futu(client: StubOrderClient) -> FutuClient:
    return cast(FutuClient, client)


def _proposal(proposal_id: str) -> TradeProposalRecord:
    return TradeProposalRecord(
        proposal_id=proposal_id,
        symbol="600000",
        side="BUY",
        quantity=100.0,
        estimated_notional=1_000.0,
        confidence=0.9,
        mode="confirm_to_trade",
        status="executing",
        proposal={},
        risk_decision={},
    )


def _order(
    proposal_id: str,
    *,
    futu_order_id: str | None = None,
    status: str = "submitted",
    filled_qty: float = 0.0,
) -> BrokerOrder:
    return BrokerOrder(
        proposal_id=proposal_id,
        futu_order_id=futu_order_id or f"order-{proposal_id}",
        symbol="600000",
        side="BUY",
        order_type="MARKET",
        price=10.0,
        qty=100.0,
        status=status,
        filled_qty=filled_qty,
        avg_fill_price=10.0 if filled_qty > 0 else None,
    )


def _uncertain_order(proposal_id: str) -> BrokerOrder:
    order = _order(proposal_id, status="submitting")
    order.futu_order_id = None
    return order


def _row(
    proposal_id: str,
    status: str,
    *,
    order_id: str | None = None,
    code: str = "SH.600000",
    side: str = "BUY",
    qty: object = 100.0,
    dealt_qty: object = 0.0,
    dealt_avg_price: object | None = 0.0,
    remark: str | None = None,
) -> dict[str, Any]:
    return {
        "order_id": order_id or f"order-{proposal_id}",
        "code": code,
        "trd_side": side,
        "trd_env": None,
        "qty": qty,
        "order_status": status,
        "dealt_qty": dealt_qty,
        "dealt_avg_price": dealt_avg_price,
        "remark": remark if remark is not None else proposal_remark(proposal_id),
    }


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    session_factory: TrackedSessionFactory,
) -> list[int]:
    account_calls: list[int] = []
    monkeypatch.setattr(order_sync, "get_session", session_factory)

    def account(_client: FutuClient) -> dict[str, Any]:
        assert session_factory.active == 0
        account_calls.append(1)
        return {"acc_id": 101, "environment": "SIMULATE"}

    monkeypatch.setattr(order_sync, "get_simulate_account", account)
    return account_calls


def _seed(
    session_factory: TrackedSessionFactory,
    *proposal_ids: str,
    orders: list[BrokerOrder] | None = None,
) -> None:
    with session_factory() as session:
        session.add_all(_proposal(proposal_id) for proposal_id in proposal_ids)
        if orders is None:
            session.add_all(_order(proposal_id) for proposal_id in proposal_ids)
        else:
            session.add_all(orders)


@pytest.mark.parametrize(
    ("futu_status", "local_status"),
    [
        ("UNSUBMITTED", "submitting"),
        ("WAITING_SUBMIT", "submitting"),
        ("SUBMITTING", "submitting"),
        ("TIMEOUT", "submitting"),
        ("SUBMITTED", "submitted"),
        ("CANCELLING_ALL", "submitted"),
        ("FILLED_PART", "partial"),
        ("CANCELLING_PART", "partial"),
        ("FILLED_ALL", "filled"),
        ("CANCELLED_PART", "cancelled"),
        ("CANCELLED_ALL", "cancelled"),
        ("SUBMIT_FAILED", "failed"),
        ("FAILED", "failed"),
        ("DISABLED", "failed"),
        ("DELETED", "failed"),
        ("FILL_CANCELLED", "failed"),
    ],
)
def test_all_supported_futu_statuses_are_explicitly_mapped(
    futu_status: str,
    local_status: str,
) -> None:
    assert order_sync.FUTU_ORDER_STATUS_MAP[futu_status] == local_status


def test_sync_filled_order_is_atomic_idempotent_and_uses_only_simulate_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sessions = TrackedSessionFactory(tmp_path / "filled.db")
    _seed(sessions, "filled")
    client = StubOrderClient(
        [_row("filled", "FILLED_ALL", dealt_qty=100, dealt_avg_price=10.08)],
        session_factory=sessions,
    )
    _wire(monkeypatch, sessions)

    first = order_sync.sync_orders(_as_futu(client))
    second = order_sync.sync_orders(_as_futu(client))

    assert first["filled"] == 1
    assert first["updated"] == 1
    assert second["active"] == 0
    assert len(client.calls) == 1
    assert client.calls[0] == {
        "context_kind": "security",
        "method": "order_list_query",
        "args": None,
        "kwargs": {"acc_id": 101, "order_id": "order-filled", "refresh_cache": True},
        "market": "CN",
        "environment": "SIMULATE",
        "confirmation": None,
    }
    with sessions() as session:
        order = session.scalar(select(BrokerOrder).where(BrokerOrder.proposal_id == "filled"))
        proposal = session.scalar(
            select(TradeProposalRecord).where(TradeProposalRecord.proposal_id == "filled")
        )
        assert order is not None and proposal is not None
        assert (order.status, order.filled_qty, order.avg_fill_price) == (
            "filled",
            100.0,
            10.08,
        )
        assert proposal.status == "executed"


def test_uncertain_submission_recovers_by_unique_remark_without_resubmitting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sessions = TrackedSessionFactory(tmp_path / "recover.db")
    _seed(sessions, "uncertain", orders=[_uncertain_order("uncertain")])
    client = StubOrderClient(
        [
            _row(
                "uncertain",
                "FILLED_PART",
                order_id="recovered-7",
                dealt_qty=40,
                dealt_avg_price=10.05,
            )
        ],
        session_factory=sessions,
    )
    _wire(monkeypatch, sessions)

    stats = order_sync.sync_orders(_as_futu(client))

    assert stats["recovered"] == 1
    assert stats["partial"] == 1
    assert len(client.calls) == 1
    assert "order_id" not in client.calls[0]["kwargs"]
    with sessions() as session:
        order = session.scalar(select(BrokerOrder).where(BrokerOrder.proposal_id == "uncertain"))
        assert order is not None
        assert (order.futu_order_id, order.status, order.filled_qty) == (
            "recovered-7",
            "partial",
            40.0,
        )


@pytest.mark.parametrize(
    ("futu_status", "dealt_qty", "avg", "order_status", "proposal_status"),
    [
        ("CANCELLED_PART", 20.0, 10.01, "cancelled", "executed"),
        ("CANCELLED_ALL", 0.0, 0.0, "cancelled", "exec_failed"),
        ("FAILED", 0.0, 0.0, "failed", "exec_failed"),
        ("FILL_CANCELLED", 0.0, 0.0, "failed", "exec_failed"),
    ],
)
def test_terminal_statuses_link_proposal_without_permanent_polling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    futu_status: str,
    dealt_qty: float,
    avg: float,
    order_status: str,
    proposal_status: str,
) -> None:
    proposal_id = futu_status.lower()
    sessions = TrackedSessionFactory(tmp_path / f"{proposal_id}.db")
    _seed(sessions, proposal_id)
    client = StubOrderClient(
        [
            _row(
                proposal_id,
                futu_status,
                dealt_qty=dealt_qty,
                dealt_avg_price=avg,
            )
        ],
        session_factory=sessions,
    )
    _wire(monkeypatch, sessions)

    stats = order_sync.sync_orders(_as_futu(client))

    assert stats[order_status] == 1
    with sessions() as session:
        order = session.scalar(select(BrokerOrder).where(BrokerOrder.proposal_id == proposal_id))
        proposal = session.scalar(
            select(TradeProposalRecord).where(TradeProposalRecord.proposal_id == proposal_id)
        )
        assert order is not None and proposal is not None
        assert order.status == order_status
        assert proposal.status == proposal_status


@pytest.mark.parametrize(
    "row_overrides",
    [
        {"code": "SZ.000001"},
        {"trd_side": "SELL"},
        {"qty": 200.0},
        {"remark": "another-system"},
    ],
)
def test_identity_mismatch_never_mutates_local_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    row_overrides: dict[str, Any],
) -> None:
    sessions = TrackedSessionFactory(tmp_path / "identity.db")
    _seed(sessions, "identity")
    row = _row("identity", "FILLED_ALL", dealt_qty=100, dealt_avg_price=10.0)
    row.update(row_overrides)
    client = StubOrderClient([row], session_factory=sessions)
    _wire(monkeypatch, sessions)

    stats = order_sync.sync_orders(_as_futu(client))

    assert stats["updated"] == 0
    assert stats["warning_count"] == 1
    with sessions() as session:
        order = session.scalar(select(BrokerOrder).where(BrokerOrder.proposal_id == "identity"))
        assert order is not None
        assert (order.status, order.filled_qty) == ("submitted", 0.0)


@pytest.mark.parametrize(
    "row",
    [
        _row("invalid", "FILLED_PART", dealt_qty=float("nan"), dealt_avg_price=10.0),
        _row("invalid", "FILLED_PART", dealt_qty=-1, dealt_avg_price=10.0),
        _row("invalid", "FILLED_PART", dealt_qty=101, dealt_avg_price=10.0),
        _row("invalid", "FILLED_PART", dealt_qty=20, dealt_avg_price=0),
        _row("invalid", "FILLED_PART", dealt_qty=0, dealt_avg_price=0),
        _row("invalid", "FILLED_PART", dealt_qty=100, dealt_avg_price=10.0),
        _row("invalid", "CANCELLING_PART", dealt_qty=0, dealt_avg_price=0),
        _row("invalid", "CANCELLING_PART", dealt_qty=100, dealt_avg_price=10.0),
        _row("invalid", "FILLED_ALL", dealt_qty=99, dealt_avg_price=10.0),
    ],
)
def test_invalid_fill_data_is_warned_and_never_persisted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    row: dict[str, Any],
) -> None:
    sessions = TrackedSessionFactory(tmp_path / "invalid.db")
    _seed(sessions, "invalid")
    client = StubOrderClient([row], session_factory=sessions)
    _wire(monkeypatch, sessions)

    stats = order_sync.sync_orders(_as_futu(client))

    assert stats["updated"] == 0
    assert stats["warning_count"] == 1


def test_stale_cache_cannot_regress_partial_fill_or_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sessions = TrackedSessionFactory(tmp_path / "regression.db")
    _seed(
        sessions,
        "regression",
        orders=[_order("regression", status="partial", filled_qty=40)],
    )
    client = StubOrderClient(
        [_row("regression", "SUBMITTED", dealt_qty=0, dealt_avg_price=0)],
        session_factory=sessions,
    )
    _wire(monkeypatch, sessions)

    stats = order_sync.sync_orders(_as_futu(client))

    assert stats["updated"] == 0
    with sessions() as session:
        order = session.scalar(select(BrokerOrder).where(BrokerOrder.proposal_id == "regression"))
        assert order is not None
        assert (order.status, order.filled_qty) == ("partial", 40.0)


def test_unknown_status_warns_without_mutation_or_job_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sessions = TrackedSessionFactory(tmp_path / "unknown.db")
    _seed(sessions, "unknown")
    client = StubOrderClient(
        [_row("unknown", "FUTURE_NEW_STATE")],
        session_factory=sessions,
    )
    _wire(monkeypatch, sessions)

    stats = order_sync.sync_orders(_as_futu(client))

    assert stats["query_errors"] == 0
    assert stats["warning_count"] == 1
    assert stats["updated"] == 0


def test_one_query_timeout_does_not_rollback_other_order_and_preserves_failure_stats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sessions = TrackedSessionFactory(tmp_path / "partial-failure.db")
    _seed(sessions, "timeout", "filled")
    client = StubOrderClient(
        [
            _row("timeout", "SUBMITTED"),
            _row("filled", "FILLED_ALL", dealt_qty=100, dealt_avg_price=10.08),
        ],
        session_factory=sessions,
        error_order_ids={"order-timeout"},
    )
    _wire(monkeypatch, sessions)

    with pytest.raises(JobExecutionError) as captured:
        order_sync.sync_orders(_as_futu(client))

    assert captured.value.stats["query_errors"] == 1
    assert captured.value.stats["filled"] == 1
    assert captured.value.stats["updated"] == 1
    with sessions() as session:
        failed_query = session.scalar(
            select(BrokerOrder).where(BrokerOrder.proposal_id == "timeout")
        )
        filled = session.scalar(select(BrokerOrder).where(BrokerOrder.proposal_id == "filled"))
        assert failed_query is not None and filled is not None
        assert failed_query.status == "submitted"
        assert filled.status == "filled"


def test_no_active_orders_does_not_open_futu_or_account_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sessions = TrackedSessionFactory(tmp_path / "empty.db")
    account_calls = _wire(monkeypatch, sessions)

    stats = order_sync.sync_orders()

    assert stats["active"] == 0
    assert account_calls == []


def test_order_sync_job_registration_is_opt_in_every_thirty_seconds() -> None:
    order_sync.register_order_sync_job()
    spec = JOBS["sync_orders"]
    assert spec.enabled_key == "paper_trading_enabled"
    assert spec.trigger.interval.total_seconds() == 30
