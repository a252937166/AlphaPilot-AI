#!/usr/bin/env python3
"""Thin, effect-free bootstrap for the registered P4.2a v2.2 rehearsal.

This file deliberately owns no rehearsal authority or state.  Before importing
the single package implementation it verifies the first operating-system exec,
fixes the only permitted import path, and rejects every ambient invocation.
"""

from __future__ import annotations

import os
import sys

_ENVIRONMENT_MARKER = "ALPHAPILOT_P42A_REHEARSAL_V2_2_ENV_LOCKED"
_REGISTERED_PROJECT_ROOT = "/Users/ouyangduning/Documents/project/interesting/AlphaPilot-AI"
_FIXED_PYTHON_LAUNCHER = _REGISTERED_PROJECT_ROOT + "/.venv/bin/python"
_FIXED_PYTHON_SHA256 = "f4cd716d4b54f205398bec6932cc59361b087494ca2ddb157a5e8631d4d6f863"
_FIXED_ORIG_ARGV_EXECUTABLE = (
    "/Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python"
)
_FIXED_ORIG_ARGV_EXECUTABLE_SHA256 = (
    "89c717ced41f6a395612366e5b038226d0d8fca36bbddd9321d385f5f370ebbe"
)
_SHIM_SUFFIX = "/scripts/rehearse_p4_2a_v2_2_heldout_full_path.py"
_EXACT_ENVIRONMENT = {
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
    _ENVIRONMENT_MARKER: "1",
}


def _sha256_file(path: str) -> str:
    """Compute SHA-256 using only os and Python integer primitives."""

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
            words.append((words[index - 16] + sigma_0 + words[index - 7] + sigma_1) & mask)
        a, b, c, d, e, f, g, h = state
        for index, constant in enumerate(constants):
            sum_1 = (
                ((e >> 6) | (e << 26)) ^ ((e >> 11) | (e << 21)) ^ ((e >> 25) | (e << 7))
            ) & mask
            choice = (e & f) ^ ((~e) & g)
            first = (h + sum_1 + choice + constant + words[index]) & mask
            sum_0 = (
                ((a >> 2) | (a << 30)) ^ ((a >> 13) | (a << 19)) ^ ((a >> 22) | (a << 10))
            ) & mask
            majority = (a & b) ^ (a & c) ^ (b & c)
            second = (sum_0 + majority) & mask
            h, g, f, e, d, c, b, a = (
                g,
                f,
                e,
                (d + first) & mask,
                c,
                b,
                a,
                (first + second) & mask,
            )
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


def _regular_unaliased_file(path: str, expected_sha256: str) -> bool:
    absolute = os.path.abspath(path)
    resolved = os.path.realpath(absolute)
    try:
        metadata = os.lstat(resolved)
    except OSError:
        return False
    return (
        absolute == resolved
        and not os.path.islink(resolved)
        and metadata.st_mode & 0o170000 == 0o100000
        and _sha256_file(resolved) == expected_sha256
    )


def _derived_project_root() -> str:
    absolute = os.path.abspath(__file__)
    resolved = os.path.realpath(absolute)
    try:
        metadata = os.lstat(absolute)
    except OSError as exc:
        raise RuntimeError("v2.2 thin shim is unavailable") from exc
    if (
        absolute != resolved
        or os.path.islink(absolute)
        or metadata.st_mode & 0o170000 != 0o100000
        or not resolved.endswith(_SHIM_SUFFIX)
    ):
        raise RuntimeError("v2.2 thin shim path is aliased or malformed")
    root = resolved[: -len(_SHIM_SUFFIX)]
    if not root or os.path.realpath(root) != root:
        raise RuntimeError("v2.2 project root is aliased")
    return root


def _expected_sys_path(project_root: str) -> tuple[str, ...]:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("v2.2 requires the frozen CPython 3.12 runtime")
    stdlib = os.path.join(sys.base_prefix, "lib", "python3.12")
    candidates = (
        os.path.join(os.path.dirname(stdlib), "python312.zip"),
        stdlib,
        os.path.join(stdlib, "lib-dynload"),
        _REGISTERED_PROJECT_ROOT + "/.venv/lib/python3.12/site-packages",
        project_root,
        project_root + "/src",
    )
    result: list[str] = []
    for candidate in candidates:
        absolute = os.path.abspath(candidate)
        if absolute not in result:
            result.append(absolute)
    return tuple(result)


def _verify_first_os_bootstrap(project_root: str) -> None:
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    expected_orig_argv = (
        _FIXED_ORIG_ARGV_EXECUTABLE,
        "-S",
        "-P",
        "-B",
        os.path.realpath(__file__),
        *sys.argv[1:],
    )
    if (
        __name__ != "__main__"
        or not isinstance(main_file, str)
        or os.path.realpath(main_file) != os.path.realpath(__file__)
        or dict(os.environ) != _EXACT_ENVIRONMENT
        or tuple(sys.orig_argv) != expected_orig_argv
        or sys.flags.hash_randomization != 0
        or sys.flags.no_site != 1
        or sys.flags.no_user_site != 1
        or not sys.flags.safe_path
        or not sys.dont_write_bytecode
        or sys.pycache_prefix != "/dev/null"
        or os.path.abspath(sys.executable) != _FIXED_PYTHON_LAUNCHER
        or not _regular_unaliased_file(
            os.path.realpath(_FIXED_PYTHON_LAUNCHER),
            _FIXED_PYTHON_SHA256,
        )
        or not _regular_unaliased_file(
            _FIXED_ORIG_ARGV_EXECUTABLE,
            _FIXED_ORIG_ARGV_EXECUTABLE_SHA256,
        )
        or project_root != _derived_project_root()
    ):
        raise RuntimeError(
            "v2.2 execution must begin with the exact registered env -i CPython -S -P -B invocation"
        )
    expected_paths = _expected_sys_path(project_root)
    sys.path[:] = list(expected_paths)
    if tuple(sys.path) != expected_paths:
        raise RuntimeError("v2.2 thin shim import authority drifted")


if __name__ == "__main__":
    _project_root = _derived_project_root()
    _verify_first_os_bootstrap(_project_root)
    import scripts.p4_2a_v2_2_heldout_rehearsal as _implementation

    raise SystemExit(_implementation.cli_main())
