from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.core.timeutil import iso_utc
from alphapilot.data.base import DataProviderError, MarketDataProvider
from alphapilot.db.models import AlertRecord, DomainEvent, MarketRegimeState
from alphapilot.futu.client import FutuClient
from alphapilot.prediction.regime import MarketRegimeClassifier
from alphapilot.services import market_data
from alphapilot.services.ai_text import compose_market_summary
from alphapilot.services.events import emit
from alphapilot.services.sectors import (
    SectorServiceError,
    get_sector_strength,
    market_breadth_from_sample,
)
from alphapilot.services.watchlist import tracked_overview

REGIME_DIRECTION = {
    "trend_up": 1.0,
    "risk_on": 0.6,
    "range": 0.0,
    "event_shock": -0.4,
    "risk_off": -0.6,
    "trend_down": -1.0,
}


def _regime_as_of(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
    else:
        return datetime.now(UTC)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def track_market_regime_change(
    session: Session,
    payload: dict[str, Any],
    benchmark: str = "SH.000001",
) -> DomainEvent | None:
    """Persist the latest regime and emit only genuine state transitions."""

    current = str(payload.get("regime") or "").strip()
    if not current:
        return None
    confidence = max(0.0, min(1.0, float(payload.get("confidence") or 0.0)))
    as_of = _regime_as_of(payload.get("as_of"))
    now = datetime.now(UTC)
    state = session.get(MarketRegimeState, benchmark)
    event: DomainEvent | None = None
    if state is None:
        state = MarketRegimeState(
            symbol=benchmark,
            regime=current,
            confidence=confidence,
            as_of=as_of,
            updated_at=now,
        )
        session.add(state)
    else:
        previous = state.regime
        if previous != current:
            event = emit(
                session,
                symbol=None,
                event_type="market_regime_change",
                title=f"市场状态由 {previous} 切换为 {current}",
                direction=REGIME_DIRECTION.get(current, 0.0),
                strength=confidence,
                summary=f"基准 {benchmark}，模型置信度 {confidence:.0%}",
                source_ref=(
                    f"regime:{benchmark}:{as_of.date().isoformat()}:{previous}->{current}"
                ),
                occurred_at=now,
            )
        state.regime = current
        state.confidence = confidence
        state.as_of = as_of
        state.updated_at = now
    session.flush()
    return event


def market_regime_for_benchmark(
    session: Session, settings: Settings, benchmark: str = "SH.000001"
) -> dict[str, Any] | None:
    provider = market_data.build_index_provider(settings)
    end = date.today()
    start = end - timedelta(days=440)
    try:
        result = market_data.get_bars_with_cache(session, provider, benchmark, start, end)
        regime = MarketRegimeClassifier().classify(benchmark, result["frame"])
        payload = regime.model_dump(mode="json")
        payload["source"] = result["source"]
        return payload
    except (DataProviderError, ValueError):
        return None


def recent_alerts(session: Session, limit: int = 10) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(AlertRecord).order_by(AlertRecord.created_at.desc()).limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "symbol": row.symbol,
            "action": row.action,
            "urgency": row.urgency,
            "confidence": row.confidence,
            "reasons": row.reasons,
            "acknowledged": row.acknowledged,
            "created_at": iso_utc(row.created_at),
        }
        for row in rows
    ]


def overview(
    session: Session,
    settings: Settings,
    provider: MarketDataProvider,
    futu_client: FutuClient,
) -> dict[str, Any]:
    indices = market_data.index_quotes(settings)
    regime = market_regime_for_benchmark(session, settings)
    if regime is not None:
        track_market_regime_change(session, regime)

    sectors: list[dict[str, Any]] = []
    sector_error: str | None = None
    try:
        sector_result = get_sector_strength(session, futu_client)
        sectors = list(sector_result.get("sectors") or [])
    except SectorServiceError as exc:
        sector_error = str(exc)

    breadth: dict[str, Any] | None = None
    try:
        breadth = market_breadth_from_sample(futu_client)
    except Exception:
        breadth = None

    watchlist = tracked_overview(session, provider)
    alerts = recent_alerts(session)

    ai_summary = compose_market_summary(
        settings,
        {
            "regime": regime,
            "indices": indices,
            "sectors": sectors[:5],
            "breadth": breadth,
            "watchlist_count": len(watchlist),
        },
        session,
    )

    return {
        "as_of": datetime.now(UTC).isoformat(),
        "regime": regime,
        "indices": indices,
        "sectors": sectors[:8],
        "sector_error": sector_error,
        "breadth": breadth,
        "watchlist": watchlist,
        "alerts": alerts,
        "ai_summary": ai_summary,
    }
