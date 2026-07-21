from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.db.models import CompositeScore, StyleDaily
from alphapilot.domain.models import (
    StyleDailyPoint,
    StyleDailyResponse,
)
from alphapilot.engines.style import style_source_fingerprint

router = APIRouter(prefix="/v1/style", tags=["style"])


@router.get("/daily", response_model=StyleDailyResponse)
def style_daily(
    days: int = Query(default=60, ge=1, le=365),
    session: Session = Depends(db_session_dependency),
) -> StyleDailyResponse:
    """Return only real persisted style observations, oldest first."""

    latest_input_date = session.scalar(select(func.max(CompositeScore.trade_date)))
    latest_style = session.scalars(
        select(StyleDaily).order_by(StyleDaily.trade_date.desc()).limit(1)
    ).first()
    validated_fingerprint: str | None = None
    if isinstance(latest_input_date, date):
        current_fingerprint = style_source_fingerprint(session, latest_input_date)
        style_is_current = bool(
            latest_style is not None
            and latest_style.trade_date == latest_input_date
            and latest_style.source_fingerprint == current_fingerprint
        )
        if not style_is_current:
            raise HTTPException(
                status_code=503,
                detail="风格序列尚未与最新综合评分同步，请先运行 compute_style_daily 任务。",
            )
        validated_fingerprint = current_fingerprint

    newest_first = session.scalars(
        select(StyleDaily).order_by(StyleDaily.trade_date.desc()).limit(days)
    ).all()
    if (
        isinstance(latest_input_date, date)
        and validated_fingerprint is not None
        and style_source_fingerprint(session, latest_input_date) != validated_fingerprint
    ):
        raise HTTPException(
            status_code=503,
            detail="风格输入在读取序列期间发生变化，请稍后重试 compute_style_daily 任务。",
        )
    rows = list(reversed(newest_first))
    return StyleDailyResponse(
        requested_days=days,
        available_days=len(rows),
        series=[
            StyleDailyPoint(
                trade_date=row.trade_date,
                growth_pct=row.growth_pct,
                value_pct=row.value_pct,
                defensive_pct=row.defensive_pct,
                balanced_pct=row.balanced_pct,
                model_version=row.model_version,
            )
            for row in rows
        ],
    )
