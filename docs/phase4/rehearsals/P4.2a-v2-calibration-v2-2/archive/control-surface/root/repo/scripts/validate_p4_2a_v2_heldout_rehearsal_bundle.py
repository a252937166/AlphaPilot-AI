from __future__ import annotations

import argparse
import ast
import builtins
import fcntl
import hashlib
import importlib.metadata
import io
import json
import locale
import math
import os
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn
from unittest.mock import patch

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PREREGISTRATION_PATH = Path(
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-preregistration-20260810.json"
)
PREREGISTRATION_SHA256 = "35b6d757876e1308d8f28ded3dc36784afb4e5d7c5c1589b8c211cc079aac7c3"
BUNDLE_SCHEMA_PATH = Path("config/schemas/p4_2a_v2_heldout_rehearsal_bundle_v2.schema.json")
BUNDLE_SCHEMA_SHA256 = "f5ff0516c58f2285302dab5d1a03daafd70ea887d4f323387d44c7c19623a8bc"
V1_FAIL_CLOSE_COMMIT = "d710e885b49006eedf4f70ea09cb81fe15d176a3"
CONTROL_MANIFEST_SCHEMA = "p4.2a-v2-heldout-rehearsal-control-manifest-v2"
BUNDLE_FILENAME = "bundle.json"
V1_REHEARSAL_DIRECTORY = Path("docs/phase4/rehearsals/P4.2a-v2-calibration")
REGISTERED_V2_REHEARSAL_DIRECTORY = Path("docs/phase4/rehearsals/P4.2a-v2-calibration-v2")

REPOSITORY_SOURCE_KINDS = frozenset(
    {"python_source", "package_initializer", "frozen_control", "project_manifest", "lockfile"}
)
RUNTIME_SOURCE_KINDS = frozenset({"python_runtime", "package_inventory"})
PYTHON_SOURCE_KINDS = frozenset({"python_source", "package_initializer"})
REQUIRED_SEED_PATHS = frozenset(
    {
        "scripts/rehearse_p4_2a_v2_heldout_full_path.py",
        PREREGISTRATION_PATH.as_posix(),
        BUNDLE_SCHEMA_PATH.as_posix(),
        "docs/phase4/reports/P4.2a-v2-heldout-preregistration-20260810.json",
        "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v1-incident-20260810.json",
        "config/p4_event_evaluation_v2.yaml",
        "config/p4_event_extract_eval_v2-heldout-qwen3.6-plus.yaml",
        "config/prompts/p4_news_event_extract_v2-r3.txt",
        "config/p4_event_extract_eval_v2-r3-qwen3.6-plus.yaml",
        "docs/phase4/reports/P4.2a-round3-adjudication-and-model-selection-override-20260810.json",
        "docs/phase4/reports/P4.2a-cost-correction-and-P4.2b-throughput-backlog-20260810.json",
        "pyproject.toml",
        "uv.lock",
    }
)
FROZEN_AUTHORITY_SHA256 = {
    PREREGISTRATION_PATH.as_posix(): PREREGISTRATION_SHA256,
    BUNDLE_SCHEMA_PATH.as_posix(): BUNDLE_SCHEMA_SHA256,
    "docs/phase4/reports/P4.2a-v2-heldout-preregistration-20260810.json": (
        "ccecbf5ca7b48b16e445318b8c94a08927432f92c7e8c12f8ab40f2916578705"
    ),
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v1-incident-20260810.json": (
        "c3224b288f5181131351ae711a673ce94ec603375925d0cc968cef85d103e785"
    ),
    "config/p4_event_evaluation_v2.yaml": (
        "18a2428a4ec04bfea6e4f4d70692f38ea82fbaee5a223f30f2465b895b238e21"
    ),
    "config/p4_event_extract_eval_v2-heldout-qwen3.6-plus.yaml": (
        "26be1765204b122908e7bd09cac857c33bd3140233df47dc3358bc590e020199"
    ),
    "config/prompts/p4_news_event_extract_v2-r3.txt": (
        "0291dc882aac42878ba00c4ed3970da72f19508308cd39211467b4fd92294f44"
    ),
    "config/p4_event_extract_eval_v2-r3-qwen3.6-plus.yaml": (
        "fa75a6cf33065745d02f74fe39e4f102723da43f37ac549058bb34fa8256a181"
    ),
    "docs/phase4/reports/P4.2a-round3-adjudication-and-model-selection-override-20260810.json": (
        "2f8d0309c4071373d85d8835e01128468db15e3cdfb596331060ebd41d73957c"
    ),
    "docs/phase4/reports/P4.2a-cost-correction-and-P4.2b-throughput-backlog-20260810.json": (
        "e42eeb2342412662a84ce0304015e6f236661069b1e725f18fd8f4dfd3fd05c5"
    ),
    "docs/phase4/eval/v2-calibration/development/P4.2a-development-v2-selection-outcome.json": (
        "36b5a004b294f012b4ab1dab659d1b3d5d98320d794ad2fe90960a617f554da1"
    ),
    (
        "docs/phase4/eval/v2-calibration/development/"
        "P4.2a-development-v2-selected-contract-freeze.json"
    ): ("0ebc5362055af7ef6409155befc5e09d345cd4f2d8d128ea0791a0c293f66f75"),
    "docs/phase4/eval/v2-calibration/development/rounds/r3/round-preregistration.json": (
        "72517cdc546adedf543e9e9abffecdca3b18a2193f2865f7c476edde25512134"
    ),
    "pyproject.toml": "b38481e57b0ba88d1b9b728c2a57583d55cf175262a8a803b483cf4823e13e29",
    "uv.lock": "10829f7ef74adfcbd4401000112b5539c899a899d09d8a3f78fdf8d95803a673",
}
LOCKED_EXECUTION_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
    "TZ": "UTC",
}
RETIRED_V1 = (
    (
        "docs/phase4/rehearsals/P4.2a-v2-calibration/contract.json",
        "61ff631b0dc025bf7441fd3bd04636d0d6d34e5085b0ca570e6f8e2fcfd298ac",
    ),
    (
        "docs/phase4/rehearsals/P4.2a-v2-calibration/inputs.jsonl",
        "a6c5e6e0da6341cecf01df038b36964af6d8bb433b72babf953b385addc87707",
    ),
    (
        "docs/phase4/rehearsals/P4.2a-v2-calibration/expected.json",
        "557433d6ebdd6e3585aad18c6204e28c1cae6417ab3b1b3591474c5f0189b9ac",
    ),
    (
        "docs/phase4/rehearsals/P4.2a-v2-calibration/pass-receipt.json",
        "2610a8b3885426e44a7f32c1d964defcaa46a118bb531a2ecc9b0f3aefa1e0f5",
    ),
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_PEP503_RE = re.compile(r"[-_.]+")


class RehearsalV2ValidationError(RuntimeError):
    """The deterministic rehearsal v2 bundle failed closed."""


JsonObject = dict[str, Any]


def _reject_constant(value: str, *, source: str) -> NoReturn:
    raise RehearsalV2ValidationError(f"{source}: non-finite JSON number is forbidden: {value}")


def strict_json_loads(payload: bytes | str, *, source: str = "JSON") -> Any:
    """Decode strict UTF-8 JSON, rejecting duplicates, non-finite values and negative zero."""

    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise RehearsalV2ValidationError(f"{source}: payload is not UTF-8") from exc
    elif isinstance(payload, str):
        text = payload
    else:
        raise RehearsalV2ValidationError(f"{source}: payload must be bytes or text")

    def object_pairs(pairs: Sequence[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise RehearsalV2ValidationError(
                    f"{source}: duplicate JSON object key is forbidden: {key}"
                )
            result[key] = value
        return result

    def integer(raw: str) -> int:
        value = int(raw)
        if raw.startswith("-") and value == 0:
            raise RehearsalV2ValidationError(f"{source}: negative zero is forbidden")
        return value

    def floating(raw: str) -> float:
        try:
            decimal = Decimal(raw)
        except InvalidOperation as exc:
            raise RehearsalV2ValidationError(f"{source}: invalid JSON number") from exc
        value = float(raw)
        if not math.isfinite(value):
            raise RehearsalV2ValidationError(
                f"{source}: non-finite JSON number is forbidden: {raw}"
            )
        if raw.startswith("-") and decimal.is_zero():
            raise RehearsalV2ValidationError(f"{source}: negative zero is forbidden")
        return value

    try:
        return json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_int=integer,
            parse_float=floating,
            parse_constant=lambda raw: _reject_constant(raw, source=source),
        )
    except RehearsalV2ValidationError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RehearsalV2ValidationError(f"{source}: invalid JSON") from exc


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise RehearsalV2ValidationError(f"{label} must be a JSON object")
    return dict(value)


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RehearsalV2ValidationError(f"{label} must be a JSON array")
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
        ).encode("utf-8")
        + b"\n"
    )


def _relative_posix(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RehearsalV2ValidationError(f"{label} is not a relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value.endswith("/")
        or "//" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise RehearsalV2ValidationError(f"{label} is not a canonical relative POSIX path")
    return value


def _safe_path(root: Path, relative: object, label: str) -> Path:
    value = _relative_posix(relative, label)
    path = root.joinpath(*PurePosixPath(value).parts)
    try:
        resolved_parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise RehearsalV2ValidationError(f"{label} parent is unavailable") from exc
    if not resolved_parent.is_relative_to(root.resolve()):
        raise RehearsalV2ValidationError(f"{label} escapes its root")
    return path


def _regular_bytes(path: Path, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RehearsalV2ValidationError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise RehearsalV2ValidationError(f"{label} is not one regular file")
    if metadata.st_nlink != 1:
        raise RehearsalV2ValidationError(f"{label} is hard-linked")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RehearsalV2ValidationError(f"{label} cannot be read") from exc


def _bound_repository_file(root: Path, relative: object, digest: object, label: str) -> bytes:
    value = _relative_posix(relative, f"{label}.path")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise RehearsalV2ValidationError(f"{label}.sha256 is invalid")
    path = root.joinpath(*PurePosixPath(value).parts)
    payload = _regular_bytes(path, label)
    if _sha256(payload) != digest:
        raise RehearsalV2ValidationError(f"{label} SHA-256 drifted")
    return payload


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=False,
        capture_output=True,
    )


def _commit_blob(root: Path, commit: str, relative: str) -> bytes:
    kind = _git(root, "cat-file", "-t", f"{commit}:{relative}")
    if kind.returncode != 0 or kind.stdout.strip() != b"blob":
        raise RehearsalV2ValidationError(f"implementation commit has no regular blob: {relative}")
    result = _git(root, "show", f"{commit}:{relative}")
    if result.returncode != 0:
        raise RehearsalV2ValidationError(f"implementation commit blob cannot be read: {relative}")
    return result.stdout


def _require_commit_blob_equality(
    root: Path,
    commit: str,
    relative: str,
    archived_payload: bytes,
) -> bytes:
    blob = _commit_blob(root, commit, relative)
    if blob != archived_payload:
        raise RehearsalV2ValidationError(
            f"archived control differs from implementation commit: {relative}"
        )
    return blob


def _validate_commit(root: Path, commit: object) -> str:
    if not isinstance(commit, str) or _COMMIT_RE.fullmatch(commit) is None:
        raise RehearsalV2ValidationError("implementation_commit is invalid")
    if _git(root, "cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
        raise RehearsalV2ValidationError("implementation_commit does not resolve to a commit")
    if _git(root, "merge-base", "--is-ancestor", V1_FAIL_CLOSE_COMMIT, commit).returncode != 0:
        raise RehearsalV2ValidationError(
            "implementation_commit does not descend from the v1 fail-close commit"
        )
    return commit


def registered_rehearsal_directory(project_root: Path = PROJECT_ROOT) -> Path:
    """Return the only literal path accepted for the registered v2 bundle."""

    literal = (project_root / REGISTERED_V2_REHEARSAL_DIRECTORY).absolute()
    try:
        resolved = literal.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise RehearsalV2ValidationError(
            "registered rehearsal literal path cannot be resolved safely"
        ) from exc
    if resolved != literal:
        raise RehearsalV2ValidationError(
            "registered rehearsal path is not the literal symlink-free authority"
        )

    current = Path(literal.anchor)
    for component in literal.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RehearsalV2ValidationError(
                "registered rehearsal literal path cannot be inspected safely"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise RehearsalV2ValidationError("registered rehearsal path contains a symlink")
    return literal


def _merkle_leaf(relative: str, payload: bytes) -> bytes:
    return hashlib.sha256(
        b"p4.2a-rehearsal-leaf-v2\0"
        + relative.encode("utf-8")
        + b"\0"
        + hashlib.sha256(payload).digest()
    ).digest()


def _merkle_root(payloads: Mapping[str, bytes]) -> str:
    if not payloads:
        raise RehearsalV2ValidationError("empty Merkle tree is forbidden")
    paths = list(payloads)
    folded = [path.casefold() for path in paths]
    if len(paths) != len(set(paths)) or len(folded) != len(set(folded)):
        raise RehearsalV2ValidationError("Merkle paths duplicate or casefold-collide")
    nodes = [
        _merkle_leaf(path, payloads[path])
        for path in sorted(paths, key=lambda item: item.encode("utf-8"))
    ]
    while len(nodes) > 1:
        if len(nodes) % 2:
            nodes.append(nodes[-1])
        nodes = [
            hashlib.sha256(b"p4.2a-rehearsal-node-v2\0" + nodes[index] + nodes[index + 1]).digest()
            for index in range(0, len(nodes), 2)
        ]
    return nodes[0].hex()


def _require_merkle_root(payloads: Mapping[str, bytes], expected: object, label: str) -> str:
    actual = _merkle_root(payloads)
    if not isinstance(expected, str) or actual != expected:
        raise RehearsalV2ValidationError(f"{label} Merkle root drifted")
    return actual


def _filesystem_inventory(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    seen_casefold: dict[str, str] = {}
    for current_raw, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_raw)
        for name in [*directory_names, *file_names]:
            path = current / name
            relative = path.relative_to(root).as_posix()
            folded = relative.casefold()
            prior = seen_casefold.get(folded)
            if prior is not None and prior != relative:
                raise RehearsalV2ValidationError(
                    f"bundle contains casefold-colliding paths: {prior}, {relative}"
                )
            seen_casefold[folded] = relative
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise RehearsalV2ValidationError(f"bundle contains a symlink: {relative}")
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(relative)
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise RehearsalV2ValidationError(
                        f"bundle contains a hard-linked file: {relative}"
                    )
                files.add(relative)
            else:
                raise RehearsalV2ValidationError(
                    f"bundle contains a special filesystem entry: {relative}"
                )
    return files, directories


def _expected_directories(files: set[str]) -> set[str]:
    result: set[str] = set()
    for relative in files:
        path = PurePosixPath(relative)
        for parent in path.parents:
            if parent.as_posix() != ".":
                result.add(parent.as_posix())
    return result


def _schema_validate(bundle: JsonObject, schema: JsonObject) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(bundle),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
    except SchemaError as exc:
        raise RehearsalV2ValidationError("registered bundle schema is invalid") from exc
    if errors:
        error: ValidationError = errors[0]
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise RehearsalV2ValidationError(
            f"bundle fails registered JSON schema at {location}: {error.message}"
        )


def _module_name(relative: str) -> tuple[str, str]:
    path = PurePosixPath(relative)
    if path.parts[0] == "scripts":
        components = list(path.with_suffix("").parts)
    elif path.parts[:2] == ("src", "alphapilot"):
        components = list(path.with_suffix("").parts[1:])
    else:
        raise RehearsalV2ValidationError(
            f"archived Python source is outside local namespaces: {relative}"
        )
    is_package = components[-1] == "__init__"
    if is_package:
        components.pop()
    module = ".".join(components)
    return module, module if is_package else module.rpartition(".")[0]


def _module_candidates(module: str) -> tuple[str, str] | tuple[()]:
    if module == "scripts" or module == "alphapilot":
        stem = "scripts" if module == "scripts" else "src/alphapilot"
        return (f"{stem}/__init__.py", f"{stem}.py")
    if module.startswith("scripts."):
        stem = "scripts/" + module.removeprefix("scripts.").replace(".", "/")
    elif module.startswith("alphapilot."):
        stem = "src/alphapilot/" + module.removeprefix("alphapilot.").replace(".", "/")
    else:
        return ()
    return (f"{stem}.py", f"{stem}/__init__.py")


def _resolve_commit_module(root: Path, commit: str, module: str) -> str | None:
    candidates: list[str] = []
    for candidate in _module_candidates(module):
        kind = _git(root, "cat-file", "-t", f"{commit}:{candidate}")
        if kind.returncode != 0:
            continue
        if kind.stdout.strip() != b"blob":
            raise RehearsalV2ValidationError(
                f"implementation commit local module is not a blob: {candidate}"
            )
        candidates.append(candidate)
    if len(candidates) > 1:
        raise RehearsalV2ValidationError(f"ambiguous commit-bound local module: {module}")
    return candidates[0] if candidates else None


def _resolve_import_from(package: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    components = package.split(".") if package else []
    remove = node.level - 1
    if remove > len(components):
        raise RehearsalV2ValidationError("relative local import escapes its package")
    prefix = components[: len(components) - remove]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def _commit_ancestor_initializers(root: Path, commit: str, relative: str) -> set[str]:
    path = PurePosixPath(relative)
    minimum = 1 if path.parts[0] == "scripts" else 2
    result: set[str] = set()
    for length in range(minimum, len(path.parent.parts) + 1):
        candidate = PurePosixPath(*path.parent.parts[:length], "__init__.py").as_posix()
        kind = _git(root, "cat-file", "-t", f"{commit}:{candidate}")
        if kind.returncode != 0:
            continue
        if kind.stdout.strip() != b"blob":
            raise RehearsalV2ValidationError(
                f"implementation commit package initializer is not a blob: {candidate}"
            )
        result.add(candidate)
    return result


def _ast_local_import_closure(
    blobs: Mapping[str, bytes], *, project_root: Path, implementation_commit: str
) -> set[str]:
    entrypoint = "scripts/rehearse_p4_2a_v2_heldout_full_path.py"
    if entrypoint not in blobs:
        raise RehearsalV2ValidationError("archived AST entrypoint is missing")
    pending = [entrypoint]
    closure: set[str] = set()
    while pending:
        relative = pending.pop(0)
        if relative in closure:
            continue
        payload = blobs.get(relative)
        if payload is None:
            raise RehearsalV2ValidationError(f"archived local module is missing: {relative}")
        try:
            tree = ast.parse(payload, filename=relative)
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise RehearsalV2ValidationError(
                f"archived local Python source cannot be parsed: {relative}"
            ) from exc
        closure.add(relative)
        _module, package = _module_name(relative)
        discovered: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    if module not in {"scripts", "alphapilot"} and not module.startswith(
                        ("scripts.", "alphapilot.")
                    ):
                        continue
                    target = _resolve_commit_module(project_root, implementation_commit, module)
                    if target is None:
                        raise RehearsalV2ValidationError(
                            f"unresolved direct local import is forbidden: {relative}: {module}"
                        )
                    discovered.add(target)
                    discovered.update(
                        _commit_ancestor_initializers(project_root, implementation_commit, target)
                    )
            elif isinstance(node, ast.ImportFrom):
                base = _resolve_import_from(package, node)
                if base in {"scripts", "alphapilot"} or base.startswith(
                    ("scripts.", "alphapilot.")
                ):
                    base_target = _resolve_commit_module(project_root, implementation_commit, base)
                    if base_target is None and base != "scripts":
                        raise RehearsalV2ValidationError(
                            f"unresolved local from-import base is forbidden: {relative}: {base}"
                        )
                    if base_target is not None:
                        discovered.add(base_target)
                        discovered.update(
                            _commit_ancestor_initializers(
                                project_root, implementation_commit, base_target
                            )
                        )
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        module = f"{base}.{alias.name}" if base else alias.name
                        target = _resolve_commit_module(project_root, implementation_commit, module)
                        # A from-import alias may be an attribute exported by the
                        # already-resolved base module.  Only aliases that resolve
                        # as local modules add closure members.
                        if target is None:
                            continue
                        discovered.add(target)
                        discovered.update(
                            _commit_ancestor_initializers(
                                project_root, implementation_commit, target
                            )
                        )
            else:
                if isinstance(node, ast.Call):
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
                        raise RehearsalV2ValidationError(
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
                        raise RehearsalV2ValidationError(
                            f"dynamic local import is forbidden: {relative}"
                        )
                continue
        pending.extend(sorted(discovered - closure, key=lambda item: item.encode("utf-8")))
    if "scripts/p4_2a_v2_dev_common.py" not in closure:
        raise RehearsalV2ValidationError("p4_2a_v2_dev_common is absent from AST closure")
    return closure


def _normalize_package_name(value: str) -> str:
    return _PEP503_RE.sub("-", value).lower()


def _normalize_distribution_rows(
    distributions: Sequence[tuple[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    names: list[str] = []
    for raw_name, version in distributions:
        if not raw_name or not version:
            raise RehearsalV2ValidationError(
                "package inventory contains an unnamed or unversioned distribution"
            )
        name = _normalize_package_name(raw_name)
        names.append(name)
        rows.append({"name": name, "version": version})
    if len(names) != len(set(names)):
        raise RehearsalV2ValidationError("package inventory has duplicate normalized names")
    rows.sort(key=lambda row: (row["name"], row["version"]))
    return rows


def _require_duplicate_package_negative_probe() -> None:
    try:
        _normalize_distribution_rows([("validator_probe.pkg", "1"), ("validator-probe-pkg", "2")])
    except RehearsalV2ValidationError as exc:
        if "duplicate normalized names" not in str(exc):
            raise RehearsalV2ValidationError(
                "package duplicate negative probe failed for the wrong reason"
            ) from exc
        return
    raise RehearsalV2ValidationError("package duplicate metadata negative probe was accepted")


def _require_recomputed_read_evidence(
    declared: Mapping[str, str], observed: Mapping[str, str], label: str
) -> None:
    if dict(declared) != dict(observed):
        missing = sorted(set(observed) - set(declared))
        unexpected = sorted(set(declared) - set(observed))
        changed = sorted(
            relative
            for relative in set(declared) & set(observed)
            if declared[relative] != observed[relative]
        )
        raise RehearsalV2ValidationError(
            f"{label} declared repository reads differ from independent open tracing: "
            f"missing={missing[:1]}, unexpected={unexpected[:1]}, changed={changed[:1]}"
        )


def _require_exact_repository_control_set(
    repository_paths: set[str],
    *,
    closure: set[str],
    replay_reads: Mapping[str, str],
) -> None:
    expected = set(REQUIRED_SEED_PATHS) | closure | set(replay_reads)
    if repository_paths != expected:
        missing = sorted(expected - repository_paths)
        unexpected = sorted(repository_paths - expected)
        raise RehearsalV2ValidationError(
            f"control exact set drifted: missing={missing[:1]}, unexpected={unexpected[:1]}"
        )


def _expected_repository_source_kind(relative: str) -> str:
    if relative.endswith("/__init__.py"):
        return "package_initializer"
    if relative.endswith(".py"):
        return "python_source"
    if relative == "pyproject.toml":
        return "project_manifest"
    if relative == "uv.lock":
        return "lockfile"
    return "frozen_control"


def _validate_locked_runtime_environment() -> None:
    drifted = [
        name
        for name, expected in LOCKED_EXECUTION_ENVIRONMENT.items()
        if os.environ.get(name) != expected
    ]
    if drifted:
        raise RehearsalV2ValidationError(f"locked execution environment drifted: {drifted[0]}")
    if sys.flags.hash_randomization != 0:
        raise RehearsalV2ValidationError(
            "PYTHONHASHSEED=0 was not applied before interpreter startup"
        )
    try:
        active_locale = locale.setlocale(locale.LC_ALL, "")
        time.tzset()
    except (locale.Error, OSError) as exc:
        raise RehearsalV2ValidationError("locked locale or timezone could not be applied") from exc
    if active_locale != "C.UTF-8":
        raise RehearsalV2ValidationError("active locale is not C.UTF-8")
    if time.tzname != ("UTC", "UTC") or time.timezone != 0:
        raise RehearsalV2ValidationError("active timezone is not UTC")


def _runtime_inventory(project_root: Path) -> tuple[bytes, bytes, list[str], int]:
    python_payload = _canonical_json_bytes(
        {
            "abi_flags": sys.abiflags,
            "cache_tag": sys.implementation.cache_tag,
            "implementation": (
                "CPython" if sys.implementation.name == "cpython" else sys.implementation.name
            ),
            "version": ".".join(str(part) for part in sys.version_info[:3]),
        }
    )
    selected: list[Path] = []
    for key in ("purelib", "platlib"):
        raw = sysconfig.get_path(key)
        if not isinstance(raw, str) or not raw:
            raise RehearsalV2ValidationError(f"sysconfig path is unavailable: {key}")
        path = Path(raw).resolve()
        if path not in selected:
            selected.append(path)
    projected: list[str] = []
    for path in selected:
        if not path.is_relative_to(project_root):
            raise RehearsalV2ValidationError("package metadata root escapes project_root")
        projected.append(path.relative_to(project_root).as_posix())
    distributions = list(importlib.metadata.distributions(path=[str(path) for path in selected]))
    raw_rows: list[tuple[str, str]] = []
    for distribution in distributions:
        raw_name = distribution.metadata["Name"]
        if not isinstance(raw_name, str) or not raw_name:
            raise RehearsalV2ValidationError("package inventory contains an unnamed distribution")
        raw_rows.append((raw_name, distribution.version))
    rows = _normalize_distribution_rows(raw_rows)
    return python_payload, _canonical_json_bytes(rows), projected, len(distributions)


def _validate_package_rows(payload: bytes) -> list[JsonObject]:
    value = strict_json_loads(payload, source="archived package inventory")
    rows = _array(value, "archived package inventory")
    normalized: list[JsonObject] = []
    names: list[str] = []
    for index, raw in enumerate(rows, 1):
        row = _object(raw, f"archived package row {index}")
        if set(row) != {"name", "version"}:
            raise RehearsalV2ValidationError(f"archived package row {index} has unexpected fields")
        name = row.get("name")
        version = row.get("version")
        if (
            not isinstance(name, str)
            or not name
            or name != _normalize_package_name(name)
            or not isinstance(version, str)
            or not version
        ):
            raise RehearsalV2ValidationError(f"archived package row {index} is invalid")
        names.append(name)
        normalized.append(row)
    if len(names) != len(set(names)):
        raise RehearsalV2ValidationError(
            "archived package inventory has duplicate normalized names"
        )
    if normalized != sorted(normalized, key=lambda row: (row["name"], row["version"])):
        raise RehearsalV2ValidationError("archived package inventory is not sorted")
    if payload != _canonical_json_bytes(normalized):
        raise RehearsalV2ValidationError("archived package inventory is not canonical")
    return normalized


def _lexical_absolute_path(raw: object) -> Path | None:
    if isinstance(raw, int) or not isinstance(raw, (str, bytes, os.PathLike)):
        return None
    try:
        return Path(os.path.abspath(Path(os.fsdecode(raw))))
    except (OSError, TypeError, ValueError):
        return None


@contextmanager
def _semantic_replay_guards(
    *,
    project_root: Path,
    replay_root: Path,
    tracked_control_paths: set[str],
    repository_read_hashes: dict[str, str] | None = None,
) -> Iterator[set[str]]:
    """Confine effects and independently record replay control reads."""

    root = project_root.resolve()
    reconstructed = replay_root.resolve()
    reads: set[str] = set()
    allowed_descriptors: dict[int, tuple[int, int, int]] = {}
    original_builtin_open: Any = builtins.open
    original_io_open: Any = io.open
    original_os_open: Any = os.open
    original_os_close: Any = os.close
    original_os_dup: Any = os.dup
    original_os_dup2: Any = os.dup2
    original_os_fstat: Any = os.fstat
    original_fcntl: Any = fcntl.fcntl
    original_flock: Any = fcntl.flock
    original_sqlite_connect: Any = sqlite3.connect

    def descriptor_identity(descriptor: int) -> tuple[int, int, int]:
        metadata = original_os_fstat(descriptor)
        return metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode)

    def require_allowed_descriptor(descriptor: int) -> None:
        expected = allowed_descriptors.get(descriptor)
        try:
            observed = descriptor_identity(descriptor)
        except OSError as exc:
            raise RehearsalV2ValidationError(
                "semantic replay attempted to use a closed or unregistered descriptor"
            ) from exc
        if expected is None or observed != expected:
            raise RehearsalV2ValidationError(
                "semantic replay attempted to use a preopened external descriptor"
            )

    def require_allowed_write(raw: object, *, dir_fd: object = None) -> None:
        if dir_fd is not None:
            raise RehearsalV2ValidationError(
                "semantic replay attempted a dir-fd filesystem mutation"
            )
        lexical = _lexical_absolute_path(raw)
        if lexical is None or not (
            lexical == reconstructed or lexical.is_relative_to(reconstructed)
        ):
            raise RehearsalV2ValidationError(
                "semantic replay attempted a write outside its reconstructed root"
            )
        existing = lexical
        while not existing.exists() and not existing.is_symlink():
            if existing == existing.parent:
                raise RehearsalV2ValidationError(
                    "semantic replay write target has no existing parent"
                )
            existing = existing.parent
        try:
            resolved_existing = existing.resolve(strict=True)
        except OSError as exc:
            raise RehearsalV2ValidationError(
                "semantic replay write target parent is unavailable"
            ) from exc
        if not (
            resolved_existing == reconstructed or resolved_existing.is_relative_to(reconstructed)
        ):
            raise RehearsalV2ValidationError(
                "semantic replay write target escapes through a symlink"
            )

    def record_read(raw: object) -> None:
        lexical = _lexical_absolute_path(raw)
        if lexical is None:
            return
        if lexical.is_relative_to(root):
            try:
                resolved = lexical.resolve(strict=True)
            except OSError:
                return
            if resolved != lexical or not resolved.is_relative_to(root):
                raise RehearsalV2ValidationError(
                    "semantic replay repository read escapes through a symlink"
                )
            if not resolved.is_file():
                return
            metadata = resolved.lstat()
            if metadata.st_nlink != 1:
                raise RehearsalV2ValidationError("semantic replay repository read is hard-linked")
            relative = resolved.relative_to(root).as_posix()
            with original_builtin_open(resolved, "rb") as handle:
                digest = _sha256(handle.read())
            if repository_read_hashes is not None:
                prior = repository_read_hashes.setdefault(relative, digest)
                if prior != digest:
                    raise RehearsalV2ValidationError(
                        f"repository file changed between replay reads: {relative}"
                    )
            return
        if lexical.is_relative_to(reconstructed):
            try:
                resolved = lexical.resolve(strict=True)
            except OSError:
                return
            if resolved != lexical or not resolved.is_relative_to(reconstructed):
                raise RehearsalV2ValidationError(
                    "semantic replay reconstructed read escapes through a symlink"
                )
            relative = resolved.relative_to(reconstructed).as_posix()
            if relative in tracked_control_paths:
                reads.add(relative)

    def mode_writes(mode: object) -> bool:
        return isinstance(mode, str) and any(flag in mode for flag in ("w", "a", "x", "+"))

    def checked_write_opener_kwargs(kwargs: Mapping[str, Any]) -> dict[str, Any]:
        checked = dict(kwargs)
        opener = checked.get("opener")
        if opener is None:
            return checked
        if not callable(opener):
            raise RehearsalV2ValidationError("semantic replay custom write opener is invalid")

        def checked_opener(*args: Any) -> int:
            descriptor = opener(*args)
            if not isinstance(descriptor, int):
                raise RehearsalV2ValidationError(
                    "semantic replay custom write opener returned a non-descriptor"
                )
            require_allowed_descriptor(descriptor)
            return descriptor

        checked["opener"] = checked_opener
        return checked

    def traced_builtin_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if isinstance(file, int):
            require_allowed_descriptor(file)
            return original_builtin_open(file, mode, *args, **kwargs)
        if mode_writes(mode):
            require_allowed_write(file)
            kwargs = checked_write_opener_kwargs(kwargs)
        else:
            if kwargs.get("opener") is not None:
                raise RehearsalV2ValidationError("semantic replay attempted a custom read opener")
            record_read(file)
        return original_builtin_open(file, mode, *args, **kwargs)

    def traced_io_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if isinstance(file, int):
            require_allowed_descriptor(file)
            return original_io_open(file, mode, *args, **kwargs)
        if mode_writes(mode):
            require_allowed_write(file)
            kwargs = checked_write_opener_kwargs(kwargs)
        else:
            if kwargs.get("opener") is not None:
                raise RehearsalV2ValidationError("semantic replay attempted a custom read opener")
            record_read(file)
        return original_io_open(file, mode, *args, **kwargs)

    def traced_os_open(path_value: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if kwargs.get("dir_fd") is not None:
            raise RehearsalV2ValidationError(
                "semantic replay attempted a dir-fd filesystem read or write"
            )
        writing = bool(
            flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND | os.O_EXCL)
        )
        if writing:
            require_allowed_write(path_value)
        else:
            record_read(path_value)
        descriptor = int(original_os_open(path_value, flags, *args, **kwargs))
        try:
            allowed_descriptors[descriptor] = descriptor_identity(descriptor)
        except OSError:
            original_os_close(descriptor)
            raise
        return descriptor

    def traced_os_close(descriptor: int) -> None:
        allowed_descriptors.pop(descriptor, None)
        original_os_close(descriptor)

    def traced_os_dup(descriptor: int) -> int:
        require_allowed_descriptor(descriptor)
        duplicate = int(original_os_dup(descriptor))
        try:
            allowed_descriptors[duplicate] = descriptor_identity(duplicate)
        except OSError:
            original_os_close(duplicate)
            raise
        return duplicate

    def traced_os_dup2(
        descriptor: int,
        target_descriptor: int,
        inheritable: bool = True,
    ) -> int:
        require_allowed_descriptor(descriptor)
        duplicate = int(original_os_dup2(descriptor, target_descriptor, inheritable=inheritable))
        allowed_descriptors[duplicate] = descriptor_identity(duplicate)
        return duplicate

    def traced_fcntl(descriptor: int, command: int, *args: Any) -> Any:
        require_allowed_descriptor(descriptor)
        duplicate_commands = {fcntl.F_DUPFD}
        if hasattr(fcntl, "F_DUPFD_CLOEXEC"):
            duplicate_commands.add(fcntl.F_DUPFD_CLOEXEC)
        if command in duplicate_commands:
            raise RehearsalV2ValidationError("semantic replay forbids fcntl descriptor duplication")
        return original_fcntl(descriptor, command, *args)

    def traced_flock(descriptor: int, operation: int) -> Any:
        require_allowed_descriptor(descriptor)
        return original_flock(descriptor, operation)

    def forbidden_network(*_args: object, **_kwargs: object) -> NoReturn:
        raise RehearsalV2ValidationError("semantic replay attempted network access")

    def forbidden_subprocess(*_args: object, **_kwargs: object) -> NoReturn:
        raise RehearsalV2ValidationError("semantic replay attempted to start a subprocess")

    def guarded_sqlite(database: object, *args: Any, **kwargs: Any) -> Any:
        if database != ":memory:":
            raise RehearsalV2ValidationError("semantic replay attempted a non-memory SQLite open")
        return original_sqlite_connect(database, *args, **kwargs)

    def guard_one_path(original: Callable[..., Any]) -> Callable[..., Any]:
        def guarded(path_value: Any, *args: Any, **kwargs: Any) -> Any:
            require_allowed_write(path_value, dir_fd=kwargs.get("dir_fd"))
            return original(path_value, *args, **kwargs)

        return guarded

    def guard_two_paths(original: Callable[..., Any]) -> Callable[..., Any]:
        def guarded(source: Any, destination: Any, *args: Any, **kwargs: Any) -> Any:
            require_allowed_write(source, dir_fd=kwargs.get("src_dir_fd"))
            require_allowed_write(destination, dir_fd=kwargs.get("dst_dir_fd"))
            return original(source, destination, *args, **kwargs)

        return guarded

    with ExitStack() as stack:
        stack.enter_context(patch.object(builtins, "open", traced_builtin_open))
        stack.enter_context(patch.object(io, "open", traced_io_open))
        stack.enter_context(patch.object(os, "open", traced_os_open))
        stack.enter_context(patch.object(os, "close", traced_os_close))
        stack.enter_context(patch.object(os, "dup", traced_os_dup))
        stack.enter_context(patch.object(os, "dup2", traced_os_dup2))
        stack.enter_context(patch.object(fcntl, "fcntl", traced_fcntl))
        stack.enter_context(patch.object(fcntl, "flock", traced_flock))
        socket_type = socket.socket
        for owner, name in (
            (socket_type, "accept"),
            (socket_type, "bind"),
            (socket_type, "connect"),
            (socket_type, "connect_ex"),
            (socket_type, "listen"),
            (socket_type, "recv"),
            (socket_type, "recvfrom"),
            (socket_type, "send"),
            (socket_type, "sendall"),
            (socket_type, "sendto"),
            (socket, "create_connection"),
            (socket, "fromfd"),
            (socket, "getaddrinfo"),
            (socket, "gethostbyname"),
            (socket, "gethostbyname_ex"),
            (socket, "gethostbyaddr"),
            (socket, "getnameinfo"),
            (socket, "socketpair"),
        ):
            if hasattr(owner, name):
                stack.enter_context(patch.object(owner, name, forbidden_network))
        stack.enter_context(patch.object(sqlite3, "connect", guarded_sqlite))
        stack.enter_context(patch.object(sqlite3.dbapi2, "connect", guarded_sqlite))
        stack.enter_context(patch.object(subprocess, "Popen", forbidden_subprocess))
        for name in (
            "chflags",
            "chmod",
            "chown",
            "lchflags",
            "lchmod",
            "lchown",
            "makedirs",
            "mkdir",
            "mkfifo",
            "mknod",
            "remove",
            "rmdir",
            "truncate",
            "unlink",
            "utime",
        ):
            if hasattr(os, name):
                original = getattr(os, name)
                stack.enter_context(patch.object(os, name, guard_one_path(original)))
        for name in ("rename", "replace", "link", "symlink"):
            if hasattr(os, name):
                original = getattr(os, name)
                stack.enter_context(patch.object(os, name, guard_two_paths(original)))
        yield reads


def _replay_full_synthetic_path(
    *,
    project_root: Path,
    expected_runs: Sequence[Mapping[str, bytes]],
    repository_payloads: Mapping[str, bytes],
) -> tuple[dict[str, str], dict[str, str]]:
    """Regenerate both runs and independently reproduce their repository-read evidence."""

    from scripts import rehearse_p4_2a_v2_heldout_full_path as runner

    preflight = runner.SuccessorPreflight(
        repository_payloads=dict(repository_payloads),
        repository_sha256={
            relative: _sha256(payload) for relative, payload in repository_payloads.items()
        },
        ast_closure_paths=tuple(
            sorted(
                (relative for relative in repository_payloads if relative.endswith(".py")),
                key=lambda value: value.encode("utf-8"),
            )
        ),
        python_inventory_sha256=(
            "ab3e067417027bb98ea4335e9086d2046ac9dfd4eaf857acc8622dc8f0a13a31"
        ),
        package_inventory_sha256=(
            "c3c7792eb31679c0eb7d3140e067d691df330cd3af302d2350bf15b74ac8ec42"
        ),
        publication_device=0,
    )
    with tempfile.TemporaryDirectory(prefix="alphapilot-p4-2a-validator-replay-") as raw:
        parent = Path(raw).resolve()
        run_roots = (parent / "run-a", parent / "run-b")
        for run_root in run_roots:
            run_root.mkdir()
        independent_a: dict[str, str] = {}
        with _semantic_replay_guards(
            project_root=project_root,
            replay_root=run_roots[0],
            tracked_control_paths=set(),
            repository_read_hashes=independent_a,
        ):
            replay_a = runner._execute_successor_run(
                label="run-a",
                root=project_root,
                workspace=run_roots[0],
                preflight=preflight,
            )
        independent_b: dict[str, str] = {}
        with _semantic_replay_guards(
            project_root=project_root,
            replay_root=run_roots[1],
            tracked_control_paths=set(),
            repository_read_hashes=independent_b,
        ):
            replay_b = runner._execute_successor_run(
                label="run-b",
                root=project_root,
                workspace=run_roots[1],
                preflight=preflight,
            )
    for index, replay in enumerate((replay_a, replay_b)):
        replay_by_path = {
            source_relative: replay.artifacts[logical_name]
            for logical_name, source_relative in runner.SUCCESSOR_ARTIFACT_INVENTORY
        }
        if replay_by_path != dict(expected_runs[index]):
            raise RehearsalV2ValidationError(
                f"independent synthetic replay {index + 1} differs from archived bytes"
            )
    _require_recomputed_read_evidence(replay_a.repository_reads, independent_a, "run-a")
    _require_recomputed_read_evidence(replay_b.repository_reads, independent_b, "run-b")
    if independent_a != independent_b:
        raise RehearsalV2ValidationError(
            "independent synthetic replays observed different repository read sets"
        )
    return independent_a, independent_b


def _validate_semantics(
    *,
    run_payloads: Mapping[str, bytes],
    control_payloads_by_repository_path: Mapping[str, bytes],
    project_root: Path,
) -> set[str]:
    """Run the registered semantic validators against one reconstructed archive."""

    from scripts import evaluate_p4_2a_v2_heldout as evaluator

    with tempfile.TemporaryDirectory(prefix="alphapilot-p4-2a-v2-validator-") as raw:
        root = Path(raw).resolve()
        for relative, payload in control_payloads_by_repository_path.items():
            target = root.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        # Preflight and dry_run require evaluation state/report to be absent.
        for relative, payload in run_payloads.items():
            if relative.endswith("P4.2a-heldout-v2-evaluation.state.jsonl") or relative.endswith(
                "P4.2a-heldout-v2-evaluation-result.json"
            ):
                continue
            target = root.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        heldout_root = root / "docs/phase4/eval/v2-calibration/heldout"
        paths = evaluator.ArtifactPaths(
            artifact_root=root,
            materialized_inputs=root
            / "docs/phase4/eval/v2-calibration/heldout/materialization/candidate-inputs.jsonl",
            materialization_manifest=root
            / "docs/phase4/eval/v2-calibration/heldout/materialization/manifest.json",
            inference_state=heldout_root / "P4.2a-heldout-v2-inference.state.jsonl",
            predictions=heldout_root / "P4.2a-heldout-v2.predictions.jsonl",
            prediction_manifest=heldout_root / "P4.2a-heldout-v2.predictions.manifest.json",
            selection=heldout_root / "P4.2a-heldout-frame-v2.selection.json",
            blind=heldout_root / "P4.2a-heldout-frame-v2.blind.jsonl",
            draft=heldout_root / "P4.2a-heldout-frame-v2.labels-ai-drafted.jsonl",
            adjudication_ui=heldout_root / "P4.2a-heldout-frame-v2.adjudication.html",
            owner_export=heldout_root / "P4.2a-heldout-frame-v2.owner-export.jsonl",
            human_adjudicated=heldout_root / "P4.2a-heldout-frame-v2.human-adjudicated.jsonl",
            owner_completion=heldout_root / "P4.2a-heldout-frame-v2.owner-completion.json",
            evaluation_state=heldout_root / "P4.2a-heldout-v2-evaluation.state.jsonl",
            report=heldout_root / "report/P4.2a-heldout-v2-evaluation-result.json",
        )
        try:
            with _semantic_replay_guards(
                project_root=project_root,
                replay_root=root,
                tracked_control_paths=set(control_payloads_by_repository_path),
            ) as replay_reads:
                dry = evaluator.dry_run(
                    root=root,
                    paths=paths,
                    clock=lambda: datetime(2026, 8, 10, 8, 15, tzinfo=UTC),
                )
                preflight = evaluator.load_preflight(root=root, paths=paths)
                if dry.get("status") != "passed" or dry.get("filesystem_mutations") != 0:
                    raise RehearsalV2ValidationError("archived run dry-run semantics failed")
                synthetic_human, synthetic_predictions = evaluator._synthetic_score_inputs(
                    preflight
                )
                metrics = evaluator.score_heldout(
                    preflight.selected, synthetic_predictions, synthetic_human
                )
                report = evaluator._report_payload(
                    preflight,
                    metrics,
                    completed_at="2026-08-10T08:15:00Z",
                    authorization=None,
                    synthetic=True,
                )
                report_payload = evaluator._canonical_json_bytes(report)
                archived_report = run_payloads[
                    "docs/phase4/eval/v2-calibration/heldout/report/"
                    "P4.2a-heldout-v2-evaluation-result.json"
                ]
                if archived_report != report_payload:
                    raise RehearsalV2ValidationError(
                        "archived synthetic report differs from registered metric assembly"
                    )
                started = {
                    "schema_version": "p4.2a-v2-heldout-evaluation-state-event-v1",
                    "event": "evaluation_started",
                    "at_utc": "2026-08-10T08:15:00Z",
                    "synthetic_rehearsal": True,
                    "design_sha256": (
                        "18a2428a4ec04bfea6e4f4d70692f38ea82fbaee5a223f30f2465b895b238e21"
                    ),
                    "preregistration_sha256": (
                        "ccecbf5ca7b48b16e445318b8c94a08927432f92c7e8c12f8ab40f2916578705"
                    ),
                    "selected_model": "qwen3.6-plus",
                    "input_hashes": dict(preflight.hashes),
                    "attempt_number": 0,
                    "maximum_real_attempts_consumed": 0,
                    "retries": 0,
                }
                terminal = {
                    "schema_version": "p4.2a-v2-heldout-evaluation-state-event-v1",
                    "event": "evaluation_completed",
                    "at_utc": "2026-08-10T08:15:00Z",
                    "synthetic_rehearsal": True,
                    "real_heldout_metrics_computed": False,
                    "one_shot_consumed": False,
                    "report_path": (
                        "docs/phase4/eval/v2-calibration/heldout/report/"
                        "P4.2a-heldout-v2-evaluation-result.json"
                    ),
                    "report_sha256": _sha256(report_payload),
                    "retries": 0,
                }
                expected_state = _canonical_json_bytes(started) + _canonical_json_bytes(terminal)
                archived_state = run_payloads[
                    "docs/phase4/eval/v2-calibration/heldout/"
                    "P4.2a-heldout-v2-evaluation.state.jsonl"
                ]
                if archived_state != expected_state:
                    raise RehearsalV2ValidationError(
                        "archived evaluation state differs from registered synthetic state machine"
                    )
        except Exception as exc:
            if isinstance(exc, RehearsalV2ValidationError):
                raise
            raise RehearsalV2ValidationError(
                "archived run failed registered materialization/inference/selection/"
                "owner validators"
            ) from exc
        return set(replay_reads)


def validate_rehearsal_bundle(
    bundle_directory: Path,
    *,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Independently rehash and semantically validate a rehearsal v2 bundle."""

    root_literal = project_root.absolute()
    input_literal = bundle_directory.absolute()
    registered_literal = (project_root / REGISTERED_V2_REHEARSAL_DIRECTORY).absolute()
    if input_literal == registered_literal:
        directory = registered_rehearsal_directory(project_root)
    else:
        try:
            directory = input_literal.resolve(strict=False)
            registered_resolved = registered_literal.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise RehearsalV2ValidationError("bundle_directory cannot be resolved safely") from exc
        if directory == registered_resolved:
            raise RehearsalV2ValidationError(
                "registered rehearsal directory must use its exact literal path"
            )
    root = root_literal.resolve()
    _validate_locked_runtime_environment()
    if directory == (root / V1_REHEARSAL_DIRECTORY).resolve():
        raise RehearsalV2ValidationError("retired rehearsal v1 can never satisfy the v2 gate")
    if bundle_directory.is_symlink() or not bundle_directory.is_dir():
        raise RehearsalV2ValidationError("bundle_directory is not one regular directory")

    prereg_payload = _bound_repository_file(
        root, PREREGISTRATION_PATH.as_posix(), PREREGISTRATION_SHA256, "preregistration"
    )
    schema_payload = _bound_repository_file(
        root, BUNDLE_SCHEMA_PATH.as_posix(), BUNDLE_SCHEMA_SHA256, "bundle schema"
    )
    prereg = _object(
        strict_json_loads(prereg_payload, source="registered preregistration"),
        "registered preregistration",
    )
    if (
        prereg.get("schema_version") != "p4.2a-v2-heldout-rehearsal-v2-preregistration-v1"
        or prereg.get("status") != "PREREGISTERED_NOT_EXECUTED"
    ):
        raise RehearsalV2ValidationError("registered preregistration semantics drifted")
    schema = _object(
        strict_json_loads(schema_payload, source="registered bundle schema"),
        "registered bundle schema",
    )
    bundle_path = directory / BUNDLE_FILENAME
    bundle_payload = _regular_bytes(bundle_path, "bundle manifest")
    bundle = _object(strict_json_loads(bundle_payload, source="bundle manifest"), "bundle")
    _schema_validate(bundle, schema)

    lineage = _object(bundle.get("lineage"), "bundle.lineage")
    if _object(lineage.get("preregistration"), "lineage.preregistration") != {
        "path": PREREGISTRATION_PATH.as_posix(),
        "sha256": PREREGISTRATION_SHA256,
    }:
        raise RehearsalV2ValidationError("bundle preregistration lineage drifted")
    if _object(lineage.get("bundle_schema"), "lineage.bundle_schema") != {
        "path": BUNDLE_SCHEMA_PATH.as_posix(),
        "sha256": BUNDLE_SCHEMA_SHA256,
    }:
        raise RehearsalV2ValidationError("bundle schema lineage drifted")
    for name in (
        "parent_preregistration",
        "v1_incident",
        "design",
        "heldout_contract",
        "round3_prompt",
        "round3_plus_contract",
    ):
        reference = _object(lineage.get(name), f"lineage.{name}")
        _bound_repository_file(
            root, reference.get("path"), reference.get("sha256"), f"lineage.{name}"
        )
    retired = _array(lineage.get("retired_v1_artifacts"), "retired_v1_artifacts")
    expected_retired = [{"path": path, "sha256": digest} for path, digest in RETIRED_V1]
    if retired != expected_retired:
        raise RehearsalV2ValidationError("retired v1 artifact registry drifted")
    for path, digest in RETIRED_V1:
        _bound_repository_file(root, path, digest, f"retired v1 artifact {path}")
    implementation_commit = _validate_commit(root, lineage.get("implementation_commit"))

    archive = _object(bundle.get("archive"), "bundle.archive")
    run_records = _array(archive.get("runs"), "bundle.archive.runs")
    control = _object(archive.get("control_surface"), "bundle.archive.control_surface")
    expected_files = {BUNDLE_FILENAME}
    run_payload_sets: list[dict[str, bytes]] = []
    run_roots: list[str] = []
    for run_index, raw_run in enumerate(run_records):
        run = _object(raw_run, f"run {run_index}")
        archive_root = _relative_posix(run.get("archive_root"), "run.archive_root")
        artifacts = _array(run.get("artifacts"), "run.artifacts")
        payloads: dict[str, bytes] = {}
        for record_index, raw_record in enumerate(artifacts):
            record = _object(raw_record, f"run artifact {record_index}")
            source_relative = _relative_posix(
                record.get("source_relative_path"), "artifact.source_relative_path"
            )
            physical = f"{archive_root}/{source_relative}"
            artifact_path = _safe_path(directory, physical, f"run artifact {source_relative}")
            payload = _regular_bytes(artifact_path, f"run artifact {source_relative}")
            if record.get("bytes") != len(payload) or record.get("sha256") != _sha256(payload):
                raise RehearsalV2ValidationError(
                    f"run artifact byte binding drifted: {source_relative}"
                )
            if source_relative in payloads:
                raise RehearsalV2ValidationError(f"duplicate run artifact path: {source_relative}")
            payloads[source_relative] = payload
            expected_files.add(physical)
        recomputed_root = _require_merkle_root(
            payloads, run.get("artifact_merkle_root_sha256"), "run artifact"
        )
        if recomputed_root != _object(bundle.get("merkle"), "bundle.merkle").get(
            "run_a_root_sha256" if run_index == 0 else "run_b_root_sha256"
        ):
            raise RehearsalV2ValidationError("run artifact Merkle root drifted")
        run_payload_sets.append(payloads)
        run_roots.append(recomputed_root)
    if run_payload_sets[0] != run_payload_sets[1]:
        raise RehearsalV2ValidationError("the two runs are not byte-identical for all artifacts")

    control_records = _array(control.get("files"), "control_surface.files")
    control_payloads: dict[str, bytes] = {}
    repo_payloads: dict[str, bytes] = {}
    python_kind_paths: set[str] = set()
    logical_names: list[str] = []
    record_paths: list[str] = []
    repository_paths: set[str] = set()
    prior_path: bytes | None = None
    for index, raw_record in enumerate(control_records):
        record = _object(raw_record, f"control record {index}")
        logical_name = record.get("logical_name")
        if not isinstance(logical_name, str) or not logical_name:
            raise RehearsalV2ValidationError("control logical_name is invalid")
        relative = _relative_posix(record.get("bundle_relative_path"), "control path")
        encoded_path = relative.encode("utf-8")
        if prior_path is not None and encoded_path <= prior_path:
            raise RehearsalV2ValidationError("control records are not strictly path-sorted")
        prior_path = encoded_path
        source_kind = record.get("source_kind")
        repository_path = record.get("repository_path")
        control_path = _safe_path(directory, relative, f"control file {logical_name}")
        payload = _regular_bytes(control_path, f"control file {logical_name}")
        if record.get("bytes") != len(payload) or record.get("sha256") != _sha256(payload):
            raise RehearsalV2ValidationError(f"control byte binding drifted: {logical_name}")
        if source_kind in REPOSITORY_SOURCE_KINDS:
            repo_relative = _relative_posix(repository_path, "control.repository_path")
            expected_relative = f"archive/control-surface/root/repo/{repo_relative}"
            if relative != expected_relative or logical_name != repo_relative:
                raise RehearsalV2ValidationError(
                    f"repository control path rule drifted: {logical_name}"
                )
            _require_commit_blob_equality(root, implementation_commit, repo_relative, payload)
            current_path = root.joinpath(*PurePosixPath(repo_relative).parts)
            if (
                _regular_bytes(current_path, f"current repository control {repo_relative}")
                != payload
            ):
                raise RehearsalV2ValidationError(
                    f"archived control differs from current registered bytes: {repo_relative}"
                )
            repo_payloads[repo_relative] = payload
            repository_paths.add(repo_relative)
            expected_kind = _expected_repository_source_kind(repo_relative)
            if source_kind != expected_kind:
                raise RehearsalV2ValidationError(
                    f"repository control source_kind drifted: {repo_relative}"
                )
            if expected_kind in PYTHON_SOURCE_KINDS:
                python_kind_paths.add(repo_relative)
        elif source_kind in RUNTIME_SOURCE_KINDS:
            if repository_path is not None:
                raise RehearsalV2ValidationError("runtime control has a repository_path")
            runtime_name = "python" if source_kind == "python_runtime" else "packages"
            if logical_name != runtime_name or relative != (
                f"archive/control-surface/root/runtime/{runtime_name}.json"
            ):
                raise RehearsalV2ValidationError("runtime control path rule drifted")
        else:
            raise RehearsalV2ValidationError(f"unknown control source_kind: {source_kind}")
        logical_names.append(logical_name.casefold())
        record_paths.append(relative.casefold())
        control_payloads[relative] = payload
        expected_files.add(relative)
    if len(logical_names) != len(set(logical_names)) or len(record_paths) != len(set(record_paths)):
        raise RehearsalV2ValidationError("control records duplicate or casefold-collide")
    if control.get("file_count") != len(control_records) or control.get("tree_member_count") != (
        len(control_records) + 1
    ):
        raise RehearsalV2ValidationError("control surface counts drifted")

    manifest_record = _object(control.get("manifest"), "control manifest record")
    manifest_relative = "archive/control-surface/manifest.json"
    manifest_payload = _regular_bytes(
        _safe_path(directory, manifest_relative, "control manifest"), "control manifest"
    )
    if manifest_record.get("bytes") != len(manifest_payload) or manifest_record.get(
        "sha256"
    ) != _sha256(manifest_payload):
        raise RehearsalV2ValidationError("control manifest byte binding drifted")
    expected_manifest = {"schema_version": CONTROL_MANIFEST_SCHEMA, "files": control_records}
    parsed_manifest = strict_json_loads(manifest_payload, source="control manifest")
    if parsed_manifest != expected_manifest or manifest_payload != _canonical_json_bytes(
        expected_manifest
    ):
        raise RehearsalV2ValidationError("control manifest is not the exact canonical registry")
    control_payloads[manifest_relative] = manifest_payload
    expected_files.add(manifest_relative)

    actual_files, actual_directories = _filesystem_inventory(directory)
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        unexpected = sorted(actual_files - expected_files)
        raise RehearsalV2ValidationError(
            f"bundle file inventory drifted: missing={missing[:1]}, unexpected={unexpected[:1]}"
        )
    expected_directories = _expected_directories(expected_files)
    if actual_directories != expected_directories:
        raise RehearsalV2ValidationError("bundle directory inventory contains unexpected entries")

    if not REQUIRED_SEED_PATHS.issubset(repository_paths):
        missing = sorted(REQUIRED_SEED_PATHS - repository_paths)
        raise RehearsalV2ValidationError(f"control surface omitted required seed: {missing[0]}")
    closure = _ast_local_import_closure(
        repo_payloads,
        project_root=root,
        implementation_commit=implementation_commit,
    )
    if closure != python_kind_paths:
        missing = sorted(closure - python_kind_paths)
        unexpected = sorted(python_kind_paths - closure)
        raise RehearsalV2ValidationError(
            f"archived AST closure drifted: missing={missing[:1]}, unexpected={unexpected[:1]}"
        )

    for relative, digest in FROZEN_AUTHORITY_SHA256.items():
        current = _bound_repository_file(root, relative, digest, f"frozen authority {relative}")
        archived = repo_payloads.get(relative)
        if archived is None or archived != current:
            raise RehearsalV2ValidationError(
                f"frozen authority is not cross-bound to archive and commit: {relative}"
            )

    environment = _object(bundle.get("execution_environment"), "execution_environment")
    python_payload, package_payload, projected_roots, raw_distribution_count = _runtime_inventory(
        root
    )
    python_control_path = "archive/control-surface/root/runtime/python.json"
    package_control_path = "archive/control-surface/root/runtime/packages.json"
    if python_control_path not in control_payloads or package_control_path not in control_payloads:
        raise RehearsalV2ValidationError("runtime control inventory is incomplete")
    archived_python = control_payloads[python_control_path]
    archived_packages = control_payloads[package_control_path]
    archived_package_rows = _validate_package_rows(archived_packages)
    if archived_python != python_payload or archived_packages != package_payload:
        raise RehearsalV2ValidationError("archived execution environment differs from runtime")
    python_environment = _object(environment.get("python"), "execution_environment.python")
    packages = _object(environment.get("packages"), "execution_environment.packages")
    if (
        python_environment.get("inventory_path") != python_control_path
        or python_environment.get("inventory_sha256") != _sha256(archived_python)
        or packages.get("inventory_path") != package_control_path
        or packages.get("sha256") != _sha256(archived_packages)
        or packages.get("raw_distribution_count") != raw_distribution_count
        or packages.get("count") != len(archived_package_rows)
        or raw_distribution_count != 84
        or len(archived_package_rows) != 84
        or packages.get("duplicate_normalized_name_count") != 0
    ):
        raise RehearsalV2ValidationError("runtime inventory counts or hashes drifted")
    if projected_roots != packages.get("selected_path_roots_project_relative"):
        raise RehearsalV2ValidationError("package inventory path scope drifted")
    roots_payload = _canonical_json_bytes(projected_roots)
    if _sha256(roots_payload) != packages.get("selected_path_roots_sha256"):
        raise RehearsalV2ValidationError("package root-list SHA-256 drifted")
    pyproject = _object(environment.get("pyproject"), "execution_environment.pyproject")
    uv_lock = _object(environment.get("uv_lock"), "execution_environment.uv_lock")
    for reference, expected_path in (
        (pyproject, "pyproject.toml"),
        (uv_lock, "uv.lock"),
    ):
        if reference.get("path") != expected_path or reference.get("sha256") != _sha256(
            repo_payloads[expected_path]
        ):
            raise RehearsalV2ValidationError(
                f"execution environment control drifted: {expected_path}"
            )
    _require_duplicate_package_negative_probe()

    merkle = _object(bundle.get("merkle"), "bundle.merkle")
    control_root = _require_merkle_root(
        control_payloads, control.get("merkle_root_sha256"), "control"
    )
    if control_root != merkle.get("control_surface_root_sha256"):
        raise RehearsalV2ValidationError("control Merkle root drifted")
    bundle_root = hashlib.sha256(
        b"p4.2a-rehearsal-bundle-v2\0"
        + bytes.fromhex(run_roots[0])
        + bytes.fromhex(run_roots[1])
        + bytes.fromhex(control_root)
    ).hexdigest()
    if bundle_root != merkle.get("bundle_root_sha256"):
        raise RehearsalV2ValidationError("bundle Merkle root drifted")

    forbidden_fragments = (
        b"file://",
        b"alphapilot-p4-2a-successor-run-a-",
        b"alphapilot-p4-2a-successor-run-b-",
        str(directory).encode("utf-8"),
        str(root).encode("utf-8"),
    )
    for label, payloads in (("run-a", run_payload_sets[0]), ("run-b", run_payload_sets[1])):
        for relative, payload in payloads.items():
            if any(fragment and fragment in payload for fragment in forbidden_fragments):
                raise RehearsalV2ValidationError(
                    f"temporary or absolute root leaked into {label} artifact {relative}"
                )

    # The current semantic implementation may be used only when every consumed local
    # Python byte is the exact archived, commit-bound byte.
    for relative in closure:
        worktree = root.joinpath(*PurePosixPath(relative).parts)
        if (
            _regular_bytes(worktree, f"semantic validator source {relative}")
            != repo_payloads[relative]
        ):
            raise RehearsalV2ValidationError(
                f"semantic validator source differs from archived implementation: {relative}"
            )
    replay_a_reads, replay_b_reads = _replay_full_synthetic_path(
        project_root=root,
        expected_runs=run_payload_sets,
        repository_payloads=repo_payloads,
    )
    if replay_a_reads != replay_b_reads:
        raise RehearsalV2ValidationError(
            "independent full-path replay repository evidence differs by run"
        )
    for relative, digest in replay_a_reads.items():
        replayed_payload = repo_payloads.get(relative)
        if replayed_payload is None or _sha256(replayed_payload) != digest:
            raise RehearsalV2ValidationError(
                f"recomputed repository read is absent or hash-drifted: {relative}"
            )
    _require_exact_repository_control_set(
        repository_paths,
        closure=closure,
        replay_reads=replay_a_reads,
    )

    semantic_a_reads = _validate_semantics(
        run_payloads=run_payload_sets[0],
        control_payloads_by_repository_path=repo_payloads,
        project_root=root,
    )
    semantic_b_reads = _validate_semantics(
        run_payloads=run_payload_sets[1],
        control_payloads_by_repository_path=repo_payloads,
        project_root=root,
    )
    if semantic_a_reads != semantic_b_reads:
        raise RehearsalV2ValidationError(
            "semantic replays observed different archived control reads"
        )
    if not semantic_a_reads.issubset(repository_paths):
        raise RehearsalV2ValidationError("semantic replay read an unregistered repository control")

    safety = _object(bundle.get("safety"), "bundle.safety")
    blockers = _object(bundle.get("remaining_blockers"), "bundle.remaining_blockers")
    if (
        safety.get("real_database_reads") != 0
        or safety.get("real_network_calls") != 0
        or safety.get("real_model_calls") != 0
        or safety.get("production_writes") is not False
        or safety.get("production_heldout_artifacts_changed") is not False
        or safety.get("real_heldout_metrics_computed") is not False
        or safety.get("real_metrics_disclosed") is not False
        or safety.get("proposals_or_orders_allowed") is not False
        or blockers.get("real_heldout_materialization_unlocked") is not False
        or blockers.get("real_heldout_inference_unlocked") is not False
        or blockers.get("heldout_metric_evaluation_unlocked") is not False
    ):
        raise RehearsalV2ValidationError("bundle safety or phase locks are not fail-closed")

    return {
        "status": "PASS_REHEARSAL_V2_ONLY_REAL_HELDOUT_REMAINS_BLOCKED",
        "bundle_root_sha256": bundle_root,
        "implementation_commit": implementation_commit,
        "run_artifact_count": len(run_payload_sets[0]),
        "byte_identical_artifact_count": len(run_payload_sets[0]),
        "control_file_count": len(control_records),
        "ast_local_import_closure_count": len(closure),
        "real_heldout_materialization_unlocked": False,
        "real_heldout_inference_unlocked": False,
        "heldout_metric_evaluation_unlocked": False,
        "v1_receipt_or_gate_accepted": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Independently validate the registered P4.2a rehearsal-v2 bundle; "
            "no path override or v1 fallback is accepted."
        )
    )
    return parser


def _main(argv: Sequence[str] | None = None) -> int:
    _parser().parse_args(argv)
    result = validate_rehearsal_bundle(
        registered_rehearsal_directory(PROJECT_ROOT),
        project_root=PROJECT_ROOT,
    )
    sys.stdout.buffer.write(_canonical_json_bytes(result))
    return 0


__all__ = [
    "PROJECT_ROOT",
    "RehearsalV2ValidationError",
    "registered_rehearsal_directory",
    "strict_json_loads",
    "validate_rehearsal_bundle",
]


if __name__ == "__main__":
    raise SystemExit(_main())
