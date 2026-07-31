from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from time import sleep
from typing import Any
from urllib.parse import quote

from sqlalchemy import create_engine

from alphapilot.db.models import Base
from alphapilot.jobs.financials import FINANCIAL_METRICS

_FINANCIAL_COLUMNS = frozenset(
    {
        "id",
        "symbol",
        "report_period",
        "metric",
        "value",
        "source",
        "available_time",
        "payload",
    }
)
_AVAILABLE_TIME_BASES = frozenset(
    {
        "provider_pub_date_end_of_day",
        "stat_date_plus_45_days",
    }
)
_SQLITE_LOCK_RETRY_DELAYS = (0.5, 1.5, 3.0)
_FINANCIAL_UNAVAILABLE_PROFILE_KEY = "financial_no_data_periods"
_REPORT_PERIOD_PATTERN = re.compile(r"^[0-9]{4}Q[1-4]$")


def _sqlite_uri(path: Path, *, read_only: bool = False) -> str:
    suffix = "?mode=ro" if read_only else ""
    return f"file:{quote(str(path.resolve()))}{suffix}"


def _read_only_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(_sqlite_uri(path, read_only=True), uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _table_columns(
    connection: sqlite3.Connection,
    table: str,
    *,
    schema: str = "main",
) -> set[str]:
    if schema not in {"main", "incoming", "source"}:
        raise ValueError(f"unsupported SQLite schema: {schema}")
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA {schema}.table_info({table})")
    }


def _require_financial_schema(connection: sqlite3.Connection, *, label: str) -> None:
    columns = _table_columns(connection, "financial_indicators")
    missing = _FINANCIAL_COLUMNS - columns
    if missing:
        raise ValueError(f"{label} is missing financial_indicators columns: {sorted(missing)}")


def _require_security_profile_schema(
    connection: sqlite3.Connection,
    *,
    label: str,
    schema: str = "main",
) -> None:
    columns = _table_columns(connection, "securities", schema=schema)
    missing = {"symbol", "profile"} - columns
    if missing:
        raise ValueError(f"{label} is missing securities columns: {sorted(missing)}")


def _profile_object(raw_profile: Any, *, label: str, symbol: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_profile) if isinstance(raw_profile, str) else raw_profile
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} has invalid Security.profile JSON for {symbol}") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} has non-object Security.profile for {symbol}")
    return dict(parsed)


def _financial_unavailable_periods(
    profile: dict[str, Any],
    *,
    label: str,
    symbol: str,
) -> set[str]:
    raw_periods = profile.get(_FINANCIAL_UNAVAILABLE_PROFILE_KEY)
    if raw_periods is None:
        return set()
    if not isinstance(raw_periods, list):
        raise ValueError(
            f"{label} has non-list {_FINANCIAL_UNAVAILABLE_PROFILE_KEY} for {symbol}"
        )
    periods: set[str] = set()
    for item in raw_periods:
        if not isinstance(item, str) or not _REPORT_PERIOD_PATTERN.fullmatch(item):
            raise ValueError(
                f"{label} has invalid {_FINANCIAL_UNAVAILABLE_PROFILE_KEY} "
                f"entry for {symbol}: {item!r}"
            )
        periods.add(item)
    return periods


def _profile_rows(connection: sqlite3.Connection, schema: str) -> list[tuple[str, Any]]:
    return [
        (str(symbol), raw_profile)
        for symbol, raw_profile in connection.execute(
            f"SELECT symbol, profile FROM {schema}.securities ORDER BY symbol"
        )
    ]


def _profile_digest(
    rows: list[tuple[str, Any]],
    *,
    label: str,
    exclude_financial_checkpoints: bool,
) -> str:
    digest = hashlib.sha256()
    for symbol, raw_profile in rows:
        profile = _profile_object(raw_profile, label=label, symbol=symbol)
        if exclude_financial_checkpoints:
            profile.pop(_FINANCIAL_UNAVAILABLE_PROFILE_KEY, None)
        encoded = json.dumps(
            [symbol, profile],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(encoded)
        digest.update(b"\n")
    return digest.hexdigest()


def _profile_checkpoint_stats(
    rows: list[tuple[str, Any]],
    *,
    label: str,
) -> tuple[int, int]:
    symbols = 0
    periods = 0
    for symbol, raw_profile in rows:
        profile = _profile_object(raw_profile, label=label, symbol=symbol)
        checkpoints = _financial_unavailable_periods(
            profile,
            label=label,
            symbol=symbol,
        )
        if checkpoints:
            symbols += 1
            periods += len(checkpoints)
    return symbols, periods


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def inspect_financial_snapshot(path: Path) -> dict[str, Any]:
    """Validate a remote financial snapshot before it may touch the local DB."""

    source_path = path.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"financial snapshot does not exist: {source_path}")

    with _read_only_connection(source_path) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise ValueError(f"financial snapshot integrity_check failed: {integrity}")
        _require_financial_schema(connection, label="financial snapshot")
        _require_security_profile_schema(connection, label="financial snapshot")

        invalid_symbol = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM financial_indicators
                WHERE length(symbol) != 6 OR symbol GLOB '*[^0-9]*'
                """
            ).fetchone()[0]
        )
        invalid_period = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM financial_indicators
                WHERE report_period NOT GLOB '[0-9][0-9][0-9][0-9]Q[1-4]'
                """
            ).fetchone()[0]
        )
        metric_placeholders = ",".join("?" for _ in FINANCIAL_METRICS)
        invalid_metric = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM financial_indicators
                WHERE metric NOT IN ({metric_placeholders})
                """,
                tuple(sorted(FINANCIAL_METRICS)),
            ).fetchone()[0]
        )
        invalid_source = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM financial_indicators
                WHERE source != 'baostock'
                """
            ).fetchone()[0]
        )
        invalid_available_time = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM financial_indicators
                WHERE available_time IS NULL OR trim(available_time) = ''
                """
            ).fetchone()[0]
        )
        violations = {
            "invalid_symbol": invalid_symbol,
            "invalid_report_period": invalid_period,
            "invalid_metric": invalid_metric,
            "invalid_source": invalid_source,
            "invalid_available_time": invalid_available_time,
        }
        if any(violations.values()):
            raise ValueError(f"financial snapshot row validation failed: {violations}")

        basis_counts: dict[str, int] = {}
        invalid_payloads = 0
        for (raw_payload,) in connection.execute(
            "SELECT payload FROM financial_indicators"
        ):
            try:
                payload = (
                    json.loads(raw_payload)
                    if isinstance(raw_payload, str)
                    else raw_payload
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                invalid_payloads += 1
                continue
            basis = payload.get("available_time_basis") if isinstance(payload, dict) else None
            if basis not in _AVAILABLE_TIME_BASES:
                invalid_payloads += 1
                continue
            basis_text = str(basis)
            basis_counts[basis_text] = basis_counts.get(basis_text, 0) + 1
        if invalid_payloads:
            raise ValueError(
                "financial snapshot contains invalid PIT payloads: "
                f"invalid_payloads={invalid_payloads}"
            )

        rows, symbols = connection.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT symbol)
            FROM financial_indicators
            """
        ).fetchone()
        metrics = {
            str(metric): int(count)
            for metric, count in connection.execute(
                """
                SELECT metric, COUNT(*)
                FROM financial_indicators
                GROUP BY metric
                ORDER BY metric
                """
            )
        }
        latest_job = connection.execute(
            """
            SELECT id, status, started_at, finished_at, stats, error
            FROM job_runs
            WHERE job_name = 'sync_financials'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        security_profile_rows = _profile_rows(connection, "main")
        checkpoint_symbols, checkpoint_periods = _profile_checkpoint_stats(
            security_profile_rows,
            label="financial snapshot",
        )

    return {
        "path": str(source_path),
        "sha256": _sha256(source_path),
        "rows": int(rows),
        "symbols": int(symbols),
        "metrics": metrics,
        "available_time_bases": basis_counts,
        "latest_sync_financials_job": list(latest_job) if latest_job else None,
        "security_profiles": len(security_profile_rows),
        "financial_checkpoint_symbols": checkpoint_symbols,
        "financial_checkpoint_periods": checkpoint_periods,
        "security_profile_sha256": _profile_digest(
            security_profile_rows,
            label="financial snapshot",
            exclude_financial_checkpoints=False,
        ),
        "security_profile_non_checkpoint_sha256": _profile_digest(
            security_profile_rows,
            label="financial snapshot",
            exclude_financial_checkpoints=True,
        ),
    }


def export_financial_snapshot(source_db: Path, output_db: Path) -> dict[str, Any]:
    """Create an atomic SQLite online-backup snapshot, safe during remote writes."""

    source_path = source_db.expanduser().resolve()
    output_path = output_db.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"source database does not exist: {source_path}")
    if source_path == output_path:
        raise ValueError("snapshot output must differ from the live source database")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
        delete=False,
    ) as temp_handle:
        temp_path = Path(temp_handle.name)
    try:
        with (
            _read_only_connection(source_path) as source,
            sqlite3.connect(temp_path) as destination,
        ):
            source.backup(destination, pages=2_048, sleep=0.05)
        inspection = inspect_financial_snapshot(temp_path)
        os.chmod(temp_path, 0o640)
        os.replace(temp_path, output_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    result = dict(inspection)
    result["path"] = str(output_path)
    result["sha256"] = _sha256(output_path)
    return result


def export_financial_staging_seed(source_db: Path, output_db: Path) -> dict[str, Any]:
    """Build a slim staging DB with only security checkpoints and financial rows."""

    source_path = source_db.expanduser().resolve()
    output_path = output_db.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"source database does not exist: {source_path}")
    if source_path == output_path:
        raise ValueError("staging seed output must differ from the source database")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
        delete=False,
    ) as temp_handle:
        temp_path = Path(temp_handle.name)
    temp_path.unlink()
    try:
        engine = create_engine(f"sqlite:///{temp_path}")
        Base.metadata.create_all(engine)
        engine.dispose()
        with sqlite3.connect(temp_path, isolation_level=None, uri=True) as destination:
            destination.execute("PRAGMA busy_timeout=15000")
            destination.execute(
                "ATTACH DATABASE ? AS source",
                (_sqlite_uri(source_path, read_only=True),),
            )
            destination.execute("BEGIN IMMEDIATE")
            for table in ("securities", "financial_indicators"):
                destination_columns = _table_columns(destination, table)
                source_columns = _table_columns(
                    destination,
                    table,
                    schema="source",
                )
                missing = destination_columns - source_columns
                if missing:
                    raise ValueError(
                        f"source database is missing {table} columns: {sorted(missing)}"
                    )
                ordered_columns = [
                    str(row[1])
                    for row in destination.execute(f"PRAGMA main.table_info({table})")
                ]
                quoted_columns = ", ".join(f'"{column}"' for column in ordered_columns)
                destination.execute(
                    f"""
                    INSERT INTO main.{table} ({quoted_columns})
                    SELECT {quoted_columns}
                    FROM source.{table}
                    """
                )
            destination.execute("COMMIT")
            destination.execute("DETACH DATABASE source")
            destination.execute("PRAGMA journal_mode=WAL")

        inspection = inspect_financial_snapshot(temp_path)
        with _read_only_connection(temp_path) as seed:
            seed_job_runs = _table_count(seed, "job_runs")
            seed_proposals = _table_count(seed, "trade_proposals")
            seed_orders = _table_count(seed, "broker_orders")
        if any((seed_job_runs, seed_proposals, seed_orders)):
            raise RuntimeError(
                "staging seed unexpectedly contains runtime/trading rows: "
                f"job_runs={seed_job_runs}, proposals={seed_proposals}, orders={seed_orders}"
            )
        os.chmod(temp_path, 0o640)
        os.replace(temp_path, output_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    result = dict(inspection)
    result.update(
        {
            "path": str(output_path),
            "sha256": _sha256(output_path),
            "job_runs": 0,
            "trade_proposals": 0,
            "broker_orders": 0,
        }
    )
    return result


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    if not _table_columns(connection, table):
        raise ValueError(f"target database is missing safety table: {table}")
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _merge_financial_unavailable_profiles(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    """Union cold-backfill checkpoints without copying any other remote profile field."""

    _require_security_profile_schema(connection, label="target database")
    _require_security_profile_schema(
        connection,
        label="financial snapshot",
        schema="incoming",
    )
    target_rows_before = _profile_rows(connection, "main")
    incoming_rows = _profile_rows(connection, "incoming")
    target_profiles = {
        symbol: _profile_object(
            raw_profile,
            label="target database",
            symbol=symbol,
        )
        for symbol, raw_profile in target_rows_before
    }
    full_hash_before = _profile_digest(
        target_rows_before,
        label="target database",
        exclude_financial_checkpoints=False,
    )
    non_checkpoint_hash_before = _profile_digest(
        target_rows_before,
        label="target database",
        exclude_financial_checkpoints=True,
    )

    incoming_checkpoint_symbols = 0
    incoming_checkpoint_periods = 0
    profiles_updated = 0
    checkpoint_periods_added = 0
    other_key_conflicts = 0
    other_keys_ignored = 0
    conflict_samples: list[dict[str, str]] = []
    missing_target_symbols: list[str] = []

    for symbol, raw_incoming_profile in incoming_rows:
        incoming_profile = _profile_object(
            raw_incoming_profile,
            label="financial snapshot",
            symbol=symbol,
        )
        incoming_periods = _financial_unavailable_periods(
            incoming_profile,
            label="financial snapshot",
            symbol=symbol,
        )
        if incoming_periods:
            incoming_checkpoint_symbols += 1
            incoming_checkpoint_periods += len(incoming_periods)
        target_profile = target_profiles.get(symbol)
        if target_profile is None:
            if incoming_periods:
                missing_target_symbols.append(symbol)
            continue

        incoming_other = {
            key: value
            for key, value in incoming_profile.items()
            if key != _FINANCIAL_UNAVAILABLE_PROFILE_KEY
        }
        other_keys_ignored += len(incoming_other)
        for key, incoming_value in incoming_other.items():
            if key in target_profile and target_profile[key] != incoming_value:
                other_key_conflicts += 1
                if len(conflict_samples) < 10:
                    conflict_samples.append({"symbol": symbol, "key": str(key)})

        existing_periods = _financial_unavailable_periods(
            target_profile,
            label="target database",
            symbol=symbol,
        )
        merged_periods = existing_periods | incoming_periods
        added = merged_periods - existing_periods
        if not added:
            continue
        merged_profile = dict(target_profile)
        merged_profile[_FINANCIAL_UNAVAILABLE_PROFILE_KEY] = sorted(merged_periods)
        connection.execute(
            "UPDATE main.securities SET profile = ? WHERE symbol = ?",
            (
                json.dumps(
                    merged_profile,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                symbol,
            ),
        )
        profiles_updated += 1
        checkpoint_periods_added += len(added)

    if missing_target_symbols:
        raise ValueError(
            "financial snapshot checkpoints reference securities absent from target; "
            f"count={len(missing_target_symbols)}, samples={missing_target_symbols[:10]}"
        )

    target_rows_after = _profile_rows(connection, "main")
    full_hash_after = _profile_digest(
        target_rows_after,
        label="target database",
        exclude_financial_checkpoints=False,
    )
    non_checkpoint_hash_after = _profile_digest(
        target_rows_after,
        label="target database",
        exclude_financial_checkpoints=True,
    )
    if non_checkpoint_hash_after != non_checkpoint_hash_before:
        raise RuntimeError(
            "financial checkpoint merge changed non-checkpoint Security.profile fields"
        )

    return {
        "incoming_security_profiles": len(incoming_rows),
        "incoming_checkpoint_symbols": incoming_checkpoint_symbols,
        "incoming_checkpoint_periods": incoming_checkpoint_periods,
        "profiles_updated": profiles_updated,
        "checkpoint_periods_added": checkpoint_periods_added,
        "other_profile_keys_ignored": other_keys_ignored,
        "other_profile_key_conflicts": other_key_conflicts,
        "other_profile_key_conflict_samples": conflict_samples,
        "missing_target_symbols": 0,
        "profile_sha256_before": full_hash_before,
        "profile_sha256_after": full_hash_after,
        "non_checkpoint_profile_sha256_before": non_checkpoint_hash_before,
        "non_checkpoint_profile_sha256_after": non_checkpoint_hash_after,
    }


def _import_once(source_path: Path, target_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(
        _sqlite_uri(target_path),
        uri=True,
        timeout=15.0,
        isolation_level=None,
    )
    attached = False
    try:
        connection.execute("PRAGMA busy_timeout=15000")
        _require_financial_schema(connection, label="target database")
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        proposals_before = _table_count(connection, "trade_proposals")
        orders_before = _table_count(connection, "broker_orders")
        local_rows_before = int(
            connection.execute("SELECT COUNT(*) FROM financial_indicators").fetchone()[0]
        )

        # Acquire the target write lock before attaching the immutable source.
        # Older SQLite builds otherwise try to include the read-only attachment
        # when BEGIN IMMEDIATE upgrades the transaction and reject the target
        # write with "attempt to write a readonly database".
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "ATTACH DATABASE ? AS incoming",
            (_sqlite_uri(source_path, read_only=True),),
        )
        attached = True
        incoming_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM incoming.financial_indicators"
            ).fetchone()[0]
        )
        new_rows = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM incoming.financial_indicators AS source
                LEFT JOIN main.financial_indicators AS target
                  ON target.symbol = source.symbol
                 AND target.report_period = source.report_period
                 AND target.metric = source.metric
                WHERE target.id IS NULL
                """
            ).fetchone()[0]
        )
        conflicting_rows = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM incoming.financial_indicators AS source
                JOIN main.financial_indicators AS target
                  ON target.symbol = source.symbol
                 AND target.report_period = source.report_period
                 AND target.metric = source.metric
                WHERE NOT (
                    target.value IS source.value
                    AND target.source IS source.source
                    AND target.available_time IS source.available_time
                    AND target.payload IS source.payload
                )
                """
            ).fetchone()[0]
        )
        if conflicting_rows:
            samples = connection.execute(
                """
                SELECT source.symbol, source.report_period, source.metric
                FROM incoming.financial_indicators AS source
                JOIN main.financial_indicators AS target
                  ON target.symbol = source.symbol
                 AND target.report_period = source.report_period
                 AND target.metric = source.metric
                WHERE NOT (
                    target.value IS source.value
                    AND target.source IS source.source
                    AND target.available_time IS source.available_time
                    AND target.payload IS source.payload
                )
                ORDER BY source.symbol, source.report_period, source.metric
                LIMIT 10
                """
            ).fetchall()
            raise ValueError(
                "financial snapshot conflicts with local PIT rows; refusing to overwrite: "
                f"count={conflicting_rows}, samples={samples}"
            )

        checkpoint_merge = _merge_financial_unavailable_profiles(connection)
        connection.execute(
            """
            INSERT INTO main.financial_indicators (
                symbol,
                report_period,
                metric,
                value,
                source,
                available_time,
                payload
            )
            SELECT
                symbol,
                report_period,
                metric,
                value,
                source,
                available_time,
                payload
            FROM incoming.financial_indicators
            WHERE 1
            ON CONFLICT(symbol, report_period, metric) DO NOTHING
            """
        )
        local_rows_after = int(
            connection.execute("SELECT COUNT(*) FROM financial_indicators").fetchone()[0]
        )
        local_symbols_after = int(
            connection.execute(
                "SELECT COUNT(DISTINCT symbol) FROM financial_indicators"
            ).fetchone()[0]
        )
        proposals_after = _table_count(connection, "trade_proposals")
        orders_after = _table_count(connection, "broker_orders")
        inserted = local_rows_after - local_rows_before
        if inserted != new_rows:
            raise RuntimeError(
                "financial import row-count mismatch after commit: "
                f"expected={new_rows}, inserted={inserted}"
            )
        if (proposals_before, orders_before) != (proposals_after, orders_after):
            raise RuntimeError(
                "financial import changed trading safety tables: "
                f"before={(proposals_before, orders_before)}, "
                f"after={(proposals_after, orders_after)}"
            )
        connection.execute("COMMIT")
        return {
            "target": str(target_path),
            "journal_mode": journal_mode,
            "busy_timeout_ms": int(
                connection.execute("PRAGMA busy_timeout").fetchone()[0]
            ),
            "incoming_rows": incoming_rows,
            "inserted": inserted,
            "already_present": incoming_rows - new_rows,
            "conflicting_rows": conflicting_rows,
            "local_rows_before": local_rows_before,
            "local_rows_after": local_rows_after,
            "local_symbols_after": local_symbols_after,
            "trade_proposals_before": proposals_before,
            "trade_proposals_after": proposals_after,
            "broker_orders_before": orders_before,
            "broker_orders_after": orders_after,
            "security_profile_merge": checkpoint_merge,
        }
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        if attached and not connection.in_transaction:
            connection.execute("DETACH DATABASE incoming")
        connection.close()


def import_financial_snapshot(source_db: Path, target_db: Path) -> dict[str, Any]:
    """Insert missing remote financial rows into the local DB without overwrites."""

    source_path = source_db.expanduser().resolve()
    target_path = target_db.expanduser().resolve()
    if source_path == target_path:
        raise ValueError("source snapshot and target database must differ")
    if not target_path.is_file():
        raise FileNotFoundError(f"target database does not exist: {target_path}")

    inspection = inspect_financial_snapshot(source_path)
    for attempt, delay in enumerate((*_SQLITE_LOCK_RETRY_DELAYS, None), start=1):
        try:
            imported = _import_once(source_path, target_path)
            return {"snapshot": inspection, "import": imported}
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower() or delay is None:
                raise
            sleep(delay)
            if attempt > len(_SQLITE_LOCK_RETRY_DELAYS):
                raise
    raise AssertionError("unreachable")
