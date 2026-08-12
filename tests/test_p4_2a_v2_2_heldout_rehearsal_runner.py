from __future__ import annotations

import ast
import copy
import fcntl
import hashlib
import importlib
import inspect
import json
import os
import signal
import stat
import subprocess
import sys
import time
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from jsonschema import Draft202012Validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREREGISTRATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-2-preregistration-20260811.json"
)
V2_1_PREREGISTRATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-1-preregistration-20260810.json"
)
BUNDLE_SCHEMA_RELATIVE = Path("config/schemas/p4_2a_v2_2_heldout_rehearsal_bundle.schema.json")
RELEASE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_2_heldout_release_authorization.schema.json"
)
SHIM_RELATIVE = Path("scripts/rehearse_p4_2a_v2_2_heldout_full_path.py")
IMPLEMENTATION_RELATIVE = Path("scripts/p4_2a_v2_2_heldout_rehearsal.py")
VALIDATOR_RELATIVE = Path("scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py")
RUNNER_TEST_RELATIVE = Path("tests/test_p4_2a_v2_2_heldout_rehearsal_runner.py")
VALIDATOR_TEST_RELATIVE = Path("tests/test_p4_2a_v2_2_heldout_rehearsal_validator.py")
IMPLEMENTATION_MODULE = "scripts.p4_2a_v2_2_heldout_rehearsal"
VALIDATOR_MODULE = "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle"
PREREGISTRATION_COMMIT = "be6423506f598c290db7ad944b002763fdf806ab"
PREREGISTRATION_PARENT = "5fe756401f20e67ff5c868bf29f099c1bfe5b4d3"
INITIAL_IMPLEMENTATION_COMMIT = "cf10ef8d636049b0fc206c8698a809be3090e1d7"
INDEPENDENT_REVIEW_COMMIT = "b21e1bdbf865dfd9c7605ecc7794fc3f8701ed1f"
INDEPENDENT_REVIEW_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-preregistration-independent-review-20260811.json"
)
INDEPENDENT_REVIEW_SHA256 = "6707e2b3c0b2ba87712e88b59ceaed17524be2de947b764a94c8b170b2a30bb6"
V2_1_IMPLEMENTATION_COMMIT = "4fce89e89fe2dba656694a7cffdc0ee1af0305c0"
V2_1_IMPLEMENTATION_PARENT = "d37040be87644977ddaad60b2590ac2e62b2aeed"
V2_1_EXACT_SURFACE = (
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
V2_1_CONSUMED_CLAIM_TOKEN = "52378ddcda558a8489795c62a5c4d290687700801320508c03c51589c202e962"
V2_1_CONSUMED_CLAIM = Path(
    "/Users/ouyangduning/Documents/project/interesting/"
    ".alphapilot-p4-2a-v2-1-execution-claim-" + V2_1_CONSUMED_CLAIM_TOKEN
)
REGISTERED_SURFACE = (
    SHIM_RELATIVE,
    IMPLEMENTATION_RELATIVE,
    VALIDATOR_RELATIVE,
    RUNNER_TEST_RELATIVE,
    VALIDATOR_TEST_RELATIVE,
)
EXPECTED_V2_1_MINT_PREREQUISITE_CONTROLS = (
    (
        Path(
            "docs/phase4/reports/"
            "P4.2a-v2-heldout-rehearsal-v2-1-preregistration-20260810.json"
        ),
        "c303cfb13a42ecbb7e0acaec04de12a9e9169b89cf9e93ea79d0f120d1439d3e",
    ),
    (
        Path("config/schemas/p4_2a_v2_1_heldout_rehearsal_bundle.schema.json"),
        "ed827e29ce853f07a9110d44c98793a4cc3ef0634a12fe7e8bc64c7290d7d716",
    ),
    (
        Path("config/schemas/p4_2a_v2_1_heldout_release_authorization.schema.json"),
        "c5a4ecfe8c5bf3e3ebea2d4470337a67dde3a8e9dbe6fc3df68b1c4e16241c51",
    ),
    (
        Path(
            "docs/phase4/reports/"
            "P4.2a-rehearsal-v2-approval-and-frame-authority-ruling-20260810.json"
        ),
        "8605dd30dd6bffa9621b6efe5e01c4c9cead615c9639259c2e79e14fcbbc3421",
    ),
    (
        Path(
            "docs/phase4/reports/"
            "P4.2a-successor-v2-1-code-gate-authorization-20260810.json"
        ),
        "e28db692dc150983f86f6760fb1a95584d8607658e8a78a0de35cf3fc81940cd",
    ),
)
EXPECTED_SYNTHETIC_RELEASE_REVIEWER = {
    "identity": "synthetic_disposable_full_shape_test_reviewer",
    "reviewer_type": "ai",
    "model": None,
    "method": (
        "deterministic synthetic receipt construction followed by the same active "
        "release validator; not a real owner approval"
    ),
    "independent_of_operator": True,
}
RELEASE_RECEIPT_DYNAMIC_KEYS = frozenset(
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


def _preregistration() -> dict[str, Any]:
    document = json.loads((PROJECT_ROOT / PREREGISTRATION_RELATIVE).read_bytes())
    assert isinstance(document, dict)
    return document


def _git(*arguments: str) -> bytes:
    environment = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    completed = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "tag.gpgSign=false",
            "-C",
            PROJECT_ROOT.as_posix(),
            *arguments,
        ],
        check=True,
        capture_output=True,
        env=environment,
    )
    return completed.stdout


def _fixture_git(project_root: Path, *arguments: str) -> bytes:
    environment = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_AUTHOR_NAME": "AlphaPilot v2.2 synthetic test",
        "GIT_AUTHOR_EMAIL": "v2-2-test@invalid.local",
        "GIT_COMMITTER_NAME": "AlphaPilot v2.2 synthetic test",
        "GIT_COMMITTER_EMAIL": "v2-2-test@invalid.local",
        "GIT_AUTHOR_DATE": "2026-08-11T12:00:00Z",
        "GIT_COMMITTER_DATE": "2026-08-11T12:00:00Z",
    }
    completed = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "tag.gpgSign=false",
            "-C",
            project_root.as_posix(),
            *arguments,
        ],
        check=True,
        capture_output=True,
        env=environment,
    )
    return completed.stdout


def _fixture_commit_file(project_root: Path, relative: Path, payload: bytes) -> str:
    path = project_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    _fixture_git(project_root, "add", "--", relative.as_posix())
    _fixture_git(project_root, "commit", "--quiet", "-m", f"add {relative.name}")
    return _fixture_git(project_root, "rev-parse", "HEAD").decode().strip()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_pointer(value: object, pointer: str) -> object:
    assert pointer.startswith("/") and pointer != "/"
    current = value
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            assert part in current
            current = current[part]
        elif isinstance(current, list):
            assert part.isascii() and part.isdecimal()
            current = current[int(part)]
        else:
            raise AssertionError(f"JSON pointer traversed a scalar: {pointer}")
    return current


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


def _imported_modules(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module not in {None, "__future__"}:
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


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
            digest = _sha256(candidate.read_bytes())
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


def _real_registered_paths() -> tuple[Path, Path]:
    preregistration = _preregistration()
    exact_os = preregistration["exact_os_bootstrap_contract"]
    ledger = Path(preregistration["series_ledger_contract"]["root_path"])
    destination = Path(exact_os["repository_root"]) / (
        "docs/phase4/rehearsals/P4.2a-v2-calibration-v2-2"
    )
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


def _locked_environment() -> dict[str, str]:
    values = _preregistration()["exact_os_bootstrap_contract"]["exact_environment_values"]
    assert isinstance(values, dict)
    return {str(key): str(value) for key, value in values.items()}


def _fixed_interpreter() -> Path:
    value = _preregistration()["exact_os_bootstrap_contract"]["python_launcher_path"]
    launcher = Path(str(value))
    assert launcher.is_file()
    return launcher


def _direct_execution(
    relative: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _fixed_interpreter().as_posix(),
            "-S",
            "-P",
            "-B",
            (PROJECT_ROOT / relative).as_posix(),
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=_locked_environment() if environment is None else environment,
    )


def _synthetic_binding(tmp_path: Path, *, label: str = "series") -> Any:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    project_root = tmp_path / f"project-{label}"
    project_root.mkdir(mode=0o700)
    destination = project_root / "docs/phase4/rehearsals/P4.2a-v2-calibration-v2-2"
    token = hashlib.sha256(
        (
            implementation.INCIDENT_SHA256
            + "\0"
            + implementation.REHEARSAL_ID
            + "\0"
            + destination.absolute().as_posix()
        ).encode("utf-8")
    ).hexdigest()
    return implementation.ExecutionBinding(
        mode="DISPOSABLE_FULL_SHAPE_TEST",
        project_root=project_root,
        shim_path=project_root / SHIM_RELATIVE,
        action_authorization_path=(
            project_root / "docs/phase4/reports/"
            "P4.2a-v2-2-rehearsal-attempt-000001-execution-authorization-20260811.json"
        ),
        destination=destination,
        series_token_sha256=token,
        ledger_root=project_root.parent
        / f".alphapilot-p4-2a-v2-2-execution-claim-{token}",
    )


def _synthetic_release_receipt(
    tmp_path: Path,
    *,
    label: str = "release-receipt",
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label=label)
    release_schema_path = binding.project_root / RELEASE_SCHEMA_RELATIVE
    release_schema_path.parent.mkdir(parents=True)
    release_schema_path.write_bytes((PROJECT_ROOT / RELEASE_SCHEMA_RELATIVE).read_bytes())
    release_schema = implementation._release_schema(binding.project_root)

    lineage_properties = release_schema["properties"]["lineage"]["properties"]

    def exact_authority(name: str) -> dict[str, Any]:
        materialized = implementation._schema_const_template(
            release_schema,
            lineage_properties[name],
        )
        assert isinstance(materialized, dict)
        return materialized

    selected_implementation_commit = "1" * 40
    reviewed_head = "2" * 40
    records = [
        {
            "ordinal": 1,
            "outcome": "FAILED",
            "implementation_epoch": 1,
            "record_root_sha256": "1" * 64,
        },
        {
            "ordinal": 2,
            "outcome": "FAILED",
            "implementation_epoch": 1,
            "record_root_sha256": "2" * 64,
        },
        {
            "ordinal": 3,
            "outcome": "CANDIDATE_VALIDATED_AND_SELECTED",
            "implementation_epoch": 1,
            "record_root_sha256": "3" * 64,
        },
    ]
    bundle = {
        "attempt_history": {
            "records": records,
            "selected_attempt_ordinal": 3,
            "history_root_sha256": "4" * 64,
            "live_ledger_root_sha256": "5" * 64,
            "series_token_sha256": binding.series_token_sha256,
            "ledger_root": binding.ledger_root.as_posix(),
        },
        "implementation_epochs": [
            {
                "epoch": 1,
                "implementation_commit": selected_implementation_commit,
                "owner_exact_surface_authorization": {
                    "path": "docs/phase4/reports/synthetic-owner-surface.json",
                    "sha256": "6" * 64,
                    "creating_commit": "3" * 40,
                    "unique_a_history_verified": True,
                },
                "independent_implementation_review": {
                    "path": "docs/phase4/reports/synthetic-implementation-review.json",
                    "sha256": "7" * 64,
                    "creating_commit": "4" * 40,
                    "unique_a_history_verified": True,
                },
                "control_merkle_root_sha256": "8" * 64,
                "first_attempt_ordinal": 1,
                "last_attempt_ordinal": 3,
            }
        ],
        "lineage": {
            "preregistration": {
                "path": PREREGISTRATION_RELATIVE.as_posix(),
                "sha256": "9" * 64,
            },
            "bundle_schema": {
                "path": BUNDLE_SCHEMA_RELATIVE.as_posix(),
                "sha256": "a" * 64,
            },
            "release_authorization_schema": {
                "path": RELEASE_SCHEMA_RELATIVE.as_posix(),
                "sha256": "b" * 64,
            },
            "preregistration_commit": PREREGISTRATION_COMMIT,
            "v2_1_consumed_attempt_incident": exact_authority("v2_1_incident"),
            "v2_2_remediation_request": exact_authority("remediation_request"),
            "v2_2_preregistration_scope_authorization": exact_authority(
                "v2_2_scope_authorization"
            ),
        },
        "merkle": {"bundle_root_sha256": "c" * 64},
        "execution_binding": implementation._execution_binding_document(binding),
    }
    review_request = implementation.AuthorityReference(
        path=(
            "docs/phase4/reports/"
            "P4.2a-v2-heldout-rehearsal-v2-2-implementation-and-execution-"
            "review-request-20260811.json"
        ),
        sha256="d" * 64,
        creating_commit=reviewed_head,
    )
    receipt = implementation._release_receipt_document(
        binding=binding,
        bundle=bundle,
        bundle_sha256="e" * 64,
        review_request=review_request,
        reviewed_head=reviewed_head,
    )
    assert isinstance(receipt, dict)
    return binding, release_schema, receipt


def _write_test_file(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    path.write_bytes(payload)
    path.chmod(mode)


def _clone_v2_1_mint_prerequisite_source(
    baseline: Path,
    root: Path,
) -> None:
    _fixture_git(
        root.parent,
        "clone",
        "--quiet",
        "--no-hardlinks",
        baseline.as_posix(),
        root.name,
    )
    for relative, expected_sha256 in EXPECTED_V2_1_MINT_PREREQUISITE_CONTROLS:
        assert _sha256((root / relative).read_bytes()) == expected_sha256


def _v2_1_mint_prerequisite_fingerprint(
    root: Path,
) -> tuple[tuple[Path, tuple[tuple[str, str, int, str], ...] | None], ...]:
    return tuple(
        (relative, _tree_fingerprint(root / relative))
        for relative, _expected_sha256 in EXPECTED_V2_1_MINT_PREREQUISITE_CONTROLS
    )


def _initialize_synthetic_ledger(binding: Any) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding.ledger_root.mkdir(mode=0o700)
    _write_test_file(
        binding.ledger_root / "series.json",
        implementation._canonical_json_bytes(
            implementation._series_document(
                binding,
                created_at_utc="2026-08-11T12:00:00Z",
            )
        ),
    )
    _write_test_file(binding.ledger_root / ".series.lock", b"")
    (binding.ledger_root / "attempts").mkdir(mode=0o700)


def _initialize_synthetic_epoch_one(
    binding: Any,
    *,
    include_initial_sibling: bool = True,
) -> tuple[str, Any, Any]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    _fixture_git(binding.project_root, "init", "--quiet")
    _fixture_git(
        binding.project_root,
        "remote",
        "add",
        "fixture-source",
        PROJECT_ROOT.as_posix(),
    )
    _fixture_git(
        binding.project_root,
        "fetch",
        "--quiet",
        "--no-tags",
        "fixture-source",
        "+refs/heads/*:refs/remotes/fixture-source/*",
    )
    _fixture_git(
        binding.project_root,
        "checkout",
        "--quiet",
        "-b",
        "synthetic-series",
        PREREGISTRATION_COMMIT,
    )
    for relative in REGISTERED_SURFACE:
        destination = binding.project_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((PROJECT_ROOT / relative).read_bytes())
    _fixture_git(
        binding.project_root,
        "add",
        "--",
        *(relative.as_posix() for relative in REGISTERED_SURFACE),
    )
    _fixture_git(binding.project_root, "commit", "--quiet", "-m", "synthetic epoch one")
    implementation_commit = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
    review_relative = Path(
        "docs/phase4/reports/P4.2a-v2-2-synthetic-epoch-1-implementation-review.json"
    )
    review_payload = implementation._canonical_json_bytes(
        {
            "schema_version": "p4.2a-v2-2-synthetic-implementation-review-v1",
            "verdict": "APPROVE_V2_2_IMPLEMENTATION",
            "reviewed_commit": implementation_commit,
            "blockers": [],
        }
    )
    review_commit = _fixture_commit_file(
        binding.project_root,
        review_relative,
        review_payload,
    )
    if include_initial_sibling:
        _fixture_git(
            binding.project_root,
            "merge",
            "--quiet",
            "--no-ff",
            "--no-edit",
            INDEPENDENT_REVIEW_COMMIT,
        )
    owner_surface = implementation.AuthorityReference(
        path=INDEPENDENT_REVIEW_RELATIVE.as_posix(),
        sha256=INDEPENDENT_REVIEW_SHA256,
        creating_commit=INDEPENDENT_REVIEW_COMMIT,
    )
    independent_review = implementation.AuthorityReference(
        path=review_relative.as_posix(),
        sha256=_sha256(review_payload),
        creating_commit=review_commit,
    )
    return implementation_commit, owner_surface, independent_review


def _initialize_synthetic_epoch_two(
    binding: Any,
    *,
    add_extra_path: bool = False,
    review_verdict: str = "APPROVE_V2_2_IMPLEMENTATION",
) -> tuple[str, Any, Any, str]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    _initialize_synthetic_epoch_one(binding)
    base_commit = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
    target_relative = IMPLEMENTATION_RELATIVE
    surface_relative = Path(
        "docs/phase4/reports/P4.2a-v2-2-synthetic-epoch-2-surface-authorization.json"
    )
    surface_payload = implementation._canonical_json_bytes(
        {
            "schema_version": ("p4.2a-v2-2-implementation-epoch-surface-authorization-v1"),
            "verdict": "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
            "owner": {"identity": "ouyang", "approved": True},
            "implementation_epoch": 2,
            "base_commit": base_commit,
            "exact_surface": [
                {"path": target_relative.as_posix(), "status": "M"},
            ],
        }
    )
    surface_commit = _fixture_commit_file(
        binding.project_root,
        surface_relative,
        surface_payload,
    )
    target = binding.project_root / target_relative
    target.write_bytes(target.read_bytes() + b"\n# synthetic epoch two byte\n")
    changed_paths = [target_relative]
    if add_extra_path:
        extra_relative = Path("docs/phase4/reports/synthetic-forbidden-extra.txt")
        extra = binding.project_root / extra_relative
        extra.write_bytes(b"outside registered harness surface\n")
        changed_paths.append(extra_relative)
    _fixture_git(
        binding.project_root,
        "add",
        "--",
        *(relative.as_posix() for relative in changed_paths),
    )
    _fixture_git(binding.project_root, "commit", "--quiet", "-m", "synthetic epoch two")
    implementation_commit = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
    review_relative = Path(
        "docs/phase4/reports/P4.2a-v2-2-synthetic-epoch-2-implementation-review.json"
    )
    review_payload = implementation._canonical_json_bytes(
        {
            "schema_version": "p4.2a-v2-2-synthetic-implementation-review-v1",
            "verdict": review_verdict,
            "reviewed_commit": implementation_commit,
            "blockers": [],
        }
    )
    review_commit = _fixture_commit_file(
        binding.project_root,
        review_relative,
        review_payload,
    )
    owner_surface = implementation.AuthorityReference(
        path=surface_relative.as_posix(),
        sha256=_sha256(surface_payload),
        creating_commit=surface_commit,
    )
    independent_review = implementation.AuthorityReference(
        path=review_relative.as_posix(),
        sha256=_sha256(review_payload),
        creating_commit=review_commit,
    )
    return implementation_commit, owner_surface, independent_review, review_commit


def _advance_synthetic_epoch_two(binding: Any) -> tuple[str, Any, Any, str]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    base_commit = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
    surface_relative = Path(
        "docs/phase4/reports/P4.2a-v2-2-synthetic-epoch-2-surface-authorization.json"
    )
    surface_payload = implementation._canonical_json_bytes(
        {
            "schema_version": (
                "p4.2a-v2-2-implementation-epoch-surface-authorization-v1"
            ),
            "verdict": "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
            "owner": {"identity": "ouyang", "approved": True},
            "implementation_epoch": 2,
            "base_commit": base_commit,
            "exact_surface": [
                {"path": IMPLEMENTATION_RELATIVE.as_posix(), "status": "M"},
            ],
        }
    )
    surface_commit = _fixture_commit_file(
        binding.project_root,
        surface_relative,
        surface_payload,
    )
    target = binding.project_root / IMPLEMENTATION_RELATIVE
    target.write_bytes(target.read_bytes() + b"\n# synthetic authorized epoch two\n")
    _fixture_git(binding.project_root, "add", "--", IMPLEMENTATION_RELATIVE.as_posix())
    _fixture_git(binding.project_root, "commit", "--quiet", "-m", "synthetic epoch two")
    implementation_commit = _fixture_git(
        binding.project_root, "rev-parse", "HEAD"
    ).decode().strip()
    review_relative = Path(
        "docs/phase4/reports/P4.2a-v2-2-synthetic-epoch-2-implementation-review.json"
    )
    review_payload = implementation._canonical_json_bytes(
        {
            "schema_version": "p4.2a-v2-2-synthetic-implementation-review-v1",
            "verdict": "APPROVE_V2_2_IMPLEMENTATION",
            "reviewed_commit": implementation_commit,
            "blockers": [],
        }
    )
    review_commit = _fixture_commit_file(
        binding.project_root,
        review_relative,
        review_payload,
    )
    return (
        implementation_commit,
        implementation.AuthorityReference(
            path=surface_relative.as_posix(),
            sha256=_sha256(surface_payload),
            creating_commit=surface_commit,
        ),
        implementation.AuthorityReference(
            path=review_relative.as_posix(),
            sha256=_sha256(review_payload),
            creating_commit=review_commit,
        ),
        review_commit,
    )


def _synthetic_action_authorization(
    binding: Any,
    *,
    ordinal: int,
    previous_history_root: str,
    implementation_epoch: int = 1,
    implementation_commit: str = "2" * 40,
    owner_surface_authorization: Any | None = None,
    independent_review: Any | None = None,
    control_merkle_root_sha256: str = "f" * 64,
) -> Any:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    action_path = binding.project_root / (
        "docs/phase4/reports/"
        f"P4.2a-v2-2-rehearsal-attempt-{ordinal:06d}-execution-authorization-20260811.json"
    )
    exact_argv = (
        implementation.FIXED_ORIG_ARGV_EXECUTABLE.as_posix(),
        "-S",
        "-P",
        "-B",
        binding.shim_path.as_posix(),
        "--execute",
        "--attempt-authorization",
        action_path.as_posix(),
        "--expected-ordinal",
        str(ordinal),
    )
    exact_environment = dict(implementation.EXACT_ENVIRONMENT)
    authority = owner_surface_authorization or implementation.AuthorityReference(
        path="docs/phase4/reports/synthetic-epoch-authority.json",
        sha256="a" * 64,
        creating_commit="b" * 40,
    )
    review = independent_review or implementation.AuthorityReference(
        path="docs/phase4/reports/synthetic-epoch-review.json",
        sha256="c" * 64,
        creating_commit="d" * 40,
    )
    if (owner_surface_authorization is None) != (independent_review is None):
        raise AssertionError("synthetic epoch authorities must be supplied together")
    if owner_surface_authorization is None:
        payload = implementation._canonical_json_bytes({"synthetic": True, "ordinal": ordinal})
        creating_commit = "e" * 40
    else:
        payload = implementation._canonical_json_bytes(
            {
                "schema_version": ("p4.2a-v2-2-rehearsal-attempt-execution-authorization-v1"),
                "authorization_id": (
                    f"P4.2A-V2-2-REHEARSAL-ATTEMPT-{ordinal:06d}-EXECUTION-AUTHORIZATION-20260811"
                ),
                "created_at_utc": "2026-08-11T12:00:00Z",
                "created_at_shanghai": "2026-08-11T20:00:00+08:00",
                "verdict": ("APPROVE_EXACTLY_ONE_V2_2_REHEARSAL_ATTEMPT_ZERO_AUTOMATIC_RETRY"),
                "owner": {
                    "identity": "ouyang",
                    "approved": True,
                    "scope": "one_disclosed_v2_2_rehearsal_ordinal_only",
                },
                "series_id": implementation.REHEARSAL_ID,
                "series_token_sha256": binding.series_token_sha256,
                "ledger_root": binding.ledger_root.as_posix(),
                "ordinal": ordinal,
                "previous_history_root_sha256": previous_history_root,
                "implementation_epoch": implementation_epoch,
                "implementation_commit": implementation_commit,
                "owner_exact_surface_authorization": authority.as_json(),
                "independent_implementation_review": review.as_json(),
                "control_merkle_root_sha256": control_merkle_root_sha256,
                "exact_argv": list(exact_argv),
                "command_sha256": implementation._command_sha256(exact_argv),
                "exact_environment": exact_environment,
                "environment_sha256": implementation._environment_sha256(exact_environment),
                "authorized_pipeline_starts": 1,
                "automatic_retry_count": 0,
                "heldout_evaluation_authorized": False,
                "locks": {
                    "real_heldout_materialization": False,
                    "real_heldout_inference": False,
                    "heldout_evaluation": False,
                    "p4_2b": False,
                    "p4_3": False,
                    "trading": False,
                },
            }
        )
        creating_commit = _fixture_commit_file(
            binding.project_root,
            action_path.relative_to(binding.project_root),
            payload,
        )
    return implementation.ActionAuthorization(
        path=action_path,
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        creating_commit=creating_commit,
        ordinal=ordinal,
        previous_history_root_sha256=previous_history_root,
        implementation_epoch=implementation_epoch,
        implementation_commit=implementation_commit,
        owner_surface_authorization=authority,
        independent_implementation_review=review,
        control_merkle_root_sha256=control_merkle_root_sha256,
        exact_argv=exact_argv,
        command_sha256=implementation._command_sha256(exact_argv),
        exact_environment=exact_environment,
        environment_sha256=implementation._environment_sha256(exact_environment),
    )


def _exact_attempt_command(binding: Any, ordinal: int) -> list[str]:
    action_path = binding.project_root / (
        "docs/phase4/reports/"
        f"P4.2a-v2-2-rehearsal-attempt-{ordinal:06d}-execution-authorization-20260811.json"
    )
    return [
        _fixed_interpreter().as_posix(),
        "-S",
        "-P",
        "-B",
        binding.shim_path.as_posix(),
        "--execute",
        "--attempt-authorization",
        action_path.as_posix(),
        "--expected-ordinal",
        str(ordinal),
    ]


def _spawn_exact_attempt(binding: Any, ordinal: int) -> subprocess.Popen[str]:
    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        return subprocess.Popen(
            _exact_attempt_command(binding, ordinal),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_locked_environment(),
        )
    finally:
        signal.signal(signal.SIGINT, previous_sigint)


def _wait_for_disposable_started_checkpoint(
    process: subprocess.Popen[str],
    binding: Any,
    ordinal: int,
    *,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        waited_pid, status = os.waitpid(process.pid, os.WNOHANG | os.WUNTRACED)
        if waited_pid == 0:
            time.sleep(0.001)
            continue
        if os.WIFSTOPPED(status):
            assert os.WSTOPSIG(status) == signal.SIGSTOP
            return _wait_for_started(process, binding, ordinal, timeout_seconds=10.0)
        if os.WIFEXITED(status):
            process.returncode = os.waitstatus_to_exitcode(status)
        elif os.WIFSIGNALED(status):
            process.returncode = -os.WTERMSIG(status)
        stdout, stderr = process.communicate()
        raise AssertionError(
            "exact-OS child exited without the disposable started checkpoint: "
            f"rc={process.returncode}; stdout={stdout!r}; stderr={stderr!r}"
        )
    process.kill()
    stdout, stderr = process.communicate(timeout=30)
    raise AssertionError(
        "timed out waiting for disposable SIGSTOP checkpoint; "
        f"stdout={stdout!r}; stderr={stderr!r}"
    )


def _wait_for_started(
    process: subprocess.Popen[str],
    binding: Any,
    ordinal: int,
    *,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    started_path = (
        binding.ledger_root / "attempts" / f"{ordinal:06d}" / "started.json"
    )
    deadline = time.monotonic() + timeout_seconds
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        if started_path.is_file() and not started_path.is_symlink():
            try:
                payload = started_path.read_bytes()
                document = implementation.strict_json_loads(
                    payload,
                    source=f"exact-OS attempt {ordinal} started",
                )
                assert isinstance(document, dict)
                assert document["ordinal"] == ordinal
                assert implementation._canonical_json_bytes(document) == payload
                return document
            except (AssertionError, OSError, ValueError) as exc:
                last_error = exc
        return_code = process.poll()
        if return_code is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"exact-OS child exited before started.json: rc={return_code}; "
                f"stdout={stdout!r}; stderr={stderr!r}"
            )
        time.sleep(0.001)
    process.kill()
    stdout, stderr = process.communicate(timeout=30)
    raise AssertionError(
        f"timed out waiting for started.json ({last_error!r}); "
        f"stdout={stdout!r}; stderr={stderr!r}"
    )


def _communicate_exact_attempt(
    process: subprocess.Popen[str],
    *,
    timeout_seconds: float = 300.0,
) -> tuple[int, str, str]:
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        stdout, stderr = process.communicate(timeout=30)
        raise AssertionError(
            f"exact-OS child timed out; stdout={stdout!r}; stderr={stderr!r}"
        ) from exc
    assert process.returncode is not None
    return process.returncode, stdout, stderr


def _exact_child_diagnostic(
    implementation: Any,
    *,
    returncode: int,
    stdout: str,
    stderr: str,
) -> str:
    return implementation._canonical_json_bytes(
        {
            "returncode": returncode,
            "stderr": stderr,
            "stdout": stdout,
        }
    ).decode("utf-8")


def _run_exact_attempt(
    binding: Any,
    ordinal: int,
    *,
    requested_outcome: str,
) -> dict[str, Any]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    assert requested_outcome in {"FAILED", "INCOMPLETE", "SUCCESS"}
    process = _spawn_exact_attempt(binding, ordinal)
    started = _wait_for_disposable_started_checkpoint(process, binding, ordinal)
    if requested_outcome == "INCOMPLETE":
        os.kill(process.pid, signal.SIGKILL)
    else:
        if requested_outcome == "FAILED":
            os.kill(process.pid, signal.SIGINT)
        os.kill(process.pid, signal.SIGCONT)
    completion_timeout_seconds = (
        3600.0 if requested_outcome == "SUCCESS" else 300.0
    )
    returncode, stdout, stderr = _communicate_exact_attempt(
        process,
        timeout_seconds=completion_timeout_seconds,
    )
    child_diagnostic = _exact_child_diagnostic(
        implementation,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
    if requested_outcome == "SUCCESS":
        assert returncode == 0, child_diagnostic
        assert stderr == "", child_diagnostic
        result = implementation.strict_json_loads(
            stdout,
            source=f"exact-OS success {ordinal}",
        )
        assert isinstance(result, dict)
        assert implementation._canonical_json_bytes(result).decode() == stdout
        assert result["status"] == "PASS_REHEARSAL_V2_2_AWAITING_OWNER_REVIEW"
    elif requested_outcome == "FAILED":
        assert returncode == 1, child_diagnostic
        assert stdout == "", child_diagnostic
        result = implementation.strict_json_loads(
            stderr,
            source=f"exact-OS failure {ordinal}",
        )
        assert isinstance(result, dict)
        assert implementation._canonical_json_bytes(result).decode() == stderr
        assert result["status"] == "FAILED_NO_AUTOMATIC_RETRY"
    else:
        assert returncode == -signal.SIGKILL, child_diagnostic
        assert stdout == "", child_diagnostic
        assert stderr == "", child_diagnostic
        result = None
    return {
        "requested_outcome": requested_outcome,
        "started": started,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "result": result,
    }


def _prepare_exact_action(
    binding: Any,
    *,
    ordinal: int,
    implementation_epoch: int,
    epoch: tuple[str, Any, Any],
    control_merkle_root_sha256: str | None = None,
) -> Any:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    history = implementation.validate_live_history(binding)
    assert len(history.records) + 1 == ordinal
    implementation_commit, owner_surface, independent_review = epoch
    if control_merkle_root_sha256 is None:
        control_merkle_root_sha256 = implementation.build_control_surface(
            binding.project_root,
            implementation_commit,
            require_current=False,
        ).merkle_root_sha256
    return _synthetic_action_authorization(
        binding,
        ordinal=ordinal,
        previous_history_root=history.history_root_sha256,
        implementation_epoch=implementation_epoch,
        implementation_commit=implementation_commit,
        owner_surface_authorization=owner_surface,
        independent_review=independent_review,
        control_merkle_root_sha256=control_merkle_root_sha256,
    )


@pytest.fixture(scope="module")
def v2_1_mint_prerequisite_source(
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    root = tmp_path_factory.mktemp("v22-mint-prerequisite-source").resolve()
    binding = _synthetic_binding(root, label="mint-prerequisite-baseline")
    _initialize_synthetic_epoch_one(binding)
    for relative, expected_sha256 in EXPECTED_V2_1_MINT_PREREQUISITE_CONTROLS:
        assert _sha256((binding.project_root / relative).read_bytes()) == expected_sha256
    return binding.project_root


@pytest.fixture(scope="module")
def exact_two_failures_then_success_series(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before = _all_real_path_fingerprints()
    root = tmp_path_factory.mktemp("v22-exact-two-failures-success").resolve()
    binding = _synthetic_binding(root, label="two-failures-success-exact")
    epoch = _initialize_synthetic_epoch_one(binding)
    control_root = implementation.build_control_surface(
        binding.project_root,
        epoch[0],
        require_current=False,
    ).merkle_root_sha256
    attempts: list[dict[str, Any]] = []
    for ordinal, requested_outcome in ((1, "FAILED"), (2, "FAILED"), (3, "SUCCESS")):
        _prepare_exact_action(
            binding,
            ordinal=ordinal,
            implementation_epoch=1,
            epoch=epoch,
            control_merkle_root_sha256=control_root,
        )
        attempts.append(
            _run_exact_attempt(
                binding,
                ordinal,
                requested_outcome=requested_outcome,
            )
        )
        history = implementation.validate_live_history(binding)
        assert len(history.records) == ordinal
        assert _all_real_path_fingerprints() == before
    bundle_path = binding.destination / implementation.BUNDLE_FILENAME
    bundle_payload = bundle_path.read_bytes()
    bundle = implementation.strict_json_loads(
        bundle_payload,
        source="exact two-failures-success bundle",
    )
    assert isinstance(bundle, dict)
    assert implementation._canonical_json_bytes(bundle) == bundle_payload
    return {
        "binding": binding,
        "epoch": epoch,
        "attempts": attempts,
        "history": history,
        "bundle_path": bundle_path,
        "bundle": bundle,
        "real_fingerprints": before,
    }


@pytest.fixture(scope="module")
def exact_failed_epoch_then_incomplete_success_series(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before = _all_real_path_fingerprints()
    root = tmp_path_factory.mktemp("v22-exact-failed-incomplete-success").resolve()
    binding = _synthetic_binding(root, label="failed-incomplete-success-exact")
    epoch_one = _initialize_synthetic_epoch_one(binding)
    control_one = implementation.build_control_surface(
        binding.project_root,
        epoch_one[0],
        require_current=False,
    ).merkle_root_sha256
    _prepare_exact_action(
        binding,
        ordinal=1,
        implementation_epoch=1,
        epoch=epoch_one,
        control_merkle_root_sha256=control_one,
    )
    first = _run_exact_attempt(binding, 1, requested_outcome="FAILED")
    first_history = implementation.validate_live_history(binding)
    assert first_history.records[0].outcome == "FAILED"
    assert (binding.ledger_root / "attempts/000001/terminal.json").is_file()

    epoch_two_full = _advance_synthetic_epoch_two(binding)
    epoch_two = epoch_two_full[:3]
    control_two = implementation.build_control_surface(
        binding.project_root,
        epoch_two[0],
        require_current=False,
    ).merkle_root_sha256
    _prepare_exact_action(
        binding,
        ordinal=2,
        implementation_epoch=2,
        epoch=epoch_two,
        control_merkle_root_sha256=control_two,
    )
    second = _run_exact_attempt(binding, 2, requested_outcome="INCOMPLETE")
    second_root = binding.ledger_root / "attempts/000002"
    assert list((second_root / "evidence").iterdir()) == []
    assert not (second_root / "candidate.json").exists()
    assert not (second_root / "terminal.json").exists()
    incomplete_history = implementation.validate_live_history(binding)
    assert incomplete_history.records[1].outcome == "INCOMPLETE_UNTERMINALIZED"
    assert incomplete_history.records[1].evidence_tree_root_sha256 == (
        implementation._evidence_empty_root_sha256()
    )

    _prepare_exact_action(
        binding,
        ordinal=3,
        implementation_epoch=2,
        epoch=epoch_two,
        control_merkle_root_sha256=control_two,
    )
    third = _run_exact_attempt(binding, 3, requested_outcome="SUCCESS")
    history = implementation.validate_live_history(binding)
    assert _all_real_path_fingerprints() == before
    bundle_path = binding.destination / implementation.BUNDLE_FILENAME
    bundle_payload = bundle_path.read_bytes()
    bundle = implementation.strict_json_loads(
        bundle_payload,
        source="exact failed-incomplete-success bundle",
    )
    assert isinstance(bundle, dict)
    assert implementation._canonical_json_bytes(bundle) == bundle_payload
    return {
        "binding": binding,
        "epoch_one": epoch_one,
        "epoch_two": epoch_two,
        "attempts": [first, second, third],
        "history": history,
        "bundle_path": bundle_path,
        "bundle": bundle,
        "real_fingerprints": before,
    }


def _write_synthetic_started_record(
    binding: Any,
    *,
    implementation_epoch: int = 1,
    implementation_commit: str = "2" * 40,
    owner_surface_authorization: Any | None = None,
    independent_review: Any | None = None,
) -> Any:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    history = implementation.validate_live_history(binding)
    ordinal = len(history.records) + 1
    action = _synthetic_action_authorization(
        binding,
        ordinal=ordinal,
        previous_history_root=history.history_root_sha256,
        implementation_epoch=implementation_epoch,
        implementation_commit=implementation_commit,
        owner_surface_authorization=owner_surface_authorization,
        independent_review=independent_review,
    )
    attempt_root = binding.ledger_root / "attempts" / f"{ordinal:06d}"
    attempt_root.mkdir(mode=0o700)
    evidence_root = attempt_root / "evidence"
    evidence_root.mkdir(mode=0o700)
    attempt_token = implementation._attempt_token_sha256(
        series_token_sha256=binding.series_token_sha256,
        ordinal=ordinal,
        implementation_commit=implementation_commit,
        previous_history_root_sha256=history.history_root_sha256,
    )
    started = {
        "schema_version": "p4.2a-v2-2-rehearsal-attempt-started-v1",
        "series_id": implementation.REHEARSAL_ID,
        "series_token_sha256": binding.series_token_sha256,
        "ordinal": ordinal,
        "attempt_token_sha256": attempt_token,
        "previous_history_root_sha256": history.history_root_sha256,
        "implementation_epoch": implementation_epoch,
        "implementation_commit": implementation_commit,
        "owner_action_time_authorization": action.authority_ref(binding.project_root).as_json(),
        "control_merkle_root_sha256": action.control_merkle_root_sha256,
        "command": list(action.exact_argv),
        "command_sha256": action.command_sha256,
        "environment": dict(action.exact_environment),
        "environment_sha256": action.environment_sha256,
        "interpreter_path": implementation.FIXED_PYTHON_LAUNCHER.as_posix(),
        "interpreter_sha256": implementation.FIXED_PYTHON_SHA256,
        "created_at_utc": "2026-08-11T12:00:00Z",
    }
    _write_test_file(
        attempt_root / "started.json",
        implementation._canonical_json_bytes(started),
    )
    return attempt_root


def test_preregistration_and_both_schemas_are_frozen_draft_2020_12_controls() -> None:
    preregistration = _preregistration()
    assert _sha256((PROJECT_ROOT / PREREGISTRATION_RELATIVE).read_bytes()) == (
        "8f52a9e24df11e23a900b5cb79720f3b4aae999c6ab770a9038ebe2617e8d8d5"
    )
    for relative, digest in (
        (
            BUNDLE_SCHEMA_RELATIVE,
            "19903ac94d4d7ced81c7f18e7b8880bd1dbb68fd3ededf3f0b91f89d034aa5db",
        ),
        (
            RELEASE_SCHEMA_RELATIVE,
            "098d213f510718aab0d9c6bfc950a30bb1c4841ca151631bea78c1bf0238e7ea",
        ),
    ):
        payload = (PROJECT_ROOT / relative).read_bytes()
        assert _sha256(payload) == digest
        schema = json.loads(payload)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["additionalProperties"] is False
        Draft202012Validator.check_schema(schema)
    assert preregistration["rehearsal_attempt_policy"]["selected_policy"] == (
        "DISCLOSED_REPEATABLE_SERIES_V1"
    )


def test_v2_1_source_projection_is_exact_typed_and_bound_to_f3d74_snapshot() -> None:
    preregistration = _preregistration()
    inheritance = preregistration["contract_inheritance"]
    projection = inheritance["source_projection"]
    source_path = Path(projection["source_file"])
    assert source_path == V2_1_PREREGISTRATION_RELATIVE
    source = json.loads((PROJECT_ROOT / source_path).read_bytes())
    snapshot: dict[str, Any] = {}
    assert set(projection["target_key_map"]) == {
        *projection["exact_sections"],
        projection["rehearsal_contract_source"],
    }
    for pointer in projection["exact_sections"]:
        target_key = projection["target_key_map"][pointer]
        assert target_key not in snapshot
        snapshot[target_key] = copy.deepcopy(_json_pointer(source, pointer))
    rehearsal = copy.deepcopy(
        _json_pointer(source, projection["rehearsal_contract_source"])
    )
    assert isinstance(rehearsal, dict)
    excluded = projection["rehearsal_contract_excluded_keys"]
    assert excluded == [
        "registered_runner",
        "registered_validator",
        "official_execution_count",
        "domain_separated_merkle",
    ]
    for key in excluded:
        assert key in rehearsal
        del rehearsal[key]
    snapshot[projection["target_key_map"]["/rehearsal_contract"]] = rehearsal
    registered = inheritance["strict_inheritance_snapshot"]
    assert _typed_equal(snapshot, registered)
    canonical = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    assert _sha256(canonical) == inheritance["strict_inheritance_snapshot_sha256"]
    assert _sha256(canonical) == (
        "f3d74f06c9b114ce85768f647252db76edadc42a95ab6a6f29c05d69f39bea0e"
    )

    typed_drift = copy.deepcopy(snapshot)
    interval = typed_drift["request_interval_contract"]["min_start_to_start_seconds"]
    assert type(interval) is float and interval == 1.0
    typed_drift["request_interval_contract"]["min_start_to_start_seconds"] = 1
    assert typed_drift == snapshot
    assert not _typed_equal(typed_drift, snapshot)
    drifted_canonical = json.dumps(
        typed_drift,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    assert _sha256(drifted_canonical) != inheritance["strict_inheritance_snapshot_sha256"]


def test_every_allowed_v2_2_delta_pointer_resolves_and_is_not_a_wildcard() -> None:
    preregistration = _preregistration()
    inheritance = preregistration["contract_inheritance"]
    pointers = inheritance["allowed_v2_2_delta_json_pointers"]
    assert len(pointers) == len(set(pointers))
    assert pointers
    assert all(pointer.startswith("/") and "*" not in pointer for pointer in pointers)
    for pointer in pointers:
        _json_pointer(preregistration, pointer)
    assert inheritance["every_allowed_pointer_must_resolve_in_this_document"] is True
    assert inheritance["no_implicit_wildcard_or_unlisted_delta"] is True


def test_runner_rederives_strict_inheritance_before_building_control_surface() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before = _all_real_path_fingerprints()
    snapshot = implementation.validate_strict_v2_1_inheritance(PROJECT_ROOT)
    assert _typed_equal(
        snapshot,
        _preregistration()["contract_inheritance"]["strict_inheritance_snapshot"],
    )
    assert "validate_strict_v2_1_inheritance(root)" in inspect.getsource(
        implementation.build_control_surface
    )
    assert implementation._typed_json_equal(1.0, 1) is False
    assert _all_real_path_fingerprints() == before


def test_every_carry_forward_authority_and_v2_1_exact_fifteen_surface_rederive(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before = _all_real_path_fingerprints()
    binding = _synthetic_binding(tmp_path, label="carry-forward")
    _initialize_synthetic_epoch_one(binding)
    execution_head = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
    lineage = _preregistration()["authorities"]["carry_forward_lineage"]
    assert len(lineage) == 9
    authority_rows = [row for row in lineage if row["path"] != "IMPLEMENTATION_COMMIT"]
    assert len(authority_rows) == 8
    replayed = implementation.validate_carry_forward_lineage(
        binding.project_root,
        execution_head=execution_head,
    )
    assert tuple(reference.as_json() for reference in replayed) == tuple(
        {
            "path": row["path"],
            "sha256": row["sha256"],
            "creating_commit": row["creating_commit"],
            "unique_a_history_verified": True,
        }
        for row in authority_rows
    )
    for row in authority_rows:
        assert row["unique_a_history_required"] is True
        assert row["current_bytes_must_equal_creating_commit_blob"] is True
        assert row["later_touch_forbidden"] is True
        payload = implementation.validate_unique_a_authority(
            binding.project_root,
            implementation.AuthorityReference(
                path=row["path"],
                sha256=row["sha256"],
                creating_commit=row["creating_commit"],
            ),
            execution_head=execution_head,
        )
        assert _sha256(payload) == row["sha256"]

    implementation_row = next(
        row for row in lineage if row["path"] == "IMPLEMENTATION_COMMIT"
    )
    assert implementation_row == {
        "path": "IMPLEMENTATION_COMMIT",
        "sha256": None,
        "creating_commit": V2_1_IMPLEMENTATION_COMMIT,
        "parent_commit": V2_1_IMPLEMENTATION_PARENT,
        "tree_and_exact_surface_must_be_rederived": True,
    }
    parents = implementation._git_bytes(
        binding.project_root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        V2_1_IMPLEMENTATION_COMMIT,
        "--",
    ).decode("ascii").split()
    assert parents == [V2_1_IMPLEMENTATION_COMMIT, V2_1_IMPLEMENTATION_PARENT]
    observed = implementation._git_bytes(
        binding.project_root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--name-status",
        "--no-renames",
        V2_1_IMPLEMENTATION_PARENT,
        V2_1_IMPLEMENTATION_COMMIT,
        "--",
    ).decode("utf-8").splitlines()
    assert tuple(tuple(line.split("\t", 1)) for line in observed) == V2_1_EXACT_SURFACE
    assert len(observed) == 15
    assert _all_real_path_fingerprints() == before


@pytest.mark.parametrize("mutation", ("modify", "delete"))
def test_carry_forward_unique_a_tamper_or_omission_rejects(
    tmp_path: Path,
    mutation: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before = _all_real_path_fingerprints()
    binding = _synthetic_binding(tmp_path, label=f"carry-forward-{mutation}")
    _initialize_synthetic_epoch_one(binding)
    row = _preregistration()["authorities"]["carry_forward_lineage"][0]
    target = binding.project_root / row["path"]
    if mutation == "modify":
        target.write_bytes(target.read_bytes() + b"\n")
        _fixture_git(binding.project_root, "add", "--", row["path"])
    else:
        target.unlink()
        _fixture_git(binding.project_root, "add", "--all", "--", row["path"])
    _fixture_git(binding.project_root, "commit", "--quiet", "-m", f"{mutation} authority")
    execution_head = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
    authority = implementation.AuthorityReference(
        path=row["path"],
        sha256=row["sha256"],
        creating_commit=row["creating_commit"],
    )
    with pytest.raises(implementation.RehearsalV22Error, match=r"unique|status-A|authority"):
        implementation.validate_unique_a_authority(
            binding.project_root,
            authority,
            execution_head=execution_head,
        )
    assert _all_real_path_fingerprints() == before


def test_review_is_a_unique_sibling_authority_not_an_implementation_ancestor() -> None:
    payload = _git("show", f"{INDEPENDENT_REVIEW_COMMIT}:{INDEPENDENT_REVIEW_RELATIVE}")
    assert _sha256(payload) == INDEPENDENT_REVIEW_SHA256
    review = json.loads(payload)
    assert review["verdict"] == "APPROVE_V2_2_PREREGISTRATION_AND_AUTHORIZE_IMPLEMENTATION"
    assert review["reviewed_commit"] == PREREGISTRATION_COMMIT
    parents = _git("rev-list", "--parents", "-n", "1", INDEPENDENT_REVIEW_COMMIT).decode().split()
    assert parents == [INDEPENDENT_REVIEW_COMMIT, PREREGISTRATION_COMMIT]
    name_status = (
        _git(
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "-r",
            INDEPENDENT_REVIEW_COMMIT,
        )
        .decode()
        .splitlines()
    )
    assert name_status == [f"A\t{INDEPENDENT_REVIEW_RELATIVE.as_posix()}"]
    assert not (PROJECT_ROOT / INDEPENDENT_REVIEW_RELATIVE).exists()


def test_registered_implementation_surface_is_exactly_five_added_paths() -> None:
    preregistration = _preregistration()["prospective_implementation_contract"]
    assert preregistration["exact_surface_count"] == 5
    assert preregistration["registered_existing_files_modified"] == []
    declared = preregistration["registered_new_files"]
    assert tuple(Path(record["path"]) for record in declared) == REGISTERED_SURFACE
    assert {record["expected_status"] for record in declared} == {"A"}
    assert all((PROJECT_ROOT / relative).is_file() for relative in REGISTERED_SURFACE)
    for relative in REGISTERED_SURFACE:
        assert b"\x00" not in (PROJECT_ROOT / relative).read_bytes()


def test_preregistration_commit_shape_and_current_implementation_topology() -> None:
    preregistration_parents = (
        _git("rev-list", "--parents", "-n", "1", PREREGISTRATION_COMMIT).decode().split()
    )
    assert preregistration_parents == [PREREGISTRATION_COMMIT, PREREGISTRATION_PARENT]
    head = _git("rev-parse", "HEAD").decode().strip()
    git_order = tuple(sorted(REGISTERED_SURFACE, key=lambda path: os.fsencode(path.as_posix())))
    if head == PREREGISTRATION_COMMIT:
        status = (
            _git(
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                *(path.as_posix() for path in REGISTERED_SURFACE),
            )
            .decode()
            .splitlines()
        )
        assert [line[3:] for line in status] == [path.as_posix() for path in git_order]
        assert all(line[:2] in {"??", "A "} for line in status)
    elif head == INITIAL_IMPLEMENTATION_COMMIT:
        parents = _git("rev-list", "--parents", "-n", "1", head).decode().split()
        assert parents == [head, PREREGISTRATION_COMMIT]
        surface = (
            _git("diff-tree", "--no-commit-id", "--name-status", "-r", head).decode().splitlines()
        )
        assert surface == [f"A\t{path.as_posix()}" for path in git_order]
    else:
        parents = _git("rev-list", "--parents", "-n", "1", head).decode().split()
        assert parents == [head, INITIAL_IMPLEMENTATION_COMMIT]
        remediation_surface = (
            _git("diff-tree", "--no-commit-id", "--name-status", "-r", head).decode().splitlines()
        )
        assert remediation_surface == [
            f"M\t{path.as_posix()}"
            for path in sorted(
                (IMPLEMENTATION_RELATIVE, RUNNER_TEST_RELATIVE),
                key=lambda path: os.fsencode(path.as_posix()),
            )
        ]
        cumulative_surface = (
            _git(
                "diff",
                "--name-status",
                PREREGISTRATION_COMMIT,
                head,
                "--",
                *(path.as_posix() for path in REGISTERED_SURFACE),
            )
            .decode()
            .splitlines()
        )
        assert cumulative_surface == [f"A\t{path.as_posix()}" for path in git_order]


@pytest.mark.parametrize("include_initial_sibling", (True, False))
def test_initial_b21_sibling_authority_must_also_be_on_execution_lineage(
    tmp_path: Path,
    include_initial_sibling: bool,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before = _all_real_path_fingerprints()
    binding = _synthetic_binding(
        tmp_path,
        label=f"initial-sibling-{include_initial_sibling}",
    )
    implementation_commit, owner_surface, independent_review = _initialize_synthetic_epoch_one(
        binding,
        include_initial_sibling=include_initial_sibling,
    )
    execution_head = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
    ancestry = set(
        _fixture_git(binding.project_root, "rev-list", execution_head).decode().splitlines()
    )
    assert implementation_commit in ancestry
    assert (INDEPENDENT_REVIEW_COMMIT in ancestry) is include_initial_sibling
    if include_initial_sibling:
        result = implementation.validate_implementation_epoch(
            binding.project_root,
            epoch=1,
            implementation_commit=implementation_commit,
            owner_surface_authorization=owner_surface,
            independent_review=independent_review,
            control_merkle_root_sha256="6" * 64,
            execution_head=execution_head,
        )
        assert result.implementation_commit == implementation_commit
    else:
        with pytest.raises(
            implementation.RehearsalV22Error,
            match=r"sibling|lineage|ancestor|authority",
        ):
            implementation.validate_implementation_epoch(
                binding.project_root,
                epoch=1,
                implementation_commit=implementation_commit,
                owner_surface_authorization=owner_surface,
                independent_review=independent_review,
                control_merkle_root_sha256="6" * 64,
                execution_head=execution_head,
            )
    assert _all_real_path_fingerprints() == before


def test_genuine_epoch_two_reads_owner_base_and_exact_am_surface_then_post_review(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before = _all_real_path_fingerprints()
    binding = _synthetic_binding(tmp_path, label="epoch-two-positive")
    implementation_commit, owner_surface, independent_review, execution_head = (
        _initialize_synthetic_epoch_two(binding)
    )
    result = implementation.validate_implementation_epoch(
        binding.project_root,
        epoch=2,
        implementation_commit=implementation_commit,
        owner_surface_authorization=owner_surface,
        independent_review=independent_review,
        control_merkle_root_sha256="7" * 64,
        execution_head=execution_head,
    )
    surface_document = json.loads((binding.project_root / owner_surface.path).read_bytes())
    assert surface_document["implementation_epoch"] == 2
    assert surface_document["exact_surface"] == [
        {"path": IMPLEMENTATION_RELATIVE.as_posix(), "status": "M"}
    ]
    assert _fixture_git(
        binding.project_root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        owner_surface.creating_commit,
    ).decode().split() == [
        owner_surface.creating_commit,
        surface_document["base_commit"],
    ]
    assert _fixture_git(
        binding.project_root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        implementation_commit,
    ).decode().split() == [implementation_commit, owner_surface.creating_commit]
    assert result == implementation.ImplementationEpochValidation(
        epoch=2,
        implementation_commit=implementation_commit,
        owner_surface_authorization=owner_surface,
        independent_implementation_review=independent_review,
        control_merkle_root_sha256="7" * 64,
    )
    assert _all_real_path_fingerprints() == before


@pytest.mark.parametrize(
    "mutation",
    (
        "unreviewed",
        "disapprove-review",
        "approve-not-review",
        "reject-review",
        "extra-path",
        "byte-mismatch",
    ),
)
def test_epoch_two_unreviewed_extra_path_or_current_byte_drift_rejects(
    tmp_path: Path,
    mutation: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before = _all_real_path_fingerprints()
    binding = _synthetic_binding(tmp_path, label=f"epoch-two-{mutation}")
    implementation_commit, owner_surface, independent_review, execution_head = (
        _initialize_synthetic_epoch_two(
            binding,
            add_extra_path=mutation == "extra-path",
            review_verdict={
                "disapprove-review": "DISAPPROVE_IMPLEMENTATION",
                "approve-not-review": "APPROVE_NOT_IMPLEMENTATION",
                "reject-review": "REJECT_IMPLEMENTATION",
            }.get(mutation, "APPROVE_V2_2_IMPLEMENTATION"),
        )
    )
    if mutation == "unreviewed":
        independent_review = owner_surface
    elif mutation == "byte-mismatch":
        target = binding.project_root / IMPLEMENTATION_RELATIVE
        target.write_bytes(target.read_bytes() + b"# unreviewed current drift\n")
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"review|surface|worktree|bytes|differs",
    ):
        implementation.validate_implementation_epoch(
            binding.project_root,
            epoch=2,
            implementation_commit=implementation_commit,
            owner_surface_authorization=owner_surface,
            independent_review=independent_review,
            control_merkle_root_sha256="8" * 64,
            execution_head=execution_head,
        )
    assert _all_real_path_fingerprints() == before


def test_required_test_contract_has_no_skip_or_alternate_gate_escape() -> None:
    preregistration = _preregistration()
    execution = preregistration["test_execution_contract"]
    assert execution == {
        "disposable_full_shape_root": (
            "byte-exact synthetic canonical repository in a fresh noncanonical temporary root"
        ),
        "bootstrap": (
            "OS first exec with exact env-i, fixed interpreter, -S -P -B, shim as __main__, "
            "package validator nested"
        ),
        "path_or_validation_override": False,
        "synthetic_destination_and_ledger_are_canonical_for_synthetic_root": True,
        "real_repository_destination_and_ledger_must_remain_absent_or_unchanged": True,
        "failure_persistence_tests_consume_only_synthetic_series_ledgers": True,
        "no_gate_monkeypatch_or_alternate_entry": True,
        "tests_that_support_PASS_must_be_named_in_evidence_and_archived_by_bytes": True,
    }
    for relative in (RUNNER_TEST_RELATIVE, VALIDATOR_TEST_RELATIVE):
        payload = (PROJECT_ROOT / relative).read_bytes()
        tree = ast.parse(payload)
        forbidden_pytest_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "pytest"
            and node.func.attr in {"skip", "xfail"}
        ]
        forbidden_marks = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr in {"skip", "skipif", "xfail"}
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "pytest"
        ]
        assert forbidden_pytest_calls == []
        assert forbidden_marks == []
        assert not any(
            isinstance(node, ast.Name) and node.id == "monkeypatch" for node in ast.walk(tree)
        )


def test_thin_shim_has_no_authority_state_and_only_calls_package_cli_main() -> None:
    source = (PROJECT_ROOT / SHIM_RELATIVE).read_bytes()
    tree = ast.parse(source)
    imported_modules = _imported_modules(tree)
    assert IMPLEMENTATION_MODULE in imported_modules
    assert {name for name in imported_modules if name != IMPLEMENTATION_MODULE} <= {"os", "sys"}
    assert VALIDATOR_MODULE.encode() not in source
    assert b"ContextVar" not in source
    assert b"_AUDIT_POLICY" not in source
    assert b"_TEMP_AUTHORITY" not in source
    assert b"_build_authority_state" not in source
    assert b"cli_main" in source
    function_names = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not ({"run_rehearsal", "validate_bundle", "_execute_temp_pipeline"} & function_names)


def test_implementation_is_the_only_authority_owner_and_validator_imports_it() -> None:
    implementation_tree = ast.parse((PROJECT_ROOT / IMPLEMENTATION_RELATIVE).read_bytes())
    validator_tree = ast.parse((PROJECT_ROOT / VALIDATOR_RELATIVE).read_bytes())
    shim_tree = ast.parse((PROJECT_ROOT / SHIM_RELATIVE).read_bytes())
    all_trees = {
        IMPLEMENTATION_RELATIVE: implementation_tree,
        VALIDATOR_RELATIVE: validator_tree,
        SHIM_RELATIVE: shim_tree,
    }
    for authority_name in ("_AUDIT_POLICY", "_TEMP_AUTHORITY"):
        creators = {
            relative
            for relative, tree in all_trees.items()
            if any(
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == authority_name
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                )
                and isinstance(node.value, ast.Call)
                for node in ast.walk(tree)
            )
        }
        assert creators == {IMPLEMENTATION_RELATIVE}
        validator_aliases = [
            node.value
            for node in ast.walk(validator_tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == authority_name
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            )
        ]
        assert len(validator_aliases) == 1
        alias = validator_aliases[0]
        assert isinstance(alias, ast.Attribute)
        assert isinstance(alias.value, ast.Name) and alias.value.id == "implementation"
        assert alias.attr == authority_name
    validator_imports = _imported_modules(validator_tree)
    assert IMPLEMENTATION_MODULE in validator_imports
    assert "scripts.rehearse_p4_2a_v2_2_heldout_full_path" not in validator_imports


def test_implementation_is_the_sole_process_audit_hook_owner_and_validator_imports_it_first(
) -> None:
    trees = {
        relative: ast.parse((PROJECT_ROOT / relative).read_bytes())
        for relative in REGISTERED_SURFACE
    }

    def audit_hook_calls(tree: ast.AST) -> list[ast.Call]:
        sys_aliases = {"sys"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sys":
                        sys_aliases.add(alias.asname or alias.name)
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "addaudithook"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in sys_aliases
        ]

    owners = {
        relative: len(audit_hook_calls(tree))
        for relative, tree in trees.items()
        if audit_hook_calls(tree)
    }
    assert owners == {IMPLEMENTATION_RELATIVE: 1}

    validator_tree = trees[VALIDATOR_RELATIVE]
    implementation_import_indexes = [
        index
        for index, node in enumerate(validator_tree.body)
        if isinstance(node, ast.Import)
        and any(
            alias.name == IMPLEMENTATION_MODULE and alias.asname == "implementation"
            for alias in node.names
        )
    ]
    assert len(implementation_import_indexes) == 1
    implementation_import_index = implementation_import_indexes[0]
    for index, node in enumerate(validator_tree.body):
        if index >= implementation_import_index or not isinstance(
            node, (ast.Import, ast.ImportFrom)
        ):
            continue
        if isinstance(node, ast.ImportFrom):
            assert node.module == "__future__"
            continue
        assert {
            alias.name for alias in node.names
        } <= {"os", "sys"}, "validator imported before the sole audit hook owner"

    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = importlib.import_module(VALIDATOR_MODULE)
    assert validator._implementation_module is implementation
    assert sum(
        1
        for relative in REGISTERED_SURFACE
        for _call in audit_hook_calls(trees[relative])
    ) == 1


def test_validator_import_guard_rejects_forged_or_repeated_finalization() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    forged = ModuleType("__main__")
    forged.__file__ = (PROJECT_ROOT / VALIDATOR_RELATIVE).as_posix()
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"forged|repeated|split",
    ):
        implementation._finish_validator_import_guard(forged)
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"repeated|split",
    ):
        implementation._finish_core_import_guard()


@pytest.mark.parametrize(
    ("relative", "mutation"),
    (
        (SHIM_RELATIVE, "add"),
        (VALIDATOR_RELATIVE, "add"),
        (RUNNER_TEST_RELATIVE, "add"),
        (IMPLEMENTATION_RELATIVE, "remove"),
        (IMPLEMENTATION_RELATIVE, "duplicate"),
    ),
    ids=("shim-add", "validator-add", "test-add", "core-remove", "core-duplicate"),
)
def test_independent_validator_rejects_any_second_or_missing_process_audit_hook(
    tmp_path: Path,
    relative: Path,
    mutation: str,
) -> None:
    validator = importlib.import_module(VALIDATOR_MODULE)
    binding = _synthetic_binding(tmp_path, label=f"audit-owner-{relative.stem}-{mutation}")
    _initialize_synthetic_ledger(binding)
    implementation_commit, _authority, _review = _initialize_synthetic_epoch_one(binding)
    payload = (binding.project_root / relative).read_bytes()
    call = b"sys.addaudithook(_process_audit_hook)"
    if mutation == "remove":
        assert payload.count(call) == 1
        payload = payload.replace(call, b"pass  # process audit hook removed", 1)
    elif mutation == "duplicate":
        assert payload.count(call) == 1
        payload = payload.replace(call, call + b"\n" + call, 1)
    else:
        payload += b"\nimport sys\nsys.addaudithook(lambda *_args: None)\n"
    drifted_commit = _fixture_commit_file(binding.project_root, relative, payload)
    assert drifted_commit != implementation_commit
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"sole process audit-hook installer",
    ):
        validator._validate_module_identity(binding.project_root, drifted_commit)


def test_runtime_module_and_both_contextvar_identities_are_exactly_shared() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = importlib.import_module(VALIDATOR_MODULE)
    assert sys.modules[IMPLEMENTATION_MODULE] is implementation
    assert validator._implementation_module is implementation
    assert "scripts.rehearse_p4_2a_v2_1_heldout_full_path" not in sys.modules
    module_contextvars = {
        name for name, value in vars(implementation).items() if isinstance(value, ContextVar)
    }
    assert module_contextvars == {"_AUDIT_POLICY", "_TEMP_AUTHORITY"}
    audit_policy = implementation._AUDIT_POLICY
    temp_authority = implementation._TEMP_AUTHORITY
    assert isinstance(audit_policy, ContextVar)
    assert isinstance(temp_authority, ContextVar)
    assert id(validator._implementation_module._AUDIT_POLICY) == id(audit_policy)
    assert id(validator._implementation_module._TEMP_AUTHORITY) == id(temp_authority)


def test_loaded_v2_base_runner_is_pure_and_v2_1_authority_runner_is_absent() -> None:
    assert "scripts.rehearse_p4_2a_v2_1_heldout_full_path" not in sys.modules
    base_runner = importlib.import_module("scripts.rehearse_p4_2a_v2_heldout_full_path")
    source = Path(base_runner.__file__).read_bytes()
    tree = ast.parse(source)
    assert b"ContextVar" not in source
    assert b"_AUDIT_POLICY" not in source
    assert b"_TEMP_AUTHORITY" not in source
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "sys"
        and node.func.attr == "addaudithook"
        for node in ast.walk(tree)
    )


def test_pipeline_copies_exactly_five_byte_frozen_v2_1_mint_prerequisites(
    tmp_path: Path,
    v2_1_mint_prerequisite_source: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    source_root = tmp_path / "source"
    _clone_v2_1_mint_prerequisite_source(
        v2_1_mint_prerequisite_source,
        source_root,
    )
    assert implementation.V2_1_MINT_PREREQUISITE_CONTROLS == (
        EXPECTED_V2_1_MINT_PREREQUISITE_CONTROLS
    )
    assert len(implementation.V2_1_MINT_PREREQUISITE_CONTROLS) == 5
    assert len(
        {
            relative.as_posix().casefold()
            for relative, _sha256_value in implementation.V2_1_MINT_PREREQUISITE_CONTROLS
        }
    ) == 5
    assert not {
        relative for relative, _sha256_value in implementation.V2_1_MINT_PREREQUISITE_CONTROLS
    } & set(implementation.prepare._registered_successor_implementation_paths(source_root))

    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    source_before = _v2_1_mint_prerequisite_fingerprint(source_root)
    implementation._copy_v2_1_mint_prerequisite_controls(source_root, workspace)
    assert _v2_1_mint_prerequisite_fingerprint(source_root) == source_before
    copied = {
        candidate.relative_to(workspace): _sha256(candidate.read_bytes())
        for candidate in workspace.rglob("*")
        if candidate.is_file() and not candidate.is_symlink()
    }
    assert copied == dict(EXPECTED_V2_1_MINT_PREREQUISITE_CONTROLS)


@pytest.mark.parametrize(
    "fault",
    ("missing", "drifted"),
)
@pytest.mark.parametrize(
    ("relative", "expected_sha256"),
    EXPECTED_V2_1_MINT_PREREQUISITE_CONTROLS,
    ids=lambda value: value.name if isinstance(value, Path) else value[:12],
)
def test_pipeline_prerequisite_missing_or_drifted_source_rejects_before_any_copy(
    tmp_path: Path,
    v2_1_mint_prerequisite_source: Path,
    fault: str,
    relative: Path,
    expected_sha256: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    source_root = tmp_path / "source"
    _clone_v2_1_mint_prerequisite_source(
        v2_1_mint_prerequisite_source,
        source_root,
    )
    workspace = tmp_path / "workspace"
    source = source_root / relative
    if fault == "missing":
        source.unlink()
    else:
        source.write_bytes(source.read_bytes() + b"\nDRIFT")
    workspace.mkdir(mode=0o700)
    assert expected_sha256 == dict(EXPECTED_V2_1_MINT_PREREQUISITE_CONTROLS)[relative]
    source_before = _v2_1_mint_prerequisite_fingerprint(source_root)
    workspace_before = _tree_fingerprint(workspace)
    with pytest.raises(
        (implementation.RehearsalV22Error, implementation.prepare.HeldoutPreparationError)
    ) as caught:
        implementation._copy_v2_1_mint_prerequisite_controls(source_root, workspace)
    assert "drifted" in str(caught.value) or "unavailable" in str(caught.value)
    assert _v2_1_mint_prerequisite_fingerprint(source_root) == source_before
    assert _tree_fingerprint(workspace) == workspace_before


def test_capability_nonces_and_mutable_registries_are_not_module_attributes() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    forbidden = {
        name
        for name in vars(implementation)
        if name.startswith(("_BOOTSTRAP_", "_CAPABILITY_", "_DELEGATION_"))
        and name.endswith(("NONCE", "REGISTRY"))
    }
    assert forbidden == set()
    for function_name in (
        "_validate_bootstrap_evidence",
        "_validate_disposable_capability",
        "_validate_validator_delegation",
    ):
        function = getattr(implementation, function_name)
        closure = inspect.getclosurevars(function)
        hidden_names = {
            name
            for name in closure.nonlocals
            if "nonce" in name.lower() or "registry" in name.lower()
        }
        assert hidden_names
        assert all(
            value is not hidden_value
            for value in vars(implementation).values()
            for hidden_name, hidden_value in closure.nonlocals.items()
            if hidden_name in hidden_names
        )


def test_stolen_closure_nonces_do_not_authorize_direct_dataclass_tokens(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label="forged-capability")

    bootstrap_closure = inspect.getclosurevars(
        implementation._validate_bootstrap_evidence
    ).nonlocals
    bootstrap_nonce = bootstrap_closure["bootstrap_nonce"]
    bootstrap_registry = bootstrap_closure["bootstrap_registry"]
    assert bootstrap_registry == ()
    forged_bootstrap = implementation._BootstrapEvidence(
        _nonce=bootstrap_nonce,
        project_root=binding.project_root,
        shim_path=binding.shim_path,
        argv=tuple(sys.argv),
        orig_argv=tuple(sys.orig_argv),
        environment=dict(os.environ),
    )
    stolen_bootstrap_snapshot = (*bootstrap_registry, forged_bootstrap)
    assert stolen_bootstrap_snapshot == (forged_bootstrap,)
    assert (
        inspect.getclosurevars(implementation._validate_bootstrap_evidence).nonlocals[
            "bootstrap_registry"
        ]
        == ()
    )
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"bootstrap|forged|stolen|stale|process",
    ):
        implementation._validate_bootstrap_evidence(forged_bootstrap)

    capability_closure = inspect.getclosurevars(
        implementation._validate_disposable_capability
    ).nonlocals
    capability_nonce = capability_closure["capability_nonce"]
    capability_registry = capability_closure["capability_registry"]
    assert capability_registry == ()
    forged_capability = implementation._DisposableCapability(
        _nonce=capability_nonce,
        binding=binding,
        bootstrap=forged_bootstrap,
        action_authorization=_synthetic_action_authorization(
            binding,
            ordinal=1,
            previous_history_root=implementation._history_empty_root_sha256(),
        ),
        real_path_fingerprints=implementation._real_path_fingerprints(),
        boundary_ids=implementation._fake_boundary_ids(),
    )
    stolen_capability_snapshot = (*capability_registry, forged_capability)
    assert stolen_capability_snapshot == (forged_capability,)
    assert (
        inspect.getclosurevars(implementation._validate_disposable_capability).nonlocals[
            "capability_registry"
        ]
        == ()
    )
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"authority|bootstrap|forged|cross-root|binding|process",
    ):
        implementation._validate_disposable_capability(
            forged_capability,
            project_root=binding.project_root,
        )

    delegation_closure = inspect.getclosurevars(
        implementation._validate_validator_delegation
    ).nonlocals
    delegation_registry = delegation_closure["delegation_registry"]
    delegation_nonce = delegation_closure["delegation_nonce"]
    validator = importlib.import_module(VALIDATOR_MODULE)
    forged_policy = object()
    forged_delegation = implementation.ValidatorDelegation(
        _nonce=delegation_nonce,
        binding=binding,
        capability_id=id(forged_capability),
        validator_module_id=id(validator),
        audit_policy_id=id(forged_policy),
        temp_authority=tmp_path,
        creator_module_id=id(implementation),
        lifetime_id=1,
    )
    assert delegation_registry == ()
    forged_record = implementation._DelegationRecord(
        token=forged_delegation,
        capability=forged_capability,
        validator_module=validator,
        policy=forged_policy,
    )
    stolen_delegation_snapshot = (*delegation_registry, forged_record)
    assert stolen_delegation_snapshot == (forged_record,)
    assert (
        inspect.getclosurevars(implementation._validate_validator_delegation).nonlocals[
            "delegation_registry"
        ]
        == ()
    )
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"authority|bootstrap|capability|forged|stolen|scope|expired|process",
    ):
        implementation._validate_validator_delegation(
            forged_delegation,
            execution_context=forged_capability,
            validator_module=validator,
            project_root=binding.project_root,
        )


def test_consumed_v2_1_claim_is_the_exact_empty_0700_directory() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    assert implementation.V2_1_EMPTY_CLAIM == V2_1_CONSUMED_CLAIM
    assert V2_1_CONSUMED_CLAIM.name.endswith(V2_1_CONSUMED_CLAIM_TOKEN)
    metadata = V2_1_CONSUMED_CLAIM.lstat()
    assert stat.S_ISDIR(metadata.st_mode)
    assert not V2_1_CONSUMED_CLAIM.is_symlink()
    assert stat.S_IMODE(metadata.st_mode) == 0o700
    assert list(V2_1_CONSUMED_CLAIM.iterdir()) == []
    assert _tree_fingerprint(V2_1_CONSUMED_CLAIM) == ((".", "directory", 0o700, ""),)


def test_implementation_direct_main_rejects_without_real_path_effect() -> None:
    before = _all_real_path_fingerprints()
    completed = _direct_execution(IMPLEMENTATION_RELATIVE)
    assert completed.returncode != 0
    assert "__main__" in completed.stderr or "direct" in completed.stderr.lower()
    assert _all_real_path_fingerprints() == before


def test_second_authority_module_object_is_rejected_in_an_isolated_process() -> None:
    before = _all_real_path_fingerprints()
    real_root = Path(str(_preregistration()["exact_os_bootstrap_contract"]["repository_root"]))
    site = real_root / ".venv/lib/python3.12/site-packages"
    code = (
        "import importlib,importlib.util,sys;"
        f"sys.path[:]=[{site.as_posix()!r},{PROJECT_ROOT.as_posix()!r},"
        f"{(PROJECT_ROOT / 'src').as_posix()!r}];"
        f"importlib.import_module({IMPLEMENTATION_MODULE!r});"
        f"s=importlib.util.spec_from_file_location('shadow_v22_authority',"
        f"{(PROJECT_ROOT / IMPLEMENTATION_RELATIVE).as_posix()!r});"
        "m=importlib.util.module_from_spec(s);"
        "s.loader.exec_module(m)"
    )
    completed = subprocess.run(
        [_fixed_interpreter().as_posix(), "-S", "-P", "-B", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=_locked_environment(),
    )
    assert completed.returncode != 0
    assert any(word in completed.stderr.lower() for word in ("module", "identity", "authority"))
    assert _all_real_path_fingerprints() == before


def test_exact_os_missing_action_receipt_rejects_before_ledger_or_destination(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before = _all_real_path_fingerprints()
    binding = _synthetic_binding(tmp_path.resolve(), label="missing-action")
    _initialize_synthetic_epoch_one(binding)
    assert not binding.action_authorization_path.exists()
    process = _spawn_exact_attempt(binding, 1)
    returncode, stdout, stderr = _communicate_exact_attempt(process)
    assert returncode == 1
    assert stdout == ""
    error = implementation.strict_json_loads(
        stderr,
        source="missing action exact-OS error",
    )
    assert error["status"] == "FAILED_NO_AUTOMATIC_RETRY"
    assert error["exception_type"] == "RehearsalV22Error"
    assert _tree_fingerprint(binding.ledger_root) is None
    assert _tree_fingerprint(binding.destination) is None
    assert _all_real_path_fingerprints() == before


def test_ordinary_wrapper_sitecustomize_environment_and_orig_argv_drift_all_reject(
    tmp_path: Path,
) -> None:
    before = _all_real_path_fingerprints()
    binding = _synthetic_binding(tmp_path.resolve(), label="bootstrap-negative-matrix")
    _initialize_synthetic_epoch_one(binding)
    exact_command = _exact_attempt_command(binding, 1)
    wrapper = (
        "import runpy;"
        f"runpy.run_path({binding.shim_path.as_posix()!r},run_name='__main__')"
    )
    site_root = tmp_path / "site-injection"
    site_root.mkdir(mode=0o700)
    (site_root / "sitecustomize.py").write_bytes(
        b"import sys\nprint('V22_SITECUSTOMIZE_LOADED', file=sys.stderr)\n"
    )
    site_environment = dict(_locked_environment())
    site_environment["PYTHONPATH"] = site_root.as_posix()
    drifted_environment = dict(_locked_environment())
    drifted_environment["TZ"] = "America/New_York"
    cases = (
        (
            "ordinary-python",
            [
                _fixed_interpreter().as_posix(),
                binding.shim_path.as_posix(),
                *exact_command[5:],
            ],
            _locked_environment(),
        ),
        (
            "runpy-orig-argv-wrapper",
            [_fixed_interpreter().as_posix(), "-S", "-P", "-B", "-c", wrapper],
            _locked_environment(),
        ),
        ("environment-drift", exact_command, drifted_environment),
        (
            "sitecustomize",
            [
                _fixed_interpreter().as_posix(),
                binding.shim_path.as_posix(),
                *exact_command[5:],
            ],
            site_environment,
        ),
    )
    results: dict[str, subprocess.CompletedProcess[str]] = {}
    for name, command, environment in cases:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=120,
        )
        results[name] = completed
        assert completed.returncode != 0, name
        assert _tree_fingerprint(binding.ledger_root) is None, name
        assert _tree_fingerprint(binding.destination) is None, name
    assert "V22_SITECUSTOMIZE_LOADED" in results["sitecustomize"].stderr
    assert _all_real_path_fingerprints() == before


@pytest.mark.parametrize("injected_kind", ("repository", "third-party"))
def test_validator_import_guard_rejects_import_time_writes_before_effect(
    tmp_path: Path,
    injected_kind: str,
) -> None:
    before = _all_real_path_fingerprints()
    sentinel = tmp_path / f"{injected_kind}-import-write.txt"
    injected_root = tmp_path / "injected"
    injected_root.mkdir(mode=0o700)
    registered_root = Path(
        str(_preregistration()["exact_os_bootstrap_contract"]["repository_root"])
    )
    site = registered_root / ".venv/lib/python3.12/site-packages"
    if injected_kind == "repository":
        scripts_root = injected_root / "scripts"
        scripts_root.mkdir(mode=0o700)
        (scripts_root / "__init__.py").write_bytes(b"")
        (scripts_root / IMPLEMENTATION_RELATIVE.name).write_bytes(
            (PROJECT_ROOT / IMPLEMENTATION_RELATIVE).read_bytes()
        )
        (scripts_root / "build_p4_2a_gold_sample.py").write_text(
            f"open({sentinel.as_posix()!r}, 'wb').write(b'forbidden')\n"
        )
        imported_module = IMPLEMENTATION_MODULE
        repository_root = injected_root
        prefix = [site.as_posix(), injected_root.as_posix()]
    else:
        jsonschema_root = injected_root / "jsonschema"
        jsonschema_root.mkdir(mode=0o700)
        (jsonschema_root / "__init__.py").write_text(
            f"open({sentinel.as_posix()!r}, 'wb').write(b'forbidden')\n"
        )
        imported_module = VALIDATOR_MODULE
        repository_root = PROJECT_ROOT
        prefix = [injected_root.as_posix(), site.as_posix(), PROJECT_ROOT.as_posix()]
    code = (
        "import importlib,os,sys;"
        "stdlib=os.path.join(sys.base_prefix,'lib',"
        "f'python{sys.version_info.major}.{sys.version_info.minor}');"
        f"root={repository_root.as_posix()!r};"
        "sys.modules['__main__'].__file__="
        f"os.path.join(root,{'scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py'!r});"
        f"sys.path[:]=[stdlib,os.path.join(stdlib,'lib-dynload'),*{prefix!r},"
        "os.path.join(root,'src')];"
        f"importlib.import_module({imported_module!r})"
    )
    completed = subprocess.run(
        [_fixed_interpreter().as_posix(), "-S", "-P", "-B", "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=_locked_environment(),
    )
    assert completed.returncode != 0
    assert "bootstrap import attempted a write" in completed.stderr
    assert not os.path.lexists(sentinel)
    assert _all_real_path_fingerprints() == before


def test_hash_domains_and_empty_roots_are_exact_and_noninterchangeable() -> None:
    empty_history = hashlib.sha256(b"p4.2a-rehearsal-v2.2-history-empty-v1\0").hexdigest()
    empty_evidence = hashlib.sha256(b"p4.2a-rehearsal-v2.2-evidence-empty-v1\0").hexdigest()
    assert empty_history != empty_evidence
    preregistration = _preregistration()
    ledger = preregistration["series_ledger_contract"]
    merkle = json.loads((PROJECT_ROOT / BUNDLE_SCHEMA_RELATIVE).read_bytes())["$defs"]["merkle"][
        "properties"
    ]
    assert (
        ledger["history_empty_root_formula"]
        == merkle["attempt_history_empty_root_formula"]["const"]
    )
    assert (
        ledger["evidence_empty_root_formula"]
        == merkle["attempt_evidence_empty_root_formula"]["const"]
    )
    assert ledger["attempt_token_formula"] == merkle["attempt_token_formula"]["const"]
    assert ledger["attempt_record_formula"] == merkle["attempt_record_formula"]["const"]
    assert ledger["history_step_formula"] == merkle["attempt_history_step_formula"]["const"]


def test_runner_and_validator_independently_agree_on_series_hash_formulas() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = importlib.import_module(VALIDATOR_MODULE)
    series_token = "1" * 64
    implementation_commit = "2" * 40
    previous = hashlib.sha256(b"p4.2a-rehearsal-v2.2-history-empty-v1\0").hexdigest()
    token = hashlib.sha256(
        b"p4.2a-rehearsal-v2.2-attempt-v1\0"
        + bytes.fromhex(series_token)
        + (1).to_bytes(8, "big")
        + bytes.fromhex(implementation_commit)
        + bytes.fromhex(previous)
    ).hexdigest()
    assert implementation._history_empty_root_sha256() == previous
    assert (
        implementation._attempt_token_sha256(
            series_token_sha256=series_token,
            ordinal=1,
            implementation_commit=implementation_commit,
            previous_history_root_sha256=previous,
        )
        == token
    )
    assert (
        validator._attempt_token(
            series_token=series_token,
            ordinal=1,
            implementation_commit=implementation_commit,
            previous_history_root=previous,
        )
        == token
    )
    record = implementation._attempt_record_root_sha256(
        ordinal=1,
        attempt_token_sha256=token,
        started_sha256="3" * 64,
        candidate_sha256=None,
        terminal_sha256=None,
        evidence_tree_root_sha256=implementation._evidence_empty_root_sha256(),
    )
    assert record == validator._attempt_record_root(
        ordinal=1,
        attempt_token=token,
        started_sha256="3" * 64,
        candidate_sha256=None,
        terminal_sha256=None,
        evidence_tree_root=validator._evidence_root({}),
    )
    assert implementation._history_step_sha256(previous, record) == validator._history_step(
        previous, record
    )


def test_control_merkle_archives_both_registered_pass_test_files_by_exact_bytes(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = importlib.import_module(VALIDATOR_MODULE)
    before = _all_real_path_fingerprints()
    binding = _synthetic_binding(tmp_path, label="control-pass-tests")
    implementation_commit, _owner, _review = _initialize_synthetic_epoch_one(binding)
    control = implementation.build_control_surface(
        binding.project_root,
        implementation_commit,
        require_current=False,
    )
    manifest = json.loads(control.manifest_payload)
    records = {record["repository_path"]: record for record in manifest["files"]}
    merkle_payloads = dict(control.payloads)
    merkle_payloads["archive/control-surface/manifest.json"] = control.manifest_payload
    assert validator._generic_merkle_root(merkle_payloads) == control.merkle_root_sha256
    preregistration = _preregistration()
    frozen_controls: dict[str, tuple[str, str]] = {
        PREREGISTRATION_RELATIVE.as_posix(): (
            _sha256((PROJECT_ROOT / PREREGISTRATION_RELATIVE).read_bytes()),
            PREREGISTRATION_COMMIT,
        ),
        BUNDLE_SCHEMA_RELATIVE.as_posix(): (
            _sha256((PROJECT_ROOT / BUNDLE_SCHEMA_RELATIVE).read_bytes()),
            PREREGISTRATION_COMMIT,
        ),
        RELEASE_SCHEMA_RELATIVE.as_posix(): (
            _sha256((PROJECT_ROOT / RELEASE_SCHEMA_RELATIVE).read_bytes()),
            PREREGISTRATION_COMMIT,
        ),
        INDEPENDENT_REVIEW_RELATIVE.as_posix(): (
            INDEPENDENT_REVIEW_SHA256,
            INDEPENDENT_REVIEW_COMMIT,
        ),
    }
    for key in ("action_scope_authorization", "remediation_request", "consumed_v2_1_incident"):
        authority = preregistration["authorities"][key]
        frozen_controls[authority["path"]] = (
            authority["sha256"],
            authority["creating_commit"],
        )
    for authority in preregistration["authorities"]["carry_forward_lineage"]:
        if authority["path"] != "IMPLEMENTATION_COMMIT":
            frozen_controls[authority["path"]] = (
                authority["sha256"],
                authority["creating_commit"],
            )
    for relative, (digest, creating_commit) in frozen_controls.items():
        archive = f"archive/control-surface/root/repo/{relative}"
        payload = implementation._git_blob(
            binding.project_root,
            creating_commit,
            relative,
        )
        assert _sha256(payload) == digest
        assert control.payloads[archive] == payload
        assert records[relative]["sha256"] == digest
        assert records[relative]["repository_path"] == relative
    for _status, relative in V2_1_EXACT_SURFACE:
        assert relative in records
    for relative in (RUNNER_TEST_RELATIVE, VALIDATOR_TEST_RELATIVE):
        archive = f"archive/control-surface/root/repo/{relative.as_posix()}"
        payload = (PROJECT_ROOT / relative).read_bytes()
        assert control.payloads[archive] == payload
        assert records[relative.as_posix()] == {
            "logical_name": relative.as_posix(),
            "bundle_relative_path": archive,
            "source_kind": "frozen_control",
            "repository_path": relative.as_posix(),
            "bytes": len(payload),
            "sha256": _sha256(payload),
        }
    assert _all_real_path_fingerprints() == before


def test_independent_validator_ast_closure_exactly_matches_producer_closure(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = importlib.import_module(VALIDATOR_MODULE)
    before = _all_real_path_fingerprints()
    binding = _synthetic_binding(tmp_path, label="independent-closure")
    implementation_commit, _owner, _review = _initialize_synthetic_epoch_one(binding)

    def blob_reader(relative: str) -> bytes:
        existence = implementation._git_completed(
            binding.project_root,
            "cat-file",
            "-e",
            f"{implementation_commit}:{relative}",
        )
        if existence.returncode != 0:
            raise implementation.base_runner.RehearsalError(
                f"optional closure candidate is absent: {relative}"
            )
        return implementation._git_blob(
            binding.project_root,
            implementation_commit,
            relative,
        )

    producer: dict[str, bytes] = {}
    for entrypoint in (SHIM_RELATIVE, IMPLEMENTATION_RELATIVE, VALIDATOR_RELATIVE):
        producer.update(
            implementation.base_runner._local_import_closure(
                entrypoint=entrypoint.as_posix(),
                blob_reader=blob_reader,
            )
        )
    independent = validator._independent_local_import_closure(
        project_root=binding.project_root,
        implementation_commit=implementation_commit,
    )
    assert independent == dict(sorted(producer.items()))
    assert set(REGISTERED_SURFACE[:3]).issubset(Path(path) for path in independent)
    assert "scripts.rehearse_p4_2a_v2_1_heldout_full_path" not in independent
    assert _all_real_path_fingerprints() == before


@pytest.mark.parametrize(
    "closure_drift",
    ("missing-local", "nonliteral-dynamic", "ambiguous-local"),
)
def test_independent_validator_ast_closure_rejects_unprovable_local_imports(
    tmp_path: Path,
    closure_drift: str,
) -> None:
    validator = importlib.import_module(VALIDATOR_MODULE)
    before = _all_real_path_fingerprints()
    binding = _synthetic_binding(tmp_path, label=f"closure-{closure_drift}")
    _initialize_synthetic_epoch_one(binding)
    target = binding.project_root / IMPLEMENTATION_RELATIVE
    if closure_drift == "missing-local":
        addition = b"\nimport scripts.synthetic_missing_local\n"
    elif closure_drift == "nonliteral-dynamic":
        addition = (
            b"\nimport importlib\n"
            b"synthetic_module_name = 'scripts.synthetic_dynamic'\n"
            b"importlib.import_module(synthetic_module_name)\n"
        )
    else:
        addition = b"\nimport scripts.synthetic_ambiguous\n"
        module_file = binding.project_root / "scripts/synthetic_ambiguous.py"
        package_file = binding.project_root / "scripts/synthetic_ambiguous/__init__.py"
        module_file.write_bytes(b"VALUE = 1\n")
        package_file.parent.mkdir(parents=True)
        package_file.write_bytes(b"VALUE = 2\n")
    target.write_bytes(target.read_bytes() + addition)
    _fixture_git(binding.project_root, "add", "--all")
    _fixture_git(binding.project_root, "commit", "--quiet", "-m", closure_drift)
    drifted_commit = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"unresolved|nonliteral|ambiguous|dynamic import",
    ):
        validator._independent_local_import_closure(
            project_root=binding.project_root,
            implementation_commit=drifted_commit,
        )
    assert _all_real_path_fingerprints() == before


def test_evidence_tree_rejects_symlink_and_hardlink_entries(tmp_path: Path) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    original = evidence / "original.bin"
    original.write_bytes(b"evidence\n")
    original.chmod(0o600)
    baseline_root, baseline_files = implementation._evidence_tree(evidence)
    assert baseline_files == (("original.bin", b"evidence\n"),)
    assert baseline_root == importlib.import_module(VALIDATOR_MODULE)._evidence_root(
        {"original.bin": b"evidence\n"}
    )

    alias = evidence / "alias.bin"
    alias.symlink_to(original)
    with pytest.raises(implementation.RehearsalV22Error, match=r"link|special"):
        implementation._evidence_tree(evidence)
    alias.unlink()

    hardlink = evidence / "hardlink.bin"
    os.link(original, hardlink)
    with pytest.raises(implementation.RehearsalV22Error, match=r"identity|mode"):
        implementation._evidence_tree(evidence)


def test_exact_os_series_two_failures_then_first_success_closes_at_ordinal_three(
    exact_two_failures_then_success_series: dict[str, Any],
) -> None:
    fixture = exact_two_failures_then_success_series
    history = fixture["history"]
    assert tuple(record.ordinal for record in history.records) == (1, 2, 3)
    assert tuple(record.outcome for record in history.records) == (
        "FAILED",
        "FAILED",
        "CANDIDATE_VALIDATED_AND_SELECTED",
    )
    assert history.failed_count == 2
    assert history.incomplete_count == 0
    assert history.validated_candidate_count == 1
    assert history.selected_attempt_ordinal == 3
    assert history.series_closed is True
    assert {record.implementation_epoch for record in history.records} == {1}
    assert {record.implementation_commit for record in history.records} == {
        fixture["epoch"][0]
    }
    assert all(record.artifact_inventory for record in history.records)
    assert [attempt["requested_outcome"] for attempt in fixture["attempts"]] == [
        "FAILED",
        "FAILED",
        "SUCCESS",
    ]
    bundle_history = fixture["bundle"]["attempt_history"]
    assert bundle_history["started_count"] == 3
    assert bundle_history["failed_count"] == 2
    assert bundle_history["incomplete_count"] == 0
    assert bundle_history["selected_attempt_ordinal"] == 3
    assert [record["outcome"] for record in bundle_history["records"]] == [
        "FAILED",
        "FAILED",
        "CANDIDATE_VALIDATED_AND_SELECTED",
    ]
    release_probe = fixture["attempts"][-1]["result"]["release_probe"]
    assert release_probe["status"] == (
        "PASS_DISPOSABLE_SAME_VALIDATOR_RELEASE_ACCEPTANCE"
    )
    assert release_probe["public_release_validation_passed"] is True
    assert release_probe["authorized_stages"] == []
    assert release_probe["heldout_evaluation_attempts_consumed"] == 0
    assert release_probe["registered_paths_untouched"] is True
    assert (
        release_probe["registered_fingerprints_before"]
        == release_probe["registered_fingerprints_after"]
    )
    assert (
        release_probe["registered_release_paths_before"]
        == release_probe["registered_release_paths_after"]
    )
    modified_rejection = release_probe["modified_after_creation_rejection"]
    assert modified_rejection == {
        "name": "modified_after_creation_receipt",
        "result": "PASS_REJECTED",
        "exception_type": "RehearsalV22ValidationError",
        "message_sha256": _sha256(
            b"authority is not one globally unique status-A Git touch"
        ),
    }
    cross_root_rejection = release_probe["cross_official_root_rejection"]
    assert cross_root_rejection == {
        "name": "synthetic_capability_against_registered_official_root",
        "result": "PASS_REJECTED",
        "exception_type": "RehearsalV22Error",
        "message_sha256": _sha256(
            b"disposable v2.2 capability is forged or cross-root"
        ),
    }
    receipt = release_probe["receipt"]
    modified = release_probe["modified_after_creation"]
    assert receipt["positive_validation_creation_state"] == {
        "unique_a_history_verified": True,
        "current_matches_creation_blob": True,
    }
    assert modified["history_statuses"] == ["A", "M"]
    assert modified["current_is_status_m_blob"] is True
    assert modified["current_matches_creation_blob"] is False
    assert modified["unique_a_current_after_negative_probe"] is False
    replay_evidence = release_probe["active_replay_evidence"]
    assert replay_evidence["positive_validation"]["invocation_count"] == 2
    assert replay_evidence["positive_validation"]["run_labels"] == [
        "run-a",
        "run-b",
    ]
    for label in (
        "modified_after_creation_rejection",
        "cross_official_root_rejection",
    ):
        assert replay_evidence[label]["invocation_count"] == 0
        assert replay_evidence[label]["run_labels"] == []
    assert all(
        row["temporary_artifact_inventory_unchanged"] is True
        and row["temporary_authority_tree_before"]
        == row["temporary_authority_tree_after"]
        for row in replay_evidence.values()
    )
    receipt_relative = Path(receipt["path"])
    receipt_history = (
        _fixture_git(
            fixture["binding"].project_root,
            "log",
            "--format=%H",
            "--",
            receipt_relative.as_posix(),
        )
        .decode("ascii", errors="strict")
        .splitlines()
    )
    assert receipt_history == [modified["commit"], receipt["creating_commit"]]
    assert _fixture_git(
        fixture["binding"].project_root,
        "rev-parse",
        "HEAD",
    ).decode().strip() == modified["commit"]
    assert _sha256(
        _fixture_git(
            fixture["binding"].project_root,
            "show",
            f"{receipt['creating_commit']}:{receipt_relative.as_posix()}",
        )
    ) == receipt["sha256"]
    assert _sha256(
        (fixture["binding"].project_root / receipt_relative).read_bytes()
    ) == modified["current_sha256"]
    assert _all_real_path_fingerprints() == fixture["real_fingerprints"]


def test_exact_os_attempt_after_published_candidate_rejects_without_ordinal_four(
    exact_two_failures_then_success_series: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    fixture = exact_two_failures_then_success_series
    binding = fixture["binding"]
    ledger_before = _tree_fingerprint(binding.ledger_root)
    destination_before = _tree_fingerprint(binding.destination)
    real_before = _all_real_path_fingerprints()
    process = _spawn_exact_attempt(binding, 4)
    returncode, stdout, stderr = _communicate_exact_attempt(process)
    assert returncode == 1
    assert stdout == ""
    error = implementation.strict_json_loads(
        stderr,
        source="exact-OS attempt after publication rejection",
    )
    assert isinstance(error, dict)
    assert implementation._canonical_json_bytes(error).decode() == stderr
    assert error == {
        "schema_version": "p4.2a-v2-2-rehearsal-execution-error-v1",
        "status": "FAILED_NO_AUTOMATIC_RETRY",
        "exception_type": "RehearsalV22Error",
        "message_sha256": _sha256(
            b"v2.2 rehearsal destination already exists"
        ),
    }
    assert not (binding.ledger_root / "attempts/000004").exists()
    assert _tree_fingerprint(binding.ledger_root) == ledger_before
    assert _tree_fingerprint(binding.destination) == destination_before
    assert _all_real_path_fingerprints() == real_before


@pytest.mark.parametrize(
    "mutation",
    ("count", "selected", "outcome", "history-root", "epoch", "mode"),
)
def test_exact_full_shape_release_creation_blob_cross_drift_rejects_before_replay(
    exact_two_failures_then_success_series: dict[str, Any],
    mutation: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = importlib.import_module(VALIDATOR_MODULE)
    fixture = exact_two_failures_then_success_series
    release_probe = fixture["attempts"][-1]["result"]["release_probe"]
    receipt_reference = release_probe["receipt"]
    receipt_payload = _fixture_git(
        fixture["binding"].project_root,
        "show",
        (
            f"{receipt_reference['creating_commit']}:"
            f"{receipt_reference['path']}"
        ),
    )
    assert _sha256(receipt_payload) == receipt_reference["sha256"]
    receipt = implementation.strict_json_loads(
        receipt_payload,
        source="exact full-shape release creation blob",
    )
    assert isinstance(receipt, dict)
    validator._cross_validate_release(bundle=fixture["bundle"], receipt=receipt)

    drifted = copy.deepcopy(receipt)
    owner = drifted["owner_authorization"]
    outcomes = owner["acknowledged_outcomes"]
    if mutation == "count":
        owner["acknowledged_failed_count"] += 1
    elif mutation == "selected":
        owner["selected_attempt_ordinal"] -= 1
    elif mutation == "outcome":
        outcomes[0]["outcome"] = "INCOMPLETE_UNTERMINALIZED"
    elif mutation == "history-root":
        drifted["lineage"]["attempt_history_root_sha256"] = "0" * 64
    elif mutation == "epoch":
        outcomes[-1]["implementation_epoch"] += 1
    else:
        drifted["execution_binding"]["mode"] = "REGISTERED_OFFICIAL"

    synthetic_before = (
        _tree_fingerprint(fixture["binding"].project_root),
        _tree_fingerprint(fixture["binding"].ledger_root),
        _tree_fingerprint(fixture["binding"].destination),
    )
    real_before = _all_real_path_fingerprints()
    with pytest.raises(validator.RehearsalV22ValidationError):
        validator._cross_validate_release(
            bundle=fixture["bundle"],
            receipt=drifted,
        )
    assert (
        _tree_fingerprint(fixture["binding"].project_root),
        _tree_fingerprint(fixture["binding"].ledger_root),
        _tree_fingerprint(fixture["binding"].destination),
    ) == synthetic_before
    assert _all_real_path_fingerprints() == real_before


def test_exact_os_selected_attempt_runs_full_path_twice_and_archives_fourteen_passes(
    exact_two_failures_then_success_series: dict[str, Any],
) -> None:
    fixture = exact_two_failures_then_success_series
    bundle = fixture["bundle"]
    runs = bundle["archive"]["runs"]
    assert [run["run_label"] for run in runs] == ["run-a", "run-b"]
    assert all(run["artifact_count"] == 14 for run in runs)
    assert all(len(run["artifacts"]) == 14 for run in runs)
    run_a = {
        artifact["source_relative_path"]: (
            fixture["binding"].destination
            / "archive/run-a/root"
            / artifact["source_relative_path"]
        ).read_bytes()
        for artifact in runs[0]["artifacts"]
    }
    run_b = {
        artifact["source_relative_path"]: (
            fixture["binding"].destination
            / "archive/run-b/root"
            / artifact["source_relative_path"]
        ).read_bytes()
        for artifact in runs[1]["artifacts"]
    }
    assert run_a == run_b
    assert len(run_a) == 14
    assert runs[0]["artifact_merkle_root_sha256"] == (
        runs[1]["artifact_merkle_root_sha256"]
    )
    identity = bundle["harness_identity"]
    assert identity["module_object_identity_equal"] is True
    assert identity["exact_os_bootstrap_passed"] is True
    assert identity["delegation_binding_passed"] == (
        "identity_root_creator_owner_and_lifetime_exact"
    )
    assert bundle["execution_binding"]["mode"] == "DISPOSABLE_FULL_SHAPE_TEST"
    assert bundle["execution_binding"]["real_registered_paths_untouched"] is True
    assert bundle["evaluation_one_shot"]["attempts_consumed_by_v2_2_rehearsal"] == 0
    assert _all_real_path_fingerprints() == fixture["real_fingerprints"]


def test_started_only_crash_uses_empty_evidence_root_and_unique_next_history(
    exact_failed_epoch_then_incomplete_success_series: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    fixture = exact_failed_epoch_then_incomplete_success_series
    history = fixture["history"]
    incomplete = history.records[1]
    assert incomplete.ordinal == 2
    assert incomplete.outcome == "INCOMPLETE_UNTERMINALIZED"
    assert incomplete.evidence_tree_root_sha256 == (
        implementation._evidence_empty_root_sha256()
    )
    assert incomplete.artifact_inventory == ()
    selected = history.records[2]
    assert selected.previous_history_root_sha256 != (
        history.records[0].record_root_sha256
    )
    assert selected.previous_history_root_sha256 != (
        implementation._history_empty_root_sha256()
    )
    assert selected.attempt_token_sha256 != incomplete.attempt_token_sha256
    assert fixture["attempts"][1]["returncode"] == -signal.SIGKILL
    assert _all_real_path_fingerprints() == fixture["real_fingerprints"]


def test_failed_incomplete_success_history_keeps_every_evidence_subtree(
    exact_failed_epoch_then_incomplete_success_series: dict[str, Any],
) -> None:
    fixture = exact_failed_epoch_then_incomplete_success_series
    history = fixture["history"]
    assert tuple(record.outcome for record in history.records) == (
        "FAILED",
        "INCOMPLETE_UNTERMINALIZED",
        "CANDIDATE_VALIDATED_AND_SELECTED",
    )
    assert history.failed_count == history.incomplete_count == 1
    assert history.selected_attempt_ordinal == 3
    assert history.records[0].artifact_inventory
    assert history.records[1].artifact_inventory == ()
    assert history.records[2].artifact_inventory
    assert tuple(record.implementation_epoch for record in history.records) == (1, 2, 2)
    bundle_history = fixture["bundle"]["attempt_history"]
    assert [record["outcome"] for record in bundle_history["records"]] == [
        "FAILED",
        "INCOMPLETE_UNTERMINALIZED",
        "CANDIDATE_VALIDATED_AND_SELECTED",
    ]
    assert [epoch["epoch"] for epoch in fixture["bundle"]["implementation_epochs"]] == [
        1,
        2,
    ]
    for record in bundle_history["records"]:
        for key in ("started", "candidate", "terminal"):
            reference = record[key]
            if reference is None:
                continue
            live = fixture["binding"].ledger_root / reference["live_relative_path"]
            archived = fixture["binding"].destination / reference["archive_relative_path"]
            assert live.read_bytes() == archived.read_bytes()
            assert _sha256(live.read_bytes()) == reference["sha256"]
        for artifact in record["artifact_inventory"]:
            live = (
                fixture["binding"].ledger_root
                / "attempts"
                / f"{record['ordinal']:06d}"
                / "evidence"
                / artifact["relative_path"]
            )
            archived = fixture["binding"].destination / artifact["archive_relative_path"]
            assert live.read_bytes() == archived.read_bytes()
            assert _sha256(live.read_bytes()) == artifact["sha256"]
    assert _all_real_path_fingerprints() == fixture["real_fingerprints"]


def test_exact_os_concurrent_holder_rejects_next_ordinal_before_attempt_creation(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before = _all_real_path_fingerprints()
    binding = _synthetic_binding(tmp_path.resolve(), label="exact-concurrency")
    epoch = _initialize_synthetic_epoch_one(binding)
    control_root = implementation.build_control_surface(
        binding.project_root,
        epoch[0],
        require_current=False,
    ).merkle_root_sha256
    _prepare_exact_action(
        binding,
        ordinal=1,
        implementation_epoch=1,
        epoch=epoch,
        control_merkle_root_sha256=control_root,
    )
    holder = _spawn_exact_attempt(binding, 1)
    _wait_for_disposable_started_checkpoint(holder, binding, 1)
    provisional = implementation.validate_live_history(binding)
    assert provisional.records[0].outcome == "INCOMPLETE_UNTERMINALIZED"
    _prepare_exact_action(
        binding,
        ordinal=2,
        implementation_epoch=1,
        epoch=epoch,
        control_merkle_root_sha256=control_root,
    )
    contender = _spawn_exact_attempt(binding, 2)
    contender_returncode, contender_stdout, contender_stderr = (
        _communicate_exact_attempt(contender)
    )
    assert contender_returncode == 1
    assert contender_stdout == ""
    contender_error = implementation.strict_json_loads(
        contender_stderr,
        source="concurrent exact-OS rejection",
    )
    assert contender_error["status"] == "FAILED_NO_AUTOMATIC_RETRY"
    assert not (binding.ledger_root / "attempts/000002").exists()

    os.kill(holder.pid, signal.SIGINT)
    os.kill(holder.pid, signal.SIGCONT)
    holder_returncode, holder_stdout, holder_stderr = _communicate_exact_attempt(holder)
    assert holder_returncode == 1
    assert holder_stdout == ""
    assert implementation.strict_json_loads(
        holder_stderr,
        source="concurrent holder failure",
    )["status"] == "FAILED_NO_AUTOMATIC_RETRY"
    terminal = binding.ledger_root / "attempts/000001/terminal.json"
    assert terminal.is_file()
    assert implementation.validate_live_history(binding).records[0].outcome == "FAILED"
    descriptor = os.open(binding.ledger_root / ".series.lock", os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
    assert _all_real_path_fingerprints() == before


@pytest.mark.parametrize(
    "mutation",
    ("duplicate-key", "noncanonical", "extra-field", "missing-field", "type-drift"),
)
def test_live_started_record_tamper_matrix_rejects_before_candidate(
    tmp_path: Path,
    mutation: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label=f"record-{mutation}")
    _initialize_synthetic_ledger(binding)
    attempt_root = _write_synthetic_started_record(binding)
    started_path = attempt_root / "started.json"
    document = json.loads(started_path.read_bytes())
    if mutation == "duplicate-key":
        payload = b'{"ordinal":1,' + started_path.read_bytes()[1:]
    elif mutation == "noncanonical":
        payload = (json.dumps(document, indent=2) + "\n").encode()
    else:
        if mutation == "extra-field":
            document["extra"] = True
        elif mutation == "missing-field":
            del document["command_sha256"]
        else:
            document["ordinal"] = "1"
        payload = implementation._canonical_json_bytes(document)
    started_path.write_bytes(payload)
    started_path.chmod(0o600)
    with pytest.raises(implementation.RehearsalV22Error, match=r"canonical|duplicate|binding"):
        implementation.validate_live_history(binding)


@pytest.mark.parametrize("entry_kind", ("hardlink", "symlink", "special"))
def test_live_ledger_rejects_link_or_special_entries(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label=f"entry-{entry_kind}")
    _initialize_synthetic_ledger(binding)
    source = binding.ledger_root / "series.json"
    target = binding.ledger_root / "unexpected"
    if entry_kind == "hardlink":
        os.link(source, target)
    elif entry_kind == "symlink":
        target.symlink_to(source)
    else:
        os.mkfifo(target, 0o600)
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"link|special|unexpected|unaliased",
    ):
        implementation.validate_live_history(binding)


def test_create_only_audit_rejects_every_existing_member_mutation_and_external_mkdir(
    exact_two_failures_then_success_series: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    fixture = exact_two_failures_then_success_series
    evidence_root = (
        fixture["binding"].ledger_root / "attempts/000003/evidence"
    )
    probe_payload = (evidence_root / "probes/create-only-ledger.json").read_bytes()
    probe = implementation.strict_json_loads(
        probe_payload,
        source="genuine create-only ledger probe",
    )
    assert isinstance(probe, dict)
    assert implementation._canonical_json_bytes(probe) == probe_payload
    assert probe["phase"] == "active_genuine_capability_before_candidate"
    assert probe["same_nested_directory_two_files"] == {
        "result": "PASS_TWO_CREATE_ONLY_FILES_PERSISTED",
        "relative_paths": [
            "probes/same-parent-first.txt",
            "probes/same-parent-second.txt",
        ],
    }
    assert (evidence_root / "probes/same-parent-first.txt").read_bytes() == b"first\n"
    assert (evidence_root / "probes/same-parent-second.txt").read_bytes() == b"second\n"
    event_probes = probe["event_mutation_probes"]
    assert [row["name"] for row in event_probes] == [
        "append_existing",
        "delete_existing",
        "delete_then_recreate_existing",
        "rename_existing",
        "chmod_existing",
        "hardlink_existing",
        "symlink_existing",
        "external_mkdir",
        "valid_dir_fd_delete_existing",
        "after_terminal_or_candidate_new_evidence",
    ]
    assert {row["result"] for row in event_probes} == {
        "PASS_REJECTED_BEFORE_EFFECT"
    }
    valid_dir_fd = next(
        row for row in event_probes if row["name"] == "valid_dir_fd_delete_existing"
    )
    assert valid_dir_fd == {
        "name": "valid_dir_fd_delete_existing",
        "result": "PASS_REJECTED_BEFORE_EFFECT",
        "exception_type": "RehearsalV22Error",
        "message_sha256": _sha256(
            b"v2.2 live ledger forbids mutation or deletion of an existing member"
        ),
    }
    prior = probe["prior_attempt_late_evidence_probes"]
    assert len(prior) == 2
    assert all(row["result"] == "PASS_REJECTED_BEFORE_EFFECT" for row in prior)
    assert all("failed" in row["name"] for row in prior)
    assert probe["all_forbidden_mutations_rejected_before_effect"] is True
    assert probe["real_path_fingerprints_unchanged"] is True
    assert _all_real_path_fingerprints() == fixture["real_fingerprints"]


def test_exact_os_genuine_capability_rejects_stolen_closure_and_atomic_race_probes(
    exact_two_failures_then_success_series: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    fixture = exact_two_failures_then_success_series
    evidence_root = fixture["binding"].ledger_root / "attempts/000003/evidence/probes"
    authority_payload = (evidence_root / "authority-forgery.json").read_bytes()
    authority = implementation.strict_json_loads(
        authority_payload,
        source="genuine exact-OS authority forgery probes",
    )
    assert authority["phase"] == "before_ledger_allocation_or_write"
    assert authority["registry_storage"] == {
        "audit_policy": "closure_private_rebound_immutable_tuple",
        "bootstrap": "closure_private_rebound_immutable_tuple",
        "capability": "closure_private_rebound_immutable_tuple",
        "delegation": "closure_private_rebound_immutable_tuple",
    }
    probe_names = [row["name"] for row in authority["probes"]]
    assert probe_names == [
        "stolen_bootstrap_nonce_and_stale_registry_snapshot",
        "direct_capability_constructor_and_stale_registry_snapshot",
        "direct_delegation_constructor_and_stale_registry_snapshot",
        "direct_audit_policy_and_stale_registry_snapshot",
        "capability_cannot_broaden_write_roots",
        "wrong_action_receipt_capability_rejected",
    ]
    assert all(
        row["result"] == "PASS_REJECTED_BEFORE_EFFECT"
        for row in authority["probes"]
    )
    assert authority["all_rejected"] is True
    assert authority["real_path_fingerprints_unchanged"] is True

    atomic_payload = (evidence_root / "atomic-publication.json").read_bytes()
    atomic = implementation.strict_json_loads(
        atomic_payload,
        source="genuine exact-OS atomic publication probe",
    )
    assert atomic["syscall"] == "renamex_np"
    assert atomic["flag"] == "RENAME_EXCL"
    assert atomic["expected_errno"] != 0
    assert atomic["probe"]["result"] == "PASS_REJECTED_BEFORE_EFFECT"
    assert atomic["candidate_preserved"] is True
    assert atomic["existing_destination_tree_unchanged"] is True
    assert atomic["single_kernel_no_replace_required_for_real_publish"] is True
    assert atomic["destination_parent_fsync_required_after_success"] is True
    assert _all_real_path_fingerprints() == fixture["real_fingerprints"]


def test_fixed_launcher_and_observed_orig_argv_executable_are_distinct_exact_bytes() -> None:
    exact_os = _preregistration()["exact_os_bootstrap_contract"]
    launcher = _fixed_interpreter()
    observed = Path(exact_os["observed_sys_orig_argv_executable_path"])
    assert launcher.as_posix() == exact_os["python_launcher_path"]
    assert _sha256(launcher.read_bytes()) == exact_os["python_launcher_sha256"]
    assert observed.is_file()
    assert observed != launcher.resolve(strict=True)
    assert _sha256(observed.read_bytes()) == exact_os["python_resolved_orig_argv_executable_sha256"]
    code = (
        "import json,sys;"
        "print(json.dumps({'executable':sys.executable,'orig0':sys.orig_argv[0],"
        "'no_site':sys.flags.no_site,'safe_path':sys.flags.safe_path,"
        "'hash':sys.flags.hash_randomization,'bytecode':sys.dont_write_bytecode},"
        "sort_keys=True))"
    )
    completed = subprocess.run(
        [launcher.as_posix(), "-S", "-P", "-B", "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=_locked_environment(),
    )
    probe = json.loads(completed.stdout)
    assert probe == {
        "bytecode": True,
        "executable": launcher.as_posix(),
        "hash": 0,
        "no_site": 1,
        "orig0": observed.as_posix(),
        "safe_path": True,
    }


def test_bundle_publication_uses_one_kernel_noreplace_rename_and_parent_fsync() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    source = inspect.getsource(implementation._publish_candidate)
    primitive_source = inspect.getsource(implementation._rename_directory_exclusive)
    module_source = (PROJECT_ROOT / IMPLEMENTATION_RELATIVE).read_text()
    assert "renamex_np" in module_source
    assert "RENAME_EXCL" in module_source
    assert "_rename_directory_exclusive" in source
    assert "os.rename(candidate, binding.destination)" not in source
    assert source.count("_rename_directory_exclusive(") == 1
    assert primitive_source.count("renamex_np(") == 1
    assert "ctypes.c_uint(0x00000004)" in primitive_source
    assert "_fsync_directory(destination_absolute.parent)" in primitive_source


def test_cli_surface_and_disposable_only_started_checkpoint_are_exact() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    assert inspect.signature(implementation.cli_main).parameters == {}
    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        implementation._normalize_cli_interrupt_handler()
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
    cli_tree = ast.parse(inspect.getsource(implementation.cli_main))
    cli_function = cli_tree.body[0]
    assert isinstance(cli_function, (ast.FunctionDef, ast.AsyncFunctionDef))
    cli_statements = cli_function.body
    if (
        cli_statements
        and isinstance(cli_statements[0], ast.Expr)
        and isinstance(cli_statements[0].value, ast.Constant)
        and isinstance(cli_statements[0].value.value, str)
    ):
        cli_statements = cli_statements[1:]
    assert isinstance(cli_statements[0], ast.Try)
    first_try_statement = cli_statements[0].body[0]
    assert isinstance(first_try_statement, ast.Expr)
    assert isinstance(first_try_statement.value, ast.Call)
    assert isinstance(first_try_statement.value.func, ast.Name)
    assert first_try_statement.value.func.id == "_normalize_cli_interrupt_handler"
    spawn_source = inspect.getsource(_spawn_exact_attempt)
    assert "signal.signal(signal.SIGINT, signal.SIG_IGN)" in spawn_source
    assert "signal.signal(signal.SIGINT, previous_sigint)" in spawn_source
    parser = implementation._parser()
    actions = {
        action.dest: action
        for action in parser._actions
        if action.dest != "help"
    }
    assert set(actions) == {"execute", "attempt_authorization", "expected_ordinal"}
    assert all(action.required for action in actions.values())
    assert actions["attempt_authorization"].default is None
    assert actions["expected_ordinal"].default is None
    source = inspect.getsource(implementation._execute_authorized_attempt)
    assert source.count("os.kill(os.getpid(), signal.SIGSTOP)") == 1
    checkpoint_if = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and "DISPOSABLE_FULL_SHAPE_TEST" in ast.unparse(node.test)
        and any(
            isinstance(candidate, ast.Call)
            and isinstance(candidate.func, ast.Attribute)
            and candidate.func.attr == "kill"
            for candidate in ast.walk(node)
        )
    )
    assert "REGISTERED_OFFICIAL" not in ast.unparse(checkpoint_if.test)
    assert "registered_official_pause_enabled" in source
    assert "resume_requires_external_SIGCONT" in source


def test_unexpected_exact_child_result_diagnostic_preserves_both_streams_canonically() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    stdout = '{"status":"unexpected-success-shape"}\n'
    stderr = '{"message_sha256":"diagnostic-only"}\n'
    diagnostic = _exact_child_diagnostic(
        implementation,
        returncode=17,
        stdout=stdout,
        stderr=stderr,
    )
    assert diagnostic == implementation._canonical_json_bytes(
        {
            "returncode": 17,
            "stderr": stderr,
            "stdout": stdout,
        }
    ).decode("utf-8")
    decoded = implementation.strict_json_loads(
        diagnostic,
        source="unexpected exact child diagnostic",
    )
    assert decoded == {"returncode": 17, "stderr": stderr, "stdout": stdout}


def test_temporary_authority_is_action_derived_and_created_only_in_issued_exact_scope(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path.resolve(), label="temp-authority-derivation")
    action = _synthetic_action_authorization(
        binding,
        ordinal=1,
        previous_history_root=implementation._history_empty_root_sha256(),
    )
    first = implementation._temporary_authority_path(binding, action)
    second = implementation._temporary_authority_path(binding, action)
    assert first == second
    assert first.parent == binding.project_root.parent
    assert first.name == (
        ".alphapilot-p4-2a-v2-2-temp-"
        + implementation._attempt_token_sha256(
            series_token_sha256=binding.series_token_sha256,
            ordinal=1,
            implementation_commit=action.implementation_commit,
            previous_history_root_sha256=action.previous_history_root_sha256,
        )
    )
    assert not os.path.lexists(first)
    policy = implementation._authority_creation_policy(binding, first)
    assert policy.write_roots == ()
    assert policy.exact_write_paths == (first,)
    create_source = inspect.getsource(implementation._create_temporary_authority)
    assert "tempfile" not in create_source
    assert "mkdtemp" not in create_source
    assert create_source.index("with _audited_execution(") < create_source.index(
        "os.mkdir(authority, 0o700)"
    )
    assert "_fsync_directory(authority.parent)" in create_source


def test_official_replay_authority_is_bundle_derived_and_never_ambient_tmp() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    source = inspect.getsource(implementation._official_validator_replay_scope)
    assert "tempfile" not in source
    assert "mkdtemp" not in source
    assert 'Path("/private/tmp")' not in source
    assert 'Path("/tmp")' not in source
    assert ".alphapilot-p4-2a-v2-2-validator-" in source
    assert source.index("bundle_payload = _regular_bytes(") < source.index(
        "authority_digest = _sha256("
    )
    assert source.index("authority_digest = _sha256(") < source.index(
        "authority = binding.project_root.parent"
    )
    assert source.index("creation_policy = _authority_creation_policy(") < source.index(
        "os.mkdir(authority, 0o700)"
    )
    assert source.count("with _audited_execution(") >= 2
    assert source.index("with _audited_execution(") < source.index(
        "os.mkdir(authority, 0o700)"
    )
    assert "os.rmdir(authority)" in source
    assert "_fsync_directory(authority.parent)" in source


def test_mutation_resolver_accepts_only_identity_checked_authority_dir_fd_paths(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    authority = tmp_path / "authority"
    nested = authority / "nested"
    external = tmp_path / "external"
    nested.mkdir(parents=True)
    external.mkdir()
    policy = implementation._AuditPolicy(
        project_root=tmp_path,
        write_roots=(authority,),
        exact_write_paths=(),
        create_only_roots=(),
        sqlite_roots=(authority,),
        git_roots=(tmp_path,),
        subprocess_mode="none",
    )

    nested_descriptor = os.open(nested, os.O_RDONLY)
    external_descriptor = os.open(external, os.O_RDONLY)
    try:
        assert implementation._audited_mutation_path(
            "member.txt",
            nested_descriptor,
            policy,
        ) == nested / "member.txt"
        assert implementation._mutation_dir_fd(
            "shutil.rmtree",
            ("member.txt", nested_descriptor),
        ) == nested_descriptor
        assert implementation._mutation_dir_fd(
            "os.remove",
            ("member.txt", nested_descriptor),
        ) == nested_descriptor

        rejected = (
            ("member.txt", external_descriptor),
            ("../member.txt", nested_descriptor),
            ("./member.txt", nested_descriptor),
            ((nested / "member.txt").as_posix(), nested_descriptor),
            ("member.txt", True),
            ("member.txt", -1),
        )
        for value, descriptor in rejected:
            with pytest.raises(implementation.RehearsalV22Error):
                implementation._audited_mutation_path(value, descriptor, policy)
    finally:
        os.close(external_descriptor)
        os.close(nested_descriptor)

    invalid_descriptor = os.open(nested, os.O_RDONLY)
    os.close(invalid_descriptor)
    with pytest.raises(implementation.RehearsalV22Error):
        implementation._audited_mutation_path(
            "member.txt",
            invalid_descriptor,
            policy,
        )

    drifted = authority / "drifted"
    drifted.mkdir()
    drifted_descriptor = os.open(drifted, os.O_RDONLY)
    try:
        os.rmdir(drifted)
        drifted.mkdir()
        with pytest.raises(implementation.RehearsalV22Error):
            implementation._audited_mutation_path(
                "member.txt",
                drifted_descriptor,
                policy,
            )
    finally:
        os.close(drifted_descriptor)

    issued_probe_source = inspect.getsource(
        implementation._ledger_create_only_probes
    )
    issued_markers = (
        "dir_fd_tree_before = _tree_fingerprint(binding.ledger_root)",
        "target_metadata_before = target.lstat()",
        "target_bytes_before = target.read_bytes()",
        "attempt_descriptor = os.open(lease.attempt_root, os.O_RDONLY)",
        '"valid_dir_fd_delete_existing"',
        'os.unlink("started.json", dir_fd=attempt_descriptor)',
        "os.close(attempt_descriptor)",
        "target_metadata_after = target.lstat()",
        "target.read_bytes() != target_bytes_before",
        "_tree_fingerprint(binding.ledger_root) != dir_fd_tree_before",
    )
    issued_offsets = [issued_probe_source.index(marker) for marker in issued_markers]
    assert issued_offsets == sorted(issued_offsets)


def test_kernel_noreplace_publish_rejects_a_racing_empty_destination_without_effect(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    candidate = tmp_path / "candidate"
    candidate.mkdir(mode=0o700)
    payload = candidate / "bundle.json"
    payload.write_bytes(b'{}\n')
    payload.chmod(0o600)
    destination = tmp_path / "destination"
    destination.mkdir(mode=0o700)
    source_metadata = candidate.lstat()
    destination_metadata = destination.lstat()
    source_tree = _tree_fingerprint(candidate)
    destination_tree = _tree_fingerprint(destination)
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"EEXIST|existing|racing destination",
    ):
        implementation._rename_directory_exclusive(candidate, destination)
    assert candidate.lstat().st_dev == source_metadata.st_dev
    assert candidate.lstat().st_ino == source_metadata.st_ino
    assert _tree_fingerprint(candidate) == source_tree
    assert destination.lstat().st_dev == destination_metadata.st_dev
    assert destination.lstat().st_ino == destination_metadata.st_ino
    assert _tree_fingerprint(destination) == destination_tree


def test_kernel_noreplace_publish_moves_one_directory_identity_and_fsyncs_parent(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    candidate = tmp_path / "candidate"
    candidate.mkdir(mode=0o700)
    (candidate / "bundle.json").write_bytes(b'{}\n')
    (candidate / "bundle.json").chmod(0o600)
    source_metadata = candidate.lstat()
    destination = tmp_path / "destination"
    evidence = implementation._rename_directory_exclusive(candidate, destination)
    assert not os.path.lexists(candidate)
    assert destination.lstat().st_dev == source_metadata.st_dev
    assert destination.lstat().st_ino == source_metadata.st_ino
    assert evidence == {
        "syscall": "renamex_np",
        "flag": "RENAME_EXCL",
        "flag_value": 4,
        "return_code": 0,
        "errno": 0,
        "single_kernel_no_replace_rename": True,
        "source_device": source_metadata.st_dev,
        "source_inode": source_metadata.st_ino,
        "destination_device": source_metadata.st_dev,
        "destination_inode": source_metadata.st_ino,
        "source_absent_after": True,
        "destination_preserved_source_identity": True,
        "destination_parent_fsync_completed": True,
    }


def test_synthetic_runtime_uses_registered_site_packages_and_only_synthetic_repo_roots(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    synthetic_root = tmp_path / "fresh-canonical-synthetic-repository"
    synthetic_root.mkdir(mode=0o700)
    observed = implementation._expected_sys_path(synthetic_root)
    registered_site = (
        Path(_preregistration()["exact_os_bootstrap_contract"]["repository_root"])
        / ".venv/lib/python3.12/site-packages"
    ).as_posix()
    assert registered_site in observed
    assert synthetic_root.as_posix() in observed
    assert (synthetic_root / "src").as_posix() in observed
    assert not any(value.startswith((synthetic_root / ".venv").as_posix()) for value in observed)


def test_fixture_database_is_create_only_0600_and_never_reused(tmp_path: Path) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before = _all_real_path_fingerprints()
    database = tmp_path / "workspace/data/alphapilot.db"
    implementation._create_fixture_database(database)
    metadata = database.lstat()
    assert stat.S_ISREG(metadata.st_mode)
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert metadata.st_nlink == 1
    connection = implementation.sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
    )
    try:
        assert connection.execute("SELECT COUNT(*) FROM news_items").fetchone() == (
            implementation.FIXTURE_RAW_COUNT,
        )
    finally:
        connection.close()
    fingerprint = _tree_fingerprint(database)
    with pytest.raises(FileExistsError, match="refusing to reuse fixture database"):
        implementation._create_fixture_database(database)
    assert _tree_fingerprint(database) == fingerprint
    assert _all_real_path_fingerprints() == before


def test_pipeline_freezes_each_bound_clock_method_once_for_mint_and_materialization() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    tree = ast.parse(inspect.getsource(implementation._execute_pipeline_inner))
    bound_names: dict[str, str] = {}
    for attribute_name in ("monotonic", "sleep"):
        reads = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == attribute_name
        ]
        assert len(reads) == 1
        read = reads[0]
        assert isinstance(read.value, ast.Name)
        assert read.value.id == "clock"
        assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and node.value is read
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ]
        assert len(assignments) == 1
        bound_names[attribute_name] = assignments[0].targets[0].id

    calls: dict[str, ast.Call] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            function_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            function_name = node.func.attr
        else:
            continue
        if function_name in {
            "_mint_v2_1_offline_rehearsal_capability",
            "run_materialize",
        }:
            assert function_name not in calls
            calls[function_name] = node
    assert set(calls) == {"_mint_v2_1_offline_rehearsal_capability", "run_materialize"}
    for call in calls.values():
        keyword_values = {keyword.arg: keyword.value for keyword in call.keywords}
        for keyword_name, bound_name in bound_names.items():
            value = keyword_values[keyword_name]
            assert isinstance(value, ast.Name)
            assert value.id == bound_name


def test_exact_attempt_completion_budget_is_local_to_success() -> None:
    assert (
        inspect.signature(_communicate_exact_attempt)
        .parameters["timeout_seconds"]
        .default
        == 300.0
    )
    tree = ast.parse(inspect.getsource(_run_exact_attempt))
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "completion_timeout_seconds"
    ]
    assert len(assignments) == 1
    selection = assignments[0].value
    assert isinstance(selection, ast.IfExp)
    assert isinstance(selection.test, ast.Compare)
    assert isinstance(selection.test.left, ast.Name)
    assert selection.test.left.id == "requested_outcome"
    assert len(selection.test.ops) == 1
    assert isinstance(selection.test.ops[0], ast.Eq)
    assert len(selection.test.comparators) == 1
    assert isinstance(selection.test.comparators[0], ast.Constant)
    assert selection.test.comparators[0].value == "SUCCESS"
    assert isinstance(selection.body, ast.Constant)
    assert selection.body.value == 3600.0
    assert isinstance(selection.orelse, ast.Constant)
    assert selection.orelse.value == 300.0

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_communicate_exact_attempt"
    ]
    assert len(calls) == 1
    keyword_values = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    timeout_value = keyword_values["timeout_seconds"]
    assert isinstance(timeout_value, ast.Name)
    assert timeout_value.id == "completion_timeout_seconds"


def _synthetic_control_surface(implementation: Any) -> Any:
    return implementation.ControlSurface(
        implementation_commit="1" * 40,
        records=({"path": "scripts/preloaded.py", "sha256": "2" * 64},),
        payloads={"scripts/preloaded.py": b"preloaded-source\n"},
        manifest_payload=b'{"schema_version":"control-manifest-v1"}\n',
        merkle_root_sha256="3" * 64,
        ast_closure_paths=(
            "scripts/imported-during-selected-runs.py",
            "scripts/preloaded.py",
        ),
        loaded_repository_sources=("scripts/preloaded.py",),
        python_inventory=b'{"python":"3.12"}\n',
        package_inventory=b'{"packages":84}\n',
    )


def test_post_run_control_surface_allows_loaded_repository_source_superset() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    preflight = _synthetic_control_surface(implementation)
    observed = replace(
        preflight,
        loaded_repository_sources=(
            *preflight.loaded_repository_sources,
            "scripts/imported-during-selected-runs.py",
        ),
    )
    assert frozenset(preflight.loaded_repository_sources) < frozenset(
        preflight.ast_closure_paths
    )
    assert frozenset(observed.loaded_repository_sources) == frozenset(
        observed.ast_closure_paths
    )
    implementation._validate_post_run_control_surface(preflight, observed)


def test_post_run_control_surface_rejects_lost_preloaded_repository_source() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    preflight = _synthetic_control_surface(implementation)
    observed = replace(
        preflight,
        loaded_repository_sources=("scripts/imported-during-selected-runs.py",),
    )
    with pytest.raises(
        implementation.RehearsalV22Error,
        match="loaded repository sources regressed",
    ):
        implementation._validate_post_run_control_surface(preflight, observed)


@pytest.mark.parametrize(
    ("field_name", "drifted_value"),
    (
        ("implementation_commit", "4" * 40),
        ("records", ({"path": "scripts/drifted.py", "sha256": "5" * 64},)),
        ("payloads", {"scripts/preloaded.py": b"drifted-source\n"}),
        ("manifest_payload", b'{"schema_version":"drifted-manifest-v1"}\n'),
        ("merkle_root_sha256", "6" * 64),
        ("ast_closure_paths", ("scripts/drifted.py",)),
        ("python_inventory", b'{"python":"drifted"}\n'),
        ("package_inventory", b'{"packages":83}\n'),
    ),
    ids=(
        "implementation-commit",
        "records",
        "payloads",
        "manifest",
        "merkle-root",
        "ast-closure",
        "python-inventory",
        "package-inventory",
    ),
)
def test_post_run_control_surface_rejects_every_stable_field_drift(
    field_name: str,
    drifted_value: Any,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    preflight = _synthetic_control_surface(implementation)
    observed = replace(preflight, **{field_name: drifted_value})
    with pytest.raises(
        implementation.RehearsalV22Error,
        match="control surface drifted during selected runs",
    ):
        implementation._validate_post_run_control_surface(preflight, observed)


def test_release_receipt_builder_materializes_static_schema_and_exact_reviewer(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = importlib.import_module(VALIDATOR_MODULE)
    before = _all_real_path_fingerprints()
    _binding, schema, receipt = _synthetic_release_receipt(tmp_path)

    static_template = implementation._schema_const_template(
        schema,
        schema,
        omit=RELEASE_RECEIPT_DYNAMIC_KEYS,
    )
    assert isinstance(static_template, dict)
    assert set(static_template) == set(schema["required"]) - RELEASE_RECEIPT_DYNAMIC_KEYS
    assert all(_typed_equal(receipt[key], value) for key, value in static_template.items())
    assert set(receipt) == set(schema["required"])
    validator._schema_validate(receipt, schema, "synthetic release receipt")
    assert not list(Draft202012Validator(schema).iter_errors(receipt))

    assert receipt["reviewer"] == EXPECTED_SYNTHETIC_RELEASE_REVIEWER
    assert receipt["reviewer"]["model"] is None
    assert "not a real owner approval" in receipt["reviewer"]["method"]
    assert receipt["owner_authorization"]["owner"] == "ouyang"
    assert receipt["owner_authorization"]["approved"] is True
    assert receipt["authorized_stages"] == []
    assert receipt["independent_checks"]["real_model_calls"] == 0
    assert receipt["independent_checks"]["real_network_calls"] == 0
    assert receipt["independent_checks"]["real_database_reads"] == 0
    assert _all_real_path_fingerprints() == before


def test_release_receipt_builder_owns_synthetic_reviewer_after_dynamic_omit() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    source = inspect.getsource(implementation._release_receipt_document)
    tree = ast.parse(source)
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "dynamic"
            for target in node.targets
        )
    ]
    assert len(assignments) == 1
    dynamic_call = assignments[0].value
    assert isinstance(dynamic_call, ast.Call)
    assert isinstance(dynamic_call.func, ast.Name)
    assert dynamic_call.func.id == "frozenset"
    assert len(dynamic_call.args) == 1
    dynamic_literal = dynamic_call.args[0]
    assert isinstance(dynamic_literal, ast.Set)
    assert {
        element.value
        for element in dynamic_literal.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    } == RELEASE_RECEIPT_DYNAMIC_KEYS

    template_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_schema_const_template"
    ]
    assert len(template_calls) == 1
    omit_keywords = [
        keyword
        for keyword in template_calls[0].keywords
        if keyword.arg == "omit"
    ]
    assert len(omit_keywords) == 1
    assert isinstance(omit_keywords[0].value, ast.Name)
    assert omit_keywords[0].value.id == "dynamic"

    update_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "receipt"
        and node.func.attr == "update"
    ]
    assert len(update_calls) == 1
    update_argument = update_calls[0].args[0]
    assert isinstance(update_argument, ast.Dict)
    update_values = {
        key.value: value
        for key, value in zip(
            update_argument.keys,
            update_argument.values,
            strict=True,
        )
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    assert ast.literal_eval(update_values["reviewer"]) == (
        EXPECTED_SYNTHETIC_RELEASE_REVIEWER
    )
    call_targets = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not {
        name
        for name in call_targets
        if any(token in name.lower() for token in ("model", "infer", "network"))
    }


def test_registered_release_probe_exits_before_synthetic_receipt_builder(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before_real = _all_real_path_fingerprints()
    binding = replace(
        _synthetic_binding(tmp_path, label="registered-release-probe"),
        mode="REGISTERED_OFFICIAL",
    )
    before_project = _tree_fingerprint(binding.project_root)
    result = implementation._run_disposable_release_probe(
        binding=binding,
        assembly=None,
        execution_context=None,
        validator_module=None,
    )
    assert result == {
        "schema_version": "p4.2a-v2-2-release-probe-result-v1",
        "status": "NOT_EXECUTED_REGISTERED_OFFICIAL",
        "synthetic_receipt_created": False,
        "registered_paths_untouched": True,
    }
    assert "reviewer" not in json.dumps(result, sort_keys=True)
    assert _tree_fingerprint(binding.project_root) == before_project
    assert _all_real_path_fingerprints() == before_real

    module_tree = ast.parse((PROJECT_ROOT / IMPLEMENTATION_RELATIVE).read_text())
    production_calls = [
        node
        for node in ast.walk(module_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_release_receipt_document"
    ]
    assert len(production_calls) == 1
    probe = next(
        node
        for node in module_tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_run_disposable_release_probe"
    )
    early_mode_gate = next(node for node in probe.body if isinstance(node, ast.If))
    assert ast.unparse(early_mode_gate.test) == (
        "binding.mode != 'DISPOSABLE_FULL_SHAPE_TEST'"
    )
    early_returns = [
        node
        for node in ast.walk(early_mode_gate)
        if isinstance(node, ast.Return)
    ]
    assert len(early_returns) == 1
    assert production_calls[0].lineno > early_mode_gate.end_lineno


@pytest.mark.parametrize(
    "fault",
    (
        "missing-identity",
        "missing-reviewer-type",
        "missing-model",
        "missing-method",
        "missing-independent",
        "extra-field",
        "identity-type",
        "identity-empty",
        "reviewer-type-type",
        "reviewer-type-enum",
        "model-type",
        "method-type",
        "method-empty",
        "independent-type",
        "independent-false",
    ),
)
def test_release_receipt_reviewer_schema_rejects_every_invalid_shape(
    tmp_path: Path,
    fault: str,
) -> None:
    validator = importlib.import_module(VALIDATOR_MODULE)
    before = _all_real_path_fingerprints()
    _binding, schema, receipt = _synthetic_release_receipt(
        tmp_path,
        label=f"reviewer-{fault}",
    )
    validator._schema_validate(receipt, schema, "baseline synthetic release receipt")
    drifted = copy.deepcopy(receipt)
    reviewer = drifted["reviewer"]
    if fault.startswith("missing-"):
        field = {
            "missing-identity": "identity",
            "missing-reviewer-type": "reviewer_type",
            "missing-model": "model",
            "missing-method": "method",
            "missing-independent": "independent_of_operator",
        }[fault]
        del reviewer[field]
    elif fault == "extra-field":
        reviewer["unexpected"] = True
    elif fault == "identity-type":
        reviewer["identity"] = 7
    elif fault == "identity-empty":
        reviewer["identity"] = ""
    elif fault == "reviewer-type-type":
        reviewer["reviewer_type"] = ["ai"]
    elif fault == "reviewer-type-enum":
        reviewer["reviewer_type"] = "operator"
    elif fault == "model-type":
        reviewer["model"] = {"name": "forbidden-structured-model"}
    elif fault == "method-type":
        reviewer["method"] = ["synthetic"]
    elif fault == "method-empty":
        reviewer["method"] = ""
    elif fault == "independent-type":
        reviewer["independent_of_operator"] = "true"
    elif fault == "independent-false":
        reviewer["independent_of_operator"] = False
    else:  # pragma: no cover - the exhaustive parameter list owns this branch.
        raise AssertionError(f"unknown reviewer fault: {fault}")

    errors = list(Draft202012Validator(schema).iter_errors(drifted))
    assert errors
    assert any(tuple(error.absolute_path)[:1] == ("reviewer",) for error in errors)
    with pytest.raises(
        validator.RehearsalV22ValidationError,
        match=r"failed JSON Schema at /reviewer",
    ):
        validator._schema_validate(drifted, schema, "drifted synthetic release receipt")
    assert _all_real_path_fingerprints() == before


def test_release_schema_keeps_schema_valid_reviewer_distinct_from_owner_authority(
    tmp_path: Path,
) -> None:
    validator = importlib.import_module(VALIDATOR_MODULE)
    before = _all_real_path_fingerprints()
    _binding, schema, receipt = _synthetic_release_receipt(
        tmp_path,
        label="non-synthetic-reviewer",
    )
    receipt["reviewer"] = {
        "identity": "independent_registered_evidence_reviewer",
        "reviewer_type": "human",
        "model": None,
        "method": "independent evidence review under the separate owner gate",
        "independent_of_operator": True,
    }
    validator._schema_validate(receipt, schema, "schema-valid release reviewer")
    assert receipt["reviewer"] != EXPECTED_SYNTHETIC_RELEASE_REVIEWER
    assert receipt["owner_authorization"]["owner"] == "ouyang"
    assert receipt["owner_authorization"]["approved"] is True
    assert receipt["authorized_stages"] == []
    assert _all_real_path_fingerprints() == before


def _exact_ls_tree_audit_shape(
    implementation: Any,
    root: Path,
) -> tuple[list[str], Any, dict[str, str]]:
    command = [
        "/usr/bin/git",
        *implementation.GIT_CONFIG_PREFIX,
        "-C",
        root.as_posix(),
        "ls-tree",
        "-z",
        "--full-tree",
        "a" * 40,
        "--",
        "scripts/module.py",
    ]
    policy = implementation._AuditPolicy(
        project_root=root,
        write_roots=(),
        exact_write_paths=(),
        create_only_roots=(),
        sqlite_roots=(),
        git_roots=(root,),
        subprocess_mode="synthetic-git",
        synthetic_git_root=root,
    )
    return command, policy, implementation._git_environment()


@pytest.mark.parametrize("subprocess_mode", ("git-read", "synthetic-git"))
def test_git_audit_allows_only_exact_read_only_ls_tree_shape(
    tmp_path: Path,
    subprocess_mode: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    root = (tmp_path / "allowed-git-root").resolve()
    root.mkdir(mode=0o700)
    command, policy, environment = _exact_ls_tree_audit_shape(implementation, root)
    policy = replace(policy, subprocess_mode=subprocess_mode)
    assert implementation._git_audit_allowed(command, None, environment, policy)


@pytest.mark.parametrize(
    "fault",
    (
        "missing-z",
        "changed-z",
        "missing-full-tree",
        "changed-full-tree",
        "short-commit",
        "uppercase-commit",
        "nonhex-commit",
        "missing-separator",
        "changed-separator",
        "unsafe-parent-path",
        "pathspec-magic",
        "double-slash-path",
        "absolute-path",
        "extra-argument",
        "wrong-root",
        "extra-environment",
        "missing-environment",
        "wrong-prefix",
        "missing-prefix",
        "subprocess-mode-none",
        "non-git-executable",
    ),
)
def test_git_audit_rejects_every_noncanonical_ls_tree_shape(
    tmp_path: Path,
    fault: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    root = (tmp_path / "allowed-git-root").resolve()
    root.mkdir(mode=0o700)
    command, policy, environment = _exact_ls_tree_audit_shape(implementation, root)
    operation = 1 + len(implementation.GIT_CONFIG_PREFIX) + 2
    if fault == "missing-z":
        del command[operation + 1]
    elif fault == "changed-z":
        command[operation + 1] = "--name-only"
    elif fault == "missing-full-tree":
        del command[operation + 2]
    elif fault == "changed-full-tree":
        command[operation + 2] = "--full-name"
    elif fault == "short-commit":
        command[operation + 3] = "a" * 39
    elif fault == "uppercase-commit":
        command[operation + 3] = "A" * 40
    elif fault == "nonhex-commit":
        command[operation + 3] = "z" * 40
    elif fault == "missing-separator":
        del command[operation + 4]
    elif fault == "changed-separator":
        command[operation + 4] = "---"
    elif fault == "unsafe-parent-path":
        command[operation + 5] = "../scripts/module.py"
    elif fault == "pathspec-magic":
        command[operation + 5] = ":(glob)scripts/*.py"
    elif fault == "double-slash-path":
        command[operation + 5] = "scripts//module.py"
    elif fault == "absolute-path":
        command[operation + 5] = "/scripts/module.py"
    elif fault == "extra-argument":
        command.append("unexpected")
    elif fault == "wrong-root":
        other = (tmp_path / "other-git-root").resolve()
        other.mkdir(mode=0o700)
        command[1 + len(implementation.GIT_CONFIG_PREFIX) + 1] = other.as_posix()
    elif fault == "extra-environment":
        environment["UNEXPECTED"] = "1"
    elif fault == "missing-environment":
        del environment["GIT_NO_REPLACE_OBJECTS"]
    elif fault == "wrong-prefix":
        command[2] = "core.hooksPath=.git/hooks"
    elif fault == "missing-prefix":
        del command[1:3]
    elif fault == "subprocess-mode-none":
        policy = replace(policy, subprocess_mode="none")
    elif fault == "non-git-executable":
        command[0] = "/usr/bin/env"
    else:  # pragma: no cover - the exhaustive parameter list owns this branch.
        raise AssertionError(f"unknown ls-tree audit fault: {fault}")
    assert not implementation._git_audit_allowed(command, None, environment, policy)


def test_git_audit_ls_tree_is_special_cased_before_generic_read_only_commands() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    tree = ast.parse(inspect.getsource(implementation._git_audit_allowed))
    string_constants = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert string_constants.count("ls-tree") == 1
    assert {"-z", "--full-tree", "--"}.issubset(string_constants)
    generic_read_only_sets = [
        {
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
        for node in ast.walk(tree)
        if isinstance(node, ast.Set)
        and any(
            isinstance(element, ast.Constant) and element.value == "cat-file"
            for element in node.elts
        )
    ]
    assert len(generic_read_only_sets) == 1
    assert "ls-tree" not in generic_read_only_sets[0]
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    lower_hex_calls = [node for node in calls if node.func.id == "_lower_hex"]
    relative_calls = [node for node in calls if node.func.id == "_relative_text"]
    assert len(lower_hex_calls) == 1
    assert len(relative_calls) == 1
    assert isinstance(lower_hex_calls[0].args[1], ast.Constant)
    assert lower_hex_calls[0].args[1].value == 40


@pytest.mark.parametrize(
    "mutator",
    (
        lambda value: {**value, "unexpected": True},
        lambda value: {key: item for key, item in value.items() if key != "policy"},
        lambda value: {**value, "automatic_retry_count": 1},
    ),
    ids=("extra-field", "missing-field", "automatic-retry"),
)
def test_series_json_contract_is_fail_closed(mutator: Any) -> None:
    contract = _preregistration()["series_ledger_contract"]["series_json_contract"]
    properties = contract["properties"]
    valid = {
        "schema_version": properties["schema_version"]["const"],
        "series_id": properties["series_id"]["const"],
        "series_token_sha256": properties["series_token_sha256"]["official_const"],
        "policy": properties["policy"]["const"],
        "ledger_root": properties["ledger_root"]["official_const"],
        "attempt_limit": properties["attempt_limit"]["const"],
        "per_attempt_action_time_owner_authorization_required": True,
        "automatic_retry_count": 0,
        "first_validated_candidate_closes_series": True,
        "preregistration": {
            "path": PREREGISTRATION_RELATIVE.as_posix(),
            "sha256": _sha256((PROJECT_ROOT / PREREGISTRATION_RELATIVE).read_bytes()),
            "creating_commit": PREREGISTRATION_COMMIT,
            "unique_a_history_verified": True,
        },
        "bundle_schema": {
            "path": BUNDLE_SCHEMA_RELATIVE.as_posix(),
            "sha256": _sha256((PROJECT_ROOT / BUNDLE_SCHEMA_RELATIVE).read_bytes()),
        },
        "release_schema": {
            "path": RELEASE_SCHEMA_RELATIVE.as_posix(),
            "sha256": _sha256((PROJECT_ROOT / RELEASE_SCHEMA_RELATIVE).read_bytes()),
        },
        "created_at_utc": "2026-08-11T12:00:00Z",
    }
    drifted = mutator(valid)
    required = set(contract["required"])
    assert set(valid) == required
    assert set(drifted) != required or drifted != valid
