"""Cross-process locks for jobs and the in-process scheduler.

The registry's thread locks only serialize callers inside one Python process.
These advisory file locks add the matching boundary between the API process,
detached runners, and a scheduler process.  Lock files contain no database
credentials; their parent directory is partitioned by a hash of the database
identity.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self, cast

from sqlalchemy.engine import make_url

_LOCK_DIR_ENV = "ALPHAPILOT_PROCESS_LOCK_DIR"
_DEFAULT_PORTS = {"postgresql": 5432}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


class ProcessLockUnavailable(RuntimeError):
    """Raised when a non-blocking process lock is already held."""


def _query_value(value: str | tuple[str, ...] | None) -> str:
    if value is None:
        return ""
    if isinstance(value, tuple):
        return ",".join(value)
    return value


def _database_identity(database_url: str) -> str:
    """Return a credential-free, canonical identity for one database."""

    parsed = make_url(database_url)
    backend = parsed.get_backend_name()
    if backend == "sqlite":
        database = parsed.database
        if not database or database == ":memory:":
            # Separate processes never share an in-memory SQLite database.
            return f"sqlite:memory:{os.getpid()}"
        raw_path = database.removeprefix("file:")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return f"sqlite:file:{path.resolve(strict=False)}"

    query_host = _query_value(parsed.query.get("host"))
    host = (parsed.host or query_host or "<local>").casefold()
    port = parsed.port or _DEFAULT_PORTS.get(backend)
    database = parsed.database or ""
    # Usernames and passwords deliberately do not participate: two application
    # roles connected to the same database must still serialize the same job.
    return "\0".join((backend, host, str(port or ""), database))


def database_lock_key(database_url: str) -> str:
    """Return the opaque lock namespace for a database URL."""

    identity = _database_identity(database_url)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _default_lock_dir() -> Path:
    configured = os.environ.get(_LOCK_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".alphapilot-ai" / "process-locks"


def _secure_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"process lock path is not a secure directory: {path}")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise PermissionError(f"process lock directory belongs to another user: {path}")
    path.chmod(0o700)


def _lock_filename(namespace: str, name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("process lock name must not be empty")
    slug = _SAFE_NAME.sub("-", normalized).strip(".-")[:40] or "lock"
    digest = hashlib.sha256(f"{namespace}\0{normalized}".encode()).hexdigest()[:16]
    return f"{slug}-{digest}.lock"


def _lock_path(
    database_url: str,
    *,
    namespace: str,
    name: str,
    lock_dir: Path | None,
) -> Path:
    root = (lock_dir or _default_lock_dir()).expanduser().resolve(strict=False)
    database_dir = root / f"db-{database_lock_key(database_url)}"
    return database_dir / _lock_filename(namespace, name)


@dataclass(slots=True)
class ProcessFileLock:
    """One advisory exclusive lock whose lifetime is tied to an open fd."""

    path: Path
    blocking: bool = True
    _handle: BinaryIO | None = field(default=None, init=False, repr=False)

    @property
    def locked(self) -> bool:
        return self._handle is not None

    def acquire(self) -> Self:
        if self._handle is not None:
            raise RuntimeError(f"process lock is already acquired: {self.path}")

        _secure_directory(self.path.parent.parent)
        _secure_directory(self.path.parent)
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError(f"process lock path is not a regular file: {self.path}")
            if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
                raise PermissionError(f"process lock file belongs to another user: {self.path}")
            os.fchmod(descriptor, 0o600)
            handle = cast(BinaryIO, os.fdopen(descriptor, "a+b", buffering=0))
        except BaseException:
            os.close(descriptor)
            raise

        operation = fcntl.LOCK_EX
        if not self.blocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), operation)
        except OSError as exc:
            handle.close()
            if not self.blocking and exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise ProcessLockUnavailable(
                    f"process lock is held by another process: {self.path}"
                ) from exc
            raise
        self._handle = handle
        return self

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def job_process_lock(
    database_url: str,
    job_name: str,
    *,
    lock_dir: Path | None = None,
    blocking: bool = True,
) -> ProcessFileLock:
    """Build a same-database, same-job mutex.

    Job execution uses blocking acquisition by default so a second process
    waits for the first run instead of overlapping it.
    """

    return ProcessFileLock(
        _lock_path(
            database_url,
            namespace="job",
            name=job_name,
            lock_dir=lock_dir,
        ),
        blocking=blocking,
    )


def scheduler_process_lock(
    database_url: str,
    *,
    lock_dir: Path | None = None,
) -> ProcessFileLock:
    """Build the fail-fast singleton lock for one database's scheduler."""

    return ProcessFileLock(
        _lock_path(
            database_url,
            namespace="scheduler",
            name="scheduler",
            lock_dir=lock_dir,
        ),
        blocking=False,
    )
