"""Is a session's daily-bar sync complete enough to build lists, score or trade on?

A sync that stops halfway (a sleeping laptop, a network outage) leaves a session with bars
for only part of the market; on 2026-09-24 the evening sheet was built on 58% of the day's
bars. Lists and scores are written create-only and the trade-action sheet is acted on, so
all three wait until the session is complete: no daily-bar sync is running, and the
session carries at least 97% of the previous session's bar count (a normal day has more
than 99%). Nothing here changes how lists are built or scored; it only says when.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.db.models import DailyBar, JobRun
from alphapilot.services.stock_picks import _sessions_after

COMPLETE_RATIO = 0.97
SYNC_JOB = "sync_daily_bars"
SYNC_RUNNING_WINDOW = timedelta(hours=6)  # an older "running" row is a killed process


def bar_count(session: Session, day: date) -> int:
    return int(session.scalar(select(func.count()).where(DailyBar.trade_date == day)) or 0)


def previous_session(session: Session, day: date) -> date | None:
    value = session.scalar(select(func.max(DailyBar.trade_date)).where(DailyBar.trade_date < day))
    return value if value is None or isinstance(value, date) else date.fromisoformat(str(value))


def session_coverage(
    session: Session, day: date, *, now: datetime | None = None
) -> tuple[bool, dict[str, Any]]:
    """``(complete, details)`` for the bars of ``day``."""

    current = (now or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
    running = session.scalar(
        select(JobRun.id)
        .where(
            JobRun.job_name == SYNC_JOB,
            JobRun.status == "running",
            JobRun.started_at >= current - SYNC_RUNNING_WINDOW,
        )
        .limit(1)
    )
    bars = bar_count(session, day)
    previous = previous_session(session, day)
    previous_bars = bar_count(session, previous) if previous is not None else 0
    complete = running is None and bars > 0 and bars >= COMPLETE_RATIO * previous_bars
    return complete, {
        "day": day.isoformat(),
        "bars": bars,
        "previous_session": previous.isoformat() if previous else None,
        "previous_bars": previous_bars,
        "sync_running": running is not None,
    }


def horizon_ready(
    session: Session,
    as_of: date,
    horizon: int,
    *,
    cache: dict[date, tuple[bool, dict[str, Any]]] | None = None,
    now: datetime | None = None,
) -> tuple[bool | None, dict[str, Any]]:
    """``None`` until the horizon has matured, then whether its entry and exit are complete.

    Uses the scorer's own session rule, so the dates checked are the dates it would score.
    """

    sessions = _sessions_after(session, as_of, horizon)
    if len(sessions) < horizon:
        return None, {}
    seen = {} if cache is None else cache
    for day in (sessions[0], sessions[horizon - 1]):
        if day not in seen:
            seen[day] = session_coverage(session, day, now=now)
        complete, details = seen[day]
        if not complete:
            return False, details
    return True, {}
