from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DailyBarEvidenceRow:
    symbol: str
    trade_date: str
    source: str


@dataclass(frozen=True)
class MockCleanupResult:
    status: str
    expected_count: int
    deleted_count: int
    before_count: int
    after_count: int
    backup_path: str | None
    backup_sha256: str | None
    database_quick_check: str
    trade_proposals_before: int
    trade_proposals_after: int
    broker_orders_before: int
    broker_orders_after: int
    rows: tuple[DailyBarEvidenceRow, ...]
    completed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "rows": [asdict(row) for row in self.rows],
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scalar_count(connection: sqlite3.Connection, table: str) -> int:
    if table not in {"trade_proposals", "broker_orders"}:
        raise ValueError(f"unsupported safety table: {table}")
    row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    if row is None:
        raise RuntimeError(f"failed to count {table}")
    return int(row[0])


def _quick_check(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA quick_check").fetchone()
    if row is None:
        raise RuntimeError("PRAGMA quick_check returned no result")
    return str(row[0])


def _load_mock_rows(connection: sqlite3.Connection) -> tuple[DailyBarEvidenceRow, ...]:
    rows = connection.execute(
        """
        SELECT symbol, trade_date, source
        FROM daily_bars
        WHERE source = 'mock'
        ORDER BY symbol, trade_date
        """
    ).fetchall()
    return tuple(
        DailyBarEvidenceRow(
            symbol=str(row[0]),
            trade_date=str(row[1]),
            source=str(row[2]),
        )
        for row in rows
    )


def _online_backup(source: sqlite3.Connection, backup_path: Path) -> tuple[Path, str]:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = backup_path.with_suffix(f"{backup_path.suffix}.partial")
    if temporary_path.exists():
        temporary_path.unlink()

    destination = sqlite3.connect(temporary_path)
    try:
        source.backup(destination, pages=2_048, sleep=0.05)
        if _quick_check(destination) != "ok":
            raise RuntimeError("backup PRAGMA quick_check failed")
    finally:
        destination.close()

    os.replace(temporary_path, backup_path)
    return backup_path, _sha256(backup_path)


def cleanup_mock_daily_bars(
    *,
    database_path: Path,
    backup_directory: Path,
    expected_count: int,
    apply: bool,
) -> MockCleanupResult:
    """Remove only bootstrap mock bars with a fail-closed count guard.

    A first successful mutation requires an online SQLite backup. Re-running the
    command against an already-clean database is an explicit idempotent no-op.
    """

    database_path = database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    if expected_count <= 0:
        raise ValueError("expected_count must be positive")

    connection = sqlite3.connect(database_path, timeout=15.0)
    connection.execute("PRAGMA busy_timeout=15000")
    try:
        before_rows = _load_mock_rows(connection)
        before_count = len(before_rows)
        proposals_before = _scalar_count(connection, "trade_proposals")
        orders_before = _scalar_count(connection, "broker_orders")
        quick_check_before = _quick_check(connection)
        if quick_check_before != "ok":
            raise RuntimeError(f"database PRAGMA quick_check failed: {quick_check_before}")

        if before_count == 0:
            completed_at = datetime.now(UTC).isoformat()
            return MockCleanupResult(
                status="already_clean",
                expected_count=expected_count,
                deleted_count=0,
                before_count=0,
                after_count=0,
                backup_path=None,
                backup_sha256=None,
                database_quick_check=quick_check_before,
                trade_proposals_before=proposals_before,
                trade_proposals_after=proposals_before,
                broker_orders_before=orders_before,
                broker_orders_after=orders_before,
                rows=(),
                completed_at=completed_at,
            )

        if before_count != expected_count:
            raise RuntimeError(
                "mock daily-bar count changed; refusing mutation: "
                f"expected={expected_count}, actual={before_count}"
            )

        if not apply:
            completed_at = datetime.now(UTC).isoformat()
            return MockCleanupResult(
                status="dry_run",
                expected_count=expected_count,
                deleted_count=0,
                before_count=before_count,
                after_count=before_count,
                backup_path=None,
                backup_sha256=None,
                database_quick_check=quick_check_before,
                trade_proposals_before=proposals_before,
                trade_proposals_after=proposals_before,
                broker_orders_before=orders_before,
                broker_orders_after=orders_before,
                rows=before_rows,
                completed_at=completed_at,
            )

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_directory.resolve() / f"alphapilot-before-mock-cleanup-{timestamp}.db"
        completed_backup, backup_sha256 = _online_backup(connection, backup_path)

        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute("DELETE FROM daily_bars WHERE source = 'mock'")
            deleted_count = int(cursor.rowcount)
            after_count = len(_load_mock_rows(connection))
            proposals_after = _scalar_count(connection, "trade_proposals")
            orders_after = _scalar_count(connection, "broker_orders")
            if deleted_count != expected_count or after_count != 0:
                raise RuntimeError(
                    "unexpected cleanup result: "
                    f"deleted={deleted_count}, remaining={after_count}"
                )
            if proposals_after != proposals_before or orders_after != orders_before:
                raise RuntimeError("trading safety-table count changed; rolling back")
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()

        quick_check_after = _quick_check(connection)
        if quick_check_after != "ok":
            raise RuntimeError(f"post-cleanup PRAGMA quick_check failed: {quick_check_after}")
        return MockCleanupResult(
            status="applied",
            expected_count=expected_count,
            deleted_count=deleted_count,
            before_count=before_count,
            after_count=after_count,
            backup_path=str(completed_backup),
            backup_sha256=backup_sha256,
            database_quick_check=quick_check_after,
            trade_proposals_before=proposals_before,
            trade_proposals_after=proposals_after,
            broker_orders_before=orders_before,
            broker_orders_after=orders_after,
            rows=before_rows,
            completed_at=datetime.now(UTC).isoformat(),
        )
    finally:
        connection.close()


def write_cleanup_evidence(result: MockCleanupResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
