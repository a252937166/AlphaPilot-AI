from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.core.timeutil import iso_utc
from alphapilot.db.engine import get_session
from alphapilot.db.models import JobRun
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register
from alphapilot.services.alert_outcomes import evaluate_mature_alerts

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
UPSTREAM_JOBS = ("sync_daily_bars", "compute_style_daily")
UPSTREAM_LEASES = {
    "sync_daily_bars": timedelta(hours=2),
    "compute_style_daily": timedelta(minutes=30),
}


def _as_utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(UTC)


def _job_now() -> datetime:
    return datetime.now(UTC)


def _upstream_state(
    session: Session,
    *,
    now: datetime,
) -> tuple[list[str], list[dict[str, Any]]]:
    rows = session.scalars(
        select(JobRun)
        .where(
            JobRun.job_name.in_(UPSTREAM_JOBS),
            JobRun.status == "running",
        )
        .order_by(JobRun.started_at, JobRun.id)
    ).all()
    active: set[str] = set()
    stale: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row.started_at, datetime):
            stale.append({"id": row.id, "job_name": row.job_name, "started_at": None})
            continue
        started_at = _as_utc(row.started_at)
        lease = UPSTREAM_LEASES[row.job_name]
        age = max(0.0, (now - started_at).total_seconds())
        if now - started_at <= lease:
            active.add(row.job_name)
            continue
        stale.append(
            {
                "id": row.id,
                "job_name": row.job_name,
                "started_at": iso_utc(row.started_at),
                "age_seconds": round(age, 1),
            }
        )
    return sorted(active), stale


def _evaluation_time(as_of_date: date | None) -> datetime:
    if as_of_date is None:
        return datetime.now(UTC)
    return datetime.combine(as_of_date, time.max, tzinfo=MARKET_TIMEZONE).astimezone(UTC)


def evaluate_alerts(as_of_date: date | None = None) -> dict[str, Any]:
    """Evaluate mature five-session alert outcomes from complete cached closes."""

    started = monotonic()
    as_of = _evaluation_time(as_of_date)
    job_now = _job_now()
    with get_session() as session:
        running, stale = _upstream_state(session, now=job_now)
        if running:
            raise JobExecutionError(
                "日线同步或风格计算仍在运行，提醒归因已延后。",
                stats={
                    "as_of": as_of.isoformat(),
                    "running_jobs": running,
                    "stale_upstream_runs": stale,
                    "reason": "upstream_job_running",
                },
            )
        stats = evaluate_mature_alerts(session, as_of=as_of)
        stats["stale_upstream_runs"] = stale
        stats["warning_count"] = len(stale)
        stats["warnings"] = (
            ["检测到超出租约的上游 running 审计行，已记录并继续归因。"] if stale else []
        )
    stats["duration_seconds"] = round(monotonic() - started, 2)
    return stats


def register_alert_outcomes_job() -> None:
    register(
        JobSpec(
            name="evaluate_alerts",
            func=evaluate_alerts,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour="19,20",
                minute=45,
                timezone=MARKET_TIMEZONE,
            ),
        )
    )
