from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alphapilot.backtest.diagnosis import (
    compare_backtests,
    factor_diagnosis_report,
    factor_ic_report,
    factor_ic_windows,
)
from alphapilot.backtest.factor_scope import HISTORICAL_FACTOR_CANDIDATES
from alphapilot.db.models import (
    BacktestDaily,
    BacktestRun,
    Base,
    FactorCorrelationStat,
    FactorICStat,
    JobRun,
)
from alphapilot.engines.factors import FACTOR_SET


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'diagnosis.db'}")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _preliminary_job_stats(
    multi_start: date,
    multi_end: date,
    flow_start: date,
    flow_end: date,
) -> dict[str, object]:
    multi_factors = [
        "momentum_20d",
        "momentum_60d",
        "volatility_20d",
        "turnover_change_5d",
        "pe_percentile",
        "pb_percentile",
    ]
    flow_factors = ["net_inflow_5d"]
    multi_result = {
        "ic_mean": 0.02,
        "ic_ir": 0.2,
        "t_stat": 2.1,
        "n_periods": 100,
        "long_short": 0.01,
    }
    flow_result = {
        "ic_mean": None,
        "ic_ir": None,
        "t_stat": None,
        "n_periods": 0,
        "long_short": None,
    }
    return {
        "status": "preliminary_train_only",
        "sample_tag": "train",
        "selected_factors": [*multi_factors, *flow_factors],
        "test_window_used": False,
        "weights_written": False,
        "cohorts": {
            "multi_year_price_valuation": {
                "factors": multi_factors,
                "train_window": {
                    "start": multi_start.isoformat(),
                    "end": multi_end.isoformat(),
                },
                "results": {factor: dict(multi_result) for factor in multi_factors},
            },
            "one_year_sector_flow": {
                "factors": flow_factors,
                "train_window": {
                    "start": flow_start.isoformat(),
                    "end": flow_end.isoformat(),
                },
                "results": {factor: dict(flow_result) for factor in flow_factors},
            },
        },
    }


def _formal_job_stats(start: date, end: date) -> dict[str, object]:
    result = {
        "ic_mean": 0.03,
        "ic_ir": 0.3,
        "t_stat": 2.5,
        "n_periods": 120,
        "long_short": 0.02,
    }
    return {
        "status": "formal_factor_research",
        "research_stage": "m3_s7_formal",
        "test_window_used": True,
        "weights_written": False,
        "historical_factor_candidates": list(HISTORICAL_FACTOR_CANDIDATES),
        "excluded_factors": {
            "net_inflow_5d": "history_excluded_pit_gap",
            "sector_strength": "live_only",
        },
        "samples": {
            "train": {
                "window": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "sessions": 900,
                },
                "results": {
                    factor: dict(result)
                    for factor in HISTORICAL_FACTOR_CANDIDATES
                },
            }
        },
    }


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

        ic = factor_ic_report(session, "full")
        diagnosis = factor_diagnosis_report(session, "full")

        assert ic["factor_count"] == len(FACTOR_SET) == 13
        assert ic["available_count"] == 4
        assert diagnosis["source_audit"]["calculation_bug_found"] is False
        assert diagnosis["classification_counts"]["ineffective"] == 4
        assert diagnosis["classification_counts"]["insufficient_data"] == 8
        assert (
            diagnosis["classification_counts"]["history_excluded_pit_gap"]
            == 1
        )
        assert diagnosis["correlation"]["values"][0][1] == pytest.approx(0.569)
        assert diagnosis["correlation"]["values"][0][4] is None
        assert diagnosis["correlation"]["n_periods"][0][4] == 0
        assert diagnosis["weights"]["test_window_used_for_weights"] is False
        assert all(item["direction_audit"]["bug_found"] is False for item in diagnosis["factors"])
    finally:
        session.close()


def test_train_windows_use_earliest_multi_year_default_and_exact_statuses(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    multi_start = date(2019, 1, 2)
    multi_end = date(2024, 4, 16)
    flow_start = date(2025, 7, 25)
    flow_end = date(2026, 4, 8)
    started_at = datetime(2026, 7, 25, 10, 0)
    written_at = datetime(2026, 7, 25, 10, 30)
    finished_at = datetime(2026, 7, 25, 11, 0)
    try:
        for factor in (
            "momentum_20d",
            "momentum_60d",
            "volatility_20d",
            "turnover_change_5d",
            "pe_percentile",
            "pb_percentile",
        ):
            session.add(
                FactorICStat(
                    factor=factor,
                    sample_tag="train",
                    start_date=multi_start,
                    end_date=multi_end,
                    ic_mean=0.02,
                    ic_ir=0.2,
                    t_stat=2.1,
                    ic_positive_ratio=0.6,
                    long_short=0.01,
                    n_periods=100,
                    updated_at=written_at,
                )
            )
        session.add(
            FactorICStat(
                factor="net_inflow_5d",
                sample_tag="train",
                start_date=flow_start,
                end_date=flow_end,
                ic_mean=None,
                ic_ir=None,
                t_stat=None,
                ic_positive_ratio=None,
                long_short=None,
                n_periods=0,
                updated_at=written_at,
            )
        )
        session.add(
            FactorICStat(
                factor="momentum_20d",
                sample_tag="train",
                start_date=date(2025, 4, 28),
                end_date=date(2026, 2, 2),
                ic_mean=-0.01,
                ic_ir=-0.1,
                t_stat=-0.5,
                ic_positive_ratio=0.4,
                long_short=-0.01,
                n_periods=12,
                updated_at=written_at,
            )
        )
        job = JobRun(
            job_name="research_preliminary_train_ic",
            started_at=started_at,
            finished_at=finished_at,
            status="ok",
            stats=_preliminary_job_stats(
                multi_start,
                multi_end,
                flow_start,
                flow_end,
            ),
        )
        session.add(job)
        session.commit()

        catalog = factor_ic_windows(session)
        report = factor_ic_report(session)
        flow_report = factor_ic_report(
            session,
            "train",
            start_date=flow_start,
            end_date=flow_end,
        )

        assert catalog["default_window"]["start_date"] == multi_start.isoformat()
        assert catalog["default_window"]["research_stage"] == "m3_preliminary_multi_year"
        assert catalog["default_window"]["research_run_id"] == job.id
        assert catalog["default_window"]["expected_factors"] == [
            "momentum_20d",
            "momentum_60d",
            "volatility_20d",
            "turnover_change_5d",
            "pe_percentile",
            "pb_percentile",
        ]
        assert {
            window["research_stage"] for window in catalog["windows"]
        } == {
            "m3_preliminary_multi_year",
            "legacy_or_other",
        }
        assert catalog["scope"]["test_window_sealed"] is True
        assert report["start_date"] == multi_start.isoformat()
        assert report["research_stage"] == "m3_preliminary_multi_year"
        assert report["research_run_id"] == job.id
        assert report["selection"]["research_run_id"] == job.id
        assert report["selection"]["expected_factors"] == report["expected_factors"]
        assert report["coverage"] == {
            "preliminary_requested_count": 6,
            "preliminary_evaluated_count": 6,
            "preliminary_measurable_count": 6,
            "preliminary_evaluated_no_sample_count": 0,
            "preliminary_not_evaluated_count": 0,
            "financial_pending_count": 5,
            "financial_pending_factors": [
                "roe",
                "net_profit_yoy",
                "ocf_to_profit",
                "debt_ratio",
                "revenue_yoy",
            ],
            "live_only_count": 1,
            "live_only_factors": ["sector_strength"],
            "historical_factor_candidate_count": 11,
            "historical_factor_candidates": [
                "momentum_20d",
                "momentum_60d",
                "volatility_20d",
                "turnover_change_5d",
                "roe",
                "net_profit_yoy",
                "ocf_to_profit",
                "debt_ratio",
                "revenue_yoy",
                "pe_percentile",
                "pb_percentile",
            ],
            "history_excluded_pit_gap_count": 1,
            "history_excluded_pit_gap_factors": ["net_inflow_5d"],
        }
        statuses = {
            item["factor"]: item["evaluation_status"] for item in report["factors"]
        }
        assert statuses["momentum_20d"] == "measured"
        assert (
            statuses["net_inflow_5d"]
            == "history_excluded_pit_gap"
        )
        assert statuses["roe"] == "not_evaluated"
        assert statuses["sector_strength"] == "live_only"
        flow = {item["factor"]: item for item in flow_report["factors"]}
        assert (
            flow["net_inflow_5d"]["evaluation_status"]
            == "history_excluded_pit_gap"
        )
        assert flow["net_inflow_5d"]["n_periods"] == 0
        assert flow["net_inflow_5d"]["ic_mean"] is None
    finally:
        session.close()


def test_m3_lineage_excludes_extra_ic_and_stale_correlation(tmp_path: Path) -> None:
    session = _session(tmp_path)
    multi_start = date(2019, 1, 2)
    multi_end = date(2024, 4, 16)
    flow_start = date(2025, 7, 25)
    flow_end = date(2026, 4, 8)
    started_at = datetime(2026, 7, 25, 10, 0)
    written_at = datetime(2026, 7, 25, 10, 30)
    finished_at = datetime(2026, 7, 25, 11, 0)
    multi_factors = (
        "momentum_20d",
        "momentum_60d",
        "volatility_20d",
        "turnover_change_5d",
        "pe_percentile",
        "pb_percentile",
    )
    try:
        for factor in multi_factors:
            session.add(
                FactorICStat(
                    factor=factor,
                    sample_tag="train",
                    start_date=multi_start,
                    end_date=multi_end,
                    ic_mean=0.02,
                    ic_ir=0.2,
                    t_stat=2.1,
                    ic_positive_ratio=0.6,
                    long_short=0.01,
                    n_periods=100,
                    updated_at=written_at,
                )
            )
        session.add(
            FactorICStat(
                factor="roe",
                sample_tag="train",
                start_date=multi_start,
                end_date=multi_end,
                ic_mean=0.99,
                ic_ir=9.0,
                t_stat=99.0,
                ic_positive_ratio=1.0,
                long_short=0.5,
                n_periods=777,
                updated_at=finished_at + timedelta(days=1),
            )
        )
        session.add(
            FactorCorrelationStat(
                left_factor="momentum_20d",
                right_factor="roe",
                sample_tag="train",
                start_date=multi_start,
                end_date=multi_end,
                correlation=0.95,
                n_periods=12,
            )
        )
        job = JobRun(
            job_name="research_preliminary_train_ic",
            started_at=started_at,
            finished_at=finished_at,
            status="ok",
            stats=_preliminary_job_stats(
                multi_start,
                multi_end,
                flow_start,
                flow_end,
            ),
        )
        session.add(job)
        session.commit()

        catalog = factor_ic_windows(session)
        diagnosis = factor_diagnosis_report(
            session,
            "train",
            start_date=multi_start,
            end_date=multi_end,
        )

        assert catalog["default_window"]["research_run_id"] == job.id
        assert catalog["default_window"]["factors"] == sorted(multi_factors)
        assert "roe" not in catalog["default_window"]["expected_factors"]
        factors = {item["factor"]: item for item in diagnosis["factors"]}
        assert diagnosis["sample"]["research_run_id"] == job.id
        assert diagnosis["sample"]["expected_factors"] == list(multi_factors)
        assert factors["roe"]["evaluation_status"] == "not_evaluated"
        assert factors["roe"]["ic_mean"] is None
        assert diagnosis["correlation"]["available"] is False
        assert diagnosis["correlation"]["available_cells"] == 0
        assert diagnosis["correlation"]["redundant_pairs"] == []
        assert "lineage" in diagnosis["correlation"]["limitation"]
    finally:
        session.close()


def test_m3_lineage_rejects_job_result_mismatch(tmp_path: Path) -> None:
    session = _session(tmp_path)
    multi_start = date(2019, 1, 2)
    multi_end = date(2024, 4, 16)
    flow_start = date(2025, 7, 25)
    flow_end = date(2026, 4, 8)
    started_at = datetime(2026, 7, 25, 10, 0)
    written_at = datetime(2026, 7, 25, 10, 30)
    finished_at = datetime(2026, 7, 25, 11, 0)
    multi_factors = (
        "momentum_20d",
        "momentum_60d",
        "volatility_20d",
        "turnover_change_5d",
        "pe_percentile",
        "pb_percentile",
    )
    try:
        for factor in multi_factors:
            session.add(
                FactorICStat(
                    factor=factor,
                    sample_tag="train",
                    start_date=multi_start,
                    end_date=multi_end,
                    ic_mean=0.02,
                    ic_ir=0.2,
                    t_stat=2.1,
                    long_short=0.01,
                    n_periods=100,
                    updated_at=written_at,
                )
            )
        stats = _preliminary_job_stats(
            multi_start,
            multi_end,
            flow_start,
            flow_end,
        )
        cohorts = stats["cohorts"]
        assert isinstance(cohorts, dict)
        multi_year = cohorts["multi_year_price_valuation"]
        assert isinstance(multi_year, dict)
        results = multi_year["results"]
        assert isinstance(results, dict)
        momentum = results["momentum_20d"]
        assert isinstance(momentum, dict)
        momentum["ic_mean"] = 0.021
        session.add(
            JobRun(
                job_name="research_preliminary_train_ic",
                started_at=started_at,
                finished_at=finished_at,
                status="ok",
                stats=stats,
            )
        )
        session.commit()

        catalog = factor_ic_windows(session)

        assert catalog["default_window"] is None
        target = next(
            window
            for window in catalog["windows"]
            if window["start_date"] == multi_start.isoformat()
        )
        assert target["research_stage"] == "legacy_or_other"
        assert target["research_run_id"] is None
    finally:
        session.close()


def test_train_window_rejects_rows_outside_successful_job_timebox(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    multi_start = date(2019, 1, 2)
    multi_end = date(2024, 4, 16)
    flow_start = date(2025, 7, 25)
    flow_end = date(2026, 4, 8)
    started_at = datetime(2026, 7, 25, 10, 0)
    finished_at = datetime(2026, 7, 25, 11, 0)
    try:
        for factor in (
            "momentum_20d",
            "momentum_60d",
            "volatility_20d",
            "turnover_change_5d",
            "pe_percentile",
            "pb_percentile",
        ):
            session.add(
                FactorICStat(
                    factor=factor,
                    sample_tag="train",
                    start_date=multi_start,
                    end_date=multi_end,
                    ic_mean=0.02,
                    ic_ir=0.2,
                    t_stat=2.1,
                    long_short=0.01,
                    n_periods=100,
                    updated_at=finished_at + timedelta(seconds=1),
                )
            )
        session.add(
            JobRun(
                job_name="research_preliminary_train_ic",
                started_at=started_at,
                finished_at=finished_at,
                status="ok",
                stats=_preliminary_job_stats(
                    multi_start,
                    multi_end,
                    flow_start,
                    flow_end,
                ),
            )
        )
        session.commit()

        catalog = factor_ic_windows(session)

        assert catalog["default_window"] is None
        target = next(
            window
            for window in catalog["windows"]
            if window["start_date"] == multi_start.isoformat()
        )
        assert target["research_stage"] == "legacy_or_other"
        assert target["research_run_id"] is None
    finally:
        session.close()


def test_exact_window_does_not_fall_back_to_another_window(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        session.add(
            FactorICStat(
                factor="momentum_20d",
                sample_tag="train",
                start_date=date(2019, 1, 2),
                end_date=date(2024, 4, 16),
                ic_mean=0.01,
                ic_ir=0.1,
                t_stat=1.0,
                ic_positive_ratio=0.5,
                long_short=0.0,
                n_periods=10,
            )
        )
        session.commit()

        missing = factor_ic_report(
            session,
            "train",
            start_date=date(2020, 1, 1),
            end_date=date(2021, 1, 1),
        )

        assert missing["available"] is False
        assert missing["start_date"] is None
        assert missing["selection"]["exact_window"] is True
        assert all(item["evaluation_status"] != "measured" for item in missing["factors"])
        with pytest.raises(ValueError, match="provided together"):
            factor_ic_report(session, "train", start_date=date(2020, 1, 1))
        with pytest.raises(ValueError, match="earlier"):
            factor_ic_report(
                session,
                "train",
                start_date=date(2021, 1, 2),
                end_date=date(2021, 1, 1),
            )
    finally:
        session.close()


def test_formal_multi_year_lineage_wins_over_same_end_301_window(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    multi_start = date(2019, 1, 2)
    short_start = date(2025, 4, 28)
    shared_end = date(2026, 2, 27)
    started_at = datetime(2026, 7, 26, 9, 0)
    written_at = datetime(2026, 7, 26, 9, 30)
    finished_at = datetime(2026, 7, 26, 10, 0)
    try:
        for factor in HISTORICAL_FACTOR_CANDIDATES:
            session.add(
                FactorICStat(
                    factor=factor,
                    sample_tag="train",
                    start_date=multi_start,
                    end_date=shared_end,
                    ic_mean=0.03,
                    ic_ir=0.3,
                    t_stat=2.5,
                    ic_positive_ratio=0.6,
                    long_short=0.02,
                    n_periods=120,
                    updated_at=written_at,
                )
            )
            session.add(
                FactorICStat(
                    factor=factor,
                    sample_tag="train",
                    start_date=short_start,
                    end_date=shared_end,
                    ic_mean=-0.9,
                    ic_ir=-9.0,
                    t_stat=-8.0,
                    ic_positive_ratio=0.1,
                    long_short=-0.5,
                    n_periods=12,
                    updated_at=written_at - timedelta(days=1),
                )
            )
        formal = JobRun(
            job_name="research_factors_m3",
            started_at=started_at,
            finished_at=finished_at,
            status="ok",
            stats=_formal_job_stats(multi_start, shared_end),
        )
        session.add(formal)
        session.commit()

        catalog = factor_ic_windows(session, "train")
        exact = factor_ic_report(
            session,
            "train",
            start_date=multi_start,
            end_date=shared_end,
        )

        assert catalog["default_window"]["start_date"] == multi_start.isoformat()
        assert catalog["default_window"]["research_stage"] == "m3_s7_formal"
        assert catalog["default_window"]["research_run_id"] == formal.id
        assert exact["selection"]["exact_window"] is True
        diagnosis = factor_diagnosis_report(
            session,
            "train",
            start_date=multi_start,
            end_date=shared_end,
        )
        assert diagnosis["sample"]["evidence_label"].startswith(
            "M3 S7 正式 JobRun"
        )
        assert "11 个历史候选因子" in diagnosis["limitations"][-3]
        assert "5 个财务因子等待" not in " ".join(diagnosis["limitations"])
        assert exact["start_date"] == multi_start.isoformat()
        assert exact["end_date"] == shared_end.isoformat()
        momentum = next(
            row for row in exact["factors"] if row["factor"] == "momentum_20d"
        )
        assert momentum["ic_mean"] == pytest.approx(0.03)
        assert momentum["ic_ir"] == pytest.approx(0.3)
        assert momentum["n_periods"] == 120
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
