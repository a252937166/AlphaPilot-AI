from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import inspect
import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable
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
V2_1_BUNDLE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_1_heldout_rehearsal_bundle.schema.json"
)
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
    "docs/phase4/reports/"
    "P4.2a-v2-2-preregistration-independent-review-20260811.json"
)
INITIAL_SIBLING_SHA256 = (
    "6707e2b3c0b2ba87712e88b59ceaed17524be2de947b764a94c8b170b2a30bb6"
)
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
            (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "addaudithook"
            )
            or (isinstance(node.func, ast.Name) and node.func.id == "addaudithook")
        )
    )


def _module_identity_subprocess(
    root: Path,
    implementation_commit: str,
    *,
    loaded_historical_module: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    interpreter = str(
        _preregistration()["exact_os_bootstrap_contract"]["python_launcher_path"]
    )
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
    duplicate_tree = _fixture_git(
        validator,
        root,
        "hash-object",
        "-t",
        "tree",
        "--literally",
        "-w",
        "--stdin",
        input_bytes=duplicate_tree_payload,
    ).strip().decode("ascii")
    duplicate_commit = _fixture_git(
        validator,
        root,
        "commit-tree",
        duplicate_tree,
        "-m",
        "duplicate path tree",
    ).strip().decode("ascii")

    malformed_tree_payload = (
        b"100644 malformed.py\0" + bytes.fromhex(first_blob.decode("ascii"))[:-1]
    )
    malformed_tree = _fixture_git(
        validator,
        root,
        "hash-object",
        "-t",
        "tree",
        "--literally",
        "-w",
        "--stdin",
        input_bytes=malformed_tree_payload,
    ).strip().decode("ascii")
    malformed_commit = _fixture_git(
        validator,
        root,
        "commit-tree",
        malformed_tree,
        "-m",
        "malformed mode tree",
    ).strip().decode("ascii")
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


def _strip_json_pointer(value: object, pointer: str, *, required: bool) -> bool:
    assert pointer.startswith("/") and pointer != "/"
    parts = [
        part.replace("~1", "/").replace("~0", "~")
        for part in pointer[1:].split("/")
    ]
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
        return set(left) == set(right) and all(
            _typed_equal(left[key], right[key]) for key in left
        )
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


def _all_real_path_fingerprints() -> tuple[
    tuple[tuple[str, str, int, str], ...] | None,
    tuple[tuple[str, str, int, str], ...] | None,
    tuple[tuple[str, str, int, str], ...] | None,
    tuple[tuple[str, str, int, str], ...] | None,
    tuple[tuple[str, str, int, str], ...] | None,
]:
    ledger, destination = _real_registered_paths()
    registered_root = Path(
        _preregistration()["exact_os_bootstrap_contract"]["repository_root"]
    )
    return (
        _tree_fingerprint(ledger),
        _tree_fingerprint(destination),
        _tree_fingerprint(V2_1_CONSUMED_CLAIM),
        _tree_fingerprint(
            registered_root / "docs/phase4/rehearsals/P4.2a-v2-calibration-v2-1"
        ),
        _tree_fingerprint(registered_root / "docs/phase4/eval/v2-calibration/heldout"),
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
    return json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8") + b"\n"


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
            "implementation_epoch": 1,
            "record_root_sha256": "5" * 64,
        },
        {
            "ordinal": 2,
            "outcome": "INCOMPLETE_UNTERMINALIZED",
            "implementation_epoch": 1,
            "record_root_sha256": "6" * 64,
        },
        {
            "ordinal": 3,
            "outcome": "CANDIDATE_VALIDATED_AND_SELECTED",
            "implementation_epoch": 1,
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
        "epoch": 1,
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
                "epoch": 1,
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
        bundle_path.write_bytes(b'{}\n')
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
    relative, _sha256_value, _hook_count = V2_1_HISTORICAL_AUTHORITY_MODULES[
        historical_module
    ]
    drifted_commit = _fixture_commit_file(
        validator,
        root,
        relative,
        (root / relative).read_bytes() + b"\n# forbidden historical byte drift\n",
    )
    completed, result = _module_identity_subprocess(root, drifted_commit)
    assert completed.returncode == 3
    assert result["exception_type"] == "RehearsalV22ValidationError"
    assert result["message"] == (
        f"inert historical authority bytes drifted: {relative.as_posix()}"
    )


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
    published_source = inspect.getsource(
        implementation._validate_published_validator_bundle
    )
    assert 'and binding.mode == "REGISTERED_OFFICIAL"' in published_source
    assert 'binding.mode in {"REGISTERED_OFFICIAL", "DISPOSABLE_FULL_SHAPE_TEST"}' not in (
        published_source
    )
    candidate_source = inspect.getsource(
        implementation._validate_official_validator_candidate
    )
    active_source = inspect.getsource(
        implementation._active_validator_execution_context
    )
    assert "_active_validator_execution_context(" in candidate_source
    assert "validate_execution_capability(" in active_source


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
        V2_1_PIPELINE_IMPLEMENTATION_COMMIT
        if wrong_side == "manifest"
        else wrong_commit
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
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id == callee_name
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == callee_name
                )
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
        selected_calls = calls("_validate_bundle_once", callee_name)
        assert len(selected_calls) == 1
        assert keyword_name(selected_calls[0], "implementation_commit") == "implementation_commit"
    epoch_calls = calls("_validate_bundle_once", "_validate_implementation_epochs")
    assert len(epoch_calls) == 1
    assert keyword_name(epoch_calls[0], "replay") == "replay"
    assert keyword_name(epoch_calls[0], "archives") == "archives"
    replay_selected_bindings = [
        node
        for node in calls("_validate_bundle_once", "_require_equal")
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
        keyword_name(epoch_validation_calls[0], "implementation_commit")
        == "implementation_commit"
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
    for function_name in ("_validate_bundle_once", "_validate_implementation_epochs"):
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
        "_validate_bundle_once(",
        "_cross_validate_release(",
        "_git_blob(root, reviewed_head",
        "_active_replay_validated_bundle(",
    )
    offsets = [source.index(marker) for marker in ordered_markers]
    assert offsets == sorted(offsets)
    assert source.count("_active_replay_validated_bundle(") == 1
    assert "_active_replay_validated_bundle(" not in inspect.getsource(
        validator._validate_bundle_once
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
        validator._validate_bundle_once
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
    observed = validator._epoch_map(
        {"implementation_epochs": [void_epoch, epoch_two]}
    )
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
        "owner_surface_authorization": void_epoch[
            "owner_exact_surface_authorization"
        ],
        "independent_implementation_review": void_epoch[
            "independent_implementation_review"
        ],
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
            "docs/phase4/reports/"
            "P4.2a-v2-2-epoch2-implementation-independent-review-20260813.json"
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
        match="not one unique status-A Git touch",
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
    creating_commit = (
        _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")
    )
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
    amended_commit = (
        _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode("ascii")
    )
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
def test_release_cross_validation_offsets_void_epoch_without_selecting_it(
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
            "owner_surface_authorization": epoch[
                "owner_exact_surface_authorization"
            ],
            "independent_implementation_review": epoch[
                "independent_implementation_review"
            ],
            "control_merkle_root_sha256": epoch["control_merkle_root_sha256"],
            "first_attempt_ordinal": epoch["first_attempt_ordinal"],
            "last_attempt_ordinal": epoch["last_attempt_ordinal"],
        }
        for epoch in bundle_epochs
    ]
    selected_commit = bundle_epochs[selected_epoch - 1]["implementation_commit"]
    receipt["lineage"]["selected_implementation_commit"] = selected_commit
    validator._cross_validate_release(bundle=bundle, receipt=receipt)
    assert receipt["implementation_epochs"][selected_epoch - 1][
        "implementation_commit"
    ] == selected_commit
    assert selected_commit != validator.VOID_EPOCH_ONE_IMPLEMENTATION_COMMIT

    receipt["lineage"]["selected_implementation_commit"] = (
        validator.VOID_EPOCH_ONE_IMPLEMENTATION_COMMIT
    )
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match="release lineage selected_implementation_commit drifted",
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


def test_initial_sibling_merge_projection_is_valid_but_generic_history_is_double_touch(
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
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"authority is not one unique status-A Git touch",
    ):
        validator._unique_a_authority(
            root,
            _initial_sibling_reference(),
            require_worktree=True,
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
        execution_head = _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode(
            "ascii"
        )
        expected = r"outside the execution-head lineage"
    elif mutation in {"descendant-modify", "descendant-delete"}:
        target = root / INITIAL_SIBLING_PATH
        if mutation == "descendant-modify":
            target.write_bytes(payload + b"drift\n")
            _fixture_git(validator, root, "add", "--", INITIAL_SIBLING_PATH.as_posix())
        else:
            _fixture_git(validator, root, "rm", "--quiet", "--", INITIAL_SIBLING_PATH.as_posix())
        _fixture_git(validator, root, "commit", "--quiet", "-m", mutation)
        execution_head = _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode(
            "ascii"
        )
        expected = r"bytes drifted in its descendant lineage"
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
        _fixture_git(validator, root, "checkout", "--quiet", "synthetic-series")
        expected = r"path exists outside its descendant lineage"
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
        side_commit = _fixture_git(validator, root, "rev-parse", "HEAD").strip().decode(
            "ascii"
        )
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
        execution_head = _fixture_git(
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
        ).strip().decode("ascii")
        _fixture_git(
            validator,
            root,
            "reset",
            "--quiet",
            "--hard",
            execution_head,
        )
        expected = r"bytes drifted in its descendant lineage"

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
    assert "_git_parents(root, INDEPENDENT_REVIEW_COMMIT)" in helper_source
    assert "(INITIAL_REVIEWED_COMMIT,)" in helper_source
    assert "_diff_name_status(" in helper_source
    assert "INITIAL_REVIEWED_COMMIT," in helper_source
    assert "INDEPENDENT_REVIEW_COMMIT," in helper_source
    assert '(("A", path),)' in helper_source
    assert "_git_blob(root, INDEPENDENT_REVIEW_COMMIT, path)" in helper_source
    assert "_sha256(payload) != INDEPENDENT_REVIEW_SHA256" in helper_source
    assert "_git_optional_blob(root, head, path) != payload" in helper_source

    control_source = inspect.getsource(validator._validate_control_archive)
    assert "if relative == INDEPENDENT_REVIEW_RELATIVE.as_posix():" in control_source
    assert "creating_payload = _validate_initial_sibling_authority(" in control_source
    assert "else:\n                    creating_payload = _unique_a_authority(" in control_source

    epoch_source = inspect.getsource(validator._validate_implementation_epochs)
    assert "if index == 1:" in epoch_source
    assert "owner_payload = _validate_initial_sibling_authority(" in epoch_source
    assert "else:\n            owner_payload = _unique_a_authority(" in epoch_source
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
        match=r"authority is not one unique status-A Git touch",
    ):
        validator._unique_a_authority(root, reference, require_worktree=True)
