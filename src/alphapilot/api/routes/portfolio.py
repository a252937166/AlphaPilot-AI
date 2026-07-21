from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from alphapilot.api.dependencies import futu_client_dependency
from alphapilot.futu.client import FutuClient, FutuClientError, FutuFeatureDisabledError
from alphapilot.services.broker import BrokerError, fetch_account_funds, fetch_positions

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
