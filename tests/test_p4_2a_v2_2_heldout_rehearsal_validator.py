from __future__ import annotations

import ast
import builtins
import copy
import ctypes
import gc
import hashlib
import importlib
import inspect
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import MISSING, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from jsonschema import Draft202012Validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREREGISTRATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-2-preregistration-20260811.json"
)
BUNDLE_SCHEMA_RELATIVE = Path("config/schemas/p4_2a_v2_2_heldout_rehearsal_bundle.schema.json")
RELEASE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_2_heldout_release_authorization.schema.json"
)
SERIES_2_PREREGISTRATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-series-2-preregistration-amendment-20260823.json"
)
SERIES_2_BUNDLE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_2_series_2_heldout_rehearsal_bundle.schema.json"
)
SERIES_2_RELEASE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_2_series_2_heldout_release_authorization.schema.json"
)
V2_1_BUNDLE_SCHEMA_RELATIVE = Path("config/schemas/p4_2a_v2_1_heldout_rehearsal_bundle.schema.json")
V2_1_RELEASE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_1_heldout_release_authorization.schema.json"
)
IMPLEMENTATION_RELATIVE = Path("scripts/p4_2a_v2_2_heldout_rehearsal.py")
SHIM_RELATIVE = Path("scripts/rehearse_p4_2a_v2_2_heldout_full_path.py")
VALIDATOR_RELATIVE = Path("scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py")
RUNNER_TEST_RELATIVE = Path("tests/test_p4_2a_v2_2_heldout_rehearsal_runner.py")
VALIDATOR_TEST_RELATIVE = Path("tests/test_p4_2a_v2_2_heldout_rehearsal_validator.py")
IMPLEMENTATION_MODULE = "scripts.p4_2a_v2_2_heldout_rehearsal"
VALIDATOR_MODULE = "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
PREREGISTRATION_COMMIT = "be6423506f598c290db7ad944b002763fdf806ab"
REGISTERED_SURFACE = (
    SHIM_RELATIVE,
    IMPLEMENTATION_RELATIVE,
    VALIDATOR_RELATIVE,
    RUNNER_TEST_RELATIVE,
    VALIDATOR_TEST_RELATIVE,
)
V2_1_HISTORICAL_AUTHORITY_MODULES = {
    "scripts.rehearse_p4_2a_v2_1_heldout_full_path": (
        Path("scripts/rehearse_p4_2a_v2_1_heldout_full_path.py"),
        "5fb771df602876e467e0882e9f7f1e43679203a37c401c5ebae777a7a3aae73e",
        2,
    ),
    "scripts.validate_p4_2a_v2_1_heldout_rehearsal_bundle": (
        Path("scripts/validate_p4_2a_v2_1_heldout_rehearsal_bundle.py"),
        "bb5ea8602f5b9e485e0c365136b43b79c08c200879df20028f03f0ced121413e",
        1,
    ),
}
V2_1_PIPELINE_IMPLEMENTATION_COMMIT = "4fce89e89fe2dba656694a7cffdc0ee1af0305c0"
INITIAL_SIBLING_PATH = Path(
    "docs/phase4/reports/P4.2a-v2-2-preregistration-independent-review-20260811.json"
)
INITIAL_SIBLING_SHA256 = "6707e2b3c0b2ba87712e88b59ceaed17524be2de947b764a94c8b170b2a30bb6"
INITIAL_SIBLING_COMMIT = "b21e1bdbf865dfd9c7605ecc7794fc3f8701ed1f"
INITIAL_SIBLING_PARENT = "be6423506f598c290db7ad944b002763fdf806ab"
V2_1_CONSUMED_CLAIM = Path(
    "/Users/ouyangduning/Documents/project/interesting/"
    ".alphapilot-p4-2a-v2-1-execution-claim-"
    "52378ddcda558a8489795c62a5c4d290687700801320508c03c51589c202e962"
)


def _preregistration() -> dict[str, Any]:
    document = json.loads((PROJECT_ROOT / PREREGISTRATION_RELATIVE).read_bytes())
    assert isinstance(document, dict)
    return document


def _fixture_git(
    validator: Any,
    root: Path,
    *arguments: str,
    input_bytes: bytes | None = None,
) -> bytes:
    environment = {
        **validator._GIT_ENVIRONMENT,
        "GIT_AUTHOR_NAME": "P4.2a v2.2 fixture",
        "GIT_AUTHOR_EMAIL": "p4.2a-v2.2-fixture@example.invalid",
        "GIT_AUTHOR_DATE": "2026-08-11T00:00:00+00:00",
        "GIT_COMMITTER_NAME": "P4.2a v2.2 fixture",
        "GIT_COMMITTER_EMAIL": "p4.2a-v2.2-fixture@example.invalid",
        "GIT_COMMITTER_DATE": "2026-08-11T00:00:00+00:00",
    }
    completed = subprocess.run(
        [
            "/usr/bin/git",
            *validator._GIT_CONFIG_PREFIX,
            "-C",
            root.as_posix(),
            *arguments,
        ],
        input=input_bytes,
        check=False,
        capture_output=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert completed.stderr == b""
    return completed.stdout


def _fixture_commit_file(
    validator: Any,
    root: Path,
    relative: Path,
    payload: bytes,
) -> str:
    path = root / relative
    path.write_bytes(payload)
    _fixture_git(validator, root, "add", "--", relative.as_posix())
    _fixture_git(validator, root, "commit", "--quiet", "-m", f"mutate {relative.name}")
    return _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")


def _initial_sibling_reference() -> dict[str, Any]:
    return {
        "path": INITIAL_SIBLING_PATH.as_posix(),
        "sha256": INITIAL_SIBLING_SHA256,
        "creating_commit": INITIAL_SIBLING_COMMIT,
        "unique_a_history_verified": True,
    }


def _initialize_initial_sibling_repository(validator: Any, root: Path) -> str:
    assert root.is_dir() and not root.is_symlink()
    _fixture_git(validator, root, "init", "--quiet")
    _fixture_git(
        validator,
        root,
        "fetch",
        "--quiet",
        "--no-tags",
        PROJECT_ROOT.as_posix(),
        INITIAL_SIBLING_COMMIT,
    )
    _fixture_git(
        validator,
        root,
        "checkout",
        "--quiet",
        "-b",
        "synthetic-series",
        INITIAL_SIBLING_PARENT,
    )
    _fixture_git(
        validator,
        root,
        "merge",
        "--quiet",
        "--no-ff",
        "--no-edit",
        INITIAL_SIBLING_COMMIT,
    )
    return _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")


@pytest.fixture(scope="module")
def initial_sibling_baseline_repository(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    validator = _validator_module()
    root = tmp_path_factory.mktemp("v22-initial-sibling-baseline").resolve()
    head = _initialize_initial_sibling_repository(validator, root)
    return {"root": root, "execution_head": head}


@pytest.fixture
def initial_sibling_git_repository(
    initial_sibling_baseline_repository: dict[str, Any],
    tmp_path: Path,
) -> dict[str, Any]:
    validator = _validator_module()
    source = initial_sibling_baseline_repository["root"]
    root = tmp_path / "initial-sibling-repository"
    _fixture_git(
        validator,
        tmp_path,
        "clone",
        "--quiet",
        "--no-hardlinks",
        source.as_posix(),
        root.name,
    )
    return {
        "root": root,
        "execution_head": (
            _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")
        ),
    }


def _audit_hook_call_count(payload: bytes) -> int:
    tree = ast.parse(payload)
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "addaudithook")
            or (isinstance(node.func, ast.Name) and node.func.id == "addaudithook")
        )
    )


def _module_identity_subprocess(
    root: Path,
    implementation_commit: str,
    *,
    loaded_historical_module: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    interpreter = str(_preregistration()["exact_os_bootstrap_contract"]["python_launcher_path"])
    historical_modules = tuple(V2_1_HISTORICAL_AUTHORITY_MODULES)
    program = """
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

root = Path(sys.argv[1]).resolve(strict=True)
implementation_commit = sys.argv[2]
historical_modules = tuple(json.loads(sys.argv[3]))
loaded_historical_module = sys.argv[4] or None
try:
    validator = importlib.import_module(
        "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
    )
    historical_loaded_before = {
        module_name: module_name in sys.modules for module_name in historical_modules
    }
    if loaded_historical_module is not None:
        sys.modules[loaded_historical_module] = ModuleType(loaded_historical_module)
    validator._validate_module_identity(root, implementation_commit)
except Exception as exc:
    print(json.dumps({
        "status": "ERROR",
        "exception_type": type(exc).__name__,
        "message": str(exc),
        "historical_loaded_before": historical_loaded_before,
    }, sort_keys=True, separators=(",", ":")))
    raise SystemExit(3)
print(json.dumps({
    "status": "PASS",
    "historical_loaded_before": historical_loaded_before,
}, sort_keys=True, separators=(",", ":")))
"""
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": root.as_posix(),
    }
    completed = subprocess.run(
        [
            interpreter,
            "-c",
            program,
            root.as_posix(),
            implementation_commit,
            json.dumps(historical_modules, separators=(",", ":")),
            loaded_historical_module or "",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=root,
        env=environment,
    )
    assert completed.stderr == ""
    lines = completed.stdout.splitlines()
    assert len(lines) == 1, completed.stdout
    result = json.loads(lines[0])
    assert isinstance(result, dict)
    return completed, result


def _assert_genuine_module_identity_pass(root: Path, implementation_commit: str) -> None:
    completed, result = _module_identity_subprocess(root, implementation_commit)
    assert completed.returncode == 0, result
    assert result == {
        "status": "PASS",
        "historical_loaded_before": {
            module_name: False for module_name in V2_1_HISTORICAL_AUTHORITY_MODULES
        },
    }


@pytest.fixture(scope="module")
def module_identity_baseline_repository(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    validator = _validator_module()
    root = tmp_path_factory.mktemp("v22-validator-module-identity")
    _fixture_git(validator, root, "init", "--quiet")
    _fixture_git(
        validator,
        root,
        "remote",
        "add",
        "fixture-source",
        PROJECT_ROOT.as_posix(),
    )
    _fixture_git(
        validator,
        root,
        "fetch",
        "--quiet",
        "--no-tags",
        "fixture-source",
        "+refs/heads/*:refs/remotes/fixture-source/*",
    )
    _fixture_git(
        validator,
        root,
        "checkout",
        "--quiet",
        "-b",
        "module-identity-baseline",
        PREREGISTRATION_COMMIT,
    )
    for relative in REGISTERED_SURFACE:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((PROJECT_ROOT / relative).read_bytes())
    _fixture_git(
        validator,
        root,
        "add",
        "--",
        *(relative.as_posix() for relative in REGISTERED_SURFACE),
    )
    _fixture_git(validator, root, "commit", "--quiet", "-m", "v2.2 exact surface")
    implementation_commit = (
        _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")
    )
    return {"root": root, "implementation_commit": implementation_commit}


@pytest.fixture
def module_identity_git_repository(
    module_identity_baseline_repository: dict[str, Any],
    tmp_path: Path,
) -> dict[str, Any]:
    validator = _validator_module()
    source = module_identity_baseline_repository["root"]
    root = tmp_path / "module-identity-repository"
    _fixture_git(
        validator,
        tmp_path,
        "clone",
        "--quiet",
        "--no-hardlinks",
        source.as_posix(),
        root.name,
    )
    return {
        "root": root,
        "implementation_commit": (
            _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")
        ),
    }


@pytest.fixture
def optional_blob_git_repository(tmp_path: Path) -> dict[str, Any]:
    validator = _validator_module()
    root = tmp_path / "optional-blob-git"
    root.mkdir(mode=0o700)
    _fixture_git(validator, root, "init", "--quiet")
    (root / "regular.py").write_bytes(b"REGULAR = True\n")
    executable = root / "executable.py"
    executable.write_bytes(b"#!/usr/bin/env python3\n")
    executable.chmod(0o755)
    (root / "tree").mkdir(mode=0o700)
    (root / "tree/module.py").write_bytes(b"TREE = True\n")
    (root / "link.py").symlink_to("regular.py")
    _fixture_git(validator, root, "add", "--all")
    _fixture_git(validator, root, "commit", "--quiet", "-m", "base blob modes")
    base_commit = _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")
    _fixture_git(
        validator,
        root,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{base_commit},gitlink",
    )
    _fixture_git(validator, root, "commit", "--quiet", "-m", "add gitlink")
    head = _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")

    first_blob = _fixture_git(
        validator,
        root,
        "hash-object",
        "-w",
        "--stdin",
        input_bytes=b"first\n",
    ).strip()
    second_blob = _fixture_git(
        validator,
        root,
        "hash-object",
        "-w",
        "--stdin",
        input_bytes=b"second\n",
    ).strip()
    duplicate_tree_payload = (
        b"100644 duplicate.py\0"
        + bytes.fromhex(first_blob.decode("ascii"))
        + b"100644 duplicate.py\0"
        + bytes.fromhex(second_blob.decode("ascii"))
    )
    duplicate_tree = (
        _fixture_git(
            validator,
            root,
            "hash-object",
            "-t",
            "tree",
            "--literally",
            "-w",
            "--stdin",
            input_bytes=duplicate_tree_payload,
        )
        .strip()
        .decode("ascii")
    )
    duplicate_commit = (
        _fixture_git(
            validator,
            root,
            "commit-tree",
            duplicate_tree,
            "-m",
            "duplicate path tree",
        )
        .strip()
        .decode("ascii")
    )

    malformed_tree_payload = (
        b"100644 malformed.py\0" + bytes.fromhex(first_blob.decode("ascii"))[:-1]
    )
    malformed_tree = (
        _fixture_git(
            validator,
            root,
            "hash-object",
            "-t",
            "tree",
            "--literally",
            "-w",
            "--stdin",
            input_bytes=malformed_tree_payload,
        )
        .strip()
        .decode("ascii")
    )
    malformed_commit = (
        _fixture_git(
            validator,
            root,
            "commit-tree",
            malformed_tree,
            "-m",
            "malformed mode tree",
        )
        .strip()
        .decode("ascii")
    )
    return {
        "root": root,
        "head": head,
        "duplicate_commit": duplicate_commit,
        "malformed_commit": malformed_commit,
    }


def _schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    bundle = json.loads((PROJECT_ROOT / BUNDLE_SCHEMA_RELATIVE).read_bytes())
    release = json.loads((PROJECT_ROOT / RELEASE_SCHEMA_RELATIVE).read_bytes())
    assert isinstance(bundle, dict) and isinstance(release, dict)
    return bundle, release


def _series_2_schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    bundle = json.loads((PROJECT_ROOT / SERIES_2_BUNDLE_SCHEMA_RELATIVE).read_bytes())
    release = json.loads((PROJECT_ROOT / SERIES_2_RELEASE_SCHEMA_RELATIVE).read_bytes())
    assert isinstance(bundle, dict) and isinstance(release, dict)
    return bundle, release


def _strip_json_pointer(value: object, pointer: str, *, required: bool) -> bool:
    assert pointer.startswith("/") and pointer != "/"
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    current = value
    for part in parts[:-1]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isascii() and part.isdecimal():
            index = int(part)
            if index >= len(current):
                if required:
                    raise AssertionError(f"JSON pointer does not resolve: {pointer}")
                return False
            current = current[index]
        else:
            if required:
                raise AssertionError(f"JSON pointer does not resolve: {pointer}")
            return False
    final = parts[-1]
    if isinstance(current, dict) and final in current:
        del current[final]
        return True
    if isinstance(current, list) and final.isascii() and final.isdecimal():
        index = int(final)
        if index < len(current):
            del current[index]
            return True
    if required:
        raise AssertionError(f"JSON pointer does not resolve: {pointer}")
    return False


def _typed_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        assert isinstance(right, dict)
        return set(left) == set(right) and all(_typed_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        assert isinstance(right, list)
        return len(left) == len(right) and all(
            _typed_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def _schemas_after_registered_delta_stripping(
    *,
    v2_2: dict[str, Any],
    v2_1: dict[str, Any],
    pointers: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    stripped_v2_2 = copy.deepcopy(v2_2)
    stripped_v2_1 = copy.deepcopy(v2_1)
    for pointer in pointers:
        _strip_json_pointer(stripped_v2_2, pointer, required=True)
        _strip_json_pointer(stripped_v2_1, pointer, required=False)
    return stripped_v2_2, stripped_v2_1


def _definition_validator(schema: dict[str, Any], definition: str) -> Draft202012Validator:
    return Draft202012Validator(
        {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            "$ref": f"#/$defs/{definition}",
        }
    )


def _tree_fingerprint(path: Path) -> tuple[tuple[str, str, int, str], ...] | None:
    if not path.exists() and not path.is_symlink():
        return None
    root = path.absolute()
    records: list[tuple[str, str, int, str]] = []
    for candidate in (root, *sorted(root.rglob("*"), key=lambda item: os.fsencode(item))):
        relative = "." if candidate == root else candidate.relative_to(root).as_posix()
        metadata = candidate.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISREG(metadata.st_mode):
            kind = "file"
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        elif stat.S_ISDIR(metadata.st_mode):
            kind = "directory"
            digest = ""
        elif stat.S_ISLNK(metadata.st_mode):
            kind = "symlink"
            digest = os.readlink(candidate)
        else:
            kind = "special"
            digest = ""
        records.append((relative, kind, mode, digest))
    return tuple(records)


def _imported_modules(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module not in {None, "__future__"}:
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


def _real_registered_paths() -> tuple[Path, Path]:
    preregistration = _preregistration()
    root = Path(str(preregistration["exact_os_bootstrap_contract"]["repository_root"]))
    ledger = Path(str(preregistration["series_ledger_contract"]["root_path"]))
    destination = root / "docs/phase4/rehearsals/P4.2a-v2-calibration-v2-2"
    return ledger, destination


def _all_real_path_fingerprints() -> tuple[tuple[tuple[str, str, int, str], ...] | None, ...]:
    ledger, destination = _real_registered_paths()
    registered_root = Path(_preregistration()["exact_os_bootstrap_contract"]["repository_root"])
    series_2_amendment = json.loads((PROJECT_ROOT / SERIES_2_PREREGISTRATION_RELATIVE).read_bytes())
    series_2_paths = series_2_amendment[
        "part_4_fresh_series_identity_and_visible_primary_mirror_paths"
    ]
    return (
        _tree_fingerprint(ledger),
        _tree_fingerprint(destination),
        _tree_fingerprint(V2_1_CONSUMED_CLAIM),
        _tree_fingerprint(registered_root / "docs/phase4/rehearsals/P4.2a-v2-calibration-v2-1"),
        _tree_fingerprint(registered_root / "docs/phase4/eval/v2-calibration/heldout"),
        _tree_fingerprint(Path(series_2_paths["primary_ledger_root"])),
        _tree_fingerprint(Path(series_2_paths["primary_receipt_root"])),
        _tree_fingerprint(Path(series_2_paths["secondary_snapshot_root"])),
        _tree_fingerprint(Path(series_2_paths["secondary_receipt_root"])),
    )


def _validator_module() -> Any:
    return importlib.import_module(VALIDATOR_MODULE)


def _minimal_execution_binding(*, mode: str) -> dict[str, Any]:
    if mode == "REGISTERED_OFFICIAL":
        return {
            "mode": mode,
            "project_root": "/Users/ouyangduning/Documents/project/interesting/AlphaPilot-AI",
            "absolute_destination": (
                "/Users/ouyangduning/Documents/project/interesting/AlphaPilot-AI/"
                "docs/phase4/rehearsals/P4.2a-v2-calibration-v2-2"
            ),
            "series_token_sha256": (
                "35ba1b83a9b187817d7a591758e1c131e867fcd37917cba0ab196799fff832ef"
            ),
            "ledger_root": (
                "/Users/ouyangduning/Documents/project/interesting/"
                ".alphapilot-p4-2a-v2-2-execution-claim-"
                "35ba1b83a9b187817d7a591758e1c131e867fcd37917cba0ab196799fff832ef"
            ),
            "derivation_recomputed": True,
            "private_rebase_capability_validated": False,
            "registered_rehearsal_paths_created_as_expected": True,
        }
    return {
        "mode": "DISPOSABLE_FULL_SHAPE_TEST",
        "project_root": "/private/tmp/alphapilot-v22-synthetic",
        "absolute_destination": (
            "/private/tmp/alphapilot-v22-synthetic/docs/phase4/rehearsals/P4.2a-v2-calibration-v2-2"
        ),
        "series_token_sha256": "1" * 64,
        "ledger_root": "/private/tmp/.alphapilot-p4-2a-v2-2-execution-claim-" + "1" * 64,
        "derivation_recomputed": True,
        "private_rebase_capability_validated": True,
        "real_registered_paths_untouched": True,
    }


def _producer_materialization_manifest_payload(
    *,
    implementation_commit: str = V2_1_PIPELINE_IMPLEMENTATION_COMMIT,
) -> bytes:
    prepare = importlib.import_module("scripts.prepare_p4_2a_v2_heldout")
    manifest = {
        "schema_version": prepare.MATERIALIZATION_MANIFEST_V2_SCHEMA,
        "execution_authority": {
            "mode": "offline_rehearsal",
            "frame_authority": {
                "path": prepare.FRAME_AUTHORITY_PATH.as_posix(),
                "sha256": prepare.FRAME_AUTHORITY_SHA256,
            },
            "successor_code_gate_authority": {
                "path": prepare.SUCCESSOR_CODE_GATE_AUTHORITY_PATH.as_posix(),
                "sha256": prepare.SUCCESSOR_CODE_GATE_AUTHORITY_SHA256,
            },
            "successor_preregistration": {
                "path": prepare.SUCCESSOR_V2_1_PREREGISTRATION_PATH.as_posix(),
                "sha256": prepare.SUCCESSOR_V2_1_PREREGISTRATION_SHA256,
            },
            "preregistration_commit": prepare.SUCCESSOR_V2_1_PREREGISTRATION_COMMIT,
            "implementation_commit": implementation_commit,
            "rehearsal_bundle": None,
            "release_authorization": None,
        },
        "request_pacing": {
            "cninfo_pdf": {
                "host": "static.cninfo.com.cn",
                "policy": "minimum_start_to_start",
                "configured_min_start_to_start_seconds": 1.0,
                "clock": "monotonic",
                "first_request_delayed": False,
                "request_start_count": 2824,
                "observed_gap_count": 2823,
                "minimum_observed_start_to_start_seconds": 1.0,
                "median_observed_start_to_start_seconds": 1.0,
                "violation_count": 0,
                "retry_count": 0,
            },
            "akshare_ths": "not_applicable_no_external_document_fetch",
            "sina_company_news": "not_applicable_no_external_document_fetch",
        },
        "runtime_start_preflight": {
            "mode": "offline_rehearsal",
            "host_probe_performed": False,
            "reason": "not_applicable_offline_rehearsal",
        },
    }
    return (
        json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _minimal_cross_valid_bundle_receipt() -> tuple[dict[str, Any], dict[str, Any]]:
    validator = _validator_module()
    binding = _minimal_execution_binding(mode="DISPOSABLE_FULL_SHAPE_TEST")
    authority = {
        "path": "docs/phase4/reports/synthetic-authority.json",
        "sha256": "1" * 64,
        "creating_commit": "2" * 40,
        "unique_a_history_verified": True,
    }
    review = {
        "path": "docs/phase4/reports/synthetic-review.json",
        "sha256": "3" * 64,
        "creating_commit": "4" * 40,
        "unique_a_history_verified": True,
    }
    records = [
        {
            "ordinal": 1,
            "outcome": "FAILED",
            "implementation_epoch": 5,
            "record_root_sha256": "5" * 64,
        },
        {
            "ordinal": 2,
            "outcome": "INCOMPLETE_UNTERMINALIZED",
            "implementation_epoch": 5,
            "record_root_sha256": "6" * 64,
        },
        {
            "ordinal": 3,
            "outcome": "CANDIDATE_VALIDATED_AND_SELECTED",
            "implementation_epoch": 5,
            "record_root_sha256": "7" * 64,
        },
    ]
    outcomes = [
        {
            "ordinal": record["ordinal"],
            "outcome": record["outcome"],
            "implementation_epoch": record["implementation_epoch"],
            "record_root_sha256": record["record_root_sha256"],
        }
        for record in records
    ]
    epoch = {
        "epoch": 5,
        "implementation_commit": "8" * 40,
        "owner_exact_surface_authorization": authority,
        "independent_implementation_review": review,
        "control_merkle_root_sha256": "9" * 64,
        "first_attempt_ordinal": 1,
        "last_attempt_ordinal": 3,
        "all_attempts_authorized": True,
    }
    shared_lineage = {
        "v2_1_consumed_attempt_incident": authority,
        "v2_2_remediation_request": review,
        "v2_2_preregistration_scope_authorization": authority,
    }
    bundle = {
        "execution_binding": binding,
        "attempt_history": {
            "series_id": validator.REHEARSAL_ID,
            "series_token_sha256": binding["series_token_sha256"],
            "ledger_root": binding["ledger_root"],
            "records": records,
            "selected_attempt_ordinal": 3,
            "history_root_sha256": "a" * 64,
            "live_ledger_root_sha256": "b" * 64,
        },
        "implementation_epochs": [epoch],
        "lineage": {
            "preregistration": authority,
            "bundle_schema": {"path": "bundle.schema.json", "sha256": "c" * 64},
            "release_authorization_schema": {
                "path": "release.schema.json",
                "sha256": "d" * 64,
            },
            "preregistration_commit": "e" * 40,
            **shared_lineage,
        },
        "merkle": {"bundle_root_sha256": "f" * 64},
    }
    receipt = {
        "execution_binding": copy.deepcopy(binding),
        "owner_authorization": {
            "owner": "ouyang",
            "approved": True,
            "approval_scope": (
                "rehearsal_evidence_and_complete_attempt_history_only_not_real_stage_release"
            ),
            "accepts_disclosed_repeatability": True,
            "acknowledged_attempt_count": 3,
            "acknowledged_failed_count": 1,
            "acknowledged_incomplete_count": 1,
            "acknowledged_outcomes": outcomes,
            "selected_attempt_ordinal": 3,
            "attempt_history_root_sha256": "a" * 64,
            "all_attempt_outcomes_reviewed": True,
            "no_hidden_or_omitted_attempt_accepted": True,
            "acknowledged_outcomes_are_contiguous_and_ordered": True,
        },
        "series_identity": {
            "series_id": validator.REHEARSAL_ID,
            "policy": validator.SERIES_POLICY,
            "series_token_sha256": binding["series_token_sha256"],
            "ledger_root": binding["ledger_root"],
            "series_closed": True,
        },
        "attempt_history_acceptance": {
            "policy": validator.SERIES_POLICY,
            "series_closed": True,
            "attempt_count": 3,
            "failed_count": 1,
            "incomplete_count": 1,
            "selected_attempt_ordinal": 3,
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
            "owner_acknowledged_outcomes_equal_ordered_bundle_records": True,
            "selected_ordinal_is_the_unique_validated_candidate": True,
            "selected_ordinal_and_epoch_match_lineage": True,
            "history_and_live_roots_match_lineage_and_bundle": True,
        },
        "implementation_epochs": [
            {
                "epoch": 5,
                "implementation_commit": epoch["implementation_commit"],
                "owner_surface_authorization": authority,
                "independent_implementation_review": review,
                "control_merkle_root_sha256": epoch["control_merkle_root_sha256"],
                "first_attempt_ordinal": 1,
                "last_attempt_ordinal": 3,
            }
        ],
        "lineage": {
            "preregistration": bundle["lineage"]["preregistration"],
            "bundle_schema": bundle["lineage"]["bundle_schema"],
            "release_schema": bundle["lineage"]["release_authorization_schema"],
            "bundle_root_sha256": bundle["merkle"]["bundle_root_sha256"],
            "attempt_history_root_sha256": "a" * 64,
            "live_ledger_root_sha256": "b" * 64,
            "preregistration_commit": bundle["lineage"]["preregistration_commit"],
            "selected_implementation_commit": epoch["implementation_commit"],
            "v2_1_incident": shared_lineage["v2_1_consumed_attempt_incident"],
            "remediation_request": shared_lineage["v2_2_remediation_request"],
            "v2_2_scope_authorization": shared_lineage["v2_2_preregistration_scope_authorization"],
        },
        "authorized_stages": [],
        "still_gated": list(validator._REAL_STAGES),
        "production_integration_gate": {"this_receipt_unlocks_real_stages": False},
        "locks": {
            "p4_2a_done": False,
            "heldout_materialization_authorized_by_this_receipt": False,
            "heldout_inference_authorized_by_this_receipt": False,
            "heldout_evaluation_unlocked": False,
            "p4_2b_unlocked": False,
            "p4_3_unlocked": False,
            "non_simulate_orders_allowed": False,
            "trading_mode": "research",
        },
    }
    return bundle, receipt


def test_validator_public_apis_are_fixed_keyword_only_read_contracts() -> None:
    validator = _validator_module()
    bundle_signature = inspect.signature(validator.validate_bundle)
    release_signature = inspect.signature(validator.validate_release_authorization)
    assert tuple(bundle_signature.parameters) == (
        "project_root",
        "bundle_path",
        "execution_context",
        "validator_delegation",
    )
    assert tuple(release_signature.parameters) == (
        "project_root",
        "receipt_path",
        "execution_context",
        "validator_delegation",
    )
    for signature in (bundle_signature, release_signature):
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in signature.parameters.values()
        )
        assert signature.parameters["execution_context"].default is None
        assert signature.parameters["validator_delegation"].default is None


def test_canonical_imported_validator_cannot_mint_official_replay_or_read_bundle() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    binding = implementation.derive_execution_binding()
    before = _all_real_path_fingerprints()
    with (
        pytest.raises(
            implementation.RehearsalV22Error,
            match=r"replay|identity|binding|active package|audit|lifetime",
        ),
        implementation._official_validator_replay_scope(
            binding=binding,
            validator_module=validator,
            bundle_path=binding.destination / implementation.BUNDLE_FILENAME,
            implementation_commit="0" * 40,
        ),
    ):
        raise AssertionError("canonical import must never enter official replay scope")
    assert _all_real_path_fingerprints() == before


def test_unprovisioned_unauthorized_import_rejects_before_any_storage_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    binding = implementation.derive_execution_binding()
    observed_probes: list[str] = []

    def poison(name: str) -> Callable[..., Any]:
        def reject_probe(*_args: object, **_kwargs: object) -> Any:
            observed_probes.append(name)
            raise AssertionError(f"unauthorized import reached {name}")

        return reject_probe

    for name in (
        "validate_live_history",
        "_validate_second_copy_history",
        "_read_only_storage_preflight",
        "_validate_registered_storage_roots",
        "_registered_storage_directory",
    ):
        monkeypatch.setattr(implementation, name, poison(name))
    with (
        pytest.raises(
            implementation.RehearsalV22Error,
            match=r"active|authority|identity|package|scope",
        ),
        implementation._official_validator_replay_scope(
            binding=binding,
            validator_module=validator,
            bundle_path=binding.destination / implementation.BUNDLE_FILENAME,
            implementation_commit="0" * 40,
        ),
    ):
        raise AssertionError("unauthorized canonical import entered replay scope")
    assert observed_probes == []


@pytest.mark.parametrize(
    "candidate_kind",
    ("unauthorized-staged", "cross-root", "official-prepublication"),
)
def test_imported_validator_rejects_unborrowed_staged_or_prepublished_bundle_path(
    tmp_path: Path,
    candidate_kind: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    raw_binding = implementation.derive_execution_binding()
    binding = validator._binding_view(raw_binding)
    before = _all_real_path_fingerprints()
    if candidate_kind == "official-prepublication":
        bundle_path = raw_binding.destination / implementation.BUNDLE_FILENAME
        assert not bundle_path.exists()
    else:
        root = tmp_path / candidate_kind
        root.mkdir(mode=0o700)
        bundle_path = root / implementation.BUNDLE_FILENAME
        bundle_path.write_bytes(b"{}\n")
        bundle_path.chmod(0o600)
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"active|authority|borrow|candidate|lifetime|scope",
    ):
        validator._authorized_bundle_directory(
            binding=binding,
            raw_binding=raw_binding,
            bundle_path=bundle_path,
            published_release_revalidation=False,
        )
    assert _all_real_path_fingerprints() == before


def test_forged_or_stale_official_replay_capability_rejects_before_bundle_access() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    binding = implementation.derive_execution_binding()
    closure = inspect.getclosurevars(implementation._validate_replay_capability).nonlocals
    replay_registry = closure["replay_registry"]
    replay_nonce = closure["replay_nonce"]
    assert replay_registry == ()
    before = _all_real_path_fingerprints()
    forged = implementation._ReplayCapability(
        _nonce=replay_nonce,
        binding=binding,
        validator_module=validator,
        validator_module_id=id(validator),
        bundle_path=binding.destination / implementation.BUNDLE_FILENAME,
        bundle_sha256="1" * 64,
        implementation_commit="2" * 40,
        history_root_sha256="3" * 64,
        control_merkle_root_sha256="4" * 64,
        real_path_fingerprints=implementation._real_path_fingerprints(),
    )
    stolen_snapshot = (*replay_registry, forged)
    assert stolen_snapshot == (forged,)
    assert (
        inspect.getclosurevars(implementation._validate_replay_capability).nonlocals[
            "replay_registry"
        ]
        == ()
    )
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"replay|identity|forged|stale",
    ):
        implementation._validate_replay_capability(forged)
    assert _all_real_path_fingerprints() == before


def test_validator_imports_only_the_package_implementation_authority() -> None:
    tree = ast.parse((PROJECT_ROOT / VALIDATOR_RELATIVE).read_bytes())
    imports = _imported_modules(tree)
    assert IMPLEMENTATION_MODULE in imports
    assert "scripts.rehearse_p4_2a_v2_2_heldout_full_path" not in imports
    validator = _validator_module()
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    assert validator._implementation_module is implementation
    assert sys.modules[IMPLEMENTATION_MODULE] is implementation
    assert id(validator._implementation_module._AUDIT_POLICY) == id(implementation._AUDIT_POLICY)
    assert id(validator._implementation_module._TEMP_AUTHORITY) == id(
        implementation._TEMP_AUTHORITY
    )


def test_genuine_selected_module_identity_accepts_frozen_unloaded_historical_sources(
    module_identity_baseline_repository: dict[str, Any],
) -> None:
    validator = _validator_module()
    root = module_identity_baseline_repository["root"]
    implementation_commit = module_identity_baseline_repository["implementation_commit"]
    closure = validator._independent_local_import_closure(
        project_root=root,
        implementation_commit=implementation_commit,
    )
    expected_hook_sources = {IMPLEMENTATION_RELATIVE.as_posix(): 1}
    for module_name, (
        relative,
        expected_sha256,
        expected_hook_count,
    ) in V2_1_HISTORICAL_AUTHORITY_MODULES.items():
        assert module_name not in sys.modules
        payload = closure[relative.as_posix()]
        assert hashlib.sha256(payload).hexdigest() == expected_sha256
        assert _audit_hook_call_count(payload) == expected_hook_count
        expected_hook_sources[relative.as_posix()] = expected_hook_count
    assert validator._audit_hook_source_map(closure) == expected_hook_sources
    _assert_genuine_module_identity_pass(root, implementation_commit)


@pytest.mark.parametrize(
    "historical_module",
    tuple(V2_1_HISTORICAL_AUTHORITY_MODULES),
    ids=("v2-1-runner-loaded", "v2-1-validator-loaded"),
)
def test_genuine_module_identity_rejects_any_loaded_historical_authority_module(
    module_identity_baseline_repository: dict[str, Any],
    historical_module: str,
) -> None:
    root = module_identity_baseline_repository["root"]
    implementation_commit = module_identity_baseline_repository["implementation_commit"]
    _assert_genuine_module_identity_pass(root, implementation_commit)
    completed, result = _module_identity_subprocess(
        root,
        implementation_commit,
        loaded_historical_module=historical_module,
    )
    assert completed.returncode == 3
    assert result["exception_type"] == "RehearsalV22ValidationError"
    assert "module, authority owner, or ContextVar identity split" in result["message"]
    assert result["historical_loaded_before"] == {
        module_name: False for module_name in V2_1_HISTORICAL_AUTHORITY_MODULES
    }


@pytest.mark.parametrize(
    "relative",
    (
        Path("scripts/prepare_p4_2a_v2_heldout.py"),
        Path("scripts/rehearse_p4_2a_v2_heldout_full_path.py"),
        Path("scripts/build_p4_2a_gold_sample.py"),
    ),
    ids=("prepare", "v2-runner", "gold-builder"),
)
def test_genuine_module_identity_rejects_an_audit_hook_in_any_other_active_closure_source(
    module_identity_git_repository: dict[str, Any],
    relative: Path,
) -> None:
    validator = _validator_module()
    root = module_identity_git_repository["root"]
    baseline_commit = module_identity_git_repository["implementation_commit"]
    _assert_genuine_module_identity_pass(root, baseline_commit)
    payload = (root / relative).read_bytes()
    payload += (
        b"\ndef _forbidden_extra_audit_hook() -> None:\n"
        b"    import sys\n"
        b"    sys.addaudithook(lambda *_args: None)\n"
    )
    drifted_commit = _fixture_commit_file(validator, root, relative, payload)
    completed, result = _module_identity_subprocess(root, drifted_commit)
    assert completed.returncode == 3
    assert result["exception_type"] == "RehearsalV22ValidationError"
    assert result["message"] == "implementation is not the sole process audit-hook installer"


@pytest.mark.parametrize("mutation", ("missing", "duplicate"))
def test_genuine_module_identity_rejects_missing_or_duplicate_core_audit_hook(
    module_identity_git_repository: dict[str, Any],
    mutation: str,
) -> None:
    validator = _validator_module()
    root = module_identity_git_repository["root"]
    baseline_commit = module_identity_git_repository["implementation_commit"]
    _assert_genuine_module_identity_pass(root, baseline_commit)
    payload = (root / IMPLEMENTATION_RELATIVE).read_bytes()
    call = b"sys.addaudithook(_process_audit_hook)"
    assert payload.count(call) == 1
    if mutation == "missing":
        payload = payload.replace(call, b"pass  # process audit hook removed", 1)
    else:
        payload = payload.replace(call, call + b"\n" + call, 1)
    drifted_commit = _fixture_commit_file(
        validator,
        root,
        IMPLEMENTATION_RELATIVE,
        payload,
    )
    completed, result = _module_identity_subprocess(root, drifted_commit)
    assert completed.returncode == 3
    assert result["exception_type"] == "RehearsalV22ValidationError"
    assert result["message"] == "implementation is not the sole process audit-hook installer"


@pytest.mark.parametrize(
    "historical_module",
    tuple(V2_1_HISTORICAL_AUTHORITY_MODULES),
    ids=("v2-1-runner-bytes", "v2-1-validator-bytes"),
)
def test_genuine_module_identity_rejects_historical_authority_byte_drift(
    module_identity_git_repository: dict[str, Any],
    historical_module: str,
) -> None:
    validator = _validator_module()
    root = module_identity_git_repository["root"]
    baseline_commit = module_identity_git_repository["implementation_commit"]
    _assert_genuine_module_identity_pass(root, baseline_commit)
    relative, _sha256_value, _hook_count = V2_1_HISTORICAL_AUTHORITY_MODULES[historical_module]
    drifted_commit = _fixture_commit_file(
        validator,
        root,
        relative,
        (root / relative).read_bytes() + b"\n# forbidden historical byte drift\n",
    )
    completed, result = _module_identity_subprocess(root, drifted_commit)
    assert completed.returncode == 3
    assert result["exception_type"] == "RehearsalV22ValidationError"
    assert result["message"] == (f"inert historical authority bytes drifted: {relative.as_posix()}")


def test_disposable_validation_is_nested_only_and_standalone_remains_official() -> None:
    # Owner withdrew the earlier synthetic-standalone request: the registered
    # disposable bootstrap remains the shim as __main__, with the validator
    # imported only inside its live capability/delegation lifetime.
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    exact_os = _preregistration()["exact_os_bootstrap_contract"]
    disposable = exact_os["disposable_full_shape_os_bootstrap"]
    template = disposable["os_execve_argv_template"]
    assert template[4] == "{synthetic_project_root}/" + SHIM_RELATIVE.as_posix()
    assert VALIDATOR_RELATIVE.as_posix() not in template
    published_source = inspect.getsource(implementation._validate_published_validator_bundle)
    assert 'and binding.mode == "REGISTERED_OFFICIAL"' in published_source
    assert 'binding.mode in {"REGISTERED_OFFICIAL", "DISPOSABLE_FULL_SHAPE_TEST"}' not in (
        published_source
    )
    candidate_source = inspect.getsource(implementation._validate_official_validator_candidate)
    active_source = inspect.getsource(implementation._active_validator_execution_context)
    assert "_active_validator_execution_context(" in candidate_source
    assert "validate_execution_capability(" in active_source


def test_official_replay_scope_ast_has_only_two_guarded_second_copy_paths() -> None:
    implementation_tree = ast.parse((PROJECT_ROOT / IMPLEMENTATION_RELATIVE).read_bytes())
    functions = {
        node.name: node
        for node in implementation_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    scope = functions["_official_validator_replay_scope"]

    def call_name(node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return None

    def ordered_calls(node: ast.AST) -> list[tuple[int, str]]:
        return sorted(
            (
                (call.lineno, name)
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                if (name := call_name(call)) is not None
            ),
            key=lambda item: item[0],
        )

    yields = sorted(
        (node for node in ast.walk(scope) if isinstance(node, ast.Yield)),
        key=lambda node: node.lineno,
    )
    assert len(yields) == 2
    in_process_branches = [
        node
        for node in scope.body
        if isinstance(node, ast.If)
        and any(isinstance(child, ast.Yield) for child in ast.walk(node))
    ]
    assert len(in_process_branches) == 1
    in_process = in_process_branches[0]
    in_process_end = in_process.end_lineno
    assert isinstance(in_process_end, int)
    assert len([node for node in ast.walk(in_process) if isinstance(node, ast.Yield)]) == 1
    assert yields[0].lineno <= in_process_end

    in_process_test_names = {
        node.id for node in ast.walk(in_process.test) if isinstance(node, ast.Name)
    }
    in_process_test_attributes = {
        node.attr for node in ast.walk(in_process.test) if isinstance(node, ast.Attribute)
    }
    assert {"validator_module", "package_validator_name", "sys"}.issubset(in_process_test_names)
    assert {"__name__", "modules"}.issubset(in_process_test_attributes)

    in_process_calls = ordered_calls(in_process)
    in_process_positions: dict[str, list[int]] = {}
    for line, name in in_process_calls:
        in_process_positions.setdefault(name, []).append(line)
    for required in (
        "_active_validator_execution_context",
        "validate_live_history",
        "_validate_second_copy_history",
        "_validate_official_validator_candidate",
        "_regular_bytes",
    ):
        assert len(in_process_positions.get(required, [])) >= 1
    assert (
        min(in_process_positions["_active_validator_execution_context"])
        < min(in_process_positions["validate_live_history"])
        < min(in_process_positions["_validate_second_copy_history"])
        < min(in_process_positions["_validate_official_validator_candidate"])
        < min(in_process_positions["_regular_bytes"])
        < yields[0].lineno
    )
    assert any(
        isinstance(node, ast.Return) and node.lineno > yields[0].lineno
        for node in ast.walk(in_process)
    )

    standalone_guards = [
        node
        for node in scope.body
        if isinstance(node, ast.If)
        and node.lineno > in_process_end
        and any(isinstance(child, ast.Raise) for child in ast.walk(node))
        and not any(isinstance(child, ast.Yield) for child in ast.walk(node))
    ]
    assert len(standalone_guards) >= 1
    standalone_guard = standalone_guards[0]
    standalone_guard_end = standalone_guard.end_lineno
    assert isinstance(standalone_guard_end, int)
    guard_names = {
        node.id for node in ast.walk(standalone_guard.test) if isinstance(node, ast.Name)
    }
    guard_attributes = {
        node.attr for node in ast.walk(standalone_guard.test) if isinstance(node, ast.Attribute)
    }
    guard_constants = {
        node.value for node in ast.walk(standalone_guard.test) if isinstance(node, ast.Constant)
    }
    assert {
        "binding",
        "REGISTERED_PROJECT_ROOT",
        "validator_module",
        "sys",
        "package_validator_name",
    }.issubset(guard_names)
    assert {"mode", "project_root", "__name__", "modules"}.issubset(guard_attributes)
    assert {"REGISTERED_OFFICIAL", "__main__"}.issubset(guard_constants)

    standalone_calls = [
        (line, name)
        for line, name in ordered_calls(scope)
        if line > standalone_guard_end and not (in_process.lineno <= line <= in_process_end)
    ]
    standalone_positions: dict[str, list[int]] = {}
    for line, name in standalone_calls:
        standalone_positions.setdefault(name, []).append(line)
    for required in (
        "_assert_locked_validator_bootstrap",
        "validate_live_history",
        "_validate_second_copy_history",
        "_regular_bytes",
        "mkdir",
    ):
        assert len(standalone_positions.get(required, [])) >= 1
    assert (
        min(standalone_positions["_assert_locked_validator_bootstrap"])
        < min(standalone_positions["validate_live_history"])
        < min(standalone_positions["_validate_second_copy_history"])
        < min(standalone_positions["_regular_bytes"])
        < min(standalone_positions["mkdir"])
        < yields[1].lineno
    )

    prefix_calls = [name for line, name in ordered_calls(scope) if line < in_process.lineno]
    forbidden_unconditional_probes = {
        "validate_live_history",
        "_validate_second_copy_history",
        "_read_only_storage_preflight",
        "_validate_registered_storage_roots",
        "_registered_storage_directory",
    }
    assert forbidden_unconditional_probes.isdisjoint(prefix_calls)
    all_call_names = [name for _line, name in ordered_calls(scope)]
    assert all_call_names.count("_regular_bytes") == 2
    assert len(in_process_positions["_regular_bytes"]) == 1
    assert len(standalone_positions["_regular_bytes"]) == 1
    assert len(in_process_positions["_validate_official_validator_candidate"]) == 1
    assert "_validate_official_validator_candidate" not in standalone_positions
    assert len(in_process_positions["_validate_second_copy_history"]) >= 1
    assert len(standalone_positions["_validate_second_copy_history"]) >= 1


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        (b'{"a":1,"a":2}', "duplicate"),
        (b'{"a":NaN}', "numeric"),
        (b'{"a":Infinity}', "numeric"),
        (b'{"a":-Infinity}', "numeric"),
    ),
)
def test_strict_json_rejects_duplicates_and_nonfinite_numbers(
    payload: bytes,
    message: str,
) -> None:
    validator = _validator_module()
    with pytest.raises(validator.RehearsalV22ValidationError, match=message):
        validator.strict_json_loads(payload, label="test record")


def test_materialization_manifest_accepts_the_real_fixed_v2_1_pipeline_producer() -> None:
    validator = _validator_module()
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    assert validator._V2_1_IMPLEMENTATION_COMMIT == V2_1_PIPELINE_IMPLEMENTATION_COMMIT
    assert implementation.V2_1_IMPLEMENTATION_COMMIT == V2_1_PIPELINE_IMPLEMENTATION_COMMIT
    payload = _producer_materialization_manifest_payload()
    manifest = json.loads(payload)
    assert manifest["execution_authority"]["mode"] == "offline_rehearsal"
    assert (
        manifest["execution_authority"]["implementation_commit"]
        == V2_1_PIPELINE_IMPLEMENTATION_COMMIT
    )
    validator._validate_materialization_manifest(
        payload,
        pipeline_implementation_commit=V2_1_PIPELINE_IMPLEMENTATION_COMMIT,
    )


@pytest.mark.parametrize(
    ("wrong_commit_kind", "wrong_side"),
    (
        ("selected-v2-2", "manifest"),
        ("selected-v2-2", "validator-expected"),
        ("arbitrary", "manifest"),
        ("arbitrary", "validator-expected"),
    ),
    ids=(
        "selected-v2-2-in-manifest",
        "selected-v2-2-as-producer-expectation",
        "arbitrary-in-manifest",
        "arbitrary-as-producer-expectation",
    ),
)
def test_materialization_manifest_never_accepts_a_harness_or_arbitrary_commit_as_producer(
    module_identity_baseline_repository: dict[str, Any],
    wrong_commit_kind: str,
    wrong_side: str,
) -> None:
    validator = _validator_module()
    baseline_payload = _producer_materialization_manifest_payload()
    validator._validate_materialization_manifest(
        baseline_payload,
        pipeline_implementation_commit=V2_1_PIPELINE_IMPLEMENTATION_COMMIT,
    )
    wrong_commit = (
        module_identity_baseline_repository["implementation_commit"]
        if wrong_commit_kind == "selected-v2-2"
        else "0" * 40
    )
    assert wrong_commit != V2_1_PIPELINE_IMPLEMENTATION_COMMIT
    payload = (
        _producer_materialization_manifest_payload(implementation_commit=wrong_commit)
        if wrong_side == "manifest"
        else baseline_payload
    )
    expected_commit = (
        V2_1_PIPELINE_IMPLEMENTATION_COMMIT if wrong_side == "manifest" else wrong_commit
    )
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"materialization implementation commit drifted",
    ):
        validator._validate_materialization_manifest(
            payload,
            pipeline_implementation_commit=expected_commit,
        )


def test_archive_semantics_keep_pipeline_producer_and_harness_commit_dataflows_separate() -> None:
    tree = ast.parse((PROJECT_ROOT / VALIDATOR_RELATIVE).read_bytes())
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def calls(function_name: str, callee_name: str) -> list[ast.Call]:
        return [
            node
            for node in ast.walk(functions[function_name])
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == callee_name)
                or (isinstance(node.func, ast.Attribute) and node.func.attr == callee_name)
            )
        ]

    def keyword_name(call: ast.Call, keyword: str) -> str:
        values = [item.value for item in call.keywords if item.arg == keyword]
        assert len(values) == 1
        value = values[0]
        assert isinstance(value, ast.Name)
        return value.id

    materialization = functions["_validate_materialization_manifest"]
    semantics = functions["_validate_artifact_semantics"]
    epochs = functions["_validate_implementation_epochs"]
    assert [argument.arg for argument in materialization.args.kwonlyargs] == [
        "pipeline_implementation_commit"
    ]
    assert [argument.arg for argument in semantics.args.kwonlyargs] == [
        "pipeline_implementation_commit"
    ]
    manifest_calls = calls("_validate_artifact_semantics", "_validate_materialization_manifest")
    assert len(manifest_calls) == 1
    assert (
        keyword_name(manifest_calls[0], "pipeline_implementation_commit")
        == "pipeline_implementation_commit"
    )
    artifact_calls = calls("_validate_archives", "_validate_artifact_semantics")
    assert len(artifact_calls) == 1
    assert (
        keyword_name(artifact_calls[0], "pipeline_implementation_commit")
        == "_V2_1_IMPLEMENTATION_COMMIT"
    )
    control_calls = calls("_validate_archives", "_validate_control_archive")
    assert len(control_calls) == 1
    assert keyword_name(control_calls[0], "implementation_commit") == "implementation_commit"
    for callee_name in ("_validate_lineage", "_validate_harness_identity", "_validate_archives"):
        selected_calls = calls("_validate_common_bundle_once", callee_name)
        assert len(selected_calls) == 1
        assert keyword_name(selected_calls[0], "implementation_commit") == "implementation_commit"
    epoch_calls = calls("_validate_common_bundle_once", "_validate_implementation_epochs")
    assert len(epoch_calls) == 1
    assert keyword_name(epoch_calls[0], "replay") == "replay"
    assert keyword_name(epoch_calls[0], "archives") == "archives"
    replay_selected_bindings = [
        node
        for node in calls("_validate_common_bundle_once", "_require_equal")
        if len(node.args) >= 2
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "implementation_commit"
        and isinstance(node.args[1], ast.Attribute)
        and isinstance(node.args[1].value, ast.Name)
        and node.args[1].value.id == "replay"
        and node.args[1].attr == "selected_implementation_commit"
    ]
    assert len(replay_selected_bindings) == 1
    epoch_validation_calls = calls(
        "_validate_implementation_epochs", "validate_implementation_epoch"
    )
    assert len(epoch_validation_calls) == 1
    assert (
        keyword_name(epoch_validation_calls[0], "implementation_commit") == "implementation_commit"
    )
    assert any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "replay"
        and node.attr == "selected_implementation_epoch"
        for node in ast.walk(epochs)
    )
    # Lineage and control replay legitimately verify the carried-forward v2.1
    # commit as historical evidence.  The selected harness extraction and its
    # epoch gate must never substitute that constant for their live value.
    for function_name in ("_validate_common_bundle_once", "_validate_implementation_epochs"):
        assert not any(
            isinstance(node, ast.Name) and node.id == "_V2_1_IMPLEMENTATION_COMMIT"
            for node in ast.walk(functions[function_name])
        )


def test_both_active_schemas_are_closed_and_compile_under_draft_2020_12() -> None:
    for schema in _schemas():
        assert schema["additionalProperties"] is False
        Draft202012Validator.check_schema(schema)
    bundle, release = _schemas()
    assert len(bundle["required"]) == 21
    assert len(release["required"]) == 20


def test_bundle_attempt_outcome_discriminator_is_exactly_three_closed_shapes() -> None:
    bundle, _release = _schemas()
    attempt = bundle["$defs"]["attemptRecord"]
    assert attempt["additionalProperties"] is False
    branches = attempt["oneOf"]
    assert [branch["properties"]["outcome"]["const"] for branch in branches] == [
        "FAILED",
        "INCOMPLETE_UNTERMINALIZED",
        "CANDIDATE_VALIDATED_AND_SELECTED",
    ]
    failed, incomplete, success = (branch["properties"] for branch in branches)
    assert failed["candidate"] == {"type": "null"}
    assert incomplete["candidate"] == {"type": "null"}
    assert incomplete["terminal"] == {"type": "null"}
    assert incomplete["error"] == {"type": "null"}
    assert success["error"] == {"type": "null"}


def test_execution_binding_modes_are_mutually_exclusive_and_fail_closed() -> None:
    bundle, release = _schemas()
    for schema in (bundle, release):
        validator = _definition_validator(schema, "executionBinding")
        official_branch = schema["$defs"]["executionBinding"]["oneOf"][0]
        official = {
            field: official_branch["properties"][field]["const"]
            for field in official_branch["required"]
        }
        disposable = _minimal_execution_binding(mode="DISPOSABLE_FULL_SHAPE_TEST")
        assert list(validator.iter_errors(official)) == []
        assert list(validator.iter_errors(disposable)) == []
        assert (
            list(
                validator.iter_errors(
                    {
                        **disposable,
                        "mode": "REGISTERED_OFFICIAL",
                    }
                )
            )
            != []
        )
        assert (
            list(
                validator.iter_errors(
                    {
                        **official,
                        "private_rebase_capability_validated": True,
                    }
                )
            )
            != []
        )


def test_release_receipt_accepts_evidence_but_unlocks_no_real_stage() -> None:
    _bundle, release = _schemas()
    properties = release["properties"]
    assert properties["authorized_stages"]["const"] == []
    assert properties["still_gated"]["const"] == [
        "materialize",
        "infer",
        "select-blind",
        "blind-draft",
        "owner-adjudication-ui",
        "finalize-owner-adjudication",
        "heldout-evaluation",
        "p4.2b",
        "p4.3",
    ]
    production = properties["production_integration_gate"]["properties"]
    assert production["this_receipt_unlocks_real_stages"]["const"] is False
    assert production["status"]["const"] == ("PENDING_SEPARATE_PRODUCTION_INTEGRATION_CODE_GATE")
    locks = properties["locks"]["properties"]
    assert locks["heldout_materialization_authorized_by_this_receipt"]["const"] is False
    assert locks["heldout_inference_authorized_by_this_receipt"]["const"] is False
    assert locks["heldout_evaluation_unlocked"]["const"] is False
    assert locks["p4_2b_unlocked"]["const"] is False
    assert locks["p4_3_unlocked"]["const"] is False


def test_release_acknowledgement_requires_one_ordered_selected_success() -> None:
    _bundle, release = _schemas()
    owner = release["properties"]["owner_authorization"]["properties"]
    outcomes = owner["acknowledged_outcomes"]
    assert outcomes["minContains"] == outcomes["maxContains"] == 1
    assert outcomes["contains"]["properties"]["outcome"]["const"] == (
        "CANDIDATE_VALIDATED_AND_SELECTED"
    )
    acceptance = release["properties"]["attempt_history_acceptance"]["properties"]
    for field in (
        "counts_equal_recomputed_records",
        "owner_acknowledged_outcomes_equal_ordered_bundle_records",
        "selected_ordinal_is_the_unique_validated_candidate",
        "selected_ordinal_and_epoch_match_lineage",
        "history_and_live_roots_match_lineage_and_bundle",
    ):
        assert acceptance[field]["const"] is True


def test_release_cross_validation_accepts_only_the_exact_recomputed_history() -> None:
    validator = _validator_module()
    bundle, receipt = _minimal_cross_valid_bundle_receipt()
    before = _all_real_path_fingerprints()
    validator._cross_validate_release(bundle=bundle, receipt=receipt)
    assert _all_real_path_fingerprints() == before


def test_release_shape_identity_cross_and_topology_dominate_any_active_replay() -> None:
    validator = _validator_module()
    before = _all_real_path_fingerprints()
    source = inspect.getsource(validator._validate_release_once)
    ordered_markers = (
        "_schema_validate(",
        "_unique_a_unserialized(",
        "_validate_active_bundle_once(",
        "_cross_validate_release(",
        "_git_blob(root, reviewed_head",
        "_active_replay_validated_bundle(",
    )
    offsets = [source.index(marker) for marker in ordered_markers]
    assert offsets == sorted(offsets)
    assert source.count("_active_replay_validated_bundle(") == 1
    assert "_active_replay_validated_bundle(" not in inspect.getsource(
        validator._validate_active_bundle_once
    )
    assert "_active_replay_validated_bundle(" not in inspect.getsource(
        validator._validate_common_bundle_once
    )
    active_source = inspect.getsource(validator._active_replay_validated_bundle)
    assert active_source.index("_authorized_bundle_directory(") < active_source.index(
        "_active_replay_selected_pipeline("
    )
    assert active_source.index("_bundle_filesystem_is_exact(") < active_source.index(
        "_active_replay_selected_pipeline("
    )
    assert _all_real_path_fingerprints() == before


@pytest.mark.parametrize(
    "mutation",
    (
        "omit-outcome",
        "change-outcome",
        "count-mismatch",
        "selected-ordinal",
        "outcome-reorder",
        "record-root",
        "selected-epoch",
        "selected-commit",
        "history-root",
        "live-ledger-root",
    ),
)
def test_release_cross_validation_rejects_every_history_acknowledgement_drift(
    mutation: str,
) -> None:
    validator = _validator_module()
    bundle, receipt = _minimal_cross_valid_bundle_receipt()
    before = _all_real_path_fingerprints()
    owner = receipt["owner_authorization"]
    outcomes = owner["acknowledged_outcomes"]
    if mutation == "omit-outcome":
        outcomes.pop(0)
        owner["acknowledged_attempt_count"] = 2
    elif mutation == "change-outcome":
        outcomes[0]["outcome"] = "INCOMPLETE_UNTERMINALIZED"
    elif mutation == "count-mismatch":
        owner["acknowledged_failed_count"] = 2
    elif mutation == "selected-ordinal":
        owner["selected_attempt_ordinal"] = 2
    elif mutation == "outcome-reorder":
        outcomes[0], outcomes[1] = outcomes[1], outcomes[0]
    elif mutation == "record-root":
        outcomes[0]["record_root_sha256"] = "0" * 64
    elif mutation == "selected-epoch":
        outcomes[2]["implementation_epoch"] = 2
    elif mutation == "selected-commit":
        receipt["lineage"]["selected_implementation_commit"] = "0" * 40
    elif mutation == "history-root":
        owner["attempt_history_root_sha256"] = "0" * 64
    else:
        receipt["lineage"]["live_ledger_root_sha256"] = "0" * 64
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"acknowledgement|lineage|equal|expected|selected|history|root",
    ):
        validator._cross_validate_release(bundle=bundle, receipt=receipt)
    assert _all_real_path_fingerprints() == before


def test_attempt_record_contract_is_closed_canonical_and_cross_bound() -> None:
    contract = _preregistration()["attempt_record_contract"]
    for name, exact_fields in (
        ("started_json_schema", "started_exact_fields"),
        ("candidate_json_schema", "candidate_exact_fields"),
        ("terminal_json_schema", "terminal_exact_fields"),
    ):
        schema = contract[name]
        assert schema["additional_properties"] is False
        assert schema["required_exactly"] == exact_fields
        assert set(schema["properties"]) == set(contract[exact_fields])
    assert contract["active_validator_live_record_algorithm"].startswith(
        "read each live and archived record as bytes"
    )
    assert contract["not_persisted_evidence_allowed"] is False
    assert contract["every_actual_evidence_byte_must_be_archived"] is True


@pytest.mark.parametrize(
    ("schema_relative", "v2_1_schema_relative", "delta_key"),
    (
        (
            BUNDLE_SCHEMA_RELATIVE,
            V2_1_BUNDLE_SCHEMA_RELATIVE,
            "bundle_schema_delta_domains",
        ),
        (
            RELEASE_SCHEMA_RELATIVE,
            V2_1_RELEASE_SCHEMA_RELATIVE,
            "release_schema_delta_domains",
        ),
    ),
    ids=("bundle", "release"),
)
def test_schema_delta_allowlist_is_complete_and_retained_v2_1_schema_is_typed_equal(
    schema_relative: Path,
    v2_1_schema_relative: Path,
    delta_key: str,
) -> None:
    preregistration = _preregistration()
    pointers = preregistration["contract_inheritance"][delta_key]
    assert pointers and len(pointers) == len(set(pointers))
    assert all(pointer.startswith("/") and "*" not in pointer for pointer in pointers)
    v2_2 = json.loads((PROJECT_ROOT / schema_relative).read_bytes())
    v2_1 = json.loads((PROJECT_ROOT / v2_1_schema_relative).read_bytes())
    stripped_v2_2, stripped_v2_1 = _schemas_after_registered_delta_stripping(
        v2_2=v2_2,
        v2_1=v2_1,
        pointers=pointers,
    )
    assert _typed_equal(stripped_v2_2, stripped_v2_1)

    missing_v2_2, missing_v2_1 = _schemas_after_registered_delta_stripping(
        v2_2=v2_2,
        v2_1=v2_1,
        pointers=pointers[:-1],
    )
    assert not _typed_equal(missing_v2_2, missing_v2_1)

    unlisted_delta = copy.deepcopy(v2_2)
    unlisted_delta["additionalProperties"] = not unlisted_delta["additionalProperties"]
    drifted_v2_2, retained_v2_1 = _schemas_after_registered_delta_stripping(
        v2_2=unlisted_delta,
        v2_1=v2_1,
        pointers=pointers,
    )
    assert not _typed_equal(drifted_v2_2, retained_v2_1)

    with pytest.raises(AssertionError, match="does not resolve"):
        _schemas_after_registered_delta_stripping(
            v2_2=v2_2,
            v2_1=v2_1,
            pointers=[*pointers, "/not-a-registered-delta"],
        )


def test_active_validator_rederives_the_frozen_typed_inheritance_contract() -> None:
    validator = _validator_module()
    before = _all_real_path_fingerprints()
    validator._validate_contract_inheritance(
        project_root=PROJECT_ROOT,
        preregistration_payload=(PROJECT_ROOT / PREREGISTRATION_RELATIVE).read_bytes(),
        bundle_schema_payload=(PROJECT_ROOT / BUNDLE_SCHEMA_RELATIVE).read_bytes(),
        release_schema_payload=(PROJECT_ROOT / RELEASE_SCHEMA_RELATIVE).read_bytes(),
    )
    assert "_validate_contract_inheritance(" in inspect.getsource(
        validator._validate_common_bundle_once
    )
    assert _all_real_path_fingerprints() == before


@pytest.mark.parametrize(
    "verdict",
    (
        "DISAPPROVE_IMPLEMENTATION",
        "NOT_APPROVED_IMPLEMENTATION",
        "APPROVE_NOT_IMPLEMENTATION",
        "APPROVE_NON_IMPLEMENTATION",
        "REJECT_IMPLEMENTATION",
        "APPROVE_OTHER",
    ),
)
def test_validator_independent_review_rejects_nonapproval_substrings(verdict: str) -> None:
    validator = _validator_module()
    implementation_commit = "2" * 40
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"review|approve|target",
    ):
        validator._validate_implementation_review_document(
            document={
                "reviewed_commit": implementation_commit,
                "verdict": verdict,
                "blockers": [],
            },
            implementation_commit=implementation_commit,
            label="synthetic independent review",
        )
    assert "_validate_implementation_review_document(" in inspect.getsource(
        validator._validate_implementation_epochs
    )


def test_validator_independent_review_accepts_exact_approve_prefix_and_commit() -> None:
    validator = _validator_module()
    implementation_commit = "3" * 40
    validator._validate_implementation_review_document(
        document={
            "reviewed_commit": implementation_commit,
            "verdict": "APPROVE_V2_2_IMPLEMENTATION",
            "blockers": [],
        },
        implementation_commit=implementation_commit,
        label="synthetic independent review",
    )


def test_series_2_packer_emits_only_explicit_used_epochs_five_and_six(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    owner = implementation.AuthorityReference(
        "docs/phase4/reports/synthetic-owner.json",
        "1" * 64,
        "2" * 40,
    )
    review = implementation.AuthorityReference(
        "docs/phase4/reports/synthetic-review.json",
        "3" * 64,
        "4" * 40,
    )
    records = tuple(
        SimpleNamespace(
            ordinal=ordinal,
            implementation_epoch=epoch,
            previous_history_root_sha256=str(ordinal) * 64,
            owner_action_time_authorization=owner,
        )
        for ordinal, epoch in ((1, 5), (2, 6))
    )
    history = SimpleNamespace(records=records, selected_attempt_ordinal=2)
    binding = SimpleNamespace(project_root=tmp_path)

    def authorization(
        _binding: Any,
        _reference: Any,
        *,
        expected_ordinal: int,
        expected_previous_history_root_sha256: str,
        require_current_process: bool,
    ) -> Any:
        del _binding, _reference, expected_previous_history_root_sha256
        assert require_current_process is False
        epoch = {1: 5, 2: 6}[expected_ordinal]
        return SimpleNamespace(
            implementation_epoch=epoch,
            implementation_commit=str(epoch) * 40,
            owner_surface_authorization=owner,
            independent_implementation_review=review,
            control_merkle_root_sha256=str(epoch + 5) * 64,
            creating_commit=str(epoch + 7) * 40,
        )

    monkeypatch.setattr(implementation, "_current_execution_head", lambda _root: "9" * 40)
    monkeypatch.setattr(implementation, "_validate_action_authorization", authorization)
    monkeypatch.setattr(
        implementation,
        "build_control_surface",
        lambda _root, commit, **_kwargs: SimpleNamespace(
            merkle_root_sha256=str(int(commit[0]) + 5) * 64
        ),
    )
    monkeypatch.setattr(implementation, "_git_is_ancestor", lambda *_args: True)
    observed = implementation._implementation_epochs(binding, history)
    assert [row["epoch"] for row in observed] == [5, 6]
    assert all(row["epoch"] != 1 for row in observed)
    assert [(row["first_attempt_ordinal"], row["last_attempt_ordinal"]) for row in observed] == [
        (1, 1),
        (2, 2),
    ]

    gap_history = SimpleNamespace(
        records=(
            records[0],
            SimpleNamespace(
                **{
                    **vars(records[1]),
                    "implementation_epoch": 7,
                }
            ),
        ),
        selected_attempt_ordinal=2,
    )
    with pytest.raises(
        implementation.RehearsalV22Error,
        match="epoch keys do not start at 5 and remain contiguous",
    ):
        implementation._implementation_epochs(binding, gap_history)


def test_void_epoch_one_is_schema_compatible_and_does_not_consume_ordinal_one() -> None:
    validator = _validator_module()
    void_epoch = {
        "epoch": 1,
        "implementation_commit": validator.VOID_EPOCH_ONE_IMPLEMENTATION_COMMIT,
        "owner_exact_surface_authorization": {
            "path": validator.INDEPENDENT_REVIEW_RELATIVE.as_posix(),
            "sha256": validator.INDEPENDENT_REVIEW_SHA256,
            "creating_commit": validator.INDEPENDENT_REVIEW_COMMIT,
            "unique_a_history_verified": True,
        },
        "independent_implementation_review": {
            "path": validator.VOID_EPOCH_ONE_ADJUDICATION_RELATIVE.as_posix(),
            "sha256": validator.VOID_EPOCH_ONE_ADJUDICATION_SHA256,
            "creating_commit": validator.VOID_EPOCH_ONE_ADJUDICATION_COMMIT,
            "unique_a_history_verified": True,
        },
        "control_merkle_root_sha256": "1" * 64,
        "first_attempt_ordinal": 1,
        "last_attempt_ordinal": 1,
        "all_attempts_authorized": True,
    }
    epoch_two = {
        "epoch": 2,
        "implementation_commit": "2" * 40,
        "owner_exact_surface_authorization": {
            "path": "docs/phase4/reports/epoch-2-owner.json",
            "sha256": "3" * 64,
            "creating_commit": "4" * 40,
            "unique_a_history_verified": True,
        },
        "independent_implementation_review": {
            "path": "docs/phase4/reports/epoch-2-review.json",
            "sha256": "5" * 64,
            "creating_commit": "6" * 40,
            "unique_a_history_verified": True,
        },
        "control_merkle_root_sha256": "7" * 64,
        "first_attempt_ordinal": 1,
        "last_attempt_ordinal": 2,
        "all_attempts_authorized": True,
    }
    observed = validator._epoch_map({"implementation_epochs": [void_epoch, epoch_two]})
    assert list(observed) == [1, 2]
    assert validator._is_void_epoch_one(observed[1]) is True
    assert observed[2]["first_attempt_ordinal"] == 1

    bundle_schema, release_schema = _schemas()
    bundle_epoch_schema = {
        "$schema": bundle_schema["$schema"],
        "$defs": bundle_schema["$defs"],
        "$ref": "#/$defs/implementationEpoch",
    }
    assert not list(Draft202012Validator(bundle_epoch_schema).iter_errors(void_epoch))
    release_void = {
        "epoch": void_epoch["epoch"],
        "implementation_commit": void_epoch["implementation_commit"],
        "owner_surface_authorization": void_epoch["owner_exact_surface_authorization"],
        "independent_implementation_review": void_epoch["independent_implementation_review"],
        "control_merkle_root_sha256": void_epoch["control_merkle_root_sha256"],
        "first_attempt_ordinal": void_epoch["first_attempt_ordinal"],
        "last_attempt_ordinal": void_epoch["last_attempt_ordinal"],
    }
    release_epoch_schema = {
        "$schema": release_schema["$schema"],
        "$defs": release_schema["$defs"],
        "$ref": "#/$defs/implementationEpoch",
    }
    assert not list(Draft202012Validator(release_epoch_schema).iter_errors(release_void))

    without_void = copy.deepcopy(epoch_two)
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match="not contiguous",
    ):
        validator._epoch_map({"implementation_epochs": [without_void]})
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match="lacks an executed epoch 2",
    ):
        validator._epoch_map({"implementation_epochs": [void_epoch]})
    tampered = copy.deepcopy(void_epoch)
    tampered["independent_implementation_review"]["sha256"] = "0" * 64
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match="attempt intervals have a gap",
    ):
        validator._epoch_map({"implementation_epochs": [tampered, epoch_two]})


def test_fixed_void_epoch_one_real_lineage_round_trips_without_heavy_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    execution_head = validator._git_bytes(PROJECT_ROOT, "rev-parse", "HEAD").decode().strip()
    calls: list[tuple[Path, str, bool]] = []

    def control(root: Path, commit: str, *, require_current: bool) -> Any:
        calls.append((root, commit, require_current))
        return SimpleNamespace(
            implementation_commit=commit,
            merkle_root_sha256="c" * 64,
            loaded_repository_sources=(),
            ast_closure_paths=(),
            records=(),
        )

    # The development checkout is a linked worktree.  Only the producer's
    # standalone-.git guard is bypassed; every fixed Git object, parent, diff,
    # unique-A authority, blob SHA, ruling, and landing relation is still read.
    monkeypatch.setattr(implementation, "_validate_git_metadata_authority", lambda _root: None)
    monkeypatch.setattr(implementation, "build_control_surface", control)
    before = _all_real_path_fingerprints()
    epoch = implementation._void_epoch_one(
        SimpleNamespace(project_root=PROJECT_ROOT),
        execution_head=execution_head,
    )
    validator._validate_void_epoch_one(
        project_root=PROJECT_ROOT,
        epoch=epoch,
        execution_head=execution_head,
    )
    assert epoch == {
        "epoch": 1,
        "implementation_commit": validator.VOID_EPOCH_ONE_IMPLEMENTATION_COMMIT,
        "owner_exact_surface_authorization": _initial_sibling_reference(),
        "independent_implementation_review": {
            "path": validator.VOID_EPOCH_ONE_ADJUDICATION_RELATIVE.as_posix(),
            "sha256": validator.VOID_EPOCH_ONE_ADJUDICATION_SHA256,
            "creating_commit": validator.VOID_EPOCH_ONE_ADJUDICATION_COMMIT,
            "unique_a_history_verified": True,
        },
        "control_merkle_root_sha256": "c" * 64,
        "first_attempt_ordinal": 1,
        "last_attempt_ordinal": 1,
        "all_attempts_authorized": True,
    }
    assert calls == [
        (PROJECT_ROOT, validator.VOID_EPOCH_ONE_IMPLEMENTATION_COMMIT, False),
        (PROJECT_ROOT, validator.VOID_EPOCH_ONE_IMPLEMENTATION_COMMIT, False),
    ]
    assert _all_real_path_fingerprints() == before


def test_landed_epoch_two_review_projection_is_preserved_and_globally_double_seen() -> None:
    validator = _validator_module()
    implementation_commit = "1b4e05c6acd513bb1bc11245911da97b6a128ca1"
    reference = {
        "path": (
            "docs/phase4/reports/P4.2a-v2-2-epoch2-implementation-independent-review-20260813.json"
        ),
        "sha256": "3805054e3369eff725c4aa1ffe60f565a4571063aa7465b17be401879a19fa2b",
        "creating_commit": "0364079d8d7660e26eb67665bd3afa82c17c334c",
        "unique_a_history_verified": True,
    }
    execution_head = validator._git_bytes(PROJECT_ROOT, "rev-parse", "HEAD").decode().strip()
    before = _all_real_path_fingerprints()
    payload = validator._validate_implementation_review_authority(
        PROJECT_ROOT,
        reference,
        implementation_commit=implementation_commit,
        execution_head=execution_head,
        require_worktree=True,
    )
    assert hashlib.sha256(payload).hexdigest() == reference["sha256"]
    global_history = validator._git_bytes(
        PROJECT_ROOT,
        "log",
        "--all",
        "--diff-merges=first-parent",
        "--format=@@%H",
        "--name-status",
        "--find-renames",
        "--find-copies",
        "--",
        reference["path"],
    ).decode("utf-8")
    assert global_history.count(f"A\t{reference['path']}") == 2
    assert f"@@{reference['creating_commit']}" in global_history
    assert "@@d15bea522cfd892f6837772a2142b903e03a9436" in global_history
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match="non-source non-projection Git touch",
    ):
        validator._unique_a_authority(
            PROJECT_ROOT,
            reference,
            require_worktree=True,
        )
    drifted = copy.deepcopy(reference)
    drifted["sha256"] = "0" * 64
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match="bytes or SHA drifted",
    ):
        validator._validate_implementation_review_authority(
            PROJECT_ROOT,
            drifted,
            implementation_commit=implementation_commit,
            execution_head=execution_head,
            require_worktree=True,
        )
    assert _all_real_path_fingerprints() == before


def test_direct_implementation_review_requires_an_exact_one_file_creation(
    tmp_path: Path,
) -> None:
    validator = _validator_module()
    root = tmp_path / "direct-implementation-review"
    root.mkdir(mode=0o700)
    _fixture_git(validator, root, "init", "--quiet")
    (root / "implementation.py").write_bytes(b"IMPLEMENTED = True\n")
    _fixture_git(validator, root, "add", "--all")
    _fixture_git(validator, root, "commit", "--quiet", "-m", "implementation")
    implementation_commit = (
        _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")
    )

    relative = Path("docs/phase4/reports/implementation-review.json")
    review_path = root / relative
    review_path.parent.mkdir(parents=True)
    payload = b'{"blockers":[],"verdict":"APPROVE_IMPLEMENTATION"}\n'
    review_path.write_bytes(payload)
    _fixture_git(validator, root, "add", "--all")
    _fixture_git(validator, root, "commit", "--quiet", "-m", "review")
    creating_commit = _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")
    reference = {
        "path": relative.as_posix(),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "creating_commit": creating_commit,
        "unique_a_history_verified": True,
    }
    assert (
        validator._validate_implementation_review_authority(
            root,
            reference,
            implementation_commit=implementation_commit,
            execution_head=creating_commit,
            require_worktree=True,
        )
        == payload
    )

    (root / "unapproved-extra.txt").write_bytes(b"extra\n")
    _fixture_git(validator, root, "add", "--all")
    _fixture_git(validator, root, "commit", "--quiet", "--amend", "--no-edit")
    amended_commit = _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")
    drifted_reference = {**reference, "creating_commit": amended_commit}
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match="direct implementation review creation surface drifted",
    ):
        validator._validate_implementation_review_authority(
            root,
            drifted_reference,
            implementation_commit=implementation_commit,
            execution_head=amended_commit,
            require_worktree=True,
        )


@pytest.mark.parametrize("selected_epoch", (2, 3))
def test_series_2_release_cross_validation_rejects_legacy_void_epoch_rows(
    selected_epoch: int,
) -> None:
    validator = _validator_module()
    bundle, receipt = _minimal_cross_valid_bundle_receipt()
    base_epoch = copy.deepcopy(bundle["implementation_epochs"][0])
    void_epoch = {
        "epoch": 1,
        "implementation_commit": validator.VOID_EPOCH_ONE_IMPLEMENTATION_COMMIT,
        "owner_exact_surface_authorization": _initial_sibling_reference(),
        "independent_implementation_review": {
            "path": validator.VOID_EPOCH_ONE_ADJUDICATION_RELATIVE.as_posix(),
            "sha256": validator.VOID_EPOCH_ONE_ADJUDICATION_SHA256,
            "creating_commit": validator.VOID_EPOCH_ONE_ADJUDICATION_COMMIT,
            "unique_a_history_verified": True,
        },
        "control_merkle_root_sha256": "0" * 64,
        "first_attempt_ordinal": 1,
        "last_attempt_ordinal": 1,
        "all_attempts_authorized": True,
    }
    epoch_two = {
        **base_epoch,
        "epoch": 2,
        "first_attempt_ordinal": 1,
        "last_attempt_ordinal": 3 if selected_epoch == 2 else 1,
    }
    bundle_epochs = [void_epoch, epoch_two]
    if selected_epoch == 3:
        bundle_epochs.append(
            {
                **copy.deepcopy(base_epoch),
                "epoch": 3,
                "implementation_commit": "a" * 40,
                "first_attempt_ordinal": 2,
                "last_attempt_ordinal": 3,
            }
        )
    bundle["implementation_epochs"] = bundle_epochs
    record_epochs = (2, 2, 2) if selected_epoch == 2 else (2, 3, 3)
    for record, epoch_number in zip(
        bundle["attempt_history"]["records"], record_epochs, strict=True
    ):
        record["implementation_epoch"] = epoch_number
    for outcome, epoch_number in zip(
        receipt["owner_authorization"]["acknowledged_outcomes"],
        record_epochs,
        strict=True,
    ):
        outcome["implementation_epoch"] = epoch_number
    receipt["implementation_epochs"] = [
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
    ]
    selected_commit = bundle_epochs[selected_epoch - 1]["implementation_commit"]
    receipt["lineage"]["selected_implementation_commit"] = selected_commit
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"explicit keys",
    ):
        validator._cross_validate_release(bundle=bundle, receipt=receipt)


@pytest.mark.parametrize(
    "mutation",
    (
        "float-to-int",
        "allowed-pointer-missing",
        "allowed-pointer-extra",
        "bundle-delta-missing",
        "bundle-delta-extra",
        "bundle-unlisted-drift",
        "release-unlisted-drift",
    ),
)
def test_active_inheritance_gate_rejects_typed_pointer_or_unlisted_schema_drift(
    mutation: str,
) -> None:
    validator = _validator_module()
    before = _all_real_path_fingerprints()
    preregistration = _preregistration()
    bundle, release = _schemas()
    inheritance = preregistration["contract_inheritance"]
    if mutation == "float-to-int":
        snapshot = inheritance["strict_inheritance_snapshot"]
        assert type(snapshot["request_interval_contract"]["min_start_to_start_seconds"]) is float
        snapshot["request_interval_contract"]["min_start_to_start_seconds"] = 1
    elif mutation == "allowed-pointer-missing":
        inheritance["allowed_v2_2_delta_json_pointers"].pop()
    elif mutation == "allowed-pointer-extra":
        inheritance["allowed_v2_2_delta_json_pointers"].append("/not-registered")
    elif mutation == "bundle-delta-missing":
        inheritance["bundle_schema_delta_domains"].pop()
    elif mutation == "bundle-delta-extra":
        inheritance["bundle_schema_delta_domains"].append("/additionalProperties")
    elif mutation == "bundle-unlisted-drift":
        bundle["additionalProperties"] = True
    else:
        release["additionalProperties"] = True
    preregistration_payload = (
        json.dumps(
            preregistration,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    bundle_payload = (
        json.dumps(
            bundle,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    release_payload = (
        json.dumps(
            release,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"inheritance|delta|schema|pointer|snapshot",
    ):
        validator._validate_contract_inheritance(
            project_root=PROJECT_ROOT,
            preregistration_payload=preregistration_payload,
            bundle_schema_payload=bundle_payload,
            release_schema_payload=release_payload,
        )
    assert _all_real_path_fingerprints() == before


def test_independent_validator_recomputes_every_series_hash_domain() -> None:
    validator = _validator_module()
    series_token = "1" * 64
    implementation_commit = "2" * 40
    previous = hashlib.sha256(b"p4.2a-rehearsal-v2.2-history-empty-v1\0").hexdigest()
    expected_token = hashlib.sha256(
        b"p4.2a-rehearsal-v2.2-attempt-v1\0"
        + bytes.fromhex(series_token)
        + (1).to_bytes(8, "big")
        + bytes.fromhex(implementation_commit)
        + bytes.fromhex(previous)
    ).hexdigest()
    assert validator._history_empty_root() == previous
    assert (
        validator._attempt_token(
            series_token=series_token,
            ordinal=1,
            implementation_commit=implementation_commit,
            previous_history_root=previous,
        )
        == expected_token
    )
    started_sha = "3" * 64
    evidence_root = validator._evidence_root({})
    expected_record = hashlib.sha256(
        b"p4.2a-rehearsal-v2.2-attempt-record-v1\0"
        + (1).to_bytes(8, "big")
        + bytes.fromhex(expected_token)
        + bytes.fromhex(started_sha)
        + bytes(32)
        + bytes(32)
        + bytes.fromhex(evidence_root)
    ).hexdigest()
    assert (
        validator._attempt_record_root(
            ordinal=1,
            attempt_token=expected_token,
            started_sha256=started_sha,
            candidate_sha256=None,
            terminal_sha256=None,
            evidence_tree_root=evidence_root,
        )
        == expected_record
    )
    expected_history = hashlib.sha256(
        b"p4.2a-rehearsal-v2.2-history-step-v1\0"
        + bytes.fromhex(previous)
        + bytes.fromhex(expected_record)
    ).hexdigest()
    assert validator._history_step(previous, expected_record) == expected_history
    assert (
        validator._command_sha256(("python", "-S", "shim.py"))
        == hashlib.sha256(b"p4.2a-v2.2-argv-v1\0python\0-S\0shim.py").hexdigest()
    )
    assert (
        validator._environment_sha256({"B": "2", "A": "1"})
        == hashlib.sha256(b"p4.2a-v2.2-env-v1\0" + b"A\0" + b"1\0" + b"B\0" + b"2\0").hexdigest()
    )


@pytest.mark.parametrize(
    "mutate",
    (
        lambda record: {**record, "extra": 1},
        lambda record: {key: value for key, value in record.items() if key != "ordinal"},
        lambda record: {**record, "ordinal": "1"},
        lambda record: {**record, "outcome": "PASS"},
    ),
    ids=("extra", "missing", "type", "outcome"),
)
def test_bundle_attempt_record_schema_rejects_shape_drift(
    mutate: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    bundle, _release = _schemas()
    validator = _definition_validator(bundle, "attemptRecord")
    base: dict[str, Any] = {
        "ordinal": 1,
        "attempt_token_sha256": "1" * 64,
        "previous_history_root_sha256": "2" * 64,
        "started": {
            "live_relative_path": "attempts/000001/started.json",
            "archive_relative_path": "archive/attempt-history/attempts/000001/started.json",
            "bytes": 1,
            "sha256": "3" * 64,
        },
        "candidate": None,
        "terminal": None,
        "outcome": "INCOMPLETE_UNTERMINALIZED",
        "reached_stage": "started",
        "implementation_epoch": 1,
        "implementation_commit": "4" * 40,
        "owner_action_time_authorization": {
            "authority": {
                "path": "docs/phase4/reports/synthetic.json",
                "sha256": "5" * 64,
                "creating_commit": "6" * 40,
                "unique_a_history_verified": True,
            },
            "archive_relative_path": (
                "archive/attempt-history/attempts/000001/action-time-authorization.json"
            ),
            "bytes": 1,
            "archive_sha256": "5" * 64,
            "source_and_archive_bytes_equal": True,
        },
        "command_sha256": "7" * 64,
        "environment_sha256": "8" * 64,
        "automatic_retry_count": 0,
        "artifact_inventory": [],
        "error": None,
        "evidence_tree_root_sha256": "9" * 64,
        "record_root_sha256": "a" * 64,
    }
    assert list(validator.iter_errors(base)) == []
    assert list(validator.iter_errors(mutate(base))) != []


def test_git_optional_blob_accepts_present_regular_blob_and_absent_path(
    optional_blob_git_repository: dict[str, Any],
) -> None:
    validator = _validator_module()
    root = optional_blob_git_repository["root"]
    commit = optional_blob_git_repository["head"]
    assert validator._git_optional_blob(root, commit, "regular.py") == b"REGULAR = True\n"
    assert validator._git_optional_blob(root, commit, "absent.py") is None


@pytest.mark.parametrize(
    "relative",
    ("executable.py", "link.py", "tree", "gitlink"),
    ids=("executable", "symlink", "tree", "submodule"),
)
def test_git_optional_blob_rejects_every_non_100644_git_entry(
    optional_blob_git_repository: dict[str, Any],
    relative: str,
) -> None:
    validator = _validator_module()
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match="not one exact regular Git blob",
    ):
        validator._git_optional_blob(
            optional_blob_git_repository["root"],
            optional_blob_git_repository["head"],
            relative,
        )


@pytest.mark.parametrize(
    ("commit_key", "relative", "message"),
    (
        ("duplicate_commit", "duplicate.py", "multiple Git tree records"),
        ("malformed_commit", "malformed.py", "hardened Git ls-tree.*failed"),
    ),
    ids=("duplicate-path-ambiguity", "malformed-mode"),
)
def test_git_optional_blob_rejects_genuine_malformed_or_ambiguous_git_trees(
    optional_blob_git_repository: dict[str, Any],
    commit_key: str,
    relative: str,
    message: str,
) -> None:
    validator = _validator_module()
    with pytest.raises(validator.RehearsalV22ValidationError, match=message):
        validator._git_optional_blob(
            optional_blob_git_repository["root"],
            optional_blob_git_repository[commit_key],
            relative,
        )


@pytest.mark.parametrize(
    "relative",
    ("../regular.py", "/regular.py", "regular.py/", "regular//file.py", ":(glob)*"),
    ids=("parent", "absolute", "trailing-slash", "double-slash", "pathspec-magic"),
)
def test_git_optional_blob_rejects_malformed_or_pathspec_ambiguous_paths(
    optional_blob_git_repository: dict[str, Any],
    relative: str,
) -> None:
    validator = _validator_module()
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match="safe relative POSIX path",
    ):
        validator._git_optional_blob(
            optional_blob_git_repository["root"],
            optional_blob_git_repository["head"],
            relative,
        )


def test_git_optional_blob_uses_strict_nul_ls_tree_without_subprocess_stdin() -> None:
    validator = _validator_module()
    tree = ast.parse(inspect.getsource(validator._git_optional_blob))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_git_bytes"
    ]
    ls_tree_calls = [
        call
        for call in calls
        if len(call.args) >= 2
        and isinstance(call.args[1], ast.Constant)
        and call.args[1].value == "ls-tree"
    ]
    assert len(ls_tree_calls) == 1
    ls_tree = ls_tree_calls[0]
    assert [
        argument.value if isinstance(argument, ast.Constant) else None
        for argument in ls_tree.args[1:4]
    ] == ["ls-tree", "-z", "--full-tree"]
    assert isinstance(ls_tree.args[4], ast.Name) and ls_tree.args[4].id == "commit"
    assert isinstance(ls_tree.args[5], ast.Constant) and ls_tree.args[5].value == "--"
    assert isinstance(ls_tree.args[6], ast.Name) and ls_tree.args[6].id == "path"
    assert all(
        keyword.arg not in {"input", "stdin"}
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
    )
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr == "PIPE"
        and isinstance(node.value, ast.Name)
        and node.value.id == "subprocess"
        for node in ast.walk(tree)
    )


def test_validator_standalone_ambient_entry_rejects_without_real_path_effect() -> None:
    preregistration = _preregistration()
    exact_os = preregistration["exact_os_bootstrap_contract"]
    launcher = Path(str(exact_os["python_launcher_path"]))
    before = _all_real_path_fingerprints()
    completed = subprocess.run(
        [launcher.as_posix(), (PROJECT_ROOT / VALIDATOR_RELATIVE).as_posix()],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": PROJECT_ROOT.as_posix()},
    )
    assert completed.returncode != 0
    assert _all_real_path_fingerprints() == before


def test_evidence_acceptance_cannot_match_the_existing_v2_1_production_gate() -> None:
    from scripts import prepare_p4_2a_v2_heldout as prepare

    v2_1_payload = (PROJECT_ROOT / prepare.SUCCESSOR_V2_1_RELEASE_SCHEMA_PATH).read_bytes()
    v2_1_schema = json.loads(v2_1_payload)
    assert hashlib.sha256(v2_1_payload).hexdigest() == (
        prepare.SUCCESSOR_V2_1_RELEASE_SCHEMA_SHA256
    )
    assert v2_1_schema["properties"]["schema_version"]["const"] == (
        "p4.2a-v2-heldout-rehearsal-v2-1-release-authorization-v1"
    )
    _bundle, release = _schemas()
    v2_2_schema_version = release["properties"]["schema_version"]["const"]
    assert v2_2_schema_version == ("p4.2a-v2-heldout-rehearsal-v2-2-evidence-acceptance-v1")
    assert v2_2_schema_version != v2_1_schema["properties"]["schema_version"]["const"]


def test_schema_only_never_substitutes_for_active_history_replay() -> None:
    validator = _validator_module()
    source = inspect.getsource(validator.validate_bundle)
    forbidden_shortcuts = (
        "return Draft202012Validator",
        "return _schema_validate",
        "bundle.get('complete_attempt_history_rehash_passed')",
        'bundle.get("complete_attempt_history_rehash_passed")',
    )
    assert not any(shortcut in source for shortcut in forbidden_shortcuts)
    preregistration = _preregistration()
    required = preregistration["required_negative_tests"]
    assert "live ledger and bundle history mismatch rejected" in required
    assert "release acknowledgement omitting or changing one outcome rejected" in required


def _assert_initial_sibling_baseline_pass(
    validator: Any,
    repository: dict[str, Any],
) -> bytes:
    before = _all_real_path_fingerprints()
    payload = validator._validate_initial_sibling_authority(
        repository["root"],
        _initial_sibling_reference(),
        execution_head=repository["execution_head"],
    )
    assert hashlib.sha256(payload).hexdigest() == INITIAL_SIBLING_SHA256
    assert _all_real_path_fingerprints() == before
    return payload


def test_initial_sibling_merge_projection_is_valid_for_generic_lineage_census(
    initial_sibling_git_repository: dict[str, Any],
) -> None:
    validator = _validator_module()
    root = initial_sibling_git_repository["root"]
    payload = _assert_initial_sibling_baseline_pass(
        validator,
        initial_sibling_git_repository,
    )
    assert payload == _fixture_git(
        validator,
        root,
        "show",
        f"{INITIAL_SIBLING_COMMIT}:{INITIAL_SIBLING_PATH.as_posix()}",
    )
    history = _fixture_git(
        validator,
        root,
        "log",
        "--all",
        "--diff-merges=first-parent",
        "--format=@@%H",
        "--name-status",
        "--find-renames",
        "--find-copies",
        "--",
        INITIAL_SIBLING_PATH.as_posix(),
    ).decode("utf-8")
    assert history.count(f"A\t{INITIAL_SIBLING_PATH.as_posix()}") == 2
    assert f"@@{INITIAL_SIBLING_COMMIT}" in history
    assert (
        validator._unique_a_authority(
            root,
            _initial_sibling_reference(),
            require_worktree=True,
        )
        == payload
    )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("path", "docs/phase4/reports/not-the-fixed-sibling.json"),
        ("sha256", "0" * 64),
        ("creating_commit", "0" * 40),
        ("unique_a_history_verified", False),
    ),
)
def test_initial_sibling_rejects_any_substituted_reference_after_baseline_pass(
    initial_sibling_git_repository: dict[str, Any],
    field: str,
    replacement: object,
) -> None:
    validator = _validator_module()
    _assert_initial_sibling_baseline_pass(validator, initial_sibling_git_repository)
    reference = _initial_sibling_reference()
    reference[field] = replacement
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"initial sibling authority reference",
    ):
        validator._validate_initial_sibling_authority(
            initial_sibling_git_repository["root"],
            reference,
            execution_head=initial_sibling_git_repository["execution_head"],
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "not-execution-head-ancestor",
        "descendant-modify",
        "descendant-delete",
        "non-descendant-duplicate-same-bytes",
        "non-descendant-duplicate-different-bytes",
        "merge-result-drift",
    ),
)
def test_initial_sibling_graph_rejects_lineage_or_path_reintroduction_after_baseline_pass(
    initial_sibling_git_repository: dict[str, Any],
    mutation: str,
) -> None:
    validator = _validator_module()
    root = initial_sibling_git_repository["root"]
    payload = _assert_initial_sibling_baseline_pass(
        validator,
        initial_sibling_git_repository,
    )
    execution_head = initial_sibling_git_repository["execution_head"]
    mutation_commit: str | None = None
    expected = r"initial sibling authority"

    if mutation == "not-execution-head-ancestor":
        _fixture_git(
            validator,
            root,
            "checkout",
            "--quiet",
            "-b",
            "non-descendant-head",
            INITIAL_SIBLING_PARENT,
        )
        unrelated = root / "non-descendant-head.txt"
        unrelated.write_bytes(b"not a b21 descendant\n")
        _fixture_git(validator, root, "add", "--", unrelated.name)
        _fixture_git(validator, root, "commit", "--quiet", "-m", mutation)
        execution_head = _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")
        expected = r"outside the execution-head lineage"
    elif mutation in {"descendant-modify", "descendant-delete"}:
        target = root / INITIAL_SIBLING_PATH
        if mutation == "descendant-modify":
            target.write_bytes(payload + b"drift\n")
            _fixture_git(validator, root, "add", "--", INITIAL_SIBLING_PATH.as_posix())
        else:
            _fixture_git(validator, root, "rm", "--quiet", "--", INITIAL_SIBLING_PATH.as_posix())
        _fixture_git(validator, root, "commit", "--quiet", "-m", mutation)
        execution_head = _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")
        mutation_commit = execution_head
        expected = r"authority has a non-source non-projection Git touch"
    elif mutation.startswith("non-descendant-duplicate"):
        _fixture_git(
            validator,
            root,
            "checkout",
            "--quiet",
            "-b",
            "duplicate-authority",
            INITIAL_SIBLING_PARENT,
        )
        target = root / INITIAL_SIBLING_PATH
        target.write_bytes(
            payload
            if mutation.endswith("same-bytes")
            else b'{"forged":"different sibling authority"}\n'
        )
        _fixture_git(validator, root, "add", "--", INITIAL_SIBLING_PATH.as_posix())
        _fixture_git(validator, root, "commit", "--quiet", "-m", mutation)
        mutation_commit = _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")
        _fixture_git(validator, root, "checkout", "--quiet", "synthetic-series")
        expected = r"authority has a non-source non-projection Git touch"
    else:
        _fixture_git(
            validator,
            root,
            "checkout",
            "--quiet",
            "-b",
            "merge-result-side",
            INITIAL_SIBLING_PARENT,
        )
        side = root / "merge-result-side.txt"
        side.write_bytes(b"side branch\n")
        _fixture_git(validator, root, "add", "--", side.name)
        _fixture_git(validator, root, "commit", "--quiet", "-m", "side branch")
        side_commit = _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")
        _fixture_git(validator, root, "checkout", "--quiet", "synthetic-series")
        (root / INITIAL_SIBLING_PATH).write_bytes(payload + b"merge drift\n")
        side.write_bytes(b"side branch\n")
        _fixture_git(
            validator,
            root,
            "add",
            "--",
            INITIAL_SIBLING_PATH.as_posix(),
            side.name,
        )
        tree = _fixture_git(validator, root, "write-tree").strip().decode("ascii")
        execution_head = (
            _fixture_git(
                validator,
                root,
                "commit-tree",
                tree,
                "-p",
                execution_head,
                "-p",
                side_commit,
                "-m",
                mutation,
            )
            .strip()
            .decode("ascii")
        )
        _fixture_git(
            validator,
            root,
            "reset",
            "--quiet",
            "--hard",
            execution_head,
        )
        mutation_commit = execution_head
        expected = r"authority has a non-source non-projection Git touch"

    if mutation_commit is not None:
        expected_status = {
            "descendant-modify": "M",
            "descendant-delete": "D",
            "non-descendant-duplicate-same-bytes": "A",
            "non-descendant-duplicate-different-bytes": "A",
            "merge-result-drift": "M",
        }[mutation]
        mutation_touches = tuple(
            touch
            for touch in validator._all_ref_path_touches(root, INITIAL_SIBLING_PATH.as_posix())
            if touch[0] == mutation_commit
        )
        assert mutation_touches == (
            (mutation_commit, expected_status, (INITIAL_SIBLING_PATH.as_posix(),)),
        )
        mutation_blob = validator._git_optional_blob(
            root,
            mutation_commit,
            INITIAL_SIBLING_PATH.as_posix(),
        )
        mutation_parents = validator._git_parents(root, mutation_commit)
        if mutation.startswith("descendant-"):
            assert len(mutation_parents) == 1
            assert validator._git_is_ancestor(root, INITIAL_SIBLING_COMMIT, mutation_commit)
            if mutation.endswith("delete"):
                assert mutation_blob is None
            else:
                assert mutation_blob != payload
        elif mutation.startswith("non-descendant-"):
            assert len(mutation_parents) == 1
            assert not validator._git_is_ancestor(root, INITIAL_SIBLING_COMMIT, mutation_commit)
            if mutation.endswith("same-bytes"):
                assert mutation_blob == payload
            else:
                assert mutation_blob != payload
        else:
            assert len(mutation_parents) == 2
            assert mutation_blob != payload

    with pytest.raises(validator.RehearsalV22ValidationError, match=expected):
        validator._validate_initial_sibling_authority(
            root,
            _initial_sibling_reference(),
            execution_head=execution_head,
        )


@pytest.mark.parametrize(
    "worktree_state",
    ("present-exact", "missing", "bytes-drift", "symlink", "hardlink-alias"),
)
def test_initial_sibling_worktree_is_optional_but_any_present_entry_is_exact_regular(
    initial_sibling_git_repository: dict[str, Any],
    tmp_path: Path,
    worktree_state: str,
) -> None:
    validator = _validator_module()
    root = initial_sibling_git_repository["root"]
    payload = _assert_initial_sibling_baseline_pass(
        validator,
        initial_sibling_git_repository,
    )
    target = root / INITIAL_SIBLING_PATH
    if worktree_state != "present-exact":
        target.unlink()
    if worktree_state == "bytes-drift":
        target.write_bytes(payload + b"worktree drift\n")
    elif worktree_state == "symlink":
        source = tmp_path / "symlink-source.json"
        source.write_bytes(payload)
        target.symlink_to(source)
    elif worktree_state == "hardlink-alias":
        source = tmp_path / "hardlink-source.json"
        source.write_bytes(payload)
        os.link(source, target)

    if worktree_state in {"present-exact", "missing"}:
        assert (
            validator._validate_initial_sibling_authority(
                root,
                _initial_sibling_reference(),
                execution_head=initial_sibling_git_repository["execution_head"],
            )
            == payload
        )
    else:
        with pytest.raises(
            validator.RehearsalV22ValidationError,
            match=r"worktree|symlink|alias|regular|bytes",
        ):
            validator._validate_initial_sibling_authority(
                root,
                _initial_sibling_reference(),
                execution_head=initial_sibling_git_repository["execution_head"],
            )


def test_initial_sibling_fixed_parent_diff_blob_and_callsite_partition_are_explicit() -> None:
    validator = _validator_module()
    helper_source = inspect.getsource(validator._validate_initial_sibling_authority)
    helper_tree = ast.parse(helper_source)
    helper_calls = [node for node in ast.walk(helper_tree) if isinstance(node, ast.Call)]

    def exact_call(name: str, second_argument: str) -> list[ast.Call]:
        return [
            node
            for node in helper_calls
            if isinstance(node.func, ast.Name)
            and node.func.id == name
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Name)
            and node.args[1].id == second_argument
            and any(
                keyword.arg == "work_tracker"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "tracker"
                for keyword in node.keywords
            )
        ]

    assert len(exact_call("_git_parents", "INDEPENDENT_REVIEW_COMMIT")) == 1
    assert "(INITIAL_REVIEWED_COMMIT,)" in helper_source
    assert "_diff_name_status(" in helper_source
    assert "INITIAL_REVIEWED_COMMIT," in helper_source
    assert "INDEPENDENT_REVIEW_COMMIT," in helper_source
    assert '(("A", path),)' in helper_source
    assert len(exact_call("_git_blob", "INDEPENDENT_REVIEW_COMMIT")) == 1
    assert "_sha256(payload) != INDEPENDENT_REVIEW_SHA256" in helper_source
    assert any(
        isinstance(node.func, ast.Name)
        and node.func.id == "_git_optional_blob"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Name)
        and node.args[1].id == "head"
        and any(keyword.arg == "work_tracker" for keyword in node.keywords)
        for node in helper_calls
    )

    control_source = inspect.getsource(validator._validate_control_archive)
    assert "if relative == INDEPENDENT_REVIEW_RELATIVE.as_posix():" in control_source
    assert "creating_payload = _validate_initial_sibling_authority(" in control_source
    assert "else:\n                    creating_payload = _unique_a_authority(" in control_source

    epoch_source = inspect.getsource(validator._validate_implementation_epochs)
    assert "_epoch_map(bundle, epoch_origin=SERIES_2_EPOCH_ORIGIN)" in epoch_source
    assert "for epoch_number, epoch in epochs.items():" in epoch_source
    assert "epoch=epoch_number" in epoch_source
    assert "replay.selected_implementation_epoch - 1" not in epoch_source
    assert "_validate_initial_sibling_authority(" not in epoch_source
    assert "_validate_void_epoch_one(" not in epoch_source
    assert "_unique_a_authority(" in epoch_source
    assert "_validate_implementation_review_authority(" in epoch_source


def test_generic_authority_keeps_unique_a_semantics_for_non_sibling_paths(
    initial_sibling_git_repository: dict[str, Any],
) -> None:
    validator = _validator_module()
    root = initial_sibling_git_repository["root"]
    _assert_initial_sibling_baseline_pass(validator, initial_sibling_git_repository)
    relative = Path("docs/phase4/reports/generic-authority.json")
    payload = b'{"authority":"generic"}\n'
    creating_commit = _fixture_commit_file(validator, root, relative, payload)
    reference = {
        "path": relative.as_posix(),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "creating_commit": creating_commit,
        "unique_a_history_verified": True,
    }
    assert validator._unique_a_authority(root, reference, require_worktree=True) == payload
    _fixture_commit_file(validator, root, relative, payload + b"drift\n")
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"non-source non-projection|authority",
    ):
        validator._unique_a_authority(root, reference, require_worktree=True)


def _series_2_epoch_row(
    epoch: int,
    *,
    first: int,
    last: int,
) -> dict[str, Any]:
    return {
        "epoch": epoch,
        "implementation_commit": f"{epoch:040x}",
        "owner_exact_surface_authorization": {
            "path": f"docs/phase4/reports/epoch-{epoch}-authority.json",
            "sha256": f"{epoch:064x}",
            "creating_commit": f"{epoch + 10:040x}",
            "unique_a_history_verified": True,
        },
        "independent_implementation_review": {
            "path": f"docs/phase4/reports/epoch-{epoch}-review.json",
            "sha256": f"{epoch + 1:064x}",
            "creating_commit": f"{epoch + 20:040x}",
            "unique_a_history_verified": True,
        },
        "control_merkle_root_sha256": f"{epoch + 2:064x}",
        "first_attempt_ordinal": first,
        "last_attempt_ordinal": last,
        "all_attempts_authorized": True,
    }


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _private_file(path: Path, payload: bytes) -> None:
    _private_directory(path.parent)
    path.write_bytes(payload)
    path.chmod(0o600)


def _copy_private_inventory(
    validator: Any,
    source: Any,
    destination: Path,
) -> Any:
    _private_directory(destination)
    for row in source.rows:
        relative = row["relative_path"]
        if relative == ".":
            continue
        target = destination / relative
        if row["kind"] == "directory":
            _private_directory(target)
        else:
            _private_file(target, source.payloads[relative])
    return validator._strict_private_tree_inventory(
        destination,
        label="synthetic secondary snapshot",
    )


def _series_2_mirror_fixture(
    tmp_path: Path,
    *,
    outcome: str = "FAILED",
) -> dict[str, Any]:
    validator = _validator_module()
    project_root = tmp_path / "project"
    primary_container = tmp_path / "primary-series-container"
    ledger = primary_container / "PRIMARY-LEDGER-DO-NOT-DELETE"
    primary_receipts = primary_container / "MIRROR-RECEIPTS-DO-NOT-DELETE"
    secondary_container = tmp_path / "secondary-series-container"
    snapshots = secondary_container / "SEALED-LEDGER-SNAPSHOTS-DO-NOT-DELETE"
    secondary_receipts = secondary_container / "MIRROR-RECEIPTS-DO-NOT-DELETE"
    for path in (
        project_root,
        primary_container,
        ledger,
        primary_receipts,
        secondary_container,
        snapshots,
        secondary_receipts,
        ledger / "attempts",
        ledger / "attempts/000001",
        ledger / "attempts/000001/evidence",
    ):
        _private_directory(path)
    _private_file(ledger / "series.json", b'{"synthetic":"series-2"}\n')
    _private_file(ledger / ".series.lock", b"")
    _private_file(ledger / "attempts/000001/started.json", b'{"started":true}\n')
    if outcome == "CANDIDATE_VALIDATED_AND_SELECTED":
        _private_file(
            ledger / "attempts/000001/candidate.json",
            b'{"selected":true}\n',
        )
    if outcome != "INCOMPLETE_UNTERMINALIZED":
        _private_file(
            ledger / "attempts/000001/terminal.json",
            b'{"terminal":true}\n',
        )
    _private_file(ledger / "attempts/000001/evidence/log.txt", b"failed\n")
    binding = validator.BindingView(
        mode="DISPOSABLE_FULL_SHAPE_TEST",
        project_root=project_root,
        absolute_destination=project_root / validator.REGISTERED_DESTINATION_RELATIVE,
        series_token_sha256="1" * 64,
        ledger_root=ledger,
        primary_series_container=primary_container,
        primary_receipt_root=primary_receipts,
        secondary_series_container=secondary_container,
        secondary_snapshot_root=snapshots,
        secondary_receipt_root=secondary_receipts,
    )
    primary_inventory = validator._strict_private_tree_inventory(
        ledger,
        label="synthetic primary ledger",
    )
    live_root = validator._path_merkle(
        primary_inventory.payloads,
        leaf_domain=b"p4.2a-rehearsal-v2.2-ledger-leaf-v1\0",
    )
    record_root = "2" * 64
    history_root = validator._history_step(
        validator._history_empty_root(),
        record_root,
    )
    record = {
        "ordinal": 1,
        "outcome": outcome,
        "implementation_epoch": 5,
        "record_root_sha256": record_root,
    }
    replay = validator.HistoryReplay(
        records=(record,),
        source_records=(({}, None, {}),),
        started_count=1,
        failed_count=int(outcome == "FAILED"),
        incomplete_count=int(outcome == "INCOMPLETE_UNTERMINALIZED"),
        selected_attempt_ordinal=1,
        selected_implementation_epoch=5,
        selected_implementation_commit="3" * 40,
        history_root_sha256=history_root,
        live_ledger_root_sha256=live_root,
        archive_merkle_root_sha256="4" * 64,
        live_payloads=primary_inventory.payloads,
        live_identities=primary_inventory.identities,
        archive_payloads={},
    )
    snapshot_name = validator._mirror_snapshot_name(1, live_root)
    snapshot = snapshots / snapshot_name
    snapshot_inventory = _copy_private_inventory(
        validator,
        primary_inventory,
        snapshot,
    )
    receipt = {
        "schema_version": validator.MIRROR_RECEIPT_SCHEMA,
        "series_token_sha256": binding.series_token_sha256,
        "ordinal": 1,
        "attempt_outcome": outcome,
        "attempt_sealed": outcome != "INCOMPLETE_UNTERMINALIZED",
        "primary_ledger_root": ledger.as_posix(),
        "secondary_snapshot_root": snapshot.as_posix(),
        "history_root_sha256": history_root,
        "live_ledger_root_sha256": live_root,
        "file_count": snapshot_inventory.file_count,
        "total_bytes": snapshot_inventory.total_bytes,
        "primary_inventory_sha256": primary_inventory.sha256,
        "secondary_inventory_sha256": snapshot_inventory.sha256,
        "second_copy_verified": True,
        "verified_at_utc": validator.FIXED_WALL_CLOCK_TEXT,
    }
    receipt_name = validator._mirror_receipt_filename(1, live_root)
    receipt_payload = validator._canonical_json_bytes(receipt)
    _private_file(primary_receipts / receipt_name, receipt_payload)
    _private_file(secondary_receipts / receipt_name, receipt_payload)
    return {
        "validator": validator,
        "binding": binding,
        "replay": replay,
        "primary_inventory": primary_inventory,
        "snapshot": snapshot,
        "receipt": receipt,
        "receipt_name": receipt_name,
    }


def _apply_series_2_second_copy_fault(
    fixture: dict[str, Any],
    *,
    fault: str,
    tmp_path: Path,
) -> None:
    validator = fixture["validator"]
    binding = fixture["binding"]
    snapshot = fixture["snapshot"]
    primary_receipt = binding.primary_receipt_root / fixture["receipt_name"]
    secondary_receipt = binding.secondary_receipt_root / fixture["receipt_name"]
    if fault == "snapshot-missing":
        shutil.rmtree(snapshot)
    elif fault == "snapshot-extra-file":
        _private_file(snapshot / "unexpected.bin", b"unexpected\n")
    elif fault == "primary-receipt-missing":
        primary_receipt.unlink()
    elif fault == "secondary-receipt-mismatch":
        secondary_receipt.write_bytes(secondary_receipt.read_bytes() + b"drift\n")
    elif fault == "paired-timestamp-substitution":
        receipt = json.loads(primary_receipt.read_bytes())
        receipt["verified_at_utc"] = "2026-08-23T12:00:00Z"
        payload = validator._canonical_json_bytes(receipt)
        primary_receipt.write_bytes(payload)
        secondary_receipt.write_bytes(payload)
    elif fault == "snapshot-mode":
        snapshot.chmod(0o755)
    elif fault == "snapshot-symlink":
        (snapshot / "alias").symlink_to(snapshot / "series.json")
    elif fault == "snapshot-hardlink":
        os.link(snapshot / "series.json", snapshot / "hardlink.json")
    elif fault == "publish-collision-residue":
        residue = binding.secondary_snapshot_root / f".staging-{snapshot.name}-collision"
        residue.mkdir(mode=0o700)
        assert snapshot.is_dir()
    else:  # pragma: no cover - the parameter table is the authority
        raise AssertionError(f"unknown second-copy fault: {fault}")


def _producer_view_of_mirror_fixture(
    fixture: dict[str, Any],
) -> tuple[Any, Any, Any]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator_binding = fixture["binding"]
    action_path = validator_binding.project_root / (
        "docs/phase4/reports/"
        "P4.2a-v2-2-rehearsal-attempt-000002-execution-authorization-20260824.json"
    )
    binding = implementation.ExecutionBinding(
        mode="DISPOSABLE_FULL_SHAPE_TEST",
        project_root=validator_binding.project_root,
        shim_path=validator_binding.project_root / implementation.SHIM_RELATIVE,
        action_authorization_path=action_path,
        destination=validator_binding.absolute_destination,
        series_token_sha256=validator_binding.series_token_sha256,
        ledger_root=validator_binding.ledger_root,
        primary_series_container=validator_binding.primary_series_container,
        primary_receipt_root=validator_binding.primary_receipt_root,
        secondary_series_container=validator_binding.secondary_series_container,
        secondary_snapshot_root=validator_binding.secondary_snapshot_root,
        secondary_receipt_root=validator_binding.secondary_receipt_root,
    )
    replay = fixture["replay"]
    attempt_root = binding.ledger_root / "attempts/000001"
    terminal_path = attempt_root / "terminal.json"
    terminal_payload = terminal_path.read_bytes()
    record = implementation.ValidatedAttemptRecord(
        ordinal=1,
        outcome="FAILED",
        reached_stage="synthetic_registered_test",
        attempt_token_sha256="1" * 64,
        previous_history_root_sha256=implementation._history_empty_root_sha256(),
        implementation_epoch=5,
        implementation_commit="5" * 40,
        owner_action_time_authorization=implementation.AuthorityReference(
            path="docs/phase4/reports/synthetic-attempt-1-authorization.json",
            sha256="6" * 64,
            creating_commit="7" * 40,
        ),
        command_sha256="8" * 64,
        environment_sha256="9" * 64,
        started_path=attempt_root / "started.json",
        started_bytes=(attempt_root / "started.json").read_bytes(),
        started_sha256=hashlib.sha256((attempt_root / "started.json").read_bytes()).hexdigest(),
        candidate_path=None,
        candidate_bytes=None,
        candidate_sha256=None,
        terminal_path=terminal_path,
        terminal_bytes=terminal_payload,
        terminal_sha256=hashlib.sha256(terminal_payload).hexdigest(),
        evidence_tree_root_sha256=implementation._evidence_empty_root_sha256(),
        artifact_inventory=(),
        error={
            "exception_type": "SyntheticFailure",
            "message_sha256": "a" * 64,
            "failing_stage": "synthetic_registered_test",
        },
        record_root_sha256="2" * 64,
        history_root_sha256=replay.history_root_sha256,
    )
    history = implementation.HistoryValidation(
        binding=binding,
        ledger_exists=True,
        records=(record,),
        started_count=1,
        failed_count=1,
        incomplete_count=0,
        validated_candidate_count=0,
        selected_attempt_ordinal=None,
        series_closed=False,
        history_root_sha256=replay.history_root_sha256,
        live_ledger_root_sha256=replay.live_ledger_root_sha256,
        live_file_inventory=tuple(fixture["primary_inventory"].payloads),
    )
    action_payload = implementation._canonical_json_bytes({"synthetic": "ordinal-2"})
    action = implementation.ActionAuthorization(
        path=action_path,
        payload=action_payload,
        sha256=hashlib.sha256(action_payload).hexdigest(),
        creating_commit="b" * 40,
        ordinal=2,
        previous_history_root_sha256=history.history_root_sha256,
        implementation_epoch=5,
        implementation_commit="5" * 40,
        owner_surface_authorization=implementation.AuthorityReference(
            path="docs/phase4/reports/synthetic-epoch-5-authority.json",
            sha256="c" * 64,
            creating_commit="d" * 40,
        ),
        independent_implementation_review=implementation.AuthorityReference(
            path="docs/phase4/reports/synthetic-epoch-5-review.json",
            sha256="e" * 64,
            creating_commit="f" * 40,
        ),
        control_merkle_root_sha256="1" * 64,
        exact_argv=("python", "--execute"),
        command_sha256="2" * 64,
        exact_environment={"PYTHONDONTWRITEBYTECODE": "1"},
        environment_sha256="3" * 64,
    )
    return binding, history, action


@contextmanager
def _validator_kernel_policy_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    label: str,
) -> Any:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    project_root = tmp_path / f"project-{label}"
    project_root.mkdir(mode=0o700)
    binding = implementation._derive_binding_unchecked(
        project_root,
        action_authorization_path=project_root
        / "docs/phase4/reports/synthetic-action-authorization.json",
    )
    write_root = tmp_path / f"descriptor-write-root-{label}"
    native_root = tmp_path / f"native-rename-root-{label}"
    write_root.mkdir(mode=0o700)
    native_root.mkdir(mode=0o700)
    policy = implementation._AuditPolicy(
        project_root=binding.project_root,
        write_roots=(write_root, native_root),
        exact_write_paths=(),
        create_only_roots=(write_root,),
        sqlite_roots=(),
        git_roots=(),
        subprocess_mode="none",
    )
    with monkeypatch.context() as patcher:
        patcher.setattr(
            implementation,
            "_assert_locked_runner_bootstrap",
            lambda _project_root: None,
        )
        with (
            implementation._bootstrap_evidence_scope(
                project_root=binding.project_root,
                shim_path=binding.shim_path,
                argv=tuple(sys.argv),
                orig_argv=tuple(sys.orig_argv),
                environment=dict(os.environ),
            ) as bootstrap,
            implementation._audited_execution(policy, bootstrap=bootstrap),
        ):
            yield implementation, policy, write_root, native_root


def _candidate_without_terminal_archive_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    validator = _validator_module()
    project_root = tmp_path / "project"
    ledger = tmp_path / "primary" / "ledger"
    archive_root = tmp_path / "bundle" / "archive" / "attempt-history"
    for path in (
        project_root,
        ledger,
        ledger / "attempts",
        ledger / "attempts/000001",
        ledger / "attempts/000001/evidence",
        ledger / "attempts/000002",
        ledger / "attempts/000002/evidence",
        archive_root,
    ):
        _private_directory(path)

    binding = validator.BindingView(
        mode="DISPOSABLE_FULL_SHAPE_TEST",
        project_root=project_root,
        absolute_destination=project_root / validator.REGISTERED_DESTINATION_RELATIVE,
        series_token_sha256="1" * 64,
        ledger_root=ledger,
        primary_series_container=ledger.parent,
        primary_receipt_root=ledger.parent / "receipts",
        secondary_series_container=tmp_path / "secondary",
        secondary_snapshot_root=tmp_path / "secondary/snapshots",
        secondary_receipt_root=tmp_path / "secondary/receipts",
    )
    epoch = _series_2_epoch_row(5, first=1, last=2)
    evidence_root = validator._evidence_root({})
    action_payloads: dict[str, bytes] = {}
    started_by_ordinal: dict[int, dict[str, Any]] = {}

    def authority(ordinal: int) -> dict[str, Any]:
        relative = (
            "docs/phase4/reports/"
            f"P4.2a-v2-2-rehearsal-attempt-{ordinal:06d}-"
            "execution-authorization-20260824.json"
        )
        payload = validator._canonical_json_bytes({"ordinal": ordinal})
        action_payloads[relative] = payload
        _private_file(project_root / relative, payload)
        return {
            "path": relative,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "creating_commit": f"{ordinal + 20:040x}",
            "unique_a_history_verified": True,
        }

    def started(ordinal: int, previous: str) -> tuple[dict[str, Any], bytes]:
        document = {
            "attempt_token_sha256": f"{ordinal:064x}",
            "previous_history_root_sha256": previous,
            "owner_action_time_authorization": authority(ordinal),
            "command_sha256": f"{ordinal + 2:064x}",
            "environment_sha256": f"{ordinal + 3:064x}",
        }
        payload = validator._canonical_json_bytes({"ordinal": ordinal, "started": True})
        started_by_ordinal[ordinal] = document
        return document, payload

    def candidate(document: dict[str, Any], ordinal: int) -> bytes:
        run_a = f"{ordinal + 4:064x}"
        run_b = f"{ordinal + 5:064x}"
        payload = {
            "schema_version": "p4.2a-v2-2-rehearsal-attempt-candidate-v1",
            "series_id": validator.REHEARSAL_ID,
            "ordinal": ordinal,
            "attempt_token_sha256": document["attempt_token_sha256"],
            "implementation_epoch": 5,
            "implementation_commit": epoch["implementation_commit"],
            "run_a_root_sha256": run_a,
            "run_b_root_sha256": run_b,
            "control_surface_root_sha256": epoch["control_merkle_root_sha256"],
            "evidence_tree_root_sha256": evidence_root,
            "candidate_content_root_sha256": validator._candidate_content_root(
                previous_history_root=document["previous_history_root_sha256"],
                run_a_root=run_a,
                run_b_root=run_b,
                control_root=epoch["control_merkle_root_sha256"],
                evidence_root=evidence_root,
            ),
            "validated_at_utc": validator.FIXED_WALL_CLOCK_TEXT,
        }
        return validator._canonical_json_bytes(payload)

    empty_root = validator._history_empty_root()
    started_one, started_one_payload = started(1, empty_root)
    candidate_one_payload = candidate(started_one, 1)
    record_one_root = validator._attempt_record_root(
        ordinal=1,
        attempt_token=started_one["attempt_token_sha256"],
        started_sha256=hashlib.sha256(started_one_payload).hexdigest(),
        candidate_sha256=hashlib.sha256(candidate_one_payload).hexdigest(),
        terminal_sha256=None,
        evidence_tree_root=evidence_root,
    )
    history_one = validator._history_step(empty_root, record_one_root)

    started_two, started_two_payload = started(2, history_one)
    candidate_two_payload = candidate(started_two, 2)
    terminal_two_payload = validator._canonical_json_bytes({"ordinal": 2, "terminal": True})
    record_two_root = validator._attempt_record_root(
        ordinal=2,
        attempt_token=started_two["attempt_token_sha256"],
        started_sha256=hashlib.sha256(started_two_payload).hexdigest(),
        candidate_sha256=hashlib.sha256(candidate_two_payload).hexdigest(),
        terminal_sha256=hashlib.sha256(terminal_two_payload).hexdigest(),
        evidence_tree_root=evidence_root,
    )

    live_payloads = {
        "series.json": validator._canonical_json_bytes({"series": 2}),
        ".series.lock": b"",
        "attempts/000001/started.json": started_one_payload,
        "attempts/000001/candidate.json": candidate_one_payload,
        "attempts/000002/started.json": started_two_payload,
        "attempts/000002/candidate.json": candidate_two_payload,
        "attempts/000002/terminal.json": terminal_two_payload,
    }
    for relative, payload in live_payloads.items():
        _private_file(ledger / relative, payload)

    archive_payloads: dict[str, bytes] = {}
    for relative, payload in live_payloads.items():
        archived = f"archive/attempt-history/{relative}"
        archive_payloads[archived] = payload
        _private_file(archive_root / relative, payload)
    for ordinal in (1, 2):
        relative = started_by_ordinal[ordinal]["owner_action_time_authorization"]["path"]
        archived = f"archive/attempt-history/attempts/{ordinal:06d}/action-time-authorization.json"
        archive_payloads[archived] = action_payloads[relative]
        _private_file(
            archive_root / f"attempts/{ordinal:06d}/action-time-authorization.json",
            action_payloads[relative],
        )

    def file_evidence(relative: str, payload: bytes) -> dict[str, Any]:
        return validator._file_evidence(
            live_relative=relative,
            archive_relative=f"archive/attempt-history/{relative}",
            payload=payload,
        )

    def action_evidence(ordinal: int) -> dict[str, Any]:
        reference = started_by_ordinal[ordinal]["owner_action_time_authorization"]
        return validator._authority_evidence(
            authority=reference,
            archive_relative=(
                f"archive/attempt-history/attempts/{ordinal:06d}/action-time-authorization.json"
            ),
            payload=action_payloads[reference["path"]],
        )

    records = [
        {
            "ordinal": 1,
            "attempt_token_sha256": started_one["attempt_token_sha256"],
            "previous_history_root_sha256": empty_root,
            "started": file_evidence("attempts/000001/started.json", started_one_payload),
            "candidate": None,
            "terminal": None,
            "outcome": "INCOMPLETE_UNTERMINALIZED",
            "reached_stage": "candidate_without_terminal",
            "implementation_epoch": 5,
            "implementation_commit": epoch["implementation_commit"],
            "owner_action_time_authorization": action_evidence(1),
            "command_sha256": started_one["command_sha256"],
            "environment_sha256": started_one["environment_sha256"],
            "automatic_retry_count": 0,
            "artifact_inventory": [],
            "error": None,
            "evidence_tree_root_sha256": evidence_root,
            "record_root_sha256": record_one_root,
        },
        {
            "ordinal": 2,
            "attempt_token_sha256": started_two["attempt_token_sha256"],
            "previous_history_root_sha256": history_one,
            "started": file_evidence("attempts/000002/started.json", started_two_payload),
            "candidate": file_evidence("attempts/000002/candidate.json", candidate_two_payload),
            "terminal": file_evidence("attempts/000002/terminal.json", terminal_two_payload),
            "outcome": "CANDIDATE_VALIDATED_AND_SELECTED",
            "reached_stage": "candidate_validated",
            "implementation_epoch": 5,
            "implementation_commit": epoch["implementation_commit"],
            "owner_action_time_authorization": action_evidence(2),
            "command_sha256": started_two["command_sha256"],
            "environment_sha256": started_two["environment_sha256"],
            "automatic_retry_count": 0,
            "artifact_inventory": [],
            "error": None,
            "evidence_tree_root_sha256": evidence_root,
            "record_root_sha256": record_two_root,
        },
    ]
    archive_files = [
        {"path": path, "sha256": hashlib.sha256(payload).hexdigest()}
        for path, payload in sorted(
            archive_payloads.items(), key=lambda item: item[0].encode("utf-8")
        )
    ]
    bundle = {
        "lineage": {"preregistration_commit": "a" * 40},
        "attempt_history": {"records": records},
        "archive": {
            "attempt_history": {
                "archive_root": "archive/attempt-history",
                "file_count": len(archive_payloads),
                "files": archive_files,
                "history_merkle_root_sha256": validator._path_merkle(
                    archive_payloads,
                    leaf_domain=b"p4.2a-rehearsal-leaf-v2.2\0",
                ),
                (
                    "every_live_started_candidate_terminal_and_action_authorization_byte_archived"
                ): True,
                "every_attempt_evidence_byte_archived": True,
            }
        },
    }

    monkeypatch.setattr(validator, "_validate_series_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        validator,
        "_epoch_map",
        lambda *args, **kwargs: {5: epoch},
    )
    monkeypatch.setattr(
        validator,
        "_validate_started",
        lambda _payload, *, ordinal, **_kwargs: started_by_ordinal[ordinal],
    )
    monkeypatch.setattr(
        validator,
        "_validate_terminal",
        lambda *args, **kwargs: {
            "outcome": "CANDIDATE_VALIDATED_AND_SELECTED",
            "reached_stage": "candidate_validated",
            "error": None,
            "artifact_inventory": [],
        },
    )
    monkeypatch.setattr(
        validator,
        "_unique_a_authority",
        lambda _root, reference, **_kwargs: action_payloads[reference["path"]],
    )
    monkeypatch.setattr(
        validator,
        "_validate_action_authorization",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        validator,
        "_git_bytes",
        lambda *args, **kwargs: ("e" * 40 + "\n").encode("ascii"),
    )
    monkeypatch.setattr(
        validator,
        "_git_commit",
        lambda _root, value, _label: validator._commit(value, "synthetic commit"),
    )
    monkeypatch.setattr(validator, "_git_is_ancestor", lambda *args, **kwargs: True)
    return {
        "validator": validator,
        "binding": binding,
        "bundle": bundle,
        "ledger": ledger,
        "archive_root": archive_root,
        "candidate_one_payload": candidate_one_payload,
        "records": records,
    }


def test_candidate_without_terminal_is_incomplete_null_in_schema_and_byte_archived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _candidate_without_terminal_archive_fixture(tmp_path, monkeypatch)
    validator = fixture["validator"]
    replay = validator._validate_attempt_history_records(
        project_root=fixture["binding"].project_root,
        bundle=fixture["bundle"],
        ledger_root=fixture["ledger"],
        archive_root=fixture["archive_root"],
        binding=fixture["binding"],
    )
    record = replay.records[0]
    assert record["outcome"] == "INCOMPLETE_UNTERMINALIZED"
    assert record["reached_stage"] == "candidate_without_terminal"
    assert record["candidate"] is None

    schema = json.loads((PROJECT_ROOT / SERIES_2_BUNDLE_SCHEMA_RELATIVE).read_bytes())
    record_validator = _definition_validator(schema, "attemptRecord")
    assert list(record_validator.iter_errors(record)) == []
    with_candidate = copy.deepcopy(record)
    with_candidate["candidate"] = validator._file_evidence(
        live_relative="attempts/000001/candidate.json",
        archive_relative=("archive/attempt-history/attempts/000001/candidate.json"),
        payload=fixture["candidate_one_payload"],
    )
    assert list(record_validator.iter_errors(with_candidate)) != []

    archived = "archive/attempt-history/attempts/000001/candidate.json"
    live = "attempts/000001/candidate.json"
    payload = fixture["candidate_one_payload"]
    manifest = {
        row["path"]: row["sha256"]
        for row in fixture["bundle"]["archive"]["attempt_history"]["files"]
    }
    assert replay.live_payloads[live] == payload
    assert replay.archive_payloads[archived] == payload
    assert manifest[archived] == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    "mutation",
    ("field-set", "noncanonical", "attempt-token", "evidence-root", "started-binding"),
)
def test_candidate_without_terminal_damage_or_binding_drift_fails_closed(
    mutation: str,
) -> None:
    validator = _validator_module()
    epoch = _series_2_epoch_row(5, first=1, last=1)
    evidence_root = validator._evidence_root({})
    started = {
        "attempt_token_sha256": "1" * 64,
        "previous_history_root_sha256": validator._history_empty_root(),
    }
    candidate = {
        "schema_version": "p4.2a-v2-2-rehearsal-attempt-candidate-v1",
        "series_id": validator.REHEARSAL_ID,
        "ordinal": 1,
        "attempt_token_sha256": started["attempt_token_sha256"],
        "implementation_epoch": 5,
        "implementation_commit": epoch["implementation_commit"],
        "run_a_root_sha256": "2" * 64,
        "run_b_root_sha256": "3" * 64,
        "control_surface_root_sha256": epoch["control_merkle_root_sha256"],
        "evidence_tree_root_sha256": evidence_root,
        "candidate_content_root_sha256": validator._candidate_content_root(
            previous_history_root=started["previous_history_root_sha256"],
            run_a_root="2" * 64,
            run_b_root="3" * 64,
            control_root=epoch["control_merkle_root_sha256"],
            evidence_root=evidence_root,
        ),
        "validated_at_utc": validator.FIXED_WALL_CLOCK_TEXT,
    }
    if mutation == "field-set":
        candidate["unexpected"] = True
    elif mutation == "attempt-token":
        candidate["attempt_token_sha256"] = "f" * 64
    elif mutation == "evidence-root":
        candidate["evidence_tree_root_sha256"] = "e" * 64
    elif mutation == "started-binding":
        candidate["implementation_commit"] = "f" * 40
    payload = validator._canonical_json_bytes(candidate)
    if mutation == "noncanonical":
        payload = json.dumps(candidate, sort_keys=True).encode("utf-8")
    with pytest.raises(validator.RehearsalV22ValidationError, match=r"candidate"):
        validator._validate_candidate(
            payload,
            ordinal=1,
            started=started,
            epoch=epoch,
            evidence_root=evidence_root,
        )


def test_series_2_active_constants_remain_isolated_and_schema_profiles_are_exact() -> None:
    validator = _validator_module()
    assert validator.INCIDENT_SHA256 == (
        "d658336f61cdca0239584b696043fe4abc5ede1ef7aff76a4fe514b7b5d0735c"
    )
    assert validator.PREREGISTRATION_RELATIVE == PREREGISTRATION_RELATIVE
    assert validator.BUNDLE_SCHEMA_RELATIVE == BUNDLE_SCHEMA_RELATIVE
    assert validator.RELEASE_SCHEMA_RELATIVE == RELEASE_SCHEMA_RELATIVE
    assert validator.SERIES_2_TOKEN_SEED_SHA256 != validator.INCIDENT_SHA256
    assert validator.SERIES_2_PREREGISTRATION_RELATIVE != validator.PREREGISTRATION_RELATIVE
    assert validator.SERIES_2_BUNDLE_SCHEMA_RELATIVE != validator.BUNDLE_SCHEMA_RELATIVE
    assert validator.SERIES_2_RELEASE_SCHEMA_RELATIVE != validator.RELEASE_SCHEMA_RELATIVE
    historical_bundle, historical_release = _schemas()
    active_bundle, active_release = _series_2_schemas()
    validator._validate_series_2_schema_profiles(
        project_root=PROJECT_ROOT,
        historical_bundle_payload=(PROJECT_ROOT / BUNDLE_SCHEMA_RELATIVE).read_bytes(),
        historical_release_payload=(PROJECT_ROOT / RELEASE_SCHEMA_RELATIVE).read_bytes(),
        active_bundle_payload=(PROJECT_ROOT / SERIES_2_BUNDLE_SCHEMA_RELATIVE).read_bytes(),
        active_release_payload=(PROJECT_ROOT / SERIES_2_RELEASE_SCHEMA_RELATIVE).read_bytes(),
    )
    for historical, active, pointers in (
        (
            historical_bundle,
            active_bundle,
            validator.SERIES_2_BUNDLE_SCHEMA_DELTA_POINTERS,
        ),
        (
            historical_release,
            active_release,
            validator.SERIES_2_RELEASE_SCHEMA_DELTA_POINTERS,
        ),
    ):
        historical_copy = copy.deepcopy(historical)
        active_copy = copy.deepcopy(active)
        for pointer in pointers:
            assert _strip_json_pointer(active_copy, pointer, required=True)
            _strip_json_pointer(historical_copy, pointer, required=False)
        assert _typed_equal(historical_copy, active_copy)


def test_series_2_series_json_is_exactly_old_thirteen_plus_origin_five(
    tmp_path: Path,
) -> None:
    validator = _validator_module()
    binding = validator.BindingView(
        mode="DISPOSABLE_FULL_SHAPE_TEST",
        project_root=tmp_path,
        absolute_destination=tmp_path / validator.REGISTERED_DESTINATION_RELATIVE,
        series_token_sha256="5" * 64,
        ledger_root=tmp_path / "primary/ledger",
        primary_series_container=tmp_path / "primary",
        primary_receipt_root=tmp_path / "primary/receipts",
        secondary_series_container=tmp_path / "secondary",
        secondary_snapshot_root=tmp_path / "secondary/snapshots",
        secondary_receipt_root=tmp_path / "secondary/receipts",
    )
    document = {
        "schema_version": validator.SERIES_2_SERIES_SCHEMA_VERSION,
        "series_id": validator.REHEARSAL_ID,
        "series_token_sha256": binding.series_token_sha256,
        "policy": validator.SERIES_POLICY,
        "ledger_root": binding.ledger_root.as_posix(),
        "attempt_limit": "unbounded_until_first_validated_success_or_owner_abandonment",
        "per_attempt_action_time_owner_authorization_required": True,
        "automatic_retry_count": 0,
        "first_validated_candidate_closes_series": True,
        "implementation_epoch_origin": 5,
        "preregistration": {
            "path": validator.SERIES_2_PREREGISTRATION_RELATIVE.as_posix(),
            "sha256": validator.SERIES_2_PREREGISTRATION_SHA256,
            "creating_commit": validator.SERIES_2_PREREGISTRATION_COMMIT,
            "unique_a_history_verified": True,
        },
        "bundle_schema": {
            "path": validator.SERIES_2_BUNDLE_SCHEMA_RELATIVE.as_posix(),
            "sha256": validator.SERIES_2_BUNDLE_SCHEMA_SHA256,
        },
        "release_schema": {
            "path": validator.SERIES_2_RELEASE_SCHEMA_RELATIVE.as_posix(),
            "sha256": validator.SERIES_2_RELEASE_SCHEMA_SHA256,
        },
        "created_at_utc": "2026-08-23T12:00:00Z",
    }
    payload = validator._canonical_json_bytes(document)
    assert set(document) == validator._SERIES_2_FIELDS
    assert len(document) == len(validator._SERIES_FIELDS) + 1 == 14
    assert (
        validator._validate_series_json(
            payload,
            binding=binding,
            preregistration_commit=validator.SERIES_2_PREREGISTRATION_COMMIT,
        )
        == document
    )
    for mutation in ("missing", "origin-four", "historical-preregistration"):
        tampered = copy.deepcopy(document)
        if mutation == "missing":
            del tampered["implementation_epoch_origin"]
        elif mutation == "origin-four":
            tampered["implementation_epoch_origin"] = 4
        else:
            tampered["preregistration"]["path"] = PREREGISTRATION_RELATIVE.as_posix()
        with pytest.raises(validator.RehearsalV22ValidationError):
            validator._validate_series_json(
                validator._canonical_json_bytes(tampered),
                binding=binding,
                preregistration_commit=validator.SERIES_2_PREREGISTRATION_COMMIT,
            )


def test_series_2_epoch_map_uses_explicit_origin_five_keys() -> None:
    validator = _validator_module()
    one = {"implementation_epochs": [_series_2_epoch_row(5, first=1, last=2)]}
    assert list(validator._epoch_map(one, epoch_origin=5)) == [5]
    transitioned = {
        "implementation_epochs": [
            _series_2_epoch_row(5, first=1, last=2),
            _series_2_epoch_row(6, first=3, last=4),
        ]
    }
    assert list(validator._epoch_map(transitioned, epoch_origin=5)) == [5, 6]
    invalid_rows = [
        [_series_2_epoch_row(origin, first=1, last=1)] for origin in (1, 2, 3, 4, 6)
    ] + [
        [
            _series_2_epoch_row(5, first=1, last=1),
            _series_2_epoch_row(7, first=2, last=2),
        ],
        [
            _series_2_epoch_row(6, first=1, last=1),
            _series_2_epoch_row(5, first=2, last=2),
        ],
    ]
    for rows in invalid_rows:
        with pytest.raises(
            validator.RehearsalV22ValidationError,
            match=r"explicit keys",
        ):
            validator._epoch_map(
                {"implementation_epochs": rows},
                epoch_origin=5,
            )


def test_series_2_cross_release_resolves_selected_epoch_five_by_key() -> None:
    validator = _validator_module()
    bundle, receipt = _minimal_cross_valid_bundle_receipt()
    validator._cross_validate_release(bundle=bundle, receipt=receipt)
    tampered = copy.deepcopy(bundle)
    tampered["implementation_epochs"][0]["epoch"] = 1
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"explicit keys",
    ):
        validator._cross_validate_release(bundle=tampered, receipt=receipt)


def test_series_2_owner_authority_epoch_must_match_explicit_epoch_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _validator_module()
    epoch = _series_2_epoch_row(5, first=1, last=1)
    owner_reference = epoch["owner_exact_surface_authorization"]
    owner_document = {
        "schema_version": "p4.2a-v2-2-implementation-epoch-surface-authorization-v1",
        "verdict": "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
        "owner": {"identity": "ouyang", "approved": True},
        "implementation_epoch": 6,
        "base_commit": "b" * 40,
        "exact_surface": [
            {
                "path": "scripts/p4_2a_v2_2_heldout_rehearsal.py",
                "status": "M",
            }
        ],
    }
    monkeypatch.setattr(
        validator,
        "_git_bytes",
        lambda *_args, **_kwargs: b"f" * 40 + b"\n",
    )
    monkeypatch.setattr(
        validator,
        "_git_commit",
        lambda _root, value, _label: str(value),
    )
    monkeypatch.setattr(validator, "_git_is_ancestor", lambda *_args: True)
    monkeypatch.setattr(
        validator,
        "_git_parents",
        lambda *_args: (owner_reference["creating_commit"],),
    )
    monkeypatch.setattr(
        validator,
        "_unique_a_authority",
        lambda *_args, **_kwargs: validator._canonical_json_bytes(owner_document),
    )
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"implementation epoch 5 surface authorization number drifted",
    ):
        validator._validate_implementation_epochs(
            project_root=tmp_path,
            bundle={"implementation_epochs": [epoch]},
            replay=SimpleNamespace(
                selected_implementation_epoch=5,
                selected_implementation_commit=epoch["implementation_commit"],
            ),
            archives=SimpleNamespace(control_root_sha256=epoch["control_merkle_root_sha256"]),
            validation_context=validator.ActiveBundleValidationContext(
                mode=validator.BundleValidationMode.ACTIVE_ATTEMPT_BUNDLE,
            ),
            control_pass_nonce=object(),
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "release-epoch-key",
        "owner-authority",
        "review-authority",
        "selected-topology",
    ),
)
def test_series_2_release_epoch_projection_rejects_authority_or_index_substitution(
    mutation: str,
) -> None:
    validator = _validator_module()
    bundle, receipt = _minimal_cross_valid_bundle_receipt()
    receipt = copy.deepcopy(receipt)
    if mutation == "release-epoch-key":
        receipt["implementation_epochs"][0]["epoch"] = 6
    elif mutation == "owner-authority":
        receipt["implementation_epochs"][0]["owner_surface_authorization"]["sha256"] = "0" * 64
    elif mutation == "review-authority":
        receipt["implementation_epochs"][0]["independent_implementation_review"]["sha256"] = (
            "0" * 64
        )
    else:
        receipt["lineage"]["selected_implementation_commit"] = "0" * 40
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=(
            r"release implementation epochs drifted|"
            r"release lineage selected_implementation_commit drifted"
        ),
    ):
        validator._cross_validate_release(bundle=bundle, receipt=receipt)


def test_independent_mirror_validation_is_read_only_and_primary_prefix_bound(
    tmp_path: Path,
) -> None:
    fixture = _series_2_mirror_fixture(tmp_path)
    validator = fixture["validator"]
    binding = fixture["binding"]
    before = tuple(
        _tree_fingerprint(path)
        for path in (
            binding.ledger_root,
            binding.secondary_snapshot_root,
            binding.primary_receipt_root,
            binding.secondary_receipt_root,
        )
    )
    receipts = validator._validate_second_copy_history(
        binding=binding,
        replay=fixture["replay"],
    )
    assert receipts == (fixture["receipt"],)
    after = tuple(
        _tree_fingerprint(path)
        for path in (
            binding.ledger_root,
            binding.secondary_snapshot_root,
            binding.primary_receipt_root,
            binding.secondary_receipt_root,
        )
    )
    assert after == before


def test_self_consistent_snapshot_and_paired_receipt_substitution_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _series_2_mirror_fixture(tmp_path)
    validator = fixture["validator"]
    binding = fixture["binding"]
    snapshot = fixture["snapshot"]
    _private_file(snapshot / "attempts/000001/started.json", b'{"substitute":true}\n')
    substituted_inventory = validator._strict_private_tree_inventory(
        snapshot,
        label="substituted mirror snapshot",
    )
    substituted_root = validator._path_merkle(
        substituted_inventory.payloads,
        leaf_domain=b"p4.2a-rehearsal-v2.2-ledger-leaf-v1\0",
    )
    substituted_snapshot = binding.secondary_snapshot_root / validator._mirror_snapshot_name(
        1, substituted_root
    )
    snapshot.rename(substituted_snapshot)
    substituted_receipt = copy.deepcopy(fixture["receipt"])
    substituted_receipt.update(
        {
            "secondary_snapshot_root": substituted_snapshot.as_posix(),
            "live_ledger_root_sha256": substituted_root,
            "file_count": substituted_inventory.file_count,
            "total_bytes": substituted_inventory.total_bytes,
            "primary_inventory_sha256": substituted_inventory.sha256,
            "secondary_inventory_sha256": substituted_inventory.sha256,
        }
    )
    substituted_name = validator._mirror_receipt_filename(1, substituted_root)
    substituted_payload = validator._canonical_json_bytes(substituted_receipt)
    for receipt_root in (
        binding.primary_receipt_root,
        binding.secondary_receipt_root,
    ):
        (receipt_root / fixture["receipt_name"]).unlink()
        _private_file(receipt_root / substituted_name, substituted_payload)
    before = _tree_fingerprint(binding.ledger_root)
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"snapshot inventory differs from current primary prefix",
    ):
        validator._validate_second_copy_history(
            binding=binding,
            replay=fixture["replay"],
        )
    assert _tree_fingerprint(binding.ledger_root) == before


@pytest.mark.parametrize(
    "mutation",
    ("missing-receipt", "mode-drift", "symlink", "hardlink", "extra-snapshot"),
)
def test_independent_mirror_validation_rejects_every_structural_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _series_2_mirror_fixture(tmp_path)
    validator = fixture["validator"]
    binding = fixture["binding"]
    snapshot = fixture["snapshot"]
    target = snapshot / "attempts/000001/started.json"
    if mutation == "missing-receipt":
        (binding.secondary_receipt_root / fixture["receipt_name"]).unlink()
    elif mutation == "mode-drift":
        target.chmod(0o644)
    elif mutation == "symlink":
        target.unlink()
        target.symlink_to(snapshot / "series.json")
    elif mutation == "hardlink":
        payload = target.read_bytes()
        target.unlink()
        outside = tmp_path / "hardlink-source"
        _private_file(outside, payload)
        os.link(outside, target)
    else:
        _private_directory(binding.secondary_snapshot_root / "unexpected")
    before = _tree_fingerprint(binding.ledger_root)
    with pytest.raises(validator.RehearsalV22ValidationError):
        validator._validate_second_copy_history(
            binding=binding,
            replay=fixture["replay"],
        )
    assert _tree_fingerprint(binding.ledger_root) == before


@pytest.mark.parametrize(
    "fault",
    (
        "snapshot-missing",
        "snapshot-extra-file",
        "primary-receipt-missing",
        "secondary-receipt-mismatch",
        "paired-timestamp-substitution",
        "snapshot-mode",
        "snapshot-symlink",
        "snapshot-hardlink",
        "publish-collision-residue",
    ),
)
def test_validator_second_copy_fault_matrix_blocks_every_continuation_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    fixture = _series_2_mirror_fixture(tmp_path)
    validator = fixture["validator"]
    validator_binding = fixture["binding"]
    _apply_series_2_second_copy_fault(fixture, fault=fault, tmp_path=tmp_path)
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding, history, next_action = _producer_view_of_mirror_fixture(fixture)
    terminal = binding.ledger_root / "attempts/000001/terminal.json"
    terminal_before = terminal.read_bytes()
    protected_paths = (
        binding.ledger_root,
        binding.secondary_snapshot_root,
        binding.primary_receipt_root,
        binding.secondary_receipt_root,
    )
    fault_state = tuple(_tree_fingerprint(path) for path in protected_paths)

    with pytest.raises(validator.RehearsalV22ValidationError):
        validator._validate_second_copy_history(
            binding=validator_binding,
            replay=fixture["replay"],
        )

    guard_calls: list[tuple[Any, Any, bool]] = []

    def reject_faulted_second_copy(
        observed_binding: Any,
        observed_history: Any,
        *,
        allow_unmirrored_final: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        assert observed_binding == binding
        assert observed_history == history
        guard_calls.append((observed_binding, observed_history, allow_unmirrored_final))
        try:
            validator._validate_second_copy_history(
                binding=validator_binding,
                replay=fixture["replay"],
            )
        except validator.RehearsalV22ValidationError as exc:
            raise implementation.RehearsalV22Error(
                "validator-confirmed second-copy fault blocks continuation"
            ) from exc
        raise AssertionError("faulted second copy unexpectedly validated")

    history_calls: list[Any] = []

    def live_history(observed_binding: Any, **kwargs: Any) -> Any:
        assert observed_binding == binding
        assert kwargs == {}
        history_calls.append(observed_binding)
        return history

    action_calls: list[tuple[Any, Any, int, str, bool]] = []

    def validate_action(
        observed_binding: Any,
        authority: Any,
        *,
        expected_ordinal: int,
        expected_previous_history_root_sha256: str,
        require_current_process: bool,
    ) -> Any:
        assert observed_binding == binding
        assert authority == next_action.authority_ref(binding.project_root)
        assert expected_ordinal == 2
        assert expected_previous_history_root_sha256 == history.history_root_sha256
        assert require_current_process is True
        action_calls.append(
            (
                observed_binding,
                authority,
                expected_ordinal,
                expected_previous_history_root_sha256,
                require_current_process,
            )
        )
        return next_action

    next_epoch_calls: list[tuple[Any, int]] = []

    def validate_next_epoch(observed_history: Any, epoch: int) -> None:
        assert observed_history == history
        assert epoch == 5
        next_epoch_calls.append((observed_history, epoch))

    repair_calls: list[tuple[object, ...]] = []

    def forbidden_automatic_repair(*args: object, **kwargs: object) -> None:
        repair_calls.append((*args, kwargs))
        raise AssertionError("second-copy fault attempted automatic repair")

    monkeypatch.setattr(
        implementation,
        "_validate_second_copy_history",
        reject_faulted_second_copy,
    )
    monkeypatch.setattr(implementation, "validate_live_history", live_history)
    monkeypatch.setattr(
        implementation,
        "_active_validator_execution_context",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(implementation, "_validate_action_authorization", validate_action)
    monkeypatch.setattr(implementation, "_validate_next_series_2_epoch", validate_next_epoch)
    monkeypatch.setattr(
        implementation,
        "_mirror_before_next_allocation",
        forbidden_automatic_repair,
    )

    rejection_messages: list[str] = []

    with pytest.raises(implementation.RehearsalV22Error) as continuation_error:
        implementation._validate_continuation_mirror_state(
            binding,
            history,
            permit_unmirrored_final_incomplete=False,
        )
    rejection_messages.append(str(continuation_error.value))
    with pytest.raises(implementation.RehearsalV22Error) as bundle_error:
        implementation._build_bundle(
            binding=binding,
            history=history,
            run_a=None,
            run_b=None,
            control=None,
        )
    rejection_messages.append(str(bundle_error.value))
    with pytest.raises(implementation.RehearsalV22Error) as replay_error:
        implementation._official_validator_replay_scope(
            binding=binding,
            validator_module=validator,
            bundle_path=binding.destination / implementation.BUNDLE_FILENAME,
            implementation_commit=next_action.implementation_commit,
        ).__enter__()
    rejection_messages.append(str(replay_error.value))

    ledger = implementation.SeriesLedger(
        binding=binding,
        execution_context=None,
        lock_descriptor=1,
        locked=True,
    )
    with pytest.raises(implementation.RehearsalV22Error) as allocation_error:
        ledger.allocate_attempt(
            next_action,
            created_at_utc=implementation.FIXED_WALL_CLOCK_TEXT,
        )
    rejection_messages.append(str(allocation_error.value))

    assert len(rejection_messages) == 4
    assert sum(
        message == "validator-confirmed second-copy fault blocks continuation"
        for message in rejection_messages
    ) == 2
    early_rejections = [
        message
        for message in rejection_messages
        if message != "validator-confirmed second-copy fault blocks continuation"
    ]
    assert len(early_rejections) == 2
    assert all(message for message in early_rejections)
    assert len(guard_calls) == 2
    assert all(allow_unmirrored is False for _, _, allow_unmirrored in guard_calls)
    assert len(history_calls) == 2
    assert len(action_calls) == 1
    assert next_epoch_calls == [(history, 5)]
    assert repair_calls == []
    assert terminal.read_bytes() == terminal_before
    assert not (binding.ledger_root / "attempts/000002").exists()
    assert tuple(_tree_fingerprint(path) for path in protected_paths) == fault_state


def test_validator_native_rename_requires_issued_authority_without_effect(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    candidate = tmp_path / "candidate"
    candidate.mkdir(mode=0o700)
    _private_file(candidate / "bundle.json", b"{}\n")
    destination = tmp_path / "destination"
    before = _tree_fingerprint(candidate)
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"issued audit policy|authority",
    ):
        implementation._rename_directory_exclusive(candidate, destination)
    assert _tree_fingerprint(candidate) == before
    assert not os.path.lexists(destination)


@pytest.mark.parametrize("primitive", ("renamex", "renameatx"))
def test_validator_cdll_factory_replacement_blocks_each_native_primitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    primitive: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    source = tmp_path / f"source-{primitive}"
    destination = tmp_path / f"destination-{primitive}"
    source.mkdir(mode=0o700)
    _private_file(source / "bundle.json", b"{}\n")
    before = _tree_fingerprint(source)
    policy = implementation._read_only_preflight_policy(PROJECT_ROOT)
    with monkeypatch.context() as patcher:
        patcher.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: object())
        with pytest.raises(implementation.RehearsalV22Error, match=r"runtime factory"):
            if primitive == "renamex":
                implementation._native_rename_exclusive_call(
                    policy,
                    source,
                    destination,
                )
            else:
                parent_descriptor = os.open(tmp_path, os.O_RDONLY)
                try:
                    implementation._native_mirror_renameatx_exclusive_call(
                        policy,
                        source,
                        destination,
                        parent_descriptor=parent_descriptor,
                        expected_parent_identity=(
                            tmp_path.stat().st_dev,
                            tmp_path.stat().st_ino,
                        ),
                    )
                finally:
                    os.close(parent_descriptor)
    assert _tree_fingerprint(source) == before
    assert not os.path.lexists(destination)


def test_validator_positive_openat_issuance_writes_exact_private_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _validator_kernel_policy_scope(
        tmp_path,
        monkeypatch,
        label="positive-openat",
    ) as (implementation, _policy, write_root, _native_root):
        parent_descriptor = os.open(write_root, os.O_RDONLY)
        try:
            gc_before = gc.isenabled()
            target = write_root / "success.bin"
            implementation._write_exclusive_at(
                parent_descriptor,
                target,
                b"descriptor-bound\n",
            )
            assert target.read_bytes() == b"descriptor-bound\n"
            assert stat.S_IMODE(target.stat().st_mode) == 0o600
            assert target.stat().st_nlink == 1
            assert gc.isenabled() is gc_before
            assert implementation._OPENAT_WRITE_CAPABILITY.get() is None
            closure = inspect.getclosurevars(implementation._openat_write_capability_path).nonlocals
            assert closure["registry"] == ()
        finally:
            os.close(parent_descriptor)


@pytest.mark.parametrize(
    "mutation",
    (
        "external-parent-descriptor",
        "wrong-entry-name",
        "append-flags",
        "public-mode",
        "bool-parent-descriptor",
        "entry-name-subclass",
        "flags-subclass",
        "mode-subclass",
        "bool-payload-bytes",
        "bytes-payload-sha256",
        "payload-sha256-subclass",
    ),
)
def test_validator_openat_capability_argument_matrix_rejects_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    with _validator_kernel_policy_scope(
        tmp_path,
        monkeypatch,
        label=f"openat-argument-{mutation}",
    ) as (implementation, policy, write_root, _native_root):
        parent_descriptor = os.open(write_root, os.O_RDONLY)
        external_descriptor = os.open(tmp_path, os.O_RDONLY)
        try:

            class EvilInt(int):
                pass

            class EvilStr(str):
                pass

            registered_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
            target = write_root / "invalid.bin"
            kwargs: dict[str, object] = {
                "parent_descriptor": parent_descriptor,
                "entry_name": target.name,
                "absolute_path": target,
                "flags": registered_flags,
                "mode": 0o600,
                "payload_bytes": 4,
                "payload_sha256": hashlib.sha256(b"test").hexdigest(),
            }
            replacements: dict[str, tuple[str, object]] = {
                "external-parent-descriptor": (
                    "parent_descriptor",
                    external_descriptor,
                ),
                "wrong-entry-name": ("entry_name", "wrong.bin"),
                "append-flags": ("flags", registered_flags | os.O_APPEND),
                "public-mode": ("mode", 0o644),
                "bool-parent-descriptor": ("parent_descriptor", True),
                "entry-name-subclass": ("entry_name", EvilStr(target.name)),
                "flags-subclass": ("flags", EvilInt(registered_flags)),
                "mode-subclass": ("mode", EvilInt(0o600)),
                "bool-payload-bytes": ("payload_bytes", True),
                "bytes-payload-sha256": ("payload_sha256", b"0" * 64),
                "payload-sha256-subclass": (
                    "payload_sha256",
                    EvilStr("0" * 64),
                ),
            }
            key, replacement = replacements[mutation]
            kwargs[key] = replacement
            with pytest.raises(
                implementation.RehearsalV22Error,
                match=r"openat|authority",
            ):
                implementation._open_exclusive_at_issued(policy, **kwargs)
            assert not target.exists()
            assert implementation._OPENAT_WRITE_CAPABILITY.get() is None
            closure = inspect.getclosurevars(implementation._openat_write_capability_path).nonlocals
            assert closure["registry"] == ()
        finally:
            os.close(external_descriptor)
            os.close(parent_descriptor)


@pytest.mark.parametrize("replacement", ("os-open", "capability-issuer"))
def test_validator_openat_hostile_runtime_replacement_has_no_write_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    with _validator_kernel_policy_scope(
        tmp_path,
        monkeypatch,
        label=f"openat-runtime-{replacement}",
    ) as (implementation, _policy, write_root, _native_root):
        parent_descriptor = os.open(write_root, os.O_RDONLY)
        target = write_root / f"blocked-{replacement}.bin"
        try:
            with monkeypatch.context() as patcher:
                if replacement == "os-open":
                    patcher.setattr(os, "open", lambda *_args, **_kwargs: -1)
                else:
                    patcher.setattr(
                        implementation,
                        "_openat_write_capability_path",
                        lambda *_args, **_kwargs: target,
                    )
                with pytest.raises(
                    implementation.RehearsalV22Error,
                    match=r"runtime authority",
                ):
                    implementation._write_exclusive_at(
                        parent_descriptor,
                        target,
                        b"blocked\n",
                    )
            assert not target.exists()
            assert implementation._OPENAT_WRITE_CAPABILITY.get() is None
            closure = inspect.getclosurevars(implementation._openat_write_capability_path).nonlocals
            assert closure["registry"] == ()
        finally:
            os.close(parent_descriptor)


def test_validator_recovery_rename_stolen_nonce_rejects_hostile_any_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    recovery_checker = implementation._recovery_rename_capability_is_issued
    recovery_closure = inspect.getclosurevars(recovery_checker).nonlocals
    recovery_nonce = recovery_closure["nonce"]
    recovery_registry = recovery_closure["registry"]
    assert recovery_registry == ()
    assert recovery_closure["capability_type"] is implementation._RecoveryRenameCapability
    assert recovery_closure["python_type"] is type
    assert recovery_closure["python_id"] is id

    stage = (tmp_path / "recovery-stage").absolute()
    destination = (tmp_path / "recovery-destination").absolute()
    unrelated = (tmp_path / "recovery-unrelated").absolute()
    recovery_policy = implementation._AuditPolicy(
        project_root=PROJECT_ROOT,
        write_roots=(stage,),
        exact_write_paths=(destination, unrelated),
        create_only_roots=(),
        sqlite_roots=(),
        git_roots=(PROJECT_ROOT,),
        subprocess_mode="git-read",
        recovery_rename_pairs=((stage, destination),),
    )
    forged_recovery = implementation._RecoveryRenameCapability(
        _nonce=recovery_nonce,
        policy_id=id(recovery_policy),
        source=stage,
        destination=destination,
    )
    forged_cross_pair = implementation._RecoveryRenameCapability(
        _nonce=recovery_nonce,
        policy_id=id(recovery_policy),
        source=stage,
        destination=unrelated,
    )
    stale_recovery_snapshot = (*recovery_registry, forged_recovery)
    assert stale_recovery_snapshot == (forged_recovery,)

    with monkeypatch.context() as patcher:
        patcher.setattr(builtins, "any", lambda *_args, **_kwargs: True)
        assert not recovery_checker(
            forged_recovery,
            policy=recovery_policy,
            source=stage,
            destination=destination,
        )
        assert not recovery_checker(
            forged_cross_pair,
            policy=recovery_policy,
            source=stage,
            destination=unrelated,
        )


def test_validator_openat_reentrant_capability_is_rejected_and_cleared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _validator_kernel_policy_scope(
        tmp_path,
        monkeypatch,
        label="openat-reentrant",
    ) as (implementation, policy, write_root, _native_root):
        parent_descriptor = os.open(write_root, os.O_RDONLY)
        target = write_root / "reentrant.bin"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        forged = implementation._OpenAtWriteCapability(
            _nonce=object(),
            policy_id=id(policy),
            parent_descriptor=parent_descriptor,
            parent_identity=(write_root.stat().st_dev, write_root.stat().st_ino),
            entry_name=target.name,
            absolute_path=target,
            flags=flags,
            mode=0o600,
            payload_bytes=8,
            payload_sha256=hashlib.sha256(b"blocked\n").hexdigest(),
        )
        token = implementation._OPENAT_WRITE_CAPABILITY.set(forged)
        try:
            with pytest.raises(
                implementation.RehearsalV22Error,
                match=r"already active",
            ):
                implementation._write_exclusive_at(
                    parent_descriptor,
                    target,
                    b"blocked\n",
                )
        finally:
            implementation._OPENAT_WRITE_CAPABILITY.reset(token)
            os.close(parent_descriptor)
        assert not target.exists()
        assert implementation._OPENAT_WRITE_CAPABILITY.get() is None
        closure = inspect.getclosurevars(implementation._openat_write_capability_path).nonlocals
        assert closure["registry"] == ()


@pytest.mark.parametrize(
    "replacement",
    (
        "cdll-init",
        "cdll-getattribute",
        "equal-looking-func-flags",
        "equal-looking-platform",
    ),
)
def test_validator_native_hostile_runtime_replacement_matrix_has_no_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    with _validator_kernel_policy_scope(
        tmp_path,
        monkeypatch,
        label=f"native-runtime-{replacement}",
    ) as (implementation, policy, _write_root, native_root):
        source = native_root / f"source-{replacement}"
        destination = native_root / f"destination-{replacement}"
        source.mkdir(mode=0o700)
        callback_effects: list[str] = []

        class EqualLookingInt(int):
            def __eq__(self, _other: object) -> bool:
                callback_effects.append("int-equality")
                return True

            def __or__(self, _other: object) -> int:
                callback_effects.append("int-or")
                return int(self)

        class EqualLookingStr(str):
            def __eq__(self, _other: object) -> bool:
                callback_effects.append("str-equality")
                return True

            def startswith(self, *_args: object, **_kwargs: object) -> bool:
                callback_effects.append("str-startswith")
                return False

        with monkeypatch.context() as patcher:
            if replacement == "cdll-init":
                patcher.setattr(ctypes.CDLL, "__init__", lambda *_args, **_kwargs: None)
            elif replacement == "cdll-getattribute":
                patcher.setattr(
                    ctypes.CDLL,
                    "__getattribute__",
                    lambda self, name: object.__getattribute__(self, name),
                )
            elif replacement == "equal-looking-func-flags":
                patcher.setattr(
                    ctypes.CDLL,
                    "_func_flags_",
                    EqualLookingInt(ctypes.CDLL._func_flags_),
                )
            else:
                patcher.setattr(
                    ctypes._sys,
                    "platform",
                    EqualLookingStr(ctypes._sys.platform),
                )
            with pytest.raises(
                implementation.RehearsalV22Error,
                match=r"runtime factory",
            ):
                implementation._native_rename_exclusive_call(
                    policy,
                    source,
                    destination,
                )
        assert source.is_dir()
        assert not destination.exists()
        assert callback_effects == []


def test_validator_native_reentrant_capability_is_rejected_without_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _validator_kernel_policy_scope(
        tmp_path,
        monkeypatch,
        label="native-reentrant",
    ) as (implementation, policy, _write_root, native_root):
        source = native_root / "source-reentrant"
        destination = native_root / "destination-reentrant"
        source.mkdir(mode=0o700)
        authority_token = implementation._TEMP_AUTHORITY.set(native_root)
        forged = implementation._NativeRenameCapability(
            _nonce=object(),
            policy_id=id(policy),
            source=source,
            destination=destination,
            symbol="renamex_np",
        )
        capability_token = implementation._NATIVE_RENAME_CAPABILITY.set(forged)
        try:
            with pytest.raises(
                implementation.RehearsalV22Error,
                match=r"non-reentrant",
            ):
                implementation._native_rename_exclusive_call(
                    policy,
                    source,
                    destination,
                )
        finally:
            implementation._NATIVE_RENAME_CAPABILITY.reset(capability_token)
            implementation._TEMP_AUTHORITY.reset(authority_token)
        assert source.is_dir()
        assert not destination.exists()
        assert implementation._NATIVE_RENAME_CAPABILITY.get() is None
        closure = inspect.getclosurevars(
            implementation._native_rename_capability_is_issued
        ).nonlocals
        assert closure["registry"] == ()


def test_validator_positive_native_issuance_renames_once_and_restores_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _validator_kernel_policy_scope(
        tmp_path,
        monkeypatch,
        label="positive-native",
    ) as (implementation, policy, _write_root, native_root):
        source = native_root / "source-success"
        destination = native_root / "destination-success"
        source.mkdir(mode=0o700)
        authority_token = implementation._TEMP_AUTHORITY.set(native_root)
        try:
            gc_before = gc.isenabled()
            signal_mask_before = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            return_code, observed_errno = implementation._native_rename_exclusive_call(
                policy,
                source,
                destination,
            )
            signal_mask_after = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        finally:
            implementation._TEMP_AUTHORITY.reset(authority_token)
        assert (return_code, observed_errno) == (0, 0)
        assert destination.is_dir()
        assert not source.exists()
        assert gc.isenabled() is gc_before
        assert signal_mask_after == signal_mask_before
        assert implementation._NATIVE_RENAME_CAPABILITY.get() is None
        closure = inspect.getclosurevars(
            implementation._native_rename_capability_is_issued
        ).nonlocals
        assert closure["registry"] == ()


def test_validator_python_monitoring_cannot_observe_issued_capability_window(
    tmp_path: Path,
) -> None:
    effect = tmp_path / "monitoring-borrow-effect.bin"
    code = f"""
import importlib
import inspect
import os
import sys

target = {effect.as_posix()!r}
module_name = {IMPLEMENTATION_MODULE!r}
observed_capability_windows = 0

def callback(*_args):
    global observed_capability_windows
    module = sys.modules.get(module_name)
    if module is None:
        return
    for name in ("_OPENAT_WRITE_CAPABILITY", "_NATIVE_RENAME_CAPABILITY"):
        capability = getattr(module, name, None)
        if capability is not None and capability.get() is not None:
            observed_capability_windows += 1
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)

sys.monitoring.use_tool_id(5, "p4.2a-capability-negative")
sys.monitoring.register_callback(5, sys.monitoring.events.LINE, callback)
sys.monitoring.set_events(5, sys.monitoring.events.LINE)
module = importlib.import_module(module_name)
blocked = []
for function_name in ("_open_exclusive_at_issued", "_native_rename_exclusive_call"):
    guard = inspect.getclosurevars(getattr(module, function_name)).nonlocals[
        "disable_runtime_callbacks"
    ]
    try:
        guard()
    except module.RehearsalV22Error:
        blocked.append(function_name)
print(",".join(blocked) + "|" + str(observed_capability_windows))
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": PROJECT_ROOT.as_posix(),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stderr == ""
    blocked, observed = completed.stdout.strip().rsplit("|", 1)
    assert blocked.split(",") == [
        "_open_exclusive_at_issued",
        "_native_rename_exclusive_call",
    ]
    assert observed == "0"
    assert not effect.exists()


def test_validator_raw_native_collision_preserves_both_trees(
    tmp_path: Path,
) -> None:
    root = tmp_path / "renameatx-collision"
    source = root / "staging"
    destination = root / "published"
    source.mkdir(parents=True, mode=0o700)
    destination.mkdir(mode=0o700)
    _private_file(source / "source.bin", b"source\n")
    _private_file(destination / "destination.bin", b"destination\n")
    before = (_tree_fingerprint(source), _tree_fingerprint(destination))
    code = f"""
import ctypes
import errno
import os

root = {root.as_posix()!r}
descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
try:
    libc = ctypes.CDLL(None, use_errno=True)
    renameatx_np = libc.renameatx_np
    renameatx_np.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameatx_np.restype = ctypes.c_int
    ctypes.set_errno(0)
    rc = renameatx_np(descriptor, b"staging", descriptor, b"published", ctypes.c_uint(4))
    observed = ctypes.get_errno()
finally:
    os.close(descriptor)
if rc != -1 or observed != errno.EEXIST:
    raise SystemExit(f"unexpected renameatx_np collision: {{rc}}/{{observed}}")
"""
    subprocess.run(
        [sys.executable, "-B", "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert source.is_dir()
    assert destination.is_dir()
    assert (_tree_fingerprint(source), _tree_fingerprint(destination)) == before


def test_bundle_and_release_common_pass_rechecks_mirror_before_any_active_replay() -> None:
    validator = _validator_module()
    bundle_source = inspect.getsource(validator._validate_common_bundle_once)
    active_bundle_source = inspect.getsource(validator._validate_active_bundle_once)
    release_source = inspect.getsource(validator._validate_release_once)
    public_bundle_source = inspect.getsource(validator.validate_bundle)
    assert bundle_source.index("_validate_second_copy_history(") < bundle_source.index(
        "return ValidatedBundle("
    )
    assert "_validate_common_bundle_once(" in active_bundle_source
    assert "_active_replay_validated_bundle(" not in active_bundle_source
    assert release_source.index("_validate_active_bundle_once(") < release_source.index(
        "_active_replay_validated_bundle("
    )
    assert public_bundle_source.index("_validate_active_bundle_once(") < public_bundle_source.index(
        "_active_replay_validated_bundle("
    )
    active_replay_source = inspect.getsource(validator._active_replay_validated_bundle)
    assert active_replay_source.index("_validate_second_copy_history(") < (
        active_replay_source.index("_authorized_bundle_directory(")
    )
    assert active_replay_source.index("_validate_second_copy_history(") < (
        active_replay_source.index("_active_replay_selected_pipeline(")
    )


def test_post_bundle_mirror_deletion_blocks_release_replay_without_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _series_2_mirror_fixture(tmp_path)
    validator = fixture["validator"]
    binding = fixture["binding"]
    bundle_path = tmp_path / "bundle.json"
    _private_file(bundle_path, b"{}\n")
    validated = validator.ValidatedBundle(
        document={},
        payload=b"{}\n",
        path=bundle_path,
        implementation_commit="3" * 40,
        archives=SimpleNamespace(),
        history=fixture["replay"],
        mirror_receipts=(),
    )
    reached: list[str] = []

    def forbidden_authorize(**_kwargs: object) -> Path:
        reached.append("authorized")
        return bundle_path.parent

    def forbidden_replay(**_kwargs: object) -> None:
        reached.append("replayed")

    monkeypatch.setattr(validator, "_authorized_bundle_directory", forbidden_authorize)
    monkeypatch.setattr(validator, "_active_replay_selected_pipeline", forbidden_replay)
    (binding.secondary_receipt_root / fixture["receipt_name"]).unlink()
    before = tuple(
        _tree_fingerprint(path)
        for path in (
            binding.ledger_root,
            binding.secondary_snapshot_root,
            binding.primary_receipt_root,
            binding.secondary_receipt_root,
            bundle_path,
        )
    )
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"paired mirror receipt bytes differ",
    ):
        validator._active_replay_validated_bundle(
            validated=validated,
            binding=binding,
            raw_binding=object(),
            execution_context=None,
            published_release_revalidation=True,
        )
    assert reached == []
    assert (
        tuple(
            _tree_fingerprint(path)
            for path in (
                binding.ledger_root,
                binding.secondary_snapshot_root,
                binding.primary_receipt_root,
                binding.secondary_receipt_root,
                bundle_path,
            )
        )
        == before
    )


def test_series_2_epoch_seven_recovery_is_disclosed_without_void3_or_completion_surface() -> None:
    validator = _validator_module()
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    for name in (
        "complete_mirror",
        "mirror_completion_authorization",
        "void_epoch_3",
    ):
        assert not hasattr(validator, name)
        assert not hasattr(implementation, name)
    assert hasattr(validator, "validate_recovered_bundle")
    assert hasattr(validator, "validate_recovered_release_authorization")
    assert hasattr(implementation, "_execute_authorized_bundle_recovery")
    assert hasattr(implementation, "consume_recovered_release_authorization")
    validator_source = inspect.getsource(validator)
    implementation_source = inspect.getsource(implementation)
    assert "--recover-sealed-bundle" in implementation_source
    assert "--consume-recovered-release" in implementation_source
    assert "PASSIVE_RECOVERED_BUNDLE" in validator_source
    assert "PASSIVE_RECOVERED_RELEASE" in validator_source


@pytest.mark.parametrize(
    "mutation",
    (
        "old-token",
        "old-root",
        "ordinal-one-history-root",
        "ordinal-two-history-root",
        "attempt-one-evidence",
        "attempt-two-started",
        "attempt-two-candidate",
        "attempt-two-terminal",
        "lineage-enters-new-root",
        "typed-bool-to-int",
    ),
)
def test_series_2_lost_history_summary_is_exact_typed_deep_equality(
    mutation: str,
) -> None:
    validator = _validator_module()
    amendment = json.loads((PROJECT_ROOT / SERIES_2_PREREGISTRATION_RELATIVE).read_bytes())
    summary = amendment["part_2_complete_lost_series_digest_history"]
    assert validator._validate_series_2_lost_history_summary(summary) == summary
    tampered = copy.deepcopy(summary)
    if mutation == "old-token":
        tampered["old_series_token_sha256"] = "0" * 64
    elif mutation == "old-root":
        tampered["old_ledger_root"] = "/private/tmp/forged-ledger"
    elif mutation == "ordinal-one-history-root":
        tampered["history_root_after_ordinal_1"] = "0" * 64
    elif mutation == "ordinal-two-history-root":
        tampered["history_root_after_ordinal_2"] = "0" * 64
    elif mutation == "attempt-one-evidence":
        tampered["attempt_1"]["evidence_tree_root_sha256"] = "0" * 64
    elif mutation == "attempt-two-started":
        tampered["attempt_2"]["started_sha256"] = "0" * 64
    elif mutation == "attempt-two-candidate":
        tampered["attempt_2"]["candidate_sha256"] = "0" * 64
    elif mutation == "attempt-two-terminal":
        tampered["attempt_2"]["terminal_sha256"] = "0" * 64
    elif mutation == "lineage-enters-new-root":
        tampered["series_1_digests_enter_series_2_attempt_history_root"] = True
    else:
        tampered["series_1_outcome_stands"] = 1
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"complete lost-series digest history drifted",
    ):
        validator._validate_series_2_lost_history_summary(tampered)


@pytest.mark.parametrize(
    ("binding_name", "field", "replacement"),
    (
        ("loss_incident", "path", "docs/phase4/reports/forged-loss.json"),
        ("loss_incident", "commit", "0" * 40),
        ("loss_incident", "sha256", "0" * 64),
        ("loss_incident", "bytes", 6423),
        ("owner_decision", "path", "docs/phase4/reports/forged-decision.json"),
        ("owner_decision", "commit", "0" * 40),
        ("owner_decision", "sha256", "0" * 64),
        ("owner_decision", "bytes", 2348),
    ),
)
def test_validator_rejects_series_2_amendment_authority_byte_substitution_matrix(
    monkeypatch: pytest.MonkeyPatch,
    binding_name: str,
    field: str,
    replacement: object,
) -> None:
    validator = _validator_module()
    original_payload = (PROJECT_ROOT / SERIES_2_PREREGISTRATION_RELATIVE).read_bytes()
    original_amendment = json.loads(original_payload)
    amendment = copy.deepcopy(original_amendment)
    amendment["part_1_authority_loss_and_owner_decision_bindings"][binding_name][field] = (
        replacement
    )
    expected = copy.deepcopy(original_amendment)
    expected["part_1_authority_loss_and_owner_decision_bindings"][binding_name][field] = replacement
    assert amendment == expected
    assert (
        amendment["part_1_authority_loss_and_owner_decision_bindings"][binding_name][field]
        != original_amendment["part_1_authority_loss_and_owner_decision_bindings"][binding_name][
            field
        ]
    )
    tampered_payload = validator._canonical_json_bytes(amendment)
    assert tampered_payload != original_payload
    original_unique_a_authority = validator._unique_a_authority
    observed_references: list[str] = []
    substituted_references: list[str] = []

    def substituted_amendment(
        root: Path,
        reference: Any,
        *,
        require_worktree: bool,
    ) -> bytes:
        path = reference.get("path")
        assert isinstance(path, str)
        observed_references.append(path)
        if path == SERIES_2_PREREGISTRATION_RELATIVE.as_posix():
            substituted_references.append(path)
            return tampered_payload
        return original_unique_a_authority(
            root,
            reference,
            require_worktree=require_worktree,
        )

    monkeypatch.setattr(validator, "_unique_a_authority", substituted_amendment)
    execution_head = (
        _fixture_git(validator, PROJECT_ROOT, "rev-parse", "HEAD").decode("ascii").strip()
    )
    real_paths_before = _all_real_path_fingerprints()
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=rf"series-2 "
        rf"{'loss incident' if binding_name == 'loss_incident' else 'owner decision'} "
        rf"binding drifted",
    ):
        validator._validate_series_2_preregistration(
            project_root=PROJECT_ROOT,
            execution_head=execution_head,
        )
    assert observed_references
    assert substituted_references == [SERIES_2_PREREGISTRATION_RELATIVE.as_posix()]
    assert _all_real_path_fingerprints() == real_paths_before


@pytest.mark.parametrize(
    ("field_path", "replacement"),
    (
        (("attempt_2", "ordinal"), 2.0),
        (("attempt_2", "outcome"), "FAILED"),
        (("attempt_2", "implementation_epoch"), 5),
        (("attempt_2", "implementation_commit"), "0" * 40),
        (("attempt_2", "started_sha256"), "0" * 64),
        (("attempt_2", "candidate_sha256"), "0" * 64),
        (("attempt_2", "terminal_sha256"), "0" * 64),
        (("attempt_2", "evidence_tree_root_sha256"), "0" * 64),
        (("attempt_2", "run_a_and_run_b_root_sha256"), "0" * 64),
        (("attempt_2", "candidate_content_root_sha256"), "0" * 64),
        (("attempt_2", "selected_control_root_sha256"), "0" * 64),
        (("attempt_2", "q_r_b_commits", 0), "0" * 40),
        (("attempt_2", "q_r_b_commits", 1), "0" * 40),
        (("attempt_2", "q_r_b_commits", 2), "0" * 40),
        (("attempt_2", "execution_authorization_sha256"), "0" * 64),
    ),
)
def test_series_2_attempt_2_every_digest_root_and_qrb_binding_is_typed_exact(
    field_path: tuple[str | int, ...],
    replacement: object,
) -> None:
    validator = _validator_module()
    amendment = json.loads((PROJECT_ROOT / SERIES_2_PREREGISTRATION_RELATIVE).read_bytes())
    summary = amendment["part_2_complete_lost_series_digest_history"]
    tampered = copy.deepcopy(summary)
    cursor: Any = tampered
    for component in field_path[:-1]:
        cursor = cursor[component]
    cursor[field_path[-1]] = replacement
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"complete lost-series digest history drifted",
    ):
        validator._validate_series_2_lost_history_summary(tampered)


def test_validator_revalidates_the_landed_series_2_amendment_end_to_end() -> None:
    validator = _validator_module()
    execution_head = (
        _fixture_git(validator, PROJECT_ROOT, "rev-parse", "HEAD").decode("ascii").strip()
    )
    assert (
        validator._validate_series_2_preregistration(
            project_root=PROJECT_ROOT,
            execution_head=execution_head,
        )
        == validator.SERIES_2_PREREGISTRATION_COMMIT
    )


def test_series_2_active_schemas_compile_and_bind_only_new_visible_identity() -> None:
    validator = _validator_module()
    active_bundle, active_release = _series_2_schemas()
    assert len(validator.SERIES_2_BUNDLE_SCHEMA_DELTA_POINTERS) == 7
    assert len(validator.SERIES_2_RELEASE_SCHEMA_DELTA_POINTERS) == 6
    for schema in (active_bundle, active_release):
        Draft202012Validator.check_schema(schema)
        binding_schema = _definition_validator(schema, "executionBinding")
        branch = schema["$defs"]["executionBinding"]["oneOf"][0]
        official = {field: branch["properties"][field]["const"] for field in branch["required"]}
        assert official["series_token_sha256"] == (validator.SERIES_2_REGISTERED_SERIES_TOKEN)
        assert official["ledger_root"] == validator.SERIES_2_PRIMARY_LEDGER_ROOT.as_posix()
        assert list(binding_schema.iter_errors(official)) == []
        old_identity = {
            **official,
            "series_token_sha256": validator.REGISTERED_SERIES_TOKEN,
            "ledger_root": validator.SERIES_2_LEGACY_LEDGER_ROOT.as_posix(),
        }
        assert list(binding_schema.iter_errors(old_identity)) != []
    active_lineage = active_bundle["$defs"]["lineage"]["properties"]
    assert (
        active_lineage["preregistration"]["allOf"][1]["properties"]["path"]["const"]
        == SERIES_2_PREREGISTRATION_RELATIVE.as_posix()
    )


@pytest.mark.parametrize("mutation", ("old-token", "old-ledger-root"))
def test_validator_binding_accepts_new_visible_identity_and_rejects_old_without_writes(
    mutation: str,
) -> None:
    validator = _validator_module()
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    raw = implementation.derive_execution_binding(
        project_root=validator.REGISTERED_PROJECT_ROOT,
    )
    view = validator._binding_view(raw)
    assert view.series_token_sha256 == validator.SERIES_2_REGISTERED_SERIES_TOKEN
    assert view.ledger_root == validator.SERIES_2_PRIMARY_LEDGER_ROOT
    assert view.primary_series_container == validator.SERIES_2_PRIMARY_SERIES_CONTAINER
    assert view.secondary_snapshot_root == validator.SERIES_2_SECONDARY_SNAPSHOT_ROOT
    forged = (
        replace(raw, series_token_sha256=validator.REGISTERED_SERIES_TOKEN)
        if mutation == "old-token"
        else replace(raw, ledger_root=validator.SERIES_2_LEGACY_LEDGER_ROOT)
    )
    before = _all_real_path_fingerprints()
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"binding derivation drifted",
    ):
        validator._binding_view(forged)
    assert _all_real_path_fingerprints() == before


@pytest.mark.parametrize(
    ("legacy_path_name", "public_api"),
    (
        ("lost-ledger", "bundle"),
        ("lost-ledger", "release"),
        ("retired-v2.1-claim", "bundle"),
        ("retired-v2.1-claim", "release"),
    ),
)
def test_official_public_validation_rejects_each_legacy_path_before_any_read(
    monkeypatch: pytest.MonkeyPatch,
    legacy_path_name: str,
    public_api: str,
) -> None:
    validator = _validator_module()
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    raw = implementation.derive_execution_binding(
        project_root=validator.REGISTERED_PROJECT_ROOT,
    )
    target = (
        validator.SERIES_2_LEGACY_LEDGER_ROOT
        if legacy_path_name == "lost-ledger"
        else validator.SERIES_2_RETIRED_V2_1_CLAIM
    )
    expected_message = (
        "lost series ledger" if legacy_path_name == "lost-ledger" else "retired v2.1 claim"
    )
    original_lexists = validator._validator_os.path.lexists
    observed_reads: list[object] = []

    def fake_lexists(path: object) -> bool:
        return Path(path).absolute() == target or original_lexists(path)

    def forbidden_read(*args: object, **kwargs: object) -> bytes:
        observed_reads.append((args, kwargs))
        raise AssertionError("legacy-path gate allowed an evidence read")

    monkeypatch.setattr(validator._validator_os.path, "lexists", fake_lexists)
    monkeypatch.setattr(validator, "_assert_official_runtime_before_read", lambda _root: None)
    monkeypatch.setattr(
        implementation,
        "derive_execution_binding",
        lambda **_kwargs: raw,
    )
    monkeypatch.setattr(validator, "_regular_bytes", forbidden_read)
    before = _all_real_path_fingerprints()
    with pytest.raises(validator.RehearsalV22ValidationError, match=expected_message):
        if public_api == "bundle":
            validator.validate_bundle(
                project_root=validator.REGISTERED_PROJECT_ROOT,
                bundle_path=validator.REGISTERED_PROJECT_ROOT
                / validator.REGISTERED_DESTINATION_RELATIVE
                / validator.BUNDLE_FILENAME,
            )
        else:
            validator.validate_release_authorization(
                project_root=validator.REGISTERED_PROJECT_ROOT,
                receipt_path=validator.REGISTERED_PROJECT_ROOT / validator.RELEASE_RELATIVE,
            )
    assert observed_reads == []
    assert _all_real_path_fingerprints() == before


def test_series_2_release_cross_validation_resolves_epoch_six_by_explicit_key() -> None:
    validator = _validator_module()
    bundle, receipt = _minimal_cross_valid_bundle_receipt()
    epoch_five = bundle["implementation_epochs"][0]
    epoch_five["first_attempt_ordinal"] = 1
    epoch_five["last_attempt_ordinal"] = 1
    epoch_six = copy.deepcopy(epoch_five)
    epoch_six.update(
        {
            "epoch": 6,
            "implementation_commit": "a" * 40,
            "first_attempt_ordinal": 2,
            "last_attempt_ordinal": 3,
        }
    )
    bundle["implementation_epochs"] = [epoch_five, epoch_six]
    record_epochs = (5, 6, 6)
    for record, epoch_number in zip(
        bundle["attempt_history"]["records"],
        record_epochs,
        strict=True,
    ):
        record["implementation_epoch"] = epoch_number
    for outcome, epoch_number in zip(
        receipt["owner_authorization"]["acknowledged_outcomes"],
        record_epochs,
        strict=True,
    ):
        outcome["implementation_epoch"] = epoch_number
    receipt["implementation_epochs"] = [
        {
            "epoch": epoch["epoch"],
            "implementation_commit": epoch["implementation_commit"],
            "owner_surface_authorization": epoch["owner_exact_surface_authorization"],
            "independent_implementation_review": epoch["independent_implementation_review"],
            "control_merkle_root_sha256": epoch["control_merkle_root_sha256"],
            "first_attempt_ordinal": epoch["first_attempt_ordinal"],
            "last_attempt_ordinal": epoch["last_attempt_ordinal"],
        }
        for epoch in (epoch_five, epoch_six)
    ]
    receipt["lineage"]["selected_implementation_commit"] = epoch_six["implementation_commit"]
    validator._cross_validate_release(bundle=bundle, receipt=receipt)


def test_paired_receipts_cannot_synchronously_change_the_fixed_verification_time(
    tmp_path: Path,
) -> None:
    fixture = _series_2_mirror_fixture(tmp_path)
    validator = fixture["validator"]
    binding = fixture["binding"]
    receipt = copy.deepcopy(fixture["receipt"])
    receipt["verified_at_utc"] = "2026-08-23T12:00:00Z"
    payload = validator._canonical_json_bytes(receipt)
    for root in (binding.primary_receipt_root, binding.secondary_receipt_root):
        _private_file(root / fixture["receipt_name"], payload)
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"verified_at_utc drifted",
    ):
        validator._validate_second_copy_history(
            binding=binding,
            replay=fixture["replay"],
        )


@pytest.mark.parametrize(
    "outcome",
    ("INCOMPLETE_UNTERMINALIZED", "CANDIDATE_VALIDATED_AND_SELECTED"),
)
def test_independent_mirror_accepts_incomplete_and_selected_registered_shapes(
    tmp_path: Path,
    outcome: str,
) -> None:
    fixture = _series_2_mirror_fixture(tmp_path, outcome=outcome)
    validator = fixture["validator"]
    assert validator._validate_second_copy_history(
        binding=fixture["binding"],
        replay=fixture["replay"],
    ) == (fixture["receipt"],)


@pytest.mark.parametrize(
    ("outcome", "mutation"),
    (
        ("INCOMPLETE_UNTERMINALIZED", "alias"),
        ("INCOMPLETE_UNTERMINALIZED", "overlap"),
        ("CANDIDATE_VALIDATED_AND_SELECTED", "alias"),
        ("CANDIDATE_VALIDATED_AND_SELECTED", "overlap"),
    ),
)
def test_incomplete_and_selected_mirrors_reject_alias_or_overlap(
    tmp_path: Path,
    outcome: str,
    mutation: str,
) -> None:
    fixture = _series_2_mirror_fixture(tmp_path, outcome=outcome)
    validator = fixture["validator"]
    binding = fixture["binding"]
    if mutation == "alias":
        alias = tmp_path / "secondary-alias"
        alias.symlink_to(binding.secondary_series_container, target_is_directory=True)
        forged = replace(
            binding,
            secondary_series_container=alias,
            secondary_snapshot_root=alias / "SEALED-LEDGER-SNAPSHOTS-DO-NOT-DELETE",
            secondary_receipt_root=alias / "MIRROR-RECEIPTS-DO-NOT-DELETE",
        )
    else:
        forged = replace(
            binding,
            secondary_series_container=binding.primary_series_container,
            secondary_snapshot_root=binding.primary_series_container
            / "SEALED-LEDGER-SNAPSHOTS-DO-NOT-DELETE",
            secondary_receipt_root=binding.primary_receipt_root,
        )
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"aliased|overlap",
    ):
        validator._validate_second_copy_history(
            binding=forged,
            replay=fixture["replay"],
        )


@pytest.mark.parametrize("race", ("symlink", "hardlink", "in-place"))
def test_descriptor_bound_inventory_rejects_racing_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
) -> None:
    fixture = _series_2_mirror_fixture(tmp_path)
    validator = fixture["validator"]
    snapshot = fixture["snapshot"]
    target = snapshot / "attempts/000001/started.json"
    original_open = validator._validator_os.open
    original_read = validator._validator_os.read
    triggered = False
    outside = tmp_path / "racing-hardlink-source"
    if race == "hardlink":
        _private_file(outside, target.read_bytes())

    def racing_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal triggered
        if (
            race in {"symlink", "hardlink"}
            and not triggered
            and path == "started.json"
            and dir_fd is not None
        ):
            triggered = True
            target.unlink()
            if race == "symlink":
                target.symlink_to(snapshot / "series.json")
            else:
                os.link(outside, target)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    def racing_read(descriptor: int, count: int) -> bytes:
        nonlocal triggered
        chunk = original_read(descriptor, count)
        if (
            race == "in-place"
            and not triggered
            and chunk
            and validator._descriptor_path(descriptor, label="race probe") == target
        ):
            triggered = True
            target.write_bytes(b'{"in_place_substitute":true}\n')
        return chunk

    monkeypatch.setattr(validator._validator_os, "open", racing_open)
    monkeypatch.setattr(validator._validator_os, "read", racing_read)
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"aliased|hardlinked|changed|unavailable",
    ):
        validator._strict_private_tree_inventory(
            snapshot,
            label="racing mirror snapshot",
        )
    assert triggered is True


def test_mirror_validation_rereads_both_receipts_after_initial_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _series_2_mirror_fixture(tmp_path)
    validator = fixture["validator"]
    binding = fixture["binding"]
    original_inventory = validator._strict_receipt_inventory
    triggered = False

    def mutate_after_initial_receipts(
        path: Path,
        *,
        label: str,
        root_descriptor: int | None = None,
        root_before: os.stat_result | None = None,
    ) -> Any:
        nonlocal triggered
        inventory = original_inventory(
            path,
            label=label,
            root_descriptor=root_descriptor,
            root_before=root_before,
        )
        if not triggered and label == "series-2 secondary receipt root":
            triggered = True
            for receipt_root in (
                binding.primary_receipt_root,
                binding.secondary_receipt_root,
            ):
                (receipt_root / fixture["receipt_name"]).write_bytes(b"{}\n")
        return inventory

    monkeypatch.setattr(
        validator,
        "_strict_receipt_inventory",
        mutate_after_initial_receipts,
    )
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"primary receipt root changed during mirror validation",
    ):
        validator._validate_second_copy_history(
            binding=binding,
            replay=fixture["replay"],
        )
    assert triggered is True


def test_mirror_validation_rereads_snapshot_members_after_initial_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _series_2_mirror_fixture(tmp_path)
    validator = fixture["validator"]
    snapshot = fixture["snapshot"]
    target = snapshot / "attempts/000001/started.json"
    original_inventory = validator._strict_private_tree_inventory
    triggered = False

    def mutate_after_initial_snapshot(
        root: Path,
        **kwargs: Any,
    ) -> Any:
        nonlocal triggered
        inventory = original_inventory(root, **kwargs)
        if not triggered and root == snapshot and kwargs.get("root_parent_descriptor") is not None:
            triggered = True
            target.write_bytes(b'{"post_read_substitute":true}\n')
        return inventory

    monkeypatch.setattr(
        validator,
        "_strict_private_tree_inventory",
        mutate_after_initial_snapshot,
    )
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"snapshot bytes or identity changed during mirror validation",
    ):
        validator._validate_second_copy_history(
            binding=fixture["binding"],
            replay=fixture["replay"],
        )
    assert triggered is True


EPOCH_7_COMPANION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-epoch7-design-review-r2-and-companion-20260827.json"
)
EPOCH_7_COMPANION_SHA256 = "43651a31b24088b0ec676bdf2fee3c0f54629471ab29d5e5164e2b2e308e7c9d"
EPOCH_7_CONTRACT_FIELDS = {
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
RECOVERED_PUBLICATION_CAPABILITY_FIELDS = (
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
RECOVERED_MODE_FORBIDDEN_ACTIVE_SYMBOLS = {
    "_active_replay_validated_bundle",
    "_active_replay_selected_pipeline",
    "_official_validator_replay_scope",
    "replay_selected_pipeline",
    "_execute_pipeline_inner",
}


def _module_level_call_graph(payload: bytes) -> dict[str, set[str]]:
    tree = ast.parse(payload)
    graph: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls: set[str] = set()
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            if isinstance(child.func, ast.Name):
                calls.add(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                calls.add(child.func.attr)
        graph[node.name] = calls
    return graph


def _reachable_calls(graph: dict[str, set[str]], root: str) -> set[str]:
    assert root in graph
    pending = [root]
    reached: set[str] = set()
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        for child in graph.get(current, set()):
            if child not in reached:
                pending.append(child)
    return reached


def test_epoch_7_companion_and_contract_are_independently_byte_bound() -> None:
    validator = _validator_module()
    registered_root = Path(validator.REGISTERED_PROJECT_ROOT)
    payload = (registered_root / EPOCH_7_COMPANION_RELATIVE).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == EPOCH_7_COMPANION_SHA256
    document = json.loads(payload)
    contract = document["epoch_7_recovery_contract"]
    assert set(contract) == EPOCH_7_CONTRACT_FIELDS
    assert contract["schema_version"] == "p4.2a-v2-2-series2-epoch7-recovery-contract-v1"
    assert type(contract["implementation_epoch"]) is int
    assert contract["implementation_epoch"] == 7
    head = subprocess.run(
        ["/usr/bin/git", "-C", registered_root.as_posix(), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    signature = inspect.signature(validator.validate_epoch_7_recovery_contract)
    if "execution_head" in signature.parameters:
        observed = validator.validate_epoch_7_recovery_contract(
            registered_root,
            execution_head=head,
        )
    else:
        observed = validator.validate_epoch_7_recovery_contract(registered_root)
    assert observed == contract


def test_epoch_7_historical_anchor_is_recomputed_against_the_bound_sealed_root() -> None:
    validator = _validator_module()
    control_root = "11" * 32
    history_root = "22" * 32
    live_root = "33" * 32
    implementation_commit = "44" * 20
    replay = validator.HistoryReplay(
        records=({}, {}),
        source_records=(
            ({}, None, {}),
            ({}, {"control_surface_root_sha256": control_root}, {}),
        ),
        started_count=2,
        failed_count=1,
        incomplete_count=0,
        selected_attempt_ordinal=2,
        selected_implementation_epoch=6,
        selected_implementation_commit=implementation_commit,
        history_root_sha256=history_root,
        live_ledger_root_sha256=live_root,
        archive_merkle_root_sha256="55" * 32,
        live_payloads={},
        live_identities={},
        archive_payloads={},
    )
    anchor = validator.HistoricalSelectedAnchor(
        implementation_epoch=6,
        implementation_commit=implementation_commit,
        control_merkle_root_sha256=control_root,
        history_root_sha256=history_root,
        live_ledger_root_sha256=live_root,
        selected_attempt_ordinal=2,
        require_current=False,
    )
    assert validator._historical_selected_anchor(replay, anchor) == anchor
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match="bound anchor",
    ):
        validator._historical_selected_anchor(
            replay,
            replace(anchor, history_root_sha256="66" * 32),
        )
    source = inspect.getsource(validator._historical_selected_anchor)
    assert "HISTORICAL_SELECTED_EPOCH" not in source
    assert "SEALED_SERIES_HISTORY_ROOT_SHA256" not in source
    assert "SEALED_SERIES_LIVE_ROOT_SHA256" not in source
    assert "HISTORICAL_SELECTED_IMPLEMENTATION_COMMIT" not in source


def test_epoch_7_recovery_reuses_validated_mirror_receipts_and_one_initial_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _validator_module()
    assert tuple(validator.ValidatedBundle.__dataclass_fields__)[-1] == "mirror_receipts"
    common_source = inspect.getsource(validator._validate_common_bundle_once)
    assert "mirror_receipts = _validate_second_copy_history(" in common_source
    assert "mirror_receipts=mirror_receipts" in common_source
    historical_source = inspect.getsource(
        validator._validate_historical_full_downstream_replay_evidence
    )
    for token in (
        'nested_fields.get("sealed_series")',
        'nested_fields.get("selected_files")',
        'nested_fields.get("selected_file_reference")',
        'nested_fields.get("sealed_mirror")',
        "validated.mirror_receipts[-1]",
        '"receipt_sha256": _sha256(latest_receipt_payload)',
        '"inventory_sha256": latest_receipt.get("primary_inventory_sha256")',
        '_require_equal(sealed, expected_sealed_series, "recovery sealed series binding")',
    ):
        assert token in historical_source
    initial_source = inspect.getsource(validator._live_anchor_from_recovery_governance)
    assert initial_source.count("_real_lineage_census(") == 1
    assert "_validate_live_execution_anchor_identity(" in initial_source
    assert "_assert_git_census_state_unchanged(" in initial_source
    assert "live_control" in initial_source
    assert tuple(validator.ArchiveReplay.__dataclass_fields__)[-2] == ("selected_control_cache")
    archive_source = inspect.getsource(validator._validate_archives)
    epoch_source = inspect.getsource(validator._validate_implementation_epochs)
    assert "selected_control_surface = _validate_control_archive(" in archive_source
    assert "selected_control_cache=selected_control_cache" in archive_source
    assert "archives.selected_control_cache" in epoch_source
    assert "_historical_control_cache_for_epoch(" in epoch_source
    selector_source = inspect.getsource(validator._historical_control_cache_for_epoch)
    assert "replay.selected_implementation_epoch != 6" in selector_source
    assert "epoch_number != 6" in selector_source
    assert "selected_require_current" in selector_source
    recovery_source = inspect.getsource(validator.validate_recovered_bundle)
    release_source = inspect.getsource(validator.validate_recovered_release_authorization)
    assert "_validate_live_execution_anchor(" in recovery_source
    assert "final_census = _validate_live_execution_anchor(" in release_source
    assert "cached_current_control=live_control" in recovery_source
    assert "cached_current_control=live_control" in release_source
    assert "control_pass_nonce=live_control_pass_nonce" in recovery_source
    assert "control_pass_nonce=live_control_pass_nonce" in release_source
    expected_refs = b"refs-unchanged\n"
    expected_head = "a" * 40
    observed_head = [expected_head]
    monkeypatch.setattr(validator, "_git_ref_snapshot", lambda _root: expected_refs)
    monkeypatch.setattr(
        validator,
        "_git_bytes",
        lambda _root, *_args: (observed_head[0] + "\n").encode("ascii"),
    )
    monkeypatch.setattr(
        validator,
        "_git_commit",
        lambda _root, value, _label: value,
    )
    validator._assert_git_census_state_unchanged(
        PROJECT_ROOT,
        expected_refs=expected_refs,
        expected_head=expected_head,
    )
    observed_head[0] = "b" * 40
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match="refs or HEAD changed",
    ):
        validator._assert_git_census_state_unchanged(
            PROJECT_ROOT,
            expected_refs=expected_refs,
            expected_head=expected_head,
        )


def _valid_control_surface_for_cache(
    producer: Any,
    root: Path,
    *,
    loaded_sources: tuple[str, ...] = ("member.py",),
) -> tuple[Any, Path, tuple[bytes, bytes]]:
    member = root / "member.py"
    member_payload = b"registered member\n"
    member.write_bytes(member_payload)
    python_payload = b'{"python":"registered"}\n'
    package_payload = b'{"packages":"registered"}\n'
    rows = [
        {
            "logical_name": "member.py",
            "bundle_relative_path": "archive/control-surface/root/repository/member.py",
            "source_kind": "frozen_control",
            "repository_path": "member.py",
            "bytes": len(member_payload),
            "sha256": producer._sha256(member_payload),
        },
        {
            "logical_name": "packages",
            "bundle_relative_path": "archive/control-surface/root/runtime/packages.json",
            "source_kind": "package_inventory",
            "repository_path": None,
            "bytes": len(package_payload),
            "sha256": producer._sha256(package_payload),
        },
        {
            "logical_name": "python",
            "bundle_relative_path": "archive/control-surface/root/runtime/python.json",
            "source_kind": "python_runtime",
            "repository_path": None,
            "bytes": len(python_payload),
            "sha256": producer._sha256(python_payload),
        },
    ]
    payloads = {
        rows[0]["bundle_relative_path"]: member_payload,
        rows[1]["bundle_relative_path"]: package_payload,
        rows[2]["bundle_relative_path"]: python_payload,
    }
    manifest = producer._canonical_json_bytes(
        {"schema_version": producer.CONTROL_MANIFEST_SCHEMA, "files": rows}
    )
    merkle_payloads = {**payloads, "archive/control-surface/manifest.json": manifest}
    return (
        producer.ControlSurface(
            implementation_commit="a" * 40,
            records=tuple(rows),
            payloads=payloads,
            manifest_payload=manifest,
            merkle_root_sha256=producer._generic_merkle_root(merkle_payloads),
            ast_closure_paths=("member.py",),
            loaded_repository_sources=loaded_sources,
            python_inventory=python_payload,
            package_inventory=package_payload,
        ),
        member,
        (python_payload, package_payload),
    )


def _freeze_live_control_cache(
    module: Any,
    *,
    root: Path,
    control: Any,
    pass_nonce: object,
    execution_head: str,
    ref_snapshot_sha256: str,
    census_sha256: str,
) -> Any:
    kwargs = {
        "implementation_commit": control.implementation_commit,
        "execution_head": execution_head,
        "ref_snapshot_sha256": ref_snapshot_sha256,
        "lineage_census_sha256": census_sha256,
        "pass_nonce": pass_nonce,
        "control": control,
    }
    if module.__name__ == VALIDATOR_MODULE:
        kwargs.update(pass_kind="LIVE_CURRENT", selected_epoch=None)
    return module._freeze_control_surface_cache(root, **kwargs)


@pytest.mark.parametrize(
    "drift",
    (
        "execution_epoch",
        "refs",
        "head",
        "control_member",
        "runtime_inventory",
        "implementation_module",
        "validator_module",
        "authority_worktree",
    ),
)
def test_epoch_7_publication_guard_rejects_every_mutable_live_span(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    drift: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    refs = b"refs/heads/main\0" + b"a" * 40 + b"\n"
    refs_state = [refs]
    head = "a" * 40
    head_state = [head]
    authority_path = tmp_path / "authority.json"
    authority_payload = b"authority\n"
    authority_path.write_bytes(authority_payload)
    control, member, runtime = _valid_control_surface_for_cache(implementation, tmp_path)
    loaded_state = [("member.py",)]
    runtime_state = [runtime]
    module_payloads = {
        implementation.IMPLEMENTATION_RELATIVE.as_posix(): b"producer",
        implementation.VALIDATOR_RELATIVE.as_posix(): b"validator",
    }
    execution_epoch = {"epoch": 7, "binding": "registered"}
    expected_execution_epoch_payload = implementation._canonical_json_bytes(execution_epoch)
    authorization = SimpleNamespace(execution_epoch=execution_epoch)
    start_census = {
        "schema_version": "synthetic-census",
        "execution_head": head,
        "ref_snapshot_before_sha256": implementation._sha256(refs),
        "ref_snapshot_after_sha256": implementation._sha256(refs),
        "rows": [
            {
                "path": authority_path.relative_to(tmp_path).as_posix(),
                "worktree_sha256": implementation._sha256(authority_payload),
            }
        ],
    }
    live_anchor = SimpleNamespace(
        execution_head=head,
        implementation_commit=control.implementation_commit,
        real_lineage_census_sha256=implementation._census_reference(start_census)[
            "canonical_json_sha256"
        ],
        control_surface=control,
        loaded_module_sha256={
            relative: implementation._sha256(payload)
            for relative, payload in module_payloads.items()
        },
    )
    control_pass_nonce = object()
    control_cache = _freeze_live_control_cache(
        implementation,
        root=tmp_path,
        control=control,
        pass_nonce=control_pass_nonce,
        execution_head=head,
        ref_snapshot_sha256=implementation._sha256(refs),
        census_sha256=live_anchor.real_lineage_census_sha256,
    )
    monkeypatch.setattr(
        implementation,
        "_git_ref_snapshot",
        lambda _root: (
            refs_state[0],
            implementation._sha256(refs_state[0]),
            len(refs_state[0].splitlines()),
        ),
    )
    monkeypatch.setattr(
        implementation,
        "_current_execution_head",
        lambda _root: head_state[0],
    )
    monkeypatch.setattr(
        implementation,
        "validate_epoch_7_recovery_contract",
        lambda _root, **_kwargs: {},
    )
    monkeypatch.setattr(
        implementation,
        "_classify_loaded_module_origins",
        lambda _root: frozenset(loaded_state[0]),
    )
    monkeypatch.setattr(
        implementation,
        "_runtime_inventory",
        lambda _root: runtime_state[0],
    )
    monkeypatch.setattr(
        implementation,
        "validate_implementation_blob",
        lambda _root, _commit, relative, **_kwargs: module_payloads[relative],
    )
    implementation._validate_live_execution_publication_guard(
        SimpleNamespace(project_root=tmp_path),
        authorization=authorization,
        live_anchor=live_anchor,
        start_census=start_census,
        expected_execution_epoch_payload=expected_execution_epoch_payload,
        control_pass_nonce=control_pass_nonce,
        control_cache=control_cache,
    )
    if drift == "execution_epoch":
        execution_epoch["binding"] = "changed"
    elif drift == "refs":
        refs_state[0] = b"refs/heads/main\0" + b"c" * 40 + b"\n"
    elif drift == "head":
        head_state[0] = "c" * 40
    elif drift == "control_member":
        member.write_bytes(b"changed member\n")
    elif drift == "runtime_inventory":
        runtime_state[0] = (b"changed python\n", runtime_state[0][1])
    elif drift == "implementation_module":
        module_payloads[implementation.IMPLEMENTATION_RELATIVE.as_posix()] = b"changed"
    elif drift == "validator_module":
        module_payloads[implementation.VALIDATOR_RELATIVE.as_posix()] = b"changed"
    else:
        authority_path.write_bytes(b"changed\n")
    expected_message = {
        "execution_epoch": "recovery execution epoch mutated before publication",
        "refs": "Git refs, HEAD, or recovery census changed before publication",
        "head": "Git refs, HEAD, or recovery census changed before publication",
        "control_member": "cached current control bytes drifted: member.py",
        "runtime_inventory": "cached live runtime inventory drifted",
        "implementation_module": "loaded module bytes changed before recovery publication",
        "validator_module": "loaded module bytes changed before recovery publication",
        "authority_worktree": "authority worktree bytes changed before recovery publication",
    }[drift]
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=re.escape(expected_message),
    ):
        implementation._validate_live_execution_publication_guard(
            SimpleNamespace(project_root=tmp_path),
            authorization=authorization,
            live_anchor=live_anchor,
            start_census=start_census,
            expected_execution_epoch_payload=expected_execution_epoch_payload,
            control_pass_nonce=control_pass_nonce,
            control_cache=control_cache,
        )
    guard_source = inspect.getsource(implementation._validate_live_execution_publication_guard)
    assert "_rename_directory_exclusive(" not in guard_source


@pytest.mark.parametrize("implementation_name", ("producer", "validator"))
@pytest.mark.parametrize(
    "drift",
    (
        "commit",
        "member",
        "loaded_sources",
        "runtime",
        "cross_root",
        "stale_pass",
        "head",
        "refs",
        "census",
        "integrity",
        "current_scope",
    ),
)
def test_epoch_7_cached_control_reuses_only_immutable_bytes_and_rereads_mutable_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    implementation_name: str,
    drift: str,
) -> None:
    producer = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    control, member, runtime = _valid_control_surface_for_cache(producer, tmp_path)
    commit = "a" * 40
    head = "b" * 40
    refs_sha256 = "c" * 64
    census_sha256 = "d" * 64
    pass_nonce = object()
    loaded_state = [("member.py",)]
    runtime_state = [runtime]
    monkeypatch.setattr(
        producer,
        "_classify_loaded_module_origins",
        lambda _root: frozenset(loaded_state[0]),
    )
    if implementation_name == "producer":
        monkeypatch.setattr(producer, "_runtime_inventory", lambda _root: runtime_state[0])
        helper = producer._revalidate_cached_current_control_surface
        error = producer.RehearsalV22Error
    else:
        monkeypatch.setattr(validator, "_independent_runtime_inventory", lambda: runtime_state[0])
        helper = validator._revalidate_cached_current_control_surface
        error = validator.RehearsalV22ValidationError
    module = producer if implementation_name == "producer" else validator
    cache = _freeze_live_control_cache(
        module,
        root=tmp_path,
        control=control,
        pass_nonce=pass_nonce,
        execution_head=head,
        ref_snapshot_sha256=refs_sha256,
        census_sha256=census_sha256,
    )
    assert (
        helper(
            tmp_path,
            implementation_commit=commit,
            execution_head=head,
            ref_snapshot_sha256=refs_sha256,
            lineage_census_sha256=census_sha256,
            pass_nonce=pass_nonce,
            cache=cache,
        )
        is None
    )
    observed_commit = commit
    observed_root = tmp_path
    observed_pass_nonce = pass_nonce
    observed_head = head
    observed_refs = refs_sha256
    observed_census = census_sha256
    observed_cache = cache
    if drift == "commit":
        observed_commit = "b" * 40
    elif drift == "member":
        member.write_bytes(b"changed\n")
    elif drift == "loaded_sources":
        loaded_state[0] = ("scripts/other.py",)
    elif drift == "runtime":
        runtime_state[0] = (b"changed", b"packages")
    elif drift == "cross_root":
        observed_root = tmp_path / "other-root"
        observed_root.mkdir()
        (observed_root / "member.py").write_bytes(member.read_bytes())
    elif drift == "stale_pass":
        observed_pass_nonce = object()
    elif drift == "head":
        observed_head = "9" * 40
    elif drift == "refs":
        observed_refs = "e" * 64
    elif drift == "census":
        observed_census = "f" * 64
    elif drift == "integrity":
        observed_cache = replace(cache, records=cache.records[1:])
    else:
        tampered = replace(
            cache,
            records=(
                replace(cache.records[0], current_byte_required=False),
                *cache.records[1:],
            ),
        )
        observed_cache = replace(
            tampered,
            integrity_sha256=module._sha256(
                module._canonical_json_bytes(module._control_cache_descriptor(tampered))
            ),
        )
    with pytest.raises(error):
        helper(
            observed_root,
            implementation_commit=observed_commit,
            execution_head=observed_head,
            ref_snapshot_sha256=observed_refs,
            lineage_census_sha256=observed_census,
            pass_nonce=observed_pass_nonce,
            cache=observed_cache,
        )


@pytest.mark.parametrize("implementation_name", ("producer", "validator"))
@pytest.mark.parametrize("mutation", ("record", "payload", "manifest", "merkle"))
def test_epoch_7_control_cache_freeze_rejects_inconsistent_surface(
    tmp_path: Path,
    implementation_name: str,
    mutation: str,
) -> None:
    producer = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    control, _member, _runtime = _valid_control_surface_for_cache(producer, tmp_path)
    control = copy.deepcopy(control)
    if mutation == "record":
        control.records[0]["sha256"] = "0" * 64
    elif mutation == "payload":
        assert isinstance(control.payloads, dict)
        control.payloads.pop(control.records[0]["bundle_relative_path"])
    elif mutation == "manifest":
        control = replace(control, manifest_payload=b"{}\n")
    else:
        control = replace(control, merkle_root_sha256="0" * 64)
    module = producer if implementation_name == "producer" else validator
    error = (
        producer.RehearsalV22Error
        if implementation_name == "producer"
        else validator.RehearsalV22ValidationError
    )
    with pytest.raises(error):
        _freeze_live_control_cache(
            module,
            root=tmp_path,
            control=control,
            pass_nonce=object(),
            execution_head="b" * 40,
            ref_snapshot_sha256="c" * 64,
            census_sha256="d" * 64,
        )


@pytest.mark.parametrize("implementation_name", ("producer", "validator"))
def test_epoch_7_control_cache_is_deep_and_alias_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    implementation_name: str,
) -> None:
    producer = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    control, member, runtime = _valid_control_surface_for_cache(producer, tmp_path)
    nonce = object()
    module = producer if implementation_name == "producer" else validator
    cache = _freeze_live_control_cache(
        module,
        root=tmp_path,
        control=control,
        pass_nonce=nonce,
        execution_head="b" * 40,
        ref_snapshot_sha256="c" * 64,
        census_sha256="d" * 64,
    )
    monkeypatch.setattr(
        producer,
        "_classify_loaded_module_origins",
        lambda _root: frozenset(("member.py",)),
    )
    if implementation_name == "producer":
        monkeypatch.setattr(producer, "_runtime_inventory", lambda _root: runtime)
        helper = producer._revalidate_cached_current_control_surface
        error = producer.RehearsalV22Error
    else:
        monkeypatch.setattr(validator, "_independent_runtime_inventory", lambda: runtime)
        helper = validator._revalidate_cached_current_control_surface
        error = validator.RehearsalV22ValidationError
    control.records[0]["repository_path"] = None
    assert isinstance(control.payloads, dict)
    control.payloads.clear()
    member.write_bytes(b"drift hidden by the mutable source alias\n")
    with pytest.raises(error, match="bytes drifted"):
        helper(
            tmp_path,
            implementation_commit="a" * 40,
            execution_head="b" * 40,
            ref_snapshot_sha256="c" * 64,
            lineage_census_sha256="d" * 64,
            pass_nonce=nonce,
            cache=cache,
        )


def test_epoch_7_historical_cache_is_closed_to_passive_selected_epoch_six(
    tmp_path: Path,
) -> None:
    producer = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    control, _member, _runtime = _valid_control_surface_for_cache(
        producer,
        tmp_path,
        loaded_sources=(),
    )
    nonce = object()
    cache = validator._freeze_control_surface_cache(
        tmp_path,
        implementation_commit="a" * 40,
        execution_head="b" * 40,
        pass_kind="HISTORICAL_SELECTED_EPOCH_6",
        selected_epoch=6,
        ref_snapshot_sha256=None,
        lineage_census_sha256=None,
        pass_nonce=nonce,
        control=control,
    )
    historical = validator.HistoricalSelectedAnchor(
        implementation_epoch=6,
        implementation_commit="a" * 40,
        control_merkle_root_sha256=control.merkle_root_sha256,
        history_root_sha256="c" * 64,
        live_ledger_root_sha256="d" * 64,
        selected_attempt_ordinal=2,
        require_current=False,
    )
    live = validator.LiveExecutionAnchor(
        implementation_epoch=7,
        implementation_commit="e" * 40,
        control_merkle_root_sha256="f" * 64,
        execution_head="b" * 40,
        owner_surface_authorization={},
        independent_implementation_review={},
        landing_commit="1" * 40,
        landing_report={},
        real_lineage_census_sha256="2" * 64,
        require_current=True,
    )
    recovered = validator.RecoveredBundleValidationContext(
        mode=validator.BundleValidationMode.PASSIVE_RECOVERED_BUNDLE,
        historical_anchor=historical,
        live_anchor=live,
    )
    replay = SimpleNamespace(
        selected_implementation_epoch=6,
        selected_implementation_commit="a" * 40,
    )
    assert (
        validator._historical_control_cache_for_epoch(
            project_root=tmp_path,
            validation_context=recovered,
            replay=replay,
            epoch_number=6,
            implementation_commit="a" * 40,
            expected_merkle_root_sha256=control.merkle_root_sha256,
            execution_head="b" * 40,
            selected_require_current=False,
            control_pass_nonce=nonce,
            cache=cache,
        )
        is cache
    )
    assert (
        validator._historical_control_cache_for_epoch(
            project_root=tmp_path,
            validation_context=recovered,
            replay=replay,
            epoch_number=5,
            implementation_commit="9" * 40,
            expected_merkle_root_sha256="8" * 64,
            execution_head="b" * 40,
            selected_require_current=False,
            control_pass_nonce=nonce,
            cache=cache,
        )
        is None
    )
    active = validator.ActiveBundleValidationContext(
        mode=validator.BundleValidationMode.ACTIVE_ATTEMPT_BUNDLE,
    )
    assert (
        validator._historical_control_cache_for_epoch(
            project_root=tmp_path,
            validation_context=active,
            replay=replay,
            epoch_number=6,
            implementation_commit="a" * 40,
            expected_merkle_root_sha256=control.merkle_root_sha256,
            execution_head="b" * 40,
            selected_require_current=True,
            control_pass_nonce=nonce,
            cache=cache,
        )
        is None
    )
    with pytest.raises(validator.RehearsalV22ValidationError, match="identity or integrity"):
        validator._historical_control_cache_for_epoch(
            project_root=tmp_path,
            validation_context=recovered,
            replay=replay,
            epoch_number=6,
            implementation_commit="a" * 40,
            expected_merkle_root_sha256=control.merkle_root_sha256,
            execution_head="b" * 40,
            selected_require_current=False,
            control_pass_nonce=object(),
            cache=cache,
        )


def test_epoch_7_recovered_release_accepts_only_an_ancestor_recovery_started_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _validator_module()
    started_head = "a" * 40
    release_head = "b" * 40
    observed: list[tuple[str, str]] = []

    def is_ancestor(_root: Path, ancestor: str, descendant: str) -> bool:
        observed.append((ancestor, descendant))
        return ancestor == started_head and descendant == release_head

    monkeypatch.setattr(validator, "_git_is_ancestor", is_ancestor)
    assert (
        validator._validated_recovery_started_execution_head(
            PROJECT_ROOT,
            started_head,
            live_execution_head=release_head,
        )
        == started_head
    )
    assert observed == [(started_head, release_head)]
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match="left the current live lineage",
    ):
        validator._validated_recovery_started_execution_head(
            PROJECT_ROOT,
            "c" * 40,
            live_execution_head=release_head,
        )


def test_recovery_tree_fingerprint_commits_the_registered_empty_series_lock(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    recovered = tmp_path / "recovered"
    archive = recovered / "archive/attempt-history"
    archive.mkdir(parents=True, mode=0o700)
    lock = archive / ".series.lock"
    lock.write_bytes(b"")
    lock.chmod(0o600)
    bundle = recovered / "bundle.json"
    bundle.write_bytes(b"{}\n")
    bundle.chmod(0o600)

    observed = validator._recovery_tree_fingerprint(recovered)
    assert observed == implementation._tree_fingerprint(recovered)
    assert observed["archive/attempt-history/.series.lock"] == (
        "file:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855:0600:1"
    )

    empty_evidence = archive / "attempts/000001/evidence/empty.bin"
    empty_evidence.parent.mkdir(parents=True, mode=0o700)
    empty_evidence.write_bytes(b"")
    empty_evidence.chmod(0o600)
    changed = validator._recovery_tree_fingerprint(recovered)
    assert changed == implementation._tree_fingerprint(recovered)
    assert changed != observed
    assert (
        changed["archive/attempt-history/attempts/000001/evidence/empty.bin"]
        == observed["archive/attempt-history/.series.lock"]
    )

    alias = empty_evidence.with_name("empty-alias.bin")
    os.link(empty_evidence, alias)
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match="is not one unaliased regular file",
    ):
        validator._recovery_tree_fingerprint(recovered)


def test_recovery_container_closed_member_checks_sort_observed_and_expected_names() -> None:
    validator = _validator_module()
    source = inspect.getsource(validator._validate_durable_recovery_evidence)
    assert source.count("sorted(path.name for path in") == 2
    assert "sorted(\n        (claim_root.name, receipt_name)\n    )" in source
    assert "sorted(\n        (receipt_name, snapshot_name)\n    )" in source


def test_epoch_7_validation_modes_and_publication_capability_are_closed() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    assert [member.value for member in validator.BundleValidationMode] == [
        "ACTIVE_ATTEMPT_BUNDLE",
        "PASSIVE_RECOVERED_BUNDLE",
        "PASSIVE_RECOVERED_RELEASE",
    ]
    assert tuple(validator.RecoveredPublicationCapability.__dataclass_fields__) == (
        RECOVERED_PUBLICATION_CAPABILITY_FIELDS
    )
    assert len(validator.RecoveredPublicationCapability.__dataclass_fields__) == 40
    assert validator.RecoveredPublicationCapability.__dataclass_params__.frozen is True
    assert validator.RecoveredPublicationCapability.__slots__ == (
        RECOVERED_PUBLICATION_CAPABILITY_FIELDS
    )
    for anchor_type in (
        validator.HistoricalSelectedAnchor,
        validator.LiveExecutionAnchor,
    ):
        assert all(
            field.default is MISSING and field.default_factory is MISSING
            for field in anchor_type.__dataclass_fields__.values()
        )
    for capability_type in (
        implementation.RecoveryExecutionCapability,
        implementation.RecoveryValidatorDelegation,
        implementation.RecoveredPublicationCapability,
        implementation.RecoveredPublicationValidatorDelegation,
    ):
        assert "start_census" not in capability_type.__dataclass_fields__


def test_both_recovered_modes_are_transitively_disjoint_from_active_symbols() -> None:
    producer_graph = _module_level_call_graph((PROJECT_ROOT / IMPLEMENTATION_RELATIVE).read_bytes())
    validator_graph = _module_level_call_graph((PROJECT_ROOT / VALIDATOR_RELATIVE).read_bytes())
    producer_recovered_roots = (
        "_execute_authorized_bundle_recovery",
        "consume_recovered_release_authorization",
    )
    validator_recovered_roots = (
        "validate_recovered_bundle",
        "validate_recovered_release_authorization",
    )
    for root in producer_recovered_roots:
        reachable = _reachable_calls(producer_graph, root)
        assert RECOVERED_MODE_FORBIDDEN_ACTIVE_SYMBOLS.isdisjoint(reachable), (
            root,
            RECOVERED_MODE_FORBIDDEN_ACTIVE_SYMBOLS & reachable,
        )
    for root in validator_recovered_roots:
        reachable = _reachable_calls(validator_graph, root)
        assert RECOVERED_MODE_FORBIDDEN_ACTIVE_SYMBOLS.isdisjoint(reachable), (
            root,
            RECOVERED_MODE_FORBIDDEN_ACTIVE_SYMBOLS & reachable,
        )
        assert "_validate_common_bundle_once" in reachable
    recovered_release_reachable = _reachable_calls(
        validator_graph,
        "validate_recovered_release_authorization",
    )
    assert {
        "_validate_passive_release_receipt",
        "_cross_validate_release",
        "_git_blob",
    } <= recovered_release_reachable
    active_validator_reachable = _reachable_calls(
        validator_graph,
        "validate_release_authorization",
    )
    assert "_active_replay_validated_bundle" in active_validator_reachable
    assert "_active_replay_selected_pipeline" in active_validator_reachable
    active_producer_reachable = _reachable_calls(
        producer_graph,
        "_execute_authorized_attempt",
    )
    assert "replay_selected_pipeline" in active_producer_reachable
    assert "_execute_pipeline_inner" in active_producer_reachable


def test_epoch_7_recovery_does_not_reinterpret_amendment_absence_facts() -> None:
    amendment = json.loads((PROJECT_ROOT / SERIES_2_PREREGISTRATION_RELATIVE).read_bytes())
    rules = amendment["part_5_epoch_origin_5_and_explicit_epoch_key_rules"][
        "legacy_and_absence_rules"
    ]
    assert {
        key: rules[key]
        for key in (
            "official_series_2_bundle_emits_void_epoch_1",
            "void_epoch_3_added",
            "two_four_exception_added",
            "sealed_bundle_recovery_added",
            "recover_sealed_bundle_cli_added",
            "consume_recovered_release_cli_added",
        )
    } == {
        "official_series_2_bundle_emits_void_epoch_1": False,
        "void_epoch_3_added": False,
        "two_four_exception_added": False,
        "sealed_bundle_recovery_added": False,
        "recover_sealed_bundle_cli_added": False,
        "consume_recovered_release_cli_added": False,
    }


def test_registered_closed_series_cannot_fall_back_to_active_when_recovery_state_is_deleted() -> (
    None
):
    validator = _validator_module()
    source = inspect.getsource(validator._validate_release_once)
    registered_gate = 'if binding.mode == "REGISTERED_OFFICIAL":'
    required_error = "registered closed series requires PASSIVE_RECOVERED_RELEASE"
    assert registered_gate in source
    assert required_error in source
    assert source.index(registered_gate) < source.index("_validate_active_bundle_once(")
    registered_block = source[
        source.index(registered_gate) : source.index("_validate_active_bundle_once(")
    ]
    assert "raise RehearsalV22ValidationError(" in registered_block
    assert "SERIES_2_PRIMARY_RECOVERY_CONTAINER" not in registered_block
    assert "SERIES_2_SECONDARY_RECOVERY_CONTAINER" not in registered_block
    assert "lexists" not in registered_block


def test_epoch_7_real_lineage_census_is_independent_consistent_and_read_only() -> None:
    validator = _validator_module()
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    registered_root = Path(validator.REGISTERED_PROJECT_ROOT)
    head = subprocess.run(
        ["/usr/bin/git", "-C", registered_root.as_posix(), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    refs_before = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            registered_root.as_posix(),
            "for-each-ref",
            "--format=%(refname)%00%(objectname)",
        ],
        check=True,
        capture_output=True,
    ).stdout
    status_before = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            registered_root.as_posix(),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
        check=True,
        capture_output=True,
    ).stdout
    validator_tracker = validator._IndependentRecoveryWorkTracker()
    validator_census = validator._real_lineage_census(
        registered_root,
        execution_head=head,
        work_tracker=validator_tracker,
    )
    producer_tracker = implementation._RecoveryWorkTracker()
    producer_census = implementation._real_lineage_census(
        registered_root,
        execution_head=head,
        work_tracker=producer_tracker,
    )
    validator_git_work = validator_tracker.snapshot()["git_objects_read"]
    assert 0 < validator_git_work <= validator.RECOVERY_WORK_LIMITS["git_objects_read"]
    assert validator_tracker.git_subprocesses_started > 0
    assert validator_tracker.git_object_read_occurrences > 0
    assert (
        validator_tracker.git_subprocesses_started
        + validator_tracker.git_object_read_occurrences
        == validator_git_work
    )
    assert producer_tracker.snapshot()["git_objects_read"] > 0
    for census in (validator_census, producer_census):
        assert census["schema_version"] == "p4.2a-v2-2-real-lineage-census-v1"
        assert census["execution_head"] == head
        assert census["reference_count"] == census["row_count"]
        assert census["source_count"] == census["row_count"]
        assert census["invalid_count"] == 0
        assert census["ref_snapshot_before_sha256"] == (census["ref_snapshot_after_sha256"])
        assert census["status"] == "PASS_REAL_LINEAGE_CENSUS"
        assert census["effects"] == {
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
        }
    assert validator._canonical_json_bytes(validator_census) == (
        implementation._canonical_json_bytes(producer_census)
    )
    assert (
        subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                registered_root.as_posix(),
                "for-each-ref",
                "--format=%(refname)%00%(objectname)",
            ],
            check=True,
            capture_output=True,
        ).stdout
        == refs_before
    )
    assert (
        subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                registered_root.as_posix(),
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
        ).stdout
        == status_before
    )

    edge_seed = dict.fromkeys(validator.RECOVERY_WORK_COUNTER_FIELDS, 0)
    edge_seed["git_objects_read"] = (
        validator.RECOVERY_WORK_LIMITS["git_objects_read"] - validator_git_work
    )
    edge_tracker = validator._IndependentRecoveryWorkTracker(edge_seed)
    edge_census = validator._real_lineage_census(
        registered_root,
        execution_head=head,
        work_tracker=edge_tracker,
    )
    assert validator._canonical_json_bytes(edge_census) == (
        validator._canonical_json_bytes(validator_census)
    )
    assert edge_tracker.snapshot()["git_objects_read"] == (
        validator.RECOVERY_WORK_LIMITS["git_objects_read"]
    )
    assert (
        edge_tracker.git_subprocesses_started + edge_tracker.git_object_read_occurrences
        == validator_git_work
    )

    negative_seed = dict(edge_seed)
    negative_seed["git_objects_read"] = (
        validator.RECOVERY_WORK_LIMITS["git_objects_read"]
        - max(1, validator_git_work // 2)
    )
    negative_tracker = validator._IndependentRecoveryWorkTracker(negative_seed)
    with pytest.raises(
        validator._RecoveryWorkBoundExceeded,
        match=r"recovery work bound exceeded: git_objects_read",
    ):
        validator._real_lineage_census(
            registered_root,
            execution_head=head,
            work_tracker=negative_tracker,
        )
    assert 0 < negative_tracker.git_subprocesses_started < (
        validator_tracker.git_subprocesses_started
    )
    assert negative_tracker.git_object_read_occurrences < (
        validator_tracker.git_object_read_occurrences
    )


def test_epoch_7_lineage_registry_deduplicates_exact_refs_and_rejects_conflicts() -> None:
    validator = _validator_module()
    base = validator._base_authority_census_specs()[0]
    baseline = validator._canonical_authority_registry(())
    assert validator._canonical_authority_registry((base, base)) == baseline
    conflicting = replace(
        base,
        pinned_sha256=("0" * 64 if base.pinned_sha256 != "0" * 64 else "1" * 64),
    )
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match="authority census registry has conflicting specs",
    ):
        validator._canonical_authority_registry((conflicting,))
