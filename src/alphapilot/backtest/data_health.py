from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from math import ceil, isfinite
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alphapilot.backtest.factor_scope import (
    HISTORICAL_FACTOR_CANDIDATES,
    HISTORY_EXCLUDED_PIT_GAP_FACTORS,
)
from alphapilot.backtest.pit import factor_zscores
from alphapilot.data.provenance import (
    AUDITED_DAILY_BAR_SOURCES,
    AUDITED_SECTOR_FLOW_SOURCES,
)
from alphapilot.engines.factors import FACTOR_SET

REPORT_VERSION = "p3.3-s6-v4"
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
LATEST_CROSS_SECTION_CANDIDATES = 60
FINANCIAL_TARGET_QUARTERS = 40
FINANCIAL_MINIMUM_MATURE_QUARTERS = 20
FINANCIAL_PUBLICATION_LAG_DAYS = 45
MIN_FINANCIAL_DEPTH_RATIO = 0.80
MIN_FINANCIAL_SYMBOL_PASS_RATIO = 0.90
MIN_PROVIDER_PUB_DATE_BASIS_RATIO = 0.95
FINANCIAL_PROVIDER_CADENCE: dict[str, dict[str, Any]] = {
    "roe": {
        "label": "quarterly",
        "expected_quarters": (1, 2, 3, 4),
    },
    "net_profit_yoy": {
        "label": "quarterly",
        "expected_quarters": (1, 2, 3, 4),
    },
    "ocf_to_profit": {
        "label": "quarterly_provider_nullable",
        "expected_quarters": (1, 2, 3, 4),
    },
    "debt_ratio": {
        "label": "quarterly",
        "expected_quarters": (1, 2, 3, 4),
    },
    "revenue_yoy": {
        "label": "semiannual_q2_q4_from_baostock_mb_revenue",
        "expected_quarters": (2, 4),
    },
}
MAX_EXTERNAL_EVIDENCE_BYTES = 64 * 1024
PIT_MANIFEST_SCHEMA_VERSION = "p3.3-s6-local-pit-manifest-v1"
EXTERNAL_PAIRING_SCHEMA_VERSION = "p3.3-s6-external-pit-pairing-v2"
PIT_CHECKED_FIELDS = {
    "daily_bars": ("close", "source", "adj_factor", "adj_source"),
    "financial_indicators": ("value", "source", "available_time"),
    "valuation_daily": (
        "pe_ttm",
        "pb_mrq",
        "ps_ttm",
        "source",
        "available_time",
    ),
}
PIT_NUMERIC_CHECKED_FIELDS = frozenset(
    {"close", "adj_factor", "value", "pe_ttm", "pb_mrq", "ps_ttm"}
)
PIT_NUMERIC_TOLERANCE_POLICY: dict[str, tuple[float, float]] = {
    # (absolute tolerance, relative tolerance)
    "close": (0.01, 0.0001),
    "adj_factor": (0.001, 0.0001),
    "value": (0.0001, 0.001),
    "pe_ttm": (0.01, 0.0001),
    "pb_mrq": (0.01, 0.0001),
    "ps_ttm": (0.01, 0.0001),
}
PIT_ALLOWED_EXTERNAL_SOURCES_BY_TABLE: dict[str, frozenset[str]] = {
    "daily_bars": frozenset(
        {
            "futu-unadjusted-day+futu-hfq-day",
            "sina-unadjusted-day+sina-hfq-day",
        }
    ),
    "financial_indicators": frozenset(
        {
            "eastmoney-f10-main-financial",
            "tushare-fina-indicator",
        }
    ),
    "valuation_daily": frozenset({"eastmoney-stock-value-em"}),
}
PIT_NON_HUMAN_REVIEWER_MARKERS = frozenset({"pending", "trial", "automated"})
PIT_MISSING_EXTERNAL_VALUES = frozenset({"n/a", "na", "null", "none", "—"})
EXTERNAL_PAIRING_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$defs": {
        "numeric_check": {
            "type": "object",
            "additionalProperties": False,
            "required": ["local_value", "external_value", "pass"],
            "properties": {
                "local_value": {"type": ["number", "null"]},
                "external_value": {
                    "oneOf": [
                        {"type": "number"},
                        {"type": "string", "minLength": 1},
                    ]
                },
                "pass": {"const": True},
                "tolerance": {"type": "number", "minimum": 0},
            },
        },
        "string_check": {
            "type": "object",
            "additionalProperties": False,
            "required": ["local_value", "external_value", "pass"],
            "properties": {
                "local_value": {"type": "string", "minLength": 1},
                "external_value": {"type": "string", "minLength": 1},
                "pass": {"const": True},
            },
        },
    },
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "pit_manifest_schema_version",
        "pit_manifest_sha256",
        "approved",
        "reviewed_at",
        "reviewer_role",
        "seed",
        "sample_size_per_table",
        "samples",
    ],
    "properties": {
        "schema_version": {"const": EXTERNAL_PAIRING_SCHEMA_VERSION},
        "pit_manifest_schema_version": {
            "const": PIT_MANIFEST_SCHEMA_VERSION,
        },
        "pit_manifest_sha256": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
        },
        "approved": {"const": True},
        "reviewed_at": {"type": "string", "minLength": 1},
        "reviewer_role": {"type": "string", "minLength": 1},
        "seed": {"type": "integer"},
        "sample_size_per_table": {"type": "integer", "minimum": 1},
        "samples": {
            "type": "array",
            "minItems": 1,
            "items": {
                "oneOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "table",
                            "key",
                            "verdict",
                            "external_source",
                            "checked_values",
                        ],
                        "properties": {
                            "table": {"const": "daily_bars"},
                            "key": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["symbol", "trade_date"],
                                "properties": {
                                    "symbol": {"type": "string", "minLength": 1},
                                    "trade_date": {"type": "string", "minLength": 1},
                                },
                            },
                            "verdict": {"const": "match"},
                            "external_source": {"type": "string", "minLength": 1},
                            "checked_values": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": list(PIT_CHECKED_FIELDS["daily_bars"]),
                                "properties": {
                                    "close": {"$ref": "#/$defs/numeric_check"},
                                    "source": {"$ref": "#/$defs/string_check"},
                                    "adj_factor": {
                                        "$ref": "#/$defs/numeric_check"
                                    },
                                    "adj_source": {
                                        "$ref": "#/$defs/string_check"
                                    },
                                },
                            },
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "table",
                            "key",
                            "verdict",
                            "external_source",
                            "checked_values",
                        ],
                        "properties": {
                            "table": {"const": "financial_indicators"},
                            "key": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["symbol", "report_period", "metric"],
                                "properties": {
                                    "symbol": {"type": "string", "minLength": 1},
                                    "report_period": {
                                        "type": "string",
                                        "minLength": 1,
                                    },
                                    "metric": {"type": "string", "minLength": 1},
                                },
                            },
                            "verdict": {"const": "match"},
                            "external_source": {"type": "string", "minLength": 1},
                            "checked_values": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": list(
                                    PIT_CHECKED_FIELDS["financial_indicators"]
                                ),
                                "properties": {
                                    "value": {"$ref": "#/$defs/numeric_check"},
                                    "source": {"$ref": "#/$defs/string_check"},
                                    "available_time": {
                                        "$ref": "#/$defs/string_check"
                                    },
                                },
                            },
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "table",
                            "key",
                            "verdict",
                            "external_source",
                            "checked_values",
                        ],
                        "properties": {
                            "table": {"const": "valuation_daily"},
                            "key": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["symbol", "trade_date"],
                                "properties": {
                                    "symbol": {"type": "string", "minLength": 1},
                                    "trade_date": {"type": "string", "minLength": 1},
                                },
                            },
                            "verdict": {"const": "match"},
                            "external_source": {"type": "string", "minLength": 1},
                            "checked_values": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": list(
                                    PIT_CHECKED_FIELDS["valuation_daily"]
                                ),
                                "properties": {
                                    "pe_ttm": {"$ref": "#/$defs/numeric_check"},
                                    "pb_mrq": {"$ref": "#/$defs/numeric_check"},
                                    "ps_ttm": {"$ref": "#/$defs/numeric_check"},
                                    "source": {"$ref": "#/$defs/string_check"},
                                    "available_time": {
                                        "$ref": "#/$defs/string_check"
                                    },
                                },
                            },
                        },
                    },
                ]
            },
        },
    },
}
REQUIRED_FINANCIAL_FACTORS = (
    "roe",
    "net_profit_yoy",
    "ocf_to_profit",
    "debt_ratio",
    "revenue_yoy",
)
HISTORICAL_FACTORS = HISTORICAL_FACTOR_CANDIDATES
PRICE_FACTORS = frozenset(
    {
        "momentum_20d",
        "momentum_60d",
        "volatility_20d",
        "turnover_change_5d",
    }
)
VALUATION_FACTORS = frozenset({"pe_percentile", "pb_percentile"})
FINANCIAL_FACTORS = frozenset(REQUIRED_FINANCIAL_FACTORS)
FLOW_FACTORS = frozenset({"net_inflow_5d"})
AUDITED_ADJ_FACTOR_SOURCES = frozenset(
    {"baostock-hfq", "sina-hfq", "tushare"}
)
AUDITED_FINANCIAL_SOURCES = frozenset({"baostock"})
AUDITED_VALUATION_SOURCES = frozenset({"em", "baostock"})
M3_SECTOR_FLOW_SOURCE = "futu-daily"
REQUIRED_TABLES = frozenset(
    {
        "securities",
        "daily_bars",
        "adj_factors",
        "financial_indicators",
        "valuation_daily",
        "sector_flow_daily",
        "sector_constituents",
        "sector_constituent_snapshots",
    }
)

FactorProbe = Callable[[Session, date], pd.DataFrame]


def _database_uri(database_path: Path) -> str:
    resolved = database_path.expanduser().resolve()
    return f"file:{quote(str(resolved), safe='/')}?mode=ro"


def _open_readonly_dbapi(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        _database_uri(database_path),
        uri=True,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=15000")
    return connection


@contextmanager
def readonly_connection(database_path: Path) -> Iterator[sqlite3.Connection]:
    """Open an existing SQLite database with writes rejected by SQLite itself."""

    if not database_path.expanduser().resolve().is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {database_path}")
    connection = _open_readonly_dbapi(database_path)
    try:
        yield connection
    finally:
        connection.close()


def _one(connection: sqlite3.Connection, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Row:
    row = connection.execute(sql, parameters).fetchone()
    if row is None:
        raise RuntimeError("health-check query unexpectedly returned no row")
    return cast(sqlite3.Row, row)


def _rows(
    connection: sqlite3.Connection,
    sql: str,
    parameters: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(sql, parameters).fetchall()]


def _in_clause(values: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    ordered = tuple(sorted(values))
    return ", ".join("?" for _ in ordered), ordered


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _duplicate_groups(
    connection: sqlite3.Connection,
    *,
    table: str,
    key_columns: str,
) -> int:
    allowed = {
        ("daily_bars", "symbol, trade_date"),
        ("adj_factors", "symbol, trade_date"),
        ("financial_indicators", "symbol, report_period, metric"),
        ("valuation_daily", "symbol, trade_date"),
        ("sector_flow_daily", "plate_code, trade_date"),
        (
            "sector_constituent_snapshots",
            "plate_code, symbol, as_of_date",
        ),
    }
    if (table, key_columns) not in allowed:
        raise ValueError("unsupported duplicate-key audit")
    row = _one(
        connection,
        f"""
        SELECT COUNT(*) AS groups_count
        FROM (
          SELECT {key_columns}
          FROM {table}
          GROUP BY {key_columns}
          HAVING COUNT(*) > 1
        )
        """,
    )
    return int(row["groups_count"])


def _source_counts(
    connection: sqlite3.Connection,
    *,
    table: str,
) -> dict[str, int]:
    allowed = {
        "daily_bars",
        "adj_factors",
        "financial_indicators",
        "valuation_daily",
        "sector_flow_daily",
    }
    if table not in allowed:
        raise ValueError("unsupported source audit")
    rows = connection.execute(
        f"""
        SELECT COALESCE(source, '<null>') AS source, COUNT(*) AS rows_count
        FROM {table}
        GROUP BY source
        ORDER BY source
        """
    ).fetchall()
    return {str(row["source"]): int(row["rows_count"]) for row in rows}


def _invalid_source_rows(
    source_counts: dict[str, int],
    allowed_sources: frozenset[str],
) -> int:
    return sum(
        rows_count
        for source, rows_count in source_counts.items()
        if source not in allowed_sources
    )


def _latest_trade_date_candidates(
    connection: sqlite3.Connection,
    *,
    limit: int = LATEST_CROSS_SECTION_CANDIDATES,
) -> list[str]:
    placeholders, sources = _in_clause(tuple(AUDITED_DAILY_BAR_SOURCES))
    rows = connection.execute(
        f"""
        SELECT trade_date
        FROM daily_bars
        WHERE symbol = 'SH.000001'
          AND source IN ({placeholders})
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (*sources, limit),
    ).fetchall()
    if not rows:
        rows = connection.execute(
            f"""
            SELECT DISTINCT trade_date
            FROM daily_bars
            WHERE source IN ({placeholders})
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (*sources, limit),
        ).fetchall()
    return [str(row["trade_date"]) for row in rows]


def _latest_cross_section(
    connection: sqlite3.Connection,
    *,
    table: str,
    universe_size: int,
    minimum_coverage: float,
    candidate_dates: Sequence[str],
    source_filter: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    if table not in {"daily_bars", "adj_factors", "valuation_daily"}:
        raise ValueError("unsupported cross-section audit")
    source_sql = ""
    source_parameters: tuple[Any, ...] = ()
    if source_filter is not None:
        placeholders, sources = _in_clause(source_filter)
        source_sql = f" AND observed.source IN ({placeholders})"
        source_parameters = sources
    threshold = max(1, ceil(universe_size * minimum_coverage))
    count_sql = f"""
        SELECT COUNT(*) AS entities
        FROM securities AS security
        WHERE security.market = 'CN'
          AND security.list_status = 'listed'
          AND EXISTS (
            SELECT 1
            FROM {table} AS observed
            WHERE observed.symbol = security.symbol
              AND observed.trade_date = ?
              {source_sql}
          )
        """
    query_plan: list[str] = []
    checked: list[dict[str, Any]] = []
    for candidate in candidate_dates:
        parameters = (candidate, *source_parameters)
        if not query_plan:
            plan_rows = connection.execute(
                f"EXPLAIN QUERY PLAN {count_sql}",
                parameters,
            ).fetchall()
            query_plan = [str(row["detail"]) for row in plan_rows]
        row = _one(
            connection,
            count_sql,
            parameters,
        )
        entities = int(row["entities"])
        checked.append({"date": candidate, "entities": entities})
        if entities >= threshold:
            return {
                "date": candidate,
                "entities": entities,
                "minimum_entities": threshold,
                "qualified": True,
                "candidates_checked": checked,
                "query_plan": query_plan,
            }
    return {
        "date": None,
        "entities": max(
            (int(item["entities"]) for item in checked),
            default=0,
        ),
        "minimum_entities": threshold,
        "qualified": False,
        "latest_candidate_date": candidate_dates[0] if candidate_dates else None,
        "candidates_checked": checked,
        "query_plan": query_plan,
    }


def _daily_bar_audit(
    connection: sqlite3.Connection,
    *,
    universe_size: int,
    minimum_market_coverage: float,
    candidate_dates: Sequence[str],
) -> dict[str, Any]:
    placeholders, sources = _in_clause(tuple(AUDITED_DAILY_BAR_SOURCES))
    summary = _one(
        connection,
        f"""
        SELECT COUNT(*) AS rows_count,
               COUNT(DISTINCT symbol) AS symbols,
               COUNT(DISTINCT trade_date) AS dates,
               MIN(trade_date) AS min_date,
               MAX(trade_date) AS max_date,
               SUM(CASE WHEN source IN ({placeholders}) THEN 1 ELSE 0 END) AS audited_rows,
               SUM(CASE WHEN close IS NULL OR close <= 0 OR high < low THEN 1 ELSE 0 END)
                   AS invalid_price_rows
        FROM daily_bars
        """,
        sources,
    )
    source_counts = _source_counts(connection, table="daily_bars")
    rows_count = int(summary["rows_count"])
    audited_rows = int(summary["audited_rows"] or 0)
    return {
        "rows": rows_count,
        "symbols": int(summary["symbols"]),
        "dates": int(summary["dates"]),
        "date_range": [summary["min_date"], summary["max_date"]],
        "source_counts": source_counts,
        "audited_source_rows": audited_rows,
        "audited_source_ratio": _ratio(audited_rows, rows_count),
        "invalid_source_rows": _invalid_source_rows(
            source_counts,
            AUDITED_DAILY_BAR_SOURCES,
        ),
        "invalid_price_rows": int(summary["invalid_price_rows"] or 0),
        "duplicate_key_groups": _duplicate_groups(
            connection,
            table="daily_bars",
            key_columns="symbol, trade_date",
        ),
        "latest_broad_cross_section": _latest_cross_section(
            connection,
            table="daily_bars",
            universe_size=universe_size,
            minimum_coverage=minimum_market_coverage,
            candidate_dates=candidate_dates,
            source_filter=tuple(AUDITED_DAILY_BAR_SOURCES),
        ),
    }


def _adj_factor_audit(
    connection: sqlite3.Connection,
    *,
    universe_size: int,
    minimum_market_coverage: float,
    candidate_dates: Sequence[str],
) -> dict[str, Any]:
    summary = _one(
        connection,
        """
        SELECT COUNT(*) AS rows_count,
               COUNT(DISTINCT symbol) AS symbols,
               COUNT(DISTINCT trade_date) AS dates,
               MIN(trade_date) AS min_date,
               MAX(trade_date) AS max_date,
               SUM(CASE WHEN adj_factor IS NULL OR adj_factor <= 0 THEN 1 ELSE 0 END)
                   AS invalid_factor_rows
        FROM adj_factors
        """,
    )
    source_counts = _source_counts(connection, table="adj_factors")
    return {
        "rows": int(summary["rows_count"]),
        "symbols": int(summary["symbols"]),
        "dates": int(summary["dates"]),
        "date_range": [summary["min_date"], summary["max_date"]],
        "source_counts": source_counts,
        "invalid_source_rows": _invalid_source_rows(
            source_counts,
            AUDITED_ADJ_FACTOR_SOURCES,
        ),
        "invalid_factor_rows": int(summary["invalid_factor_rows"] or 0),
        "duplicate_key_groups": _duplicate_groups(
            connection,
            table="adj_factors",
            key_columns="symbol, trade_date",
        ),
        "latest_broad_cross_section": _latest_cross_section(
            connection,
            table="adj_factors",
            universe_size=universe_size,
            minimum_coverage=minimum_market_coverage,
            candidate_dates=candidate_dates,
        ),
    }


def _daily_adj_key_audit(connection: sqlite3.Connection) -> dict[str, int]:
    placeholders, sources = _in_clause(tuple(AUDITED_DAILY_BAR_SOURCES))
    missing = _one(
        connection,
        f"""
        SELECT COUNT(*) AS rows_count
        FROM securities AS security
        JOIN daily_bars AS bars
          ON bars.symbol = security.symbol
        LEFT JOIN adj_factors AS factors
          ON factors.symbol = bars.symbol
         AND factors.trade_date = bars.trade_date
        WHERE security.market = 'CN'
          AND security.list_status = 'listed'
          AND bars.source IN ({placeholders})
          AND factors.id IS NULL
        """,
        sources,
    )
    extra = _one(
        connection,
        f"""
        SELECT COUNT(*) AS rows_count
        FROM securities AS security
        JOIN adj_factors AS factors
          ON factors.symbol = security.symbol
        LEFT JOIN daily_bars AS bars
          ON bars.symbol = factors.symbol
         AND bars.trade_date = factors.trade_date
         AND bars.source IN ({placeholders})
        WHERE security.market = 'CN'
          AND security.list_status = 'listed'
          AND bars.id IS NULL
        """,
        sources,
    )
    return {
        "audited_daily_keys_without_adj": int(missing["rows_count"]),
        "adj_keys_without_audited_daily": int(extra["rows_count"]),
    }


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _expected_financial_availability(payload: dict[str, Any]) -> datetime | None:
    basis = payload.get("available_time_basis")
    if basis == "provider_pub_date_end_of_day":
        raw_dates = payload.get("pub_dates")
        if not isinstance(raw_dates, list) or not raw_dates:
            return None
        basis_date = _parse_date(raw_dates[0])
        if basis_date is None:
            return None
        local = datetime.combine(
            basis_date + timedelta(days=1),
            time.min,
            tzinfo=MARKET_TIMEZONE,
        )
        return local.astimezone(UTC)
    if basis == "stat_date_plus_45_days":
        basis_date = _parse_date(payload.get("stat_date"))
        if basis_date is None:
            return None
        local = datetime.combine(
            basis_date + timedelta(days=45),
            time.min,
            tzinfo=MARKET_TIMEZONE,
        )
        return local.astimezone(UTC)
    return None


def _financial_pit_audit(connection: sqlite3.Connection) -> dict[str, Any]:
    basis_counts: dict[str, int] = {}
    anomaly_counts = {
        "malformed_payload": 0,
        "missing_available_time": 0,
        "unsupported_basis": 0,
        "basis_fields_missing": 0,
        "available_time_mismatch": 0,
    }
    examples: list[dict[str, Any]] = []
    cursor = connection.execute(
        """
        SELECT symbol, report_period, metric, available_time, payload
        FROM financial_indicators
        """
    )
    for row in cursor:
        raw_payload = row["payload"]
        malformed_payload = False
        try:
            payload = json.loads(str(raw_payload)) if raw_payload is not None else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
            malformed_payload = True
        if not isinstance(payload, dict):
            payload = {}
            malformed_payload = True
        basis = str(payload.get("available_time_basis") or "<missing>")
        basis_counts[basis] = basis_counts.get(basis, 0) + 1
        available_time = _parse_datetime(row["available_time"])
        expected = _expected_financial_availability(payload)
        anomaly: str | None = None
        if malformed_payload:
            anomaly = "malformed_payload"
        elif available_time is None:
            anomaly = "missing_available_time"
        elif basis not in {
            "provider_pub_date_end_of_day",
            "stat_date_plus_45_days",
        }:
            anomaly = "unsupported_basis"
        elif expected is None:
            anomaly = "basis_fields_missing"
        elif abs((available_time - expected).total_seconds()) > 1:
            anomaly = "available_time_mismatch"
        if anomaly is not None:
            anomaly_counts[anomaly] += 1
            if len(examples) < 5:
                examples.append(
                    {
                        "symbol": row["symbol"],
                        "report_period": row["report_period"],
                        "metric": row["metric"],
                        "anomaly": anomaly,
                        "available_time": row["available_time"],
                    }
                )
    anomaly_rows = sum(anomaly_counts.values())
    total_rows = sum(basis_counts.values())
    provider_rows = basis_counts.get("provider_pub_date_end_of_day", 0)
    return {
        "available_time_basis_counts": dict(sorted(basis_counts.items())),
        "anomaly_counts": anomaly_counts,
        "anomaly_rows": anomaly_rows,
        "provider_pub_date_end_of_day_rows": provider_rows,
        "provider_pub_date_end_of_day_ratio": _ratio(provider_rows, total_rows),
        "examples": examples,
    }


def _quarter_end(period: str) -> date | None:
    if len(period) != 6 or period[4] != "Q":
        return None
    try:
        year = int(period[:4])
        quarter = int(period[5])
    except ValueError:
        return None
    month_day = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}.get(quarter)
    if month_day is None:
        return None
    return date(year, month_day[0], month_day[1])


def _completed_quarter_labels(count: int, *, as_of_date: date) -> list[str]:
    year = as_of_date.year
    quarter = (as_of_date.month - 1) // 3 + 1
    periods: list[str] = []
    for _ in range(count):
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
        periods.append(f"{year}Q{quarter}")
    periods.reverse()
    return periods


def _periods(value: object) -> set[str]:
    return {
        item
        for item in str(value or "").split(",")
        if _quarter_end(item) is not None
    }


def _depth_bucket(periods: int) -> str:
    if periods < 10:
        return "0-9"
    if periods < 20:
        return "10-19"
    if periods < 30:
        return "20-29"
    if periods < 40:
        return "30-39"
    return "40+"


def _financial_provider_supports(symbol: str, board: object) -> bool:
    return (
        len(symbol) == 6
        and symbol.isdigit()
        and str(board or "") != "北交所"
        and not symbol.startswith(("4", "8", "92"))
    )


def _financial_depth_audit(
    connection: sqlite3.Connection,
    *,
    as_of_date: date,
) -> dict[str, Any]:
    target_periods = _completed_quarter_labels(
        FINANCIAL_TARGET_QUARTERS,
        as_of_date=as_of_date,
    )
    publishable_periods = [
        period
        for period in target_periods
        if (
            (period_end := _quarter_end(period)) is not None
            and period_end + timedelta(days=FINANCIAL_PUBLICATION_LAG_DAYS)
            <= as_of_date
        )
    ]
    target_set = set(target_periods)
    publishable_set = set(publishable_periods)
    placeholders, audited_sources = _in_clause(tuple(AUDITED_DAILY_BAR_SOURCES))
    security_rows = connection.execute(
        f"""
        SELECT security.symbol,
               security.board,
               security.listed_date,
               (
                 SELECT MIN(bars.trade_date)
                 FROM daily_bars AS bars
                 WHERE bars.symbol = security.symbol
                   AND bars.source IN ({placeholders})
               ) AS first_audited_bar
        FROM securities AS security
        WHERE security.market = 'CN'
          AND security.list_status = 'listed'
        ORDER BY security.symbol
        """,
        audited_sources,
    ).fetchall()
    listing_info: dict[str, tuple[date | None, str]] = {}
    listing_basis_counts = {
        "security_master": 0,
        "first_audited_bar": 0,
        "unknown": 0,
    }
    provider_unsupported_symbols = 0
    for row in security_rows:
        symbol = str(row["symbol"])
        if not _financial_provider_supports(symbol, row["board"]):
            provider_unsupported_symbols += 1
            continue
        listed_date = _parse_date(row["listed_date"])
        basis = "security_master"
        if listed_date is None:
            listed_date = _parse_date(row["first_audited_bar"])
            basis = "first_audited_bar" if listed_date is not None else "unknown"
        listing_info[symbol] = (listed_date, basis)
        listing_basis_counts[basis] += 1
    rows = connection.execute(
        """
        SELECT symbol,
               metric,
               GROUP_CONCAT(DISTINCT report_period) AS observed_periods,
               GROUP_CONCAT(
                 DISTINCT CASE WHEN value IS NOT NULL THEN report_period END
               ) AS non_null_periods
        FROM financial_indicators
        WHERE metric IN ('roe', 'net_profit_yoy', 'ocf_to_profit',
                         'debt_ratio', 'revenue_yoy')
        GROUP BY symbol, metric
        ORDER BY metric, symbol
        """
    ).fetchall()
    by_metric_symbol = {
        (str(row["metric"]), str(row["symbol"])): {
            "observed": _periods(row["observed_periods"]).intersection(target_set),
            "non_null": _periods(row["non_null_periods"]).intersection(target_set),
        }
        for row in rows
    }
    metrics: dict[str, Any] = {}
    unknown_listing_date_symbols = listing_basis_counts["unknown"]
    symbols_without_publishable_quarter = sum(
        1
        for listed_date, _basis in listing_info.values()
        if listed_date is not None
        and not any(
            (period_end := _quarter_end(period)) is not None
            and period_end >= listed_date
            for period in publishable_periods
        )
    )
    for metric in REQUIRED_FINANCIAL_FACTORS:
        symbols_evaluated = 0
        mature_symbols = 0
        depth_sufficient = 0
        cross_year_sufficient = 0
        fresh_symbols = 0
        target_achieved = 0
        buckets = {"0-9": 0, "10-19": 0, "20-29": 0, "30-39": 0, "40+": 0}
        gaps: list[dict[str, Any]] = []
        for symbol, (listed_date, listing_basis) in listing_info.items():
            if listed_date is None:
                continue
            expected = {
                period
                for period in publishable_periods
                if (
                    (period_end := _quarter_end(period)) is not None
                    and period_end >= listed_date
                )
            }
            if not expected:
                continue
            symbols_evaluated += 1
            if len(expected) >= FINANCIAL_MINIMUM_MATURE_QUARTERS:
                mature_symbols += 1
            payload = by_metric_symbol.get(
                (metric, symbol),
                {"observed": set(), "non_null": set()},
            )
            expected_target = {
                period
                for period in target_periods
                if (
                    (period_end := _quarter_end(period)) is not None
                    and period_end >= listed_date
                )
            }
            target_non_null = (
                set(payload["non_null"])
                .intersection(target_set)
                .intersection(expected_target)
            )
            non_null = target_non_null.intersection(publishable_set).intersection(
                expected
            )
            actual_depth = len(non_null)
            buckets[_depth_bucket(len(target_non_null))] += 1
            minimum_depth = (
                FINANCIAL_MINIMUM_MATURE_QUARTERS
                if len(expected) >= FINANCIAL_MINIMUM_MATURE_QUARTERS
                else max(1, ceil(len(expected) * MIN_FINANCIAL_DEPTH_RATIO))
            )
            expected_years = {period[:4] for period in expected}
            actual_years = {period[:4] for period in non_null}
            minimum_years = (
                5
                if len(expected_years) >= 5
                else max(1, ceil(len(expected_years) * MIN_FINANCIAL_DEPTH_RATIO))
            )
            depth_ok = actual_depth >= minimum_depth
            years_ok = len(actual_years) >= minimum_years
            latest_expected = max(expected)
            fresh = bool(non_null) and max(non_null) >= latest_expected
            if depth_ok:
                depth_sufficient += 1
            if years_ok:
                cross_year_sufficient += 1
            if fresh:
                fresh_symbols += 1
            if len(target_non_null) >= FINANCIAL_TARGET_QUARTERS:
                target_achieved += 1
            if (not depth_ok or not years_ok or not fresh) and len(gaps) < 5:
                gaps.append(
                    {
                        "symbol": symbol,
                        "listed_date": (
                            listed_date.isoformat() if listed_date is not None else None
                        ),
                        "listed_date_basis": listing_basis,
                        "expected_publishable_quarters": len(expected),
                        "non_null_quarters": actual_depth,
                        "minimum_quarters": minimum_depth,
                        "non_null_years": len(actual_years),
                        "minimum_years": minimum_years,
                        "latest_required_period": latest_expected,
                        "latest_non_null_period": max(non_null) if non_null else None,
                        "missing_period_sample": sorted(expected - non_null)[:5],
                    }
                )
        cadence = FINANCIAL_PROVIDER_CADENCE[metric]
        metrics[metric] = {
            "diagnostic_only": True,
            "provider_cadence": cadence["label"],
            "provider_expected_quarters": list(cadence["expected_quarters"]),
            "symbols_evaluated": symbols_evaluated,
            "mature_symbols": mature_symbols,
            "minimum_mature_quarters": FINANCIAL_MINIMUM_MATURE_QUARTERS,
            "minimum_mature_years": 5,
            "depth_sufficient_symbols": depth_sufficient,
            "depth_sufficient_ratio": _ratio(depth_sufficient, symbols_evaluated),
            "cross_year_sufficient_symbols": cross_year_sufficient,
            "cross_year_sufficient_ratio": _ratio(
                cross_year_sufficient,
                symbols_evaluated,
            ),
            "fresh_symbols": fresh_symbols,
            "fresh_ratio": _ratio(fresh_symbols, symbols_evaluated),
            "target_40_quarters_achieved_symbols": target_achieved,
            "target_40_quarters_achieved_ratio": _ratio(
                target_achieved,
                symbols_evaluated,
            ),
            "non_null_quarter_distribution": buckets,
            "representative_gaps": gaps,
        }
    return {
        "target_quarters": FINANCIAL_TARGET_QUARTERS,
        "minimum_mature_quarters": FINANCIAL_MINIMUM_MATURE_QUARTERS,
        "target_period_range": [target_periods[0], target_periods[-1]],
        "publishable_period_range": [
            publishable_periods[0] if publishable_periods else None,
            publishable_periods[-1] if publishable_periods else None,
        ],
        "publication_lag_days": FINANCIAL_PUBLICATION_LAG_DAYS,
        "universe_definition": (
            "BaoStock-supported current securities where market='CN' and "
            "list_status='listed', excluding board='北交所' and symbols starting "
            "with 4/8/92; "
            "listed_date falls back to first audited daily_bar as in backtest PIT"
        ),
        "universe_symbols": len(listing_info),
        "provider_unsupported_symbols": provider_unsupported_symbols,
        "listing_date_basis_counts": listing_basis_counts,
        "unknown_listing_date_symbols": unknown_listing_date_symbols,
        "new_symbols_without_publishable_quarter": (
            symbols_without_publishable_quarter
        ),
        "metric_depth": metrics,
    }


def _financial_audit(
    connection: sqlite3.Connection,
    *,
    universe_size: int,
    as_of_date: date,
) -> dict[str, Any]:
    summary = _one(
        connection,
        """
        SELECT COUNT(*) AS rows_count,
               COUNT(DISTINCT symbol) AS symbols,
               MIN(report_period) AS min_period,
               MAX(report_period) AS max_period,
               MIN(available_time) AS min_available_time,
               MAX(available_time) AS max_available_time
        FROM financial_indicators
        """,
    )
    metric_rows = _rows(
        connection,
        """
        SELECT metric,
               COUNT(*) AS rows,
               COUNT(DISTINCT symbol) AS symbols,
               SUM(CASE WHEN value IS NOT NULL THEN 1 ELSE 0 END) AS non_null_rows,
               COUNT(DISTINCT report_period) AS distinct_periods,
               COUNT(
                 DISTINCT CASE WHEN value IS NOT NULL THEN report_period END
               ) AS non_null_distinct_periods,
               COUNT(DISTINCT SUBSTR(report_period, 1, 4)) AS distinct_years,
               MIN(report_period) AS min_period,
               MAX(report_period) AS max_period
        FROM financial_indicators
        GROUP BY metric
        ORDER BY metric
        """,
    )
    metrics = {
        str(row["metric"]): {
            "rows": int(row["rows"]),
            "symbols": int(row["symbols"]),
            "symbol_coverage_ratio": _ratio(int(row["symbols"]), universe_size),
            "non_null_rows": int(row["non_null_rows"] or 0),
            "non_null_ratio": _ratio(
                int(row["non_null_rows"] or 0),
                int(row["rows"]),
            ),
            "distinct_periods": int(row["distinct_periods"]),
            "non_null_distinct_periods": int(row["non_null_distinct_periods"]),
            "distinct_years": int(row["distinct_years"]),
            "period_range": [row["min_period"], row["max_period"]],
        }
        for row in metric_rows
    }
    source_counts = _source_counts(connection, table="financial_indicators")
    symbols = int(summary["symbols"])
    pit = _financial_pit_audit(connection)
    return {
        "rows": int(summary["rows_count"]),
        "symbols": symbols,
        "symbol_coverage_ratio": _ratio(symbols, universe_size),
        "report_period_range": [summary["min_period"], summary["max_period"]],
        "available_time_range": [
            summary["min_available_time"],
            summary["max_available_time"],
        ],
        "metrics": metrics,
        "missing_required_metrics": sorted(set(REQUIRED_FINANCIAL_FACTORS) - set(metrics)),
        "source_counts": source_counts,
        "invalid_source_rows": _invalid_source_rows(
            source_counts,
            AUDITED_FINANCIAL_SOURCES,
        ),
        "duplicate_key_groups": _duplicate_groups(
            connection,
            table="financial_indicators",
            key_columns="symbol, report_period, metric",
        ),
        "pit": pit,
        "depth_contract": _financial_depth_audit(
            connection,
            as_of_date=as_of_date,
        ),
    }


def _valuation_audit(
    connection: sqlite3.Connection,
    *,
    universe_size: int,
    minimum_market_coverage: float,
    candidate_dates: Sequence[str],
) -> dict[str, Any]:
    summary = _one(
        connection,
        """
        SELECT COUNT(*) AS rows_count,
               COUNT(DISTINCT symbol) AS symbols,
               COUNT(DISTINCT trade_date) AS dates,
               MIN(trade_date) AS min_date,
               MAX(trade_date) AS max_date,
               SUM(CASE WHEN pe_ttm IS NOT NULL THEN 1 ELSE 0 END) AS pe_non_null,
               SUM(CASE WHEN pe_ttm > 0 THEN 1 ELSE 0 END) AS pe_positive,
               SUM(CASE WHEN pb_mrq IS NOT NULL THEN 1 ELSE 0 END) AS pb_non_null,
               SUM(CASE WHEN pb_mrq > 0 THEN 1 ELSE 0 END) AS pb_positive,
               SUM(CASE WHEN ps_ttm IS NOT NULL THEN 1 ELSE 0 END) AS ps_non_null,
               SUM(
                 CASE
                   WHEN available_time IS NULL
                     OR ABS(
                       julianday(available_time)
                       - julianday(trade_date || ' 07:00:00')
                     ) > 0.000012
                   THEN 1
                   ELSE 0
                 END
               ) AS pit_anomaly_rows
        FROM valuation_daily
        """,
    )
    rows_count = int(summary["rows_count"])
    source_counts = _source_counts(connection, table="valuation_daily")
    return {
        "rows": rows_count,
        "symbols": int(summary["symbols"]),
        "dates": int(summary["dates"]),
        "date_range": [summary["min_date"], summary["max_date"]],
        "metrics": {
            "pe_ttm": {
                "non_null_rows": int(summary["pe_non_null"] or 0),
                "non_null_ratio": _ratio(int(summary["pe_non_null"] or 0), rows_count),
                "positive_rows": int(summary["pe_positive"] or 0),
                "positive_ratio": _ratio(int(summary["pe_positive"] or 0), rows_count),
            },
            "pb_mrq": {
                "non_null_rows": int(summary["pb_non_null"] or 0),
                "non_null_ratio": _ratio(int(summary["pb_non_null"] or 0), rows_count),
                "positive_rows": int(summary["pb_positive"] or 0),
                "positive_ratio": _ratio(int(summary["pb_positive"] or 0), rows_count),
            },
            "ps_ttm": {
                "non_null_rows": int(summary["ps_non_null"] or 0),
                "non_null_ratio": _ratio(int(summary["ps_non_null"] or 0), rows_count),
            },
        },
        "source_counts": source_counts,
        "invalid_source_rows": _invalid_source_rows(
            source_counts,
            AUDITED_VALUATION_SOURCES,
        ),
        "pit_anomaly_rows": int(summary["pit_anomaly_rows"] or 0),
        "duplicate_key_groups": _duplicate_groups(
            connection,
            table="valuation_daily",
            key_columns="symbol, trade_date",
        ),
        "latest_broad_cross_section": _latest_cross_section(
            connection,
            table="valuation_daily",
            universe_size=universe_size,
            minimum_coverage=minimum_market_coverage,
            candidate_dates=candidate_dates,
        ),
    }


def _sector_flow_audit(connection: sqlite3.Connection) -> dict[str, Any]:
    summary = _one(
        connection,
        """
        SELECT COUNT(*) AS rows_count,
               COUNT(DISTINCT plate_code) AS plates,
               COUNT(DISTINCT trade_date) AS dates,
               MIN(trade_date) AS min_date,
               MAX(trade_date) AS max_date,
               SUM(CASE WHEN net_inflow IS NOT NULL THEN 1 ELSE 0 END) AS net_non_null,
               SUM(CASE WHEN main_inflow IS NOT NULL THEN 1 ELSE 0 END) AS main_non_null
        FROM sector_flow_daily
        """,
    )
    membership = _one(
        connection,
        """
        SELECT COUNT(*) AS rows_count,
               COUNT(DISTINCT symbol) AS symbols,
               COUNT(DISTINCT plate_code) AS plates,
               MIN(refreshed_at) AS min_refreshed_at,
               MAX(refreshed_at) AS max_refreshed_at
        FROM sector_constituents
        """,
    )
    membership_history = _one(
        connection,
        """
        SELECT COUNT(*) AS rows_count,
               COUNT(DISTINCT symbol) AS symbols,
               COUNT(DISTINCT plate_code) AS plates,
               COUNT(DISTINCT as_of_date) AS dates,
               MIN(as_of_date) AS min_date,
               MAX(as_of_date) AS max_date,
               MIN(available_time) AS min_available_time,
               MAX(available_time) AS max_available_time
        FROM sector_constituent_snapshots
        """,
    )
    rows_count = int(summary["rows_count"])
    source_counts = _source_counts(connection, table="sector_flow_daily")
    historical = _one(
        connection,
        """
        SELECT COUNT(*) AS rows_count,
               COUNT(DISTINCT plate_code) AS plates,
               COUNT(DISTINCT trade_date) AS dates,
               MIN(trade_date) AS min_date,
               MAX(trade_date) AS max_date
        FROM sector_flow_daily
        WHERE source = ?
        """,
        (M3_SECTOR_FLOW_SOURCE,),
    )
    historical_rows = int(historical["rows_count"])
    historical_plates = int(historical["plates"])
    historical_dates = int(historical["dates"])
    historical_start = historical["min_date"]
    historical_end = historical["max_date"]
    historical_window_rows = historical_rows
    mixed_source_rows_in_window = 0
    live_forward = {
        "rows": 0,
        "plates": 0,
        "dates": 0,
        "date_range": [None, None],
        "source_counts": {},
    }
    if historical_start is not None and historical_end is not None:
        window = _one(
            connection,
            """
            SELECT COUNT(*) AS rows_count
            FROM sector_flow_daily
            WHERE trade_date >= ? AND trade_date <= ?
            """,
            (historical_start, historical_end),
        )
        historical_window_rows = int(window["rows_count"])
        mixed_source_rows_in_window = historical_window_rows - historical_rows
        live_summary = _one(
            connection,
            """
            SELECT COUNT(*) AS rows_count,
                   COUNT(DISTINCT plate_code) AS plates,
                   COUNT(DISTINCT trade_date) AS dates,
                   MIN(trade_date) AS min_date,
                   MAX(trade_date) AS max_date
            FROM sector_flow_daily
            WHERE trade_date > ?
            """,
            (historical_end,),
        )
        live_sources = connection.execute(
            """
            SELECT COALESCE(source, '<null>') AS source, COUNT(*) AS rows_count
            FROM sector_flow_daily
            WHERE trade_date > ?
            GROUP BY source
            ORDER BY source
            """,
            (historical_end,),
        ).fetchall()
        live_forward = {
            "rows": int(live_summary["rows_count"]),
            "plates": int(live_summary["plates"]),
            "dates": int(live_summary["dates"]),
            "date_range": [live_summary["min_date"], live_summary["max_date"]],
            "source_counts": {
                str(row["source"]): int(row["rows_count"]) for row in live_sources
            },
        }
    expected_historical_rows = historical_plates * historical_dates
    rectangular_gap_rows = max(0, expected_historical_rows - historical_rows)
    return {
        "rows": rows_count,
        "plates": int(summary["plates"]),
        "dates": int(summary["dates"]),
        "date_range": [summary["min_date"], summary["max_date"]],
        "net_inflow_non_null_rows": int(summary["net_non_null"] or 0),
        "net_inflow_non_null_ratio": _ratio(int(summary["net_non_null"] or 0), rows_count),
        "main_inflow_non_null_rows": int(summary["main_non_null"] or 0),
        "main_inflow_non_null_ratio": _ratio(
            int(summary["main_non_null"] or 0),
            rows_count,
        ),
        "source_counts": source_counts,
        "historical_source": M3_SECTOR_FLOW_SOURCE,
        "historical_source_rows": historical_rows,
        "historical_source_ratio": _ratio(
            historical_rows,
            historical_window_rows,
        ),
        "historical_backfill": {
            "source": M3_SECTOR_FLOW_SOURCE,
            "rows": historical_rows,
            "plates": historical_plates,
            "dates": historical_dates,
            "date_range": [historical_start, historical_end],
            "expected_rectangular_rows": expected_historical_rows,
            "rectangular_gap_rows": rectangular_gap_rows,
            "window_rows": historical_window_rows,
            "mixed_source_rows_in_window": mixed_source_rows_in_window,
        },
        "live_forward": live_forward,
        "invalid_source_rows": _invalid_source_rows(
            source_counts,
            AUDITED_SECTOR_FLOW_SOURCES,
        ),
        "duplicate_key_groups": _duplicate_groups(
            connection,
            table="sector_flow_daily",
            key_columns="plate_code, trade_date",
        ),
        "membership_snapshot": {
            "rows": int(membership["rows_count"]),
            "symbols": int(membership["symbols"]),
            "plates": int(membership["plates"]),
            "refreshed_at_range": [
                membership["min_refreshed_at"],
                membership["max_refreshed_at"],
            ],
        },
        "membership_pit_history": {
            "rows": int(membership_history["rows_count"]),
            "symbols": int(membership_history["symbols"]),
            "plates": int(membership_history["plates"]),
            "dates": int(membership_history["dates"]),
            "date_range": [
                membership_history["min_date"],
                membership_history["max_date"],
            ],
            "available_time_range": [
                membership_history["min_available_time"],
                membership_history["max_available_time"],
            ],
            "duplicate_key_groups": _duplicate_groups(
                connection,
                table="sector_constituent_snapshots",
                key_columns="plate_code, symbol, as_of_date",
            ),
        },
    }


def _calendar(connection: sqlite3.Connection) -> list[date]:
    placeholders, sources = _in_clause(tuple(AUDITED_DAILY_BAR_SOURCES))
    rows = connection.execute(
        f"""
        SELECT trade_date
        FROM daily_bars
        WHERE symbol = 'SH.000001'
          AND source IN ({placeholders})
        ORDER BY trade_date
        """,
        sources,
    ).fetchall()
    if not rows:
        rows = connection.execute(
            f"""
            SELECT trade_date
            FROM daily_bars
            WHERE source IN ({placeholders})
            GROUP BY trade_date
            ORDER BY trade_date
            """,
            sources,
        ).fetchall()
    result: list[date] = []
    for row in rows:
        parsed = _parse_date(row["trade_date"])
        if parsed is not None:
            result.append(parsed)
    return result


def _pick_probe_dates(connection: sqlite3.Connection) -> dict[str, list[str]]:
    calendar = _calendar(connection)
    valuation_range = _one(
        connection,
        "SELECT MIN(trade_date) AS min_date, MAX(trade_date) AS max_date FROM valuation_daily",
    )
    valuation_start = _parse_date(valuation_range["min_date"])
    valuation_end = _parse_date(valuation_range["max_date"])
    usable = [
        observed
        for observed in calendar
        if (valuation_start is None or observed >= valuation_start)
        and (valuation_end is None or observed <= valuation_end)
    ]
    if len(usable) > 90:
        usable = usable[89:]
    multi_year: list[date] = []
    if usable:
        indexes = (0, len(usable) // 2, len(usable) - 1)
        multi_year = list(dict.fromkeys(usable[index] for index in indexes))

    flow_rows = connection.execute(
        """
        SELECT trade_date
        FROM sector_flow_daily
        GROUP BY trade_date
        ORDER BY trade_date
        """
    ).fetchall()
    flow_dates = [
        parsed
        for row in flow_rows
        if (parsed := _parse_date(row["trade_date"])) is not None
    ]
    warmed_flow_dates = flow_dates[4:]
    flow_probe: list[date] = []
    if warmed_flow_dates:
        indexes = (0, len(warmed_flow_dates) // 2, len(warmed_flow_dates) - 1)
        flow_probe = list(
            dict.fromkeys(warmed_flow_dates[index] for index in indexes)
        )
    all_dates = sorted(set(multi_year + flow_probe))
    return {
        "multi_year": [item.isoformat() for item in multi_year],
        "sector_flow_one_year": [item.isoformat() for item in flow_probe],
        "all": [item.isoformat() for item in all_dates],
    }


def _membership_pit_visibility(
    connection: sqlite3.Connection,
    *,
    flow_probe_dates: Sequence[str],
) -> dict[str, dict[str, Any]]:
    visibility: dict[str, dict[str, Any]] = {}
    for date_text in flow_probe_dates:
        probe_date = date.fromisoformat(date_text)
        cutoff = datetime.combine(
            probe_date,
            time(hour=19, minute=30),
            tzinfo=MARKET_TIMEZONE,
        ).astimezone(UTC)
        row = _one(
            connection,
            """
            SELECT COUNT(*) AS rows_count,
                   COUNT(DISTINCT symbol) AS symbols,
                   COUNT(DISTINCT plate_code) AS plates
            FROM sector_constituent_snapshots
            WHERE as_of_date = ?
              AND julianday(available_time) <= julianday(?)
            """,
            (date_text, cutoff.isoformat()),
        )
        rows_count = int(row["rows_count"])
        visibility[date_text] = {
            "decision_cutoff": cutoff.isoformat(),
            "rows": rows_count,
            "symbols": int(row["symbols"]),
            "plates": int(row["plates"]),
            "visible": rows_count > 0,
        }
    return visibility


def _readonly_sqlalchemy_engine(database_path: Path) -> Any:
    def creator() -> sqlite3.Connection:
        return _open_readonly_dbapi(database_path)

    return create_engine("sqlite://", creator=creator)


def _factor_group(factor: str) -> str:
    if factor in PRICE_FACTORS:
        return "price_volume"
    if factor in FINANCIAL_FACTORS:
        return "financial"
    if factor in VALUATION_FACTORS:
        return "valuation"
    if factor in FLOW_FACTORS:
        return "sector_flow"
    return "live_only"


def _factor_availability(
    database_path: Path,
    *,
    schedule: dict[str, list[str]],
    membership_pit_visibility: dict[str, dict[str, Any]],
    minimum_cross_section: int,
    probe: FactorProbe,
) -> dict[str, Any]:
    probe_rows: dict[str, dict[str, Any]] = {}
    engine = _readonly_sqlalchemy_engine(database_path)
    try:
        with Session(engine) as session:
            for date_text in schedule["all"]:
                probe_date = date.fromisoformat(date_text)
                try:
                    frame = probe(session, probe_date)
                except Exception as exc:
                    probe_rows[date_text] = {
                        "date": date_text,
                        "error": f"{type(exc).__name__}: {exc}",
                        "eligible": 0,
                        "counts": {},
                    }
                    continue
                factor_counts = {
                    factor: (
                        int(frame[factor].notna().sum())
                        if factor in frame.columns
                        else 0
                    )
                    for factor in HISTORICAL_FACTORS
                }
                probe_rows[date_text] = {
                    "date": date_text,
                    "error": None,
                    "eligible": int(frame.attrs.get("eligible", len(frame))),
                    "counts": factor_counts,
                }
    finally:
        engine.dispose()

    factors: list[dict[str, Any]] = []
    for factor in FACTOR_SET:
        group = _factor_group(factor)
        if factor in HISTORY_EXCLUDED_PIT_GAP_FACTORS:
            factors.append(
                {
                    "factor": factor,
                    "group": group,
                    "status": "history_excluded_pit_gap",
                    "minimum_cross_section": None,
                    "probes": [],
                    "non_null_summary": {
                        "probe_count": 0,
                        "minimum_n": None,
                        "maximum_n": None,
                        "sufficient_probe_count": 0,
                    },
                    "representative_gaps": [],
                    "reason": (
                        "2026-07-25 架构裁定：历史板块成分 PIT 不可重建；"
                        "该因子退出 S7/S9，日快照仅供未来窗口。"
                    ),
                    "cause_class": "history_excluded_pit_gap",
                    "historical_candidate": False,
                    "live_forward": True,
                }
            )
            continue
        if factor == "sector_strength":
            factors.append(
                {
                    "factor": factor,
                    "group": group,
                    "status": "live_only",
                    "minimum_cross_section": None,
                    "probes": [],
                    "non_null_summary": {
                        "probe_count": 0,
                        "minimum_n": None,
                        "maximum_n": None,
                        "sufficient_probe_count": 0,
                    },
                    "representative_gaps": [],
                    "reason": (
                        "SectorSnapshot is a live derived signal with no historical PIT series."
                    ),
                    "historical_candidate": False,
                    "live_forward": False,
                }
            )
            continue
        relevant_dates = (
            schedule["sector_flow_one_year"]
            if factor in FLOW_FACTORS
            else schedule["multi_year"]
        )
        observations = [
            {
                "date": date_text,
                "n": probe_rows.get(date_text, {}).get("counts", {}).get(factor, 0),
                "eligible": probe_rows.get(date_text, {}).get("eligible", 0),
                "error": probe_rows.get(date_text, {}).get("error"),
                "membership_pit": (
                    membership_pit_visibility.get(date_text)
                    if factor in FLOW_FACTORS
                    else None
                ),
            }
            for date_text in relevant_dates
        ]
        errors = [item for item in observations if item["error"] is not None]
        observed_counts = [
            int(item["n"]) for item in observations if item["error"] is None
        ]
        membership_gap = factor in FLOW_FACTORS and any(
            not bool((item.get("membership_pit") or {}).get("visible"))
            for item in observations
        )
        if not observations:
            status = "not_probed"
            reason = "No PIT-valid probe date was available for this factor interval."
        elif membership_gap:
            status = "unavailable"
            reason = (
                "At least one one-year probe has no sector membership visible by "
                "its decision cutoff."
            )
        elif errors:
            status = "probe_error"
            reason = "At least one read-only factor_zscores probe failed."
        elif observed_counts and all(
            value >= minimum_cross_section for value in observed_counts
        ):
            status = "sufficient"
            reason = None
        elif observed_counts and max(observed_counts) == 0:
            status = "unavailable"
            reason = "All PIT probes returned n=0."
        else:
            status = "insufficient"
            reason = "At least one PIT probe is below the declared cross-section threshold."
        representative_gaps = [
            {
                "date": item["date"],
                "n": item["n"],
                "eligible": item["eligible"],
                "error": item["error"],
                "membership_pit": item.get("membership_pit"),
            }
            for item in observations
            if item["error"] is not None
            or int(item["n"]) < minimum_cross_section
            or (
                factor in FLOW_FACTORS
                and not bool((item.get("membership_pit") or {}).get("visible"))
            )
        ][:3]
        factors.append(
            {
                "factor": factor,
                "group": group,
                "status": status,
                "minimum_cross_section": minimum_cross_section,
                "probes": observations,
                "non_null_summary": {
                    "probe_count": len(observations),
                    "minimum_n": min(observed_counts) if observed_counts else None,
                    "maximum_n": max(observed_counts) if observed_counts else None,
                    "sufficient_probe_count": sum(
                        value >= minimum_cross_section for value in observed_counts
                    ),
                },
                "representative_gaps": representative_gaps,
                "reason": reason,
                "historical_candidate": True,
                "live_forward": False,
            }
        )
    return {
        "minimum_cross_section": minimum_cross_section,
        "schedule": schedule,
        "sector_membership_pit_visibility": membership_pit_visibility,
        "probe_results": [probe_rows[key] for key in sorted(probe_rows)],
        "factors": factors,
    }


def _diagnose_factor_gaps(
    availability: dict[str, Any],
    *,
    input_coverage: dict[str, Any],
    key_audit: dict[str, int],
    minimum_market_coverage: float,
) -> None:
    financial = input_coverage["financial_indicators"]
    valuation = input_coverage["valuation_daily"]
    sector = input_coverage["sector_flow_daily"]
    membership_visibility = availability["sector_membership_pit_visibility"]
    for item in availability["factors"]:
        status = str(item["status"])
        factor = str(item["factor"])
        if status == "sufficient":
            item["cause_class"] = None
            continue
        if status == "live_only":
            item["cause_class"] = "live_only"
            continue
        if status == "history_excluded_pit_gap":
            item["cause_class"] = "history_excluded_pit_gap"
            continue
        if status == "probe_error":
            item["cause_class"] = "probe_error"
            continue
        if factor in FINANCIAL_FACTORS:
            coverage = financial["symbol_coverage_ratio"]
            if coverage is None or coverage < minimum_market_coverage:
                item["cause_class"] = "input_data_gap"
                item["reason"] = (
                    f"S2 财务股票覆盖率仅 {coverage!s}，未达到 "
                    f"{minimum_market_coverage:.0%}；先完成 S2。"
                )
            elif financial["pit"]["anomaly_rows"]:
                item["cause_class"] = "pit_data_error"
                item["reason"] = "财务 available_time 审计异常导致 PIT 截面不可信。"
            else:
                item["cause_class"] = "suspected_factor_path_bug"
                item["reason"] = (
                    "财务底表覆盖和 PIT 已过闸但因子截面不足，需检查因子/取数路径。"
                )
            continue
        if factor in VALUATION_FACTORS:
            metric = "pe_ttm" if factor == "pe_percentile" else "pb_mrq"
            if valuation["metrics"][metric]["positive_rows"] == 0:
                item["cause_class"] = "input_data_gap"
                item["reason"] = f"{metric} 无正值，百分位因子没有可用输入。"
            elif valuation["pit_anomaly_rows"]:
                item["cause_class"] = "pit_data_error"
                item["reason"] = "估值 available_time 审计异常导致 PIT 截面不可信。"
            else:
                item["cause_class"] = "suspected_factor_path_bug"
                item["reason"] = (
                    "估值底表有正值且 PIT 已过闸，但精确决策日因子截面不足，需检查单日查询路径。"
                )
            continue
        if factor in FLOW_FACTORS:
            if any(
                not bool(payload.get("visible"))
                for payload in membership_visibility.values()
            ):
                item["cause_class"] = "pit_membership_gap"
                item["reason"] = (
                    "板块资金流存在，但 sector_constituents 的 refreshed_at 晚于探针决策时点；"
                    "严格 PIT 下不得借用当前成分。"
                )
            elif sector["dates"] < 5:
                item["cause_class"] = "input_data_gap"
                item["reason"] = "板块资金流不足 5 个交易日，无法计算 net_inflow_5d。"
            else:
                item["cause_class"] = "suspected_factor_path_bug"
                item["reason"] = (
                    "资金流与 PIT 成分表表面可用但因子截面不足，需检查五日聚合/成分映射。"
                )
            continue
        if factor in PRICE_FACTORS:
            if (
                key_audit["audited_daily_keys_without_adj"]
                or key_audit["adj_keys_without_audited_daily"]
            ):
                item["cause_class"] = "input_data_gap"
                item["reason"] = "审计日线与复权键不齐，价量因子输入会被 inner join 丢弃。"
            else:
                item["cause_class"] = "suspected_factor_path_bug"
                item["reason"] = (
                    "日线/复权键完整但价量截面不足，需检查历史窗口与因子计算路径。"
                )


def _stratified_ids(
    connection: sqlite3.Connection,
    *,
    table: str,
    count: int,
    seed: int,
) -> list[int]:
    if table not in {"daily_bars", "financial_indicators", "valuation_daily"}:
        raise ValueError("unsupported sample table")
    bounds = _one(
        connection,
        f"SELECT MIN(id) AS min_id, MAX(id) AS max_id FROM {table}",
    )
    if bounds["min_id"] is None or bounds["max_id"] is None:
        return []
    minimum = int(bounds["min_id"])
    maximum = int(bounds["max_id"])
    generator = random.Random(seed)
    targets = sorted(
        {
            generator.randint(minimum, maximum)
            for _ in range(max(count * 3, count))
        }
    )
    selected: list[int] = []
    for target in targets:
        row = connection.execute(
            f"SELECT id FROM {table} WHERE id >= ? ORDER BY id LIMIT 1",
            (target,),
        ).fetchone()
        if row is not None and int(row["id"]) not in selected:
            selected.append(int(row["id"]))
        if len(selected) == count:
            break
    return selected


def _pit_samples(
    connection: sqlite3.Connection,
    *,
    sample_size: int,
    seed: int,
) -> dict[str, Any]:
    daily_ids = _stratified_ids(
        connection,
        table="daily_bars",
        count=sample_size,
        seed=seed,
    )
    financial_ids = _stratified_ids(
        connection,
        table="financial_indicators",
        count=sample_size,
        seed=seed + 1,
    )
    valuation_ids = _stratified_ids(
        connection,
        table="valuation_daily",
        count=sample_size,
        seed=seed + 2,
    )
    daily: list[dict[str, Any]] = []
    daily_source_placeholders, daily_sources = _in_clause(
        tuple(AUDITED_DAILY_BAR_SOURCES)
    )
    adj_source_placeholders, adj_sources = _in_clause(
        tuple(AUDITED_ADJ_FACTOR_SOURCES)
    )
    for row_id in daily_ids:
        row = _one(
            connection,
            """
            SELECT bars.symbol, bars.trade_date, bars.close, bars.source,
                   factors.adj_factor, factors.source AS adj_source
            FROM daily_bars AS bars
            LEFT JOIN adj_factors AS factors
              ON factors.symbol = bars.symbol
             AND factors.trade_date = bars.trade_date
            WHERE bars.id = ?
            """,
            (row_id,),
        )
        sample = dict(row)
        anchor = connection.execute(
            f"""
            SELECT bars.trade_date, factors.adj_factor
            FROM daily_bars AS bars
            JOIN adj_factors AS factors
              ON factors.symbol = bars.symbol
             AND factors.trade_date = bars.trade_date
            WHERE bars.symbol = ?
              AND bars.trade_date > ?
              AND bars.source IN ({daily_source_placeholders})
              AND factors.source IN ({adj_source_placeholders})
              AND factors.adj_factor > 0
            ORDER BY bars.trade_date DESC
            LIMIT 1
            """,
            (
                str(sample["symbol"]),
                str(sample["trade_date"]),
                *daily_sources,
                *adj_sources,
            ),
        ).fetchone()
        if anchor is None:
            raise RuntimeError(
                "daily PIT sample has no audited adjustment normalization anchor"
            )
        anchor_factor = float(anchor["adj_factor"])
        if not isfinite(anchor_factor) or anchor_factor <= 0:
            raise RuntimeError(
                "daily PIT sample adjustment normalization anchor is invalid"
            )
        sample["adj_anchor_date"] = str(anchor["trade_date"])
        sample["adj_anchor_factor"] = anchor_factor
        daily.append(sample)
    financial = [
        dict(
            _one(
                connection,
                """
                SELECT symbol, report_period, metric, value, source,
                       available_time, payload
                FROM financial_indicators
                WHERE id = ?
                """,
                (row_id,),
            )
        )
        for row_id in financial_ids
    ]
    valuation = [
        dict(
            _one(
                connection,
                """
                SELECT symbol, trade_date, pe_ttm, pb_mrq, ps_ttm,
                       source, available_time
                FROM valuation_daily
                WHERE id = ?
                """,
                (row_id,),
            )
        )
        for row_id in valuation_ids
    ]
    return {
        "selection": "deterministic pseudo-random row-id sampling",
        "seed": seed,
        "sample_size_per_table": sample_size,
        "external_source_pairing": "not_performed_by_this_read_only_script",
        "daily_bars_with_adj": daily,
        "financial_indicators": financial,
        "valuation_daily": valuation,
    }


def _pit_manifest_payload(pit_samples: dict[str, Any]) -> dict[str, Any]:
    """Return the complete local sample snapshot covered by an external sign-off."""

    return {
        "schema_version": PIT_MANIFEST_SCHEMA_VERSION,
        "selection": pit_samples["selection"],
        "seed": pit_samples["seed"],
        "sample_size_per_table": pit_samples["sample_size_per_table"],
        "daily_bars_with_adj": pit_samples["daily_bars_with_adj"],
        "financial_indicators": pit_samples["financial_indicators"],
        "valuation_daily": pit_samples["valuation_daily"],
    }


def _pit_manifest_sha256(pit_samples: dict[str, Any]) -> str:
    canonical = json.dumps(
        _pit_manifest_payload(pit_samples),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _sample_identity(
    table: str,
    key: dict[str, Any],
) -> tuple[str, tuple[tuple[str, str], ...]]:
    key_fields = {
        "daily_bars": ("symbol", "trade_date"),
        "financial_indicators": ("symbol", "report_period", "metric"),
        "valuation_daily": ("symbol", "trade_date"),
    }.get(table)
    if key_fields is None:
        raise ValueError(f"unsupported external PIT sample table: {table}")
    return (
        table,
        tuple((field, str(key.get(field) or "")) for field in key_fields),
    )


def _expected_sample_records(
    pit_samples: dict[str, Any],
) -> dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]]:
    records: dict[
        tuple[str, tuple[tuple[str, str], ...]],
        dict[str, Any],
    ] = {}
    for table in ("daily_bars", "financial_indicators", "valuation_daily"):
        source_key = (
            "daily_bars_with_adj"
            if table == "daily_bars"
            else table
        )
        for sample in pit_samples[source_key]:
            identity = _sample_identity(table, sample)
            if identity in records:
                raise RuntimeError("local PIT sampling produced duplicate business keys")
            records[identity] = sample
    return records


def _validate_checked_values(
    *,
    table: str,
    current_sample: dict[str, Any],
    evidence_sample: dict[str, Any],
) -> None:
    checked_values = evidence_sample["checked_values"]
    if not isinstance(checked_values, dict):
        raise ValueError("external PIT pairing checked_values must be an object")
    for field in PIT_CHECKED_FIELDS[table]:
        comparison = checked_values[field]
        if not isinstance(comparison, dict):
            raise ValueError(
                f"external PIT pairing {table}.{field} check must be an object"
            )
        if comparison["pass"] is not True:
            raise ValueError(
                f"external PIT pairing {table}.{field} pass must be true"
            )
        local_value = comparison["local_value"]
        external_value = comparison["external_value"]
        current_value = current_sample.get(field)
        if field in PIT_NUMERIC_CHECKED_FIELDS:
            if current_value is None:
                if local_value is not None:
                    raise ValueError(
                        f"external PIT pairing {table}.{field} local_value "
                        "does not match the current sample"
                    )
                if (
                    not isinstance(external_value, str)
                    or external_value.strip().casefold()
                    not in PIT_MISSING_EXTERNAL_VALUES
                ):
                    raise ValueError(
                        f"external PIT pairing {table}.{field} external_value "
                        "must explicitly represent the missing external value"
                    )
                if comparison.get("tolerance") != 0.0:
                    raise ValueError(
                        f"external PIT pairing {table}.{field} missing-value "
                        "tolerance must be exactly zero"
                    )
                continue
            if (
                isinstance(local_value, bool)
                or not isinstance(local_value, (int, float))
                or not isfinite(float(local_value))
                or float(local_value) != float(current_value)
            ):
                raise ValueError(
                    f"external PIT pairing {table}.{field} local_value "
                    "does not match the current sample"
                )
            if (
                isinstance(external_value, bool)
                or not isinstance(external_value, (int, float))
                or not isfinite(float(external_value))
            ):
                raise ValueError(
                    f"external PIT pairing {table}.{field} external_value "
                    "must be a finite number"
                )
            tolerance_value = comparison.get("tolerance", 0.0)
            if (
                isinstance(tolerance_value, bool)
                or not isinstance(tolerance_value, (int, float))
                or not isfinite(float(tolerance_value))
                or float(tolerance_value) < 0
            ):
                raise ValueError(
                    f"external PIT pairing {table}.{field} tolerance "
                    "must be a finite non-negative number"
                )
            absolute_tolerance, relative_tolerance = PIT_NUMERIC_TOLERANCE_POLICY[
                field
            ]
            expected_tolerance = max(
                absolute_tolerance,
                relative_tolerance
                * max(abs(float(local_value)), abs(float(external_value))),
            )
            if float(tolerance_value) != expected_tolerance:
                raise ValueError(
                    f"external PIT pairing {table}.{field} tolerance "
                    "does not match the fixed policy"
                )
            if abs(float(local_value) - float(external_value)) > expected_tolerance:
                raise ValueError(
                    f"external PIT pairing {table}.{field} values exceed tolerance"
                )
            continue
        current_text = str(current_value or "")
        if (
            not isinstance(local_value, str)
            or local_value != current_text
            or not local_value.strip()
        ):
            raise ValueError(
                f"external PIT pairing {table}.{field} local_value "
                "does not match the current sample"
            )
        if (
            not isinstance(external_value, str)
            or not external_value.strip()
            or external_value != local_value
        ):
            raise ValueError(
                f"external PIT pairing {table}.{field} external_value "
                "does not match the signed local value"
            )


def _external_pairing_evidence(
    evidence_path: Path | None,
    *,
    pit_samples: dict[str, Any],
) -> dict[str, Any]:
    if evidence_path is None:
        return {
            "accepted": False,
            "basename": None,
            "sha256": None,
            "bytes": 0,
            "schema_version": EXTERNAL_PAIRING_SCHEMA_VERSION,
            "pit_manifest_schema_version": PIT_MANIFEST_SCHEMA_VERSION,
            "pit_manifest_sha256": pit_samples["manifest_sha256"],
            "reviewer_role": None,
            "reviewed_at": None,
            "sample_count": 0,
        }
    resolved = evidence_path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"external PIT pairing evidence not found: {resolved}")
    evidence_bytes = resolved.stat().st_size
    if evidence_bytes > MAX_EXTERNAL_EVIDENCE_BYTES:
        raise ValueError(
            "external PIT pairing evidence exceeds "
            f"{MAX_EXTERNAL_EVIDENCE_BYTES} bytes"
        )
    payload = resolved.read_bytes()
    if not payload:
        raise ValueError("external PIT pairing evidence must not be empty")
    if len(payload) != evidence_bytes:
        raise ValueError("external PIT pairing evidence changed while being read")
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("external PIT pairing evidence must be UTF-8 text") from exc
    if not content.strip():
        raise ValueError("external PIT pairing evidence must contain non-whitespace text")
    def reject_non_finite_json(value: str) -> None:
        raise ValueError(f"non-finite JSON number is not allowed: {value}")

    try:
        def reject_duplicate_json_keys(
            pairs: list[tuple[str, Any]],
        ) -> dict[str, Any]:
            strict_object: dict[str, Any] = {}
            for key, value in pairs:
                if key in strict_object:
                    raise ValueError(f"duplicate JSON key is not allowed: {key}")
                strict_object[key] = value
            return strict_object

        document = json.loads(
            content,
            object_pairs_hook=reject_duplicate_json_keys,
            parse_constant=reject_non_finite_json,
        )
    except json.JSONDecodeError as exc:
        raise ValueError("external PIT pairing evidence must be strict JSON") from exc
    except ValueError as exc:
        raise ValueError("external PIT pairing evidence must be strict JSON") from exc
    errors = sorted(
        Draft202012Validator(EXTERNAL_PAIRING_SCHEMA).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise ValueError(
            "external PIT pairing evidence schema violation at "
            f"{location}: {errors[0].message}"
        )
    if not isinstance(document, dict):
        raise ValueError("external PIT pairing evidence root must be an object")
    reviewer_role = str(document["reviewer_role"]).strip()
    if (
        not reviewer_role
        or any(
            marker in reviewer_role.casefold()
            for marker in PIT_NON_HUMAN_REVIEWER_MARKERS
        )
    ):
        raise ValueError(
            "external PIT pairing reviewer_role must identify a human reviewer"
        )
    reviewed_at_text = str(document["reviewed_at"]).strip()
    reviewed_at = _parse_datetime(reviewed_at_text)
    if (
        reviewed_at is None
        or datetime.fromisoformat(reviewed_at_text.replace("Z", "+00:00")).utcoffset()
        is None
    ):
        raise ValueError("external PIT pairing reviewed_at must be timezone-aware")
    if int(document["seed"]) != int(pit_samples["seed"]):
        raise ValueError("external PIT pairing seed does not match this report")
    if int(document["sample_size_per_table"]) != int(
        pit_samples["sample_size_per_table"]
    ):
        raise ValueError(
            "external PIT pairing sample_size_per_table does not match this report"
        )
    if str(document["pit_manifest_schema_version"]) != str(
        pit_samples["manifest_schema_version"]
    ):
        raise ValueError(
            "external PIT pairing manifest schema does not match this report"
        )
    if str(document["pit_manifest_sha256"]) != str(
        pit_samples["manifest_sha256"]
    ):
        raise ValueError(
            "external PIT pairing manifest SHA-256 does not match the current "
            "sample values"
        )
    raw_samples = document["samples"]
    if not isinstance(raw_samples, list):
        raise ValueError("external PIT pairing samples must be a list")
    supplied: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    evidence_by_identity: dict[
        tuple[str, tuple[tuple[str, str], ...]],
        dict[str, Any],
    ] = {}
    for sample in raw_samples:
        if not isinstance(sample, dict):
            raise ValueError("external PIT pairing sample must be an object")
        table = str(sample["table"])
        source = str(sample["external_source"]).strip()
        allowed_sources = PIT_ALLOWED_EXTERNAL_SOURCES_BY_TABLE.get(table, frozenset())
        if source not in allowed_sources:
            raise ValueError(
                "external PIT pairing external_source is not on the audited "
                f"non-BaoStock route for {table}: {source or '<blank>'}"
            )
        key = sample["key"]
        if not isinstance(key, dict):
            raise ValueError("external PIT pairing sample key must be an object")
        identity = _sample_identity(table, key)
        supplied.append(identity)
        evidence_by_identity[identity] = sample
    supplied_set = set(supplied)
    if len(supplied_set) != len(supplied):
        raise ValueError("external PIT pairing contains duplicate sample keys")
    expected = _expected_sample_records(pit_samples)
    if supplied_set != set(expected):
        raise ValueError(
            "external PIT pairing samples do not exactly cover this report's "
            f"selected PIT samples: expected={len(expected)}, supplied={len(supplied_set)}"
        )
    for identity, current_sample in expected.items():
        _validate_checked_values(
            table=identity[0],
            current_sample=current_sample,
            evidence_sample=evidence_by_identity[identity],
        )
    return {
        "accepted": True,
        "basename": resolved.name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "schema_version": str(document["schema_version"]),
        "pit_manifest_schema_version": str(
            document["pit_manifest_schema_version"]
        ),
        "pit_manifest_sha256": str(document["pit_manifest_sha256"]),
        "reviewer_role": reviewer_role,
        "reviewed_at": reviewed_at_text,
        "sample_count": len(supplied),
    }


def _gate(
    report: dict[str, Any],
    *,
    minimum_market_coverage: float,
    minimum_sector_plates: int,
    minimum_sector_dates: int,
) -> dict[str, Any]:
    inputs = report["input_coverage"]
    daily = inputs["daily_bars"]
    adj = inputs["adj_factors"]
    financial = inputs["financial_indicators"]
    valuation = inputs["valuation_daily"]
    sector = inputs["sector_flow_daily"]
    keys = report["daily_adj_key_audit"]
    factors = report["factor_availability"]["factors"]
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, Any]] = [
        {
            "code": "SURVIVORSHIP_BIAS",
            "message": "证券主表不含完整退市股历史，历史截面仍有幸存者偏差。",
        },
        {
            "code": "SECTOR_FLOW_ONE_YEAR_LIMIT",
            "message": "富途 DAY 板块资金流只有约一年硬上限，不能外推为多年资金流历史。",
        },
        {
            "code": "SECTOR_FLOW_FIXED_TOP5_LOOKAHEAD",
            "message": "S5 用当前 top5 固定篮子回填历史，存在已披露的轻微前视偏差。",
        },
        {
            "code": "SECTOR_STRENGTH_LIVE_ONLY",
            "message": "sector_strength 是实时衍生量，明确不进入历史回测。",
        },
        {
            "code": "FACTOR_NET_INFLOW_5D_HISTORY_EXCLUDED_PIT_GAP",
            "message": (
                "2026-07-25 架构裁定：net_inflow_5d 因历史板块成分 PIT 缺口"
                "退出 S7/S9；日快照仅供未来窗口，不阻断 S7。"
            ),
        },
    ]

    def block(code: str, message: str, *, kind: str = "automated") -> None:
        blockers.append({"code": code, "message": message, "kind": kind})

    for name, payload in (
        ("daily_bars", daily),
        ("adj_factors", adj),
        ("financial_indicators", financial),
        ("valuation_daily", valuation),
        ("sector_flow_daily", sector),
    ):
        if payload["duplicate_key_groups"]:
            block(
                f"{name.upper()}_DUPLICATES",
                f"{name} 存在 {payload['duplicate_key_groups']} 组重复复合键。",
            )
        if payload["invalid_source_rows"]:
            block(
                f"{name.upper()}_SOURCE",
                f"{name} 有 {payload['invalid_source_rows']} 行来源不在审计白名单。",
            )

    if daily["invalid_price_rows"]:
        block("DAILY_BAR_VALUES", "daily_bars 存在非法价格区间。")
    if adj["invalid_factor_rows"]:
        block("ADJ_FACTOR_VALUES", "adj_factors 存在空值或非正复权因子。")
    if keys["audited_daily_keys_without_adj"] or keys["adj_keys_without_audited_daily"]:
        block(
            "DAILY_ADJ_KEY_MISMATCH",
            "daily_bars 与 adj_factors 的审计日期键未逐行对齐。",
        )
    financial_coverage = financial["symbol_coverage_ratio"]
    if financial_coverage is None or financial_coverage < minimum_market_coverage:
        block(
            "S2_FINANCIAL_COVERAGE_INCOMPLETE",
            (
                f"财务覆盖率 {financial_coverage!s} 低于闸门 "
                f"{minimum_market_coverage:.0%}；S2 未完成，S6 不得放行。"
            ),
        )
    if financial["missing_required_metrics"]:
        block(
            "FINANCIAL_METRICS_MISSING",
            f"财务底表缺少指标：{', '.join(financial['missing_required_metrics'])}。",
        )
    if financial_coverage is not None and financial_coverage >= minimum_market_coverage:
        for metric in REQUIRED_FINANCIAL_FACTORS:
            metric_payload = financial["metrics"].get(metric)
            metric_coverage = (
                metric_payload.get("symbol_coverage_ratio")
                if isinstance(metric_payload, dict)
                else None
            )
            if metric_coverage is None or metric_coverage < minimum_market_coverage:
                block(
                    f"FINANCIAL_{metric.upper()}_COVERAGE",
                    (
                        f"财务指标 {metric} 股票覆盖率 {metric_coverage!s} "
                        f"低于闸门 {minimum_market_coverage:.0%}。"
                    ),
                )
    if financial["pit"]["anomaly_rows"]:
        block(
            "FINANCIAL_PIT_ANOMALY",
            f"财务 available_time 口径有 {financial['pit']['anomaly_rows']} 行异常。",
        )
    provider_basis_ratio = financial["pit"]["provider_pub_date_end_of_day_ratio"]
    if (
        provider_basis_ratio is None
        or provider_basis_ratio < MIN_PROVIDER_PUB_DATE_BASIS_RATIO
    ):
        block(
            "FINANCIAL_PROVIDER_PUB_DATE_BASIS",
            (
                "provider_pub_date_end_of_day 比例 "
                f"{provider_basis_ratio!s} 低于 "
                f"{MIN_PROVIDER_PUB_DATE_BASIS_RATIO:.0%}。"
            ),
        )
    if financial_coverage is not None and financial_coverage >= minimum_market_coverage:
        depth_metrics = financial["depth_contract"]["metric_depth"]
        for metric in REQUIRED_FINANCIAL_FACTORS:
            depth = depth_metrics.get(metric)
            if not isinstance(depth, dict):
                continue
            for dimension, ratio_key, label in (
                ("depth", "depth_sufficient_ratio", "成熟/次新自适应季度深度"),
                ("cross_year", "cross_year_sufficient_ratio", "跨年截面"),
                ("freshness", "fresh_ratio", "近端可披露季度覆盖"),
            ):
                ratio = depth[ratio_key]
                if (
                    ratio is not None
                    and ratio >= MIN_FINANCIAL_SYMBOL_PASS_RATIO
                ):
                    continue
                warnings.append(
                    {
                        "code": (
                            f"FINANCIAL_{metric.upper()}_{dimension.upper()}"
                        ),
                        "message": (
                            f"{metric} {label}诊断率 {ratio!s} 低于参考值 "
                            f"{MIN_FINANCIAL_SYMBOL_PASS_RATIO:.0%}；"
                            "该统计反映 provider cadence/字段空值，不等同于采集漏失。"
                            "S2 键闭环、PIT 完整性和固定 factor_zscores 截面仍是硬门。"
                        ),
                        "kind": "provider_null_diagnostic",
                        "metric": metric,
                        "dimension": dimension,
                        "observed_ratio": ratio,
                        "reference_ratio": MIN_FINANCIAL_SYMBOL_PASS_RATIO,
                        "provider_cadence": depth["provider_cadence"],
                        "provider_expected_quarters": depth[
                            "provider_expected_quarters"
                        ],
                        "blocking": False,
                    }
                )
    if valuation["pit_anomaly_rows"]:
        block(
            "VALUATION_PIT_ANOMALY",
            f"估值 available_time 口径有 {valuation['pit_anomaly_rows']} 行异常。",
        )
    historical_sector = sector["historical_backfill"]
    if (
        historical_sector["plates"] < minimum_sector_plates
        or historical_sector["dates"] < minimum_sector_dates
        or historical_sector["rectangular_gap_rows"]
    ):
        block(
            "SECTOR_FLOW_COVERAGE",
            (
                f"M3 {M3_SECTOR_FLOW_SOURCE} 历史子集仅 "
                f"{historical_sector['plates']} 板块/{historical_sector['dates']} 日期，"
                f"矩形缺口 {historical_sector['rectangular_gap_rows']} 行；"
                f"闸门要求至少 {minimum_sector_plates}/{minimum_sector_dates} 且无缺口。"
            ),
        )
    if historical_sector["mixed_source_rows_in_window"]:
        block(
            "SECTOR_FLOW_HISTORICAL_SOURCE",
            (
                f"M3 历史窗 {historical_sector['date_range'][0]}.."
                f"{historical_sector['date_range'][1]} 内有 "
                f"{historical_sector['mixed_source_rows_in_window']} 行不来自 "
                f"DAY 口径 {M3_SECTOR_FLOW_SOURCE}。"
            ),
        )
    membership_history = sector["membership_pit_history"]
    if membership_history["duplicate_key_groups"]:
        block(
            "SECTOR_CONSTITUENT_SNAPSHOT_DUPLICATES",
            (
                "sector_constituent_snapshots 存在 "
                f"{membership_history['duplicate_key_groups']} 组重复复合键。"
            ),
        )
    broad_sections = {
        "DAILY_BARS": daily["latest_broad_cross_section"],
        "ADJ_FACTORS": adj["latest_broad_cross_section"],
        "VALUATION_DAILY": valuation["latest_broad_cross_section"],
    }
    for label, broad in broad_sections.items():
        if (
            not broad["qualified"]
            or not broad["date"]
            or broad["entities"] < broad["minimum_entities"]
        ):
            block(
                f"{label}_BROAD_CROSS_SECTION_MISSING",
                (
                    f"{label} 在最近候选交易日没有达到 "
                    f"{broad['minimum_entities']} 个已上市 A 股的广覆盖截面。"
                ),
            )
    if all(payload["qualified"] for payload in broad_sections.values()):
        daily_latest = broad_sections["DAILY_BARS"]["date"]
        if broad_sections["ADJ_FACTORS"]["date"] != daily_latest:
            block(
                "ADJ_FACTOR_STALE",
                "adj_factors 最新广覆盖截面未与 daily_bars 对齐。",
            )
        if broad_sections["VALUATION_DAILY"]["date"] != daily_latest:
            block(
                "VALUATION_STALE",
                "valuation_daily 最新广覆盖截面未与 daily_bars 对齐。",
            )
    for item in factors:
        if item["status"] in {"live_only", "history_excluded_pit_gap"}:
            continue
        if item["status"] != "sufficient":
            block(
                f"FACTOR_{str(item['factor']).upper()}_{str(item['status']).upper()}",
                f"{item['factor']} PIT 可测性为 {item['status']}。",
            )
    automated_checks_pass = not blockers
    if not report["external_pit_pairing"]["accepted"]:
        block(
            "EXTERNAL_PIT_PAIRING_PENDING",
            "尚无显式外部数据源 PIT 对拍签认证据，自动检查通过也不得进入 S7。",
            kind="manual_evidence",
        )
    ready_for_s7 = automated_checks_pass and not blockers
    return {
        "status": "pass" if ready_for_s7 else "blocked",
        "automated_checks_pass": automated_checks_pass,
        "ready_for_s7": ready_for_s7,
        "blockers": blockers,
        "warnings": warnings,
    }


def build_data_health_report(
    database_path: Path,
    *,
    as_of_date: date | None = None,
    external_pit_pairing_evidence: Path | None = None,
    minimum_market_coverage: float = 0.90,
    minimum_factor_cross_section: int = 100,
    minimum_sector_plates: int = 100,
    minimum_sector_dates: int = 200,
    sample_size: int = 5,
    sample_seed: int = 20260725,
    factor_probe: FactorProbe = factor_zscores,
) -> dict[str, Any]:
    """Build the P3.3-S6 gate report without opening any writable DB connection."""

    if not 0 < minimum_market_coverage <= 1:
        raise ValueError("minimum_market_coverage must be in (0, 1]")
    if minimum_factor_cross_section < 1:
        raise ValueError("minimum_factor_cross_section must be positive")
    if minimum_sector_plates < 1 or minimum_sector_dates < 5:
        raise ValueError("sector thresholds are invalid")
    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    audit_date = as_of_date or datetime.now(MARKET_TIMEZONE).date()

    with readonly_connection(database_path) as connection:
        table_names = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing_tables = sorted(REQUIRED_TABLES - table_names)
        if missing_tables:
            raise RuntimeError(f"S6 required tables are missing: {', '.join(missing_tables)}")
        query_only = bool(int(_one(connection, "PRAGMA query_only")[0]))
        universe = _one(
            connection,
            """
            SELECT COUNT(*) AS securities
            FROM securities
            WHERE market = 'CN' AND list_status = 'listed'
            """,
        )
        universe_size = int(universe["securities"])
        candidate_dates = _latest_trade_date_candidates(connection)
        input_coverage = {
            "daily_bars": _daily_bar_audit(
                connection,
                universe_size=universe_size,
                minimum_market_coverage=minimum_market_coverage,
                candidate_dates=candidate_dates,
            ),
            "adj_factors": _adj_factor_audit(
                connection,
                universe_size=universe_size,
                minimum_market_coverage=minimum_market_coverage,
                candidate_dates=candidate_dates,
            ),
            "financial_indicators": _financial_audit(
                connection,
                universe_size=universe_size,
                as_of_date=audit_date,
            ),
            "valuation_daily": _valuation_audit(
                connection,
                universe_size=universe_size,
                minimum_market_coverage=minimum_market_coverage,
                candidate_dates=candidate_dates,
            ),
            "sector_flow_daily": _sector_flow_audit(connection),
        }
        key_audit = _daily_adj_key_audit(connection)
        schedule = _pick_probe_dates(connection)
        membership_visibility = _membership_pit_visibility(
            connection,
            flow_probe_dates=schedule["sector_flow_one_year"],
        )
        samples = _pit_samples(
            connection,
            sample_size=sample_size,
            seed=sample_seed,
        )
        samples["manifest_schema_version"] = PIT_MANIFEST_SCHEMA_VERSION
        samples["manifest_sha256"] = _pit_manifest_sha256(samples)

    external_pairing = _external_pairing_evidence(
        external_pit_pairing_evidence,
        pit_samples=samples,
    )
    if external_pairing["accepted"]:
        samples["external_source_pairing"] = "evidence_supplied"
        samples["external_pairing_evidence_sha256"] = external_pairing["sha256"]

    factor_availability = _factor_availability(
        database_path,
        schedule=schedule,
        membership_pit_visibility=membership_visibility,
        minimum_cross_section=minimum_factor_cross_section,
        probe=factor_probe,
    )
    _diagnose_factor_gaps(
        factor_availability,
        input_coverage=input_coverage,
        key_audit=key_audit,
        minimum_market_coverage=minimum_market_coverage,
    )
    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "as_of_date": audit_date.isoformat(),
        "database": {
            "path": str(database_path.expanduser().resolve()),
            "open_mode": "ro",
            "query_only": query_only,
        },
        "thresholds": {
            "minimum_market_coverage": minimum_market_coverage,
            "minimum_factor_cross_section": minimum_factor_cross_section,
            "minimum_sector_plates": minimum_sector_plates,
            "minimum_sector_dates": minimum_sector_dates,
            "financial_target_quarters": FINANCIAL_TARGET_QUARTERS,
            "financial_minimum_mature_quarters": (
                FINANCIAL_MINIMUM_MATURE_QUARTERS
            ),
            "minimum_financial_symbol_pass_ratio": (
                MIN_FINANCIAL_SYMBOL_PASS_RATIO
            ),
            "minimum_provider_pub_date_basis_ratio": (
                MIN_PROVIDER_PUB_DATE_BASIS_RATIO
            ),
        },
        "universe": {
            "definition": "securities.market='CN' AND list_status='listed'",
            "securities": universe_size,
        },
        "input_coverage": input_coverage,
        "daily_adj_key_audit": key_audit,
        "factor_availability": factor_availability,
        "historical_factor_scope": {
            "candidate_factors": list(HISTORICAL_FACTOR_CANDIDATES),
            "candidate_count": len(HISTORICAL_FACTOR_CANDIDATES),
            "history_excluded_pit_gap": list(
                HISTORY_EXCLUDED_PIT_GAP_FACTORS
            ),
            "ruling_date": "2026-07-25",
        },
        "pit_samples": samples,
        "external_pit_pairing": external_pairing,
    }
    report["gate"] = _gate(
        report,
        minimum_market_coverage=minimum_market_coverage,
        minimum_sector_plates=minimum_sector_plates,
        minimum_sector_dates=minimum_sector_dates,
    )
    return report


def _display(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_data_health_markdown(report: dict[str, Any]) -> str:
    """Render the same machine-readable S6 report as an audit-friendly Markdown file."""

    gate = report["gate"]
    inputs = report["input_coverage"]
    lines = [
        "# P3.3-S6 回填后数据体检",
        "",
        f"- 生成时间（UTC）：{report['generated_at']}",
        f"- 数据库模式：`{report['database']['open_mode']}` / "
        f"`query_only={str(report['database']['query_only']).lower()}`",
        f"- 闸门：**{str(gate['status']).upper()}**",
        f"- 自动检查通过：`{str(gate['automated_checks_pass']).lower()}`",
        f"- 可进入 S7：`{str(gate['ready_for_s7']).lower()}`",
        f"- 外部 PIT 对拍证据："
        f"`{'accepted' if report['external_pit_pairing']['accepted'] else 'pending'}`",
        f"- M3 历史候选：`{report['historical_factor_scope']['candidate_count']}` "
        "因子；`net_inflow_5d` 为 `history_excluded_pit_gap` / live-forward",
        "",
        "## 输入底表覆盖",
        "",
        "| 底表 | 行数 | 标的/板块 | 日期/报告期范围 | 重复键组 | 非法来源行 |",
        "|---|---:|---:|---|---:|---:|",
    ]
    rows = (
        (
            "daily_bars",
            inputs["daily_bars"],
            inputs["daily_bars"]["symbols"],
            inputs["daily_bars"]["date_range"],
        ),
        (
            "adj_factors",
            inputs["adj_factors"],
            inputs["adj_factors"]["symbols"],
            inputs["adj_factors"]["date_range"],
        ),
        (
            "financial_indicators",
            inputs["financial_indicators"],
            inputs["financial_indicators"]["symbols"],
            inputs["financial_indicators"]["report_period_range"],
        ),
        (
            "valuation_daily",
            inputs["valuation_daily"],
            inputs["valuation_daily"]["symbols"],
            inputs["valuation_daily"]["date_range"],
        ),
        (
            "sector_flow_daily",
            inputs["sector_flow_daily"],
            inputs["sector_flow_daily"]["plates"],
            inputs["sector_flow_daily"]["date_range"],
        ),
    )
    for name, payload, entities, observed_range in rows:
        lines.append(
            f"| {name} | {payload['rows']:,} | {entities:,} | "
            f"{_display(observed_range[0])} → {_display(observed_range[1])} | "
            f"{payload['duplicate_key_groups']} | {payload['invalid_source_rows']} |"
        )
    lines.extend(["", "### 最新广覆盖截面", ""])
    for name in ("daily_bars", "adj_factors", "valuation_daily"):
        broad = inputs[name]["latest_broad_cross_section"]
        lines.append(
            f"- `{name}`：date={_display(broad['date'])}，"
            f"entities={broad['entities']}/{broad['minimum_entities']}，"
            f"qualified={str(broad['qualified']).lower()}"
        )

    sector = inputs["sector_flow_daily"]
    historical_sector = sector["historical_backfill"]
    live_forward_sector = sector["live_forward"]
    lines.extend(
        [
            "",
            "### 板块资金流历史窗与 live-forward",
            "",
            f"- M3 历史子集 `{historical_sector['source']}`："
            f"{historical_sector['rows']:,} 行 / "
            f"{historical_sector['plates']:,} 板块 / "
            f"{historical_sector['dates']:,} 日，"
            f"{_display(historical_sector['date_range'][0])} → "
            f"{_display(historical_sector['date_range'][1])}；"
            f"矩形缺口={historical_sector['rectangular_gap_rows']:,}，"
            f"窗内混源={historical_sector['mixed_source_rows_in_window']:,}",
            "- 历史窗后 live-forward："
            f"{live_forward_sector['rows']:,} 行 / "
            f"{live_forward_sector['dates']:,} 日，"
            f"{_display(live_forward_sector['date_range'][0])} → "
            f"{_display(live_forward_sector['date_range'][1])}；"
            "source_counts="
            f"`{json.dumps(live_forward_sector['source_counts'], ensure_ascii=False)}`",
        ]
    )

    key_audit = report["daily_adj_key_audit"]
    lines.extend(
        [
            "",
            "## 日线与复权键",
            "",
            f"- 审计日线缺复权：{key_audit['audited_daily_keys_without_adj']:,}",
            f"- 复权缺审计日线：{key_audit['adj_keys_without_audited_daily']:,}",
            "",
            "## 财务指标与 available_time",
            "",
            "| 指标 | 行数 | 股票数 | 非空率 | 非空季度数 | 年数 | 报告期 |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for metric in REQUIRED_FINANCIAL_FACTORS:
        payload = inputs["financial_indicators"]["metrics"].get(metric)
        if payload is None:
            lines.append(f"| {metric} | 0 | 0 | — | 0 | 0 | — |")
            continue
        lines.append(
            f"| {metric} | {payload['rows']:,} | {payload['symbols']:,} | "
            f"{_display(payload['non_null_ratio'])} | "
            f"{payload['non_null_distinct_periods']} | {payload['distinct_years']} | "
            f"{_display(payload['period_range'][0])} → "
            f"{_display(payload['period_range'][1])} |"
        )
    financial_pit = inputs["financial_indicators"]["pit"]
    lines.extend(
        [
            "",
            f"- available_time basis："
            f"`{json.dumps(financial_pit['available_time_basis_counts'], ensure_ascii=False)}`",
            f"- provider_pub_date_end_of_day 比例："
            f"{_display(financial_pit['provider_pub_date_end_of_day_ratio'])} "
            f"（闸门 ≥ {MIN_PROVIDER_PUB_DATE_BASIS_RATIO:.0%}）",
            f"- 财务 PIT 异常行：{financial_pit['anomaly_rows']:,}",
            f"- 估值 PIT 异常行：{inputs['valuation_daily']['pit_anomaly_rows']:,}",
            "- 板块成分 PIT 日快照："
            f"{inputs['sector_flow_daily']['membership_pit_history']['rows']:,} 行 / "
            f"{inputs['sector_flow_daily']['membership_pit_history']['dates']:,} 日，"
            "仅供 2026-07-25 裁定后的前向窗口",
            "",
            "### 财务 40 季度目标与最低可测深度",
            "",
        ]
    )
    depth_contract = inputs["financial_indicators"]["depth_contract"]
    listing_basis = depth_contract["listing_date_basis_counts"]
    lines.extend(
        [
            f"- 深度审计全集：{depth_contract['universe_definition']}",
            f"- 深度审计股票数：{depth_contract['universe_symbols']:,}",
            "- Provider 不支持股票数："
            f"{depth_contract['provider_unsupported_symbols']:,}"
            "（不纳入财务深度诊断分母）",
            "- 上市日期口径："
            f"security_master={listing_basis['security_master']:,}，"
            f"first_audited_bar={listing_basis['first_audited_bar']:,}，"
            f"unknown={listing_basis['unknown']:,}",
            "- 无已可披露季度的次新股："
            f"{depth_contract['new_symbols_without_publishable_quarter']:,}"
            "（不纳入深度分母）",
            "",
            "| 指标 | provider cadence | 原始季度深度诊断率 | ≥5年诊断率 | "
            "近端诊断率 | 40季度达成率 | 代表性缺口 |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for metric in REQUIRED_FINANCIAL_FACTORS:
        depth = depth_contract["metric_depth"].get(metric, {})
        gap = (depth.get("representative_gaps") or [{}])[0]
        gap_text = (
            f"{gap.get('symbol')} actual={gap.get('non_null_quarters')}/"
            f"min={gap.get('minimum_quarters')} latest="
            f"{gap.get('latest_non_null_period')}/{gap.get('latest_required_period')}"
            if gap
            else "—"
        )
        lines.append(
            f"| {metric} | {_display(depth.get('provider_cadence'))} "
            f"{_display(depth.get('provider_expected_quarters'))} | "
            f"{_display(depth.get('depth_sufficient_ratio'))} | "
            f"{_display(depth.get('cross_year_sufficient_ratio'))} | "
            f"{_display(depth.get('fresh_ratio'))} | "
            f"{_display(depth.get('target_40_quarters_achieved_ratio'))} | "
            f"{gap_text} |"
        )
    lines.extend(
        [
            "",
            "## 逐因子 PIT 可测性",
            "",
            "| 因子 | 分组 | 状态 | 非空截面统计 | 历史决策日截面 n | "
            "代表性缺口/原因 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for item in report["factor_availability"]["factors"]:
        observations = ", ".join(
            f"{probe['date']}={probe['n']}/{probe['eligible']}"
            for probe in item["probes"]
        ) or "—"
        summary = item["non_null_summary"]
        summary_text = (
            f"min={_display(summary['minimum_n'])}, "
            f"max={_display(summary['maximum_n'])}, "
            f"pass={summary['sufficient_probe_count']}/{summary['probe_count']}"
        )
        gaps = item.get("representative_gaps") or []
        gap_evidence = (
            "; ".join(
                f"{gap['date']}:n={gap['n']}"
                for gap in gaps
            )
            or "—"
        )
        reason = str(item.get("reason") or "")
        gap_text = (
            f"{reason}; {gap_evidence}"
            if reason
            else gap_evidence
        )
        lines.append(
            f"| {item['factor']} | {item['group']} | {item['status']} | "
            f"{summary_text} | {observations} | {gap_text} |"
        )
    membership_visibility = report["factor_availability"][
        "sector_membership_pit_visibility"
    ]
    lines.extend(["", "### 资金流探针的成分 PIT 可见性", ""])
    if membership_visibility:
        for probe_date, payload in membership_visibility.items():
            lines.append(
                f"- `{probe_date}`：rows={payload['rows']}，"
                f"symbols={payload['symbols']}，visible="
                f"{str(payload['visible']).lower()}，cutoff={payload['decision_cutoff']}"
            )
    else:
        lines.append("- 无暖机后的可用资金流探针日期。")

    evidence = report["external_pit_pairing"]
    lines.extend(
        [
            "",
            "## 外部 PIT 对拍签认",
            "",
            f"- accepted：`{str(evidence['accepted']).lower()}`",
            f"- basename：{_display(evidence['basename'])}",
            f"- sha256：{_display(evidence['sha256'])}",
            f"- bytes：{evidence['bytes']}",
            f"- schema_version：{_display(evidence['schema_version'])}",
            "- pit_manifest_schema_version："
            f"{_display(evidence['pit_manifest_schema_version'])}",
            f"- pit_manifest_sha256：{_display(evidence['pit_manifest_sha256'])}",
            f"- reviewer_role：{_display(evidence['reviewer_role'])}",
            f"- reviewed_at：{_display(evidence['reviewed_at'])}",
            f"- sample_count：{evidence['sample_count']}",
        ]
    )

    lines.extend(["", "## 阻断项", ""])
    if gate["blockers"]:
        lines.extend(
            f"- `{item['code']}`：{item['message']}"
            for item in gate["blockers"]
        )
    else:
        lines.append("- 无")
    lines.extend(["", "## 风险与保留项", ""])
    lines.extend(
        f"- `{item['code']}`：{item['message']}"
        for item in gate["warnings"]
    )
    lines.extend(
        [
            "",
            "> 本报告由 SQLite `mode=ro` + `PRAGMA query_only=ON` 生成；"
            "未注册 JobSpec、未写 JobRun、未修改权重或交易安全闸。",
            "",
        ]
    )
    return "\n".join(lines)
