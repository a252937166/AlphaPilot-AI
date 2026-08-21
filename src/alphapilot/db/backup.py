from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

BACKUP_FORMAT_VERSION = 1
BACKUP_MANAGED_BY = "alphapilot.db.backup"
DEFAULT_RETENTION = 3
DEFAULT_MINIMUM_FREE_BYTES = 512 * 1024 * 1024
BACKUP_PREFIX = "alphapilot-full-"
_BACKUP_PATTERN = re.compile(r"^alphapilot-full-\d{8}T\d{12}Z\.db$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CRITICAL_TABLES = (
    "trade_proposals",
    "broker_orders",
    "runtime_flags",
    "job_runs",
)


class DatabaseBackupError(RuntimeError):
    """Base class for fail-closed database backup errors."""


class DatabaseBackupBusyError(DatabaseBackupError):
    """Another backup process already owns the non-blocking lock."""


class DatabaseBackupSpaceError(DatabaseBackupError):
    """The backup destination cannot safely hold another full snapshot."""


class DatabaseBackupVerificationError(DatabaseBackupError):
    """A backup or its manifest failed integrity verification."""


def _sqlite_read_only_uri(path: Path) -> str:
    return f"file:{quote(str(path), safe='/')}?mode=ro"


@contextmanager
def _read_only_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(
        _sqlite_read_only_uri(path),
        uri=True,
        timeout=15.0,
    )
    try:
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA query_only=ON")
        query_only = connection.execute("PRAGMA query_only").fetchone()
        if query_only is None or int(query_only[0]) != 1:
            raise DatabaseBackupError("SQLite source did not enter query_only mode")
        yield connection
    finally:
        connection.close()


@contextmanager
def _non_blocking_backup_lock(directory: Path) -> Iterator[None]:
    lock_path = directory / ".alphapilot-full-backup.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(lock_path, 0o600)
    with os.fdopen(descriptor, "a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DatabaseBackupBusyError("another database backup is already running") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _table_evidence(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for table in _CRITICAL_TABLES:
        exists = (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            is not None
        )
        table_evidence: dict[str, Any] = {"present": exists, "rows": None}
        if exists:
            row = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
            if row is None:
                raise DatabaseBackupVerificationError(
                    f"failed to count critical table {table}"
                )
            table_evidence["rows"] = int(row[0])
            columns = {
                str(column[1])
                for column in connection.execute(f'PRAGMA table_info("{table}")')
            }
            if "id" in columns:
                max_id = connection.execute(
                    f'SELECT MAX(id) FROM "{table}"'
                ).fetchone()
                table_evidence["max_id"] = (
                    int(max_id[0]) if max_id is not None and max_id[0] is not None else None
                )
            if table == "runtime_flags" and {"key", "value"} <= columns:
                table_evidence["values"] = [
                    {"key": str(key), "value": bool(value)}
                    for key, value in connection.execute(
                        'SELECT "key", "value" FROM "runtime_flags" ORDER BY "key"'
                    )
                ]
        evidence[table] = table_evidence
    return evidence


def _inspect_database(path: Path) -> dict[str, Any]:
    try:
        with _read_only_connection(path) as connection:
            quick_check_rows = [
                str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()
            ]
            if quick_check_rows != ["ok"]:
                raise DatabaseBackupVerificationError(
                    f"backup PRAGMA quick_check failed: {quick_check_rows}"
                )
            page_count_row = connection.execute("PRAGMA page_count").fetchone()
            page_size_row = connection.execute("PRAGMA page_size").fetchone()
            user_version_row = connection.execute("PRAGMA user_version").fetchone()
            if (
                page_count_row is None
                or page_size_row is None
                or user_version_row is None
            ):
                raise DatabaseBackupVerificationError(
                    "backup database metadata PRAGMA returned no result"
                )
            return {
                "quick_check": "ok",
                "page_count": int(page_count_row[0]),
                "page_size": int(page_size_row[0]),
                "user_version": int(user_version_row[0]),
                "critical_tables": _table_evidence(connection),
            }
    except sqlite3.Error as exc:
        raise DatabaseBackupVerificationError(
            f"backup database could not be inspected: {exc}"
        ) from exc


def _timestamp(now: datetime) -> tuple[str, str]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("backup timestamp must be timezone-aware")
    utc_now = now.astimezone(UTC)
    return (
        utc_now.strftime("%Y%m%dT%H%M%S%fZ"),
        utc_now.isoformat().replace("+00:00", "Z"),
    )


def manifest_path_for(backup_path: Path) -> Path:
    return backup_path.with_suffix(".manifest.json")


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".partial",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatabaseBackupVerificationError(
            f"backup manifest is unreadable: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise DatabaseBackupVerificationError("backup manifest root must be an object")
    return payload


def _matching_backups(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and _BACKUP_PATTERN.fullmatch(path.name) is not None
    )


def _file_identity(path: Path) -> dict[str, int]:
    file_stat = path.stat()
    return {
        "device": file_stat.st_dev,
        "inode": file_stat.st_ino,
        "mtime_ns": file_stat.st_mtime_ns,
        "ctime_ns": file_stat.st_ctime_ns,
    }


def _retention_identity_matches(
    recorded: object,
    observed: dict[str, int],
) -> bool:
    if (
        not isinstance(recorded, dict)
        or set(recorded) != {"device", "inode", "mtime_ns", "ctime_ns"}
        or type(recorded.get("device")) is not int
        or recorded["device"] < 0
    ):
        return False
    return all(
        type(recorded.get(field)) is int and recorded[field] == observed[field]
        for field in ("inode", "mtime_ns", "ctime_ns")
    )


def _retention_eligible_backups(directory: Path) -> list[Path]:
    """Return backups proven at creation and unchanged since publication."""

    eligible: list[Path] = []
    for backup_path in _matching_backups(directory):
        manifest_path = manifest_path_for(backup_path)
        if not manifest_path.is_file():
            continue
        try:
            manifest = _load_manifest(manifest_path)
        except DatabaseBackupVerificationError:
            continue
        evidence = manifest.get("backup")
        if (
            manifest.get("format_version") != BACKUP_FORMAT_VERSION
            or manifest.get("managed_by") != BACKUP_MANAGED_BY
            or not isinstance(evidence, dict)
            or evidence.get("filename") != backup_path.name
            or evidence.get("quick_check") != "ok"
            or evidence.get("size_bytes") != backup_path.stat().st_size
            or not isinstance(evidence.get("sha256"), str)
            or _SHA256_PATTERN.fullmatch(evidence["sha256"]) is None
            or not _retention_identity_matches(
                evidence.get("file_identity"),
                _file_identity(backup_path),
            )
        ):
            continue
        eligible.append(backup_path)
    return eligible


def _apply_retention(directory: Path, retain: int) -> tuple[list[str], list[str]]:
    if retain < 1:
        raise ValueError("backup retention must be at least 1")
    backups = _retention_eligible_backups(directory)
    removed: list[str] = []
    for backup_path in backups[:-retain]:
        manifest_path = manifest_path_for(backup_path)
        backup_path.unlink()
        manifest_path.unlink(missing_ok=True)
        removed.append(str(backup_path))
    if removed:
        _fsync_directory(directory)
    kept = [str(path) for path in _retention_eligible_backups(directory)]
    return kept, removed


def _require_backup_space(
    source_path: Path,
    backup_directory: Path,
    minimum_free_bytes: int,
) -> dict[str, int]:
    if minimum_free_bytes < 0:
        raise ValueError("minimum_free_bytes must not be negative")
    source_size = source_path.stat().st_size
    wal_path = source_path.with_name(f"{source_path.name}-wal")
    source_wal_size = wal_path.stat().st_size if wal_path.is_file() else 0
    free_bytes = shutil.disk_usage(backup_directory).free
    required_bytes = source_size + source_wal_size + minimum_free_bytes
    if free_bytes < required_bytes:
        raise DatabaseBackupSpaceError(
            "insufficient backup space: "
            f"free={free_bytes}, required={required_bytes}, source={source_size}, "
            f"source_wal={source_wal_size}, "
            f"reserve={minimum_free_bytes}"
        )
    return {
        "source_size_bytes": source_size,
        "source_wal_size_bytes": source_wal_size,
        "free_bytes_before": free_bytes,
        "required_bytes": required_bytes,
        "minimum_free_bytes": minimum_free_bytes,
    }


def create_database_backup(
    source_db: Path,
    backup_directory: Path,
    *,
    retain: int = DEFAULT_RETENTION,
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
    now: datetime | None = None,
    pages: int = 2_048,
    sleep: float = 0.05,
) -> dict[str, Any]:
    """Create, validate and atomically publish one online SQLite backup."""

    source_path = source_db.expanduser().resolve()
    directory = backup_directory.expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"source database does not exist: {source_path}")
    if retain < 1:
        raise ValueError("backup retention must be at least 1")
    if pages <= 0:
        raise ValueError("backup pages must be positive")
    if sleep < 0:
        raise ValueError("backup sleep must not be negative")

    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    timestamp, created_at = _timestamp(now or datetime.now(UTC))
    backup_path = directory / f"{BACKUP_PREFIX}{timestamp}.db"
    manifest_path = manifest_path_for(backup_path)
    if backup_path.exists() or manifest_path.exists():
        raise FileExistsError(f"backup timestamp already exists: {backup_path.name}")

    with _non_blocking_backup_lock(directory):
        space = _require_backup_space(source_path, directory, minimum_free_bytes)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{backup_path.name}.",
            suffix=".partial",
            dir=directory,
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        published_database = False
        try:
            with (
                _read_only_connection(source_path) as source,
                sqlite3.connect(temporary_path) as destination,
            ):
                source_journal_mode_row = source.execute("PRAGMA journal_mode").fetchone()
                if source_journal_mode_row is None:
                    raise DatabaseBackupError("SQLite source journal_mode is unavailable")
                source_journal_mode = str(source_journal_mode_row[0]).lower()
                source.backup(destination, pages=pages, sleep=sleep)

            inspection = _inspect_database(temporary_path)
            os.chmod(temporary_path, 0o600)
            _fsync_file(temporary_path)
            backup_size = temporary_path.stat().st_size
            backup_sha256 = _sha256(temporary_path)
            manifest: dict[str, Any] = {
                "format_version": BACKUP_FORMAT_VERSION,
                "managed_by": BACKUP_MANAGED_BY,
                "created_at": created_at,
                "source": {
                    "path": str(source_path),
                    "journal_mode": source_journal_mode,
                    "query_only": True,
                    "size_bytes": space["source_size_bytes"],
                },
                "backup": {
                    "filename": backup_path.name,
                    "size_bytes": backup_size,
                    "sha256": backup_sha256,
                    **inspection,
                },
            }

            os.replace(temporary_path, backup_path)
            published_database = True
            _fsync_directory(directory)
            manifest["backup"]["file_identity"] = _file_identity(backup_path)
            _write_manifest(manifest_path, manifest)
            kept, removed = _apply_retention(directory, retain)
            return {
                "backup_path": str(backup_path),
                "manifest_path": str(manifest_path),
                "sha256": backup_sha256,
                "size_bytes": backup_size,
                "quick_check": inspection["quick_check"],
                "critical_tables": inspection["critical_tables"],
                "source_query_only": True,
                "source_journal_mode": source_journal_mode,
                "space": space,
                "retention": {
                    "limit": retain,
                    "kept": kept,
                    "removed": removed,
                },
            }
        except Exception:
            temporary_path.unlink(missing_ok=True)
            if published_database and not manifest_path.exists():
                backup_path.unlink(missing_ok=True)
                _fsync_directory(directory)
            raise


def verify_database_backup(
    backup_db: Path,
    manifest_file: Path | None = None,
) -> dict[str, Any]:
    """Verify checksum, manifest evidence and SQLite integrity without mutation."""

    backup_path = backup_db.expanduser().resolve()
    manifest_path = (
        manifest_file.expanduser().resolve()
        if manifest_file is not None
        else manifest_path_for(backup_path)
    )
    if not backup_path.is_file():
        raise FileNotFoundError(f"backup database does not exist: {backup_path}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"backup manifest does not exist: {manifest_path}")
    for protected_path in (backup_path, manifest_path):
        mode = protected_path.stat().st_mode & 0o777
        if mode & 0o077:
            raise DatabaseBackupVerificationError(
                f"backup artifact permissions are too broad: {protected_path} mode={mode:o}"
            )

    manifest = _load_manifest(manifest_path)
    if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
        raise DatabaseBackupVerificationError(
            f"unsupported backup manifest format: {manifest.get('format_version')!r}"
        )
    if manifest.get("managed_by") != BACKUP_MANAGED_BY:
        raise DatabaseBackupVerificationError(
            f"unsupported backup manager: {manifest.get('managed_by')!r}"
        )
    source = manifest.get("source")
    expected = manifest.get("backup")
    if not isinstance(source, dict) or source.get("query_only") is not True:
        raise DatabaseBackupVerificationError(
            "backup manifest does not prove a query_only source"
        )
    if not isinstance(expected, dict):
        raise DatabaseBackupVerificationError("backup manifest is missing backup evidence")
    if expected.get("filename") != backup_path.name:
        raise DatabaseBackupVerificationError(
            "backup filename does not match its manifest"
        )

    observed_size = backup_path.stat().st_size
    expected_size = expected.get("size_bytes")
    if not isinstance(expected_size, int) or observed_size != expected_size:
        raise DatabaseBackupVerificationError(
            f"backup size mismatch: expected={expected_size!r}, observed={observed_size}"
        )
    observed_sha256 = _sha256(backup_path)
    expected_sha256 = expected.get("sha256")
    if not isinstance(expected_sha256, str) or observed_sha256 != expected_sha256:
        raise DatabaseBackupVerificationError(
            "backup SHA-256 mismatch: "
            f"expected={expected_sha256!r}, observed={observed_sha256}"
        )

    observed = _inspect_database(backup_path)
    for field in (
        "quick_check",
        "page_count",
        "page_size",
        "user_version",
        "critical_tables",
    ):
        if observed[field] != expected.get(field):
            raise DatabaseBackupVerificationError(
                f"backup evidence mismatch for {field}: "
                f"expected={expected.get(field)!r}, observed={observed[field]!r}"
            )
    return {
        "verified": True,
        "backup_path": str(backup_path),
        "manifest_path": str(manifest_path),
        "sha256": observed_sha256,
        "size_bytes": observed_size,
        **observed,
    }
