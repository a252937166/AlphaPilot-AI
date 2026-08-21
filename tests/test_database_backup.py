from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alphapilot.db import backup as backup_module
from alphapilot.db.backup import (
    DatabaseBackupBusyError,
    DatabaseBackupSpaceError,
    DatabaseBackupVerificationError,
    create_database_backup,
    manifest_path_for,
    verify_database_backup,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _create_wal_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.executescript(
        """
        CREATE TABLE payload (
            id INTEGER PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE trade_proposals (id INTEGER PRIMARY KEY);
        CREATE TABLE broker_orders (id INTEGER PRIMARY KEY);
        CREATE TABLE runtime_flags (
            key TEXT PRIMARY KEY,
            value BOOLEAN NOT NULL
        );
        CREATE TABLE job_runs (id INTEGER PRIMARY KEY, status TEXT NOT NULL);
        INSERT INTO trade_proposals (id) VALUES (1);
        INSERT INTO broker_orders (id) VALUES (1);
        INSERT INTO runtime_flags (key, value) VALUES ('trading_halted', 1);
        INSERT INTO job_runs (id, status) VALUES (1, 'ok');
        """
    )
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("INSERT INTO payload (id, value) VALUES (1, 'committed-in-wal')")
    connection.commit()
    wal_path = path.with_name(f"{path.name}-wal")
    assert wal_path.is_file()
    assert wal_path.stat().st_size > 0
    return connection


def _backup(
    source: Path,
    directory: Path,
    *,
    now: datetime,
    retain: int = 3,
) -> dict[str, object]:
    return create_database_backup(
        source,
        directory,
        now=now,
        retain=retain,
        minimum_free_bytes=0,
        pages=1,
        sleep=0.0,
    )


def test_online_backup_includes_uncheckpointed_wal_and_records_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "live.db"
    writer = _create_wal_database(source)
    backup_directory = tmp_path / "backups"
    try:
        result = _backup(
            source,
            backup_directory,
            now=datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
        )
    finally:
        writer.close()

    backup_path = Path(str(result["backup_path"]))
    manifest_path = Path(str(result["manifest_path"]))
    assert stat.S_IMODE(backup_directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert not backup_path.with_name(f"{backup_path.name}-wal").exists()
    assert not backup_path.with_name(f"{backup_path.name}-shm").exists()

    with sqlite3.connect(
        f"file:{backup_path}?mode=ro",
        uri=True,
    ) as connection:
        assert connection.execute("SELECT value FROM payload").fetchone() == (
            "committed-in-wal",
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source"]["query_only"] is True
    assert manifest["source"]["journal_mode"] == "wal"
    assert manifest["managed_by"] == backup_module.BACKUP_MANAGED_BY
    assert manifest["backup"]["quick_check"] == "ok"
    assert manifest["backup"]["file_identity"] == backup_module._file_identity(
        backup_path
    )
    assert manifest["backup"]["critical_tables"]["trade_proposals"]["rows"] == 1
    assert manifest["backup"]["critical_tables"]["broker_orders"]["rows"] == 1
    assert manifest["backup"]["critical_tables"]["runtime_flags"]["rows"] == 1
    assert manifest["backup"]["critical_tables"]["runtime_flags"]["values"] == [
        {"key": "trading_halted", "value": True}
    ]
    assert manifest["backup"]["critical_tables"]["job_runs"]["rows"] == 1
    assert result["space"]["source_wal_size_bytes"] > 0
    assert verify_database_backup(backup_path)["verified"] is True


def test_verify_rejects_tampered_backup(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    writer = _create_wal_database(source)
    try:
        result = _backup(
            source,
            tmp_path / "backups",
            now=datetime(2026, 7, 30, 12, 1, tzinfo=UTC),
        )
    finally:
        writer.close()

    backup_path = Path(str(result["backup_path"]))
    original_size = backup_path.stat().st_size
    with backup_path.open("r+b") as handle:
        handle.seek(max(0, original_size // 2))
        original = handle.read(1)
        assert original
        handle.seek(max(0, original_size // 2))
        handle.write(bytes([original[0] ^ 0xFF]))
        handle.flush()
        os.fsync(handle.fileno())

    with pytest.raises(DatabaseBackupVerificationError, match="SHA-256 mismatch"):
        verify_database_backup(backup_path)


def test_failed_backup_does_not_replace_previous_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "live.db"
    writer = _create_wal_database(source)
    backup_directory = tmp_path / "backups"
    try:
        first = _backup(
            source,
            backup_directory,
            now=datetime(2026, 7, 30, 12, 2, tzinfo=UTC),
        )
        first_path = Path(str(first["backup_path"]))
        first_manifest = Path(str(first["manifest_path"]))
        first_sha256 = _sha256(first_path)
        first_manifest_bytes = first_manifest.read_bytes()

        def reject_snapshot(_path: Path) -> dict[str, object]:
            raise DatabaseBackupVerificationError("injected validation failure")

        monkeypatch.setattr(backup_module, "_inspect_database", reject_snapshot)
        with pytest.raises(
            DatabaseBackupVerificationError,
            match="injected validation failure",
        ):
            _backup(
                source,
                backup_directory,
                now=datetime(2026, 7, 30, 12, 3, tzinfo=UTC),
            )
    finally:
        writer.close()

    assert _sha256(first_path) == first_sha256
    assert first_manifest.read_bytes() == first_manifest_bytes
    assert len(list(backup_directory.glob("alphapilot-full-*.db"))) == 1
    assert not list(backup_directory.glob("*.partial"))


def test_successful_backup_retains_only_three_matching_snapshots(
    tmp_path: Path,
) -> None:
    source = tmp_path / "live.db"
    writer = _create_wal_database(source)
    backup_directory = tmp_path / "backups"
    start = datetime(2026, 7, 20, 3, 30, tzinfo=UTC)
    try:
        for offset in range(5):
            _backup(
                source,
                backup_directory,
                now=start + timedelta(days=offset),
            )
    finally:
        writer.close()

    matching = sorted(backup_directory.glob("alphapilot-full-*.db"))
    assert len(matching) == 3
    assert "20260720" not in matching[0].name
    assert "20260721" not in matching[0].name
    assert "20260722" in matching[0].name
    assert all(manifest_path_for(path).is_file() for path in matching)

    unrelated = backup_directory / "manual-before-upgrade.db"
    unrelated.write_bytes(b"do-not-delete")
    orphan = backup_directory / "alphapilot-full-20260719T030000000000Z.db"
    orphan.write_bytes(b"manifest-missing-do-not-delete")
    _backup(
        source,
        backup_directory,
        now=start + timedelta(days=5),
    )
    assert unrelated.read_bytes() == b"do-not-delete"
    assert orphan.read_bytes() == b"manifest-missing-do-not-delete"
    assert len(list(backup_directory.glob("alphapilot-full-*.db"))) == 4


def test_retention_ignores_device_only_drift_and_removes_exact_oldest_backup(
    tmp_path: Path,
) -> None:
    source = tmp_path / "live.db"
    writer = _create_wal_database(source)
    backup_directory = tmp_path / "backups"
    start = datetime(2026, 7, 20, 3, 30, tzinfo=UTC)
    try:
        results = [
            _backup(
                source,
                backup_directory,
                now=start + timedelta(days=offset),
            )
            for offset in range(3)
        ]
        oldest = Path(str(results[0]["backup_path"]))
        oldest_manifest = manifest_path_for(oldest)
        manifest = json.loads(oldest_manifest.read_text(encoding="utf-8"))
        recorded_device = manifest["backup"]["file_identity"]["device"]
        assert type(recorded_device) is int
        manifest["backup"]["file_identity"]["device"] = recorded_device + 1
        oldest_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        newest = _backup(
            source,
            backup_directory,
            now=start + timedelta(days=3),
        )
    finally:
        writer.close()

    assert not oldest.exists()
    assert not oldest_manifest.exists()
    matching = sorted(backup_directory.glob("alphapilot-full-*.db"))
    assert len(matching) == 3
    assert Path(str(newest["backup_path"])) == matching[-1]
    assert newest["retention"]["removed"] == [str(oldest)]


def test_retention_does_not_count_same_size_tampered_backup_as_healthy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "live.db"
    writer = _create_wal_database(source)
    backup_directory = tmp_path / "backups"
    start = datetime(2026, 7, 20, 3, 30, tzinfo=UTC)
    try:
        results = [
            _backup(
                source,
                backup_directory,
                now=start + timedelta(days=offset),
            )
            for offset in range(3)
        ]
        oldest = Path(str(results[0]["backup_path"]))
        newest = Path(str(results[-1]["backup_path"]))
        with newest.open("r+b") as handle:
            handle.seek(max(0, newest.stat().st_size // 2))
            original = handle.read(1)
            assert original
            handle.seek(max(0, newest.stat().st_size // 2))
            handle.write(bytes([original[0] ^ 0xFF]))
            handle.flush()
            os.fsync(handle.fileno())

        _backup(
            source,
            backup_directory,
            now=start + timedelta(days=3),
        )
    finally:
        writer.close()

    assert oldest.is_file()
    assert manifest_path_for(oldest).is_file()
    assert len(list(backup_directory.glob("alphapilot-full-*.db"))) == 4


def test_retention_unlink_failure_keeps_manifest_for_existing_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "live.db"
    writer = _create_wal_database(source)
    backup_directory = tmp_path / "backups"
    start = datetime(2026, 7, 20, 3, 30, tzinfo=UTC)
    original_unlink = Path.unlink
    try:
        results = [
            _backup(
                source,
                backup_directory,
                now=start + timedelta(days=offset),
            )
            for offset in range(3)
        ]
        oldest = Path(str(results[0]["backup_path"]))
        oldest_manifest = manifest_path_for(oldest)

        def fail_target_unlink(
            path: Path,
            missing_ok: bool = False,
        ) -> None:
            if path == oldest:
                raise OSError("injected backup unlink failure")
            original_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", fail_target_unlink)
        with pytest.raises(OSError, match="injected backup unlink failure"):
            _backup(
                source,
                backup_directory,
                now=start + timedelta(days=3),
            )
    finally:
        writer.close()

    assert oldest.is_file()
    assert oldest_manifest.is_file()
    assert verify_database_backup(oldest, oldest_manifest)["verified"] is True


def test_space_gate_fails_before_creating_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "live.db"
    writer = _create_wal_database(source)
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    actual_usage = shutil.disk_usage(backup_directory)
    usage_type = type(actual_usage)
    monkeypatch.setattr(
        backup_module.shutil,
        "disk_usage",
        lambda _path: usage_type(
            actual_usage.total,
            actual_usage.total - source.stat().st_size + 1,
            source.stat().st_size - 1,
        ),
    )
    try:
        with pytest.raises(DatabaseBackupSpaceError, match="insufficient backup space"):
            create_database_backup(
                source,
                backup_directory,
                now=datetime(2026, 7, 30, 12, 4, tzinfo=UTC),
                minimum_free_bytes=0,
            )
    finally:
        writer.close()

    assert not list(backup_directory.glob("alphapilot-full-*.db"))
    assert stat.S_IMODE(backup_directory.stat().st_mode) == 0o700


def test_verify_rejects_group_or_world_readable_artifact(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    writer = _create_wal_database(source)
    try:
        result = _backup(
            source,
            tmp_path / "backups",
            now=datetime(2026, 7, 30, 12, 6, tzinfo=UTC),
        )
    finally:
        writer.close()

    backup_path = Path(str(result["backup_path"]))
    backup_path.chmod(0o644)
    with pytest.raises(
        DatabaseBackupVerificationError,
        match="permissions are too broad",
    ):
        verify_database_backup(backup_path)


def test_backup_lock_is_non_blocking(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    writer = _create_wal_database(source)
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir(mode=0o700)
    lock_path = backup_directory / ".alphapilot-full-backup.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(DatabaseBackupBusyError, match="already running"):
                _backup(
                    source,
                    backup_directory,
                    now=datetime(2026, 7, 30, 12, 5, tzinfo=UTC),
                )
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            writer.close()
