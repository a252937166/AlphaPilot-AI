from __future__ import annotations

import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.db.models import CompositeScore, FactorValue, StockScore
from alphapilot.engines.stock_score import (
    DIMENSION_FACTORS,
    DIMENSION_LABELS,
    DIMENSION_ORDER,
    DIMENSION_WEIGHTS,
    REQUIRED_FACTORS,
)


def _finite_or_none(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def latest_score(session: Session, symbol: str) -> StockScore | None:
    """Return only a score aligned to the symbol's latest composite-factor date."""

    latest_factor_date = session.scalar(
        select(CompositeScore.trade_date)
        .where(CompositeScore.symbol == symbol)
        .order_by(CompositeScore.trade_date.desc(), CompositeScore.id.desc())
        .limit(1)
    )
    if latest_factor_date is None:
        return None
    return session.scalars(
        select(StockScore)
        .where(
            StockScore.symbol == symbol,
            StockScore.trade_date == latest_factor_date,
        )
        .order_by(StockScore.id.desc())
        .limit(1)
    ).first()


def score_payload(session: Session, score: StockScore) -> dict[str, Any]:
    """Serialize a persisted score with same-day input availability disclosure."""

    rows = session.scalars(
        select(FactorValue)
        .where(
            FactorValue.symbol == score.symbol,
            FactorValue.trade_date == score.trade_date,
            FactorValue.factor.in_(REQUIRED_FACTORS),
        )
        .order_by(FactorValue.factor, FactorValue.id.desc())
    ).all()
    latest_by_factor: dict[str, FactorValue] = {}
    for row in rows:
        latest_by_factor.setdefault(row.factor, row)

    inputs: dict[str, dict[str, Any]] = {}
    for factor in REQUIRED_FACTORS:
        factor_row = latest_by_factor.get(factor)
        zscore = _finite_or_none(factor_row.zscore) if factor_row is not None else None
        inputs[factor] = {
            "raw": _finite_or_none(factor_row.raw) if factor_row is not None else None,
            "zscore": zscore,
            "available": zscore is not None,
            "model_version": factor_row.model_version if factor_row is not None else None,
        }

    radar: list[dict[str, Any]] = []
    degraded_dimensions: list[str] = []
    for dimension in DIMENSION_ORDER:
        factors = DIMENSION_FACTORS[dimension]
        available_inputs = sum(bool(inputs[factor]["available"]) for factor in factors)
        complete = available_inputs == len(factors)
        if not complete:
            degraded_dimensions.append(dimension)
        radar.append(
            {
                "key": dimension,
                "name": DIMENSION_LABELS[dimension],
                "value": getattr(score, dimension),
                "max": 10.0,
                "available_inputs": available_inputs,
                "required_inputs": len(factors),
                "degraded": not complete,
            }
        )

    missing_factors = [
        factor for factor in REQUIRED_FACTORS if not bool(inputs[factor]["available"])
    ]
    available_count = len(REQUIRED_FACTORS) - len(missing_factors)
    return {
        "symbol": score.symbol,
        "trade_date": score.trade_date.isoformat(),
        "tech": score.tech,
        "capital": score.capital,
        "fundamental": score.fundamental,
        "valuation": score.valuation,
        "sentiment": score.sentiment,
        "composite": score.composite,
        "model_version": score.model_version,
        "dimension_weights": dict(DIMENSION_WEIGHTS),
        "radar": radar,
        "inputs": inputs,
        "missing_factors": missing_factors,
        "degraded_dimensions": degraded_dimensions,
        "input_coverage": round(available_count / len(REQUIRED_FACTORS), 6),
        "degraded": bool(missing_factors),
        "degradation_reason": (
            "部分因子暂无同日有效值，对应固定因子槽按中性 z=0 映射为 5 分。"
            if missing_factors
            else None
        ),
    }


def latest_score_payload(session: Session, symbol: str) -> dict[str, Any] | None:
    score = latest_score(session, symbol)
    return score_payload(session, score) if score is not None else None
