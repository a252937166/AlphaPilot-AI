#!/usr/bin/env python3
"""Fail-closed evaluator for the P4.2a v2 held-out 40/20 frame.

The command has two deliberately separate modes:

* The read-only dry-run machinery validates every input and substitutes
  structurally isomorphic labels, but under the current successor release it
  is callable only by the private offline rehearsal capability.
* ``--formal`` is unavailable without a separate independent-review
  authorization.  It claims the create-only state only after every preflight
  has passed, writes one create-only report, and always appends one terminal
  event.  There is no retry path.

This module performs no model calls and never writes the production database.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, cast
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402
from scripts import build_p4_2a_gold_sample as gold_builder  # noqa: E402
from scripts import build_p4_2a_v2_heldout_adjudication_ui as heldout_ui  # noqa: E402
from scripts import p4_2a_v2_dev_common as pipeline_common  # noqa: E402
from scripts import prepare_p4_2a_v2_heldout as heldout_prepare  # noqa: E402
from scripts import seal_p4_2a_v2_ai_draft as base_seal  # noqa: E402
from scripts import seal_p4_2a_v2_heldout_draft as heldout_seal  # noqa: E402

from alphapilot.llm.p4_news_eval import EventEvaluationDesignError  # noqa: E402
from alphapilot.llm.p4_news_event import EventExtractContract  # noqa: E402

JsonObject = dict[str, Any]

DESIGN_PATH = Path("config/p4_event_evaluation_v2.yaml")
DESIGN_SHA256 = "18a2428a4ec04bfea6e4f4d70692f38ea82fbaee5a223f30f2465b895b238e21"
PREREGISTRATION_PATH = Path("docs/phase4/reports/P4.2a-v2-heldout-preregistration-20260810.json")
PREREGISTRATION_SHA256 = "ccecbf5ca7b48b16e445318b8c94a08927432f92c7e8c12f8ab40f2916578705"
SELECTION_OUTCOME_PATH = Path(
    "docs/phase4/eval/v2-calibration/development/P4.2a-development-v2-selection-outcome.json"
)
SELECTION_OUTCOME_SHA256 = "36b5a004b294f012b4ab1dab659d1b3d5d98320d794ad2fe90960a617f554da1"
SELECTED_FREEZE_PATH = Path(
    "docs/phase4/eval/v2-calibration/development/P4.2a-development-v2-selected-contract-freeze.json"
)
SELECTED_FREEZE_SHA256 = "0ebc5362055af7ef6409155befc5e09d345cd4f2d8d128ea0791a0c293f66f75"
HELDOUT_CONTRACT_PATH = Path("config/p4_event_extract_eval_v2-heldout-qwen3.6-plus.yaml")
HELDOUT_CONTRACT_SHA256 = "26be1765204b122908e7bd09cac857c33bd3140233df47dc3358bc590e020199"
ROUND3_CONTRACT_PATH = Path("config/p4_event_extract_eval_v2-r3-qwen3.6-plus.yaml")
ROUND3_CONTRACT_SHA256 = "fa75a6cf33065745d02f74fe39e4f102723da43f37ac549058bb34fa8256a181"
PROMPT_PATH = Path("config/prompts/p4_news_event_extract_v2-r3.txt")
PROMPT_SHA256 = "0291dc882aac42878ba00c4ed3970da72f19508308cd39211467b4fd92294f44"
OWNER_AMENDMENT_PATH = Path(
    "docs/phase4/reports/P4.2a-round3-adjudication-and-model-selection-override-20260810.json"
)
OWNER_AMENDMENT_SHA256 = "2f8d0309c4071373d85d8835e01128468db15e3cdfb596331060ebd41d73957c"
COST_CORRECTION_PATH = Path(
    "docs/phase4/reports/P4.2a-cost-correction-and-P4.2b-throughput-backlog-20260810.json"
)
COST_CORRECTION_SHA256 = "e42eeb2342412662a84ce0304015e6f236661069b1e725f18fd8f4dfd3fd05c5"
SUCCESSOR_PREREGISTRATION_PATH = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-1-preregistration-20260810.json"
)
SUCCESSOR_PREREGISTRATION_SHA256 = (
    "c303cfb13a42ecbb7e0acaec04de12a9e9169b89cf9e93ea79d0f120d1439d3e"
)
SUCCESSOR_PREREGISTRATION_COMMIT = "b302d5889f01296568340bcc15041cc554ceb2c7"
FRAME_AUTHORITY_PATH = Path(
    "docs/phase4/reports/"
    "P4.2a-rehearsal-v2-approval-and-frame-authority-ruling-20260810.json"
)
FRAME_AUTHORITY_SHA256 = (
    "8605dd30dd6bffa9621b6efe5e01c4c9cead615c9639259c2e79e14fcbbc3421"
)
SUCCESSOR_CODE_GATE_AUTHORITY_PATH = Path(
    "docs/phase4/reports/P4.2a-successor-v2-1-code-gate-authorization-20260810.json"
)
SUCCESSOR_CODE_GATE_AUTHORITY_SHA256 = (
    "e28db692dc150983f86f6760fb1a95584d8607658e8a78a0de35cf3fc81940cd"
)
SUCCESSOR_REHEARSAL_BUNDLE_PATH = Path(
    "docs/phase4/rehearsals/P4.2a-v2-calibration-v2-1/bundle.json"
)
SUCCESSOR_RELEASE_AUTHORIZATION_PATH = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-1-release-authorization-20260810.json"
)
MATERIALIZATION_MANIFEST_SCHEMA = "p4.2a-v2-heldout-materialization-manifest-v2"

FRAME_ID = "p4.2a-heldout-frame-v2"
MODEL = "qwen3.6-plus"
EXPECTED_DRAFTER_ID = "OpenAI Codex GPT-5"
EXPECTED_ADJUDICATOR_ID = "ouyang"
EXPECTED_REVIEWER_ID = "independent_ai_architect_claude_code"
EXPECTED_REVIEWER_TYPE = "ai"
EXPECTED_REVIEWER_ROLE = "independent_ai_architect_claude_code"
EXPECTED_REVIEWER_MODEL = "claude-fable-5"
EXPECTED_RAW_COUNT = 4048
EXPECTED_RAW_BY_SOURCE = {
    "akshare_ths": 1021,
    "cninfo": 2824,
    "sina_company_news": 203,
}
SOURCE_WINDOW_START_UTC = datetime(2026, 8, 5, 16, 0, tzinfo=UTC)
SOURCE_WINDOW_END_UTC = datetime(2026, 8, 8, 16, 0, tzinfo=UTC)
SELECTION_SEED = "alphapilot-p4.2a-heldout-frame-v2-20260809-r1"
EXPECTED_COUNT = 60
POSITIVE_COUNT = 40
NEGATIVE_COUNT = 20
PRECISION_MINIMUM = 0.80
FALSE_OMISSION_RATE_MAXIMUM = 0.20
SYMBOL_ACCURACY_MINIMUM = 0.95
REPORT_FILENAME = "P4.2a-heldout-v2-evaluation-result.json"
DESIGN_REF = {"path": str(DESIGN_PATH), "sha256": DESIGN_SHA256}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SYMBOL_RE = re.compile(r"^[0-9]{6}$")
_EVENT_TYPES = frozenset(
    {
        "earnings_preannounce",
        "major_contract",
        "buyback_or_holder_change",
        "regulatory_action",
        "halt_resume",
        "ma_restructure",
        "policy_sector",
        "dividend",
        "other",
    }
)
_LABEL_FIELDS = (
    "symbols",
    "event_type",
    "direction",
    "materiality",
    "evidence_span",
    "notes",
)
_MATERIALIZED_LABEL_FIELDS = frozenset(
    {
        "symbols",
        "event_type",
        "direction",
        "materiality",
        "summary",
        "confidence",
        "evidence_span",
    }
)
_INFERENCE_SETTINGS_SAFETY = {
    "trading_mode": "research",
    "live_trading_enabled": False,
    "paper_trading_enabled": False,
    "paper_auto_trading_enabled": False,
    "futu_enable_account_mutation": False,
    "futu_enable_trade": False,
    "unlock_trade_permanently_blocked": True,
}
_SNAPSHOT_FIELDS = {
    "sqlite_uri_mode",
    "pragma_query_only",
    "connection_total_changes",
    "llm_call_count",
    "llm_call_max_id",
    "trade_proposal_count",
    "broker_order_count",
    "non_simulate_order_count",
    "news_events_table_exists",
    "universe_symbol_count",
}
_COMPLETION_VALIDATION = {
    "blind_schema": "p4.2a-v2-heldout-owner-blind-item-v1",
    "ai_draft_schema": "p4.2a-v2-ai-draft-item-v1",
    "owner_export_schema": "p4.2a-v2-owner-adjudication-export-item-v1",
    "human_gold_schema": "p4.2a-v2-human-adjudicated-item-v1",
    "exact_same_order_identity": True,
    "recursive_blindness_check": True,
    "evidence_grounding_check": True,
    "draft_notes_null_check": True,
    "drafter_not_evaluated_model_check": True,
    "human_distinct_from_drafter_check": True,
    "per_item_confirmation_check": True,
    "delta_recomputed_check": True,
    "body_evidence_preserved_without_refetch": True,
    "raw_owner_export_retained": True,
    "private_selection_binding_check": True,
    "ui_byte_reconstruction_check": True,
    "timestamp_order_check": True,
    "heldout_40_20_partition_check": True,
    "full_candidate_inference_success_check": True,
}
_DRY_RUN_STAGES = [
    "frozen_controls",
    "materialization_and_inference_lineage",
    "full_pool_predictions",
    "deterministic_selection",
    "blind_draft_owner_human_chain",
    "owner_completion",
    "synthetic_metric_assembly",
    "synthetic_report_serialization",
]
_FORBIDDEN_BLIND_KEYS = frozenset(
    {
        "sampling_stratum",
        "model_prediction",
        "prediction",
        "predicted_materiality",
        "selection_seed",
        "selection_rank_sha256",
        "selection_basis",
        "score",
    }
)


class HeldoutEvaluationError(RuntimeError):
    """One frozen held-out evaluation invariant failed."""


@dataclass(frozen=True, slots=True)
class ControlBundle:
    preregistration: JsonObject
    design: JsonObject
    heldout_contract: JsonObject
    round3_contract: JsonObject
    materialized_schema: JsonObject
    control_hashes: Mapping[str, str]
    retired_ids: frozenset[int]


@dataclass(frozen=True, slots=True)
class ArtifactPaths:
    materialized_inputs: Path
    materialization_manifest: Path
    inference_state: Path
    predictions: Path
    prediction_manifest: Path
    selection: Path
    blind: Path
    draft: Path
    adjudication_ui: Path
    owner_export: Path
    human_adjudicated: Path
    owner_completion: Path
    evaluation_state: Path
    report: Path
    artifact_root: Path | None = None


@dataclass(frozen=True, slots=True)
class Preflight:
    controls: ControlBundle
    paths: ArtifactPaths
    hashes: Mapping[str, str]
    materialized_inputs: tuple[JsonObject, ...]
    materialization_manifest: JsonObject
    inference_state: tuple[JsonObject, ...]
    prediction_manifest: JsonObject
    selection: JsonObject
    selected: tuple[JsonObject, ...]
    predictions_by_id: Mapping[int, JsonObject]
    blind: tuple[JsonObject, ...]
    draft: tuple[JsonObject, ...]
    owner_export: tuple[JsonObject, ...]
    human: tuple[JsonObject, ...]
    completion: JsonObject


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _strict_object(pairs: Sequence[tuple[str, Any]]) -> JsonObject:
    value: JsonObject = {}
    for key, item in pairs:
        if key in value:
            raise HeldoutEvaluationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> NoReturn:
    raise HeldoutEvaluationError(f"non-finite JSON value is forbidden: {value}")


def _regular_payload(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise HeldoutEvaluationError(f"{label} is not one regular file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise HeldoutEvaluationError(f"{label} is unavailable") from exc


def _load_json(path: Path, label: str) -> tuple[JsonObject, bytes]:
    payload = _regular_payload(path, label)
    try:
        value: object = json.loads(
            payload,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HeldoutEvaluationError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise HeldoutEvaluationError(f"{label} must be a JSON object")
    return cast(JsonObject, value), payload


def _load_jsonl(path: Path, label: str) -> tuple[list[JsonObject], bytes]:
    payload = _regular_payload(path, label)
    rows: list[JsonObject] = []
    for line_number, line in enumerate(payload.splitlines(), 1):
        if not line.strip():
            raise HeldoutEvaluationError(f"{label} line {line_number} is blank")
        try:
            value: object = json.loads(
                line,
                object_pairs_hook=_strict_object,
                parse_constant=_reject_nonfinite,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HeldoutEvaluationError(f"{label} line {line_number} is invalid") from exc
        if not isinstance(value, dict):
            raise HeldoutEvaluationError(f"{label} line {line_number} is not an object")
        rows.append(cast(JsonObject, value))
    if not rows:
        raise HeldoutEvaluationError(f"{label} is empty")
    return rows, payload


def _load_yaml(path: Path, label: str) -> tuple[JsonObject, bytes]:
    payload = _regular_payload(path, label)
    try:
        value: object = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise HeldoutEvaluationError(f"{label} is invalid YAML") from exc
    if not isinstance(value, dict):
        raise HeldoutEvaluationError(f"{label} must be a mapping")
    return cast(JsonObject, value), payload


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HeldoutEvaluationError(f"{label} must be a mapping")
    return cast(Mapping[str, Any], value)


def _sequence(value: object, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise HeldoutEvaluationError(f"{label} must be a sequence")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise HeldoutEvaluationError(f"{label} fields drifted")


def _bound_file(root: Path, relative: Path, digest: str, label: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise HeldoutEvaluationError(f"{label} escapes the project root")
    payload = _regular_payload(path, label)
    if _sha256_bytes(payload) != digest:
        raise HeldoutEvaluationError(f"{label} differs from its frozen SHA-256")
    return path


def _bound_ref(root: Path, value: object, label: str) -> tuple[Path, str]:
    reference = _mapping(value, label)
    _exact_keys(reference, {"path", "sha256"}, label)
    raw_path = reference.get("path")
    digest = reference.get("sha256")
    if not isinstance(raw_path, str) or not raw_path or not _is_sha(digest):
        raise HeldoutEvaluationError(f"{label} reference is invalid")
    return _bound_file(root, Path(raw_path), cast(str, digest), label), cast(str, digest)


def _path_from_design(root: Path, artifacts: Mapping[str, Any], name: str) -> Path:
    entry = _mapping(artifacts.get(name), f"design.artifacts.{name}")
    raw = entry.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise HeldoutEvaluationError(f"design.artifacts.{name}.path is invalid")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise HeldoutEvaluationError(f"design.artifacts.{name}.path escapes the project")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise HeldoutEvaluationError(f"design.artifacts.{name}.path escapes the project")
    return path


def registered_artifact_paths(root: Path, design: Mapping[str, Any]) -> ArtifactPaths:
    artifacts = _mapping(design.get("artifacts"), "design.artifacts")
    report_directory = _path_from_design(root, artifacts, "heldout_report_directory")
    return ArtifactPaths(
        materialized_inputs=_path_from_design(root, artifacts, "heldout_materialized_inputs_jsonl"),
        materialization_manifest=_path_from_design(
            root, artifacts, "heldout_materialization_manifest"
        ),
        inference_state=_path_from_design(root, artifacts, "heldout_inference_state_jsonl"),
        predictions=_path_from_design(root, artifacts, "heldout_predictions_jsonl"),
        prediction_manifest=_path_from_design(root, artifacts, "heldout_predictions_manifest"),
        selection=_path_from_design(root, artifacts, "heldout_private_selection_manifest"),
        blind=_path_from_design(root, artifacts, "heldout_owner_blind_jsonl"),
        draft=_path_from_design(root, artifacts, "heldout_ai_draft_jsonl"),
        adjudication_ui=_path_from_design(root, artifacts, "heldout_adjudication_html"),
        owner_export=_path_from_design(root, artifacts, "heldout_owner_raw_export_jsonl"),
        human_adjudicated=_path_from_design(root, artifacts, "heldout_human_adjudicated_jsonl"),
        owner_completion=_path_from_design(root, artifacts, "heldout_owner_completion_manifest"),
        evaluation_state=_path_from_design(root, artifacts, "heldout_evaluation_state_jsonl"),
        report=report_directory / REPORT_FILENAME,
        artifact_root=root,
    )


def _render_expected_adjudication_ui(
    blind: Sequence[Mapping[str, Any]],
    draft: Sequence[Mapping[str, Any]],
    *,
    control_root: Path,
    paths: ArtifactPaths,
    selection_payload: bytes,
    blind_payload: bytes,
    draft_payload: bytes,
) -> bytes:
    """Rederive the exact owner UI from independently validated frozen inputs."""

    try:
        registered_contract = heldout_seal.load_registered_contract(
            control_root / DESIGN_PATH,
            project_root=control_root,
        )
        contract = replace(
            registered_contract,
            artifacts={
                "development_private_selection_manifest": paths.selection,
                "development_owner_blind_jsonl": paths.blind,
                "development_ai_draft_jsonl": paths.draft,
                "development_adjudication_html": paths.adjudication_ui,
                "development_owner_raw_export_jsonl": paths.owner_export,
                "development_human_adjudicated_jsonl": paths.human_adjudicated,
                "development_owner_completion_manifest": paths.owner_completion,
            },
        )
        rendered, row_count = heldout_ui.render_registered_ui_payload(
            blind,
            draft,
            contract=contract,
            blind_payload=blind_payload,
            draft_payload=draft_payload,
            selection_payload=selection_payload,
        )
    except (base_seal.V2AdjudicationError, EventEvaluationDesignError) as exc:
        raise HeldoutEvaluationError("held-out adjudication UI inputs are invalid") from exc
    if row_count != EXPECTED_COUNT:
        raise HeldoutEvaluationError("held-out adjudication UI row count drifted")
    rendered_bytes: bytes = rendered
    return rendered_bytes


def load_control_bundle(root: Path = PROJECT_ROOT) -> ControlBundle:
    """Load and cross-check the complete byte-frozen control surface."""

    frozen = {
        "preregistration": (PREREGISTRATION_PATH, PREREGISTRATION_SHA256, "json"),
        "design": (DESIGN_PATH, DESIGN_SHA256, "yaml"),
        "selection_outcome": (SELECTION_OUTCOME_PATH, SELECTION_OUTCOME_SHA256, "json"),
        "selected_freeze": (SELECTED_FREEZE_PATH, SELECTED_FREEZE_SHA256, "json"),
        "heldout_contract": (HELDOUT_CONTRACT_PATH, HELDOUT_CONTRACT_SHA256, "yaml"),
        "round3_contract": (ROUND3_CONTRACT_PATH, ROUND3_CONTRACT_SHA256, "yaml"),
        "prompt": (PROMPT_PATH, PROMPT_SHA256, "bytes"),
        "owner_amendment": (OWNER_AMENDMENT_PATH, OWNER_AMENDMENT_SHA256, "json"),
        "cost_correction": (COST_CORRECTION_PATH, COST_CORRECTION_SHA256, "json"),
    }
    loaded: dict[str, JsonObject] = {}
    hashes: dict[str, str] = {}
    for name, (relative, digest, kind) in frozen.items():
        path = _bound_file(root, relative, digest, name)
        hashes[name] = digest
        if kind == "json":
            loaded[name] = _load_json(path, name)[0]
        elif kind == "yaml":
            loaded[name] = _load_yaml(path, name)[0]

    prereg = loaded["preregistration"]
    design = loaded["design"]
    heldout_contract = loaded["heldout_contract"]
    round3_contract = loaded["round3_contract"]
    outcome = loaded["selection_outcome"]
    freeze = loaded["selected_freeze"]
    if (
        prereg.get("schema_version") != "p4.2a-v2-heldout-preregistration-v1"
        or prereg.get("status")
        != "PREREGISTERED_BEFORE_SYNTHETIC_REHEARSAL_AND_ANY_HELDOUT_ARTIFACT"
        or design.get("schema_version") != "p4.2a-evaluation-design-v2"
        or design.get("production_writes_allowed") is not False
        or outcome.get("selected_model") != MODEL
        or freeze.get("selected_model") != MODEL
        or heldout_contract.get("schema_version") != "p4.2a-heldout-event-extract-contract-v2"
        or round3_contract.get("schema_version") != "p4.2a-development-event-extract-contract-v2-r3"
    ):
        raise HeldoutEvaluationError("P4.2a v2 held-out control versions drifted")
    selected = _mapping(prereg.get("selected_extractor"), "prereg.selected_extractor")
    request = _mapping(prereg.get("request_contract"), "prereg.request_contract")
    metrics = _mapping(prereg.get("metrics"), "prereg.metrics")
    if (
        selected.get("model") != MODEL
        or _mapping(selected.get("round_3_prompt"), "round_3_prompt").get("sha256") != PROMPT_SHA256
        or _mapping(selected.get("round_3_contract"), "round_3_contract").get("sha256")
        != ROUND3_CONTRACT_SHA256
        or _mapping(selected.get("heldout_execution_contract"), "heldout_contract").get("sha256")
        != HELDOUT_CONTRACT_SHA256
        or request.get("one_news_item_per_request") is not True
        or request.get("one_request_per_eligible_candidate") is not True
        or request.get("multi_item_prompt_forbidden") is not True
        or request.get("prefilter_forbidden") is not True
        or request.get("additional_body_shortening_for_cost_forbidden") is not True
        or request.get("any_candidate_failure") != "terminal_inference_failed_no_sampling_no_retry"
        or _mapping(metrics.get("materiality_precision"), "precision").get("denominator")
        != POSITIVE_COUNT
        or _mapping(metrics.get("materiality_false_omission_rate"), "FOR").get("denominator")
        != NEGATIVE_COUNT
        or _mapping(metrics.get("materiality_recall"), "recall").get("value") is not None
    ):
        raise HeldoutEvaluationError("P4.2a v2 held-out preregistration semantics drifted")
    llm = _mapping(heldout_contract.get("llm"), "heldout_contract.llm")
    shape = _mapping(heldout_contract.get("request_shape"), "heldout_contract.request_shape")
    if (
        llm.get("model") != MODEL
        or llm.get("max_retries") != 0
        or llm.get("enable_thinking") is not False
        or shape.get("one_news_item_per_request") is not True
        or shape.get("one_request_per_eligible_candidate") is not True
        or shape.get("multi_item_prompt_forbidden") is not True
        or shape.get("prefilter_forbidden") is not True
        or shape.get("additional_body_shortening_for_cost_forbidden") is not True
        or shape.get("automatic_retries") != 0
        or shape.get("failed_candidate_retries") != 0
    ):
        raise HeldoutEvaluationError("held-out execution contract drifted")
    round3_files = _mapping(round3_contract.get("contract_files"), "round3.contract_files")
    heldout_files = _mapping(heldout_contract.get("contract_files"), "heldout.contract_files")
    if (
        round3_files != heldout_files
        or _mapping(round3_files.get("prompt"), "prompt").get("sha256") != PROMPT_SHA256
    ):
        raise HeldoutEvaluationError("held-out inference semantics differ from Round 3")

    contract_file_names = {"prompt", "schema", "materialized_schema"}
    _exact_keys(heldout_files, contract_file_names, "heldout.contract_files")
    materialized_schema: JsonObject | None = None
    for name in ("schema", "materialized_schema"):
        path, digest = _bound_ref(root, heldout_files.get(name), f"contract {name}")
        hashes[f"contract_{name}"] = digest
        if name == "materialized_schema":
            materialized_schema = _load_json(path, "materialized result schema")[0]

    frames = _mapping(design.get("frames"), "design.frames")
    frame = _mapping(frames.get("heldout_frame_v2"), "design heldout frame")
    design_lineage = _mapping(frame.get("source_lineage"), "design source lineage")
    prereg_source = _mapping(prereg.get("source_frame"), "prereg source frame")
    prereg_lineage = _mapping(prereg_source.get("source_lineage"), "prereg source lineage")
    lineage_names = {
        "round3_evidence": "round_3_evidence_sha256",
        "round3_independent_review": "round_3_independent_review_sha256",
        "incremental_evidence": "incremental_evidence_sha256",
        "incremental_independent_review": "incremental_independent_review_sha256",
    }
    for name, prereg_name in lineage_names.items():
        _path, digest = _bound_ref(root, design_lineage.get(name), f"source lineage {name}")
        if prereg_lineage.get(prereg_name) != digest:
            raise HeldoutEvaluationError(f"source lineage {name} preregistration drifted")
        hashes[f"source_lineage_{name}"] = digest

    eligibility = _mapping(prereg.get("eligibility_and_sampling"), "prereg eligibility")
    retired_ref = _mapping(eligibility.get("retired_selection"), "retired selection")
    retired_path, retired_digest = _bound_ref(
        root,
        {"path": retired_ref.get("path"), "sha256": retired_ref.get("sha256")},
        "retired selection",
    )
    retired_manifest = _load_json(retired_path, "retired selection")[0]
    retired_selection = _mapping(retired_manifest.get("selection"), "retired selection body")
    retired_rows = _sequence(retired_selection.get("selected"), "retired selected rows")
    retired_ids: list[int] = []
    for raw in retired_rows:
        row = _mapping(raw, "retired selected row")
        identifier = row.get("news_item_id")
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
            raise HeldoutEvaluationError("retired selection contains an invalid ID")
        retired_ids.append(identifier)
    compact_ids = json.dumps(sorted(retired_ids), separators=(",", ":")).encode("ascii")
    if (
        len(retired_ids) != len(set(retired_ids))
        or retired_ref.get("count") != len(retired_ids)
        or retired_ref.get("sorted_ids_compact_json_sha256") != _sha256_bytes(compact_ids)
    ):
        raise HeldoutEvaluationError("retired selection identity binding drifted")
    hashes["retired_selection"] = retired_digest
    if materialized_schema is None:
        raise HeldoutEvaluationError("materialized result schema is unavailable")
    return ControlBundle(
        preregistration=prereg,
        design=design,
        heldout_contract=heldout_contract,
        round3_contract=round3_contract,
        materialized_schema=materialized_schema,
        control_hashes=hashes,
        retired_ids=frozenset(retired_ids),
    )


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _aware_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise HeldoutEvaluationError(f"{label} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise HeldoutEvaluationError(f"{label} must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HeldoutEvaluationError(f"{label} must be a timezone-aware timestamp")
    return parsed


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HeldoutEvaluationError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HeldoutEvaluationError(f"{label} must be a non-negative integer")
    return value


def _registered_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _validate_snapshot(value: object, label: str) -> JsonObject:
    snapshot = dict(_mapping(value, label))
    _exact_keys(snapshot, _SNAPSHOT_FIELDS, label)
    for field in (
        "connection_total_changes",
        "llm_call_count",
        "trade_proposal_count",
        "broker_order_count",
        "non_simulate_order_count",
        "universe_symbol_count",
    ):
        _nonnegative_int(snapshot.get(field), f"{label}.{field}")
    maximum_id = snapshot.get("llm_call_max_id")
    if maximum_id is not None:
        _positive_int(maximum_id, f"{label}.llm_call_max_id")
    query_only = _nonnegative_int(
        snapshot.get("pragma_query_only"), f"{label}.pragma_query_only"
    )
    if (
        snapshot.get("sqlite_uri_mode") != "ro"
        or query_only != 1
        or snapshot.get("connection_total_changes") != 0
        or not isinstance(snapshot.get("news_events_table_exists"), bool)
        or snapshot.get("non_simulate_order_count") != 0
    ):
        raise HeldoutEvaluationError(f"{label} does not prove read-only production safety")
    return snapshot


def _validate_materialized_result(
    value: object, *, candidate: Mapping[str, Any], label: str
) -> JsonObject:
    result = dict(_mapping(value, label))
    _exact_keys(result, set(_MATERIALIZED_LABEL_FIELDS), label)
    symbols = result.get("symbols")
    summary = result.get("summary")
    evidence = result.get("evidence_span")
    confidence = result.get("confidence")
    direction = result.get("direction")
    materiality = result.get("materiality")
    original_text = candidate.get("original_text")
    ingested_symbol = candidate.get("ingested_symbol")
    if (
        not isinstance(symbols, list)
        or len(symbols) > 12
        or any(not isinstance(item, str) or _SYMBOL_RE.fullmatch(item) is None for item in symbols)
        or symbols != sorted(set(symbols))
        or result.get("event_type") not in _EVENT_TYPES
        or isinstance(direction, bool)
        or direction not in (-1, 0, 1)
        or isinstance(materiality, bool)
        or materiality not in (0, 1, 2, 3)
        or not isinstance(summary, str)
        or not summary.strip()
        or len(summary) > 240
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
        or not isinstance(evidence, str)
        or not 1 <= len(evidence) <= 500
        or not isinstance(original_text, str)
        or evidence not in original_text
    ):
        raise HeldoutEvaluationError(f"{label} violates the materialized result schema")
    if any(symbol != ingested_symbol and original_text.find(symbol) < 0 for symbol in symbols):
        raise HeldoutEvaluationError(f"{label} contains an ungrounded symbol")
    return result


def _validate_candidate_rows(
    rows: Sequence[JsonObject],
    *,
    retired_ids: frozenset[int],
    active_contract: EventExtractContract,
) -> tuple[tuple[JsonObject, ...], dict[int, JsonObject]]:
    normalized: list[JsonObject] = []
    by_id: dict[int, JsonObject] = {}
    prior_id = 0
    for index, raw in enumerate(rows, 1):
        row = dict(raw)
        _exact_keys(row, set(pipeline_common.CANDIDATE_KEYS), f"candidate row {index}")
        identifier = _positive_int(row.get("news_item_id"), f"candidate row {index}.news_item_id")
        original_text = row.get("original_text")
        ingested_symbol = row.get("ingested_symbol")
        if (
            identifier <= prior_id
            or identifier in by_id
            or identifier in retired_ids
            or row.get("schema_version") != "p4.2a-heldout-candidate-input-v1.1"
            or row.get("source") not in EXPECTED_RAW_BY_SOURCE
            or row.get("design_sha256") != DESIGN_SHA256
            or row.get("contract_sha256") != HELDOUT_CONTRACT_SHA256
            or row.get("model") != MODEL
            or not isinstance(row.get("url"), str)
            or not row["url"]
            or not isinstance(row.get("title"), str)
            or not row["title"]
            or not isinstance(original_text, str)
            or not original_text
            or not isinstance(row.get("body_state"), str)
            or not row["body_state"]
            or not isinstance(row.get("body_evidence"), Mapping)
            or (
                ingested_symbol is not None
                and (
                    not isinstance(ingested_symbol, str)
                    or _SYMBOL_RE.fullmatch(ingested_symbol) is None
                )
            )
        ):
            raise HeldoutEvaluationError("materialized candidate identity/schema drifted")
        for field in (
            "content_hash",
            "text_sha256",
            "input_sha256",
            "declared_input_sha256",
        ):
            if not _is_sha(row.get(field)):
                raise HeldoutEvaluationError(f"candidate row {index}.{field} is invalid")
        if row.get("text_sha256") != _sha256_bytes(original_text.encode("utf-8")) or row.get(
            "input_sha256"
        ) == row.get("declared_input_sha256"):
            raise HeldoutEvaluationError("materialized candidate hashes drifted")
        try:
            gold_builder.validate_body_evidence(row, label=f"materialized candidate row {index}")
        except gold_builder.GoldSampleError as exc:
            raise HeldoutEvaluationError(
                f"materialized candidate row {index} body evidence drifted"
            ) from exc
        available_time = _aware_datetime(
            row.get("available_time"), f"candidate row {index}.available_time"
        ).astimezone(UTC)
        if not SOURCE_WINDOW_START_UTC <= available_time < SOURCE_WINDOW_END_UTC:
            raise HeldoutEvaluationError(
                f"candidate row {index}.available_time is outside the frozen source window"
            )
        if row.get("published_at") is not None:
            _aware_datetime(row.get("published_at"), f"candidate row {index}.published_at")
        prior_id = identifier
        by_id[identifier] = row
        normalized.append(row)
    try:
        heldout_prepare._validate_candidate_input_hashes(normalized, active_contract)
    except heldout_prepare.HeldoutPreparationError as exc:
        raise HeldoutEvaluationError(
            "materialized candidate contract/body binding drifted"
        ) from exc
    if len(rows) < EXPECTED_COUNT:
        raise HeldoutEvaluationError("eligible materialized pool is smaller than held-out quota")
    return tuple(normalized), by_id


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HeldoutEvaluationError(f"{label} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise HeldoutEvaluationError(f"{label} must be a finite number")
    return normalized


def _validate_execution_authority(value: object) -> str:
    authority = _mapping(value, "materialization.execution_authority")
    _exact_keys(
        authority,
        {
            "mode",
            "frame_authority",
            "successor_code_gate_authority",
            "successor_preregistration",
            "preregistration_commit",
            "implementation_commit",
            "rehearsal_bundle",
            "release_authorization",
        },
        "materialization.execution_authority",
    )
    frame = _mapping(authority.get("frame_authority"), "execution_authority.frame_authority")
    successor = _mapping(
        authority.get("successor_code_gate_authority"),
        "execution_authority.successor_code_gate_authority",
    )
    preregistration = _mapping(
        authority.get("successor_preregistration"),
        "execution_authority.successor_preregistration",
    )
    for reference, label in (
        (frame, "execution_authority.frame_authority"),
        (successor, "execution_authority.successor_code_gate_authority"),
        (preregistration, "execution_authority.successor_preregistration"),
    ):
        _exact_keys(reference, {"path", "sha256"}, label)
    if (
        dict(frame)
        != {"path": str(FRAME_AUTHORITY_PATH), "sha256": FRAME_AUTHORITY_SHA256}
        or dict(successor)
        != {
            "path": str(SUCCESSOR_CODE_GATE_AUTHORITY_PATH),
            "sha256": SUCCESSOR_CODE_GATE_AUTHORITY_SHA256,
        }
        or dict(preregistration)
        != {
            "path": str(SUCCESSOR_PREREGISTRATION_PATH),
            "sha256": SUCCESSOR_PREREGISTRATION_SHA256,
        }
        or authority.get("preregistration_commit") != SUCCESSOR_PREREGISTRATION_COMMIT
        or not isinstance(authority.get("implementation_commit"), str)
        or _GIT_COMMIT_RE.fullmatch(cast(str, authority["implementation_commit"])) is None
    ):
        raise HeldoutEvaluationError("materialization execution authority drifted")

    mode = authority.get("mode")
    if mode == "offline_rehearsal":
        if authority.get("rehearsal_bundle") is not None or authority.get(
            "release_authorization"
        ) is not None:
            raise HeldoutEvaluationError(
                "offline materialization authority must be nonrecursive"
            )
        return mode
    if mode != "real_owner_released":
        raise HeldoutEvaluationError("materialization execution authority mode drifted")

    bundle = _mapping(authority.get("rehearsal_bundle"), "execution_authority.rehearsal_bundle")
    release = _mapping(
        authority.get("release_authorization"),
        "execution_authority.release_authorization",
    )
    _exact_keys(bundle, {"path", "sha256", "bundle_root_sha256"}, "rehearsal bundle")
    _exact_keys(
        release,
        {"path", "sha256", "receipt_creating_commit", "verdict"},
        "release authorization",
    )
    if (
        bundle.get("path") != str(SUCCESSOR_REHEARSAL_BUNDLE_PATH)
        or not _is_sha(bundle.get("sha256"))
        or not _is_sha(bundle.get("bundle_root_sha256"))
        or release.get("path") != str(SUCCESSOR_RELEASE_AUTHORIZATION_PATH)
        or not _is_sha(release.get("sha256"))
        or not isinstance(release.get("receipt_creating_commit"), str)
        or _GIT_COMMIT_RE.fullmatch(cast(str, release["receipt_creating_commit"])) is None
        or release.get("verdict")
        != "APPROVE_SUCCESSOR_V2_1_REAL_HELDOUT_PREPARATION"
    ):
        raise HeldoutEvaluationError("real materialization release authority drifted")
    return mode


def _validate_request_pacing(value: object) -> None:
    pacing = _mapping(value, "materialization.request_pacing")
    _exact_keys(
        pacing,
        {"cninfo_pdf", "akshare_ths", "sina_company_news"},
        "materialization.request_pacing",
    )
    if (
        pacing.get("akshare_ths") != "not_applicable_no_external_document_fetch"
        or pacing.get("sina_company_news")
        != "not_applicable_no_external_document_fetch"
    ):
        raise HeldoutEvaluationError("non-CNInfo request pacing evidence drifted")
    cninfo = _mapping(pacing.get("cninfo_pdf"), "materialization.request_pacing.cninfo_pdf")
    _exact_keys(
        cninfo,
        {
            "host",
            "policy",
            "configured_min_start_to_start_seconds",
            "clock",
            "first_request_delayed",
            "request_start_count",
            "observed_gap_count",
            "minimum_observed_start_to_start_seconds",
            "median_observed_start_to_start_seconds",
            "violation_count",
            "retry_count",
        },
        "materialization.request_pacing.cninfo_pdf",
    )
    request_count = _nonnegative_int(cninfo.get("request_start_count"), "CNInfo request count")
    gap_count = _nonnegative_int(cninfo.get("observed_gap_count"), "CNInfo gap count")
    violation_count = _nonnegative_int(cninfo.get("violation_count"), "CNInfo violation count")
    retry_count = _nonnegative_int(cninfo.get("retry_count"), "CNInfo retry count")
    minimum = cninfo.get("minimum_observed_start_to_start_seconds")
    median = cninfo.get("median_observed_start_to_start_seconds")
    if gap_count == 0:
        gap_statistics_valid = minimum is None and median is None
    else:
        minimum_value = _finite_number(minimum, "CNInfo minimum observed gap")
        median_value = _finite_number(median, "CNInfo median observed gap")
        gap_statistics_valid = (
            minimum_value >= 1.0
            and median_value >= minimum_value
        )
    if (
        cninfo.get("host") != "static.cninfo.com.cn"
        or cninfo.get("policy") != "minimum_start_to_start"
        or cninfo.get("configured_min_start_to_start_seconds") != 1.0
        or cninfo.get("clock") != "monotonic"
        or cninfo.get("first_request_delayed") is not False
        or request_count != EXPECTED_RAW_BY_SOURCE["cninfo"]
        or gap_count != max(request_count - 1, 0)
        or not gap_statistics_valid
        or violation_count != 0
        or retry_count != 0
    ):
        raise HeldoutEvaluationError("CNInfo request pacing evidence drifted")


def _validate_runtime_start_preflight(value: object, *, authority_mode: str) -> None:
    preflight = _mapping(value, "materialization.runtime_start_preflight")
    if authority_mode == "offline_rehearsal":
        _exact_keys(
            preflight,
            {"mode", "host_probe_performed", "reason"},
            "offline runtime start preflight",
        )
        if dict(preflight) != {
            "mode": "offline_rehearsal",
            "host_probe_performed": False,
            "reason": "not_applicable_offline_rehearsal",
        }:
            raise HeldoutEvaluationError("offline runtime start preflight drifted")
        return

    _exact_keys(
        preflight,
        {
            "mode",
            "observed_at_utc",
            "observed_at_shanghai",
            "backup_stamp",
            "database_backup_launchagent",
            "database_backup_lock",
            "verified_backup",
            "operator_timing_attestation",
        },
        "real runtime start preflight",
    )
    observed_utc = _aware_datetime(preflight.get("observed_at_utc"), "runtime observed_at_utc")
    observed_shanghai = _aware_datetime(
        preflight.get("observed_at_shanghai"), "runtime observed_at_shanghai"
    )
    if observed_utc.astimezone(UTC) != observed_shanghai.astimezone(UTC):
        raise HeldoutEvaluationError("runtime start timestamps disagree")

    stamp = _mapping(preflight.get("backup_stamp"), "runtime backup stamp")
    launchagent = _mapping(
        preflight.get("database_backup_launchagent"), "runtime launchagent"
    )
    lock = _mapping(preflight.get("database_backup_lock"), "runtime backup lock")
    backup = _mapping(preflight.get("verified_backup"), "runtime verified backup")
    attestation = _mapping(
        preflight.get("operator_timing_attestation"), "runtime operator attestation"
    )
    _exact_keys(
        stamp,
        {"path", "expected_shanghai_date", "observed_value", "regular_file", "symlink", "mode"},
        "runtime backup stamp",
    )
    _exact_keys(
        launchagent,
        {"label", "target", "loaded", "state", "last_exit_code"},
        "runtime launchagent",
    )
    _exact_keys(
        lock,
        {"path", "nonblocking_exclusive_flock_acquired", "held"},
        "runtime backup lock",
    )
    _exact_keys(
        backup,
        {
            "manifest_path",
            "manifest_sha256",
            "backup_path",
            "backup_sha256",
            "created_at_utc",
            "created_at_shanghai",
            "quick_check",
            "verify_database_backup_passed",
        },
        "runtime verified backup",
    )
    _exact_keys(
        attestation,
        {
            "observed_start_cst",
            "attester_identity",
            "explicitly_supplied",
            "input_channel",
            "cninfo_midnight_batch_assessment",
            "p4_1_dense_poll_slot_assessment",
            "decision",
            "automatic_blackout_verification",
            "authority_path",
            "authority_sha256",
        },
        "runtime operator attestation",
    )
    expected_date = observed_shanghai.date().isoformat()
    backup_created_utc = _aware_datetime(
        backup.get("created_at_utc"), "verified backup created_at_utc"
    )
    backup_created_shanghai = _aware_datetime(
        backup.get("created_at_shanghai"), "verified backup created_at_shanghai"
    )
    operator_start = _aware_datetime(
        attestation.get("observed_start_cst"), "operator observed_start_cst"
    )
    attester = attestation.get("attester_identity")
    if (
        preflight.get("mode") != "real"
        or stamp.get("expected_shanghai_date") != expected_date
        or stamp.get("observed_value") != expected_date
        or stamp.get("regular_file") is not True
        or stamp.get("symlink") is not False
        or stamp.get("mode") != "0600"
        or launchagent.get("label") != "com.alphapilot.database-backup"
        or launchagent.get("loaded") is not True
        or launchagent.get("state") != "not running"
        or launchagent.get("last_exit_code") != 0
        or lock.get("nonblocking_exclusive_flock_acquired") is not True
        or lock.get("held") is not False
        or not _is_sha(backup.get("manifest_sha256"))
        or not _is_sha(backup.get("backup_sha256"))
        or backup_created_utc.astimezone(UTC) != backup_created_shanghai.astimezone(UTC)
        or backup_created_shanghai.date().isoformat() != expected_date
        or backup_created_shanghai.hour < 22
        or backup.get("quick_check") != "ok"
        or backup.get("verify_database_backup_passed") is not True
        or operator_start.astimezone(UTC) != observed_utc.astimezone(UTC)
        or not isinstance(attester, str)
        or not attester.strip()
        or attestation.get("explicitly_supplied") is not True
        or attestation.get("input_channel")
        != "required_real_CLI_flags_or_required_typed_run_materialize_argument_no_default"
        or attestation.get("cninfo_midnight_batch_assessment") != "clear_for_start"
        or attestation.get("p4_1_dense_poll_slot_assessment") != "clear_for_start"
        or attestation.get("decision")
        != "launched_outside_owner_identified_CNInfo_midnight_and_dense_P4_1_slots"
        or attestation.get("automatic_blackout_verification") is not False
        or attestation.get("authority_path") != str(SUCCESSOR_CODE_GATE_AUTHORITY_PATH)
        or attestation.get("authority_sha256") != SUCCESSOR_CODE_GATE_AUTHORITY_SHA256
    ):
        raise HeldoutEvaluationError("real runtime start preflight drifted")
    for field, expected_name in (
        (stamp.get("path"), "last-success-shanghai-date"),
        (lock.get("path"), ".daily-backup.lock"),
    ):
        if not isinstance(field, str) or Path(field).name != expected_name:
            raise HeldoutEvaluationError("runtime backup path evidence drifted")
    target = launchagent.get("target")
    if (
        not isinstance(target, str)
        or not target.startswith("gui/")
        or not target.endswith("/com.alphapilot.database-backup")
        or any(
            not isinstance(backup.get(field), str) or not cast(str, backup[field])
            for field in ("manifest_path", "backup_path")
        )
    ):
        raise HeldoutEvaluationError("runtime backup evidence drifted")


def _validate_materialization_manifest(
    manifest: JsonObject,
    candidates: Sequence[JsonObject],
    *,
    controls: ControlBundle,
    root: Path,
    paths: ArtifactPaths,
    inputs_payload: bytes,
    control_root: Path = PROJECT_ROOT,
) -> None:
    _exact_keys(
        manifest,
        {
            "schema_version",
            "frame_id",
            "lineage",
            "artifacts",
            "counts",
            "layers",
            "production_database",
            "execution_authority",
            "request_pacing",
            "runtime_start_preflight",
        },
        "materialization manifest",
    )
    authority_mode = _validate_execution_authority(manifest.get("execution_authority"))
    _validate_request_pacing(manifest.get("request_pacing"))
    _validate_runtime_start_preflight(
        manifest.get("runtime_start_preflight"), authority_mode=authority_mode
    )
    lineage = _mapping(manifest.get("lineage"), "materialization.lineage")
    _exact_keys(
        lineage,
        {
            "preregistration",
            "design",
            "contract",
            "source_window",
            "source_lineage",
            "retired_selection_sha256",
        },
        "materialization.lineage",
    )
    expected_source = _mapping(controls.preregistration.get("source_frame"), "prereg source")
    expected_prereg_lineage = _mapping(expected_source.get("source_lineage"), "prereg lineage")
    source_lineage = _mapping(lineage.get("source_lineage"), "materialization source lineage")
    _mapping(source_lineage.get("evidence"), "materialization source evidence")
    design_frames = _mapping(controls.design.get("frames"), "design.frames")
    design_frame = _mapping(design_frames.get("heldout_frame_v2"), "design heldout frame")
    design_lineage = _mapping(design_frame.get("source_lineage"), "design source lineage")
    expected_evidence = {
        name: dict(_mapping(design_lineage.get(name), f"design lineage {name}"))
        for name in (
            "round3_evidence",
            "round3_independent_review",
            "incremental_evidence",
            "incremental_independent_review",
        )
    }
    if source_lineage != {
        "required_closed_dates_shanghai": expected_prereg_lineage.get(
            "required_closed_dates_shanghai"
        ),
        "verified_checkpoint_date_shanghai": expected_prereg_lineage.get(
            "verified_checkpoint_date_shanghai"
        ),
        "migration_job_run_ids": expected_prereg_lineage.get("migration_job_run_ids"),
        "evidence": expected_evidence,
    }:
        raise HeldoutEvaluationError("materialization source lineage drifted")
    retired = _mapping(
        _mapping(
            controls.preregistration.get("eligibility_and_sampling"), "prereg eligibility"
        ).get("retired_selection"),
        "retired selection",
    )
    production_database = _mapping(
        manifest.get("production_database"), "materialization.production_database"
    )
    _exact_keys(
        production_database,
        {"mode", "pragma_query_only", "writes"},
        "materialization.production_database",
    )
    production_query_only = _nonnegative_int(
        production_database.get("pragma_query_only"),
        "materialization.production_database.pragma_query_only",
    )
    production_writes = _nonnegative_int(
        production_database.get("writes"),
        "materialization.production_database.writes",
    )
    if (
        manifest.get("schema_version") != MATERIALIZATION_MANIFEST_SCHEMA
        or manifest.get("frame_id") != FRAME_ID
        or lineage.get("preregistration")
        != {"path": str(PREREGISTRATION_PATH), "sha256": PREREGISTRATION_SHA256}
        or lineage.get("design") != DESIGN_REF
        or lineage.get("contract")
        != {"path": str(HELDOUT_CONTRACT_PATH), "sha256": HELDOUT_CONTRACT_SHA256, "model": MODEL}
        or lineage.get("source_window")
        != {
            "start_inclusive_utc": "2026-08-05T16:00:00Z",
            "end_exclusive_utc": "2026-08-08T16:00:00Z",
        }
        or lineage.get("retired_selection_sha256") != retired.get("sha256")
        or production_database.get("mode") != "ro"
        or production_query_only != 1
        or production_writes != 0
    ):
        raise HeldoutEvaluationError("materialization header/control binding drifted")
    artifacts = _mapping(manifest.get("artifacts"), "materialization.artifacts")
    _exact_keys(artifacts, {"eligible_inputs_jsonl", "manifest"}, "materialization.artifacts")
    if artifacts.get("eligible_inputs_jsonl") != {
        "path": _registered_path(root, paths.materialized_inputs),
        "sha256": _sha256_bytes(inputs_payload),
        "create_only": True,
    } or artifacts.get("manifest") != {
        "path": _registered_path(root, paths.materialization_manifest),
        "create_only": True,
    }:
        raise HeldoutEvaluationError("materialization artifact binding drifted")
    source_frame = _mapping(
        controls.preregistration.get("source_frame"), "preregistration.source_frame"
    )
    expected_raw_count = _nonnegative_int(
        source_frame.get("expected_raw_candidate_count"),
        "preregistration.source_frame.expected_raw_candidate_count",
    )
    raw_by_source = _mapping(
        source_frame.get("expected_raw_candidates_by_source"),
        "preregistration.source_frame.expected_raw_candidates_by_source",
    )
    _exact_keys(
        raw_by_source,
        set(EXPECTED_RAW_BY_SOURCE),
        "preregistration.source_frame.expected_raw_candidates_by_source",
    )
    expected_raw_by_source = {
        source: _nonnegative_int(
            raw_by_source.get(source),
            f"preregistration.source_frame.expected_raw_candidates_by_source.{source}",
        )
        for source in EXPECTED_RAW_BY_SOURCE
    }
    if (
        expected_raw_count != EXPECTED_RAW_COUNT
        or expected_raw_by_source != EXPECTED_RAW_BY_SOURCE
        or sum(expected_raw_by_source.values()) != expected_raw_count
    ):
        raise HeldoutEvaluationError("preregistered raw source composition drifted")

    counts = _mapping(manifest.get("counts"), "materialization.counts")
    _exact_keys(
        counts,
        {
            "raw_source_window",
            "retired_excluded_before_materialization",
            "all_candidates_after_retirement",
            "eligible_candidates",
            "ineligible_candidates",
            "ineligible_by_reason",
        },
        "materialization.counts",
    )
    raw_source_window = _nonnegative_int(
        counts.get("raw_source_window"), "raw source-window candidates"
    )
    retired_excluded = _nonnegative_int(
        counts.get("retired_excluded_before_materialization"),
        "retired candidates excluded before materialization",
    )
    all_after = _nonnegative_int(counts.get("all_candidates_after_retirement"), "all candidates")
    eligible_count = _nonnegative_int(
        counts.get("eligible_candidates"), "eligible candidates"
    )
    ineligible_count = _nonnegative_int(
        counts.get("ineligible_candidates"), "ineligible candidates"
    )
    if (
        raw_source_window != expected_raw_count
        or retired_excluded != 0
        or all_after != expected_raw_count
        or eligible_count != len(candidates)
        or all_after != len(candidates) + ineligible_count
    ):
        raise HeldoutEvaluationError("materialization counts drifted")
    layers = _mapping(manifest.get("layers"), "materialization.layers")
    _exact_keys(
        layers,
        {"all_candidates", "eligible_candidates", "ineligible_candidates"},
        "materialization.layers",
    )
    all_rows = _sequence(layers.get("all_candidates"), "materialization all candidates")
    eligible_rows = _sequence(
        layers.get("eligible_candidates"), "materialization eligible candidates"
    )
    ineligible_rows = _sequence(
        layers.get("ineligible_candidates"), "materialization ineligible candidates"
    )
    expected_eligible = [
        {
            field: row[field]
            for field in (
                "news_item_id",
                "source",
                "input_sha256",
                "declared_input_sha256",
                "text_sha256",
            )
        }
        for row in candidates
    ]
    all_ids: list[int] = []
    all_by_id: dict[int, Mapping[str, Any]] = {}
    all_source_counts: Counter[str] = Counter()
    for raw in all_rows:
        row = _mapping(raw, "all candidate")
        identity_fields = {"news_item_id", "source", "url", "content_hash"}
        if set(row) != identity_fields:
            raise HeldoutEvaluationError("materialization all candidate fields drifted")
        identifier = _positive_int(row.get("news_item_id"), "all candidate ID")
        if (
            row.get("source") not in EXPECTED_RAW_BY_SOURCE
            or not isinstance(row.get("url"), str)
            or not row["url"]
            or not _is_sha(row.get("content_hash"))
        ):
            raise HeldoutEvaluationError("materialization all-candidate identity drifted")
        all_ids.append(identifier)
        all_by_id[identifier] = row
        all_source_counts[cast(str, row["source"])] += 1
    if dict(sorted(all_source_counts.items())) != dict(sorted(expected_raw_by_source.items())):
        raise HeldoutEvaluationError("materialization raw source composition drifted")
    for candidate in candidates:
        identifier = cast(int, candidate["news_item_id"])
        identity = all_by_id.get(identifier)
        if identity is None or any(
            identity.get(field) != candidate.get(field)
            for field in ("source", "url", "content_hash")
        ):
            raise HeldoutEvaluationError(
                "materialization eligible identity differs from all-candidate layer"
            )
    v17_document = _mapping(
        yaml.safe_load((control_root / "config/p4_event_evaluation_v1_7.yaml").read_bytes()),
        "v1.7 eligibility design",
    )
    eligibility_policy = _mapping(
        v17_document.get("candidate_eligibility"),
        "v1.7 candidate eligibility",
    )
    minimum_characters = _positive_int(
        eligibility_policy.get("minimum_extracted_characters"),
        "minimum extracted characters",
    )
    maximum_pdf_bytes = _positive_int(
        eligibility_policy.get("max_pdf_bytes"),
        "maximum PDF bytes",
    )
    ineligible_ids: list[int] = []
    eligible_ids = [cast(int, row["news_item_id"]) for row in candidates]
    reason_counts: dict[str, int] = {}
    for raw in ineligible_rows:
        row = _mapping(raw, "ineligible candidate")
        _exact_keys(
            row,
            {
                "news_item_id",
                "url",
                "reason",
                "measured_value",
                "gate_value",
                "pdf_sha256",
            },
            "materialization ineligible candidate",
        )
        identifier = _positive_int(row.get("news_item_id"), "ineligible candidate ID")
        reason = row.get("reason")
        if reason not in {"pdf_text_below_min_char_gate", "pdf_exceeds_size_bound"}:
            raise HeldoutEvaluationError("materialization ineligible reason drifted")
        measured = _nonnegative_int(row.get("measured_value"), "ineligible measured value")
        gate = _positive_int(row.get("gate_value"), "ineligible gate value")
        pdf_sha = row.get("pdf_sha256")
        all_identity = all_by_id.get(identifier)
        url_value = row.get("url")
        parsed_url = urlparse(url_value) if isinstance(url_value, str) else None
        if (
            all_identity is None
            or all_identity.get("source") != "cninfo"
            or url_value != all_identity.get("url")
            or parsed_url is None
            or parsed_url.scheme != "https"
            or parsed_url.hostname != "static.cninfo.com.cn"
            or not parsed_url.path.casefold().endswith(".pdf")
            or (pdf_sha is not None and not _is_sha(pdf_sha))
            or (
                reason == "pdf_text_below_min_char_gate"
                and (
                    gate != minimum_characters
                    or measured >= gate
                    or pdf_sha is None
                )
            )
            or (
                reason == "pdf_exceeds_size_bound"
                and (gate != maximum_pdf_bytes or measured <= gate)
            )
        ):
            raise HeldoutEvaluationError("materialization ineligible evidence drifted")
        ineligible_ids.append(identifier)
        reason_counts[cast(str, reason)] = reason_counts.get(cast(str, reason), 0) + 1
    if (
        len(all_rows) != all_after
        or len(all_ids) != len(set(all_ids))
        or all_ids != sorted(all_ids)
        or len(ineligible_rows) != ineligible_count
        or len(ineligible_ids) != len(set(ineligible_ids))
        or set(eligible_ids).intersection(ineligible_ids)
        or set(all_ids) != set(eligible_ids).union(ineligible_ids)
        or set(all_ids).intersection(controls.retired_ids)
        or list(eligible_rows) != expected_eligible
        or counts.get("ineligible_by_reason") != dict(sorted(reason_counts.items()))
    ):
        raise HeldoutEvaluationError("materialization layer coverage drifted")


def _label(value: object, *, original_text: str, label: str) -> JsonObject:
    result = dict(_mapping(value, label))
    _exact_keys(result, set(_LABEL_FIELDS), label)
    symbols = result.get("symbols")
    evidence = result.get("evidence_span")
    notes = result.get("notes")
    if (
        not isinstance(symbols, list)
        or any(not isinstance(item, str) or _SYMBOL_RE.fullmatch(item) is None for item in symbols)
        or symbols != sorted(set(symbols))
        or result.get("event_type") not in _EVENT_TYPES
        or isinstance(result.get("direction"), bool)
        or result.get("direction") not in (-1, 0, 1)
        or isinstance(result.get("materiality"), bool)
        or result.get("materiality") not in (0, 1, 2, 3)
        or not isinstance(evidence, str)
        or not evidence
        or evidence not in original_text
        or (notes is not None and (not isinstance(notes, str) or not notes.strip()))
    ):
        raise HeldoutEvaluationError(f"{label} violates the frozen label schema")
    return result


def _recursive_forbidden(value: object, path: str = "$") -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in _FORBIDDEN_BLIND_KEYS:
                return f"{path}.{key}"
            nested = _recursive_forbidden(item, f"{path}.{key}")
            if nested is not None:
                return nested
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            nested = _recursive_forbidden(item, f"{path}[{index}]")
            if nested is not None:
                return nested
    return None


def _identity(row: Mapping[str, Any]) -> tuple[int, int, str]:
    sample_index = row.get("sample_index")
    news_item_id = row.get("news_item_id")
    input_sha = row.get("input_sha256")
    if (
        isinstance(sample_index, bool)
        or not isinstance(sample_index, int)
        or isinstance(news_item_id, bool)
        or not isinstance(news_item_id, int)
        or not _is_sha(input_sha)
    ):
        raise HeldoutEvaluationError("held-out artifact identity is invalid")
    return sample_index, news_item_id, cast(str, input_sha)


def _validate_prediction_rows(
    rows: Sequence[JsonObject], candidates: Sequence[JsonObject]
) -> tuple[dict[int, JsonObject], dict[int, str], dict[str, int]]:
    if len(rows) != len(candidates):
        raise HeldoutEvaluationError("full eligible prediction coverage drifted")
    by_id: dict[int, JsonObject] = {}
    strata: dict[int, str] = {}
    counts = {"predicted_positive": 0, "predicted_negative": 0}
    expected_security = {
        "credentials_persisted": False,
        "exception_detail_persisted": False,
        "llm_audit_storage": "isolated_in_memory",
        "llm_audit_status": "recorded",
        "production_database_access": "sqlite_uri_mode_ro_query_only",
        "raw_prompt_persisted": False,
        "raw_transport_response_persisted": False,
        "redaction_status": "passed",
    }
    for index, (raw, candidate) in enumerate(zip(rows, candidates, strict=True), 1):
        row = dict(raw)
        _exact_keys(row, set(pipeline_common.PREDICTION_KEYS), f"prediction row {index}")
        identifier = _positive_int(row.get("news_item_id"), f"prediction row {index}.news_item_id")
        if identifier in by_id or identifier != candidate.get("news_item_id"):
            raise HeldoutEvaluationError("prediction IDs/order do not cover the full eligible pool")
        if any(row.get(field) != candidate.get(field) for field in pipeline_common.JOIN_FIELDS):
            raise HeldoutEvaluationError("prediction source/join fields drifted")
        if row.get("schema_version") != "p4.2a-offline-extract-row-v1" or row.get("status") != "ok":
            raise HeldoutEvaluationError("every eligible prediction must be one successful row")
        _aware_datetime(row.get("recorded_at_utc"), f"prediction row {index}.recorded_at_utc")
        _nonnegative_int(row.get("latency_ms"), f"prediction row {index}.latency_ms")
        _nonnegative_int(
            row.get("llm_audit_latency_ms"), f"prediction row {index}.llm_audit_latency_ms"
        )
        tokens = _mapping(row.get("tokens"), f"prediction row {index}.tokens")
        _exact_keys(
            tokens, {"prompt_tokens", "completion_tokens"}, f"prediction row {index}.tokens"
        )
        for name in ("prompt_tokens", "completion_tokens"):
            value = tokens.get(name)
            if value is not None:
                _nonnegative_int(value, f"prediction row {index}.tokens.{name}")
        if (
            dict(_mapping(row.get("security"), f"prediction row {index}.security"))
            != expected_security
        ):
            raise HeldoutEvaluationError("prediction security evidence drifted")
        prediction = _validate_materialized_result(
            row.get("prediction"), candidate=candidate, label=f"prediction row {index}.prediction"
        )
        materiality = cast(int, prediction["materiality"])
        stratum = "predicted_positive" if materiality >= 2 else "predicted_negative"
        counts[stratum] += 1
        strata[identifier] = stratum
        by_id[identifier] = row
    return by_id, strata, counts


def _validate_inference_chain(
    states: Sequence[JsonObject],
    prediction_manifest: JsonObject,
    *,
    candidates: Sequence[JsonObject],
    predictions: Sequence[JsonObject],
    root: Path,
    paths: ArtifactPaths,
    materialization_payload: bytes,
    inference_payload: bytes,
    predictions_payload: bytes,
    prediction_manifest_payload: bytes,
    preregistered_at: datetime,
) -> tuple[str, datetime]:
    if len(states) != 2:
        raise HeldoutEvaluationError("inference state must contain exactly start and completion")
    started = dict(states[0])
    completed = dict(states[1])
    _exact_keys(
        started,
        {
            "schema_version",
            "status",
            "execution_id",
            "started_at_utc",
            "eligible_candidate_count",
            "candidate_order",
            "preregistration_sha256",
            "materialization_manifest_sha256",
            "contract_sha256",
            "model",
            "automatic_retries",
            "failed_candidate_retries",
            "settings_safety",
            "production_snapshot_before",
        },
        "inference started event",
    )
    _exact_keys(
        completed,
        {
            "schema_version",
            "status",
            "execution_id",
            "preregistration_sha256",
            "materialization_manifest_sha256",
            "completed_at_utc",
            "prediction_count",
            "predictions_sha256",
            "prediction_manifest_sha256",
            "production_snapshot_unchanged",
            "production_snapshot_before",
            "production_snapshot_after",
        },
        "inference completed event",
    )
    _exact_keys(
        prediction_manifest,
        {
            "schema_version",
            "frame_id",
            "execution_id",
            "preregistration_sha256",
            "materialization_manifest_sha256",
            "contract_sha256",
            "model",
            "candidate_count",
            "prediction_count",
            "status_ok_count",
            "status_failed_count",
            "one_news_item_per_request",
            "one_request_per_eligible_candidate",
            "automatic_retries",
            "failed_candidate_retries",
            "production_snapshot_unchanged",
            "production_snapshot_before",
            "production_snapshot_after",
            "settings_safety",
            "predictions",
        },
        "prediction manifest",
    )
    execution_id = started.get("execution_id")
    if not isinstance(execution_id, str):
        raise HeldoutEvaluationError("inference execution ID is invalid")
    try:
        uuid.UUID(execution_id)
    except ValueError as exc:
        raise HeldoutEvaluationError("inference execution ID is invalid") from exc
    started_at = _aware_datetime(started.get("started_at_utc"), "inference started_at")
    completed_at = _aware_datetime(completed.get("completed_at_utc"), "inference completed_at")
    if started_at < preregistered_at or started_at < SOURCE_WINDOW_END_UTC:
        raise HeldoutEvaluationError(
            "inference start precedes preregistration or source-window closure"
        )
    if completed_at < started_at:
        raise HeldoutEvaluationError("inference completion precedes its start")
    previous_recorded_at = started_at
    for index, prediction in enumerate(predictions, 1):
        recorded_at = _aware_datetime(
            prediction.get("recorded_at_utc"),
            f"prediction row {index}.recorded_at_utc",
        )
        if (
            recorded_at < started_at
            or recorded_at > completed_at
            or recorded_at < previous_recorded_at
        ):
            raise HeldoutEvaluationError(
                "prediction recorded_at timeline is outside or reorders inference"
            )
        previous_recorded_at = recorded_at
    before = _validate_snapshot(
        started.get("production_snapshot_before"), "production snapshot before"
    )
    completed_before = _validate_snapshot(
        completed.get("production_snapshot_before"), "completed snapshot before"
    )
    after = _validate_snapshot(
        completed.get("production_snapshot_after"), "production snapshot after"
    )
    manifest_before = _validate_snapshot(
        prediction_manifest.get("production_snapshot_before"), "manifest snapshot before"
    )
    manifest_after = _validate_snapshot(
        prediction_manifest.get("production_snapshot_after"), "manifest snapshot after"
    )
    materialization_sha = _sha256_bytes(materialization_payload)
    predictions_sha = _sha256_bytes(predictions_payload)
    manifest_sha = _sha256_bytes(prediction_manifest_payload)
    if (
        started.get("schema_version") != "p4.2a-v2-heldout-inference-state-v1"
        or started.get("status") != "inference_started"
        or started.get("eligible_candidate_count") != len(candidates)
        or started.get("candidate_order") != "ascending_news_item_id"
        or started.get("preregistration_sha256") != PREREGISTRATION_SHA256
        or started.get("materialization_manifest_sha256") != materialization_sha
        or started.get("contract_sha256") != HELDOUT_CONTRACT_SHA256
        or started.get("model") != MODEL
        or started.get("automatic_retries") != 0
        or started.get("failed_candidate_retries") != 0
        or started.get("settings_safety") != _INFERENCE_SETTINGS_SAFETY
        or completed.get("schema_version") != "p4.2a-v2-heldout-inference-state-v1"
        or completed.get("status") != "completed_all_eligible_candidates_once"
        or completed.get("execution_id") != execution_id
        or completed.get("preregistration_sha256") != PREREGISTRATION_SHA256
        or completed.get("materialization_manifest_sha256") != materialization_sha
        or completed.get("prediction_count") != len(predictions)
        or completed.get("predictions_sha256") != predictions_sha
        or completed.get("prediction_manifest_sha256") != manifest_sha
        or completed.get("production_snapshot_unchanged") is not True
        or before != completed_before
        or before != after
        or prediction_manifest.get("schema_version") != "p4.2a-v2-heldout-prediction-manifest-v1"
        or prediction_manifest.get("frame_id") != FRAME_ID
        or prediction_manifest.get("execution_id") != execution_id
        or prediction_manifest.get("preregistration_sha256") != PREREGISTRATION_SHA256
        or prediction_manifest.get("materialization_manifest_sha256") != materialization_sha
        or prediction_manifest.get("contract_sha256") != HELDOUT_CONTRACT_SHA256
        or prediction_manifest.get("model") != MODEL
        or prediction_manifest.get("candidate_count") != len(candidates)
        or prediction_manifest.get("prediction_count") != len(predictions)
        or prediction_manifest.get("status_ok_count") != len(predictions)
        or prediction_manifest.get("status_failed_count") != 0
        or prediction_manifest.get("one_news_item_per_request") is not True
        or prediction_manifest.get("one_request_per_eligible_candidate") is not True
        or prediction_manifest.get("automatic_retries") != 0
        or prediction_manifest.get("failed_candidate_retries") != 0
        or prediction_manifest.get("production_snapshot_unchanged") is not True
        or prediction_manifest.get("settings_safety") != _INFERENCE_SETTINGS_SAFETY
        or manifest_before != before
        or manifest_after != after
        or prediction_manifest.get("predictions")
        != {"path": _registered_path(root, paths.predictions), "sha256": predictions_sha}
    ):
        raise HeldoutEvaluationError("inference execution/prereg/contract/snapshot chain drifted")
    return execution_id, completed_at


def _validate_selection(
    selection: JsonObject,
    *,
    candidates: Sequence[JsonObject],
    predictions_by_id: Mapping[int, JsonObject],
    strata: Mapping[int, str],
    stratum_counts: Mapping[str, int],
    retired_ids: frozenset[int],
    execution_id: str,
    root: Path,
    paths: ArtifactPaths,
    payloads: Mapping[str, bytes],
) -> tuple[JsonObject, ...]:
    _exact_keys(
        selection,
        {
            "schema_version",
            "design",
            "frame_id",
            "source_lineage",
            "audit",
            "selection",
            "owner_delivery",
            "production_writes",
        },
        "selection manifest",
    )
    if (
        selection.get("schema_version") != "p4.2a-v2-heldout-selection-manifest-v1"
        or selection.get("design") != DESIGN_REF
        or selection.get("frame_id") != FRAME_ID
        or selection.get("production_writes") is not False
    ):
        raise HeldoutEvaluationError("held-out selection header drifted")
    ranked_selected: list[tuple[str, str, JsonObject]] = []
    for stratum, quota in (
        ("predicted_positive", POSITIVE_COUNT),
        ("predicted_negative", NEGATIVE_COUNT),
    ):
        ranked = sorted(
            (
                pipeline_common.selection_rank(
                    seed=SELECTION_SEED,
                    sampling_stratum=stratum,
                    news_item_id=cast(int, row["news_item_id"]),
                    input_sha256=cast(str, row["input_sha256"]),
                ),
                row,
            )
            for row in candidates
            if strata[cast(int, row["news_item_id"])] == stratum
        )
        if len(ranked) < quota:
            raise HeldoutEvaluationError(f"insufficient full-pool {stratum} rows")
        ranked_selected.extend((stratum, rank, row) for rank, row in ranked[:quota])
    ordered = sorted(
        (
            pipeline_common.owner_order_rank(
                design_sha256=DESIGN_SHA256,
                news_item_id=cast(int, row["news_item_id"]),
                input_sha256=cast(str, row["input_sha256"]),
            ),
            stratum,
            rank,
            row,
        )
        for stratum, rank, row in ranked_selected
    )
    expected_selected: list[JsonObject] = []
    selected_bindings: list[JsonObject] = []
    for index, (owner_rank, stratum, rank, candidate) in enumerate(ordered, 1):
        identifier = cast(int, candidate["news_item_id"])
        expected_selected.append(
            {
                "sample_index": index,
                "news_item_id": identifier,
                "source": candidate["source"],
                "input_sha256": candidate["input_sha256"],
                "declared_input_sha256": candidate["declared_input_sha256"],
                "text_sha256": candidate["text_sha256"],
                "contract_sha256": candidate["contract_sha256"],
                "model": candidate["model"],
                "sampling_stratum": stratum,
                "selection_rank_sha256": rank,
                "owner_order_sha256": owner_rank,
            }
        )
        selected_bindings.append(
            {
                "sample_index": index,
                "news_item_id": identifier,
                "prediction_row_sha256": _sha256_bytes(
                    _canonical_json_bytes(predictions_by_id[identifier])
                ),
            }
        )
    body = _mapping(selection.get("selection"), "selection.selection")
    _exact_keys(
        body,
        {"algorithm", "seed", "without_replacement", "selected_counts", "selected"},
        "selection.selection",
    )
    expected_counts = {
        "predicted_positive": POSITIVE_COUNT,
        "predicted_negative": NEGATIVE_COUNT,
        "extract_failed": 0,
        "total": EXPECTED_COUNT,
    }
    if (
        body.get("algorithm") != "sha256_rank_without_replacement_per_stratum_v1"
        or body.get("seed") != SELECTION_SEED
        or body.get("without_replacement") is not True
        or body.get("selected_counts") != expected_counts
        or body.get("selected") != expected_selected
    ):
        raise HeldoutEvaluationError("deterministic without-replacement selection drifted")
    selected_ids = {cast(int, row["news_item_id"]) for row in expected_selected}
    audit = _mapping(selection.get("audit"), "selection.audit")
    expected_audit = {
        "eligible_candidate_count": len(candidates),
        "successful_prediction_count": len(candidates),
        "extract_failed_count": 0,
        "available_by_stratum": dict(stratum_counts),
        "retired_selected_intersection_count": len(selected_ids.intersection(retired_ids)),
        "input_prediction_identity_match": True,
    }
    if dict(audit) != expected_audit or expected_audit["retired_selected_intersection_count"] != 0:
        raise HeldoutEvaluationError("selection audit drifted")
    source_lineage = _mapping(selection.get("source_lineage"), "selection.source_lineage")
    expected_lineage = {
        "binding_scope": "registered_full_execution",
        "preregistration": {"path": str(PREREGISTRATION_PATH), "sha256": PREREGISTRATION_SHA256},
        "design": DESIGN_REF,
        "materialized_inputs": {
            "path": _registered_path(root, paths.materialized_inputs),
            "sha256": _sha256_bytes(payloads["materialized_inputs"]),
            "row_count": len(candidates),
        },
        "predictions": {
            "path": _registered_path(root, paths.predictions),
            "sha256": _sha256_bytes(payloads["predictions"]),
            "row_count": len(candidates),
        },
        "heldout_execution_contract": {
            "path": str(HELDOUT_CONTRACT_PATH),
            "sha256": HELDOUT_CONTRACT_SHA256,
            "model": MODEL,
        },
        "selected_predictions": {
            "binding": "sha256_of_canonical_complete_prediction_row",
            "count": EXPECTED_COUNT,
            "bindings_sha256": _sha256_bytes(
                pipeline_common.canonical_json_bytes(selected_bindings)
            ),
            "bindings": selected_bindings,
        },
        "materialization_manifest": {
            "path": _registered_path(root, paths.materialization_manifest),
            "sha256": _sha256_bytes(payloads["materialization_manifest"]),
        },
        "inference_state": {
            "path": _registered_path(root, paths.inference_state),
            "sha256": _sha256_bytes(payloads["inference_state"]),
            "event_count": 2,
        },
        "prediction_manifest": {
            "path": _registered_path(root, paths.prediction_manifest),
            "sha256": _sha256_bytes(payloads["prediction_manifest"]),
        },
        "execution": {
            "execution_id": execution_id,
            "eligible_candidate_count": len(candidates),
            "prediction_count": len(candidates),
            "status_ok_count": len(candidates),
            "status_failed_count": 0,
            "automatic_retries": 0,
            "failed_candidate_retries": 0,
            "terminal_status": "completed_all_eligible_candidates_once",
        },
    }
    if dict(source_lineage) != expected_lineage:
        raise HeldoutEvaluationError("selection full source lineage drifted")
    delivery = _mapping(selection.get("owner_delivery"), "selection.owner_delivery")
    if dict(delivery) != {
        "path": _registered_path(root, paths.blind),
        "sha256": _sha256_bytes(payloads["blind"]),
        "row_count": EXPECTED_COUNT,
        "prediction_visible": False,
        "sampling_stratum_visible": False,
        "selection_rank_visible": False,
        "gold_state": "empty_object_pending_ai_draft_and_human_adjudication",
    }:
        raise HeldoutEvaluationError("held-out owner-delivery binding/blindness drifted")
    return tuple(expected_selected)


def _validate_owner_chain(
    selected: Sequence[JsonObject],
    candidates_by_id: Mapping[int, JsonObject],
    blind: Sequence[JsonObject],
    draft: Sequence[JsonObject],
    owner: Sequence[JsonObject],
    human: Sequence[JsonObject],
    *,
    inference_completed_at: datetime,
) -> JsonObject:
    if not all(len(rows) == EXPECTED_COUNT for rows in (blind, draft, owner, human)):
        raise HeldoutEvaluationError("held-out owner chain must contain exactly 60 rows")
    draft_fields = {
        "schema_version",
        "design",
        "frame_id",
        "sample_index",
        "news_item_id",
        "input_sha256",
        "drafter_id",
        "drafted_at",
        "draft_label",
    }
    owner_fields = {
        "schema_version",
        "design",
        "frame_id",
        "sample_index",
        "news_item_id",
        "input_sha256",
        "sealed_draft_item_sha256",
        "draft_label",
        "human_label",
        "annotation_status",
        "adjudication",
    }
    adjudication_fields = {
        "method",
        "drafter_id",
        "adjudicator_id",
        "confirmed",
        "changed",
        "changed_fields",
        "adjudicated_at",
    }
    human_fields = (set(pipeline_common.BLIND_FIELDS) - {"schema_version", "gold"}) | {
        "schema_version",
        "annotation_status",
        "annotation_type",
        "drafted_at",
        "adjudicated_at",
        "draft_label",
        "gold",
        "provenance",
    }
    provenance_fields = {
        "method",
        "design",
        "frame_id",
        "blind_input_sha256",
        "sealed_draft_item_sha256",
        "owner_export_item_sha256",
        "drafter_id",
        "adjudicator_id",
        "human_confirmation",
        "changed",
        "changed_fields",
    }
    drafted_at_value: str | None = None
    drafted_datetime: datetime | None = None
    adjudicated_values: list[str] = []
    changed_item_count = 0
    changed_field_counts = {field: 0 for field in _LABEL_FIELDS}
    for index, (selected_row, blind_row, draft_row, owner_row, human_row) in enumerate(
        zip(selected, blind, draft, owner, human, strict=True), 1
    ):
        identity = _identity(selected_row)
        if any(_identity(row) != identity for row in (blind_row, draft_row, owner_row, human_row)):
            raise HeldoutEvaluationError("held-out owner-chain identity/order drifted")
        identifier = identity[1]
        candidate = candidates_by_id.get(identifier)
        if candidate is None:
            raise HeldoutEvaluationError("held-out owner chain is not in the eligible pool")
        _exact_keys(blind_row, set(pipeline_common.BLIND_FIELDS), f"blind row {index}")
        try:
            pipeline_common.validate_blind_row(blind_row)
        except pipeline_common.DevelopmentFrameError as exc:
            raise HeldoutEvaluationError(f"held-out blind row {index} is invalid") from exc
        if (
            blind_row.get("schema_version") != "p4.2a-v2-heldout-owner-blind-item-v1"
            or blind_row.get("design") != DESIGN_REF
            or blind_row.get("frame_id") != FRAME_ID
            or blind_row.get("sample_index") != index
            or blind_row.get("gold") != {}
            or _recursive_forbidden(blind_row) is not None
        ):
            raise HeldoutEvaluationError("held-out blind row leaks frozen metadata")
        for field in (
            "news_item_id",
            "source",
            "url",
            "title",
            "ingested_symbol",
            "published_at",
            "available_time",
            "original_text",
            "input_sha256",
            "text_sha256",
            "body_state",
            "body_evidence",
        ):
            if blind_row.get(field) != candidate.get(field):
                raise HeldoutEvaluationError(
                    f"held-out blind row candidate binding drifted: {field}"
                )
        original_text = blind_row.get("original_text")
        if (
            not isinstance(original_text, str)
            or not original_text
            or blind_row.get("text_sha256") != _sha256_bytes(original_text.encode("utf-8"))
        ):
            raise HeldoutEvaluationError("held-out blind original_text is invalid")
        _exact_keys(draft_row, draft_fields, f"draft row {index}")
        if (
            draft_row.get("schema_version") != "p4.2a-v2-ai-draft-item-v1"
            or draft_row.get("design") != DESIGN_REF
            or draft_row.get("frame_id") != FRAME_ID
            or draft_row.get("sample_index") != index
        ):
            raise HeldoutEvaluationError("held-out AI draft header drifted")
        draft_actor = draft_row.get("drafter_id")
        if draft_actor != EXPECTED_DRAFTER_ID:
            raise HeldoutEvaluationError("held-out draft must use the registered Codex drafter")
        draft_timestamp = draft_row.get("drafted_at")
        parsed_draft = _aware_datetime(draft_timestamp, f"draft row {index}.drafted_at")
        if parsed_draft < inference_completed_at:
            raise HeldoutEvaluationError("held-out draft precedes inference completion")
        if drafted_at_value is None:
            drafted_at_value = cast(str, draft_timestamp)
            drafted_datetime = parsed_draft
        elif draft_timestamp != drafted_at_value:
            raise HeldoutEvaluationError("held-out draft must use one drafting timestamp")
        draft_label = _label(
            draft_row.get("draft_label"), original_text=original_text, label=f"draft row {index}"
        )
        if draft_label.get("notes") is not None:
            raise HeldoutEvaluationError("held-out AI draft notes must be null")
        draft_sha = _sha256_bytes(_canonical_json_bytes(draft_row))
        _exact_keys(owner_row, owner_fields, f"owner row {index}")
        if (
            owner_row.get("schema_version") != "p4.2a-v2-owner-adjudication-export-item-v1"
            or owner_row.get("design") != DESIGN_REF
            or owner_row.get("frame_id") != FRAME_ID
            or owner_row.get("sample_index") != index
            or owner_row.get("annotation_status") != "adjudicated"
            or owner_row.get("sealed_draft_item_sha256") != draft_sha
            or owner_row.get("draft_label") != draft_label
        ):
            raise HeldoutEvaluationError("held-out owner export sealed-draft binding drifted")
        human_label = _label(
            owner_row.get("human_label"),
            original_text=original_text,
            label=f"owner row {index}",
        )
        adjudication = _mapping(owner_row.get("adjudication"), f"owner row {index}.adjudication")
        _exact_keys(adjudication, adjudication_fields, f"owner row {index}.adjudication")
        changed_fields = [
            field for field in _LABEL_FIELDS if draft_label.get(field) != human_label.get(field)
        ]
        owner_actor = adjudication.get("adjudicator_id")
        if (
            adjudication.get("method") != "ai_drafted_human_adjudicated"
            or adjudication.get("drafter_id") != EXPECTED_DRAFTER_ID
            or owner_actor != EXPECTED_ADJUDICATOR_ID
            or adjudication.get("confirmed") is not True
            or adjudication.get("changed") is not bool(changed_fields)
            or adjudication.get("changed_fields") != changed_fields
        ):
            raise HeldoutEvaluationError("held-out owner adjudication provenance drifted")
        adjudicated_at = adjudication.get("adjudicated_at")
        parsed_adjudication = _aware_datetime(adjudicated_at, f"owner row {index}.adjudicated_at")
        if drafted_datetime is None or parsed_adjudication < drafted_datetime:
            raise HeldoutEvaluationError("held-out adjudication precedes the sealed draft")
        adjudicated_values.append(cast(str, adjudicated_at))
        if changed_fields:
            changed_item_count += 1
            for field in changed_fields:
                changed_field_counts[field] += 1
        _exact_keys(human_row, human_fields, f"human row {index}")
        immutable = {
            field: blind_row[field]
            for field in pipeline_common.BLIND_FIELDS
            if field not in {"schema_version", "gold"}
        }
        if (
            human_row.get("schema_version") != "p4.2a-v2-human-adjudicated-item-v1"
            or human_row.get("annotation_status") != "completed"
            or human_row.get("annotation_type") != "ai_drafted_human_adjudicated"
            or any(human_row.get(field) != value for field, value in immutable.items())
            or human_row.get("drafted_at") != drafted_at_value
            or human_row.get("adjudicated_at") != adjudicated_at
            or human_row.get("draft_label") != draft_label
            or human_row.get("gold") != human_label
        ):
            raise HeldoutEvaluationError("held-out canonical human gold drifted")
        provenance = _mapping(human_row.get("provenance"), f"human row {index}.provenance")
        _exact_keys(provenance, provenance_fields, f"human row {index}.provenance")
        owner_sha = _sha256_bytes(_canonical_json_bytes(owner_row))
        if (
            provenance.get("method") != "ai_drafted_human_adjudicated"
            or provenance.get("design") != DESIGN_REF
            or provenance.get("frame_id") != FRAME_ID
            or provenance.get("blind_input_sha256") != blind_row.get("input_sha256")
            or provenance.get("sealed_draft_item_sha256") != draft_sha
            or provenance.get("owner_export_item_sha256") != owner_sha
            or provenance.get("drafter_id") != EXPECTED_DRAFTER_ID
            or provenance.get("adjudicator_id") != EXPECTED_ADJUDICATOR_ID
            or provenance.get("human_confirmation") is not True
            or provenance.get("changed") is not bool(changed_fields)
            or provenance.get("changed_fields") != changed_fields
        ):
            raise HeldoutEvaluationError("held-out canonical provenance drifted")
    if drafted_at_value is None or not adjudicated_values:
        raise HeldoutEvaluationError("held-out owner chain is empty")
    ordered_adjudications = sorted(
        adjudicated_values,
        key=lambda value: _aware_datetime(value, "adjudicated_at"),
    )
    return {
        "drafter_id": EXPECTED_DRAFTER_ID,
        "adjudicator_id": EXPECTED_ADJUDICATOR_ID,
        "row_count": EXPECTED_COUNT,
        "all_items_human_confirmed": True,
        "changed_item_count": changed_item_count,
        "unchanged_item_count": EXPECTED_COUNT - changed_item_count,
        "changed_field_counts": changed_field_counts,
        "drafted_at": drafted_at_value,
        "earliest_adjudicated_at": ordered_adjudications[0],
        "latest_adjudicated_at": ordered_adjudications[-1],
    }


def _validate_completion(
    completion: JsonObject,
    paths: ArtifactPaths,
    payloads: Mapping[str, bytes],
    *,
    root: Path,
    chain_summary: Mapping[str, Any],
    eligible_candidate_count: int,
) -> None:
    _exact_keys(
        completion,
        {
            "schema_version",
            "design",
            "frame_id",
            "completed_at",
            "artifacts",
            "provenance",
            "validation",
            "model_execution",
            "heldout_touched",
            "safety",
        },
        "completion manifest",
    )
    if (
        completion.get("schema_version") != "p4.2a-v2-owner-completion-manifest-v1"
        or completion.get("design") != DESIGN_REF
        or completion.get("frame_id") != FRAME_ID
        or completion.get("heldout_touched") is not True
    ):
        raise HeldoutEvaluationError("held-out completion manifest header drifted")
    completed_at = _aware_datetime(completion.get("completed_at"), "completion.completed_at")
    if completed_at < _aware_datetime(
        chain_summary.get("latest_adjudicated_at"), "completion latest adjudication"
    ):
        raise HeldoutEvaluationError("completion precedes the latest adjudication")
    artifacts = _mapping(completion.get("artifacts"), "completion.artifacts")
    _exact_keys(
        artifacts,
        {
            "private_selection",
            "owner_blind",
            "ai_draft",
            "adjudication_ui",
            "owner_raw_export",
            "human_adjudicated",
        },
        "completion.artifacts",
    )
    expected = {
        "private_selection": (paths.selection, "selection", None),
        "owner_blind": (paths.blind, "blind", EXPECTED_COUNT),
        "ai_draft": (paths.draft, "draft", EXPECTED_COUNT),
        "adjudication_ui": (paths.adjudication_ui, "adjudication_ui", None),
        "owner_raw_export": (paths.owner_export, "owner_export", EXPECTED_COUNT),
        "human_adjudicated": (
            paths.human_adjudicated,
            "human_adjudicated",
            EXPECTED_COUNT,
        ),
    }
    for name, (path, payload_name, row_count) in expected.items():
        entry = _mapping(artifacts.get(name), f"completion.artifacts.{name}")
        expected_fields = {"path", "sha256"}
        if row_count is not None:
            expected_fields.add("row_count")
        _exact_keys(entry, expected_fields, f"completion.artifacts.{name}")
        if (
            entry.get("path") != _registered_path(root, path)
            or entry.get("sha256") != _sha256_bytes(payloads[payload_name])
            or (row_count is not None and entry.get("row_count") != row_count)
        ):
            raise HeldoutEvaluationError(f"completion artifact binding drifted: {name}")
    provenance = _mapping(completion.get("provenance"), "completion.provenance")
    validation = _mapping(completion.get("validation"), "completion.validation")
    model_execution = _mapping(completion.get("model_execution"), "completion.model_execution")
    safety = _mapping(completion.get("safety"), "completion.safety")
    if dict(provenance) != dict(chain_summary):
        raise HeldoutEvaluationError("held-out completion provenance drifted")
    if dict(validation) != _COMPLETION_VALIDATION:
        raise HeldoutEvaluationError("held-out completion validation drifted")
    if dict(model_execution) != {
        "drafting_ai_inference_occurred": True,
        "drafting_ai": EXPECTED_DRAFTER_ID,
        "drafting_ai_is_evaluated_model": False,
        "selected_model": MODEL,
        "selected_model_candidate_inference_count": eligible_candidate_count,
        "selected_model_candidate_failure_count": 0,
        "final_one_shot_evaluation_calls": 0,
        "workflow_script_model_calls": 0,
    }:
        raise HeldoutEvaluationError("held-out completion model execution drifted")
    if dict(safety) != {
        "production_database_writes": 0,
        "proposals_or_orders_created": False,
        "one_shot_evaluation_consumed": False,
        "p4_2a_done": False,
        "p4_2b_unlocked": False,
        "p4_3_unlocked": False,
    }:
        raise HeldoutEvaluationError("held-out completion safety drifted")


def load_preflight(
    *,
    root: Path = PROJECT_ROOT,
    paths: ArtifactPaths | None = None,
    require_unclaimed_state: bool = True,
    execution_context: heldout_prepare._OfflineRehearsalCapability | None = None,
) -> Preflight:
    try:
        authority_binding = heldout_prepare.load_binding(root)
        stage_authority = heldout_prepare.validate_v2_1_stage_authorization(
            authority_binding,
            stage="evaluation",
            execution_context=execution_context,
        )
    except heldout_prepare.HeldoutPreparationError as exc:
        raise HeldoutEvaluationError(
            f"held-out evaluation remains authority-gated: {exc}"
        ) from exc
    controls = load_control_bundle(root)
    resolved_paths = paths or registered_artifact_paths(root, controls.design)
    if isinstance(
        stage_authority,
        (
            heldout_prepare.V21ReleaseAuthorization,
            heldout_prepare._OfflineRehearsalCapability,
        ),
    ):
        expected_paths = {
            "materialized_inputs": authority_binding.artifacts["materialized_inputs"],
            "materialization_manifest": authority_binding.artifacts[
                "materialization_manifest"
            ],
            "inference_state": authority_binding.artifacts["inference_state"],
            "predictions": authority_binding.artifacts["predictions"],
            "prediction_manifest": authority_binding.artifacts["prediction_manifest"],
            "selection": authority_binding.artifacts["private_selection"],
            "blind": authority_binding.artifacts["owner_blind"],
            "draft": authority_binding.artifacts["ai_draft"],
            "adjudication_ui": authority_binding.artifacts["adjudication_ui"],
            "owner_export": authority_binding.artifacts["owner_export"],
            "human_adjudicated": authority_binding.artifacts["human_adjudicated"],
            "owner_completion": authority_binding.artifacts["owner_completion"],
            "evaluation_state": authority_binding.artifacts["evaluation_state"],
            "report": authority_binding.artifacts["report_directory"] / REPORT_FILENAME,
        }
        if any(
            cast(Path, getattr(resolved_paths, name)).resolve() != expected.resolve()
            for name, expected in expected_paths.items()
        ):
            raise HeldoutEvaluationError(
                "held-out evaluator inputs are not the registered successor artifacts"
            )
    declared_artifact_root = resolved_paths.artifact_root or root
    if declared_artifact_root.is_symlink() or not declared_artifact_root.is_dir():
        raise HeldoutEvaluationError("declared held-out artifact root is unavailable")
    artifact_root = declared_artifact_root.resolve()
    for artifact_path in (
        resolved_paths.materialized_inputs,
        resolved_paths.materialization_manifest,
        resolved_paths.inference_state,
        resolved_paths.predictions,
        resolved_paths.prediction_manifest,
        resolved_paths.selection,
        resolved_paths.blind,
        resolved_paths.draft,
        resolved_paths.adjudication_ui,
        resolved_paths.owner_export,
        resolved_paths.human_adjudicated,
        resolved_paths.owner_completion,
        resolved_paths.evaluation_state,
        resolved_paths.report,
    ):
        if not artifact_path.resolve().is_relative_to(artifact_root):
            raise HeldoutEvaluationError("held-out artifact escapes its declared artifact root")
    if require_unclaimed_state and (
        resolved_paths.evaluation_state.exists() or resolved_paths.evaluation_state.is_symlink()
    ):
        raise HeldoutEvaluationError("held-out evaluation state is already claimed")
    if resolved_paths.report.exists() or resolved_paths.report.is_symlink():
        raise HeldoutEvaluationError("held-out evaluation report already exists")
    materialized_inputs, materialized_inputs_payload = _load_jsonl(
        resolved_paths.materialized_inputs, "held-out materialized inputs"
    )
    materialization_manifest, materialization_manifest_payload = _load_json(
        resolved_paths.materialization_manifest, "held-out materialization manifest"
    )
    inference_state, inference_state_payload = _load_jsonl(
        resolved_paths.inference_state, "held-out inference state"
    )
    predictions, predictions_payload = _load_jsonl(
        resolved_paths.predictions, "held-out predictions"
    )
    prediction_manifest, prediction_manifest_payload = _load_json(
        resolved_paths.prediction_manifest, "held-out prediction manifest"
    )
    selection, selection_payload = _load_json(resolved_paths.selection, "held-out selection")
    blind, blind_payload = _load_jsonl(resolved_paths.blind, "held-out blind")
    draft, draft_payload = _load_jsonl(resolved_paths.draft, "held-out draft")
    adjudication_ui_payload = _regular_payload(
        resolved_paths.adjudication_ui, "held-out adjudication UI"
    )
    owner, owner_payload = _load_jsonl(resolved_paths.owner_export, "held-out owner export")
    human, human_payload = _load_jsonl(resolved_paths.human_adjudicated, "held-out human gold")
    completion, completion_payload = _load_json(
        resolved_paths.owner_completion, "held-out owner completion"
    )
    payloads = {
        "materialized_inputs": materialized_inputs_payload,
        "materialization_manifest": materialization_manifest_payload,
        "inference_state": inference_state_payload,
        "predictions": predictions_payload,
        "prediction_manifest": prediction_manifest_payload,
        "selection": selection_payload,
        "blind": blind_payload,
        "draft": draft_payload,
        "adjudication_ui": adjudication_ui_payload,
        "owner_export": owner_payload,
        "human_adjudicated": human_payload,
        "owner_completion": completion_payload,
    }
    active_contract = heldout_prepare._load_selected_contract(root.resolve())
    candidates, candidates_by_id = _validate_candidate_rows(
        materialized_inputs,
        retired_ids=controls.retired_ids,
        active_contract=active_contract,
    )
    if (
        resolved_paths.materialized_inputs.resolve()
        == authority_binding.artifacts["materialized_inputs"].resolve()
        and resolved_paths.materialization_manifest.resolve()
        == authority_binding.artifacts["materialization_manifest"].resolve()
    ):
        try:
            heldout_prepare.validate_v2_1_materialization_manifest(
                authority_binding,
                materialization_manifest,
                candidates,
                inputs_sha256=_sha256_bytes(materialized_inputs_payload),
                validated_stage="evaluation",
                execution_context=execution_context,
            )
        except heldout_prepare.HeldoutPreparationError as exc:
            raise HeldoutEvaluationError(
                f"materialization manifest v2 producer validation failed: {exc}"
            ) from exc
    _validate_materialization_manifest(
        materialization_manifest,
        candidates,
        controls=controls,
        root=artifact_root,
        paths=resolved_paths,
        inputs_payload=materialized_inputs_payload,
        control_root=root.resolve(),
    )
    predictions_by_id, strata, stratum_counts = _validate_prediction_rows(predictions, candidates)
    execution_id, inference_completed_at = _validate_inference_chain(
        inference_state,
        prediction_manifest,
        candidates=candidates,
        predictions=predictions,
        root=artifact_root,
        paths=resolved_paths,
        materialization_payload=materialization_manifest_payload,
        inference_payload=inference_state_payload,
        predictions_payload=predictions_payload,
        prediction_manifest_payload=prediction_manifest_payload,
        preregistered_at=_aware_datetime(
            controls.preregistration.get("created_at_utc"),
            "held-out preregistration created_at_utc",
        ),
    )
    selected = _validate_selection(
        selection,
        candidates=candidates,
        predictions_by_id=predictions_by_id,
        strata=strata,
        stratum_counts=stratum_counts,
        retired_ids=controls.retired_ids,
        execution_id=execution_id,
        root=artifact_root,
        paths=resolved_paths,
        payloads=payloads,
    )
    chain_summary = _validate_owner_chain(
        selected,
        candidates_by_id,
        blind,
        draft,
        owner,
        human,
        inference_completed_at=inference_completed_at,
    )
    expected_adjudication_ui = _render_expected_adjudication_ui(
        blind,
        draft,
        control_root=root.resolve(),
        paths=resolved_paths,
        selection_payload=selection_payload,
        blind_payload=blind_payload,
        draft_payload=draft_payload,
    )
    if adjudication_ui_payload != expected_adjudication_ui:
        raise HeldoutEvaluationError(
            "held-out adjudication_ui differs from deterministic frozen-input rendering"
        )
    _validate_completion(
        completion,
        resolved_paths,
        payloads,
        root=artifact_root,
        chain_summary=chain_summary,
        eligible_candidate_count=len(candidates),
    )
    return Preflight(
        controls=controls,
        paths=resolved_paths,
        hashes={name: _sha256_bytes(payload) for name, payload in payloads.items()},
        materialized_inputs=candidates,
        materialization_manifest=materialization_manifest,
        inference_state=tuple(inference_state),
        prediction_manifest=prediction_manifest,
        selection=selection,
        selected=selected,
        predictions_by_id=predictions_by_id,
        blind=tuple(blind),
        draft=tuple(draft),
        owner_export=tuple(owner),
        human=tuple(human),
        completion=completion,
    )


def score_heldout(
    selected: Sequence[Mapping[str, Any]],
    predictions_by_id: Mapping[int, Mapping[str, Any]],
    human: Sequence[Mapping[str, Any]],
) -> JsonObject:
    """Score only the registered 40 positive and 20 negative strata."""

    gold_by_id = {
        cast(int, row["news_item_id"]): _mapping(row.get("gold"), "human.gold") for row in human
    }
    tp = fp = fn = tn = symbol_matches = symbol_bearing_matches = 0
    symbol_bearing_count = 0
    for item in selected:
        identifier = cast(int, item["news_item_id"])
        prediction = _mapping(predictions_by_id[identifier].get("prediction"), "prediction")
        gold = gold_by_id[identifier]
        predicted_positive = prediction.get("materiality") in (2, 3)
        gold_positive = gold.get("materiality") in (2, 3)
        stratum = item.get("sampling_stratum")
        if stratum == "predicted_positive":
            if not predicted_positive:
                raise HeldoutEvaluationError("positive stratum prediction changed after selection")
            if gold_positive:
                tp += 1
            else:
                fp += 1
        elif stratum == "predicted_negative":
            if predicted_positive:
                raise HeldoutEvaluationError("negative stratum prediction changed after selection")
            if gold_positive:
                fn += 1
            else:
                tn += 1
        else:
            raise HeldoutEvaluationError("unknown held-out sampling stratum")
        prediction_symbols = prediction.get("symbols")
        gold_symbols = gold.get("symbols")
        if prediction_symbols == gold_symbols:
            symbol_matches += 1
        if prediction_symbols or gold_symbols:
            symbol_bearing_count += 1
            if prediction_symbols == gold_symbols:
                symbol_bearing_matches += 1
    if tp + fp != POSITIVE_COUNT or fn + tn != NEGATIVE_COUNT:
        raise HeldoutEvaluationError("held-out metric partitions drifted")
    precision = tp / POSITIVE_COUNT
    false_omission_rate = fn / NEGATIVE_COUNT
    symbol_accuracy = symbol_matches / EXPECTED_COUNT
    symbol_bearing_accuracy = (
        symbol_bearing_matches / symbol_bearing_count if symbol_bearing_count else None
    )
    symbol_bearing_passed = (
        symbol_bearing_accuracy is not None and symbol_bearing_accuracy >= SYMBOL_ACCURACY_MINIMUM
    )
    return {
        "sampling_frame": {
            "frame_id": FRAME_ID,
            "total_size": EXPECTED_COUNT,
            "registered_strata": {
                "selected_model_predicted_positive": POSITIVE_COUNT,
                "selected_model_predicted_negative": NEGATIVE_COUNT,
            },
        },
        "confusion_matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "materiality_precision": {
            "metric_partition": "selected_model_predicted_positive_stratum",
            "sampling_frame": FRAME_ID,
            "sampling_strata": ["selected_model_predicted_positive"],
            "denominator": POSITIVE_COUNT,
            "formula": "tp / (tp + fp)",
            "gate_or_diagnostic": "final_one_shot_gate",
            "tp": tp,
            "fp": fp,
            "value": precision,
            "threshold": PRECISION_MINIMUM,
            "passed": precision >= PRECISION_MINIMUM,
        },
        "materiality_false_omission_rate": {
            "metric_partition": "selected_model_predicted_negative_stratum",
            "sampling_frame": FRAME_ID,
            "sampling_strata": ["selected_model_predicted_negative"],
            "denominator": NEGATIVE_COUNT,
            "formula": "fn / (fn + tn)",
            "gate_or_diagnostic": "final_one_shot_gate",
            "fn": fn,
            "tn": tn,
            "value": false_omission_rate,
            "threshold": FALSE_OMISSION_RATE_MAXIMUM,
            "passed": false_omission_rate <= FALSE_OMISSION_RATE_MAXIMUM,
        },
        "materiality_recall": {
            "metric_partition": "omitted_in_favor_of_false_omission_rate",
            "sampling_frame": FRAME_ID,
            "sampling_strata": [
                "selected_model_predicted_positive",
                "selected_model_predicted_negative",
            ],
            "denominator": "not_estimated",
            "formula": "not_estimable",
            "gate_or_diagnostic": "omitted_not_estimable",
            "value": None,
            "passed": None,
        },
        "symbol_exact_set_accuracy": {
            "metric_partition": "all_successful_heldout60_predictions",
            "sampling_frame": FRAME_ID,
            "sampling_strata": [
                "selected_model_predicted_positive",
                "selected_model_predicted_negative",
            ],
            "denominator": EXPECTED_COUNT,
            "formula": "exact_symbol_set_matches / comparable_predictions",
            "gate_or_diagnostic": "final_one_shot_gate",
            "matches": symbol_matches,
            "value": symbol_accuracy,
            "threshold": SYMBOL_ACCURACY_MINIMUM,
            "passed": symbol_accuracy >= SYMBOL_ACCURACY_MINIMUM,
        },
        "symbol_bearing_exact_set_accuracy": {
            "metric_partition": "gold_or_prediction_symbol_bearing_items",
            "sampling_frame": FRAME_ID,
            "sampling_strata": [
                "selected_model_predicted_positive",
                "selected_model_predicted_negative",
            ],
            "denominator": symbol_bearing_count,
            "formula": ("exact_symbol_set_matches_on_symbol_bearing_items / symbol_bearing_items"),
            "gate_or_diagnostic": "final_one_shot_gate",
            "matches": symbol_bearing_matches,
            "value": symbol_bearing_accuracy,
            "threshold": SYMBOL_ACCURACY_MINIMUM,
            "passed": symbol_bearing_passed,
            "zero_denominator_policy": "fail",
        },
        "all_gates_passed": (
            precision >= PRECISION_MINIMUM
            and false_omission_rate <= FALSE_OMISSION_RATE_MAXIMUM
            and symbol_accuracy >= SYMBOL_ACCURACY_MINIMUM
            and symbol_bearing_passed
        ),
    }


def _synthetic_score_inputs(
    preflight: Preflight,
) -> tuple[tuple[JsonObject, ...], dict[int, JsonObject]]:
    synthetic_human: list[JsonObject] = []
    synthetic_predictions: dict[int, JsonObject] = {}
    for item, real_human in zip(preflight.selected, preflight.human, strict=True):
        identifier = cast(int, item["news_item_id"])
        positive = item["sampling_stratum"] == "predicted_positive"
        label = {
            "symbols": [],
            "event_type": "other",
            "direction": 0,
            "materiality": 2 if positive else 1,
            "evidence_span": "synthetic-full-path-rehearsal",
            "notes": None,
        }
        human = dict(real_human)
        human["gold"] = dict(label)
        human["synthetic_metric_fixture"] = True
        prediction = dict(preflight.predictions_by_id[identifier])
        prediction["prediction"] = {
            **label,
            "summary": "synthetic-full-path-rehearsal",
            "confidence": 1.0,
        }
        synthetic_human.append(human)
        synthetic_predictions[identifier] = prediction
    return tuple(synthetic_human), synthetic_predictions


def _report_payload(
    preflight: Preflight,
    metrics: Mapping[str, Any],
    *,
    completed_at: str,
    authorization: Mapping[str, Any] | None,
    synthetic: bool,
) -> JsonObject:
    return {
        "schema_version": "p4.2a-v2-heldout-evaluation-report-v1",
        "status": "synthetic_rehearsal" if synthetic else "completed",
        "completed_at_utc": completed_at,
        "design": DESIGN_REF,
        "preregistration": {
            "path": str(PREREGISTRATION_PATH),
            "sha256": PREREGISTRATION_SHA256,
        },
        "selected_model": MODEL,
        "inference_contract": {
            "heldout_contract_sha256": HELDOUT_CONTRACT_SHA256,
            "round_3_contract_sha256": ROUND3_CONTRACT_SHA256,
            "prompt_sha256": PROMPT_SHA256,
            "one_news_item_per_request": True,
            "one_request_per_item": True,
            "multi_item_prompt": False,
            "additional_body_shortening": False,
            "prefilter": False,
            "retries": 0,
        },
        "inputs": {
            "hashes": dict(preflight.hashes),
            "row_counts": {
                "selected": len(preflight.selected),
                "blind": len(preflight.blind),
                "draft": len(preflight.draft),
                "owner_export": len(preflight.owner_export),
                "human_adjudicated": len(preflight.human),
            },
            "human_provenance": {
                "annotation_type": "ai_drafted_human_adjudicated",
                "draft_annotator": _mapping(
                    preflight.completion.get("provenance"), "completion.provenance"
                ).get("drafter_id"),
                "human_adjudicator": _mapping(
                    preflight.completion.get("provenance"), "completion.provenance"
                ).get("adjudicator_id"),
            },
        },
        "authorization": dict(authorization) if authorization is not None else None,
        "metrics": dict(metrics),
        "real_heldout_metrics_computed": not synthetic,
        "phase_gates": {
            "p4_2a_evaluation_passed": (
                bool(metrics.get("all_gates_passed")) if not synthetic else None
            ),
            "p4_2a_done": False,
            "p4_2b_unlocked": False,
            "p4_3_unlocked": False,
        },
        "safety": {
            "production_database_writes": 0,
            "model_calls": 0,
            "proposals_or_orders_created": False,
            "one_shot_consumed": not synthetic,
        },
    }


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


def _utc_now(clock: Callable[[], datetime]) -> str:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise HeldoutEvaluationError("clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def dry_run(
    *,
    root: Path = PROJECT_ROOT,
    paths: ArtifactPaths | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    execution_context: heldout_prepare._OfflineRehearsalCapability | None = None,
) -> JsonObject:
    """Validate the real chain, then exercise metrics/report only on synthetic data."""

    preflight = load_preflight(
        root=root,
        paths=paths,
        execution_context=execution_context,
    )
    before = {
        path: _sha256_file(path)
        for path in (
            preflight.paths.materialized_inputs,
            preflight.paths.materialization_manifest,
            preflight.paths.inference_state,
            preflight.paths.predictions,
            preflight.paths.prediction_manifest,
            preflight.paths.selection,
            preflight.paths.blind,
            preflight.paths.draft,
            preflight.paths.adjudication_ui,
            preflight.paths.owner_export,
            preflight.paths.human_adjudicated,
            preflight.paths.owner_completion,
        )
    }
    synthetic_human, synthetic_predictions = _synthetic_score_inputs(preflight)
    metrics = score_heldout(preflight.selected, synthetic_predictions, synthetic_human)
    payload = _canonical_json_bytes(
        _report_payload(
            preflight,
            metrics,
            completed_at=_utc_now(clock),
            authorization=None,
            synthetic=True,
        )
    )
    json.loads(payload, object_pairs_hook=_strict_object, parse_constant=_reject_nonfinite)
    after = {path: _sha256_file(path) for path in before}
    if before != after:
        raise HeldoutEvaluationError("dry-run input bytes changed")
    if preflight.paths.evaluation_state.exists() or preflight.paths.report.exists():
        raise HeldoutEvaluationError("dry-run mutated the formal state/report")
    return {
        "schema_version": "p4.2a-v2-heldout-evaluation-dry-run-v1",
        "status": "passed",
        "validated_input_hashes": dict(preflight.hashes),
        "synthetic_report_sha256": _sha256_bytes(payload),
        "validated_through": "report_serialization_in_memory",
        "real_heldout_metrics_computed": False,
        "one_shot_consumed": False,
        "state_created": False,
        "report_created": False,
        "filesystem_mutations": 0,
        "stages": list(_DRY_RUN_STAGES),
    }


def _create_only(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise HeldoutEvaluationError("create-only output traverses a symlink")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        _fsync_directory(path.parent)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise HeldoutEvaluationError("create-only output is not regular")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise HeldoutEvaluationError("create-only write failed")
            view = view[written:]
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


def _append_terminal(path: Path, event: Mapping[str, Any]) -> None:
    if path.is_symlink() or not path.is_file():
        raise HeldoutEvaluationError("one-shot state disappeared after claim")
    flags = os.O_WRONLY | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise HeldoutEvaluationError("one-shot state is not regular")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        rows, _payload = _load_jsonl(path, "one-shot state")
        if len(rows) != 1 or rows[0].get("event") != "evaluation_started":
            raise HeldoutEvaluationError("one-shot state cannot accept a terminal event")
        view = memoryview(_canonical_json_bytes(event))
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise HeldoutEvaluationError("terminal state write failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _authorization(
    path: Path,
    expected_sha256: str,
    *,
    preflight: Preflight,
) -> JsonObject:
    if not _is_sha(expected_sha256):
        raise HeldoutEvaluationError("independent-review SHA-256 is invalid")
    review, payload = _load_json(path, "independent held-out authorization")
    if _sha256_bytes(payload) != expected_sha256:
        raise HeldoutEvaluationError("independent held-out authorization SHA-256 drifted")
    _exact_keys(
        review,
        {
            "schema_version",
            "decision",
            "preregistration_sha256",
            "design_sha256",
            "input_hashes",
            "dry_run_receipt",
            "reviewer",
            "authorization",
        },
        "independent authorization",
    )
    authorization = _mapping(review.get("authorization"), "review.authorization")
    reviewer = _mapping(review.get("reviewer"), "review.reviewer")
    receipt_ref = _mapping(review.get("dry_run_receipt"), "review.dry_run_receipt")
    _exact_keys(
        authorization,
        {
            "selected_model",
            "one_shot_count",
            "zero_retries",
            "formal_evaluation_allowed",
        },
        "review.authorization",
    )
    _exact_keys(
        reviewer,
        {
            "reviewer_id",
            "reviewer_type",
            "reviewer_role",
            "reviewer_model",
            "independent",
        },
        "review.reviewer",
    )
    _exact_keys(receipt_ref, {"path", "sha256"}, "review.dry_run_receipt")
    reviewer_id = reviewer.get("reviewer_id")
    one_shot_count = _nonnegative_int(
        authorization.get("one_shot_count"), "review.authorization.one_shot_count"
    )
    if (
        review.get("schema_version") != "p4.2a-v2-heldout-evaluation-independent-review-v1"
        or review.get("decision") != "APPROVE_ONE_SHOT_EVALUATION"
        or review.get("preregistration_sha256") != PREREGISTRATION_SHA256
        or review.get("design_sha256") != DESIGN_SHA256
        or review.get("input_hashes") != dict(preflight.hashes)
        or authorization.get("selected_model") != MODEL
        or one_shot_count != 1
        or authorization.get("zero_retries") is not True
        or authorization.get("formal_evaluation_allowed") is not True
        or reviewer_id != EXPECTED_REVIEWER_ID
        or reviewer.get("reviewer_type") != EXPECTED_REVIEWER_TYPE
        or reviewer.get("reviewer_role") != EXPECTED_REVIEWER_ROLE
        or reviewer.get("reviewer_model") != EXPECTED_REVIEWER_MODEL
        or reviewer.get("independent") is not True
    ):
        raise HeldoutEvaluationError("independent held-out authorization is not sufficient")
    receipt_path_value = receipt_ref.get("path")
    receipt_sha = receipt_ref.get("sha256")
    if (
        not isinstance(receipt_path_value, str)
        or not receipt_path_value
        or not _is_sha(receipt_sha)
    ):
        raise HeldoutEvaluationError("independent dry-run receipt reference is invalid")
    receipt_path = Path(receipt_path_value)
    if not receipt_path.is_absolute():
        receipt_path = path.parent / receipt_path
    receipt, receipt_payload = _load_json(receipt_path, "independent dry-run receipt")
    if _sha256_bytes(receipt_payload) != receipt_sha:
        raise HeldoutEvaluationError("independent dry-run receipt SHA-256 drifted")
    _exact_keys(
        receipt,
        {
            "schema_version",
            "status",
            "validated_input_hashes",
            "synthetic_report_sha256",
            "validated_through",
            "real_heldout_metrics_computed",
            "one_shot_consumed",
            "state_created",
            "report_created",
            "filesystem_mutations",
            "stages",
        },
        "independent dry-run receipt",
    )
    filesystem_mutations = _nonnegative_int(
        receipt.get("filesystem_mutations"), "dry-run receipt.filesystem_mutations"
    )
    if (
        receipt.get("schema_version") != "p4.2a-v2-heldout-evaluation-dry-run-v1"
        or receipt.get("status") != "passed"
        or receipt.get("validated_input_hashes") != dict(preflight.hashes)
        or not _is_sha(receipt.get("synthetic_report_sha256"))
        or receipt.get("validated_through") != "report_serialization_in_memory"
        or receipt.get("real_heldout_metrics_computed") is not False
        or receipt.get("one_shot_consumed") is not False
        or receipt.get("state_created") is not False
        or receipt.get("report_created") is not False
        or filesystem_mutations != 0
        or receipt.get("stages") != _DRY_RUN_STAGES
    ):
        raise HeldoutEvaluationError("independent dry-run receipt is not sufficient")
    return {
        "schema_version": cast(str, review["schema_version"]),
        "decision": cast(str, review["decision"]),
        "authorization_sha256": expected_sha256,
        "reviewer_id": reviewer_id,
        "reviewer_type": EXPECTED_REVIEWER_TYPE,
        "reviewer_role": EXPECTED_REVIEWER_ROLE,
        "reviewer_model": EXPECTED_REVIEWER_MODEL,
        "independent": True,
        "selected_model": MODEL,
        "one_shot_count": 1,
        "zero_retries": True,
        "formal_evaluation_allowed": True,
        "input_hashes": dict(preflight.hashes),
        "dry_run_receipt_sha256": cast(str, receipt_sha),
    }


def formal_evaluate(
    *,
    authorization_path: Path,
    authorization_sha256: str,
    root: Path = PROJECT_ROOT,
    paths: ArtifactPaths | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> JsonObject:
    """Consume the single evaluation only after every read-only preflight passes."""

    preflight = load_preflight(root=root, paths=paths)
    review = _authorization(authorization_path, authorization_sha256, preflight=preflight)
    started_at = _utc_now(clock)
    if _aware_datetime(started_at, "formal evaluation start") < _aware_datetime(
        preflight.completion.get("completed_at"),
        "owner completion completed_at",
    ):
        raise HeldoutEvaluationError(
            "formal evaluation start precedes owner-chain completion"
        )
    started = {
        "schema_version": "p4.2a-v2-heldout-evaluation-state-event-v1",
        "event": "evaluation_started",
        "at_utc": started_at,
        "design_sha256": DESIGN_SHA256,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "authorization_sha256": authorization_sha256,
        "selected_model": MODEL,
        "input_hashes": dict(preflight.hashes),
        "attempt_number": 1,
        "maximum_attempts": 1,
        "retries": 0,
    }
    _create_only(preflight.paths.evaluation_state, _canonical_json_bytes(started))
    try:
        metrics = score_heldout(preflight.selected, preflight.predictions_by_id, preflight.human)
        completed_at = _utc_now(clock)
        report = _report_payload(
            preflight,
            metrics,
            completed_at=completed_at,
            authorization=review,
            synthetic=False,
        )
        report_payload = _canonical_json_bytes(report)
        _create_only(preflight.paths.report, report_payload)
        terminal = {
            "schema_version": "p4.2a-v2-heldout-evaluation-state-event-v1",
            "event": "evaluation_completed",
            "at_utc": completed_at,
            "passed": metrics["all_gates_passed"],
            "report_path": str(preflight.paths.report),
            "report_sha256": _sha256_bytes(report_payload),
            "retries": 0,
        }
        _append_terminal(preflight.paths.evaluation_state, terminal)
        return report
    except BaseException as exc:
        terminal = {
            "schema_version": "p4.2a-v2-heldout-evaluation-state-event-v1",
            "event": "evaluation_failed",
            "at_utc": _utc_now(clock),
            "error_code": type(exc).__name__,
            "raw_payload_persisted": False,
            "retry_allowed": False,
            "retries": 0,
        }
        _append_terminal(preflight.paths.evaluation_state, terminal)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--formal", action="store_true")
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--authorization-sha256")
    arguments = parser.parse_args(argv)
    try:
        if arguments.dry_run:
            result = dry_run()
        else:
            if arguments.authorization is None or arguments.authorization_sha256 is None:
                raise HeldoutEvaluationError(
                    "formal evaluation requires an independently reviewed authorization"
                )
            result = formal_evaluate(
                authorization_path=arguments.authorization,
                authorization_sha256=arguments.authorization_sha256,
            )
    except (HeldoutEvaluationError, FileExistsError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
