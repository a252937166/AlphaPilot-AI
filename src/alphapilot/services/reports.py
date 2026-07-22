from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.core.timeutil import iso_utc
from alphapilot.data.base import MarketDataProvider
from alphapilot.db.models import AlertRecord, DailyReport, ForecastSnapshot
from alphapilot.services import market_data
from alphapilot.services.ai_text import compose_market_summary
from alphapilot.services.alert_outcomes import build_signal_attribution
from alphapilot.services.disclosures import list_disclosures
from alphapilot.services.watchlist import list_items, tracked_overview

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _forecast_hit_stats(session: Session, symbol_bars: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Score persisted 1d forecasts against realized next-day returns."""
    snapshots = session.scalars(
        select(ForecastSnapshot).order_by(ForecastSnapshot.as_of.desc()).limit(400)
    ).all()
    evaluated = 0
    hits = 0
    samples: list[dict[str, Any]] = []
    for snapshot in snapshots:
        bars = symbol_bars.get(snapshot.symbol)
        if bars is None or bars.empty:
            continue
        horizon = (snapshot.horizons or {}).get("1d") or {}
        p_up = horizon.get("p_up")
        if p_up is None:
            continue
        as_of_date = snapshot.as_of.date()
        dates = [pd.Timestamp(value).date() for value in bars["date"]]
        future_positions = [index for index, value in enumerate(dates) if value > as_of_date]
        if not future_positions:
            continue
        next_index = future_positions[0]
        base_index = next_index - 1
        if base_index < 0:
            continue
        realized = float(bars.iloc[next_index]["close"]) / float(
            bars.iloc[base_index]["close"]
        ) - 1
        predicted_up = float(p_up) >= 0.5
        hit = (realized >= 0) == predicted_up
        evaluated += 1
        hits += int(hit)
        if len(samples) < 20:
            samples.append(
                {
                    "symbol": snapshot.symbol,
                    "as_of": snapshot.as_of.isoformat(),
                    "p_up_1d": round(float(p_up), 3),
                    "realized_return_1d": round(realized, 4),
                    "hit": hit,
                }
            )
    return {
        "evaluated": evaluated,
        "hits": hits,
        "hit_rate": round(hits / evaluated, 4) if evaluated else None,
        "samples": samples,
    }


def generate_daily_report(
    session: Session,
    settings: Settings,
    provider: MarketDataProvider,
    *,
    report_date: date | None = None,
) -> dict[str, Any]:
    target_date = report_date or date.today()
    indices = market_data.index_quotes(settings)
    watchlist_rows = tracked_overview(session, provider)

    lookback_start = target_date - timedelta(days=30)
    symbol_bars: dict[str, pd.DataFrame] = {}
    for item in list_items(session):
        frame = market_data.load_bars(session, item.symbol, lookback_start, target_date)
        if not frame.empty:
            symbol_bars[item.symbol] = frame
    hit_stats = _forecast_hit_stats(session, symbol_bars)

    gainers = sorted(
        [row for row in watchlist_rows if row.get("change_pct") is not None],
        key=lambda row: float(row["change_pct"]),
        reverse=True,
    )
    day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=MARKET_TIMEZONE)
    day_end = day_start + timedelta(days=1)
    todays_alerts = session.scalars(
        select(AlertRecord)
        .where(
            AlertRecord.created_at >= day_start.astimezone(UTC),
            AlertRecord.created_at < day_end.astimezone(UTC),
        )
        .order_by(AlertRecord.created_at.desc())
        .limit(30)
    ).all()
    disclosures = list_disclosures(session, None, limit=15)
    signal_attribution = build_signal_attribution(session, target_date)

    summary_context = {
        "indices": indices,
        "watchlist_top": gainers[:5],
        "hit_stats": {key: hit_stats[key] for key in ("evaluated", "hit_rate")},
        "alert_count": len(todays_alerts),
    }
    ai_summary = compose_market_summary(settings, summary_context)

    payload: dict[str, Any] = {
        "report_date": target_date.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "indices": indices,
        "watchlist": watchlist_rows,
        "watchlist_gainers": gainers[:5],
        "watchlist_losers": gainers[-5:][::-1] if gainers else [],
        "forecast_hit_stats": hit_stats,
        "signal_attribution": signal_attribution,
        "alerts": [
            {
                "id": record.id,
                "symbol": record.symbol,
                "action": record.action,
                "urgency": record.urgency,
                "confidence": record.confidence,
                "reasons": record.reasons,
                "created_at": iso_utc(record.created_at),
            }
            for record in todays_alerts
        ],
        "disclosures": disclosures,
        "ai_summary": ai_summary,
        "tomorrow_focus": [
            {
                "symbol": row["symbol"],
                "display_name": row.get("display_name"),
                "reason": f"最新信号 {row.get('alert_action') or 'HOLD'}，"
                f"20日上涨概率 {row.get('p_up_20d')}",
            }
            for row in watchlist_rows
            if row.get("alert_action") in {"BUY_CANDIDATE", "REDUCE", "EXIT", "REVIEW_REQUIRED"}
        ][:6],
        "disclaimer": "本报告由规则引擎自动生成，仅用于工程验证，不构成投资建议。",
    }

    existing = session.get(
        DailyReport, {"report_date": target_date.isoformat(), "kind": "post_market"}
    )
    if existing is None:
        session.add(
            DailyReport(
                report_date=target_date.isoformat(), kind="post_market", payload=payload
            )
        )
    else:
        existing.payload = payload
        existing.generated_at = datetime.now(UTC)
    return payload


def get_daily_report(
    session: Session,
    *,
    report_date: date | None = None,
) -> dict[str, Any] | None:
    target = (report_date or date.today()).isoformat()
    row = session.get(DailyReport, {"report_date": target, "kind": "post_market"})
    if row is None:
        return None
    return dict(row.payload)
