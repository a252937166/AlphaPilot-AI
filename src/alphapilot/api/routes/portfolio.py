from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, futu_client_dependency
from alphapilot.futu.client import FutuClient, FutuClientError, FutuFeatureDisabledError
from alphapilot.services.broker import BrokerError, fetch_account_funds, fetch_positions
from alphapilot.services.portfolio import (
    PortfolioServiceError,
    get_portfolio_attribution,
    get_portfolio_overview,
)

router = APIRouter(prefix="/v1/portfolio", tags=["portfolio"])


@router.get("/account")
def portfolio_account(
    client: FutuClient = Depends(futu_client_dependency),
) -> dict[str, Any]:
    try:
        funds = fetch_account_funds(client)
        positions = fetch_positions(client)
    except FutuFeatureDisabledError as exc:
        raise HTTPException(
            status_code=503,
            detail="富途模拟账户只读查询未启用。",
        ) from exc
    except BrokerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except FutuClientError as exc:
        raise HTTPException(
            status_code=503,
            detail="富途模拟账户暂不可用，请确认 OpenD 与交易登录状态。",
        ) from exc
    return {
        "market": "CN",
        "environment": "SIMULATE",
        **funds,
        "positions": positions,
    }


@router.get("/overview")
def portfolio_overview(
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    try:
        return get_portfolio_overview(session)
    except PortfolioServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/attribution")
def portfolio_attribution(
    days: int = Query(default=60, ge=1, le=365),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    try:
        return get_portfolio_attribution(session, days)
    except PortfolioServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
