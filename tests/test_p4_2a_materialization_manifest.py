from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

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
        title=f"公告 {news_item_id}",
        url=(
            "https://static.cninfo.com.cn/finalpage/2026-08-05/"
            f"{news_item_id}.PDF"
        ),
        published_at=datetime(2026, 8, 5, 1, tzinfo=UTC),
        available_time=datetime(2026, 8, 5, 2, tzinfo=UTC),
        content_hash=_sha(f"content-{news_item_id}"),
        raw_payload={},
    )


def _fixture(
    tmp_path: Path,
) -> tuple[
    list[builder.NewsRow],
    builder.HeldoutCandidateMaterialization,
    builder.FrozenEvaluationDesign,
    EventExtractContract,
    str,
]:
    rows = [_row(news_item_id) for news_item_id in range(1, 5)]
    annotation_contract = builder.FrozenContract(
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
    design = builder.FrozenEvaluationDesign(
        path=tmp_path / "config/p4_event_evaluation_v1_7.yaml",
        sha256=_sha("evaluation-design"),
        document={
            "schema_version": "p4.2a-evaluation-design-v1.7",
            "artifacts": {
                "heldout_candidate_inputs_jsonl": {
                    "path": "docs/phase4/eval/candidate-inputs.jsonl"
                },
                "heldout_candidate_materialization_manifest_json": {
                    "path": "docs/phase4/eval/materialization.manifest.json"
                },
                "prediction_contract_freeze_receipt_json": {
                    "path": "docs/phase4/eval/freeze-receipt.json"
                },
            },
        },
        base_contract=annotation_contract,
    )
    active_contract = EventExtractContract(
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
        max_items_per_run=1,
        max_input_characters=16_000,
        explicit_cache_enabled=False,
        evidence_candidate_selection=True,
        materialized_schema={},
    )

    eligible_records = []
    for row in (rows[0], rows[2]):
        eligible_records.append(
            {
                "news_item_id": row.news_item_id,
                "source": row.source,
                "url": row.url,
                "content_hash": row.content_hash,
                "input_sha256": _sha(f"input-{row.news_item_id}"),
                "declared_input_sha256": _sha(f"declared-{row.news_item_id}"),
                "text_sha256": _sha(f"text-{row.news_item_id}"),
                "body_evidence": {"pdf_sha256": _sha(f"pdf-{row.news_item_id}")},
            }
        )
    ineligible = (
        {
            "news_item_id": rows[1].news_item_id,
            "url": rows[1].url,
            "reason": "pdf_text_below_min_char_gate",
            "measured_value": 79,
            "gate_value": 80,
            "pdf_sha256": _sha("short-pdf"),
        },
        {
            "news_item_id": rows[3].news_item_id,
            "url": rows[3].url,
            "reason": "pdf_exceeds_size_bound",
            "measured_value": 8 * 1024 * 1024 + 1,
            "gate_value": 8 * 1024 * 1024,
            "pdf_sha256": None,
        },
    )
    materialization = builder.HeldoutCandidateMaterialization(
        all_candidates=tuple(
            {
                "news_item_id": row.news_item_id,
                "source": row.source,
                "url": row.url,
                "content_hash": row.content_hash,
            }
            for row in rows
        ),
        eligible_records=tuple(eligible_records),
        ineligible_candidates=ineligible,
        reason_counts={
            "pdf_exceeds_size_bound": 1,
            "pdf_text_below_min_char_gate": 1,
        },
    )
    return rows, materialization, design, active_contract, _sha("freeze-receipt")


def test_materialization_manifest_binds_ordered_partition_and_lineage(
    tmp_path: Path,
) -> None:
    rows, materialization, design, active_contract, receipt_sha256 = _fixture(tmp_path)

    manifest, payload = builder.heldout_materialization_manifest_payload(
        materialization,
        design=design,
        active_contract=active_contract,
        freeze_receipt_sha256=receipt_sha256,
        project_root=tmp_path,
    )
    repeated_manifest, repeated_payload = builder.heldout_materialization_manifest_payload(
        materialization,
        design=design,
        active_contract=active_contract,
        freeze_receipt_sha256=receipt_sha256,
        project_root=tmp_path,
    )

    assert payload == repeated_payload
    assert manifest == repeated_manifest == json.loads(payload)
    assert manifest["counts"] == {
        "all_candidates": 4,
        "eligible_candidates": 2,
        "ineligible_candidates": 2,
        "ineligible_by_reason": {
            "pdf_exceeds_size_bound": 1,
            "pdf_text_below_min_char_gate": 1,
        },
    }
    assert [row["news_item_id"] for row in manifest["layers"]["all_candidates"]] == [
        1,
        2,
        3,
        4,
    ]
    assert [
        row["news_item_id"] for row in manifest["layers"]["eligible_candidates"]
    ] == [1, 3]
    assert [
        row["news_item_id"] for row in manifest["layers"]["ineligible_candidates"]
    ] == [2, 4]
    assert manifest["lineage"]["evaluation_design"]["sha256"] == design.sha256
    assert manifest["lineage"]["prediction_contract"]["sha256"] == active_contract.sha256
    assert manifest["lineage"]["freeze_receipt"]["sha256"] == receipt_sha256
    assert manifest["artifacts"]["eligible_inputs_jsonl"]["path"] == (
        "docs/phase4/eval/candidate-inputs.jsonl"
    )
    assert manifest["artifacts"]["eligible_inputs_jsonl"]["sha256"] == hashlib.sha256(
        builder._json_line_bytes(materialization.eligible_records)
    ).hexdigest()
    assert manifest["artifacts"]["materialization_manifest_json"] == {
        "path": "docs/phase4/eval/materialization.manifest.json",
        "create_only": True,
    }

    assert (
        builder.validate_heldout_materialization_manifest(
            manifest,
            rows=rows,
            eligible_records=materialization.eligible_records,
            design=design,
            active_contract=active_contract,
            freeze_receipt_sha256=receipt_sha256,
            project_root=tmp_path,
        )
        == manifest
    )


def test_materialization_manifest_payload_is_safe_for_create_only_bundle(
    tmp_path: Path,
) -> None:
    _rows, materialization, design, active_contract, receipt_sha256 = _fixture(tmp_path)
    _manifest, manifest_payload = builder.heldout_materialization_manifest_payload(
        materialization,
        design=design,
        active_contract=active_contract,
        freeze_receipt_sha256=receipt_sha256,
        project_root=tmp_path,
    )
    inputs_path = tmp_path / "docs/phase4/eval/candidate-inputs.jsonl"
    manifest_path = tmp_path / "docs/phase4/eval/materialization.manifest.json"
    inputs_path.parent.mkdir(parents=True)
    inputs_payload = builder._json_line_bytes(materialization.eligible_records)

    expected = builder._write_create_only_bundle(
        {inputs_path: inputs_payload, manifest_path: manifest_payload}
    )
    assert expected[inputs_path] == hashlib.sha256(inputs_payload).hexdigest()
    assert expected[manifest_path] == hashlib.sha256(manifest_payload).hexdigest()
    assert builder._write_create_only_bundle(
        {inputs_path: inputs_payload, manifest_path: manifest_payload}
    ) == expected
    with pytest.raises(FileExistsError, match="refusing to overwrite mismatched"):
        builder._write_create_only_bundle(
            {inputs_path: inputs_payload, manifest_path: manifest_payload + b" "}
        )
    assert manifest_path.read_bytes() == manifest_payload


def test_materialization_manifest_rejects_non_partitioned_result(tmp_path: Path) -> None:
    _rows, materialization, design, active_contract, receipt_sha256 = _fixture(tmp_path)
    broken = builder.HeldoutCandidateMaterialization(
        all_candidates=materialization.all_candidates,
        eligible_records=materialization.eligible_records[:-1],
        ineligible_candidates=materialization.ineligible_candidates,
        reason_counts=materialization.reason_counts,
    )

    with pytest.raises(builder.GoldSampleError, match="do not close"):
        builder.heldout_materialization_manifest_payload(
            broken,
            design=design,
            active_contract=active_contract,
            freeze_receipt_sha256=receipt_sha256,
            project_root=tmp_path,
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "full_order",
        "eligible_overlap",
        "excluded_reason",
        "excluded_measurement",
        "reason_count",
        "inputs_path",
        "inputs_sha256",
        "design_sha256",
        "contract_sha256",
        "receipt_sha256",
        "extra_field",
    ),
)
def test_materialization_manifest_tampering_fails_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    rows, materialization, design, active_contract, receipt_sha256 = _fixture(tmp_path)
    manifest, _payload = builder.heldout_materialization_manifest_payload(
        materialization,
        design=design,
        active_contract=active_contract,
        freeze_receipt_sha256=receipt_sha256,
        project_root=tmp_path,
    )
    tampered = copy.deepcopy(manifest)
    if mutation == "full_order":
        tampered["layers"]["all_candidates"][0:2] = reversed(
            tampered["layers"]["all_candidates"][0:2]
        )
    elif mutation == "eligible_overlap":
        tampered["layers"]["ineligible_candidates"][0]["news_item_id"] = 1
    elif mutation == "excluded_reason":
        tampered["layers"]["ineligible_candidates"][0]["reason"] = "http_5xx"
    elif mutation == "excluded_measurement":
        tampered["layers"]["ineligible_candidates"][0]["measured_value"] = 80
    elif mutation == "reason_count":
        tampered["counts"]["ineligible_by_reason"]["pdf_exceeds_size_bound"] = 2
    elif mutation == "inputs_path":
        tampered["artifacts"]["eligible_inputs_jsonl"]["path"] = "elsewhere.jsonl"
    elif mutation == "inputs_sha256":
        tampered["artifacts"]["eligible_inputs_jsonl"]["sha256"] = "0" * 64
    elif mutation == "design_sha256":
        tampered["lineage"]["evaluation_design"]["sha256"] = "0" * 64
    elif mutation == "contract_sha256":
        tampered["lineage"]["prediction_contract"]["sha256"] = "0" * 64
    elif mutation == "receipt_sha256":
        tampered["lineage"]["freeze_receipt"]["sha256"] = "0" * 64
    else:
        tampered["unexpected"] = True

    with pytest.raises(builder.GoldSampleError):
        builder.validate_heldout_materialization_manifest(
            tampered,
            rows=rows,
            eligible_records=materialization.eligible_records,
            design=design,
            active_contract=active_contract,
            freeze_receipt_sha256=receipt_sha256,
            project_root=tmp_path,
        )
