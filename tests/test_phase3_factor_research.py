from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from alphapilot.backtest import factor_research
from alphapilot.backtest.factor_research import all_factors_ic, single_factor_ic
from alphapilot.db.models import (
    AdjFactor,
    Base,
    DailyBar,
    FactorICStat,
    FinancialIndicator,
    Security,
)
from alphapilot.engines.factors import FACTOR_SET


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'factor-research.db'}")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _bar(symbol: str, trade_date: date, close: float) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100.0,
        amount=close * 100.0,
        source="baostock",
    )


def test_single_factor_ic_excludes_future_financial_observations(
    tmp_path: Path,
) -> None:
    dates = [stamp.date() for stamp in pd.bdate_range("2026-01-01", periods=100)]
    decision_index = 90
    decision = dates[decision_index]
    exit_date = dates[decision_index + 5]
    session = _session(tmp_path)
    try:
        for rank, symbol in enumerate(("600001", "600002", "600003"), start=1):
            session.add(
                Security(
                    symbol=symbol,
                    market="CN",
                    name=symbol,
                    board="主板",
                    is_st=False,
                    list_status="listed",
                    listed_date="2020-01-01",
                )
            )
            for index, trade_day in enumerate(dates):
                progress = max(0, min(index - decision_index, 5))
                close = 100.0 * (1.0 + rank * 0.01 * progress / 5)
                session.add(_bar(symbol, trade_day, close))
                session.add(
                    AdjFactor(
                        symbol=symbol,
                        trade_date=trade_day,
                        adj_factor=1.0,
                        source="test",
                    )
                )
            session.add_all(
                [
                    FinancialIndicator(
                        symbol=symbol,
                        report_period="2025Q4",
                        metric="roe",
                        value=float(rank),
                        source="test",
                        available_time=datetime.combine(
                            decision - timedelta(days=1),
                            datetime.min.time(),
                            tzinfo=UTC,
                        ),
                    ),
                    FinancialIndicator(
                        symbol=symbol,
                        report_period="2026Q1",
                        metric="roe",
                        value=float(4 - rank),
                        source="test",
                        available_time=datetime.combine(
                            exit_date + timedelta(days=1),
                            datetime.min.time(),
                            tzinfo=UTC,
                        ),
                    ),
                ]
            )
        session.commit()

        result = single_factor_ic(
            session,
            "roe",
            decision,
            exit_date,
            horizon=5,
            rebalance="5d",
        )

        assert result["factor"] == "roe"
        assert result["n_periods"] == 1
        assert result["ic_mean"] == pytest.approx(1.0)
        assert result["ic_positive_ratio"] == pytest.approx(1.0)
        assert result["t_stat"] is None
        assert result["decay"]["5d"] == {
            "ic_mean": pytest.approx(1.0),
            "n_periods": 1,
        }
        assert result["long_short"] is not None
    finally:
        session.close()


def test_all_factors_reuses_each_pit_snapshot_and_upserts_full_stats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = [stamp.date() for stamp in pd.bdate_range("2026-01-01", periods=41)]
    session = _session(tmp_path)
    for trade_day in dates:
        session.add(_bar("SH.000001", trade_day, 100.0))
    session.commit()

    snapshot_calls: list[date] = []

    def fake_scores(_session: Session, as_of: date) -> pd.DataFrame:
        snapshot_calls.append(as_of)
        return pd.DataFrame(
            {factor: [1.0, 2.0, 3.0] for factor in FACTOR_SET},
            index=["600001", "600002", "600003"],
        )

    def fake_returns(
        _session: Session,
        symbols: list[str],
        _entry: date,
        _exit: date,
    ) -> pd.Series:
        assert symbols == ["600001", "600002", "600003"]
        return pd.Series(
            [0.01, 0.02, 0.03],
            index=symbols,
            name="forward_return",
        )

    monkeypatch.setattr(factor_research, "factor_zscores", fake_scores)
    monkeypatch.setattr(factor_research, "forward_return", fake_returns)
    try:
        table = all_factors_ic(session, dates[0], dates[-1])
        session.commit()

        assert table["factor"].tolist() == FACTOR_SET
        assert len(table) == len(FACTOR_SET) == 13
        assert table["n_periods"].tolist() == [2] * len(FACTOR_SET)
        assert table["ic_mean"].tolist() == pytest.approx([1.0] * len(FACTOR_SET))
        assert snapshot_calls == [dates[0], dates[20]]
        assert len(session.scalars(select(FactorICStat)).all()) == len(FACTOR_SET)

        all_factors_ic(session, dates[0], dates[-1])
        session.commit()
        assert len(session.scalars(select(FactorICStat)).all()) == len(FACTOR_SET)
    finally:
        session.close()
