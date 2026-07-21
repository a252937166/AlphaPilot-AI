from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.triggers.cron import CronTrigger

from alphapilot.data.baostock_provider import BaoStockMarketDataProvider
from alphapilot.data.base import DataProviderError
from alphapilot.db.engine import get_session
from alphapilot.db.models import Security
from alphapilot.jobs.registry import JobSpec, register
from alphapilot.services.market_data import latest_trade_date


def _clean_code(value: object) -> str | None:
    raw = str(value).strip().lower()
    if raw.endswith(".0"):
        raw = raw[:-2]
    if "." in raw:
        market, raw = raw.split(".", 1)
        if market not in {"sh", "sz", "bj"}:
            return None
        if market == "sh" and not raw.startswith("6"):
            return None
        if market == "sz" and not raw.startswith(("0", "3")):
            return None
        if market == "bj" and not raw.startswith(("4", "8")):
            return None
    digits = "".join(character for character in raw if character.isdigit()).zfill(6)
    if len(digits) != 6 or not digits.startswith(("0", "3", "4", "6", "8", "9")):
        return None
    return digits


def _first_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str:
    normalized = {str(column).strip().lower(): str(column) for column in frame.columns}
    for candidate in candidates:
        match = normalized.get(candidate.lower())
        if match is not None:
            return match
    raise DataProviderError(f"missing expected columns {candidates}; got {list(frame.columns)}")


def _load_akshare_universe() -> pd.DataFrame:
    try:
        import akshare as ak
    except ImportError as exc:
        raise DataProviderError(
            'AKShare is not installed. Run: pip install -e ".[cn-data]"'
        ) from exc
    try:
        frame = ak.stock_info_a_code_name()
    except Exception as exc:
        raise DataProviderError(f"AKShare universe query failed: {exc}") from exc
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise DataProviderError("AKShare returned an empty A-share universe")
    code_column = _first_column(frame, ("code", "代码"))
    name_column = _first_column(frame, ("name", "名称"))
    return frame[[code_column, name_column]].rename(
        columns={code_column: "code", name_column: "name"}
    )


def _load_baostock_universe(
    provider: BaoStockMarketDataProvider, trade_date: date
) -> pd.DataFrame:
    frame = provider.get_stock_universe(trade_date)
    code_column = _first_column(frame, ("code",))
    name_column = _first_column(frame, ("code_name", "codename"))
    status_column = _first_column(frame, ("tradestatus", "trade_status"))
    active = frame[frame[status_column].astype(str) == "1"]
    return active[[code_column, name_column]].rename(
        columns={code_column: "code", name_column: "name"}
    )


def _load_baostock_industries(provider: BaoStockMarketDataProvider) -> pd.DataFrame:
    frame = provider.get_stock_industries()
    code_column = _first_column(frame, ("code",))
    industry_column = _first_column(frame, ("industry",))
    return frame[[code_column, industry_column]].rename(
        columns={code_column: "code", industry_column: "industry"}
    )


def _board_for_symbol(symbol: str) -> str:
    if symbol.startswith("688"):
        return "科创板"
    if symbol.startswith(("300", "301")):
        return "创业板"
    if symbol.startswith(("4", "8", "92")):
        return "北交所"
    return "主板"


def _normalized_rows(frame: pd.DataFrame) -> dict[str, str]:
    rows: dict[str, str] = {}
    for item in frame.to_dict(orient="records"):
        values = {str(key): value for key, value in item.items()}
        code = _clean_code(values.get("code"))
        name = str(values.get("name") or "").strip()
        if code is not None and name:
            rows[code] = name
    return rows


def _industry_map(frame: pd.DataFrame) -> dict[str, str]:
    rows: dict[str, str] = {}
    for item in frame.to_dict(orient="records"):
        values = {str(key): value for key, value in item.items()}
        code = _clean_code(values.get("code"))
        industry = str(values.get("industry") or "").strip()
        if code is not None and industry:
            rows[code] = industry
    return rows


def sync_universe() -> dict[str, Any]:
    """Refresh the complete A-share security master with a live fallback chain."""

    provider = BaoStockMarketDataProvider()
    warnings: list[str] = []
    source = "akshare"
    try:
        universe = _load_akshare_universe()
    except DataProviderError as exc:
        warnings.append(str(exc))
        source = "baostock"
        with get_session() as session:
            trade_date = latest_trade_date(session)
        universe = _load_baostock_universe(provider, trade_date)

    try:
        industries = _industry_map(_load_baostock_industries(provider))
    except DataProviderError as exc:
        warnings.append(str(exc))
        industries = {}

    stocks = _normalized_rows(universe)
    if len(stocks) < 100:
        raise DataProviderError(f"universe unexpectedly small: {len(stocks)}")

    inserted = 0
    updated = 0
    st_count = 0
    now = datetime.now(UTC)
    with get_session() as session:
        for symbol, name in stocks.items():
            is_st = "ST" in name.upper()
            st_count += int(is_st)
            security = session.get(Security, symbol)
            if security is None:
                security = Security(symbol=symbol)
                session.add(security)
                inserted += 1
            else:
                updated += 1
            industry = industries.get(symbol)
            security.name = name
            if industry is not None:
                security.industry_csrc = industry
                if security.industry is None:
                    security.industry = industry
            security.board = _board_for_symbol(symbol)
            security.is_st = is_st
            security.list_status = "listed"
            security.updated_at = now

    return {
        "total": len(stocks),
        "inserted": inserted,
        "updated": updated,
        "st_count": st_count,
        "industry_count": len(industries),
        "source": source,
        "warnings": warnings,
    }


def register_universe_job() -> None:
    register(
        JobSpec(
            name="sync_universe",
            func=sync_universe,
            trigger=CronTrigger(hour=8, minute=30, timezone=ZoneInfo("Asia/Shanghai")),
        )
    )
