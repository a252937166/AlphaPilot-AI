from __future__ import annotations

from datetime import timedelta
from time import monotonic, sleep
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select

from alphapilot.backtest.adjust import sync_adj_factors
from alphapilot.core.job_execution_context import current_job_run
from alphapilot.db.engine import get_session
from alphapilot.db.models import JobRun, utcnow
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register

_DAILY_BAR_WAIT_TIMEOUT_SECONDS = 30 * 60
_DAILY_BAR_POLL_SECONDS = 5.0
_RECENT_JOB_WINDOW = timedelta(hours=2)


def _daily_bars_running() -> bool:
    cutoff = utcnow() - _RECENT_JOB_WINDOW
    with get_session() as session:
        return bool(
            session.scalar(
                select(func.count())
                .select_from(JobRun)
                .where(
                    JobRun.job_name == "sync_daily_bars",
                    JobRun.status == "running",
                    JobRun.started_at >= cutoff,
                )
            )
        )


def _wait_for_daily_bars() -> tuple[float, bool]:
    started = monotonic()
    did_wait = False
    while _daily_bars_running():
        did_wait = True
        waited = monotonic() - started
        if waited >= _DAILY_BAR_WAIT_TIMEOUT_SECONDS:
            raise JobExecutionError(
                "等待日线同步完成超过 30 分钟，复权因子同步未启动。",
                stats={
                    "reason": "daily_bars_wait_timeout",
                    "waited_seconds": round(waited, 2),
                },
            )
        sleep(_DAILY_BAR_POLL_SECONDS)
    return monotonic() - started, did_wait


def _recovery_plan_supersession(
    *,
    expected_daily_bars_job_run_id: int,
    expected_timed_out_adj_factors_job_run_id: int,
) -> dict[str, Any] | None:
    """Recheck a catch-up plan after the per-job process lock is held."""

    current = current_job_run()
    if current is None or current.job_name != "sync_adj_factors":
        raise JobExecutionError(
            "复权因子追补缺少当前审计任务身份。",
            stats={"reason": "adjustment_factor_recovery_missing_job_context"},
        )
    with get_session() as session:
        latest_daily = session.scalar(
            select(JobRun)
            .where(JobRun.job_name == "sync_daily_bars")
            .order_by(JobRun.id.desc())
            .limit(1)
        )
        predecessor = session.scalar(
            select(JobRun)
            .where(
                JobRun.job_name == "sync_adj_factors",
                JobRun.id < current.run_id,
            )
            .order_by(JobRun.id.desc())
            .limit(1)
        )
    if (
        latest_daily is None
        or latest_daily.id != expected_daily_bars_job_run_id
        or latest_daily.status != "ok"
        or predecessor is None
    ):
        raise JobExecutionError(
            "复权因子追补计划对应的日线血统已变化。",
            stats={
                "reason": "adjustment_factor_recovery_lineage_changed",
                "expected_daily_bars_job_run_id": expected_daily_bars_job_run_id,
                "actual_daily_bars_job_run_id": (
                    latest_daily.id if latest_daily is not None else None
                ),
                "expected_timed_out_adj_factors_job_run_id": (
                    expected_timed_out_adj_factors_job_run_id
                ),
                "actual_predecessor_adj_factors_job_run_id": (
                    predecessor.id if predecessor is not None else None
                ),
            },
        )
    if predecessor.id == expected_timed_out_adj_factors_job_run_id:
        predecessor_stats = predecessor.stats if isinstance(predecessor.stats, dict) else {}
        if (
            predecessor.status == "failed"
            and predecessor_stats.get("reason") == "daily_bars_wait_timeout"
        ):
            return None
        raise JobExecutionError(
            "复权因子追补前驱不再是获准恢复的等待超时。",
            stats={
                "reason": "adjustment_factor_recovery_predecessor_changed",
                "expected_timed_out_adj_factors_job_run_id": (
                    expected_timed_out_adj_factors_job_run_id
                ),
                "actual_predecessor_status": predecessor.status,
                "actual_predecessor_reason": predecessor_stats.get("reason"),
            },
        )

    predecessor_stats = predecessor.stats if isinstance(predecessor.stats, dict) else {}
    if predecessor.id < expected_timed_out_adj_factors_job_run_id:
        raise JobExecutionError(
            "复权因子追补计划绑定的超时前驱已不可见。",
            stats={
                "reason": "adjustment_factor_recovery_expected_predecessor_missing",
                "expected_timed_out_adj_factors_job_run_id": (
                    expected_timed_out_adj_factors_job_run_id
                ),
                "actual_predecessor_adj_factors_job_run_id": predecessor.id,
            },
        )
    if (
        predecessor.id > expected_timed_out_adj_factors_job_run_id
        and predecessor.status == "ok"
    ):
        return {
            **predecessor_stats,
            "recovery_plan_superseded": True,
            "recovery_provider_skipped": True,
            "recovery_successor_job_run_id": predecessor.id,
            "expected_timed_out_adj_factors_job_run_id": (
                expected_timed_out_adj_factors_job_run_id
            ),
        }
    raise JobExecutionError(
        "复权因子追补计划已被另一条未成功运行取代。",
        stats={
            "reason": "adjustment_factor_recovery_superseded_by_non_success",
            "expected_timed_out_adj_factors_job_run_id": (
                expected_timed_out_adj_factors_job_run_id
            ),
            "actual_predecessor_adj_factors_job_run_id": predecessor.id,
            "actual_predecessor_status": predecessor.status,
            "actual_predecessor_stats": predecessor_stats,
        },
    )


def sync_adj_factors_job(
    *,
    force_refresh_latest: bool = False,
    recovery_expected_daily_bars_job_run_id: int | None = None,
    recovery_expected_timed_out_adj_factors_job_run_id: int | None = None,
) -> dict[str, Any]:
    """Run the incremental, source-audited adjustment-factor sync."""

    recovery_ids = (
        recovery_expected_daily_bars_job_run_id,
        recovery_expected_timed_out_adj_factors_job_run_id,
    )
    if (recovery_ids[0] is None) != (recovery_ids[1] is None):
        raise JobExecutionError(
            "复权因子追补必须同时绑定日线与超时任务。",
            stats={"reason": "adjustment_factor_recovery_binding_incomplete"},
        )
    if recovery_ids[0] is not None and recovery_ids[1] is not None:
        superseded = _recovery_plan_supersession(
            expected_daily_bars_job_run_id=recovery_ids[0],
            expected_timed_out_adj_factors_job_run_id=recovery_ids[1],
        )
        if superseded is not None:
            superseded["waited_for_daily_bars_seconds"] = 0.0
            superseded["forced_refresh_latest"] = force_refresh_latest
            return superseded

    waited, did_wait = _wait_for_daily_bars()
    with get_session() as session:
        stats: dict[str, Any] = sync_adj_factors(
            session,
            refresh_latest=did_wait or force_refresh_latest,
        )
    stats["waited_for_daily_bars_seconds"] = round(waited, 2)
    stats["forced_refresh_latest"] = force_refresh_latest
    return stats


def register_backtest_jobs() -> None:
    register(
        JobSpec(
            name="sync_adj_factors",
            func=sync_adj_factors_job,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=18,
                minute=50,
                timezone=ZoneInfo("Asia/Shanghai"),
            ),
        )
    )
