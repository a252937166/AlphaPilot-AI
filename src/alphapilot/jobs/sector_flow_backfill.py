from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from datetime import date, datetime, timedelta
from time import monotonic, sleep
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from alphapilot.data.base import DataProviderError
from alphapilot.data.provenance import AUDITED_SECTOR_FLOW_SOURCES
from alphapilot.db.engine import get_session
from alphapilot.db.models import SectorFlowDaily
from alphapilot.futu.client import FutuClient, get_futu_client
from alphapilot.jobs.registry import JobExecutionError, JobOutcome, JobSpec, register
from alphapilot.jobs.sectors_sync import (
    CAPITAL_FLOW_PAUSE_SECONDS,
    TOP_FLOW_MEMBERS,
    _cached_plates,
    _finite,
    _provider_trade_date,
    _snapshot_batches,
)

logger = logging.getLogger(__name__)

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
FUTU_DAILY_SOURCE = "futu-daily"
MAX_WINDOW_DAYS = 365
RECENT_FLOW_TRADE_DAYS = 5
RECENT_FLOW_REPAIR_HOUR = 17
RECENT_FLOW_REPAIR_MINUTE = 30
SQLITE_LOCK_RETRY_DELAYS_SECONDS = (0.5, 1.5, 3.0)


def _market_today() -> date:
    return datetime.now(MARKET_TIMEZONE).date()


def _selected_members(
    client: FutuClient,
    plates: Mapping[str, dict[str, Any]],
    *,
    snapshot_failures: list[dict[str, str]] | None = None,
) -> tuple[dict[str, list[str]], list[str], list[dict[str, str]]]:
    all_codes = list(
        dict.fromkeys(str(code) for info in plates.values() for code in info["constituents"])
    )
    snapshot = _snapshot_batches(client, all_codes, failures=snapshot_failures)
    quote_map = {
        str(record.get("code")): {str(key): value for key, value in record.items()}
        for record in snapshot.to_dict(orient="records")
    }
    selected_by_plate: dict[str, list[str]] = {}
    ranking_failures: list[dict[str, str]] = []
    for plate_code, info in plates.items():
        candidates = [str(code) for code in info["constituents"] if str(code) in quote_map]
        candidates.sort(
            key=lambda code: _finite(quote_map[code].get("total_market_val")) or 0.0,
            reverse=True,
        )
        required_ranked_members = len(set(info["constituents"]))
        selected = candidates[:TOP_FLOW_MEMBERS]
        if len(candidates) < required_ranked_members:
            ranking_failures.append(
                {
                    "plate_code": plate_code,
                    "error": "incomplete ranking universe: "
                    f"available={len(candidates)}, required={required_ranked_members}",
                }
            )
            selected = []
        selected_by_plate[plate_code] = selected
    selected_codes = list(
        dict.fromkeys(code for codes in selected_by_plate.values() for code in codes)
    )
    if not selected_codes:
        raise DataProviderError("Futu sector-flow backfill found no ranked A-share members")
    return selected_by_plate, selected_codes, ranking_failures


def _cn_trading_dates(
    client: FutuClient,
    *,
    start_date: date,
    end_date: date,
) -> list[date]:
    payload = client.quote_call_raw(
        "request_trading_days",
        args=["CN", start_date.isoformat(), end_date.isoformat()],
    )
    if not isinstance(payload, list):
        raise DataProviderError("Futu trading calendar returned an invalid payload")
    dates = sorted(
        {
            trade_date
            for raw in payload
            if isinstance(raw, Mapping)
            and (trade_date := _provider_trade_date(raw.get("time"))) is not None
            and start_date <= trade_date <= end_date
        }
    )
    if not dates:
        raise DataProviderError("Futu trading calendar returned no dates for the backfill window")
    return dates


def _daily_flow_values(
    frame: pd.DataFrame,
    *,
    start_date: date,
    end_date: date,
) -> dict[date, tuple[float, float | None]]:
    required = {"capital_flow_item_time", "in_flow"}
    missing = required.difference(str(column) for column in frame.columns)
    if missing:
        raise DataProviderError(f"Futu capital-flow schema missing columns: {sorted(missing)}")

    values: dict[date, tuple[float, float | None]] = {}
    for raw in frame.to_dict(orient="records"):
        trade_date = _provider_trade_date(raw.get("capital_flow_item_time"))
        if trade_date is None or not start_date <= trade_date <= end_date:
            continue
        net = _finite(raw.get("in_flow"))
        if net is None:
            continue
        main = _finite(raw.get("main_in_flow"))
        if main is None:
            super_flow = _finite(raw.get("super_in_flow"))
            big_flow = _finite(raw.get("big_in_flow"))
            if super_flow is not None and big_flow is not None:
                main = super_flow + big_flow
        values[trade_date] = (net, main)
    return values


def _aggregate_plate_rows(
    selected_by_plate: Mapping[str, list[str]],
    values_by_symbol: Mapping[str, dict[date, tuple[float, float | None]]],
    *,
    required_dates: list[date],
) -> dict[date, list[dict[str, Any]]]:
    rows_by_date: dict[date, list[dict[str, Any]]] = {}
    for plate_code, symbols in selected_by_plate.items():
        if not symbols:
            continue
        for trade_date in required_dates:
            if any(trade_date not in values_by_symbol.get(symbol, {}) for symbol in symbols):
                continue
            available = [values_by_symbol[symbol][trade_date] for symbol in symbols]
            main_values = [main for _, main in available if main is not None]
            rows_by_date.setdefault(trade_date, []).append(
                {
                    "plate_code": plate_code,
                    "net_inflow": sum(net for net, _ in available),
                    "main_inflow": (sum(main_values) if len(main_values) == len(symbols) else None),
                }
            )
    return rows_by_date


def _is_sqlite_write_lock(exc: Exception) -> bool:
    return isinstance(exc, OperationalError) and "database is locked" in str(exc).lower()


def _persist_day(
    trade_date: date,
    rows: list[dict[str, Any]],
    *,
    missing_only: bool = False,
) -> tuple[int, int, int]:
    retry_count = 0
    while True:
        try:
            with get_session() as session:
                existing = {
                    row.plate_code: row
                    for row in session.scalars(
                        select(SectorFlowDaily).where(SectorFlowDaily.trade_date == trade_date)
                    ).all()
                }
                inserted = 0
                updated = 0
                skipped = 0
                for item in rows:
                    plate_code = str(item["plate_code"])
                    record = existing.get(plate_code)
                    record_source = record.source if record is not None else None
                    record_is_valid = (
                        record is not None
                        and _finite(record.net_inflow) is not None
                        and record_source in AUDITED_SECTOR_FLOW_SOURCES
                    )
                    if record_is_valid and (
                        missing_only or record_source == FUTU_DAILY_SOURCE
                    ):
                        skipped += 1
                        continue
                    if record is None:
                        record = SectorFlowDaily(
                            plate_code=plate_code,
                            trade_date=trade_date,
                            source=FUTU_DAILY_SOURCE,
                        )
                        session.add(record)
                        inserted += 1
                    else:
                        updated += 1
                    record.net_inflow = _finite(item.get("net_inflow"))
                    record.main_inflow = _finite(item.get("main_inflow"))
                    record.source = FUTU_DAILY_SOURCE
            return inserted, updated, skipped
        except OperationalError as exc:
            if not _is_sqlite_write_lock(exc) or retry_count >= len(
                SQLITE_LOCK_RETRY_DELAYS_SECONDS
            ):
                raise
            delay = SQLITE_LOCK_RETRY_DELAYS_SECONDS[retry_count]
            retry_count += 1
            logger.warning(
                "sector-flow backfill SQLite lock date=%s retry=%s/%s delay=%ss",
                trade_date,
                retry_count,
                len(SQLITE_LOCK_RETRY_DELAYS_SECONDS),
                delay,
            )
            sleep(delay)


def _coverage(
    *,
    start_date: date,
    end_date: date,
    plates_total: int,
    expected_trade_dates: list[date] | None = None,
    plate_codes: set[str] | None = None,
    accepted_sources: Collection[str] = (FUTU_DAILY_SOURCE,),
) -> dict[str, Any]:
    with get_session() as session:
        statement = select(
            func.count(SectorFlowDaily.id),
            func.count(func.distinct(SectorFlowDaily.plate_code)),
            func.count(func.distinct(SectorFlowDaily.trade_date)),
            func.min(SectorFlowDaily.trade_date),
            func.max(SectorFlowDaily.trade_date),
        ).where(
            SectorFlowDaily.source.in_(accepted_sources),
            SectorFlowDaily.net_inflow.is_not(None),
            SectorFlowDaily.trade_date >= start_date,
            SectorFlowDaily.trade_date <= end_date,
        )
        if plate_codes is not None:
            statement = statement.where(SectorFlowDaily.plate_code.in_(plate_codes))
        row = session.execute(statement).one()
    rows = int(row[0] or 0)
    trade_days = int(row[2] or 0)
    expected_trade_days = (
        len(expected_trade_dates) if expected_trade_dates is not None else trade_days
    )
    expected = plates_total * expected_trade_days
    return {
        "rows": rows,
        "plates": int(row[1] or 0),
        "trade_days": trade_days,
        "expected_trade_days": expected_trade_days,
        "expected_rows": expected,
        "row_coverage": round(rows / expected, 6) if expected else 0.0,
        "min_trade_date": row[3].isoformat() if isinstance(row[3], date) else None,
        "max_trade_date": row[4].isoformat() if isinstance(row[4], date) else None,
    }


def backfill_sector_flows(
    *,
    start_date: date,
    end_date: date | None = None,
    pause_seconds: float = CAPITAL_FLOW_PAUSE_SECONDS,
    missing_only: bool = False,
) -> dict[str, Any] | JobOutcome:
    """Backfill at most one Futu DAY year with the accepted fixed-basket bias.

    Historical plate rows deliberately reuse today's market-cap top five.  That
    basket is not point-in-time correct and therefore carries the light
    look-ahead deviation accepted for P3.3-S5; keep it explicit in JobRun stats.
    """

    started = monotonic()
    resolved_end = end_date or _market_today()
    if resolved_end < start_date:
        raise ValueError("end_date must not be earlier than start_date")
    if (resolved_end - start_date).days > MAX_WINDOW_DAYS:
        raise ValueError("Futu DAY capital-flow window must not exceed 365 days")
    if pause_seconds < 0:
        raise ValueError("pause_seconds must not be negative")

    stats: dict[str, Any] = {
        "source": FUTU_DAILY_SOURCE,
        "start_date": start_date.isoformat(),
        "end_date": resolved_end.isoformat(),
        "plates_total": 0,
        "selected_symbols": 0,
        "member_calls": 0,
        "symbols_with_data": 0,
        "symbols_no_data": 0,
        "failure_count": 0,
        "failures": [],
        "rows_aggregated": 0,
        "inserted": 0,
        "updated": 0,
        "skipped_existing": 0,
        "coverage": {},
        "idempotent_skip": False,
        "is_complete": False,
        "missing_only": missing_only,
        "depth_limit": "Futu PeriodType.DAY <= 365 calendar days",
        "basket_basis": "current total_market_val fixed top5",
        "lookahead_bias": "current top5 fixed for history; accepted M3 limitation",
    }
    try:
        client = get_futu_client()
        plates = _cached_plates()
        stats["plates_total"] = len(plates)
        trading_dates = _cn_trading_dates(
            client,
            start_date=start_date,
            end_date=resolved_end,
        )
        accepted_coverage_sources = (
            AUDITED_SECTOR_FLOW_SOURCES if missing_only else (FUTU_DAILY_SOURCE,)
        )
        existing_coverage = _coverage(
            start_date=start_date,
            end_date=resolved_end,
            plates_total=len(plates),
            expected_trade_dates=trading_dates,
            plate_codes=set(plates),
            accepted_sources=accepted_coverage_sources,
        )
        if (
            existing_coverage["plates"] == len(plates)
            and existing_coverage["trade_days"] == len(trading_dates)
            and existing_coverage["rows"] == len(plates) * len(trading_dates)
            and existing_coverage["min_trade_date"] == trading_dates[0].isoformat()
            and existing_coverage["max_trade_date"] == trading_dates[-1].isoformat()
        ):
            stats["coverage"] = existing_coverage
            stats["skipped_existing"] = int(existing_coverage["rows"])
            stats["idempotent_skip"] = True
            stats["is_complete"] = True
            stats["duration_seconds"] = round(monotonic() - started, 2)
            return stats

        snapshot_failures: list[dict[str, str]] = []
        selected_by_plate, selected_codes, ranking_failures = _selected_members(
            client,
            plates,
            snapshot_failures=snapshot_failures,
        )
        stats["selected_symbols"] = len(selected_codes)
        stats["snapshot_failure_count"] = len(snapshot_failures)
        stats["snapshot_failures"] = snapshot_failures[:20]
        stats["ranking_failure_count"] = len(ranking_failures)
        stats["ranking_failures"] = ranking_failures[:20]
        values_by_symbol: dict[str, dict[date, tuple[float, float | None]]] = {}
        failures: list[dict[str, str]] = []

        for index, code in enumerate(selected_codes, start=1):
            stats["member_calls"] = index
            try:
                payload = client.quote_call_raw(
                    "get_capital_flow",
                    kwargs={
                        "stock_code": code,
                        "period_type": {
                            "__futu_constant__": "PeriodType.DAY",
                        },
                        "start": start_date.isoformat(),
                        "end": resolved_end.isoformat(),
                    },
                )
                if not isinstance(payload, pd.DataFrame):
                    raise DataProviderError("Futu returned an invalid capital-flow payload")
                values = _daily_flow_values(
                    payload,
                    start_date=start_date,
                    end_date=resolved_end,
                )
                values_by_symbol[code] = values
                if values:
                    stats["symbols_with_data"] += 1
                else:
                    stats["symbols_no_data"] += 1
            except Exception as exc:
                if len(failures) < 20:
                    failures.append(
                        {
                            "symbol": code,
                            "error": f"{type(exc).__name__}: {exc}"[:500],
                        }
                    )
                stats["failure_count"] = int(stats["failure_count"]) + 1
            finally:
                if pause_seconds > 0:
                    sleep(pause_seconds)
            if index % 25 == 0:
                logger.info(
                    "sector-flow backfill progress processed=%s total=%s failures=%s",
                    index,
                    len(selected_codes),
                    stats["failure_count"],
                )

        stats["failures"] = failures
        rows_by_date = _aggregate_plate_rows(
            selected_by_plate,
            values_by_symbol,
            required_dates=trading_dates,
        )
        stats["rows_aggregated"] = sum(len(rows) for rows in rows_by_date.values())
        for trade_date, rows in sorted(rows_by_date.items()):
            inserted, updated, skipped = _persist_day(
                trade_date,
                rows,
                missing_only=missing_only,
            )
            stats["inserted"] = int(stats["inserted"]) + inserted
            stats["updated"] = int(stats["updated"]) + updated
            stats["skipped_existing"] = int(stats["skipped_existing"]) + skipped

        stats["coverage"] = _coverage(
            start_date=start_date,
            end_date=resolved_end,
            plates_total=len(plates),
            expected_trade_dates=trading_dates,
            plate_codes=set(plates),
            accepted_sources=accepted_coverage_sources,
        )
        coverage = stats["coverage"]
        stats["is_complete"] = (
            coverage["rows"] == coverage["expected_rows"]
            and coverage["plates"] == len(plates)
            and coverage["trade_days"] == len(trading_dates)
        )
        stats["missing_plate_days"] = max(
            0,
            int(coverage["expected_rows"]) - int(coverage["rows"]),
        )
        stats["duration_seconds"] = round(monotonic() - started, 2)
        if not stats["is_complete"]:
            return JobOutcome(status="degraded", stats=stats)
        return stats
    except JobExecutionError:
        raise
    except Exception as exc:
        raise JobExecutionError(
            f"Futu sector-flow backfill failed: {type(exc).__name__}: {exc}",
            stats={
                **stats,
                "duration_seconds": round(monotonic() - started, 2),
            },
        ) from exc


def _missing_recent_plate_dates(
    *,
    trading_dates: list[date],
    plate_codes: set[str],
) -> dict[date, list[str]]:
    if not trading_dates or not plate_codes:
        return {}
    with get_session() as session:
        observed = {
            (str(plate_code), trade_date)
            for plate_code, trade_date in session.execute(
                select(SectorFlowDaily.plate_code, SectorFlowDaily.trade_date).where(
                    SectorFlowDaily.trade_date.in_(trading_dates),
                    SectorFlowDaily.plate_code.in_(plate_codes),
                    SectorFlowDaily.net_inflow.is_not(None),
                    SectorFlowDaily.source.in_(AUDITED_SECTOR_FLOW_SOURCES),
                )
            )
        }
    return {
        trade_date: sorted(
            plate_code for plate_code in plate_codes if (plate_code, trade_date) not in observed
        )
        for trade_date in trading_dates
        if any((plate_code, trade_date) not in observed for plate_code in plate_codes)
    }


def repair_recent_sector_flow_gaps(
    *,
    lookback_trade_days: int = RECENT_FLOW_TRADE_DAYS,
    pause_seconds: float = CAPITAL_FLOW_PAUSE_SECONDS,
) -> dict[str, Any] | JobOutcome:
    """Audit and fill only real gaps in the most recent trading-day window."""

    if not 1 <= lookback_trade_days <= 20:
        raise ValueError("lookback_trade_days must be between 1 and 20")
    started = monotonic()
    client = get_futu_client()
    end_date = _market_today()
    calendar_start = end_date - timedelta(days=max(30, lookback_trade_days * 4))
    trading_dates = _cn_trading_dates(
        client,
        start_date=calendar_start,
        end_date=end_date,
    )[-lookback_trade_days:]
    if len(trading_dates) < lookback_trade_days:
        raise DataProviderError(
            "Futu trading calendar did not cover the requested recent window: "
            f"returned={len(trading_dates)}, required={lookback_trade_days}"
        )
    plates = _cached_plates()
    plate_codes = set(plates)
    missing_before = _missing_recent_plate_dates(
        trading_dates=trading_dates,
        plate_codes=plate_codes,
    )
    missing_before_count = sum(len(codes) for codes in missing_before.values())
    if not missing_before:
        return {
            "trade_dates": [item.isoformat() for item in trading_dates],
            "plates_total": len(plates),
            "expected_rows": len(plates) * len(trading_dates),
            "missing_before": 0,
            "missing_after": 0,
            "repaired": 0,
            "is_complete": True,
            "backfill": None,
            "duration_seconds": round(monotonic() - started, 2),
        }

    result = backfill_sector_flows(
        start_date=trading_dates[0],
        end_date=trading_dates[-1],
        pause_seconds=pause_seconds,
        missing_only=True,
    )
    backfill_stats = result.stats if isinstance(result, JobOutcome) else result
    missing_after = _missing_recent_plate_dates(
        trading_dates=trading_dates,
        plate_codes=plate_codes,
    )
    missing_after_count = sum(len(codes) for codes in missing_after.values())
    stats = {
        "trade_dates": [item.isoformat() for item in trading_dates],
        "plates_total": len(plates),
        "expected_rows": len(plates) * len(trading_dates),
        "missing_before": missing_before_count,
        "missing_dates_before": {
            item.isoformat(): codes[:50] for item, codes in missing_before.items()
        },
        "missing_after": missing_after_count,
        "missing_dates_after": {
            item.isoformat(): codes[:50] for item, codes in missing_after.items()
        },
        "repaired": missing_before_count - missing_after_count,
        "is_complete": not missing_after,
        "backfill": backfill_stats,
        "duration_seconds": round(monotonic() - started, 2),
    }
    if missing_after:
        return JobOutcome(status="degraded", stats=stats)
    return stats


def register_sector_flow_backfill_job() -> None:
    register(
        JobSpec(
            name="backfill_sector_flows",
            func=backfill_sector_flows,
            trigger=None,
        )
    )
    register(
        JobSpec(
            name="repair_recent_sector_flow_gaps",
            func=repair_recent_sector_flow_gaps,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=RECENT_FLOW_REPAIR_HOUR,
                minute=RECENT_FLOW_REPAIR_MINUTE,
                timezone=MARKET_TIMEZONE,
            ),
        )
    )
