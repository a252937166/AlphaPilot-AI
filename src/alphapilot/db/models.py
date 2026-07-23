from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON, list[Any]: JSON}


class Security(Base):
    """Security master row enriched from cninfo/providers."""

    __tablename__ = "securities"

    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    market: Mapped[str] = mapped_column(String(8), default="CN")
    name: Mapped[str | None] = mapped_column(String(64))
    org_id: Mapped[str | None] = mapped_column(String(32))
    industry: Mapped[str | None] = mapped_column(String(64))
    industry_csrc: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry_futu: Mapped[str | None] = mapped_column(String(64), nullable=True)
    board: Mapped[str | None] = mapped_column(String(32))
    is_st: Mapped[bool] = mapped_column(Boolean, default=False)
    list_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    float_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_ttm: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    style_tag: Mapped[str | None] = mapped_column(String(16), nullable=True)
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    listed_date: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str | None] = mapped_column(String(32))
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DailyBar(Base):
    """Cached daily OHLCV; source and ingested_at keep provenance auditable."""

    __tablename__ = "daily_bars"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", name="uq_daily_bars_symbol_date"),
        Index("ix_daily_bars_symbol_date", "symbol", "trade_date"),
        Index("ix_daily_bars_trade_date_symbol", "trade_date", "symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24))
    trade_date: Mapped[date] = mapped_column(Date)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(24))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdjFactor(Base):
    """Sourced daily adjustment factor used to reconstruct adjusted prices."""

    __tablename__ = "adj_factors"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", name="uq_adj"),
        Index("ix_adj_symbol_date", "symbol", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    adj_factor: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16), default="tushare")


class Disclosure(Base):
    """Company announcement pulled from cninfo."""

    __tablename__ = "disclosures"
    __table_args__ = (
        UniqueConstraint("symbol", "url", name="uq_disclosures_symbol_url"),
        Index("ix_disclosures_symbol_published", "symbol", "published_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(512))
    category: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(24), default="cninfo")
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WatchlistItem(Base):
    """Tracked stock plus its investment thesis."""

    __tablename__ = "watchlist_items"

    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    group_name: Mapped[str] = mapped_column(String(32), default="core")
    display_name: Mapped[str | None] = mapped_column(String(64))
    cost_price: Mapped[float | None] = mapped_column(Float)
    quantity: Mapped[float | None] = mapped_column(Float)
    thesis: Mapped[str | None] = mapped_column(Text)
    catalysts: Mapped[list[Any]] = mapped_column(JSON, default=list)
    risks: Mapped[list[Any]] = mapped_column(JSON, default=list)
    invalidation_rules: Mapped[list[Any]] = mapped_column(JSON, default=list)
    initial_confidence: Mapped[float | None] = mapped_column(Float)
    thesis_state: Mapped[str] = mapped_column(String(24), default="unchanged")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ForecastSnapshot(Base):
    """History of model outputs; the review page scores them against reality."""

    __tablename__ = "forecast_snapshots"
    __table_args__ = (Index("ix_forecasts_symbol_asof", "symbol", "as_of"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(24))
    model_version: Mapped[str] = mapped_column(String(64))
    horizons: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ThesisTransition(Base):
    """Auditable watchlist thesis-state change tied to one evidence snapshot."""

    __tablename__ = "thesis_transitions"
    __table_args__ = (
        UniqueConstraint("trigger_ref", name="uq_thesis_transition_ref"),
        Index("ix_thesis_transition_symbol_created", "symbol", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    from_state: Mapped[str] = mapped_column(String(16))
    to_state: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    trigger_ref: Mapped[str] = mapped_column(String(128))
    model_version: Mapped[str] = mapped_column(String(32), default="thesis-drift-v1.0.0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class AlertRecord(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24))
    action: Mapped[str] = mapped_column(String(24))
    urgency: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    suggested_position_change: Mapped[float] = mapped_column(Float, default=0.0)
    target_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_notional: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    invalidation: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(String(64))
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertOutcome(Base):
    """Auditable five-session result for one persisted stock alert."""

    __tablename__ = "alert_outcomes"
    __table_args__ = (
        CheckConstraint("horizon_days > 0", name="ck_alert_outcome_horizon"),
        CheckConstraint(
            "maturity_date > origin_date",
            name="ck_alert_outcome_date_order",
        ),
        CheckConstraint(
            "realized_return IS NULL OR realized_return > -1",
            name="ck_alert_outcome_realized_return",
        ),
    )

    alert_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("alerts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    horizon_days: Mapped[int] = mapped_column(Integer, default=5)
    origin_date: Mapped[date] = mapped_column(Date)
    maturity_date: Mapped[date] = mapped_column(Date)
    realized_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    contribution: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str] = mapped_column(
        String(32),
        default="signal-attribution-v1.0.0",
    )
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        index=True,
    )


class ScreeningRun(Base):
    __tablename__ = "screening_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    universe: Mapped[str] = mapped_column(String(24), default="custom")
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provider: Mapped[str] = mapped_column(String(24))
    model_version: Mapped[str] = mapped_column(String(64))
    requested: Mapped[int] = mapped_column(Integer)
    succeeded: Mapped[int] = mapped_column(Integer)
    failed: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    candidates: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TradeProposalRecord(Base):
    """Proposal + risk decision + review state for the execution workflow."""

    __tablename__ = "trade_proposals"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_trade_proposals_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[str] = mapped_column(String(64), unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    symbol: Mapped[str] = mapped_column(String(24))
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[float] = mapped_column(Float)
    estimated_notional: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    proposal: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_decision: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_alert_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("alerts.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BrokerOrder(Base):
    """One auditable SIMULATE broker order for an approved trade proposal."""

    __tablename__ = "broker_orders"
    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_broker_orders_proposal"),
        UniqueConstraint("futu_order_id", name="uq_broker_orders_futu_order"),
        CheckConstraint(
            "environment = 'SIMULATE'",
            name="ck_broker_orders_environment",
        ),
        CheckConstraint(
            "status IN ('submitting', 'submitted', 'filled', 'partial', 'cancelled', 'failed')",
            name="ck_broker_orders_status",
        ),
        CheckConstraint("qty > 0", name="ck_broker_orders_qty"),
        CheckConstraint(
            "price IS NULL OR price > 0",
            name="ck_broker_orders_price",
        ),
        CheckConstraint(
            "filled_qty >= 0 AND filled_qty <= qty",
            name="ck_broker_orders_filled_qty",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("trade_proposals.proposal_id"),
        index=True,
    )
    futu_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    symbol: Mapped[str] = mapped_column(String(24))
    side: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(16), default="MARKET")
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    qty: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default="submitting")
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    environment: Mapped[str] = mapped_column(String(12), default="SIMULATE")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class PortfolioSnapshot(Base):
    """One end-of-day valuation snapshot of the Futu SIMULATE portfolio."""

    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        CheckConstraint("total_value > 0", name="ck_portfolio_snapshot_total_value"),
        CheckConstraint("cash >= 0", name="ck_portfolio_snapshot_cash"),
        CheckConstraint(
            "daily_return IS NULL OR daily_return > -1",
            name="ck_portfolio_snapshot_daily_return",
        ),
        CheckConstraint(
            "benchmark_return IS NULL OR benchmark_return > -1",
            name="ck_portfolio_snapshot_benchmark_return",
        ),
        CheckConstraint(
            "drawdown IS NULL OR (drawdown >= -1 AND drawdown <= 0)",
            name="ck_portfolio_snapshot_drawdown",
        ),
        CheckConstraint("source = 'futu-sim'", name="ck_portfolio_snapshot_source"),
    )

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    total_value: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    positions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    daily_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    excess_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="futu-sim")


class SectorSnapshot(Base):
    """Sampled sector strength snapshot computed from constituent quotes."""

    __tablename__ = "sector_snapshots"
    __table_args__ = (Index("ix_sector_snapshots_asof", "as_of"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[list[Any]] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(24), default="futu")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketSnapshotAgg(Base):
    """Minute-level full-market breadth and liquidity aggregate."""

    __tablename__ = "market_snapshot_agg"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    advancers: Mapped[int] = mapped_column(Integer)
    decliners: Mapped[int] = mapped_column(Integer)
    unchanged: Mapped[int] = mapped_column(Integer)
    limit_up: Mapped[int] = mapped_column(Integer)
    limit_down: Mapped[int] = mapped_column(Integer)
    broken_boards: Mapped[int] = mapped_column(Integer)
    up_gt4: Mapped[int] = mapped_column(Integer)
    down_gt4: Mapped[int] = mapped_column(Integer)
    total_amount: Mapped[float] = mapped_column(Float)
    avg_change_pct: Mapped[float] = mapped_column(Float)
    median_change_pct: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16), default="futu")


class MarketSentiment(Base):
    """Auditable intraday sentiment derived from one full-market snapshot."""

    __tablename__ = "market_sentiment"
    __table_args__ = (
        UniqueConstraint("source_snapshot_id", name="uq_market_sentiment_snapshot"),
        Index("ix_market_sentiment_ts", "ts"),
        CheckConstraint("score >= 0 AND score <= 100", name="ck_market_sentiment_score"),
        CheckConstraint(
            "breadth_sub >= 0 AND breadth_sub <= 100",
            name="ck_market_sentiment_breadth",
        ),
        CheckConstraint(
            "limitup_sub >= 0 AND limitup_sub <= 100",
            name="ck_market_sentiment_limitup",
        ),
        CheckConstraint(
            "volume_sub >= 0 AND volume_sub <= 100",
            name="ck_market_sentiment_volume",
        ),
        CheckConstraint(
            "volatility_sub >= 0 AND volatility_sub <= 100",
            name="ck_market_sentiment_volatility",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("market_snapshot_agg.id"),
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    score: Mapped[float] = mapped_column(Float)
    breadth_sub: Mapped[float] = mapped_column(Float)
    limitup_sub: Mapped[float] = mapped_column(Float)
    volume_sub: Mapped[float] = mapped_column(Float)
    volatility_sub: Mapped[float] = mapped_column(Float)
    label: Mapped[str] = mapped_column(String(16))
    model_version: Mapped[str] = mapped_column(String(32))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SectorConstituent(Base):
    """Persisted Futu industry-plate membership, refreshed weekly."""

    __tablename__ = "sector_constituents"
    __table_args__ = (UniqueConstraint("plate_code", "symbol", name="uq_sector_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plate_code: Mapped[str] = mapped_column(String(24), index=True)
    plate_name: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SectorFlowDaily(Base):
    """Daily plate-level fund-flow aggregate with explicit source provenance."""

    __tablename__ = "sector_flow_daily"
    __table_args__ = (UniqueConstraint("plate_code", "trade_date", name="uq_sector_flow"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plate_code: Mapped[str] = mapped_column(String(24), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    net_inflow: Mapped[float | None] = mapped_column(Float, nullable=True)
    main_inflow: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(16))


class SectorForecast(Base):
    """Daily cross-sectional sector forecast with real rolling outcomes."""

    __tablename__ = "sector_forecasts"
    __table_args__ = (
        UniqueConstraint("plate_code", "trade_date", "horizon", name="uq_sector_fc"),
        Index("ix_sector_fc_date_horizon", "trade_date", "horizon"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plate_code: Mapped[str] = mapped_column(String(24), index=True)
    plate_name: Mapped[str] = mapped_column(String(64))
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    horizon: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    expected_excess: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    lifecycle: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rsi14: Mapped[float | None] = mapped_column(Float, nullable=True)
    reversal_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str] = mapped_column(String(32), default="sector-fc-v1.0.0")


class CalendarEvent(Base):
    """Point-in-time stock event used by the product calendar and future backtests."""

    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "event_type",
            "event_date",
            "title",
            name="uq_calendar",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    event_type: Mapped[str] = mapped_column(String(24))
    event_date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(24))
    available_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DomainEvent(Base):
    """Normalized event stream consumed by monitoring, insights, and review flows."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("source_ref", name="uq_events_source_ref"),
        Index("ix_events_symbol_occurred", "symbol", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str | None] = mapped_column(String(24), index=True, nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[float] = mapped_column(Float, default=0.0)
    strength: Mapped[float] = mapped_column(Float, default=0.5)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MarketRegimeState(Base):
    """Last observed benchmark regime used to detect actual state transitions."""

    __tablename__ = "market_regime_states"

    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    regime: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FinancialIndicator(Base):
    """Quarterly fundamental metric with point-in-time availability provenance."""

    __tablename__ = "financial_indicators"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "report_period",
            "metric",
            name="uq_fin_metric",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    report_period: Mapped[str] = mapped_column(String(10))
    metric: Mapped[str] = mapped_column(String(32))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="baostock")
    available_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class FactorValue(Base):
    """Point-in-time cross-sectional factor observation for one security."""

    __tablename__ = "factor_values"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", "factor", name="uq_factor"),
        Index("ix_factor_date", "factor", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    factor: Mapped[str] = mapped_column(String(32))
    raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    zscore: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str] = mapped_column(String(32), default="factor-v1.0.0")


class CompositeScore(Base):
    """Daily 0-100 stock score with its auditable factor inputs."""

    __tablename__ = "composite_scores"
    __table_args__ = (UniqueConstraint("symbol", "trade_date", name="uq_comp_score"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    score: Mapped[float] = mapped_column(Float)
    win_rate_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    factors: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model_version: Mapped[str] = mapped_column(String(32))


class ScoreOutcomeStat(Base):
    """Calibrated 20-session hit rate for one composite-score decile."""

    __tablename__ = "score_outcome_stats"
    __table_args__ = (
        UniqueConstraint(
            "score_model_version",
            "decile",
            "horizon",
            name="uq_sos",
        ),
        CheckConstraint("decile >= 1 AND decile <= 10", name="ck_sos_decile"),
        CheckConstraint("horizon > 0", name="ck_sos_horizon"),
        CheckConstraint("samples >= 0", name="ck_sos_samples"),
        CheckConstraint(
            "positive_samples >= 0 AND positive_samples <= samples",
            name="ck_sos_positive_samples",
        ),
        CheckConstraint(
            "win_rate IS NULL OR (win_rate >= 0 AND win_rate <= 1)",
            name="ck_sos_rate",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decile: Mapped[int] = mapped_column(Integer)
    horizon: Mapped[int] = mapped_column(Integer, default=20)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    positive_samples: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_model_version: Mapped[str] = mapped_column(String(32))
    model_version: Mapped[str] = mapped_column(String(32), default="score-outcome-v1.0.0")
    as_of_date: Mapped[date] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StockScore(Base):
    """Daily five-dimension stock score derived from PIT factor z-scores."""

    __tablename__ = "stock_scores"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", name="uq_stock_score"),
        Index("ix_stock_score_date", "trade_date"),
        CheckConstraint("tech >= 0 AND tech <= 10", name="ck_stock_score_tech"),
        CheckConstraint("capital >= 0 AND capital <= 10", name="ck_stock_score_capital"),
        CheckConstraint(
            "fundamental >= 0 AND fundamental <= 10",
            name="ck_stock_score_fundamental",
        ),
        CheckConstraint(
            "valuation >= 0 AND valuation <= 10",
            name="ck_stock_score_valuation",
        ),
        CheckConstraint(
            "sentiment >= 0 AND sentiment <= 10",
            name="ck_stock_score_sentiment",
        ),
        CheckConstraint(
            "composite >= 0 AND composite <= 10",
            name="ck_stock_score_composite",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    tech: Mapped[float] = mapped_column(Float)
    capital: Mapped[float] = mapped_column(Float)
    fundamental: Mapped[float] = mapped_column(Float)
    valuation: Mapped[float] = mapped_column(Float)
    sentiment: Mapped[float] = mapped_column(Float)
    composite: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(32))


class StockInsight(Base):
    """Cached, evidence-grounded stock interpretation for the product UI."""

    __tablename__ = "stock_insights"
    __table_args__ = (
        CheckConstraint("source IN ('rule', 'llm')", name="ck_stock_insight_source"),
    )

    symbol: Mapped[str] = mapped_column(String(24), primary_key=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    core_view: Mapped[str] = mapped_column(Text)
    drivers: Mapped[list[Any]] = mapped_column(JSON, default=list)
    model_version: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))


class StyleDaily(Base):
    """Turnover-weighted daily market style distribution."""

    __tablename__ = "style_daily"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    growth_pct: Mapped[float] = mapped_column(Float)
    value_pct: Mapped[float] = mapped_column(Float)
    defensive_pct: Mapped[float] = mapped_column(Float)
    balanced_pct: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(32), default="style-v1.0.0")
    source_fingerprint: Mapped[str] = mapped_column(String(64), default="")


class Notification(Base):
    """One idempotent user-facing notice for a persisted domain artifact."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("kind", "ref_id", name="uq_notifications_kind_ref"),
        CheckConstraint(
            "kind IN ('alert', 'event', 'job', 'system')",
            name="ck_notifications_kind",
        ),
        CheckConstraint(
            "level IN ('info', 'warn', 'error')",
            name="ck_notifications_level",
        ),
        Index("ix_notifications_read_created", "read_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))
    ref_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(16), default="info")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DailyReport(Base):
    __tablename__ = "daily_reports"

    report_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True, default="post_market")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobRun(Base):
    """Auditable execution record for every scheduled or manually triggered job."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class LLMCall(Base):
    """One audit row for each logical LLM request, including failed requests."""

    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint("latency_ms >= 0", name="ck_llm_calls_latency_nonnegative"),
        CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name="ck_llm_calls_prompt_tokens_nonnegative",
        ),
        CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="ck_llm_calls_completion_tokens_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    purpose: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(128))
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RuntimeFlag(Base):
    """A persisted operator safety switch that survives API restarts."""

    __tablename__ = "runtime_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
    )


class BacktestRun(Base):
    """One reproducible point-in-time backtest configuration and outcome."""

    __tablename__ = "backtest_runs"
    __table_args__ = (
        CheckConstraint("top_pct > 0 AND top_pct <= 1", name="ck_bt_run_top_pct"),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_bt_run_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    signal_id: Mapped[str] = mapped_column(String(64), default="composite-v1")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    rebalance_freq: Mapped[str] = mapped_column(String(8), default="5d")
    top_pct: Mapped[float] = mapped_column(Float, default=0.1)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="running")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BacktestDaily(Base):
    """Daily strategy, benchmark, cross-sectional, and turnover observations."""

    __tablename__ = "backtest_daily"
    __table_args__ = (
        UniqueConstraint("run_id", "trade_date", name="uq_bt_daily_run_date"),
        Index("ix_bt_daily", "run_id", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"))
    trade_date: Mapped[date] = mapped_column(Date)
    rank_ic: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_ret: Mapped[float | None] = mapped_column(Float, nullable=True)
    ls_ret: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    nav: Mapped[float] = mapped_column(Float)
    benchmark_nav: Mapped[float] = mapped_column(Float)
    market_nav: Mapped[float] = mapped_column(Float)
    n_eligible: Mapped[int] = mapped_column(Integer)
    group_returns: Mapped[list[Any]] = mapped_column(JSON, default=list)
