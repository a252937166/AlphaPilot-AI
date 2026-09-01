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
import builtins
import contextvars
import copy
import ctypes
import errno
import fcntl
import gc
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
from contextlib import contextmanager, suppress
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
AuthorityCensusRole = Literal[
    "PINNED_SOURCE",
    "PINNED_LANDING_PROJECTION",
    "PINNED_SOURCE_WITH_DESCENDANT_GRAPH",
    "DISCOVER_SOURCE_AFTER_PROJECTIONS",
]
FixedCarryForwardBinding = tuple[str, str, str, AuthorityCensusRole, str | None, int]


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
    mirror_write_phase: Literal["initialize", "staging", "publish", "receipt"] | None = None
    mirror_snapshot_root: Path | None = None
    primary_receipt_root: Path | None = None
    secondary_receipt_root: Path | None = None
    mirror_staging_root: Path | None = None
    mirror_receipt_paths: tuple[Path, Path] | None = None
    mirror_publish_paths: tuple[Path, Path] | None = None
    recovery_rename_pairs: tuple[tuple[Path, Path], ...] = ()


@dataclass(frozen=True)
class _MirrorPhaseCapability:
    _nonce: object
    outer_policy_id: int
    issued_policy_id: int
    phase: Literal["initialize", "staging", "publish", "receipt"]


@dataclass(frozen=True)
class _NativeRenameCapability:
    _nonce: object
    policy_id: int
    source: Path
    destination: Path
    symbol: Literal["renamex_np", "renameatx_np"]


@dataclass(frozen=True)
class _RecoveryRenameCapability:
    _nonce: object
    policy_id: int
    source: Path
    destination: Path


@dataclass(frozen=True)
class _OpenAtWriteCapability:
    _nonce: object
    policy_id: int
    parent_descriptor: int
    parent_identity: tuple[int, int]
    entry_name: str
    absolute_path: Path
    flags: int
    mode: int
    payload_bytes: int
    payload_sha256: str


@dataclass(frozen=True)
class _MirrorCommitCapability:
    _nonce: object
    ledger_id: int
    ordinal: int
    history_root_sha256: str
    reason: Literal["TERMINAL_SEAL", "CONTINUATION_FREEZE"]


_AUDIT_POLICY: contextvars.ContextVar[_AuditPolicy | None] = contextvars.ContextVar(
    "p4_2a_v2_2_rehearsal_audit_policy",
    default=None,
)
_TEMP_AUTHORITY: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "p4_2a_v2_2_rehearsal_temp_authority",
    default=None,
)
_MIRROR_PHASE_CAPABILITY: contextvars.ContextVar[_MirrorPhaseCapability | None] = (
    contextvars.ContextVar(
        "p4_2a_v2_2_mirror_phase_capability",
        default=None,
    )
)
_NATIVE_RENAME_CAPABILITY: contextvars.ContextVar[_NativeRenameCapability | None] = (
    contextvars.ContextVar(
        "p4_2a_v2_2_native_rename_capability",
        default=None,
    )
)
_RECOVERY_RENAME_CAPABILITY: contextvars.ContextVar[_RecoveryRenameCapability | None] = (
    contextvars.ContextVar(
        "p4_2a_v2_2_recovery_rename_capability",
        default=None,
    )
)
_OPENAT_WRITE_CAPABILITY: contextvars.ContextVar[_OpenAtWriteCapability | None] = (
    contextvars.ContextVar(
        "p4_2a_v2_2_openat_write_capability",
        default=None,
    )
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
            raise RehearsalV22Error("v2.2 mutation without dir_fd requires an absolute path")
        lexical = _lexical_path(value)
        raw_path = Path(os.fsdecode(cast(str | bytes | os.PathLike[str], value)))
        if lexical is None or not raw_path.is_absolute():
            raise RehearsalV22Error("v2.2 mutation without dir_fd requires an absolute path")
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


def _mirror_create_is_authorized(
    path: Path,
    *,
    directory: bool,
    policy: _AuditPolicy,
) -> bool:
    phase = policy.mirror_write_phase
    roots = (
        policy.mirror_snapshot_root,
        policy.primary_receipt_root,
        policy.secondary_receipt_root,
    )
    if phase is None or any(root is None for root in roots):
        return False
    mirror_snapshot_root, primary_receipt_root, secondary_receipt_root = cast(
        tuple[Path, Path, Path], roots
    )
    if phase == "initialize":
        return directory and path in {
            mirror_snapshot_root,
            primary_receipt_root,
            secondary_receipt_root,
        }
    if phase == "staging":
        staging = policy.mirror_staging_root
        if staging is None or not (path == staging or path.is_relative_to(staging)):
            return False
        return directory or path != staging
    if phase == "receipt":
        receipt_paths = policy.mirror_receipt_paths
        return not directory and receipt_paths is not None and path in receipt_paths
    return False


def _create_only_write_is_authorized(
    path: Path,
    *,
    directory: bool,
    policy: _AuditPolicy,
) -> bool:
    return _ledger_create_is_authorized(
        path,
        directory=directory,
        policy=policy,
    ) or _mirror_create_is_authorized(
        path,
        directory=directory,
        policy=policy,
    )


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


def _build_openat_write_state() -> tuple[Any, Any]:
    nonce = object()
    registry: tuple[_OpenAtWriteCapability, ...] = ()
    capability_context = _OPENAT_WRITE_CAPABILITY
    capability_type = _OpenAtWriteCapability
    audit_policy_context = _AUDIT_POLICY
    audit_policy_checker = _audit_policy_is_issued
    audit_hook = _process_audit_hook
    import_guard_checker = _import_guard_is_active
    audited_directory_from_fd = _audited_directory_from_fd
    require_write_path = _require_audited_write_path
    python_id = id
    python_type = type
    python_str = str
    python_int = int
    python_str = str
    python_len = len
    path_type = type(Path("."))
    os_open = os.open
    os_close = os.close
    os_fstat = os.fstat
    os_fsdecode = os.fsdecode
    lexists = os.path.lexists
    fcntl_call = fcntl.fcntl
    f_getpath = fcntl.F_GETPATH
    pthread_sigmask = signal.pthread_sigmask
    valid_signals = signal.valid_signals
    signal_block = signal.SIG_BLOCK
    signal_setmask = signal.SIG_SETMASK
    signals_type = signal.Signals
    sigkill = signal.SIGKILL
    sigstop = signal.SIGSTOP
    sys_gettrace = sys.gettrace
    sys_getprofile = sys.getprofile
    monitoring_module = sys.monitoring  # type: ignore[attr-defined]  # Python 3.12
    monitoring_get_tool = monitoring_module.get_tool
    monitoring_get_events = monitoring_module.get_events
    monitoring_use_tool_id = monitoring_module.use_tool_id
    monitoring_free_tool_id = monitoring_module.free_tool_id
    monitoring_set_events = monitoring_module.set_events
    monitoring_set_local_events = monitoring_module.set_local_events
    monitoring_register_callback = monitoring_module.register_callback
    monitoring_restart_events = monitoring_module.restart_events
    monitoring_tool_ids = (0, 1, 2, 3, 4, 5)
    gc_isenabled = gc.isenabled
    gc_disable = gc.disable
    gc_enable = gc.enable
    registered_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    registered_mode = 0o600

    def require_runtime_identity() -> None:
        if (
            _OPENAT_WRITE_CAPABILITY is not capability_context
            or _OpenAtWriteCapability is not capability_type
            or _AUDIT_POLICY is not audit_policy_context
            or _audit_policy_is_issued is not audit_policy_checker
            or _process_audit_hook is not audit_hook
            or _import_guard_is_active is not import_guard_checker
            or _audited_directory_from_fd is not audited_directory_from_fd
            or _require_audited_write_path is not require_write_path
            or globals().get("_openat_write_capability_path") is not capability_is_issued
            or globals().get("_open_exclusive_at_issued") is not open_exclusive
            or os.open is not os_open
            or os.close is not os_close
            or os.fstat is not os_fstat
            or os.fsdecode is not os_fsdecode
            or os.path.lexists is not lexists
            or fcntl.fcntl is not fcntl_call
            or f_getpath != fcntl.F_GETPATH
            or signal.pthread_sigmask is not pthread_sigmask
            or signal.valid_signals is not valid_signals
            or signal.SIG_BLOCK is not signal_block
            or signal.SIG_SETMASK is not signal_setmask
            or signal.Signals is not signals_type
            or signal.SIGKILL is not sigkill
            or signal.SIGSTOP is not sigstop
            or sys.gettrace is not sys_gettrace
            or sys.getprofile is not sys_getprofile
            or sys.monitoring is not monitoring_module  # type: ignore[attr-defined]
            or monitoring_module.get_tool is not monitoring_get_tool
            or monitoring_module.get_events is not monitoring_get_events
            or monitoring_module.use_tool_id is not monitoring_use_tool_id
            or monitoring_module.free_tool_id is not monitoring_free_tool_id
            or monitoring_module.set_events is not monitoring_set_events
            or monitoring_module.set_local_events is not monitoring_set_local_events
            or monitoring_module.register_callback is not monitoring_register_callback
            or monitoring_module.restart_events is not monitoring_restart_events
            or gc.isenabled is not gc_isenabled
            or gc.disable is not gc_disable
            or gc.enable is not gc_enable
            or sys_gettrace() is not None
            or sys_getprofile() is not None
        ):
            raise RehearsalV22Error("openat runtime authority identity drifted")

    def disable_runtime_callbacks() -> bool:
        require_runtime_identity()
        for tool_id in monitoring_tool_ids:
            events = monitoring_get_events(tool_id)
            if (
                monitoring_get_tool(tool_id) is not None
                or python_type(events) is not python_int
                or events != 0
            ):
                raise RehearsalV22Error("openat refuses active Python monitoring callbacks")
        was_enabled = gc_isenabled()
        if was_enabled:
            gc_disable()
        if gc_isenabled():
            raise RehearsalV22Error("openat could not disable cyclic GC callbacks")
        return was_enabled

    def capability_is_issued(
        capability: _OpenAtWriteCapability | None,
        *,
        policy: _AuditPolicy,
        raw_path: object,
        raw_flags: object,
    ) -> Path | None:
        if (
            capability is None
            or python_type(capability) is not capability_type
            or capability._nonce is not nonce
            or capability.policy_id != python_id(policy)
            or python_type(raw_path) is not python_str
            or raw_path != capability.entry_name
            or python_type(raw_flags) is not python_int
            or raw_flags != capability.flags
            or python_type(capability.parent_descriptor) is not python_int
            or python_type(capability.entry_name) is not python_str
            or python_type(capability.absolute_path) is not path_type
            or python_type(capability.flags) is not python_int
            or capability.flags != registered_flags
            or python_type(capability.mode) is not python_int
            or capability.mode != registered_mode
            or python_type(capability.payload_bytes) is not python_int
            or capability.payload_bytes < 0
            or python_type(capability.payload_sha256) is not python_str
            or python_len(capability.payload_sha256) != 64
        ):
            return None
        for character in capability.payload_sha256:
            if character not in "0123456789abcdef":
                return None
        for record in registry:
            if record is capability:
                return capability.absolute_path
        return None

    def open_exclusive(
        policy: _AuditPolicy,
        *,
        parent_descriptor: int,
        entry_name: str,
        absolute_path: Path,
        flags: int,
        mode: int,
        payload_bytes: int,
        payload_sha256: str,
    ) -> int:
        nonlocal registry
        require_runtime_identity()
        invalid_payload_sha = False
        if python_type(payload_sha256) is python_str:
            for character in payload_sha256:
                if character not in "0123456789abcdef":
                    invalid_payload_sha = True
                    break
        if (
            audit_policy_context.get() is not policy
            or not audit_policy_checker(policy)
            or import_guard_checker()
            or python_type(parent_descriptor) is not python_int
            or parent_descriptor < 0
            or python_type(entry_name) is not python_str
            or not entry_name
            or entry_name in {".", ".."}
            or "/" in entry_name
            or python_type(absolute_path) is not path_type
            or python_type(flags) is not python_int
            or flags != registered_flags
            or python_type(mode) is not python_int
            or mode != registered_mode
            or python_type(payload_bytes) is not python_int
            or audited_directory_from_fd(parent_descriptor) / entry_name != absolute_path
            or require_write_path(absolute_path, policy) != absolute_path
            or payload_bytes < 0
            or python_type(payload_sha256) is not python_str
            or python_len(payload_sha256) != 64
            or invalid_payload_sha
        ):
            raise RehearsalV22Error("openat write lacks exact issued parent authority")
        parent_metadata = os_fstat(parent_descriptor)
        capability = _OpenAtWriteCapability(
            _nonce=nonce,
            policy_id=python_id(policy),
            parent_descriptor=parent_descriptor,
            parent_identity=(parent_metadata.st_dev, parent_metadata.st_ino),
            entry_name=entry_name,
            absolute_path=absolute_path,
            flags=flags,
            mode=mode,
            payload_bytes=payload_bytes,
            payload_sha256=payload_sha256,
        )
        if registry or capability_context.get() is not None:
            raise RehearsalV22Error("openat write capability is already active")
        blocked = {item for item in valid_signals() if item not in {sigkill, sigstop}}
        gc_was_enabled = disable_runtime_callbacks()
        try:
            prior_signal_mask = pthread_sigmask(signal_block, blocked)
            prior_registry = registry
            token: contextvars.Token[_OpenAtWriteCapability | None] | None = None
            try:
                registry = (*prior_registry, capability)
                try:
                    token = capability_context.set(capability)
                    descriptor = os_open(
                        entry_name,
                        flags,
                        mode,
                        dir_fd=parent_descriptor,
                    )
                finally:
                    registry = prior_registry
            finally:
                try:
                    if token is not None:
                        capability_context.reset(token)
                finally:
                    pthread_sigmask(signal_setmask, prior_signal_mask)
        finally:
            if gc_was_enabled:
                gc_enable()
        require_runtime_identity()
        if gc_isenabled() is not gc_was_enabled:
            raise RehearsalV22Error("openat cyclic GC state was not restored")
        try:
            opened = os_fstat(descriptor)
            parent_after = os_fstat(parent_descriptor)
            raw = fcntl_call(descriptor, f_getpath, b"\0" * 1024)
            terminator = raw.find(b"\0")
            descriptor_path = Path(os_fsdecode(raw[:terminator]))
            if (
                terminator <= 0
                or descriptor_path.absolute() != absolute_path.absolute()
                or (parent_after.st_dev, parent_after.st_ino) != capability.parent_identity
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or stat.S_IMODE(opened.st_mode) != mode
                or opened.st_size != 0
                or fcntl_call(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_WRONLY
                or fcntl_call(descriptor, fcntl.F_GETFL) & os.O_APPEND
                or fcntl_call(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC == 0
            ):
                raise RehearsalV22Error("openat descriptor escaped its identity-bound target")
        except BaseException:
            os_close(descriptor)
            raise
        return descriptor

    return capability_is_issued, open_exclusive


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
        recursive_full_tree = (
            len(operation) == 6
            and operation[1:4] == ("-r", "-z", "--full-tree")
            and _lower_hex(operation[4], 40)
            and operation[5] == "--"
        )
        if recursive_full_tree:
            return policy.subprocess_mode in {"git-read", "synthetic-git"}
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
        return relative == operation[5] and policy.subprocess_mode in {"git-read", "synthetic-git"}
    read_only = operation[0] in {
        "cat-file",
        "diff",
        "for-each-ref",
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
    if event in {"sys.addaudithook", "sys.setprofile", "sys.settrace"} or event.startswith(
        "sys.monitoring."
    ):
        raise RehearsalV22Error(
            "v2.2 process forbids installing another runtime callback after bootstrap"
        )
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
        if _OPENAT_WRITE_CAPABILITY.get() is not None:
            try:
                path, mode, flags = arguments
            except ValueError as exc:
                raise RehearsalV22Error("v2.2 openat audit event shape drifted") from exc
            issued_openat_path = _openat_write_capability_path(
                _OPENAT_WRITE_CAPABILITY.get(),
                policy=policy,
                raw_path=path,
                raw_flags=flags,
            )
            if issued_openat_path is None or mode is not None:
                raise RehearsalV22Error("v2.2 openat write lacks exact audit authority")
            return
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
                    or not _create_only_write_is_authorized(
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
            or not _create_only_write_is_authorized(
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
    if event in {"ctypes.dlopen", "ctypes.dlsym"}:
        native_capability = _NATIVE_RENAME_CAPABILITY.get()
        exact_symbol = (event == "ctypes.dlopen" and arguments == (None,)) or (
            event == "ctypes.dlsym"
            and arguments != ()
            and native_capability is not None
            and arguments[-1] == native_capability.symbol
        )
        if not exact_symbol or not _native_rename_capability_is_issued(
            native_capability,
            policy=policy,
        ):
            raise RehearsalV22Error("v2.2 native symbol access lacks rename authority")
        return
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
        for record in policy_registry:  # noqa: SIM110 - audit path avoids builtins.any
            if record is policy:
                return True
        return False

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
            mirror_dynamic_changed = (
                policy.mirror_write_phase != current.mirror_write_phase
                or policy.mirror_staging_root != current.mirror_staging_root
                or policy.mirror_receipt_paths != current.mirror_receipt_paths
                or policy.mirror_publish_paths != current.mirror_publish_paths
            )
            if (
                policy.project_root != current.project_root
                or policy.write_roots != current.write_roots
                or policy.exact_write_paths != current.exact_write_paths
                or policy.create_only_roots != current.create_only_roots
                or policy.sqlite_roots != current.sqlite_roots
                or policy.git_roots != current.git_roots
                or policy.subprocess_mode != current.subprocess_mode
                or policy.synthetic_git_root != current.synthetic_git_root
                or policy.mirror_snapshot_root != current.mirror_snapshot_root
                or policy.primary_receipt_root != current.primary_receipt_root
                or policy.secondary_receipt_root != current.secondary_receipt_root
                or policy.recovery_rename_pairs != current.recovery_rename_pairs
                or (
                    mirror_dynamic_changed
                    and not _mirror_phase_capability_is_issued(
                        _MIRROR_PHASE_CAPABILITY.get(),
                        outer=current,
                        issued=policy,
                    )
                )
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

(
    _openat_write_capability_path,
    _open_exclusive_at_issued,
) = _build_openat_write_state()


def _build_mirror_phase_state() -> tuple[Any, Any]:
    nonce = object()
    capabilities: tuple[_MirrorPhaseCapability, ...] = ()
    sequences: tuple[object, ...] = ()

    def capability_is_issued(
        capability: _MirrorPhaseCapability | None,
        *,
        outer: _AuditPolicy | None,
        issued: _AuditPolicy,
    ) -> bool:
        return bool(
            capability is not None
            and capability._nonce is nonce
            and (outer is None or capability.outer_policy_id == id(outer))
            and capability.issued_policy_id == id(issued)
            and capability.phase == issued.mirror_write_phase
            and any(record is capability for record in capabilities)
        )

    @contextmanager
    def mirror_write_sequence(
        ledger: SeriesLedger,
        history: HistoryValidation,
        *,
        mirror_commit_capability: _MirrorCommitCapability,
        staging: Path,
        snapshot: Path,
        receipt_paths: tuple[Path, Path],
        initialize_roots: bool,
    ) -> Iterator[Callable[[str], Any]]:
        nonlocal capabilities, sequences
        _validate_series_lock_held(ledger)
        _consume_mirror_commit_capability(
            mirror_commit_capability,
            ledger=ledger,
            history=history,
        )
        binding = ledger.binding
        outer = _AUDIT_POLICY.get()
        if (
            outer is None
            or not _audit_policy_is_issued(outer)
            or history.binding != binding
            or not history.records
            or history.live_ledger_root_sha256 is None
            or outer.ledger_root != binding.ledger_root
            or outer.mirror_snapshot_root != binding.secondary_snapshot_root
            or outer.primary_receipt_root != binding.primary_receipt_root
            or outer.secondary_receipt_root != binding.secondary_receipt_root
        ):
            raise RehearsalV22Error("mirror sequence lacks held-lock live-history authority")
        ordinal = len(history.records)
        live_root = history.live_ledger_root_sha256
        expected_snapshot = binding.secondary_snapshot_root / _mirror_snapshot_name(
            ordinal, live_root
        )
        expected_receipt_name = _mirror_receipt_filename(ordinal, live_root)
        expected_receipts = (
            binding.primary_receipt_root / expected_receipt_name,
            binding.secondary_receipt_root / expected_receipt_name,
        )
        if (
            snapshot != expected_snapshot
            or receipt_paths != expected_receipts
            or staging.parent != binding.secondary_snapshot_root
            or not staging.name.startswith(f".staging-{expected_snapshot.name}-")
            or any(os.path.lexists(path) for path in (staging, snapshot, *receipt_paths))
        ):
            raise RehearsalV22Error("mirror sequence paths differ from the live ordinal")
        last_record = history.records[-1]
        expected_receipt_payload: bytes | None = None
        expected_phases = (
            ("initialize", "staging", "publish", "receipt")
            if initialize_roots
            else ("staging", "publish", "receipt")
        )
        sequence_token = object()
        if sequences:
            raise RehearsalV22Error("a mirror write sequence is already active")
        sequences = (*sequences, sequence_token)
        next_index = 0

        @contextmanager
        def phase_scope(raw_phase: str) -> Iterator[None]:
            nonlocal capabilities, expected_receipt_payload, next_index
            if (
                sequence_token not in sequences
                or next_index >= len(expected_phases)
                or raw_phase != expected_phases[next_index]
            ):
                raise RehearsalV22Error("mirror phase is forged, repeated, or reordered")
            phase = cast(
                Literal["initialize", "staging", "publish", "receipt"],
                raw_phase,
            )
            issued = replace(
                outer,
                mirror_write_phase=phase,
                mirror_staging_root=(staging if phase in {"staging", "publish"} else None),
                mirror_receipt_paths=(receipt_paths if phase == "receipt" else None),
                mirror_publish_paths=((staging, snapshot) if phase == "publish" else None),
            )
            capability = _MirrorPhaseCapability(
                _nonce=nonce,
                outer_policy_id=id(outer),
                issued_policy_id=id(issued),
                phase=phase,
            )
            capabilities = (*capabilities, capability)
            capability_token = _MIRROR_PHASE_CAPABILITY.set(capability)
            completed = False
            try:
                with _audited_execution(issued):
                    yield
                if phase == "initialize":
                    for root, label in (
                        (binding.secondary_snapshot_root, "snapshot root"),
                        (binding.primary_receipt_root, "primary receipt root"),
                        (binding.secondary_receipt_root, "secondary receipt root"),
                    ):
                        _registered_storage_directory(root, f"mirror {label}")
                        if any(root.iterdir()):
                            raise RehearsalV22Error(
                                "mirror initialization created an unexpected member"
                            )
                elif phase == "staging":
                    _registered_storage_directory(staging, "capability-bound staged mirror")
                elif phase == "publish":
                    if os.path.lexists(staging):
                        raise RehearsalV22Error("mirror publish phase postcondition drifted")
                    _registered_storage_directory(snapshot, "capability-bound published mirror")
                else:
                    paired = tuple(
                        _regular_bytes(path, "capability-bound mirror receipt")
                        for path in receipt_paths
                    )
                    if paired[0] != paired[1]:
                        raise RehearsalV22Error("mirror receipt phase postcondition drifted")
                    receipt = _object(
                        strict_json_loads(
                            paired[0], source="capability-bound mirror receipt"
                        ),
                        "capability-bound mirror receipt",
                    )
                    if (
                        set(receipt) != MIRROR_RECEIPT_FIELDS
                        or _canonical_json_bytes(receipt) != paired[0]
                        or receipt.get("schema_version") != MIRROR_RECEIPT_SCHEMA
                        or receipt.get("series_token_sha256") != binding.series_token_sha256
                        or receipt.get("ordinal") != ordinal
                        or receipt.get("attempt_outcome") != last_record.outcome
                        or receipt.get("attempt_sealed")
                        is not (last_record.outcome != "INCOMPLETE_UNTERMINALIZED")
                        or receipt.get("primary_ledger_root") != binding.ledger_root.as_posix()
                        or receipt.get("secondary_snapshot_root") != snapshot.as_posix()
                        or receipt.get("history_root_sha256") != history.history_root_sha256
                        or receipt.get("live_ledger_root_sha256") != live_root
                        or type(receipt.get("file_count")) is not int
                        or cast(int, receipt["file_count"]) < 1
                        or type(receipt.get("total_bytes")) is not int
                        or cast(int, receipt["total_bytes"]) < 1
                        or not _lower_hex(receipt.get("primary_inventory_sha256"), 64)
                        or receipt.get("primary_inventory_sha256")
                        != receipt.get("secondary_inventory_sha256")
                        or receipt.get("second_copy_verified") is not True
                        or receipt.get("verified_at_utc") != FIXED_WALL_CLOCK_TEXT
                    ):
                        raise RehearsalV22Error("mirror receipt phase postcondition drifted")
                    expected_receipt_payload = paired[0]
                completed = True
            finally:
                _MIRROR_PHASE_CAPABILITY.reset(capability_token)
                capabilities = tuple(record for record in capabilities if record is not capability)
            if completed:
                next_index += 1

        completed_sequence = False
        body_failed = False
        try:
            yield phase_scope
            completed_sequence = next_index == len(expected_phases)
            if completed_sequence:
                if expected_receipt_payload is None:
                    raise RehearsalV22Error(
                        "mirror sequence completed without bound inventory and receipt"
                    )
                current_history = validate_live_history(binding)
                if current_history != history:
                    raise RehearsalV22Error(
                        "primary ledger drifted during the mirror write sequence"
                    )
                _validate_hot_second_copy_commitment(binding, current_history)
        except BaseException:
            body_failed = True
            raise
        finally:
            sequences = tuple(record for record in sequences if record is not sequence_token)
        if not body_failed and not completed_sequence:
            raise RehearsalV22Error("mirror write sequence did not complete every exact phase")

    return capability_is_issued, mirror_write_sequence


(
    _mirror_phase_capability_is_issued,
    _mirror_write_sequence,
) = _build_mirror_phase_state()


def _build_recovery_rename_state() -> tuple[Any, Any]:
    nonce = object()
    registry: tuple[_RecoveryRenameCapability, ...] = ()
    capability_type = _RecoveryRenameCapability
    capability_context = _RECOVERY_RENAME_CAPABILITY
    audit_context = _AUDIT_POLICY
    policy_checker = _audit_policy_is_issued
    python_type = type
    python_id = id

    def capability_is_issued(
        capability: _RecoveryRenameCapability | None,
        *,
        policy: _AuditPolicy,
        source: Path,
        destination: Path,
    ) -> bool:
        if (
            capability is None
            or python_type(capability) is not capability_type
            or capability._nonce is not nonce
            or capability.policy_id != python_id(policy)
            or capability.source != source
            or capability.destination != destination
            or (source, destination) not in policy.recovery_rename_pairs
        ):
            return False
        for record in registry:  # noqa: SIM110 - audit path avoids dynamic builtins.any
            if record is capability:
                return True
        return False

    @contextmanager
    def recovery_rename_scope(
        policy: _AuditPolicy,
        *,
        source: Path,
        destination: Path,
    ) -> Iterator[None]:
        nonlocal registry
        active_policy = audit_context.get()
        if (
            active_policy is not policy
            or not policy_checker(policy)
            or registry
            or capability_context.get() is not None
            or (source, destination) not in policy.recovery_rename_pairs
            or source not in policy.write_roots
            or destination not in policy.exact_write_paths
            or source == destination
            or not source.is_absolute()
            or not destination.is_absolute()
        ):
            raise RehearsalV22Error(
                "recovery rename lacks one exact issued stage-to-final capability"
            )
        capability = capability_type(
            _nonce=nonce,
            policy_id=python_id(policy),
            source=source,
            destination=destination,
        )
        registry = (*registry, capability)
        token = capability_context.set(capability)
        try:
            yield
        finally:
            capability_context.reset(token)
            registry = tuple(record for record in registry if record is not capability)

    return capability_is_issued, recovery_rename_scope


(
    _recovery_rename_capability_is_issued,
    _recovery_rename_scope,
) = _build_recovery_rename_state()


def _build_native_rename_state() -> tuple[Any, Any, Any]:
    nonce = object()
    registry: tuple[_NativeRenameCapability, ...] = ()
    capability_context = _NATIVE_RENAME_CAPABILITY
    capability_type = _NativeRenameCapability
    recovery_capability_type = _RecoveryRenameCapability
    audit_policy_context = _AUDIT_POLICY
    temp_authority_context = _TEMP_AUTHORITY
    recovery_capability_context = _RECOVERY_RENAME_CAPABILITY
    mirror_phase_context = _MIRROR_PHASE_CAPABILITY
    audit_policy_checker = _audit_policy_is_issued
    recovery_capability_checker = _recovery_rename_capability_is_issued
    mirror_phase_checker = _mirror_phase_capability_is_issued
    audit_hook = _process_audit_hook
    import_guard_checker = _import_guard_is_active
    require_write_path = _require_audited_write_path
    path_in_roots = _path_in_roots
    path_in_create_only_root = _path_in_create_only_root
    python_id = id
    python_type = type
    python_str = str
    builtins_build_class = builtins.__build_class__
    builtins_isinstance = builtins.isinstance
    builtins_setattr = builtins.setattr
    builtins_int = builtins.int
    ctypes_sys = ctypes._sys  # type: ignore[attr-defined]  # CPython private API
    ctypes_os = ctypes._os  # type: ignore[attr-defined]  # CPython private API
    ctypes_sys_platform = ctypes_sys.platform
    ctypes_os_name = ctypes_os.name
    cdll_factory = ctypes.CDLL
    cdll_new = ctypes.CDLL.__new__
    cdll_init = ctypes.CDLL.__init__
    cdll_getattr = ctypes.CDLL.__getattr__
    cdll_getattribute = ctypes.CDLL.__getattribute__
    cdll_getitem = ctypes.CDLL.__getitem__
    cdll_setattr = ctypes.CDLL.__setattr__
    cdll_func_flags = ctypes.CDLL._func_flags_
    cdll_func_restype = ctypes.CDLL._func_restype_
    ctypes_dlopen = ctypes._dlopen  # type: ignore[attr-defined]  # CPython private API
    cfuncptr_type = ctypes._CFuncPtr  # type: ignore[attr-defined]  # CPython private API
    cfuncptr_call = cfuncptr_type.__call__
    cfuncptr_setattr = cfuncptr_type.__setattr__
    funcflag_use_errno = ctypes._FUNCFLAG_USE_ERRNO  # type: ignore[attr-defined]
    set_errno = ctypes.set_errno
    get_errno = ctypes.get_errno
    fsencode = os.fsencode
    c_int = ctypes.c_int
    c_char_p = ctypes.c_char_p
    c_uint = ctypes.c_uint
    c_int_new = c_int.__new__
    c_int_init = c_int.__init__
    c_int_from_param_owner = c_int if "from_param" in c_int.__dict__ else type(c_int)
    c_int_from_param = c_int_from_param_owner.__dict__["from_param"]
    c_char_p_new = c_char_p.__new__
    c_char_p_init = c_char_p.__init__
    c_char_p_from_param_owner = c_char_p if "from_param" in c_char_p.__dict__ else type(c_char_p)
    c_char_p_from_param = c_char_p_from_param_owner.__dict__["from_param"]
    c_uint_new = c_uint.__new__
    c_uint_init = c_uint.__init__
    c_uint_from_param_owner = c_uint if "from_param" in c_uint.__dict__ else type(c_uint)
    c_uint_from_param = c_uint_from_param_owner.__dict__["from_param"]
    python_int = int
    sys_gettrace = sys.gettrace
    sys_getprofile = sys.getprofile
    monitoring_module = sys.monitoring  # type: ignore[attr-defined]  # Python 3.12
    monitoring_get_tool = monitoring_module.get_tool
    monitoring_get_events = monitoring_module.get_events
    monitoring_use_tool_id = monitoring_module.use_tool_id
    monitoring_free_tool_id = monitoring_module.free_tool_id
    monitoring_set_events = monitoring_module.set_events
    monitoring_set_local_events = monitoring_module.set_local_events
    monitoring_register_callback = monitoring_module.register_callback
    monitoring_restart_events = monitoring_module.restart_events
    monitoring_tool_ids = (0, 1, 2, 3, 4, 5)
    gc_isenabled = gc.isenabled
    gc_disable = gc.disable
    gc_enable = gc.enable
    pthread_sigmask = signal.pthread_sigmask
    signal_valid_signals = signal.valid_signals
    signal_block = signal.SIG_BLOCK
    signal_setmask = signal.SIG_SETMASK
    signals_type = signal.Signals
    sigkill = signal.SIGKILL
    sigstop = signal.SIGSTOP

    def require_native_runtime_identity() -> None:
        if (
            _NATIVE_RENAME_CAPABILITY is not capability_context
            or _NativeRenameCapability is not capability_type
            or _RecoveryRenameCapability is not recovery_capability_type
            or _AUDIT_POLICY is not audit_policy_context
            or _TEMP_AUTHORITY is not temp_authority_context
            or _RECOVERY_RENAME_CAPABILITY is not recovery_capability_context
            or _MIRROR_PHASE_CAPABILITY is not mirror_phase_context
            or _audit_policy_is_issued is not audit_policy_checker
            or _recovery_rename_capability_is_issued is not recovery_capability_checker
            or _mirror_phase_capability_is_issued is not mirror_phase_checker
            or _process_audit_hook is not audit_hook
            or _import_guard_is_active is not import_guard_checker
            or _require_audited_write_path is not require_write_path
            or _path_in_roots is not path_in_roots
            or _path_in_create_only_root is not path_in_create_only_root
            or globals().get("_native_rename_capability_is_issued") is not capability_is_issued
            or builtins.__build_class__ is not builtins_build_class
            or builtins.isinstance is not builtins_isinstance
            or builtins.setattr is not builtins_setattr
            or builtins.int is not builtins_int
            or ctypes._sys is not ctypes_sys  # type: ignore[attr-defined]
            or ctypes._os is not ctypes_os  # type: ignore[attr-defined]
            or python_type(ctypes_sys.platform) is not python_str
            or ctypes_sys.platform != ctypes_sys_platform
            or python_type(ctypes_os.name) is not python_str
            or ctypes_os.name != ctypes_os_name
            or ctypes.CDLL is not cdll_factory
            or ctypes.CDLL.__new__ is not cdll_new
            or ctypes.CDLL.__init__ is not cdll_init
            or ctypes.CDLL.__getattr__ is not cdll_getattr
            or ctypes.CDLL.__getattribute__ is not cdll_getattribute
            or ctypes.CDLL.__getitem__ is not cdll_getitem
            or ctypes.CDLL.__setattr__ is not cdll_setattr
            or python_type(ctypes.CDLL._func_flags_) is not python_int
            or ctypes.CDLL._func_flags_ != cdll_func_flags
            or ctypes.CDLL._func_restype_ is not cdll_func_restype
            or ctypes._dlopen is not ctypes_dlopen  # type: ignore[attr-defined]
            or ctypes._CFuncPtr is not cfuncptr_type  # type: ignore[attr-defined]
            or cfuncptr_type.__call__ is not cfuncptr_call
            or cfuncptr_type.__setattr__ is not cfuncptr_setattr
            or python_type(ctypes._FUNCFLAG_USE_ERRNO)  # type: ignore[attr-defined]
            is not python_int
            or funcflag_use_errno != ctypes._FUNCFLAG_USE_ERRNO  # type: ignore[attr-defined]
            or ctypes.set_errno is not set_errno
            or ctypes.get_errno is not get_errno
            or os.fsencode is not fsencode
            or ctypes.c_int is not c_int
            or ctypes.c_char_p is not c_char_p
            or ctypes.c_uint is not c_uint
            or c_int.__new__ is not c_int_new
            or c_int.__init__ is not c_int_init
            or c_int_from_param_owner.__dict__.get("from_param") is not c_int_from_param
            or c_char_p.__new__ is not c_char_p_new
            or c_char_p.__init__ is not c_char_p_init
            or c_char_p_from_param_owner.__dict__.get("from_param") is not c_char_p_from_param
            or c_uint.__new__ is not c_uint_new
            or c_uint.__init__ is not c_uint_init
            or c_uint_from_param_owner.__dict__.get("from_param") is not c_uint_from_param
            or sys.gettrace is not sys_gettrace
            or sys.getprofile is not sys_getprofile
            or sys.monitoring is not monitoring_module  # type: ignore[attr-defined]
            or monitoring_module.get_tool is not monitoring_get_tool
            or monitoring_module.get_events is not monitoring_get_events
            or monitoring_module.use_tool_id is not monitoring_use_tool_id
            or monitoring_module.free_tool_id is not monitoring_free_tool_id
            or monitoring_module.set_events is not monitoring_set_events
            or monitoring_module.set_local_events is not monitoring_set_local_events
            or monitoring_module.register_callback is not monitoring_register_callback
            or monitoring_module.restart_events is not monitoring_restart_events
            or gc.isenabled is not gc_isenabled
            or gc.disable is not gc_disable
            or gc.enable is not gc_enable
            or signal.pthread_sigmask is not pthread_sigmask
            or signal.valid_signals is not signal_valid_signals
            or signal.SIG_BLOCK is not signal_block
            or signal.SIG_SETMASK is not signal_setmask
            or signal.Signals is not signals_type
            or signal.SIGKILL is not sigkill
            or signal.SIGSTOP is not sigstop
            or sys_gettrace() is not None
            or sys_getprofile() is not None
        ):
            raise RehearsalV22Error("native rename runtime factory identity drifted")

    def disable_runtime_callbacks() -> bool:
        require_native_runtime_identity()
        for tool_id in monitoring_tool_ids:
            events = monitoring_get_events(tool_id)
            if (
                monitoring_get_tool(tool_id) is not None
                or python_type(events) is not python_int
                or events != 0
            ):
                raise RehearsalV22Error("native rename refuses active Python monitoring callbacks")
        was_enabled = gc_isenabled()
        if was_enabled:
            gc_disable()
        if gc_isenabled():
            raise RehearsalV22Error("native rename could not disable cyclic GC callbacks")
        return was_enabled

    def block_runtime_callbacks() -> set[signal.Signals]:
        blocked = {item for item in signal_valid_signals() if item not in {sigkill, sigstop}}
        return cast(
            set[signal.Signals],
            pthread_sigmask(signal_block, blocked),
        )

    def capability_is_issued(
        capability: _NativeRenameCapability | None,
        *,
        policy: _AuditPolicy,
    ) -> bool:
        if (
            capability is None
            or python_type(capability) is not capability_type
            or capability._nonce is not nonce
            or capability.policy_id != python_id(policy)
        ):
            return False
        for record in registry:  # noqa: SIM110 - audit path avoids dynamic builtins.any
            if record is capability:
                return True
        return False

    def native_rename_exclusive_call(
        policy: _AuditPolicy,
        source: Path,
        destination: Path,
    ) -> tuple[int, int]:
        nonlocal registry
        require_native_runtime_identity()
        if (
            audit_policy_context.get() is not policy
            or not audit_policy_checker(policy)
            or require_write_path(source, policy) != source
            or require_write_path(destination, policy) != destination
        ):
            raise RehearsalV22Error("native rename scope lacks exact issued paths")
        mirror_roots = tuple(
            root
            for root in (
                policy.mirror_snapshot_root,
                policy.primary_receipt_root,
                policy.secondary_receipt_root,
            )
            if root is not None
        )
        mirror_path = path_in_roots(source, mirror_roots) or path_in_roots(
            destination,
            mirror_roots,
        )
        authority = temp_authority_context.get()
        recovery_capability = recovery_capability_context.get()
        ordinary_temporary_rename = bool(
            authority is not None
            and source.parent == authority
            and source.is_relative_to(authority)
            and (
                destination.parent == authority
                or (
                    destination in policy.exact_write_paths
                    and not destination.is_relative_to(authority)
                )
            )
        )
        recovery_rename = recovery_capability_checker(
            recovery_capability,
            policy=policy,
            source=source,
            destination=destination,
        )
        if (
            path_in_create_only_root(source, policy)
            or path_in_create_only_root(destination, policy)
            or mirror_path
            or ordinary_temporary_rename is recovery_rename
        ):
            raise RehearsalV22Error(
                "native bundle rename lacks one exclusive temporary or recovery authority"
            )
        if registry or capability_context.get() is not None:
            raise RehearsalV22Error("native rename capability is non-reentrant")
        capability = _NativeRenameCapability(
            _nonce=nonce,
            policy_id=python_id(policy),
            source=source,
            destination=destination,
            symbol="renamex_np",
        )
        source_bytes = fsencode(source)
        destination_bytes = fsencode(destination)
        rename_flags = c_uint(0x00000004)
        rename_argtypes: Any = [c_char_p, c_char_p, c_uint]
        require_native_runtime_identity()
        gc_was_enabled = disable_runtime_callbacks()
        try:
            prior_signal_mask = block_runtime_callbacks()
            prior_registry = registry
            token: contextvars.Token[_NativeRenameCapability | None] | None = None
            try:
                try:
                    registry = (*prior_registry, capability)
                    token = capability_context.set(capability)
                    libc = cdll_factory(None, use_errno=True)
                    renamex_np = libc.renamex_np
                    renamex_np.argtypes = rename_argtypes
                    renamex_np.restype = c_int
                    set_errno(0)
                    raw_return_code = renamex_np(
                        source_bytes,
                        destination_bytes,
                        rename_flags,
                    )
                    observed_errno = get_errno()
                finally:
                    registry = prior_registry
            finally:
                try:
                    if token is not None:
                        capability_context.reset(token)
                finally:
                    pthread_sigmask(signal_setmask, prior_signal_mask)
        finally:
            if gc_was_enabled:
                gc_enable()
        require_native_runtime_identity()
        if gc_isenabled() is not gc_was_enabled:
            raise RehearsalV22Error("native rename cyclic GC state was not restored")
        if (
            python_type(raw_return_code) is not python_int
            or python_type(observed_errno) is not python_int
        ):
            raise RehearsalV22Error("native rename returned a non-integer result")
        return_code = raw_return_code
        return return_code, observed_errno

    def native_mirror_renameatx_exclusive_call(
        policy: _AuditPolicy,
        source: Path,
        destination: Path,
        *,
        parent_descriptor: int,
        expected_parent_identity: tuple[int, int],
    ) -> tuple[int, int, bool]:
        nonlocal registry
        require_native_runtime_identity()
        mirror_roots = tuple(
            root
            for root in (
                policy.mirror_snapshot_root,
                policy.primary_receipt_root,
                policy.secondary_receipt_root,
            )
            if root is not None
        )
        if (
            audit_policy_context.get() is not policy
            or not audit_policy_checker(policy)
            or require_write_path(source, policy) != source
            or require_write_path(destination, policy) != destination
            or not path_in_roots(source, mirror_roots)
            or not path_in_roots(destination, mirror_roots)
            or policy.mirror_write_phase != "publish"
            or policy.mirror_publish_paths != (source, destination)
            or policy.mirror_staging_root != source
            or policy.mirror_snapshot_root is None
            or source.parent != policy.mirror_snapshot_root
            or destination.parent != policy.mirror_snapshot_root
            or source.parent != destination.parent
            or not mirror_phase_checker(
                mirror_phase_context.get(),
                outer=None,
                issued=policy,
            )
        ):
            raise RehearsalV22Error(
                "native mirror rename escaped its exact issued publish capability"
            )
        if registry or capability_context.get() is not None:
            raise RehearsalV22Error("native mirror rename capability is non-reentrant")
        try:
            raw = fcntl.fcntl(parent_descriptor, fcntl.F_GETPATH, b"\0" * 1024)
            terminator = raw.find(b"\0")
            descriptor_path = Path(os.fsdecode(raw[:terminator]))
            descriptor_metadata = os.fstat(parent_descriptor)
            parent_metadata = source.parent.lstat()
            if (
                terminator <= 0
                or descriptor_path.resolve(strict=True) != source.parent.resolve(strict=True)
                or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
                != expected_parent_identity
                or (parent_metadata.st_dev, parent_metadata.st_ino) != expected_parent_identity
                or not stat.S_ISDIR(descriptor_metadata.st_mode)
            ):
                raise RehearsalV22Error("renameatx_np parent descriptor identity drifted")
            source_name = fsencode(source.name)
            destination_name = fsencode(destination.name)
            rename_flags = c_uint(0x00000004)
            rename_argtypes: Any = [c_int, c_char_p, c_int, c_char_p, c_uint]
            capability = _NativeRenameCapability(
                _nonce=nonce,
                policy_id=python_id(policy),
                source=source,
                destination=destination,
                symbol="renameatx_np",
            )
            require_native_runtime_identity()
            gc_was_enabled = disable_runtime_callbacks()
            try:
                prior_signal_mask = block_runtime_callbacks()
                prior_registry = registry
                token: contextvars.Token[_NativeRenameCapability | None] | None = None
                try:
                    try:
                        registry = (*prior_registry, capability)
                        token = capability_context.set(capability)
                        libc = cdll_factory(None, use_errno=True)
                        renameatx_np = libc.renameatx_np
                        renameatx_np.argtypes = rename_argtypes
                        renameatx_np.restype = c_int
                        set_errno(0)
                        raw_return_code = renameatx_np(
                            parent_descriptor,
                            source_name,
                            parent_descriptor,
                            destination_name,
                            rename_flags,
                        )
                        observed_errno = get_errno()
                    finally:
                        registry = prior_registry
                finally:
                    try:
                        if token is not None:
                            capability_context.reset(token)
                    finally:
                        pthread_sigmask(signal_setmask, prior_signal_mask)
            finally:
                if gc_was_enabled:
                    gc_enable()
            require_native_runtime_identity()
            if gc_isenabled() is not gc_was_enabled:
                raise RehearsalV22Error("native mirror rename cyclic GC state was not restored")
            if (
                python_type(raw_return_code) is not python_int
                or python_type(observed_errno) is not python_int
            ):
                raise RehearsalV22Error("native mirror rename returned a non-integer result")
            return_code = raw_return_code
            parent_fsync_completed = False
            if return_code == 0:
                os.fsync(parent_descriptor)
                parent_fsync_completed = True
        finally:
            pass
        return return_code, observed_errno, parent_fsync_completed

    return (
        capability_is_issued,
        native_rename_exclusive_call,
        native_mirror_renameatx_exclusive_call,
    )


(
    _native_rename_capability_is_issued,
    _native_rename_exclusive_call,
    _native_mirror_renameatx_exclusive_call,
) = _build_native_rename_state()


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
SERIES_2_PREREGISTRATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-series-2-preregistration-amendment-20260823.json"
)
SERIES_2_PREREGISTRATION_SHA256 = "be98803a6b6cbe25a79242c23ee728d0ed687ac70e3f6990230bb1710886e91c"
SERIES_2_PREREGISTRATION_COMMIT = "b6dfff08557fdbca1336f816b197cd6c8a0d5c41"
SERIES_2_PREREGISTRATION_PARENT = "f21fa10babd9b300fae03c751ba038c7ebc77392"
SERIES_2_TOKEN_SEED_SHA256 = "2deee3072c339d8e8993bbf8ca8ecbe9380576c1499835828064fb4aead43d30"
SERIES_2_BUNDLE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_2_series_2_heldout_rehearsal_bundle.schema.json"
)
SERIES_2_BUNDLE_SCHEMA_SHA256 = "252ad069ed300917989a97656b4c38e6ee2c74069b2bacd6c258b263b5684ec7"
SERIES_2_RELEASE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_2_series_2_heldout_release_authorization.schema.json"
)
SERIES_2_RELEASE_SCHEMA_SHA256 = "c7228bff2d4ec575bcdec024194ce2b53d37a27e05ba793bd4bdf145a97f63be"
SERIES_2_LOSS_INCIDENT_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-sealed-ledger-loss-incident-20260823.json"
)
SERIES_2_LOSS_INCIDENT_COMMIT = "a7cea63378a39702b1618895b3a7350febcb5da6"
SERIES_2_OWNER_DECISION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-owner-decision-rerun-rehearsal-series-2-20260823.json"
)
SERIES_2_OWNER_DECISION_SHA256 = "e0fc6a17c853be063632551b4b794091a6152324af7f7ec95262ed2af8538051"
SERIES_2_OWNER_DECISION_COMMIT = "9a028855c73c4feba36125ed30cf5a7d4db5fff4"
SERIES_2_EPOCH_ORIGIN = 5
SERIES_2_SERIES_SCHEMA_VERSION = "p4.2a-v2-2-rehearsal-series-2-v1"
EPOCH_7_ADJUDICATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-series2-ordinal2-adjudication-and-epoch7-direction-20260827.json"
)
EPOCH_7_ADJUDICATION_SHA256 = "47d8a9bbd842b496352ba210952539cb8ad1e7ab36091ab0465b8bf4c0048119"
EPOCH_7_ADJUDICATION_COMMIT = "2dd5d60121dab100c3b2000ec73dbc5ce1cd4aa0"
EPOCH_7_COMPANION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-epoch7-design-review-r2-and-companion-20260827.json"
)
EPOCH_7_COMPANION_SHA256 = "43651a31b24088b0ec676bdf2fee3c0f54629471ab29d5e5164e2b2e308e7c9d"
EPOCH_7_COMPANION_COMMIT = "c2aee25cd96296245d21b776974193172578dae3"
EPOCH_7_DESIGN_R1_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch7-sealed-bundle-recovery-design-proposal-20260827.md"
)
EPOCH_7_DESIGN_R1_SHA256 = "c80f9219a1bd61aee0bbf143295b177926377dab570aae578785dec95f628e0f"
EPOCH_7_DESIGN_R1_BYTES = 53_608
EPOCH_7_DESIGN_R1_COMMIT = "9cd424c292d658c1ddb1092f618049e6283aabaf"
EPOCH_7_DESIGN_R2_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch7-sealed-bundle-recovery-design-proposal-r2-20260827.md"
)
EPOCH_7_DESIGN_R2_SHA256 = "46ea89f8edf838edcca6b6f34996be273c7ea73e04ee7e2b998293c83984f3e1"
EPOCH_7_DESIGN_R2_BYTES = 27_880
EPOCH_7_DESIGN_R2_COMMIT = "1e2e23f8948aa88376dfba45b01b2666b5c9ddaf"
EPOCH_7_SURFACE_AUTHORITY_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-epoch7-surface-authority-20260827.json"
)
EPOCH_7_SURFACE_AUTHORITY_SHA256 = (
    "eb2eb477165af6eb4493f3892328b73ba373a7bc83d8857514eb328f52a0430e"
)
EPOCH_7_SURFACE_AUTHORITY_COMMIT = "06336a9f593ede4132be73a8c8a087df18db904b"
EPOCH_7_RECOVERY_CONTRACT_SCHEMA = "p4.2a-v2-2-series2-epoch7-recovery-contract-v1"
EPOCH_7_RECOVERY_CONTRACT_FIELDS = frozenset(
    {
        "schema_version",
        "governing_adjudication",
        "implementation_epoch",
        "recovery_review_request_contract",
        "recovery_authorization_contract",
        "recovery_owner_binding_contract",
        "recovery_claim_contract",
        "bundle_mirror_receipt_contract",
        "dual_byte_anchor_contract",
        "unique_a_and_lineage_census_contract",
        "protected_inputs_and_permitted_outputs",
        "legacy_absence_and_locks",
    }
)
EPOCH_7_IMPLEMENTATION_EPOCH = 7
EPOCH_7_HISTORICAL_SELECTED_EPOCH = 6
EPOCH_7_HISTORICAL_SELECTED_COMMIT = "e5aab9772793a7b0465f100cb48f99a1bc4e45dc"
EPOCH_7_HISTORICAL_CONTROL_ROOT_SHA256 = (
    "5948fd29a8c3f38399e6518699483f61094d577ae695bc7aa0b48c84e5b8829d"
)
EPOCH_7_SEALED_HISTORY_ROOT_SHA256 = (
    "832559b59a8edc09c04b0f5a7c09cea71e5c3597c2bdc0831072a4122bf016e7"
)
EPOCH_7_SEALED_LIVE_LEDGER_ROOT_SHA256 = (
    "ab08612ba2e11e45c3a3415ca7079117f2a739bf470719abf4f204958272574d"
)
EPOCH_7_SELECTED_EVIDENCE_ROOT_SHA256 = (
    "eb44c7f3219e3f9ce92fbf17fd2da0e4b643ad0abc09f4008b2da1e35d426093"
)
EPOCH_7_SELECTED_CANDIDATE_CONTENT_ROOT_SHA256 = (
    "520c371eeceac62ee7fb567d91bd6aba094e04f9bfa297108f8b850d72f45f2b"
)
EPOCH_7_SELECTED_RUN_ROOT_SHA256 = (
    "5fb8edf3aa65cdcd0f54b82bdf6f240104fa8537c1004e640671910115f8f314"
)
EPOCH_7_SELECTED_STARTED_SHA256 = "4166a58d1c4b118d74ca402ef6f1635f98aab7cd0402dfda918e1e30bebd246c"
EPOCH_7_SELECTED_CANDIDATE_SHA256 = (
    "e1d67123469ce63739936b7db8a520f4f0cc8dda969455a7117246cda4485086"
)
EPOCH_7_SELECTED_TERMINAL_SHA256 = (
    "3f41d80d63214af379bd8423ab7e0c61d6508ab19339f9bc7bf9a1d9ac4e0bf5"
)
EPOCH_7_SELECTED_STARTED_BYTES = 2109
EPOCH_7_SELECTED_CANDIDATE_BYTES = 833
EPOCH_7_SELECTED_TERMINAL_BYTES = 12206
EPOCH_7_SELECTED_TERMINAL_INVENTORY_COUNT = 36
EPOCH_7_SELECTED_TERMINAL_INVENTORY_BYTES = 50_213_329
EPOCH_7_SELECTED_RUN_A_PROBE_SHA256 = (
    "c53e94d513443399e2135c77fb6f556bc3359fb39f77ab0882755afe1a77628b"
)
EPOCH_7_SELECTED_RUN_B_PROBE_SHA256 = (
    "7552c2a86515adae7206423429bf8fb61f5ca0a2038ceccf0734be447c5ded0b"
)
EPOCH_7_SELECTED_PROBE_BYTES = 1273
EPOCH_7_SEALED_MIRROR_RECEIPT_SHA256 = (
    "b8da48fa759d7f5301dff63eed61c711d3fb01e2715fbc45ddd27a28545820f6"
)
EPOCH_7_SEALED_MIRROR_RECEIPT_BYTES = 1222
EPOCH_7_RECOVERY_REVIEW_REQUEST_SCHEMA = (
    "p4.2a-v2-2-series2-sealed-bundle-recovery-review-request-v1"
)
EPOCH_7_RECOVERY_AUTHORIZATION_SCHEMA = "p4.2a-v2-2-series2-sealed-bundle-recovery-authorization-v1"
EPOCH_7_RECOVERY_OWNER_BINDING_SCHEMA = (
    "p4.2a-v2-2-series2-sealed-bundle-recovery-owner-confirmation-binding-v1"
)
EPOCH_7_RECOVERY_STARTED_SCHEMA = "p4.2a-v2-2-series2-bundle-recovery-started-v1"
EPOCH_7_RECOVERY_TERMINAL_SCHEMA = "p4.2a-v2-2-series2-bundle-recovery-terminal-v1"
EPOCH_7_RECOVERY_MIRROR_RECEIPT_SCHEMA = "p4.2a-v2-2-series2-bundle-recovery-mirror-receipt-v1"
EPOCH_7_LINEAGE_CENSUS_SCHEMA = "p4.2a-v2-2-real-lineage-census-v1"
EPOCH_8_DESIGN_PROPOSAL_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch8-preclaim-census-symmetry-design-proposal-20260901.md"
)
EPOCH_8_DESIGN_PROPOSAL_SHA256 = (
    "0a0df03f853730e83b6963564035134538769c2e3db1ad07961379e3448a44b1"
)
EPOCH_8_DESIGN_PROPOSAL_BYTES = 30_343
EPOCH_8_DESIGN_PROPOSAL_COMMIT = "45f486cb72a08e3520d863c86218c44ad1d5ce90"
EPOCH_8_DESIGN_REVIEW_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch8-preclaim-census-symmetry-"
    "independent-design-review-20260901.json"
)
EPOCH_8_DESIGN_REVIEW_SHA256 = (
    "e1b494d9ab76c704745cf7fbd00ec14269faf8f0a919343cc5244fd187a194a6"
)
EPOCH_8_DESIGN_REVIEW_BYTES = 9_610
EPOCH_8_DESIGN_REVIEW_COMMIT = "a1dff7a8b9d093404272e57fe30b6f1ddb575516"
EPOCH_8_ADJUDICATION_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-series2-epoch7-recovery-preclaim-refusal-and-epoch8-direction-20260901.json"
)
EPOCH_8_ADJUDICATION_SHA256 = (
    "673d74ac6229f891fa517ec6dadf4cdd2c2093edf110c7c4c8a277d1b425252c"
)
EPOCH_8_ADJUDICATION_BYTES = 10_520
EPOCH_8_ADJUDICATION_COMMIT = "87896e9b2c42d6110968876d21f3b0f3963d2ac7"
EPOCH_8_COMPANION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-series2-epoch8-companion-20260901.json"
)
EPOCH_8_COMPANION_SHA256 = "4d25ba645c81b3e0d6a3458a47d9e10c80b7cd61f9ad16a28404160af91226ed"
EPOCH_8_COMPANION_BYTES = 53_282
EPOCH_8_COMPANION_COMMIT = "a39c0263fefcfbdb1886100fec1b71ec374b43a4"
EPOCH_8_SURFACE_AUTHORITY_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch8-surface-authority-20260901.json"
)
EPOCH_8_SURFACE_AUTHORITY_SHA256 = (
    "4547a2231c23a0fff96dced033028c279c4247c76130e79360e2ec602f8dd016"
)
EPOCH_8_SURFACE_AUTHORITY_BYTES = 726
EPOCH_8_SURFACE_AUTHORITY_COMMIT = "73a703a422b5209115f5b244490db36e06b1f15d"
EPOCH_8_RECOVERY_CONTRACT_SCHEMA = "p4.2a-v2-2-series2-epoch8-recovery-contract-v1"
EPOCH_8_RECOVERY_CONTRACT_FIELDS = frozenset(
    {
        "schema_version",
        "governing_adjudication",
        "implementation_epoch",
        "registered_preflight_contract",
        "recovery_review_request_contract",
        "recovery_authorization_contract",
        "recovery_owner_binding_contract",
        "recovery_claim_contract",
        "bundle_mirror_receipt_contract",
        "dual_byte_anchor_contract",
        "unique_a_and_lineage_census_contract",
        "protected_inputs_and_permitted_outputs",
        "legacy_absence_and_locks",
    }
)
EPOCH_8_RECOVERY_CONTRACT_CANONICAL_SHA256 = (
    "36b1ae714faf2746f677e3c5aa452d2dc1822234dd10d687aa11d804ac606dbf"
)
EPOCH_8_IMPLEMENTATION_EPOCH = 8
EPOCH_8_HISTORICAL_SELECTED_EPOCH = 6
EPOCH_8_HISTORICAL_SELECTED_COMMIT = "e5aab9772793a7b0465f100cb48f99a1bc4e45dc"
EPOCH_8_HISTORICAL_CONTROL_ROOT_SHA256 = (
    "5948fd29a8c3f38399e6518699483f61094d577ae695bc7aa0b48c84e5b8829d"
)
EPOCH_8_SEALED_HISTORY_ROOT_SHA256 = (
    "832559b59a8edc09c04b0f5a7c09cea71e5c3597c2bdc0831072a4122bf016e7"
)
EPOCH_8_SEALED_LIVE_LEDGER_ROOT_SHA256 = (
    "ab08612ba2e11e45c3a3415ca7079117f2a739bf470719abf4f204958272574d"
)
EPOCH_8_SELECTED_EVIDENCE_ROOT_SHA256 = (
    "eb44c7f3219e3f9ce92fbf17fd2da0e4b643ad0abc09f4008b2da1e35d426093"
)
EPOCH_8_SELECTED_CANDIDATE_CONTENT_ROOT_SHA256 = (
    "520c371eeceac62ee7fb567d91bd6aba094e04f9bfa297108f8b850d72f45f2b"
)
EPOCH_8_SELECTED_RUN_ROOT_SHA256 = (
    "5fb8edf3aa65cdcd0f54b82bdf6f240104fa8537c1004e640671910115f8f314"
)
EPOCH_8_SELECTED_STARTED_SHA256 = "4166a58d1c4b118d74ca402ef6f1635f98aab7cd0402dfda918e1e30bebd246c"
EPOCH_8_SELECTED_CANDIDATE_SHA256 = (
    "e1d67123469ce63739936b7db8a520f4f0cc8dda969455a7117246cda4485086"
)
EPOCH_8_SELECTED_TERMINAL_SHA256 = (
    "3f41d80d63214af379bd8423ab7e0c61d6508ab19339f9bc7bf9a1d9ac4e0bf5"
)
EPOCH_8_SELECTED_STARTED_BYTES = 2_109
EPOCH_8_SELECTED_CANDIDATE_BYTES = 833
EPOCH_8_SELECTED_TERMINAL_BYTES = 12_206
EPOCH_8_SELECTED_TERMINAL_INVENTORY_COUNT = 36
EPOCH_8_SELECTED_TERMINAL_INVENTORY_BYTES = 50_213_329
EPOCH_8_SELECTED_RUN_A_PROBE_SHA256 = (
    "c53e94d513443399e2135c77fb6f556bc3359fb39f77ab0882755afe1a77628b"
)
EPOCH_8_SELECTED_RUN_B_PROBE_SHA256 = (
    "7552c2a86515adae7206423429bf8fb61f5ca0a2038ceccf0734be447c5ded0b"
)
EPOCH_8_SELECTED_PROBE_BYTES = 1_273
EPOCH_8_SEALED_MIRROR_RECEIPT_SHA256 = (
    "b8da48fa759d7f5301dff63eed61c711d3fb01e2715fbc45ddd27a28545820f6"
)
EPOCH_8_SEALED_MIRROR_RECEIPT_BYTES = 1_222
EPOCH_8_RECOVERY_REVIEW_REQUEST_SCHEMA = (
    "p4.2a-v2-2-series2-sealed-bundle-recovery-review-request-v2"
)
EPOCH_8_RECOVERY_AUTHORIZATION_SCHEMA = (
    "p4.2a-v2-2-series2-sealed-bundle-recovery-authorization-v1"
)
EPOCH_8_RECOVERY_OWNER_BINDING_SCHEMA = (
    "p4.2a-v2-2-series2-sealed-bundle-recovery-owner-confirmation-binding-v1"
)
EPOCH_8_RECOVERY_STARTED_SCHEMA = "p4.2a-v2-2-series2-bundle-recovery-started-v1"
EPOCH_8_RECOVERY_TERMINAL_SCHEMA = "p4.2a-v2-2-series2-bundle-recovery-terminal-v1"
EPOCH_8_RECOVERY_MIRROR_RECEIPT_SCHEMA = (
    "p4.2a-v2-2-series2-bundle-recovery-mirror-receipt-v1"
)
EPOCH_8_LINEAGE_CENSUS_SCHEMA = "p4.2a-v2-2-real-lineage-census-v1"
EPOCH_8_READ_ONLY_PREFLIGHT_SCHEMA = "p4.2a-v2-2-read-only-implementation-preflight-v2"
EPOCH_7_LIVE_REVIEW_CARRY_FORWARD: FixedCarryForwardBinding = (
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch7-r2-implementation-independent-review-20260831.json",
    "5712cc01f088ba96e9f199e60e327f171e24b23a4b6c1ca972d147bba75a208f",
    "6f150a31336fbb06cfbe0c42507806025b42daaa",
    "PINNED_LANDING_PROJECTION",
    "6f150a31336fbb06cfbe0c42507806025b42daaa",
    15_546,
)
EPOCH_7_LANDING_CARRY_FORWARD: FixedCarryForwardBinding = (
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch7-r2-merge-landing-record-20260901.json",
    "03ba42262592c67df605021ee4f2ec5dfc495301f28f7ceb2aa514697f010fb6",
    "dcd749f4707f6b806249e842e1402d0c12df2fbf",
    "PINNED_SOURCE",
    None,
    6_625,
)
EPOCH_7_REFUSED_Q_CARRY_FORWARD: FixedCarryForwardBinding = (
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-through-ordinal-000002-bundle-recovery-review-request-20260901.json",
    "fbd9df2346090a4ac23a1957f7367103229316b38a3a3c76d2392657f0a2938f",
    "f9743fdfce5d503c975a8fae3b32e95501b86db2",
    "PINNED_SOURCE",
    None,
    55_722,
)
EPOCH_7_REFUSED_R_CARRY_FORWARD: FixedCarryForwardBinding = (
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-through-ordinal-000002-bundle-recovery-authorization-20260901.json",
    "0eba7d27441e83547a0052d1fc184e9bee3df03dda447942cf351e765121d890",
    "88de28884f33ec4beba2dd4c42880fdb9c9ae9a8",
    "PINNED_SOURCE",
    None,
    9_272,
)
EPOCH_7_REFUSED_B_CARRY_FORWARD: FixedCarryForwardBinding = (
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-through-ordinal-000002-"
    "bundle-recovery-owner-confirmation-binding-20260901.json",
    "34b50becd377c65dc5ef17e83b7794be1c9800a0e263a653f30338dbdad29cc2",
    "7b9aa18372410baa5d96bd9560fa10a2c6a3d8ac",
    "PINNED_SOURCE",
    None,
    2_841,
)
RECOVERY_WORK_COUNTER_FIELDS = (
    "git_objects_read",
    "recursive_bytes_hashed",
    "sealed_snapshot_files_visited",
    "bundle_bytes_copied",
)
RECOVERY_WORK_LIMITS: Mapping[str, int] = {
    "git_objects_read": 20_000,
    "recursive_bytes_hashed": 768_000_000,
    "sealed_snapshot_files_visited": 2_000,
    "bundle_bytes_copied": 256_000_000,
}
SERIES_2_EPOCH_5_LANDING_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch5-registered-gate-landing-report-20260826.json"
)
SERIES_2_EPOCH_5_LANDING_SHA256 = "cd3b0faf61d54824739f2f5263718aee455cd1ef59199ea8a7076ffe60f39ac9"
SERIES_2_EPOCH_5_LANDING_COMMIT = "9094039a09034e279fb26f97d2830aa227fdcdad"
SERIES_2_EPOCH_5_MERGE_COMMIT = "c41f333419a58731e85d23b74cffea0fca564c5d"
SERIES_2_EPOCH_6_LANDING_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch6-registered-gate-landing-report-20260827.json"
)
SERIES_2_EPOCH_6_LANDING_SHA256 = "ceffb325dc69f04a2158fe94bead7d841602613a1e2dc280d36bfced7e6ce6fc"
SERIES_2_EPOCH_6_LANDING_COMMIT = "ef21ffe14fb6bdd90346ec3694cc986e46212e1d"
SERIES_2_EPOCH_6_MERGE_COMMIT = "0961b1a781c5618a8623155b3ea911de7e9717da"
SERIES_2_ATTEMPT_1_AUTHORITY = (
    "docs/phase4/reports/P4.2a-v2-2-rehearsal-attempt-000001-execution-authorization-20260826.json",
    "4cf5d3936754fabb155880b9936198b389efb7e167f61d6c42d4dcf75ae8f05b",
    "911b6c5695a2e0014546edf9ce919d9d8922586e",
)
SERIES_2_ATTEMPT_2_AUTHORITY = (
    "docs/phase4/reports/P4.2a-v2-2-rehearsal-attempt-000002-execution-authorization-20260827.json",
    "371d92b946bf9f1f2e3ea67bf5cd8a47bc73190df33b89b8d404f92c12c97138",
    "2877f24843c69ec295f7fcb5ffe19ffd81371144",
)
SERIES_2_EPOCH_5_SURFACE_AUTHORITY = (
    "docs/phase4/reports/P4.2a-v2-2-series-2-epoch5-surface-authority-20260823.json",
    "b97dce798eed5be8450e462cfdfccde949677c823c867ed35b6738dc5f3f4270",
    "5bea28957e873857e7bca6dd30f7226d8b09bbf7",
)
SERIES_2_EPOCH_5_REVIEW = (
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch5-implementation-independent-review-20260825.json",
    "cd220ea474e7e7f92e85b42411f03274352d4a6b7323a41d68eb8ca4626324f2",
    SERIES_2_EPOCH_5_MERGE_COMMIT,
)
SERIES_2_EPOCH_6_SURFACE_AUTHORITY = (
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch6-surface-authority-20260826.json",
    "b2a0e1c3aae4b6b826b522aa74472415b1b782990326301aa68e467eadc45a92",
    "3ccc2f267a05137edf86c5eb72f82e0057d74f98",
)
SERIES_2_EPOCH_6_REVIEW = (
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch6-implementation-independent-review-20260827.json",
    "84c6fab7ca36087b656cda17351da298a8bc4a7b4093a059f91a085b286d26e4",
    SERIES_2_EPOCH_6_MERGE_COMMIT,
)
RELEASE_REVIEW_REQUEST_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-2-implementation-and-execution-review-request-20260811.json"
)
RELEASE_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-2-release-authorization-20260811.json"
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
VOID_EPOCH_ONE_REVIEW_SHA256 = "e348bbc6c2976d473bf2b8e5b280784fd45ff7ae1ba7d7a4119309eb178b16cf"
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
LEGACY_OFFICIAL_SERIES_TOKEN = "35ba1b83a9b187817d7a591758e1c131e867fcd37917cba0ab196799fff832ef"
LEGACY_OFFICIAL_LEDGER_ROOT = REGISTERED_PROJECT_ROOT.parent / (
    ".alphapilot-p4-2a-v2-2-execution-claim-" + LEGACY_OFFICIAL_SERIES_TOKEN
)
OFFICIAL_SERIES_TOKEN = "2543d679819f96958baf747ef61dda2044013a0b00a9cb824c0d7675640d9f93"
OFFICIAL_PRIMARY_SERIES_CONTAINER = Path(
    "/Users/ouyangduning/AlphaPilot-EVIDENCE-DO-NOT-DELETE/P4.2a/v2.2/"
    "SERIES-000002-" + OFFICIAL_SERIES_TOKEN
)
OFFICIAL_LEDGER_ROOT = OFFICIAL_PRIMARY_SERIES_CONTAINER / "PRIMARY-LEDGER-DO-NOT-DELETE"
OFFICIAL_PRIMARY_RECEIPT_ROOT = OFFICIAL_PRIMARY_SERIES_CONTAINER / "MIRROR-RECEIPTS-DO-NOT-DELETE"
OFFICIAL_SECONDARY_SERIES_CONTAINER = Path(
    "/Users/ouyangduning/AlphaPilot-EVIDENCE-MIRROR-DO-NOT-DELETE/P4.2a/v2.2/"
    "SERIES-000002-" + OFFICIAL_SERIES_TOKEN
)
OFFICIAL_SECONDARY_SNAPSHOT_ROOT = (
    OFFICIAL_SECONDARY_SERIES_CONTAINER / "SEALED-LEDGER-SNAPSHOTS-DO-NOT-DELETE"
)
OFFICIAL_SECONDARY_RECEIPT_ROOT = (
    OFFICIAL_SECONDARY_SERIES_CONTAINER / "MIRROR-RECEIPTS-DO-NOT-DELETE"
)
OFFICIAL_PRIMARY_RECOVERY_CONTAINER = Path(
    "/Users/ouyangduning/AlphaPilot-EVIDENCE-DO-NOT-DELETE/P4.2a/v2.2/"
    "BUNDLE-RECOVERY-SERIES-000002-" + OFFICIAL_SERIES_TOKEN
)
OFFICIAL_SECONDARY_RECOVERY_CONTAINER = Path(
    "/Users/ouyangduning/AlphaPilot-EVIDENCE-MIRROR-DO-NOT-DELETE/P4.2a/v2.2/"
    "BUNDLE-RECOVERY-SERIES-000002-" + OFFICIAL_SERIES_TOKEN
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
MIRROR_INVENTORY_PREFIX = b"p4.2a-rehearsal-v2.2-mirror-inventory-v1\0"
MIRROR_RECEIPT_SCHEMA = "p4.2a-v2-2-series-2-mirror-verification-v1"


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


def _assert_recovery_work_bound(counters: Mapping[str, int]) -> None:
    expected_fields = (
        "git_objects_read",
        "recursive_bytes_hashed",
        "sealed_snapshot_files_visited",
        "bundle_bytes_copied",
    )
    if expected_fields != RECOVERY_WORK_COUNTER_FIELDS or set(counters) != set(expected_fields):
        raise RehearsalV22Error("recovery work counters have a nonregistered field set")
    for field in expected_fields:
        observed = counters[field]
        limit = RECOVERY_WORK_LIMITS.get(field)
        if (
            isinstance(observed, bool)
            or not isinstance(observed, int)
            or observed < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit <= 0
            or observed > limit
        ):
            raise RehearsalV22Error(f"recovery work bound exceeded: {field}")


class _RecoveryWorkTracker:
    """Incrementally account registered recovery work without changing evidence schemas."""

    def __init__(self, initial: Mapping[str, int] | None = None) -> None:
        source = (
            {field: 0 for field in RECOVERY_WORK_COUNTER_FIELDS}
            if initial is None
            else dict(initial)
        )
        _assert_recovery_work_bound(source)
        self._counters = source
        self.git_subprocesses_started = 0
        self.git_object_reads = 0

    def snapshot(self) -> dict[str, int]:
        result = {
            field: self._counters[field] for field in RECOVERY_WORK_COUNTER_FIELDS
        }
        _assert_recovery_work_bound(result)
        return result

    def charge_git(self, *, subprocesses: int = 0, object_reads: int = 0) -> None:
        if (
            isinstance(subprocesses, bool)
            or not isinstance(subprocesses, int)
            or subprocesses < 0
            or isinstance(object_reads, bool)
            or not isinstance(object_reads, int)
            or object_reads < 0
        ):
            raise RehearsalV22Error("recovery Git work charge is invalid")
        increment = subprocesses + object_reads
        prospective = self.snapshot()
        prospective["git_objects_read"] += increment
        _assert_recovery_work_bound(prospective)
        self._counters = prospective
        self.git_subprocesses_started += subprocesses
        self.git_object_reads += object_reads
        if (
            self.git_subprocesses_started + self.git_object_reads
            > self._counters["git_objects_read"]
        ):
            raise RehearsalV22Error("recovery Git work component accounting drifted")

    def add_registered(self, counters: Mapping[str, int]) -> None:
        _assert_recovery_work_bound(counters)
        prospective = self.snapshot()
        for field in RECOVERY_WORK_COUNTER_FIELDS:
            prospective[field] += counters[field]
        _assert_recovery_work_bound(prospective)
        self._counters = prospective


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


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


@contextmanager
def _held_directory_identity(path: Path, label: str) -> Iterator[int]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise RehearsalV22Error(f"{label} directory is unavailable") from exc
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RehearsalV22Error(f"{label} directory cannot be held without aliases") from exc
    try:
        opened = os.fstat(descriptor)
        raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, b"\0" * 1024)
        terminator = raw.find(b"\0")
        descriptor_path = Path(os.fsdecode(raw[:terminator]))
        if (
            terminator <= 0
            or descriptor_path != path.absolute()
            or not stat.S_ISDIR(opened.st_mode)
            or _stat_identity(opened) != _stat_identity(before)
            or path.is_symlink()
        ):
            raise RehearsalV22Error(f"{label} held directory identity drifted")
        yield descriptor
        after_descriptor = os.fstat(descriptor)
        after_path = path.lstat()
        if (
            _stat_identity(after_descriptor) != _stat_identity(before)
            or _stat_identity(after_path) != _stat_identity(before)
            or path.is_symlink()
        ):
            raise RehearsalV22Error(f"{label} directory changed while held")
    except OSError as exc:
        raise RehearsalV22Error(f"{label} held directory verification failed") from exc
    finally:
        os.close(descriptor)


def _regular_bytes(path: Path, label: str, *, allow_zero: bool = True) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise RehearsalV22Error(f"{label} is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or path.resolve(strict=True) != path.absolute()
        or (not allow_zero and before.st_size == 0)
    ):
        raise RehearsalV22Error(f"{label} is not one unaliased regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RehearsalV22Error(f"{label} cannot be opened without following aliases") from exc
    try:
        opened = os.fstat(descriptor)
        raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, b"\0" * 1024)
        terminator = raw.find(b"\0")
        descriptor_path = Path(os.fsdecode(raw[:terminator]))
        if (
            terminator <= 0
            or descriptor_path != path.absolute()
            or _stat_identity(opened) != _stat_identity(before)
        ):
            raise RehearsalV22Error(f"{label} descriptor identity drifted before read")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
        after_descriptor = os.fstat(descriptor)
        after_path = path.lstat()
        if (
            len(payload) != before.st_size
            or _stat_identity(after_descriptor) != _stat_identity(before)
            or _stat_identity(after_path) != _stat_identity(before)
            or path.is_symlink()
        ):
            raise RehearsalV22Error(f"{label} identity or bytes drifted during read")
    except OSError as exc:
        raise RehearsalV22Error(f"{label} descriptor read failed") from exc
    finally:
        os.close(descriptor)
    return payload


def _fixed_launcher_bytes() -> bytes:
    """Read the frozen venv launcher while preserving its intentional symlink path."""

    try:
        launcher_metadata = FIXED_PYTHON_LAUNCHER.lstat()
        resolved = FIXED_PYTHON_LAUNCHER.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except OSError as exc:
        raise RehearsalV22Error("fixed Python launcher is unavailable") from exc
    if (
        not (stat.S_ISLNK(launcher_metadata.st_mode) or stat.S_ISREG(launcher_metadata.st_mode))
        or resolved.is_symlink()
        or not stat.S_ISREG(resolved_metadata.st_mode)
        or resolved_metadata.st_nlink != 1
    ):
        raise RehearsalV22Error("fixed Python launcher chain is not one regular executable")
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


def _write_exclusive_at(
    parent_descriptor: int,
    path: Path,
    payload: bytes,
    *,
    mode: int = 0o600,
) -> None:
    policy = _AUDIT_POLICY.get()
    if policy is None or not _audit_policy_is_issued(policy):
        raise RehearsalV22Error("create-only openat lacks an issued audit policy")
    entry_name = path.name
    if (
        not entry_name
        or entry_name in {".", ".."}
        or "/" in entry_name
        or _audited_directory_from_fd(parent_descriptor) / entry_name != path
    ):
        raise RehearsalV22Error("create-only openat target escaped its held parent")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = _open_exclusive_at_issued(
        policy,
        parent_descriptor=parent_descriptor,
        entry_name=entry_name,
        absolute_path=path,
        flags=flags,
        mode=mode,
        payload_bytes=len(payload),
        payload_sha256=_sha256(payload),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != mode
            or _descriptor_path(descriptor, label="create-only openat file") != path.absolute()
        ):
            raise RehearsalV22Error("create-only openat file identity or mode drifted")
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        path_after = os.stat(
            entry_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            _stat_identity(after) != _stat_identity(path_after)
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or after.st_size != len(payload)
        ):
            raise RehearsalV22Error("create-only openat file changed during write")
    finally:
        os.close(descriptor)
    read_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    read_descriptor = os.open(
        entry_name,
        read_flags,
        dir_fd=parent_descriptor,
    )
    try:
        read_opened = os.fstat(read_descriptor)
        if (
            _stat_identity(read_opened) != _stat_identity(after)
            or _descriptor_path(
                read_descriptor,
                label="create-only openat readback",
            )
            != path.absolute()
        ):
            raise RehearsalV22Error("create-only openat readback identity drifted")
        observed_chunks: list[bytes] = []
        while True:
            chunk = os.read(read_descriptor, 1024 * 1024)
            if not chunk:
                break
            observed_chunks.append(chunk)
        observed_payload = b"".join(observed_chunks)
        read_after = os.fstat(read_descriptor)
        if (
            _stat_identity(read_after) != _stat_identity(read_opened)
            or observed_payload != payload
            or _sha256(observed_payload) != _sha256(payload)
        ):
            raise RehearsalV22Error("create-only openat readback bytes drifted")
    finally:
        os.close(read_descriptor)
    os.fsync(parent_descriptor)


def _tree_fingerprint_with_work(root: Path) -> tuple[dict[str, str], int, int]:
    if not os.path.lexists(root):
        return {".": "absent"}, 0, 0
    result: dict[str, str] = {}
    recursive_bytes = 0
    files_visited = 0
    root_metadata = root.lstat()
    if root.is_symlink():
        return {".": f"symlink:{os.readlink(root)}"}, 0, 0
    if root.is_file():
        payload = root.read_bytes()
        return (
            {".": f"file:{_sha256(payload)}:{root_metadata.st_mode:o}"},
            len(payload),
            1,
        )
    if not root.is_dir():
        return {".": f"special:{root_metadata.st_mode:o}"}, 0, 0
    result["."] = f"directory:{stat.S_IMODE(root_metadata.st_mode):04o}"
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().encode()
    ):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if path.is_symlink():
            result[relative] = f"symlink:{os.readlink(path)}"
        elif path.is_file():
            payload = path.read_bytes()
            recursive_bytes += len(payload)
            files_visited += 1
            result[relative] = (
                f"file:{_sha256(payload)}:{stat.S_IMODE(metadata.st_mode):04o}:{metadata.st_nlink}"
            )
        elif path.is_dir():
            result[relative] = f"directory:{stat.S_IMODE(metadata.st_mode):04o}"
        else:
            result[relative] = f"special:{metadata.st_mode:o}"
    return result, recursive_bytes, files_visited


def _tree_fingerprint(root: Path) -> dict[str, str]:
    return _tree_fingerprint_with_work(root)[0]


@dataclass(frozen=True)
class ExecutionBinding:
    mode: ExecutionMode
    project_root: Path
    shim_path: Path
    action_authorization_path: Path
    destination: Path
    series_token_sha256: str
    ledger_root: Path
    primary_series_container: Path
    primary_receipt_root: Path
    secondary_series_container: Path
    secondary_snapshot_root: Path
    secondary_receipt_root: Path


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
class AuthorityCensusSpec:
    reference: AuthorityReference
    role: AuthorityCensusRole
    declared_landing_projection_commit: str | None


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
            if (
                history.records
                and history.records[-1].outcome == "INCOMPLETE_UNTERMINALIZED"
                and history.records[-1].terminal_path is None
            ):
                _validate_hot_second_copy_commitment(
                    binding,
                    history,
                    allow_unmirrored_final=True,
                )
            else:
                _validate_hot_second_copy_commitment(binding, history)
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
            if authorization.ordinal == len(history.records):
                target_presence = tuple(
                    os.path.lexists(path) for path in _final_mirror_targets(binding, history)
                )
                if target_presence == (True, True, True):
                    _validate_hot_second_copy_commitment(binding, history)
                elif target_presence == (False, False, False):
                    _validate_hot_second_copy_commitment(
                        binding,
                        history,
                        allow_unmirrored_final=True,
                    )
                else:
                    raise RehearsalV22Error(
                        "current capability attempt has partial mirror artifacts"
                    )
            else:
                _validate_hot_second_copy_commitment(binding, history)
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
            matches = [active for active in replay_observation_registry if active.token is token]
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
                active for active in replay_observation_registry if active.token is not token
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
            replacement if active is target else active for active in replay_observation_registry
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
        SERIES_2_TOKEN_SEED_SHA256.lower()
        + "\0"
        + REHEARSAL_ID
        + "\0"
        + destination.absolute().as_posix()
    ).encode("utf-8")
    if destination == OFFICIAL_DESTINATION and len(material) != 232:
        raise RehearsalV22Error("series-2 token material length drifted")
    return _sha256(material)


def _disposable_storage_roots(
    project_root: Path,
    token: str,
) -> tuple[Path, Path, Path, Path, Path]:
    parent = project_root.parent
    primary_container = (
        parent
        / f"{project_root.name}-EVIDENCE-DO-NOT-DELETE"
        / "P4.2a/v2.2"
        / f"SERIES-000002-{token}"
    )
    secondary_container = (
        parent
        / f"{project_root.name}-EVIDENCE-MIRROR-DO-NOT-DELETE"
        / "P4.2a/v2.2"
        / f"SERIES-000002-{token}"
    )
    return (
        primary_container,
        primary_container / "PRIMARY-LEDGER-DO-NOT-DELETE",
        primary_container / "MIRROR-RECEIPTS-DO-NOT-DELETE",
        secondary_container,
        secondary_container / "SEALED-LEDGER-SNAPSHOTS-DO-NOT-DELETE",
    )


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
    if root == REGISTERED_PROJECT_ROOT:
        mode: ExecutionMode = "REGISTERED_OFFICIAL"
        primary_container = OFFICIAL_PRIMARY_SERIES_CONTAINER
        ledger = OFFICIAL_LEDGER_ROOT
        primary_receipts = OFFICIAL_PRIMARY_RECEIPT_ROOT
        secondary_container = OFFICIAL_SECONDARY_SERIES_CONTAINER
        secondary_snapshots = OFFICIAL_SECONDARY_SNAPSHOT_ROOT
        secondary_receipts = OFFICIAL_SECONDARY_RECEIPT_ROOT
        if (
            destination != OFFICIAL_DESTINATION
            or token != OFFICIAL_SERIES_TOKEN
            or ledger != OFFICIAL_LEDGER_ROOT
        ):
            raise RehearsalV22Error("official v2.2 execution binding derivation drifted")
    else:
        mode = "DISPOSABLE_FULL_SHAPE_TEST"
        canonical = REGISTERED_PROJECT_ROOT
        (
            primary_container,
            ledger,
            primary_receipts,
            secondary_container,
            secondary_snapshots,
        ) = _disposable_storage_roots(root, token)
        secondary_receipts = secondary_container / "MIRROR-RECEIPTS-DO-NOT-DELETE"
        if (
            root.is_relative_to(canonical)
            or canonical.is_relative_to(root)
            or destination == OFFICIAL_DESTINATION
            or any(
                path
                in {
                    OFFICIAL_PRIMARY_SERIES_CONTAINER,
                    OFFICIAL_LEDGER_ROOT,
                    OFFICIAL_PRIMARY_RECEIPT_ROOT,
                    OFFICIAL_SECONDARY_SERIES_CONTAINER,
                    OFFICIAL_SECONDARY_SNAPSHOT_ROOT,
                    OFFICIAL_SECONDARY_RECEIPT_ROOT,
                }
                for path in (
                    primary_container,
                    ledger,
                    primary_receipts,
                    secondary_container,
                    secondary_snapshots,
                    secondary_receipts,
                )
            )
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
        primary_series_container=primary_container,
        primary_receipt_root=primary_receipts,
        secondary_series_container=secondary_container,
        secondary_snapshot_root=secondary_snapshots,
        secondary_receipt_root=secondary_receipts,
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


def _registered_storage_directory(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RehearsalV22Error(f"{label} is not owner-provisioned") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
        or resolved != path.absolute()
    ):
        raise RehearsalV22Error(f"{label} identity, owner, or mode drifted")
    return resolved


def _storage_directory_evidence(path: Path, label: str) -> JsonObject:
    _registered_storage_directory(path, label)
    metadata = path.lstat()
    return {
        "path": path.as_posix(),
        "owner_uid": metadata.st_uid,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode_octal": "0700",
        "non_symlink": True,
        "canonical_unaliased": True,
    }


def _validate_registered_storage_roots(binding: ExecutionBinding) -> None:
    primary = _registered_storage_directory(
        binding.primary_series_container,
        "series-2 primary container",
    )
    secondary = _registered_storage_directory(
        binding.secondary_series_container,
        "series-2 secondary container",
    )
    expected = (
        binding.primary_series_container / "PRIMARY-LEDGER-DO-NOT-DELETE",
        binding.primary_series_container / "MIRROR-RECEIPTS-DO-NOT-DELETE",
        binding.secondary_series_container / "SEALED-LEDGER-SNAPSHOTS-DO-NOT-DELETE",
        binding.secondary_series_container / "MIRROR-RECEIPTS-DO-NOT-DELETE",
    )
    if expected != (
        binding.ledger_root,
        binding.primary_receipt_root,
        binding.secondary_snapshot_root,
        binding.secondary_receipt_root,
    ):
        raise RehearsalV22Error("series-2 storage leaf binding drifted")
    protected = (
        binding.project_root,
        binding.destination,
        PROTECTED_HELDOUT_ROOT,
    )
    if (
        primary == secondary
        or (primary.stat().st_dev, primary.stat().st_ino)
        == (secondary.stat().st_dev, secondary.stat().st_ino)
        or primary.is_relative_to(secondary)
        or secondary.is_relative_to(primary)
        or any(
            primary == path
            or secondary == path
            or primary.is_relative_to(path)
            or secondary.is_relative_to(path)
            or path.is_relative_to(primary)
            or path.is_relative_to(secondary)
            for path in protected
        )
    ):
        raise RehearsalV22Error("series-2 evidence roots overlap protected state")
    if binding.mode == "REGISTERED_OFFICIAL" and (
        binding.primary_series_container != OFFICIAL_PRIMARY_SERIES_CONTAINER
        or binding.secondary_series_container != OFFICIAL_SECONDARY_SERIES_CONTAINER
        or os.path.lexists(LEGACY_OFFICIAL_LEDGER_ROOT)
        or os.path.lexists(V2_1_EMPTY_CLAIM)
    ):
        raise RehearsalV22Error(
            "official series-2 storage binding drifted or a retired root reappeared"
        )


def _read_only_storage_preflight(binding: ExecutionBinding) -> JsonObject:
    _validate_registered_storage_roots(binding)
    leaves = {
        "primary_ledger": binding.ledger_root,
        "primary_receipts": binding.primary_receipt_root,
        "secondary_snapshots": binding.secondary_snapshot_root,
        "secondary_receipts": binding.secondary_receipt_root,
    }
    presence = {name: os.path.lexists(path) for name, path in leaves.items()}
    mirrored_history: JsonObject | None = None
    if not presence["primary_ledger"]:
        present = [name for name, value in presence.items() if value]
        if present:
            raise RehearsalV22Error(
                "series-2 fresh-root preflight found partial mirror leaves: "
                + ", ".join(sorted(present))
            )
        storage_state = "FRESH_SERIES_ALL_REGISTERED_LEAVES_ABSENT"
    else:
        history = validate_live_history(binding)
        if not history.records:
            raise RehearsalV22Error("series-2 primary ledger exists without an allocated attempt")
        final = history.records[-1]
        pending_incomplete = (
            final.outcome == "INCOMPLETE_UNTERMINALIZED" and final.terminal_path is None
        )
        observed_mirror_presence = (
            presence["primary_receipts"],
            presence["secondary_snapshots"],
            presence["secondary_receipts"],
        )
        final_targets = _final_mirror_targets(binding, history)
        final_target_presence = tuple(os.path.lexists(path) for path in final_targets)
        if pending_incomplete and final_target_presence == (False, False, False):
            expected_presence = (
                (False, False, False) if len(history.records) == 1 else (True, True, True)
            )
            if observed_mirror_presence != expected_presence:
                raise RehearsalV22Error(
                    "series-2 pending-incomplete preflight found partial mirror leaves"
                )
            receipts = _validate_second_copy_history(
                binding,
                history,
                allow_unmirrored_final=True,
            )
            storage_state = "EXISTING_FINAL_INCOMPLETE_PENDING_LOCKED_MIRROR"
        else:
            if observed_mirror_presence != (True, True, True) or final_target_presence != (
                True,
                True,
                True,
            ):
                raise RehearsalV22Error(
                    "series-2 established-root preflight found partial mirror artifacts"
                )
            receipts = _validate_second_copy_history(binding, history)
            storage_state = "EXISTING_FULLY_MIRRORED"
        mirrored_history = {
            "attempt_count": len(history.records),
            "history_root_sha256": history.history_root_sha256,
            "live_ledger_root_sha256": history.live_ledger_root_sha256,
            "receipt_count": len(receipts),
            "series_closed": history.series_closed,
        }
    if os.path.lexists(binding.destination):
        raise RehearsalV22Error("series-2 registered-root preflight destination exists")
    if binding.mode == "REGISTERED_OFFICIAL" and (
        os.path.lexists(LEGACY_OFFICIAL_LEDGER_ROOT) or os.path.lexists(V2_1_EMPTY_CLAIM)
    ):
        raise RehearsalV22Error(
            "series-2 registered-root preflight found a lost ledger or retired claim"
        )
    return {
        "primary_container": _storage_directory_evidence(
            binding.primary_series_container,
            "series-2 primary container",
        ),
        "secondary_container": _storage_directory_evidence(
            binding.secondary_series_container,
            "series-2 secondary container",
        ),
        "containers_non_overlapping": True,
        "storage_state": storage_state,
        "registered_leaf_state": {
            name: ("PRESENT_VERIFIED" if presence[name] else "ABSENT")
            for name in sorted(leaves, key=lambda value: value.encode("utf-8"))
        },
        "mirrored_history": mirrored_history,
        "bundle_destination_absent": True,
        "lost_series_ledger_absent": (
            not os.path.lexists(LEGACY_OFFICIAL_LEDGER_ROOT)
            if binding.mode == "REGISTERED_OFFICIAL"
            else True
        ),
        "retired_v2_1_claim_absent": (
            not os.path.lexists(V2_1_EMPTY_CLAIM) if binding.mode == "REGISTERED_OFFICIAL" else True
        ),
        "paths_created": 0,
    }


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
    work_tracker: _RecoveryWorkTracker | None = None,
    object_reads: int = 0,
) -> subprocess.CompletedProcess[bytes]:
    """Run one hardened Git operation; exposed for the independent validator."""

    root = project_root.absolute()
    _validate_git_metadata_authority(root)
    if work_tracker is not None:
        work_tracker.charge_git(subprocesses=1, object_reads=object_reads)
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


def _git_bytes(
    project_root: Path,
    *arguments: str,
    work_tracker: _RecoveryWorkTracker | None = None,
    object_reads: int = 0,
) -> bytes:
    completed = _git_completed(
        project_root,
        *arguments,
        work_tracker=work_tracker,
        object_reads=object_reads,
    )
    if completed.returncode != 0:
        raise RehearsalV22Error(f"hardened Git operation failed: {' '.join(arguments[:3])}")
    return completed.stdout


def _git_is_ancestor(
    project_root: Path,
    ancestor: str,
    descendant: str,
    *,
    work_tracker: _RecoveryWorkTracker | None = None,
) -> bool:
    if not _lower_hex(ancestor, 40) or not _lower_hex(descendant, 40):
        raise RehearsalV22Error("Git ancestry argument is not one full lowercase commit")
    completed = _git_completed(
        project_root,
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
        work_tracker=work_tracker,
        object_reads=2,
    )
    if completed.stderr or completed.returncode not in {0, 1}:
        raise RehearsalV22Error("hardened Git ancestry proof failed")
    return completed.returncode == 0


def _git_commit(
    project_root: Path,
    value: object,
    label: str,
    *,
    work_tracker: _RecoveryWorkTracker | None = None,
) -> str:
    if not _lower_hex(value, 40):
        raise RehearsalV22Error(f"{label} is not one full lowercase Git commit")
    commit = cast(str, value)
    observed = (
        _git_bytes(
            project_root,
            "rev-parse",
            "--verify",
            f"{commit}^{{commit}}",
            work_tracker=work_tracker,
            object_reads=1,
        )
        .decode("ascii", errors="strict")
        .strip()
    )
    if observed != commit:
        raise RehearsalV22Error(f"{label} does not identify one exact commit")
    return commit


def _git_blob(
    project_root: Path,
    commit: str,
    relative: str,
    *,
    work_tracker: _RecoveryWorkTracker | None = None,
) -> bytes:
    _git_commit(project_root, commit, "blob commit", work_tracker=work_tracker)
    _relative_text(relative, "Git blob path")
    kind = _git_bytes(
        project_root,
        "cat-file",
        "-t",
        f"{commit}:{relative}",
        work_tracker=work_tracker,
        object_reads=1,
    ).strip()
    payload = _git_bytes(
        project_root,
        "show",
        f"{commit}:{relative}",
        work_tracker=work_tracker,
        object_reads=1,
    )
    if kind != b"blob":
        raise RehearsalV22Error(f"Git object is not one blob: {relative}")
    return payload


def _git_optional_blob_epoch_7(
    project_root: Path,
    commit: str,
    relative: str,
    *,
    work_tracker: _RecoveryWorkTracker | None = None,
) -> bytes | None:
    resolved = _git_commit(
        project_root,
        commit,
        "optional lineage blob commit",
        work_tracker=work_tracker,
    )
    _relative_text(relative, "optional lineage blob path")
    output = _git_bytes(
        project_root,
        "ls-tree",
        "-z",
        "--full-tree",
        resolved,
        "--",
        relative,
        work_tracker=work_tracker,
        object_reads=1,
    )
    if not output:
        return None
    rows = output.removesuffix(b"\0").split(b"\0")
    if len(rows) != 1:
        raise RehearsalV22Error("lineage optional Git blob lookup is ambiguous")
    metadata, separator, raw_path = rows[0].partition(b"\t")
    fields = metadata.split()
    if (
        separator != b"\t"
        or raw_path.decode("utf-8", errors="strict") != relative
        or len(fields) != 3
        or fields[0] not in {b"100644", b"100755"}
        or fields[1] != b"blob"
        or re.fullmatch(rb"[0-9a-f]{40}", fields[2]) is None
    ):
        raise RehearsalV22Error("lineage optional Git blob row is malformed")
    return _git_blob(project_root, resolved, relative, work_tracker=work_tracker)


def _git_parents_epoch_7(
    project_root: Path,
    commit: str,
    *,
    work_tracker: _RecoveryWorkTracker | None = None,
) -> tuple[str, ...]:
    resolved = _git_commit(
        project_root,
        commit,
        "lineage census commit",
        work_tracker=work_tracker,
    )
    fields = (
        _git_bytes(
            project_root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            resolved,
            "--",
            work_tracker=work_tracker,
            object_reads=1,
        )
        .decode("ascii", errors="strict")
        .strip()
        .split()
    )
    if not fields or fields[0] != resolved:
        raise RehearsalV22Error("lineage census parent row is malformed")
    return tuple(
        _git_commit(
            project_root,
            value,
            "lineage census parent",
            work_tracker=work_tracker,
        )
        for value in fields[1:]
    )


def _git_ref_snapshot(
    project_root: Path,
    *,
    work_tracker: _RecoveryWorkTracker | None = None,
) -> tuple[bytes, str, int]:
    payload = _git_bytes(
        project_root,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)",
        work_tracker=work_tracker,
    )
    rows = payload.splitlines()
    if any(row.count(b"\0") != 1 for row in rows) or rows != sorted(rows):
        raise RehearsalV22Error("real-lineage ref snapshot is malformed or unordered")
    if work_tracker is not None:
        work_tracker.charge_git(object_reads=len(rows))
    return payload, _sha256(payload), len(rows)


def _git_all_ref_commits(
    project_root: Path,
    *,
    work_tracker: _RecoveryWorkTracker,
) -> tuple[str, ...]:
    commits = tuple(
        line
        for line in _git_bytes(
            project_root,
            "rev-list",
            "--all",
            work_tracker=work_tracker,
        )
        .decode("ascii", errors="strict")
        .splitlines()
        if line
    )
    if (
        not commits
        or len(commits) != len(set(commits))
        or any(not _lower_hex(commit, 40) for commit in commits)
    ):
        raise RehearsalV22Error("real-lineage all-ref commit snapshot is malformed")
    work_tracker.charge_git(object_reads=len(commits))
    return commits


def _all_ref_path_touches(
    project_root: Path,
    relative: str,
    *,
    all_ref_commit_count: int,
    work_tracker: _RecoveryWorkTracker,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    history = _git_bytes(
        project_root,
        "log",
        "--all",
        "--diff-merges=first-parent",
        "--format=@@%H",
        "--name-status",
        "--find-renames",
        "--find-copies",
        "--",
        relative,
        work_tracker=work_tracker,
    ).decode("utf-8", errors="strict")
    touches: list[tuple[str, str, tuple[str, ...]]] = []
    active: str | None = None
    history_commit_count = 0
    for raw in history.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("@@"):
            history_commit_count += 1
            if history_commit_count > all_ref_commit_count:
                raise RehearsalV22Error(
                    "real-lineage path history exceeds its all-ref commit snapshot"
                )
            active = _git_commit(
                project_root,
                line[2:],
                "lineage touch commit",
                work_tracker=work_tracker,
            )
            continue
        fields = tuple(line.split("\t"))
        if active is None or len(fields) < 2:
            raise RehearsalV22Error("real-lineage path history is malformed")
        touches.append((active, fields[0], fields[1:]))
    return tuple(touches)


def _classify_unique_a_lineage(
    project_root: Path,
    spec: AuthorityCensusSpec,
    *,
    execution_head: str,
    all_ref_commits: tuple[str, ...] | None = None,
    work_tracker: _RecoveryWorkTracker | None = None,
) -> JsonObject:
    """Classify one source A plus only byte-identical first-parent projections."""

    root = project_root.absolute()
    tracker = _RecoveryWorkTracker() if work_tracker is None else work_tracker
    if all_ref_commits is None:
        all_ref_commits = _git_all_ref_commits(root, work_tracker=tracker)
    work_tracker = tracker
    head = _git_commit(
        root,
        execution_head,
        "lineage classification HEAD",
        work_tracker=work_tracker,
    )
    authority = spec.reference
    relative = _relative_text(authority.path, "lineage authority path")
    touches = _all_ref_path_touches(
        root,
        relative,
        all_ref_commit_count=len(all_ref_commits),
        work_tracker=work_tracker,
    )
    direct_sources = [
        commit
        for commit, status, paths in touches
        if status == "A"
        and paths == (relative,)
        and len(_git_parents_epoch_7(root, commit, work_tracker=work_tracker)) == 1
        and _git_optional_blob_epoch_7(
            root,
            _git_parents_epoch_7(root, commit, work_tracker=work_tracker)[0],
            relative,
            work_tracker=work_tracker,
        )
        is None
    ]
    if spec.role == "PINNED_LANDING_PROJECTION":
        declared = spec.declared_landing_projection_commit
        if declared is None or declared != authority.creating_commit:
            raise RehearsalV22Error("landing-projection spec does not pin its declared commit")
        matching_sources = [
            commit
            for commit in direct_sources
            if _sha256(_git_blob(root, commit, relative, work_tracker=work_tracker))
            == authority.sha256
        ]
        if len(matching_sources) != 1:
            raise RehearsalV22Error("landing projection has no unique logical source")
        source = matching_sources[0]
    elif spec.role == "DISCOVER_SOURCE_AFTER_PROJECTIONS":
        if len(direct_sources) != 1 or direct_sources[0] != authority.creating_commit:
            raise RehearsalV22Error("lineage source discovery is ambiguous or drifted")
        source = direct_sources[0]
    else:
        source = _git_commit(
            root,
            authority.creating_commit,
            "lineage pinned source",
            work_tracker=work_tracker,
        )
    pinned = _git_blob(
        root,
        authority.creating_commit,
        relative,
        work_tracker=work_tracker,
    )
    if _sha256(pinned) != authority.sha256 or _git_blob(
        root,
        source,
        relative,
        work_tracker=work_tracker,
    ) != pinned:
        raise RehearsalV22Error("lineage pinned source SHA drifted")
    classified: list[JsonObject] = []
    source_count = 0
    projection_count = 0
    for commit, status, paths in touches:
        parents = _git_parents_epoch_7(root, commit, work_tracker=work_tracker)
        first_status = status if paths == (relative,) else f"{status}:{'|'.join(paths)}"
        classification = "INVALID"
        source_is_ancestor = False
        second_parent_diff_empty = False
        raw_equal = (
            _git_optional_blob_epoch_7(
                root,
                commit,
                relative,
                work_tracker=work_tracker,
            )
            == pinned
        )
        if commit == source and status == "A" and paths == (relative,):
            if (
                len(parents) != 1
                or _git_optional_blob_epoch_7(
                    root,
                    parents[0],
                    relative,
                    work_tracker=work_tracker,
                )
                is not None
            ):
                raise RehearsalV22Error("lineage pinned source is not one direct status-A")
            classification = "PINNED_SOURCE"
            source_count += 1
        elif status == "A" and paths == (relative,) and len(parents) == 2:
            first, second = parents
            source_is_ancestor = _git_is_ancestor(
                root,
                source,
                second,
                work_tracker=work_tracker,
            )
            second_parent_diff_empty = not _git_bytes(
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-status",
                "--no-renames",
                second,
                commit,
                "--",
                relative,
                work_tracker=work_tracker,
                object_reads=2,
            )
            if (
                _git_optional_blob_epoch_7(
                    root,
                    first,
                    relative,
                    work_tracker=work_tracker,
                )
                is None
                and _git_optional_blob_epoch_7(
                    root,
                    second,
                    relative,
                    work_tracker=work_tracker,
                )
                == pinned
                and raw_equal
                and source_is_ancestor
                and second_parent_diff_empty
            ):
                classification = "FIRST_PARENT_MERGE_PROJECTION"
                projection_count += 1
        classified.append(
            {
                "commit": commit,
                "parents": list(parents),
                "first_parent_status": first_status,
                "classification": classification,
                "blob_sha256": _sha256(pinned) if raw_equal else None,
                "raw_bytes_equal_pinned": raw_equal,
                "source_is_ancestor_of_second_parent": source_is_ancestor,
                "second_parent_to_merge_path_diff_empty": second_parent_diff_empty,
            }
        )
    if source_count != 1 or any(row["classification"] == "INVALID" for row in classified):
        raise RehearsalV22Error(f"authority has an invalid all-ref Git touch: {relative}")
    if spec.role == "PINNED_LANDING_PROJECTION":
        projections = {
            cast(str, row["commit"])
            for row in classified
            if row["classification"] == "FIRST_PARENT_MERGE_PROJECTION"
        }
        if (
            spec.declared_landing_projection_commit is None
            or spec.declared_landing_projection_commit not in projections
        ):
            raise RehearsalV22Error("review landing projection differs from its declaration")
    elif spec.declared_landing_projection_commit is not None:
        raise RehearsalV22Error("non-review census role declared a landing projection")
    if spec.role == "PINNED_SOURCE_WITH_DESCENDANT_GRAPH":
        for commit in all_ref_commits:
            observed = _git_optional_blob_epoch_7(
                root,
                commit,
                relative,
                work_tracker=work_tracker,
            )
            if _git_is_ancestor(root, source, commit, work_tracker=work_tracker):
                if observed != pinned:
                    raise RehearsalV22Error("descendant authority bytes drifted")
            elif observed is not None:
                raise RehearsalV22Error("authority path exists outside source descendants")
    if not _git_is_ancestor(root, source, head, work_tracker=work_tracker):
        raise RehearsalV22Error("lineage pinned source is outside execution HEAD")
    classified.sort(key=lambda row: cast(str, row["commit"]).encode("ascii"))
    head_payload = _git_optional_blob_epoch_7(
        root,
        head,
        relative,
        work_tracker=work_tracker,
    )
    worktree = _regular_bytes(root / relative, f"lineage worktree {relative}")
    if head_payload != pinned or worktree != pinned:
        raise RehearsalV22Error("lineage pinned bytes differ at HEAD or worktree")
    return {
        "path": relative,
        "pinned_sha256": authority.sha256,
        "pinned_creating_commit": authority.creating_commit,
        "mode": spec.role,
        "logical_source_commit": source,
        "declared_landing_projection_commit": spec.declared_landing_projection_commit,
        "raw_touch_count": len(touches),
        "source_count": source_count,
        "projection_count": projection_count,
        "touches": classified,
        "execution_head_contains_source": True,
        "head_blob_sha256": _sha256(head_payload),
        "worktree_sha256": _sha256(worktree),
        "verdict": "PASS_ONE_LOGICAL_SOURCE_AND_ONLY_LAWFUL_PROJECTIONS",
    }


def _authority_census_registry(
    additional_references: Sequence[AuthorityCensusSpec | AuthorityReference],
) -> tuple[AuthorityCensusSpec, ...]:
    entries: list[AuthorityCensusSpec] = [
        AuthorityCensusSpec(
            AuthorityReference(path, digest, commit),
            "PINNED_SOURCE",
            None,
        )
        for path, digest, commit in CARRY_FORWARD_AUTHORITIES.values()
    ]
    entries.extend(
        (
            AuthorityCensusSpec(
                AuthorityReference(
                    INITIAL_SURFACE_REVIEW_RELATIVE.as_posix(),
                    INITIAL_SURFACE_REVIEW_SHA256,
                    INITIAL_SURFACE_REVIEW_COMMIT,
                ),
                "PINNED_SOURCE_WITH_DESCENDANT_GRAPH",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    PREREGISTRATION_RELATIVE.as_posix(),
                    PREREGISTRATION_SHA256,
                    PREREGISTRATION_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(INCIDENT_RELATIVE.as_posix(), INCIDENT_SHA256, INCIDENT_COMMIT),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    REMEDIATION_RELATIVE.as_posix(),
                    REMEDIATION_SHA256,
                    REMEDIATION_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    SCOPE_AUTHORIZATION_RELATIVE.as_posix(),
                    SCOPE_AUTHORIZATION_SHA256,
                    SCOPE_AUTHORIZATION_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    SERIES_2_PREREGISTRATION_RELATIVE.as_posix(),
                    SERIES_2_PREREGISTRATION_SHA256,
                    SERIES_2_PREREGISTRATION_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    SERIES_2_OWNER_DECISION_RELATIVE.as_posix(),
                    SERIES_2_OWNER_DECISION_SHA256,
                    SERIES_2_OWNER_DECISION_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    SERIES_2_LOSS_INCIDENT_RELATIVE.as_posix(),
                    SERIES_2_TOKEN_SEED_SHA256,
                    SERIES_2_LOSS_INCIDENT_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    VOID_EPOCH_ONE_ADJUDICATION_RELATIVE.as_posix(),
                    VOID_EPOCH_ONE_ADJUDICATION_SHA256,
                    VOID_EPOCH_ONE_ADJUDICATION_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    VOID_EPOCH_ONE_REVIEW_RELATIVE.as_posix(),
                    VOID_EPOCH_ONE_REVIEW_SHA256,
                    VOID_EPOCH_ONE_REVIEW_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    SERIES_2_EPOCH_5_LANDING_RELATIVE.as_posix(),
                    SERIES_2_EPOCH_5_LANDING_SHA256,
                    SERIES_2_EPOCH_5_LANDING_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(*SERIES_2_ATTEMPT_1_AUTHORITY),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(*SERIES_2_ATTEMPT_2_AUTHORITY),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(*SERIES_2_EPOCH_5_SURFACE_AUTHORITY),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(*SERIES_2_EPOCH_5_REVIEW),
                "PINNED_LANDING_PROJECTION",
                SERIES_2_EPOCH_5_REVIEW[2],
            ),
            AuthorityCensusSpec(
                AuthorityReference(*SERIES_2_EPOCH_6_SURFACE_AUTHORITY),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(*SERIES_2_EPOCH_6_REVIEW),
                "PINNED_LANDING_PROJECTION",
                SERIES_2_EPOCH_6_REVIEW[2],
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    SERIES_2_EPOCH_6_LANDING_RELATIVE.as_posix(),
                    SERIES_2_EPOCH_6_LANDING_SHA256,
                    SERIES_2_EPOCH_6_LANDING_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    EPOCH_7_ADJUDICATION_RELATIVE.as_posix(),
                    EPOCH_7_ADJUDICATION_SHA256,
                    EPOCH_7_ADJUDICATION_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    EPOCH_7_COMPANION_RELATIVE.as_posix(),
                    EPOCH_7_COMPANION_SHA256,
                    EPOCH_7_COMPANION_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    EPOCH_7_SURFACE_AUTHORITY_RELATIVE.as_posix(),
                    EPOCH_7_SURFACE_AUTHORITY_SHA256,
                    EPOCH_7_SURFACE_AUTHORITY_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(*EPOCH_7_LIVE_REVIEW_CARRY_FORWARD[:3]),
                EPOCH_7_LIVE_REVIEW_CARRY_FORWARD[3],
                EPOCH_7_LIVE_REVIEW_CARRY_FORWARD[4],
            ),
            AuthorityCensusSpec(
                AuthorityReference(*EPOCH_7_LANDING_CARRY_FORWARD[:3]),
                EPOCH_7_LANDING_CARRY_FORWARD[3],
                EPOCH_7_LANDING_CARRY_FORWARD[4],
            ),
            AuthorityCensusSpec(
                AuthorityReference(*EPOCH_7_REFUSED_Q_CARRY_FORWARD[:3]),
                EPOCH_7_REFUSED_Q_CARRY_FORWARD[3],
                EPOCH_7_REFUSED_Q_CARRY_FORWARD[4],
            ),
            AuthorityCensusSpec(
                AuthorityReference(*EPOCH_7_REFUSED_R_CARRY_FORWARD[:3]),
                EPOCH_7_REFUSED_R_CARRY_FORWARD[3],
                EPOCH_7_REFUSED_R_CARRY_FORWARD[4],
            ),
            AuthorityCensusSpec(
                AuthorityReference(*EPOCH_7_REFUSED_B_CARRY_FORWARD[:3]),
                EPOCH_7_REFUSED_B_CARRY_FORWARD[3],
                EPOCH_7_REFUSED_B_CARRY_FORWARD[4],
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    EPOCH_8_ADJUDICATION_RELATIVE.as_posix(),
                    EPOCH_8_ADJUDICATION_SHA256,
                    EPOCH_8_ADJUDICATION_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    EPOCH_8_COMPANION_RELATIVE.as_posix(),
                    EPOCH_8_COMPANION_SHA256,
                    EPOCH_8_COMPANION_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
            AuthorityCensusSpec(
                AuthorityReference(
                    EPOCH_8_SURFACE_AUTHORITY_RELATIVE.as_posix(),
                    EPOCH_8_SURFACE_AUTHORITY_SHA256,
                    EPOCH_8_SURFACE_AUTHORITY_COMMIT,
                ),
                "PINNED_SOURCE",
                None,
            ),
        )
    )
    entries.extend(
        value
        if isinstance(value, AuthorityCensusSpec)
        else AuthorityCensusSpec(value, "PINNED_SOURCE", None)
        for value in additional_references
    )
    by_path: dict[str, AuthorityCensusSpec] = {}
    for spec in entries:
        existing = by_path.get(spec.reference.path)
        if existing is not None and existing != spec:
            raise RehearsalV22Error("real-lineage registry has conflicting same-path bindings")
        by_path[spec.reference.path] = spec
    return tuple(by_path[path] for path in sorted(by_path, key=lambda value: value.encode("utf-8")))


def _real_lineage_census(
    project_root: Path,
    *,
    execution_head: str,
    additional_references: Sequence[AuthorityCensusSpec | AuthorityReference] = (),
    work_tracker: _RecoveryWorkTracker | None = None,
) -> JsonObject:
    root = project_root.absolute()
    tracker = _RecoveryWorkTracker() if work_tracker is None else work_tracker
    before_payload, before_sha, before_count = _git_ref_snapshot(
        root,
        work_tracker=tracker,
    )
    all_ref_commits = _git_all_ref_commits(root, work_tracker=tracker)
    registry = _authority_census_registry(additional_references)
    registry_payload = _canonical_json_bytes(
        [
            {
                "path": spec.reference.path,
                "pinned_sha256": spec.reference.sha256,
                "pinned_creating_commit": spec.reference.creating_commit,
                "mode": spec.role,
                "declared_landing_projection_commit": (spec.declared_landing_projection_commit),
            }
            for spec in registry
        ]
    )
    rows: list[JsonObject] = []
    for spec in registry:
        rows.append(
            _classify_unique_a_lineage(
                root,
                spec,
                execution_head=execution_head,
                all_ref_commits=all_ref_commits,
                work_tracker=tracker,
            )
        )
        _assert_recovery_work_bound(tracker.snapshot())
    after_payload, after_sha, after_count = _git_ref_snapshot(
        root,
        work_tracker=tracker,
    )
    if before_payload != after_payload or before_sha != after_sha or before_count != after_count:
        raise RehearsalV22Error("Git refs changed during the real-lineage census")
    projection_count = sum(cast(int, row["projection_count"]) for row in rows)
    return {
        "schema_version": EPOCH_8_LINEAGE_CENSUS_SCHEMA,
        "execution_head": _git_commit(
            root,
            execution_head,
            "real-lineage execution HEAD",
            work_tracker=tracker,
        ),
        "authority_registry_sha256": _sha256(registry_payload),
        "ref_snapshot_before_sha256": before_sha,
        "ref_snapshot_after_sha256": after_sha,
        "reference_count": len(registry),
        "row_count": len(rows),
        "source_count": len(rows),
        "projection_count": projection_count,
        "invalid_count": 0,
        "rows": rows,
        "effects": {
            "git_ref_write": False,
            "git_index_write": False,
            "git_worktree_write": False,
            "ledger_write": False,
            "mirror_write": False,
            "destination_write": False,
            "temporary_write": False,
            "network_access": False,
            "database_access": False,
            "pipeline_execution": False,
            "heldout_access": False,
        },
        "status": "PASS_REAL_LINEAGE_CENSUS",
    }


def _exact_contract_string_sequence(
    value: object,
    expected: tuple[str, ...],
    label: str,
) -> None:
    observed = _array(value, label)
    if (
        any(not isinstance(item, str) for item in observed)
        or len(observed) != len(set(cast(list[str], observed)))
        or tuple(observed) != expected
    ):
        raise RehearsalV22Error(f"{label} is not the exact ordered string contract")


def _exact_contract_object(
    value: object,
    expected_fields: frozenset[str],
    label: str,
) -> JsonObject:
    observed = _object(value, label)
    if set(observed) != expected_fields:
        raise RehearsalV22Error(f"{label} does not have the exact closed field set")
    return observed


def validate_epoch_7_recovery_contract(
    project_root: Path,
    *,
    execution_head: str,
) -> JsonObject:
    root = project_root.absolute()
    companion = AuthorityReference(
        EPOCH_7_COMPANION_RELATIVE.as_posix(),
        EPOCH_7_COMPANION_SHA256,
        EPOCH_7_COMPANION_COMMIT,
    )
    payload = _git_blob(root, companion.creating_commit, companion.path)
    current = _regular_bytes(root / companion.path, "epoch-7 recovery companion")
    if payload != current or _sha256(payload) != companion.sha256:
        raise RehearsalV22Error("epoch-7 recovery companion bytes drifted")
    document = _object(
        strict_json_loads(payload, source="epoch-7 recovery companion"),
        "epoch-7 recovery companion",
    )
    reviewed = _object(document.get("part_1_design_reviewed"), "epoch-7 design review")
    owner = _object(document.get("part_2_owner_approval"), "epoch-7 owner approval")
    contract = _object(document.get("epoch_7_recovery_contract"), "epoch-7 recovery contract")
    if (
        reviewed.get("verdict") != "PASS_DESIGN_REVIEW"
        or set(contract) != EPOCH_7_RECOVERY_CONTRACT_FIELDS
        or contract.get("schema_version") != EPOCH_7_RECOVERY_CONTRACT_SCHEMA
        or contract.get("implementation_epoch") != 7
        or owner.get("approved_surface")
        != [
            {"path": relative.as_posix(), "status": "M"}
            for relative in sorted(
                (
                    IMPLEMENTATION_RELATIVE,
                    VALIDATOR_RELATIVE,
                    RUNNER_TEST_RELATIVE,
                    VALIDATOR_TEST_RELATIVE,
                ),
                key=lambda value: value.as_posix().encode("utf-8"),
            )
        ]
    ):
        raise RehearsalV22Error("epoch-7 recovery contract or approved surface drifted")

    for relative, digest, size, commit, label in (
        (
            EPOCH_7_DESIGN_R1_RELATIVE.as_posix(),
            EPOCH_7_DESIGN_R1_SHA256,
            EPOCH_7_DESIGN_R1_BYTES,
            EPOCH_7_DESIGN_R1_COMMIT,
            "epoch-7 recovery design r1",
        ),
        (
            EPOCH_7_DESIGN_R2_RELATIVE.as_posix(),
            EPOCH_7_DESIGN_R2_SHA256,
            EPOCH_7_DESIGN_R2_BYTES,
            EPOCH_7_DESIGN_R2_COMMIT,
            "epoch-7 recovery design r2",
        ),
    ):
        design_payload = _git_blob(root, commit, relative)
        if len(design_payload) != size or _sha256(design_payload) != digest:
            raise RehearsalV22Error(f"{label} Git bytes drifted")

    governance = _exact_contract_object(
        contract.get("governing_adjudication"),
        frozenset({"path", "sha256", "creating_commit", "unique_a_history_verified"}),
        "epoch-7 governing adjudication",
    )
    if (
        governance
        != AuthorityReference(
            EPOCH_7_ADJUDICATION_RELATIVE.as_posix(),
            EPOCH_7_ADJUDICATION_SHA256,
            EPOCH_7_ADJUDICATION_COMMIT,
        ).as_json()
    ):
        raise RehearsalV22Error("epoch-7 governing adjudication binding drifted")

    q = _exact_contract_object(
        contract.get("recovery_review_request_contract"),
        frozenset(
            {
                "schema_version",
                "path_pattern",
                "topology",
                "exact_top_level_fields",
                "status_value",
                "nested_exact_field_sets",
                "rules",
            }
        ),
        "epoch-7 recovery Q contract",
    )
    r = _exact_contract_object(
        contract.get("recovery_authorization_contract"),
        frozenset(
            {
                "schema_version",
                "path_pattern",
                "topology",
                "exact_top_level_fields",
                "verdict_value",
                "counters",
                "nested_exact_field_sets",
                "effect_authorization_exact",
                "fixed_values",
            }
        ),
        "epoch-7 recovery R contract",
    )
    b = _exact_contract_object(
        contract.get("recovery_owner_binding_contract"),
        frozenset(
            {
                "schema_version",
                "path_pattern",
                "topology",
                "exact_top_level_fields",
                "nested_exact_field_sets",
                "fixed_values",
                "cli_operations",
                "bootstrap_order",
            }
        ),
        "epoch-7 recovery B contract",
    )
    claim = _exact_contract_object(
        contract.get("recovery_claim_contract"),
        frozenset(
            {
                "claim_name",
                "linearization",
                "started_schema_version",
                "started_exact_fields",
                "terminal_schema_version",
                "terminal_exact_fields",
                "outcomes",
                "crash_states",
            }
        ),
        "epoch-7 recovery claim contract",
    )
    receipt = _exact_contract_object(
        contract.get("bundle_mirror_receipt_contract"),
        frozenset({"schema_version", "exact_fields", "filename_pattern", "rules"}),
        "epoch-7 recovery receipt contract",
    )
    anchors = _exact_contract_object(
        contract.get("dual_byte_anchor_contract"),
        frozenset(
            {
                "historical_selected_anchor",
                "live_execution_anchor",
                "mode_enum",
                "recovered_publication_capability_exact_fields",
                "capability_required_values",
                "hook_disposition_authority",
                "release_truth_condition",
                "no_fallback",
            }
        ),
        "epoch-7 dual-anchor contract",
    )
    census = _exact_contract_object(
        contract.get("unique_a_and_lineage_census_contract"),
        frozenset(
            {
                "roles",
                "scanner_role_map",
                "projection_criteria",
                "rules",
                "census_schema_version",
                "census_exact_fields",
                "row_exact_fields",
                "touch_exact_fields",
                "timing",
            }
        ),
        "epoch-7 lineage census contract",
    )
    protected = _exact_contract_object(
        contract.get("protected_inputs_and_permitted_outputs"),
        frozenset(
            {
                "read_only_inputs",
                "permitted_recovery_writes",
                "recovery_containers",
                "container_rules",
                "consume_mode_effects",
                "forbidden_calls",
                "sealed_input_invariance",
            }
        ),
        "epoch-7 protected-input contract",
    )
    legacy = _exact_contract_object(
        contract.get("legacy_absence_and_locks"),
        frozenset(
            {"amendment_time_facts_permanently_false", "disclosure_rule", "epoch_table", "locks"}
        ),
        "epoch-7 legacy locks",
    )

    _exact_contract_string_sequence(
        q.get("exact_top_level_fields"),
        EPOCH_7_RECOVERY_REVIEW_REQUEST_FIELD_ORDER,
        "epoch-7 Q top-level fields",
    )
    _exact_contract_string_sequence(
        r.get("exact_top_level_fields"),
        RECOVERY_AUTHORIZATION_FIELD_ORDER,
        "epoch-7 R top-level fields",
    )
    _exact_contract_string_sequence(
        b.get("exact_top_level_fields"),
        RECOVERY_OWNER_BINDING_FIELD_ORDER,
        "epoch-7 B top-level fields",
    )
    _exact_contract_string_sequence(
        claim.get("started_exact_fields"),
        RECOVERY_STARTED_FIELD_ORDER,
        "epoch-7 claim started fields",
    )
    _exact_contract_string_sequence(
        claim.get("terminal_exact_fields"),
        RECOVERY_TERMINAL_FIELD_ORDER,
        "epoch-7 claim terminal fields",
    )
    _exact_contract_string_sequence(
        receipt.get("exact_fields"),
        RECOVERY_MIRROR_RECEIPT_FIELD_ORDER,
        "epoch-7 mirror receipt fields",
    )
    _exact_contract_string_sequence(
        anchors.get("recovered_publication_capability_exact_fields"),
        RECOVERED_PUBLICATION_CAPABILITY_FIELD_ORDER,
        "epoch-7 recovered-publication capability fields",
    )
    _exact_contract_string_sequence(
        census.get("census_exact_fields"),
        RECOVERY_CENSUS_FIELD_ORDER,
        "epoch-7 census fields",
    )
    _exact_contract_string_sequence(
        census.get("row_exact_fields"),
        RECOVERY_CENSUS_ROW_FIELD_ORDER,
        "epoch-7 census row fields",
    )
    _exact_contract_string_sequence(
        census.get("touch_exact_fields"),
        RECOVERY_CENSUS_TOUCH_FIELD_ORDER,
        "epoch-7 census touch fields",
    )

    q_nested = _object(q.get("nested_exact_field_sets"), "epoch-7 Q nested fields")
    r_nested = _object(r.get("nested_exact_field_sets"), "epoch-7 R nested fields")
    b_nested = _object(b.get("nested_exact_field_sets"), "epoch-7 B nested fields")
    q_expected: Mapping[str, tuple[str, ...]] = {
        "requester": ("identity", "role", "scope"),
        "landed_epoch_7": (
            "epoch",
            "implementation_commit",
            "owner_exact_surface_authorization",
            "independent_implementation_review",
            "merge_commit",
            "landing_report",
            "control_merkle_root_sha256",
            "control_record_count",
        ),
        "registered_read_only_recovery_preflight": (
            "exact_argv",
            "stdout_canonical_json",
            "stdout_sha256",
            "stdout_bytes",
            "stderr_bytes",
            "returncode",
            "status",
            "real_lineage_census",
        ),
        "preflight_before_after_equality": (
            "head",
            "control_surface",
            "git_refs",
            "official_ledger",
            "sealed_mirror",
            "destination",
            "heldout",
            "temporary_paths",
        ),
        "proposed_recovery_authorization": (
            "path",
            "document",
            "canonical_json_sha256",
            "bytes",
            "currently_effective",
        ),
        "requested_owner_action_time_confirmation": (
            "required_owner_identity",
            "requested_exact_confirmation",
            "delivery_channel",
            "confirmation_not_yet_received",
        ),
        "post_confirmation_plan_not_yet_executed": (
            "land_r",
            "land_b",
            "revalidate_start_census",
            "one_recovery_start",
            "zero_pipeline_start",
            "zero_automatic_retry",
        ),
        "current_locks": (
            "series_closed",
            "attempts_allocated",
            "selected_attempt_ordinal",
            "ledger_and_sealed_mirror_read_only",
            "destination_created",
            "bundle_recovery_authorization_created",
            "owner_confirmation_binding_created",
            "bundle_recovery_starts",
            "pipeline_starts_in_recovery",
            "automatic_retries_in_recovery",
            "recovery_claim_created",
            "recovered_bundle_mirror_created",
            "heldout_evaluation_attempts_consumed",
            "p4_2a_done",
            "p4_2b_unlocked",
            "p4_3_unlocked",
            "trading_unlocked",
        ),
    }
    r_expected: Mapping[str, tuple[str, ...]] = {
        "owner": ("identity", "approved", "scope"),
        "sealed_series": tuple(RECOVERY_SEALED_SERIES_FIELDS),
        "selected_files": ("started", "candidate", "terminal"),
        "selected_file_reference": ("relative_path", "sha256", "bytes"),
        "sealed_mirror": (
            "snapshot_count",
            "receipt_count",
            "latest_ordinal",
            "latest_snapshot_path",
            "primary_receipt_path",
            "secondary_receipt_path",
            "receipt_sha256",
            "receipt_bytes",
            "inventory_sha256",
            "file_count",
            "total_bytes",
            "paired_receipts_byte_identical",
        ),
        "execution_epoch": (
            "epoch",
            "implementation_commit",
            "owner_exact_surface_authorization",
            "independent_implementation_review",
            "merge_commit",
            "landing_report",
            "control_merkle_root_sha256",
            "control_record_count",
            "real_lineage_census",
            "latest_complete_landed_epoch_required",
            "current_control_bytes_required",
            "loaded_module_bytes_required",
        ),
        "real_lineage_census": (
            "schema_version",
            "execution_head",
            "reference_count",
            "row_count",
            "projection_count",
            "invalid_count",
            "canonical_json_sha256",
            "bytes",
            "result",
            "all_references_revalidated_at_start",
        ),
        "destination": (
            "absolute_path",
            "required_absent_before_start",
            "publication_mode",
            "bundle_schema_version",
            "expected_bundle_status",
            "recovery_storage",
        ),
        "recovery_storage": (
            "primary_recovery_container",
            "secondary_recovery_container",
            "claim_name_derived_from_authorization_sha256",
            "destination_stage_name_derived_from_authorization_sha256",
            "secondary_snapshot_stage_name_derived_from_authorization_sha256",
            "secondary_snapshot_name_derived_from_authorization_sha256_and_tree_root",
            "receipt_name_derived_from_authorization_sha256_and_tree_root",
            "destination_publication_mode",
            "secondary_snapshot_publication_mode",
            "primary_receipt_publication_mode",
            "secondary_receipt_publication_mode",
            "paired_receipts_required",
        ),
        "interpreter": (
            "launcher_path",
            "launcher_sha256",
            "orig_argv_executable",
            "orig_argv_executable_sha256",
            "version",
        ),
        "locks": (
            "p4_2a_done",
            "p4_2b_unlocked",
            "p4_3_unlocked",
            "heldout_evaluation_unlocked",
            "real_trading_unlocked",
            "non_simulate_trading_unlocked",
        ),
    }
    # The order in every companion array is authority.  Spell out the one long
    # set from the canonical companion rather than inheriting frozenset order.
    r_expected = dict(r_expected)
    r_expected["sealed_series"] = (
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
        "sealed_mirror",
    )
    b_expected: Mapping[str, tuple[str, ...]] = {
        "review_request_and_recovery_authorization": (
            "path",
            "sha256",
            "bytes",
            "creating_commit",
        ),
        "owner_confirmation": (
            "identity",
            "confirmation_text",
            "observed_at_utc",
            "observed_at_shanghai",
            "source",
            "authorization_sha256",
        ),
        "authorized_scope": (
            "series_token_sha256",
            "selected_attempt_ordinal",
            "authorized_bundle_recovery_starts",
            "authorized_pipeline_starts",
            "automatic_retry_count",
            "scope",
        ),
        "explicit_exclusions": (
            "attempt_allocation",
            "ledger_or_sealed_mirror_write",
            "pipeline",
            "heldout_materialization_inference_or_evaluation",
            "p4_2b",
            "p4_3",
            "trading",
        ),
        "registered_read_only_recovery_preflight": (
            "path",
            "stdout_sha256",
            "stdout_bytes",
            "real_lineage_census_sha256",
            "result",
        ),
        "machine_boundary": (
            "consumed_by_recovery_runner",
            "evidence_only",
            "passed_as_bundle_recovery_confirmation_binding",
            "machine_recovery_authorization_remains_exactly_19_fields",
            "this_document_adds_no_field_to_the_19_field_authorization",
        ),
    }
    for label, observed, expected in (
        ("Q", q_nested, q_expected),
        ("R", r_nested, r_expected),
        ("B", b_nested, b_expected),
    ):
        if set(observed) != set(expected):
            raise RehearsalV22Error(f"epoch-7 {label} nested field registry drifted")
        for key, fields in expected.items():
            _exact_contract_string_sequence(
                observed.get(key), fields, f"epoch-7 {label} nested {key} fields"
            )

    expected_effects = {
        "attempt_allocation": False,
        "candidate_or_terminal_rewrite": False,
        "destination_publish_once": True,
        "git_metadata_or_tracked_worktree_write": False,
        "git_object_read": True,
        "heldout_materialization_inference_or_evaluation": False,
        "ledger_read": True,
        "ledger_write": False,
        "model_access": False,
        "network_access": False,
        "paired_bundle_receipts_create_once": True,
        "pipeline_execution": False,
        "recovery_claim_create_once": True,
        "sealed_ledger_mirror_read": True,
        "sealed_ledger_mirror_write": False,
        "secondary_bundle_mirror_publish_once": True,
        "sqlite_or_production_database_access": False,
        "destination_stage_create_once": True,
        "secondary_snapshot_stage_create_once": True,
    }
    counters = _object(r.get("counters"), "epoch-7 R counters")
    if (
        q.get("schema_version") != EPOCH_7_RECOVERY_REVIEW_REQUEST_SCHEMA
        or r.get("schema_version") != EPOCH_7_RECOVERY_AUTHORIZATION_SCHEMA
        or b.get("schema_version") != EPOCH_7_RECOVERY_OWNER_BINDING_SCHEMA
        or claim.get("started_schema_version") != EPOCH_7_RECOVERY_STARTED_SCHEMA
        or claim.get("terminal_schema_version") != EPOCH_7_RECOVERY_TERMINAL_SCHEMA
        or receipt.get("schema_version") != EPOCH_7_RECOVERY_MIRROR_RECEIPT_SCHEMA
        or r.get("verdict_value")
        != "APPROVE_EXACTLY_ONE_SEALED_BUNDLE_RECOVERY_ZERO_PIPELINE_START_ZERO_AUTOMATIC_RETRY"
        or counters
        != {
            "authorized_bundle_recovery_starts": 1,
            "authorized_pipeline_starts": 0,
            "automatic_retry_count": 0,
        }
        or any(type(value) is not int for value in counters.values())
        or r.get("effect_authorization_exact") != expected_effects
        or any(type(value) is not bool for value in expected_effects.values())
        or anchors.get("mode_enum")
        != ["ACTIVE_ATTEMPT_BUNDLE", "PASSIVE_RECOVERED_BUNDLE", "PASSIVE_RECOVERED_RELEASE"]
        or census.get("roles")
        != [
            "PINNED_SOURCE",
            "PINNED_LANDING_PROJECTION",
            "PINNED_SOURCE_WITH_DESCENDANT_GRAPH",
            "DISCOVER_SOURCE_AFTER_PROJECTIONS",
        ]
        or census.get("scanner_role_map")
        != {
            "_unique_a_authority": "PINNED_SOURCE",
            "_validate_implementation_review_authority.all_touches": "PINNED_LANDING_PROJECTION",
            "_validate_initial_sibling_authority": "PINNED_SOURCE_WITH_DESCENDANT_GRAPH",
            "_unique_a_unserialized": "DISCOVER_SOURCE_AFTER_PROJECTIONS",
        }
        or protected.get("recovery_containers")
        != [
            OFFICIAL_PRIMARY_RECOVERY_CONTAINER.as_posix(),
            OFFICIAL_SECONDARY_RECOVERY_CONTAINER.as_posix(),
        ]
    ):
        raise RehearsalV22Error("epoch-7 exact recovery predicates drifted")

    legacy = _object(contract.get("legacy_absence_and_locks"), "epoch-7 legacy locks")
    expected_absence = [
        "official_series_2_bundle_emits_void_epoch_1",
        "void_epoch_3_added",
        "two_four_exception_added",
        "sealed_bundle_recovery_added",
        "recover_sealed_bundle_cli_added",
        "consume_recovered_release_cli_added",
    ]
    if legacy.get("amendment_time_facts_permanently_false") != expected_absence:
        raise RehearsalV22Error("epoch-7 legacy absence contract drifted")
    authority_payload = _git_blob(
        root,
        EPOCH_7_SURFACE_AUTHORITY_COMMIT,
        EPOCH_7_SURFACE_AUTHORITY_RELATIVE.as_posix(),
    )
    authority = _object(
        strict_json_loads(authority_payload, source="epoch-7 surface authority"),
        "epoch-7 surface authority",
    )
    parents = _git_parents_epoch_7(root, EPOCH_7_SURFACE_AUTHORITY_COMMIT)
    if (
        _sha256(authority_payload) != EPOCH_7_SURFACE_AUTHORITY_SHA256
        or parents != (EPOCH_7_COMPANION_COMMIT,)
        or authority.get("implementation_epoch") != 7
        or authority.get("base_commit") != EPOCH_7_COMPANION_COMMIT
        or not _git_is_ancestor(root, EPOCH_7_SURFACE_AUTHORITY_COMMIT, execution_head)
    ):
        raise RehearsalV22Error("epoch-7 surface authority topology drifted")
    _classify_unique_a_lineage(
        root,
        AuthorityCensusSpec(companion, "PINNED_SOURCE", None),
        execution_head=execution_head,
    )
    _classify_unique_a_lineage(
        root,
        AuthorityCensusSpec(
            AuthorityReference(
                EPOCH_7_SURFACE_AUTHORITY_RELATIVE.as_posix(),
                EPOCH_7_SURFACE_AUTHORITY_SHA256,
                EPOCH_7_SURFACE_AUTHORITY_COMMIT,
            ),
            "PINNED_SOURCE",
            None,
        ),
        execution_head=execution_head,
    )
    return contract


def validate_epoch_8_recovery_contract(
    project_root: Path,
    *,
    execution_head: str,
) -> JsonObject:
    """Validate the owner-issued epoch-8 contract without reusing epoch-7 authority."""

    root = project_root.absolute()
    proposal = AuthorityReference(
        EPOCH_8_DESIGN_PROPOSAL_RELATIVE.as_posix(),
        EPOCH_8_DESIGN_PROPOSAL_SHA256,
        EPOCH_8_DESIGN_PROPOSAL_COMMIT,
    )
    design_review = AuthorityReference(
        EPOCH_8_DESIGN_REVIEW_RELATIVE.as_posix(),
        EPOCH_8_DESIGN_REVIEW_SHA256,
        EPOCH_8_DESIGN_REVIEW_COMMIT,
    )
    adjudication = AuthorityReference(
        EPOCH_8_ADJUDICATION_RELATIVE.as_posix(),
        EPOCH_8_ADJUDICATION_SHA256,
        EPOCH_8_ADJUDICATION_COMMIT,
    )
    companion = AuthorityReference(
        EPOCH_8_COMPANION_RELATIVE.as_posix(),
        EPOCH_8_COMPANION_SHA256,
        EPOCH_8_COMPANION_COMMIT,
    )
    surface_authority = AuthorityReference(
        EPOCH_8_SURFACE_AUTHORITY_RELATIVE.as_posix(),
        EPOCH_8_SURFACE_AUTHORITY_SHA256,
        EPOCH_8_SURFACE_AUTHORITY_COMMIT,
    )
    governance = (
        (proposal, EPOCH_8_DESIGN_PROPOSAL_BYTES, "epoch-8 design proposal"),
        (design_review, EPOCH_8_DESIGN_REVIEW_BYTES, "epoch-8 independent design review"),
        (adjudication, EPOCH_8_ADJUDICATION_BYTES, "epoch-8 governing adjudication"),
        (companion, EPOCH_8_COMPANION_BYTES, "epoch-8 recovery companion"),
        (surface_authority, EPOCH_8_SURFACE_AUTHORITY_BYTES, "epoch-8 surface authority"),
    )
    payloads: dict[str, bytes] = {}
    for reference, expected_bytes, label in governance:
        payload = validate_unique_a_authority(
            root,
            reference,
            execution_head=execution_head,
        )
        if len(payload) != expected_bytes:
            raise RehearsalV22Error(f"{label} byte count drifted")
        payloads[reference.path] = payload
    if (
        _git_parents_epoch_7(root, EPOCH_8_DESIGN_PROPOSAL_COMMIT)
        != (EPOCH_7_REFUSED_B_CARRY_FORWARD[2],)
        or _git_parents_epoch_7(root, EPOCH_8_DESIGN_REVIEW_COMMIT)
        != (EPOCH_8_DESIGN_PROPOSAL_COMMIT,)
        or _git_parents_epoch_7(root, EPOCH_8_ADJUDICATION_COMMIT)
        != (EPOCH_8_DESIGN_REVIEW_COMMIT,)
        or _git_parents_epoch_7(root, EPOCH_8_COMPANION_COMMIT)
        != (EPOCH_8_ADJUDICATION_COMMIT,)
        or _git_parents_epoch_7(root, EPOCH_8_SURFACE_AUTHORITY_COMMIT)
        != (EPOCH_8_COMPANION_COMMIT,)
    ):
        raise RehearsalV22Error("epoch-8 governance is not the exact linear authority chain")

    design_review_document = _object(
        strict_json_loads(
            payloads[design_review.path],
            source="epoch-8 independent design review",
        ),
        "epoch-8 independent design review",
    )
    adjudication_document = _object(
        strict_json_loads(
            payloads[adjudication.path],
            source="epoch-8 governing adjudication",
        ),
        "epoch-8 governing adjudication",
    )
    if (
        design_review_document.get("verdict") != "PASS_DESIGN_REVIEW_ONLY"
        or _object(
            adjudication_document.get("part_1_incident_adjudicated"),
            "epoch-8 adjudicated incident",
        ).get("verdict")
        != "VALID_NON_CONSUMING_PRECLAIM_REFUSAL"
    ):
        raise RehearsalV22Error("epoch-8 design review or incident adjudication drifted")

    companion_document = _object(
        strict_json_loads(
            payloads[companion.path],
            source="epoch-8 recovery companion",
        ),
        "epoch-8 recovery companion",
    )
    reviewed = _object(
        companion_document.get("part_1_design_reviewed"),
        "epoch-8 design review binding",
    )
    owner = _object(
        companion_document.get("part_2_owner_approval"),
        "epoch-8 owner approval binding",
    )
    contract = _object(
        companion_document.get("epoch_8_recovery_contract"),
        "epoch-8 recovery contract",
    )
    expected_surface = [
        {"path": relative.as_posix(), "status": "M"}
        for relative in sorted(
            (
                IMPLEMENTATION_RELATIVE,
                VALIDATOR_RELATIVE,
                RUNNER_TEST_RELATIVE,
                VALIDATOR_TEST_RELATIVE,
            ),
            key=lambda value: value.as_posix().encode("utf-8"),
        )
    ]
    if (
        reviewed.get("verdict") != "PASS_DESIGN_REVIEW"
        or owner.get("accepted_owner_decision_count") != 12
        or owner.get("rejected_owner_decision_count") != 0
        or owner.get("future_surface_limit") != expected_surface
        or set(contract) != EPOCH_8_RECOVERY_CONTRACT_FIELDS
        or contract.get("schema_version") != EPOCH_8_RECOVERY_CONTRACT_SCHEMA
        or contract.get("implementation_epoch") != EPOCH_8_IMPLEMENTATION_EPOCH
        or _sha256(_canonical_json_bytes(contract))
        != EPOCH_8_RECOVERY_CONTRACT_CANONICAL_SHA256
    ):
        raise RehearsalV22Error("epoch-8 recovery contract or approved surface drifted")

    expected_reviewed_references = {
        "proposal": (
            proposal,
            EPOCH_8_DESIGN_PROPOSAL_BYTES,
        ),
        "independent_review": (
            design_review,
            EPOCH_8_DESIGN_REVIEW_BYTES,
        ),
        "governing_adjudication": (
            adjudication,
            EPOCH_8_ADJUDICATION_BYTES,
        ),
    }
    for key, (reference, expected_bytes) in expected_reviewed_references.items():
        observed = _object(reviewed.get(key), f"epoch-8 {key} binding")
        if (
            observed.get("path") != reference.path
            or observed.get("sha256") != reference.sha256
            or observed.get("creating_commit") != reference.creating_commit
            or observed.get("bytes") != expected_bytes
            or observed.get("unique_a_history_verified") is not True
        ):
            raise RehearsalV22Error(f"epoch-8 {key} binding drifted")

    governing = _exact_contract_object(
        contract.get("governing_adjudication"),
        frozenset({"path", "sha256", "creating_commit", "unique_a_history_verified"}),
        "epoch-8 governing adjudication",
    )
    if governing != adjudication.as_json():
        raise RehearsalV22Error("epoch-8 governing adjudication contract drifted")

    preflight = _exact_contract_object(
        contract.get("registered_preflight_contract"),
        frozenset(
            {
                "schema_version",
                "exact_top_level_fields",
                "nested_exact_field_sets",
                "cli_contract",
                "landing_authority_contract",
                "baseline_contract",
                "fixed_values",
                "rules",
            }
        ),
        "epoch-8 registered preflight contract",
    )
    q = _exact_contract_object(
        contract.get("recovery_review_request_contract"),
        frozenset(
            {
                "schema_version",
                "path_pattern",
                "topology",
                "exact_top_level_fields",
                "status_value",
                "nested_exact_field_sets",
                "rules",
            }
        ),
        "epoch-8 recovery Q contract",
    )
    r = _exact_contract_object(
        contract.get("recovery_authorization_contract"),
        frozenset(
            {
                "schema_version",
                "path_pattern",
                "topology",
                "exact_top_level_fields",
                "verdict_value",
                "counters",
                "nested_exact_field_sets",
                "effect_authorization_exact",
                "fixed_values",
            }
        ),
        "epoch-8 recovery R contract",
    )
    b = _exact_contract_object(
        contract.get("recovery_owner_binding_contract"),
        frozenset(
            {
                "schema_version",
                "path_pattern",
                "topology",
                "exact_top_level_fields",
                "nested_exact_field_sets",
                "fixed_values",
                "cli_operations",
                "bootstrap_order",
            }
        ),
        "epoch-8 recovery B contract",
    )
    claim = _exact_contract_object(
        contract.get("recovery_claim_contract"),
        frozenset(
            {
                "claim_name",
                "linearization",
                "started_schema_version",
                "started_exact_fields",
                "terminal_schema_version",
                "terminal_exact_fields",
                "outcomes",
                "crash_states",
            }
        ),
        "epoch-8 recovery claim contract",
    )
    receipt = _exact_contract_object(
        contract.get("bundle_mirror_receipt_contract"),
        frozenset({"schema_version", "exact_fields", "filename_pattern", "rules"}),
        "epoch-8 recovery receipt contract",
    )
    anchors = _exact_contract_object(
        contract.get("dual_byte_anchor_contract"),
        frozenset(
            {
                "historical_selected_anchor",
                "live_execution_anchor",
                "mode_enum",
                "recovered_publication_capability_exact_fields",
                "capability_required_values",
                "hook_disposition_authority",
                "release_truth_condition",
                "no_fallback",
            }
        ),
        "epoch-8 dual-anchor contract",
    )
    census = _exact_contract_object(
        contract.get("unique_a_and_lineage_census_contract"),
        frozenset(
            {
                "roles",
                "scanner_role_map",
                "projection_criteria",
                "rules",
                "census_schema_version",
                "census_exact_fields",
                "row_exact_fields",
                "touch_exact_fields",
                "timing",
                "fixed_carry_forward_row_fields",
                "fixed_carry_forward_rows",
                "baseline_and_start_contract",
            }
        ),
        "epoch-8 lineage census contract",
    )
    protected = _exact_contract_object(
        contract.get("protected_inputs_and_permitted_outputs"),
        frozenset(
            {
                "read_only_inputs",
                "permitted_recovery_writes",
                "recovery_containers",
                "container_rules",
                "consume_mode_effects",
                "forbidden_calls",
                "sealed_input_invariance",
            }
        ),
        "epoch-8 protected-input contract",
    )
    legacy = _exact_contract_object(
        contract.get("legacy_absence_and_locks"),
        frozenset(
            {"amendment_time_facts_permanently_false", "disclosure_rule", "epoch_table", "locks"}
        ),
        "epoch-8 legacy locks",
    )

    _exact_contract_string_sequence(
        preflight.get("exact_top_level_fields"),
        EPOCH_8_READ_ONLY_PREFLIGHT_FIELD_ORDER,
        "epoch-8 preflight top-level fields",
    )
    _exact_contract_string_sequence(
        q.get("exact_top_level_fields"),
        RECOVERY_REVIEW_REQUEST_FIELD_ORDER,
        "epoch-8 Q top-level fields",
    )
    _exact_contract_string_sequence(
        r.get("exact_top_level_fields"),
        RECOVERY_AUTHORIZATION_FIELD_ORDER,
        "epoch-8 R top-level fields",
    )
    _exact_contract_string_sequence(
        b.get("exact_top_level_fields"),
        RECOVERY_OWNER_BINDING_FIELD_ORDER,
        "epoch-8 B top-level fields",
    )
    _exact_contract_string_sequence(
        claim.get("started_exact_fields"),
        RECOVERY_STARTED_FIELD_ORDER,
        "epoch-8 started fields",
    )
    _exact_contract_string_sequence(
        claim.get("terminal_exact_fields"),
        RECOVERY_TERMINAL_FIELD_ORDER,
        "epoch-8 terminal fields",
    )
    _exact_contract_string_sequence(
        receipt.get("exact_fields"),
        RECOVERY_MIRROR_RECEIPT_FIELD_ORDER,
        "epoch-8 receipt fields",
    )
    _exact_contract_string_sequence(
        anchors.get("recovered_publication_capability_exact_fields"),
        RECOVERED_PUBLICATION_CAPABILITY_FIELD_ORDER,
        "epoch-8 recovered-publication fields",
    )
    _exact_contract_string_sequence(
        census.get("census_exact_fields"),
        RECOVERY_CENSUS_FIELD_ORDER,
        "epoch-8 census fields",
    )
    _exact_contract_string_sequence(
        census.get("row_exact_fields"),
        RECOVERY_CENSUS_ROW_FIELD_ORDER,
        "epoch-8 census row fields",
    )
    _exact_contract_string_sequence(
        census.get("touch_exact_fields"),
        RECOVERY_CENSUS_TOUCH_FIELD_ORDER,
        "epoch-8 census touch fields",
    )

    q_nested = _object(q.get("nested_exact_field_sets"), "epoch-8 Q nested fields")
    landed_fields = (
        "epoch",
        "implementation_commit",
        "owner_exact_surface_authorization",
        "independent_implementation_review",
        "merge_commit",
        "landing_report",
        "control_merkle_root_sha256",
        "control_record_count",
    )
    if "landed_epoch_7" in q_nested or set(q_nested) != {
        "requester",
        "landed_execution_epoch",
        "registered_read_only_recovery_preflight",
        "preflight_before_after_equality",
        "proposed_recovery_authorization",
        "requested_owner_action_time_confirmation",
        "post_confirmation_plan_not_yet_executed",
        "current_locks",
    }:
        raise RehearsalV22Error("epoch-8 Q nested field registry drifted")
    _exact_contract_string_sequence(
        q_nested.get("landed_execution_epoch"),
        landed_fields,
        "epoch-8 landed execution epoch fields",
    )
    preflight_nested = _object(
        preflight.get("nested_exact_field_sets"),
        "epoch-8 preflight nested fields",
    )
    if (
        preflight.get("schema_version") != EPOCH_8_READ_ONLY_PREFLIGHT_SCHEMA
        or q.get("schema_version") != EPOCH_8_RECOVERY_REVIEW_REQUEST_SCHEMA
        or r.get("schema_version") != EPOCH_8_RECOVERY_AUTHORIZATION_SCHEMA
        or b.get("schema_version") != EPOCH_8_RECOVERY_OWNER_BINDING_SCHEMA
        or claim.get("started_schema_version") != EPOCH_8_RECOVERY_STARTED_SCHEMA
        or claim.get("terminal_schema_version") != EPOCH_8_RECOVERY_TERMINAL_SCHEMA
        or receipt.get("schema_version") != EPOCH_8_RECOVERY_MIRROR_RECEIPT_SCHEMA
        or "registered_recovery_storage" not in preflight_nested
        or "epoch_7_recovery_storage" in preflight_nested
    ):
        raise RehearsalV22Error("epoch-8 exact schema set drifted")

    expected_carry_forward_rows = []
    for path, digest, commit, role, projection, size in (
        EPOCH_7_LIVE_REVIEW_CARRY_FORWARD,
        EPOCH_7_LANDING_CARRY_FORWARD,
        EPOCH_7_REFUSED_Q_CARRY_FORWARD,
        EPOCH_7_REFUSED_R_CARRY_FORWARD,
        EPOCH_7_REFUSED_B_CARRY_FORWARD,
        (
            EPOCH_8_ADJUDICATION_RELATIVE.as_posix(),
            EPOCH_8_ADJUDICATION_SHA256,
            EPOCH_8_ADJUDICATION_COMMIT,
            "PINNED_SOURCE",
            None,
            EPOCH_8_ADJUDICATION_BYTES,
        ),
    ):
        expected_carry_forward_rows.append(
            {
                "path": path,
                "sha256": digest,
                "bytes": size,
                "creating_commit": commit,
                "role": role,
                "declared_landing_projection_commit": projection,
            }
        )
    if (
        census.get("fixed_carry_forward_rows") != expected_carry_forward_rows
        or census.get("census_schema_version") != EPOCH_8_LINEAGE_CENSUS_SCHEMA
        or census.get("roles")
        != [
            "PINNED_SOURCE",
            "PINNED_LANDING_PROJECTION",
            "PINNED_SOURCE_WITH_DESCENDANT_GRAPH",
            "DISCOVER_SOURCE_AFTER_PROJECTIONS",
        ]
        or protected.get("recovery_containers")
        != [
            OFFICIAL_PRIMARY_RECOVERY_CONTAINER.as_posix(),
            OFFICIAL_SECONDARY_RECOVERY_CONTAINER.as_posix(),
        ]
        or anchors.get("mode_enum")
        != ["ACTIVE_ATTEMPT_BUNDLE", "PASSIVE_RECOVERED_BUNDLE", "PASSIVE_RECOVERED_RELEASE"]
        or legacy.get("amendment_time_facts_permanently_false")
        != [
            "official_series_2_bundle_emits_void_epoch_1",
            "void_epoch_3_added",
            "two_four_exception_added",
            "sealed_bundle_recovery_added",
            "recover_sealed_bundle_cli_added",
            "consume_recovered_release_cli_added",
        ]
    ):
        raise RehearsalV22Error("epoch-8 carry-forward, anchor, or legacy contract drifted")

    authority_payload = payloads[surface_authority.path]
    authority_document = _object(
        strict_json_loads(authority_payload, source="epoch-8 surface authority"),
        "epoch-8 surface authority",
    )
    if (
        set(authority_document)
        != {
            "schema_version",
            "verdict",
            "owner",
            "implementation_epoch",
            "base_commit",
            "exact_surface",
        }
        or authority_document.get("schema_version")
        != "p4.2a-v2-2-implementation-epoch-surface-authorization-v1"
        or authority_document.get("verdict")
        != "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE"
        or authority_document.get("implementation_epoch") != EPOCH_8_IMPLEMENTATION_EPOCH
        or authority_document.get("base_commit") != EPOCH_8_COMPANION_COMMIT
        or authority_document.get("exact_surface") != expected_surface
        or _object(authority_document.get("owner"), "epoch-8 surface authority owner")
        != {"identity": "ouyang", "approved": True}
    ):
        raise RehearsalV22Error("epoch-8 surface authority bytes or semantics drifted")
    _classify_unique_a_lineage(
        root,
        AuthorityCensusSpec(companion, "PINNED_SOURCE", None),
        execution_head=execution_head,
    )
    _classify_unique_a_lineage(
        root,
        AuthorityCensusSpec(surface_authority, "PINNED_SOURCE", None),
        execution_head=execution_head,
    )
    return contract


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
    if os.path.lexists(current_path):
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
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdecimal() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise RehearsalV22Error(f"{label} does not resolve")
    return current


def _delete_json_pointer_if_present(document: object, pointer: str) -> bool:
    parts = _json_pointer_parts(pointer, "schema delta pointer")
    current = document
    for part in parts[:-1]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdecimal() and int(part) < len(current):
            current = current[int(part)]
        else:
            return False
    final = parts[-1]
    if isinstance(current, dict) and final in current:
        del current[final]
    elif isinstance(current, list) and final.isdecimal() and int(final) < len(current):
        del current[int(final)]
    else:
        return False
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


def validate_strict_v2_1_inheritance(project_root: Path) -> JsonObject:
    """Independently rederive the frozen v2.1 projection and schema zero-diffs."""

    root = project_root.absolute()
    prereg_payload = _regular_bytes(root / PREREGISTRATION_RELATIVE, "v2.2 preregistration")
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
    v2_1_path = _safe_path(root, source_contract.get("source_file"), "v2.1 preregistration")
    v2_1_payload = _regular_bytes(v2_1_path, "v2.1 preregistration")
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
        old_payload = _regular_bytes(root / old_path, f"v2.1 {label} schema")
        new_payload = _regular_bytes(root / new_path, f"v2.2 {label} schema")
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


SERIES_2_BUNDLE_SCHEMA_DELTA_POINTERS = (
    "/$id",
    "/$defs/lineage/properties/preregistration/allOf/1/properties/path/const",
    "/$defs/lineage/properties/bundle_schema/allOf/1/properties/path/const",
    "/$defs/lineage/properties/release_authorization_schema/allOf/1/properties/path/const",
    "/$defs/lineage/properties/release_authorization_schema/allOf/1/properties/sha256/const",
    "/$defs/executionBinding/oneOf/0/properties/series_token_sha256/const",
    "/$defs/executionBinding/oneOf/0/properties/ledger_root/const",
)
SERIES_2_RELEASE_SCHEMA_DELTA_POINTERS = (
    "/$id",
    "/properties/lineage/properties/preregistration/allOf/1/properties/path/const",
    "/properties/lineage/properties/bundle_schema/allOf/1/properties/path/const",
    "/properties/lineage/properties/release_schema/allOf/1/properties/path/const",
    "/$defs/executionBinding/oneOf/0/properties/series_token_sha256/const",
    "/$defs/executionBinding/oneOf/0/properties/ledger_root/const",
)


def _validate_series_2_schema_profile(
    root: Path,
    *,
    historical_path: Path,
    historical_sha256: str,
    active_path: Path,
    active_sha256: str,
    pointers: tuple[str, ...],
    label: str,
) -> None:
    historical_payload = _regular_bytes(root / historical_path, f"historical {label}")
    active_payload = _regular_bytes(root / active_path, f"series-2 {label}")
    if _sha256(historical_payload) != historical_sha256 or _sha256(active_payload) != active_sha256:
        raise RehearsalV22Error(f"series-2 {label} binding bytes drifted")
    historical = copy.deepcopy(
        _object(
            strict_json_loads(historical_payload, source=f"historical {label}"),
            f"historical {label}",
        )
    )
    active = copy.deepcopy(
        _object(
            strict_json_loads(active_payload, source=f"series-2 {label}"),
            f"series-2 {label}",
        )
    )
    for pointer in pointers:
        if not _delete_json_pointer_if_present(active, pointer):
            raise RehearsalV22Error(f"series-2 {label} delta pointer is absent: {pointer}")
        _delete_json_pointer_if_present(historical, pointer)
    if not _typed_json_equal(historical, active):
        raise RehearsalV22Error(f"series-2 {label} changes a non-binding pointer")


def validate_series_2_preregistration(
    project_root: Path,
    *,
    execution_head: str,
) -> AuthorityReference:
    """Revalidate the exact landed amendment, its loss chain, and binding profiles."""

    root = project_root.absolute()
    head = _git_commit(root, execution_head, "series-2 execution head")
    amendment = AuthorityReference(
        SERIES_2_PREREGISTRATION_RELATIVE.as_posix(),
        SERIES_2_PREREGISTRATION_SHA256,
        SERIES_2_PREREGISTRATION_COMMIT,
    )
    payload = validate_unique_a_authority(root, amendment, execution_head=head)
    if _git_bytes(
        root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        SERIES_2_PREREGISTRATION_COMMIT,
        "--",
    ).decode("ascii", errors="strict").strip().split() != [
        SERIES_2_PREREGISTRATION_COMMIT,
        SERIES_2_PREREGISTRATION_PARENT,
    ]:
        raise RehearsalV22Error("series-2 preregistration parent drifted")
    expected_surface = {
        SERIES_2_PREREGISTRATION_RELATIVE.as_posix(): "A",
        SERIES_2_BUNDLE_SCHEMA_RELATIVE.as_posix(): "A",
        SERIES_2_RELEASE_SCHEMA_RELATIVE.as_posix(): "A",
    }
    if (
        _parse_name_status(
            _git_bytes(
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-status",
                "--no-renames",
                SERIES_2_PREREGISTRATION_PARENT,
                SERIES_2_PREREGISTRATION_COMMIT,
                "--",
            )
        )
        != expected_surface
    ):
        raise RehearsalV22Error("series-2 preregistration is not the exact three-A package")
    document = _object(
        strict_json_loads(payload, source="series-2 preregistration amendment"),
        "series-2 preregistration amendment",
    )
    part_1 = _object(
        document.get("part_1_authority_loss_and_owner_decision_bindings"),
        "series-2 amendment authority bindings",
    )
    loss = _object(part_1.get("loss_incident"), "series-2 loss incident binding")
    owner = _object(part_1.get("owner_decision"), "series-2 owner decision binding")
    if not _typed_json_equal(
        loss,
        {
            "commit": SERIES_2_LOSS_INCIDENT_COMMIT,
            "path": SERIES_2_LOSS_INCIDENT_RELATIVE.as_posix(),
            "sha256": SERIES_2_TOKEN_SEED_SHA256,
            "bytes": 6422,
            "verdict": "SEALED_LEDGER_BYTES_LOST_SERIES_SUCCESS_STANDS_BY_DIGEST",
        },
    ) or not _typed_json_equal(
        owner,
        {
            "commit": SERIES_2_OWNER_DECISION_COMMIT,
            "path": SERIES_2_OWNER_DECISION_RELATIVE.as_posix(),
            "sha256": SERIES_2_OWNER_DECISION_SHA256,
            "bytes": 2347,
            "decision": "run it again",
        },
    ):
        raise RehearsalV22Error("series-2 loss or owner-decision binding drifted")
    lost_history = _object(
        document.get("part_2_complete_lost_series_digest_history"),
        "complete lost-series digest history",
    )
    if not _typed_json_equal(
        lost_history,
        {
            "classification": "DIGEST_PROOF_CHAIN_NOT_RECONSTRUCTABLE_LEDGER_BYTES",
            "old_series_token_sha256": (
                "35ba1b83a9b187817d7a591758e1c131e867fcd37917cba0ab196799fff832ef"
            ),
            "old_ledger_root": (
                "/Users/ouyangduning/Documents/project/interesting/"
                ".alphapilot-p4-2a-v2-2-execution-claim-"
                "35ba1b83a9b187817d7a591758e1c131e867fcd37917cba0ab196799fff832ef"
            ),
            "history_root_after_ordinal_1": (
                "076ae961fc149ae271bf5a3724c1677abccfea7139589909ea717a7f4a38083a"
            ),
            "history_root_after_ordinal_2": (
                "a466de7b349882f2bcd556a4b4d00bf38bace9adb593b0e3b6296c415a8c9ca1"
            ),
            "attempt_1": {
                "ordinal": 1,
                "outcome": "FAILED",
                "implementation_epoch": 2,
                "implementation_commit": "1b4e05c6acd513bb1bc11245911da97b6a128ca1",
                "evidence_tree_root_sha256": (
                    "deea0e81e3fd8a5c886cc4c757fb5485cb7f750718462489dea48d3deed2691c"
                ),
            },
            "attempt_2": {
                "ordinal": 2,
                "outcome": "CANDIDATE_VALIDATED_AND_SELECTED",
                "implementation_epoch": 4,
                "implementation_commit": "890e9002116c625d41f6aa037975df15d1546c56",
                "started_sha256": (
                    "75771a37572fb9191a9db26f986b1e9d89c26843556b502866322a8f4bdaf42d"
                ),
                "candidate_sha256": (
                    "92652f963b04b79e29580978cd6857c2154df0b429ac09502be6c0c0c5d84da5"
                ),
                "terminal_sha256": (
                    "7ba4ed1b5d7e7abc462b312f08b131ff438cc524cecbdeea6b43dc199292e3dc"
                ),
                "evidence_tree_root_sha256": (
                    "f38b18b972f14a170fc9bb4129f25ec77e8ad1c4e8a8f137b5853cc371b694c2"
                ),
                "run_a_and_run_b_root_sha256": (
                    "5fb8edf3aa65cdcd0f54b82bdf6f240104fa8537c1004e640671910115f8f314"
                ),
                "candidate_content_root_sha256": (
                    "5de4f74d1f73e5f90aa9c196c8fc6574bce2ecfa91abd750b22726c14c6a60b7"
                ),
                "selected_control_root_sha256": (
                    "76076606d6e40cdd386b28cdd5bc40a8957693b8cfdc8b17a0a77410b4e082e8"
                ),
                "q_r_b_commits": [
                    "f6f993d0e9f30b6f6c5250a94a4a49b179fc8ff1",
                    "f004054c1797904206e5590f2b0f4751848665c1",
                    "1832ed7c6130b71d5a99722721eaec83b2adabdd",
                ],
                "execution_authorization_sha256": (
                    "8f5c0b31ef88922b8b44b202c729fad55a4d0cb172c000089991f1cd2c995461"
                ),
            },
            "contemporaneous_verification_commits": [
                "7fc122f575801ff43d2446a2c59491a086735e93",
                "0548692480ff8325b69be92f01e0d42e11ad4eb0",
                "7cf819d43d5b73f7b8f1469a556c79ece12587f0",
            ],
            "series_1_outcome_stands": True,
            "series_1_bundle_cannot_be_reconstructed_or_released": True,
            "series_1_digests_enter_series_2_amendment_lineage": True,
            "series_1_digests_enter_series_2_attempt_history_root": False,
            (
                "series_1_digests_enter_series_2_control_surface_and_"
                "bundle_lineage_through_the_amendment"
            ): True,
            "old_ledger_and_retired_v2_1_claim_must_remain_absent": True,
        },
    ):
        raise RehearsalV22Error("complete lost-series digest history drifted")
    for reference, expected_bytes in (
        (
            AuthorityReference(
                SERIES_2_LOSS_INCIDENT_RELATIVE.as_posix(),
                SERIES_2_TOKEN_SEED_SHA256,
                SERIES_2_LOSS_INCIDENT_COMMIT,
            ),
            6422,
        ),
        (
            AuthorityReference(
                SERIES_2_OWNER_DECISION_RELATIVE.as_posix(),
                SERIES_2_OWNER_DECISION_SHA256,
                SERIES_2_OWNER_DECISION_COMMIT,
            ),
            2347,
        ),
    ):
        observed = validate_unique_a_authority(root, reference, execution_head=head)
        if len(observed) != expected_bytes:
            raise RehearsalV22Error("series-2 lineage authority byte count drifted")
    identity = _object(
        document.get("part_4_fresh_series_identity_and_visible_primary_mirror_paths"),
        "series-2 identity",
    )
    if (
        identity.get("series_token_sha256") != OFFICIAL_SERIES_TOKEN
        or identity.get("loss_incident_file_sha256") != SERIES_2_TOKEN_SEED_SHA256
        or identity.get("primary_series_container") != OFFICIAL_PRIMARY_SERIES_CONTAINER.as_posix()
        or identity.get("primary_ledger_root") != OFFICIAL_LEDGER_ROOT.as_posix()
        or identity.get("primary_receipt_root") != OFFICIAL_PRIMARY_RECEIPT_ROOT.as_posix()
        or identity.get("secondary_series_container")
        != OFFICIAL_SECONDARY_SERIES_CONTAINER.as_posix()
        or identity.get("secondary_snapshot_root") != OFFICIAL_SECONDARY_SNAPSHOT_ROOT.as_posix()
        or identity.get("secondary_receipt_root") != OFFICIAL_SECONDARY_RECEIPT_ROOT.as_posix()
    ):
        raise RehearsalV22Error("series-2 visible storage binding drifted")
    isolation = _object(identity.get("constant_isolation"), "series-2 constant isolation")
    if (
        isolation.get("new_token_seed_constant") != "SERIES_2_TOKEN_SEED_SHA256"
        or isolation.get("new_token_seed_value") != SERIES_2_TOKEN_SEED_SHA256
        or isolation.get("legacy_incident_sha256_constant_name") != "INCIDENT_SHA256"
        or isolation.get("legacy_incident_sha256_value_unchanged") != INCIDENT_SHA256
    ):
        raise RehearsalV22Error("series-2 and historical incident constants are not isolated")
    reset = _object(
        document.get("part_3_non_reconstruction_and_independent_empty_history_reset"),
        "series-2 history reset",
    )
    if (
        reset.get("history_empty_root_sha256") != _history_empty_root_sha256()
        or reset.get("ordinal_1_previous_history_root_sha256") != _history_empty_root_sha256()
        or reset.get("old_history_root_must_not_seed_series_2") is not True
    ):
        raise RehearsalV22Error("series-2 independent empty-history reset drifted")
    _validate_series_2_schema_profile(
        root,
        historical_path=BUNDLE_SCHEMA_RELATIVE,
        historical_sha256=BUNDLE_SCHEMA_SHA256,
        active_path=SERIES_2_BUNDLE_SCHEMA_RELATIVE,
        active_sha256=SERIES_2_BUNDLE_SCHEMA_SHA256,
        pointers=SERIES_2_BUNDLE_SCHEMA_DELTA_POINTERS,
        label="bundle schema",
    )
    _validate_series_2_schema_profile(
        root,
        historical_path=RELEASE_SCHEMA_RELATIVE,
        historical_sha256=RELEASE_SCHEMA_SHA256,
        active_path=SERIES_2_RELEASE_SCHEMA_RELATIVE,
        active_sha256=SERIES_2_RELEASE_SCHEMA_SHA256,
        pointers=SERIES_2_RELEASE_SCHEMA_DELTA_POINTERS,
        label="release schema",
    )
    return amendment


def validate_carry_forward_lineage(
    project_root: Path,
    *,
    execution_head: str,
    require_current: bool = True,
) -> tuple[AuthorityReference, ...]:
    """Reprove the nine frozen carry-forward rows and v2.1 exact 15-path tree."""

    root = project_root.absolute()
    head = _git_commit(root, execution_head, "carry-forward execution head")
    prereg_payload = _regular_bytes(root / PREREGISTRATION_RELATIVE, "v2.2 preregistration")
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
        validate_unique_a_authority(root, reference, execution_head=head)
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
    if epoch >= SERIES_2_EPOCH_ORIGIN:
        if not _git_is_ancestor(root, SERIES_2_PREREGISTRATION_COMMIT, base_commit):
            raise RehearsalV22Error("later epoch base lost active series-2 preregistration lineage")
        if not _git_is_ancestor(root, SERIES_2_PREREGISTRATION_COMMIT, selected):
            raise RehearsalV22Error(
                "later epoch implementation lost active series-2 preregistration lineage"
            )
    return payload


def _document_mentions_commit(value: object, commit: object) -> bool:
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
        "schema_version": SERIES_2_SERIES_SCHEMA_VERSION,
        "series_id": REHEARSAL_ID,
        "series_token_sha256": binding.series_token_sha256,
        "policy": SERIES_POLICY,
        "ledger_root": binding.ledger_root.as_posix(),
        "attempt_limit": "unbounded_until_first_validated_success_or_owner_abandonment",
        "per_attempt_action_time_owner_authorization_required": True,
        "automatic_retry_count": 0,
        "first_validated_candidate_closes_series": True,
        "implementation_epoch_origin": SERIES_2_EPOCH_ORIGIN,
        "preregistration": {
            "path": SERIES_2_PREREGISTRATION_RELATIVE.as_posix(),
            "sha256": SERIES_2_PREREGISTRATION_SHA256,
            "creating_commit": SERIES_2_PREREGISTRATION_COMMIT,
            "unique_a_history_verified": True,
        },
        "bundle_schema": {
            "path": SERIES_2_BUNDLE_SCHEMA_RELATIVE.as_posix(),
            "sha256": SERIES_2_BUNDLE_SCHEMA_SHA256,
        },
        "release_schema": {
            "path": SERIES_2_RELEASE_SCHEMA_RELATIVE.as_posix(),
            "sha256": SERIES_2_RELEASE_SCHEMA_SHA256,
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
    "implementation_epoch_origin",
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
RECOVERY_REVIEW_REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "request_id",
        "created_at_utc",
        "created_at_shanghai",
        "status",
        "requester",
        "landed_execution_epoch",
        "registered_read_only_recovery_preflight",
        "preflight_before_after_equality",
        "proposed_recovery_authorization",
        "requested_owner_action_time_confirmation",
        "post_confirmation_plan_not_yet_executed",
        "current_locks",
    }
)
EPOCH_7_RECOVERY_REVIEW_REQUEST_FIELDS = frozenset(
    (RECOVERY_REVIEW_REQUEST_FIELDS - {"landed_execution_epoch"}) | {"landed_epoch_7"}
)
RECOVERY_AUTHORIZATION_FIELDS = frozenset(
    {
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
)
RECOVERY_OWNER_BINDING_FIELDS = frozenset(
    {
        "schema_version",
        "binding_id",
        "created_at_utc",
        "created_at_shanghai",
        "status",
        "review_request",
        "recovery_authorization",
        "owner_confirmation",
        "authorized_scope",
        "explicit_exclusions",
        "registered_read_only_recovery_preflight",
        "machine_boundary",
    }
)
RECOVERY_SEALED_SERIES_FIELDS = frozenset(
    {
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
        "sealed_mirror",
    }
)
RECOVERY_SELECTED_FILES_FIELDS = frozenset({"started", "candidate", "terminal"})
RECOVERY_SELECTED_FILE_FIELDS = frozenset({"relative_path", "sha256", "bytes"})
RECOVERY_EXECUTION_EPOCH_FIELDS = frozenset(
    {
        "epoch",
        "implementation_commit",
        "owner_exact_surface_authorization",
        "independent_implementation_review",
        "merge_commit",
        "landing_report",
        "control_merkle_root_sha256",
        "control_record_count",
        "real_lineage_census",
        "latest_complete_landed_epoch_required",
        "current_control_bytes_required",
        "loaded_module_bytes_required",
    }
)
RECOVERY_CENSUS_REFERENCE_FIELDS = frozenset(
    {
        "schema_version",
        "execution_head",
        "reference_count",
        "row_count",
        "projection_count",
        "invalid_count",
        "canonical_json_sha256",
        "bytes",
        "result",
        "all_references_revalidated_at_start",
    }
)
RECOVERY_DESTINATION_FIELDS = frozenset(
    {
        "absolute_path",
        "required_absent_before_start",
        "publication_mode",
        "bundle_schema_version",
        "expected_bundle_status",
        "recovery_storage",
    }
)
RECOVERY_STORAGE_FIELDS = frozenset(
    {
        "primary_recovery_container",
        "secondary_recovery_container",
        "claim_name_derived_from_authorization_sha256",
        "destination_stage_name_derived_from_authorization_sha256",
        "secondary_snapshot_stage_name_derived_from_authorization_sha256",
        "secondary_snapshot_name_derived_from_authorization_sha256_and_tree_root",
        "receipt_name_derived_from_authorization_sha256_and_tree_root",
        "destination_publication_mode",
        "secondary_snapshot_publication_mode",
        "primary_receipt_publication_mode",
        "secondary_receipt_publication_mode",
        "paired_receipts_required",
    }
)
RECOVERY_INTERPRETER_FIELDS = frozenset(
    {
        "launcher_path",
        "launcher_sha256",
        "orig_argv_executable",
        "orig_argv_executable_sha256",
        "version",
    }
)
RECOVERY_EFFECT_FIELDS = frozenset(
    {
        "attempt_allocation",
        "candidate_or_terminal_rewrite",
        "destination_publish_once",
        "destination_stage_create_once",
        "git_metadata_or_tracked_worktree_write",
        "git_object_read",
        "heldout_materialization_inference_or_evaluation",
        "ledger_read",
        "ledger_write",
        "model_access",
        "network_access",
        "paired_bundle_receipts_create_once",
        "pipeline_execution",
        "recovery_claim_create_once",
        "sealed_ledger_mirror_read",
        "sealed_ledger_mirror_write",
        "secondary_bundle_mirror_publish_once",
        "secondary_snapshot_stage_create_once",
        "sqlite_or_production_database_access",
    }
)
RECOVERY_LOCK_FIELDS = frozenset(
    {
        "p4_2a_done",
        "p4_2b_unlocked",
        "p4_3_unlocked",
        "heldout_evaluation_unlocked",
        "real_trading_unlocked",
        "non_simulate_trading_unlocked",
    }
)
RECOVERY_STARTED_FIELDS = frozenset(
    {
        "schema_version",
        "recovery_id",
        "authorization",
        "owner_confirmation_binding",
        "created_at_utc",
        "created_at_shanghai",
        "execution_head",
        "execution_epoch",
        "sealed_history_root_sha256",
        "sealed_live_ledger_root_sha256",
        "sealed_mirror_receipt_sha256",
        "destination",
        "destination_stage",
        "secondary_snapshot_stage",
        "secondary_snapshot_target",
        "state",
        "authorized_bundle_recovery_starts",
        "authorized_pipeline_starts",
        "automatic_retry_count",
    }
)
RECOVERY_TERMINAL_FIELDS = frozenset(
    {
        "schema_version",
        "recovery_id",
        "authorization",
        "owner_confirmation_binding",
        "completed_at_utc",
        "completed_at_shanghai",
        "outcome",
        "reached_stage",
        "sealed_ledger_before_sha256",
        "sealed_ledger_after_sha256",
        "sealed_mirror_before_sha256",
        "sealed_mirror_after_sha256",
        "destination",
        "published_bundle_sha256",
        "published_tree_sha256",
        "secondary_snapshot",
        "secondary_snapshot_tree_sha256",
        "primary_receipt",
        "secondary_receipt",
        "paired_receipts_byte_identical",
        "destination_stage_absent",
        "secondary_snapshot_stage_absent",
        "pipeline_starts",
        "automatic_retry_count",
        "error",
    }
)
RECOVERY_MIRROR_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "recovery_authorization_sha256",
        "owner_confirmation_binding_sha256",
        "recovery_id",
        "series_id",
        "series_token_sha256",
        "sealed_history_root_sha256",
        "sealed_live_ledger_root_sha256",
        "selected_attempt_ordinal",
        "selected_implementation_epoch",
        "selected_implementation_commit",
        "execution_epoch",
        "execution_implementation_commit",
        "execution_head",
        "destination",
        "published_bundle_sha256",
        "published_tree_sha256",
        "secondary_snapshot",
        "secondary_snapshot_tree_sha256",
        "destination_and_snapshot_byte_identical",
        "pipeline_starts",
        "automatic_retry_count",
        "sealed_ledger_before_after_equal",
        "sealed_mirror_before_after_equal",
        "verified_at_utc",
    }
)
RECOVERY_REVIEW_REQUEST_FIELD_ORDER = (
    "schema_version",
    "request_id",
    "created_at_utc",
    "created_at_shanghai",
    "status",
    "requester",
    "landed_execution_epoch",
    "registered_read_only_recovery_preflight",
    "preflight_before_after_equality",
    "proposed_recovery_authorization",
    "requested_owner_action_time_confirmation",
    "post_confirmation_plan_not_yet_executed",
    "current_locks",
)
EPOCH_7_RECOVERY_REVIEW_REQUEST_FIELD_ORDER = tuple(
    "landed_epoch_7" if field == "landed_execution_epoch" else field
    for field in RECOVERY_REVIEW_REQUEST_FIELD_ORDER
)
EPOCH_8_READ_ONLY_PREFLIGHT_FIELD_ORDER = (
    "schema_version",
    "status",
    "mode",
    "execution_head",
    "implementation_epoch",
    "implementation_commit",
    "owner_exact_surface_authorization",
    "independent_implementation_review",
    "control_merkle_root_sha256",
    "control_record_count",
    "registered_surface",
    "series_2_registered_storage",
    "real_lineage_census",
    "registered_recovery_storage",
    "sealed_recovery_inputs",
    "effect_summary",
)
RECOVERY_AUTHORIZATION_FIELD_ORDER = (
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
)
RECOVERY_OWNER_BINDING_FIELD_ORDER = (
    "schema_version",
    "binding_id",
    "created_at_utc",
    "created_at_shanghai",
    "status",
    "review_request",
    "recovery_authorization",
    "owner_confirmation",
    "authorized_scope",
    "explicit_exclusions",
    "registered_read_only_recovery_preflight",
    "machine_boundary",
)
RECOVERY_STARTED_FIELD_ORDER = (
    "schema_version",
    "recovery_id",
    "authorization",
    "owner_confirmation_binding",
    "created_at_utc",
    "created_at_shanghai",
    "execution_head",
    "execution_epoch",
    "sealed_history_root_sha256",
    "sealed_live_ledger_root_sha256",
    "sealed_mirror_receipt_sha256",
    "destination",
    "destination_stage",
    "secondary_snapshot_stage",
    "secondary_snapshot_target",
    "state",
    "authorized_bundle_recovery_starts",
    "authorized_pipeline_starts",
    "automatic_retry_count",
)
RECOVERY_TERMINAL_FIELD_ORDER = (
    "schema_version",
    "recovery_id",
    "authorization",
    "owner_confirmation_binding",
    "completed_at_utc",
    "completed_at_shanghai",
    "outcome",
    "reached_stage",
    "sealed_ledger_before_sha256",
    "sealed_ledger_after_sha256",
    "sealed_mirror_before_sha256",
    "sealed_mirror_after_sha256",
    "destination",
    "published_bundle_sha256",
    "published_tree_sha256",
    "secondary_snapshot",
    "secondary_snapshot_tree_sha256",
    "primary_receipt",
    "secondary_receipt",
    "paired_receipts_byte_identical",
    "destination_stage_absent",
    "secondary_snapshot_stage_absent",
    "pipeline_starts",
    "automatic_retry_count",
    "error",
)
RECOVERY_MIRROR_RECEIPT_FIELD_ORDER = (
    "schema_version",
    "recovery_authorization_sha256",
    "owner_confirmation_binding_sha256",
    "recovery_id",
    "series_id",
    "series_token_sha256",
    "sealed_history_root_sha256",
    "sealed_live_ledger_root_sha256",
    "selected_attempt_ordinal",
    "selected_implementation_epoch",
    "selected_implementation_commit",
    "execution_epoch",
    "execution_implementation_commit",
    "execution_head",
    "destination",
    "published_bundle_sha256",
    "published_tree_sha256",
    "secondary_snapshot",
    "secondary_snapshot_tree_sha256",
    "destination_and_snapshot_byte_identical",
    "pipeline_starts",
    "automatic_retry_count",
    "sealed_ledger_before_after_equal",
    "sealed_mirror_before_after_equal",
    "verified_at_utc",
)
RECOVERY_CENSUS_FIELD_ORDER = (
    "schema_version",
    "execution_head",
    "authority_registry_sha256",
    "ref_snapshot_before_sha256",
    "ref_snapshot_after_sha256",
    "reference_count",
    "row_count",
    "source_count",
    "projection_count",
    "invalid_count",
    "rows",
    "effects",
    "status",
)
RECOVERY_CENSUS_ROW_FIELD_ORDER = (
    "path",
    "pinned_sha256",
    "pinned_creating_commit",
    "mode",
    "logical_source_commit",
    "declared_landing_projection_commit",
    "raw_touch_count",
    "source_count",
    "projection_count",
    "touches",
    "execution_head_contains_source",
    "head_blob_sha256",
    "worktree_sha256",
    "verdict",
)
RECOVERY_CENSUS_TOUCH_FIELD_ORDER = (
    "commit",
    "parents",
    "first_parent_status",
    "classification",
    "blob_sha256",
    "raw_bytes_equal_pinned",
    "source_is_ancestor_of_second_parent",
    "second_parent_to_merge_path_diff_empty",
)
RECOVERED_PUBLICATION_CAPABILITY_FIELD_ORDER = (
    "recovery_authorization_path",
    "recovery_authorization_sha256",
    "recovery_authorization_creating_commit",
    "owner_binding_path",
    "owner_binding_sha256",
    "owner_binding_creating_commit",
    "claim_root",
    "claim_started_sha256",
    "claim_terminal_sha256",
    "series_token_sha256",
    "selected_attempt_ordinal",
    "selected_implementation_epoch",
    "selected_implementation_commit",
    "sealed_history_root_sha256",
    "sealed_live_ledger_root_sha256",
    "destination",
    "published_bundle_sha256",
    "published_tree_sha256",
    "secondary_snapshot",
    "secondary_snapshot_tree_sha256",
    "primary_receipt_path",
    "secondary_receipt_path",
    "paired_receipt_sha256",
    "paired_receipt_bytes",
    "execution_epoch",
    "execution_implementation_commit",
    "execution_control_merkle_root_sha256",
    "recovery_starts",
    "pipeline_starts",
    "automatic_retry_count",
    "sealed_ledger_before_after_equal",
    "sealed_mirror_before_after_equal",
    "selected_candidate_sha256",
    "selected_terminal_sha256",
    "selected_evidence_tree_root_sha256",
    "historical_run_a_root_sha256",
    "historical_run_b_root_sha256",
    "historical_run_a_probe_sha256",
    "historical_run_b_probe_sha256",
    "historical_full_downstream_replay_verified",
)
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


MIRROR_RECEIPT_FIELDS = {
    "schema_version",
    "series_token_sha256",
    "ordinal",
    "attempt_outcome",
    "attempt_sealed",
    "primary_ledger_root",
    "secondary_snapshot_root",
    "history_root_sha256",
    "live_ledger_root_sha256",
    "file_count",
    "total_bytes",
    "primary_inventory_sha256",
    "secondary_inventory_sha256",
    "second_copy_verified",
    "verified_at_utc",
}
HOT_SECOND_COPY_COMMITMENT_SCHEMA = "p4.2a-v2-2-series-2-hot-second-copy-commitment-v1"
HOT_SECOND_COPY_FIXED_WORK_UNITS = 10
HOT_SECOND_COPY_PER_SNAPSHOT_WORK_UNITS = 6
HOT_SECOND_COPY_MAX_RECEIPT_BYTES = 4096
HOT_SECOND_COPY_MAX_METADATA_FILES = RECOVERY_WORK_LIMITS["sealed_snapshot_files_visited"]


@dataclass(frozen=True)
class _TreeInventory:
    rows: tuple[JsonObject, ...]
    payloads: Mapping[str, bytes]
    identities: Mapping[str, tuple[int, ...]]
    sha256: str
    file_count: int
    total_bytes: int


def _validate_tree_inventory_identities(
    root: Path,
    inventory: _TreeInventory,
    *,
    label: str,
    permit_root_metadata_refresh: bool = False,
) -> None:
    """Rebind an already-hashed tree without reading any payload again."""

    expected_rows = {
        cast(str, row["relative_path"]): row for row in inventory.rows
    }
    expected_paths = set(expected_rows)
    observed_paths = {"."}
    try:
        root_metadata = root.lstat()
        descendants = sorted(
            root.rglob("*"),
            key=lambda path: path.relative_to(root).as_posix().encode("utf-8"),
        )
    except OSError as exc:
        raise RehearsalV22Error(f"{label} identity tree is unavailable") from exc
    for path in descendants:
        relative = path.relative_to(root).as_posix()
        _relative_text(relative, f"{label} member")
        observed_paths.add(relative)
    if observed_paths != expected_paths:
        raise RehearsalV22Error(f"{label} member set changed after payload validation")
    for relative in sorted(expected_paths, key=lambda value: value.encode("utf-8")):
        path = root if relative == "." else root.joinpath(*PurePosixPath(relative).parts)
        try:
            metadata = root_metadata if relative == "." else path.lstat()
        except OSError as exc:
            raise RehearsalV22Error(f"{label} member identity is unavailable") from exc
        row = expected_rows[relative]
        kind = row["kind"]
        if path.is_symlink() or (
            kind == "directory" and not stat.S_ISDIR(metadata.st_mode)
        ) or (kind == "file" and not stat.S_ISREG(metadata.st_mode)):
            raise RehearsalV22Error(f"{label} member kind or alias changed")
        expected_mode = 0o700 if kind == "directory" else 0o600
        if (
            stat.S_IMODE(metadata.st_mode) != expected_mode
            or metadata.st_uid != os.getuid()
            or (kind == "file" and metadata.st_nlink != 1)
            or (kind == "file" and metadata.st_size != row["bytes"])
        ):
            raise RehearsalV22Error(f"{label} member metadata changed")
        expected_identity = inventory.identities.get(relative)
        if expected_identity is None:
            raise RehearsalV22Error(f"{label} identity commitment is incomplete")
        if not (permit_root_metadata_refresh and relative == ".") and (
            _stat_identity(metadata) != expected_identity
        ):
            raise RehearsalV22Error(f"{label} member identity changed")


def _hot_snapshot_metadata_commitment(
    root: Path,
    history: HistoryValidation,
    *,
    ordinal: int,
    expected_file_count: int,
    expected_total_bytes: int,
    remaining_file_budget: int,
) -> JsonObject:
    """Bind exact snapshot names and metadata without reading one payload byte."""

    expected_files = {
        relative
        for relative in history.live_file_inventory
        if not relative.startswith("attempts/")
        or int(PurePosixPath(relative).parts[1]) <= ordinal
    }
    if (
        len(expected_files) != expected_file_count
        or expected_file_count > remaining_file_budget
    ):
        raise RehearsalV22Error("hot mirror snapshot file count exceeds its commitment")
    expected_directories = {"."}
    expected_directories.update(
        f"attempts/{record.ordinal:06d}/evidence"
        for record in history.records[:ordinal]
    )
    for relative in expected_files:
        parent = PurePosixPath(relative).parent
        while parent.as_posix() != ".":
            expected_directories.add(parent.as_posix())
            parent = parent.parent

    observed_files: dict[str, tuple[int, int, int, tuple[int, ...]]] = {}
    observed_directories: dict[str, tuple[int, ...]] = {}
    try:
        for directory, raw_directories, raw_files in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            relative_directory = (
                "."
                if directory_path == root
                else directory_path.relative_to(root).as_posix()
            )
            if relative_directory != ".":
                _relative_text(relative_directory, "hot mirror snapshot directory")
            directory_metadata = directory_path.lstat()
            if (
                directory_path.is_symlink()
                or not stat.S_ISDIR(directory_metadata.st_mode)
                or stat.S_IMODE(directory_metadata.st_mode) != 0o700
                or directory_metadata.st_uid != os.getuid()
            ):
                raise RehearsalV22Error("hot mirror snapshot directory metadata drifted")
            observed_directories[relative_directory] = _stat_identity(directory_metadata)
            raw_directories.sort(key=lambda value: value.encode("utf-8"))
            raw_files.sort(key=lambda value: value.encode("utf-8"))
            for name in (*raw_directories, *raw_files):
                if name in {"", ".", ".."} or "/" in name:
                    raise RehearsalV22Error("hot mirror snapshot member name is unsafe")
            for name in raw_directories:
                child = directory_path / name
                child_metadata = child.lstat()
                if child.is_symlink() or not stat.S_ISDIR(child_metadata.st_mode):
                    raise RehearsalV22Error("hot mirror snapshot directory is aliased")
            for name in raw_files:
                child = directory_path / name
                child_metadata = child.lstat()
                relative = child.relative_to(root).as_posix()
                _relative_text(relative, "hot mirror snapshot file")
                if (
                    child.is_symlink()
                    or not stat.S_ISREG(child_metadata.st_mode)
                    or stat.S_IMODE(child_metadata.st_mode) != 0o600
                    or child_metadata.st_uid != os.getuid()
                    or child_metadata.st_nlink != 1
                ):
                    raise RehearsalV22Error("hot mirror snapshot file metadata drifted")
                observed_files[relative] = (
                    child_metadata.st_size,
                    stat.S_IMODE(child_metadata.st_mode),
                    child_metadata.st_nlink,
                    _stat_identity(child_metadata),
                )
                if len(observed_files) > remaining_file_budget:
                    raise RehearsalV22Error(
                        "hot mirror snapshot metadata work bound exceeded"
                    )
    except OSError as exc:
        raise RehearsalV22Error("hot mirror snapshot metadata is unavailable") from exc

    if (
        set(observed_files) != expected_files
        or set(observed_directories) != expected_directories
        or sum(row[0] for row in observed_files.values()) != expected_total_bytes
    ):
        raise RehearsalV22Error("hot mirror snapshot names or sizes drifted")
    for relative, identity in observed_directories.items():
        path = root if relative == "." else root.joinpath(*PurePosixPath(relative).parts)
        if _stat_identity(path.lstat()) != identity:
            raise RehearsalV22Error("hot mirror snapshot directory identity changed")
    for relative, row in observed_files.items():
        path = root.joinpath(*PurePosixPath(relative).parts)
        if _stat_identity(path.lstat()) != row[3]:
            raise RehearsalV22Error("hot mirror snapshot file identity changed")
    commitment_rows = [
        {
            "path": relative,
            "bytes": row[0],
            "mode": f"{row[1]:04o}",
            "nlink": row[2],
        }
        for relative, row in sorted(
            observed_files.items(), key=lambda item: item[0].encode("utf-8")
        )
    ]
    return {
        "file_count": len(observed_files),
        "directory_count": len(observed_directories),
        "total_bytes": expected_total_bytes,
        "name_metadata_commitment_sha256": _sha256(
            _canonical_json_bytes(commitment_rows)
        ),
    }


def _descriptor_path(descriptor: int, *, label: str) -> Path:
    try:
        raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, b"\0" * 1024)
    except (OSError, TypeError, ValueError) as exc:
        raise RehearsalV22Error(f"{label} descriptor path is unavailable") from exc
    terminator = raw.find(b"\0")
    if terminator <= 0:
        raise RehearsalV22Error(f"{label} descriptor path is unavailable")
    return Path(os.fsdecode(raw[:terminator])).absolute()


def _open_stable_read_descriptor(
    path: Path,
    *,
    label: str,
    directory: bool,
    parent_descriptor: int | None = None,
    entry_name: str | None = None,
) -> tuple[int, os.stat_result]:
    if (parent_descriptor is None) != (entry_name is None):
        raise RehearsalV22Error(f"{label} parent descriptor binding is incomplete")
    try:
        before = path.lstat()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        if directory:
            flags |= getattr(os, "O_DIRECTORY", 0)
        target: str | Path = cast(str, entry_name) if parent_descriptor is not None else path
        descriptor = os.open(target, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise RehearsalV22Error(f"{label} is unavailable or aliased") from exc
    try:
        opened = os.fstat(descriptor)
        after_open = path.lstat()
        descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        descriptor_fd_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
        expected_kind = stat.S_ISDIR(opened.st_mode) if directory else stat.S_ISREG(opened.st_mode)
        if (
            len({_stat_identity(before), _stat_identity(opened), _stat_identity(after_open)}) != 1
            or not expected_kind
            or _descriptor_path(descriptor, label=label) != path.absolute()
            or descriptor_flags & os.O_ACCMODE != os.O_RDONLY
            or descriptor_fd_flags & fcntl.FD_CLOEXEC == 0
        ):
            raise RehearsalV22Error(f"{label} descriptor identity changed or escaped")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, before


def _verify_stable_directory_descriptor(
    descriptor: int,
    path: Path,
    before: os.stat_result,
    *,
    label: str,
) -> None:
    descriptor_after = os.fstat(descriptor)
    path_after = path.lstat()
    if (
        len(
            {
                _stat_identity(before),
                _stat_identity(descriptor_after),
                _stat_identity(path_after),
            }
        )
        != 1
        or _descriptor_path(descriptor, label=label) != path.absolute()
    ):
        raise RehearsalV22Error(f"{label} directory changed during descriptor read")


def _close_stable_directory_descriptor(
    descriptor: int,
    path: Path,
    before: os.stat_result,
    *,
    label: str,
) -> None:
    try:
        _verify_stable_directory_descriptor(descriptor, path, before, label=label)
    finally:
        os.close(descriptor)


def _read_stable_regular_descriptor(
    path: Path,
    *,
    label: str,
    parent_descriptor: int,
    entry_name: str,
) -> tuple[bytes, os.stat_result]:
    descriptor, before = _open_stable_read_descriptor(
        path,
        label=label,
        directory=False,
        parent_descriptor=parent_descriptor,
        entry_name=entry_name,
    )
    if (
        stat.S_IMODE(before.st_mode) != 0o600
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
    ):
        os.close(descriptor)
        raise RehearsalV22Error(f"{label} is hardlinked, wrong-mode, or wrong-owner")
    chunks: list[bytes] = []
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        descriptor_after = os.fstat(descriptor)
        path_after = path.lstat()
        descriptor_path = _descriptor_path(descriptor, label=label)
    except BaseException:
        os.close(descriptor)
        raise
    os.close(descriptor)
    payload = b"".join(chunks)
    if (
        len(
            {
                _stat_identity(before),
                _stat_identity(descriptor_after),
                _stat_identity(path_after),
            }
        )
        != 1
        or descriptor_path != path.absolute()
        or len(payload) != before.st_size
    ):
        raise RehearsalV22Error(f"{label} changed during descriptor read")
    return payload, before


def _strict_private_tree_inventory(
    root: Path,
    *,
    label: str,
    root_descriptor: int | None = None,
    root_before: os.stat_result | None = None,
    root_parent_descriptor: int | None = None,
    root_entry_name: str | None = None,
    maximum_attempt_ordinal: int | None = None,
    expected_attempt_count: int | None = None,
) -> _TreeInventory:
    rows: list[JsonObject] = []
    payloads: dict[str, bytes] = {}
    identities: dict[str, tuple[int, ...]] = {}

    def walk_directory(
        path: Path,
        *,
        relative: str,
        parent_descriptor: int | None,
        entry_name: str | None,
        supplied_descriptor: int | None = None,
        supplied_before: os.stat_result | None = None,
    ) -> None:
        owns_descriptor = supplied_descriptor is None
        if supplied_descriptor is None:
            descriptor, before = _open_stable_read_descriptor(
                path,
                label=f"{label} directory {relative}",
                directory=True,
                parent_descriptor=parent_descriptor,
                entry_name=entry_name,
            )
        else:
            if supplied_before is None:
                raise RehearsalV22Error(f"{label} supplied root lacks identity")
            descriptor, before = supplied_descriptor, supplied_before
            _verify_stable_directory_descriptor(
                descriptor,
                path,
                before,
                label=f"{label} directory {relative}",
            )
        if stat.S_IMODE(before.st_mode) != 0o700 or before.st_uid != os.getuid():
            if owns_descriptor:
                os.close(descriptor)
            raise RehearsalV22Error(f"{label} directory mode or owner drifted")
        rows.append(
            {
                "relative_path": relative,
                "kind": "directory",
                "mode": 0o700,
                "bytes": 0,
                "sha256": None,
            }
        )
        identities[relative] = _stat_identity(before)
        try:
            names = sorted(os.listdir(descriptor), key=lambda value: value.encode("utf-8"))
            if relative == "attempts" and expected_attempt_count is not None:
                expected_names = [
                    f"{ordinal:06d}" for ordinal in range(1, expected_attempt_count + 1)
                ]
                if names != expected_names:
                    raise RehearsalV22Error(
                        f"{label} attempt ordinals contain a gap, extra, or noncanonical member"
                    )
            for name in names:
                if not isinstance(name, str) or name in {"", ".", ".."} or "/" in name:
                    raise RehearsalV22Error(f"{label} contains an invalid member")
                child = path / name
                child_relative = name if relative == "." else f"{relative}/{name}"
                _relative_text(child_relative, f"{label} member")
                if (
                    maximum_attempt_ordinal is not None
                    and relative == "attempts"
                    and re.fullmatch(r"[0-9]{6}", name) is not None
                    and int(name) > maximum_attempt_ordinal
                ):
                    continue
                child_metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(child_metadata.st_mode):
                    walk_directory(
                        child,
                        relative=child_relative,
                        parent_descriptor=descriptor,
                        entry_name=name,
                    )
                    continue
                if not stat.S_ISREG(child_metadata.st_mode):
                    raise RehearsalV22Error(f"{label} contains an alias or special member")
                payload, stable_metadata = _read_stable_regular_descriptor(
                    child,
                    label=f"{label} file {child_relative}",
                    parent_descriptor=descriptor,
                    entry_name=name,
                )
                payloads[child_relative] = payload
                identities[child_relative] = _stat_identity(stable_metadata)
                rows.append(
                    {
                        "relative_path": child_relative,
                        "kind": "file",
                        "mode": 0o600,
                        "bytes": len(payload),
                        "sha256": _sha256(payload),
                    }
                )
        except BaseException:
            if owns_descriptor:
                os.close(descriptor)
            raise
        if owns_descriptor:
            _close_stable_directory_descriptor(
                descriptor,
                path,
                before,
                label=f"{label} directory {relative}",
            )
        else:
            _verify_stable_directory_descriptor(
                descriptor,
                path,
                before,
                label=f"{label} directory {relative}",
            )

    walk_directory(
        root,
        relative=".",
        parent_descriptor=root_parent_descriptor,
        entry_name=root_entry_name,
        supplied_descriptor=root_descriptor,
        supplied_before=root_before,
    )
    if expected_attempt_count is not None and "attempts" not in identities:
        raise RehearsalV22Error(f"{label} omits the attempts directory")
    root_rows = [row for row in rows if row["relative_path"] == "."]
    if len(root_rows) != 1:
        raise RehearsalV22Error(f"{label} root inventory drifted")
    ordered_rows = tuple(
        root_rows
        + sorted(
            (row for row in rows if row["relative_path"] != "."),
            key=lambda row: cast(str, row["relative_path"]).encode("utf-8"),
        )
    )
    ordered_payloads = dict(sorted(payloads.items(), key=lambda item: item[0].encode("utf-8")))
    ordered_identities = dict(sorted(identities.items(), key=lambda item: item[0].encode("utf-8")))
    return _TreeInventory(
        rows=ordered_rows,
        payloads=ordered_payloads,
        identities=ordered_identities,
        sha256=_sha256(MIRROR_INVENTORY_PREFIX + _canonical_json_bytes(list(ordered_rows))),
        file_count=len(ordered_payloads),
        total_bytes=sum(len(payload) for payload in ordered_payloads.values()),
    )


def _strict_primary_prefix_inventory(
    root: Path,
    ordinal: int,
    *,
    expected_attempt_count: int,
    label: str,
    root_descriptor: int | None = None,
    root_before: os.stat_result | None = None,
) -> _TreeInventory:
    if ordinal < 1 or expected_attempt_count < ordinal:
        raise RehearsalV22Error(f"{label} prefix ordinal is invalid")
    return _strict_private_tree_inventory(
        root,
        label=label,
        root_descriptor=root_descriptor,
        root_before=root_before,
        maximum_attempt_ordinal=ordinal,
        expected_attempt_count=expected_attempt_count,
    )


def _mirror_receipt_filename(ordinal: int, live_root: str) -> str:
    if ordinal < 1 or not _lower_hex(live_root, 64):
        raise RehearsalV22Error("mirror receipt identity is invalid")
    return f"through-ordinal-{ordinal:06d}-{live_root}.mirror-verification.json"


def _inventory_through_ordinal(
    inventory: _TreeInventory,
    ordinal: int,
) -> _TreeInventory:
    def included(relative: str) -> bool:
        if relative in {".", "series.json", ".series.lock", "attempts"}:
            return True
        parts = PurePosixPath(relative).parts
        return (
            len(parts) >= 2
            and parts[0] == "attempts"
            and re.fullmatch(r"[0-9]{6}", parts[1]) is not None
            and int(parts[1]) <= ordinal
        )

    rows = tuple(
        copy.deepcopy(row) for row in inventory.rows if included(cast(str, row["relative_path"]))
    )
    payloads = {
        relative: payload for relative, payload in inventory.payloads.items() if included(relative)
    }
    identities = {
        relative: identity
        for relative, identity in inventory.identities.items()
        if included(relative)
    }
    return _TreeInventory(
        rows=rows,
        payloads=payloads,
        identities=identities,
        sha256=_sha256(MIRROR_INVENTORY_PREFIX + _canonical_json_bytes(list(rows))),
        file_count=len(payloads),
        total_bytes=sum(len(payload) for payload in payloads.values()),
    )


def _attempt_record_mirror_signature(record: ValidatedAttemptRecord) -> tuple[object, ...]:
    return (
        record.ordinal,
        record.outcome,
        record.reached_stage,
        record.attempt_token_sha256,
        record.previous_history_root_sha256,
        record.implementation_epoch,
        record.implementation_commit,
        record.owner_action_time_authorization,
        record.command_sha256,
        record.environment_sha256,
        record.started_bytes,
        record.started_sha256,
        record.candidate_bytes,
        record.candidate_sha256,
        record.terminal_bytes,
        record.terminal_sha256,
        record.evidence_tree_root_sha256,
        record.artifact_inventory,
        record.error,
        record.record_root_sha256,
        record.history_root_sha256,
    )


def _mirror_snapshot_name(ordinal: int, live_root: str) -> str:
    return f"through-ordinal-{ordinal:06d}-{live_root}"


def _strict_receipt_inventory(
    path: Path,
    label: str,
    *,
    root_descriptor: int | None = None,
    root_before: os.stat_result | None = None,
) -> _TreeInventory:
    inventory = _strict_private_tree_inventory(
        path,
        label=label,
        root_descriptor=root_descriptor,
        root_before=root_before,
    )
    if any(
        row["kind"] != "file" or len(PurePosixPath(cast(str, row["relative_path"])).parts) != 1
        for row in inventory.rows
        if row["relative_path"] != "."
    ):
        raise RehearsalV22Error(f"{label} contains a noncanonical receipt member")
    return inventory


def _strict_receipt_root(
    path: Path,
    label: str,
    *,
    root_descriptor: int | None = None,
    root_before: os.stat_result | None = None,
) -> tuple[tuple[str, bytes], ...]:
    inventory = _strict_receipt_inventory(
        path,
        label,
        root_descriptor=root_descriptor,
        root_before=root_before,
    )
    return tuple(inventory.payloads.items())


def _validate_hot_second_copy_commitment(
    binding: ExecutionBinding,
    history: HistoryValidation,
    *,
    allow_unmirrored_final: bool = False,
) -> JsonObject:
    """Bind immutable mirror descriptors without replaying snapshot payload trees.

    ``validate_live_history`` remains the caller's full validation of the active
    ledger.  The full recursive second-copy validator remains mandatory at
    registered read-only, recovery, bundle, and release boundaries.  Active
    continuation and seal capability consumption need only prove that the
    same create-only snapshot names and paired, canonical seal receipts still
    bind the already-validated history.  This keeps the hot work linear in the
    number of sealed ordinals and independent of accumulated snapshot bytes.
    """

    _validate_registered_storage_roots(binding)
    count = len(history.records)
    if allow_unmirrored_final and count == 0:
        raise RehearsalV22Error("empty history cannot permit an unmirrored final attempt")
    expected_verified = count - (1 if allow_unmirrored_final else 0)
    if expected_verified < 0:
        raise RehearsalV22Error("hot mirror commitment count is invalid")
    leaves = (
        binding.secondary_snapshot_root,
        binding.primary_receipt_root,
        binding.secondary_receipt_root,
    )
    present = tuple(os.path.lexists(path) for path in leaves)
    if expected_verified == 0:
        if present != (False, False, False):
            raise RehearsalV22Error(
                "unmirrored history has persistent mirror initialization residue"
            )
    elif present != (True, True, True):
        raise RehearsalV22Error("series-2 hot mirror leaves are partial or absent")

    held_roots: list[tuple[int, Path, os.stat_result, str]] = []
    held_snapshots: list[tuple[int, Path, os.stat_result, str]] = []
    receipt_identities: list[tuple[int, str, tuple[int, ...], str]] = []

    def open_root(
        path: Path,
        label: str,
        *,
        parent_descriptor: int | None = None,
        entry_name: str | None = None,
    ) -> tuple[int, Path, os.stat_result, str]:
        descriptor, before = _open_stable_read_descriptor(
            path,
            label=label,
            directory=True,
            parent_descriptor=parent_descriptor,
            entry_name=entry_name,
        )
        if stat.S_IMODE(before.st_mode) != 0o700 or before.st_uid != os.getuid():
            os.close(descriptor)
            raise RehearsalV22Error(f"{label} owner or mode drifted")
        result = (descriptor, path, before, label)
        held_roots.append(result)
        return result

    def direct_names(descriptor: int, label: str) -> list[str]:
        names = sorted(os.listdir(descriptor), key=lambda value: value.encode("utf-8"))
        if any(
            not isinstance(name, str) or name in {"", ".", ".."} or "/" in name for name in names
        ):
            raise RehearsalV22Error(f"{label} contains an invalid direct member")
        return names

    def receipt_payloads(
        root: tuple[int, Path, os.stat_result, str],
    ) -> tuple[tuple[str, bytes], ...]:
        descriptor, path, _before, label = root
        result: list[tuple[str, bytes]] = []
        for name in direct_names(descriptor, label):
            child = path / name
            payload, metadata = _read_stable_regular_descriptor(
                child,
                label=f"{label} receipt {name}",
                parent_descriptor=descriptor,
                entry_name=name,
            )
            if len(payload) > HOT_SECOND_COPY_MAX_RECEIPT_BYTES:
                raise RehearsalV22Error("hot mirror receipt exceeds its registered byte bound")
            receipt_identities.append(
                (descriptor, name, _stat_identity(metadata), f"{label} receipt {name}")
            )
            result.append((name, payload))
        return tuple(result)

    result: JsonObject | None = None
    try:
        primary_container = open_root(
            binding.primary_series_container,
            "series-2 hot primary owner container",
        )
        secondary_container = open_root(
            binding.secondary_series_container,
            "series-2 hot secondary owner container",
        )
        primary_names = direct_names(primary_container[0], primary_container[3])
        secondary_names = direct_names(secondary_container[0], secondary_container[3])
        expected_primary_names = [binding.ledger_root.name] if history.ledger_exists else []
        if expected_verified:
            expected_primary_names.append(binding.primary_receipt_root.name)
        expected_secondary_names = (
            sorted(
                [
                    binding.secondary_snapshot_root.name,
                    binding.secondary_receipt_root.name,
                ],
                key=lambda value: value.encode("utf-8"),
            )
            if expected_verified
            else []
        )
        if (
            primary_names
            != sorted(
                expected_primary_names,
                key=lambda value: value.encode("utf-8"),
            )
            or secondary_names != expected_secondary_names
        ):
            raise RehearsalV22Error(
                "series-2 owner container contains an unregistered direct member"
            )

        commitment_rows: list[JsonObject] = []
        snapshot_names: list[str] = []
        snapshot_metadata_files_visited = 0
        snapshot_metadata_directories_visited = 0
        primary_receipts: tuple[tuple[str, bytes], ...] = ()
        secondary_receipts: tuple[tuple[str, bytes], ...] = ()
        if expected_verified:
            snapshot_root = open_root(
                binding.secondary_snapshot_root,
                "series-2 hot snapshot root",
                parent_descriptor=secondary_container[0],
                entry_name=binding.secondary_snapshot_root.name,
            )
            primary_receipt_root = open_root(
                binding.primary_receipt_root,
                "series-2 hot primary receipt root",
                parent_descriptor=primary_container[0],
                entry_name=binding.primary_receipt_root.name,
            )
            secondary_receipt_root = open_root(
                binding.secondary_receipt_root,
                "series-2 hot secondary receipt root",
                parent_descriptor=secondary_container[0],
                entry_name=binding.secondary_receipt_root.name,
            )
            snapshot_names = direct_names(snapshot_root[0], snapshot_root[3])
            if len(snapshot_names) != expected_verified:
                raise RehearsalV22Error("hot mirror snapshot count blocks capability use")
            for name in snapshot_names:
                snapshot = binding.secondary_snapshot_root / name
                descriptor, before = _open_stable_read_descriptor(
                    snapshot,
                    label=f"series-2 hot held snapshot {name}",
                    directory=True,
                    parent_descriptor=snapshot_root[0],
                    entry_name=name,
                )
                if stat.S_IMODE(before.st_mode) != 0o700 or before.st_uid != os.getuid():
                    os.close(descriptor)
                    raise RehearsalV22Error("hot mirror snapshot owner or mode drifted")
                held_snapshots.append(
                    (descriptor, snapshot, before, f"series-2 hot held snapshot {name}")
                )
            primary_receipts = receipt_payloads(primary_receipt_root)
            secondary_receipts = receipt_payloads(secondary_receipt_root)
            if primary_receipts != secondary_receipts:
                raise RehearsalV22Error("hot paired mirror receipt bytes differ")
            if len(primary_receipts) != expected_verified:
                raise RehearsalV22Error("hot mirror receipt count blocks capability use")

            observed_snapshot_names: list[str] = []
            for expected_ordinal, (filename, payload) in enumerate(primary_receipts, 1):
                document = _object(
                    strict_json_loads(
                        payload,
                        source=f"hot mirror receipt {expected_ordinal}",
                    ),
                    f"hot mirror receipt {expected_ordinal}",
                )
                if (
                    set(document) != MIRROR_RECEIPT_FIELDS
                    or _canonical_json_bytes(document) != payload
                ):
                    raise RehearsalV22Error("hot mirror receipt is not exact canonical JSON")
                live_root = document.get("live_ledger_root_sha256")
                history_root = document.get("history_root_sha256")
                primary_inventory_root = document.get("primary_inventory_sha256")
                secondary_inventory_root = document.get("secondary_inventory_sha256")
                file_count = document.get("file_count")
                total_bytes = document.get("total_bytes")
                record = history.records[expected_ordinal - 1]
                snapshot_name = (
                    _mirror_snapshot_name(
                        expected_ordinal,
                        cast(str, live_root),
                    )
                    if _lower_hex(live_root, 64)
                    else ""
                )
                snapshot_path = binding.secondary_snapshot_root / snapshot_name
                if (
                    document.get("schema_version") != MIRROR_RECEIPT_SCHEMA
                    or document.get("series_token_sha256") != binding.series_token_sha256
                    or type(document.get("ordinal")) is not int
                    or document.get("ordinal") != expected_ordinal
                    or document.get("attempt_outcome") != record.outcome
                    or document.get("attempt_sealed")
                    is not (record.outcome != "INCOMPLETE_UNTERMINALIZED")
                    or document.get("primary_ledger_root") != binding.ledger_root.as_posix()
                    or not _lower_hex(live_root, 64)
                    or not _lower_hex(history_root, 64)
                    or history_root != record.history_root_sha256
                    or not _lower_hex(primary_inventory_root, 64)
                    or primary_inventory_root != secondary_inventory_root
                    or type(file_count) is not int
                    or file_count < 1
                    or type(total_bytes) is not int
                    or total_bytes < 1
                    or filename != _mirror_receipt_filename(expected_ordinal, cast(str, live_root))
                    or document.get("secondary_snapshot_root") != snapshot_path.as_posix()
                    or document.get("second_copy_verified") is not True
                    or document.get("verified_at_utc") != FIXED_WALL_CLOCK_TEXT
                ):
                    raise RehearsalV22Error("hot mirror receipt binding drifted")
                if (
                    expected_ordinal == count
                    and expected_verified == count
                    and (
                        history_root != history.history_root_sha256
                        or live_root != history.live_ledger_root_sha256
                    )
                ):
                    raise RehearsalV22Error(
                        "latest hot mirror receipt does not bind the active ledger"
                    )
                metadata = _hot_snapshot_metadata_commitment(
                    snapshot_path,
                    history,
                    ordinal=expected_ordinal,
                    expected_file_count=file_count,
                    expected_total_bytes=total_bytes,
                    remaining_file_budget=(
                        HOT_SECOND_COPY_MAX_METADATA_FILES
                        - snapshot_metadata_files_visited
                    ),
                )
                snapshot_metadata_files_visited += cast(int, metadata["file_count"])
                snapshot_metadata_directories_visited += cast(
                    int,
                    metadata["directory_count"],
                )
                observed_snapshot_names.append(snapshot_name)
                commitment_rows.append(
                    {
                        "ordinal": expected_ordinal,
                        "receipt_name": filename,
                        "receipt_sha256": _sha256(payload),
                        "snapshot_name": snapshot_name,
                        "history_root_sha256": history_root,
                        "live_ledger_root_sha256": live_root,
                        "attempt_outcome": record.outcome,
                        "attempt_sealed": document.get("attempt_sealed"),
                        "file_count": file_count,
                        "total_bytes": total_bytes,
                        "primary_inventory_sha256": primary_inventory_root,
                        "secondary_inventory_sha256": secondary_inventory_root,
                        "name_metadata_commitment_sha256": metadata[
                            "name_metadata_commitment_sha256"
                        ],
                    }
                )
            if snapshot_names != sorted(
                observed_snapshot_names,
                key=lambda value: value.encode("utf-8"),
            ):
                raise RehearsalV22Error("hot mirror snapshot names differ from receipts")

            if (
                direct_names(snapshot_root[0], snapshot_root[3]) != snapshot_names
                or tuple(name for name, _payload in primary_receipts)
                != tuple(direct_names(primary_receipt_root[0], primary_receipt_root[3]))
                or tuple(name for name, _payload in secondary_receipts)
                != tuple(direct_names(secondary_receipt_root[0], secondary_receipt_root[3]))
            ):
                raise RehearsalV22Error("hot mirror roots changed during commitment read")
            for descriptor, name, identity, label in receipt_identities:
                current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if _stat_identity(current) != identity:
                    raise RehearsalV22Error(f"{label} identity changed after read")

        if (
            direct_names(primary_container[0], primary_container[3]) != primary_names
            or direct_names(secondary_container[0], secondary_container[3]) != secondary_names
        ):
            raise RehearsalV22Error("series-2 owner container changed during hot validation")

        roots_inspected = 2 + (3 if expected_verified else 0) + len(snapshot_names)
        container_entries_inspected = len(primary_names) + len(secondary_names)
        snapshot_entries_inspected = len(snapshot_names)
        receipt_entries_inspected = len(primary_receipts) + len(secondary_receipts)
        receipt_payloads_read = len(primary_receipts) + len(secondary_receipts)
        receipt_payload_bytes_read = sum(
            len(payload) for _name, payload in (*primary_receipts, *secondary_receipts)
        )
        work_units = (
            roots_inspected
            + container_entries_inspected
            + snapshot_entries_inspected
            + receipt_entries_inspected
            + receipt_payloads_read
        )
        work_limit = (
            HOT_SECOND_COPY_FIXED_WORK_UNITS
            + HOT_SECOND_COPY_PER_SNAPSHOT_WORK_UNITS * expected_verified
        )
        receipt_byte_limit = 2 * HOT_SECOND_COPY_MAX_RECEIPT_BYTES * expected_verified
        snapshot_payload_files_hashed = 0
        snapshot_payload_bytes_hashed = 0
        if (
            work_units > work_limit
            or receipt_payload_bytes_read > receipt_byte_limit
            or snapshot_payload_files_hashed != 0
            or snapshot_payload_bytes_hashed != 0
        ):
            raise RehearsalV22Error("hot mirror commitment exceeded its linear work bound")
        result = {
            "schema_version": HOT_SECOND_COPY_COMMITMENT_SCHEMA,
            "sealed_snapshot_count": expected_verified,
            "commitment_sha256": _sha256(_canonical_json_bytes(commitment_rows)),
            "commitment_rows": commitment_rows,
            "work": {
                "root_directories_inspected": roots_inspected,
                "container_entries_inspected": container_entries_inspected,
                "snapshot_entries_inspected": snapshot_entries_inspected,
                "receipt_entries_inspected": receipt_entries_inspected,
                "receipt_payloads_read": receipt_payloads_read,
                "receipt_payload_bytes_read": receipt_payload_bytes_read,
                "snapshot_payload_files_hashed": snapshot_payload_files_hashed,
                "snapshot_payload_bytes_hashed": snapshot_payload_bytes_hashed,
                "snapshot_metadata_files_visited": snapshot_metadata_files_visited,
                "snapshot_metadata_directories_visited": (
                    snapshot_metadata_directories_visited
                ),
                "snapshot_metadata_file_limit": HOT_SECOND_COPY_MAX_METADATA_FILES,
                "work_units": work_units,
                "linear_work_unit_limit": work_limit,
                "receipt_payload_byte_limit": receipt_byte_limit,
            },
        }
    except BaseException:
        for descriptor, _path, _before, _label in reversed(held_snapshots):
            with suppress(OSError):
                os.close(descriptor)
        for descriptor, _path, _before, _label in reversed(held_roots):
            with suppress(OSError):
                os.close(descriptor)
        raise

    close_error: BaseException | None = None
    for descriptor, path, before, label in reversed(held_snapshots):
        try:
            _close_stable_directory_descriptor(descriptor, path, before, label=label)
        except BaseException as exc:
            if close_error is None:
                close_error = exc
    for descriptor, path, before, label in reversed(held_roots):
        try:
            _close_stable_directory_descriptor(descriptor, path, before, label=label)
        except BaseException as exc:
            if close_error is None:
                close_error = exc
    if close_error is not None:
        raise close_error
    if result is None:
        raise RehearsalV22Error("hot mirror commitment produced no observation")
    return result


def _validate_second_copy_history(
    binding: ExecutionBinding,
    history: HistoryValidation,
    *,
    allow_unmirrored_final: bool = False,
    historical_authority_bytes: bool = False,
) -> tuple[JsonObject, ...]:
    """Descriptor-bind primary, snapshots, and both receipts for one full pass."""

    _validate_registered_storage_roots(binding)
    count = len(history.records)
    leaves = (
        binding.secondary_snapshot_root,
        binding.primary_receipt_root,
        binding.secondary_receipt_root,
    )
    present = tuple(os.path.lexists(path) for path in leaves)
    if count == 0:
        if any(present):
            raise RehearsalV22Error("mirror leaves exist before the first sealed attempt")
        return ()
    expected_verified = count - (1 if allow_unmirrored_final else 0)
    if expected_verified == 0:
        if present == (False, False, False):
            return ()
        raise RehearsalV22Error(
            "first unmirrored attempt has persistent mirror initialization residue"
        )
    if present != (True, True, True):
        raise RehearsalV22Error("series-2 mirror leaves are partial or absent")

    held_roots: list[tuple[int, Path, os.stat_result, str]] = []
    held_snapshots: list[tuple[int, Path, os.stat_result, str, str]] = []

    def open_root(
        path: Path,
        label: str,
        *,
        parent_descriptor: int | None = None,
        entry_name: str | None = None,
    ) -> tuple[int, Path, os.stat_result, str]:
        descriptor, before = _open_stable_read_descriptor(
            path,
            label=label,
            directory=True,
            parent_descriptor=parent_descriptor,
            entry_name=entry_name,
        )
        if stat.S_IMODE(before.st_mode) != 0o700 or before.st_uid != os.getuid():
            os.close(descriptor)
            raise RehearsalV22Error(f"{label} owner or mode drifted")
        result = (descriptor, path, before, label)
        held_roots.append(result)
        return result

    try:
        primary_container = open_root(
            binding.primary_series_container,
            "series-2 primary owner container",
        )
        secondary_container = open_root(
            binding.secondary_series_container,
            "series-2 secondary owner container",
        )
        primary_root = open_root(
            binding.ledger_root,
            "series-2 primary live-ledger root",
            parent_descriptor=primary_container[0],
            entry_name=binding.ledger_root.name,
        )
        snapshot_root = open_root(
            binding.secondary_snapshot_root,
            "series-2 snapshot root",
            parent_descriptor=secondary_container[0],
            entry_name=binding.secondary_snapshot_root.name,
        )
        primary_receipt_root = open_root(
            binding.primary_receipt_root,
            "series-2 primary receipt root",
            parent_descriptor=primary_container[0],
            entry_name=binding.primary_receipt_root.name,
        )
        secondary_receipt_root = open_root(
            binding.secondary_receipt_root,
            "series-2 secondary receipt root",
            parent_descriptor=secondary_container[0],
            entry_name=binding.secondary_receipt_root.name,
        )

        primary_receipt_inventory = _strict_receipt_inventory(
            binding.primary_receipt_root,
            "series-2 primary receipt root",
            root_descriptor=primary_receipt_root[0],
            root_before=primary_receipt_root[2],
        )
        secondary_receipt_inventory = _strict_receipt_inventory(
            binding.secondary_receipt_root,
            "series-2 secondary receipt root",
            root_descriptor=secondary_receipt_root[0],
            root_before=secondary_receipt_root[2],
        )
        primary_receipts = tuple(primary_receipt_inventory.payloads.items())
        if primary_receipts != tuple(secondary_receipt_inventory.payloads.items()):
            raise RehearsalV22Error("series-2 paired mirror receipt bytes differ")
        if len(primary_receipts) != expected_verified:
            raise RehearsalV22Error("series-2 mirror receipt count blocks continuation")

        snapshot_names = sorted(
            os.listdir(snapshot_root[0]),
            key=lambda value: value.encode("utf-8"),
        )
        if len(snapshot_names) != expected_verified or any(
            not isinstance(name, str) or name in {"", ".", ".."} or "/" in name
            for name in snapshot_names
        ):
            raise RehearsalV22Error("series-2 mirror snapshot count or name blocks continuation")
        snapshot_handles: dict[str, tuple[int, Path, os.stat_result, str, str]] = {}
        for name in snapshot_names:
            path = binding.secondary_snapshot_root / name
            descriptor, before = _open_stable_read_descriptor(
                path,
                label=f"series-2 held snapshot {name}",
                directory=True,
                parent_descriptor=snapshot_root[0],
                entry_name=name,
            )
            if stat.S_IMODE(before.st_mode) != 0o700 or before.st_uid != os.getuid():
                os.close(descriptor)
                raise RehearsalV22Error("series-2 held snapshot owner or mode drifted")
            handle = (descriptor, path, before, f"series-2 held snapshot {name}", name)
            held_snapshots.append(handle)
            snapshot_handles[name] = handle

        prefix_mode = allow_unmirrored_final and expected_verified < count
        current_primary_inventory = (
            _strict_primary_prefix_inventory(
                binding.ledger_root,
                expected_verified,
                expected_attempt_count=count,
                label="series-2 already-mirrored primary prefix",
                root_descriptor=primary_root[0],
                root_before=primary_root[2],
            )
            if prefix_mode
            else _strict_private_tree_inventory(
                binding.ledger_root,
                label="series-2 current primary live ledger",
                root_descriptor=primary_root[0],
                root_before=primary_root[2],
            )
        )
        if not prefix_mode:
            replayed_primary = validate_live_history(
                binding,
                historical_authority_bytes=historical_authority_bytes,
            )
            if replayed_primary != history:
                raise RehearsalV22Error(
                    "primary live history changed during second-copy validation"
                )
            replay_bound_primary = _strict_private_tree_inventory(
                binding.ledger_root,
                label="series-2 replay-bound primary live ledger",
                root_descriptor=primary_root[0],
                root_before=primary_root[2],
            )
            if (
                replay_bound_primary.rows != current_primary_inventory.rows
                or replay_bound_primary.payloads != current_primary_inventory.payloads
                or replay_bound_primary.identities != current_primary_inventory.identities
            ):
                raise RehearsalV22Error("primary live ledger changed during history replay")

        receipts: list[JsonObject] = []
        observed_snapshot_names: set[str] = set()
        initial_snapshot_inventories: dict[str, _TreeInventory] = {}
        for expected_ordinal, (filename, payload) in enumerate(primary_receipts, 1):
            document = _object(
                strict_json_loads(payload, source=f"mirror receipt {expected_ordinal}"),
                f"mirror receipt {expected_ordinal}",
            )
            if set(document) != MIRROR_RECEIPT_FIELDS or _canonical_json_bytes(document) != payload:
                raise RehearsalV22Error("mirror receipt is not exact canonical JSON")
            live_root = document.get("live_ledger_root_sha256")
            if (
                document.get("schema_version") != MIRROR_RECEIPT_SCHEMA
                or document.get("series_token_sha256") != binding.series_token_sha256
                or document.get("ordinal") != expected_ordinal
                or document.get("primary_ledger_root") != binding.ledger_root.as_posix()
                or not _lower_hex(live_root, 64)
                or filename != _mirror_receipt_filename(expected_ordinal, cast(str, live_root))
                or document.get("second_copy_verified") is not True
                or document.get("verified_at_utc") != FIXED_WALL_CLOCK_TEXT
            ):
                raise RehearsalV22Error("mirror receipt identity or binding drifted")
            snapshot_name = _mirror_snapshot_name(
                expected_ordinal,
                cast(str, live_root),
            )
            snapshot = binding.secondary_snapshot_root / snapshot_name
            if (
                document.get("secondary_snapshot_root") != snapshot.as_posix()
                or snapshot_name not in snapshot_handles
            ):
                raise RehearsalV22Error("mirror receipt snapshot binding drifted")
            snapshot_handle = snapshot_handles[snapshot_name]
            snapshot_inventory = _strict_private_tree_inventory(
                snapshot,
                label=f"series-2 snapshot {expected_ordinal}",
                root_descriptor=snapshot_handle[0],
                root_before=snapshot_handle[2],
            )
            initial_snapshot_inventories[snapshot_name] = snapshot_inventory
            snapshot_history = validate_live_history(
                binding,
                ledger_root=snapshot,
                historical_authority_bytes=historical_authority_bytes,
            )
            replay_bound_snapshot = _strict_private_tree_inventory(
                snapshot,
                label=f"series-2 replay-bound snapshot {expected_ordinal}",
                root_descriptor=snapshot_handle[0],
                root_before=snapshot_handle[2],
            )
            if (
                replay_bound_snapshot.rows != snapshot_inventory.rows
                or replay_bound_snapshot.payloads != snapshot_inventory.payloads
                or replay_bound_snapshot.identities != snapshot_inventory.identities
            ):
                raise RehearsalV22Error("mirror snapshot changed during history replay")
            record = snapshot_history.records[-1] if snapshot_history.records else None
            expected_record = history.records[expected_ordinal - 1]
            if (
                len(snapshot_history.records) != expected_ordinal
                or record is None
                or record.outcome != expected_record.outcome
                or tuple(
                    _attempt_record_mirror_signature(item) for item in snapshot_history.records
                )
                != tuple(
                    _attempt_record_mirror_signature(item)
                    for item in history.records[:expected_ordinal]
                )
                or snapshot_history.history_root_sha256 != document.get("history_root_sha256")
                or document.get("history_root_sha256")
                != history.records[expected_ordinal - 1].history_root_sha256
                or snapshot_history.live_ledger_root_sha256 != live_root
                or document.get("attempt_outcome") != expected_record.outcome
                or document.get("attempt_sealed")
                is not (expected_record.outcome != "INCOMPLETE_UNTERMINALIZED")
            ):
                raise RehearsalV22Error("mirror snapshot history differs from its receipt")
            primary_prefix_inventory = _inventory_through_ordinal(
                current_primary_inventory,
                expected_ordinal,
            )
            if (
                snapshot_inventory.rows != primary_prefix_inventory.rows
                or snapshot_inventory.payloads != primary_prefix_inventory.payloads
                or document.get("file_count") != snapshot_inventory.file_count
                or document.get("total_bytes") != snapshot_inventory.total_bytes
                or document.get("primary_inventory_sha256") != snapshot_inventory.sha256
                or document.get("primary_inventory_sha256") != primary_prefix_inventory.sha256
                or document.get("secondary_inventory_sha256") != snapshot_inventory.sha256
            ):
                raise RehearsalV22Error("mirror snapshot inventory differs from its receipt")
            observed_snapshot_names.add(snapshot_name)
            receipts.append(document)
        if set(snapshot_names) != observed_snapshot_names:
            raise RehearsalV22Error("mirror snapshot root contains a staging or extra artifact")

        final_primary_receipt_inventory = _strict_receipt_inventory(
            binding.primary_receipt_root,
            "series-2 final primary receipt root recheck",
            root_descriptor=primary_receipt_root[0],
            root_before=primary_receipt_root[2],
        )
        final_secondary_receipt_inventory = _strict_receipt_inventory(
            binding.secondary_receipt_root,
            "series-2 final secondary receipt root recheck",
            root_descriptor=secondary_receipt_root[0],
            root_before=secondary_receipt_root[2],
        )
        for initial, final, label in (
            (
                primary_receipt_inventory,
                final_primary_receipt_inventory,
                "primary receipt root",
            ),
            (
                secondary_receipt_inventory,
                final_secondary_receipt_inventory,
                "secondary receipt root",
            ),
        ):
            if (
                final.rows != initial.rows
                or final.payloads != initial.payloads
                or final.identities != initial.identities
            ):
                raise RehearsalV22Error(f"{label} changed during cross-tree mirror validation")
        final_snapshot_names = sorted(
            os.listdir(snapshot_root[0]),
            key=lambda value: value.encode("utf-8"),
        )
        if final_snapshot_names != snapshot_names:
            raise RehearsalV22Error("snapshot root changed during cross-tree mirror validation")
        for snapshot_name, initial_inventory in initial_snapshot_inventories.items():
            handle = snapshot_handles[snapshot_name]
            final_inventory = _strict_private_tree_inventory(
                handle[1],
                label=f"series-2 final snapshot {snapshot_name} recheck",
                root_descriptor=handle[0],
                root_before=handle[2],
            )
            if (
                final_inventory.rows != initial_inventory.rows
                or final_inventory.payloads != initial_inventory.payloads
                or final_inventory.identities != initial_inventory.identities
            ):
                raise RehearsalV22Error(
                    "snapshot bytes or identity changed during mirror validation"
                )
        final_primary_inventory = (
            _strict_primary_prefix_inventory(
                binding.ledger_root,
                expected_verified,
                expected_attempt_count=count,
                label="series-2 final already-mirrored primary prefix recheck",
                root_descriptor=primary_root[0],
                root_before=primary_root[2],
            )
            if prefix_mode
            else _strict_private_tree_inventory(
                binding.ledger_root,
                label="series-2 final primary live ledger recheck",
                root_descriptor=primary_root[0],
                root_before=primary_root[2],
            )
        )
        if (
            final_primary_inventory.rows != current_primary_inventory.rows
            or final_primary_inventory.payloads != current_primary_inventory.payloads
            or final_primary_inventory.identities != current_primary_inventory.identities
        ):
            raise RehearsalV22Error("primary live ledger changed during mirror validation")
        if not prefix_mode:
            latest = receipts[-1]
            if (
                latest.get("history_root_sha256") != history.history_root_sha256
                or latest.get("live_ledger_root_sha256") != history.live_ledger_root_sha256
                or latest.get("primary_inventory_sha256") != final_primary_inventory.sha256
                or latest.get("file_count") != final_primary_inventory.file_count
                or latest.get("total_bytes") != final_primary_inventory.total_bytes
            ):
                raise RehearsalV22Error("latest mirror does not bind the current live ledger")
        result = tuple(receipts)
    except BaseException:
        for descriptor, _path, _before, _label, _name in held_snapshots:
            with suppress(OSError):
                os.close(descriptor)
        for descriptor, _path, _before, _label in reversed(held_roots):
            with suppress(OSError):
                os.close(descriptor)
        raise

    close_error: BaseException | None = None
    for descriptor, path, before, label, _name in reversed(held_snapshots):
        try:
            _close_stable_directory_descriptor(
                descriptor,
                path,
                before,
                label=label,
            )
        except BaseException as exc:
            if close_error is None:
                close_error = exc
    for descriptor, path, before, label in reversed(held_roots):
        try:
            _close_stable_directory_descriptor(
                descriptor,
                path,
                before,
                label=label,
            )
        except BaseException as exc:
            if close_error is None:
                close_error = exc
    if close_error is not None:
        raise close_error
    return result


def _publish_mirror_snapshot_exclusive(
    source: Path,
    destination: Path,
    *,
    parent_descriptor: int,
) -> JsonObject:
    policy = _AUDIT_POLICY.get()
    if (
        policy is None
        or not _audit_policy_is_issued(policy)
        or policy.mirror_write_phase != "publish"
        or policy.mirror_publish_paths != (source, destination)
        or policy.mirror_staging_root != source
        or policy.mirror_snapshot_root is None
        or source.parent != policy.mirror_snapshot_root
        or destination.parent != policy.mirror_snapshot_root
        or source == destination
        or not source.name.startswith(".staging-through-ordinal-")
        or not destination.name.startswith("through-ordinal-")
    ):
        raise RehearsalV22Error("mirror publish lacks its exact issued same-parent authority")
    return _rename_mirror_directory_exclusive(
        source,
        destination,
        parent_descriptor=parent_descriptor,
    )


def _validate_series_lock_held(ledger: SeriesLedger) -> None:
    binding = ledger.binding
    descriptor = ledger.lock_descriptor
    if not ledger.locked or descriptor is None or descriptor < 0:
        raise RehearsalV22Error("mirror requires the held exclusive series lock")
    lock_path = binding.ledger_root / ".series.lock"
    try:
        raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, b"\0" * 1024)
        terminator = raw.find(b"\0")
        descriptor_path = Path(os.fsdecode(raw[:terminator]))
        descriptor_metadata = os.fstat(descriptor)
        path_metadata = lock_path.lstat()
        descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    except (OSError, TypeError, ValueError) as exc:
        raise RehearsalV22Error("exclusive series lock identity is unavailable") from exc
    if (
        terminator <= 0
        or descriptor_path.resolve(strict=True) != lock_path.resolve(strict=True)
        or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
        != (path_metadata.st_dev, path_metadata.st_ino)
        or not stat.S_ISREG(descriptor_metadata.st_mode)
        or descriptor_flags & os.O_ACCMODE != os.O_RDONLY
    ):
        raise RehearsalV22Error("exclusive series lock descriptor identity drifted")


def _verify_current_private_directory_descriptor(
    descriptor: int,
    path: Path,
    *,
    label: str,
) -> None:
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
    except OSError as exc:
        raise RehearsalV22Error(f"{label} identity is unavailable") from exc
    if (
        _stat_identity(opened) != _stat_identity(current)
        or _descriptor_path(descriptor, label=label) != path.absolute()
        or not stat.S_ISDIR(opened.st_mode)
        or stat.S_IMODE(opened.st_mode) != 0o700
        or opened.st_uid != os.getuid()
    ):
        raise RehearsalV22Error(f"{label} descriptor identity drifted")


@contextmanager
def _held_mirror_write_roots(
    binding: ExecutionBinding,
    *,
    initialize_roots: bool,
    mirror_phase: Callable[[str], Any],
) -> Iterator[Mapping[str, int]]:
    held: list[tuple[str, int, Path]] = []

    def open_root(
        key: str,
        path: Path,
        *,
        parent_descriptor: int | None = None,
        entry_name: str | None = None,
    ) -> int:
        descriptor, before = _open_stable_read_descriptor(
            path,
            label=f"held mirror {key}",
            directory=True,
            parent_descriptor=parent_descriptor,
            entry_name=entry_name,
        )
        if stat.S_IMODE(before.st_mode) != 0o700 or before.st_uid != os.getuid():
            os.close(descriptor)
            raise RehearsalV22Error(f"held mirror {key} owner or mode drifted")
        held.append((key, descriptor, path))
        return descriptor

    try:
        primary_container_descriptor = open_root(
            "primary owner container",
            binding.primary_series_container,
        )
        secondary_container_descriptor = open_root(
            "secondary owner container",
            binding.secondary_series_container,
        )
        ledger_descriptor = open_root(
            "primary ledger root",
            binding.ledger_root,
            parent_descriptor=primary_container_descriptor,
            entry_name=binding.ledger_root.name,
        )
        if initialize_roots:
            with mirror_phase("initialize"):
                for parent_descriptor, path in (
                    (secondary_container_descriptor, binding.secondary_snapshot_root),
                    (primary_container_descriptor, binding.primary_receipt_root),
                    (secondary_container_descriptor, binding.secondary_receipt_root),
                ):
                    os.mkdir(path.name, 0o700, dir_fd=parent_descriptor)
                    os.fsync(parent_descriptor)
        snapshot_descriptor = open_root(
            "snapshot root",
            binding.secondary_snapshot_root,
            parent_descriptor=secondary_container_descriptor,
            entry_name=binding.secondary_snapshot_root.name,
        )
        primary_receipt_descriptor = open_root(
            "primary receipt root",
            binding.primary_receipt_root,
            parent_descriptor=primary_container_descriptor,
            entry_name=binding.primary_receipt_root.name,
        )
        secondary_receipt_descriptor = open_root(
            "secondary receipt root",
            binding.secondary_receipt_root,
            parent_descriptor=secondary_container_descriptor,
            entry_name=binding.secondary_receipt_root.name,
        )
        yield {
            "primary_container": primary_container_descriptor,
            "secondary_container": secondary_container_descriptor,
            "ledger": ledger_descriptor,
            "snapshot": snapshot_descriptor,
            "primary_receipt": primary_receipt_descriptor,
            "secondary_receipt": secondary_receipt_descriptor,
        }
    except BaseException:
        for _key, descriptor, _path in reversed(held):
            with suppress(OSError):
                os.close(descriptor)
        raise
    close_error: BaseException | None = None
    for key, descriptor, path in reversed(held):
        try:
            _verify_current_private_directory_descriptor(
                descriptor,
                path,
                label=f"held mirror {key}",
            )
        except BaseException as exc:
            if close_error is None:
                close_error = exc
        finally:
            os.close(descriptor)
    if close_error is not None:
        raise close_error


def _mirror_live_ledger(
    ledger: SeriesLedger,
    history: HistoryValidation,
    *,
    mirror_commit_capability: _MirrorCommitCapability,
) -> JsonObject:
    binding = ledger.binding
    _validate_series_lock_held(ledger)
    if not _mirror_commit_capability_is_issued(
        mirror_commit_capability,
        ledger=ledger,
        history=history,
    ):
        raise RehearsalV22Error(
            "mirror publication lacks a one-use terminal or continuation trigger"
        )
    if not history.records or history.live_ledger_root_sha256 is None:
        raise RehearsalV22Error("mirror requires one persisted live attempt")
    ordinal = len(history.records)
    _validate_hot_second_copy_commitment(
        binding,
        history,
        allow_unmirrored_final=True,
    )
    policy = _AUDIT_POLICY.get()
    if (
        policy is None
        or policy.ledger_root != binding.ledger_root
        or policy.mirror_snapshot_root != binding.secondary_snapshot_root
        or policy.primary_receipt_root != binding.primary_receipt_root
        or policy.secondary_receipt_root != binding.secondary_receipt_root
    ):
        raise RehearsalV22Error("mirror lacks the registered held-lock write policy")
    mirror_leaves = (
        binding.secondary_snapshot_root,
        binding.primary_receipt_root,
        binding.secondary_receipt_root,
    )
    present = tuple(os.path.lexists(path) for path in mirror_leaves)
    if present not in {(False, False, False), (True, True, True)}:
        raise RehearsalV22Error("mirror leaf initialization is partial")
    initialize_roots = present == (False, False, False)
    live_root = history.live_ledger_root_sha256
    snapshot_name = _mirror_snapshot_name(ordinal, live_root)
    snapshot = binding.secondary_snapshot_root / snapshot_name
    receipt_name = _mirror_receipt_filename(ordinal, live_root)
    receipt_paths = (
        binding.primary_receipt_root / receipt_name,
        binding.secondary_receipt_root / receipt_name,
    )
    if os.path.lexists(snapshot) or any(os.path.lexists(path) for path in receipt_paths):
        raise RehearsalV22Error("mirror publication target already exists")
    staging = binding.secondary_snapshot_root / (f".staging-{snapshot_name}-{uuid.uuid4().hex}")
    if os.path.lexists(staging):
        raise RehearsalV22Error("mirror staging path already exists")
    primary_inventory: _TreeInventory | None = None
    receipt: JsonObject | None = None
    with (
        _mirror_write_sequence(
            ledger,
            history,
            mirror_commit_capability=mirror_commit_capability,
            staging=staging,
            snapshot=snapshot,
            receipt_paths=receipt_paths,
            initialize_roots=initialize_roots,
        ) as mirror_phase,
        _held_mirror_write_roots(
            binding,
            initialize_roots=initialize_roots,
            mirror_phase=mirror_phase,
        ) as roots,
    ):
        staging_descriptor: int | None = None
        staging_descriptor_path = staging
        nested_descriptors: list[tuple[int, Path]] = []
        try:
            with mirror_phase("staging"):
                os.mkdir(staging.name, 0o700, dir_fd=roots["snapshot"])
                os.fsync(roots["snapshot"])
                staging_descriptor, _staging_initial = _open_stable_read_descriptor(
                    staging,
                    label="durable mirror staging claim",
                    directory=True,
                    parent_descriptor=roots["snapshot"],
                    entry_name=staging.name,
                )
                # The descriptor-bound, uniquely named empty staging
                # directory is this ordinal's durable mirror-start claim.
                primary_before = os.fstat(roots["ledger"])
                primary_inventory = _strict_private_tree_inventory(
                    binding.ledger_root,
                    label=("series-2 primary live ledger after durable mirror claim"),
                    root_descriptor=roots["ledger"],
                    root_before=primary_before,
                )
                directory_rows = sorted(
                    (
                        row
                        for row in primary_inventory.rows
                        if row["kind"] == "directory" and row["relative_path"] != "."
                    ),
                    key=lambda row: (
                        len(PurePosixPath(cast(str, row["relative_path"])).parts),
                        cast(str, row["relative_path"]).encode("utf-8"),
                    ),
                )
                directory_descriptors: dict[str, int] = {
                    ".": staging_descriptor,
                }
                for row in directory_rows:
                    relative = cast(str, row["relative_path"])
                    relative_path = PurePosixPath(relative)
                    parent_relative = relative_path.parent.as_posix()
                    parent_descriptor = directory_descriptors.get(parent_relative)
                    if parent_descriptor is None:
                        raise RehearsalV22Error("mirror directory copy lost its held parent")
                    directory = staging.joinpath(*relative_path.parts)
                    os.mkdir(
                        relative_path.name,
                        0o700,
                        dir_fd=parent_descriptor,
                    )
                    os.fsync(parent_descriptor)
                    descriptor, _before = _open_stable_read_descriptor(
                        directory,
                        label=f"staged mirror directory {relative}",
                        directory=True,
                        parent_descriptor=parent_descriptor,
                        entry_name=relative_path.name,
                    )
                    directory_descriptors[relative] = descriptor
                    nested_descriptors.append((descriptor, directory))
                for relative, payload in primary_inventory.payloads.items():
                    relative_path = PurePosixPath(relative)
                    parent_descriptor = directory_descriptors.get(relative_path.parent.as_posix())
                    if parent_descriptor is None:
                        raise RehearsalV22Error("mirror file copy lost its held parent")
                    _write_exclusive_at(
                        parent_descriptor,
                        staging.joinpath(*relative_path.parts),
                        payload,
                        mode=0o600,
                    )
                for descriptor, directory in reversed(nested_descriptors):
                    os.fsync(descriptor)
                    _verify_current_private_directory_descriptor(
                        descriptor,
                        directory,
                        label="staged mirror nested directory",
                    )
                    os.close(descriptor)
                nested_descriptors.clear()
                os.fsync(staging_descriptor)
                os.fsync(roots["snapshot"])
            if primary_inventory is None or staging_descriptor is None:
                raise RehearsalV22Error("mirror staging omitted its durable primary inventory")
            staged_before = os.fstat(staging_descriptor)
            staged_inventory = _strict_private_tree_inventory(
                staging,
                label="series-2 staged mirror",
                root_descriptor=staging_descriptor,
                root_before=staged_before,
            )
            staged_history = validate_live_history(binding, ledger_root=staging)
            _validate_tree_inventory_identities(
                staging,
                staged_inventory,
                label="series-2 replay-bound staged mirror",
            )
            if (
                staged_inventory.rows != primary_inventory.rows
                or staged_inventory.payloads != primary_inventory.payloads
                or staged_inventory.sha256 != primary_inventory.sha256
                or staged_history.history_root_sha256 != history.history_root_sha256
                or staged_history.live_ledger_root_sha256 != live_root
            ):
                raise RehearsalV22Error("staged mirror differs from the primary ledger")
            primary_inventory = replace(primary_inventory, payloads={})
            staged_inventory = replace(staged_inventory, payloads={})
            with mirror_phase("publish"):
                _publish_mirror_snapshot_exclusive(
                    staging,
                    snapshot,
                    parent_descriptor=roots["snapshot"],
                )
                staging_descriptor_path = snapshot
            _validate_tree_inventory_identities(
                snapshot,
                label="series-2 published mirror",
                inventory=staged_inventory,
                permit_root_metadata_refresh=True,
            )
            published_history = validate_live_history(
                binding,
                ledger_root=snapshot,
            )
            _validate_tree_inventory_identities(
                snapshot,
                label="series-2 replay-bound published mirror",
                inventory=staged_inventory,
                permit_root_metadata_refresh=True,
            )
            if (
                published_history.history_root_sha256 != history.history_root_sha256
                or published_history.live_ledger_root_sha256 != live_root
            ):
                raise RehearsalV22Error("published mirror differs from the primary ledger")
            last = history.records[-1]
            receipt = {
                "schema_version": MIRROR_RECEIPT_SCHEMA,
                "series_token_sha256": binding.series_token_sha256,
                "ordinal": ordinal,
                "attempt_outcome": last.outcome,
                "attempt_sealed": last.outcome != "INCOMPLETE_UNTERMINALIZED",
                "primary_ledger_root": binding.ledger_root.as_posix(),
                "secondary_snapshot_root": snapshot.as_posix(),
                "history_root_sha256": history.history_root_sha256,
                "live_ledger_root_sha256": live_root,
                "file_count": primary_inventory.file_count,
                "total_bytes": primary_inventory.total_bytes,
                "primary_inventory_sha256": primary_inventory.sha256,
                "secondary_inventory_sha256": staged_inventory.sha256,
                "second_copy_verified": True,
                "verified_at_utc": FIXED_WALL_CLOCK_TEXT,
            }
            receipt_payload = _canonical_json_bytes(receipt)
            with mirror_phase("receipt"):
                _write_exclusive_at(
                    roots["primary_receipt"],
                    receipt_paths[0],
                    receipt_payload,
                    mode=0o600,
                )
                _write_exclusive_at(
                    roots["secondary_receipt"],
                    receipt_paths[1],
                    receipt_payload,
                    mode=0o600,
                )
        finally:
            for descriptor, _directory in reversed(nested_descriptors):
                with suppress(OSError):
                    os.close(descriptor)
            if staging_descriptor is not None:
                try:
                    _verify_current_private_directory_descriptor(
                        staging_descriptor,
                        staging_descriptor_path,
                        label="mirror staging or published snapshot",
                    )
                finally:
                    os.close(staging_descriptor)
    if receipt is None:
        raise RehearsalV22Error("mirror publication produced no verification receipt")
    receipt_payload = _canonical_json_bytes(receipt)
    if tuple(_regular_bytes(path, "mirror verification receipt") for path in receipt_paths) != (
        receipt_payload,
        receipt_payload,
    ):
        raise RehearsalV22Error("paired mirror receipts differ after durable publication")
    _validate_hot_second_copy_commitment(binding, history)
    return receipt


def _build_mirror_commit_state() -> tuple[Any, Any, Any, Any]:
    nonce = object()
    registry: tuple[_MirrorCommitCapability, ...] = ()
    consumed: tuple[tuple[Path, int, str, str], ...] = ()

    def capability_is_issued(
        capability: _MirrorCommitCapability,
        *,
        ledger: SeriesLedger,
        history: HistoryValidation,
    ) -> bool:
        return bool(
            isinstance(capability, _MirrorCommitCapability)
            and capability._nonce is nonce
            and capability.ledger_id == id(ledger)
            and capability.ordinal == len(history.records)
            and capability.history_root_sha256 == history.history_root_sha256
            and capability.reason in {"TERMINAL_SEAL", "CONTINUATION_FREEZE"}
            and any(record is capability for record in registry)
        )

    def consume_capability(
        capability: _MirrorCommitCapability,
        *,
        ledger: SeriesLedger,
        history: HistoryValidation,
    ) -> None:
        nonlocal registry
        if not capability_is_issued(capability, ledger=ledger, history=history):
            raise RehearsalV22Error("mirror sequence lacks an issued commit trigger")
        registry = tuple(record for record in registry if record is not capability)

    def validate_terminal_trigger(
        ledger: SeriesLedger,
        history: HistoryValidation,
        lease: AttemptLease | None,
    ) -> None:
        policy = _AUDIT_POLICY.get()
        if lease is None:
            raise RehearsalV22Error("terminal mirror trigger has no active lease")
        expected_terminal = lease.attempt_root / "terminal.json"
        execution_action = getattr(
            ledger.execution_context,
            "action_authorization",
            None,
        )
        if (
            ledger.active_lease is not lease
            or execution_action is not lease.action_authorization
            or not lease.terminal_written
            or not lease.frozen
            or policy is None
            or not _audit_policy_is_issued(policy)
            or policy.ledger_write_phase != "frozen"
            or policy.ledger_root != ledger.binding.ledger_root
            or policy.active_attempt_root != lease.attempt_root
            or len(history.records) != lease.ordinal
            or history.binding != ledger.binding
            or history != validate_live_history(ledger.binding)
            or history.records[-1].terminal_path != expected_terminal
            or history.records[-1].owner_action_time_authorization
            != lease.action_authorization.authority_ref(ledger.binding.project_root)
            or not expected_terminal.is_file()
            or expected_terminal.is_symlink()
        ):
            raise RehearsalV22Error(
                "terminal mirror trigger lacks the issued durable terminal state"
            )
        if (
            _validate_execution_capability(
                ledger.execution_context,
                project_root=ledger.binding.project_root,
            )
            != ledger.binding
        ):
            raise RehearsalV22Error("terminal mirror trigger capability drifted")
        _validate_hot_second_copy_commitment(
            ledger.binding,
            history,
            allow_unmirrored_final=True,
        )

    def validate_continuation_trigger(
        ledger: SeriesLedger,
        history: HistoryValidation,
        action_authorization: ActionAuthorization | None,
    ) -> None:
        authorization = action_authorization
        policy = _AUDIT_POLICY.get()
        execution_action = getattr(
            ledger.execution_context,
            "action_authorization",
            None,
        )
        if not isinstance(authorization, ActionAuthorization):
            raise RehearsalV22Error("continuation mirror trigger lacks next action authorization")
        authorization_exact: ActionAuthorization = authorization
        if (
            execution_action is not authorization_exact
            or ledger.active_lease is not None
            or policy is None
            or not _audit_policy_is_issued(policy)
            or policy.ledger_write_phase != "frozen"
            or policy.ledger_root != ledger.binding.ledger_root
            or policy.active_attempt_root is not None
            or not history.records
            or history.binding != ledger.binding
            or history != validate_live_history(ledger.binding)
            or history.records[-1].outcome != "INCOMPLETE_UNTERMINALIZED"
            or history.records[-1].terminal_path is not None
        ):
            raise RehearsalV22Error(
                "continuation mirror trigger is not one prior crash-incomplete history"
            )
        if (
            _validate_execution_capability(
                ledger.execution_context,
                project_root=ledger.binding.project_root,
            )
            != ledger.binding
        ):
            raise RehearsalV22Error("continuation mirror trigger capability drifted")
        expected_ordinal = len(history.records) + 1
        revalidated_action = _validate_action_authorization(
            ledger.binding,
            authorization_exact.authority_ref(ledger.binding.project_root),
            expected_ordinal=expected_ordinal,
            expected_previous_history_root_sha256=history.history_root_sha256,
            require_current_process=True,
        )
        if revalidated_action != authorization_exact:
            raise RehearsalV22Error("continuation mirror trigger action authorization drifted")
        _validate_next_series_2_epoch(
            history,
            authorization_exact.implementation_epoch,
        )
        _validate_hot_second_copy_commitment(
            ledger.binding,
            history,
            allow_unmirrored_final=True,
        )

    def publish(
        ledger: SeriesLedger,
        history: HistoryValidation,
        *,
        reason: Literal["TERMINAL_SEAL", "CONTINUATION_FREEZE"],
        lease: AttemptLease | None = None,
        action_authorization: ActionAuthorization | None = None,
    ) -> JsonObject:
        nonlocal consumed, registry
        _validate_series_lock_held(ledger)
        if reason == "TERMINAL_SEAL":
            if action_authorization is not None:
                raise RehearsalV22Error("terminal mirror trigger received a next action")
            validate_terminal_trigger(ledger, history, lease)
        else:
            if lease is not None:
                raise RehearsalV22Error("continuation mirror trigger received an active lease")
            validate_continuation_trigger(ledger, history, action_authorization)
        identity = (
            ledger.binding.ledger_root,
            len(history.records),
            history.history_root_sha256,
            reason,
        )
        if identity in consumed:
            raise RehearsalV22Error("mirror trigger was already consumed in this process")
        capability = _MirrorCommitCapability(
            _nonce=nonce,
            ledger_id=id(ledger),
            ordinal=len(history.records),
            history_root_sha256=history.history_root_sha256,
            reason=reason,
        )
        consumed = (*consumed, identity)
        registry = (*registry, capability)
        try:
            return _mirror_live_ledger(
                ledger,
                history,
                mirror_commit_capability=capability,
            )
        finally:
            registry = tuple(record for record in registry if record is not capability)

    def mirror_after_terminal_seal(
        lease: AttemptLease,
        history: HistoryValidation,
    ) -> JsonObject:
        ledger = lease.ledger
        return publish(
            ledger,
            history,
            reason="TERMINAL_SEAL",
            lease=lease,
        )

    def mirror_before_next_allocation(
        ledger: SeriesLedger,
        history: HistoryValidation,
        action_authorization: ActionAuthorization,
    ) -> JsonObject:
        return publish(
            ledger,
            history,
            reason="CONTINUATION_FREEZE",
            action_authorization=action_authorization,
        )

    return (
        capability_is_issued,
        consume_capability,
        mirror_after_terminal_seal,
        mirror_before_next_allocation,
    )


(
    _mirror_commit_capability_is_issued,
    _consume_mirror_commit_capability,
    _mirror_after_terminal_seal,
    _mirror_before_next_allocation,
) = _build_mirror_commit_state()


def _final_mirror_targets(
    binding: ExecutionBinding,
    history: HistoryValidation,
) -> tuple[Path, Path, Path]:
    if not history.records or history.live_ledger_root_sha256 is None:
        raise RehearsalV22Error("final mirror target requires persisted history")
    ordinal = len(history.records)
    live_root = history.live_ledger_root_sha256
    receipt_name = _mirror_receipt_filename(ordinal, live_root)
    return (
        binding.secondary_snapshot_root / _mirror_snapshot_name(ordinal, live_root),
        binding.primary_receipt_root / receipt_name,
        binding.secondary_receipt_root / receipt_name,
    )


def _validate_continuation_mirror_state(
    binding: ExecutionBinding,
    history: HistoryValidation,
    *,
    permit_unmirrored_final_incomplete: bool,
) -> bool:
    if not history.records:
        _validate_hot_second_copy_commitment(binding, history)
        return True
    final = history.records[-1]
    targets = _final_mirror_targets(binding, history)
    if (
        permit_unmirrored_final_incomplete
        and final.outcome == "INCOMPLETE_UNTERMINALIZED"
        and not any(os.path.lexists(path) for path in targets)
    ):
        _validate_hot_second_copy_commitment(
            binding,
            history,
            allow_unmirrored_final=True,
        )
        return False
    _validate_hot_second_copy_commitment(binding, history)
    return True


def _validate_authority_ref_shape(value: object, label: str) -> AuthorityReference:
    return _authority_from_json(value, label)


def _live_ledger_files_unheld(
    binding: ExecutionBinding,
    *,
    ledger_root: Path | None = None,
) -> tuple[tuple[str, bytes], ...]:
    root = binding.ledger_root if ledger_root is None else ledger_root
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
        payload = _regular_bytes(path, f"live ledger file {relative}")
        if _stat_identity(path.lstat()) != _stat_identity(metadata):
            raise RehearsalV22Error(f"live ledger file changed during read: {relative}")
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


def _live_ledger_files(
    binding: ExecutionBinding,
    *,
    ledger_root: Path | None = None,
) -> tuple[tuple[str, bytes], ...]:
    root = binding.ledger_root if ledger_root is None else ledger_root
    with _held_directory_identity(root, "live ledger root"):
        return _live_ledger_files_unheld(binding, ledger_root=root)


def _live_ledger_root(
    binding: ExecutionBinding,
    *,
    ledger_root: Path | None = None,
) -> tuple[str, tuple[str, ...]]:
    files = _live_ledger_files(binding, ledger_root=ledger_root)
    leaves = [
        hashlib.sha256(
            LEDGER_LEAF_PREFIX + relative.encode("utf-8") + b"\0" + hashlib.sha256(payload).digest()
        ).digest()
        for relative, payload in files
    ]
    root = _binary_merkle_root(leaves, node_prefix=MERKLE_NODE_PREFIX, empty_root=None)
    return root, tuple(relative for relative, _payload in files)


def _validate_series_document(
    binding: ExecutionBinding,
    *,
    ledger_root: Path | None = None,
) -> None:
    root = binding.ledger_root if ledger_root is None else ledger_root
    document, payload, _digest = _canonical_object_file(
        root / "series.json",
        label="series.json",
        exact_fields=SERIES_FIELDS,
    )
    if (
        document.get("schema_version") != SERIES_2_SERIES_SCHEMA_VERSION
        or document.get("series_id") != REHEARSAL_ID
        or document.get("series_token_sha256") != binding.series_token_sha256
        or document.get("policy") != SERIES_POLICY
        or document.get("ledger_root") != binding.ledger_root.as_posix()
        or document.get("attempt_limit")
        != "unbounded_until_first_validated_success_or_owner_abandonment"
        or document.get("per_attempt_action_time_owner_authorization_required") is not True
        or document.get("automatic_retry_count") != 0
        or document.get("first_validated_candidate_closes_series") is not True
        or document.get("implementation_epoch_origin") != SERIES_2_EPOCH_ORIGIN
        or document.get("preregistration")
        != {
            "path": SERIES_2_PREREGISTRATION_RELATIVE.as_posix(),
            "sha256": SERIES_2_PREREGISTRATION_SHA256,
            "creating_commit": SERIES_2_PREREGISTRATION_COMMIT,
            "unique_a_history_verified": True,
        }
        or document.get("bundle_schema")
        != {
            "path": SERIES_2_BUNDLE_SCHEMA_RELATIVE.as_posix(),
            "sha256": SERIES_2_BUNDLE_SCHEMA_SHA256,
        }
        or document.get("release_schema")
        != {
            "path": SERIES_2_RELEASE_SCHEMA_RELATIVE.as_posix(),
            "sha256": SERIES_2_RELEASE_SCHEMA_SHA256,
        }
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
    historical_execution_head: str | None = None,
) -> ActionAuthorization:
    """Actively revalidate one action receipt before ledger allocation or replay."""

    root = binding.project_root
    if require_current_process and historical_execution_head is not None:
        raise RehearsalV22Error("a current action process cannot use a historical execution head")
    execution_head = (
        _git_commit(root, historical_execution_head, "historical action execution head")
        if historical_execution_head is not None
        else _current_execution_head(root)
    )
    payload = validate_unique_a_authority(
        root,
        authority,
        execution_head=execution_head,
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


def _validate_recovery_timestamp_pair(
    utc_value: object,
    shanghai_value: object,
    label: str,
) -> tuple[str, str]:
    if (
        not isinstance(utc_value, str)
        or RFC3339_UTC_SECONDS.fullmatch(utc_value) is None
        or not isinstance(shanghai_value, str)
        or RFC3339_SHANGHAI_SECONDS.fullmatch(shanghai_value) is None
    ):
        raise RehearsalV22Error(f"{label} timestamps are invalid")
    try:
        if datetime.fromisoformat(utc_value.replace("Z", "+00:00")) != datetime.fromisoformat(
            shanghai_value
        ):
            raise RehearsalV22Error(f"{label} timestamps disagree")
    except ValueError as exc:
        raise RehearsalV22Error(f"{label} timestamp is invalid") from exc
    return utc_value, shanghai_value


def _validate_embedded_storage_directory_evidence(
    value: object,
    *,
    expected_path: Path,
    label: str,
) -> JsonObject:
    """Validate recorded directory identity without observing the directory yet."""

    evidence = _exact_contract_object(
        value,
        frozenset(
            {
                "path",
                "owner_uid",
                "device",
                "inode",
                "mode_octal",
                "non_symlink",
                "canonical_unaliased",
            }
        ),
        label,
    )
    if (
        evidence.get("path") != expected_path.as_posix()
        or type(evidence.get("owner_uid")) is not int
        or evidence.get("owner_uid") != os.getuid()
        or type(evidence.get("device")) is not int
        or cast(int, evidence.get("device")) < 0
        or type(evidence.get("inode")) is not int
        or cast(int, evidence.get("inode")) <= 0
        or evidence.get("mode_octal") != "0700"
        or evidence.get("non_symlink") is not True
        or evidence.get("canonical_unaliased") is not True
    ):
        raise RehearsalV22Error(f"{label} identity semantics drifted")
    return evidence


def _validate_epoch_8_embedded_preflight_semantics(
    binding: ExecutionBinding,
    authorization: BundleRecoveryAuthorization,
    stdout_document: Mapping[str, Any],
    *,
    execution_landing: AuthorityReference,
) -> tuple[JsonObject, JsonObject | None, JsonObject]:
    """Validate the complete Q-embedded preflight before live storage is read."""

    root = binding.project_root.absolute()
    expected_mode = (
        "REGISTERED_OFFICIAL"
        if binding.mode == "REGISTERED_OFFICIAL"
        else "NONREGISTERED_READ_ONLY_TEST"
    )
    if (
        stdout_document.get("execution_head") != execution_landing.creating_commit
        or stdout_document.get("mode") != expected_mode
        or (
            binding.mode == "REGISTERED_OFFICIAL"
            and root != REGISTERED_PROJECT_ROOT
        )
        or (
            binding.mode == "DISPOSABLE_FULL_SHAPE_TEST"
            and root == REGISTERED_PROJECT_ROOT
        )
    ):
        raise RehearsalV22Error("Q preflight execution-head or root classification drifted")

    implementation_commit = cast(
        str,
        authorization.execution_epoch.get("implementation_commit"),
    )
    registered_surface = _array(
        stdout_document.get("registered_surface"),
        "bundle recovery Q registered surface",
    )
    expected_surface = [
        {
            "path": relative.as_posix(),
            "sha256": _sha256(
                _git_blob(root, implementation_commit, relative.as_posix())
            ),
        }
        for relative in IMPLEMENTATION_SURFACE
    ]
    if registered_surface != expected_surface or any(
        set(_object(row, "bundle recovery Q registered surface row"))
        != {"path", "sha256"}
        for row in registered_surface
    ):
        raise RehearsalV22Error("Q preflight registered surface drifted")

    sealed = _object(authorization.sealed_series, "bundle recovery sealed series")
    sealed_mirror = _exact_contract_object(
        sealed.get("sealed_mirror"),
        frozenset(
            {
                "snapshot_count",
                "receipt_count",
                "latest_ordinal",
                "latest_snapshot_path",
                "primary_receipt_path",
                "secondary_receipt_path",
                "receipt_sha256",
                "receipt_bytes",
                "inventory_sha256",
                "file_count",
                "total_bytes",
                "paired_receipts_byte_identical",
            }
        ),
        "bundle recovery sealed mirror",
    )
    series_storage = _exact_contract_object(
        stdout_document.get("series_2_registered_storage"),
        frozenset(
            {
                "primary_container",
                "secondary_container",
                "containers_non_overlapping",
                "storage_state",
                "registered_leaf_state",
                "mirrored_history",
                "bundle_destination_absent",
                "lost_series_ledger_absent",
                "retired_v2_1_claim_absent",
                "paths_created",
            }
        ),
        "bundle recovery Q series-2 registered storage",
    )
    _validate_embedded_storage_directory_evidence(
        series_storage.get("primary_container"),
        expected_path=binding.primary_series_container,
        label="bundle recovery Q primary series container",
    )
    _validate_embedded_storage_directory_evidence(
        series_storage.get("secondary_container"),
        expected_path=binding.secondary_series_container,
        label="bundle recovery Q secondary series container",
    )
    leaf_state = _exact_contract_object(
        series_storage.get("registered_leaf_state"),
        frozenset(
            {
                "primary_ledger",
                "primary_receipts",
                "secondary_snapshots",
                "secondary_receipts",
            }
        ),
        "bundle recovery Q registered leaf state",
    )
    mirrored_history = _exact_contract_object(
        series_storage.get("mirrored_history"),
        frozenset(
            {
                "attempt_count",
                "history_root_sha256",
                "live_ledger_root_sha256",
                "receipt_count",
                "series_closed",
            }
        ),
        "bundle recovery Q mirrored history",
    )
    if (
        series_storage.get("containers_non_overlapping") is not True
        or series_storage.get("storage_state") != "EXISTING_FULLY_MIRRORED"
        or any(value != "PRESENT_VERIFIED" for value in leaf_state.values())
        or mirrored_history
        != {
            "attempt_count": sealed.get("started_count"),
            "history_root_sha256": sealed.get("history_root_sha256"),
            "live_ledger_root_sha256": sealed.get("live_ledger_root_sha256"),
            "receipt_count": sealed_mirror.get("receipt_count"),
            "series_closed": sealed.get("series_closed"),
        }
        or series_storage.get("bundle_destination_absent") is not True
        or series_storage.get("lost_series_ledger_absent") is not True
        or series_storage.get("retired_v2_1_claim_absent") is not True
        or series_storage.get("paths_created") != 0
    ):
        raise RehearsalV22Error("Q preflight series-2 storage semantics drifted")

    preflight_storage_value = stdout_document.get("registered_recovery_storage")
    registered_preflight_storage: JsonObject | None
    if binding.mode == "REGISTERED_OFFICIAL":
        registered_preflight_storage = _exact_contract_object(
            preflight_storage_value,
            frozenset(
                {
                    "primary_container",
                    "secondary_container",
                    "both_owner_provisioned_empty",
                    "leaf_paths_created",
                }
            ),
            "bundle recovery Q registered recovery storage",
        )
        destination_storage = _exact_contract_object(
            _object(authorization.destination, "bundle recovery destination").get(
                "recovery_storage"
            ),
            RECOVERY_STORAGE_FIELDS,
            "bundle recovery R storage contract",
        )
        _validate_embedded_storage_directory_evidence(
            registered_preflight_storage.get("primary_container"),
            expected_path=Path(
                cast(str, destination_storage.get("primary_recovery_container"))
            ),
            label="bundle recovery Q primary recovery container",
        )
        _validate_embedded_storage_directory_evidence(
            registered_preflight_storage.get("secondary_container"),
            expected_path=Path(
                cast(str, destination_storage.get("secondary_recovery_container"))
            ),
            label="bundle recovery Q secondary recovery container",
        )
        if (
            registered_preflight_storage.get("both_owner_provisioned_empty") is not True
            or registered_preflight_storage.get("leaf_paths_created") != 0
        ):
            raise RehearsalV22Error("Q preflight recovery storage is not exactly empty")
    else:
        if preflight_storage_value is not None:
            raise RehearsalV22Error("synthetic Q preflight exposed registered recovery storage")
        registered_preflight_storage = None

    sealed_inputs = _exact_contract_object(
        stdout_document.get("sealed_recovery_inputs"),
        frozenset(
            {
                "series_closed",
                "record_count",
                "selected_attempt_ordinal",
                "selected_implementation_epoch",
                "selected_implementation_commit",
                "history_root_sha256",
                "live_ledger_root_sha256",
                "mirror_receipt_count",
                "sealed_input_fingerprints",
                "work_counters",
                "ledger_and_mirror_read_only",
            }
        ),
        "bundle recovery Q sealed recovery inputs",
    )
    fingerprints = _exact_contract_object(
        sealed_inputs.get("sealed_input_fingerprints"),
        frozenset(
            {
                "active_ledger",
                "primary_seal_receipt",
                "secondary_seal_receipt",
                "through_ordinal_2_snapshot",
            }
        ),
        "bundle recovery Q sealed input fingerprints",
    )
    counters = _exact_contract_object(
        sealed_inputs.get("work_counters"),
        frozenset(RECOVERY_WORK_COUNTER_FIELDS),
        "bundle recovery Q sealed input work counters",
    )
    typed_counters: dict[str, int] = {}
    for name in RECOVERY_WORK_COUNTER_FIELDS:
        value = counters.get(name)
        if type(value) is not int or value < 0:
            raise RehearsalV22Error("Q preflight work counters are malformed")
        typed_counters[name] = value
    _assert_recovery_work_bound(typed_counters)
    if (
        any(not _lower_hex(value, 64) for value in fingerprints.values())
        or typed_counters["git_objects_read"] != 0
        or typed_counters["bundle_bytes_copied"] != 0
        or sealed_inputs.get("series_closed") != sealed.get("series_closed")
        or sealed_inputs.get("record_count") != sealed.get("started_count")
        or sealed_inputs.get("selected_attempt_ordinal")
        != sealed.get("selected_attempt_ordinal")
        or sealed_inputs.get("selected_implementation_epoch")
        != sealed.get("selected_implementation_epoch")
        or sealed_inputs.get("selected_implementation_commit")
        != sealed.get("selected_implementation_commit")
        or sealed_inputs.get("history_root_sha256") != sealed.get("history_root_sha256")
        or sealed_inputs.get("live_ledger_root_sha256")
        != sealed.get("live_ledger_root_sha256")
        or sealed_inputs.get("mirror_receipt_count") != sealed_mirror.get("receipt_count")
        or sealed_inputs.get("ledger_and_mirror_read_only") is not True
    ):
        raise RehearsalV22Error("Q preflight sealed recovery inputs drifted")

    effects = _exact_contract_object(
        stdout_document.get("effect_summary"),
        frozenset(
            {
                "action_receipt_required",
                "action_receipts_read",
                "project_and_gate_state_writes_permitted",
                "temporary_authorities_created",
                "ledgers_created",
                "storage_containers_created",
                "mirror_leaves_created",
                "attempts_allocated",
                "pipeline_starts",
                "automatic_retries",
                "heldout_evaluation_attempts_consumed",
                "shallow_alternate_partial_and_included_git_config_rejected",
                "stdout_persistence_controlled_by_caller",
            }
        ),
        "bundle recovery Q preflight effects",
    )
    if effects != {
        "action_receipt_required": False,
        "action_receipts_read": 0,
        "project_and_gate_state_writes_permitted": False,
        "temporary_authorities_created": 0,
        "ledgers_created": 0,
        "storage_containers_created": 0,
        "mirror_leaves_created": 0,
        "attempts_allocated": 0,
        "pipeline_starts": 0,
        "automatic_retries": 0,
        "heldout_evaluation_attempts_consumed": 0,
        "shallow_alternate_partial_and_included_git_config_rejected": True,
        "stdout_persistence_controlled_by_caller": True,
    }:
        raise RehearsalV22Error("Q preflight effects are not exactly zero")
    return series_storage, registered_preflight_storage, sealed_inputs


def _validate_bundle_recovery_authorization(
    binding: ExecutionBinding,
    authority: AuthorityReference,
    *,
    require_current_process: bool,
) -> BundleRecoveryAuthorization:
    """Validate R identity and scope before inspecting any recovery storage."""

    root = binding.project_root.absolute()
    execution_head = _current_execution_head(root)
    payload = validate_unique_a_authority(
        root,
        authority,
        execution_head=execution_head,
    )
    document = _object(
        strict_json_loads(payload, source="bundle recovery authorization"),
        "bundle recovery authorization",
    )
    if (
        set(document) != RECOVERY_AUTHORIZATION_FIELDS
        or _canonical_json_bytes(document) != payload
        or document.get("schema_version") != EPOCH_8_RECOVERY_AUTHORIZATION_SCHEMA
        or document.get("verdict")
        != "APPROVE_EXACTLY_ONE_SEALED_BUNDLE_RECOVERY_ZERO_PIPELINE_START_ZERO_AUTOMATIC_RETRY"
    ):
        raise RehearsalV22Error("bundle recovery authorization is not exact canonical R")
    _created_utc, created_shanghai = _validate_recovery_timestamp_pair(
        document.get("created_at_utc"),
        document.get("created_at_shanghai"),
        "bundle recovery authorization",
    )
    date_token = created_shanghai[:10].replace("-", "")
    expected_relative = (
        "docs/phase4/reports/P4.2a-v2-2-series2-through-ordinal-000002-"
        f"bundle-recovery-authorization-{date_token}.json"
    )
    expected_path = root.joinpath(*PurePosixPath(expected_relative).parts)
    owner = _exact_contract_object(
        document.get("owner"),
        frozenset({"identity", "approved", "scope"}),
        "bundle recovery owner",
    )
    sealed = _exact_contract_object(
        document.get("sealed_series"),
        RECOVERY_SEALED_SERIES_FIELDS,
        "bundle recovery sealed series",
    )
    execution = _exact_contract_object(
        document.get("execution_epoch"),
        RECOVERY_EXECUTION_EPOCH_FIELDS,
        "bundle recovery execution epoch",
    )
    destination = _exact_contract_object(
        document.get("destination"),
        RECOVERY_DESTINATION_FIELDS,
        "bundle recovery destination",
    )
    storage = _exact_contract_object(
        destination.get("recovery_storage"),
        RECOVERY_STORAGE_FIELDS,
        "bundle recovery storage contract",
    )
    interpreter = _exact_contract_object(
        document.get("interpreter"),
        RECOVERY_INTERPRETER_FIELDS,
        "bundle recovery interpreter",
    )
    effects = _exact_contract_object(
        document.get("effect_authorization"),
        RECOVERY_EFFECT_FIELDS,
        "bundle recovery effects",
    )
    locks = _exact_contract_object(
        document.get("locks"),
        RECOVERY_LOCK_FIELDS,
        "bundle recovery locks",
    )
    selected_files = _exact_contract_object(
        sealed.get("selected_files"),
        RECOVERY_SELECTED_FILES_FIELDS,
        "bundle recovery selected files",
    )
    for name in ("started", "candidate", "terminal"):
        _exact_contract_object(
            selected_files.get(name),
            RECOVERY_SELECTED_FILE_FIELDS,
            f"bundle recovery selected {name}",
        )
    sealed_mirror = _exact_contract_object(
        sealed.get("sealed_mirror"),
        frozenset(
            {
                "snapshot_count",
                "receipt_count",
                "latest_ordinal",
                "latest_snapshot_path",
                "primary_receipt_path",
                "secondary_receipt_path",
                "receipt_sha256",
                "receipt_bytes",
                "inventory_sha256",
                "file_count",
                "total_bytes",
                "paired_receipts_byte_identical",
            }
        ),
        "bundle recovery sealed mirror",
    )
    census_reference = _exact_contract_object(
        execution.get("real_lineage_census"),
        RECOVERY_CENSUS_REFERENCE_FIELDS,
        "bundle recovery census reference",
    )
    exact_argv_raw = document.get("exact_argv")
    exact_environment_raw = document.get("exact_environment")
    if (
        authority.path != expected_relative
        or expected_path != binding.action_authorization_path
        or not isinstance(document.get("authorization_id"), str)
        or not cast(str, document["authorization_id"])
        or owner
        != {
            "identity": "ouyang",
            "approved": True,
            "scope": "one_disclosed_sealed_bundle_recovery_only",
        }
        or not isinstance(exact_argv_raw, list)
        or any(not isinstance(value, str) or not value for value in exact_argv_raw)
        or len(exact_argv_raw) != 10
        or exact_argv_raw[:7]
        != [
            FIXED_PYTHON_LAUNCHER.as_posix(),
            "-S",
            "-P",
            "-B",
            binding.shim_path.as_posix(),
            "--recover-sealed-bundle",
            "--bundle-recovery-authorization",
        ]
        or exact_argv_raw[7] != expected_path.as_posix()
        or exact_argv_raw[8] != "--bundle-recovery-owner-confirmation-binding"
        or not isinstance(exact_argv_raw[9], str)
        or not re.fullmatch(
            r".*/docs/phase4/reports/P4\.2a-v2-2-series2-through-ordinal-000002-"
            r"bundle-recovery-owner-confirmation-binding-[0-9]{8}\.json",
            exact_argv_raw[9],
        )
        or not isinstance(exact_environment_raw, dict)
        or exact_environment_raw != EXACT_ENVIRONMENT
        or document.get("command_sha256") != _command_sha256(cast(list[str], exact_argv_raw))
        or document.get("environment_sha256")
        != _environment_sha256(cast(dict[str, str], exact_environment_raw))
        or type(document.get("authorized_bundle_recovery_starts")) is not int
        or document.get("authorized_bundle_recovery_starts") != 1
        or type(document.get("authorized_pipeline_starts")) is not int
        or document.get("authorized_pipeline_starts") != 0
        or type(document.get("automatic_retry_count")) is not int
        or document.get("automatic_retry_count") != 0
    ):
        raise RehearsalV22Error("bundle recovery R identity or exact argv drifted")
    expected_selected_files = {
        "started": {
            "relative_path": "attempts/000002/started.json",
            "sha256": EPOCH_8_SELECTED_STARTED_SHA256,
            "bytes": EPOCH_8_SELECTED_STARTED_BYTES,
        },
        "candidate": {
            "relative_path": "attempts/000002/candidate.json",
            "sha256": EPOCH_8_SELECTED_CANDIDATE_SHA256,
            "bytes": EPOCH_8_SELECTED_CANDIDATE_BYTES,
        },
        "terminal": {
            "relative_path": "attempts/000002/terminal.json",
            "sha256": EPOCH_8_SELECTED_TERMINAL_SHA256,
            "bytes": EPOCH_8_SELECTED_TERMINAL_BYTES,
        },
    }
    expected_sealed_scalars: dict[str, object] = {
        "series_id": REHEARSAL_ID,
        "series_token_sha256": binding.series_token_sha256,
        "ledger_root": binding.ledger_root.as_posix(),
        "series_closed": True,
        "started_count": 2,
        "failed_count": 1,
        "incomplete_count": 0,
        "validated_candidate_count": 1,
        "selected_attempt_ordinal": 2,
        "selected_implementation_epoch": EPOCH_8_HISTORICAL_SELECTED_EPOCH,
        "selected_terminal_outcome": "CANDIDATE_VALIDATED_AND_SELECTED",
        "selected_reached_stage": "bundle_candidate_validated",
        "automatic_retry_count": 0,
    }
    if binding.mode == "REGISTERED_OFFICIAL":
        expected_sealed_scalars.update(
            {
                "history_root_sha256": EPOCH_8_SEALED_HISTORY_ROOT_SHA256,
                "live_ledger_root_sha256": EPOCH_8_SEALED_LIVE_LEDGER_ROOT_SHA256,
                "selected_implementation_commit": EPOCH_8_HISTORICAL_SELECTED_COMMIT,
                "selected_control_merkle_root_sha256": EPOCH_8_HISTORICAL_CONTROL_ROOT_SHA256,
                "selected_evidence_tree_root_sha256": EPOCH_8_SELECTED_EVIDENCE_ROOT_SHA256,
                "selected_candidate_content_root_sha256": (
                    EPOCH_8_SELECTED_CANDIDATE_CONTENT_ROOT_SHA256
                ),
                "selected_run_a_root_sha256": EPOCH_8_SELECTED_RUN_ROOT_SHA256,
                "selected_run_b_root_sha256": EPOCH_8_SELECTED_RUN_ROOT_SHA256,
            }
        )
    if (
        any(sealed.get(key) != value for key, value in expected_sealed_scalars.items())
        or (binding.mode == "REGISTERED_OFFICIAL" and selected_files != expected_selected_files)
        or any(
            not _lower_hex(sealed.get(key), 64)
            for key in (
                "history_root_sha256",
                "live_ledger_root_sha256",
                "selected_control_merkle_root_sha256",
                "selected_evidence_tree_root_sha256",
                "selected_candidate_content_root_sha256",
                "selected_run_a_root_sha256",
                "selected_run_b_root_sha256",
            )
        )
        or not _lower_hex(sealed.get("selected_implementation_commit"), 40)
        or sealed_mirror.get("snapshot_count") != 2
        or sealed_mirror.get("receipt_count") != 2
        or sealed_mirror.get("latest_ordinal") != 2
        or not _lower_hex(sealed_mirror.get("receipt_sha256"), 64)
        or type(sealed_mirror.get("receipt_bytes")) is not int
        or not _lower_hex(sealed_mirror.get("inventory_sha256"), 64)
        or type(sealed_mirror.get("file_count")) is not int
        or type(sealed_mirror.get("total_bytes")) is not int
        or sealed_mirror.get("paired_receipts_byte_identical") is not True
        or (
            binding.mode == "REGISTERED_OFFICIAL"
            and (
                sealed_mirror.get("receipt_sha256") != EPOCH_8_SEALED_MIRROR_RECEIPT_SHA256
                or sealed_mirror.get("receipt_bytes") != EPOCH_8_SEALED_MIRROR_RECEIPT_BYTES
                or sealed_mirror.get("inventory_sha256")
                != "16689ab8e8fb3933d5c912adda098ec0b1b83866b0ce0f32f07b17fdb234ebdb"
                or sealed_mirror.get("file_count") != 48
                or sealed_mirror.get("total_bytes") != 50_237_638
            )
        )
    ):
        raise RehearsalV22Error("bundle recovery sealed-series constants drifted")
    expected_effects = {
        "attempt_allocation": False,
        "candidate_or_terminal_rewrite": False,
        "destination_publish_once": True,
        "git_metadata_or_tracked_worktree_write": False,
        "git_object_read": True,
        "heldout_materialization_inference_or_evaluation": False,
        "ledger_read": True,
        "ledger_write": False,
        "model_access": False,
        "network_access": False,
        "paired_bundle_receipts_create_once": True,
        "pipeline_execution": False,
        "recovery_claim_create_once": True,
        "sealed_ledger_mirror_read": True,
        "sealed_ledger_mirror_write": False,
        "secondary_bundle_mirror_publish_once": True,
        "sqlite_or_production_database_access": False,
        "destination_stage_create_once": True,
        "secondary_snapshot_stage_create_once": True,
    }
    if (
        effects != expected_effects
        or any(type(value) is not bool for value in effects.values())
        or any(value is not False for value in locks.values())
        or destination.get("absolute_path") != binding.destination.as_posix()
        or destination.get("required_absent_before_start") is not True
        or destination.get("publication_mode") != "ATOMIC_DIRECTORY_NO_REPLACE"
        or destination.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION
        or destination.get("expected_bundle_status") != "PASS_REHEARSAL_V2_2_AWAITING_OWNER_REVIEW"
        or (
            binding.mode == "REGISTERED_OFFICIAL"
            and storage.get("primary_recovery_container")
            != OFFICIAL_PRIMARY_RECOVERY_CONTAINER.as_posix()
        )
        or (
            binding.mode == "REGISTERED_OFFICIAL"
            and storage.get("secondary_recovery_container")
            != OFFICIAL_SECONDARY_RECOVERY_CONTAINER.as_posix()
        )
        or not isinstance(storage.get("primary_recovery_container"), str)
        or not Path(cast(str, storage.get("primary_recovery_container"))).is_absolute()
        or not isinstance(storage.get("secondary_recovery_container"), str)
        or not Path(cast(str, storage.get("secondary_recovery_container"))).is_absolute()
        or storage.get("destination_publication_mode") != "ATOMIC_DIRECTORY_NO_REPLACE"
        or storage.get("secondary_snapshot_publication_mode") != "ATOMIC_DIRECTORY_NO_REPLACE"
        or storage.get("primary_receipt_publication_mode") != "CREATE_ONLY"
        or storage.get("secondary_receipt_publication_mode") != "CREATE_ONLY"
        or storage.get("paired_receipts_required") is not True
        or any(
            storage.get(key) is not True
            for key in (
                "claim_name_derived_from_authorization_sha256",
                "destination_stage_name_derived_from_authorization_sha256",
                "secondary_snapshot_stage_name_derived_from_authorization_sha256",
                "secondary_snapshot_name_derived_from_authorization_sha256_and_tree_root",
                "receipt_name_derived_from_authorization_sha256_and_tree_root",
            )
        )
        or interpreter
        != {
            "launcher_path": FIXED_PYTHON_LAUNCHER.as_posix(),
            "launcher_sha256": FIXED_PYTHON_SHA256,
            "orig_argv_executable": FIXED_ORIG_ARGV_EXECUTABLE.as_posix(),
            "orig_argv_executable_sha256": FIXED_ORIG_ARGV_EXECUTABLE_SHA256,
            "version": platform.python_version(),
        }
        or execution.get("epoch") != EPOCH_8_IMPLEMENTATION_EPOCH
        or execution.get("latest_complete_landed_epoch_required") is not True
        or execution.get("current_control_bytes_required") is not True
        or execution.get("loaded_module_bytes_required") is not True
        or census_reference.get("schema_version") != EPOCH_8_LINEAGE_CENSUS_SCHEMA
        or census_reference.get("result") != "PASS_REAL_LINEAGE_CENSUS"
        or census_reference.get("all_references_revalidated_at_start") is not True
        or census_reference.get("invalid_count") != 0
    ):
        raise RehearsalV22Error("bundle recovery effects, storage, or live envelope drifted")
    if require_current_process:
        observed_os_argv = (FIXED_PYTHON_LAUNCHER.as_posix(), *sys.orig_argv[1:])
        if observed_os_argv != tuple(exact_argv_raw) or dict(os.environ) != EXACT_ENVIRONMENT:
            raise RehearsalV22Error("bundle recovery R differs from this locked process")
    return BundleRecoveryAuthorization(
        path=expected_path,
        payload=payload,
        sha256=authority.sha256,
        creating_commit=authority.creating_commit,
        authorization_id=cast(str, document["authorization_id"]),
        sealed_series=dict(sealed),
        execution_epoch=dict(execution),
        destination=dict(destination),
        exact_argv=tuple(cast(list[str], exact_argv_raw)),
        command_sha256=cast(str, document["command_sha256"]),
        exact_environment=dict(cast(dict[str, str], exact_environment_raw)),
        environment_sha256=cast(str, document["environment_sha256"]),
        effect_authorization=cast(dict[str, bool], dict(effects)),
        interpreter=dict(interpreter),
        locks=cast(dict[str, bool], dict(locks)),
    )


def _validate_recovery_owner_binding(
    binding: ExecutionBinding,
    authorization: BundleRecoveryAuthorization,
    owner_binding_authority: AuthorityReference,
) -> RecoveryOwnerBinding:
    """Validate B and its Q->R->B topology before any storage observation."""

    root = binding.project_root.absolute()
    execution_head = _current_execution_head(root)
    payload = validate_unique_a_authority(
        root,
        owner_binding_authority,
        execution_head=execution_head,
    )
    document = _object(
        strict_json_loads(payload, source="recovery owner confirmation binding"),
        "recovery owner confirmation binding",
    )
    if (
        set(document) != RECOVERY_OWNER_BINDING_FIELDS
        or _canonical_json_bytes(document) != payload
        or document.get("schema_version") != EPOCH_8_RECOVERY_OWNER_BINDING_SCHEMA
        or not isinstance(document.get("status"), str)
        or not document.get("status")
    ):
        raise RehearsalV22Error("recovery owner confirmation binding is not exact canonical B")
    _created_utc, created_shanghai = _validate_recovery_timestamp_pair(
        document.get("created_at_utc"),
        document.get("created_at_shanghai"),
        "recovery owner binding",
    )
    date_token = created_shanghai[:10].replace("-", "")
    expected_relative = (
        "docs/phase4/reports/P4.2a-v2-2-series2-through-ordinal-000002-"
        f"bundle-recovery-owner-confirmation-binding-{date_token}.json"
    )
    if (
        owner_binding_authority.path != expected_relative
        or not isinstance(document.get("binding_id"), str)
        or not document.get("binding_id")
        or document.get("status") != "OWNER_CONFIRMATION_BOUND"
    ):
        raise RehearsalV22Error("recovery owner confirmation binding identity drifted")
    review_row = _exact_contract_object(
        document.get("review_request"),
        frozenset({"path", "sha256", "bytes", "creating_commit"}),
        "recovery owner binding Q reference",
    )
    authorization_row = _exact_contract_object(
        document.get("recovery_authorization"),
        frozenset({"path", "sha256", "bytes", "creating_commit"}),
        "recovery owner binding R reference",
    )
    confirmation = _exact_contract_object(
        document.get("owner_confirmation"),
        frozenset(
            {
                "identity",
                "confirmation_text",
                "observed_at_utc",
                "observed_at_shanghai",
                "source",
                "authorization_sha256",
            }
        ),
        "recovery owner confirmation",
    )
    _validate_recovery_timestamp_pair(
        confirmation.get("observed_at_utc"),
        confirmation.get("observed_at_shanghai"),
        "recovery owner confirmation",
    )
    scope = _exact_contract_object(
        document.get("authorized_scope"),
        frozenset(
            {
                "series_token_sha256",
                "selected_attempt_ordinal",
                "authorized_bundle_recovery_starts",
                "authorized_pipeline_starts",
                "automatic_retry_count",
                "scope",
            }
        ),
        "recovery owner authorized scope",
    )
    exclusions = _exact_contract_object(
        document.get("explicit_exclusions"),
        frozenset(
            {
                "attempt_allocation",
                "ledger_or_sealed_mirror_write",
                "pipeline",
                "heldout_materialization_inference_or_evaluation",
                "p4_2b",
                "p4_3",
                "trading",
            }
        ),
        "recovery owner exclusions",
    )
    preflight = _exact_contract_object(
        document.get("registered_read_only_recovery_preflight"),
        frozenset(
            {"path", "stdout_sha256", "stdout_bytes", "real_lineage_census_sha256", "result"}
        ),
        "recovery owner preflight reference",
    )
    machine = _exact_contract_object(
        document.get("machine_boundary"),
        frozenset(
            {
                "consumed_by_recovery_runner",
                "evidence_only",
                "passed_as_bundle_recovery_confirmation_binding",
                "machine_recovery_authorization_remains_exactly_19_fields",
                "this_document_adds_no_field_to_the_19_field_authorization",
            }
        ),
        "recovery owner machine boundary",
    )
    b_parents = _git_parents_epoch_7(root, owner_binding_authority.creating_commit)
    r_parents = _git_parents_epoch_7(root, authorization.creating_commit)
    if len(b_parents) != 1 or b_parents[0] != authorization.creating_commit or len(r_parents) != 1:
        raise RehearsalV22Error("recovery Q->R->B topology drifted")
    q_commit = r_parents[0]
    q_reference = AuthorityReference(
        cast(str, review_row.get("path")),
        cast(str, review_row.get("sha256")),
        cast(str, review_row.get("creating_commit")),
    )
    if q_reference.creating_commit != q_commit:
        raise RehearsalV22Error("recovery Q reference is not the direct parent of R")
    q_payload = validate_unique_a_authority(root, q_reference, execution_head=execution_head)
    q_document = _object(
        strict_json_loads(q_payload, source="bundle recovery Q"),
        "bundle recovery Q",
    )
    _q_created_utc, _q_created_shanghai = _validate_recovery_timestamp_pair(
        q_document.get("created_at_utc"),
        q_document.get("created_at_shanghai"),
        "bundle recovery Q",
    )
    proposed = _exact_contract_object(
        q_document.get("proposed_recovery_authorization"),
        frozenset({"path", "document", "canonical_json_sha256", "bytes", "currently_effective"}),
        "bundle recovery Q proposed R",
    )
    requester = _exact_contract_object(
        q_document.get("requester"),
        frozenset({"identity", "role", "scope"}),
        "bundle recovery Q requester",
    )
    landed_epoch = _exact_contract_object(
        q_document.get("landed_execution_epoch"),
        frozenset(
            {
                "epoch",
                "implementation_commit",
                "owner_exact_surface_authorization",
                "independent_implementation_review",
                "merge_commit",
                "landing_report",
                "control_merkle_root_sha256",
                "control_record_count",
            }
        ),
        "bundle recovery Q landed epoch",
    )
    q_preflight = _exact_contract_object(
        q_document.get("registered_read_only_recovery_preflight"),
        frozenset(
            {
                "exact_argv",
                "stdout_canonical_json",
                "stdout_sha256",
                "stdout_bytes",
                "stderr_bytes",
                "returncode",
                "status",
                "real_lineage_census",
            }
        ),
        "bundle recovery Q preflight",
    )
    equality = _exact_contract_object(
        q_document.get("preflight_before_after_equality"),
        frozenset(
            {
                "head",
                "control_surface",
                "git_refs",
                "official_ledger",
                "sealed_mirror",
                "destination",
                "heldout",
                "temporary_paths",
            }
        ),
        "bundle recovery Q preflight equality",
    )
    requested = _exact_contract_object(
        q_document.get("requested_owner_action_time_confirmation"),
        frozenset(
            {
                "required_owner_identity",
                "requested_exact_confirmation",
                "delivery_channel",
                "confirmation_not_yet_received",
            }
        ),
        "bundle recovery Q requested confirmation",
    )
    post_plan = _exact_contract_object(
        q_document.get("post_confirmation_plan_not_yet_executed"),
        frozenset(
            {
                "land_r",
                "land_b",
                "revalidate_start_census",
                "one_recovery_start",
                "zero_pipeline_start",
                "zero_automatic_retry",
            }
        ),
        "bundle recovery Q post-confirmation plan",
    )
    current_locks = _exact_contract_object(
        q_document.get("current_locks"),
        frozenset(
            {
                "series_closed",
                "attempts_allocated",
                "selected_attempt_ordinal",
                "ledger_and_sealed_mirror_read_only",
                "destination_created",
                "bundle_recovery_authorization_created",
                "owner_confirmation_binding_created",
                "bundle_recovery_starts",
                "pipeline_starts_in_recovery",
                "automatic_retries_in_recovery",
                "recovery_claim_created",
                "recovered_bundle_mirror_created",
                "heldout_evaluation_attempts_consumed",
                "p4_2a_done",
                "p4_2b_unlocked",
                "p4_3_unlocked",
                "trading_unlocked",
            }
        ),
        "bundle recovery Q locks",
    )
    stdout_text = q_preflight.get("stdout_canonical_json")
    if not isinstance(stdout_text, str):
        raise RehearsalV22Error("bundle recovery Q preflight stdout is not text")
    stdout_payload = stdout_text.encode("utf-8")
    stdout_document = _object(
        strict_json_loads(stdout_payload, source="bundle recovery Q preflight stdout"),
        "bundle recovery Q preflight stdout",
    )
    if _canonical_json_bytes(stdout_document) != stdout_payload:
        raise RehearsalV22Error("bundle recovery Q preflight stdout is not canonical JSON")
    r_reference = authorization.authority_ref(root)
    execution_owner = _validate_authority_ref_shape(
        authorization.execution_epoch.get("owner_exact_surface_authorization"),
        "bundle recovery Q execution owner",
    )
    execution_review = _validate_authority_ref_shape(
        authorization.execution_epoch.get("independent_implementation_review"),
        "bundle recovery Q execution review",
    )
    execution_landing = _validate_authority_ref_shape(
        authorization.execution_epoch.get("landing_report"),
        "bundle recovery Q execution landing",
    )
    (
        series_storage_preflight,
        registered_preflight_storage,
        sealed_inputs_preflight,
    ) = _validate_epoch_8_embedded_preflight_semantics(
        binding,
        authorization,
        stdout_document,
        execution_landing=execution_landing,
    )
    expected_preflight_argv = [
        FIXED_PYTHON_LAUNCHER.as_posix(),
        "-S",
        "-P",
        "-B",
        (root / SHIM_RELATIVE).as_posix(),
        "--preflight-only",
        "--implementation-epoch",
        str(EPOCH_8_IMPLEMENTATION_EPOCH),
        "--implementation-commit",
        cast(str, authorization.execution_epoch.get("implementation_commit")),
        "--owner-surface-authorization",
        root.joinpath(*PurePosixPath(execution_owner.path).parts).as_posix(),
        "--independent-implementation-review",
        root.joinpath(*PurePosixPath(execution_review.path).parts).as_posix(),
        "--landing-report",
        root.joinpath(*PurePosixPath(execution_landing.path).parts).as_posix(),
    ]
    expected_r_row = {
        "path": r_reference.path,
        "sha256": r_reference.sha256,
        "bytes": len(authorization.payload),
        "creating_commit": r_reference.creating_commit,
    }
    if (
        set(q_document) != RECOVERY_REVIEW_REQUEST_FIELDS
        or _canonical_json_bytes(q_document) != q_payload
        or q_document.get("schema_version") != EPOCH_8_RECOVERY_REVIEW_REQUEST_SCHEMA
        or not isinstance(q_document.get("request_id"), str)
        or not q_document.get("request_id")
        or q_document.get("status") != "AWAITING_INDEPENDENT_REVIEW_AND_OWNER_CONFIRMATION"
        or requester
        != {
            "identity": "codex",
            "role": "operator",
            "scope": "sealed_bundle_recovery_only",
        }
        or landed_epoch
        != {
            key: authorization.execution_epoch[key]
            for key in (
                "epoch",
                "implementation_commit",
                "owner_exact_surface_authorization",
                "independent_implementation_review",
                "merge_commit",
                "landing_report",
                "control_merkle_root_sha256",
                "control_record_count",
            )
        }
        or q_preflight.get("stdout_sha256") != _sha256(stdout_payload)
        or q_preflight.get("exact_argv") != expected_preflight_argv
        or q_preflight.get("stdout_bytes") != len(stdout_payload)
        or q_preflight.get("stderr_bytes") != 0
        or q_preflight.get("returncode") != 0
        or q_preflight.get("status") != "PASS_READ_ONLY_IMPLEMENTATION_PREFLIGHT"
        or q_preflight.get("real_lineage_census")
        != authorization.execution_epoch.get("real_lineage_census")
        or stdout_document.get("status") != "PASS_READ_ONLY_IMPLEMENTATION_PREFLIGHT"
        or set(stdout_document) != set(EPOCH_8_READ_ONLY_PREFLIGHT_FIELD_ORDER)
        or stdout_document.get("schema_version") != EPOCH_8_READ_ONLY_PREFLIGHT_SCHEMA
        or "epoch_7_recovery_storage" in stdout_document
        or stdout_document.get("implementation_epoch") != EPOCH_8_IMPLEMENTATION_EPOCH
        or stdout_document.get("implementation_commit")
        != authorization.execution_epoch.get("implementation_commit")
        or stdout_document.get("owner_exact_surface_authorization")
        != execution_owner.as_json()
        or stdout_document.get("independent_implementation_review")
        != execution_review.as_json()
        or stdout_document.get("control_merkle_root_sha256")
        != authorization.execution_epoch.get("control_merkle_root_sha256")
        or stdout_document.get("control_record_count")
        != authorization.execution_epoch.get("control_record_count")
        or _census_reference(
            _object(
                stdout_document.get("real_lineage_census"),
                "bundle recovery Q full preflight census",
            )
        )
        != q_preflight.get("real_lineage_census")
        or stdout_document.get("real_lineage_census") is None
        or any(value is not True for value in equality.values())
        or any(value is not True for value in post_plan.values())
        or requested.get("required_owner_identity") != "ouyang"
        or requested.get("delivery_channel") != "in_person_via_independent_reviewer"
        or requested.get("confirmation_not_yet_received") is not True
        or not isinstance(requested.get("requested_exact_confirmation"), str)
        or not requested.get("requested_exact_confirmation")
        or confirmation.get("confirmation_text") != requested.get("requested_exact_confirmation")
        or current_locks
        != {
            "series_closed": True,
            "attempts_allocated": 2,
            "selected_attempt_ordinal": 2,
            "ledger_and_sealed_mirror_read_only": True,
            "destination_created": False,
            "bundle_recovery_authorization_created": False,
            "owner_confirmation_binding_created": False,
            "bundle_recovery_starts": 0,
            "pipeline_starts_in_recovery": 0,
            "automatic_retries_in_recovery": 0,
            "recovery_claim_created": False,
            "recovered_bundle_mirror_created": False,
            "heldout_evaluation_attempts_consumed": 0,
            "p4_2a_done": False,
            "p4_2b_unlocked": False,
            "p4_3_unlocked": False,
            "trading_unlocked": False,
        }
        or review_row
        != {
            "path": q_reference.path,
            "sha256": q_reference.sha256,
            "bytes": len(q_payload),
            "creating_commit": q_reference.creating_commit,
        }
        or authorization_row != expected_r_row
        or proposed.get("path") != r_reference.path
        or proposed.get("document")
        != strict_json_loads(authorization.payload, source="recovery authorization proposed bytes")
        or proposed.get("canonical_json_sha256") != authorization.sha256
        or proposed.get("bytes") != len(authorization.payload)
        or proposed.get("currently_effective") is not False
        or confirmation.get("identity") != "ouyang"
        or confirmation.get("source") != "业主向复核方当面确认，由复核方转达"
        or confirmation.get("authorization_sha256") != authorization.sha256
        or authorization.sha256 not in cast(str, confirmation.get("confirmation_text"))
        or "pipeline start 0" not in cast(str, confirmation.get("confirmation_text"))
        or "automatic retry 0" not in cast(str, confirmation.get("confirmation_text"))
        or scope
        != {
            "series_token_sha256": binding.series_token_sha256,
            "selected_attempt_ordinal": 2,
            "authorized_bundle_recovery_starts": 1,
            "authorized_pipeline_starts": 0,
            "automatic_retry_count": 0,
            "scope": "one_disclosed_sealed_bundle_recovery_only",
        }
        or any(value is not True for value in exclusions.values())
        or machine
        != {
            "consumed_by_recovery_runner": True,
            "evidence_only": False,
            "passed_as_bundle_recovery_confirmation_binding": True,
            "machine_recovery_authorization_remains_exactly_19_fields": True,
            "this_document_adds_no_field_to_the_19_field_authorization": True,
        }
        or preflight.get("result") != "PASS_READ_ONLY_IMPLEMENTATION_PREFLIGHT"
        or preflight.get("stdout_sha256") != q_preflight.get("stdout_sha256")
        or preflight.get("stdout_bytes") != q_preflight.get("stdout_bytes")
        or preflight.get("real_lineage_census_sha256")
        != _object(
            authorization.execution_epoch.get("real_lineage_census"),
            "recovery execution census reference",
        ).get("canonical_json_sha256")
    ):
        raise RehearsalV22Error("recovery owner binding semantics drifted")
    return RecoveryOwnerBinding(
        path=root.joinpath(*PurePosixPath(owner_binding_authority.path).parts),
        payload=payload,
        sha256=owner_binding_authority.sha256,
        creating_commit=owner_binding_authority.creating_commit,
        review_request=q_reference,
        recovery_authorization=r_reference,
        owner_confirmation=dict(confirmation),
        series_2_registered_storage_preflight=series_storage_preflight,
        registered_recovery_storage_preflight=registered_preflight_storage,
        sealed_recovery_inputs_preflight=sealed_inputs_preflight,
    )


def _validated_attempt(
    binding: ExecutionBinding,
    *,
    ordinal: int,
    previous_history_root: str,
    ledger_root: Path | None = None,
    historical_authority_bytes: bool = False,
) -> ValidatedAttemptRecord:
    root = binding.ledger_root if ledger_root is None else ledger_root
    attempt_root = root / "attempts" / f"{ordinal:06d}"
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
        historical_execution_head=(
            authorization.creating_commit if historical_authority_bytes else None
        ),
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
    if terminal is None:
        outcome: AttemptOutcome = "INCOMPLETE_UNTERMINALIZED"
        reached_stage = (
            "candidate_without_terminal" if candidate is not None else "started_without_terminal"
        )
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


def validate_live_history(
    binding: ExecutionBinding,
    *,
    ledger_root: Path | None = None,
    historical_authority_bytes: bool = False,
) -> HistoryValidation:
    """Strictly replay every live attempt byte and recompute both history roots."""

    root = binding.ledger_root if ledger_root is None else ledger_root
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
    _validate_series_document(binding, ledger_root=root)
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
        record = _validated_attempt(
            binding,
            ordinal=ordinal,
            previous_history_root=previous,
            ledger_root=root,
            historical_authority_bytes=historical_authority_bytes,
        )
        if selected is not None:
            raise RehearsalV22Error("attempt exists after first validated candidate")
        if record.outcome == "CANDIDATE_VALIDATED_AND_SELECTED":
            selected = ordinal
        records.append(record)
        previous = record.history_root_sha256
    if records:
        used_epochs: list[int] = []
        for record in records:
            if not used_epochs or used_epochs[-1] != record.implementation_epoch:
                used_epochs.append(record.implementation_epoch)
        if used_epochs != list(
            range(SERIES_2_EPOCH_ORIGIN, SERIES_2_EPOCH_ORIGIN + len(used_epochs))
        ):
            raise RehearsalV22Error(
                "series-2 implementation epochs do not start at 5 and advance contiguously"
            )
    live_root, inventory = _live_ledger_root(binding, ledger_root=root)
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


def _validate_next_series_2_epoch(
    history: HistoryValidation,
    implementation_epoch: int,
) -> None:
    permitted = (
        {SERIES_2_EPOCH_ORIGIN}
        if not history.records
        else {
            history.records[-1].implementation_epoch,
            history.records[-1].implementation_epoch + 1,
        }
    )
    if implementation_epoch not in permitted:
        raise RehearsalV22Error(
            "next series-2 action epoch is not origin 5, the current epoch, or its successor"
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
        history = validate_live_history(self.ledger.binding)
        if (
            len(history.records) != self.ordinal
            or history.records[-1].terminal_path != path
            or history.records[-1].outcome != outcome
        ):
            raise RehearsalV22Error("sealed terminal did not replay as the active attempt")
        _mirror_after_terminal_seal(self, history)
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
        _validate_registered_storage_roots(binding)
        root = binding.ledger_root
        if not os.path.lexists(root):
            if any(
                os.path.lexists(path)
                for path in (
                    binding.primary_receipt_root,
                    binding.secondary_snapshot_root,
                    binding.secondary_receipt_root,
                )
            ):
                raise RehearsalV22Error("mirror leaf appeared before primary ledger initialization")
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
        ordinal = len(history.records) + 1
        if (
            action_authorization.ordinal != ordinal
            or action_authorization.previous_history_root_sha256 != history.history_root_sha256
            or action_authorization.path != self.binding.action_authorization_path
        ):
            raise RehearsalV22Error("action authorization does not bind the next live ordinal")
        observed_action = _validate_action_authorization(
            self.binding,
            action_authorization.authority_ref(self.binding.project_root),
            expected_ordinal=ordinal,
            expected_previous_history_root_sha256=history.history_root_sha256,
            require_current_process=True,
        )
        if observed_action != action_authorization:
            raise RehearsalV22Error("locked allocation action authorization drifted")
        _validate_next_series_2_epoch(
            history,
            action_authorization.implementation_epoch,
        )
        mirrored = _validate_continuation_mirror_state(
            self.binding,
            history,
            permit_unmirrored_final_incomplete=True,
        )
        if not mirrored:
            policy = _AUDIT_POLICY.get()
            if policy is None:
                raise RehearsalV22Error("incomplete-history mirror lacks an audit policy")
            with _audited_execution(
                replace(
                    policy,
                    ledger_write_phase="frozen",
                    ledger_root=self.binding.ledger_root,
                    active_attempt_root=None,
                )
            ):
                _mirror_before_next_allocation(self, history, action_authorization)
            history = validate_live_history(self.binding)
            _validate_hot_second_copy_commitment(self.binding, history)
        if history.series_closed:
            raise RehearsalV22Error("first validated candidate already closed the series")
        ordinal = len(history.records) + 1
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


@dataclass(frozen=True, slots=True)
class _FrozenControlRecord:
    logical_name: str
    bundle_relative_path: str
    source_kind: str
    repository_path: str | None
    byte_count: int
    sha256: str
    current_byte_required: bool


@dataclass(frozen=True, slots=True)
class _FrozenPayloadFact:
    bundle_relative_path: str
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _ControlSurfaceCacheEnvelope:
    """Deep-immutable, single-pass cache of already validated control facts."""

    _nonce: object
    pass_kind: Literal["LIVE_CURRENT"]
    resolved_project_root: str
    root_st_dev: int
    root_st_ino: int
    execution_head: str
    ref_snapshot_sha256: str
    lineage_census_sha256: str
    implementation_commit: str
    records: tuple[_FrozenControlRecord, ...]
    payload_facts: tuple[_FrozenPayloadFact, ...]
    manifest_payload: bytes
    manifest_sha256: str
    merkle_root_sha256: str
    ast_closure_paths: tuple[str, ...]
    loaded_repository_sources: tuple[str, ...]
    python_inventory_bytes: int
    python_inventory_sha256: str
    package_inventory_bytes: int
    package_inventory_sha256: str
    integrity_sha256: str


@dataclass(frozen=True)
class HistoricalSelectedAnchor:
    """Selected ordinal-2 identity, derived only from sealed bytes and Git blobs."""

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
    require_current: Literal[False]


@dataclass(frozen=True)
class LiveExecutionAnchor:
    """Latest landed epoch identity, derived from current reviewed bytes."""

    execution_epoch: int
    implementation_commit: str
    owner_surface_authorization: AuthorityReference
    independent_implementation_review: AuthorityReference
    merge_commit: str
    landing_report: AuthorityReference
    control_surface: ControlSurface
    execution_head: str
    loaded_module_sha256: Mapping[str, str]
    real_lineage_census_sha256: str
    require_current: Literal[True]


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
    interpreter: Mapping[str, Any]
    locks: Mapping[str, bool]

    def authority_ref(self, project_root: Path) -> AuthorityReference:
        return AuthorityReference(
            path=self.path.relative_to(project_root).as_posix(),
            sha256=self.sha256,
            creating_commit=self.creating_commit,
        )


@dataclass(frozen=True)
class RecoveryOwnerBinding:
    path: Path
    payload: bytes
    sha256: str
    creating_commit: str
    review_request: AuthorityReference
    recovery_authorization: AuthorityReference
    owner_confirmation: Mapping[str, Any]
    series_2_registered_storage_preflight: Mapping[str, Any] | None = None
    registered_recovery_storage_preflight: Mapping[str, Any] | None = None
    sealed_recovery_inputs_preflight: Mapping[str, Any] | None = None

    def authority_ref(self, project_root: Path) -> AuthorityReference:
        return AuthorityReference(
            path=self.path.relative_to(project_root).as_posix(),
            sha256=self.sha256,
            creating_commit=self.creating_commit,
        )


@dataclass(frozen=True)
class RecoveredPublicationCapability:
    """Exact 40-field, non-fallback authority shared with the validator."""

    recovery_authorization_path: str
    recovery_authorization_sha256: str
    recovery_authorization_creating_commit: str
    owner_binding_path: str
    owner_binding_sha256: str
    owner_binding_creating_commit: str
    claim_root: str
    claim_started_sha256: str
    claim_terminal_sha256: str
    series_token_sha256: str
    selected_attempt_ordinal: int
    selected_implementation_epoch: int
    selected_implementation_commit: str
    sealed_history_root_sha256: str
    sealed_live_ledger_root_sha256: str
    destination: str
    published_bundle_sha256: str
    published_tree_sha256: str
    secondary_snapshot: str
    secondary_snapshot_tree_sha256: str
    primary_receipt_path: str
    secondary_receipt_path: str
    paired_receipt_sha256: str
    paired_receipt_bytes: int
    execution_epoch: int
    execution_implementation_commit: str
    execution_control_merkle_root_sha256: str
    recovery_starts: int
    pipeline_starts: int
    automatic_retry_count: int
    sealed_ledger_before_after_equal: bool
    sealed_mirror_before_after_equal: bool
    selected_candidate_sha256: str
    selected_terminal_sha256: str
    selected_evidence_tree_root_sha256: str
    historical_run_a_root_sha256: str
    historical_run_b_root_sha256: str
    historical_run_a_probe_sha256: str
    historical_run_b_probe_sha256: str
    historical_full_downstream_replay_verified: bool


@dataclass(frozen=True)
class _SealedPipelineReplay:
    run_label: str
    artifacts: Mapping[str, bytes]
    probe_evidence: Mapping[str, Any]


@dataclass(frozen=True)
class _RecoveryStoragePaths:
    primary_container: Path
    secondary_container: Path
    claim_root: Path
    destination_stage: Path
    secondary_snapshot_stage: Path
    secondary_snapshot_prefix: str
    receipt_prefix: str


@dataclass(frozen=True)
class RecoveryExecutionCapability:
    _nonce: object
    binding: ExecutionBinding
    bootstrap: _BootstrapEvidence
    authorization: BundleRecoveryAuthorization
    owner_binding: RecoveryOwnerBinding
    historical_anchor: HistoricalSelectedAnchor
    live_anchor: LiveExecutionAnchor
    storage: _RecoveryStoragePaths
    audit_policy: _AuditPolicy


@dataclass(frozen=True)
class RecoveryValidatorDelegation:
    _nonce: object
    capability_id: int
    validator_module_id: int
    bundle_path: Path
    bundle_sha256: str
    lifetime_id: int


@dataclass(frozen=True)
class RecoveredPublicationValidatorDelegation:
    _nonce: object
    capability_id: int
    validator_module_id: int
    bundle_path: Path
    bundle_sha256: str
    release_path: Path
    release_sha256: str
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
            raise RehearsalV22Error(f"explicit registered package root is unavailable: {key}")
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
            raise RehearsalV22Error("fixed registered package inventory root is aliased")
        projected.append(package_root.relative_to(REGISTERED_PROJECT_ROOT).as_posix())
    if (
        projected != [PACKAGE_ROOT_RELATIVE.as_posix()]
        or _sha256(_canonical_json_bytes(projected)) != PACKAGE_ROOTS_SHA256
    ):
        raise RehearsalV22Error("fixed registered package root projection drifted")
    rows: list[JsonObject] = []
    names: list[str] = []
    for distribution in importlib.metadata.distributions(
        path=[path.as_posix() for path in selected]
    ):
        raw_name = distribution.metadata["Name"]
        if not isinstance(raw_name, str) or not raw_name:
            raise RehearsalV22Error("registered package inventory contains an unnamed distribution")
        name = _normalized_distribution_name(raw_name)
        names.append(name)
        rows.append({"name": name, "version": distribution.version})
    if len(names) != 84 or len(set(names)) != 84:
        raise RehearsalV22Error("registered package inventory count or name uniqueness drifted")
    rows.sort(key=lambda row: (cast(str, row["name"]), cast(str, row["version"])))
    payload = _canonical_json_bytes(rows)
    if _sha256(payload) != PACKAGE_INVENTORY_SHA256:
        raise RehearsalV22Error("registered package inventory bytes drifted")
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


def _commit_tree_blob_reader(
    project_root: Path,
    implementation_commit: str,
) -> Callable[[str], bytes]:
    """Bind one immutable commit tree once and read each requested blob once."""

    root = project_root.absolute()
    commit = _git_commit(root, implementation_commit, "control tree implementation commit")
    raw_tree = _git_bytes(
        root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        commit,
        "--",
    )
    entries: dict[str, tuple[bytes, str]] = {}
    raw_paths: list[bytes] = []
    for raw_row in raw_tree.removesuffix(b"\0").split(b"\0") if raw_tree else ():
        metadata, separator, raw_path = raw_row.partition(b"\t")
        fields = metadata.split()
        if (
            separator != b"\t"
            or len(fields) != 3
            or re.fullmatch(rb"[0-7]{6}", fields[0]) is None
            or fields[1] != b"blob"
            or re.fullmatch(rb"[0-9a-f]{40}", fields[2]) is None
        ):
            raise RehearsalV22Error("control implementation tree row is malformed")
        try:
            relative = raw_path.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RehearsalV22Error("control implementation tree path is not UTF-8") from exc
        parsed = PurePosixPath(relative)
        if (
            not relative
            or parsed.is_absolute()
            or any(part in {"", ".", ".."} for part in parsed.parts)
            or parsed.as_posix() != relative
        ):
            raise RehearsalV22Error("control implementation tree path is unsafe")
        if relative in entries:
            raise RehearsalV22Error("control implementation tree repeats a path")
        entries[relative] = (fields[0], fields[2].decode("ascii", errors="strict"))
        raw_paths.append(raw_path)
    if raw_paths != sorted(raw_paths):
        raise RehearsalV22Error("control implementation tree paths are not ordered")
    cache: dict[str, bytes] = {}

    def read_blob(relative: str) -> bytes:
        path = _relative_text(relative, "control implementation blob path")
        cached = cache.get(path)
        if cached is not None:
            return cached
        entry = entries.get(path)
        if entry is None:
            raise base_runner.RehearsalError(f"optional closure candidate is absent: {path}")
        mode, object_id = entry
        if mode not in {b"100644", b"100755"}:
            raise RehearsalV22Error(f"control implementation path is not regular: {path}")
        payload = _git_bytes(root, "cat-file", "blob", object_id)
        observed_object_id = hashlib.sha1(
            b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload
        ).hexdigest()
        if observed_object_id != object_id:
            raise RehearsalV22Error(f"control implementation blob identity drifted: {path}")
        cache[path] = payload
        return payload

    return read_blob


def build_control_surface(
    project_root: Path,
    implementation_commit: str,
    *,
    require_current: bool = True,
) -> ControlSurface:
    """Rebuild the commit-bound control archive for one implementation epoch."""

    root = project_root.absolute()
    commit = _git_commit(root, implementation_commit, "control implementation commit")
    series_2_control = _git_is_ancestor(root, SERIES_2_PREREGISTRATION_COMMIT, commit)
    execution_head = _git_bytes(root, "rev-parse", "HEAD").decode("ascii", errors="strict").strip()
    validate_strict_v2_1_inheritance(root)
    validate_carry_forward_lineage(
        root,
        execution_head=execution_head,
        require_current=True,
    )
    if _git_is_ancestor(root, SERIES_2_PREREGISTRATION_COMMIT, execution_head):
        validate_series_2_preregistration(root, execution_head=execution_head)
    validate_unique_a_authority(
        root,
        AuthorityReference(
            INITIAL_SURFACE_REVIEW_RELATIVE.as_posix(),
            INITIAL_SURFACE_REVIEW_SHA256,
            INITIAL_SURFACE_REVIEW_COMMIT,
        ),
        execution_head=execution_head,
        allow_initial_sibling=True,
    )

    blob_reader = _commit_tree_blob_reader(root, commit)

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
    if series_2_control:
        controls.update(
            {
                SERIES_2_PREREGISTRATION_RELATIVE.as_posix(),
                SERIES_2_BUNDLE_SCHEMA_RELATIVE.as_posix(),
                SERIES_2_RELEASE_SCHEMA_RELATIVE.as_posix(),
                SERIES_2_LOSS_INCIDENT_RELATIVE.as_posix(),
                SERIES_2_OWNER_DECISION_RELATIVE.as_posix(),
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
    if series_2_control:
        frozen.update(
            {
                SERIES_2_PREREGISTRATION_RELATIVE.as_posix(): (SERIES_2_PREREGISTRATION_SHA256),
                SERIES_2_BUNDLE_SCHEMA_RELATIVE.as_posix(): SERIES_2_BUNDLE_SCHEMA_SHA256,
                SERIES_2_RELEASE_SCHEMA_RELATIVE.as_posix(): SERIES_2_RELEASE_SCHEMA_SHA256,
                SERIES_2_LOSS_INCIDENT_RELATIVE.as_posix(): SERIES_2_TOKEN_SEED_SHA256,
                SERIES_2_OWNER_DECISION_RELATIVE.as_posix(): SERIES_2_OWNER_DECISION_SHA256,
            }
        )
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


_CONTROL_RECORD_FIELDS = frozenset(
    {
        "logical_name",
        "bundle_relative_path",
        "source_kind",
        "repository_path",
        "bytes",
        "sha256",
    }
)
_CONTROL_MANIFEST_RELATIVE = "archive/control-surface/manifest.json"
_CONTROL_PYTHON_RUNTIME_RELATIVE = "archive/control-surface/root/runtime/python.json"
_CONTROL_PACKAGE_RUNTIME_RELATIVE = "archive/control-surface/root/runtime/packages.json"


def _control_cache_root_identity(project_root: Path) -> tuple[Path, int, int]:
    absolute = project_root.absolute()
    resolved = project_root.resolve(strict=True)
    if absolute != resolved or project_root.is_symlink():
        raise RehearsalV22Error("control cache project root is not one resolved real directory")
    status = resolved.stat()
    if not stat.S_ISDIR(status.st_mode):
        raise RehearsalV22Error("control cache project root is not a directory")
    return resolved, status.st_dev, status.st_ino


def _frozen_control_record_document(record: _FrozenControlRecord) -> JsonObject:
    return {
        "logical_name": record.logical_name,
        "bundle_relative_path": record.bundle_relative_path,
        "source_kind": record.source_kind,
        "repository_path": record.repository_path,
        "bytes": record.byte_count,
        "sha256": record.sha256,
    }


def _control_cache_descriptor(cache: _ControlSurfaceCacheEnvelope) -> JsonObject:
    return {
        "pass_kind": cache.pass_kind,
        "resolved_project_root": cache.resolved_project_root,
        "root_st_dev": cache.root_st_dev,
        "root_st_ino": cache.root_st_ino,
        "execution_head": cache.execution_head,
        "ref_snapshot_sha256": cache.ref_snapshot_sha256,
        "lineage_census_sha256": cache.lineage_census_sha256,
        "implementation_commit": cache.implementation_commit,
        "records": [
            {
                **_frozen_control_record_document(record),
                "current_byte_required": record.current_byte_required,
            }
            for record in cache.records
        ],
        "payload_facts": [
            {
                "bundle_relative_path": fact.bundle_relative_path,
                "bytes": fact.byte_count,
                "sha256": fact.sha256,
            }
            for fact in cache.payload_facts
        ],
        "manifest_bytes": len(cache.manifest_payload),
        "manifest_sha256": cache.manifest_sha256,
        "merkle_root_sha256": cache.merkle_root_sha256,
        "ast_closure_paths": list(cache.ast_closure_paths),
        "loaded_repository_sources": list(cache.loaded_repository_sources),
        "python_inventory_bytes": cache.python_inventory_bytes,
        "python_inventory_sha256": cache.python_inventory_sha256,
        "package_inventory_bytes": cache.package_inventory_bytes,
        "package_inventory_sha256": cache.package_inventory_sha256,
    }


def _control_cache_merkle_root(
    payload_facts: Sequence[_FrozenPayloadFact],
    *,
    manifest_payload: bytes,
) -> str:
    facts = (
        *payload_facts,
        _FrozenPayloadFact(
            bundle_relative_path=_CONTROL_MANIFEST_RELATIVE,
            byte_count=len(manifest_payload),
            sha256=_sha256(manifest_payload),
        ),
    )
    ordered = sorted(facts, key=lambda fact: fact.bundle_relative_path.encode("utf-8"))
    leaves = [
        hashlib.sha256(
            MERKLE_LEAF_PREFIX
            + fact.bundle_relative_path.encode("utf-8")
            + b"\0"
            + bytes.fromhex(fact.sha256)
        ).digest()
        for fact in ordered
    ]
    return _binary_merkle_root(leaves, node_prefix=MERKLE_NODE_PREFIX, empty_root=None)


def _validate_control_surface_cache_integrity(
    project_root: Path,
    *,
    implementation_commit: str,
    execution_head: str,
    ref_snapshot_sha256: str,
    lineage_census_sha256: str,
    pass_nonce: object,
    cache: _ControlSurfaceCacheEnvelope,
) -> None:
    root, root_st_dev, root_st_ino = _control_cache_root_identity(project_root)
    if (
        type(cache) is not _ControlSurfaceCacheEnvelope
        or cache._nonce is not pass_nonce
        or cache.pass_kind != "LIVE_CURRENT"
        or cache.resolved_project_root != root.as_posix()
        or cache.root_st_dev != root_st_dev
        or cache.root_st_ino != root_st_ino
        or cache.implementation_commit != implementation_commit
        or cache.execution_head != execution_head
        or not _lower_hex(cache.execution_head, 40)
        or cache.ref_snapshot_sha256 != ref_snapshot_sha256
        or not _lower_hex(cache.ref_snapshot_sha256, 64)
        or cache.lineage_census_sha256 != lineage_census_sha256
        or not _lower_hex(cache.lineage_census_sha256, 64)
        or not cache.records
        or not _lower_hex(cache.manifest_sha256, 64)
        or not _lower_hex(cache.merkle_root_sha256, 64)
        or cache.integrity_sha256
        != _sha256(_canonical_json_bytes(_control_cache_descriptor(cache)))
    ):
        raise RehearsalV22Error("live control cache identity or integrity drifted")
    manifest = _object(
        strict_json_loads(cache.manifest_payload, source="cached control manifest"),
        "cached control manifest",
    )
    manifest_records = [_frozen_control_record_document(record) for record in cache.records]
    if (
        set(manifest) != {"schema_version", "files"}
        or manifest.get("schema_version") != CONTROL_MANIFEST_SCHEMA
        or manifest.get("files") != manifest_records
        or _canonical_json_bytes(manifest) != cache.manifest_payload
        or _sha256(cache.manifest_payload) != cache.manifest_sha256
    ):
        raise RehearsalV22Error("live control cache manifest drifted")
    record_paths = tuple(record.bundle_relative_path for record in cache.records)
    fact_paths = tuple(fact.bundle_relative_path for fact in cache.payload_facts)
    if (
        record_paths != tuple(sorted(record_paths, key=lambda value: value.encode("utf-8")))
        or len(set(record_paths)) != len(record_paths)
        or fact_paths != record_paths
        or len(set(fact_paths)) != len(fact_paths)
        or any(
            fact.byte_count != record.byte_count or fact.sha256 != record.sha256
            for record, fact in zip(cache.records, cache.payload_facts, strict=True)
        )
        or any(
            record.current_byte_required
            is not (
                record.repository_path is not None
                and (
                    CONTROL_GOVERNANCE_AUTHORITIES.get(record.repository_path) is None
                    or CONTROL_GOVERNANCE_AUTHORITIES[record.repository_path][2] is True
                )
            )
            for record in cache.records
        )
        or _control_cache_merkle_root(
            cache.payload_facts,
            manifest_payload=cache.manifest_payload,
        )
        != cache.merkle_root_sha256
    ):
        raise RehearsalV22Error("live control cache record, payload, or Merkle facts drifted")
    repository_paths = {
        record.repository_path for record in cache.records if record.repository_path is not None
    }
    if (
        cache.ast_closure_paths != tuple(sorted(set(cache.ast_closure_paths)))
        or cache.loaded_repository_sources != tuple(sorted(set(cache.loaded_repository_sources)))
        or not set(cache.loaded_repository_sources).issubset(cache.ast_closure_paths)
        or not set(cache.ast_closure_paths).issubset(repository_paths)
    ):
        raise RehearsalV22Error("live control cache closure facts drifted")
    fact_by_path = {fact.bundle_relative_path: fact for fact in cache.payload_facts}
    python_fact = fact_by_path.get(_CONTROL_PYTHON_RUNTIME_RELATIVE)
    package_fact = fact_by_path.get(_CONTROL_PACKAGE_RUNTIME_RELATIVE)
    if (
        python_fact is None
        or package_fact is None
        or python_fact.byte_count != cache.python_inventory_bytes
        or python_fact.sha256 != cache.python_inventory_sha256
        or package_fact.byte_count != cache.package_inventory_bytes
        or package_fact.sha256 != cache.package_inventory_sha256
    ):
        raise RehearsalV22Error("live control cache runtime facts drifted")


def _freeze_control_surface_cache(
    project_root: Path,
    *,
    implementation_commit: str,
    execution_head: str,
    ref_snapshot_sha256: str,
    lineage_census_sha256: str,
    pass_nonce: object,
    control: ControlSurface,
) -> _ControlSurfaceCacheEnvelope:
    if (
        type(control) is not ControlSurface
        or control.implementation_commit != implementation_commit
    ):
        raise RehearsalV22Error("live control cache source is not its exact implementation")
    root, root_st_dev, root_st_ino = _control_cache_root_identity(project_root)
    frozen_records: list[_FrozenControlRecord] = []
    for raw_record in control.records:
        record = _object(raw_record, "live control cache source record")
        if set(record) != _CONTROL_RECORD_FIELDS:
            raise RehearsalV22Error("live control cache source record fields drifted")
        logical_name = record.get("logical_name")
        source_kind = record.get("source_kind")
        if not isinstance(logical_name, str) or not logical_name:
            raise RehearsalV22Error("live control cache logical name is malformed")
        if not isinstance(source_kind, str) or not source_kind:
            raise RehearsalV22Error("live control cache source kind is malformed")
        bundle_relative_path = _relative_text(
            record.get("bundle_relative_path"),
            "live control cache bundle path",
        )
        repository_raw = record.get("repository_path")
        repository_path = (
            None
            if repository_raw is None
            else _relative_text(repository_raw, "live control cache repository path")
        )
        byte_count = record.get("bytes")
        digest = record.get("sha256")
        if type(byte_count) is not int or byte_count < 0 or not _lower_hex(digest, 64):
            raise RehearsalV22Error("live control cache byte or SHA fact is malformed")
        governance = (
            None if repository_path is None else CONTROL_GOVERNANCE_AUTHORITIES.get(repository_path)
        )
        frozen_records.append(
            _FrozenControlRecord(
                logical_name=logical_name,
                bundle_relative_path=bundle_relative_path,
                source_kind=source_kind,
                repository_path=repository_path,
                byte_count=byte_count,
                sha256=cast(str, digest),
                current_byte_required=(
                    repository_path is not None and (governance is None or governance[2] is True)
                ),
            )
        )
    records = tuple(frozen_records)
    payload_items = tuple(
        sorted(control.payloads.items(), key=lambda item: item[0].encode("utf-8"))
    )
    payload_facts: list[_FrozenPayloadFact] = []
    for relative, payload in payload_items:
        if not isinstance(relative, str) or type(payload) is not bytes:
            raise RehearsalV22Error("live control cache payload is malformed")
        payload_facts.append(
            _FrozenPayloadFact(
                bundle_relative_path=_relative_text(relative, "live control cache payload path"),
                byte_count=len(payload),
                sha256=_sha256(payload),
            )
        )
    manifest_sha256 = _sha256(control.manifest_payload)
    python_sha256 = _sha256(control.python_inventory)
    package_sha256 = _sha256(control.package_inventory)
    draft = _ControlSurfaceCacheEnvelope(
        _nonce=pass_nonce,
        pass_kind="LIVE_CURRENT",
        resolved_project_root=root.as_posix(),
        root_st_dev=root_st_dev,
        root_st_ino=root_st_ino,
        execution_head=execution_head,
        ref_snapshot_sha256=ref_snapshot_sha256,
        lineage_census_sha256=lineage_census_sha256,
        implementation_commit=implementation_commit,
        records=records,
        payload_facts=tuple(payload_facts),
        manifest_payload=bytes(control.manifest_payload),
        manifest_sha256=manifest_sha256,
        merkle_root_sha256=control.merkle_root_sha256,
        ast_closure_paths=tuple(control.ast_closure_paths),
        loaded_repository_sources=tuple(control.loaded_repository_sources),
        python_inventory_bytes=len(control.python_inventory),
        python_inventory_sha256=python_sha256,
        package_inventory_bytes=len(control.package_inventory),
        package_inventory_sha256=package_sha256,
        integrity_sha256="",
    )
    cache = replace(
        draft,
        integrity_sha256=_sha256(_canonical_json_bytes(_control_cache_descriptor(draft))),
    )
    raw_payloads = dict(payload_items)
    if (
        tuple(fact.bundle_relative_path for fact in cache.payload_facts)
        != tuple(record.bundle_relative_path for record in cache.records)
        or any(
            len(raw_payloads[fact.bundle_relative_path]) != fact.byte_count
            or _sha256(raw_payloads[fact.bundle_relative_path]) != fact.sha256
            for fact in cache.payload_facts
        )
        or cache.merkle_root_sha256
        != _generic_merkle_root(
            {
                **raw_payloads,
                _CONTROL_MANIFEST_RELATIVE: cache.manifest_payload,
            }
        )
    ):
        raise RehearsalV22Error("live control cache source payloads are inconsistent")
    _validate_control_surface_cache_integrity(
        root,
        implementation_commit=implementation_commit,
        execution_head=execution_head,
        ref_snapshot_sha256=ref_snapshot_sha256,
        lineage_census_sha256=lineage_census_sha256,
        pass_nonce=pass_nonce,
        cache=cache,
    )
    return cache


def _historical_selected_anchor(
    binding: ExecutionBinding,
    history: HistoryValidation,
) -> HistoricalSelectedAnchor:
    """Bind selected history to immutable Git/archive bytes, never current bytes."""

    if (
        not history.series_closed
        or history.selected_attempt_ordinal is None
        or history.validated_candidate_count != 1
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
        historical_execution_head=selected.owner_action_time_authorization.creating_commit,
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
        or candidate.get("control_surface_root_sha256") != authorization.control_merkle_root_sha256
        or candidate.get("evidence_tree_root_sha256") != selected.evidence_tree_root_sha256
    ):
        raise RehearsalV22Error("historical selected anchor roots drifted")
    selected_git_blobs = {
        cast(str, row["repository_path"]): cast(str, row["sha256"])
        for row in control.records
        if row.get("repository_path") is not None
    }
    archived_git_blobs = {
        relative: _sha256(control.payloads[f"archive/control-surface/root/repo/{relative}"])
        for relative in selected_git_blobs
    }
    if not selected_git_blobs or selected_git_blobs != archived_git_blobs:
        raise RehearsalV22Error("historical selected Git blob map drifted")
    if binding.mode == "REGISTERED_OFFICIAL" and (
        len(history.records) != 2
        or history.failed_count != 1
        or history.incomplete_count != 0
        or selected.ordinal != 2
        or selected.implementation_epoch != EPOCH_8_HISTORICAL_SELECTED_EPOCH
        or selected.implementation_commit != EPOCH_8_HISTORICAL_SELECTED_COMMIT
        or selected.started_sha256 != EPOCH_8_SELECTED_STARTED_SHA256
        or len(selected.started_bytes) != EPOCH_8_SELECTED_STARTED_BYTES
        or selected.candidate_sha256 != EPOCH_8_SELECTED_CANDIDATE_SHA256
        or len(selected.candidate_bytes) != EPOCH_8_SELECTED_CANDIDATE_BYTES
        or selected.terminal_sha256 != EPOCH_8_SELECTED_TERMINAL_SHA256
        or len(selected.terminal_bytes) != EPOCH_8_SELECTED_TERMINAL_BYTES
        or history.history_root_sha256 != EPOCH_8_SEALED_HISTORY_ROOT_SHA256
        or history.live_ledger_root_sha256 != EPOCH_8_SEALED_LIVE_LEDGER_ROOT_SHA256
        or selected.evidence_tree_root_sha256 != EPOCH_8_SELECTED_EVIDENCE_ROOT_SHA256
        or control.merkle_root_sha256 != EPOCH_8_HISTORICAL_CONTROL_ROOT_SHA256
        or candidate.get("candidate_content_root_sha256")
        != EPOCH_8_SELECTED_CANDIDATE_CONTENT_ROOT_SHA256
        or candidate.get("run_a_root_sha256") != EPOCH_8_SELECTED_RUN_ROOT_SHA256
        or candidate.get("run_b_root_sha256") != EPOCH_8_SELECTED_RUN_ROOT_SHA256
    ):
        raise RehearsalV22Error("registered historical selected anchor drifted")
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
        candidate_content_root_sha256=cast(str, candidate["candidate_content_root_sha256"]),
        run_a_root_sha256=cast(str, candidate["run_a_root_sha256"]),
        run_b_root_sha256=cast(str, candidate["run_b_root_sha256"]),
        selected_git_blob_sha256=dict(sorted(selected_git_blobs.items())),
        require_current=False,
    )


def _history_census_specs(
    binding: ExecutionBinding,
    history: HistoryValidation,
) -> tuple[AuthorityCensusSpec, ...]:
    specs: list[AuthorityCensusSpec] = []
    for record in history.records:
        action = _validate_action_authorization(
            binding,
            record.owner_action_time_authorization,
            expected_ordinal=record.ordinal,
            expected_previous_history_root_sha256=record.previous_history_root_sha256,
            require_current_process=False,
            historical_execution_head=record.owner_action_time_authorization.creating_commit,
        )
        specs.extend(
            (
                AuthorityCensusSpec(
                    record.owner_action_time_authorization,
                    "PINNED_SOURCE",
                    None,
                ),
                AuthorityCensusSpec(
                    action.owner_surface_authorization,
                    "PINNED_SOURCE",
                    None,
                ),
                AuthorityCensusSpec(
                    action.independent_implementation_review,
                    "PINNED_LANDING_PROJECTION",
                    action.independent_implementation_review.creating_commit,
                ),
            )
        )
    return tuple(specs)


def _census_reference(census: Mapping[str, Any]) -> JsonObject:
    payload = _canonical_json_bytes(dict(census))
    return {
        "schema_version": census.get("schema_version"),
        "execution_head": census.get("execution_head"),
        "reference_count": census.get("reference_count"),
        "row_count": census.get("row_count"),
        "projection_count": census.get("projection_count"),
        "invalid_count": census.get("invalid_count"),
        "canonical_json_sha256": _sha256(payload),
        "bytes": len(payload),
        "result": census.get("status"),
        "all_references_revalidated_at_start": True,
    }


def _assert_git_census_state_unchanged(
    project_root: Path,
    *,
    expected_refs: bytes,
    expected_head: str,
) -> None:
    observed_refs, _observed_sha, _observed_count = _git_ref_snapshot(project_root)
    observed_head = _current_execution_head(project_root)
    if observed_refs != expected_refs or observed_head != expected_head:
        raise RehearsalV22Error(
            "Git refs or HEAD changed between recovery census and live-anchor validation"
        )


def _live_execution_census_specs(
    execution_epoch: Mapping[str, Any],
    additional_references: Sequence[AuthorityCensusSpec | AuthorityReference],
) -> tuple[AuthorityCensusSpec | AuthorityReference, ...]:
    owner = _validate_authority_ref_shape(
        execution_epoch.get("owner_exact_surface_authorization"),
        "live execution census owner authority",
    )
    review = _validate_authority_ref_shape(
        execution_epoch.get("independent_implementation_review"),
        "live execution census independent review",
    )
    landing = _validate_authority_ref_shape(
        execution_epoch.get("landing_report"),
        "live execution census landing report",
    )
    merge_commit = execution_epoch.get("merge_commit")
    if not _lower_hex(merge_commit, 40):
        raise RehearsalV22Error("live execution census merge commit is malformed")
    return (
        AuthorityCensusSpec(owner, "PINNED_SOURCE", None),
        AuthorityCensusSpec(
            review,
            "PINNED_LANDING_PROJECTION",
            cast(str, merge_commit),
        ),
        AuthorityCensusSpec(landing, "PINNED_SOURCE", None),
        *additional_references,
    )


def _validate_epoch_8_landing_authority(
    project_root: Path,
    *,
    execution_head: str,
    implementation_commit: str,
    owner: AuthorityReference,
    review: AuthorityReference,
    landing_report: AuthorityReference,
    control: ControlSurface,
) -> str:
    """Prove the explicit epoch-8 review projection and landing authority."""

    root = project_root.absolute()
    if (
        owner
        != AuthorityReference(
            EPOCH_8_SURFACE_AUTHORITY_RELATIVE.as_posix(),
            EPOCH_8_SURFACE_AUTHORITY_SHA256,
            EPOCH_8_SURFACE_AUTHORITY_COMMIT,
        )
        or not landing_report.path.startswith("docs/phase4/reports/")
        or not landing_report.path.endswith(".json")
    ):
        raise RehearsalV22Error("epoch-8 landing owner or registered path drifted")
    merge_commit = _git_commit(
        root,
        review.creating_commit,
        "epoch-8 landing merge commit",
    )
    review_parents = _git_parents_epoch_7(root, review.creating_commit)
    landing_parents = _git_parents_epoch_7(root, landing_report.creating_commit)
    review_payload = _git_blob(root, review.creating_commit, review.path)
    landing_payload = _git_blob(root, landing_report.creating_commit, landing_report.path)
    review_document = _object(
        strict_json_loads(review_payload, source="epoch-8 independent implementation review"),
        "epoch-8 independent implementation review",
    )
    landing_document = _object(
        strict_json_loads(landing_payload, source="epoch-8 landing report"),
        "epoch-8 landing report",
    )
    if (
        len(review_parents) != 2
        or _git_parents_epoch_7(root, review_parents[1]) != (implementation_commit,)
        or _git_blob(root, review_parents[1], review.path) != review_payload
        or _git_bytes(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            review_parents[1],
            merge_commit,
            "--",
            review.path,
        )
        or _sha256(review_payload) != review.sha256
        or not isinstance(review_document.get("verdict"), str)
        or not cast(str, review_document["verdict"]).startswith("APPROVE")
        or landing_parents != (merge_commit,)
        or _sha256(landing_payload) != landing_report.sha256
        or not _document_mentions_commit(landing_document, implementation_commit)
        or not _document_mentions_commit(landing_document, review.creating_commit)
        or not _document_mentions_commit(landing_document, review_parents[0])
        or not _document_mentions_commit(landing_document, review_parents[1])
        or not _document_mentions_commit(landing_document, owner.creating_commit)
        or not _document_mentions_commit(landing_document, owner.sha256)
        or not _document_mentions_commit(landing_document, review.sha256)
        or not _document_mentions_commit(landing_document, control.merkle_root_sha256)
        or not _document_mentions_commit(landing_document, len(control.records))
        or not _git_is_ancestor(root, landing_report.creating_commit, execution_head)
    ):
        raise RehearsalV22Error("epoch-8 review/merge/landing topology or binding drifted")
    return merge_commit


def _live_execution_anchor_with_census(
    binding: ExecutionBinding,
    execution_epoch: Mapping[str, Any],
    *,
    additional_references: Sequence[AuthorityCensusSpec | AuthorityReference] = (),
    work_tracker: _RecoveryWorkTracker | None = None,
) -> tuple[LiveExecutionAnchor, JsonObject]:
    """Prove latest landed epoch-8 governance and every current executing byte."""

    root = binding.project_root.absolute()
    execution_head = _current_execution_head(root)
    validate_epoch_8_recovery_contract(root, execution_head=execution_head)
    epoch = execution_epoch.get("epoch")
    implementation_commit = execution_epoch.get("implementation_commit")
    if epoch != EPOCH_8_IMPLEMENTATION_EPOCH or isinstance(epoch, bool):
        raise RehearsalV22Error("live execution epoch is not exact epoch 8")
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
    declared_merge_commit = _git_commit(
        root,
        execution_epoch.get("merge_commit"),
        "live execution merge commit",
    )
    control = build_control_surface(root, commit, require_current=True)
    if (
        owner
        != AuthorityReference(
            EPOCH_8_SURFACE_AUTHORITY_RELATIVE.as_posix(),
            EPOCH_8_SURFACE_AUTHORITY_SHA256,
            EPOCH_8_SURFACE_AUTHORITY_COMMIT,
        )
        or execution_epoch.get("control_merkle_root_sha256") != control.merkle_root_sha256
        or execution_epoch.get("control_record_count") != len(control.records)
        or execution_epoch.get("latest_complete_landed_epoch_required") is not True
        or execution_epoch.get("current_control_bytes_required") is not True
        or execution_epoch.get("loaded_module_bytes_required") is not True
    ):
        raise RehearsalV22Error("live execution control or authority binding drifted")
    validated = validate_implementation_epoch(
        root,
        epoch=EPOCH_8_IMPLEMENTATION_EPOCH,
        implementation_commit=commit,
        owner_surface_authorization=owner,
        independent_review=review,
        control_merkle_root_sha256=control.merkle_root_sha256,
        execution_head=execution_head,
        require_current_bytes=True,
    )
    if validated.implementation_commit != commit:
        raise RehearsalV22Error("live execution epoch validation returned another commit")
    merge_commit = _validate_epoch_8_landing_authority(
        root,
        execution_head=execution_head,
        implementation_commit=commit,
        owner=owner,
        review=review,
        landing_report=landing_report,
        control=control,
    )
    if merge_commit != declared_merge_commit:
        raise RehearsalV22Error("live execution landing merge declaration drifted")
    refs_before_census, _refs_sha, _refs_count = _git_ref_snapshot(root)
    tracker = _RecoveryWorkTracker() if work_tracker is None else work_tracker
    census = _real_lineage_census(
        root,
        execution_head=execution_head,
        additional_references=_live_execution_census_specs(
            execution_epoch,
            additional_references,
        ),
        work_tracker=tracker,
    )
    census_reference = _object(
        execution_epoch.get("real_lineage_census"),
        "live execution baseline lineage census reference",
    )
    if (
        set(census_reference) != RECOVERY_CENSUS_REFERENCE_FIELDS
        or census_reference.get("schema_version") != EPOCH_8_LINEAGE_CENSUS_SCHEMA
        or census_reference.get("result") != "PASS_REAL_LINEAGE_CENSUS"
        or census_reference.get("invalid_count") != 0
        or census_reference.get("all_references_revalidated_at_start") is not True
        or not _lower_hex(census_reference.get("execution_head"), 40)
        or not _git_is_ancestor(
            root,
            cast(str, census_reference.get("execution_head")),
            execution_head,
        )
    ):
        raise RehearsalV22Error("live execution baseline census binding drifted")
    loaded_module_sha256: dict[str, str] = {}
    for relative in (IMPLEMENTATION_RELATIVE, VALIDATOR_RELATIVE):
        payload = validate_implementation_blob(
            root,
            commit,
            relative.as_posix(),
            require_current=True,
        )
        loaded_module_sha256[relative.as_posix()] = _sha256(payload)
    anchor = LiveExecutionAnchor(
        execution_epoch=EPOCH_8_IMPLEMENTATION_EPOCH,
        implementation_commit=commit,
        owner_surface_authorization=owner,
        independent_implementation_review=review,
        merge_commit=merge_commit,
        landing_report=landing_report,
        control_surface=control,
        execution_head=execution_head,
        loaded_module_sha256=loaded_module_sha256,
        real_lineage_census_sha256=cast(str, _census_reference(census)["canonical_json_sha256"]),
        require_current=True,
    )
    _assert_git_census_state_unchanged(
        root,
        expected_refs=refs_before_census,
        expected_head=execution_head,
    )
    return anchor, census


def _live_execution_anchor(
    binding: ExecutionBinding,
    execution_epoch: Mapping[str, Any],
    *,
    additional_references: Sequence[AuthorityCensusSpec | AuthorityReference] = (),
) -> LiveExecutionAnchor:
    anchor, _census = _live_execution_anchor_with_census(
        binding,
        execution_epoch,
        additional_references=additional_references,
    )
    return anchor


def _revalidate_cached_current_control_surface(
    project_root: Path,
    *,
    implementation_commit: str,
    execution_head: str,
    ref_snapshot_sha256: str,
    lineage_census_sha256: str,
    pass_nonce: object,
    cache: _ControlSurfaceCacheEnvelope,
) -> None:
    """Re-read mutable inputs from a deep-immutable, single-pass fact set."""

    root = project_root.resolve(strict=True)
    _validate_control_surface_cache_integrity(
        root,
        implementation_commit=implementation_commit,
        execution_head=execution_head,
        ref_snapshot_sha256=ref_snapshot_sha256,
        lineage_census_sha256=lineage_census_sha256,
        pass_nonce=pass_nonce,
        cache=cache,
    )
    for record in cache.records:
        if not record.current_byte_required:
            continue
        relative = cast(str, record.repository_path)
        payload = _regular_bytes(
            _safe_path(root, relative, f"cached current control {relative}"),
            f"cached current control {relative}",
        )
        if len(payload) != record.byte_count or _sha256(payload) != record.sha256:
            raise RehearsalV22Error(f"cached current control bytes drifted: {relative}")
    if tuple(sorted(_classify_loaded_module_origins(root))) != tuple(
        cache.loaded_repository_sources
    ):
        raise RehearsalV22Error("cached live loaded-source inventory drifted")
    python_payload, package_payload = _runtime_inventory(root)
    if (
        len(python_payload) != cache.python_inventory_bytes
        or _sha256(python_payload) != cache.python_inventory_sha256
        or len(package_payload) != cache.package_inventory_bytes
        or _sha256(package_payload) != cache.package_inventory_sha256
    ):
        raise RehearsalV22Error("cached live runtime inventory drifted")


def _validate_live_execution_publication_guard(
    binding: ExecutionBinding,
    *,
    authorization: BundleRecoveryAuthorization,
    live_anchor: LiveExecutionAnchor,
    start_census: Mapping[str, Any],
    expected_execution_epoch_payload: bytes,
    control_pass_nonce: object,
    control_cache: _ControlSurfaceCacheEnvelope,
) -> None:
    """Recheck every mutable live byte after validation without replaying lineage."""

    root = binding.project_root.absolute()
    if _canonical_json_bytes(dict(authorization.execution_epoch)) != (
        expected_execution_epoch_payload
    ):
        raise RehearsalV22Error("recovery execution epoch mutated before publication")
    census_reference = _census_reference(start_census)
    observed_refs, observed_refs_sha, _observed_refs_count = _git_ref_snapshot(root)
    if (
        _current_execution_head(root) != live_anchor.execution_head
        or observed_refs_sha != start_census.get("ref_snapshot_before_sha256")
        or observed_refs_sha != start_census.get("ref_snapshot_after_sha256")
        or census_reference.get("canonical_json_sha256") != live_anchor.real_lineage_census_sha256
    ):
        raise RehearsalV22Error("Git refs, HEAD, or recovery census changed before publication")
    validate_epoch_8_recovery_contract(root, execution_head=live_anchor.execution_head)
    _revalidate_cached_current_control_surface(
        root,
        implementation_commit=live_anchor.implementation_commit,
        execution_head=live_anchor.execution_head,
        ref_snapshot_sha256=cast(str, start_census.get("ref_snapshot_after_sha256")),
        lineage_census_sha256=live_anchor.real_lineage_census_sha256,
        pass_nonce=control_pass_nonce,
        cache=control_cache,
    )
    if control_cache.merkle_root_sha256 != live_anchor.control_surface.merkle_root_sha256:
        raise RehearsalV22Error("current control cache belongs to another live anchor")
    expected_modules = dict(live_anchor.loaded_module_sha256)
    if set(expected_modules) != {
        IMPLEMENTATION_RELATIVE.as_posix(),
        VALIDATOR_RELATIVE.as_posix(),
    }:
        raise RehearsalV22Error("live module binding changed before recovery publication")
    for relative, expected_sha256 in expected_modules.items():
        payload = validate_implementation_blob(
            root,
            live_anchor.implementation_commit,
            relative,
            require_current=True,
        )
        if _sha256(payload) != expected_sha256:
            raise RehearsalV22Error("loaded module bytes changed before recovery publication")
    rows = _array(start_census.get("rows"), "recovery publication census rows")
    for row in rows:
        authority = _object(row, "recovery publication census row")
        relative = _relative_text(
            authority.get("path"),
            "recovery publication census path",
        )
        worktree_sha256 = authority.get("worktree_sha256")
        if not _lower_hex(worktree_sha256, 64) or _sha256(
            _regular_bytes(root / relative, f"recovery publication authority {relative}")
        ) != cast(str, worktree_sha256):
            raise RehearsalV22Error("authority worktree bytes changed before recovery publication")
    final_refs, _final_refs_sha, _final_refs_count = _git_ref_snapshot(root)
    _assert_git_census_state_unchanged(
        root,
        expected_refs=observed_refs,
        expected_head=live_anchor.execution_head,
    )
    if final_refs != observed_refs:
        raise RehearsalV22Error("Git refs changed during recovery publication guard")


def _registered_recovery_storage(
    binding: ExecutionBinding,
    authorization: BundleRecoveryAuthorization,
    *,
    expected_state: Literal["PRECLAIM_EMPTY", "PUBLISHED_SUCCESS"],
    preflight_storage: Mapping[str, Any] | None = None,
) -> _RecoveryStoragePaths:
    """Validate the owner containers in one explicit, non-fallback state."""

    storage = _object(
        _object(authorization.destination, "recovery destination").get("recovery_storage"),
        "registered recovery storage",
    )
    primary = Path(cast(str, storage.get("primary_recovery_container"))).absolute()
    secondary = Path(cast(str, storage.get("secondary_recovery_container"))).absolute()
    _registered_storage_directory(primary, "primary bundle-recovery container")
    _registered_storage_directory(secondary, "secondary bundle-recovery container")
    if preflight_storage is not None:
        recorded_primary = _exact_contract_object(
            preflight_storage.get("primary_container"),
            frozenset(
                {
                    "path",
                    "owner_uid",
                    "device",
                    "inode",
                    "mode_octal",
                    "non_symlink",
                    "canonical_unaliased",
                }
            ),
            "recorded primary recovery container",
        )
        recorded_secondary = _exact_contract_object(
            preflight_storage.get("secondary_container"),
            frozenset(
                {
                    "path",
                    "owner_uid",
                    "device",
                    "inode",
                    "mode_octal",
                    "non_symlink",
                    "canonical_unaliased",
                }
            ),
            "recorded secondary recovery container",
        )
        if (
            recorded_primary
            != _storage_directory_evidence(primary, "primary bundle-recovery container")
            or recorded_secondary
            != _storage_directory_evidence(secondary, "secondary bundle-recovery container")
        ):
            raise RehearsalV22Error(
                "recovery container identity differs from the registered preflight"
            )
    protected = (
        binding.project_root,
        binding.destination,
        binding.primary_series_container,
        binding.secondary_series_container,
        binding.ledger_root,
        binding.primary_receipt_root,
        binding.secondary_snapshot_root,
        binding.secondary_receipt_root,
        PROTECTED_HELDOUT_ROOT,
    )
    if (
        primary == secondary
        or (primary.stat().st_dev, primary.stat().st_ino)
        == (secondary.stat().st_dev, secondary.stat().st_ino)
        or primary.is_relative_to(secondary)
        or secondary.is_relative_to(primary)
        or any(
            candidate == guarded
            or candidate.is_relative_to(guarded)
            or guarded.is_relative_to(candidate)
            for candidate in (primary, secondary)
            for guarded in protected
        )
    ):
        raise RehearsalV22Error("recovery containers overlap or reuse protected state")
    digest = authorization.sha256
    claim = primary / f"CLAIM-{digest}"
    destination_stage = binding.destination.parent / (
        f".{binding.destination.name}.recovery-stage-{digest}"
    )
    secondary_stage = secondary / f".bundle-snapshot-stage-{digest}"
    if expected_state == "PRECLAIM_EMPTY":
        if (
            tuple(primary.iterdir())
            or tuple(secondary.iterdir())
            or any(
                os.path.lexists(path)
                for path in (binding.destination, claim, destination_stage, secondary_stage)
            )
        ):
            raise RehearsalV22Error("recovery preclaim storage is not exactly empty")
    elif expected_state == "PUBLISHED_SUCCESS":
        if (
            not binding.destination.is_dir()
            or binding.destination.is_symlink()
            or not claim.is_dir()
            or claim.is_symlink()
            or os.path.lexists(destination_stage)
            or os.path.lexists(secondary_stage)
        ):
            raise RehearsalV22Error("published recovery storage is partial or aliased")
    else:
        raise RehearsalV22Error("recovery storage expected state is unregistered")
    if destination_stage.parent.stat().st_dev != binding.destination.parent.stat().st_dev:
        raise RehearsalV22Error("recovery destination stage is not on the destination filesystem")
    return _RecoveryStoragePaths(
        primary_container=primary,
        secondary_container=secondary,
        claim_root=claim,
        destination_stage=destination_stage,
        secondary_snapshot_stage=secondary_stage,
        secondary_snapshot_prefix=f"RECOVERED-BUNDLE-{digest}-",
        receipt_prefix=f"recovery-{digest}-",
    )


def _sealed_recovery_fingerprints(
    binding: ExecutionBinding,
    history: HistoryValidation,
) -> tuple[dict[str, str], dict[str, int]]:
    snapshot, primary_receipt, secondary_receipt = _final_mirror_targets(binding, history)
    roots = {
        "active_ledger": binding.ledger_root,
        "through_ordinal_2_snapshot": snapshot,
        "primary_seal_receipt": primary_receipt,
        "secondary_seal_receipt": secondary_receipt,
    }
    fingerprints: dict[str, str] = {}
    recursive_bytes = 0
    files_visited = 0
    for label, path in roots.items():
        if not os.path.lexists(path):
            raise RehearsalV22Error(f"sealed recovery input is absent: {label}")
        tree, observed_bytes, observed_files = _tree_fingerprint_with_work(path)
        fingerprints[label] = _sha256(_canonical_json_bytes(tree))
        recursive_bytes += observed_bytes
        files_visited += observed_files
    counters = {
        "git_objects_read": 0,
        "recursive_bytes_hashed": recursive_bytes,
        "sealed_snapshot_files_visited": files_visited,
        "bundle_bytes_copied": 0,
    }
    _assert_recovery_work_bound(counters)
    return fingerprints, counters


def _validate_sealed_recovery_inputs(
    binding: ExecutionBinding,
    authorization: BundleRecoveryAuthorization,
    owner_binding: RecoveryOwnerBinding,
    storage: _RecoveryStoragePaths,
) -> tuple[
    HistoryValidation,
    HistoricalSelectedAnchor,
    Mapping[str, str],
    Mapping[str, int],
]:
    """Cross-check R/B against the sealed ledger and final mirrored snapshot."""

    if owner_binding.recovery_authorization != authorization.authority_ref(binding.project_root):
        raise RehearsalV22Error("sealed recovery inputs are bound by another R")
    history = validate_live_history(binding, historical_authority_bytes=True)
    receipts = _validate_second_copy_history(
        binding,
        history,
        historical_authority_bytes=True,
    )
    if (
        len(history.records) != 2
        or history.started_count != 2
        or history.failed_count != 1
        or history.incomplete_count != 0
        or history.validated_candidate_count != 1
        or history.selected_attempt_ordinal != 2
        or not history.series_closed
        or history.live_ledger_root_sha256 is None
        or len(receipts) != 2
    ):
        raise RehearsalV22Error("sealed recovery requires the exact closed [5,6] history")
    selected = history.records[1]
    if selected.candidate_bytes is None or selected.terminal_bytes is None:
        raise RehearsalV22Error("sealed selected attempt lacks candidate or terminal bytes")
    candidate = _object(
        strict_json_loads(selected.candidate_bytes, source="sealed selected candidate"),
        "sealed selected candidate",
    )
    historical_base = _historical_selected_anchor(binding, history)
    historical = HistoricalSelectedAnchor(
        selected_epoch=historical_base.selected_epoch,
        selected_commit=historical_base.selected_commit,
        owner_action_time_authorization=historical_base.owner_action_time_authorization,
        owner_surface_authorization=historical_base.owner_surface_authorization,
        independent_implementation_review=historical_base.independent_implementation_review,
        control_surface=historical_base.control_surface,
        history_root_sha256=historical_base.history_root_sha256,
        live_ledger_root_sha256=historical_base.live_ledger_root_sha256,
        evidence_tree_root_sha256=historical_base.evidence_tree_root_sha256,
        candidate_content_root_sha256=historical_base.candidate_content_root_sha256,
        run_a_root_sha256=historical_base.run_a_root_sha256,
        run_b_root_sha256=historical_base.run_b_root_sha256,
        selected_git_blob_sha256=historical_base.selected_git_blob_sha256,
        require_current=False,
    )
    sealed = _object(authorization.sealed_series, "recovery sealed-series binding")
    expected_files = {
        "started": {
            "relative_path": "attempts/000002/started.json",
            "sha256": selected.started_sha256,
            "bytes": len(selected.started_bytes),
        },
        "candidate": {
            "relative_path": "attempts/000002/candidate.json",
            "sha256": selected.candidate_sha256,
            "bytes": len(selected.candidate_bytes),
        },
        "terminal": {
            "relative_path": "attempts/000002/terminal.json",
            "sha256": selected.terminal_sha256,
            "bytes": len(selected.terminal_bytes),
        },
    }
    expected_values = {
        "series_id": REHEARSAL_ID,
        "series_token_sha256": binding.series_token_sha256,
        "ledger_root": binding.ledger_root.as_posix(),
        "history_root_sha256": history.history_root_sha256,
        "live_ledger_root_sha256": history.live_ledger_root_sha256,
        "series_closed": True,
        "started_count": history.started_count,
        "failed_count": history.failed_count,
        "incomplete_count": history.incomplete_count,
        "validated_candidate_count": history.validated_candidate_count,
        "selected_attempt_ordinal": selected.ordinal,
        "selected_implementation_epoch": selected.implementation_epoch,
        "selected_implementation_commit": selected.implementation_commit,
        "selected_control_merkle_root_sha256": candidate.get("control_surface_root_sha256"),
        "selected_evidence_tree_root_sha256": selected.evidence_tree_root_sha256,
        "selected_candidate_content_root_sha256": candidate.get("candidate_content_root_sha256"),
        "selected_run_a_root_sha256": candidate.get("run_a_root_sha256"),
        "selected_run_b_root_sha256": candidate.get("run_b_root_sha256"),
        "selected_terminal_outcome": selected.outcome,
        "selected_reached_stage": selected.reached_stage,
        "automatic_retry_count": 0,
        "selected_files": expected_files,
    }
    if any(sealed.get(key) != value for key, value in expected_values.items()):
        raise RehearsalV22Error("R sealed-series values differ from live immutable history")
    snapshot, primary_receipt, secondary_receipt = _final_mirror_targets(binding, history)
    latest_receipt = receipts[-1]
    receipt_payload = _regular_bytes(primary_receipt, "final primary sealed mirror receipt")
    secondary_receipt_payload = _regular_bytes(
        secondary_receipt,
        "final secondary sealed mirror receipt",
    )
    sealed_mirror = _object(sealed.get("sealed_mirror"), "R sealed mirror binding")
    expected_mirror = {
        "snapshot_count": 2,
        "receipt_count": 2,
        "latest_ordinal": 2,
        "latest_snapshot_path": snapshot.as_posix(),
        "primary_receipt_path": primary_receipt.as_posix(),
        "secondary_receipt_path": secondary_receipt.as_posix(),
        "receipt_sha256": _sha256(receipt_payload),
        "receipt_bytes": len(receipt_payload),
        "inventory_sha256": latest_receipt.get("primary_inventory_sha256"),
        "file_count": latest_receipt.get("file_count"),
        "total_bytes": latest_receipt.get("total_bytes"),
        "paired_receipts_byte_identical": True,
    }
    if (
        receipt_payload != secondary_receipt_payload
        or sealed_mirror != expected_mirror
        or snapshot.is_symlink()
        or not snapshot.is_dir()
        or storage.claim_root.parent != storage.primary_container
    ):
        raise RehearsalV22Error("R sealed mirror paths or bytes differ from the final snapshot")
    fingerprints, counters = _sealed_recovery_fingerprints(binding, history)
    return history, historical, fingerprints, counters


def _cross_bind_epoch_8_preflight_inputs_live(
    binding: ExecutionBinding,
    owner_binding: RecoveryOwnerBinding,
    *,
    history: HistoryValidation,
    fingerprints: Mapping[str, str],
    counters: Mapping[str, int],
) -> None:
    """Re-measure immutable preflight inputs only after identity and census checks."""

    if (
        owner_binding.series_2_registered_storage_preflight is None
        or owner_binding.sealed_recovery_inputs_preflight is None
    ):
        raise RehearsalV22Error("recovery owner binding omitted Q preflight input records")
    recorded_series = _object(
        owner_binding.series_2_registered_storage_preflight,
        "recorded Q series-2 storage",
    )
    if (
        recorded_series.get("primary_container")
        != _storage_directory_evidence(
            binding.primary_series_container,
            "series-2 primary container",
        )
        or recorded_series.get("secondary_container")
        != _storage_directory_evidence(
            binding.secondary_series_container,
            "series-2 secondary container",
        )
        or recorded_series.get("registered_leaf_state")
        != {
            "primary_ledger": "PRESENT_VERIFIED",
            "primary_receipts": "PRESENT_VERIFIED",
            "secondary_receipts": "PRESENT_VERIFIED",
            "secondary_snapshots": "PRESENT_VERIFIED",
        }
        or recorded_series.get("mirrored_history")
        != {
            "attempt_count": len(history.records),
            "history_root_sha256": history.history_root_sha256,
            "live_ledger_root_sha256": history.live_ledger_root_sha256,
            "receipt_count": 2,
            "series_closed": history.series_closed,
        }
    ):
        raise RehearsalV22Error("live series-2 storage differs from the Q preflight")
    recorded_sealed = _object(
        owner_binding.sealed_recovery_inputs_preflight,
        "recorded Q sealed recovery inputs",
    )
    if (
        recorded_sealed.get("series_closed") != history.series_closed
        or recorded_sealed.get("record_count") != len(history.records)
        or recorded_sealed.get("selected_attempt_ordinal")
        != history.selected_attempt_ordinal
        or recorded_sealed.get("history_root_sha256") != history.history_root_sha256
        or recorded_sealed.get("live_ledger_root_sha256")
        != history.live_ledger_root_sha256
        or recorded_sealed.get("mirror_receipt_count") != 2
        or recorded_sealed.get("sealed_input_fingerprints") != dict(fingerprints)
        or recorded_sealed.get("work_counters") != dict(counters)
        or recorded_sealed.get("ledger_and_mirror_read_only") is not True
    ):
        raise RehearsalV22Error("live sealed inputs differ from the Q preflight")


def _validate_recovery_qrb_census_delta(
    project_root: Path,
    authorization: BundleRecoveryAuthorization,
    owner_binding: RecoveryOwnerBinding,
    start_census: Mapping[str, Any],
) -> None:
    root = project_root.absolute()
    baseline = cast(
        str,
        _object(
            authorization.execution_epoch.get("real_lineage_census"),
            "recovery baseline census",
        ).get("execution_head"),
    )
    current = _current_execution_head(root)
    expected = (
        owner_binding.review_request.creating_commit,
        authorization.creating_commit,
        owner_binding.creating_commit,
    )
    observed = tuple(
        _git_bytes(root, "rev-list", "--first-parent", "--reverse", f"{baseline}..{current}", "--")
        .decode("ascii", errors="strict")
        .splitlines()
    )
    if observed != expected or current != owner_binding.creating_commit:
        raise RehearsalV22Error("preflight-to-start Git delta is not exact linear Q/R/B")
    for commit, reference in zip(
        expected,
        (
            owner_binding.review_request,
            authorization.authority_ref(root),
            owner_binding.authority_ref(root),
        ),
        strict=True,
    ):
        parents = _git_parents_epoch_7(root, commit)
        surface = _parse_name_status(
            _git_bytes(
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-status",
                "--no-renames",
                parents[0],
                commit,
                "--",
            )
        )
        if len(parents) != 1 or surface != {reference.path: "A"}:
            raise RehearsalV22Error("recovery Q/R/B is not one exact unique-A commit")
    q_payload = _git_blob(
        root,
        owner_binding.review_request.creating_commit,
        owner_binding.review_request.path,
    )
    q = _object(strict_json_loads(q_payload, source="recovery Q census delta"), "recovery Q")
    q_preflight = _object(
        q.get("registered_read_only_recovery_preflight"),
        "recovery Q embedded preflight",
    )
    stdout_text = q_preflight.get("stdout_canonical_json")
    if not isinstance(stdout_text, str):
        raise RehearsalV22Error("recovery Q omits embedded preflight stdout")
    stdout_payload = stdout_text.encode("utf-8")
    if _sha256(stdout_payload) != q_preflight.get("stdout_sha256") or len(
        stdout_payload
    ) != q_preflight.get("stdout_bytes"):
        raise RehearsalV22Error("recovery Q embedded preflight bytes drifted")
    stdout_document = _object(
        strict_json_loads(stdout_payload, source="recovery Q embedded preflight stdout"),
        "recovery Q embedded preflight stdout",
    )
    if _canonical_json_bytes(stdout_document) != stdout_payload:
        raise RehearsalV22Error("recovery Q embedded preflight is not canonical")
    baseline_census = _object(
        stdout_document.get("real_lineage_census"),
        "recovery baseline full lineage census",
    )
    if (
        set(baseline_census) != set(RECOVERY_CENSUS_FIELD_ORDER)
        or baseline_census.get("execution_head") != baseline
        or baseline_census.get("status") != "PASS_REAL_LINEAGE_CENSUS"
        or baseline_census.get("invalid_count") != 0
        or _census_reference(baseline_census)
        != authorization.execution_epoch.get("real_lineage_census")
        or set(start_census) != set(RECOVERY_CENSUS_FIELD_ORDER)
    ):
        raise RehearsalV22Error("recovery baseline or start census binding drifted")
    qrb_paths = {
        owner_binding.review_request.path,
        authorization.authority_ref(root).path,
        owner_binding.authority_ref(root).path,
    }
    baseline_rows = {
        cast(str, row["path"]): row
        for row in _array(baseline_census.get("rows"), "baseline census rows")
    }
    start_rows = {
        cast(str, row["path"]): row for row in _array(start_census.get("rows"), "start census rows")
    }
    if (
        set(start_rows) - qrb_paths != set(baseline_rows)
        or any(start_rows[path] != row for path, row in baseline_rows.items())
        or qrb_paths - set(start_rows)
    ):
        raise RehearsalV22Error("start census changed a baseline row or omitted Q/R/B")
    current_ref_payload, _current_ref_sha, _current_ref_count = _git_ref_snapshot(root)
    expected_baseline_rows: list[bytes] = []
    changed_refs: list[str] = []
    for raw in current_ref_payload.splitlines():
        ref_raw, separator, object_raw = raw.partition(b"\0")
        if separator != b"\0":
            raise RehearsalV22Error("current ref snapshot row is malformed")
        ref = ref_raw.decode("utf-8", errors="strict")
        if ref in {"refs/heads/main", "refs/remotes/origin/main"}:
            if object_raw.decode("ascii", errors="strict") != current:
                raise RehearsalV22Error("main or origin/main is not at recovery B")
            changed_refs.append(ref)
            object_raw = baseline.encode("ascii")
        expected_baseline_rows.append(ref_raw + b"\0" + object_raw)
    expected_baseline_payload = b"\n".join(expected_baseline_rows)
    if current_ref_payload.endswith(b"\n"):
        expected_baseline_payload += b"\n"
    if (
        changed_refs != ["refs/heads/main", "refs/remotes/origin/main"]
        or _sha256(expected_baseline_payload) != baseline_census.get("ref_snapshot_before_sha256")
        or baseline_census.get("ref_snapshot_before_sha256")
        != baseline_census.get("ref_snapshot_after_sha256")
    ):
        raise RehearsalV22Error("Git ref delta is not only main/origin-main Q/R/B advancement")


def _recovered_release_census_specs(
    binding: ExecutionBinding,
    receipt_path: Path,
    *,
    execution_head: str,
    work_tracker: _RecoveryWorkTracker,
) -> tuple[AuthorityCensusSpec, ...]:
    """Derive every authority consumed by the fixed recovered-release receipt."""

    root = binding.project_root.absolute()
    all_ref_commits = _git_all_ref_commits(root, work_tracker=work_tracker)
    expected_receipt = root / RELEASE_RELATIVE
    if receipt_path.absolute() != expected_receipt:
        raise RehearsalV22Error("recovered release receipt path is not RELEASE_RELATIVE")
    payload = _regular_bytes(expected_receipt, "recovered release census receipt")
    document = _object(
        strict_json_loads(payload, source="recovered release census receipt"),
        "recovered release census receipt",
    )
    if _canonical_json_bytes(document) != payload:
        raise RehearsalV22Error("recovered release receipt is not canonical JSON")
    schema = _release_schema(root)
    schema_properties = _object(
        schema.get("properties"),
        "recovered release schema properties",
    )
    schema_version = _object(
        schema_properties.get("schema_version"),
        "recovered release schema-version property",
    ).get("const")
    if not isinstance(schema_version, str) or document.get("schema_version") != schema_version:
        raise RehearsalV22Error("recovered release schema version drifted")

    relative = RELEASE_RELATIVE.as_posix()
    touches = _all_ref_path_touches(
        root,
        relative,
        all_ref_commit_count=len(all_ref_commits),
        work_tracker=work_tracker,
    )
    direct_sources = [
        commit
        for commit, status, paths in touches
        if status == "A"
        and paths == (relative,)
        and len(_git_parents_epoch_7(root, commit, work_tracker=work_tracker)) == 1
        and _git_optional_blob_epoch_7(
            root,
            _git_parents_epoch_7(root, commit, work_tracker=work_tracker)[0],
            relative,
            work_tracker=work_tracker,
        )
        is None
    ]
    if len(direct_sources) != 1:
        raise RehearsalV22Error("recovered release receipt source is ambiguous")
    receipt_spec = AuthorityCensusSpec(
        AuthorityReference(relative, _sha256(payload), direct_sources[0]),
        "DISCOVER_SOURCE_AFTER_PROJECTIONS",
        None,
    )
    lineage = _object(document.get("lineage"), "recovered release census lineage")
    authority_specs = tuple(
        AuthorityCensusSpec(
            _validate_authority_ref_shape(
                lineage.get(key),
                f"recovered release census {key}",
            ),
            "PINNED_SOURCE",
            None,
        )
        for key in (
            "v2_1_incident",
            "remediation_request",
            "v2_2_scope_authorization",
            "review_request",
        )
    )
    specs = (receipt_spec, *authority_specs)
    for spec in specs:
        _classify_unique_a_lineage(
            root,
            spec,
            execution_head=execution_head,
            all_ref_commits=all_ref_commits,
            work_tracker=work_tracker,
        )
    return specs


def _preflight_bundle_recovery(
    binding: ExecutionBinding,
    *,
    recovery_authorization_path: Path,
    owner_binding_path: Path,
    bootstrap: _BootstrapEvidence,
    operation: Literal["RECOVERY_START", "RECOVERED_RELEASE"],
    release_receipt_path: Path | None = None,
    control_pass_nonce: object,
) -> tuple[
    BundleRecoveryAuthorization,
    RecoveryOwnerBinding,
    HistoricalSelectedAnchor,
    LiveExecutionAnchor,
    HistoryValidation,
    _RecoveryStoragePaths,
    Mapping[str, str],
    Mapping[str, int],
    JsonObject,
    _ControlSurfaceCacheEnvelope,
]:
    """The only recovery bootstrap; identity always precedes storage observation."""

    _validate_bootstrap_evidence(bootstrap)
    execution_head = _current_execution_head(binding.project_root)
    r_reference = _authority_reference_for_path(
        binding.project_root,
        recovery_authorization_path,
        execution_head=execution_head,
        label="bundle recovery authorization",
    )
    authorization = _validate_bundle_recovery_authorization(
        binding,
        r_reference,
        require_current_process=(operation == "RECOVERY_START"),
    )
    b_reference = _authority_reference_for_path(
        binding.project_root,
        owner_binding_path,
        execution_head=execution_head,
        label="bundle recovery owner confirmation binding",
    )
    owner_binding = _validate_recovery_owner_binding(
        binding,
        authorization,
        b_reference,
    )
    qrb_specs = (
        AuthorityCensusSpec(owner_binding.review_request, "PINNED_SOURCE", None),
        AuthorityCensusSpec(
            authorization.authority_ref(binding.project_root), "PINNED_SOURCE", None
        ),
        AuthorityCensusSpec(
            owner_binding.authority_ref(binding.project_root), "PINNED_SOURCE", None
        ),
    )
    work_tracker = _RecoveryWorkTracker()
    census_specs: tuple[AuthorityCensusSpec, ...]
    if operation == "RECOVERY_START":
        if release_receipt_path is not None:
            raise RehearsalV22Error("recovery start received a release receipt path")
        census_specs = qrb_specs
    else:
        if release_receipt_path is None:
            raise RehearsalV22Error("recovered release omitted the fixed receipt path")
        census_specs = (
            *qrb_specs,
            *_recovered_release_census_specs(
                binding,
                release_receipt_path,
                execution_head=execution_head,
                work_tracker=work_tracker,
            ),
        )
    live_anchor, start_census = _live_execution_anchor_with_census(
        binding,
        authorization.execution_epoch,
        additional_references=census_specs,
        work_tracker=work_tracker,
    )
    if _census_reference(start_census).get("canonical_json_sha256") != (
        live_anchor.real_lineage_census_sha256
    ):
        raise RehearsalV22Error("live anchor and start census disagree")
    live_control_cache = _freeze_control_surface_cache(
        binding.project_root,
        implementation_commit=live_anchor.implementation_commit,
        execution_head=live_anchor.execution_head,
        ref_snapshot_sha256=cast(str, start_census.get("ref_snapshot_after_sha256")),
        lineage_census_sha256=live_anchor.real_lineage_census_sha256,
        pass_nonce=control_pass_nonce,
        control=live_anchor.control_surface,
    )
    if operation == "RECOVERY_START":
        _validate_recovery_qrb_census_delta(
            binding.project_root,
            authorization,
            owner_binding,
            start_census,
        )
    storage = _registered_recovery_storage(
        binding,
        authorization,
        expected_state=("PRECLAIM_EMPTY" if operation == "RECOVERY_START" else "PUBLISHED_SUCCESS"),
        preflight_storage=owner_binding.registered_recovery_storage_preflight,
    )
    history, historical_anchor, fingerprints, counters = _validate_sealed_recovery_inputs(
        binding,
        authorization,
        owner_binding,
        storage,
    )
    _cross_bind_epoch_8_preflight_inputs_live(
        binding,
        owner_binding,
        history=history,
        fingerprints=fingerprints,
        counters=counters,
    )
    work_tracker.add_registered(counters)
    observed_work = work_tracker.snapshot()
    return (
        authorization,
        owner_binding,
        historical_anchor,
        live_anchor,
        history,
        storage,
        fingerprints,
        observed_work,
        start_census,
        live_control_cache,
    )


def _build_epoch_8_recovery_authority_state() -> tuple[Any, ...]:
    recovery_nonce = object()
    recovery_delegation_nonce = object()
    publication_delegation_nonce = object()
    recovery_registry: tuple[RecoveryExecutionCapability, ...] = ()
    recovery_delegation_registry: tuple[
        tuple[RecoveryValidatorDelegation, RecoveryExecutionCapability, ModuleType], ...
    ] = ()
    publication_registry: tuple[
        tuple[
            RecoveredPublicationCapability,
            ExecutionBinding,
            BundleRecoveryAuthorization,
            RecoveryOwnerBinding,
            HistoricalSelectedAnchor,
            LiveExecutionAnchor,
            ModuleType,
            Path,
            Path,
        ],
        ...,
    ] = ()
    publication_delegation_registry: tuple[
        tuple[
            RecoveredPublicationValidatorDelegation,
            RecoveredPublicationCapability,
            ModuleType,
        ],
        ...,
    ] = ()

    @contextmanager
    def recovery_execution_capability_scope(
        *,
        binding: ExecutionBinding,
        bootstrap: _BootstrapEvidence,
        authorization: BundleRecoveryAuthorization,
        owner_binding: RecoveryOwnerBinding,
        historical_anchor: HistoricalSelectedAnchor,
        live_anchor: LiveExecutionAnchor,
        storage: _RecoveryStoragePaths,
        audit_policy: _AuditPolicy,
    ) -> Iterator[RecoveryExecutionCapability]:
        nonlocal recovery_registry
        _validate_bootstrap_evidence(bootstrap)
        policy = _AUDIT_POLICY.get()
        if (
            policy is not audit_policy
            or not _audit_policy_is_issued(audit_policy)
            or authorization.path != binding.action_authorization_path
            or owner_binding.recovery_authorization
            != authorization.authority_ref(binding.project_root)
            or historical_anchor.require_current is not False
            or live_anchor.require_current is not True
        ):
            raise RehearsalV22Error("recovery capability lacks its exact identity or audit scope")
        value = RecoveryExecutionCapability(
            _nonce=recovery_nonce,
            binding=binding,
            bootstrap=bootstrap,
            authorization=authorization,
            owner_binding=owner_binding,
            historical_anchor=historical_anchor,
            live_anchor=live_anchor,
            storage=storage,
            audit_policy=audit_policy,
        )
        recovery_registry = (*recovery_registry, value)
        try:
            yield value
        finally:
            recovery_registry = tuple(item for item in recovery_registry if item is not value)

    @contextmanager
    def borrow_recovery_validator_authority(
        execution_context: RecoveryExecutionCapability,
        *,
        validator_module: ModuleType,
        bundle_path: Path,
    ) -> Iterator[RecoveryValidatorDelegation]:
        nonlocal recovery_delegation_registry
        if (
            execution_context._nonce is not recovery_nonce
            or not any(item is execution_context for item in recovery_registry)
            or validator_module.__name__ != "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
            or sys.modules.get(validator_module.__name__) is not validator_module
        ):
            raise RehearsalV22Error("recovery validator delegation lacks issued identity")
        bundle = bundle_path.absolute()
        bundle_sha = _sha256(_regular_bytes(bundle, "delegated recovered bundle"))
        token = RecoveryValidatorDelegation(
            _nonce=recovery_delegation_nonce,
            capability_id=id(execution_context),
            validator_module_id=id(validator_module),
            bundle_path=bundle,
            bundle_sha256=bundle_sha,
            lifetime_id=id(object()),
        )
        record = (token, execution_context, validator_module)
        recovery_delegation_registry = (*recovery_delegation_registry, record)
        try:
            yield token
        finally:
            recovery_delegation_registry = tuple(
                item for item in recovery_delegation_registry if item is not record
            )

    def validate_recovery_validator_delegation(
        execution_context: RecoveryExecutionCapability,
        validator_delegation: RecoveryValidatorDelegation,
        validator_module: ModuleType,
        project_root: Path,
        bundle_path: Path,
    ) -> tuple[
        ExecutionBinding,
        BundleRecoveryAuthorization,
        RecoveryOwnerBinding,
        HistoricalSelectedAnchor,
        LiveExecutionAnchor,
    ]:
        records = [item for item in recovery_delegation_registry if item[0] is validator_delegation]
        bundle = bundle_path.absolute()
        if (
            execution_context._nonce is not recovery_nonce
            or not any(item is execution_context for item in recovery_registry)
            or len(records) != 1
            or records[0][1] is not execution_context
            or records[0][2] is not validator_module
            or validator_delegation._nonce is not recovery_delegation_nonce
            or validator_delegation.capability_id != id(execution_context)
            or validator_delegation.validator_module_id != id(validator_module)
            or validator_delegation.bundle_path != bundle
            or not _lower_hex(validator_delegation.bundle_sha256, 64)
            or execution_context.binding.project_root != project_root.absolute()
            or execution_context.historical_anchor.require_current is not False
            or execution_context.live_anchor.require_current is not True
        ):
            raise RehearsalV22Error("recovery validator delegation is forged or stale")
        return (
            execution_context.binding,
            execution_context.authorization,
            execution_context.owner_binding,
            execution_context.historical_anchor,
            execution_context.live_anchor,
        )

    @contextmanager
    def recovered_publication_validation_scope(
        *,
        capability: RecoveredPublicationCapability,
        binding: ExecutionBinding,
        authorization: BundleRecoveryAuthorization,
        owner_binding: RecoveryOwnerBinding,
        historical_anchor: HistoricalSelectedAnchor,
        live_anchor: LiveExecutionAnchor,
        validator_module: ModuleType,
        bundle_path: Path,
        receipt_path: Path,
    ) -> Iterator[tuple[RecoveredPublicationCapability, RecoveredPublicationValidatorDelegation]]:
        nonlocal publication_registry, publication_delegation_registry
        bundle = bundle_path.absolute()
        receipt = receipt_path.absolute()
        if (
            tuple(capability.__dataclass_fields__) != RECOVERED_PUBLICATION_CAPABILITY_FIELD_ORDER
            or receipt != binding.project_root / RELEASE_RELATIVE
            or validator_module.__name__ != "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
            or sys.modules.get(validator_module.__name__) is not validator_module
            or capability.recovery_authorization_sha256 != authorization.sha256
            or capability.owner_binding_sha256 != owner_binding.sha256
            or capability.published_bundle_sha256
            != _sha256(_regular_bytes(bundle, "recovered publication bundle"))
            or historical_anchor.require_current is not False
            or live_anchor.require_current is not True
        ):
            raise RehearsalV22Error("recovered publication capability binding drifted")
        record = (
            capability,
            binding,
            authorization,
            owner_binding,
            historical_anchor,
            live_anchor,
            validator_module,
            bundle,
            receipt,
        )
        publication_registry = (*publication_registry, record)
        token = RecoveredPublicationValidatorDelegation(
            _nonce=publication_delegation_nonce,
            capability_id=id(capability),
            validator_module_id=id(validator_module),
            bundle_path=bundle,
            bundle_sha256=capability.published_bundle_sha256,
            release_path=receipt,
            release_sha256=_sha256(_regular_bytes(receipt, "recovered release receipt")),
            lifetime_id=id(object()),
        )
        delegation_record = (token, capability, validator_module)
        publication_delegation_registry = (
            *publication_delegation_registry,
            delegation_record,
        )
        try:
            yield capability, token
        finally:
            publication_delegation_registry = tuple(
                item for item in publication_delegation_registry if item is not delegation_record
            )
            publication_registry = tuple(
                item for item in publication_registry if item is not record
            )

    def validate_recovered_publication_validator_delegation(
        execution_context: RecoveredPublicationCapability,
        validator_delegation: RecoveredPublicationValidatorDelegation,
        validator_module: ModuleType,
        project_root: Path,
        bundle_path: Path,
        receipt_path: Path,
    ) -> tuple[
        ExecutionBinding,
        BundleRecoveryAuthorization,
        RecoveryOwnerBinding,
        HistoricalSelectedAnchor,
        LiveExecutionAnchor,
    ]:
        matches = [item for item in publication_registry if item[0] is execution_context]
        delegations = [
            item for item in publication_delegation_registry if item[0] is validator_delegation
        ]
        if len(matches) != 1 or len(delegations) != 1:
            raise RehearsalV22Error("recovered publication delegation is absent or ambiguous")
        (
            _capability,
            binding,
            authorization,
            owner_binding,
            historical_anchor,
            live_anchor,
            registered_validator,
            bundle,
            receipt,
        ) = matches[0]
        if (
            registered_validator is not validator_module
            or delegations[0][1] is not execution_context
            or delegations[0][2] is not validator_module
            or validator_delegation._nonce is not publication_delegation_nonce
            or validator_delegation.capability_id != id(execution_context)
            or validator_delegation.bundle_path != bundle_path.absolute()
            or validator_delegation.release_path != receipt_path.absolute()
            or receipt_path.absolute() != project_root.absolute() / RELEASE_RELATIVE
            or bundle != bundle_path.absolute()
            or receipt != receipt_path.absolute()
        ):
            raise RehearsalV22Error("recovered publication delegation is forged or stale")
        return binding, authorization, owner_binding, historical_anchor, live_anchor

    return (
        recovery_execution_capability_scope,
        borrow_recovery_validator_authority,
        validate_recovery_validator_delegation,
        recovered_publication_validation_scope,
        validate_recovered_publication_validator_delegation,
    )


(
    _recovery_execution_capability_scope,
    _borrow_recovery_validator_authority,
    _validate_recovery_validator_delegation,
    _recovered_publication_validation_scope,
    _validate_recovered_publication_validator_delegation,
) = _build_epoch_8_recovery_authority_state()


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
        raise RehearsalV22Error("loaded repository sources regressed during selected runs")


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
    _validate_second_copy_history(binding, validate_live_history(binding))
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
        capability = _active_validator_execution_context(
            binding=binding,
            validator_module=validator_module,
        )
        history = validate_live_history(binding)
        _validate_second_copy_history(binding, history)
        candidate_root = _validate_official_validator_candidate(
            binding=binding,
            validator_module=validator_module,
            bundle_path=bundle_path,
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
    history = validate_live_history(binding)
    _validate_second_copy_history(binding, history)
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
                binding.primary_receipt_root,
                binding.secondary_snapshot_root,
                binding.secondary_receipt_root,
                LEGACY_OFFICIAL_LEDGER_ROOT,
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
                _validate_second_copy_history(binding, history)
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
                    merkle.get("attempt_history_root_sha256") != history.history_root_sha256
                    or merkle.get("control_surface_root_sha256") != control.merkle_root_sha256
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


REGISTERED_FINGERPRINT_KEYS = frozenset(
    {
        "registered_v2_2_primary_container",
        "registered_v2_2_secondary_container",
        "registered_v2_2_destination",
        "registered_v2_2_ledger",
        "registered_v2_2_primary_receipts",
        "registered_v2_2_secondary_snapshots",
        "registered_v2_2_secondary_receipts",
        "lost_v2_2_ledger",
        "retired_v2_1_destination",
        "consumed_v2_1_claim",
        "real_heldout_root",
    }
)

# There are sixteen AST Call nodes plus one FunctionDef: seventeen source-level
# semantic occurrences.  Raw text search also sees the audit strings below, so it is
# intentionally not the counting rule.  The table never drives the predicate.
# Each row is (source occurrence, official reachability, legitimate changed keys,
# paired-span explanation).
REAL_PATH_FINGERPRINT_OCCURRENCE_AUDIT = (
    (
        "validate_disposable_capability.snapshot_compare",
        "disposable_only",
        (),
        "Seeded by _run_cli; disposable state is outside all registered roots.",
    ),
    (
        "validate_replay_capability.snapshot_compare",
        "official",
        (),
        "Standalone validator replay is read-only; its authority is a sibling temp root.",
    ),
    (
        "_official_validator_replay_scope.in_process_before",
        "official",
        (),
        "Before borrowing validator authority for a staged, unpublished bundle.",
    ),
    (
        "_official_validator_replay_scope.in_process_after",
        "official",
        (),
        "In-process validation may not change any registered path.",
    ),
    (
        "_official_validator_replay_scope.standalone_before",
        "official",
        (),
        "Before creating a sibling validator-replay temp authority.",
    ),
    (
        "_official_validator_replay_scope.standalone_after",
        "official",
        (),
        "Replay authority creation, use and cleanup stay outside registered roots.",
    ),
    (
        "_real_path_fingerprints.definition",
        "definition_not_call",
        (),
        "The seventeenth semantic occurrence is the definition, not an AST Call node.",
    ),
    (
        "_run_disposable_release_probe.before",
        "disposable_only",
        (),
        "Registered official mode returns before this synthetic-release span.",
    ),
    (
        "_run_disposable_release_probe.after",
        "disposable_only",
        (),
        "Disposable release writes remain under the synthetic project root.",
    ),
    (
        "_preallocation_authority_probes.before",
        "official",
        (),
        "All hostile authority probes must reject before effect.",
    ),
    (
        "_preallocation_authority_probes.forged_disposable_snapshot",
        "disposable_only",
        (),
        "The official branch does not construct a disposable capability snapshot.",
    ),
    (
        "_preallocation_authority_probes.wrong_disposable_snapshot",
        "disposable_only",
        (),
        "The official branch does not construct a disposable capability snapshot.",
    ),
    (
        "_preallocation_authority_probes.after",
        "official",
        (),
        "Preallocation probes may not allocate or write registered state.",
    ),
    (
        "_ledger_create_only_probes.positive_before",
        "official",
        (
            "registered_v2_2_ledger",
            "registered_v2_2_primary_container",
        ),
        "Begins the exact two-file active-ledger positive transition.",
    ),
    (
        "_ledger_create_only_probes.positive_after_and_negative_baseline",
        "official",
        (
            "registered_v2_2_ledger",
            "registered_v2_2_primary_container",
        ),
        "Only ancestor-or-self views of the active ledger may reflect the two writes.",
    ),
    (
        "_ledger_create_only_probes.negative_after",
        "official",
        (),
        "All mutation probes after the positive baseline must reject before effect.",
    ),
    (
        "_run_cli.disposable_capability_seed",
        "disposable_only",
        (),
        "Official execution does not seed a disposable registered-path snapshot.",
    ),
)

# Terminal sealing and second-copy publication are deliberately not bracketed by a
# _real_path_fingerprints equality span.  Terminal persistence changes the active
# ledger and its ancestor view.  Mirror-only publication changes five registered
# views, and their union is six keys.
TERMINAL_SEAL_REGISTERED_KEY_AUDIT = (
    "registered_v2_2_ledger",
    "registered_v2_2_primary_container",
)
MIRROR_ONLY_REGISTERED_KEY_AUDIT = (
    "registered_v2_2_primary_container",
    "registered_v2_2_primary_receipts",
    "registered_v2_2_secondary_container",
    "registered_v2_2_secondary_receipts",
    "registered_v2_2_secondary_snapshots",
)
SEAL_THEN_MIRROR_REGISTERED_KEY_AUDIT = (
    "registered_v2_2_ledger",
    "registered_v2_2_primary_container",
    "registered_v2_2_primary_receipts",
    "registered_v2_2_secondary_container",
    "registered_v2_2_secondary_receipts",
    "registered_v2_2_secondary_snapshots",
)
SEAL_THEN_MIRROR_ATTEMPT_1_AUDIT_NOTE = (
    "Attempt 1 raised inside the positive-ledger transition before the negative-after "
    "snapshot. SeriesLedger.__exit__ then persisted the failure terminal and mirrored "
    "it outside every _real_path_fingerprints equality span. Mirror safety came from "
    "the dedicated capability, tree inventory, paired receipts and second-copy check."
)


def _registered_fingerprint_scopes(
    *,
    primary_series_container: Path,
    ledger_root: Path,
) -> dict[str, Path]:
    scopes = {
        "registered_v2_2_primary_container": primary_series_container,
        "registered_v2_2_secondary_container": OFFICIAL_SECONDARY_SERIES_CONTAINER,
        "registered_v2_2_destination": OFFICIAL_DESTINATION,
        "registered_v2_2_ledger": ledger_root,
        "registered_v2_2_primary_receipts": OFFICIAL_PRIMARY_RECEIPT_ROOT,
        "registered_v2_2_secondary_snapshots": OFFICIAL_SECONDARY_SNAPSHOT_ROOT,
        "registered_v2_2_secondary_receipts": OFFICIAL_SECONDARY_RECEIPT_ROOT,
        "lost_v2_2_ledger": LEGACY_OFFICIAL_LEDGER_ROOT,
        "retired_v2_1_destination": V2_1_DESTINATION,
        "consumed_v2_1_claim": V2_1_EMPTY_CLAIM,
        "real_heldout_root": PROTECTED_HELDOUT_ROOT,
    }
    if frozenset(scopes) != REGISTERED_FINGERPRINT_KEYS:
        raise RehearsalV22Error("registered fingerprint scope key set drifted")
    return scopes


def _shallow_registered_fingerprint(
    root: Path,
    *,
    hash_regular_payloads: bool,
) -> dict[str, str]:
    """Fingerprint one root and its direct members without recursive descent."""

    if not os.path.lexists(root):
        return {".": "absent"}
    metadata = root.lstat()
    if root.is_symlink():
        return {".": f"symlink:{os.readlink(root)}"}
    if root.is_file():
        digest = _sha256(root.read_bytes()) if hash_regular_payloads else "not-read"
        return {".": (f"file:{digest}:{stat.S_IMODE(metadata.st_mode):04o}:{metadata.st_nlink}")}
    if not root.is_dir():
        return {".": f"special:{metadata.st_mode:o}"}
    observed = {".": f"directory:{stat.S_IMODE(metadata.st_mode):04o}"}
    for path in sorted(root.iterdir(), key=lambda item: os.fsencode(item.name)):
        member = path.lstat()
        if path.is_symlink():
            value = f"symlink:{os.readlink(path)}"
        elif path.is_file():
            digest = _sha256(path.read_bytes()) if hash_regular_payloads else "not-read"
            value = f"file:{digest}:{stat.S_IMODE(member.st_mode):04o}:{member.st_nlink}"
        elif path.is_dir():
            value = (
                f"directory:{stat.S_IMODE(member.st_mode):04o}:"
                f"device:{member.st_dev}:inode:{member.st_ino}:uid:{member.st_uid}"
            )
        else:
            value = f"special:{member.st_mode:o}"
        observed[path.name] = value
    return observed


def _receipt_commitment_fingerprint(
    root: Path,
) -> tuple[dict[str, str], dict[str, str]]:
    """Hash only the small, direct receipt files and expose their byte roots."""

    fingerprint = _shallow_registered_fingerprint(
        root,
        hash_regular_payloads=True,
    )
    receipt_roots = {
        name: value.split(":", 2)[1]
        for name, value in fingerprint.items()
        if name != "." and value.startswith("file:")
    }
    return fingerprint, receipt_roots


def _snapshot_commitment_fingerprint(
    root: Path,
    *,
    receipt_roots: Mapping[str, str],
) -> dict[str, str]:
    """Bind snapshot names to seal-time roots without reading snapshot payloads."""

    observed = _shallow_registered_fingerprint(
        root,
        hash_regular_payloads=False,
    )
    for name, value in tuple(observed.items()):
        if name == "." or not value.startswith("directory:"):
            continue
        receipt_name = f"{name}.mirror-verification.json"
        observed[name] = f"{value}:receipt:{receipt_roots.get(receipt_name, 'absent')}"
    return observed


def _composed_registered_container_fingerprint(
    root: Path,
    *,
    children: Mapping[str, Mapping[str, str]],
) -> dict[str, str]:
    """Compose direct registered children while retaining active-ledger detail."""

    shallow = _shallow_registered_fingerprint(root, hash_regular_payloads=True)
    if shallow.get(".", "").startswith("directory:"):
        observed: dict[str, str] = {".": shallow["."]}
        for name, value in shallow.items():
            if name == ".":
                continue
            child = children.get(name)
            if child is None:
                observed[name] = value
                continue
            for relative, child_value in child.items():
                observed[name if relative == "." else f"{name}/{relative}"] = child_value
    else:
        observed = shallow
    observed[".identity"] = (
        f"device:{root.lstat().st_dev}:inode:{root.lstat().st_ino}:uid:{root.lstat().st_uid}"
        if os.path.lexists(root)
        else "absent"
    )
    return observed


def _real_path_fingerprints() -> dict[str, Mapping[str, str]]:
    active_ledger = _tree_fingerprint(OFFICIAL_LEDGER_ROOT)
    primary_receipts, _primary_receipt_roots = _receipt_commitment_fingerprint(
        OFFICIAL_PRIMARY_RECEIPT_ROOT
    )
    secondary_receipts, secondary_receipt_roots = _receipt_commitment_fingerprint(
        OFFICIAL_SECONDARY_RECEIPT_ROOT
    )
    secondary_snapshots = _snapshot_commitment_fingerprint(
        OFFICIAL_SECONDARY_SNAPSHOT_ROOT,
        receipt_roots=secondary_receipt_roots,
    )
    observed: dict[str, Mapping[str, str]] = {
        "registered_v2_2_primary_container": _composed_registered_container_fingerprint(
            OFFICIAL_PRIMARY_SERIES_CONTAINER,
            children={
                OFFICIAL_LEDGER_ROOT.name: active_ledger,
                OFFICIAL_PRIMARY_RECEIPT_ROOT.name: primary_receipts,
            },
        ),
        "registered_v2_2_secondary_container": _composed_registered_container_fingerprint(
            OFFICIAL_SECONDARY_SERIES_CONTAINER,
            children={
                OFFICIAL_SECONDARY_SNAPSHOT_ROOT.name: secondary_snapshots,
                OFFICIAL_SECONDARY_RECEIPT_ROOT.name: secondary_receipts,
            },
        ),
        "registered_v2_2_destination": _tree_fingerprint(OFFICIAL_DESTINATION),
        "registered_v2_2_ledger": active_ledger,
        "registered_v2_2_primary_receipts": primary_receipts,
        "registered_v2_2_secondary_snapshots": secondary_snapshots,
        "registered_v2_2_secondary_receipts": secondary_receipts,
        "lost_v2_2_ledger": _tree_fingerprint(LEGACY_OFFICIAL_LEDGER_ROOT),
        "retired_v2_1_destination": _tree_fingerprint(V2_1_DESTINATION),
        "consumed_v2_1_claim": _tree_fingerprint(V2_1_EMPTY_CLAIM),
        "real_heldout_root": _tree_fingerprint(PROTECTED_HELDOUT_ROOT),
    }
    if frozenset(observed) != REGISTERED_FINGERPRINT_KEYS:
        raise RehearsalV22Error("registered fingerprint implementation key set drifted")
    return observed


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
        raise RehearsalV22Error("v2.1 mint prerequisite control registry is duplicate or expanded")
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
            raise RehearsalV22Error(f"v2.1 mint prerequisite source bytes drifted: {relative}")
        payloads.append((target, payload))
        expected_by_target[target] = payload
    prepare._publish_create_only(tuple(payloads))
    for target, expected_payload in expected_by_target.items():
        if (
            _regular_bytes(
                target,
                f"copied v2.1 mint prerequisite {target.relative_to(workspace)}",
                allow_zero=False,
            )
            != expected_payload
        ):
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


def _bundle_schema(project_root: Path) -> JsonObject:
    payload = _regular_bytes(
        project_root / SERIES_2_BUNDLE_SCHEMA_RELATIVE,
        "series-2 v2.2 bundle schema",
    )
    if _sha256(payload) != SERIES_2_BUNDLE_SCHEMA_SHA256:
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


def _verified_file_ref(project_root: Path, relative: str, digest: str) -> JsonObject:
    payload = _regular_bytes(
        _safe_path(project_root, relative, f"lineage file {relative}"),
        f"lineage file {relative}",
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
) -> JsonObject:
    lineage: JsonObject = {
        "preregistration": _verified_file_ref(
            project_root,
            SERIES_2_PREREGISTRATION_RELATIVE.as_posix(),
            SERIES_2_PREREGISTRATION_SHA256,
        ),
        "bundle_schema": _verified_file_ref(
            project_root,
            SERIES_2_BUNDLE_SCHEMA_RELATIVE.as_posix(),
            SERIES_2_BUNDLE_SCHEMA_SHA256,
        ),
        "release_authorization_schema": _verified_file_ref(
            project_root,
            SERIES_2_RELEASE_SCHEMA_RELATIVE.as_posix(),
            SERIES_2_RELEASE_SCHEMA_SHA256,
        ),
        "v1_fail_close_commit": V1_FAIL_CLOSE_COMMIT,
        "preregistration_commit": SERIES_2_PREREGISTRATION_COMMIT,
        "implementation_commit": implementation_commit,
        "retired_v1_artifacts": [
            _verified_file_ref(project_root, path, digest)
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
        lineage[key] = _verified_file_ref(project_root, path, digest)
    for key, (path, digest, creating_commit) in CARRY_FORWARD_AUTHORITIES.items():
        lineage[key] = AuthorityReference(path, digest, creating_commit).as_json()
    return lineage


def _run_archive(
    replay: PipelineReplay,
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
            if record.outcome == "CANDIDATE_VALIDATED_AND_SELECTED":
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
        action_path = _safe_path(
            binding.project_root,
            record.owner_action_time_authorization.path,
            f"attempt {record.ordinal} action authorization",
        )
        action_payload = _regular_bytes(
            action_path,
            f"attempt {record.ordinal} action authorization",
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


def _implementation_epochs(
    binding: ExecutionBinding,
    history: HistoryValidation,
) -> list[JsonObject]:
    if not history.records:
        raise RehearsalV22Error("implementation epochs require at least one attempt")
    execution_head = _current_execution_head(binding.project_root)
    groups: list[list[ValidatedAttemptRecord]] = []
    for record in history.records:
        if not groups or groups[-1][0].implementation_epoch != record.implementation_epoch:
            groups.append([record])
        else:
            groups[-1].append(record)
    used_epochs = [group[0].implementation_epoch for group in groups]
    if used_epochs != list(range(SERIES_2_EPOCH_ORIGIN, SERIES_2_EPOCH_ORIGIN + len(groups))):
        raise RehearsalV22Error(
            "series-2 implementation epoch keys do not start at 5 and remain contiguous"
        )
    result: list[JsonObject] = []
    for records in groups:
        expected_epoch = records[0].implementation_epoch
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
        control = build_control_surface(
            binding.project_root,
            authorization.implementation_commit,
            require_current=(
                history.selected_attempt_ordinal is not None
                and records[-1].ordinal == history.selected_attempt_ordinal
            ),
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
    implementation_commit: str,
) -> JsonObject:
    files = {
        "thin_main_shim": SHIM_RELATIVE,
        "implementation_module": IMPLEMENTATION_RELATIVE,
        "validator_module": VALIDATOR_RELATIVE,
    }
    result: JsonObject = {}
    for key, relative in files.items():
        payload = validate_implementation_blob(
            binding.project_root,
            implementation_commit,
            relative.as_posix(),
        )
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
    run_a: PipelineReplay,
    run_b: PipelineReplay,
    control: ControlSurface,
) -> _BundleAssembly:
    _validate_second_copy_history(binding, history)
    if (
        run_a.run_label != "run-a"
        or run_b.run_label != "run-b"
        or dict(run_a.artifacts) != dict(run_b.artifacts)
        or len(run_a.artifacts) != 14
    ):
        raise RehearsalV22Error("selected runs are not 14/14 byte-identical")
    history_archive = _history_archive(binding, history)
    selected = history_archive.selected_record
    if selected.implementation_commit != control.implementation_commit:
        raise RehearsalV22Error("selected history and control implementation disagree")
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
    schema = _bundle_schema(binding.project_root)
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
        ),
        "publication": _constant_section(schema, "publication"),
        "execution_binding": _execution_binding_document(binding),
        "rehearsal_attempt_policy": _constant_section(
            schema,
            "rehearsalAttemptPolicy",
        ),
        "harness_identity": _harness_identity(
            binding,
            implementation_commit=selected.implementation_commit,
        ),
        "implementation_epochs": _implementation_epochs(binding, history),
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


def _rehydrate_sealed_pipeline_replays(
    binding: ExecutionBinding,
    history: HistoryValidation,
    historical_anchor: HistoricalSelectedAnchor,
) -> tuple[_SealedPipelineReplay, _SealedPipelineReplay, Mapping[str, int]]:
    """Read the two selected archived run trees; never execute a pipeline."""

    if (
        historical_anchor.require_current is not False
        or history.selected_attempt_ordinal != 2
        or len(history.records) != 2
    ):
        raise RehearsalV22Error("sealed replay rehydration lacks the historical anchor")
    evidence = binding.ledger_root / "attempts/000002/evidence"
    replays: list[_SealedPipelineReplay] = []
    copied = 0
    for run_label in ("run-a", "run-b"):
        artifacts: dict[str, bytes] = {}
        for logical_name, source_relative in ARTIFACT_INVENTORY:
            path = evidence.joinpath(
                *PurePosixPath(f"runs/{run_label}/root/{source_relative}").parts
            )
            payload = _regular_bytes(path, f"sealed {run_label} artifact {logical_name}")
            artifacts[logical_name] = payload
            copied += len(payload)
        probe_path = evidence / "probes" / f"{run_label}.json"
        probe_payload = _regular_bytes(probe_path, f"sealed {run_label} probe")
        copied += len(probe_payload)
        probes = _object(
            strict_json_loads(probe_payload, source=f"sealed {run_label} probe"),
            f"sealed {run_label} probe",
        )
        if _canonical_json_bytes(probes) != probe_payload:
            raise RehearsalV22Error("sealed pipeline probe is not canonical JSON")
        if binding.mode == "REGISTERED_OFFICIAL" and (
            len(probe_payload) != EPOCH_8_SELECTED_PROBE_BYTES
            or _sha256(probe_payload)
            != (
                EPOCH_8_SELECTED_RUN_A_PROBE_SHA256
                if run_label == "run-a"
                else EPOCH_8_SELECTED_RUN_B_PROBE_SHA256
            )
        ):
            raise RehearsalV22Error("registered sealed pipeline probe bytes drifted")
        replays.append(
            _SealedPipelineReplay(
                run_label=run_label,
                artifacts=dict(sorted(artifacts.items())),
                probe_evidence=probes,
            )
        )
    run_a, run_b = replays
    if dict(run_a.artifacts) != dict(run_b.artifacts) or len(run_a.artifacts) != len(
        ARTIFACT_INVENTORY
    ):
        raise RehearsalV22Error("sealed selected run trees are not byte-identical")
    counters = {
        "git_objects_read": 0,
        "recursive_bytes_hashed": copied,
        "sealed_snapshot_files_visited": len(ARTIFACT_INVENTORY) * 2 + 2,
        "bundle_bytes_copied": 0,
    }
    _assert_recovery_work_bound(counters)
    return run_a, run_b, counters


def _recovered_implementation_epochs(
    binding: ExecutionBinding,
    history: HistoryValidation,
    historical_anchor: HistoricalSelectedAnchor,
) -> list[JsonObject]:
    if historical_anchor.require_current is not False:
        raise RehearsalV22Error("recovered implementation epochs require historical bytes")
    groups: list[list[ValidatedAttemptRecord]] = []
    for record in history.records:
        if not groups or groups[-1][0].implementation_epoch != record.implementation_epoch:
            groups.append([record])
        else:
            groups[-1].append(record)
    if [group[0].implementation_epoch for group in groups] != [5, 6]:
        raise RehearsalV22Error("recovered bundle implementation epoch table is not [5,6]")
    result: list[JsonObject] = []
    for records in groups:
        first = records[0]
        action = _validate_action_authorization(
            binding,
            first.owner_action_time_authorization,
            expected_ordinal=first.ordinal,
            expected_previous_history_root_sha256=first.previous_history_root_sha256,
            require_current_process=False,
            historical_execution_head=first.owner_action_time_authorization.creating_commit,
        )
        control = build_control_surface(
            binding.project_root,
            action.implementation_commit,
            require_current=False,
        )
        for record in records:
            observed = _validate_action_authorization(
                binding,
                record.owner_action_time_authorization,
                expected_ordinal=record.ordinal,
                expected_previous_history_root_sha256=record.previous_history_root_sha256,
                require_current_process=False,
                historical_execution_head=record.owner_action_time_authorization.creating_commit,
            )
            if (
                observed.implementation_epoch != first.implementation_epoch
                or observed.implementation_commit != action.implementation_commit
                or observed.control_merkle_root_sha256 != control.merkle_root_sha256
            ):
                raise RehearsalV22Error("recovered epoch attempts disagree")
        result.append(
            {
                "epoch": first.implementation_epoch,
                "implementation_commit": action.implementation_commit,
                "owner_exact_surface_authorization": action.owner_surface_authorization.as_json(),
                "independent_implementation_review": (
                    action.independent_implementation_review.as_json()
                ),
                "control_merkle_root_sha256": control.merkle_root_sha256,
                "first_attempt_ordinal": records[0].ordinal,
                "last_attempt_ordinal": records[-1].ordinal,
                "all_attempts_authorized": True,
            }
        )
    if (
        result[-1]["implementation_commit"] != historical_anchor.selected_commit
        or result[-1]["control_merkle_root_sha256"]
        != historical_anchor.control_surface.merkle_root_sha256
    ):
        raise RehearsalV22Error("recovered epoch table differs from historical anchor")
    return result


def _recovered_harness_identity(
    binding: ExecutionBinding,
    historical_anchor: HistoricalSelectedAnchor,
) -> JsonObject:
    if historical_anchor.require_current is not False:
        raise RehearsalV22Error("recovered harness identity requires historical bytes")
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
        result[key] = {"path": relative.as_posix(), "sha256": _sha256(payload)}
    result.update(
        {
            "implementation_module_name": MODULE_NAME,
            "validator_module_name": "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle",
            "authority_owner_module": MODULE_NAME,
            "shim_has_authority_state": False,
            "validator_import_target": MODULE_NAME,
            "module_object_identity_equal": True,
            "exact_os_bootstrap_passed": True,
            "implementation_direct_execution_rejected": True,
            "second_authority_module_rejected": True,
            "delegation_binding_passed": "identity_root_creator_owner_and_lifetime_exact",
        }
    )
    return result


def _build_recovered_bundle(
    *,
    binding: ExecutionBinding,
    history: HistoryValidation,
    run_a: _SealedPipelineReplay,
    run_b: _SealedPipelineReplay,
    historical_anchor: HistoricalSelectedAnchor,
    live_anchor: LiveExecutionAnchor,
) -> _BundleAssembly:
    """Assemble the unchanged schema from sealed history without active replay."""

    if historical_anchor.require_current is not False or live_anchor.require_current is not True:
        raise RehearsalV22Error("recovered bundle requires both non-substitutable anchors")
    if (
        run_a.run_label != "run-a"
        or run_b.run_label != "run-b"
        or dict(run_a.artifacts) != dict(run_b.artifacts)
        or len(run_a.artifacts) != 14
    ):
        raise RehearsalV22Error("rehydrated selected runs are not 14/14 byte-identical")
    control = historical_anchor.control_surface
    history_archive = _history_archive(binding, history)
    selected = history_archive.selected_record
    replay_a = PipelineReplay(
        run_label=run_a.run_label,
        artifacts=run_a.artifacts,
        probe_evidence=cast(Mapping[str, JsonObject], run_a.probe_evidence),
        write_root=binding.ledger_root,
        removed=True,
    )
    replay_b = PipelineReplay(
        run_label=run_b.run_label,
        artifacts=run_b.artifacts,
        probe_evidence=cast(Mapping[str, JsonObject], run_b.probe_evidence),
        write_root=binding.ledger_root,
        removed=True,
    )
    run_a_record, run_a_payloads, run_a_root = _run_archive(replay_a)
    run_b_record, run_b_payloads, run_b_root = _run_archive(replay_b)
    control_record, control_payloads = _control_archive(control)
    if selected.candidate_bytes is None or selected.terminal_bytes is None:
        raise RehearsalV22Error("recovered selected attempt lacks sealed candidate bytes")
    candidate = _object(
        strict_json_loads(selected.candidate_bytes, source="recovered selected candidate"),
        "recovered selected candidate",
    )
    if (
        selected.implementation_commit != historical_anchor.selected_commit
        or run_a_root != historical_anchor.run_a_root_sha256
        or run_b_root != historical_anchor.run_b_root_sha256
        or candidate.get("control_surface_root_sha256") != control.merkle_root_sha256
        or candidate.get("evidence_tree_root_sha256") != historical_anchor.evidence_tree_root_sha256
    ):
        raise RehearsalV22Error("rehydrated roots differ from historical selected anchor")
    bundle_root = _bundle_root_sha256(
        attempt_history_root_sha256=history_archive.history_root_sha256,
        run_a_root_sha256=run_a_root,
        run_b_root_sha256=run_b_root,
        control_surface_root_sha256=control.merkle_root_sha256,
    )
    schema = _bundle_schema(binding.project_root)
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
        "recovered bundle Merkle section",
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
            implementation_commit=historical_anchor.selected_commit,
        ),
        "publication": _constant_section(schema, "publication"),
        "execution_binding": _execution_binding_document(binding),
        "rehearsal_attempt_policy": _constant_section(schema, "rehearsalAttemptPolicy"),
        "harness_identity": _recovered_harness_identity(binding, historical_anchor),
        "implementation_epochs": _recovered_implementation_epochs(
            binding,
            history,
            historical_anchor,
        ),
        "attempt_history": history_archive.summary,
        "determinism": _constant_section(schema, "determinism"),
        "real_entry_gate_validation": _constant_section(schema, "realEntryGateValidation"),
        "request_interval_validation": _constant_section(schema, "requestIntervalValidation"),
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
        raise RehearsalV22Error("recovered bundle archive paths collide")
    return _BundleAssembly(
        document=document,
        payloads=dict(sorted(payloads.items(), key=lambda item: item[0].encode("utf-8"))),
        bundle_payload=_canonical_json_bytes(document),
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


def _rename_directory_exclusive(
    source: Path,
    destination: Path,
    *,
    recovery_expected_source_tree: Mapping[str, str] | None = None,
) -> JsonObject:
    """Use Darwin's single-kernel-call no-replace directory rename."""

    policy = _AUDIT_POLICY.get()
    if policy is None or not _audit_policy_is_issued(policy):
        raise RehearsalV22Error("exclusive rename lacks an issued audit policy")
    source_absolute = _require_audited_write_path(source, policy)
    destination_absolute = _require_audited_write_path(destination, policy)
    mirror_path = _path_in_create_only_root(
        source_absolute,
        policy,
    ) or _path_in_create_only_root(destination_absolute, policy)
    if mirror_path:
        raise RehearsalV22Error("bundle renamex_np primitive cannot publish a mirror snapshot")
    if platform.system() != "Darwin":
        raise RehearsalV22Error("v2.2 atomic publication requires Darwin renamex_np")
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
    recovery_capability = _RECOVERY_RENAME_CAPABILITY.get()
    recovery_rename_is_issued = _recovery_rename_capability_is_issued(
        recovery_capability,
        policy=policy,
        source=source_absolute,
        destination=destination_absolute,
    )
    if recovery_expected_source_tree is not None and not recovery_rename_is_issued:
        raise RehearsalV22Error("only an issued recovery rename may use a precomputed source tree")
    source_tree = (
        dict(recovery_expected_source_tree)
        if recovery_expected_source_tree is not None
        else _tree_fingerprint(source_absolute)
    )
    source_before = {
        "device": source_metadata.st_dev,
        "inode": source_metadata.st_ino,
        "tree": source_tree,
    }
    destination_before = _tree_fingerprint(destination_absolute)
    return_code, observed_errno = _native_rename_exclusive_call(
        policy,
        source_absolute,
        destination_absolute,
    )
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


def _rename_mirror_directory_exclusive(
    source: Path,
    destination: Path,
    *,
    parent_descriptor: int,
) -> JsonObject:
    """Publish one mirror snapshot with the registered parent-fd primitive."""

    policy = _AUDIT_POLICY.get()
    if policy is None or not _audit_policy_is_issued(policy):
        raise RehearsalV22Error("mirror exclusive rename lacks an issued audit policy")
    source_absolute = _require_audited_write_path(source, policy)
    destination_absolute = _require_audited_write_path(destination, policy)
    if (
        policy.mirror_write_phase != "publish"
        or policy.mirror_publish_paths != (source_absolute, destination_absolute)
        or policy.mirror_staging_root != source_absolute
        or policy.mirror_snapshot_root is None
        or source_absolute.parent != policy.mirror_snapshot_root
        or destination_absolute.parent != policy.mirror_snapshot_root
        or source_absolute.parent != destination_absolute.parent
    ):
        raise RehearsalV22Error("exclusive mirror rename escaped its exact publish phase")
    if platform.system() != "Darwin":
        raise RehearsalV22Error("series-2 mirror publication requires Darwin renameatx_np")
    source_metadata = source_absolute.lstat()
    parent_metadata = destination_absolute.parent.lstat()
    parent_identity = (parent_metadata.st_dev, parent_metadata.st_ino)
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
        raise RehearsalV22Error("mirror rename source, parent, or device drifted")
    source_before = {
        "device": source_metadata.st_dev,
        "inode": source_metadata.st_ino,
        "tree": _tree_fingerprint(source_absolute),
    }
    destination_before = _tree_fingerprint(destination_absolute)
    return_code, observed_errno, parent_fsync_completed = _native_mirror_renameatx_exclusive_call(
        policy,
        source_absolute,
        destination_absolute,
        parent_descriptor=parent_descriptor,
        expected_parent_identity=parent_identity,
    )
    if return_code != 0:
        source_after = source_absolute.lstat()
        destination_after = _tree_fingerprint(destination_absolute)
        if (
            observed_errno != errno.EEXIST
            or parent_fsync_completed
            or source_after.st_dev != source_metadata.st_dev
            or source_after.st_ino != source_metadata.st_ino
            or _tree_fingerprint(source_absolute) != source_before["tree"]
            or (destination_before != {".": "absent"} and destination_after != destination_before)
        ):
            raise RehearsalV22Error("mirror exclusive rename failed without preserving both paths")
        raise RehearsalV22Error(
            "mirror exclusive rename rejected an existing destination with EEXIST"
        )
    if observed_errno != 0 or destination_before != {".": "absent"} or not parent_fsync_completed:
        raise RehearsalV22Error("mirror exclusive rename succeeded from invalid state")
    destination_metadata = destination_absolute.lstat()
    parent_after = destination_absolute.parent.lstat()
    if (
        os.path.lexists(source_absolute)
        or destination_absolute.is_symlink()
        or not stat.S_ISDIR(destination_metadata.st_mode)
        or destination_absolute.resolve(strict=True) != destination_absolute
        or destination_metadata.st_dev != source_metadata.st_dev
        or destination_metadata.st_ino != source_metadata.st_ino
        or (parent_after.st_dev, parent_after.st_ino) != parent_identity
    ):
        raise RehearsalV22Error("mirror exclusive rename result identity drifted")
    return {
        "syscall": "renameatx_np",
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
        "destination_parent_fsync_via_identity_bound_descriptor": True,
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


def _recovery_execution_policy(
    binding: ExecutionBinding,
    storage: _RecoveryStoragePaths,
    *,
    secondary_snapshot: Path,
    primary_receipt: Path,
    secondary_receipt: Path,
) -> _AuditPolicy:
    staging_roots = (
        storage.destination_stage,
        storage.secondary_snapshot_stage,
    )
    exact_outputs = (
        storage.claim_root,
        storage.claim_root / "terminal.json",
        binding.destination,
        secondary_snapshot,
        primary_receipt,
        secondary_receipt,
    )
    return _AuditPolicy(
        project_root=binding.project_root,
        write_roots=staging_roots,
        exact_write_paths=exact_outputs,
        create_only_roots=(),
        sqlite_roots=(),
        git_roots=(binding.project_root,),
        subprocess_mode="git-read",
        recovery_rename_pairs=(
            (storage.destination_stage, binding.destination),
            (storage.secondary_snapshot_stage, secondary_snapshot),
        ),
    )


def _recovery_claim_policy(
    binding: ExecutionBinding,
    storage: _RecoveryStoragePaths,
) -> _AuditPolicy:
    """Authorize only the durable claim directory and its two canonical records."""

    claim_paths = (
        storage.claim_root,
        storage.claim_root / "started.json",
        storage.claim_root / "terminal.json",
    )
    return _AuditPolicy(
        project_root=binding.project_root,
        write_roots=(),
        exact_write_paths=claim_paths,
        create_only_roots=(),
        sqlite_roots=(),
        git_roots=(binding.project_root,),
        subprocess_mode="git-read",
    )


def _recovery_secondary_snapshot_template(storage: _RecoveryStoragePaths) -> Path:
    template = storage.secondary_container / (storage.secondary_snapshot_prefix + "<TREE_SHA256>")
    if template.as_posix().count("<TREE_SHA256>") != 1:
        raise RehearsalV22Error("recovery snapshot template is not one exact placeholder")
    return template


def _recovery_timestamp_pair() -> tuple[str, str]:
    instant = datetime.now(UTC).replace(microsecond=0)
    return (
        instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
        instant.astimezone(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
    )


def _stage_recovered_bundle_tree(
    stage: Path,
    assembly: _BundleAssembly,
) -> int:
    if os.path.lexists(stage):
        raise RehearsalV22Error("recovered bundle stage already exists")
    os.mkdir(stage, 0o700)
    _fsync_directory(stage.parent)
    for relative, payload in assembly.payloads.items():
        normalized = _relative_text(relative, "recovered bundle archive path")
        parent = _ensure_private_directory(stage, PurePosixPath(normalized).parent)
        _write_exclusive(parent / PurePosixPath(normalized).name, payload, mode=0o600)
    _write_exclusive(stage / BUNDLE_FILENAME, assembly.bundle_payload, mode=0o600)
    for directory in sorted(
        (path for path in stage.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        _fsync_directory(directory)
    _fsync_directory(stage)
    return len(assembly.bundle_payload) + sum(
        len(payload) for payload in assembly.payloads.values()
    )


def _recovered_assembly_tree_fingerprint(
    assembly: _BundleAssembly,
) -> tuple[dict[str, str], int, int]:
    result = {".": "directory:0700"}
    files = {**assembly.payloads, BUNDLE_FILENAME: assembly.bundle_payload}
    directories: set[str] = set()
    for relative, payload in files.items():
        normalized = _relative_text(relative, "recovered assembly path")
        pure = PurePosixPath(normalized)
        for parent in pure.parents:
            if parent.as_posix() != ".":
                directories.add(parent.as_posix())
        result[normalized] = f"file:{_sha256(payload)}:0600:1"
    for relative in directories:
        result[relative] = "directory:0700"
    return (
        dict(sorted(result.items(), key=lambda item: item[0].encode("utf-8"))),
        sum(len(payload) for payload in files.values()),
        len(files),
    )


def _copy_recovered_bundle_tree(source: Path, destination_stage: Path) -> int:
    if source.is_symlink() or not source.is_dir() or os.path.lexists(destination_stage):
        raise RehearsalV22Error("secondary recovered snapshot copy has invalid endpoints")
    os.mkdir(destination_stage, 0o700)
    _fsync_directory(destination_stage.parent)
    copied_bytes = 0
    for path in sorted(
        source.rglob("*"),
        key=lambda item: item.relative_to(source).as_posix().encode("utf-8"),
    ):
        relative = path.relative_to(source)
        target = destination_stage / relative
        metadata = path.lstat()
        if path.is_symlink():
            raise RehearsalV22Error("published recovered bundle contains a symlink")
        if stat.S_ISDIR(metadata.st_mode):
            os.mkdir(target, 0o700)
            _fsync_directory(target.parent)
        elif stat.S_ISREG(metadata.st_mode):
            payload = _regular_bytes(path, "published recovered bundle file")
            _write_exclusive(target, payload)
            copied_bytes += len(payload)
        else:
            raise RehearsalV22Error("published recovered bundle contains a special entry")
    for directory in sorted(
        (path for path in destination_stage.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        _fsync_directory(directory)
    _fsync_directory(destination_stage)
    return copied_bytes


@dataclass
class _RecoveryOutputState:
    secondary_snapshot: Path
    primary_receipt: Path
    secondary_receipt: Path
    published_bundle_sha256: str | None = None
    published_tree_sha256: str | None = None


def _write_recovery_failure_terminal(
    *,
    binding: ExecutionBinding,
    authorization: BundleRecoveryAuthorization,
    owner_binding: RecoveryOwnerBinding,
    history: HistoryValidation,
    storage: _RecoveryStoragePaths,
    sealed_fingerprints: Mapping[str, str],
    output_state: _RecoveryOutputState,
    error: BaseException,
) -> None:
    terminal_path = storage.claim_root / "terminal.json"
    if os.path.lexists(terminal_path):
        return
    after_fingerprints, _failure_work = _sealed_recovery_fingerprints(binding, history)
    completed_utc, completed_shanghai = _recovery_timestamp_pair()
    partial = os.path.lexists(binding.destination)
    failure_terminal = {
        "schema_version": EPOCH_8_RECOVERY_TERMINAL_SCHEMA,
        "recovery_id": authorization.authorization_id,
        "authorization": authorization.authority_ref(binding.project_root).as_json(),
        "owner_confirmation_binding": owner_binding.authority_ref(binding.project_root).as_json(),
        "completed_at_utc": completed_utc,
        "completed_at_shanghai": completed_shanghai,
        "outcome": (
            "PUBLISHED_MIRROR_INCOMPLETE_OWNER_RECONCILIATION_REQUIRED"
            if partial
            else "FAILED_NO_AUTOMATIC_RETRY"
        ),
        "reached_stage": "recovery_failed_after_claim",
        "sealed_ledger_before_sha256": sealed_fingerprints["active_ledger"],
        "sealed_ledger_after_sha256": after_fingerprints["active_ledger"],
        "sealed_mirror_before_sha256": sealed_fingerprints["through_ordinal_2_snapshot"],
        "sealed_mirror_after_sha256": after_fingerprints["through_ordinal_2_snapshot"],
        "destination": binding.destination.as_posix(),
        "published_bundle_sha256": output_state.published_bundle_sha256,
        "published_tree_sha256": output_state.published_tree_sha256,
        "secondary_snapshot": output_state.secondary_snapshot.as_posix(),
        "secondary_snapshot_tree_sha256": None,
        "primary_receipt": output_state.primary_receipt.as_posix(),
        "secondary_receipt": output_state.secondary_receipt.as_posix(),
        "paired_receipts_byte_identical": False,
        "destination_stage_absent": not os.path.lexists(storage.destination_stage),
        "secondary_snapshot_stage_absent": not os.path.lexists(storage.secondary_snapshot_stage),
        "pipeline_starts": 0,
        "automatic_retry_count": 0,
        "error": {
            "exception_type": type(error).__name__,
            "message_sha256": _sha256(str(error).encode("utf-8")),
        },
    }
    _write_exclusive(terminal_path, _canonical_json_bytes(failure_terminal))


@contextmanager
def _recovery_postclaim_execution_scope(
    *,
    binding: ExecutionBinding,
    authorization: BundleRecoveryAuthorization,
    owner_binding: RecoveryOwnerBinding,
    history: HistoryValidation,
    storage: _RecoveryStoragePaths,
    sealed_fingerprints: Mapping[str, str],
    output_state: _RecoveryOutputState,
    policy: _AuditPolicy,
    claim_policy: _AuditPolicy,
    bootstrap: _BootstrapEvidence,
) -> Iterator[None]:
    """Guarantee that every failure after a durable claim receives one terminal."""

    try:
        with _audited_execution(policy, bootstrap=bootstrap):
            yield
    except BaseException as exc:
        if not os.path.lexists(storage.claim_root / "terminal.json"):
            with _audited_execution(claim_policy, bootstrap=bootstrap):
                _write_recovery_failure_terminal(
                    binding=binding,
                    authorization=authorization,
                    owner_binding=owner_binding,
                    history=history,
                    storage=storage,
                    sealed_fingerprints=sealed_fingerprints,
                    output_state=output_state,
                    error=exc,
                )
        raise


def _execute_authorized_bundle_recovery(
    *,
    binding: ExecutionBinding,
    authorization: BundleRecoveryAuthorization,
    owner_binding: RecoveryOwnerBinding,
    historical_anchor: HistoricalSelectedAnchor,
    live_anchor: LiveExecutionAnchor,
    history: HistoryValidation,
    storage: _RecoveryStoragePaths,
    sealed_fingerprints: Mapping[str, str],
    initial_work_counters: Mapping[str, int],
    start_census: Mapping[str, Any],
    control_pass_nonce: object,
    live_control_cache: _ControlSurfaceCacheEnvelope,
    bootstrap: _BootstrapEvidence,
    validator_module: ModuleType,
) -> tuple[_BundleAssembly, JsonObject, JsonObject]:
    """Consume one recovery start, rehydrate sealed evidence, and publish twice."""

    expected_execution_epoch_payload = _canonical_json_bytes(dict(authorization.execution_epoch))
    if os.path.lexists(storage.claim_root):
        raise RehearsalV22Error("bundle recovery authorization was already consumed")
    claim_policy = _recovery_claim_policy(binding, storage)
    secondary_snapshot_template = _recovery_secondary_snapshot_template(storage)
    receipt_name_template = storage.receipt_prefix + "<TREE_SHA256>.bundle-mirror-verification.json"
    output_state = _RecoveryOutputState(
        secondary_snapshot=secondary_snapshot_template,
        primary_receipt=storage.primary_container / receipt_name_template,
        secondary_receipt=storage.secondary_container / receipt_name_template,
    )
    with _audited_execution(claim_policy, bootstrap=bootstrap):
        os.mkdir(storage.claim_root, 0o700)
        try:
            _fsync_directory(storage.primary_container)
            created_utc, created_shanghai = _recovery_timestamp_pair()
            started = {
                "schema_version": EPOCH_8_RECOVERY_STARTED_SCHEMA,
                "recovery_id": authorization.authorization_id,
                "authorization": authorization.authority_ref(binding.project_root).as_json(),
                "owner_confirmation_binding": owner_binding.authority_ref(
                    binding.project_root
                ).as_json(),
                "created_at_utc": created_utc,
                "created_at_shanghai": created_shanghai,
                "execution_head": live_anchor.execution_head,
                "execution_epoch": live_anchor.execution_epoch,
                "sealed_history_root_sha256": historical_anchor.history_root_sha256,
                "sealed_live_ledger_root_sha256": historical_anchor.live_ledger_root_sha256,
                "sealed_mirror_receipt_sha256": cast(
                    str,
                    _object(
                        authorization.sealed_series.get("sealed_mirror"),
                        "sealed mirror",
                    ).get("receipt_sha256"),
                ),
                "destination": binding.destination.as_posix(),
                "destination_stage": storage.destination_stage.as_posix(),
                "secondary_snapshot_stage": storage.secondary_snapshot_stage.as_posix(),
                "secondary_snapshot_target": secondary_snapshot_template.as_posix(),
                "state": "STARTED",
                "authorized_bundle_recovery_starts": 1,
                "authorized_pipeline_starts": 0,
                "automatic_retry_count": 0,
            }
            started_payload = _canonical_json_bytes(started)
            started_path = storage.claim_root / "started.json"
            _write_exclusive(started_path, started_payload)
            observed_started, observed_started_payload, _started_sha256 = _canonical_object_file(
                started_path,
                label="new recovery started claim",
                exact_fields=set(RECOVERY_STARTED_FIELDS),
            )
            if observed_started_payload != started_payload or not _typed_json_equal(
                observed_started,
                started,
            ):
                raise RehearsalV22Error("recovery started claim changed after durable write")
        except BaseException as exc:
            _write_recovery_failure_terminal(
                binding=binding,
                authorization=authorization,
                owner_binding=owner_binding,
                history=history,
                storage=storage,
                sealed_fingerprints=sealed_fingerprints,
                output_state=output_state,
                error=exc,
            )
            raise
    try:
        with _audited_execution(
            _read_only_preflight_policy(binding.project_root),
            bootstrap=bootstrap,
        ):
            run_a, run_b, rehydrate_work = _rehydrate_sealed_pipeline_replays(
                binding,
                history,
                historical_anchor,
            )
            assembly = _build_recovered_bundle(
                binding=binding,
                history=history,
                run_a=run_a,
                run_b=run_b,
                historical_anchor=historical_anchor,
                live_anchor=live_anchor,
            )
            (
                planned_tree,
                planned_recursive_bytes,
                planned_files_visited,
            ) = _recovered_assembly_tree_fingerprint(assembly)
            planned_tree_sha = _sha256(_canonical_json_bytes(planned_tree))
            projected_work = {
                "git_objects_read": (
                    initial_work_counters["git_objects_read"]
                    + rehydrate_work["git_objects_read"]
                ),
                "recursive_bytes_hashed": (
                    initial_work_counters["recursive_bytes_hashed"] * 3
                    + rehydrate_work["recursive_bytes_hashed"]
                    + planned_recursive_bytes * 3
                ),
                "sealed_snapshot_files_visited": (
                    initial_work_counters["sealed_snapshot_files_visited"] * 3
                    + rehydrate_work["sealed_snapshot_files_visited"]
                    + planned_files_visited * 3
                ),
                "bundle_bytes_copied": planned_recursive_bytes * 2,
            }
            _assert_recovery_work_bound(projected_work)
        secondary_snapshot = storage.secondary_container / (
            storage.secondary_snapshot_prefix + planned_tree_sha
        )
        expected_snapshot = Path(
            secondary_snapshot_template.as_posix().replace(
                "<TREE_SHA256>",
                planned_tree_sha,
                1,
            )
        )
        if secondary_snapshot != expected_snapshot:
            raise RehearsalV22Error(
                "recovered bundle tree SHA does not resolve the started snapshot template"
            )
        primary_receipt = storage.primary_container / (
            storage.receipt_prefix + planned_tree_sha + ".bundle-mirror-verification.json"
        )
        secondary_receipt = storage.secondary_container / primary_receipt.name
        output_state.secondary_snapshot = secondary_snapshot
        output_state.primary_receipt = primary_receipt
        output_state.secondary_receipt = secondary_receipt
        if any(
            os.path.lexists(path)
            for path in (secondary_snapshot, primary_receipt, secondary_receipt)
        ):
            raise RehearsalV22Error("derived recovery snapshot or receipt already exists")
        policy = _recovery_execution_policy(
            binding,
            storage,
            secondary_snapshot=secondary_snapshot,
            primary_receipt=primary_receipt,
            secondary_receipt=secondary_receipt,
        )
    except BaseException as exc:
        with _audited_execution(claim_policy, bootstrap=bootstrap):
            _write_recovery_failure_terminal(
                binding=binding,
                authorization=authorization,
                owner_binding=owner_binding,
                history=history,
                storage=storage,
                sealed_fingerprints=sealed_fingerprints,
                output_state=output_state,
                error=exc,
            )
        raise
    with _recovery_postclaim_execution_scope(
        binding=binding,
        authorization=authorization,
        owner_binding=owner_binding,
        history=history,
        storage=storage,
        sealed_fingerprints=sealed_fingerprints,
        output_state=output_state,
        policy=policy,
        claim_policy=claim_policy,
        bootstrap=bootstrap,
    ):
        receipt: JsonObject | None = None
        terminal: JsonObject | None = None
        published_bundle_sha: str | None = output_state.published_bundle_sha256
        published_tree_sha: str | None = output_state.published_tree_sha256
        try:
            staged_bundle_bytes = _stage_recovered_bundle_tree(
                storage.destination_stage,
                assembly,
            )
            bundle_path = storage.destination_stage / BUNDLE_FILENAME
            with (
                _recovery_execution_capability_scope(
                    binding=binding,
                    bootstrap=bootstrap,
                    authorization=authorization,
                    owner_binding=owner_binding,
                    historical_anchor=historical_anchor,
                    live_anchor=live_anchor,
                    storage=storage,
                    audit_policy=policy,
                ) as recovery_context,
                _borrow_recovery_validator_authority(
                    recovery_context,
                    validator_module=validator_module,
                    bundle_path=bundle_path,
                ) as validator_delegation,
            ):
                validator_api = getattr(validator_module, "validate_recovered_bundle", None)
                if not callable(validator_api):
                    raise RehearsalV22Error("passive recovered-bundle validator is unavailable")
                validated_document = validator_api(
                    project_root=binding.project_root,
                    bundle_path=bundle_path,
                    execution_context=recovery_context,
                    validator_delegation=validator_delegation,
                )
            if not _typed_json_equal(validated_document, assembly.document):
                raise RehearsalV22Error("passive validator returned another bundle document")
            _validate_live_execution_publication_guard(
                binding,
                authorization=authorization,
                live_anchor=live_anchor,
                start_census=start_census,
                expected_execution_epoch_payload=expected_execution_epoch_payload,
                control_pass_nonce=control_pass_nonce,
                control_cache=live_control_cache,
            )
            with _recovery_rename_scope(
                policy,
                source=storage.destination_stage,
                destination=binding.destination,
            ):
                _rename_directory_exclusive(
                    storage.destination_stage,
                    binding.destination,
                    recovery_expected_source_tree=planned_tree,
                )
            published_bundle_sha = _sha256(
                _regular_bytes(binding.destination / BUNDLE_FILENAME, "published recovered bundle")
            )
            output_state.published_bundle_sha256 = published_bundle_sha
            (
                published_tree,
                destination_recursive_bytes,
                destination_files_visited,
            ) = _tree_fingerprint_with_work(binding.destination)
            published_tree_sha = _sha256(_canonical_json_bytes(published_tree))
            output_state.published_tree_sha256 = published_tree_sha
            if published_tree_sha != planned_tree_sha:
                raise RehearsalV22Error("published recovery tree differs from its preclaim plan")
            if any(
                os.path.lexists(path)
                for path in (
                    secondary_snapshot,
                    primary_receipt,
                    secondary_receipt,
                    storage.secondary_snapshot_stage,
                )
            ):
                raise RehearsalV22Error("recovered mirror output appeared before publication")
            secondary_copy_bytes = _copy_recovered_bundle_tree(
                binding.destination,
                storage.secondary_snapshot_stage,
            )
            with _recovery_rename_scope(
                policy,
                source=storage.secondary_snapshot_stage,
                destination=secondary_snapshot,
            ):
                _rename_directory_exclusive(
                    storage.secondary_snapshot_stage,
                    secondary_snapshot,
                    recovery_expected_source_tree=published_tree,
                )
            (
                snapshot_tree,
                snapshot_recursive_bytes,
                snapshot_files_visited,
            ) = _tree_fingerprint_with_work(secondary_snapshot)
            snapshot_tree_sha = _sha256(_canonical_json_bytes(snapshot_tree))
            after_fingerprints, after_work = _sealed_recovery_fingerprints(binding, history)
            if (
                snapshot_tree_sha != published_tree_sha
                or snapshot_tree != published_tree
                or dict(after_fingerprints) != dict(sealed_fingerprints)
            ):
                raise RehearsalV22Error("recovered mirror or sealed inputs changed")
            verified_utc, _verified_shanghai = _recovery_timestamp_pair()
            receipt = {
                "schema_version": EPOCH_8_RECOVERY_MIRROR_RECEIPT_SCHEMA,
                "recovery_authorization_sha256": authorization.sha256,
                "owner_confirmation_binding_sha256": owner_binding.sha256,
                "recovery_id": authorization.authorization_id,
                "series_id": REHEARSAL_ID,
                "series_token_sha256": binding.series_token_sha256,
                "sealed_history_root_sha256": historical_anchor.history_root_sha256,
                "sealed_live_ledger_root_sha256": historical_anchor.live_ledger_root_sha256,
                "selected_attempt_ordinal": 2,
                "selected_implementation_epoch": historical_anchor.selected_epoch,
                "selected_implementation_commit": historical_anchor.selected_commit,
                "execution_epoch": live_anchor.execution_epoch,
                "execution_implementation_commit": live_anchor.implementation_commit,
                "execution_head": live_anchor.execution_head,
                "destination": binding.destination.as_posix(),
                "published_bundle_sha256": published_bundle_sha,
                "published_tree_sha256": published_tree_sha,
                "secondary_snapshot": secondary_snapshot.as_posix(),
                "secondary_snapshot_tree_sha256": snapshot_tree_sha,
                "destination_and_snapshot_byte_identical": True,
                "pipeline_starts": 0,
                "automatic_retry_count": 0,
                "sealed_ledger_before_after_equal": True,
                "sealed_mirror_before_after_equal": True,
                "verified_at_utc": verified_utc,
            }
            receipt_payload = _canonical_json_bytes(receipt)
            _write_exclusive(primary_receipt, receipt_payload)
            _write_exclusive(secondary_receipt, receipt_payload)
            if (
                _regular_bytes(primary_receipt, "primary recovery receipt") != receipt_payload
                or _regular_bytes(secondary_receipt, "secondary recovery receipt")
                != receipt_payload
            ):
                raise RehearsalV22Error("paired recovered bundle receipts differ")
            final_fingerprints, final_work = _sealed_recovery_fingerprints(binding, history)
            if dict(final_fingerprints) != dict(sealed_fingerprints):
                raise RehearsalV22Error("sealed inputs changed after paired recovery receipts")
            work = {
                field: initial_work_counters[field]
                + rehydrate_work[field]
                + after_work[field]
                + final_work[field]
                for field in RECOVERY_WORK_COUNTER_FIELDS
            }
            work["recursive_bytes_hashed"] += (
                planned_recursive_bytes + destination_recursive_bytes + snapshot_recursive_bytes
            )
            work["sealed_snapshot_files_visited"] += (
                planned_files_visited + destination_files_visited + snapshot_files_visited
            )
            work["bundle_bytes_copied"] += staged_bundle_bytes + secondary_copy_bytes
            if work != projected_work:
                raise RehearsalV22Error(
                    "observed recovery work differs from its fail-fast projection"
                )
            _assert_recovery_work_bound(work)
            completed_utc, completed_shanghai = _recovery_timestamp_pair()
            terminal = {
                "schema_version": EPOCH_8_RECOVERY_TERMINAL_SCHEMA,
                "recovery_id": authorization.authorization_id,
                "authorization": authorization.authority_ref(binding.project_root).as_json(),
                "owner_confirmation_binding": owner_binding.authority_ref(
                    binding.project_root
                ).as_json(),
                "completed_at_utc": completed_utc,
                "completed_at_shanghai": completed_shanghai,
                "outcome": "BUNDLE_RECOVERY_PUBLISHED_MIRRORED_AND_RECEIPTED",
                "reached_stage": "paired_receipts_verified",
                "sealed_ledger_before_sha256": sealed_fingerprints["active_ledger"],
                "sealed_ledger_after_sha256": final_fingerprints["active_ledger"],
                "sealed_mirror_before_sha256": sealed_fingerprints["through_ordinal_2_snapshot"],
                "sealed_mirror_after_sha256": final_fingerprints["through_ordinal_2_snapshot"],
                "destination": binding.destination.as_posix(),
                "published_bundle_sha256": published_bundle_sha,
                "published_tree_sha256": published_tree_sha,
                "secondary_snapshot": secondary_snapshot.as_posix(),
                "secondary_snapshot_tree_sha256": snapshot_tree_sha,
                "primary_receipt": primary_receipt.as_posix(),
                "secondary_receipt": secondary_receipt.as_posix(),
                "paired_receipts_byte_identical": True,
                "destination_stage_absent": not os.path.lexists(storage.destination_stage),
                "secondary_snapshot_stage_absent": not os.path.lexists(
                    storage.secondary_snapshot_stage
                ),
                "pipeline_starts": 0,
                "automatic_retry_count": 0,
                "error": None,
            }
            if (
                terminal["destination_stage_absent"] is not True
                or terminal["secondary_snapshot_stage_absent"] is not True
            ):
                raise RehearsalV22Error("recovery success left a consumed stage behind")
            _write_exclusive(
                storage.claim_root / "terminal.json",
                _canonical_json_bytes(terminal),
            )
            return assembly, receipt, terminal
        except BaseException as exc:
            _write_recovery_failure_terminal(
                binding=binding,
                authorization=authorization,
                owner_binding=owner_binding,
                history=history,
                storage=storage,
                sealed_fingerprints=sealed_fingerprints,
                output_state=output_state,
                error=exc,
            )
            raise


def _recovered_publication_capability(
    *,
    binding: ExecutionBinding,
    authorization: BundleRecoveryAuthorization,
    owner_binding: RecoveryOwnerBinding,
    historical_anchor: HistoricalSelectedAnchor,
    live_anchor: LiveExecutionAnchor,
    storage: _RecoveryStoragePaths,
) -> tuple[RecoveredPublicationCapability, Mapping[str, int]]:
    claim = storage.claim_root
    if sorted(path.name for path in claim.iterdir()) != ["started.json", "terminal.json"]:
        raise RehearsalV22Error("recovered publication claim inventory is not exact")
    started, _started_payload, started_sha = _canonical_object_file(
        claim / "started.json",
        label="recovered publication started claim",
        exact_fields=set(RECOVERY_STARTED_FIELDS),
    )
    terminal, _terminal_payload, terminal_sha = _canonical_object_file(
        claim / "terminal.json",
        label="recovered publication terminal claim",
        exact_fields=set(RECOVERY_TERMINAL_FIELDS),
    )
    _validate_recovery_timestamp_pair(
        started.get("created_at_utc"),
        started.get("created_at_shanghai"),
        "recovered publication started",
    )
    _validate_recovery_timestamp_pair(
        terminal.get("completed_at_utc"),
        terminal.get("completed_at_shanghai"),
        "recovered publication terminal",
    )
    bundle_path = binding.destination / BUNDLE_FILENAME
    bundle_payload = _regular_bytes(bundle_path, "recovered published bundle")
    published_tree_sha = _sha256(_canonical_json_bytes(_tree_fingerprint(binding.destination)))
    primary_receipt = Path(cast(str, terminal.get("primary_receipt"))).absolute()
    secondary_receipt = Path(cast(str, terminal.get("secondary_receipt"))).absolute()
    secondary_snapshot = Path(cast(str, terminal.get("secondary_snapshot"))).absolute()
    snapshot_template = _recovery_secondary_snapshot_template(storage).as_posix()
    terminal_tree_sha = terminal.get("published_tree_sha256")
    resolved_snapshot_target = (
        Path(
            snapshot_template.replace(
                "<TREE_SHA256>",
                cast(str, terminal_tree_sha),
                1,
            )
        ).absolute()
        if _lower_hex(terminal_tree_sha, 64)
        else None
    )
    if (
        not primary_receipt.is_relative_to(storage.primary_container)
        or not secondary_receipt.is_relative_to(storage.secondary_container)
        or not secondary_snapshot.is_relative_to(storage.secondary_container)
        or resolved_snapshot_target != secondary_snapshot
    ):
        raise RehearsalV22Error(
            "recovered publication outputs escape or violate the started snapshot template"
        )
    receipt_payload = _regular_bytes(primary_receipt, "recovered primary bundle receipt")
    secondary_receipt_payload = _regular_bytes(
        secondary_receipt,
        "recovered secondary bundle receipt",
    )
    receipt = _object(
        strict_json_loads(receipt_payload, source="recovered bundle receipt"),
        "recovered bundle receipt",
    )
    if set(receipt) != RECOVERY_MIRROR_RECEIPT_FIELDS or _canonical_json_bytes(receipt) != (
        receipt_payload
    ):
        raise RehearsalV22Error("recovered bundle receipt shape or canonical bytes drifted")
    history = validate_live_history(binding, historical_authority_bytes=True)
    if history.selected_attempt_ordinal != 2 or len(history.records) != 2:
        raise RehearsalV22Error("recovered publication history is not selected ordinal 2")
    selected = history.records[1]
    sealed_fingerprints, sealed_work = _sealed_recovery_fingerprints(binding, history)
    run_a, run_b, rehydrate_work = _rehydrate_sealed_pipeline_replays(
        binding,
        history,
        historical_anchor,
    )
    replay_a = PipelineReplay(
        run_a.run_label,
        run_a.artifacts,
        cast(Mapping[str, JsonObject], run_a.probe_evidence),
        binding.ledger_root,
        True,
    )
    replay_b = PipelineReplay(
        run_b.run_label,
        run_b.artifacts,
        cast(Mapping[str, JsonObject], run_b.probe_evidence),
        binding.ledger_root,
        True,
    )
    _record_a, _payload_a, run_a_root = _run_archive(replay_a)
    _record_b, _payload_b, run_b_root = _run_archive(replay_b)
    reference = authorization.authority_ref(binding.project_root).as_json()
    owner_reference = owner_binding.authority_ref(binding.project_root).as_json()
    started_execution_head = started.get("execution_head")
    if not _lower_hex(started_execution_head, 40) or not _git_is_ancestor(
        binding.project_root,
        cast(str, started_execution_head),
        live_anchor.execution_head,
    ):
        raise RehearsalV22Error("recovered publication started HEAD left live lineage")
    expected_receipt_sha = _object(
        authorization.sealed_series.get("sealed_mirror"),
        "recovered publication sealed mirror",
    ).get("receipt_sha256")
    if (
        started.get("schema_version") != EPOCH_8_RECOVERY_STARTED_SCHEMA
        or started.get("recovery_id") != authorization.authorization_id
        or started.get("authorization") != reference
        or started.get("owner_confirmation_binding") != owner_reference
        or started.get("execution_epoch") != EPOCH_8_IMPLEMENTATION_EPOCH
        or started.get("sealed_history_root_sha256") != historical_anchor.history_root_sha256
        or started.get("sealed_live_ledger_root_sha256")
        != historical_anchor.live_ledger_root_sha256
        or started.get("sealed_mirror_receipt_sha256") != expected_receipt_sha
        or started.get("destination") != binding.destination.as_posix()
        or started.get("destination_stage") != storage.destination_stage.as_posix()
        or started.get("secondary_snapshot_stage") != storage.secondary_snapshot_stage.as_posix()
        or started.get("secondary_snapshot_target") != snapshot_template
        or started.get("state") != "STARTED"
        or started.get("authorized_bundle_recovery_starts") != 1
        or started.get("authorized_pipeline_starts") != 0
        or started.get("automatic_retry_count") != 0
        or terminal.get("schema_version") != EPOCH_8_RECOVERY_TERMINAL_SCHEMA
        or terminal.get("recovery_id") != authorization.authorization_id
        or terminal.get("authorization") != reference
        or terminal.get("owner_confirmation_binding") != owner_reference
        or terminal.get("outcome") != "BUNDLE_RECOVERY_PUBLISHED_MIRRORED_AND_RECEIPTED"
        or terminal.get("reached_stage") != "paired_receipts_verified"
        or terminal.get("destination") != binding.destination.as_posix()
        or terminal.get("published_bundle_sha256") != _sha256(bundle_payload)
        or terminal.get("published_tree_sha256") != published_tree_sha
        or terminal.get("secondary_snapshot") != secondary_snapshot.as_posix()
        or terminal.get("secondary_snapshot_tree_sha256") != published_tree_sha
        or terminal.get("primary_receipt") != primary_receipt.as_posix()
        or terminal.get("secondary_receipt") != secondary_receipt.as_posix()
        or terminal.get("paired_receipts_byte_identical") is not True
        or terminal.get("destination_stage_absent") is not True
        or terminal.get("secondary_snapshot_stage_absent") is not True
        or terminal.get("pipeline_starts") != 0
        or terminal.get("automatic_retry_count") != 0
        or terminal.get("error") is not None
        or receipt_payload != secondary_receipt_payload
        or receipt.get("schema_version") != EPOCH_8_RECOVERY_MIRROR_RECEIPT_SCHEMA
        or receipt.get("recovery_authorization_sha256") != authorization.sha256
        or receipt.get("owner_confirmation_binding_sha256") != owner_binding.sha256
        or receipt.get("recovery_id") != authorization.authorization_id
        or receipt.get("series_id") != REHEARSAL_ID
        or receipt.get("series_token_sha256") != binding.series_token_sha256
        or receipt.get("sealed_history_root_sha256") != historical_anchor.history_root_sha256
        or receipt.get("sealed_live_ledger_root_sha256")
        != historical_anchor.live_ledger_root_sha256
        or receipt.get("selected_attempt_ordinal") != 2
        or receipt.get("selected_implementation_epoch") != historical_anchor.selected_epoch
        or receipt.get("selected_implementation_commit") != historical_anchor.selected_commit
        or receipt.get("execution_epoch") != EPOCH_8_IMPLEMENTATION_EPOCH
        or receipt.get("execution_implementation_commit") != live_anchor.implementation_commit
        or receipt.get("execution_head") != started_execution_head
        or receipt.get("destination") != binding.destination.as_posix()
        or receipt.get("published_bundle_sha256") != _sha256(bundle_payload)
        or receipt.get("published_tree_sha256") != published_tree_sha
        or receipt.get("secondary_snapshot") != secondary_snapshot.as_posix()
        or receipt.get("secondary_snapshot_tree_sha256") != published_tree_sha
        or receipt.get("destination_and_snapshot_byte_identical") is not True
        or receipt.get("pipeline_starts") != 0
        or receipt.get("automatic_retry_count") != 0
        or receipt.get("sealed_ledger_before_after_equal") is not True
        or receipt.get("sealed_mirror_before_after_equal") is not True
        or not isinstance(receipt.get("verified_at_utc"), str)
        or RFC3339_UTC_SECONDS.fullmatch(cast(str, receipt.get("verified_at_utc"))) is None
        or _tree_fingerprint(secondary_snapshot) != _tree_fingerprint(binding.destination)
        or terminal.get("sealed_ledger_before_sha256") != terminal.get("sealed_ledger_after_sha256")
        or terminal.get("sealed_ledger_after_sha256") != sealed_fingerprints["active_ledger"]
        or terminal.get("sealed_mirror_before_sha256") != terminal.get("sealed_mirror_after_sha256")
        or terminal.get("sealed_mirror_after_sha256")
        != sealed_fingerprints["through_ordinal_2_snapshot"]
        or run_a_root != historical_anchor.run_a_root_sha256
        or run_b_root != historical_anchor.run_b_root_sha256
    ):
        raise RehearsalV22Error("recovered publication durable evidence drifted")
    capability = RecoveredPublicationCapability(
        recovery_authorization_path=authorization.authority_ref(binding.project_root).path,
        recovery_authorization_sha256=authorization.sha256,
        recovery_authorization_creating_commit=authorization.creating_commit,
        owner_binding_path=owner_binding.authority_ref(binding.project_root).path,
        owner_binding_sha256=owner_binding.sha256,
        owner_binding_creating_commit=owner_binding.creating_commit,
        claim_root=claim.as_posix(),
        claim_started_sha256=started_sha,
        claim_terminal_sha256=terminal_sha,
        series_token_sha256=binding.series_token_sha256,
        selected_attempt_ordinal=2,
        selected_implementation_epoch=historical_anchor.selected_epoch,
        selected_implementation_commit=historical_anchor.selected_commit,
        sealed_history_root_sha256=historical_anchor.history_root_sha256,
        sealed_live_ledger_root_sha256=historical_anchor.live_ledger_root_sha256,
        destination=binding.destination.as_posix(),
        published_bundle_sha256=_sha256(bundle_payload),
        published_tree_sha256=published_tree_sha,
        secondary_snapshot=secondary_snapshot.as_posix(),
        secondary_snapshot_tree_sha256=published_tree_sha,
        primary_receipt_path=primary_receipt.as_posix(),
        secondary_receipt_path=secondary_receipt.as_posix(),
        paired_receipt_sha256=_sha256(receipt_payload),
        paired_receipt_bytes=len(receipt_payload),
        execution_epoch=live_anchor.execution_epoch,
        execution_implementation_commit=live_anchor.implementation_commit,
        execution_control_merkle_root_sha256=live_anchor.control_surface.merkle_root_sha256,
        recovery_starts=1,
        pipeline_starts=0,
        automatic_retry_count=0,
        sealed_ledger_before_after_equal=True,
        sealed_mirror_before_after_equal=True,
        selected_candidate_sha256=cast(str, selected.candidate_sha256),
        selected_terminal_sha256=cast(str, selected.terminal_sha256),
        selected_evidence_tree_root_sha256=historical_anchor.evidence_tree_root_sha256,
        historical_run_a_root_sha256=run_a_root,
        historical_run_b_root_sha256=run_b_root,
        historical_run_a_probe_sha256=_sha256(_canonical_json_bytes(dict(run_a.probe_evidence))),
        historical_run_b_probe_sha256=_sha256(_canonical_json_bytes(dict(run_b.probe_evidence))),
        historical_full_downstream_replay_verified=True,
    )
    work = {
        field: sealed_work[field] + rehydrate_work[field] for field in RECOVERY_WORK_COUNTER_FIELDS
    }
    work["bundle_bytes_copied"] += len(bundle_payload)
    _assert_recovery_work_bound(work)
    return capability, work


def consume_recovered_release_authorization(
    *,
    binding: ExecutionBinding,
    validator_module: ModuleType,
    recovery_authorization_path: Path,
    owner_binding_path: Path,
    receipt_path: Path,
    bootstrap: _BootstrapEvidence,
) -> JsonObject:
    """Read-only recovered release validation under the fixed registered receipt."""

    if receipt_path.absolute() != binding.project_root / RELEASE_RELATIVE:
        raise RehearsalV22Error("recovered release receipt path is not RELEASE_RELATIVE")
    control_pass_nonce = object()
    (
        authorization,
        owner_binding,
        historical_anchor,
        live_anchor,
        _history,
        storage,
        _sealed_fingerprints,
        preflight_work,
        _start_census,
        _live_control_cache,
    ) = _preflight_bundle_recovery(
        binding,
        recovery_authorization_path=recovery_authorization_path,
        owner_binding_path=owner_binding_path,
        bootstrap=bootstrap,
        operation="RECOVERED_RELEASE",
        release_receipt_path=receipt_path.absolute(),
        control_pass_nonce=control_pass_nonce,
    )
    observed_before = {
        "git_refs": _git_ref_snapshot(binding.project_root)[1],
        "ledger": _tree_fingerprint(binding.ledger_root),
        "sealed_mirror": _tree_fingerprint(binding.secondary_snapshot_root),
        "destination": _tree_fingerprint(binding.destination),
        "primary_recovery": _tree_fingerprint(storage.primary_container),
        "secondary_recovery": _tree_fingerprint(storage.secondary_container),
        "release": _tree_fingerprint(receipt_path.absolute()),
    }
    capability, capability_work = _recovered_publication_capability(
        binding=binding,
        authorization=authorization,
        owner_binding=owner_binding,
        historical_anchor=historical_anchor,
        live_anchor=live_anchor,
        storage=storage,
    )
    bundle_path = binding.destination / BUNDLE_FILENAME
    with _recovered_publication_validation_scope(
        capability=capability,
        binding=binding,
        authorization=authorization,
        owner_binding=owner_binding,
        historical_anchor=historical_anchor,
        live_anchor=live_anchor,
        validator_module=validator_module,
        bundle_path=bundle_path,
        receipt_path=receipt_path.absolute(),
    ) as (execution_context, validator_delegation):
        validator_api = getattr(
            validator_module,
            "validate_recovered_release_authorization",
            None,
        )
        if not callable(validator_api):
            raise RehearsalV22Error("passive recovered-release validator is unavailable")
        result = validator_api(
            project_root=binding.project_root,
            bundle_path=bundle_path,
            receipt_path=receipt_path.absolute(),
            execution_context=execution_context,
            validator_delegation=validator_delegation,
        )
    result_object = _object(result, "recovered release validation result")
    expected_result_fields = {
        "schema_version",
        "status",
        "mode",
        "release_path",
        "release_sha256",
        "bundle_path",
        "bundle_sha256",
        "recovery_authorization_sha256",
        "owner_binding_sha256",
        "claim_terminal_sha256",
        "paired_receipt_sha256",
        "real_lineage_census_sha256",
        "historical_selected_anchor",
        "live_execution_anchor",
        "effect_summary",
    }
    effects = _exact_contract_object(
        result_object.get("effect_summary"),
        frozenset(
            {
                "filesystem_writes",
                "git_writes",
                "ledger_writes",
                "sealed_mirror_writes",
                "destination_writes",
                "temporary_writes",
                "pipeline_starts",
                "automatic_retries",
                "heldout_evaluation_attempts_consumed",
                "model_calls",
                "network_calls",
                "database_accesses",
                "before_after_equal",
            }
        ),
        "recovered release effect summary",
    )
    historical_result = _exact_contract_object(
        result_object.get("historical_selected_anchor"),
        frozenset(
            {
                "implementation_epoch",
                "implementation_commit",
                "control_merkle_root_sha256",
                "history_root_sha256",
                "live_ledger_root_sha256",
                "require_current",
            }
        ),
        "recovered release historical anchor result",
    )
    live_result = _exact_contract_object(
        result_object.get("live_execution_anchor"),
        frozenset(
            {
                "implementation_epoch",
                "implementation_commit",
                "control_merkle_root_sha256",
                "real_lineage_census_sha256",
                "require_current",
            }
        ),
        "recovered release live anchor result",
    )
    expected_effects: JsonObject = {
        key: 0
        for key in (
            "filesystem_writes",
            "git_writes",
            "ledger_writes",
            "sealed_mirror_writes",
            "destination_writes",
            "temporary_writes",
            "pipeline_starts",
            "automatic_retries",
            "heldout_evaluation_attempts_consumed",
            "model_calls",
            "network_calls",
            "database_accesses",
        )
    }
    expected_effects["before_after_equal"] = True
    if (
        set(result_object) != expected_result_fields
        or result_object.get("schema_version")
        != "p4.2a-v2-2-series2-read-only-recovered-release-revalidation-result-v1"
        or result_object.get("status") != "PASS_READ_ONLY_RECOVERED_RELEASE_REVALIDATION"
        or result_object.get("mode") != "PASSIVE_RECOVERED_RELEASE"
        or result_object.get("release_path") != receipt_path.absolute().as_posix()
        or result_object.get("release_sha256")
        != _sha256(_regular_bytes(receipt_path.absolute(), "validated release receipt"))
        or result_object.get("bundle_path") != bundle_path.as_posix()
        or result_object.get("bundle_sha256") != capability.published_bundle_sha256
        or result_object.get("recovery_authorization_sha256") != authorization.sha256
        or result_object.get("owner_binding_sha256") != owner_binding.sha256
        or result_object.get("claim_terminal_sha256") != capability.claim_terminal_sha256
        or result_object.get("paired_receipt_sha256") != capability.paired_receipt_sha256
        or result_object.get("real_lineage_census_sha256") != live_anchor.real_lineage_census_sha256
        or historical_result
        != {
            "implementation_epoch": historical_anchor.selected_epoch,
            "implementation_commit": historical_anchor.selected_commit,
            "control_merkle_root_sha256": historical_anchor.control_surface.merkle_root_sha256,
            "history_root_sha256": historical_anchor.history_root_sha256,
            "live_ledger_root_sha256": historical_anchor.live_ledger_root_sha256,
            "require_current": False,
        }
        or live_result
        != {
            "implementation_epoch": live_anchor.execution_epoch,
            "implementation_commit": live_anchor.implementation_commit,
            "control_merkle_root_sha256": live_anchor.control_surface.merkle_root_sha256,
            "real_lineage_census_sha256": live_anchor.real_lineage_census_sha256,
            "require_current": True,
        }
        or effects != expected_effects
        or any(
            type(effects.get(key)) is not int
            for key in expected_effects
            if key != "before_after_equal"
        )
        or type(effects.get("before_after_equal")) is not bool
    ):
        raise RehearsalV22Error(
            "recovered release validator result is not exact zero-effect evidence"
        )
    observed_after = {
        "git_refs": _git_ref_snapshot(binding.project_root)[1],
        "ledger": _tree_fingerprint(binding.ledger_root),
        "sealed_mirror": _tree_fingerprint(binding.secondary_snapshot_root),
        "destination": _tree_fingerprint(binding.destination),
        "primary_recovery": _tree_fingerprint(storage.primary_container),
        "secondary_recovery": _tree_fingerprint(storage.secondary_container),
        "release": _tree_fingerprint(receipt_path.absolute()),
    }
    if observed_before != observed_after:
        raise RehearsalV22Error("recovered release consumption changed governed state")
    work = {
        field: preflight_work[field] + capability_work[field]
        for field in RECOVERY_WORK_COUNTER_FIELDS
    }
    _assert_recovery_work_bound(work)
    return result_object


def _release_schema(project_root: Path) -> JsonObject:
    payload = _regular_bytes(
        project_root / SERIES_2_RELEASE_SCHEMA_RELATIVE,
        "series-2 v2.2 release schema",
    )
    if _sha256(payload) != SERIES_2_RELEASE_SCHEMA_SHA256:
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
        raise RehearsalV22Error("synthetic Git commit lacks issued disposable authority")
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
            "schema_version": ("p4.2a-v2-2-disposable-release-probe-review-request-v1"),
            "status": "DISPOSABLE_FULL_SHAPE_TEST_ONLY",
            "mode": binding.mode,
            "bundle": {
                "path": f"{DESTINATION_RELATIVE.as_posix()}/{BUNDLE_FILENAME}",
                "sha256": bundle_sha256,
                "creating_commit": bundle_commit,
            },
            "review_scope": ("synthetic_rehearsal_evidence_and_complete_history_only"),
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
    incomplete_count = sum(record["outcome"] == "INCOMPLETE_UNTERMINALIZED" for record in records)
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
        epoch for epoch in bundle_epochs if epoch.get("epoch") == selected_epoch_number
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
                    "rehearsal_evidence_and_complete_attempt_history_only_not_real_stage_release"
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
                "selected_implementation_commit": selected_epoch["implementation_commit"],
                "rehearsal_evidence_commit": reviewed_head,
                "v2_1_incident": bundle_lineage["v2_1_consumed_attempt_incident"],
                "remediation_request": bundle_lineage["v2_2_remediation_request"],
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
                ("owner_acknowledged_outcomes_equal_ordered_bundle_records"): True,
                "selected_ordinal_is_the_unique_validated_candidate": True,
                "selected_ordinal_and_epoch_match_lineage": True,
                "history_and_live_roots_match_lineage_and_bundle": True,
            },
            "implementation_epochs": [
                {
                    "epoch": epoch["epoch"],
                    "implementation_commit": epoch["implementation_commit"],
                    "owner_surface_authorization": epoch["owner_exact_surface_authorization"],
                    "independent_implementation_review": epoch["independent_implementation_review"],
                    "control_merkle_root_sha256": epoch["control_merkle_root_sha256"],
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
    bundle_sha256 = _sha256(_regular_bytes(bundle_path, "published disposable bundle"))
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
    if positive_replay_labels != ("run-a", "run-b") or positive_temp_before != positive_temp_after:
        raise RehearsalV22Error("positive release replay count or temp cleanup drifted")
    if validated != receipt_document:
        raise RehearsalV22Error("independent release validator returned different receipt bytes")

    modified_receipt_document = copy.deepcopy(receipt_document)
    modified_receipt_document["created_at_utc"] = "2026-08-10T12:30:01Z"
    modified_receipt_document["created_at_shanghai"] = "2026-08-10T20:30:01+08:00"
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
        or _regular_bytes(receipt_path, "modified disposable receipt") != modified_receipt_payload
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
                {"commit": commit, "status": status} for commit, status in receipt_touch_history
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


def _validate_registered_ledger_fingerprint_projection(
    *,
    container_root: Path,
    ledger_root: Path,
    before_real: Mapping[str, Mapping[str, str]],
    after_real: Mapping[str, Mapping[str, str]],
    before_active: Mapping[str, str],
    after_active: Mapping[str, str],
) -> None:
    before_fingerprint_keys = frozenset(before_real)
    after_fingerprint_keys = frozenset(after_real)
    if (
        before_fingerprint_keys != after_fingerprint_keys
        or before_fingerprint_keys != REGISTERED_FINGERPRINT_KEYS
    ):
        raise RehearsalV22Error("registered fingerprint key set drifted")
    if ledger_root == container_root or not ledger_root.is_relative_to(container_root):
        raise RehearsalV22Error("active ledger root is not nested below its container")
    scopes = _registered_fingerprint_scopes(
        primary_series_container=container_root,
        ledger_root=ledger_root,
    )
    ledger_scope_keys = {name for name, scope in scopes.items() if scope == ledger_root}
    if len(ledger_scope_keys) != 1:
        raise RehearsalV22Error(
            "official active ledger fingerprint is not the registered ledger fingerprint"
        )
    ledger_key = next(iter(ledger_scope_keys))
    if before_real[ledger_key] != before_active or after_real[ledger_key] != after_active:
        raise RehearsalV22Error(
            "official active ledger fingerprint is not the registered ledger fingerprint"
        )
    changed_real = {name for name in before_real if before_real[name] != after_real[name]}
    expected_changed_real = {
        name
        for name, scope in scopes.items()
        if scope == ledger_root or ledger_root.is_relative_to(scope)
    }
    if changed_real != expected_changed_real:
        raise RehearsalV22Error(
            "official positive ledger writes changed outside active ledger ancestor scopes"
        )
    ledger_changes = {
        relative: (before_active.get(relative), after_active.get(relative))
        for relative in sorted(
            set(before_active) | set(after_active), key=lambda value: value.encode("utf-8")
        )
        if before_active.get(relative) != after_active.get(relative)
    }
    for name in expected_changed_real.difference(ledger_scope_keys):
        prefix = ledger_root.relative_to(scopes[name]).as_posix()
        projected_changes = {
            (prefix if relative == "." else f"{prefix}/{relative}"): values
            for relative, values in ledger_changes.items()
        }
        ancestor_changes = {
            relative: (
                before_real[name].get(relative),
                after_real[name].get(relative),
            )
            for relative in sorted(
                set(before_real[name]) | set(after_real[name]),
                key=lambda value: value.encode("utf-8"),
            )
            if before_real[name].get(relative) != after_real[name].get(relative)
        }
        if ancestor_changes != projected_changes:
            raise RehearsalV22Error(
                "official active ledger ancestor fingerprint projection drifted"
            )


def _validate_active_ledger_positive_transition(
    *,
    mode: ExecutionMode,
    container_root: Path,
    ledger_root: Path,
    before_real: Mapping[str, Mapping[str, str]],
    after_real: Mapping[str, Mapping[str, str]],
    before_active: Mapping[str, str],
    after_active: Mapping[str, str],
    created: tuple[tuple[Path, bytes], ...],
) -> JsonObject:
    before_fingerprint_keys = frozenset(before_real)
    after_fingerprint_keys = frozenset(after_real)
    if (
        before_fingerprint_keys != after_fingerprint_keys
        or before_fingerprint_keys != REGISTERED_FINGERPRINT_KEYS
    ):
        raise RehearsalV22Error("registered fingerprint key set drifted")
    expected_created: dict[str, str] = {}
    for path, payload in created:
        try:
            relative = path.relative_to(ledger_root).as_posix()
        except ValueError as exc:
            raise RehearsalV22Error("positive ledger evidence escaped the active ledger") from exc
        if relative in expected_created:
            raise RehearsalV22Error("positive ledger evidence path was duplicated")
        expected_created[relative] = f"file:{_sha256(payload)}:0600:1"
    changed_existing = {
        relative for relative, value in before_active.items() if after_active.get(relative) != value
    }
    created_active = {
        relative: value for relative, value in after_active.items() if relative not in before_active
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
    official = mode == "REGISTERED_OFFICIAL"
    if official:
        _validate_registered_ledger_fingerprint_projection(
            container_root=container_root,
            ledger_root=ledger_root,
            before_real=before_real,
            after_real=after_real,
            before_active=before_active,
            after_active=after_active,
        )
    elif mode == "DISPOSABLE_FULL_SHAPE_TEST":
        if ledger_root == OFFICIAL_LEDGER_ROOT or before_real != after_real:
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
        mode=binding.mode,
        container_root=binding.primary_series_container,
        ledger_root=binding.ledger_root,
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
        relative = binding.action_authorization_path.relative_to(binding.project_root).as_posix()
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
    landing_report_path: Path | None = None,
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
    preflight_binding = _derive_binding_unchecked(
        root,
        action_authorization_path=root
        / (
            "docs/phase4/reports/P4.2a-v2-2-rehearsal-attempt-"
            "000001-execution-authorization-19700101.json"
        ),
    )
    storage_preflight: JsonObject | None = None
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
    if b"promisor" in git_config or b"partialclone" in git_config or b"[include" in git_config:
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
    landing_report = (
        _authority_reference_for_path(
            root,
            landing_report_path,
            execution_head=execution_head,
            label="epoch-8 landing report",
        )
        if landing_report_path is not None
        else None
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
    real_lineage_census: JsonObject | None = None
    real_lineage_census_recheck: JsonObject | None = None
    real_lineage_work: Mapping[str, int] | None = None
    real_lineage_recheck_work: Mapping[str, int] | None = None
    recovery_storage_preflight: JsonObject | None = None
    sealed_recovery_preflight: JsonObject | None = None
    census_specs: tuple[AuthorityCensusSpec | AuthorityReference, ...] = ()
    if epoch.epoch == EPOCH_8_IMPLEMENTATION_EPOCH:
        if landing_report is None:
            raise RehearsalV22Error("epoch-8 preflight requires an explicit landing report")
        validate_epoch_8_recovery_contract(root, execution_head=execution_head)
        merge_commit = _validate_epoch_8_landing_authority(
            root,
            execution_head=execution_head,
            implementation_commit=epoch.implementation_commit,
            owner=owner_surface,
            review=independent_review,
            landing_report=landing_report,
            control=control,
        )
        census_specs = _live_execution_census_specs(
            {
                "owner_exact_surface_authorization": owner_surface.as_json(),
                "independent_implementation_review": independent_review.as_json(),
                "landing_report": landing_report.as_json(),
                "merge_commit": merge_commit,
            },
            (),
        )
    elif epoch.epoch == EPOCH_7_IMPLEMENTATION_EPOCH:
        if landing_report is not None:
            raise RehearsalV22Error("historical epoch-7 preflight rejects a landing argument")
        validate_epoch_7_recovery_contract(root, execution_head=execution_head)
        census_specs = (
            AuthorityCensusSpec(owner_surface, "PINNED_SOURCE", None),
            AuthorityCensusSpec(
                independent_review,
                "PINNED_LANDING_PROJECTION",
                independent_review.creating_commit,
            ),
        )
    elif landing_report is not None:
        raise RehearsalV22Error("historical preflight rejects an epoch-8 landing argument")
    if census_specs:
        first_census_tracker = _RecoveryWorkTracker()
        real_lineage_census = _real_lineage_census(
            root,
            execution_head=execution_head,
            additional_references=census_specs,
            work_tracker=first_census_tracker,
        )
        real_lineage_work = first_census_tracker.snapshot()
        storage_preflight = _read_only_storage_preflight(preflight_binding)
        if root == REGISTERED_PROJECT_ROOT:
            primary_recovery = _registered_storage_directory(
                OFFICIAL_PRIMARY_RECOVERY_CONTAINER,
                "registered primary recovery container",
            )
            secondary_recovery = _registered_storage_directory(
                OFFICIAL_SECONDARY_RECOVERY_CONTAINER,
                "registered secondary recovery container",
            )
            if tuple(primary_recovery.iterdir()) or tuple(secondary_recovery.iterdir()):
                raise RehearsalV22Error("registered recovery containers are not empty at preflight")
        if root == REGISTERED_PROJECT_ROOT or epoch.epoch == EPOCH_8_IMPLEMENTATION_EPOCH:
            history = validate_live_history(preflight_binding)
            receipts = _validate_second_copy_history(preflight_binding, history)
            historical = _historical_selected_anchor(preflight_binding, history)
            sealed_fingerprints, work = _sealed_recovery_fingerprints(
                preflight_binding,
                history,
            )
            if root == REGISTERED_PROJECT_ROOT:
                recovery_storage_preflight = {
                    "primary_container": _storage_directory_evidence(
                        primary_recovery,
                        "registered primary recovery container",
                    ),
                    "secondary_container": _storage_directory_evidence(
                        secondary_recovery,
                        "registered secondary recovery container",
                    ),
                    "both_owner_provisioned_empty": True,
                    "leaf_paths_created": 0,
                }
            sealed_recovery_preflight = {
                "series_closed": history.series_closed,
                "record_count": len(history.records),
                "selected_attempt_ordinal": history.selected_attempt_ordinal,
                "selected_implementation_epoch": historical.selected_epoch,
                "selected_implementation_commit": historical.selected_commit,
                "history_root_sha256": historical.history_root_sha256,
                "live_ledger_root_sha256": historical.live_ledger_root_sha256,
                "mirror_receipt_count": len(receipts),
                "sealed_input_fingerprints": sealed_fingerprints,
                "work_counters": work,
                "ledger_and_mirror_read_only": True,
            }
        second_census_tracker = _RecoveryWorkTracker()
        real_lineage_census_recheck = _real_lineage_census(
            root,
            execution_head=execution_head,
            additional_references=census_specs,
            work_tracker=second_census_tracker,
        )
        real_lineage_recheck_work = second_census_tracker.snapshot()
    else:
        storage_preflight = _read_only_storage_preflight(preflight_binding)
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
        or (
            landing_report is not None
            and _authority_reference_for_path(
                root,
                cast(Path, landing_report_path),
                execution_head=execution_head,
                label="epoch-8 landing report",
            )
            != landing_report
        )
        or _read_only_storage_preflight(preflight_binding) != storage_preflight
        or (
            real_lineage_census is not None
            and (
                real_lineage_census_recheck != real_lineage_census
                or real_lineage_recheck_work != real_lineage_work
            )
        )
    ):
        raise RehearsalV22Error("read-only implementation preflight snapshot changed")
    result: JsonObject = {
        "schema_version": (
            EPOCH_8_READ_ONLY_PREFLIGHT_SCHEMA
            if epoch.epoch >= EPOCH_8_IMPLEMENTATION_EPOCH
            else "p4.2a-v2-2-read-only-implementation-preflight-v1"
        ),
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
        "series_2_registered_storage": storage_preflight,
        "real_lineage_census": real_lineage_census,
        "sealed_recovery_inputs": sealed_recovery_preflight,
        "effect_summary": {
            "action_receipt_required": False,
            "action_receipts_read": 0,
            "project_and_gate_state_writes_permitted": False,
            "temporary_authorities_created": 0,
            "ledgers_created": 0,
            "storage_containers_created": 0,
            "mirror_leaves_created": 0,
            "attempts_allocated": 0,
            "pipeline_starts": 0,
            "automatic_retries": 0,
            "heldout_evaluation_attempts_consumed": 0,
            "shallow_alternate_partial_and_included_git_config_rejected": True,
            "stdout_persistence_controlled_by_caller": True,
        },
    }
    if epoch.epoch >= EPOCH_8_IMPLEMENTATION_EPOCH:
        result["registered_recovery_storage"] = recovery_storage_preflight
    else:
        result["epoch_7_recovery_storage"] = recovery_storage_preflight
    if epoch.epoch == EPOCH_8_IMPLEMENTATION_EPOCH and set(result) != set(
        EPOCH_8_READ_ONLY_PREFLIGHT_FIELD_ORDER
    ):
        raise RehearsalV22Error("epoch-8 preflight output field set drifted")
    return result


def _preflight_action(
    binding: ExecutionBinding,
    *,
    expected_ordinal: int,
) -> tuple[HistoryValidation, ActionAuthorization, ControlSurface]:
    if os.path.lexists(binding.destination):
        raise RehearsalV22Error("v2.2 rehearsal destination already exists")
    _validate_registered_storage_roots(binding)
    history = validate_live_history(binding)
    if history.series_closed:
        raise RehearsalV22Error("v2.2 rehearsal series is already closed")
    _validate_continuation_mirror_state(
        binding,
        history,
        permit_unmirrored_final_incomplete=True,
    )
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
    _validate_next_series_2_epoch(history, action.implementation_epoch)
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
        OFFICIAL_PRIMARY_SERIES_CONTAINER,
        OFFICIAL_PRIMARY_RECEIPT_ROOT,
        OFFICIAL_SECONDARY_SERIES_CONTAINER,
        OFFICIAL_SECONDARY_SNAPSHOT_ROOT,
        OFFICIAL_SECONDARY_RECEIPT_ROOT,
        LEGACY_OFFICIAL_LEDGER_ROOT,
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
        write_roots=(
            authority,
            binding.ledger_root,
            binding.primary_receipt_root,
            binding.secondary_snapshot_root,
            binding.secondary_receipt_root,
        ),
        exact_write_paths=(
            binding.ledger_root,
            binding.primary_receipt_root,
            binding.secondary_snapshot_root,
            binding.secondary_receipt_root,
            binding.destination,
            *disposable_release_paths,
        ),
        create_only_roots=(
            binding.ledger_root,
            binding.primary_receipt_root,
            binding.secondary_snapshot_root,
            binding.secondary_receipt_root,
        ),
        sqlite_roots=(authority,),
        git_roots=(binding.project_root,),
        subprocess_mode=subprocess_mode,
        mirror_snapshot_root=binding.secondary_snapshot_root,
        primary_receipt_root=binding.primary_receipt_root,
        secondary_receipt_root=binding.secondary_receipt_root,
    )


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
                        "schema_version": ("p4.2a-v2-2-release-after-publication-plan-v1"),
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
            run_a_root_sha256 = _generic_merkle_root(
                {relative: run_a.artifacts[logical] for logical, relative in ARTIFACT_INVENTORY}
            )
            run_b_root_sha256 = _generic_merkle_root(
                {relative: run_b.artifacts[logical] for logical, relative in ARTIFACT_INVENTORY}
            )
            candidate_path = lease.write_candidate(
                run_a_root_sha256=run_a_root_sha256,
                run_b_root_sha256=run_b_root_sha256,
                control_surface_root_sha256=control.merkle_root_sha256,
                validated_at_utc=FIXED_WALL_CLOCK_TEXT,
            )
            if binding.mode == "DISPOSABLE_FULL_SHAPE_TEST":
                observed_disposable = _validate_disposable_capability(
                    execution_context,
                    project_root=binding.project_root,
                )
                candidate_document, _candidate_bytes, _candidate_sha256 = _canonical_object_file(
                    candidate_path,
                    label="disposable candidate checkpoint",
                    exact_fields=CANDIDATE_FIELDS,
                )
                if (
                    observed_disposable != binding
                    or not lease.candidate_written
                    or not lease.frozen
                    or lease.terminal_written
                    or candidate_path != lease.attempt_root / "candidate.json"
                    or candidate_document.get("ordinal") != lease.ordinal
                    or candidate_document.get("attempt_token_sha256") != lease.attempt_token_sha256
                    or candidate_document.get("implementation_epoch") != action.implementation_epoch
                    or candidate_document.get("implementation_commit")
                    != action.implementation_commit
                    or candidate_document.get("run_a_root_sha256") != run_a_root_sha256
                    or candidate_document.get("run_b_root_sha256") != run_b_root_sha256
                    or candidate_document.get("control_surface_root_sha256")
                    != control.merkle_root_sha256
                    or os.path.lexists(lease.attempt_root / "terminal.json")
                ):
                    raise RehearsalV22Error(
                        "disposable candidate checkpoint is not durable pre-terminal state"
                    )
                lease.reached_stage = "candidate_fsynced_before_terminal_checkpoint"
                os.kill(os.getpid(), signal.SIGSTOP)
            lease.write_terminal(
                outcome="CANDIDATE_VALIDATED_AND_SELECTED",
                reached_stage="bundle_candidate_validated",
                completed_at_utc=FIXED_WALL_CLOCK_TEXT,
            )
            lease.reached_stage = "series_closed"
            history = validate_live_history(binding)
            assembly = _build_bundle(
                binding=binding,
                history=history,
                run_a=run_a,
                run_b=run_b,
                control=control,
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
    parser.add_argument("--expected-ordinal", type=_positive_ordinal)
    parser.add_argument("--implementation-epoch", type=_positive_ordinal)
    parser.add_argument("--implementation-commit")
    parser.add_argument("--owner-surface-authorization", type=Path)
    parser.add_argument("--independent-implementation-review", type=Path)
    parser.add_argument("--landing-report", type=Path)
    parser.add_argument("--bundle-recovery-authorization", type=Path)
    parser.add_argument("--bundle-recovery-owner-confirmation-binding", type=Path)
    return parser


def _normalize_cli_interrupt_handler() -> None:
    """Make SIGINT deterministic when a detached parent ignored it before exec."""

    signal.signal(signal.SIGINT, signal.default_int_handler)
    if signal.getsignal(signal.SIGINT) is not signal.default_int_handler:
        raise RehearsalV22Error("v2.2 CLI could not install the Python SIGINT handler")


def _run_cli() -> JsonObject:
    arguments = _parser().parse_args()
    preflight_values = (
        arguments.implementation_epoch,
        arguments.implementation_commit,
        arguments.owner_surface_authorization,
        arguments.independent_implementation_review,
    )
    recovery_values = (
        arguments.bundle_recovery_authorization,
        arguments.bundle_recovery_owner_confirmation_binding,
    )
    if arguments.preflight_only is True:
        if (
            arguments.execute is True
            or arguments.recover_sealed_bundle is True
            or arguments.consume_recovered_release is True
            or arguments.attempt_authorization is not None
            or arguments.expected_ordinal is not None
            or any(value is None for value in preflight_values)
            or any(value is not None for value in recovery_values)
            or (
                cast(int, arguments.implementation_epoch) >= EPOCH_8_IMPLEMENTATION_EPOCH
                and arguments.landing_report is None
            )
            or (
                cast(int, arguments.implementation_epoch) < EPOCH_8_IMPLEMENTATION_EPOCH
                and arguments.landing_report is not None
            )
        ):
            raise RehearsalV22Error("v2.2 read-only preflight arguments are not exact")
    elif arguments.recover_sealed_bundle is True or arguments.consume_recovered_release is True:
        if (
            arguments.execute is True
            or arguments.attempt_authorization is not None
            or arguments.expected_ordinal is not None
            or arguments.landing_report is not None
            or any(value is not None for value in preflight_values)
            or any(value is None for value in recovery_values)
        ):
            raise RehearsalV22Error("v2.2 recovered-mode arguments are not exact")
    elif (
        arguments.execute is not True
        or arguments.attempt_authorization is None
        or arguments.expected_ordinal is None
        or arguments.landing_report is not None
        or any(value is not None for value in preflight_values)
        or any(value is not None for value in recovery_values)
    ):
        raise RehearsalV22Error("v2.2 execution arguments are not exact")
    if arguments.preflight_only is True:
        preidentity_owner_path = cast(Path, arguments.owner_surface_authorization).absolute()
        preidentity_review_path = cast(Path, arguments.independent_implementation_review).absolute()
        preidentity_landing_path = (
            cast(Path, arguments.landing_report).absolute()
            if arguments.landing_report is not None
            else None
        )
        preidentity_preflight_argv = (
            "--preflight-only",
            "--implementation-epoch",
            str(cast(int, arguments.implementation_epoch)),
            "--implementation-commit",
            cast(str, arguments.implementation_commit),
            "--owner-surface-authorization",
            preidentity_owner_path.as_posix(),
            "--independent-implementation-review",
            preidentity_review_path.as_posix(),
            *(
                ("--landing-report", preidentity_landing_path.as_posix())
                if preidentity_landing_path is not None
                else ()
            ),
        )
        if tuple(sys.argv[1:]) != preidentity_preflight_argv:
            raise RehearsalV22Error("v2.2 read-only preflight CLI shape is not exact")
    project_root = _main_project_root()
    if arguments.preflight_only is True:
        if (
            arguments.execute is True
            or arguments.recover_sealed_bundle is True
            or arguments.consume_recovered_release is True
            or arguments.attempt_authorization is not None
            or arguments.expected_ordinal is not None
            or any(value is None for value in preflight_values)
            or any(value is not None for value in recovery_values)
        ):
            raise RehearsalV22Error("v2.2 read-only preflight arguments are not exact")
        owner_surface_path = cast(Path, arguments.owner_surface_authorization).absolute()
        independent_review_path = cast(
            Path,
            arguments.independent_implementation_review,
        ).absolute()
        landing_path = (
            cast(Path, arguments.landing_report).absolute()
            if arguments.landing_report is not None
            else None
        )
        recovered_expected_argv = (
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
            *(
                ("--landing-report", landing_path.as_posix())
                if landing_path is not None
                else ()
            ),
        )
        if tuple(sys.argv) != recovered_expected_argv:
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
                landing_report_path=landing_path,
            )
    if arguments.recover_sealed_bundle is True or arguments.consume_recovered_release is True:
        if (
            arguments.execute is True
            or arguments.preflight_only is True
            or arguments.attempt_authorization is not None
            or arguments.expected_ordinal is not None
            or arguments.landing_report is not None
            or any(value is not None for value in preflight_values)
            or any(value is None for value in recovery_values)
        ):
            raise RehearsalV22Error("v2.2 recovered-mode arguments are not exact")
        recovery_path = cast(Path, arguments.bundle_recovery_authorization).absolute()
        owner_binding_path = cast(
            Path,
            arguments.bundle_recovery_owner_confirmation_binding,
        ).absolute()
        operation_flag = (
            "--recover-sealed-bundle"
            if arguments.recover_sealed_bundle is True
            else "--consume-recovered-release"
        )
        expected_argv = (
            (project_root / SHIM_RELATIVE).as_posix(),
            operation_flag,
            "--bundle-recovery-authorization",
            recovery_path.as_posix(),
            "--bundle-recovery-owner-confirmation-binding",
            owner_binding_path.as_posix(),
        )
        if tuple(sys.argv) != expected_argv:
            raise RehearsalV22Error("v2.2 recovered-mode argv is not exact")
        binding = _derive_binding_unchecked(
            project_root,
            action_authorization_path=recovery_path,
        )
        from scripts import (
            validate_p4_2a_v2_2_heldout_rehearsal_bundle as validator_module,
        )

        with _bootstrap_evidence_scope(
            project_root=project_root,
            shim_path=binding.shim_path,
            argv=tuple(sys.argv),
            orig_argv=tuple(sys.orig_argv),
            environment=dict(os.environ),
        ) as bootstrap:
            if arguments.consume_recovered_release is True:
                with _audited_execution(
                    _read_only_preflight_policy(project_root),
                    bootstrap=bootstrap,
                ):
                    return consume_recovered_release_authorization(
                        binding=binding,
                        validator_module=validator_module,
                        recovery_authorization_path=recovery_path,
                        owner_binding_path=owner_binding_path,
                        receipt_path=project_root / RELEASE_RELATIVE,
                        bootstrap=bootstrap,
                    )
            with _audited_execution(
                _read_only_preflight_policy(project_root),
                bootstrap=bootstrap,
            ):
                control_pass_nonce = object()
                (
                    recovery_authorization,
                    recovery_owner_binding,
                    historical_anchor,
                    live_anchor,
                    history,
                    storage,
                    sealed_fingerprints,
                    work_counters,
                    start_census,
                    live_control_cache,
                ) = _preflight_bundle_recovery(
                    binding,
                    recovery_authorization_path=recovery_path,
                    owner_binding_path=owner_binding_path,
                    bootstrap=bootstrap,
                    operation="RECOVERY_START",
                    control_pass_nonce=control_pass_nonce,
                )
            assembly, receipt, terminal = _execute_authorized_bundle_recovery(
                binding=binding,
                authorization=recovery_authorization,
                owner_binding=recovery_owner_binding,
                historical_anchor=historical_anchor,
                live_anchor=live_anchor,
                history=history,
                storage=storage,
                sealed_fingerprints=sealed_fingerprints,
                initial_work_counters=work_counters,
                start_census=start_census,
                control_pass_nonce=control_pass_nonce,
                live_control_cache=live_control_cache,
                bootstrap=bootstrap,
                validator_module=validator_module,
            )
            return {
                "schema_version": "p4.2a-v2-2-series2-bundle-recovery-result-v1",
                "status": "PASS_BUNDLE_RECOVERY_PUBLISHED_MIRRORED_AND_RECEIPTED",
                "mode": binding.mode,
                "bundle_path": (binding.destination / BUNDLE_FILENAME).as_posix(),
                "bundle_sha256": _sha256(assembly.bundle_payload),
                "bundle_root_sha256": assembly.bundle_root_sha256,
                "receipt_sha256": _sha256(_canonical_json_bytes(receipt)),
                "terminal_sha256": _sha256(_canonical_json_bytes(terminal)),
                "recovery_starts": 1,
                "pipeline_starts": 0,
                "automatic_retry_count": 0,
            }
    if (
        arguments.execute is not True
        or arguments.attempt_authorization is None
        or arguments.expected_ordinal is None
        or arguments.landing_report is not None
        or any(value is not None for value in preflight_values)
        or any(value is not None for value in recovery_values)
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
