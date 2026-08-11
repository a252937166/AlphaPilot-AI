#!/usr/bin/env python3
"""Independently validate the P4.2a successor-v2.1 rehearsal bundle."""

# ruff: noqa: E402

from __future__ import annotations

import os as _validator_os
import sys as _validator_sys

_VALIDATOR_ENVIRONMENT_MARKER = "ALPHAPILOT_P42A_REHEARSAL_V2_1_ENV_LOCKED"
_VALIDATOR_PROJECT_ROOT = _validator_os.path.dirname(
    _validator_os.path.dirname(_validator_os.path.realpath(__file__))
)
_VALIDATOR_PYTHON_EXECUTABLE = _validator_os.path.join(
    _VALIDATOR_PROJECT_ROOT,
    ".venv/bin/python",
)
_VALIDATOR_PYTHON_EXECUTABLE_SHA256 = (
    "f4cd716d4b54f205398bec6932cc59361b087494ca2ddb157a5e8631d4d6f863"
)
_VALIDATOR_ORIG_ARGV_EXECUTABLE_SHA256 = (
    "89c717ced41f6a395612366e5b038226d0d8fca36bbddd9321d385f5f370ebbe"
)
_VALIDATOR_LOCKED_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "OPENBLAS_MAIN_FREE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "PYTHONNOUSERSITE": "1",
    "PYTHONPYCACHEPREFIX": "/dev/null",
    "PYTHONSAFEPATH": "1",
    "TZ": "UTC",
}
_VALIDATOR_EXEC_ENVIRONMENT = {
    **_VALIDATOR_LOCKED_ENVIRONMENT,
    "__CF_USER_TEXT_ENCODING": f"0x{_validator_os.getuid():X}:0x0:0x0",
    "PATH": "/usr/bin:/bin",
    _VALIDATOR_ENVIRONMENT_MARKER: "1",
}


def _validator_early_sha256(path: str) -> str:
    """Compute SHA-256 before any shadowable module import."""

    constants = (
        0x428A2F98,
        0x71374491,
        0xB5C0FBCF,
        0xE9B5DBA5,
        0x3956C25B,
        0x59F111F1,
        0x923F82A4,
        0xAB1C5ED5,
        0xD807AA98,
        0x12835B01,
        0x243185BE,
        0x550C7DC3,
        0x72BE5D74,
        0x80DEB1FE,
        0x9BDC06A7,
        0xC19BF174,
        0xE49B69C1,
        0xEFBE4786,
        0x0FC19DC6,
        0x240CA1CC,
        0x2DE92C6F,
        0x4A7484AA,
        0x5CB0A9DC,
        0x76F988DA,
        0x983E5152,
        0xA831C66D,
        0xB00327C8,
        0xBF597FC7,
        0xC6E00BF3,
        0xD5A79147,
        0x06CA6351,
        0x14292967,
        0x27B70A85,
        0x2E1B2138,
        0x4D2C6DFC,
        0x53380D13,
        0x650A7354,
        0x766A0ABB,
        0x81C2C92E,
        0x92722C85,
        0xA2BFE8A1,
        0xA81A664B,
        0xC24B8B70,
        0xC76C51A3,
        0xD192E819,
        0xD6990624,
        0xF40E3585,
        0x106AA070,
        0x19A4C116,
        0x1E376C08,
        0x2748774C,
        0x34B0BCB5,
        0x391C0CB3,
        0x4ED8AA4A,
        0x5B9CCA4F,
        0x682E6FF3,
        0x748F82EE,
        0x78A5636F,
        0x84C87814,
        0x8CC70208,
        0x90BEFFFA,
        0xA4506CEB,
        0xBEF9A3F7,
        0xC67178F2,
    )
    state = [
        0x6A09E667,
        0xBB67AE85,
        0x3C6EF372,
        0xA54FF53A,
        0x510E527F,
        0x9B05688C,
        0x1F83D9AB,
        0x5BE0CD19,
    ]
    descriptor = _validator_os.open(path, _validator_os.O_RDONLY)
    try:
        payload = bytearray()
        while True:
            chunk = _validator_os.read(descriptor, 131072)
            if not chunk:
                break
            payload.extend(chunk)
    finally:
        _validator_os.close(descriptor)
    bit_length = len(payload) * 8
    payload.append(0x80)
    while len(payload) % 64 != 56:
        payload.append(0)
    payload.extend(bit_length.to_bytes(8, "big"))
    mask = 0xFFFFFFFF
    for offset in range(0, len(payload), 64):
        words = [
            int.from_bytes(payload[offset + index : offset + index + 4], "big")
            for index in range(0, 64, 4)
        ]
        for index in range(16, 64):
            word_15 = words[index - 15]
            word_2 = words[index - 2]
            sigma_0 = (
                ((word_15 >> 7) | (word_15 << 25))
                ^ ((word_15 >> 18) | (word_15 << 14))
                ^ (word_15 >> 3)
            ) & mask
            sigma_1 = (
                ((word_2 >> 17) | (word_2 << 15))
                ^ ((word_2 >> 19) | (word_2 << 13))
                ^ (word_2 >> 10)
            ) & mask
            words.append(
                (words[index - 16] + sigma_0 + words[index - 7] + sigma_1) & mask
            )
        a, b, c, d, e, f, g, h = state
        for index, constant in enumerate(constants):
            sum_1 = (
                ((e >> 6) | (e << 26))
                ^ ((e >> 11) | (e << 21))
                ^ ((e >> 25) | (e << 7))
            ) & mask
            choice = (e & f) ^ ((~e) & g)
            first = (h + sum_1 + choice + constant + words[index]) & mask
            sum_0 = (
                ((a >> 2) | (a << 30))
                ^ ((a >> 13) | (a << 19))
                ^ ((a >> 22) | (a << 10))
            ) & mask
            majority = (a & b) ^ (a & c) ^ (b & c)
            second = (sum_0 + majority) & mask
            h, g, f, e, d, c, b, a = g, f, e, (d + first) & mask, c, b, a, (
                first + second
            ) & mask
        state = [
            (state[0] + a) & mask,
            (state[1] + b) & mask,
            (state[2] + c) & mask,
            (state[3] + d) & mask,
            (state[4] + e) & mask,
            (state[5] + f) & mask,
            (state[6] + g) & mask,
            (state[7] + h) & mask,
        ]
    return "".join(f"{value:08x}" for value in state)


def _validator_fixed_python_executable() -> str:
    launcher = _validator_os.path.abspath(_VALIDATOR_PYTHON_EXECUTABLE)
    target = _validator_os.path.realpath(launcher)
    try:
        metadata = _validator_os.lstat(target)
    except OSError as exc:
        raise RuntimeError("registered v2.1 validator Python is unavailable") from exc
    if (
        metadata.st_mode & 0o170000 != 0o100000
        or _validator_os.path.islink(target)
        or _validator_early_sha256(target) != _VALIDATOR_PYTHON_EXECUTABLE_SHA256
    ):
        raise RuntimeError("registered v2.1 validator Python drifted")
    return launcher


def _validator_early_runtime_is_locked() -> bool:
    return (
        dict(_validator_os.environ) == _VALIDATOR_EXEC_ENVIRONMENT
        and _validator_sys.flags.hash_randomization == 0
        and _validator_sys.flags.no_site == 1
        and _validator_sys.flags.no_user_site == 1
        and _validator_sys.flags.safe_path
        and _validator_sys.dont_write_bytecode
        and _validator_sys.pycache_prefix == "/dev/null"
        and _validator_os.path.abspath(_validator_sys.executable)
        == _validator_fixed_python_executable()
    )


def _validator_orig_argv_executable() -> str:
    executable = _validator_os.path.join(
        _validator_sys.base_prefix,
        "Resources/Python.app/Contents/MacOS/Python",
    )
    try:
        metadata = _validator_os.lstat(executable)
    except OSError as exc:
        raise RuntimeError("registered v2.1 validator orig_argv executable is unavailable") from exc
    if (
        metadata.st_mode & 0o170000 != 0o100000
        or _validator_os.path.islink(executable)
        or _validator_early_sha256(executable) != _VALIDATOR_ORIG_ARGV_EXECUTABLE_SHA256
    ):
        raise RuntimeError("registered v2.1 validator orig_argv executable drifted")
    return executable


def _validator_direct_entry_is_locked() -> bool:
    main_module = _validator_sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    expected = (
        _validator_orig_argv_executable(),
        "-S",
        "-P",
        "-B",
        _validator_os.path.realpath(__file__),
        *_validator_sys.argv[1:],
    )
    return (
        isinstance(main_file, str)
        and _validator_os.path.realpath(main_file) == _validator_os.path.realpath(__file__)
        and tuple(_validator_sys.orig_argv) == expected
    )


if __name__ == "__main__" and not _validator_sys.argv[1:] and not (
    _validator_early_runtime_is_locked() and _validator_direct_entry_is_locked()
):
    raise RuntimeError(
        "registered v2.1 validation must start in the exact -S -P -B interpreter environment"
    )
if __name__ == "__main__" and not (
    _validator_early_runtime_is_locked() and _validator_direct_entry_is_locked()
):
    _validator_python = _validator_fixed_python_executable()
    _validator_os.execve(
        _validator_python,
        [
            _validator_python,
            "-S",
            "-P",
            "-B",
            _validator_os.path.realpath(__file__),
            *_validator_sys.argv[1:],
        ],
        _VALIDATOR_EXEC_ENVIRONMENT,
    )


_VALIDATOR_REGISTERED_BOOTSTRAP = (
    __name__ == "__main__" and _validator_direct_entry_is_locked()
)
if _VALIDATOR_REGISTERED_BOOTSTRAP and not (
    _validator_early_runtime_is_locked() and _validator_direct_entry_is_locked()
):
    raise RuntimeError("registered v2.1 validator interpreter is not isolated")

_VALIDATOR_BOOTSTRAP_GUARD_ACTIVE = _VALIDATOR_REGISTERED_BOOTSTRAP


def _validator_bootstrap_audit_hook(event: str, arguments: tuple[object, ...]) -> None:
    if not _VALIDATOR_BOOTSTRAP_GUARD_ACTIVE:
        return
    if event == "open":
        mode = arguments[1] if len(arguments) > 1 else None
        flags = arguments[2] if len(arguments) > 2 else 0
        writing = (isinstance(mode, str) and any(value in mode for value in "wax+")) or (
            isinstance(flags, int)
            and bool(
                flags
                & (
                    _validator_os.O_WRONLY
                    | _validator_os.O_RDWR
                    | _validator_os.O_CREAT
                    | _validator_os.O_TRUNC
                    | _validator_os.O_APPEND
                    | _validator_os.O_EXCL
                )
            )
        )
        if writing:
            raise RuntimeError("registered v2.1 validator bootstrap attempted a write")
        return
    if event in {
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
    } or event.startswith(("os.exec", "socket.")) or event == "sqlite3.connect":
        raise RuntimeError("registered v2.1 validator bootstrap attempted an external effect")


_validator_sys.addaudithook(_validator_bootstrap_audit_hook)

_validator_site_packages = _validator_os.path.join(
    _VALIDATOR_PROJECT_ROOT,
    ".venv/lib/python3.12/site-packages",
)
if _VALIDATOR_REGISTERED_BOOTSTRAP:
    _validator_stdlib = _validator_os.path.join(
        _validator_sys.base_prefix,
        "lib",
        f"python{_validator_sys.version_info.major}.{_validator_sys.version_info.minor}",
    )
    _validator_candidates = (
        _validator_os.path.join(
            _validator_os.path.dirname(_validator_stdlib),
            f"python{_validator_sys.version_info.major}{_validator_sys.version_info.minor}.zip",
        ),
        _validator_stdlib,
        _validator_os.path.join(_validator_stdlib, "lib-dynload"),
        _validator_site_packages,
        _VALIDATOR_PROJECT_ROOT,
        _validator_os.path.join(_VALIDATOR_PROJECT_ROOT, "src"),
    )
    _validator_runtime_paths: list[str] = []
    for _validator_candidate in _validator_candidates:
        _validator_absolute = _validator_os.path.abspath(_validator_candidate)
        if _validator_absolute not in _validator_runtime_paths:
            _validator_runtime_paths.append(_validator_absolute)
    _validator_sys.path[:] = _validator_runtime_paths
    _validator_real_executable = _validator_os.path.realpath(_VALIDATOR_PYTHON_EXECUTABLE)
    if (
        not _validator_os.path.isfile(_validator_real_executable)
        or _validator_os.path.islink(_validator_real_executable)
        or _validator_early_sha256(_validator_real_executable)
        != _VALIDATOR_PYTHON_EXECUTABLE_SHA256
        or _validator_os.path.realpath(_validator_site_packages) != _validator_site_packages
        or not _validator_os.path.isdir(_validator_site_packages)
        or _validator_os.path.islink(_validator_site_packages)
    ):
        raise RuntimeError("registered v2.1 validator runtime authority drifted")
else:
    _validator_runtime_paths = list(_validator_sys.path)

import argparse
import ast
import contextvars
import hashlib
import importlib.metadata
import json
import platform
import re
import stat
import subprocess
import sys
import sysconfig
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, cast

from jsonschema import Draft202012Validator, FormatChecker
from scripts import rehearse_p4_2a_v2_1_heldout_full_path as runner

if _VALIDATOR_REGISTERED_BOOTSTRAP:
    if (
        not runner._early_runtime_is_locked()
        or runner._EARLY_EXEC_ENVIRONMENT != _VALIDATOR_EXEC_ENVIRONMENT
        or tuple(_validator_sys.path) != tuple(runner._fixed_runtime_paths())
    ):
        raise RuntimeError("registered v2.1 validator runtime binding drifted")
    _validator_configured = sysconfig.get_paths()
    _VALIDATOR_REPOSITORY_MODULE_PATHS = runner._classify_loaded_module_origins(
        modules=_validator_sys.modules,
        repository_root=Path(_VALIDATOR_PROJECT_ROOT),
        runner_path=Path(__file__),
        site_root=Path(_validator_site_packages),
        stdlib_roots=(
            Path(_validator_configured["stdlib"]),
            Path(_validator_configured["platstdlib"]),
        ),
    )
else:
    _VALIDATOR_REPOSITORY_MODULE_PATHS = frozenset()

_VALIDATOR_BOOTSTRAP_GUARD_ACTIVE = False

JsonObject = dict[str, Any]
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_FILENAME = "bundle.json"
VALIDATOR_RESULT_SCHEMA = "p4.2a-v2-heldout-validator-result-v2.1"
REGISTERED_DIRECTORY = runner.SUCCESSOR_DIRECTORY_RELATIVE
TIMING_PREREGISTRATION_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-1-prediction-timing-seam-preregistration-20260810.json"
)
TIMING_PREREGISTRATION_SHA256 = (
    "1052c7a33268572fc794517844dae4b6c1ea504121712ad2f55ec814a7446f9a"
)
TIMING_PREREGISTRATION_COMMIT = "b3c2d2216c1feffd9949f181fa6766f8357ff683"
TIMING_PREIMPLEMENTATION_SHA256 = {
    "scripts/run_p4_2a_offline_extract.py": (
        "5889341f1336f9d75891793b06c689e7b5bfb7180e135a6ff361cdf83e4ef21b"
    ),
    "tests/test_p4_2a_offline_extract.py": (
        "76570178cf5de80dbed29ffc1317574daf75db863cd06da5e49cad3b2681886d"
    ),
}
SCOPE_CORRECTION_RULING_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-1-scope-correction-owner-ruling-20260810.json"
)
SCOPE_CORRECTION_RULING_SHA256 = (
    "36a3baea9ce5e4c28c7e6aff9e77c09691024a870513f49f2094b07963f3582e"
)
SCOPE_CORRECTION_RULING_COMMIT = "88690ef488925f9de922569f961ec4ff1a23bb78"
CONTROL_PLANE_AUTHORIZATION_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-1-control-plane-registry-expansion-authorization-20260811.json"
)
CONTROL_PLANE_AUTHORIZATION_SHA256 = (
    "ab85a0ddd90728c7d41051e640b59f7dc777f2f2aec3c8290286206979251796"
)
CONTROL_PLANE_AUTHORIZATION_COMMIT = "d37040be87644977ddaad60b2590ac2e62b2aeed"
FINALIZER_TEST_RELATIVE = "tests/test_p4_2a_v2_heldout_finalizer.py"
PREEXPANSION_IMPLEMENTATION_COUNT = 14
EXPANDED_IMPLEMENTATION_COUNT = 15
REPOSITORY_SOURCE_KINDS = frozenset(
    {"python_source", "package_initializer", "frozen_control", "project_manifest", "lockfile"}
)
RUNTIME_SOURCE_KINDS = frozenset({"python_runtime", "package_inventory"})
PYTHON_SOURCE_KINDS = frozenset({"python_source", "package_initializer"})
class RehearsalV21ValidationError(RuntimeError):
    """Fail-closed bundle validation error."""


def _reject_constant(value: str, *, source: str) -> NoReturn:
    raise RehearsalV21ValidationError(f"{source} contains invalid numeric constant {value}")


def strict_json_loads(payload: bytes | str, *, source: str = "JSON") -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise RehearsalV21ValidationError(f"{source} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            payload,
            object_pairs_hook=unique,
            parse_constant=lambda value: _reject_constant(value, source=source),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RehearsalV21ValidationError(f"{source} is not strict JSON") from exc


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise RehearsalV21ValidationError(f"{label} must be an object")
    return cast(JsonObject, value)


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RehearsalV21ValidationError(f"{label} must be an array")
    return value


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _regular_bytes(path: Path, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RehearsalV21ValidationError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or path.is_symlink():
        raise RehearsalV21ValidationError(f"{label} is not one regular unlinked file")
    return path.read_bytes()


def _relative(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RehearsalV21ValidationError(f"{label} is not a relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise RehearsalV21ValidationError(f"{label} escapes its authority root")
    return pure.as_posix()


def _safe_path(root: Path, relative: object, label: str) -> Path:
    text = _relative(relative, label)
    path = root.joinpath(*PurePosixPath(text).parts)
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise RehearsalV21ValidationError(f"{label} cannot be resolved") from exc
    if not resolved.is_relative_to(root) or resolved != path.absolute():
        raise RehearsalV21ValidationError(f"{label} contains a symlink or alias")
    return path


def _bound_repository_file(root: Path, relative: str, digest: str, label: str) -> bytes:
    path = _safe_path(root, relative, label)
    payload = _regular_bytes(path, label)
    if _sha256(payload) != digest:
        raise RehearsalV21ValidationError(f"{label} SHA-256 drifted")
    return payload


def _git(root: Path, *arguments: str) -> bytes:
    try:
        runner._validate_git_metadata_authority(root)
    except runner.RehearsalV21Error as exc:
        raise RehearsalV21ValidationError("Git metadata authority is invalid") from exc
    completed = subprocess.run(
        [
            "/usr/bin/git",
            *runner.GIT_CONFIG_PREFIX,
            "-C",
            str(root),
            *arguments,
        ],
        check=False,
        capture_output=True,
        env=runner._sanitized_git_environment(),
    )
    if completed.returncode != 0 or completed.stderr:
        raise RehearsalV21ValidationError(
            f"git {' '.join(arguments[:2])} failed: "
            f"{completed.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return completed.stdout


def _commit(root: Path, value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise RehearsalV21ValidationError(f"{label} is not a full Git commit")
    observed = _git(root, "rev-parse", "--verify", f"{value}^{{commit}}").decode().strip()
    if observed != value:
        raise RehearsalV21ValidationError(f"{label} does not identify one exact commit")
    return value


def _require_ancestor(root: Path, ancestor: str, descendant: str, label: str) -> None:
    try:
        runner._validate_git_metadata_authority(root)
    except runner.RehearsalV21Error as exc:
        raise RehearsalV21ValidationError("Git metadata authority is invalid") from exc
    completed = subprocess.run(
        [
            "/usr/bin/git",
            *runner.GIT_CONFIG_PREFIX,
            "-C",
            str(root),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        check=False,
        capture_output=True,
        env=runner._sanitized_git_environment(),
    )
    if completed.returncode != 0 or completed.stderr:
        raise RehearsalV21ValidationError(f"{label} ancestry proof failed")


def _commit_blob(root: Path, commit: str, relative: str) -> bytes:
    return _git(root, "show", f"{commit}:{relative}")


def _unique_added_path_commit(root: Path, relative: str) -> str:
    history = _git(
        root,
        "log",
        "--first-parent",
        "--diff-merges=first-parent",
        "--format=@@%H",
        "--name-status",
        "--find-renames",
        "--find-copies",
        "--",
        relative,
    ).decode("utf-8", errors="strict")
    touches: list[tuple[str, str, tuple[str, ...]]] = []
    active_commit: str | None = None
    for raw_line in history.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("@@"):
            active_commit = line[2:]
            continue
        if active_commit is None:
            raise RehearsalV21ValidationError(
                "control-plane authority history is malformed"
            )
        fields = tuple(line.split("\t"))
        if len(fields) < 2:
            raise RehearsalV21ValidationError(
                "control-plane authority history is malformed"
            )
        touches.append((active_commit, fields[0], fields[1:]))
    if len(touches) != 1:
        raise RehearsalV21ValidationError(
            "control-plane authority is not one unique Git touch"
        )
    commit, status_code, paths = touches[0]
    if status_code != "A" or paths != (relative,):
        raise RehearsalV21ValidationError(
            "control-plane authority unique Git touch is not status A"
        )
    return _commit(root, commit, "control-plane authority creating commit")


def _validate_control_plane_registry_expansion(root: Path) -> str:
    ruling_payload = _bound_repository_file(
        root,
        SCOPE_CORRECTION_RULING_RELATIVE.as_posix(),
        SCOPE_CORRECTION_RULING_SHA256,
        "scope-correction owner ruling",
    )
    authorization_payload = _bound_repository_file(
        root,
        CONTROL_PLANE_AUTHORIZATION_RELATIVE.as_posix(),
        CONTROL_PLANE_AUTHORIZATION_SHA256,
        "control-plane registry authorization",
    )
    ruling = _object(
        strict_json_loads(ruling_payload, source="scope-correction owner ruling"),
        "scope-correction owner ruling",
    )
    authorization = _object(
        strict_json_loads(
            authorization_payload,
            source="control-plane registry authorization",
        ),
        "control-plane registry authorization",
    )
    ruling_bindings = _object(
        ruling.get("part_1_bindings_required_by_the_disclosure"),
        "scope-correction ruling bindings",
    )
    authorized_scope = _object(
        authorization.get("part_2_authorised_scope"),
        "control-plane authorized scope",
    )
    permitted_effects = _array(
        authorized_scope.get("permitted_effects_exhaustive"),
        "control-plane permitted effects",
    )
    if (
        ruling.get("schema_version")
        != "p4.2a-v2-heldout-rehearsal-v2-1-scope-correction-owner-ruling-v1"
        or ruling.get("verdict") != "ACCEPT"
        or ruling_bindings.get("explicit_verdict")
        != (
            "ACCEPT the retrospective registration of "
            "tests/test_p4_2a_v2_heldout_finalizer.py into the registered "
            "existing-test-update scope"
        )
        or authorization.get("schema_version")
        != "p4.2a-v2-1-control-plane-registry-expansion-authorization-v1"
        or authorization.get("verdict")
        != "AUTHORIZE_NARROW_CONTROL_PLANE_REGISTRY_EXPANSION"
        or authorization.get("repository_head_at_authorization")
        != SCOPE_CORRECTION_RULING_COMMIT
        or authorized_scope.get("modifiable_paths_exhaustive")
        != [
            "scripts/prepare_p4_2a_v2_heldout.py",
            "scripts/rehearse_p4_2a_v2_1_heldout_full_path.py",
            "scripts/validate_p4_2a_v2_1_heldout_rehearsal_bundle.py",
        ]
        or len(permitted_effects) != 3
    ):
        raise RehearsalV21ValidationError(
            "control-plane registry authority contract drifted"
        )
    expansion = permitted_effects[1]
    prefix = "expand the registered path set from 14 to exactly 15 by appending "
    suffix = " to registered_existing_test_updates"
    if (
        not isinstance(expansion, str)
        or not expansion.startswith(prefix)
        or not expansion.endswith(suffix)
    ):
        raise RehearsalV21ValidationError(
            "control-plane registry expansion declaration drifted"
        )
    appended = expansion[len(prefix) : -len(suffix)]
    if appended != FINALIZER_TEST_RELATIVE:
        raise RehearsalV21ValidationError(
            "control-plane appended registry path drifted"
        )
    ruling_commit = _unique_added_path_commit(
        root,
        SCOPE_CORRECTION_RULING_RELATIVE.as_posix(),
    )
    authorization_commit = _unique_added_path_commit(
        root,
        CONTROL_PLANE_AUTHORIZATION_RELATIVE.as_posix(),
    )
    if (
        ruling_commit != SCOPE_CORRECTION_RULING_COMMIT
        or authorization_commit != CONTROL_PLANE_AUTHORIZATION_COMMIT
        or _commit_blob(
            root,
            ruling_commit,
            SCOPE_CORRECTION_RULING_RELATIVE.as_posix(),
        )
        != ruling_payload
        or _commit_blob(
            root,
            authorization_commit,
            CONTROL_PLANE_AUTHORIZATION_RELATIVE.as_posix(),
        )
        != authorization_payload
    ):
        raise RehearsalV21ValidationError(
            "control-plane registry authority history drifted"
        )
    parents = _git(
        root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        authorization_commit,
        "--",
    ).decode("ascii", errors="strict").strip().split()
    if parents != [authorization_commit, ruling_commit]:
        raise RehearsalV21ValidationError(
            "control-plane authorization is not directly based on the scope ruling"
        )
    if (
        runner.SCOPE_CORRECTION_RULING_RELATIVE != SCOPE_CORRECTION_RULING_RELATIVE
        or runner.SCOPE_CORRECTION_RULING_SHA256 != SCOPE_CORRECTION_RULING_SHA256
        or runner.SCOPE_CORRECTION_RULING_COMMIT != SCOPE_CORRECTION_RULING_COMMIT
        or runner.CONTROL_PLANE_AUTHORIZATION_RELATIVE
        != CONTROL_PLANE_AUTHORIZATION_RELATIVE
        or runner.CONTROL_PLANE_AUTHORIZATION_SHA256
        != CONTROL_PLANE_AUTHORIZATION_SHA256
        or runner.CONTROL_PLANE_AUTHORIZATION_COMMIT
        != CONTROL_PLANE_AUTHORIZATION_COMMIT
        or appended != runner.FINALIZER_TEST_RELATIVE
    ):
        raise RehearsalV21ValidationError(
            "runner/validator control-plane authority pins disagree"
        )
    return appended


def _validate_implementation_commit_surface(
    root: Path,
    implementation_commit: str,
    preregistration: Mapping[str, Any],
) -> None:
    contract = _object(
        preregistration.get("implementation_contract"),
        "preregistration implementation_contract",
    )
    expected: dict[str, str] = {}
    for field, status_code in (
        ("registered_modified_consumers", "M"),
        ("registered_new_files", "A"),
        ("registered_existing_test_updates", "M"),
    ):
        values = _array(contract.get(field), f"implementation_contract.{field}")
        for value in values:
            relative = _relative(value, f"implementation_contract.{field}")
            if relative in expected:
                raise RehearsalV21ValidationError(
                    "implementation registry contains duplicate paths"
                )
            expected[relative] = status_code
    for relative in runner.PREDICTION_TIMING_IMPLEMENTATION_PATHS:
        if relative in expected:
            raise RehearsalV21ValidationError(
                "prediction timing implementation registry contains duplicates"
            )
        expected[relative] = "M"
    if len(expected) != PREEXPANSION_IMPLEMENTATION_COUNT:
        raise RehearsalV21ValidationError(
            "pre-expansion implementation registry count drifted"
        )
    appended = _validate_control_plane_registry_expansion(root)
    if appended in expected:
        raise RehearsalV21ValidationError(
            "control-plane implementation registry contains duplicates"
        )
    expected[appended] = "M"
    if len(expected) != EXPANDED_IMPLEMENTATION_COUNT:
        raise RehearsalV21ValidationError(
            "expanded implementation registry count drifted"
        )
    parents = _git(
        root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        implementation_commit,
        "--",
    ).decode("ascii", errors="strict").strip().split()
    if parents != [implementation_commit, CONTROL_PLANE_AUTHORIZATION_COMMIT]:
        raise RehearsalV21ValidationError(
            "implementation commit does not directly follow the control-plane authorization"
        )
    try:
        observed = runner._parse_implementation_name_status(
            _git(
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-status",
                "--no-renames",
                CONTROL_PLANE_AUTHORIZATION_COMMIT,
                implementation_commit,
                "--",
            )
        )
    except runner.RehearsalV21Error as exc:
        raise RehearsalV21ValidationError("implementation diff is invalid") from exc
    if observed != expected:
        raise RehearsalV21ValidationError(
            "implementation commit path/status surface drifted"
        )


def _validate_prediction_timing_control(
    root: Path,
    implementation_commit: str,
) -> None:
    if (
        runner.PREDICTION_TIMING_PREREG_RELATIVE != TIMING_PREREGISTRATION_RELATIVE
        or runner.PREDICTION_TIMING_PREREG_SHA256 != TIMING_PREREGISTRATION_SHA256
        or runner.PREDICTION_TIMING_PREREG_COMMIT != TIMING_PREREGISTRATION_COMMIT
        or set(runner.PREDICTION_TIMING_IMPLEMENTATION_PATHS)
        != set(TIMING_PREIMPLEMENTATION_SHA256)
    ):
        raise RehearsalV21ValidationError("runner/validator prediction timing pins disagree")
    payload = _bound_repository_file(
        root,
        TIMING_PREREGISTRATION_RELATIVE.as_posix(),
        TIMING_PREREGISTRATION_SHA256,
        "prediction timing preregistration",
    )
    document = _object(
        strict_json_loads(payload, source="prediction timing preregistration"),
        "prediction timing preregistration",
    )
    scope = _object(
        document.get("prospective_scope_extension"),
        "prediction timing prospective scope",
    )
    records = _array(scope.get("paths"), "prediction timing paths")
    observed_preimplementation: dict[str, str] = {}
    for raw_record in records:
        record = _object(raw_record, "prediction timing path")
        relative = _relative(record.get("path"), "prediction timing path")
        digest = record.get("current_sha256")
        if not isinstance(digest, str) or relative in observed_preimplementation:
            raise RehearsalV21ValidationError(
                "prediction timing preregistration target declaration drifted"
            )
        observed_preimplementation[relative] = digest
    if (
        document.get("status") != "PREREGISTERED_BEFORE_TIMING_SEAM_IMPLEMENTATION"
        or observed_preimplementation != TIMING_PREIMPLEMENTATION_SHA256
    ):
        raise RehearsalV21ValidationError("prediction timing preregistration contract drifted")
    status = _git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        TIMING_PREREGISTRATION_RELATIVE.as_posix(),
    )
    if status:
        raise RehearsalV21ValidationError("prediction timing preregistration is dirty")
    history = _git(
        root,
        "log",
        "--first-parent",
        "--diff-merges=first-parent",
        "--format=@@%H",
        "--name-status",
        "--find-renames",
        "--find-copies",
        "--",
        TIMING_PREREGISTRATION_RELATIVE.as_posix(),
    ).decode("utf-8", errors="strict")
    touches: list[tuple[str, str, tuple[str, ...]]] = []
    active_commit: str | None = None
    for raw_line in history.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("@@"):
            active_commit = line[2:]
            continue
        if active_commit is None:
            raise RehearsalV21ValidationError("prediction timing Git history is malformed")
        fields = tuple(line.split("\t"))
        if len(fields) < 2:
            raise RehearsalV21ValidationError("prediction timing Git history is malformed")
        touches.append((active_commit, fields[0], fields[1:]))
    if touches != [
        (
            TIMING_PREREGISTRATION_COMMIT,
            "A",
            (TIMING_PREREGISTRATION_RELATIVE.as_posix(),),
        )
    ]:
        raise RehearsalV21ValidationError(
            "prediction timing preregistration is not one unique status-A touch"
        )
    _require_ancestor(
        root,
        TIMING_PREREGISTRATION_COMMIT,
        implementation_commit,
        "prediction timing implementation",
    )
    if (
        _commit_blob(
            root,
            TIMING_PREREGISTRATION_COMMIT,
            TIMING_PREREGISTRATION_RELATIVE.as_posix(),
        )
        != payload
        or _commit_blob(
            root,
            implementation_commit,
            TIMING_PREREGISTRATION_RELATIVE.as_posix(),
        )
        != payload
    ):
        raise RehearsalV21ValidationError(
            "prediction timing preregistration differs from creation or implementation blob"
        )
    for relative, preimplementation_sha in TIMING_PREIMPLEMENTATION_SHA256.items():
        creation_blob = _commit_blob(root, TIMING_PREREGISTRATION_COMMIT, relative)
        implementation_blob = _commit_blob(root, implementation_commit, relative)
        current = _regular_bytes(
            _safe_path(root, relative, f"current prediction timing control {relative}"),
            f"current prediction timing control {relative}",
        )
        if (
            _sha256(creation_blob) != preimplementation_sha
            or implementation_blob == creation_blob
            or current != implementation_blob
        ):
            raise RehearsalV21ValidationError(
                f"prediction timing implementation ordering or bytes drifted: {relative}"
            )


def _schema_validate(bundle: JsonObject, schema: JsonObject) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(bundle), key=lambda error: list(error.path))
    except Exception as exc:
        if isinstance(exc, RehearsalV21ValidationError):
            raise
        raise RehearsalV21ValidationError("bundle schema could not be evaluated") from exc
    if errors:
        first = errors[0]
        path = ".".join(str(item) for item in first.path) or "$"
        raise RehearsalV21ValidationError(f"bundle schema rejected {path}: {first.message}")


def _filesystem_inventory(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not (
            stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)
        ):
            raise RehearsalV21ValidationError(
                f"bundle contains a symlink or special entry: {relative}"
            )
        if stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1:
                raise RehearsalV21ValidationError(f"bundle file is hard-linked: {relative}")
            files.add(relative)
        else:
            directories.add(relative)
    if len({item.casefold() for item in files | directories}) != len(files | directories):
        raise RehearsalV21ValidationError("bundle inventory contains a casefold collision")
    return files, directories


def _expected_directories(files: set[str]) -> set[str]:
    result: set[str] = set()
    for relative in files:
        parent = PurePosixPath(relative).parent
        while parent != PurePosixPath("."):
            result.add(parent.as_posix())
            parent = parent.parent
    return result


def _tree_fingerprint(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = f"symlink:{path.readlink()}"
        elif path.is_file():
            result[relative] = f"file:{_sha256(path.read_bytes())}"
        elif path.is_dir():
            result[relative] = "directory"
        else:
            result[relative] = "special"
    return result


def _source_kind(relative: str, closure: set[str]) -> str:
    if relative in closure:
        return "package_initializer" if relative.endswith("/__init__.py") else "python_source"
    if relative == "pyproject.toml":
        return "project_manifest"
    if relative == "uv.lock":
        return "lockfile"
    return "frozen_control"


def _validate_runtime_start_control_flow(repository_payloads: Mapping[str, bytes]) -> None:
    relative = "scripts/prepare_p4_2a_v2_heldout.py"
    payload = repository_payloads.get(relative)
    if payload is None:
        raise RehearsalV21ValidationError("archived prepare source is unavailable")
    try:
        tree = ast.parse(payload, filename=relative)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise RehearsalV21ValidationError("archived prepare source is not parseable") from exc
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "run_materialize"
    ]
    if len(functions) != 1 or not functions[0].body:
        raise RehearsalV21ValidationError("run_materialize control flow is unavailable")
    function = functions[0]
    first = function.body[0]
    if (
        not isinstance(first, ast.Assign)
        or not isinstance(first.value, ast.Call)
        or not isinstance(first.value.func, ast.Name)
        or first.value.func.id != "validate_v2_1_stage_authorization"
    ):
        raise RehearsalV21ValidationError("run_materialize does not gate at its first statement")
    calls: dict[str, int] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.setdefault(node.func.id, node.lineno)
    ordered = (
        "validate_v2_1_stage_authorization",
        "_real_runtime_start_preflight",
        "_window_rows",
        "_materialization_design",
        "_publish_create_only",
    )
    if any(name not in calls for name in ordered) or [calls[name] for name in ordered] != sorted(
        calls[name] for name in ordered
    ):
        raise RehearsalV21ValidationError(
            "run_materialize gate/runtime/window/effect order drifted"
        )


def _v2_1_module_name(relative: str) -> tuple[str, str]:
    path = PurePosixPath(relative)
    components = list(path.with_suffix("").parts)
    if components and components[0] == "src" and len(components) > 1:
        components = components[1:]
    if not components or components[0] not in {"scripts", "alphapilot"}:
        raise RehearsalV21ValidationError(
            f"archived Python source is outside local namespaces: {relative}"
        )
    is_package = components[-1] == "__init__"
    if is_package:
        components.pop()
    module = ".".join(components)
    return module, module if is_package else module.rpartition(".")[0]


def _v2_1_module_candidates(module: str) -> tuple[str, str] | tuple[()]:
    if module in {"scripts", "alphapilot"}:
        stem = "scripts" if module == "scripts" else "src/alphapilot"
        return (f"{stem}/__init__.py", f"{stem}.py")
    if module.startswith("scripts."):
        stem = "scripts/" + module.removeprefix("scripts.").replace(".", "/")
    elif module.startswith("alphapilot."):
        stem = "src/alphapilot/" + module.removeprefix("alphapilot.").replace(".", "/")
    else:
        return ()
    return (f"{stem}.py", f"{stem}/__init__.py")


def _v2_1_resolve_archived_module(
    blobs: Mapping[str, bytes],
    module: str,
) -> str | None:
    candidates = [candidate for candidate in _v2_1_module_candidates(module) if candidate in blobs]
    if len(candidates) > 1:
        raise RehearsalV21ValidationError(f"ambiguous archived local module: {module}")
    return candidates[0] if candidates else None


def _v2_1_resolve_import_from(package: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    components = package.split(".") if package else []
    remove = node.level - 1
    if remove > len(components):
        raise RehearsalV21ValidationError("relative local import escapes its package")
    prefix = components[: len(components) - remove]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def _v2_1_archived_ancestor_initializers(
    blobs: Mapping[str, bytes],
    relative: str,
) -> set[str]:
    path = PurePosixPath(relative)
    minimum = 1 if path.parts[0] == "scripts" else 2
    result: set[str] = set()
    for length in range(minimum, len(path.parent.parts) + 1):
        candidate = PurePosixPath(
            *path.parent.parts[:length],
            "__init__.py",
        ).as_posix()
        if candidate in blobs:
            result.add(candidate)
    return result


def _v2_1_ast_local_import_closure(blobs: Mapping[str, bytes]) -> set[str]:
    """Rebuild the v2.1 closure solely from archived source bytes."""

    entrypoint = "scripts/rehearse_p4_2a_v2_1_heldout_full_path.py"
    if entrypoint not in blobs:
        raise RehearsalV21ValidationError("archived v2.1 AST entrypoint is missing")
    pending = [entrypoint]
    closure: set[str] = set()
    while pending:
        relative = pending.pop(0)
        if relative in closure:
            continue
        payload = blobs.get(relative)
        if payload is None:
            raise RehearsalV21ValidationError(f"archived local module is missing: {relative}")
        try:
            tree = ast.parse(payload, filename=relative)
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise RehearsalV21ValidationError(
                f"archived local Python source cannot be parsed: {relative}"
            ) from exc
        closure.add(relative)
        _module, package = _v2_1_module_name(relative)
        discovered: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    if module not in {"scripts", "alphapilot"} and not module.startswith(
                        ("scripts.", "alphapilot.")
                    ):
                        continue
                    target = _v2_1_resolve_archived_module(blobs, module)
                    if target is None:
                        raise RehearsalV21ValidationError(
                            f"unresolved archived local import is forbidden: {relative}: {module}"
                        )
                    discovered.add(target)
                    discovered.update(_v2_1_archived_ancestor_initializers(blobs, target))
            elif isinstance(node, ast.ImportFrom):
                base = _v2_1_resolve_import_from(package, node)
                if base in {"scripts", "alphapilot"} or base.startswith(
                    ("scripts.", "alphapilot.")
                ):
                    base_target = _v2_1_resolve_archived_module(blobs, base)
                    if base_target is None and base != "scripts":
                        raise RehearsalV21ValidationError(
                            "unresolved archived local from-import base is forbidden: "
                            f"{relative}: {base}"
                        )
                    if base_target is not None:
                        discovered.add(base_target)
                        discovered.update(
                            _v2_1_archived_ancestor_initializers(blobs, base_target)
                        )
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        module = f"{base}.{alias.name}" if base else alias.name
                        target = _v2_1_resolve_archived_module(blobs, module)
                        if target is None:
                            continue
                        discovered.add(target)
                        discovered.update(
                            _v2_1_archived_ancestor_initializers(blobs, target)
                        )
            elif isinstance(node, ast.Call):
                call_name = ""
                if isinstance(node.func, ast.Name):
                    call_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    call_name = node.func.attr
                target = (
                    node.args[0].value
                    if node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    else None
                )
                if call_name in {"__import__", "import_module"} and target is None:
                    raise RehearsalV21ValidationError(
                        f"non-literal dynamic import is forbidden: {relative}"
                    )
                if (
                    call_name in {"__import__", "import_module"}
                    and isinstance(target, str)
                    and (
                        target in {"scripts", "alphapilot"}
                        or target.startswith(("scripts.", "alphapilot."))
                    )
                ):
                    raise RehearsalV21ValidationError(
                        f"dynamic local import is forbidden: {relative}"
                    )
        pending.extend(sorted(discovered - closure, key=lambda item: item.encode("utf-8")))
    required = {
        "scripts/p4_2a_v2_dev_common.py",
        "scripts/validate_p4_2a_v2_1_heldout_rehearsal_bundle.py",
    }
    missing_required = required - closure
    if missing_required:
        raise RehearsalV21ValidationError(
            "required v2.1 source is absent from AST closure: "
            + ", ".join(sorted(missing_required))
        )
    return closure


def _runtime_inventory_v2_1(project_root: Path) -> tuple[bytes, bytes, list[str], int]:
    root = project_root.resolve()
    python_payload = _canonical_json_bytes(
        {
            "abi_flags": sys.abiflags,
            "cache_tag": sys.implementation.cache_tag,
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        }
    )
    if _sha256(python_payload) != runner.PYTHON_INVENTORY_SHA256:
        raise RehearsalV21ValidationError("active Python runtime inventory drifted")
    venv_root = root / ".venv"
    scheme = sysconfig.get_preferred_scheme("prefix")
    variables = {"base": venv_root.as_posix(), "platbase": venv_root.as_posix()}
    selected: list[Path] = []
    for key in ("purelib", "platlib"):
        raw = sysconfig.get_path(key, scheme=scheme, vars=variables)
        if not isinstance(raw, str) or not raw:
            raise RehearsalV21ValidationError(
                f"explicit sysconfig package root is unavailable: {key}"
            )
        candidate = Path(raw).absolute()
        if candidate not in selected:
            selected.append(candidate)
    projected: list[str] = []
    for package_root in selected:
        try:
            metadata = package_root.lstat()
        except OSError as exc:
            raise RehearsalV21ValidationError(
                "fixed package metadata root is unavailable"
            ) from exc
        if (
            package_root.resolve(strict=True) != package_root
            or package_root.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or not package_root.is_relative_to(root)
        ):
            raise RehearsalV21ValidationError("fixed package metadata root is aliased")
        projected.append(package_root.relative_to(root).as_posix())
    if projected != [runner.PACKAGE_ROOT_RELATIVE.as_posix()]:
        raise RehearsalV21ValidationError(
            "explicit sysconfig package root projection drifted"
        )
    if _sha256(_canonical_json_bytes(projected)) != runner.PACKAGE_ROOTS_SHA256:
        raise RehearsalV21ValidationError("fixed package metadata root binding drifted")
    distributions = list(
        importlib.metadata.distributions(path=[path.as_posix() for path in selected])
    )
    raw_rows: list[tuple[str, str]] = []
    for distribution in distributions:
        raw_name = distribution.metadata["Name"]
        if not isinstance(raw_name, str) or not raw_name:
            raise RehearsalV21ValidationError(
                "active package inventory contains an unnamed distribution"
            )
        raw_rows.append((raw_name, distribution.version))
    rows = _normalize_distribution_rows_v2_1(raw_rows)
    if len(rows) != 84:
        raise RehearsalV21ValidationError(
            "active package inventory count drifted"
        )
    package_payload = _canonical_json_bytes(rows)
    if _sha256(package_payload) != runner.PACKAGE_INVENTORY_SHA256:
        raise RehearsalV21ValidationError("active package inventory bytes drifted")
    return python_payload, package_payload, projected, len(distributions)


def _normalize_distribution_rows_v2_1(
    raw_rows: Sequence[tuple[str, str]],
) -> list[JsonObject]:
    rows: list[JsonObject] = []
    names: list[str] = []
    for raw_name, version in raw_rows:
        if not raw_name or not version:
            raise RehearsalV21ValidationError(
                "package inventory contains an unnamed or unversioned distribution"
            )
        name = re.sub(r"[-_.]+", "-", raw_name).lower()
        names.append(name)
        rows.append({"name": name, "version": version})
    if len(names) != len(set(names)):
        raise RehearsalV21ValidationError(
            "package inventory has duplicate normalized names"
        )
    rows.sort(key=lambda row: (cast(str, row["name"]), cast(str, row["version"])))
    return rows


def _require_duplicate_package_negative_probe_v2_1() -> None:
    try:
        _normalize_distribution_rows_v2_1(
            [("validator_probe.pkg", "1"), ("validator-probe-pkg", "2")]
        )
    except RehearsalV21ValidationError as exc:
        if "duplicate normalized names" not in str(exc):
            raise RehearsalV21ValidationError(
                "v2.1 duplicate package probe failed for the wrong reason"
            ) from exc
        return
    else:
        raise RehearsalV21ValidationError(
            "v2.1 duplicate package normalization negative probe was accepted"
        )


def registered_rehearsal_directory(project_root: Path = PROJECT_ROOT) -> Path:
    directory: Path = runner.registered_rehearsal_directory(project_root)
    return directory


def _validate_materialization_manifest(payload: bytes, *, implementation_commit: str) -> None:
    manifest = _object(strict_json_loads(payload, source="materialization manifest"), "manifest")
    if manifest.get("schema_version") != "p4.2a-v2-heldout-materialization-manifest-v2":
        raise RehearsalV21ValidationError("materialization manifest is not successor v2")
    authority = _object(manifest.get("execution_authority"), "manifest.execution_authority")
    if (
        authority.get("mode") != "offline_rehearsal"
        or authority.get("rehearsal_bundle") is not None
        or authority.get("release_authorization") is not None
        or authority.get("implementation_commit") != implementation_commit
        or authority.get("preregistration_commit") != runner.PREREGISTRATION_COMMIT
    ):
        raise RehearsalV21ValidationError("offline execution_authority is recursive or drifted")
    pacing = _object(manifest.get("request_pacing"), "manifest.request_pacing")
    cninfo = _object(pacing.get("cninfo_pdf"), "manifest.request_pacing.cninfo_pdf")
    expected = {
        "host": "static.cninfo.com.cn",
        "policy": "minimum_start_to_start",
        "configured_min_start_to_start_seconds": 1.0,
        "clock": "monotonic",
        "first_request_delayed": False,
        "request_start_count": runner.CNINFO_REQUEST_COUNT,
        "observed_gap_count": runner.CNINFO_GAP_COUNT,
        "minimum_observed_start_to_start_seconds": 1.0,
        "median_observed_start_to_start_seconds": 1.0,
        "violation_count": 0,
        "retry_count": 0,
    }
    if cninfo != expected:
        raise RehearsalV21ValidationError("CNInfo pacing evidence drifted")
    if (
        pacing.get("akshare_ths") != "not_applicable_no_external_document_fetch"
        or pacing.get("sina_company_news") != "not_applicable_no_external_document_fetch"
    ):
        raise RehearsalV21ValidationError("non-CNInfo request pacing evidence drifted")
    runtime = _object(manifest.get("runtime_start_preflight"), "runtime_start_preflight")
    if runtime != {
        "mode": "offline_rehearsal",
        "host_probe_performed": False,
        "reason": "not_applicable_offline_rehearsal",
    }:
        raise RehearsalV21ValidationError("offline runtime-start evidence drifted")


def _validate_artifact_semantics(
    payloads: Mapping[str, bytes], *, implementation_commit: str
) -> None:
    manifest_path = dict(runner.ARTIFACT_INVENTORY)["materialization_manifest"]
    _validate_materialization_manifest(
        payloads[manifest_path], implementation_commit=implementation_commit
    )
    inputs_path = dict(runner.ARTIFACT_INVENTORY)["materialized_inputs"]
    inference_state_path = dict(runner.ARTIFACT_INVENTORY)["inference_state"]
    predictions_path = dict(runner.ARTIFACT_INVENTORY)["predictions"]
    selection_path = dict(runner.ARTIFACT_INVENTORY)["private_selection"]
    blind_path = dict(runner.ARTIFACT_INVENTORY)["owner_blind"]
    draft_path = dict(runner.ARTIFACT_INVENTORY)["ai_draft"]
    inputs = [
        _object(strict_json_loads(line, source="candidate input"), "candidate input")
        for line in payloads[inputs_path].splitlines()
        if line
    ]
    predictions = [
        _object(strict_json_loads(line, source="prediction"), "prediction")
        for line in payloads[predictions_path].splitlines()
        if line
    ]
    _validate_prediction_timing(payloads[inference_state_path], predictions)
    blind = [
        _object(strict_json_loads(line, source="blind row"), "blind row")
        for line in payloads[blind_path].splitlines()
        if line
    ]
    drafts = [
        _object(strict_json_loads(line, source="draft row"), "draft row")
        for line in payloads[draft_path].splitlines()
        if line
    ]
    selection = _object(
        strict_json_loads(payloads[selection_path], source="selection"), "selection"
    )
    counts = _object(
        _object(selection.get("selection"), "selection.selection").get("selected_counts"),
        "selected_counts",
    )
    if (
        len(inputs) != runner.FIXTURE_RAW_COUNT
        or len(predictions) != runner.FIXTURE_RAW_COUNT
        or len(blind) != 60
        or len(drafts) != 60
        or counts.get("predicted_positive") != 40
        or counts.get("predicted_negative") != 20
        or counts.get("extract_failed") not in {None, 0}
        or counts.get("total") != 60
    ):
        raise RehearsalV21ValidationError("full-pool inference or 40/20 selection drifted")
    forbidden = ("prediction", "stratum", "rank", "score", "sampling", "selection")

    def leak(value: object) -> bool:
        if isinstance(value, dict):
            return any(
                any(token in str(key).casefold().replace("-", "_") for token in forbidden)
                or leak(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(leak(item) for item in value)
        return False

    if any(leak(row) or row.get("gold") not in ({}, None) for row in blind):
        raise RehearsalV21ValidationError("blind artifact leaks held-out selection data")
    if any(row.get("drafter_id") != heldout_drafter_id() for row in drafts):
        raise RehearsalV21ValidationError("AI draft independence identity drifted")


def _aware_utc_instant(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise RehearsalV21ValidationError(f"{label} is not a UTC timestamp")
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RehearsalV21ValidationError(f"{label} is not ISO-8601") from exc
    if (
        observed.tzinfo is None
        or observed.utcoffset() is None
        or observed.utcoffset() != UTC.utcoffset(observed)
    ):
        raise RehearsalV21ValidationError(f"{label} is not timezone-aware UTC")
    return observed.astimezone(UTC)


def _validate_prediction_timing(
    inference_state_payload: bytes,
    predictions: Sequence[Mapping[str, Any]],
) -> None:
    states = [
        _object(strict_json_loads(line, source="inference state"), "inference state")
        for line in inference_state_payload.splitlines()
        if line
    ]
    if len(states) != 2:
        raise RehearsalV21ValidationError("inference state is not exactly two events")
    started, completed = states
    if (
        started.get("status") != "inference_started"
        or completed.get("status") != "completed_all_eligible_candidates_once"
        or started.get("execution_id") != completed.get("execution_id")
    ):
        raise RehearsalV21ValidationError("inference state event sequence drifted")
    started_at = _aware_utc_instant(started.get("started_at_utc"), "inference start")
    completed_at = _aware_utc_instant(
        completed.get("completed_at_utc"),
        "inference completion",
    )
    fixed = _aware_utc_instant(runner.FIXED_WALL_CLOCK_TEXT, "fixed rehearsal clock")
    if started_at != fixed or completed_at != fixed or completed_at < started_at:
        raise RehearsalV21ValidationError("inference state timing differs from fixed rehearsal")
    previous = started_at
    for index, prediction in enumerate(predictions, 1):
        recorded = _aware_utc_instant(
            prediction.get("recorded_at_utc"),
            f"prediction {index} recorded_at",
        )
        latency = prediction.get("latency_ms")
        if (
            recorded < started_at
            or recorded > completed_at
            or recorded < previous
            or recorded != fixed
            or isinstance(latency, bool)
            or latency != 0
        ):
            raise RehearsalV21ValidationError(
                "prediction timing is outside the state interval or has nonzero latency"
            )
        previous = recorded


def heldout_drafter_id() -> str:
    return "OpenAI Codex GPT-5"


def _validate_bundle_once(project_root: Path, bundle_path: Path) -> JsonObject:
    """Fully rehash and semantically validate one bundle without replay side effects."""

    root = project_root.resolve()
    path = bundle_path.absolute()
    if path.name != BUNDLE_FILENAME:
        raise RehearsalV21ValidationError("bundle_path must name bundle.json")
    directory = path.parent
    if path.is_symlink() or directory.is_symlink() or not directory.is_dir():
        raise RehearsalV21ValidationError("bundle authority is not one regular directory")
    if path.resolve(strict=False) != path:
        raise RehearsalV21ValidationError("bundle path contains a symlink or alias")

    prereg_payload = _bound_repository_file(
        root,
        runner.PREREGISTRATION_RELATIVE.as_posix(),
        runner.PREREGISTRATION_SHA256,
        "successor preregistration",
    )
    schema_payload = _bound_repository_file(
        root,
        runner.BUNDLE_SCHEMA_RELATIVE.as_posix(),
        runner.BUNDLE_SCHEMA_SHA256,
        "bundle schema",
    )
    _bound_repository_file(
        root,
        runner.RELEASE_SCHEMA_RELATIVE.as_posix(),
        runner.RELEASE_SCHEMA_SHA256,
        "release schema",
    )
    prereg = _object(strict_json_loads(prereg_payload, source="preregistration"), "prereg")
    if prereg.get("status") != "PREREGISTERED_NOT_IMPLEMENTED_NOT_EXECUTED":
        raise RehearsalV21ValidationError("successor preregistration status drifted")
    schema = _object(strict_json_loads(schema_payload, source="bundle schema"), "schema")
    bundle_payload = _regular_bytes(path, "bundle manifest")
    bundle = _object(strict_json_loads(bundle_payload, source="bundle manifest"), "bundle")
    _schema_validate(bundle, schema)

    lineage = _object(bundle.get("lineage"), "bundle.lineage")
    exact_refs = {
        "preregistration": (
            runner.PREREGISTRATION_RELATIVE.as_posix(),
            runner.PREREGISTRATION_SHA256,
        ),
        "bundle_schema": (
            runner.BUNDLE_SCHEMA_RELATIVE.as_posix(),
            runner.BUNDLE_SCHEMA_SHA256,
        ),
        "release_authorization_schema": (
            runner.RELEASE_SCHEMA_RELATIVE.as_posix(),
            runner.RELEASE_SCHEMA_SHA256,
        ),
    }
    for name, (relative, digest) in exact_refs.items():
        if _object(lineage.get(name), f"lineage.{name}") != {
            "path": relative,
            "sha256": digest,
        }:
            raise RehearsalV21ValidationError(f"lineage.{name} drifted")
    if lineage.get("preregistration_commit") != runner.PREREGISTRATION_COMMIT:
        raise RehearsalV21ValidationError("preregistration commit binding drifted")
    implementation_commit = _commit(
        root, lineage.get("implementation_commit"), "implementation commit"
    )
    _validate_implementation_commit_surface(root, implementation_commit, prereg)
    _validate_prediction_timing_control(root, implementation_commit)
    _require_ancestor(root, runner.PREREGISTRATION_COMMIT, implementation_commit, "preregistration")
    _require_ancestor(
        root,
        runner.PREDICTION_TIMING_PREREG_COMMIT,
        implementation_commit,
        "prediction timing preregistration",
    )
    _require_ancestor(root, runner.V1_FAIL_CLOSE_COMMIT, implementation_commit, "v1 fail-close")
    try:
        git_binding = runner._git_binding_for_commit(root, implementation_commit)
    except RehearsalV21ValidationError:
        raise
    except Exception as exc:
        raise RehearsalV21ValidationError(
            "prediction timing preregistration control binding failed"
        ) from exc

    expected_files = {BUNDLE_FILENAME}
    archive = _object(bundle.get("archive"), "bundle.archive")
    run_records = _array(archive.get("runs"), "archive.runs")
    run_payloads: list[dict[str, bytes]] = []
    run_roots: list[str] = []
    expected_inventory = list(runner.ARTIFACT_INVENTORY)
    for index, raw_run in enumerate(run_records):
        record = _object(raw_run, f"run {index}")
        archive_root = _relative(record.get("archive_root"), "archive_root")
        artifact_records = _array(record.get("artifacts"), "artifacts")
        payloads: dict[str, bytes] = {}
        observed_inventory: list[tuple[str, str]] = []
        for raw_artifact in artifact_records:
            artifact = _object(raw_artifact, "artifact")
            logical = cast(str, artifact.get("logical_name"))
            relative = _relative(artifact.get("source_relative_path"), "source_relative_path")
            observed_inventory.append((logical, relative))
            physical = f"{archive_root}/{relative}"
            payload = _regular_bytes(_safe_path(directory, physical, "artifact"), "artifact")
            if artifact.get("bytes") != len(payload) or artifact.get("sha256") != _sha256(payload):
                raise RehearsalV21ValidationError(f"artifact bytes drifted: {logical}")
            payloads[relative] = payload
            expected_files.add(physical)
        if observed_inventory != expected_inventory or len(payloads) != 14:
            raise RehearsalV21ValidationError("artifact inventory is not the exact registered 14")
        root_digest = runner._merkle_root(payloads)
        if root_digest != record.get("artifact_merkle_root_sha256"):
            raise RehearsalV21ValidationError("run Merkle root drifted")
        run_payloads.append(payloads)
        run_roots.append(root_digest)
    if len(run_payloads) != 2 or run_payloads[0] != run_payloads[1]:
        raise RehearsalV21ValidationError("dual runs are not 14/14 byte-identical")

    control = _object(archive.get("control_surface"), "control_surface")
    control_records = _array(control.get("files"), "control files")
    repository_payloads: dict[str, bytes] = {}
    control_payloads: dict[str, bytes] = {}
    python_paths: set[str] = set()
    prior: bytes | None = None
    for raw_record in control_records:
        record = _object(raw_record, "control record")
        bundle_relative = _relative(record.get("bundle_relative_path"), "control path")
        encoded = bundle_relative.encode("utf-8")
        if prior is not None and encoded <= prior:
            raise RehearsalV21ValidationError("control records are not strict path order")
        prior = encoded
        payload = _regular_bytes(_safe_path(directory, bundle_relative, "control"), "control")
        if record.get("bytes") != len(payload) or record.get("sha256") != _sha256(payload):
            raise RehearsalV21ValidationError("control byte binding drifted")
        kind = record.get("source_kind")
        if kind in REPOSITORY_SOURCE_KINDS:
            repository_relative = _relative(record.get("repository_path"), "repository_path")
            if bundle_relative != f"archive/control-surface/root/repo/{repository_relative}":
                raise RehearsalV21ValidationError("repository control projection drifted")
            if _commit_blob(root, implementation_commit, repository_relative) != payload:
                raise RehearsalV21ValidationError("control differs from implementation commit")
            current = _regular_bytes(
                _safe_path(root, repository_relative, "current control"),
                "current control",
            )
            if current != payload:
                raise RehearsalV21ValidationError("current source differs from archived control")
            repository_payloads[repository_relative] = payload
            if kind in PYTHON_SOURCE_KINDS:
                python_paths.add(repository_relative)
        elif kind not in RUNTIME_SOURCE_KINDS or record.get("repository_path") is not None:
            raise RehearsalV21ValidationError("control source kind drifted")
        control_payloads[bundle_relative] = payload
        expected_files.add(bundle_relative)

    _validate_runtime_start_control_flow(repository_payloads)

    manifest_relative = "archive/control-surface/manifest.json"
    manifest_payload = _regular_bytes(
        _safe_path(directory, manifest_relative, "control manifest"), "control manifest"
    )
    manifest_record = _object(control.get("manifest"), "control manifest record")
    if manifest_record.get("bytes") != len(manifest_payload) or manifest_record.get(
        "sha256"
    ) != _sha256(manifest_payload):
        raise RehearsalV21ValidationError("control manifest byte binding drifted")
    expected_manifest = {"schema_version": runner.CONTROL_MANIFEST_SCHEMA, "files": control_records}
    if strict_json_loads(manifest_payload, source="control manifest") != expected_manifest or (
        manifest_payload != _canonical_json_bytes(expected_manifest)
    ):
        raise RehearsalV21ValidationError("control manifest is not canonical")
    control_payloads[manifest_relative] = manifest_payload
    expected_files.add(manifest_relative)

    actual_files, actual_directories = _filesystem_inventory(directory)
    if actual_files != expected_files or actual_directories != _expected_directories(
        expected_files
    ):
        raise RehearsalV21ValidationError("bundle filesystem inventory drifted")

    try:
        closure = _v2_1_ast_local_import_closure(repository_payloads)
    except Exception as exc:
        raise RehearsalV21ValidationError("archived AST import closure is invalid") from exc
    if _VALIDATOR_REGISTERED_BOOTSTRAP:
        try:
            runner._require_loaded_repository_sources_in_closure(
                _VALIDATOR_REPOSITORY_MODULE_PATHS,
                closure,
            )
        except runner.RehearsalV21Error as exc:
            raise RehearsalV21ValidationError(
                "loaded validator sources are absent from the archived AST closure"
            ) from exc
    if closure != python_paths:
        raise RehearsalV21ValidationError("archived Python control set is not the AST closure")
    try:
        expected_repository_paths, _expected_closure = runner._registered_control_paths(
            root,
            git_binding,
        )
    except Exception as exc:
        raise RehearsalV21ValidationError("registered control-set derivation failed") from exc
    if set(repository_payloads) != expected_repository_paths:
        raise RehearsalV21ValidationError(
            "control surface is not the exact registered source/control set"
        )
    for relative, digest in runner.FROZEN_FILE_SHA256.items():
        payload = _bound_repository_file(root, relative, digest, f"frozen control {relative}")
        if repository_payloads.get(relative) != payload:
            raise RehearsalV21ValidationError(f"frozen control omitted: {relative}")
    try:
        declared = runner._declared_preregistration_hashes(root)
    except Exception as exc:
        raise RehearsalV21ValidationError(
            "preregistered byte-frozen reference validation failed"
        ) from exc
    for relative, digest in declared.items():
        payload = _bound_repository_file(
            root,
            relative,
            digest,
            f"preregistered byte-frozen control {relative}",
        )
        if repository_payloads.get(relative) != payload:
            raise RehearsalV21ValidationError(
                f"preregistered byte-frozen control omitted: {relative}"
            )

    environment = _object(bundle.get("execution_environment"), "execution_environment")
    try:
        python_payload, package_payload, projected_roots, raw_count = (
            _runtime_inventory_v2_1(runner.PROJECT_ROOT)
        )
        _require_duplicate_package_negative_probe_v2_1()
    except Exception as exc:
        raise RehearsalV21ValidationError("runtime inventory validation failed") from exc
    python_path = "archive/control-surface/root/runtime/python.json"
    package_path = "archive/control-surface/root/runtime/packages.json"
    if (
        control_payloads.get(python_path) != python_payload
        or control_payloads.get(package_path) != package_payload
    ):
        raise RehearsalV21ValidationError("archived runtime differs from active runtime")
    packages = _object(environment.get("packages"), "execution_environment.packages")
    if (
        packages.get("selected_path_roots_project_relative") != projected_roots
        or packages.get("raw_distribution_count") != raw_count
        or packages.get("count") != 84
        or packages.get("sha256") != _sha256(package_payload)
    ):
        raise RehearsalV21ValidationError("runtime environment evidence drifted")

    control_root = runner._merkle_root(control_payloads)
    merkle = _object(bundle.get("merkle"), "bundle.merkle")
    bundle_root = runner._bundle_root(run_roots[0], run_roots[1], control_root)
    if (
        control.get("merkle_root_sha256") != control_root
        or merkle.get("run_a_root_sha256") != run_roots[0]
        or merkle.get("run_b_root_sha256") != run_roots[1]
        or merkle.get("control_surface_root_sha256") != control_root
        or merkle.get("bundle_root_sha256") != bundle_root
    ):
        raise RehearsalV21ValidationError("v2.1 Merkle evidence drifted")

    _validate_artifact_semantics(run_payloads[0], implementation_commit=implementation_commit)
    safety = _object(bundle.get("safety"), "bundle.safety")
    blockers = _object(bundle.get("remaining_blockers"), "remaining_blockers")
    if (
        safety.get("real_database_reads") != 0
        or safety.get("real_network_calls") != 0
        or safety.get("real_model_calls") != 0
        or safety.get("production_writes") is not False
        or blockers.get("real_heldout_materialization_unlocked") is not False
        or blockers.get("real_heldout_inference_unlocked") is not False
        or blockers.get("heldout_metric_evaluation_unlocked") is not False
    ):
        raise RehearsalV21ValidationError("bundle safety or locks are not fail-closed")
    return bundle


@contextmanager
def _forbid_repository_database(project_root: Path) -> Iterator[None]:
    root = project_root.resolve()
    with runner._audited_execution(
        runner._AuditPolicy(
            project_root=root,
            write_roots=(),
            sqlite_roots=(),
            subprocess_mode="none",
        )
    ):
        try:
            yield
        except runner.RehearsalV21Error as exc:
            raise RehearsalV21ValidationError(
                "independent replay attempted a repository database open"
            ) from exc


def _archived_artifacts(bundle: Mapping[str, Any], directory: Path) -> dict[str, dict[str, bytes]]:
    archive = _object(bundle.get("archive"), "bundle.archive")
    result: dict[str, dict[str, bytes]] = {}
    for raw_run in _array(archive.get("runs"), "archive.runs"):
        run = _object(raw_run, "archive run")
        label = cast(str, run["run_label"])
        archive_root = cast(str, run["archive_root"])
        artifacts: dict[str, bytes] = {}
        for raw_artifact in _array(run.get("artifacts"), "archive artifacts"):
            artifact = _object(raw_artifact, "archive artifact")
            logical = cast(str, artifact["logical_name"])
            source_relative = cast(str, artifact["source_relative_path"])
            artifacts[logical] = _regular_bytes(
                _safe_path(
                    directory,
                    f"{archive_root}/{source_relative}",
                    f"archived {label} {logical}",
                ),
                f"archived {label} {logical}",
            )
        result[label] = artifacts
    return result


def _active_replay(
    *,
    project_root: Path,
    bundle_path: Path,
    bundle: JsonObject,
) -> None:
    """Independently re-execute both real-entry offline paths and every probe."""

    root = project_root.resolve()
    directory = bundle_path.parent
    lineage = _object(bundle.get("lineage"), "bundle.lineage")
    implementation_commit = cast(str, lineage["implementation_commit"])
    archived = _archived_artifacts(bundle, directory)
    protected = (root / "docs/phase4/eval/v2-calibration/heldout").resolve()
    protected_before = _tree_fingerprint(protected)
    bundle_before = _tree_fingerprint(directory)

    with (
        runner._isolated_temp_directory("alphapilot-p4-2a-v2-1-validator-run-a-") as run_a_root,
        runner._isolated_temp_directory("alphapilot-p4-2a-v2-1-validator-run-b-") as run_b_root,
    ):
        if (
            run_a_root == run_b_root
            or run_a_root.is_relative_to(root)
            or run_b_root.is_relative_to(root)
            or run_a_root.is_relative_to(directory)
            or run_b_root.is_relative_to(directory)
        ):
            raise RehearsalV21ValidationError("independent replay roots are not isolated")
        try:
            replay_a = runner._execute_temp_pipeline(
                label="run-a",
                project_root=root,
                workspace=run_a_root,
                implementation_commit=implementation_commit,
            )
            replay_b = runner._execute_temp_pipeline(
                label="run-b",
                project_root=root,
                workspace=run_b_root,
                implementation_commit=implementation_commit,
            )
        except Exception as exc:
            raise RehearsalV21ValidationError("independent full-path dual replay failed") from exc

    expected_probes = {
        "noop_sleeper",
        "deterministic_ineligible",
        "unexpected_failure",
        "operator_attestation",
        "runtime_start_gate",
        "seal_and_ui_gate",
        "finalize_real",
        "authority_gate",
        "execution_authority_modes",
    }
    if (
        replay_a.artifacts != archived.get("run-a")
        or replay_b.artifacts != archived.get("run-b")
        or replay_a.artifacts != replay_b.artifacts
        or set(replay_a.probes) != expected_probes
        or not all(replay_a.probes.values())
        or replay_b.probes
    ):
        raise RehearsalV21ValidationError(
            "independent replay differs from archived artifacts or probe outcomes"
        )
    if (
        _tree_fingerprint(protected) != protected_before
        or _tree_fingerprint(directory) != bundle_before
    ):
        raise RehearsalV21ValidationError(
            "independent replay mutated a protected repository or bundle path"
        )

    payloads = {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file() and path != bundle_path
    }
    try:
        evidence = runner._synthetic_release_receipt_probe(
            project_root=root,
            implementation_commit=implementation_commit,
            bundle=bundle,
            payloads=payloads,
        )
    except Exception as exc:
        raise RehearsalV21ValidationError(
            "independent synthetic release-receipt replay failed"
        ) from exc
    if not all(vars(evidence).values()):
        raise RehearsalV21ValidationError(
            "independent synthetic release-receipt evidence is incomplete"
        )


def _nested_synthetic_validation_allowed(
    *,
    state_matches: bool,
    root: Path,
    bundle_path: Path,
    outer_root: Path | None,
    policy: runner._AuditPolicy | None,
) -> bool:
    synthetic_root = policy.synthetic_git_root if policy is not None else None
    return bool(
        state_matches
        and outer_root is not None
        and root != runner.PROJECT_ROOT.resolve()
        and policy is not None
        and policy.subprocess_mode == "synthetic_git"
        and policy.project_root.resolve() == outer_root
        and synthetic_root is not None
        and root.is_relative_to(synthetic_root.resolve())
        and bundle_path
        == (root / runner.SUCCESSOR_DIRECTORY_RELATIVE / runner.BUNDLE_FILENAME).absolute()
        and (root / ".git").is_dir()
        and not (root / ".git").is_symlink()
    )


def _build_validate_bundle_api() -> Any:
    """Keep the sole recursion authority inside the canonical API closure."""

    recursion_authority = object()
    active_recursion: contextvars.ContextVar[tuple[object, Path] | None] = contextvars.ContextVar(
        "p4_2a_v2_1_internal_validation_recursion",
        default=None,
    )

    def validate_bundle(project_root: Path, bundle_path: Path) -> Mapping[str, Any]:
        """Read-only full rehash, semantic check and independent active replay."""

        root = project_root.resolve()
        path = bundle_path.absolute()
        if root == runner.PROJECT_ROOT.resolve():
            if _VALIDATOR_REGISTERED_BOOTSTRAP:
                _assert_registered_validator_environment()
            elif not (
                runner._REGISTERED_BOOTSTRAP
                and runner._early_runtime_is_locked()
                and tuple(sys.path) == tuple(runner._fixed_runtime_paths())
            ):
                raise RehearsalV21ValidationError(
                    "canonical bundle validation requires a locked registered process"
                )
        bundle = _validate_bundle_once(root, path)
        state = active_recursion.get()
        policy = runner._AUDIT_POLICY.get()
        nested_synthetic = _nested_synthetic_validation_allowed(
            state_matches=state is not None and state[0] is recursion_authority,
            root=root,
            bundle_path=path,
            outer_root=state[1] if state is not None else None,
            policy=policy,
        )
        if nested_synthetic:
            return bundle
        token = active_recursion.set((recursion_authority, root))
        try:
            with runner._temporary_authority_scope(
                project_root=root,
                forbidden_paths=(
                    path.parent,
                    root / "docs/phase4/eval/v2-calibration/heldout",
                ),
            ):
                _active_replay(
                    project_root=root,
                    bundle_path=path,
                    bundle=bundle,
                )
        finally:
            active_recursion.reset(token)
        return bundle

    return validate_bundle


validate_bundle = _build_validate_bundle_api()
del _build_validate_bundle_api


def _assert_registered_validator_environment() -> None:
    if (
        not _VALIDATOR_REGISTERED_BOOTSTRAP
        or not _validator_early_runtime_is_locked()
        or not _validator_direct_entry_is_locked()
        or not runner._early_runtime_is_locked()
        or tuple(sys.path) != tuple(runner._fixed_runtime_paths())
    ):
        raise RehearsalV21ValidationError(
            "registered v2.1 validator requires the exact locked interpreter environment"
        )
    configured = sysconfig.get_paths()
    try:
        current_repository_modules = runner._classify_loaded_module_origins(
            modules=sys.modules,
            repository_root=PROJECT_ROOT,
            runner_path=Path(__file__),
            site_root=Path(_validator_site_packages),
            stdlib_roots=(
                Path(configured["stdlib"]),
                Path(configured["platstdlib"]),
            ),
        )
    except RuntimeError as exc:
        raise RehearsalV21ValidationError(
            "registered v2.1 validator module origin drifted"
        ) from exc
    if current_repository_modules != _VALIDATOR_REPOSITORY_MODULE_PATHS:
        raise RehearsalV21ValidationError(
            "registered v2.1 validator loaded repository module set drifted"
        )


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description=__doc__)


def _validator_result(bundle: Mapping[str, Any], *, bundle_sha256: str) -> JsonObject:
    lineage = _object(bundle.get("lineage"), "lineage")
    return {
        "schema_version": VALIDATOR_RESULT_SCHEMA,
        "status": "PASS_REHEARSAL_V2_1_AWAITING_OWNER_REVIEW",
        "bundle_path": (runner.SUCCESSOR_DIRECTORY_RELATIVE / BUNDLE_FILENAME).as_posix(),
        "bundle_sha256": bundle_sha256,
        "bundle_root_sha256": _object(bundle.get("merkle"), "merkle")[
            "bundle_root_sha256"
        ],
        "implementation_commit": lineage["implementation_commit"],
        "real_heldout_materialization_unlocked": False,
        "heldout_metric_evaluation_unlocked": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        _assert_registered_validator_environment()
    except RehearsalV21ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    _parser().parse_args(argv)
    bundle_path = registered_rehearsal_directory(PROJECT_ROOT) / BUNDLE_FILENAME
    try:
        bundle = validate_bundle(PROJECT_ROOT, bundle_path)
        bundle_sha256 = _sha256(_regular_bytes(bundle_path, "registered bundle"))
    except (OSError, RehearsalV21ValidationError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(
        _canonical_json_bytes(_validator_result(bundle, bundle_sha256=bundle_sha256))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
