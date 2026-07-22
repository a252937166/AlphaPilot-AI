from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from queue import Empty
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from alphapilot.api.dependencies import futu_client_dependency
from alphapilot.futu.client import (
    TRADE_MUTATION_METHODS,
    FutuCallValidationError,
    FutuClient,
    FutuFeatureDisabledError,
    FutuMethodNotAllowedError,
    FutuSDKError,
    FutuUnavailableError,
)

router = APIRouter(prefix="/v1/futu", tags=["futu"])

_HTTP_PRIVATE_TRADE_METHODS = frozenset({"get_acc_list", "position_list_query"})


class FutuQuoteCallRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    arguments: list[Any] = Field(default_factory=list, alias="args", max_length=1000)
    keyword_arguments: dict[str, Any] = Field(default_factory=dict, alias="kwargs")


class FutuTradeCallRequest(FutuQuoteCallRequest):
    market: str = Field(default="HK", min_length=2, max_length=16)
    environment: Literal["SIMULATE", "REAL"] = "SIMULATE"
    confirmation: str | None = Field(default=None, max_length=64)


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, (FutuMethodNotAllowedError, FutuFeatureDisabledError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, FutuCallValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, FutuSDKError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if isinstance(exc, FutuUnavailableError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    raise exc


@router.get("/status")
def futu_status(client: FutuClient = Depends(futu_client_dependency)) -> dict[str, Any]:
    return client.status(max_age_seconds=0)


@router.get("/capabilities")
def futu_capabilities(client: FutuClient = Depends(futu_client_dependency)) -> dict[str, Any]:
    return client.capabilities()


@router.post("/quote/{method}")
def futu_quote_call(
    method: str,
    request: FutuQuoteCallRequest,
    client: FutuClient = Depends(futu_client_dependency),
) -> dict[str, Any]:
    try:
        return client.quote_call(method, request.arguments, request.keyword_arguments)
    except Exception as exc:
        _raise_http_error(exc)
        raise AssertionError("unreachable") from exc


@router.post("/trade/{context_kind}/{method}")
def futu_trade_call(
    context_kind: Literal["security", "future", "crypto"],
    method: str,
    request: FutuTradeCallRequest,
    client: FutuClient = Depends(futu_client_dependency),
) -> dict[str, Any]:
    try:
        if method in TRADE_MUTATION_METHODS:
            raise FutuMethodNotAllowedError(
                "通用富途 HTTP 路由禁止交易写操作，请使用受控的模拟交易执行接口。"
            )
        if method in _HTTP_PRIVATE_TRADE_METHODS:
            raise FutuMethodNotAllowedError(
                "富途账户与持仓查询仅供内部使用，请改用 /v1/portfolio/account。"
            )
        return client.trade_call(
            context_kind,
            method,
            args=request.arguments,
            kwargs=request.keyword_arguments,
            market=request.market,
            environment=request.environment,
            confirmation=request.confirmation,
        )
    except Exception as exc:
        _raise_http_error(exc)
        raise AssertionError("unreachable") from exc


@router.websocket("/stream")
async def futu_event_stream(
    websocket: WebSocket,
    client: FutuClient = Depends(futu_client_dependency),
) -> None:
    await websocket.accept()
    try:
        event_queue = await asyncio.to_thread(client.subscribe_events)
    except Exception as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=1011)
        return

    try:
        await websocket.send_json(
            {
                "type": "ready",
                "received_at": datetime.now(UTC).isoformat(),
                "event_types": sorted(client.capabilities()["push_event_types"]),
            }
        )
        while True:
            try:
                event = await asyncio.to_thread(event_queue.get, True, 15.0)
            except Empty:
                event = {"type": "heartbeat", "received_at": datetime.now(UTC).isoformat()}
            await websocket.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        client.unsubscribe_events(event_queue)
