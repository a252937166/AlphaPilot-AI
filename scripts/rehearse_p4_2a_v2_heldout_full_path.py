#!/usr/bin/env python3
"""Run the P4.2a v2 held-out full path with synthetic, offline-only data.

All working artifacts live in a disposable directory outside the repository.
Only after every stage passes are the four registered rehearsal artifacts
published create-only.  The command never opens the production database,
never calls a network or model provider, and never computes real held-out
metrics.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import build_p4_2a_gold_sample as materializer  # noqa: E402
from scripts import build_p4_2a_v2_heldout_adjudication_ui as heldout_ui  # noqa: E402
from scripts import evaluate_p4_2a_v2_heldout as evaluator  # noqa: E402
from scripts import finalize_p4_2a_v2_heldout_adjudication as heldout_finalizer  # noqa: E402
from scripts import prepare_p4_2a_v2_heldout as prepare  # noqa: E402
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
SYNTHETIC_COUNT = 80
SYNTHETIC_POSITIVE_COUNT = 50
SYNTHETIC_NEGATIVE_COUNT = 30
SELECTED_POSITIVE_COUNT = 40
SELECTED_NEGATIVE_COUNT = 20
OWNER_CHAIN_COUNT = 60
SYNTHETIC_ID_START = 900_001
SYNTHETIC_DRAFTER = heldout_seal.EXPECTED_DRAFTER_ID
SYNTHETIC_ADJUDICATOR = "ouyang"
SYNTHETIC_DRAFTED_AT = "2026-08-10T08:00:00Z"
SYNTHETIC_ADJUDICATED_AT = "2026-08-10T08:05:00Z"
SYNTHETIC_COMPLETED_AT = "2026-08-10T08:10:00Z"

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
        "report_path": str(paths.report),
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


def _execute_temp_pipeline(
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="run the offline synthetic rehearsal and publish the registered create-only receipt",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.execute:
        print("ERROR: --execute is required", file=sys.stderr)
        return 2
    try:
        receipt = run_rehearsal()
    except (RehearsalError, FileExistsError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "passed",
                "receipt": str(receipt.relative_to(PROJECT_ROOT)),
                "real_model_calls": 0,
                "production_writes": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
