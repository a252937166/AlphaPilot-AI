from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from alphapilot.backtest import factor_research
from alphapilot.backtest.factor_research import (
    all_factors_ic,
    classify_factors,
    factor_correlation,
    persist_factor_correlation,
    single_factor_ic,
)
from alphapilot.db.models import (
    AdjFactor,
    Base,
    DailyBar,
    FactorCorrelationStat,
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


def test_factor_correlation_averages_fixed_decision_cross_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = [stamp.date() for stamp in pd.bdate_range("2026-01-01", periods=61)]
    session = _session(tmp_path)
    for trade_day in dates:
        session.add(_bar("SH.000001", trade_day, 100.0))
    session.commit()

    calls: list[date] = []

    def fake_scores(_session: Session, as_of: date) -> pd.DataFrame:
        calls.append(as_of)
        frame = pd.DataFrame(
            index=["a", "b", "c", "d"],
            columns=FACTOR_SET,
            dtype=float,
        )
        frame["momentum_20d"] = [1.0, 2.0, 3.0, 4.0]
        frame["momentum_60d"] = [2.0, 4.0, 6.0, 8.0]
        frame["volatility_20d"] = [-1.0, -2.0, -3.0, -4.0]
        frame["turnover_change_5d"] = [1.0, 4.0, 2.0, 3.0]
        return frame

    monkeypatch.setattr(factor_research, "factor_zscores", fake_scores)
    try:
        corr = factor_correlation(session, dates[0], dates[-1])

        assert calls == [dates[0], dates[20], dates[40], dates[60]]
        assert corr.at["momentum_20d", "momentum_60d"] == pytest.approx(1.0)
        assert corr.at["momentum_20d", "volatility_20d"] == pytest.approx(-1.0)
        assert pd.isna(corr.at["momentum_20d", "roe"])
        assert corr.attrs["minimum_pair_periods"] == 3
        assert any(
            item["left"] == "momentum_20d" and item["right"] == "momentum_60d"
            for item in corr.attrs["redundant_pairs"]
        )
    finally:
        session.close()


def test_factor_correlation_snapshot_replaces_reliable_cells(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    corr = pd.DataFrame(
        float("nan"),
        index=FACTOR_SET,
        columns=FACTOR_SET,
    )
    corr.at["momentum_20d", "momentum_20d"] = 1.0
    corr.at["momentum_20d", "momentum_60d"] = 0.5
    corr.at["momentum_60d", "momentum_20d"] = 0.5
    corr.attrs["pair_periods"] = {
        factor: {other: 0 for other in FACTOR_SET} for factor in FACTOR_SET
    }
    corr.attrs["pair_periods"]["momentum_20d"]["momentum_20d"] = 5
    corr.attrs["pair_periods"]["momentum_60d"]["momentum_20d"] = 5
    try:
        stored = persist_factor_correlation(
            session,
            corr,
            sample_tag="full",
            start=date(2025, 1, 1),
            end=date(2025, 12, 31),
        )
        session.commit()

        assert stored == 2
        rows = list(session.scalars(select(FactorCorrelationStat)))
        assert len(rows) == 2
        assert {row.n_periods for row in rows} == {5}

        corr.at["momentum_20d", "momentum_60d"] = float("nan")
        persist_factor_correlation(
            session,
            corr,
            sample_tag="full",
            start=date(2025, 1, 1),
            end=date(2025, 12, 31),
        )
        session.commit()
        assert len(session.scalars(select(FactorCorrelationStat)).all()) == 1
    finally:
        session.close()


def test_classify_factors_separates_significance_data_gaps_and_redundancy() -> None:
    rows = [
        {
            "factor": factor,
            "ic_mean": None,
            "ic_ir": None,
            "t_stat": None,
            "n_periods": 0,
        }
        for factor in FACTOR_SET
    ]
    by_factor = {str(row["factor"]): row for row in rows}
    by_factor["momentum_20d"].update(
        ic_mean=0.10,
        ic_ir=0.40,
        t_stat=2.50,
        n_periods=10,
    )
    by_factor["momentum_60d"].update(
        ic_mean=-0.10,
        ic_ir=-0.80,
        t_stat=-3.00,
        n_periods=10,
    )
    by_factor["volatility_20d"].update(
        ic_mean=-0.02,
        ic_ir=-0.10,
        t_stat=-1.00,
        n_periods=10,
    )
    corr = pd.DataFrame(
        float("nan"),
        index=FACTOR_SET,
        columns=FACTOR_SET,
    )
    for factor in FACTOR_SET:
        corr.at[factor, factor] = 1.0
    corr.at["momentum_20d", "momentum_60d"] = 0.90
    corr.at["momentum_60d", "momentum_20d"] = 0.90

    diagnosis = classify_factors(pd.DataFrame(rows), corr)
    factors = diagnosis["factors"]

    assert factors["momentum_20d"]["classification"] == "significant_positive"
    assert factors["momentum_60d"]["classification"] == "significant_reverse"
    assert factors["volatility_20d"]["classification"] == "ineffective"
    assert factors["roe"]["classification"] == "insufficient_data"
    assert factors["momentum_20d"]["redundant"] is True
    assert factors["momentum_20d"]["retained_factor"] == "momentum_60d"
    assert factors["momentum_60d"]["redundant"] is False
