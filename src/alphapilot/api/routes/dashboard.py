from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import (
    db_session_dependency,
    futu_client_dependency,
    get_provider,
    settings_dependency,
)
from alphapilot.core.config import Settings
from alphapilot.futu.client import FutuClient
from alphapilot.services import dashboard as dashboard_service

router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])


@router.get("/overview")
def dashboard_overview(
    provider: str | None = Query(default=None),
    session: Session = Depends(db_session_dependency),
    settings: Settings = Depends(settings_dependency),
    futu_client: FutuClient = Depends(futu_client_dependency),
) -> dict[str, Any]:
    selected = get_provider(provider)
    return dashboard_service.overview(session, settings, selected, futu_client)
