from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from math import floor, isfinite
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.core.timeutil import iso_utc
from alphapilot.data.base import MarketDataProvider
from alphapilot.db.models import (
    AlertRecord,
    DailyReport,
    DomainEvent,
    FactorValue,
    ForecastSnapshot,
    SectorConstituent,
)
from alphapilot.engines.sector_forecast import normalize_constituent_symbol
from alphapilot.llm.client import LLMUnavailable, chat_json
from alphapilot.llm.prompts import REVIEW_ADVICE
from alphapilot.services import market_data
from alphapilot.services.ai_text import compose_market_summary
from alphapilot.services.alert_outcomes import (
    build_signal_attribution,
    list_signal_outcome_rows,
)
from alphapilot.services.disclosures import list_disclosures
from alphapilot.services.watchlist import list_items, tracked_overview

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
_CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
_ARABIC_QUANTITY_RE = re.compile(r"[0-9０-９%％]")
_CHINESE_QUANTITY_RE = re.compile(r"[零〇二三四五六七八九十百千万亿两半]")
_ONE_QUANTITY_RE = re.compile(
    r"一(?:条|个|次|成|点|日|天|周|月|年|倍|手|股|元|块|档|级|位|分)"
    r"|(?:第|百分之|分之|至|为|到|约|仅|共)一(?=[，。；、\s]|$)"
)
_NO_ARABIC_QUANTITY_PATTERN = r"^[^0-9０-９%％]*$"
_ACTION_LABELS = {
    "BUY_CANDIDATE": "买入候选",
    "ADD": "加仓",
    "REDUCE": "减仓",
    "EXIT": "退出",
    "STOP": "止损",
    "WATCH": "观察",
    "HOLD": "持有",
    "REVIEW": "复核",
    "REVIEW_REQUIRED": "需要复核",
}
_VOLATILITY_LABELS = {
    "low": "低波动",
    "mid": "中波动",
    "high": "高波动",
    "insufficient": "三分位样本不足",
    "unavailable": "波动数据缺失",
}
_EVENT_TYPE_STYLE = {
    "disclosure": ("公告", "blue"),
    "capital_anomaly": ("资金异动", "yellow"),
    "market_regime_change": ("市场状态", "purple"),
    "thesis_shift": ("逻辑变化", "red"),
}


@dataclass(frozen=True)
class _OutcomeObservation:
    alert_id: int
    action: str
    sector_key: str
    sector_label: str
    hit: bool | None
    volatility_group: str
    contribution: float | None


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _contains_quantitative_text(value: str) -> bool:
    return any(
        pattern.search(value) is not None
        for pattern in (
            _ARABIC_QUANTITY_RE,
            _CHINESE_QUANTITY_RE,
            _ONE_QUANTITY_RE,
        )
    )


def _quantile(values: list[float], proportion: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * proportion
    lower = floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _report_day_bounds(target_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(target_date, datetime.min.time(), tzinfo=MARKET_TIMEZONE)
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def _sector_memberships(
    session: Session,
    symbols: set[str],
) -> dict[str, tuple[str, str]]:
    """Resolve one conservative current sector membership for each symbol."""

    if not symbols:
        return {}
    provider_symbols = sorted(
        {value for symbol in symbols for value in (symbol, f"SH.{symbol}", f"SZ.{symbol}")}
    )
    rows = session.execute(
        select(
            SectorConstituent.symbol,
            SectorConstituent.plate_code,
            SectorConstituent.plate_name,
        ).where(SectorConstituent.symbol.in_(provider_symbols))
    ).all()
    memberships: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for raw_symbol, raw_code, raw_name in rows:
        symbol = normalize_constituent_symbol(raw_symbol)
        plate_code = str(raw_code or "").strip()
        plate_name = str(raw_name or "").strip()
        if symbol in symbols and plate_code:
            memberships[symbol].add((plate_code, plate_name or plate_code))

    resolved: dict[str, tuple[str, str]] = {}
    for symbol in symbols:
        candidates = memberships.get(symbol, set())
        resolved[symbol] = (
            next(iter(candidates))
            if len(candidates) == 1
            else (
                "unknown",
                "板块未知",
            )
        )
    return resolved


def _volatility_groups(
    session: Session,
    outcome_rows: list[dict[str, Any]],
) -> dict[int, str]:
    """Bucket outcomes by their origin-day full cross-sectional z-score."""

    origin_dates = {date.fromisoformat(str(row["origin_date"])) for row in outcome_rows}
    factor_rows = session.execute(
        select(FactorValue.symbol, FactorValue.trade_date, FactorValue.zscore).where(
            FactorValue.factor == "volatility_20d",
            FactorValue.trade_date.in_(sorted(origin_dates)),
        )
    ).all()
    cross_sections: dict[date, list[float]] = defaultdict(list)
    values_by_key: dict[tuple[str, date], float] = {}
    for raw_symbol, trade_date, raw_zscore in factor_rows:
        zscore = _finite_float(raw_zscore)
        if not isinstance(trade_date, date) or zscore is None:
            continue
        symbol = str(raw_symbol or "").strip()
        cross_sections[trade_date].append(zscore)
        values_by_key[(symbol, trade_date)] = zscore

    thresholds: dict[date, tuple[float, float]] = {}
    for trade_date, values in cross_sections.items():
        if len(values) >= 3 and len(set(values)) >= 3:
            thresholds[trade_date] = (
                _quantile(values, 1 / 3),
                _quantile(values, 2 / 3),
            )

    groups: dict[int, str] = {}
    for row in outcome_rows:
        alert_id = int(row["alert_id"])
        origin_date = date.fromisoformat(str(row["origin_date"]))
        value = values_by_key.get((str(row["symbol"]), origin_date))
        bounds = thresholds.get(origin_date)
        if value is None:
            groups[alert_id] = "unavailable"
        elif bounds is None:
            groups[alert_id] = "insufficient"
        elif value <= bounds[0]:
            groups[alert_id] = "low"
        elif value <= bounds[1]:
            groups[alert_id] = "mid"
        else:
            groups[alert_id] = "high"
    return groups


def _outcome_observations(
    session: Session,
    target_date: date,
) -> list[_OutcomeObservation]:
    rows = list_signal_outcome_rows(session, target_date)
    if not rows:
        return []
    symbols = {str(row["symbol"]) for row in rows}
    sector_by_symbol = _sector_memberships(session, symbols)
    volatility_by_alert = _volatility_groups(session, rows)
    observations: list[_OutcomeObservation] = []
    for row in rows:
        symbol = str(row["symbol"])
        sector_key, sector_label = sector_by_symbol.get(
            symbol,
            ("unknown", "板块未知"),
        )
        hit_value = row["hit"]
        observations.append(
            _OutcomeObservation(
                alert_id=int(row["alert_id"]),
                action=str(row["action"]),
                sector_key=sector_key,
                sector_label=sector_label,
                hit=hit_value if isinstance(hit_value, bool) else None,
                volatility_group=volatility_by_alert[int(row["alert_id"])],
                contribution=_finite_float(row["contribution"]),
            )
        )
    return observations


def _statistics_rows(
    session: Session,
    target_date: date,
) -> list[dict[str, Any]]:
    observations = _outcome_observations(session, target_date)
    if not observations:
        return []
    buckets: dict[tuple[str, str, str], list[_OutcomeObservation]] = defaultdict(list)
    for row in observations:
        buckets[("action", row.action, _ACTION_LABELS.get(row.action, row.action))].append(row)
        buckets[("sector", row.sector_key, row.sector_label)].append(row)
        volatility = row.volatility_group
        buckets[("volatility_tercile", volatility, _VOLATILITY_LABELS[volatility])].append(row)

    dimension_labels = {
        "action": "信号动作",
        "sector": "板块",
        "volatility_tercile": "波动三分位",
    }
    dimension_order = {"action": 0, "sector": 1, "volatility_tercile": 2}
    rows: list[dict[str, Any]] = []
    ordered = sorted(
        buckets.items(),
        key=lambda item: (
            dimension_order[item[0][0]],
            -len(item[1]),
            item[0][2],
            item[0][1],
        ),
    )
    for (dimension, group, group_label), samples in ordered:
        directional = [row for row in samples if isinstance(row.hit, bool)]
        hits = sum(row.hit is True for row in directional)
        hit_rate = round(hits / len(directional), 4) if directional else None
        contribution_total = round(
            sum(row.contribution for row in samples if row.contribution is not None),
            8,
        )
        if hit_rate is None:
            text = (
                f"{dimension_labels[dimension]}“{group_label}”共 {len(samples)} 条归因，"
                "暂无可计算命中率的方向样本。"
            )
        else:
            text = (
                f"{dimension_labels[dimension]}“{group_label}”方向样本 {len(directional)} 条，"
                f"命中 {hits} 条，命中率 {hit_rate:.1%}。"
            )
        rows.append(
            {
                "kind": "statistic",
                "source": "statistics",
                "source_label": "统计",
                "ref": f"{dimension}:{group}",
                "dimension": dimension,
                "dimension_label": dimension_labels[dimension],
                "group": group,
                "group_label": group_label,
                "outcomes": len(samples),
                "directional_evaluated": len(directional),
                "hits": hits,
                "hit_rate": hit_rate,
                "contribution_total": contribution_total,
                "text": text,
            }
        )
    return rows


def _review_schema(refs: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["suggestions"],
        "additionalProperties": False,
        "properties": {
            "suggestions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "required": ["title", "text", "basis_refs"],
                    "additionalProperties": False,
                    "properties": {
                        "title": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 32,
                            "pattern": _NO_ARABIC_QUANTITY_PATTERN,
                        },
                        "text": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 120,
                            "pattern": _NO_ARABIC_QUANTITY_PATTERN,
                        },
                        "basis_refs": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 3,
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": refs},
                        },
                    },
                },
            }
        },
    }


def _empty_improvement_state() -> dict[str, Any]:
    return {
        "source": "statistics",
        "source_label": "统计",
        "suggestions": [],
        "statistics": [],
        "empty_reason": "暂无已完成五日归因的提醒样本，暂不生成策略改进结论。",
        "fallback_reason": None,
        "sector_membership_basis": "current_snapshot",
        "volatility_basis": "origin_date_cross_section_zscore",
    }


def build_improvement_suggestions(
    session: Session,
    settings: Settings,
    target_date: date,
) -> dict[str, Any]:
    """Return evidence-bound LLM suggestions or the exact grouped statistics."""

    statistics = _statistics_rows(session, target_date)
    if not statistics:
        return _empty_improvement_state()
    statistics_fallback = {
        "source": "statistics",
        "source_label": "统计",
        "suggestions": [],
        "statistics": statistics,
        "empty_reason": None,
        "fallback_reason": "LLM 不可用或输出未通过校验，展示可复算统计。",
        "sector_membership_basis": "current_snapshot",
        "volatility_basis": "origin_date_cross_section_zscore",
    }
    if not any(int(row["directional_evaluated"]) > 0 for row in statistics):
        statistics_fallback["fallback_reason"] = "暂无方向性归因样本，展示分组统计且不计算命中率。"
        return statistics_fallback

    refs = [str(row["ref"]) for row in statistics]
    input_payload = {
        "horizon_days": 5,
        "grouping_basis": {
            "sector_membership": "current_snapshot",
            "volatility_tercile": "origin_date_cross_section_zscore",
        },
        "statistics": [
            {
                key: row[key]
                for key in (
                    "ref",
                    "dimension_label",
                    "group_label",
                    "outcomes",
                    "directional_evaluated",
                    "hits",
                    "hit_rate",
                    "contribution_total",
                )
            }
            for row in statistics
        ],
    }
    user = json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))
    try:
        result = chat_json(
            "review_advice",
            REVIEW_ADVICE,
            user,
            _review_schema(refs),
            settings=settings,
            # This report session remains read-only until all LLM calls finish;
            # audit in a short owned transaction so a second Qwen request never
            # waits while the caller holds SQLite's writer lock.
            session=None,
        )
    except LLMUnavailable:
        return statistics_fallback

    suggestions_value = result.get("suggestions")
    if not isinstance(suggestions_value, list) or not 1 <= len(suggestions_value) <= 4:
        return statistics_fallback
    by_ref = {str(row["ref"]): row for row in statistics}
    accepted: list[dict[str, Any]] = []
    seen_text: set[str] = set()
    for value in suggestions_value:
        if not isinstance(value, dict) or set(value) != {"title", "text", "basis_refs"}:
            return statistics_fallback
        title_value = value.get("title")
        text_value = value.get("text")
        basis_value = value.get("basis_refs")
        if (
            not isinstance(title_value, str)
            or not isinstance(text_value, str)
            or not isinstance(basis_value, list)
        ):
            return statistics_fallback
        title = title_value.strip()
        text = text_value.strip()
        basis_refs = [item for item in basis_value if isinstance(item, str)]
        if (
            not title
            or not text
            or len(title) > 32
            or len(text) > 120
            or _CHINESE_RE.search(title) is None
            or _CHINESE_RE.search(text) is None
            or _contains_quantitative_text(f"{title} {text}")
            or text in seen_text
            or len(basis_refs) != len(basis_value)
            or not 1 <= len(basis_refs) <= 3
            or len(set(basis_refs)) != len(basis_refs)
            or any(ref not in by_ref for ref in basis_refs)
        ):
            return statistics_fallback
        seen_text.add(text)
        accepted.append(
            {
                "kind": "suggestion",
                "source": "llm",
                "source_label": "AI 建议",
                "title": title,
                "text": text,
                "basis_refs": basis_refs,
                "basis": [by_ref[ref] for ref in basis_refs],
            }
        )
    return {
        "source": "llm",
        "source_label": "AI 建议",
        "suggestions": accepted,
        "statistics": statistics,
        "empty_reason": None,
        "fallback_reason": None,
        "sector_membership_basis": "current_snapshot",
        "volatility_basis": "origin_date_cross_section_zscore",
    }


def build_event_timeline(
    session: Session,
    target_date: date,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the latest ten events known by the target Shanghai trading day."""

    day_start, day_end = _report_day_bounds(target_date)
    observed_at = now or datetime.now(UTC)
    observed_utc = (
        observed_at.replace(tzinfo=UTC) if observed_at.tzinfo is None else observed_at
    ).astimezone(UTC)
    cutoff = min(day_end, observed_utc + timedelta(microseconds=1))
    rows: list[DomainEvent] = []
    if cutoff > day_start:
        rows = list(
            session.scalars(
                select(DomainEvent)
                .where(
                    DomainEvent.occurred_at >= day_start,
                    DomainEvent.occurred_at < cutoff,
                    DomainEvent.ingested_at < cutoff,
                )
                .order_by(DomainEvent.occurred_at.desc(), DomainEvent.id.desc())
                .limit(10)
            ).all()
        )
    items: list[dict[str, Any]] = []
    for row in rows:
        type_label, type_color = _EVENT_TYPE_STYLE.get(
            row.event_type,
            ("其他", "gray"),
        )
        items.append(
            {
                "id": row.id,
                "symbol": row.symbol,
                "event_type": row.event_type,
                "type_label": type_label,
                "type_color": type_color,
                "title": row.title,
                "summary": row.summary,
                "direction": row.direction,
                "strength": row.strength,
                "occurred_at": iso_utc(row.occurred_at),
                "source_ref": row.source_ref,
            }
        )
    return {
        "items": items,
        "empty_reason": None if items else "当日暂无已入库的重要事件。",
        "timezone": "Asia/Shanghai",
    }


def _forecast_hit_stats(session: Session, symbol_bars: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Score persisted 1d forecasts against realized next-day returns."""
    snapshots = session.scalars(
        select(ForecastSnapshot).order_by(ForecastSnapshot.as_of.desc()).limit(400)
    ).all()
    evaluated = 0
    hits = 0
    samples: list[dict[str, Any]] = []
    for snapshot in snapshots:
        bars = symbol_bars.get(snapshot.symbol)
        if bars is None or bars.empty:
            continue
        horizon = (snapshot.horizons or {}).get("1d") or {}
        p_up = horizon.get("p_up")
        if p_up is None:
            continue
        as_of_date = snapshot.as_of.date()
        dates = [pd.Timestamp(value).date() for value in bars["date"]]
        future_positions = [index for index, value in enumerate(dates) if value > as_of_date]
        if not future_positions:
            continue
        next_index = future_positions[0]
        base_index = next_index - 1
        if base_index < 0:
            continue
        realized = float(bars.iloc[next_index]["close"]) / float(bars.iloc[base_index]["close"]) - 1
        predicted_up = float(p_up) >= 0.5
        hit = (realized >= 0) == predicted_up
        evaluated += 1
        hits += int(hit)
        if len(samples) < 20:
            samples.append(
                {
                    "symbol": snapshot.symbol,
                    "as_of": snapshot.as_of.isoformat(),
                    "p_up_1d": round(float(p_up), 3),
                    "realized_return_1d": round(realized, 4),
                    "hit": hit,
                }
            )
    return {
        "evaluated": evaluated,
        "hits": hits,
        "hit_rate": round(hits / evaluated, 4) if evaluated else None,
        "samples": samples,
    }


def generate_daily_report(
    session: Session,
    settings: Settings,
    provider: MarketDataProvider,
    *,
    report_date: date | None = None,
) -> dict[str, Any]:
    target_date = report_date or date.today()
    indices = market_data.index_quotes(settings)
    watchlist_rows = tracked_overview(session, provider)

    lookback_start = target_date - timedelta(days=30)
    symbol_bars: dict[str, pd.DataFrame] = {}
    for item in list_items(session):
        frame = market_data.load_bars(session, item.symbol, lookback_start, target_date)
        if not frame.empty:
            symbol_bars[item.symbol] = frame
    hit_stats = _forecast_hit_stats(session, symbol_bars)

    gainers = sorted(
        [row for row in watchlist_rows if row.get("change_pct") is not None],
        key=lambda row: float(row["change_pct"]),
        reverse=True,
    )
    day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=MARKET_TIMEZONE)
    day_end = day_start + timedelta(days=1)
    todays_alerts = session.scalars(
        select(AlertRecord)
        .where(
            AlertRecord.created_at >= day_start.astimezone(UTC),
            AlertRecord.created_at < day_end.astimezone(UTC),
        )
        .order_by(AlertRecord.created_at.desc())
        .limit(30)
    ).all()
    disclosures = list_disclosures(session, None, limit=15)
    signal_attribution = build_signal_attribution(session, target_date)
    improvement_suggestions = build_improvement_suggestions(session, settings, target_date)
    event_timeline = build_event_timeline(session, target_date)

    summary_context = {
        "indices": indices,
        "watchlist_top": gainers[:5],
        "hit_stats": {key: hit_stats[key] for key in ("evaluated", "hit_rate")},
        "alert_count": len(todays_alerts),
    }
    ai_summary = compose_market_summary(settings, summary_context, session)

    payload: dict[str, Any] = {
        "report_date": target_date.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "indices": indices,
        "watchlist": watchlist_rows,
        "watchlist_gainers": gainers[:5],
        "watchlist_losers": gainers[-5:][::-1] if gainers else [],
        "forecast_hit_stats": hit_stats,
        "signal_attribution": signal_attribution,
        "improvement_suggestions": improvement_suggestions,
        "event_timeline": event_timeline,
        "alerts": [
            {
                "id": record.id,
                "symbol": record.symbol,
                "action": record.action,
                "urgency": record.urgency,
                "confidence": record.confidence,
                "reasons": record.reasons,
                "created_at": iso_utc(record.created_at),
            }
            for record in todays_alerts
        ],
        "disclosures": disclosures,
        "ai_summary": ai_summary,
        "tomorrow_focus": [
            {
                "symbol": row["symbol"],
                "display_name": row.get("display_name"),
                "reason": f"最新信号 {row.get('alert_action') or 'HOLD'}，"
                f"20日上涨概率 {row.get('p_up_20d')}",
            }
            for row in watchlist_rows
            if row.get("alert_action") in {"BUY_CANDIDATE", "REDUCE", "EXIT", "REVIEW_REQUIRED"}
        ][:6],
        "disclaimer": "本报告由规则引擎自动生成，仅用于工程验证，不构成投资建议。",
    }

    existing = session.get(
        DailyReport, {"report_date": target_date.isoformat(), "kind": "post_market"}
    )
    if existing is None:
        session.add(
            DailyReport(report_date=target_date.isoformat(), kind="post_market", payload=payload)
        )
    else:
        existing.payload = payload
        existing.generated_at = datetime.now(UTC)
    return payload


def get_daily_report(
    session: Session,
    *,
    report_date: date | None = None,
) -> dict[str, Any] | None:
    target = (report_date or date.today()).isoformat()
    row = session.get(DailyReport, {"report_date": target, "kind": "post_market"})
    if row is None:
        return None
    return dict(row.payload)
