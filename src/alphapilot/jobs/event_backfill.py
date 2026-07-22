from __future__ import annotations

from time import monotonic
from typing import Any

from sqlalchemy import select

from alphapilot.db.engine import get_session
from alphapilot.db.models import Disclosure, DomainEvent
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register
from alphapilot.services.event_extract import (
    classify_disclosure,
    extracted_disclosure_subtype,
    persist_disclosure_event,
)


def _pending_disclosure_ids() -> tuple[list[int], int]:
    """Return legacy/missing event IDs and the total disclosure count."""

    with get_session() as session:
        disclosure_ids = list(
            session.scalars(select(Disclosure.id).order_by(Disclosure.id)).all()
        )
        events = {
            event.source_ref: extracted_disclosure_subtype(event)
            for event in session.scalars(
                select(DomainEvent).where(DomainEvent.source_ref.like("disclosure:%"))
            ).all()
            if event.source_ref is not None
        }

    pending = [
        disclosure_id
        for disclosure_id in disclosure_ids
        if events.get(f"disclosure:{disclosure_id}") is None
    ]
    return pending, len(disclosure_ids)


def backfill_events() -> dict[str, Any]:
    """Classify stored disclosures that lack a normalized event.

    The read session closes before each LLM wait, and persistence uses a fresh
    short transaction afterward. A failed row remains pending for the next run.
    """

    started = monotonic()
    pending_ids, total = _pending_disclosure_ids()
    stats: dict[str, Any] = {
        "total": total,
        "pending": len(pending_ids),
        "scanned": 0,
        "extracted": 0,
        "llm": 0,
        "fallback": 0,
        "failed": 0,
        "skipped": total - len(pending_ids),
        "failures": [],
    }

    for disclosure_id in pending_ids:
        stats["scanned"] += 1
        try:
            with get_session() as read_session:
                disclosure = read_session.get(Disclosure, disclosure_id)
                if disclosure is None:
                    raise LookupError(f"disclosure {disclosure_id} disappeared")
                title = disclosure.title

            extraction = classify_disclosure(title)

            with get_session() as write_session:
                disclosure = write_session.get(Disclosure, disclosure_id)
                if disclosure is None:
                    raise LookupError(f"disclosure {disclosure_id} disappeared")
                event = persist_disclosure_event(
                    write_session,
                    disclosure,
                    extraction,
                )
                if event is None:
                    raise RuntimeError("extractor returned no event")
            # Count the row only after the write context has committed. A
            # commit-time failure must remain pending and count solely failed.
            stats["extracted"] += 1
            if extraction.source == "llm":
                stats["llm"] += 1
            else:
                stats["fallback"] += 1
        except Exception as exc:
            stats["failed"] += 1
            failures = stats["failures"]
            if isinstance(failures, list) and len(failures) < 20:
                failures.append(
                    {
                        "disclosure_id": disclosure_id,
                        "error": f"{type(exc).__name__}: {exc}"[:500],
                    }
                )

    stats["duration_seconds"] = round(monotonic() - started, 2)
    if stats["failed"]:
        raise JobExecutionError(
            f"公告事件回填失败 {stats['failed']} 条",
            stats=stats,
        )
    return stats


def register_event_backfill_job() -> None:
    register(
        JobSpec(
            name="backfill_events",
            func=backfill_events,
            # This is an explicit one-time migration job. It remains available
            # through POST /v1/jobs/backfill_events/run but is never scheduled.
            trigger=None,
        )
    )
