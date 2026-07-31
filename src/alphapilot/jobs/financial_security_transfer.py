from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

_SYMBOL_PATTERN = re.compile(r"^[0-9]{6}$")
_MAX_EXPLICIT_SYMBOLS = 100
_SAFETY_TABLES = (
    "financial_indicators",
    "job_runs",
    "trade_proposals",
    "broker_orders",
)


def _sqlite_uri(path: Path, *, mode: str) -> str:
    if mode not in {"ro", "rw"}:
        raise ValueError(f"unsupported SQLite mode: {mode}")
    return f"file:{quote(str(path.resolve()))}?mode={mode}"


def _quoted_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    rows = connection.execute(
        f"PRAGMA table_info({_quoted_identifier(table)})"
    ).fetchall()
    if not rows:
        raise ValueError(f"SQLite database is missing required table: {table}")
    return [str(row[1]) for row in rows]


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(
            connection.execute(
                f"SELECT COUNT(*) FROM {_quoted_identifier(table)}"
            ).fetchone()[0]
        )
        for table in _SAFETY_TABLES
        if _table_columns(connection, table)
    }


def _normalized_allowlist(symbols: Sequence[str]) -> tuple[str, ...]:
    requested = tuple(symbols)
    if not requested:
        raise ValueError("at least one explicit --symbol is required")
    if len(requested) > _MAX_EXPLICIT_SYMBOLS:
        raise ValueError(
            "explicit security allowlist is too large: "
            f"count={len(requested)}, maximum={_MAX_EXPLICIT_SYMBOLS}"
        )
    invalid = [symbol for symbol in requested if not _SYMBOL_PATTERN.fullmatch(symbol)]
    if invalid:
        raise ValueError(f"invalid six-digit security symbols: {invalid}")
    if len(set(requested)) != len(requested):
        raise ValueError("explicit security allowlist contains duplicate symbols")
    return tuple(sorted(requested))


def _rows_by_symbol(
    connection: sqlite3.Connection,
    *,
    columns: Sequence[str],
    symbols: Sequence[str],
) -> dict[str, tuple[Any, ...]]:
    quoted_columns = ", ".join(_quoted_identifier(column) for column in columns)
    placeholders = ", ".join("?" for _ in symbols)
    rows = connection.execute(
        f"""
        SELECT {quoted_columns}
        FROM securities
        WHERE symbol IN ({placeholders})
        ORDER BY symbol
        """,
        tuple(symbols),
    ).fetchall()
    symbol_index = columns.index("symbol")
    return {str(row[symbol_index]): tuple(row) for row in rows}


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes_hex__": value.hex()}
    raise TypeError(f"cannot serialize SQLite value of type {type(value).__name__}")


def _rows_sha256(
    *,
    columns: Sequence[str],
    rows: dict[str, tuple[Any, ...]],
) -> str:
    payload = {
        "columns": list(columns),
        "rows": [list(rows[symbol]) for symbol in sorted(rows)],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def export_staging_security_metadata(
    source_db: Path,
    output_db: Path,
    *,
    symbols: Sequence[str],
) -> dict[str, Any]:
    """Export allowlisted Security rows into a no-clobber metadata-only SQLite DB."""

    allowlist = _normalized_allowlist(symbols)
    source_path = source_db.expanduser().resolve()
    output_path = output_db.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"source SQLite database does not exist: {source_path}")
    if source_path == output_path:
        raise ValueError("source and output SQLite databases must differ")
    if output_path.exists():
        raise FileExistsError(f"metadata output already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(
        _sqlite_uri(source_path, mode="ro"),
        uri=True,
        timeout=15.0,
        isolation_level=None,
    )
    temp_path: Path | None = None
    try:
        source.execute("PRAGMA query_only=ON")
        source_query_only = bool(int(source.execute("PRAGMA query_only").fetchone()[0]))
        if not source_query_only:
            raise RuntimeError("source SQLite connection did not enter query_only mode")
        source_columns = _table_columns(source, "securities")
        source_rows = _rows_by_symbol(
            source,
            columns=source_columns,
            symbols=allowlist,
        )
        missing_from_source = sorted(set(allowlist) - set(source_rows))
        if missing_from_source:
            raise ValueError(
                "explicitly allowed securities are absent from source: "
                f"{missing_from_source}"
            )
        table_schema_row = source.execute(
            """
            SELECT sql
            FROM sqlite_schema
            WHERE type = 'table' AND name = 'securities'
            """
        ).fetchone()
        if table_schema_row is None or not isinstance(table_schema_row[0], str):
            raise ValueError("source securities table has no reusable CREATE TABLE schema")
        table_schema_sql = str(table_schema_row[0])
        index_schema_sql = [
            str(row[0])
            for row in source.execute(
                """
                SELECT sql
                FROM sqlite_schema
                WHERE type = 'index'
                  AND tbl_name = 'securities'
                  AND sql IS NOT NULL
                ORDER BY name
                """
            )
        ]
        selected_rows_sha256 = _rows_sha256(
            columns=source_columns,
            rows=source_rows,
        )

        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=output_path.parent,
            delete=False,
        ) as temp_handle:
            temp_path = Path(temp_handle.name)
        temp_path.unlink()

        destination = sqlite3.connect(temp_path, isolation_level=None)
        try:
            destination.execute("PRAGMA busy_timeout=15000")
            destination.execute("BEGIN IMMEDIATE")
            destination.execute(table_schema_sql)
            for statement in index_schema_sql:
                destination.execute(statement)
            quoted_columns = ", ".join(
                _quoted_identifier(column) for column in source_columns
            )
            value_placeholders = ", ".join("?" for _ in source_columns)
            destination.executemany(
                f"""
                INSERT INTO securities ({quoted_columns})
                VALUES ({value_placeholders})
                """,
                [source_rows[symbol] for symbol in allowlist],
            )
            destination.execute("COMMIT")

            output_tables = [
                str(row[0])
                for row in destination.execute(
                    """
                    SELECT name
                    FROM sqlite_schema
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                )
            ]
            if output_tables != ["securities"]:
                raise RuntimeError(
                    f"metadata output contains unexpected tables: {output_tables}"
                )
            output_columns = _table_columns(destination, "securities")
            if output_columns != source_columns:
                raise RuntimeError(
                    "metadata output securities schema differs from source: "
                    f"source={source_columns}, output={output_columns}"
                )
            output_rows = _rows_by_symbol(
                destination,
                columns=output_columns,
                symbols=allowlist,
            )
            if output_rows != source_rows:
                raise RuntimeError(
                    "metadata output rows do not match all source Security fields"
                )
            output_row_count = int(
                destination.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
            )
            if output_row_count != len(allowlist):
                raise RuntimeError(
                    "metadata output contains non-allowlisted rows: "
                    f"rows={output_row_count}, allowlist={len(allowlist)}"
                )
            quick_check = str(destination.execute("PRAGMA quick_check").fetchone()[0])
            if quick_check != "ok":
                raise RuntimeError(
                    f"metadata output SQLite quick_check failed: {quick_check}"
                )
        except Exception:
            if destination.in_transaction:
                destination.execute("ROLLBACK")
            raise
        finally:
            destination.close()

        os.chmod(temp_path, 0o640)
        try:
            os.link(temp_path, output_path)
        except FileExistsError as exc:
            raise FileExistsError(
                f"metadata output appeared during export; refusing overwrite: {output_path}"
            ) from exc
        temp_path.unlink()
        temp_path = None
    finally:
        source.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    return {
        "schema_version": "alphapilot.p3.s2.security-metadata-export.v1",
        "allowlist": list(allowlist),
        "source": {
            "path": str(source_path),
            "mode": "ro",
            "query_only": source_query_only,
            "selected_rows": len(source_rows),
            "selected_rows_sha256": selected_rows_sha256,
        },
        "output": {
            "path": str(output_path),
            "mode": "new_no_clobber",
            "tables": ["securities"],
            "security_columns": len(source_columns),
            "security_rows": output_row_count,
            "selected_rows_sha256": selected_rows_sha256,
            "quick_check": quick_check,
            "sha256": _file_sha256(output_path),
        },
    }


def import_missing_staging_securities(
    source_db: Path,
    target_db: Path,
    *,
    symbols: Sequence[str],
) -> dict[str, Any]:
    """Insert explicitly allowed missing Security rows into an S2 staging SQLite DB.

    The source is opened read-only. The target is opened read/write without create
    permission and changed in one immediate transaction. Existing Security rows are
    never updated, and the four S2/runtime safety-table counts must remain unchanged.
    """

    allowlist = _normalized_allowlist(symbols)
    source_path = source_db.expanduser().resolve()
    target_path = target_db.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"source SQLite database does not exist: {source_path}")
    if not target_path.is_file():
        raise FileNotFoundError(f"target SQLite database does not exist: {target_path}")
    if source_path == target_path:
        raise ValueError("source and target SQLite databases must differ")

    source = sqlite3.connect(
        _sqlite_uri(source_path, mode="ro"),
        uri=True,
        timeout=15.0,
        isolation_level=None,
    )
    target = sqlite3.connect(
        _sqlite_uri(target_path, mode="rw"),
        uri=True,
        timeout=15.0,
        isolation_level=None,
    )
    try:
        source.execute("PRAGMA query_only=ON")
        source_query_only = bool(int(source.execute("PRAGMA query_only").fetchone()[0]))
        if not source_query_only:
            raise RuntimeError("source SQLite connection did not enter query_only mode")
        source_columns = _table_columns(source, "securities")
        source_rows = _rows_by_symbol(
            source,
            columns=source_columns,
            symbols=allowlist,
        )
        missing_from_source = sorted(set(allowlist) - set(source_rows))
        if missing_from_source:
            raise ValueError(
                "explicitly allowed securities are absent from source: "
                f"{missing_from_source}"
            )

        target.execute("PRAGMA busy_timeout=15000")
        target.execute("PRAGMA foreign_keys=ON")
        busy_timeout_ms = int(target.execute("PRAGMA busy_timeout").fetchone()[0])
        journal_mode = str(target.execute("PRAGMA journal_mode").fetchone()[0])
        target_columns = _table_columns(target, "securities")
        source_column_set = set(source_columns)
        target_column_set = set(target_columns)
        if source_column_set != target_column_set:
            raise ValueError(
                "source/target securities fields differ; refusing a partial-field copy: "
                f"missing_in_target={sorted(source_column_set - target_column_set)}, "
                f"extra_in_target={sorted(target_column_set - source_column_set)}"
            )
        source_indexes = {
            column: index for index, column in enumerate(source_columns)
        }
        source_rows_in_target_order = {
            symbol: tuple(
                row[source_indexes[column]] for column in target_columns
            )
            for symbol, row in source_rows.items()
        }
        source_rows_sha256 = _rows_sha256(
            columns=target_columns,
            rows=source_rows_in_target_order,
        )

        target.execute("BEGIN IMMEDIATE")
        safety_before = _table_counts(target)
        securities_before = int(
            target.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
        )
        target_rows_before = _rows_by_symbol(
            target,
            columns=target_columns,
            symbols=allowlist,
        )
        already_present = tuple(sorted(target_rows_before))
        to_insert = tuple(sorted(set(allowlist) - set(target_rows_before)))

        quoted_columns = ", ".join(
            _quoted_identifier(column) for column in target_columns
        )
        value_placeholders = ", ".join("?" for _ in target_columns)
        if to_insert:
            target.executemany(
                f"""
                INSERT INTO securities ({quoted_columns})
                VALUES ({value_placeholders})
                """,
                [source_rows_in_target_order[symbol] for symbol in to_insert],
            )

        target_rows_after = _rows_by_symbol(
            target,
            columns=target_columns,
            symbols=allowlist,
        )
        securities_after = int(
            target.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
        )
        safety_after = _table_counts(target)

        if set(target_rows_after) != set(allowlist):
            raise RuntimeError(
                "target is missing allowed securities after insert: "
                f"{sorted(set(allowlist) - set(target_rows_after))}"
            )
        changed_existing = [
            symbol
            for symbol in already_present
            if target_rows_after[symbol] != target_rows_before[symbol]
        ]
        if changed_existing:
            raise RuntimeError(
                f"existing target securities changed unexpectedly: {changed_existing}"
            )
        incorrect_insertions = [
            symbol
            for symbol in to_insert
            if target_rows_after[symbol] != source_rows_in_target_order[symbol]
        ]
        if incorrect_insertions:
            raise RuntimeError(
                "inserted target securities do not match all source fields: "
                f"{incorrect_insertions}"
            )
        if securities_after - securities_before != len(to_insert):
            raise RuntimeError(
                "target securities count changed unexpectedly: "
                f"before={securities_before}, after={securities_after}, "
                f"expected_inserted={len(to_insert)}"
            )
        if safety_after != safety_before:
            raise RuntimeError(
                "security metadata import changed protected table counts: "
                f"before={safety_before}, after={safety_after}"
            )

        existing_row_mismatches = [
            symbol
            for symbol in already_present
            if target_rows_before[symbol] != source_rows_in_target_order[symbol]
        ]
        target_rows_sha256 = _rows_sha256(
            columns=target_columns,
            rows=target_rows_after,
        )
        target.execute("COMMIT")
    except Exception:
        if target.in_transaction:
            target.execute("ROLLBACK")
        raise
    finally:
        source.close()
        target.close()

    return {
        "schema_version": "alphapilot.p3.s2.security-metadata-import.v1",
        "allowlist": list(allowlist),
        "source": {
            "path": str(source_path),
            "mode": "ro",
            "query_only": source_query_only,
            "selected_rows": len(source_rows),
            "selected_rows_sha256": source_rows_sha256,
        },
        "target": {
            "path": str(target_path),
            "mode": "rw_existing_only",
            "journal_mode": journal_mode,
            "busy_timeout_ms": busy_timeout_ms,
            "security_columns_copied": len(target_columns),
            "securities_before": securities_before,
            "securities_after": securities_after,
            "selected_rows_sha256": target_rows_sha256,
        },
        "inserted_symbols": list(to_insert),
        "already_present_symbols": list(already_present),
        "existing_row_mismatches": existing_row_mismatches,
        "protected_counts_before": safety_before,
        "protected_counts_after": safety_after,
        "protected_counts_unchanged": True,
    }


__all__ = [
    "export_staging_security_metadata",
    "import_missing_staging_securities",
]
