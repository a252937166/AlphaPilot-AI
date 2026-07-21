from __future__ import annotations

from enum import IntEnum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, futu_client_dependency
from alphapilot.futu.client import FutuClient
from alphapilot.services.sectors import (
    SectorServiceError,
    get_sector_forecast_view,
    get_sector_strength,
)

router = APIRouter(prefix="/v1/sectors", tags=["sectors"])


class SectorHorizon(IntEnum):
    FIVE = 5
    TEN = 10
    TWENTY = 20


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


@router.get("/forecast")
def sector_forecast(
    horizon: SectorHorizon = Query(default=SectorHorizon.FIVE),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    try:
        return get_sector_forecast_view(session, horizon=int(horizon))
    except SectorServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/lifecycle")
def sector_lifecycle(
    horizon: SectorHorizon = Query(default=SectorHorizon.TWENTY),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    try:
        return get_sector_forecast_view(session, horizon=int(horizon), view="lifecycle")
    except SectorServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/overbought")
def sector_overbought(
    horizon: SectorHorizon = Query(default=SectorHorizon.TWENTY),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    try:
        return get_sector_forecast_view(session, horizon=int(horizon), view="overbought")
    except SectorServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/reversal")
def sector_reversal(
    horizon: SectorHorizon = Query(default=SectorHorizon.TWENTY),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    try:
        return get_sector_forecast_view(session, horizon=int(horizon), view="reversal")
    except SectorServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
