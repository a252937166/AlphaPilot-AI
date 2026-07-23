from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from alphapilot.backtest.report import generate_report
from alphapilot.db.models import BacktestDaily, BacktestRun, Base


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'report.db'}")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _seed_completed_run(
    session: Session,
    *,
    strategy_nav: list[float],
    benchmark_nav: list[float],
    market_nav: list[float],
) -> int:
    dates = [
        date(2026, 7, 1),
        date(2026, 7, 2),
        date(2026, 7, 3),
        date(2026, 7, 6),
    ]
    run = BacktestRun(
        name="报告测试",
        signal_id="composite-v1",
        start_date=dates[0],
        end_date=dates[-1],
        rebalance_freq="5d",
        top_pct=0.1,
        params={
            "initial_capital": 1_000_000.0,
            "execution": "decision T close; fill T+1 open",
        },
        status="completed",
        summary={
            "total_cost": 1_000.0,
            "total_traded": 1_000_000.0,
            "day_errors": [],
            "missing_benchmark_days": 0,
        },
    )
    session.add(run)
    session.flush()
    rank_ics = [0.3, 0.4, 0.5, 0.4]
    for index, trade_date in enumerate(dates):
        session.add(
            BacktestDaily(
                run_id=run.id,
                trade_date=trade_date,
                rank_ic=rank_ics[index],
                long_ret=0.0 if index == 0 else strategy_nav[index] / strategy_nav[index - 1] - 1,
                ls_ret=0.01,
                turnover=None if index == 0 else 0.2,
                nav=strategy_nav[index],
                benchmark_nav=benchmark_nav[index],
                market_nav=market_nav[index],
                n_eligible=100,
                group_returns=[
                    -0.01 + group_index * 0.002
                    for group_index in range(10)
                ],
            )
        )
    session.commit()
    return int(run.id)


def test_generate_report_supports_positive_alpha_only_when_all_gates_pass(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    try:
        run_id = _seed_completed_run(
            session,
            strategy_nav=[1.0, 1.02, 1.04, 1.06],
            benchmark_nav=[1.0, 1.005, 1.01, 1.015],
            market_nav=[1.0, 1.004, 1.008, 1.012],
        )

        report = generate_report(session, run_id)

        assert report["conclusion"]["alpha_supported"] is True
        assert report["conclusion"]["status"] == "alpha_supported_in_sample"
        assert report["layers"]["top_minus_bottom"] > 0
        assert report["layers"]["strictly_monotonic"] is True
        assert report["costs"]["bps_of_traded_notional"] == 10.0
        assert report["long_short_gross_diagnostic"]["costed"] is False
        assert report["probability_calibration"]["available"] is False
        assert report["coverage"]["effective_start_date"] == "2026-07-01"
        assert report["coverage"]["first_execution_date"] == "2026-07-02"
        assert report["coverage"]["warmup_days_excluded_from_performance"] == 0
        assert any(
            item["code"] == "survivorship_bias"
            for item in report["limitations"]
        )
        json.dumps(report, allow_nan=False, ensure_ascii=False)
    finally:
        session.close()


def test_generate_report_gives_honest_negative_conclusion(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        run_id = _seed_completed_run(
            session,
            strategy_nav=[1.0, 0.98, 0.96, 0.94],
            benchmark_nav=[1.0, 1.0, 1.0, 1.0],
            market_nav=[1.0, 0.99, 0.99, 1.0],
        )

        report = generate_report(session, run_id)

        assert report["conclusion"]["alpha_supported"] is False
        assert report["conclusion"]["status"] == "no_reliable_alpha_evidence"
        assert "positive_net_return" in report["conclusion"]["failed_gates"]
        assert "beats_csi300" in report["conclusion"]["failed_gates"]
        assert "不因结论为负而调参重跑" in report["conclusion"]["policy"]
    finally:
        session.close()


def test_generate_report_rejects_missing_or_running_runs(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        running = BacktestRun(
            name="未完成",
            signal_id="composite-v1",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 2),
            params={},
            status="running",
        )
        session.add(running)
        session.commit()

        for run_id in (int(running.id), 999):
            try:
                generate_report(session, run_id)
            except ValueError:
                pass
            else:
                raise AssertionError("generate_report must reject unavailable reports")
    finally:
        session.close()


def test_performance_excludes_pre_signal_cash_warmup_for_fair_benchmarks(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    try:
        run_id = _seed_completed_run(
            session,
            strategy_nav=[1.0, 1.0, 0.9, 0.8],
            benchmark_nav=[1.0, 2.0, 2.0, 2.0],
            market_nav=[1.0, 1.5, 1.5, 1.5],
        )
        rows = list(
            session.scalars(
                select(BacktestDaily)
                .where(BacktestDaily.run_id == run_id)
                .order_by(BacktestDaily.trade_date)
            )
        )
        rows[1].turnover = None
        rows[2].turnover = 0.2
        session.commit()

        report = generate_report(session, run_id)

        assert report["coverage"]["warmup_days_excluded_from_performance"] == 1
        assert report["coverage"]["effective_start_date"] == "2026-07-02"
        assert report["coverage"]["first_execution_date"] == "2026-07-03"
        assert report["benchmarks"]["csi300"]["total_return"] == 0.0
        assert report["benchmarks"]["equal_weight_market"]["total_return"] == 0.0
        assert report["net_long_performance"]["total_return"] == pytest.approx(-0.2)
    finally:
        session.close()
