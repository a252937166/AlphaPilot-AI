from fastapi import APIRouter
from pydantic import BaseModel

from alphapilot.core.config import get_settings
from alphapilot.domain.models import PortfolioState, RiskDecision, TradeProposal
from alphapilot.risk.guardrails import TradeGuardrails

router = APIRouter(prefix="/v1/trades", tags=["trading-risk"])


class TradeEvaluationRequest(BaseModel):
    proposal: TradeProposal
    portfolio: PortfolioState


@router.post("/evaluate", response_model=RiskDecision)
def evaluate_trade(request: TradeEvaluationRequest) -> RiskDecision:
    return TradeGuardrails(get_settings()).evaluate(request.proposal, request.portfolio)
