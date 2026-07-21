from __future__ import annotations

from math import isfinite
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, settings_dependency
from alphapilot.core.config import Settings
from alphapilot.db.models import CompositeScore, FactorValue
from alphapilot.engines.factors import load_weights
from alphapilot.services.watchlist import normalize_symbol

router = APIRouter(prefix="/v1", tags=["factors"])


def _optional_number(value: float | None) -> float | None:
    if value is None:
        return None
    return value if isfinite(value) else None


@router.get("/factors/weights")
def factor_weights(
    settings: Settings = Depends(settings_dependency),
) -> dict[str, Any]:
    try:
        config = load_weights(settings.factor_weights_file)
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"因子权重配置不可用：{exc}") from exc
    return {
        "version": config.version,
        "profile": config.profile,
        "weights": config.weights,
    }


@router.get("/stocks/{symbol}/factors")
def stock_factors(
    symbol: str,
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    code = normalize_symbol(symbol)
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status_code=422, detail="股票代码必须是 6 位数字。")

    latest = session.scalars(
        select(CompositeScore)
        .where(CompositeScore.symbol == code)
        .order_by(CompositeScore.trade_date.desc(), CompositeScore.id.desc())
        .limit(1)
    ).first()
    if latest is None:
        raise HTTPException(status_code=404, detail=f"暂无 {code} 的因子评分。")

    rows = session.scalars(
        select(FactorValue)
        .where(
            FactorValue.symbol == code,
            FactorValue.trade_date == latest.trade_date,
        )
        .order_by(FactorValue.factor)
    ).all()
    factors = {
        row.factor: {
            "raw": _optional_number(row.raw),
            "zscore": _optional_number(row.zscore),
        }
        for row in rows
    }
    return {
        "symbol": code,
        "trade_date": latest.trade_date.isoformat(),
        "score": latest.score,
        "win_rate_20d": _optional_number(latest.win_rate_20d),
        "model_version": latest.model_version,
        "factors": factors,
    }
