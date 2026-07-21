from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from alphapilot.cninfo.client import CninfoClient, get_cninfo_client
from alphapilot.data.baostock_provider import BaoStockMarketDataProvider
from alphapilot.data.base import DataProviderError
from alphapilot.db.engine import get_session
from alphapilot.db.models import CalendarEvent, WatchlistItem
from alphapilot.jobs.registry import JobSpec, register
from alphapilot.services.watchlist import normalize_symbol

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
CALENDAR_LOOKBACK_DAYS = 730


class EventCandidate(TypedDict):
    symbol: str
    event_type: str
    event_date: date
    title: str
    payload: dict[str, Any]
    source: str
    available_time: datetime


def _parse_date(value: object) -> date | None:
    if value is None or str(value).strip() in {"", "NaT", "None", "nan"}:
        return None
    try:
        return pd.Timestamp(str(value)).date()
    except (TypeError, ValueError):
        return None


def _available_at(value: date | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    return datetime.combine(value, time.min, tzinfo=MARKET_TIMEZONE).astimezone(UTC)


def _utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(UTC)


def _json_scalar(value: object) -> Any:
    if value is None:
        return None
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        try:
            return _json_scalar(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


def _record_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _json_scalar(value) for key, value in record.items()}


def _dividend_events(
    provider: BaoStockMarketDataProvider,
    symbol: str,
    year: int,
    now: datetime,
) -> list[EventCandidate]:
    frame = provider.get_dividend_data(symbol, year)
    events: list[EventCandidate] = []
    for raw in frame.to_dict(orient="records"):
        record = {str(key): value for key, value in raw.items()}
        event_date = next(
            (
                parsed
                for field in (
                    "dividPayDate",
                    "dividStockMarketDate",
                    "dividRegistDate",
                    "dividOperateDate",
                    "dividPlanDate",
                    "dividPlanAnnounceDate",
                )
                if (parsed := _parse_date(record.get(field))) is not None
            ),
            None,
        )
        if event_date is None:
            continue
        plan = str(record.get("dividCashStock") or "").strip()
        title = f"分红：{plan}" if plan else f"{year} 年度分红实施"
        announced = _parse_date(record.get("dividPlanAnnounceDate"))
        payload = _record_payload(record)
        payload["report_year"] = year
        payload["event_date_basis"] = "dividPayDate-first"
        events.append(
            {
                "symbol": symbol,
                "event_type": "dividend",
                "event_date": event_date,
                "title": title,
                "payload": payload,
                "source": "baostock",
                "available_time": _available_at(announced, now),
            }
        )
    return events


def _forecast_events(
    provider: BaoStockMarketDataProvider,
    symbol: str,
    start: date,
    end: date,
    now: datetime,
) -> list[EventCandidate]:
    frame = provider.get_forecast_reports(symbol, start, end)
    events: list[EventCandidate] = []
    for raw in frame.to_dict(orient="records"):
        record = {str(key): value for key, value in raw.items()}
        published = _parse_date(record.get("profitForcastExpPubDate"))
        if published is None:
            continue
        stat_date = _parse_date(record.get("profitForcastExpStatDate"))
        forecast_type = str(record.get("profitForcastType") or "业绩预告").strip()
        suffix = f"（报告期 {stat_date.isoformat()}）" if stat_date else ""
        events.append(
            {
                "symbol": symbol,
                "event_type": "earnings_preview",
                "event_date": published,
                "title": f"业绩预告：{forecast_type}{suffix}",
                "payload": _record_payload(record),
                "source": "baostock",
                "available_time": _available_at(published, now),
            }
        )
    return events


def _unlock_events(symbol: str, now: datetime) -> list[EventCandidate]:
    """Bounded equivalent of AKShare stock_restricted_release_queue_em(symbol)."""

    response = httpx.get(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        params={
            "sortColumns": "FREE_DATE",
            "sortTypes": "-1",
            "pageSize": "500",
            "pageNumber": "1",
            "reportName": "RPT_LIFT_STAGE",
            "filter": f'(SECURITY_CODE="{symbol}")',
            "columns": (
                "SECURITY_CODE,SECURITY_NAME_ABBR,FREE_DATE,CURRENT_FREE_SHARES,"
                "ABLE_FREE_SHARES,LIFT_MARKET_CAP,FREE_RATIO,NEW,B20_ADJCHRATE,"
                "A20_ADJCHRATE,FREE_SHARES_TYPE,TOTAL_RATIO,NON_FREE_SHARES,"
                "BATCH_HOLDER_NUM"
            ),
            "source": "WEB",
            "client": "WEB",
        },
        headers={"User-Agent": "Mozilla/5.0 (compatible; AlphaPilot-AI/0.2)"},
        timeout=5.0,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise DataProviderError("Eastmoney unlock payload is invalid")
    result = payload.get("result")
    if not result:
        return []
    if not isinstance(result, Mapping) or not isinstance(result.get("data"), list):
        raise DataProviderError("Eastmoney unlock response has invalid result data")

    events: list[EventCandidate] = []
    for item in result["data"]:
        if not isinstance(item, Mapping):
            continue
        event_date = _parse_date(item.get("FREE_DATE"))
        if event_date is None:
            continue
        share_type = str(item.get("FREE_SHARES_TYPE") or "限售股份").strip()
        raw_payload = _record_payload({str(key): value for key, value in item.items()})
        for field in (
            "CURRENT_FREE_SHARES",
            "ABLE_FREE_SHARES",
            "LIFT_MARKET_CAP",
            "NON_FREE_SHARES",
        ):
            value = item.get(field)
            try:
                raw_payload[field] = float(str(value)) * 10_000
            except (TypeError, ValueError):
                raw_payload[field] = None
        raw_payload["available_time_basis"] = "ingested_at"
        events.append(
            {
                "symbol": symbol,
                "event_type": "unlock",
                "event_date": event_date,
                "title": f"限售股解禁：{share_type}",
                "payload": raw_payload,
                "source": "eastmoney",
                "available_time": now,
            }
        )
    return events


def _announcement_events(
    client: CninfoClient,
    symbol: str,
    start: date,
    end: date,
) -> list[EventCandidate]:
    announcements = client.announcements(symbol, start, end, page_size=100)
    events: list[EventCandidate] = []
    for item in announcements:
        title = str(item.get("title") or "").strip()
        if "业绩预告" in title:
            event_type = "earnings_preview"
        elif "年度报告" in title or "季度报告" in title:
            event_type = "earnings_report"
        else:
            continue
        published_at = item.get("published_at")
        if not isinstance(published_at, datetime):
            continue
        aware_published = (
            published_at.replace(tzinfo=UTC)
            if published_at.tzinfo is None
            else published_at.astimezone(UTC)
        )
        events.append(
            {
                "symbol": symbol,
                "event_type": event_type,
                "event_date": aware_published.astimezone(MARKET_TIMEZONE).date(),
                "title": title,
                "payload": {
                    "url": item.get("url"),
                    "category": item.get("category"),
                    "published_at": aware_published.isoformat(),
                },
                "source": "cninfo",
                "available_time": aware_published,
            }
        )
    return events


def _event_key(event: EventCandidate) -> tuple[str, str, date, str]:
    return (
        event["symbol"],
        event["event_type"],
        event["event_date"],
        event["title"],
    )


def _persist_events(events: list[EventCandidate]) -> tuple[int, int]:
    if not events:
        return 0, 0
    symbols = sorted({event["symbol"] for event in events})
    inserted = 0
    updated = 0
    with get_session() as session:
        existing = {
            (row.symbol, row.event_type, row.event_date, row.title): row
            for row in session.scalars(
                select(CalendarEvent).where(CalendarEvent.symbol.in_(symbols))
            ).all()
        }
        for event in events:
            key = _event_key(event)
            record = existing.get(key)
            available_time = event["available_time"]
            if record is None:
                record = CalendarEvent(
                    symbol=event["symbol"],
                    event_type=event["event_type"],
                    event_date=event["event_date"],
                    title=event["title"],
                )
                session.add(record)
                existing[key] = record
                inserted += 1
            else:
                updated += 1
                if _utc(record.available_time) < available_time:
                    available_time = _utc(record.available_time)
            record.payload = event["payload"]
            record.source = event["source"]
            record.available_time = available_time
    return inserted, updated


def sync_calendar(symbols: list[str] | None = None) -> dict[str, Any]:
    """Refresh dividend, unlock, and earnings events for tracked stocks."""

    started = monotonic()
    now = datetime.now(UTC)
    today = now.astimezone(MARKET_TIMEZONE).date()
    if symbols is None:
        with get_session() as session:
            targets = list(
                session.scalars(select(WatchlistItem.symbol).order_by(WatchlistItem.symbol)).all()
            )
    else:
        targets = [normalize_symbol(symbol) for symbol in symbols]
    targets = list(dict.fromkeys(symbol for symbol in targets if len(symbol) == 6))
    if not targets:
        raise DataProviderError("calendar sync requires at least one tracked symbol")

    provider = BaoStockMarketDataProvider()
    cninfo = get_cninfo_client()
    start = today - timedelta(days=CALENDAR_LOOKBACK_DAYS)
    candidates: list[EventCandidate] = []
    warnings: list[dict[str, str]] = []
    for symbol in targets:
        for year in (today.year, today.year - 1):
            try:
                candidates.extend(_dividend_events(provider, symbol, year, now))
            except Exception as exc:
                warnings.append(
                    {
                        "symbol": symbol,
                        "source": f"baostock-dividend-{year}",
                        "error": f"{type(exc).__name__}: {exc}"[:500],
                    }
                )
        try:
            candidates.extend(_forecast_events(provider, symbol, start, today, now))
        except Exception as exc:
            warnings.append(
                {
                    "symbol": symbol,
                    "source": "baostock-forecast",
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )
        try:
            candidates.extend(_unlock_events(symbol, now))
        except Exception as exc:
            warnings.append(
                {
                    "symbol": symbol,
                    "source": "eastmoney-unlock",
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )
        try:
            candidates.extend(_announcement_events(cninfo, symbol, start, today))
        except Exception as exc:
            warnings.append(
                {
                    "symbol": symbol,
                    "source": "cninfo",
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )

    unique_events = list({_event_key(event): event for event in candidates}.values())
    inserted, updated = _persist_events(unique_events)
    by_symbol = Counter(event["symbol"] for event in unique_events)
    by_type = Counter(event["event_type"] for event in unique_events)
    by_source = Counter(event["source"] for event in unique_events)
    return {
        "symbols": targets,
        "symbols_total": len(targets),
        "symbols_with_events": len(by_symbol),
        "events": len(unique_events),
        "inserted": inserted,
        "updated": updated,
        "by_symbol": dict(by_symbol),
        "by_type": dict(by_type),
        "by_source": dict(by_source),
        "warnings": warnings,
        "warning_count": len(warnings),
        "duration_seconds": round(monotonic() - started, 2),
    }


def register_calendar_job() -> None:
    register(
        JobSpec(
            name="sync_calendar",
            func=sync_calendar,
            trigger=CronTrigger(
                hour=7,
                minute=30,
                timezone=MARKET_TIMEZONE,
            ),
        )
    )
