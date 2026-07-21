from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
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


class AlertRecord(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(24))
    action: Mapped[str] = mapped_column(String(24))
    urgency: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    suggested_position_change: Mapped[float] = mapped_column(Float, default=0.0)
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    invalidation: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(String(64))
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScreeningRun(Base):
    __tablename__ = "screening_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[str] = mapped_column(String(64), unique=True)
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
