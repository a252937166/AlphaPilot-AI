from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alphapilot.backtest.costs import (
    CostModel,
    t_plus_one_sellable,
    tradable_at_open,
    trade_cost,
    trade_cost_components,
)
from alphapilot.db.models import AdjFactor, Base, DailyBar, Security

PREVIOUS = date(2026, 7, 20)
TRADE_DATE = date(2026, 7, 21)


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'costs.db'}")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _seed_market(
    session: Session,
    symbol: str,
    *,
    board: str,
    open_price: float | None,
    previous_close: float = 10.0,
    previous_factor: float = 1.0,
    current_factor: float = 1.0,
    is_st: bool = False,
    st_known: bool = True,
    volume: float = 100.0,
    source: str = "baostock",
) -> None:
    snapshot_at = (
        datetime(2026, 7, 21, 1, 29, tzinfo=UTC)
        if st_known
        else datetime(2026, 7, 20, 2, tzinfo=UTC)
    )
    session.add(
        Security(
            symbol=symbol,
            market="CN",
            board=board,
            is_st=is_st,
            list_status="listed",
            snapshot_at=snapshot_at,
        )
    )
    session.add(
        DailyBar(
            symbol=symbol,
            trade_date=PREVIOUS,
            open=previous_close,
            high=previous_close,
            low=previous_close,
            close=previous_close,
            volume=100.0,
            amount=previous_close * 100.0,
            source=source,
        )
    )
    session.add(
        AdjFactor(
            symbol=symbol,
            trade_date=PREVIOUS,
            adj_factor=previous_factor,
            source="test",
        )
    )
    if open_price is not None:
        session.add(
            DailyBar(
                symbol=symbol,
                trade_date=TRADE_DATE,
                open=open_price,
                high=open_price,
                low=open_price,
                close=open_price,
                volume=volume,
                amount=open_price * volume,
                source=source,
            )
        )
        session.add(
            AdjFactor(
                symbol=symbol,
                trade_date=TRADE_DATE,
                adj_factor=current_factor,
                source="test",
            )
        )
    session.commit()


def test_trade_cost_applies_minimum_commission_and_sell_only_tax() -> None:
    buy = trade_cost_components(10_000.0, "buy")
    sell = trade_cost_components(10_000.0, "sell")

    assert buy == pytest.approx(
        {
            "commission": 5.0,
            "stamp_duty": 0.0,
            "transfer_fee": 0.2,
            "slippage": 5.0,
        }
    )
    assert sell == pytest.approx(
        {
            "commission": 5.0,
            "stamp_duty": 10.0,
            "transfer_fee": 0.2,
            "slippage": 5.0,
        }
    )
    assert trade_cost(10_000.0, "buy") == pytest.approx(10.2)
    assert trade_cost(10_000.0, "sell") == pytest.approx(20.2)
    assert trade_cost(0.0, "sell") == 0.0


def test_trade_cost_scales_bps_and_rejects_invalid_inputs() -> None:
    model = CostModel()

    assert trade_cost(1_000_000.0, "buy", model) == pytest.approx(770.0)
    assert trade_cost(1_000_000.0, "sell", model) == pytest.approx(1770.0)
    with pytest.raises(ValueError, match="notional"):
        trade_cost(-1.0, "buy")
    with pytest.raises(ValueError, match="side"):
        trade_cost(1.0, "hold")
    with pytest.raises(ValueError, match="commission_bps"):
        CostModel(commission_bps=-1)
    with pytest.raises(ValueError, match="slippage_bps"):
        CostModel(slippage_bps=float("nan"))
    with pytest.raises(ValueError, match="notional"):
        trade_cost(float("inf"), "buy")


@pytest.mark.parametrize(
    ("symbol", "board", "is_st", "limit_open"),
    [
        ("600001", "主板", False, 11.0),
        ("300001", "创业板", False, 12.0),
        ("688001", "科创板", False, 12.0),
        ("920001", "北交所", False, 13.0),
        ("600002", "主板", True, 10.5),
    ],
)
def test_open_limit_blocks_buy_and_sell(
    tmp_path: Path,
    symbol: str,
    board: str,
    is_st: bool,
    limit_open: float,
) -> None:
    session = _session(tmp_path)
    try:
        _seed_market(
            session,
            symbol,
            board=board,
            open_price=limit_open,
            is_st=is_st,
        )

        assert tradable_at_open(session, symbol, TRADE_DATE, "buy") is False

        current = session.query(DailyBar).filter(
            DailyBar.symbol == symbol,
            DailyBar.trade_date == TRADE_DATE,
        ).one()
        ratio = (limit_open / 10.0) - 1.0
        current.open = round(10.0 * (1.0 - ratio), 2)
        session.commit()
        assert tradable_at_open(session, symbol, TRADE_DATE, "sell") is False
    finally:
        session.close()


def test_open_limit_uses_adjusted_reference_on_ex_date(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        _seed_market(
            session,
            "600001",
            board="主板",
            open_price=5.5,
            previous_close=10.0,
            previous_factor=1.0,
            current_factor=2.0,
        )

        assert tradable_at_open(session, "600001", TRADE_DATE, "buy") is False
    finally:
        session.close()


def test_suspension_unknown_st_and_missing_factor_fail_closed(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    try:
        _seed_market(
            session,
            "600001",
            board="主板",
            open_price=None,
        )
        _seed_market(
            session,
            "600002",
            board="主板",
            open_price=10.5,
            st_known=False,
        )
        _seed_market(
            session,
            "600003",
            board="主板",
            open_price=10.4,
            volume=0.0,
        )
        _seed_market(
            session,
            "600004",
            board="主板",
            open_price=10.4,
            source="mock",
        )
        _seed_market(
            session,
            "600005",
            board="主板",
            open_price=10.5,
        )
        after_open = session.get(Security, "600005")
        assert after_open is not None
        after_open.snapshot_at = datetime(2026, 7, 21, 2, tzinfo=UTC)
        session.commit()

        assert tradable_at_open(session, "600001", TRADE_DATE, "buy") is False
        assert tradable_at_open(session, "600002", TRADE_DATE, "buy") is False
        assert tradable_at_open(session, "600003", TRADE_DATE, "buy") is False
        assert tradable_at_open(session, "600004", TRADE_DATE, "buy") is False
        assert tradable_at_open(session, "600005", TRADE_DATE, "buy") is False

        factor = session.query(AdjFactor).filter(
            AdjFactor.symbol == "600002",
            AdjFactor.trade_date == TRADE_DATE,
        ).one()
        session.delete(factor)
        session.commit()
        assert tradable_at_open(session, "600002", TRADE_DATE, "buy") is False
    finally:
        session.close()


def test_t_plus_one_sellability() -> None:
    assert t_plus_one_sellable(TRADE_DATE, TRADE_DATE) is False
    assert t_plus_one_sellable(TRADE_DATE, date(2026, 7, 22)) is True
    assert t_plus_one_sellable(TRADE_DATE, date(2026, 7, 20)) is False
