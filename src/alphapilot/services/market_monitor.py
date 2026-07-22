from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from itertools import pairwise
from math import isfinite
from typing import Any, Literal, TypedDict
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings, get_settings
from alphapilot.core.timeutil import iso_utc
from alphapilot.db.models import (
    DomainEvent,
    MarketSentiment,
    MarketSnapshotAgg,
    SectorSnapshot,
    WatchlistItem,
)
from alphapilot.llm.client import LLMUnavailable, chat_json
from alphapilot.llm.prompts import MARKET_MONITOR_POLISH

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
MAX_FEED_LIMIT = 100
_CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
_FACT_VALUE_RE = re.compile(r"(?:[A-Z]{2}\.[A-Z0-9]+)|(?:-?\d+(?:\.\d+)?%?)")
_SECTOR_FROM_RE = re.compile(r"榜首由\s*(.+?)（[A-Z]{2}\.")
_SECTOR_TO_RE = re.compile(r"切换为\s*(.+?)（[A-Z]{2}\.")
_CAPITAL_FACT_RE = re.compile(r"自选股\s+\d{6}\s+(?:触发资金异动，|资金异动：)(.+?)。?$")
_DIRECTION_FACTS = (
    "增加",
    "减少",
    "上涨家数占优",
    "下跌家数占优",
    "超过",
    "升至",
)
_SENTIMENT_ORDER = {"冰点": 0, "偏弱": 1, "中性": 2, "偏强": 3, "过热": 4}

FeedLevel = Literal["info", "warn"]


class FeedItem(TypedDict):
    ts: str
    text: str
    level: FeedLevel


@dataclass(frozen=True)
class _Candidate:
    ts: datetime
    sequence: int
    text: str
    level: FeedLevel


def _as_utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(UTC)


def _day_bounds(now: datetime) -> tuple[datetime, datetime]:
    local_day = _as_utc(now).astimezone(MARKET_TIMEZONE).date()
    start = datetime.combine(local_day, time.min, tzinfo=MARKET_TIMEZONE).astimezone(UTC)
    end = datetime.combine(
        local_day + timedelta(days=1),
        time.min,
        tzinfo=MARKET_TIMEZONE,
    ).astimezone(UTC)
    return start, end


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _broken_board_rate(row: MarketSnapshotAgg) -> float | None:
    denominator = row.limit_up + row.broken_boards
    if denominator <= 0:
        return None
    return row.broken_boards / denominator * 100.0


def _top_sector(payload: object) -> tuple[str, str, float | None] | None:
    if not isinstance(payload, list):
        return None
    candidates: list[tuple[float, float, str, int, str, float | None]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, Mapping):
            continue
        code_value = item.get("plate_code")
        name_value = item.get("plate_name")
        if not isinstance(code_value, str) or not code_value.strip():
            continue
        code = code_value.strip()
        name = name_value.strip() if isinstance(name_value, str) and name_value.strip() else code
        rank = _finite_number(item.get("rank"))
        strength = _finite_number(item.get("strength"))
        candidates.append(
            (
                rank if rank is not None else float("inf"),
                -(strength if strength is not None else float("-inf")),
                code,
                index,
                name,
                strength,
            )
        )
    if not candidates:
        return None
    _rank, _strength_sort, code, _index, name, strength = min(candidates)
    return code, name, strength


def _protected_fact_tokens(text: str) -> Counter[str]:
    tokens = Counter(_FACT_VALUE_RE.findall(text))
    tokens.update(label for label in _SENTIMENT_ORDER if label in text)
    tokens.update(word for word in _DIRECTION_FACTS if word in text)
    for pattern in (_SECTOR_FROM_RE, _SECTOR_TO_RE):
        match = pattern.search(text)
        if match is not None:
            tokens.update([match.group(1).strip()])
    capital_match = _CAPITAL_FACT_RE.search(text)
    if capital_match is not None:
        # Capital anomaly titles/summaries may contain no number. Preserve the
        # complete producer fact so optional polishing cannot reverse it while
        # retaining only the stock code.
        tokens.update([capital_match.group(1).strip()])
    return tokens


def _market_candidates(
    session: Session,
    start: datetime,
    end: datetime,
    sequence: int,
) -> tuple[list[_Candidate], int]:
    rows = session.scalars(
        select(MarketSnapshotAgg)
        .where(MarketSnapshotAgg.ts >= start, MarketSnapshotAgg.ts < end)
        .order_by(MarketSnapshotAgg.ts, MarketSnapshotAgg.id)
    ).all()
    items: list[_Candidate] = []
    for previous, current in pairwise(rows):
        previous_amount = _finite_number(previous.total_amount)
        current_amount = _finite_number(current.total_amount)
        if previous_amount is not None and previous_amount > 0 and current_amount is not None:
            delta_pct = (current_amount - previous_amount) / previous_amount * 100.0
            if abs(delta_pct) >= 5.0:
                direction = "增加" if delta_pct > 0 else "减少"
                level: FeedLevel = "info" if delta_pct > 0 else "warn"
                items.append(
                    _Candidate(
                        ts=_as_utc(current.ts),
                        sequence=sequence,
                        text=(
                            f"全市场累计成交额由 {previous_amount / 100_000_000:.2f} 亿元"
                            f"变为 {current_amount / 100_000_000:.2f} 亿元，"
                            f"环比{direction} {abs(delta_pct):.1f}%。"
                        ),
                        level=level,
                    )
                )
                sequence += 1

        previous_balance = previous.advancers - previous.decliners
        current_balance = current.advancers - current.decliners
        if previous_balance < 0 < current_balance or previous_balance > 0 > current_balance:
            advancers_took_lead = current_balance > 0
            items.append(
                _Candidate(
                    ts=_as_utc(current.ts),
                    sequence=sequence,
                    text=(
                        "市场宽度反转，"
                        f"上涨/下跌家数由 {previous.advancers}/{previous.decliners} "
                        f"变为 {current.advancers}/{current.decliners}，"
                        f"当前{'上涨家数占优' if advancers_took_lead else '下跌家数占优'}。"
                    ),
                    level="info" if advancers_took_lead else "warn",
                )
            )
            sequence += 1

        limit_up_delta = current.limit_up - previous.limit_up
        if abs(limit_up_delta) >= 5:
            items.append(
                _Candidate(
                    ts=_as_utc(current.ts),
                    sequence=sequence,
                    text=(
                        f"涨停家数由 {previous.limit_up} 家变为 {current.limit_up} 家，"
                        f"{'增加' if limit_up_delta > 0 else '减少'} {abs(limit_up_delta)} 家。"
                    ),
                    level="info" if limit_up_delta > 0 else "warn",
                )
            )
            sequence += 1

        previous_broken_rate = _broken_board_rate(previous)
        current_broken_rate = _broken_board_rate(current)
        if (
            current_broken_rate is not None
            and current_broken_rate > 30.0
            and (previous_broken_rate is None or previous_broken_rate <= 30.0)
        ):
            items.append(
                _Candidate(
                    ts=_as_utc(current.ts),
                    sequence=sequence,
                    text=(
                        f"炸板率升至 {current_broken_rate:.1f}%："
                        f"涨停 {current.limit_up} 家、炸板 {current.broken_boards} 家，"
                        "超过 30% 预警线。"
                    ),
                    level="warn",
                )
            )
            sequence += 1
    return items, sequence


def _sector_candidates(
    session: Session,
    start: datetime,
    end: datetime,
    sequence: int,
) -> tuple[list[_Candidate], int]:
    rows = session.scalars(
        select(SectorSnapshot)
        .where(SectorSnapshot.as_of >= start, SectorSnapshot.as_of < end)
        .order_by(SectorSnapshot.as_of, SectorSnapshot.id)
    ).all()
    items: list[_Candidate] = []
    for previous, current in pairwise(rows):
        previous_top = _top_sector(previous.payload)
        current_top = _top_sector(current.payload)
        if previous_top is None or current_top is None or previous_top[0] == current_top[0]:
            continue
        strength_text = f"，强度 {current_top[2]:.2f}" if current_top[2] is not None else ""
        items.append(
            _Candidate(
                ts=_as_utc(current.as_of),
                sequence=sequence,
                text=(
                    f"板块强度榜首由 {previous_top[1]}（{previous_top[0]}）"
                    f"切换为 {current_top[1]}（{current_top[0]}{strength_text}）。"
                ),
                level="info",
            )
        )
        sequence += 1
    return items, sequence


def _sentiment_candidates(
    session: Session,
    start: datetime,
    end: datetime,
    sequence: int,
) -> tuple[list[_Candidate], int]:
    rows = session.scalars(
        select(MarketSentiment)
        .where(MarketSentiment.ts >= start, MarketSentiment.ts < end)
        .order_by(MarketSentiment.ts, MarketSentiment.id)
    ).all()
    items: list[_Candidate] = []
    for previous, current in pairwise(rows):
        if previous.label == current.label:
            continue
        previous_rank = _SENTIMENT_ORDER.get(previous.label)
        current_rank = _SENTIMENT_ORDER.get(current.label)
        weakened = (
            previous_rank is not None and current_rank is not None and current_rank < previous_rank
        )
        items.append(
            _Candidate(
                ts=_as_utc(current.ts),
                sequence=sequence,
                text=(
                    f"市场情绪由“{previous.label}”换档为“{current.label}”，"
                    f"情绪分由 {previous.score:.1f} 变为 {current.score:.1f}。"
                ),
                level="warn" if weakened else "info",
            )
        )
        sequence += 1
    return items, sequence


def _event_candidates(
    session: Session,
    start: datetime,
    end: datetime,
    sequence: int,
) -> tuple[list[_Candidate], int]:
    watchlist = set(session.scalars(select(WatchlistItem.symbol)).all())
    if not watchlist:
        return [], sequence
    rows = session.scalars(
        select(DomainEvent)
        .where(
            DomainEvent.event_type == "capital_anomaly",
            DomainEvent.symbol.in_(watchlist),
            DomainEvent.occurred_at >= start,
            DomainEvent.occurred_at < end,
        )
        .order_by(DomainEvent.occurred_at, DomainEvent.id)
    ).all()
    items: list[_Candidate] = []
    for row in rows:
        detail = row.summary.strip() if isinstance(row.summary, str) and row.summary.strip() else ""
        title = row.title.strip()
        fact = f"{title}：{detail}" if detail else title
        items.append(
            _Candidate(
                ts=_as_utc(row.occurred_at),
                sequence=sequence,
                text=f"自选股 {row.symbol} 触发资金异动，{fact}。",
                level="warn",
            )
        )
        sequence += 1
    return items, sequence


def _polish_schema(item_count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["items"],
        "additionalProperties": False,
        "properties": {
            "items": {
                "type": "array",
                "minItems": item_count,
                "maxItems": item_count,
                "items": {
                    "type": "object",
                    "required": ["index", "text"],
                    "additionalProperties": False,
                    "properties": {
                        "index": {"type": "integer", "minimum": 0, "maximum": item_count - 1},
                        "text": {"type": "string", "minLength": 1, "maxLength": 160},
                    },
                },
            }
        },
    }


def _polish_feed(
    items: list[FeedItem],
    *,
    settings: Settings,
    session: Session,
) -> list[FeedItem]:
    if not items or not settings.llm_polish_feed:
        return items
    payload = {
        "items": [{"index": index, "text": item["text"]} for index, item in enumerate(items)]
    }
    try:
        result = chat_json(
            "market_feed_polish",
            MARKET_MONITOR_POLISH,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            _polish_schema(len(items)),
            settings=settings,
            session=session,
        )
    except LLMUnavailable:
        return items
    polished_value = result.get("items")
    if not isinstance(polished_value, list) or len(polished_value) != len(items):
        return items
    polished_by_index: dict[int, str] = {}
    for value in polished_value:
        if not isinstance(value, Mapping):
            return items
        index = value.get("index")
        text_value = value.get("text")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(items)
            or index in polished_by_index
            or not isinstance(text_value, str)
        ):
            return items
        polished_text = text_value.strip()
        if (
            not polished_text
            or _CHINESE_RE.search(polished_text) is None
            or _protected_fact_tokens(polished_text) != _protected_fact_tokens(items[index]["text"])
        ):
            return items
        polished_by_index[index] = polished_text
    if set(polished_by_index) != set(range(len(items))):
        return items
    return [
        {"ts": item["ts"], "text": polished_by_index[index], "level": item["level"]}
        for index, item in enumerate(items)
    ]


def build_feed(
    session: Session,
    limit: int = 20,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Build a deterministic current-day market monitor feed from stored facts."""

    if not 1 <= limit <= MAX_FEED_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_FEED_LIMIT}")
    observed_at = _as_utc(now or datetime.now(UTC))
    start, day_end = _day_bounds(observed_at)
    end = min(day_end, observed_at + timedelta(microseconds=1))
    sequence = 0
    candidates: list[_Candidate] = []
    for builder in (
        _market_candidates,
        _sector_candidates,
        _sentiment_candidates,
        _event_candidates,
    ):
        built, sequence = builder(session, start, end, sequence)
        candidates.extend(built)
    candidates.sort(key=lambda item: (item.ts, item.sequence), reverse=True)
    items: list[FeedItem] = [
        {
            "ts": iso_utc(item.ts) or "",
            "text": item.text,
            "level": item.level,
        }
        for item in candidates[:limit]
    ]
    resolved_settings = settings or get_settings()
    polished = _polish_feed(items, settings=resolved_settings, session=session)
    return [dict(item) for item in polished]


__all__ = ["build_feed"]
