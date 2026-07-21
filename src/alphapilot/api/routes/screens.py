from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, get_provider
from alphapilot.core.timeutil import iso_utc
from alphapilot.data.base import DataProviderError
from alphapilot.db.models import ScreeningRun
from alphapilot.domain.models import ScreeningRequest, ScreeningResponse
from alphapilot.prediction.baseline import BaselineForecastEngine
from alphapilot.screening.service import ScreeningService

router = APIRouter(prefix="/v1/screens", tags=["screening"])

# Demo universe until the point-in-time security master lands.
DEFAULT_UNIVERSE = [
    "600519", "300750", "002594", "600000", "000001",
    "000333", "601318", "600036", "000858", "002415",
    "688111", "603259", "600900", "601012", "300059",
]


@router.post("/run", response_model=ScreeningResponse)
def run_screen(
    request: ScreeningRequest,
    session: Session = Depends(db_session_dependency),
) -> ScreeningResponse:
    try:
        provider = get_provider(request.provider)
        response = ScreeningService(provider, BaselineForecastEngine()).run(request)
    except DataProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.add(
        ScreeningRun(
            provider=response.provider,
            model_version=response.model_version,
            requested=response.requested,
            succeeded=response.succeeded,
            failed=response.failed,
            candidates=[item.model_dump(mode="json") for item in response.candidates],
        )
    )
    return response


@router.get("/universe")
def default_universe() -> dict[str, Any]:
    return {"symbols": DEFAULT_UNIVERSE}


@router.get("/latest")
def latest_screen(session: Session = Depends(db_session_dependency)) -> dict[str, Any]:
    run = session.scalars(
        select(ScreeningRun).order_by(ScreeningRun.created_at.desc()).limit(1)
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="No screening run recorded yet.")
    return {
        "id": run.id,
        "provider": run.provider,
        "model_version": run.model_version,
        "requested": run.requested,
        "succeeded": run.succeeded,
        "failed": run.failed,
        "candidates": run.candidates,
        "created_at": iso_utc(run.created_at),
    }
