from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alphapilot.db.backup import create_database_backup

EXPECTED_S7_WEEKEND_KEYS: tuple[tuple[str, str], ...] = (
    ("920344", "2019-06-30"),
    ("920799", "2019-06-30"),
)


class CalendarHygieneError(RuntimeError):
    """A fail-closed calendar-hygiene validation or mutation gate failed."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.details = dict(details or {})


def _validate_evidence_path(database: Path, evidence_path: Path) -> Path:
    evidence = evidence_path.expanduser().resolve()
    protected = {
        database,
        database.with_name(f"{database.name}-wal"),
        database.with_name(f"{database.name}-shm"),
        database.with_name(f"{database.name}-journal"),
    }
    if evidence in protected:
        raise CalendarHygieneError(
            "evidence path aliases the live SQLite database or one of its sidecars"
        )
    if evidence.exists():
        for protected_path in protected:
            if protected_path.exists() and os.path.samefile(evidence, protected_path):
                raise CalendarHygieneError(
                    "evidence path is a hard-link alias of a protected SQLite file"
                )
    return evidence


def _rows(
    connection: sqlite3.Connection,
    table: str,
) -> list[dict[str, Any]]:
    if table not in {"daily_bars", "adj_factors"}:
        raise ValueError(f"unsupported calendar-hygiene table: {table}")
    return [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT *
            FROM {table}
            WHERE CAST(strftime('%w', trade_date) AS INTEGER) IN (0, 6)
            ORDER BY trade_date, symbol
            """
        ).fetchall()
    ]


def _count(connection: sqlite3.Connection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    if row is None:
        raise RuntimeError("calendar-hygiene count query returned no row")
    return int(row[0])


def _quick_check(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA quick_check").fetchone()
    if row is None:
        raise RuntimeError("PRAGMA quick_check returned no row")
    return str(row[0])


def _keys(rows: Sequence[dict[str, Any]]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted((str(row["symbol"]), str(row["trade_date"])) for row in rows)
    )


def _validate_scope(
    *,
    daily_rows: Sequence[dict[str, Any]],
    adj_rows: Sequence[dict[str, Any]],
    expected_keys: tuple[tuple[str, str], ...],
) -> None:
    if _keys(daily_rows) != expected_keys or _keys(adj_rows) != expected_keys:
        raise RuntimeError(
            "weekend-row identity changed; refusing mutation: "
            f"daily={_keys(daily_rows)!r}, adj={_keys(adj_rows)!r}"
        )
    if any(str(row["source"]) != "sina" for row in daily_rows):
        raise RuntimeError("weekend daily-bar scope contains a non-Sina row")
    if any(str(row["source"]) != "sina-hfq" for row in adj_rows):
        raise RuntimeError("weekend adjustment scope contains a non-Sina row")


def _safety_snapshot(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "trade_proposals": _count(
            connection,
            "SELECT COUNT(*) FROM trade_proposals",
        ),
        "broker_orders": _count(
            connection,
            "SELECT COUNT(*) FROM broker_orders",
        ),
        "running_job_runs": _count(
            connection,
            "SELECT COUNT(*) FROM job_runs WHERE status = 'running'",
        ),
        "daily_duplicate_groups": _count(
            connection,
            """
            SELECT COUNT(*) FROM (
              SELECT symbol, trade_date
              FROM daily_bars
              GROUP BY symbol, trade_date
              HAVING COUNT(*) > 1
            )
            """,
        ),
        "adj_duplicate_groups": _count(
            connection,
            """
            SELECT COUNT(*) FROM (
              SELECT symbol, trade_date
              FROM adj_factors
              GROUP BY symbol, trade_date
              HAVING COUNT(*) > 1
            )
            """,
        ),
        "daily_without_adj": _count(
            connection,
            """
            SELECT COUNT(*)
            FROM daily_bars AS daily
            LEFT JOIN adj_factors AS adj
              ON adj.symbol = daily.symbol
             AND adj.trade_date = daily.trade_date
            WHERE adj.id IS NULL
            """,
        ),
        "adj_without_daily": _count(
            connection,
            """
            SELECT COUNT(*)
            FROM adj_factors AS adj
            LEFT JOIN daily_bars AS daily
              ON daily.symbol = adj.symbol
             AND daily.trade_date = adj.trade_date
            WHERE daily.id IS NULL
            """,
        ),
    }


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


def _apply_cleanup(
    *,
    database: Path,
    daily_before: Sequence[dict[str, Any]],
    adj_before: Sequence[dict[str, Any]],
    expected_keys: tuple[tuple[str, str], ...],
    safety_before: dict[str, int],
) -> tuple[int, int, dict[str, int], str]:
    connection = sqlite3.connect(database, timeout=15.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            daily_locked = _rows(connection, "daily_bars")
            adj_locked = _rows(connection, "adj_factors")
            if daily_locked != daily_before or adj_locked != adj_before:
                raise CalendarHygieneError(
                    "weekend rows changed after backup; rolling back"
                )
            adj_deleted = 0
            daily_deleted = 0
            for symbol, trade_date in expected_keys:
                adj_deleted += int(
                    connection.execute(
                        """
                        DELETE FROM adj_factors
                        WHERE symbol = ? AND trade_date = ? AND source = 'sina-hfq'
                        """,
                        (symbol, trade_date),
                    ).rowcount
                )
                daily_deleted += int(
                    connection.execute(
                        """
                        DELETE FROM daily_bars
                        WHERE symbol = ? AND trade_date = ? AND source = 'sina'
                        """,
                        (symbol, trade_date),
                    ).rowcount
                )
            if adj_deleted != len(expected_keys) or daily_deleted != len(expected_keys):
                raise CalendarHygieneError(
                    "unexpected weekend cleanup row count; rolling back: "
                    f"daily={daily_deleted}, adj={adj_deleted}"
                )
            if _rows(connection, "daily_bars") or _rows(connection, "adj_factors"):
                raise CalendarHygieneError(
                    "weekend rows remain after cleanup; rolling back"
                )
            safety_after = _safety_snapshot(connection)
            if safety_after != safety_before:
                raise CalendarHygieneError(
                    "safety/key invariant changed during calendar cleanup; rolling back: "
                    f"before={safety_before!r}, after={safety_after!r}"
                )
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()

        try:
            quick_after = _quick_check(connection)
        except Exception as exc:
            raise CalendarHygieneError(
                "post-commit PRAGMA quick_check could not be completed",
                details={"mutation_committed": True},
            ) from exc
        if quick_after != "ok":
            raise CalendarHygieneError(
                f"post-cleanup quick_check failed: {quick_after}",
                details={
                    "quick_check": quick_after,
                    "mutation_committed": True,
                },
            )
        return daily_deleted, adj_deleted, safety_after, quick_after
    finally:
        connection.close()


def cleanup_s7_weekend_rows(
    *,
    database_path: Path,
    backup_directory: Path,
    evidence_path: Path,
    apply: bool,
    expected_keys: tuple[tuple[str, str], ...] = EXPECTED_S7_WEEKEND_KEYS,
) -> dict[str, Any]:
    """Remove only the two proven weekend rows after a verified online backup."""

    started_at = datetime.now(UTC)
    database = database_path.expanduser().resolve()
    evidence_destination = _validate_evidence_path(database, evidence_path)
    base_evidence: dict[str, Any] = {
        "operation": "p3_m3_s7_calendar_hygiene",
        "mode": "apply" if apply else "dry_run",
        "database": str(database),
        "started_at": started_at.isoformat(),
    }
    evidence: dict[str, Any] | None = None
    stage = "initialized"
    mutation_committed = False
    try:
        stage = "preflight"
        if not database.is_file():
            raise FileNotFoundError(database)
        if not expected_keys:
            raise ValueError("expected_keys must not be empty")
        if tuple(sorted(expected_keys)) != expected_keys:
            raise ValueError("expected_keys must be sorted and unique")

        connection = sqlite3.connect(database, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            daily_before = _rows(connection, "daily_bars")
            adj_before = _rows(connection, "adj_factors")
            quick_before = _quick_check(connection)
            safety_before = _safety_snapshot(connection)
            evidence = {
                **base_evidence,
                "expected_keys": [list(key) for key in expected_keys],
                "daily_rows_before": daily_before,
                "adj_rows_before": adj_before,
                "daily_rows_deleted": 0,
                "adj_rows_deleted": 0,
                "backup": None,
                "quick_check_before": quick_before,
                "safety_before": safety_before,
                "stage": "planned",
                "mutation_committed": False,
            }
            if quick_before != "ok":
                raise CalendarHygieneError(
                    f"pre-cleanup quick_check failed: {quick_before}"
                )
            if safety_before["running_job_runs"]:
                raise CalendarHygieneError(
                    "running JobRun rows exist; refusing calendar cleanup"
                )

            if not daily_before and not adj_before:
                evidence.update(
                    {
                        "status": "already_clean",
                        "quick_check_after": quick_before,
                        "safety_after": safety_before,
                        "completed_at": datetime.now(UTC).isoformat(),
                        "stage": "complete",
                    }
                )
                _atomic_write_json(evidence_destination, evidence)
                return evidence

            _validate_scope(
                daily_rows=daily_before,
                adj_rows=adj_before,
                expected_keys=expected_keys,
            )
            if not apply:
                evidence.update(
                    {
                        "status": "dry_run",
                        "quick_check_after": quick_before,
                        "safety_after": safety_before,
                        "completed_at": datetime.now(UTC).isoformat(),
                        "stage": "dry_run",
                    }
                )
                _atomic_write_json(evidence_destination, evidence)
                return evidence
        finally:
            connection.close()

        stage = "backup"
        backup = create_database_backup(
            database,
            backup_directory,
            retain=100_000,
        )
        evidence["backup"] = backup
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
            (
                daily_deleted,
                adj_deleted,
                safety_after,
                quick_after,
            ) = _apply_cleanup(
                database=database,
                daily_before=daily_before,
                adj_before=adj_before,
                expected_keys=expected_keys,
                safety_before=safety_before,
            )
        except CalendarHygieneError as exc:
            if exc.details.get("mutation_committed") is True:
                mutation_committed = True
                stage = "committed_verification_failed"
            raise

        mutation_committed = True
        stage = "committed"
        evidence.update(
            {
                "status": "applied",
                "daily_rows_deleted": daily_deleted,
                "adj_rows_deleted": adj_deleted,
                "quick_check_after": quick_after,
                "safety_after": safety_after,
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
            isinstance(exc, CalendarHygieneError)
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
            "details": exc.details if isinstance(exc, CalendarHygieneError) else {},
            "completed_at": datetime.now(UTC).isoformat(),
        }
        _atomic_write_json(evidence_destination, blocked)
        raise
