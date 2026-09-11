#!/usr/bin/env python3
"""Finalize the P4.2a v2 dev45 owner adjudication as create-only evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import build_p4_2a_v2_adjudication_ui as ui  # noqa: E402
from scripts import seal_p4_2a_v2_ai_draft as seal  # noqa: E402

from alphapilot.llm.p4_news_eval import (  # noqa: E402
    EVALUATION_DESIGN_V2_PATH,
    EventEvaluationDesignError,
)

HUMAN_GOLD_SCHEMA = "p4.2a-v2-human-adjudicated-item-v1"
COMPLETION_SCHEMA = "p4.2a-v2-owner-completion-manifest-v1"
EXPORT_FIELDS = frozenset(
    {
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
)
ADJUDICATION_FIELDS = frozenset(
    {
        "method",
        "drafter_id",
        "adjudicator_id",
        "confirmed",
        "changed",
        "changed_fields",
        "adjudicated_at",
    }
)


def _changed_fields(draft_label: Mapping[str, Any], human_label: Mapping[str, Any]) -> list[str]:
    return [
        field for field in seal.LABEL_FIELDS if draft_label.get(field) != human_label.get(field)
    ]


def _adjudicator_id(value: object, *, drafter_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise seal.V2AdjudicationError("adjudicator_id must be non-blank")
    normalized = value.strip()
    identity_key = seal.actor_identity_key(normalized)
    if not identity_key:
        raise seal.V2AdjudicationError("adjudicator_id must contain letters or digits")
    if identity_key == seal.actor_identity_key(drafter_id):
        raise seal.V2AdjudicationError("human adjudicator must differ from the AI drafter")
    return normalized


def normalize_owner_export(
    blind_rows: Sequence[Mapping[str, Any]],
    draft_rows: Sequence[Mapping[str, Any]],
    export_rows: Sequence[Mapping[str, Any]],
    *,
    contract: seal.V2AdjudicationContract,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Recompute all mutable deltas and provenance from the three inputs."""

    blind = seal.validate_blind_rows(blind_rows, contract=contract)
    drafts, drafter_id = seal.validate_sealed_draft_rows(blind, draft_rows, contract=contract)
    if len(export_rows) != contract.expected_count:
        raise seal.V2AdjudicationError(
            f"owner export must contain exactly {contract.expected_count} rows"
        )
    human_rows: list[dict[str, Any]] = []
    one_adjudicator: str | None = None
    drafted_at = str(drafts[0]["drafted_at"])
    drafted_datetime = seal._aware_datetime(drafted_at, label="sealed draft drafted_at")
    adjudicated_timestamps: list[str] = []
    changed_item_count = 0
    changed_field_counts = {field: 0 for field in seal.LABEL_FIELDS}
    for index, (blind_row, draft, raw_export) in enumerate(
        zip(blind, drafts, export_rows, strict=True), 1
    ):
        export = dict(raw_export)
        if set(export) != EXPORT_FIELDS:
            raise seal.V2AdjudicationError(f"owner export row {index} fields drifted")
        leaked = seal._find_hidden_metadata(export)
        if leaked is not None:
            raise seal.V2AdjudicationError(f"owner export row {index} leaks metadata at {leaked}")
        expected_draft_sha = seal.sha256_bytes(seal.canonical_json_bytes(draft))
        if (
            export.get("schema_version") != ui.EXPORT_SCHEMA
            or export.get("design") != contract.design_ref
            or export.get("frame_id") != contract.frame_id
            or export.get("sample_index") != index
            or export.get("news_item_id") != blind_row["news_item_id"]
            or export.get("input_sha256") != blind_row["input_sha256"]
            or export.get("sealed_draft_item_sha256") != expected_draft_sha
            or export.get("draft_label") != draft["draft_label"]
            or export.get("annotation_status") != "adjudicated"
        ):
            raise seal.V2AdjudicationError("owner export IDs/order/design/draft binding drifted")
        human_label = seal.validate_label(
            export.get("human_label"),
            original_text=str(blind_row["original_text"]),
            taxonomy=contract.taxonomy,
            label=f"owner export row {index} human_label",
            require_null_notes=False,
        )
        adjudication = export.get("adjudication")
        if not isinstance(adjudication, Mapping) or set(adjudication) != ADJUDICATION_FIELDS:
            raise seal.V2AdjudicationError(f"owner export row {index} adjudication fields drifted")
        adjudicator_id = _adjudicator_id(adjudication.get("adjudicator_id"), drafter_id=drafter_id)
        if one_adjudicator is None:
            one_adjudicator = adjudicator_id
        elif adjudicator_id != one_adjudicator:
            raise seal.V2AdjudicationError("owner export must use one consistent human adjudicator")
        changed_fields = _changed_fields(draft["draft_label"], human_label)
        changed = bool(changed_fields)
        if (
            adjudication.get("method") != "ai_drafted_human_adjudicated"
            or adjudication.get("drafter_id") != drafter_id
            or adjudication.get("confirmed") is not True
            or adjudication.get("changed") is not changed
            or adjudication.get("changed_fields") != changed_fields
        ):
            raise seal.V2AdjudicationError(
                f"owner export row {index} claimed delta/provenance drifted"
            )
        adjudicated_at = seal._aware_iso(
            adjudication.get("adjudicated_at"),
            label=f"owner export row {index} adjudicated_at",
        )
        if (
            seal._aware_datetime(
                adjudicated_at,
                label=f"owner export row {index} adjudicated_at",
            )
            < drafted_datetime
        ):
            raise seal.V2AdjudicationError(
                f"owner export row {index} adjudicated_at precedes drafted_at"
            )
        adjudicated_timestamps.append(adjudicated_at)
        if changed:
            changed_item_count += 1
            for field in changed_fields:
                changed_field_counts[field] += 1
        provenance = {
            "method": "ai_drafted_human_adjudicated",
            "design": dict(contract.design_ref),
            "frame_id": contract.frame_id,
            "blind_input_sha256": blind_row["input_sha256"],
            "sealed_draft_item_sha256": expected_draft_sha,
            "owner_export_item_sha256": seal.sha256_bytes(seal.canonical_json_bytes(export)),
            "drafter_id": drafter_id,
            "adjudicator_id": adjudicator_id,
            "human_confirmation": True,
            "changed": changed,
            "changed_fields": changed_fields,
        }
        immutable = {
            field: blind_row[field]
            for field in seal.BLIND_FIELDS
            if field not in {"schema_version", "gold"}
        }
        human_rows.append(
            {
                "schema_version": HUMAN_GOLD_SCHEMA,
                **immutable,
                "annotation_status": "completed",
                "annotation_type": "ai_drafted_human_adjudicated",
                "drafted_at": draft["drafted_at"],
                "adjudicated_at": adjudicated_at,
                "draft_label": draft["draft_label"],
                "gold": human_label,
                "provenance": provenance,
            }
        )
    if one_adjudicator is None:
        raise seal.V2AdjudicationError("owner export has no adjudicator")
    summary = {
        "drafter_id": drafter_id,
        "adjudicator_id": one_adjudicator,
        "row_count": len(human_rows),
        "all_items_human_confirmed": True,
        "changed_item_count": changed_item_count,
        "unchanged_item_count": len(human_rows) - changed_item_count,
        "changed_field_counts": changed_field_counts,
        "drafted_at": drafted_at,
        "earliest_adjudicated_at": min(
            adjudicated_timestamps,
            key=lambda value: seal._aware_datetime(value, label="adjudicated_at"),
        ),
        "latest_adjudicated_at": max(
            adjudicated_timestamps,
            key=lambda value: seal._aware_datetime(value, label="adjudicated_at"),
        ),
    }
    return human_rows, summary


def completion_manifest(
    *,
    contract: seal.V2AdjudicationContract,
    completed_at: str,
    selection_manifest_payload: bytes,
    blind_payload: bytes,
    draft_payload: bytes,
    ui_payload: bytes,
    raw_export_payload: bytes,
    human_payload: bytes,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    timestamp = seal._aware_iso(completed_at, label="completed_at")
    latest_adjudicated_at = summary.get("latest_adjudicated_at")
    if (
        seal._aware_datetime(timestamp, label="completed_at")
        < seal._aware_datetime(
            latest_adjudicated_at,
            label="summary.latest_adjudicated_at",
        )
    ):
        raise seal.V2AdjudicationError("completed_at precedes an item adjudicated_at")

    def artifact(name: str, payload: bytes, row_count: int | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "path": str(contract.artifacts[name].relative_to(contract.project_root)),
            "sha256": seal.sha256_bytes(payload),
        }
        if row_count is not None:
            result["row_count"] = row_count
        return result

    return {
        "schema_version": COMPLETION_SCHEMA,
        "design": dict(contract.design_ref),
        "frame_id": contract.frame_id,
        "completed_at": timestamp,
        "artifacts": {
            "private_selection": artifact(
                "development_private_selection_manifest",
                selection_manifest_payload,
            ),
            "owner_blind": artifact(
                "development_owner_blind_jsonl", blind_payload, contract.expected_count
            ),
            "ai_draft": artifact(
                "development_ai_draft_jsonl", draft_payload, contract.expected_count
            ),
            "adjudication_ui": artifact("development_adjudication_html", ui_payload),
            "owner_raw_export": artifact(
                "development_owner_raw_export_jsonl",
                raw_export_payload,
                contract.expected_count,
            ),
            "human_adjudicated": artifact(
                "development_human_adjudicated_jsonl",
                human_payload,
                contract.expected_count,
            ),
        },
        "provenance": dict(summary),
        "validation": {
            "blind_schema": seal.BLIND_SCHEMA,
            "ai_draft_schema": seal.SEALED_DRAFT_SCHEMA,
            "owner_export_schema": ui.EXPORT_SCHEMA,
            "human_gold_schema": HUMAN_GOLD_SCHEMA,
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
        },
        "model_execution": {
            "drafting_ai_inference_occurred": True,
            "drafting_ai": summary["drafter_id"],
            "drafting_ai_is_evaluated_model": False,
            "evaluated_model_calls_before_calibration": 0,
            "heldout_model_calls": 0,
            "workflow_script_model_calls": 0,
        },
        "heldout_touched": False,
    }


def _write_bundle_create_only(
    payloads: Sequence[tuple[Path, bytes]],
) -> dict[Path, str]:
    if len({path for path, _ in payloads}) != len(payloads):
        raise seal.V2AdjudicationError("completion output paths overlap")
    for path, _ in payloads:
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to overwrite create-only artifact: {path}")
    created: list[Path] = []
    try:
        for path, payload in payloads:
            seal.write_create_only(path, payload)
            created.append(path)
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        raise
    return {path: seal.sha256_bytes(payload) for path, payload in payloads}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-export", type=Path, required=True)
    parser.add_argument("--completed-at", required=True)
    parser.add_argument("--selection-manifest", type=Path, default=None)
    parser.add_argument("--blind", type=Path, default=None)
    parser.add_argument("--draft", type=Path, default=None)
    parser.add_argument("--human-output", type=Path, default=None)
    parser.add_argument("--completion-manifest", type=Path, default=None)
    parser.add_argument("--evaluation-design", type=Path, default=EVALUATION_DESIGN_V2_PATH)
    arguments = parser.parse_args(argv)
    try:
        contract = seal.load_registered_contract(arguments.evaluation_design)
        selection_manifest_path = seal._bound_input(
            arguments.selection_manifest
            or contract.artifacts["development_private_selection_manifest"],
            contract.artifacts["development_private_selection_manifest"],
            label="selection manifest",
        )
        blind_path = seal._bound_input(
            arguments.blind or contract.artifacts["development_owner_blind_jsonl"],
            contract.artifacts["development_owner_blind_jsonl"],
            label="blind",
        )
        draft_path = seal._bound_input(
            arguments.draft or contract.artifacts["development_ai_draft_jsonl"],
            contract.artifacts["development_ai_draft_jsonl"],
            label="draft",
        )
        human_path = seal._bound_input(
            arguments.human_output or contract.artifacts["development_human_adjudicated_jsonl"],
            contract.artifacts["development_human_adjudicated_jsonl"],
            label="human output",
        )
        manifest_path = seal._bound_input(
            arguments.completion_manifest
            or contract.artifacts["development_owner_completion_manifest"],
            contract.artifacts["development_owner_completion_manifest"],
            label="completion manifest",
        )
        raw_target = contract.artifacts["development_owner_raw_export_jsonl"]
        raw_source_candidate = arguments.owner_export.expanduser()
        if raw_source_candidate.is_symlink():
            raise seal.V2AdjudicationError("owner export must not be a symlink")
        raw_source = raw_source_candidate.resolve()
        blind_rows, blind_payload, selection_manifest_payload = seal.read_bound_blind_bundle(
            selection_manifest_path,
            blind_path,
            contract=contract,
        )
        draft_rows, draft_payload = seal.read_jsonl(draft_path, label="sealed draft")
        export_rows, raw_payload = seal.read_jsonl(raw_source, label="owner export")
        ui_path = contract.artifacts["development_adjudication_html"]
        if ui_path.is_symlink() or not ui_path.is_file():
            raise seal.V2AdjudicationError("registered adjudication UI is unavailable")
        ui_payload = ui_path.read_bytes()
        expected_ui_payload, _ = ui.render_registered_ui_payload(
            blind_rows,
            draft_rows,
            contract=contract,
            blind_payload=blind_payload,
            draft_payload=draft_payload,
        )
        if ui_payload != expected_ui_payload:
            raise seal.V2AdjudicationError(
                "registered adjudication UI differs from its deterministic blind/draft rendering"
            )
        human_rows, summary = normalize_owner_export(
            blind_rows, draft_rows, export_rows, contract=contract
        )
        human_payload = seal.canonical_jsonl_bytes(human_rows)
        manifest = completion_manifest(
            contract=contract,
            completed_at=arguments.completed_at,
            selection_manifest_payload=selection_manifest_payload,
            blind_payload=blind_payload,
            draft_payload=draft_payload,
            ui_payload=ui_payload,
            raw_export_payload=raw_payload,
            human_payload=human_payload,
            summary=summary,
        )
        manifest_payload = seal.canonical_json_bytes(manifest)
        outputs: list[tuple[Path, bytes]] = []
        if raw_source == raw_target:
            if seal.sha256_bytes(raw_source.read_bytes()) != seal.sha256_bytes(raw_payload):
                raise seal.V2AdjudicationError("registered raw export changed during validation")
        else:
            outputs.append((raw_target, raw_payload))
        outputs.extend(((human_path, human_payload), (manifest_path, manifest_payload)))
        hashes = _write_bundle_create_only(outputs)
    except (
        EventEvaluationDesignError,
        seal.V2AdjudicationError,
        FileExistsError,
        OSError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "completed",
                "row_count": summary["row_count"],
                "drafter_id": summary["drafter_id"],
                "adjudicator_id": summary["adjudicator_id"],
                "changed_item_count": summary["changed_item_count"],
                "raw_export_sha256": seal.sha256_bytes(raw_payload),
                "human_gold_sha256": hashes[human_path],
                "completion_manifest_sha256": hashes[manifest_path],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
