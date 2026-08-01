from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from alphapilot.core.config import get_settings
from alphapilot.db.backup import create_database_backup, verify_database_backup
from alphapilot.futu.client import PERMANENTLY_BLOCKED_METHODS

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_PROVIDER_DATE_MINIMUM = date(1990, 1, 1)
_FUTU_MARKETS: tuple[tuple[str, str], ...] = (
    ("SH", "Market.SH"),
    ("SZ", "Market.SZ"),
)
_CODE_PATTERN = re.compile(r"^(SH|SZ)\.(\d{6})$")
_TUSHARE_BSE_CODE_PATTERN = re.compile(r"^(\d{6})\.BJ$")
_SH_A_SHARE_PATTERN = re.compile(r"^(?:60|68)\d{4}$")
_SZ_A_SHARE_PATTERN = re.compile(r"^(?:00|30)\d{4}$")
_BSE_A_SHARE_PATTERN = re.compile(r"^(?:[48]\d{5}|92\d{4})$")
_TUSHARE_STOCK_BASIC_FIELDS = "ts_code,symbol,exchange,list_status,list_date"
_TUSHARE_API_URL = "https://api.tushare.pro"


class FutuBasicInfoClient(Protocol):
    def quote_call_raw(
        self,
        method: str,
        args: list[Any] | None = None,
        kwargs: Mapping[str, Any] | None = None,
    ) -> Any: ...


TushareStockBasicFetcher = Callable[[str], pd.DataFrame]


class ListedDateBackfillError(RuntimeError):
    """A fail-closed listing-date validation or mutation gate failed."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.details = dict(details or {})


def _sqlite_read_only_uri(path: Path) -> str:
    return f"file:{quote(str(path), safe='/')}?mode=ro"


def _validate_evidence_path(database: Path, evidence_path: Path) -> Path:
    evidence = evidence_path.expanduser().resolve()
    protected = {
        database,
        database.with_name(f"{database.name}-wal"),
        database.with_name(f"{database.name}-shm"),
        database.with_name(f"{database.name}-journal"),
    }
    if evidence in protected:
        raise ListedDateBackfillError(
            "evidence path aliases the live SQLite database or one of its sidecars"
        )
    if evidence.exists():
        for protected_path in protected:
            if protected_path.exists() and os.path.samefile(evidence, protected_path):
                raise ListedDateBackfillError(
                    "evidence path is a hard-link alias of a protected SQLite file"
                )
    return evidence


def _atomic_write_json(path: Path, document: Mapping[str, Any]) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory_descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _table_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    if not rows:
        raise ListedDateBackfillError(f"required SQLite table is missing: {table}")
    return tuple(str(row[1]) for row in rows)


def _validate_schema(connection: sqlite3.Connection) -> tuple[str, ...]:
    security_columns = _table_columns(connection, "securities")
    required_security_columns = {
        "symbol",
        "market",
        "list_status",
        "listed_date",
        "profile",
    }
    missing = sorted(required_security_columns - set(security_columns))
    if missing:
        raise ListedDateBackfillError(
            "securities schema is missing required columns",
            details={"missing_columns": missing},
        )
    for table in ("trade_proposals", "broker_orders", "job_runs"):
        _table_columns(connection, table)
    if "status" not in _table_columns(connection, "job_runs"):
        raise ListedDateBackfillError("job_runs schema is missing status")
    return security_columns


def _scalar_count(connection: sqlite3.Connection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    if row is None:
        raise ListedDateBackfillError("SQLite count query returned no row")
    return int(row[0])


def _quick_check(connection: sqlite3.Connection) -> str:
    rows = [str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()]
    return "ok" if rows == ["ok"] else json.dumps(rows, ensure_ascii=False)


def _safety_snapshot(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "trade_proposals": _scalar_count(
            connection,
            "SELECT COUNT(*) FROM trade_proposals",
        ),
        "broker_orders": _scalar_count(
            connection,
            "SELECT COUNT(*) FROM broker_orders",
        ),
        "running_job_runs": _scalar_count(
            connection,
            "SELECT COUNT(*) FROM job_runs WHERE status = 'running'",
        ),
    }


def _application_safety_snapshot() -> dict[str, Any]:
    settings = get_settings()
    snapshot: dict[str, Any] = {
        "trading_mode": settings.trading_mode,
        "live_trading_enabled": settings.live_trading_enabled,
        "paper_trading_enabled": settings.paper_trading_enabled,
        "paper_auto_trading_enabled": settings.paper_auto_trading_enabled,
        "futu_enable_account_mutation": settings.futu_enable_account_mutation,
        "futu_enable_trade": settings.futu_enable_trade,
        "unlock_trade_permanently_blocked": ("unlock_trade" in PERMANENTLY_BLOCKED_METHODS),
    }
    # This maintenance command follows the signed S6 invariants: research mode,
    # live=false, paper_auto=false, and a permanently blocked unlock endpoint.
    # Manual SIMULATE support may remain enabled here; the detached formal S7
    # runner independently forces paper/trade capabilities off before research.
    blockers = [
        key
        for key, unsafe in {
            "trading_mode": snapshot["trading_mode"] != "research",
            "live_trading_enabled": snapshot["live_trading_enabled"],
            "paper_auto_trading_enabled": snapshot["paper_auto_trading_enabled"],
            "unlock_trade_permanently_blocked": not snapshot["unlock_trade_permanently_blocked"],
        }.items()
        if unsafe
    ]
    if blockers:
        raise ListedDateBackfillError(
            "research/trading safety gate is not closed",
            details={"application_safety": snapshot, "blockers": blockers},
        )
    return snapshot


def _market_for_symbol(symbol: str) -> str | None:
    if _SH_A_SHARE_PATTERN.fullmatch(symbol) is not None:
        return "SH"
    if _SZ_A_SHARE_PATTERN.fullmatch(symbol) is not None:
        return "SZ"
    if _BSE_A_SHARE_PATTERN.fullmatch(symbol) is not None:
        return "BSE"
    return None


def _authority_for_market(market: str) -> str:
    if market in {"SH", "SZ"}:
        return "futu"
    if market == "BSE":
        return "tushare"
    raise ListedDateBackfillError(
        "current CN-listed security has no configured listing-date authority",
        details={"market": market},
    )


def _authority_code(symbol: str, market: str) -> str:
    if market in {"SH", "SZ"}:
        return f"{market}.{symbol}"
    if market == "BSE":
        return f"{symbol}.BJ"
    raise ListedDateBackfillError(
        "cannot build listing-date authority code for unsupported market",
        details={"symbol": symbol, "market": market},
    )


def _strict_tushare_date(value: object, *, context: str) -> date:
    if not isinstance(value, str):
        raise ListedDateBackfillError(
            f"{context} must be text in exact YYYYMMDD form",
            details={"value_type": type(value).__name__},
        )
    if value.strip() != value or re.fullmatch(r"\d{8}", value) is None:
        raise ListedDateBackfillError(
            f"{context} must be an exact YYYYMMDD date",
            details={"value": value},
        )
    try:
        parsed = datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise ListedDateBackfillError(
            f"{context} is not a valid calendar date",
            details={"value": value},
        ) from exc
    if parsed < _PROVIDER_DATE_MINIMUM:
        raise ListedDateBackfillError(
            f"{context} predates the supported mainland exchange history",
            details={"value": parsed.isoformat()},
        )
    return parsed


def _strict_iso_date(value: object, *, context: str) -> date:
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            raise ListedDateBackfillError(f"{context} is empty")
        parsed = value.date()
    elif isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if text != value or re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) is None:
            raise ListedDateBackfillError(
                f"{context} must be an exact YYYY-MM-DD date",
                details={"value": value},
            )
        try:
            parsed = date.fromisoformat(text)
        except ValueError as exc:
            raise ListedDateBackfillError(
                f"{context} is not a valid calendar date",
                details={"value": value},
            ) from exc
    else:
        raise ListedDateBackfillError(
            f"{context} has an unsupported value type",
            details={"value_type": type(value).__name__},
        )
    if parsed < _PROVIDER_DATE_MINIMUM:
        raise ListedDateBackfillError(
            f"{context} predates the supported mainland exchange history",
            details={"value": parsed.isoformat()},
        )
    return parsed


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _security_rows(
    connection: sqlite3.Connection,
    columns: tuple[str, ...],
) -> tuple[dict[str, Any], ...]:
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    rows = connection.execute(
        f'SELECT {quoted_columns} FROM "securities" ORDER BY "symbol"'
    ).fetchall()
    return tuple(dict(zip(columns, row, strict=True)) for row in rows)


def _security_rows_sha256(
    columns: tuple[str, ...],
    rows: tuple[dict[str, Any], ...],
) -> str:
    normalized = json.dumps(
        {
            "columns": list(columns),
            "rows": rows,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def _verify_backup_security_snapshot(
    backup_path: Path,
    *,
    columns: tuple[str, ...],
    rows_before: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    connection = sqlite3.connect(
        _sqlite_read_only_uri(backup_path),
        uri=True,
        timeout=15.0,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        query_only = connection.execute("PRAGMA query_only").fetchone()
        if query_only is None or int(query_only[0]) != 1:
            raise ListedDateBackfillError(
                "backup securities verification did not enter query_only mode"
            )
        backup_columns = _validate_schema(connection)
        if backup_columns != columns:
            raise ListedDateBackfillError(
                "backup securities schema differs from the mutation preflight"
            )
        backup_rows = _security_rows(connection, backup_columns)
        if backup_rows != rows_before:
            raise ListedDateBackfillError(
                "backup securities snapshot differs from the mutation preflight"
            )
        digest = _security_rows_sha256(columns, rows_before)
        return {
            "query_only": True,
            "rows": len(rows_before),
            "columns": len(columns),
            "sha256": digest,
            "matches_preflight": True,
        }
    finally:
        connection.close()


def _eligible_rows(
    rows: tuple[dict[str, Any], ...],
    *,
    as_of_date: date,
) -> tuple[dict[str, Any], ...]:
    eligible: list[dict[str, Any]] = []
    for row in rows:
        if row["market"] != "CN" or row["list_status"] != "listed":
            continue
        raw_symbol = row["symbol"]
        if not isinstance(raw_symbol, str):
            raise ListedDateBackfillError("securities.symbol must be text")
        symbol = raw_symbol.strip()
        if symbol != raw_symbol:
            raise ListedDateBackfillError(
                "securities.symbol contains surrounding whitespace",
                details={"symbol": raw_symbol},
            )
        market = _market_for_symbol(symbol)
        if market is None:
            raise ListedDateBackfillError(
                "current CN-listed security has an unsupported symbol prefix",
                details={"symbol": symbol},
            )
        listed_date = row["listed_date"]
        if not _missing(listed_date):
            parsed = _strict_iso_date(
                listed_date,
                context=f"securities[{symbol}].listed_date",
            )
            if parsed > as_of_date:
                raise ListedDateBackfillError(
                    "an existing listed_date is in the future",
                    details={"symbol": symbol, "listed_date": parsed.isoformat()},
                )
        eligible.append(
            {
                **row,
                "_authority": _authority_for_market(market),
                "_authority_market": market,
            }
        )
    return tuple(eligible)


def _read_preflight(
    database: Path,
    *,
    as_of_date: date,
) -> tuple[
    tuple[str, ...],
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    dict[str, int],
    str,
]:
    connection = sqlite3.connect(
        _sqlite_read_only_uri(database),
        uri=True,
        timeout=15.0,
    )
    try:
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA query_only=ON")
        query_only = connection.execute("PRAGMA query_only").fetchone()
        if query_only is None or int(query_only[0]) != 1:
            raise ListedDateBackfillError("SQLite preflight did not enter query_only mode")
        columns = _validate_schema(connection)
        quick_check = _quick_check(connection)
        if quick_check != "ok":
            raise ListedDateBackfillError(
                "pre-backfill PRAGMA quick_check failed",
                details={"quick_check": quick_check},
            )
        safety = _safety_snapshot(connection)
        if safety["running_job_runs"] != 0:
            raise ListedDateBackfillError(
                "running JobRun rows exist; refusing Futu query and database mutation",
                details={"safety": safety},
            )
        all_rows = _security_rows(connection, columns)
        eligible = _eligible_rows(all_rows, as_of_date=as_of_date)
        return columns, all_rows, eligible, safety, quick_check
    finally:
        connection.close()


def fetch_tushare_bse_stock_basic(token: str) -> pd.DataFrame:
    """Fetch the current BSE listing authority with exactly one API call."""

    resolved_token = token.strip()
    if not resolved_token:
        raise ListedDateBackfillError("Tushare token is required for the BSE stock_basic authority")
    try:
        with httpx.Client(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(30.0, connect=5.0),
        ) as client:
            response = client.post(
                _TUSHARE_API_URL,
                json={
                    "api_name": "stock_basic",
                    "token": resolved_token,
                    "params": {"exchange": "BSE", "list_status": "L"},
                    "fields": _TUSHARE_STOCK_BASIC_FIELDS,
                },
                headers={
                    "User-Agent": "AlphaPilotAI/0.3 listing-date-audit",
                },
            )
            response.raise_for_status()
            document = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        raise ListedDateBackfillError(
            "Tushare stock_basic authority call failed",
            details={
                "provider": "tushare",
                "api_name": "stock_basic",
                "provider_error_type": type(exc).__name__,
                "http_status": status_code,
            },
        ) from exc
    if not isinstance(document, dict):
        raise ListedDateBackfillError("Tushare stock_basic authority response is not an object")
    if document.get("code") != 0:
        raise ListedDateBackfillError(
            "Tushare stock_basic authority returned a business error",
            details={
                "provider": "tushare",
                "api_name": "stock_basic",
                "provider_error_code": document.get("code"),
            },
        )
    data = document.get("data")
    fields = data.get("fields") if isinstance(data, dict) else None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(fields, list) or not isinstance(items, list):
        raise ListedDateBackfillError("Tushare stock_basic authority response is incomplete")
    normalized_fields = [str(field) for field in fields]
    normalized_rows = [
        item for item in items if isinstance(item, list) and len(item) == len(normalized_fields)
    ]
    if len(normalized_rows) != len(items):
        raise ListedDateBackfillError("Tushare stock_basic authority contains malformed rows")
    return pd.DataFrame(normalized_rows, columns=normalized_fields)


def _normalized_mapping_sha256(mapping: Mapping[str, str]) -> str:
    normalized = json.dumps(
        sorted(mapping.items()),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def _futu_provider_map(
    client: FutuBasicInfoClient,
    *,
    target_symbols: frozenset[str],
) -> tuple[dict[str, str], dict[str, Any]]:
    mapping: dict[str, str] = {}
    unavailable_target_dates: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    rows_by_market: dict[str, int] = {}
    for market, market_constant in _FUTU_MARKETS:
        payload = client.quote_call_raw(
            "get_stock_basicinfo",
            kwargs={
                "market": {"__futu_constant__": market_constant},
                "stock_type": {"__futu_constant__": "SecurityType.STOCK"},
            },
        )
        if not isinstance(payload, pd.DataFrame):
            raise ListedDateBackfillError(
                "Futu get_stock_basicinfo returned a non-DataFrame payload",
                details={"market": market, "payload_type": type(payload).__name__},
            )
        required_columns = {"code", "listing_date", "stock_type"}
        missing_columns = sorted(required_columns - set(payload.columns))
        if missing_columns:
            raise ListedDateBackfillError(
                "Futu basic-info payload is missing required columns",
                details={"market": market, "missing_columns": missing_columns},
            )
        if payload.empty:
            raise ListedDateBackfillError(
                "Futu basic-info payload is unexpectedly empty",
                details={"market": market},
            )
        rows_by_market[market] = len(payload)
        for index, row in payload.iterrows():
            raw_code = row["code"]
            if not isinstance(raw_code, str):
                raise ListedDateBackfillError(
                    "Futu basic-info code must be text",
                    details={"market": market, "row_index": str(index)},
                )
            code_match = _CODE_PATTERN.fullmatch(raw_code)
            if code_match is None or code_match.group(1) != market:
                raise ListedDateBackfillError(
                    "Futu basic-info code is outside the requested SH/SZ market",
                    details={
                        "market": market,
                        "row_index": str(index),
                        "code": raw_code,
                    },
                )
            if raw_code in seen_codes:
                raise ListedDateBackfillError(
                    "Futu returned a duplicate SH/SZ stock code",
                    details={"code": raw_code},
                )
            seen_codes.add(raw_code)
            if row["stock_type"] != "STOCK":
                raise ListedDateBackfillError(
                    "Futu basic-info row is not SecurityType.STOCK",
                    details={
                        "market": market,
                        "row_index": str(index),
                        "code": raw_code,
                        "stock_type": str(row["stock_type"]),
                    },
                )
            symbol = code_match.group(2)
            expected_market = _market_for_symbol(symbol)
            if expected_market is None:
                continue
            if expected_market != market:
                raise ListedDateBackfillError(
                    "Futu A-share code prefix conflicts with its requested market",
                    details={
                        "code": raw_code,
                        "expected_market": expected_market,
                        "actual_market": market,
                    },
                )
            if symbol not in target_symbols:
                continue
            try:
                parsed_date = _strict_iso_date(
                    row["listing_date"],
                    context=f"Futu[{raw_code}].listing_date",
                )
            except ListedDateBackfillError:
                if str(row["listing_date"]) != "1970-01-01":
                    raise
                unavailable_target_dates.append(
                    {
                        "symbol": symbol,
                        "futu_code": raw_code,
                        "provider_value": "1970-01-01",
                        "reason": "futu_sentinel_date",
                    }
                )
                continue
            mapping[symbol] = parsed_date.isoformat()

    return mapping, {
        "authority": "futu",
        "method": "get_stock_basicinfo",
        "markets": ["SH", "SZ"],
        "security_type": "STOCK",
        "quote_calls": 2,
        "rows_by_market": rows_by_market,
        "target_symbols": len(target_symbols),
        "target_rows_found": len(mapping),
        "unavailable_target_dates": unavailable_target_dates,
        "eligible_a_share_rows": len(mapping),
        "normalized_sha256": _normalized_mapping_sha256(mapping),
    }


def _tushare_provider_map(
    fetcher: TushareStockBasicFetcher,
    *,
    token: str,
    target_symbols: frozenset[str],
) -> tuple[dict[str, str], dict[str, Any]]:
    try:
        payload = fetcher(token)
    except ListedDateBackfillError:
        raise
    except Exception as exc:
        raise ListedDateBackfillError(
            "Tushare stock_basic authority call failed",
            details={
                "provider": "tushare",
                "api_name": "stock_basic",
                "provider_error_type": type(exc).__name__,
            },
        ) from exc

    if not isinstance(payload, pd.DataFrame):
        raise ListedDateBackfillError(
            "Tushare stock_basic returned a non-DataFrame payload",
            details={"payload_type": type(payload).__name__},
        )
    required_columns = {
        "ts_code",
        "symbol",
        "exchange",
        "list_status",
        "list_date",
    }
    missing_columns = sorted(required_columns - set(payload.columns))
    if missing_columns:
        raise ListedDateBackfillError(
            "Tushare stock_basic payload is missing required columns",
            details={"missing_columns": missing_columns},
        )
    if payload.empty:
        raise ListedDateBackfillError("Tushare stock_basic BSE payload is unexpectedly empty")

    mapping: dict[str, str] = {}
    seen_codes: set[str] = set()
    seen_symbols: set[str] = set()
    for index, row in payload.iterrows():
        raw_ts_code = row["ts_code"]
        raw_symbol = row["symbol"]
        raw_exchange = row["exchange"]
        raw_list_status = row["list_status"]
        if not isinstance(raw_ts_code, str):
            raise ListedDateBackfillError(
                "Tushare stock_basic ts_code must be text",
                details={"row_index": str(index)},
            )
        code_match = _TUSHARE_BSE_CODE_PATTERN.fullmatch(raw_ts_code)
        if code_match is None:
            raise ListedDateBackfillError(
                "Tushare stock_basic ts_code must be an exact six-digit .BJ code",
                details={"row_index": str(index), "ts_code": raw_ts_code},
            )
        if not isinstance(raw_symbol, str) or _BSE_A_SHARE_PATTERN.fullmatch(raw_symbol) is None:
            raise ListedDateBackfillError(
                "Tushare stock_basic symbol is not an exact BSE A-share symbol",
                details={"row_index": str(index), "symbol": str(raw_symbol)},
            )
        if code_match.group(1) != raw_symbol:
            raise ListedDateBackfillError(
                "Tushare stock_basic ts_code and symbol disagree",
                details={
                    "row_index": str(index),
                    "ts_code": raw_ts_code,
                    "symbol": raw_symbol,
                },
            )
        if raw_exchange != "BSE":
            raise ListedDateBackfillError(
                "Tushare stock_basic exchange must be BSE",
                details={
                    "row_index": str(index),
                    "ts_code": raw_ts_code,
                    "exchange": str(raw_exchange),
                },
            )
        if raw_list_status != "L":
            raise ListedDateBackfillError(
                "Tushare stock_basic list_status must be L",
                details={
                    "row_index": str(index),
                    "ts_code": raw_ts_code,
                    "list_status": str(raw_list_status),
                },
            )
        if raw_ts_code in seen_codes or raw_symbol in seen_symbols:
            raise ListedDateBackfillError(
                "Tushare returned a duplicate BSE stock code",
                details={"ts_code": raw_ts_code, "symbol": raw_symbol},
            )
        seen_codes.add(raw_ts_code)
        seen_symbols.add(raw_symbol)
        parsed_date = _strict_tushare_date(
            row["list_date"],
            context=f"Tushare[{raw_ts_code}].list_date",
        )
        if raw_symbol in target_symbols:
            mapping[raw_symbol] = parsed_date.isoformat()

    unresolved_target_symbols = sorted(target_symbols - mapping.keys())
    return mapping, {
        "authority": "tushare",
        "api_name": "stock_basic",
        "params": {"exchange": "BSE", "list_status": "L"},
        "fields": _TUSHARE_STOCK_BASIC_FIELDS.split(","),
        "api_calls": 1,
        "retry_attempts": 0,
        "payload_rows": len(payload),
        "target_symbols": len(target_symbols),
        "target_rows_found": len(mapping),
        "unresolved_target_symbols": unresolved_target_symbols,
        "normalized_sha256": _normalized_mapping_sha256(mapping),
    }


def _plan(
    eligible_rows: tuple[dict[str, Any], ...],
    provider_dates: Mapping[str, str],
    *,
    as_of_date: date,
) -> tuple[list[dict[str, str | None]], dict[str, Any]]:
    candidates: list[dict[str, str | None]] = []
    unresolved: list[str] = []
    unverifiable_existing: list[str] = []
    mismatches: list[dict[str, str]] = []
    existing_checked = 0
    for row in eligible_rows:
        symbol = str(row["symbol"])
        authority = str(row["_authority"])
        authority_market = str(row["_authority_market"])
        authority_code = _authority_code(symbol, authority_market)
        provider_date = provider_dates.get(symbol)
        if provider_date is None:
            if _missing(row["listed_date"]):
                unresolved.append(symbol)
            else:
                unverifiable_existing.append(symbol)
            continue
        parsed_provider_date = _strict_iso_date(
            provider_date,
            context=f"provider_dates[{symbol}]",
        )
        if parsed_provider_date > as_of_date:
            raise ListedDateBackfillError(
                "listing-date authority returned a future date",
                details={
                    "symbol": symbol,
                    "authority": authority,
                    "listed_date": parsed_provider_date.isoformat(),
                },
            )
        current = row["listed_date"]
        if _missing(current):
            candidates.append(
                {
                    "symbol": symbol,
                    "authority": authority,
                    "authority_code": authority_code,
                    "old_listed_date": None if current is None else str(current),
                    "new_listed_date": provider_date,
                }
            )
            continue
        existing_checked += 1
        current_date = _strict_iso_date(
            current,
            context=f"securities[{symbol}].listed_date",
        ).isoformat()
        if current_date != provider_date:
            mismatches.append(
                {
                    "symbol": symbol,
                    "authority": authority,
                    "authority_code": authority_code,
                    "database_listed_date": current_date,
                    "authority_listed_date": provider_date,
                }
            )

    checks = {
        "eligible_database_rows": len(eligible_rows),
        "existing_values_checked": existing_checked,
        "existing_value_mismatches": mismatches,
        "existing_values_without_provider_authority": sorted(unverifiable_existing),
        "unresolved_symbols": sorted(unresolved),
        "missing_values_to_update": len(candidates),
    }
    if unverifiable_existing or mismatches:
        raise ListedDateBackfillError(
            "listing-date providers do not form a complete, equal authority "
            "set for existing values",
            details=checks,
        )
    return candidates, checks


def _verify_backup_safety(
    backup: Mapping[str, Any],
    verified: Mapping[str, Any],
    safety: Mapping[str, int],
) -> None:
    if verified.get("verified") is not True:
        raise ListedDateBackfillError("database backup did not verify")
    if (
        verified.get("backup_path") != backup.get("backup_path")
        or verified.get("sha256") != backup.get("sha256")
        or verified.get("quick_check") != "ok"
    ):
        raise ListedDateBackfillError(
            "database backup verification does not match the created snapshot"
        )
    critical_tables = verified.get("critical_tables")
    if not isinstance(critical_tables, Mapping):
        raise ListedDateBackfillError("verified backup lacks critical-table evidence")
    for table in ("trade_proposals", "broker_orders"):
        evidence = critical_tables.get(table)
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("present") is not True
            or evidence.get("rows") != safety[table]
        ):
            raise ListedDateBackfillError(
                "verified backup trading-safety count mismatch",
                details={
                    "table": table,
                    "expected": safety[table],
                    "observed": evidence,
                },
            )


def _apply_candidates(
    *,
    database: Path,
    columns: tuple[str, ...],
    rows_before: tuple[dict[str, Any], ...],
    candidates: list[dict[str, str | None]],
    safety_before: dict[str, int],
    as_of_date: date,
) -> tuple[dict[str, int], str]:
    candidate_dates = {
        str(candidate["symbol"]): str(candidate["new_listed_date"]) for candidate in candidates
    }
    connection = sqlite3.connect(database, timeout=15.0)
    try:
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            if _quick_check(connection) != "ok":
                raise ListedDateBackfillError("locked pre-mutation PRAGMA quick_check failed")
            if _safety_snapshot(connection) != safety_before:
                raise ListedDateBackfillError(
                    "trading or running-job safety state changed after preflight"
                )
            locked_rows = _security_rows(connection, columns)
            if locked_rows != rows_before:
                raise ListedDateBackfillError(
                    "securities rows changed after authority validation and backup"
                )
            _eligible_rows(locked_rows, as_of_date=as_of_date)

            for symbol, listed_date in sorted(candidate_dates.items()):
                cursor = connection.execute(
                    """
                    UPDATE securities
                    SET listed_date = ?
                    WHERE symbol = ?
                      AND (listed_date IS NULL OR trim(listed_date) = '')
                    """,
                    (listed_date, symbol),
                )
                if cursor.rowcount != 1:
                    raise ListedDateBackfillError(
                        "missing-only listed_date update affected an unexpected row count",
                        details={"symbol": symbol, "rowcount": cursor.rowcount},
                    )

            rows_after = _security_rows(connection, columns)
            expected_rows: list[dict[str, Any]] = []
            for row in rows_before:
                expected = dict(row)
                symbol = str(row["symbol"])
                if symbol in candidate_dates:
                    expected["listed_date"] = candidate_dates[symbol]
                expected_rows.append(expected)
            if rows_after != tuple(expected_rows):
                raise ListedDateBackfillError(
                    "a Security field other than the approved listed_date values changed"
                )
            safety_after = _safety_snapshot(connection)
            if safety_after != safety_before:
                raise ListedDateBackfillError(
                    "trading or running-job safety state changed during mutation"
                )
            locked_quick_check = _quick_check(connection)
            if locked_quick_check != "ok":
                raise ListedDateBackfillError(
                    "post-update pre-commit PRAGMA quick_check failed",
                    details={"quick_check": locked_quick_check},
                )
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()

        try:
            quick_after = _quick_check(connection)
        except Exception as exc:
            raise ListedDateBackfillError(
                "post-commit PRAGMA quick_check could not be completed",
                details={"mutation_committed": True},
            ) from exc
        if quick_after != "ok":
            raise ListedDateBackfillError(
                "post-commit PRAGMA quick_check failed",
                details={
                    "quick_check": quick_after,
                    "mutation_committed": True,
                },
            )
        return safety_after, quick_after
    finally:
        connection.close()


def backfill_security_listed_dates(
    *,
    database_path: Path,
    backup_directory: Path,
    evidence_path: Path,
    client: FutuBasicInfoClient,
    tushare_token: str,
    tushare_stock_basic_fetcher: TushareStockBasicFetcher = (fetch_tushare_bse_stock_basic),
    apply: bool = False,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    """Backfill current CN listing dates from Futu SH/SZ and Tushare BSE."""

    started_at = datetime.now(UTC)
    database = database_path.expanduser().resolve()
    evidence_destination = _validate_evidence_path(database, evidence_path)
    effective_as_of = as_of_date or datetime.now(_SHANGHAI).date()
    base_evidence: dict[str, Any] = {
        "operation": "p3_m3_s7_security_listed_date_backfill",
        "mode": "apply" if apply else "dry_run",
        "database": str(database),
        "as_of_date": effective_as_of.isoformat(),
        "started_at": started_at.isoformat(),
        "safety_contract": {
            "markets": ["SH", "SZ", "BSE"],
            "provider_methods": [
                "Futu get_stock_basicinfo",
                "Tushare stock_basic",
            ],
            "provider_surfaces": ["quote_read_only", "market_metadata_read_only"],
            "provider_calls": {
                "futu_quote_calls": 2,
                "tushare_api_calls": 1,
                "tushare_retries": 0,
            },
            "database_columns_mutable": ["securities.listed_date"],
            "missing_only": True,
            "scheduler_started": False,
            "trade_api_called": False,
            "baostock_called": False,
        },
    }
    evidence: dict[str, Any] | None = None
    stage = "initialized"
    mutation_committed = False
    try:
        stage = "preflight"
        if not database.is_file():
            raise FileNotFoundError(database)
        resolved_tushare_token = tushare_token.strip()
        if not resolved_tushare_token:
            raise ListedDateBackfillError(
                "Tushare token is required for the BSE stock_basic authority"
            )
        (
            columns,
            all_rows,
            eligible_rows,
            safety_before,
            quick_before,
        ) = _read_preflight(database, as_of_date=effective_as_of)
        application_safety = _application_safety_snapshot()
        futu_target_symbols = frozenset(
            str(row["symbol"]) for row in eligible_rows if row["_authority"] == "futu"
        )
        tushare_target_symbols = frozenset(
            str(row["symbol"]) for row in eligible_rows if row["_authority"] == "tushare"
        )
        tushare_dates, tushare_evidence = _tushare_provider_map(
            tushare_stock_basic_fetcher,
            token=resolved_tushare_token,
            target_symbols=tushare_target_symbols,
        )
        futu_dates, futu_evidence = _futu_provider_map(
            client,
            target_symbols=futu_target_symbols,
        )
        overlap = sorted(futu_dates.keys() & tushare_dates.keys())
        if overlap:
            raise ListedDateBackfillError(
                "listing-date authority maps overlap",
                details={"symbols": overlap},
            )
        provider_dates = {**futu_dates, **tushare_dates}
        provider_evidence = {
            "futu": futu_evidence,
            "tushare": tushare_evidence,
            "calls": {
                "futu_quote_calls": 2,
                "tushare_api_calls": 1,
                "total_read_only_calls": 3,
                "retry_attempts": 0,
            },
            "normalized_sha256": _normalized_mapping_sha256(provider_dates),
        }
        candidates, checks = _plan(
            eligible_rows,
            provider_dates,
            as_of_date=effective_as_of,
        )

        evidence = {
            **base_evidence,
            "provider": provider_evidence,
            "checks": {
                **checks,
                "database_security_rows": len(all_rows),
                "non_current_cn_listed_rows_ignored": len(all_rows) - len(eligible_rows),
                "quick_check_before": quick_before,
                "current_existing_value_equality": True,
                "profile_and_other_fields_mutable": False,
            },
            "candidates": candidates,
            "safety_before": safety_before,
            "application_safety": application_safety,
            "backup": None,
            "stage": "planned",
            "mutation_committed": False,
        }
        if not candidates:
            terminal_status = (
                "complete_with_unresolved" if checks["unresolved_symbols"] else "already_complete"
            )
            evidence.update(
                {
                    "status": terminal_status,
                    "updated_rows": 0,
                    "safety_after": safety_before,
                    "quick_check_after": quick_before,
                    "completed_at": datetime.now(UTC).isoformat(),
                    "stage": "complete",
                }
            )
            _atomic_write_json(evidence_destination, evidence)
            return evidence
        if not apply:
            evidence.update(
                {
                    "status": "dry_run",
                    "updated_rows": 0,
                    "safety_after": safety_before,
                    "quick_check_after": quick_before,
                    "completed_at": datetime.now(UTC).isoformat(),
                    "stage": "dry_run",
                }
            )
            _atomic_write_json(evidence_destination, evidence)
            return evidence

        stage = "backup"
        backup = create_database_backup(
            database,
            backup_directory,
            retain=100_000,
        )
        verified_backup = verify_database_backup(
            Path(str(backup["backup_path"])),
            Path(str(backup["manifest_path"])),
        )
        _verify_backup_safety(backup, verified_backup, safety_before)
        backup_security_snapshot = _verify_backup_security_snapshot(
            Path(str(backup["backup_path"])),
            columns=columns,
            rows_before=all_rows,
        )
        evidence["backup"] = {
            **backup,
            "independent_verification": verified_backup,
            "securities_snapshot": backup_security_snapshot,
        }
        evidence.update(
            {
                "status": "prepared",
                "stage": "prepared",
                "mutation_committed": False,
                "prepared_at": datetime.now(UTC).isoformat(),
            }
        )
        _atomic_write_json(evidence_destination, evidence)
        stage = "prepared"

        try:
            safety_after, quick_after = _apply_candidates(
                database=database,
                columns=columns,
                rows_before=all_rows,
                candidates=candidates,
                safety_before=safety_before,
                as_of_date=effective_as_of,
            )
        except ListedDateBackfillError as exc:
            if exc.details.get("mutation_committed") is True:
                mutation_committed = True
                stage = "committed_verification_failed"
            raise
        mutation_committed = True
        stage = "committed"
        evidence.update(
            {
                "status": (
                    "applied_with_unresolved" if checks["unresolved_symbols"] else "applied"
                ),
                "updated_rows": len(candidates),
                "safety_after": safety_after,
                "quick_check_after": quick_after,
                "completed_at": datetime.now(UTC).isoformat(),
                "stage": "committed",
                "mutation_committed": True,
            }
        )
        _atomic_write_json(evidence_destination, evidence)
        return evidence
    except Exception as exc:
        preserved = evidence or base_evidence
        if (
            isinstance(exc, ListedDateBackfillError)
            and exc.details.get("mutation_committed") is True
        ):
            mutation_committed = True
        blocked = {
            **preserved,
            "status": ("blocked_after_commit" if mutation_committed else "blocked"),
            "stage": stage,
            "mutation_committed": mutation_committed,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "details": exc.details if isinstance(exc, ListedDateBackfillError) else {},
            "completed_at": datetime.now(UTC).isoformat(),
        }
        _atomic_write_json(evidence_destination, blocked)
        raise
