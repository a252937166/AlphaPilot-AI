from __future__ import annotations

import json
from typing import Any

import httpx

from alphapilot.core.config import Settings


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


def compose_market_summary(settings: Settings, context: dict[str, Any]) -> dict[str, str]:
    """LLM-generated market comment when configured, deterministic template otherwise."""
    if settings.llm_base_url and settings.llm_api_key and settings.llm_model:
        try:
            payload = {
                "model": settings.llm_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是量化投研助手。基于给定 JSON 数据写一段不超过120字的中文市场观察，"
                            "只描述数据中可见的事实与不确定性，禁止编造具体价格预测，"
                            "结尾注明不构成投资建议。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(context, ensure_ascii=False, default=str)[:6000],
                    },
                ],
                "temperature": 0.3,
            }
            response = httpx.post(
                settings.llm_base_url.rstrip("/") + "/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                timeout=12.0,
            )
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"].strip()
            if text:
                return {"text": text, "source": "llm"}
        except Exception:  # LLM is optional; template keeps the page alive
            pass
    return {"text": _template_market_summary(context), "source": "template"}
