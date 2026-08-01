from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from alphapilot.db import calendar_hygiene
from alphapilot.db.calendar_hygiene import (
    CalendarHygieneError,
    cleanup_s7_weekend_rows,
)


def _database(path: Path, *, extra_weekend: bool = False) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE daily_bars (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                amount REAL,
                source TEXT NOT NULL
            );
            CREATE TABLE adj_factors (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                adj_factor REAL NOT NULL,
                source TEXT NOT NULL
            );
            CREATE TABLE trade_proposals (id INTEGER PRIMARY KEY);
            CREATE TABLE broker_orders (id INTEGER PRIMARY KEY);
            CREATE TABLE job_runs (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL
            );
            INSERT INTO trade_proposals VALUES (1);
            INSERT INTO broker_orders VALUES (1);
            """
        )
        for index, symbol in enumerate(("920344", "920799"), start=1):
            connection.execute(
                """
                INSERT INTO daily_bars
                VALUES (?, ?, '2019-06-30', 1, 1, 1, 1, 1, 1, 'sina')
                """,
                (index, symbol),
            )
            connection.execute(
                """
                INSERT INTO adj_factors
                VALUES (?, ?, '2019-06-30', 1, 'sina-hfq')
                """,
                (index, symbol),
            )
        if extra_weekend:
            connection.execute(
                """
                INSERT INTO daily_bars
                VALUES (3, '920999', '2019-06-30', 1, 1, 1, 1, 1, 1, 'sina')
                """
            )
            connection.execute(
                """
                INSERT INTO adj_factors
                VALUES (3, '920999', '2019-06-30', 1, 'sina-hfq')
                """
            )
        connection.commit()
    finally:
        connection.close()


def _weekend_counts(path: Path) -> tuple[int, int]:
    with sqlite3.connect(path) as connection:
        daily = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM daily_bars
                WHERE CAST(strftime('%w', trade_date) AS INTEGER) IN (0, 6)
                """
            ).fetchone()[0]
        )
        adj = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM adj_factors
                WHERE CAST(strftime('%w', trade_date) AS INTEGER) IN (0, 6)
                """
            ).fetchone()[0]
        )
    return daily, adj


def _stub_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup = tmp_path / "backup.db"
    backup.write_bytes(b"verified-backup")
    monkeypatch.setattr(
        calendar_hygiene,
        "create_database_backup",
        lambda *_args, **_kwargs: {
            "backup_path": str(backup),
            "sha256": "a" * 64,
            "quick_check": "ok",
        },
    )


def test_calendar_cleanup_is_dry_run_then_guarded_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "calendar.db"
    evidence = tmp_path / "evidence.json"
    _database(database)
    _stub_backup(tmp_path, monkeypatch)

    dry_run = cleanup_s7_weekend_rows(
        database_path=database,
        backup_directory=tmp_path,
        evidence_path=evidence,
        apply=False,
    )
    assert dry_run["status"] == "dry_run"
    assert dry_run["daily_rows_deleted"] == 0

    applied = cleanup_s7_weekend_rows(
        database_path=database,
        backup_directory=tmp_path,
        evidence_path=evidence,
        apply=True,
    )
    assert applied["status"] == "applied"
    assert applied["daily_rows_deleted"] == 2
    assert applied["adj_rows_deleted"] == 2
    assert applied["safety_before"] == applied["safety_after"]
    assert applied["stage"] == "committed"
    assert applied["mutation_committed"] is True
    assert "prepared_at" in applied

    idempotent = cleanup_s7_weekend_rows(
        database_path=database,
        backup_directory=tmp_path,
        evidence_path=evidence,
        apply=True,
    )
    assert idempotent["status"] == "already_clean"
    assert idempotent["backup"] is None


def test_calendar_cleanup_rejects_scope_drift_without_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "calendar.db"
    _database(database, extra_weekend=True)
    monkeypatch.setattr(
        calendar_hygiene,
        "create_database_backup",
        lambda *_args, **_kwargs: pytest.fail("scope drift must fail before backup"),
    )

    with pytest.raises(RuntimeError, match="identity changed"):
        cleanup_s7_weekend_rows(
            database_path=database,
            backup_directory=tmp_path,
            evidence_path=tmp_path / "evidence.json",
            apply=True,
        )


@pytest.mark.parametrize(
    "alias_kind",
    [
        "database",
        "wal",
        "shm",
        "journal",
        "database_hardlink",
        "wal_hardlink",
        "shm_hardlink",
        "journal_hardlink",
    ],
)
def test_calendar_evidence_path_cannot_alias_sqlite_files(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    database = tmp_path / "calendar.db"
    _database(database)
    database_before = database.read_bytes()
    suffixes = {
        "database": "",
        "wal": "-wal",
        "shm": "-shm",
        "journal": "-journal",
    }
    protected_kind = alias_kind.removesuffix("_hardlink")
    protected = database.with_name(f"{database.name}{suffixes[protected_kind]}")
    if alias_kind.endswith("_hardlink"):
        if protected_kind != "database":
            protected.write_bytes(f"synthetic-{protected_kind}".encode())
        evidence = tmp_path / f"{protected_kind}-hardlink-evidence.json"
        os.link(protected, evidence)
    else:
        evidence = protected

    with pytest.raises(CalendarHygieneError, match="alias"):
        cleanup_s7_weekend_rows(
            database_path=database,
            backup_directory=tmp_path / "backups",
            evidence_path=evidence,
            apply=True,
        )

    assert database.read_bytes() == database_before
    assert not (tmp_path / "backups").exists()


def test_prepared_evidence_is_durable_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "calendar.db"
    evidence = tmp_path / "evidence.json"
    _database(database)
    _stub_backup(tmp_path, monkeypatch)

    def stop_at_mutation(**_kwargs: Any) -> tuple[int, int, dict[str, int], str]:
        prepared = json.loads(evidence.read_text(encoding="utf-8"))
        assert prepared["status"] == "prepared"
        assert prepared["stage"] == "prepared"
        assert prepared["mutation_committed"] is False
        assert prepared["backup"]["quick_check"] == "ok"
        raise RuntimeError("simulated crash before mutation")

    monkeypatch.setattr(calendar_hygiene, "_apply_cleanup", stop_at_mutation)

    with pytest.raises(RuntimeError, match="simulated crash before mutation"):
        cleanup_s7_weekend_rows(
            database_path=database,
            backup_directory=tmp_path,
            evidence_path=evidence,
            apply=True,
        )

    assert _weekend_counts(database) == (2, 2)
    blocked = json.loads(evidence.read_text(encoding="utf-8"))
    assert blocked["status"] == "blocked"
    assert blocked["stage"] == "prepared"
    assert blocked["mutation_committed"] is False


def test_prepared_evidence_write_failure_aborts_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "calendar.db"
    evidence = tmp_path / "evidence.json"
    _database(database)
    _stub_backup(tmp_path, monkeypatch)
    real_write = calendar_hygiene._atomic_write_json

    def fail_prepared_write(path: Path, document: Mapping[str, Any]) -> None:
        if document.get("status") == "prepared":
            raise OSError("simulated prepared evidence failure")
        real_write(path, document)

    monkeypatch.setattr(
        calendar_hygiene,
        "_atomic_write_json",
        fail_prepared_write,
    )

    with pytest.raises(OSError, match="simulated prepared evidence failure"):
        cleanup_s7_weekend_rows(
            database_path=database,
            backup_directory=tmp_path,
            evidence_path=evidence,
            apply=True,
        )

    assert _weekend_counts(database) == (2, 2)
    blocked = json.loads(evidence.read_text(encoding="utf-8"))
    assert blocked["status"] == "blocked"
    assert blocked["stage"] == "backup"
    assert blocked["mutation_committed"] is False


def test_post_commit_quick_check_failure_records_committed_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "calendar.db"
    evidence = tmp_path / "evidence.json"
    _database(database)
    _stub_backup(tmp_path, monkeypatch)
    real_quick_check = calendar_hygiene._quick_check
    calls = 0

    def fail_post_commit(connection: sqlite3.Connection) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            return "simulated corruption"
        return real_quick_check(connection)

    monkeypatch.setattr(calendar_hygiene, "_quick_check", fail_post_commit)

    with pytest.raises(CalendarHygieneError, match="post-cleanup quick_check failed"):
        cleanup_s7_weekend_rows(
            database_path=database,
            backup_directory=tmp_path,
            evidence_path=evidence,
            apply=True,
        )

    assert calls == 2
    assert _weekend_counts(database) == (0, 0)
    blocked = json.loads(evidence.read_text(encoding="utf-8"))
    assert blocked["status"] == "blocked_after_commit"
    assert blocked["stage"] == "committed_verification_failed"
    assert blocked["mutation_committed"] is True
    assert blocked["details"]["quick_check"] == "simulated corruption"


def test_final_evidence_failure_records_that_database_commit_succeeded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "calendar.db"
    evidence = tmp_path / "evidence.json"
    _database(database)
    _stub_backup(tmp_path, monkeypatch)
    real_write = calendar_hygiene._atomic_write_json
    writes = 0

    def fail_only_final_write(
        path: Path,
        document: Mapping[str, Any],
    ) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated final evidence failure")
        real_write(path, document)

    monkeypatch.setattr(
        calendar_hygiene,
        "_atomic_write_json",
        fail_only_final_write,
    )

    with pytest.raises(OSError, match="simulated final evidence failure"):
        cleanup_s7_weekend_rows(
            database_path=database,
            backup_directory=tmp_path,
            evidence_path=evidence,
            apply=True,
        )

    assert writes == 3
    assert _weekend_counts(database) == (0, 0)
    blocked = json.loads(evidence.read_text(encoding="utf-8"))
    assert blocked["status"] == "blocked_after_commit"
    assert blocked["stage"] == "committed"
    assert blocked["mutation_committed"] is True
    assert blocked["backup"]["quick_check"] == "ok"
