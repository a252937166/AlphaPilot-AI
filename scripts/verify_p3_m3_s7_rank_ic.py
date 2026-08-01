from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

SCHEMA_VERSION = "p3.3-s7-independent-rank-ic-v1"
AUDITED_DAILY_BAR_SOURCES = ("akshare", "baostock", "futu", "futu-close", "sina")
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
DECISION_TIME = time(19, 30)
MIN_LISTING_AGE_DAYS = 60
DEFAULT_TOLERANCE = 1e-10
PREDECLARED_CALENDAR_SESSIONS = 1_838
PREDECLARED_DECISION_PERIODS = 91
PREDECLARED_MIN_PAIRED_SYMBOLS = 100


class VerificationError(RuntimeError):
    """Fail-closed error for an unverifiable independent Rank-IC result."""


@dataclass(frozen=True, slots=True)
class VerificationContract:
    factor: str
    sample_tag: str
    start_date: date
    end_date: date
    horizon_sessions: int
    rebalance_sessions: int


PREDECLARED_CONTRACT = VerificationContract(
    factor="roe",
    sample_tag="full",
    start_date=date(2019, 1, 2),
    end_date=date(2026, 7, 31),
    horizon_sessions=20,
    rebalance_sessions=20,
)


@dataclass(frozen=True, slots=True)
class SecurityRecord:
    listed_date: date | None
    first_audited_bar: date | None
    is_st: bool
    snapshot_at: datetime | None


@dataclass(frozen=True, slots=True)
class FinancialEvent:
    available_time: datetime
    row_id: int
    symbol: str
    report_period: str
    value: float


@dataclass(frozen=True, slots=True)
class DailySnapshot:
    close: float
    volume: float
    amount: float | None
    adj_factor: float
    adjustment_missing: bool


@contextmanager
def _read_only_connection(path: Path) -> Iterator[sqlite3.Connection]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=15.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        connection.execute("BEGIN")
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _finite_float(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _adjustment_value(value: object) -> tuple[float, bool]:
    """Mirror pandas fillna(1.0), but reject non-finite persisted adjustments."""

    if value is None:
        return 1.0, True
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return 1.0, True
    if math.isnan(number):
        return 1.0, True
    if not math.isfinite(number):
        raise VerificationError("adj_factors contains an infinite adj_factor")
    return number, False


def _required_finite_numeric(
    value: object,
    *,
    field: str,
    identity: str,
) -> float:
    number = _finite_float(value)
    if number is None:
        raise VerificationError(f"{identity} has invalid {field}")
    return number


def _optional_finite_numeric(
    value: object,
    *,
    field: str,
    identity: str,
) -> float | None:
    if value is None:
        return None
    number = _finite_float(value)
    if number is None:
        raise VerificationError(f"{identity} has non-finite {field}")
    return number


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _decision_bounds(day: date) -> tuple[datetime, datetime]:
    local_start = datetime.combine(day, time.min, tzinfo=MARKET_TIMEZONE)
    local_cutoff = datetime.combine(day, DECISION_TIME, tzinfo=MARKET_TIMEZONE)
    return local_start.astimezone(UTC), local_cutoff.astimezone(UTC)


def _source_placeholders() -> str:
    return ", ".join("?" for _ in AUDITED_DAILY_BAR_SOURCES)


def _require_schema(connection: sqlite3.Connection) -> None:
    expected = {
        "securities": {
            "symbol",
            "market",
            "list_status",
            "is_st",
            "snapshot_at",
            "listed_date",
        },
        "daily_bars": {
            "symbol",
            "trade_date",
            "close",
            "volume",
            "amount",
            "source",
        },
        "adj_factors": {"symbol", "trade_date", "adj_factor"},
        "financial_indicators": {
            "id",
            "symbol",
            "report_period",
            "metric",
            "value",
            "available_time",
        },
        "factor_ic_stats": {
            "factor",
            "sample_tag",
            "start_date",
            "end_date",
            "ic_mean",
            "ic_ir",
            "t_stat",
            "ic_positive_ratio",
            "long_short",
            "n_periods",
        },
    }
    for table, required_columns in expected.items():
        columns = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        missing = sorted(required_columns.difference(columns))
        if missing:
            raise VerificationError(f"{table} is missing required columns: {missing}")


def _calendar(
    connection: sqlite3.Connection,
    *,
    start_date: date,
    end_date: date,
) -> list[date]:
    rows = connection.execute(
        f"""
        SELECT DISTINCT trade_date
        FROM daily_bars
        WHERE trade_date >= ?
          AND trade_date <= ?
          AND source IN ({_source_placeholders()})
        ORDER BY trade_date
        """,
        (
            start_date.isoformat(),
            end_date.isoformat(),
            *AUDITED_DAILY_BAR_SOURCES,
        ),
    ).fetchall()
    result: list[date] = []
    for row in rows:
        parsed = _parse_date(row["trade_date"])
        if parsed is None:
            raise VerificationError(f"invalid daily_bars.trade_date: {row['trade_date']!r}")
        result.append(parsed)
    return result


def _calendar_cross_section_audit(
    connection: sqlite3.Connection,
    *,
    start_date: date,
    end_date: date,
) -> dict[str, int]:
    rows = connection.execute(
        f"""
        SELECT trade_date, COUNT(DISTINCT symbol) AS symbols
        FROM daily_bars
        WHERE trade_date >= ?
          AND trade_date <= ?
          AND source IN ({_source_placeholders()})
        GROUP BY trade_date
        ORDER BY trade_date
        """,
        (
            start_date.isoformat(),
            end_date.isoformat(),
            *AUDITED_DAILY_BAR_SOURCES,
        ),
    ).fetchall()
    counts: list[int] = []
    weekend_dates = 0
    for row in rows:
        parsed = _parse_date(row["trade_date"])
        if parsed is None:
            raise VerificationError(f"invalid daily_bars.trade_date: {row['trade_date']!r}")
        weekend_dates += int(parsed.weekday() >= 5)
        counts.append(int(row["symbols"]))
    return {
        "minimum_symbols": min(counts, default=0),
        "weekend_dates": weekend_dates,
    }


def _load_security_records(
    connection: sqlite3.Connection,
) -> dict[str, SecurityRecord]:
    first_rows = connection.execute(
        f"""
        SELECT symbol, MIN(trade_date) AS first_audited_bar
        FROM daily_bars
        WHERE source IN ({_source_placeholders()})
        GROUP BY symbol
        """,
        AUDITED_DAILY_BAR_SOURCES,
    ).fetchall()
    first_dates = {
        str(row["symbol"]): _parse_date(row["first_audited_bar"]) for row in first_rows
    }
    rows = connection.execute(
        """
        SELECT symbol, listed_date, is_st, snapshot_at
        FROM securities
        WHERE market = 'CN' AND list_status = 'listed'
        """
    ).fetchall()
    return {
        str(row["symbol"]): SecurityRecord(
            listed_date=_parse_date(row["listed_date"]),
            first_audited_bar=first_dates.get(str(row["symbol"])),
            is_st=bool(row["is_st"]),
            snapshot_at=_parse_datetime(row["snapshot_at"]),
        )
        for row in rows
    }


def _load_financial_events(
    connection: sqlite3.Connection,
    *,
    factor: str,
) -> list[FinancialEvent]:
    duplicate = connection.execute(
        """
        SELECT symbol, report_period, COUNT(*) AS row_count
        FROM financial_indicators
        WHERE metric = ?
        GROUP BY symbol, report_period
        HAVING COUNT(*) > 1
        LIMIT 1
        """,
        (factor,),
    ).fetchone()
    if duplicate is not None:
        raise VerificationError(
            "financial_indicators contains a duplicate factor key: "
            f"{duplicate['symbol']}/{duplicate['report_period']}"
        )

    events: list[FinancialEvent] = []
    rows = connection.execute(
        """
        SELECT id, symbol, report_period, value, available_time
        FROM financial_indicators
        WHERE metric = ? AND value IS NOT NULL
        """,
        (factor,),
    )
    for row in rows:
        available_time = _parse_datetime(row["available_time"])
        value = _finite_float(row["value"])
        report_period = str(row["report_period"] or "").strip()
        if available_time is None:
            raise VerificationError(
                f"financial row id={row['id']} has invalid available_time"
            )
        if value is None:
            raise VerificationError(f"financial row id={row['id']} has non-finite value")
        if not report_period:
            raise VerificationError(f"financial row id={row['id']} has empty report_period")
        events.append(
            FinancialEvent(
                available_time=available_time,
                row_id=int(row["id"]),
                symbol=str(row["symbol"]),
                report_period=report_period,
                value=value,
            )
        )
    events.sort(key=lambda item: (item.available_time, item.row_id))
    return events


def _daily_snapshot(
    connection: sqlite3.Connection,
    *,
    trade_date: date,
) -> dict[str, DailySnapshot]:
    rows = connection.execute(
        f"""
        SELECT bars.symbol, bars.close, bars.volume, bars.amount,
               factors.adj_factor
        FROM daily_bars AS bars
        LEFT JOIN adj_factors AS factors
          ON factors.symbol = bars.symbol
         AND factors.trade_date = bars.trade_date
        WHERE bars.trade_date = ?
          AND bars.source IN ({_source_placeholders()})
        ORDER BY bars.symbol
        """,
        (trade_date.isoformat(), *AUDITED_DAILY_BAR_SOURCES),
    ).fetchall()
    result: dict[str, DailySnapshot] = {}
    for row in rows:
        symbol = str(row["symbol"])
        identity = f"daily_bars {symbol}/{trade_date.isoformat()}"
        close = _required_finite_numeric(
            row["close"],
            field="close",
            identity=identity,
        )
        volume = _required_finite_numeric(
            row["volume"],
            field="volume",
            identity=identity,
        )
        amount = _optional_finite_numeric(
            row["amount"],
            field="amount",
            identity=identity,
        )
        adjustment, adjustment_missing = _adjustment_value(row["adj_factor"])
        if symbol in result:
            raise VerificationError(
                f"daily_bars contains duplicate audited rows for {symbol}/{trade_date}"
            )
        result[symbol] = DailySnapshot(
            close=close,
            volume=volume,
            amount=amount,
            adj_factor=adjustment,
            adjustment_missing=adjustment_missing,
        )
    return result


def _eligible_symbols(
    *,
    securities: Mapping[str, SecurityRecord],
    decision_bars: Mapping[str, DailySnapshot],
    decision_date: date,
) -> set[str]:
    day_start, cutoff = _decision_bounds(decision_date)
    listing_cutoff = decision_date - timedelta(days=MIN_LISTING_AGE_DAYS)
    result: set[str] = set()
    for symbol, bar in decision_bars.items():
        security = securities.get(symbol)
        if security is None:
            continue
        if bar.close <= 0 or bar.volume <= 0 or bar.amount is None or bar.amount <= 0:
            continue
        listing_date = security.listed_date or security.first_audited_bar
        if listing_date is None or listing_date > listing_cutoff:
            continue
        st_status_known = (
            security.snapshot_at is not None
            and day_start <= security.snapshot_at <= cutoff
        )
        if st_status_known and security.is_st:
            continue
        result.add(symbol)
    return result


def _linear_quantile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("quantile requires at least one value")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    fraction = position - lower_index
    return ordered[lower_index] + (
        ordered[upper_index] - ordered[lower_index]
    ) * fraction


def _winsorized_zscores(values: Mapping[str, float]) -> dict[str, float]:
    if not values:
        return {}
    finite = {symbol: number for symbol, number in values.items() if math.isfinite(number)}
    if not finite:
        return {}
    lower = _linear_quantile(list(finite.values()), 0.025)
    upper = _linear_quantile(list(finite.values()), 0.975)
    clipped = {
        symbol: min(upper, max(lower, number)) for symbol, number in finite.items()
    }
    mean = math.fsum(clipped.values()) / len(clipped)
    variance = math.fsum((number - mean) ** 2 for number in clipped.values()) / len(clipped)
    standard_deviation = math.sqrt(variance)
    if math.isclose(standard_deviation, 0.0, rel_tol=1e-5, abs_tol=1e-8):
        return dict.fromkeys(clipped, 0.0)
    return {
        symbol: (number - mean) / standard_deviation
        for symbol, number in clipped.items()
    }


def _average_ranks(values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    ranks: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = ((index + 1) + end) / 2.0
        for symbol, _value in ordered[index:end]:
            ranks[symbol] = average_rank
        index = end
    return ranks


def _pearson(left: Mapping[str, float], right: Mapping[str, float]) -> float | None:
    symbols = sorted(set(left).intersection(right))
    if len(symbols) < 2:
        return None
    left_values = [left[symbol] for symbol in symbols]
    right_values = [right[symbol] for symbol in symbols]
    left_mean = math.fsum(left_values) / len(left_values)
    right_mean = math.fsum(right_values) / len(right_values)
    left_centered = [value - left_mean for value in left_values]
    right_centered = [value - right_mean for value in right_values]
    left_sum_squares = math.fsum(value * value for value in left_centered)
    right_sum_squares = math.fsum(value * value for value in right_centered)
    if left_sum_squares <= 0 or right_sum_squares <= 0:
        return None
    covariance = math.fsum(
        left_value * right_value
        for left_value, right_value in zip(
            left_centered,
            right_centered,
            strict=True,
        )
    )
    result = covariance / math.sqrt(left_sum_squares * right_sum_squares)
    return result if math.isfinite(result) else None


def _rank_ic(scores: Mapping[str, float], returns: Mapping[str, float]) -> float | None:
    symbols = sorted(set(scores).intersection(returns))
    paired_scores = {
        symbol: scores[symbol]
        for symbol in symbols
        if math.isfinite(scores[symbol]) and math.isfinite(returns[symbol])
    }
    paired_returns = {symbol: returns[symbol] for symbol in paired_scores}
    if len(paired_scores) < 2:
        return None
    score_ranks = _average_ranks(paired_scores)
    return_ranks = _average_ranks(paired_returns)
    if len(set(score_ranks.values())) < 2 or len(set(return_ranks.values())) < 2:
        return None
    return _pearson(score_ranks, return_ranks)


def _summary(ic_values: Sequence[float]) -> dict[str, float | int | None]:
    finite = [value for value in ic_values if math.isfinite(value)]
    sample_count = len(finite)
    if sample_count == 0:
        return {
            "ic_mean": None,
            "ic_std": None,
            "ic_ir": None,
            "t_stat": None,
            "ic_positive_ratio": None,
            "n_periods": 0,
        }
    mean = math.fsum(finite) / sample_count
    standard_deviation: float | None = None
    if sample_count > 1:
        variance = math.fsum((value - mean) ** 2 for value in finite) / (
            sample_count - 1
        )
        standard_deviation = math.sqrt(variance)
    ic_ir = (
        mean / standard_deviation
        if standard_deviation is not None and standard_deviation > 0
        else None
    )
    t_stat = (
        mean / (standard_deviation / math.sqrt(sample_count))
        if standard_deviation is not None and standard_deviation > 0
        else None
    )
    return {
        "ic_mean": mean,
        "ic_std": standard_deviation,
        "ic_ir": ic_ir,
        "t_stat": t_stat,
        "ic_positive_ratio": sum(value > 0 for value in finite) / sample_count,
        "n_periods": sample_count,
    }


def _stored_exact_window(
    connection: sqlite3.Connection,
    *,
    contract: VerificationContract,
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT factor, sample_tag, start_date, end_date, ic_mean, ic_ir,
               t_stat, ic_positive_ratio, long_short, n_periods
        FROM factor_ic_stats
        WHERE factor = ?
          AND sample_tag = ?
          AND start_date = ?
          AND end_date = ?
        """,
        (
            contract.factor,
            contract.sample_tag,
            contract.start_date.isoformat(),
            contract.end_date.isoformat(),
        ),
    ).fetchall()
    if len(rows) != 1:
        raise VerificationError(
            "expected exactly one persisted exact-window factor_ic_stats row, "
            f"found {len(rows)}"
        )
    row = rows[0]
    identity = (
        "factor_ic_stats "
        f"{contract.factor}/{contract.sample_tag}/"
        f"{contract.start_date.isoformat()}..{contract.end_date.isoformat()}"
    )
    n_periods = int(row["n_periods"])
    if n_periods < 0:
        raise VerificationError(f"{identity} has a negative n_periods")
    return {
        "factor": str(row["factor"]),
        "sample_tag": str(row["sample_tag"]),
        "start_date": str(row["start_date"]),
        "end_date": str(row["end_date"]),
        "ic_mean": _optional_finite_numeric(
            row["ic_mean"],
            field="ic_mean",
            identity=identity,
        ),
        "ic_ir": _optional_finite_numeric(
            row["ic_ir"],
            field="ic_ir",
            identity=identity,
        ),
        "t_stat": _optional_finite_numeric(
            row["t_stat"],
            field="t_stat",
            identity=identity,
        ),
        "ic_positive_ratio": _optional_finite_numeric(
            row["ic_positive_ratio"],
            field="ic_positive_ratio",
            identity=identity,
        ),
        "long_short": _optional_finite_numeric(
            row["long_short"],
            field="long_short",
            identity=identity,
        ),
        "n_periods": n_periods,
    }


def _comparison(
    independent: Mapping[str, float | int | None],
    stored: Mapping[str, Any],
    *,
    tolerance: float,
) -> dict[str, Any]:
    fields: dict[str, dict[str, Any]] = {}
    for name in (
        "n_periods",
        "ic_mean",
        "ic_ir",
        "t_stat",
        "ic_positive_ratio",
    ):
        expected = independent[name]
        observed = stored[name]
        absolute_delta: float | None = None
        if name == "n_periods":
            matches = expected == observed
        elif expected is None or observed is None:
            matches = expected is None and observed is None
        else:
            expected_number = float(expected)
            observed_number = float(observed)
            absolute_delta = abs(expected_number - observed_number)
            matches = math.isclose(
                expected_number,
                observed_number,
                rel_tol=tolerance,
                abs_tol=tolerance,
            )
        fields[name] = {
            "independent": expected,
            "persisted": observed,
            "absolute_delta": absolute_delta,
            "matches": matches,
        }
    return {
        "tolerance": tolerance,
        "fields": fields,
        "all_match": all(bool(item["matches"]) for item in fields.values()),
        "uncompared_persisted_fields": ["long_short"],
    }


def _advance_financial_state(
    events: Sequence[FinancialEvent],
    *,
    next_event_index: int,
    cutoff: datetime,
    latest: dict[str, FinancialEvent],
) -> int:
    index = next_event_index
    while index < len(events) and events[index].available_time <= cutoff:
        event = events[index]
        current = latest.get(event.symbol)
        if current is None or event.report_period > current.report_period:
            latest[event.symbol] = event
        index += 1
    return index


def _adjusted_returns(
    *,
    symbols: set[str],
    entry_bars: Mapping[str, DailySnapshot],
    exit_bars: Mapping[str, DailySnapshot],
) -> tuple[dict[str, float], int, int]:
    returns: dict[str, float] = {}
    missing_adjustment_rows = 0
    missing_endpoint_symbols = 0
    for symbol in symbols:
        entry = entry_bars.get(symbol)
        exit_value = exit_bars.get(symbol)
        if entry is None or exit_value is None:
            missing_endpoint_symbols += 1
            continue
        missing_adjustment_rows += int(entry.adjustment_missing)
        missing_adjustment_rows += int(exit_value.adjustment_missing)
        entry_price = entry.close * entry.adj_factor
        exit_price = exit_value.close * exit_value.adj_factor
        if entry_price <= 0 or exit_price <= 0:
            missing_endpoint_symbols += 1
            continue
        realized = exit_price / entry_price - 1.0
        if math.isfinite(realized):
            returns[symbol] = realized
        else:
            missing_endpoint_symbols += 1
    return returns, missing_adjustment_rows, missing_endpoint_symbols


def _recompute_rank_ic(
    connection: sqlite3.Connection,
    *,
    contract: VerificationContract,
) -> dict[str, Any]:
    calendar = _calendar(
        connection,
        start_date=contract.start_date,
        end_date=contract.end_date,
    )
    if not calendar:
        raise VerificationError("the predeclared window has no audited trading sessions")
    if calendar[0] != contract.start_date or calendar[-1] != contract.end_date:
        raise VerificationError(
            "audited calendar does not exactly cover the predeclared endpoints: "
            f"{calendar[0]}..{calendar[-1]}"
        )
    securities = _load_security_records(connection)
    events = _load_financial_events(connection, factor=contract.factor)
    latest_financials: dict[str, FinancialEvent] = {}
    next_event_index = 0
    ic_values: list[float] = []
    periods: list[dict[str, Any]] = []

    for decision_index in range(0, len(calendar), contract.rebalance_sessions):
        exit_index = decision_index + contract.horizon_sessions
        if exit_index >= len(calendar):
            continue
        decision_date = calendar[decision_index]
        exit_date = calendar[exit_index]
        _day_start, cutoff = _decision_bounds(decision_date)
        next_event_index = _advance_financial_state(
            events,
            next_event_index=next_event_index,
            cutoff=cutoff,
            latest=latest_financials,
        )
        entry_bars = _daily_snapshot(connection, trade_date=decision_date)
        exit_bars = _daily_snapshot(connection, trade_date=exit_date)
        eligible = _eligible_symbols(
            securities=securities,
            decision_bars=entry_bars,
            decision_date=decision_date,
        )
        raw_scores = {
            symbol: latest_financials[symbol].value
            for symbol in eligible
            if symbol in latest_financials
        }
        scores = _winsorized_zscores(raw_scores)
        returns, missing_adjustments, missing_endpoints = _adjusted_returns(
            symbols=set(scores),
            entry_bars=entry_bars,
            exit_bars=exit_bars,
        )
        rank_ic = _rank_ic(scores, returns)
        if rank_ic is not None:
            ic_values.append(rank_ic)
        periods.append(
            {
                "decision_date": decision_date.isoformat(),
                "exit_date": exit_date.isoformat(),
                "eligible_symbols": len(eligible),
                "roe_symbols": len(scores),
                "paired_symbols": len(set(scores).intersection(returns)),
                "rank_ic": rank_ic,
                "financial_events_visible": next_event_index,
                "missing_adjustment_rows": missing_adjustments,
                "missing_endpoint_symbols": missing_endpoints,
            }
        )

    calendar_payload = "\n".join(item.isoformat() for item in calendar).encode()
    calendar_cross_section = _calendar_cross_section_audit(
        connection,
        start_date=contract.start_date,
        end_date=contract.end_date,
    )
    return {
        "summary": _summary(ic_values),
        "calendar": {
            "start_date": calendar[0].isoformat(),
            "end_date": calendar[-1].isoformat(),
            "sessions": len(calendar),
            "sha256": hashlib.sha256(calendar_payload).hexdigest(),
            **calendar_cross_section,
        },
        "inputs": {
            "listed_cn_securities": len(securities),
            "financial_events_scanned": len(events),
            "financial_events_visible_at_last_decision": next_event_index,
            "audited_daily_bar_sources": list(AUDITED_DAILY_BAR_SOURCES),
        },
        "periods": periods,
    }


def _validate_recomputed_contract(
    independent: Mapping[str, Any],
    *,
    contract: VerificationContract,
) -> dict[str, Any]:
    summary = independent.get("summary")
    calendar = independent.get("calendar")
    periods = independent.get("periods")
    if not isinstance(summary, Mapping):
        raise VerificationError("independent summary is missing")
    if not isinstance(calendar, Mapping) or not isinstance(periods, list):
        raise VerificationError("independent calendar audit is missing")

    n_periods = int(summary.get("n_periods") or 0)
    if n_periods <= 0:
        raise VerificationError("independent Rank-IC produced zero valid periods")
    for field in ("ic_mean", "ic_positive_ratio"):
        value = _finite_float(summary.get(field))
        if value is None:
            raise VerificationError(f"independent Rank-IC produced no finite {field}")

    checks: dict[str, Any] = {
        "valid_rank_ic_periods_positive": True,
        "calendar_sessions": int(calendar.get("sessions") or 0),
        "theoretical_decision_periods": len(periods),
        "predeclared_fixed_contract": contract == PREDECLARED_CONTRACT,
    }
    if contract == PREDECLARED_CONTRACT:
        if checks["calendar_sessions"] != PREDECLARED_CALENDAR_SESSIONS:
            raise VerificationError(
                "predeclared calendar session count changed: "
                f"{checks['calendar_sessions']} != {PREDECLARED_CALENDAR_SESSIONS}"
            )
        if checks["theoretical_decision_periods"] != PREDECLARED_DECISION_PERIODS:
            raise VerificationError(
                "predeclared decision-period count changed: "
                f"{checks['theoretical_decision_periods']} "
                f"!= {PREDECLARED_DECISION_PERIODS}"
            )
        if int(calendar.get("weekend_dates") or 0) != 0:
            raise VerificationError("predeclared calendar contains weekend dates")
        if int(calendar.get("minimum_symbols") or 0) < PREDECLARED_MIN_PAIRED_SYMBOLS:
            raise VerificationError(
                "predeclared calendar contains a sparse audited cross-section"
            )
        if len(periods) != PREDECLARED_DECISION_PERIODS:
            raise VerificationError(
                "predeclared Rank-IC period list is incomplete"
            )
        insufficient_periods = [
            str(item.get("decision_date"))
            for item in periods
            if int(item.get("paired_symbols") or 0)
            < PREDECLARED_MIN_PAIRED_SYMBOLS
            or _finite_float(item.get("rank_ic")) is None
        ]
        if insufficient_periods:
            raise VerificationError(
                "predeclared Rank-IC has insufficient or invalid periods: "
                f"{insufficient_periods[:5]}"
            )
        if n_periods != PREDECLARED_DECISION_PERIODS:
            raise VerificationError(
                "predeclared Rank-IC valid-period count changed: "
                f"{n_periods} != {PREDECLARED_DECISION_PERIODS}"
            )
        for field in ("ic_ir", "t_stat"):
            value = _finite_float(summary.get(field))
            if value is None:
                raise VerificationError(
                    f"predeclared Rank-IC produced no finite {field}"
                )
        checks.update(
            {
                "expected_calendar_sessions": PREDECLARED_CALENDAR_SESSIONS,
                "expected_theoretical_decision_periods": (
                    PREDECLARED_DECISION_PERIODS
                ),
                "minimum_paired_symbols_per_period": (
                    PREDECLARED_MIN_PAIRED_SYMBOLS
                ),
                "all_periods_valid": True,
                "finite_ic_ir": True,
                "finite_t_stat": True,
            }
        )
    return checks


def _verify_database_for_contract(
    database: Path,
    *,
    contract: VerificationContract = PREDECLARED_CONTRACT,
    tolerance: float = DEFAULT_TOLERANCE,
) -> dict[str, Any]:
    if contract.factor != "roe" or contract.sample_tag != "full":
        raise ValueError("this independent verifier is intentionally limited to full-window ROE")
    if contract.horizon_sessions <= 0 or contract.rebalance_sessions <= 0:
        raise ValueError("horizon and rebalance sessions must be positive")
    if contract.end_date < contract.start_date:
        raise ValueError("end_date must not precede start_date")
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance must be a finite non-negative number")

    resolved = database.expanduser().resolve()
    with _read_only_connection(resolved) as connection:
        _require_schema(connection)
        query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
        data_version_before = int(connection.execute("PRAGMA data_version").fetchone()[0])
        independent = _recompute_rank_ic(connection, contract=contract)
        contract_checks = _validate_recomputed_contract(
            independent,
            contract=contract,
        )
        stored = _stored_exact_window(connection, contract=contract)
        comparison = _comparison(
            independent["summary"],
            stored,
            tolerance=tolerance,
        )
        data_version_after = int(connection.execute("PRAGMA data_version").fetchone()[0])

    contract_payload = {
        **asdict(contract),
        "start_date": contract.start_date.isoformat(),
        "end_date": contract.end_date.isoformat(),
    }
    snapshot_stable = data_version_before == data_version_after
    status = (
        "pass"
        if comparison["all_match"] and query_only == 1 and snapshot_stable
        else "fail"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": status,
        "independence": {
            "implementation": "python_standard_library_and_direct_sqlite",
            "repository_factor_research_called": False,
            "repository_pit_called": False,
            "repository_metrics_called": False,
            "writes_to_database": False,
        },
        "predeclared_contract": contract_payload,
        "database": {
            "path": str(resolved),
            "open_mode": "sqlite_mode_ro",
            "query_only": query_only == 1,
            "data_version_before": data_version_before,
            "data_version_after": data_version_after,
            "transaction_snapshot": "single_read_transaction",
            "stable_during_verification": snapshot_stable,
        },
        "contract_checks": contract_checks,
        "independent": independent,
        "persisted_exact_window": stored,
        "comparison": comparison,
    }


def verify_database(
    database: Path,
    *,
    contract: VerificationContract = PREDECLARED_CONTRACT,
    tolerance: float = DEFAULT_TOLERANCE,
) -> dict[str, Any]:
    """Run only the immutable public S7 release contract."""

    if contract != PREDECLARED_CONTRACT:
        raise ValueError("the public verifier contract is immutable")
    if tolerance != DEFAULT_TOLERANCE:
        raise ValueError("the public verifier tolerance is immutable")
    return _verify_database_for_contract(
        database,
        contract=contract,
        tolerance=tolerance,
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Independently recompute the predeclared P3.3-S7 full-window ROE "
            "Rank-IC using direct read-only SQLite access and Python's standard library. "
            "The factor, dates, horizon and rebalance interval are intentionally fixed."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/alphapilot.db"),
        help="SQLite database opened with mode=ro and PRAGMA query_only=ON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="JSON evidence path. The database is never modified.",
    )
    return parser.parse_args()


def _validated_output_path(*, database: Path, output: Path) -> Path:
    database_path = database.expanduser().resolve()
    destination = output.expanduser().resolve()
    if destination.suffix.lower() != ".json":
        raise ValueError("independent evidence output must use a .json suffix")
    protected = (
        database_path,
        Path(f"{database_path}-wal"),
        Path(f"{database_path}-shm"),
        Path(f"{database_path}-journal"),
    )
    if _aliases_protected_path(destination, protected=protected):
        raise ValueError(
            "independent evidence output must not alias the SQLite database "
            "or one of its sidecars"
        )
    return destination


def _aliases_protected_path(
    candidate: Path,
    *,
    protected: Sequence[Path],
) -> bool:
    resolved_candidate = candidate.expanduser().resolve()
    for item in protected:
        resolved_item = item.expanduser().resolve()
        if resolved_candidate == resolved_item:
            return True
        if (
            resolved_candidate.exists()
            and resolved_item.exists()
            and resolved_candidate.samefile(resolved_item)
        ):
            return True
    return False


def _write_json(
    path: Path,
    report: Mapping[str, Any],
    *,
    database: Path,
) -> str:
    payload = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    destination = _validated_output_path(database=database, output=path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    protected = (
        database.expanduser().resolve(),
        Path(f"{database.expanduser().resolve()}-wal"),
        Path(f"{database.expanduser().resolve()}-shm"),
        Path(f"{database.expanduser().resolve()}-journal"),
    )
    if _aliases_protected_path(temporary, protected=protected):
        raise ValueError(
            "independent evidence temporary output must not alias the SQLite "
            "database or one of its sidecars"
        )
    temporary.write_text(f"{payload}\n", encoding="utf-8")
    temporary.replace(destination)
    return hashlib.sha256(f"{payload}\n".encode()).hexdigest()


def main() -> int:
    arguments = _arguments()
    try:
        database = arguments.db.expanduser().resolve()
        output = _validated_output_path(
            database=database,
            output=arguments.output,
        )
        report = verify_database(database)
        output_sha256 = _write_json(output, report, database=database)
    except (
        FileNotFoundError,
        OSError,
        ValueError,
        VerificationError,
        sqlite3.Error,
    ) as exc:
        print(
            f"S7 independent Rank-IC verification failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(output),
                "output_sha256": output_sha256,
                "comparison_all_match": report["comparison"]["all_match"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
