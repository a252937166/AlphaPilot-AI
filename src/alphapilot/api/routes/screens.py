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
from alphapilot.services.screening_v2 import (
    ScreeningFilterError,
    ScreeningUnavailableError,
    run_factor_screen,
)

router = APIRouter(prefix="/v1/screens", tags=["screening"])

# Demo universe until the point-in-time security master lands.
DEFAULT_UNIVERSE = [
    "600519",
    "300750",
    "002594",
    "600000",
    "000001",
    "000333",
    "601318",
    "600036",
    "000858",
    "002415",
    "688111",
    "603259",
    "600900",
    "601012",
    "300059",
]


def _screen_filters(request: ScreeningRequest) -> dict[str, Any]:
    return {
        "symbols": list(request.symbols) if request.symbols is not None else None,
        "industries": (
            sorted(
                dict.fromkeys(
                    industry.strip() for industry in request.industries if industry.strip()
                )
            )
            if request.industries is not None
            else None
        ),
        "style": request.style,
        "risk_level": request.risk_level,
        "min_market_cap": request.min_market_cap,
        "top_n": request.top_n,
        "sort_by": request.sort_by,
        "provider": request.provider,
        "lookback_days": request.lookback_days,
    }


@router.post("/run", response_model=ScreeningResponse)
def run_screen(
    request: ScreeningRequest,
    session: Session = Depends(db_session_dependency),
) -> ScreeningResponse:
    if request.universe == "custom":
        filter_error = request.custom_filter_error()
        if filter_error is not None:
            raise HTTPException(status_code=422, detail=filter_error)
        try:
            provider = get_provider(request.provider)
            response = ScreeningService(provider, BaselineForecastEngine()).run(request)
        except (DataProviderError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail="行情数据不可用，请检查数据源配置和股票代码后重试。",
            ) from exc
    else:
        try:
            response = run_factor_screen(session, request)
        except ScreeningFilterError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ScreeningUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    session.add(
        ScreeningRun(
            universe=request.universe,
            filters=_screen_filters(request),
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
        select(ScreeningRun)
        .order_by(ScreeningRun.created_at.desc(), ScreeningRun.id.desc())
        .limit(1)
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="暂无选股运行记录。")
    return {
        "id": run.id,
        "universe": run.universe,
        "filters": run.filters,
        "provider": run.provider,
        "model_version": run.model_version,
        "requested": run.requested,
        "succeeded": run.succeeded,
        "failed": run.failed,
        "candidates": run.candidates,
        "created_at": iso_utc(run.created_at),
    }


def _candidate_symbols(candidates: list[Any]) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        symbol = str(candidate.get("symbol") or "").strip()
        if not symbol or symbol in seen:
            continue
        symbols.append(symbol)
        seen.add(symbol)
    return symbols


@router.get("/diff")
def screen_diff(session: Session = Depends(db_session_dependency)) -> dict[str, Any]:
    current = session.scalars(
        select(ScreeningRun)
        .order_by(ScreeningRun.created_at.desc(), ScreeningRun.id.desc())
        .limit(1)
    ).first()
    if current is None:
        raise HTTPException(status_code=404, detail="暂无选股运行记录，无法比较变化。")

    current_filters = current.filters if isinstance(current.filters, dict) else {}
    prior_runs = session.scalars(
        select(ScreeningRun)
        .where(
            ScreeningRun.id != current.id,
            ScreeningRun.universe == current.universe,
        )
        .order_by(ScreeningRun.created_at.desc(), ScreeningRun.id.desc())
    ).all()
    previous = next(
        (
            run
            for run in prior_runs
            if (run.filters if isinstance(run.filters, dict) else {}) == current_filters
        ),
        None,
    )
    current_symbols = _candidate_symbols(current.candidates)
    previous_symbols = _candidate_symbols(previous.candidates) if previous else []
    current_set = set(current_symbols)
    previous_set = set(previous_symbols)
    return {
        "current_run_id": current.id,
        "previous_run_id": previous.id if previous else None,
        "universe": current.universe,
        "filters": current_filters,
        "baseline_missing": previous is None,
        "new": [symbol for symbol in current_symbols if symbol not in previous_set],
        "dropped": [symbol for symbol in previous_symbols if symbol not in current_set],
        "stayed": len(current_set.intersection(previous_set)),
    }
