from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import (
    db_session_dependency,
    get_provider,
    settings_dependency,
)
from alphapilot.core.config import Settings
from alphapilot.services import reports as report_service

router = APIRouter(prefix="/v1/reports", tags=["reports"])


@router.get("/daily")
def get_daily_report(
    report_date: date | None = Query(default=None),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    payload = report_service.get_daily_report(session, report_date=report_date)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail="No daily report yet; POST /v1/reports/daily/generate first.",
        )
    return payload


@router.post("/daily/generate")
def generate_daily_report(
    report_date: date | None = Query(default=None),
    provider: str | None = Query(default=None),
    session: Session = Depends(db_session_dependency),
    settings: Settings = Depends(settings_dependency),
) -> dict[str, Any]:
    selected = get_provider(provider)
    return report_service.generate_daily_report(
        session, settings, selected, report_date=report_date
    )
