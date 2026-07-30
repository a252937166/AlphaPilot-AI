from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alphapilot.db import backup_schedule
from alphapilot.db.backup import DatabaseBackupBusyError


def test_daily_backup_waits_for_window_and_runs_once_per_shanghai_date(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    def create(*_args: object, **kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "backup_path": "/backup.db",
            "manifest_path": "/backup.manifest.json",
        }

    monkeypatch.setattr(backup_schedule, "create_database_backup", create)
    runtime = tmp_path / "runtime"
    before = backup_schedule.run_daily_database_backup(
        tmp_path / "live.db",
        tmp_path / "backups",
        runtime,
        now=datetime(2026, 7, 30, 13, 59, tzinfo=UTC),
    )
    first = backup_schedule.run_daily_database_backup(
        tmp_path / "live.db",
        tmp_path / "backups",
        runtime,
        now=datetime(2026, 7, 30, 14, 0, tzinfo=UTC),
    )
    second = backup_schedule.run_daily_database_backup(
        tmp_path / "live.db",
        tmp_path / "backups",
        runtime,
        now=datetime(2026, 7, 30, 15, 0, tzinfo=UTC),
    )

    assert before["status"] == "not_due"
    assert first["status"] == "backed_up"
    assert second["status"] == "already_complete"
    assert len(calls) == 1
    assert (runtime / "last-success-shanghai-date").read_text().strip() == "2026-07-30"
    assert (runtime / "last-success-shanghai-date").stat().st_mode & 0o777 == 0o600


def test_failed_daily_backup_does_not_advance_success_stamp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("injected backup failure")

    monkeypatch.setattr(backup_schedule, "create_database_backup", fail)
    runtime = tmp_path / "runtime"

    with pytest.raises(RuntimeError, match="injected backup failure"):
        backup_schedule.run_daily_database_backup(
            tmp_path / "live.db",
            tmp_path / "backups",
            runtime,
            now=datetime(2026, 7, 30, 14, 0, tzinfo=UTC),
        )

    assert not (runtime / "last-success-shanghai-date").exists()


def test_daily_backup_reconciles_published_backup_after_stamp_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "live.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE payload (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO payload (id) VALUES (1)")
        connection.commit()

    runtime = tmp_path / "runtime"
    backup_directory = tmp_path / "backups"
    original_write_stamp = backup_schedule._write_success_stamp

    def fail_stamp(_path: Path, _date: str) -> None:
        raise OSError("injected stamp failure")

    monkeypatch.setattr(backup_schedule, "_write_success_stamp", fail_stamp)
    with pytest.raises(OSError, match="injected stamp failure"):
        backup_schedule.run_daily_database_backup(
            source,
            backup_directory,
            runtime,
            minimum_free_bytes=0,
            now=datetime(2026, 7, 30, 14, 0, tzinfo=UTC),
        )

    assert len(list(backup_directory.glob("alphapilot-full-*.db"))) == 1
    monkeypatch.setattr(
        backup_schedule,
        "_write_success_stamp",
        original_write_stamp,
    )
    recovered = backup_schedule.run_daily_database_backup(
        source,
        backup_directory,
        runtime,
        minimum_free_bytes=0,
        now=datetime(2026, 7, 30, 14, 15, tzinfo=UTC),
    )

    assert recovered["status"] == "reconciled"
    assert recovered["verified"] is True
    assert len(list(backup_directory.glob("alphapilot-full-*.db"))) == 1
    assert (runtime / "last-success-shanghai-date").read_text().strip() == "2026-07-30"


def test_daily_backup_gate_allows_only_one_concurrent_creator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def create(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=5)
        return {
            "backup_path": "/backup.db",
            "manifest_path": "/backup.manifest.json",
        }

    monkeypatch.setattr(backup_schedule, "create_database_backup", create)
    runtime = tmp_path / "runtime"
    arguments = (
        tmp_path / "live.db",
        tmp_path / "backups",
        runtime,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            backup_schedule.run_daily_database_backup,
            *arguments,
            now=datetime(2026, 7, 30, 14, 0, tzinfo=UTC),
        )
        assert entered.wait(timeout=5)
        try:
            with pytest.raises(DatabaseBackupBusyError, match="already running"):
                backup_schedule.run_daily_database_backup(
                    *arguments,
                    now=datetime(2026, 7, 30, 14, 1, tzinfo=UTC),
                )
        finally:
            release.set()
        assert first.result(timeout=5)["status"] == "backed_up"

    assert calls == 1


def test_daily_backup_rejects_naive_timestamp(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        backup_schedule.run_daily_database_backup(
            tmp_path / "live.db",
            tmp_path / "backups",
            tmp_path / "runtime",
            now=datetime(2026, 7, 30, 22, 0),
        )
