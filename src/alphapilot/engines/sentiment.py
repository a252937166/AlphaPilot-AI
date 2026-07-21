from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from math import isfinite, sqrt
from threading import Lock
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.core.timeutil import iso_utc
from alphapilot.db.models import DailyBar, MarketSnapshotAgg

MODEL_VERSION = "sentiment-v1.0.0"
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
HISTORY_DAYS = 250
HISTORY_CALENDAR_DAYS = 550
CACHE_TTL_SECONDS = 300.0
CLOSE_START = time(15, 0)
CLOSE_END = time(15, 6)
MARKET_OPEN = time(9, 25)
SAME_TIME_TOLERANCE_SECONDS = 90.0
INDEX_SYMBOL = "SH.000001"
WEIGHTS: Mapping[str, float] = {
    "breadth": 0.30,
    "limitup": 0.25,
    "volume": 0.25,
    "volatility": 0.20,
}


@dataclass(frozen=True, slots=True)
class SnapshotObservation:
    snapshot_id: int
    ts: datetime
    trade_date: date
    seconds: float
    breadth: float | None
    limit_ecology: float
    total_amount: float | None


@dataclass(frozen=True, slots=True)
class IndexClose:
    row_id: int
    trade_date: date
    close: float


@dataclass(frozen=True, slots=True)
class HistoricalInputs:
    snapshots: tuple[SnapshotObservation, ...]
    index_closes: tuple[IndexClose, ...]


@dataclass(frozen=True, slots=True)
class CacheEntry:
    expires_at: float
    inputs: HistoricalInputs


_CACHE: dict[tuple[int, date, date, int, int, int], CacheEntry] = {}
_CACHE_LOCK = Lock()


def clear_sentiment_cache() -> None:
    """Clear the historical-input cache; tests use this for DB isolation."""

    with _CACHE_LOCK:
        _CACHE.clear()


def _as_utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(UTC)


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _seconds(local: datetime) -> float:
    return local.hour * 3600 + local.minute * 60 + local.second + local.microsecond / 1_000_000


def _snapshot_observation(row: MarketSnapshotAgg) -> SnapshotObservation:
    if row.id is None:
        raise ValueError("市场快照尚未持久化，无法生成可追溯情绪记录。")
    local = _as_utc(row.ts).astimezone(MARKET_TIMEZONE)
    denominator = row.advancers + row.decliners
    breadth = row.advancers / denominator if denominator > 0 else None
    amount = _finite_float(row.total_amount)
    return SnapshotObservation(
        snapshot_id=int(row.id),
        ts=_as_utc(row.ts),
        trade_date=local.date(),
        seconds=_seconds(local),
        breadth=breadth,
        limit_ecology=float(row.limit_up - row.broken_boards),
        total_amount=amount if amount is not None and amount > 0 else None,
    )


def _snapshot_history(session: Session, current_day: date) -> tuple[SnapshotObservation, ...]:
    local_start = datetime.combine(current_day, time.min, tzinfo=MARKET_TIMEZONE)
    end_utc = local_start.astimezone(UTC)
    start_utc = (local_start - timedelta(days=HISTORY_CALENDAR_DAYS)).astimezone(UTC)
    rows = session.scalars(
        select(MarketSnapshotAgg)
        .where(
            MarketSnapshotAgg.ts >= start_utc,
            MarketSnapshotAgg.ts < end_utc,
        )
        .order_by(MarketSnapshotAgg.ts, MarketSnapshotAgg.id)
    ).all()
    return tuple(_snapshot_observation(row) for row in rows)


def _index_cutoff(local_as_of: datetime) -> date:
    if local_as_of.time() < CLOSE_END:
        return local_as_of.date() - timedelta(days=1)
    return local_as_of.date()


def _complete_index_closes(
    session: Session,
    *,
    as_of: datetime,
    cutoff: date,
) -> tuple[IndexClose, ...]:
    rows = session.execute(
        select(
            DailyBar.id,
            DailyBar.trade_date,
            DailyBar.close,
            DailyBar.ingested_at,
        )
        .where(
            DailyBar.symbol == INDEX_SYMBOL,
            DailyBar.trade_date <= cutoff,
            DailyBar.ingested_at <= as_of,
        )
        .order_by(DailyBar.trade_date.desc(), DailyBar.id.desc())
        .limit(HISTORY_DAYS + 40)
    ).all()
    complete: list[IndexClose] = []
    for row_id, trade_date, close_value, ingested_at in rows:
        close = _finite_float(close_value)
        if (
            not isinstance(trade_date, date)
            or not isinstance(ingested_at, datetime)
            or close is None
            or close <= 0
        ):
            continue
        ingested_local = _as_utc(ingested_at).astimezone(MARKET_TIMEZONE)
        complete_same_day = (
            ingested_local.date() == trade_date and ingested_local.time() >= CLOSE_END
        )
        if not (trade_date < ingested_local.date() or complete_same_day):
            continue
        complete.append(IndexClose(int(row_id), trade_date, close))
    complete.sort(key=lambda item: (item.trade_date, item.row_id))
    return tuple(complete)


def _historical_inputs(session: Session, as_of: datetime) -> HistoricalInputs:
    utc_as_of = _as_utc(as_of)
    local_as_of = utc_as_of.astimezone(MARKET_TIMEZONE)
    cutoff = _index_cutoff(local_as_of)
    bind = session.get_bind()
    wall_clock_bucket = int(utc_as_of.timestamp() // CACHE_TTL_SECONDS)
    local_start = datetime.combine(local_as_of.date(), time.min, tzinfo=MARKET_TIMEZONE).astimezone(
        UTC
    )
    snapshot_signature = int(
        session.scalar(
            select(func.max(MarketSnapshotAgg.id)).where(MarketSnapshotAgg.ts < local_start)
        )
        or 0
    )
    index_signature = int(
        session.scalar(
            select(func.max(DailyBar.id)).where(
                DailyBar.symbol == INDEX_SYMBOL,
                DailyBar.trade_date <= cutoff,
                DailyBar.ingested_at <= utc_as_of,
            )
        )
        or 0
    )
    key = (
        id(bind),
        local_as_of.date(),
        cutoff,
        wall_clock_bucket,
        snapshot_signature,
        index_signature,
    )
    now = monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None and cached.expires_at > now:
            return cached.inputs

    inputs = HistoricalInputs(
        snapshots=_snapshot_history(session, local_as_of.date()),
        index_closes=_complete_index_closes(session, as_of=utc_as_of, cutoff=cutoff),
    )
    with _CACHE_LOCK:
        expired = [cache_key for cache_key, entry in _CACHE.items() if entry.expires_at <= now]
        for cache_key in expired:
            _CACHE.pop(cache_key, None)
        _CACHE[key] = CacheEntry(expires_at=now + CACHE_TTL_SECONDS, inputs=inputs)
    return inputs


def _is_close_observation(item: SnapshotObservation) -> bool:
    local = item.ts.astimezone(MARKET_TIMEZONE)
    return CLOSE_START <= local.time() < CLOSE_END


def _daily_close_rows(
    rows: Sequence[SnapshotObservation],
    calendar_dates: set[date],
) -> list[SnapshotObservation]:
    selected: dict[date, SnapshotObservation] = {}
    for item in rows:
        if not _is_close_observation(item):
            continue
        if calendar_dates and item.trade_date not in calendar_dates:
            continue
        existing = selected.get(item.trade_date)
        if existing is None or (item.ts, item.snapshot_id) > (
            existing.ts,
            existing.snapshot_id,
        ):
            selected[item.trade_date] = item
    return [selected[day] for day in sorted(selected)][-(HISTORY_DAYS + 1) :]


def _same_time_rows(
    rows: Sequence[SnapshotObservation],
    calendar_dates: set[date],
    target_seconds: float,
) -> list[SnapshotObservation]:
    selected: dict[date, SnapshotObservation] = {}
    distances: dict[date, float] = {}
    for item in rows:
        if calendar_dates and item.trade_date not in calendar_dates:
            continue
        distance = abs(item.seconds - target_seconds)
        if distance > SAME_TIME_TOLERANCE_SECONDS:
            continue
        previous_distance = distances.get(item.trade_date)
        existing = selected.get(item.trade_date)
        if (
            previous_distance is None
            or distance < previous_distance
            or (
                distance == previous_distance
                and existing is not None
                and (item.ts, item.snapshot_id) > (existing.ts, existing.snapshot_id)
            )
        ):
            selected[item.trade_date] = item
            distances[item.trade_date] = distance
    return [selected[day] for day in sorted(selected)][-(HISTORY_DAYS + 1) :]


def _percentile_rank(value: float, values: Sequence[float]) -> float:
    finite_values = [float(item) for item in values if isfinite(float(item))]
    if not isfinite(value) or not finite_values:
        raise ValueError("分位计算缺少有限样本。")
    if len(finite_values) == 1 or max(finite_values) == min(finite_values):
        return 50.0
    less = sum(item < value for item in finite_values)
    equal = sum(item == value for item in finite_values)
    if equal == 0:
        raise ValueError("当前值必须包含在分位样本中。")
    average_rank = less + 1.0 + (equal - 1.0) / 2.0
    return max(0.0, min(100.0, (average_rank - 1.0) / (len(finite_values) - 1.0) * 100.0))


def _component(
    *,
    raw: float | None,
    samples: Sequence[float],
    missing_reason: str,
    insufficient_reason: str,
    extras: Mapping[str, Any] | None = None,
) -> tuple[float, dict[str, Any]]:
    sample_values = [float(item) for item in samples if isfinite(float(item))]
    if raw is None:
        score = 50.0
        available = False
        degraded = True
        reason: str | None = missing_reason
    else:
        score = _percentile_rank(raw, sample_values)
        available = True
        degraded = len(sample_values) < 2
        reason = insufficient_reason if degraded else None
    details: dict[str, Any] = {
        "raw": raw,
        "percentile": score,
        "sample_size": len(sample_values),
        "historical_samples": max(0, len(sample_values) - (1 if raw is not None else 0)),
        "available": available,
        "degraded": degraded,
        "reason": reason,
    }
    if extras:
        details.update(extras)
    return score, details


def _rolling_volatility(closes: Sequence[IndexClose]) -> list[tuple[date, float]]:
    if len(closes) < 21:
        return []
    returns = [
        closes[index].close / closes[index - 1].close - 1.0 for index in range(1, len(closes))
    ]
    result: list[tuple[date, float]] = []
    for end in range(19, len(returns)):
        window = returns[end - 19 : end + 1]
        mean = sum(window) / len(window)
        variance = sum((item - mean) ** 2 for item in window) / len(window)
        volatility = sqrt(variance) * sqrt(252.0)
        if isfinite(volatility):
            result.append((closes[end + 1].trade_date, volatility))
    return result[-HISTORY_DAYS:]


def _adjacent_trading_days(
    previous: date,
    current: date,
    calendar: Sequence[date],
    current_day: date,
) -> bool:
    positions = {trade_date: index for index, trade_date in enumerate(calendar)}
    if previous in positions and current in positions:
        return positions[current] == positions[previous] + 1
    return bool(
        current == current_day
        and current not in positions
        and calendar
        and previous == calendar[-1]
    )


def _volume_component(
    current: SnapshotObservation,
    history: HistoricalInputs,
) -> tuple[float, dict[str, Any]]:
    calendar = [item.trade_date for item in history.index_closes]
    calendar_dates = set(calendar)
    local = current.ts.astimezone(MARKET_TIMEZONE)
    if CLOSE_START <= local.time() < CLOSE_END:
        mode = "close_to_close"
        representatives = _daily_close_rows(history.snapshots, calendar_dates)
    elif MARKET_OPEN <= local.time() < CLOSE_START:
        mode = "same_time_prior_day"
        representatives = _same_time_rows(history.snapshots, calendar_dates, current.seconds)
    else:
        mode = "off_market_unavailable"
        representatives = []

    ordered = [*representatives, current]
    changes: list[tuple[date, float]] = []
    prior: SnapshotObservation | None = None
    for previous, item in pairwise(ordered):
        if (
            previous.total_amount is None
            or item.total_amount is None
            or not _adjacent_trading_days(
                previous.trade_date,
                item.trade_date,
                calendar,
                current.trade_date,
            )
        ):
            continue
        change = item.total_amount / previous.total_amount - 1.0
        if isfinite(change):
            changes.append((item.trade_date, change))
            if item.trade_date == current.trade_date:
                prior = previous

    current_change = next(
        (value for trade_date, value in reversed(changes) if trade_date == current.trade_date),
        None,
    )
    sample = [value for _, value in changes[-HISTORY_DAYS:]]
    score, details = _component(
        raw=current_change,
        samples=sample,
        missing_reason=(
            "当前为强制非交易时段快照，累计成交额不参与日环比。"
            if mode == "off_market_unavailable"
            else "缺少上一交易日同一时刻或收盘成交额，量能子分按中性 50。"
        ),
        insufficient_reason="量能环比历史不足，单样本分位按中性 50。",
        extras={
            "mode": mode,
            "current_amount": current.total_amount,
            "prior_amount": prior.total_amount if prior is not None else None,
            "prior_trade_date": prior.trade_date.isoformat() if prior is not None else None,
        },
    )
    return score, details


def sentiment_label(score: float) -> str:
    if score < 30.0:
        return "冰点"
    if score < 45.0:
        return "偏弱"
    if score < 60.0:
        return "中性"
    if score <= 75.0:
        return "偏强"
    return "过热"


def money_effect_label(score: float) -> str:
    labels = {
        "冰点": "赚钱效应低迷",
        "偏弱": "赚钱效应偏弱",
        "中性": "赚钱效应中性",
        "偏强": "赚钱效应较强",
        "过热": "赚钱效应活跃",
    }
    return labels[sentiment_label(score)]


def liquidity_label(score: float) -> str:
    labels = {
        "冰点": "资金面明显缩量",
        "偏弱": "资金面偏弱",
        "中性": "资金面平稳",
        "偏强": "资金面偏强",
        "过热": "资金面显著放量",
    }
    return labels[sentiment_label(score)]


def compute(
    session: Session,
    current: MarketSnapshotAgg | None = None,
    *,
    source_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute a fixed-weight, auditable 0-100 market sentiment snapshot."""

    if current is None:
        current = session.scalars(
            select(MarketSnapshotAgg)
            .order_by(MarketSnapshotAgg.ts.desc(), MarketSnapshotAgg.id.desc())
            .limit(1)
        ).first()
    if current is None:
        raise ValueError("暂无全市场快照，无法计算市场情绪。")
    if not isfinite(sum(WEIGHTS.values())) or abs(sum(WEIGHTS.values()) - 1.0) > 1e-12:
        raise ValueError("市场情绪固定权重之和必须为 1。")

    observation = _snapshot_observation(current)
    history = _historical_inputs(session, observation.ts)
    calendar_dates = {item.trade_date for item in history.index_closes}
    close_rows = _daily_close_rows(history.snapshots, calendar_dates)

    breadth_sample = [item.breadth for item in close_rows if item.breadth is not None]
    if observation.breadth is not None:
        breadth_sample.append(observation.breadth)
    breadth_sub, breadth_details = _component(
        raw=observation.breadth,
        samples=breadth_sample[-HISTORY_DAYS:],
        missing_reason="上涨与下跌家数之和为 0，宽度子分按中性 50。",
        insufficient_reason="缺少历史收盘宽度样本，单样本分位按中性 50。",
        extras={
            "advancers": current.advancers,
            "decliners": current.decliners,
        },
    )

    limit_sample = [item.limit_ecology for item in close_rows]
    limit_sample.append(observation.limit_ecology)
    limitup_sub, limitup_details = _component(
        raw=observation.limit_ecology,
        samples=limit_sample[-HISTORY_DAYS:],
        missing_reason="涨停生态输入不可用，子分按中性 50。",
        insufficient_reason="缺少历史收盘涨停生态样本，单样本分位按中性 50。",
        extras={
            "limit_up": current.limit_up,
            "broken_boards": current.broken_boards,
        },
    )

    volume_sub, volume_details = _volume_component(observation, history)

    volatility_series = _rolling_volatility(history.index_closes)
    volatility_raw = volatility_series[-1][1] if volatility_series else None
    volatility_sample = [value for _, value in volatility_series]
    volatility_percentile, volatility_details = _component(
        raw=volatility_raw,
        samples=volatility_sample,
        missing_reason="上证指数完整收盘价不足 21 根，波动子分按中性 50。",
        insufficient_reason="上证指数滚动波动历史不足，单样本分位按中性 50。",
        extras={
            "index_symbol": INDEX_SYMBOL,
            "latest_complete_trade_date": (
                history.index_closes[-1].trade_date.isoformat() if history.index_closes else None
            ),
            "annualized_ddof": 0,
        },
    )
    volatility_sub = 100.0 - volatility_percentile
    volatility_details["risk_percentile"] = volatility_percentile
    volatility_details["percentile"] = volatility_sub

    subs = {
        "breadth": breadth_sub,
        "limitup": limitup_sub,
        "volume": volume_sub,
        "volatility": volatility_sub,
    }
    score = sum(subs[name] * WEIGHTS[name] for name in WEIGHTS)
    numeric = [score, *subs.values()]
    if not all(isfinite(value) and 0.0 <= value <= 100.0 for value in numeric):
        raise ValueError("市场情绪计算产生了非有限值或越界结果。")

    components = {
        "breadth": breadth_details,
        "limitup": limitup_details,
        "volume": volume_details,
        "volatility": volatility_details,
    }
    degraded_components = [
        name for name, details in components.items() if bool(details["degraded"])
    ]
    missing_inputs = [
        name for name, details in components.items() if not bool(details["available"])
    ]
    details: dict[str, Any] = {
        "weights": dict(WEIGHTS),
        "components": components,
        "history_close_days": len(close_rows),
        "degraded_components": degraded_components,
        "missing_inputs": missing_inputs,
        "degraded": bool(degraded_components),
        "degradation_reason": (
            "部分子项缺少真实历史基线，固定槽按中性 50 计入且未重分配权重。"
            if degraded_components
            else None
        ),
        "source": {
            "snapshot_id": observation.snapshot_id,
            "snapshot_source": current.source,
            **dict(source_context or {}),
        },
    }
    return {
        "source_snapshot_id": observation.snapshot_id,
        "as_of": iso_utc(observation.ts),
        "score": score,
        "label": sentiment_label(score),
        "subs": subs,
        "money_effect": money_effect_label(limitup_sub),
        "liquidity": liquidity_label(volume_sub),
        "model_version": MODEL_VERSION,
        "details": details,
    }
