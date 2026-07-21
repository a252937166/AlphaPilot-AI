from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from datetime import UTC, date, datetime
from time import monotonic, sleep
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete, select

from alphapilot.data.base import DataProviderError
from alphapilot.db.engine import get_session
from alphapilot.db.models import SectorConstituent, SectorFlowDaily
from alphapilot.futu.client import FutuClient, get_futu_client
from alphapilot.jobs.registry import JobSpec, register

logger = logging.getLogger(__name__)
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
PLATE_REQUEST_PAUSE_SECONDS = 3.2
CAPITAL_FLOW_PAUSE_SECONDS = 1.05
SNAPSHOT_BATCH_SIZE = 400
TOP_FLOW_MEMBERS = 5
SNAPSHOT_NET_FLOW_FIELDS = (
    "net_inflow",
    "net_in_flow",
    "capital_flow",
    "in_flow",
)
SNAPSHOT_MAIN_FLOW_FIELDS = (
    "main_inflow",
    "main_in_flow",
    "main_capital_flow",
)


def _finite(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _market_today() -> date:
    return datetime.now(MARKET_TIMEZONE).date()


def _cn_trade_day(client: FutuClient, target: date) -> date | None:
    """Resolve the requested CN trade day from OpenD, never from stale local bars."""

    value = target.isoformat()
    calendar = client.quote_call_raw(
        "request_trading_days",
        args=["CN", value, value],
    )
    if not isinstance(calendar, list):
        raise DataProviderError("Futu trading calendar returned an invalid payload")
    for raw in calendar:
        if not isinstance(raw, Mapping):
            continue
        raw_date = str(raw.get("time") or "").strip()
        if raw_date == value:
            return target
    return None


def _is_a_share_code(code: str) -> bool:
    if "." not in code:
        return False
    market, symbol = code.upper().split(".", 1)
    return len(symbol) == 6 and (
        (market == "SH" and symbol.startswith("6"))
        or (market == "SZ" and symbol.startswith(("0", "3")))
    )


def _snapshot_batches(client: FutuClient, codes: list[str]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    unique_codes = list(dict.fromkeys(codes))
    for offset in range(0, len(unique_codes), SNAPSHOT_BATCH_SIZE):
        payload = client.quote_call_raw(
            "get_market_snapshot",
            args=[unique_codes[offset : offset + SNAPSHOT_BATCH_SIZE]],
        )
        if not isinstance(payload, pd.DataFrame) or payload.empty:
            raise DataProviderError("Futu returned an empty sector snapshot batch")
        frames.append(payload.copy())
    if not frames:
        raise DataProviderError("sector snapshot requires cached constituents")
    return pd.concat(frames, ignore_index=True)


def sync_sector_constituents(
    pause_seconds: float = PLATE_REQUEST_PAUSE_SECONDS,
) -> dict[str, Any]:
    """Atomically replace the full Futu industry membership cache."""

    started = monotonic()
    client = get_futu_client()
    plates = client.quote_call_raw("get_plate_list", args=["SH", "INDUSTRY"])
    if not isinstance(plates, pd.DataFrame) or plates.empty:
        raise DataProviderError("Futu returned no industry plates")

    refreshed_at = datetime.now(UTC)
    members: list[SectorConstituent] = []
    seen: set[tuple[str, str]] = set()
    failures: list[dict[str, str]] = []
    loaded_plates = 0
    plate_records = plates.to_dict(orient="records")
    for index, raw_plate in enumerate(plate_records, start=1):
        plate = {str(key): value for key, value in raw_plate.items()}
        plate_code = str(plate.get("code") or "").strip()
        plate_name = str(plate.get("plate_name") or "").strip()
        if not plate_code or not plate_name:
            continue
        try:
            stocks = client.quote_call_raw("get_plate_stock", args=[plate_code])
            if not isinstance(stocks, pd.DataFrame) or stocks.empty:
                raise DataProviderError("empty constituent list")
            plate_member_count = 0
            for raw_stock in stocks.to_dict(orient="records"):
                stock = {str(key): value for key, value in raw_stock.items()}
                code = str(stock.get("code") or "").strip().upper()
                if not _is_a_share_code(code) or (plate_code, code) in seen:
                    continue
                seen.add((plate_code, code))
                name = str(stock.get("stock_name") or "").strip() or None
                members.append(
                    SectorConstituent(
                        plate_code=plate_code,
                        plate_name=plate_name,
                        symbol=code,
                        name=name,
                        refreshed_at=refreshed_at,
                    )
                )
                plate_member_count += 1
            if plate_member_count:
                loaded_plates += 1
        except Exception as exc:
            failures.append(
                {
                    "plate_code": plate_code,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )
        finally:
            if pause_seconds > 0:
                sleep(pause_seconds)
        if index % 10 == 0:
            logger.info(
                "sector constituent sync progress processed=%s total=%s members=%s failures=%s",
                index,
                len(plate_records),
                len(members),
                len(failures),
            )

    required_plates = max(1, math.floor(len(plate_records) * 0.8))
    if loaded_plates < required_plates or not members:
        raise DataProviderError(
            "sector constituent coverage below safety floor: "
            f"loaded={loaded_plates}, total={len(plate_records)}, members={len(members)}"
        )
    with get_session() as session:
        session.execute(delete(SectorConstituent))
        session.add_all(members)

    return {
        "plates": loaded_plates,
        "plates_reported": len(plate_records),
        "members": len(members),
        "unique_symbols": len({member.symbol for member in members}),
        "failures": failures[:20],
        "failure_count": len(failures),
        "refreshed_at": refreshed_at.isoformat(),
        "duration_seconds": round(monotonic() - started, 2),
    }


def _cached_plates() -> dict[str, dict[str, Any]]:
    with get_session() as session:
        rows = session.scalars(
            select(SectorConstituent).order_by(
                SectorConstituent.plate_code, SectorConstituent.symbol
            )
        ).all()
    plates: dict[str, dict[str, Any]] = {}
    for row in rows:
        info = plates.setdefault(
            row.plate_code,
            {"plate_name": row.plate_name, "constituents": []},
        )
        info["constituents"].append(row.symbol)
    if not plates:
        raise DataProviderError(
            "sector constituent cache is empty; run sync_sector_constituents first"
        )
    return plates


def _eastmoney_sector_flows() -> dict[str, float]:
    """Try Eastmoney once, with a bounded timeout unlike the AKShare wrapper."""

    response = httpx.get(
        "https://push2.eastmoney.com/api/qt/clist/get",
        params={
            "pn": "1",
            "pz": "500",
            "po": "1",
            "np": "1",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "fltt": "2",
            "invt": "2",
            "fid0": "f62",
            "fs": "m:90 t:2",
            "stat": "1",
            "fields": "f12,f14,f62",
            "rt": "52975239",
        },
        headers={"User-Agent": "Mozilla/5.0 (compatible; AlphaPilot-AI/0.2)"},
        timeout=5.0,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise DataProviderError("Eastmoney sector flow payload is invalid")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise DataProviderError("Eastmoney sector flow response has no data")
    raw_rows = data.get("diff")
    if isinstance(raw_rows, Mapping):
        rows: list[object] = list(raw_rows.values())
    elif isinstance(raw_rows, list):
        rows = raw_rows
    else:
        raise DataProviderError("Eastmoney sector flow response has no rows")
    flows: dict[str, float] = {}
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("f14") or "").strip()
        value = _finite(item.get("f62"))
        if name and value is not None:
            flows[name] = value
    if not flows:
        raise DataProviderError("Eastmoney returned no usable sector flows")
    return flows


def _find_field(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    return next((candidate for candidate in candidates if candidate in columns), None)


def _provider_trade_date(value: object) -> date | None:
    try:
        timestamp = pd.Timestamp(str(value))
    except (TypeError, ValueError):
        return None
    if pd.isna(timestamp):
        return None
    return timestamp.date()


def _snapshot_flow_rows(
    plates: Mapping[str, dict[str, Any]],
    quote_map: Mapping[str, Mapping[str, Any]],
    net_field: str,
    main_field: str | None,
    trade_day: date,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plate_code, info in plates.items():
        net_values: list[float] = []
        main_values: list[float] = []
        for code in info["constituents"]:
            quote = quote_map.get(str(code))
            if quote is None:
                continue
            if _provider_trade_date(quote.get("update_time")) != trade_day:
                continue
            net = _finite(quote.get(net_field))
            if net is not None:
                net_values.append(net)
            if main_field is not None:
                main = _finite(quote.get(main_field))
                if main is not None:
                    main_values.append(main)
        if net_values:
            rows.append(
                {
                    "plate_code": plate_code,
                    "net_inflow": sum(net_values),
                    "main_inflow": sum(main_values) if main_values else None,
                    "source": "futu-snapshot",
                }
            )
    return rows


def _latest_capital_flow(
    frame: pd.DataFrame,
    trade_day: date,
) -> tuple[float, float | None]:
    if frame.empty or "in_flow" not in frame.columns:
        raise DataProviderError("Futu capital flow returned no in_flow data")
    if "capital_flow_item_time" not in frame.columns:
        raise DataProviderError("Futu capital flow returned no item timestamp")
    fresh_records = [
        {str(key): value for key, value in raw.items()}
        for raw in frame.to_dict(orient="records")
        if _provider_trade_date(raw.get("capital_flow_item_time")) == trade_day
    ]
    if not fresh_records:
        raise DataProviderError(f"Futu capital flow has no rows for {trade_day.isoformat()}")
    fresh_records.sort(key=lambda item: str(item.get("capital_flow_item_time") or ""))
    record = fresh_records[-1]
    net = _finite(record.get("in_flow"))
    if net is None:
        raise DataProviderError("Futu capital flow latest in_flow is invalid")
    main = _finite(record.get("main_in_flow"))
    if main is None:
        super_flow = _finite(record.get("super_in_flow"))
        big_flow = _finite(record.get("big_in_flow"))
        if super_flow is not None and big_flow is not None:
            main = super_flow + big_flow
    return net, main


def _futu_top5_flow_rows(
    client: FutuClient,
    plates: Mapping[str, dict[str, Any]],
    quote_map: Mapping[str, Mapping[str, Any]],
    *,
    trade_day: date,
    pause_seconds: float,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    selected_by_plate: dict[str, list[str]] = {}
    for plate_code, info in plates.items():
        candidates = [str(code) for code in info["constituents"] if str(code) in quote_map]
        candidates.sort(
            key=lambda code: _finite(quote_map[code].get("total_market_val")) or 0.0,
            reverse=True,
        )
        selected_by_plate[plate_code] = candidates[:TOP_FLOW_MEMBERS]
    selected_codes = list(
        dict.fromkeys(code for codes in selected_by_plate.values() for code in codes)
    )

    values: dict[str, tuple[float, float | None]] = {}
    failures: list[dict[str, str]] = []
    for index, code in enumerate(selected_codes, start=1):
        try:
            frame = client.quote_call_raw("get_capital_flow", args=[code])
            if not isinstance(frame, pd.DataFrame):
                raise DataProviderError("invalid Futu capital-flow payload")
            values[code] = _latest_capital_flow(frame, trade_day)
        except Exception as exc:
            failures.append({"symbol": code, "error": f"{type(exc).__name__}: {exc}"[:500]})
        finally:
            if pause_seconds > 0:
                sleep(pause_seconds)
        if index % 25 == 0:
            logger.info(
                "sector flow progress processed=%s total=%s failures=%s",
                index,
                len(selected_codes),
                len(failures),
            )

    rows: list[dict[str, Any]] = []
    for plate_code, codes in selected_by_plate.items():
        available = [values[code] for code in codes if code in values]
        if not available:
            continue
        rows.append(
            {
                "plate_code": plate_code,
                "net_inflow": sum(value[0] for value in available),
                "main_inflow": (
                    sum(value for _, value in available if value is not None)
                    if any(value is not None for _, value in available)
                    else None
                ),
                "source": "futu-top5",
            }
        )
    return rows, failures, len(selected_codes)


def _persist_flow_rows(rows: list[dict[str, Any]], trade_day: date) -> tuple[int, int]:
    inserted = 0
    updated = 0
    with get_session() as session:
        existing = {
            row.plate_code: row
            for row in session.scalars(
                select(SectorFlowDaily).where(SectorFlowDaily.trade_date == trade_day)
            ).all()
        }
        for item in rows:
            plate_code = str(item["plate_code"])
            record = existing.get(plate_code)
            if record is None:
                record = SectorFlowDaily(plate_code=plate_code, trade_date=trade_day)
                session.add(record)
                inserted += 1
            else:
                updated += 1
            record.net_inflow = _finite(item.get("net_inflow"))
            record.main_inflow = _finite(item.get("main_inflow"))
            record.source = str(item["source"])
    return inserted, updated


def sync_sector_flows(
    pause_seconds: float = CAPITAL_FLOW_PAUSE_SECONDS,
) -> dict[str, Any]:
    """Persist daily plate flows using the best currently available source."""

    started = monotonic()
    client = get_futu_client()
    requested_date = _market_today()
    trade_day = _cn_trade_day(client, requested_date)
    if trade_day is None:
        return {
            "trade_date": None,
            "requested_date": requested_date.isoformat(),
            "source": None,
            "rows": 0,
            "inserted": 0,
            "updated": 0,
            "skipped": "non_trading_day",
            "duration_seconds": round(monotonic() - started, 2),
        }
    plates = _cached_plates()
    all_codes = list(
        dict.fromkeys(str(code) for info in plates.values() for code in info["constituents"])
    )
    snapshot = _snapshot_batches(client, all_codes)
    columns = {str(column) for column in snapshot.columns}
    quote_map = {
        str(record.get("code")): {str(key): value for key, value in record.items()}
        for record in snapshot.to_dict(orient="records")
    }
    net_field = _find_field(columns, SNAPSHOT_NET_FLOW_FIELDS)
    main_field = _find_field(columns, SNAPSHOT_MAIN_FLOW_FIELDS)
    warnings: list[str] = []
    failures: list[dict[str, str]] = []
    selected_codes = 0
    rows: list[dict[str, Any]] = []
    source = ""

    if net_field is not None:
        rows = _snapshot_flow_rows(plates, quote_map, net_field, main_field, trade_day)
        if rows:
            source = "futu-snapshot"
        else:
            warnings.append(f"snapshot flow field {net_field} contained no usable numeric values")

    if not rows:
        try:
            em_flows = _eastmoney_sector_flows()
            for plate_code, info in plates.items():
                value = em_flows.get(str(info["plate_name"]).strip())
                if value is not None:
                    rows.append(
                        {
                            "plate_code": plate_code,
                            "net_inflow": value,
                            "main_inflow": value,
                            "source": "em",
                        }
                    )
            minimum_em_rows = max(1, math.floor(len(plates) * 0.5))
            if len(rows) < minimum_em_rows:
                warnings.append(
                    "Eastmoney sector names matched too few Futu plates: "
                    f"matched={len(rows)}, required={minimum_em_rows}"
                )
                rows = []
        except Exception as exc:
            warnings.append(f"Eastmoney sector flow unavailable: {type(exc).__name__}: {exc}")

        if not rows:
            rows, failures, selected_codes = _futu_top5_flow_rows(
                client,
                plates,
                quote_map,
                trade_day=trade_day,
                pause_seconds=pause_seconds,
            )
            source = "futu-top5"
        else:
            source = "em"

    if not rows:
        raise DataProviderError("no sector flow rows could be produced")
    inserted, updated = _persist_flow_rows(rows, trade_day)
    return {
        "trade_date": trade_day.isoformat(),
        "requested_date": requested_date.isoformat(),
        "source": source,
        "rows": len(rows),
        "inserted": inserted,
        "updated": updated,
        "plates_total": len(plates),
        "snapshot_symbols": len(quote_map),
        "snapshot_flow_field": net_field,
        "snapshot_main_flow_field": main_field,
        "capital_flow_symbols": selected_codes,
        "failure_count": len(failures),
        "failures": failures[:20],
        "warnings": warnings,
        "skipped": None,
        "duration_seconds": round(monotonic() - started, 2),
    }


def register_sector_jobs() -> None:
    register(
        JobSpec(
            name="sync_sector_constituents",
            func=sync_sector_constituents,
            trigger=CronTrigger(
                day_of_week="sun",
                hour=9,
                minute=0,
                timezone=MARKET_TIMEZONE,
            ),
        )
    )
    register(
        JobSpec(
            name="sync_sector_flows",
            func=sync_sector_flows,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=15,
                minute=20,
                timezone=MARKET_TIMEZONE,
            ),
        )
    )
