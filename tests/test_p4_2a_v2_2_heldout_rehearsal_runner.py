from __future__ import annotations

import ast
import copy
import fcntl
import hashlib
import importlib
import inspect
import json
import os
import shutil
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
EPOCH_TWO_SURFACE_AUTHORITY_COMMIT = "fa4f9e0233de20d5edef201e3a93aed10a67b8be"
EPOCH_THREE_SURFACE_AUTHORITY_COMMIT = "d6c9c353217e00730457bf6b944ff26a32b8cf85"
EPOCH_FOUR_SURFACE_AUTHORITY_COMMIT = "2c3171e68ce0146c158a294d0e494ae4b618310b"
SERIES_2_EPOCH_FIVE_SURFACE_AUTHORITY_COMMIT = "5bea28957e873857e7bca6dd30f7226d8b09bbf7"
SERIES_2_EPOCH_FIVE_COMPANION_COMMIT = "281ba10ee4aa2dd09f04b75804d78def3e405365"
SERIES_2_EPOCH_FIVE_SURFACE_AUTHORITY_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-series-2-epoch5-surface-authority-20260823.json"
)
SERIES_2_EPOCH_SIX_SURFACE_AUTHORITY_COMMIT = "3ccc2f267a05137edf86c5eb72f82e0057d74f98"
SERIES_2_EPOCH_SIX_COMPANION_COMMIT = "d665e40d14f5de4671abc5c85dff220f3fb77247"
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
        Path("docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-1-preregistration-20260810.json"),
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
        Path("docs/phase4/reports/P4.2a-successor-v2-1-code-gate-authorization-20260810.json"),
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


def _independent_generic_merkle_root(payloads: dict[str, bytes]) -> str:
    assert payloads
    nodes = [
        hashlib.sha256(
            b"p4.2a-rehearsal-leaf-v2.2\0"
            + relative.encode()
            + b"\0"
            + hashlib.sha256(payloads[relative]).digest()
        ).digest()
        for relative in sorted(payloads, key=lambda value: value.encode())
    ]
    while len(nodes) > 1:
        if len(nodes) % 2:
            nodes.append(nodes[-1])
        nodes = [
            hashlib.sha256(
                b"p4.2a-rehearsal-node-v2.2\0" + nodes[index] + nodes[index + 1]
            ).digest()
            for index in range(0, len(nodes), 2)
        ]
    return nodes[0].hex()


def _independent_attempt_record_root(record: Any) -> str:
    values = (
        record.attempt_token_sha256,
        record.started_sha256,
        record.candidate_sha256 or "0" * 64,
        record.terminal_sha256 or "0" * 64,
        record.evidence_tree_root_sha256,
    )
    return _sha256(
        b"p4.2a-rehearsal-v2.2-attempt-record-v1\0"
        + record.ordinal.to_bytes(8, "big")
        + b"".join(bytes.fromhex(value) for value in values)
    )


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
        return set(left) == set(right) and all(_typed_equal(left[key], right[key]) for key in left)
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
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    return implementation.OFFICIAL_LEDGER_ROOT, implementation.OFFICIAL_DESTINATION


def _all_real_path_fingerprints() -> tuple[
    tuple[tuple[str, str, int, str], ...] | None,
    ...,
]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    ledger, destination = _real_registered_paths()
    registered_root = Path(_preregistration()["exact_os_bootstrap_contract"]["repository_root"])
    return (
        _tree_fingerprint(ledger),
        _tree_fingerprint(destination),
        _tree_fingerprint(implementation.OFFICIAL_PRIMARY_RECEIPT_ROOT),
        _tree_fingerprint(implementation.OFFICIAL_SECONDARY_SNAPSHOT_ROOT),
        _tree_fingerprint(implementation.OFFICIAL_SECONDARY_RECEIPT_ROOT),
        _tree_fingerprint(implementation.LEGACY_OFFICIAL_LEDGER_ROOT),
        _tree_fingerprint(V2_1_CONSUMED_CLAIM),
        _tree_fingerprint(registered_root / "docs/phase4/rehearsals/P4.2a-v2-calibration-v2-1"),
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
    binding = implementation._derive_binding_unchecked(
        project_root,
        action_authorization_path=(
            project_root / "docs/phase4/reports/"
            "P4.2a-v2-2-rehearsal-attempt-000001-execution-authorization-20260811.json"
        ),
    )
    for container in (
        binding.primary_series_container,
        binding.secondary_series_container,
    ):
        container.parent.mkdir(parents=True, mode=0o700)
        container.mkdir(mode=0o700)
    return binding


def _run_exact_nested_fingerprint_probe(
    tmp_path: Path,
    *,
    scenario: str,
) -> dict[str, Any]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    registered_before = implementation._real_path_fingerprints()
    registered_project_root = Path(
        str(_preregistration()["exact_os_bootstrap_contract"]["repository_root"])
    )
    fixed_site_packages = registered_project_root / ".venv/lib/python3.12/site-packages"
    assert fixed_site_packages.is_dir()
    probe_script = tmp_path / f"exact-nested-fingerprint-{scenario}.py"
    work_root = tmp_path / f"exact-nested-fingerprint-{scenario}-state"
    template = """
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__PROJECT_ROOT__)
FIXED_LAUNCHER = Path(__FIXED_LAUNCHER__)
FIXED_SITE_PACKAGES = Path(__FIXED_SITE_PACKAGES__)
WORK_ROOT = Path(__WORK_ROOT__)
SCENARIO = __SCENARIO__

assert sys.version_info[:2] == (3, 12)
assert sys.flags.hash_randomization == 0
assert sys.flags.no_site == 1
assert sys.flags.no_user_site == 1
assert sys.flags.safe_path
assert sys.dont_write_bytecode
assert sys.pycache_prefix == "/dev/null"

stdlib = Path(sys.base_prefix) / "lib/python3.12"
sys.path[:] = [
    (stdlib.parent / "python312.zip").as_posix(),
    stdlib.as_posix(),
    (stdlib / "lib-dynload").as_posix(),
    FIXED_SITE_PACKAGES.as_posix(),
    PROJECT_ROOT.as_posix(),
    (PROJECT_ROOT / "src").as_posix(),
]

from scripts import p4_2a_v2_2_heldout_rehearsal as implementation

assert dict(os.environ) == dict(implementation.EXACT_ENVIRONMENT)
assert sys.executable == FIXED_LAUNCHER.as_posix()
assert sys.orig_argv == [
    implementation.FIXED_ORIG_ARGV_EXECUTABLE.as_posix(),
    "-S",
    "-P",
    "-B",
    Path(__file__).as_posix(),
]

container_root = WORK_ROOT / "primary-container"
ledger_root = container_root / "PRIMARY-LEDGER-DO-NOT-DELETE"
probes = ledger_root / "attempts/000001/evidence/probes"
stable_receipts = container_root / "MIRROR-RECEIPTS-DO-NOT-DELETE"
secondary_container = WORK_ROOT / "secondary-container"
secondary_snapshots = secondary_container / "SEALED-LEDGER-SNAPSHOTS-DO-NOT-DELETE"
secondary_receipts = secondary_container / "MIRROR-RECEIPTS-DO-NOT-DELETE"
destination_root = WORK_ROOT / "registered-destination"
lost_ledger_root = WORK_ROOT / "lost-ledger"
retired_v2_1_destination = WORK_ROOT / "retired-v2-1-destination"
consumed_v2_1_claim = WORK_ROOT / "consumed-v2-1-claim"
third_root = WORK_ROOT / "third-registered-root"
probes.mkdir(parents=True, mode=0o700)
stable_receipts.mkdir(mode=0o700)
secondary_container.mkdir(mode=0o700)
for directory in (
    WORK_ROOT,
    container_root,
    ledger_root,
    ledger_root / "attempts",
    ledger_root / "attempts/000001",
    ledger_root / "attempts/000001/evidence",
    probes,
    stable_receipts,
    secondary_container,
):
    directory.chmod(0o700)
stable_receipt = stable_receipts / "preexisting.json"
stable_receipt.write_bytes(implementation._canonical_json_bytes({"stable": True}))
stable_receipt.chmod(0o600)


def fingerprinted_container(path: Path) -> dict[str, str]:
    metadata = path.lstat()
    return {
        **implementation._tree_fingerprint(path),
        ".identity": (
            f"device:{metadata.st_dev}:inode:{metadata.st_ino}:uid:{metadata.st_uid}"
        ),
    }


def registered_view() -> dict[str, dict[str, str]]:
    scopes = {
        "registered_v2_2_primary_container": container_root,
        "registered_v2_2_secondary_container": secondary_container,
        "registered_v2_2_destination": destination_root,
        "registered_v2_2_ledger": ledger_root,
        "registered_v2_2_primary_receipts": stable_receipts,
        "registered_v2_2_secondary_snapshots": secondary_snapshots,
        "registered_v2_2_secondary_receipts": secondary_receipts,
        "lost_v2_2_ledger": lost_ledger_root,
        "retired_v2_1_destination": retired_v2_1_destination,
        "consumed_v2_1_claim": consumed_v2_1_claim,
        "real_heldout_root": third_root,
    }
    assert frozenset(scopes) == implementation.REGISTERED_FINGERPRINT_KEYS
    observed = {
        name: implementation._tree_fingerprint(path) for name, path in scopes.items()
    }
    observed["registered_v2_2_primary_container"] = fingerprinted_container(
        container_root
    )
    observed["registered_v2_2_secondary_container"] = fingerprinted_container(
        secondary_container
    )
    assert frozenset(observed) == implementation.REGISTERED_FINGERPRINT_KEYS
    return observed


first = probes / "same-parent-first.txt"
second = probes / "same-parent-second.txt"
before_active = implementation._tree_fingerprint(ledger_root)
before_real = registered_view()
if SCENARIO != "container-only":
    for path, payload in ((first, b"first\\n"), (second, b"second\\n")):
        path.write_bytes(payload)
        path.chmod(0o600)
if SCENARIO in {"outside-ledger-file", "container-only"}:
    rogue = container_root / "outside-active-ledger.txt"
    rogue.write_bytes(b"forbidden\\n")
    rogue.chmod(0o600)
elif SCENARIO == "third-key":
    third_root.mkdir(mode=0o700)
    third = third_root / "unexpected.txt"
    third.write_bytes(b"forbidden\\n")
    third.chmod(0o600)
after_active = implementation._tree_fingerprint(ledger_root)
after_real = registered_view()

if SCENARIO == "positive":
    result = implementation._validate_active_ledger_positive_transition(
        mode="REGISTERED_OFFICIAL",
        container_root=container_root,
        ledger_root=ledger_root,
        before_real=before_real,
        after_real=after_real,
        before_active=before_active,
        after_active=after_active,
        created=((first, b"first\\n"), (second, b"second\\n")),
    )
    assert result["active_ledger_is_registered_ledger"] is True
else:
    expected_message = {
        "outside-ledger-file": "ancestor fingerprint projection drifted",
        "container-only": "outside active ledger ancestor scopes",
        "third-key": "outside active ledger ancestor scopes",
    }[SCENARIO]
    try:
        implementation._validate_registered_ledger_fingerprint_projection(
            container_root=container_root,
            ledger_root=ledger_root,
            before_real=before_real,
            after_real=after_real,
            before_active=before_active,
            after_active=after_active,
        )
    except implementation.RehearsalV22Error as exc:
        assert expected_message in str(exc)
    else:
        raise AssertionError(f"negative exact-OS probe was accepted: {SCENARIO}")

changed_keys = sorted(
    (
        name
        for name in before_real
        if before_real[name] != after_real[name]
    ),
    key=lambda value: value.encode("utf-8"),
)
sys.stdout.buffer.write(
    implementation._canonical_json_bytes(
        {
            "schema_version": "p4.2a-series2-epoch6-exact-os-nested-fingerprint-probe-v1",
            "status": "PASS_EXACT_OS_NESTED_LEDGER_PROJECTION",
            "scenario": SCENARIO,
            "changed_registered_keys": changed_keys,
            "registered_before": before_real,
            "registered_after": after_real,
            "locked_environment": True,
            "no_site": True,
            "safe_path": True,
            "bytecode_disabled": True,
        }
    )
)
"""
    probe_script.write_text(
        template.replace("__PROJECT_ROOT__", repr(PROJECT_ROOT.as_posix()))
        .replace("__FIXED_LAUNCHER__", repr(_fixed_interpreter().as_posix()))
        .replace("__FIXED_SITE_PACKAGES__", repr(fixed_site_packages.as_posix()))
        .replace("__WORK_ROOT__", repr(work_root.as_posix()))
        .replace("__SCENARIO__", repr(scenario))
    )
    probe_script.chmod(0o600)
    completed = subprocess.run(
        [
            _fixed_interpreter().as_posix(),
            "-S",
            "-P",
            "-B",
            probe_script.as_posix(),
        ],
        check=False,
        capture_output=True,
        env=_locked_environment(),
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert completed.stderr == b""
    document = implementation.strict_json_loads(
        completed.stdout,
        source=f"exact-OS nested fingerprint probe {scenario}",
    )
    assert completed.stdout == implementation._canonical_json_bytes(document)
    assert document["status"] == "PASS_EXACT_OS_NESTED_LEDGER_PROJECTION"
    assert document["scenario"] == scenario
    assert document["locked_environment"] is True
    assert document["no_site"] is True
    assert document["safe_path"] is True
    assert document["bytecode_disabled"] is True
    synthetic_before = document["registered_before"]
    synthetic_after = document["registered_after"]
    assert isinstance(synthetic_before, dict)
    assert isinstance(synthetic_after, dict)
    assert frozenset(synthetic_before) == implementation.REGISTERED_FINGERPRINT_KEYS
    assert frozenset(synthetic_after) == implementation.REGISTERED_FINGERPRINT_KEYS
    assert synthetic_before["registered_v2_2_primary_receipts"]["."] == ("directory:0700")
    assert (
        synthetic_before["registered_v2_2_primary_receipts"]
        == synthetic_after["registered_v2_2_primary_receipts"]
    )
    assert "preexisting.json" in synthetic_before["registered_v2_2_primary_receipts"]
    assert document["changed_registered_keys"] == sorted(
        (name for name in synthetic_before if synthetic_before[name] != synthetic_after[name]),
        key=lambda value: value.encode("utf-8"),
    )
    assert implementation._real_path_fingerprints() == registered_before
    return document


def _synthetic_release_receipt(
    tmp_path: Path,
    *,
    label: str = "release-receipt",
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label=label)
    release_schema_path = binding.project_root / implementation.SERIES_2_RELEASE_SCHEMA_RELATIVE
    release_schema_path.parent.mkdir(parents=True)
    release_schema_path.write_bytes(
        (PROJECT_ROOT / implementation.SERIES_2_RELEASE_SCHEMA_RELATIVE).read_bytes()
    )
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
            "implementation_epoch": 5,
            "record_root_sha256": "1" * 64,
        },
        {
            "ordinal": 2,
            "outcome": "FAILED",
            "implementation_epoch": 5,
            "record_root_sha256": "2" * 64,
        },
        {
            "ordinal": 3,
            "outcome": "CANDIDATE_VALIDATED_AND_SELECTED",
            "implementation_epoch": 5,
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
                "epoch": 5,
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
                "path": implementation.SERIES_2_PREREGISTRATION_RELATIVE.as_posix(),
                "sha256": implementation.SERIES_2_PREREGISTRATION_SHA256,
            },
            "bundle_schema": {
                "path": implementation.SERIES_2_BUNDLE_SCHEMA_RELATIVE.as_posix(),
                "sha256": implementation.SERIES_2_BUNDLE_SCHEMA_SHA256,
            },
            "release_authorization_schema": {
                "path": implementation.SERIES_2_RELEASE_SCHEMA_RELATIVE.as_posix(),
                "sha256": implementation.SERIES_2_RELEASE_SCHEMA_SHA256,
            },
            "preregistration_commit": implementation.SERIES_2_PREREGISTRATION_COMMIT,
            "v2_1_consumed_attempt_incident": exact_authority("v2_1_incident"),
            "v2_2_remediation_request": exact_authority("remediation_request"),
            "v2_2_preregistration_scope_authorization": exact_authority("v2_2_scope_authorization"),
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
    authority_epoch: int = 2,
) -> tuple[str, Any, Any, str]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    _initialize_synthetic_epoch_one(binding)
    base_commit = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
    target_relatives = (IMPLEMENTATION_RELATIVE, RUNNER_TEST_RELATIVE)
    surface_relative = Path(
        "docs/phase4/reports/P4.2a-v2-2-synthetic-epoch-2-surface-authorization.json"
    )
    surface_payload = implementation._canonical_json_bytes(
        {
            "schema_version": ("p4.2a-v2-2-implementation-epoch-surface-authorization-v1"),
            "verdict": "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
            "owner": {"identity": "ouyang", "approved": True},
            "implementation_epoch": authority_epoch,
            "base_commit": base_commit,
            "exact_surface": [
                {"path": relative.as_posix(), "status": "M"}
                for relative in sorted(
                    target_relatives,
                    key=lambda path: os.fsencode(path.as_posix()),
                )
            ],
        }
    )
    surface_commit = _fixture_commit_file(
        binding.project_root,
        surface_relative,
        surface_payload,
    )
    for target_relative in target_relatives:
        target = binding.project_root / target_relative
        target.write_bytes(target.read_bytes() + b"\n# synthetic epoch two byte\n")
    changed_paths = list(target_relatives)
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


def _initialize_synthetic_series_2_epoch_five(
    binding: Any,
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
        "synthetic-series-2-epoch-5",
        SERIES_2_EPOCH_FIVE_SURFACE_AUTHORITY_COMMIT,
    )
    changed = (
        IMPLEMENTATION_RELATIVE,
        VALIDATOR_RELATIVE,
        RUNNER_TEST_RELATIVE,
        VALIDATOR_TEST_RELATIVE,
    )
    for relative in changed:
        destination = binding.project_root / relative
        destination.write_bytes((PROJECT_ROOT / relative).read_bytes())
    _fixture_git(
        binding.project_root,
        "add",
        "--",
        *(relative.as_posix() for relative in changed),
    )
    _fixture_git(
        binding.project_root,
        "commit",
        "--quiet",
        "-m",
        "synthetic series-2 epoch five",
    )
    implementation_commit = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
    review_relative = Path(
        "docs/phase4/reports/P4.2a-v2-2-series-2-synthetic-epoch-5-implementation-review.json"
    )
    review_payload = implementation._canonical_json_bytes(
        {
            "schema_version": "p4.2a-v2-2-synthetic-implementation-review-v1",
            "verdict": "APPROVE_SERIES_2_EPOCH_5_IMPLEMENTATION",
            "reviewed_commit": implementation_commit,
            "blockers": [],
        }
    )
    review_commit = _fixture_commit_file(
        binding.project_root,
        review_relative,
        review_payload,
    )
    authority_payload = (
        binding.project_root / SERIES_2_EPOCH_FIVE_SURFACE_AUTHORITY_RELATIVE
    ).read_bytes()
    return (
        implementation_commit,
        implementation.AuthorityReference(
            path=SERIES_2_EPOCH_FIVE_SURFACE_AUTHORITY_RELATIVE.as_posix(),
            sha256=_sha256(authority_payload),
            creating_commit=SERIES_2_EPOCH_FIVE_SURFACE_AUTHORITY_COMMIT,
        ),
        implementation.AuthorityReference(
            path=review_relative.as_posix(),
            sha256=_sha256(review_payload),
            creating_commit=review_commit,
        ),
    )


def _advance_synthetic_series_2_epoch_six(
    binding: Any,
) -> tuple[str, Any, Any]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    base_commit = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
    changed = (IMPLEMENTATION_RELATIVE, RUNNER_TEST_RELATIVE)
    authority_relative = Path(
        "docs/phase4/reports/P4.2a-v2-2-series-2-synthetic-epoch-6-surface-authorization.json"
    )
    authority_payload = implementation._canonical_json_bytes(
        {
            "schema_version": "p4.2a-v2-2-implementation-epoch-surface-authorization-v1",
            "verdict": "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
            "owner": {"identity": "ouyang", "approved": True},
            "implementation_epoch": 6,
            "base_commit": base_commit,
            "exact_surface": [
                {"path": relative.as_posix(), "status": "M"}
                for relative in sorted(changed, key=lambda path: os.fsencode(path.as_posix()))
            ],
        }
    )
    authority_commit = _fixture_commit_file(
        binding.project_root,
        authority_relative,
        authority_payload,
    )
    for relative in changed:
        path = binding.project_root / relative
        path.write_bytes(path.read_bytes() + b"\n# synthetic series-2 epoch six\n")
    _fixture_git(
        binding.project_root,
        "add",
        "--",
        *(relative.as_posix() for relative in changed),
    )
    _fixture_git(binding.project_root, "commit", "--quiet", "-m", "synthetic epoch six")
    implementation_commit = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
    review_relative = Path(
        "docs/phase4/reports/P4.2a-v2-2-series-2-synthetic-epoch-6-implementation-review.json"
    )
    review_payload = implementation._canonical_json_bytes(
        {
            "schema_version": "p4.2a-v2-2-synthetic-implementation-review-v1",
            "verdict": "APPROVE_SERIES_2_EPOCH_6_IMPLEMENTATION",
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
            path=authority_relative.as_posix(),
            sha256=_sha256(authority_payload),
            creating_commit=authority_commit,
        ),
        implementation.AuthorityReference(
            path=review_relative.as_posix(),
            sha256=_sha256(review_payload),
            creating_commit=review_commit,
        ),
    )


def _advance_synthetic_epoch_two(binding: Any) -> tuple[str, Any, Any, str]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    base_commit = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
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
    implementation_commit = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
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
    implementation_epoch: int = 5,
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


def _exact_read_only_preflight_command(
    binding: Any,
    *,
    implementation_epoch: int,
    implementation_commit: str,
    owner_surface_authorization: Any,
    independent_review: Any,
) -> list[str]:
    return [
        _fixed_interpreter().as_posix(),
        "-S",
        "-P",
        "-B",
        binding.shim_path.as_posix(),
        "--preflight-only",
        "--implementation-epoch",
        str(implementation_epoch),
        "--implementation-commit",
        implementation_commit,
        "--owner-surface-authorization",
        (binding.project_root / owner_surface_authorization.path).as_posix(),
        "--independent-implementation-review",
        (binding.project_root / independent_review.path).as_posix(),
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
        f"timed out waiting for disposable SIGSTOP checkpoint; stdout={stdout!r}; stderr={stderr!r}"
    )


def _wait_for_started(
    process: subprocess.Popen[str],
    binding: Any,
    ordinal: int,
    *,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    started_path = binding.ledger_root / "attempts" / f"{ordinal:06d}" / "started.json"
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
        f"timed out waiting for started.json ({last_error!r}); stdout={stdout!r}; stderr={stderr!r}"
    )


def _wait_for_disposable_candidate_checkpoint(
    process: subprocess.Popen[str],
    binding: Any,
    ordinal: int,
    started: dict[str, Any],
    *,
    timeout_seconds: float = 3600.0,
) -> dict[str, Any]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    attempt_root = binding.ledger_root / "attempts" / f"{ordinal:06d}"
    candidate_path = attempt_root / "candidate.json"
    terminal_path = attempt_root / "terminal.json"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        waited_pid, status = os.waitpid(process.pid, os.WNOHANG | os.WUNTRACED)
        if waited_pid == 0:
            time.sleep(0.001)
            continue
        if os.WIFSTOPPED(status):
            assert os.WSTOPSIG(status) == signal.SIGSTOP
            assert candidate_path.is_file() and not candidate_path.is_symlink()
            assert not os.path.lexists(terminal_path)
            metadata = candidate_path.lstat()
            assert stat.S_ISREG(metadata.st_mode)
            assert stat.S_IMODE(metadata.st_mode) == 0o600
            assert metadata.st_nlink == 1
            payload = candidate_path.read_bytes()
            document = implementation.strict_json_loads(
                payload,
                source=f"exact-OS attempt {ordinal} candidate checkpoint",
            )
            assert isinstance(document, dict)
            assert set(document) == implementation.CANDIDATE_FIELDS
            assert implementation._canonical_json_bytes(document) == payload
            assert document["ordinal"] == ordinal
            assert document["attempt_token_sha256"] == started["attempt_token_sha256"]
            assert document["implementation_epoch"] == started["implementation_epoch"]
            assert document["implementation_commit"] == started["implementation_commit"]
            return {
                "child_pid": process.pid,
                "phase": "candidate_fsynced_before_terminal_checkpoint",
                "signal": "SIGSTOP",
                "signal_number": signal.SIGSTOP,
                "candidate_relative_path": (f"attempts/{ordinal:06d}/candidate.json"),
                "candidate_bytes": len(payload),
                "candidate_sha256": _sha256(payload),
                "candidate": document,
                "candidate_canonical": True,
                "candidate_mode_0600": True,
                "candidate_nlink_one": True,
                "candidate_identity": {
                    "st_dev": metadata.st_dev,
                    "st_ino": metadata.st_ino,
                    "st_size": metadata.st_size,
                    "st_mtime_ns": metadata.st_mtime_ns,
                    "st_ctime_ns": metadata.st_ctime_ns,
                },
                "terminal_absent_at_stop": True,
                "external_parent_observed_stop": True,
            }
        if os.WIFEXITED(status):
            process.returncode = os.waitstatus_to_exitcode(status)
        elif os.WIFSIGNALED(status):
            process.returncode = -os.WTERMSIG(status)
        stdout, stderr = process.communicate()
        raise AssertionError(
            "exact-OS child exited without the disposable candidate checkpoint: "
            f"rc={process.returncode}; stdout={stdout!r}; stderr={stderr!r}"
        )
    process.kill()
    stdout, stderr = process.communicate(timeout=30)
    raise AssertionError(
        f"timed out waiting for candidate SIGSTOP checkpoint; stdout={stdout!r}; stderr={stderr!r}"
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
    candidate_checkpoint: dict[str, Any] | None = None
    if requested_outcome == "FAILED":
        os.kill(process.pid, signal.SIGINT)
        os.kill(process.pid, signal.SIGCONT)
    else:
        os.kill(process.pid, signal.SIGCONT)
        candidate_checkpoint = _wait_for_disposable_candidate_checkpoint(
            process,
            binding,
            ordinal,
            started,
        )
        if requested_outcome == "INCOMPLETE":
            candidate_checkpoint["external_parent_action"] = "SIGKILL"
            os.kill(process.pid, signal.SIGKILL)
        else:
            candidate_checkpoint["external_parent_action"] = "SIGCONT"
            os.kill(process.pid, signal.SIGCONT)
    completion_timeout_seconds = 3600.0 if requested_outcome == "SUCCESS" else 300.0
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
        assert candidate_checkpoint is not None
        candidate_path = binding.ledger_root / "attempts" / f"{ordinal:06d}" / "candidate.json"
        candidate_metadata = candidate_path.lstat()
        assert {
            "st_dev": candidate_metadata.st_dev,
            "st_ino": candidate_metadata.st_ino,
            "st_size": candidate_metadata.st_size,
            "st_mtime_ns": candidate_metadata.st_mtime_ns,
            "st_ctime_ns": candidate_metadata.st_ctime_ns,
        } == candidate_checkpoint["candidate_identity"]
        candidate_payload = candidate_path.read_bytes()
        assert len(candidate_payload) == candidate_checkpoint["candidate_bytes"]
        assert _sha256(candidate_payload) == candidate_checkpoint["candidate_sha256"]
        assert not os.path.lexists(candidate_path.parent / "terminal.json")
        candidate_checkpoint["candidate_unchanged_after_external_kill"] = True
        result = None
    return {
        "requested_outcome": requested_outcome,
        "started": started,
        "candidate_checkpoint": candidate_checkpoint,
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
    epoch = _initialize_synthetic_series_2_epoch_five(binding)
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
            implementation_epoch=5,
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
    epoch_one = _initialize_synthetic_series_2_epoch_five(binding)
    control_one = implementation.build_control_surface(
        binding.project_root,
        epoch_one[0],
        require_current=False,
    ).merkle_root_sha256
    _prepare_exact_action(
        binding,
        ordinal=1,
        implementation_epoch=5,
        epoch=epoch_one,
        control_merkle_root_sha256=control_one,
    )
    first = _run_exact_attempt(binding, 1, requested_outcome="FAILED")
    first_history = implementation.validate_live_history(binding)
    assert first_history.records[0].outcome == "FAILED"
    assert (binding.ledger_root / "attempts/000001/terminal.json").is_file()

    epoch_two_full = _advance_synthetic_series_2_epoch_six(binding)
    epoch_two = epoch_two_full[:3]
    control_two = implementation.build_control_surface(
        binding.project_root,
        epoch_two[0],
        require_current=False,
    ).merkle_root_sha256
    _prepare_exact_action(
        binding,
        ordinal=2,
        implementation_epoch=6,
        epoch=epoch_two,
        control_merkle_root_sha256=control_two,
    )
    second_root = binding.ledger_root / "attempts/000002"
    assert not os.path.lexists(second_root)
    assert not os.path.lexists(second_root / "candidate.json")
    assert not os.path.lexists(second_root / "terminal.json")
    second = _run_exact_attempt(binding, 2, requested_outcome="INCOMPLETE")
    assert list((second_root / "evidence").iterdir())
    candidate_payload = (second_root / "candidate.json").read_bytes()
    candidate_document = implementation.strict_json_loads(
        candidate_payload,
        source="exact candidate-without-terminal ordinal two",
    )
    assert isinstance(candidate_document, dict)
    assert implementation._canonical_json_bytes(candidate_document) == candidate_payload
    assert not (second_root / "terminal.json").exists()
    incomplete_history = implementation.validate_live_history(binding)
    assert incomplete_history.records[1].outcome == "INCOMPLETE_UNTERMINALIZED"
    assert incomplete_history.records[1].reached_stage == "candidate_without_terminal"
    assert incomplete_history.records[1].candidate_bytes == candidate_payload
    assert incomplete_history.records[1].terminal_bytes is None
    assert incomplete_history.records[1].evidence_tree_root_sha256 != (
        implementation._evidence_empty_root_sha256()
    )
    checkpoint = second["candidate_checkpoint"]
    assert checkpoint["child_pid"] > 0
    assert checkpoint["phase"] == "candidate_fsynced_before_terminal_checkpoint"
    assert checkpoint["signal"] == "SIGSTOP"
    assert checkpoint["signal_number"] == signal.SIGSTOP
    assert checkpoint["candidate_relative_path"] == "attempts/000002/candidate.json"
    assert checkpoint["candidate_bytes"] == len(candidate_payload)
    assert checkpoint["candidate_sha256"] == _sha256(candidate_payload)
    assert checkpoint["candidate"] == candidate_document
    assert checkpoint["candidate_canonical"] is True
    assert checkpoint["candidate_mode_0600"] is True
    assert checkpoint["candidate_nlink_one"] is True
    candidate_metadata = (second_root / "candidate.json").lstat()
    assert checkpoint["candidate_identity"] == {
        "st_dev": candidate_metadata.st_dev,
        "st_ino": candidate_metadata.st_ino,
        "st_size": candidate_metadata.st_size,
        "st_mtime_ns": candidate_metadata.st_mtime_ns,
        "st_ctime_ns": candidate_metadata.st_ctime_ns,
    }
    assert checkpoint["terminal_absent_at_stop"] is True
    assert checkpoint["external_parent_observed_stop"] is True
    assert checkpoint["external_parent_action"] == "SIGKILL"
    assert checkpoint["candidate_unchanged_after_external_kill"] is True

    _prepare_exact_action(
        binding,
        ordinal=3,
        implementation_epoch=6,
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
    implementation_epoch: int = 5,
    implementation_commit: str = "2" * 40,
    owner_surface_authorization: Any | None = None,
    independent_review: Any | None = None,
    control_merkle_root_sha256: str = "f" * 64,
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
        control_merkle_root_sha256=control_merkle_root_sha256,
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


def _write_synthetic_candidate_record(
    binding: Any,
    attempt_root: Path,
    *,
    run_a_root_sha256: str = "a" * 64,
    run_b_root_sha256: str = "b" * 64,
) -> dict[str, Any]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    started = json.loads((attempt_root / "started.json").read_bytes())
    evidence_root_sha256, _inventory = implementation._inventory_from_evidence(
        attempt_root / "evidence"
    )
    control = started["control_merkle_root_sha256"]
    candidate = {
        "schema_version": "p4.2a-v2-2-rehearsal-attempt-candidate-v1",
        "series_id": implementation.REHEARSAL_ID,
        "ordinal": int(attempt_root.name),
        "attempt_token_sha256": started["attempt_token_sha256"],
        "implementation_epoch": started["implementation_epoch"],
        "implementation_commit": started["implementation_commit"],
        "run_a_root_sha256": run_a_root_sha256,
        "run_b_root_sha256": run_b_root_sha256,
        "control_surface_root_sha256": control,
        "evidence_tree_root_sha256": evidence_root_sha256,
        "candidate_content_root_sha256": (
            implementation._candidate_content_root_sha256(
                previous_history_root_sha256=started["previous_history_root_sha256"],
                run_a_root_sha256=run_a_root_sha256,
                run_b_root_sha256=run_b_root_sha256,
                control_surface_root_sha256=control,
                evidence_tree_root_sha256=evidence_root_sha256,
            )
        ),
        "validated_at_utc": "2026-08-23T12:00:00Z",
    }
    _write_test_file(
        attempt_root / "candidate.json",
        implementation._canonical_json_bytes(candidate),
    )
    return candidate


def _append_synthetic_terminal_record(
    binding: Any,
    *,
    outcome: str,
    implementation_epoch: int = 5,
    epoch: tuple[str, Any, Any] | None = None,
    control_merkle_root_sha256: str = "f" * 64,
    run_a_root_sha256: str = "a" * 64,
    run_b_root_sha256: str = "b" * 64,
) -> Any:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    if outcome not in {"FAILED", "CANDIDATE_VALIDATED_AND_SELECTED"}:
        raise AssertionError(f"unsupported synthetic terminal outcome: {outcome}")
    attempt_root = _write_synthetic_started_record(
        binding,
        implementation_epoch=implementation_epoch,
        implementation_commit=(epoch[0] if epoch is not None else f"{implementation_epoch:x}" * 40),
        owner_surface_authorization=(epoch[1] if epoch is not None else None),
        independent_review=(epoch[2] if epoch is not None else None),
        control_merkle_root_sha256=control_merkle_root_sha256,
    )
    ordinal = int(attempt_root.name)
    started = json.loads((attempt_root / "started.json").read_bytes())
    evidence_root_sha256 = implementation._evidence_empty_root_sha256()
    if outcome == "CANDIDATE_VALIDATED_AND_SELECTED":
        _write_synthetic_candidate_record(
            binding,
            attempt_root,
            run_a_root_sha256=run_a_root_sha256,
            run_b_root_sha256=run_b_root_sha256,
        )
        error: dict[str, Any] | None = None
    else:
        error = {
            "exception_type": "SyntheticFailure",
            "message_sha256": "d" * 64,
            "failing_stage": "synthetic_registered_test",
        }
    terminal = {
        "schema_version": "p4.2a-v2-2-rehearsal-attempt-terminal-v1",
        "series_id": implementation.REHEARSAL_ID,
        "ordinal": ordinal,
        "attempt_token_sha256": started["attempt_token_sha256"],
        "outcome": outcome,
        "reached_stage": "synthetic_registered_test",
        "implementation_epoch": implementation_epoch,
        "implementation_commit": started["implementation_commit"],
        "automatic_retry_count": 0,
        "artifact_inventory": [],
        "error": error,
        "evidence_tree_root_sha256": evidence_root_sha256,
        "completed_at_utc": "2026-08-23T12:00:00Z",
    }
    _write_test_file(
        attempt_root / "terminal.json",
        implementation._canonical_json_bytes(terminal),
    )
    return implementation.validate_live_history(binding)


def _install_synthetic_mirror(binding: Any, history: Any) -> dict[str, Any]:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    assert history.records
    for root in (
        binding.secondary_snapshot_root,
        binding.primary_receipt_root,
        binding.secondary_receipt_root,
    ):
        if not root.exists():
            root.mkdir(mode=0o700)
    ordinal = len(history.records)
    live_root = history.live_ledger_root_sha256
    assert isinstance(live_root, str)
    snapshot = binding.secondary_snapshot_root / implementation._mirror_snapshot_name(
        ordinal,
        live_root,
    )
    shutil.copytree(binding.ledger_root, snapshot, copy_function=shutil.copy2)
    primary = implementation._strict_private_tree_inventory(
        binding.ledger_root,
        label="synthetic primary mirror source",
    )
    secondary = implementation._strict_private_tree_inventory(
        snapshot,
        label="synthetic secondary mirror snapshot",
    )
    assert primary.rows == secondary.rows
    assert primary.payloads == secondary.payloads
    record = history.records[-1]
    receipt = {
        "schema_version": implementation.MIRROR_RECEIPT_SCHEMA,
        "series_token_sha256": binding.series_token_sha256,
        "ordinal": ordinal,
        "attempt_outcome": record.outcome,
        "attempt_sealed": record.outcome != "INCOMPLETE_UNTERMINALIZED",
        "primary_ledger_root": binding.ledger_root.as_posix(),
        "secondary_snapshot_root": snapshot.as_posix(),
        "history_root_sha256": history.history_root_sha256,
        "live_ledger_root_sha256": live_root,
        "file_count": primary.file_count,
        "total_bytes": primary.total_bytes,
        "primary_inventory_sha256": primary.sha256,
        "secondary_inventory_sha256": secondary.sha256,
        "second_copy_verified": True,
        "verified_at_utc": implementation.FIXED_WALL_CLOCK_TEXT,
    }
    payload = implementation._canonical_json_bytes(receipt)
    name = implementation._mirror_receipt_filename(ordinal, live_root)
    paths = (
        binding.primary_receipt_root / name,
        binding.secondary_receipt_root / name,
    )
    for path in paths:
        _write_test_file(path, payload)
    return {"snapshot": snapshot, "receipt_paths": paths, "receipt": receipt}


@pytest.fixture(scope="module")
def series_2_epoch_five_source(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Any]:
    root = tmp_path_factory.mktemp("series-2-epoch-five-source").resolve()
    binding = _synthetic_binding(root, label="registered-source")
    epoch = _initialize_synthetic_series_2_epoch_five(binding)
    return {"project_root": binding.project_root, "epoch": epoch}


def _clone_series_2_epoch_five_source(
    source: dict[str, Any],
    binding: Any,
) -> tuple[str, Any, Any]:
    assert list(binding.project_root.iterdir()) == []
    binding.project_root.rmdir()
    _fixture_git(
        binding.project_root.parent,
        "clone",
        "--quiet",
        "--no-hardlinks",
        source["project_root"].as_posix(),
        binding.project_root.name,
    )
    return source["epoch"]


def test_series_2_constants_are_isolated_and_official_token_recomputes() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    assert implementation.INCIDENT_SHA256 == (
        "d658336f61cdca0239584b696043fe4abc5ede1ef7aff76a4fe514b7b5d0735c"
    )
    assert implementation.PREREGISTRATION_SHA256 == (
        "8f52a9e24df11e23a900b5cb79720f3b4aae999c6ab770a9038ebe2617e8d8d5"
    )
    assert implementation.SERIES_2_TOKEN_SEED_SHA256 != implementation.INCIDENT_SHA256
    assert implementation.SERIES_2_PREREGISTRATION_SHA256 != implementation.PREREGISTRATION_SHA256
    material = (
        implementation.SERIES_2_TOKEN_SEED_SHA256
        + "\0"
        + implementation.REHEARSAL_ID
        + "\0"
        + implementation.OFFICIAL_DESTINATION.as_posix()
    ).encode()
    assert len(material) == 232
    assert _sha256(material) == implementation.OFFICIAL_SERIES_TOKEN
    assert implementation._series_token(implementation.OFFICIAL_DESTINATION) == (
        "2543d679819f96958baf747ef61dda2044013a0b00a9cb824c0d7675640d9f93"
    )
    assert implementation.OFFICIAL_LEDGER_ROOT != implementation.LEGACY_OFFICIAL_LEDGER_ROOT
    assert "EVIDENCE-DO-NOT-DELETE" in implementation.OFFICIAL_LEDGER_ROOT.as_posix()


def test_series_2_series_document_is_exact_fourteen_fields_with_origin_five(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label="series-document")
    document = implementation._series_document(
        binding,
        created_at_utc="2026-08-23T12:00:00Z",
    )
    assert len(document) == 14
    assert set(document) == set(implementation.SERIES_FIELDS)
    assert document["schema_version"] == implementation.SERIES_2_SERIES_SCHEMA_VERSION
    assert document["implementation_epoch_origin"] == 5
    assert document["series_token_sha256"] == binding.series_token_sha256


def test_series_2_amendment_and_exact_schema_pointer_profiles_revalidate(
    tmp_path: Path,
    series_2_epoch_five_source: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label="amendment-validation")
    epoch = _clone_series_2_epoch_five_source(series_2_epoch_five_source, binding)
    head = _fixture_git(binding.project_root, "rev-parse", "HEAD").decode().strip()
    reference = implementation.validate_series_2_preregistration(
        binding.project_root,
        execution_head=head,
    )
    assert reference.path == implementation.SERIES_2_PREREGISTRATION_RELATIVE.as_posix()
    assert reference.sha256 == implementation.SERIES_2_PREREGISTRATION_SHA256
    assert len(implementation.SERIES_2_BUNDLE_SCHEMA_DELTA_POINTERS) == 7
    assert len(implementation.SERIES_2_RELEASE_SCHEMA_DELTA_POINTERS) == 6
    assert implementation._typed_json_equal(True, 1) is False
    preregistration_source = inspect.getsource(implementation.validate_series_2_preregistration)
    assert preregistration_source.count("_typed_json_equal(") >= 3
    surface = implementation.build_control_surface(
        binding.project_root,
        epoch[0],
        require_current=False,
    )
    amendment = implementation.SERIES_2_PREREGISTRATION_RELATIVE.as_posix()
    amendment_record = next(row for row in surface.records if row["logical_name"] == amendment)
    amendment_payload = surface.payloads[amendment_record["bundle_relative_path"]]
    assert implementation.SERIES_2_TOKEN_SEED_SHA256.encode() in amendment_payload
    assert implementation._history_empty_root_sha256() != (
        "a466de7b349882f2bcd556a4b4d00bf38bace9adb593b0e3b6296c415a8c9ca1"
    )


def test_series_2_fresh_storage_preflight_is_read_only_and_identity_bound(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label="storage-preflight")
    before = (
        _tree_fingerprint(binding.primary_series_container),
        _tree_fingerprint(binding.secondary_series_container),
    )
    evidence = implementation._read_only_storage_preflight(binding)
    assert evidence["storage_state"] == "FRESH_SERIES_ALL_REGISTERED_LEAVES_ABSENT"
    assert set(evidence["primary_container"]) >= {"device", "inode", "owner_uid"}
    assert set(evidence["registered_leaf_state"].values()) == {"ABSENT"}
    assert evidence["paths_created"] == 0
    assert (
        _tree_fingerprint(binding.primary_series_container),
        _tree_fingerprint(binding.secondary_series_container),
    ) == before


@pytest.mark.parametrize(
    "leaf_name",
    (
        "ledger_root",
        "primary_receipt_root",
        "secondary_snapshot_root",
        "secondary_receipt_root",
    ),
)
def test_series_2_fresh_storage_preflight_rejects_each_precreated_leaf(
    tmp_path: Path,
    leaf_name: str,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label=f"precreated-{leaf_name}")
    leaf = getattr(binding, leaf_name)
    leaf.mkdir(mode=0o700)
    before = _tree_fingerprint(leaf)
    with pytest.raises(implementation.RehearsalV22Error, match=r"leaf|mirror|series"):
        implementation._read_only_storage_preflight(binding)
    assert _tree_fingerprint(leaf) == before


def test_series_2_next_epoch_accepts_origin_repeat_and_successor_only(
    tmp_path: Path,
    series_2_epoch_five_source: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label="epoch-transitions")
    _initialize_synthetic_ledger(binding)
    empty = implementation.validate_live_history(binding)
    implementation._validate_next_series_2_epoch(empty, 5)
    with pytest.raises(implementation.RehearsalV22Error, match=r"origin 5"):
        implementation._validate_next_series_2_epoch(empty, 4)
    epoch_five_binding = _clone_series_2_epoch_five_source(
        series_2_epoch_five_source,
        binding,
    )
    epoch_five = _append_synthetic_terminal_record(
        binding,
        outcome="FAILED",
        epoch=epoch_five_binding,
    )
    implementation._validate_next_series_2_epoch(epoch_five, 5)
    implementation._validate_next_series_2_epoch(epoch_five, 6)
    for forbidden in (4, 7):
        with pytest.raises(implementation.RehearsalV22Error, match=r"successor"):
            implementation._validate_next_series_2_epoch(epoch_five, forbidden)
    epoch_six_binding = _advance_synthetic_series_2_epoch_six(binding)
    epoch_six = _append_synthetic_terminal_record(
        binding,
        outcome="FAILED",
        implementation_epoch=6,
        epoch=epoch_six_binding[:3],
    )
    with pytest.raises(implementation.RehearsalV22Error, match=r"successor"):
        implementation._validate_next_series_2_epoch(epoch_six, 5)


@pytest.mark.parametrize(
    "outcome",
    ("FAILED", "INCOMPLETE_UNTERMINALIZED", "CANDIDATE_VALIDATED_AND_SELECTED"),
)
def test_series_2_full_snapshot_and_paired_receipts_validate_each_outcome(
    tmp_path: Path,
    outcome: str,
    series_2_epoch_five_source: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label=f"mirror-positive-{outcome}")
    _initialize_synthetic_ledger(binding)
    epoch = _clone_series_2_epoch_five_source(series_2_epoch_five_source, binding)
    history = (
        implementation.validate_live_history(binding)
        if outcome == "INCOMPLETE_UNTERMINALIZED"
        else None
    )
    if outcome == "INCOMPLETE_UNTERMINALIZED":
        _write_synthetic_started_record(
            binding,
            implementation_commit=epoch[0],
            owner_surface_authorization=epoch[1],
            independent_review=epoch[2],
        )
        history = implementation.validate_live_history(binding)
    else:
        history = _append_synthetic_terminal_record(
            binding,
            outcome=outcome,
            epoch=epoch,
        )
    installed = _install_synthetic_mirror(binding, history)
    receipts = implementation._validate_second_copy_history(binding, history)
    assert receipts == (installed["receipt"],)
    assert implementation._read_only_storage_preflight(binding)["storage_state"] == (
        "EXISTING_FULLY_MIRRORED"
    )
    assert all(path.is_file() for path in installed["receipt_paths"])
    assert installed["snapshot"].is_dir()


def test_candidate_without_terminal_is_incomplete_mirrored_continuable_and_archived(
    tmp_path: Path,
    series_2_epoch_five_source: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label="candidate-without-terminal-positive")
    _initialize_synthetic_ledger(binding)
    epoch = _clone_series_2_epoch_five_source(series_2_epoch_five_source, binding)
    attempt_root = _write_synthetic_started_record(
        binding,
        implementation_commit=epoch[0],
        owner_surface_authorization=epoch[1],
        independent_review=epoch[2],
    )
    candidate = _write_synthetic_candidate_record(binding, attempt_root)
    candidate_payload = (attempt_root / "candidate.json").read_bytes()

    incomplete = implementation.validate_live_history(binding)
    assert incomplete.started_count == incomplete.incomplete_count == 1
    assert incomplete.failed_count == incomplete.validated_candidate_count == 0
    assert incomplete.selected_attempt_ordinal is None
    assert incomplete.series_closed is False
    record = incomplete.records[0]
    assert record.outcome == "INCOMPLETE_UNTERMINALIZED"
    assert record.reached_stage == "candidate_without_terminal"
    assert record.candidate_bytes == candidate_payload
    assert record.candidate_sha256 == _sha256(candidate_payload)
    assert record.terminal_bytes is None
    assert record.terminal_sha256 is None
    assert record.evidence_tree_root_sha256 == candidate["evidence_tree_root_sha256"]
    installed_incomplete = _install_synthetic_mirror(binding, incomplete)
    assert (
        implementation._validate_continuation_mirror_state(
            binding,
            incomplete,
            permit_unmirrored_final_incomplete=False,
        )
        is True
    )
    assert implementation._validate_second_copy_history(binding, incomplete) == (
        installed_incomplete["receipt"],
    )

    completed = _append_synthetic_terminal_record(
        binding,
        outcome="CANDIDATE_VALIDATED_AND_SELECTED",
        epoch=epoch,
    )
    assert tuple(item.outcome for item in completed.records) == (
        "INCOMPLETE_UNTERMINALIZED",
        "CANDIDATE_VALIDATED_AND_SELECTED",
    )
    assert completed.records[0].reached_stage == "candidate_without_terminal"
    assert completed.selected_attempt_ordinal == 2
    assert completed.validated_candidate_count == 1
    assert completed.series_closed is True
    _install_synthetic_mirror(binding, completed)
    implementation._validate_second_copy_history(binding, completed)

    packed = implementation._history_archive(binding, completed)
    schema = implementation._bundle_schema(binding.project_root)
    attempt_record_schema = {
        "$schema": schema["$schema"],
        "$ref": "#/$defs/attemptRecord",
        "$defs": schema["$defs"],
    }
    packed_record = packed.summary["records"][0]
    assert not list(Draft202012Validator(attempt_record_schema).iter_errors(packed_record))
    assert packed_record["ordinal"] == 1
    assert packed_record["outcome"] == "INCOMPLETE_UNTERMINALIZED"
    assert packed_record["reached_stage"] == "candidate_without_terminal"
    assert packed_record["candidate"] is None
    assert packed_record["terminal"] is None
    assert _independent_attempt_record_root(completed.records[0]) == (
        completed.records[0].record_root_sha256
    )
    assert packed_record["record_root_sha256"] == completed.records[0].record_root_sha256
    candidate_archive = "archive/attempt-history/attempts/000001/candidate.json"
    assert packed.payloads[candidate_archive] == candidate_payload
    assert {
        "path": candidate_archive,
        "sha256": _sha256(candidate_payload),
    } in packed.archive_record["files"]
    assert packed.archive_record["history_merkle_root_sha256"] == (
        _independent_generic_merkle_root(dict(packed.payloads))
    )


@pytest.mark.parametrize(
    "fault",
    (
        "field-set",
        "noncanonical",
        "attempt-token",
        "evidence-root",
        "started-binding",
    ),
)
def test_candidate_without_terminal_rejects_every_candidate_binding_drift(
    tmp_path: Path,
    fault: str,
    series_2_epoch_five_source: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label=f"candidate-without-terminal-{fault}")
    _initialize_synthetic_ledger(binding)
    epoch = _clone_series_2_epoch_five_source(series_2_epoch_five_source, binding)
    attempt_root = _write_synthetic_started_record(
        binding,
        implementation_commit=epoch[0],
        owner_surface_authorization=epoch[1],
        independent_review=epoch[2],
    )
    candidate = _write_synthetic_candidate_record(binding, attempt_root)
    candidate_path = attempt_root / "candidate.json"
    if fault == "field-set":
        candidate["unexpected"] = True
    elif fault == "attempt-token":
        candidate["attempt_token_sha256"] = "f" * 64
    elif fault == "evidence-root":
        candidate["evidence_tree_root_sha256"] = "e" * 64
        candidate["candidate_content_root_sha256"] = implementation._candidate_content_root_sha256(
            previous_history_root_sha256=json.loads((attempt_root / "started.json").read_bytes())[
                "previous_history_root_sha256"
            ],
            run_a_root_sha256=candidate["run_a_root_sha256"],
            run_b_root_sha256=candidate["run_b_root_sha256"],
            control_surface_root_sha256=candidate["control_surface_root_sha256"],
            evidence_tree_root_sha256=candidate["evidence_tree_root_sha256"],
        )
    elif fault == "started-binding":
        candidate["implementation_commit"] = "f" * 40
    if fault == "noncanonical":
        candidate_path.write_bytes(
            (json.dumps(candidate, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
    else:
        candidate_path.write_bytes(implementation._canonical_json_bytes(candidate))
    before = _tree_fingerprint(binding.ledger_root)
    with pytest.raises(implementation.RehearsalV22Error, match=r"candidate"):
        implementation.validate_live_history(binding)
    assert _tree_fingerprint(binding.ledger_root) == before
    assert not (attempt_root / "terminal.json").exists()
    assert not any(
        os.path.lexists(path)
        for path in (
            binding.secondary_snapshot_root,
            binding.primary_receipt_root,
            binding.secondary_receipt_root,
        )
    )


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
def test_series_2_second_copy_tamper_matrix_blocks_without_rolling_back_terminal(
    tmp_path: Path,
    fault: str,
    series_2_epoch_five_source: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label=f"mirror-fault-{fault}")
    _initialize_synthetic_ledger(binding)
    epoch = _clone_series_2_epoch_five_source(series_2_epoch_five_source, binding)
    history = _append_synthetic_terminal_record(
        binding,
        outcome="FAILED",
        epoch=epoch,
    )
    installed = _install_synthetic_mirror(binding, history)
    snapshot = installed["snapshot"]
    primary_receipt, secondary_receipt = installed["receipt_paths"]
    if fault == "snapshot-missing":
        shutil.rmtree(snapshot)
    elif fault == "snapshot-extra-file":
        _write_test_file(snapshot / "unexpected.bin", b"unexpected\n")
    elif fault == "primary-receipt-missing":
        primary_receipt.unlink()
    elif fault == "secondary-receipt-mismatch":
        secondary_receipt.write_bytes(secondary_receipt.read_bytes() + b"drift\n")
    elif fault == "paired-timestamp-substitution":
        document = json.loads(primary_receipt.read_bytes())
        document["verified_at_utc"] = "2026-08-23T12:00:00Z"
        drifted = implementation._canonical_json_bytes(document)
        primary_receipt.write_bytes(drifted)
        secondary_receipt.write_bytes(drifted)
    elif fault == "snapshot-mode":
        snapshot.chmod(0o755)
    elif fault == "snapshot-symlink":
        (snapshot / "alias").symlink_to(snapshot / "series.json")
    elif fault == "snapshot-hardlink":
        os.link(snapshot / "series.json", snapshot / "hardlink.json")
    else:
        residue = binding.secondary_snapshot_root / (f".staging-{snapshot.name}-collision")
        residue.mkdir(mode=0o700)
        assert snapshot.is_dir()
    terminal = binding.ledger_root / "attempts/000001/terminal.json"
    terminal_before = terminal.read_bytes()
    with pytest.raises(implementation.RehearsalV22Error):
        implementation._validate_second_copy_history(binding, history)
    with pytest.raises(implementation.RehearsalV22Error):
        implementation._validate_continuation_mirror_state(
            binding,
            history,
            permit_unmirrored_final_incomplete=False,
        )
    with pytest.raises(implementation.RehearsalV22Error):
        implementation._build_bundle(
            binding=binding,
            history=history,
            run_a=None,
            run_b=None,
            control=None,
        )
    assert terminal.read_bytes() == terminal_before
    assert not (binding.ledger_root / "attempts/000002").exists()


def test_first_incomplete_empty_mirror_roots_are_permanent_residue_not_completion(
    tmp_path: Path,
    series_2_epoch_five_source: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label="first-incomplete-residue")
    _initialize_synthetic_ledger(binding)
    epoch = _clone_series_2_epoch_five_source(series_2_epoch_five_source, binding)
    _write_synthetic_started_record(
        binding,
        implementation_commit=epoch[0],
        owner_surface_authorization=epoch[1],
        independent_review=epoch[2],
    )
    history = implementation.validate_live_history(binding)
    assert implementation._read_only_storage_preflight(binding)["storage_state"] == (
        "EXISTING_FINAL_INCOMPLETE_PENDING_LOCKED_MIRROR"
    )
    for root in (
        binding.secondary_snapshot_root,
        binding.primary_receipt_root,
        binding.secondary_receipt_root,
    ):
        root.mkdir(mode=0o700)
    with pytest.raises(implementation.RehearsalV22Error, match=r"residue"):
        implementation._validate_continuation_mirror_state(
            binding,
            history,
            permit_unmirrored_final_incomplete=True,
        )
    assert not (binding.ledger_root / "attempts/000002").exists()


def test_later_incomplete_staging_claim_blocks_implicit_mirror_retry(
    tmp_path: Path,
    series_2_epoch_five_source: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label="later-incomplete-residue")
    _initialize_synthetic_ledger(binding)
    epoch = _clone_series_2_epoch_five_source(series_2_epoch_five_source, binding)
    first = _append_synthetic_terminal_record(binding, outcome="FAILED", epoch=epoch)
    _install_synthetic_mirror(binding, first)
    _write_synthetic_started_record(
        binding,
        implementation_commit=epoch[0],
        owner_surface_authorization=epoch[1],
        independent_review=epoch[2],
    )
    history = implementation.validate_live_history(binding)
    attempts_root = binding.ledger_root / "attempts"
    for invalid_name in ("999999", "not-an-ordinal"):
        invalid = attempts_root / invalid_name
        invalid.mkdir(mode=0o700)
        with pytest.raises(
            implementation.RehearsalV22Error,
            match=r"ordinal|gap|extra|noncanonical",
        ):
            implementation._strict_primary_prefix_inventory(
                binding.ledger_root,
                1,
                expected_attempt_count=2,
                label="prefix adversarial member",
            )
        invalid.rmdir()
    second = attempts_root / "000002"
    gap = attempts_root / "000003"
    second.rename(gap)
    try:
        with pytest.raises(
            implementation.RehearsalV22Error,
            match=r"ordinal|gap|extra|noncanonical",
        ):
            implementation._strict_primary_prefix_inventory(
                binding.ledger_root,
                1,
                expected_attempt_count=2,
                label="prefix ordinal gap",
            )
    finally:
        gap.rename(second)
    live_root = history.live_ledger_root_sha256
    assert isinstance(live_root, str)
    staging = binding.secondary_snapshot_root / (
        ".staging-" + implementation._mirror_snapshot_name(2, live_root) + "-precopy-failure"
    )
    staging.mkdir(mode=0o700)
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"staging|extra|snapshot count",
    ):
        implementation._validate_continuation_mirror_state(
            binding,
            history,
            permit_unmirrored_final_incomplete=True,
        )
    assert staging.is_dir()
    assert not (binding.ledger_root / "attempts/000003").exists()


def test_active_started_lease_cannot_forge_a_mirror_commit_capability(
    tmp_path: Path,
    series_2_epoch_five_source: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    binding = _synthetic_binding(tmp_path, label="mirror-trigger-forgery")
    _initialize_synthetic_ledger(binding)
    epoch = _clone_series_2_epoch_five_source(series_2_epoch_five_source, binding)
    _write_synthetic_started_record(
        binding,
        implementation_commit=epoch[0],
        owner_surface_authorization=epoch[1],
        independent_review=epoch[2],
    )
    history = implementation.validate_live_history(binding)
    ledger = implementation.SeriesLedger(binding=binding, execution_context=None)
    descriptor = os.open(binding.ledger_root / ".series.lock", os.O_RDONLY)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    ledger.lock_descriptor = descriptor
    ledger.locked = True
    forged = implementation._MirrorCommitCapability(
        _nonce=object(),
        ledger_id=id(ledger),
        ordinal=1,
        history_root_sha256=history.history_root_sha256,
        reason="TERMINAL_SEAL",
    )
    try:
        with pytest.raises(implementation.RehearsalV22Error, match=r"one-use|trigger"):
            implementation._mirror_live_ledger(
                ledger,
                history,
                mirror_commit_capability=forged,
            )
        live_root = history.live_ledger_root_sha256
        assert isinstance(live_root, str)
        snapshot = binding.secondary_snapshot_root / implementation._mirror_snapshot_name(
            1,
            live_root,
        )
        staging = binding.secondary_snapshot_root / f".staging-{snapshot.name}-forged"
        receipt_name = implementation._mirror_receipt_filename(1, live_root)
        with pytest.raises(implementation.RehearsalV22Error, match=r"commit trigger"):
            implementation._mirror_write_sequence(
                ledger,
                history,
                mirror_commit_capability=forged,
                staging=staging,
                snapshot=snapshot,
                receipt_paths=(
                    binding.primary_receipt_root / receipt_name,
                    binding.secondary_receipt_root / receipt_name,
                ),
                initialize_roots=True,
            ).__enter__()
        stolen_publish = inspect.getclosurevars(
            implementation._mirror_after_terminal_seal
        ).nonlocals["publish"]
        with pytest.raises(implementation.RehearsalV22Error, match=r"terminal|trigger"):
            stolen_publish(ledger, history, reason="TERMINAL_SEAL")
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    assert not any(
        os.path.lexists(path)
        for path in (
            binding.secondary_snapshot_root,
            binding.primary_receipt_root,
            binding.secondary_receipt_root,
        )
    )


def test_series_2_has_no_void_recovery_or_completion_entrypoint() -> None:
    source = (PROJECT_ROOT / IMPLEMENTATION_RELATIVE).read_text()
    for forbidden in (
        "--recover-sealed-bundle",
        "--consume-recovered-release",
        "bundle-recovery-authorization-v1",
        "void_epoch_3",
    ):
        assert forbidden not in source
    assert "_mirror_before_next_allocation" in source
    assert source.count("_mirror_live_ledger(") == 2


def test_read_only_preflight_rechecks_storage_identity_and_full_state_at_exit() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    source = inspect.getsource(implementation._read_only_implementation_preflight)
    assert source.count("_read_only_storage_preflight(preflight_binding)") == 2
    storage_source = inspect.getsource(implementation._storage_directory_evidence)
    assert '"device": metadata.st_dev' in storage_source
    assert '"inode": metadata.st_ino' in storage_source
    fingerprints = inspect.getsource(implementation._real_path_fingerprints)
    assert "registered_v2_2_primary_container" in fingerprints
    assert "registered_v2_2_secondary_container" in fingerprints


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
    rehearsal = copy.deepcopy(_json_pointer(source, projection["rehearsal_contract_source"]))
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

    implementation_row = next(row for row in lineage if row["path"] == "IMPLEMENTATION_COMMIT")
    assert implementation_row == {
        "path": "IMPLEMENTATION_COMMIT",
        "sha256": None,
        "creating_commit": V2_1_IMPLEMENTATION_COMMIT,
        "parent_commit": V2_1_IMPLEMENTATION_PARENT,
        "tree_and_exact_surface_must_be_rederived": True,
    }
    parents = (
        implementation._git_bytes(
            binding.project_root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            V2_1_IMPLEMENTATION_COMMIT,
            "--",
        )
        .decode("ascii")
        .split()
    )
    assert parents == [V2_1_IMPLEMENTATION_COMMIT, V2_1_IMPLEMENTATION_PARENT]
    observed = (
        implementation._git_bytes(
            binding.project_root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            V2_1_IMPLEMENTATION_PARENT,
            V2_1_IMPLEMENTATION_COMMIT,
            "--",
        )
        .decode("utf-8")
        .splitlines()
    )
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
    head = _git("rev-parse", "HEAD").decode().strip()
    review_is_on_execution_lineage = INDEPENDENT_REVIEW_COMMIT in set(
        _git("rev-list", head).decode().splitlines()
    )
    review_path = PROJECT_ROOT / INDEPENDENT_REVIEW_RELATIVE
    if review_is_on_execution_lineage:
        assert review_path.is_file()
        assert _sha256(review_path.read_bytes()) == INDEPENDENT_REVIEW_SHA256
    else:
        assert not review_path.exists()


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
    parents = _git("rev-list", "--parents", "-n", "1", head).decode().split()
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
        assert parents == [head, PREREGISTRATION_COMMIT]
        surface = (
            _git("diff-tree", "--no-commit-id", "--name-status", "-r", head).decode().splitlines()
        )
        assert surface == [f"A\t{path.as_posix()}" for path in git_order]
    elif parents == [head, INITIAL_IMPLEMENTATION_COMMIT]:
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
    elif head == EPOCH_TWO_SURFACE_AUTHORITY_COMMIT:
        status = (
            _git(
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            )
            .decode()
            .splitlines()
        )
        assert [line[3:] for line in status] == [
            path.as_posix()
            for path in sorted(
                (IMPLEMENTATION_RELATIVE, RUNNER_TEST_RELATIVE),
                key=lambda path: os.fsencode(path.as_posix()),
            )
        ]
        assert all(line[:2] in {" M", "M "} for line in status)
    elif parents == [head, EPOCH_TWO_SURFACE_AUTHORITY_COMMIT]:
        assert parents == [head, EPOCH_TWO_SURFACE_AUTHORITY_COMMIT]
        epoch_two_surface = (
            _git("diff-tree", "--no-commit-id", "--name-status", "-r", head).decode().splitlines()
        )
        assert epoch_two_surface == [
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
    elif head == EPOCH_THREE_SURFACE_AUTHORITY_COMMIT:
        status = _git("status", "--porcelain=v1", "--untracked-files=all").decode().splitlines()
        epoch_three_paths = sorted(
            (
                IMPLEMENTATION_RELATIVE,
                VALIDATOR_RELATIVE,
                RUNNER_TEST_RELATIVE,
                VALIDATOR_TEST_RELATIVE,
            ),
            key=lambda path: os.fsencode(path.as_posix()),
        )
        assert [line[3:] for line in status] == [path.as_posix() for path in epoch_three_paths]
        assert all(line[:2] in {" M", "M "} for line in status)
    elif parents == [head, EPOCH_THREE_SURFACE_AUTHORITY_COMMIT]:
        assert parents == [head, EPOCH_THREE_SURFACE_AUTHORITY_COMMIT]
        epoch_three_surface = (
            _git("diff-tree", "--no-commit-id", "--name-status", "-r", head).decode().splitlines()
        )
        assert epoch_three_surface == [
            f"M\t{path.as_posix()}"
            for path in sorted(
                (
                    IMPLEMENTATION_RELATIVE,
                    VALIDATOR_RELATIVE,
                    RUNNER_TEST_RELATIVE,
                    VALIDATOR_TEST_RELATIVE,
                ),
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
    elif head == EPOCH_FOUR_SURFACE_AUTHORITY_COMMIT:
        epoch_four_paths = sorted(
            (IMPLEMENTATION_RELATIVE, RUNNER_TEST_RELATIVE),
            key=lambda path: os.fsencode(path.as_posix()),
        )
        status = (
            _git(
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            )
            .decode()
            .splitlines()
        )
        assert [line[3:] for line in status] == [path.as_posix() for path in epoch_four_paths]
        assert all(line[:2] in {" M", "M "} for line in status)
    elif parents == [head, EPOCH_FOUR_SURFACE_AUTHORITY_COMMIT]:
        assert parents == [head, EPOCH_FOUR_SURFACE_AUTHORITY_COMMIT]
        epoch_four_surface = (
            _git("diff-tree", "--no-commit-id", "--name-status", "-r", head).decode().splitlines()
        )
        assert epoch_four_surface == [
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
    elif head == SERIES_2_EPOCH_FIVE_SURFACE_AUTHORITY_COMMIT:
        assert parents == [head, SERIES_2_EPOCH_FIVE_COMPANION_COMMIT]
        epoch_five_paths = sorted(
            (
                IMPLEMENTATION_RELATIVE,
                VALIDATOR_RELATIVE,
                RUNNER_TEST_RELATIVE,
                VALIDATOR_TEST_RELATIVE,
            ),
            key=lambda path: os.fsencode(path.as_posix()),
        )
        status = _git("status", "--porcelain=v1", "--untracked-files=all").decode().splitlines()
        assert [line[3:] for line in status] == [path.as_posix() for path in epoch_five_paths]
        assert all(line[:2] in {" M", "M "} for line in status)
    elif parents == [head, SERIES_2_EPOCH_FIVE_SURFACE_AUTHORITY_COMMIT]:
        epoch_five_surface = (
            _git("diff-tree", "--no-commit-id", "--name-status", "-r", head).decode().splitlines()
        )
        assert epoch_five_surface == [
            f"M\t{path.as_posix()}"
            for path in sorted(
                (
                    IMPLEMENTATION_RELATIVE,
                    VALIDATOR_RELATIVE,
                    RUNNER_TEST_RELATIVE,
                    VALIDATOR_TEST_RELATIVE,
                ),
                key=lambda path: os.fsencode(path.as_posix()),
            )
        ]
    elif head == SERIES_2_EPOCH_SIX_SURFACE_AUTHORITY_COMMIT:
        assert parents == [head, SERIES_2_EPOCH_SIX_COMPANION_COMMIT]
        epoch_six_paths = sorted(
            (IMPLEMENTATION_RELATIVE, RUNNER_TEST_RELATIVE),
            key=lambda path: os.fsencode(path.as_posix()),
        )
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
        assert [line[3:] for line in status] == [path.as_posix() for path in epoch_six_paths]
        assert all(line[:2] in {" M", "M "} for line in status)
    elif parents == [head, SERIES_2_EPOCH_SIX_SURFACE_AUTHORITY_COMMIT]:
        epoch_six_surface = (
            _git("diff-tree", "--no-commit-id", "--name-status", "-r", head).decode().splitlines()
        )
        assert epoch_six_surface == [
            f"M\t{path.as_posix()}"
            for path in sorted(
                (IMPLEMENTATION_RELATIVE, RUNNER_TEST_RELATIVE),
                key=lambda path: os.fsencode(path.as_posix()),
            )
        ]
    else:
        pytest.fail(f"unregistered implementation topology at HEAD {head}")


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
        {"path": IMPLEMENTATION_RELATIVE.as_posix(), "status": "M"},
        {"path": RUNNER_TEST_RELATIVE.as_posix(), "status": "M"},
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
    substituted_root = tmp_path / "authority-index-substitution"
    substituted_root.mkdir(mode=0o700)
    substituted_binding = _synthetic_binding(
        substituted_root,
        label="epoch-two-authority-index-substitution",
    )
    (
        substituted_implementation,
        substituted_owner,
        substituted_review,
        substituted_head,
    ) = _initialize_synthetic_epoch_two(
        substituted_binding,
        authority_epoch=3,
    )
    substituted_document = json.loads(
        (substituted_binding.project_root / substituted_owner.path).read_bytes()
    )
    assert substituted_document["implementation_epoch"] == 3
    assert (
        _sha256((substituted_binding.project_root / substituted_owner.path).read_bytes())
        == substituted_owner.sha256
    )
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"epoch|surface",
    ):
        implementation.validate_implementation_epoch(
            substituted_binding.project_root,
            epoch=2,
            implementation_commit=substituted_implementation,
            owner_surface_authorization=substituted_owner,
            independent_review=substituted_review,
            control_merkle_root_sha256="7" * 64,
            execution_head=substituted_head,
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


def _assert_runner_import_roots_are_immutable(
    tree: ast.AST,
    *,
    expected_attribute_writes: dict[tuple[str, str, str, str], int] | None = None,
) -> None:
    imported_names: set[str] = set()
    dynamic_import_callables = {"__import__"}
    dynamic_code_callables = {"CodeType", "FunctionType", "compile", "eval", "exec"}
    partial_callables: set[str] = set()
    setter_callables = {
        "__delattr__",
        "__delitem__",
        "__setattr__",
        "__setitem__",
        "delattr",
        "delitem",
        "setattr",
        "setitem",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(
                alias.asname or alias.name.split(".", 1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name != "*"
                observed_name = alias.asname or alias.name
                imported_names.add(observed_name)
                if node.module == "functools" and alias.name in {
                    "partial",
                    "partialmethod",
                }:
                    partial_callables.add(observed_name)
                if alias.name in dynamic_code_callables:
                    dynamic_code_callables.add(observed_name)
                if alias.name in setter_callables:
                    setter_callables.add(observed_name)

    tainted_names = set(imported_names)

    def rooted_name(node: ast.AST) -> str | None:
        cursor = node
        while isinstance(cursor, (ast.Attribute, ast.Subscript)):
            cursor = cursor.value
        if isinstance(cursor, ast.Call):
            if (
                isinstance(cursor.func, ast.Name)
                and cursor.func.id in {"getattr", "vars"}
                and cursor.args
            ):
                return rooted_name(cursor.args[0])
            if (
                isinstance(cursor.func, ast.Attribute)
                and cursor.func.attr == "__getattribute__"
                and cursor.args
            ):
                return rooted_name(cursor.args[0])
            if isinstance(cursor.func, ast.Name) and cursor.func.id in {"globals", "locals"}:
                return "__dynamic_namespace__"
        return cursor.id if isinstance(cursor, ast.Name) else None

    tainted_names.update({"__builtins__", "__dynamic_namespace__"})

    def selected_attribute(node: ast.AST) -> str | None:
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Subscript):
            selected = node.slice
            if isinstance(selected, ast.Constant) and isinstance(selected.value, str):
                return selected.value
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            return node.args[1].value
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "__getattribute__"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            return node.args[1].value
        return None

    def is_dynamic_import_callable(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in dynamic_import_callables
        selected = selected_attribute(node)
        return selected in {"__import__", "import_module"} and (rooted_name(node) in tainted_names)

    def is_dynamic_code_callable(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in dynamic_code_callables
        return selected_attribute(node) in {
            "CodeType",
            "FunctionType",
            "compile",
            "eval",
            "exec",
        }

    def is_partial_callable(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in partial_callables
        return selected_attribute(node) in {"partial", "partialmethod"} and (
            rooted_name(node) in tainted_names
        )

    def is_setter_callable(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in setter_callables
        return selected_attribute(node) in {
            "__delattr__",
            "__delitem__",
            "__setattr__",
            "__setitem__",
            "delattr",
            "delitem",
            "setattr",
            "setitem",
        }

    def expression_is_tainted(node: ast.AST) -> bool:
        root = rooted_name(node)
        if root in tainted_names:
            return True
        if isinstance(node, ast.Call):
            if is_dynamic_import_callable(node.func):
                return True
            if isinstance(node.func, ast.Name) and node.func.id == "type" and node.args:
                return expression_is_tainted(node.args[0])
            selected = selected_attribute(node)
            if selected is not None and selected in {
                "__dict__",
                "__getattribute__",
                "__import__",
                "import_module",
            }:
                return root in tainted_names
            return False
        if isinstance(node, ast.NamedExpr):
            return expression_is_tainted(node.value)
        return False

    def bound_names(node: ast.AST) -> set[str]:
        if isinstance(node, ast.Name):
            return {node.id}
        if isinstance(node, (ast.List, ast.Tuple)):
            return set().union(*(bound_names(element) for element in node.elts))
        return set()

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = node.value
                names = set().union(*(bound_names(target) for target in node.targets))
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
                if node.value is None:
                    continue
                value = node.value
                names = bound_names(node.target)
            else:
                continue
            if is_dynamic_import_callable(value):
                previous = len(dynamic_import_callables)
                dynamic_import_callables.update(names)
                changed = changed or len(dynamic_import_callables) != previous
            if is_dynamic_code_callable(value):
                previous = len(dynamic_code_callables)
                dynamic_code_callables.update(names)
                changed = changed or len(dynamic_code_callables) != previous
            if is_partial_callable(value):
                previous = len(partial_callables)
                partial_callables.update(names)
                changed = changed or len(partial_callables) != previous
            if is_setter_callable(value):
                previous = len(setter_callables)
                setter_callables.update(names)
                changed = changed or len(setter_callables) != previous
            if expression_is_tainted(value):
                previous = len(tainted_names)
                tainted_names.update(names)
                changed = changed or len(tainted_names) != previous

    assert isinstance(tree, ast.Module)
    local_functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    local_function_aliases = {name: name for name in local_functions}
    changed = True
    while changed:
        changed = False
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Name) or value.id not in local_function_aliases:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for name in set().union(*(bound_names(target) for target in targets)):
                if name not in local_function_aliases:
                    local_function_aliases[name] = local_function_aliases[value.id]
                    changed = True

    def scoped_nodes(statements: list[ast.stmt]) -> list[ast.AST]:
        observed: list[ast.AST] = []
        pending: list[ast.AST] = list(reversed(statements))
        while pending:
            node = pending.pop()
            observed.append(node)
            if isinstance(
                node,
                (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef, ast.Lambda),
            ):
                continue
            pending.extend(reversed(list(ast.iter_child_nodes(node))))
        return observed

    module_scope_nodes = scoped_nodes(
        [
            node
            for node in tree.body
            if not isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef))
        ]
    )
    function_scope_nodes = {
        name: scoped_nodes(function.body) for name, function in local_functions.items()
    }
    scoped_tainted_names = {name: set() for name in local_functions}

    def expression_is_tainted_in_scope(node: ast.AST, scope_name: str | None) -> bool:
        extra_names = scoped_tainted_names.get(scope_name, set())
        root = rooted_name(node)
        if root in extra_names:
            return True
        if isinstance(node, ast.Call):
            if is_dynamic_import_callable(node.func):
                return True
            if isinstance(node.func, ast.Name) and node.func.id == "type" and node.args:
                return expression_is_tainted_in_scope(node.args[0], scope_name)
            selected = selected_attribute(node)
            if selected in {
                "__dict__",
                "__getattribute__",
                "__import__",
                "import_module",
            }:
                return root in (tainted_names | extra_names)
        if isinstance(node, ast.NamedExpr):
            return expression_is_tainted_in_scope(node.value, scope_name)
        return expression_is_tainted(node)

    def function_parameters(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> tuple[list[ast.arg], ast.arg | None]:
        return (
            [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs],
            function.args.vararg,
        )

    def propagate_call_arguments(
        call: ast.Call,
        *,
        caller_scope: str | None,
    ) -> bool:
        if not isinstance(call.func, ast.Name):
            return False
        callee_name = local_function_aliases.get(call.func.id)
        if callee_name is None:
            return False
        function = local_functions[callee_name]
        parameters, vararg = function_parameters(function)
        positional_parameters = [*function.args.posonlyargs, *function.args.args]
        keyword_parameters = {parameter.arg: parameter for parameter in parameters}
        observed_change = False
        for index, argument in enumerate(call.args):
            assert not is_dynamic_code_callable(argument)
            assert not is_setter_callable(argument)
            assert not is_partial_callable(argument)
            if index < len(positional_parameters):
                parameter = positional_parameters[index]
            elif vararg is not None:
                parameter = vararg
            else:
                continue
            if (
                expression_is_tainted_in_scope(argument, caller_scope)
                and parameter.arg not in scoped_tainted_names[callee_name]
            ):
                scoped_tainted_names[callee_name].add(parameter.arg)
                observed_change = True
        for keyword in call.keywords:
            assert not is_dynamic_code_callable(keyword.value)
            assert not is_setter_callable(keyword.value)
            assert not is_partial_callable(keyword.value)
            parameter = keyword_parameters.get(keyword.arg or "")
            if parameter is None:
                continue
            if (
                expression_is_tainted_in_scope(keyword.value, caller_scope)
                and parameter.arg not in scoped_tainted_names[callee_name]
            ):
                scoped_tainted_names[callee_name].add(parameter.arg)
                observed_change = True
        return observed_change

    changed = True
    while changed:
        changed = False
        for scope_name, nodes in function_scope_nodes.items():
            for node in nodes:
                if isinstance(node, ast.Assign):
                    value = node.value
                    targets = node.targets
                elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
                    if node.value is None:
                        continue
                    value = node.value
                    targets = [node.target]
                else:
                    continue
                if expression_is_tainted_in_scope(value, scope_name):
                    for name in set().union(*(bound_names(target) for target in targets)):
                        if name not in scoped_tainted_names[scope_name]:
                            scoped_tainted_names[scope_name].add(name)
                            changed = True
        for node in module_scope_nodes:
            if isinstance(node, ast.Call):
                changed = propagate_call_arguments(node, caller_scope=None) or changed
        for scope_name, nodes in function_scope_nodes.items():
            for node in nodes:
                if isinstance(node, ast.Call):
                    changed = propagate_call_arguments(node, caller_scope=scope_name) or changed

    expected_attribute_writes = expected_attribute_writes or {}
    observed_attribute_writes: dict[tuple[str, str, str, str], int] = {}

    def collect_attribute_writes(node: ast.AST, function_name: str) -> None:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            for statement in node.body:
                collect_attribute_writes(statement, node.name)
            return
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Del, ast.Store)):
            key = (
                function_name,
                ast.unparse(node.value),
                node.attr,
                type(node.ctx).__name__,
            )
            observed_attribute_writes[key] = observed_attribute_writes.get(key, 0) + 1
        for child in ast.iter_child_nodes(node):
            collect_attribute_writes(child, function_name)

    collect_attribute_writes(tree, "<module>")
    assert observed_attribute_writes == expected_attribute_writes

    for scope_name, nodes in function_scope_nodes.items():
        scoped_mutation_targets: list[ast.AST] = []
        for node in nodes:
            if isinstance(node, ast.Assign):
                scoped_mutation_targets.extend(node.targets)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                scoped_mutation_targets.append(node.target)
            elif isinstance(node, ast.Delete):
                scoped_mutation_targets.extend(node.targets)
        assert not any(
            isinstance(target, (ast.Attribute, ast.Subscript))
            and expression_is_tainted_in_scope(target, scope_name)
            for target in scoped_mutation_targets
        )

    mutation_targets: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            mutation_targets.extend(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            mutation_targets.append(node.target)
        elif isinstance(node, ast.Delete):
            mutation_targets.extend(node.targets)
    tainted_mutation_targets = [
        ast.unparse(target)
        for target in mutation_targets
        if isinstance(target, (ast.Attribute, ast.Subscript)) and expression_is_tainted(target)
    ]
    assert tainted_mutation_targets == [], tainted_mutation_targets
    assert not any(
        isinstance(target, ast.Attribute) and target.attr in {"__code__", "__globals__"}
        for target in mutation_targets
    )
    forbidden_subscript_write_roots = [
        ast.unparse(target)
        for target in mutation_targets
        if isinstance(target, ast.Subscript)
        and (
            (
                isinstance(target.value, ast.Call)
                and isinstance(target.value.func, ast.Name)
                and target.value.func.id in {"globals", "locals", "vars"}
            )
            or selected_attribute(target.value) == "__dict__"
        )
    ]
    assert forbidden_subscript_write_roots == []

    mutating_attributes = {
        "__delattr__",
        "__delitem__",
        "__setattr__",
        "__setitem__",
        "clear",
        "delattr",
        "patch",
        "pop",
        "popitem",
        "putenv",
        "setattr",
        "setdefault",
        "unsetenv",
        "update",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        assert not is_dynamic_code_callable(node.func)
        assert not is_partial_callable(node.func)
        assert not is_setter_callable(node.func)
        assert not any(
            is_dynamic_code_callable(argument)
            or is_partial_callable(argument)
            or is_setter_callable(argument)
            for argument in node.args
        )
        assert not any(
            is_dynamic_code_callable(keyword.value)
            or is_partial_callable(keyword.value)
            or is_setter_callable(keyword.value)
            for keyword in node.keywords
        )
        if isinstance(node.func, ast.Attribute) and node.func.attr in mutating_attributes:
            assert not expression_is_tainted(node.func.value)
        selected = selected_attribute(node.func)
        if selected in mutating_attributes:
            assert not expression_is_tainted(node.func)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Lambda):
            continue
        assert not any(
            isinstance(candidate, ast.Call)
            and (
                is_dynamic_code_callable(candidate.func)
                or is_partial_callable(candidate.func)
                or is_setter_callable(candidate.func)
                or (
                    isinstance(candidate.func, ast.Attribute)
                    and candidate.func.attr in mutating_attributes
                )
            )
            for candidate in ast.walk(node.body)
        )


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
        if relative == RUNNER_TEST_RELATIVE:
            _assert_runner_import_roots_are_immutable(
                tree,
                expected_attribute_writes={
                    (
                        "_wait_for_disposable_started_checkpoint",
                        "process",
                        "returncode",
                        "Store",
                    ): 2,
                    (
                        "_wait_for_disposable_candidate_checkpoint",
                        "process",
                        "returncode",
                        "Store",
                    ): 2,
                    (
                        "test_active_started_lease_cannot_forge_a_mirror_commit_capability",
                        "ledger",
                        "lock_descriptor",
                        "Store",
                    ): 1,
                    (
                        "test_active_started_lease_cannot_forge_a_mirror_commit_capability",
                        "ledger",
                        "locked",
                        "Store",
                    ): 1,
                    (
                        "test_validator_import_guard_rejects_forged_or_repeated_finalization",
                        "forged",
                        "__file__",
                        "Store",
                    ): 1,
                },
            )
            forbidden_fixture_names = {
                "monkeypatch",
                "mocker",
                "patcher",
                "mock_patch",
            }
            forbidden_fixture_types = {"MonkeyPatch"}
            forbidden_callable_aliases = {
                "MonkeyPatch",
                "delattr",
                "patch",
                "setattr",
            }
            protected_module_names = {
                "builtins",
                "ctypes",
                "fcntl",
                "gc",
                "importlib",
                "os",
                "signal",
                "subprocess",
                "sys",
                "time",
            }
            tainted_names = set(protected_module_names)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name not in {"mock", "unittest.mock"}
                        if alias.name in protected_module_names:
                            tainted_names.add(alias.asname or alias.name)
                elif isinstance(node, ast.ImportFrom):
                    assert node.module not in {"mock", "unittest", "unittest.mock"}
                    if node.module == "pytest":
                        for alias in node.names:
                            if alias.name == "MonkeyPatch":
                                observed_alias = alias.asname or alias.name
                                forbidden_fixture_types.add(observed_alias)
                                forbidden_callable_aliases.add(observed_alias)
                    if node.module in protected_module_names:
                        tainted_names.update(alias.asname or alias.name for alias in node.names)

            def rooted_name(node: ast.AST) -> str | None:
                cursor = node
                while isinstance(cursor, (ast.Attribute, ast.Subscript)):
                    cursor = cursor.value
                if isinstance(cursor, ast.Call):
                    if (
                        isinstance(cursor.func, ast.Name)
                        and cursor.func.id in {"getattr", "vars"}
                        and cursor.args
                    ):
                        return rooted_name(cursor.args[0])
                    if (
                        isinstance(cursor.func, ast.Attribute)
                        and cursor.func.attr == "__getattribute__"
                        and cursor.args
                    ):
                        return rooted_name(cursor.args[0])
                    if isinstance(cursor.func, ast.Name) and cursor.func.id in {
                        "globals",
                        "locals",
                    }:
                        return "__dynamic_namespace__"
                return cursor.id if isinstance(cursor, ast.Name) else None

            tainted_names.add("__dynamic_namespace__")
            changed = True
            while changed:
                changed = False
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                        continue
                    value = node.value
                    if value is None:
                        continue
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    names = [target.id for target in targets if isinstance(target, ast.Name)]
                    if (
                        isinstance(value, ast.Call)
                        and ast.unparse(value.func) == "importlib.import_module"
                        and value.args
                        and ast.unparse(value.args[0])
                        in {"IMPLEMENTATION_MODULE", "VALIDATOR_MODULE"}
                    ) or rooted_name(value) in tainted_names:
                        for name in names:
                            if name not in tainted_names:
                                tainted_names.add(name)
                                changed = True
                    if (isinstance(value, ast.Name) and value.id in forbidden_callable_aliases) or (
                        isinstance(value, ast.Attribute)
                        and value.attr in {"MonkeyPatch", "delattr", "patch", "setattr"}
                    ):
                        previous = len(forbidden_callable_aliases)
                        forbidden_callable_aliases.update(names)
                        changed = changed or len(forbidden_callable_aliases) != previous
                    if (isinstance(value, ast.Name) and value.id in forbidden_fixture_types) or (
                        isinstance(value, ast.Attribute) and value.attr == "MonkeyPatch"
                    ):
                        previous = len(forbidden_fixture_types)
                        forbidden_fixture_types.update(names)
                        changed = changed or len(forbidden_fixture_types) != previous

            functions = [
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            registered_runner_fixtures = {
                "exact_failed_epoch_then_incomplete_success_series",
                "exact_two_failures_then_success_series",
                "series_2_epoch_five_source",
                "tmp_path",
                "tmp_path_factory",
                "v2_1_mint_prerequisite_source",
            }
            for function in functions:
                argument_names = {
                    argument.arg
                    for argument in (
                        *function.args.posonlyargs,
                        *function.args.args,
                        *function.args.kwonlyargs,
                    )
                }
                for argument in (
                    *function.args.posonlyargs,
                    *function.args.args,
                    *function.args.kwonlyargs,
                ):
                    assert argument.arg not in forbidden_fixture_names
                    if argument.annotation is not None:
                        annotation_names = {
                            candidate.id
                            for candidate in ast.walk(argument.annotation)
                            if isinstance(candidate, ast.Name)
                        }
                        assert annotation_names.isdisjoint(forbidden_fixture_types)
                if function.name.startswith("test_"):
                    parametrized_names: set[str] = set()
                    for decorator in function.decorator_list:
                        if not (
                            isinstance(decorator, ast.Call)
                            and isinstance(decorator.func, ast.Attribute)
                            and decorator.func.attr == "parametrize"
                            and decorator.args
                        ):
                            continue
                        declared = decorator.args[0]
                        if isinstance(declared, ast.Constant) and isinstance(declared.value, str):
                            parametrized_names.update(
                                name.strip() for name in declared.value.split(",")
                            )
                        elif isinstance(declared, (ast.List, ast.Tuple)):
                            for element in declared.elts:
                                assert isinstance(element, ast.Constant)
                                assert isinstance(element.value, str)
                                parametrized_names.add(element.value)
                    assert argument_names <= (registered_runner_fixtures | parametrized_names)
                elif any(
                    ast.unparse(decorator).startswith("pytest.fixture")
                    for decorator in function.decorator_list
                ):
                    assert function.name in registered_runner_fixtures
                    assert argument_names <= {"tmp_path_factory"}
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    called = ast.unparse(node.func)
                    assert called not in {
                        "pytest.MonkeyPatch",
                        "unittest.mock.patch",
                        "unittest.mock.patch.object",
                    }
                    assert not (
                        isinstance(node.func, ast.Name) and node.func.id in forbidden_fixture_names
                    )
                    assert not (
                        isinstance(node.func, ast.Name) and node.func.id in forbidden_fixture_types
                    )
                    assert not (
                        isinstance(node.func, ast.Name)
                        and node.func.id == "getattr"
                        and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)
                        and node.args[1].value in {"MonkeyPatch", "patch"}
                    )
                    assert not (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr == "getfixturevalue"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value in forbidden_fixture_names
                    )

            mutation_targets: list[ast.AST] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    mutation_targets.extend(node.targets)
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                    mutation_targets.append(node.target)
                elif isinstance(node, ast.Delete):
                    mutation_targets.extend(node.targets)
            assert not any(
                isinstance(target, (ast.Attribute, ast.Subscript))
                and rooted_name(target) in tainted_names
                for target in mutation_targets
            )
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    assert node.func.id not in forbidden_callable_aliases
                if isinstance(node.func, ast.Attribute) and node.func.attr in {
                    "setattr",
                    "delattr",
                    "__setattr__",
                    "__delattr__",
                    "update",
                    "clear",
                    "pop",
                    "popitem",
                    "setdefault",
                    "__setitem__",
                    "__delitem__",
                    "putenv",
                    "unsetenv",
                }:
                    assert rooted_name(node.func.value) not in tainted_names
                if (
                    (isinstance(node.func, ast.Name) and node.func.id in {"setattr", "delattr"})
                    or (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"__setattr__", "__delattr__"}
                    )
                ) and node.args:
                    assert rooted_name(node.args[0]) not in tainted_names
                if (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "getattr"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value
                    in {
                        "__delattr__",
                        "__delitem__",
                        "__setattr__",
                        "__setitem__",
                        "clear",
                        "delattr",
                        "patch",
                        "pop",
                        "popitem",
                        "putenv",
                        "setattr",
                        "setdefault",
                        "unsetenv",
                        "update",
                    }
                    and node.args
                ):
                    assert rooted_name(node.args[0]) not in tainted_names

    hostile_sources = {
        "container_attribute_assignment": """
import hashlib
box = [hashlib]
box[0].sha256 = replacement
""",
        "dynamic_exec": """
exec("import hashlib; hashlib.sha256 = replacement")
""",
        "lambda_setter": """
import hashlib
apply_replacement = lambda replacement: setattr(hashlib, "sha256", replacement)
apply_replacement(replacement)
""",
        "local_parameter_taint": """
import hashlib

def mutate(target, replacement):
    target.sha256 = replacement

mutate(hashlib, replacement)
""",
        "partial_setter_capture": """
import functools
import hashlib
wrapped = functools.partial(setattr, hashlib, "sha256")
wrapped(replacement)
""",
        "returned_alias_attribute_assignment": """
import hashlib

def expose_hashlib():
    return hashlib

returned = expose_hashlib()
returned.sha256 = replacement
""",
        "direct_hashlib_assignment": """
import hashlib
hashlib.sha256 = replacement
""",
        "path_class_dunder_assignment": """
from pathlib import Path as RegisteredPath
type.__setattr__(RegisteredPath, "read_bytes", replacement)
""",
        "path_class_dict_assignment": """
from pathlib import Path
Path.__dict__["read_bytes"] = replacement
""",
        "dynamic_import_setattr": """
import importlib as loader_module
load = loader_module.import_module
loaded_hashlib = load("hashlib")
setattr(loaded_hashlib, "sha256", replacement)
""",
        "builtin_import_vars_assignment": """
load = __import__
loaded_hashlib = load("hashlib")
vars(loaded_hashlib)["sha256"] = replacement
""",
    }
    for label, source in hostile_sources.items():
        with pytest.raises(AssertionError):
            _assert_runner_import_roots_are_immutable(
                ast.parse(source, filename=f"hostile-{label}.py")
            )
    _assert_runner_import_roots_are_immutable(
        ast.parse(
            """
import hashlib
from dataclasses import dataclass
from pathlib import Path

@dataclass
class LocalRecord:
    value: str

def legitimate_fixture_write(tmp_path):
    record = LocalRecord(hashlib.sha256(b"payload").hexdigest())
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(record.value.upper().encode())
    return Path(artifact).read_bytes()
""",
            filename="normal-runner-local-state.py",
        )
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


def test_implementation_is_the_sole_process_audit_hook_owner_and_validator_imports_it_first() -> (
    None
):
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
        assert {alias.name for alias in node.names} <= {"os", "sys"}, (
            "validator imported before the sole audit hook owner"
        )

    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    validator = importlib.import_module(VALIDATOR_MODULE)
    assert validator._implementation_module is implementation
    assert (
        sum(1 for relative in REGISTERED_SURFACE for _call in audit_hook_calls(trees[relative]))
        == 1
    )
    for forbidden_event in (
        "sys.addaudithook",
        "sys.settrace",
        "sys.setprofile",
        "sys.monitoring.register_callback",
        "sys.monitoring.set_events",
        "sys.monitoring.use_tool_id",
    ):
        with pytest.raises(
            implementation.RehearsalV22Error,
            match=r"runtime callback",
        ):
            sys.audit(forbidden_event)


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
    assert module_contextvars == {
        "_AUDIT_POLICY",
        "_TEMP_AUTHORITY",
        "_MIRROR_PHASE_CAPABILITY",
        "_NATIVE_RENAME_CAPABILITY",
        "_OPENAT_WRITE_CAPABILITY",
    }
    audit_policy = implementation._AUDIT_POLICY
    temp_authority = implementation._TEMP_AUTHORITY
    assert isinstance(audit_policy, ContextVar)
    assert isinstance(temp_authority, ContextVar)
    assert id(validator._implementation_module._AUDIT_POLICY) == id(audit_policy)
    assert id(validator._implementation_module._TEMP_AUTHORITY) == id(temp_authority)
    assert id(validator._implementation_module._MIRROR_PHASE_CAPABILITY) == id(
        implementation._MIRROR_PHASE_CAPABILITY
    )
    assert id(validator._implementation_module._NATIVE_RENAME_CAPABILITY) == id(
        implementation._NATIVE_RENAME_CAPABILITY
    )
    assert id(validator._implementation_module._OPENAT_WRITE_CAPABILITY) == id(
        implementation._OPENAT_WRITE_CAPABILITY
    )


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
    assert (
        len(
            {
                relative.as_posix().casefold()
                for relative, _sha256_value in implementation.V2_1_MINT_PREREQUISITE_CONTROLS
            }
        )
        == 5
    )
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


def test_retired_v2_1_claim_is_absent_from_the_series_2_registered_root() -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    assert implementation.V2_1_EMPTY_CLAIM == V2_1_CONSUMED_CLAIM
    assert V2_1_CONSUMED_CLAIM.name.endswith(V2_1_CONSUMED_CLAIM_TOKEN)
    assert not os.path.lexists(V2_1_CONSUMED_CLAIM)
    assert _tree_fingerprint(V2_1_CONSUMED_CLAIM) is None


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


def test_exact_os_read_only_preflight_validates_epoch_control_and_registered_bytes(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before_real = _all_real_path_fingerprints()
    binding = _synthetic_binding(tmp_path.resolve(), label="read-only-preflight")
    implementation_commit, owner_surface, independent_review, execution_head = (
        _initialize_synthetic_epoch_two(binding)
    )
    command = _exact_read_only_preflight_command(
        binding,
        implementation_epoch=2,
        implementation_commit=implementation_commit,
        owner_surface_authorization=owner_surface,
        independent_review=independent_review,
    )
    assert "--attempt-authorization" not in command
    before_project = _tree_fingerprint(binding.project_root)
    before_temp = tuple(sorted(binding.project_root.parent.glob(".alphapilot-p4-2a-v2-2-temp-*")))
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=_locked_environment(),
        timeout=3600,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    result = implementation.strict_json_loads(
        completed.stdout,
        source="read-only implementation preflight result",
    )
    assert implementation._canonical_json_bytes(result) == completed.stdout.encode("utf-8")
    assert set(result) == {
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
        "effect_summary",
    }
    assert result == {
        **result,
        "schema_version": "p4.2a-v2-2-read-only-implementation-preflight-v1",
        "status": "PASS_READ_ONLY_IMPLEMENTATION_PREFLIGHT",
        "mode": "NONREGISTERED_READ_ONLY_TEST",
        "execution_head": execution_head,
        "implementation_epoch": 2,
        "implementation_commit": implementation_commit,
        "owner_exact_surface_authorization": owner_surface.as_json(),
        "independent_implementation_review": independent_review.as_json(),
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
    storage = result["series_2_registered_storage"]
    assert set(storage) == {
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
    container_evidence: dict[str, dict[str, object]] = {}
    for evidence_key, registered_path in (
        ("primary_container", binding.primary_series_container),
        ("secondary_container", binding.secondary_series_container),
    ):
        metadata = registered_path.lstat()
        assert metadata.st_uid == os.getuid()
        container_evidence[evidence_key] = {
            "path": registered_path.as_posix(),
            "owner_uid": os.getuid(),
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "mode_octal": "0700",
            "non_symlink": True,
            "canonical_unaliased": True,
        }
    registered_leaf_paths = {
        "primary_ledger": binding.ledger_root,
        "primary_receipts": binding.primary_receipt_root,
        "secondary_snapshots": binding.secondary_snapshot_root,
        "secondary_receipts": binding.secondary_receipt_root,
    }
    assert registered_leaf_paths == {
        "primary_ledger": binding.primary_series_container / "PRIMARY-LEDGER-DO-NOT-DELETE",
        "primary_receipts": binding.primary_series_container / "MIRROR-RECEIPTS-DO-NOT-DELETE",
        "secondary_snapshots": binding.secondary_series_container
        / "SEALED-LEDGER-SNAPSHOTS-DO-NOT-DELETE",
        "secondary_receipts": binding.secondary_series_container / "MIRROR-RECEIPTS-DO-NOT-DELETE",
    }
    assert all(not os.path.lexists(path) for path in registered_leaf_paths.values())
    assert storage == {
        **container_evidence,
        "containers_non_overlapping": True,
        "storage_state": "FRESH_SERIES_ALL_REGISTERED_LEAVES_ABSENT",
        "registered_leaf_state": {
            "primary_ledger": "ABSENT",
            "primary_receipts": "ABSENT",
            "secondary_receipts": "ABSENT",
            "secondary_snapshots": "ABSENT",
        },
        "mirrored_history": None,
        "bundle_destination_absent": True,
        "lost_series_ledger_absent": True,
        "retired_v2_1_claim_absent": True,
        "paths_created": 0,
    }
    assert isinstance(result["control_record_count"], int)
    assert result["control_record_count"] > len(REGISTERED_SURFACE)
    assert isinstance(result["control_merkle_root_sha256"], str)
    assert len(result["control_merkle_root_sha256"]) == 64
    assert [row["path"] for row in result["registered_surface"]] == [
        relative.as_posix() for relative in REGISTERED_SURFACE
    ]
    assert all(set(row) == {"path", "sha256"} for row in result["registered_surface"])
    assert [row["sha256"] for row in result["registered_surface"]] == [
        _sha256(
            _fixture_git(
                binding.project_root,
                "show",
                f"{implementation_commit}:{relative.as_posix()}",
            )
        )
        for relative in REGISTERED_SURFACE
    ]
    mixed_arguments = subprocess.run(
        [
            *command,
            "--attempt-authorization",
            binding.action_authorization_path.as_posix(),
            "--expected-ordinal",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=_locked_environment(),
        timeout=300,
    )
    assert mixed_arguments.returncode == 1
    assert mixed_arguments.stdout == ""
    mixed_error = implementation.strict_json_loads(
        mixed_arguments.stderr,
        source="mixed preflight and attempt arguments rejection",
    )
    assert mixed_error == {
        "schema_version": "p4.2a-v2-2-rehearsal-execution-error-v1",
        "status": "FAILED_NO_AUTOMATIC_RETRY",
        "exception_type": "RehearsalV22Error",
        "message_sha256": _sha256(b"v2.2 read-only preflight arguments are not exact"),
    }
    assert _tree_fingerprint(binding.project_root) == before_project
    assert _tree_fingerprint(binding.ledger_root) is None
    assert _tree_fingerprint(binding.destination) is None
    assert (
        tuple(sorted(binding.project_root.parent.glob(".alphapilot-p4-2a-v2-2-temp-*")))
        == before_temp
    )
    assert _all_real_path_fingerprints() == before_real


def test_exact_os_read_only_preflight_reports_every_current_control_drift(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before_real = _all_real_path_fingerprints()
    binding = _synthetic_binding(tmp_path.resolve(), label="read-only-preflight-control-drift")
    implementation_commit, owner_surface, independent_review, _execution_head = (
        _initialize_synthetic_epoch_two(binding)
    )
    drifted_controls: list[dict[str, str]] = []
    for relative, suffix in (
        (Path("src/alphapilot/core/config.py"), b"\n# synthetic epoch-4 config drift\n"),
        (Path("src/alphapilot/db/models.py"), b"\n# synthetic epoch-4 model drift\n"),
    ):
        selected = _fixture_git(
            binding.project_root,
            "show",
            f"{implementation_commit}:{relative.as_posix()}",
        )
        current = selected + suffix
        (binding.project_root / relative).write_bytes(current)
        drifted_controls.append(
            {
                "repository_path": relative.as_posix(),
                "selected_commit_sha256": _sha256(selected),
                "worktree_sha256": _sha256(current),
            }
        )
    expected_message = (
        "control differs from selected commit: "
        + implementation._canonical_json_bytes(drifted_controls).decode("utf-8").removesuffix("\n")
    )
    command = _exact_read_only_preflight_command(
        binding,
        implementation_epoch=2,
        implementation_commit=implementation_commit,
        owner_surface_authorization=owner_surface,
        independent_review=independent_review,
    )
    before_project = _tree_fingerprint(binding.project_root)
    before_temp = tuple(sorted(binding.project_root.parent.glob(".alphapilot-p4-2a-v2-2-temp-*")))
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=_locked_environment(),
        timeout=300,
    )
    assert completed.returncode == 1
    assert completed.stdout == ""
    error = implementation.strict_json_loads(
        completed.stderr,
        source="multi-member control drift preflight rejection",
    )
    assert error == {
        "schema_version": "p4.2a-v2-2-rehearsal-execution-error-v1",
        "status": "FAILED_NO_AUTOMATIC_RETRY",
        "exception_type": "RehearsalV22Error",
        "message_sha256": _sha256(expected_message.encode("utf-8")),
    }
    assert _tree_fingerprint(binding.project_root) == before_project
    assert _tree_fingerprint(binding.ledger_root) is None
    assert _tree_fingerprint(binding.destination) is None
    assert (
        tuple(sorted(binding.project_root.parent.glob(".alphapilot-p4-2a-v2-2-temp-*")))
        == before_temp
    )
    assert _all_real_path_fingerprints() == before_real


def test_ordinary_wrapper_sitecustomize_environment_and_orig_argv_drift_all_reject(
    tmp_path: Path,
) -> None:
    before = _all_real_path_fingerprints()
    binding = _synthetic_binding(tmp_path.resolve(), label="bootstrap-negative-matrix")
    _initialize_synthetic_epoch_one(binding)
    exact_command = _exact_attempt_command(binding, 1)
    wrapper = f"import runpy;runpy.run_path({binding.shim_path.as_posix()!r},run_name='__main__')"
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


def test_exact_os_series_2_epoch_five_ordinal_one_seals_mirrors_and_validates_bundle(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    before = _all_real_path_fingerprints()
    binding = _synthetic_binding(tmp_path.resolve(), label="exact-series-2-ordinal-one")
    epoch = _initialize_synthetic_series_2_epoch_five(binding)
    control_root = implementation.build_control_surface(
        binding.project_root,
        epoch[0],
        require_current=False,
    ).merkle_root_sha256
    _prepare_exact_action(
        binding,
        ordinal=1,
        implementation_epoch=5,
        epoch=epoch,
        control_merkle_root_sha256=control_root,
    )
    attempt = _run_exact_attempt(binding, 1, requested_outcome="SUCCESS")
    history = implementation.validate_live_history(binding)
    assert tuple(record.ordinal for record in history.records) == (1,)
    assert tuple(record.implementation_epoch for record in history.records) == (5,)
    assert history.records[0].outcome == "CANDIDATE_VALIDATED_AND_SELECTED"
    assert (binding.ledger_root / "attempts/000001/terminal.json").is_file()
    receipts = implementation._validate_second_copy_history(binding, history)
    assert len(receipts) == 1
    snapshot_members = tuple(binding.secondary_snapshot_root.iterdir())
    assert len(snapshot_members) == 1
    assert snapshot_members[0].name == implementation._mirror_snapshot_name(
        1,
        history.live_ledger_root_sha256,
    )
    receipt_name = implementation._mirror_receipt_filename(
        1,
        history.live_ledger_root_sha256,
    )
    paired = (
        (binding.primary_receipt_root / receipt_name).read_bytes(),
        (binding.secondary_receipt_root / receipt_name).read_bytes(),
    )
    assert paired[0] == paired[1] == implementation._canonical_json_bytes(receipts[0])
    bundle_payload = (binding.destination / implementation.BUNDLE_FILENAME).read_bytes()
    bundle = implementation.strict_json_loads(bundle_payload, source="series-2 ordinal-one")
    assert [row["epoch"] for row in bundle["implementation_epochs"]] == [5]
    assert not any("void" in json.dumps(row).lower() for row in bundle["implementation_epochs"])
    assert attempt["result"]["release_probe"]["public_release_validation_passed"] is True
    assert _all_real_path_fingerprints() == before


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
    assert {record.implementation_epoch for record in history.records} == {5}
    assert {record.implementation_commit for record in history.records} == {fixture["epoch"][0]}
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
    assert release_probe["status"] == ("PASS_DISPOSABLE_SAME_VALIDATOR_RELEASE_ACCEPTANCE")
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
        "message_sha256": _sha256(b"authority is not one globally unique status-A Git touch"),
    }
    cross_root_rejection = release_probe["cross_official_root_rejection"]
    assert cross_root_rejection == {
        "name": "synthetic_capability_against_registered_official_root",
        "result": "PASS_REJECTED",
        "exception_type": "RehearsalV22Error",
        "message_sha256": _sha256(b"disposable v2.2 capability is forged or cross-root"),
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
        and row["temporary_authority_tree_before"] == row["temporary_authority_tree_after"]
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
    assert (
        _fixture_git(
            fixture["binding"].project_root,
            "rev-parse",
            "HEAD",
        )
        .decode()
        .strip()
        == modified["commit"]
    )
    assert (
        _sha256(
            _fixture_git(
                fixture["binding"].project_root,
                "show",
                f"{receipt['creating_commit']}:{receipt_relative.as_posix()}",
            )
        )
        == receipt["sha256"]
    )
    assert (
        _sha256((fixture["binding"].project_root / receipt_relative).read_bytes())
        == modified["current_sha256"]
    )
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
        "message_sha256": _sha256(b"v2.2 rehearsal destination already exists"),
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
        (f"{receipt_reference['creating_commit']}:{receipt_reference['path']}"),
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
            fixture["binding"].destination / "archive/run-a/root" / artifact["source_relative_path"]
        ).read_bytes()
        for artifact in runs[0]["artifacts"]
    }
    run_b = {
        artifact["source_relative_path"]: (
            fixture["binding"].destination / "archive/run-b/root" / artifact["source_relative_path"]
        ).read_bytes()
        for artifact in runs[1]["artifacts"]
    }
    assert run_a == run_b
    assert len(run_a) == 14
    assert runs[0]["artifact_merkle_root_sha256"] == (runs[1]["artifact_merkle_root_sha256"])
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


def test_candidate_checkpoint_crash_preserves_candidate_and_unique_next_history(
    exact_failed_epoch_then_incomplete_success_series: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    fixture = exact_failed_epoch_then_incomplete_success_series
    history = fixture["history"]
    incomplete = history.records[1]
    assert incomplete.ordinal == 2
    assert incomplete.outcome == "INCOMPLETE_UNTERMINALIZED"
    assert incomplete.reached_stage == "candidate_without_terminal"
    assert incomplete.evidence_tree_root_sha256 != (implementation._evidence_empty_root_sha256())
    assert incomplete.artifact_inventory
    assert incomplete.candidate_bytes is not None
    assert incomplete.candidate_sha256 == _sha256(incomplete.candidate_bytes)
    assert incomplete.terminal_bytes is None
    selected = history.records[2]
    assert selected.previous_history_root_sha256 != (history.records[0].record_root_sha256)
    assert selected.previous_history_root_sha256 != (implementation._history_empty_root_sha256())
    assert selected.attempt_token_sha256 != incomplete.attempt_token_sha256
    assert fixture["attempts"][1]["returncode"] == -signal.SIGKILL
    checkpoint = fixture["attempts"][1]["candidate_checkpoint"]
    assert checkpoint["child_pid"] > 0
    assert checkpoint["phase"] == "candidate_fsynced_before_terminal_checkpoint"
    assert checkpoint["terminal_absent_at_stop"] is True
    assert checkpoint["external_parent_observed_stop"] is True
    assert checkpoint["external_parent_action"] == "SIGKILL"
    assert checkpoint["candidate_unchanged_after_external_kill"] is True
    assert _all_real_path_fingerprints() == fixture["real_fingerprints"]


def test_failed_incomplete_success_history_keeps_every_evidence_subtree(
    exact_failed_epoch_then_incomplete_success_series: dict[str, Any],
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
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
    assert history.records[1].artifact_inventory
    assert history.records[1].candidate_bytes is not None
    assert history.records[1].terminal_bytes is None
    assert history.records[2].artifact_inventory
    assert tuple(record.implementation_epoch for record in history.records) == (5, 6, 6)
    bundle_history = fixture["bundle"]["attempt_history"]
    assert [record["outcome"] for record in bundle_history["records"]] == [
        "FAILED",
        "INCOMPLETE_UNTERMINALIZED",
        "CANDIDATE_VALIDATED_AND_SELECTED",
    ]
    assert [epoch["epoch"] for epoch in fixture["bundle"]["implementation_epochs"]] == [
        5,
        6,
    ]
    schema = json.loads(
        (
            fixture["binding"].project_root / implementation.SERIES_2_BUNDLE_SCHEMA_RELATIVE
        ).read_bytes()
    )
    assert not list(Draft202012Validator(schema).iter_errors(fixture["bundle"]))
    incomplete_bundle_record = bundle_history["records"][1]
    assert incomplete_bundle_record["candidate"] is None
    assert incomplete_bundle_record["terminal"] is None
    assert incomplete_bundle_record["reached_stage"] == "candidate_without_terminal"
    candidate_archive = "archive/attempt-history/attempts/000002/candidate.json"
    live_candidate = history.records[1].candidate_bytes
    assert live_candidate is not None
    archived_candidate = fixture["binding"].destination / candidate_archive
    assert archived_candidate.read_bytes() == live_candidate
    assert {
        "path": candidate_archive,
        "sha256": _sha256(live_candidate),
    } in fixture["bundle"]["archive"]["attempt_history"]["files"]
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
    epoch = _initialize_synthetic_series_2_epoch_five(binding)
    control_root = implementation.build_control_surface(
        binding.project_root,
        epoch[0],
        require_current=False,
    ).merkle_root_sha256
    _prepare_exact_action(
        binding,
        ordinal=1,
        implementation_epoch=5,
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
        implementation_epoch=5,
        epoch=epoch,
        control_merkle_root_sha256=control_root,
    )
    contender = _spawn_exact_attempt(binding, 2)
    contender_returncode, contender_stdout, contender_stderr = _communicate_exact_attempt(contender)
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
    assert (
        implementation.strict_json_loads(
            holder_stderr,
            source="concurrent holder failure",
        )["status"]
        == "FAILED_NO_AUTOMATIC_RETRY"
    )
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
    evidence_root = fixture["binding"].ledger_root / "attempts/000003/evidence"
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
    assert probe["active_ledger_fingerprint_discipline"] == {
        "active_ledger_fingerprinted": True,
        "active_ledger_is_registered_ledger": False,
        "exact_legal_create_only_paths": [
            "attempts/000003/evidence/probes/same-parent-first.txt",
            "attempts/000003/evidence/probes/same-parent-second.txt",
        ],
        "non_active_registered_paths_unchanged": True,
        "negative_probe_baseline_is_after_positive_writes": True,
    }
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
    assert {row["result"] for row in event_probes} == {"PASS_REJECTED_BEFORE_EFFECT"}
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


def test_active_official_ledger_fingerprint_allows_only_two_exact_positive_writes(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    container_root = (tmp_path / "fast-primary-container").absolute()
    ledger_root = container_root / "PRIMARY-LEDGER-DO-NOT-DELETE"
    prefix = ledger_root.relative_to(container_root).as_posix()
    before_active = {
        ".": "directory:0700",
        "attempts": "directory:0700",
        "attempts/000001": "directory:0700",
        "attempts/000001/evidence": "directory:0700",
        "attempts/000001/evidence/probes": "directory:0700",
    }
    first = ledger_root / "attempts/000001/evidence/probes/same-parent-first.txt"
    second = ledger_root / "attempts/000001/evidence/probes/same-parent-second.txt"
    created = ((first, b"first\n"), (second, b"second\n"))
    first_fingerprint = f"file:{_sha256(b'first' + bytes([10]))}:0600:1"
    second_fingerprint = f"file:{_sha256(b'second' + bytes([10]))}:0600:1"
    after_active = {
        **before_active,
        first.relative_to(ledger_root).as_posix(): first_fingerprint,
        second.relative_to(ledger_root).as_posix(): second_fingerprint,
    }
    before_container = {
        ".": "directory:0700",
        ".identity": "device:1:inode:2:uid:3",
        prefix: "directory:0700",
        f"{prefix}/attempts": "directory:0700",
        f"{prefix}/attempts/000001": "directory:0700",
        f"{prefix}/attempts/000001/evidence": "directory:0700",
        f"{prefix}/attempts/000001/evidence/probes": "directory:0700",
        "MIRROR-RECEIPTS-DO-NOT-DELETE": "directory:0700",
        "MIRROR-RECEIPTS-DO-NOT-DELETE/preexisting.json": (
            f"file:{_sha256(b'preexisting')}:0600:1"
        ),
    }
    after_container = {
        **before_container,
        f"{prefix}/{first.relative_to(ledger_root).as_posix()}": first_fingerprint,
        f"{prefix}/{second.relative_to(ledger_root).as_posix()}": second_fingerprint,
    }
    before_real = {name: {".": "absent"} for name in implementation.REGISTERED_FINGERPRINT_KEYS}
    before_real["registered_v2_2_primary_container"] = before_container
    before_real["registered_v2_2_ledger"] = before_active
    after_real = {
        **before_real,
        "registered_v2_2_primary_container": after_container,
        "registered_v2_2_ledger": after_active,
    }
    observed = implementation._validate_active_ledger_positive_transition(
        mode="REGISTERED_OFFICIAL",
        container_root=container_root,
        ledger_root=ledger_root,
        before_real=before_real,
        after_real=after_real,
        before_active=before_active,
        after_active=after_active,
        created=created,
    )
    assert observed == {
        "active_ledger_fingerprinted": True,
        "active_ledger_is_registered_ledger": True,
        "exact_legal_create_only_paths": [
            first.relative_to(ledger_root).as_posix(),
            second.relative_to(ledger_root).as_posix(),
        ],
        "non_active_registered_paths_unchanged": True,
        "negative_probe_baseline_is_after_positive_writes": True,
    }

    implementation_tree = ast.parse((PROJECT_ROOT / IMPLEMENTATION_RELATIVE).read_bytes())
    calls = [
        node
        for node in ast.walk(implementation_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_real_path_fingerprints"
    ]
    definitions = [
        node
        for node in ast.walk(implementation_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_real_path_fingerprints"
    ]
    occurrence_audit = implementation.REAL_PATH_FINGERPRINT_OCCURRENCE_AUDIT
    assert len(calls) == 16
    assert len(definitions) == 1
    assert len(occurrence_audit) == 17
    assert sum(row[1] == "definition_not_call" for row in occurrence_audit) == 1
    assert implementation.SEAL_THEN_MIRROR_REGISTERED_KEY_AUDIT == (
        "registered_v2_2_ledger",
        "registered_v2_2_primary_container",
        "registered_v2_2_primary_receipts",
        "registered_v2_2_secondary_container",
        "registered_v2_2_secondary_receipts",
        "registered_v2_2_secondary_snapshots",
    )
    assert implementation.TERMINAL_SEAL_REGISTERED_KEY_AUDIT == (
        "registered_v2_2_ledger",
        "registered_v2_2_primary_container",
    )
    assert implementation.MIRROR_ONLY_REGISTERED_KEY_AUDIT == (
        "registered_v2_2_primary_container",
        "registered_v2_2_primary_receipts",
        "registered_v2_2_secondary_container",
        "registered_v2_2_secondary_receipts",
        "registered_v2_2_secondary_snapshots",
    )
    assert set(implementation.SEAL_THEN_MIRROR_REGISTERED_KEY_AUDIT) == (
        set(implementation.TERMINAL_SEAL_REGISTERED_KEY_AUDIT)
        | set(implementation.MIRROR_ONLY_REGISTERED_KEY_AUDIT)
    )
    assert "SeriesLedger.__exit__" in (implementation.SEAL_THEN_MIRROR_ATTEMPT_1_AUDIT_NOTE)

    legacy_nine_before = {
        key: value
        for key, value in before_real.items()
        if key
        not in {
            "registered_v2_2_primary_container",
            "registered_v2_2_secondary_container",
        }
    }
    legacy_nine_after = {
        **legacy_nine_before,
        "registered_v2_2_ledger": after_active,
    }
    before_missing = dict(before_real)
    before_missing.pop("registered_v2_2_primary_container")
    after_missing = dict(after_real)
    after_missing.pop("registered_v2_2_secondary_container")
    shared_unknown_before = {
        **before_real,
        "unregistered_future_surface": {".": "absent"},
    }
    shared_unknown_after = {
        **after_real,
        "unregistered_future_surface": {".": "absent"},
    }
    for drifted_before, drifted_after in (
        (legacy_nine_before, legacy_nine_after),
        (before_missing, after_real),
        (before_real, after_missing),
        (shared_unknown_before, shared_unknown_after),
    ):
        with pytest.raises(
            implementation.RehearsalV22Error,
            match="registered fingerprint key set drifted",
        ):
            implementation._validate_active_ledger_positive_transition(
                mode="REGISTERED_OFFICIAL",
                container_root=container_root,
                ledger_root=ledger_root,
                before_real=drifted_before,
                after_real=drifted_after,
                before_active=before_active,
                after_active=after_active,
                created=created,
            )

    escaped_real = copy.deepcopy(after_real)
    escaped_real["real_heldout_root"] = {".": "directory:0700"}
    with pytest.raises(
        implementation.RehearsalV22Error,
        match="outside active ledger ancestor scopes",
    ):
        implementation._validate_active_ledger_positive_transition(
            mode="REGISTERED_OFFICIAL",
            container_root=container_root,
            ledger_root=ledger_root,
            before_real=before_real,
            after_real=escaped_real,
            before_active=before_active,
            after_active=after_active,
            created=created,
        )

    extra_relative = "attempts/000001/evidence/probes/third.txt"
    extra_active = {
        **after_active,
        extra_relative: f"file:{_sha256(b'third' + bytes([10]))}:0600:1",
    }
    extra_real = {
        **after_real,
        "registered_v2_2_primary_container": {
            **after_container,
            f"{prefix}/{extra_relative}": extra_active[extra_relative],
        },
        "registered_v2_2_ledger": extra_active,
    }
    with pytest.raises(
        implementation.RehearsalV22Error,
        match="exceeded exact evidence writes",
    ):
        implementation._validate_active_ledger_positive_transition(
            mode="REGISTERED_OFFICIAL",
            container_root=container_root,
            ledger_root=ledger_root,
            before_real=before_real,
            after_real=extra_real,
            before_active=before_active,
            after_active=extra_active,
            created=created,
        )


def test_exact_os_active_ledger_accepts_real_nested_container_projection(
    tmp_path: Path,
) -> None:
    document = _run_exact_nested_fingerprint_probe(tmp_path, scenario="positive")
    assert document["changed_registered_keys"] == [
        "registered_v2_2_ledger",
        "registered_v2_2_primary_container",
    ]


@pytest.mark.parametrize(
    ("drift_kind", "expected_changed"),
    (
        (
            "outside-ledger-file",
            [
                "registered_v2_2_ledger",
                "registered_v2_2_primary_container",
            ],
        ),
        ("container-only", ["registered_v2_2_primary_container"]),
        (
            "third-key",
            [
                "real_heldout_root",
                "registered_v2_2_ledger",
                "registered_v2_2_primary_container",
            ],
        ),
    ),
)
def test_exact_os_active_ledger_rejects_real_nested_projection_drift(
    tmp_path: Path,
    drift_kind: str,
    expected_changed: list[str],
) -> None:
    document = _run_exact_nested_fingerprint_probe(tmp_path, scenario=drift_kind)
    assert document["changed_registered_keys"] == expected_changed


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
    assert all(row["result"] == "PASS_REJECTED_BEFORE_EFFECT" for row in authority["probes"])
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
    bundle_native_source = inspect.getsource(implementation._native_rename_exclusive_call)
    mirror_primitive_source = inspect.getsource(implementation._rename_mirror_directory_exclusive)
    mirror_native_source = inspect.getsource(implementation._native_mirror_renameatx_exclusive_call)
    module_source = (PROJECT_ROOT / IMPLEMENTATION_RELATIVE).read_text()
    assert "renameatx_np" in module_source
    assert "renamex_np" in module_source
    assert "RENAME_EXCL" in module_source
    assert "_rename_directory_exclusive" in source
    assert "os.rename(candidate, binding.destination)" not in source
    assert source.count("_rename_directory_exclusive(") == 1
    assert "_native_rename_exclusive_call" in primitive_source
    assert bundle_native_source.count("renamex_np(") == 1
    assert "renameatx_np(" not in bundle_native_source
    assert 'symbol="renamex_np"' in bundle_native_source
    assert "_native_mirror_renameatx_exclusive_call" in mirror_primitive_source
    assert mirror_native_source.count("renameatx_np(") == 1
    assert "renamex_np(" not in mirror_native_source
    assert 'symbol="renameatx_np"' in mirror_native_source
    assert "fcntl.F_GETPATH" in mirror_native_source
    assert "fsencode(source.name)" in mirror_native_source
    assert "fsencode(destination.name)" in mirror_native_source
    assert "c_uint(0x00000004)" in bundle_native_source
    assert "c_uint(0x00000004)" in mirror_native_source
    for hardened_source in (bundle_native_source, mirror_native_source):
        assert "disable_runtime_callbacks()" in hardened_source
        assert "block_runtime_callbacks()" in hardened_source
        assert "capability_context.set(capability)" in hardened_source
        assert "registry = prior_registry" in hardened_source
        assert hardened_source.index("registry = prior_registry") < hardened_source.index(
            "capability_context.reset(token)"
        )
    assert "_fsync_directory(destination_absolute.parent)" in primitive_source
    assert "destination_parent_fsync_via_identity_bound_descriptor" in (mirror_primitive_source)
    openat_source = inspect.getsource(implementation._open_exclusive_at_issued)
    write_at_source = inspect.getsource(implementation._write_exclusive_at)
    assert "flags != registered_flags" in openat_source
    assert "mode != registered_mode" in openat_source
    assert "os.O_WRONLY" in write_at_source
    assert "os.O_RDWR" not in write_at_source
    assert "FD_CLOEXEC" in openat_source
    assert "opened.st_nlink != 1" in openat_source
    assert "disable_runtime_callbacks()" in openat_source
    assert openat_source.index("registry = prior_registry") < openat_source.index(
        "capability_context.reset(token)"
    )


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
    actions = {action.dest: action for action in parser._actions if action.dest != "help"}
    assert set(actions) == {
        "execute",
        "preflight_only",
        "attempt_authorization",
        "expected_ordinal",
        "implementation_epoch",
        "implementation_commit",
        "owner_surface_authorization",
        "independent_implementation_review",
    }
    operation_groups = [
        group
        for group in parser._mutually_exclusive_groups
        if {action.dest for action in group._group_actions} == {"execute", "preflight_only"}
    ]
    assert len(operation_groups) == 1
    assert operation_groups[0].required is True
    assert not any(action.required for action in actions.values())
    assert actions["attempt_authorization"].default is None
    assert actions["expected_ordinal"].default is None
    assert actions["implementation_epoch"].default is None
    assert actions["implementation_commit"].default is None
    assert actions["owner_surface_authorization"].default is None
    assert actions["independent_implementation_review"].default is None
    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args(["--execute", "--preflight-only"])
    read_only_source = inspect.getsource(implementation._read_only_implementation_preflight)
    for forbidden in (
        "_validate_action_authorization",
        "_create_temporary_authority",
        "SeriesLedger",
        "_execution_capability_scope",
        "_execute_authorized_attempt",
    ):
        assert forbidden not in read_only_source
    run_source = inspect.getsource(implementation._run_cli)
    assert run_source.index("_read_only_implementation_preflight(") < run_source.index(
        "_create_temporary_authority("
    )
    policy = implementation._read_only_preflight_policy(PROJECT_ROOT)
    assert policy.write_roots == ()
    assert policy.exact_write_paths == ()
    assert policy.create_only_roots == ()
    assert policy.sqlite_roots == ()
    assert policy.git_roots == (PROJECT_ROOT,)
    assert policy.subprocess_mode == "git-read"
    source = inspect.getsource(implementation._execute_authorized_attempt)
    assert source.count("os.kill(os.getpid(), signal.SIGSTOP)") == 2
    checkpoint_ifs = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.If)
        and any(
            isinstance(candidate, ast.Call)
            and isinstance(candidate.func, ast.Attribute)
            and candidate.func.attr == "kill"
            and len(candidate.args) == 2
            and ast.unparse(candidate.args[0]) == "os.getpid()"
            and ast.unparse(candidate.args[1]) == "signal.SIGSTOP"
            for candidate in ast.walk(node)
        )
    ]
    assert len(checkpoint_ifs) == 2
    for checkpoint_if in checkpoint_ifs:
        assert isinstance(checkpoint_if.test, ast.Compare)
        assert ast.unparse(checkpoint_if.test) == ("binding.mode == 'DISPOSABLE_FULL_SHAPE_TEST'")
        assert "REGISTERED_OFFICIAL" not in ast.unparse(checkpoint_if)
    checkpoint_bodies = [ast.unparse(node) for node in checkpoint_ifs]
    assert sum("disposable_started_checkpoint" in body for body in checkpoint_bodies) == 1
    assert sum("candidate_document" in body for body in checkpoint_bodies) == 1
    assert sum("_canonical_object_file" in body for body in checkpoint_bodies) == 1
    assert sum("terminal.json" in body for body in checkpoint_bodies) == 1
    first_stop = source.index("os.kill(os.getpid(), signal.SIGSTOP)")
    second_stop = source.index("os.kill(os.getpid(), signal.SIGSTOP)", first_stop + 1)
    assert source.index("ledger.allocate_attempt(") < first_stop
    assert first_stop < source.index("candidate_path = lease.write_candidate(")
    assert source.index("candidate_path = lease.write_candidate(") < second_stop
    assert second_stop < source.index("lease.write_terminal(")
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
    assert source.index("with _audited_execution(") < source.index("os.mkdir(authority, 0o700)")
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
        assert (
            implementation._audited_mutation_path(
                "member.txt",
                nested_descriptor,
                policy,
            )
            == nested / "member.txt"
        )
        assert (
            implementation._mutation_dir_fd(
                "shutil.rmtree",
                ("member.txt", nested_descriptor),
            )
            == nested_descriptor
        )
        assert (
            implementation._mutation_dir_fd(
                "os.remove",
                ("member.txt", nested_descriptor),
            )
            == nested_descriptor
        )

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

    issued_probe_source = inspect.getsource(implementation._ledger_create_only_probes)
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


def test_kernel_noreplace_primitive_rejects_existing_destination_without_authority(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    candidate = tmp_path / "candidate"
    candidate.mkdir(mode=0o700)
    payload = candidate / "bundle.json"
    payload.write_bytes(b"{}\n")
    payload.chmod(0o600)
    destination = tmp_path / "destination"
    destination.mkdir(mode=0o700)
    source_metadata = candidate.lstat()
    destination_metadata = destination.lstat()
    source_tree = _tree_fingerprint(candidate)
    destination_tree = _tree_fingerprint(destination)
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"issued audit policy|authority",
    ):
        implementation._rename_directory_exclusive(candidate, destination)
    assert candidate.lstat().st_dev == source_metadata.st_dev
    assert candidate.lstat().st_ino == source_metadata.st_ino
    assert _tree_fingerprint(candidate) == source_tree
    assert destination.lstat().st_dev == destination_metadata.st_dev
    assert destination.lstat().st_ino == destination_metadata.st_ino
    assert _tree_fingerprint(destination) == destination_tree


def test_kernel_noreplace_primitive_cannot_move_without_issued_path_authority(
    tmp_path: Path,
) -> None:
    implementation = importlib.import_module(IMPLEMENTATION_MODULE)
    candidate = tmp_path / "candidate"
    candidate.mkdir(mode=0o700)
    (candidate / "bundle.json").write_bytes(b"{}\n")
    (candidate / "bundle.json").chmod(0o600)
    destination = tmp_path / "destination"
    before = _tree_fingerprint(candidate)
    with pytest.raises(
        implementation.RehearsalV22Error,
        match=r"issued audit policy|authority",
    ):
        implementation._rename_directory_exclusive(candidate, destination)
    assert _tree_fingerprint(candidate) == before
    assert not os.path.lexists(destination)

    openat_source = inspect.getsource(implementation._open_exclusive_at_issued)
    native_sources = (
        inspect.getsource(implementation._native_rename_exclusive_call),
        inspect.getsource(implementation._native_mirror_renameatx_exclusive_call),
    )
    assert openat_source.index("if registry or capability_context.get() is not None:") < (
        openat_source.index("prior_registry = registry")
    )
    assert openat_source.index("registry = prior_registry") < openat_source.index(
        "capability_context.reset(token)"
    )
    for native_source in native_sources:
        assert native_source.index("if registry or capability_context.get() is not None:") < (
            native_source.index("capability = _NativeRenameCapability(")
        )
        assert native_source.index("registry = prior_registry") < native_source.index(
            "capability_context.reset(token)"
        )

    syscall_code = f"""
import ctypes
import os

source = {candidate.as_posix()!r}
destination = {destination.as_posix()!r}
libc = ctypes.CDLL(None, use_errno=True)
renamex_np = libc.renamex_np
renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
renamex_np.restype = ctypes.c_int
ctypes.set_errno(0)
rc = renamex_np(os.fsencode(source), os.fsencode(destination), ctypes.c_uint(4))
if rc != 0 or ctypes.get_errno() != 0:
    raise SystemExit(f"unexpected renamex_np result: {{rc}}/{{ctypes.get_errno()}}")
"""
    subprocess.run(
        [sys.executable, "-B", "-c", syscall_code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert not os.path.lexists(candidate)
    assert _tree_fingerprint(destination) == before
    assert implementation._OPENAT_WRITE_CAPABILITY.get() is None
    assert implementation._NATIVE_RENAME_CAPABILITY.get() is None
    assert (
        inspect.getclosurevars(implementation._openat_write_capability_path).nonlocals["registry"]
        == ()
    )
    assert (
        inspect.getclosurevars(implementation._native_rename_capability_is_issued).nonlocals[
            "registry"
        ]
        == ()
    )

    monitoring_effect = tmp_path / "monitoring-borrow-effect.bin"
    monitoring_code = f"""
import importlib
import inspect
import os
import sys

target = {monitoring_effect.as_posix()!r}
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
    monitored = subprocess.run(
        [sys.executable, "-B", "-c", monitoring_code],
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
    assert monitored.stderr == ""
    blocked_output, observed_output = monitored.stdout.strip().rsplit("|", 1)
    assert blocked_output.split(",") == [
        "_open_exclusive_at_issued",
        "_native_rename_exclusive_call",
    ]
    assert observed_output == "0"
    assert not monitoring_effect.exists()

    collision_root = tmp_path / "renameatx-collision"
    collision_source = collision_root / "staging"
    collision_destination = collision_root / "published"
    collision_source.mkdir(parents=True, mode=0o700)
    collision_destination.mkdir(mode=0o700)
    (collision_source / "source.bin").write_bytes(b"source\n")
    (collision_destination / "destination.bin").write_bytes(b"destination\n")
    collision_before = (
        _tree_fingerprint(collision_source),
        _tree_fingerprint(collision_destination),
    )
    collision_code = f"""
import ctypes
import errno
import os

root = {collision_root.as_posix()!r}
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
        [sys.executable, "-B", "-c", collision_code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert collision_source.is_dir() and collision_destination.is_dir()
    assert (
        _tree_fingerprint(collision_source),
        _tree_fingerprint(collision_destination),
    ) == collision_before


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
        inspect.signature(_communicate_exact_attempt).parameters["timeout_seconds"].default == 300.0
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
    assert frozenset(preflight.loaded_repository_sources) < frozenset(preflight.ast_closure_paths)
    assert frozenset(observed.loaded_repository_sources) == frozenset(observed.ast_closure_paths)
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
        and any(isinstance(target, ast.Name) and target.id == "dynamic" for target in node.targets)
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
    omit_keywords = [keyword for keyword in template_calls[0].keywords if keyword.arg == "omit"]
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
    assert ast.literal_eval(update_values["reviewer"]) == (EXPECTED_SYNTHETIC_RELEASE_REVIEWER)
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
        if isinstance(node, ast.FunctionDef) and node.name == "_run_disposable_release_probe"
    )
    early_mode_gate = next(node for node in probe.body if isinstance(node, ast.If))
    assert ast.unparse(early_mode_gate.test) == ("binding.mode != 'DISPOSABLE_FULL_SHAPE_TEST'")
    early_returns = [node for node in ast.walk(early_mode_gate) if isinstance(node, ast.Return)]
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
