from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

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


StyleTag = Literal["growth", "value", "defensive", "balanced"]


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
    universe: Literal["all", "watchlist", "custom"] = "all"
    symbols: list[str] | None = Field(default=None, max_length=500)
    industries: list[str] | None = Field(default=None, max_length=100)
    style: StyleTag | None = None
    risk_level: Literal["low", "mid", "high"] | None = None
    min_market_cap: float | None = Field(default=None, ge=0)
    top_n: int = Field(default=50, ge=1, le=100)
    sort_by: Literal["score", "expected_return", "win_rate"] = "score"
    horizon_days: Literal[5, 20] = 20
    provider: str | None = None
    lookback_days: int = Field(default=220, ge=80, le=1500)

    @model_validator(mode="before")
    @classmethod
    def preserve_legacy_symbols_mode(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        inferred = dict(value)
        if "universe" not in inferred and "symbols" in inferred:
            inferred["universe"] = "custom"
        if inferred.get("universe") == "custom" and "top_n" not in inferred:
            inferred["top_n"] = 20
        return inferred

    def custom_filter_error(self) -> str | None:
        if self.universe != "custom":
            return None
        if self.style is not None:
            return "custom 兼容模式不支持 style；请改用 all 或 watchlist 股票池。"
        unsupported: list[str] = []
        if self.industries is not None:
            unsupported.append("industries")
        if self.risk_level is not None:
            unsupported.append("risk_level")
        if self.min_market_cap is not None:
            unsupported.append("min_market_cap")
        if self.sort_by != "score":
            unsupported.append("sort_by")
        if unsupported:
            fields = "、".join(unsupported)
            return f"custom 兼容模式不支持 {fields}；请改用 all 或 watchlist 股票池。"
        return None

    @model_validator(mode="after")
    def validate_top_n(self) -> ScreeningRequest:
        if self.universe == "custom":
            if not self.symbols:
                raise ValueError("自定义股票池至少需要一个股票代码。")
            if self.top_n > len(self.symbols):
                self.top_n = len(self.symbols)
        return self


class ScreeningCandidate(BaseModel):
    rank: int
    symbol: str
    score: float = Field(ge=0, le=100)
    trend_score: float | None = Field(default=None, ge=0, le=100)
    risk_score: float | None = Field(default=None, ge=0, le=100)
    quality_placeholder_score: float | None = Field(default=None, ge=0, le=100)
    # Display-only disclosure fields: they annotate a candidate and never
    # filter or reorder the shortlist (P4.3 owns the actual gates).
    avg_amount_20d: float | None = Field(default=None, ge=0)
    low_liquidity: bool | None = None
    high_volatility: bool | None = None
    p_up_5d: float | None = Field(default=None, ge=0, le=1)
    p_up_20d: float | None = Field(default=None, ge=0, le=1)
    expected_return_5d: float | None = None
    expected_return_20d: float | None = None
    confidence_5d: float | None = Field(default=None, ge=0, le=1)
    confidence_20d: float | None = Field(default=None, ge=0, le=1)
    display_name: str | None = None
    industry: str | None = None
    style: StyleTag | None = None
    risk_level: Literal["low", "mid", "high"] | None = None
    market_cap: float | None = None
    trade_date: date | None = None
    win_rate_20d: float | None = Field(default=None, ge=0, le=1)
    forecast_source: str | None = None
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


class PersistedScreeningResponse(ScreeningResponse):
    run_id: int = Field(ge=1)


class StyleDailyPoint(BaseModel):
    trade_date: date
    growth_pct: float = Field(ge=0, le=1)
    value_pct: float = Field(ge=0, le=1)
    defensive_pct: float = Field(ge=0, le=1)
    balanced_pct: float = Field(ge=0, le=1)
    model_version: str


class StyleDailyResponse(BaseModel):
    requested_days: int = Field(ge=1)
    available_days: int = Field(ge=0)
    series: list[StyleDailyPoint]


class StyleExposureSlice(BaseModel):
    style: StyleTag
    count: int = Field(ge=0)
    pct: float = Field(ge=0, le=1)


class StyleExposureResponse(BaseModel):
    run_id: int = Field(ge=1)
    total_candidates: int = Field(ge=0)
    exposure: list[StyleExposureSlice]


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
    source_alert_id: int | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskDecision(BaseModel):
    approved: bool
    reasons: list[str]
    evaluated_at: datetime
    requires_human_confirmation: bool
