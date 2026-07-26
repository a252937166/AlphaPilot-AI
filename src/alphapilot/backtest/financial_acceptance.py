from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

SCHEMA_VERSION = "p3.3-s2-final-acceptance-v1"
PUBDATE_PLAN_SCHEMA_VERSION = "p3.3-s2-pubdate-plan-v1"
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
TARGET_QUARTERS = 40
MINIMUM_COVERED_SYMBOLS = 5_000
MINIMUM_PROVIDER_PUB_DATE_RATIO = 0.95
REQUIRED_METRICS = (
    "debt_ratio",
    "net_profit_yoy",
    "ocf_to_profit",
    "revenue_yoy",
    "roe",
)
_REQUIRED_METRIC_SET = frozenset(REQUIRED_METRICS)
_REPORT_PERIOD_PATTERN = re.compile(r"^[0-9]{4}Q[1-4]$")
_NEGATIVE_CHECKPOINT_KEY = "financial_no_data_periods"
_ALLOWED_AVAILABILITY_BASES = frozenset({"provider_pub_date_end_of_day", "stat_date_plus_45_days"})


@dataclass(frozen=True, slots=True)
class ShardContract:
    name: str
    symbol_min: int | None
    symbol_max_exclusive: int | None
    provider_request_budget: int


SHARD_CONTRACTS: dict[str, ShardContract] = {
    "aliyun": ShardContract(
        name="aliyun",
        symbol_min=None,
        symbol_max_exclusive=300_387,
        provider_request_budget=40_000,
    ),
    "dogcloud": ShardContract(
        name="dogcloud",
        symbol_min=300_387,
        symbol_max_exclusive=603_182,
        provider_request_budget=40_000,
    ),
    "lax": ShardContract(
        name="lax",
        symbol_min=603_182,
        symbol_max_exclusive=None,
        provider_request_budget=39_999,
    ),
}


def _sqlite_uri(path: Path) -> str:
    return f"file:{quote(str(path.expanduser().resolve()))}?mode=ro"


@contextmanager
def _read_only_connection(path: Path) -> Iterator[sqlite3.Connection]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {resolved}")
    connection = sqlite3.connect(_sqlite_uri(resolved), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        yield connection
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.expanduser().resolve().open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _parse_json_object(raw: object) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return dict(parsed) if isinstance(parsed, dict) else None


def _parse_datetime(raw: object) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_date(raw: object) -> date | None:
    text = str(raw or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _expected_available_time(payload: Mapping[str, Any]) -> datetime | None:
    basis = payload.get("available_time_basis")
    basis_date: date | None
    offset: int
    if basis == "provider_pub_date_end_of_day":
        pub_dates = payload.get("pub_dates")
        if not isinstance(pub_dates, list) or not pub_dates:
            return None
        basis_date = _parse_date(pub_dates[0])
        offset = 1
    elif basis == "stat_date_plus_45_days":
        basis_date = _parse_date(payload.get("stat_date"))
        offset = 45
    else:
        return None
    if basis_date is None:
        return None
    local = datetime.combine(
        basis_date + timedelta(days=offset),
        time.min,
        tzinfo=MARKET_TIMEZONE,
    )
    return local.astimezone(UTC)


def _completed_quarters(count: int, *, as_of_date: date) -> list[str]:
    if count <= 0:
        raise ValueError("quarter count must be positive")
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


def _is_supported_security(symbol: str, board: object) -> bool:
    return (
        len(symbol) == 6
        and symbol.isdigit()
        and str(board or "") != "北交所"
        and not symbol.startswith(("4", "8", "92"))
    )


def _security_universe(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT symbol, board
        FROM securities
        WHERE market = 'CN' AND list_status = 'listed'
        ORDER BY symbol
        """
    ).fetchall()
    supported = {
        str(row["symbol"])
        for row in rows
        if _is_supported_security(str(row["symbol"]), row["board"])
    }
    return {
        "definition": (
            "securities where market='CN' and list_status='listed', excluding "
            "board='北交所' and symbols starting with 4/8/92"
        ),
        "cn_listed_symbols": len(rows),
        "provider_supported_symbols": len(supported),
        "provider_unsupported_symbols": len(rows) - len(supported),
        "supported_symbol_set": supported,
    }


def _metric_coverage(
    connection: sqlite3.Connection,
    *,
    target_periods: Sequence[str],
    eligible_symbols: set[str],
) -> dict[str, Any]:
    placeholders = ",".join("?" for _ in target_periods)
    rows = connection.execute(
        f"""
        SELECT metric,
               symbol,
               COUNT(*) AS rows_count,
               SUM(CASE WHEN value IS NOT NULL THEN 1 ELSE 0 END) AS non_null_rows,
               MIN(report_period) AS min_period,
               MAX(report_period) AS max_period
        FROM financial_indicators
        WHERE report_period IN ({placeholders})
        GROUP BY metric, symbol
        ORDER BY metric, symbol
        """,
        tuple(target_periods),
    ).fetchall()
    per_metric: dict[str, dict[str, Any]] = {
        metric: {
            "rows": 0,
            "symbols": 0,
            "non_null_rows": 0,
            "period_min": None,
            "period_max": None,
        }
        for metric in REQUIRED_METRICS
    }
    extra_metrics: set[str] = set()
    for row in rows:
        symbol = str(row["symbol"])
        if symbol not in eligible_symbols:
            continue
        metric = str(row["metric"])
        if metric not in per_metric:
            extra_metrics.add(metric)
            continue
        payload = per_metric[metric]
        payload["rows"] += int(row["rows_count"])
        payload["symbols"] += 1
        payload["non_null_rows"] += int(row["non_null_rows"] or 0)
        observed_min = str(row["min_period"])
        observed_max = str(row["max_period"])
        current_min = payload["period_min"]
        current_max = payload["period_max"]
        payload["period_min"] = (
            observed_min if current_min is None else min(str(current_min), observed_min)
        )
        payload["period_max"] = (
            observed_max if current_max is None else max(str(current_max), observed_max)
        )
    for payload in per_metric.values():
        payload["symbol_coverage_ratio"] = _ratio(
            int(payload["symbols"]),
            len(eligible_symbols),
        )
        payload["non_null_ratio"] = _ratio(
            int(payload["non_null_rows"]),
            int(payload["rows"]),
        )
    covered_symbols = {
        str(row["symbol"])
        for row in connection.execute(
            f"""
            SELECT DISTINCT symbol
            FROM financial_indicators
            WHERE report_period IN ({placeholders})
            """,
            tuple(target_periods),
        )
        if str(row["symbol"]) in eligible_symbols
    }
    return {
        "symbols": len(covered_symbols),
        "symbol_coverage_ratio": _ratio(len(covered_symbols), len(eligible_symbols)),
        "metrics": per_metric,
        "missing_required_metrics": sorted(
            metric for metric, payload in per_metric.items() if int(payload["rows"]) == 0
        ),
        "extra_metrics": sorted(extra_metrics),
    }


def _key_and_checkpoint_closure(
    connection: sqlite3.Connection,
    *,
    target_periods: Sequence[str],
    eligible_symbols: set[str],
) -> dict[str, Any]:
    duplicate_groups = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT symbol, report_period, metric
                FROM financial_indicators
                GROUP BY symbol, report_period, metric
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
    )
    placeholders = ",".join("?" for _ in target_periods)
    bundle_rows = connection.execute(
        f"""
        SELECT symbol,
               report_period,
               COUNT(*) AS rows_count,
               GROUP_CONCAT(DISTINCT metric) AS metrics
        FROM financial_indicators
        WHERE report_period IN ({placeholders})
        GROUP BY symbol, report_period
        ORDER BY symbol, report_period
        """,
        tuple(target_periods),
    ).fetchall()
    positive_pairs: set[tuple[str, str]] = set()
    partial_pair_count = 0
    partial_pairs: list[dict[str, Any]] = []
    for row in bundle_rows:
        symbol = str(row["symbol"])
        if symbol not in eligible_symbols:
            continue
        period = str(row["report_period"])
        metrics = frozenset(str(row["metrics"] or "").split(","))
        rows_count = int(row["rows_count"])
        if metrics == _REQUIRED_METRIC_SET and rows_count == len(REQUIRED_METRICS):
            positive_pairs.add((symbol, period))
        else:
            partial_pair_count += 1
            if len(partial_pairs) < 20:
                partial_pairs.append(
                    {
                        "symbol": symbol,
                        "report_period": period,
                        "rows": rows_count,
                        "metrics": sorted(metrics - {""}),
                    }
                )

    target_set = set(target_periods)
    negative_pairs: set[tuple[str, str]] = set()
    invalid_profile_json = 0
    invalid_checkpoint_container = 0
    invalid_checkpoint_entries = 0
    duplicate_checkpoint_entries = 0
    outside_target_pairs = 0
    checkpoint_symbols: set[str] = set()
    for row in connection.execute(
        """
        SELECT symbol, profile
        FROM securities
        WHERE market = 'CN' AND list_status = 'listed'
        ORDER BY symbol
        """
    ):
        symbol = str(row["symbol"])
        if symbol not in eligible_symbols:
            continue
        profile = _parse_json_object(row["profile"])
        if profile is None:
            invalid_profile_json += 1
            continue
        raw_checkpoints = profile.get(_NEGATIVE_CHECKPOINT_KEY, [])
        if not isinstance(raw_checkpoints, list):
            invalid_checkpoint_container += 1
            continue
        seen_for_symbol: set[str] = set()
        for raw_period in raw_checkpoints:
            if not isinstance(raw_period, str) or not _REPORT_PERIOD_PATTERN.fullmatch(raw_period):
                invalid_checkpoint_entries += 1
                continue
            if raw_period in seen_for_symbol:
                duplicate_checkpoint_entries += 1
                continue
            seen_for_symbol.add(raw_period)
            if raw_period not in target_set:
                outside_target_pairs += 1
                continue
            negative_pairs.add((symbol, raw_period))
            checkpoint_symbols.add(symbol)

    overlaps = positive_pairs & negative_pairs
    unresolved_count = 0
    unresolved_samples: list[dict[str, str]] = []
    for symbol in sorted(eligible_symbols):
        for period in target_periods:
            pair = (symbol, period)
            if pair in positive_pairs or pair in negative_pairs:
                continue
            unresolved_count += 1
            if len(unresolved_samples) < 20:
                unresolved_samples.append({"symbol": symbol, "report_period": period})
    target_pairs = len(eligible_symbols) * len(target_periods)
    resolved_pairs = len(positive_pairs | negative_pairs)
    return {
        "target_symbol_periods": target_pairs,
        "positive_complete_pairs": len(positive_pairs),
        "negative_checkpoint_pairs": len(negative_pairs),
        "negative_checkpoint_symbols": len(checkpoint_symbols),
        "resolved_pairs": resolved_pairs,
        "resolved_ratio": _ratio(resolved_pairs, target_pairs),
        "partial_pair_count": partial_pair_count,
        "partial_pair_samples": partial_pairs,
        "unresolved_pairs": unresolved_count,
        "unresolved_samples": unresolved_samples,
        "positive_negative_overlaps": len(overlaps),
        "positive_negative_overlap_samples": [
            {"symbol": symbol, "report_period": period} for symbol, period in sorted(overlaps)[:20]
        ],
        "duplicate_financial_key_groups": duplicate_groups,
        "invalid_profile_json": invalid_profile_json,
        "invalid_checkpoint_container": invalid_checkpoint_container,
        "invalid_checkpoint_entries": invalid_checkpoint_entries,
        "duplicate_checkpoint_entries": duplicate_checkpoint_entries,
        "checkpoint_pairs_outside_target_window": outside_target_pairs,
    }


def _pit_audit(
    connection: sqlite3.Connection,
    *,
    eligible_symbols: set[str],
) -> dict[str, Any]:
    basis_counts: dict[str, int] = {}
    anomaly_counts = {
        "malformed_payload": 0,
        "missing_available_time": 0,
        "unsupported_basis": 0,
        "basis_fields_missing": 0,
        "available_time_mismatch": 0,
        "invalid_source": 0,
    }
    examples: list[dict[str, Any]] = []
    for row in connection.execute(
        """
        SELECT symbol, report_period, metric, source, available_time, payload
        FROM financial_indicators
        ORDER BY id
        """
    ):
        symbol = str(row["symbol"])
        if symbol not in eligible_symbols:
            continue
        payload = _parse_json_object(row["payload"])
        anomaly: str | None = None
        if payload is None:
            payload = {}
            anomaly = "malformed_payload"
            basis = "<malformed>"
        else:
            basis = str(payload.get("available_time_basis") or "<missing>")
        basis_counts[basis] = basis_counts.get(basis, 0) + 1
        if anomaly is None and str(row["source"]) != "baostock":
            anomaly = "invalid_source"
        available_time = _parse_datetime(row["available_time"])
        expected = _expected_available_time(payload)
        if anomaly is None and available_time is None:
            anomaly = "missing_available_time"
        elif anomaly is None and basis not in _ALLOWED_AVAILABILITY_BASES:
            anomaly = "unsupported_basis"
        elif anomaly is None and expected is None:
            anomaly = "basis_fields_missing"
        elif (
            anomaly is None
            and available_time is not None
            and expected is not None
            and abs((available_time - expected).total_seconds()) > 1
        ):
            anomaly = "available_time_mismatch"
        if anomaly is not None:
            anomaly_counts[anomaly] += 1
            if len(examples) < 20:
                examples.append(
                    {
                        "symbol": symbol,
                        "report_period": str(row["report_period"]),
                        "metric": str(row["metric"]),
                        "anomaly": anomaly,
                    }
                )
    total_rows = sum(basis_counts.values())
    provider_rows = basis_counts.get("provider_pub_date_end_of_day", 0)
    return {
        "available_time_basis_counts": dict(sorted(basis_counts.items())),
        "provider_pub_date_end_of_day_rows": provider_rows,
        "provider_pub_date_end_of_day_ratio": _ratio(provider_rows, total_rows),
        "anomaly_counts": anomaly_counts,
        "anomaly_rows": sum(anomaly_counts.values()),
        "examples": examples,
    }


def _pubdate_plan(
    connection: sqlite3.Connection,
    *,
    target_periods: Sequence[str],
    eligible_symbols: set[str],
    sample_size: int,
    seed: int,
) -> dict[str, Any]:
    if sample_size <= 0:
        raise ValueError("pubDate sample size must be positive")
    placeholders = ",".join("?" for _ in target_periods)
    candidates_by_symbol: dict[str, dict[str, Any]] = {}
    for row in connection.execute(
        f"""
        SELECT symbol, report_period, available_time, payload
        FROM financial_indicators
        WHERE metric = 'roe'
          AND report_period IN ({placeholders})
        ORDER BY symbol, report_period DESC
        """,
        tuple(target_periods),
    ):
        symbol = str(row["symbol"])
        if symbol not in eligible_symbols or symbol in candidates_by_symbol:
            continue
        payload = _parse_json_object(row["payload"])
        if payload is None:
            continue
        if payload.get("available_time_basis") != "provider_pub_date_end_of_day":
            continue
        pub_dates = payload.get("pub_dates")
        if not isinstance(pub_dates, list) or not pub_dates:
            continue
        pub_date = _parse_date(pub_dates[0])
        if pub_date is None:
            continue
        period = str(row["report_period"])
        if not _REPORT_PERIOD_PATTERN.fullmatch(period):
            continue
        candidates_by_symbol[symbol] = {
            "symbol": symbol,
            "report_period": period,
            "year": int(period[:4]),
            "quarter": int(period[-1]),
            "local_pub_date": pub_date.isoformat(),
            "local_available_time": str(row["available_time"]),
            "local_source_field": payload.get("source_field"),
            "planned_query": "BaoStock query_profit_data",
        }
    ranked = sorted(
        candidates_by_symbol.values(),
        key=lambda item: hashlib.sha256(
            f"{seed}|{item['symbol']}|{item['report_period']}".encode()
        ).hexdigest(),
    )
    samples = ranked[:sample_size]
    blockers: list[str] = []
    if len(samples) != sample_size:
        blockers.append(
            f"only {len(samples)} eligible pubDate candidates for sample_size={sample_size}"
        )
    return {
        "schema_version": PUBDATE_PLAN_SCHEMA_VERSION,
        "mode": "plan_only",
        "network_called": False,
        "provider_imported": False,
        "approved": False,
        "seed": seed,
        "selection_method": (
            "latest target-window ROE row per supported symbol, then ascending "
            "sha256(seed|symbol|report_period); no result-based resampling"
        ),
        "candidate_symbols": len(candidates_by_symbol),
        "sample_size": sample_size,
        "planned_provider_queries": len(samples),
        "hard_provider_query_cap": sample_size,
        "ready_for_authorized_execution": not blockers,
        "blockers": blockers,
        "samples": samples,
        "execution_invariants": [
            "execute only after S2 collection completes and the egress quota is authorized",
            "one process, one BaoStock connection, at most five actual queries",
            "10001011 or any query failure stops the audit without retry",
            "compare statDate, pubDate, and pubDate+1 Shanghai midnight converted to UTC",
            "five matches are required; incomplete or mismatched evidence cannot be signed",
        ],
    }


def _parse_stats(raw: object) -> dict[str, Any] | None:
    parsed = _parse_json_object(raw)
    return parsed if parsed is not None else None


def _shard_symbol_count(
    connection: sqlite3.Connection,
    contract: ShardContract,
) -> int:
    count = 0
    for row in connection.execute("SELECT symbol FROM securities ORDER BY symbol"):
        symbol = str(row["symbol"])
        if len(symbol) != 6 or not symbol.isdigit():
            continue
        numeric = int(symbol)
        if contract.symbol_min is not None and numeric < contract.symbol_min:
            continue
        if contract.symbol_max_exclusive is not None and numeric >= contract.symbol_max_exclusive:
            continue
        count += 1
    return count


def _idempotent_empty_run_reasons(
    *,
    status: object,
    error: object,
    finished_at: object,
    stats: Mapping[str, Any] | None,
    contract: ShardContract,
    expected_symbols_total: int,
) -> list[str]:
    reasons: list[str] = []
    if status != "ok":
        reasons.append(f"status must be ok, observed={status!r}")
    if error not in {None, ""}:
        reasons.append("error must be null")
    if finished_at in {None, ""}:
        reasons.append("finished_at must be present")
    if stats is None:
        return [*reasons, "stats must be a JSON object"]

    exact_values: dict[str, Any] = {
        "quarters_requested": TARGET_QUARTERS,
        "symbol_min": contract.symbol_min,
        "symbol_max_exclusive": contract.symbol_max_exclusive,
        "provider_request_budget": contract.provider_request_budget,
        "symbols_total": expected_symbols_total,
        "symbols_processed": expected_symbols_total,
        "symbols_done": 0,
        "symbols_with_data": 0,
        "symbols_failed": 0,
        "financial_quarters_queried": 0,
        "prior_revenue_queries": 0,
        "unavailable_checkpoints_added": 0,
        "metrics_inserted": 0,
        "metrics_updated": 0,
        "resume_symbol": None,
        "stopped_for_request_budget": False,
        "is_complete": True,
        "provider_probe_requests": 1,
        "provider_requests_estimated": 1,
        "failures": [],
    }
    for key, expected in exact_values.items():
        observed = stats.get(key)
        if observed != expected:
            reasons.append(f"{key} expected={expected!r}, observed={observed!r}")
    symbols_skipped = stats.get("symbols_skipped")
    symbols_unsupported = stats.get("symbols_unsupported")
    if not isinstance(symbols_skipped, int) or not isinstance(symbols_unsupported, int):
        reasons.append("symbols_skipped and symbols_unsupported must be integers")
    elif symbols_skipped + symbols_unsupported != expected_symbols_total:
        reasons.append("symbols_skipped + symbols_unsupported must equal symbols_total")
    provider_probe_rows = stats.get("provider_probe_rows")
    if not isinstance(provider_probe_rows, int) or provider_probe_rows < 0:
        reasons.append("provider_probe_rows must be a non-negative integer")
    skipped_checkpoints = stats.get("quarters_skipped_unavailable_checkpoint")
    if not isinstance(skipped_checkpoints, int) or skipped_checkpoints < 0:
        reasons.append("quarters_skipped_unavailable_checkpoint must be a non-negative integer")
    duration = stats.get("duration_seconds")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration < 0:
        reasons.append("duration_seconds must be a non-negative number")
    return reasons


def _shard_audit(path: Path, contract: ShardContract) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    with _read_only_connection(resolved) as connection:
        query_only = bool(int(connection.execute("PRAGMA query_only").fetchone()[0]))
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        expected_symbols = _shard_symbol_count(connection, contract)
        latest = connection.execute(
            """
            SELECT id, status, started_at, finished_at, stats, error
            FROM job_runs
            WHERE job_name = 'sync_financials'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if latest is None:
            return {
                "name": contract.name,
                "path": str(resolved),
                "sha256": _sha256(resolved),
                "open_mode": "ro",
                "query_only": query_only,
                "quick_check": quick_check,
                "expected_symbols_total": expected_symbols,
                "idempotent_empty_run_passed": False,
                "reasons": ["no sync_financials JobRun found"],
                "latest_job": None,
            }
        stats = _parse_stats(latest["stats"])
        reasons = _idempotent_empty_run_reasons(
            status=latest["status"],
            error=latest["error"],
            finished_at=latest["finished_at"],
            stats=stats,
            contract=contract,
            expected_symbols_total=expected_symbols,
        )
        if quick_check != "ok":
            reasons.append(f"PRAGMA quick_check expected='ok', observed={quick_check!r}")
        return {
            "name": contract.name,
            "path": str(resolved),
            "sha256": _sha256(resolved),
            "open_mode": "ro",
            "query_only": query_only,
            "quick_check": quick_check,
            "expected_boundary": {
                "symbol_min": contract.symbol_min,
                "symbol_max_exclusive": contract.symbol_max_exclusive,
            },
            "expected_symbols_total": expected_symbols,
            "idempotent_empty_run_passed": not reasons,
            "reasons": reasons,
            "latest_job": {
                "id": int(latest["id"]),
                "status": latest["status"],
                "started_at": latest["started_at"],
                "finished_at": latest["finished_at"],
                "error": latest["error"],
                "stats": stats,
            },
        }


def build_s2_financial_acceptance_report(
    database_path: Path,
    *,
    as_of_date: date,
    shard_databases: Mapping[str, Path] | None = None,
    minimum_covered_symbols: int = MINIMUM_COVERED_SYMBOLS,
    minimum_provider_pub_date_ratio: float = MINIMUM_PROVIDER_PUB_DATE_RATIO,
    pubdate_sample_size: int = 5,
    pubdate_seed: int = 20260726,
) -> dict[str, Any]:
    if minimum_covered_symbols <= 0:
        raise ValueError("minimum_covered_symbols must be positive")
    if not 0 < minimum_provider_pub_date_ratio <= 1:
        raise ValueError("minimum_provider_pub_date_ratio must be in (0, 1]")
    target_periods = _completed_quarters(TARGET_QUARTERS, as_of_date=as_of_date)
    resolved_database = database_path.expanduser().resolve()
    with _read_only_connection(resolved_database) as connection:
        query_only = bool(int(connection.execute("PRAGMA query_only").fetchone()[0]))
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        universe = _security_universe(connection)
        eligible_symbols = set(universe.pop("supported_symbol_set"))
        coverage = _metric_coverage(
            connection,
            target_periods=target_periods,
            eligible_symbols=eligible_symbols,
        )
        closure = _key_and_checkpoint_closure(
            connection,
            target_periods=target_periods,
            eligible_symbols=eligible_symbols,
        )
        pit = _pit_audit(connection, eligible_symbols=eligible_symbols)
        pubdate_plan = _pubdate_plan(
            connection,
            target_periods=target_periods,
            eligible_symbols=eligible_symbols,
            sample_size=pubdate_sample_size,
            seed=pubdate_seed,
        )

    provided_shards = dict(shard_databases or {})
    unknown_shards = sorted(set(provided_shards) - set(SHARD_CONTRACTS))
    if unknown_shards:
        raise ValueError(f"unknown shard names: {unknown_shards}")
    shard_reports = {
        name: _shard_audit(provided_shards[name], contract)
        for name, contract in SHARD_CONTRACTS.items()
        if name in provided_shards
    }

    blockers: list[dict[str, str]] = []

    def block(code: str, detail: str) -> None:
        blockers.append({"code": code, "detail": detail})

    if quick_check != "ok":
        block("SQLITE_QUICK_CHECK", f"PRAGMA quick_check={quick_check!r}")
    required_symbols = min(
        minimum_covered_symbols,
        int(universe["provider_supported_symbols"]),
    )
    if int(coverage["symbols"]) < required_symbols:
        block(
            "S2_FINANCIAL_COVERAGE_INCOMPLETE",
            f"covered={coverage['symbols']}, required={required_symbols}",
        )
    if coverage["missing_required_metrics"] or coverage["extra_metrics"]:
        block(
            "FINANCIAL_METRIC_SET",
            (f"missing={coverage['missing_required_metrics']}, extra={coverage['extra_metrics']}"),
        )
    for metric, payload in coverage["metrics"].items():
        if int(payload["symbols"]) < required_symbols:
            block(
                f"FINANCIAL_{metric.upper()}_COVERAGE",
                f"covered={payload['symbols']}, required={required_symbols}",
            )
    closure_block_fields = (
        "partial_pair_count",
        "unresolved_pairs",
        "positive_negative_overlaps",
        "duplicate_financial_key_groups",
        "invalid_profile_json",
        "invalid_checkpoint_container",
        "invalid_checkpoint_entries",
        "duplicate_checkpoint_entries",
    )
    for field in closure_block_fields:
        if int(closure[field]) != 0:
            block(
                f"FINANCIAL_CHECKPOINT_{field.upper()}",
                f"{field}={closure[field]}",
            )
    if int(pit["anomaly_rows"]) != 0:
        block("FINANCIAL_PIT_ANOMALY", f"anomaly_rows={pit['anomaly_rows']}")
    provider_ratio = pit["provider_pub_date_end_of_day_ratio"]
    if provider_ratio is None or provider_ratio < minimum_provider_pub_date_ratio:
        block(
            "FINANCIAL_PROVIDER_PUB_DATE_BASIS",
            (f"ratio={provider_ratio!r}, required={minimum_provider_pub_date_ratio}"),
        )
    missing_shards = sorted(set(SHARD_CONTRACTS) - set(provided_shards))
    if missing_shards:
        block(
            "IDEMPOTENT_EMPTY_RUN_EVIDENCE_MISSING",
            f"missing shard snapshots={missing_shards}",
        )
    for name, shard in shard_reports.items():
        if not shard["idempotent_empty_run_passed"]:
            block(
                f"IDEMPOTENT_EMPTY_RUN_{name.upper()}",
                "; ".join(shard["reasons"]),
            )
    if not pubdate_plan["ready_for_authorized_execution"]:
        block(
            "PUBDATE_PLAN_INCOMPLETE",
            "; ".join(pubdate_plan["blockers"]),
        )

    local_checks_passed = not blockers
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "read_only_plan_only",
        "network_called": False,
        "baostock_imported": False,
        "generated_at": datetime.now(UTC).isoformat(),
        "database": {
            "path": str(resolved_database),
            "open_mode": "ro",
            "query_only": query_only,
            "quick_check": quick_check,
        },
        "contract": {
            "as_of_date": as_of_date.isoformat(),
            "target_quarters": TARGET_QUARTERS,
            "target_period_range": [target_periods[0], target_periods[-1]],
            "required_metrics": list(REQUIRED_METRICS),
            "minimum_covered_symbols": minimum_covered_symbols,
            "effective_required_symbols": required_symbols,
            "minimum_provider_pub_date_ratio": minimum_provider_pub_date_ratio,
        },
        "universe": universe,
        "coverage": coverage,
        "checkpoint_closure": closure,
        "pit": pit,
        "shards": shard_reports,
        "pubdate_plan": pubdate_plan,
        "gate": {
            "local_checks_passed": local_checks_passed,
            "ready_for_authorized_pubdate_execution": local_checks_passed,
            "ready_for_s2_signoff": False,
            "blockers": blockers,
            "external_pending": [
                "authorized five-stock BaoStock pubDate execution",
                "architect review and signature",
            ],
        },
    }


def render_s2_financial_acceptance_markdown(report: Mapping[str, Any]) -> str:
    contract = report["contract"]
    universe = report["universe"]
    coverage = report["coverage"]
    closure = report["checkpoint_closure"]
    pit = report["pit"]
    gate = report["gate"]
    lines = [
        "# P3.3-S2 财务回填最终验收（只读预检）",
        "",
        f"- 模式：`{report['mode']}`",
        f"- as-of：`{contract['as_of_date']}`",
        f"- 目标季度：`{contract['target_period_range'][0]}` → "
        f"`{contract['target_period_range'][1]}`",
        f"- BaoStock 支持证券：{universe['provider_supported_symbols']:,}",
        f"- 已有财务证券：{coverage['symbols']:,}",
        "",
        "## 五指标覆盖",
        "",
        "| metric | rows | symbols | symbol coverage | non-null |",
        "|---|---:|---:|---:|---:|",
    ]
    for metric in REQUIRED_METRICS:
        payload = coverage["metrics"][metric]
        lines.append(
            f"| {metric} | {payload['rows']:,} | {payload['symbols']:,} | "
            f"{payload['symbol_coverage_ratio']!s} | {payload['non_null_ratio']!s} |"
        )
    lines.extend(
        [
            "",
            "## 40 季度正/负断点闭环",
            "",
            f"- 目标股票季度：{closure['target_symbol_periods']:,}",
            f"- 完整五指标季度：{closure['positive_complete_pairs']:,}",
            f"- 负向断点季度：{closure['negative_checkpoint_pairs']:,}",
            f"- 未解析季度：{closure['unresolved_pairs']:,}",
            f"- 部分季度：{closure['partial_pair_count']:,}",
            f"- 正负重叠：{closure['positive_negative_overlaps']:,}",
            f"- 财务重复键组：{closure['duplicate_financial_key_groups']:,}",
            "",
            "## PIT",
            "",
            f"- basis：`{json.dumps(pit['available_time_basis_counts'], ensure_ascii=False)}`",
            f"- provider_pub_date_end_of_day 比例：{pit['provider_pub_date_end_of_day_ratio']!s}",
            f"- PIT 异常行：{pit['anomaly_rows']:,}",
            "",
            "## 三分片幂等空跑",
            "",
        ]
    )
    for name in SHARD_CONTRACTS:
        shard = report["shards"].get(name)
        if shard is None:
            lines.append(f"- `{name}`：缺少快照证据")
        else:
            lines.append(
                f"- `{name}`：{'pass' if shard['idempotent_empty_run_passed'] else 'blocked'}"
            )
    pubdate = report["pubdate_plan"]
    lines.extend(
        [
            "",
            "## pubDate 五股计划",
            "",
            f"- mode：`{pubdate['mode']}`",
            f"- network_called：`{str(pubdate['network_called']).lower()}`",
            f"- planned queries：{pubdate['planned_provider_queries']}",
            f"- ready：`{str(pubdate['ready_for_authorized_execution']).lower()}`",
            "",
            "## Gate",
            "",
            f"- 本地可检查项：`{'pass' if gate['local_checks_passed'] else 'blocked'}`",
            "- S2 签字：`pending`（仍需获准的五股真实 pubDate 对拍与架构师签字）",
        ]
    )
    for blocker in gate["blockers"]:
        lines.append(f"- `{blocker['code']}`：{blocker['detail']}")
    return "\n".join(lines) + "\n"
