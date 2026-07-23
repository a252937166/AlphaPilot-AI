from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alphapilot.backtest.diagnosis import (
    compare_backtests,
    factor_diagnosis_report,
    factor_ic_report,
)
from alphapilot.db.models import (
    BacktestDaily,
    BacktestRun,
    Base,
    FactorCorrelationStat,
    FactorICStat,
)
from alphapilot.engines.factors import FACTOR_SET


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'diagnosis.db'}")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def test_factor_diagnosis_keeps_missing_cells_explicit(tmp_path: Path) -> None:
    session = _session(tmp_path)
    start = date(2025, 4, 28)
    end = date(2026, 7, 23)
    try:
        for factor in FACTOR_SET:
            available = factor in {
                "momentum_20d",
                "momentum_60d",
                "volatility_20d",
                "turnover_change_5d",
            }
            session.add(
                FactorICStat(
                    factor=factor,
                    sample_tag="full",
                    start_date=start,
                    end_date=end,
                    ic_mean=-0.04 if available else None,
                    ic_ir=-0.2 if available else None,
                    t_stat=-1.0 if available else None,
                    ic_positive_ratio=0.4 if available else None,
                    long_short=-0.01 if available else None,
                    n_periods=13 if available else 0,
                )
            )
        session.add_all(
            [
                FactorCorrelationStat(
                    left_factor="momentum_20d",
                    right_factor="momentum_20d",
                    sample_tag="full",
                    start_date=start,
                    end_date=end,
                    correlation=1.0,
                    n_periods=13,
                ),
                FactorCorrelationStat(
                    left_factor="momentum_20d",
                    right_factor="momentum_60d",
                    sample_tag="full",
                    start_date=start,
                    end_date=end,
                    correlation=0.569,
                    n_periods=12,
                ),
            ]
        )
        session.commit()

        ic = factor_ic_report(session)
        diagnosis = factor_diagnosis_report(session)

        assert ic["factor_count"] == len(FACTOR_SET) == 13
        assert ic["available_count"] == 4
        assert diagnosis["source_audit"]["calculation_bug_found"] is False
        assert diagnosis["classification_counts"]["ineffective"] == 4
        assert diagnosis["classification_counts"]["insufficient_data"] == 9
        assert diagnosis["correlation"]["values"][0][1] == pytest.approx(0.569)
        assert diagnosis["correlation"]["values"][0][4] is None
        assert diagnosis["correlation"]["n_periods"][0][4] == 0
        assert diagnosis["weights"]["test_window_used_for_weights"] is False
        assert all(item["direction_audit"]["bug_found"] is False for item in diagnosis["factors"])
    finally:
        session.close()


def _completed_run(
    session: Session,
    *,
    signal_id: str,
    navs: list[float],
    rank_ics: list[float],
) -> BacktestRun:
    days = [date(2026, 3, 12), date(2026, 3, 13), date(2026, 3, 16)]
    run = BacktestRun(
        name=signal_id,
        signal_id=signal_id,
        start_date=days[0],
        end_date=days[-1],
        rebalance_freq="20d",
        top_pct=0.1,
        params={
            "initial_capital": 1_000_000.0,
            "execution": "decision T close; fill T+1 open",
            "cost_model": {
                "commission_bps": 2.5,
                "commission_min": 5.0,
                "stamp_duty_bps": 10.0,
                "transfer_bps": 0.2,
                "slippage_bps": 5.0,
            },
        },
        status="completed",
        summary={
            "total_cost": 100.0,
            "total_traded": 100_000.0,
            "day_errors": [],
            "missing_benchmark_days": 0,
        },
    )
    session.add(run)
    session.flush()
    for index, trade_day in enumerate(days):
        session.add(
            BacktestDaily(
                run_id=run.id,
                trade_date=trade_day,
                rank_ic=rank_ics[index],
                long_ret=0.0 if index == 0 else navs[index] / navs[index - 1] - 1,
                ls_ret=-0.001,
                turnover=None if index == 0 else 0.1,
                nav=navs[index],
                benchmark_nav=1.0 + index * 0.002,
                market_nav=1.0 + index * 0.003,
                n_eligible=100,
                group_returns=[-0.001 + group * 0.0001 for group in range(10)],
            )
        )
    session.flush()
    return run


def test_compare_backtests_applies_preregistered_failure_gate(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        v1 = _completed_run(
            session,
            signal_id="composite-v1",
            navs=[1.0, 0.99, 0.98],
            rank_ics=[-0.03, -0.01, -0.02],
        )
        v2 = _completed_run(
            session,
            signal_id="composite-v2",
            navs=[1.0, 0.98, 0.97],
            rank_ics=[0.01, 0.02, -0.01],
        )
        session.commit()

        result = compare_backtests(session, v1.id, v2.id)

        assert result["protocol"]["same_window_and_costs"] is True
        assert result["protocol"]["weights_frozen_before_test"] is True
        assert result["verdict"]["status"] == "failed"
        assert result["verdict"]["significant_positive_ic"] is False
        assert result["curve"]["dates"] == [
            "2026-03-12",
            "2026-03-13",
            "2026-03-16",
        ]
        assert result["v1"]["signal_id"] == "composite-v1"
        assert result["v2"]["signal_id"] == "composite-v2"
    finally:
        session.close()
