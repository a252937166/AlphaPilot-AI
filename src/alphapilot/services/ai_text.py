from __future__ import annotations

import hashlib
import json
import time
from threading import Lock
from typing import Any

from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.llm.client import LLMUnavailable, chat_json

MARKET_SUMMARY_CACHE_TTL_SECONDS = 600.0
_MARKET_SUMMARY_CACHE_MAX_ENTRIES = 128
_market_summary_cache: dict[str, tuple[float, dict[str, str]]] = {}
_market_summary_cache_lock = Lock()

_MARKET_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["text"],
    "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 120},
    },
    "additionalProperties": False,
}

_MARKET_SUMMARY_SYSTEM = (
    "你是量化投研助手。基于给定 JSON 数据写一段不超过120字的中文市场观察，"
    "只描述数据中可见的事实与不确定性，禁止编造具体价格预测，"
    "结尾注明不构成投资建议。只返回 JSON 对象，格式为 {\"text\": \"摘要\"}。"
)


def _template_market_summary(context: dict[str, Any]) -> str:
    regime = context.get("regime") or {}
    indices = context.get("indices") or []
    sectors = context.get("sectors") or []
    breadth = context.get("breadth") or {}
    parts: list[str] = []
    if regime:
        parts.append(
            f"基准指数状态判定为「{regime.get('regime', 'unknown')}」，"
            f"置信度 {float(regime.get('confidence', 0)) * 100:.0f}%。"
        )
    ups = [item for item in indices if (item.get("change_pct") or 0) > 0]
    if indices:
        parts.append(f"跟踪的 {len(indices)} 个核心指数中 {len(ups)} 个上涨。")
    if breadth:
        parts.append(
            f"样本宽度 {breadth.get('advancers', 0)} 涨 / {breadth.get('decliners', 0)} 跌，"
            f"平均涨跌 {breadth.get('avg_change_pct', 0)}%。"
        )
    if sectors:
        top = sectors[0]
        parts.append(
            f"板块强度榜首为{top.get('plate_name')}（强度 {top.get('strength')}，"
            f"上涨占比 {float(top.get('up_ratio', 0)) * 100:.0f}%）。"
        )
    parts.append("以上为规则模板生成的观察摘要，仅描述数据，不构成投资建议。")
    return "".join(parts)


def _market_summary_cache_key(settings: Settings, context: dict[str, Any]) -> str:
    effective_model = settings.llm_purpose_models.get("market_summary", settings.llm_model)
    signature = {
        "context": context,
        "llm_configured": bool(settings.llm_base_url and settings.llm_api_key),
        "base_url": settings.llm_base_url or "",
        "model": effective_model,
    }
    canonical = json.dumps(
        signature,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _get_cached_market_summary(cache_key: str) -> dict[str, str] | None:
    now = time.monotonic()
    with _market_summary_cache_lock:
        cached = _market_summary_cache.get(cache_key)
        if cached is None:
            return None
        expires_at, result = cached
        if expires_at <= now:
            del _market_summary_cache[cache_key]
            return None
        return dict(result)


def _cache_market_summary(cache_key: str, result: dict[str, str]) -> None:
    now = time.monotonic()
    with _market_summary_cache_lock:
        expired_keys = [
            key for key, (expires_at, _) in _market_summary_cache.items() if expires_at <= now
        ]
        for key in expired_keys:
            del _market_summary_cache[key]
        if len(_market_summary_cache) >= _MARKET_SUMMARY_CACHE_MAX_ENTRIES:
            oldest_key = min(
                _market_summary_cache,
                key=lambda key: _market_summary_cache[key][0],
            )
            del _market_summary_cache[oldest_key]
        _market_summary_cache[cache_key] = (
            now + MARKET_SUMMARY_CACHE_TTL_SECONDS,
            dict(result),
        )


def _clear_market_summary_cache() -> None:
    """Clear process-local summary content cache for deterministic tests."""
    with _market_summary_cache_lock:
        _market_summary_cache.clear()


def compose_market_summary(
    settings: Settings,
    context: dict[str, Any],
    session: Session | None = None,
) -> dict[str, str]:
    """LLM-generated market comment when configured, deterministic template otherwise."""
    cache_key = _market_summary_cache_key(settings, context)
    cached = _get_cached_market_summary(cache_key)
    if cached is not None:
        return cached

    try:
        payload = chat_json(
            "market_summary",
            _MARKET_SUMMARY_SYSTEM,
            json.dumps(context, ensure_ascii=False, default=str)[:6000],
            _MARKET_SUMMARY_SCHEMA,
            settings=settings,
            session=session,
        )
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            result = {"text": text.strip(), "source": "llm"}
        else:
            result = {"text": _template_market_summary(context), "source": "template"}
    except LLMUnavailable:
        result = {"text": _template_market_summary(context), "source": "template"}

    _cache_market_summary(cache_key, result)
    return result
