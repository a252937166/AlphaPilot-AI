from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from scripts import build_p4_2a_gold_sample as builder
from scripts import evaluate_p4_2a_gold as evaluator

PROJECT_DIR = Path(__file__).resolve().parent.parent
V1_6_DESIGN = PROJECT_DIR / "config/p4_event_evaluation_v1_6.yaml"
V1_7_DESIGN = PROJECT_DIR / "config/p4_event_evaluation_v1_7.yaml"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _row(news_item_id: int) -> builder.NewsRow:
    return builder.NewsRow(
        news_item_id=news_item_id,
        source="cninfo",
        ingested_symbol=f"{news_item_id:06d}",
        title=f"公告 {news_item_id}",
        url=f"https://static.cninfo.com.cn/finalpage/2026-08-04/{news_item_id}.PDF",
        published_at=datetime(2026, 8, 4, tzinfo=UTC),
        available_time=datetime(2026, 8, 4, 1, tzinfo=UTC),
        content_hash=_sha(f"content-{news_item_id}"),
        raw_payload={},
    )


def _eligible_record(row: builder.NewsRow) -> dict[str, Any]:
    return {
        "news_item_id": row.news_item_id,
        "source": row.source,
        "url": row.url,
        "content_hash": row.content_hash,
        "input_sha256": _sha(f"active-{row.news_item_id}"),
        "declared_input_sha256": _sha(f"declared-{row.news_item_id}"),
        "text_sha256": _sha(f"text-{row.news_item_id}"),
        "body_evidence": {"pdf_sha256": _sha(f"pdf-{row.news_item_id}")},
    }


def _expected_binding(manifest_sha256: str) -> dict[str, Any]:
    return {
        "manifest_path": (
            "docs/phase4/eval/P4.2a-heldout-materialization-v1.7/manifest.json"
        ),
        "manifest_sha256": manifest_sha256,
        "raw_candidate_count": 2,
        "eligible_candidate_count": 1,
        "ineligible_candidate_count": 1,
        "ineligible_by_reason": {
            "pdf_exceeds_size_bound": 0,
            "pdf_text_below_min_char_gate": 1,
        },
    }


def _valid_materialization() -> tuple[
    builder.FrozenEvaluationDesign,
    builder.EventExtractContract,
    str,
    list[builder.NewsRow],
    list[dict[str, Any]],
    dict[str, Any],
    str,
]:
    design = builder.load_evaluation_design(V1_7_DESIGN)
    active, _receipt, receipt_sha256 = builder.load_active_prediction_contract(design)
    rows = [_row(1), _row(2)]
    eligible_records = [_eligible_record(rows[0])]
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
        ineligible_candidates=(
            {
                "news_item_id": rows[1].news_item_id,
                "url": rows[1].url,
                "reason": "pdf_text_below_min_char_gate",
                "measured_value": 53,
                "gate_value": 80,
                "pdf_sha256": _sha("short-pdf"),
            },
        ),
        reason_counts={"pdf_text_below_min_char_gate": 1},
    )
    manifest, payload = builder.heldout_materialization_manifest_payload(
        materialization,
        design=design,
        active_contract=active,
        freeze_receipt_sha256=receipt_sha256,
    )
    return (
        design,
        active,
        receipt_sha256,
        rows,
        eligible_records,
        manifest,
        hashlib.sha256(payload).hexdigest(),
    )


def test_evaluator_materialization_returns_only_eligible_rows_and_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design, active, receipt_sha, rows, records, manifest, manifest_sha = (
        _valid_materialization()
    )
    monkeypatch.setattr(evaluator, "_read_json", lambda *_args, **_kwargs: (manifest, manifest_sha))
    monkeypatch.setattr(
        builder,
        "heldout_materialization_binding",
        lambda *_args, **_kwargs: _expected_binding(manifest_sha),
    )

    eligible_rows, binding, ineligible_ids = (
        evaluator._materialization_binding_for_evaluation(
            design=design,
            active_contract=active,
            receipt_sha256=receipt_sha,
            candidate_rows=rows,
            candidate_records=records,
        )
    )

    assert [row.news_item_id for row in eligible_rows] == [1]
    assert ineligible_ids == frozenset({2})
    assert binding == _expected_binding(manifest_sha)


def test_evaluator_materialization_rejects_eligible_layer_or_manifest_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design, active, receipt_sha, rows, records, manifest, manifest_sha = (
        _valid_materialization()
    )
    monkeypatch.setattr(evaluator, "_read_json", lambda *_args, **_kwargs: (manifest, manifest_sha))
    monkeypatch.setattr(
        builder,
        "heldout_materialization_binding",
        lambda *_args, **_kwargs: _expected_binding(manifest_sha),
    )

    with pytest.raises(
        evaluator.GoldEvaluationError,
        match="materialization manifest is invalid",
    ):
        evaluator._materialization_binding_for_evaluation(
            design=design,
            active_contract=active,
            receipt_sha256=receipt_sha,
            candidate_rows=rows,
            candidate_records=[_eligible_record(rows[1])],
        )

    drifted = dict(manifest)
    drifted["counts"] = {
        **manifest["counts"],
        "eligible_candidates": 2,
    }
    monkeypatch.setattr(evaluator, "_read_json", lambda *_args, **_kwargs: (drifted, manifest_sha))
    with pytest.raises(
        evaluator.GoldEvaluationError,
        match="materialization manifest is invalid",
    ):
        evaluator._materialization_binding_for_evaluation(
            design=design,
            active_contract=active,
            receipt_sha256=receipt_sha,
            candidate_rows=rows,
            candidate_records=records,
        )


def test_evaluator_legacy_design_keeps_full_candidate_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design = builder.load_evaluation_design(V1_6_DESIGN)
    active, _receipt, receipt_sha = builder.load_active_prediction_contract(design)
    rows = [_row(1), _row(2)]
    monkeypatch.setattr(
        evaluator,
        "_read_json",
        lambda *_args, **_kwargs: pytest.fail("legacy evaluation must not load a manifest"),
    )

    eligible_rows, binding, excluded = evaluator._materialization_binding_for_evaluation(
        design=design,
        active_contract=active,
        receipt_sha256=receipt_sha,
        candidate_rows=rows,
        candidate_records=[],
    )

    assert eligible_rows == rows
    assert binding is None
    assert excluded == frozenset()


def test_selection_manifest_rejects_ineligible_id_before_scoring() -> None:
    design = builder.load_evaluation_design(V1_7_DESIGN)
    active, _receipt, receipt_sha = builder.load_active_prediction_contract(design)
    materialization = {
        "manifest_path": "docs/phase4/eval/materialization.json",
        "manifest_sha256": "1" * 64,
        "raw_candidate_count": 41,
        "eligible_candidate_count": 40,
        "ineligible_candidate_count": 1,
        "ineligible_by_reason": {
            "pdf_exceeds_size_bound": 0,
            "pdf_text_below_min_char_gate": 1,
        },
    }
    heldout_ids = [*range(101, 140), 200]
    annotation_ids = [*range(1, 61), *heldout_ids]
    annotations: dict[int, dict[str, Any]] = {}
    selected: list[dict[str, Any]] = []
    seed = "heldout-seed"
    for news_item_id in annotation_ids:
        declared = _sha(f"declared-{news_item_id}")
        active_input = _sha(f"active-{news_item_id}")
        text_sha = _sha(f"text-{news_item_id}")
        annotations[news_item_id] = {
            "record": {
                "input_sha256": declared,
                "text_sha256": text_sha,
            }
        }
        if news_item_id in heldout_ids:
            selected.append(
                {
                    "news_item_id": news_item_id,
                    "input_sha256": active_input,
                    "declared_input_sha256": declared,
                    "text_sha256": text_sha,
                    "selection_rank_sha256": builder.heldout_prediction_rank(
                        seed=seed,
                        news_item_id=news_item_id,
                        input_sha256=active_input,
                    ),
                }
            )
    manifest: dict[str, Any] = {
        "schema_version": "p4.2a-heldout-selection-manifest-v1.2",
        "design": {
            "sha256": design.sha256,
            "schema_version": design.document["schema_version"],
        },
        "annotation_contract": {"sha256": design.base_contract.sha256},
        "prediction_contract": {
            "contract_sha256": active.sha256,
            "freeze_receipt_sha256": receipt_sha,
        },
        "inference": {"state_sha256": "2" * 64},
        "materialization": materialization,
        "candidate_inputs": {
            "sha256": "3" * 64,
            "count": 40,
            "cninfo_bodies_frozen_before_prediction": True,
        },
        "candidate_predictions": {
            "sha256": "4" * 64,
            "manifest_sha256": "5" * 64,
            "success_count": 40,
        },
        "eligible_pool": {
            "count": 40,
            "positive_rate_denominator": "successful_predictions",
            "positive_rate": 1.0,
        },
        "selection": {
            "seed": seed,
            "selected_count": 40,
            "selected": selected,
        },
        "owner_delivery": {
            "predictions_visible": False,
            "selection_basis_visible": False,
            "forbidden_field_violation_count": 0,
        },
    }

    with pytest.raises(
        evaluator.GoldEvaluationError,
        match="materialization-ineligible ID",
    ):
        evaluator._validate_selection_manifest(
            manifest,
            manifest_sha256="6" * 64,
            design=design,
            annotations=annotations,
            active_contract=active,
            receipt_sha256=receipt_sha,
            candidate_inputs_sha256="3" * 64,
            candidate_predictions_sha256="4" * 64,
            candidate_prediction_manifest_sha256="5" * 64,
            inference_state_sha256="2" * 64,
            materialization_binding=materialization,
            ineligible_ids=frozenset({200}),
        )


def test_materialization_binding_is_required_and_legacy_rejects_unregistered_evidence() -> None:
    binding = {
        "manifest_path": "manifest.json",
        "manifest_sha256": "1" * 64,
        "raw_candidate_count": 2,
        "eligible_candidate_count": 1,
        "ineligible_candidate_count": 1,
        "ineligible_by_reason": {},
    }
    evaluator._require_materialization_binding(
        {"materialization": binding},
        expected=binding,
        label="fixture",
    )
    with pytest.raises(evaluator.GoldEvaluationError, match="binding drifted"):
        evaluator._require_materialization_binding(
            {"materialization": {**binding, "eligible_candidate_count": 2}},
            expected=binding,
            label="fixture",
        )
    with pytest.raises(evaluator.GoldEvaluationError, match="unregistered"):
        evaluator._require_materialization_binding(
            {"materialization": binding},
            expected=None,
            label="legacy fixture",
        )


def test_evaluator_cli_registers_explicit_v1_7_scope(tmp_path: Path) -> None:
    arguments = evaluator._arguments(
        [
            "--scope",
            "heldout-final-v1.7",
            "--evaluation-design",
            str(V1_7_DESIGN),
            "--output",
            str(tmp_path / "report.json"),
        ]
    )

    assert arguments.scope == "heldout-final-v1.7"
    assert arguments.evaluation_design == V1_7_DESIGN
