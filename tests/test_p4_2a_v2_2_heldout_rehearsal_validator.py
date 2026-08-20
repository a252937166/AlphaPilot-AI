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
from contextlib import nullcontext
from dataclasses import replace
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
import hashlib
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
    implementation = validator.implementation
    authority = implementation.AuthorityReference(
        "docs/phase4/reports/synthetic-live-authority.json",
        "0" * 64,
        "0" * 40,
    )
    control = implementation.ControlSurface(
        implementation_commit=implementation_commit,
        records=(),
        payloads={},
        manifest_payload=b"",
        merkle_root_sha256="0" * 64,
        ast_closure_paths=(),
        loaded_repository_sources=(),
        python_inventory=b"",
        package_inventory=b"",
    )
    loaded = {
        "scripts/p4_2a_v2_2_heldout_rehearsal.py": hashlib.sha256(
            (root / "scripts/p4_2a_v2_2_heldout_rehearsal.py").read_bytes()
        ).hexdigest(),
        "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py": hashlib.sha256(
            (root / "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py").read_bytes()
        ).hexdigest(),
    }
    live_anchor = implementation.LiveExecutionAnchor(
        execution_epoch=5,
        implementation_commit=implementation_commit,
        owner_surface_authorization=authority,
        independent_implementation_review=authority,
        merge_commit="0" * 40,
        landing_report=authority,
        control_surface=control,
        execution_head=implementation_commit,
        loaded_module_sha256=loaded,
    )
    validator._validate_module_identity(root, live_anchor)
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
def latest_landed_epoch_repository(tmp_path: Path) -> dict[str, Any]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    root = tmp_path / "latest-landed-epoch"
    root.mkdir(mode=0o700)
    _fixture_git(validator, root, "init", "--quiet")
    target = Path("scripts/p4_2a_v2_2_heldout_rehearsal.py")
    (root / target).parent.mkdir(parents=True)
    (root / target).write_bytes(b"EPOCH_FOUR = True\n")
    _fixture_git(validator, root, "add", "--", target.as_posix())
    _fixture_git(validator, root, "commit", "--quiet", "-m", "epoch four base")
    base_commit = _fixture_git(validator, root, "rev-parse", "HEAD").decode().strip()

    authority_relative = Path(
        "docs/phase4/reports/P4.2a-v2-2-epoch5-surface-authority-20260820.json"
    )
    authority_document = {
        "schema_version": "p4.2a-v2-2-implementation-epoch-surface-authorization-v1",
        "verdict": "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
        "owner": {"identity": "ouyang", "approved": True},
        "implementation_epoch": 5,
        "base_commit": base_commit,
        "exact_surface": [{"path": target.as_posix(), "status": "M"}],
    }
    authority_payload = implementation._canonical_json_bytes(authority_document)
    (root / authority_relative).parent.mkdir(parents=True, mode=0o700)
    authority_commit = _fixture_commit_file(
        validator,
        root,
        authority_relative,
        authority_payload,
    )
    (root / target).write_bytes(b"EPOCH_FIVE = True\n")
    _fixture_git(validator, root, "add", "--", target.as_posix())
    _fixture_git(validator, root, "commit", "--quiet", "-m", "epoch five implementation")
    implementation_commit = _fixture_git(
        validator,
        root,
        "rev-parse",
        "HEAD",
    ).decode().strip()

    review_relative = Path(
        "docs/phase4/reports/"
        "P4.2a-v2-2-epoch5-implementation-independent-review-20260820.json"
    )
    review_payload = implementation._canonical_json_bytes(
        {
            "schema_version": "p4.2a-independent-review-v1",
            "verdict": "APPROVE_EPOCH5_IMPLEMENTATION",
            "reviewed_commit": implementation_commit,
            "blockers": [],
        }
    )
    _fixture_git(
        validator,
        root,
        "checkout",
        "--quiet",
        "-b",
        "epoch5-review",
        implementation_commit,
    )
    review_commit = _fixture_commit_file(
        validator,
        root,
        review_relative,
        review_payload,
    )
    _fixture_git(
        validator,
        root,
        "checkout",
        "--quiet",
        "-B",
        "main",
        implementation_commit,
    )
    _fixture_git(
        validator,
        root,
        "merge",
        "--quiet",
        "--no-ff",
        "--no-edit",
        review_commit,
    )
    merge_commit = _fixture_git(validator, root, "rev-parse", "HEAD").decode().strip()
    landing_relative = Path(
        "docs/phase4/reports/"
        "P4.2a-v2-2-epoch5-registered-gate-landing-report-20260820.json"
    )
    landing_payload = implementation._canonical_json_bytes(
        {
            "schema_version": (
                "p4.2a-v2-2-epoch5-synthetic-registered-gate-landing-report-v1"
            ),
            "status": "PASS_REGISTERED_GATE_LANDING_REPORT_READY_BEFORE_MERGE",
            "candidate_lineage": {
                "implementation_commit": implementation_commit,
                "independent_review": {
                    "path": review_relative.as_posix(),
                    "creating_commit": review_commit,
                    "sha256": hashlib.sha256(review_payload).hexdigest(),
                },
            },
            "planned_landing": {"second_parent": review_commit},
            "merge_commit": merge_commit,
        }
    )
    landing_commit = _fixture_commit_file(
        validator,
        root,
        landing_relative,
        landing_payload,
    )
    authority = implementation.AuthorityReference(
        authority_relative.as_posix(),
        hashlib.sha256(authority_payload).hexdigest(),
        authority_commit,
    )
    review = implementation.AuthorityReference(
        review_relative.as_posix(),
        hashlib.sha256(review_payload).hexdigest(),
        review_commit,
    )
    landing = implementation.AuthorityReference(
        landing_relative.as_posix(),
        hashlib.sha256(landing_payload).hexdigest(),
        landing_commit,
    )
    control = implementation.ControlSurface(
        implementation_commit=implementation_commit,
        records=({"repository_path": target.as_posix()},),
        payloads={},
        manifest_payload=b"",
        merkle_root_sha256="1" * 64,
        ast_closure_paths=(),
        loaded_repository_sources=(),
        python_inventory=b"",
        package_inventory=b"",
    )
    anchor = implementation.LiveExecutionAnchor(
        execution_epoch=5,
        implementation_commit=implementation_commit,
        owner_surface_authorization=authority,
        independent_implementation_review=review,
        merge_commit=merge_commit,
        landing_report=landing,
        control_surface=control,
        execution_head=landing_commit,
        loaded_module_sha256={
            "scripts/p4_2a_v2_2_heldout_rehearsal.py": "2" * 64,
            "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py": "3" * 64,
        },
    )
    validator._validate_latest_landed_epoch(
        project_root=root,
        live_anchor=anchor,
    )
    return {
        "root": root,
        "target": target,
        "anchor": anchor,
        "landing_commit": landing_commit,
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


def _void_epoch_three_document(validator: Any) -> dict[str, Any]:
    return {
        "epoch": 3,
        "implementation_commit": validator.VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT,
        "owner_exact_surface_authorization": {
            "path": validator.VOID_EPOCH_THREE_AUTHORITY_RELATIVE.as_posix(),
            "sha256": validator.VOID_EPOCH_THREE_AUTHORITY_SHA256,
            "creating_commit": validator.VOID_EPOCH_THREE_IMPLEMENTATION_PARENT,
            "unique_a_history_verified": True,
        },
        "independent_implementation_review": {
            "path": validator.VOID_EPOCH_THREE_REASON_RELATIVE.as_posix(),
            "sha256": validator.VOID_EPOCH_THREE_REASON_SHA256,
            "creating_commit": validator.VOID_EPOCH_THREE_REASON_COMMIT,
            "unique_a_history_verified": True,
        },
        "control_merkle_root_sha256": validator.VOID_EPOCH_THREE_CONTROL_ROOT_SHA256,
        "first_attempt_ordinal": 2,
        "last_attempt_ordinal": 2,
        "all_attempts_authorized": True,
    }


def _ordinary_epoch_document(
    number: int,
    *,
    first: int,
    last: int,
) -> dict[str, Any]:
    return {
        "epoch": number,
        "implementation_commit": f"{number:x}" * 40,
        "owner_exact_surface_authorization": {
            "path": f"docs/phase4/reports/epoch-{number}-owner.json",
            "sha256": f"{number + 1:x}" * 64,
            "creating_commit": f"{number + 2:x}" * 40,
            "unique_a_history_verified": True,
        },
        "independent_implementation_review": {
            "path": f"docs/phase4/reports/epoch-{number}-review.json",
            "sha256": f"{number + 3:x}" * 64,
            "creating_commit": f"{number + 4:x}" * 40,
            "unique_a_history_verified": True,
        },
        "control_merkle_root_sha256": f"{number + 5:x}" * 64,
        "first_attempt_ordinal": first,
        "last_attempt_ordinal": last,
        "all_attempts_authorized": True,
    }


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
    recovered_bundle_signature = inspect.signature(validator.validate_recovered_bundle)
    recovered_release_signature = inspect.signature(
        validator.validate_recovered_release_authorization
    )
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
    for signature, path_parameter in (
        (recovered_bundle_signature, "bundle_path"),
        (recovered_release_signature, "receipt_path"),
    ):
        assert tuple(signature.parameters) == (
            "project_root",
            path_parameter,
            "recovery_context",
            "recovery_validator_delegation",
        )
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            and parameter.default is inspect.Parameter.empty
            for parameter in signature.parameters.values()
        )


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


def test_passive_recovery_apis_reject_forged_capability_before_any_path_read() -> None:
    validator = _validator_module()
    before = _all_real_path_fingerprints()
    for function, path_keyword in (
        (validator.validate_recovered_bundle, "bundle_path"),
        (
            validator.validate_recovered_release_authorization,
            "receipt_path",
        ),
    ):
        arguments = {
            "project_root": PROJECT_ROOT,
            path_keyword: PROJECT_ROOT / "must-not-be-read.json",
            "recovery_context": object(),
            "recovery_validator_delegation": object(),
        }
        with pytest.raises(
            (validator.RehearsalV22ValidationError, RuntimeError),
            match=r"recovery|capability|delegation|forged|binding",
        ):
            function(**arguments)
    assert _all_real_path_fingerprints() == before
    for function in (
        validator.validate_recovered_bundle,
        validator.validate_recovered_release_authorization,
    ):
        source = inspect.getsource(function)
        assert "_active_replay_validated_bundle" not in source
        assert "_active_replay_selected_pipeline" not in source
        assert "_passive_revalidate_validated_bundle" in source or (
            "ValidationMode.RECOVERED_RELEASE_PASSIVE" in source
        )


def test_recovered_release_uses_its_own_read_only_scope_and_zero_replay_path() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    assert implementation.RecoveredReleaseCapability is not (
        implementation.RecoveryExecutionCapability
    )
    assert not issubclass(
        implementation.RecoveredReleaseCapability,
        implementation.RecoveryExecutionCapability,
    )
    scope_source = inspect.getsource(
        implementation._recovered_release_validation_scope
    )
    assert "_read_only_preflight_policy(binding.project_root)" in scope_source
    assert "write_roots" in scope_source
    assert "exact_write_paths" in scope_source
    assert "create_only_roots" in scope_source
    assert "sqlite_roots" in scope_source
    assert "_recovery_execution_capability_scope" not in scope_source

    context_source = inspect.getsource(
        validator._recovered_release_validation_context
    )
    assert "_validate_recovered_release_capability(" in context_source
    assert "_validate_recovered_release_validator_delegation(" in context_source
    assert "_recovered_release_validation_anchors(" in context_source
    assert "_validate_recovery_execution_capability(" not in context_source
    assert "_validate_recovery_validator_delegation(" not in context_source
    assert "_recovery_validation_anchors(" not in context_source

    public_source = inspect.getsource(
        validator.validate_recovered_release_authorization
    )
    assert "ValidationMode.RECOVERED_RELEASE_PASSIVE" in public_source
    assert "execution_context=None" in public_source
    assert "_active_replay_validated_bundle" not in public_source
    release_source = inspect.getsource(validator._validate_release_once)
    assert "if validation_mode is ValidationMode.ORDINARY_ACTIVE:" in release_source
    assert "_active_replay_validated_bundle(" in release_source
    assert "else:\n        _passive_revalidate_validated_bundle(" in release_source


def test_passive_bundle_controls_and_inheritance_use_only_selected_git_blobs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    validator = _validator_module()
    bundle_directory = tmp_path / "passive-bundle"
    bundle_directory.mkdir(mode=0o700)
    bundle_path = bundle_directory / validator.BUNDLE_FILENAME
    bundle_path.write_bytes(b"{}\n")
    historical = SimpleNamespace(
        selected_epoch=4,
        selected_commit="890e9002116c625d41f6aa037975df15d1546c56",
    )
    live = SimpleNamespace(execution_epoch=5, implementation_commit="1" * 40)
    requested: list[str] = []

    def selected_blob(**arguments: object) -> bytes:
        relative = arguments["relative_path"]
        assert isinstance(relative, str)
        requested.append(relative)
        payload = (PROJECT_ROOT / relative).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == arguments["expected_sha256"]
        return payload

    monkeypatch.setattr(validator, "_historical_selected_anchor", lambda value: value)
    monkeypatch.setattr(validator, "_live_execution_anchor", lambda value: value)
    monkeypatch.setattr(validator, "_validate_live_execution_anchor", lambda **_kwargs: None)
    monkeypatch.setattr(validator, "_validated_implementation_blob", selected_blob)
    monkeypatch.setattr(
        validator,
        "_bound_control",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("passive validation read a current control")
        ),
    )
    monkeypatch.setattr(validator, "_schema_validate", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        validator,
        "_validate_binding_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("controls complete")),
    )
    binding = validator.BindingView(
        mode="DISPOSABLE_FULL_SHAPE_TEST",
        project_root=tmp_path,
        absolute_destination=bundle_directory,
        series_token_sha256="2" * 64,
        ledger_root=tmp_path / "ledger",
    )
    with pytest.raises(RuntimeError, match="controls complete"):
        validator._validate_bundle_once(
            project_root=tmp_path,
            bundle_path=bundle_path,
            binding=binding,
            bundle_directory=bundle_directory,
            historical_anchor=historical,
            live_anchor=live,
            validation_mode=validator.ValidationMode.RECOVERY_PASSIVE,
            expected_bundle_sha256=None,
        )
    assert requested == [
        BUNDLE_SCHEMA_RELATIVE.as_posix(),
        PREREGISTRATION_RELATIVE.as_posix(),
        RELEASE_SCHEMA_RELATIVE.as_posix(),
        validator._CARRY_FORWARD_AUTHORITIES["v2_1_preregistration"][0],
        V2_1_BUNDLE_SCHEMA_RELATIVE.as_posix(),
        V2_1_RELEASE_SCHEMA_RELATIVE.as_posix(),
    ]


def test_producer_consumes_recovered_release_through_passive_validator_api_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    root = (tmp_path / "recovered-release-consumer").resolve()
    root.mkdir(mode=0o700)
    destination = root / validator.REGISTERED_DESTINATION_RELATIVE
    destination.mkdir(parents=True, mode=0o700)
    bundle_path = destination / validator.BUNDLE_FILENAME
    bundle_payload = b'{"status":"recovered"}\n'
    bundle_path.write_bytes(bundle_payload)
    ledger = root / "ledger"
    ledger.mkdir(mode=0o700)
    claim = root / "claim"
    claim.mkdir(mode=0o700)
    recovery_authorization_path = root / "recovery.json"
    recovery_authorization_path.write_bytes(b"{}\n")
    receipt_path = root / validator.RELEASE_RELATIVE
    receipt_path.parent.mkdir(parents=True, mode=0o700)
    reviewed_head = "a" * 40
    authority = {
        "path": "docs/phase4/reports/authority.json",
        "sha256": "b" * 64,
        "creating_commit": "c" * 40,
        "unique_a_history_verified": True,
    }
    receipt = {
        "execution_binding": {},
        "reviewed_repository_head": reviewed_head,
        "lineage": {
            "bundle": {
                "path": (
                    f"{validator.REGISTERED_DESTINATION_RELATIVE.as_posix()}/"
                    f"{validator.BUNDLE_FILENAME}"
                ),
                "sha256": hashlib.sha256(bundle_payload).hexdigest(),
            },
            "rehearsal_evidence_commit": reviewed_head,
            "selected_implementation_commit": "d" * 40,
            "preregistration_commit": "e" * 40,
            "v2_1_incident": authority,
            "remediation_request": authority,
            "v2_2_scope_authorization": authority,
            "review_request": authority,
        },
    }
    receipt_payload = implementation._canonical_json_bytes(receipt)
    receipt_path.write_bytes(receipt_payload)
    raw_binding = implementation.ExecutionBinding(
        mode="REGISTERED_OFFICIAL",
        project_root=root,
        shim_path=root / SHIM_RELATIVE,
        action_authorization_path=recovery_authorization_path,
        destination=destination,
        series_token_sha256="1" * 64,
        ledger_root=ledger,
    )
    view = validator.BindingView(
        mode=raw_binding.mode,
        project_root=root,
        absolute_destination=destination,
        series_token_sha256=raw_binding.series_token_sha256,
        ledger_root=ledger,
    )
    recovery_context = object()
    delegation = object()
    historical = SimpleNamespace(selected_commit="d" * 40)
    live = object()
    observed = {"public_api": 0, "passive_release": 0, "active": 0}

    monkeypatch.setattr(
        implementation,
        "_recovered_release_validation_scope",
        lambda **_kwargs: nullcontext((recovery_context, delegation)),
    )
    monkeypatch.setattr(
        implementation,
        "_validate_recovered_release_capability",
        lambda *_args, **_kwargs: raw_binding,
    )
    monkeypatch.setattr(
        implementation,
        "_validate_recovered_release_validator_delegation",
        lambda *_args, **_kwargs: raw_binding,
    )
    monkeypatch.setattr(
        implementation,
        "_recovered_release_validation_anchors",
        lambda *_args, **_kwargs: (historical, live),
    )
    monkeypatch.setattr(
        implementation,
        "_authority_reference_for_path",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        implementation,
        "_validate_bundle_recovery_authorization",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(implementation, "_recovery_claim_path", lambda *_args: claim)
    monkeypatch.setattr(implementation, "_current_execution_head", lambda _root: "f" * 40)
    monkeypatch.setattr(validator, "_binding_view", lambda _value: view)
    monkeypatch.setattr(validator, "_historical_selected_anchor", lambda value: value)
    monkeypatch.setattr(validator, "_live_execution_anchor", lambda value: value)
    monkeypatch.setattr(
        validator,
        "_validated_implementation_blob",
        lambda **_kwargs: (PROJECT_ROOT / RELEASE_SCHEMA_RELATIVE).read_bytes(),
    )
    monkeypatch.setattr(validator, "_schema_validate", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(validator, "_validate_binding_document", lambda *_args: None)
    monkeypatch.setattr(validator, "_git_commit", lambda _root, value, _label: value)
    monkeypatch.setattr(validator, "_git_bytes", lambda *_args: ("f" * 40 + "\n").encode())
    monkeypatch.setattr(
        validator,
        "_unique_a_unserialized",
        lambda *_args, **_kwargs: ("9" * 40, receipt_payload),
    )
    monkeypatch.setattr(validator, "_git_is_ancestor", lambda *_args: True)
    authority_current_flags: list[bool] = []
    monkeypatch.setattr(
        validator,
        "_unique_a_authority",
        lambda _root, _authority, *, require_worktree: authority_current_flags.append(
            require_worktree
        ),
    )
    validated = validator.ValidatedBundle(
        document={"status": "recovered"},
        payload=bundle_payload,
        path=bundle_path,
        project_root=root,
        bundle_directory=destination,
        implementation_commit="d" * 40,
        archives=SimpleNamespace(),
        history=SimpleNamespace(),
        historical_anchor=historical,
        live_anchor=live,
        validation_mode=validator.ValidationMode.RECOVERED_RELEASE_PASSIVE,
    )
    monkeypatch.setattr(validator, "_validate_bundle_once", lambda **_kwargs: validated)
    monkeypatch.setattr(validator, "_cross_validate_release", lambda **_kwargs: None)
    monkeypatch.setattr(validator, "_git_blob", lambda *_args: bundle_payload)

    def passive_release(*, validated: object) -> None:
        assert validated is not None
        observed["passive_release"] += 1

    def active_canary(*_args: object, **_kwargs: object) -> None:
        observed["active"] += 1
        raise AssertionError("recovered release reached active replay or pipeline")

    monkeypatch.setattr(validator, "_passive_revalidate_validated_bundle", passive_release)
    monkeypatch.setattr(validator, "_active_replay_validated_bundle", active_canary)
    monkeypatch.setattr(validator, "_active_replay_selected_pipeline", active_canary)
    monkeypatch.setattr(implementation, "replay_selected_pipeline", active_canary)
    monkeypatch.setattr(implementation, "_execute_pipeline_inner", active_canary)
    public_api = validator.validate_recovered_release_authorization

    def public_api_spy(**arguments: object) -> dict[str, Any]:
        observed["public_api"] += 1
        return public_api(**arguments)

    monkeypatch.setattr(
        validator,
        "validate_recovered_release_authorization",
        public_api_spy,
    )
    result = implementation.consume_recovered_release_authorization(
        binding=raw_binding,
        validator_module=validator,
        recovery_authorization_path=recovery_authorization_path,
        receipt_path=receipt_path,
    )
    assert result == receipt
    assert observed == {"public_api": 1, "passive_release": 1, "active": 0}
    assert authority_current_flags == [False, False, False, False]


@pytest.mark.parametrize(
    "mutation",
    (
        "top-extra",
        "owner-extra",
        "selected-file-extra",
        "starts-two",
        "pipeline-one",
        "retry-one",
        "ledger-write",
        "phase-unlock",
        "series-open",
        "terminal-not-selected",
        "owner-approved-int",
        "starts-bool",
        "pipeline-bool",
        "retry-bool",
        "series-closed-int",
        "started-count-bool",
        "failed-count-bool",
        "selected-ordinal-bool",
        "selected-epoch-bool",
        "execution-epoch-bool",
        "control-count-bool",
        "latest-required-int",
        "effect-int",
        "lock-int",
        "selected-file-bytes-bool",
    ),
)
def test_recovery_authorization_shape_or_effect_drift_rejects_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    root = tmp_path / "recovery-authorization-root"
    root.mkdir(mode=0o700)
    destination = root / "destination"
    ledger = tmp_path / "sealed-ledger"
    authority_path = root / "recovery.json"
    binding = implementation.ExecutionBinding(
        mode="DISPOSABLE_FULL_SHAPE_TEST",
        project_root=root,
        shim_path=root / "scripts/rehearse_p4_2a_v2_2_heldout_full_path.py",
        action_authorization_path=authority_path,
        destination=destination,
        series_token_sha256="1" * 64,
        ledger_root=ledger,
    )
    started_bytes = b'{"started":true}\n'
    candidate_document = {
        "run_a_root_sha256": "2" * 64,
        "run_b_root_sha256": "2" * 64,
        "control_surface_root_sha256": "3" * 64,
        "evidence_tree_root_sha256": "4" * 64,
        "candidate_content_root_sha256": "5" * 64,
    }
    candidate_bytes = implementation._canonical_json_bytes(candidate_document)
    terminal_bytes = b'{"terminal":true}\n'
    selected = SimpleNamespace(
        ordinal=2,
        implementation_epoch=4,
        implementation_commit="6" * 40,
        started_bytes=started_bytes,
        candidate_bytes=candidate_bytes,
        terminal_bytes=terminal_bytes,
        evidence_tree_root_sha256="4" * 64,
    )
    history = SimpleNamespace(
        records=(SimpleNamespace(), selected),
        series_closed=True,
        selected_attempt_ordinal=2,
        validated_candidate_count=1,
        incomplete_count=0,
        live_ledger_root_sha256="7" * 64,
        history_root_sha256="8" * 64,
        started_count=2,
        failed_count=1,
    )
    exact_argv = ["python", "--recover-sealed-bundle"]
    exact_environment = {"LOCKED": "1"}
    selected_files = {
        "started": {
            "relative_path": "attempts/000002/started.json",
            "sha256": hashlib.sha256(started_bytes).hexdigest(),
            "bytes": len(started_bytes),
        },
        "candidate": {
            "relative_path": "attempts/000002/candidate.json",
            "sha256": hashlib.sha256(candidate_bytes).hexdigest(),
            "bytes": len(candidate_bytes),
        },
        "terminal": {
            "relative_path": "attempts/000002/terminal.json",
            "sha256": hashlib.sha256(terminal_bytes).hexdigest(),
            "bytes": len(terminal_bytes),
        },
    }
    document = {
        "schema_version": "p4.2a-v2-2-sealed-bundle-recovery-authorization-v1",
        "authorization_id": "SYNTHETIC-RECOVERY",
        "created_at_utc": "2026-08-20T12:00:00Z",
        "created_at_shanghai": "2026-08-20T20:00:00+08:00",
        "verdict": "APPROVE_EXACTLY_ONE_SEALED_BUNDLE_RECOVERY_ZERO_PIPELINE_START",
        "owner": {
            "identity": "ouyang",
            "approved": True,
            "scope": "one disclosed sealed-bundle recovery only",
        },
        "sealed_series": {
            "series_id": implementation.REHEARSAL_ID,
            "series_token_sha256": binding.series_token_sha256,
            "ledger_root": ledger.as_posix(),
            "history_root_sha256": history.history_root_sha256,
            "live_ledger_root_sha256": history.live_ledger_root_sha256,
            "series_closed": True,
            "started_count": 2,
            "failed_count": 1,
            "incomplete_count": 0,
            "validated_candidate_count": 1,
            "selected_attempt_ordinal": 2,
            "selected_implementation_epoch": 4,
            "selected_implementation_commit": selected.implementation_commit,
            "selected_control_merkle_root_sha256": "3" * 64,
            "selected_evidence_tree_root_sha256": "4" * 64,
            "selected_candidate_content_root_sha256": "5" * 64,
            "selected_run_a_root_sha256": "2" * 64,
            "selected_run_b_root_sha256": "2" * 64,
            "selected_terminal_outcome": "CANDIDATE_VALIDATED_AND_SELECTED",
            "selected_reached_stage": "bundle_candidate_validated",
            "automatic_retry_count": 0,
            "selected_files": selected_files,
        },
        "execution_epoch": {
            "epoch": 5,
            "implementation_commit": "9" * 40,
            "owner_exact_surface_authorization": {
                "path": "docs/phase4/reports/owner.json",
                "sha256": "a" * 64,
                "creating_commit": "b" * 40,
                "unique_a_history_verified": True,
            },
            "independent_implementation_review": {
                "path": "docs/phase4/reports/review.json",
                "sha256": "c" * 64,
                "creating_commit": "d" * 40,
                "unique_a_history_verified": True,
            },
            "merge_commit": "e" * 40,
            "landing_report": {
                "path": "docs/phase4/reports/landing.json",
                "sha256": "f" * 64,
                "creating_commit": "1" * 40,
                "unique_a_history_verified": True,
            },
            "control_merkle_root_sha256": "2" * 64,
            "control_record_count": 75,
            "latest_complete_landed_epoch_required": True,
            "current_control_bytes_required": True,
            "loaded_module_bytes_required": True,
        },
        "destination": {
            "absolute_path": destination.as_posix(),
            "required_absent_before_start": True,
            "publication_mode": "ATOMIC_DIRECTORY_NO_REPLACE",
            "bundle_schema_version": implementation.BUNDLE_SCHEMA_VERSION,
            "expected_bundle_status": "PASS_REHEARSAL_V2_2_AWAITING_OWNER_REVIEW",
        },
        "exact_argv": exact_argv,
        "command_sha256": implementation._command_sha256(exact_argv),
        "exact_environment": exact_environment,
        "environment_sha256": implementation._environment_sha256(exact_environment),
        "authorized_bundle_recovery_starts": 1,
        "authorized_pipeline_starts": 0,
        "automatic_retry_count": 0,
        "effect_authorization": {
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
        },
        "interpreter": {
            "path": Path(sys.executable).absolute().as_posix(),
            "sha256": hashlib.sha256(
                Path(sys.executable).absolute().read_bytes()
            ).hexdigest(),
            "version": (
                f"{sys.version_info.major}.{sys.version_info.minor}."
                f"{sys.version_info.micro}"
            ),
        },
        "locks": {
            "p4_2a_done": False,
            "p4_2b_unlocked": False,
            "p4_3_unlocked": False,
            "heldout_evaluation_unlocked": False,
            "real_trading_unlocked": False,
            "non_simulate_trading_unlocked": False,
        },
    }
    if mutation == "top-extra":
        document["unexpected"] = False
    elif mutation == "owner-extra":
        document["owner"]["unexpected"] = False
    elif mutation == "selected-file-extra":
        document["sealed_series"]["selected_files"]["started"]["unexpected"] = 0
    elif mutation == "starts-two":
        document["authorized_bundle_recovery_starts"] = 2
    elif mutation == "pipeline-one":
        document["authorized_pipeline_starts"] = 1
    elif mutation == "retry-one":
        document["automatic_retry_count"] = 1
    elif mutation == "ledger-write":
        document["effect_authorization"]["ledger_write"] = True
    elif mutation == "phase-unlock":
        document["locks"]["p4_2b_unlocked"] = True
    elif mutation == "series-open":
        document["sealed_series"]["series_closed"] = False
    elif mutation == "terminal-not-selected":
        document["sealed_series"]["selected_terminal_outcome"] = "FAILED"
    elif mutation == "owner-approved-int":
        document["owner"]["approved"] = 1
    elif mutation == "starts-bool":
        document["authorized_bundle_recovery_starts"] = True
    elif mutation == "pipeline-bool":
        document["authorized_pipeline_starts"] = False
    elif mutation == "retry-bool":
        document["automatic_retry_count"] = False
    elif mutation == "series-closed-int":
        document["sealed_series"]["series_closed"] = 1
    elif mutation == "started-count-bool":
        document["sealed_series"]["started_count"] = True
    elif mutation == "failed-count-bool":
        document["sealed_series"]["failed_count"] = True
    elif mutation == "selected-ordinal-bool":
        document["sealed_series"]["selected_attempt_ordinal"] = True
    elif mutation == "selected-epoch-bool":
        document["sealed_series"]["selected_implementation_epoch"] = True
    elif mutation == "execution-epoch-bool":
        document["execution_epoch"]["epoch"] = True
    elif mutation == "control-count-bool":
        document["execution_epoch"]["control_record_count"] = True
    elif mutation == "latest-required-int":
        document["execution_epoch"]["latest_complete_landed_epoch_required"] = 1
    elif mutation == "effect-int":
        document["effect_authorization"]["ledger_read"] = 1
    elif mutation == "lock-int":
        document["locks"]["p4_2a_done"] = 0
    else:
        document["sealed_series"]["selected_files"]["started"]["bytes"] = False
    payload = implementation._canonical_json_bytes(document)
    authority = implementation.AuthorityReference(
        "recovery.json",
        hashlib.sha256(payload).hexdigest(),
        "2" * 40,
    )
    monkeypatch.setattr(
        implementation,
        "validate_unique_a_authority",
        lambda *_args, **_kwargs: payload,
    )
    monkeypatch.setattr(
        implementation,
        "_current_execution_head",
        lambda _root: "3" * 40,
    )
    monkeypatch.setattr(
        implementation,
        "validate_live_history",
        lambda _binding: history,
    )
    monkeypatch.setattr(
        implementation,
        "_live_execution_anchor",
        lambda _binding, _execution: object(),
    )
    monkeypatch.setattr(implementation, "_git_is_ancestor", lambda *_args: True)
    before = _tree_fingerprint(tmp_path)
    with pytest.raises(implementation.RehearsalV22Error):
        implementation._validate_bundle_recovery_authorization(
            binding,
            authority,
            require_current_process=False,
        )
    assert _tree_fingerprint(tmp_path) == before
    assert not os.path.lexists(destination)
    assert not os.path.lexists(ledger)


@pytest.mark.parametrize(
    "history_shape",
    ("missing-void-three", "extra-epoch", "wrong-interval", "unauthorized"),
)
def test_bundle_recovery_rejects_every_closed_history_except_exact_two_four(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    history_shape: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    root = tmp_path / "closed-history-root"
    scripts = root / "scripts"
    scripts.mkdir(parents=True, mode=0o700)
    producer_path = root / IMPLEMENTATION_RELATIVE
    validator_path = root / VALIDATOR_RELATIVE
    producer_path.write_bytes(b"producer\n")
    validator_path.write_bytes(b"validator\n")
    binding = implementation.ExecutionBinding(
        mode="DISPOSABLE_FULL_SHAPE_TEST",
        project_root=root,
        shim_path=root / SHIM_RELATIVE,
        action_authorization_path=root / "recovery.json",
        destination=root / "destination",
        series_token_sha256="1" * 64,
        ledger_root=tmp_path / "sealed-ledger",
    )
    rows = [
        {
            "epoch": number,
            "first_attempt_ordinal": ordinal,
            "last_attempt_ordinal": ordinal,
            "all_attempts_authorized": True,
        }
        for number, ordinal in ((1, 1), (2, 1), (3, 2), (4, 2))
    ]
    if history_shape == "missing-void-three":
        rows.pop(2)
    elif history_shape == "extra-epoch":
        rows.append(
            {
                "epoch": 5,
                "first_attempt_ordinal": 2,
                "last_attempt_ordinal": 2,
                "all_attempts_authorized": True,
            }
        )
    elif history_shape == "wrong-interval":
        rows[2]["first_attempt_ordinal"] = 1
    else:
        rows[3]["all_attempts_authorized"] = False
    live = SimpleNamespace(
        loaded_module_sha256={
            IMPLEMENTATION_RELATIVE.as_posix(): hashlib.sha256(
                producer_path.read_bytes()
            ).hexdigest(),
            VALIDATOR_RELATIVE.as_posix(): hashlib.sha256(
                validator_path.read_bytes()
            ).hexdigest(),
        }
    )
    monkeypatch.setattr(validator, "__file__", validator_path.as_posix())
    monkeypatch.setattr(implementation, "_current_execution_head", lambda _root: "2" * 40)
    monkeypatch.setattr(implementation, "_recovery_reference", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        implementation,
        "_validate_bundle_recovery_authorization",
        lambda *_args, **_kwargs: SimpleNamespace(execution_epoch={}),
    )
    monkeypatch.setattr(
        implementation,
        "_recovery_claim_path",
        lambda *_args: tmp_path / "absent-claim",
    )
    monkeypatch.setattr(
        implementation,
        "_recovery_temporary_authority_path",
        lambda *_args: tmp_path / "absent-temporary",
    )
    monkeypatch.setattr(implementation, "validate_live_history", lambda _binding: object())
    monkeypatch.setattr(
        implementation,
        "_historical_selected_anchor",
        lambda *_args: object(),
    )
    monkeypatch.setattr(implementation, "_live_execution_anchor", lambda *_args: live)
    monkeypatch.setattr(
        implementation,
        "_module_identity_observation",
        lambda: SimpleNamespace(module_origin=producer_path),
    )
    monkeypatch.setattr(implementation, "_implementation_epochs", lambda *_args: rows)
    monkeypatch.setattr(
        implementation,
        "_void_epoch_one",
        lambda *_args, **_kwargs: rows[0],
    )
    monkeypatch.setattr(
        implementation,
        "_void_epoch_three",
        lambda *_args, **_kwargs: rows[2],
    )
    monkeypatch.setattr(
        implementation,
        "_rehydrate_sealed_pipeline_replays",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("invalid history reached sealed rehydration")
        ),
    )
    before = _tree_fingerprint(tmp_path)
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"exact disclosed \[2,4\] shape",
    ):
        implementation._preflight_bundle_recovery(binding)
    assert _tree_fingerprint(tmp_path) == before


def test_recovery_rejects_staged_tree_tamper_after_passive_validation_before_rename(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    root = tmp_path / "staged-tamper-root"
    root.mkdir(mode=0o700)
    ledger = tmp_path / "staged-tamper-ledger"
    ledger.mkdir(mode=0o700)
    (ledger / "sealed.json").write_bytes(b"{}\n")
    binding = implementation.ExecutionBinding(
        mode="DISPOSABLE_FULL_SHAPE_TEST",
        project_root=root,
        shim_path=root / SHIM_RELATIVE,
        action_authorization_path=root / "recovery.json",
        destination=root / "destination",
        series_token_sha256="1" * 64,
        ledger_root=ledger,
    )
    candidate = root / "staged-candidate"
    bundle_payload = b'{"status":"sealed"}\n'
    assembly = SimpleNamespace(
        document={"status": "sealed"},
        bundle_payload=bundle_payload,
    )
    published: list[Path] = []

    def stage(**_kwargs: object) -> Path:
        candidate.mkdir(mode=0o700)
        (candidate / implementation.BUNDLE_FILENAME).write_bytes(bundle_payload)
        return candidate

    def passive_validate(**arguments: object) -> dict[str, str]:
        bundle_path = arguments["bundle_path"]
        assert isinstance(bundle_path, Path)
        (bundle_path.parent / "post-validation-tamper").write_bytes(b"tamper\n")
        return assembly.document

    def publish(_binding: object, staged: Path) -> dict[str, str]:
        published.append(staged)
        return {}

    monkeypatch.setattr(
        implementation,
        "_validate_recovery_execution_capability",
        lambda *_args, **_kwargs: binding,
    )
    monkeypatch.setattr(implementation, "_build_bundle", lambda **_kwargs: assembly)
    monkeypatch.setattr(implementation, "_stage_bundle", stage)
    monkeypatch.setattr(
        implementation,
        "_borrow_recovery_validator_authority",
        lambda *_args, **_kwargs: nullcontext(object()),
    )
    monkeypatch.setattr(implementation, "_publish_candidate", publish)
    monkeypatch.setattr(
        implementation,
        "_write_recovery_terminal",
        lambda **_kwargs: {},
    )
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"staged|candidate|changed|drift",
    ):
        implementation._execute_authorized_bundle_recovery(
            binding=binding,
            history=object(),
            authorization=object(),
            historical_anchor=object(),
            live_anchor=object(),
            run_a=object(),
            run_b=object(),
            ledger_snapshot=implementation._tree_fingerprint(ledger),
            bootstrap=object(),
            recovery_context=object(),
            validator_module=SimpleNamespace(
                validate_recovered_bundle=passive_validate,
            ),
            claim_root=root / "claim",
            temporary_authority=root / "temporary",
        )
    assert published == []
    assert not os.path.lexists(binding.destination)


@pytest.mark.parametrize(
    "mutation",
    (
        "valid",
        "started-schema",
        "started-recovery-id",
        "started-pipeline-bool",
        "terminal-schema",
        "terminal-recovery-id",
        "terminal-pipeline-bool",
        "terminal-temporary-int",
        "terminal-error-false",
        "terminal-bundle-sha",
    ),
)
def test_successful_recovery_claim_requires_exact_published_zero_start_semantics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    root = tmp_path / "successful-claim-root"
    root.mkdir(mode=0o700)
    ledger = tmp_path / "successful-claim-ledger"
    ledger.mkdir(mode=0o700)
    (ledger / "series.json").write_bytes(b"{}\n")
    destination = root / "destination"
    destination.mkdir(mode=0o700)
    bundle_payload = b'{"status":"recovered"}\n'
    (destination / implementation.BUNDLE_FILENAME).write_bytes(bundle_payload)
    binding = implementation.ExecutionBinding(
        mode="DISPOSABLE_FULL_SHAPE_TEST",
        project_root=root,
        shim_path=root / SHIM_RELATIVE,
        action_authorization_path=root / "recovery.json",
        destination=destination,
        series_token_sha256="1" * 64,
        ledger_root=ledger,
    )
    authorization = implementation.BundleRecoveryAuthorization(
        path=root / "recovery.json",
        payload=b"{}\n",
        sha256="2" * 64,
        creating_commit="3" * 40,
        authorization_id="RECOVERY-EXACT-CLAIM",
        sealed_series={
            "history_root_sha256": "4" * 64,
            "live_ledger_root_sha256": "5" * 64,
        },
        execution_epoch={"epoch": 5},
        destination={},
        exact_argv=(),
        command_sha256="6" * 64,
        exact_environment={},
        environment_sha256="7" * 64,
        effect_authorization={},
        interpreter={},
        locks={},
    )
    claim = implementation._recovery_claim_path(binding, authorization)
    claim.mkdir(mode=0o700)
    reference = authorization.authority_ref(root).as_json()
    landing_commit = "a" * 40
    started_commit = "b" * 40
    live_head = "8" * 40
    started = {
        "schema_version": "p4.2a-v2-2-sealed-bundle-recovery-started-v1",
        "recovery_id": authorization.authorization_id,
        "authorization": reference,
        "created_at_utc": "2026-08-20T12:00:00Z",
        "created_at_shanghai": "2026-08-20T20:00:00+08:00",
        "execution_head": started_commit,
        "execution_epoch": 5,
        "sealed_history_root_sha256": "4" * 64,
        "sealed_live_ledger_root_sha256": "5" * 64,
        "destination": destination.as_posix(),
        "state": "STARTED",
        "authorized_bundle_recovery_starts": 1,
        "authorized_pipeline_starts": 0,
        "automatic_retry_count": 0,
    }
    ledger_sha = hashlib.sha256(
        implementation._canonical_json_bytes(implementation._tree_fingerprint(ledger))
    ).hexdigest()
    destination_sha = hashlib.sha256(
        implementation._canonical_json_bytes(
            implementation._tree_fingerprint(destination)
        )
    ).hexdigest()
    terminal = {
        "schema_version": "p4.2a-v2-2-sealed-bundle-recovery-terminal-v1",
        "recovery_id": authorization.authorization_id,
        "authorization": reference,
        "completed_at_utc": "2026-08-20T12:01:00Z",
        "completed_at_shanghai": "2026-08-20T20:01:00+08:00",
        "outcome": "BUNDLE_RECOVERY_PUBLISHED",
        "reached_stage": "bundle_recovery_published",
        "sealed_ledger_before_sha256": ledger_sha,
        "sealed_ledger_after_sha256": ledger_sha,
        "destination": destination.as_posix(),
        "published_bundle_sha256": hashlib.sha256(bundle_payload).hexdigest(),
        "published_tree_sha256": destination_sha,
        "temporary_authority_absent": True,
        "pipeline_starts": 0,
        "automatic_retry_count": 0,
        "error": None,
    }
    if mutation == "started-schema":
        started["schema_version"] = "wrong"
    elif mutation == "started-recovery-id":
        started["recovery_id"] = "wrong"
    elif mutation == "started-pipeline-bool":
        started["authorized_pipeline_starts"] = False
    elif mutation == "terminal-schema":
        terminal["schema_version"] = "wrong"
    elif mutation == "terminal-recovery-id":
        terminal["recovery_id"] = "wrong"
    elif mutation == "terminal-pipeline-bool":
        terminal["pipeline_starts"] = False
    elif mutation == "terminal-temporary-int":
        terminal["temporary_authority_absent"] = 1
    elif mutation == "terminal-error-false":
        terminal["error"] = False
    elif mutation == "terminal-bundle-sha":
        terminal["published_bundle_sha256"] = "9" * 64
    for path, document in (
        (claim / "started.json", started),
        (claim / "terminal.json", terminal),
    ):
        path.write_bytes(implementation._canonical_json_bytes(document))
        path.chmod(0o600)
    monkeypatch.setattr(
        implementation,
        "_live_execution_anchor",
        lambda *_args: SimpleNamespace(
            execution_head=live_head,
            execution_epoch=5,
            landing_report=SimpleNamespace(creating_commit=landing_commit),
        ),
    )
    monkeypatch.setattr(
        implementation,
        "_git_commit",
        lambda _root, value, _label: value,
    )

    def first_parent_git_bytes(_root: Path, *arguments: str) -> bytes:
        assert arguments == (
            "rev-list",
            "--first-parent",
            "--reverse",
            live_head,
            "--",
        )
        return (
            f"{landing_commit}\n{authorization.creating_commit}\n"
            f"{started_commit}\n{live_head}\n"
        ).encode("ascii")

    monkeypatch.setattr(implementation, "_git_bytes", first_parent_git_bytes)
    if mutation == "valid":
        observed_claim, observed_terminal, terminal_sha, bundle_sha = (
            implementation._successful_recovery_claim(binding, authorization)
        )
        assert observed_claim == claim
        assert observed_terminal == terminal
        assert terminal_sha == hashlib.sha256(
            implementation._canonical_json_bytes(terminal)
        ).hexdigest()
        assert bundle_sha == hashlib.sha256(bundle_payload).hexdigest()
    else:
        with pytest.raises(
            implementation.RehearsalV22Error,
            match=r"success claim semantics|recovery.*claim",
        ):
            implementation._successful_recovery_claim(binding, authorization)


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


def test_archive_semantics_and_dual_byte_anchors_are_noninterchangeable() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    assert implementation.HistoricalSelectedAnchor is not implementation.LiveExecutionAnchor
    bundle_signature = inspect.signature(validator._validate_bundle_once)
    assert tuple(bundle_signature.parameters) == (
        "project_root",
        "bundle_path",
        "binding",
        "bundle_directory",
        "historical_anchor",
        "live_anchor",
        "validation_mode",
        "expected_bundle_sha256",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in bundle_signature.parameters.values()
    )
    assert all(
        parameter.default is inspect.Parameter.empty
        for parameter in bundle_signature.parameters.values()
        if parameter.name
        in {"historical_anchor", "live_anchor", "validation_mode"}
    )
    helper_contracts = {
        "_validate_control_archive": "historical_anchor",
        "_validate_archives": "historical_anchor",
        "_validate_lineage": "historical_anchor",
        "_validate_harness_identity": "historical_anchor",
    }
    for helper_name, anchor_name in helper_contracts.items():
        signature = inspect.signature(getattr(validator, helper_name))
        assert anchor_name in signature.parameters
        assert signature.parameters[anchor_name].default is inspect.Parameter.empty
        source = inspect.getsource(getattr(validator, helper_name))
        assert (
            "historical_anchor.selected_commit" in source
            or "historical_anchor=historical_anchor" in source
        )
    epoch_signature = inspect.signature(validator._validate_implementation_epochs)
    assert "historical_anchor" in epoch_signature.parameters
    assert "live_anchor" in epoch_signature.parameters
    assert all(
        epoch_signature.parameters[name].default is inspect.Parameter.empty
        for name in ("historical_anchor", "live_anchor")
    )
    control_source = inspect.getsource(validator._validate_control_archive)
    assert "require_current=False" in control_source
    assert "require_current=True" not in control_source
    assert "_validated_live_implementation_blob" not in control_source
    assert "_validated_implementation_blob" in control_source
    bundle_source = inspect.getsource(validator._validate_bundle_once)
    assert "historical.selected_commit" in bundle_source
    assert "live.implementation_commit" in bundle_source
    assert "historical = _historical_selected_anchor(historical_anchor)" in bundle_source
    assert "live = _live_execution_anchor(live_anchor)" in bundle_source
    assert "historical_anchor=historical" in bundle_source
    assert "live_anchor=live" in bundle_source
    assert "historical_anchor or live_anchor" not in bundle_source
    assert "live_anchor or historical_anchor" not in bundle_source

    archives_tree = ast.parse(inspect.getsource(validator._validate_archives))
    artifact_calls = [
        node
        for node in ast.walk(archives_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_validate_artifact_semantics"
    ]
    assert len(artifact_calls) == 1
    pipeline_keywords = [
        keyword.value
        for keyword in artifact_calls[0].keywords
        if keyword.arg == "pipeline_implementation_commit"
    ]
    assert len(pipeline_keywords) == 1
    assert isinstance(pipeline_keywords[0], ast.Name)
    assert pipeline_keywords[0].id == "_V2_1_IMPLEMENTATION_COMMIT"


def test_live_control_accepts_nonempty_loaded_repository_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    authority = implementation.AuthorityReference(
        "docs/phase4/reports/live-authority.json",
        "1" * 64,
        "2" * 40,
    )
    control = implementation.ControlSurface(
        implementation_commit="3" * 40,
        records=({"repository_path": "src/alphapilot/live.py"},),
        payloads={"src/alphapilot/live.py": b"LIVE = True\n"},
        manifest_payload=b"{}\n",
        merkle_root_sha256="4" * 64,
        ast_closure_paths=("src/alphapilot/live.py",),
        loaded_repository_sources=("src/alphapilot/live.py",),
        python_inventory=b"python\n",
        package_inventory=b"packages\n",
    )
    live = implementation.LiveExecutionAnchor(
        execution_epoch=5,
        implementation_commit=control.implementation_commit,
        owner_surface_authorization=authority,
        independent_implementation_review=authority,
        merge_commit="5" * 40,
        landing_report=authority,
        control_surface=control,
        execution_head="6" * 40,
        loaded_module_sha256={
            IMPLEMENTATION_RELATIVE.as_posix(): "7" * 64,
            VALIDATOR_RELATIVE.as_posix(): "8" * 64,
        },
    )
    observed_module_identity: list[Any] = []
    monkeypatch.setattr(
        implementation,
        "build_control_surface",
        lambda *_args, **_kwargs: control,
    )
    monkeypatch.setattr(
        validator,
        "_validate_module_identity",
        lambda _root, anchor: observed_module_identity.append(anchor),
    )
    validator._validate_current_control_and_modules(
        project_root=tmp_path,
        live_anchor=live,
    )
    assert observed_module_identity == [live]


@pytest.mark.parametrize(
    "mutation",
    (
        "later-authority",
        "duplicate-authority",
        "later-review-only",
        "later-landing-only",
        "control-modified-then-restored",
    ),
)
def test_latest_landed_epoch_rejects_later_duplicate_incomplete_or_restored_control(
    latest_landed_epoch_repository: dict[str, Any],
    mutation: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    root = latest_landed_epoch_repository["root"]
    anchor = latest_landed_epoch_repository["anchor"]
    if mutation in {"later-authority", "duplicate-authority"}:
        epoch = 6 if mutation == "later-authority" else 5
        date = "20260821"
        relative = Path(
            "docs/phase4/reports/"
            f"P4.2a-v2-2-epoch{epoch}-surface-authority-{date}.json"
        )
        payload = implementation._canonical_json_bytes(
            {
                "schema_version": (
                    "p4.2a-v2-2-implementation-epoch-surface-authorization-v1"
                ),
                "verdict": "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
                "owner": {"identity": "ouyang", "approved": True},
                "implementation_epoch": epoch,
                "base_commit": latest_landed_epoch_repository["landing_commit"],
                "exact_surface": [
                    {
                        "path": latest_landed_epoch_repository[
                            "target"
                        ].as_posix(),
                        "status": "M",
                    }
                ],
            }
        )
        _fixture_commit_file(validator, root, relative, payload)
    elif mutation in {"later-review-only", "later-landing-only"}:
        suffix = (
            "implementation-independent-review"
            if mutation == "later-review-only"
            else "uncompleted-landing-report"
        )
        relative = Path(
            "docs/phase4/reports/"
            f"P4.2a-v2-2-epoch6-{suffix}-20260821.json"
        )
        _fixture_commit_file(
            validator,
            root,
            relative,
            implementation._canonical_json_bytes(
                {
                    "schema_version": "p4.2a-v2-2-incomplete-epoch-artifact-v1",
                    "epoch": 6,
                }
            ),
        )
    else:
        target = root / latest_landed_epoch_repository["target"]
        expected = target.read_bytes()
        target.write_bytes(expected + b"CONTROL_DRIFT = True\n")
        _fixture_git(
            validator,
            root,
            "add",
            "--",
            latest_landed_epoch_repository["target"].as_posix(),
        )
        _fixture_git(validator, root, "commit", "--quiet", "-m", "control drift")
        target.write_bytes(expected)
        _fixture_git(
            validator,
            root,
            "add",
            "--",
            latest_landed_epoch_repository["target"].as_posix(),
        )
        _fixture_git(validator, root, "commit", "--quiet", "-m", "restore control")
    drifted_anchor = replace(
        anchor,
        execution_head=(
            _fixture_git(validator, root, "rev-parse", "HEAD").decode().strip()
        ),
    )
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"latest|duplicate|later|ambiguous|landed epoch|post-landing control",
    ):
        validator._validate_latest_landed_epoch(
            project_root=root,
            live_anchor=drifted_anchor,
        )


@pytest.mark.parametrize("commit_kind", ("authority", "landing"))
def test_latest_landed_epoch_rejects_unrelated_path_in_governance_commit(
    latest_landed_epoch_repository: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    commit_kind: str,
) -> None:
    validator = _validator_module()
    root = latest_landed_epoch_repository["root"]
    anchor = latest_landed_epoch_repository["anchor"]
    if commit_kind == "authority":
        commit = anchor.owner_surface_authorization.creating_commit
        base = validator._git_parents(root, commit)[0]
    else:
        commit = anchor.landing_report.creating_commit
        base = anchor.merge_commit
    original = validator._diff_name_status

    def injected(root_value: Path, base_value: str, commit_value: str) -> Any:
        rows = original(root_value, base_value, commit_value)
        if (base_value, commit_value) == (base, commit):
            return (*rows, ("A", "docs/phase4/reports/unrelated-extra.json"))
        return rows

    monkeypatch.setattr(validator, "_diff_name_status", injected)
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"exact unique-A",
    ):
        validator._validate_latest_landed_epoch(
            project_root=root,
            live_anchor=anchor,
        )


def test_latest_landed_epoch_accepts_unrelated_unicode_path_in_first_parent_history(
    latest_landed_epoch_repository: dict[str, Any],
) -> None:
    validator = _validator_module()
    root = latest_landed_epoch_repository["root"]
    relative = Path("docs/phase4/reports/无关历史证据.txt")
    _fixture_commit_file(validator, root, relative, b"unrelated\n")
    live_anchor = replace(
        latest_landed_epoch_repository["anchor"],
        execution_head=(
            _fixture_git(validator, root, "rev-parse", "HEAD").decode().strip()
        ),
    )

    validator._validate_latest_landed_epoch(
        project_root=root,
        live_anchor=live_anchor,
    )


def test_latest_landed_epoch_accepts_unrelated_ascii_space_and_plus_path(
    latest_landed_epoch_repository: dict[str, Any],
) -> None:
    validator = _validator_module()
    root = latest_landed_epoch_repository["root"]
    relative = Path("docs/phase4/reports/unrelated first-parent+evidence.txt")
    _fixture_commit_file(validator, root, relative, b"unrelated\n")
    live_anchor = replace(
        latest_landed_epoch_repository["anchor"],
        execution_head=(
            _fixture_git(validator, root, "rev-parse", "HEAD").decode().strip()
        ),
    )

    validator._validate_latest_landed_epoch(
        project_root=root,
        live_anchor=live_anchor,
    )


@pytest.mark.parametrize("artifact_kind", ("authority", "review", "landing"))
def test_latest_landed_epoch_rejects_malformed_lower_epoch_governance_artifact(
    latest_landed_epoch_repository: dict[str, Any],
    artifact_kind: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    root = latest_landed_epoch_repository["root"]
    anchor = latest_landed_epoch_repository["anchor"]
    target = latest_landed_epoch_repository["target"].as_posix()
    if artifact_kind == "authority":
        relative = Path(
            "docs/phase4/reports/"
            "P4.2a-v2-2-epoch4-surface-authority-20260819.json"
        )
        document = {
            "schema_version": (
                "p4.2a-v2-2-implementation-epoch-surface-authorization-v1"
            ),
            "verdict": "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
            "owner": {"identity": "ouyang", "approved": True},
            "implementation_epoch": 4,
            "base_commit": latest_landed_epoch_repository["landing_commit"],
            "exact_surface": [
                {"path": target, "status": "M", "unexpected": True}
            ],
        }
    elif artifact_kind == "review":
        relative = Path(
            "docs/phase4/reports/"
            "P4.2a-v2-2-epoch4-implementation-independent-review-20260819.json"
        )
        document = {
            "schema_version": "p4.2a-independent-review-v0",
            "verdict": "APPROVE_EPOCH4_IMPLEMENTATION",
            "reviewed_commit": anchor.implementation_commit,
            "blockers": [],
        }
    else:
        review_relative = Path(
            "docs/phase4/reports/"
            "P4.2a-v2-2-epoch4-implementation-independent-review-20260819.json"
        )
        review_commit = _fixture_commit_file(
            validator,
            root,
            review_relative,
            implementation._canonical_json_bytes(
                {
                    "schema_version": "p4.2a-independent-review-v1",
                    "verdict": "APPROVE_EPOCH4_IMPLEMENTATION",
                    "reviewed_commit": anchor.implementation_commit,
                    "blockers": [],
                }
            ),
        )
        relative = Path(
            "docs/phase4/reports/"
            "P4.2a-v2-2-epoch4-registered-gate-landing-report-20260819.json"
        )
        document = {
            "schema_version": "p4.2a-v2-2-epoch4-registered-gate-landing-report-v1",
            "status": "PASS_REGISTERED_GATE_LANDING_REPORT_READY_BEFORE_MERGE",
            "candidate_lineage": {
                "implementation_commit": anchor.implementation_commit,
                "independent_review": {
                    "path": review_relative.as_posix(),
                    "creating_commit": review_commit,
                },
            },
            "planned_landing": {"second_parent": "0" * 40},
        }
    _fixture_commit_file(
        validator,
        root,
        relative,
        implementation._canonical_json_bytes(document),
    )
    drifted_anchor = replace(
        anchor,
        execution_head=(
            _fixture_git(validator, root, "rev-parse", "HEAD").decode().strip()
        ),
    )

    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"landed epoch",
    ):
        validator._validate_latest_landed_epoch(
            project_root=root,
            live_anchor=drifted_anchor,
        )


@pytest.mark.parametrize("mutation", ("commit-alias", "digest"))
def test_latest_landed_epoch_rejects_lower_landing_review_alias_or_digest_drift(
    latest_landed_epoch_repository: dict[str, Any],
    mutation: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    root = latest_landed_epoch_repository["root"]
    anchor = latest_landed_epoch_repository["anchor"]
    review_relative = Path(
        "docs/phase4/reports/"
        "P4.2a-v2-2-epoch4-implementation-independent-review-20260819.json"
    )
    review_payload = implementation._canonical_json_bytes(
        {
            "schema_version": "p4.2a-independent-review-v1",
            "verdict": "APPROVE_EPOCH4_IMPLEMENTATION",
            "reviewed_commit": anchor.implementation_commit,
            "blockers": [],
        }
    )
    review_commit = _fixture_commit_file(
        validator,
        root,
        review_relative,
        review_payload,
    )
    review_row = {
        "path": review_relative.as_posix(),
        "creating_commit": review_commit,
        "sha256": hashlib.sha256(review_payload).hexdigest(),
    }
    if mutation == "commit-alias":
        review_row["commit"] = review_commit
    else:
        review_row["sha256"] = "0" * 64
    landing_relative = Path(
        "docs/phase4/reports/"
        "P4.2a-v2-2-epoch4-registered-gate-landing-report-20260819.json"
    )
    _fixture_commit_file(
        validator,
        root,
        landing_relative,
        implementation._canonical_json_bytes(
            {
                "schema_version": (
                    "p4.2a-v2-2-epoch4-registered-gate-landing-report-v1"
                ),
                "status": "PASS_REGISTERED_GATE_LANDING_REPORT_READY_BEFORE_MERGE",
                "candidate_lineage": {
                    "implementation_commit": anchor.implementation_commit,
                    "independent_review": review_row,
                },
                "planned_landing": {"second_parent": review_commit},
            }
        ),
    )
    drifted_anchor = replace(
        anchor,
        execution_head=(
            _fixture_git(validator, root, "rev-parse", "HEAD").decode().strip()
        ),
    )

    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"landing review.*(?:alias|digest)",
    ):
        validator._validate_latest_landed_epoch(
            project_root=root,
            live_anchor=drifted_anchor,
        )


def test_first_parent_governance_rejects_incomplete_epoch_99_artifact_in_root_commit(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = _validator_module()
    root = tmp_path / "root-governance-artifact"
    root.mkdir(mode=0o700)
    _fixture_git(validator, root, "init", "--quiet")
    relative = Path(
        "docs/phase4/reports/"
        "P4.2a-v2-2-epoch99-surface-authority-20260820.json"
    )
    (root / relative).parent.mkdir(parents=True, mode=0o700)
    head = _fixture_commit_file(
        validator,
        root,
        relative,
        implementation._canonical_json_bytes(
            {"schema_version": "p4.2a-v2-2-incomplete-root-epoch-v1"}
        ),
    )
    chain = validator._first_parent_chain(root, head)

    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"landed epoch authority",
    ):
        validator._validate_first_parent_epoch_governance(root=root, chain=chain)


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


def test_bundle_epoch_table_accepts_only_exact_used_epoch_gap_two_four(
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
            outcome=(
                "FAILED"
                if ordinal == 1
                else "CANDIDATE_VALIDATED_AND_SELECTED"
            ),
            previous_history_root_sha256=str(ordinal) * 64,
            owner_action_time_authorization=owner,
        )
        for ordinal, epoch in ((1, 2), (2, 4))
    )
    history = SimpleNamespace(
        records=records,
        selected_attempt_ordinal=2,
        validated_candidate_count=1,
        incomplete_count=0,
        series_closed=True,
    )
    binding = SimpleNamespace(project_root=tmp_path)
    void = {
        "epoch": 1,
        "implementation_commit": implementation.VOID_EPOCH_ONE_IMPLEMENTATION_COMMIT,
        "owner_exact_surface_authorization": owner.as_json(),
        "independent_implementation_review": review.as_json(),
        "control_merkle_root_sha256": "5" * 64,
        "first_attempt_ordinal": 1,
        "last_attempt_ordinal": 1,
        "all_attempts_authorized": True,
    }

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
        epoch = 2 if expected_ordinal == 1 else 4
        return SimpleNamespace(
            implementation_epoch=epoch,
            implementation_commit=str(epoch) * 40,
            owner_surface_authorization=owner,
            independent_implementation_review=review,
            control_merkle_root_sha256=str(epoch + 5) * 64,
            creating_commit=str(epoch + 7) * 40,
        )

    monkeypatch.setattr(implementation, "_current_execution_head", lambda _root: "9" * 40)
    monkeypatch.setattr(implementation, "_void_epoch_one", lambda *_args, **_kwargs: void)
    void_three = _void_epoch_three_document(_validator_module())
    monkeypatch.setattr(
        implementation,
        "_void_epoch_three",
        lambda *_args, **_kwargs: void_three,
    )
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
    assert [row["epoch"] for row in observed] == [1, 2, 3, 4]
    assert observed[0] == void
    assert observed[2] == void_three
    assert [(row["first_attempt_ordinal"], row["last_attempt_ordinal"]) for row in observed] == [
        (1, 1),
        (1, 1),
        (2, 2),
        (2, 2),
    ]

    for used_epochs in ((2, 5), (1, 3)):
        gap_history = SimpleNamespace(
            records=tuple(
                SimpleNamespace(
                    **{
                        **vars(record),
                        "implementation_epoch": epoch,
                    }
                )
                for record, epoch in zip(records, used_epochs, strict=True)
            ),
            selected_attempt_ordinal=2,
            validated_candidate_count=1,
            incomplete_count=0,
            series_closed=True,
        )
        with pytest.raises(
            implementation.RehearsalV22Error,
            match="epoch numbers are not contiguous",
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


def test_void_epoch_three_is_exact_and_excluded_from_interval_accounting() -> None:
    validator = _validator_module()
    void_one = {
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
    epoch_two = _ordinary_epoch_document(2, first=1, last=1)
    void_three = _void_epoch_three_document(validator)
    epoch_four = _ordinary_epoch_document(4, first=2, last=2)
    observed = validator._epoch_map(
        {
            "implementation_epochs": [
                void_one,
                epoch_two,
                void_three,
                epoch_four,
            ]
        }
    )
    assert list(observed) == [1, 2, 3, 4]
    assert validator._is_void_epoch_one(observed[1]) is True
    assert validator._is_void_epoch_three(observed[3]) is True
    assert [
        (row["first_attempt_ordinal"], row["last_attempt_ordinal"])
        for row in observed.values()
    ] == [(1, 1), (1, 1), (2, 2), (2, 2)]

    bundle_schema, release_schema = _schemas()
    bundle_epoch_schema = {
        "$schema": bundle_schema["$schema"],
        "$defs": bundle_schema["$defs"],
        "$ref": "#/$defs/implementationEpoch",
    }
    assert not list(
        Draft202012Validator(bundle_epoch_schema).iter_errors(void_three)
    )
    release_void = {
        "epoch": void_three["epoch"],
        "implementation_commit": void_three["implementation_commit"],
        "owner_surface_authorization": void_three[
            "owner_exact_surface_authorization"
        ],
        "independent_implementation_review": void_three[
            "independent_implementation_review"
        ],
        "control_merkle_root_sha256": void_three[
            "control_merkle_root_sha256"
        ],
        "first_attempt_ordinal": void_three["first_attempt_ordinal"],
        "last_attempt_ordinal": void_three["last_attempt_ordinal"],
    }
    release_epoch_schema = {
        "$schema": release_schema["$schema"],
        "$defs": release_schema["$defs"],
        "$ref": "#/$defs/implementationEpoch",
    }
    assert not list(
        Draft202012Validator(release_epoch_schema).iter_errors(release_void)
    )

    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"void epoch 3.*epoch 4|executed epoch 4",
    ):
        validator._epoch_map(
            {"implementation_epochs": [void_one, epoch_two, void_three]}
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "epoch",
        "implementation",
        "owner",
        "reason",
        "control",
        "first",
        "last",
        "authorization",
    ),
)
def test_void_epoch_three_tamper_is_never_treated_as_a_sentinel(
    mutation: str,
) -> None:
    validator = _validator_module()
    void_one = {
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
    epoch_two = _ordinary_epoch_document(2, first=1, last=1)
    void_three = _void_epoch_three_document(validator)
    epoch_four = _ordinary_epoch_document(4, first=2, last=2)
    if mutation == "epoch":
        void_three["epoch"] = 4
    elif mutation == "implementation":
        void_three["implementation_commit"] = "0" * 40
    elif mutation == "owner":
        void_three["owner_exact_surface_authorization"]["sha256"] = "0" * 64
    elif mutation == "reason":
        void_three["independent_implementation_review"]["sha256"] = "0" * 64
    elif mutation == "control":
        void_three["control_merkle_root_sha256"] = "0" * 64
    elif mutation == "first":
        void_three["first_attempt_ordinal"] = 3
    elif mutation == "last":
        void_three["last_attempt_ordinal"] = 3
    else:
        void_three["all_attempts_authorized"] = False
    assert validator._is_void_epoch_three(void_three) is False
    with pytest.raises(validator.RehearsalV22ValidationError):
        validator._epoch_map(
            {
                "implementation_epochs": [
                    void_one,
                    epoch_two,
                    void_three,
                    epoch_four,
                ]
            }
        )


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


def test_fixed_void_epoch_three_real_lineage_round_trips_all_pinned_rulings(
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
            merkle_root_sha256=validator.VOID_EPOCH_THREE_CONTROL_ROOT_SHA256,
            loaded_repository_sources=(),
            ast_closure_paths=(),
            records=tuple(
                {
                    "repository_path": (
                        f"synthetic/control/member-{index:02d}.json"
                    )
                }
                for index in range(validator.VOID_EPOCH_THREE_CONTROL_RECORD_COUNT)
            ),
        )

    monkeypatch.setattr(implementation, "_validate_git_metadata_authority", lambda _root: None)
    monkeypatch.setattr(implementation, "build_control_surface", control)
    before = _all_real_path_fingerprints()
    epoch = implementation._void_epoch_three(
        SimpleNamespace(project_root=PROJECT_ROOT),
        execution_head=execution_head,
    )
    validator._validate_void_epoch_three(
        project_root=PROJECT_ROOT,
        epoch=epoch,
        execution_head=execution_head,
    )
    assert epoch == _void_epoch_three_document(validator)
    assert calls == [
        (
            PROJECT_ROOT,
            validator.VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT,
            False,
        ),
        (
            PROJECT_ROOT,
            validator.VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT,
            False,
        ),
    ]
    producer_source = inspect.getsource(implementation._void_epoch_three)
    validator_source = inspect.getsource(validator._validate_void_epoch_three)
    producer_pins = (
        "VOID_EPOCH_THREE_REVIEW_COMMIT",
        "VOID_EPOCH_THREE_LANDING_COMMIT",
        "VOID_EPOCH_THREE_GATE_RULING_COMMIT",
        "VOID_EPOCH_THREE_REANCHOR_COMMIT",
        "VOID_EPOCH_THREE_REASON_COMMIT",
    )
    validator_pins = (
        "VOID_EPOCH_THREE_REVIEW_COMMIT",
        "VOID_EPOCH_THREE_LANDING_COMMIT",
        "VOID_EPOCH_THREE_GATE_ADJUDICATION_COMMIT",
        "VOID_EPOCH_THREE_REANCHOR_COMMIT",
        "VOID_EPOCH_THREE_REASON_COMMIT",
    )
    assert all(name in producer_source for name in producer_pins)
    assert all(name in validator_source for name in validator_pins)
    review_path = validator.VOID_EPOCH_THREE_REVIEW_RELATIVE.as_posix()
    assert set(
        validator._path_history_touches(PROJECT_ROOT, relative=review_path)
    ) == {
        (
            validator.VOID_EPOCH_THREE_REVIEW_COMMIT,
            "A",
            (review_path,),
        ),
        (
            validator.VOID_EPOCH_THREE_LANDING_COMMIT,
            "A",
            (review_path,),
        ),
    }
    source_review_payload = validator._git_blob(
        PROJECT_ROOT,
        validator.VOID_EPOCH_THREE_REVIEW_COMMIT,
        review_path,
    )
    assert hashlib.sha256(source_review_payload).hexdigest() == (
        validator.VOID_EPOCH_THREE_REVIEW_SHA256
    )
    assert validator._git_blob(
        PROJECT_ROOT,
        validator.VOID_EPOCH_THREE_LANDING_COMMIT,
        review_path,
    ) == source_review_payload
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
        require_worktree=True,
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
            require_worktree=True,
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
            require_worktree=True,
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
                require_worktree=True,
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
                require_worktree=True,
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


def test_historical_initial_sibling_calls_never_require_worktree_bytes(
    initial_sibling_git_repository: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _validator_module()
    for function_name in (
        "_validate_control_archive",
        "_validate_void_epoch_one",
        "_validate_implementation_epochs",
    ):
        tree = ast.parse(inspect.getsource(getattr(validator, function_name)))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_validate_initial_sibling_authority"
        ]
        assert calls, function_name
        for call in calls:
            values = [
                keyword.value
                for keyword in call.keywords
                if keyword.arg == "require_worktree"
            ]
            assert len(values) == 1
            assert isinstance(values[0], ast.Constant)
            assert values[0].value is False

    def forbidden_worktree_read(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("historical validation attempted a worktree byte read")

    monkeypatch.setattr(validator, "_regular_bytes", forbidden_worktree_read)
    payload = validator._validate_initial_sibling_authority(
        initial_sibling_git_repository["root"],
        _initial_sibling_reference(),
        execution_head=initial_sibling_git_repository["execution_head"],
        require_worktree=False,
    )
    assert hashlib.sha256(payload).hexdigest() == INITIAL_SIBLING_SHA256


def test_historical_action_owner_and_review_calls_never_request_current_bytes() -> None:
    validator = _validator_module()

    def false_keywords(function_name: str, callee: str, keyword: str) -> list[ast.Constant]:
        tree = ast.parse(inspect.getsource(getattr(validator, function_name)))
        values: list[ast.Constant] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = (
                function.id
                if isinstance(function, ast.Name)
                else function.attr
                if isinstance(function, ast.Attribute)
                else ""
            )
            if name != callee:
                continue
            matches = [item.value for item in node.keywords if item.arg == keyword]
            assert len(matches) == 1, (function_name, callee, keyword)
            assert isinstance(matches[0], ast.Constant)
            values.append(matches[0])
        assert values, (function_name, callee, keyword)
        assert all(value.value is False for value in values)
        return values

    false_keywords(
        "_validate_attempt_history_records",
        "_unique_a_authority",
        "require_worktree",
    )
    false_keywords(
        "_validate_implementation_epochs",
        "_unique_a_authority",
        "require_worktree",
    )
    false_keywords(
        "_validate_implementation_epochs",
        "_validate_implementation_review_authority",
        "require_worktree",
    )
    false_keywords(
        "_validate_implementation_epochs",
        "validate_implementation_epoch",
        "require_current_bytes",
    )
    lineage_source = inspect.getsource(validator._validate_lineage)
    assert "_regular_bytes(" not in lineage_source
    lineage_tree = ast.parse(lineage_source)
    lineage_authority_calls = [
        node
        for node in ast.walk(lineage_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_unique_a_authority"
    ]
    assert lineage_authority_calls
    for call in lineage_authority_calls:
        values = [item.value for item in call.keywords if item.arg == "require_worktree"]
        assert len(values) == 1
        assert isinstance(values[0], ast.Constant)
        assert values[0].value is False


def test_producer_void_and_history_archive_governance_is_git_only() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)

    def false_keyword_calls(function_name: str, callee: str, keyword: str) -> int:
        tree = ast.parse(inspect.getsource(getattr(implementation, function_name)))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == callee
        ]
        assert calls, (function_name, callee)
        for call in calls:
            values = [item.value for item in call.keywords if item.arg == keyword]
            assert len(values) == 1, (function_name, callee, keyword)
            assert isinstance(values[0], ast.Constant)
            assert values[0].value is False
        return len(calls)

    assert false_keyword_calls(
        "_void_epoch_one",
        "validate_unique_a_authority",
        "require_current",
    ) == 3
    assert false_keyword_calls(
        "_void_epoch_three",
        "validate_unique_a_authority",
        "require_current",
    ) == 3
    assert false_keyword_calls(
        "_void_epoch_three",
        "_later_epoch_surface",
        "require_current",
    ) == 1
    assert false_keyword_calls(
        "_history_archive",
        "validate_unique_a_authority",
        "require_current",
    ) == 1
    history_source = inspect.getsource(implementation._history_archive)
    assert "action_path =" not in history_source
    assert "action_payload = _regular_bytes" not in history_source
    assert "record.owner_action_time_authorization" in history_source
    assert "_sha256(action_payload)" in history_source


def test_producer_history_archive_uses_action_creation_blob_when_worktree_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    project_root = (tmp_path / "history-archive-project").absolute()
    project_root.mkdir(mode=0o700)
    ledger_root = (tmp_path / "history-archive-ledger").absolute()
    ledger_root.mkdir(mode=0o700)
    (ledger_root / "series.json").write_bytes(b"{}\n")
    (ledger_root / ".series.lock").write_bytes(b"")
    token = "1" * 64
    binding = implementation.ExecutionBinding(
        mode="DISPOSABLE_FULL_SHAPE_TEST",
        project_root=project_root,
        shim_path=project_root / SHIM_RELATIVE,
        action_authorization_path=project_root / "unused.json",
        destination=project_root / "destination",
        series_token_sha256=token,
        ledger_root=ledger_root,
    )
    action_relative = (
        "docs/phase4/reports/"
        "P4.2a-v2-2-rehearsal-attempt-000001-execution-authorization-20260811.json"
    )
    action_payload = implementation._canonical_json_bytes({"authority": "immutable-git"})
    action = implementation.AuthorityReference(
        action_relative,
        hashlib.sha256(action_payload).hexdigest(),
        "2" * 40,
    )
    attempt_root = ledger_root / "attempts/000001"
    record = implementation.ValidatedAttemptRecord(
        ordinal=1,
        outcome="CANDIDATE_VALIDATED_AND_SELECTED",
        reached_stage="bundle_candidate_validated",
        attempt_token_sha256="3" * 64,
        previous_history_root_sha256="4" * 64,
        implementation_epoch=4,
        implementation_commit="5" * 40,
        owner_action_time_authorization=action,
        command_sha256="6" * 64,
        environment_sha256="7" * 64,
        started_path=attempt_root / "started.json",
        started_bytes=b'{"started":true}\n',
        started_sha256="8" * 64,
        candidate_path=attempt_root / "candidate.json",
        candidate_bytes=b'{"candidate":true}\n',
        candidate_sha256="9" * 64,
        terminal_path=attempt_root / "terminal.json",
        terminal_bytes=b'{"terminal":true}\n',
        terminal_sha256="a" * 64,
        evidence_tree_root_sha256=implementation._evidence_empty_root_sha256(),
        artifact_inventory=(),
        error=None,
        record_root_sha256="b" * 64,
        history_root_sha256="c" * 64,
    )
    history = implementation.HistoryValidation(
        binding=binding,
        ledger_exists=True,
        records=(record,),
        started_count=1,
        failed_count=0,
        incomplete_count=0,
        validated_candidate_count=1,
        selected_attempt_ordinal=1,
        series_closed=True,
        history_root_sha256="c" * 64,
        live_ledger_root_sha256="d" * 64,
        live_file_inventory=(),
    )
    action_worktree = project_root / action_relative
    original_regular_bytes = implementation._regular_bytes
    calls: list[tuple[str, bool | None]] = []

    def regular_bytes(path: Path, label: str, **kwargs: object) -> bytes:
        if path == action_worktree:
            raise AssertionError("history archive read action authority from worktree")
        return original_regular_bytes(path, label, **kwargs)

    def unique_authority(
        _root: Path,
        authority: Any,
        **kwargs: object,
    ) -> bytes:
        calls.append((authority.path, kwargs.get("require_current")))
        return action_payload

    monkeypatch.setattr(implementation, "_regular_bytes", regular_bytes)
    monkeypatch.setattr(implementation, "_current_execution_head", lambda _root: "e" * 40)
    monkeypatch.setattr(implementation, "validate_unique_a_authority", unique_authority)
    archive = implementation._history_archive(binding, history)
    archived_path = "archive/attempt-history/attempts/000001/action-time-authorization.json"
    assert archive.payloads[archived_path] == action_payload
    assert calls == [(action_relative, False)]
    assert not os.path.lexists(action_worktree)


def test_recovery_schema_and_lineage_read_selected_git_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    selected_commit = "1" * 40
    observed: list[tuple[str, str]] = []

    def git_blob(_root: Path, commit: str, relative: str) -> bytes:
        assert commit == selected_commit
        observed.append((commit, relative))
        return (PROJECT_ROOT / relative).read_bytes()

    def forbidden_current_read(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("recovery historical schema/lineage read current bytes")

    monkeypatch.setattr(implementation, "_git_blob", git_blob)
    monkeypatch.setattr(implementation, "_regular_bytes", forbidden_current_read)
    schema = implementation._bundle_schema(
        PROJECT_ROOT,
        historical_selected_commit=selected_commit,
    )
    lineage = implementation._bundle_lineage(
        PROJECT_ROOT,
        implementation_commit=selected_commit,
        historical_selected_commit=selected_commit,
    )
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert lineage["implementation_commit"] == selected_commit
    assert (selected_commit, BUNDLE_SCHEMA_RELATIVE.as_posix()) in observed
    assert len(observed) > 10
    build_source = inspect.getsource(implementation._build_bundle)
    assert "isinstance(run_a, _SealedPipelineReplay)" in build_source
    assert "historical_anchor.selected_commit if all(sealed_inputs) else None" in build_source
    assert "historical_selected_commit=historical_source_commit" in build_source


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
