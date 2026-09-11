from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar, cast

import pytest
from scripts import evaluate_p4_2a_v2_heldout as evaluator
from scripts import p4_2a_v2_dev_common as pipeline_common
from scripts import prepare_p4_2a_v2_heldout as heldout_prepare

JsonObject = dict[str, Any]
Tamper = Callable[[Any], None]
_TestCallable = TypeVar("_TestCallable", bound=Callable[..., object])
_fixture: Callable[[_TestCallable], _TestCallable] = cast(
    Callable[[_TestCallable], _TestCallable], pytest.fixture
)
_parametrize: Callable[..., Callable[[_TestCallable], _TestCallable]] = cast(
    Callable[..., Callable[[_TestCallable], _TestCallable]],
    pytest.mark.parametrize,
)


@_fixture
def _unit_release_gate_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """These fixtures exercise consumers; the successor runner probes the real gate."""

    monkeypatch.setattr(
        heldout_prepare,
        "validate_v2_1_stage_authorization",
        lambda _binding, *, stage, execution_context=None: execution_context or stage,
    )


_ISOLATED_CONSUMER = cast(
    Callable[[_TestCallable], _TestCallable],
    pytest.mark.usefixtures("_unit_release_gate_isolation"),
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()


def _write_json(path: Path, value: object) -> bytes:
    payload = _json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _write_jsonl(path: Path, rows: list[JsonObject]) -> bytes:
    payload = b"".join(_json_bytes(row) for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _read_json(path: Path) -> JsonObject:
    return cast(JsonObject, json.loads(path.read_text()))


def _read_jsonl(path: Path) -> list[JsonObject]:
    return [cast(JsonObject, json.loads(line)) for line in path.read_text().splitlines()]


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _label(materiality: int, symbol: str, evidence: str) -> JsonObject:
    return {
        "symbols": [symbol],
        "event_type": "other",
        "direction": 0,
        "materiality": materiality,
        "evidence_span": evidence,
        "notes": None,
    }


def _prediction_label(materiality: int, symbol: str, evidence: str) -> JsonObject:
    return {
        "symbols": [symbol],
        "event_type": "other",
        "direction": 0,
        "materiality": materiality,
        "summary": "synthetic held-out prediction",
        "confidence": 1.0,
        "evidence_span": evidence,
    }


def _artifact_paths(root: Path) -> evaluator.ArtifactPaths:
    return evaluator.ArtifactPaths(
        materialized_inputs=root / "materialized-inputs.jsonl",
        materialization_manifest=root / "materialization-manifest.json",
        inference_state=root / "inference.state.jsonl",
        predictions=root / "predictions.jsonl",
        prediction_manifest=root / "predictions.manifest.json",
        selection=root / "selection.json",
        blind=root / "blind.jsonl",
        draft=root / "draft.jsonl",
        adjudication_ui=root / "adjudication.html",
        owner_export=root / "owner-export.jsonl",
        human_adjudicated=root / "human.jsonl",
        owner_completion=root / "completion.json",
        evaluation_state=root / "evaluation.state.jsonl",
        report=root / "report/result.json",
        artifact_root=root,
    )


def _source_lineage(controls: evaluator.ControlBundle) -> JsonObject:
    prereg_source = cast(Mapping[str, Any], controls.preregistration["source_frame"])
    prereg_lineage = cast(Mapping[str, Any], prereg_source["source_lineage"])
    frames = cast(Mapping[str, Any], controls.design["frames"])
    frame = cast(Mapping[str, Any], frames["heldout_frame_v2"])
    design_lineage = cast(Mapping[str, Any], frame["source_lineage"])
    return {
        "required_closed_dates_shanghai": prereg_lineage["required_closed_dates_shanghai"],
        "verified_checkpoint_date_shanghai": prereg_lineage["verified_checkpoint_date_shanghai"],
        "migration_job_run_ids": prereg_lineage["migration_job_run_ids"],
        "evidence": {
            name: copy.deepcopy(design_lineage[name])
            for name in (
                "round3_evidence",
                "round3_independent_review",
                "incremental_evidence",
                "incremental_independent_review",
            )
        },
    }


def _snapshot(*, universe_symbol_count: int = 80) -> JsonObject:
    return {
        "sqlite_uri_mode": "ro",
        "pragma_query_only": 1,
        "connection_total_changes": 0,
        "llm_call_count": 0,
        "llm_call_max_id": None,
        "trade_proposal_count": 0,
        "broker_order_count": 0,
        "non_simulate_order_count": 0,
        "news_events_table_exists": True,
        "universe_symbol_count": universe_symbol_count,
    }


def _fixture_bundle(tmp_path: Path) -> evaluator.ArtifactPaths:
    paths = _artifact_paths(tmp_path)
    controls = evaluator.load_control_bundle()
    active_contract = heldout_prepare._load_selected_contract(evaluator.PROJECT_ROOT)
    candidates: list[JsonObject] = []
    predictions: list[JsonObject] = []
    candidate_sources = [
        *(["akshare_ths"] * evaluator.EXPECTED_RAW_BY_SOURCE["akshare_ths"]),
        *(["sina_company_news"] * evaluator.EXPECTED_RAW_BY_SOURCE["sina_company_news"]),
        *(["cninfo"] * 80),
    ]
    for offset, source in enumerate(candidate_sources):
        identifier = 900_001 + offset
        symbol = str(identifier)
        candidate = heldout_prepare._synthetic_input(
            identifier,
            contract=active_contract,
            source=source,
        )
        candidate["schema_version"] = "p4.2a-heldout-candidate-input-v1.1"
        candidates.append(candidate)
        original_text = cast(str, candidate["original_text"])
        evidence = original_text
        materiality = 2 if offset < 50 else 1
        predictions.append(
            {
                "schema_version": "p4.2a-offline-extract-row-v1",
                "news_item_id": identifier,
                "source": source,
                "status": "ok",
                "model": evaluator.MODEL,
                "contract_sha256": evaluator.HELDOUT_CONTRACT_SHA256,
                "input_sha256": candidate["input_sha256"],
                "declared_input_sha256": candidate["declared_input_sha256"],
                "text_sha256": candidate["text_sha256"],
                "recorded_at_utc": "2026-08-10T04:05:00Z",
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
                "prediction": _prediction_label(materiality, symbol, evidence),
            }
        )

    payloads: dict[str, bytes] = {
        "materialized_inputs": _write_jsonl(paths.materialized_inputs, candidates),
        "predictions": _write_jsonl(paths.predictions, predictions),
    }

    def relative(path: Path) -> str:
        registered_path: str = evaluator._registered_path(tmp_path, path)
        return registered_path

    prereg_eligibility = cast(
        Mapping[str, Any], controls.preregistration["eligibility_and_sampling"]
    )
    retired = cast(Mapping[str, Any], prereg_eligibility["retired_selection"])
    candidate_source_counts = {
        source: sum(row["source"] == source for row in candidates)
        for source in evaluator.EXPECTED_RAW_BY_SOURCE
    }
    ineligible_sources = [
        source
        for source, expected_count in evaluator.EXPECTED_RAW_BY_SOURCE.items()
        for _ in range(expected_count - candidate_source_counts[source])
    ]
    ineligible_ids = list(range(2_000_000, 2_000_000 + len(ineligible_sources)))
    ineligible_id_sources = list(zip(ineligible_ids, ineligible_sources, strict=True))
    eligible_layer = [
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
    ]
    ineligible_layer = [
        {
            "news_item_id": identifier,
            "url": f"https://static.cninfo.com.cn/synthetic/{identifier}.PDF",
            "reason": "pdf_text_below_min_char_gate",
            "measured_value": 0,
            "gate_value": 80,
            "pdf_sha256": _sha(f"pdf-{identifier}"),
        }
        for identifier, _source in ineligible_id_sources
    ]
    all_candidate_layer = [
        {
            "news_item_id": cast(int, row["news_item_id"]),
            "source": row["source"],
            "url": row["url"],
            "content_hash": row["content_hash"],
        }
        for row in candidates
    ] + [
        {
            "news_item_id": identifier,
            "source": source,
            "url": f"https://static.cninfo.com.cn/synthetic/{identifier}.PDF",
            "content_hash": _sha(f"content-{identifier}"),
        }
        for identifier, source in ineligible_id_sources
    ]
    materialization_manifest: JsonObject = {
        "schema_version": evaluator.MATERIALIZATION_MANIFEST_SCHEMA,
        "frame_id": evaluator.FRAME_ID,
        "lineage": {
            "preregistration": {
                "path": str(evaluator.PREREGISTRATION_PATH),
                "sha256": evaluator.PREREGISTRATION_SHA256,
            },
            "design": dict(evaluator.DESIGN_REF),
            "contract": {
                "path": str(evaluator.HELDOUT_CONTRACT_PATH),
                "sha256": evaluator.HELDOUT_CONTRACT_SHA256,
                "model": evaluator.MODEL,
            },
            "source_window": {
                "start_inclusive_utc": "2026-08-05T16:00:00Z",
                "end_exclusive_utc": "2026-08-08T16:00:00Z",
            },
            "source_lineage": _source_lineage(controls),
            "retired_selection_sha256": retired["sha256"],
        },
        "artifacts": {
            "eligible_inputs_jsonl": {
                "path": relative(paths.materialized_inputs),
                "sha256": _digest(payloads["materialized_inputs"]),
                "create_only": True,
            },
            "manifest": {
                "path": relative(paths.materialization_manifest),
                "create_only": True,
            },
        },
        "counts": {
            "raw_source_window": 4048,
            "retired_excluded_before_materialization": 0,
            "all_candidates_after_retirement": 4048,
            "eligible_candidates": len(candidates),
            "ineligible_candidates": len(ineligible_layer),
            "ineligible_by_reason": {
                "pdf_text_below_min_char_gate": len(ineligible_layer)
            },
        },
        "layers": {
            "all_candidates": all_candidate_layer,
            "eligible_candidates": eligible_layer,
            "ineligible_candidates": ineligible_layer,
        },
        "production_database": {"mode": "ro", "pragma_query_only": 1, "writes": 0},
        "execution_authority": {
            "mode": "offline_rehearsal",
            "frame_authority": {
                "path": str(evaluator.FRAME_AUTHORITY_PATH),
                "sha256": evaluator.FRAME_AUTHORITY_SHA256,
            },
            "successor_code_gate_authority": {
                "path": str(evaluator.SUCCESSOR_CODE_GATE_AUTHORITY_PATH),
                "sha256": evaluator.SUCCESSOR_CODE_GATE_AUTHORITY_SHA256,
            },
            "successor_preregistration": {
                "path": str(evaluator.SUCCESSOR_PREREGISTRATION_PATH),
                "sha256": evaluator.SUCCESSOR_PREREGISTRATION_SHA256,
            },
            "preregistration_commit": evaluator.SUCCESSOR_PREREGISTRATION_COMMIT,
            "implementation_commit": "1" * 40,
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
                "request_start_count": evaluator.EXPECTED_RAW_BY_SOURCE["cninfo"],
                "observed_gap_count": evaluator.EXPECTED_RAW_BY_SOURCE["cninfo"] - 1,
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
    payloads["materialization_manifest"] = _write_json(
        paths.materialization_manifest, materialization_manifest
    )

    execution_id = "a43d80d8-f71b-4dd0-b1f9-71db88084915"
    snapshot = _snapshot(universe_symbol_count=len(candidates))
    prediction_manifest: JsonObject = {
        "schema_version": "p4.2a-v2-heldout-prediction-manifest-v1",
        "frame_id": evaluator.FRAME_ID,
        "execution_id": execution_id,
        "preregistration_sha256": evaluator.PREREGISTRATION_SHA256,
        "materialization_manifest_sha256": _digest(payloads["materialization_manifest"]),
        "contract_sha256": evaluator.HELDOUT_CONTRACT_SHA256,
        "model": evaluator.MODEL,
        "candidate_count": len(candidates),
        "prediction_count": len(predictions),
        "status_ok_count": len(predictions),
        "status_failed_count": 0,
        "one_news_item_per_request": True,
        "one_request_per_eligible_candidate": True,
        "automatic_retries": 0,
        "failed_candidate_retries": 0,
        "production_snapshot_unchanged": True,
        "production_snapshot_before": copy.deepcopy(snapshot),
        "production_snapshot_after": copy.deepcopy(snapshot),
        "settings_safety": copy.deepcopy(evaluator._INFERENCE_SETTINGS_SAFETY),
        "predictions": {
            "path": relative(paths.predictions),
            "sha256": _digest(payloads["predictions"]),
        },
    }
    payloads["prediction_manifest"] = _write_json(paths.prediction_manifest, prediction_manifest)
    inference_state: list[JsonObject] = [
        {
            "schema_version": "p4.2a-v2-heldout-inference-state-v1",
            "status": "inference_started",
            "execution_id": execution_id,
            "started_at_utc": "2026-08-10T04:00:00Z",
            "eligible_candidate_count": len(candidates),
            "candidate_order": "ascending_news_item_id",
            "preregistration_sha256": evaluator.PREREGISTRATION_SHA256,
            "materialization_manifest_sha256": _digest(payloads["materialization_manifest"]),
            "contract_sha256": evaluator.HELDOUT_CONTRACT_SHA256,
            "model": evaluator.MODEL,
            "automatic_retries": 0,
            "failed_candidate_retries": 0,
            "settings_safety": copy.deepcopy(evaluator._INFERENCE_SETTINGS_SAFETY),
            "production_snapshot_before": copy.deepcopy(snapshot),
        },
        {
            "schema_version": "p4.2a-v2-heldout-inference-state-v1",
            "status": "completed_all_eligible_candidates_once",
            "execution_id": execution_id,
            "preregistration_sha256": evaluator.PREREGISTRATION_SHA256,
            "materialization_manifest_sha256": _digest(payloads["materialization_manifest"]),
            "completed_at_utc": "2026-08-10T04:10:00Z",
            "prediction_count": len(predictions),
            "predictions_sha256": _digest(payloads["predictions"]),
            "prediction_manifest_sha256": _digest(payloads["prediction_manifest"]),
            "production_snapshot_unchanged": True,
            "production_snapshot_before": copy.deepcopy(snapshot),
            "production_snapshot_after": copy.deepcopy(snapshot),
        },
    ]
    payloads["inference_state"] = _write_jsonl(paths.inference_state, inference_state)

    predictions_by_id = {cast(int, row["news_item_id"]): row for row in predictions}
    ranked_selected: list[tuple[str, str, JsonObject]] = []
    for stratum, quota, rows in (
        ("predicted_positive", 40, candidates[:50]),
        ("predicted_negative", 20, candidates[50:]),
    ):
        ranked = sorted(
            (
                pipeline_common.selection_rank(
                    seed=evaluator.SELECTION_SEED,
                    sampling_stratum=stratum,
                    news_item_id=cast(int, row["news_item_id"]),
                    input_sha256=cast(str, row["input_sha256"]),
                ),
                row,
            )
            for row in rows
        )
        ranked_selected.extend((stratum, rank, row) for rank, row in ranked[:quota])
    ordered = sorted(
        (
            pipeline_common.owner_order_rank(
                design_sha256=evaluator.DESIGN_SHA256,
                news_item_id=cast(int, row["news_item_id"]),
                input_sha256=cast(str, row["input_sha256"]),
            ),
            stratum,
            rank,
            row,
        )
        for stratum, rank, row in ranked_selected
    )
    selected: list[JsonObject] = []
    bindings: list[JsonObject] = []
    blind: list[JsonObject] = []
    for sample_index, (owner_rank, stratum, rank, candidate) in enumerate(ordered, 1):
        identifier = cast(int, candidate["news_item_id"])
        selected.append(
            {
                "sample_index": sample_index,
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
        bindings.append(
            {
                "sample_index": sample_index,
                "news_item_id": identifier,
                "prediction_row_sha256": _digest(_json_bytes(predictions_by_id[identifier])),
            }
        )
        blind.append(
            {
                "schema_version": "p4.2a-v2-heldout-owner-blind-item-v1",
                "design": dict(evaluator.DESIGN_REF),
                "frame_id": evaluator.FRAME_ID,
                "sample_index": sample_index,
                "news_item_id": identifier,
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
        )
    payloads["blind"] = _write_jsonl(paths.blind, blind)
    selection: JsonObject = {
        "schema_version": "p4.2a-v2-heldout-selection-manifest-v1",
        "design": dict(evaluator.DESIGN_REF),
        "frame_id": evaluator.FRAME_ID,
        "source_lineage": {
            "binding_scope": "registered_full_execution",
            "preregistration": {
                "path": str(evaluator.PREREGISTRATION_PATH),
                "sha256": evaluator.PREREGISTRATION_SHA256,
            },
            "design": dict(evaluator.DESIGN_REF),
            "materialized_inputs": {
                "path": relative(paths.materialized_inputs),
                "sha256": _digest(payloads["materialized_inputs"]),
                "row_count": len(candidates),
            },
            "predictions": {
                "path": relative(paths.predictions),
                "sha256": _digest(payloads["predictions"]),
                "row_count": len(predictions),
            },
            "heldout_execution_contract": {
                "path": str(evaluator.HELDOUT_CONTRACT_PATH),
                "sha256": evaluator.HELDOUT_CONTRACT_SHA256,
                "model": evaluator.MODEL,
            },
            "selected_predictions": {
                "binding": "sha256_of_canonical_complete_prediction_row",
                "count": 60,
                "bindings_sha256": _digest(pipeline_common.canonical_json_bytes(bindings)),
                "bindings": bindings,
            },
            "materialization_manifest": {
                "path": relative(paths.materialization_manifest),
                "sha256": _digest(payloads["materialization_manifest"]),
            },
            "inference_state": {
                "path": relative(paths.inference_state),
                "sha256": _digest(payloads["inference_state"]),
                "event_count": 2,
            },
            "prediction_manifest": {
                "path": relative(paths.prediction_manifest),
                "sha256": _digest(payloads["prediction_manifest"]),
            },
            "execution": {
                "execution_id": execution_id,
                "eligible_candidate_count": len(candidates),
                "prediction_count": len(predictions),
                "status_ok_count": len(predictions),
                "status_failed_count": 0,
                "automatic_retries": 0,
                "failed_candidate_retries": 0,
                "terminal_status": "completed_all_eligible_candidates_once",
            },
        },
        "selection": {
            "algorithm": "sha256_rank_without_replacement_per_stratum_v1",
            "seed": evaluator.SELECTION_SEED,
            "without_replacement": True,
            "selected_counts": {
                "predicted_positive": 40,
                "predicted_negative": 20,
                "extract_failed": 0,
                "total": 60,
            },
            "selected": selected,
        },
        "audit": {
            "eligible_candidate_count": len(candidates),
            "successful_prediction_count": len(predictions),
            "extract_failed_count": 0,
            "available_by_stratum": {
                "predicted_positive": 50,
                "predicted_negative": len(candidates) - 50,
            },
            "retired_selected_intersection_count": 0,
            "input_prediction_identity_match": True,
        },
        "owner_delivery": {
            "path": relative(paths.blind),
            "sha256": _digest(payloads["blind"]),
            "row_count": 60,
            "prediction_visible": False,
            "sampling_stratum_visible": False,
            "selection_rank_visible": False,
            "gold_state": "empty_object_pending_ai_draft_and_human_adjudication",
        },
        "production_writes": False,
    }
    payloads["selection"] = _write_json(paths.selection, selection)

    drafts: list[JsonObject] = []
    owner: list[JsonObject] = []
    human: list[JsonObject] = []
    for blind_row in blind:
        identifier = cast(int, blind_row["news_item_id"])
        symbol = cast(str, blind_row["ingested_symbol"])
        evidence = cast(str, blind_row["original_text"])
        gold_materiality = cast(
            int,
            cast(Mapping[str, Any], predictions_by_id[identifier]["prediction"])["materiality"],
        )
        draft_label = _label(gold_materiality, symbol, evidence)
        common = {
            "design": dict(evaluator.DESIGN_REF),
            "frame_id": evaluator.FRAME_ID,
            "sample_index": blind_row["sample_index"],
            "news_item_id": identifier,
            "input_sha256": blind_row["input_sha256"],
        }
        draft: JsonObject = {
            "schema_version": "p4.2a-v2-ai-draft-item-v1",
            **common,
            "drafter_id": evaluator.EXPECTED_DRAFTER_ID,
            "drafted_at": "2026-08-10T04:20:00Z",
            "draft_label": draft_label,
        }
        drafts.append(draft)
        owner_row: JsonObject = {
            "schema_version": "p4.2a-v2-owner-adjudication-export-item-v1",
            **common,
            "sealed_draft_item_sha256": _digest(_json_bytes(draft)),
            "draft_label": copy.deepcopy(draft_label),
            "human_label": copy.deepcopy(draft_label),
            "annotation_status": "adjudicated",
            "adjudication": {
                "method": "ai_drafted_human_adjudicated",
                "drafter_id": evaluator.EXPECTED_DRAFTER_ID,
                "adjudicator_id": evaluator.EXPECTED_ADJUDICATOR_ID,
                "confirmed": True,
                "changed": False,
                "changed_fields": [],
                "adjudicated_at": "2026-08-10T04:30:00Z",
            },
        }
        owner.append(owner_row)
        human.append(
            {
                "schema_version": "p4.2a-v2-human-adjudicated-item-v1",
                **{
                    key: copy.deepcopy(value)
                    for key, value in blind_row.items()
                    if key not in {"schema_version", "gold"}
                },
                "annotation_status": "completed",
                "annotation_type": "ai_drafted_human_adjudicated",
                "drafted_at": "2026-08-10T04:20:00Z",
                "adjudicated_at": "2026-08-10T04:30:00Z",
                "draft_label": copy.deepcopy(draft_label),
                "gold": copy.deepcopy(draft_label),
                "provenance": {
                    "method": "ai_drafted_human_adjudicated",
                    "design": dict(evaluator.DESIGN_REF),
                    "frame_id": evaluator.FRAME_ID,
                    "blind_input_sha256": blind_row["input_sha256"],
                    "sealed_draft_item_sha256": owner_row["sealed_draft_item_sha256"],
                    "owner_export_item_sha256": _digest(_json_bytes(owner_row)),
                    "drafter_id": evaluator.EXPECTED_DRAFTER_ID,
                    "adjudicator_id": evaluator.EXPECTED_ADJUDICATOR_ID,
                    "human_confirmation": True,
                    "changed": False,
                    "changed_fields": [],
                },
            }
        )
    payloads["draft"] = _write_jsonl(paths.draft, drafts)
    payloads["adjudication_ui"] = evaluator._render_expected_adjudication_ui(
        blind,
        drafts,
        control_root=evaluator.PROJECT_ROOT,
        paths=paths,
        selection_payload=payloads["selection"],
        blind_payload=payloads["blind"],
        draft_payload=payloads["draft"],
    )
    paths.adjudication_ui.write_bytes(payloads["adjudication_ui"])
    payloads["owner_export"] = _write_jsonl(paths.owner_export, owner)
    payloads["human_adjudicated"] = _write_jsonl(paths.human_adjudicated, human)
    chain_summary: JsonObject = {
        "drafter_id": evaluator.EXPECTED_DRAFTER_ID,
        "adjudicator_id": evaluator.EXPECTED_ADJUDICATOR_ID,
        "row_count": 60,
        "all_items_human_confirmed": True,
        "changed_item_count": 0,
        "unchanged_item_count": 60,
        "changed_field_counts": {field: 0 for field in evaluator._LABEL_FIELDS},
        "drafted_at": "2026-08-10T04:20:00Z",
        "earliest_adjudicated_at": "2026-08-10T04:30:00Z",
        "latest_adjudicated_at": "2026-08-10T04:30:00Z",
    }

    def artifact(path: Path, payload_name: str, row_count: int | None = None) -> JsonObject:
        result: JsonObject = {
            "path": relative(path),
            "sha256": _digest(payloads[payload_name]),
        }
        if row_count is not None:
            result["row_count"] = row_count
        return result

    completion: JsonObject = {
        "schema_version": "p4.2a-v2-owner-completion-manifest-v1",
        "design": dict(evaluator.DESIGN_REF),
        "frame_id": evaluator.FRAME_ID,
        "completed_at": "2026-08-10T04:40:00Z",
        "artifacts": {
            "private_selection": artifact(paths.selection, "selection"),
            "owner_blind": artifact(paths.blind, "blind", 60),
            "ai_draft": artifact(paths.draft, "draft", 60),
            "adjudication_ui": artifact(paths.adjudication_ui, "adjudication_ui"),
            "owner_raw_export": artifact(paths.owner_export, "owner_export", 60),
            "human_adjudicated": artifact(paths.human_adjudicated, "human_adjudicated", 60),
        },
        "provenance": chain_summary,
        "validation": copy.deepcopy(evaluator._COMPLETION_VALIDATION),
        "model_execution": {
            "drafting_ai_inference_occurred": True,
            "drafting_ai": evaluator.EXPECTED_DRAFTER_ID,
            "drafting_ai_is_evaluated_model": False,
            "selected_model": evaluator.MODEL,
            "selected_model_candidate_inference_count": len(candidates),
            "selected_model_candidate_failure_count": 0,
            "final_one_shot_evaluation_calls": 0,
            "workflow_script_model_calls": 0,
        },
        "heldout_touched": True,
        "safety": {
            "production_database_writes": 0,
            "proposals_or_orders_created": False,
            "one_shot_evaluation_consumed": False,
            "p4_2a_done": False,
            "p4_2b_unlocked": False,
            "p4_3_unlocked": False,
        },
    }
    _write_json(paths.owner_completion, completion)
    return paths


def _clock() -> datetime:
    return datetime(2026, 8, 10, 5, 0, tzinfo=UTC)


def _authorization(
    tmp_path: Path,
    paths: evaluator.ArtifactPaths,
    *,
    reviewer_id: str = evaluator.EXPECTED_REVIEWER_ID,
) -> tuple[Path, str, Path]:
    receipt_path = tmp_path / "independent-dry-run-receipt.json"
    receipt_payload = _write_json(receipt_path, evaluator.dry_run(paths=paths, clock=_clock))
    receipt = _read_json(receipt_path)
    path = tmp_path / "independent-review-secret-source.json"
    payload = _write_json(
        path,
        {
            "schema_version": "p4.2a-v2-heldout-evaluation-independent-review-v1",
            "decision": "APPROVE_ONE_SHOT_EVALUATION",
            "preregistration_sha256": evaluator.PREREGISTRATION_SHA256,
            "design_sha256": evaluator.DESIGN_SHA256,
            "input_hashes": receipt["validated_input_hashes"],
            "dry_run_receipt": {
                "path": receipt_path.name,
                "sha256": _digest(receipt_payload),
            },
            "reviewer": {
                "reviewer_id": reviewer_id,
                "reviewer_type": evaluator.EXPECTED_REVIEWER_TYPE,
                "reviewer_role": evaluator.EXPECTED_REVIEWER_ROLE,
                "reviewer_model": evaluator.EXPECTED_REVIEWER_MODEL,
                "independent": True,
            },
            "authorization": {
                "selected_model": evaluator.MODEL,
                "one_shot_count": 1,
                "zero_retries": True,
                "formal_evaluation_allowed": True,
            },
        },
    )
    return path, _digest(payload), receipt_path


def _rewrite_json(path: Path, tamper: Tamper) -> None:
    value = _read_json(path)
    tamper(value)
    _write_json(path, value)


def _rewrite_jsonl(path: Path, tamper: Tamper) -> None:
    rows = _read_jsonl(path)
    tamper(rows)
    _write_jsonl(path, rows)


def _rebind_blind_payload_for_schema_test(paths: evaluator.ArtifactPaths) -> None:
    selection = _read_json(paths.selection)
    cast(dict[str, Any], selection["owner_delivery"])["sha256"] = _digest(paths.blind.read_bytes())
    _write_json(paths.selection, selection)
    completion = _read_json(paths.owner_completion)
    artifacts = cast(dict[str, Any], completion["artifacts"])
    cast(dict[str, Any], artifacts["owner_blind"])["sha256"] = _digest(paths.blind.read_bytes())
    cast(dict[str, Any], artifacts["private_selection"])["sha256"] = _digest(
        paths.selection.read_bytes()
    )
    _write_json(paths.owner_completion, completion)


def test_control_bundle_binds_every_frozen_control_and_source_lineage_file() -> None:
    controls = evaluator.load_control_bundle()
    assert controls.control_hashes == {
        "preregistration": evaluator.PREREGISTRATION_SHA256,
        "design": evaluator.DESIGN_SHA256,
        "selection_outcome": evaluator.SELECTION_OUTCOME_SHA256,
        "selected_freeze": evaluator.SELECTED_FREEZE_SHA256,
        "heldout_contract": evaluator.HELDOUT_CONTRACT_SHA256,
        "round3_contract": evaluator.ROUND3_CONTRACT_SHA256,
        "prompt": evaluator.PROMPT_SHA256,
        "owner_amendment": evaluator.OWNER_AMENDMENT_SHA256,
        "cost_correction": evaluator.COST_CORRECTION_SHA256,
        "contract_schema": "c106cd15bd974de19ecc01d6e99e8f39c39fbf14df3a3b4dc74ee9b08ff6dd66",
        "contract_materialized_schema": (
            "0ac68654ce23ecd4e537d849d695e092c76dcb9de0fb03793e65ae62b181947f"
        ),
        "source_lineage_round3_evidence": (
            "57f9b99e99358b5e8c596485774702858e5c572cb3116407a279d0195b044318"
        ),
        "source_lineage_round3_independent_review": (
            "db68d926bd02a273daf88740c93b49bec413ff95160852f1ad1cacbaa82360c5"
        ),
        "source_lineage_incremental_evidence": (
            "b60946d37ebc687e8f7c7861ede743f5fba98844ba331d0bbfb7c19f4a0de7d7"
        ),
        "source_lineage_incremental_independent_review": (
            "c65c909c19bd0914cc8fcb5d098ca4b21454a0db19e223fd807647a92928979a"
        ),
        "retired_selection": "9da50ea8720b01b58c6d19eb9d7b11705a0c561c61da432543e2ab5644b3abe1",
    }
    assert len(controls.retired_ids) == 40


@_ISOLATED_CONSUMER
def test_dry_run_validates_full_real_chain_but_scores_only_synthetic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture_bundle(tmp_path)
    input_paths = (
        paths.materialized_inputs,
        paths.materialization_manifest,
        paths.inference_state,
        paths.predictions,
        paths.prediction_manifest,
        paths.selection,
        paths.blind,
        paths.draft,
        paths.adjudication_ui,
        paths.owner_export,
        paths.human_adjudicated,
        paths.owner_completion,
    )
    before = {path: path.read_bytes() for path in input_paths}
    original = cast(Callable[..., JsonObject], evaluator.score_heldout)
    observed: dict[str, bool] = {}

    def synthetic_only(*args: Any, **kwargs: Any) -> JsonObject:
        human = args[2]
        assert all(row.get("synthetic_metric_fixture") is True for row in human)
        observed["synthetic"] = True
        return original(*args, **kwargs)

    monkeypatch.setattr(evaluator, "score_heldout", synthetic_only)
    result = evaluator.dry_run(paths=paths, clock=_clock)

    assert result["status"] == "passed"
    assert result["stages"] == evaluator._DRY_RUN_STAGES
    assert result["real_heldout_metrics_computed"] is False
    assert result["one_shot_consumed"] is False
    assert result["filesystem_mutations"] == 0
    assert observed == {"synthetic": True}
    assert not paths.evaluation_state.exists()
    assert not paths.report.exists()
    assert before == {path: path.read_bytes() for path in before}


@_ISOLATED_CONSUMER
def test_score_uses_registered_40_positive_and_20_negative_partitions(
    tmp_path: Path,
) -> None:
    paths = _fixture_bundle(tmp_path)
    preflight = evaluator.load_preflight(paths=paths)
    score = evaluator.score_heldout(
        preflight.selected, preflight.predictions_by_id, preflight.human
    )
    assert score["confusion_matrix"] == {"tp": 40, "fp": 0, "fn": 0, "tn": 20}
    assert score["materiality_precision"]["denominator"] == 40
    assert score["materiality_false_omission_rate"]["denominator"] == 20
    assert score["materiality_recall"]["value"] is None
    assert score["symbol_exact_set_accuracy"]["passed"] is True


@_parametrize(
    ("artifact", "tamper", "match"),
    [
        (
            "materialized_inputs",
            lambda rows: rows[0].__setitem__("model", "other-model"),
            "candidate identity/schema",
        ),
        (
            "materialized_inputs",
            lambda rows: rows.append(copy.deepcopy(rows[0])),
            "candidate identity/schema|full eligible prediction coverage",
        ),
        (
            "materialized_inputs",
            lambda rows: cast(dict[str, Any], rows[0]["body_evidence"]).__setitem__(
                "url", "https://example.invalid/tampered"
            ),
            "body evidence drifted",
        ),
        (
            "predictions",
            lambda rows: rows[0].__setitem__("extra", True),
            "prediction row 1 fields",
        ),
        (
            "predictions",
            lambda rows: rows[0].__setitem__("source", "cninfo"),
            "source/join fields",
        ),
        (
            "predictions",
            lambda rows: rows[0].__setitem__("status", "failed"),
            "successful row",
        ),
        (
            "predictions",
            lambda rows: cast(dict[str, Any], rows[0]["prediction"]).__setitem__(
                "evidence_span", "not present"
            ),
            "materialized result schema",
        ),
        (
            "predictions",
            lambda rows: rows.pop(),
            "full eligible prediction coverage",
        ),
        (
            "inference_state",
            lambda rows: rows[0].__setitem__("preregistration_sha256", "f" * 64),
            "execution/prereg/contract/snapshot chain",
        ),
        (
            "inference_state",
            lambda rows: cast(dict[str, Any], rows[1]["production_snapshot_after"]).__setitem__(
                "trade_proposal_count", 1
            ),
            "execution/prereg/contract/snapshot chain",
        ),
        (
            "blind",
            lambda rows: rows[0].__setitem__("schema_version", "p4.2a-v2-owner-blind-item-v1"),
            "blind row leaks frozen metadata",
        ),
        (
            "blind",
            lambda rows: cast(dict[str, Any], rows[0]["body_evidence"]).__setitem__(
                "prediction", {"materiality": 2}
            ),
            "blind row.*invalid|blind row leaks",
        ),
        (
            "draft",
            lambda rows: rows[0].__setitem__("drafter_id", evaluator.MODEL),
            "registered Codex drafter",
        ),
        (
            "owner_export",
            lambda rows: rows[0].__setitem__("sealed_draft_item_sha256", "f" * 64),
            "sealed-draft binding",
        ),
        (
            "owner_export",
            lambda rows: cast(dict[str, Any], rows[0]["adjudication"]).__setitem__(
                "adjudicator_id", evaluator.EXPECTED_DRAFTER_ID
            ),
            "adjudication provenance",
        ),
        (
            "human_adjudicated",
            lambda rows: cast(dict[str, Any], rows[0]["provenance"]).__setitem__(
                "owner_export_item_sha256", "f" * 64
            ),
            "canonical provenance",
        ),
    ],
)
@_ISOLATED_CONSUMER
def test_jsonl_tamper_classes_fail_closed(
    tmp_path: Path, artifact: str, tamper: Tamper, match: str
) -> None:
    paths = _fixture_bundle(tmp_path)
    _rewrite_jsonl(cast(Path, getattr(paths, artifact)), tamper)
    if artifact == "blind":
        _rebind_blind_payload_for_schema_test(paths)
    with pytest.raises(evaluator.HeldoutEvaluationError, match=match):
        evaluator.load_preflight(paths=paths)


@_parametrize(
    "recorded_times",
    [
        ("2026-08-10T03:59:59Z", "2026-08-10T04:05:00Z"),
        ("2026-08-10T04:06:00Z", "2026-08-10T04:05:00Z"),
        ("2026-08-10T04:05:00Z", "2026-08-10T04:11:00Z"),
    ],
)
@_ISOLATED_CONSUMER
def test_prediction_recorded_times_must_close_inside_ordered_inference_window(
    tmp_path: Path,
    recorded_times: tuple[str, str],
) -> None:
    paths = _fixture_bundle(tmp_path)
    rows = _read_jsonl(paths.predictions)
    rows[0]["recorded_at_utc"], rows[1]["recorded_at_utc"] = recorded_times
    _write_jsonl(paths.predictions, rows)

    with pytest.raises(evaluator.HeldoutEvaluationError, match="recorded_at timeline"):
        evaluator.load_preflight(paths=paths)


@_ISOLATED_CONSUMER
def test_materialization_rejects_coherent_eligible_identity_relink(
    tmp_path: Path,
) -> None:
    paths = _fixture_bundle(tmp_path)
    manifest = _read_json(paths.materialization_manifest)
    all_rows = cast(
        list[dict[str, Any]],
        cast(dict[str, Any], manifest["layers"])["all_candidates"],
    )
    first_identity = {
        field: all_rows[0][field] for field in ("source", "url", "content_hash")
    }
    second_identity = {
        field: all_rows[1][field] for field in ("source", "url", "content_hash")
    }
    all_rows[0].update(second_identity)
    all_rows[1].update(first_identity)
    _write_json(paths.materialization_manifest, manifest)

    with pytest.raises(
        evaluator.HeldoutEvaluationError,
        match="eligible identity differs from all-candidate layer",
    ):
        evaluator.load_preflight(paths=paths)


@_parametrize(
    "available_time",
    ["2026-08-05T15:59:59Z", "2026-08-08T16:00:00Z"],
)
@_ISOLATED_CONSUMER
def test_materialization_rejects_candidate_outside_frozen_source_window(
    tmp_path: Path,
    available_time: str,
) -> None:
    paths = _fixture_bundle(tmp_path)
    candidates = _read_jsonl(paths.materialized_inputs)
    candidates[0]["available_time"] = available_time
    _write_jsonl(paths.materialized_inputs, candidates)

    with pytest.raises(evaluator.HeldoutEvaluationError, match="outside the frozen source window"):
        evaluator.load_preflight(paths=paths)


@_ISOLATED_CONSUMER
def test_materialization_rejects_coherent_raw_source_composition_tamper(
    tmp_path: Path,
) -> None:
    paths = _fixture_bundle(tmp_path)
    manifest = _read_json(paths.materialization_manifest)
    all_rows = cast(
        list[dict[str, Any]],
        cast(dict[str, Any], manifest["layers"])["all_candidates"],
    )
    ineligible_cninfo = next(
        row
        for row in all_rows
        if row["source"] == "cninfo" and cast(int, row["news_item_id"]) >= 2_000_000
    )
    ineligible_cninfo["source"] = "akshare_ths"
    _write_json(paths.materialization_manifest, manifest)

    with pytest.raises(
        evaluator.HeldoutEvaluationError,
        match="materialization raw source composition drifted",
    ):
        evaluator.load_preflight(paths=paths)


@_ISOLATED_CONSUMER
def test_materialization_manifest_v1_is_rejected_by_v2_consumer(tmp_path: Path) -> None:
    paths = _fixture_bundle(tmp_path)
    manifest = _read_json(paths.materialization_manifest)
    manifest["schema_version"] = "p4.2a-v2-heldout-materialization-manifest-v1"
    _write_json(paths.materialization_manifest, manifest)

    with pytest.raises(
        evaluator.HeldoutEvaluationError,
        match="materialization header/control binding drifted",
    ):
        evaluator.load_preflight(paths=paths)


@_parametrize(
    ("section", "tamper", "match"),
    [
        (
            "execution_authority",
            lambda value: value.__setitem__("release_authorization", {}),
            "nonrecursive",
        ),
        (
            "request_pacing",
            lambda value: cast(dict[str, Any], value["cninfo_pdf"]).__setitem__(
                "minimum_observed_start_to_start_seconds", 0.999
            ),
            "request pacing evidence drifted",
        ),
        (
            "runtime_start_preflight",
            lambda value: value.__setitem__("host_probe_performed", True),
            "offline runtime start preflight drifted",
        ),
    ],
)
@_ISOLATED_CONSUMER
def test_materialization_manifest_v2_new_sections_fail_closed(
    tmp_path: Path,
    section: str,
    tamper: Tamper,
    match: str,
) -> None:
    paths = _fixture_bundle(tmp_path)
    manifest = _read_json(paths.materialization_manifest)
    tamper(cast(dict[str, Any], manifest[section]))
    _write_json(paths.materialization_manifest, manifest)

    with pytest.raises(evaluator.HeldoutEvaluationError, match=match):
        evaluator.load_preflight(paths=paths)


def test_evaluation_stage_gate_runs_before_any_heldout_input_read(
    tmp_path: Path,
) -> None:
    paths = _artifact_paths(tmp_path / "must-not-be-read")

    with pytest.raises(
        evaluator.HeldoutEvaluationError,
        match="held-out evaluation remains authority-gated: held-out evaluation remains locked",
    ):
        evaluator.load_preflight(paths=paths)
    assert paths.artifact_root is not None
    assert paths.artifact_root.exists() is False


@_ISOLATED_CONSUMER
def test_candidate_contract_hash_recomputation_rejects_coherent_artifact_relink(
    tmp_path: Path,
) -> None:
    paths = _fixture_bundle(tmp_path)
    candidates = _read_jsonl(paths.materialized_inputs)
    candidates[0]["title"] = "attacker changed title but retained frozen serializer hashes"
    inputs_payload = _write_jsonl(paths.materialized_inputs, candidates)

    manifest = _read_json(paths.materialization_manifest)
    artifacts = cast(dict[str, Any], manifest["artifacts"])
    cast(dict[str, Any], artifacts["eligible_inputs_jsonl"])["sha256"] = _digest(
        inputs_payload
    )
    manifest_payload = _write_json(paths.materialization_manifest, manifest)

    prediction_manifest = _read_json(paths.prediction_manifest)
    prediction_manifest["materialization_manifest_sha256"] = _digest(manifest_payload)
    prediction_manifest_payload = _write_json(paths.prediction_manifest, prediction_manifest)
    states = _read_jsonl(paths.inference_state)
    states[0]["materialization_manifest_sha256"] = _digest(manifest_payload)
    states[1]["materialization_manifest_sha256"] = _digest(manifest_payload)
    states[1]["prediction_manifest_sha256"] = _digest(prediction_manifest_payload)
    _write_jsonl(paths.inference_state, states)

    with pytest.raises(
        evaluator.HeldoutEvaluationError,
        match="materialized candidate contract/body binding drifted",
    ):
        evaluator.load_preflight(paths=paths)


@_ISOLATED_CONSUMER
def test_inference_must_start_after_preregistration_and_source_window(
    tmp_path: Path,
) -> None:
    paths = _fixture_bundle(tmp_path)
    states = _read_jsonl(paths.inference_state)
    states[0]["started_at_utc"] = "2026-08-10T03:49:59Z"
    _write_jsonl(paths.inference_state, states)

    with pytest.raises(
        evaluator.HeldoutEvaluationError,
        match="inference start precedes preregistration or source-window closure",
    ):
        evaluator.load_preflight(paths=paths)


@_ISOLATED_CONSUMER
def test_boolean_readonly_integer_evidence_fails_closed(tmp_path: Path) -> None:
    paths = _fixture_bundle(tmp_path)
    states = _read_jsonl(paths.inference_state)
    cast(dict[str, Any], states[0]["production_snapshot_before"])[
        "pragma_query_only"
    ] = True
    _write_jsonl(paths.inference_state, states)
    with pytest.raises(evaluator.HeldoutEvaluationError, match="non-negative integer"):
        evaluator.load_preflight(paths=paths)

    paths = _fixture_bundle(tmp_path / "materialization")
    manifest = _read_json(paths.materialization_manifest)
    cast(dict[str, Any], manifest["production_database"])["pragma_query_only"] = True
    _write_json(paths.materialization_manifest, manifest)
    with pytest.raises(evaluator.HeldoutEvaluationError, match="non-negative integer"):
        evaluator.load_preflight(paths=paths)


@_parametrize(
    ("artifact", "tamper", "match"),
    [
        (
            "materialization_manifest",
            lambda value: cast(dict[str, Any], value["counts"]).__setitem__(
                "raw_source_window", 4047
            ),
            "materialization counts",
        ),
        (
            "materialization_manifest",
            lambda value: cast(dict[str, Any], value["lineage"]).__setitem__(
                "retired_selection_sha256", "f" * 64
            ),
            "materialization header/control binding",
        ),
        (
            "materialization_manifest",
            lambda value: cast(
                list[dict[str, Any]],
                cast(dict[str, Any], value["layers"])["all_candidates"],
            )[0].__setitem__("extra", True),
            "materialization all candidate fields",
        ),
        (
            "prediction_manifest",
            lambda value: value.__setitem__("contract_sha256", "f" * 64),
            "execution/prereg/contract/snapshot chain",
        ),
        (
            "selection",
            lambda value: cast(dict[str, Any], value["selection"]).__setitem__(
                "seed", "attacker-seed"
            ),
            "deterministic without-replacement selection",
        ),
        (
            "selection",
            lambda value: cast(dict[str, Any], value["selection"]).__setitem__(
                "without_replacement", False
            ),
            "deterministic without-replacement selection",
        ),
        (
            "selection",
            lambda value: cast(
                list[dict[str, Any]], cast(dict[str, Any], value["selection"])["selected"]
            )[0].__setitem__("selection_rank_sha256", "f" * 64),
            "deterministic without-replacement selection",
        ),
        (
            "selection",
            lambda value: cast(dict[str, Any], value["audit"]).__setitem__(
                "available_by_stratum",
                {"predicted_positive": 49, "predicted_negative": 31},
            ),
            "selection audit",
        ),
        (
            "selection",
            lambda value: cast(
                dict[str, Any],
                cast(dict[str, Any], value["source_lineage"])["materialized_inputs"],
            ).__setitem__("sha256", "f" * 64),
            "selection full source lineage",
        ),
        (
            "owner_completion",
            lambda value: cast(dict[str, Any], value["validation"]).__setitem__(
                "heldout_40_20_partition_check", False
            ),
            "completion validation",
        ),
        (
            "owner_completion",
            lambda value: cast(dict[str, Any], value["validation"]).pop(
                "full_candidate_inference_success_check"
            ),
            "completion validation",
        ),
        (
            "owner_completion",
            lambda value: cast(dict[str, Any], value["model_execution"]).__setitem__(
                "selected_model_candidate_failure_count", 1
            ),
            "completion model execution",
        ),
    ],
)
@_ISOLATED_CONSUMER
def test_json_manifest_tamper_classes_fail_closed(
    tmp_path: Path, artifact: str, tamper: Tamper, match: str
) -> None:
    paths = _fixture_bundle(tmp_path)
    _rewrite_json(cast(Path, getattr(paths, artifact)), tamper)
    with pytest.raises(evaluator.HeldoutEvaluationError, match=match):
        evaluator.load_preflight(paths=paths)


@_ISOLATED_CONSUMER
def test_adjudication_ui_bytes_are_bound_by_completion(tmp_path: Path) -> None:
    paths = _fixture_bundle(tmp_path)
    paths.adjudication_ui.write_text("tampered UI")
    with pytest.raises(evaluator.HeldoutEvaluationError, match="adjudication_ui"):
        evaluator.load_preflight(paths=paths)


@_ISOLATED_CONSUMER
def test_adjudication_ui_is_rerendered_instead_of_trusting_completion_hash(
    tmp_path: Path,
) -> None:
    paths = _fixture_bundle(tmp_path)
    paths.adjudication_ui.write_bytes(paths.adjudication_ui.read_bytes() + b"<!-- tampered -->\n")
    completion = _read_json(paths.owner_completion)
    artifacts = cast(dict[str, Any], completion["artifacts"])
    cast(dict[str, Any], artifacts["adjudication_ui"])["sha256"] = _digest(
        paths.adjudication_ui.read_bytes()
    )
    _write_json(paths.owner_completion, completion)

    with pytest.raises(
        evaluator.HeldoutEvaluationError,
        match="adjudication_ui differs from deterministic frozen-input rendering",
    ):
        evaluator.load_preflight(paths=paths)


@_ISOLATED_CONSUMER
def test_coherently_rebound_draft_must_not_precede_inference_completion(
    tmp_path: Path,
) -> None:
    paths = _fixture_bundle(tmp_path)
    early_draft = "2026-08-10T04:09:59Z"
    drafts = _read_jsonl(paths.draft)
    for draft_row in drafts:
        draft_row["drafted_at"] = early_draft
    draft_payload = _write_jsonl(paths.draft, drafts)

    owner = _read_jsonl(paths.owner_export)
    for draft_row, owner_row in zip(drafts, owner, strict=True):
        owner_row["sealed_draft_item_sha256"] = _digest(_json_bytes(draft_row))
    owner_payload = _write_jsonl(paths.owner_export, owner)

    human = _read_jsonl(paths.human_adjudicated)
    for _draft_row, owner_row, human_row in zip(drafts, owner, human, strict=True):
        human_row["drafted_at"] = early_draft
        provenance = cast(dict[str, Any], human_row["provenance"])
        provenance["sealed_draft_item_sha256"] = owner_row["sealed_draft_item_sha256"]
        provenance["owner_export_item_sha256"] = _digest(_json_bytes(owner_row))
    human_payload = _write_jsonl(paths.human_adjudicated, human)

    blind = _read_jsonl(paths.blind)
    ui_payload = evaluator._render_expected_adjudication_ui(
        blind,
        drafts,
        control_root=evaluator.PROJECT_ROOT,
        paths=paths,
        selection_payload=paths.selection.read_bytes(),
        blind_payload=paths.blind.read_bytes(),
        draft_payload=draft_payload,
    )
    paths.adjudication_ui.write_bytes(ui_payload)

    completion = _read_json(paths.owner_completion)
    artifacts = cast(dict[str, Any], completion["artifacts"])
    for name, payload in (
        ("ai_draft", draft_payload),
        ("adjudication_ui", ui_payload),
        ("owner_raw_export", owner_payload),
        ("human_adjudicated", human_payload),
    ):
        cast(dict[str, Any], artifacts[name])["sha256"] = _digest(payload)
    cast(dict[str, Any], completion["provenance"])["drafted_at"] = early_draft
    _write_json(paths.owner_completion, completion)

    with pytest.raises(
        evaluator.HeldoutEvaluationError,
        match="draft precedes inference completion",
    ):
        evaluator.load_preflight(paths=paths)


@_ISOLATED_CONSUMER
def test_adjudication_timestamps_may_follow_owner_navigation_order(tmp_path: Path) -> None:
    paths = _fixture_bundle(tmp_path)
    owner = _read_jsonl(paths.owner_export)
    cast(dict[str, Any], owner[0]["adjudication"])["adjudicated_at"] = (
        "2026-08-10T04:35:00Z"
    )
    cast(dict[str, Any], owner[1]["adjudication"])["adjudicated_at"] = (
        "2026-08-10T04:32:00Z"
    )
    owner_payload = _write_jsonl(paths.owner_export, owner)

    human = _read_jsonl(paths.human_adjudicated)
    for owner_row, human_row in zip(owner, human, strict=True):
        adjudicated_at = cast(dict[str, Any], owner_row["adjudication"])["adjudicated_at"]
        human_row["adjudicated_at"] = adjudicated_at
        cast(dict[str, Any], human_row["provenance"])["owner_export_item_sha256"] = _digest(
            _json_bytes(owner_row)
        )
    human_payload = _write_jsonl(paths.human_adjudicated, human)

    completion = _read_json(paths.owner_completion)
    artifacts = cast(dict[str, Any], completion["artifacts"])
    cast(dict[str, Any], artifacts["owner_raw_export"])["sha256"] = _digest(owner_payload)
    cast(dict[str, Any], artifacts["human_adjudicated"])["sha256"] = _digest(human_payload)
    provenance = cast(dict[str, Any], completion["provenance"])
    provenance["earliest_adjudicated_at"] = "2026-08-10T04:30:00Z"
    provenance["latest_adjudicated_at"] = "2026-08-10T04:35:00Z"
    _write_json(paths.owner_completion, completion)

    preflight = evaluator.load_preflight(paths=paths)
    assert len(preflight.human) == evaluator.EXPECTED_COUNT


@_ISOLATED_CONSUMER
def test_declared_artifact_root_rejects_path_escape(tmp_path: Path) -> None:
    paths = _fixture_bundle(tmp_path)
    escaped = evaluator.ArtifactPaths(
        materialized_inputs=paths.materialized_inputs,
        materialization_manifest=paths.materialization_manifest,
        inference_state=paths.inference_state,
        predictions=paths.predictions,
        prediction_manifest=paths.prediction_manifest,
        selection=paths.selection,
        blind=paths.blind,
        draft=paths.draft,
        adjudication_ui=paths.adjudication_ui,
        owner_export=paths.owner_export,
        human_adjudicated=paths.human_adjudicated,
        owner_completion=paths.owner_completion,
        evaluation_state=paths.evaluation_state,
        report=tmp_path.parent / "escaped-report.json",
        artifact_root=tmp_path,
    )
    with pytest.raises(evaluator.HeldoutEvaluationError, match="escapes"):
        evaluator.load_preflight(paths=escaped)


@_ISOLATED_CONSUMER
def test_authorization_requires_distinct_identity_current_hashes_and_receipt(
    tmp_path: Path,
) -> None:
    paths = _fixture_bundle(tmp_path)
    review, digest, _receipt = _authorization(
        tmp_path, paths, reviewer_id=evaluator.EXPECTED_ADJUDICATOR_ID
    )
    with pytest.raises(evaluator.HeldoutEvaluationError, match="not sufficient"):
        evaluator.formal_evaluate(
            paths=paths,
            authorization_path=review,
            authorization_sha256=digest,
            clock=_clock,
        )
    assert not paths.evaluation_state.exists()

    review, digest, _receipt = _authorization(
        tmp_path, paths, reviewer_id="independent-reviewer"
    )
    with pytest.raises(evaluator.HeldoutEvaluationError, match="not sufficient"):
        evaluator.formal_evaluate(
            paths=paths,
            authorization_path=review,
            authorization_sha256=digest,
            clock=_clock,
        )
    assert not paths.evaluation_state.exists()

    review, _digest_before, _receipt = _authorization(tmp_path, paths)
    authorization = _read_json(review)
    cast(dict[str, Any], authorization["input_hashes"])["blind"] = "f" * 64
    mutated = _write_json(review, authorization)
    with pytest.raises(evaluator.HeldoutEvaluationError, match="not sufficient"):
        evaluator.formal_evaluate(
            paths=paths,
            authorization_path=review,
            authorization_sha256=_digest(mutated),
            clock=_clock,
        )
    assert not paths.evaluation_state.exists()


@_parametrize(
    ("field", "value"),
    [
        ("reviewer_type", "human"),
        ("reviewer_role", "independent_reviewer"),
        ("reviewer_model", "claude-other"),
    ],
)
@_ISOLATED_CONSUMER
def test_authorization_requires_exact_registered_reviewer_object(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    paths = _fixture_bundle(tmp_path)
    review, _digest_before, _receipt = _authorization(tmp_path, paths)
    authorization = _read_json(review)
    cast(dict[str, Any], authorization["reviewer"])[field] = value
    payload = _write_json(review, authorization)

    with pytest.raises(evaluator.HeldoutEvaluationError, match="not sufficient"):
        evaluator.formal_evaluate(
            paths=paths,
            authorization_path=review,
            authorization_sha256=_digest(payload),
            clock=_clock,
        )
    assert not paths.evaluation_state.exists()


@_ISOLATED_CONSUMER
def test_authorization_integer_booleans_fail_closed(tmp_path: Path) -> None:
    paths = _fixture_bundle(tmp_path)
    review, _digest_before, receipt = _authorization(tmp_path, paths)
    authorization = _read_json(review)
    cast(dict[str, Any], authorization["authorization"])["one_shot_count"] = True
    payload = _write_json(review, authorization)
    with pytest.raises(evaluator.HeldoutEvaluationError, match="non-negative integer"):
        evaluator.formal_evaluate(
            paths=paths,
            authorization_path=review,
            authorization_sha256=_digest(payload),
            clock=_clock,
        )
    assert not paths.evaluation_state.exists()

    review, _digest_before, receipt = _authorization(tmp_path / "receipt", paths)
    receipt_value = _read_json(receipt)
    receipt_value["filesystem_mutations"] = False
    receipt_payload = _write_json(receipt, receipt_value)
    authorization = _read_json(review)
    cast(dict[str, Any], authorization["dry_run_receipt"])["sha256"] = _digest(
        receipt_payload
    )
    payload = _write_json(review, authorization)
    with pytest.raises(evaluator.HeldoutEvaluationError, match="non-negative integer"):
        evaluator.formal_evaluate(
            paths=paths,
            authorization_path=review,
            authorization_sha256=_digest(payload),
            clock=_clock,
        )
    assert not paths.evaluation_state.exists()

    review, _digest_before, receipt = _authorization(tmp_path, paths)
    receipt_value = _read_json(receipt)
    receipt_value["stages"] = ["frozen_controls"]
    _write_json(receipt, receipt_value)
    authorization = _read_json(review)
    cast(dict[str, Any], authorization["dry_run_receipt"])["sha256"] = _digest(receipt.read_bytes())
    mutated = _write_json(review, authorization)
    with pytest.raises(evaluator.HeldoutEvaluationError, match="receipt is not sufficient"):
        evaluator.formal_evaluate(
            paths=paths,
            authorization_path=review,
            authorization_sha256=_digest(mutated),
            clock=_clock,
        )
    assert not paths.evaluation_state.exists()


@_ISOLATED_CONSUMER
def test_authorization_schema_rejects_arbitrary_secret_fields(tmp_path: Path) -> None:
    paths = _fixture_bundle(tmp_path)
    review, _digest_before, _receipt = _authorization(tmp_path, paths)
    value = _read_json(review)
    value["api_key"] = "must-never-enter-report"
    payload = _write_json(review, value)
    with pytest.raises(evaluator.HeldoutEvaluationError, match="fields drifted"):
        evaluator.formal_evaluate(
            paths=paths,
            authorization_path=review,
            authorization_sha256=_digest(payload),
            clock=_clock,
        )
    assert not paths.evaluation_state.exists()


@_ISOLATED_CONSUMER
def test_formal_claims_once_reports_safe_authorization_projection_and_terminalizes(
    tmp_path: Path,
) -> None:
    paths = _fixture_bundle(tmp_path)
    review, digest, receipt = _authorization(tmp_path, paths)
    report = evaluator.formal_evaluate(
        paths=paths,
        authorization_path=review,
        authorization_sha256=digest,
        clock=_clock,
    )
    events = _read_jsonl(paths.evaluation_state)
    assert [event["event"] for event in events] == [
        "evaluation_started",
        "evaluation_completed",
    ]
    authorization = cast(Mapping[str, Any], report["authorization"])
    assert set(authorization) == {
        "schema_version",
        "decision",
        "authorization_sha256",
        "reviewer_id",
        "reviewer_type",
        "reviewer_role",
        "reviewer_model",
        "independent",
        "selected_model",
        "one_shot_count",
        "zero_retries",
        "formal_evaluation_allowed",
        "input_hashes",
        "dry_run_receipt_sha256",
    }
    serialized = paths.report.read_text()
    assert review.name not in serialized
    assert receipt.name not in serialized
    assert report["real_heldout_metrics_computed"] is True
    assert report["phase_gates"]["p4_2a_done"] is False
    assert report["phase_gates"]["p4_2b_unlocked"] is False
    assert report["phase_gates"]["p4_3_unlocked"] is False
    with pytest.raises(evaluator.HeldoutEvaluationError, match="already claimed"):
        evaluator.formal_evaluate(
            paths=paths,
            authorization_path=review,
            authorization_sha256=digest,
            clock=_clock,
        )


@_ISOLATED_CONSUMER
def test_formal_preflight_failure_does_not_claim_state(tmp_path: Path) -> None:
    paths = _fixture_bundle(tmp_path)
    review, digest, _receipt = _authorization(tmp_path, paths)
    paths.human_adjudicated.unlink()
    with pytest.raises(evaluator.HeldoutEvaluationError, match="human gold"):
        evaluator.formal_evaluate(
            paths=paths,
            authorization_path=review,
            authorization_sha256=digest,
            clock=_clock,
        )
    assert not paths.evaluation_state.exists()
    assert not paths.report.exists()


@_ISOLATED_CONSUMER
def test_formal_start_must_follow_owner_completion_without_claiming_state(
    tmp_path: Path,
) -> None:
    paths = _fixture_bundle(tmp_path)
    review, digest, _receipt = _authorization(tmp_path, paths)

    with pytest.raises(
        evaluator.HeldoutEvaluationError,
        match="formal evaluation start precedes owner-chain completion",
    ):
        evaluator.formal_evaluate(
            paths=paths,
            authorization_path=review,
            authorization_sha256=digest,
            clock=lambda: datetime(2026, 8, 10, 4, 39, 59, tzinfo=UTC),
        )
    assert not paths.evaluation_state.exists()
    assert not paths.report.exists()


@_ISOLATED_CONSUMER
def test_failure_after_claim_appends_safe_terminal_and_never_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _fixture_bundle(tmp_path)
    review, digest, _receipt = _authorization(tmp_path, paths)
    original_create = evaluator._create_only

    def fail_report(path: Path, payload: bytes) -> None:
        if path == paths.report:
            raise OSError("secret transport detail")
        original_create(path, payload)

    monkeypatch.setattr(evaluator, "_create_only", fail_report)
    with pytest.raises(OSError, match="secret transport detail"):
        evaluator.formal_evaluate(
            paths=paths,
            authorization_path=review,
            authorization_sha256=digest,
            clock=_clock,
        )
    events = _read_jsonl(paths.evaluation_state)
    assert [event["event"] for event in events] == [
        "evaluation_started",
        "evaluation_failed",
    ]
    assert events[-1]["error_code"] == "OSError"
    assert events[-1]["retry_allowed"] is False
    assert "secret" not in paths.evaluation_state.read_text()


def test_terminal_append_loops_until_every_byte_is_written_and_fsyncs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state.jsonl"
    _write_jsonl(state, [{"event": "evaluation_started"}])
    original_write = os.write
    calls: list[int] = []

    def partial_write(descriptor: int, payload: Any) -> int:
        data = bytes(payload)
        chunk = data[: min(7, len(data))]
        calls.append(len(chunk))
        return original_write(descriptor, chunk)

    monkeypatch.setattr(os, "write", partial_write)
    evaluator._append_terminal(
        state,
        {"event": "evaluation_completed", "passed": True, "retries": 0},
    )
    assert len(calls) > 1
    assert [row["event"] for row in _read_jsonl(state)] == [
        "evaluation_started",
        "evaluation_completed",
    ]


def test_create_only_claim_fsyncs_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory_fsyncs = 0
    original_fsync = os.fsync

    def spy_fsync(descriptor: int) -> None:
        nonlocal directory_fsyncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsyncs += 1
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    target = tmp_path / "claim" / "state.jsonl"
    evaluator._create_only(target, b'{"event":"evaluation_started"}\n')

    assert target.is_file()
    assert directory_fsyncs >= 1
