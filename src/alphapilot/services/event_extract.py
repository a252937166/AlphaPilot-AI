from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

from jsonschema import ValidationError, validate
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.db.models import Disclosure, DomainEvent
from alphapilot.llm.client import LLMUnavailable, chat_json
from alphapilot.llm.prompts import EVENT_EXTRACT
from alphapilot.services.events import emit
from alphapilot.services.notifications import push_event

EVENT_SUBTYPES = frozenset(
    {
        "earnings",
        "dividend",
        "holder_change",
        "regulation",
        "contract",
        "buyback",
        "personnel",
        "other",
    }
)
EVENT_SUBTYPE_LABELS: dict[str, str] = {
    "earnings": "业绩",
    "dividend": "分红",
    "holder_change": "股东变动",
    "regulation": "监管",
    "contract": "合同",
    "buyback": "回购",
    "personnel": "人事",
    "other": "其他",
}
_LABEL_TO_SUBTYPE = {label: subtype for subtype, label in EVENT_SUBTYPE_LABELS.items()}

EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "event_type",
        "direction",
        "strength",
        "summary",
        "source_quote",
    ],
    "properties": {
        "event_type": {"enum": sorted(EVENT_SUBTYPES)},
        "direction": {"type": "number", "minimum": -1, "maximum": 1},
        "strength": {"type": "number", "minimum": 0, "maximum": 1},
        "horizon_days": {"type": "integer", "minimum": 1, "maximum": 250},
        "summary": {"type": "string", "minLength": 1, "maxLength": 120},
        "source_quote": {"type": "string", "minLength": 1, "maxLength": 200},
    },
    "additionalProperties": False,
}

_POSITIVE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("预增", "earnings"),
    ("中标", "contract"),
    ("回购", "buyback"),
    ("分红", "dividend"),
)
_NEGATIVE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("预减", "earnings"),
    ("问询", "regulation"),
    ("处罚", "regulation"),
    ("减持", "holder_change"),
)
_CHINESE_TEXT = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True, slots=True)
class DisclosureExtraction:
    """Validated disclosure classification detached from database state."""

    subtype: str
    direction: float
    strength: float
    summary: str
    source_quote: str
    source: Literal["llm", "rule"]


def _validated_llm_result(result: dict[str, Any], title: str) -> dict[str, Any] | None:
    try:
        validate(instance=result, schema=EVENT_SCHEMA)
    except ValidationError:
        return None

    source_quote = result["source_quote"]
    summary = result["summary"]
    if (
        not source_quote.strip()
        or source_quote not in title
        or not summary.strip()
        or _CHINESE_TEXT.search(summary) is None
    ):
        return None
    return result


def _keyword_fallback(title: str) -> dict[str, Any]:
    for keyword, event_type in _POSITIVE_KEYWORDS:
        if keyword in title:
            return {
                "event_type": event_type,
                "direction": 0.5,
                "strength": 0.5,
                "summary": f"规则识别“{keyword}”：{title}"[:120],
                "source_quote": keyword,
            }
    for keyword, event_type in _NEGATIVE_KEYWORDS:
        if keyword in title:
            return {
                "event_type": event_type,
                "direction": -0.5,
                "strength": 0.5,
                "summary": f"规则识别“{keyword}”：{title}"[:120],
                "source_quote": keyword,
            }
    return {
        "event_type": "other",
        "direction": 0.0,
        "strength": 0.5,
        "summary": f"规则未识别明确方向：{title}"[:120],
        "source_quote": title[:200],
    }


def classify_disclosure(
    title: str,
    *,
    settings: Settings | None = None,
    session: Session | None = None,
) -> DisclosureExtraction:
    """Classify one title without mutating disclosure or event rows."""
    extraction_source: Literal["llm", "rule"] = "llm"
    try:
        candidate = chat_json(
            "event_extract",
            EVENT_EXTRACT,
            title,
            EVENT_SCHEMA,
            settings=settings,
            session=session,
        )
        result = _validated_llm_result(candidate, title)
    except LLMUnavailable:
        result = None
    if result is None:
        result = _keyword_fallback(title)
        extraction_source = "rule"
    return DisclosureExtraction(
        subtype=str(result["event_type"]),
        direction=float(result["direction"]),
        strength=float(result["strength"]),
        summary=str(result["summary"]),
        source_quote=str(result["source_quote"]),
        source=extraction_source,
    )


def extracted_disclosure_subtype(event: DomainEvent) -> str | None:
    """Return the stored S2 subtype, or None for a legacy/unclassified event."""
    if event.event_type != "disclosure" or not event.summary:
        return None
    subtype, separator, summary = event.summary.partition("｜")
    if not separator or not summary.strip():
        return None
    if subtype in EVENT_SUBTYPES:
        # Compatibility for the pre-commit live backfill. New rows always use
        # the Chinese label so the API never exposes an English marker.
        return subtype
    if subtype in _LABEL_TO_SUBTYPE:
        return _LABEL_TO_SUBTYPE[subtype]
    return None


def persist_disclosure_event(
    session: Session,
    disclosure: Disclosure,
    extraction: DisclosureExtraction,
) -> DomainEvent | None:
    """Idempotently apply a completed classification without network access."""
    if disclosure.id is None:
        session.add(disclosure)
        session.flush()
    if disclosure.id is None:
        return None

    source_ref = f"disclosure:{disclosure.id}"
    subtype_label = EVENT_SUBTYPE_LABELS[extraction.subtype]
    event_summary = f"{subtype_label}｜{extraction.summary}"
    event = session.scalar(select(DomainEvent).where(DomainEvent.source_ref == source_ref).limit(1))
    previous_strength = event.strength if event is not None else None
    created = False
    occurred_at = disclosure.published_at or disclosure.ingested_at or datetime.now(UTC)
    if event is None:
        values: dict[str, Any] = {
            "symbol": disclosure.symbol,
            "event_type": "disclosure",
            "title": disclosure.title,
            "direction": extraction.direction,
            "strength": extraction.strength,
            "summary": event_summary,
            "source_ref": source_ref,
            "occurred_at": occurred_at,
        }
        if session.get_bind().dialect.name == "sqlite":
            # The unique source_ref is the cross-request idempotency boundary.
            # INSERT .. DO NOTHING avoids the select-then-insert race without
            # widening transactions around an LLM network wait.
            statement = (
                sqlite_insert(DomainEvent)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["source_ref"])
            )
            result = cast(CursorResult[Any], session.execute(statement))
            created = result.rowcount == 1
            session.flush()
            event = session.scalar(
                select(DomainEvent).where(DomainEvent.source_ref == source_ref).limit(1)
            )
        else:
            event = emit(session, **values)
    if event is None:
        raise RuntimeError("公告事件原子写入失败")

    event.symbol = disclosure.symbol
    event.event_type = "disclosure"
    event.direction = extraction.direction
    event.strength = extraction.strength
    event.title = disclosure.title
    event.summary = event_summary
    event.occurred_at = occurred_at
    event.__dict__["_extraction_source"] = extraction.source
    session.flush()
    crossed_notification_threshold = (
        previous_strength is not None and previous_strength < 0.6 and event.strength >= 0.6
    )
    if created or crossed_notification_threshold:
        push_event(session, event)
    return event


def extract_disclosure_event(
    session: Session,
    disclosure: Disclosure,
    *,
    settings: Settings | None = None,
) -> DomainEvent | None:
    """Classify and persist one disclosure for non-batch callers.

    Batch workflows use the two phases directly so no database write
    transaction remains open while another title waits on the LLM.
    """
    extraction = classify_disclosure(
        disclosure.title,
        settings=settings,
        session=session,
    )
    return persist_disclosure_event(session, disclosure, extraction)


__all__ = [
    "EVENT_SCHEMA",
    "EVENT_SUBTYPES",
    "EVENT_SUBTYPE_LABELS",
    "DisclosureExtraction",
    "classify_disclosure",
    "extract_disclosure_event",
    "extracted_disclosure_subtype",
    "persist_disclosure_event",
]
