from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.core.timeutil import iso_utc
from alphapilot.db.models import (
    DailyBar,
    DomainEvent,
    ForecastSnapshot,
    SectorConstituent,
    SectorForecast,
    Security,
    StockInsight,
)
from alphapilot.engines.sector_forecast import normalize_constituent_symbol
from alphapilot.engines.stock_score import DIMENSION_LABELS, DIMENSION_ORDER
from alphapilot.llm.client import LLMUnavailable, chat_json
from alphapilot.llm.prompts import STOCK_INSIGHT
from alphapilot.services import stock_scores
from alphapilot.services.watchlist import normalize_symbol

MODEL_VERSION = "stock-insight-v1.0.1"
CACHE_TTL = timedelta(hours=24)
TAGS = frozenset({"利多", "利空", "中性"})
_CHINESE = re.compile(r"[\u3400-\u9fff]")
_SOURCE_REF_PATTERN = (
    r"^(event:[1-9][0-9]*|"
    r"score:(tech|capital|fundamental|valuation|sentiment)|"
    r"sector:[A-Z]{2}\.[A-Z0-9]+|"
    r"forecast:[1-9][0-9]*|profile:[0-9]{6})$"
)
_BUILD_LOCKS = tuple(Lock() for _ in range(64))

INSIGHT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["core_view", "drivers"],
    "additionalProperties": False,
    "properties": {
        "core_view": {"type": "string", "minLength": 1, "maxLength": 120},
        "drivers": {
            "type": "array",
            "minItems": 3,
            "maxItems": 6,
            "uniqueItems": True,
            "items": {
                "type": "object",
                "required": ["text", "tag", "source_ref"],
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string", "minLength": 1, "maxLength": 40},
                    "tag": {"type": "string", "enum": sorted(TAGS)},
                    "source_ref": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 128,
                        "pattern": _SOURCE_REF_PATTERN,
                    },
                },
            },
        },
    },
}


def _build_lock(symbol: str) -> Lock:
    # A fixed stripe pool bounds memory even if an HTTP scanner probes many
    # syntactically valid symbols. Collisions only serialize independent builds.
    return _BUILD_LOCKS[int(symbol) % len(_BUILD_LOCKS)]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _finite(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: object, limit: int) -> str:
    normalized = " ".join(str(value or "").split())
    return normalized[:limit]


def _contains_chinese(value: str) -> bool:
    return _CHINESE.search(value) is not None


def _tag(value: float | None, *, positive: float, negative: float) -> str:
    if value is None:
        return "中性"
    if value >= positive:
        return "利多"
    if value <= negative:
        return "利空"
    return "中性"


def _driver(text: str, tag: str, source_ref: str) -> dict[str, str]:
    return {"text": _text(text, 40), "tag": tag, "source_ref": source_ref}


def _latest_events(session: Session, symbol: str) -> list[DomainEvent]:
    return list(
        session.scalars(
            select(DomainEvent)
            .where(DomainEvent.symbol == symbol)
            .order_by(DomainEvent.occurred_at.desc(), DomainEvent.id.desc())
            .limit(5)
        ).all()
    )


def _latest_forecast(session: Session, symbol: str) -> ForecastSnapshot | None:
    return session.scalars(
        select(ForecastSnapshot)
        .where(ForecastSnapshot.symbol == symbol)
        .order_by(
            ForecastSnapshot.as_of.desc(),
            ForecastSnapshot.created_at.desc(),
            ForecastSnapshot.id.desc(),
        )
        .limit(1)
    ).first()


def _sector_context(session: Session, symbol: str) -> dict[str, Any] | None:
    provider_symbols = [symbol, f"SH.{symbol}", f"SZ.{symbol}"]
    members = session.scalars(
        select(SectorConstituent)
        .where(SectorConstituent.symbol.in_(provider_symbols))
        .order_by(SectorConstituent.plate_code)
    ).all()
    plate_codes = sorted(
        {
            member.plate_code
            for member in members
            if normalize_constituent_symbol(member.symbol) == symbol
        }
    )
    if not plate_codes:
        return None

    latest_date = session.scalar(
        select(func.max(SectorForecast.trade_date)).where(
            SectorForecast.plate_code.in_(plate_codes)
        )
    )
    if latest_date is None:
        return None
    latest_benchmark = session.scalar(
        select(func.max(DailyBar.trade_date)).where(DailyBar.symbol == "SH.000001")
    )
    if latest_benchmark is not None and latest_benchmark != latest_date:
        return None
    rows = session.scalars(
        select(SectorForecast)
        .where(
            SectorForecast.plate_code.in_(plate_codes),
            SectorForecast.trade_date == latest_date,
        )
        .order_by(SectorForecast.plate_code, SectorForecast.horizon, SectorForecast.id.desc())
    ).all()
    by_plate: dict[str, list[SectorForecast]] = {}
    for row in rows:
        by_plate.setdefault(row.plate_code, []).append(row)
    if not by_plate:
        return None

    # Industry memberships should be singular. A stable code tie-break avoids
    # opportunistically selecting whichever overlapping plate scores highest.
    plate_code = sorted(by_plate)[0]
    forecasts = sorted(by_plate[plate_code], key=lambda row: (row.horizon, -row.id))
    anchor = next((row for row in forecasts if row.horizon == 20), forecasts[-1])
    flow_mode = "no-flow" if anchor.model_version.endswith("-no-flow") else "with-flow"
    return {
        "source_ref": f"sector:{plate_code}",
        "plate_code": plate_code,
        "plate_name": anchor.plate_name,
        "trade_date": anchor.trade_date.isoformat(),
        "lifecycle": anchor.lifecycle,
        "model_version": anchor.model_version,
        "flow_mode": flow_mode,
        "backtest_scope": "fixed-current-membership",
        "limitations": (
            ["当前模型未使用资金流特征"] if flow_mode == "no-flow" else []
        )
        + ["回测使用固定当前成分，不代表时点成分宇宙"],
        "forecasts": [
            {
                "horizon": row.horizon,
                "score": _finite(row.score),
                "expected_excess": _finite(row.expected_excess),
                "win_rate": _finite(row.win_rate),
                "lifecycle": row.lifecycle,
                "rsi14": _finite(row.rsi14),
                "reversal_score": _finite(row.reversal_score),
            }
            for row in forecasts
        ],
    }


def _forecast_horizons(row: ForecastSnapshot) -> dict[str, Any]:
    fields = (
        "horizon_days",
        "p_up",
        "expected_return",
        "q10",
        "q50",
        "q90",
        "confidence",
    )
    result: dict[str, Any] = {}
    for raw_key, raw_payload in row.horizons.items():
        key = _text(raw_key, 16)
        if not key or not isinstance(raw_payload, dict):
            continue
        payload: dict[str, Any] = {}
        for field in fields:
            if field not in raw_payload:
                continue
            value = raw_payload[field]
            if field == "horizon_days":
                number = _finite(value)
                payload[field] = int(number) if number is not None else None
            else:
                payload[field] = _finite(value)
        result[key] = payload
    return result


def _profile_context(row: Security) -> dict[str, Any]:
    raw = row.profile if isinstance(row.profile, dict) else {}
    return {
        "source_ref": f"profile:{row.symbol}",
        "symbol": row.symbol,
        "name": _text(row.name, 64) or None,
        "org_name": _text(raw.get("ORGNAME"), 96) or None,
        "industry": _text(row.industry_csrc or row.industry, 64) or None,
        "board": _text(row.board, 32) or None,
        "listed_date": _text(row.listed_date, 16) or None,
        "status": _text(row.status or row.list_status, 32) or None,
        "isin": _text(raw.get("F013V"), 24) or None,
        "market_cap": _finite(row.market_cap),
        "pe_ttm": _finite(row.pe_ttm),
        "pb": _finite(row.pb),
        "style_tag": _text(row.style_tag, 16) or None,
        "updated_at": iso_utc(row.updated_at),
    }


def _build_context(session: Session, symbol: str, as_of: datetime) -> tuple[dict[str, Any], bool]:
    score = stock_scores.latest_score(session, symbol)
    score_details = stock_scores.score_payload(session, score) if score is not None else None
    events = _latest_events(session, symbol)
    sector = _sector_context(session, symbol)
    forecast = _latest_forecast(session, symbol)
    security = session.get(Security, symbol)

    radar_by_key = {
        str(item.get("key")): item
        for item in (score_details.get("radar", []) if score_details is not None else [])
        if isinstance(item, dict)
    }
    dimensions: list[dict[str, Any]] = []
    for dimension in DIMENSION_ORDER:
        radar = radar_by_key.get(dimension, {})
        value = _finite(getattr(score, dimension)) if score is not None else None
        available_inputs = int(radar.get("available_inputs") or 0)
        required_inputs = int(radar.get("required_inputs") or 0)
        dimensions.append(
            {
                "source_ref": f"score:{dimension}",
                "key": dimension,
                "name": DIMENSION_LABELS[dimension],
                "value": value,
                # A persisted neutral-filled score is not itself proof that a
                # factor input existed. Keep it visible for rule disclosure,
                # but do not let the LLM cite a wholly unavailable dimension.
                "available": value is not None and available_inputs > 0,
                "available_inputs": available_inputs,
                "required_inputs": required_inputs,
                "degraded": bool(radar.get("degraded", score is not None)),
            }
        )
    event_payload = [
        {
            "source_ref": f"event:{event.id}",
            "event_type": event.event_type,
            "direction": _finite(event.direction),
            "strength": _finite(event.strength),
            "title": _text(event.title, 160),
            "summary": _text(event.summary, 200) or None,
            "occurred_at": iso_utc(event.occurred_at),
            "ingested_at": iso_utc(event.ingested_at),
        }
        for event in events
    ]
    forecast_payload = (
        {
            "source_ref": f"forecast:{forecast.id}",
            "as_of": iso_utc(forecast.as_of),
            "provider": forecast.provider,
            "model_version": forecast.model_version,
            "horizons": _forecast_horizons(forecast),
        }
        if forecast is not None
        else None
    )
    profile = _profile_context(security) if security is not None else None

    allowed_refs = [
        item["source_ref"]
        for item in dimensions
        if item["available"] and _finite(item.get("value")) is not None
    ]
    allowed_refs.extend(item["source_ref"] for item in event_payload)
    if sector is not None:
        allowed_refs.append(str(sector["source_ref"]))
    if forecast_payload is not None:
        allowed_refs.append(str(forecast_payload["source_ref"]))
    if profile is not None:
        allowed_refs.append(str(profile["source_ref"]))

    context = {
        "symbol": symbol,
        "as_of": iso_utc(as_of),
        "allowed_source_refs": allowed_refs,
        "score": {
            "trade_date": score.trade_date.isoformat() if score is not None else None,
            "composite": _finite(score.composite) if score is not None else None,
            "model_version": score.model_version if score is not None else None,
            "degraded": bool(score_details.get("degraded")) if score_details else False,
            "degraded_dimensions": (
                list(score_details.get("degraded_dimensions", [])) if score_details else []
            ),
            "missing_factors": (
                list(score_details.get("missing_factors", [])) if score_details else []
            ),
            "input_coverage": (
                _finite(score_details.get("input_coverage")) if score_details else None
            ),
            "degradation_reason": (
                score_details.get("degradation_reason") if score_details else None
            ),
            "dimensions": dimensions,
        },
        "events": event_payload,
        "sector": sector,
        "forecast": forecast_payload,
        "profile": profile,
    }
    has_evidence = any((score is not None, bool(events), sector, forecast, security))
    return context, has_evidence


def _score_driver(dimension: dict[str, Any], qualifier: str | None = None) -> dict[str, str]:
    label = str(dimension["name"])
    value = _finite(dimension.get("value"))
    if value is None:
        return _driver(
            f"{label}评分暂无有效数据，证据仍在积累",
            "中性",
            str(dimension["source_ref"]),
        )
    if not bool(dimension.get("available")):
        return _driver(
            f"{label}因子输入暂缺，{value:.1f}分仅作降级参考",
            "中性",
            str(dimension["source_ref"]),
        )
    suffix = f"，为五维{qualifier}" if qualifier else ""
    if bool(dimension.get("degraded")):
        suffix += "，数据不完整"
    return _driver(
        f"{label}评分{value:.1f}/10{suffix}",
        _tag(value, positive=6.0, negative=4.0),
        str(dimension["source_ref"]),
    )


def _forecast_driver(forecast: dict[str, Any]) -> dict[str, str] | None:
    horizons = forecast.get("horizons")
    if not isinstance(horizons, dict) or not horizons:
        return None
    key = "20d" if "20d" in horizons else sorted(horizons)[-1]
    payload = horizons.get(key)
    if not isinstance(payload, dict):
        return None
    days = _finite(payload.get("horizon_days"))
    p_up = _finite(payload.get("p_up"))
    expected = _finite(payload.get("expected_return"))
    label = f"{int(days)}日" if days is not None else f"{key}期"
    if p_up is not None and expected is not None:
        text = f"{label}模型上涨概率{p_up:.1%}，预期收益{expected:+.1%}"
    elif p_up is not None:
        text = f"{label}模型上涨概率为{p_up:.1%}"
    elif expected is not None:
        text = f"{label}模型预期收益为{expected:+.1%}"
    else:
        text = f"{label}预测已生成，但方向证据有限"
    probability_tag = _tag(p_up, positive=0.55, negative=0.45)
    return_tag = _tag(expected, positive=0.005, negative=-0.005)
    if probability_tag != "中性" and return_tag != "中性" and probability_tag != return_tag:
        driver_tag = "中性"
    elif probability_tag != "中性":
        driver_tag = probability_tag
    else:
        driver_tag = return_tag
    return _driver(text, driver_tag, str(forecast["source_ref"]))


def _sector_driver(sector: dict[str, Any]) -> dict[str, str] | None:
    forecasts = sector.get("forecasts")
    if not isinstance(forecasts, list) or not forecasts:
        return None
    valid = [item for item in forecasts if isinstance(item, dict)]
    if not valid:
        return None
    anchor = next((item for item in valid if item.get("horizon") == 20), valid[-1])
    horizon = int(_finite(anchor.get("horizon")) or 20)
    score = _finite(anchor.get("score"))
    lifecycle_map = {
        "boom": "旺盛",
        "rising": "上升",
        "decline": "回落",
        "bottoming": "筑底",
        "recovery": "修复",
    }
    lifecycle = lifecycle_map.get(str(anchor.get("lifecycle") or ""), "待确认")
    plate_name = _text(sector.get("plate_name"), 16) or str(sector["plate_code"])
    score_text = f"评分{score:.1f}" if score is not None else "评分暂缺"
    limitation = (
        "，无资金流/固定成分口径"
        if sector.get("flow_mode") == "no-flow"
        else "，固定成分口径"
    )
    return _driver(
        f"{plate_name}板块{horizon}日{score_text}，阶段{lifecycle}{limitation}",
        _tag(score, positive=60.0, negative=40.0),
        str(sector["source_ref"]),
    )


def _profile_driver(profile: dict[str, Any]) -> dict[str, str]:
    name = _text(profile.get("name"), 16) or str(profile["symbol"])
    industry = _text(profile.get("industry"), 18)
    text = f"公司档案显示{name}属于{industry}" if industry else f"公司档案已收录{name}"
    return _driver(text, "中性", str(profile["source_ref"]))


def _append_unique(
    drivers: list[dict[str, str]],
    candidate: dict[str, str] | None,
) -> None:
    if candidate is None or not candidate["text"]:
        return
    if any(
        row["source_ref"] == candidate["source_ref"] or row["text"] == candidate["text"]
        for row in drivers
    ):
        return
    drivers.append(candidate)


def _rule_result(context: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    score = context["score"]
    dimensions = list(score["dimensions"])
    available = [
        item
        for item in dimensions
        if bool(item.get("available")) and _finite(item.get("value")) is not None
    ]
    drivers: list[dict[str, str]] = []

    if available:
        order = {dimension: index for index, dimension in enumerate(DIMENSION_ORDER)}
        highest = sorted(
            available,
            key=lambda item: (-float(item["value"]), order[str(item["key"])]),
        )[0]
        lowest = sorted(
            available,
            key=lambda item: (float(item["value"]), order[str(item["key"])]),
        )[0]
        spread = float(highest["value"]) - float(lowest["value"])
        _append_unique(drivers, _score_driver(highest, "较高项" if spread > 0 else None))
        if str(lowest["source_ref"]) != str(highest["source_ref"]):
            _append_unique(drivers, _score_driver(lowest, "较低项" if spread > 0 else None))

    events = context["events"]
    if events:
        event = events[0]
        fact = _text(event.get("summary") or event.get("title"), 36)
        _append_unique(
            drivers,
            _driver(
                f"事件：{fact}",
                _tag(_finite(event.get("direction")), positive=0.01, negative=-0.01),
                str(event["source_ref"]),
            ),
        )
    if context["sector"] is not None:
        _append_unique(drivers, _sector_driver(context["sector"]))
    if context["forecast"] is not None:
        _append_unique(drivers, _forecast_driver(context["forecast"]))
    if context["profile"] is not None:
        _append_unique(drivers, _profile_driver(context["profile"]))

    def dimension_distance(item: dict[str, Any]) -> tuple[float, int]:
        value = _finite(item.get("value"))
        normalized = 5.0 if value is None else value
        return -abs(normalized - 5.0), DIMENSION_ORDER.index(str(item["key"]))

    remaining = sorted(dimensions, key=dimension_distance)
    for dimension in remaining:
        if len(drivers) >= 3:
            break
        _append_unique(drivers, _score_driver(dimension))

    composite = _finite(score.get("composite"))
    profile = context.get("profile")
    name = (
        _text(profile.get("name"), 20)
        if isinstance(profile, dict) and profile.get("name")
        else str(context["symbol"])
    )
    if composite is None:
        core_view = (
            f"{name}当前缺少完整五维评分，暂不形成方向性结论；"
            "以下仅展示可核验的数据状态，仅供研究参考。"
        )
    elif not available:
        core_view = (
            f"{name}当前综合评分为{composite:.1f}/10，但五维因子输入不足；"
            "该分值仅作降级参考，不形成方向性结论。"
        )
    else:
        top = max(available, key=lambda item: float(item["value"]))
        bottom = min(available, key=lambda item: float(item["value"]))
        degraded_note = "部分维度数据不完整；" if bool(score.get("degraded")) else ""
        if math.isclose(float(top["value"]), float(bottom["value"]), abs_tol=1e-9):
            core_view = (
                f"{name}最新五维综合评分{composite:.1f}/10，"
                f"{degraded_note}当前可用维度分值接近，暂无明显强弱分化；"
                "仅供研究参考。"
            )
        else:
            core_view = (
                f"{name}最新五维综合评分{composite:.1f}/10，"
                f"{top['name']}相对较强、{bottom['name']}相对偏弱；"
                f"{degraded_note}"
                "仍需结合事件与板块证据审慎观察，仅供研究参考。"
            )
    return _text(core_view, 120), drivers[:6]


def _validated_llm_result(
    payload: dict[str, Any],
    allowed_refs: set[str],
) -> tuple[str, list[dict[str, str]]] | None:
    raw_core = payload.get("core_view")
    if not isinstance(raw_core, str):
        return None
    core_view = _text(raw_core, 120)
    if not core_view or not _contains_chinese(core_view):
        return None
    raw_drivers = payload.get("drivers")
    if not isinstance(raw_drivers, list):
        return None

    drivers: list[dict[str, str]] = []
    for raw in raw_drivers:
        if not isinstance(raw, dict):
            continue
        text = raw.get("text")
        tag = raw.get("tag")
        source_ref = raw.get("source_ref")
        if (
            not isinstance(text, str)
            or not isinstance(tag, str)
            or not isinstance(source_ref, str)
        ):
            continue
        normalized_text = _text(text, 40)
        if (
            not normalized_text
            or not _contains_chinese(normalized_text)
            or tag not in TAGS
            or source_ref not in allowed_refs
        ):
            continue
        _append_unique(drivers, _driver(normalized_text, tag, source_ref))
    if len(drivers) < 3:
        return None
    return core_view, drivers[:6]


def _cached_contract_is_valid(cached: StockInsight) -> bool:
    if cached.source not in {"rule", "llm"}:
        return False
    if (
        not isinstance(cached.core_view, str)
        or cached.core_view != _text(cached.core_view, 120)
        or not _contains_chinese(cached.core_view)
    ):
        return False
    if not isinstance(cached.drivers, list) or not 3 <= len(cached.drivers) <= 6:
        return False

    seen_refs: set[str] = set()
    seen_texts: set[str] = set()
    for raw in cached.drivers:
        if not isinstance(raw, dict) or set(raw) != {"text", "tag", "source_ref"}:
            return False
        text = raw.get("text")
        tag = raw.get("tag")
        source_ref = raw.get("source_ref")
        if (
            not isinstance(text, str)
            or not isinstance(tag, str)
            or not isinstance(source_ref, str)
        ):
            return False
        if (
            text != _text(text, 40)
            or not _contains_chinese(text)
            or tag not in TAGS
            or re.fullmatch(_SOURCE_REF_PATTERN, source_ref) is None
            or source_ref in seen_refs
            or text in seen_texts
        ):
            return False
        seen_refs.add(source_ref)
        seen_texts.add(text)
    return True


def _cache_is_fresh(
    session: Session,
    cached: StockInsight,
    symbol: str,
    now: datetime,
) -> bool:
    if cached.model_version != MODEL_VERSION or not _cached_contract_is_valid(cached):
        return False
    if _as_utc(cached.generated_at) <= now - CACHE_TTL:
        return False
    newer_event = session.scalar(
        select(DomainEvent.id)
        .where(
            DomainEvent.symbol == symbol,
            DomainEvent.ingested_at > cached.generated_at,
        )
        .limit(1)
    )
    return newer_event is None


def _persist_insight(
    session: Session,
    cached: StockInsight | None,
    *,
    symbol: str,
    generated_at: datetime,
    core_view: str,
    drivers: list[dict[str, str]],
    source: str,
) -> StockInsight:
    values = {
        "symbol": symbol,
        "generated_at": generated_at,
        "core_view": core_view,
        "drivers": drivers,
        "model_version": MODEL_VERSION,
        "source": source,
    }
    if session.get_bind().dialect.name == "sqlite":
        statement = sqlite_insert(StockInsight).values(**values)
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["symbol"],
                set_={
                    "generated_at": statement.excluded.generated_at,
                    "core_view": statement.excluded.core_view,
                    "drivers": statement.excluded.drivers,
                    "model_version": statement.excluded.model_version,
                    "source": statement.excluded.source,
                },
                # A slower process must not overwrite a result built from a
                # later evidence watermark.
                where=statement.excluded.generated_at > StockInsight.generated_at,
            )
        )
        stored = session.get(StockInsight, symbol, populate_existing=True)
        if stored is None:
            raise RuntimeError("个股解读原子写入失败。")
        return stored

    if cached is None:
        cached = StockInsight(symbol=symbol)
        session.add(cached)
    cached.generated_at = generated_at
    cached.core_view = core_view
    cached.drivers = drivers
    cached.model_version = MODEL_VERSION
    cached.source = source
    session.flush()
    return cached


def get_or_build(
    session: Session,
    symbol: str,
    force: bool = False,
    *,
    settings: Settings | None = None,
) -> StockInsight:
    """Return a fresh cached insight or build one from persisted evidence."""

    code = normalize_symbol(symbol)
    if len(code) != 6 or not code.isdigit():
        raise ValueError("股票代码必须是 6 位数字。")

    now = datetime.now(UTC)
    cached = session.get(StockInsight, code)
    if cached is not None and not force and _cache_is_fresh(session, cached, code, now):
        return cached

    with _build_lock(code):
        # Re-read after waiting for another in-process builder. SQLite's atomic
        # upsert below also covers the small gap before that request commits.
        cached = session.get(StockInsight, code, populate_existing=True)
        now = datetime.now(UTC)
        if cached is not None and not force and _cache_is_fresh(session, cached, code, now):
            return cached

        # This timestamp is the input watermark, not the completion time. An
        # event ingested while the LLM runs will invalidate this result.
        input_as_of = datetime.now(UTC)
        context, has_evidence = _build_context(session, code, input_as_of)

        rule_core, rule_drivers = _rule_result(context)
        if not 3 <= len(rule_drivers) <= 6:
            raise RuntimeError("规则解读未能生成 3 至 6 条可核验驱动。")
        core_view = rule_core
        drivers = rule_drivers
        source = "rule"
        # Three distinct evidence refs are the minimum needed for a contract-
        # valid LLM response. With less evidence, keep the honest rule result
        # and avoid asking the model to manufacture extra drivers.
        if has_evidence and len(context["allowed_source_refs"]) >= 3:
            try:
                response = chat_json(
                    "stock_insight",
                    STOCK_INSIGHT,
                    "以下 JSON 是唯一证据。source_ref 只能逐字取自 allowed_source_refs：\n"
                    + json.dumps(
                        context,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    INSIGHT_SCHEMA,
                    settings=settings,
                    session=session,
                )
                accepted = _validated_llm_result(
                    response, set(context["allowed_source_refs"])
                )
                if accepted is not None:
                    core_view, drivers = accepted
                    source = "llm"
            except LLMUnavailable:
                pass

        return _persist_insight(
            session,
            cached,
            symbol=code,
            generated_at=input_as_of,
            core_view=core_view,
            drivers=drivers,
            source=source,
        )


def insight_payload(insight: StockInsight) -> dict[str, Any]:
    return {
        "symbol": insight.symbol,
        "generated_at": iso_utc(insight.generated_at),
        "core_view": insight.core_view,
        "drivers": insight.drivers,
        "model_version": insight.model_version,
        "source": insight.source,
    }


__all__ = [
    "INSIGHT_SCHEMA",
    "get_or_build",
    "insight_payload",
]
