from fastapi import APIRouter, HTTPException

from alphapilot.api.dependencies import get_provider
from alphapilot.data.base import DataProviderError
from alphapilot.domain.models import ScreeningRequest, ScreeningResponse
from alphapilot.prediction.baseline import BaselineForecastEngine
from alphapilot.screening.service import ScreeningService

router = APIRouter(prefix="/v1/screens", tags=["screening"])


@router.post("/run", response_model=ScreeningResponse)
def run_screen(request: ScreeningRequest) -> ScreeningResponse:
    try:
        provider = get_provider(request.provider)
        return ScreeningService(provider, BaselineForecastEngine()).run(request)
    except DataProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
