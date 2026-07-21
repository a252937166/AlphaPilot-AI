from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, futu_client_dependency
from alphapilot.futu.client import FutuClient
from alphapilot.services.sectors import SectorServiceError, get_sector_strength

router = APIRouter(prefix="/v1/sectors", tags=["sectors"])


@router.get("/strength")
def sector_strength(
    refresh: bool = Query(default=False),
    session: Session = Depends(db_session_dependency),
    client: FutuClient = Depends(futu_client_dependency),
) -> dict[str, Any]:
    try:
        return get_sector_strength(session, client, refresh=refresh)
    except SectorServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
