from __future__ import annotations

import argparse
import copy
import fcntl
import json
import math
import os
import re
import shutil
import sys
import tempfile
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn, cast
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if __package__ in {None, ""}:
    sys.path[:0] = [str(PROJECT_ROOT), str(PROJECT_ROOT / "src")]

import yaml  # noqa: E402
from scripts import build_p4_2a_gold_sample as gold_builder  # noqa: E402
from scripts import p4_2a_v2_dev_common as common  # noqa: E402
from scripts import run_p4_2a_offline_extract as offline_extract  # noqa: E402
from scripts import run_p4_2a_v2_dev_calibration as dev_runner  # noqa: E402
from scripts.run_p4_2a_heldout_predictions import HeldoutPredictionError  # noqa: E402
from scripts.run_p4_2a_offline_extract import (  # noqa: E402
    DECLARED_INPUT_LEGACY_V1,
    ChatJsonCallable,
    ExtractRecord,
    _settings_from_project_env,
    extract_records,
)

from alphapilot.core.config import Settings  # noqa: E402
from alphapilot.llm.p4_news_eval import load_event_evaluation_design  # noqa: E402
from alphapilot.llm.p4_news_event import (  # noqa: E402
    EventExtractContract,
    EventExtractValidationError,
)

JsonObject = dict[str, Any]

PREREGISTRATION_PATH = Path("docs/phase4/reports/P4.2a-v2-heldout-preregistration-20260810.json")
PREREGISTRATION_SHA256 = "ccecbf5ca7b48b16e445318b8c94a08927432f92c7e8c12f8ab40f2916578705"
DESIGN_PATH = Path("config/p4_event_evaluation_v2.yaml")
DESIGN_SHA256 = "18a2428a4ec04bfea6e4f4d70692f38ea82fbaee5a223f30f2465b895b238e21"
HELDOUT_CONTRACT_PATH = Path("config/p4_event_extract_eval_v2-heldout-qwen3.6-plus.yaml")
HELDOUT_CONTRACT_SHA256 = "26be1765204b122908e7bd09cac857c33bd3140233df47dc3358bc590e020199"
ROUND3_CONTRACT_PATH = Path("config/p4_event_extract_eval_v2-r3-qwen3.6-plus.yaml")
ROUND3_CONTRACT_SHA256 = "fa75a6cf33065745d02f74fe39e4f102723da43f37ac549058bb34fa8256a181"
ROUND3_PROMPT_PATH = Path("config/prompts/p4_news_event_extract_v2-r3.txt")
ROUND3_PROMPT_SHA256 = "0291dc882aac42878ba00c4ed3970da72f19508308cd39211467b4fd92294f44"
SELECTION_OUTCOME_SHA256 = "36b5a004b294f012b4ab1dab659d1b3d5d98320d794ad2fe90960a617f554da1"
SELECTED_FREEZE_SHA256 = "0ebc5362055af7ef6409155befc5e09d345cd4f2d8d128ea0791a0c293f66f75"

FRAME_ID = "p4.2a-heldout-frame-v2"
MODEL = "qwen3.6-plus"
WINDOW_START_UTC = "2026-08-05T16:00:00Z"
WINDOW_END_UTC = "2026-08-08T16:00:00Z"
SQLITE_WINDOW_START_UTC = "2026-08-05 16:00:00"
SQLITE_WINDOW_END_UTC = "2026-08-08 16:00:00"
EXPECTED_RAW_COUNT = 4048
EXPECTED_BY_SOURCE = {
    "akshare_ths": 1021,
    "cninfo": 2824,
    "sina_company_news": 203,
}
SEED = "alphapilot-p4.2a-heldout-frame-v2-20260809-r1"
POSITIVE_SELECTED = 40
NEGATIVE_SELECTED = 20

FULL_REHEARSAL_RECEIPT_SCHEMA = (
    "p4.2a-v2-heldout-full-path-rehearsal-pass-receipt-v1"
)
FULL_REHEARSAL_CONTRACT_SCHEMA = "p4.2a-v2-heldout-full-path-rehearsal-contract-v1"
FULL_REHEARSAL_EXPECTED_SCHEMA = "p4.2a-v2-heldout-full-path-rehearsal-expected-v1"
FULL_REHEARSAL_PUBLISHED_ARTIFACTS = frozenset(
    {"contract.json", "inputs.jsonl", "expected.json"}
)
FULL_REHEARSAL_TESTED_CODE_PATHS = frozenset(
    {
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
    }
)
_LOWER_HEX = frozenset("0123456789abcdef")
_SIX_DIGIT_SYMBOL_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_INFERENCE_SETTINGS_SAFETY: JsonObject = {
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


class HeldoutPreparationError(RuntimeError):
    """The frozen v2 held-out preparation contract was violated."""


@dataclass(frozen=True, slots=True)
class HeldoutBinding:
    root: Path
    preregistration: JsonObject
    design: JsonObject
    contract: EventExtractContract
    artifacts: Mapping[str, Path]
    retired_ids: frozenset[int]


@dataclass(frozen=True, slots=True)
class SelectionResult:
    manifest: JsonObject
    blind_rows: tuple[JsonObject, ...]


@dataclass(frozen=True, slots=True)
class SelectionExecutionBinding:
    materialization_manifest_sha256: str
    inference_state_sha256: str
    prediction_manifest_sha256: str
    execution_id: str
    eligible_candidate_count: int
    prediction_count: int
    status_ok_count: int
    status_failed_count: int
    started_at_utc: str
    completed_at_utc: str


ProductionSnapshotLoader = Callable[[Path], dev_runner.ProductionSnapshot]


def _mapping(value: object, label: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise HeldoutPreparationError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise HeldoutPreparationError(f"{label} contains a non-string key")
    return dict(value)


def _require_sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _LOWER_HEX for character in value)
    ):
        raise HeldoutPreparationError(f"{label} must be a lowercase SHA-256")
    return value


def _strict_json_loads(payload: str, label: str) -> object:
    def object_pairs(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise HeldoutPreparationError(f"{label} contains duplicate key {key!r}")
            result[key] = value
        return result

    def finite_float(raw: str) -> float:
        value = float(raw)
        if not math.isfinite(value):
            raise HeldoutPreparationError(f"{label} contains non-finite number {raw!r}")
        return value

    def reject_constant(raw: str) -> NoReturn:
        raise HeldoutPreparationError(f"{label} contains non-finite number {raw!r}")

    try:
        return json.loads(
            payload,
            object_pairs_hook=object_pairs,
            parse_float=finite_float,
            parse_constant=reject_constant,
        )
    except HeldoutPreparationError:
        raise
    except (TypeError, ValueError) as exc:
        raise HeldoutPreparationError(f"{label} is invalid JSON") from exc


def _load_json(path: Path, label: str) -> JsonObject:
    try:
        payload = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise HeldoutPreparationError(f"{label} is unavailable") from exc
    value = _strict_json_loads(payload, label)
    return _mapping(value, label)


def _load_jsonl(path: Path, label: str) -> list[JsonObject]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise HeldoutPreparationError(f"{label} is unavailable") from exc
    rows: list[JsonObject] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            raise HeldoutPreparationError(f"{label} line {number} is blank")
        value = _strict_json_loads(line, f"{label} line {number}")
        rows.append(_mapping(value, f"{label} line {number}"))
    return rows


def _resolve(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise HeldoutPreparationError(f"{label} path is invalid")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise HeldoutPreparationError(f"{label} escapes the project root")
    return path


def _verify_file(root: Path, ref: object, label: str) -> Path:
    item = _mapping(ref, label)
    path = _resolve(root, item.get("path"), label)
    expected = item.get("sha256")
    if (
        not isinstance(expected, str)
        or path.is_symlink()
        or not path.is_file()
        or common.sha256_file(path) != expected
    ):
        raise HeldoutPreparationError(f"{label} bytes drifted")
    return path


def _assert_exact(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise HeldoutPreparationError(f"{label} drifted")


def _artifact_map(root: Path, prereg: Mapping[str, Any]) -> dict[str, Path]:
    raw = _mapping(prereg.get("artifacts"), "preregistration.artifacts")
    expected = {
        "synthetic_rehearsal",
        "materialized_inputs",
        "materialization_manifest",
        "inference_state",
        "predictions",
        "prediction_manifest",
        "private_selection",
        "owner_blind",
        "ai_draft",
        "adjudication_ui",
        "owner_export",
        "human_adjudicated",
        "owner_completion",
        "evaluation_state",
        "report_directory",
    }
    if set(raw) != expected:
        raise HeldoutPreparationError("preregistration artifact registry drifted")
    return {name: _resolve(root, value, f"artifact.{name}") for name, value in raw.items()}


def _verified_source_lineage(binding: HeldoutBinding) -> JsonObject:
    frames = _mapping(binding.design.get("frames"), "design.frames")
    heldout = _mapping(frames.get("heldout_frame_v2"), "design heldout frame")
    design_lineage = _mapping(heldout.get("source_lineage"), "design source lineage")
    prereg_source = _mapping(binding.preregistration.get("source_frame"), "source frame")
    prereg_lineage = _mapping(prereg_source.get("source_lineage"), "prereg source lineage")
    reference_names = {
        "round3_evidence": "round_3_evidence_sha256",
        "round3_independent_review": "round_3_independent_review_sha256",
        "incremental_evidence": "incremental_evidence_sha256",
        "incremental_independent_review": "incremental_independent_review_sha256",
    }
    evidence: JsonObject = {}
    for design_name, prereg_name in reference_names.items():
        ref = _mapping(design_lineage.get(design_name), f"source lineage {design_name}")
        path = _verify_file(binding.root, ref, f"source lineage {design_name}")
        _assert_exact(ref.get("sha256"), prereg_lineage.get(prereg_name), prereg_name)
        evidence[design_name] = {
            "path": path.relative_to(binding.root).as_posix(),
            "sha256": ref["sha256"],
        }
    required_dates = design_lineage.get("required_closed_dates_shanghai")
    checkpoint = design_lineage.get("verified_checkpoint_date_shanghai")
    job_runs = design_lineage.get("migration_job_run_ids")
    _assert_exact(
        required_dates, prereg_lineage.get("required_closed_dates_shanghai"), "closed dates"
    )
    _assert_exact(
        checkpoint, prereg_lineage.get("verified_checkpoint_date_shanghai"), "checkpoint date"
    )
    _assert_exact(job_runs, prereg_lineage.get("migration_job_run_ids"), "migration JobRuns")
    if required_dates != ["2026-08-06", "2026-08-07", "2026-08-08"]:
        raise HeldoutPreparationError("closed-date source lineage drifted")
    if checkpoint != "2026-08-08" or job_runs != [76932, 76933]:
        raise HeldoutPreparationError("checkpoint source lineage drifted")
    return {
        "required_closed_dates_shanghai": required_dates,
        "verified_checkpoint_date_shanghai": checkpoint,
        "migration_job_run_ids": job_runs,
        "evidence": evidence,
    }


def _load_selected_contract(root: Path) -> EventExtractContract:
    design = dev_runner._load_design(root)
    prereg, round_binding = dev_runner._load_preregistration(
        root,
        design,
        round_number=3,
        preregistration_path=dev_runner.ROUND_3_PREREGISTRATION_PATH,
        preregistration_sha256=cast(str, dev_runner.ROUND_3_PREREGISTRATION_SHA256),
    )
    bindings = dev_runner._load_contracts(root, prereg, round_binding)
    selected = next((item.contract for item in bindings if item.model_slug == MODEL), None)
    if selected is None or selected.path != (root / ROUND3_CONTRACT_PATH).resolve():
        raise HeldoutPreparationError("Round 3 selected contract is unavailable")
    if selected.sha256 != ROUND3_CONTRACT_SHA256:
        raise HeldoutPreparationError("Round 3 selected contract bytes drifted")
    heldout_path = root / HELDOUT_CONTRACT_PATH
    if (
        heldout_path.is_symlink()
        or not heldout_path.is_file()
        or common.sha256_file(heldout_path) != HELDOUT_CONTRACT_SHA256
    ):
        raise HeldoutPreparationError("held-out execution contract bytes drifted")
    try:
        wrapper: object = yaml.safe_load(heldout_path.read_bytes())
    except yaml.YAMLError as exc:
        raise HeldoutPreparationError("held-out execution contract is invalid") from exc
    document = _mapping(wrapper, "held-out execution contract")
    _assert_exact(document.get("heldout_access_allowed"), True, "heldout access")
    _assert_exact(document.get("production_writes_allowed"), False, "production writes")
    _assert_exact(
        document.get("artifact_root"), "docs/phase4/eval/v2-calibration/heldout", "artifact root"
    )
    _assert_exact(
        document.get("selected_development_contract"),
        {
            "path": ROUND3_CONTRACT_PATH.as_posix(),
            "sha256": ROUND3_CONTRACT_SHA256,
            "schema_version": "p4.2a-development-event-extract-contract-v2-r3",
            "inheritance": "inference_semantics_byte_frozen",
        },
        "selected development contract",
    )
    llm = _mapping(document.get("llm"), "held-out llm")
    _assert_exact(
        {
            key: llm.get(key)
            for key in (
                "model",
                "endpoint",
                "temperature",
                "enable_thinking",
                "max_output_tokens",
                "total_deadline_seconds",
                "max_retries",
                "response_format",
            )
        },
        {
            "model": MODEL,
            "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "temperature": 0.2,
            "enable_thinking": False,
            "max_output_tokens": 2000,
            "total_deadline_seconds": 20.0,
            "max_retries": 0,
            "response_format": "json_object",
        },
        "held-out LLM controls",
    )
    request = _mapping(document.get("request_shape"), "request_shape")
    for key in ("one_news_item_per_request", "one_request_per_eligible_candidate"):
        _assert_exact(request.get(key), True, key)
    _assert_exact(request.get("failed_candidate_retries"), 0, "failed candidate retries")
    _assert_exact(request.get("automatic_retries"), 0, "automatic retries")
    return replace(
        selected,
        path=heldout_path.resolve(),
        sha256=HELDOUT_CONTRACT_SHA256,
        max_items_per_run=EXPECTED_RAW_COUNT,
    )


def _retired_ids(root: Path, prereg: Mapping[str, Any]) -> frozenset[int]:
    eligibility = _mapping(prereg.get("eligibility_and_sampling"), "eligibility_and_sampling")
    retired = _mapping(eligibility.get("retired_selection"), "retired_selection")
    path = _verify_file(root, retired, "retired_selection")
    manifest = _load_json(path, "retired selection")
    selection = _mapping(manifest.get("selection"), "retired selection.selection")
    rows = selection.get("selected")
    if not isinstance(rows, list):
        raise HeldoutPreparationError("retired selection rows are invalid")
    identifiers: list[int] = []
    for raw in rows:
        item = _mapping(raw, "retired selection item")
        identifier = item.get("news_item_id")
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
            raise HeldoutPreparationError("retired selection contains an invalid id")
        identifiers.append(identifier)
    if len(identifiers) != len(set(identifiers)):
        raise HeldoutPreparationError("retired selection contains duplicate ids")
    _assert_exact(len(identifiers), retired.get("count"), "retired count")
    compact_ids = json.dumps(sorted(identifiers), separators=(",", ":")).encode("ascii")
    digest = common.sha256_bytes(compact_ids)
    _assert_exact(digest, retired.get("sorted_ids_compact_json_sha256"), "retired id digest")
    return frozenset(identifiers)


def load_binding(project_root: Path = PROJECT_ROOT) -> HeldoutBinding:
    root = project_root.resolve()
    prereg_path = root / PREREGISTRATION_PATH
    if (
        prereg_path.is_symlink()
        or not prereg_path.is_file()
        or common.sha256_file(prereg_path) != PREREGISTRATION_SHA256
    ):
        raise HeldoutPreparationError("held-out preregistration bytes drifted")
    prereg = _load_json(prereg_path, "held-out preregistration")
    _assert_exact(
        prereg.get("schema_version"),
        "p4.2a-v2-heldout-preregistration-v1",
        "preregistration schema",
    )
    _assert_exact(
        prereg.get("status"),
        "PREREGISTERED_BEFORE_SYNTHETIC_REHEARSAL_AND_ANY_HELDOUT_ARTIFACT",
        "preregistration status",
    )
    design_ref = _mapping(prereg.get("design"), "design")
    _assert_exact(design_ref.get("path"), DESIGN_PATH.as_posix(), "design path")
    _assert_exact(design_ref.get("sha256"), DESIGN_SHA256, "design SHA")
    design_path = _verify_file(root, design_ref, "evaluation design")
    design = load_event_evaluation_design(design_path, project_root=root)
    _assert_exact(
        design.document.get("schema_version"), "p4.2a-evaluation-design-v2", "design schema"
    )
    authorities = _mapping(prereg.get("authorities"), "authorities")
    for name in authorities:
        _verify_file(root, authorities[name], f"authority.{name}")
    _assert_exact(
        _mapping(authorities.get("selection_outcome"), "selection outcome").get("sha256"),
        SELECTION_OUTCOME_SHA256,
        "selection outcome SHA",
    )
    _assert_exact(
        _mapping(authorities.get("selected_contract_freeze"), "selected freeze").get("sha256"),
        SELECTED_FREEZE_SHA256,
        "selected freeze SHA",
    )
    selected = _mapping(prereg.get("selected_extractor"), "selected_extractor")
    _assert_exact(selected.get("model"), MODEL, "selected model")
    _assert_exact(
        selected.get("round_3_prompt"),
        {
            "path": ROUND3_PROMPT_PATH.as_posix(),
            "sha256": ROUND3_PROMPT_SHA256,
            "bytes_must_remain_unchanged": True,
        },
        "Round 3 prompt binding",
    )
    _assert_exact(
        selected.get("round_3_contract"),
        {
            "path": ROUND3_CONTRACT_PATH.as_posix(),
            "sha256": ROUND3_CONTRACT_SHA256,
            "inference_semantics_must_remain_unchanged": True,
        },
        "Round 3 contract binding",
    )
    _assert_exact(
        selected.get("heldout_execution_contract"),
        {"path": HELDOUT_CONTRACT_PATH.as_posix(), "sha256": HELDOUT_CONTRACT_SHA256},
        "held-out contract binding",
    )
    source = _mapping(prereg.get("source_frame"), "source_frame")
    _assert_exact(source.get("frame_id"), FRAME_ID, "frame id")
    _assert_exact(source.get("utc_start_inclusive"), WINDOW_START_UTC, "window start")
    _assert_exact(source.get("utc_end_exclusive"), WINDOW_END_UTC, "window end")
    _assert_exact(source.get("expected_raw_candidate_count"), EXPECTED_RAW_COUNT, "raw count")
    _assert_exact(
        source.get("expected_raw_candidates_by_source"), EXPECTED_BY_SOURCE, "source counts"
    )
    request = _mapping(prereg.get("request_contract"), "request_contract")
    _assert_exact(request.get("one_news_item_per_request"), True, "one item per request")
    _assert_exact(
        request.get("one_request_per_eligible_candidate"),
        True,
        "one request per eligible candidate",
    )
    _assert_exact(
        request.get("candidate_order"),
        "ascending_news_item_id_without_gaps_or_reordering",
        "candidate order",
    )
    _assert_exact(
        request.get("any_candidate_failure"),
        "terminal_inference_failed_no_sampling_no_retry",
        "failure policy",
    )
    safety = _mapping(prereg.get("safety"), "safety")
    required_safety = {
        "production_writes_allowed": False,
        "scheduler_changes_allowed": False,
        "p4_1_observation_window_mutation_allowed": False,
        "proposals_or_orders_allowed": False,
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
    }
    _assert_exact(safety, required_safety, "safety locks")
    artifacts = _artifact_map(root, prereg)
    design_artifacts = _mapping(design.document.get("artifacts"), "design.artifacts")
    design_names = {
        "synthetic_rehearsal_directory": "synthetic_rehearsal",
        "heldout_materialized_inputs_jsonl": "materialized_inputs",
        "heldout_materialization_manifest": "materialization_manifest",
        "heldout_inference_state_jsonl": "inference_state",
        "heldout_predictions_jsonl": "predictions",
        "heldout_predictions_manifest": "prediction_manifest",
        "heldout_private_selection_manifest": "private_selection",
        "heldout_owner_blind_jsonl": "owner_blind",
    }
    for design_name, prereg_name in design_names.items():
        item = _mapping(design_artifacts.get(design_name), f"design.artifacts.{design_name}")
        _assert_exact(
            _resolve(root, item.get("path"), design_name),
            artifacts[prereg_name],
            f"artifact {design_name}",
        )
    return HeldoutBinding(
        root=root,
        preregistration=prereg,
        design=design.document,
        contract=_load_selected_contract(root),
        artifacts=artifacts,
        retired_ids=_retired_ids(root, prereg),
    )


def _publish_create_only(payloads: Sequence[tuple[Path, bytes]]) -> None:
    paths = [path for path, _payload in payloads]
    if not paths or len(paths) != len(set(paths)):
        raise HeldoutPreparationError("create-only publication paths are invalid")
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to overwrite held-out artifact: {path}")
    staged: list[tuple[Path, Path]] = []
    created: list[Path] = []
    try:
        for target, payload in payloads:
            with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            staged.append((temporary, target))
        for temporary, target in staged:
            os.link(temporary, target)
            created.append(target)
        for directory in {target.parent for _temporary, target in staged}:
            _fsync_directory(directory)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
            _fsync_directory(path.parent)
        raise
    finally:
        for temporary, _target in staged:
            temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@lru_cache(maxsize=1)
def _project_synthetic_contract() -> EventExtractContract:
    return _load_selected_contract(PROJECT_ROOT)


def _recomputed_candidate_input_hashes(
    row: Mapping[str, Any],
    contract: EventExtractContract,
) -> tuple[str, str]:
    """Rebuild active and frozen-legacy identities with the production serializer."""

    try:
        active = gold_builder._input_sha256(row, contract)
        declared = gold_builder._declared_input_sha256(row, contract)
    except (gold_builder.GoldSampleError, EventExtractValidationError) as exc:
        raise HeldoutPreparationError("candidate input identity cannot be rederived") from exc
    if active == declared:
        raise HeldoutPreparationError("candidate input identities are not selector-distinct")
    return active, declared


def _validate_candidate_input_hashes(
    rows: Sequence[Mapping[str, Any]],
    contract: EventExtractContract,
) -> None:
    for index, row in enumerate(rows, 1):
        if set(row) != common.CANDIDATE_KEYS:
            raise HeldoutPreparationError(f"candidate row {index} schema drifted")
        original_text = row.get("original_text")
        source = row.get("source")
        symbol = row.get("ingested_symbol")
        available_time = row.get("available_time")
        published_at = row.get("published_at")
        if (
            row.get("schema_version") != "p4.2a-heldout-candidate-input-v1.1"
            or source not in EXPECTED_BY_SOURCE
            or row.get("design_sha256") != DESIGN_SHA256
            or row.get("contract_sha256") != HELDOUT_CONTRACT_SHA256
            or row.get("model") != MODEL
            or not isinstance(row.get("url"), str)
            or not row["url"]
            or not isinstance(row.get("title"), str)
            or not row["title"]
            or not isinstance(original_text, str)
            or not original_text
            or row.get("text_sha256")
            != common.sha256_bytes(original_text.encode("utf-8"))
            or (
                symbol is not None
                and (
                    not isinstance(symbol, str)
                    or len(symbol) != 6
                    or not symbol.isdigit()
                )
            )
        ):
            raise HeldoutPreparationError(f"candidate row {index} identity/schema drifted")
        for field in ("content_hash", "text_sha256", "input_sha256", "declared_input_sha256"):
            _require_sha256(row.get(field), f"candidate row {index} {field}")
        for field, value, nullable in (
            ("available_time", available_time, False),
            ("published_at", published_at, True),
        ):
            if nullable and value is None:
                continue
            if not isinstance(value, str) or not value:
                raise HeldoutPreparationError(
                    f"candidate row {index} {field} is not timezone-aware"
                )
            try:
                timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HeldoutPreparationError(
                    f"candidate row {index} {field} is not timezone-aware"
                ) from exc
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise HeldoutPreparationError(
                    f"candidate row {index} {field} is not timezone-aware"
                )
        try:
            gold_builder.validate_body_evidence(row, label=f"candidate row {index}")
        except gold_builder.GoldSampleError as exc:
            raise HeldoutPreparationError(
                f"candidate row {index} body evidence drifted"
            ) from exc
        evidence = _mapping(row.get("body_evidence"), f"candidate row {index} body evidence")
        if source == "cninfo":
            if (
                row.get("body_state") != "announcement_body"
                or evidence.get("required") is not True
                or evidence.get("source") != "cninfo_pdf"
                or evidence.get("url") != row.get("url")
                or evidence.get("pdf_persisted") is not False
                or not isinstance(evidence.get("pdf_sha256"), str)
                or not isinstance(evidence.get("full_text_sha256"), str)
            ):
                raise HeldoutPreparationError(
                    f"candidate row {index} lacks the frozen CNInfo announcement body"
                )
        elif source in {"akshare_ths", "sina_company_news"}:
            expected_body_state = (
                "title_digest_short" if source == "akshare_ths" else "title_only"
            )
            if row.get("body_state") != expected_body_state or dict(evidence) != {
                "required": False,
                "source": None,
                "url": None,
                "pdf_sha256": None,
                "full_text_sha256": None,
                "full_text_character_count": None,
                "annotation_text_character_count": None,
                "body_characters_in_original_text": None,
                "text_truncated": False,
                "pdf_persisted": False,
            }:
                raise HeldoutPreparationError(
                    f"candidate row {index} non-CNInfo body discipline drifted"
                )
        else:
            raise HeldoutPreparationError(f"candidate row {index} source drifted")
        active, declared = _recomputed_candidate_input_hashes(row, contract)
        if row.get("input_sha256") != active or row.get("declared_input_sha256") != declared:
            raise HeldoutPreparationError(
                f"candidate row {index} input identities drifted from the frozen contract"
            )


def _synthetic_input(
    identifier: int,
    *,
    contract: EventExtractContract | None = None,
    source: str = "sina_company_news",
) -> JsonObject:
    if source not in EXPECTED_BY_SOURCE:
        raise HeldoutPreparationError("synthetic candidate source is outside the frozen frame")
    text = f"synthetic held-out item {identifier}"
    text_sha = common.sha256_bytes(text.encode())
    url = (
        f"https://static.cninfo.com.cn/synthetic/{identifier}.PDF"
        if source == "cninfo"
        else f"https://example.invalid/{identifier}"
    )
    if source == "cninfo":
        body_state = "announcement_body"
        body_evidence: JsonObject = {
            "required": True,
            "source": "cninfo_pdf",
            "url": url,
            "pdf_sha256": common.sha256_bytes(f"pdf-{identifier}".encode()),
            "full_text_sha256": text_sha,
            "full_text_character_count": len(text),
            "annotation_text_character_count": len(text),
            "body_characters_in_original_text": len(text),
            "text_truncated": False,
            "pdf_persisted": False,
        }
    else:
        body_state = "title_digest_short" if source == "akshare_ths" else "title_only"
        body_evidence = {
            "required": False,
            "source": None,
            "url": None,
            "pdf_sha256": None,
            "full_text_sha256": None,
            "full_text_character_count": None,
            "annotation_text_character_count": None,
            "body_characters_in_original_text": None,
            "text_truncated": False,
            "pdf_persisted": False,
        }
    row: JsonObject = {
        "schema_version": "p4.2a-heldout-candidate-input-v1.1",
        "news_item_id": identifier,
        "source": source,
        "url": url,
        "title": text,
        "ingested_symbol": f"{identifier:06d}",
        "published_at": "2026-08-06T00:00:00Z",
        "available_time": "2026-08-06T00:01:00Z",
        "original_text": text,
        "body_state": body_state,
        "body_evidence": body_evidence,
        "content_hash": common.sha256_bytes(f"content-{identifier}".encode()),
        "text_sha256": text_sha,
        "contract_sha256": HELDOUT_CONTRACT_SHA256,
        "design_sha256": DESIGN_SHA256,
        "model": MODEL,
    }
    active, declared = _recomputed_candidate_input_hashes(
        row,
        contract or _project_synthetic_contract(),
    )
    row["input_sha256"] = active
    row["declared_input_sha256"] = declared
    return row


def _synthetic_prediction(
    row: Mapping[str, Any],
    *,
    materiality: int,
    recorded_at_utc: str = "2026-08-10T00:00:30Z",
) -> JsonObject:
    return {
        "schema_version": "p4.2a-offline-extract-row-v1",
        "recorded_at_utc": recorded_at_utc,
        "news_item_id": row["news_item_id"],
        "source": row["source"],
        "input_sha256": row["input_sha256"],
        "declared_input_sha256": row["declared_input_sha256"],
        "text_sha256": row["text_sha256"],
        "contract_sha256": HELDOUT_CONTRACT_SHA256,
        "model": MODEL,
        "latency_ms": 0,
        "llm_audit_latency_ms": 0,
        "tokens": {"prompt_tokens": 0, "completion_tokens": 0},
        "security": {
            "credentials_persisted": False,
            "exception_detail_persisted": False,
            "llm_audit_storage": "isolated_in_memory",
            "llm_audit_status": "recorded",
            "production_database_access": "sqlite_uri_mode_ro_query_only",
            "raw_prompt_persisted": False,
            "raw_transport_response_persisted": False,
            "redaction_status": "passed",
        },
        "status": "ok",
        "prediction": {
            "symbols": [row["ingested_symbol"]],
            "event_type": "other",
            "direction": 0,
            "materiality": materiality,
            "summary": "合成持出集事件。",
            "confidence": 1.0,
            "evidence_span": row["original_text"],
        },
    }


def _synthetic_production_materialization_fixture(
    binding: HeldoutBinding,
) -> tuple[list[JsonObject], JsonObject]:
    """Build a legal production-shaped offline fixture for deep validator tests."""

    _ensure_synthetic_control_surface(binding)

    sources = [
        *(["akshare_ths"] * EXPECTED_BY_SOURCE["akshare_ths"]),
        *(["sina_company_news"] * EXPECTED_BY_SOURCE["sina_company_news"]),
        *(["cninfo"] * 80),
    ]
    candidates = [
        _synthetic_input(
            900_001 + offset,
            contract=binding.contract,
            source=source,
        )
        for offset, source in enumerate(sources)
    ]
    ineligible_count = EXPECTED_BY_SOURCE["cninfo"] - 80
    ineligible: list[JsonObject] = []
    ineligible_all: list[JsonObject] = []
    v17_document = _mapping(
        yaml.safe_load((PROJECT_ROOT / "config/p4_event_evaluation_v1_7.yaml").read_bytes()),
        "v1.7 synthetic fixture design",
    )
    eligibility = _mapping(
        v17_document.get("candidate_eligibility"),
        "v1.7 synthetic fixture eligibility",
    )
    minimum_characters = eligibility.get("minimum_extracted_characters")
    if (
        isinstance(minimum_characters, bool)
        or not isinstance(minimum_characters, int)
        or minimum_characters <= 0
    ):
        raise HeldoutPreparationError("synthetic fixture minimum character gate drifted")
    for offset in range(ineligible_count):
        identifier = 2_000_001 + offset
        url = f"https://static.cninfo.com.cn/synthetic/{identifier}.PDF"
        ineligible_all.append(
            {
                "news_item_id": identifier,
                "source": "cninfo",
                "url": url,
                "content_hash": common.sha256_bytes(
                    f"synthetic-ineligible-content-{identifier}".encode()
                ),
            }
        )
        ineligible.append(
            {
                "news_item_id": identifier,
                "url": url,
                "reason": "pdf_text_below_min_char_gate",
                "measured_value": 0,
                "gate_value": minimum_characters,
                "pdf_sha256": common.sha256_bytes(
                    f"synthetic-ineligible-pdf-{identifier}".encode()
                ),
            }
        )
    input_payload = common.canonical_jsonl_bytes(candidates)
    retired = _mapping(
        _mapping(
            binding.preregistration.get("eligibility_and_sampling"),
            "eligibility_and_sampling",
        ).get("retired_selection"),
        "retired_selection",
    )
    manifest: JsonObject = {
        "schema_version": "p4.2a-v2-heldout-materialization-manifest-v1",
        "frame_id": FRAME_ID,
        "lineage": {
            "preregistration": {
                "path": PREREGISTRATION_PATH.as_posix(),
                "sha256": PREREGISTRATION_SHA256,
            },
            "design": {"path": DESIGN_PATH.as_posix(), "sha256": DESIGN_SHA256},
            "contract": {
                "path": HELDOUT_CONTRACT_PATH.as_posix(),
                "sha256": HELDOUT_CONTRACT_SHA256,
                "model": MODEL,
            },
            "source_window": {
                "start_inclusive_utc": WINDOW_START_UTC,
                "end_exclusive_utc": WINDOW_END_UTC,
            },
            "source_lineage": _verified_source_lineage(binding),
            "retired_selection_sha256": retired["sha256"],
        },
        "artifacts": {
            "eligible_inputs_jsonl": {
                "path": _relative_artifact_path(binding, "materialized_inputs"),
                "sha256": common.sha256_bytes(input_payload),
                "create_only": True,
            },
            "manifest": {
                "path": _relative_artifact_path(binding, "materialization_manifest"),
                "create_only": True,
            },
        },
        "counts": {
            "raw_source_window": EXPECTED_RAW_COUNT,
            "retired_excluded_before_materialization": 0,
            "all_candidates_after_retirement": EXPECTED_RAW_COUNT,
            "eligible_candidates": len(candidates),
            "ineligible_candidates": len(ineligible),
            "ineligible_by_reason": {
                "pdf_text_below_min_char_gate": len(ineligible)
            },
        },
        "layers": {
            "all_candidates": [
                {
                    "news_item_id": row["news_item_id"],
                    "source": row["source"],
                    "url": row["url"],
                    "content_hash": row["content_hash"],
                }
                for row in candidates
            ]
            + ineligible_all,
            "eligible_candidates": [
                {
                    key: row[key]
                    for key in (
                        "news_item_id",
                        "source",
                        "input_sha256",
                        "declared_input_sha256",
                        "text_sha256",
                    )
                }
                for row in candidates
            ],
            "ineligible_candidates": ineligible,
        },
        "production_database": {"mode": "ro", "pragma_query_only": 1, "writes": 0},
    }
    return candidates, manifest


def _ensure_synthetic_control_surface(binding: HeldoutBinding) -> None:
    """Copy every byte-frozen control needed by a temporary full-chain fixture."""

    if binding.root.resolve() == PROJECT_ROOT.resolve():
        return
    shutil.copytree(
        PROJECT_ROOT / "config",
        binding.root / "config",
        dirs_exist_ok=True,
    )

    def copy_bound_files(value: object) -> None:
        if isinstance(value, Mapping):
            path_value = value.get("path")
            digest = value.get("sha256")
            if isinstance(path_value, str) and isinstance(digest, str):
                source = (PROJECT_ROOT / path_value).resolve()
                if source.is_file() and source.is_relative_to(PROJECT_ROOT):
                    target = binding.root / path_value
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
            for nested in value.values():
                copy_bound_files(nested)
        elif isinstance(value, list):
            for nested in value:
                copy_bound_files(nested)

    copy_bound_files(binding.preregistration)
    copy_bound_files(binding.design)


def _write_synthetic_production_execution_fixture(
    binding: HeldoutBinding,
    *,
    started_at_utc: str = "2026-08-10T04:00:00Z",
    recorded_at_utc: str = "2026-08-10T04:00:30Z",
    completed_at_utc: str = "2026-08-10T04:01:00Z",
    failed_status: bool = False,
    terminal_predictions_sha256: str | None = None,
) -> tuple[list[JsonObject], list[JsonObject], str]:
    """Write one production-shaped execution fixture outside the real repository."""

    if binding.root.resolve() == PROJECT_ROOT.resolve():
        raise HeldoutPreparationError(
            "synthetic execution fixture cannot target production artifacts"
        )
    candidates, materialization = _synthetic_production_materialization_fixture(binding)
    predictions = [
        _synthetic_prediction(
            row,
            materiality=2 if index <= 50 else 1,
            recorded_at_utc=recorded_at_utc,
        )
        for index, row in enumerate(candidates, 1)
    ]
    if failed_status:
        predictions[0] = {**predictions[0], "status": "extract_failed", "prediction": None}
    for name in (
        "materialized_inputs",
        "materialization_manifest",
        "inference_state",
        "predictions",
        "prediction_manifest",
    ):
        binding.artifacts[name].parent.mkdir(parents=True, exist_ok=True)
    input_payload = common.canonical_jsonl_bytes(candidates)
    binding.artifacts["materialized_inputs"].write_bytes(input_payload)
    binding.artifacts["materialization_manifest"].write_bytes(
        common.canonical_json_bytes(materialization)
    )
    materialization_sha = common.sha256_file(binding.artifacts["materialization_manifest"])
    prediction_payload = common.canonical_jsonl_bytes(predictions)
    binding.artifacts["predictions"].write_bytes(prediction_payload)
    prediction_sha = common.sha256_bytes(prediction_payload)
    execution_id = "67ce8592-82af-41c2-80d4-8d1b0f21bd35"
    snapshot: JsonObject = {
        "sqlite_uri_mode": "ro",
        "pragma_query_only": 1,
        "connection_total_changes": 0,
        "llm_call_count": 0,
        "llm_call_max_id": None,
        "trade_proposal_count": 0,
        "broker_order_count": 0,
        "non_simulate_order_count": 0,
        "news_events_table_exists": False,
        "universe_symbol_count": len(candidates),
    }
    prediction_manifest: JsonObject = {
        "schema_version": "p4.2a-v2-heldout-prediction-manifest-v1",
        "frame_id": FRAME_ID,
        "execution_id": execution_id,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "materialization_manifest_sha256": materialization_sha,
        "contract_sha256": HELDOUT_CONTRACT_SHA256,
        "model": MODEL,
        "candidate_count": len(candidates),
        "prediction_count": len(predictions),
        "status_ok_count": len(predictions),
        "status_failed_count": 0,
        "one_news_item_per_request": True,
        "one_request_per_eligible_candidate": True,
        "automatic_retries": 0,
        "failed_candidate_retries": 0,
        "production_snapshot_unchanged": True,
        "production_snapshot_before": snapshot,
        "production_snapshot_after": snapshot,
        "settings_safety": _INFERENCE_SETTINGS_SAFETY,
        "predictions": {
            "path": _relative_artifact_path(binding, "predictions"),
            "sha256": prediction_sha,
        },
    }
    binding.artifacts["prediction_manifest"].write_bytes(
        common.canonical_json_bytes(prediction_manifest)
    )
    prediction_manifest_sha = common.sha256_file(binding.artifacts["prediction_manifest"])
    states: list[JsonObject] = [
        {
            "schema_version": "p4.2a-v2-heldout-inference-state-v1",
            "status": "inference_started",
            "execution_id": execution_id,
            "started_at_utc": started_at_utc,
            "eligible_candidate_count": len(candidates),
            "candidate_order": "ascending_news_item_id",
            "preregistration_sha256": PREREGISTRATION_SHA256,
            "materialization_manifest_sha256": materialization_sha,
            "contract_sha256": HELDOUT_CONTRACT_SHA256,
            "model": MODEL,
            "automatic_retries": 0,
            "failed_candidate_retries": 0,
            "settings_safety": _INFERENCE_SETTINGS_SAFETY,
            "production_snapshot_before": snapshot,
        },
        {
            "schema_version": "p4.2a-v2-heldout-inference-state-v1",
            "status": "completed_all_eligible_candidates_once",
            "execution_id": execution_id,
            "preregistration_sha256": PREREGISTRATION_SHA256,
            "materialization_manifest_sha256": materialization_sha,
            "completed_at_utc": completed_at_utc,
            "prediction_count": len(predictions),
            "predictions_sha256": terminal_predictions_sha256 or prediction_sha,
            "prediction_manifest_sha256": prediction_manifest_sha,
            "production_snapshot_unchanged": True,
            "production_snapshot_before": snapshot,
            "production_snapshot_after": snapshot,
        },
    ]
    binding.artifacts["inference_state"].write_bytes(common.canonical_jsonl_bytes(states))
    return candidates, predictions, execution_id


def _positive_news_item_id(row: Mapping[str, Any], label: str) -> int:
    identifier = row.get("news_item_id")
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
        raise HeldoutPreparationError(f"{label} news_item_id is invalid")
    return identifier


def select_and_blind(
    binding: HeldoutBinding,
    candidates: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    *,
    execution_binding: SelectionExecutionBinding | None = None,
) -> SelectionResult:
    candidate_rows = [
        _mapping(row, f"candidate row {index}") for index, row in enumerate(candidates, start=1)
    ]
    candidate_ids = [
        _positive_news_item_id(row, f"candidate row {index}")
        for index, row in enumerate(candidate_rows, start=1)
    ]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise HeldoutPreparationError("candidate ids are not unique")
    inputs = dict(zip(candidate_ids, candidate_rows, strict=True))

    prediction_rows = [
        _mapping(row, f"prediction row {index}")
        for index, row in enumerate(predictions, start=1)
    ]
    prediction_ids = [
        _positive_news_item_id(row, f"prediction row {index}")
        for index, row in enumerate(prediction_rows, start=1)
    ]
    if len(prediction_ids) != len(set(prediction_ids)):
        raise HeldoutPreparationError("prediction ids are not unique")
    if set(prediction_ids) != set(candidate_ids):
        raise HeldoutPreparationError(
            "prediction ids do not exactly match eligible candidate ids"
        )
    if execution_binding is None and all(
        binding.artifacts[name].is_file() and not binding.artifacts[name].is_symlink()
        for name in (
            "materialized_inputs",
            "materialization_manifest",
            "inference_state",
            "predictions",
            "prediction_manifest",
        )
    ):
        execution_binding = _selection_execution_binding_from_artifacts(
            binding,
            candidate_rows,
            prediction_rows,
        )

    joined: list[tuple[JsonObject, JsonObject, str]] = []
    failures = 0
    for identifier, prediction in zip(prediction_ids, prediction_rows, strict=True):
        candidate = inputs[identifier]
        for field in common.JOIN_FIELDS:
            if prediction.get(field) != candidate.get(field):
                raise HeldoutPreparationError(f"prediction join field drifted: {field}")
        if prediction.get("status") != "ok":
            failures += 1
            continue
        result = _mapping(prediction.get("prediction"), "prediction")
        materiality = result.get("materiality")
        if (
            isinstance(materiality, bool)
            or not isinstance(materiality, int)
            or materiality not in (0, 1, 2, 3)
        ):
            raise HeldoutPreparationError("prediction materiality is invalid")
        stratum = "predicted_positive" if materiality >= 2 else "predicted_negative"
        joined.append((candidate, prediction, stratum))
    if len(joined) + failures != len(inputs):
        raise HeldoutPreparationError("full eligible pool was not inferred exactly once")
    if failures != 0:
        raise HeldoutPreparationError(
            "full eligible inference contains failures; sampling is forbidden"
        )
    by_stratum: dict[str, list[tuple[JsonObject, JsonObject, str]]] = {
        "predicted_positive": [],
        "predicted_negative": [],
    }
    for item in joined:
        by_stratum[item[2]].append(item)
    selected: list[tuple[JsonObject, JsonObject, str, str]] = []
    for stratum, count in (
        ("predicted_positive", POSITIVE_SELECTED),
        ("predicted_negative", NEGATIVE_SELECTED),
    ):
        ranked = sorted(
            by_stratum[stratum],
            key=lambda item: common.selection_rank(
                seed=SEED,
                sampling_stratum=stratum,
                news_item_id=int(item[0]["news_item_id"]),
                input_sha256=str(item[0]["input_sha256"]),
            ),
        )
        if len(ranked) < count:
            raise HeldoutPreparationError(f"insufficient {stratum} stratum")
        for candidate, prediction, actual_stratum in ranked[:count]:
            rank = common.selection_rank(
                seed=SEED,
                sampling_stratum=actual_stratum,
                news_item_id=int(candidate["news_item_id"]),
                input_sha256=str(candidate["input_sha256"]),
            )
            selected.append((candidate, prediction, actual_stratum, rank))
    selected.sort(
        key=lambda item: common.owner_order_rank(
            design_sha256=DESIGN_SHA256,
            news_item_id=int(item[0]["news_item_id"]),
            input_sha256=str(item[0]["input_sha256"]),
        )
    )
    selection_rows: list[JsonObject] = []
    blind_rows: list[JsonObject] = []
    selected_prediction_bindings: list[JsonObject] = []
    for index, (candidate, prediction, stratum, rank) in enumerate(selected, start=1):
        owner_rank = common.owner_order_rank(
            design_sha256=DESIGN_SHA256,
            news_item_id=int(candidate["news_item_id"]),
            input_sha256=str(candidate["input_sha256"]),
        )
        selection_rows.append(
            {
                "sample_index": index,
                "news_item_id": candidate["news_item_id"],
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
        selected_prediction_bindings.append(
            {
                "sample_index": index,
                "news_item_id": candidate["news_item_id"],
                "prediction_row_sha256": common.sha256_bytes(
                    common.canonical_json_bytes(prediction)
                ),
            }
        )
        blind = {
            "schema_version": "p4.2a-v2-heldout-owner-blind-item-v1",
            "design": {"path": DESIGN_PATH.as_posix(), "sha256": DESIGN_SHA256},
            "frame_id": FRAME_ID,
            "sample_index": index,
            "news_item_id": candidate["news_item_id"],
            "source": candidate["source"],
            "url": candidate["url"],
            "title": candidate["title"],
            "ingested_symbol": candidate["ingested_symbol"],
            "published_at": candidate["published_at"],
            "available_time": candidate["available_time"],
            "original_text": candidate["original_text"],
            "input_sha256": candidate["input_sha256"],
            "text_sha256": candidate["text_sha256"],
            "body_state": candidate["body_state"],
            "body_evidence": copy.deepcopy(candidate["body_evidence"]),
            "gold": {},
        }
        common.validate_blind_row(blind)
        blind_rows.append(blind)
    blind_payload = common.canonical_jsonl_bytes(blind_rows)
    materialized_inputs_sha256 = common.sha256_bytes(
        common.canonical_jsonl_bytes(candidate_rows)
    )
    predictions_sha256 = common.sha256_bytes(common.canonical_jsonl_bytes(prediction_rows))
    source_lineage: JsonObject = {
        "binding_scope": (
            "registered_full_execution"
            if execution_binding is not None
            else "payload_only_synthetic_or_unit_helper"
        ),
        "preregistration": {
            "path": PREREGISTRATION_PATH.as_posix(),
            "sha256": PREREGISTRATION_SHA256,
        },
        "design": {"path": DESIGN_PATH.as_posix(), "sha256": DESIGN_SHA256},
        "materialized_inputs": {
            "path": binding.artifacts["materialized_inputs"]
            .relative_to(binding.root)
            .as_posix(),
            "sha256": materialized_inputs_sha256,
            "row_count": len(candidate_rows),
        },
        "predictions": {
            "path": binding.artifacts["predictions"].relative_to(binding.root).as_posix(),
            "sha256": predictions_sha256,
            "row_count": len(prediction_rows),
        },
        "heldout_execution_contract": {
            "path": HELDOUT_CONTRACT_PATH.as_posix(),
            "sha256": HELDOUT_CONTRACT_SHA256,
            "model": MODEL,
        },
        "selected_predictions": {
            "binding": "sha256_of_canonical_complete_prediction_row",
            "count": len(selected_prediction_bindings),
            "bindings_sha256": common.sha256_bytes(
                common.canonical_json_bytes(selected_prediction_bindings)
            ),
            "bindings": selected_prediction_bindings,
        },
    }
    if execution_binding is not None:
        for label, digest in (
            (
                "materialization manifest SHA",
                execution_binding.materialization_manifest_sha256,
            ),
            ("inference state SHA", execution_binding.inference_state_sha256),
            ("prediction manifest SHA", execution_binding.prediction_manifest_sha256),
        ):
            _require_sha256(digest, label)
        if (
            not execution_binding.execution_id
            or execution_binding.eligible_candidate_count != len(candidate_rows)
            or execution_binding.prediction_count != len(prediction_rows)
            or execution_binding.status_ok_count != len(prediction_rows)
            or execution_binding.status_failed_count != 0
        ):
            raise HeldoutPreparationError("selection execution binding counts drifted")
        source_lineage.update(
            {
                "materialization_manifest": {
                    "path": binding.artifacts["materialization_manifest"]
                    .relative_to(binding.root)
                    .as_posix(),
                    "sha256": execution_binding.materialization_manifest_sha256,
                },
                "inference_state": {
                    "path": binding.artifacts["inference_state"]
                    .relative_to(binding.root)
                    .as_posix(),
                    "sha256": execution_binding.inference_state_sha256,
                    "event_count": 2,
                },
                "prediction_manifest": {
                    "path": binding.artifacts["prediction_manifest"]
                    .relative_to(binding.root)
                    .as_posix(),
                    "sha256": execution_binding.prediction_manifest_sha256,
                },
                "execution": {
                    "execution_id": execution_binding.execution_id,
                    "eligible_candidate_count": execution_binding.eligible_candidate_count,
                    "prediction_count": execution_binding.prediction_count,
                    "status_ok_count": execution_binding.status_ok_count,
                    "status_failed_count": execution_binding.status_failed_count,
                    "automatic_retries": 0,
                    "failed_candidate_retries": 0,
                    "terminal_status": "completed_all_eligible_candidates_once",
                },
            }
        )
    manifest: JsonObject = {
        "schema_version": "p4.2a-v2-heldout-selection-manifest-v1",
        "frame_id": FRAME_ID,
        "design": {"path": DESIGN_PATH.as_posix(), "sha256": DESIGN_SHA256},
        "source_lineage": source_lineage,
        "selection": {
            "algorithm": "sha256_rank_without_replacement_per_stratum_v1",
            "seed": SEED,
            "without_replacement": True,
            "selected_counts": {
                "predicted_positive": POSITIVE_SELECTED,
                "predicted_negative": NEGATIVE_SELECTED,
                "extract_failed": 0,
                "total": POSITIVE_SELECTED + NEGATIVE_SELECTED,
            },
            "selected": selection_rows,
        },
        "audit": {
            "eligible_candidate_count": len(candidates),
            "successful_prediction_count": len(joined),
            "extract_failed_count": failures,
            "available_by_stratum": {key: len(value) for key, value in by_stratum.items()},
            "retired_selected_intersection_count": len(
                {int(row["news_item_id"]) for row in selection_rows} & binding.retired_ids
            ),
            "input_prediction_identity_match": True,
        },
        "owner_delivery": {
            "path": binding.artifacts["owner_blind"].relative_to(binding.root).as_posix(),
            "sha256": common.sha256_bytes(blind_payload),
            "row_count": 60,
            "prediction_visible": False,
            "sampling_stratum_visible": False,
            "selection_rank_visible": False,
            "gold_state": "empty_object_pending_ai_draft_and_human_adjudication",
        },
        "production_writes": False,
    }
    if manifest["audit"]["retired_selected_intersection_count"] != 0:
        raise HeldoutPreparationError("retired held-out id was selected")
    return SelectionResult(manifest=manifest, blind_rows=tuple(blind_rows))


def run_synthetic_rehearsal(binding: HeldoutBinding) -> Path:
    # This helper intentionally does not occupy the four registered full-path
    # rehearsal artifacts.  The integrated draft/UI/evaluator rehearsal owns
    # those create-only paths and is the only component allowed to unlock
    # materialization.
    directory = binding.artifacts["synthetic_rehearsal"] / "preparation-helper"
    inputs = [_synthetic_input(identifier) for identifier in range(1, 81)]
    predictions = [
        _synthetic_prediction(row, materiality=2 if index <= 50 else 1)
        for index, row in enumerate(inputs, start=1)
    ]
    result = select_and_blind(binding, inputs, predictions)
    contract = {
        "schema_version": "p4.2a-v2-heldout-synthetic-rehearsal-contract-v1",
        "preregistration": {
            "path": PREREGISTRATION_PATH.as_posix(),
            "sha256": PREREGISTRATION_SHA256,
        },
        "real_database_read": False,
        "real_model_calls": 0,
        "selection_counts": {"predicted_positive": 40, "predicted_negative": 20},
    }
    expected = {
        "schema_version": "p4.2a-v2-heldout-synthetic-rehearsal-expected-v1",
        "selected_count": 60,
        "blind_rows": 60,
        "forbidden_blind_paths": 0,
        "retired_intersection": 0,
    }
    payloads = {
        directory / "contract.json": common.canonical_json_bytes(contract),
        directory / "inputs.jsonl": common.canonical_jsonl_bytes(inputs),
        directory / "expected.json": common.canonical_json_bytes(expected),
    }
    receipt = {
        "schema_version": "p4.2a-v2-heldout-preparation-helper-receipt-v1",
        "status": "preparation_helper_passed_not_full_path",
        "full_path_covered": False,
        "materialization_gate_unlock": False,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "design_sha256": DESIGN_SHA256,
        "heldout_contract_sha256": HELDOUT_CONTRACT_SHA256,
        "inputs_sha256": common.sha256_bytes(payloads[directory / "inputs.jsonl"]),
        "selection_manifest_sha256": common.sha256_bytes(
            common.canonical_json_bytes(result.manifest)
        ),
        "blind_sha256": common.sha256_bytes(common.canonical_jsonl_bytes(result.blind_rows)),
        "real_database_read": False,
        "real_model_calls": 0,
        "production_writes": False,
    }
    payloads[directory / "preparation-helper-receipt.json"] = common.canonical_json_bytes(receipt)
    _publish_create_only(tuple(payloads.items()))
    return directory / "preparation-helper-receipt.json"


def _materialization_design(binding: HeldoutBinding) -> gold_builder.FrozenEvaluationDesign:
    v17_path = binding.root / "config/p4_event_evaluation_v1_7.yaml"
    raw: object = yaml.safe_load(v17_path.read_bytes())
    old = _mapping(raw, "v1.7 design")
    document = copy.deepcopy(binding.design)
    document["candidate_eligibility"] = copy.deepcopy(old.get("candidate_eligibility"))
    return gold_builder.FrozenEvaluationDesign(
        path=binding.root / DESIGN_PATH,
        sha256=DESIGN_SHA256,
        document=document,
        base_contract=gold_builder.load_contract(binding.root / dev_runner.BASE_CONTRACT_PATH),
    )


def _window_rows(binding: HeldoutBinding, database: Path) -> list[gold_builder.NewsRow]:
    query = """
    SELECT id, source, symbol, title, url, published_at, available_time, content_hash, raw_payload
    FROM news_items
    WHERE available_time >= ? AND available_time < ?
      AND source IN ('cninfo', 'akshare_ths', 'sina_company_news')
    ORDER BY id
    """
    with gold_builder.open_read_only_database(database) as connection:
        raw = connection.execute(
            query,
            (SQLITE_WINDOW_START_UTC, SQLITE_WINDOW_END_UTC),
        ).fetchall()
        if connection.total_changes != 0:
            raise HeldoutPreparationError("production database was modified")
    rows = [gold_builder._news_row(row) for row in raw]
    if len(rows) != EXPECTED_RAW_COUNT:
        raise HeldoutPreparationError(f"raw candidate count drifted: {len(rows)}")
    counts = dict(sorted(Counter(row.source for row in rows).items()))
    if counts != EXPECTED_BY_SOURCE:
        raise HeldoutPreparationError(f"raw source counts drifted: {counts}")
    if [row.news_item_id for row in rows] != sorted(row.news_item_id for row in rows):
        raise HeldoutPreparationError("raw candidates are not ordered by id")
    return [row for row in rows if row.news_item_id not in binding.retired_ids]


def _validate_full_path_rehearsal_gate(binding: HeldoutBinding) -> JsonObject:
    directory = binding.artifacts["synthetic_rehearsal"]
    receipt_path = directory / "pass-receipt.json"
    expected_names = FULL_REHEARSAL_PUBLISHED_ARTIFACTS | {receipt_path.name}
    if directory.is_symlink() or not directory.is_dir():
        raise HeldoutPreparationError("full-path synthetic rehearsal directory is unavailable")
    children = list(directory.iterdir())
    if (
        {path.name for path in children} != expected_names
        or any(path.is_symlink() or not path.is_file() for path in children)
    ):
        raise HeldoutPreparationError(
            "full-path synthetic rehearsal artifact registry drifted"
        )

    contract_path = directory / "contract.json"
    inputs_path = directory / "inputs.jsonl"
    expected_path = directory / "expected.json"
    receipt = _load_json(receipt_path, "synthetic rehearsal receipt")
    contract = _load_json(contract_path, "synthetic rehearsal contract")
    inputs = _load_jsonl(inputs_path, "synthetic rehearsal inputs")
    expected = _load_json(expected_path, "synthetic rehearsal expected result")

    receipt_fields = {
        "schema_version",
        "status",
        "full_path_covered",
        "materialization_gate_unlock",
        "preregistration_sha256",
        "design_sha256",
        "heldout_contract_sha256",
        "published_artifact_sha256",
        "tested_code_sha256",
        "internal_artifact_sha256",
        "materialized_candidate_count",
        "inference_candidate_count",
        "one_item_model_call_count",
        "mock_model_calls",
        "selection_counts",
        "owner_chain_count",
        "formal_state_events",
        "synthetic_report_status",
        "production_writes",
        "production_heldout_artifacts_changed",
        "real_database_reads",
        "real_network_calls",
        "real_model_calls",
        "real_heldout_metrics_computed",
        "real_metrics_disclosed",
        "temporary_workspace_removed",
    }
    contract_fields = {
        "schema_version",
        "preregistration_sha256",
        "design_sha256",
        "heldout_contract_sha256",
        "fixture",
        "request_contract",
        "selection_counts",
        "workspace_policy",
        "network_allowed",
        "production_database_allowed",
        "production_heldout_artifact_writes_allowed",
        "real_model_calls_allowed",
        "real_heldout_metrics_allowed",
        "tested_code_sha256",
    }
    expected_fields = {
        "schema_version",
        "materialized_candidate_count",
        "inference_candidate_count",
        "mock_model_call_count",
        "one_item_model_call_count",
        "selection_counts",
        "blind_row_count",
        "draft_row_count",
        "owner_chain_count",
        "formal_state_events",
        "synthetic_report_status",
        "real_heldout_metrics_computed",
        "production_writes",
        "real_database_reads",
        "real_network_calls",
        "real_model_calls",
    }
    if (
        set(receipt) != receipt_fields
        or set(contract) != contract_fields
        or set(expected) != expected_fields
    ):
        raise HeldoutPreparationError("full-path synthetic rehearsal schema drifted")

    published_hashes = _mapping(
        receipt.get("published_artifact_sha256"), "rehearsal published hashes"
    )
    if set(published_hashes) != FULL_REHEARSAL_PUBLISHED_ARTIFACTS:
        raise HeldoutPreparationError("rehearsal published artifact registry drifted")
    for name in FULL_REHEARSAL_PUBLISHED_ARTIFACTS:
        digest = _require_sha256(published_hashes.get(name), f"rehearsal {name} SHA")
        if common.sha256_file(directory / name) != digest:
            raise HeldoutPreparationError(f"rehearsal {name} bytes drifted")

    tested_code = _mapping(receipt.get("tested_code_sha256"), "rehearsal tested code")
    contract_tested_code = _mapping(
        contract.get("tested_code_sha256"), "rehearsal contract tested code"
    )
    if (
        set(tested_code) != FULL_REHEARSAL_TESTED_CODE_PATHS
        or contract_tested_code != tested_code
    ):
        raise HeldoutPreparationError("rehearsal tested-code registry drifted")
    for relative, raw_digest in tested_code.items():
        digest = _require_sha256(raw_digest, f"tested code {relative} SHA")
        path = _resolve(binding.root, relative, f"tested code {relative}")
        if path.is_symlink() or not path.is_file() or common.sha256_file(path) != digest:
            raise HeldoutPreparationError(f"tested code bytes drifted: {relative}")

    internal_hashes = _mapping(
        receipt.get("internal_artifact_sha256"), "rehearsal internal hashes"
    )
    expected_internal_names = {
        "materialized_inputs",
        "materialization_manifest",
        "inference_state",
        "predictions",
        "prediction_manifest",
        "private_selection",
        "owner_blind",
        "ai_draft",
        "adjudication_ui",
        "owner_export",
        "human_adjudicated",
        "owner_completion",
        "evaluation_state",
        "synthetic_report",
    }
    if set(internal_hashes) != expected_internal_names:
        raise HeldoutPreparationError("rehearsal internal artifact registry drifted")
    for name, digest in internal_hashes.items():
        _require_sha256(digest, f"rehearsal internal artifact {name} SHA")

    selection_counts = {
        "predicted_positive": POSITIVE_SELECTED,
        "predicted_negative": NEGATIVE_SELECTED,
        "total": POSITIVE_SELECTED + NEGATIVE_SELECTED,
    }
    expected_result = {
        "schema_version": FULL_REHEARSAL_EXPECTED_SCHEMA,
        "materialized_candidate_count": 80,
        "inference_candidate_count": 80,
        "mock_model_call_count": 80,
        "one_item_model_call_count": 80,
        "selection_counts": selection_counts,
        "blind_row_count": 60,
        "draft_row_count": 60,
        "owner_chain_count": 60,
        "formal_state_events": ["evaluation_started", "evaluation_completed"],
        "synthetic_report_status": "synthetic_rehearsal",
        "real_heldout_metrics_computed": False,
        "production_writes": False,
        "real_database_reads": 0,
        "real_network_calls": 0,
        "real_model_calls": 0,
    }
    if expected != expected_result:
        raise HeldoutPreparationError("synthetic rehearsal expected-result contract drifted")
    if contract != {
        "schema_version": FULL_REHEARSAL_CONTRACT_SCHEMA,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "design_sha256": DESIGN_SHA256,
        "heldout_contract_sha256": HELDOUT_CONTRACT_SHA256,
        "fixture": {
            "synthetic_candidate_count": 80,
            "predicted_positive_pool_count": 50,
            "predicted_negative_pool_count": 30,
        },
        "request_contract": {
            "one_news_item_per_request": True,
            "one_request_per_eligible_candidate": True,
            "automatic_retries": 0,
        },
        "selection_counts": selection_counts,
        "workspace_policy": "temporary_and_outside_registered_artifact_roots",
        "network_allowed": False,
        "production_database_allowed": False,
        "production_heldout_artifact_writes_allowed": False,
        "real_model_calls_allowed": 0,
        "real_heldout_metrics_allowed": False,
        "tested_code_sha256": tested_code,
    }:
        raise HeldoutPreparationError("synthetic rehearsal registered contract drifted")

    input_ids = [
        _positive_news_item_id(row, f"rehearsal input row {index}")
        for index, row in enumerate(inputs, start=1)
    ]
    if (
        len(inputs) != 80
        or input_ids != sorted(input_ids)
        or len(input_ids) != len(set(input_ids))
        or any(
            row.get("schema_version") != "p4.2a-heldout-candidate-input-v1.1"
            or row.get("contract_sha256") != HELDOUT_CONTRACT_SHA256
            or row.get("model") != MODEL
            for row in inputs
        )
    ):
        raise HeldoutPreparationError("synthetic rehearsal inputs drifted")

    receipt_expected = {
        "schema_version": FULL_REHEARSAL_RECEIPT_SCHEMA,
        "status": "passed",
        "full_path_covered": True,
        "materialization_gate_unlock": True,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "design_sha256": DESIGN_SHA256,
        "heldout_contract_sha256": HELDOUT_CONTRACT_SHA256,
        "published_artifact_sha256": published_hashes,
        "tested_code_sha256": tested_code,
        "internal_artifact_sha256": internal_hashes,
        "materialized_candidate_count": 80,
        "inference_candidate_count": 80,
        "one_item_model_call_count": 80,
        "mock_model_calls": 80,
        "selection_counts": selection_counts,
        "owner_chain_count": 60,
        "formal_state_events": ["evaluation_started", "evaluation_completed"],
        "synthetic_report_status": "synthetic_rehearsal",
        "production_writes": False,
        "production_heldout_artifacts_changed": False,
        "real_database_reads": 0,
        "real_network_calls": 0,
        "real_model_calls": 0,
        "real_heldout_metrics_computed": False,
        "real_metrics_disclosed": False,
        "temporary_workspace_removed": True,
    }
    if receipt != receipt_expected:
        raise HeldoutPreparationError("full-path synthetic rehearsal receipt drifted")
    return receipt


def run_materialize(
    binding: HeldoutBinding,
    *,
    database: Path | None = None,
    pdf_fetcher: gold_builder.PdfFetcher = gold_builder.download_cninfo_pdf,
    pdf_text_extractor: gold_builder.PdfTextExtractor = gold_builder.extract_cninfo_pdf_text,
) -> tuple[Path, Path]:
    _validate_full_path_rehearsal_gate(binding)
    db = database or (binding.root / "data/alphapilot.db")
    rows = _window_rows(binding, db)
    if {row.news_item_id for row in rows} & binding.retired_ids:
        raise HeldoutPreparationError("retired ids were not excluded before materialization")
    materialized = gold_builder.materialize_heldout_candidate_inputs(
        rows,
        _materialization_design(binding),
        binding.contract,
        pdf_fetcher=pdf_fetcher,
        pdf_text_extractor=pdf_text_extractor,
    )
    input_payload = common.canonical_jsonl_bytes(materialized.eligible_records)
    manifest = {
        "schema_version": "p4.2a-v2-heldout-materialization-manifest-v1",
        "frame_id": FRAME_ID,
        "lineage": {
            "preregistration": {
                "path": PREREGISTRATION_PATH.as_posix(),
                "sha256": PREREGISTRATION_SHA256,
            },
            "design": {"path": DESIGN_PATH.as_posix(), "sha256": DESIGN_SHA256},
            "contract": {
                "path": HELDOUT_CONTRACT_PATH.as_posix(),
                "sha256": HELDOUT_CONTRACT_SHA256,
                "model": MODEL,
            },
            "source_window": {
                "start_inclusive_utc": WINDOW_START_UTC,
                "end_exclusive_utc": WINDOW_END_UTC,
            },
            "source_lineage": _verified_source_lineage(binding),
            "retired_selection_sha256": _mapping(
                _mapping(binding.preregistration["eligibility_and_sampling"], "eligibility")[
                    "retired_selection"
                ],
                "retired",
            )["sha256"],
        },
        "artifacts": {
            "eligible_inputs_jsonl": {
                "path": binding.artifacts["materialized_inputs"]
                .relative_to(binding.root)
                .as_posix(),
                "sha256": common.sha256_bytes(input_payload),
                "create_only": True,
            },
            "manifest": {
                "path": binding.artifacts["materialization_manifest"]
                .relative_to(binding.root)
                .as_posix(),
                "create_only": True,
            },
        },
        "counts": {
            "raw_source_window": EXPECTED_RAW_COUNT,
            "retired_excluded_before_materialization": EXPECTED_RAW_COUNT - len(rows),
            "all_candidates_after_retirement": len(materialized.all_candidates),
            "eligible_candidates": len(materialized.eligible_records),
            "ineligible_candidates": len(materialized.ineligible_candidates),
            "ineligible_by_reason": dict(materialized.reason_counts),
        },
        "layers": {
            "all_candidates": list(materialized.all_candidates),
            "eligible_candidates": [
                {
                    "news_item_id": row["news_item_id"],
                    "source": row["source"],
                    "input_sha256": row["input_sha256"],
                    "declared_input_sha256": row["declared_input_sha256"],
                    "text_sha256": row["text_sha256"],
                }
                for row in materialized.eligible_records
            ],
            "ineligible_candidates": list(materialized.ineligible_candidates),
        },
        "production_database": {"mode": "ro", "pragma_query_only": 1, "writes": 0},
    }
    _publish_create_only(
        (
            (binding.artifacts["materialized_inputs"], input_payload),
            (binding.artifacts["materialization_manifest"], common.canonical_json_bytes(manifest)),
        )
    )
    return binding.artifacts["materialized_inputs"], binding.artifacts["materialization_manifest"]


def _extract_record(row: Mapping[str, Any]) -> ExtractRecord:
    return ExtractRecord(
        news_item_id=int(row["news_item_id"]),
        source=str(row["source"]),
        ingested_symbol=cast(str | None, row.get("ingested_symbol")),
        title=str(row["title"]),
        original_text=str(row["original_text"]),
        published_at=cast(str | None, row.get("published_at")),
        available_time=str(row["available_time"]),
        body_state=str(row["body_state"]),
        declared_input_sha256=str(row["declared_input_sha256"]),
        declared_text_sha256=str(row["text_sha256"]),
        declared_input_representation=DECLARED_INPUT_LEGACY_V1,
    )


def _append_state_descriptor(descriptor: int, row: Mapping[str, Any]) -> None:
    remaining = memoryview(common.canonical_json_bytes(row))
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("held-out inference state write made no progress")
        remaining = remaining[written:]
    os.fsync(descriptor)


@contextmanager
def _exclusive_inference_state(path: Path) -> Iterator[int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        _fsync_directory(path.parent)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield descriptor
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def run_infer(
    binding: HeldoutBinding,
    *,
    settings: Settings | None = None,
    chat_json_fn: ChatJsonCallable | None = None,
    snapshot_loader: ProductionSnapshotLoader = dev_runner._production_snapshot,
) -> tuple[Path, Path]:
    candidates = _load_jsonl(binding.artifacts["materialized_inputs"], "held-out inputs")
    manifest = _load_json(binding.artifacts["materialization_manifest"], "materialization manifest")
    if _mapping(manifest.get("artifacts"), "manifest artifacts")["eligible_inputs_jsonl"][
        "sha256"
    ] != common.sha256_file(binding.artifacts["materialized_inputs"]):
        raise HeldoutPreparationError("materialized input digest drifted")
    ids = [int(row["news_item_id"]) for row in candidates]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise HeldoutPreparationError("eligible candidates are not unique ascending ids")
    for path in (
        binding.artifacts["inference_state"],
        binding.artifacts["predictions"],
        binding.artifacts["prediction_manifest"],
    ):
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to reuse held-out inference artifact: {path}")
    base_settings = settings or _settings_from_project_env(binding.root)
    try:
        active_settings = dev_runner._model_settings(base_settings, binding.contract)
        settings_safety = dev_runner._settings_safety(active_settings)
    except (
        dev_runner.CalibrationRoundError,
        HeldoutPredictionError,
    ) as exc:
        raise HeldoutPreparationError(
            "held-out inference settings failed the pre-state safety gate"
        ) from exc
    production_before = snapshot_loader(binding.root)
    materialization_sha256 = common.sha256_file(binding.artifacts["materialization_manifest"])
    execution_id = str(uuid.uuid4())
    state = binding.artifacts["inference_state"]
    active_candidate_index: int | None = None
    active_candidate_id: int | None = None
    with _exclusive_inference_state(state) as state_descriptor:
        _append_state_descriptor(
            state_descriptor,
            {
                "schema_version": "p4.2a-v2-heldout-inference-state-v1",
                "status": "inference_started",
                "execution_id": execution_id,
                "started_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "eligible_candidate_count": len(candidates),
                "candidate_order": "ascending_news_item_id",
                "preregistration_sha256": PREREGISTRATION_SHA256,
                "materialization_manifest_sha256": materialization_sha256,
                "contract_sha256": HELDOUT_CONTRACT_SHA256,
                "model": MODEL,
                "automatic_retries": 0,
                "failed_candidate_retries": 0,
                "settings_safety": settings_safety,
                "production_snapshot_before": dev_runner._snapshot_evidence(
                    production_before
                ),
            },
        )
        try:
            for index, candidate in enumerate(candidates, start=1):
                active_candidate_index = index
                active_candidate_id = _positive_news_item_id(
                    candidate, f"eligible candidate {index}"
                )
                summary = extract_records(
                    binding.contract,
                    [_extract_record(candidate)],
                    output_path=binding.artifacts["predictions"],
                    eval_root=binding.artifacts["predictions"].parent,
                    universe_symbols=production_before.universe_symbols,
                    settings=active_settings,
                    retry_failures=False,
                    chat_json_fn=chat_json_fn,
                )
                if (
                    summary.newly_attempted_count != 1
                    or summary.success_count != 1
                    or summary.failure_count != 0
                ):
                    raise HeldoutPreparationError(
                        f"candidate {candidate['news_item_id']} failed; inference is terminal"
                    )
                if summary.retried_failure_count != 0:
                    raise HeldoutPreparationError("a held-out candidate was retried")
                if index != summary.output_line_count:
                    raise HeldoutPreparationError("prediction append-only line count drifted")
            production_after = snapshot_loader(binding.root)
            if production_after != production_before:
                raise HeldoutPreparationError("production database safety snapshot changed")
            predictions = _load_jsonl(binding.artifacts["predictions"], "held-out predictions")
            prediction_manifest = {
                "schema_version": "p4.2a-v2-heldout-prediction-manifest-v1",
                "frame_id": FRAME_ID,
                "execution_id": execution_id,
                "preregistration_sha256": PREREGISTRATION_SHA256,
                "materialization_manifest_sha256": materialization_sha256,
                "contract_sha256": HELDOUT_CONTRACT_SHA256,
                "model": MODEL,
                "candidate_count": len(candidates),
                "prediction_count": len(predictions),
                "status_ok_count": sum(row.get("status") == "ok" for row in predictions),
                "status_failed_count": sum(row.get("status") != "ok" for row in predictions),
                "one_news_item_per_request": True,
                "one_request_per_eligible_candidate": True,
                "automatic_retries": 0,
                "failed_candidate_retries": 0,
                "production_snapshot_unchanged": True,
                "production_snapshot_before": dev_runner._snapshot_evidence(
                    production_before
                ),
                "production_snapshot_after": dev_runner._snapshot_evidence(
                    production_after
                ),
                "settings_safety": settings_safety,
                "predictions": {
                    "path": binding.artifacts["predictions"].relative_to(binding.root).as_posix(),
                    "sha256": common.sha256_file(binding.artifacts["predictions"]),
                },
            }
            _publish_create_only(
                (
                    (
                        binding.artifacts["prediction_manifest"],
                        common.canonical_json_bytes(prediction_manifest),
                    ),
                )
            )
            _append_state_descriptor(
                state_descriptor,
                {
                    "schema_version": "p4.2a-v2-heldout-inference-state-v1",
                    "status": "completed_all_eligible_candidates_once",
                    "execution_id": execution_id,
                    "preregistration_sha256": PREREGISTRATION_SHA256,
                    "materialization_manifest_sha256": materialization_sha256,
                    "completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "prediction_count": len(predictions),
                    "predictions_sha256": common.sha256_file(binding.artifacts["predictions"]),
                    "prediction_manifest_sha256": common.sha256_file(
                        binding.artifacts["prediction_manifest"]
                    ),
                    "production_snapshot_unchanged": True,
                    "production_snapshot_before": dev_runner._snapshot_evidence(
                        production_before
                    ),
                    "production_snapshot_after": dev_runner._snapshot_evidence(
                        production_after
                    ),
                },
            )
        except BaseException as exc:
            _append_state_descriptor(
                state_descriptor,
                {
                    "schema_version": "p4.2a-v2-heldout-inference-state-v1",
                    "status": "terminal_failed_no_sampling_no_retry",
                    "execution_id": execution_id,
                    "preregistration_sha256": PREREGISTRATION_SHA256,
                    "materialization_manifest_sha256": materialization_sha256,
                    "failed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "error_type": type(exc).__name__,
                    "failed_candidate_index": active_candidate_index,
                    "failed_news_item_id": active_candidate_id,
                    "automatic_retries": 0,
                    "failed_candidate_retries": 0,
                    "sampling_allowed": False,
                },
            )
            raise
    return binding.artifacts["predictions"], binding.artifacts["prediction_manifest"]


def _require_regular_artifact(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise HeldoutPreparationError(f"{label} is unavailable or not a regular file")


def _relative_artifact_path(binding: HeldoutBinding, name: str) -> str:
    return binding.artifacts[name].relative_to(binding.root).as_posix()


def _validate_materialization_for_selection(
    binding: HeldoutBinding,
    manifest: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    inputs_sha256: str,
) -> None:
    if manifest.get("synthetic_rehearsal") is True:
        if binding.root.resolve() == PROJECT_ROOT.resolve():
            raise HeldoutPreparationError(
                "synthetic materialization cannot enter the production owner chain"
            )
        synthetic_fields = {
            "schema_version",
            "frame_id",
            "synthetic_rehearsal",
            "lineage",
            "artifacts",
            "counts",
            "production_database",
        }
        artifacts = _mapping(manifest.get("artifacts"), "synthetic materialization artifacts")
        if (
            set(manifest) != synthetic_fields
            or manifest.get("schema_version")
            != "p4.2a-v2-heldout-materialization-manifest-v1"
            or manifest.get("frame_id") != FRAME_ID
            or manifest.get("lineage")
            != {
                "preregistration_sha256": PREREGISTRATION_SHA256,
                "design_sha256": DESIGN_SHA256,
                "heldout_contract_sha256": HELDOUT_CONTRACT_SHA256,
            }
            or artifacts
            != {
                "eligible_inputs_jsonl": {
                    "path": _relative_artifact_path(binding, "materialized_inputs"),
                    "sha256": inputs_sha256,
                    "create_only": True,
                }
            }
            or manifest.get("counts")
            != {
                "all_candidates": len(candidates),
                "eligible_candidates": len(candidates),
                "ineligible_candidates": 0,
            }
            or manifest.get("production_database")
            != {"opened": False, "reads": 0, "writes": 0}
        ):
            raise HeldoutPreparationError("synthetic materialization manifest drifted")
        return
    expected_top_level = {
        "schema_version",
        "frame_id",
        "lineage",
        "artifacts",
        "counts",
        "layers",
        "production_database",
    }
    if set(manifest) != expected_top_level:
        raise HeldoutPreparationError("materialization manifest schema drifted")
    lineage = _mapping(manifest.get("lineage"), "materialization lineage")
    if set(lineage) != {
        "preregistration",
        "design",
        "contract",
        "source_window",
        "source_lineage",
        "retired_selection_sha256",
    }:
        raise HeldoutPreparationError("materialization lineage schema drifted")
    artifacts = _mapping(manifest.get("artifacts"), "materialization artifacts")
    if set(artifacts) != {"eligible_inputs_jsonl", "manifest"}:
        raise HeldoutPreparationError("materialization artifact schema drifted")
    inputs_ref = _mapping(artifacts.get("eligible_inputs_jsonl"), "materialized inputs ref")
    manifest_ref = _mapping(artifacts.get("manifest"), "materialization manifest ref")
    counts = _mapping(manifest.get("counts"), "materialization counts")
    if set(counts) != {
        "raw_source_window",
        "retired_excluded_before_materialization",
        "all_candidates_after_retirement",
        "eligible_candidates",
        "ineligible_candidates",
        "ineligible_by_reason",
    }:
        raise HeldoutPreparationError("materialization count schema drifted")
    layers = _mapping(manifest.get("layers"), "materialization layers")
    if set(layers) != {"all_candidates", "eligible_candidates", "ineligible_candidates"}:
        raise HeldoutPreparationError("materialization layer schema drifted")
    all_layer = layers.get("all_candidates")
    eligible_layer = layers.get("eligible_candidates")
    ineligible_layer = layers.get("ineligible_candidates")
    if not all(isinstance(value, list) for value in (all_layer, eligible_layer, ineligible_layer)):
        raise HeldoutPreparationError("materialization layers are invalid")
    all_rows = cast(list[Any], all_layer)
    eligible_rows = cast(list[Any], eligible_layer)
    ineligible_rows = cast(list[Any], ineligible_layer)
    _validate_candidate_input_hashes(candidates, binding.contract)
    window_start = datetime.fromisoformat(WINDOW_START_UTC.replace("Z", "+00:00"))
    window_end = datetime.fromisoformat(WINDOW_END_UTC.replace("Z", "+00:00"))
    candidate_ids: list[int] = []
    for index, candidate in enumerate(candidates, 1):
        identifier = _positive_news_item_id(candidate, f"candidate row {index}")
        available_raw = candidate.get("available_time")
        if not isinstance(available_raw, str) or not available_raw:
            raise HeldoutPreparationError(
                f"candidate row {index} available_time is invalid"
            )
        try:
            available = datetime.fromisoformat(available_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HeldoutPreparationError(
                f"candidate row {index} available_time is invalid"
            ) from exc
        if (
            available.tzinfo is None
            or available.utcoffset() is None
            or not window_start <= available.astimezone(UTC) < window_end
            or candidate.get("source") not in EXPECTED_BY_SOURCE
            or identifier in binding.retired_ids
        ):
            raise HeldoutPreparationError(
                f"candidate row {index} is outside the frozen materialization frame"
            )
        candidate_ids.append(identifier)
    if candidate_ids != sorted(candidate_ids) or len(candidate_ids) != len(set(candidate_ids)):
        raise HeldoutPreparationError("materialized candidate ids are not unique ascending")
    expected_eligible_layer = [
        {
            "news_item_id": row.get("news_item_id"),
            "source": row.get("source"),
            "input_sha256": row.get("input_sha256"),
            "declared_input_sha256": row.get("declared_input_sha256"),
            "text_sha256": row.get("text_sha256"),
        }
        for row in candidates
    ]
    contract_ref = _mapping(lineage.get("contract"), "materialization contract")
    eligibility = _mapping(
        binding.preregistration.get("eligibility_and_sampling"),
        "eligibility_and_sampling",
    )
    retired = _mapping(eligibility.get("retired_selection"), "retired_selection")
    production_database = _mapping(
        manifest.get("production_database"), "materialization production database"
    )
    if (
        set(production_database) != {"mode", "pragma_query_only", "writes"}
        or production_database.get("mode") != "ro"
        or isinstance(production_database.get("pragma_query_only"), bool)
        or production_database.get("pragma_query_only") != 1
        or isinstance(production_database.get("writes"), bool)
        or production_database.get("writes") != 0
    ):
        raise HeldoutPreparationError("materialization production database safety drifted")
    if (
        manifest.get("schema_version") != "p4.2a-v2-heldout-materialization-manifest-v1"
        or manifest.get("frame_id") != FRAME_ID
        or lineage.get("preregistration")
        != {"path": PREREGISTRATION_PATH.as_posix(), "sha256": PREREGISTRATION_SHA256}
        or lineage.get("design")
        != {"path": DESIGN_PATH.as_posix(), "sha256": DESIGN_SHA256}
        or contract_ref
        != {
            "path": HELDOUT_CONTRACT_PATH.as_posix(),
            "sha256": HELDOUT_CONTRACT_SHA256,
            "model": MODEL,
        }
        or lineage.get("source_window")
        != {
            "start_inclusive_utc": WINDOW_START_UTC,
            "end_exclusive_utc": WINDOW_END_UTC,
        }
        or lineage.get("source_lineage") != _verified_source_lineage(binding)
        or lineage.get("retired_selection_sha256") != retired.get("sha256")
        or inputs_ref
        != {
            "path": _relative_artifact_path(binding, "materialized_inputs"),
            "sha256": inputs_sha256,
            "create_only": True,
        }
        or manifest_ref
        != {
            "path": _relative_artifact_path(binding, "materialization_manifest"),
            "create_only": True,
        }
        or eligible_rows != expected_eligible_layer
    ):
        raise HeldoutPreparationError("materialization manifest lineage or counts drifted")

    count_values: dict[str, int] = {}
    for field in (
        "raw_source_window",
        "retired_excluded_before_materialization",
        "all_candidates_after_retirement",
        "eligible_candidates",
        "ineligible_candidates",
    ):
        value = counts.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HeldoutPreparationError(f"materialization count {field} is invalid")
        count_values[field] = value
    if (
        count_values["raw_source_window"] != EXPECTED_RAW_COUNT
        or count_values["retired_excluded_before_materialization"] != 0
        or count_values["all_candidates_after_retirement"] != EXPECTED_RAW_COUNT
        or count_values["eligible_candidates"] != len(candidates)
        or count_values["ineligible_candidates"] != len(ineligible_rows)
        or len(all_rows) != EXPECTED_RAW_COUNT
        or len(candidates) + len(ineligible_rows) != EXPECTED_RAW_COUNT
    ):
        raise HeldoutPreparationError("materialization production counts drifted")

    all_ids: list[int] = []
    all_by_id: dict[int, Mapping[str, Any]] = {}
    source_counts: Counter[str] = Counter()
    for index, raw in enumerate(all_rows, 1):
        row = _mapping(raw, f"materialization all candidate {index}")
        if set(row) != {"news_item_id", "source", "url", "content_hash"}:
            raise HeldoutPreparationError("materialization all-candidate schema drifted")
        identifier = _positive_news_item_id(row, f"materialization all candidate {index}")
        source = row.get("source")
        url = row.get("url")
        content_hash = row.get("content_hash")
        if (
            source not in EXPECTED_BY_SOURCE
            or not isinstance(url, str)
            or not url
            or not isinstance(content_hash, str)
            or len(content_hash) != 64
            or any(character not in _LOWER_HEX for character in content_hash)
        ):
            raise HeldoutPreparationError("materialization all-candidate identity drifted")
        all_ids.append(identifier)
        all_by_id[identifier] = row
        source_counts[cast(str, source)] += 1
    if (
        all_ids != sorted(all_ids)
        or len(all_ids) != len(set(all_ids))
        or set(all_ids).intersection(binding.retired_ids)
        or dict(sorted(source_counts.items())) != dict(sorted(EXPECTED_BY_SOURCE.items()))
    ):
        raise HeldoutPreparationError("materialization raw source composition drifted")
    for candidate in candidates:
        identifier = cast(int, candidate["news_item_id"])
        identity = all_by_id.get(identifier)
        if identity is None or any(
            identity.get(field) != candidate.get(field)
            for field in ("source", "url", "content_hash")
        ):
            raise HeldoutPreparationError(
                "materialization eligible identity differs from all-candidate layer"
            )

    eligibility_document = _mapping(
        yaml.safe_load(
            (binding.root / "config/p4_event_evaluation_v1_7.yaml").read_bytes()
        ),
        "v1.7 eligibility design",
    )
    eligibility_policy = _mapping(
        eligibility_document.get("candidate_eligibility"),
        "v1.7 candidate eligibility",
    )
    minimum_characters = eligibility_policy.get("minimum_extracted_characters")
    maximum_pdf_bytes = eligibility_policy.get("max_pdf_bytes")
    if (
        isinstance(minimum_characters, bool)
        or not isinstance(minimum_characters, int)
        or minimum_characters <= 0
        or isinstance(maximum_pdf_bytes, bool)
        or not isinstance(maximum_pdf_bytes, int)
        or maximum_pdf_bytes <= 0
    ):
        raise HeldoutPreparationError("frozen PDF eligibility gates drifted")
    ineligible_ids: list[int] = []
    reason_counts: Counter[str] = Counter()
    for index, raw in enumerate(ineligible_rows, 1):
        row = _mapping(raw, f"materialization ineligible candidate {index}")
        if set(row) != {
            "news_item_id",
            "url",
            "reason",
            "measured_value",
            "gate_value",
            "pdf_sha256",
        }:
            raise HeldoutPreparationError("materialization ineligible schema drifted")
        identifier = _positive_news_item_id(
            row, f"materialization ineligible candidate {index}"
        )
        reason = row.get("reason")
        measured = row.get("measured_value")
        gate = row.get("gate_value")
        pdf_sha = row.get("pdf_sha256")
        identity = all_by_id.get(identifier)
        url_value = row.get("url")
        parsed_url = urlparse(url_value) if isinstance(url_value, str) else None
        if (
            reason not in {"pdf_text_below_min_char_gate", "pdf_exceeds_size_bound"}
            or isinstance(measured, bool)
            or not isinstance(measured, int)
            or measured < 0
            or isinstance(gate, bool)
            or not isinstance(gate, int)
            or gate <= 0
            or identity is None
            or identity.get("source") != "cninfo"
            or url_value != identity.get("url")
            or parsed_url is None
            or parsed_url.scheme != "https"
            or parsed_url.hostname != "static.cninfo.com.cn"
            or not parsed_url.path.casefold().endswith(".pdf")
            or (
                pdf_sha is not None
                and (
                    not isinstance(pdf_sha, str)
                    or len(pdf_sha) != 64
                    or any(character not in _LOWER_HEX for character in pdf_sha)
                )
            )
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
            raise HeldoutPreparationError("materialization ineligible evidence drifted")
        ineligible_ids.append(identifier)
        reason_counts[cast(str, reason)] += 1
    if (
        len(ineligible_ids) != len(set(ineligible_ids))
        or set(candidate_ids).intersection(ineligible_ids)
        or set(all_ids) != set(candidate_ids).union(ineligible_ids)
        or counts.get("ineligible_by_reason") != dict(sorted(reason_counts.items()))
    ):
        raise HeldoutPreparationError("materialization layer coverage drifted")


def _aware_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise HeldoutPreparationError(f"{label} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HeldoutPreparationError(
            f"{label} must be a timezone-aware timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HeldoutPreparationError(f"{label} must be a timezone-aware timestamp")
    return parsed


def _validate_snapshot(value: object, label: str) -> JsonObject:
    snapshot = _mapping(value, label)
    if set(snapshot) != _SNAPSHOT_FIELDS:
        raise HeldoutPreparationError(f"{label} schema drifted")
    for field in (
        "pragma_query_only",
        "connection_total_changes",
        "llm_call_count",
        "trade_proposal_count",
        "broker_order_count",
        "non_simulate_order_count",
        "universe_symbol_count",
    ):
        item = snapshot.get(field)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise HeldoutPreparationError(f"{label}.{field} is invalid")
    maximum_id = snapshot.get("llm_call_max_id")
    if maximum_id is not None and (
        isinstance(maximum_id, bool) or not isinstance(maximum_id, int) or maximum_id <= 0
    ):
        raise HeldoutPreparationError(f"{label}.llm_call_max_id is invalid")
    if (
        snapshot.get("sqlite_uri_mode") != "ro"
        or snapshot.get("pragma_query_only") != 1
        or snapshot.get("connection_total_changes") != 0
        or not isinstance(snapshot.get("news_events_table_exists"), bool)
        or snapshot.get("non_simulate_order_count") != 0
    ):
        raise HeldoutPreparationError(f"{label} does not prove read-only safety")
    return snapshot


def _validate_candidate_prediction_rows(
    binding: HeldoutBinding,
    candidates: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
) -> None:
    """Revalidate every inference input/output, including unselected rows."""

    if len(candidates) != len(predictions):
        raise HeldoutPreparationError("candidate/prediction coverage drifted")
    _validate_candidate_input_hashes(candidates, binding.contract)
    universe_symbols: set[str] = set()
    for candidate in candidates:
        symbol = candidate.get("ingested_symbol")
        if isinstance(symbol, str):
            universe_symbols.add(symbol)
        original_text = candidate.get("original_text")
        if isinstance(original_text, str):
            universe_symbols.update(_SIX_DIGIT_SYMBOL_RE.findall(original_text))
    for index, (candidate, prediction) in enumerate(
        zip(candidates, predictions, strict=True), 1
    ):
        if set(prediction) != common.PREDICTION_KEYS:
            raise HeldoutPreparationError(f"prediction row {index} schema drifted")
        try:
            prepared = offline_extract._prepare_records(
                binding.contract,
                [_extract_record(candidate)],
            )[0]
            offline_extract._validate_resumed_success(
                prediction,
                prepared,
                binding.contract,
                universe_symbols,
            )
        except (
            offline_extract.OfflineExtractError,
            EventExtractValidationError,
        ) as exc:
            raise HeldoutPreparationError(
                f"prediction row {index} contract/security/result drifted"
            ) from exc


def _validate_completed_inference(
    binding: HeldoutBinding,
    *,
    states: Sequence[Mapping[str, Any]],
    prediction_manifest: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    materialization_sha256: str,
    predictions_sha256: str,
    prediction_manifest_sha256: str,
) -> SelectionExecutionBinding:
    if len(states) != 2:
        raise HeldoutPreparationError("inference state must contain exactly two events")
    started, completed = states
    started_fields = {
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
    }
    completed_fields = {
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
    }
    manifest_fields = {
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
    }
    if (
        set(started) != started_fields
        or set(completed) != completed_fields
        or set(prediction_manifest) != manifest_fields
    ):
        raise HeldoutPreparationError("inference state or prediction manifest schema drifted")
    execution_id = started.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        raise HeldoutPreparationError("inference execution id is invalid")
    try:
        uuid.UUID(execution_id)
    except ValueError as exc:
        raise HeldoutPreparationError("inference execution id is invalid") from exc
    started_at = _aware_datetime(started.get("started_at_utc"), "inference start")
    completed_at = _aware_datetime(completed.get("completed_at_utc"), "inference completion")
    preregistered_at = _aware_datetime(
        binding.preregistration.get("created_at_utc"),
        "held-out preregistration created_at_utc",
    )
    source_window_end = _aware_datetime(WINDOW_END_UTC, "held-out source window end")
    if started_at < preregistered_at or started_at < source_window_end:
        raise HeldoutPreparationError(
            "inference start precedes preregistration or source-window closure"
        )
    if completed_at < started_at:
        raise HeldoutPreparationError("inference completion precedes its start")
    if any(row.get("status") != "ok" for row in predictions):
        raise HeldoutPreparationError("not every eligible prediction has status ok")
    _validate_candidate_prediction_rows(binding, candidates, predictions)
    previous_recorded_at = started_at
    for index, prediction in enumerate(predictions, 1):
        recorded_at = _aware_datetime(
            prediction.get("recorded_at_utc"),
            f"prediction row {index} recorded_at",
        )
        if (
            recorded_at < started_at
            or recorded_at > completed_at
            or recorded_at < previous_recorded_at
        ):
            raise HeldoutPreparationError(
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
        prediction_manifest.get("production_snapshot_before"),
        "manifest snapshot before",
    )
    manifest_after = _validate_snapshot(
        prediction_manifest.get("production_snapshot_after"),
        "manifest snapshot after",
    )
    for value, label in (
        (started.get("eligible_candidate_count"), "eligible_candidate_count"),
        (started.get("automatic_retries"), "started automatic_retries"),
        (started.get("failed_candidate_retries"), "started failed_candidate_retries"),
        (completed.get("prediction_count"), "completed prediction_count"),
        (prediction_manifest.get("candidate_count"), "manifest candidate_count"),
        (prediction_manifest.get("prediction_count"), "manifest prediction_count"),
        (prediction_manifest.get("status_ok_count"), "manifest status_ok_count"),
        (prediction_manifest.get("status_failed_count"), "manifest status_failed_count"),
        (prediction_manifest.get("automatic_retries"), "manifest automatic_retries"),
        (
            prediction_manifest.get("failed_candidate_retries"),
            "manifest failed_candidate_retries",
        ),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HeldoutPreparationError(f"{label} is invalid")
    predictions_ref = _mapping(
        prediction_manifest.get("predictions"), "prediction manifest predictions"
    )
    if (
        started.get("schema_version") != "p4.2a-v2-heldout-inference-state-v1"
        or started.get("status") != "inference_started"
        or started.get("eligible_candidate_count") != len(candidates)
        or started.get("candidate_order") != "ascending_news_item_id"
        or started.get("preregistration_sha256") != PREREGISTRATION_SHA256
        or started.get("materialization_manifest_sha256") != materialization_sha256
        or started.get("contract_sha256") != HELDOUT_CONTRACT_SHA256
        or started.get("model") != MODEL
        or started.get("automatic_retries") != 0
        or started.get("failed_candidate_retries") != 0
        or started.get("settings_safety") != _INFERENCE_SETTINGS_SAFETY
        or completed.get("schema_version") != "p4.2a-v2-heldout-inference-state-v1"
        or completed.get("status") != "completed_all_eligible_candidates_once"
        or completed.get("execution_id") != execution_id
        or completed.get("preregistration_sha256") != PREREGISTRATION_SHA256
        or completed.get("materialization_manifest_sha256") != materialization_sha256
        or completed.get("prediction_count") != len(predictions)
        or completed.get("predictions_sha256") != predictions_sha256
        or completed.get("prediction_manifest_sha256") != prediction_manifest_sha256
        or completed.get("production_snapshot_unchanged") is not True
        or completed.get("production_snapshot_before")
        != before
        or completed_before != before
        or after != before
        or prediction_manifest.get("schema_version")
        != "p4.2a-v2-heldout-prediction-manifest-v1"
        or prediction_manifest.get("frame_id") != FRAME_ID
        or prediction_manifest.get("execution_id") != execution_id
        or prediction_manifest.get("preregistration_sha256") != PREREGISTRATION_SHA256
        or prediction_manifest.get("materialization_manifest_sha256")
        != materialization_sha256
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
        or prediction_manifest.get("production_snapshot_before")
        != started.get("production_snapshot_before")
        or prediction_manifest.get("production_snapshot_after")
        != completed.get("production_snapshot_after")
        or prediction_manifest.get("settings_safety") != started.get("settings_safety")
        or manifest_before != before
        or manifest_after != after
        or predictions_ref
        != {
            "path": _relative_artifact_path(binding, "predictions"),
            "sha256": predictions_sha256,
        }
    ):
        raise HeldoutPreparationError("completed inference hash/count lineage drifted")
    candidate_ids = [
        _positive_news_item_id(row, f"candidate row {index}")
        for index, row in enumerate(candidates, start=1)
    ]
    prediction_ids = [
        _positive_news_item_id(row, f"prediction row {index}")
        for index, row in enumerate(predictions, start=1)
    ]
    if candidate_ids != sorted(candidate_ids) or len(candidate_ids) != len(set(candidate_ids)):
        raise HeldoutPreparationError("eligible candidate IDs are not unique ascending IDs")
    if prediction_ids != candidate_ids:
        raise HeldoutPreparationError(
            "prediction order and IDs do not exactly match eligible candidates"
        )
    return SelectionExecutionBinding(
        materialization_manifest_sha256=materialization_sha256,
        inference_state_sha256=common.sha256_file(binding.artifacts["inference_state"]),
        prediction_manifest_sha256=prediction_manifest_sha256,
        execution_id=execution_id,
        eligible_candidate_count=len(candidates),
        prediction_count=len(predictions),
        status_ok_count=len(predictions),
        status_failed_count=0,
        started_at_utc=cast(str, started["started_at_utc"]),
        completed_at_utc=cast(str, completed["completed_at_utc"]),
    )


def _selection_execution_binding_from_artifacts(
    binding: HeldoutBinding,
    candidates: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
) -> SelectionExecutionBinding:
    for name in (
        "materialized_inputs",
        "materialization_manifest",
        "inference_state",
        "predictions",
        "prediction_manifest",
    ):
        _require_regular_artifact(binding.artifacts[name], name.replace("_", " "))
    inputs_payload = common.canonical_jsonl_bytes(candidates)
    predictions_payload = common.canonical_jsonl_bytes(predictions)
    if binding.artifacts["materialized_inputs"].read_bytes() != inputs_payload:
        raise HeldoutPreparationError("materialized inputs are not canonical JSONL bytes")
    if binding.artifacts["predictions"].read_bytes() != predictions_payload:
        raise HeldoutPreparationError("predictions are not canonical JSONL bytes")
    materialization_manifest = _load_json(
        binding.artifacts["materialization_manifest"], "materialization manifest"
    )
    states = _load_jsonl(binding.artifacts["inference_state"], "inference state")
    prediction_manifest = _load_json(
        binding.artifacts["prediction_manifest"], "prediction manifest"
    )
    inputs_sha256 = common.sha256_bytes(inputs_payload)
    predictions_sha256 = common.sha256_bytes(predictions_payload)
    materialization_sha256 = common.sha256_file(binding.artifacts["materialization_manifest"])
    prediction_manifest_sha256 = common.sha256_file(binding.artifacts["prediction_manifest"])
    _validate_materialization_for_selection(
        binding,
        materialization_manifest,
        candidates,
        inputs_sha256=inputs_sha256,
    )
    return _validate_completed_inference(
        binding,
        states=states,
        prediction_manifest=prediction_manifest,
        candidates=candidates,
        predictions=predictions,
        materialization_sha256=materialization_sha256,
        predictions_sha256=predictions_sha256,
        prediction_manifest_sha256=prediction_manifest_sha256,
    )


def run_select_blind(binding: HeldoutBinding) -> tuple[Path, Path]:
    source_names = (
        "materialized_inputs",
        "materialization_manifest",
        "inference_state",
        "predictions",
    )
    for name in source_names:
        _require_regular_artifact(binding.artifacts[name], name.replace("_", " "))
    states = _load_jsonl(binding.artifacts["inference_state"], "inference state")
    if (
        len(states) != 2
        or states[0].get("status") != "inference_started"
        or states[1].get("status") != "completed_all_eligible_candidates_once"
    ):
        raise HeldoutPreparationError("inference is not in the completed terminal state")
    _require_regular_artifact(
        binding.artifacts["prediction_manifest"], "prediction manifest"
    )
    candidates = _load_jsonl(binding.artifacts["materialized_inputs"], "held-out inputs")
    materialization_manifest = _load_json(
        binding.artifacts["materialization_manifest"], "materialization manifest"
    )
    predictions = _load_jsonl(binding.artifacts["predictions"], "held-out predictions")
    prediction_manifest = _load_json(
        binding.artifacts["prediction_manifest"], "prediction manifest"
    )
    inputs_payload = common.canonical_jsonl_bytes(candidates)
    predictions_payload = common.canonical_jsonl_bytes(predictions)
    if binding.artifacts["materialized_inputs"].read_bytes() != inputs_payload:
        raise HeldoutPreparationError("materialized inputs are not canonical JSONL bytes")
    if binding.artifacts["predictions"].read_bytes() != predictions_payload:
        raise HeldoutPreparationError("predictions are not canonical JSONL bytes")
    inputs_sha256 = common.sha256_bytes(inputs_payload)
    predictions_sha256 = common.sha256_bytes(predictions_payload)
    materialization_sha256 = common.sha256_file(binding.artifacts["materialization_manifest"])
    prediction_manifest_sha256 = common.sha256_file(binding.artifacts["prediction_manifest"])
    _validate_materialization_for_selection(
        binding,
        materialization_manifest,
        candidates,
        inputs_sha256=inputs_sha256,
    )
    execution_binding = _validate_completed_inference(
        binding,
        states=states,
        prediction_manifest=prediction_manifest,
        candidates=candidates,
        predictions=predictions,
        materialization_sha256=materialization_sha256,
        predictions_sha256=predictions_sha256,
        prediction_manifest_sha256=prediction_manifest_sha256,
    )
    result = select_and_blind(
        binding,
        candidates,
        predictions,
        execution_binding=execution_binding,
    )
    _publish_create_only(
        (
            (binding.artifacts["private_selection"], common.canonical_json_bytes(result.manifest)),
            (binding.artifacts["owner_blind"], common.canonical_jsonl_bytes(result.blind_rows)),
        )
    )
    return binding.artifacts["private_selection"], binding.artifacts["owner_blind"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare the frozen P4.2a v2 held-out frame")
    parser.add_argument(
        "stage", choices=("validate", "synthetic-rehearsal", "materialize", "infer", "select-blind")
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> NoReturn:
    args = _parser().parse_args(argv)
    binding = load_binding(args.project_root)
    if args.stage == "validate":
        result: object = {"status": "valid", "preregistration_sha256": PREREGISTRATION_SHA256}
    elif args.stage == "synthetic-rehearsal":
        result = run_synthetic_rehearsal(binding).as_posix()
    elif args.stage == "materialize":
        result = [path.as_posix() for path in run_materialize(binding)]
    elif args.stage == "infer":
        result = [path.as_posix() for path in run_infer(binding)]
    else:
        result = [path.as_posix() for path in run_select_blind(binding)]
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
