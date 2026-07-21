from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import cninfo_client_dependency, db_session_dependency
from alphapilot.cninfo.client import CninfoClient, CninfoError, CninfoNotConfiguredError
from alphapilot.services import disclosures as disclosure_service

router = APIRouter(prefix="/v1/disclosures", tags=["disclosures"])


@router.get("/status")
def cninfo_status(client: CninfoClient = Depends(cninfo_client_dependency)) -> dict[str, Any]:
    return {
        "configured": client.configured,
        "webapi_base_url": client.settings.cninfo_base_url,
        "announcement_base_url": client.settings.cninfo_announcement_base_url,
        "note": "webapi 凭据仅从本地 .env 读取，公告查询走巨潮公开接口。",
    }


@router.get("/{symbol}")
def list_symbol_disclosures(
    symbol: str,
    sync: bool = Query(default=False, description="true 时先从巨潮拉取最新公告"),
    days: int = Query(default=45, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(db_session_dependency),
    client: CninfoClient = Depends(cninfo_client_dependency),
) -> dict[str, Any]:
    sync_result: dict[str, Any] | None = None
    if sync:
        try:
            sync_result = disclosure_service.sync_disclosures(
                session, client, symbol, days=days
            )
        except CninfoNotConfiguredError:
            # Announcements come from the public endpoint and work without
            # webapi credentials, so this should not happen; keep the guard.
            sync_result = {"error": "cninfo not configured"}
        except CninfoError as exc:
            sync_result = {"error": str(exc)}
    disclosures = disclosure_service.list_disclosures(session, symbol, limit=limit)
    return {"symbol": symbol, "sync": sync_result, "disclosures": disclosures}


@router.post("/{symbol}/sync")
def sync_symbol(
    symbol: str,
    session: Session = Depends(db_session_dependency),
    client: CninfoClient = Depends(cninfo_client_dependency),
) -> dict[str, Any]:
    result = disclosure_service.safe_sync_symbol(session, client, symbol)
    if isinstance(result.get("disclosures"), str):
        raise HTTPException(status_code=502, detail=result)
    return result
