from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from scripts import build_p4_2a_gold_sample as builder

from alphapilot.llm.p4_news_event import EventExtractContract


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _row(news_item_id: int) -> builder.NewsRow:
    return builder.NewsRow(
        news_item_id=news_item_id,
        source="cninfo",
        ingested_symbol=f"{news_item_id:06d}",
        title=f"announcement {news_item_id}",
        url=(f"https://static.cninfo.com.cn/finalpage/2026-08-05/{news_item_id}.PDF"),
        published_at=datetime(2026, 8, 5, 1, tzinfo=UTC),
        available_time=datetime(2026, 8, 5, 2, tzinfo=UTC),
        content_hash=_sha(f"content-{news_item_id}"),
        raw_payload={},
    )


def _base_contract(tmp_path: Path) -> builder.FrozenContract:
    return builder.FrozenContract(
        path=tmp_path / "config/p4_event_extract_eval_v1.yaml",
        sha256=_sha("annotation-contract"),
        document={
            "schema_version": "p4.2a-event-extract-eval-v1",
            "announcement_body": {
                "allowed_scheme": "https",
                "allowed_host": "static.cninfo.com.cn",
                "follow_redirects": False,
                "tls_verify": True,
                "connect_timeout_seconds": 5.0,
                "read_timeout_seconds": 20.0,
                "max_pdf_bytes": 8 * 1024 * 1024,
                "required_magic": "%PDF-",
                "extractor_command": "pdftotext",
                "extractor_timeout_seconds": 20.0,
                "max_annotation_text_characters": 16_000,
                "minimum_extracted_characters": 80,
            },
        },
    )


def _active_contract(tmp_path: Path) -> EventExtractContract:
    return EventExtractContract(
        path=tmp_path / "config/p4_event_extract_eval_v1_7.yaml",
        sha256=_sha("prediction-contract"),
        document={"schema_version": "p4.2a-event-extract-eval-v1.7"},
        prompt="test prompt",
        schema={},
        model="qwen3.7-flash",
        endpoint="https://example.invalid/v1",
        purpose="p4_news_event_extract",
        timeout=20.0,
        max_tokens=2_000,
        max_retries=0,
        max_items_per_run=2_000,
        max_input_characters=16_000,
        explicit_cache_enabled=False,
        evidence_candidate_selection=True,
        materialized_schema={},
    )


def _design(
    tmp_path: Path,
    *,
    selected_count: int = 40,
) -> builder.FrozenEvaluationDesign:
    artifacts = {
        "prediction_contract_freeze_receipt_json": {"path": "docs/phase4/eval/freeze-receipt.json"},
        "heldout_candidate_inputs_jsonl": {
            "path": "docs/phase4/eval/materialization/candidate-inputs.jsonl"
        },
        "heldout_candidate_materialization_manifest_json": {
            "path": "docs/phase4/eval/materialization/manifest.json"
        },
        "heldout_candidate_predictions_jsonl": {
            "path": "docs/phase4/eval/candidate-predictions.jsonl"
        },
        "heldout_candidate_predictions_manifest_json": {
            "path": "docs/phase4/eval/candidate-predictions.manifest.json"
        },
        "dev_60_frozen_jsonl": {"path": "docs/phase4/eval/dev60.jsonl"},
        "heldout_40_blind_sample_jsonl": {"path": "docs/phase4/eval/heldout40.blind.jsonl"},
        "heldout_selection_manifest_json": {"path": "docs/phase4/eval/heldout40.selection.json"},
        "combined_100_annotations_jsonl": {"path": "docs/phase4/eval/combined100.jsonl"},
    }
    return builder.FrozenEvaluationDesign(
        path=tmp_path / "config/p4_event_evaluation_v1_7.yaml",
        sha256=_sha("evaluation-design"),
        document={
            "schema_version": "p4.2a-evaluation-design-v1.7",
            "artifact_root": "docs/phase4/eval",
            "artifacts": artifacts,
            "splits": {
                "heldout_40": {
                    "sampling": {
                        "algorithm": "sha256_rank_without_replacement",
                        "deterministic_seed": "heldout-test-seed",
                        "selected_count": selected_count,
                    }
                }
            },
            "owner_delivery": {"forbidden_fields": []},
        },
        base_contract=_base_contract(tmp_path),
    )


def _candidate_record(row: builder.NewsRow) -> dict[str, Any]:
    return {
        "news_item_id": row.news_item_id,
        "source": row.source,
        "url": row.url,
        "title": row.title,
        "ingested_symbol": row.ingested_symbol,
        "published_at": row.published_at.isoformat().replace("+00:00", "Z"),
        "available_time": row.available_time.isoformat().replace("+00:00", "Z"),
        "original_text": f"verbatim announcement body {row.news_item_id}",
        "body_state": "announcement_body",
        "content_hash": row.content_hash,
        "input_sha256": _sha(f"active-input-{row.news_item_id}"),
        "declared_input_sha256": _sha(f"declared-input-{row.news_item_id}"),
        "text_sha256": _sha(f"text-{row.news_item_id}"),
        "body_evidence": {"pdf_sha256": _sha(f"pdf-{row.news_item_id}")},
    }


def _materialization(
    rows: list[builder.NewsRow],
    *,
    excluded_id: int,
) -> builder.HeldoutCandidateMaterialization:
    eligible_rows = [row for row in rows if row.news_item_id != excluded_id]
    return builder.HeldoutCandidateMaterialization(
        all_candidates=tuple(
            {
                "news_item_id": row.news_item_id,
                "source": row.source,
                "url": row.url,
                "content_hash": row.content_hash,
            }
            for row in rows
        ),
        eligible_records=tuple(_candidate_record(row) for row in eligible_rows),
        ineligible_candidates=(
            {
                "news_item_id": excluded_id,
                "url": rows[excluded_id - 1].url,
                "reason": "pdf_text_below_min_char_gate",
                "measured_value": 32,
                "gate_value": 80,
                "pdf_sha256": _sha("short-pdf"),
            },
        ),
        reason_counts={"pdf_text_below_min_char_gate": 1},
    )


def test_materialization_binding_and_eligible_rows_are_exactly_manifest_bound(
    tmp_path: Path,
) -> None:
    design = _design(tmp_path, selected_count=2)
    active_contract = _active_contract(tmp_path)
    rows = [_row(news_item_id) for news_item_id in (1, 2, 3)]
    materialization = _materialization(rows, excluded_id=2)
    receipt_sha256 = _sha("freeze-receipt")
    manifest, payload = builder.heldout_materialization_manifest_payload(
        materialization,
        design=design,
        active_contract=active_contract,
        freeze_receipt_sha256=receipt_sha256,
        project_root=tmp_path,
    )
    manifest_path = builder.evaluation_artifact_path(
        design,
        "heldout_candidate_materialization_manifest_json",
        project_root=tmp_path,
    )
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_bytes(payload)
    manifest_sha256 = hashlib.sha256(payload).hexdigest()

    binding = builder.heldout_materialization_binding(
        manifest,
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
        project_root=tmp_path,
    )
    eligible_rows = builder.heldout_eligible_rows_from_materialization(rows, manifest)

    assert binding == {
        "manifest_path": "docs/phase4/eval/materialization/manifest.json",
        "manifest_sha256": manifest_sha256,
        "raw_candidate_count": 3,
        "eligible_candidate_count": 2,
        "ineligible_candidate_count": 1,
        "ineligible_by_reason": {
            "pdf_exceeds_size_bound": 0,
            "pdf_text_below_min_char_gate": 1,
        },
    }
    assert [row.news_item_id for row in eligible_rows] == [1, 3]
    assert 2 not in {row.news_item_id for row in eligible_rows}

    manifest_path.write_bytes(payload + b" ")
    with pytest.raises(builder.GoldSampleError, match="manifest bytes drifted"):
        builder.heldout_materialization_binding(
            manifest,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            project_root=tmp_path,
        )


def test_inference_started_and_terminal_must_share_exact_materialization_binding(
    tmp_path: Path,
) -> None:
    design = _design(tmp_path, selected_count=2)
    active_contract = _active_contract(tmp_path)
    candidate_records = [_candidate_record(_row(news_item_id)) for news_item_id in (1, 3)]
    candidate_inputs_sha256 = _sha("candidate-inputs")
    prediction_manifest_sha256 = _sha("prediction-manifest")
    receipt_sha256 = _sha("freeze-receipt")
    binding = {
        "manifest_path": "docs/phase4/eval/materialization/manifest.json",
        "manifest_sha256": _sha("materialization-manifest"),
        "raw_candidate_count": 3,
        "eligible_candidate_count": 2,
        "ineligible_candidate_count": 1,
        "ineligible_by_reason": {
            "pdf_exceeds_size_bound": 0,
            "pdf_text_below_min_char_gate": 1,
        },
    }
    identity_sha256 = builder._ordered_candidate_identity_sha256(candidate_records)
    state = {
        "events": [
            {
                "event": "inference_started",
                "design_sha256": design.sha256,
                "contract_sha256": active_contract.sha256,
                "freeze_receipt_sha256": receipt_sha256,
                "candidate_inputs_sha256": candidate_inputs_sha256,
                "candidate_identity_sha256": identity_sha256,
                "candidate_count": 2,
                "materialization": binding,
            },
            {
                "event": "inference_completed",
                "design_sha256": design.sha256,
                "contract_sha256": active_contract.sha256,
                "candidate_count": 2,
                "attempted_count": 2,
                "success_count": 2,
                "failure_count": 0,
                "prediction_manifest_sha256": prediction_manifest_sha256,
                "materialization": binding,
            },
        ]
    }

    builder.validate_inference_completion_bindings(
        state,
        design=design,
        active_contract=active_contract,
        receipt_sha256=receipt_sha256,
        candidate_records=candidate_records,
        candidate_inputs_sha256=candidate_inputs_sha256,
        prediction_manifest_sha256=prediction_manifest_sha256,
        attempted_count=2,
        success_count=2,
        failure_count=0,
        materialization_binding=binding,
    )

    state["events"][0]["materialization"] = {
        **binding,
        "eligible_candidate_count": 3,
    }
    with pytest.raises(builder.GoldSampleError, match="started receipt/contract/candidate"):
        builder.validate_inference_completion_bindings(
            state,
            design=design,
            active_contract=active_contract,
            receipt_sha256=receipt_sha256,
            candidate_records=candidate_records,
            candidate_inputs_sha256=candidate_inputs_sha256,
            prediction_manifest_sha256=prediction_manifest_sha256,
            attempted_count=2,
            success_count=2,
            failure_count=0,
            materialization_binding=binding,
        )

    state["events"][0]["materialization"] = binding
    state["events"][1]["materialization"] = {
        **binding,
        "manifest_sha256": _sha("drifted-manifest"),
    }
    with pytest.raises(builder.GoldSampleError, match="terminal manifest/count"):
        builder.validate_inference_completion_bindings(
            state,
            design=design,
            active_contract=active_contract,
            receipt_sha256=receipt_sha256,
            candidate_records=candidate_records,
            candidate_inputs_sha256=candidate_inputs_sha256,
            prediction_manifest_sha256=prediction_manifest_sha256,
            attempted_count=2,
            success_count=2,
            failure_count=0,
            materialization_binding=binding,
        )


def test_owner_blind40_uses_only_eligible_partition_and_never_refetches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    excluded_id = 21
    rows = [_row(news_item_id) for news_item_id in range(1, 42)]
    materialization = _materialization(rows, excluded_id=excluded_id)
    design = _design(tmp_path)
    active_contract = _active_contract(tmp_path)
    receipt_sha256 = _sha("freeze-receipt")
    receipt = {"frozen_at_utc": "2026-08-05T16:00:00Z"}
    original_project_dir = builder.PROJECT_DIR
    original_evaluation_artifact_path = builder.evaluation_artifact_path
    original_validate_manifest = builder.validate_heldout_materialization_manifest
    original_materialization_binding = builder.heldout_materialization_binding

    def artifact_path(
        active_design: builder.FrozenEvaluationDesign,
        name: str,
        *,
        project_root: Path = tmp_path,
    ) -> Path:
        return original_evaluation_artifact_path(
            active_design,
            name,
            project_root=project_root,
        )

    monkeypatch.setattr(builder, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(builder, "evaluation_artifact_path", artifact_path)
    monkeypatch.setattr(builder, "load_evaluation_design", lambda _path: design)
    monkeypatch.setattr(builder, "require_heldout_ready", lambda *_args: None)
    monkeypatch.setattr(
        builder,
        "load_active_prediction_contract",
        lambda _design: (active_contract, receipt, receipt_sha256),
    )
    monkeypatch.setattr(
        builder,
        "load_completed_one_shot_state",
        lambda *_args, **_kwargs: (
            {
                "started_at_utc": "2026-08-06T00:20:00Z",
                "terminal_at_utc": "2026-08-06T00:21:00Z",
                "status": "completed",
                "started_event_count": 1,
                "events": [],
                "path": "docs/phase4/eval/inference.state.jsonl",
            },
            _sha("inference-state"),
        ),
    )

    candidate_records = [dict(record) for record in materialization.eligible_records]
    prediction_records = [
        {
            "news_item_id": int(record["news_item_id"]),
            "status": "ok",
            "prediction": {"materiality": 2},
            "input_sha256": record["input_sha256"],
            "declared_input_sha256": record["declared_input_sha256"],
            "text_sha256": record["text_sha256"],
        }
        for record in candidate_records
    ]
    inputs_path = artifact_path(design, "heldout_candidate_inputs_jsonl")
    predictions_path = artifact_path(design, "heldout_candidate_predictions_jsonl")
    prediction_manifest_path = artifact_path(
        design,
        "heldout_candidate_predictions_manifest_json",
    )
    dev_path = artifact_path(design, "dev_60_frozen_jsonl")
    for path in (inputs_path, predictions_path, prediction_manifest_path, dev_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    inputs_path.write_bytes(builder._json_line_bytes(candidate_records))
    predictions_path.write_bytes(builder._json_line_bytes(prediction_records))
    prediction_manifest_path.write_text("{}\n", encoding="utf-8")
    dev_path.write_bytes(
        builder._json_line_bytes([{"sample_index": sample_index} for sample_index in range(1, 61)])
    )

    _manifest, manifest_payload = builder.heldout_materialization_manifest_payload(
        materialization,
        design=design,
        active_contract=active_contract,
        freeze_receipt_sha256=receipt_sha256,
        project_root=tmp_path,
    )
    manifest_path = artifact_path(
        design,
        "heldout_candidate_materialization_manifest_json",
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(manifest_payload)

    monkeypatch.setattr(
        builder,
        "validate_heldout_materialization_manifest",
        lambda document, **kwargs: original_validate_manifest(
            document,
            **kwargs,
            project_root=tmp_path,
        ),
    )
    monkeypatch.setattr(
        builder,
        "heldout_materialization_binding",
        lambda document, **kwargs: original_materialization_binding(
            document,
            **kwargs,
            project_root=tmp_path,
        ),
    )
    monkeypatch.setattr(builder, "_heldout_candidate_rows", lambda *_args: rows)

    observed: dict[str, Any] = {}

    def validate_inputs(
        records: list[dict[str, Any]],
        *,
        rows: list[builder.NewsRow],
        **_kwargs: object,
    ) -> dict[int, dict[str, Any]]:
        observed["validated_row_ids"] = [row.news_item_id for row in rows]
        return {int(record["news_item_id"]): record for record in records}

    def validate_predictions(
        records: list[dict[str, Any]],
        **_kwargs: object,
    ) -> tuple[dict[int, dict[str, Any]], int, int]:
        return {int(record["news_item_id"]): record for record in records}, 40, 0

    def capture_prediction_manifest(
        _manifest: dict[str, Any],
        **kwargs: object,
    ) -> None:
        observed["prediction_materialization"] = kwargs["materialization_binding"]

    def capture_inference_binding(
        _state: dict[str, Any],
        **kwargs: object,
    ) -> None:
        observed["inference_materialization"] = kwargs["materialization_binding"]

    monkeypatch.setattr(builder, "validate_heldout_candidate_inputs", validate_inputs)
    monkeypatch.setattr(
        builder,
        "validate_heldout_candidate_predictions",
        validate_predictions,
    )
    monkeypatch.setattr(builder, "_validate_prediction_manifest", capture_prediction_manifest)
    monkeypatch.setattr(
        builder,
        "validate_inference_completion_bindings",
        capture_inference_binding,
    )
    monkeypatch.setattr(builder, "validate_blind_record", lambda *_args: None)
    monkeypatch.setattr(
        builder,
        "_heldout_owner_record",
        lambda *, candidate, row, base_contract, sample_index: {
            "news_item_id": row.news_item_id,
            "sample_index": sample_index,
        },
    )

    def forbidden_refetch(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("owner selection must never refetch or rematerialize PDFs")

    monkeypatch.setattr(builder, "download_cninfo_pdf", forbidden_refetch)
    monkeypatch.setattr(builder, "extract_cninfo_pdf_text", forbidden_refetch)
    monkeypatch.setattr(builder, "materialize_heldout_candidate_inputs", forbidden_refetch)

    database_path = tmp_path / "data/alphapilot.db"
    database_path.parent.mkdir(parents=True)
    database_path.touch()

    @contextmanager
    def read_only_database(_path: Path) -> Iterator[object]:
        yield object()

    monkeypatch.setattr(builder, "open_read_only_database", read_only_database)

    result = builder.build_heldout_owner_sample(
        tmp_path / "config/p4_event_evaluation_v1_7.yaml",
        database_path=database_path,
        now=datetime(2026, 8, 6, 1, tzinfo=UTC),
    )

    blind_path = tmp_path / str(result["heldout_blind_sample"])
    blind_ids = {
        int(json.loads(line)["news_item_id"])
        for line in blind_path.read_text(encoding="utf-8").splitlines()
    }
    expected_eligible_ids = {row.news_item_id for row in rows if row.news_item_id != excluded_id}
    assert blind_ids == expected_eligible_ids
    assert excluded_id not in blind_ids
    assert observed["validated_row_ids"] == sorted(expected_eligible_ids)
    assert observed["prediction_materialization"] == observed["inference_materialization"]
    assert observed["prediction_materialization"] == {
        "manifest_path": "docs/phase4/eval/materialization/manifest.json",
        "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "raw_candidate_count": 41,
        "eligible_candidate_count": 40,
        "ineligible_candidate_count": 1,
        "ineligible_by_reason": {
            "pdf_exceeds_size_bound": 0,
            "pdf_text_below_min_char_gate": 1,
        },
    }
    assert result["raw_candidate_count"] == 41
    assert result["candidate_count"] == 40
    assert result["ineligible_candidate_count"] == 1
    assert result["second_cninfo_body_fetch_count"] == 0
    assert tmp_path == builder.PROJECT_DIR
    assert original_project_dir != tmp_path
