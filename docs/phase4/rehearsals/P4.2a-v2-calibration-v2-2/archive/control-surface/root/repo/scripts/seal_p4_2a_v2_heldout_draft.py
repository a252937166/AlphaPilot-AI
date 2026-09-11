#!/usr/bin/env python3
"""Create-only seal the P4.2a v2 heldout60 non-evaluated AI draft.

This command is deliberately offline.  It validates the registered heldout
selection/blind bundle, accepts a candidate JSONL drafted by OpenAI Codex GPT-5,
and publishes only the sealed draft artifact.  It never calls an evaluated
model and never creates owner or human-gold artifacts.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import p4_2a_v2_dev_common as common  # noqa: E402
from scripts import prepare_p4_2a_v2_heldout as prepare  # noqa: E402
from scripts import seal_p4_2a_v2_ai_draft as base  # noqa: E402

from alphapilot.llm.p4_news_eval import (  # noqa: E402
    EVALUATION_DESIGN_V2_PATH,
    EventEvaluationDesignError,
    load_event_evaluation_design,
)

DESIGN_RELATIVE_PATH = "config/p4_event_evaluation_v2.yaml"
EXPECTED_DESIGN_SHA256 = "18a2428a4ec04bfea6e4f4d70692f38ea82fbaee5a223f30f2465b895b238e21"
PREREGISTRATION_RELATIVE_PATH = (
    "docs/phase4/reports/P4.2a-v2-heldout-preregistration-20260810.json"
)
EXPECTED_PREREGISTRATION_SHA256 = (
    "ccecbf5ca7b48b16e445318b8c94a08927432f92c7e8c12f8ab40f2916578705"
)
HELDOUT_CONTRACT_RELATIVE_PATH = "config/p4_event_extract_eval_v2-heldout-qwen3.6-plus.yaml"
EXPECTED_HELDOUT_CONTRACT_SHA256 = (
    "26be1765204b122908e7bd09cac857c33bd3140233df47dc3358bc590e020199"
)
FRAME_ID = "p4.2a-heldout-frame-v2"
EXPECTED_COUNT = 60
EXPECTED_DRAFTER_ID = "OpenAI Codex GPT-5"
EVALUATED_MODEL = "qwen3.6-plus"
SELECTION_MANIFEST_SCHEMA = "p4.2a-v2-heldout-selection-manifest-v1"
BLIND_SCHEMA = "p4.2a-v2-heldout-owner-blind-item-v1"
HELDOUT_SELECTION_FIELDS = frozenset(
    {"algorithm", "seed", "without_replacement", "selected_counts", "selected"}
)
EXPECTED_COUNTS = {
    "predicted_positive": 40,
    "predicted_negative": 20,
    "extract_failed": 0,
    "total": EXPECTED_COUNT,
}

_ARTIFACT_ALIASES = {
    "development_private_selection_manifest": "heldout_private_selection_manifest",
    "development_owner_blind_jsonl": "heldout_owner_blind_jsonl",
    "development_ai_draft_jsonl": "heldout_ai_draft_jsonl",
    "development_adjudication_html": "heldout_adjudication_html",
    "development_owner_raw_export_jsonl": "heldout_owner_raw_export_jsonl",
    "development_human_adjudicated_jsonl": "heldout_human_adjudicated_jsonl",
    "development_owner_completion_manifest": "heldout_owner_completion_manifest",
}


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise base.V2AdjudicationError(f"{label} must be a mapping")
    return value


def validate_stage_authority(
    project_root: Path,
    *,
    stage: str,
    execution_context: prepare._OfflineRehearsalCapability | None = None,
) -> prepare.HeldoutBinding:
    """Revalidate the successor authority before any held-out input is read."""

    try:
        binding = prepare.load_binding(project_root)
        prepare.validate_v2_1_stage_authorization(
            binding,
            stage=stage,
            execution_context=execution_context,
        )
        return binding
    except prepare.HeldoutPreparationError as exc:
        raise base.V2AdjudicationError(
            f"heldout {stage} remains authority-gated: {exc}"
        ) from exc


def prevalidate_stage_authority(
    project_root: Path,
    *,
    stage: str,
    execution_context: prepare._OfflineRehearsalCapability | None = None,
    prevalidated_authority: prepare._PrevalidatedStageAuthority | None = None,
    validated_stage: str | None = None,
) -> tuple[prepare.HeldoutBinding, prepare._PrevalidatedStageAuthority]:
    """Mint once at stage entry or consume the same identity-bound stage token."""

    try:
        binding = prepare.load_binding(project_root)
        if prevalidated_authority is None:
            if validated_stage is not None:
                raise prepare.HeldoutPreparationError(
                    "validated_stage requires prevalidated authority"
                )
            delegated = prepare._prevalidate_v2_1_stage_authorization(
                binding,
                stage=stage,
                execution_context=execution_context,
            )
        else:
            if execution_context is not None or validated_stage != stage:
                raise prepare.HeldoutPreparationError(
                    "prevalidated consumer authority is ambiguous or cross-stage"
                )
            prepare._consume_prevalidated_v2_1_stage_authorization(
                binding,
                prevalidated_authority,
                validated_stage,
            )
            delegated = prevalidated_authority
        return binding, delegated
    except prepare.HeldoutPreparationError as exc:
        raise base.V2AdjudicationError(
            f"heldout {stage} remains authority-gated: {exc}"
        ) from exc


def _verify_preregistration(project_root: Path, design_sha256: str) -> None:
    path = (project_root / PREREGISTRATION_RELATIVE_PATH).resolve()
    preregistration, payload = base.read_json_object(path, label="heldout preregistration")
    if base.sha256_bytes(payload) != EXPECTED_PREREGISTRATION_SHA256:
        raise base.V2AdjudicationError("heldout preregistration SHA-256 drifted")
    design = _mapping(preregistration.get("design"), label="preregistration design")
    selected = _mapping(
        preregistration.get("selected_extractor"), label="selected extractor"
    )
    execution = _mapping(
        selected.get("heldout_execution_contract"), label="heldout execution contract"
    )
    request = _mapping(preregistration.get("request_contract"), label="request contract")
    owner = _mapping(
        preregistration.get("owner_blindness_and_gold"), label="owner blindness"
    )
    if (
        design.get("path") != DESIGN_RELATIVE_PATH
        or design.get("sha256") != design_sha256
        or selected.get("model") != EVALUATED_MODEL
        or execution.get("path") != HELDOUT_CONTRACT_RELATIVE_PATH
        or execution.get("sha256") != EXPECTED_HELDOUT_CONTRACT_SHA256
        or request.get("one_news_item_per_request") is not True
        or request.get("one_request_per_eligible_candidate") is not True
        or request.get("multi_item_prompt_forbidden") is not True
        or request.get("prefilter_forbidden") is not True
        or request.get("additional_body_shortening_for_cost_forbidden") is not True
        or owner.get("draft_annotator") != EXPECTED_DRAFTER_ID
        or owner.get("draft_annotator_is_evaluated_model") is not False
        or owner.get("gold_is_final_only_after_60_of_60_human_confirmation") is not True
    ):
        raise base.V2AdjudicationError("heldout preregistration semantics drifted")


def load_registered_contract(
    design_path: Path = EVALUATION_DESIGN_V2_PATH,
    *,
    project_root: Path = PROJECT_ROOT,
) -> base.V2AdjudicationContract:
    """Load only the frozen heldout60 surface of the registered v2 design."""

    root = project_root.resolve()
    expected_path = (root / DESIGN_RELATIVE_PATH).resolve()
    candidate = design_path.expanduser()
    if candidate.is_symlink() or candidate.resolve() != expected_path:
        raise base.V2AdjudicationError("only the registered P4.2a v2 design may be used")
    design = load_event_evaluation_design(expected_path, project_root=root)
    if design.sha256 != EXPECTED_DESIGN_SHA256:
        raise base.V2AdjudicationError("P4.2a v2 evaluation design SHA-256 drifted")

    document = design.document
    frames = _mapping(document.get("frames"), label="v2 frames")
    frame = _mapping(frames.get("heldout_frame_v2"), label="heldout frame")
    strata = _mapping(frame.get("strata"), label="heldout strata")
    annotation = _mapping(frame.get("annotation"), label="heldout annotation")
    if (
        document.get("schema_version") != base.DESIGN_SCHEMA
        or frame.get("frame_id") != FRAME_ID
        or frame.get("total_selected_count") != EXPECTED_COUNT
        or _mapping(strata.get("predicted_positive"), label="positive stratum").get(
            "selected_count"
        )
        != 40
        or _mapping(strata.get("predicted_negative"), label="negative stratum").get(
            "selected_count"
        )
        != 20
        or _mapping(strata.get("extract_failed"), label="failed stratum").get(
            "selected_count"
        )
        != 0
        or annotation.get("type") != "ai_drafted_human_adjudicated"
        or annotation.get("drafting_ai_must_not_be_evaluated_model") is not True
        or annotation.get("adjudicator_role") != "owner_human"
        or annotation.get("gold_is_final_only_after_human_adjudication") is not True
        or frame.get("prompt_iteration_allowed") is not False
    ):
        raise base.V2AdjudicationError("heldout60 annotation/frame contract drifted")

    raw_artifacts = _mapping(document.get("artifacts"), label="v2 artifacts")
    artifacts: dict[str, Path] = {}
    for generic_name, registered_name in _ARTIFACT_ALIASES.items():
        entry = _mapping(raw_artifacts.get(registered_name), label=registered_name)
        if (
            entry.get("create_only") is not True
            or entry.get("locked_until_development_review") is not True
        ):
            raise base.V2AdjudicationError(
                f"registered heldout artifact {registered_name} is not locked create-only"
            )
        artifacts[generic_name] = base._resolved_artifact_path(
            root,
            entry.get("path"),
            label=f"artifacts.{registered_name}.path",
        )
    if len(set(artifacts.values())) != len(artifacts):
        raise base.V2AdjudicationError("registered heldout artifact paths overlap")

    taxonomy = _mapping(design.base_contract.document.get("taxonomy"), label="base taxonomy")
    taxonomy_values = taxonomy.get("values")
    if (
        not isinstance(taxonomy_values, list)
        or not taxonomy_values
        or any(not isinstance(item, str) or not item for item in taxonomy_values)
    ):
        raise base.V2AdjudicationError("base taxonomy values are invalid")

    execution_path = (root / HELDOUT_CONTRACT_RELATIVE_PATH).resolve()
    if execution_path.is_symlink() or not execution_path.is_file():
        raise base.V2AdjudicationError("heldout execution contract is unavailable")
    if base.sha256_bytes(execution_path.read_bytes()) != EXPECTED_HELDOUT_CONTRACT_SHA256:
        raise base.V2AdjudicationError("heldout execution contract SHA-256 drifted")
    _verify_preregistration(root, design.sha256)

    return base.V2AdjudicationContract(
        project_root=root,
        design_path=expected_path,
        design_ref={"path": DESIGN_RELATIVE_PATH, "sha256": design.sha256},
        frame_id=FRAME_ID,
        expected_count=EXPECTED_COUNT,
        taxonomy=frozenset(taxonomy_values),
        artifacts=artifacts,
        blind_schema=BLIND_SCHEMA,
    )


def validate_selection_manifest_binding(
    manifest: Mapping[str, Any],
    blind_rows: Sequence[Mapping[str, Any]],
    blind_payload: bytes,
    *,
    contract: base.V2AdjudicationContract,
) -> dict[str, Any]:
    """Bind a heldout60 blind delivery to its private 40/20 selection."""

    blind = base.validate_blind_rows(blind_rows, contract=contract)
    normalized = dict(manifest)
    if set(normalized) != base.SELECTION_MANIFEST_FIELDS:
        raise base.V2AdjudicationError("heldout private selection manifest fields drifted")
    if (
        normalized.get("schema_version") != SELECTION_MANIFEST_SCHEMA
        or normalized.get("design") != contract.design_ref
        or normalized.get("frame_id") != contract.frame_id
        or normalized.get("production_writes") is not False
    ):
        raise base.V2AdjudicationError("heldout private selection manifest identity drifted")

    owner = normalized.get("owner_delivery")
    if not isinstance(owner, Mapping) or set(owner) != base.OWNER_DELIVERY_FIELDS:
        raise base.V2AdjudicationError("heldout owner_delivery fields drifted")
    expected_blind_path = str(
        contract.artifacts["development_owner_blind_jsonl"].relative_to(
            contract.project_root
        )
    )
    if (
        owner.get("path") != expected_blind_path
        or owner.get("sha256") != base.sha256_bytes(blind_payload)
        or owner.get("row_count") != EXPECTED_COUNT
        or owner.get("sampling_stratum_visible") is not False
        or owner.get("prediction_visible") is not False
        or owner.get("selection_rank_visible") is not False
        or owner.get("gold_state")
        != "empty_object_pending_ai_draft_and_human_adjudication"
    ):
        raise base.V2AdjudicationError("heldout selection does not bind exact blind bytes")

    selection = normalized.get("selection")
    if not isinstance(selection, Mapping) or set(selection) != HELDOUT_SELECTION_FIELDS:
        raise base.V2AdjudicationError("heldout selection fields drifted")
    selected = selection.get("selected")
    if (
        selection.get("algorithm") != "sha256_rank_without_replacement_per_stratum_v1"
        or selection.get("selected_counts") != EXPECTED_COUNTS
        or selection.get("without_replacement") is not True
        or not isinstance(selected, list)
        or len(selected) != EXPECTED_COUNT
    ):
        raise base.V2AdjudicationError("heldout selection counts/order contract drifted")

    observed = {"predicted_positive": 0, "predicted_negative": 0}
    seen_ids: set[int] = set()
    for index, (raw_selected, blind_row) in enumerate(
        zip(selected, blind, strict=True), 1
    ):
        if not isinstance(raw_selected, Mapping) or set(raw_selected) != base.SELECTED_ITEM_FIELDS:
            raise base.V2AdjudicationError(f"heldout private selection row {index} fields drifted")
        row = dict(raw_selected)
        item_id = base._positive_int(
            row.get("news_item_id"), label=f"heldout selection row {index} news_item_id"
        )
        if item_id in seen_ids:
            raise base.V2AdjudicationError(f"heldout selection duplicates news_item_id={item_id}")
        seen_ids.add(item_id)
        stratum = row.get("sampling_stratum")
        if not isinstance(stratum, str) or stratum not in observed:
            raise base.V2AdjudicationError(f"heldout selection row {index} has invalid stratum")
        observed[stratum] += 1
        if (
            row.get("sample_index") != index
            or item_id != blind_row["news_item_id"]
            or row.get("input_sha256") != blind_row["input_sha256"]
            or row.get("source") != blind_row["source"]
            or row.get("text_sha256") != blind_row["text_sha256"]
            or row.get("model") != EVALUATED_MODEL
            or row.get("contract_sha256") != EXPECTED_HELDOUT_CONTRACT_SHA256
        ):
            raise base.V2AdjudicationError(
                "heldout selection IDs/order/input/model bindings do not match blind JSONL"
            )
        for field in (
            "selection_rank_sha256",
            "owner_order_sha256",
            "input_sha256",
            "declared_input_sha256",
            "text_sha256",
            "contract_sha256",
        ):
            if not base._is_sha256(row.get(field)):
                raise base.V2AdjudicationError(
                    f"heldout selection row {index} {field} is invalid"
                )
    if observed != {"predicted_positive": 40, "predicted_negative": 20}:
        raise base.V2AdjudicationError("heldout observed stratum counts drifted")
    return normalized


def read_bound_blind_bundle(
    selection_manifest_path: Path,
    blind_path: Path,
    *,
    contract: base.V2AdjudicationContract,
    execution_context: prepare._OfflineRehearsalCapability | None = None,
    stage: str = "seal-draft",
    prevalidated_authority: prepare._PrevalidatedStageAuthority | None = None,
    validated_stage: str | None = None,
) -> tuple[list[dict[str, Any]], bytes, bytes, str]:
    """Re-derive and bind the complete producer chain before owner access.

    The private manifest is not trusted merely because its hashes are shaped
    correctly.  We revalidate materialization and inference, deterministically
    recompute selection/ranks/owner ordering, and require exact canonical bytes
    for both the private manifest and the blind delivery.
    """

    try:
        binding, delegated = prevalidate_stage_authority(
            contract.project_root,
            stage=stage,
            execution_context=execution_context,
            prevalidated_authority=prevalidated_authority,
            validated_stage=validated_stage,
        )
        if binding.root != contract.project_root.resolve():
            raise prepare.HeldoutPreparationError("producer project root drifted")
        expected_paths = {
            "private_selection": contract.artifacts[
                "development_private_selection_manifest"
            ],
            "owner_blind": contract.artifacts["development_owner_blind_jsonl"],
        }
        for name, expected in expected_paths.items():
            if binding.artifacts[name].resolve() != expected.resolve():
                raise prepare.HeldoutPreparationError(
                    f"producer/owner artifact path drifted: {name}"
                )
        if (
            selection_manifest_path.resolve()
            != binding.artifacts["private_selection"].resolve()
            or blind_path.resolve() != binding.artifacts["owner_blind"].resolve()
        ):
            raise prepare.HeldoutPreparationError(
                "owner inputs are not the registered producer outputs"
            )

        candidates = prepare._load_jsonl(
            binding.artifacts["materialized_inputs"], "held-out materialized inputs"
        )
        predictions = prepare._load_jsonl(
            binding.artifacts["predictions"], "held-out predictions"
        )
        execution = prepare._selection_execution_binding_from_artifacts(
            binding,
            candidates,
            predictions,
            prevalidated_authority=delegated,
            validated_stage=stage,
        )
        recomputed = prepare.select_and_blind(
            binding,
            candidates,
            predictions,
            execution_binding=execution,
        )
        source_lineage = recomputed.manifest.get("source_lineage")
        if (
            not isinstance(source_lineage, Mapping)
            or source_lineage.get("binding_scope") != "registered_full_execution"
        ):
            raise prepare.HeldoutPreparationError(
                "owner chain requires registered full-execution lineage"
            )

        expected_manifest_payload = common.canonical_json_bytes(recomputed.manifest)
        expected_blind_payload = common.canonical_jsonl_bytes(recomputed.blind_rows)
        manifest, manifest_payload = base.read_json_object(
            selection_manifest_path, label="heldout private selection manifest"
        )
        blind_rows, blind_payload = base.read_jsonl(blind_path, label="heldout blind")
        if manifest_payload != expected_manifest_payload:
            raise prepare.HeldoutPreparationError(
                "private selection bytes differ from deterministic producer re-derivation"
            )
        if blind_payload != expected_blind_payload:
            raise prepare.HeldoutPreparationError(
                "owner blind bytes differ from deterministic producer re-derivation"
            )
        validate_selection_manifest_binding(
            manifest, blind_rows, blind_payload, contract=contract
        )

        lineage = source_lineage
        current_hashes = {
            "materialized_inputs": common.sha256_file(
                binding.artifacts["materialized_inputs"]
            ),
            "materialization_manifest": common.sha256_file(
                binding.artifacts["materialization_manifest"]
            ),
            "inference_state": common.sha256_file(binding.artifacts["inference_state"]),
            "predictions": common.sha256_file(binding.artifacts["predictions"]),
            "prediction_manifest": common.sha256_file(
                binding.artifacts["prediction_manifest"]
            ),
        }
        for name, digest in current_hashes.items():
            reference = lineage.get(name)
            if not isinstance(reference, Mapping) or reference.get("sha256") != digest:
                raise prepare.HeldoutPreparationError(
                    f"producer artifact changed during owner-chain validation: {name}"
                )
        return (
            blind_rows,
            blind_payload,
            manifest_payload,
            execution.completed_at_utc,
        )
    except prepare.HeldoutPreparationError as exc:
        raise base.V2AdjudicationError(
            f"heldout producer chain validation failed: {exc}"
        ) from exc


def seal_candidate_rows(
    blind_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    *,
    contract: base.V2AdjudicationContract,
    drafter_id: str,
    drafted_at: str,
    inference_completed_at: str | None = None,
) -> list[dict[str, Any]]:
    if drafter_id != EXPECTED_DRAFTER_ID:
        raise base.V2AdjudicationError(
            f"heldout draft must use registered drafter_id={EXPECTED_DRAFTER_ID!r}"
        )
    drafter = base.normalize_drafter_id(drafter_id)
    if inference_completed_at is not None:
        drafted = datetime.fromisoformat(
            base._aware_iso(drafted_at, label="drafted_at").replace("Z", "+00:00")
        )
        inference_completed = datetime.fromisoformat(
            base._aware_iso(
                inference_completed_at,
                label="inference_completed_at",
            ).replace("Z", "+00:00")
        )
        if drafted < inference_completed:
            raise base.V2AdjudicationError(
                "heldout draft timestamp precedes completed candidate inference"
            )
    sealed: list[dict[str, Any]] = base.seal_candidate_rows(
        blind_rows,
        candidate_rows,
        contract=contract,
        drafter_id=drafter,
        drafted_at=drafted_at,
    )
    return sealed


def validate_draft_timeline(
    draft_rows: Sequence[Mapping[str, Any]],
    *,
    inference_completed_at: str,
) -> None:
    """Require every owner-visible draft to follow completed model inference."""

    inference_completed = datetime.fromisoformat(
        base._aware_iso(
            inference_completed_at,
            label="inference_completed_at",
        ).replace("Z", "+00:00")
    )
    for index, row in enumerate(draft_rows, 1):
        drafted = datetime.fromisoformat(
            base._aware_iso(
                row.get("drafted_at"),
                label=f"sealed draft row {index} drafted_at",
            ).replace("Z", "+00:00")
        )
        if drafted < inference_completed:
            raise base.V2AdjudicationError(
                "heldout draft timestamp precedes completed candidate inference"
            )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-draft", type=Path, required=True)
    parser.add_argument("--drafter-id", default=EXPECTED_DRAFTER_ID)
    parser.add_argument("--drafted-at", required=True)
    parser.add_argument("--selection-manifest", type=Path, default=None)
    parser.add_argument("--blind", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--evaluation-design", type=Path, default=EVALUATION_DESIGN_V2_PATH)
    arguments = parser.parse_args(argv)
    try:
        _binding, stage_authority = prevalidate_stage_authority(
            PROJECT_ROOT,
            stage="seal-draft",
        )
        contract = load_registered_contract(arguments.evaluation_design)
        selection_path = base._bound_input(
            arguments.selection_manifest
            or contract.artifacts["development_private_selection_manifest"],
            contract.artifacts["development_private_selection_manifest"],
            label="selection manifest",
        )
        blind_path = base._bound_input(
            arguments.blind or contract.artifacts["development_owner_blind_jsonl"],
            contract.artifacts["development_owner_blind_jsonl"],
            label="blind",
        )
        output_path = base._bound_input(
            arguments.output or contract.artifacts["development_ai_draft_jsonl"],
            contract.artifacts["development_ai_draft_jsonl"],
            label="output",
        )
        candidate_path = arguments.candidate_draft.expanduser()
        if candidate_path.is_symlink():
            raise base.V2AdjudicationError("candidate draft must not be a symlink")
        candidate_path = candidate_path.resolve()
        blind_rows, _, _, inference_completed_at = read_bound_blind_bundle(
            selection_path,
            blind_path,
            contract=contract,
            stage="seal-draft",
            prevalidated_authority=stage_authority,
            validated_stage="seal-draft",
        )
        candidate_rows, _ = base.read_jsonl(candidate_path, label="heldout candidate draft")
        sealed = seal_candidate_rows(
            blind_rows,
            candidate_rows,
            contract=contract,
            drafter_id=arguments.drafter_id,
            drafted_at=arguments.drafted_at,
            inference_completed_at=inference_completed_at,
        )
        payload = base.canonical_jsonl_bytes(sealed)
        digest = base.write_create_only(output_path, payload)
    except (
        EventEvaluationDesignError,
        base.V2AdjudicationError,
        FileExistsError,
        OSError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "sealed",
                "frame_id": FRAME_ID,
                "output": str(output_path.relative_to(contract.project_root)),
                "sha256": digest,
                "row_count": len(sealed),
                "drafter_id": sealed[0]["drafter_id"],
                "drafting_ai_is_evaluated_model": False,
                "sealing_cli_model_called": False,
                "owner_or_human_gold_created": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
