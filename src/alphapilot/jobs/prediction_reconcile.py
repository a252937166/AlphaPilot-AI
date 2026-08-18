from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from time import monotonic
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.core.config import get_settings
from alphapilot.data.provenance import AUDITED_DAILY_BAR_SOURCES
from alphapilot.db.engine import get_session
from alphapilot.db.models import (
    CompositeScore,
    DailyBar,
    FactorValue,
    JobRun,
    Security,
    StockScore,
    StyleDaily,
)
from alphapilot.engines.factors import FACTOR_SET, load_weights
from alphapilot.engines.stock_score import MODEL_VERSION as STOCK_SCORE_MODEL_VERSION
from alphapilot.jobs.factors import (
    MARKET_TIMEZONE,
    MIN_INPUT_COVERAGE,
    _live_input_contract,
    _market_coverage,
)
from alphapilot.jobs.registry import (
    JobExecutionError,
    JobOutcome,
    JobSpec,
    register,
    run_job,
)
from alphapilot.jobs.style import MODEL_VERSION as STYLE_MODEL_VERSION


@dataclass(frozen=True, slots=True)
class _PredictionState:
    target_date: date | None
    factor_current: bool
    style_current: bool
    details: dict[str, Any]


def _latest_run(session: Session, job_name: str) -> JobRun | None:
    return session.scalar(
        select(JobRun).where(JobRun.job_name == job_name).order_by(JobRun.id.desc()).limit(1)
    )


def _completed_run_for_date(
    session: Session,
    job_name: str,
    target_date: date,
) -> JobRun | None:
    target = target_date.isoformat()
    rows = session.scalars(
        select(JobRun)
        .where(JobRun.job_name == job_name, JobRun.status == "ok")
        .order_by(JobRun.id.desc())
        .limit(100)
    ).all()
    for row in rows:
        stats = row.stats if isinstance(row.stats, dict) else {}
        if stats.get("date") == target and stats.get("skipped") is None:
            return row
    return None


def _target_date(session: Session) -> date | None:
    return session.scalar(
        select(func.max(DailyBar.trade_date))
        .select_from(DailyBar)
        .join(Security, Security.symbol == DailyBar.symbol)
        .where(
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
            Security.market == "CN",
            Security.list_status == "listed",
        )
    )


def _run_stats(run: JobRun | None) -> dict[str, Any]:
    if run is None or not isinstance(run.stats, dict):
        return {}
    return dict(run.stats)


def _prediction_state(session: Session) -> _PredictionState:
    target = _target_date(session)
    if target is None:
        return _PredictionState(
            target_date=None,
            factor_current=False,
            style_current=False,
            details={"reason": "no_daily_bars"},
        )

    settings = get_settings()
    weights = load_weights(settings.factor_weights_file)
    factor_model_version = f"factor-{weights.version}"
    score_model_version = f"factor-score-{weights.version}"
    market_coverage = _market_coverage(session, target)
    universe = int(market_coverage["universe"])
    factor_run = _completed_run_for_date(session, "compute_factors", target)
    daily_run = _latest_run(session, "sync_daily_bars")
    adj_run = _latest_run(session, "sync_adj_factors")
    valuation_run = _latest_run(session, "sync_valuation_daily")
    daily_stats = _run_stats(daily_run)
    live_input_stats, live_input_reason = _live_input_contract(
        session,
        target_date=target,
        universe=universe,
    )
    factor_input_reason = live_input_reason
    if (
        factor_input_reason is None
        and float(market_coverage["input_coverage"]) < MIN_INPUT_COVERAGE
    ):
        factor_input_reason = "input_coverage_below_floor"

    upstream_runs = (daily_run, adj_run, valuation_run)
    upstream_contract_ok = (
        all(
            run is not None and run.status == "ok" and run.finished_at is not None
            for run in upstream_runs
        )
        and daily_stats.get("latest_trade_date") == target.isoformat()
        and factor_input_reason is None
    )
    latest_upstream_finish: datetime | None = None
    if upstream_contract_ok:
        finished_times = [
            run.finished_at
            for run in upstream_runs
            if run is not None and run.finished_at is not None
        ]
        latest_upstream_finish = max(finished_times)

    factor_audit_fresh = (
        factor_run is not None
        and factor_run.finished_at is not None
        and latest_upstream_finish is not None
        and factor_run.finished_at >= latest_upstream_finish
    )
    factor_counts: dict[str, int] = {}
    factor_rows = 0
    composite_rows = 0
    stock_score_rows = 0
    output_contract_ok = False
    if factor_audit_fresh:
        factor_counts = {
            str(factor): int(count)
            for factor, count in session.execute(
                select(FactorValue.factor, func.count())
                .where(
                    FactorValue.factor.in_(FACTOR_SET),
                    FactorValue.trade_date == target,
                    FactorValue.model_version == factor_model_version,
                )
                .group_by(FactorValue.factor)
            ).all()
        }
        factor_rows = sum(factor_counts.values())
        composite_rows = int(
            session.scalar(
                select(func.count())
                .select_from(CompositeScore)
                .where(
                    CompositeScore.trade_date == target,
                    CompositeScore.model_version == score_model_version,
                )
            )
            or 0
        )
        stock_score_rows = int(
            session.scalar(
                select(func.count())
                .select_from(StockScore)
                .where(
                    StockScore.trade_date == target,
                    StockScore.model_version == STOCK_SCORE_MODEL_VERSION,
                )
            )
            or 0
        )
        output_contract_ok = (
            universe > 0
            and composite_rows / universe >= MIN_INPUT_COVERAGE
            and len(factor_counts) == len(FACTOR_SET)
            and all(count == composite_rows for count in factor_counts.values())
            and stock_score_rows == composite_rows
        )
    factor_current = factor_audit_fresh and output_contract_ok
    style_run = (
        _completed_run_for_date(session, "compute_style_daily", target) if factor_current else None
    )
    style_row = session.get(StyleDaily, target) if factor_current else None
    style_current = (
        factor_current
        and style_row is not None
        and style_row.model_version == STYLE_MODEL_VERSION
        and style_run is not None
        and style_run.finished_at is not None
        and factor_run is not None
        and factor_run.finished_at is not None
        and style_run.finished_at >= factor_run.finished_at
    )
    return _PredictionState(
        target_date=target,
        factor_current=factor_current,
        style_current=style_current,
        details={
            "date": target.isoformat(),
            "universe": universe,
            "eligible": int(market_coverage["eligible"]),
            "input_coverage": float(market_coverage["input_coverage"]),
            "factor_rows": factor_rows,
            "factor_counts": factor_counts,
            "composite_rows": composite_rows,
            "stock_score_rows": stock_score_rows,
            "factor_job_run_id": factor_run.id if factor_run is not None else None,
            "style_job_run_id": style_run.id if style_run is not None else None,
            "daily_bars_job_run_id": daily_run.id if daily_run is not None else None,
            "adj_factors_job_run_id": adj_run.id if adj_run is not None else None,
            "valuation_job_run_id": valuation_run.id if valuation_run is not None else None,
            "upstream_contract_ok": upstream_contract_ok,
            "factor_audit_fresh": factor_audit_fresh,
            "output_counts_checked": factor_audit_fresh,
            "output_contract_ok": output_contract_ok,
            "live_input_reason": live_input_reason,
            "factor_input_reason": factor_input_reason,
            "live_input_stats": live_input_stats,
        },
    )


def prediction_outputs_readiness(
    session: Session,
) -> tuple[date | None, bool, dict[str, Any]]:
    """Expose the reconciler's full output contract to downstream read-only jobs."""

    state = _prediction_state(session)
    return (
        state.target_date,
        state.factor_current and state.style_current,
        dict(state.details),
    )


def _failed_child_stats(
    *,
    child_name: str,
    child_run: JobRun,
    state: _PredictionState,
) -> dict[str, Any]:
    return {
        **state.details,
        "reason": f"{child_name}_failed",
        "child_job_run_id": child_run.id,
        "child_status": child_run.status,
        "child_error": child_run.error,
        "child_stats": _run_stats(child_run),
    }


def reconcile_prediction_outputs() -> dict[str, Any] | JobOutcome:
    """Converge the latest complete EOD inputs to factors and style outputs."""

    started = monotonic()
    with get_session() as session:
        initial = _prediction_state(session)
    if initial.target_date is None:
        return {
            **initial.details,
            "skipped": "no_daily_bars",
            "duration_seconds": round(monotonic() - started, 2),
        }
    if initial.factor_current and initial.style_current:
        return {
            **initial.details,
            "skipped": "already_current",
            "duration_seconds": round(monotonic() - started, 2),
        }
    if not initial.factor_current and initial.details.get("upstream_contract_ok") is not True:
        return {
            **initial.details,
            "skipped": "upstream_inputs_not_final",
            "upstream_reason": initial.details.get("factor_input_reason")
            or initial.details.get("live_input_reason")
            or "upstream_audit_not_final",
            "message": "盘后输入尚未形成完整终态，等待上游恢复后自动补算。",
            "duration_seconds": round(monotonic() - started, 2),
        }

    factor_run_id: int | None = None
    if not initial.factor_current:
        factor_run = run_job("compute_factors", allow_catchup=True)
        factor_run_id = factor_run.id
        if factor_run.status != "ok":
            return JobOutcome(
                status="degraded",
                stats={
                    **_failed_child_stats(
                        child_name="compute_factors",
                        child_run=factor_run,
                        state=initial,
                    ),
                    "skipped": "compute_factors_failed",
                    "message": "自动补算因子失败，风格计算未启动。",
                    "duration_seconds": round(monotonic() - started, 2),
                },
            )
        factor_stats = _run_stats(factor_run)
        if factor_stats.get("skipped") is not None:
            return {
                **initial.details,
                "factor_job_run_id": factor_run.id,
                "skipped": factor_stats["skipped"],
                "factor_stats": factor_stats,
                "duration_seconds": round(monotonic() - started, 2),
            }
        if factor_stats.get("date") != initial.target_date.isoformat():
            raise JobExecutionError(
                "自动补算因子日期与最新日线不一致，风格计算未启动。",
                stats={
                    **initial.details,
                    "reason": "factor_date_mismatch",
                    "factor_job_run_id": factor_run.id,
                    "factor_stats": factor_stats,
                },
            )

    with get_session() as session:
        after_factor = _prediction_state(session)
    if not after_factor.factor_current:
        raise JobExecutionError(
            "因子子任务结束后输出仍未满足完整性合同。",
            stats={
                **after_factor.details,
                "reason": "factor_output_not_current",
                "factor_job_run_id": factor_run_id,
            },
        )

    style_run_id: int | None = None
    if not after_factor.style_current:
        assert after_factor.target_date is not None
        style_run = run_job(
            "compute_style_daily",
            trade_date=after_factor.target_date,
        )
        style_run_id = style_run.id
        style_stats = _run_stats(style_run)
        if style_run.status != "ok":
            return JobOutcome(
                status="degraded",
                stats={
                    **_failed_child_stats(
                        child_name="compute_style_daily",
                        child_run=style_run,
                        state=after_factor,
                    ),
                    "skipped": "compute_style_daily_failed",
                    "message": "自动补算风格失败。",
                    "duration_seconds": round(monotonic() - started, 2),
                },
            )
        if style_stats.get("skipped") is not None:
            return {
                **after_factor.details,
                "style_job_run_id": style_run.id,
                "skipped": style_stats["skipped"],
                "style_stats": style_stats,
                "duration_seconds": round(monotonic() - started, 2),
            }

    with get_session() as session:
        completed = _prediction_state(session)
    if not completed.factor_current or not completed.style_current:
        raise JobExecutionError(
            "预测链补算结束后仍未收敛到完整终态。",
            stats={
                **completed.details,
                "reason": "prediction_outputs_not_current",
                "factor_job_run_id": factor_run_id,
                "style_job_run_id": style_run_id,
            },
        )
    return {
        **completed.details,
        "factor_job_run_id": factor_run_id or completed.details["factor_job_run_id"],
        "style_job_run_id": style_run_id or completed.details["style_job_run_id"],
        "skipped": None,
        "duration_seconds": round(monotonic() - started, 2),
    }


def register_prediction_reconcile_job() -> None:
    register(
        JobSpec(
            name="reconcile_prediction_outputs",
            func=reconcile_prediction_outputs,
            trigger=CronTrigger(
                day_of_week="mon-sat",
                hour="0-1,7-9,19-23",
                minute="7,22,37,52",
                timezone=MARKET_TIMEZONE,
            ),
        )
    )
