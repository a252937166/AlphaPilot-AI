from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, futu_client_dependency
from alphapilot.core.config import get_settings
from alphapilot.core.timeutil import iso_utc
from alphapilot.db.models import BrokerOrder, TradeProposalRecord
from alphapilot.domain.models import PortfolioState, RiskDecision, TradeProposal
from alphapilot.futu.client import FutuClient
from alphapilot.risk.guardrails import TradeGuardrails
from alphapilot.services.executor import (
    ExecutionBlocked,
    ExecutionConflict,
    ExecutionRejected,
    ExecutionUnavailable,
    execute_proposal,
)

router = APIRouter(prefix="/v1/trades", tags=["trading-risk"])
orders_router = APIRouter(prefix="/v1/orders", tags=["paper-orders"])


class TradeEvaluationRequest(BaseModel):
    proposal: TradeProposal
    portfolio: PortfolioState


@router.post("/evaluate", response_model=RiskDecision)
def evaluate_trade(request: TradeEvaluationRequest) -> RiskDecision:
    return TradeGuardrails(get_settings()).evaluate(request.proposal, request.portfolio)


def _proposal_payload(record: TradeProposalRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "proposal_id": record.proposal_id,
        "symbol": record.symbol,
        "side": record.side,
        "quantity": record.quantity,
        "estimated_notional": record.estimated_notional,
        "confidence": record.confidence,
        "mode": record.mode,
        "status": record.status,
        "proposal": record.proposal,
        "risk_decision": record.risk_decision,
        "created_at": iso_utc(record.created_at),
        "reviewed_at": iso_utc(record.reviewed_at),
    }


def _order_payload(order: BrokerOrder) -> dict[str, Any]:
    return {
        "id": order.id,
        "proposal_id": order.proposal_id,
        "futu_order_id": order.futu_order_id,
        "symbol": order.symbol,
        "side": order.side,
        "order_type": order.order_type,
        "price": order.price,
        "qty": order.qty,
        "status": order.status,
        "filled_qty": order.filled_qty,
        "avg_fill_price": order.avg_fill_price,
        "environment": order.environment,
        "error": order.error,
        "created_at": iso_utc(order.created_at),
        "updated_at": iso_utc(order.updated_at),
    }


@router.post("/proposals")
def create_proposal(
    request: TradeEvaluationRequest,
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    """Evaluate and persist a proposal; execution stays disabled by default."""
    decision = TradeGuardrails(get_settings()).evaluate(request.proposal, request.portfolio)
    existing = session.scalars(
        select(TradeProposalRecord).where(
            TradeProposalRecord.proposal_id == request.proposal.proposal_id
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"Proposal {request.proposal.proposal_id} already exists"
        )
    record = TradeProposalRecord(
        proposal_id=request.proposal.proposal_id,
        symbol=request.proposal.symbol,
        side=request.proposal.side.value,
        quantity=request.proposal.quantity,
        estimated_notional=request.proposal.estimated_notional,
        confidence=request.proposal.confidence,
        mode=request.proposal.mode.value,
        status="pending" if decision.approved else "rejected_by_risk",
        proposal=request.proposal.model_dump(mode="json"),
        risk_decision=decision.model_dump(mode="json"),
    )
    session.add(record)
    session.flush()
    return {
        "proposal": _proposal_payload(record),
        "risk_decision": decision.model_dump(mode="json"),
    }


@router.get("/proposals")
def list_proposals(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    query = select(TradeProposalRecord).order_by(TradeProposalRecord.created_at.desc()).limit(limit)
    if status:
        query = query.where(TradeProposalRecord.status == status)
    records = session.scalars(query).all()
    return {"proposals": [_proposal_payload(record) for record in records]}


@router.post("/proposals/{record_id}/approve")
def approve_proposal(
    record_id: int,
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    record = session.get(TradeProposalRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Proposal {record_id} not found")
    if record.status not in {"pending"}:
        raise HTTPException(status_code=409, detail=f"Proposal is {record.status}, not pending")
    settings = get_settings()
    record.reviewed_at = datetime.now(UTC)
    # Approval records intent only. The dedicated executor still requires all
    # paper switches, a fresh risk check, and the SIMULATE-only broker path.
    record.status = (
        "approved"
        if settings.futu_enable_trade and settings.paper_trading_enabled
        else "approved_no_execution"
    )
    return {"proposal": _proposal_payload(record)}


@router.post("/proposals/{record_id}/execute")
def execute_trade_proposal(
    record_id: int,
    session: Session = Depends(db_session_dependency),
    client: FutuClient = Depends(futu_client_dependency),
) -> dict[str, Any]:
    record = session.get(TradeProposalRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Proposal {record_id} not found")
    try:
        order = execute_proposal(session, client, record)
    except ExecutionBlocked as exc:
        raise HTTPException(status_code=423 if exc.halted else 403, detail=str(exc)) from exc
    except ExecutionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExecutionRejected as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ExecutionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"order": _order_payload(order), "proposal": _proposal_payload(record)}


@router.post("/proposals/{record_id}/reject")
def reject_proposal(
    record_id: int,
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    record = session.get(TradeProposalRecord, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Proposal {record_id} not found")
    if record.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Proposal is {record.status}, not pending",
        )
    record.status = "rejected"
    record.reviewed_at = datetime.now(UTC)
    return {"proposal": _proposal_payload(record)}


@orders_router.get("")
def list_orders(
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    records = session.scalars(
        select(BrokerOrder).order_by(BrokerOrder.created_at.desc()).limit(limit)
    ).all()
    return {"orders": [_order_payload(order) for order in records]}


@orders_router.get("/{order_id}")
def get_order(
    order_id: int,
    session: Session = Depends(db_session_dependency),
) -> dict[str, Any]:
    order = session.get(BrokerOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    return {"order": _order_payload(order)}
