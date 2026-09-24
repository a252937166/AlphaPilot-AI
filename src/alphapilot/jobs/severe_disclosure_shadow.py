"""Shadow the severe screen with jev: one typed question per new CNInfo title.

Runs beside ``severe_disclosure_screen`` and never changes it, because the screen is an
input to the frozen stock-pick universe. Each run takes the next batch of announcements
after the cursor in ``<severe_shadow_dir>/state.json``, skips the ones the screen treats
as stale history, asks jev about the rest and appends one record per title to
``<severe_shadow_dir>/<Shanghai date>.jsonl``. The first run starts at the screen's go-live
day, so the backlog is replayed batch by batch.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.core.config import get_settings
from alphapilot.db.engine import get_session
from alphapilot.db.models import NewsItem
from alphapilot.jobs.registry import JobOutcome, JobSpec, register
from alphapilot.llm.typesafe import JevClient
from alphapilot.services.severe_shadow import (
    CONTEXT_DAYS,
    MARKET_TIMEZONE,
    OUT_OF_CREDITS,
    PREFILTER,
    QUESTION_VERSION,
    Announcement,
    Asker,
    append_records,
    ask_titles,
    build_record,
    build_state,
    jev_severe,
    pick_context,
    rule_severe,
)

JOB_NAME = "severe_disclosure_shadow"
SOURCE = "cninfo"
START = datetime(2026, 9, 11, tzinfo=MARKET_TIMEZONE)  # the screen went live that day
STALE_DAYS = 7  # the screen's lookback: older than this at ingestion is history
BATCH = 1500  # titles asked per run
SCAN = 20000  # rows examined per run, so a stretch of stale backfill is crossed quickly
WORKERS = 8
MAX_ERROR_SHARE = 0.5
STATE_FILE = "state.json"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _load_cursor(root: Path) -> int | None:
    path = root / STATE_FILE
    if not path.exists():
        return None
    return int(json.loads(path.read_text(encoding="utf-8"))["last_news_id"])


def _save_cursor(root: Path, news_id: int, now: datetime) -> None:
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / (STATE_FILE + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "last_news_id": news_id,
                "question_version": QUESTION_VERSION,
                "updated_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    temporary.replace(root / STATE_FILE)


def _is_stale(published_at: datetime | None, available_time: datetime) -> bool:
    return published_at is not None and _as_utc(published_at) < _as_utc(available_time) - timedelta(
        days=STALE_DAYS
    )


def _context_pool(session: Session, targets: Sequence[Announcement]) -> dict[str, list[Any]]:
    """Every title of the targets' companies that could enter a target's context."""

    symbols = sorted({target.symbol for target in targets if target.symbol})
    if not symbols:
        return {}
    low = min(_as_utc(t.available_time) for t in targets) - timedelta(days=CONTEXT_DAYS)
    high = max(_as_utc(t.available_time) for t in targets)
    pool: dict[str, list[Any]] = defaultdict(list)
    for start in range(0, len(symbols), 500):
        for row in session.execute(
            select(
                NewsItem.id,
                NewsItem.symbol,
                NewsItem.title,
                NewsItem.published_at,
                NewsItem.available_time,
            ).where(
                NewsItem.source == SOURCE,
                NewsItem.symbol.in_(symbols[start : start + 500]),
                NewsItem.available_time >= low,
                NewsItem.available_time <= high,
            )
        ):
            pool[row.symbol].append(row)
    return pool


def run_severe_disclosure_shadow(
    *,
    output_dir: str | Path | None = None,
    client: Asker | None = None,
    batch: int = BATCH,
    scan: int = SCAN,
    workers: int = WORKERS,
    now: datetime | None = None,
) -> dict[str, Any] | JobOutcome:
    started = monotonic()
    settings = get_settings()
    current = _as_utc(now or datetime.now(UTC))
    root = Path(output_dir) if output_dir is not None else Path(settings.severe_shadow_dir)
    if client is None and not settings.jev_api_key:
        return JobOutcome(status="degraded", stats={"skipped": "jev_not_configured"})
    cursor = _load_cursor(root)
    with get_session() as session:
        if cursor is None:
            first = session.scalar(
                select(func.min(NewsItem.id)).where(
                    NewsItem.source == SOURCE, NewsItem.available_time >= START.astimezone(UTC)
                )
            )
            cursor = first - 1 if first is not None else 0
        rows = session.execute(
            select(
                NewsItem.id,
                NewsItem.symbol,
                NewsItem.title,
                NewsItem.published_at,
                NewsItem.available_time,
            )
            .where(NewsItem.source == SOURCE, NewsItem.id > cursor)
            .order_by(NewsItem.id)
            .limit(scan)
        ).all()
        fresh: list[Any] = []
        examined = 0
        for row in rows:
            if len(fresh) == batch:
                break
            examined += 1
            if PREFILTER.search(row.title) and not _is_stale(row.published_at, row.available_time):
                fresh.append(row)
        rows = rows[:examined]
        pool = _context_pool(session, fresh)
    contexts = {row.id: pick_context(row, pool.get(row.symbol or "", [])) for row in fresh}
    stats: dict[str, Any] = {
        "cursor_from": cursor,
        "fetched": len(rows),
        "stale_or_filtered": 0,
        "asked": 0,
        "errors": 0,
        "rule_severe": 0,
        "jev_severe": 0,
        "written": {},
    }
    if not rows:
        stats["duration_seconds"] = round(monotonic() - started, 2)
        return stats
    stats["stale_or_filtered"] = len(rows) - len(fresh)
    own: JevClient | None = None
    asker: Asker
    if client is None:
        own = asker = JevClient(settings.jev_api_key, settings.jev_model)
    else:
        asker = client
    try:
        states = {row.id: build_state(row.title, contexts[row.id]) for row in fresh}
        results = ask_titles(asker, states, workers=workers)
    finally:
        if own is not None:
            own.close()
    stats["asked"] = len(fresh)
    stats["errors"] = sum(1 for result in results.values() if "error" in result)
    if any(result.get("error") == OUT_OF_CREDITS for result in results.values()):
        # The shared jev account is empty: keep the cursor and let the owner top it up.
        stats["duration_seconds"] = round(monotonic() - started, 2)
        return JobOutcome(status="degraded", stats={**stats, "reason": "jev_out_of_credits"})
    if fresh and stats["errors"] > MAX_ERROR_SHARE * len(fresh):
        # jev is down or refusing: keep the cursor so the batch is asked again next run.
        stats["duration_seconds"] = round(monotonic() - started, 2)
        return JobOutcome(status="degraded", stats={**stats, "reason": "jev_mostly_failed"})
    records = [
        build_record(
            news_id=row.id,
            symbol=row.symbol,
            title=row.title,
            published_at=row.published_at,
            available_time=row.available_time,
            result=results[row.id],
            model=asker.model,
            asked_at=current,
            context_ids=[item.id for item in contexts[row.id]],
        )
        for row in fresh
    ]
    stats["rule_severe"] = sum(1 for record in records if rule_severe(record))
    stats["jev_severe"] = sum(1 for record in records if jev_severe(record))
    stats["written"] = append_records(root, records)
    stats["cursor_to"] = rows[-1].id
    _save_cursor(root, rows[-1].id, current)
    stats["duration_seconds"] = round(monotonic() - started, 2)
    return stats


def register_severe_disclosure_shadow_job() -> None:
    register(
        JobSpec(
            name=JOB_NAME,
            func=run_severe_disclosure_shadow,
            trigger=IntervalTrigger(minutes=10),
            enabled_key="severe_shadow_enabled",
            misfire_grace_time=300,
        )
    )
