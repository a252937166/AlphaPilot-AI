from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MarketRegime(StrEnum):
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    EVENT_SHOCK = "event_shock"


class AlertAction(StrEnum):
    WATCH = "WATCH"
    BUY_CANDIDATE = "BUY_CANDIDATE"
    ADD = "ADD"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    STOP = "STOP"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class AlertUrgency(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TradingMode(StrEnum):
    RESEARCH = "research"
    OBSERVE = "observe"
    ALERT = "alert"
    CONFIRM_TO_TRADE = "confirm_to_trade"
    PAPER_AUTO = "paper_auto"
    LIMITED_LIVE_AUTO = "limited_live_auto"


class TradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class HorizonForecast(BaseModel):
    horizon_days: int
    p_up: float = Field(ge=0, le=1)
    expected_return: float
    q10: float
    q50: float
    q90: float
    confidence: float = Field(ge=0, le=1)


class StockForecast(BaseModel):
    symbol: str
    as_of: datetime
    provider: str
    model_version: str
    data_points: int
    features: dict[str, float]
    horizons: dict[str, HorizonForecast]
    warnings: list[str] = Field(default_factory=list)


class ScreeningRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=500)
    top_n: int = Field(default=20, ge=1, le=100)
    provider: str | None = None
    lookback_days: int = Field(default=220, ge=80, le=1500)

    @model_validator(mode="after")
    def validate_top_n(self) -> ScreeningRequest:
        if self.top_n > len(self.symbols):
            self.top_n = len(self.symbols)
        return self


class ScreeningCandidate(BaseModel):
    rank: int
    symbol: str
    score: float = Field(ge=0, le=100)
    trend_score: float = Field(ge=0, le=100)
    risk_score: float = Field(ge=0, le=100)
    quality_placeholder_score: float = Field(ge=0, le=100)
    p_up_5d: float = Field(ge=0, le=1)
    p_up_20d: float = Field(ge=0, le=1)
    expected_return_20d: float
    confidence_20d: float = Field(ge=0, le=1)
    reasons: list[str]
    warnings: list[str] = Field(default_factory=list)


class ScreeningResponse(BaseModel):
    generated_at: datetime
    provider: str
    model_version: str
    requested: int
    succeeded: int
    failed: dict[str, str]
    candidates: list[ScreeningCandidate]


class StockAlert(BaseModel):
    symbol: str
    action: AlertAction
    urgency: AlertUrgency
    confidence: float = Field(ge=0, le=1)
    suggested_position_change: float = Field(ge=-1, le=1)
    reasons: list[str]
    invalidation: str
    expires_at: datetime
    model_version: str
    as_of: datetime


class RegimeResult(BaseModel):
    symbol: str
    regime: MarketRegime
    confidence: float = Field(ge=0, le=1)
    as_of: datetime
    features: dict[str, float]
    explanation: list[str]


class ScenarioRequest(BaseModel):
    title: str
    event_description: str
    event_direction: float = Field(ge=-1, le=1)
    affected_symbols: list[str] = Field(default_factory=list)
    affected_sectors: list[str] = Field(default_factory=list)
    horizon_days: int = Field(default=20, ge=1, le=250)
    runs: int = Field(default=200, ge=20, le=5000)
    seed: int = 42


class AgentScenarioView(BaseModel):
    agent_type: str
    belief_change: float
    trade_intent: str
    expected_return: float
    confidence: float = Field(ge=0, le=1)
    explanation: str


class ScenarioResponse(BaseModel):
    scenario_id: str
    engine: str
    generated_at: datetime
    p_positive: float = Field(ge=0, le=1)
    expected_impact: float
    q10: float
    q50: float
    q90: float
    dispersion: float
    agent_views: list[AgentScenarioView]
    assumptions: list[str]


class PortfolioState(BaseModel):
    equity: float = Field(gt=0)
    cash: float = Field(ge=0)
    daily_pnl_pct: float
    current_position_pct: float = Field(ge=0, le=1)
    sector_position_pct: float = Field(ge=0, le=1)
    open_orders_for_symbol: int = Field(default=0, ge=0)


class TradeProposal(BaseModel):
    proposal_id: str
    idempotency_key: str
    symbol: str
    side: TradeSide
    quantity: float = Field(gt=0)
    estimated_notional: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    market_data_as_of: datetime
    model_version: str
    mode: TradingMode
    source_alert_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskDecision(BaseModel):
    approved: bool
    reasons: list[str]
    evaluated_at: datetime
    requires_human_confirmation: bool
