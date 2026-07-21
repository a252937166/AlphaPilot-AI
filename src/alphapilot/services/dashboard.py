from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.core.timeutil import iso_utc
from alphapilot.data.base import DataProviderError, MarketDataProvider
from alphapilot.db.models import AlertRecord
from alphapilot.futu.client import FutuClient
from alphapilot.prediction.regime import MarketRegimeClassifier
from alphapilot.services import market_data
from alphapilot.services.ai_text import compose_market_summary
from alphapilot.services.sectors import (
    SectorServiceError,
    get_sector_strength,
    market_breadth_from_sample,
)
from alphapilot.services.watchlist import tracked_overview


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
