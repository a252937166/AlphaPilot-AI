"""Is a session's data complete enough to build lists, score or trade on?

A sync that stops halfway (a sleeping laptop, a network outage) leaves a session with rows
for only part of the market; on 2026-09-24 the evening sheet was built on 58% of the day's
bars. Lists and scores are written create-only and the trade-action sheet is acted on, so
all three wait until the session is complete: no sync is running, and the session carries
at least 97% of the largest count among the five sessions before it (a normal day has more
than 99%). The largest rather than the previous one, because broken evenings come in a
row: the Eastmoney valuation of 2026-09-28 and 09-29 both stopped partway. Lists also need
the day's valuation, which feeds their value signals. Nothing here changes how lists are
built or scored; it only says when.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.db.models import DailyBar, JobRun, ValuationDaily
from alphapilot.services.stock_picks import _sessions_after

COMPLETE_RATIO = 0.97
REFERENCE_SESSIONS = 5
SYNC_JOB = "sync_daily_bars"
VALUATION_JOB = "sync_valuation_daily"
SYNC_RUNNING_WINDOW = timedelta(hours=6)  # an older "running" row is a killed process


def bar_count(session: Session, day: date) -> int:
    return int(session.scalar(select(func.count()).where(DailyBar.trade_date == day)) or 0)


def valuation_count(session: Session, day: date) -> int:
    return int(session.scalar(select(func.count()).where(ValuationDaily.trade_date == day)) or 0)


def previous_sessions(session: Session, day: date, count: int = REFERENCE_SESSIONS) -> list[date]:
    """The market sessions before ``day``, newest first."""

    rows = session.scalars(
        select(DailyBar.trade_date)
        .where(DailyBar.trade_date < day)
        .group_by(DailyBar.trade_date)
        .order_by(DailyBar.trade_date.desc())
        .limit(count)
    ).all()
    return [r if isinstance(r, date) else date.fromisoformat(str(r)) for r in rows]


def _sync_running(session: Session, job: str, now: datetime | None) -> bool:
    current = (now or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
    running = session.scalar(
        select(JobRun.id)
        .where(
            JobRun.job_name == job,
            JobRun.status == "running",
            JobRun.started_at >= current - SYNC_RUNNING_WINDOW,
        )
        .limit(1)
    )
    return running is not None


def _coverage(
    session: Session,
    day: date,
    counter: Callable[[Session, date], int],
    job: str,
    now: datetime | None,
) -> tuple[bool, dict[str, Any]]:
    running = _sync_running(session, job, now)
    rows = counter(session, day)
    earlier = previous_sessions(session, day)
    reference = max((counter(session, d) for d in earlier), default=0)
    complete = not running and rows > 0 and rows >= COMPLETE_RATIO * reference
    return complete, {
        "day": day.isoformat(),
        "rows": rows,
        "reference_rows": reference,
        "reference_sessions": [d.isoformat() for d in earlier],
        "sync_running": running,
    }


def session_coverage(
    session: Session, day: date, *, now: datetime | None = None
) -> tuple[bool, dict[str, Any]]:
    """``(complete, details)`` for the daily bars of ``day``."""

    complete, details = _coverage(session, day, bar_count, SYNC_JOB, now)
    return complete, {**details, "data": "daily_bars"}


def valuation_coverage(
    session: Session, day: date, *, now: datetime | None = None
) -> tuple[bool, dict[str, Any]]:
    """``(complete, details)`` for the Eastmoney valuation rows of ``day``."""

    complete, details = _coverage(session, day, valuation_count, VALUATION_JOB, now)
    return complete, {**details, "data": "valuation"}


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
