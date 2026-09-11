#!/usr/bin/env python3
"""Create the preregistered P4.2a successor-v2.1 offline rehearsal bundle.

The registered CLI is deliberately create-only and has no path, Git, database,
network, model, clock, or validation override.  Unit tests exercise the same
implementation only through isolated temporary roots and explicit in-process
dependencies.
"""

# ruff: noqa: E402

from __future__ import annotations

import os
import sys

# The registered CLI fixes the executable, process-global nondeterminism, import
# roots, and an import-time effect guard before any shadowable import.  Normal
# diagnostic/test imports do not re-exec.
_EARLY_ENVIRONMENT_MARKER = "ALPHAPILOT_P42A_REHEARSAL_V2_1_ENV_LOCKED"
_EARLY_PROJECT_ROOT_TEXT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
_EARLY_PYTHON_EXECUTABLE_TEXT = os.path.join(
    _EARLY_PROJECT_ROOT_TEXT,
    ".venv/bin/python",
)
_EARLY_PYTHON_EXECUTABLE_SHA256 = (
    "f4cd716d4b54f205398bec6932cc59361b087494ca2ddb157a5e8631d4d6f863"
)
_EARLY_ORIG_ARGV_EXECUTABLE_SHA256 = (
    "89c717ced41f6a395612366e5b038226d0d8fca36bbddd9321d385f5f370ebbe"
)
_EARLY_LOCKED_ENVIRONMENT = {
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
_EARLY_EXEC_ENVIRONMENT = {
    **_EARLY_LOCKED_ENVIRONMENT,
    "__CF_USER_TEXT_ENCODING": f"0x{os.getuid():X}:0x0:0x0",
    "PATH": "/usr/bin:/bin",
    _EARLY_ENVIRONMENT_MARKER: "1",
}


def _early_sha256(path: str) -> str:
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
    descriptor = os.open(path, os.O_RDONLY)
    try:
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, 131072)
            if not chunk:
                break
            payload.extend(chunk)
    finally:
        os.close(descriptor)
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


def _early_fixed_python_executable() -> str:
    launcher = os.path.abspath(_EARLY_PYTHON_EXECUTABLE_TEXT)
    executable = os.path.realpath(launcher)
    try:
        metadata = os.lstat(executable)
    except OSError as exc:
        raise RuntimeError("registered v2.1 Python executable is unavailable") from exc
    if (
        metadata.st_mode & 0o170000 != 0o100000
        or os.path.islink(executable)
        or _early_sha256(executable) != _EARLY_PYTHON_EXECUTABLE_SHA256
    ):
        raise RuntimeError("registered v2.1 Python executable drifted")
    return launcher


def _early_runtime_is_locked() -> bool:
    return (
        dict(os.environ) == _EARLY_EXEC_ENVIRONMENT
        and sys.flags.hash_randomization == 0
        and sys.flags.no_site == 1
        and sys.flags.no_user_site == 1
        and sys.flags.safe_path
        and sys.dont_write_bytecode
        and sys.pycache_prefix == "/dev/null"
        and os.path.abspath(sys.executable) == _early_fixed_python_executable()
    )


def _early_orig_argv_executable() -> str:
    """Return the frozen macOS CPython application executable recorded in orig_argv."""

    executable = os.path.join(
        sys.base_prefix,
        "Resources/Python.app/Contents/MacOS/Python",
    )
    try:
        metadata = os.lstat(executable)
    except OSError as exc:
        raise RuntimeError("registered v2.1 orig_argv executable is unavailable") from exc
    if (
        metadata.st_mode & 0o170000 != 0o100000
        or os.path.islink(executable)
        or _early_sha256(executable) != _EARLY_ORIG_ARGV_EXECUTABLE_SHA256
    ):
        raise RuntimeError("registered v2.1 orig_argv executable drifted")
    return executable


def _early_runner_entry_is_locked() -> bool:
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    expected = (
        _early_orig_argv_executable(),
        "-S",
        "-P",
        "-B",
        os.path.realpath(__file__),
        *sys.argv[1:],
    )
    return (
        isinstance(main_file, str)
        and os.path.realpath(main_file) == os.path.realpath(__file__)
        and tuple(sys.orig_argv) == expected
    )


if (
    __name__ == "__main__"
    and sys.argv[1:] == ["--execute"]
    and not (_early_runtime_is_locked() and _early_runner_entry_is_locked())
):
    raise RuntimeError(
        "registered v2.1 execution must start in the exact -S -P -B interpreter environment"
    )
if __name__ == "__main__" and not (
    _early_runtime_is_locked() and _early_runner_entry_is_locked()
):
    _early_python = _early_fixed_python_executable()
    os.execve(
        _early_python,
        [
            _early_python,
            "-S",
            "-P",
            "-B",
            os.path.realpath(__file__),
            *sys.argv[1:],
        ],
        _EARLY_EXEC_ENVIRONMENT,
    )


_CLI_BOOTSTRAP = (
    __name__ == "__main__"
    and os.environ.get(_EARLY_ENVIRONMENT_MARKER) == "1"
    and _early_runner_entry_is_locked()
)
if _CLI_BOOTSTRAP and not (_early_runtime_is_locked() and _early_runner_entry_is_locked()):
    raise RuntimeError("v2.1 CLI bootstrap interpreter is not isolated")
_REGISTERED_BOOTSTRAP = (
    _CLI_BOOTSTRAP
    and sys.argv[1:] == ["--execute"]
)

_BOOTSTRAP_GUARD_ACTIVE = _CLI_BOOTSTRAP


def _bootstrap_audit_hook(event: str, arguments: tuple[object, ...]) -> None:
    """Deny import-time effects before repository controls can be verified."""

    if not _BOOTSTRAP_GUARD_ACTIVE:
        return
    if event == "open":
        mode = arguments[1] if len(arguments) > 1 else None
        flags = arguments[2] if len(arguments) > 2 else 0
        writing = (isinstance(mode, str) and any(value in mode for value in "wax+")) or (
            isinstance(flags, int)
            and bool(
                flags
                & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND | os.O_EXCL)
            )
        )
        if writing:
            raise RuntimeError("registered v2.1 bootstrap attempted a filesystem write")
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
        raise RuntimeError("registered v2.1 bootstrap attempted an external effect")


sys.addaudithook(_bootstrap_audit_hook)

_EARLY_REGISTERED_SYS_PATH: tuple[str, ...] = tuple(sys.path)
if _CLI_BOOTSTRAP:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("registered v2.1 requires the frozen Python 3.12 runtime")
    _early_stdlib = os.path.join(
        sys.base_prefix,
        "lib",
        f"python{sys.version_info.major}.{sys.version_info.minor}",
    )
    _early_site_packages = os.path.join(
        _EARLY_PROJECT_ROOT_TEXT,
        ".venv/lib/python3.12/site-packages",
    )
    try:
        _early_site_metadata = os.lstat(_early_site_packages)
    except OSError as exc:
        raise RuntimeError("registered v2.1 site-packages authority is unavailable") from exc
    if (
        os.path.realpath(_early_site_packages) != _early_site_packages
        or _early_site_metadata.st_mode & 0o170000 != 0o040000
        or os.path.islink(_early_site_packages)
    ):
        raise RuntimeError("registered v2.1 site-packages authority drifted")
    candidates = (
        os.path.join(
            os.path.dirname(_early_stdlib),
            f"python{sys.version_info.major}{sys.version_info.minor}.zip",
        ),
        _early_stdlib,
        os.path.join(_early_stdlib, "lib-dynload"),
        _early_site_packages,
        _EARLY_PROJECT_ROOT_TEXT,
        os.path.join(_EARLY_PROJECT_ROOT_TEXT, "src"),
    )
    result: list[str] = []
    for candidate in candidates:
        text = os.path.abspath(candidate)
        if text not in result:
            result.append(text)
    _EARLY_REGISTERED_SYS_PATH = tuple(result)
    sys.path[:] = list(_EARLY_REGISTERED_SYS_PATH)

import argparse
import contextvars
import copy
import fcntl
import hashlib
import importlib.metadata
import json
import locale
import platform
import re
import shutil
import sqlite3
import stat
import subprocess
import sysconfig
import tempfile
import time
import uuid
from collections import Counter
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, cast


def _fixed_python_executable() -> Path:
    return Path(_early_fixed_python_executable())


def _registered_environment_is_exact(environment: Mapping[str, str]) -> bool:
    return dict(environment) == _EARLY_EXEC_ENVIRONMENT


def _fixed_runtime_paths() -> tuple[str, ...]:
    root = Path(__file__).resolve().parents[1]
    configured = sysconfig.get_paths()
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("registered v2.1 requires the frozen Python 3.12 runtime")
    site_packages = root / ".venv/lib/python3.12/site-packages"
    candidates = (
        Path(configured["stdlib"]).parent
        / f"python{sys.version_info.major}{sys.version_info.minor}.zip",
        Path(configured["stdlib"]),
        Path(configured["platstdlib"]),
        Path(configured["stdlib"]) / "lib-dynload",
        site_packages,
        root,
        root / "src",
    )
    result: list[str] = []
    for candidate in candidates:
        text = candidate.absolute().as_posix()
        if text not in result:
            result.append(text)
    return tuple(result)


_REGISTERED_SYS_PATH = (
    _EARLY_REGISTERED_SYS_PATH if _CLI_BOOTSTRAP else tuple(sys.path)
)
if _CLI_BOOTSTRAP and _fixed_runtime_paths() != _REGISTERED_SYS_PATH:
    raise RuntimeError("registered v2.1 fixed import path derivation drifted")

from scripts import build_p4_2a_gold_sample as gold_builder
from scripts import build_p4_2a_v2_heldout_adjudication_ui as heldout_ui
from scripts import evaluate_p4_2a_v2_heldout as evaluator
from scripts import finalize_p4_2a_v2_heldout_adjudication as heldout_finalizer
from scripts import prepare_p4_2a_v2_heldout as prepare
from scripts import rehearse_p4_2a_v2_heldout_full_path as v2_runner
from scripts import seal_p4_2a_v2_ai_draft as base_seal
from scripts import seal_p4_2a_v2_heldout_draft as heldout_seal
from scripts.run_p4_2a_offline_extract import MonotonicNsClock, RecordedAtClock
from scripts.run_p4_2a_v2_dev_calibration import ProductionSnapshot
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.db.models import LLMCall

_ALLOWED_ORIGINLESS_RUNTIME_MODULES = frozenset(
    {"_cython_3_1_4", "_cython_3_2_4", "_cython_3_2_5", "cython_runtime"}
)


def _resolved_module_path(raw: str, *, directory: bool, label: str) -> Path:
    candidate = Path(raw).absolute()
    try:
        resolved = candidate.resolve(strict=True)
        metadata = resolved.lstat()
    except OSError as exc:
        raise RuntimeError(f"registered v2.1 {label} is unavailable") from exc
    expected_kind = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if candidate != resolved or not expected_kind:
        raise RuntimeError(f"registered v2.1 {label} is aliased or has the wrong kind")
    return resolved


def _expected_repository_module_files(
    module_name: str,
    *,
    repository_root: Path,
    runner_path: Path,
) -> frozenset[Path]:
    if module_name == "__main__":
        return frozenset({runner_path})
    if module_name.startswith("scripts."):
        base = repository_root.joinpath(*module_name.split("."))
    elif module_name == "alphapilot" or module_name.startswith("alphapilot."):
        base = repository_root / "src"
        base = base.joinpath(*module_name.split("."))
    else:
        return frozenset()
    return frozenset({base.with_suffix(".py"), base / "__init__.py"})


def _expected_repository_module_directory(
    module_name: str,
    *,
    repository_root: Path,
) -> Path | None:
    if module_name == "scripts" or module_name.startswith("scripts."):
        return repository_root.joinpath(*module_name.split("."))
    if module_name == "alphapilot" or module_name.startswith("alphapilot."):
        return (repository_root / "src").joinpath(*module_name.split("."))
    return None


def _classify_loaded_module_origins(
    *,
    modules: Mapping[str, object],
    repository_root: Path,
    runner_path: Path,
    site_root: Path,
    stdlib_roots: Sequence[Path],
) -> frozenset[str]:
    """Reject every loaded origin outside the frozen runtime/repository mapping."""

    repository_root = repository_root.resolve(strict=True)
    runner_path = runner_path.resolve(strict=True)
    site_root = site_root.resolve(strict=True)
    frozen_stdlib_roots = tuple(root.resolve(strict=True) for root in stdlib_roots)
    repository_paths: set[str] = set()
    typing_module = modules.get("typing")

    def is_bound_six_moves(module_name: str, module: object) -> bool:
        six_module = modules.get("six")
        if (
            module_name != "six.moves"
            or not isinstance(six_module, ModuleType)
            or module is not getattr(six_module, "moves", None)
        ):
            return False
        raw_six_origin = getattr(six_module, "__file__", None)
        if not isinstance(raw_six_origin, str):
            return False
        six_origin = _resolved_module_path(
            raw_six_origin,
            directory=False,
            label="module origin six",
        )
        return six_origin.is_relative_to(site_root)

    def classify(path: Path, *, module_name: str, is_directory: bool) -> None:
        repository_namespace = (
            module_name == "__main__"
            or module_name == "scripts"
            or module_name.startswith("scripts.")
            or module_name == "alphapilot"
            or module_name.startswith("alphapilot.")
        )
        if repository_namespace:
            if is_directory:
                expected_directory = _expected_repository_module_directory(
                    module_name,
                    repository_root=repository_root,
                )
                if expected_directory is None or path != expected_directory:
                    raise RuntimeError(
                        f"registered v2.1 repository package path is not registered: {module_name}"
                    )
                return
            expected_files = _expected_repository_module_files(
                module_name,
                repository_root=repository_root,
                runner_path=runner_path,
            )
            if path not in expected_files:
                raise RuntimeError(
                    f"registered v2.1 repository module origin is not registered: {module_name}"
                )
            repository_paths.add(path.relative_to(repository_root).as_posix())
            return
        # The registered site-packages authority is physically nested beneath
        # the repository root, so classify it before rejecting other root files.
        if path.is_relative_to(site_root):
            return
        if any(path.is_relative_to(root) for root in frozen_stdlib_roots):
            return
        if path.is_relative_to(repository_root):
            raise RuntimeError(
                f"registered v2.1 repository module origin is not registered: {module_name}"
            )
        raise RuntimeError(f"registered v2.1 module origin escaped: {module_name}")

    for module_name, module in sorted(modules.items()):
        if module is None:
            continue
        if not isinstance(module, ModuleType):
            suffix = module_name.removeprefix("typing.")
            if (
                module_name not in {"typing.io", "typing.re"}
                or typing_module is None
                or module is not getattr(typing_module, suffix, None)
            ):
                raise RuntimeError(
                    f"registered v2.1 sys.modules contains a non-module entry: {module_name}"
                )
            continue
        raw_origin = getattr(module, "__file__", None)
        module_spec = getattr(module, "__spec__", None)
        spec_origin = getattr(module_spec, "origin", None)
        raw_paths = getattr(module, "__path__", None)
        has_bound_file = False
        if isinstance(raw_origin, str):
            if raw_origin.startswith("<"):
                raise RuntimeError(
                    f"registered v2.1 module has a synthetic file origin: {module_name}"
                )
            classify(
                _resolved_module_path(
                    raw_origin,
                    directory=False,
                    label=f"module origin {module_name}",
                ),
                module_name=module_name,
                is_directory=False,
            )
            has_bound_file = True
        elif raw_origin is not None:
            raise RuntimeError(f"registered v2.1 module origin is invalid: {module_name}")
        elif spec_origin not in {None, "built-in", "frozen"}:
            raise RuntimeError(f"registered v2.1 module spec origin is invalid: {module_name}")
        elif raw_paths is None and spec_origin is None:
            if module_name not in _ALLOWED_ORIGINLESS_RUNTIME_MODULES:
                raise RuntimeError(f"registered v2.1 module has no bound origin: {module_name}")

        if raw_paths is not None:
            try:
                paths = tuple(raw_paths)
            except TypeError as exc:
                raise RuntimeError(
                    f"registered v2.1 package path is invalid: {module_name}"
                ) from exc
            if not paths and (
                has_bound_file or is_bound_six_moves(module_name, module)
            ):
                continue
            if not paths:
                raise RuntimeError(f"registered v2.1 package path is empty: {module_name}")
            for raw_path in paths:
                if not isinstance(raw_path, str):
                    raise RuntimeError(
                        f"registered v2.1 package path is invalid: {module_name}"
                    )
                classify(
                    _resolved_module_path(
                        raw_path,
                        directory=True,
                        label=f"package path {module_name}",
                    ),
                    module_name=module_name,
                    is_directory=True,
                )
    return frozenset(repository_paths)


def _assert_registered_module_origins() -> frozenset[str]:
    if not _CLI_BOOTSTRAP:
        return frozenset()
    if tuple(sys.path) != _REGISTERED_SYS_PATH:
        raise RuntimeError("registered v2.1 import path drifted")
    if "sitecustomize" in sys.modules or "usercustomize" in sys.modules:
        raise RuntimeError("registered v2.1 imported an ambient customization module")
    repository_root = Path(__file__).resolve().parents[1]
    configured = sysconfig.get_paths()
    return _classify_loaded_module_origins(
        modules=sys.modules,
        repository_root=repository_root,
        runner_path=Path(__file__),
        site_root=repository_root / ".venv/lib/python3.12/site-packages",
        stdlib_roots=(Path(configured["stdlib"]), Path(configured["platstdlib"])),
    )


_REGISTERED_REPOSITORY_MODULE_PATHS = _assert_registered_module_origins()
_BOOTSTRAP_GUARD_ACTIVE = False

JsonObject = dict[str, Any]
Clock = Callable[[], datetime]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREREGISTRATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-1-preregistration-20260810.json"
)
PREREGISTRATION_SHA256 = "c303cfb13a42ecbb7e0acaec04de12a9e9169b89cf9e93ea79d0f120d1439d3e"
PREREGISTRATION_COMMIT = "b302d5889f01296568340bcc15041cc554ceb2c7"
PREDICTION_TIMING_PREREG_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-1-prediction-timing-seam-preregistration-20260810.json"
)
PREDICTION_TIMING_PREREG_SHA256 = (
    "1052c7a33268572fc794517844dae4b6c1ea504121712ad2f55ec814a7446f9a"
)
PREDICTION_TIMING_PREREG_COMMIT = "b3c2d2216c1feffd9949f181fa6766f8357ff683"
PREDICTION_TIMING_IMPLEMENTATION_PATHS = (
    "scripts/run_p4_2a_offline_extract.py",
    "tests/test_p4_2a_offline_extract.py",
)
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
BUNDLE_SCHEMA_RELATIVE = Path("config/schemas/p4_2a_v2_1_heldout_rehearsal_bundle.schema.json")
BUNDLE_SCHEMA_SHA256 = "ed827e29ce853f07a9110d44c98793a4cc3ef0634a12fe7e8bc64c7290d7d716"
RELEASE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_1_heldout_release_authorization.schema.json"
)
RELEASE_SCHEMA_SHA256 = "c5a4ecfe8c5bf3e3ebea2d4470337a67dde3a8e9dbe6fc3df68b1c4e16241c51"
SUCCESSOR_DIRECTORY_RELATIVE = Path("docs/phase4/rehearsals/P4.2a-v2-calibration-v2-1")
REVIEW_REQUEST_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-1-implementation-and-execution-review-request-20260810.json"
)
RELEASE_AUTHORIZATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-1-release-authorization-20260810.json"
)
BUNDLE_FILENAME = "bundle.json"
BUNDLE_SCHEMA_VERSION = "p4.2a-v2-heldout-rehearsal-bundle-v2.1"
REHEARSAL_ID = "P4.2A-V2-HELDOUT-REHEARSAL-V2-1-DETERMINISTIC-20260810"
CONTROL_MANIFEST_SCHEMA = "p4.2a-v2-heldout-rehearsal-control-manifest-v2.1"
V1_FAIL_CLOSE_COMMIT = "d710e885b49006eedf4f70ea09cb81fe15d176a3"
FIXED_WALL_CLOCK = datetime(2026, 8, 10, 12, 30, tzinfo=UTC)
FIXED_WALL_CLOCK_TEXT = "2026-08-10T12:30:00Z"
UUID_NAMESPACE = uuid.UUID("4a8a9839-d0a6-509a-b193-ddf4b5700780")
MONOTONIC_INITIAL_SECONDS = 1000.0
PACKAGE_ROOT_RELATIVE = Path(".venv/lib/python3.12/site-packages")
PACKAGE_ROOTS_SHA256 = "fae235892c0988d4093d1ad12b034a6126d116e436393e837a8b2f71601fbd12"
PACKAGE_INVENTORY_SHA256 = "c3c7792eb31679c0eb7d3140e067d691df330cd3af302d2350bf15b74ac8ec42"
PYTHON_INVENTORY_SHA256 = "ab3e067417027bb98ea4335e9086d2046ac9dfd4eaf857acc8622dc8f0a13a31"
CNINFO_REQUEST_COUNT = 2824
CNINFO_GAP_COUNT = 2823
FIXTURE_RAW_COUNT = 4048
FIXTURE_BY_SOURCE = {
    "cninfo": 2824,
    "akshare_ths": 1021,
    "sina_company_news": 203,
}
FIXTURE_ID_START = 9_000_001

MERKLE_LEAF_PREFIX = b"p4.2a-rehearsal-leaf-v2.1\0"
MERKLE_NODE_PREFIX = b"p4.2a-rehearsal-node-v2.1\0"
BUNDLE_ROOT_PREFIX = b"p4.2a-rehearsal-bundle-v2.1\0"

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
    (
        "predictions",
        "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-v2.predictions.jsonl",
    ),
    (
        "prediction_manifest",
        "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-v2.predictions.manifest.json",
    ),
    (
        "private_selection",
        "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-frame-v2.selection.json",
    ),
    (
        "owner_blind",
        "docs/phase4/eval/v2-calibration/heldout/P4.2a-heldout-frame-v2.blind.jsonl",
    ),
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

FROZEN_FILE_SHA256: dict[str, str] = {
    PREREGISTRATION_RELATIVE.as_posix(): PREREGISTRATION_SHA256,
    PREDICTION_TIMING_PREREG_RELATIVE.as_posix(): PREDICTION_TIMING_PREREG_SHA256,
    SCOPE_CORRECTION_RULING_RELATIVE.as_posix(): SCOPE_CORRECTION_RULING_SHA256,
    CONTROL_PLANE_AUTHORIZATION_RELATIVE.as_posix(): CONTROL_PLANE_AUTHORIZATION_SHA256,
    BUNDLE_SCHEMA_RELATIVE.as_posix(): BUNDLE_SCHEMA_SHA256,
    RELEASE_SCHEMA_RELATIVE.as_posix(): RELEASE_SCHEMA_SHA256,
    "docs/phase4/reports/P4.2a-successor-v2-1-code-gate-authorization-20260810.json": (
        "e28db692dc150983f86f6760fb1a95584d8607658e8a78a0de35cf3fc81940cd"
    ),
    "docs/phase4/reports/P4.2a-rehearsal-v2-approval-and-frame-authority-ruling-20260810.json": (
        "8605dd30dd6bffa9621b6efe5e01c4c9cead615c9639259c2e79e14fcbbc3421"
    ),
    "config/p4_event_evaluation_v2.yaml": prepare.DESIGN_SHA256,
    "config/p4_event_extract_eval_v2-heldout-qwen3.6-plus.yaml": (prepare.HELDOUT_CONTRACT_SHA256),
    "config/p4_event_extract_eval_v2-r3-qwen3.6-plus.yaml": prepare.ROUND3_CONTRACT_SHA256,
    "config/prompts/p4_news_event_extract_v2-r3.txt": prepare.ROUND3_PROMPT_SHA256,
    "config/p4_news_poll_v2_1.yaml": (
        "9d56e137baf10bd0858723a93aff02c57bf7b35f8705f1817b16a89ec615183f"
    ),
    "pyproject.toml": "b38481e57b0ba88d1b9b728c2a57583d55cf175262a8a803b483cf4823e13e29",
    "uv.lock": "10829f7ef74adfcbd4401000112b5539c899a899d09d8a3f78fdf8d95803a673",
}

ENTRYPOINTS = (
    "scripts/rehearse_p4_2a_v2_1_heldout_full_path.py",
    "scripts/validate_p4_2a_v2_1_heldout_rehearsal_bundle.py",
    "scripts/prepare_p4_2a_v2_heldout.py",
    "scripts/evaluate_p4_2a_v2_heldout.py",
    "scripts/seal_p4_2a_v2_heldout_draft.py",
    "scripts/build_p4_2a_v2_heldout_adjudication_ui.py",
    "scripts/finalize_p4_2a_v2_heldout_adjudication.py",
)


class RehearsalV21Error(RuntimeError):
    """Fail-closed successor-v2.1 rehearsal error."""


def _assert_registered_environment() -> None:
    """Require the interpreter state established by the first OS-level exec."""

    if (
        not _REGISTERED_BOOTSTRAP
        or not _early_runtime_is_locked()
        or not _early_runner_entry_is_locked()
        or tuple(sys.path) != _REGISTERED_SYS_PATH
    ):
        raise RehearsalV21Error(
            "registered v2.1 execution requires the exact locked interpreter environment"
        )
    try:
        current_repository_modules = _assert_registered_module_origins()
    except RuntimeError as exc:
        raise RehearsalV21Error("registered v2.1 module origin drifted") from exc
    if current_repository_modules != _REGISTERED_REPOSITORY_MODULE_PATHS:
        raise RehearsalV21Error("registered v2.1 loaded repository module set drifted")
    try:
        active_locale = locale.setlocale(locale.LC_ALL, "")
        time.tzset()
    except (locale.Error, OSError) as exc:
        raise RehearsalV21Error(
            "registered v2.1 interpreter locale or timezone is unavailable"
        ) from exc
    if active_locale != "C.UTF-8" or time.tzname != ("UTC", "UTC") or time.timezone != 0:
        raise RehearsalV21Error("registered v2.1 interpreter locale or timezone drifted")


@dataclass(frozen=True)
class DeterministicClock:
    seconds: float = MONOTONIC_INITIAL_SECONDS

    def monotonic(self) -> float:
        return self.seconds

    def sleep(self, duration: float) -> None:
        if not isinstance(duration, (int, float)) or duration < 0:
            raise RehearsalV21Error("deterministic sleeper received an invalid duration")
        object.__setattr__(self, "seconds", self.seconds + float(duration))


@dataclass(frozen=True)
class RehearsalRun:
    label: str
    artifacts: Mapping[str, bytes]
    probes: Mapping[str, bool]


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
class GateProbeEvidence:
    synthetic_release_positive: bool
    create_only_history_positive: bool
    modified_after_creation_rejected: bool
    canonical_validator_used: bool
    canonical_root_unlock_rejected: bool
    noop_sleeper_rejected_before_second_fetch: bool
    deterministic_ineligible_semantics_passed: bool
    unexpected_failure_semantics_passed: bool
    seal_and_ui_gate_probes_passed: bool
    finalize_real_rejection_passed: bool
    attestation_negative_probes_passed: bool
    runtime_start_negative_probes_passed: bool


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


def _canonical_jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(_canonical_json_bytes(dict(row)) for row in rows)


def _merkle_leaf(relative_path: str, payload: bytes) -> bytes:
    return hashlib.sha256(
        MERKLE_LEAF_PREFIX
        + relative_path.encode("utf-8")
        + b"\0"
        + hashlib.sha256(payload).digest()
    ).digest()


def _merkle_root(payloads: Mapping[str, bytes]) -> str:
    if not payloads:
        raise RehearsalV21Error("empty Merkle tree is forbidden")
    nodes = [
        _merkle_leaf(relative, payloads[relative])
        for relative in sorted(payloads, key=lambda item: item.encode("utf-8"))
    ]
    while len(nodes) > 1:
        if len(nodes) % 2:
            nodes.append(nodes[-1])
        nodes = [
            hashlib.sha256(MERKLE_NODE_PREFIX + nodes[index] + nodes[index + 1]).digest()
            for index in range(0, len(nodes), 2)
        ]
    return nodes[0].hex()


def _bundle_root(run_a: str, run_b: str, control: str) -> str:
    return hashlib.sha256(
        BUNDLE_ROOT_PREFIX + bytes.fromhex(run_a) + bytes.fromhex(run_b) + bytes.fromhex(control)
    ).hexdigest()


def _artifact_paths(binding: prepare.HeldoutBinding) -> dict[str, Path]:
    return {
        **binding.artifacts,
        "synthetic_report": binding.artifacts["report_directory"] / evaluator.REPORT_FILENAME,
    }


def _workspace_artifacts(
    workspace: Path, source_binding: prepare.HeldoutBinding
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
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to reuse fixture database: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
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
        observed = connection.execute("SELECT COUNT(*) FROM news_items").fetchone()
        if observed != (FIXTURE_RAW_COUNT,):
            raise RehearsalV21Error("fixture database row count drifted")
    finally:
        connection.close()
    os.chmod(path, 0o600)


def _fake_pdf_fetcher(url: str, _policy: object) -> bytes:
    identifier = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return b"%PDF-1.4\n% offline successor v2.1 " + identifier.encode("ascii") + b"\n"


def _fake_pdf_text_extractor(pdf_bytes: bytes, _policy: object) -> gold_builder.ExtractedPdfText:
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


@dataclass(frozen=True)
class _AuditPolicy:
    project_root: Path
    write_roots: tuple[Path, ...]
    sqlite_roots: tuple[Path, ...]
    subprocess_mode: str
    synthetic_git_root: Path | None = None


_AUDIT_POLICY: contextvars.ContextVar[_AuditPolicy | None] = contextvars.ContextVar(
    "p4_2a_v2_1_rehearsal_audit_policy",
    default=None,
)
_TEMP_AUTHORITY: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "p4_2a_v2_1_rehearsal_temp_authority",
    default=None,
)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_temp_authority(
    *,
    project_root: Path,
    forbidden_paths: Sequence[Path],
) -> Path:
    base = Path("/private/tmp")
    try:
        base_metadata = base.lstat()
    except OSError as exc:
        raise RehearsalV21Error("fixed temporary authority base is unavailable") from exc
    if (
        base.resolve(strict=True) != base
        or base.is_symlink()
        or not stat.S_ISDIR(base_metadata.st_mode)
    ):
        raise RehearsalV21Error("fixed temporary authority base is aliased")
    root = project_root.resolve()
    forbidden = (*tuple(path.absolute() for path in forbidden_paths), root)
    authority = Path(
        tempfile.mkdtemp(
            prefix=f"alphapilot-p4-2a-v2-1-{os.getuid()}-",
            dir=base,
        )
    ).absolute()
    try:
        metadata = authority.lstat()
        if (
            authority.resolve(strict=True) != authority
            or authority.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or any(
                authority == path
                or authority.is_relative_to(path)
                or path.is_relative_to(authority)
                for path in forbidden
            )
        ):
            raise RehearsalV21Error("temporary authority is unsafe")
        probe = authority / "create-cleanup-fsync.probe"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(probe, flags, 0o600)
        try:
            os.write(descriptor, b"p4.2a-v2.1-temp-authority\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_directory(authority)
        probe.unlink()
        _fsync_directory(authority)
        if any(authority.iterdir()):
            raise RehearsalV21Error("temporary authority probe left residue")
        return authority
    except Exception:
        probe = authority / "create-cleanup-fsync.probe"
        if probe.exists() and not probe.is_symlink():
            probe.unlink()
        if authority.exists() and not authority.is_symlink() and not any(authority.iterdir()):
            authority.rmdir()
            _fsync_directory(base)
        raise


def _remove_temp_authority(authority: Path) -> None:
    base = Path("/private/tmp")
    try:
        metadata = authority.lstat()
    except OSError as exc:
        raise RehearsalV21Error("temporary authority disappeared") from exc
    if (
        authority.parent != base
        or authority.resolve(strict=True) != authority
        or authority.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or any(authority.iterdir())
    ):
        raise RehearsalV21Error("temporary authority is not empty and removable")
    authority.rmdir()
    _fsync_directory(base)


@contextmanager
def _temporary_authority_scope(
    *,
    project_root: Path,
    forbidden_paths: Sequence[Path],
) -> Iterator[Path]:
    existing = _TEMP_AUTHORITY.get()
    if existing is not None:
        yield existing
        return
    authority = _create_temp_authority(
        project_root=project_root,
        forbidden_paths=forbidden_paths,
    )
    token = _TEMP_AUTHORITY.set(authority)
    try:
        yield authority
    finally:
        _TEMP_AUTHORITY.reset(token)
        _remove_temp_authority(authority)


def _lowercase_commit(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_relative_text(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    pure = PurePosixPath(value)
    return not pure.is_absolute() and all(part not in {"", ".", ".."} for part in pure.parts)


def _path_in_roots(path: Path, roots: Sequence[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def _lexical_path(value: object) -> Path | None:
    if isinstance(value, int) or not isinstance(value, (str, bytes, os.PathLike)):
        return None
    try:
        return Path(os.path.abspath(Path(os.fsdecode(value))))
    except (OSError, TypeError, ValueError):
        return None


def _require_audited_write_path(value: object, policy: _AuditPolicy) -> Path:
    lexical = _lexical_path(value)
    if lexical is None or not _path_in_roots(lexical, policy.write_roots):
        raise RehearsalV21Error("offline replay attempted an external filesystem write")
    existing = lexical
    while not existing.exists() and not existing.is_symlink():
        if existing == existing.parent:
            raise RehearsalV21Error("offline replay write target has no safe parent")
        existing = existing.parent
    try:
        resolved = existing.resolve(strict=True)
    except OSError as exc:
        raise RehearsalV21Error("offline replay write target parent is unavailable") from exc
    if not _path_in_roots(resolved, policy.write_roots):
        raise RehearsalV21Error("offline replay write target escapes through a symlink")
    return lexical


def _require_audited_write_descriptor(value: object, policy: _AuditPolicy) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RehearsalV21Error("offline replay write descriptor is invalid")
    try:
        raw_path = fcntl.fcntl(value, fcntl.F_GETPATH, b"\0" * 1024)
        terminator = raw_path.find(b"\0")
        if terminator <= 0:
            raise RehearsalV21Error("offline replay write descriptor path is invalid")
        descriptor_path = Path(os.fsdecode(raw_path[:terminator]))
        if not descriptor_path.is_absolute():
            raise RehearsalV21Error("offline replay write descriptor path is not absolute")
        lexical = _require_audited_write_path(descriptor_path, policy)
        descriptor_metadata = os.fstat(value)
        path_metadata = lexical.stat()
        descriptor_flags = fcntl.fcntl(value, fcntl.F_GETFL)
    except RehearsalV21Error:
        raise
    except (OSError, ValueError) as exc:
        raise RehearsalV21Error("offline replay write descriptor is unavailable") from exc
    if (
        not stat.S_ISREG(descriptor_metadata.st_mode)
        or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
        != (path_metadata.st_dev, path_metadata.st_ino)
        or descriptor_flags & os.O_ACCMODE not in {os.O_WRONLY, os.O_RDWR}
    ):
        raise RehearsalV21Error("offline replay write descriptor identity is invalid")


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


def _git_command_parts(
    command: object,
    cwd: object,
) -> tuple[Path, tuple[str, ...], bool] | None:
    if not isinstance(command, (list, tuple)) or any(not isinstance(item, str) for item in command):
        return None
    arguments = tuple(cast(Sequence[str], command))
    if not arguments or arguments[0] not in {"git", "/usr/bin/git"}:
        return None
    configured = arguments[1 : 1 + len(GIT_CONFIG_PREFIX)] == GIT_CONFIG_PREFIX
    offset = 1 + len(GIT_CONFIG_PREFIX) if configured else 1
    if len(arguments) > offset and arguments[offset] == "clone":
        operation = arguments[offset:]
        if len(operation) < 3:
            return None
        return Path(operation[-2]).resolve(), operation, configured
    if len(arguments) > offset + 2 and arguments[offset] == "-C":
        root = Path(arguments[offset + 1]).resolve()
        operation = arguments[offset + 2 :]
    else:
        lexical_cwd = _lexical_path(cwd)
        if lexical_cwd is None:
            return None
        root = lexical_cwd.resolve()
        operation = arguments[offset:]
    return root, operation, configured


def _read_only_git_operation(operation: Sequence[str]) -> bool:
    arguments = tuple(operation)
    if arguments == ("rev-parse", "HEAD"):
        return True
    if (
        len(arguments) == 3
        and arguments[:2] == ("rev-parse", "--verify")
        and arguments[2].endswith("^{commit}")
        and _lowercase_commit(arguments[2][:-9])
    ):
        return True
    if len(arguments) == 3 and arguments[:2] == ("cat-file", "-e"):
        value = arguments[2]
        return (value.endswith("^{commit}") and _lowercase_commit(value[:-9])) or _lowercase_commit(
            value
        )
    if len(arguments) == 3 and arguments[:2] == ("cat-file", "-t"):
        commit, separator, relative = arguments[2].partition(":")
        return separator == ":" and _lowercase_commit(commit) and _safe_relative_text(relative)
    if (
        len(arguments) == 4
        and arguments[:2] == ("merge-base", "--is-ancestor")
        and _lowercase_commit(arguments[2])
        and _lowercase_commit(arguments[3])
    ):
        return True
    if len(arguments) == 2 and arguments[0] == "show":
        commit, separator, relative = arguments[1].partition(":")
        return separator == ":" and _lowercase_commit(commit) and _safe_relative_text(relative)
    if (
        len(arguments) == 5
        and arguments[:4] == ("status", "--porcelain=v1", "--untracked-files=all", "--")
        and _safe_relative_text(arguments[4])
    ):
        return True
    if (
        len(arguments) in {5, 6}
        and arguments[:4] == ("rev-list", "--parents", "-n", "1")
        and _lowercase_commit(arguments[4])
        and (len(arguments) == 5 or arguments[5] == "--")
    ):
        return True
    if (
        len(arguments) == 8
        and arguments[:5]
        == ("diff", "--no-ext-diff", "--no-textconv", "--name-status", "--no-renames")
        and _lowercase_commit(arguments[5])
        and _lowercase_commit(arguments[6])
        and arguments[7] == "--"
    ):
        return True
    return (
        len(arguments) == 9
        and arguments[:8]
        == (
            "log",
            "--first-parent",
            "--diff-merges=first-parent",
            "--format=@@%H",
            "--name-status",
            "--find-renames",
            "--find-copies",
            "--",
        )
        and _safe_relative_text(arguments[8])
    )


def _allowed_git_subprocess(
    command: object,
    cwd: object,
    environment: object,
    policy: _AuditPolicy,
) -> bool:
    if not isinstance(environment, Mapping):
        return False
    observed_environment = {
        str(key): str(value) for key, value in cast(Mapping[object, object], environment).items()
    }
    base_environment = _sanitized_git_environment()
    permitted_environments: tuple[dict[str, str], ...] = (base_environment,)
    if policy.subprocess_mode == "synthetic_git":
        synthetic_environment = _sanitized_git_environment(synthetic_identity=True)
        permitted_environments = (base_environment, synthetic_environment)
    if observed_environment not in permitted_environments:
        return False
    parsed = _git_command_parts(command, cwd)
    if parsed is None:
        return False
    root, operation, configured = parsed
    try:
        _validate_git_metadata_authority(root)
    except RehearsalV21Error:
        return False
    allowed_read_roots = {prepare.PROJECT_ROOT.resolve()}
    if policy.synthetic_git_root is not None:
        allowed_read_roots.add(policy.project_root)
        if root.is_relative_to(policy.synthetic_git_root):
            allowed_read_roots.add(root)
    if configured and root in allowed_read_roots and _read_only_git_operation(operation):
        return True
    if policy.subprocess_mode != "synthetic_git" or policy.synthetic_git_root is None:
        return False
    synthetic = policy.synthetic_git_root
    if operation and operation[0] == "clone":
        return (
            configured
            and len(operation) == 6
            and operation[1:4] == ("--quiet", "--local", "--no-checkout")
            and Path(operation[4]).resolve() == policy.project_root
            and Path(operation[5]).resolve().is_relative_to(synthetic)
        )
    if not root.is_relative_to(synthetic) or not configured:
        return False
    if len(operation) == 2 and operation[0] == "read-tree" and _lowercase_commit(operation[1]):
        return True
    if len(operation) >= 3 and operation[:2] == ("add", "--"):
        return all(_safe_relative_text(value) for value in operation[2:])
    return (
        len(operation) == 5
        and operation[:4] == ("commit", "--quiet", "--no-gpg-sign", "-m")
        and operation[4] in {"synthetic evidence", "synthetic release", "mutate receipt"}
    )


def _audit_hook(event: str, arguments: tuple[Any, ...]) -> None:
    policy = _AUDIT_POLICY.get()
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
                _require_audited_write_path(path, policy)
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
        _require_audited_write_path(arguments[0] if arguments else None, policy)
        has_dir_fd = (
            event in {"os.remove", "os.rmdir", "os.unlink", "shutil.rmtree"}
            and len(arguments) >= 2
            and arguments[-1] not in {None, -1}
        ) or (
            event in {"os.chmod", "os.chown", "os.lchown", "os.mkdir", "os.utime"}
            and len(arguments) >= 3
            and arguments[-1] not in {None, -1}
        )
        if has_dir_fd:
            raise RehearsalV21Error("offline replay attempted a dir-fd filesystem write")
        return
    if event in {"os.rename", "os.replace"}:
        _require_audited_write_path(arguments[0] if arguments else None, policy)
        _require_audited_write_path(arguments[1] if len(arguments) > 1 else None, policy)
        if any(value not in {None, -1} for value in arguments[2:]):
            raise RehearsalV21Error("offline replay attempted a dir-fd rename")
        return
    if event == "os.link":
        hardlink_source = _require_audited_write_path(
            arguments[0] if arguments else None,
            policy,
        )
        hardlink_destination = _require_audited_write_path(
            arguments[1] if len(arguments) > 1 else None,
            policy,
        )
        if any(value not in {None, -1} for value in arguments[2:]):
            raise RehearsalV21Error("offline replay attempted a dir-fd hard link")
        if not any(
            (hardlink_source == root or hardlink_source.is_relative_to(root))
            and (hardlink_destination == root or hardlink_destination.is_relative_to(root))
            for root in policy.write_roots
        ):
            raise RehearsalV21Error("offline replay hard link crosses its workspace")
        try:
            source_metadata = hardlink_source.lstat()
        except OSError as exc:
            raise RehearsalV21Error("offline replay hard-link source is unavailable") from exc
        if (
            hardlink_source.is_symlink()
            or not stat.S_ISREG(source_metadata.st_mode)
            or source_metadata.st_nlink != 1
            or hardlink_source.resolve(strict=True) != hardlink_source
        ):
            raise RehearsalV21Error("offline replay hard-link source is not a unique regular file")
        if hardlink_destination.exists() or hardlink_destination.is_symlink():
            raise RehearsalV21Error("offline replay hard-link destination already exists")
        return
    if event == "os.symlink":
        source = arguments[0] if arguments else None
        destination = _require_audited_write_path(
            arguments[1] if len(arguments) > 1 else None,
            policy,
        )
        if len(arguments) > 2 and arguments[2] not in {None, -1}:
            raise RehearsalV21Error("offline replay attempted a dir-fd symlink")
        if not isinstance(source, (str, bytes, os.PathLike)):
            raise RehearsalV21Error("offline replay symlink target is invalid")
        source_path = Path(os.fsdecode(source))
        target = source_path if source_path.is_absolute() else destination.parent / source_path
        if not _path_in_roots(target.resolve(strict=False), policy.write_roots):
            raise RehearsalV21Error("offline replay symlink target escapes its workspace")
        return
    if event == "subprocess.Popen":
        command = arguments[1] if len(arguments) > 1 else None
        cwd = arguments[2] if len(arguments) > 2 else None
        environment = arguments[3] if len(arguments) > 3 else None
        if not _allowed_git_subprocess(command, cwd, environment, policy):
            raise RehearsalV21Error("offline replay attempted a subprocess")
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
        raise RehearsalV21Error(
            "offline replay attempted an alternate process or thread execution path"
        )
    if event == "sqlite3.connect":
        database = arguments[0] if arguments else None
        if database == ":memory:":
            return
        raw = str(database)
        if raw.startswith("file:"):
            raw = raw[5:].split("?", 1)[0]
        candidate = Path(raw).expanduser().resolve()
        if not _path_in_roots(candidate, policy.sqlite_roots):
            raise RehearsalV21Error("offline replay attempted an external database open")
        return
    if event.startswith("socket."):
        raise RehearsalV21Error("offline rehearsal attempted a network operation")


sys.addaudithook(_audit_hook)


@contextmanager
def _audited_execution(policy: _AuditPolicy) -> Iterator[None]:
    token = _AUDIT_POLICY.set(policy)
    try:
        yield
    finally:
        _AUDIT_POLICY.reset(token)


@contextmanager
def _guarded_temp_execution(
    *,
    project_root: Path,
    workspace: Path,
) -> Iterator[None]:
    """Confine real-entry replay effects through a process audit policy."""

    allowed = workspace.resolve()
    with _audited_execution(
        _AuditPolicy(
            project_root=project_root.resolve(),
            write_roots=(allowed,),
            sqlite_roots=(allowed,),
            subprocess_mode="canonical_git_reads",
        )
    ):
        yield


def registered_rehearsal_directory(project_root: Path = PROJECT_ROOT) -> Path:
    root = project_root.resolve()
    literal = (root / SUCCESSOR_DIRECTORY_RELATIVE).absolute()
    cursor = root
    for component in SUCCESSOR_DIRECTORY_RELATIVE.parts:
        cursor = cursor / component
        try:
            metadata = cursor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise RehearsalV21Error("registered v2.1 path contains a symlink")
        if cursor != literal and not stat.S_ISDIR(metadata.st_mode):
            raise RehearsalV21Error("registered v2.1 path ancestor is not a directory")
    if literal.resolve(strict=False) != literal:
        raise RehearsalV21Error("registered v2.1 path is an alias")
    return literal


def _copy_controls(project_root: Path, workspace: Path) -> None:
    """Copy every binding/control byte needed by the real entrypoints."""

    v2_runner._copy_control_surface(project_root, workspace)
    preregistration = _preregistration_document(project_root)
    implementation = cast(Mapping[str, Any], preregistration.get("implementation_contract", {}))
    declared = _declared_preregistration_hashes(project_root)
    relative_paths = (
        set(FROZEN_FILE_SHA256)
        | set(declared)
        | set(PREDICTION_TIMING_IMPLEMENTATION_PATHS)
    )
    for field in (
        "registered_modified_consumers",
        "registered_new_files",
        "registered_existing_test_updates",
    ):
        values = implementation.get(field)
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not item for item in values
        ):
            raise RehearsalV21Error(f"preregistered {field} is unavailable")
        relative_paths.update(cast(list[str], values))
    relative_paths.add(_control_plane_registry_expansion(project_root))
    payloads: list[tuple[Path, bytes]] = []
    for relative in sorted(relative_paths):
        source = _safe_repository_file(project_root, relative, f"registered control {relative}")
        target = workspace / relative
        if target.exists():
            if (
                target.is_symlink()
                or not target.is_file()
                or target.read_bytes() != source.read_bytes()
            ):
                raise RehearsalV21Error(f"copied control byte drifted: {relative}")
            continue
        digest = FROZEN_FILE_SHA256.get(relative) or declared.get(relative)
        payload = source.read_bytes()
        if digest is not None and _sha256(payload) != digest:
            raise RehearsalV21Error(f"frozen control SHA drifted: {relative}")
        payloads.append((target, payload))
    prepare._publish_create_only(tuple(payloads))


def _temporary_binding(
    *,
    project_root: Path,
    workspace: Path,
) -> tuple[prepare.HeldoutBinding, Path]:
    if workspace.is_relative_to(project_root):
        raise RehearsalV21Error("offline workspace must be outside the canonical repository")
    _copy_controls(project_root, workspace)
    source_binding = prepare.load_binding(project_root)
    artifacts = _workspace_artifacts(workspace, source_binding)
    binding = replace(
        source_binding,
        root=workspace,
        artifacts=artifacts,
    )
    database = workspace / "data/alphapilot.db"
    _create_fixture_database(database)
    if set(range(FIXTURE_ID_START, FIXTURE_ID_START + FIXTURE_RAW_COUNT)) & binding.retired_ids:
        raise RehearsalV21Error("fixture ids intersect the retired held-out ids")
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
            raise RehearsalV21Error("snapshot loader escaped the offline workspace")
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
            raise RehearsalV21Error("mocked model request contract drifted")
        payload = json.loads(user)
        if not isinstance(payload, dict):
            raise RehearsalV21Error("mocked model input is not one object")
        identifier = payload.get("news_item_id")
        evidence = payload.get("evidence_candidates")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or not isinstance(evidence, list)
            or not evidence
            or not isinstance(evidence[0], list)
            or len(evidence[0]) != 4
        ):
            raise RehearsalV21Error("mocked model input is not one materialized candidate")
        if len(calls) >= len(expected_ids):
            raise RehearsalV21Error("mocked model received an extra candidate")
        expected = expected_ids[len(calls)]
        if identifier != expected:
            raise RehearsalV21Error("mocked model call order drifted")
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
            "symbols": [str(payload.get("ingested_symbol"))],
            "event_type": "other",
            "direction": 0,
            "materiality": 2 if len(calls) <= 100 else 1,
            "summary": "v2.1 离线排练结构化结果。",
            "confidence": 1.0,
            "evidence_candidate_id": first[0],
        }

    def wall_clock() -> datetime:
        return FIXED_WALL_CLOCK

    def execution_id_factory() -> str:
        return str(uuid.uuid5(UUID_NAMESPACE, "v2.1-inference\0full-pool"))

    def prediction_recorded_at_clock() -> str:
        return FIXED_WALL_CLOCK_TEXT

    def prediction_monotonic_ns_clock() -> int:
        return int(timing_clock.monotonic() * 1_000_000_000)

    return InferenceHarness(
        settings=_settings(),
        chat_json_fn=mocked_model,
        snapshot_loader=snapshot_loader,
        wall_clock=wall_clock,
        execution_id_factory=execution_id_factory,
        prediction_recorded_at_clock=prediction_recorded_at_clock,
        prediction_monotonic_ns_clock=prediction_monotonic_ns_clock,
        calls=calls,
    )


def _inert_inference_harness(
    binding: prepare.HeldoutBinding,
    *,
    timing_clock: DeterministicClock,
) -> InferenceHarness:
    calls: list[int] = []

    def forbidden_model(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise RehearsalV21Error("materialization-only probe attempted inference")

    def forbidden_snapshot(_root: Path) -> ProductionSnapshot:
        raise RehearsalV21Error("materialization-only probe attempted a production snapshot")

    def wall_clock() -> datetime:
        return FIXED_WALL_CLOCK

    def execution_id_factory() -> str:
        return str(uuid.uuid5(UUID_NAMESPACE, f"v2.1-inert\0{binding.root.as_posix()}"))

    def prediction_recorded_at_clock() -> str:
        return FIXED_WALL_CLOCK_TEXT

    def prediction_monotonic_ns_clock() -> int:
        return int(timing_clock.monotonic() * 1_000_000_000)

    return InferenceHarness(
        settings=_settings(),
        chat_json_fn=forbidden_model,
        snapshot_loader=forbidden_snapshot,
        wall_clock=wall_clock,
        execution_id_factory=execution_id_factory,
        prediction_recorded_at_clock=prediction_recorded_at_clock,
        prediction_monotonic_ns_clock=prediction_monotonic_ns_clock,
        calls=calls,
    )


def _mint_offline_capability(
    binding: prepare.HeldoutBinding,
    *,
    database: Path,
    pdf_fetcher: gold_builder.PdfFetcher,
    pdf_text_extractor: gold_builder.PdfTextExtractor,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    inference: InferenceHarness,
    implementation_commit: str,
) -> prepare._OfflineRehearsalCapability:
    return prepare._mint_v2_1_offline_rehearsal_capability(
        binding,
        database=database,
        pdf_fetcher=pdf_fetcher,
        pdf_text_extractor=pdf_text_extractor,
        monotonic=monotonic,
        sleep=sleep,
        inference_settings=inference.settings,
        chat_json_fn=inference.chat_json_fn,
        snapshot_loader=inference.snapshot_loader,
        wall_clock=inference.wall_clock,
        execution_id_factory=inference.execution_id_factory,
        prediction_recorded_at_clock=inference.prediction_recorded_at_clock,
        prediction_monotonic_ns_clock=inference.prediction_monotonic_ns_clock,
        implementation_commit=implementation_commit,
    )


def _mocked_inference(
    binding: prepare.HeldoutBinding,
    *,
    execution_context: prepare._OfflineRehearsalCapability,
    harness: InferenceHarness,
) -> int:
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
        raise RehearsalV21Error("mocked inference did not call every eligible candidate once")
    predictions = tuple(
        json.loads(line)
        for line in binding.artifacts["predictions"].read_text().splitlines()
        if line
    )
    states = tuple(
        json.loads(line)
        for line in binding.artifacts["inference_state"].read_text().splitlines()
        if line
    )
    if (
        len(predictions) != FIXTURE_RAW_COUNT
        or len(states) != 2
        or states[0].get("started_at_utc") != FIXED_WALL_CLOCK_TEXT
        or states[1].get("completed_at_utc") != FIXED_WALL_CLOCK_TEXT
        or any(row.get("recorded_at_utc") != FIXED_WALL_CLOCK_TEXT for row in predictions)
        or any(row.get("latency_ms") != 0 for row in predictions)
    ):
        raise RehearsalV21Error("deterministic prediction timing evidence drifted")
    return len(harness.calls)


def _adjudication_contract(
    binding: prepare.HeldoutBinding,
) -> base_seal.V2AdjudicationContract:
    source = heldout_seal.load_registered_contract(
        binding.root / heldout_seal.DESIGN_RELATIVE_PATH,
        project_root=binding.root,
    )
    artifacts = {
        "development_private_selection_manifest": binding.artifacts["private_selection"],
        "development_owner_blind_jsonl": binding.artifacts["owner_blind"],
        "development_ai_draft_jsonl": binding.artifacts["ai_draft"],
        "development_adjudication_html": binding.artifacts["adjudication_ui"],
        "development_owner_raw_export_jsonl": binding.artifacts["owner_export"],
        "development_human_adjudicated_jsonl": binding.artifacts["human_adjudicated"],
        "development_owner_completion_manifest": binding.artifacts["owner_completion"],
    }
    return replace(source, project_root=binding.root, artifacts=artifacts)


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
        raise RehearsalV21Error("offline adjudication UI row count drifted")
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
        raise RehearsalV21Error("offline finalizer row count drifted")


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
        raise RehearsalV21Error("synthetic evaluator dry-run failed")
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
    evaluator._create_only(paths.evaluation_state, evaluator._canonical_json_bytes(started))
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
    terminal = {
        "schema_version": "p4.2a-v2-heldout-evaluation-state-event-v1",
        "event": "evaluation_completed",
        "at_utc": FIXED_WALL_CLOCK_TEXT,
        "synthetic_rehearsal": True,
        "real_heldout_metrics_computed": False,
        "one_shot_consumed": False,
        "report_path": paths.report.relative_to(binding.root).as_posix(),
        "report_sha256": _sha256(report_payload),
        "retries": 0,
    }
    evaluator._append_terminal(paths.evaluation_state, terminal)


def _execute_temp_pipeline_inner(
    *,
    label: str,
    project_root: Path,
    workspace: Path,
    implementation_commit: str,
) -> RehearsalRun:
    binding, database = _temporary_binding(project_root=project_root, workspace=workspace)
    probes: dict[str, bool] = {}
    if label == "run-a":
        probes.update(
            _run_offline_negative_probes(
                binding=binding,
                database=database,
                implementation_commit=implementation_commit,
            )
        )
    clock = DeterministicClock()
    monotonic = clock.monotonic
    sleeper = clock.sleep
    fetcher = _fake_pdf_fetcher
    extractor = _fake_pdf_text_extractor
    inference = _full_inference_harness(binding, timing_clock=clock)
    capability = _mint_offline_capability(
        binding,
        database=database,
        pdf_fetcher=fetcher,
        pdf_text_extractor=extractor,
        monotonic=monotonic,
        sleep=sleeper,
        inference=inference,
        implementation_commit=implementation_commit,
    )
    prepare.run_materialize(
        binding,
        operator_timing_attestation=None,
        database=database,
        pdf_fetcher=fetcher,
        pdf_text_extractor=extractor,
        execution_context=capability,
        monotonic=monotonic,
        sleep=sleeper,
    )
    _mocked_inference(
        binding,
        execution_context=capability,
        harness=inference,
    )
    prepare.run_select_blind(binding, execution_context=capability)
    _run_owner_chain(binding, execution_context=capability)
    _run_synthetic_evaluator(binding, execution_context=capability)
    paths = _artifact_paths(binding)
    artifacts: dict[str, bytes] = {}
    for logical_name, relative in ARTIFACT_INVENTORY:
        path = paths[logical_name]
        if path.is_symlink() or not path.is_file():
            raise RehearsalV21Error(f"full path omitted artifact: {logical_name}")
        if path.relative_to(workspace).as_posix() != relative:
            raise RehearsalV21Error(f"artifact path drifted: {logical_name}")
        payload = path.read_bytes()
        if str(workspace).encode() in payload or workspace.as_uri().encode() in payload:
            raise RehearsalV21Error(f"temporary path leaked into artifact: {logical_name}")
        artifacts[logical_name] = payload
    if len(artifacts) != 14:
        raise RehearsalV21Error("full path did not produce the exact 14 artifacts")
    return RehearsalRun(label=label, artifacts=artifacts, probes=probes)


def _execute_temp_pipeline(
    *,
    label: str,
    project_root: Path,
    workspace: Path,
    implementation_commit: str,
) -> RehearsalRun:
    try:
        with _guarded_temp_execution(
            project_root=project_root,
            workspace=workspace,
        ):
            return _execute_temp_pipeline_inner(
                label=label,
                project_root=project_root,
                workspace=workspace,
                implementation_commit=implementation_commit,
            )
    except RehearsalV21Error:
        raise
    except Exception as exc:
        raise RehearsalV21Error(f"guarded {label} full-path replay failed") from exc


def _probe_artifacts(binding: prepare.HeldoutBinding, name: str) -> dict[str, Path]:
    root = binding.root / ".v2-1-negative-probes" / name
    result: dict[str, Path] = {}
    for key, value in binding.artifacts.items():
        relative = value.relative_to(binding.root)
        result[key] = root / relative
    return result


def _expect_preparation_rejection(call: Callable[[], object], label: str) -> None:
    try:
        call()
    except prepare.HeldoutPreparationError:
        return
    raise RehearsalV21Error(f"negative probe was unexpectedly accepted: {label}")


def _run_runtime_start_negative_probes(binding: prepare.HeldoutBinding) -> bool:
    """Exercise every preregistered real-start rejection with temp-only inputs."""

    probe_root = binding.root / ".v2-1-negative-probes/runtime-start"
    runtime_directory = probe_root / "runtime"
    runtime_directory.mkdir(parents=True, mode=0o700)
    stamp = runtime_directory / "last-success-shanghai-date"
    lock = runtime_directory / ".daily-backup.lock"
    shanghai_date = "2026-08-10"

    _expect_preparation_rejection(
        lambda: prepare._backup_stamp_evidence(runtime_directory, shanghai_date),
        "backup stamp missing",
    )
    stamp.write_bytes(b"2026-08-09\n")
    os.chmod(stamp, 0o600)
    _expect_preparation_rejection(
        lambda: prepare._backup_stamp_evidence(runtime_directory, shanghai_date),
        "backup stamp stale",
    )
    stamp.unlink()
    symlink_target = runtime_directory / "symlink-stamp-target"
    symlink_target.write_bytes(b"2026-08-10\n")
    os.chmod(symlink_target, 0o600)
    stamp.symlink_to(symlink_target)
    _expect_preparation_rejection(
        lambda: prepare._backup_stamp_evidence(runtime_directory, shanghai_date),
        "backup stamp symlink",
    )
    stamp.unlink()
    stamp.write_bytes(b"2026-08-10\n")
    os.chmod(stamp, 0o644)
    _expect_preparation_rejection(
        lambda: prepare._backup_stamp_evidence(runtime_directory, shanghai_date),
        "backup stamp wrong mode",
    )

    launchd_label = "com.alphapilot.database-backup"
    launchd_target = f"gui/{os.getuid()}/{launchd_label}"
    for output, label in (
        ("", "backup LaunchAgent unloaded or incomplete"),
        ("state = running\nlast exit code = 0\n", "backup LaunchAgent running"),
        ("state = not running\nlast exit code = 1\n", "backup LaunchAgent nonzero"),
    ):
        def parse_launchagent_fixture(value: str = output) -> Mapping[str, Any]:
            return cast(
                Mapping[str, Any],
                prepare._parse_launchagent_evidence(
                    label=launchd_label,
                    target=launchd_target,
                    output=value,
                ),
            )

        _expect_preparation_rejection(
            parse_launchagent_fixture,
            label,
        )

    lock.write_bytes(b"")
    os.chmod(lock, 0o600)
    descriptor = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _expect_preparation_rejection(
            lambda: prepare._backup_lock_evidence(runtime_directory),
            "backup lock held",
        )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    backup_directory = binding.root / "data/backups"
    backup_directory.mkdir(parents=True, mode=0o700)
    observed_at = datetime(2026, 8, 10, 15, 30, tzinfo=UTC)
    _expect_preparation_rejection(
        lambda: prepare._verified_backup_evidence(binding, observed_at),
        "backup manifest missing",
    )
    invalid_manifest = backup_directory / "alphapilot-full-invalid.manifest.json"
    invalid_manifest.write_bytes(b"not-json\n")
    _expect_preparation_rejection(
        lambda: prepare._verified_backup_evidence(binding, observed_at),
        "backup manifest invalid",
    )
    invalid_manifest.unlink()

    backup_sha = _sha256(b"synthetic-backup")

    def manifest(created_at: str) -> bytes:
        return _canonical_json_bytes(
            {
                "format_version": 1,
                "managed_by": "alphapilot.db.backup",
                "created_at": created_at,
                "backup": {
                    "filename": "alphapilot-full-synthetic.db",
                    "sha256": backup_sha,
                },
            }
        )

    manifest_path = backup_directory / "alphapilot-full-synthetic.manifest.json"
    manifest_path.write_bytes(manifest("2026-08-10T13:30:00Z"))
    _expect_preparation_rejection(
        lambda: prepare._verified_backup_evidence(binding, observed_at),
        "backup manifest before 22 CST",
    )
    manifest_path.write_bytes(manifest("2026-08-10T14:30:00Z"))
    (backup_directory / "alphapilot-full-synthetic.db").write_bytes(b"synthetic-backup")

    _expect_preparation_rejection(
        lambda: prepare._verified_backup_evidence(binding, observed_at),
        "backup verification failure",
    )
    return True


def _run_offline_negative_probes(
    *,
    binding: prepare.HeldoutBinding,
    database: Path,
    implementation_commit: str,
) -> dict[str, bool]:
    """Exercise fail-closed seams without touching a registered destination."""

    noop_binding = replace(binding, artifacts=_probe_artifacts(binding, "noop-sleeper"))
    fetch_count = 0

    def counting_fetch(url: str, policy: object) -> bytes:
        nonlocal fetch_count
        fetch_count += 1
        return _fake_pdf_fetcher(url, policy)

    def fixed_monotonic() -> float:
        return MONOTONIC_INITIAL_SECONDS

    def noop_sleep(_duration: float) -> None:
        return None

    noop_inference = _inert_inference_harness(
        noop_binding,
        timing_clock=DeterministicClock(),
    )
    noop_capability = _mint_offline_capability(
        noop_binding,
        database=database,
        pdf_fetcher=counting_fetch,
        pdf_text_extractor=_fake_pdf_text_extractor,
        monotonic=fixed_monotonic,
        sleep=noop_sleep,
        inference=noop_inference,
        implementation_commit=implementation_commit,
    )
    try:
        prepare.run_materialize(
            noop_binding,
            operator_timing_attestation=None,
            database=database,
            pdf_fetcher=counting_fetch,
            pdf_text_extractor=_fake_pdf_text_extractor,
            execution_context=noop_capability,
            monotonic=fixed_monotonic,
            sleep=noop_sleep,
        )
    except prepare.HeldoutPreparationError as exc:
        if "did not advance" not in str(exc):
            raise RehearsalV21Error("no-op sleeper failed for the wrong reason") from exc
    else:
        raise RehearsalV21Error("no-op sleeper was accepted")
    noop_outputs_absent = all(
        not path.exists() and not path.is_symlink()
        for key, path in noop_binding.artifacts.items()
        if key in {"materialized_inputs", "materialization_manifest"}
    )
    if fetch_count != 1 or not noop_outputs_absent:
        raise RehearsalV21Error("no-op sleeper crossed the second-fetch/publication boundary")

    policy = gold_builder.announcement_body_policy(
        gold_builder.FrozenContract(
            path=binding.contract.path,
            sha256=binding.contract.sha256,
            document=binding.contract.document,
        )
    )
    pacer = prepare._CninfoStartPacer(lambda: 1_000.0, lambda _duration: None)
    allowed_fetch_calls = 0

    def deterministic_size(_url: str, _policy: object) -> bytes:
        nonlocal allowed_fetch_calls
        allowed_fetch_calls += 1
        raise gold_builder.CandidateDocumentIneligible(
            reason="pdf_exceeds_size_bound",
            measured_value=policy.max_pdf_bytes + 1,
            gate_value=policy.max_pdf_bytes,
            pdf_sha256=None,
        )

    paced_fetch, _checked_extract = prepare._paced_pdf_boundaries(
        pacer,
        deterministic_size,
        _fake_pdf_text_extractor,
    )
    try:
        paced_fetch(
            "https://static.cninfo.com.cn/finalpage/2026-08-06/negative.PDF",
            policy,
        )
    except gold_builder.CandidateDocumentIneligible as exc:
        if exc.reason != "pdf_exceeds_size_bound":
            raise RehearsalV21Error("deterministic ineligible reason drifted") from exc
    else:
        raise RehearsalV21Error("deterministic ineligible fetch was not preserved")

    extract_calls = 0

    def deterministic_short(_payload: bytes, _policy: object) -> gold_builder.ExtractedPdfText:
        nonlocal extract_calls
        extract_calls += 1
        raise gold_builder.CandidateDocumentIneligible(
            reason="pdf_text_below_min_char_gate",
            measured_value=1,
            gate_value=policy.minimum_extracted_characters,
            pdf_sha256=_sha256(b"%PDF-short"),
        )

    _unused_fetch, checked_extract = prepare._paced_pdf_boundaries(
        prepare._CninfoStartPacer(lambda: 1_000.0, lambda _duration: None),
        _fake_pdf_fetcher,
        deterministic_short,
    )
    try:
        checked_extract(b"%PDF-short", policy)
    except gold_builder.CandidateDocumentIneligible as exc:
        if exc.reason != "pdf_text_below_min_char_gate":
            raise RehearsalV21Error("deterministic extractor reason drifted") from exc
    else:
        raise RehearsalV21Error("deterministic ineligible extraction was not preserved")

    unexpected_calls = 0

    def unexpected_fetch(_url: str, _policy: object) -> bytes:
        nonlocal unexpected_calls
        unexpected_calls += 1
        raise gold_builder.GoldSampleError("synthetic transport failure")

    unexpected_paced, _ = prepare._paced_pdf_boundaries(
        prepare._CninfoStartPacer(lambda: 1_000.0, lambda _duration: None),
        unexpected_fetch,
        _fake_pdf_text_extractor,
    )
    try:
        unexpected_paced(
            "https://static.cninfo.com.cn/finalpage/2026-08-06/failure.PDF",
            policy,
        )
    except gold_builder.GoldSampleError:
        pass
    else:
        raise RehearsalV21Error("unexpected transport failure was swallowed")
    if allowed_fetch_calls != 1 or extract_calls != 1 or unexpected_calls != 1:
        raise RehearsalV21Error("candidate failure probe retried a boundary")

    ineligible_binding = replace(
        binding,
        artifacts=_probe_artifacts(binding, "deterministic-ineligible-full-path"),
    )
    ineligible_clock = DeterministicClock()
    ineligible_monotonic = ineligible_clock.monotonic
    ineligible_sleep = ineligible_clock.sleep
    ineligible_fetch_counts: Counter[int] = Counter()
    ineligible_extract_counts: Counter[int] = Counter()

    def mixed_fetch(url: str, body_policy: object) -> bytes:
        identifier = int(url.rsplit("/", 1)[-1].removesuffix(".PDF"))
        ineligible_fetch_counts[identifier] += 1
        if identifier == FIXTURE_ID_START:
            raise gold_builder.CandidateDocumentIneligible(
                reason="pdf_exceeds_size_bound",
                measured_value=policy.max_pdf_bytes + 1,
                gate_value=policy.max_pdf_bytes,
                pdf_sha256=None,
            )
        return _fake_pdf_fetcher(url, body_policy)

    def mixed_extract(pdf_bytes: bytes, body_policy: object) -> gold_builder.ExtractedPdfText:
        identifier = int(pdf_bytes.split()[-1])
        ineligible_extract_counts[identifier] += 1
        if identifier == FIXTURE_ID_START + 1:
            raise gold_builder.CandidateDocumentIneligible(
                reason="pdf_text_below_min_char_gate",
                measured_value=1,
                gate_value=policy.minimum_extracted_characters,
                pdf_sha256=_sha256(pdf_bytes),
            )
        return _fake_pdf_text_extractor(pdf_bytes, body_policy)

    ineligible_inference = _inert_inference_harness(
        ineligible_binding,
        timing_clock=ineligible_clock,
    )
    ineligible_capability = _mint_offline_capability(
        ineligible_binding,
        database=database,
        pdf_fetcher=mixed_fetch,
        pdf_text_extractor=mixed_extract,
        monotonic=ineligible_monotonic,
        sleep=ineligible_sleep,
        inference=ineligible_inference,
        implementation_commit=implementation_commit,
    )
    prepare.run_materialize(
        ineligible_binding,
        operator_timing_attestation=None,
        database=database,
        pdf_fetcher=mixed_fetch,
        pdf_text_extractor=mixed_extract,
        execution_context=ineligible_capability,
        monotonic=ineligible_monotonic,
        sleep=ineligible_sleep,
    )
    ineligible_manifest = json.loads(
        ineligible_binding.artifacts["materialization_manifest"].read_bytes()
    )
    ineligible_counts = cast(Mapping[str, object], ineligible_manifest["counts"])
    if (
        ineligible_counts.get("eligible_candidates") != FIXTURE_RAW_COUNT - 2
        or ineligible_counts.get("ineligible_candidates") != 2
        or ineligible_counts.get("ineligible_by_reason")
        != {
            "pdf_exceeds_size_bound": 1,
            "pdf_text_below_min_char_gate": 1,
        }
        or any(count != 1 for count in ineligible_fetch_counts.values())
        or any(count != 1 for count in ineligible_extract_counts.values())
    ):
        raise RehearsalV21Error(
            "deterministic ineligible candidates were not recorded and continued once"
        )

    def assert_full_abort(
        *,
        name: str,
        failing_fetch: Callable[[str, object], bytes],
    ) -> None:
        failure_binding = replace(
            binding,
            artifacts=_probe_artifacts(binding, name),
        )
        failure_clock = DeterministicClock()
        failure_monotonic = failure_clock.monotonic
        failure_sleep = failure_clock.sleep
        failure_inference = _inert_inference_harness(
            failure_binding,
            timing_clock=failure_clock,
        )
        failure_capability = _mint_offline_capability(
            failure_binding,
            database=database,
            pdf_fetcher=failing_fetch,
            pdf_text_extractor=_fake_pdf_text_extractor,
            monotonic=failure_monotonic,
            sleep=failure_sleep,
            inference=failure_inference,
            implementation_commit=implementation_commit,
        )
        try:
            prepare.run_materialize(
                failure_binding,
                operator_timing_attestation=None,
                database=database,
                pdf_fetcher=failing_fetch,
                pdf_text_extractor=_fake_pdf_text_extractor,
                execution_context=failure_capability,
                monotonic=failure_monotonic,
                sleep=failure_sleep,
            )
        except (gold_builder.GoldSampleError, prepare.HeldoutPreparationError):
            pass
        else:
            raise RehearsalV21Error(f"{name} did not abort materialization")
        if any(
            failure_binding.artifacts[key].exists() or failure_binding.artifacts[key].is_symlink()
            for key in ("materialized_inputs", "materialization_manifest")
        ):
            raise RehearsalV21Error(f"{name} published a partial artifact")

    full_unexpected_calls = 0

    def full_unexpected_fetch(_url: str, _body_policy: object) -> bytes:
        nonlocal full_unexpected_calls
        full_unexpected_calls += 1
        raise gold_builder.GoldSampleError("synthetic unexpected transport failure")

    assert_full_abort(name="unexpected-failure-full-path", failing_fetch=full_unexpected_fetch)
    if full_unexpected_calls != 1:
        raise RehearsalV21Error("unexpected full-path failure was retried")

    unknown_reason_calls = 0

    def unknown_reason_fetch(_url: str, _body_policy: object) -> bytes:
        nonlocal unknown_reason_calls
        unknown_reason_calls += 1
        raise gold_builder.CandidateDocumentIneligible(
            reason="synthetic_unknown_reason",
            measured_value=1,
            gate_value=2,
            pdf_sha256=None,
        )

    assert_full_abort(name="unknown-reason-full-path", failing_fetch=unknown_reason_fetch)
    if unknown_reason_calls != 1:
        raise RehearsalV21Error("unknown ineligible reason was retried")

    invalid_attestations = (
        None,
        prepare.OperatorTimingAttestation("", "clear_for_start", "clear_for_start"),
        prepare.OperatorTimingAttestation("owner", "blocked", "clear_for_start"),
        prepare.OperatorTimingAttestation("owner", "clear_for_start", "blocked"),
    )
    for attestation in invalid_attestations:
        try:
            prepare._operator_attestation_evidence(
                attestation,
                observed_start_shanghai=FIXED_WALL_CLOCK.astimezone(prepare._SHANGHAI),
            )
        except prepare.HeldoutPreparationError:
            continue
        raise RehearsalV21Error("implicit or non-clear operator attestation was accepted")

    contract = _adjudication_contract(binding)
    try:
        heldout_seal.read_bound_blind_bundle(
            binding.artifacts["private_selection"],
            binding.artifacts["owner_blind"],
            contract=contract,
            execution_context=None,
            stage="seal-draft",
        )
    except base_seal.V2AdjudicationError:
        pass
    else:
        raise RehearsalV21Error("seal accepted a missing successor release")
    _expect_preparation_rejection(
        lambda: prepare.validate_v2_1_stage_authorization(
            binding,
            stage="build-adjudication-ui",
            execution_context=None,
        ),
        "adjudication UI missing successor release",
    )
    try:
        heldout_finalizer.finalize_owner_export(
            contract=contract,
            owner_export_path=binding.root / "missing-owner-export.jsonl",
            completed_at=FIXED_WALL_CLOCK_TEXT,
            execution_context=None,
        )
    except base_seal.V2AdjudicationError:
        pass
    else:
        raise RehearsalV21Error("canonical finalize accepted the preparation release scope")

    for forged in (True, "offline", {}, object()):

        def validate_forged(value: object = forged) -> object:
            return prepare.validate_v2_1_stage_authorization(
                binding,
                stage="materialize",
                execution_context=cast(Any, value),
            )

        _expect_preparation_rejection(
            validate_forged,
            "forged offline capability",
        )
    canonical_binding = prepare.load_binding(PROJECT_ROOT)
    _expect_preparation_rejection(
        lambda: prepare.validate_v2_1_stage_authorization(
            canonical_binding,
            stage="materialize",
            execution_context=noop_capability,
        ),
        "offline capability at canonical project root",
    )

    synthetic_release = prepare.V21ReleaseAuthorization(
        project_root=binding.root,
        receipt_path=binding.root / prepare.SUCCESSOR_V2_1_RELEASE_PATH,
        receipt_sha256="0" * 64,
        receipt_creating_commit=implementation_commit,
        preregistration_commit=PREREGISTRATION_COMMIT,
        implementation_commit=implementation_commit,
        rehearsal_evidence_commit=implementation_commit,
        bundle_path=binding.root / prepare.SUCCESSOR_V2_1_BUNDLE_PATH,
        bundle_sha256="1" * 64,
        bundle_root_sha256="2" * 64,
    )
    real_authority = prepare._execution_authority_evidence(synthetic_release)
    offline_authority = prepare._execution_authority_evidence(noop_capability)
    if (
        real_authority.get("mode") != "real_owner_released"
        or not isinstance(real_authority.get("rehearsal_bundle"), Mapping)
        or not isinstance(real_authority.get("release_authorization"), Mapping)
        or offline_authority.get("mode") != "offline_rehearsal"
        or offline_authority.get("rehearsal_bundle") is not None
        or offline_authority.get("release_authorization") is not None
    ):
        raise RehearsalV21Error("execution_authority mode branch evidence drifted")

    return {
        "noop_sleeper": True,
        "deterministic_ineligible": True,
        "unexpected_failure": True,
        "operator_attestation": True,
        "runtime_start_gate": _run_runtime_start_negative_probes(binding),
        "seal_and_ui_gate": True,
        "finalize_real": True,
        "authority_gate": True,
        "execution_authority_modes": True,
    }


def _sanitized_git_environment(*, synthetic_identity: bool = False) -> dict[str, str]:
    environment = {
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
        environment.update(
            {
                "GIT_AUTHOR_NAME": "AlphaPilot synthetic rehearsal",
                "GIT_AUTHOR_EMAIL": "synthetic-rehearsal@invalid.local",
                "GIT_COMMITTER_NAME": "AlphaPilot synthetic rehearsal",
                "GIT_COMMITTER_EMAIL": "synthetic-rehearsal@invalid.local",
            }
        )
    return environment


def _validate_git_metadata_authority(project_root: Path) -> None:
    """Reject mutable Git metadata that can reinterpret bound commit bytes/history."""

    try:
        root = project_root.absolute()
        if root.resolve(strict=True) != root:
            raise RehearsalV21Error("Git authority root is aliased")
        git_directory = root / ".git"
        git_metadata = git_directory.lstat()
        if (
            not stat.S_ISDIR(git_metadata.st_mode)
            or git_directory.is_symlink()
            or git_directory.resolve(strict=True) != git_directory
        ):
            raise RehearsalV21Error("Git metadata authority is not one regular directory")
        info_directory = git_directory / "info"
        if os.path.lexists(info_directory):
            info_metadata = info_directory.lstat()
            if (
                not stat.S_ISDIR(info_metadata.st_mode)
                or info_directory.is_symlink()
                or info_directory.resolve(strict=True) != info_directory
            ):
                raise RehearsalV21Error("Git info metadata authority is aliased")
        grafts = info_directory / "grafts"
        if os.path.lexists(grafts):
            raise RehearsalV21Error("legacy Git graft metadata is forbidden")
    except RehearsalV21Error:
        raise
    except (OSError, RuntimeError) as exc:
        raise RehearsalV21Error("Git metadata authority is unavailable") from exc


def _git_read(project_root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    _validate_git_metadata_authority(project_root)
    completed = subprocess.run(
        [
            "/usr/bin/git",
            *GIT_CONFIG_PREFIX,
            "-C",
            str(project_root),
            *arguments,
        ],
        check=False,
        capture_output=True,
        env=_sanitized_git_environment(),
    )
    if completed.returncode == 0 and completed.stderr:
        raise RehearsalV21Error("Git read emitted unexpected diagnostic output")
    return completed


def _git_binding_for_commit(
    project_root: Path,
    implementation_commit: str,
) -> v2_runner.GitBinding:
    try:
        if not _lowercase_commit(implementation_commit):
            raise RehearsalV21Error("implementation commit is invalid")
        head_result = _git_read(project_root, "rev-parse", "HEAD")
        head = head_result.stdout.decode("ascii", errors="strict").strip()
        if head_result.returncode != 0 or not _lowercase_commit(head):
            raise RehearsalV21Error("current HEAD is unavailable")
        if (
            _git_read(
                project_root,
                "merge-base",
                "--is-ancestor",
                implementation_commit,
                head,
            ).returncode
            != 0
        ):
            raise RehearsalV21Error("current HEAD does not descend from implementation commit")

        def blob_reader(relative: str) -> bytes:
            kind = _git_read(
                project_root,
                "cat-file",
                "-t",
                f"{implementation_commit}:{relative}",
            )
            blob = _git_read(project_root, "show", f"{implementation_commit}:{relative}")
            if kind.returncode != 0 or kind.stdout.strip() != b"blob" or blob.returncode != 0:
                raise v2_runner.RehearsalError(
                    f"implementation commit lacks a control blob: {relative}"
                )
            return blob.stdout

        def commit_exists() -> bool:
            return (
                _git_read(
                    project_root,
                    "cat-file",
                    "-e",
                    f"{implementation_commit}^{{commit}}",
                ).returncode
                == 0
            )

        def required_ancestor_present() -> bool:
            return (
                _git_read(
                    project_root,
                    "merge-base",
                    "--is-ancestor",
                    V1_FAIL_CLOSE_COMMIT,
                    implementation_commit,
                ).returncode
                == 0
            )

        binding = v2_runner.GitBinding(
            implementation_commit=implementation_commit,
            blob_reader=blob_reader,
            commit_exists=commit_exists,
            required_ancestor_present=required_ancestor_present,
        )
        if not binding.commit_exists() or not binding.required_ancestor_present():
            raise RehearsalV21Error("implementation lineage is unavailable")
    except Exception as exc:
        raise RehearsalV21Error("successor implementation Git binding is unavailable") from exc
    completed = _git_read(
        project_root,
        "merge-base",
        "--is-ancestor",
        PREREGISTRATION_COMMIT,
        binding.implementation_commit,
    )
    if completed.returncode != 0:
        raise RehearsalV21Error("implementation commit does not descend from v2.1 preregistration")
    try:
        timing_creation_commit = prepare._unique_added_path_commit(
            project_root,
            PREDICTION_TIMING_PREREG_RELATIVE,
        )
    except prepare.HeldoutPreparationError as exc:
        raise RehearsalV21Error("prediction timing preregistration history drifted") from exc
    timing_ancestry = _git_read(
        project_root,
        "merge-base",
        "--is-ancestor",
        PREDICTION_TIMING_PREREG_COMMIT,
        binding.implementation_commit,
    )
    if (
        timing_creation_commit != PREDICTION_TIMING_PREREG_COMMIT
        or timing_ancestry.returncode != 0
    ):
        raise RehearsalV21Error(
            "implementation commit does not descend from the unique timing preregistration"
        )
    return binding


def _git_binding(project_root: Path) -> v2_runner.GitBinding:
    """Bind a registered preclaim to the exact current implementation HEAD."""

    head = _git_read(project_root, "rev-parse", "HEAD")
    implementation_commit = head.stdout.decode("ascii", errors="strict").strip()
    if head.returncode != 0 or not _lowercase_commit(implementation_commit):
        raise RehearsalV21Error("implementation HEAD is unavailable")
    return _git_binding_for_commit(project_root, implementation_commit)


def _safe_repository_file(project_root: Path, relative: str, label: str) -> Path:
    root = project_root.absolute()
    pure = PurePosixPath(relative)
    if (
        root.resolve(strict=True) != root
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise RehearsalV21Error(f"{label} path escapes or aliases the repository")
    path = root.joinpath(*pure.parts)
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except (OSError, RuntimeError) as exc:
        raise RehearsalV21Error(f"{label} is unavailable") from exc
    if (
        resolved != path.absolute()
        or not resolved.is_relative_to(root)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or path.is_symlink()
    ):
        raise RehearsalV21Error(f"{label} is not one unaliased regular file")
    return path


def _repository_bytecode_fingerprint(project_root: Path) -> dict[str, str]:
    """Snapshot all local Python bytecode paths that a lazy import could mutate."""

    root = project_root.resolve()
    observed: dict[str, str] = {}
    for relative_root in ("scripts", "src", "tests"):
        base = root / relative_root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                if "__pycache__" in path.parts or path.suffix == ".pyc":
                    observed[relative] = f"symlink:{path.readlink()}"
            elif path.is_file() and ("__pycache__" in path.parts or path.suffix == ".pyc"):
                observed[relative] = f"file:{_sha256(path.read_bytes())}"
            elif path.is_dir() and path.name == "__pycache__":
                observed[relative] = "directory"
    return observed


def _preregistration_document(project_root: Path) -> JsonObject:
    payload = _safe_repository_file(
        project_root,
        PREREGISTRATION_RELATIVE.as_posix(),
        "successor preregistration",
    ).read_bytes()
    if _sha256(payload) != PREREGISTRATION_SHA256:
        raise RehearsalV21Error("successor preregistration bytes drifted")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise RehearsalV21Error("successor preregistration is not an object")
    return cast(JsonObject, value)


def _prediction_timing_preregistration_document(project_root: Path) -> JsonObject:
    payload = _safe_repository_file(
        project_root,
        PREDICTION_TIMING_PREREG_RELATIVE.as_posix(),
        "prediction timing preregistration",
    ).read_bytes()
    if _sha256(payload) != PREDICTION_TIMING_PREREG_SHA256:
        raise RehearsalV21Error("prediction timing preregistration bytes drifted")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise RehearsalV21Error("prediction timing preregistration is not an object")
    document = cast(JsonObject, value)
    self_binding = document.get("self_binding_and_ordering")
    scope = document.get("prospective_scope_extension")
    if (
        document.get("status") != "PREREGISTERED_BEFORE_TIMING_SEAM_IMPLEMENTATION"
        or not isinstance(self_binding, Mapping)
        or self_binding.get("this_preregistration_path")
        != PREDICTION_TIMING_PREREG_RELATIVE.as_posix()
        or not isinstance(scope, Mapping)
    ):
        raise RehearsalV21Error("prediction timing preregistration contract drifted")
    raw_paths = scope.get("paths")
    if not isinstance(raw_paths, list):
        raise RehearsalV21Error("prediction timing implementation paths are unavailable")
    observed_paths = tuple(
        item.get("path") if isinstance(item, Mapping) else None for item in raw_paths
    )
    if observed_paths != PREDICTION_TIMING_IMPLEMENTATION_PATHS:
        raise RehearsalV21Error("prediction timing implementation path registry drifted")
    return document


def _prediction_timing_control_binding(
    *,
    project_root: Path,
    git_binding: v2_runner.GitBinding,
) -> None:
    """Bind preregistration ordering and both later timing implementation blobs."""

    document = _prediction_timing_preregistration_document(project_root)
    try:
        creation_commit = prepare._unique_added_path_commit(
            project_root,
            PREDICTION_TIMING_PREREG_RELATIVE,
        )
    except prepare.HeldoutPreparationError as exc:
        raise RehearsalV21Error("prediction timing preregistration history drifted") from exc
    if creation_commit != PREDICTION_TIMING_PREREG_COMMIT:
        raise RehearsalV21Error("prediction timing preregistration creation commit drifted")
    ancestry = _git_read(
        project_root,
        "merge-base",
        "--is-ancestor",
        PREDICTION_TIMING_PREREG_COMMIT,
        git_binding.implementation_commit,
    )
    if ancestry.returncode != 0:
        raise RehearsalV21Error(
            "implementation commit does not descend from timing preregistration"
        )
    current_preregistration = _safe_repository_file(
        project_root,
        PREDICTION_TIMING_PREREG_RELATIVE.as_posix(),
        "prediction timing preregistration",
    ).read_bytes()
    if (
        git_binding.blob_reader(PREDICTION_TIMING_PREREG_RELATIVE.as_posix())
        != current_preregistration
        or _git_read(
            project_root,
            "show",
            f"{PREDICTION_TIMING_PREREG_COMMIT}:"
            f"{PREDICTION_TIMING_PREREG_RELATIVE.as_posix()}",
        ).stdout
        != current_preregistration
    ):
        raise RehearsalV21Error(
            "prediction timing preregistration differs from its unique creation blob"
        )
    scope = cast(Mapping[str, Any], document["prospective_scope_extension"])
    path_records = cast(list[Mapping[str, Any]], scope["paths"])
    for record in path_records:
        relative = cast(str, record["path"])
        recorded_preimplementation_sha = record.get("current_sha256")
        creation_blob = _git_read(
            project_root,
            "show",
            f"{PREDICTION_TIMING_PREREG_COMMIT}:{relative}",
        )
        if (
            creation_blob.returncode != 0
            or not isinstance(recorded_preimplementation_sha, str)
            or _sha256(creation_blob.stdout) != recorded_preimplementation_sha
        ):
            raise RehearsalV21Error(
                f"prediction timing target was not byte-frozen before preregistration: {relative}"
            )
        implementation_blob = git_binding.blob_reader(relative)
        if implementation_blob == creation_blob.stdout:
            raise RehearsalV21Error(
                f"prediction timing target was not implemented after preregistration: {relative}"
            )


def _control_plane_registry_expansion(project_root: Path) -> str:
    ruling_payload = _safe_repository_file(
        project_root,
        SCOPE_CORRECTION_RULING_RELATIVE.as_posix(),
        "scope-correction owner ruling",
    ).read_bytes()
    authorization_payload = _safe_repository_file(
        project_root,
        CONTROL_PLANE_AUTHORIZATION_RELATIVE.as_posix(),
        "control-plane registry authorization",
    ).read_bytes()
    if (
        _sha256(ruling_payload) != SCOPE_CORRECTION_RULING_SHA256
        or _sha256(authorization_payload) != CONTROL_PLANE_AUTHORIZATION_SHA256
    ):
        raise RehearsalV21Error("control-plane registry authority bytes drifted")
    ruling_value = json.loads(ruling_payload)
    authorization_value = json.loads(authorization_payload)
    if not isinstance(ruling_value, dict) or not isinstance(authorization_value, dict):
        raise RehearsalV21Error("control-plane registry authority is not an object")
    ruling = cast(JsonObject, ruling_value)
    authorization = cast(JsonObject, authorization_value)
    ruling_bindings = ruling.get("part_1_bindings_required_by_the_disclosure")
    authorized_scope = authorization.get("part_2_authorised_scope")
    if not isinstance(ruling_bindings, Mapping) or not isinstance(
        authorized_scope, Mapping
    ):
        raise RehearsalV21Error("control-plane registry authority contract drifted")
    modifiable_paths = authorized_scope.get("modifiable_paths_exhaustive")
    permitted_effects = authorized_scope.get("permitted_effects_exhaustive")
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
        or modifiable_paths
        != [
            "scripts/prepare_p4_2a_v2_heldout.py",
            "scripts/rehearse_p4_2a_v2_1_heldout_full_path.py",
            "scripts/validate_p4_2a_v2_1_heldout_rehearsal_bundle.py",
        ]
        or not isinstance(permitted_effects, list)
        or len(permitted_effects) != 3
    ):
        raise RehearsalV21Error("control-plane registry authority contract drifted")
    expansion = permitted_effects[1]
    prefix = "expand the registered path set from 14 to exactly 15 by appending "
    suffix = " to registered_existing_test_updates"
    if (
        not isinstance(expansion, str)
        or not expansion.startswith(prefix)
        or not expansion.endswith(suffix)
    ):
        raise RehearsalV21Error("control-plane registry expansion declaration drifted")
    appended = expansion[len(prefix) : -len(suffix)]
    if appended != FINALIZER_TEST_RELATIVE:
        raise RehearsalV21Error("control-plane appended registry path drifted")
    try:
        ruling_commit = prepare._unique_added_path_commit(
            project_root,
            SCOPE_CORRECTION_RULING_RELATIVE,
        )
        authorization_commit = prepare._unique_added_path_commit(
            project_root,
            CONTROL_PLANE_AUTHORIZATION_RELATIVE,
        )
    except prepare.HeldoutPreparationError as exc:
        raise RehearsalV21Error("control-plane registry authority history drifted") from exc
    if (
        ruling_commit != SCOPE_CORRECTION_RULING_COMMIT
        or authorization_commit != CONTROL_PLANE_AUTHORIZATION_COMMIT
        or _git_read(
            project_root,
            "show",
            f"{ruling_commit}:{SCOPE_CORRECTION_RULING_RELATIVE.as_posix()}",
        ).stdout
        != ruling_payload
        or _git_read(
            project_root,
            "show",
            f"{authorization_commit}:{CONTROL_PLANE_AUTHORIZATION_RELATIVE.as_posix()}",
        ).stdout
        != authorization_payload
    ):
        raise RehearsalV21Error("control-plane registry authority history drifted")
    parents = _git_read(
        project_root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        authorization_commit,
        "--",
    )
    if (
        parents.returncode != 0
        or parents.stdout.decode("ascii", errors="strict").strip().split()
        != [authorization_commit, ruling_commit]
    ):
        raise RehearsalV21Error(
            "control-plane authorization is not directly based on the scope ruling"
        )
    if (
        prepare.SUCCESSOR_V2_1_SCOPE_CORRECTION_RULING_PATH
        != SCOPE_CORRECTION_RULING_RELATIVE
        or prepare.SUCCESSOR_V2_1_SCOPE_CORRECTION_RULING_SHA256
        != SCOPE_CORRECTION_RULING_SHA256
        or prepare.SUCCESSOR_V2_1_SCOPE_CORRECTION_RULING_COMMIT
        != SCOPE_CORRECTION_RULING_COMMIT
        or prepare.SUCCESSOR_V2_1_CONTROL_PLANE_AUTHORIZATION_PATH
        != CONTROL_PLANE_AUTHORIZATION_RELATIVE
        or prepare.SUCCESSOR_V2_1_CONTROL_PLANE_AUTHORIZATION_SHA256
        != CONTROL_PLANE_AUTHORIZATION_SHA256
        or prepare.SUCCESSOR_V2_1_CONTROL_PLANE_AUTHORIZATION_COMMIT
        != CONTROL_PLANE_AUTHORIZATION_COMMIT
        or prepare.SUCCESSOR_V2_1_FINALIZER_TEST_PATH.as_posix() != appended
    ):
        raise RehearsalV21Error("prepare/runner control-plane authority pins disagree")
    return appended


def _registered_implementation_statuses(project_root: Path) -> dict[str, str]:
    """Return the 14-path pre-expansion registry frozen by the base authorities."""

    preregistration = _preregistration_document(project_root)
    contract = preregistration.get("implementation_contract")
    if not isinstance(contract, Mapping):
        raise RehearsalV21Error("successor implementation contract is unavailable")
    expected: dict[str, str] = {}
    for field, status_code in (
        ("registered_modified_consumers", "M"),
        ("registered_new_files", "A"),
        ("registered_existing_test_updates", "M"),
    ):
        values = contract.get(field)
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value for value in values
        ):
            raise RehearsalV21Error(f"successor implementation registry {field} drifted")
        for value in cast(list[str], values):
            if value in expected:
                raise RehearsalV21Error("successor implementation registry has duplicates")
            expected[value] = status_code
    for relative in PREDICTION_TIMING_IMPLEMENTATION_PATHS:
        if relative in expected:
            raise RehearsalV21Error("prediction timing implementation registry has duplicates")
        expected[relative] = "M"
    if len(expected) != PREEXPANSION_IMPLEMENTATION_COUNT:
        raise RehearsalV21Error("pre-expansion implementation registry count drifted")
    return expected


def _expanded_registered_implementation_statuses(
    project_root: Path,
) -> dict[str, str]:
    """Apply the one authorized test-path append and expose the 14 -> 15 transition."""

    expected = dict(_registered_implementation_statuses(project_root))
    appended = _control_plane_registry_expansion(project_root)
    if appended in expected:
        raise RehearsalV21Error("control-plane implementation registry has duplicates")
    expected[appended] = "M"
    if len(expected) != EXPANDED_IMPLEMENTATION_COUNT:
        raise RehearsalV21Error("expanded implementation registry count drifted")
    return expected


def _parse_implementation_name_status(payload: bytes) -> dict[str, str]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RehearsalV21Error("implementation diff is not UTF-8") from exc
    observed: dict[str, str] = {}
    for raw_line in text.splitlines():
        fields = raw_line.split("\t")
        if (
            len(fields) != 2
            or fields[0] not in {"A", "M"}
            or not _safe_relative_text(fields[1])
            or fields[1] in observed
        ):
            raise RehearsalV21Error(
                "implementation commit contains a forbidden or duplicate path operation"
            )
        observed[fields[1]] = fields[0]
    return observed


def _require_exact_implementation_name_status(
    payload: bytes,
    expected: Mapping[str, str],
) -> None:
    if _parse_implementation_name_status(payload) != dict(expected):
        raise RehearsalV21Error("implementation commit path/status surface drifted")


def _validate_implementation_commit_surface(
    *,
    project_root: Path,
    implementation_commit: str,
) -> None:
    parents = _git_read(
        project_root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        implementation_commit,
        "--",
    )
    if parents.returncode != 0:
        raise RehearsalV21Error("implementation commit parent proof failed")
    commits = parents.stdout.decode("ascii", errors="strict").strip().split()
    if commits != [implementation_commit, CONTROL_PLANE_AUTHORIZATION_COMMIT]:
        raise RehearsalV21Error(
            "implementation commit must directly follow the control-plane authorization"
        )
    diff = _git_read(
        project_root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--name-status",
        "--no-renames",
        CONTROL_PLANE_AUTHORIZATION_COMMIT,
        implementation_commit,
        "--",
    )
    if diff.returncode != 0:
        raise RehearsalV21Error("implementation commit diff proof failed")
    _require_exact_implementation_name_status(
        diff.stdout,
        _expanded_registered_implementation_statuses(project_root),
    )


def _declared_preregistration_hashes(project_root: Path) -> dict[str, str]:
    """Derive every byte-frozen path/SHA pair from the frozen preregistration."""

    document = _preregistration_document(project_root)
    declared: dict[str, str] = {}

    def collect(value: object) -> None:
        if isinstance(value, dict):
            relative = value.get("path")
            digest = value.get("sha256")
            if isinstance(relative, str) and isinstance(digest, str):
                pure = PurePosixPath(relative)
                if (
                    pure.is_absolute()
                    or any(part in {"", ".", ".."} for part in pure.parts)
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                ):
                    raise RehearsalV21Error(
                        "successor preregistration contains an invalid byte-frozen reference"
                    )
                prior = declared.setdefault(pure.as_posix(), digest)
                if prior != digest:
                    raise RehearsalV21Error(
                        f"successor preregistration has conflicting hashes: {relative}"
                    )
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(document)
    for relative, digest in declared.items():
        path = _safe_repository_file(
            project_root,
            relative,
            f"preregistered byte-frozen control {relative}",
        )
        if _sha256(path.read_bytes()) != digest:
            raise RehearsalV21Error(
                f"preregistered byte-frozen control differs from its declaration: {relative}"
            )
    return declared


def _registered_control_paths(
    project_root: Path,
    git_binding: v2_runner.GitBinding,
) -> tuple[set[str], set[str]]:
    closure = set(
        v2_runner._local_import_closure(
            entrypoint="scripts/rehearse_p4_2a_v2_1_heldout_full_path.py",
            blob_reader=git_binding.blob_reader,
        )
    )
    declared = _declared_preregistration_hashes(project_root)
    paths = (
        set(closure)
        | set(FROZEN_FILE_SHA256)
        | set(declared)
        | set(PREDICTION_TIMING_IMPLEMENTATION_PATHS)
    )
    prereg = _preregistration_document(project_root)
    implementation = cast(Mapping[str, object], prereg["implementation_contract"])
    for field in (
        "registered_modified_consumers",
        "registered_new_files",
        "registered_existing_test_updates",
    ):
        values = implementation[field]
        if not isinstance(values, list):
            raise RehearsalV21Error(f"implementation registry {field} drifted")
        paths.update(cast(list[str], values))
    paths.add(_control_plane_registry_expansion(project_root))
    return paths, closure


def _v2_1_python_inventory() -> bytes:
    payload = _canonical_json_bytes(
        {
            "abi_flags": sys.abiflags,
            "cache_tag": sys.implementation.cache_tag,
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        }
    )
    if _sha256(payload) != PYTHON_INVENTORY_SHA256:
        raise RehearsalV21Error("Python runtime inventory differs from the frozen runtime")
    return payload


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _v2_1_package_inventory(project_root: Path) -> tuple[bytes, list[str], int]:
    root = project_root.resolve()
    venv_root = root / ".venv"
    scheme = sysconfig.get_preferred_scheme("prefix")
    variables = {"base": venv_root.as_posix(), "platbase": venv_root.as_posix()}
    selected: list[Path] = []
    for key in ("purelib", "platlib"):
        raw = sysconfig.get_path(key, scheme=scheme, vars=variables)
        if not isinstance(raw, str) or not raw:
            raise RehearsalV21Error(f"explicit sysconfig package root is unavailable: {key}")
        candidate = Path(raw).absolute()
        if candidate not in selected:
            selected.append(candidate)
    projected: list[str] = []
    for package_root in selected:
        try:
            metadata = package_root.lstat()
        except OSError as exc:
            raise RehearsalV21Error("fixed package inventory root is unavailable") from exc
        if (
            package_root.resolve(strict=True) != package_root
            or package_root.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or not package_root.is_relative_to(root)
        ):
            raise RehearsalV21Error("fixed package inventory root is aliased")
        projected.append(package_root.relative_to(root).as_posix())
    if projected != [PACKAGE_ROOT_RELATIVE.as_posix()]:
        raise RehearsalV21Error("explicit sysconfig package root projection drifted")
    roots_payload = _canonical_json_bytes(projected)
    if _sha256(roots_payload) != PACKAGE_ROOTS_SHA256:
        raise RehearsalV21Error("fixed package inventory root binding drifted")
    distributions = list(
        importlib.metadata.distributions(path=[path.as_posix() for path in selected])
    )
    rows: list[JsonObject] = []
    names: list[str] = []
    for distribution in distributions:
        raw_name = distribution.metadata["Name"]
        if not isinstance(raw_name, str) or not raw_name:
            raise RehearsalV21Error("package inventory contains an unnamed distribution")
        name = _normalized_distribution_name(raw_name)
        names.append(name)
        rows.append({"name": name, "version": distribution.version})
    if len(names) != 84 or len(set(names)) != 84:
        raise RehearsalV21Error(
            "package inventory count or normalized-name uniqueness drifted"
        )
    rows.sort(key=lambda row: (cast(str, row["name"]), cast(str, row["version"])))
    payload = _canonical_json_bytes(rows)
    if _sha256(payload) != PACKAGE_INVENTORY_SHA256:
        raise RehearsalV21Error("package inventory bytes differ from the frozen runtime")
    return payload, projected, len(distributions)


def _require_loaded_repository_sources_in_closure(
    loaded_sources: Collection[str],
    closure: set[str],
) -> None:
    unexpected_loaded_sources = set(loaded_sources) - closure
    if unexpected_loaded_sources:
        joined = ", ".join(sorted(unexpected_loaded_sources))
        raise RehearsalV21Error(
            "loaded repository sources are absent from the commit-bound AST closure: "
            f"{joined}"
        )


def _control_payloads(
    *,
    project_root: Path,
    git_binding: v2_runner.GitBinding,
) -> tuple[list[JsonObject], dict[str, bytes], bytes, bytes]:
    _validate_implementation_commit_surface(
        project_root=project_root,
        implementation_commit=git_binding.implementation_commit,
    )
    _prediction_timing_control_binding(
        project_root=project_root,
        git_binding=git_binding,
    )
    paths, closure = _registered_control_paths(project_root, git_binding)
    if _REGISTERED_BOOTSTRAP:
        _require_loaded_repository_sources_in_closure(
            _REGISTERED_REPOSITORY_MODULE_PATHS,
            closure,
        )
    declared = _declared_preregistration_hashes(project_root)
    records: list[JsonObject] = []
    payloads: dict[str, bytes] = {}
    for relative in sorted(paths, key=lambda value: value.encode("utf-8")):
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            raise RehearsalV21Error(f"control path escapes repository: {relative}")
        payload = git_binding.blob_reader(relative)
        current = _safe_repository_file(project_root, relative, f"control {relative}")
        if current.read_bytes() != payload:
            raise RehearsalV21Error(f"worktree differs from implementation commit: {relative}")
        frozen = FROZEN_FILE_SHA256.get(relative)
        if frozen is not None and _sha256(payload) != frozen:
            raise RehearsalV21Error(f"frozen control differs from preregistration: {relative}")
        declared_digest = declared.get(relative)
        if declared_digest is not None and _sha256(payload) != declared_digest:
            raise RehearsalV21Error(f"control differs from preregistered declaration: {relative}")
        if relative in closure:
            kind = "package_initializer" if relative.endswith("/__init__.py") else "python_source"
        elif relative == "pyproject.toml":
            kind = "project_manifest"
        elif relative == "uv.lock":
            kind = "lockfile"
        else:
            kind = "frozen_control"
        bundle_relative = f"archive/control-surface/root/repo/{relative}"
        payloads[bundle_relative] = payload
        records.append(
            {
                "logical_name": relative,
                "bundle_relative_path": bundle_relative,
                "source_kind": kind,
                "repository_path": relative,
                "bytes": len(payload),
                "sha256": _sha256(payload),
            }
        )
    python_payload = _v2_1_python_inventory()
    package_payload, _projected_roots, _raw_count = _v2_1_package_inventory(project_root)
    for name, payload, kind in (
        ("python", python_payload, "python_runtime"),
        ("packages", package_payload, "package_inventory"),
    ):
        relative = f"archive/control-surface/root/runtime/{name}.json"
        payloads[relative] = payload
        records.append(
            {
                "logical_name": name,
                "bundle_relative_path": relative,
                "source_kind": kind,
                "repository_path": None,
                "bytes": len(payload),
                "sha256": _sha256(payload),
            }
        )
    records.sort(key=lambda row: cast(str, row["bundle_relative_path"]).encode("utf-8"))
    logical = [cast(str, row["logical_name"]).casefold() for row in records]
    relative_names = [cast(str, row["bundle_relative_path"]).casefold() for row in records]
    if len(logical) != len(set(logical)) or len(relative_names) != len(set(relative_names)):
        raise RehearsalV21Error("control surface contains a duplicate or casefold collision")
    return records, payloads, python_payload, package_payload


def _run_archive(
    run: RehearsalRun,
    *,
    archive_root: str,
) -> tuple[JsonObject, dict[str, bytes], str]:
    records: list[JsonObject] = []
    archived: dict[str, bytes] = {}
    merkle_payloads: dict[str, bytes] = {}
    for logical_name, source_relative in ARTIFACT_INVENTORY:
        payload = run.artifacts[logical_name]
        records.append(
            {
                "logical_name": logical_name,
                "source_relative_path": source_relative,
                "bytes": len(payload),
                "sha256": _sha256(payload),
            }
        )
        archived[f"{archive_root}/{source_relative}"] = payload
        merkle_payloads[source_relative] = payload
    root = _merkle_root(merkle_payloads)
    return (
        {
            "run_label": run.label,
            "archive_root": archive_root,
            "artifact_count": 14,
            "artifacts": records,
            "artifact_merkle_root_sha256": root,
        },
        archived,
        root,
    )


def _ref(document: Mapping[str, Any], section: str, name: str) -> JsonObject:
    value = cast(Mapping[str, Any], cast(Mapping[str, Any], document[section])[name])
    return {"path": value["path"], "sha256": value["sha256"]}


def _lineage(
    project_root: Path,
    implementation_commit: str,
) -> JsonObject:
    prereg = _preregistration_document(project_root)
    authorities = cast(Mapping[str, Any], prereg["authorities"])
    frozen = cast(Mapping[str, Any], prereg["frozen_inputs"])
    frame = cast(Mapping[str, Any], authorities["frame_authority_and_parent_approval"])
    return {
        "preregistration": {
            "path": PREREGISTRATION_RELATIVE.as_posix(),
            "sha256": PREREGISTRATION_SHA256,
        },
        "bundle_schema": {
            "path": BUNDLE_SCHEMA_RELATIVE.as_posix(),
            "sha256": BUNDLE_SCHEMA_SHA256,
        },
        "release_authorization_schema": {
            "path": RELEASE_SCHEMA_RELATIVE.as_posix(),
            "sha256": RELEASE_SCHEMA_SHA256,
        },
        "parent_heldout_preregistration": _ref(
            prereg, "authorities", "parent_heldout_preregistration"
        ),
        "parent_rehearsal_v2_preregistration": _ref(
            prereg, "authorities", "parent_rehearsal_v2_preregistration"
        ),
        "parent_rehearsal_v2_bundle_schema": _ref(
            prereg, "authorities", "parent_rehearsal_v2_bundle_schema"
        ),
        "parent_rehearsal_v2_bundle": _ref(prereg, "authorities", "parent_rehearsal_v2_bundle"),
        "parent_rehearsal_v2_review_request": _ref(
            prereg, "authorities", "parent_rehearsal_v2_review_request"
        ),
        "parent_rehearsal_v2_approval": {
            "path": frame["path"],
            "sha256": frame["sha256"],
        },
        "frame_authority_ruling": {
            "path": frame["path"],
            "sha256": frame["sha256"],
        },
        "successor_v2_1_authorization": _ref(
            prereg, "authorities", "successor_v2_1_code_gate_authorization"
        ),
        "full_pool_cost_acceptance": _ref(prereg, "authorities", "full_pool_cost_acceptance"),
        "same_publisher_interval_basis": _ref(
            prereg, "authorities", "same_publisher_interval_basis"
        ),
        "v1_incident": _ref(prereg, "authorities", "v1_incident"),
        "v1_fail_close_commit": V1_FAIL_CLOSE_COMMIT,
        "preregistration_commit": PREREGISTRATION_COMMIT,
        "implementation_commit": implementation_commit,
        "design": {
            "path": frozen["evaluation_design"]["path"],
            "sha256": frozen["evaluation_design"]["sha256"],
        },
        "heldout_contract": {
            "path": frozen["heldout_execution_contract"]["path"],
            "sha256": frozen["heldout_execution_contract"]["sha256"],
        },
        "round3_prompt": {
            "path": frozen["round3_prompt"]["path"],
            "sha256": frozen["round3_prompt"]["sha256"],
        },
        "round3_plus_contract": {
            "path": frozen["round3_plus_contract"]["path"],
            "sha256": frozen["round3_plus_contract"]["sha256"],
        },
        "retired_v1_artifacts": [
            {"path": path, "sha256": digest} for path, digest in v2_runner.RETIRED_V1_REFERENCES
        ],
    }


def _bundle_candidate(
    *,
    project_root: Path,
    git_binding: v2_runner.GitBinding,
    run_a: RehearsalRun,
    run_b: RehearsalRun,
) -> tuple[JsonObject, dict[str, bytes]]:
    if set(run_a.artifacts) != set(run_b.artifacts) or any(
        run_a.artifacts[name] != run_b.artifacts[name] for name in run_a.artifacts
    ):
        raise RehearsalV21Error("dual runs are not byte-identical across all 14 artifacts")
    required_probe_names = {
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
    if set(run_a.probes) != required_probe_names or not all(run_a.probes.values()):
        raise RehearsalV21Error("run-a fail-closed probes are incomplete")
    if run_b.probes:
        raise RehearsalV21Error("run-b unexpectedly repeated registered negative probes")

    run_a_record, run_a_payloads, run_a_root = _run_archive(
        run_a, archive_root="archive/run-a/root"
    )
    run_b_record, run_b_payloads, run_b_root = _run_archive(
        run_b, archive_root="archive/run-b/root"
    )
    control_records, control_payloads, python_payload, package_payload = _control_payloads(
        project_root=project_root,
        git_binding=git_binding,
    )
    control_manifest_payload = _canonical_json_bytes(
        {"schema_version": CONTROL_MANIFEST_SCHEMA, "files": control_records}
    )
    control_tree_payloads = dict(control_payloads)
    control_tree_payloads["archive/control-surface/manifest.json"] = control_manifest_payload
    control_root = _merkle_root(control_tree_payloads)
    root_digest = _bundle_root(run_a_root, run_b_root, control_root)
    control_manifest_record = {
        "logical_name": "control_surface_manifest",
        "bundle_relative_path": "archive/control-surface/manifest.json",
        "source_kind": "control_manifest",
        "repository_path": None,
        "bytes": len(control_manifest_payload),
        "sha256": _sha256(control_manifest_payload),
    }
    bundle: JsonObject = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "rehearsal_id": REHEARSAL_ID,
        "status": "passed_awaiting_owner_review",
        "lineage": _lineage(project_root, git_binding.implementation_commit),
        "publication": {
            "directory": SUCCESSOR_DIRECTORY_RELATIVE.as_posix(),
            "bundle_manifest": BUNDLE_FILENAME,
            "atomic_create_only": True,
            "directory_absent_before_execution": True,
            "staged_outside_repository": True,
            "published_by_single_atomic_rename": True,
            "symlink_count": 0,
            "unexpected_entry_count": 0,
        },
        "determinism": {
            "run_labels": ["run-a", "run-b"],
            "distinct_temp_roots": True,
            "artifact_count_per_run": 14,
            "byte_identical_artifact_count": 14,
            "mismatch_count": 0,
            "normalization_used": False,
            "wall_clock": {
                "policy": "every_injected_wall_clock_read_returns_the_same_value",
                "value_utc": FIXED_WALL_CLOCK_TEXT,
            },
            "monotonic_clock": {
                "policy": "deterministic_clock_advanced_only_by_registered_sleeper",
                "initial_seconds": MONOTONIC_INITIAL_SECONDS,
                "reset_for_each_run": True,
            },
            "uuid_policy": {
                "algorithm": "uuid5_sha1_rfc4122",
                "namespace": str(UUID_NAMESPACE),
                "uuid4_forbidden": True,
            },
            "temp_root_leak_count": 0,
        },
        "real_entry_gate_validation": {
            "single_registered_entrypoint": (
                "scripts/prepare_p4_2a_v2_heldout.py::run_materialize"
            ),
            "real_context_requires_release_receipt": True,
            "registered_real_release_receipt_used_during_rehearsal": False,
            "synthetic_successor_release_receipt_positive_probe_passed": True,
            "synthetic_receipt_schema_validation_passed": True,
            "synthetic_receipt_git_create_only_history_positive_probe_passed": True,
            "synthetic_receipt_modified_after_creation_negative_probe_passed": True,
            "synthetic_receipt_validator_is_canonical_validator": True,
            "synthetic_receipt_cannot_unlock_canonical_real_execution": True,
            "offline_capability_private_identity": True,
            "offline_capability_non_serializable": True,
            "offline_root_is_distinct_temporary_root": True,
            "offline_database_is_inside_temporary_root": True,
            "offline_fetcher_is_stub": True,
            "canonical_project_root_rejects_offline_capability": run_a.probes["authority_gate"],
            "missing_release_receipt_rejected_before_database_or_artifact_write": (
                run_a.probes["authority_gate"]
            ),
            "offline_manifest_execution_authority_nonrecursive_binding_passed": (
                run_a.probes["execution_authority_modes"]
            ),
            "synthetic_real_manifest_execution_authority_shape_positive_probe_passed": (
                run_a.probes["execution_authority_modes"]
            ),
            "seal_and_ui_direct_release_gate_probes_passed": run_a.probes["seal_and_ui_gate"],
            "canonical_finalize_real_context_rejection_probe_passed": run_a.probes["finalize_real"],
            "operator_timing_attestation_explicit_input_negative_probes_passed": (
                run_a.probes["operator_attestation"]
            ),
            "retired_v1_rejected": True,
            "wrapper_monkeypatch_or_alternate_entry_used": False,
        },
        "request_interval_validation": {
            "cninfo_pdf": {
                "host": "static.cninfo.com.cn",
                "policy": "minimum_start_to_start",
                "min_start_to_start_seconds": 1.0,
                "clock": "monotonic",
                "first_request_delay": False,
                "retries": 0,
                "synthetic_fixture_request_start_count": CNINFO_REQUEST_COUNT,
                "synthetic_fixture_observed_gap_count": CNINFO_GAP_COUNT,
                "minimum_observed_gap_seconds": 1.0,
                "median_observed_gap_seconds": 1.0,
                "violation_count": 0,
            },
            "akshare_ths": "not_applicable_no_external_document_fetch",
            "sina_company_news": "not_applicable_no_external_document_fetch",
            "delay_probe": {
                "result": "PASS_NOOP_SLEEPER_REJECTED_BEFORE_SECOND_FETCH",
                "first_fetch_count": 1,
                "second_fetch_started": False,
                "failure_reason": "monotonic_clock_did_not_advance_after_sleep",
            },
            "candidate_document_handling_probes": {
                "deterministic_ineligible_reasons": [
                    "pdf_text_below_min_char_gate",
                    "pdf_exceeds_size_bound",
                ],
                "deterministic_ineligible_result": (
                    "PASS_RECORDED_EXCLUDED_CONTINUED_ZERO_RETRY_ZERO_RETURN_TO_POOL"
                ),
                "unexpected_failure_result": (
                    "PASS_ABORTED_ENTIRE_MATERIALIZATION_ZERO_RETRY_ZERO_PARTIAL_PUBLISH"
                ),
                "unknown_ineligible_reason_result": (
                    "PASS_FAIL_CLOSED_ZERO_RETRY_ZERO_PARTIAL_PUBLISH"
                ),
            },
        },
        "archive": {
            "runs": [run_a_record, run_b_record],
            "control_surface": {
                "archive_root": "archive/control-surface/root",
                "manifest": control_manifest_record,
                "file_count": len(control_records),
                "tree_member_count": len(control_records) + 1,
                "tree_member_count_rule": "tree_member_count == file_count + 1",
                "manifest_included_in_merkle": True,
                "files": control_records,
                "merkle_root_sha256": control_root,
            },
        },
        "execution_environment": {
            "pyproject": {
                "path": "pyproject.toml",
                "sha256": FROZEN_FILE_SHA256["pyproject.toml"],
            },
            "uv_lock": {"path": "uv.lock", "sha256": FROZEN_FILE_SHA256["uv.lock"]},
            "python": {
                "implementation": "CPython",
                "version": "3.12.0",
                "cache_tag": "cpython-312",
                "abi_flags": "",
                "inventory_path": "archive/control-surface/root/runtime/python.json",
                "inventory_sha256": _sha256(python_payload),
            },
            "packages": {
                "source_call": (
                    "importlib.metadata.distributions(path=derived_absolute_selected_path_roots)"
                ),
                "path_scope": (
                    "deduplicated_resolved_sysconfig_purelib_and_platlib_only_no_sys_path_fallback"
                ),
                "sysconfig_path_keys": ["purelib", "platlib"],
                "absolute_path_roots_policy": "derived_at_validation_time_not_persisted",
                "selected_path_roots_project_relative": [".venv/lib/python3.12/site-packages"],
                "selected_path_roots_sha256": (
                    "fae235892c0988d4093d1ad12b034a6126d116e436393e837a8b2f71601fbd12"
                ),
                "editable_repository_metadata_excluded": True,
                "excluded_repository_metadata_path": "src/alphapilot_ai.egg-info",
                "canonicalization": (
                    "sorted_unique_pep503_normalized_name_and_importlib_metadata_version_"
                    "as_canonical_json_array_newline"
                ),
                "inventory_path": "archive/control-surface/root/runtime/packages.json",
                "raw_distribution_count": 84,
                "count": 84,
                "duplicate_normalized_name_count": 0,
                "duplicate_normalized_name_policy": (
                    "fail_closed_before_inventory_hash_acceptance"
                ),
                "duplicate_metadata_negative_probe": (
                    "PASS_INJECTED_SECOND_SAME_NORMALIZED_NAME_REJECTED"
                ),
                "sha256": _sha256(package_payload),
            },
        },
        "merkle": {
            "hash": "sha256",
            "domain": "p4.2a-rehearsal-v2.1",
            "path_encoding": "utf-8-posix-relative",
            "sort_order": "ascending_unsigned_utf8_path_bytes",
            "leaf_formula": (
                "SHA256(utf8('p4.2a-rehearsal-leaf-v2.1\\0') || utf8(relative_path) "
                "|| NUL || SHA256(file_bytes).digest())"
            ),
            "control_tree_path_basis": (
                "bundle_relative_paths_for_manifest_and_every_control_root_file"
            ),
            "control_manifest_leaf_relative_path": "archive/control-surface/manifest.json",
            "control_manifest_leaf_formula": (
                "SHA256(utf8('p4.2a-rehearsal-leaf-v2.1\\0') || "
                "utf8('archive/control-surface/manifest.json') || NUL || "
                "SHA256(control_manifest_bytes).digest())"
            ),
            "node_formula": (
                "SHA256(utf8('p4.2a-rehearsal-node-v2.1\\0') || "
                "left_digest_32_bytes || right_digest_32_bytes)"
            ),
            "odd_leaf_policy": "duplicate_last_digest_at_each_level",
            "empty_tree_policy": "forbidden",
            "run_a_root_sha256": run_a_root,
            "run_b_root_sha256": run_b_root,
            "control_surface_root_sha256": control_root,
            "bundle_root_formula": (
                "SHA256(utf8('p4.2a-rehearsal-bundle-v2.1\\0') || "
                "run_a_root_digest_32_bytes || run_b_root_digest_32_bytes || "
                "control_surface_root_digest_32_bytes)"
            ),
            "bundle_root_sha256": root_digest,
            "bundle_manifest_is_not_a_member_of_any_merkle_tree": True,
        },
        "semantic_validation": {
            "json_schema_valid": True,
            "lineage_rehash_passed": True,
            "retired_v1_immutability_passed": True,
            "implementation_commit_object_exists": True,
            "implementation_commit_descends_from_preregistration": True,
            "implementation_commit_descends_from_v1_fail_close": True,
            "implementation_commit_tree_binding_passed": True,
            "full_archive_rehash_passed": True,
            "control_manifest_rehash_passed": True,
            "artifact_semantics_passed": True,
            "run_artifact_bytes_equal": True,
            "ast_local_import_closure_complete": True,
            "package_initializers_complete": True,
            "control_surface_exact_set_match": True,
            "environment_binding_passed": True,
            "package_inventory_path_scope_binding_passed": True,
            "package_duplicate_metadata_negative_probe_passed": True,
            "merkle_roots_recomputed": True,
            "real_run_materialize_path_covered": True,
            "materialization_manifest_v2_evidence_recomputed": True,
            "rate_limiter_delay_probe_passed": run_a.probes["noop_sleeper"],
            "synthetic_release_receipt_positive_probe_passed": True,
            "release_receipt_create_only_git_history_probes_passed": True,
            "materialization_execution_authority_mode_branches_recomputed": run_a.probes[
                "execution_authority_modes"
            ],
            "deterministic_ineligible_zero_retry_continuation_probe_passed": run_a.probes[
                "deterministic_ineligible"
            ],
            "unexpected_fetch_or_extract_failure_zero_retry_abort_probe_passed": (
                run_a.probes["unexpected_failure"]
            ),
            "seal_and_ui_release_gate_probes_passed": run_a.probes["seal_and_ui_gate"],
            "canonical_finalize_real_context_rejection_probe_passed": run_a.probes["finalize_real"],
            "operator_timing_attestation_explicit_input_negative_probes_passed": (
                run_a.probes["operator_attestation"]
            ),
            "authority_gate_negative_probes_passed": run_a.probes["authority_gate"],
            "runtime_start_gate_negative_probes_passed": run_a.probes["runtime_start_gate"],
            "full_downstream_path_replayed": True,
            "no_v1_fallback": True,
            "old_v2_accepted_for_current_source": False,
            "v1_receipt_or_gate_accepted": False,
            "independent_gate_result": "PASS_REHEARSAL_V2_1_AWAITING_OWNER_REVIEW",
        },
        "safety": {
            "real_database_reads": 0,
            "real_network_calls": 0,
            "real_model_calls": 0,
            "production_writes": False,
            "production_heldout_artifacts_changed": False,
            "real_heldout_metrics_computed": False,
            "real_metrics_disclosed": False,
            "proposals_or_orders_allowed": False,
        },
        "locks": {
            "trading_mode": "research",
            "live_trading_enabled": False,
            "paper_trading_enabled": False,
            "paper_auto_trading_enabled": False,
            "futu_enable_trade": False,
            "futu_enable_account_mutation": False,
            "unlock_trade_permanently_blocked": True,
            "p4_2a_done": False,
            "p4_2b_unlocked": False,
            "p4_3_unlocked": False,
        },
        "remaining_blockers": {
            "successor_v2_1_owner_review": "PENDING",
            "real_heldout_materialization_unlocked": False,
            "real_heldout_inference_unlocked": False,
            "heldout_metric_evaluation_unlocked": False,
        },
    }
    payloads = {
        **run_a_payloads,
        **run_b_payloads,
        **control_payloads,
        "archive/control-surface/manifest.json": control_manifest_payload,
    }
    return bundle, payloads


def _synthetic_git_environment() -> dict[str, str]:
    return _sanitized_git_environment(synthetic_identity=True)


def _git_text(root: Path, *arguments: str) -> str:
    _validate_git_metadata_authority(root)
    completed = subprocess.run(
        [
            "/usr/bin/git",
            *GIT_CONFIG_PREFIX,
            "-C",
            str(root),
            *arguments,
        ],
        check=False,
        capture_output=True,
        env=_synthetic_git_environment(),
    )
    if completed.returncode != 0 or completed.stderr:
        raise RehearsalV21Error(
            f"synthetic Git command failed: {' '.join(arguments[:2])}: "
            f"{completed.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return completed.stdout.decode("utf-8", errors="strict").strip()


def _write_bundle_tree(
    directory: Path,
    *,
    bundle: Mapping[str, Any],
    payloads: Mapping[str, bytes],
) -> Path:
    if directory.is_symlink() or not directory.is_dir() or any(directory.iterdir()):
        raise RehearsalV21Error("bundle staging directory is not one empty regular directory")
    for relative, payload in sorted(payloads.items(), key=lambda item: item[0].encode()):
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            raise RehearsalV21Error("bundle payload path escapes staging")
        v2_runner._write_exclusive(directory.joinpath(*pure.parts), payload)
    bundle_path = directory / BUNDLE_FILENAME
    v2_runner._write_exclusive(bundle_path, _canonical_json_bytes(bundle))
    v2_runner._fsync_tree(directory)
    return bundle_path


def _synthetic_review_request_payload(bundle: Mapping[str, Any]) -> bytes:
    return _canonical_json_bytes(
        {
            "schema_version": "p4.2a-v2-heldout-rehearsal-v2-1-synthetic-review-request-v1",
            "status": "SYNTHETIC_REHEARSAL_ONLY_NOT_OWNER_REVIEW",
            "bundle_root_sha256": cast(Mapping[str, Any], bundle["merkle"])["bundle_root_sha256"],
            "prediction_timing_preregistration": {
                "path": PREDICTION_TIMING_PREREG_RELATIVE.as_posix(),
                "sha256": PREDICTION_TIMING_PREREG_SHA256,
                "creation_commit": PREDICTION_TIMING_PREREG_COMMIT,
            },
            "real_release_authorized": False,
        }
    )


def _synthetic_release_payload(
    *,
    bundle: Mapping[str, Any],
    bundle_sha256: str,
    implementation_commit: str,
    evidence_commit: str,
    reviewed_head: str,
    review_sha256: str,
) -> bytes:
    bundle_root = cast(Mapping[str, Any], bundle["merkle"])["bundle_root_sha256"]
    receipt: JsonObject = {
        "schema_version": ("p4.2a-v2-heldout-rehearsal-v2-1-release-authorization-v1"),
        "authorization_id": ("P4.2A-V2-HELDOUT-REHEARSAL-V2-1-RELEASE-AUTHORIZATION-20260810"),
        "created_at_utc": "2026-08-10T16:00:00Z",
        "created_at_shanghai": "2026-08-11T00:00:00+08:00",
        "verdict": prepare.SUCCESSOR_V2_1_RELEASE_VERDICT,
        "reviewed_repository_head": reviewed_head,
        "reviewer": {
            "identity": "synthetic-independent-validator",
            "reviewer_type": "ai",
            "model": "offline-deterministic-probe",
            "method": "full_rehash_and_semantic_replay",
            "independent_of_operator": True,
        },
        "owner_authorization": {
            "owner": "ouyang",
            "approved": True,
            "approval_scope": "real_heldout_preparation_only_not_evaluation",
        },
        "lineage": {
            "preregistration": {
                "path": PREREGISTRATION_RELATIVE.as_posix(),
                "sha256": PREREGISTRATION_SHA256,
            },
            "bundle_schema": {
                "path": BUNDLE_SCHEMA_RELATIVE.as_posix(),
                "sha256": BUNDLE_SCHEMA_SHA256,
            },
            "release_schema": {
                "path": RELEASE_SCHEMA_RELATIVE.as_posix(),
                "sha256": RELEASE_SCHEMA_SHA256,
            },
            "bundle": {
                "path": f"{SUCCESSOR_DIRECTORY_RELATIVE.as_posix()}/{BUNDLE_FILENAME}",
                "sha256": bundle_sha256,
            },
            "bundle_root_sha256": bundle_root,
            "preregistration_commit": PREREGISTRATION_COMMIT,
            "implementation_commit": implementation_commit,
            "rehearsal_evidence_commit": evidence_commit,
            "frame_authority_ruling": {
                "path": (
                    "docs/phase4/reports/"
                    "P4.2a-rehearsal-v2-approval-and-frame-authority-ruling-20260810.json"
                ),
                "sha256": ("8605dd30dd6bffa9621b6efe5e01c4c9cead615c9639259c2e79e14fcbbc3421"),
            },
            "successor_v2_1_authorization": {
                "path": (
                    "docs/phase4/reports/P4.2a-successor-v2-1-code-gate-authorization-20260810.json"
                ),
                "sha256": ("e28db692dc150983f86f6760fb1a95584d8607658e8a78a0de35cf3fc81940cd"),
            },
            "review_request": {
                "path": REVIEW_REQUEST_RELATIVE.as_posix(),
                "sha256": review_sha256,
            },
        },
        "independent_checks": {
            "bundle_full_rehash_passed": True,
            "implementation_commit_binding_passed": True,
            "double_run_14_of_14_byte_identical": True,
            "real_run_materialize_path_covered": True,
            "request_interval_evidence_passed": True,
            "noop_sleeper_negative_probe_passed": True,
            "synthetic_successor_release_receipt_positive_probe_passed": True,
            "release_receipt_create_only_git_history_probes_passed": True,
            "materialization_execution_authority_mode_branches_passed": True,
            "deterministic_ineligible_and_unexpected_failure_semantics_passed": True,
            "seal_and_ui_release_gate_probes_passed": True,
            "canonical_finalize_remains_gated_probe_passed": True,
            "operator_timing_attestation_explicit_input_negative_probes_passed": True,
            "full_downstream_replay_passed": True,
            "real_database_reads": 0,
            "real_network_calls": 0,
            "real_model_calls": 0,
            "production_writes": False,
            "old_v2_approval_used_for_current_source": False,
        },
        "authorized_stages": [
            "materialize",
            "infer",
            "select-blind",
            "blind-draft",
            "owner-adjudication-ui",
        ],
        "still_gated": [
            "finalize-owner-adjudication",
            "heldout-evaluation",
            "p4.2b",
            "p4.3",
        ],
        "runtime_start_policy": {
            "live_probe_required": True,
            "probe_timing": "after_release_gate_before_real_network_or_artifact_write",
            "backup_stamp_policy": "current_Asia_Shanghai_date",
            "database_backup_launchagent_policy": ("loaded_state_not_running_last_exit_code_0"),
            "database_backup_lock_policy": "nonblocking_flock_proves_not_held",
            "latest_backup_manifest_policy": (
                "created_after_22_00_CST_quick_check_ok_database_exists"
            ),
            "avoid_cninfo_midnight_batch": True,
            "avoid_dense_p4_1_trading_slots": True,
            "observations_must_be_recorded_in_materialization_manifest": True,
        },
        "locks": {
            "p4_2a_done": False,
            "heldout_evaluation_unlocked": False,
            "p4_2b_unlocked": False,
            "p4_3_unlocked": False,
            "trading_mode": "research",
            "non_simulate_orders_allowed": False,
        },
    }
    return _canonical_json_bytes(receipt)


@contextmanager
def _isolated_temp_directory(prefix: str) -> Iterator[Path]:
    active = _AUDIT_POLICY.get()
    parent = active.write_roots[0] if active is not None else _TEMP_AUTHORITY.get()
    if parent is None:
        raise RehearsalV21Error("isolated temporary directory lacks a preflight authority")
    with tempfile.TemporaryDirectory(prefix=prefix, dir=parent) as raw:
        yield Path(raw).resolve()


@contextmanager
def _synthetic_git_workspace(project_root: Path) -> Iterator[Path]:
    with (
        _isolated_temp_directory("alphapilot-p4-2a-v2-1-release-probe-") as container,
        _audited_execution(
            _AuditPolicy(
                project_root=project_root.resolve(),
                write_roots=(container,),
                sqlite_roots=(container,),
                subprocess_mode="synthetic_git",
                synthetic_git_root=container,
            )
        ),
    ):
        yield container


def _synthetic_release_receipt_probe(
    *,
    project_root: Path,
    implementation_commit: str,
    bundle: Mapping[str, Any],
    payloads: Mapping[str, bytes],
) -> GateProbeEvidence:
    """Prove the real gate's positive and create-only history branches in temp Git."""

    with _synthetic_git_workspace(project_root) as container:
        repository = container / "repository"
        _validate_git_metadata_authority(project_root)
        completed = subprocess.run(
            [
                "/usr/bin/git",
                *GIT_CONFIG_PREFIX,
                "clone",
                "--quiet",
                "--local",
                "--no-checkout",
                str(project_root),
                str(repository),
            ],
            check=False,
            capture_output=True,
            env=_synthetic_git_environment(),
        )
        if completed.returncode != 0 or completed.stderr:
            raise RehearsalV21Error("could not create isolated synthetic Git repository")
        if _git_text(repository, "rev-parse", "HEAD") != implementation_commit:
            raise RehearsalV21Error("synthetic Git clone does not bind implementation HEAD")
        _git_text(repository, "read-tree", implementation_commit)
        control_prefix = "archive/control-surface/root/repo/"
        copied_controls = 0
        for archived_relative, payload in sorted(payloads.items()):
            if not archived_relative.startswith(control_prefix):
                continue
            repository_relative = archived_relative.removeprefix(control_prefix)
            v2_runner._write_exclusive(repository / repository_relative, payload)
            copied_controls += 1
        expected_controls = sum(
            1
            for record in cast(
                Sequence[Mapping[str, Any]],
                cast(Mapping[str, Any], bundle["archive"])["control_surface"]["files"],
            )
            if record.get("repository_path") is not None
        )
        if copied_controls != expected_controls:
            raise RehearsalV21Error("synthetic Git control reconstruction is incomplete")

        bundle_directory = repository / SUCCESSOR_DIRECTORY_RELATIVE
        bundle_directory.mkdir(parents=True, mode=0o700)
        bundle_path = _write_bundle_tree(
            bundle_directory,
            bundle=bundle,
            payloads=payloads,
        )
        review_payload = _synthetic_review_request_payload(bundle)
        review_path = repository / REVIEW_REQUEST_RELATIVE
        v2_runner._write_exclusive(review_path, review_payload)
        _git_text(
            repository,
            "add",
            "--",
            SUCCESSOR_DIRECTORY_RELATIVE.as_posix(),
            REVIEW_REQUEST_RELATIVE.as_posix(),
        )
        _git_text(repository, "commit", "--quiet", "--no-gpg-sign", "-m", "synthetic evidence")
        evidence_commit = _git_text(repository, "rev-parse", "HEAD")
        reviewed_head = evidence_commit
        receipt_payload = _synthetic_release_payload(
            bundle=bundle,
            bundle_sha256=_sha256(bundle_path.read_bytes()),
            implementation_commit=implementation_commit,
            evidence_commit=evidence_commit,
            reviewed_head=reviewed_head,
            review_sha256=_sha256(review_payload),
        )
        receipt_path = repository / RELEASE_AUTHORIZATION_RELATIVE
        v2_runner._write_exclusive(receipt_path, receipt_payload)
        _git_text(
            repository,
            "add",
            "--",
            RELEASE_AUTHORIZATION_RELATIVE.as_posix(),
        )
        _git_text(repository, "commit", "--quiet", "--no-gpg-sign", "-m", "synthetic release")
        authorization = prepare.validate_v2_1_release_authorization(repository)
        if (
            authorization.project_root != repository
            or authorization.implementation_commit != implementation_commit
            or authorization.rehearsal_evidence_commit != evidence_commit
            or authorization.bundle_sha256 != _sha256(bundle_path.read_bytes())
        ):
            raise RehearsalV21Error("synthetic successor release result drifted")

        canonical_binding = prepare.load_binding(project_root)
        try:
            prepare.validate_v2_1_stage_authorization(
                canonical_binding,
                stage="materialize",
                execution_context=authorization,
            )
        except prepare.HeldoutPreparationError:
            canonical_rejected = True
        else:
            raise RehearsalV21Error("synthetic receipt unlocked canonical execution")

        modified = json.loads(receipt_payload)
        modified["reviewer"]["method"] = "committed_after_creation_must_reject"
        receipt_path.write_bytes(_canonical_json_bytes(modified))
        _git_text(
            repository,
            "add",
            "--",
            RELEASE_AUTHORIZATION_RELATIVE.as_posix(),
        )
        _git_text(repository, "commit", "--quiet", "--no-gpg-sign", "-m", "mutate receipt")
        try:
            prepare.validate_v2_1_release_authorization(repository)
        except prepare.HeldoutPreparationError:
            modified_rejected = True
        else:
            raise RehearsalV21Error("modified synthetic receipt was accepted")

    return GateProbeEvidence(
        synthetic_release_positive=True,
        create_only_history_positive=True,
        modified_after_creation_rejected=modified_rejected,
        canonical_validator_used=True,
        canonical_root_unlock_rejected=canonical_rejected,
        noop_sleeper_rejected_before_second_fetch=True,
        deterministic_ineligible_semantics_passed=True,
        unexpected_failure_semantics_passed=True,
        seal_and_ui_gate_probes_passed=True,
        finalize_real_rejection_passed=True,
        attestation_negative_probes_passed=True,
        runtime_start_negative_probes_passed=True,
    )


def registered_execution_claim_directory(
    project_root: Path = PROJECT_ROOT,
    destination: Path | None = None,
) -> Path:
    root = project_root.resolve()
    literal_destination = destination or registered_rehearsal_directory(root)
    material = (
        PREREGISTRATION_SHA256
        + "\0"
        + REHEARSAL_ID
        + "\0"
        + literal_destination.absolute().as_posix()
    ).encode()
    token = hashlib.sha256(material).hexdigest()
    return root.parent / f".alphapilot-p4-2a-v2-1-execution-claim-{token}"


def _claim_registered_execution(root: Path, destination: Path) -> Path:
    claim = registered_execution_claim_directory(root, destination)
    if claim.parent != root.parent:
        raise RehearsalV21Error("registered v2.1 claim parent drifted")
    try:
        v2_runner._preflight_atomic_publication(
            publication_parent=claim.parent,
            destination=destination,
        )
    except Exception as exc:
        raise RehearsalV21Error("registered v2.1 atomic publication is unavailable") from exc
    try:
        os.mkdir(claim, 0o700)
    except FileExistsError as exc:
        raise FileExistsError(
            f"v2.1 rehearsal execution was already claimed and cannot be retried: {claim}"
        ) from exc
    v2_runner._fsync_directory(claim.parent)
    metadata = claim.lstat()
    if (
        claim.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or any(claim.iterdir())
    ):
        raise RehearsalV21Error("registered v2.1 execution claim is invalid")
    return claim


def _test_staging(destination: Path) -> tuple[Path, Path]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    parent = Path(
        tempfile.mkdtemp(
            prefix="alphapilot-p4-2a-v2-1-test-stage-",
            dir=destination.parent,
        )
    ).resolve()
    staged = parent / "bundle"
    staged.mkdir(mode=0o700)
    return staged, parent


def _run_rehearsal_to(
    *,
    project_root: Path,
    destination: Path,
    registered: bool,
    validate_before_publish: bool = True,
) -> Path:
    """Private implementation seam for isolated tests and the fixed CLI."""

    if registered:
        _assert_registered_environment()
    root = project_root.resolve()
    target = destination.absolute()
    registered_target = registered_rehearsal_directory(root)
    if registered:
        if root != PROJECT_ROOT.resolve() or target != registered_target:
            raise RehearsalV21Error("registered execution forbids root or path overrides")
    else:
        if target == registered_target or target.is_relative_to(root):
            raise RehearsalV21Error("test publication must remain outside the repository")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"refusing to overwrite v2.1 rehearsal bundle: {target}")

    # Every ordinary drift/error must fail before the durable one-shot claim.
    git_binding = _git_binding(root)
    _control_payloads(project_root=root, git_binding=git_binding)
    bytecode_before = _repository_bytecode_fingerprint(root)
    protected = (root / "docs/phase4/eval/v2-calibration/heldout").resolve()
    protected_before = v2_runner._tree_fingerprint(protected)
    retired_before = {
        relative: v2_runner._sha256_file(root / relative)
        for relative, _digest in v2_runner.RETIRED_V1_REFERENCES
    }
    for relative, digest in v2_runner.RETIRED_V1_REFERENCES:
        if retired_before.get(relative) != digest:
            raise RehearsalV21Error(f"retired v1 evidence drifted: {relative}")

    temp_authority = _create_temp_authority(
        project_root=root,
        forbidden_paths=(target, protected),
    )
    temp_token = _TEMP_AUTHORITY.set(temp_authority)
    staging_parent: Path | None = None
    try:
        if registered:
            staged = _claim_registered_execution(root, target)
        else:
            staged, staging_parent = _test_staging(target)
    except BaseException:
        _TEMP_AUTHORITY.reset(temp_token)
        _remove_temp_authority(temp_authority)
        raise

    try:
        with (
            _isolated_temp_directory("alphapilot-p4-2a-v2-1-run-a-") as run_a_root,
            _isolated_temp_directory("alphapilot-p4-2a-v2-1-run-b-") as run_b_root,
        ):
            if (
                run_a_root == run_b_root
                or run_a_root.is_relative_to(root)
                or run_b_root.is_relative_to(root)
                or run_a_root.is_relative_to(target)
                or run_b_root.is_relative_to(target)
            ):
                raise RehearsalV21Error("dual replay roots are not distinct and isolated")
            run_a = _execute_temp_pipeline(
                label="run-a",
                project_root=root,
                workspace=run_a_root,
                implementation_commit=git_binding.implementation_commit,
            )
            run_b = _execute_temp_pipeline(
                label="run-b",
                project_root=root,
                workspace=run_b_root,
                implementation_commit=git_binding.implementation_commit,
            )
        if run_a_root.exists() or run_b_root.exists():
            raise RehearsalV21Error("dual replay roots were not removed")
        if v2_runner._tree_fingerprint(protected) != protected_before:
            raise RehearsalV21Error("production held-out tree changed during rehearsal")
        retired_after = {
            relative: v2_runner._sha256_file(root / relative) for relative in retired_before
        }
        if retired_after != retired_before:
            raise RehearsalV21Error("retired v1 evidence changed during rehearsal")

        bundle, payloads = _bundle_candidate(
            project_root=root,
            git_binding=git_binding,
            run_a=run_a,
            run_b=run_b,
        )
        gate_evidence = _synthetic_release_receipt_probe(
            project_root=root,
            implementation_commit=git_binding.implementation_commit,
            bundle=bundle,
            payloads=payloads,
        )
        if not all(vars(gate_evidence).values()):
            raise RehearsalV21Error("successor gate probe evidence is incomplete")
        bundle_path = _write_bundle_tree(staged, bundle=bundle, payloads=payloads)
        if validate_before_publish:
            from scripts.validate_p4_2a_v2_1_heldout_rehearsal_bundle import (
                validate_bundle,
            )

            validate_bundle(project_root=root, bundle_path=bundle_path)
        if _repository_bytecode_fingerprint(root) != bytecode_before:
            raise RehearsalV21Error(
                "canonical repository bytecode changed during the offline rehearsal"
            )
        try:
            v2_runner._atomic_directory_create_only(staged, target)
        except Exception as exc:
            if isinstance(exc, FileExistsError):
                raise
            raise RehearsalV21Error("v2.1 atomic publication failed") from exc
        return target / BUNDLE_FILENAME
    finally:
        if not registered and staging_parent is not None:
            shutil.rmtree(staging_parent, ignore_errors=True)
        _TEMP_AUTHORITY.reset(temp_token)
        _remove_temp_authority(temp_authority)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.execute:
        print("ERROR: --execute is required", file=sys.stderr)
        return 2
    try:
        bundle = run_rehearsal()
    except (FileExistsError, OSError, RehearsalV21Error, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "passed_awaiting_owner_review",
                "bundle": bundle.relative_to(PROJECT_ROOT).as_posix(),
                "real_database_reads": 0,
                "real_network_calls": 0,
                "real_model_calls": 0,
            },
            sort_keys=True,
        )
    )
    return 0


def run_rehearsal() -> Path:
    """Execute the one fixed, create-only registered successor-v2.1 rehearsal."""

    if os.environ.get(_EARLY_ENVIRONMENT_MARKER) != "1":
        raise RehearsalV21Error("registered v2.1 execution requires the locked CLI")
    return _run_rehearsal_to(
        project_root=PROJECT_ROOT,
        destination=registered_rehearsal_directory(PROJECT_ROOT),
        registered=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
