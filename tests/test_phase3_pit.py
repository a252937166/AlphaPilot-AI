from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from alphapilot.backtest.pit import (
    eligible_universe,
    forward_return,
    signal_scores,
)
from alphapilot.db.models import (
    AdjFactor,
    Base,
    DailyBar,
    FinancialIndicator,
    Security,
)
from alphapilot.engines.factors import compute_factors_for_date


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'pit.db'}")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _security(
    symbol: str,
    *,
    listed_date: str = "",
    list_status: str = "listed",
    is_st: bool = False,
    snapshot_at: datetime | None = None,
) -> Security:
    return Security(
        symbol=symbol,
        market="CN",
        name=symbol,
        board="主板",
        is_st=is_st,
        list_status=list_status,
        listed_date=listed_date,
        snapshot_at=snapshot_at,
    )


def _bar(
    symbol: str,
    trade_date: date,
    close: float,
    *,
    source: str = "baostock",
    volume: float = 100.0,
) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        amount=close * volume,
        source=source,
    )


def test_eligible_universe_enforces_age_suspension_and_audited_sources(
    tmp_path: Path,
) -> None:
    as_of = date(2026, 7, 21)
    session = _session(tmp_path)
    try:
        session.add_all(
            [
                _security("600001"),
                _security("600002"),
                _security("600003"),
                _security("600004"),
                _security("600005", list_status="delisted"),
                _security(
                    "600006",
                    listed_date="2020-01-01",
                    snapshot_at=datetime(2026, 7, 20, 17, tzinfo=UTC),
                ),
                _security("600007"),
            ]
        )
        session.add_all(
            [
                _bar("600001", as_of - timedelta(days=61), 10.0),
                _bar("600001", as_of, 10.0),
                _bar("600002", as_of - timedelta(days=30), 10.0),
                _bar("600002", as_of, 10.0),
                _bar("600003", as_of - timedelta(days=61), 10.0),
                _bar("600003", as_of, 10.0, volume=0.0),
                _bar("600004", as_of - timedelta(days=61), 10.0, source="mock"),
                _bar("600004", as_of, 10.0, source="mock"),
                _bar("600005", as_of - timedelta(days=61), 10.0),
                _bar("600005", as_of, 10.0),
                _bar("600006", as_of, 10.0),
                # An old mock row must not make a recently observed audited
                # symbol pass the 60-day listing-age fallback.
                _bar("600007", as_of - timedelta(days=100), 10.0, source="mock"),
                _bar("600007", as_of - timedelta(days=30), 10.0),
                _bar("600007", as_of, 10.0),
            ]
        )
        session.commit()

        frame = eligible_universe(session, as_of)

        assert frame["symbol"].tolist() == ["600001", "600006"]
        assert frame.set_index("symbol").loc["600001", "listing_age_basis"] == (
            "first_audited_bar"
        )
        assert frame.set_index("symbol").loc["600006", "listing_age_basis"] == (
            "security_master"
        )
        assert frame.attrs["has_survivorship_bias"] is True
        assert "退市股" in frame.attrs["survivorship_bias_warning"]
        assert frame.set_index("symbol").loc["600006", "st_status_known"]
        assert frame.attrs["st_status_known"] == 1
    finally:
        session.close()


def test_eligible_universe_uses_bounded_index_searches(tmp_path: Path) -> None:
    as_of = date(2026, 7, 21)
    engine = create_engine(f"sqlite:///{tmp_path / 'pit-plan.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for number in range(100):
            symbol = f"{600100 + number:06d}"
            session.add(_security(symbol, listed_date="2020-01-01"))
            session.add_all(
                [
                    _bar(symbol, as_of - timedelta(days=61), 10.0),
                    _bar(symbol, as_of, 10.0),
                ]
            )
        session.commit()

    captured: list[tuple[str, Any]] = []

    def capture_statement(
        _connection: Any,
        _cursor: Any,
        statement: str,
        parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        with Session(engine) as session:
            frame = eligible_universe(session, as_of)
            assert not session.new
            assert not session.dirty
            assert not session.deleted
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert len(frame) == 100
    assert len(captured) == 1
    statement, parameters = captured[0]
    with engine.connect() as connection:
        plan = "\n".join(
            str(row[3])
            for row in connection.exec_driver_sql(
                f"EXPLAIN QUERY PLAN {statement}",
                parameters,
            )
        )
    assert "CORRELATED SCALAR SUBQUERY" in plan
    assert "SEARCH first_audited_bar" in plan
    assert "symbol=?" in plan
    assert "SCAN first_audited_bar" not in plan
    assert "SCAN daily_bars" not in plan
    assert "MATERIALIZE" not in plan


def test_signal_scores_excludes_future_financials_and_uses_adjusted_momentum(
    tmp_path: Path,
) -> None:
    as_of = date(2026, 7, 21)
    dates = [stamp.date() for stamp in pd.bdate_range(end=as_of, periods=90)]
    session = _session(tmp_path)
    try:
        for offset, symbol in enumerate(("600001", "600002", "600003"), start=1):
            session.add(
                _security(
                    symbol,
                    listed_date="2020-01-01",
                    is_st=symbol == "600003",
                )
            )
            for index, trade_day in enumerate(dates):
                split_day = symbol == "600001" and index == len(dates) - 1
                close = 5.0 if split_day else 10.0
                factor = 2.0 if split_day else 1.0
                session.add(_bar(symbol, trade_day, close))
                session.add(
                    AdjFactor(
                        symbol=symbol,
                        trade_date=trade_day,
                        adj_factor=factor,
                        source="test",
                    )
                )
            session.add(
                FinancialIndicator(
                    symbol=symbol,
                    report_period="2025Q4",
                    metric="roe",
                    value=offset / 10,
                    source="test",
                    available_time=datetime(2026, 7, 1, tzinfo=UTC),
                )
            )
        session.add(
            FinancialIndicator(
                symbol="600001",
                report_period="2026Q2",
                metric="roe",
                value=9.0,
                source="test",
                available_time=datetime(2026, 7, 22, tzinfo=UTC),
            )
        )
        session.commit()

        raw = compute_factors_for_date(session, as_of)
        scores = signal_scores(session, as_of, {"roe": 1.0})

        assert raw.loc["600001", "momentum_20d"] == pytest.approx(0.0)
        assert scores.to_dict() == pytest.approx(
            {"600001": 0.0, "600002": 50.0, "600003": 100.0}
        )
        assert scores.attrs["factor_attrs"]["financial_values"] == 3
        assert scores.attrs["has_survivorship_bias"] is True
        assert "历史 ST 状态不可用" in scores.attrs["st_history_warning"]
    finally:
        session.close()


def test_forward_return_uses_adjusted_endpoints_and_marks_factor_fallback(
    tmp_path: Path,
) -> None:
    entry = date(2026, 7, 20)
    exit_ = date(2026, 7, 21)
    session = _session(tmp_path)
    try:
        session.add_all(
            [
                _bar("600001", entry, 10.0),
                _bar("600001", exit_, 5.0),
                AdjFactor(
                    symbol="600001",
                    trade_date=entry,
                    adj_factor=1.0,
                    source="test",
                ),
                AdjFactor(
                    symbol="600001",
                    trade_date=exit_,
                    adj_factor=2.0,
                    source="test",
                ),
                _bar("600002", entry, 10.0),
                _bar("600002", exit_, 11.0),
                AdjFactor(
                    symbol="600002",
                    trade_date=entry,
                    adj_factor=1.0,
                    source="test",
                ),
                _bar("600003", entry, 10.0, source="mock"),
                _bar("600003", exit_, 20.0, source="mock"),
            ]
        )
        session.commit()

        returns = forward_return(
            session,
            ["600001", "600002", "600003", "600004"],
            entry,
            exit_,
        )

        assert returns.loc["600001"] == pytest.approx(0.0)
        assert returns.loc["600002"] == pytest.approx(0.1)
        assert pd.isna(returns.loc["600003"])
        assert pd.isna(returns.loc["600004"])
        assert returns.attrs["degraded"] is True
        assert returns.attrs["missing_adjustment_rows"] == 1
        assert returns.attrs["missing_endpoint_symbols"] == 2
    finally:
        session.close()
