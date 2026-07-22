from __future__ import annotations

from datetime import UTC
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alphapilot.db.models import Base, BrokerOrder, TradeProposalRecord


def _proposal(proposal_id: str) -> TradeProposalRecord:
    return TradeProposalRecord(
        proposal_id=proposal_id,
        symbol="600000",
        side="BUY",
        quantity=100.0,
        estimated_notional=1_000.0,
        confidence=0.8,
        mode="paper",
    )


def _order(proposal_id: str, **overrides: Any) -> BrokerOrder:
    values: dict[str, Any] = {
        "proposal_id": proposal_id,
        "symbol": "600000",
        "side": "BUY",
        "qty": 100.0,
    }
    values.update(overrides)
    return BrokerOrder(**values)


def test_broker_order_create_all_and_defaults(tmp_path: Any) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'broker-orders.db'}")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    assert "broker_orders" in inspector.get_table_names()
    foreign_keys = inspector.get_foreign_keys("broker_orders")
    assert any(
        foreign_key["constrained_columns"] == ["proposal_id"]
        and foreign_key["referred_table"] == "trade_proposals"
        and foreign_key["referred_columns"] == ["proposal_id"]
        for foreign_key in foreign_keys
    )

    with Session(engine) as session:
        session.add(_proposal("proposal-defaults"))
        order = _order("proposal-defaults")
        session.add(order)
        session.flush()

        assert order.id is not None
        assert order.order_type == "MARKET"
        assert order.status == "submitting"
        assert order.filled_qty == pytest.approx(0.0)
        assert order.environment == "SIMULATE"
        assert order.created_at.tzinfo is UTC
        assert order.updated_at.tzinfo is UTC


def test_broker_order_proposal_is_unique(tmp_path: Any) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'unique-proposal.db'}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(_proposal("proposal-once"))
        session.add(_order("proposal-once", futu_order_id="futu-1"))
        session.commit()

        session.add(_order("proposal-once", futu_order_id="futu-2"))
        with pytest.raises(IntegrityError):
            session.flush()


def test_broker_order_futu_order_id_is_unique_when_present(tmp_path: Any) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'unique-futu-order.db'}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add_all([_proposal("proposal-1"), _proposal("proposal-2")])
        session.add(_order("proposal-1", futu_order_id="futu-1"))
        session.commit()

        session.add(_order("proposal-2", futu_order_id="futu-1"))
        with pytest.raises(IntegrityError):
            session.flush()


def test_broker_order_rejects_real_environment(tmp_path: Any) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'simulate-only.db'}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(_proposal("proposal-real"))
        session.add(_order("proposal-real", environment="REAL"))
        with pytest.raises(IntegrityError):
            session.flush()


@pytest.mark.parametrize(
    ("overrides", "proposal_id"),
    [
        ({"status": "unknown"}, "proposal-status"),
        ({"qty": 0.0}, "proposal-qty"),
        ({"price": 0.0}, "proposal-price"),
        ({"filled_qty": -1.0}, "proposal-negative-fill"),
        ({"filled_qty": 101.0}, "proposal-overfill"),
    ],
)
def test_broker_order_rejects_invalid_state(
    tmp_path: Any,
    overrides: dict[str, Any],
    proposal_id: str,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / f'{proposal_id}.db'}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(_proposal(proposal_id))
        session.add(_order(proposal_id, **overrides))
        with pytest.raises(IntegrityError):
            session.flush()
