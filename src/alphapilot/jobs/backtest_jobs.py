from __future__ import annotations

from datetime import timedelta
from time import monotonic, sleep
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select

from alphapilot.backtest.adjust import sync_adj_factors
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


def _wait_for_daily_bars() -> float:
    started = monotonic()
    while _daily_bars_running():
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
    return monotonic() - started


def sync_adj_factors_job() -> dict[str, Any]:
    """Run the incremental, source-audited adjustment-factor sync."""

    waited = _wait_for_daily_bars()
    with get_session() as session:
        stats = sync_adj_factors(
            session,
            refresh_latest=waited >= _DAILY_BAR_POLL_SECONDS,
        )
    stats["waited_for_daily_bars_seconds"] = round(waited, 2)
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
