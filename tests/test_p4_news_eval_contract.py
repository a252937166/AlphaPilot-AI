from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from alphapilot.llm import p4_news_eval
from alphapilot.llm.p4_news_eval import (
    EventEvaluationDesignError,
    load_event_evaluation_design,
)


def _set_nested(document: dict[str, Any], path: tuple[str, ...], value: object) -> None:
    current: dict[str, Any] = document
    for part in path[:-1]:
        nested = current[part]
        assert isinstance(nested, dict)
        current = nested
    current[path[-1]] = value


def _variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[dict[str, Any]], None],
) -> Path:
    document = yaml.safe_load(p4_news_eval.DEFAULT_EVALUATION_DESIGN_PATH.read_bytes())
    assert isinstance(document, dict)
    mutate(document)
    path = tmp_path / "p4_event_evaluation_v1_1.yaml"
    path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        p4_news_eval,
        "EXPECTED_EVALUATION_DESIGN_SHA256",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    return path


def test_load_event_evaluation_design_verifies_v1_1_contract() -> None:
    design = load_event_evaluation_design()
    artifacts = design.document["artifacts"]
    freeze = design.document["prediction_contract_freeze"]
    dev_final = freeze["dev_final_predictions"]
    inference = design.document["one_shot"]["inference"]
    evaluation = design.document["one_shot"]["evaluation"]
    heldout = design.document["splits"]["heldout_40"]
    completion = design.document["owner_annotation_completion"]

    assert design.sha256 == p4_news_eval.EXPECTED_EVALUATION_DESIGN_SHA256
    assert design.document["schema_version"] == "p4.2a-evaluation-design-v1.1"
    assert design.base_contract.sha256 == p4_news_eval.EXPECTED_BASE_CONTRACT_SHA256
    assert design.document["splits"]["dev_60"]["count"] == 60
    assert design.document["splits"]["heldout_40"]["count"] == 40
    assert (
        design.document["splits"]["heldout_40"]["candidate_batch"]["selection_ready_after"]
        == "2026-08-06T00:10:00+08:00"
    )
    assert artifacts["dev_final_predictions_jsonl"]["create_only"] is True
    assert artifacts["dev_final_predictions_manifest_json"]["create_only"] is True
    assert artifacts["heldout_40_blind_sample_jsonl"]["create_only"] is True
    assert artifacts["heldout_40_owner_annotations_jsonl"]["create_only"] is True
    assert artifacts["combined_100_annotations_jsonl"]["create_only"] is True
    assert artifacts["owner_completion_manifest_json"]["create_only"] is True
    assert freeze["receipt_created_after_dev_final_predictions"] is True
    assert freeze["heldout_inference_requires_valid_receipt"] is True
    assert dev_final["row_count"] == 60
    assert dev_final["required_identity_match_artifact"] == "dev_60_frozen_jsonl"
    assert dev_final["success_plus_failure_must_equal_row_count"] is True
    assert dev_final["contract_sha256_must_equal_receipt_contract_sha256"] is True
    assert inference["required_preconditions"] == [
        "valid_prediction_contract_freeze_receipt",
        "dev_final_predictions_bytes_and_manifest_verified",
        "dev_final_predictions_contract_matches_active_contract",
    ]
    assert heldout["owner_blind_sample_artifact"] == "heldout_40_blind_sample_jsonl"
    assert heldout["owner_completed_annotation_artifact"] == (
        "heldout_40_owner_annotations_jsonl"
    )
    assert heldout["selection_must_not_create"] == [
        "heldout_40_owner_annotations_jsonl",
        "combined_100_annotations_jsonl",
        "owner_completion_manifest_json",
    ]
    assert completion["manifest_artifact"] == "owner_completion_manifest_json"
    assert completion["dev"]["required_completed_count"] == 60
    assert completion["heldout"]["required_completed_count"] == 40
    assert completion["combined"]["required_row_count"] == 100
    assert evaluation["required_preconditions"] == [
        "valid_owner_completion_manifest",
        "owner_completion_artifact_bytes_and_counts_verified",
        "combined_annotations_sha256_and_identity_match_manifest",
    ]


def test_base_event_extract_contract_remains_byte_frozen() -> None:
    payload = (
        p4_news_eval.PROJECT_ROOT / "config/p4_event_extract_eval_v1.yaml"
    ).read_bytes()

    assert hashlib.sha256(payload).hexdigest() == p4_news_eval.EXPECTED_BASE_CONTRACT_SHA256


def test_design_rejects_byte_drift(tmp_path: Path) -> None:
    drifted = tmp_path / "p4_event_evaluation_v1_1.yaml"
    drifted.write_bytes(p4_news_eval.DEFAULT_EVALUATION_DESIGN_PATH.read_bytes() + b"\n")

    with pytest.raises(EventEvaluationDesignError, match="frozen SHA-256"):
        load_event_evaluation_design(drifted)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (
            ("splits", "heldout_40", "eligible_pool", "materiality_minimum"),
            1,
            "positive-pool",
        ),
        (
            ("splits", "heldout_40", "sampling", "selected_count"),
            39,
            "deterministic sampling",
        ),
        (
            ("splits", "heldout_40", "prompt_iteration_allowed"),
            True,
            "role/count",
        ),
        (
            ("splits", "heldout_40", "owner_blind_sample_artifact"),
            "heldout_40_owner_annotations_jsonl",
            "role/count",
        ),
        (
            ("splits", "heldout_40", "selection_must_not_create"),
            ["combined_100_annotations_jsonl"],
            "role/count",
        ),
        (
            ("one_shot", "inference", "maximum_started_events"),
            2,
            "inference one-shot",
        ),
        (
            (
                "one_shot",
                "inference",
                "required_preconditions",
            ),
            ["valid_prediction_contract_freeze_receipt"],
            "inference one-shot",
        ),
        (
            ("one_shot", "evaluation", "maximum_started_events"),
            2,
            "evaluation one-shot",
        ),
        (
            ("one_shot", "evaluation", "required_preconditions"),
            ["valid_owner_completion_manifest"],
            "evaluation one-shot",
        ),
        (
            (
                "prediction_contract_freeze",
                "dev_final_predictions",
                "row_count",
            ),
            59,
            "dev-final prediction freeze",
        ),
        (
            (
                "prediction_contract_freeze",
                "dev_final_predictions",
                "success_plus_failure_must_equal_row_count",
            ),
            False,
            "dev-final prediction freeze",
        ),
        (
            (
                "prediction_contract_freeze",
                "dev_final_predictions",
                "contract_sha256_must_equal_receipt_contract_sha256",
            ),
            False,
            "dev-final prediction freeze",
        ),
        (
            (
                "owner_annotation_completion",
                "dev",
                "required_completed_count",
            ),
            59,
            "dev owner-completion",
        ),
        (
            (
                "owner_annotation_completion",
                "combined",
                "renumbering_rule",
            ),
            "preserve_source_indices",
            "combined owner-completion",
        ),
        (
            (
                "owner_annotation_completion",
                "preconditions_before_combined_create",
            ),
            ["dev_owner_annotations_all_completed"],
            "owner-completion proof",
        ),
        (
            ("evaluation", "materiality_precision_minimum"),
            0.79,
            "metrics/report",
        ),
        (
            ("evaluation", "symbol_mapping_accuracy_minimum"),
            0.94,
            "metrics/report",
        ),
        (
            ("evaluation", "heldout_test_runs_maximum"),
            2,
            "metrics/report",
        ),
    ],
)
def test_design_rejects_weakened_dev_heldout_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    variant = _variant(
        tmp_path,
        monkeypatch,
        lambda document: _set_nested(document, path, value),
    )

    with pytest.raises(EventEvaluationDesignError, match=message):
        load_event_evaluation_design(variant)


def test_design_rejects_owner_selection_basis_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mutate(document: dict[str, Any]) -> None:
        owner = document["owner_delivery"]
        assert isinstance(owner, dict)
        fields = owner["forbidden_fields"]
        assert isinstance(fields, list)
        fields.remove("predicted_materiality")

    variant = _variant(tmp_path, monkeypatch, mutate)

    with pytest.raises(EventEvaluationDesignError, match="owner-delivery"):
        load_event_evaluation_design(variant)


def test_design_rejects_missing_dev_final_receipt_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mutate(document: dict[str, Any]) -> None:
        freeze = document["prediction_contract_freeze"]
        assert isinstance(freeze, dict)
        fields = freeze["required_receipt_fields"]
        assert isinstance(fields, list)
        fields.remove("dev_final_predictions_contract_sha256")

    variant = _variant(tmp_path, monkeypatch, mutate)

    with pytest.raises(EventEvaluationDesignError, match="prediction-contract freeze"):
        load_event_evaluation_design(variant)


def test_design_rejects_missing_owner_completion_manifest_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mutate(document: dict[str, Any]) -> None:
        completion = document["owner_annotation_completion"]
        assert isinstance(completion, dict)
        fields = completion["required_manifest_fields"]
        assert isinstance(fields, list)
        fields.remove("combined_annotations_sha256")

    variant = _variant(tmp_path, monkeypatch, mutate)

    with pytest.raises(EventEvaluationDesignError, match="owner-completion proof"):
        load_event_evaluation_design(variant)


def test_design_requires_one_shot_and_failure_disclosures() -> None:
    design = load_event_evaluation_design()
    required = set(design.document["evaluation"]["required_report_fields"])

    assert {
        "prediction_contract.dev_final_predictions_sha256",
        "prediction_contract.dev_final_predictions_manifest_sha256",
        "prediction_contract.dev_final_predictions_contract_sha256",
        "splits.dev60.final_prediction_failure_ids",
        "splits.dev60.final_prediction_identity_sha256",
        "owner_completion.manifest_sha256",
        "owner_completion.dev_annotations_sha256",
        "owner_completion.heldout_blind_sample_sha256",
        "owner_completion.heldout_annotations_sha256",
        "owner_completion.combined_annotations_sha256",
        "owner_completion.combined_ordered_identity_sha256",
        "owner_completion.combined_renumbering_rule",
        "owner_completion.identity_validation_passed",
        "owner_completion.blindness_validation_passed",
        "splits.heldout40.predicted_positive_pool_count",
        "splits.heldout40.predicted_positive_pool_rate",
        "one_shot.inference.started_event_count",
        "one_shot.evaluation.started_event_count",
        "owner_delivery.forbidden_field_violation_count",
        "diagnostics.offline_trial.predicted_materiality_gte_2_rate",
        "diagnostics.offline_trial.gold_intersection_failure_ids",
        "diagnostics.active_prediction.gold_failure_ids",
        "metrics.materiality_precision.heldout40.denominator",
        "metrics.symbol_exact_set.dev60",
        "metrics.symbol_exact_set.heldout40",
        "metrics.symbol_exact_set.all100",
    }.issubset(required)
