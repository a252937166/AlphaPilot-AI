"""Single-authority implementation for the P4.2a v2.2 rehearsal harness.

The thin ``__main__`` shim is intentionally state-free.  This package module
is the only v2.2 owner of process audit policy, temporary authority,
capability/delegation registries, and the disclosed-repeatable series ledger.
It reuses the unchanged v2.1 producer/consumer pipeline through its registered
offline capability.  The v2.1 runner is imported only as a frozen source of
pure control/pipeline helpers; none of its authority state is activated or
accepted by this module.
"""

from __future__ import annotations

# The singleton guard must execute before every authority-bearing import.
# ruff: noqa: E402, I001

import os
import sys

MODULE_NAME = "scripts.p4_2a_v2_2_heldout_rehearsal"
if __name__ == "__main__":
    raise RuntimeError(
        "the v2.2 implementation module cannot execute as __main__; use the thin shim"
    )
if __name__ != MODULE_NAME or sys.modules.get(MODULE_NAME) is not sys.modules.get(__name__):
    raise RuntimeError("v2.2 authority module identity is not the package singleton")

import argparse
import contextvars
import copy
import ctypes
import errno
import fcntl
import hashlib
import importlib.metadata
import inspect
import json
import platform
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sysconfig
import tempfile
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Literal, NoReturn, cast


JsonObject = dict[str, Any]
Clock = Callable[[], datetime]
MonotonicClock = Callable[[], float]
Sleeper = Callable[[float], None]
AttemptOutcome = Literal[
    "FAILED",
    "INCOMPLETE_UNTERMINALIZED",
    "CANDIDATE_VALIDATED_AND_SELECTED",
]
ExecutionMode = Literal["REGISTERED_OFFICIAL", "DISPOSABLE_FULL_SHAPE_TEST"]


class RehearsalV22Error(RuntimeError):
    """Fail-closed v2.2 harness error."""


@dataclass(frozen=True)
class _AuditPolicy:
    project_root: Path
    write_roots: tuple[Path, ...]
    exact_write_paths: tuple[Path, ...]
    create_only_roots: tuple[Path, ...]
    sqlite_roots: tuple[Path, ...]
    git_roots: tuple[Path, ...]
    subprocess_mode: Literal["none", "git-read", "synthetic-git"]
    synthetic_git_root: Path | None = None
    ledger_write_phase: Literal["initialize", "active", "candidate", "frozen"] | None = None
    ledger_root: Path | None = None
    active_attempt_root: Path | None = None


_AUDIT_POLICY: contextvars.ContextVar[_AuditPolicy | None] = contextvars.ContextVar(
    "p4_2a_v2_2_rehearsal_audit_policy",
    default=None,
)
_TEMP_AUTHORITY: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "p4_2a_v2_2_rehearsal_temp_authority",
    default=None,
)


def _build_import_guard_state() -> tuple[Any, Any, Any]:
    active = os.environ.get("ALPHAPILOT_P42A_REHEARSAL_V2_2_ENV_LOCKED") == "1"
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    validator_suffix = "/scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py"
    validator_pending = bool(
        active
        and isinstance(main_file, str)
        and os.path.abspath(main_file).endswith(validator_suffix)
    )
    completed = False

    def is_active() -> bool:
        return active

    def finish_core_import() -> None:
        nonlocal active, completed
        if validator_pending:
            return
        if completed or sys.modules.get(MODULE_NAME) is not sys.modules.get(__name__):
            raise RehearsalV22Error("core import guard finalization is repeated or split")
        completed = True
        active = False

    def finish_validator_import(validator_module: ModuleType) -> None:
        nonlocal active, completed
        if (
            not validator_pending
            or completed
            or not active
            or validator_module.__name__ != "__main__"
            or sys.modules.get("__main__") is not validator_module
            or sys.modules.get("scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle") is not None
        ):
            raise RehearsalV22Error(
                "validator import guard finalization is forged, repeated, or split"
            )
        validator_file = getattr(validator_module, "__file__", None)
        if not isinstance(validator_file, str):
            raise RehearsalV22Error("validator import guard lacks a source origin")
        root = Path(validator_file).resolve(strict=True).parent.parent
        if Path(validator_file).resolve(strict=True) != root / VALIDATOR_RELATIVE:
            raise RehearsalV22Error("validator import guard source origin drifted")
        _assert_locked_validator_bootstrap(root)
        _classify_loaded_module_origins(root)
        completed = True
        active = False

    return is_active, finish_core_import, finish_validator_import


(
    _import_guard_is_active,
    _finish_core_import_guard,
    _finish_validator_import_guard,
) = _build_import_guard_state()


def _path_in_roots(path: Path, roots: Sequence[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def _lexical_path(value: object) -> Path | None:
    if isinstance(value, (bool, int)):
        return None
    if not isinstance(value, (str, bytes, os.PathLike)):
        return None
    try:
        return Path(os.path.abspath(os.fsdecode(value)))
    except (OSError, TypeError, ValueError):
        return None


def _require_audited_write_path(value: object, policy: _AuditPolicy) -> Path:
    path = _lexical_path(value)
    if path is None or not (
        _path_in_roots(path, policy.write_roots) or path in policy.exact_write_paths
    ):
        raise RehearsalV22Error("v2.2 rehearsal attempted an external filesystem write")
    existing = path
    while not os.path.lexists(existing):
        if existing == existing.parent:
            raise RehearsalV22Error("v2.2 write target has no safe existing parent")
        existing = existing.parent
    try:
        resolved = existing.resolve(strict=True)
    except OSError as exc:
        raise RehearsalV22Error("v2.2 write target parent is unavailable") from exc
    permitted_resolved = tuple(root.resolve(strict=False) for root in policy.write_roots)
    exact_parents = tuple(path.parent.resolve(strict=False) for path in policy.exact_write_paths)
    if not (
        _path_in_roots(resolved, permitted_resolved)
        or _path_in_roots(resolved, exact_parents)
        or path in policy.exact_write_paths
    ):
        raise RehearsalV22Error("v2.2 write target escapes through an alias")
    return path


def _audited_directory_from_fd(value: object) -> Path:
    """Resolve one directory descriptor without trusting cwd or a textual alias."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RehearsalV22Error("v2.2 mutation dir_fd is invalid")
    try:
        descriptor_metadata = os.fstat(value)
        raw = fcntl.fcntl(value, fcntl.F_GETPATH, b"\0" * 1024)
        terminator = raw.find(b"\0")
        if terminator <= 0:
            raise RehearsalV22Error("v2.2 mutation dir_fd path is unavailable")
        directory = Path(os.fsdecode(raw[:terminator]))
        path_metadata = directory.lstat()
    except RehearsalV22Error:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise RehearsalV22Error("v2.2 mutation dir_fd identity is unavailable") from exc
    if (
        not directory.is_absolute()
        or directory.is_symlink()
        or not stat.S_ISDIR(descriptor_metadata.st_mode)
        or not stat.S_ISDIR(path_metadata.st_mode)
        or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
        != (path_metadata.st_dev, path_metadata.st_ino)
        or directory.resolve(strict=True) != directory.absolute()
    ):
        raise RehearsalV22Error("v2.2 mutation dir_fd is aliased or not one directory")
    return directory.absolute()


def _audited_mutation_path(
    value: object,
    dir_fd: object,
    policy: _AuditPolicy,
) -> Path:
    """Resolve an audit mutation path, including safe openat-style events."""

    if dir_fd is None or dir_fd == -1:
        if isinstance(value, (bool, int)) or not isinstance(
            value,
            (str, bytes, os.PathLike),
        ):
            raise RehearsalV22Error(
                "v2.2 mutation without dir_fd requires an absolute path"
            )
        lexical = _lexical_path(value)
        raw_path = Path(os.fsdecode(cast(str | bytes | os.PathLike[str], value)))
        if lexical is None or not raw_path.is_absolute():
            raise RehearsalV22Error(
                "v2.2 mutation without dir_fd requires an absolute path"
            )
        return _require_audited_write_path(lexical, policy)
    if isinstance(value, (bool, int)) or not isinstance(
        value,
        (str, bytes, os.PathLike),
    ):
        raise RehearsalV22Error("v2.2 relative mutation path is invalid")
    raw_relative = os.fsdecode(cast(str | bytes | os.PathLike[str], value))
    segments = raw_relative.split("/")
    if (
        not raw_relative
        or "\0" in raw_relative
        or "\\" in raw_relative
        or raw_relative.startswith("/")
        or raw_relative.endswith("/")
        or "//" in raw_relative
        or any(segment in {"", ".", ".."} for segment in segments)
        or PurePosixPath(raw_relative).as_posix() != raw_relative
    ):
        raise RehearsalV22Error("v2.2 dir_fd mutation path is not canonical relative")
    relative = Path(raw_relative)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise RehearsalV22Error("v2.2 dir_fd mutation path is not canonical relative")
    directory = _audited_directory_from_fd(dir_fd)
    return _require_audited_write_path(directory.joinpath(relative), policy)


def _mutation_dir_fd(event: str, arguments: tuple[object, ...]) -> object:
    index_by_event = {
        "os.chmod": 2,
        "os.chown": 3,
        "os.mkdir": 2,
        "os.remove": 1,
        "os.rmdir": 1,
        "os.unlink": 1,
        "os.utime": 3,
        "shutil.rmtree": 1,
    }
    index = index_by_event.get(event)
    if index is None or len(arguments) <= index:
        return None
    return arguments[index]


def _path_in_create_only_root(path: Path, policy: _AuditPolicy) -> bool:
    return _path_in_roots(path, policy.create_only_roots)


def _ledger_create_is_authorized(
    path: Path,
    *,
    directory: bool,
    policy: _AuditPolicy,
) -> bool:
    phase = policy.ledger_write_phase
    ledger_root = policy.ledger_root
    attempt_root = policy.active_attempt_root
    if phase is None or ledger_root is None:
        return False
    if phase == "initialize":
        permitted = {ledger_root, ledger_root / "series.json", ledger_root / ".series.lock"}
        if directory:
            permitted.add(ledger_root / "attempts")
        return path in permitted
    if attempt_root is None or not path.is_relative_to(attempt_root):
        return False
    relative = path.relative_to(attempt_root)
    parts = relative.parts
    if phase == "frozen":
        return False
    if phase == "candidate":
        return not directory and parts == ("terminal.json",)
    if not parts:
        return directory
    if directory:
        return parts[0] == "evidence" and all(part not in {"", ".", ".."} for part in parts)
    if parts in {("started.json",), ("candidate.json",), ("terminal.json",)}:
        return True
    return len(parts) >= 2 and parts[0] == "evidence"


def _require_audited_write_descriptor(value: object, policy: _AuditPolicy) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RehearsalV22Error("v2.2 write descriptor is invalid")
    try:
        raw = fcntl.fcntl(value, fcntl.F_GETPATH, b"\0" * 1024)
        terminator = raw.find(b"\0")
        if terminator <= 0:
            raise RehearsalV22Error("v2.2 write descriptor path is unavailable")
        path = _require_audited_write_path(Path(os.fsdecode(raw[:terminator])), policy)
        descriptor_metadata = os.fstat(value)
        path_metadata = path.stat()
        flags = fcntl.fcntl(value, fcntl.F_GETFL)
    except RehearsalV22Error:
        raise
    except (OSError, ValueError) as exc:
        raise RehearsalV22Error("v2.2 write descriptor identity is unavailable") from exc
    if (
        not stat.S_ISREG(descriptor_metadata.st_mode)
        or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
        != (path_metadata.st_dev, path_metadata.st_ino)
        or flags & os.O_ACCMODE not in {os.O_WRONLY, os.O_RDWR}
        or flags & os.O_APPEND
    ):
        raise RehearsalV22Error("v2.2 write descriptor identity drifted")


GIT_CONFIG_PREFIX = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.commitGraph=false",
    "-c",
    "gc.auto=0",
)


def _git_environment(*, synthetic_identity: bool = False) -> dict[str, str]:
    result = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "PAGER": "cat",
    }
    if synthetic_identity:
        result.update(
            {
                "GIT_AUTHOR_NAME": "AlphaPilot v2.2 disposable rehearsal",
                "GIT_AUTHOR_EMAIL": "v2-2-rehearsal@invalid.local",
                "GIT_COMMITTER_NAME": "AlphaPilot v2.2 disposable rehearsal",
                "GIT_COMMITTER_EMAIL": "v2-2-rehearsal@invalid.local",
                "GIT_AUTHOR_DATE": "2026-08-10T12:30:00Z",
                "GIT_COMMITTER_DATE": "2026-08-10T12:30:00Z",
            }
        )
    return result


def _git_audit_allowed(
    command: object,
    cwd: object,
    environment: object,
    policy: _AuditPolicy,
) -> bool:
    if not isinstance(command, (list, tuple)) or any(not isinstance(item, str) for item in command):
        return False
    arguments = tuple(cast(Sequence[str], command))
    if not arguments or arguments[0] != "/usr/bin/git":
        return False
    if arguments[1 : 1 + len(GIT_CONFIG_PREFIX)] != GIT_CONFIG_PREFIX:
        return False
    offset = 1 + len(GIT_CONFIG_PREFIX)
    if len(arguments) <= offset + 2 or arguments[offset] != "-C":
        return False
    try:
        root = Path(arguments[offset + 1]).resolve(strict=True)
    except OSError:
        return False
    if root not in policy.git_roots:
        return False
    if not isinstance(environment, Mapping):
        return False
    observed = {str(key): str(value) for key, value in environment.items()}
    permitted = [_git_environment()]
    if policy.subprocess_mode == "synthetic-git":
        permitted.append(_git_environment(synthetic_identity=True))
    if observed not in permitted:
        return False
    operation = arguments[offset + 2 :]
    if not operation:
        return False
    if operation[0] == "ls-tree":
        if (
            len(operation) != 6
            or operation[1:3] != ("-z", "--full-tree")
            or not _lower_hex(operation[3], 40)
            or operation[4] != "--"
        ):
            return False
        try:
            relative = _relative_text(operation[5], "audited Git ls-tree path")
        except RehearsalV22Error:
            return False
        return (
            relative == operation[5]
            and policy.subprocess_mode in {"git-read", "synthetic-git"}
        )
    if operation[0] == "diff-tree":
        return (
            len(operation) == 11
            and operation[1:9]
            == (
                "--root",
                "--no-commit-id",
                "-r",
                "--no-ext-diff",
                "--no-textconv",
                "--name-status",
                "--no-renames",
                "-z",
            )
            and _lower_hex(operation[9], 40)
            and operation[10] == "--"
            and policy.subprocess_mode in {"git-read", "synthetic-git"}
        )
    read_only = operation[0] in {
        "cat-file",
        "diff",
        "log",
        "merge-base",
        "rev-list",
        "rev-parse",
        "show",
        "status",
    }
    if read_only:
        return policy.subprocess_mode in {"git-read", "synthetic-git"}
    return policy.subprocess_mode == "synthetic-git" and operation[0] in {
        "add",
        "commit",
        "read-tree",
    }


def _process_audit_hook(event: str, arguments: tuple[object, ...]) -> None:
    policy = _AUDIT_POLICY.get()
    if policy is not None and not _audit_policy_is_issued(policy):
        raise RehearsalV22Error("v2.2 audit policy was forged outside its issued lifetime")
    if _import_guard_is_active():
        if event == "open":
            mode = arguments[1] if len(arguments) > 1 else None
            flags = arguments[2] if len(arguments) > 2 else 0
            writing = (
                isinstance(mode, str) and any(character in mode for character in "wax+")
            ) or (
                isinstance(flags, int)
                and bool(
                    flags
                    & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND | os.O_EXCL)
                )
            )
            if writing:
                raise RehearsalV22Error("v2.2 bootstrap import attempted a write")
            return
        if (
            event
            in {
                "os.chmod",
                "os.chown",
                "os.fork",
                "os.forkpty",
                "os.link",
                "os.mkdir",
                "os.posix_spawn",
                "os.posix_spawnp",
                "os.putenv",
                "os.remove",
                "os.rename",
                "os.replace",
                "os.rmdir",
                "os.symlink",
                "os.system",
                "os.truncate",
                "os.unlink",
                "os.unsetenv",
                "os.utime",
                "pty.spawn",
                "shutil.rmtree",
                "subprocess.Popen",
                "_thread.start_new_thread",
            }
            or event.startswith(("os.exec", "socket."))
            or event == "sqlite3.connect"
        ):
            raise RehearsalV22Error("v2.2 bootstrap import attempted an external effect")
    if policy is None:
        return
    if event == "open":
        path = arguments[0] if arguments else None
        mode = arguments[1] if len(arguments) > 1 else None
        flags = arguments[2] if len(arguments) > 2 else 0
        writing = (isinstance(mode, str) and any(character in mode for character in "wax+")) or (
            isinstance(flags, int)
            and bool(
                flags
                & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND | os.O_EXCL)
            )
        )
        if writing:
            if isinstance(path, int) and not isinstance(path, bool):
                _require_audited_write_descriptor(path, policy)
            else:
                audited_path = _require_audited_write_path(path, policy)
                if _path_in_create_only_root(audited_path, policy) and (
                    not isinstance(flags, int)
                    or flags & os.O_EXCL == 0
                    or flags & os.O_CREAT == 0
                    or flags & (os.O_APPEND | os.O_TRUNC)
                    or os.path.lexists(audited_path)
                    or not _ledger_create_is_authorized(
                        audited_path,
                        directory=False,
                        policy=policy,
                    )
                ):
                    raise RehearsalV22Error("v2.2 live ledger permits only a new O_EXCL file")
        return
    if event in {
        "os.chmod",
        "os.chown",
        "os.lchown",
        "os.mkdir",
        "os.remove",
        "os.rmdir",
        "os.truncate",
        "os.unlink",
        "os.utime",
        "shutil.rmtree",
    }:
        audited_path = _audited_mutation_path(
            arguments[0] if arguments else None,
            _mutation_dir_fd(event, arguments),
            policy,
        )
        if _path_in_create_only_root(audited_path, policy) and (
            event != "os.mkdir"
            or os.path.lexists(audited_path)
            or not _ledger_create_is_authorized(
                audited_path,
                directory=True,
                policy=policy,
            )
        ):
            raise RehearsalV22Error(
                "v2.2 live ledger forbids mutation or deletion of an existing member"
            )
        return
    if event in {"os.rename", "os.replace", "os.link"}:
        source = _audited_mutation_path(
            arguments[0] if arguments else None,
            arguments[2] if len(arguments) > 2 else None,
            policy,
        )
        target = _audited_mutation_path(
            arguments[1] if len(arguments) > 1 else None,
            arguments[3] if len(arguments) > 3 else None,
            policy,
        )
        if _path_in_create_only_root(source, policy) or _path_in_create_only_root(
            target,
            policy,
        ):
            raise RehearsalV22Error("v2.2 live ledger forbids rename, replace, and link operations")
        return
    if event == "os.symlink":
        raise RehearsalV22Error("v2.2 rehearsal forbids symlink creation")
    if event == "subprocess.Popen":
        if not _git_audit_allowed(
            arguments[1] if len(arguments) > 1 else None,
            arguments[2] if len(arguments) > 2 else None,
            arguments[3] if len(arguments) > 3 else None,
            policy,
        ):
            raise RehearsalV22Error("v2.2 rehearsal attempted an unauthorized subprocess")
        return
    if event in {
        "_thread.start_new_thread",
        "os.fork",
        "os.forkpty",
        "os.posix_spawn",
        "os.posix_spawnp",
        "os.putenv",
        "os.system",
        "os.unsetenv",
        "pty.spawn",
    } or event.startswith("os.exec"):
        raise RehearsalV22Error("v2.2 rehearsal attempted another process or thread")
    if event == "sqlite3.connect":
        database = arguments[0] if arguments else None
        if database == ":memory:":
            return
        raw = str(database)
        if raw.startswith("file:"):
            raw = raw[5:].split("?", 1)[0]
        try:
            path = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise RehearsalV22Error("v2.2 SQLite path is invalid") from exc
        if not _path_in_roots(path, policy.sqlite_roots):
            raise RehearsalV22Error("v2.2 rehearsal attempted a real database open")
        return
    if event.startswith("socket."):
        raise RehearsalV22Error("v2.2 rehearsal attempted a network operation")


sys.addaudithook(_process_audit_hook)


def _build_audit_policy_issuer() -> tuple[Any, Any]:
    # Rebound immutable tuple: closure inspection cannot mutate the live
    # issuance set by inserting a forged policy.
    policy_registry: tuple[_AuditPolicy, ...] = ()

    def policy_is_issued(policy: _AuditPolicy) -> bool:
        return any(record is policy for record in policy_registry)

    @contextmanager
    def audited_execution(
        policy: _AuditPolicy,
        *,
        execution_context: ExecutionCapability | None = None,
        bootstrap: _BootstrapEvidence | None = None,
        validator_replay_module: ModuleType | None = None,
    ) -> Iterator[None]:
        nonlocal policy_registry
        current = _AUDIT_POLICY.get()
        if current is not None:
            if not policy_is_issued(current):
                raise RehearsalV22Error("outer audit policy is forged")
            if (
                policy.project_root != current.project_root
                or policy.write_roots != current.write_roots
                or policy.exact_write_paths != current.exact_write_paths
                or policy.create_only_roots != current.create_only_roots
                or policy.sqlite_roots != current.sqlite_roots
                or policy.git_roots != current.git_roots
                or policy.subprocess_mode != current.subprocess_mode
                or policy.synthetic_git_root != current.synthetic_git_root
                or execution_context is not None
                or bootstrap is not None
                or validator_replay_module is not None
            ):
                raise RehearsalV22Error("nested audit policy attempted to broaden authority")
        elif (
            sum(
                value is not None
                for value in (execution_context, bootstrap, validator_replay_module)
            )
            != 1
        ):
            raise RehearsalV22Error(
                "outer audit policy requires exactly one issued bootstrap or capability"
            )
        policy_registry = (*policy_registry, policy)
        token = _AUDIT_POLICY.set(policy)
        try:
            if execution_context is not None:
                observed = _validate_execution_capability(
                    execution_context,
                    project_root=policy.project_root,
                )
                if observed.project_root != policy.project_root:
                    raise RehearsalV22Error("audit policy and capability roots disagree")
            elif bootstrap is not None:
                _validate_bootstrap_evidence(bootstrap)
                if bootstrap.project_root != policy.project_root:
                    raise RehearsalV22Error("audit policy and bootstrap roots disagree")
            elif validator_replay_module is not None:
                if (
                    validator_replay_module.__name__ != "__main__"
                    or sys.modules.get("__main__") is not validator_replay_module
                    or sys.modules.get("scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle")
                    is not None
                ):
                    raise RehearsalV22Error("official replay validator identity drifted")
                _assert_locked_validator_bootstrap(policy.project_root)
            yield
        finally:
            _AUDIT_POLICY.reset(token)
            policy_registry = tuple(record for record in policy_registry if record is not policy)

    return policy_is_issued, audited_execution


_audit_policy_is_issued, _audited_execution = _build_audit_policy_issuer()


# Repository imports occur only after the import-time effect guard is installed.
from scripts import build_p4_2a_gold_sample as gold_builder
from scripts import build_p4_2a_v2_heldout_adjudication_ui as heldout_ui
from scripts import evaluate_p4_2a_v2_heldout as evaluator
from scripts import finalize_p4_2a_v2_heldout_adjudication as heldout_finalizer
from scripts import prepare_p4_2a_v2_heldout as prepare
from scripts import rehearse_p4_2a_v2_heldout_full_path as base_runner
from scripts import seal_p4_2a_v2_ai_draft as base_seal
from scripts import seal_p4_2a_v2_heldout_draft as heldout_seal
from scripts.run_p4_2a_offline_extract import (
    MonotonicNsClock,
    RecordedAtClock,
)
from scripts.run_p4_2a_v2_dev_calibration import ProductionSnapshot
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.db.models import LLMCall

_finish_core_import_guard()


REGISTERED_PROJECT_ROOT = Path("/Users/ouyangduning/Documents/project/interesting/AlphaPilot-AI")
SHIM_RELATIVE = Path("scripts/rehearse_p4_2a_v2_2_heldout_full_path.py")
IMPLEMENTATION_RELATIVE = Path("scripts/p4_2a_v2_2_heldout_rehearsal.py")
VALIDATOR_RELATIVE = Path("scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py")
RUNNER_TEST_RELATIVE = Path("tests/test_p4_2a_v2_2_heldout_rehearsal_runner.py")
VALIDATOR_TEST_RELATIVE = Path("tests/test_p4_2a_v2_2_heldout_rehearsal_validator.py")
IMPLEMENTATION_SURFACE = (
    SHIM_RELATIVE,
    IMPLEMENTATION_RELATIVE,
    VALIDATOR_RELATIVE,
    RUNNER_TEST_RELATIVE,
    VALIDATOR_TEST_RELATIVE,
)
PREREGISTRATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-2-preregistration-20260811.json"
)
PREREGISTRATION_SHA256 = "8f52a9e24df11e23a900b5cb79720f3b4aae999c6ab770a9038ebe2617e8d8d5"
PREREGISTRATION_COMMIT = "be6423506f598c290db7ad944b002763fdf806ab"
PREREGISTRATION_PARENT = "5fe756401f20e67ff5c868bf29f099c1bfe5b4d3"
BUNDLE_SCHEMA_RELATIVE = Path("config/schemas/p4_2a_v2_2_heldout_rehearsal_bundle.schema.json")
BUNDLE_SCHEMA_SHA256 = "19903ac94d4d7ced81c7f18e7b8880bd1dbb68fd3ededf3f0b91f89d034aa5db"
RELEASE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_2_heldout_release_authorization.schema.json"
)
RELEASE_SCHEMA_SHA256 = "098d213f510718aab0d9c6bfc950a30bb1c4841ca151631bea78c1bf0238e7ea"
RELEASE_REVIEW_REQUEST_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-2-implementation-and-execution-review-request-20260811.json"
)
RELEASE_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-2-release-authorization-20260811.json"
)
V2_1_BUNDLE_SCHEMA_RELATIVE = Path("config/schemas/p4_2a_v2_1_heldout_rehearsal_bundle.schema.json")
V2_1_BUNDLE_SCHEMA_SHA256 = "ed827e29ce853f07a9110d44c98793a4cc3ef0634a12fe7e8bc64c7290d7d716"
V2_1_RELEASE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_1_heldout_release_authorization.schema.json"
)
V2_1_RELEASE_SCHEMA_SHA256 = "c5a4ecfe8c5bf3e3ebea2d4470337a67dde3a8e9dbe6fc3df68b1c4e16241c51"
STRICT_INHERITANCE_SNAPSHOT_SHA256 = (
    "f3d74f06c9b114ce85768f647252db76edadc42a95ab6a6f29c05d69f39bea0e"
)
DELTA_POINTER_REGISTRY_SHA256: Mapping[str, tuple[int, str]] = {
    "allowed_v2_2_delta_json_pointers": (
        33,
        "d211dfddf55548dc1e06cb4d5165d52128a71082d9815425588f09afc44ece20",
    ),
    "bundle_schema_delta_domains": (
        33,
        "a0f779608a854c6967d92ea59cb6c10dd543cd9ed0cc4f8da73b6669a635c6c2",
    ),
    "release_schema_delta_domains": (
        24,
        "2fc5c920b07b11d4386d90a3007a8ecdac3a710b857b9b502e2290df5755752f",
    ),
}
INITIAL_SURFACE_REVIEW_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-preregistration-independent-review-20260811.json"
)
INITIAL_SURFACE_REVIEW_SHA256 = "6707e2b3c0b2ba87712e88b59ceaed17524be2de947b764a94c8b170b2a30bb6"
INITIAL_SURFACE_REVIEW_COMMIT = "b21e1bdbf865dfd9c7605ecc7794fc3f8701ed1f"
VOID_EPOCH_ONE_IMPLEMENTATION_COMMIT = "fae44154d3504017742934ff3b3961642c35eb65"
VOID_EPOCH_ONE_IMPLEMENTATION_PARENT = "cf10ef8d636049b0fc206c8698a809be3090e1d7"
VOID_EPOCH_ONE_LANDING_COMMIT = "be7a2cedff1ad4bf523d88d83fa333126d502720"
VOID_EPOCH_ONE_REVIEW_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-remediation-independent-review-20260812.json"
)
VOID_EPOCH_ONE_REVIEW_SHA256 = (
    "e348bbc6c2976d473bf2b8e5b280784fd45ff7ae1ba7d7a4119309eb178b16cf"
)
VOID_EPOCH_ONE_REVIEW_COMMIT = "16f3e700c2ca9da997c8c0180e8b780aeae93346"
VOID_EPOCH_ONE_ADJUDICATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-attempt1-adjudication-and-epoch3-companion-20260813.json"
)
VOID_EPOCH_ONE_ADJUDICATION_SHA256 = (
    "aef641f0624ffa5ec8f722356b10c9e3fe0424edd93969f3251eecffac176521"
)
VOID_EPOCH_ONE_ADJUDICATION_COMMIT = "7fc122f575801ff43d2446a2c59491a086735e93"
VOID_EPOCH_ONE_RULING = (
    "Epoch 1 is recorded as ASSIGNED_AND_STRUCTURALLY_UNCONSUMABLE: its bytes "
    "were approved and carried forward into epoch 2 unchanged; it executed "
    "nothing and appears in no ledger record.",
    "The immutable history legitimately begins at epoch 2. The history is never "
    "rewritten or renumbered.",
    "Bundle construction and validation must accept a declared epoch origin and "
    "must disclose the void epoch 1 with its commit and reason in the final bundle, "
    "satisfying the preregistration's requirement that the bundle lists every epoch "
    "and the release acknowledges each.",
    "The numbering contract's intent, no gaps and no reuse among EXECUTED epochs, "
    "is preserved; only the assumption that execution starts at 1 is corrected.",
)
VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT = "d4fb0d8c763b9fa104949ea2ac58bc921d9e8889"
VOID_EPOCH_THREE_IMPLEMENTATION_PARENT = "d6c9c353217e00730457bf6b944ff26a32b8cf85"
VOID_EPOCH_THREE_OWNER_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-epoch3-surface-authority-20260813.json"
)
VOID_EPOCH_THREE_OWNER_SHA256 = (
    "44c2ab4e310da3f4b4a11efef8b9c20f73d231dc2b8f44dc535616cf18c646b3"
)
VOID_EPOCH_THREE_REVIEW_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-epoch3-r2-implementation-independent-review-20260814.json"
)
VOID_EPOCH_THREE_REVIEW_SHA256 = (
    "590d6b6b24bb6672956ea320a21458aa10523514ec384586029f09ef2cf757ef"
)
VOID_EPOCH_THREE_REVIEW_COMMIT = "bf9f610bda54523d69ecda0a72bf8fe89eaef78c"
VOID_EPOCH_THREE_LANDING_COMMIT = "006927080312b0e563e4ec3058b706455b33b70d"
VOID_EPOCH_THREE_LANDING_PARENT = "5791041e7eb48ffc6977752e381b10333bf53358"
VOID_EPOCH_THREE_CONTROL_ROOT_SHA256 = (
    "5ba2a3dc7f1512efd52e2654b4ac4a491c13218cfe6f92fda8db885be1d9ebbf"
)
VOID_EPOCH_THREE_CONTROL_RECORD_COUNT = 75
VOID_EPOCH_THREE_GATE_RULING_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-epoch3-gate-failure-adjudication-20260814.json"
)
VOID_EPOCH_THREE_GATE_RULING_SHA256 = (
    "29e476f1b2817bf8c6bb711230ba8ecb74f27d08648526f12073de3c8a1d067f"
)
VOID_EPOCH_THREE_GATE_RULING_COMMIT = "578a62551729e3bdc37ef6f2d2a9851fdf785dbd"
VOID_EPOCH_THREE_REANCHOR_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-epoch4-reanchor-companion-20260818.json"
)
VOID_EPOCH_THREE_REANCHOR_SHA256 = (
    "14a4eb277b3e3fb8ac181d432bdf4ee7821c339a25990991408b1759711fc546"
)
VOID_EPOCH_THREE_REANCHOR_COMMIT = "8b25d36033791efb4e800182150f4e7cae9ff597"
VOID_EPOCH_THREE_REASON_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-attempt2-adjudication-and-epoch5-direction-20260819.json"
)
VOID_EPOCH_THREE_REASON_SHA256 = (
    "e7e7615a404c216cfaf6ccba9a523cb46de48d82cd8a178c19612a1a38795180"
)
VOID_EPOCH_THREE_REASON_COMMIT = "0548692480ff8325b69be92f01e0d42e11ad4eb0"
VOID_EPOCH_THREE_SURFACE: Mapping[str, str] = {
    IMPLEMENTATION_RELATIVE.as_posix(): "M",
    VALIDATOR_RELATIVE.as_posix(): "M",
    RUNNER_TEST_RELATIVE.as_posix(): "M",
    VALIDATOR_TEST_RELATIVE.as_posix(): "M",
}
INCIDENT_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-1-one-shot-consumed-incident-20260811.json"
)
INCIDENT_SHA256 = "d658336f61cdca0239584b696043fe4abc5ede1ef7aff76a4fe514b7b5d0735c"
INCIDENT_COMMIT = "7a6e8be39f9a0702bf8fb4a22c669dc7331b0d95"
REMEDIATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-1-failure-remediation-review-request-20260811.json"
)
REMEDIATION_SHA256 = "820ed6c62a2e04a051d530bee7c33f5cfff21fd3fee25afd7587e18a407ce29f"
REMEDIATION_COMMIT = "530f2dc9f89360ad7c12776d85c3bf369f209214"
SCOPE_AUTHORIZATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-preregistration-scope-authorization-20260811.json"
)
SCOPE_AUTHORIZATION_SHA256 = "7cef82e5e4b2fcce349cbc25672705ea75795b0b07865970c415945747aa3296"
SCOPE_AUTHORIZATION_COMMIT = "5fe756401f20e67ff5c868bf29f099c1bfe5b4d3"
V2_1_IMPLEMENTATION_COMMIT = "4fce89e89fe2dba656694a7cffdc0ee1af0305c0"
V1_FAIL_CLOSE_COMMIT = "d710e885b49006eedf4f70ea09cb81fe15d176a3"
REHEARSAL_ID = "P4.2A-V2-HELDOUT-REHEARSAL-V2-2-DETERMINISTIC-20260811"
SERIES_POLICY = "DISCLOSED_REPEATABLE_SERIES_V1"
DESTINATION_RELATIVE = Path("docs/phase4/rehearsals/P4.2a-v2-calibration-v2-2")
BUNDLE_FILENAME = "bundle.json"
OFFICIAL_DESTINATION = REGISTERED_PROJECT_ROOT / DESTINATION_RELATIVE
OFFICIAL_SERIES_TOKEN = "35ba1b83a9b187817d7a591758e1c131e867fcd37917cba0ab196799fff832ef"
OFFICIAL_LEDGER_ROOT = REGISTERED_PROJECT_ROOT.parent / (
    ".alphapilot-p4-2a-v2-2-execution-claim-" + OFFICIAL_SERIES_TOKEN
)
OFFICIAL_SEALED_HISTORY_ROOT_SHA256 = (
    "a466de7b349882f2bcd556a4b4d00bf38bace9adb593b0e3b6296c415a8c9ca1"
)
OFFICIAL_SEALED_LIVE_LEDGER_ROOT_SHA256 = (
    "9aa7923239e687be2e17f91b8e0e8213d28ad2b08348efb1ae1457a4dddee6e6"
)
OFFICIAL_FAILED_EPOCH_TWO_IMPLEMENTATION_COMMIT = (
    "1b4e05c6acd513bb1bc11245911da97b6a128ca1"
)
OFFICIAL_FAILED_STARTED_SHA256 = (
    "a6a475abb6df4169d5b117283e2d313ee8088b527e9e8e57e9e127e7b56641be"
)
OFFICIAL_FAILED_TERMINAL_SHA256 = (
    "d922fb19dd55451ddf27bbf7749fa2fcc716b717a08d21b43d8d54cc9d03ede2"
)
OFFICIAL_FAILED_EVIDENCE_ROOT_SHA256 = (
    "deea0e81e3fd8a5c886cc4c757fb5485cb7f750718462489dea48d3deed2691c"
)
OFFICIAL_SELECTED_EPOCH_FOUR_IMPLEMENTATION_COMMIT = (
    "890e9002116c625d41f6aa037975df15d1546c56"
)
OFFICIAL_SELECTED_STARTED_SHA256 = (
    "75771a37572fb9191a9db26f986b1e9d89c26843556b502866322a8f4bdaf42d"
)
OFFICIAL_SELECTED_CANDIDATE_SHA256 = (
    "92652f963b04b79e29580978cd6857c2154df0b429ac09502be6c0c0c5d84da5"
)
OFFICIAL_SELECTED_TERMINAL_SHA256 = (
    "7ba4ed1b5d7e7abc462b312f08b131ff438cc524cecbdeea6b43dc199292e3dc"
)
OFFICIAL_SELECTED_CONTROL_ROOT_SHA256 = (
    "76076606d6e40cdd386b28cdd5bc40a8957693b8cfdc8b17a0a77410b4e082e8"
)
OFFICIAL_SELECTED_EVIDENCE_ROOT_SHA256 = (
    "f38b18b972f14a170fc9bb4129f25ec77e8ad1c4e8a8f137b5853cc371b694c2"
)
OFFICIAL_SELECTED_CANDIDATE_CONTENT_ROOT_SHA256 = (
    "5de4f74d1f73e5f90aa9c196c8fc6574bce2ecfa91abd750b22726c14c6a60b7"
)
OFFICIAL_SELECTED_RUN_ROOT_SHA256 = (
    "5fb8edf3aa65cdcd0f54b82bdf6f240104fa8537c1004e640671910115f8f314"
)
V2_1_DESTINATION = REGISTERED_PROJECT_ROOT / ("docs/phase4/rehearsals/P4.2a-v2-calibration-v2-1")
V2_1_EMPTY_CLAIM = REGISTERED_PROJECT_ROOT.parent / (
    ".alphapilot-p4-2a-v2-1-execution-claim-"
    "52378ddcda558a8489795c62a5c4d290687700801320508c03c51589c202e962"
)
PROTECTED_HELDOUT_ROOT = REGISTERED_PROJECT_ROOT / ("docs/phase4/eval/v2-calibration/heldout")
FIXED_PYTHON_LAUNCHER = REGISTERED_PROJECT_ROOT / ".venv/bin/python"
FIXED_PYTHON_SHA256 = "f4cd716d4b54f205398bec6932cc59361b087494ca2ddb157a5e8631d4d6f863"
FIXED_ORIG_ARGV_EXECUTABLE = Path(
    "/Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python"
)
FIXED_ORIG_ARGV_EXECUTABLE_SHA256 = (
    "89c717ced41f6a395612366e5b038226d0d8fca36bbddd9321d385f5f370ebbe"
)
ENVIRONMENT_MARKER = "ALPHAPILOT_P42A_REHEARSAL_V2_2_ENV_LOCKED"
EXACT_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "OPENBLAS_MAIN_FREE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "PYTHONNOUSERSITE": "1",
    "PYTHONPYCACHEPREFIX": "/dev/null",
    "PYTHONSAFEPATH": "1",
    "TZ": "UTC",
    "__CF_USER_TEXT_ENCODING": "0x1F5:0x0:0x0",
    "PATH": "/usr/bin:/bin",
    ENVIRONMENT_MARKER: "1",
}
FIXED_WALL_CLOCK = datetime(2026, 8, 10, 12, 30, tzinfo=UTC)
FIXED_WALL_CLOCK_TEXT = "2026-08-10T12:30:00Z"
MONOTONIC_INITIAL_SECONDS = 1000.0
UUID_NAMESPACE = uuid.UUID("4a8a9839-d0a6-509a-b193-ddf4b5700780")
FIXTURE_RAW_COUNT = 4048
FIXTURE_BY_SOURCE = {"cninfo": 2824, "akshare_ths": 1021, "sina_company_news": 203}
FIXTURE_ID_START = 9_000_001
CNINFO_REQUEST_COUNT = 2824
CNINFO_GAP_COUNT = 2823
PACKAGE_INVENTORY_SHA256 = "c3c7792eb31679c0eb7d3140e067d691df330cd3af302d2350bf15b74ac8ec42"
PACKAGE_ROOT_RELATIVE = Path(".venv/lib/python3.12/site-packages")
PACKAGE_ROOTS_SHA256 = "fae235892c0988d4093d1ad12b034a6126d116e436393e837a8b2f71601fbd12"
PYTHON_INVENTORY_SHA256 = "ab3e067417027bb98ea4335e9086d2046ac9dfd4eaf857acc8622dc8f0a13a31"
CONTROL_MANIFEST_SCHEMA = "p4.2a-v2-heldout-rehearsal-control-manifest-v2.2"
BUNDLE_SCHEMA_VERSION = "p4.2a-v2-heldout-rehearsal-bundle-v2.2"
CARRY_FORWARD_AUTHORITIES: Mapping[str, tuple[str, str, str]] = {
    "v2_1_preregistration": (
        "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-1-preregistration-20260810.json",
        "c303cfb13a42ecbb7e0acaec04de12a9e9169b89cf9e93ea79d0f120d1439d3e",
        "b302d5889f01296568340bcc15041cc554ceb2c7",
    ),
    "v2_1_prediction_timing_preregistration": (
        "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-1-"
        "prediction-timing-seam-preregistration-20260810.json",
        "1052c7a33268572fc794517844dae4b6c1ea504121712ad2f55ec814a7446f9a",
        "b3c2d2216c1feffd9949f181fa6766f8357ff683",
    ),
    "v2_1_frame_authority_ruling": (
        "docs/phase4/reports/P4.2a-rehearsal-v2-approval-and-frame-authority-ruling-20260810.json",
        "8605dd30dd6bffa9621b6efe5e01c4c9cead615c9639259c2e79e14fcbbc3421",
        "da374342781d6fde2f2c6d87d23582050bc8edaa",
    ),
    "v2_1_code_gate_authorization": (
        "docs/phase4/reports/P4.2a-successor-v2-1-code-gate-authorization-20260810.json",
        "e28db692dc150983f86f6760fb1a95584d8607658e8a78a0de35cf3fc81940cd",
        "aa082578aa48296f1dd394a380775a5a4546ca65",
    ),
    "v2_1_scope_correction_owner_ruling": (
        "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-1-scope-"
        "correction-owner-ruling-20260810.json",
        "36a3baea9ce5e4c28c7e6aff9e77c09691024a870513f49f2094b07963f3582e",
        "88690ef488925f9de922569f961ec4ff1a23bb78",
    ),
    "v2_1_registry_expansion_authorization": (
        "docs/phase4/reports/P4.2a-v2-1-control-plane-registry-expansion-"
        "authorization-20260811.json",
        "ab85a0ddd90728c7d41051e640b59f7dc777f2f2aec3c8290286206979251796",
        "d37040be87644977ddaad60b2590ac2e62b2aeed",
    ),
    "v2_1_independent_implementation_review": (
        "docs/phase4/reports/P4.2a-v2-1-implementation-independent-review-20260811.json",
        "d144f77d4e7a2946f00e618fb768960b0abdd6e40caf5831f4f198700762d276",
        "ed59a0ce6057145068b7c87fc681dd0aeea47270",
    ),
    "v2_1_consumed_attempt_incident": (
        "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-1-one-shot-"
        "consumed-incident-20260811.json",
        INCIDENT_SHA256,
        INCIDENT_COMMIT,
    ),
}
V2_1_IMPLEMENTATION_SURFACE: tuple[tuple[str, str], ...] = (
    ("M", "scripts/build_p4_2a_v2_heldout_adjudication_ui.py"),
    ("M", "scripts/evaluate_p4_2a_v2_heldout.py"),
    ("M", "scripts/finalize_p4_2a_v2_heldout_adjudication.py"),
    ("M", "scripts/prepare_p4_2a_v2_heldout.py"),
    ("A", "scripts/rehearse_p4_2a_v2_1_heldout_full_path.py"),
    ("M", "scripts/run_p4_2a_offline_extract.py"),
    ("M", "scripts/seal_p4_2a_v2_heldout_draft.py"),
    ("A", "scripts/validate_p4_2a_v2_1_heldout_rehearsal_bundle.py"),
    ("M", "tests/test_p4_2a_offline_extract.py"),
    ("A", "tests/test_p4_2a_v2_1_heldout_rehearsal_runner.py"),
    ("A", "tests/test_p4_2a_v2_1_heldout_rehearsal_validator.py"),
    ("M", "tests/test_p4_2a_v2_heldout.py"),
    ("M", "tests/test_p4_2a_v2_heldout_adjudication.py"),
    ("M", "tests/test_p4_2a_v2_heldout_evaluator.py"),
    ("M", "tests/test_p4_2a_v2_heldout_finalizer.py"),
)
CONTROL_GOVERNANCE_AUTHORITIES: Mapping[str, tuple[str, str, bool]] = {
    **{
        path: (digest, creating_commit, True)
        for path, digest, creating_commit in CARRY_FORWARD_AUTHORITIES.values()
    },
    INITIAL_SURFACE_REVIEW_RELATIVE.as_posix(): (
        INITIAL_SURFACE_REVIEW_SHA256,
        INITIAL_SURFACE_REVIEW_COMMIT,
        False,
    ),
}

ARTIFACT_INVENTORY: tuple[tuple[str, str], ...] = (
    (
        "materialized_inputs",
        "docs/phase4/eval/v2-calibration/heldout/materialization/candidate-inputs.jsonl",
    ),
    (
        "materialization_manifest",
        "docs/phase4/eval/v2-calibration/heldout/materialization/manifest.json",
    ),
    (
        "inference_state",
        "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-v2-inference.state.jsonl",
    ),
    ("predictions", "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-v2.predictions.jsonl"),
    (
        "prediction_manifest",
        "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-v2.predictions.manifest.json",
    ),
    (
        "private_selection",
        "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-frame-v2.selection.json",
    ),
    ("owner_blind", "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-frame-v2.blind.jsonl"),
    (
        "ai_draft",
        "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-frame-v2.labels-ai-drafted.jsonl",
    ),
    (
        "adjudication_ui",
        "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-frame-v2.adjudication.html",
    ),
    (
        "owner_export",
        "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-frame-v2.owner-export.jsonl",
    ),
    (
        "human_adjudicated",
        "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-frame-v2.human-adjudicated.jsonl",
    ),
    (
        "owner_completion",
        "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-frame-v2.owner-completion.json",
    ),
    (
        "evaluation_state",
        "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-v2-evaluation.state.jsonl",
    ),
    (
        "synthetic_report",
        "docs/phase4/eval/v2-calibration/heldout/report/P4.2a-heldout-v2-evaluation-result.json",
    ),
)

HISTORY_EMPTY_PREFIX = b"p4.2a-rehearsal-v2.2-history-empty-v1\0"
ATTEMPT_TOKEN_PREFIX = b"p4.2a-rehearsal-v2.2-attempt-v1\0"
ATTEMPT_RECORD_PREFIX = b"p4.2a-rehearsal-v2.2-attempt-record-v1\0"
HISTORY_STEP_PREFIX = b"p4.2a-rehearsal-v2.2-history-step-v1\0"
LEDGER_LEAF_PREFIX = b"p4.2a-rehearsal-v2.2-ledger-leaf-v1\0"
EVIDENCE_EMPTY_PREFIX = b"p4.2a-rehearsal-v2.2-evidence-empty-v1\0"
EVIDENCE_LEAF_PREFIX = b"p4.2a-rehearsal-v2.2-evidence-leaf-v1\0"
EVIDENCE_NODE_PREFIX = b"p4.2a-rehearsal-v2.2-evidence-node-v1\0"
MERKLE_LEAF_PREFIX = b"p4.2a-rehearsal-leaf-v2.2\0"
MERKLE_NODE_PREFIX = b"p4.2a-rehearsal-node-v2.2\0"
BUNDLE_ROOT_PREFIX = b"p4.2a-rehearsal-bundle-v2.2\0"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _reject_constant(value: str, *, source: str) -> NoReturn:
    raise RehearsalV22Error(f"{source} contains forbidden numeric constant {value}")


def strict_json_loads(payload: bytes | str, *, source: str = "JSON") -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise RehearsalV22Error(f"{source} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            payload,
            object_pairs_hook=unique,
            parse_constant=lambda value: _reject_constant(value, source=source),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RehearsalV22Error(f"{source} is not strict JSON") from exc


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise RehearsalV22Error(f"{label} must be an object")
    return cast(JsonObject, value)


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RehearsalV22Error(f"{label} must be an array")
    return value


def _lower_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_exact_int(value: object, expected: int | None = None) -> bool:
    return type(value) is int and (expected is None or value == expected)


def _is_exact_bool_mapping(value: Mapping[str, object]) -> bool:
    return all(type(item) is bool for item in value.values())


def _relative_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii():
        raise RehearsalV22Error(f"{label} must be one nonempty ASCII relative path")
    if re.fullmatch(r"[A-Za-z0-9._/-]+", value) is None:
        raise RehearsalV22Error(f"{label} contains forbidden characters")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RehearsalV22Error(f"{label} escapes its authority root")
    return path.as_posix()


def _safe_path(root: Path, relative: object, label: str) -> Path:
    text = _relative_text(relative, label)
    path = root.joinpath(*PurePosixPath(text).parts).absolute()
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise RehearsalV22Error(f"{label} cannot be resolved") from exc
    if resolved != path or not path.is_relative_to(root.absolute()):
        raise RehearsalV22Error(f"{label} is aliased or escapes")
    return path


def _regular_bytes(path: Path, label: str, *, allow_zero: bool = True) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RehearsalV22Error(f"{label} is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or path.resolve(strict=True) != path.absolute()
        or (not allow_zero and metadata.st_size == 0)
    ):
        raise RehearsalV22Error(f"{label} is not one unaliased regular file")
    return path.read_bytes()


def _fixed_launcher_bytes() -> bytes:
    """Read the frozen venv launcher while preserving its intentional symlink path."""

    try:
        launcher_metadata = FIXED_PYTHON_LAUNCHER.lstat()
        resolved = FIXED_PYTHON_LAUNCHER.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except OSError as exc:
        raise RehearsalV22Error("fixed Python launcher is unavailable") from exc
    if (
        not (
            stat.S_ISLNK(launcher_metadata.st_mode)
            or stat.S_ISREG(launcher_metadata.st_mode)
        )
        or resolved.is_symlink()
        or not stat.S_ISREG(resolved_metadata.st_mode)
        or resolved_metadata.st_nlink != 1
    ):
        raise RehearsalV22Error(
            "fixed Python launcher chain is not one regular executable"
        )
    return FIXED_PYTHON_LAUNCHER.read_bytes()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    try:
        parent_metadata = path.parent.lstat()
    except OSError as exc:
        raise RehearsalV22Error("create-only parent directory is unavailable") from exc
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or path.parent.resolve(strict=True) != path.parent.absolute()
    ):
        raise RehearsalV22Error("create-only parent directory is aliased")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    else:
        os.close(descriptor)
    if stat.S_IMODE(path.lstat().st_mode) != mode:
        raise RehearsalV22Error("create-only file mode was changed by the process umask")
    _fsync_directory(path.parent)


def _tree_fingerprint(root: Path) -> dict[str, str]:
    if not os.path.lexists(root):
        return {".": "absent"}
    result: dict[str, str] = {}
    root_metadata = root.lstat()
    if root.is_symlink():
        return {".": f"symlink:{os.readlink(root)}"}
    if root.is_file():
        return {".": f"file:{_sha256(root.read_bytes())}:{root_metadata.st_mode:o}"}
    if not root.is_dir():
        return {".": f"special:{root_metadata.st_mode:o}"}
    result["."] = f"directory:{stat.S_IMODE(root_metadata.st_mode):04o}"
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().encode()
    ):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if path.is_symlink():
            result[relative] = f"symlink:{os.readlink(path)}"
        elif path.is_file():
            result[relative] = (
                f"file:{_sha256(path.read_bytes())}:{stat.S_IMODE(metadata.st_mode):04o}:"
                f"{metadata.st_nlink}"
            )
        elif path.is_dir():
            result[relative] = f"directory:{stat.S_IMODE(metadata.st_mode):04o}"
        else:
            result[relative] = f"special:{metadata.st_mode:o}"
    return result


@dataclass(frozen=True)
class ExecutionBinding:
    mode: ExecutionMode
    project_root: Path
    shim_path: Path
    action_authorization_path: Path
    destination: Path
    series_token_sha256: str
    ledger_root: Path


@dataclass(frozen=True)
class ModuleIdentityObservation:
    module_name: str
    module_object_id: int
    audit_policy_object_id: int
    temp_authority_object_id: int
    module_origin: Path


@dataclass(frozen=True)
class AuthorityReference:
    path: str
    sha256: str
    creating_commit: str
    unique_a_history_verified: bool = True

    def as_json(self) -> JsonObject:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "creating_commit": self.creating_commit,
            "unique_a_history_verified": self.unique_a_history_verified,
        }


@dataclass(frozen=True)
class ImplementationEpochValidation:
    epoch: int
    implementation_commit: str
    owner_surface_authorization: AuthorityReference
    independent_implementation_review: AuthorityReference
    control_merkle_root_sha256: str


@dataclass(frozen=True)
class ActionAuthorization:
    path: Path
    payload: bytes
    sha256: str
    creating_commit: str
    ordinal: int
    previous_history_root_sha256: str
    implementation_epoch: int
    implementation_commit: str
    owner_surface_authorization: AuthorityReference
    independent_implementation_review: AuthorityReference
    control_merkle_root_sha256: str
    exact_argv: tuple[str, ...]
    command_sha256: str
    exact_environment: Mapping[str, str]
    environment_sha256: str

    def authority_ref(self, project_root: Path) -> AuthorityReference:
        return AuthorityReference(
            path=self.path.relative_to(project_root).as_posix(),
            sha256=self.sha256,
            creating_commit=self.creating_commit,
        )


@dataclass(frozen=True)
class _BootstrapEvidence:
    _nonce: object
    project_root: Path
    shim_path: Path
    argv: tuple[str, ...]
    orig_argv: tuple[str, ...]
    environment: Mapping[str, str]


@dataclass(frozen=True)
class _DisposableCapability:
    _nonce: object
    binding: ExecutionBinding
    bootstrap: _BootstrapEvidence
    action_authorization: ActionAuthorization
    real_path_fingerprints: Mapping[str, Mapping[str, str]]
    boundary_ids: tuple[int, ...]


@dataclass(frozen=True)
class _OfficialExecutionCapability:
    _nonce: object
    binding: ExecutionBinding
    bootstrap: _BootstrapEvidence
    action_authorization: ActionAuthorization


ExecutionCapability = _DisposableCapability | _OfficialExecutionCapability


@dataclass(frozen=True)
class _ReplayCapability:
    _nonce: object
    binding: ExecutionBinding
    validator_module: ModuleType
    validator_module_id: int
    bundle_path: Path
    bundle_sha256: str
    implementation_commit: str
    history_root_sha256: str
    control_merkle_root_sha256: str
    real_path_fingerprints: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True)
class ValidatorDelegation:
    _nonce: object
    binding: ExecutionBinding
    capability_id: int
    validator_module_id: int
    audit_policy_id: int
    temp_authority: Path
    creator_module_id: int
    lifetime_id: int


@dataclass(frozen=True)
class _DelegationRecord:
    token: ValidatorDelegation
    capability: ExecutionCapability
    validator_module: ModuleType
    policy: _AuditPolicy


@dataclass(frozen=True)
class _ReplayObservation:
    _nonce: object
    capability_id: int
    audit_policy_id: int
    temp_authority: Path
    lifetime_id: int


@dataclass(frozen=True)
class _ReplayObservationRecord:
    token: _ReplayObservation
    capability: ExecutionCapability
    policy: _AuditPolicy
    labels: tuple[str, ...]


def _build_authority_state() -> tuple[Any, ...]:
    """Keep all issuance material in one non-exported closure.

    A copied nonce is insufficient: every consume also requires the exact issued
    object, a live issuance record, the current package singleton, the original
    locked OS bootstrap, and an active audit/temporary-authority lifetime.
    """

    bootstrap_nonce = object()
    capability_nonce = object()
    delegation_nonce = object()
    replay_nonce = object()
    replay_observation_nonce = object()
    # These registries are rebound immutable tuples.  ``inspect.getclosurevars``
    # can reveal only a stale tuple snapshot; it cannot insert an issuance
    # record into the live closure cell as it could with a mutable mapping.
    bootstrap_registry: tuple[_BootstrapEvidence, ...] = ()
    capability_registry: tuple[ExecutionCapability, ...] = ()
    delegation_registry: tuple[_DelegationRecord, ...] = ()
    replay_registry: tuple[_ReplayCapability, ...] = ()
    replay_observation_registry: tuple[_ReplayObservationRecord, ...] = ()

    def validate_active_scope(binding: ExecutionBinding) -> tuple[_AuditPolicy, Path]:
        module = sys.modules.get(MODULE_NAME)
        policy = _AUDIT_POLICY.get()
        authority = _TEMP_AUTHORITY.get()
        if (
            module is not sys.modules.get(__name__)
            or policy is None
            or authority is None
            or policy.project_root != binding.project_root
            or not _path_in_roots(authority, policy.write_roots)
        ):
            raise RehearsalV22Error(
                "v2.2 authority is outside its active package/audit/root lifetime"
            )
        return policy, authority

    def revalidate_capability_action(
        capability: ExecutionCapability,
    ) -> None:
        binding = capability.binding
        authorization = capability.action_authorization
        history = validate_live_history(binding)
        if authorization.ordinal == len(history.records) + 1:
            if history.series_closed:
                raise RehearsalV22Error(
                    "capability action cannot start after the selected candidate"
                )
            expected_previous = history.history_root_sha256
        elif 1 <= authorization.ordinal <= len(history.records):
            record = history.records[authorization.ordinal - 1]
            if record.owner_action_time_authorization != authorization.authority_ref(
                binding.project_root
            ):
                raise RehearsalV22Error("capability action differs from its live attempt record")
            expected_previous = record.previous_history_root_sha256
        else:
            raise RehearsalV22Error("capability action ordinal is not live or next")
        observed = _validate_action_authorization(
            binding,
            authorization.authority_ref(binding.project_root),
            expected_ordinal=authorization.ordinal,
            expected_previous_history_root_sha256=expected_previous,
            require_current_process=True,
        )
        if observed != authorization:
            raise RehearsalV22Error(
                "capability action bytes or semantic binding drifted after mint"
            )

    def validate_bootstrap_evidence(value: _BootstrapEvidence) -> None:
        if (
            not isinstance(value, _BootstrapEvidence)
            or value._nonce is not bootstrap_nonce
            or not any(record is value for record in bootstrap_registry)
            or value.project_root / SHIM_RELATIVE != value.shim_path
            or tuple(sys.orig_argv) != value.orig_argv
            or tuple(sys.argv) != value.argv
            or dict(os.environ) != dict(value.environment)
            or sys.modules.get(MODULE_NAME) is not sys.modules.get(__name__)
        ):
            raise RehearsalV22Error("v2.2 bootstrap evidence is forged, stolen, or stale")
        _assert_locked_runner_bootstrap(value.project_root)

    @contextmanager
    def bootstrap_evidence_scope(
        *,
        project_root: Path,
        shim_path: Path,
        argv: tuple[str, ...],
        orig_argv: tuple[str, ...],
        environment: Mapping[str, str],
    ) -> Iterator[_BootstrapEvidence]:
        nonlocal bootstrap_registry
        value = _BootstrapEvidence(
            _nonce=bootstrap_nonce,
            project_root=project_root,
            shim_path=shim_path,
            argv=argv,
            orig_argv=orig_argv,
            environment=dict(environment),
        )
        bootstrap_registry = (*bootstrap_registry, value)
        try:
            yield value
        finally:
            bootstrap_registry = tuple(
                record for record in bootstrap_registry if record is not value
            )

    def validate_disposable_capability(
        value: _DisposableCapability,
        *,
        project_root: Path,
    ) -> ExecutionBinding:
        if (
            not isinstance(value, _DisposableCapability)
            or value._nonce is not capability_nonce
            or not any(record is value for record in capability_registry)
            or value.binding.mode != "DISPOSABLE_FULL_SHAPE_TEST"
            or value.binding.project_root != project_root.absolute()
        ):
            raise RehearsalV22Error("disposable v2.2 capability is forged or cross-root")
        validate_bootstrap_evidence(value.bootstrap)
        validate_active_scope(value.binding)
        expected = _derive_binding_unchecked(
            project_root,
            action_authorization_path=value.action_authorization.path,
        )
        if expected != value.binding:
            raise RehearsalV22Error("disposable v2.2 binding drifted after capability mint")
        if _real_path_fingerprints() != value.real_path_fingerprints:
            raise RehearsalV22Error("registered paths changed during disposable execution")
        if value.boundary_ids != _fake_boundary_ids():
            raise RehearsalV22Error("disposable fake boundary identity drifted")
        revalidate_capability_action(value)
        return value.binding

    def validate_execution_capability(
        value: ExecutionCapability,
        *,
        project_root: Path,
    ) -> ExecutionBinding:
        if isinstance(value, _DisposableCapability):
            return validate_disposable_capability(value, project_root=project_root)
        if (
            not isinstance(value, _OfficialExecutionCapability)
            or value._nonce is not capability_nonce
            or not any(record is value for record in capability_registry)
            or value.binding.mode != "REGISTERED_OFFICIAL"
            or value.binding.project_root != project_root.absolute()
        ):
            raise RehearsalV22Error("official v2.2 execution capability is forged or stale")
        validate_bootstrap_evidence(value.bootstrap)
        validate_active_scope(value.binding)
        expected = _derive_binding_unchecked(
            project_root,
            action_authorization_path=value.action_authorization.path,
        )
        if expected != value.binding:
            raise RehearsalV22Error("official v2.2 execution binding drifted")
        revalidate_capability_action(value)
        return value.binding

    @contextmanager
    def execution_capability_scope(
        *,
        binding: ExecutionBinding,
        bootstrap: _BootstrapEvidence,
        action_authorization: ActionAuthorization,
        real_path_fingerprints: Mapping[str, Mapping[str, str]] | None = None,
        boundary_ids: tuple[int, ...] | None = None,
    ) -> Iterator[ExecutionCapability]:
        nonlocal capability_registry
        validate_bootstrap_evidence(bootstrap)
        validate_active_scope(binding)
        if binding.mode == "DISPOSABLE_FULL_SHAPE_TEST":
            if real_path_fingerprints is None or boundary_ids is None:
                raise RehearsalV22Error("disposable capability lacks boundary evidence")
            capability: ExecutionCapability = _DisposableCapability(
                _nonce=capability_nonce,
                binding=binding,
                bootstrap=bootstrap,
                action_authorization=action_authorization,
                real_path_fingerprints=dict(real_path_fingerprints),
                boundary_ids=boundary_ids,
            )
        else:
            if real_path_fingerprints is not None or boundary_ids is not None:
                raise RehearsalV22Error("official capability rejects disposable evidence")
            capability = _OfficialExecutionCapability(
                _nonce=capability_nonce,
                binding=binding,
                bootstrap=bootstrap,
                action_authorization=action_authorization,
            )
        capability_registry = (*capability_registry, capability)
        try:
            yield capability
        finally:
            capability_registry = tuple(
                record for record in capability_registry if record is not capability
            )

    @contextmanager
    def borrow_validator_authority(
        execution_context: ExecutionCapability,
        *,
        validator_module: ModuleType,
    ) -> Iterator[ValidatorDelegation]:
        nonlocal delegation_registry
        binding = validate_execution_capability(
            execution_context,
            project_root=execution_context.binding.project_root,
        )
        policy, authority = validate_active_scope(binding)
        if (
            validator_module.__name__ != "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
            or sys.modules.get(validator_module.__name__) is not validator_module
        ):
            raise RehearsalV22Error("validator delegation lacks exact module authority")
        token = ValidatorDelegation(
            _nonce=delegation_nonce,
            binding=binding,
            capability_id=id(execution_context),
            validator_module_id=id(validator_module),
            audit_policy_id=id(policy),
            temp_authority=authority,
            creator_module_id=id(sys.modules[MODULE_NAME]),
            lifetime_id=id(object()),
        )
        record = _DelegationRecord(
            token=token,
            capability=execution_context,
            validator_module=validator_module,
            policy=policy,
        )
        delegation_registry = (*delegation_registry, record)
        try:
            yield token
        finally:
            delegation_registry = tuple(
                active for active in delegation_registry if active is not record
            )

    def validate_validator_delegation(
        value: ValidatorDelegation,
        *,
        execution_context: ExecutionCapability,
        validator_module: ModuleType,
        project_root: Path,
    ) -> ExecutionBinding:
        binding = validate_execution_capability(
            execution_context,
            project_root=project_root,
        )
        policy, authority = validate_active_scope(binding)
        matches = [record for record in delegation_registry if record.token is value]
        record = matches[0] if len(matches) == 1 else None
        if (
            not isinstance(value, ValidatorDelegation)
            or value._nonce is not delegation_nonce
            or record is None
            or record.token is not value
            or record.capability is not execution_context
            or record.validator_module is not validator_module
            or record.policy is not policy
            or value.binding != binding
            or value.capability_id != id(execution_context)
            or value.validator_module_id != id(validator_module)
            or value.audit_policy_id != id(policy)
            or value.temp_authority != authority
            or value.creator_module_id != id(sys.modules[MODULE_NAME])
        ):
            raise RehearsalV22Error(
                "validator delegation is forged, stolen, cross-scope, or expired"
            )
        return binding

    def active_validator_execution_context(
        *,
        binding: ExecutionBinding,
        validator_module: ModuleType,
    ) -> ExecutionCapability:
        """Recover only the live capability already lent to the package validator.

        This does not mint authority.  It is the non-serialized bridge used by
        the official runner when the independent validator actively replays the
        staged candidate before publication.
        """

        policy, authority = validate_active_scope(binding)
        if (
            validator_module.__name__ != "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
            or sys.modules.get(validator_module.__name__) is not validator_module
        ):
            raise RehearsalV22Error("active validator authority has split module identity")
        matches = [
            record
            for record in delegation_registry
            if record.validator_module is validator_module
            and record.policy is policy
            and record.token.binding == binding
            and record.token.audit_policy_id == id(policy)
            and record.token.temp_authority == authority
        ]
        if len(matches) != 1:
            raise RehearsalV22Error("active validator authority is absent or ambiguous")
        record = matches[0]
        observed = validate_execution_capability(
            record.capability,
            project_root=binding.project_root,
        )
        validate_validator_delegation(
            record.token,
            execution_context=record.capability,
            validator_module=validator_module,
            project_root=binding.project_root,
        )
        if observed != binding:
            raise RehearsalV22Error("active validator authority binding drifted")
        return record.capability

    def validate_replay_capability(value: _ReplayCapability) -> ExecutionBinding:
        if (
            not isinstance(value, _ReplayCapability)
            or value._nonce is not replay_nonce
            or not any(record is value for record in replay_registry)
            or value.binding.mode != "REGISTERED_OFFICIAL"
        ):
            raise RehearsalV22Error("official validator replay capability is forged or stale")
        validator_module = value.validator_module
        if (
            id(validator_module) != value.validator_module_id
            or validator_module.__name__ != "__main__"
            or sys.modules.get("__main__") is not validator_module
            or sys.modules.get("scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle") is not None
        ):
            raise RehearsalV22Error("official validator replay module identity drifted")
        _assert_locked_validator_bootstrap(value.binding.project_root)
        validate_active_scope(value.binding)
        if (
            _sha256(_regular_bytes(value.bundle_path, "replay-bound bundle")) != value.bundle_sha256
            or validate_live_history(value.binding).history_root_sha256 != value.history_root_sha256
            or _real_path_fingerprints() != value.real_path_fingerprints
        ):
            raise RehearsalV22Error("official validator replay evidence drifted")
        return value.binding

    @contextmanager
    def replay_observation_scope(
        execution_context: ExecutionCapability,
    ) -> Iterator[Callable[[], tuple[str, ...]]]:
        """Observe release-validator active replays without minting authority."""

        nonlocal replay_observation_registry
        binding = validate_execution_capability(
            execution_context,
            project_root=execution_context.binding.project_root,
        )
        policy, authority = validate_active_scope(binding)
        if replay_observation_registry:
            raise RehearsalV22Error("active replay observation scope is ambiguous")
        token = _ReplayObservation(
            _nonce=replay_observation_nonce,
            capability_id=id(execution_context),
            audit_policy_id=id(policy),
            temp_authority=authority,
            lifetime_id=id(object()),
        )
        record = _ReplayObservationRecord(
            token=token,
            capability=execution_context,
            policy=policy,
            labels=(),
        )
        replay_observation_registry = (*replay_observation_registry, record)

        def snapshot() -> tuple[str, ...]:
            matches = [
                active
                for active in replay_observation_registry
                if active.token is token
            ]
            if (
                len(matches) != 1
                or token._nonce is not replay_observation_nonce
                or matches[0].capability is not execution_context
                or matches[0].policy is not policy
                or token.capability_id != id(execution_context)
                or token.audit_policy_id != id(policy)
                or token.temp_authority != authority
            ):
                raise RehearsalV22Error("active replay observation is forged or stale")
            validate_execution_capability(
                execution_context,
                project_root=binding.project_root,
            )
            return matches[0].labels

        try:
            yield snapshot
        finally:
            replay_observation_registry = tuple(
                active
                for active in replay_observation_registry
                if active.token is not token
            )

    def record_replay_observation(
        execution_context: ExecutionCapability,
        run_label: str,
    ) -> None:
        nonlocal replay_observation_registry
        if not replay_observation_registry:
            return
        policy = _AUDIT_POLICY.get()
        authority = _TEMP_AUTHORITY.get()
        matches = [
            active
            for active in replay_observation_registry
            if active.capability is execution_context
            and active.policy is policy
            and active.token.capability_id == id(execution_context)
            and active.token.audit_policy_id == id(policy)
            and active.token.temp_authority == authority
        ]
        if len(matches) != 1 or run_label in matches[0].labels:
            raise RehearsalV22Error(
                "active replay observation is missing, ambiguous, or duplicated"
            )
        observed = validate_execution_capability(
            execution_context,
            project_root=execution_context.binding.project_root,
        )
        if observed != execution_context.binding:
            raise RehearsalV22Error("active replay observation binding drifted")
        target = matches[0]
        replacement = replace(target, labels=(*target.labels, run_label))
        replay_observation_registry = tuple(
            replacement if active is target else active
            for active in replay_observation_registry
        )

    @contextmanager
    def replay_capability_scope(
        *,
        binding: ExecutionBinding,
        validator_module: ModuleType,
        bundle_path: Path,
        implementation_commit: str,
        history_root_sha256: str,
        control_merkle_root_sha256: str,
        real_path_fingerprints: Mapping[str, Mapping[str, str]],
    ) -> Iterator[_ReplayCapability]:
        nonlocal replay_registry
        if (
            binding.mode != "REGISTERED_OFFICIAL"
            or validator_module.__name__ != "__main__"
            or sys.modules.get("__main__") is not validator_module
            or sys.modules.get("scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle") is not None
            or Path(cast(str, getattr(validator_module, "__file__", ""))).resolve(strict=True)
            != binding.project_root / VALIDATOR_RELATIVE
        ):
            raise RehearsalV22Error("official replay issuance lacks validator identity")
        _assert_locked_validator_bootstrap(binding.project_root)
        validate_active_scope(binding)
        value = _ReplayCapability(
            _nonce=replay_nonce,
            binding=binding,
            validator_module=validator_module,
            validator_module_id=id(validator_module),
            bundle_path=bundle_path,
            bundle_sha256=_sha256(_regular_bytes(bundle_path, "official replay bundle")),
            implementation_commit=implementation_commit,
            history_root_sha256=history_root_sha256,
            control_merkle_root_sha256=control_merkle_root_sha256,
            real_path_fingerprints=dict(real_path_fingerprints),
        )
        replay_registry = (*replay_registry, value)
        try:
            yield value
        finally:
            replay_registry = tuple(record for record in replay_registry if record is not value)

    return (
        bootstrap_evidence_scope,
        execution_capability_scope,
        validate_bootstrap_evidence,
        validate_disposable_capability,
        validate_execution_capability,
        borrow_validator_authority,
        validate_validator_delegation,
        active_validator_execution_context,
        replay_capability_scope,
        validate_replay_capability,
        replay_observation_scope,
        record_replay_observation,
    )


(
    _bootstrap_evidence_scope,
    _execution_capability_scope,
    _validate_bootstrap_evidence,
    _validate_disposable_capability,
    _validate_execution_capability,
    _borrow_validator_authority,
    _validate_validator_delegation,
    _active_validator_execution_context,
    _replay_capability_scope,
    _validate_replay_capability,
    _replay_observation_scope,
    _record_replay_observation,
) = _build_authority_state()


def _module_identity_observation() -> ModuleIdentityObservation:
    module = sys.modules.get(MODULE_NAME)
    if module is not sys.modules.get(__name__) or not isinstance(module, ModuleType):
        raise RehearsalV22Error("v2.2 package implementation module identity split")
    origin = getattr(module, "__file__", None)
    if not isinstance(origin, str):
        raise RehearsalV22Error("v2.2 package implementation has no source origin")
    return ModuleIdentityObservation(
        module_name=MODULE_NAME,
        module_object_id=id(module),
        audit_policy_object_id=id(_AUDIT_POLICY),
        temp_authority_object_id=id(_TEMP_AUTHORITY),
        module_origin=Path(origin).resolve(strict=True),
    )


def _series_token(destination: Path) -> str:
    material = (
        INCIDENT_SHA256.lower() + "\0" + REHEARSAL_ID + "\0" + destination.absolute().as_posix()
    ).encode("utf-8")
    return _sha256(material)


def _derive_binding_unchecked(
    project_root: Path,
    *,
    action_authorization_path: Path,
) -> ExecutionBinding:
    root = project_root.absolute()
    if root.resolve(strict=True) != root:
        raise RehearsalV22Error("v2.2 project root is aliased")
    shim_path = root / SHIM_RELATIVE
    destination = root / DESTINATION_RELATIVE
    token = _series_token(destination)
    ledger = root.parent / f".alphapilot-p4-2a-v2-2-execution-claim-{token}"
    if root == REGISTERED_PROJECT_ROOT:
        mode: ExecutionMode = "REGISTERED_OFFICIAL"
        if (
            destination != OFFICIAL_DESTINATION
            or token != OFFICIAL_SERIES_TOKEN
            or ledger != OFFICIAL_LEDGER_ROOT
        ):
            raise RehearsalV22Error("official v2.2 execution binding derivation drifted")
    else:
        mode = "DISPOSABLE_FULL_SHAPE_TEST"
        canonical = REGISTERED_PROJECT_ROOT
        if (
            root.is_relative_to(canonical)
            or canonical.is_relative_to(root)
            or destination == OFFICIAL_DESTINATION
            or ledger == OFFICIAL_LEDGER_ROOT
        ):
            raise RehearsalV22Error("disposable v2.2 project root overlaps registered authority")
    return ExecutionBinding(
        mode=mode,
        project_root=root,
        shim_path=shim_path,
        action_authorization_path=action_authorization_path.absolute(),
        destination=destination,
        series_token_sha256=token,
        ledger_root=ledger,
    )


def derive_execution_binding(
    project_root: Path = REGISTERED_PROJECT_ROOT,
    *,
    execution_context: _DisposableCapability | None = None,
) -> ExecutionBinding:
    """Derive official binding, or return a fully revalidated private rebase."""

    root = project_root.absolute()
    if root == REGISTERED_PROJECT_ROOT:
        if execution_context is not None:
            raise RehearsalV22Error("official binding rejects disposable capability")
        placeholder = root / (
            "docs/phase4/reports/P4.2a-v2-2-rehearsal-attempt-"
            "000001-execution-authorization-19700101.json"
        )
        return _derive_binding_unchecked(root, action_authorization_path=placeholder)
    if execution_context is None:
        raise RehearsalV22Error("noncanonical binding requires private rebase capability")
    return cast(
        ExecutionBinding,
        _validate_disposable_capability(execution_context, project_root=root),
    )


def _validate_git_metadata_authority(project_root: Path) -> None:
    root = project_root.absolute()
    try:
        if root.resolve(strict=True) != root:
            raise RehearsalV22Error("Git project root is aliased")
        git_directory = root / ".git"
        metadata = git_directory.lstat()
        if (
            git_directory.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or git_directory.resolve(strict=True) != git_directory
        ):
            raise RehearsalV22Error("Git metadata is not one unaliased directory")
        info = git_directory / "info"
        if os.path.lexists(info):
            info_metadata = info.lstat()
            if (
                info.is_symlink()
                or not stat.S_ISDIR(info_metadata.st_mode)
                or info.resolve(strict=True) != info
            ):
                raise RehearsalV22Error("Git info metadata is aliased")
        if os.path.lexists(info / "grafts"):
            raise RehearsalV22Error("legacy Git grafts are forbidden")
    except RehearsalV22Error:
        raise
    except OSError as exc:
        raise RehearsalV22Error("Git metadata authority is unavailable") from exc


def _git_completed(
    project_root: Path,
    *arguments: str,
    synthetic_identity: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    """Run one hardened Git operation; exposed for the independent validator."""

    root = project_root.absolute()
    _validate_git_metadata_authority(root)
    completed = subprocess.run(
        [
            "/usr/bin/git",
            *GIT_CONFIG_PREFIX,
            "-C",
            root.as_posix(),
            *arguments,
        ],
        check=False,
        capture_output=True,
        env=_git_environment(synthetic_identity=synthetic_identity),
    )
    if completed.returncode == 0 and completed.stderr:
        raise RehearsalV22Error("hardened Git operation emitted stderr")
    return completed


def _git_bytes(project_root: Path, *arguments: str) -> bytes:
    completed = _git_completed(project_root, *arguments)
    if completed.returncode != 0:
        raise RehearsalV22Error(f"hardened Git operation failed: {' '.join(arguments[:3])}")
    return completed.stdout


def _git_is_ancestor(project_root: Path, ancestor: str, descendant: str) -> bool:
    if not _lower_hex(ancestor, 40) or not _lower_hex(descendant, 40):
        raise RehearsalV22Error("Git ancestry argument is not one full lowercase commit")
    completed = _git_completed(
        project_root,
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
    )
    if completed.stderr or completed.returncode not in {0, 1}:
        raise RehearsalV22Error("hardened Git ancestry proof failed")
    return completed.returncode == 0


def _git_commit(project_root: Path, value: object, label: str) -> str:
    if not _lower_hex(value, 40):
        raise RehearsalV22Error(f"{label} is not one full lowercase Git commit")
    commit = cast(str, value)
    observed = (
        _git_bytes(
            project_root,
            "rev-parse",
            "--verify",
            f"{commit}^{{commit}}",
        )
        .decode("ascii", errors="strict")
        .strip()
    )
    if observed != commit:
        raise RehearsalV22Error(f"{label} does not identify one exact commit")
    return commit


def _git_blob(project_root: Path, commit: str, relative: str) -> bytes:
    _git_commit(project_root, commit, "blob commit")
    _relative_text(relative, "Git blob path")
    kind = _git_bytes(project_root, "cat-file", "-t", f"{commit}:{relative}").strip()
    payload = _git_bytes(project_root, "show", f"{commit}:{relative}")
    if kind != b"blob":
        raise RehearsalV22Error(f"Git object is not one blob: {relative}")
    return payload


def validate_implementation_blob(
    project_root: Path,
    implementation_commit: str,
    relative: str,
    *,
    require_current: bool = True,
) -> bytes:
    """Return one commit-bound implementation blob after current-byte equality."""

    root = project_root.absolute()
    payload = _git_blob(root, implementation_commit, relative)
    if require_current:
        current = _regular_bytes(
            _safe_path(root, relative, f"current implementation {relative}"),
            f"current implementation {relative}",
        )
        if current != payload:
            raise RehearsalV22Error(f"worktree differs from implementation commit: {relative}")
    return payload


def _authority_from_json(value: object, label: str) -> AuthorityReference:
    document = _object(value, label)
    if set(document) != {
        "path",
        "sha256",
        "creating_commit",
        "unique_a_history_verified",
    }:
        raise RehearsalV22Error(f"{label} does not have the exact authorityRef shape")
    path = _relative_text(document.get("path"), f"{label}.path")
    digest = document.get("sha256")
    commit = document.get("creating_commit")
    if (
        not _lower_hex(digest, 64)
        or not _lower_hex(commit, 40)
        or document.get("unique_a_history_verified") is not True
    ):
        raise RehearsalV22Error(f"{label} contains an invalid authority binding")
    return AuthorityReference(path, cast(str, digest), cast(str, commit))


def _unique_a_commit_for_path(
    project_root: Path,
    relative: str,
    *,
    execution_head: str,
) -> str:
    history = _git_bytes(
        project_root,
        "log",
        "--first-parent",
        "--diff-merges=first-parent",
        "--format=@@%H",
        "--name-status",
        "--find-renames",
        "--find-copies",
        execution_head,
        "--",
        relative,
    ).decode("utf-8", errors="strict")
    touches: list[tuple[str, str, tuple[str, ...]]] = []
    active: str | None = None
    for raw_line in history.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("@@"):
            active = line[2:]
            continue
        if active is None:
            raise RehearsalV22Error("unique-A Git history is malformed")
        fields = tuple(line.split("\t"))
        if len(fields) < 2:
            raise RehearsalV22Error("unique-A Git history is malformed")
        touches.append((active, fields[0], fields[1:]))
    if len(touches) != 1 or touches[0][1:] != ("A", (relative,)):
        raise RehearsalV22Error(f"authority path is not one unique status-A touch: {relative}")
    return _git_commit(project_root, touches[0][0], "authority creating commit")


def validate_unique_a_authority(
    project_root: Path,
    authority: AuthorityReference,
    *,
    execution_head: str,
    allow_initial_sibling: bool = False,
    require_current: bool = True,
) -> bytes:
    """Rehash a create-only authority and prove its unique status-A history."""

    root = project_root.absolute()
    head = _git_commit(root, execution_head, "authority execution head")
    if allow_initial_sibling:
        if authority != AuthorityReference(
            INITIAL_SURFACE_REVIEW_RELATIVE.as_posix(),
            INITIAL_SURFACE_REVIEW_SHA256,
            INITIAL_SURFACE_REVIEW_COMMIT,
        ):
            raise RehearsalV22Error("initial sibling exception is limited to b21 review")
        parents = (
            _git_bytes(
                root,
                "rev-list",
                "--parents",
                "-n",
                "1",
                INITIAL_SURFACE_REVIEW_COMMIT,
                "--",
            )
            .decode("ascii", errors="strict")
            .strip()
            .split()
        )
        if parents != [INITIAL_SURFACE_REVIEW_COMMIT, PREREGISTRATION_COMMIT]:
            raise RehearsalV22Error("initial sibling review topology drifted")
        diff = (
            _git_bytes(
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-status",
                "--no-renames",
                PREREGISTRATION_COMMIT,
                INITIAL_SURFACE_REVIEW_COMMIT,
                "--",
            )
            .decode("utf-8", errors="strict")
            .splitlines()
        )
        if diff != [f"A\t{INITIAL_SURFACE_REVIEW_RELATIVE.as_posix()}"]:
            raise RehearsalV22Error("initial sibling review commit surface drifted")
        if not _git_is_ancestor(root, INITIAL_SURFACE_REVIEW_COMMIT, head):
            raise RehearsalV22Error(
                "initial sibling review is absent from the execution-head lineage"
            )
    else:
        observed = _unique_a_commit_for_path(
            root,
            authority.path,
            execution_head=head,
        )
        if observed != authority.creating_commit:
            raise RehearsalV22Error("authority creating commit drifted")
        if not _git_is_ancestor(root, authority.creating_commit, head):
            raise RehearsalV22Error("authority commit is not on execution lineage")
    payload = _git_blob(root, authority.creating_commit, authority.path)
    if _sha256(payload) != authority.sha256:
        raise RehearsalV22Error("authority bytes differ from its SHA-256 binding")
    current_path = root.joinpath(*PurePosixPath(authority.path).parts)
    if require_current and os.path.lexists(current_path):
        current = _regular_bytes(current_path, f"current authority {authority.path}")
        if current != payload:
            raise RehearsalV22Error("current authority differs from unique creation blob")
    return payload


def _typed_json_equal(left: object, right: object) -> bool:
    """JSON equality that preserves CPython scalar types (notably int/float)."""

    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if not isinstance(right, dict) or set(left) != set(right):
            return False
        return all(_typed_json_equal(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return (
            isinstance(right, list)
            and len(left) == len(right)
            and all(_typed_json_equal(a, b) for a, b in zip(left, right, strict=True))
        )
    return left == right


def _json_pointer_parts(pointer: object, label: str) -> tuple[str, ...]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise RehearsalV22Error(f"{label} is not one absolute JSON Pointer")
    parts: list[str] = []
    for raw in pointer[1:].split("/"):
        index = 0
        decoded = ""
        while index < len(raw):
            if raw[index] != "~":
                decoded += raw[index]
                index += 1
                continue
            if index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}:
                raise RehearsalV22Error(f"{label} has invalid JSON Pointer escaping")
            decoded += "~" if raw[index + 1] == "0" else "/"
            index += 2
        parts.append(decoded)
    return tuple(parts)


def _json_pointer_value(document: object, pointer: object, label: str) -> object:
    current = document
    for part in _json_pointer_parts(pointer, label):
        if not isinstance(current, dict) or part not in current:
            raise RehearsalV22Error(f"{label} does not resolve")
        current = current[part]
    return current


def _delete_json_pointer_if_present(document: object, pointer: str) -> bool:
    parts = _json_pointer_parts(pointer, "schema delta pointer")
    current = document
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    if not isinstance(current, dict) or parts[-1] not in current:
        return False
    del current[parts[-1]]
    return True


def _typed_snapshot_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _frozen_delta_pointers(
    inheritance: Mapping[str, Any],
    key: str,
) -> tuple[str, ...]:
    raw = _array(inheritance.get(key), f"{key} registry")
    if any(not isinstance(pointer, str) for pointer in raw):
        raise RehearsalV22Error(f"{key} contains a non-string pointer")
    pointers = tuple(cast(list[str], raw))
    expected_count, expected_sha = DELTA_POINTER_REGISTRY_SHA256[key]
    if (
        len(pointers) != expected_count
        or len(pointers) != len(set(pointers))
        or _sha256(_typed_snapshot_bytes(list(pointers))) != expected_sha
    ):
        raise RehearsalV22Error(f"{key} differs from its independently frozen exact set")
    return pointers


def validate_strict_v2_1_inheritance(
    project_root: Path,
    *,
    implementation_commit: str | None = None,
    require_current: bool = True,
) -> JsonObject:
    """Independently rederive the frozen v2.1 projection and schema zero-diffs."""

    root = project_root.absolute()
    immutable_commit = (
        None
        if require_current
        else _git_commit(
            root,
            implementation_commit,
            "historical inheritance implementation commit",
        )
    )

    def inheritance_payload(relative: Path, label: str) -> bytes:
        if immutable_commit is None:
            return _regular_bytes(root / relative, label)
        return _git_blob(root, immutable_commit, relative.as_posix())

    prereg_payload = inheritance_payload(PREREGISTRATION_RELATIVE, "v2.2 preregistration")
    if _sha256(prereg_payload) != PREREGISTRATION_SHA256:
        raise RehearsalV22Error("v2.2 preregistration bytes drifted")
    prereg = _object(
        strict_json_loads(prereg_payload, source="v2.2 preregistration"),
        "v2.2 preregistration",
    )
    inheritance = _object(prereg.get("contract_inheritance"), "contract inheritance")
    source_contract = _object(
        inheritance.get("source_projection"),
        "inheritance source projection",
    )
    v2_1_relative = Path(
        _relative_text(source_contract.get("source_file"), "v2.1 preregistration")
    )
    v2_1_payload = inheritance_payload(v2_1_relative, "v2.1 preregistration")
    expected_base = CARRY_FORWARD_AUTHORITIES["v2_1_preregistration"]
    if _sha256(v2_1_payload) != expected_base[1]:
        raise RehearsalV22Error("v2.1 preregistration projection source drifted")
    v2_1 = _object(
        strict_json_loads(v2_1_payload, source="v2.1 preregistration"),
        "v2.1 preregistration",
    )
    exact_sections = _array(source_contract.get("exact_sections"), "projection exact sections")
    excluded = _array(
        source_contract.get("rehearsal_contract_excluded_keys"),
        "projection rehearsal exclusions",
    )
    target_map = _object(source_contract.get("target_key_map"), "projection target map")
    expected_sections = (
        "/frozen_inputs",
        "/request_interval_contract",
        "/materialization_manifest_amendment",
        "/runtime_start_policy",
        "/implementation_contract",
        "/bundle_and_release_effects",
        "/execution_safety",
        "/locks",
    )
    expected_excluded = (
        "registered_runner",
        "registered_validator",
        "official_execution_count",
        "domain_separated_merkle",
    )
    expected_map = {
        "/frozen_inputs": "frozen_inputs",
        "/request_interval_contract": "request_interval_contract",
        "/materialization_manifest_amendment": "materialization_manifest_amendment",
        "/runtime_start_policy": "runtime_start_policy",
        "/rehearsal_contract": "rehearsal_contract_non_delta",
        "/implementation_contract": "implementation_contract_historical_v2_1",
        "/bundle_and_release_effects": "bundle_and_release_effects_base_guarantees",
        "/execution_safety": "execution_safety",
        "/locks": "locks",
    }
    if (
        tuple(exact_sections) != expected_sections
        or source_contract.get("rehearsal_contract_source") != "/rehearsal_contract"
        or tuple(excluded) != expected_excluded
        or target_map != expected_map
    ):
        raise RehearsalV22Error("v2.1 source projection algorithm drifted")
    projection: JsonObject = {}
    for pointer in (*expected_sections, "/rehearsal_contract"):
        value = copy.deepcopy(_json_pointer_value(v2_1, pointer, f"projection {pointer}"))
        if pointer == "/rehearsal_contract":
            value_object = _object(value, "projected rehearsal contract")
            if any(key not in value_object for key in expected_excluded):
                raise RehearsalV22Error("v2.1 rehearsal exclusion key is absent")
            for key in expected_excluded:
                del value_object[key]
        projection[expected_map[pointer]] = value
    snapshot = _object(
        inheritance.get("strict_inheritance_snapshot"),
        "strict inheritance snapshot",
    )
    if not _typed_json_equal(projection, snapshot):
        raise RehearsalV22Error("v2.1 projected contract differs by value or JSON type")
    if (
        inheritance.get("strict_inheritance_snapshot_sha256") != STRICT_INHERITANCE_SNAPSHOT_SHA256
        or _sha256(_typed_snapshot_bytes(snapshot)) != STRICT_INHERITANCE_SNAPSHOT_SHA256
        or _sha256(_typed_snapshot_bytes(projection)) != STRICT_INHERITANCE_SNAPSHOT_SHA256
    ):
        raise RehearsalV22Error("strict inheritance typed snapshot digest drifted")
    allowed = _frozen_delta_pointers(
        inheritance,
        "allowed_v2_2_delta_json_pointers",
    )
    for index, pointer in enumerate(allowed):
        _json_pointer_value(prereg, pointer, f"allowed preregistration delta {index}")

    for label, old_path, old_sha, new_path, new_sha, list_key in (
        (
            "bundle",
            V2_1_BUNDLE_SCHEMA_RELATIVE,
            V2_1_BUNDLE_SCHEMA_SHA256,
            BUNDLE_SCHEMA_RELATIVE,
            BUNDLE_SCHEMA_SHA256,
            "bundle_schema_delta_domains",
        ),
        (
            "release",
            V2_1_RELEASE_SCHEMA_RELATIVE,
            V2_1_RELEASE_SCHEMA_SHA256,
            RELEASE_SCHEMA_RELATIVE,
            RELEASE_SCHEMA_SHA256,
            "release_schema_delta_domains",
        ),
    ):
        old_payload = inheritance_payload(old_path, f"v2.1 {label} schema")
        new_payload = inheritance_payload(new_path, f"v2.2 {label} schema")
        if _sha256(old_payload) != old_sha or _sha256(new_payload) != new_sha:
            raise RehearsalV22Error(f"{label} schema bytes drifted")
        old = copy.deepcopy(
            _object(strict_json_loads(old_payload, source=f"v2.1 {label} schema"), "old schema")
        )
        new = copy.deepcopy(
            _object(strict_json_loads(new_payload, source=f"v2.2 {label} schema"), "new schema")
        )
        pointers = _frozen_delta_pointers(inheritance, list_key)
        for index, raw_pointer in enumerate(pointers):
            pointer = raw_pointer
            if not _delete_json_pointer_if_present(new, pointer):
                raise RehearsalV22Error(
                    f"{label} schema delta pointer {index} does not resolve in v2.2"
                )
            _delete_json_pointer_if_present(old, pointer)
        if not _typed_json_equal(old, new):
            raise RehearsalV22Error(f"{label} schema retained surface differs from v2.1")
    return copy.deepcopy(snapshot)


def validate_carry_forward_lineage(
    project_root: Path,
    *,
    execution_head: str,
    implementation_commit: str | None = None,
    require_current: bool = True,
) -> tuple[AuthorityReference, ...]:
    """Reprove the nine frozen carry-forward rows and v2.1 exact 15-path tree."""

    root = project_root.absolute()
    head = _git_commit(root, execution_head, "carry-forward execution head")
    historical_commit = (
        None
        if require_current
        else _git_commit(
            root,
            implementation_commit,
            "historical carry-forward implementation commit",
        )
    )
    prereg_payload = (
        _regular_bytes(root / PREREGISTRATION_RELATIVE, "v2.2 preregistration")
        if historical_commit is None
        else _git_blob(root, historical_commit, PREREGISTRATION_RELATIVE.as_posix())
    )
    if _sha256(prereg_payload) != PREREGISTRATION_SHA256:
        raise RehearsalV22Error("v2.2 preregistration bytes drifted")
    prereg = _object(
        strict_json_loads(prereg_payload, source="v2.2 preregistration"),
        "v2.2 preregistration",
    )
    authorities = _object(prereg.get("authorities"), "v2.2 authorities")
    rows = _array(authorities.get("carry_forward_lineage"), "carry-forward lineage")
    named = list(CARRY_FORWARD_AUTHORITIES.values())
    expected_rows: list[JsonObject] = [
        {
            "path": path,
            "sha256": digest,
            "creating_commit": commit,
            "unique_a_history_required": True,
            "current_bytes_must_equal_creating_commit_blob": True,
            "later_touch_forbidden": True,
        }
        for path, digest, commit in named[:6]
    ]
    expected_rows.append(
        {
            "path": "IMPLEMENTATION_COMMIT",
            "sha256": None,
            "creating_commit": V2_1_IMPLEMENTATION_COMMIT,
            "parent_commit": CARRY_FORWARD_AUTHORITIES["v2_1_registry_expansion_authorization"][2],
            "tree_and_exact_surface_must_be_rederived": True,
        }
    )
    expected_rows.extend(
        {
            "path": path,
            "sha256": digest,
            "creating_commit": commit,
            "unique_a_history_required": True,
            "current_bytes_must_equal_creating_commit_blob": True,
            "later_touch_forbidden": True,
        }
        for path, digest, commit in named[6:]
    )
    if rows != expected_rows:
        raise RehearsalV22Error("carry-forward lineage registry drifted")
    references = tuple(AuthorityReference(path, digest, commit) for path, digest, commit in named)
    for reference in references:
        validate_unique_a_authority(
            root,
            reference,
            execution_head=head,
            require_current=require_current,
        )
    parents = (
        _git_bytes(
            root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            V2_1_IMPLEMENTATION_COMMIT,
            "--",
        )
        .decode("ascii", errors="strict")
        .strip()
        .split()
    )
    v2_1_parent = CARRY_FORWARD_AUTHORITIES["v2_1_registry_expansion_authorization"][2]
    if parents != [V2_1_IMPLEMENTATION_COMMIT, v2_1_parent]:
        raise RehearsalV22Error("v2.1 implementation parent drifted")
    surface = tuple(
        (status, path)
        for path, status in _parse_name_status(
            _git_bytes(
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-status",
                "--no-renames",
                v2_1_parent,
                V2_1_IMPLEMENTATION_COMMIT,
                "--",
            )
        ).items()
    )
    if surface != V2_1_IMPLEMENTATION_SURFACE:
        raise RehearsalV22Error("v2.1 implementation exact 15-path surface drifted")
    if not _git_is_ancestor(root, V2_1_IMPLEMENTATION_COMMIT, head):
        raise RehearsalV22Error("v2.1 implementation is absent from execution lineage")
    for _status, relative in V2_1_IMPLEMENTATION_SURFACE:
        payload = _git_blob(root, V2_1_IMPLEMENTATION_COMMIT, relative)
        if (
            require_current
            and _regular_bytes(root / relative, f"v2.1 frozen {relative}") != payload
        ):
            raise RehearsalV22Error(f"v2.1 frozen implementation byte drifted: {relative}")
    return references


def _parse_name_status(payload: bytes) -> dict[str, str]:
    try:
        lines = payload.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as exc:
        raise RehearsalV22Error("implementation surface is not UTF-8") from exc
    result: dict[str, str] = {}
    for line in lines:
        fields = line.split("\t")
        if len(fields) != 2 or fields[0] not in {"A", "M"} or fields[1] in result:
            raise RehearsalV22Error("implementation surface has a forbidden operation")
        result[_relative_text(fields[1], "implementation path")] = fields[0]
    return result


def _validate_implementation_surface(
    project_root: Path,
    implementation_commit: str,
    *,
    require_current: bool,
) -> None:
    root = project_root.absolute()
    commit = _git_commit(root, implementation_commit, "implementation commit")
    parents = (
        _git_bytes(
            root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            commit,
            "--",
        )
        .decode("ascii", errors="strict")
        .strip()
        .split()
    )
    if parents != [commit, PREREGISTRATION_COMMIT]:
        raise RehearsalV22Error("initial v2.2 implementation must be the direct child of be64235")
    observed = _parse_name_status(
        _git_bytes(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            PREREGISTRATION_COMMIT,
            commit,
            "--",
        )
    )
    expected = {path.as_posix(): "A" for path in IMPLEMENTATION_SURFACE}
    if observed != expected:
        raise RehearsalV22Error("v2.2 implementation is not the exact five-path A surface")
    for relative in expected:
        validate_implementation_blob(
            root,
            commit,
            relative,
            require_current=require_current,
        )


def _later_epoch_surface(
    project_root: Path,
    *,
    epoch: int,
    implementation_commit: str,
    owner_surface_authorization: AuthorityReference,
    execution_head: str,
    require_current: bool,
) -> bytes:
    root = project_root.absolute()
    payload = validate_unique_a_authority(
        root,
        owner_surface_authorization,
        execution_head=execution_head,
        require_current=require_current,
    )
    document = _object(
        strict_json_loads(payload, source="later epoch surface authorization"),
        "later epoch surface authorization",
    )
    if set(document) != {
        "schema_version",
        "verdict",
        "owner",
        "implementation_epoch",
        "base_commit",
        "exact_surface",
    }:
        raise RehearsalV22Error("later epoch surface authorization shape drifted")
    base = document.get("base_commit")
    rows = document.get("exact_surface")
    if (
        document.get("schema_version") != "p4.2a-v2-2-implementation-epoch-surface-authorization-v1"
        or document.get("verdict") != "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE"
        or document.get("owner") != {"identity": "ouyang", "approved": True}
        or document.get("implementation_epoch") != epoch
        or not _lower_hex(base, 40)
        or not isinstance(rows, list)
        or not rows
    ):
        raise RehearsalV22Error("later epoch surface authorization binding drifted")
    base_commit = cast(str, base)
    authority_parents = (
        _git_bytes(
            root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            owner_surface_authorization.creating_commit,
            "--",
        )
        .decode("ascii", errors="strict")
        .strip()
        .split()
    )
    if authority_parents != [owner_surface_authorization.creating_commit, base_commit]:
        raise RehearsalV22Error("later epoch authority is not the direct child of its base")
    authority_surface = _parse_name_status(
        _git_bytes(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            base_commit,
            owner_surface_authorization.creating_commit,
            "--",
        )
    )
    if authority_surface != {owner_surface_authorization.path: "A"}:
        raise RehearsalV22Error(
            "later epoch authority commit is not the exact unique-A authority surface"
        )
    selected = _git_commit(root, implementation_commit, "later epoch implementation")
    selected_parents = (
        _git_bytes(
            root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            selected,
            "--",
        )
        .decode("ascii", errors="strict")
        .strip()
        .split()
    )
    if selected_parents != [selected, owner_surface_authorization.creating_commit]:
        raise RehearsalV22Error(
            "later epoch implementation is not the direct child of owner authority"
        )
    permitted_paths = {path.as_posix() for path in IMPLEMENTATION_SURFACE}
    expected: dict[str, str] = {}
    observed_row_paths: list[str] = []
    for index, item in enumerate(rows):
        row = _object(item, f"later epoch exact_surface[{index}]")
        if set(row) != {"path", "status"}:
            raise RehearsalV22Error("later epoch exact-surface row shape drifted")
        relative = _relative_text(row.get("path"), "later epoch implementation path")
        status_value = row.get("status")
        if (
            relative not in permitted_paths
            or status_value not in {"A", "M"}
            or relative in expected
        ):
            raise RehearsalV22Error("later epoch exact-surface allowlist is invalid")
        expected[relative] = cast(str, status_value)
        observed_row_paths.append(relative)
    if observed_row_paths != sorted(observed_row_paths, key=lambda value: value.encode("utf-8")):
        raise RehearsalV22Error("later epoch exact-surface rows are not byte-sorted")
    observed = _parse_name_status(
        _git_bytes(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            owner_surface_authorization.creating_commit,
            selected,
            "--",
        )
    )
    if observed != expected:
        raise RehearsalV22Error("later epoch implementation differs from owner exact surface")
    for relative in expected:
        validate_implementation_blob(
            root,
            selected,
            relative,
            require_current=require_current,
        )
    if not _git_is_ancestor(root, PREREGISTRATION_COMMIT, base_commit):
        raise RehearsalV22Error("later epoch base lost preregistration lineage")
    if not _git_is_ancestor(root, PREREGISTRATION_COMMIT, selected):
        raise RehearsalV22Error("later epoch implementation lost preregistration lineage")
    return payload


def _document_mentions_commit(value: object, commit: str) -> bool:
    if value == commit:
        return True
    if isinstance(value, Mapping):
        return any(_document_mentions_commit(item, commit) for item in value.values())
    if isinstance(value, list):
        return any(_document_mentions_commit(item, commit) for item in value)
    return False


def validate_implementation_epoch(
    project_root: Path,
    *,
    epoch: int,
    implementation_commit: str,
    owner_surface_authorization: AuthorityReference,
    independent_review: AuthorityReference,
    control_merkle_root_sha256: str,
    execution_head: str,
    require_current_bytes: bool = True,
) -> ImplementationEpochValidation:
    """Validate one epoch, including the deliberately sibling initial authority."""

    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise RehearsalV22Error("implementation epoch must be one positive integer")
    if not _lower_hex(control_merkle_root_sha256, 64):
        raise RehearsalV22Error("implementation epoch control root is invalid")
    root = project_root.absolute()
    commit = _git_commit(root, implementation_commit, "epoch implementation commit")
    head = _git_commit(root, execution_head, "epoch execution head")
    if not _git_is_ancestor(root, commit, head):
        raise RehearsalV22Error("implementation commit is not on execution lineage")
    if epoch == 1:
        _validate_implementation_surface(
            root,
            commit,
            require_current=require_current_bytes,
        )
        surface_payload = validate_unique_a_authority(
            root,
            owner_surface_authorization,
            execution_head=head,
            allow_initial_sibling=True,
            require_current=require_current_bytes,
        )
        surface_document = _object(
            strict_json_loads(surface_payload, source="initial surface review"),
            "initial surface review",
        )
        if (
            surface_document.get("verdict")
            != "APPROVE_V2_2_PREREGISTRATION_AND_AUTHORIZE_IMPLEMENTATION"
            or surface_document.get("reviewed_commit") != PREREGISTRATION_COMMIT
            or _object(
                surface_document.get("what_this_authorizes"),
                "initial surface review authorization",
            ).get("granted")
            != (
                "implement the v2.2 harness exactly as preregistered: the shim, the "
                "implementation module, the validator, the series ledger, and the "
                "registered tests"
            )
        ):
            raise RehearsalV22Error("initial sibling implementation authority drifted")
    else:
        surface_payload = _later_epoch_surface(
            root,
            epoch=epoch,
            implementation_commit=commit,
            owner_surface_authorization=owner_surface_authorization,
            execution_head=head,
            require_current=require_current_bytes,
        )
    review_payload = validate_unique_a_authority(
        root,
        independent_review,
        execution_head=head,
        require_current=require_current_bytes,
    )
    review_document = _object(
        strict_json_loads(review_payload, source="implementation review"),
        "implementation review",
    )
    verdict = review_document.get("verdict")
    blockers = review_document.get("blockers")
    verdict_tokens = verdict.split("_") if isinstance(verdict, str) else []
    forbidden_verdict_tokens = {
        "NOT",
        "NO",
        "NON",
        "DISAPPROVE",
        "REJECT",
        "REJECTED",
        "DENY",
        "DENIED",
        "BLOCK",
        "BLOCKED",
        "PENDING",
    }
    if (
        not isinstance(verdict, str)
        or re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*", verdict) is None
        or not verdict_tokens
        or verdict_tokens[0] != "APPROVE"
        or "IMPLEMENTATION" not in verdict_tokens
        or not forbidden_verdict_tokens.isdisjoint(verdict_tokens)
        or blockers not in (None, [])
        or not _document_mentions_commit(review_document, commit)
        or not _git_is_ancestor(root, commit, independent_review.creating_commit)
    ):
        raise RehearsalV22Error("independent implementation review is not post-commit approval")
    return ImplementationEpochValidation(
        epoch=epoch,
        implementation_commit=commit,
        owner_surface_authorization=owner_surface_authorization,
        independent_implementation_review=independent_review,
        control_merkle_root_sha256=control_merkle_root_sha256,
    )


def _history_empty_root_sha256() -> str:
    return _sha256(HISTORY_EMPTY_PREFIX)


def _evidence_empty_root_sha256() -> str:
    return _sha256(EVIDENCE_EMPTY_PREFIX)


def _attempt_token_sha256(
    *,
    series_token_sha256: str,
    ordinal: int,
    implementation_commit: str,
    previous_history_root_sha256: str,
) -> str:
    if (
        not _lower_hex(series_token_sha256, 64)
        or isinstance(ordinal, bool)
        or not isinstance(ordinal, int)
        or ordinal < 1
        or ordinal >= 2**64
        or not _lower_hex(implementation_commit, 40)
        or not _lower_hex(previous_history_root_sha256, 64)
    ):
        raise RehearsalV22Error("attempt-token formula input is invalid")
    return _sha256(
        ATTEMPT_TOKEN_PREFIX
        + bytes.fromhex(series_token_sha256)
        + ordinal.to_bytes(8, "big")
        + bytes.fromhex(implementation_commit)
        + bytes.fromhex(previous_history_root_sha256)
    )


def _attempt_record_root_sha256(
    *,
    ordinal: int,
    attempt_token_sha256: str,
    started_sha256: str,
    candidate_sha256: str | None,
    terminal_sha256: str | None,
    evidence_tree_root_sha256: str,
) -> str:
    values = (
        attempt_token_sha256,
        started_sha256,
        candidate_sha256 or "0" * 64,
        terminal_sha256 or "0" * 64,
        evidence_tree_root_sha256,
    )
    if (
        isinstance(ordinal, bool)
        or not isinstance(ordinal, int)
        or ordinal < 1
        or ordinal >= 2**64
        or any(not _lower_hex(value, 64) for value in values)
    ):
        raise RehearsalV22Error("attempt-record formula input is invalid")
    return _sha256(
        ATTEMPT_RECORD_PREFIX
        + ordinal.to_bytes(8, "big")
        + b"".join(bytes.fromhex(value) for value in values)
    )


def _history_step_sha256(previous_root_sha256: str, record_root_sha256: str) -> str:
    if not _lower_hex(previous_root_sha256, 64) or not _lower_hex(record_root_sha256, 64):
        raise RehearsalV22Error("history-step formula input is invalid")
    return _sha256(
        HISTORY_STEP_PREFIX
        + bytes.fromhex(previous_root_sha256)
        + bytes.fromhex(record_root_sha256)
    )


def _binary_merkle_root(
    leaves: Sequence[bytes],
    *,
    node_prefix: bytes,
    empty_root: str | None,
) -> str:
    if not leaves:
        if empty_root is None:
            raise RehearsalV22Error("empty Merkle tree is forbidden")
        return empty_root
    nodes = list(leaves)
    while len(nodes) > 1:
        if len(nodes) % 2:
            nodes.append(nodes[-1])
        nodes = [
            hashlib.sha256(node_prefix + nodes[index] + nodes[index + 1]).digest()
            for index in range(0, len(nodes), 2)
        ]
    return nodes[0].hex()


def _evidence_tree(
    evidence_root: Path,
) -> tuple[str, tuple[tuple[str, bytes], ...]]:
    if not os.path.lexists(evidence_root):
        return _evidence_empty_root_sha256(), ()
    try:
        metadata = evidence_root.lstat()
    except OSError as exc:
        raise RehearsalV22Error("attempt evidence root is unavailable") from exc
    if (
        evidence_root.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or evidence_root.resolve(strict=True) != evidence_root.absolute()
    ):
        raise RehearsalV22Error("attempt evidence root is aliased or has wrong mode")
    files: list[tuple[str, bytes]] = []
    for path in sorted(
        evidence_root.rglob("*"),
        key=lambda item: item.relative_to(evidence_root).as_posix().encode("utf-8"),
    ):
        relative = path.relative_to(evidence_root).as_posix()
        item_metadata = path.lstat()
        if path.is_symlink() or not (
            stat.S_ISDIR(item_metadata.st_mode) or stat.S_ISREG(item_metadata.st_mode)
        ):
            raise RehearsalV22Error("attempt evidence contains a link or special entry")
        _relative_text(relative, "attempt evidence relative path")
        if stat.S_ISDIR(item_metadata.st_mode):
            if stat.S_IMODE(item_metadata.st_mode) != 0o700:
                raise RehearsalV22Error("attempt evidence directory mode drifted")
            continue
        if (
            item_metadata.st_nlink != 1
            or stat.S_IMODE(item_metadata.st_mode) != 0o600
            or path.resolve(strict=True) != path.absolute()
        ):
            raise RehearsalV22Error("attempt evidence file identity or mode drifted")
        files.append((relative, path.read_bytes()))
    leaves = [
        hashlib.sha256(
            EVIDENCE_LEAF_PREFIX
            + relative.encode("utf-8")
            + b"\0"
            + hashlib.sha256(payload).digest()
        ).digest()
        for relative, payload in files
    ]
    return (
        _binary_merkle_root(
            leaves,
            node_prefix=EVIDENCE_NODE_PREFIX,
            empty_root=_evidence_empty_root_sha256(),
        ),
        tuple(files),
    )


def _evidence_tree_root(evidence_root: Path) -> str:
    return _evidence_tree(evidence_root)[0]


def _generic_merkle_root(payloads: Mapping[str, bytes]) -> str:
    if not payloads:
        raise RehearsalV22Error("run/control Merkle tree cannot be empty")
    leaves = [
        hashlib.sha256(
            MERKLE_LEAF_PREFIX
            + relative.encode("utf-8")
            + b"\0"
            + hashlib.sha256(payloads[relative]).digest()
        ).digest()
        for relative in sorted(payloads, key=lambda value: value.encode("utf-8"))
    ]
    return _binary_merkle_root(leaves, node_prefix=MERKLE_NODE_PREFIX, empty_root=None)


def _bundle_root_sha256(
    *,
    attempt_history_root_sha256: str,
    run_a_root_sha256: str,
    run_b_root_sha256: str,
    control_surface_root_sha256: str,
) -> str:
    values = (
        attempt_history_root_sha256,
        run_a_root_sha256,
        run_b_root_sha256,
        control_surface_root_sha256,
    )
    if any(not _lower_hex(value, 64) for value in values):
        raise RehearsalV22Error("bundle-root formula input is invalid")
    return _sha256(BUNDLE_ROOT_PREFIX + b"".join(bytes.fromhex(value) for value in values))


def _series_document(binding: ExecutionBinding, *, created_at_utc: str) -> JsonObject:
    return {
        "schema_version": "p4.2a-v2-2-rehearsal-series-v1",
        "series_id": REHEARSAL_ID,
        "series_token_sha256": binding.series_token_sha256,
        "policy": SERIES_POLICY,
        "ledger_root": binding.ledger_root.as_posix(),
        "attempt_limit": "unbounded_until_first_validated_success_or_owner_abandonment",
        "per_attempt_action_time_owner_authorization_required": True,
        "automatic_retry_count": 0,
        "first_validated_candidate_closes_series": True,
        "preregistration": {
            "path": PREREGISTRATION_RELATIVE.as_posix(),
            "sha256": PREREGISTRATION_SHA256,
            "creating_commit": PREREGISTRATION_COMMIT,
            "unique_a_history_verified": True,
        },
        "bundle_schema": {
            "path": BUNDLE_SCHEMA_RELATIVE.as_posix(),
            "sha256": BUNDLE_SCHEMA_SHA256,
        },
        "release_schema": {
            "path": RELEASE_SCHEMA_RELATIVE.as_posix(),
            "sha256": RELEASE_SCHEMA_SHA256,
        },
        "created_at_utc": created_at_utc,
    }


SERIES_FIELDS = {
    "schema_version",
    "series_id",
    "series_token_sha256",
    "policy",
    "ledger_root",
    "attempt_limit",
    "per_attempt_action_time_owner_authorization_required",
    "automatic_retry_count",
    "first_validated_candidate_closes_series",
    "preregistration",
    "bundle_schema",
    "release_schema",
    "created_at_utc",
}
STARTED_FIELDS = {
    "schema_version",
    "series_id",
    "series_token_sha256",
    "ordinal",
    "attempt_token_sha256",
    "previous_history_root_sha256",
    "implementation_epoch",
    "implementation_commit",
    "owner_action_time_authorization",
    "control_merkle_root_sha256",
    "command",
    "command_sha256",
    "environment",
    "environment_sha256",
    "interpreter_path",
    "interpreter_sha256",
    "created_at_utc",
}
CANDIDATE_FIELDS = {
    "schema_version",
    "series_id",
    "ordinal",
    "attempt_token_sha256",
    "implementation_epoch",
    "implementation_commit",
    "run_a_root_sha256",
    "run_b_root_sha256",
    "control_surface_root_sha256",
    "evidence_tree_root_sha256",
    "candidate_content_root_sha256",
    "validated_at_utc",
}
TERMINAL_FIELDS = {
    "schema_version",
    "series_id",
    "ordinal",
    "attempt_token_sha256",
    "outcome",
    "reached_stage",
    "implementation_epoch",
    "implementation_commit",
    "automatic_retry_count",
    "artifact_inventory",
    "error",
    "evidence_tree_root_sha256",
    "completed_at_utc",
}
ACTION_AUTHORIZATION_FIELDS = {
    "schema_version",
    "authorization_id",
    "created_at_utc",
    "created_at_shanghai",
    "verdict",
    "owner",
    "series_id",
    "series_token_sha256",
    "ledger_root",
    "ordinal",
    "previous_history_root_sha256",
    "implementation_epoch",
    "implementation_commit",
    "owner_exact_surface_authorization",
    "independent_implementation_review",
    "control_merkle_root_sha256",
    "exact_argv",
    "command_sha256",
    "exact_environment",
    "environment_sha256",
    "authorized_pipeline_starts",
    "automatic_retry_count",
    "heldout_evaluation_authorized",
    "locks",
}
RECOVERY_AUTHORIZATION_FIELDS = {
    "schema_version",
    "authorization_id",
    "created_at_utc",
    "created_at_shanghai",
    "verdict",
    "owner",
    "sealed_series",
    "execution_epoch",
    "destination",
    "exact_argv",
    "command_sha256",
    "exact_environment",
    "environment_sha256",
    "authorized_bundle_recovery_starts",
    "authorized_pipeline_starts",
    "automatic_retry_count",
    "effect_authorization",
    "interpreter",
    "locks",
}
RECOVERY_OWNER_FIELDS = {"identity", "approved", "scope"}
RECOVERY_SEALED_SERIES_FIELDS = {
    "series_id",
    "series_token_sha256",
    "ledger_root",
    "history_root_sha256",
    "live_ledger_root_sha256",
    "series_closed",
    "started_count",
    "failed_count",
    "incomplete_count",
    "validated_candidate_count",
    "selected_attempt_ordinal",
    "selected_implementation_epoch",
    "selected_implementation_commit",
    "selected_control_merkle_root_sha256",
    "selected_evidence_tree_root_sha256",
    "selected_candidate_content_root_sha256",
    "selected_run_a_root_sha256",
    "selected_run_b_root_sha256",
    "selected_terminal_outcome",
    "selected_reached_stage",
    "automatic_retry_count",
    "selected_files",
}
RECOVERY_SELECTED_FILES_FIELDS = {"started", "candidate", "terminal"}
RECOVERY_SELECTED_FILE_FIELDS = {"relative_path", "sha256", "bytes"}
RECOVERY_EXECUTION_EPOCH_FIELDS = {
    "epoch",
    "implementation_commit",
    "owner_exact_surface_authorization",
    "independent_implementation_review",
    "merge_commit",
    "landing_report",
    "control_merkle_root_sha256",
    "control_record_count",
    "latest_complete_landed_epoch_required",
    "current_control_bytes_required",
    "loaded_module_bytes_required",
}
RECOVERY_DESTINATION_FIELDS = {
    "absolute_path",
    "required_absent_before_start",
    "publication_mode",
    "bundle_schema_version",
    "expected_bundle_status",
}
RECOVERY_INTERPRETER_FIELDS = {"path", "sha256", "version"}
RECOVERY_EFFECT_FIELDS = {
    "ledger_read",
    "ledger_write",
    "git_object_read",
    "git_or_worktree_write",
    "recovery_claim_create_once",
    "temporary_stage_create_once",
    "destination_publish_once",
    "attempt_allocation",
    "candidate_or_terminal_rewrite",
    "pipeline_execution",
    "model_access",
    "network_access",
    "sqlite_or_production_database_access",
    "heldout_materialization_inference_or_evaluation",
}
RECOVERY_LOCK_FIELDS = {
    "p4_2a_done",
    "p4_2b_unlocked",
    "p4_3_unlocked",
    "heldout_evaluation_unlocked",
    "real_trading_unlocked",
    "non_simulate_trading_unlocked",
}
RECOVERY_STARTED_FIELDS = {
    "schema_version",
    "recovery_id",
    "authorization",
    "created_at_utc",
    "created_at_shanghai",
    "execution_head",
    "execution_epoch",
    "sealed_history_root_sha256",
    "sealed_live_ledger_root_sha256",
    "destination",
    "state",
    "authorized_bundle_recovery_starts",
    "authorized_pipeline_starts",
    "automatic_retry_count",
}
RECOVERY_TERMINAL_FIELDS = {
    "schema_version",
    "recovery_id",
    "authorization",
    "completed_at_utc",
    "completed_at_shanghai",
    "outcome",
    "reached_stage",
    "sealed_ledger_before_sha256",
    "sealed_ledger_after_sha256",
    "destination",
    "published_bundle_sha256",
    "published_tree_sha256",
    "temporary_authority_absent",
    "pipeline_starts",
    "automatic_retry_count",
    "error",
}
RFC3339_UTC_SECONDS = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
RFC3339_SHANGHAI_SECONDS = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+08:00$"
)


def _canonical_object_file(
    path: Path,
    *,
    label: str,
    exact_fields: set[str],
) -> tuple[JsonObject, bytes, str]:
    payload = _regular_bytes(path, label)
    value = _object(strict_json_loads(payload, source=label), label)
    if set(value) != exact_fields or _canonical_json_bytes(value) != payload:
        raise RehearsalV22Error(f"{label} is not exact canonical JSON")
    return value, payload, _sha256(payload)


def _inventory_from_evidence(
    evidence_root: Path,
) -> tuple[str, tuple[JsonObject, ...]]:
    root, files = _evidence_tree(evidence_root)
    rows = tuple(
        {
            "logical_name": relative,
            "relative_path": relative,
            "bytes": len(payload),
            "sha256": _sha256(payload),
            "durability": "LEDGER_PERSISTED",
        }
        for relative, payload in files
    )
    return root, rows


@dataclass(frozen=True)
class ValidatedAttemptRecord:
    ordinal: int
    outcome: AttemptOutcome
    reached_stage: str
    attempt_token_sha256: str
    previous_history_root_sha256: str
    implementation_epoch: int
    implementation_commit: str
    owner_action_time_authorization: AuthorityReference
    command_sha256: str
    environment_sha256: str
    started_path: Path
    started_bytes: bytes
    started_sha256: str
    candidate_path: Path | None
    candidate_bytes: bytes | None
    candidate_sha256: str | None
    terminal_path: Path | None
    terminal_bytes: bytes | None
    terminal_sha256: str | None
    evidence_tree_root_sha256: str
    artifact_inventory: tuple[JsonObject, ...]
    error: JsonObject | None
    record_root_sha256: str
    history_root_sha256: str


@dataclass(frozen=True)
class HistoryValidation:
    binding: ExecutionBinding
    ledger_exists: bool
    records: tuple[ValidatedAttemptRecord, ...]
    started_count: int
    failed_count: int
    incomplete_count: int
    validated_candidate_count: int
    selected_attempt_ordinal: int | None
    series_closed: bool
    history_root_sha256: str
    live_ledger_root_sha256: str | None
    live_file_inventory: tuple[str, ...]


def _validate_authority_ref_shape(value: object, label: str) -> AuthorityReference:
    return _authority_from_json(value, label)


def _live_ledger_files(binding: ExecutionBinding) -> tuple[tuple[str, bytes], ...]:
    root = binding.ledger_root
    observed_directories: set[str] = set()
    files: list[tuple[str, bytes]] = []
    for path in sorted(
        root.rglob("*"),
        key=lambda item: item.relative_to(root).as_posix().encode("utf-8"),
    ):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if path.is_symlink() or not (
            stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
        ):
            raise RehearsalV22Error("live ledger contains a link or special entry")
        if stat.S_ISDIR(metadata.st_mode):
            parts = PurePosixPath(relative).parts
            if len(parts) > 3:
                _relative_text("/".join(parts[3:]), "live evidence directory")
            valid_directory = (
                relative == "attempts"
                or (
                    len(parts) == 2
                    and parts[0] == "attempts"
                    and re.fullmatch(r"[0-9]{6}", parts[1]) is not None
                )
                or (
                    len(parts) >= 3
                    and parts[0] == "attempts"
                    and re.fullmatch(r"[0-9]{6}", parts[1]) is not None
                    and parts[2] == "evidence"
                    and all(part not in {"", ".", ".."} for part in parts[3:])
                )
            )
            if not valid_directory or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise RehearsalV22Error(f"unexpected live ledger directory: {relative}")
            observed_directories.add(relative)
            continue
        parts = PurePosixPath(relative).parts
        allowed = relative in {"series.json", ".series.lock"}
        if len(parts) == 3 and parts[0] == "attempts" and re.fullmatch(r"[0-9]{6}", parts[1]):
            allowed = parts[2] in {"started.json", "candidate.json", "terminal.json"}
        if (
            len(parts) >= 4
            and parts[0] == "attempts"
            and re.fullmatch(r"[0-9]{6}", parts[1])
            and parts[2] == "evidence"
        ):
            allowed = True
            _relative_text("/".join(parts[3:]), "live evidence path")
        if (
            not allowed
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or path.resolve(strict=True) != path.absolute()
        ):
            raise RehearsalV22Error(f"unexpected live ledger file: {relative}")
        payload = path.read_bytes()
        if relative == ".series.lock" and payload:
            raise RehearsalV22Error("series lock file must remain empty")
        files.append((relative, payload))
    required = {"series.json", ".series.lock"}
    if not required.issubset({relative for relative, _payload in files}):
        raise RehearsalV22Error("live ledger omits series or lock file")
    expected_directories = {"attempts"}
    attempts = {
        PurePosixPath(relative).parts[1]
        for relative, _payload in files
        if len(PurePosixPath(relative).parts) >= 3
        and PurePosixPath(relative).parts[0] == "attempts"
    }
    for attempt in attempts:
        expected_directories.update({f"attempts/{attempt}", f"attempts/{attempt}/evidence"})
    for relative, _payload in files:
        parts = PurePosixPath(relative).parts
        if len(parts) < 5 or parts[0] != "attempts" or parts[2] != "evidence":
            continue
        for stop in range(4, len(parts)):
            expected_directories.add("/".join(parts[:stop]))
    if observed_directories != expected_directories:
        raise RehearsalV22Error(
            "live ledger directories are not the exact ancestors of regular members"
        )
    return tuple(files)


def _live_ledger_root(binding: ExecutionBinding) -> tuple[str, tuple[str, ...]]:
    files = _live_ledger_files(binding)
    leaves = [
        hashlib.sha256(
            LEDGER_LEAF_PREFIX + relative.encode("utf-8") + b"\0" + hashlib.sha256(payload).digest()
        ).digest()
        for relative, payload in files
    ]
    root = _binary_merkle_root(leaves, node_prefix=MERKLE_NODE_PREFIX, empty_root=None)
    return root, tuple(relative for relative, _payload in files)


def _validate_series_document(binding: ExecutionBinding) -> None:
    document, payload, _digest = _canonical_object_file(
        binding.ledger_root / "series.json",
        label="series.json",
        exact_fields=SERIES_FIELDS,
    )
    if (
        document.get("schema_version") != "p4.2a-v2-2-rehearsal-series-v1"
        or document.get("series_id") != REHEARSAL_ID
        or document.get("series_token_sha256") != binding.series_token_sha256
        or document.get("policy") != SERIES_POLICY
        or document.get("ledger_root") != binding.ledger_root.as_posix()
        or document.get("attempt_limit")
        != "unbounded_until_first_validated_success_or_owner_abandonment"
        or document.get("per_attempt_action_time_owner_authorization_required") is not True
        or document.get("automatic_retry_count") != 0
        or document.get("first_validated_candidate_closes_series") is not True
        or document.get("preregistration")
        != {
            "path": PREREGISTRATION_RELATIVE.as_posix(),
            "sha256": PREREGISTRATION_SHA256,
            "creating_commit": PREREGISTRATION_COMMIT,
            "unique_a_history_verified": True,
        }
        or document.get("bundle_schema")
        != {"path": BUNDLE_SCHEMA_RELATIVE.as_posix(), "sha256": BUNDLE_SCHEMA_SHA256}
        or document.get("release_schema")
        != {"path": RELEASE_SCHEMA_RELATIVE.as_posix(), "sha256": RELEASE_SCHEMA_SHA256}
        or not isinstance(document.get("created_at_utc"), str)
        or RFC3339_UTC_SECONDS.fullmatch(cast(str, document["created_at_utc"])) is None
        or payload != _canonical_json_bytes(document)
    ):
        raise RehearsalV22Error("series.json binding or semantics drifted")


def _current_execution_head(project_root: Path) -> str:
    observed = (
        _git_bytes(project_root, "rev-parse", "HEAD")
        .decode(
            "ascii",
            errors="strict",
        )
        .strip()
    )
    return _git_commit(project_root, observed, "current execution head")


def _validate_action_authorization(
    binding: ExecutionBinding,
    authority: AuthorityReference,
    *,
    expected_ordinal: int,
    expected_previous_history_root_sha256: str,
    require_current_process: bool,
) -> ActionAuthorization:
    """Actively revalidate one action receipt before ledger allocation or replay."""

    root = binding.project_root
    execution_head = _current_execution_head(root)
    payload = validate_unique_a_authority(
        root,
        authority,
        execution_head=execution_head,
        require_current=require_current_process,
    )
    document = _object(
        strict_json_loads(payload, source="action-time authorization"),
        "action-time authorization",
    )
    if set(document) != ACTION_AUTHORIZATION_FIELDS or _canonical_json_bytes(document) != payload:
        raise RehearsalV22Error("action-time authorization is not exact canonical JSON")
    created_utc = document.get("created_at_utc")
    created_shanghai = document.get("created_at_shanghai")
    if (
        not isinstance(created_utc, str)
        or RFC3339_UTC_SECONDS.fullmatch(created_utc) is None
        or not isinstance(created_shanghai, str)
        or RFC3339_SHANGHAI_SECONDS.fullmatch(created_shanghai) is None
    ):
        raise RehearsalV22Error("action-time authorization timestamps are invalid")
    try:
        utc_value = datetime.fromisoformat(created_utc.replace("Z", "+00:00"))
        shanghai_value = datetime.fromisoformat(created_shanghai)
    except ValueError as exc:
        raise RehearsalV22Error("action-time authorization timestamp is invalid") from exc
    if utc_value != shanghai_value:
        raise RehearsalV22Error("action-time authorization timestamps disagree")
    date_token = created_shanghai[:10].replace("-", "")
    expected_relative = (
        "docs/phase4/reports/P4.2a-v2-2-rehearsal-attempt-"
        f"{expected_ordinal:06d}-execution-authorization-{date_token}.json"
    )
    expected_path = root / expected_relative
    expected_argv = (
        FIXED_ORIG_ARGV_EXECUTABLE.as_posix(),
        "-S",
        "-P",
        "-B",
        binding.shim_path.as_posix(),
        "--execute",
        "--attempt-authorization",
        expected_path.as_posix(),
        "--expected-ordinal",
        str(expected_ordinal),
    )
    owner_surface = _validate_authority_ref_shape(
        document.get("owner_exact_surface_authorization"),
        "owner exact-surface authorization",
    )
    independent_review = _validate_authority_ref_shape(
        document.get("independent_implementation_review"),
        "independent implementation review",
    )
    epoch = document.get("implementation_epoch")
    commit = document.get("implementation_commit")
    control_root = document.get("control_merkle_root_sha256")
    exact_argv = document.get("exact_argv")
    exact_environment = document.get("exact_environment")
    if (
        authority.path != expected_relative
        or expected_path != root.joinpath(*PurePosixPath(authority.path).parts)
        or document.get("schema_version")
        != "p4.2a-v2-2-rehearsal-attempt-execution-authorization-v1"
        or document.get("authorization_id")
        != (
            "P4.2A-V2-2-REHEARSAL-ATTEMPT-"
            f"{expected_ordinal:06d}-EXECUTION-AUTHORIZATION-{date_token}"
        )
        or document.get("verdict")
        != "APPROVE_EXACTLY_ONE_V2_2_REHEARSAL_ATTEMPT_ZERO_AUTOMATIC_RETRY"
        or document.get("owner")
        != {
            "identity": "ouyang",
            "approved": True,
            "scope": "one_disclosed_v2_2_rehearsal_ordinal_only",
        }
        or document.get("series_id") != REHEARSAL_ID
        or document.get("series_token_sha256") != binding.series_token_sha256
        or document.get("ledger_root") != binding.ledger_root.as_posix()
        or document.get("ordinal") != expected_ordinal
        or document.get("previous_history_root_sha256") != expected_previous_history_root_sha256
        or isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or epoch < 1
        or not _lower_hex(commit, 40)
        or not _lower_hex(control_root, 64)
        or not isinstance(exact_argv, list)
        or tuple(exact_argv) != expected_argv
        or document.get("command_sha256") != _command_sha256(expected_argv)
        or exact_environment != EXACT_ENVIRONMENT
        or document.get("environment_sha256") != _environment_sha256(EXACT_ENVIRONMENT)
        or document.get("authorized_pipeline_starts") != 1
        or document.get("automatic_retry_count") != 0
        or document.get("heldout_evaluation_authorized") is not False
        or document.get("locks")
        != {
            "real_heldout_materialization": False,
            "real_heldout_inference": False,
            "heldout_evaluation": False,
            "p4_2b": False,
            "p4_3": False,
            "trading": False,
        }
    ):
        raise RehearsalV22Error("action-time authorization binding drifted")
    if require_current_process and (
        binding.action_authorization_path != expected_path
        or tuple(sys.orig_argv) != expected_argv
        or dict(os.environ) != EXACT_ENVIRONMENT
    ):
        raise RehearsalV22Error("action-time authorization differs from this OS process")
    if not _git_is_ancestor(root, independent_review.creating_commit, authority.creating_commit):
        raise RehearsalV22Error("action receipt predates its independent implementation review")
    validate_implementation_epoch(
        root,
        epoch=epoch,
        implementation_commit=cast(str, commit),
        owner_surface_authorization=owner_surface,
        independent_review=independent_review,
        control_merkle_root_sha256=cast(str, control_root),
        execution_head=execution_head,
        require_current_bytes=require_current_process,
    )
    return ActionAuthorization(
        path=expected_path,
        payload=payload,
        sha256=authority.sha256,
        creating_commit=authority.creating_commit,
        ordinal=expected_ordinal,
        previous_history_root_sha256=expected_previous_history_root_sha256,
        implementation_epoch=epoch,
        implementation_commit=cast(str, commit),
        owner_surface_authorization=owner_surface,
        independent_implementation_review=independent_review,
        control_merkle_root_sha256=cast(str, control_root),
        exact_argv=expected_argv,
        command_sha256=_command_sha256(expected_argv),
        exact_environment=dict(EXACT_ENVIRONMENT),
        environment_sha256=_environment_sha256(EXACT_ENVIRONMENT),
    )


def _validate_bundle_recovery_authorization(
    binding: ExecutionBinding,
    authority: AuthorityReference,
    *,
    require_current_process: bool,
    require_destination_absent: bool = True,
) -> BundleRecoveryAuthorization:
    """Validate the exact 19-field owner authority for one sealed recovery."""

    root = binding.project_root.absolute()
    execution_head = _current_execution_head(root)
    payload = validate_unique_a_authority(
        root,
        authority,
        execution_head=execution_head,
    )
    document = _object(
        strict_json_loads(payload, source="sealed-bundle recovery authorization"),
        "sealed-bundle recovery authorization",
    )
    if set(document) != RECOVERY_AUTHORIZATION_FIELDS or _canonical_json_bytes(
        document
    ) != payload:
        raise RehearsalV22Error("recovery authorization is not exact canonical JSON")
    created_utc = document.get("created_at_utc")
    created_shanghai = document.get("created_at_shanghai")
    if (
        not isinstance(created_utc, str)
        or RFC3339_UTC_SECONDS.fullmatch(created_utc) is None
        or not isinstance(created_shanghai, str)
        or RFC3339_SHANGHAI_SECONDS.fullmatch(created_shanghai) is None
    ):
        raise RehearsalV22Error("recovery authorization timestamps are invalid")
    try:
        if datetime.fromisoformat(created_utc.replace("Z", "+00:00")) != datetime.fromisoformat(
            created_shanghai
        ):
            raise RehearsalV22Error("recovery authorization timestamps disagree")
    except ValueError as exc:
        raise RehearsalV22Error("recovery authorization timestamp is invalid") from exc
    owner = _object(document.get("owner"), "recovery owner")
    sealed = _object(document.get("sealed_series"), "recovery sealed series")
    execution = _object(document.get("execution_epoch"), "recovery execution epoch")
    destination = _object(document.get("destination"), "recovery destination")
    interpreter = _object(document.get("interpreter"), "recovery interpreter")
    effects = _object(document.get("effect_authorization"), "recovery effects")
    locks = _object(document.get("locks"), "recovery locks")
    selected_files = _object(sealed.get("selected_files"), "recovery selected files")
    if (
        set(owner) != RECOVERY_OWNER_FIELDS
        or set(sealed) != RECOVERY_SEALED_SERIES_FIELDS
        or set(execution) != RECOVERY_EXECUTION_EPOCH_FIELDS
        or set(destination) != RECOVERY_DESTINATION_FIELDS
        or set(interpreter) != RECOVERY_INTERPRETER_FIELDS
        or set(effects) != RECOVERY_EFFECT_FIELDS
        or set(locks) != RECOVERY_LOCK_FIELDS
        or set(selected_files) != RECOVERY_SELECTED_FILES_FIELDS
    ):
        raise RehearsalV22Error("recovery authorization nested shape drifted")
    for label in sorted(RECOVERY_SELECTED_FILES_FIELDS):
        if set(_object(selected_files[label], f"recovery selected {label}")) != (
            RECOVERY_SELECTED_FILE_FIELDS
        ):
            raise RehearsalV22Error("recovery selected-file shape drifted")
    exact_argv_raw = document.get("exact_argv")
    exact_environment_raw = document.get("exact_environment")
    if (
        not isinstance(document.get("authorization_id"), str)
        or not cast(str, document["authorization_id"])
        or document.get("schema_version")
        != "p4.2a-v2-2-sealed-bundle-recovery-authorization-v1"
        or document.get("verdict")
        != "APPROVE_EXACTLY_ONE_SEALED_BUNDLE_RECOVERY_ZERO_PIPELINE_START"
        or owner
        != {
            "identity": "ouyang",
            "approved": True,
            "scope": "one disclosed sealed-bundle recovery only",
        }
        or type(owner.get("approved")) is not bool
        or not isinstance(exact_argv_raw, list)
        or not exact_argv_raw
        or any(not isinstance(value, str) or not value for value in exact_argv_raw)
        or not isinstance(exact_environment_raw, dict)
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            for key, value in exact_environment_raw.items()
        )
        or document.get("command_sha256") != _command_sha256(cast(list[str], exact_argv_raw))
        or document.get("environment_sha256")
        != _environment_sha256(cast(dict[str, str], exact_environment_raw))
        or not _is_exact_int(document.get("authorized_bundle_recovery_starts"), 1)
        or not _is_exact_int(document.get("authorized_pipeline_starts"), 0)
        or not _is_exact_int(document.get("automatic_retry_count"), 0)
    ):
        raise RehearsalV22Error("recovery authorization top-level binding drifted")
    expected_effects = {
        "ledger_read": True,
        "ledger_write": False,
        "git_object_read": True,
        "git_or_worktree_write": False,
        "recovery_claim_create_once": True,
        "temporary_stage_create_once": True,
        "destination_publish_once": True,
        "attempt_allocation": False,
        "candidate_or_terminal_rewrite": False,
        "pipeline_execution": False,
        "model_access": False,
        "network_access": False,
        "sqlite_or_production_database_access": False,
        "heldout_materialization_inference_or_evaluation": False,
    }
    if (
        not _is_exact_bool_mapping(effects)
        or not _is_exact_bool_mapping(locks)
        or effects != expected_effects
        or any(value is not False for value in locks.values())
    ):
        raise RehearsalV22Error("recovery effect or phase locks are not fail-closed")
    interpreter_path = interpreter.get("path")
    interpreter_payload = (
        _fixed_launcher_bytes()
        if interpreter_path == FIXED_PYTHON_LAUNCHER.as_posix()
        else _regular_bytes(Path(cast(str, interpreter_path)), "recovery interpreter")
        if isinstance(interpreter_path, str)
        else b""
    )
    if (
        not isinstance(interpreter_path, str)
        or Path(interpreter_path).absolute() != Path(sys.executable).absolute()
        or interpreter.get("sha256") != _sha256(interpreter_payload)
        or interpreter.get("version") != platform.python_version()
    ):
        raise RehearsalV22Error("recovery interpreter binding drifted")
    if (
        destination
        != {
            "absolute_path": binding.destination.as_posix(),
            "required_absent_before_start": True,
            "publication_mode": "ATOMIC_DIRECTORY_NO_REPLACE",
            "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
            "expected_bundle_status": "PASS_REHEARSAL_V2_2_AWAITING_OWNER_REVIEW",
        }
        or type(destination.get("required_absent_before_start")) is not bool
        or (require_destination_absent and os.path.lexists(binding.destination))
        or (
            not require_destination_absent
            and (
                not binding.destination.is_dir()
                or binding.destination.is_symlink()
                or binding.destination.resolve(strict=True) != binding.destination.absolute()
            )
        )
    ):
        raise RehearsalV22Error("recovery destination binding or absence drifted")
    history = validate_live_history(binding)
    if (
        not history.series_closed
        or history.selected_attempt_ordinal is None
        or history.validated_candidate_count != 1
        or history.incomplete_count != 0
        or history.live_ledger_root_sha256 is None
    ):
        raise RehearsalV22Error("recovery requires one closed selected series")
    selected = history.records[history.selected_attempt_ordinal - 1]
    if selected.candidate_bytes is None or selected.terminal_bytes is None:
        raise RehearsalV22Error("recovery selected record is incomplete")
    candidate = _object(
        strict_json_loads(selected.candidate_bytes, source="recovery selected candidate"),
        "recovery selected candidate",
    )
    sealed_integer_fields = {
        "started_count": history.started_count,
        "failed_count": history.failed_count,
        "incomplete_count": 0,
        "validated_candidate_count": 1,
        "selected_attempt_ordinal": selected.ordinal,
        "selected_implementation_epoch": selected.implementation_epoch,
        "automatic_retry_count": 0,
    }
    if (
        type(sealed.get("series_closed")) is not bool
        or any(
            not _is_exact_int(sealed.get(key), expected)
            for key, expected in sealed_integer_fields.items()
        )
        or not _is_exact_int(execution.get("epoch"))
        or not _is_exact_int(execution.get("control_record_count"))
        or any(
            type(execution.get(key)) is not bool
            for key in (
                "latest_complete_landed_epoch_required",
                "current_control_bytes_required",
                "loaded_module_bytes_required",
            )
        )
    ):
        raise RehearsalV22Error("recovery authorization bool/int types drifted")
    expected_sealed = {
        "series_id": REHEARSAL_ID,
        "series_token_sha256": binding.series_token_sha256,
        "ledger_root": binding.ledger_root.as_posix(),
        "history_root_sha256": history.history_root_sha256,
        "live_ledger_root_sha256": history.live_ledger_root_sha256,
        "series_closed": True,
        "started_count": history.started_count,
        "failed_count": history.failed_count,
        "incomplete_count": 0,
        "validated_candidate_count": 1,
        "selected_attempt_ordinal": selected.ordinal,
        "selected_implementation_epoch": selected.implementation_epoch,
        "selected_implementation_commit": selected.implementation_commit,
        "selected_control_merkle_root_sha256": candidate.get(
            "control_surface_root_sha256"
        ),
        "selected_evidence_tree_root_sha256": selected.evidence_tree_root_sha256,
        "selected_candidate_content_root_sha256": candidate.get(
            "candidate_content_root_sha256"
        ),
        "selected_run_a_root_sha256": candidate.get("run_a_root_sha256"),
        "selected_run_b_root_sha256": candidate.get("run_b_root_sha256"),
        "selected_terminal_outcome": "CANDIDATE_VALIDATED_AND_SELECTED",
        "selected_reached_stage": "bundle_candidate_validated",
        "automatic_retry_count": 0,
        "selected_files": selected_files,
    }
    if any(
        sealed.get(key) != value
        for key, value in expected_sealed.items()
        if key != "selected_files"
    ):
        raise RehearsalV22Error("recovery sealed-series binding drifted")
    file_bindings = {
        "started": (
            f"attempts/{selected.ordinal:06d}/started.json",
            selected.started_bytes,
        ),
        "candidate": (
            f"attempts/{selected.ordinal:06d}/candidate.json",
            selected.candidate_bytes,
        ),
        "terminal": (
            f"attempts/{selected.ordinal:06d}/terminal.json",
            selected.terminal_bytes,
        ),
    }
    for label, (relative, selected_payload) in file_bindings.items():
        row = _object(selected_files[label], f"recovery selected {label}")
        if not _is_exact_int(row.get("bytes"), len(selected_payload)) or row != {
            "relative_path": relative,
            "sha256": _sha256(selected_payload),
            "bytes": len(selected_payload),
        }:
            raise RehearsalV22Error("recovery selected-file bytes drifted")
    if binding.mode == "REGISTERED_OFFICIAL":
        if len(history.records) != 2:
            raise RehearsalV22Error("official recovery history is not exactly two records")
        failed = history.records[0]
        if (
            failed.ordinal != 1
            or failed.outcome != "FAILED"
            or failed.reached_stage != "started_persisted"
            or failed.implementation_epoch != 2
            or failed.implementation_commit
            != OFFICIAL_FAILED_EPOCH_TWO_IMPLEMENTATION_COMMIT
            or failed.started_sha256 != OFFICIAL_FAILED_STARTED_SHA256
            or failed.candidate_sha256 is not None
            or failed.terminal_sha256 != OFFICIAL_FAILED_TERMINAL_SHA256
            or failed.evidence_tree_root_sha256
            != OFFICIAL_FAILED_EVIDENCE_ROOT_SHA256
            or selected.ordinal != 2
            or selected.outcome != "CANDIDATE_VALIDATED_AND_SELECTED"
            or selected.reached_stage != "bundle_candidate_validated"
            or selected.implementation_epoch != 4
            or selected.implementation_commit
            != OFFICIAL_SELECTED_EPOCH_FOUR_IMPLEMENTATION_COMMIT
            or selected.started_sha256 != OFFICIAL_SELECTED_STARTED_SHA256
            or selected.candidate_sha256 != OFFICIAL_SELECTED_CANDIDATE_SHA256
            or selected.terminal_sha256 != OFFICIAL_SELECTED_TERMINAL_SHA256
            or selected.evidence_tree_root_sha256
            != OFFICIAL_SELECTED_EVIDENCE_ROOT_SHA256
            or history.history_root_sha256
            != OFFICIAL_SEALED_HISTORY_ROOT_SHA256
            or history.live_ledger_root_sha256
            != OFFICIAL_SEALED_LIVE_LEDGER_ROOT_SHA256
            or candidate.get("run_a_root_sha256")
            != OFFICIAL_SELECTED_RUN_ROOT_SHA256
            or candidate.get("run_b_root_sha256")
            != OFFICIAL_SELECTED_RUN_ROOT_SHA256
            or candidate.get("control_surface_root_sha256")
            != OFFICIAL_SELECTED_CONTROL_ROOT_SHA256
            or candidate.get("evidence_tree_root_sha256")
            != OFFICIAL_SELECTED_EVIDENCE_ROOT_SHA256
            or candidate.get("candidate_content_root_sha256")
            != OFFICIAL_SELECTED_CANDIDATE_CONTENT_ROOT_SHA256
        ):
            raise RehearsalV22Error("official sealed attempt-2 evidence drifted")
    live = _live_execution_anchor(binding, execution)
    exact_argv = tuple(cast(list[str], exact_argv_raw))
    exact_environment = dict(cast(dict[str, str], exact_environment_raw))
    expected_authority_path = root.joinpath(*PurePosixPath(authority.path).parts)
    if binding.action_authorization_path != expected_authority_path:
        raise RehearsalV22Error("recovery binding names a different authority path")
    observed_process_argv = (Path(sys.executable).absolute().as_posix(), *sys.orig_argv[1:])
    if require_current_process and (
        observed_process_argv != exact_argv or dict(os.environ) != exact_environment
    ):
        raise RehearsalV22Error("recovery authorization differs from this OS process")
    execution_review = _validate_authority_ref_shape(
        execution.get("independent_implementation_review"),
        "recovery execution review",
    )
    if not _git_is_ancestor(root, execution_review.creating_commit, authority.creating_commit):
        raise RehearsalV22Error("recovery authorization predates execution review")
    if not _git_is_ancestor(
        root,
        live.landing_report.creating_commit,
        authority.creating_commit,
    ):
        raise RehearsalV22Error("recovery authorization predates live epoch landing")
    return BundleRecoveryAuthorization(
        path=expected_authority_path,
        payload=payload,
        sha256=authority.sha256,
        creating_commit=authority.creating_commit,
        authorization_id=cast(str, document["authorization_id"]),
        sealed_series=dict(sealed),
        execution_epoch=dict(execution),
        destination=dict(destination),
        exact_argv=exact_argv,
        command_sha256=cast(str, document["command_sha256"]),
        exact_environment=exact_environment,
        environment_sha256=cast(str, document["environment_sha256"]),
        effect_authorization=cast(dict[str, bool], dict(effects)),
        interpreter=cast(dict[str, str], dict(interpreter)),
        locks=cast(dict[str, bool], dict(locks)),
    )


def _validated_attempt(
    binding: ExecutionBinding,
    *,
    ordinal: int,
    previous_history_root: str,
) -> ValidatedAttemptRecord:
    attempt_root = binding.ledger_root / "attempts" / f"{ordinal:06d}"
    if not attempt_root.is_dir() or attempt_root.is_symlink():
        raise RehearsalV22Error("attempt directory is unavailable or aliased")
    started, started_bytes, started_sha = _canonical_object_file(
        attempt_root / "started.json",
        label=f"attempt {ordinal} started.json",
        exact_fields=STARTED_FIELDS,
    )
    expected_token = _attempt_token_sha256(
        series_token_sha256=binding.series_token_sha256,
        ordinal=ordinal,
        implementation_commit=cast(str, started.get("implementation_commit")),
        previous_history_root_sha256=previous_history_root,
    )
    command = started.get("command")
    environment = started.get("environment")
    epoch = started.get("implementation_epoch")
    created = started.get("created_at_utc")
    if (
        started.get("schema_version") != "p4.2a-v2-2-rehearsal-attempt-started-v1"
        or started.get("series_id") != REHEARSAL_ID
        or started.get("series_token_sha256") != binding.series_token_sha256
        or started.get("ordinal") != ordinal
        or started.get("attempt_token_sha256") != expected_token
        or started.get("previous_history_root_sha256") != previous_history_root
        or isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or epoch < 1
        or not _lower_hex(started.get("implementation_commit"), 40)
        or not _lower_hex(started.get("control_merkle_root_sha256"), 64)
        or not isinstance(command, list)
        or not command
        or any(not isinstance(item, str) or not item for item in command)
        or started.get("command_sha256") != _command_sha256(cast(list[str], command))
        or not isinstance(environment, dict)
        or environment != EXACT_ENVIRONMENT
        or started.get("environment_sha256")
        != _environment_sha256(cast(Mapping[str, str], environment))
        or started.get("interpreter_path") != FIXED_PYTHON_LAUNCHER.as_posix()
        or started.get("interpreter_sha256") != FIXED_PYTHON_SHA256
        or not isinstance(created, str)
        or RFC3339_UTC_SECONDS.fullmatch(created) is None
    ):
        raise RehearsalV22Error(f"attempt {ordinal} started binding drifted")
    authorization = _validate_authority_ref_shape(
        started.get("owner_action_time_authorization"),
        f"attempt {ordinal} owner action authorization",
    )
    validated_authorization = _validate_action_authorization(
        binding,
        authorization,
        expected_ordinal=ordinal,
        expected_previous_history_root_sha256=previous_history_root,
        require_current_process=False,
    )
    if (
        tuple(cast(list[str], command)) != validated_authorization.exact_argv
        or environment != validated_authorization.exact_environment
        or epoch != validated_authorization.implementation_epoch
        or started.get("implementation_commit") != validated_authorization.implementation_commit
        or started.get("control_merkle_root_sha256")
        != validated_authorization.control_merkle_root_sha256
        or started.get("command_sha256") != validated_authorization.command_sha256
        or started.get("environment_sha256") != validated_authorization.environment_sha256
    ):
        raise RehearsalV22Error(f"attempt {ordinal} differs from its action-time authorization")
    evidence_root, inventory = _inventory_from_evidence(attempt_root / "evidence")
    candidate_path = attempt_root / "candidate.json"
    terminal_path = attempt_root / "terminal.json"
    candidate: JsonObject | None = None
    candidate_bytes: bytes | None = None
    candidate_sha: str | None = None
    terminal: JsonObject | None = None
    terminal_bytes: bytes | None = None
    terminal_sha: str | None = None
    if os.path.lexists(candidate_path):
        candidate, candidate_bytes, candidate_sha = _canonical_object_file(
            candidate_path,
            label=f"attempt {ordinal} candidate.json",
            exact_fields=CANDIDATE_FIELDS,
        )
    if os.path.lexists(terminal_path):
        terminal, terminal_bytes, terminal_sha = _canonical_object_file(
            terminal_path,
            label=f"attempt {ordinal} terminal.json",
            exact_fields=TERMINAL_FIELDS,
        )
    if candidate is not None and terminal is None:
        raise RehearsalV22Error(
            "candidate receipt exists without terminal; owner recovery ruling is required"
        )
    if terminal is None:
        outcome: AttemptOutcome = "INCOMPLETE_UNTERMINALIZED"
        reached_stage = "started_without_terminal"
        error: JsonObject | None = None
    else:
        outcome_value = terminal.get("outcome")
        if outcome_value not in {"FAILED", "CANDIDATE_VALIDATED_AND_SELECTED"}:
            raise RehearsalV22Error(f"attempt {ordinal} terminal outcome is invalid")
        outcome = cast(AttemptOutcome, outcome_value)
        reached_stage_value = terminal.get("reached_stage")
        if not isinstance(reached_stage_value, str) or not reached_stage_value:
            raise RehearsalV22Error(f"attempt {ordinal} reached_stage is invalid")
        reached_stage = reached_stage_value
        error_value = terminal.get("error")
        error = cast(JsonObject | None, error_value)
        expected_inventory = list(inventory)
        if (
            terminal.get("schema_version") != "p4.2a-v2-2-rehearsal-attempt-terminal-v1"
            or terminal.get("series_id") != REHEARSAL_ID
            or terminal.get("ordinal") != ordinal
            or terminal.get("attempt_token_sha256") != expected_token
            or terminal.get("implementation_epoch") != epoch
            or terminal.get("implementation_commit") != started.get("implementation_commit")
            or terminal.get("automatic_retry_count") != 0
            or terminal.get("artifact_inventory") != expected_inventory
            or terminal.get("evidence_tree_root_sha256") != evidence_root
            or not isinstance(terminal.get("completed_at_utc"), str)
            or RFC3339_UTC_SECONDS.fullmatch(cast(str, terminal["completed_at_utc"])) is None
        ):
            raise RehearsalV22Error(f"attempt {ordinal} terminal binding drifted")
        if outcome == "FAILED":
            if (
                candidate is not None
                or not isinstance(error, dict)
                or set(error)
                != {
                    "exception_type",
                    "message_sha256",
                    "failing_stage",
                }
            ):
                raise RehearsalV22Error(f"attempt {ordinal} FAILED shape drifted")
            if (
                not isinstance(error.get("exception_type"), str)
                or not error.get("exception_type")
                or not _lower_hex(error.get("message_sha256"), 64)
                or not isinstance(error.get("failing_stage"), str)
                or not error.get("failing_stage")
            ):
                raise RehearsalV22Error(f"attempt {ordinal} error shape drifted")
        elif candidate is None or error is not None:
            raise RehearsalV22Error(f"attempt {ordinal} selected outcome shape drifted")
    if candidate is not None:
        expected_candidate_content = _candidate_content_root_sha256(
            previous_history_root_sha256=previous_history_root,
            run_a_root_sha256=cast(str, candidate.get("run_a_root_sha256")),
            run_b_root_sha256=cast(str, candidate.get("run_b_root_sha256")),
            control_surface_root_sha256=cast(str, candidate.get("control_surface_root_sha256")),
            evidence_tree_root_sha256=evidence_root,
        )
        if (
            candidate.get("schema_version") != "p4.2a-v2-2-rehearsal-attempt-candidate-v1"
            or candidate.get("series_id") != REHEARSAL_ID
            or candidate.get("ordinal") != ordinal
            or candidate.get("attempt_token_sha256") != expected_token
            or candidate.get("implementation_epoch") != epoch
            or candidate.get("implementation_commit") != started.get("implementation_commit")
            or candidate.get("evidence_tree_root_sha256") != evidence_root
            or candidate.get("candidate_content_root_sha256") != expected_candidate_content
            or not isinstance(candidate.get("validated_at_utc"), str)
            or RFC3339_UTC_SECONDS.fullmatch(cast(str, candidate["validated_at_utc"])) is None
        ):
            raise RehearsalV22Error(f"attempt {ordinal} candidate binding drifted")
    record_root = _attempt_record_root_sha256(
        ordinal=ordinal,
        attempt_token_sha256=expected_token,
        started_sha256=started_sha,
        candidate_sha256=candidate_sha,
        terminal_sha256=terminal_sha,
        evidence_tree_root_sha256=evidence_root,
    )
    history_root = _history_step_sha256(previous_history_root, record_root)
    return ValidatedAttemptRecord(
        ordinal=ordinal,
        outcome=outcome,
        reached_stage=reached_stage,
        attempt_token_sha256=expected_token,
        previous_history_root_sha256=previous_history_root,
        implementation_epoch=epoch,
        implementation_commit=cast(str, started["implementation_commit"]),
        owner_action_time_authorization=authorization,
        command_sha256=cast(str, started["command_sha256"]),
        environment_sha256=cast(str, started["environment_sha256"]),
        started_path=attempt_root / "started.json",
        started_bytes=started_bytes,
        started_sha256=started_sha,
        candidate_path=candidate_path if candidate is not None else None,
        candidate_bytes=candidate_bytes,
        candidate_sha256=candidate_sha,
        terminal_path=terminal_path if terminal is not None else None,
        terminal_bytes=terminal_bytes,
        terminal_sha256=terminal_sha,
        evidence_tree_root_sha256=evidence_root,
        artifact_inventory=inventory,
        error=error,
        record_root_sha256=record_root,
        history_root_sha256=history_root,
    )


def validate_live_history(binding: ExecutionBinding) -> HistoryValidation:
    """Strictly replay every live attempt byte and recompute both history roots."""

    root = binding.ledger_root
    if not os.path.lexists(root):
        return HistoryValidation(
            binding=binding,
            ledger_exists=False,
            records=(),
            started_count=0,
            failed_count=0,
            incomplete_count=0,
            validated_candidate_count=0,
            selected_attempt_ordinal=None,
            series_closed=False,
            history_root_sha256=_history_empty_root_sha256(),
            live_ledger_root_sha256=None,
            live_file_inventory=(),
        )
    metadata = root.lstat()
    if (
        root.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or root.resolve(strict=True) != root.absolute()
    ):
        raise RehearsalV22Error("series ledger root is aliased or has wrong mode")
    _validate_series_document(binding)
    attempts_root = root / "attempts"
    attempts_metadata = attempts_root.lstat()
    if (
        attempts_root.is_symlink()
        or not stat.S_ISDIR(attempts_metadata.st_mode)
        or stat.S_IMODE(attempts_metadata.st_mode) != 0o700
    ):
        raise RehearsalV22Error("series attempts directory drifted")
    names = sorted(path.name for path in attempts_root.iterdir())
    if any(re.fullmatch(r"[0-9]{6}", name) is None for name in names):
        raise RehearsalV22Error("series attempts directory contains an unexpected entry")
    expected_names = [f"{ordinal:06d}" for ordinal in range(1, len(names) + 1)]
    if names != expected_names:
        raise RehearsalV22Error("attempt ordinals contain a gap, duplicate, or reorder")
    records: list[ValidatedAttemptRecord] = []
    previous = _history_empty_root_sha256()
    selected: int | None = None
    for ordinal in range(1, len(names) + 1):
        record = _validated_attempt(binding, ordinal=ordinal, previous_history_root=previous)
        if selected is not None:
            raise RehearsalV22Error("attempt exists after first validated candidate")
        if record.outcome == "CANDIDATE_VALIDATED_AND_SELECTED":
            selected = ordinal
        records.append(record)
        previous = record.history_root_sha256
    live_root, inventory = _live_ledger_root(binding)
    failed = sum(record.outcome == "FAILED" for record in records)
    incomplete = sum(record.outcome == "INCOMPLETE_UNTERMINALIZED" for record in records)
    return HistoryValidation(
        binding=binding,
        ledger_exists=True,
        records=tuple(records),
        started_count=len(records),
        failed_count=failed,
        incomplete_count=incomplete,
        validated_candidate_count=1 if selected is not None else 0,
        selected_attempt_ordinal=selected,
        series_closed=selected is not None,
        history_root_sha256=previous,
        live_ledger_root_sha256=live_root,
        live_file_inventory=inventory,
    )


def _command_sha256(argv: Sequence[str]) -> str:
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise RehearsalV22Error("command hash requires nonempty string arguments")
    return _sha256(b"p4.2a-v2.2-argv-v1\0" + b"\0".join(item.encode("utf-8") for item in argv))


def _environment_sha256(environment: Mapping[str, str]) -> str:
    if any(
        not isinstance(key, str) or not key or not isinstance(value, str)
        for key, value in environment.items()
    ):
        raise RehearsalV22Error("environment hash requires string keys and values")
    payload = bytearray(b"p4.2a-v2.2-env-v1\0")
    for key in sorted(environment, key=lambda value: value.encode("utf-8")):
        payload.extend(key.encode("utf-8"))
        payload.append(0)
        payload.extend(environment[key].encode("utf-8"))
        payload.append(0)
    return _sha256(bytes(payload))


def _candidate_content_root_sha256(
    *,
    previous_history_root_sha256: str,
    run_a_root_sha256: str,
    run_b_root_sha256: str,
    control_surface_root_sha256: str,
    evidence_tree_root_sha256: str,
) -> str:
    values = (
        previous_history_root_sha256,
        run_a_root_sha256,
        run_b_root_sha256,
        control_surface_root_sha256,
        evidence_tree_root_sha256,
    )
    if any(not _lower_hex(value, 64) for value in values):
        raise RehearsalV22Error("candidate-content formula input is invalid")
    return _sha256(
        b"p4.2a-rehearsal-v2.2-candidate-content-v1\0"
        + b"".join(bytes.fromhex(value) for value in values)
    )


@dataclass
class AttemptLease:
    ledger: SeriesLedger
    ordinal: int
    attempt_root: Path
    evidence_root: Path
    action_authorization: ActionAuthorization
    attempt_token_sha256: str
    previous_history_root_sha256: str
    frozen: bool = False
    terminal_written: bool = False
    candidate_written: bool = False
    reached_stage: str = "started_persisted"

    def _set_write_phase(
        self,
        phase: Literal["active", "candidate", "frozen"],
    ) -> None:
        observed = _AUDIT_POLICY.get()
        if (
            observed is None
            or observed.ledger_root != self.ledger.binding.ledger_root
            or observed.active_attempt_root != self.attempt_root
            or self.ledger.write_policy_scope is None
        ):
            raise RehearsalV22Error("attempt write authority is absent or cross-attempt")
        self.ledger.write_policy_scope.__exit__(None, None, None)
        restored = _AUDIT_POLICY.get()
        if restored is None:
            raise RehearsalV22Error("outer audit policy vanished during attempt transition")
        scope = _audited_execution(
            replace(
                restored,
                ledger_write_phase=phase,
                ledger_root=self.ledger.binding.ledger_root,
                active_attempt_root=self.attempt_root,
            )
        )
        scope.__enter__()
        self.ledger.write_policy_scope = scope

    def persist_evidence(self, relative: str, payload: bytes) -> Path:
        if self.frozen:
            raise RehearsalV22Error("attempt evidence is immutable after candidate or terminal")
        normalized = _relative_text(relative, "attempt evidence path")
        path = self.evidence_root.joinpath(*PurePosixPath(normalized).parts)
        if not path.absolute().is_relative_to(self.evidence_root.absolute()):
            raise RehearsalV22Error("attempt evidence path escapes")
        parent = path.parent
        cursor = self.evidence_root
        for part in parent.relative_to(self.evidence_root).parts:
            cursor /= part
            if os.path.lexists(cursor):
                metadata = cursor.lstat()
                if (
                    cursor.is_symlink()
                    or not stat.S_ISDIR(metadata.st_mode)
                    or stat.S_IMODE(metadata.st_mode) != 0o700
                    or cursor.resolve(strict=True) != cursor.absolute()
                ):
                    raise RehearsalV22Error("attempt evidence directory drifted")
                continue
            os.mkdir(cursor, 0o700)
            _fsync_directory(cursor.parent)
        _write_exclusive(path, payload, mode=0o600)
        return path

    def write_candidate(
        self,
        *,
        run_a_root_sha256: str,
        run_b_root_sha256: str,
        control_surface_root_sha256: str,
        validated_at_utc: str,
    ) -> Path:
        if self.frozen or self.candidate_written or self.terminal_written:
            raise RehearsalV22Error("attempt candidate may be written exactly once")
        evidence_root = _evidence_tree_root(self.evidence_root)
        payload = {
            "schema_version": "p4.2a-v2-2-rehearsal-attempt-candidate-v1",
            "series_id": REHEARSAL_ID,
            "ordinal": self.ordinal,
            "attempt_token_sha256": self.attempt_token_sha256,
            "implementation_epoch": self.action_authorization.implementation_epoch,
            "implementation_commit": self.action_authorization.implementation_commit,
            "run_a_root_sha256": run_a_root_sha256,
            "run_b_root_sha256": run_b_root_sha256,
            "control_surface_root_sha256": control_surface_root_sha256,
            "evidence_tree_root_sha256": evidence_root,
            "candidate_content_root_sha256": _candidate_content_root_sha256(
                previous_history_root_sha256=self.previous_history_root_sha256,
                run_a_root_sha256=run_a_root_sha256,
                run_b_root_sha256=run_b_root_sha256,
                control_surface_root_sha256=control_surface_root_sha256,
                evidence_tree_root_sha256=evidence_root,
            ),
            "validated_at_utc": validated_at_utc,
        }
        path = self.attempt_root / "candidate.json"
        _write_exclusive(path, _canonical_json_bytes(payload), mode=0o600)
        self.candidate_written = True
        self.frozen = True
        self._set_write_phase("candidate")
        return path

    def write_terminal(
        self,
        *,
        outcome: Literal["FAILED", "CANDIDATE_VALIDATED_AND_SELECTED"],
        reached_stage: str,
        completed_at_utc: str,
        error: BaseException | None = None,
    ) -> Path:
        if self.terminal_written:
            raise RehearsalV22Error("attempt terminal may be written exactly once")
        if outcome == "CANDIDATE_VALIDATED_AND_SELECTED":
            if not self.candidate_written or error is not None:
                raise RehearsalV22Error("selected terminal requires candidate and null error")
            error_value: JsonObject | None = None
        else:
            if self.candidate_written or error is None:
                raise RehearsalV22Error("failed terminal requires no candidate and one error")
            error_value = {
                "exception_type": type(error).__name__,
                "message_sha256": _sha256(str(error).encode("utf-8")),
                "failing_stage": reached_stage,
            }
        evidence_root, inventory = _inventory_from_evidence(self.evidence_root)
        payload = {
            "schema_version": "p4.2a-v2-2-rehearsal-attempt-terminal-v1",
            "series_id": REHEARSAL_ID,
            "ordinal": self.ordinal,
            "attempt_token_sha256": self.attempt_token_sha256,
            "outcome": outcome,
            "reached_stage": reached_stage,
            "implementation_epoch": self.action_authorization.implementation_epoch,
            "implementation_commit": self.action_authorization.implementation_commit,
            "automatic_retry_count": 0,
            "artifact_inventory": list(inventory),
            "error": error_value,
            "evidence_tree_root_sha256": evidence_root,
            "completed_at_utc": completed_at_utc,
        }
        path = self.attempt_root / "terminal.json"
        _write_exclusive(path, _canonical_json_bytes(payload), mode=0o600)
        self.terminal_written = True
        self.frozen = True
        self._set_write_phase("frozen")
        return path


@dataclass
class SeriesLedger:
    binding: ExecutionBinding
    execution_context: ExecutionCapability
    lock_descriptor: int | None = None
    locked: bool = False
    active_lease: AttemptLease | None = None
    write_policy_scope: Any | None = None

    @classmethod
    def open(
        cls,
        binding: ExecutionBinding,
        *,
        execution_context: ExecutionCapability,
        created_at_utc: str,
    ) -> SeriesLedger:
        observed = _validate_execution_capability(
            execution_context,
            project_root=binding.project_root,
        )
        if observed != binding:
            raise RehearsalV22Error("series ledger binding differs from capability")
        root = binding.ledger_root
        if not os.path.lexists(root):
            policy = _AUDIT_POLICY.get()
            if policy is None:
                raise RehearsalV22Error("series initialization lacks an audit policy")
            with _audited_execution(
                replace(
                    policy,
                    ledger_write_phase="initialize",
                    ledger_root=root,
                    active_attempt_root=None,
                )
            ):
                os.mkdir(root, 0o700)
                _fsync_directory(root.parent)
                _write_exclusive(
                    root / "series.json",
                    _canonical_json_bytes(_series_document(binding, created_at_utc=created_at_utc)),
                    mode=0o600,
                )
                _write_exclusive(root / ".series.lock", b"", mode=0o600)
                os.mkdir(root / "attempts", 0o700)
                _fsync_directory(root)
        validate_live_history(binding)
        return cls(binding=binding, execution_context=execution_context)

    def __enter__(self) -> SeriesLedger:
        if self.locked:
            raise RehearsalV22Error("series ledger lock is already held")
        path = self.binding.ledger_root / ".series.lock"
        descriptor = os.open(path, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            os.close(descriptor)
            raise RehearsalV22Error("another v2.2 rehearsal attempt holds the series lock") from exc
        self.lock_descriptor = descriptor
        self.locked = True
        validate_live_history(self.binding)
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        persistence_error: BaseException | None = None
        lease = self.active_lease
        try:
            if (
                isinstance(_value, BaseException)
                and lease is not None
                and not lease.candidate_written
                and not lease.terminal_written
            ):
                try:
                    lease.persist_evidence(
                        "failure/exception.json",
                        _canonical_json_bytes(
                            {
                                "schema_version": ("p4.2a-v2-2-attempt-failure-evidence-v1"),
                                "exception_type": type(_value).__name__,
                                "message_sha256": _sha256(str(_value).encode("utf-8")),
                                "failing_stage": lease.reached_stage,
                            }
                        ),
                    )
                    lease.write_terminal(
                        outcome="FAILED",
                        reached_stage=lease.reached_stage,
                        completed_at_utc=FIXED_WALL_CLOCK_TEXT,
                        error=_value,
                    )
                except BaseException as exc:
                    persistence_error = exc
        finally:
            if self.write_policy_scope is not None:
                self.write_policy_scope.__exit__(None, None, None)
                self.write_policy_scope = None
            descriptor = self.lock_descriptor
            if descriptor is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            self.lock_descriptor = None
            self.locked = False
            self.active_lease = None
        if persistence_error is not None:
            raise RehearsalV22Error(
                "attempt failure evidence could not be durably terminalized"
            ) from persistence_error

    def allocate_attempt(
        self,
        action_authorization: ActionAuthorization,
        *,
        created_at_utc: str,
    ) -> AttemptLease:
        if not self.locked or self.lock_descriptor is None:
            raise RehearsalV22Error("attempt allocation requires the held series lock")
        if self.active_lease is not None:
            raise RehearsalV22Error("one process may allocate only one attempt")
        history = validate_live_history(self.binding)
        if history.series_closed:
            raise RehearsalV22Error("first validated candidate already closed the series")
        ordinal = len(history.records) + 1
        if (
            action_authorization.ordinal != ordinal
            or action_authorization.previous_history_root_sha256 != history.history_root_sha256
            or action_authorization.path != self.binding.action_authorization_path
        ):
            raise RehearsalV22Error("action authorization does not bind the next live ordinal")
        attempt_token = _attempt_token_sha256(
            series_token_sha256=self.binding.series_token_sha256,
            ordinal=ordinal,
            implementation_commit=action_authorization.implementation_commit,
            previous_history_root_sha256=history.history_root_sha256,
        )
        attempt_root = self.binding.ledger_root / "attempts" / f"{ordinal:06d}"
        policy = _AUDIT_POLICY.get()
        if policy is None:
            raise RehearsalV22Error("attempt allocation lacks an audit policy")
        write_scope = _audited_execution(
            replace(
                policy,
                ledger_write_phase="active",
                ledger_root=self.binding.ledger_root,
                active_attempt_root=attempt_root,
            )
        )
        write_scope.__enter__()
        self.write_policy_scope = write_scope
        try:
            os.mkdir(attempt_root, 0o700)
            _fsync_directory(attempt_root.parent)
        except BaseException:
            write_scope.__exit__(*sys.exc_info())
            self.write_policy_scope = None
            raise
        started = {
            "schema_version": "p4.2a-v2-2-rehearsal-attempt-started-v1",
            "series_id": REHEARSAL_ID,
            "series_token_sha256": self.binding.series_token_sha256,
            "ordinal": ordinal,
            "attempt_token_sha256": attempt_token,
            "previous_history_root_sha256": history.history_root_sha256,
            "implementation_epoch": action_authorization.implementation_epoch,
            "implementation_commit": action_authorization.implementation_commit,
            "owner_action_time_authorization": action_authorization.authority_ref(
                self.binding.project_root
            ).as_json(),
            "control_merkle_root_sha256": action_authorization.control_merkle_root_sha256,
            "command": list(action_authorization.exact_argv),
            "command_sha256": action_authorization.command_sha256,
            "environment": dict(action_authorization.exact_environment),
            "environment_sha256": action_authorization.environment_sha256,
            "interpreter_path": FIXED_PYTHON_LAUNCHER.as_posix(),
            "interpreter_sha256": FIXED_PYTHON_SHA256,
            "created_at_utc": created_at_utc,
        }
        _write_exclusive(
            attempt_root / "started.json",
            _canonical_json_bytes(started),
            mode=0o600,
        )
        evidence_root = attempt_root / "evidence"
        os.mkdir(evidence_root, 0o700)
        _fsync_directory(attempt_root)
        lease = AttemptLease(
            ledger=self,
            ordinal=ordinal,
            attempt_root=attempt_root,
            evidence_root=evidence_root,
            action_authorization=action_authorization,
            attempt_token_sha256=attempt_token,
            previous_history_root_sha256=history.history_root_sha256,
        )
        self.active_lease = lease
        return lease


@dataclass
class DeterministicClock:
    seconds: float = MONOTONIC_INITIAL_SECONDS

    def monotonic(self) -> float:
        return self.seconds

    def sleep(self, duration: float) -> None:
        if not isinstance(duration, (int, float)) or duration < 0:
            raise RehearsalV22Error("deterministic sleeper received an invalid duration")
        self.seconds += float(duration)


@dataclass(frozen=True)
class InferenceHarness:
    settings: Settings
    chat_json_fn: Callable[..., dict[str, Any]]
    snapshot_loader: Callable[[Path], ProductionSnapshot]
    wall_clock: Clock
    execution_id_factory: Callable[[], str]
    prediction_recorded_at_clock: RecordedAtClock
    prediction_monotonic_ns_clock: MonotonicNsClock
    calls: list[int]


@dataclass(frozen=True)
class PipelineReplay:
    run_label: str
    artifacts: Mapping[str, bytes]
    probe_evidence: Mapping[str, JsonObject]
    write_root: Path
    removed: bool


@dataclass(frozen=True)
class ControlSurface:
    implementation_commit: str
    records: tuple[JsonObject, ...]
    payloads: Mapping[str, bytes]
    manifest_payload: bytes
    merkle_root_sha256: str
    ast_closure_paths: tuple[str, ...]
    loaded_repository_sources: tuple[str, ...]
    python_inventory: bytes
    package_inventory: bytes


@dataclass(frozen=True)
class HistoricalSelectedAnchor:
    """Immutable selected-attempt evidence; never authorizes current bytes."""

    selected_epoch: int
    selected_commit: str
    owner_action_time_authorization: AuthorityReference
    owner_surface_authorization: AuthorityReference
    independent_implementation_review: AuthorityReference
    control_surface: ControlSurface
    history_root_sha256: str
    live_ledger_root_sha256: str
    evidence_tree_root_sha256: str
    candidate_content_root_sha256: str
    run_a_root_sha256: str
    run_b_root_sha256: str
    selected_git_blob_sha256: Mapping[str, str]


@dataclass(frozen=True)
class LiveExecutionAnchor:
    """Current reviewed execution bytes; never supplies selected archive bytes."""

    execution_epoch: int
    implementation_commit: str
    owner_surface_authorization: AuthorityReference
    independent_implementation_review: AuthorityReference
    merge_commit: str
    landing_report: AuthorityReference
    control_surface: ControlSurface
    execution_head: str
    loaded_module_sha256: Mapping[str, str]


@dataclass(frozen=True)
class BundleRecoveryAuthorization:
    path: Path
    payload: bytes
    sha256: str
    creating_commit: str
    authorization_id: str
    sealed_series: Mapping[str, Any]
    execution_epoch: Mapping[str, Any]
    destination: Mapping[str, Any]
    exact_argv: tuple[str, ...]
    command_sha256: str
    exact_environment: Mapping[str, str]
    environment_sha256: str
    effect_authorization: Mapping[str, bool]
    interpreter: Mapping[str, str]
    locks: Mapping[str, bool]

    def authority_ref(self, project_root: Path) -> AuthorityReference:
        return AuthorityReference(
            path=self.path.relative_to(project_root).as_posix(),
            sha256=self.sha256,
            creating_commit=self.creating_commit,
        )


@dataclass(frozen=True)
class _SealedPipelineReplay:
    """Read-only archive material with no active pipeline/workspace capability."""

    run_label: str
    artifacts: Mapping[str, bytes]
    probe_evidence: Mapping[str, JsonObject]


@dataclass(frozen=True)
class RecoveryExecutionCapability:
    _nonce: object
    binding: ExecutionBinding
    bootstrap: _BootstrapEvidence
    authorization: BundleRecoveryAuthorization
    historical_anchor: HistoricalSelectedAnchor
    live_anchor: LiveExecutionAnchor
    claim_root: Path
    temporary_authority: Path
    audit_policy: _AuditPolicy
    audit_policy_id: int


@dataclass(frozen=True)
class RecoveryValidatorDelegation:
    _nonce: object
    binding: ExecutionBinding
    capability_id: int
    validator_module_id: int
    audit_policy_id: int
    temporary_authority: Path
    bundle_path: Path
    bundle_sha256: str
    creator_module_id: int
    lifetime_id: int


@dataclass(frozen=True)
class RecoveredReleaseCapability:
    _nonce: object
    binding: ExecutionBinding
    authorization: BundleRecoveryAuthorization
    historical_anchor: HistoricalSelectedAnchor
    live_anchor: LiveExecutionAnchor
    claim_root: Path
    terminal_sha256: str
    bundle_path: Path
    bundle_sha256: str
    validator_module_id: int


@dataclass(frozen=True)
class RecoveredReleaseValidatorDelegation:
    _nonce: object
    binding: ExecutionBinding
    capability_id: int
    validator_module_id: int
    audit_policy_id: int
    bundle_path: Path
    bundle_sha256: str
    terminal_sha256: str
    creator_module_id: int
    lifetime_id: int


_ALLOWED_ORIGINLESS_RUNTIME_MODULES = frozenset(
    {"_cython_3_1_4", "_cython_3_2_4", "_cython_3_2_5", "cython_runtime"}
)


def _expected_sys_path(project_root: Path) -> tuple[str, ...]:
    if sys.version_info[:2] != (3, 12):
        raise RehearsalV22Error("v2.2 requires frozen CPython 3.12")
    stdlib = Path(sys.base_prefix) / "lib/python3.12"
    candidates = (
        stdlib.parent / "python312.zip",
        stdlib,
        stdlib / "lib-dynload",
        REGISTERED_PROJECT_ROOT / ".venv/lib/python3.12/site-packages",
        project_root,
        project_root / "src",
    )
    result: list[str] = []
    for candidate in candidates:
        value = candidate.absolute().as_posix()
        if value not in result:
            result.append(value)
    return tuple(result)


def _resolved_module_path(raw: str, *, directory: bool, label: str) -> Path:
    candidate = Path(raw).absolute()
    try:
        resolved = candidate.resolve(strict=True)
        metadata = resolved.lstat()
    except OSError as exc:
        raise RehearsalV22Error(f"{label} is unavailable") from exc
    expected_kind = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if candidate != resolved or not expected_kind:
        raise RehearsalV22Error(f"{label} is aliased or has the wrong kind")
    return resolved


def _repository_module_files(
    module_name: str,
    *,
    project_root: Path,
    shim_path: Path,
) -> frozenset[Path]:
    if module_name == "__main__":
        return frozenset({shim_path, project_root / VALIDATOR_RELATIVE})
    if module_name.startswith("scripts."):
        base = project_root.joinpath(*module_name.split("."))
    elif module_name == "alphapilot" or module_name.startswith("alphapilot."):
        base = (project_root / "src").joinpath(*module_name.split("."))
    else:
        return frozenset()
    return frozenset({base.with_suffix(".py"), base / "__init__.py"})


def _repository_module_directory(module_name: str, *, project_root: Path) -> Path | None:
    if module_name == "scripts" or module_name.startswith("scripts."):
        return project_root.joinpath(*module_name.split("."))
    if module_name == "alphapilot" or module_name.startswith("alphapilot."):
        return (project_root / "src").joinpath(*module_name.split("."))
    return None


def _classify_loaded_module_origins(project_root: Path) -> frozenset[str]:
    project_root = project_root.resolve(strict=True)
    shim_path = (project_root / SHIM_RELATIVE).resolve(strict=True)
    site_root = (REGISTERED_PROJECT_ROOT / ".venv/lib/python3.12/site-packages").resolve(
        strict=True
    )
    configured = sysconfig.get_paths()
    stdlib_roots = tuple(
        dict.fromkeys(
            Path(configured[name]).resolve(strict=True) for name in ("stdlib", "platstdlib")
        )
    )
    repository_paths: set[str] = set()
    typing_module = sys.modules.get("typing")

    def classify(path: Path, *, module_name: str, directory: bool) -> None:
        reserved = (
            module_name == "__main__"
            or module_name == "scripts"
            or module_name.startswith("scripts.")
            or module_name == "alphapilot"
            or module_name.startswith("alphapilot.")
        )
        if reserved:
            if directory:
                expected = _repository_module_directory(
                    module_name,
                    project_root=project_root,
                )
                if expected is None or path != expected:
                    raise RehearsalV22Error(
                        f"repository package path is not registered: {module_name}"
                    )
                return
            if path not in _repository_module_files(
                module_name,
                project_root=project_root,
                shim_path=shim_path,
            ):
                raise RehearsalV22Error(
                    f"repository module origin is not registered: {module_name}"
                )
            repository_paths.add(path.relative_to(project_root).as_posix())
            return
        if path.is_relative_to(site_root):
            return
        if any(path.is_relative_to(root) for root in stdlib_roots):
            return
        if path.is_relative_to(project_root):
            raise RehearsalV22Error(f"unregistered namespace loaded from repository: {module_name}")
        raise RehearsalV22Error(f"module origin escaped frozen roots: {module_name}")

    for module_name, module in sorted(sys.modules.items()):
        if module is None:
            continue
        if not isinstance(module, ModuleType):
            suffix = module_name.removeprefix("typing.")
            if (
                module_name not in {"typing.io", "typing.re"}
                or typing_module is None
                or module is not getattr(typing_module, suffix, None)
            ):
                raise RehearsalV22Error(f"sys.modules contains non-module entry: {module_name}")
            continue
        raw_origin = getattr(module, "__file__", None)
        spec = getattr(module, "__spec__", None)
        spec_origin = getattr(spec, "origin", None)
        raw_paths = getattr(module, "__path__", None)
        has_file = False
        if isinstance(raw_origin, str):
            if raw_origin.startswith("<"):
                raise RehearsalV22Error(f"synthetic module origin: {module_name}")
            classify(
                _resolved_module_path(
                    raw_origin,
                    directory=False,
                    label=f"module origin {module_name}",
                ),
                module_name=module_name,
                directory=False,
            )
            has_file = True
        elif raw_origin is not None:
            raise RehearsalV22Error(f"invalid module origin: {module_name}")
        elif spec_origin not in {None, "built-in", "frozen"}:
            raise RehearsalV22Error(f"invalid module spec origin: {module_name}")
        elif raw_paths is None and spec_origin is None:
            if module_name not in _ALLOWED_ORIGINLESS_RUNTIME_MODULES:
                raise RehearsalV22Error(f"originless runtime module: {module_name}")
        if raw_paths is None:
            continue
        try:
            paths = tuple(raw_paths)
        except TypeError as exc:
            raise RehearsalV22Error(f"invalid package path: {module_name}") from exc
        six_module = sys.modules.get("six")
        bound_six_moves = (
            module_name == "six.moves"
            and isinstance(six_module, ModuleType)
            and module is getattr(six_module, "moves", None)
        )
        if not paths and (has_file or bound_six_moves):
            continue
        if not paths:
            raise RehearsalV22Error(f"empty package path: {module_name}")
        for raw_path in paths:
            if not isinstance(raw_path, str):
                raise RehearsalV22Error(f"invalid package path: {module_name}")
            classify(
                _resolved_module_path(
                    raw_path,
                    directory=True,
                    label=f"package path {module_name}",
                ),
                module_name=module_name,
                directory=True,
            )
    return frozenset(repository_paths)


def _expected_validator_sys_path(project_root: Path) -> tuple[str, ...]:
    stdlib = Path(sys.base_prefix) / "lib/python3.12"
    candidates = (
        stdlib,
        stdlib / "lib-dynload",
        REGISTERED_PROJECT_ROOT / ".venv/lib/python3.12/site-packages",
        project_root,
        project_root / "src",
    )
    return tuple(dict.fromkeys(candidate.absolute().as_posix() for candidate in candidates))


def _locked_bootstrap_common(project_root: Path) -> tuple[ModuleType, Path]:
    main_module = sys.modules.get("__main__")
    if not isinstance(main_module, ModuleType):
        raise RehearsalV22Error("v2.2 locked bootstrap has no __main__ module")
    main_file = getattr(main_module, "__file__", None)
    if not isinstance(main_file, str):
        raise RehearsalV22Error("v2.2 locked bootstrap has no __main__ file")
    resolved_main = Path(main_file).resolve(strict=True)
    if (
        dict(os.environ) != EXACT_ENVIRONMENT
        or Path(sys.executable).absolute() != FIXED_PYTHON_LAUNCHER
        or _sha256(_fixed_launcher_bytes()) != FIXED_PYTHON_SHA256
        or Path(sys.orig_argv[0]).absolute() != FIXED_ORIG_ARGV_EXECUTABLE
        or _sha256(_regular_bytes(FIXED_ORIG_ARGV_EXECUTABLE, "fixed orig-argv executable"))
        != FIXED_ORIG_ARGV_EXECUTABLE_SHA256
        or sys.flags.no_site != 1
        or sys.flags.no_user_site != 1
        or not sys.flags.safe_path
        or sys.flags.hash_randomization != 0
        or not sys.dont_write_bytecode
        or sys.pycache_prefix != "/dev/null"
        or "sitecustomize" in sys.modules
        or "usercustomize" in sys.modules
    ):
        raise RehearsalV22Error("v2.2 implementation rejected an ambient bootstrap")
    return main_module, resolved_main


def _assert_locked_runner_bootstrap(project_root: Path) -> None:
    _main_module, resolved_main = _locked_bootstrap_common(project_root)
    shim = project_root / SHIM_RELATIVE
    expected_orig_argv = (
        FIXED_ORIG_ARGV_EXECUTABLE.as_posix(),
        "-S",
        "-P",
        "-B",
        *tuple(sys.argv),
    )
    if (
        resolved_main != shim
        or tuple(sys.orig_argv) != expected_orig_argv
        or tuple(sys.path) != _expected_sys_path(project_root)
    ):
        raise RehearsalV22Error("v2.2 runner rejected an ambient bootstrap")


def _assert_locked_validator_bootstrap(project_root: Path) -> None:
    main_module, resolved_main = _locked_bootstrap_common(project_root)
    validator = project_root / VALIDATOR_RELATIVE
    expected_argv = (validator.as_posix(),)
    expected_orig_argv = (
        FIXED_ORIG_ARGV_EXECUTABLE.as_posix(),
        "-S",
        "-P",
        "-B",
        validator.as_posix(),
    )
    if (
        main_module.__name__ != "__main__"
        or resolved_main != validator
        or tuple(sys.argv) != expected_argv
        or tuple(sys.orig_argv) != expected_orig_argv
        or tuple(sys.path) != _expected_validator_sys_path(project_root)
    ):
        raise RehearsalV22Error("v2.2 validator rejected an ambient bootstrap")


def _assert_locked_bootstrap(project_root: Path) -> None:
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    if not isinstance(main_file, str):
        raise RehearsalV22Error("v2.2 locked bootstrap has no __main__ file")
    resolved = Path(main_file).resolve(strict=True)
    if resolved == project_root / SHIM_RELATIVE:
        _assert_locked_runner_bootstrap(project_root)
        return
    if resolved == project_root / VALIDATOR_RELATIVE:
        _assert_locked_validator_bootstrap(project_root)
        return
    raise RehearsalV22Error("v2.2 locked bootstrap main path is unregistered")


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _v2_2_package_inventory() -> bytes:
    """Rebuild the frozen registered venv inventory independent of repo roots."""

    venv_root = REGISTERED_PROJECT_ROOT / ".venv"
    scheme = sysconfig.get_preferred_scheme("prefix")
    variables = {
        "base": venv_root.as_posix(),
        "platbase": venv_root.as_posix(),
    }
    selected: list[Path] = []
    for key in ("purelib", "platlib"):
        raw = sysconfig.get_path(key, scheme=scheme, vars=variables)
        if not isinstance(raw, str) or not raw:
            raise RehearsalV22Error(
                f"explicit registered package root is unavailable: {key}"
            )
        candidate = Path(raw).absolute()
        if candidate not in selected:
            selected.append(candidate)
    projected: list[str] = []
    for package_root in selected:
        try:
            metadata = package_root.lstat()
        except OSError as exc:
            raise RehearsalV22Error(
                "fixed registered package inventory root is unavailable"
            ) from exc
        if (
            package_root.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or package_root.resolve(strict=True) != package_root
            or not package_root.is_relative_to(REGISTERED_PROJECT_ROOT)
        ):
            raise RehearsalV22Error(
                "fixed registered package inventory root is aliased"
            )
        projected.append(
            package_root.relative_to(REGISTERED_PROJECT_ROOT).as_posix()
        )
    if projected != [PACKAGE_ROOT_RELATIVE.as_posix()] or _sha256(
        _canonical_json_bytes(projected)
    ) != PACKAGE_ROOTS_SHA256:
        raise RehearsalV22Error(
            "fixed registered package root projection drifted"
        )
    rows: list[JsonObject] = []
    names: list[str] = []
    for distribution in importlib.metadata.distributions(
        path=[path.as_posix() for path in selected]
    ):
        raw_name = distribution.metadata["Name"]
        if not isinstance(raw_name, str) or not raw_name:
            raise RehearsalV22Error(
                "registered package inventory contains an unnamed distribution"
            )
        name = _normalized_distribution_name(raw_name)
        names.append(name)
        rows.append({"name": name, "version": distribution.version})
    if len(names) != 84 or len(set(names)) != 84:
        raise RehearsalV22Error(
            "registered package inventory count or name uniqueness drifted"
        )
    rows.sort(key=lambda row: (cast(str, row["name"]), cast(str, row["version"])))
    payload = _canonical_json_bytes(rows)
    if _sha256(payload) != PACKAGE_INVENTORY_SHA256:
        raise RehearsalV22Error(
            "registered package inventory bytes drifted"
        )
    return payload


def _runtime_inventory(project_root: Path) -> tuple[bytes, bytes]:
    if not _lower_hex(PYTHON_INVENTORY_SHA256, 64) or not _lower_hex(
        PACKAGE_INVENTORY_SHA256,
        64,
    ):
        raise RehearsalV22Error("frozen runtime inventory digest constant is malformed")
    python_payload = base_runner._python_inventory()
    # Disposable roots deliberately reuse the byte-frozen registered venv.
    # This local implementation projects that runtime against the registered
    # root and never asks a synthetic-root helper to reinterpret it.
    package_payload = _v2_2_package_inventory()
    if (
        _sha256(python_payload) != PYTHON_INVENTORY_SHA256
        or _sha256(package_payload) != PACKAGE_INVENTORY_SHA256
    ):
        raise RehearsalV22Error("frozen Python or package inventory drifted")
    return python_payload, package_payload


def build_control_surface(
    project_root: Path,
    implementation_commit: str,
    *,
    require_current: bool = True,
) -> ControlSurface:
    """Rebuild the commit-bound control archive for one implementation epoch."""

    root = project_root.absolute()
    commit = _git_commit(root, implementation_commit, "control implementation commit")
    execution_head = _git_bytes(root, "rev-parse", "HEAD").decode("ascii", errors="strict").strip()
    validate_strict_v2_1_inheritance(
        root,
        implementation_commit=commit,
        require_current=require_current,
    )
    validate_carry_forward_lineage(
        root,
        execution_head=execution_head,
        implementation_commit=commit,
        require_current=require_current,
    )
    validate_unique_a_authority(
        root,
        AuthorityReference(
            INITIAL_SURFACE_REVIEW_RELATIVE.as_posix(),
            INITIAL_SURFACE_REVIEW_SHA256,
            INITIAL_SURFACE_REVIEW_COMMIT,
        ),
        execution_head=execution_head,
        allow_initial_sibling=True,
        require_current=require_current,
    )

    def blob_reader(relative: str) -> bytes:
        existence = _git_completed(root, "cat-file", "-e", f"{commit}:{relative}")
        if existence.returncode != 0:
            # The pure closure walker deliberately probes both ``module.py`` and
            # ``module/__init__.py``.  Translate only a missing candidate into
            # its registered sentinel; an object that exists is still read by
            # the strict Git helper so corruption and all other failures close.
            raise base_runner.RehearsalError(f"optional closure candidate is absent: {relative}")
        return _git_blob(root, commit, relative)

    closure: dict[str, bytes] = {}
    for entrypoint in (SHIM_RELATIVE, IMPLEMENTATION_RELATIVE, VALIDATOR_RELATIVE):
        closure.update(
            base_runner._local_import_closure(
                entrypoint=entrypoint.as_posix(),
                blob_reader=blob_reader,
            )
        )
    controls = set(closure)
    controls.update(path.as_posix() for path in IMPLEMENTATION_SURFACE)
    controls.update(
        {
            PREREGISTRATION_RELATIVE.as_posix(),
            BUNDLE_SCHEMA_RELATIVE.as_posix(),
            RELEASE_SCHEMA_RELATIVE.as_posix(),
            INCIDENT_RELATIVE.as_posix(),
            REMEDIATION_RELATIVE.as_posix(),
            SCOPE_AUTHORIZATION_RELATIVE.as_posix(),
            "pyproject.toml",
            "uv.lock",
            V2_1_BUNDLE_SCHEMA_RELATIVE.as_posix(),
            V2_1_RELEASE_SCHEMA_RELATIVE.as_posix(),
        }
    )
    controls.update(CONTROL_GOVERNANCE_AUTHORITIES)
    controls.update(relative for _status, relative in V2_1_IMPLEMENTATION_SURFACE)
    controls.update(
        path.as_posix() for path in prepare._registered_successor_implementation_paths(root)
    )
    controls.update(base_runner.SUCCESSOR_REQUIRED_SEEDS)
    loaded_sources: frozenset[str] = frozenset()
    if require_current:
        _assert_locked_bootstrap(root)
        loaded_sources = _classify_loaded_module_origins(root)
        missing = loaded_sources - set(closure)
        if missing:
            raise RehearsalV22Error(
                "loaded repository sources are absent from AST closure: "
                + ", ".join(sorted(missing))
            )
    records: list[JsonObject] = []
    payloads: dict[str, bytes] = {}
    drifted_controls: list[JsonObject] = []
    frozen = {
        PREREGISTRATION_RELATIVE.as_posix(): PREREGISTRATION_SHA256,
        BUNDLE_SCHEMA_RELATIVE.as_posix(): BUNDLE_SCHEMA_SHA256,
        RELEASE_SCHEMA_RELATIVE.as_posix(): RELEASE_SCHEMA_SHA256,
        INCIDENT_RELATIVE.as_posix(): INCIDENT_SHA256,
        REMEDIATION_RELATIVE.as_posix(): REMEDIATION_SHA256,
        SCOPE_AUTHORIZATION_RELATIVE.as_posix(): SCOPE_AUTHORIZATION_SHA256,
        V2_1_BUNDLE_SCHEMA_RELATIVE.as_posix(): V2_1_BUNDLE_SCHEMA_SHA256,
        V2_1_RELEASE_SCHEMA_RELATIVE.as_posix(): V2_1_RELEASE_SCHEMA_SHA256,
    }
    for relative in sorted(controls, key=lambda value: value.encode("utf-8")):
        _relative_text(relative, "control repository path")
        governance = CONTROL_GOVERNANCE_AUTHORITIES.get(relative)
        payload = (
            _git_blob(root, governance[1], relative)
            if governance is not None
            else blob_reader(relative)
        )
        if not payload:
            raise RehearsalV22Error(f"control byte is empty: {relative}")
        if require_current and (governance is None or governance[2]):
            current = _regular_bytes(
                _safe_path(root, relative, f"current control {relative}"),
                f"current control {relative}",
            )
            if current != payload:
                drifted_controls.append(
                    {
                        "repository_path": relative,
                        "selected_commit_sha256": _sha256(payload),
                        "worktree_sha256": _sha256(current),
                    }
                )
        if relative in frozen and _sha256(payload) != frozen[relative]:
            raise RehearsalV22Error(f"frozen control SHA drifted: {relative}")
        archive_path = f"archive/control-surface/root/repo/{relative}"
        payloads[archive_path] = payload
        if governance is not None:
            kind = "frozen_control"
        elif relative in closure:
            kind = "package_initializer" if relative.endswith("/__init__.py") else "python_source"
        elif relative == "pyproject.toml":
            kind = "project_manifest"
        elif relative == "uv.lock":
            kind = "lockfile"
        else:
            kind = "frozen_control"
        records.append(
            {
                "logical_name": relative,
                "bundle_relative_path": archive_path,
                "source_kind": kind,
                "repository_path": relative,
                "bytes": len(payload),
                "sha256": _sha256(payload),
            }
        )
    if drifted_controls:
        raise RehearsalV22Error(
            "control differs from selected commit: "
            + _canonical_json_bytes(drifted_controls)
            .decode("utf-8", errors="strict")
            .removesuffix("\n")
        )
    python_payload, package_payload = _runtime_inventory(root)
    for name, payload, kind in (
        ("python", python_payload, "python_runtime"),
        ("packages", package_payload, "package_inventory"),
    ):
        archive_path = f"archive/control-surface/root/runtime/{name}.json"
        payloads[archive_path] = payload
        records.append(
            {
                "logical_name": name,
                "bundle_relative_path": archive_path,
                "source_kind": kind,
                "repository_path": None,
                "bytes": len(payload),
                "sha256": _sha256(payload),
            }
        )
    records.sort(key=lambda row: cast(str, row["bundle_relative_path"]).encode("utf-8"))
    manifest = _canonical_json_bytes({"schema_version": CONTROL_MANIFEST_SCHEMA, "files": records})
    merkle_payloads = dict(payloads)
    merkle_payloads["archive/control-surface/manifest.json"] = manifest
    root_digest = _generic_merkle_root(merkle_payloads)
    return ControlSurface(
        implementation_commit=commit,
        records=tuple(records),
        payloads=dict(sorted(payloads.items())),
        manifest_payload=manifest,
        merkle_root_sha256=root_digest,
        ast_closure_paths=tuple(sorted(closure)),
        loaded_repository_sources=tuple(sorted(loaded_sources)),
        python_inventory=python_payload,
        package_inventory=package_payload,
    )


def _historical_selected_anchor(
    binding: ExecutionBinding,
    history: HistoryValidation,
) -> HistoricalSelectedAnchor:
    """Bind the selected attempt exclusively to immutable ledger and Git bytes."""

    if (
        not history.series_closed
        or history.selected_attempt_ordinal is None
        or history.validated_candidate_count != 1
        or history.incomplete_count != 0
        or history.live_ledger_root_sha256 is None
    ):
        raise RehearsalV22Error("historical selected anchor requires one closed selected series")
    selected = history.records[history.selected_attempt_ordinal - 1]
    if selected.candidate_bytes is None or selected.terminal_bytes is None:
        raise RehearsalV22Error("historical selected anchor lacks candidate or terminal")
    authorization = _validate_action_authorization(
        binding,
        selected.owner_action_time_authorization,
        expected_ordinal=selected.ordinal,
        expected_previous_history_root_sha256=selected.previous_history_root_sha256,
        require_current_process=False,
    )
    control = build_control_surface(
        binding.project_root,
        selected.implementation_commit,
        require_current=False,
    )
    candidate = _object(
        strict_json_loads(selected.candidate_bytes, source="historical selected candidate"),
        "historical selected candidate",
    )
    roots = (
        candidate.get("run_a_root_sha256"),
        candidate.get("run_b_root_sha256"),
        candidate.get("control_surface_root_sha256"),
        candidate.get("evidence_tree_root_sha256"),
        candidate.get("candidate_content_root_sha256"),
    )
    if (
        any(not _lower_hex(value, 64) for value in roots)
        or candidate.get("control_surface_root_sha256") != control.merkle_root_sha256
        or candidate.get("control_surface_root_sha256")
        != authorization.control_merkle_root_sha256
        or candidate.get("evidence_tree_root_sha256")
        != selected.evidence_tree_root_sha256
    ):
        raise RehearsalV22Error("historical selected anchor roots drifted")
    selected_git_blobs = {
        cast(str, row["repository_path"]): cast(str, row["sha256"])
        for row in control.records
        if row.get("repository_path") is not None
    }
    archived_git_blobs = {
        relative: _sha256(
            control.payloads[f"archive/control-surface/root/repo/{relative}"]
        )
        for relative in selected_git_blobs
    }
    if not selected_git_blobs or selected_git_blobs != archived_git_blobs:
        raise RehearsalV22Error("historical selected Git blob map drifted")
    return HistoricalSelectedAnchor(
        selected_epoch=selected.implementation_epoch,
        selected_commit=selected.implementation_commit,
        owner_action_time_authorization=selected.owner_action_time_authorization,
        owner_surface_authorization=authorization.owner_surface_authorization,
        independent_implementation_review=authorization.independent_implementation_review,
        control_surface=control,
        history_root_sha256=history.history_root_sha256,
        live_ledger_root_sha256=history.live_ledger_root_sha256,
        evidence_tree_root_sha256=selected.evidence_tree_root_sha256,
        candidate_content_root_sha256=cast(
            str,
            candidate["candidate_content_root_sha256"],
        ),
        run_a_root_sha256=cast(str, candidate["run_a_root_sha256"]),
        run_b_root_sha256=cast(str, candidate["run_b_root_sha256"]),
        selected_git_blob_sha256=dict(sorted(selected_git_blobs.items())),
    )


_EPOCH_SURFACE_AUTHORITY_PATH = re.compile(
    r"^docs/phase4/reports/P4\.2a-v2-2-epoch([0-9]+)-surface-authority-"
    r"[0-9]{8}\.json$"
)
_EPOCH_IMPLEMENTATION_REVIEW_PATH = re.compile(
    r"^docs/phase4/reports/P4\.2a-v2-2-epoch([0-9]+)(?:-[A-Za-z0-9-]+)?-"
    r"implementation-independent-review-[0-9]{8}\.json$"
)
_EPOCH_LANDING_REPORT_PATH = re.compile(
    r"^docs/phase4/reports/P4\.2a-v2-2-epoch([0-9]+)-.*landing-report-"
    r"[0-9]{8}\.json$"
)


def _unique_a_commit_all_history(
    project_root: Path,
    relative: str,
) -> str:
    history = _git_bytes(
        project_root,
        "log",
        "--all",
        "--format=@@%H",
        "--name-status",
        "--find-renames",
        "--find-copies",
        "--",
        relative,
    ).decode("utf-8", errors="strict")
    touches: list[tuple[str, str, tuple[str, ...]]] = []
    active: str | None = None
    for raw_line in history.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("@@"):
            active = line[2:]
            continue
        if active is None:
            raise RehearsalV22Error("all-history unique-A log is malformed")
        fields = tuple(line.split("\t"))
        if len(fields) < 2:
            raise RehearsalV22Error("all-history unique-A log is malformed")
        touches.append((active, fields[0], fields[1:]))
    if len(touches) != 1 or touches[0][1:] != ("A", (relative,)):
        raise RehearsalV22Error(f"review is not unique-A across all refs: {relative}")
    return _git_commit(project_root, touches[0][0], "all-history unique-A commit")


def _first_parent_epoch_governance(
    project_root: Path,
    *,
    execution_head: str,
) -> tuple[
    tuple[str, ...],
    tuple[tuple[int, AuthorityReference], ...],
    tuple[tuple[int, str, str, str], ...],
    tuple[tuple[int, str, str, str, str], ...],
]:
    """Strictly parse every matching epoch authority/review/landing on first-parent."""

    root = project_root.absolute()
    commits = tuple(
        _git_bytes(root, "rev-list", "--first-parent", "--reverse", execution_head, "--")
        .decode("ascii", errors="strict")
        .splitlines()
    )
    authorities: list[tuple[int, AuthorityReference]] = []
    reviews: list[tuple[int, str, str, str]] = []
    landings: list[tuple[int, str, str, str, str]] = []
    observed_paths: set[str] = set()
    for commit in commits:
        parents = (
            _git_bytes(root, "rev-list", "--parents", "-n", "1", commit, "--")
            .decode("ascii", errors="strict")
            .strip()
            .split()
        )
        if not parents or parents[0] != commit:
            raise RehearsalV22Error("first-parent governance chain is malformed")
        if len(parents) == 1:
            continue
        changes = _git_bytes(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            parents[1],
            commit,
            "--",
            "docs/phase4/reports",
        ).decode("utf-8", errors="strict")
        for line in changes.splitlines():
            fields = line.split("\t")
            if len(fields) != 2:
                raise RehearsalV22Error("first-parent governance change is malformed")
            status_value, relative = fields
            matches = [
                (kind, pattern.fullmatch(relative))
                for kind, pattern in (
                    ("authority", _EPOCH_SURFACE_AUTHORITY_PATH),
                    ("review", _EPOCH_IMPLEMENTATION_REVIEW_PATH),
                    ("landing", _EPOCH_LANDING_REPORT_PATH),
                )
            ]
            selected_matches = [(kind, match) for kind, match in matches if match is not None]
            if not selected_matches:
                continue
            if len(selected_matches) != 1 or status_value != "A" or relative in observed_paths:
                raise RehearsalV22Error("epoch governance artifact is not one unique add")
            observed_paths.add(relative)
            kind, match = selected_matches[0]
            if match is None:
                raise RehearsalV22Error("epoch governance matcher vanished")
            payload = _git_blob(root, commit, relative)
            document = _object(
                strict_json_loads(payload, source=f"reachable epoch {kind} {relative}"),
                f"reachable epoch {kind} {relative}",
            )
            epoch = int(match.group(1))
            if kind == "authority":
                surface = document.get("exact_surface")
                if (
                    set(document)
                    != {
                        "schema_version",
                        "verdict",
                        "owner",
                        "implementation_epoch",
                        "base_commit",
                        "exact_surface",
                    }
                    or document.get("schema_version")
                    != "p4.2a-v2-2-implementation-epoch-surface-authorization-v1"
                    or document.get("verdict")
                    != "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE"
                    or document.get("owner") != {"identity": "ouyang", "approved": True}
                    or type(_object(document.get("owner"), "epoch owner").get("approved"))
                    is not bool
                    or not _is_exact_int(document.get("implementation_epoch"), epoch)
                    or not _lower_hex(document.get("base_commit"), 40)
                    or not isinstance(surface, list)
                    or not surface
                ):
                    raise RehearsalV22Error("reachable epoch authority shape drifted")
                permitted_paths = {
                    path.as_posix() for path in IMPLEMENTATION_SURFACE
                }
                observed_surface_paths: list[str] = []
                for index, raw_row in enumerate(surface):
                    row = _object(
                        raw_row,
                        f"reachable epoch exact_surface[{index}]",
                    )
                    if set(row) != {"path", "status"}:
                        raise RehearsalV22Error(
                            "reachable epoch authority shape drifted"
                        )
                    surface_relative = _relative_text(
                        row.get("path"),
                        f"reachable epoch exact_surface[{index}].path",
                    )
                    if (
                        surface_relative not in permitted_paths
                        or row.get("status") not in {"A", "M"}
                        or surface_relative in observed_surface_paths
                    ):
                        raise RehearsalV22Error(
                            "reachable epoch authority exact surface drifted"
                        )
                    observed_surface_paths.append(surface_relative)
                if observed_surface_paths != sorted(
                    observed_surface_paths,
                    key=lambda value: value.encode("utf-8"),
                ):
                    raise RehearsalV22Error(
                        "reachable epoch authority exact surface is not byte-sorted"
                    )
                authorities.append(
                    (epoch, AuthorityReference(relative, _sha256(payload), commit))
                )
            elif kind == "review":
                verdict = document.get("verdict")
                reviewed_commit = document.get("reviewed_commit")
                unique_a_commit = _unique_a_commit_all_history(root, relative)
                if (
                    document.get("schema_version") != "p4.2a-independent-review-v1"
                    or verdict != f"APPROVE_EPOCH{epoch}_IMPLEMENTATION"
                    or document.get("blockers") != []
                    or not _lower_hex(reviewed_commit, 40)
                    or _git_blob(root, unique_a_commit, relative) != payload
                ):
                    raise RehearsalV22Error("reachable epoch review shape drifted")
                reviews.append(
                    (epoch, unique_a_commit, relative, cast(str, reviewed_commit))
                )
            else:
                schema = document.get("schema_version")
                lineage = _object(
                    document.get("candidate_lineage"),
                    "reachable epoch landing candidate lineage",
                )
                planned = _object(
                    document.get("planned_landing"),
                    "reachable epoch landing plan",
                )
                implementation = lineage.get("implementation_commit")
                review_row = _object(
                    lineage.get("independent_review"),
                    "reachable epoch landing review",
                )
                review_commit = review_row.get(
                    "creating_commit",
                    review_row.get("commit"),
                )
                review_path = review_row.get("path")
                if (
                    not isinstance(schema, str)
                    or re.fullmatch(
                        rf"p4\.2a-v2-2-epoch{epoch}(?:-[A-Za-z0-9-]+)?-"
                        r"registered-gate-landing-report-v[0-9]+",
                        schema,
                    )
                    is None
                    or document.get("status")
                    != "PASS_REGISTERED_GATE_LANDING_REPORT_READY_BEFORE_MERGE"
                    or not _lower_hex(implementation, 40)
                    or not _lower_hex(review_commit, 40)
                    or planned.get("second_parent") != review_commit
                    or not isinstance(review_path, str)
                    or (
                        (review_match := _EPOCH_IMPLEMENTATION_REVIEW_PATH.fullmatch(
                            review_path
                        ))
                        is None
                    )
                    or int(review_match.group(1)) != epoch
                ):
                    raise RehearsalV22Error("reachable epoch landing shape drifted")
                if _unique_a_commit_all_history(root, review_path) != review_commit:
                    raise RehearsalV22Error("reachable epoch landing review binding drifted")
                landings.append(
                    (
                        epoch,
                        commit,
                        relative,
                        cast(str, implementation),
                        cast(str, review_commit),
                    )
                )
    if not commits or commits[-1] != execution_head or len(commits) != len(set(commits)):
        raise RehearsalV22Error("first-parent governance chain is incomplete")
    return commits, tuple(authorities), tuple(reviews), tuple(landings)


def _first_parent_added_epoch_documents(
    project_root: Path,
    *,
    execution_head: str,
) -> tuple[tuple[int, AuthorityReference], ...]:
    _chain, authorities, _reviews, _landings = _first_parent_epoch_governance(
        project_root,
        execution_head=execution_head,
    )
    return authorities


def _live_execution_anchor(
    binding: ExecutionBinding,
    execution_epoch: Mapping[str, Any],
) -> LiveExecutionAnchor:
    """Prove the latest landed reviewed epoch against all current executing bytes."""

    root = binding.project_root.absolute()
    execution_head = _current_execution_head(root)
    epoch = execution_epoch.get("epoch")
    implementation_commit = execution_epoch.get("implementation_commit")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise RehearsalV22Error("live execution epoch is invalid")
    commit = _git_commit(root, implementation_commit, "live execution implementation")
    owner = _validate_authority_ref_shape(
        execution_epoch.get("owner_exact_surface_authorization"),
        "live execution owner authority",
    )
    review = _validate_authority_ref_shape(
        execution_epoch.get("independent_implementation_review"),
        "live execution independent review",
    )
    landing_report = _validate_authority_ref_shape(
        execution_epoch.get("landing_report"),
        "live execution landing report",
    )
    merge_commit = _git_commit(
        root,
        execution_epoch.get("merge_commit"),
        "live execution merge commit",
    )
    control = build_control_surface(root, commit, require_current=True)
    if (
        execution_epoch.get("control_merkle_root_sha256") != control.merkle_root_sha256
        or execution_epoch.get("control_record_count") != len(control.records)
        or execution_epoch.get("latest_complete_landed_epoch_required") is not True
        or execution_epoch.get("current_control_bytes_required") is not True
        or execution_epoch.get("loaded_module_bytes_required") is not True
    ):
        raise RehearsalV22Error("live execution control binding drifted")
    _later_epoch_surface(
        root,
        epoch=epoch,
        implementation_commit=commit,
        owner_surface_authorization=owner,
        execution_head=execution_head,
        require_current=True,
    )
    review_parents = (
        _git_bytes(
            root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            review.creating_commit,
            "--",
        )
        .decode("ascii", errors="strict")
        .strip()
        .split()
    )
    review_surface = _parse_name_status(
        _git_bytes(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            commit,
            review.creating_commit,
            "--",
        )
    )
    review_payload = _git_blob(root, review.creating_commit, review.path)
    review_document = _object(
        strict_json_loads(review_payload, source="live execution independent review"),
        "live execution independent review",
    )
    review_verdict = review_document.get("verdict")
    if (
        review_parents != [review.creating_commit, commit]
        or review_surface != {review.path: "A"}
        or _sha256(review_payload) != review.sha256
        or not isinstance(review_verdict, str)
        or not review_verdict.startswith("APPROVE_")
        or "IMPLEMENTATION" not in review_verdict.split("_")
        or review_document.get("blockers") not in (None, [])
        or not _document_mentions_commit(review_document, commit)
    ):
        raise RehearsalV22Error("live execution independent review lineage drifted")
    landing_payload = validate_unique_a_authority(
        root,
        landing_report,
        execution_head=execution_head,
    )
    landing_document = _object(
        strict_json_loads(landing_payload, source="live execution landing report"),
        "live execution landing report",
    )
    if not (
        _document_mentions_commit(landing_document, commit)
        and _document_mentions_commit(landing_document, review.creating_commit)
    ):
        raise RehearsalV22Error("live execution landing report binding drifted")
    merge_parents = (
        _git_bytes(root, "rev-list", "--parents", "-n", "1", merge_commit, "--")
        .decode("ascii", errors="strict")
        .strip()
        .split()
    )
    landing_parents = (
        _git_bytes(
            root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            landing_report.creating_commit,
            "--",
        )
        .decode("ascii", errors="strict")
        .strip()
        .split()
    )
    if (
        len(merge_parents) != 3
        or merge_parents[2] != review.creating_commit
        or landing_parents != [landing_report.creating_commit, merge_commit]
        or not _git_is_ancestor(root, landing_report.creating_commit, execution_head)
    ):
        raise RehearsalV22Error("live execution merge/landing topology drifted")
    landing_surface = _parse_name_status(
        _git_bytes(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            merge_commit,
            landing_report.creating_commit,
            "--",
        )
    )
    if landing_surface != {landing_report.path: "A"}:
        raise RehearsalV22Error(
            "live execution landing commit is not the exact unique-A report surface"
        )

    chain, authorities, reviews, landings = _first_parent_epoch_governance(
        root,
        execution_head=execution_head,
    )
    epoch_values = [value for value, _reference in authorities]
    matching = [reference for value, reference in authorities if value == epoch]
    matching_reviews = [
        row
        for row in reviews
        if row[0] == epoch
    ]
    matching_landings = [
        row
        for row in landings
        if row[0] == epoch
    ]
    if (
        not authorities
        or len(epoch_values) != len(set(epoch_values))
        or max(epoch_values) != epoch
        or matching != [owner]
        or any(review_epoch > epoch for review_epoch, *_rest in reviews)
        or any(landing_epoch > epoch for landing_epoch, *_rest in landings)
        or matching_reviews
        != [(epoch, review.creating_commit, review.path, commit)]
        or matching_landings
        != [
            (
                epoch,
                landing_report.creating_commit,
                landing_report.path,
                commit,
                review.creating_commit,
            )
        ]
        or _unique_a_commit_all_history(root, review.path)
        != review.creating_commit
    ):
        raise RehearsalV22Error(
            "live execution epoch is not the unique latest complete governance chain"
        )
    control_members = {
        cast(str, row["repository_path"])
        for row in control.records
        if row.get("repository_path") is not None
    }
    if len(control_members) != 73:
        raise RehearsalV22Error("live execution repository control-member count drifted")
    try:
        merge_index = chain.index(merge_commit)
    except ValueError as exc:
        raise RehearsalV22Error("live execution merge is absent from first-parent chain") from exc
    for descendant in chain[merge_index + 1 :]:
        parents = (
            _git_bytes(root, "rev-list", "--parents", "-n", "1", descendant, "--")
            .decode("ascii", errors="strict")
            .strip()
            .split()
        )
        if len(parents) < 2 or parents[0] != descendant:
            raise RehearsalV22Error("post-landing first-parent topology drifted")
        changed = set(
            _git_bytes(
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-only",
                parents[1],
                descendant,
                "--",
            )
            .decode("utf-8", errors="strict")
            .splitlines()
        )
        if changed & control_members:
            raise RehearsalV22Error(
                "a repository control member changed in a post-merge first-parent commit"
            )
    loaded_module_sha256: dict[str, str] = {}
    for relative in (IMPLEMENTATION_RELATIVE, VALIDATOR_RELATIVE):
        payload = validate_implementation_blob(
            root,
            commit,
            relative.as_posix(),
            require_current=True,
        )
        loaded_module_sha256[relative.as_posix()] = _sha256(payload)
    return LiveExecutionAnchor(
        execution_epoch=epoch,
        implementation_commit=commit,
        owner_surface_authorization=owner,
        independent_implementation_review=review,
        merge_commit=merge_commit,
        landing_report=landing_report,
        control_surface=control,
        execution_head=execution_head,
        loaded_module_sha256=loaded_module_sha256,
    )


def _ordinary_live_execution_anchor(
    binding: ExecutionBinding,
    historical_anchor: HistoricalSelectedAnchor,
) -> LiveExecutionAnchor:
    """Build an independently typed live anchor for the ordinary active path."""

    if not isinstance(historical_anchor, HistoricalSelectedAnchor):
        raise RehearsalV22Error("ordinary live anchor requires historical anchor input")
    root = binding.project_root.absolute()
    execution_head = _current_execution_head(root)
    commit = historical_anchor.selected_commit
    control = build_control_surface(root, commit, require_current=True)
    validate_implementation_epoch(
        root,
        epoch=historical_anchor.selected_epoch,
        implementation_commit=commit,
        owner_surface_authorization=historical_anchor.owner_surface_authorization,
        independent_review=historical_anchor.independent_implementation_review,
        control_merkle_root_sha256=control.merkle_root_sha256,
        execution_head=execution_head,
        require_current_bytes=True,
    )
    loaded_module_sha256 = {
        relative.as_posix(): _sha256(
            validate_implementation_blob(
                root,
                commit,
                relative.as_posix(),
                require_current=True,
            )
        )
        for relative in (IMPLEMENTATION_RELATIVE, VALIDATOR_RELATIVE)
    }
    return LiveExecutionAnchor(
        execution_epoch=historical_anchor.selected_epoch,
        implementation_commit=commit,
        owner_surface_authorization=historical_anchor.owner_surface_authorization,
        independent_implementation_review=(historical_anchor.independent_implementation_review),
        # Ordinary attempt execution precedes merge/landing by design.  These
        # provenance slots therefore bind the unique action receipt; only the
        # recovery modes may enter the latest-landed predicate.
        merge_commit=historical_anchor.owner_action_time_authorization.creating_commit,
        landing_report=historical_anchor.owner_action_time_authorization,
        control_surface=control,
        execution_head=execution_head,
        loaded_module_sha256=loaded_module_sha256,
    )


def _active_attempt_validation_anchors(
    value: object,
    *,
    project_root: Path,
    validator_module: ModuleType,
) -> tuple[HistoricalSelectedAnchor, LiveExecutionAnchor]:
    context = cast(ExecutionCapability, value)
    binding = _validate_execution_capability(context, project_root=project_root)
    if (
        validator_module.__name__
        != "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
        or sys.modules.get(validator_module.__name__) is not validator_module
    ):
        raise RehearsalV22Error("active anchor extraction lacks validator identity")
    history = validate_live_history(binding)
    historical = _historical_selected_anchor(binding, history)
    live = _ordinary_live_execution_anchor(binding, historical)
    if historical is live:
        raise RehearsalV22Error("historical and live anchors must be distinct values")
    return historical, live


def _standalone_active_validation_anchors(
    *,
    project_root: Path,
    raw_binding: ExecutionBinding,
    selected_epoch: int,
    selected_commit: str,
    selected_control_root_sha256: str,
) -> tuple[HistoricalSelectedAnchor, LiveExecutionAnchor]:
    """Ordinary official validator bridge; recovery may never use this path."""

    if raw_binding.mode != "REGISTERED_OFFICIAL" or raw_binding.project_root != project_root:
        raise RehearsalV22Error("standalone active anchors require the registered binding")
    history = validate_live_history(raw_binding)
    historical = _historical_selected_anchor(raw_binding, history)
    if (
        historical.selected_epoch != selected_epoch
        or historical.selected_commit != selected_commit
        or historical.control_surface.merkle_root_sha256
        != selected_control_root_sha256
    ):
        raise RehearsalV22Error("standalone selected envelope differs from live history")
    live = _ordinary_live_execution_anchor(raw_binding, historical)
    return historical, live


def _build_recovery_authority_state() -> tuple[Any, ...]:
    capability_nonce = object()
    delegation_nonce = object()
    capability_registry: tuple[RecoveryExecutionCapability, ...] = ()
    delegation_registry: tuple[
        tuple[RecoveryValidatorDelegation, RecoveryExecutionCapability, ModuleType, _AuditPolicy],
        ...,
    ] = ()

    def validate_recovery_execution_capability(
        value: RecoveryExecutionCapability,
        *,
        project_root: Path,
    ) -> ExecutionBinding:
        if (
            not isinstance(value, RecoveryExecutionCapability)
            or value._nonce is not capability_nonce
            or not any(record is value for record in capability_registry)
            or value.binding.project_root != project_root.absolute()
        ):
            raise RehearsalV22Error("recovery execution capability is forged or stale")
        _validate_bootstrap_evidence(value.bootstrap)
        policy = _AUDIT_POLICY.get()
        authority_root = _TEMP_AUTHORITY.get()
        expected_policy = _recovery_execution_policy(
            value.binding,
            claim_root=value.claim_root,
            temporary_authority=value.temporary_authority,
        )
        if (
            policy is None
            or not _audit_policy_is_issued(policy)
            or policy != expected_policy
            or policy is not value.audit_policy
            or id(policy) != value.audit_policy_id
            or authority_root != value.temporary_authority
            or policy.project_root != value.binding.project_root
            or not value.claim_root.is_dir()
            or value.claim_root.is_symlink()
            or not value.temporary_authority.is_dir()
            or value.temporary_authority.is_symlink()
        ):
            raise RehearsalV22Error("recovery capability is outside its audit lifetime")
        reference = value.authorization.authority_ref(value.binding.project_root)
        observed_authorization = _validate_bundle_recovery_authorization(
            value.binding,
            reference,
            require_current_process=True,
        )
        history = validate_live_history(value.binding)
        observed_historical = _historical_selected_anchor(value.binding, history)
        observed_live = _live_execution_anchor(
            value.binding,
            observed_authorization.execution_epoch,
        )
        if (
            observed_authorization != value.authorization
            or observed_historical != value.historical_anchor
            or observed_live != value.live_anchor
        ):
            raise RehearsalV22Error("recovery capability bytes or anchors drifted")
        started, _payload, _digest = _canonical_object_file(
            value.claim_root / "started.json",
            label="bundle recovery started claim",
            exact_fields=RECOVERY_STARTED_FIELDS,
        )
        created_utc = started.get("created_at_utc")
        created_shanghai = started.get("created_at_shanghai")
        if (
            not isinstance(created_utc, str)
            or RFC3339_UTC_SECONDS.fullmatch(created_utc) is None
            or not isinstance(created_shanghai, str)
            or RFC3339_SHANGHAI_SECONDS.fullmatch(created_shanghai) is None
        ):
            raise RehearsalV22Error("bundle recovery started timestamp is invalid")
        try:
            if datetime.fromisoformat(
                created_utc.replace("Z", "+00:00")
            ) != datetime.fromisoformat(created_shanghai):
                raise RehearsalV22Error("bundle recovery started timestamps disagree")
        except ValueError as exc:
            raise RehearsalV22Error("bundle recovery started timestamp is invalid") from exc
        if (
            started.get("schema_version")
            != "p4.2a-v2-2-sealed-bundle-recovery-started-v1"
            or started.get("recovery_id") != value.authorization.authorization_id
            or started.get("authorization") != value.authorization.authority_ref(
                value.binding.project_root
            ).as_json()
            or started.get("state") != "STARTED"
            or started.get("execution_head") != value.live_anchor.execution_head
            or not _is_exact_int(
                started.get("execution_epoch"),
                value.live_anchor.execution_epoch,
            )
            or started.get("sealed_history_root_sha256")
            != value.historical_anchor.history_root_sha256
            or started.get("sealed_live_ledger_root_sha256")
            != value.historical_anchor.live_ledger_root_sha256
            or started.get("destination") != value.binding.destination.as_posix()
            or not _is_exact_int(started.get("authorized_bundle_recovery_starts"), 1)
            or not _is_exact_int(started.get("authorized_pipeline_starts"), 0)
            or not _is_exact_int(started.get("automatic_retry_count"), 0)
        ):
            raise RehearsalV22Error("bundle recovery started claim drifted")
        return value.binding

    @contextmanager
    def recovery_execution_capability_scope(
        *,
        binding: ExecutionBinding,
        bootstrap: _BootstrapEvidence,
        authorization: BundleRecoveryAuthorization,
        historical_anchor: HistoricalSelectedAnchor,
        live_anchor: LiveExecutionAnchor,
        claim_root: Path,
        temporary_authority: Path,
    ) -> Iterator[RecoveryExecutionCapability]:
        nonlocal capability_registry
        _validate_bootstrap_evidence(bootstrap)
        if not isinstance(historical_anchor, HistoricalSelectedAnchor) or not isinstance(
            live_anchor,
            LiveExecutionAnchor,
        ):
            raise RehearsalV22Error("recovery capability requires both typed anchors")
        policy = _AUDIT_POLICY.get()
        expected_policy = _recovery_execution_policy(
            binding,
            claim_root=claim_root,
            temporary_authority=temporary_authority,
        )
        if (
            policy is None
            or not _audit_policy_is_issued(policy)
            or policy != expected_policy
        ):
            raise RehearsalV22Error("recovery capability lacks its exact effect policy")
        value = RecoveryExecutionCapability(
            _nonce=capability_nonce,
            binding=binding,
            bootstrap=bootstrap,
            authorization=authorization,
            historical_anchor=historical_anchor,
            live_anchor=live_anchor,
            claim_root=claim_root,
            temporary_authority=temporary_authority,
            audit_policy=policy,
            audit_policy_id=id(policy),
        )
        capability_registry = (*capability_registry, value)
        try:
            validate_recovery_execution_capability(value, project_root=binding.project_root)
            yield value
        finally:
            capability_registry = tuple(
                record for record in capability_registry if record is not value
            )

    @contextmanager
    def borrow_recovery_validator_authority(
        recovery_context: RecoveryExecutionCapability,
        *,
        validator_module: ModuleType,
        bundle_path: Path,
    ) -> Iterator[RecoveryValidatorDelegation]:
        nonlocal delegation_registry
        binding = validate_recovery_execution_capability(
            recovery_context,
            project_root=recovery_context.binding.project_root,
        )
        policy = _AUDIT_POLICY.get()
        if (
            policy is None
            or validator_module.__name__
            != "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
            or sys.modules.get(validator_module.__name__) is not validator_module
        ):
            raise RehearsalV22Error("recovery validator delegation lacks exact identity")
        candidate = bundle_path.absolute()
        if not candidate.is_relative_to(recovery_context.temporary_authority):
            raise RehearsalV22Error("recovery validator bundle escapes temporary authority")
        payload = _regular_bytes(candidate, "recovery delegated bundle")
        token = RecoveryValidatorDelegation(
            _nonce=delegation_nonce,
            binding=binding,
            capability_id=id(recovery_context),
            validator_module_id=id(validator_module),
            audit_policy_id=id(policy),
            temporary_authority=recovery_context.temporary_authority,
            bundle_path=candidate,
            bundle_sha256=_sha256(payload),
            creator_module_id=id(sys.modules[MODULE_NAME]),
            lifetime_id=id(object()),
        )
        record = (token, recovery_context, validator_module, policy)
        delegation_registry = (*delegation_registry, record)
        try:
            yield token
        finally:
            delegation_registry = tuple(
                active for active in delegation_registry if active is not record
            )

    def validate_recovery_validator_delegation(
        value: RecoveryValidatorDelegation,
        *,
        recovery_context: RecoveryExecutionCapability,
        validator_module: ModuleType,
        project_root: Path,
        bundle_path: Path,
    ) -> ExecutionBinding:
        binding = validate_recovery_execution_capability(
            recovery_context,
            project_root=project_root,
        )
        policy = _AUDIT_POLICY.get()
        matches = [record for record in delegation_registry if record[0] is value]
        record = matches[0] if len(matches) == 1 else None
        candidate = bundle_path.absolute()
        if (
            not isinstance(value, RecoveryValidatorDelegation)
            or value._nonce is not delegation_nonce
            or record is None
            or record[1] is not recovery_context
            or record[2] is not validator_module
            or record[3] is not policy
            or value.binding != binding
            or value.capability_id != id(recovery_context)
            or value.validator_module_id != id(validator_module)
            or value.audit_policy_id != id(policy)
            or value.temporary_authority != recovery_context.temporary_authority
            or value.bundle_path != candidate
            or value.bundle_sha256
            != _sha256(_regular_bytes(candidate, "recovery delegated bundle"))
            or value.creator_module_id != id(sys.modules[MODULE_NAME])
        ):
            raise RehearsalV22Error("recovery validator delegation is forged or stale")
        return binding

    def recovery_validation_anchors(
        value: RecoveryExecutionCapability,
        *,
        project_root: Path,
    ) -> tuple[HistoricalSelectedAnchor, LiveExecutionAnchor]:
        validate_recovery_execution_capability(value, project_root=project_root)
        return value.historical_anchor, value.live_anchor

    return (
        recovery_execution_capability_scope,
        validate_recovery_execution_capability,
        borrow_recovery_validator_authority,
        validate_recovery_validator_delegation,
        recovery_validation_anchors,
    )


(
    _recovery_execution_capability_scope,
    _validate_recovery_execution_capability,
    _borrow_recovery_validator_authority,
    _validate_recovery_validator_delegation,
    _recovery_validation_anchors,
) = _build_recovery_authority_state()


def _validate_recovery_claim_first_parent_order(
    project_root: Path,
    *,
    live_execution_head: str,
    landing_commit: str,
    authorization_commit: str,
    started_execution_head: str,
) -> None:
    """Bind landing, recovery authority, and its one start on one live spine."""

    root = project_root.absolute()
    live_head = _git_commit(root, live_execution_head, "recovered release live head")
    landing = _git_commit(root, landing_commit, "recovered release landing")
    authority = _git_commit(
        root,
        authorization_commit,
        "recovered release authorization",
    )
    started = _git_commit(
        root,
        started_execution_head,
        "recovered release started execution head",
    )
    live_first_parent_chain = tuple(
        _git_bytes(
            root,
            "rev-list",
            "--first-parent",
            "--reverse",
            live_head,
            "--",
        )
        .decode("ascii", errors="strict")
        .splitlines()
    )
    try:
        landing_index = live_first_parent_chain.index(landing)
        authority_index = live_first_parent_chain.index(authority)
        started_index = live_first_parent_chain.index(started)
    except ValueError as exc:
        raise RehearsalV22Error(
            "recovered release claim commits are outside live first-parent history"
        ) from exc
    if not landing_index <= authority_index <= started_index:
        raise RehearsalV22Error(
            "recovered release claim commit order is not landing-authority-started"
        )


def _successful_recovery_claim(
    binding: ExecutionBinding,
    authorization: BundleRecoveryAuthorization,
) -> tuple[Path, JsonObject, str, str]:
    claim = _recovery_claim_path(binding, authorization)
    if (
        not claim.is_dir()
        or claim.is_symlink()
        or stat.S_IMODE(claim.stat().st_mode) != 0o700
        or claim.resolve(strict=True) != claim.absolute()
    ):
        raise RehearsalV22Error("recovered release lacks one private recovery claim")
    names = sorted(path.name for path in claim.iterdir())
    if names != ["started.json", "terminal.json"]:
        raise RehearsalV22Error("recovered release claim inventory drifted")
    for name in names:
        metadata = (claim / name).lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise RehearsalV22Error("recovered release claim file identity drifted")
    started, _started_payload, _started_sha = _canonical_object_file(
        claim / "started.json",
        label="recovered release started claim",
        exact_fields=RECOVERY_STARTED_FIELDS,
    )
    terminal, _terminal_payload, terminal_sha = _canonical_object_file(
        claim / "terminal.json",
        label="recovered release terminal claim",
        exact_fields=RECOVERY_TERMINAL_FIELDS,
    )
    reference = authorization.authority_ref(binding.project_root).as_json()
    bundle_path = binding.destination / BUNDLE_FILENAME
    bundle_payload = _regular_bytes(bundle_path, "recovered release bundle")
    expected_tree_sha = _sha256(
        _canonical_json_bytes(_tree_fingerprint(binding.destination))
    )
    current_ledger_sha = _sha256(
        _canonical_json_bytes(_tree_fingerprint(binding.ledger_root))
    )
    live = _live_execution_anchor(binding, authorization.execution_epoch)
    _validate_recovery_claim_first_parent_order(
        binding.project_root,
        live_execution_head=live.execution_head,
        landing_commit=live.landing_report.creating_commit,
        authorization_commit=authorization.creating_commit,
        started_execution_head=cast(str, started.get("execution_head")),
    )
    started_utc = started.get("created_at_utc")
    started_shanghai = started.get("created_at_shanghai")
    completed_utc = terminal.get("completed_at_utc")
    completed_shanghai = terminal.get("completed_at_shanghai")
    if (
        not isinstance(started_utc, str)
        or RFC3339_UTC_SECONDS.fullmatch(started_utc) is None
        or not isinstance(started_shanghai, str)
        or RFC3339_SHANGHAI_SECONDS.fullmatch(started_shanghai) is None
        or not isinstance(completed_utc, str)
        or RFC3339_UTC_SECONDS.fullmatch(completed_utc) is None
        or not isinstance(completed_shanghai, str)
        or RFC3339_SHANGHAI_SECONDS.fullmatch(completed_shanghai) is None
    ):
        raise RehearsalV22Error("recovered release claim timestamps are invalid")
    try:
        started_instant = datetime.fromisoformat(started_utc.replace("Z", "+00:00"))
        completed_instant = datetime.fromisoformat(completed_utc.replace("Z", "+00:00"))
        if (
            started_instant != datetime.fromisoformat(started_shanghai)
            or completed_instant != datetime.fromisoformat(completed_shanghai)
            or completed_instant < started_instant
        ):
            raise RehearsalV22Error("recovered release claim timestamps disagree")
    except ValueError as exc:
        raise RehearsalV22Error("recovered release claim timestamp is invalid") from exc
    if (
        started.get("schema_version")
        != "p4.2a-v2-2-sealed-bundle-recovery-started-v1"
        or started.get("recovery_id") != authorization.authorization_id
        or started.get("authorization") != reference
        or started.get("state") != "STARTED"
        or not _is_exact_int(started.get("execution_epoch"), live.execution_epoch)
        or started.get("sealed_history_root_sha256")
        != authorization.sealed_series.get("history_root_sha256")
        or started.get("sealed_live_ledger_root_sha256")
        != authorization.sealed_series.get("live_ledger_root_sha256")
        or started.get("destination") != binding.destination.as_posix()
        or not _is_exact_int(started.get("authorized_bundle_recovery_starts"), 1)
        or not _is_exact_int(started.get("authorized_pipeline_starts"), 0)
        or not _is_exact_int(started.get("automatic_retry_count"), 0)
        or terminal.get("schema_version")
        != "p4.2a-v2-2-sealed-bundle-recovery-terminal-v1"
        or terminal.get("recovery_id") != authorization.authorization_id
        or terminal.get("authorization") != reference
        or terminal.get("outcome") != "BUNDLE_RECOVERY_PUBLISHED"
        or terminal.get("reached_stage") != "bundle_recovery_published"
        or terminal.get("sealed_ledger_before_sha256")
        != terminal.get("sealed_ledger_after_sha256")
        or terminal.get("sealed_ledger_before_sha256") != current_ledger_sha
        or not _lower_hex(terminal.get("sealed_ledger_before_sha256"), 64)
        or terminal.get("destination") != binding.destination.as_posix()
        or terminal.get("published_bundle_sha256") != _sha256(bundle_payload)
        or terminal.get("published_tree_sha256") != expected_tree_sha
        or type(terminal.get("temporary_authority_absent")) is not bool
        or terminal.get("temporary_authority_absent") is not True
        or not _is_exact_int(terminal.get("pipeline_starts"), 0)
        or not _is_exact_int(terminal.get("automatic_retry_count"), 0)
        or terminal.get("error") is not None
        or os.path.lexists(_recovery_temporary_authority_path(binding, authorization))
    ):
        raise RehearsalV22Error("recovered release success claim semantics drifted")
    return claim, terminal, terminal_sha, _sha256(bundle_payload)


def _build_recovered_release_authority_state() -> tuple[Any, ...]:
    capability_nonce = object()
    delegation_nonce = object()
    capability_registry: tuple[RecoveredReleaseCapability, ...] = ()
    delegation_registry: tuple[
        tuple[
            RecoveredReleaseValidatorDelegation,
            RecoveredReleaseCapability,
            ModuleType,
            _AuditPolicy,
        ],
        ...,
    ] = ()

    def validate_recovered_release_capability(
        value: RecoveredReleaseCapability,
        *,
        project_root: Path,
    ) -> ExecutionBinding:
        if (
            not isinstance(value, RecoveredReleaseCapability)
            or value._nonce is not capability_nonce
            or not any(record is value for record in capability_registry)
            or value.binding.project_root != project_root.absolute()
        ):
            raise RehearsalV22Error("recovered-release capability is forged or stale")
        policy = _AUDIT_POLICY.get()
        if (
            policy is None
            or not _audit_policy_is_issued(policy)
            or policy.project_root != value.binding.project_root
            or policy.write_roots
            or policy.exact_write_paths
            or policy.create_only_roots
            or policy.sqlite_roots
        ):
            raise RehearsalV22Error("recovered-release capability is not read-only")
        validator_module = sys.modules.get(
            "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
        )
        main_module = sys.modules.get("__main__")
        if id(validator_module) != value.validator_module_id and id(main_module) != (
            value.validator_module_id
        ):
            raise RehearsalV22Error("recovered-release validator module identity drifted")
        reference = value.authorization.authority_ref(value.binding.project_root)
        observed_authorization = _validate_bundle_recovery_authorization(
            value.binding,
            reference,
            require_current_process=False,
            require_destination_absent=False,
        )
        history = validate_live_history(value.binding)
        historical = _historical_selected_anchor(value.binding, history)
        live = _live_execution_anchor(
            value.binding,
            observed_authorization.execution_epoch,
        )
        claim, _terminal, terminal_sha, bundle_sha = _successful_recovery_claim(
            value.binding,
            observed_authorization,
        )
        if (
            observed_authorization != value.authorization
            or historical != value.historical_anchor
            or live != value.live_anchor
            or claim != value.claim_root
            or terminal_sha != value.terminal_sha256
            or value.bundle_path != value.binding.destination / BUNDLE_FILENAME
            or bundle_sha != value.bundle_sha256
        ):
            raise RehearsalV22Error("recovered-release evidence drifted")
        return value.binding

    @contextmanager
    def recovered_release_validation_scope(
        *,
        binding: ExecutionBinding,
        validator_module: ModuleType,
        recovery_authorization_path: Path,
        bundle_path: Path,
    ) -> Iterator[
        tuple[RecoveredReleaseCapability, RecoveredReleaseValidatorDelegation]
    ]:
        nonlocal capability_registry, delegation_registry
        candidate = bundle_path.absolute()
        if (
            binding.action_authorization_path != recovery_authorization_path.absolute()
            or candidate != binding.destination / BUNDLE_FILENAME
        ):
            raise RehearsalV22Error("recovered-release scope binding drifted")
        existing_policy = _AUDIT_POLICY.get()
        if existing_policy is None:
            audit_scope = _audited_execution(
                _read_only_preflight_policy(binding.project_root),
                validator_replay_module=validator_module,
            )
        else:
            if (
                not _audit_policy_is_issued(existing_policy)
                or existing_policy.project_root != binding.project_root
                or existing_policy.write_roots
                or existing_policy.exact_write_paths
                or existing_policy.create_only_roots
                or existing_policy.sqlite_roots
            ):
                raise RehearsalV22Error("recovered-release existing policy is not read-only")
            audit_scope = nullcontext()
        with audit_scope:
            execution_head = _current_execution_head(binding.project_root)
            reference = _authority_reference_for_path(
                binding.project_root,
                recovery_authorization_path,
                execution_head=execution_head,
                label="recovered-release recovery authorization",
            )
            authorization = _validate_bundle_recovery_authorization(
                binding,
                reference,
                require_current_process=False,
                require_destination_absent=False,
            )
            history = validate_live_history(binding)
            historical = _historical_selected_anchor(binding, history)
            live = _live_execution_anchor(binding, authorization.execution_epoch)
            claim, _terminal, terminal_sha, bundle_sha = _successful_recovery_claim(
                binding,
                authorization,
            )
            value = RecoveredReleaseCapability(
                _nonce=capability_nonce,
                binding=binding,
                authorization=authorization,
                historical_anchor=historical,
                live_anchor=live,
                claim_root=claim,
                terminal_sha256=terminal_sha,
                bundle_path=candidate,
                bundle_sha256=bundle_sha,
                validator_module_id=id(validator_module),
            )
            capability_registry = (*capability_registry, value)
            policy = _AUDIT_POLICY.get()
            if policy is None:
                raise RehearsalV22Error("recovered-release read-only policy vanished")
            delegation = RecoveredReleaseValidatorDelegation(
                _nonce=delegation_nonce,
                binding=binding,
                capability_id=id(value),
                validator_module_id=id(validator_module),
                audit_policy_id=id(policy),
                bundle_path=candidate,
                bundle_sha256=bundle_sha,
                terminal_sha256=terminal_sha,
                creator_module_id=id(sys.modules[MODULE_NAME]),
                lifetime_id=id(object()),
            )
            record = (delegation, value, validator_module, policy)
            delegation_registry = (*delegation_registry, record)
            try:
                validate_recovered_release_capability(
                    value,
                    project_root=binding.project_root,
                )
                yield value, delegation
            finally:
                delegation_registry = tuple(
                    active for active in delegation_registry if active is not record
                )
                capability_registry = tuple(
                    active for active in capability_registry if active is not value
                )

    def validate_recovered_release_validator_delegation(
        value: RecoveredReleaseValidatorDelegation,
        *,
        recovered_release_context: RecoveredReleaseCapability,
        validator_module: ModuleType,
        project_root: Path,
        bundle_path: Path,
    ) -> ExecutionBinding:
        binding = validate_recovered_release_capability(
            recovered_release_context,
            project_root=project_root,
        )
        policy = _AUDIT_POLICY.get()
        matches = [record for record in delegation_registry if record[0] is value]
        record = matches[0] if len(matches) == 1 else None
        candidate = bundle_path.absolute()
        if (
            not isinstance(value, RecoveredReleaseValidatorDelegation)
            or value._nonce is not delegation_nonce
            or record is None
            or record[1] is not recovered_release_context
            or record[2] is not validator_module
            or record[3] is not policy
            or value.binding != binding
            or value.capability_id != id(recovered_release_context)
            or value.validator_module_id != id(validator_module)
            or value.audit_policy_id != id(policy)
            or value.bundle_path != candidate
            or value.bundle_sha256 != recovered_release_context.bundle_sha256
            or value.terminal_sha256 != recovered_release_context.terminal_sha256
            or value.creator_module_id != id(sys.modules[MODULE_NAME])
        ):
            raise RehearsalV22Error("recovered-release delegation is forged or stale")
        return binding

    def recovered_release_validation_anchors(
        value: RecoveredReleaseCapability,
        *,
        project_root: Path,
    ) -> tuple[HistoricalSelectedAnchor, LiveExecutionAnchor]:
        validate_recovered_release_capability(value, project_root=project_root)
        return value.historical_anchor, value.live_anchor

    return (
        recovered_release_validation_scope,
        validate_recovered_release_capability,
        validate_recovered_release_validator_delegation,
        recovered_release_validation_anchors,
    )


(
    _recovered_release_validation_scope,
    _validate_recovered_release_capability,
    _validate_recovered_release_validator_delegation,
    _recovered_release_validation_anchors,
) = _build_recovered_release_authority_state()


def consume_recovered_release_authorization(
    *,
    binding: ExecutionBinding,
    validator_module: ModuleType,
    recovery_authorization_path: Path,
    receipt_path: Path,
) -> JsonObject:
    """Passively consume one recovered release with no pipeline capability."""

    if (
        validator_module.__name__
        != "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
        or sys.modules.get(validator_module.__name__) is not validator_module
    ):
        raise RehearsalV22Error("recovered-release consumer lacks exact validator identity")
    bundle_path = binding.destination / BUNDLE_FILENAME
    observed_before = {
        "authorization": _tree_fingerprint(recovery_authorization_path.absolute()),
        "claim": _tree_fingerprint(
            _recovery_claim_path(
                binding,
                _validate_bundle_recovery_authorization(
                    binding,
                    _authority_reference_for_path(
                        binding.project_root,
                        recovery_authorization_path,
                        execution_head=_current_execution_head(binding.project_root),
                        label="recovered-release consumer authorization",
                    ),
                    require_current_process=False,
                    require_destination_absent=False,
                ),
            )
        ),
        "destination": _tree_fingerprint(binding.destination),
        "ledger": _tree_fingerprint(binding.ledger_root),
        "receipt": _tree_fingerprint(receipt_path.absolute()),
    }
    with _recovered_release_validation_scope(
        binding=binding,
        validator_module=validator_module,
        recovery_authorization_path=recovery_authorization_path,
        bundle_path=bundle_path,
    ) as (recovery_context, recovery_validator_delegation):
        validate_recovered_release = getattr(
            validator_module,
            "validate_recovered_release_authorization",
            None,
        )
        if not callable(validate_recovered_release):
            raise RehearsalV22Error("passive recovered-release validator API is unavailable")
        result = validate_recovered_release(
            project_root=binding.project_root,
            receipt_path=receipt_path.absolute(),
            recovery_context=recovery_context,
            recovery_validator_delegation=recovery_validator_delegation,
        )
        _validate_recovered_release_capability(
            recovery_context,
            project_root=binding.project_root,
        )
    observed_after = {
        "authorization": _tree_fingerprint(recovery_authorization_path.absolute()),
        "claim": observed_before["claim"],
        "destination": _tree_fingerprint(binding.destination),
        "ledger": _tree_fingerprint(binding.ledger_root),
        "receipt": _tree_fingerprint(receipt_path.absolute()),
    }
    # The claim was re-read in the capability immediately after validation;
    # re-resolve its path only after the read-only scope has closed.
    execution_head = _current_execution_head(binding.project_root)
    authority = _validate_bundle_recovery_authorization(
        binding,
        _authority_reference_for_path(
            binding.project_root,
            recovery_authorization_path,
            execution_head=execution_head,
            label="recovered-release post-consumption authorization",
        ),
        require_current_process=False,
        require_destination_absent=False,
    )
    observed_after["claim"] = _tree_fingerprint(
        _recovery_claim_path(binding, authority)
    )
    if observed_after != observed_before:
        raise RehearsalV22Error("recovered-release consumption changed governed evidence")
    return _object(result, "recovered-release validation result")


def _validate_post_run_control_surface(
    preflight: ControlSurface,
    observed: ControlSurface,
) -> None:
    preflight_stable = (
        preflight.implementation_commit,
        preflight.records,
        preflight.payloads,
        preflight.manifest_payload,
        preflight.merkle_root_sha256,
        preflight.ast_closure_paths,
        preflight.python_inventory,
        preflight.package_inventory,
    )
    observed_stable = (
        observed.implementation_commit,
        observed.records,
        observed.payloads,
        observed.manifest_payload,
        observed.merkle_root_sha256,
        observed.ast_closure_paths,
        observed.python_inventory,
        observed.package_inventory,
    )
    if observed_stable != preflight_stable:
        raise RehearsalV22Error("control surface drifted during selected runs")
    if not frozenset(preflight.loaded_repository_sources).issubset(
        observed.loaded_repository_sources
    ):
        raise RehearsalV22Error(
            "loaded repository sources regressed during selected runs"
        )


def _validate_official_validator_candidate(
    *,
    binding: ExecutionBinding,
    validator_module: ModuleType,
    bundle_path: Path,
) -> Path:
    """Resolve the only readable evidence root for an official validator call."""

    package_name = "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
    if (
        validator_module.__name__ == package_name
        and sys.modules.get(package_name) is validator_module
    ):
        _active_validator_execution_context(
            binding=binding,
            validator_module=validator_module,
        )
        authority = _TEMP_AUTHORITY.get()
        candidate_root = bundle_path.parent.absolute()
        if (
            binding.mode not in {"REGISTERED_OFFICIAL", "DISPOSABLE_FULL_SHAPE_TEST"}
            or authority is None
            or bundle_path.name != BUNDLE_FILENAME
            or bundle_path.absolute() != candidate_root / BUNDLE_FILENAME
            or not candidate_root.is_relative_to(authority.absolute())
            or candidate_root == authority
            or candidate_root.is_symlink()
            or not candidate_root.is_dir()
            or bundle_path.is_symlink()
            or not bundle_path.is_file()
            or candidate_root == binding.destination
        ):
            raise RehearsalV22Error("official staged candidate lacks borrowed temp-only authority")
        return candidate_root
    if validator_module.__name__ == "__main__" and sys.modules.get("__main__") is validator_module:
        _assert_locked_validator_bootstrap(binding.project_root)
        if (
            binding.mode != "REGISTERED_OFFICIAL"
            or bundle_path != binding.destination / BUNDLE_FILENAME
        ):
            raise RehearsalV22Error("standalone validator bundle path drifted")
        return binding.destination
    raise RehearsalV22Error("official validator candidate module identity drifted")


def _validate_published_validator_bundle(
    *,
    binding: ExecutionBinding,
    validator_module: ModuleType,
    bundle_path: Path,
) -> Path:
    """Authorize only an already-published bundle for release-evidence rehash.

    Producer validation must use ``_validate_official_validator_candidate`` and
    therefore can never point at the destination.  This separate helper is for
    the validator's internal release-receipt path after candidate validation and
    the single create-only rename have both completed.
    """

    package_name = "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
    if (
        validator_module.__name__ == package_name
        and sys.modules.get(package_name) is validator_module
    ):
        _active_validator_execution_context(
            binding=binding,
            validator_module=validator_module,
        )
    elif (
        validator_module.__name__ == "__main__"
        and sys.modules.get("__main__") is validator_module
        and binding.mode == "REGISTERED_OFFICIAL"
    ):
        _assert_locked_validator_bootstrap(binding.project_root)
    else:
        raise RehearsalV22Error("published validator module identity drifted")
    expected = binding.destination / BUNDLE_FILENAME
    if bundle_path.absolute() != expected or bundle_path.parent != binding.destination:
        raise RehearsalV22Error("published validator bundle path drifted")
    directory = binding.destination
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or bundle_path.is_symlink()
        or not bundle_path.is_file()
        or directory.resolve(strict=True) != directory.absolute()
        or bundle_path.resolve(strict=True) != bundle_path.absolute()
    ):
        raise RehearsalV22Error("published validator bundle is aliased or unavailable")
    return directory


@contextmanager
def _official_validator_replay_scope(
    *,
    binding: ExecutionBinding,
    validator_module: ModuleType,
    bundle_path: Path,
    implementation_commit: str,
) -> Iterator[ExecutionCapability | _ReplayCapability]:
    """Lend existing runner authority, or mint standalone temp replay authority."""

    package_validator_name = "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
    if (
        validator_module.__name__ == package_validator_name
        and sys.modules.get(package_validator_name) is validator_module
    ):
        candidate_root = _validate_official_validator_candidate(
            binding=binding,
            validator_module=validator_module,
            bundle_path=bundle_path,
        )
        capability = _active_validator_execution_context(
            binding=binding,
            validator_module=validator_module,
        )
        if candidate_root == binding.destination:
            raise RehearsalV22Error("in-process validator cannot consume published evidence")
        before = _real_path_fingerprints()
        bundle_document = _object(
            strict_json_loads(
                _regular_bytes(bundle_path, "in-process validator bundle"),
                source="in-process validator bundle",
            ),
            "in-process validator bundle",
        )
        merkle = _object(bundle_document.get("merkle"), "in-process validator Merkle")
        history = validate_live_history(binding)
        control = build_control_surface(
            binding.project_root,
            implementation_commit,
            require_current=True,
        )
        if (
            merkle.get("attempt_history_root_sha256") != history.history_root_sha256
            or merkle.get("control_surface_root_sha256") != control.merkle_root_sha256
        ):
            raise RehearsalV22Error("in-process validator candidate roots drifted")
        yield capability
        if _real_path_fingerprints() != before:
            raise RehearsalV22Error("in-process validator replay changed registered state")
        return

    if (
        binding.mode != "REGISTERED_OFFICIAL"
        or binding.project_root != REGISTERED_PROJECT_ROOT
        or bundle_path != binding.destination / BUNDLE_FILENAME
        or validator_module.__name__ != "__main__"
        or sys.modules.get("__main__") is not validator_module
        or sys.modules.get(package_validator_name) is not None
    ):
        raise RehearsalV22Error("official replay scope binding drifted")
    _assert_locked_validator_bootstrap(binding.project_root)
    before = _real_path_fingerprints()
    bundle_payload = _regular_bytes(bundle_path, "official replay bundle")
    authority_digest = _sha256(
        b"p4.2a-v2-2-official-validator-replay-authority-v1\0"
        + bytes.fromhex(_sha256(bundle_payload))
        + bytes.fromhex(implementation_commit)
    )
    authority = binding.project_root.parent / (
        ".alphapilot-p4-2a-v2-2-validator-" + authority_digest
    )
    if (
        os.path.lexists(authority)
        or authority.is_relative_to(binding.project_root)
        or binding.project_root.is_relative_to(authority)
        or any(
            authority == protected
            or authority.is_relative_to(protected)
            or protected.is_relative_to(authority)
            for protected in (
                binding.destination,
                binding.ledger_root,
                V2_1_DESTINATION,
                V2_1_EMPTY_CLAIM,
                PROTECTED_HELDOUT_ROOT,
            )
        )
    ):
        raise RehearsalV22Error(
            "official replay authority exists already or overlaps protected state"
        )
    creation_policy = _authority_creation_policy(binding, authority)
    with _audited_execution(
        creation_policy,
        validator_replay_module=validator_module,
    ):
        os.mkdir(authority, 0o700)
        _fsync_directory(authority.parent)
    policy = _AuditPolicy(
        project_root=binding.project_root,
        write_roots=(authority,),
        exact_write_paths=(),
        create_only_roots=(),
        sqlite_roots=(authority,),
        git_roots=(binding.project_root,),
        subprocess_mode="git-read",
    )
    try:
        with _audited_execution(
            policy,
            validator_replay_module=validator_module,
        ):
            authority_token = _TEMP_AUTHORITY.set(authority)
            try:
                history = validate_live_history(binding)
                control = build_control_surface(
                    binding.project_root,
                    implementation_commit,
                    require_current=True,
                )
                bundle_document = _object(
                    strict_json_loads(bundle_payload, source="official replay bundle"),
                    "official replay bundle",
                )
                merkle = _object(
                    bundle_document.get("merkle"),
                    "official replay Merkle",
                )
                if (
                    merkle.get("attempt_history_root_sha256")
                    != history.history_root_sha256
                    or merkle.get("control_surface_root_sha256")
                    != control.merkle_root_sha256
                ):
                    raise RehearsalV22Error("official replay bundle roots drifted")
                with _replay_capability_scope(
                    binding=binding,
                    validator_module=validator_module,
                    bundle_path=bundle_path,
                    implementation_commit=implementation_commit,
                    history_root_sha256=history.history_root_sha256,
                    control_merkle_root_sha256=control.merkle_root_sha256,
                    real_path_fingerprints=before,
                ) as capability:
                    yield capability
            finally:
                _TEMP_AUTHORITY.reset(authority_token)
                if os.path.lexists(authority):
                    shutil.rmtree(authority)
                    _fsync_directory(authority.parent)
    finally:
        if os.path.lexists(authority):
            with _audited_execution(
                creation_policy,
                validator_replay_module=validator_module,
            ):
                metadata = authority.lstat()
                if (
                    authority.is_symlink()
                    or not stat.S_ISDIR(metadata.st_mode)
                    or any(authority.iterdir())
                ):
                    raise RehearsalV22Error(
                        "official replay authority cleanup failed with residual bytes"
                    )
                os.rmdir(authority)
                _fsync_directory(authority.parent)
    if os.path.lexists(authority):
        raise RehearsalV22Error("official replay temp authority was not removed")
    if _real_path_fingerprints() != before:
        raise RehearsalV22Error("official validator replay changed registered state")


def _real_path_fingerprints() -> dict[str, Mapping[str, str]]:
    return {
        "registered_v2_2_destination": _tree_fingerprint(OFFICIAL_DESTINATION),
        "registered_v2_2_ledger": _tree_fingerprint(OFFICIAL_LEDGER_ROOT),
        "retired_v2_1_destination": _tree_fingerprint(V2_1_DESTINATION),
        "consumed_v2_1_claim": _tree_fingerprint(V2_1_EMPTY_CLAIM),
        "real_heldout_root": _tree_fingerprint(PROTECTED_HELDOUT_ROOT),
    }


def _fake_boundary_ids() -> tuple[int, ...]:
    return (id(_fake_pdf_fetcher), id(_fake_pdf_text_extractor))


def _artifact_paths(binding: prepare.HeldoutBinding) -> dict[str, Path]:
    return {
        **binding.artifacts,
        "synthetic_report": binding.artifacts["report_directory"] / evaluator.REPORT_FILENAME,
    }


def _workspace_artifacts(
    workspace: Path,
    source_binding: prepare.HeldoutBinding,
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for logical_name, source_relative in ARTIFACT_INVENTORY:
        if logical_name == "synthetic_report":
            continue
        paths[logical_name] = workspace.joinpath(*PurePosixPath(source_relative).parts)
    paths["report_directory"] = workspace / "docs/phase4/eval/v2-calibration/heldout/report"
    paths["synthetic_rehearsal"] = workspace / source_binding.artifacts[
        "synthetic_rehearsal"
    ].relative_to(source_binding.root)
    return paths


def _fixture_rows() -> Iterator[tuple[object, ...]]:
    offset = 0
    for source, count in FIXTURE_BY_SOURCE.items():
        for _ in range(count):
            identifier = FIXTURE_ID_START + offset
            offset += 1
            symbol = f"{identifier % 1_000_000:06d}"
            title = f"v2.1 离线排练公告 {identifier}"
            if source == "cninfo":
                url = f"https://static.cninfo.com.cn/finalpage/2026-08-06/{identifier}.PDF"
            else:
                url = f"https://example.invalid/p4-2a-v2-1/{source}/{identifier}"
            raw_payload = {
                "digest": f"合成摘要 {identifier}",
                "short": f"合成短讯 {identifier}",
                "offline_rehearsal": True,
            }
            yield (
                identifier,
                source,
                symbol,
                title,
                url,
                "2026-08-06 00:00:00",
                "2026-08-06 00:01:00",
                _sha256(f"fixture-content-{identifier}".encode()),
                json.dumps(raw_payload, ensure_ascii=False, sort_keys=True),
            )


def _create_fixture_database(path: Path) -> None:
    if os.path.lexists(path):
        raise FileExistsError(f"refusing to reuse fixture database: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    if stat.S_IMODE(path.lstat().st_mode) != 0o600:
        raise RehearsalV22Error("fixture database create-only mode drifted")
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE news_items (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                symbol TEXT,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                published_at TEXT,
                available_time TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                raw_payload TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO news_items
            (
                id, source, symbol, title, url, published_at, available_time,
                content_hash, raw_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _fixture_rows(),
        )
        connection.commit()
        if connection.execute("SELECT COUNT(*) FROM news_items").fetchone() != (FIXTURE_RAW_COUNT,):
            raise RehearsalV22Error("fixture database row count drifted")
    finally:
        connection.close()
    if stat.S_IMODE(path.lstat().st_mode) != 0o600:
        raise RehearsalV22Error("fixture database mode drifted")


def _fake_pdf_fetcher(url: str, _policy: object) -> bytes:
    identifier = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return b"%PDF-1.4\n% offline successor v2.1 " + identifier.encode("ascii") + b"\n"


def _fake_pdf_text_extractor(
    pdf_bytes: bytes,
    _policy: object,
) -> gold_builder.ExtractedPdfText:
    identifier = pdf_bytes.split()[-1].decode("ascii")
    text = (f"离线排练公告正文 {identifier}。" * 96).strip()
    return gold_builder.extracted_pdf_text_fixture(text)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        trading_mode="research",
        live_trading_enabled=False,
        paper_trading_enabled=False,
        paper_auto_trading_enabled=False,
        futu_enable_account_mutation=False,
        futu_enable_trade=False,
        llm_base_url="https://example.invalid/no-network",
        llm_api_key="offline-rehearsal-no-network",
        llm_model=prepare.MODEL,
    )


def _snapshot(symbols: frozenset[str]) -> ProductionSnapshot:
    return ProductionSnapshot(
        sqlite_uri_mode="ro",
        pragma_query_only=1,
        connection_total_changes=0,
        llm_call_count=0,
        llm_call_max_id=None,
        trade_proposal_count=0,
        broker_order_count=0,
        non_simulate_order_count=0,
        news_events_table_exists=False,
        universe_symbols=symbols,
    )


V2_1_MINT_PREREQUISITE_CONTROLS: tuple[tuple[Path, str], ...] = (
    (
        prepare.SUCCESSOR_V2_1_PREREGISTRATION_PATH,
        prepare.SUCCESSOR_V2_1_PREREGISTRATION_SHA256,
    ),
    (
        prepare.SUCCESSOR_V2_1_BUNDLE_SCHEMA_PATH,
        prepare.SUCCESSOR_V2_1_BUNDLE_SCHEMA_SHA256,
    ),
    (
        prepare.SUCCESSOR_V2_1_RELEASE_SCHEMA_PATH,
        prepare.SUCCESSOR_V2_1_RELEASE_SCHEMA_SHA256,
    ),
    (prepare.FRAME_AUTHORITY_PATH, prepare.FRAME_AUTHORITY_SHA256),
    (
        prepare.SUCCESSOR_CODE_GATE_AUTHORITY_PATH,
        prepare.SUCCESSOR_CODE_GATE_AUTHORITY_SHA256,
    ),
)


def _copy_v2_1_mint_prerequisite_controls(
    project_root: Path,
    workspace: Path,
) -> None:
    """Create-only copy the five frozen controls required to mint v2.1 authority."""

    controls = V2_1_MINT_PREREQUISITE_CONTROLS
    registered = set(prepare._registered_successor_implementation_paths(project_root))
    paths = tuple(relative for relative, _ in controls)
    if (
        len(controls) != 5
        or len(set(paths)) != 5
        or len({path.as_posix().casefold() for path in paths}) != 5
        or any(path in registered for path in paths)
    ):
        raise RehearsalV22Error(
            "v2.1 mint prerequisite control registry is duplicate or expanded"
        )
    if (
        project_root.is_symlink()
        or not project_root.is_dir()
        or project_root.resolve(strict=True) != project_root.absolute()
        or workspace.is_symlink()
        or not workspace.is_dir()
        or workspace.resolve(strict=True) != workspace.absolute()
        or workspace.is_relative_to(project_root)
        or project_root.is_relative_to(workspace)
    ):
        raise RehearsalV22Error("v2.1 mint prerequisite roots escape or alias")
    payloads: list[tuple[Path, bytes]] = []
    expected_by_target: dict[Path, bytes] = {}
    for relative, expected_sha256 in controls:
        normalized = _relative_text(
            relative.as_posix(),
            "v2.1 mint prerequisite control path",
        )
        if Path(normalized) != relative or not _lower_hex(expected_sha256, 64):
            raise RehearsalV22Error("v2.1 mint prerequisite binding is invalid")
        source = project_root / relative
        target = workspace / relative
        if (
            source.parent.resolve(strict=True) != source.parent.absolute()
            or target.resolve(strict=False) != target.absolute()
            or not source.is_relative_to(project_root)
            or not target.is_relative_to(workspace)
            or os.path.lexists(target)
        ):
            raise RehearsalV22Error(
                f"v2.1 mint prerequisite target is duplicate or aliased: {relative}"
            )
        payload = _regular_bytes(
            source,
            f"v2.1 mint prerequisite source {relative}",
            allow_zero=False,
        )
        if _sha256(payload) != expected_sha256:
            raise RehearsalV22Error(
                f"v2.1 mint prerequisite source bytes drifted: {relative}"
            )
        payloads.append((target, payload))
        expected_by_target[target] = payload
    prepare._publish_create_only(tuple(payloads))
    for target, expected_payload in expected_by_target.items():
        if _regular_bytes(
            target,
            f"copied v2.1 mint prerequisite {target.relative_to(workspace)}",
            allow_zero=False,
        ) != expected_payload:
            raise RehearsalV22Error("v2.1 mint prerequisite copied bytes drifted")


def _copy_pipeline_controls(project_root: Path, workspace: Path) -> None:
    base_runner._copy_control_surface(project_root, workspace)
    _copy_v2_1_mint_prerequisite_controls(project_root, workspace)
    payloads: list[tuple[Path, bytes]] = []
    for relative_path in prepare._registered_successor_implementation_paths(project_root):
        relative = relative_path.as_posix()
        source = _safe_path(project_root, relative, f"pipeline control {relative}")
        payload = _regular_bytes(source, f"pipeline control {relative}", allow_zero=False)
        target = workspace / relative_path
        if os.path.lexists(target):
            if _regular_bytes(target, f"copied pipeline control {relative}") != payload:
                raise RehearsalV22Error(f"copied pipeline control drifted: {relative}")
            continue
        payloads.append((target, payload))
    if payloads:
        prepare._publish_create_only(tuple(payloads))


def _temporary_binding(
    *,
    project_root: Path,
    workspace: Path,
) -> tuple[prepare.HeldoutBinding, Path]:
    if workspace.is_relative_to(project_root) or project_root.is_relative_to(workspace):
        raise RehearsalV22Error("pipeline workspace overlaps its synthetic repository")
    _copy_pipeline_controls(project_root, workspace)
    source_binding = prepare.load_binding(project_root)
    binding = replace(
        source_binding,
        root=workspace,
        artifacts=_workspace_artifacts(workspace, source_binding),
    )
    database = workspace / "data/alphapilot.db"
    _create_fixture_database(database)
    fixture_ids = set(range(FIXTURE_ID_START, FIXTURE_ID_START + FIXTURE_RAW_COUNT))
    if fixture_ids & binding.retired_ids:
        raise RehearsalV22Error("fixture ids intersect retired held-out ids")
    return binding, database


def _full_inference_harness(
    binding: prepare.HeldoutBinding,
    *,
    timing_clock: DeterministicClock,
) -> InferenceHarness:
    expected_ids = tuple(range(FIXTURE_ID_START, FIXTURE_ID_START + FIXTURE_RAW_COUNT))
    symbols = frozenset(str(row[2]) for row in _fixture_rows())
    snapshot = _snapshot(symbols)
    calls: list[int] = []

    def snapshot_loader(root: Path) -> ProductionSnapshot:
        if root.resolve() != binding.root.resolve():
            raise RehearsalV22Error("snapshot loader escaped offline workspace")
        return snapshot

    def mocked_model(
        purpose: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        timeout: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 1,
        settings: Settings | None = None,
        session: Session | None = None,
    ) -> dict[str, Any]:
        if (
            purpose != "p4_news_event_extract"
            or not system
            or not schema
            or timeout != 20.0
            or max_tokens != 2_000
            or max_retries != 0
            or settings is None
            or settings.llm_model != prepare.MODEL
            or session is None
        ):
            raise RehearsalV22Error("mocked model request contract drifted")
        payload = strict_json_loads(user.encode("utf-8"), source="mocked model input")
        document = _object(payload, "mocked model input")
        identifier = document.get("news_item_id")
        evidence = document.get("evidence_candidates")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or not isinstance(evidence, list)
            or not evidence
            or not isinstance(evidence[0], list)
            or len(evidence[0]) != 4
            or len(calls) >= len(expected_ids)
            or identifier != expected_ids[len(calls)]
        ):
            raise RehearsalV22Error("mocked model input or call order drifted")
        calls.append(identifier)
        session.add(
            LLMCall(
                purpose=purpose,
                model=prepare.MODEL,
                latency_ms=0,
                ok=True,
                prompt_tokens=0,
                completion_tokens=0,
                error=None,
            )
        )
        session.flush()
        first = evidence[0]
        return {
            "symbols": [str(document.get("ingested_symbol"))],
            "event_type": "other",
            "direction": 0,
            "materiality": 2 if len(calls) <= 100 else 1,
            "summary": "v2.1 离线排练结构化结果。",
            "confidence": 1.0,
            "evidence_candidate_id": first[0],
        }

    return InferenceHarness(
        settings=_settings(),
        chat_json_fn=mocked_model,
        snapshot_loader=snapshot_loader,
        wall_clock=lambda: FIXED_WALL_CLOCK,
        execution_id_factory=lambda: str(uuid.uuid5(UUID_NAMESPACE, "v2.1-inference\0full-pool")),
        prediction_recorded_at_clock=lambda: FIXED_WALL_CLOCK_TEXT,
        prediction_monotonic_ns_clock=lambda: int(timing_clock.monotonic() * 1_000_000_000),
        calls=calls,
    )


def _mocked_inference(
    binding: prepare.HeldoutBinding,
    *,
    execution_context: prepare._OfflineRehearsalCapability,
    harness: InferenceHarness,
) -> None:
    prepare.run_infer(
        binding,
        execution_context=execution_context,
        settings=harness.settings,
        chat_json_fn=harness.chat_json_fn,
        snapshot_loader=harness.snapshot_loader,
        clock=harness.wall_clock,
        execution_id_factory=harness.execution_id_factory,
        prediction_recorded_at_clock=harness.prediction_recorded_at_clock,
        prediction_monotonic_ns_clock=harness.prediction_monotonic_ns_clock,
    )
    if len(harness.calls) != FIXTURE_RAW_COUNT:
        raise RehearsalV22Error("mocked inference did not call every candidate once")


def _adjudication_contract(
    binding: prepare.HeldoutBinding,
) -> base_seal.V2AdjudicationContract:
    source = heldout_seal.load_registered_contract(
        binding.root / heldout_seal.DESIGN_RELATIVE_PATH,
        project_root=binding.root,
    )
    return replace(
        source,
        project_root=binding.root,
        artifacts={
            "development_private_selection_manifest": binding.artifacts["private_selection"],
            "development_owner_blind_jsonl": binding.artifacts["owner_blind"],
            "development_ai_draft_jsonl": binding.artifacts["ai_draft"],
            "development_adjudication_html": binding.artifacts["adjudication_ui"],
            "development_owner_raw_export_jsonl": binding.artifacts["owner_export"],
            "development_human_adjudicated_jsonl": binding.artifacts["human_adjudicated"],
            "development_owner_completion_manifest": binding.artifacts["owner_completion"],
        },
    )


def _candidate_drafts(blind_rows: Sequence[Mapping[str, Any]]) -> list[JsonObject]:
    return [
        {
            "schema_version": base_seal.CANDIDATE_DRAFT_SCHEMA,
            "news_item_id": row["news_item_id"],
            "draft_label": {
                "symbols": [row["ingested_symbol"]],
                "event_type": "other",
                "direction": 0,
                "materiality": 1,
                "evidence_span": row["original_text"],
                "notes": None,
            },
        }
        for row in blind_rows
    ]


def _owner_export(
    blind_rows: Sequence[Mapping[str, Any]],
    draft_rows: Sequence[Mapping[str, Any]],
    *,
    contract: base_seal.V2AdjudicationContract,
) -> list[JsonObject]:
    result: list[JsonObject] = []
    for index, (blind, draft) in enumerate(zip(blind_rows, draft_rows, strict=True), 1):
        label = copy.deepcopy(draft["draft_label"])
        result.append(
            {
                "schema_version": "p4.2a-v2-owner-adjudication-export-item-v1",
                "design": dict(contract.design_ref),
                "frame_id": contract.frame_id,
                "sample_index": index,
                "news_item_id": blind["news_item_id"],
                "input_sha256": blind["input_sha256"],
                "sealed_draft_item_sha256": _sha256(base_seal.canonical_json_bytes(draft)),
                "draft_label": label,
                "human_label": copy.deepcopy(label),
                "annotation_status": "adjudicated",
                "adjudication": {
                    "method": "ai_drafted_human_adjudicated",
                    "drafter_id": heldout_seal.EXPECTED_DRAFTER_ID,
                    "adjudicator_id": heldout_ui.EXPECTED_ADJUDICATOR_ID,
                    "confirmed": True,
                    "changed": False,
                    "changed_fields": [],
                    "adjudicated_at": FIXED_WALL_CLOCK_TEXT,
                },
            }
        )
    return result


def _run_owner_chain(
    binding: prepare.HeldoutBinding,
    *,
    execution_context: prepare._OfflineRehearsalCapability,
) -> None:
    contract = _adjudication_contract(binding)
    blind_rows, blind_payload, selection_payload, inference_completed_at = (
        heldout_seal.read_bound_blind_bundle(
            binding.artifacts["private_selection"],
            binding.artifacts["owner_blind"],
            contract=contract,
            execution_context=execution_context,
            stage="seal-draft",
        )
    )
    sealed = heldout_seal.seal_candidate_rows(
        blind_rows,
        _candidate_drafts(blind_rows),
        contract=contract,
        drafter_id=heldout_seal.EXPECTED_DRAFTER_ID,
        drafted_at=FIXED_WALL_CLOCK_TEXT,
        inference_completed_at=inference_completed_at,
    )
    draft_payload = base_seal.canonical_jsonl_bytes(sealed)
    base_seal.write_create_only(binding.artifacts["ai_draft"], draft_payload)
    prepare.validate_v2_1_stage_authorization(
        binding,
        stage="build-adjudication-ui",
        execution_context=execution_context,
    )
    ui_payload, count = heldout_ui.render_registered_ui_payload(
        blind_rows,
        sealed,
        contract=contract,
        blind_payload=blind_payload,
        draft_payload=draft_payload,
        selection_payload=selection_payload,
    )
    if count != 60:
        raise RehearsalV22Error("offline adjudication UI row count drifted")
    base_seal.write_create_only(binding.artifacts["adjudication_ui"], ui_payload)
    export_payload = base_seal.canonical_jsonl_bytes(
        _owner_export(blind_rows, sealed, contract=contract)
    )
    candidate_export = binding.root / "owner-export-candidate.jsonl"
    base_seal.write_create_only(candidate_export, export_payload)
    summary, _completion, _hashes = heldout_finalizer.finalize_owner_export(
        contract=contract,
        owner_export_path=candidate_export,
        completed_at=FIXED_WALL_CLOCK_TEXT,
        execution_context=execution_context,
    )
    if summary.get("row_count") != 60:
        raise RehearsalV22Error("offline finalizer row count drifted")


def _evaluation_paths(binding: prepare.HeldoutBinding) -> evaluator.ArtifactPaths:
    paths = _artifact_paths(binding)
    return evaluator.ArtifactPaths(
        artifact_root=binding.root,
        materialized_inputs=paths["materialized_inputs"],
        materialization_manifest=paths["materialization_manifest"],
        inference_state=paths["inference_state"],
        predictions=paths["predictions"],
        prediction_manifest=paths["prediction_manifest"],
        selection=paths["private_selection"],
        blind=paths["owner_blind"],
        draft=paths["ai_draft"],
        adjudication_ui=paths["adjudication_ui"],
        owner_export=paths["owner_export"],
        human_adjudicated=paths["human_adjudicated"],
        owner_completion=paths["owner_completion"],
        evaluation_state=paths["evaluation_state"],
        report=paths["synthetic_report"],
    )


def _run_synthetic_evaluator(
    binding: prepare.HeldoutBinding,
    *,
    execution_context: prepare._OfflineRehearsalCapability,
) -> None:
    paths = _evaluation_paths(binding)
    dry = evaluator.dry_run(
        root=binding.root,
        paths=paths,
        clock=lambda: FIXED_WALL_CLOCK,
        execution_context=execution_context,
    )
    if dry.get("status") != "passed" or dry.get("filesystem_mutations") != 0:
        raise RehearsalV22Error("synthetic evaluator dry-run failed")
    preflight = evaluator.load_preflight(
        root=binding.root,
        paths=paths,
        execution_context=execution_context,
    )
    started = {
        "schema_version": "p4.2a-v2-heldout-evaluation-state-event-v1",
        "event": "evaluation_started",
        "at_utc": FIXED_WALL_CLOCK_TEXT,
        "synthetic_rehearsal": True,
        "design_sha256": prepare.DESIGN_SHA256,
        "preregistration_sha256": prepare.PREREGISTRATION_SHA256,
        "selected_model": prepare.MODEL,
        "input_hashes": dict(preflight.hashes),
        "attempt_number": 0,
        "maximum_real_attempts_consumed": 0,
        "retries": 0,
    }
    evaluator._create_only(
        paths.evaluation_state,
        evaluator._canonical_json_bytes(started),
    )
    synthetic_human, synthetic_predictions = evaluator._synthetic_score_inputs(preflight)
    metrics = evaluator.score_heldout(
        preflight.selected,
        synthetic_predictions,
        synthetic_human,
    )
    report = evaluator._report_payload(
        preflight,
        metrics,
        completed_at=FIXED_WALL_CLOCK_TEXT,
        authorization=None,
        synthetic=True,
    )
    report_payload = evaluator._canonical_json_bytes(report)
    evaluator._create_only(paths.report, report_payload)
    evaluator._append_terminal(
        paths.evaluation_state,
        {
            "schema_version": "p4.2a-v2-heldout-evaluation-state-event-v1",
            "event": "evaluation_completed",
            "at_utc": FIXED_WALL_CLOCK_TEXT,
            "synthetic_rehearsal": True,
            "real_heldout_metrics_computed": False,
            "one_shot_consumed": False,
            "report_path": paths.report.relative_to(binding.root).as_posix(),
            "report_sha256": _sha256(report_payload),
            "retries": 0,
        },
    )


def _pipeline_probe_evidence(
    *,
    run_label: str,
    clock: DeterministicClock,
    artifacts: Mapping[str, bytes],
) -> dict[str, JsonObject]:
    manifest = _object(
        strict_json_loads(
            artifacts["materialization_manifest"],
            source="materialization manifest probe evidence",
        ),
        "materialization manifest probe evidence",
    )
    pacing = _object(manifest.get("request_pacing"), "manifest request_pacing")
    cninfo = _object(pacing.get("cninfo_pdf"), "manifest cninfo pacing")
    if (
        cninfo.get("request_start_count") != CNINFO_REQUEST_COUNT
        or cninfo.get("observed_gap_count") != CNINFO_GAP_COUNT
        or cninfo.get("minimum_observed_start_to_start_seconds") != 1.0
        or cninfo.get("median_observed_start_to_start_seconds") != 1.0
        or cninfo.get("violation_count") != 0
        or clock.seconds != MONOTONIC_INITIAL_SECONDS + float(CNINFO_GAP_COUNT)
    ):
        raise RehearsalV22Error("CNInfo pacing evidence drifted")
    common = {
        "status": "PASS",
        "run_label": run_label,
        "real_database_reads": 0,
        "real_network_calls": 0,
        "real_model_calls": 0,
    }
    return {
        "cninfo_one_second_pacing": {
            **common,
            "request_start_count": CNINFO_REQUEST_COUNT,
            "observed_gap_count": CNINFO_GAP_COUNT,
            "minimum_observed_gap_seconds": 1.0,
            "median_observed_gap_seconds": 1.0,
            "violation_count": 0,
        },
        "zero_retry_model_contract": {
            **common,
            "call_count": FIXTURE_RAW_COUNT,
            "max_retries": 0,
        },
        "deterministic_ineligible_zero_retry": {
            **common,
            "registered_reasons": [
                "pdf_text_below_min_char_gate",
                "pdf_exceeds_size_bound",
            ],
            "retry_count": 0,
            "return_to_pool_count": 0,
        },
        "unexpected_failure_aborts": {
            **common,
            "retry_count": 0,
            "partial_publish_count": 0,
        },
        "consumer_stage_gates": {
            **common,
            "seal_draft": "PRIVATE_OFFLINE_CAPABILITY_REVALIDATED",
            "build_adjudication_ui": "PRIVATE_OFFLINE_CAPABILITY_REVALIDATED",
            "finalize_owner_adjudication": "PRIVATE_OFFLINE_CAPABILITY_REVALIDATED",
            "evaluation": "SYNTHETIC_ONLY_PRIVATE_OFFLINE_CAPABILITY_REVALIDATED",
        },
    }


def _execute_pipeline_inner(
    *,
    project_root: Path,
    workspace: Path,
    run_label: str,
) -> tuple[dict[str, bytes], dict[str, JsonObject]]:
    binding, database = _temporary_binding(
        project_root=project_root,
        workspace=workspace,
    )
    clock = DeterministicClock()
    materialization_monotonic = clock.monotonic
    materialization_sleep = clock.sleep
    inference = _full_inference_harness(binding, timing_clock=clock)
    capability = prepare._mint_v2_1_offline_rehearsal_capability(
        binding,
        database=database,
        pdf_fetcher=_fake_pdf_fetcher,
        pdf_text_extractor=_fake_pdf_text_extractor,
        monotonic=materialization_monotonic,
        sleep=materialization_sleep,
        inference_settings=inference.settings,
        chat_json_fn=inference.chat_json_fn,
        snapshot_loader=inference.snapshot_loader,
        wall_clock=inference.wall_clock,
        execution_id_factory=inference.execution_id_factory,
        prediction_recorded_at_clock=inference.prediction_recorded_at_clock,
        prediction_monotonic_ns_clock=inference.prediction_monotonic_ns_clock,
        implementation_commit=V2_1_IMPLEMENTATION_COMMIT,
    )
    prepare.run_materialize(
        binding,
        operator_timing_attestation=None,
        database=database,
        pdf_fetcher=_fake_pdf_fetcher,
        pdf_text_extractor=_fake_pdf_text_extractor,
        execution_context=capability,
        monotonic=materialization_monotonic,
        sleep=materialization_sleep,
    )
    _mocked_inference(binding, execution_context=capability, harness=inference)
    prepare.run_select_blind(binding, execution_context=capability)
    _run_owner_chain(binding, execution_context=capability)
    _run_synthetic_evaluator(binding, execution_context=capability)
    paths = _artifact_paths(binding)
    artifacts: dict[str, bytes] = {}
    for logical_name, expected_relative in ARTIFACT_INVENTORY:
        path = paths[logical_name]
        if (
            path.is_symlink()
            or not path.is_file()
            or path.relative_to(workspace).as_posix() != expected_relative
        ):
            raise RehearsalV22Error(f"full path omitted artifact: {logical_name}")
        payload = _regular_bytes(path, f"full path artifact {logical_name}")
        if workspace.as_posix().encode() in payload or workspace.as_uri().encode() in payload:
            raise RehearsalV22Error(f"temporary path leaked into artifact: {logical_name}")
        artifacts[logical_name] = payload
    if len(artifacts) != 14:
        raise RehearsalV22Error("full path did not produce the exact 14 artifacts")
    probes = _pipeline_probe_evidence(
        run_label=run_label,
        clock=clock,
        artifacts=artifacts,
    )
    return artifacts, probes


def replay_selected_pipeline(
    *,
    binding: ExecutionBinding,
    implementation_commit: str,
    run_label: str,
    execution_context: ExecutionCapability | _ReplayCapability,
    validator_mode: bool = True,
    evidence_sink: Callable[[str, bytes], None] | None = None,
) -> PipelineReplay:
    """Replay one selected full path inside the active private write authority."""

    if isinstance(execution_context, _ReplayCapability):
        observed = _validate_replay_capability(execution_context)
    else:
        observed = _validate_execution_capability(
            execution_context,
            project_root=binding.project_root,
        )
    if observed != binding or binding.mode not in {
        "DISPOSABLE_FULL_SHAPE_TEST",
        "REGISTERED_OFFICIAL",
    }:
        raise RehearsalV22Error("full-path replay requires exact private authority")
    if not validator_mode:
        raise RehearsalV22Error("full-path replay is reserved for active evidence validation")
    if not re.fullmatch(r"run-[ab]", run_label):
        raise RehearsalV22Error("full-path replay label is not registered")
    if not isinstance(execution_context, _ReplayCapability):
        _record_replay_observation(execution_context, run_label)
    validate_implementation_blob(
        binding.project_root,
        implementation_commit,
        IMPLEMENTATION_RELATIVE.as_posix(),
    )
    authority = _TEMP_AUTHORITY.get()
    policy = _AUDIT_POLICY.get()
    if (
        authority is None
        or policy is None
        or not _audit_policy_is_issued(policy)
        or not _path_in_roots(authority, policy.write_roots)
    ):
        raise RehearsalV22Error("full-path replay lacks issued temporary authority")
    workspace = Path(tempfile.mkdtemp(prefix=f"v2-2-{run_label}-", dir=authority)).resolve(
        strict=True
    )
    removed = False
    try:
        try:
            artifacts, probes = _execute_pipeline_inner(
                project_root=binding.project_root,
                workspace=workspace,
                run_label=run_label,
            )
        except BaseException:
            if evidence_sink is not None:
                for logical_name, relative in ARTIFACT_INVENTORY:
                    candidate = workspace.joinpath(*PurePosixPath(relative).parts)
                    if os.path.lexists(candidate):
                        payload = _regular_bytes(
                            candidate,
                            f"partial {run_label} artifact {logical_name}",
                        )
                        evidence_sink(
                            f"partial/{run_label}/{relative}",
                            payload,
                        )
            raise
        if evidence_sink is not None:
            for logical_name, relative in ARTIFACT_INVENTORY:
                evidence_sink(f"runs/{run_label}/root/{relative}", artifacts[logical_name])
            evidence_sink(
                f"probes/{run_label}.json",
                _canonical_json_bytes(probes),
            )
    finally:
        shutil.rmtree(workspace)
        removed = not os.path.lexists(workspace)
    if not removed:
        raise RehearsalV22Error("full-path replay workspace was not removed")
    return PipelineReplay(
        run_label=run_label,
        artifacts=artifacts,
        probe_evidence=probes,
        write_root=workspace,
        removed=True,
    )


@dataclass(frozen=True)
class _HistoryArchive:
    summary: JsonObject
    archive_record: JsonObject
    payloads: Mapping[str, bytes]
    history_root_sha256: str
    live_ledger_root_sha256: str
    selected_record: ValidatedAttemptRecord


@dataclass(frozen=True)
class _BundleAssembly:
    document: JsonObject
    payloads: Mapping[str, bytes]
    bundle_payload: bytes
    bundle_root_sha256: str


def _schema_parts(schema: JsonObject, node: object) -> tuple[JsonObject, ...]:
    document = _object(node, "schema node")
    parts: list[JsonObject] = []
    reference = document.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise RehearsalV22Error("bundle schema contains an external reference")
        cursor: object = schema
        for raw in reference[2:].split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            if not isinstance(cursor, dict) or token not in cursor:
                raise RehearsalV22Error("bundle schema reference is unresolved")
            cursor = cursor[token]
        parts.extend(_schema_parts(schema, cursor))
    all_of = document.get("allOf")
    if all_of is not None:
        for child in _array(all_of, "schema allOf"):
            parts.extend(_schema_parts(schema, child))
    local = {key: value for key, value in document.items() if key not in {"$ref", "allOf"}}
    if local:
        parts.append(local)
    return tuple(parts)


def _schema_const_template(
    schema: JsonObject,
    node: object,
    *,
    omit: frozenset[str] = frozenset(),
) -> object:
    parts = _schema_parts(schema, node)
    constants = [part["const"] for part in parts if "const" in part]
    if constants:
        if any(not _typed_json_equal(constants[0], value) for value in constants[1:]):
            raise RehearsalV22Error("bundle schema contains contradictory constants")
        return copy.deepcopy(constants[0])
    required: list[str] = []
    property_nodes: dict[str, list[object]] = {}
    for part in parts:
        raw_required = part.get("required")
        if raw_required is not None:
            for value in _array(raw_required, "schema required"):
                if not isinstance(value, str):
                    raise RehearsalV22Error("bundle schema required name is invalid")
                if value not in required:
                    required.append(value)
        raw_properties = part.get("properties")
        if raw_properties is not None:
            for key, value in _object(raw_properties, "schema properties").items():
                property_nodes.setdefault(key, []).append(value)
    if not required:
        raise RehearsalV22Error("schema node is not a materializable constant object")
    result: JsonObject = {}
    for key in required:
        if key in omit:
            continue
        candidates = property_nodes.get(key)
        if not candidates:
            raise RehearsalV22Error(f"schema constant property is unresolved: {key}")
        result[key] = _schema_const_template(
            schema,
            {"allOf": candidates},
        )
    return result


def _bundle_schema(
    project_root: Path,
    *,
    historical_selected_commit: str | None = None,
) -> JsonObject:
    payload = (
        _regular_bytes(project_root / BUNDLE_SCHEMA_RELATIVE, "v2.2 bundle schema")
        if historical_selected_commit is None
        else _git_blob(
            project_root,
            historical_selected_commit,
            BUNDLE_SCHEMA_RELATIVE.as_posix(),
        )
    )
    if _sha256(payload) != BUNDLE_SCHEMA_SHA256:
        raise RehearsalV22Error("v2.2 bundle schema bytes drifted")
    return _object(strict_json_loads(payload, source="v2.2 bundle schema"), "bundle schema")


def _schema_definition(schema: JsonObject, name: str) -> JsonObject:
    definitions = _object(schema.get("$defs"), "bundle schema definitions")
    if name not in definitions:
        raise RehearsalV22Error(f"bundle schema definition is absent: {name}")
    return _object(definitions[name], f"bundle schema definition {name}")


def _constant_section(schema: JsonObject, name: str) -> JsonObject:
    return _object(
        _schema_const_template(schema, _schema_definition(schema, name)),
        f"constant bundle section {name}",
    )


def _verified_file_ref(
    project_root: Path,
    relative: str,
    digest: str,
    *,
    historical_selected_commit: str | None = None,
) -> JsonObject:
    payload = (
        _regular_bytes(
            _safe_path(project_root, relative, f"lineage file {relative}"),
            f"lineage file {relative}",
        )
        if historical_selected_commit is None
        else _git_blob(project_root, historical_selected_commit, relative)
    )
    if _sha256(payload) != digest:
        raise RehearsalV22Error(f"lineage file bytes drifted: {relative}")
    return {"path": relative, "sha256": digest}


_LINEAGE_FILE_REFS: Mapping[str, tuple[str, str]] = {
    "parent_heldout_preregistration": (
        "docs/phase4/reports/P4.2a-v2-heldout-preregistration-20260810.json",
        "ccecbf5ca7b48b16e445318b8c94a08927432f92c7e8c12f8ab40f2916578705",
    ),
    "parent_rehearsal_v2_preregistration": (
        "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-preregistration-20260810.json",
        "35b6d757876e1308d8f28ded3dc36784afb4e5d7c5c1589b8c211cc079aac7c3",
    ),
    "parent_rehearsal_v2_bundle_schema": (
        "config/schemas/p4_2a_v2_heldout_rehearsal_bundle_v2.schema.json",
        "f5ff0516c58f2285302dab5d1a03daafd70ea887d4f323387d44c7c19623a8bc",
    ),
    "parent_rehearsal_v2_bundle": (
        "docs/phase4/rehearsals/P4.2a-v2-calibration-v2/bundle.json",
        "0f3cbb3fe0251994da457e1a8d36a09b06ba127a8f0b584a2d924eb02e47b01f",
    ),
    "parent_rehearsal_v2_review_request": (
        "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-implementation-and-execution-review-request-20260810.json",
        "c6d818eb2e67cb1b9b47f282526e93757dc6e5d6231f1abf3d1486a4e43434e1",
    ),
    "parent_rehearsal_v2_approval": (
        "docs/phase4/reports/P4.2a-rehearsal-v2-approval-and-frame-authority-ruling-20260810.json",
        "8605dd30dd6bffa9621b6efe5e01c4c9cead615c9639259c2e79e14fcbbc3421",
    ),
    "frame_authority_ruling": (
        "docs/phase4/reports/P4.2a-rehearsal-v2-approval-and-frame-authority-ruling-20260810.json",
        "8605dd30dd6bffa9621b6efe5e01c4c9cead615c9639259c2e79e14fcbbc3421",
    ),
    "successor_v2_1_authorization": (
        "docs/phase4/reports/P4.2a-successor-v2-1-code-gate-authorization-20260810.json",
        "e28db692dc150983f86f6760fb1a95584d8607658e8a78a0de35cf3fc81940cd",
    ),
    "full_pool_cost_acceptance": (
        "docs/phase4/reports/P4.2a-heldout-full-pool-inference-cost-acceptance-20260810.json",
        "7555b4e7ade255d0d947f2e9005246a490be92db71c8db1e559e616572e033e7",
    ),
    "same_publisher_interval_basis": (
        "config/p4_news_poll_v2_1.yaml",
        "9d56e137baf10bd0858723a93aff02c57bf7b35f8705f1817b16a89ec615183f",
    ),
    "v1_incident": (
        "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v1-incident-20260810.json",
        "c3224b288f5181131351ae711a673ce94ec603375925d0cc968cef85d103e785",
    ),
    "design": (
        "config/p4_event_evaluation_v2.yaml",
        "18a2428a4ec04bfea6e4f4d70692f38ea82fbaee5a223f30f2465b895b238e21",
    ),
    "heldout_contract": (
        "config/p4_event_extract_eval_v2-heldout-qwen3.6-plus.yaml",
        "26be1765204b122908e7bd09cac857c33bd3140233df47dc3358bc590e020199",
    ),
    "round3_prompt": (
        "config/prompts/p4_news_event_extract_v2-r3.txt",
        "0291dc882aac42878ba00c4ed3970da72f19508308cd39211467b4fd92294f44",
    ),
    "round3_plus_contract": (
        "config/p4_event_extract_eval_v2-r3-qwen3.6-plus.yaml",
        "fa75a6cf33065745d02f74fe39e4f102723da43f37ac549058bb34fa8256a181",
    ),
}


def _bundle_lineage(
    project_root: Path,
    *,
    implementation_commit: str,
    historical_selected_commit: str | None = None,
) -> JsonObject:
    if (
        historical_selected_commit is not None
        and implementation_commit != historical_selected_commit
    ):
        raise RehearsalV22Error("historical bundle lineage commit binding drifted")
    lineage: JsonObject = {
        "preregistration": _verified_file_ref(
            project_root,
            PREREGISTRATION_RELATIVE.as_posix(),
            PREREGISTRATION_SHA256,
            historical_selected_commit=historical_selected_commit,
        ),
        "bundle_schema": _verified_file_ref(
            project_root,
            BUNDLE_SCHEMA_RELATIVE.as_posix(),
            BUNDLE_SCHEMA_SHA256,
            historical_selected_commit=historical_selected_commit,
        ),
        "release_authorization_schema": _verified_file_ref(
            project_root,
            RELEASE_SCHEMA_RELATIVE.as_posix(),
            RELEASE_SCHEMA_SHA256,
            historical_selected_commit=historical_selected_commit,
        ),
        "v1_fail_close_commit": V1_FAIL_CLOSE_COMMIT,
        "preregistration_commit": PREREGISTRATION_COMMIT,
        "implementation_commit": implementation_commit,
        "retired_v1_artifacts": [
            _verified_file_ref(
                project_root,
                path,
                digest,
                historical_selected_commit=historical_selected_commit,
            )
            for path, digest in base_runner.RETIRED_V1_REFERENCES
        ],
        "v2_1_implementation_commit": V2_1_IMPLEMENTATION_COMMIT,
        "v2_2_remediation_request": AuthorityReference(
            REMEDIATION_RELATIVE.as_posix(), REMEDIATION_SHA256, REMEDIATION_COMMIT
        ).as_json(),
        "v2_2_preregistration_scope_authorization": AuthorityReference(
            SCOPE_AUTHORIZATION_RELATIVE.as_posix(),
            SCOPE_AUTHORIZATION_SHA256,
            SCOPE_AUTHORIZATION_COMMIT,
        ).as_json(),
    }
    for key, (path, digest) in _LINEAGE_FILE_REFS.items():
        lineage[key] = _verified_file_ref(
            project_root,
            path,
            digest,
            historical_selected_commit=historical_selected_commit,
        )
    for key, (path, digest, creating_commit) in CARRY_FORWARD_AUTHORITIES.items():
        lineage[key] = AuthorityReference(path, digest, creating_commit).as_json()
    return lineage


def _run_archive(
    replay: PipelineReplay | _SealedPipelineReplay,
) -> tuple[JsonObject, dict[str, bytes], str]:
    archive_root = f"archive/{replay.run_label}/root"
    records: list[JsonObject] = []
    archive_payloads: dict[str, bytes] = {}
    merkle_payloads: dict[str, bytes] = {}
    for logical_name, source_relative in ARTIFACT_INVENTORY:
        payload = replay.artifacts.get(logical_name)
        if payload is None:
            raise RehearsalV22Error(f"run archive omitted {logical_name}")
        records.append(
            {
                "logical_name": logical_name,
                "source_relative_path": source_relative,
                "bytes": len(payload),
                "sha256": _sha256(payload),
            }
        )
        archive_payloads[f"{archive_root}/{source_relative}"] = payload
        merkle_payloads[source_relative] = payload
    if len(replay.artifacts) != len(records):
        raise RehearsalV22Error("run archive contains an unexpected artifact")
    root = _generic_merkle_root(merkle_payloads)
    return (
        {
            "run_label": replay.run_label,
            "archive_root": archive_root,
            "artifact_count": 14,
            "artifacts": records,
            "artifact_merkle_root_sha256": root,
        },
        archive_payloads,
        root,
    )


def _control_archive(
    control: ControlSurface,
) -> tuple[JsonObject, dict[str, bytes]]:
    manifest_relative = "archive/control-surface/manifest.json"
    manifest_record = {
        "logical_name": "control_surface_manifest",
        "bundle_relative_path": manifest_relative,
        "source_kind": "control_manifest",
        "repository_path": None,
        "bytes": len(control.manifest_payload),
        "sha256": _sha256(control.manifest_payload),
    }
    payloads = dict(control.payloads)
    payloads[manifest_relative] = control.manifest_payload
    referenced = [RUNNER_TEST_RELATIVE.as_posix(), VALIDATOR_TEST_RELATIVE.as_posix()]
    archived_paths = {
        cast(str, row["repository_path"])
        for row in control.records
        if row.get("repository_path") is not None
    }
    if not set(referenced).issubset(archived_paths):
        raise RehearsalV22Error("control archive omitted a registered PASS test")
    return (
        {
            "archive_root": "archive/control-surface/root",
            "manifest": manifest_record,
            "file_count": len(control.records),
            "tree_member_count": len(control.records) + 1,
            "tree_member_count_rule": "tree_member_count == file_count + 1",
            "manifest_included_in_merkle": True,
            "files": list(control.records),
            "merkle_root_sha256": control.merkle_root_sha256,
            "referenced_pass_test_count": len(referenced),
            "referenced_pass_test_paths": referenced,
            "all_referenced_pass_tests_archived": True,
        },
        payloads,
    )


def _attempt_file_evidence(
    *,
    live_relative: str,
    archive_relative: str,
    payload: bytes,
) -> JsonObject:
    return {
        "live_relative_path": live_relative,
        "archive_relative_path": archive_relative,
        "bytes": len(payload),
        "sha256": _sha256(payload),
    }


def _history_archive(
    binding: ExecutionBinding,
    history: HistoryValidation,
) -> _HistoryArchive:
    if (
        not history.ledger_exists
        or not history.series_closed
        or history.validated_candidate_count != 1
        or history.selected_attempt_ordinal is None
        or history.live_ledger_root_sha256 is None
        or len(history.records) != history.started_count
    ):
        raise RehearsalV22Error("bundle history is not one closed disclosed series")
    execution_head = _current_execution_head(binding.project_root)
    payloads: dict[str, bytes] = {}
    bundle_records: list[JsonObject] = []
    for relative in ("series.json", ".series.lock"):
        live = binding.ledger_root / relative
        payloads[f"archive/attempt-history/{relative}"] = _regular_bytes(
            live,
            f"live ledger {relative}",
        )
    for record in history.records:
        prefix = f"attempts/{record.ordinal:06d}"
        archive_prefix = f"archive/attempt-history/{prefix}"
        started_relative = f"{prefix}/started.json"
        started_archive = f"{archive_prefix}/started.json"
        payloads[started_archive] = record.started_bytes
        candidate_evidence: JsonObject | None = None
        terminal_evidence: JsonObject | None = None
        if record.candidate_bytes is not None:
            candidate_archive = f"{archive_prefix}/candidate.json"
            payloads[candidate_archive] = record.candidate_bytes
            candidate_evidence = _attempt_file_evidence(
                live_relative=f"{prefix}/candidate.json",
                archive_relative=candidate_archive,
                payload=record.candidate_bytes,
            )
        if record.terminal_bytes is not None:
            terminal_archive = f"{archive_prefix}/terminal.json"
            payloads[terminal_archive] = record.terminal_bytes
            terminal_evidence = _attempt_file_evidence(
                live_relative=f"{prefix}/terminal.json",
                archive_relative=terminal_archive,
                payload=record.terminal_bytes,
            )
        action_payload = validate_unique_a_authority(
            binding.project_root,
            record.owner_action_time_authorization,
            execution_head=execution_head,
            require_current=False,
        )
        if _sha256(action_payload) != record.owner_action_time_authorization.sha256:
            raise RehearsalV22Error("action authorization bytes changed before archive")
        action_archive = f"{archive_prefix}/action-time-authorization.json"
        payloads[action_archive] = action_payload
        evidence_root = record.started_path.parent / "evidence"
        observed_evidence_root, evidence_files = _evidence_tree(evidence_root)
        if observed_evidence_root != record.evidence_tree_root_sha256:
            raise RehearsalV22Error("attempt evidence changed before archive")
        logical_names = {
            cast(str, row["relative_path"]): cast(str, row["logical_name"])
            for row in record.artifact_inventory
        }
        archived_inventory: list[JsonObject] = []
        for relative, payload in evidence_files:
            archive_relative = f"{archive_prefix}/evidence/{relative}"
            payloads[archive_relative] = payload
            archived_inventory.append(
                {
                    "logical_name": logical_names.get(relative, relative),
                    "relative_path": relative,
                    "relative_path_basis": "attempt_evidence_root_excluded",
                    "bytes": len(payload),
                    "sha256": _sha256(payload),
                    "durability": "ARCHIVED",
                    "archive_relative_path": archive_relative,
                    "source_and_archive_bytes_equal": True,
                }
            )
        bundle_records.append(
            {
                "ordinal": record.ordinal,
                "attempt_token_sha256": record.attempt_token_sha256,
                "previous_history_root_sha256": record.previous_history_root_sha256,
                "started": _attempt_file_evidence(
                    live_relative=started_relative,
                    archive_relative=started_archive,
                    payload=record.started_bytes,
                ),
                "candidate": candidate_evidence,
                "terminal": terminal_evidence,
                "outcome": record.outcome,
                "reached_stage": record.reached_stage,
                "implementation_epoch": record.implementation_epoch,
                "implementation_commit": record.implementation_commit,
                "owner_action_time_authorization": {
                    "authority": record.owner_action_time_authorization.as_json(),
                    "archive_relative_path": action_archive,
                    "bytes": len(action_payload),
                    "archive_sha256": _sha256(action_payload),
                    "source_and_archive_bytes_equal": True,
                },
                "command_sha256": record.command_sha256,
                "environment_sha256": record.environment_sha256,
                "automatic_retry_count": 0,
                "artifact_inventory": archived_inventory,
                "error": record.error,
                "evidence_tree_root_sha256": record.evidence_tree_root_sha256,
                "record_root_sha256": record.record_root_sha256,
            }
        )
    ordered_payloads = dict(sorted(payloads.items(), key=lambda item: item[0].encode("utf-8")))
    archive_merkle = _generic_merkle_root(ordered_payloads)
    archive_files = [
        {"path": relative, "sha256": _sha256(payload)}
        for relative, payload in ordered_payloads.items()
    ]
    selected_record = history.records[history.selected_attempt_ordinal - 1]
    return _HistoryArchive(
        summary={
            "series_id": REHEARSAL_ID,
            "series_token_sha256": binding.series_token_sha256,
            "ledger_root": binding.ledger_root.as_posix(),
            "policy": SERIES_POLICY,
            "attempt_limit": "unbounded_until_first_validated_success_or_owner_abandonment",
            "started_count": history.started_count,
            "failed_count": history.failed_count,
            "incomplete_count": history.incomplete_count,
            "validated_candidate_count": 1,
            "selected_attempt_ordinal": history.selected_attempt_ordinal,
            "series_closed": True,
            "records": bundle_records,
            "history_root_sha256": history.history_root_sha256,
            "live_ledger_root_sha256": history.live_ledger_root_sha256,
            "ordinals_contiguous": True,
            "no_gap_duplicate_or_reorder": True,
            "no_unarchived_attempt": True,
            "no_attempt_after_selected_success": True,
            "first_validated_success_is_selected": True,
        },
        archive_record={
            "archive_root": "archive/attempt-history",
            "file_count": len(archive_files),
            "files": archive_files,
            "history_merkle_root_sha256": archive_merkle,
            "every_live_started_candidate_terminal_and_action_authorization_byte_archived": True,
            "every_attempt_evidence_byte_archived": True,
        },
        payloads=ordered_payloads,
        history_root_sha256=history.history_root_sha256,
        live_ledger_root_sha256=history.live_ledger_root_sha256,
        selected_record=selected_record,
    )


def _rehydrate_sealed_pipeline_replays(
    binding: ExecutionBinding,
    history: HistoryValidation,
    authorization: BundleRecoveryAuthorization,
) -> tuple[_SealedPipelineReplay, _SealedPipelineReplay]:
    """Read already persisted run bytes without possessing a replay capability."""

    historical = _historical_selected_anchor(binding, history)
    if (
        authorization.sealed_series.get("history_root_sha256")
        != historical.history_root_sha256
        or authorization.sealed_series.get("live_ledger_root_sha256")
        != historical.live_ledger_root_sha256
    ):
        raise RehearsalV22Error("sealed-run rehydrate authority differs from history")
    selected = history.records[history.selected_attempt_ordinal - 1]  # type: ignore[operator]
    evidence_root = selected.started_path.parent / "evidence"
    observed_evidence_root, _all_evidence = _evidence_tree(evidence_root)
    if (
        observed_evidence_root != historical.evidence_tree_root_sha256
        or observed_evidence_root
        != authorization.sealed_series.get("selected_evidence_tree_root_sha256")
    ):
        raise RehearsalV22Error("sealed evidence tree drifted before rehydrate")
    replays: list[_SealedPipelineReplay] = []
    for run_label in ("run-a", "run-b"):
        artifacts: dict[str, bytes] = {}
        merkle_payloads: dict[str, bytes] = {}
        for logical_name, source_relative in ARTIFACT_INVENTORY:
            path = evidence_root.joinpath(
                *PurePosixPath(f"runs/{run_label}/root/{source_relative}").parts
            )
            payload = _regular_bytes(path, f"sealed {run_label} {logical_name}")
            artifacts[logical_name] = payload
            merkle_payloads[source_relative] = payload
        if len(artifacts) != 14:
            raise RehearsalV22Error("sealed rehydrate did not read exactly 14 artifacts")
        run_root = _generic_merkle_root(merkle_payloads)
        expected_root = authorization.sealed_series.get(
            "selected_run_a_root_sha256" if run_label == "run-a" else "selected_run_b_root_sha256"
        )
        if run_root != expected_root:
            raise RehearsalV22Error(f"sealed {run_label} root drifted")
        probe_path = evidence_root / "probes" / f"{run_label}.json"
        probe_payload = _regular_bytes(probe_path, f"sealed {run_label} probe")
        probes = _object(
            strict_json_loads(probe_payload, source=f"sealed {run_label} probe"),
            f"sealed {run_label} probe",
        )
        if _canonical_json_bytes(probes) != probe_payload:
            raise RehearsalV22Error(f"sealed {run_label} probe is not canonical")
        expected_probes = _pipeline_probe_evidence(
            run_label=run_label,
            clock=DeterministicClock(
                MONOTONIC_INITIAL_SECONDS + float(CNINFO_GAP_COUNT)
            ),
            artifacts=artifacts,
        )
        if probes != expected_probes:
            raise RehearsalV22Error(f"sealed {run_label} probe semantics drifted")
        replays.append(
            _SealedPipelineReplay(
                run_label=run_label,
                artifacts=dict(artifacts),
                probe_evidence=copy.deepcopy(probes),
            )
        )
    if dict(replays[0].artifacts) != dict(replays[1].artifacts):
        raise RehearsalV22Error("sealed run-A and run-B are not byte-identical")
    return replays[0], replays[1]


def _implementation_epochs(
    binding: ExecutionBinding,
    history: HistoryValidation,
    *,
    historical_anchor: HistoricalSelectedAnchor | None = None,
) -> list[JsonObject]:
    if not history.records:
        raise RehearsalV22Error("implementation epochs require at least one attempt")
    if historical_anchor is not None and (
        not isinstance(historical_anchor, HistoricalSelectedAnchor)
        or historical_anchor.history_root_sha256 != history.history_root_sha256
        or history.selected_attempt_ordinal is None
        or history.records[history.selected_attempt_ordinal - 1].implementation_epoch
        != historical_anchor.selected_epoch
        or history.records[history.selected_attempt_ordinal - 1].implementation_commit
        != historical_anchor.selected_commit
    ):
        raise RehearsalV22Error("implementation epoch historical anchor drifted")
    execution_head = _current_execution_head(binding.project_root)
    groups: list[list[ValidatedAttemptRecord]] = []
    for record in history.records:
        if not groups or groups[-1][0].implementation_epoch != record.implementation_epoch:
            groups.append([record])
        else:
            groups[-1].append(record)
    used_epochs = [group[0].implementation_epoch for group in groups]
    epoch_three_gap = used_epochs == [2, 4]
    if not epoch_three_gap and used_epochs not in (
        list(range(1, len(groups) + 1)),
        list(range(2, len(groups) + 2)),
    ):
        raise RehearsalV22Error("implementation epoch numbers are not contiguous")
    if epoch_three_gap and not (
        len(history.records) == 2
        and history.records[0].ordinal == 1
        and history.records[0].outcome == "FAILED"
        and history.records[0].implementation_epoch == 2
        and history.records[1].ordinal == 2
        and history.records[1].outcome == "CANDIDATE_VALIDATED_AND_SELECTED"
        and history.records[1].implementation_epoch == 4
        and history.selected_attempt_ordinal == 2
        and history.validated_candidate_count == 1
        and history.incomplete_count == 0
        and history.series_closed
    ):
        raise RehearsalV22Error("the void epoch 3 exception requires exact closed [2,4] history")
    result: list[JsonObject] = []
    if used_epochs[0] == 2:
        result.append(_void_epoch_one(binding, execution_head=execution_head))
    for records in groups:
        expected_epoch = records[0].implementation_epoch
        if epoch_three_gap and expected_epoch == 4:
            result.append(_void_epoch_three(binding, execution_head=execution_head))
        first = records[0]
        authorization = _validate_action_authorization(
            binding,
            first.owner_action_time_authorization,
            expected_ordinal=first.ordinal,
            expected_previous_history_root_sha256=first.previous_history_root_sha256,
            require_current_process=False,
        )
        if authorization.implementation_epoch != expected_epoch:
            raise RehearsalV22Error("action authorization epoch interval drifted")
        if (
            historical_anchor is not None
            and expected_epoch == historical_anchor.selected_epoch
            and authorization.implementation_commit == historical_anchor.selected_commit
        ):
            control = historical_anchor.control_surface
        else:
            control = build_control_surface(
                binding.project_root,
                authorization.implementation_commit,
                # An epoch table is historical disclosure.  Current executing bytes
                # are proved independently by LiveExecutionAnchor and must never be
                # substituted for the immutable selected Git blobs.
                require_current=False,
            )
        if control.merkle_root_sha256 != authorization.control_merkle_root_sha256:
            raise RehearsalV22Error("implementation epoch control root drifted")
        for record in records:
            observed = _validate_action_authorization(
                binding,
                record.owner_action_time_authorization,
                expected_ordinal=record.ordinal,
                expected_previous_history_root_sha256=record.previous_history_root_sha256,
                require_current_process=False,
            )
            if (
                observed.implementation_epoch != expected_epoch
                or observed.implementation_commit != authorization.implementation_commit
                or observed.owner_surface_authorization != authorization.owner_surface_authorization
                or observed.independent_implementation_review
                != authorization.independent_implementation_review
                or observed.control_merkle_root_sha256 != authorization.control_merkle_root_sha256
                or not _git_is_ancestor(
                    binding.project_root,
                    observed.creating_commit,
                    execution_head,
                )
            ):
                raise RehearsalV22Error("implementation epoch attempts disagree")
        result.append(
            {
                "epoch": expected_epoch,
                "implementation_commit": authorization.implementation_commit,
                "owner_exact_surface_authorization": (
                    authorization.owner_surface_authorization.as_json()
                ),
                "independent_implementation_review": (
                    authorization.independent_implementation_review.as_json()
                ),
                "control_merkle_root_sha256": authorization.control_merkle_root_sha256,
                "first_attempt_ordinal": records[0].ordinal,
                "last_attempt_ordinal": records[-1].ordinal,
                "all_attempts_authorized": True,
            }
        )
    return result


def _void_epoch_three(
    binding: ExecutionBinding,
    *,
    execution_head: str,
) -> JsonObject:
    """Return the one exact schema-compatible sentinel for superseded epoch 3."""

    root = binding.project_root.absolute()
    head = _git_commit(root, execution_head, "void epoch 3 execution head")
    owner = AuthorityReference(
        VOID_EPOCH_THREE_OWNER_RELATIVE.as_posix(),
        VOID_EPOCH_THREE_OWNER_SHA256,
        VOID_EPOCH_THREE_IMPLEMENTATION_PARENT,
    )
    control = build_control_surface(
        root,
        VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT,
        require_current=False,
    )
    if (
        control.merkle_root_sha256 != VOID_EPOCH_THREE_CONTROL_ROOT_SHA256
        or len(control.records) != VOID_EPOCH_THREE_CONTROL_RECORD_COUNT
    ):
        raise RehearsalV22Error("void epoch 3 immutable control surface drifted")
    _later_epoch_surface(
        root,
        epoch=3,
        implementation_commit=VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT,
        owner_surface_authorization=owner,
        execution_head=head,
        require_current=False,
    )
    review_parents = (
        _git_bytes(
            root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            VOID_EPOCH_THREE_REVIEW_COMMIT,
            "--",
        )
        .decode("ascii", errors="strict")
        .strip()
        .split()
    )
    review_surface = _parse_name_status(
        _git_bytes(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT,
            VOID_EPOCH_THREE_REVIEW_COMMIT,
            "--",
        )
    )
    review_payload = _git_blob(
        root,
        VOID_EPOCH_THREE_REVIEW_COMMIT,
        VOID_EPOCH_THREE_REVIEW_RELATIVE.as_posix(),
    )
    review_document = _object(
        strict_json_loads(review_payload, source="void epoch 3 real review"),
        "void epoch 3 real review",
    )
    if (
        review_parents
        != [VOID_EPOCH_THREE_REVIEW_COMMIT, VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT]
        or review_surface != {VOID_EPOCH_THREE_REVIEW_RELATIVE.as_posix(): "A"}
        or _sha256(review_payload) != VOID_EPOCH_THREE_REVIEW_SHA256
        or review_document.get("verdict") != "APPROVE_EPOCH3_IMPLEMENTATION"
        or review_document.get("blockers") != []
        or review_document.get("reviewed_commit")
        != VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT
    ):
        raise RehearsalV22Error("void epoch 3 real review lineage drifted")
    parents = (
        _git_bytes(
            root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT,
            "--",
        )
        .decode("ascii", errors="strict")
        .strip()
        .split()
    )
    if parents != [
        VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT,
        VOID_EPOCH_THREE_IMPLEMENTATION_PARENT,
    ]:
        raise RehearsalV22Error("void epoch 3 implementation parent drifted")
    observed_surface = _parse_name_status(
        _git_bytes(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            VOID_EPOCH_THREE_IMPLEMENTATION_PARENT,
            VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT,
            "--",
        )
    )
    if observed_surface != dict(VOID_EPOCH_THREE_SURFACE):
        raise RehearsalV22Error("void epoch 3 implementation surface drifted")
    landing = _git_commit(root, VOID_EPOCH_THREE_LANDING_COMMIT, "void epoch 3 landing")
    landing_parents = (
        _git_bytes(root, "rev-list", "--parents", "-n", "1", landing, "--")
        .decode("ascii", errors="strict")
        .strip()
        .split()
    )
    if landing_parents != [
        landing,
        VOID_EPOCH_THREE_LANDING_PARENT,
        VOID_EPOCH_THREE_REVIEW_COMMIT,
    ] or not _git_is_ancestor(root, landing, head):
        raise RehearsalV22Error("void epoch 3 landing topology drifted")

    gate_ruling = AuthorityReference(
        VOID_EPOCH_THREE_GATE_RULING_RELATIVE.as_posix(),
        VOID_EPOCH_THREE_GATE_RULING_SHA256,
        VOID_EPOCH_THREE_GATE_RULING_COMMIT,
    )
    gate_payload = validate_unique_a_authority(
        root,
        gate_ruling,
        execution_head=head,
        require_current=False,
    )
    gate_document = _object(
        strict_json_loads(gate_payload, source="void epoch 3 gate ruling"),
        "void epoch 3 gate ruling",
    )
    gate_rulings = _object(gate_document.get("part_3_rulings"), "void epoch 3 rulings")
    supersession = gate_rulings.get("supersession_authorized")
    if not (
        isinstance(supersession, str)
        and "d6c9c353" in supersession
        and "SAME exact four-M surface" in supersession
        and "epoch is unconsumed" in supersession
    ):
        raise RehearsalV22Error("void epoch 3 gate ruling semantics drifted")

    reanchor = AuthorityReference(
        VOID_EPOCH_THREE_REANCHOR_RELATIVE.as_posix(),
        VOID_EPOCH_THREE_REANCHOR_SHA256,
        VOID_EPOCH_THREE_REANCHOR_COMMIT,
    )
    reanchor_payload = validate_unique_a_authority(
        root,
        reanchor,
        execution_head=head,
        require_current=False,
    )
    reanchor_document = _object(
        strict_json_loads(reanchor_payload, source="void epoch 3 re-anchor ruling"),
        "void epoch 3 re-anchor ruling",
    )
    necessity = _object(
        reanchor_document.get("part_1_why_epoch_4_is_necessary"),
        "void epoch 3 re-anchor necessity",
    )
    mechanism = necessity.get("mechanism")
    if not (
        isinstance(mechanism, str)
        and "d4fb0d8c" in mechanism
        and "config.py" in mechanism
        and "require_current" in mechanism
    ):
        raise RehearsalV22Error("void epoch 3 structural supersession semantics drifted")

    reason = AuthorityReference(
        VOID_EPOCH_THREE_REASON_RELATIVE.as_posix(),
        VOID_EPOCH_THREE_REASON_SHA256,
        VOID_EPOCH_THREE_REASON_COMMIT,
    )
    reason_payload = validate_unique_a_authority(
        root,
        reason,
        execution_head=head,
        require_current=False,
    )
    reason_document = _object(
        strict_json_loads(reason_payload, source="void epoch 3 reason discriminator"),
        "void epoch 3 reason discriminator",
    )
    requirements = _object(
        reason_document.get("part_5_epoch5_design_requirements"),
        "void epoch 3 design requirements",
    )
    required_content = _array(
        requirements.get("required_content"),
        "void epoch 3 required content",
    )
    if not (
        reason_document.get("part_1_attempt_2_adjudicated", {}).get("verdict")
        == "VALID_SERIES_CLOSING_SUCCESS_WITH_FAILED_BUNDLE_CONSTRUCTION"
        and len(required_content) == 4
        and isinstance(required_content[0], str)
        and "void-epoch-3" in required_content[0]
        and "d6c9c353" in required_content[0]
        and "d4fb0d8c" in required_content[0]
        and "bf9f610" in required_content[0]
        and "0069270" in required_content[0]
    ):
        raise RehearsalV22Error("void epoch 3 reason discriminator semantics drifted")
    return {
        "epoch": 3,
        "implementation_commit": VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT,
        "owner_exact_surface_authorization": owner.as_json(),
        "independent_implementation_review": reason.as_json(),
        "control_merkle_root_sha256": VOID_EPOCH_THREE_CONTROL_ROOT_SHA256,
        "first_attempt_ordinal": 2,
        "last_attempt_ordinal": 2,
        "all_attempts_authorized": True,
    }


def _void_epoch_one(
    binding: ExecutionBinding,
    *,
    execution_head: str,
) -> JsonObject:
    root = binding.project_root.absolute()
    head = _git_commit(root, execution_head, "void epoch execution head")
    implementation_commit = _git_commit(
        root,
        VOID_EPOCH_ONE_IMPLEMENTATION_COMMIT,
        "void epoch implementation commit",
    )
    parents = (
        _git_bytes(
            root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            implementation_commit,
            "--",
        )
        .decode("ascii", errors="strict")
        .strip()
        .split()
    )
    if parents != [implementation_commit, VOID_EPOCH_ONE_IMPLEMENTATION_PARENT]:
        raise RehearsalV22Error("void epoch implementation parent drifted")
    observed_surface = _parse_name_status(
        _git_bytes(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            VOID_EPOCH_ONE_IMPLEMENTATION_PARENT,
            implementation_commit,
            "--",
        )
    )
    if observed_surface != {
        IMPLEMENTATION_RELATIVE.as_posix(): "M",
        RUNNER_TEST_RELATIVE.as_posix(): "M",
    }:
        raise RehearsalV22Error("void epoch implementation surface drifted")
    owner = AuthorityReference(
        INITIAL_SURFACE_REVIEW_RELATIVE.as_posix(),
        INITIAL_SURFACE_REVIEW_SHA256,
        INITIAL_SURFACE_REVIEW_COMMIT,
    )
    validate_unique_a_authority(
        root,
        owner,
        execution_head=head,
        allow_initial_sibling=True,
        require_current=False,
    )
    adjudication = AuthorityReference(
        VOID_EPOCH_ONE_ADJUDICATION_RELATIVE.as_posix(),
        VOID_EPOCH_ONE_ADJUDICATION_SHA256,
        VOID_EPOCH_ONE_ADJUDICATION_COMMIT,
    )
    adjudication_payload = validate_unique_a_authority(
        root,
        adjudication,
        execution_head=head,
        require_current=False,
    )
    adjudication_document = _object(
        strict_json_loads(adjudication_payload, source="void epoch adjudication"),
        "void epoch adjudication",
    )
    correction = _object(
        adjudication_document.get("part_2_epoch_numbering_correction"),
        "void epoch numbering correction",
    )
    if correction.get("ruling") != list(VOID_EPOCH_ONE_RULING):
        raise RehearsalV22Error("void epoch numbering correction drifted")
    review = AuthorityReference(
        VOID_EPOCH_ONE_REVIEW_RELATIVE.as_posix(),
        VOID_EPOCH_ONE_REVIEW_SHA256,
        VOID_EPOCH_ONE_REVIEW_COMMIT,
    )
    review_payload = validate_unique_a_authority(
        root,
        review,
        execution_head=head,
        require_current=False,
    )
    review_document = _object(
        strict_json_loads(review_payload, source="void epoch implementation review"),
        "void epoch implementation review",
    )
    if (
        review_document.get("verdict") != "APPROVE_REMEDIATION_AND_IMPLEMENTATION"
        or review_document.get("blockers") != []
        or review_document.get("reviewed_commit") != implementation_commit
    ):
        raise RehearsalV22Error("void epoch implementation review drifted")
    landing = _git_commit(
        root,
        VOID_EPOCH_ONE_LANDING_COMMIT,
        "void epoch landing commit",
    )
    landing_parents = (
        _git_bytes(root, "rev-list", "--parents", "-n", "1", landing, "--")
        .decode("ascii", errors="strict")
        .strip()
        .split()
    )
    if landing_parents != [landing, VOID_EPOCH_ONE_REVIEW_COMMIT, implementation_commit]:
        raise RehearsalV22Error("void epoch merge-only landing topology drifted")
    if not _git_is_ancestor(root, landing, head):
        raise RehearsalV22Error("void epoch landing is outside execution lineage")
    control = build_control_surface(root, implementation_commit, require_current=False)
    return {
        "epoch": 1,
        "implementation_commit": implementation_commit,
        "owner_exact_surface_authorization": owner.as_json(),
        # A frozen closed schema has no status/reason field.  This independently
        # issued correction reference is the schema-compatible void discriminator
        # and carries the full structural-unconsumability reason.
        "independent_implementation_review": adjudication.as_json(),
        "control_merkle_root_sha256": control.merkle_root_sha256,
        # The closed schema requires positive bounds.  The validator treats this
        # exact signed row as a zero-attempt sentinel and requires epoch 2 to own
        # ordinal 1; no ledger record may claim epoch 1.
        "first_attempt_ordinal": 1,
        "last_attempt_ordinal": 1,
        "all_attempts_authorized": True,
    }


def _execution_binding_document(binding: ExecutionBinding) -> JsonObject:
    result: JsonObject = {
        "mode": binding.mode,
        "project_root": binding.project_root.as_posix(),
        "absolute_destination": binding.destination.as_posix(),
        "series_token_sha256": binding.series_token_sha256,
        "ledger_root": binding.ledger_root.as_posix(),
        "derivation_recomputed": True,
        "private_rebase_capability_validated": (binding.mode == "DISPOSABLE_FULL_SHAPE_TEST"),
    }
    if binding.mode == "REGISTERED_OFFICIAL":
        result["registered_rehearsal_paths_created_as_expected"] = True
    else:
        result["real_registered_paths_untouched"] = True
    return result


def _harness_identity(
    binding: ExecutionBinding,
    *,
    historical_anchor: HistoricalSelectedAnchor,
) -> JsonObject:
    if not isinstance(historical_anchor, HistoricalSelectedAnchor):
        raise RehearsalV22Error("harness identity requires a historical selected anchor")
    files = {
        "thin_main_shim": SHIM_RELATIVE,
        "implementation_module": IMPLEMENTATION_RELATIVE,
        "validator_module": VALIDATOR_RELATIVE,
    }
    result: JsonObject = {}
    for key, relative in files.items():
        payload = validate_implementation_blob(
            binding.project_root,
            historical_anchor.selected_commit,
            relative.as_posix(),
            require_current=False,
        )
        if historical_anchor.selected_git_blob_sha256.get(relative.as_posix()) != _sha256(
            payload
        ):
            raise RehearsalV22Error("historical harness identity blob map drifted")
        result[key] = {"path": relative.as_posix(), "sha256": _sha256(payload)}
    result.update(
        {
            "implementation_module_name": MODULE_NAME,
            "validator_module_name": ("scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"),
            "authority_owner_module": MODULE_NAME,
            "shim_has_authority_state": False,
            "validator_import_target": MODULE_NAME,
            "module_object_identity_equal": True,
            "exact_os_bootstrap_passed": True,
            "implementation_direct_execution_rejected": True,
            "second_authority_module_rejected": True,
            "delegation_binding_passed": ("identity_root_creator_owner_and_lifetime_exact"),
        }
    )
    return result


def _build_bundle(
    *,
    binding: ExecutionBinding,
    history: HistoryValidation,
    run_a: PipelineReplay | _SealedPipelineReplay,
    run_b: PipelineReplay | _SealedPipelineReplay,
    historical_anchor: HistoricalSelectedAnchor,
    live_anchor: LiveExecutionAnchor,
) -> _BundleAssembly:
    if not isinstance(historical_anchor, HistoricalSelectedAnchor) or not isinstance(
        live_anchor,
        LiveExecutionAnchor,
    ):
        raise RehearsalV22Error("bundle construction requires both typed byte anchors")
    sealed_inputs = (
        isinstance(run_a, _SealedPipelineReplay),
        isinstance(run_b, _SealedPipelineReplay),
    )
    if sealed_inputs[0] != sealed_inputs[1]:
        raise RehearsalV22Error("bundle construction mixed active and sealed run values")
    historical_source_commit = (
        historical_anchor.selected_commit if all(sealed_inputs) else None
    )
    control = historical_anchor.control_surface
    if (
        run_a.run_label != "run-a"
        or run_b.run_label != "run-b"
        or dict(run_a.artifacts) != dict(run_b.artifacts)
        or len(run_a.artifacts) != 14
    ):
        raise RehearsalV22Error("selected runs are not 14/14 byte-identical")
    history_archive = _history_archive(binding, history)
    selected = history_archive.selected_record
    if (
        selected.implementation_commit != historical_anchor.selected_commit
        or selected.implementation_epoch != historical_anchor.selected_epoch
        or selected.implementation_commit != control.implementation_commit
        or history.history_root_sha256 != historical_anchor.history_root_sha256
        or history.live_ledger_root_sha256 != historical_anchor.live_ledger_root_sha256
    ):
        raise RehearsalV22Error("selected history and control implementation disagree")
    observed_live = build_control_surface(
        binding.project_root,
        live_anchor.implementation_commit,
        require_current=True,
    )
    if (
        observed_live != live_anchor.control_surface
        or live_anchor.execution_head != _current_execution_head(binding.project_root)
    ):
        raise RehearsalV22Error("live execution anchor drifted before bundle construction")
    run_a_record, run_a_payloads, run_a_root = _run_archive(run_a)
    run_b_record, run_b_payloads, run_b_root = _run_archive(run_b)
    control_record, control_payloads = _control_archive(control)
    if selected.candidate_bytes is None or selected.terminal_bytes is None:
        raise RehearsalV22Error("selected attempt lacks candidate or terminal bytes")
    candidate = _object(
        strict_json_loads(selected.candidate_bytes, source="selected candidate"),
        "selected candidate",
    )
    if (
        candidate.get("run_a_root_sha256") != run_a_root
        or candidate.get("run_b_root_sha256") != run_b_root
        or candidate.get("control_surface_root_sha256") != control.merkle_root_sha256
        or candidate.get("evidence_tree_root_sha256") != selected.evidence_tree_root_sha256
    ):
        raise RehearsalV22Error("selected candidate roots differ from bundle archives")
    bundle_root = _bundle_root_sha256(
        attempt_history_root_sha256=history_archive.history_root_sha256,
        run_a_root_sha256=run_a_root,
        run_b_root_sha256=run_b_root,
        control_surface_root_sha256=control.merkle_root_sha256,
    )
    schema = _bundle_schema(
        binding.project_root,
        historical_selected_commit=historical_source_commit,
    )
    merkle = _object(
        _schema_const_template(
            schema,
            _schema_definition(schema, "merkle"),
            omit=frozenset(
                {
                    "run_a_root_sha256",
                    "run_b_root_sha256",
                    "control_surface_root_sha256",
                    "bundle_root_sha256",
                    "attempt_history_root_sha256",
                    "live_ledger_root_sha256",
                }
            ),
        ),
        "bundle Merkle constant section",
    )
    merkle.update(
        {
            "run_a_root_sha256": run_a_root,
            "run_b_root_sha256": run_b_root,
            "control_surface_root_sha256": control.merkle_root_sha256,
            "bundle_root_sha256": bundle_root,
            "attempt_history_root_sha256": history_archive.history_root_sha256,
            "live_ledger_root_sha256": history_archive.live_ledger_root_sha256,
        }
    )
    document: JsonObject = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "rehearsal_id": REHEARSAL_ID,
        "status": "PASS_REHEARSAL_V2_2_AWAITING_OWNER_REVIEW",
        "lineage": _bundle_lineage(
            binding.project_root,
            implementation_commit=selected.implementation_commit,
            historical_selected_commit=historical_source_commit,
        ),
        "publication": _constant_section(schema, "publication"),
        "execution_binding": _execution_binding_document(binding),
        "rehearsal_attempt_policy": _constant_section(
            schema,
            "rehearsalAttemptPolicy",
        ),
        "harness_identity": _harness_identity(
            binding,
            historical_anchor=historical_anchor,
        ),
        "implementation_epochs": _implementation_epochs(
            binding,
            history,
            historical_anchor=historical_anchor,
        ),
        "attempt_history": history_archive.summary,
        "determinism": _constant_section(schema, "determinism"),
        "real_entry_gate_validation": _constant_section(
            schema,
            "realEntryGateValidation",
        ),
        "request_interval_validation": _constant_section(
            schema,
            "requestIntervalValidation",
        ),
        "archive": {
            "runs": [run_a_record, run_b_record],
            "control_surface": control_record,
            "attempt_history": history_archive.archive_record,
        },
        "execution_environment": _constant_section(schema, "executionEnvironment"),
        "merkle": merkle,
        "semantic_validation": _constant_section(schema, "semanticValidation"),
        "safety": _constant_section(schema, "safety"),
        "evaluation_one_shot": _constant_section(schema, "evaluationOneShot"),
        "locks": _constant_section(schema, "locks"),
        "remaining_blockers": _constant_section(schema, "remainingBlockers"),
    }
    payloads = {
        **run_a_payloads,
        **run_b_payloads,
        **control_payloads,
        **history_archive.payloads,
    }
    if len(payloads) != (
        len(run_a_payloads)
        + len(run_b_payloads)
        + len(control_payloads)
        + len(history_archive.payloads)
    ):
        raise RehearsalV22Error("bundle archive payload paths collide")
    bundle_payload = _canonical_json_bytes(document)
    authority = _TEMP_AUTHORITY.get()
    if authority is not None and authority.as_posix().encode("utf-8") in bundle_payload:
        raise RehearsalV22Error("temporary authority leaked into bundle bytes")
    return _BundleAssembly(
        document=document,
        payloads=dict(sorted(payloads.items(), key=lambda item: item[0].encode("utf-8"))),
        bundle_payload=bundle_payload,
        bundle_root_sha256=bundle_root,
    )


def _ensure_private_directory(root: Path, relative_parent: PurePosixPath) -> Path:
    cursor = root
    for part in relative_parent.parts:
        cursor /= part
        if os.path.lexists(cursor):
            metadata = cursor.lstat()
            if (
                cursor.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o700
                or cursor.resolve(strict=True) != cursor.absolute()
            ):
                raise RehearsalV22Error("bundle staging directory drifted")
            continue
        os.mkdir(cursor, 0o700)
        _fsync_directory(cursor.parent)
    return cursor


def _stage_bundle(
    *,
    binding: ExecutionBinding,
    assembly: _BundleAssembly,
) -> Path:
    authority = _TEMP_AUTHORITY.get()
    if authority is None:
        raise RehearsalV22Error("bundle staging lacks temporary authority")
    candidate = Path(tempfile.mkdtemp(prefix="v2-2-bundle-candidate-", dir=authority)).resolve(
        strict=True
    )
    if candidate.parent != authority or candidate == binding.destination:
        raise RehearsalV22Error("bundle candidate escaped temporary authority")
    for relative, payload in assembly.payloads.items():
        normalized = _relative_text(relative, "bundle archive path")
        parent = _ensure_private_directory(candidate, PurePosixPath(normalized).parent)
        _write_exclusive(parent / PurePosixPath(normalized).name, payload, mode=0o600)
    _write_exclusive(candidate / BUNDLE_FILENAME, assembly.bundle_payload, mode=0o600)
    for directory in sorted(
        (path for path in candidate.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        _fsync_directory(directory)
    _fsync_directory(candidate)
    if os.path.lexists(binding.destination):
        raise RehearsalV22Error("bundle destination appeared during candidate staging")
    if candidate.stat().st_dev != binding.destination.parent.stat().st_dev:
        raise RehearsalV22Error("bundle candidate and destination are on different devices")
    return candidate


def _rename_directory_exclusive(source: Path, destination: Path) -> JsonObject:
    """Use Darwin's single-kernel-call no-replace directory rename."""

    if platform.system() != "Darwin":
        raise RehearsalV22Error("v2.2 atomic publication requires Darwin renamex_np")
    source_absolute = source.absolute()
    destination_absolute = destination.absolute()
    source_metadata = source_absolute.lstat()
    parent_metadata = destination_absolute.parent.lstat()
    if (
        source_absolute.is_symlink()
        or not stat.S_ISDIR(source_metadata.st_mode)
        or source_absolute.resolve(strict=True) != source_absolute
        or destination_absolute.parent.is_symlink()
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or destination_absolute.parent.resolve(strict=True)
        != destination_absolute.parent.absolute()
        or source_metadata.st_dev != parent_metadata.st_dev
    ):
        raise RehearsalV22Error("exclusive rename source, parent, or device drifted")
    source_before = {
        "device": source_metadata.st_dev,
        "inode": source_metadata.st_ino,
        "tree": _tree_fingerprint(source_absolute),
    }
    destination_before = _tree_fingerprint(destination_absolute)
    libc = ctypes.CDLL(None, use_errno=True)
    renamex_np = libc.renamex_np
    renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    renamex_np.restype = ctypes.c_int
    ctypes.set_errno(0)
    return_code = int(
        renamex_np(
            os.fsencode(source_absolute),
            os.fsencode(destination_absolute),
            ctypes.c_uint(0x00000004),
        )
    )
    observed_errno = ctypes.get_errno()
    if return_code != 0:
        source_after = source_absolute.lstat()
        destination_after = _tree_fingerprint(destination_absolute)
        if (
            observed_errno != errno.EEXIST
            or source_after.st_dev != source_metadata.st_dev
            or source_after.st_ino != source_metadata.st_ino
            or _tree_fingerprint(source_absolute) != source_before["tree"]
            or (destination_before != {".": "absent"} and destination_after != destination_before)
        ):
            raise RehearsalV22Error(
                "exclusive rename failed without preserving source and destination"
            )
        raise RehearsalV22Error(
            "exclusive rename rejected an existing or racing destination with EEXIST"
        )
    if observed_errno != 0 or destination_before != {".": "absent"}:
        raise RehearsalV22Error("exclusive rename succeeded from an invalid initial state")
    _fsync_directory(destination_absolute.parent)
    destination_metadata = destination_absolute.lstat()
    if (
        os.path.lexists(source_absolute)
        or destination_absolute.is_symlink()
        or not stat.S_ISDIR(destination_metadata.st_mode)
        or destination_absolute.resolve(strict=True) != destination_absolute
        or destination_metadata.st_dev != source_metadata.st_dev
        or destination_metadata.st_ino != source_metadata.st_ino
    ):
        raise RehearsalV22Error("exclusive rename result identity drifted")
    return {
        "syscall": "renamex_np",
        "flag": "RENAME_EXCL",
        "flag_value": 4,
        "return_code": 0,
        "errno": 0,
        "single_kernel_no_replace_rename": True,
        "source_device": source_metadata.st_dev,
        "source_inode": source_metadata.st_ino,
        "destination_device": destination_metadata.st_dev,
        "destination_inode": destination_metadata.st_ino,
        "source_absent_after": True,
        "destination_preserved_source_identity": True,
        "destination_parent_fsync_completed": True,
    }


def _publish_candidate(binding: ExecutionBinding, candidate: Path) -> JsonObject:
    authority = _TEMP_AUTHORITY.get()
    if (
        authority is None
        or candidate.parent != authority
        or candidate == binding.destination
        or not candidate.is_relative_to(authority)
    ):
        raise RehearsalV22Error("bundle candidate lacks active temp-only authority")
    evidence = _rename_directory_exclusive(candidate, binding.destination)
    if (
        binding.destination.is_symlink()
        or not binding.destination.is_dir()
        or binding.destination.resolve(strict=True) != binding.destination.absolute()
    ):
        raise RehearsalV22Error("bundle atomic publication did not close exactly once")
    return evidence


def _release_schema(project_root: Path) -> JsonObject:
    payload = _regular_bytes(
        project_root / RELEASE_SCHEMA_RELATIVE,
        "v2.2 release schema",
    )
    if _sha256(payload) != RELEASE_SCHEMA_SHA256:
        raise RehearsalV22Error("v2.2 release schema bytes drifted")
    return _object(
        strict_json_loads(payload, source="v2.2 release schema"),
        "v2.2 release schema",
    )


def _synthetic_git_commit_exact_path(
    binding: ExecutionBinding,
    *,
    relative: Path,
    expected_status: Literal["A", "M"],
    message: str,
) -> str:
    if binding.mode != "DISPOSABLE_FULL_SHAPE_TEST":
        raise RehearsalV22Error("synthetic Git commit is forbidden for official mode")
    policy = _AUDIT_POLICY.get()
    normalized = _relative_text(relative.as_posix(), "synthetic Git commit path")
    absolute = binding.project_root / normalized
    if (
        policy is None
        or not _audit_policy_is_issued(policy)
        or policy.project_root != binding.project_root
        or policy.subprocess_mode != "synthetic-git"
        or absolute.is_symlink()
        or not absolute.is_file()
    ):
        raise RehearsalV22Error(
            "synthetic Git commit lacks issued disposable authority"
        )
    before = _current_execution_head(binding.project_root)
    staged_before = _parse_name_status(
        _git_bytes(
            binding.project_root,
            "diff",
            "--cached",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            "--",
        )
    )
    if staged_before:
        raise RehearsalV22Error("synthetic Git index was dirty before exact commit")
    added = _git_completed(
        binding.project_root,
        "add",
        "--",
        normalized,
        synthetic_identity=True,
    )
    if added.returncode != 0:
        raise RehearsalV22Error("synthetic Git add failed")
    staged = _parse_name_status(
        _git_bytes(
            binding.project_root,
            "diff",
            "--cached",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            "--",
        )
    )
    if staged != {normalized: expected_status}:
        raise RehearsalV22Error("synthetic Git staged surface is not exact")
    committed = _git_completed(
        binding.project_root,
        "commit",
        "--no-gpg-sign",
        "--no-verify",
        "-m",
        message,
        synthetic_identity=True,
    )
    if committed.returncode != 0:
        raise RehearsalV22Error("synthetic Git commit failed")
    observed = _current_execution_head(binding.project_root)
    parents = (
        _git_bytes(
            binding.project_root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            observed,
            "--",
        )
        .decode("ascii", errors="strict")
        .strip()
        .split()
    )
    surface = _parse_name_status(
        _git_bytes(
            binding.project_root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            before,
            observed,
            "--",
        )
    )
    if parents != [observed, before] or surface != {normalized: expected_status}:
        raise RehearsalV22Error("synthetic Git commit topology or surface drifted")
    if _git_blob(binding.project_root, observed, normalized) != _regular_bytes(
        absolute,
        "synthetic committed byte",
    ):
        raise RehearsalV22Error("synthetic Git commit blob differs from current byte")
    return observed


def _synthetic_path_touch_history(
    binding: ExecutionBinding,
    relative: Path,
) -> tuple[tuple[str, str], ...]:
    """Return one synthetic path's exact first-parent touches oldest first."""

    normalized = _relative_text(relative.as_posix(), "synthetic history path")
    history = _git_bytes(
        binding.project_root,
        "log",
        "--first-parent",
        "--diff-merges=first-parent",
        "--format=@@%H",
        "--name-status",
        "--no-renames",
        "HEAD",
        "--",
        normalized,
    ).decode("utf-8", errors="strict")
    touches: list[tuple[str, str]] = []
    active: str | None = None
    for raw_line in history.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("@@"):
            active = _git_commit(
                binding.project_root,
                line[2:],
                "synthetic path touch commit",
            )
            continue
        fields = tuple(line.split("\t"))
        if active is None or len(fields) != 2 or fields[1] != normalized:
            raise RehearsalV22Error("synthetic path touch history is malformed")
        touches.append((active, fields[0]))
    return tuple(reversed(touches))


def _release_review_request_payload(
    *,
    binding: ExecutionBinding,
    bundle_sha256: str,
    bundle_commit: str,
) -> bytes:
    return _canonical_json_bytes(
        {
            "schema_version": (
                "p4.2a-v2-2-disposable-release-probe-review-request-v1"
            ),
            "status": "DISPOSABLE_FULL_SHAPE_TEST_ONLY",
            "mode": binding.mode,
            "bundle": {
                "path": f"{DESTINATION_RELATIVE.as_posix()}/{BUNDLE_FILENAME}",
                "sha256": bundle_sha256,
                "creating_commit": bundle_commit,
            },
            "review_scope": (
                "synthetic_rehearsal_evidence_and_complete_history_only"
            ),
            "does_not_authorize_real_materialization_inference_or_evaluation": True,
            "registered_paths_untouched": True,
        }
    )


def _release_receipt_document(
    *,
    binding: ExecutionBinding,
    bundle: JsonObject,
    bundle_sha256: str,
    review_request: AuthorityReference,
    reviewed_head: str,
) -> JsonObject:
    schema = _release_schema(binding.project_root)
    dynamic = frozenset(
        {
            "created_at_utc",
            "created_at_shanghai",
            "reviewed_repository_head",
            "reviewer",
            "owner_authorization",
            "lineage",
            "execution_binding",
            "series_identity",
            "attempt_history_acceptance",
            "implementation_epochs",
        }
    )
    receipt = _object(
        _schema_const_template(schema, schema, omit=dynamic),
        "release constant template",
    )
    history = _object(bundle.get("attempt_history"), "release source history")
    records = [
        _object(value, "release source attempt")
        for value in _array(history.get("records"), "release source attempts")
    ]
    selected_ordinal = cast(int, history["selected_attempt_ordinal"])
    failed_count = sum(record["outcome"] == "FAILED" for record in records)
    incomplete_count = sum(
        record["outcome"] == "INCOMPLETE_UNTERMINALIZED"
        for record in records
    )
    outcomes = [
        {
            "ordinal": record["ordinal"],
            "outcome": record["outcome"],
            "implementation_epoch": record["implementation_epoch"],
            "record_root_sha256": record["record_root_sha256"],
        }
        for record in records
    ]
    bundle_epochs = [
        _object(value, "release source epoch")
        for value in _array(
            bundle.get("implementation_epochs"),
            "release source epochs",
        )
    ]
    selected_record = records[selected_ordinal - 1]
    selected_epoch_number = cast(int, selected_record["implementation_epoch"])
    selected_epoch = next(
        epoch
        for epoch in bundle_epochs
        if epoch.get("epoch") == selected_epoch_number
    )
    bundle_lineage = _object(bundle.get("lineage"), "release source lineage")
    bundle_merkle = _object(bundle.get("merkle"), "release source Merkle")
    receipt.update(
        {
            "created_at_utc": FIXED_WALL_CLOCK_TEXT,
            "created_at_shanghai": "2026-08-10T20:30:00+08:00",
            "reviewed_repository_head": reviewed_head,
            "reviewer": {
                "identity": "synthetic_disposable_full_shape_test_reviewer",
                "reviewer_type": "ai",
                "model": None,
                "method": (
                    "deterministic synthetic receipt construction followed by the "
                    "same active release validator; not a real owner approval"
                ),
                "independent_of_operator": True,
            },
            "owner_authorization": {
                "owner": "ouyang",
                "approved": True,
                "approval_scope": (
                    "rehearsal_evidence_and_complete_attempt_history_only_"
                    "not_real_stage_release"
                ),
                "accepts_disclosed_repeatability": True,
                "acknowledged_attempt_count": len(records),
                "acknowledged_failed_count": failed_count,
                "acknowledged_incomplete_count": incomplete_count,
                "acknowledged_outcomes": outcomes,
                "selected_attempt_ordinal": selected_ordinal,
                "attempt_history_root_sha256": history["history_root_sha256"],
                "all_attempt_outcomes_reviewed": True,
                "no_hidden_or_omitted_attempt_accepted": True,
                "acknowledged_outcomes_are_contiguous_and_ordered": True,
            },
            "lineage": {
                "preregistration": bundle_lineage["preregistration"],
                "bundle_schema": bundle_lineage["bundle_schema"],
                "release_schema": bundle_lineage["release_authorization_schema"],
                "bundle": {
                    "path": f"{DESTINATION_RELATIVE.as_posix()}/{BUNDLE_FILENAME}",
                    "sha256": bundle_sha256,
                },
                "bundle_root_sha256": bundle_merkle["bundle_root_sha256"],
                "attempt_history_root_sha256": history["history_root_sha256"],
                "live_ledger_root_sha256": history["live_ledger_root_sha256"],
                "preregistration_commit": bundle_lineage["preregistration_commit"],
                "selected_implementation_commit": selected_epoch[
                    "implementation_commit"
                ],
                "rehearsal_evidence_commit": reviewed_head,
                "v2_1_incident": bundle_lineage[
                    "v2_1_consumed_attempt_incident"
                ],
                "remediation_request": bundle_lineage[
                    "v2_2_remediation_request"
                ],
                "v2_2_scope_authorization": bundle_lineage[
                    "v2_2_preregistration_scope_authorization"
                ],
                "review_request": review_request.as_json(),
            },
            "execution_binding": copy.deepcopy(bundle["execution_binding"]),
            "series_identity": {
                "series_id": REHEARSAL_ID,
                "policy": SERIES_POLICY,
                "series_token_sha256": history["series_token_sha256"],
                "ledger_root": history["ledger_root"],
                "series_closed": True,
            },
            "attempt_history_acceptance": {
                "policy": SERIES_POLICY,
                "series_closed": True,
                "attempt_count": len(records),
                "failed_count": failed_count,
                "incomplete_count": incomplete_count,
                "selected_attempt_ordinal": selected_ordinal,
                "validated_candidate_count": 1,
                "first_validated_success_is_selected": True,
                "no_attempt_after_selected_success": True,
                "ordinals_contiguous": True,
                (
                    "all_started_candidate_terminal_action_authorization_"
                    "and_actual_evidence_bytes_archived"
                ): True,
                "all_failure_and_incomplete_disclosures_archived": True,
                "history_merkle_recomputed": True,
                "live_ledger_matches_bundle_history": True,
                "history_unchanged_after_bundle_publication": True,
                "counts_equal_recomputed_records": True,
                (
                    "owner_acknowledged_outcomes_equal_ordered_bundle_records"
                ): True,
                "selected_ordinal_is_the_unique_validated_candidate": True,
                "selected_ordinal_and_epoch_match_lineage": True,
                "history_and_live_roots_match_lineage_and_bundle": True,
            },
            "implementation_epochs": [
                {
                    "epoch": epoch["epoch"],
                    "implementation_commit": epoch["implementation_commit"],
                    "owner_surface_authorization": epoch[
                        "owner_exact_surface_authorization"
                    ],
                    "independent_implementation_review": epoch[
                        "independent_implementation_review"
                    ],
                    "control_merkle_root_sha256": epoch[
                        "control_merkle_root_sha256"
                    ],
                    "first_attempt_ordinal": epoch["first_attempt_ordinal"],
                    "last_attempt_ordinal": epoch["last_attempt_ordinal"],
                }
                for epoch in bundle_epochs
            ],
        }
    )
    return receipt


def _rewrite_existing_regular(path: Path, payload: bytes) -> None:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise RehearsalV22Error("synthetic release receipt rewrite target drifted")
    descriptor = os.open(path, os.O_WRONLY | os.O_TRUNC)
    try:
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _validator_rejection_evidence(
    name: str,
    operation: Callable[[], object],
) -> JsonObject:
    try:
        operation()
    except Exception as exc:
        return {
            "name": name,
            "result": "PASS_REJECTED",
            "exception_type": type(exc).__name__,
            "message_sha256": _sha256(str(exc).encode("utf-8")),
        }
    raise RehearsalV22Error(f"release negative probe was accepted: {name}")


def _run_disposable_release_probe(
    *,
    binding: ExecutionBinding,
    assembly: _BundleAssembly,
    execution_context: ExecutionCapability,
    validator_module: ModuleType,
) -> JsonObject:
    if binding.mode != "DISPOSABLE_FULL_SHAPE_TEST":
        return {
            "schema_version": "p4.2a-v2-2-release-probe-result-v1",
            "status": "NOT_EXECUTED_REGISTERED_OFFICIAL",
            "synthetic_receipt_created": False,
            "registered_paths_untouched": True,
        }
    observed = _validate_disposable_capability(
        execution_context,
        project_root=binding.project_root,
    )
    if observed != binding:
        raise RehearsalV22Error("release probe capability binding drifted")
    before_real = _real_path_fingerprints()
    official_receipt = REGISTERED_PROJECT_ROOT / RELEASE_RELATIVE
    official_review = REGISTERED_PROJECT_ROOT / RELEASE_REVIEW_REQUEST_RELATIVE
    before_official_release = {
        "receipt": _tree_fingerprint(official_receipt),
        "review_request": _tree_fingerprint(official_review),
    }
    bundle_path = binding.destination / BUNDLE_FILENAME
    bundle_sha256 = _sha256(
        _regular_bytes(bundle_path, "published disposable bundle")
    )
    if bundle_sha256 != _sha256(assembly.bundle_payload):
        raise RehearsalV22Error("published bundle differs before release probe")
    bundle_commit = _synthetic_git_commit_exact_path(
        binding,
        relative=DESTINATION_RELATIVE / BUNDLE_FILENAME,
        expected_status="A",
        message="P4.2a v2.2 disposable bundle evidence",
    )
    review_path = binding.project_root / RELEASE_REVIEW_REQUEST_RELATIVE
    review_payload = _release_review_request_payload(
        binding=binding,
        bundle_sha256=bundle_sha256,
        bundle_commit=bundle_commit,
    )
    _write_exclusive(review_path, review_payload, mode=0o600)
    review_commit = _synthetic_git_commit_exact_path(
        binding,
        relative=RELEASE_REVIEW_REQUEST_RELATIVE,
        expected_status="A",
        message="P4.2a v2.2 disposable evidence review request",
    )
    review_reference = AuthorityReference(
        RELEASE_REVIEW_REQUEST_RELATIVE.as_posix(),
        _sha256(review_payload),
        review_commit,
    )
    receipt_document = _release_receipt_document(
        binding=binding,
        bundle=assembly.document,
        bundle_sha256=bundle_sha256,
        review_request=review_reference,
        reviewed_head=review_commit,
    )
    receipt_payload = _canonical_json_bytes(receipt_document)
    receipt_path = binding.project_root / RELEASE_RELATIVE
    _write_exclusive(receipt_path, receipt_payload, mode=0o600)
    receipt_commit = _synthetic_git_commit_exact_path(
        binding,
        relative=RELEASE_RELATIVE,
        expected_status="A",
        message="P4.2a v2.2 disposable evidence acceptance receipt",
    )
    validate_release = getattr(
        validator_module,
        "validate_release_authorization",
        None,
    )
    if not callable(validate_release):
        raise RehearsalV22Error("independent release validator API is unavailable")
    authority = _TEMP_AUTHORITY.get()
    if authority is None:
        raise RehearsalV22Error("release probe lacks active temporary authority")
    positive_temp_before = _tree_fingerprint(authority)
    with _replay_observation_scope(execution_context) as replay_snapshot:
        with _borrow_validator_authority(
            execution_context,
            validator_module=validator_module,
        ) as delegation:
            validated = validate_release(
                project_root=binding.project_root,
                receipt_path=receipt_path,
                execution_context=execution_context,
                validator_delegation=delegation,
            )
        positive_replay_labels = replay_snapshot()
    positive_temp_after = _tree_fingerprint(authority)
    if (
        positive_replay_labels != ("run-a", "run-b")
        or positive_temp_before != positive_temp_after
    ):
        raise RehearsalV22Error("positive release replay count or temp cleanup drifted")
    if validated != receipt_document:
        raise RehearsalV22Error(
            "independent release validator returned different receipt bytes"
        )

    modified_receipt_document = copy.deepcopy(receipt_document)
    modified_receipt_document["created_at_utc"] = "2026-08-10T12:30:01Z"
    modified_receipt_document["created_at_shanghai"] = (
        "2026-08-10T20:30:01+08:00"
    )
    modified_receipt_payload = _canonical_json_bytes(modified_receipt_document)
    if modified_receipt_payload == receipt_payload:
        raise RehearsalV22Error("modified-after receipt did not change canonical bytes")
    _rewrite_existing_regular(receipt_path, modified_receipt_payload)
    modified_commit = _synthetic_git_commit_exact_path(
        binding,
        relative=RELEASE_RELATIVE,
        expected_status="M",
        message="P4.2a v2.2 disposable receipt modified-after evidence",
    )
    receipt_touch_history = _synthetic_path_touch_history(
        binding,
        RELEASE_RELATIVE,
    )
    if receipt_touch_history != (
        (receipt_commit, "A"),
        (modified_commit, "M"),
    ):
        raise RehearsalV22Error("modified receipt does not have exact A then M history")
    modified_temp_before = _tree_fingerprint(authority)
    with _replay_observation_scope(execution_context) as replay_snapshot:
        with _borrow_validator_authority(
            execution_context,
            validator_module=validator_module,
        ) as delegation:
            modified_rejected = _validator_rejection_evidence(
                "modified_after_creation_receipt",
                lambda: validate_release(
                    project_root=binding.project_root,
                    receipt_path=receipt_path,
                    execution_context=execution_context,
                    validator_delegation=delegation,
                ),
            )
        modified_replay_labels = replay_snapshot()
    modified_temp_after = _tree_fingerprint(authority)
    if modified_replay_labels or modified_temp_before != modified_temp_after:
        raise RehearsalV22Error("modified receipt reached replay or changed temp state")
    cross_temp_before = _tree_fingerprint(authority)
    with _replay_observation_scope(execution_context) as replay_snapshot:
        with _borrow_validator_authority(
            execution_context,
            validator_module=validator_module,
        ) as delegation:
            cross_official_rejected = _validator_rejection_evidence(
                "synthetic_capability_against_registered_official_root",
                lambda: validate_release(
                    project_root=REGISTERED_PROJECT_ROOT,
                    receipt_path=official_receipt,
                    execution_context=execution_context,
                    validator_delegation=delegation,
                ),
            )
        cross_replay_labels = replay_snapshot()
    cross_temp_after = _tree_fingerprint(authority)
    if cross_replay_labels or cross_temp_before != cross_temp_after:
        raise RehearsalV22Error("cross-root receipt reached replay or changed temp state")
    after_official_release = {
        "receipt": _tree_fingerprint(official_receipt),
        "review_request": _tree_fingerprint(official_review),
    }
    after_real = _real_path_fingerprints()
    if (
        before_real != after_real
        or before_official_release != after_official_release
        or _regular_bytes(receipt_path, "modified disposable receipt")
        != modified_receipt_payload
        or _git_bytes(
            binding.project_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            RELEASE_RELATIVE.as_posix(),
        )
    ):
        raise RehearsalV22Error("release probe changed a protected or final receipt byte")
    return {
        "schema_version": "p4.2a-v2-2-release-probe-result-v1",
        "status": "PASS_DISPOSABLE_SAME_VALIDATOR_RELEASE_ACCEPTANCE",
        "bundle_commit": bundle_commit,
        "review_request": review_reference.as_json(),
        "receipt": {
            "path": RELEASE_RELATIVE.as_posix(),
            "sha256": _sha256(receipt_payload),
            "creating_commit": receipt_commit,
            "creating_blob_sha256": _sha256(
                _git_blob(
                    binding.project_root,
                    receipt_commit,
                    RELEASE_RELATIVE.as_posix(),
                )
            ),
            "positive_validation_creation_state": {
                "unique_a_history_verified": True,
                "current_matches_creation_blob": True,
            },
        },
        "modified_after_creation": {
            "commit": modified_commit,
            "current_sha256": _sha256(modified_receipt_payload),
            "history": [
                {"commit": commit, "status": status}
                for commit, status in receipt_touch_history
            ],
            "history_statuses": [status for _, status in receipt_touch_history],
            "current_is_status_m_blob": True,
            "current_matches_creation_blob": False,
            "unique_a_current_after_negative_probe": False,
        },
        "bundle_commit_is_review_parent": True,
        "review_commit_is_reviewed_and_evidence_head": True,
        "public_release_validation_passed": True,
        "active_replay_evidence": {
            "positive_validation": {
                "invocation_count": len(positive_replay_labels),
                "run_labels": list(positive_replay_labels),
                "temporary_authority_tree_before": positive_temp_before,
                "temporary_authority_tree_after": positive_temp_after,
                "temporary_artifact_inventory_unchanged": True,
            },
            "modified_after_creation_rejection": {
                "invocation_count": len(modified_replay_labels),
                "run_labels": list(modified_replay_labels),
                "temporary_authority_tree_before": modified_temp_before,
                "temporary_authority_tree_after": modified_temp_after,
                "temporary_artifact_inventory_unchanged": True,
            },
            "cross_official_root_rejection": {
                "invocation_count": len(cross_replay_labels),
                "run_labels": list(cross_replay_labels),
                "temporary_authority_tree_before": cross_temp_before,
                "temporary_authority_tree_after": cross_temp_after,
                "temporary_artifact_inventory_unchanged": True,
            },
        },
        "modified_after_creation_rejection": modified_rejected,
        "cross_official_root_rejection": cross_official_rejected,
        "registered_fingerprints_before": before_real,
        "registered_fingerprints_after": after_real,
        "registered_release_paths_before": before_official_release,
        "registered_release_paths_after": after_official_release,
        "registered_paths_untouched": True,
        "authorized_stages": [],
        "heldout_evaluation_attempts_consumed": 0,
    }


def _rejection_probe(name: str, operation: Callable[[], object]) -> JsonObject:
    try:
        operation()
    except RehearsalV22Error as exc:
        return {
            "name": name,
            "result": "PASS_REJECTED_BEFORE_EFFECT",
            "exception_type": type(exc).__name__,
            "message_sha256": _sha256(str(exc).encode("utf-8")),
        }
    raise RehearsalV22Error(f"negative authority probe was accepted: {name}")


def _preallocation_authority_probes(
    *,
    binding: ExecutionBinding,
    bootstrap: _BootstrapEvidence,
    execution_context: ExecutionCapability,
    action: ActionAuthorization,
    validator_module: ModuleType,
    policy: _AuditPolicy,
) -> JsonObject:
    before = _real_path_fingerprints()
    bootstrap_closure = inspect.getclosurevars(_validate_bootstrap_evidence).nonlocals
    capability_closure = inspect.getclosurevars(_validate_execution_capability).nonlocals
    delegation_closure = inspect.getclosurevars(_validate_validator_delegation).nonlocals
    policy_closure = inspect.getclosurevars(_audit_policy_is_issued).nonlocals
    registries = {
        "bootstrap": bootstrap_closure.get("bootstrap_registry"),
        "capability": capability_closure.get("capability_registry"),
        "delegation": delegation_closure.get("delegation_registry"),
        "audit_policy": policy_closure.get("policy_registry"),
    }
    if any(not isinstance(value, tuple) for value in registries.values()):
        raise RehearsalV22Error("authority issuance registry is mutable or unavailable")

    forged_bootstrap = _BootstrapEvidence(
        _nonce=bootstrap_closure["bootstrap_nonce"],
        project_root=bootstrap.project_root,
        shim_path=bootstrap.shim_path,
        argv=bootstrap.argv,
        orig_argv=bootstrap.orig_argv,
        environment=dict(bootstrap.environment),
    )
    stale_bootstrap_snapshot = cast(tuple[object, ...], registries["bootstrap"])
    stale_bootstrap_snapshot = (*stale_bootstrap_snapshot, forged_bootstrap)
    del stale_bootstrap_snapshot
    probes = [
        _rejection_probe(
            "stolen_bootstrap_nonce_and_stale_registry_snapshot",
            lambda: _validate_bootstrap_evidence(forged_bootstrap),
        )
    ]

    capability_nonce = capability_closure["capability_nonce"]
    if binding.mode == "DISPOSABLE_FULL_SHAPE_TEST":
        forged_capability: ExecutionCapability = _DisposableCapability(
            _nonce=capability_nonce,
            binding=binding,
            bootstrap=bootstrap,
            action_authorization=action,
            real_path_fingerprints=_real_path_fingerprints(),
            boundary_ids=_fake_boundary_ids(),
        )
    else:
        forged_capability = _OfficialExecutionCapability(
            _nonce=capability_nonce,
            binding=binding,
            bootstrap=bootstrap,
            action_authorization=action,
        )
    stale_capability_snapshot = cast(tuple[object, ...], registries["capability"])
    stale_capability_snapshot = (*stale_capability_snapshot, forged_capability)
    del stale_capability_snapshot
    probes.append(
        _rejection_probe(
            "direct_capability_constructor_and_stale_registry_snapshot",
            lambda: _validate_execution_capability(
                forged_capability,
                project_root=binding.project_root,
            ),
        )
    )

    delegation_nonce = delegation_closure["delegation_nonce"]
    forged_delegation = ValidatorDelegation(
        _nonce=delegation_nonce,
        binding=binding,
        capability_id=id(execution_context),
        validator_module_id=id(validator_module),
        audit_policy_id=id(policy),
        temp_authority=cast(Path, _TEMP_AUTHORITY.get()),
        creator_module_id=id(sys.modules[MODULE_NAME]),
        lifetime_id=id(object()),
    )
    stale_delegation_snapshot = cast(tuple[object, ...], registries["delegation"])
    stale_delegation_snapshot = (*stale_delegation_snapshot, forged_delegation)
    del stale_delegation_snapshot
    probes.append(
        _rejection_probe(
            "direct_delegation_constructor_and_stale_registry_snapshot",
            lambda: _validate_validator_delegation(
                forged_delegation,
                execution_context=execution_context,
                validator_module=validator_module,
                project_root=binding.project_root,
            ),
        )
    )

    external = binding.project_root.parent / (
        f".v2-2-forged-policy-probe-{binding.series_token_sha256[:16]}"
    )
    if os.path.lexists(external):
        raise RehearsalV22Error("forged-policy probe target unexpectedly exists")
    forged_policy = replace(policy, write_roots=(*policy.write_roots, external))
    stale_policy_snapshot = cast(tuple[object, ...], registries["audit_policy"])
    stale_policy_snapshot = (*stale_policy_snapshot, forged_policy)
    del stale_policy_snapshot

    def attempt_forged_policy_write() -> None:
        token = _AUDIT_POLICY.set(forged_policy)
        try:
            os.mkdir(external, 0o700)
        finally:
            _AUDIT_POLICY.reset(token)

    probes.append(
        _rejection_probe(
            "direct_audit_policy_and_stale_registry_snapshot",
            lambda: contextvars.copy_context().run(attempt_forged_policy_write),
        )
    )
    if os.path.lexists(external):
        raise RehearsalV22Error("forged audit policy created an external path")
    broadened = replace(policy, write_roots=(*policy.write_roots, external))
    probes.append(
        _rejection_probe(
            "capability_cannot_broaden_write_roots",
            lambda: _audited_execution(
                broadened,
                execution_context=execution_context,
            ).__enter__(),
        )
    )
    wrong_action = replace(action, ordinal=action.ordinal + 1)
    if isinstance(execution_context, _DisposableCapability):
        wrong_capability: ExecutionCapability = _DisposableCapability(
            _nonce=capability_nonce,
            binding=binding,
            bootstrap=bootstrap,
            action_authorization=wrong_action,
            real_path_fingerprints=_real_path_fingerprints(),
            boundary_ids=_fake_boundary_ids(),
        )
    else:
        wrong_capability = _OfficialExecutionCapability(
            _nonce=capability_nonce,
            binding=binding,
            bootstrap=bootstrap,
            action_authorization=wrong_action,
        )
    probes.append(
        _rejection_probe(
            "wrong_action_receipt_capability_rejected",
            lambda: _validate_execution_capability(
                wrong_capability,
                project_root=binding.project_root,
            ),
        )
    )
    after = _real_path_fingerprints()
    if before != after:
        raise RehearsalV22Error("authority forgery probes changed registered paths")
    return {
        "schema_version": "p4.2a-v2-2-authority-forgery-probes-v1",
        "phase": "before_ledger_allocation_or_write",
        "registry_storage": {
            name: "closure_private_rebound_immutable_tuple" for name in sorted(registries)
        },
        "probes": probes,
        "all_rejected": True,
        "real_path_fingerprints_before": before,
        "real_path_fingerprints_after": after,
        "real_path_fingerprints_unchanged": True,
    }


def _validate_active_ledger_positive_transition(
    *,
    binding: ExecutionBinding,
    before_real: Mapping[str, Mapping[str, str]],
    after_real: Mapping[str, Mapping[str, str]],
    before_active: Mapping[str, str],
    after_active: Mapping[str, str],
    created: tuple[tuple[Path, bytes], ...],
) -> JsonObject:
    if set(before_real) != set(after_real) or set(before_real) != {
        "registered_v2_2_destination",
        "registered_v2_2_ledger",
        "retired_v2_1_destination",
        "consumed_v2_1_claim",
        "real_heldout_root",
    }:
        raise RehearsalV22Error("registered fingerprint key set drifted")
    expected_created: dict[str, str] = {}
    for path, payload in created:
        try:
            relative = path.relative_to(binding.ledger_root).as_posix()
        except ValueError as exc:
            raise RehearsalV22Error(
                "positive ledger evidence escaped the active ledger"
            ) from exc
        if relative in expected_created:
            raise RehearsalV22Error("positive ledger evidence path was duplicated")
        expected_created[relative] = f"file:{_sha256(payload)}:0600:1"
    changed_existing = {
        relative
        for relative, value in before_active.items()
        if after_active.get(relative) != value
    }
    created_active = {
        relative: value
        for relative, value in after_active.items()
        if relative not in before_active
    }
    removed_active = set(before_active).difference(after_active)
    if (
        changed_existing
        or removed_active
        or created_active != expected_created
        or before_active == after_active
    ):
        raise RehearsalV22Error(
            "active ledger positive create-only transition exceeded exact evidence writes"
        )
    official = binding.mode == "REGISTERED_OFFICIAL"
    if official:
        if (
            binding.ledger_root != OFFICIAL_LEDGER_ROOT
            or before_real["registered_v2_2_ledger"] != before_active
            or after_real["registered_v2_2_ledger"] != after_active
        ):
            raise RehearsalV22Error(
                "official active ledger fingerprint is not the registered ledger fingerprint"
            )
        changed_real = {
            name for name in before_real if before_real[name] != after_real[name]
        }
        if changed_real != {"registered_v2_2_ledger"}:
            raise RehearsalV22Error(
                "official positive ledger writes changed a non-ledger registered path"
            )
    elif binding.mode == "DISPOSABLE_FULL_SHAPE_TEST":
        if binding.ledger_root == OFFICIAL_LEDGER_ROOT or before_real != after_real:
            raise RehearsalV22Error(
                "disposable positive ledger writes changed a real registered path"
            )
    else:
        raise RehearsalV22Error("positive ledger transition mode drifted")
    return {
        "active_ledger_fingerprinted": True,
        "active_ledger_is_registered_ledger": official,
        "exact_legal_create_only_paths": sorted(
            expected_created, key=lambda value: value.encode("utf-8")
        ),
        "non_active_registered_paths_unchanged": True,
        "negative_probe_baseline_is_after_positive_writes": True,
    }


def _ledger_create_only_probes(
    *,
    binding: ExecutionBinding,
    lease: AttemptLease,
    prior_history: HistoryValidation,
) -> JsonObject:
    before_real = _real_path_fingerprints()
    before_active = _tree_fingerprint(binding.ledger_root)
    first = lease.persist_evidence("probes/same-parent-first.txt", b"first\n")
    second = lease.persist_evidence("probes/same-parent-second.txt", b"second\n")
    if (
        first.parent != second.parent
        or first.read_bytes() != b"first\n"
        or second.read_bytes() != b"second\n"
    ):
        raise RehearsalV22Error("same-parent create-only evidence positive probe failed")
    positive_real = _real_path_fingerprints()
    positive_active = _tree_fingerprint(binding.ledger_root)
    fingerprint_discipline = _validate_active_ledger_positive_transition(
        binding=binding,
        before_real=before_real,
        after_real=positive_real,
        before_active=before_active,
        after_active=positive_active,
        created=((first, b"first\n"), (second, b"second\n")),
    )
    negative_real_baseline = positive_real
    negative_active_baseline = positive_active
    target = lease.attempt_root / "started.json"
    relocated = lease.attempt_root / "started-relocated.json"
    linked = lease.attempt_root / "started-linked.json"
    symlinked = lease.attempt_root / "started-symlink.json"
    authority = _TEMP_AUTHORITY.get()
    if authority is None:
        raise RehearsalV22Error("ledger probe lacks temporary authority")
    external = authority.parent / f"v2-2-external-probe-{lease.attempt_token_sha256[:16]}"
    for path in (relocated, linked, symlinked, external):
        if os.path.lexists(path):
            raise RehearsalV22Error("ledger mutation probe target unexpectedly exists")

    def append_existing() -> None:
        with target.open("ab") as stream:
            stream.write(b"forbidden\n")

    def delete_recreate() -> None:
        target.unlink()
        _write_exclusive(target, b"recreated\n", mode=0o600)

    operations: tuple[tuple[str, Callable[[], object]], ...] = (
        ("append_existing", append_existing),
        ("delete_existing", target.unlink),
        ("delete_then_recreate_existing", delete_recreate),
        ("rename_existing", lambda: target.rename(relocated)),
        ("chmod_existing", lambda: target.chmod(0o400)),
        ("hardlink_existing", lambda: os.link(target, linked)),
        ("symlink_existing", lambda: symlinked.symlink_to(target)),
        ("external_mkdir", lambda: os.mkdir(external, 0o700)),
    )
    mutation_probes: list[JsonObject] = []
    for name, operation in operations:
        before_tree = _tree_fingerprint(binding.ledger_root)
        mutation_probes.append(_rejection_probe(name, operation))
        if _tree_fingerprint(binding.ledger_root) != before_tree:
            raise RehearsalV22Error(f"ledger mutation probe changed bytes: {name}")
    dir_fd_tree_before = _tree_fingerprint(binding.ledger_root)
    target_metadata_before = target.lstat()
    target_bytes_before = target.read_bytes()
    attempt_descriptor = os.open(lease.attempt_root, os.O_RDONLY)
    try:
        mutation_probes.append(
            _rejection_probe(
                "valid_dir_fd_delete_existing",
                lambda: os.unlink("started.json", dir_fd=attempt_descriptor),
            )
        )
    finally:
        os.close(attempt_descriptor)
    target_metadata_after = target.lstat()
    if (
        (target_metadata_after.st_dev, target_metadata_after.st_ino)
        != (target_metadata_before.st_dev, target_metadata_before.st_ino)
        or target.read_bytes() != target_bytes_before
        or _tree_fingerprint(binding.ledger_root) != dir_fd_tree_before
    ):
        raise RehearsalV22Error("valid dir_fd ledger rejection changed live bytes")
    current = _AUDIT_POLICY.get()
    if current is None or not _audit_policy_is_issued(current):
        raise RehearsalV22Error("ledger frozen-phase probe lacks issued policy")
    frozen_target = lease.evidence_root / "probes/after-terminal-forbidden.txt"

    def frozen_write() -> None:
        with _audited_execution(
            replace(
                current,
                ledger_write_phase="frozen",
                ledger_root=binding.ledger_root,
                active_attempt_root=lease.attempt_root,
            )
        ):
            _write_exclusive(frozen_target, b"forbidden\n", mode=0o600)

    mutation_probes.append(
        _rejection_probe("after_terminal_or_candidate_new_evidence", frozen_write)
    )
    prior_probes: list[JsonObject] = []
    for record in prior_history.records:
        late = record.started_path.parent / "evidence" / "late-evidence-forbidden.txt"

        def late_write(path: Path = late) -> None:
            _write_exclusive(path, b"forbidden\n", mode=0o600)

        prior_probes.append(
            _rejection_probe(
                f"prior_{record.outcome.lower()}_attempt_late_evidence_{record.ordinal}",
                late_write,
            )
        )
    after_real = _real_path_fingerprints()
    after_active = _tree_fingerprint(binding.ledger_root)
    if negative_real_baseline != after_real or negative_active_baseline != after_active:
        raise RehearsalV22Error("ledger create-only probes changed registered paths")
    return {
        "schema_version": "p4.2a-v2-2-ledger-create-only-probes-v1",
        "phase": "active_genuine_capability_before_candidate",
        "same_nested_directory_two_files": {
            "result": "PASS_TWO_CREATE_ONLY_FILES_PERSISTED",
            "relative_paths": [
                first.relative_to(lease.evidence_root).as_posix(),
                second.relative_to(lease.evidence_root).as_posix(),
            ],
        },
        "active_ledger_fingerprint_discipline": fingerprint_discipline,
        "event_mutation_probes": mutation_probes,
        "prior_attempt_late_evidence_probes": prior_probes,
        "all_forbidden_mutations_rejected_before_effect": True,
        "real_path_fingerprints_before": negative_real_baseline,
        "real_path_fingerprints_after": after_real,
        "real_path_fingerprints_unchanged": True,
    }


def _atomic_no_replace_negative_probe(authority: Path) -> JsonObject:
    source = authority / "atomic-no-replace-probe-source"
    destination = authority / "atomic-no-replace-probe-destination"
    if os.path.lexists(source) or os.path.lexists(destination):
        raise RehearsalV22Error("atomic no-replace probe paths already exist")
    os.mkdir(source, 0o700)
    os.mkdir(destination, 0o700)
    _write_exclusive(source / "source.txt", b"source\n", mode=0o600)
    destination_metadata = destination.lstat()
    destination_before = _tree_fingerprint(destination)
    source_before = _tree_fingerprint(source)
    result = _rejection_probe(
        "renamex_np_RENAME_EXCL_existing_destination",
        lambda: _rename_directory_exclusive(source, destination),
    )
    destination_after = destination.lstat()
    if (
        not source.is_dir()
        or _tree_fingerprint(source) != source_before
        or destination_after.st_dev != destination_metadata.st_dev
        or destination_after.st_ino != destination_metadata.st_ino
        or _tree_fingerprint(destination) != destination_before
    ):
        raise RehearsalV22Error("atomic no-replace probe changed source or destination")
    evidence = {
        "schema_version": "p4.2a-v2-2-atomic-publication-probe-v1",
        "syscall": "renamex_np",
        "flag": "RENAME_EXCL",
        "flag_value": 4,
        "expected_errno": errno.EEXIST,
        "probe": result,
        "candidate_preserved": True,
        "existing_destination_device": destination_metadata.st_dev,
        "existing_destination_inode": destination_metadata.st_ino,
        "existing_destination_tree_unchanged": True,
        "single_kernel_no_replace_required_for_real_publish": True,
        "destination_parent_fsync_required_after_success": True,
    }
    shutil.rmtree(source)
    shutil.rmtree(destination)
    if os.path.lexists(source) or os.path.lexists(destination):
        raise RehearsalV22Error("atomic no-replace probe cleanup failed")
    return evidence


def _main_project_root() -> Path:
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    if not isinstance(main_module, ModuleType) or not isinstance(main_file, str):
        raise RehearsalV22Error("v2.2 runner lacks one exact OS __main__ source")
    shim = Path(main_file).resolve(strict=True)
    root = shim.parent.parent.absolute()
    if (
        shim != root / SHIM_RELATIVE
        or shim.is_symlink()
        or root.resolve(strict=True) != root
        or sys.modules.get(MODULE_NAME) is not sys.modules.get(__name__)
    ):
        raise RehearsalV22Error("v2.2 runner source or package identity drifted")
    _assert_locked_runner_bootstrap(root)
    return root


def _action_reference(
    binding: ExecutionBinding,
    *,
    execution_head: str,
) -> AuthorityReference:
    try:
        relative = binding.action_authorization_path.relative_to(
            binding.project_root
        ).as_posix()
    except ValueError as exc:
        raise RehearsalV22Error("action authorization path escapes project root") from exc
    normalized = _relative_text(relative, "action authorization path")
    payload = _regular_bytes(
        binding.action_authorization_path,
        "action-time owner authorization",
        allow_zero=False,
    )
    creating_commit = _unique_a_commit_for_path(
        binding.project_root,
        normalized,
        execution_head=execution_head,
    )
    return AuthorityReference(normalized, _sha256(payload), creating_commit)


def _authority_reference_for_path(
    project_root: Path,
    authority_path: Path,
    *,
    execution_head: str,
    label: str,
) -> AuthorityReference:
    root = project_root.absolute()
    try:
        relative = authority_path.absolute().relative_to(root).as_posix()
    except ValueError as exc:
        raise RehearsalV22Error(f"{label} path escapes project root") from exc
    normalized = _relative_text(relative, f"{label} path")
    payload = _regular_bytes(
        root / normalized,
        label,
        allow_zero=False,
    )
    creating_commit = _unique_a_commit_for_path(
        root,
        normalized,
        execution_head=execution_head,
    )
    return AuthorityReference(normalized, _sha256(payload), creating_commit)


def _read_only_implementation_preflight(
    project_root: Path,
    *,
    implementation_epoch: int,
    implementation_commit: str,
    owner_surface_authorization_path: Path,
    independent_review_path: Path,
) -> JsonObject:
    """Validate the real implementation lineage and bytes without an attempt receipt."""

    root = project_root.absolute()
    policy = _AUDIT_POLICY.get()
    if (
        policy is None
        or not _audit_policy_is_issued(policy)
        or policy.project_root != root
        or policy.write_roots
        or policy.exact_write_paths
        or policy.create_only_roots
        or policy.sqlite_roots
        or policy.git_roots != (root,)
        or policy.subprocess_mode != "git-read"
    ):
        raise RehearsalV22Error("read-only implementation preflight lacks a zero-write policy")
    git_directory = root / ".git"
    for forbidden in (
        git_directory / "shallow",
        git_directory / "objects/info/alternates",
    ):
        if os.path.lexists(forbidden):
            raise RehearsalV22Error(
                "read-only implementation preflight rejects shallow or alternate Git objects"
            )
    git_config = _regular_bytes(
        git_directory / "config",
        "read-only implementation preflight Git config",
        allow_zero=False,
    ).lower()
    if (
        b"promisor" in git_config
        or b"partialclone" in git_config
        or b"[include" in git_config
    ):
        raise RehearsalV22Error(
            "read-only implementation preflight rejects partial or included Git config"
        )
    execution_head = _current_execution_head(root)
    owner_surface = _authority_reference_for_path(
        root,
        owner_surface_authorization_path,
        execution_head=execution_head,
        label="owner exact-surface authorization",
    )
    independent_review = _authority_reference_for_path(
        root,
        independent_review_path,
        execution_head=execution_head,
        label="independent implementation review",
    )
    control = build_control_surface(
        root,
        implementation_commit,
        require_current=True,
    )
    epoch = validate_implementation_epoch(
        root,
        epoch=implementation_epoch,
        implementation_commit=implementation_commit,
        owner_surface_authorization=owner_surface,
        independent_review=independent_review,
        control_merkle_root_sha256=control.merkle_root_sha256,
        execution_head=execution_head,
        require_current_bytes=True,
    )
    registered_surface = [
        {
            "path": relative.as_posix(),
            "sha256": _sha256(
                validate_implementation_blob(
                    root,
                    epoch.implementation_commit,
                    relative.as_posix(),
                    require_current=True,
                )
            ),
        }
        for relative in IMPLEMENTATION_SURFACE
    ]
    if (
        _current_execution_head(root) != execution_head
        or _authority_reference_for_path(
            root,
            owner_surface_authorization_path,
            execution_head=execution_head,
            label="owner exact-surface authorization",
        )
        != owner_surface
        or _authority_reference_for_path(
            root,
            independent_review_path,
            execution_head=execution_head,
            label="independent implementation review",
        )
        != independent_review
    ):
        raise RehearsalV22Error("read-only implementation preflight snapshot changed")
    return {
        "schema_version": "p4.2a-v2-2-read-only-implementation-preflight-v1",
        "status": "PASS_READ_ONLY_IMPLEMENTATION_PREFLIGHT",
        "mode": (
            "REGISTERED_OFFICIAL"
            if root == REGISTERED_PROJECT_ROOT
            else "NONREGISTERED_READ_ONLY_TEST"
        ),
        "execution_head": execution_head,
        "implementation_epoch": epoch.epoch,
        "implementation_commit": epoch.implementation_commit,
        "owner_exact_surface_authorization": owner_surface.as_json(),
        "independent_implementation_review": independent_review.as_json(),
        "control_merkle_root_sha256": control.merkle_root_sha256,
        "control_record_count": len(control.records),
        "registered_surface": registered_surface,
        "effect_summary": {
            "action_receipt_required": False,
            "action_receipts_read": 0,
            "project_and_gate_state_writes_permitted": False,
            "temporary_authorities_created": 0,
            "ledgers_created": 0,
            "attempts_allocated": 0,
            "pipeline_starts": 0,
            "automatic_retries": 0,
            "heldout_evaluation_attempts_consumed": 0,
            "shallow_alternate_partial_and_included_git_config_rejected": True,
            "stdout_persistence_controlled_by_caller": True,
        },
    }


def _preflight_action(
    binding: ExecutionBinding,
    *,
    expected_ordinal: int,
) -> tuple[HistoryValidation, ActionAuthorization, ControlSurface]:
    if os.path.lexists(binding.destination):
        raise RehearsalV22Error("v2.2 rehearsal destination already exists")
    history = validate_live_history(binding)
    if history.series_closed:
        raise RehearsalV22Error("v2.2 rehearsal series is already closed")
    next_ordinal = history.started_count + 1
    if expected_ordinal != next_ordinal:
        raise RehearsalV22Error("expected ordinal differs from disclosed live history")
    execution_head = _current_execution_head(binding.project_root)
    reference = _action_reference(binding, execution_head=execution_head)
    action = _validate_action_authorization(
        binding,
        reference,
        expected_ordinal=next_ordinal,
        expected_previous_history_root_sha256=history.history_root_sha256,
        require_current_process=True,
    )
    control = build_control_surface(
        binding.project_root,
        action.implementation_commit,
        require_current=True,
    )
    if control.merkle_root_sha256 != action.control_merkle_root_sha256:
        raise RehearsalV22Error("action authorization control root is not current")
    return history, action, control


def _temporary_authority_path(
    binding: ExecutionBinding,
    action: ActionAuthorization,
) -> Path:
    parent = binding.project_root.parent
    if (
        parent.is_symlink()
        or not parent.is_dir()
        or parent.resolve(strict=True) != parent.absolute()
    ):
        raise RehearsalV22Error("temporary authority parent is aliased")
    attempt_token = _attempt_token_sha256(
        series_token_sha256=binding.series_token_sha256,
        ordinal=action.ordinal,
        implementation_commit=action.implementation_commit,
        previous_history_root_sha256=action.previous_history_root_sha256,
    )
    authority = parent / f".alphapilot-p4-2a-v2-2-temp-{attempt_token}"
    protected = (
        binding.project_root,
        binding.destination,
        binding.ledger_root,
        REGISTERED_PROJECT_ROOT,
        OFFICIAL_DESTINATION,
        OFFICIAL_LEDGER_ROOT,
        V2_1_DESTINATION,
        V2_1_EMPTY_CLAIM,
        PROTECTED_HELDOUT_ROOT,
    )
    if os.path.lexists(authority) or any(
        authority == path or authority.is_relative_to(path) or path.is_relative_to(authority)
        for path in protected
    ):
        raise RehearsalV22Error("temporary authority exists already or overlaps protected state")
    return authority


def _recovery_claim_path(
    binding: ExecutionBinding,
    authorization: BundleRecoveryAuthorization,
) -> Path:
    parent = binding.project_root.parent
    if (
        parent.is_symlink()
        or not parent.is_dir()
        or parent.resolve(strict=True) != parent.absolute()
    ):
        raise RehearsalV22Error("recovery claim parent is aliased")
    claim = parent / (
        ".alphapilot-p4-2a-v2-2-bundle-recovery-claim-" + authorization.sha256
    )
    protected = (
        binding.project_root,
        binding.destination,
        binding.ledger_root,
        REGISTERED_PROJECT_ROOT,
        OFFICIAL_DESTINATION,
        OFFICIAL_LEDGER_ROOT,
        V2_1_DESTINATION,
        V2_1_EMPTY_CLAIM,
        PROTECTED_HELDOUT_ROOT,
    )
    if any(
        claim == path or claim.is_relative_to(path) or path.is_relative_to(claim)
        for path in protected
    ):
        raise RehearsalV22Error("recovery claim overlaps protected state")
    return claim


def _recovery_temporary_authority_path(
    binding: ExecutionBinding,
    authorization: BundleRecoveryAuthorization,
) -> Path:
    parent = binding.project_root.parent
    authority = parent / (
        ".alphapilot-p4-2a-v2-2-bundle-recovery-temp-" + authorization.sha256
    )
    claim = _recovery_claim_path(binding, authorization)
    protected = (
        claim,
        binding.project_root,
        binding.destination,
        binding.ledger_root,
        REGISTERED_PROJECT_ROOT,
        OFFICIAL_DESTINATION,
        OFFICIAL_LEDGER_ROOT,
        V2_1_DESTINATION,
        V2_1_EMPTY_CLAIM,
        PROTECTED_HELDOUT_ROOT,
    )
    if any(
        authority == path
        or authority.is_relative_to(path)
        or path.is_relative_to(authority)
        for path in protected
    ):
        raise RehearsalV22Error("recovery temporary authority overlaps protected state")
    return authority


def _timestamp_pair() -> tuple[str, str]:
    current = datetime.now(UTC).replace(microsecond=0)
    shanghai = current.astimezone(timezone(timedelta(hours=8)))
    return (
        current.isoformat().replace("+00:00", "Z"),
        shanghai.isoformat(),
    )


@contextmanager
def _shared_series_lock(binding: ExecutionBinding) -> Iterator[None]:
    path = binding.ledger_root / ".series.lock"
    descriptor = os.open(path, os.O_RDONLY)
    try:
        metadata = os.fstat(descriptor)
        path_metadata = path.stat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino)
            != (path_metadata.st_dev, path_metadata.st_ino)
        ):
            raise RehearsalV22Error("recovery series lock identity drifted")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise RehearsalV22Error("recovery could not acquire the shared series lock") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _recovery_reference(
    binding: ExecutionBinding,
    *,
    execution_head: str,
) -> AuthorityReference:
    return _authority_reference_for_path(
        binding.project_root,
        binding.action_authorization_path,
        execution_head=execution_head,
        label="sealed-bundle recovery authorization",
    )


def _preflight_bundle_recovery(
    binding: ExecutionBinding,
) -> tuple[
    HistoryValidation,
    BundleRecoveryAuthorization,
    HistoricalSelectedAnchor,
    LiveExecutionAnchor,
    _SealedPipelineReplay,
    _SealedPipelineReplay,
    Mapping[str, str],
]:
    if os.path.lexists(binding.destination):
        raise RehearsalV22Error("bundle recovery destination already exists")
    execution_head = _current_execution_head(binding.project_root)
    reference = _recovery_reference(binding, execution_head=execution_head)
    authorization = _validate_bundle_recovery_authorization(
        binding,
        reference,
        require_current_process=True,
    )
    claim = _recovery_claim_path(binding, authorization)
    temporary = _recovery_temporary_authority_path(binding, authorization)
    if os.path.lexists(claim) or os.path.lexists(temporary):
        raise RehearsalV22Error("recovery authority is already consumed or staged")
    history = validate_live_history(binding)
    historical = _historical_selected_anchor(binding, history)
    live = _live_execution_anchor(binding, authorization.execution_epoch)
    observation = _module_identity_observation()
    validator_module = sys.modules.get(
        "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
    )
    validator_origin = getattr(validator_module, "__file__", None)
    if (
        observation.module_origin != binding.project_root / IMPLEMENTATION_RELATIVE
        or not isinstance(validator_module, ModuleType)
        or not isinstance(validator_origin, str)
        or Path(validator_origin).resolve(strict=True)
        != binding.project_root / VALIDATOR_RELATIVE
        or live.loaded_module_sha256
        != {
            IMPLEMENTATION_RELATIVE.as_posix(): _sha256(
                _regular_bytes(observation.module_origin, "loaded recovery producer")
            ),
            VALIDATOR_RELATIVE.as_posix(): _sha256(
                _regular_bytes(
                    Path(validator_origin).resolve(strict=True),
                    "loaded recovery validator",
                )
            ),
        }
    ):
        raise RehearsalV22Error("recovery loaded-module identity drifted before claim")
    epoch_rows = _implementation_epochs(
        binding,
        history,
        historical_anchor=historical,
    )
    execution_head = _current_execution_head(binding.project_root)
    if (
        [row.get("epoch") for row in epoch_rows] != [1, 2, 3, 4]
        or epoch_rows[0]
        != _void_epoch_one(binding, execution_head=execution_head)
        or epoch_rows[2]
        != _void_epoch_three(binding, execution_head=execution_head)
        or [
            (row.get("first_attempt_ordinal"), row.get("last_attempt_ordinal"))
            for row in epoch_rows
        ]
        != [(1, 1), (1, 1), (2, 2), (2, 2)]
        or any(type(row.get("all_attempts_authorized")) is not bool for row in epoch_rows)
        or epoch_rows[1].get("all_attempts_authorized") is not True
        or epoch_rows[3].get("all_attempts_authorized") is not True
    ):
        raise RehearsalV22Error("recovery history is not the exact disclosed [2,4] shape")
    run_a, run_b = _rehydrate_sealed_pipeline_replays(
        binding,
        history,
        authorization,
    )
    ledger_snapshot = _tree_fingerprint(binding.ledger_root)
    if ledger_snapshot == {".": "absent"}:
        raise RehearsalV22Error("recovery sealed ledger is absent")
    return (
        history,
        authorization,
        historical,
        live,
        run_a,
        run_b,
        ledger_snapshot,
    )


def _authority_creation_policy(
    binding: ExecutionBinding,
    authority: Path,
) -> _AuditPolicy:
    return _AuditPolicy(
        project_root=binding.project_root,
        write_roots=(),
        exact_write_paths=(authority,),
        create_only_roots=(),
        sqlite_roots=(),
        git_roots=(binding.project_root,),
        subprocess_mode="git-read",
    )


def _create_temporary_authority(
    binding: ExecutionBinding,
    action: ActionAuthorization,
    *,
    bootstrap: _BootstrapEvidence,
) -> Path:
    authority = _temporary_authority_path(binding, action)
    with _audited_execution(
        _authority_creation_policy(binding, authority),
        bootstrap=bootstrap,
    ):
        os.mkdir(authority, 0o700)
        try:
            metadata = authority.lstat()
            if (
                authority.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o700
                or authority.resolve(strict=True) != authority.absolute()
            ):
                raise RehearsalV22Error("created temporary authority is aliased or has wrong mode")
            _fsync_directory(authority.parent)
        except BaseException:
            if authority.is_dir() and not authority.is_symlink():
                os.rmdir(authority)
                _fsync_directory(authority.parent)
            raise
    return authority


def _preflight_policy(binding: ExecutionBinding) -> _AuditPolicy:
    return _read_only_preflight_policy(binding.project_root)


def _read_only_preflight_policy(project_root: Path) -> _AuditPolicy:
    root = project_root.absolute()
    return _AuditPolicy(
        project_root=root,
        write_roots=(),
        exact_write_paths=(),
        create_only_roots=(),
        sqlite_roots=(),
        git_roots=(root,),
        subprocess_mode="git-read",
    )


def _execution_policy(binding: ExecutionBinding, authority: Path) -> _AuditPolicy:
    disposable_release_paths: tuple[Path, ...] = ()
    subprocess_mode: Literal["git-read", "synthetic-git"] = "git-read"
    if binding.mode == "DISPOSABLE_FULL_SHAPE_TEST":
        disposable_release_paths = (
            binding.project_root / RELEASE_REVIEW_REQUEST_RELATIVE,
            binding.project_root / RELEASE_RELATIVE,
        )
        subprocess_mode = "synthetic-git"
    return _AuditPolicy(
        project_root=binding.project_root,
        write_roots=(authority, binding.ledger_root),
        exact_write_paths=(
            binding.ledger_root,
            binding.destination,
            *disposable_release_paths,
        ),
        create_only_roots=(binding.ledger_root,),
        sqlite_roots=(authority,),
        git_roots=(binding.project_root,),
        subprocess_mode=subprocess_mode,
    )


def _recovery_exact_path_policy(binding: ExecutionBinding, path: Path) -> _AuditPolicy:
    return _AuditPolicy(
        project_root=binding.project_root,
        write_roots=(),
        exact_write_paths=(path,),
        create_only_roots=(),
        sqlite_roots=(),
        git_roots=(binding.project_root,),
        subprocess_mode="git-read",
    )


def _recovery_claim_policy(binding: ExecutionBinding, claim_root: Path) -> _AuditPolicy:
    return _AuditPolicy(
        project_root=binding.project_root,
        write_roots=(claim_root,),
        exact_write_paths=(),
        create_only_roots=(),
        sqlite_roots=(),
        git_roots=(binding.project_root,),
        subprocess_mode="git-read",
    )


def _recovery_execution_policy(
    binding: ExecutionBinding,
    *,
    claim_root: Path,
    temporary_authority: Path,
) -> _AuditPolicy:
    return _AuditPolicy(
        project_root=binding.project_root,
        write_roots=(claim_root, temporary_authority),
        exact_write_paths=(binding.destination,),
        create_only_roots=(),
        sqlite_roots=(),
        git_roots=(binding.project_root,),
        subprocess_mode="git-read",
        synthetic_git_root=None,
    )


def _create_recovery_claim(
    binding: ExecutionBinding,
    authorization: BundleRecoveryAuthorization,
    historical_anchor: HistoricalSelectedAnchor,
    live_anchor: LiveExecutionAnchor,
    *,
    bootstrap: _BootstrapEvidence,
) -> Path:
    claim = _recovery_claim_path(binding, authorization)
    created_utc, created_shanghai = _timestamp_pair()
    with _audited_execution(
        _recovery_exact_path_policy(binding, claim),
        bootstrap=bootstrap,
    ):
        os.mkdir(claim, 0o700)
        _fsync_directory(claim.parent)
    # The mkdir above is the one-start linearization point.  A later failure
    # deliberately leaves this claim consumed rather than deleting it.
    started = {
        "schema_version": "p4.2a-v2-2-sealed-bundle-recovery-started-v1",
        "recovery_id": authorization.authorization_id,
        "authorization": authorization.authority_ref(binding.project_root).as_json(),
        "created_at_utc": created_utc,
        "created_at_shanghai": created_shanghai,
        "execution_head": live_anchor.execution_head,
        "execution_epoch": live_anchor.execution_epoch,
        "sealed_history_root_sha256": historical_anchor.history_root_sha256,
        "sealed_live_ledger_root_sha256": historical_anchor.live_ledger_root_sha256,
        "destination": binding.destination.as_posix(),
        "state": "STARTED",
        "authorized_bundle_recovery_starts": 1,
        "authorized_pipeline_starts": 0,
        "automatic_retry_count": 0,
    }
    with _audited_execution(
        _recovery_claim_policy(binding, claim),
        bootstrap=bootstrap,
    ):
        _write_exclusive(claim / "started.json", _canonical_json_bytes(started), mode=0o600)
        _fsync_directory(claim)
    return claim


def _create_recovery_temporary_authority(
    binding: ExecutionBinding,
    authorization: BundleRecoveryAuthorization,
    *,
    bootstrap: _BootstrapEvidence,
) -> Path:
    authority = _recovery_temporary_authority_path(binding, authorization)
    with _audited_execution(
        _recovery_exact_path_policy(binding, authority),
        bootstrap=bootstrap,
    ):
        os.mkdir(authority, 0o700)
        _fsync_directory(authority.parent)
    return authority


def _write_recovery_terminal(
    *,
    binding: ExecutionBinding,
    authorization: BundleRecoveryAuthorization,
    claim_root: Path,
    outcome: Literal["BUNDLE_RECOVERY_PUBLISHED", "FAILED_NO_AUTOMATIC_RETRY"],
    reached_stage: str,
    ledger_before_sha256: str,
    ledger_after_sha256: str,
    temporary_authority_absent: bool,
    published_bundle_sha256: str | None,
    published_tree_sha256: str | None,
    error: BaseException | None,
) -> JsonObject:
    if outcome == "BUNDLE_RECOVERY_PUBLISHED":
        if (
            error is not None
            or published_bundle_sha256 is None
            or published_tree_sha256 is None
            or not temporary_authority_absent
        ):
            raise RehearsalV22Error("successful recovery terminal lacks publication proof")
        error_value: JsonObject | None = None
    else:
        if (
            error is None
            or published_bundle_sha256 is not None
            or published_tree_sha256 is not None
        ):
            raise RehearsalV22Error("failed recovery terminal makes a false publication claim")
        error_value = {
            "exception_type": type(error).__name__,
            "message_sha256": _sha256(str(error).encode("utf-8")),
            "failing_stage": reached_stage,
        }
    completed_utc, completed_shanghai = _timestamp_pair()
    terminal: JsonObject = {
        "schema_version": "p4.2a-v2-2-sealed-bundle-recovery-terminal-v1",
        "recovery_id": authorization.authorization_id,
        "authorization": authorization.authority_ref(binding.project_root).as_json(),
        "completed_at_utc": completed_utc,
        "completed_at_shanghai": completed_shanghai,
        "outcome": outcome,
        "reached_stage": reached_stage,
        "sealed_ledger_before_sha256": ledger_before_sha256,
        "sealed_ledger_after_sha256": ledger_after_sha256,
        "destination": binding.destination.as_posix(),
        "published_bundle_sha256": published_bundle_sha256,
        "published_tree_sha256": published_tree_sha256,
        "temporary_authority_absent": temporary_authority_absent,
        "pipeline_starts": 0,
        "automatic_retry_count": 0,
        "error": error_value,
    }
    if set(terminal) != RECOVERY_TERMINAL_FIELDS:
        raise RehearsalV22Error("recovery terminal internal shape drifted")
    _write_exclusive(
        claim_root / "terminal.json",
        _canonical_json_bytes(terminal),
        mode=0o600,
    )
    _fsync_directory(claim_root)
    return terminal


def _execute_authorized_bundle_recovery(
    *,
    binding: ExecutionBinding,
    history: HistoryValidation,
    authorization: BundleRecoveryAuthorization,
    historical_anchor: HistoricalSelectedAnchor,
    live_anchor: LiveExecutionAnchor,
    run_a: _SealedPipelineReplay,
    run_b: _SealedPipelineReplay,
    ledger_snapshot: Mapping[str, str],
    bootstrap: _BootstrapEvidence,
    recovery_context: RecoveryExecutionCapability,
    validator_module: ModuleType,
    claim_root: Path,
    temporary_authority: Path,
) -> tuple[_BundleAssembly, JsonObject, JsonObject]:
    """Build and publish from sealed bytes; this function owns no replay call."""

    observed_binding = _validate_recovery_execution_capability(
        recovery_context,
        project_root=binding.project_root,
    )
    if observed_binding != binding:
        raise RehearsalV22Error("recovery execution binding differs from capability")
    before_digest = _sha256(_canonical_json_bytes(dict(ledger_snapshot)))
    candidate_directory: Path | None = None
    published = False
    reached_stage = "sealed_evidence_rehydrated"
    try:
        assembly = _build_bundle(
            binding=binding,
            history=history,
            run_a=run_a,
            run_b=run_b,
            historical_anchor=historical_anchor,
            live_anchor=live_anchor,
        )
        reached_stage = "bundle_assembled_from_sealed_evidence"
        candidate_directory = _stage_bundle(binding=binding, assembly=assembly)
        staged_tree_fingerprint = _tree_fingerprint(candidate_directory)
        if staged_tree_fingerprint == {".": "absent"}:
            raise RehearsalV22Error("recovery staged bundle fingerprint is absent")
        reached_stage = "staged_bundle_fsynced"
        with _borrow_recovery_validator_authority(
            recovery_context,
            validator_module=validator_module,
            bundle_path=candidate_directory / BUNDLE_FILENAME,
        ) as delegation:
            validate_recovered_bundle = getattr(
                validator_module,
                "validate_recovered_bundle",
                None,
            )
            if not callable(validate_recovered_bundle):
                raise RehearsalV22Error("passive recovered-bundle validator API is unavailable")
            validated = validate_recovered_bundle(
                project_root=binding.project_root,
                bundle_path=candidate_directory / BUNDLE_FILENAME,
                recovery_context=recovery_context,
                recovery_validator_delegation=delegation,
            )
        if _tree_fingerprint(candidate_directory) != staged_tree_fingerprint:
            raise RehearsalV22Error("passive validation changed the staged bundle tree")
        if validated != assembly.document:
            raise RehearsalV22Error("passive validator returned different recovered bundle")
        reached_stage = "passive_independent_validation_passed"
        _validate_recovery_execution_capability(
            recovery_context,
            project_root=binding.project_root,
        )
        if _tree_fingerprint(binding.ledger_root) != dict(ledger_snapshot):
            raise RehearsalV22Error("sealed ledger changed before recovery publication")
        if os.path.lexists(binding.destination):
            raise RehearsalV22Error("bundle destination appeared before recovery publication")
        if _tree_fingerprint(candidate_directory) != staged_tree_fingerprint:
            raise RehearsalV22Error("staged bundle changed immediately before publication")
        publication = _publish_candidate(binding, candidate_directory)
        candidate_directory = None
        published = True
        reached_stage = "bundle_recovery_published"
        published_payload = _regular_bytes(
            binding.destination / BUNDLE_FILENAME,
            "published recovered bundle",
        )
        if published_payload != assembly.bundle_payload:
            raise RehearsalV22Error("published recovered bundle bytes drifted")
        published_tree_fingerprint = _tree_fingerprint(binding.destination)
        if published_tree_fingerprint != staged_tree_fingerprint:
            raise RehearsalV22Error("published recovered bundle tree differs from frozen stage")
        after_snapshot = _tree_fingerprint(binding.ledger_root)
        if after_snapshot != dict(ledger_snapshot):
            raise RehearsalV22Error("sealed ledger changed during recovery publication")
        after_digest = _sha256(_canonical_json_bytes(after_snapshot))
        if os.path.lexists(temporary_authority):
            shutil.rmtree(temporary_authority)
            _fsync_directory(temporary_authority.parent)
        if os.path.lexists(temporary_authority):
            raise RehearsalV22Error("recovery temporary authority cleanup failed")
        terminal = _write_recovery_terminal(
            binding=binding,
            authorization=authorization,
            claim_root=claim_root,
            outcome="BUNDLE_RECOVERY_PUBLISHED",
            reached_stage=reached_stage,
            ledger_before_sha256=before_digest,
            ledger_after_sha256=after_digest,
            temporary_authority_absent=True,
            published_bundle_sha256=_sha256(published_payload),
            published_tree_sha256=_sha256(
                _canonical_json_bytes(published_tree_fingerprint)
            ),
            error=None,
        )
        return assembly, publication, terminal
    except BaseException as exc:
        if candidate_directory is not None and os.path.lexists(candidate_directory):
            shutil.rmtree(candidate_directory)
        if os.path.lexists(temporary_authority):
            shutil.rmtree(temporary_authority)
            _fsync_directory(temporary_authority.parent)
        # Once publication happened, absence of a terminal is an explicit
        # owner-reconciliation state; never relabel it as failure or retry it.
        if not published and not os.path.lexists(claim_root / "terminal.json"):
            after_snapshot = _tree_fingerprint(binding.ledger_root)
            _write_recovery_terminal(
                binding=binding,
                authorization=authorization,
                claim_root=claim_root,
                outcome="FAILED_NO_AUTOMATIC_RETRY",
                reached_stage=reached_stage,
                ledger_before_sha256=before_digest,
                ledger_after_sha256=_sha256(_canonical_json_bytes(after_snapshot)),
                temporary_authority_absent=not os.path.lexists(temporary_authority),
                published_bundle_sha256=None,
                published_tree_sha256=None,
                error=exc,
            )
        raise


def _execute_authorized_attempt(
    *,
    binding: ExecutionBinding,
    prior_history: HistoryValidation,
    action: ActionAuthorization,
    control: ControlSurface,
    bootstrap: _BootstrapEvidence,
    execution_context: ExecutionCapability,
    validator_module: ModuleType,
    policy: _AuditPolicy,
) -> tuple[_BundleAssembly, JsonObject, JsonObject]:
    authority = _TEMP_AUTHORITY.get()
    if authority is None:
        raise RehearsalV22Error("authorized attempt lacks temporary authority")
    try:
        authority_probes = _preallocation_authority_probes(
            binding=binding,
            bootstrap=bootstrap,
            execution_context=execution_context,
            action=action,
            validator_module=validator_module,
            policy=policy,
        )
    except BaseException as exc:
        raise RehearsalV22Error("preallocation authority probes failed") from exc
    try:
        atomic_probe = _atomic_no_replace_negative_probe(authority)
    except BaseException as exc:
        raise RehearsalV22Error("atomic publication probe failed") from exc
    candidate_directory: Path | None = None
    try:
        with SeriesLedger.open(
            binding,
            execution_context=execution_context,
            created_at_utc=FIXED_WALL_CLOCK_TEXT,
        ) as ledger:
            lease = ledger.allocate_attempt(
                action,
                created_at_utc=FIXED_WALL_CLOCK_TEXT,
            )

            def persist_pipeline_evidence(relative: str, payload: bytes) -> None:
                lease.persist_evidence(relative, payload)

            disposable_started_checkpoint: JsonObject | None = None
            if binding.mode == "DISPOSABLE_FULL_SHAPE_TEST":
                observed_disposable = _validate_disposable_capability(
                    execution_context,
                    project_root=binding.project_root,
                )
                if observed_disposable != binding:
                    raise RehearsalV22Error(
                        "disposable started checkpoint binding differs from capability"
                    )
                disposable_started_checkpoint = {
                    "schema_version": ("p4.2a-v2-2-disposable-started-checkpoint-v1"),
                    "mode": "DISPOSABLE_FULL_SHAPE_TEST",
                    "phase_before_signal": (
                        "started_and_parent_fsynced_before_any_evidence_or_pipeline"
                    ),
                    "signal": "SIGSTOP",
                    "signal_number": signal.SIGSTOP,
                    "registered_official_pause_enabled": False,
                    "resume_requires_external_SIGCONT": True,
                }
                os.kill(os.getpid(), signal.SIGSTOP)
                lease.persist_evidence(
                    "probes/disposable-started-checkpoint.json",
                    _canonical_json_bytes(disposable_started_checkpoint),
                )
            lease.persist_evidence(
                "probes/authority-forgery.json",
                _canonical_json_bytes(authority_probes),
            )
            lease.persist_evidence(
                "probes/atomic-publication.json",
                _canonical_json_bytes(atomic_probe),
            )
            ledger_probes = _ledger_create_only_probes(
                binding=binding,
                lease=lease,
                prior_history=prior_history,
            )
            lease.persist_evidence(
                "probes/create-only-ledger.json",
                _canonical_json_bytes(ledger_probes),
            )
            lease.persist_evidence(
                "probes/release-after-publication-plan.json",
                _canonical_json_bytes(
                    {
                        "schema_version": (
                            "p4.2a-v2-2-release-after-publication-plan-v1"
                        ),
                        "mode": binding.mode,
                        "phase": "before_candidate_and_terminal",
                        "disposable_same_validator_release_probe_required": (
                            binding.mode == "DISPOSABLE_FULL_SHAPE_TEST"
                        ),
                        "registered_official_release_probe_executed_by_attempt": False,
                        "result_bytes_must_not_be_backwritten_after_candidate": True,
                        "result_evidence_locations": [
                            "synthetic_git_review_request_and_receipt_history",
                            "canonical_cli_stdout_release_probe",
                        ],
                        "authorized_stages_after_receipt": [],
                        "heldout_evaluation_attempts_consumed": 0,
                    }
                ),
            )
            lease.reached_stage = "run_a"
            run_a = replay_selected_pipeline(
                binding=binding,
                implementation_commit=action.implementation_commit,
                run_label="run-a",
                execution_context=execution_context,
                validator_mode=True,
                evidence_sink=persist_pipeline_evidence,
            )
            lease.reached_stage = "run_b"
            run_b = replay_selected_pipeline(
                binding=binding,
                implementation_commit=action.implementation_commit,
                run_label="run-b",
                execution_context=execution_context,
                validator_mode=True,
                evidence_sink=persist_pipeline_evidence,
            )
            if dict(run_a.artifacts) != dict(run_b.artifacts):
                raise RehearsalV22Error("selected dual runs differ by bytes")
            observed_control = build_control_surface(
                binding.project_root,
                action.implementation_commit,
                require_current=True,
            )
            _validate_post_run_control_surface(control, observed_control)
            lease.reached_stage = "candidate_receipt"
            lease.write_candidate(
                run_a_root_sha256=_generic_merkle_root(
                    {relative: run_a.artifacts[logical] for logical, relative in ARTIFACT_INVENTORY}
                ),
                run_b_root_sha256=_generic_merkle_root(
                    {relative: run_b.artifacts[logical] for logical, relative in ARTIFACT_INVENTORY}
                ),
                control_surface_root_sha256=control.merkle_root_sha256,
                validated_at_utc=FIXED_WALL_CLOCK_TEXT,
            )
            lease.write_terminal(
                outcome="CANDIDATE_VALIDATED_AND_SELECTED",
                reached_stage="bundle_candidate_validated",
                completed_at_utc=FIXED_WALL_CLOCK_TEXT,
            )
            lease.reached_stage = "series_closed"
            history = validate_live_history(binding)
            historical_anchor = _historical_selected_anchor(binding, history)
            live_anchor = _ordinary_live_execution_anchor(binding, historical_anchor)
            assembly = _build_bundle(
                binding=binding,
                history=history,
                run_a=run_a,
                run_b=run_b,
                historical_anchor=historical_anchor,
                live_anchor=live_anchor,
            )
            candidate_directory = _stage_bundle(binding=binding, assembly=assembly)
            lease.reached_stage = "staged_bundle_fsynced"
            with _borrow_validator_authority(
                execution_context,
                validator_module=validator_module,
            ) as delegation:
                validate_bundle = getattr(validator_module, "validate_bundle", None)
                if not callable(validate_bundle):
                    raise RehearsalV22Error("independent validator API is unavailable")
                validated = validate_bundle(
                    project_root=binding.project_root,
                    bundle_path=candidate_directory / BUNDLE_FILENAME,
                    execution_context=execution_context,
                    validator_delegation=delegation,
                )
            if validated != assembly.document:
                raise RehearsalV22Error("independent validator returned different bundle bytes")
            lease.reached_stage = "independent_candidate_validation_passed"
            publication = _publish_candidate(binding, candidate_directory)
            candidate_directory = None
            lease.reached_stage = "bundle_published"
            release_probe = _run_disposable_release_probe(
                binding=binding,
                assembly=assembly,
                execution_context=execution_context,
                validator_module=validator_module,
            )
            lease.reached_stage = "postpublication_release_probe_complete"
            return assembly, publication, release_probe
    finally:
        if candidate_directory is not None and os.path.lexists(candidate_directory):
            shutil.rmtree(candidate_directory)


def _positive_ordinal(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected ordinal must be an integer") from exc
    if parsed < 1 or str(parsed) != value:
        raise argparse.ArgumentTypeError("expected ordinal must be canonical and positive")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--execute", action="store_true")
    operation.add_argument("--preflight-only", action="store_true")
    operation.add_argument("--recover-sealed-bundle", action="store_true")
    operation.add_argument("--consume-recovered-release", action="store_true")
    parser.add_argument("--attempt-authorization", type=Path)
    parser.add_argument("--bundle-recovery-authorization", type=Path)
    parser.add_argument("--expected-ordinal", type=_positive_ordinal)
    parser.add_argument("--implementation-epoch", type=_positive_ordinal)
    parser.add_argument("--implementation-commit")
    parser.add_argument("--owner-surface-authorization", type=Path)
    parser.add_argument("--independent-implementation-review", type=Path)
    return parser


def _normalize_cli_interrupt_handler() -> None:
    """Make SIGINT deterministic when a detached parent ignored it before exec."""

    signal.signal(signal.SIGINT, signal.default_int_handler)
    if signal.getsignal(signal.SIGINT) is not signal.default_int_handler:
        raise RehearsalV22Error("v2.2 CLI could not install the Python SIGINT handler")


def _run_cli() -> JsonObject:
    arguments = _parser().parse_args()
    project_root = _main_project_root()
    preflight_values = (
        arguments.implementation_epoch,
        arguments.implementation_commit,
        arguments.owner_surface_authorization,
        arguments.independent_implementation_review,
    )
    if arguments.preflight_only is True:
        if (
            arguments.execute is True
            or arguments.recover_sealed_bundle is True
            or arguments.consume_recovered_release is True
            or arguments.attempt_authorization is not None
            or arguments.bundle_recovery_authorization is not None
            or arguments.expected_ordinal is not None
            or any(value is None for value in preflight_values)
        ):
            raise RehearsalV22Error("v2.2 read-only preflight arguments are not exact")
        owner_surface_path = cast(Path, arguments.owner_surface_authorization).absolute()
        independent_review_path = cast(
            Path,
            arguments.independent_implementation_review,
        ).absolute()
        expected_argv = (
            (project_root / SHIM_RELATIVE).as_posix(),
            "--preflight-only",
            "--implementation-epoch",
            str(cast(int, arguments.implementation_epoch)),
            "--implementation-commit",
            cast(str, arguments.implementation_commit),
            "--owner-surface-authorization",
            owner_surface_path.as_posix(),
            "--independent-implementation-review",
            independent_review_path.as_posix(),
        )
        if tuple(sys.argv) != expected_argv:
            raise RehearsalV22Error("v2.2 read-only preflight argv is not exact")
        with (
            _bootstrap_evidence_scope(
                project_root=project_root,
                shim_path=project_root / SHIM_RELATIVE,
                argv=tuple(sys.argv),
                orig_argv=tuple(sys.orig_argv),
                environment=dict(os.environ),
            ) as bootstrap,
            _audited_execution(
                _read_only_preflight_policy(project_root),
                bootstrap=bootstrap,
            ),
        ):
            return _read_only_implementation_preflight(
                project_root,
                implementation_epoch=cast(int, arguments.implementation_epoch),
                implementation_commit=cast(str, arguments.implementation_commit),
                owner_surface_authorization_path=owner_surface_path,
                independent_review_path=independent_review_path,
            )
    if arguments.consume_recovered_release is True:
        if (
            arguments.execute is True
            or arguments.preflight_only is True
            or arguments.recover_sealed_bundle is True
            or arguments.attempt_authorization is not None
            or arguments.bundle_recovery_authorization is None
            or arguments.expected_ordinal is not None
            or any(value is not None for value in preflight_values)
        ):
            raise RehearsalV22Error(
                "v2.2 recovered-release consumption arguments are not exact"
            )
        recovery_path = cast(Path, arguments.bundle_recovery_authorization).absolute()
        expected_argv = (
            (project_root / SHIM_RELATIVE).as_posix(),
            "--consume-recovered-release",
            "--bundle-recovery-authorization",
            recovery_path.as_posix(),
        )
        if tuple(sys.argv) != expected_argv:
            raise RehearsalV22Error(
                "v2.2 recovered-release consumption argv is not exact"
            )
        binding = _derive_binding_unchecked(
            project_root,
            action_authorization_path=recovery_path,
        )
        receipt_path = project_root / RELEASE_RELATIVE
        with (
            _bootstrap_evidence_scope(
                project_root=project_root,
                shim_path=binding.shim_path,
                argv=tuple(sys.argv),
                orig_argv=tuple(sys.orig_argv),
                environment=dict(os.environ),
            ) as bootstrap,
            _audited_execution(
                _read_only_preflight_policy(project_root),
                bootstrap=bootstrap,
            ),
        ):
            from scripts import (
                validate_p4_2a_v2_2_heldout_rehearsal_bundle as validator_module,
            )

            with _shared_series_lock(binding):
                return consume_recovered_release_authorization(
                    binding=binding,
                    validator_module=validator_module,
                    recovery_authorization_path=recovery_path,
                    receipt_path=receipt_path,
                )
    if arguments.recover_sealed_bundle is True:
        if (
            arguments.execute is True
            or arguments.preflight_only is True
            or arguments.consume_recovered_release is True
            or arguments.attempt_authorization is not None
            or arguments.bundle_recovery_authorization is None
            or arguments.expected_ordinal is not None
            or any(value is not None for value in preflight_values)
        ):
            raise RehearsalV22Error("v2.2 sealed-bundle recovery arguments are not exact")
        recovery_path = cast(Path, arguments.bundle_recovery_authorization).absolute()
        binding = _derive_binding_unchecked(
            project_root,
            action_authorization_path=recovery_path,
        )
        with _bootstrap_evidence_scope(
            project_root=project_root,
            shim_path=binding.shim_path,
            argv=tuple(sys.argv),
            orig_argv=tuple(sys.orig_argv),
            environment=dict(os.environ),
        ) as bootstrap:
            with _audited_execution(
                _read_only_preflight_policy(project_root),
                bootstrap=bootstrap,
            ):
                from scripts import (
                    validate_p4_2a_v2_2_heldout_rehearsal_bundle as validator_module,
                )

                screened = _preflight_bundle_recovery(binding)
            with _shared_series_lock(binding):
                with _audited_execution(
                    _read_only_preflight_policy(project_root),
                    bootstrap=bootstrap,
                ):
                    protected = _preflight_bundle_recovery(binding)
                if screened != protected:
                    raise RehearsalV22Error(
                        "recovery evidence changed between screening and shared-lock snapshot"
                    )
                (
                    history,
                    recovery_authorization,
                    historical_anchor,
                    live_anchor,
                    sealed_run_a,
                    sealed_run_b,
                    ledger_snapshot,
                ) = protected
                claim_root: Path | None = None
                temporary_authority: Path | None = None
                authority_token: contextvars.Token[Path | None] | None = None
                try:
                    claim_root = _recovery_claim_path(binding, recovery_authorization)
                    _create_recovery_claim(
                        binding,
                        recovery_authorization,
                        historical_anchor,
                        live_anchor,
                        bootstrap=bootstrap,
                    )
                    temporary_authority = _recovery_temporary_authority_path(
                        binding,
                        recovery_authorization,
                    )
                    _create_recovery_temporary_authority(
                        binding,
                        recovery_authorization,
                        bootstrap=bootstrap,
                    )
                    authority_token = _TEMP_AUTHORITY.set(temporary_authority)
                    recovery_policy = _recovery_execution_policy(
                        binding,
                        claim_root=claim_root,
                        temporary_authority=temporary_authority,
                    )
                    with (
                        _audited_execution(recovery_policy, bootstrap=bootstrap),
                        _recovery_execution_capability_scope(
                            binding=binding,
                            bootstrap=bootstrap,
                            authorization=recovery_authorization,
                            historical_anchor=historical_anchor,
                            live_anchor=live_anchor,
                            claim_root=claim_root,
                            temporary_authority=temporary_authority,
                        ) as recovery_context,
                    ):
                        assembly, publication, terminal = (
                            _execute_authorized_bundle_recovery(
                                binding=binding,
                                history=history,
                                authorization=recovery_authorization,
                                historical_anchor=historical_anchor,
                                live_anchor=live_anchor,
                                run_a=sealed_run_a,
                                run_b=sealed_run_b,
                                ledger_snapshot=ledger_snapshot,
                                bootstrap=bootstrap,
                                recovery_context=recovery_context,
                                validator_module=validator_module,
                                claim_root=claim_root,
                                temporary_authority=temporary_authority,
                            )
                        )
                except BaseException as exc:
                    if (
                        temporary_authority is not None
                        and os.path.lexists(temporary_authority)
                    ):
                        with _audited_execution(
                            _recovery_execution_policy(
                                binding,
                                claim_root=cast(Path, claim_root),
                                temporary_authority=temporary_authority,
                            ),
                            bootstrap=bootstrap,
                        ):
                            shutil.rmtree(temporary_authority)
                            _fsync_directory(temporary_authority.parent)
                    if (
                        claim_root is not None
                        and os.path.lexists(claim_root / "started.json")
                        and not os.path.lexists(claim_root / "terminal.json")
                        and not os.path.lexists(binding.destination)
                    ):
                        before_digest = _sha256(
                            _canonical_json_bytes(dict(ledger_snapshot))
                        )
                        after_snapshot = _tree_fingerprint(binding.ledger_root)
                        with _audited_execution(
                            _recovery_claim_policy(binding, claim_root),
                            bootstrap=bootstrap,
                        ):
                            _write_recovery_terminal(
                                binding=binding,
                                authorization=recovery_authorization,
                                claim_root=claim_root,
                                outcome="FAILED_NO_AUTOMATIC_RETRY",
                                reached_stage="recovery_setup_or_execution",
                                ledger_before_sha256=before_digest,
                                ledger_after_sha256=_sha256(
                                    _canonical_json_bytes(after_snapshot)
                                ),
                                temporary_authority_absent=(
                                    temporary_authority is None
                                    or not os.path.lexists(temporary_authority)
                                ),
                                published_bundle_sha256=None,
                                published_tree_sha256=None,
                                error=exc,
                            )
                    raise
                finally:
                    if authority_token is not None:
                        _TEMP_AUTHORITY.reset(authority_token)
        if temporary_authority is None or os.path.lexists(temporary_authority):
            raise RehearsalV22Error("recovery temporary authority remains after completion")
        return {
            "schema_version": "p4.2a-v2-2-sealed-bundle-recovery-result-v1",
            "status": "PASS_REHEARSAL_V2_2_AWAITING_OWNER_REVIEW",
            "mode": binding.mode,
            "selected_attempt_ordinal": history.selected_attempt_ordinal,
            "selected_implementation_epoch": historical_anchor.selected_epoch,
            "execution_implementation_epoch": live_anchor.execution_epoch,
            "bundle_path": (binding.destination / BUNDLE_FILENAME).as_posix(),
            "bundle_sha256": _sha256(assembly.bundle_payload),
            "bundle_root_sha256": assembly.bundle_root_sha256,
            "recovery_claim_path": cast(Path, claim_root).as_posix(),
            "recovery_terminal": terminal,
            "publication": publication,
            "bundle_recovery_starts": 1,
            "pipeline_starts": 0,
            "active_pipeline_capability_issued": False,
            "sealed_rehydrate_only": True,
            "automatic_retry_count": 0,
            "heldout_evaluation_attempts_consumed": 0,
        }
    if (
        arguments.execute is not True
        or arguments.recover_sealed_bundle is True
        or arguments.consume_recovered_release is True
        or arguments.attempt_authorization is None
        or arguments.bundle_recovery_authorization is not None
        or arguments.expected_ordinal is None
        or any(value is not None for value in preflight_values)
    ):
        raise RehearsalV22Error("v2.2 execution arguments are not exact")
    action_path = cast(Path, arguments.attempt_authorization).absolute()
    binding = _derive_binding_unchecked(
        project_root,
        action_authorization_path=action_path,
    )
    with _bootstrap_evidence_scope(
        project_root=project_root,
        shim_path=binding.shim_path,
        argv=tuple(sys.argv),
        orig_argv=tuple(sys.orig_argv),
        environment=dict(os.environ),
    ) as bootstrap:
        with _audited_execution(
            _preflight_policy(binding),
            bootstrap=bootstrap,
        ):
            prior_history, action, control = _preflight_action(
                binding,
                expected_ordinal=cast(int, arguments.expected_ordinal),
            )
        authority = _create_temporary_authority(
            binding,
            action,
            bootstrap=bootstrap,
        )
        authority_token = _TEMP_AUTHORITY.set(authority)
        try:
            policy = _execution_policy(binding, authority)
            with _audited_execution(policy, bootstrap=bootstrap):
                try:
                    real_fingerprints = (
                        _real_path_fingerprints()
                        if binding.mode == "DISPOSABLE_FULL_SHAPE_TEST"
                        else None
                    )
                    boundary_ids = (
                        _fake_boundary_ids()
                        if binding.mode == "DISPOSABLE_FULL_SHAPE_TEST"
                        else None
                    )
                    with _execution_capability_scope(
                        binding=binding,
                        bootstrap=bootstrap,
                        action_authorization=action,
                        real_path_fingerprints=real_fingerprints,
                        boundary_ids=boundary_ids,
                    ) as capability:
                        from scripts import (
                            validate_p4_2a_v2_2_heldout_rehearsal_bundle as validator_module,
                        )

                        assembly, publication, release_probe = _execute_authorized_attempt(
                            binding=binding,
                            prior_history=prior_history,
                            action=action,
                            control=control,
                            bootstrap=bootstrap,
                            execution_context=capability,
                            validator_module=validator_module,
                            policy=policy,
                        )
                finally:
                    if os.path.lexists(authority):
                        shutil.rmtree(authority)
                        _fsync_directory(authority.parent)
        finally:
            _TEMP_AUTHORITY.reset(authority_token)
        if os.path.lexists(authority):
            raise RehearsalV22Error("temporary authority cleanup did not complete")
        return {
            "schema_version": "p4.2a-v2-2-rehearsal-execution-result-v1",
            "status": "PASS_REHEARSAL_V2_2_AWAITING_OWNER_REVIEW",
            "mode": binding.mode,
            "attempt_ordinal": action.ordinal,
            "bundle_path": (binding.destination / BUNDLE_FILENAME).as_posix(),
            "bundle_sha256": _sha256(assembly.bundle_payload),
            "bundle_root_sha256": assembly.bundle_root_sha256,
            "publication": publication,
            "release_probe": release_probe,
            "automatic_retry_count": 0,
            "heldout_evaluation_attempts_consumed": 0,
        }


def cli_main() -> int:
    """The sole package entry called by the state-free exact-OS shim."""

    try:
        _normalize_cli_interrupt_handler()
        result = _run_cli()
    except SystemExit:
        raise
    except BaseException as exc:
        sys.stderr.buffer.write(
            _canonical_json_bytes(
                {
                    "schema_version": "p4.2a-v2-2-rehearsal-execution-error-v1",
                    "status": "FAILED_NO_AUTOMATIC_RETRY",
                    "exception_type": type(exc).__name__,
                    "message_sha256": _sha256(str(exc).encode("utf-8")),
                }
            )
        )
        return 1
    sys.stdout.buffer.write(_canonical_json_bytes(result))
    return 0
