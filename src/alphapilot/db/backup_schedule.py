from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from alphapilot.db.backup import (
    BACKUP_MANAGED_BY,
    BACKUP_PREFIX,
    DEFAULT_MINIMUM_FREE_BYTES,
    DEFAULT_RETENTION,
    DatabaseBackupBusyError,
    DatabaseBackupVerificationError,
    create_database_backup,
    verify_database_backup,
)

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
DEFAULT_WINDOW_START_HOUR = 22


@contextmanager
def _daily_backup_lock(runtime_directory: Path) -> Iterator[None]:
    runtime_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runtime_directory, 0o700)
    lock_path = runtime_directory / ".daily-backup.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(lock_path, 0o600)
    with os.fdopen(descriptor, "a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DatabaseBackupBusyError(
                "another daily database backup gate is already running"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _parse_created_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _find_verified_backup_for_window(
    backup_directory: Path,
    *,
    shanghai_date: str,
    window_start_hour: int,
) -> dict[str, Any] | None:
    if not backup_directory.is_dir():
        return None
    manifests = sorted(
        backup_directory.glob(f"{BACKUP_PREFIX}*.manifest.json"),
        reverse=True,
    )
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        if manifest.get("managed_by") != BACKUP_MANAGED_BY:
            continue
        created_at = _parse_created_at(manifest.get("created_at"))
        if created_at is None:
            continue
        market_created_at = created_at.astimezone(MARKET_TIMEZONE)
        if (
            market_created_at.date().isoformat() != shanghai_date
            or market_created_at.hour < window_start_hour
        ):
            continue
        backup = manifest.get("backup")
        if not isinstance(backup, dict):
            continue
        filename = backup.get("filename")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or not filename.startswith(BACKUP_PREFIX)
        ):
            continue
        try:
            verified = verify_database_backup(
                backup_directory / filename,
                manifest_path,
            )
        except (
            DatabaseBackupVerificationError,
            FileNotFoundError,
            OSError,
        ):
            continue
        return {
            "created_at": created_at.isoformat(),
            **verified,
        }
    return None


def _write_success_stamp(path: Path, shanghai_date: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".partial",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"{shanghai_date}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def run_daily_database_backup(
    source_db: Path,
    backup_directory: Path,
    runtime_directory: Path,
    *,
    retain: int = DEFAULT_RETENTION,
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
    window_start_hour: int = DEFAULT_WINDOW_START_HOUR,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run at most once per Shanghai date after the configured local hour."""

    if not 0 <= window_start_hour <= 23:
        raise ValueError("backup window_start_hour must be between 0 and 23")
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("daily backup timestamp must be timezone-aware")
    market_now = current_time.astimezone(MARKET_TIMEZONE)
    shanghai_date = market_now.date().isoformat()
    runtime_path = runtime_directory.expanduser().resolve()
    backup_path = backup_directory.expanduser().resolve()
    stamp_path = runtime_path / "last-success-shanghai-date"
    if market_now.hour < window_start_hour:
        return {
            "status": "not_due",
            "shanghai_date": shanghai_date,
            "window_start_hour": window_start_hour,
        }

    with _daily_backup_lock(runtime_path):
        if (
            stamp_path.is_file()
            and stamp_path.read_text(encoding="utf-8").strip() == shanghai_date
        ):
            return {
                "status": "already_complete",
                "shanghai_date": shanghai_date,
                "stamp_path": str(stamp_path),
            }

        recovered = _find_verified_backup_for_window(
            backup_path,
            shanghai_date=shanghai_date,
            window_start_hour=window_start_hour,
        )
        if recovered is not None:
            _write_success_stamp(stamp_path, shanghai_date)
            return {
                "status": "reconciled",
                "shanghai_date": shanghai_date,
                "stamp_path": str(stamp_path),
                **recovered,
            }

        result = create_database_backup(
            source_db,
            backup_path,
            retain=retain,
            minimum_free_bytes=minimum_free_bytes,
            now=market_now.astimezone(UTC),
        )
        _write_success_stamp(stamp_path, shanghai_date)
        return {
            "status": "backed_up",
            "shanghai_date": shanghai_date,
            "stamp_path": str(stamp_path),
            **result,
        }
