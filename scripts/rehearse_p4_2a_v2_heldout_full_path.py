#!/usr/bin/env python3
"""Build the registered P4.2a successor-v2 rehearsal bundle offline.

The official CLI executes the full synthetic path twice in isolated temporary
roots, verifies byte-identical 14-artifact runs and the commit-bound control
surface, then publishes the complete bundle atomically and create-only.  The
retired v1 helper remains available only to its historical tests; the CLI never
publishes v1 evidence.  No path may open the production database, call a
network or model provider, or compute real held-out metrics.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import copy
import ctypes
import errno
import fcntl
import hashlib
import importlib.metadata
import io
import json
import locale
import os
import platform
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, cast
from unittest.mock import patch

# The official CLI must lock locale, timezone and Python hashing before any
# third-party or repository-local module is imported.  Invalid/diagnostic CLI
# invocations do not execute the rehearsal and can continue to argparse below.
_EARLY_ENVIRONMENT_MARKER = "ALPHAPILOT_P42A_REHEARSAL_V2_ENV_LOCKED"
_EARLY_LOCKED_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONHASHSEED": "0",
    "TZ": "UTC",
}
if (
    __name__ == "__main__"
    and sys.argv[1:] == ["--execute"]
    and os.environ.get(_EARLY_ENVIRONMENT_MARKER) != "1"
):
    _early_environment = dict(os.environ)
    _early_environment.update(_EARLY_LOCKED_ENVIRONMENT)
    _early_environment[_EARLY_ENVIRONMENT_MARKER] = "1"
    os.execve(
        sys.executable,
        [sys.executable, str(Path(__file__).resolve()), "--execute"],
        _early_environment,
    )

import yaml  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import build_p4_2a_gold_sample as materializer  # noqa: E402
from scripts import build_p4_2a_v2_heldout_adjudication_ui as heldout_ui  # noqa: E402
from scripts import evaluate_p4_2a_v2_heldout as evaluator  # noqa: E402
from scripts import finalize_p4_2a_v2_heldout_adjudication as heldout_finalizer  # noqa: E402
from scripts import prepare_p4_2a_v2_heldout as prepare  # noqa: E402
from scripts import run_p4_2a_offline_extract as offline_extract  # noqa: E402
from scripts import run_p4_2a_v2_dev_calibration as dev_runner  # noqa: E402
from scripts import seal_p4_2a_v2_ai_draft as base_seal  # noqa: E402
from scripts import seal_p4_2a_v2_heldout_draft as heldout_seal  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alphapilot.core.config import Settings  # noqa: E402
from alphapilot.db.models import LLMCall  # noqa: E402

JsonObject = dict[str, Any]
Clock = Callable[[], datetime]

PREREGISTRATION_SHA256 = prepare.PREREGISTRATION_SHA256
DESIGN_SHA256 = prepare.DESIGN_SHA256
HELDOUT_CONTRACT_SHA256 = prepare.HELDOUT_CONTRACT_SHA256
REGISTERED_REHEARSAL_RELATIVE = Path("docs/phase4/rehearsals/P4.2a-v2-calibration")
SUCCESSOR_PREREGISTRATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-preregistration-20260810.json"
)
SUCCESSOR_PREREGISTRATION_SHA256 = (
    "35b6d757876e1308d8f28ded3dc36784afb4e5d7c5c1589b8c211cc079aac7c3"
)
SUCCESSOR_SCHEMA_RELATIVE = Path("config/schemas/p4_2a_v2_heldout_rehearsal_bundle_v2.schema.json")
SUCCESSOR_SCHEMA_SHA256 = "f5ff0516c58f2285302dab5d1a03daafd70ea887d4f323387d44c7c19623a8bc"
SUCCESSOR_DIRECTORY_RELATIVE = Path("docs/phase4/rehearsals/P4.2a-v2-calibration-v2")
SUCCESSOR_BUNDLE_NAME = "bundle.json"
SUCCESSOR_SCHEMA_VERSION = "p4.2a-v2-heldout-rehearsal-bundle-v2"
SUCCESSOR_REHEARSAL_ID = "P4.2A-V2-HELDOUT-REHEARSAL-V2-DETERMINISTIC-20260810"
V1_FAIL_CLOSE_COMMIT = "d710e885b49006eedf4f70ea09cb81fe15d176a3"
V1_TOOLING_COMMIT = "bb5ced1adc83bfe88fa4c86f9b70513a3de97503"
V1_EVIDENCE_COMMIT = "924ddd6efe6da1437e4ea613f30603feb595573f"
FIXED_WALL_CLOCK = datetime(2026, 8, 10, 8, 15, tzinfo=UTC)
FIXED_WALL_CLOCK_TEXT = "2026-08-10T08:15:00Z"
UUID_NAMESPACE = uuid.UUID("4a8a9839-d0a6-509a-b193-ddf4b5700780")
MONOTONIC_INITIAL_NS = 1_000_000_000_000
MONOTONIC_STEP_NS = 1_000_000
CONTROL_MANIFEST_SCHEMA = "p4.2a-v2-heldout-rehearsal-control-manifest-v2"
ENVIRONMENT_REEXEC_MARKER = "ALPHAPILOT_P42A_REHEARSAL_V2_ENV_LOCKED"
LOCKED_EXECUTION_ENVIRONMENT = dict(_EARLY_LOCKED_ENVIRONMENT)
PACKAGE_SOURCE_CALL = "importlib.metadata.distributions(path=derived_absolute_selected_path_roots)"
PACKAGE_PATH_SCOPE = "deduplicated_resolved_sysconfig_purelib_and_platlib_only_no_sys_path_fallback"
PACKAGE_CANONICALIZATION = (
    "sorted_unique_pep503_normalized_name_and_importlib_metadata_version_"
    "as_canonical_json_array_newline"
)
PACKAGE_NEGATIVE_PROBE = "PASS_INJECTED_SECOND_SAME_NORMALIZED_NAME_REJECTED"
MERKLE_LEAF_FORMULA = (
    "SHA256(utf8('p4.2a-rehearsal-leaf-v2\\0') || utf8(relative_path) || NUL || "
    "SHA256(file_bytes).digest())"
)
CONTROL_TREE_PATH_BASIS = "bundle_relative_paths_for_manifest_and_every_control_root_file"
CONTROL_MANIFEST_LEAF_FORMULA = (
    "SHA256(utf8('p4.2a-rehearsal-leaf-v2\\0') || "
    "utf8('archive/control-surface/manifest.json') || NUL || "
    "SHA256(control_manifest_bytes).digest())"
)
MERKLE_NODE_FORMULA = (
    "SHA256(utf8('p4.2a-rehearsal-node-v2\\0') || left_digest_32_bytes || right_digest_32_bytes)"
)
BUNDLE_ROOT_FORMULA = (
    "SHA256(utf8('p4.2a-rehearsal-bundle-v2\\0') || run_a_root_digest_32_bytes || "
    "run_b_root_digest_32_bytes || control_surface_root_digest_32_bytes)"
)
SYNTHETIC_COUNT = 80
SYNTHETIC_POSITIVE_COUNT = 50
SYNTHETIC_NEGATIVE_COUNT = 30
SELECTED_POSITIVE_COUNT = 40
SELECTED_NEGATIVE_COUNT = 20
OWNER_CHAIN_COUNT = 60
SYNTHETIC_ID_START = 900_001
SYNTHETIC_DRAFTER = heldout_seal.EXPECTED_DRAFTER_ID
SYNTHETIC_ADJUDICATOR = "ouyang"
SYNTHETIC_DRAFTED_AT = FIXED_WALL_CLOCK_TEXT
SYNTHETIC_ADJUDICATED_AT = FIXED_WALL_CLOCK_TEXT
SYNTHETIC_COMPLETED_AT = FIXED_WALL_CLOCK_TEXT

TESTED_CODE_PATHS = (
    "scripts/rehearse_p4_2a_v2_heldout_full_path.py",
    "scripts/prepare_p4_2a_v2_heldout.py",
    "scripts/build_p4_2a_gold_sample.py",
    "scripts/run_p4_2a_offline_extract.py",
    "scripts/seal_p4_2a_v2_heldout_draft.py",
    "scripts/seal_p4_2a_v2_ai_draft.py",
    "scripts/build_p4_2a_v2_heldout_adjudication_ui.py",
    "scripts/build_p4_2a_v2_adjudication_ui.py",
    "scripts/finalize_p4_2a_v2_heldout_adjudication.py",
    "scripts/evaluate_p4_2a_v2_heldout.py",
)

SUCCESSOR_ARTIFACT_INVENTORY = (
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

SUCCESSOR_REQUIRED_SEEDS = (
    "scripts/rehearse_p4_2a_v2_heldout_full_path.py",
    SUCCESSOR_PREREGISTRATION_RELATIVE.as_posix(),
    SUCCESSOR_SCHEMA_RELATIVE.as_posix(),
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
)

SUCCESSOR_FROZEN_CONTROL_SHA256 = {
    SUCCESSOR_PREREGISTRATION_RELATIVE.as_posix(): SUCCESSOR_PREREGISTRATION_SHA256,
    SUCCESSOR_SCHEMA_RELATIVE.as_posix(): SUCCESSOR_SCHEMA_SHA256,
    "docs/phase4/reports/P4.2a-v2-heldout-preregistration-20260810.json": (
        "ccecbf5ca7b48b16e445318b8c94a08927432f92c7e8c12f8ab40f2916578705"
    ),
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v1-incident-20260810.json": (
        "c3224b288f5181131351ae711a673ce94ec603375925d0cc968cef85d103e785"
    ),
    "config/p4_event_evaluation_v2.yaml": DESIGN_SHA256,
    "config/p4_event_extract_eval_v2-heldout-qwen3.6-plus.yaml": HELDOUT_CONTRACT_SHA256,
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
    "pyproject.toml": "b38481e57b0ba88d1b9b728c2a57583d55cf175262a8a803b483cf4823e13e29",
    "uv.lock": "10829f7ef74adfcbd4401000112b5539c899a899d09d8a3f78fdf8d95803a673",
}

RETIRED_V1_REFERENCES = (
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

run_select_blind = prepare.run_select_blind


class RehearsalError(RuntimeError):
    """The synthetic full-path rehearsal violated its frozen safety contract."""


@dataclass(frozen=True, slots=True)
class RehearsalEvidence:
    inputs_payload: bytes
    internal_artifact_sha256: Mapping[str, str]
    mock_model_calls: int
    selection_counts: Mapping[str, int]
    owner_chain_count: int
    formal_state_events: tuple[str, ...]
    synthetic_report_status: str


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _canonical_jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(_canonical_json_bytes(row) for row in rows)


def _read_json(path: Path) -> JsonObject:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RehearsalError(f"synthetic artifact is not a JSON object: {path.name}")
    return cast(JsonObject, value)


def _read_jsonl(path: Path) -> list[JsonObject]:
    rows: list[JsonObject] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value: object = json.loads(line)
        if not isinstance(value, dict):
            raise RehearsalError(
                f"synthetic artifact line is not an object: {path.name}:{line_number}"
            )
        rows.append(cast(JsonObject, value))
    return rows


def _regular_code_hashes(project_root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in TESTED_CODE_PATHS:
        path = project_root / relative
        if path.is_symlink() or not path.is_file():
            raise RehearsalError(f"tested code file is unavailable: {relative}")
        hashes[relative] = _sha256_file(path)
    return hashes


def _tree_fingerprint(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    if root.is_symlink() or not root.is_dir():
        raise RehearsalError(f"protected artifact root is not a regular directory: {root}")
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[f"symlink:{relative}"] = str(path.readlink())
        elif path.is_file():
            result[f"file:{relative}"] = _sha256_file(path)
        elif path.is_dir():
            result[f"directory:{relative}"] = "present"
    return result


def registered_rehearsal_directory(project_root: Path = PROJECT_ROOT) -> Path:
    root = project_root.resolve()
    binding = prepare.load_binding(root)
    registered = binding.artifacts["synthetic_rehearsal"]
    expected = (root / REGISTERED_REHEARSAL_RELATIVE).resolve()
    if registered != expected:
        raise RehearsalError("registered synthetic rehearsal directory drifted")
    return registered


def validate_rehearsal_gate(
    directory: Path,
    *,
    project_root: Path = PROJECT_ROOT,
) -> JsonObject:
    """Validate a published bundle with the exact materialization gate."""

    binding = prepare.load_binding(project_root.resolve())
    artifacts = dict(binding.artifacts)
    artifacts["synthetic_rehearsal"] = directory.resolve()
    return prepare._validate_full_path_rehearsal_gate(
        replace(binding, artifacts=artifacts)
    )


def _assert_publish_targets_absent(directory: Path) -> tuple[Path, Path, Path, Path]:
    names = ("contract.json", "inputs.jsonl", "expected.json", "pass-receipt.json")
    if directory.is_symlink():
        raise RehearsalError("registered rehearsal directory must not be a symlink")
    if directory.exists() and not directory.is_dir():
        raise RehearsalError("registered rehearsal path is not a directory")
    paths = tuple(directory / name for name in names)
    for path in paths:
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to overwrite create-only rehearsal artifact: {path}")
    if directory.exists() and any(directory.iterdir()):
        raise RehearsalError("registered rehearsal directory contains unregistered artifacts")
    return cast(tuple[Path, Path, Path, Path], paths)


def _workspace_artifacts(
    workspace: Path,
    source_binding: prepare.HeldoutBinding,
) -> dict[str, Path]:
    """Mirror every registered artifact path under the isolated temp root."""

    artifacts: dict[str, Path] = {}
    for name, source in source_binding.artifacts.items():
        if not source.is_relative_to(source_binding.root):
            raise RehearsalError(f"registered artifact escapes project root: {name}")
        artifacts[name] = workspace / source.relative_to(source_binding.root)
    return artifacts


def _reference_path(value: object, label: str) -> Path:
    if not isinstance(value, Mapping):
        raise RehearsalError(f"{label} is not a frozen file reference")
    raw = value.get("path")
    if not isinstance(raw, str) or not raw:
        raise RehearsalError(f"{label} has no frozen path")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise RehearsalError(f"{label} escapes the temporary control root")
    return relative


def _copy_control_surface(source_root: Path, workspace: Path) -> None:
    controls = evaluator.load_control_bundle(source_root)
    relative_paths = {
        evaluator.PREREGISTRATION_PATH,
        evaluator.DESIGN_PATH,
        evaluator.SELECTION_OUTCOME_PATH,
        evaluator.SELECTED_FREEZE_PATH,
        evaluator.HELDOUT_CONTRACT_PATH,
        evaluator.ROUND3_CONTRACT_PATH,
        evaluator.PROMPT_PATH,
        evaluator.OWNER_AMENDMENT_PATH,
        evaluator.COST_CORRECTION_PATH,
        dev_runner.ROUND_3_PREREGISTRATION_PATH,
        Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.3-r1.predictions.jsonl"),
        Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.3-r1.manifest.json"),
        Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.3-r1.report.json"),
        Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.3-r1.blocker.json"),
    }
    contract_files = cast(Mapping[str, Any], controls.heldout_contract["contract_files"])
    for name in ("schema", "materialized_schema"):
        relative_paths.add(_reference_path(contract_files[name], f"contract {name}"))
    frame = cast(
        Mapping[str, Any],
        cast(Mapping[str, Any], controls.design["frames"])["heldout_frame_v2"],
    )
    source_lineage = cast(Mapping[str, Any], frame["source_lineage"])
    for name in (
        "round3_evidence",
        "round3_independent_review",
        "incremental_evidence",
        "incremental_independent_review",
    ):
        relative_paths.add(_reference_path(source_lineage[name], f"source lineage {name}"))
    eligibility = cast(Mapping[str, Any], controls.preregistration["eligibility_and_sampling"])
    relative_paths.add(_reference_path(eligibility["retired_selection"], "retired selection"))
    frozen_hashes: dict[Path, str] = {}

    def collect_frozen_references(value: object) -> None:
        if isinstance(value, Mapping):
            path_value = value.get("path")
            sha_value = value.get("sha256")
            if isinstance(path_value, str) and isinstance(sha_value, str):
                relative = _reference_path(value, "design frozen reference")
                existing = frozen_hashes.setdefault(relative, sha_value)
                if existing != sha_value:
                    raise RehearsalError(
                        f"conflicting frozen hashes for design reference: {relative}"
                    )
                relative_paths.add(relative)
            for key, nested in value.items():
                if key.endswith("_path"):
                    sha_key = f"{key[:-5]}_sha256"
                    sibling_sha = value.get(sha_key)
                    if isinstance(nested, str) and isinstance(sibling_sha, str):
                        relative = _reference_path(
                            {"path": nested}, f"design frozen reference {key}"
                        )
                        existing = frozen_hashes.setdefault(relative, sibling_sha)
                        if existing != sibling_sha:
                            raise RehearsalError(
                                "conflicting frozen hashes for design reference: "
                                f"{relative}"
                            )
                        relative_paths.add(relative)
                collect_frozen_references(nested)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for nested in value:
                collect_frozen_references(nested)

    design_relative = evaluator.DESIGN_PATH
    seen_designs: set[Path] = set()
    while design_relative not in seen_designs:
        seen_designs.add(design_relative)
        relative_paths.add(design_relative)
        design_source = (source_root / design_relative).resolve()
        if (
            not design_source.is_relative_to(source_root)
            or design_source.is_symlink()
            or not design_source.is_file()
        ):
            raise RehearsalError(
                f"frozen evaluation-design ancestor is unavailable: {design_relative}"
            )
        document = yaml.safe_load(design_source.read_bytes())
        if not isinstance(document, Mapping):
            raise RehearsalError(
                f"frozen evaluation-design ancestor is invalid: {design_relative}"
            )
        collect_frozen_references(document)
        parent = document.get("extends_design")
        if not isinstance(parent, Mapping) or not isinstance(parent.get("path"), str):
            break
        design_relative = _reference_path(parent, "extends_design")

    round_preregistration_source = (
        source_root / dev_runner.ROUND_3_PREREGISTRATION_PATH
    ).resolve()
    if (
        not round_preregistration_source.is_relative_to(source_root)
        or round_preregistration_source.is_symlink()
        or not round_preregistration_source.is_file()
        or _sha256_file(round_preregistration_source)
        != dev_runner.ROUND_3_PREREGISTRATION_SHA256
    ):
        raise RehearsalError("Round 3 preregistration is unavailable or drifted")
    round_preregistration = json.loads(
        round_preregistration_source.read_text(encoding="utf-8")
    )
    if not isinstance(round_preregistration, Mapping):
        raise RehearsalError("Round 3 preregistration is invalid")
    collect_frozen_references(round_preregistration)

    payloads: list[tuple[Path, bytes]] = []
    for relative in sorted(relative_paths):
        source = (source_root / relative).resolve()
        target = (workspace / relative).resolve()
        if (
            not source.is_relative_to(source_root)
            or source.is_symlink()
            or not source.is_file()
            or not target.is_relative_to(workspace)
        ):
            raise RehearsalError(f"frozen control file is unavailable: {relative}")
        expected_hash = frozen_hashes.get(relative)
        if expected_hash is not None and _sha256_file(source) != expected_hash:
            raise RehearsalError(f"frozen control file hash drifted: {relative}")
        payloads.append((target, source.read_bytes()))
    prepare._publish_create_only(tuple(payloads))
    evaluator.load_control_bundle(workspace)


def _synthetic_rows() -> list[materializer.NewsRow]:
    rows: list[materializer.NewsRow] = []
    for offset in range(SYNTHETIC_COUNT):
        identifier = SYNTHETIC_ID_START + offset
        title = f"合成排练证据条目 {identifier}"
        rows.append(
            materializer.NewsRow(
                news_item_id=identifier,
                source="sina_company_news",
                ingested_symbol=f"{identifier:06d}",
                title=title,
                url=f"https://example.invalid/p4-2a-v2-rehearsal/{identifier}",
                published_at=datetime(2026, 8, 6, 0, 0, tzinfo=UTC),
                available_time=datetime(2026, 8, 6, 0, 1, tzinfo=UTC),
                content_hash=_sha256_bytes(f"synthetic-content-{identifier}".encode()),
                raw_payload={"synthetic_rehearsal": True},
            )
        )
    return rows


def _materialize(
    source_binding: prepare.HeldoutBinding,
    temp_binding: prepare.HeldoutBinding,
) -> bytes:
    rows = _synthetic_rows()

    def forbidden_pdf_fetch(*_args: object, **_kwargs: object) -> bytes:
        raise RehearsalError("synthetic materialization attempted network PDF access")

    def forbidden_pdf_extract(*_args: object, **_kwargs: object) -> materializer.ExtractedPdfText:
        raise RehearsalError("synthetic materialization attempted PDF extraction")

    legacy_design = materializer.load_evaluation_design(
        source_binding.root / "config/p4_event_evaluation_v1_7.yaml"
    )
    synthetic_design_document = copy.deepcopy(source_binding.design)
    synthetic_design_document["candidate_eligibility"] = copy.deepcopy(
        legacy_design.document["candidate_eligibility"]
    )
    synthetic_design = materializer.FrozenEvaluationDesign(
        path=source_binding.root / prepare.DESIGN_PATH,
        sha256=DESIGN_SHA256,
        document=synthetic_design_document,
        base_contract=legacy_design.base_contract,
    )
    result = materializer.materialize_heldout_candidate_inputs(
        rows,
        synthetic_design,
        source_binding.contract,
        pdf_fetcher=forbidden_pdf_fetch,
        pdf_text_extractor=forbidden_pdf_extract,
    )
    if (
        len(result.all_candidates) != SYNTHETIC_COUNT
        or len(result.eligible_records) != SYNTHETIC_COUNT
        or result.ineligible_candidates
    ):
        raise RehearsalError("synthetic materialization did not produce exactly 80 eligible rows")
    inputs_payload = _canonical_jsonl_bytes(result.eligible_records)
    manifest = {
        "schema_version": "p4.2a-v2-heldout-materialization-manifest-v1",
        "frame_id": prepare.FRAME_ID,
        "synthetic_rehearsal": True,
        "lineage": {
            "preregistration_sha256": PREREGISTRATION_SHA256,
            "design_sha256": DESIGN_SHA256,
            "heldout_contract_sha256": HELDOUT_CONTRACT_SHA256,
        },
        "artifacts": {
            "eligible_inputs_jsonl": {
                "path": temp_binding.artifacts["materialized_inputs"]
                .relative_to(temp_binding.root)
                .as_posix(),
                "sha256": _sha256_bytes(inputs_payload),
                "create_only": True,
            },
        },
        "counts": {
            "all_candidates": SYNTHETIC_COUNT,
            "eligible_candidates": SYNTHETIC_COUNT,
            "ineligible_candidates": 0,
        },
        "production_database": {"opened": False, "reads": 0, "writes": 0},
    }
    prepare._publish_create_only(
        (
            (temp_binding.artifacts["materialized_inputs"], inputs_payload),
            (
                temp_binding.artifacts["materialization_manifest"],
                _canonical_json_bytes(manifest),
            ),
        )
    )
    return inputs_payload


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        trading_mode="research",
        live_trading_enabled=False,
        paper_trading_enabled=False,
        paper_auto_trading_enabled=False,
        futu_enable_account_mutation=False,
        futu_enable_trade=False,
        llm_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        llm_api_key="synthetic-rehearsal-no-network",
        llm_model=prepare.MODEL,
    )


def _snapshot(symbols: frozenset[str]) -> dev_runner.ProductionSnapshot:
    return dev_runner.ProductionSnapshot(
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


def _run_inference(temp_binding: prepare.HeldoutBinding) -> list[int]:
    calls: list[int] = []
    symbols = frozenset(f"{SYNTHETIC_ID_START + offset:06d}" for offset in range(SYNTHETIC_COUNT))
    snapshot = _snapshot(symbols)

    def snapshot_loader(root: Path) -> dev_runner.ProductionSnapshot:
        if root != temp_binding.root:
            raise RehearsalError("synthetic snapshot loader received a non-temp root")
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
            raise RehearsalError("mocked model received a drifted one-item request")
        payload: object = json.loads(user)
        if not isinstance(payload, dict):
            raise RehearsalError("mocked model user payload is not an object")
        identifier_value = payload.get("news_item_id")
        candidates = payload.get("evidence_candidates")
        if (
            isinstance(identifier_value, bool)
            or not isinstance(identifier_value, int)
            or not isinstance(candidates, list)
            or not candidates
            or not isinstance(candidates[0], list)
            or len(candidates[0]) != 4
        ):
            raise RehearsalError("mocked model request is not one materialized item")
        identifier = identifier_value
        expected_identifier = SYNTHETIC_ID_START + len(calls)
        if identifier != expected_identifier:
            raise RehearsalError("mocked model calls are not ascending one-item requests")
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
        first = candidates[0]
        return {
            "symbols": [f"{identifier:06d}"],
            "event_type": "other",
            "direction": 0,
            "materiality": (2 if len(calls) <= SYNTHETIC_POSITIVE_COUNT else 1),
            "summary": "合成排练结构化抽取结果。",
            "confidence": 1.0,
            "evidence_candidate_id": first[0],
        }

    prepare.run_infer(
        temp_binding,
        settings=_settings(),
        chat_json_fn=mocked_model,
        snapshot_loader=snapshot_loader,
        clock=lambda: FIXED_WALL_CLOCK,
        execution_id_factory=lambda: str(
            uuid.uuid5(
                UUID_NAMESPACE,
                "inference_execution\0p4.2a-heldout-frame-v2-synthetic",
            )
        ),
    )
    expected_calls = list(range(SYNTHETIC_ID_START, SYNTHETIC_ID_START + SYNTHETIC_COUNT))
    if calls != expected_calls:
        raise RehearsalError("mocked inference did not call every candidate exactly once")
    return calls


def _adjudication_contract(
    source_contract: base_seal.V2AdjudicationContract,
    binding: prepare.HeldoutBinding,
) -> base_seal.V2AdjudicationContract:
    artifacts = {
        "development_private_selection_manifest": binding.artifacts["private_selection"],
        "development_owner_blind_jsonl": binding.artifacts["owner_blind"],
        "development_ai_draft_jsonl": binding.artifacts["ai_draft"],
        "development_adjudication_html": binding.artifacts["adjudication_ui"],
        "development_owner_raw_export_jsonl": binding.artifacts["owner_export"],
        "development_human_adjudicated_jsonl": binding.artifacts["human_adjudicated"],
        "development_owner_completion_manifest": binding.artifacts["owner_completion"],
    }
    return replace(source_contract, project_root=binding.root, artifacts=artifacts)


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
    rows: list[JsonObject] = []
    for index, (blind, draft) in enumerate(zip(blind_rows, draft_rows, strict=True), 1):
        draft_label = copy.deepcopy(draft["draft_label"])
        rows.append(
            {
                "schema_version": "p4.2a-v2-owner-adjudication-export-item-v1",
                "design": dict(contract.design_ref),
                "frame_id": contract.frame_id,
                "sample_index": index,
                "news_item_id": blind["news_item_id"],
                "input_sha256": blind["input_sha256"],
                "sealed_draft_item_sha256": _sha256_bytes(base_seal.canonical_json_bytes(draft)),
                "draft_label": draft_label,
                "human_label": copy.deepcopy(draft_label),
                "annotation_status": "adjudicated",
                "adjudication": {
                    "method": "ai_drafted_human_adjudicated",
                    "drafter_id": SYNTHETIC_DRAFTER,
                    "adjudicator_id": SYNTHETIC_ADJUDICATOR,
                    "confirmed": True,
                    "changed": False,
                    "changed_fields": [],
                    "adjudicated_at": SYNTHETIC_ADJUDICATED_AT,
                },
            }
        )
    return rows


def _run_owner_chain(
    binding: prepare.HeldoutBinding,
    *,
    contract: base_seal.V2AdjudicationContract,
) -> int:
    (
        blind_rows,
        blind_payload,
        selection_payload,
        inference_completed_at,
    ) = heldout_seal.read_bound_blind_bundle(
        binding.artifacts["private_selection"],
        binding.artifacts["owner_blind"],
        contract=contract,
    )
    sealed = heldout_seal.seal_candidate_rows(
        blind_rows,
        _candidate_drafts(blind_rows),
        contract=contract,
        drafter_id=SYNTHETIC_DRAFTER,
        drafted_at=SYNTHETIC_DRAFTED_AT,
        inference_completed_at=inference_completed_at,
    )
    draft_payload = base_seal.canonical_jsonl_bytes(sealed)
    base_seal.write_create_only(binding.artifacts["ai_draft"], draft_payload)
    ui_payload, ui_count = heldout_ui.render_registered_ui_payload(
        blind_rows,
        sealed,
        contract=contract,
        blind_payload=blind_payload,
        draft_payload=draft_payload,
        selection_payload=selection_payload,
    )
    if ui_count != OWNER_CHAIN_COUNT:
        raise RehearsalError("synthetic owner UI does not contain 60 rows")
    base_seal.write_create_only(binding.artifacts["adjudication_ui"], ui_payload)
    export_rows = _owner_export(blind_rows, sealed, contract=contract)
    owner_payload = base_seal.canonical_jsonl_bytes(export_rows)
    candidate_export = binding.root / "owner-export-candidate.jsonl"
    base_seal.write_create_only(candidate_export, owner_payload)
    summary, _completion, _hashes = heldout_finalizer.finalize_owner_export(
        contract=contract,
        owner_export_path=candidate_export,
        completed_at=SYNTHETIC_COMPLETED_AT,
    )
    if summary.get("row_count") != OWNER_CHAIN_COUNT:
        raise RehearsalError("heldout finalizer did not freeze all 60 owner rows")
    return int(summary["row_count"])


def _evaluation_paths(binding: prepare.HeldoutBinding) -> evaluator.ArtifactPaths:
    return evaluator.ArtifactPaths(
        artifact_root=binding.root,
        materialized_inputs=binding.artifacts["materialized_inputs"],
        materialization_manifest=binding.artifacts["materialization_manifest"],
        inference_state=binding.artifacts["inference_state"],
        predictions=binding.artifacts["predictions"],
        prediction_manifest=binding.artifacts["prediction_manifest"],
        selection=binding.artifacts["private_selection"],
        blind=binding.artifacts["owner_blind"],
        draft=binding.artifacts["ai_draft"],
        adjudication_ui=binding.artifacts["adjudication_ui"],
        owner_export=binding.artifacts["owner_export"],
        human_adjudicated=binding.artifacts["human_adjudicated"],
        owner_completion=binding.artifacts["owner_completion"],
        evaluation_state=binding.artifacts["evaluation_state"],
        report=binding.artifacts["report_directory"] / evaluator.REPORT_FILENAME,
    )


def _fixed_clock() -> datetime:
    return datetime(2026, 8, 10, 8, 15, tzinfo=UTC)


def _formal_synthetic_evaluate(
    *,
    project_root: Path,
    paths: evaluator.ArtifactPaths,
    clock: Clock,
) -> tuple[JsonObject, tuple[str, ...]]:
    dry_run = evaluator.dry_run(root=project_root, paths=paths, clock=clock)
    if (
        dry_run.get("status") != "passed"
        or dry_run.get("real_heldout_metrics_computed") is not False
        or dry_run.get("filesystem_mutations") != 0
    ):
        raise RehearsalError("synthetic evaluator dry-run did not pass safely")
    preflight = evaluator.load_preflight(root=project_root, paths=paths)
    started_at = evaluator._utc_now(clock)
    started = {
        "schema_version": "p4.2a-v2-heldout-evaluation-state-event-v1",
        "event": "evaluation_started",
        "at_utc": started_at,
        "synthetic_rehearsal": True,
        "design_sha256": DESIGN_SHA256,
        "preregistration_sha256": PREREGISTRATION_SHA256,
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
        completed_at=evaluator._utc_now(clock),
        authorization=None,
        synthetic=True,
    )
    report_payload = evaluator._canonical_json_bytes(report)
    evaluator._create_only(paths.report, report_payload)
    terminal = {
        "schema_version": "p4.2a-v2-heldout-evaluation-state-event-v1",
        "event": "evaluation_completed",
        "at_utc": evaluator._utc_now(clock),
        "synthetic_rehearsal": True,
        "real_heldout_metrics_computed": False,
        "one_shot_consumed": False,
        "report_path": paths.report.relative_to(project_root).as_posix(),
        "report_sha256": _sha256_bytes(report_payload),
        "retries": 0,
    }
    evaluator._append_terminal(paths.evaluation_state, terminal)
    events = tuple(str(row.get("event")) for row in _read_jsonl(paths.evaluation_state))
    if events != ("evaluation_started", "evaluation_completed"):
        raise RehearsalError("synthetic formal evaluator state did not terminalize")
    if (
        report.get("status") != "synthetic_rehearsal"
        or report.get("real_heldout_metrics_computed") is not False
        or cast(Mapping[str, Any], report.get("safety", {})).get("one_shot_consumed") is not False
    ):
        raise RehearsalError("synthetic formal evaluator report disclosed real results")
    return report, events


def _internal_hashes(binding: prepare.HeldoutBinding) -> dict[str, str]:
    paths = {
        "materialized_inputs": binding.artifacts["materialized_inputs"],
        "materialization_manifest": binding.artifacts["materialization_manifest"],
        "inference_state": binding.artifacts["inference_state"],
        "predictions": binding.artifacts["predictions"],
        "prediction_manifest": binding.artifacts["prediction_manifest"],
        "private_selection": binding.artifacts["private_selection"],
        "owner_blind": binding.artifacts["owner_blind"],
        "ai_draft": binding.artifacts["ai_draft"],
        "adjudication_ui": binding.artifacts["adjudication_ui"],
        "owner_export": binding.artifacts["owner_export"],
        "human_adjudicated": binding.artifacts["human_adjudicated"],
        "owner_completion": binding.artifacts["owner_completion"],
        "evaluation_state": binding.artifacts["evaluation_state"],
        "synthetic_report": (binding.artifacts["report_directory"] / evaluator.REPORT_FILENAME),
    }
    if any(path.is_symlink() or not path.is_file() for path in paths.values()):
        raise RehearsalError("synthetic full path did not create every temp artifact")
    return {name: _sha256_file(path) for name, path in paths.items()}


def _execute_temp_pipeline_inner(
    *,
    project_root: Path,
    workspace: Path,
    source_binding: prepare.HeldoutBinding,
    clock: Clock,
) -> RehearsalEvidence:
    if workspace.is_relative_to(project_root):
        raise RehearsalError("synthetic workspace must be outside the project root")
    artifacts = _workspace_artifacts(workspace, source_binding)
    if any(not path.is_relative_to(workspace) for path in artifacts.values()):
        raise RehearsalError("synthetic artifact escaped the temporary workspace")
    _copy_control_surface(project_root, workspace)
    temp_binding = replace(
        source_binding,
        root=workspace,
        artifacts=artifacts,
        retired_ids=frozenset(),
    )
    inputs_payload = _materialize(source_binding, temp_binding)
    calls = _run_inference(temp_binding)
    run_select_blind(temp_binding)
    candidates = _read_jsonl(temp_binding.artifacts["materialized_inputs"])
    predictions = _read_jsonl(temp_binding.artifacts["predictions"])
    repeated = prepare.select_and_blind(temp_binding, candidates, predictions)
    if (
        _canonical_json_bytes(repeated.manifest)
        != temp_binding.artifacts["private_selection"].read_bytes()
        or _canonical_jsonl_bytes(repeated.blind_rows)
        != temp_binding.artifacts["owner_blind"].read_bytes()
    ):
        raise RehearsalError("40/20 selection or blind owner order is not deterministic")
    source_adjudication_contract = heldout_seal.load_registered_contract(project_root=project_root)
    contract = _adjudication_contract(source_adjudication_contract, temp_binding)
    owner_count = _run_owner_chain(temp_binding, contract=contract)
    report, state_events = _formal_synthetic_evaluate(
        project_root=workspace,
        paths=_evaluation_paths(temp_binding),
        clock=clock,
    )
    selection = _read_json(temp_binding.artifacts["private_selection"])
    selection_body = cast(Mapping[str, Any], selection["selection"])
    selected_counts = cast(Mapping[str, Any], selection_body["selected_counts"])
    counts = {
        "predicted_positive": int(selected_counts["predicted_positive"]),
        "predicted_negative": int(selected_counts["predicted_negative"]),
        "total": int(selected_counts["total"]),
    }
    return RehearsalEvidence(
        inputs_payload=inputs_payload,
        internal_artifact_sha256=_internal_hashes(temp_binding),
        mock_model_calls=len(calls),
        selection_counts=counts,
        owner_chain_count=owner_count,
        formal_state_events=state_events,
        synthetic_report_status=str(report["status"]),
    )


class _DeterministicMonotonic:
    def __init__(self) -> None:
        self._next = MONOTONIC_INITIAL_NS

    def __call__(self) -> int:
        value = self._next
        self._next += MONOTONIC_STEP_NS
        return value


def _execute_temp_pipeline(
    *,
    project_root: Path,
    workspace: Path,
    source_binding: prepare.HeldoutBinding,
    clock: Clock,
) -> RehearsalEvidence:
    monotonic = _DeterministicMonotonic()
    with (
        patch.object(
            offline_extract,
            "_utc_now",
            lambda: FIXED_WALL_CLOCK.isoformat(),
        ),
        patch.object(offline_extract, "monotonic_ns", monotonic),
    ):
        return _execute_temp_pipeline_inner(
            project_root=project_root,
            workspace=workspace,
            source_binding=source_binding,
            clock=clock,
        )


def _contract_payload(tested_code: Mapping[str, str]) -> bytes:
    return _canonical_json_bytes(
        {
            "schema_version": "p4.2a-v2-heldout-full-path-rehearsal-contract-v1",
            "preregistration_sha256": PREREGISTRATION_SHA256,
            "design_sha256": DESIGN_SHA256,
            "heldout_contract_sha256": HELDOUT_CONTRACT_SHA256,
            "fixture": {
                "synthetic_candidate_count": SYNTHETIC_COUNT,
                "predicted_positive_pool_count": SYNTHETIC_POSITIVE_COUNT,
                "predicted_negative_pool_count": SYNTHETIC_NEGATIVE_COUNT,
            },
            "request_contract": {
                "one_news_item_per_request": True,
                "one_request_per_eligible_candidate": True,
                "automatic_retries": 0,
            },
            "selection_counts": {
                "predicted_positive": SELECTED_POSITIVE_COUNT,
                "predicted_negative": SELECTED_NEGATIVE_COUNT,
                "total": OWNER_CHAIN_COUNT,
            },
            "workspace_policy": "temporary_and_outside_registered_artifact_roots",
            "network_allowed": False,
            "production_database_allowed": False,
            "production_heldout_artifact_writes_allowed": False,
            "real_model_calls_allowed": 0,
            "real_heldout_metrics_allowed": False,
            "tested_code_sha256": dict(tested_code),
        }
    )


def _expected_payload() -> bytes:
    return _canonical_json_bytes(
        {
            "schema_version": "p4.2a-v2-heldout-full-path-rehearsal-expected-v1",
            "materialized_candidate_count": SYNTHETIC_COUNT,
            "inference_candidate_count": SYNTHETIC_COUNT,
            "mock_model_call_count": SYNTHETIC_COUNT,
            "one_item_model_call_count": SYNTHETIC_COUNT,
            "selection_counts": {
                "predicted_positive": SELECTED_POSITIVE_COUNT,
                "predicted_negative": SELECTED_NEGATIVE_COUNT,
                "total": OWNER_CHAIN_COUNT,
            },
            "blind_row_count": OWNER_CHAIN_COUNT,
            "draft_row_count": OWNER_CHAIN_COUNT,
            "owner_chain_count": OWNER_CHAIN_COUNT,
            "formal_state_events": ["evaluation_started", "evaluation_completed"],
            "synthetic_report_status": "synthetic_rehearsal",
            "real_heldout_metrics_computed": False,
            "production_writes": False,
            "real_database_reads": 0,
            "real_network_calls": 0,
            "real_model_calls": 0,
        }
    )


def run_rehearsal(
    *,
    project_root: Path = PROJECT_ROOT,
    publish_directory: Path | None = None,
    workspace_parent: Path | None = None,
    clock: Clock = _fixed_clock,
) -> Path:
    """Execute the offline rehearsal and create the four result files atomically.

    ``publish_directory`` and ``workspace_parent`` exist solely for isolated
    automated tests.  The CLI exposes neither override.
    """

    root = project_root.resolve()
    registered_directory = registered_rehearsal_directory(root)
    destination = (publish_directory or registered_directory).resolve()
    contract_path, inputs_path, expected_path, receipt_path = _assert_publish_targets_absent(
        destination
    )
    protected_root = (root / "docs/phase4/eval/v2-calibration/heldout").resolve()
    production_before = _tree_fingerprint(protected_root)
    code_before = _regular_code_hashes(root)
    source_binding = prepare.load_binding(root)
    contract_payload = _contract_payload(code_before)
    expected_payload = _expected_payload()

    parent: Path | None = workspace_parent.resolve() if workspace_parent else None
    if parent is not None:
        if parent.is_relative_to(root) or parent.is_relative_to(destination):
            raise RehearsalError("temporary workspace parent overlaps a registered root")
        parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="alphapilot-p4-2a-v2-full-rehearsal-",
        dir=str(parent) if parent else None,
    ) as temporary:
        workspace = Path(temporary).resolve()
        evidence = _execute_temp_pipeline(
            project_root=root,
            workspace=workspace,
            source_binding=source_binding,
            clock=clock,
        )
    if workspace.exists():
        raise RehearsalError("temporary rehearsal workspace was not removed")
    code_after = _regular_code_hashes(root)
    if code_after != code_before:
        raise RehearsalError("tested code changed during the rehearsal")
    production_after = _tree_fingerprint(protected_root)
    if production_after != production_before:
        raise RehearsalError("production held-out artifacts changed during rehearsal")
    if evidence.mock_model_calls != SYNTHETIC_COUNT:
        raise RehearsalError("synthetic model call count drifted")
    expected_counts = {
        "predicted_positive": SELECTED_POSITIVE_COUNT,
        "predicted_negative": SELECTED_NEGATIVE_COUNT,
        "total": OWNER_CHAIN_COUNT,
    }
    if dict(evidence.selection_counts) != expected_counts:
        raise RehearsalError("synthetic selection counts drifted")
    published_hashes = {
        "contract.json": _sha256_bytes(contract_payload),
        "inputs.jsonl": _sha256_bytes(evidence.inputs_payload),
        "expected.json": _sha256_bytes(expected_payload),
    }
    receipt = {
        "schema_version": "p4.2a-v2-heldout-full-path-rehearsal-pass-receipt-v1",
        "status": "passed",
        "full_path_covered": True,
        "materialization_gate_unlock": True,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "design_sha256": DESIGN_SHA256,
        "heldout_contract_sha256": HELDOUT_CONTRACT_SHA256,
        "published_artifact_sha256": published_hashes,
        "tested_code_sha256": code_after,
        "internal_artifact_sha256": dict(evidence.internal_artifact_sha256),
        "materialized_candidate_count": SYNTHETIC_COUNT,
        "inference_candidate_count": SYNTHETIC_COUNT,
        "one_item_model_call_count": SYNTHETIC_COUNT,
        "mock_model_calls": evidence.mock_model_calls,
        "selection_counts": expected_counts,
        "owner_chain_count": evidence.owner_chain_count,
        "formal_state_events": list(evidence.formal_state_events),
        "synthetic_report_status": evidence.synthetic_report_status,
        "production_writes": False,
        "production_heldout_artifacts_changed": False,
        "real_database_reads": 0,
        "real_network_calls": 0,
        "real_model_calls": 0,
        "real_heldout_metrics_computed": False,
        "real_metrics_disclosed": False,
        "temporary_workspace_removed": True,
    }
    receipt_payload = _canonical_json_bytes(receipt)
    prepare._publish_create_only(
        (
            (contract_path, contract_payload),
            (inputs_path, evidence.inputs_payload),
            (expected_path, expected_payload),
            (receipt_path, receipt_payload),
        )
    )
    if sorted(path.name for path in destination.iterdir()) != [
        "contract.json",
        "expected.json",
        "inputs.jsonl",
        "pass-receipt.json",
    ]:
        raise RehearsalError("registered rehearsal publication contains unexpected files")
    return receipt_path


@dataclass(frozen=True, slots=True)
class GitBinding:
    implementation_commit: str
    blob_reader: Callable[[str], bytes]
    commit_exists: Callable[[], bool]
    required_ancestor_present: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class SuccessorRun:
    label: str
    artifacts: Mapping[str, bytes]
    repository_reads: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class SuccessorPreflight:
    repository_payloads: Mapping[str, bytes]
    repository_sha256: Mapping[str, str]
    ast_closure_paths: tuple[str, ...]
    python_inventory_sha256: str
    package_inventory_sha256: str
    publication_device: int


@dataclass(frozen=True, slots=True)
class RegisteredBootstrap:
    binding: GitBinding
    preflight: SuccessorPreflight


_REGISTERED_BOOTSTRAP_TOKEN = object()
_registered_bootstrap_state: tuple[object, RegisteredBootstrap] | None = None


def _git_command(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=False,
        capture_output=True,
    )


def _production_git_binding(root: Path) -> GitBinding:
    head = _git_command(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise RehearsalError("cannot resolve successor implementation commit")
    commit = head.stdout.decode("ascii", errors="strict").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RehearsalError("successor implementation commit is invalid")

    def blob_reader(relative: str) -> bytes:
        kind = _git_command(root, "cat-file", "-t", f"{commit}:{relative}")
        if kind.returncode != 0 or kind.stdout.strip() != b"blob":
            raise RehearsalError(
                f"implementation commit has no regular blob for control file: {relative}"
            )
        result = _git_command(root, "show", f"{commit}:{relative}")
        if result.returncode != 0:
            raise RehearsalError(
                f"implementation commit has no regular blob for control file: {relative}"
            )
        return result.stdout

    def commit_exists() -> bool:
        return _git_command(root, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0

    def ancestor_present() -> bool:
        return (
            _git_command(
                root, "merge-base", "--is-ancestor", V1_FAIL_CLOSE_COMMIT, commit
            ).returncode
            == 0
        )

    return GitBinding(
        implementation_commit=commit,
        blob_reader=blob_reader,
        commit_exists=commit_exists,
        required_ancestor_present=ancestor_present,
    )


def _assert_git_binding(binding: GitBinding) -> None:
    if (
        not re.fullmatch(r"[0-9a-f]{40}", binding.implementation_commit)
        or not binding.commit_exists()
        or not binding.required_ancestor_present()
    ):
        raise RehearsalError(
            "successor implementation commit is missing or does not descend from v1 fail-close"
        )


def _module_name(relative: str) -> tuple[str, str]:
    path = Path(relative)
    if path.parts[0] == "scripts":
        components = list(path.with_suffix("").parts)
    elif path.parts[:2] == ("src", "alphapilot"):
        components = list(path.with_suffix("").parts[1:])
    else:
        raise RehearsalError(f"local Python source is outside registered namespaces: {relative}")
    is_package = components[-1] == "__init__"
    if is_package:
        components.pop()
    module = ".".join(components)
    package = module if is_package else module.rpartition(".")[0]
    return module, package


def _ancestor_initializers(relative: str, blob_reader: Callable[[str], bytes]) -> set[str]:
    path = Path(relative)
    if path.parts[0] == "scripts":
        start = 1
    elif path.parts[:2] == ("src", "alphapilot"):
        start = 2
    else:
        return set()
    result: set[str] = set()
    parent_parts = path.parent.parts
    for length in range(start, len(parent_parts) + 1):
        candidate = Path(*parent_parts[:length]) / "__init__.py"
        relative_candidate = candidate.as_posix()
        try:
            blob_reader(relative_candidate)
        except RehearsalError:
            continue
        result.add(relative_candidate)
    return result


def _resolve_import_from(package: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    components = package.split(".") if package else []
    remove = node.level - 1
    if remove > len(components):
        raise RehearsalError("relative local import escapes its package")
    prefix = components[: len(components) - remove]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def _resolved_local_module_paths(
    module_name: str,
    *,
    blob_reader: Callable[[str], bytes],
    unresolved_is_error: bool,
) -> set[str]:
    if module_name not in {"scripts", "alphapilot"} and not module_name.startswith(
        ("scripts.", "alphapilot.")
    ):
        return set()
    candidate = _module_file_from_reader(module_name, blob_reader)
    if candidate is None:
        if unresolved_is_error and module_name != "scripts":
            raise RehearsalError(
                f"unresolved local import in implementation commit: {module_name}"
            )
        return set()
    return {candidate, *_ancestor_initializers(candidate, blob_reader)}


def _local_import_closure(
    *,
    entrypoint: str,
    blob_reader: Callable[[str], bytes],
) -> dict[str, bytes]:
    pending = [entrypoint]
    payloads: dict[str, bytes] = {}
    while pending:
        relative = pending.pop(0)
        if relative in payloads:
            continue
        payload = blob_reader(relative)
        try:
            tree = ast.parse(payload, filename=relative)
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise RehearsalError(f"cannot parse archived local Python source: {relative}") from exc
        payloads[relative] = payload
        _module, package = _module_name(relative)
        discovered: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    discovered.update(
                        _resolved_local_module_paths(
                            alias.name,
                            blob_reader=blob_reader,
                            unresolved_is_error=True,
                        )
                    )
                continue
            elif isinstance(node, ast.ImportFrom):
                base = _resolve_import_from(package, node)
                if base:
                    discovered.update(
                        _resolved_local_module_paths(
                            base,
                            blob_reader=blob_reader,
                            unresolved_is_error=True,
                        )
                    )
                for alias in node.names:
                    if alias.name != "*":
                        discovered.update(
                            _resolved_local_module_paths(
                                f"{base}.{alias.name}" if base else alias.name,
                                blob_reader=blob_reader,
                                unresolved_is_error=False,
                            )
                        )
                continue
            else:
                if isinstance(node, ast.Call):
                    name = ""
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    target = (
                        node.args[0].value
                        if node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                        else None
                    )
                    if name in {"__import__", "import_module"}:
                        if not isinstance(target, str):
                            raise RehearsalError(
                                "non-literal dynamic import cannot be proven non-local in "
                                f"replay closure: {relative}"
                            )
                        if target in {"scripts", "alphapilot"} or target.startswith(
                            ("scripts.", "alphapilot.")
                        ):
                            raise RehearsalError(
                                f"runtime dynamic import is forbidden in replay closure: {relative}"
                            )
                continue
        pending.extend(sorted(discovered - payloads.keys()))
    return dict(sorted(payloads.items()))


def _module_file_from_reader(
    module_name: str,
    blob_reader: Callable[[str], bytes],
) -> str | None:
    if module_name == "scripts":
        return None
    if module_name.startswith("scripts."):
        stem = "scripts/" + module_name.removeprefix("scripts.").replace(".", "/")
    elif module_name == "alphapilot":
        stem = "src/alphapilot"
    elif module_name.startswith("alphapilot."):
        stem = "src/alphapilot/" + module_name.removeprefix("alphapilot.").replace(".", "/")
    else:
        return None
    found: list[str] = []
    for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
        try:
            blob_reader(candidate)
        except RehearsalError:
            continue
        found.append(candidate)
    if len(found) > 1:
        raise RehearsalError(f"ambiguous local import in implementation commit: {module_name}")
    return found[0] if found else None


def _lexical_absolute_path(raw_path: object) -> Path | None:
    if isinstance(raw_path, int) or not isinstance(raw_path, (str, bytes, os.PathLike)):
        return None
    try:
        decoded = Path(os.fsdecode(raw_path))
        return Path(os.path.abspath(decoded))
    except (TypeError, ValueError, OSError):
        return None


def _repository_read_is_forbidden(relative: str) -> bool:
    lowered = relative.casefold()
    name = PurePosixPath(relative).name.casefold()
    return (
        name == ".env"
        or name.startswith(".env.")
        or name.endswith((".db", ".sqlite", ".sqlite3", ".pem", ".key"))
        or any(token in lowered for token in ("secret", "credential"))
    )


@contextmanager
def _trace_repository_reads(
    root: Path,
    *,
    blob_reader: Callable[[str], bytes],
    allowed_write_root: Path,
) -> Iterator[dict[str, str]]:
    reads: dict[str, str] = {}
    reading_commit_blob = False
    original_builtin_open: Any = builtins.open
    original_io_open: Any = io.open
    original_os_open: Any = os.open
    original_mkdir: Any = os.mkdir
    original_remove: Any = os.remove
    original_unlink: Any = os.unlink
    original_rmdir: Any = os.rmdir
    original_rename: Any = os.rename
    original_replace: Any = os.replace
    original_chmod: Any = os.chmod
    original_chown: Any = os.chown
    original_chflags: Any = os.chflags
    original_lchflags: Any = os.lchflags
    original_lchmod: Any = os.lchmod
    original_lchown: Any = os.lchown
    original_fchmod: Any = os.fchmod
    original_fchown: Any = os.fchown
    original_ftruncate: Any = os.ftruncate
    original_write: Any = os.write
    original_pwrite: Any = os.pwrite
    original_truncate: Any = os.truncate
    original_utime: Any = os.utime
    original_link: Any = os.link

    resolved_root = root.resolve()
    resolved_write_root = allowed_write_root.resolve()

    def require_allowed_descriptor(descriptor: int) -> None:
        try:
            raw_path = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0]
            lexical = Path(os.fsdecode(raw_path))
            resolved = lexical.resolve(strict=True)
        except (OSError, TypeError, ValueError) as exc:
            raise RehearsalError("successor rehearsal used an unbound write descriptor") from exc
        if lexical != resolved or not (
            resolved == resolved_write_root or resolved.is_relative_to(resolved_write_root)
        ):
            raise RehearsalError("successor rehearsal write descriptor escapes its temp root")

    def require_allowed_write(raw_path: object, *, dir_fd: object = None) -> None:
        if dir_fd is not None:
            raise RehearsalError("successor rehearsal attempted a dir-fd filesystem mutation")
        lexical = _lexical_absolute_path(raw_path)
        if lexical is None or not (
            lexical == resolved_write_root or lexical.is_relative_to(resolved_write_root)
        ):
            raise RehearsalError("successor rehearsal attempted a write outside its temp root")
        existing = lexical
        while not existing.exists() and not existing.is_symlink():
            if existing == existing.parent:
                raise RehearsalError("successor write target has no existing parent")
            existing = existing.parent
        try:
            resolved_existing = existing.resolve(strict=True)
        except OSError as exc:
            raise RehearsalError("successor write target parent is unavailable") from exc
        if resolved_existing != existing:
            raise RehearsalError("successor write target traverses a symbolic link")
        if not (
            resolved_existing == resolved_write_root
            or resolved_existing.is_relative_to(resolved_write_root)
        ):
            raise RehearsalError("successor write target escapes its temp root through a symlink")

    def record(raw_path: object) -> None:
        nonlocal reading_commit_blob
        lexical = _lexical_absolute_path(raw_path)
        if lexical is None:
            return
        if lexical == resolved_write_root or lexical.is_relative_to(resolved_write_root):
            try:
                resolved = lexical.resolve(strict=True)
            except OSError:
                return
            if resolved != lexical or not (
                resolved == resolved_write_root
                or resolved.is_relative_to(resolved_write_root)
            ):
                raise RehearsalError("temp-root read escapes through a symlink")
            return
        if not lexical.is_relative_to(resolved_root):
            raise RehearsalError(f"successor rehearsal attempted an external read: {lexical}")
        try:
            resolved = lexical.resolve(strict=True)
        except OSError as exc:
            raise RehearsalError(
                "successor rehearsal attempted an unavailable repository read"
            ) from exc
        if resolved != lexical or not resolved.is_relative_to(resolved_root):
            raise RehearsalError("repository read escapes through a symlink")
        if not resolved.is_file():
            return
        relative = resolved.relative_to(resolved_root).as_posix()
        if _repository_read_is_forbidden(relative):
            raise RehearsalError(f"successor rehearsal touched forbidden input: {relative}")
        metadata = resolved.lstat()
        if metadata.st_nlink != 1:
            raise RehearsalError(f"repository read is hard-linked: {relative}")
        with original_builtin_open(resolved, "rb") as handle:
            payload = handle.read()
        reading_commit_blob = True
        try:
            blob = blob_reader(relative)
        finally:
            reading_commit_blob = False
        if payload != blob:
            raise RehearsalError(f"repository read differs from implementation commit: {relative}")
        digest = _sha256_bytes(payload)
        prior = reads.setdefault(relative, digest)
        if prior != digest:
            raise RehearsalError(f"repository file changed between reads: {relative}")

    def traced_builtin_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if reading_commit_blob:
            return original_builtin_open(file, mode, *args, **kwargs)
        write_mode = any(flag in mode for flag in ("w", "a", "x", "+"))
        if kwargs.get("opener") is not None and not write_mode:
            raise RehearsalError("successor rehearsal attempted a custom read opener")
        if isinstance(file, int):
            if write_mode:
                require_allowed_descriptor(file)
            return original_builtin_open(file, mode, *args, **kwargs)
        if write_mode:
            require_allowed_write(file)
        else:
            record(file)
        return original_builtin_open(file, mode, *args, **kwargs)

    def traced_io_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if reading_commit_blob:
            return original_io_open(file, mode, *args, **kwargs)
        write_mode = any(flag in mode for flag in ("w", "a", "x", "+"))
        if kwargs.get("opener") is not None and not write_mode:
            raise RehearsalError("successor rehearsal attempted a custom read opener")
        if isinstance(file, int):
            if write_mode:
                require_allowed_descriptor(file)
            return original_io_open(file, mode, *args, **kwargs)
        if write_mode:
            require_allowed_write(file)
        else:
            record(file)
        return original_io_open(file, mode, *args, **kwargs)

    def traced_os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if reading_commit_blob:
            return cast(int, original_os_open(path, flags, *args, **kwargs))
        if kwargs.get("dir_fd") is not None:
            raise RehearsalError("successor rehearsal attempted a dir-fd filesystem access")
        write_flags = os.O_CREAT | os.O_TRUNC | os.O_APPEND
        if (flags & os.O_ACCMODE) != os.O_RDONLY or flags & write_flags:
            require_allowed_write(path, dir_fd=kwargs.get("dir_fd"))
        else:
            record(path)
        return cast(int, original_os_open(path, flags, *args, **kwargs))

    def guarded_mkdir(path: Any, *args: Any, **kwargs: Any) -> None:
        require_allowed_write(path, dir_fd=kwargs.get("dir_fd"))
        original_mkdir(path, *args, **kwargs)

    def guarded_unary(original: Callable[..., Any], path: Any, *args: Any, **kwargs: Any) -> Any:
        require_allowed_write(path, dir_fd=kwargs.get("dir_fd"))
        return original(path, *args, **kwargs)

    def guarded_binary(
        original: Callable[..., Any],
        source: Any,
        destination: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        require_allowed_write(
            source,
            dir_fd=kwargs.get("src_dir_fd"),
        )
        require_allowed_write(
            destination,
            dir_fd=kwargs.get("dst_dir_fd"),
        )
        return original(source, destination, *args, **kwargs)

    def forbidden_symlink(*_args: object, **_kwargs: object) -> NoReturn:
        raise RehearsalError("successor rehearsal attempted to create a symbolic link")

    def forbidden_special_file(*_args: object, **_kwargs: object) -> NoReturn:
        raise RehearsalError("successor rehearsal attempted to create a special file")

    def guarded_descriptor(
        original: Callable[..., Any], descriptor: int, *args: Any, **kwargs: Any
    ) -> Any:
        require_allowed_descriptor(descriptor)
        return original(descriptor, *args, **kwargs)

    replacements = (
        (builtins, "open", traced_builtin_open),
        (io, "open", traced_io_open),
        (os, "open", traced_os_open),
        (os, "mkdir", guarded_mkdir),
        (os, "remove", partial(guarded_unary, original_remove)),
        (os, "unlink", partial(guarded_unary, original_unlink)),
        (os, "rmdir", partial(guarded_unary, original_rmdir)),
        (os, "rename", partial(guarded_binary, original_rename)),
        (os, "replace", partial(guarded_binary, original_replace)),
        (os, "chmod", partial(guarded_unary, original_chmod)),
        (os, "chown", partial(guarded_unary, original_chown)),
        (os, "chflags", partial(guarded_unary, original_chflags)),
        (os, "lchflags", partial(guarded_unary, original_lchflags)),
        (os, "lchmod", partial(guarded_unary, original_lchmod)),
        (os, "lchown", partial(guarded_unary, original_lchown)),
        (os, "fchmod", partial(guarded_descriptor, original_fchmod)),
        (os, "fchown", partial(guarded_descriptor, original_fchown)),
        (os, "ftruncate", partial(guarded_descriptor, original_ftruncate)),
        (os, "write", partial(guarded_descriptor, original_write)),
        (os, "pwrite", partial(guarded_descriptor, original_pwrite)),
        (os, "truncate", partial(guarded_unary, original_truncate)),
        (os, "utime", partial(guarded_unary, original_utime)),
        (os, "link", partial(guarded_binary, original_link)),
        (os, "symlink", forbidden_symlink),
        (os, "mkfifo", forbidden_special_file),
        (os, "mknod", forbidden_special_file),
    )
    with ExitStack() as stack:
        for target, attribute, replacement in replacements:
            stack.enter_context(patch.object(target, attribute, replacement))
        yield reads


@contextmanager
def _forbid_network() -> Iterator[None]:
    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise RehearsalError("successor rehearsal attempted a real network call")

    with (
        patch.object(socket.socket, "connect", forbidden),
        patch.object(socket.socket, "connect_ex", forbidden),
        patch.object(socket.socket, "sendto", forbidden),
        patch.object(socket.socket, "send", forbidden),
        patch.object(socket.socket, "sendall", forbidden),
        patch.object(socket.socket, "recv", forbidden),
        patch.object(socket.socket, "recvfrom", forbidden),
        patch.object(socket.socket, "listen", forbidden),
        patch.object(socket.socket, "accept", forbidden),
        patch.object(socket.socket, "bind", forbidden),
        patch.object(socket, "create_connection", forbidden),
        patch.object(socket, "getaddrinfo", forbidden),
        patch.object(socket, "gethostbyname", forbidden),
        patch.object(socket, "gethostbyname_ex", forbidden),
        patch.object(socket, "getnameinfo", forbidden),
        patch.object(socket, "socketpair", forbidden),
        patch.object(socket, "socket", forbidden),
    ):
        yield


@contextmanager
def _forbid_real_database() -> Iterator[None]:
    original_connect: Any = sqlite3.connect

    def guarded_connect(database: object, *args: Any, **kwargs: Any) -> Any:
        if database != ":memory:":
            raise RehearsalError("successor rehearsal attempted a real database open")
        return original_connect(database, *args, **kwargs)

    with (
        patch.object(sqlite3, "connect", guarded_connect),
        patch.object(sqlite3.dbapi2, "connect", guarded_connect),
    ):
        yield


@contextmanager
def _forbid_subprocess() -> Iterator[None]:
    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        raise RehearsalError("successor rehearsal attempted to start a subprocess")

    with patch.object(subprocess, "Popen", forbidden):
        yield


def _artifact_bytes(workspace: Path, source_binding: prepare.HeldoutBinding) -> dict[str, bytes]:
    workspace_artifacts = _workspace_artifacts(workspace, source_binding)
    by_name = {
        **workspace_artifacts,
        "synthetic_report": workspace_artifacts["report_directory"] / evaluator.REPORT_FILENAME,
    }
    result: dict[str, bytes] = {}
    for logical_name, source_relative in SUCCESSOR_ARTIFACT_INVENTORY:
        path = by_name[logical_name]
        if path.is_symlink() or not path.is_file():
            raise RehearsalError(f"successor run omitted artifact: {logical_name}")
        if path.relative_to(workspace).as_posix() != source_relative:
            raise RehearsalError(f"successor run artifact path drifted: {logical_name}")
        result[logical_name] = path.read_bytes()
    return result


def _execute_successor_run(
    *,
    label: str,
    root: Path,
    workspace: Path,
    preflight: SuccessorPreflight,
) -> SuccessorRun:
    def preflight_blob_reader(relative: str) -> bytes:
        try:
            return preflight.repository_payloads[relative]
        except KeyError as exc:
            raise RehearsalError(
                f"synthetic run read an unpreflighted repository file: {relative}"
            ) from exc

    with (
        _trace_repository_reads(
            root,
            blob_reader=preflight_blob_reader,
            allowed_write_root=workspace,
        ) as reads,
        _forbid_network(),
        _forbid_real_database(),
        _forbid_subprocess(),
    ):
        source_binding = prepare.load_binding(root)
        _execute_temp_pipeline(
            project_root=root,
            workspace=workspace,
            source_binding=source_binding,
            clock=lambda: FIXED_WALL_CLOCK,
        )
        artifacts = _artifact_bytes(workspace, source_binding)
    forbidden_values = (str(workspace), workspace.as_uri())
    for logical_name, payload in artifacts.items():
        if any(value.encode("utf-8") in payload for value in forbidden_values):
            raise RehearsalError(f"temporary root leaked into archived artifact: {logical_name}")
    return SuccessorRun(label=label, artifacts=artifacts, repository_reads=dict(reads))


def _python_inventory() -> bytes:
    payload = {
        "abi_flags": sys.abiflags,
        "cache_tag": sys.implementation.cache_tag,
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
    }
    encoded = _canonical_json_bytes(payload)
    if _sha256_bytes(encoded) != "ab3e067417027bb98ea4335e9086d2046ac9dfd4eaf857acc8622dc8f0a13a31":
        raise RehearsalError("Python runtime inventory differs from the preregistration")
    return encoded


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _package_inventory(root: Path) -> bytes:
    selected: list[Path] = []
    for key in ("purelib", "platlib"):
        raw = sysconfig.get_path(key)
        if not isinstance(raw, str) or not raw:
            raise RehearsalError(f"sysconfig {key} path is unavailable")
        path = Path(raw).resolve()
        if path not in selected:
            selected.append(path)
    projected: list[str] = []
    for path in selected:
        if not path.is_relative_to(root):
            raise RehearsalError("package inventory root escapes the project")
        projected.append(path.relative_to(root).as_posix())
    if projected != [".venv/lib/python3.12/site-packages"]:
        raise RehearsalError("package inventory root differs from the preregistration")
    # The preregistered hash is over the array, not the wrapper used in the bundle.
    roots_payload = (
        json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if _sha256_bytes(roots_payload) != (
        "fae235892c0988d4093d1ad12b034a6126d116e436393e837a8b2f71601fbd12"
    ):
        raise RehearsalError("package inventory root binding drifted")
    distributions = list(importlib.metadata.distributions(path=[str(path) for path in selected]))
    rows: list[dict[str, str]] = []
    names: list[str] = []
    for distribution in distributions:
        raw_name = distribution.metadata["Name"]
        if not isinstance(raw_name, str) or not raw_name:
            raise RehearsalError("package inventory contains an unnamed distribution")
        name = _normalized_distribution_name(raw_name)
        names.append(name)
        rows.append({"name": name, "version": distribution.version})
    if len(names) != 84 or len(set(names)) != 84:
        raise RehearsalError("package inventory count or normalized-name uniqueness drifted")
    rows.sort(key=lambda row: (row["name"], row["version"]))
    # Inventory bytes are the raw array specified by the preregistration.
    payload = (
        json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if _sha256_bytes(payload) != "c3c7792eb31679c0eb7d3140e067d691df330cd3af302d2350bf15b74ac8ec42":
        raise RehearsalError("package inventory bytes differ from the preregistration")
    return payload


def _worktree_control_payload(root: Path, relative: str) -> bytes:
    path = root / relative
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RehearsalError(f"worktree control is unavailable: {relative}") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or resolved != path.absolute()
        or not resolved.is_relative_to(root)
    ):
        raise RehearsalError(f"worktree control is not one in-root regular file: {relative}")
    return path.read_bytes()


def _preflight_successor_execution(
    *,
    root: Path,
    binding: GitBinding,
    publication_parent: Path,
    destination: Path,
) -> SuccessorPreflight:
    _assert_git_binding(binding)
    repository_payloads: dict[str, bytes] = {}

    def cached_blob_reader(relative: str) -> bytes:
        payload = repository_payloads.get(relative)
        if payload is None:
            payload = binding.blob_reader(relative)
            repository_payloads[relative] = payload
        return payload

    closure = _local_import_closure(
        entrypoint="scripts/rehearse_p4_2a_v2_heldout_full_path.py",
        blob_reader=cached_blob_reader,
    )
    for relative in SUCCESSOR_REQUIRED_SEEDS:
        cached_blob_reader(relative)
    with tempfile.TemporaryDirectory(prefix="alphapilot-p4-2a-preflight-") as raw_workspace:
        preflight_workspace = Path(raw_workspace).resolve()
        with (
            _trace_repository_reads(
                root,
                blob_reader=cached_blob_reader,
                allowed_write_root=preflight_workspace,
            ) as discovered_reads,
            _forbid_network(),
            _forbid_real_database(),
        ):
            prepare.load_binding(root)
            _copy_control_surface(root, preflight_workspace)
    repository_paths = set(SUCCESSOR_REQUIRED_SEEDS) | set(closure) | set(discovered_reads)
    repository_sha256: dict[str, str] = {}
    for relative in sorted(repository_paths, key=lambda value: value.encode("utf-8")):
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise RehearsalError(f"preflight control path escapes repository: {relative}")
        blob = cached_blob_reader(relative)
        if not blob:
            raise RehearsalError(f"preflight control file is empty: {relative}")
        if _worktree_control_payload(root, relative) != blob:
            raise RehearsalError(
                f"worktree control differs from implementation commit before execution: {relative}"
            )
        digest = _sha256_bytes(blob)
        frozen_digest = SUCCESSOR_FROZEN_CONTROL_SHA256.get(relative)
        if frozen_digest is not None and digest != frozen_digest:
            raise RehearsalError(f"frozen successor control drifted before execution: {relative}")
        repository_sha256[relative] = digest
    python_payload = _python_inventory()
    package_payload = _package_inventory(root)
    publication_device = _preflight_atomic_publication(
        publication_parent=publication_parent,
        destination=destination,
    )
    return SuccessorPreflight(
        repository_payloads=dict(sorted(repository_payloads.items())),
        repository_sha256=repository_sha256,
        ast_closure_paths=tuple(sorted(closure, key=lambda value: value.encode("utf-8"))),
        python_inventory_sha256=_sha256_bytes(python_payload),
        package_inventory_sha256=_sha256_bytes(package_payload),
        publication_device=publication_device,
    )


def _control_payloads(
    *,
    root: Path,
    binding: GitBinding,
    run_a: SuccessorRun,
    run_b: SuccessorRun,
) -> tuple[list[JsonObject], dict[str, bytes], bytes, bytes]:
    if run_a.repository_reads != run_b.repository_reads:
        raise RehearsalError("instrumented successor runs observed different control files")
    closure = _local_import_closure(
        entrypoint="scripts/rehearse_p4_2a_v2_heldout_full_path.py",
        blob_reader=binding.blob_reader,
    )
    repository_paths = set(SUCCESSOR_REQUIRED_SEEDS)
    repository_paths.update(run_a.repository_reads)
    repository_paths.update(closure)
    payloads: dict[str, bytes] = {}
    records: list[JsonObject] = []
    for relative in sorted(repository_paths, key=lambda value: value.encode("utf-8")):
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise RehearsalError(f"control path escapes repository: {relative}")
        blob = binding.blob_reader(relative)
        if _worktree_control_payload(root, relative) != blob:
            raise RehearsalError(f"worktree control differs from implementation commit: {relative}")
        frozen_digest = SUCCESSOR_FROZEN_CONTROL_SHA256.get(relative)
        if frozen_digest is not None and _sha256_bytes(blob) != frozen_digest:
            raise RehearsalError(f"frozen successor control drifted after execution: {relative}")
        if relative in closure:
            source_kind = (
                "package_initializer" if relative.endswith("/__init__.py") else "python_source"
            )
        elif relative == "pyproject.toml":
            source_kind = "project_manifest"
        elif relative == "uv.lock":
            source_kind = "lockfile"
        else:
            source_kind = "frozen_control"
        bundle_relative = f"archive/control-surface/root/repo/{relative}"
        payloads[bundle_relative] = blob
        records.append(
            {
                "logical_name": relative,
                "bundle_relative_path": bundle_relative,
                "source_kind": source_kind,
                "repository_path": relative,
                "bytes": len(blob),
                "sha256": _sha256_bytes(blob),
            }
        )
    python_payload = _python_inventory()
    package_payload = _package_inventory(root)
    for logical_name, payload, source_kind in (
        ("python", python_payload, "python_runtime"),
        ("packages", package_payload, "package_inventory"),
    ):
        bundle_relative = f"archive/control-surface/root/runtime/{logical_name}.json"
        payloads[bundle_relative] = payload
        records.append(
            {
                "logical_name": logical_name,
                "bundle_relative_path": bundle_relative,
                "source_kind": source_kind,
                "repository_path": None,
                "bytes": len(payload),
                "sha256": _sha256_bytes(payload),
            }
        )
    records.sort(key=lambda row: cast(str, row["bundle_relative_path"]).encode("utf-8"))
    logical_names = [cast(str, row["logical_name"]).casefold() for row in records]
    paths = [cast(str, row["bundle_relative_path"]).casefold() for row in records]
    if len(logical_names) != len(set(logical_names)) or len(paths) != len(set(paths)):
        raise RehearsalError("control inventory contains duplicate or casefold-colliding records")
    return records, payloads, python_payload, package_payload


def _merkle_leaf(relative_path: str, payload: bytes) -> bytes:
    return hashlib.sha256(
        b"p4.2a-rehearsal-leaf-v2\0"
        + relative_path.encode("utf-8")
        + b"\0"
        + hashlib.sha256(payload).digest()
    ).digest()


def _merkle_root(items: Mapping[str, bytes]) -> str:
    if not items:
        raise RehearsalError("empty Merkle tree is forbidden")
    nodes = [
        _merkle_leaf(path, items[path])
        for path in sorted(items, key=lambda value: value.encode("utf-8"))
    ]
    while len(nodes) > 1:
        if len(nodes) % 2:
            nodes.append(nodes[-1])
        nodes = [
            hashlib.sha256(b"p4.2a-rehearsal-node-v2\0" + nodes[index] + nodes[index + 1]).digest()
            for index in range(0, len(nodes), 2)
        ]
    return nodes[0].hex()


def _run_archive(
    run: SuccessorRun,
    *,
    archive_root: str,
) -> tuple[JsonObject, dict[str, bytes], str]:
    records: list[JsonObject] = []
    archive_payloads: dict[str, bytes] = {}
    merkle_payloads: dict[str, bytes] = {}
    for logical_name, source_relative in SUCCESSOR_ARTIFACT_INVENTORY:
        payload = run.artifacts[logical_name]
        records.append(
            {
                "logical_name": logical_name,
                "source_relative_path": source_relative,
                "bytes": len(payload),
                "sha256": _sha256_bytes(payload),
            }
        )
        archive_payloads[f"{archive_root}/{source_relative}"] = payload
        merkle_payloads[source_relative] = payload
    root_digest = _merkle_root(merkle_payloads)
    return (
        {
            "run_label": run.label,
            "archive_root": archive_root,
            "artifact_count": 14,
            "artifacts": records,
            "artifact_merkle_root_sha256": root_digest,
        },
        archive_payloads,
        root_digest,
    )


def _lineage(binding: GitBinding) -> JsonObject:
    return {
        "preregistration": {
            "path": SUCCESSOR_PREREGISTRATION_RELATIVE.as_posix(),
            "sha256": SUCCESSOR_PREREGISTRATION_SHA256,
        },
        "bundle_schema": {
            "path": SUCCESSOR_SCHEMA_RELATIVE.as_posix(),
            "sha256": SUCCESSOR_SCHEMA_SHA256,
        },
        "parent_preregistration": {
            "path": "docs/phase4/reports/P4.2a-v2-heldout-preregistration-20260810.json",
            "sha256": PREREGISTRATION_SHA256,
        },
        "v1_incident": {
            "path": "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v1-incident-20260810.json",
            "sha256": "c3224b288f5181131351ae711a673ce94ec603375925d0cc968cef85d103e785",
        },
        "tooling_base_commit": V1_TOOLING_COMMIT,
        "v1_evidence_commit": V1_EVIDENCE_COMMIT,
        "v1_fail_close_commit": V1_FAIL_CLOSE_COMMIT,
        "implementation_commit": binding.implementation_commit,
        "design": {
            "path": "config/p4_event_evaluation_v2.yaml",
            "sha256": DESIGN_SHA256,
        },
        "heldout_contract": {
            "path": "config/p4_event_extract_eval_v2-heldout-qwen3.6-plus.yaml",
            "sha256": HELDOUT_CONTRACT_SHA256,
        },
        "round3_prompt": {
            "path": "config/prompts/p4_news_event_extract_v2-r3.txt",
            "sha256": "0291dc882aac42878ba00c4ed3970da72f19508308cd39211467b4fd92294f44",
        },
        "round3_plus_contract": {
            "path": "config/p4_event_extract_eval_v2-r3-qwen3.6-plus.yaml",
            "sha256": "fa75a6cf33065745d02f74fe39e4f102723da43f37ac549058bb34fa8256a181",
        },
        "retired_v1_artifacts": [
            {"path": path, "sha256": digest} for path, digest in RETIRED_V1_REFERENCES
        ],
    }


def _bundle_payloads(
    *,
    root: Path,
    binding: GitBinding,
    run_a: SuccessorRun,
    run_b: SuccessorRun,
) -> tuple[JsonObject, dict[str, bytes]]:
    if set(run_a.artifacts) != set(run_b.artifacts) or any(
        run_a.artifacts[name] != run_b.artifacts[name] for name in run_a.artifacts
    ):
        raise RehearsalError("successor dual runs are not byte-identical across all 14 artifacts")
    run_a_record, run_a_payloads, run_a_root = _run_archive(
        run_a, archive_root="archive/run-a/root"
    )
    run_b_record, run_b_payloads, run_b_root = _run_archive(
        run_b, archive_root="archive/run-b/root"
    )
    control_records, control_payloads, python_payload, package_payload = _control_payloads(
        root=root,
        binding=binding,
        run_a=run_a,
        run_b=run_b,
    )
    control_manifest_payload = _canonical_json_bytes(
        {"schema_version": CONTROL_MANIFEST_SCHEMA, "files": control_records}
    )
    control_tree_payloads = dict(control_payloads)
    control_tree_payloads["archive/control-surface/manifest.json"] = control_manifest_payload
    control_root = _merkle_root(control_tree_payloads)
    bundle_root = hashlib.sha256(
        b"p4.2a-rehearsal-bundle-v2\0"
        + bytes.fromhex(run_a_root)
        + bytes.fromhex(run_b_root)
        + bytes.fromhex(control_root)
    ).hexdigest()
    control_manifest_record = {
        "logical_name": "control_surface_manifest",
        "bundle_relative_path": "archive/control-surface/manifest.json",
        "source_kind": "control_manifest",
        "repository_path": None,
        "bytes": len(control_manifest_payload),
        "sha256": _sha256_bytes(control_manifest_payload),
    }
    bundle: JsonObject = {
        "schema_version": SUCCESSOR_SCHEMA_VERSION,
        "rehearsal_id": SUCCESSOR_REHEARSAL_ID,
        "status": "passed",
        "lineage": _lineage(binding),
        "publication": {
            "directory": SUCCESSOR_DIRECTORY_RELATIVE.as_posix(),
            "bundle_manifest": SUCCESSOR_BUNDLE_NAME,
            "atomic_create_only": True,
            "directory_absent_before_publish": True,
            "staged_outside_repository": True,
            "published_by_single_atomic_rename": True,
            "symlink_count": 0,
            "unexpected_entry_count": 0,
        },
        "determinism": {
            "run_labels": ["run-a", "run-b"],
            "distinct_temp_roots": True,
            "temp_roots_outside_repository_and_destination": True,
            "temp_root_values_persisted": False,
            "artifact_count_per_run": 14,
            "byte_identical_artifact_count": 14,
            "mismatch_count": 0,
            "normalization_used": False,
            "wall_clock": {
                "policy": "every_injected_wall_clock_read_returns_the_same_value",
                "value_utc": FIXED_WALL_CLOCK_TEXT,
            },
            "monotonic_clock": {
                "policy": "deterministic_counter_advance_per_read",
                "initial_seconds": 1000.0,
                "step_seconds": 0.001,
                "reset_for_each_run": True,
            },
            "uuid_policy": {
                "algorithm": "uuid5_sha1_rfc4122",
                "namespace": str(UUID_NAMESPACE),
                "name_formula": "logical_record_type + NUL + stable_business_key",
                "uuid4_forbidden": True,
            },
            "persisted_path_policy": "posix_relative_to_synthetic_root_never_absolute",
            "temp_root_leak_count": 0,
        },
        "archive": {
            "artifact_path_rule": "archive_root + '/' + source_relative_path",
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
                "sha256": "b38481e57b0ba88d1b9b728c2a57583d55cf175262a8a803b483cf4823e13e29",
            },
            "uv_lock": {
                "path": "uv.lock",
                "sha256": "10829f7ef74adfcbd4401000112b5539c899a899d09d8a3f78fdf8d95803a673",
            },
            "python": {
                "implementation": "CPython",
                "version": "3.12.0",
                "cache_tag": "cpython-312",
                "abi_flags": "",
                "inventory_path": "archive/control-surface/root/runtime/python.json",
                "inventory_sha256": _sha256_bytes(python_payload),
            },
            "packages": {
                "source_call": PACKAGE_SOURCE_CALL,
                "path_scope": PACKAGE_PATH_SCOPE,
                "sysconfig_path_keys": ["purelib", "platlib"],
                "absolute_path_roots_policy": "derived_at_validation_time_not_persisted",
                "selected_path_roots_project_relative": [".venv/lib/python3.12/site-packages"],
                "selected_path_roots_sha256": (
                    "fae235892c0988d4093d1ad12b034a6126d116e436393e837a8b2f71601fbd12"
                ),
                "editable_repository_metadata_excluded": True,
                "excluded_repository_metadata_path": "src/alphapilot_ai.egg-info",
                "canonicalization": PACKAGE_CANONICALIZATION,
                "inventory_path": "archive/control-surface/root/runtime/packages.json",
                "raw_distribution_count": 84,
                "count": 84,
                "duplicate_normalized_name_count": 0,
                "duplicate_normalized_name_policy": "fail_closed_before_inventory_hash_acceptance",
                "duplicate_metadata_negative_probe": PACKAGE_NEGATIVE_PROBE,
                "sha256": _sha256_bytes(package_payload),
            },
        },
        "merkle": {
            "hash": "sha256",
            "path_encoding": "utf-8-posix-relative",
            "sort_order": "ascending_unsigned_utf8_path_bytes",
            "leaf_formula": MERKLE_LEAF_FORMULA,
            "control_tree_path_basis": CONTROL_TREE_PATH_BASIS,
            "control_manifest_leaf_relative_path": "archive/control-surface/manifest.json",
            "control_manifest_leaf_formula": CONTROL_MANIFEST_LEAF_FORMULA,
            "node_formula": MERKLE_NODE_FORMULA,
            "odd_leaf_policy": "duplicate_last_digest_at_each_level",
            "empty_tree_policy": "forbidden",
            "run_a_root_sha256": run_a_root,
            "run_b_root_sha256": run_b_root,
            "control_surface_root_sha256": control_root,
            "bundle_root_formula": BUNDLE_ROOT_FORMULA,
            "bundle_root_sha256": bundle_root,
            "bundle_manifest_is_not_a_member_of_any_merkle_tree": True,
        },
        "semantic_validation": {
            "json_schema_valid": True,
            "lineage_rehash_passed": True,
            "retired_v1_immutability_passed": True,
            "implementation_commit_object_exists": True,
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
            "no_v1_fallback": True,
            "v1_receipt_or_gate_accepted": False,
            "independent_gate_result": "PASS_REHEARSAL_V2_ONLY_REAL_HELDOUT_REMAINS_BLOCKED",
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
            "cost_and_strata_authority_conflict": "BLOCKED_PENDING_OWNER_CLARIFICATION",
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


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise RehearsalError("successor bundle write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RehearsalError("successor bundle staging contains a symlink")
        if path.is_file():
            metadata = path.stat()
            if metadata.st_nlink != 1:
                raise RehearsalError("successor bundle staging contains a hardlink")
        elif not path.is_dir():
            raise RehearsalError("successor bundle staging contains a special file")
    for directory in sorted(
        [path for path in root.rglob("*") if path.is_dir()],
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        _fsync_directory(directory)
    _fsync_directory(root)


def _renamex_np() -> Any:
    if sys.platform != "darwin":
        raise RehearsalError("atomic create-only directory publication requires renamex_np")
    try:
        renamex_np = ctypes.CDLL(None, use_errno=True).renamex_np
    except AttributeError as exc:
        raise RehearsalError("renamex_np is unavailable") from exc
    renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    renamex_np.restype = ctypes.c_int
    return renamex_np


def _device_id(path: Path) -> int:
    return path.stat().st_dev


def _preflight_atomic_publication(*, publication_parent: Path, destination: Path) -> int:
    for path, label in (
        (publication_parent, "publication staging parent"),
        (destination.parent, "publication destination parent"),
    ):
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RehearsalError(f"{label} is unavailable") from exc
        if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise RehearsalError(f"{label} is not one regular directory")
    staging_device = _device_id(publication_parent)
    if staging_device != _device_id(destination.parent):
        raise RehearsalError("successor staging and destination are on different filesystems")
    renamex_np = _renamex_np()
    probe_root = Path(
        tempfile.mkdtemp(
            prefix=".alphapilot-p4-2a-renamex-probe-",
            dir=publication_parent,
        )
    )
    try:
        source = probe_root / "source"
        destination_probe = probe_root / "destination"
        collision = probe_root / "collision"
        source.mkdir(mode=0o700)
        collision.mkdir(mode=0o700)
        ctypes.set_errno(0)
        if renamex_np(os.fsencode(source), os.fsencode(destination_probe), 0x00000004) != 0:
            error = ctypes.get_errno()
            raise RehearsalError(
                f"renamex_np create-only capability probe failed: {os.strerror(error)}"
            )
        ctypes.set_errno(0)
        if renamex_np(os.fsencode(collision), os.fsencode(destination_probe), 0x00000004) == 0:
            raise RehearsalError("renamex_np create-only capability probe overwrote a directory")
        error = ctypes.get_errno()
        if error not in {errno.EEXIST, errno.ENOTEMPTY}:
            raise RehearsalError(
                f"renamex_np collision probe returned unexpected errno: {error}"
            )
        if not collision.is_dir() or not destination_probe.is_dir():
            raise RehearsalError("renamex_np collision probe changed an existing directory")
        _fsync_directory(probe_root)
    finally:
        try:
            shutil.rmtree(probe_root)
        except OSError as exc:
            raise RehearsalError("renamex_np capability probe could not be removed") from exc
        if probe_root.exists() or probe_root.is_symlink():
            raise RehearsalError("renamex_np capability probe cleanup was incomplete")
        _fsync_directory(publication_parent)
    return staging_device


def registered_execution_claim_directory(
    project_root: Path = PROJECT_ROOT,
    destination: Path | None = None,
) -> Path:
    root = project_root.resolve()
    literal_destination = (
        destination.absolute()
        if destination is not None
        else registered_successor_directory(root)
    )
    claim_material = (
        SUCCESSOR_PREREGISTRATION_SHA256
        + "\0"
        + SUCCESSOR_REHEARSAL_ID
        + "\0"
        + literal_destination.as_posix()
    ).encode("utf-8")
    token = hashlib.sha256(claim_material).hexdigest()
    return root.parent / f".alphapilot-p4-2a-v2-execution-claim-{token}"


def _claim_registered_execution(*, root: Path, destination: Path) -> Path:
    claim = registered_execution_claim_directory(root, destination)
    if claim.parent != root.parent:
        raise RehearsalError("registered execution claim parent drifted")
    _preflight_atomic_publication(
        publication_parent=claim.parent,
        destination=destination,
    )
    try:
        os.mkdir(claim, 0o700)
    except FileExistsError as exc:
        raise FileExistsError(
            f"successor execution was already claimed and can never be retried: {claim}"
        ) from exc
    except OSError as exc:
        raise RehearsalError("successor execution claim could not be created") from exc
    _fsync_directory(claim.parent)
    claim_metadata = claim.lstat()
    if (
        claim.is_symlink()
        or not stat.S_ISDIR(claim_metadata.st_mode)
        or stat.S_IMODE(claim_metadata.st_mode) != 0o700
        or any(claim.iterdir())
    ):
        raise RehearsalError("successor execution claim is not one empty regular directory")
    if _device_id(claim) != _device_id(destination.parent):
        raise RehearsalError("successor execution claim is on the wrong filesystem")
    return claim


def _atomic_directory_create_only(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite successor rehearsal bundle: {destination}")
    _preflight_atomic_publication(
        publication_parent=source.parent,
        destination=destination,
    )
    if source.stat().st_dev != destination.parent.stat().st_dev:
        raise RehearsalError("successor staging and destination are on different filesystems")
    renamex_np = _renamex_np()
    result = renamex_np(os.fsencode(source), os.fsencode(destination), 0x00000004)
    if result != 0:
        error = ctypes.get_errno()
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(
                f"refusing to overwrite successor rehearsal bundle: {destination}"
            )
        raise OSError(error, os.strerror(error), destination)
    for parent in sorted(
        {source.parent, destination.parent},
        key=lambda path: os.fsencode(path),
    ):
        _fsync_directory(parent)


def _publish_successor_bundle(
    *,
    root: Path,
    destination: Path,
    bundle: Mapping[str, Any],
    archived_payloads: Mapping[str, bytes],
    validate_before_publish: bool,
    staged_bundle: Path,
) -> Path:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite successor rehearsal bundle: {destination}")
    if (
        staged_bundle.is_symlink()
        or not staged_bundle.is_dir()
        or any(staged_bundle.iterdir())
        or staged_bundle.is_relative_to(root)
        or staged_bundle.is_relative_to(destination)
    ):
        raise RehearsalError("successor bundle staging claim is not empty and isolated")
    for relative, payload in sorted(
        archived_payloads.items(), key=lambda item: item[0].encode("utf-8")
    ):
        target = staged_bundle / relative
        if not target.resolve().is_relative_to(staged_bundle):
            raise RehearsalError("successor archive path escapes staging")
        _write_exclusive(target, payload)
    bundle_payload = _canonical_json_bytes(bundle)
    _write_exclusive(staged_bundle / SUCCESSOR_BUNDLE_NAME, bundle_payload)
    expected_files = set(archived_payloads) | {SUCCESSOR_BUNDLE_NAME}
    observed_files = {
        path.relative_to(staged_bundle).as_posix()
        for path in staged_bundle.rglob("*")
        if path.is_file()
    }
    if observed_files != expected_files:
        raise RehearsalError("successor bundle staging inventory drifted")
    _fsync_tree(staged_bundle)
    if validate_before_publish:
        from scripts.validate_p4_2a_v2_heldout_rehearsal_bundle import (
            validate_rehearsal_bundle,
        )

        validate_rehearsal_bundle(staged_bundle, project_root=root)
    _atomic_directory_create_only(staged_bundle, destination)
    return destination / SUCCESSOR_BUNDLE_NAME


def registered_successor_directory(project_root: Path = PROJECT_ROOT) -> Path:
    root = project_root.resolve()
    if not root.is_dir():
        raise RehearsalError("project root is unavailable for registered successor publication")
    if (
        SUCCESSOR_DIRECTORY_RELATIVE.is_absolute()
        or ".." in SUCCESSOR_DIRECTORY_RELATIVE.parts
    ):
        raise RehearsalError("registered successor directory is not one literal relative path")
    destination = (root / SUCCESSOR_DIRECTORY_RELATIVE).absolute()
    if not destination.is_relative_to(root):
        raise RehearsalError("registered successor directory escapes project root")
    cursor = root
    for component in SUCCESSOR_DIRECTORY_RELATIVE.parts:
        cursor = cursor / component
        try:
            metadata = cursor.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RehearsalError(
                "registered successor directory component is unavailable"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise RehearsalError(
                "registered successor directory contains a symbolic link substitution"
            )
        if cursor != destination and not stat.S_ISDIR(metadata.st_mode):
            raise RehearsalError(
                "registered successor directory ancestor is not one regular directory"
            )
    try:
        resolved_destination = destination.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise RehearsalError("registered successor directory cannot be resolved safely") from exc
    if resolved_destination != destination:
        raise RehearsalError(
            "registered successor directory contains a symbolic link substitution"
        )
    return destination


def _execution_environment_is_locked() -> bool:
    if (
        os.environ.get(ENVIRONMENT_REEXEC_MARKER) != "1"
        or sys.flags.hash_randomization != 0
        or any(
            os.environ.get(name) != value
            for name, value in LOCKED_EXECUTION_ENVIRONMENT.items()
        )
    ):
        return False
    try:
        active_locale = locale.setlocale(locale.LC_ALL, "")
        time.tzset()
    except (locale.Error, OSError):
        return False
    return active_locale == "C.UTF-8" and time.tzname == ("UTC", "UTC") and time.timezone == 0


def _reexec_in_locked_environment(arguments: Sequence[str]) -> NoReturn:
    environment = dict(os.environ)
    environment.update(LOCKED_EXECUTION_ENVIRONMENT)
    environment[ENVIRONMENT_REEXEC_MARKER] = "1"
    os.execve(
        sys.executable,
        [sys.executable, str(Path(__file__).resolve()), *arguments],
        environment,
    )
    raise RehearsalError("locked-environment re-exec unexpectedly returned")


def _bootstrap_registered_execution() -> None:
    global _registered_bootstrap_state
    if _registered_bootstrap_state is not None:
        raise RehearsalError("registered successor bootstrap may be established only once")
    if not _execution_environment_is_locked():
        raise RehearsalError("registered successor bootstrap requires the locked interpreter")
    root = PROJECT_ROOT.resolve()
    destination = registered_successor_directory(root)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite successor rehearsal bundle: {destination}")
    claim = registered_execution_claim_directory(root, destination)
    binding = _production_git_binding(root)
    preflight = _preflight_successor_execution(
        root=root,
        binding=binding,
        publication_parent=claim.parent,
        destination=destination,
    )
    _registered_bootstrap_state = (
        _REGISTERED_BOOTSTRAP_TOKEN,
        RegisteredBootstrap(binding=binding, preflight=preflight),
    )


def _consume_registered_bootstrap() -> RegisteredBootstrap:
    global _registered_bootstrap_state
    state = _registered_bootstrap_state
    _registered_bootstrap_state = None
    if state is None or state[0] is not _REGISTERED_BOOTSTRAP_TOKEN:
        raise RehearsalError(
            "registered successor execution requires an in-process verified bootstrap"
        )
    return state[1]


def _create_test_staging(destination: Path) -> tuple[Path, Path]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_parent = Path(
        tempfile.mkdtemp(
            prefix="alphapilot-p4-2a-v2-test-stage-",
            dir=destination.parent,
        )
    ).resolve()
    staged_bundle = staging_parent / "bundle"
    os.mkdir(staged_bundle, 0o700)
    _fsync_directory(staging_parent)
    return staged_bundle, staging_parent


def run_successor_rehearsal(
    *,
    project_root: Path = PROJECT_ROOT,
    publish_directory: Path | None = None,
    workspace_parent: Path | None = None,
    git_binding: GitBinding | None = None,
    validate_before_publish: bool = True,
) -> Path:
    """Run the preregistered successor twice and atomically publish one v2 bundle.

    The path and Git overrides are dependency-injection seams for unit tests only;
    the CLI exposes none of them.
    """

    root = project_root.resolve()
    registered_destination = registered_successor_directory(root)
    destination = (
        publish_directory.resolve()
        if publish_directory is not None
        else registered_destination
    )
    if destination.is_relative_to(root) and destination != registered_destination:
        raise RehearsalError("test publication may not target an unregistered repository path")
    is_registered = destination == registered_destination
    if is_registered:
        if (
            root != PROJECT_ROOT.resolve()
            or publish_directory is not None
            or workspace_parent is not None
            or git_binding is not None
            or not validate_before_publish
        ):
            raise RehearsalError(
                "registered successor execution forbids every dependency-injection override"
            )
        bootstrap = _consume_registered_bootstrap()
        binding = bootstrap.binding
        preflight_before = bootstrap.preflight
        staged_bundle: Path | None = None
        staging_parent: Path | None = None
    else:
        if publish_directory is None:
            raise RehearsalError("non-default project_root requires an out-of-root test target")
        staged_bundle, staging_parent = _create_test_staging(destination)
        binding = git_binding or _production_git_binding(root)
        try:
            preflight_before = _preflight_successor_execution(
                root=root,
                binding=binding,
                publication_parent=staged_bundle.parent,
                destination=destination,
            )
        except BaseException:
            shutil.rmtree(staging_parent, ignore_errors=True)
            raise
    if destination.exists() or destination.is_symlink():
        if staging_parent is not None:
            shutil.rmtree(staging_parent, ignore_errors=True)
        raise FileExistsError(f"refusing to overwrite successor rehearsal bundle: {destination}")
    v1_before = {
        relative: _sha256_file(root / relative) for relative, _digest in RETIRED_V1_REFERENCES
    }
    for relative, expected in RETIRED_V1_REFERENCES:
        if v1_before[relative] != expected:
            if staging_parent is not None:
                shutil.rmtree(staging_parent, ignore_errors=True)
            raise RehearsalError(
                f"retired v1 artifact drifted before successor execution: {relative}"
            )
    protected_root = (root / "docs/phase4/eval/v2-calibration/heldout").resolve()
    heldout_before = _tree_fingerprint(protected_root)
    parent = workspace_parent.resolve() if workspace_parent is not None else None
    if parent is not None:
        if parent.is_relative_to(root) or parent.is_relative_to(destination):
            if staging_parent is not None:
                shutil.rmtree(staging_parent, ignore_errors=True)
            raise RehearsalError("successor temp parent overlaps a registered root")
        parent.mkdir(parents=True, exist_ok=True)
    if is_registered:
        current_preflight = _preflight_successor_execution(
            root=root,
            binding=binding,
            publication_parent=registered_execution_claim_directory(root, destination).parent,
            destination=destination,
        )
        if current_preflight != preflight_before:
            raise RehearsalError("registered successor controls drifted after bootstrap")
        staged_bundle = _claim_registered_execution(root=root, destination=destination)
    if staged_bundle is None:
        raise RehearsalError("successor execution did not acquire one staging claim")
    try:
        with (
            tempfile.TemporaryDirectory(
                prefix="alphapilot-p4-2a-successor-run-a-",
                dir=str(parent) if parent else None,
            ) as run_a_raw,
            tempfile.TemporaryDirectory(
                prefix="alphapilot-p4-2a-successor-run-b-",
                dir=str(parent) if parent else None,
            ) as run_b_raw,
        ):
            run_a_root = Path(run_a_raw).resolve()
            run_b_root = Path(run_b_raw).resolve()
            if (
                run_a_root == run_b_root
                or run_a_root.is_relative_to(root)
                or run_b_root.is_relative_to(root)
                or run_a_root.is_relative_to(destination)
                or run_b_root.is_relative_to(destination)
                or run_a_root.is_relative_to(run_b_root)
                or run_b_root.is_relative_to(run_a_root)
            ):
                raise RehearsalError("successor run roots are not distinct and isolated")
            run_a = _execute_successor_run(
                label="run-a",
                root=root,
                workspace=run_a_root,
                preflight=preflight_before,
            )
            run_b = _execute_successor_run(
                label="run-b",
                root=root,
                workspace=run_b_root,
                preflight=preflight_before,
            )
        if run_a_root.exists() or run_b_root.exists():
            raise RehearsalError("successor temporary roots were not removed")
        if _tree_fingerprint(protected_root) != heldout_before:
            raise RehearsalError("real held-out artifacts changed during successor rehearsal")
        v1_after = {relative: _sha256_file(root / relative) for relative in v1_before}
        if v1_after != v1_before:
            raise RehearsalError("retired v1 evidence changed during successor rehearsal")
        postflight = _preflight_successor_execution(
            root=root,
            binding=binding,
            publication_parent=staged_bundle.parent,
            destination=destination,
        )
        if postflight != preflight_before:
            raise RehearsalError("successor control or runtime binding drifted during execution")
        bundle, payloads = _bundle_payloads(
            root=root,
            binding=binding,
            run_a=run_a,
            run_b=run_b,
        )
        return _publish_successor_bundle(
            root=root,
            destination=destination,
            bundle=bundle,
            archived_payloads=payloads,
            validate_before_publish=validate_before_publish,
            staged_bundle=staged_bundle,
        )
    finally:
        if not is_registered and staging_parent is not None:
            shutil.rmtree(staging_parent, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="run the offline synthetic successor twice and atomically publish the v2 bundle",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    arguments = _parser().parse_args(raw_arguments)
    if not arguments.execute:
        print("ERROR: --execute is required", file=sys.stderr)
        return 2
    if not _execution_environment_is_locked():
        _reexec_in_locked_environment(raw_arguments)
    try:
        _bootstrap_registered_execution()
        bundle = run_successor_rehearsal()
    except (RehearsalError, FileExistsError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "passed",
                "bundle": str(bundle.relative_to(PROJECT_ROOT)),
                "successor_gate": "PASS_REHEARSAL_V2_ONLY_REAL_HELDOUT_REMAINS_BLOCKED",
                "real_model_calls": 0,
                "production_writes": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
