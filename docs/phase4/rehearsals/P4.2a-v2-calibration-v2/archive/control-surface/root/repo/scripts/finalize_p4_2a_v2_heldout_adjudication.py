#!/usr/bin/env python3
"""Create-only finalize the P4.2a v2 held-out owner adjudication.

The held-out owner export is never scored here.  This command only validates
the blinded 60-row chain, independently recomputes every human delta, and
freezes the raw export, canonical human gold, and completion manifest.  The
one-shot evaluation remains a separate, independently authorized operation.
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

from scripts import build_p4_2a_v2_heldout_adjudication_ui as heldout_ui  # noqa: E402
from scripts import finalize_p4_2a_v2_dev_adjudication as base_finalizer  # noqa: E402
from scripts import seal_p4_2a_v2_ai_draft as base_seal  # noqa: E402
from scripts import seal_p4_2a_v2_heldout_draft as heldout  # noqa: E402

from alphapilot.llm.p4_news_eval import (  # noqa: E402
    EVALUATION_DESIGN_V2_PATH,
    EventEvaluationDesignError,
)


def build_heldout_completion_manifest(
    *,
    contract: base_seal.V2AdjudicationContract,
    completed_at: str,
    selection_manifest: Mapping[str, Any],
    selection_manifest_payload: bytes,
    blind_payload: bytes,
    draft_payload: bytes,
    ui_payload: bytes,
    raw_export_payload: bytes,
    human_payload: bytes,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the held-out-specific completion manifest without scoring it."""

    if (
        summary.get("drafter_id") != heldout.EXPECTED_DRAFTER_ID
        or summary.get("adjudicator_id") != "ouyang"
        or summary.get("row_count") != heldout.EXPECTED_COUNT
        or summary.get("all_items_human_confirmed") is not True
    ):
        raise base_seal.V2AdjudicationError(
            "heldout completion requires the registered drafter and owner adjudicator"
        )

    manifest = base_finalizer.completion_manifest(
        contract=contract,
        completed_at=completed_at,
        selection_manifest_payload=selection_manifest_payload,
        blind_payload=blind_payload,
        draft_payload=draft_payload,
        ui_payload=ui_payload,
        raw_export_payload=raw_export_payload,
        human_payload=human_payload,
        summary=summary,
    )
    audit = selection_manifest.get("audit")
    if not isinstance(audit, Mapping):
        raise base_seal.V2AdjudicationError("heldout selection audit is invalid")
    candidate_count = audit.get("eligible_candidate_count")
    success_count = audit.get("successful_prediction_count")
    failure_count = audit.get("extract_failed_count")
    if (
        isinstance(candidate_count, bool)
        or not isinstance(candidate_count, int)
        or candidate_count < contract.expected_count
        or success_count != candidate_count
        or failure_count != 0
    ):
        raise base_seal.V2AdjudicationError(
            "heldout selection does not prove a complete successful candidate inference"
        )
    validation = manifest.get("validation")
    if not isinstance(validation, dict):
        raise base_seal.V2AdjudicationError("completion validation is invalid")
    validation["blind_schema"] = heldout.BLIND_SCHEMA
    validation["heldout_40_20_partition_check"] = True
    validation["full_candidate_inference_success_check"] = True
    manifest["model_execution"] = {
        "drafting_ai_inference_occurred": True,
        "drafting_ai": summary["drafter_id"],
        "drafting_ai_is_evaluated_model": False,
        "selected_model": heldout.EVALUATED_MODEL,
        "selected_model_candidate_inference_count": candidate_count,
        "selected_model_candidate_failure_count": 0,
        "final_one_shot_evaluation_calls": 0,
        "workflow_script_model_calls": 0,
    }
    manifest["heldout_touched"] = True
    manifest["safety"] = {
        "production_database_writes": 0,
        "proposals_or_orders_created": False,
        "one_shot_evaluation_consumed": False,
        "p4_2a_done": False,
        "p4_2b_unlocked": False,
        "p4_3_unlocked": False,
    }
    return manifest


def finalize_owner_export(
    *,
    contract: base_seal.V2AdjudicationContract,
    owner_export_path: Path,
    completed_at: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[Path, str]]:
    """Validate all registered inputs and atomically freeze the three outputs."""

    selection_path = contract.artifacts["development_private_selection_manifest"]
    blind_path = contract.artifacts["development_owner_blind_jsonl"]
    draft_path = contract.artifacts["development_ai_draft_jsonl"]
    ui_path = contract.artifacts["development_adjudication_html"]
    raw_target = contract.artifacts["development_owner_raw_export_jsonl"]
    human_path = contract.artifacts["development_human_adjudicated_jsonl"]
    completion_path = contract.artifacts["development_owner_completion_manifest"]

    (
        blind_rows,
        blind_payload,
        selection_payload,
        inference_completed_at,
    ) = heldout.read_bound_blind_bundle(
        selection_path,
        blind_path,
        contract=contract,
    )
    selection, observed_selection_payload = base_seal.read_json_object(
        selection_path,
        label="heldout private selection manifest",
    )
    if observed_selection_payload != selection_payload:
        raise base_seal.V2AdjudicationError("heldout selection changed during validation")
    draft_rows, draft_payload = base_seal.read_jsonl(draft_path, label="heldout sealed draft")
    heldout.validate_draft_timeline(
        draft_rows,
        inference_completed_at=inference_completed_at,
    )

    source_candidate = owner_export_path.expanduser()
    if source_candidate.is_symlink():
        raise base_seal.V2AdjudicationError("owner export must not be a symlink")
    source = source_candidate.resolve()
    export_rows, raw_payload = base_seal.read_jsonl(source, label="heldout owner export")
    for index, row in enumerate(export_rows, 1):
        adjudication = row.get("adjudication")
        if (
            not isinstance(adjudication, Mapping)
            or adjudication.get("drafter_id") != heldout.EXPECTED_DRAFTER_ID
            or adjudication.get("adjudicator_id")
            != heldout_ui.EXPECTED_ADJUDICATOR_ID
        ):
            raise base_seal.V2AdjudicationError(
                f"heldout owner export row {index} actor identity drifted"
            )

    if ui_path.is_symlink() or not ui_path.is_file():
        raise base_seal.V2AdjudicationError("registered heldout adjudication UI is unavailable")
    ui_payload = ui_path.read_bytes()
    expected_ui, _row_count = heldout_ui.render_registered_ui_payload(
        blind_rows,
        draft_rows,
        contract=contract,
        blind_payload=blind_payload,
        draft_payload=draft_payload,
        selection_payload=selection_payload,
    )
    if ui_payload != expected_ui:
        raise base_seal.V2AdjudicationError(
            "registered heldout adjudication UI differs from its deterministic rendering"
        )

    human_rows, summary = base_finalizer.normalize_owner_export(
        blind_rows,
        draft_rows,
        export_rows,
        contract=contract,
    )
    latest_adjudication: datetime | None = None
    for index, (draft, export) in enumerate(zip(draft_rows, export_rows, strict=True), 1):
        adjudication = export["adjudication"]
        if not isinstance(adjudication, Mapping):
            raise base_seal.V2AdjudicationError(
                f"heldout owner export row {index} adjudication is invalid"
            )
        drafted_at = datetime.fromisoformat(
            base_seal._aware_iso(
                draft.get("drafted_at"),
                label=f"heldout draft row {index} drafted_at",
            ).replace("Z", "+00:00")
        )
        adjudicated_at = datetime.fromisoformat(
            base_seal._aware_iso(
                adjudication.get("adjudicated_at"),
                label=f"heldout owner row {index} adjudicated_at",
            ).replace("Z", "+00:00")
        )
        if adjudicated_at < drafted_at:
            raise base_seal.V2AdjudicationError(
                "heldout adjudication timestamp precedes its AI draft"
            )
        if latest_adjudication is None or adjudicated_at > latest_adjudication:
            latest_adjudication = adjudicated_at
    completion_at = datetime.fromisoformat(
        base_seal._aware_iso(completed_at, label="completed_at").replace("Z", "+00:00")
    )
    if latest_adjudication is None or completion_at < latest_adjudication:
        raise base_seal.V2AdjudicationError(
            "heldout completion timestamp precedes owner adjudication"
        )
    human_payload = base_seal.canonical_jsonl_bytes(human_rows)
    completion = build_heldout_completion_manifest(
        contract=contract,
        completed_at=completed_at,
        selection_manifest=selection,
        selection_manifest_payload=selection_payload,
        blind_payload=blind_payload,
        draft_payload=draft_payload,
        ui_payload=ui_payload,
        raw_export_payload=raw_payload,
        human_payload=human_payload,
        summary=summary,
    )
    completion_payload = base_seal.canonical_json_bytes(completion)

    outputs: list[tuple[Path, bytes]] = []
    if source == raw_target.resolve():
        if base_seal.sha256_bytes(source.read_bytes()) != base_seal.sha256_bytes(raw_payload):
            raise base_seal.V2AdjudicationError(
                "registered heldout raw export changed during validation"
            )
    else:
        outputs.append((raw_target, raw_payload))
    outputs.extend(((human_path, human_payload), (completion_path, completion_payload)))
    hashes = base_finalizer._write_bundle_create_only(outputs)
    return summary, completion, hashes


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-export", type=Path, required=True)
    parser.add_argument("--completed-at", required=True)
    parser.add_argument("--evaluation-design", type=Path, default=EVALUATION_DESIGN_V2_PATH)
    arguments = parser.parse_args(argv)
    try:
        contract = heldout.load_registered_contract(arguments.evaluation_design)
        summary, _completion, hashes = finalize_owner_export(
            contract=contract,
            owner_export_path=arguments.owner_export,
            completed_at=arguments.completed_at,
        )
        raw_path = contract.artifacts["development_owner_raw_export_jsonl"]
        human_path = contract.artifacts["development_human_adjudicated_jsonl"]
        completion_path = contract.artifacts["development_owner_completion_manifest"]
        raw_sha = base_seal.sha256_bytes(raw_path.read_bytes())
    except (
        EventEvaluationDesignError,
        base_seal.V2AdjudicationError,
        FileExistsError,
        OSError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "heldout_owner_gold_frozen_pending_dry_run",
                "row_count": summary["row_count"],
                "drafter_id": summary["drafter_id"],
                "adjudicator_id": summary["adjudicator_id"],
                "changed_item_count": summary["changed_item_count"],
                "raw_export_sha256": raw_sha,
                "human_gold_sha256": hashes[human_path],
                "completion_manifest_sha256": hashes[completion_path],
                "one_shot_evaluation_consumed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
