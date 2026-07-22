from __future__ import annotations

from enum import IntEnum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, futu_client_dependency
from alphapilot.futu.client import FutuClient
from alphapilot.services.sectors import (
    SectorNotFoundError,
    SectorServiceError,
    get_sector_forecast_view,
    get_sector_leaders,
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


@router.get("/{plate_code}/leaders")
def sector_leaders(
    plate_code: str = Path(
        min_length=5,
        max_length=24,
        pattern=r"^(SH|SZ)\.[A-Z0-9]+$",
    ),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    try:
        return get_sector_leaders(session, plate_code=plate_code)
    except SectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SectorServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
