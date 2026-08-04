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
    validate_heldout_annotation_provenance,
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


def _v1_3_variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[dict[str, Any]], None],
) -> Path:
    document = yaml.safe_load(p4_news_eval.EVALUATION_DESIGN_V1_3_PATH.read_bytes())
    assert isinstance(document, dict)
    mutate(document)
    path = tmp_path / "p4_event_evaluation_v1_3.yaml"
    path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        p4_news_eval,
        "EXPECTED_EVALUATION_DESIGN_V1_3_SHA256",
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
    assert heldout["owner_completed_annotation_artifact"] == ("heldout_40_owner_annotations_jsonl")
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


def test_load_event_evaluation_design_v1_2_binds_plus_mainland_and_preserves_split() -> None:
    legacy = load_event_evaluation_design()
    design = load_event_evaluation_design(p4_news_eval.EVALUATION_DESIGN_V1_2_PATH)

    assert design.sha256 == p4_news_eval.EXPECTED_EVALUATION_DESIGN_V1_2_SHA256
    assert design.document["schema_version"] == "p4.2a-evaluation-design-v1.2"
    assert design.prediction_contract.model == "qwen3.6-plus"
    assert (
        design.prediction_contract.endpoint == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert (
        design.document["splits"]["heldout_40"]["sampling"]
        == legacy.document["splits"]["heldout_40"]["sampling"]
    )
    assert (
        design.document["splits"]["heldout_40"]["candidate_batch"]
        == legacy.document["splits"]["heldout_40"]["candidate_batch"]
    )
    current_evaluation = dict(design.document["evaluation"])
    legacy_evaluation = dict(legacy.document["evaluation"])
    current_report_fields = current_evaluation.pop("required_report_fields")
    legacy_report_fields = legacy_evaluation.pop("required_report_fields")
    assert current_evaluation == legacy_evaluation
    assert current_report_fields[: len(legacy_report_fields)] == legacy_report_fields
    assert current_report_fields[len(legacy_report_fields) :] == [
        "prediction_contract.endpoint",
        "prediction_contract.explicit_cache_enabled",
        "owner_completion.heldout_annotation_type",
        "owner_completion.heldout_drafter_ids",
        "owner_completion.heldout_adjudicator_ids",
        "owner_completion.heldout_human_adjudication_validated",
    ]
    assert (
        design.document["prediction_contract_freeze"]["required_model"]
        == design.prediction_contract.model
    )
    assert (
        design.document["prediction_contract_freeze"]["required_endpoint"]
        == design.prediction_contract.endpoint
    )
    assert design.document["prediction_contract_freeze"]["required_receipt_fields"][-2:] == [
        "endpoint",
        "explicit_cache_enabled",
    ]
    legacy_artifacts = legacy.document["artifacts"]
    current_artifacts = design.document["artifacts"]
    for name in p4_news_eval._V1_2_CREATE_ONLY_ARTIFACTS:
        assert current_artifacts[name]["path"] != legacy_artifacts[name]["path"]
        assert "v1.2" in current_artifacts[name]["path"]


def test_v1_2_heldout_provenance_accepts_only_distinct_human_adjudicator() -> None:
    design = load_event_evaluation_design(p4_news_eval.EVALUATION_DESIGN_V1_2_PATH)
    record = {
        "annotation_type": "ai_drafted_human_adjudicated",
        "drafter_id": "ChatGPT GPT-5.6 Pro",
        "adjudicator_id": "owner-ouyang",
        "annotation_owner": "owner-ouyang",
    }

    assert validate_heldout_annotation_provenance(record, design) == {
        "annotation_type": "ai_drafted_human_adjudicated",
        "drafter_id": "ChatGPT GPT-5.6 Pro",
        "adjudicator_id": "owner-ouyang",
    }

    pure_ai = {**record, "adjudicator_id": "ChatGPT GPT-5.6 Pro"}
    with pytest.raises(EventEvaluationDesignError, match="must differ"):
        validate_heldout_annotation_provenance(pure_ai, design)

    missing_human = {**record, "adjudicator_id": "", "annotation_owner": ""}
    with pytest.raises(EventEvaluationDesignError, match="adjudicator_id is missing"):
        validate_heldout_annotation_provenance(missing_human, design)


def test_load_event_evaluation_design_v1_3_extends_v1_2_without_split_drift() -> None:
    previous = load_event_evaluation_design(p4_news_eval.EVALUATION_DESIGN_V1_2_PATH)
    design = load_event_evaluation_design(p4_news_eval.EVALUATION_DESIGN_V1_3_PATH)

    assert design.sha256 == p4_news_eval.EXPECTED_EVALUATION_DESIGN_V1_3_SHA256
    assert design.document["schema_version"] == "p4.2a-evaluation-design-v1.3"
    assert design.prediction_contract.sha256 == (
        "e6d3e7db08e2d226c850092f0f794d7194eaf1935a56cbfe267a86e1297f37fc"
    )
    assert design.prediction_contract.model == "qwen3.6-plus"
    assert (
        design.prediction_contract.endpoint == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert design.prediction_contract.evidence_span_match_mode == (
        "unicode_whitespace_elided_contiguous_substring_v1"
    )
    assert design.prediction_contract.explicit_cache_enabled is False
    assert design.document["splits"] == previous.document["splits"]
    assert (
        design.document["heldout_annotation_provenance"]
        == previous.document["heldout_annotation_provenance"]
    )
    assert (
        design.document["owner_annotation_completion"]
        == previous.document["owner_annotation_completion"]
    )

    previous_evaluation = dict(previous.document["evaluation"])
    current_evaluation = dict(design.document["evaluation"])
    previous_report_fields = previous_evaluation.pop("required_report_fields")
    current_report_fields = current_evaluation.pop("required_report_fields")
    assert current_evaluation == previous_evaluation
    assert current_report_fields[: len(previous_report_fields)] == previous_report_fields
    assert current_report_fields[len(previous_report_fields) :] == [
        "prediction_contract.evidence_span_match_mode",
        "evidence_validation.v1_3_actual",
        "evidence_validation.whitespace_normalized_counterfactual",
        "evidence_validation.v1_4_actual",
        "evidence_validation.v1_4_legacy_exact_shadow",
        "symbol_diagnostics.ai_label_defect_ids",
        "symbol_diagnostics.adjusted_exact_set",
    ]

    current_artifacts = design.document["artifacts"]
    previous_artifacts = previous.document["artifacts"]
    for name in p4_news_eval._V1_2_CREATE_ONLY_ARTIFACTS:
        assert current_artifacts[name]["path"] != previous_artifacts[name]["path"]
        assert "v1.3" in current_artifacts[name]["path"]
    freeze = design.document["prediction_contract_freeze"]
    assert freeze["required_evidence_span_match_mode"] == (
        "unicode_whitespace_elided_contiguous_substring_v1"
    )
    assert freeze["required_receipt_fields"][-1] == "evidence_span_match_mode"


def test_v1_3_binds_append_only_historical_comparison() -> None:
    design = load_event_evaluation_design(p4_news_eval.EVALUATION_DESIGN_V1_3_PATH)
    historical = design.document["historical_comparison"]

    assert historical["v1_3_actual"] == {
        "evidence_source": "immutable_local_artifacts",
        "success_count": 53,
        "failure_count": 7,
        "failure_ids": [250, 258, 287, 304, 306, 336, 358],
        "predictions_sha256": ("b882a5cdad7025f8499eae75b617e189174ef866ab949749dd58c4a193229134"),
        "manifest_sha256": ("4eb7f05e8196ac5dd4d646bb1b8be7a56b93123e4866b0ba4a79121ffb370262"),
        "report_sha256": ("781f6b7f30d97b9a43978feccec6891fa9b959aca0572a53784527a7e0e926e9"),
        "blocker_sha256": (
            "6efed45a618a9892fdcd321dd43db2232b3ddf1a4eb2ada7d371cb0da9a3dc3d"
        ),
    }
    assert historical["whitespace_normalized_counterfactual"] == {
        "evidence_source": "independent_reviewer_external_reproduction",
        "locally_recomputed_from_persisted_raw_payload": False,
        "success_count": 58,
        "failure_count": 2,
        "normalization_recovered_ids": [250, 258, 287, 306, 358],
        "true_synthesis_failure_ids": [304, 336],
    }
    assert historical["v1_4_required"] == {
        "success_count": 60,
        "failure_count": 0,
        "materiality_positive_agreement_minimum": 0.80,
        "symbol_exact_set_agreement_minimum": 0.95,
    }
    assert historical["symbol_adjudication"] == {
        "ai_label_defect_ids": [44],
        "model_over_attribution_ids": [75, 210, 232, 393],
        "frozen_dev_labels_must_remain_unchanged": True,
        "raw_gate_uses_frozen_labels": True,
        "adjusted_diagnostic_excludes_label_defects": True,
    }


def test_v1_3_historical_artifact_verifier_fails_closed_on_byte_drift(
    tmp_path: Path,
) -> None:
    hashes: dict[str, str] = {}
    for sha_field, relative_path in (
        p4_news_eval._V1_3_HISTORICAL_ARTIFACT_PATHS.items()
    ):
        payload = f"fixture:{sha_field}".encode()
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        hashes[sha_field] = hashlib.sha256(payload).hexdigest()

    p4_news_eval._verify_v1_3_historical_artifacts(tmp_path, hashes)
    drifted_path = (
        tmp_path
        / p4_news_eval._V1_3_HISTORICAL_ARTIFACT_PATHS["blocker_sha256"]
    )
    drifted_path.write_bytes(b"drifted")

    with pytest.raises(EventEvaluationDesignError, match="frozen SHA-256"):
        p4_news_eval._verify_v1_3_historical_artifacts(tmp_path, hashes)


def test_load_event_evaluation_design_v1_4_binds_v1_5_and_failed_round() -> None:
    previous = load_event_evaluation_design(p4_news_eval.EVALUATION_DESIGN_V1_3_PATH)
    design = load_event_evaluation_design(p4_news_eval.EVALUATION_DESIGN_V1_4_PATH)

    assert design.sha256 == p4_news_eval.EXPECTED_EVALUATION_DESIGN_V1_4_SHA256
    assert design.document["schema_version"] == "p4.2a-evaluation-design-v1.4"
    assert design.prediction_contract.sha256 == (
        "a07f9f37e0877bd06ce3dc9e8a0e03c51bbb92fdc3ba6738b6932d7679aca560"
    )
    assert design.prediction_contract.model == "qwen3.6-plus"
    assert design.prediction_contract.explicit_cache_enabled is False
    assert (
        design.prediction_contract.evidence_span_match_mode
        == "unicode_whitespace_elided_contiguous_substring_v1"
    )
    assert design.document["splits"] == previous.document["splits"]
    assert (
        design.document["heldout_annotation_provenance"]
        == previous.document["heldout_annotation_provenance"]
    )

    derived = design.document["derived_after_failed_round"]
    assert derived["round_id"] == "v1.4-r1"
    assert derived["formal_dev_round_valid"] is False
    assert derived["heldout_accessed"] is False
    assert derived["extraction"]["success_count"] == 54
    assert derived["extraction"]["failure_ids"] == [253, 258, 280, 304, 336, 340]
    assert derived["extraction"]["post_validation"]["root_cause"] == (
        "unadjudicated_from_safe_artifacts"
    )
    assert derived["artifacts"]["blocker"]["sha256"] == (
        "3d0038515e208bca37e096b280ec411da2086addd8696d2ee6e8afa36fad00f9"
    )
    assert derived["report_integrity_disclosure"] == {
        "immutable_report_preserved": True,
        "flash_baseline_changed_dimensions_omitted": [
            "evidence_span_match_mode",
            "validation_contract",
        ],
        "baseline_comparable_count": 59,
        "candidate_comparable_count": 54,
        "causal_reading_forbidden": True,
    }
    assert design.document["historical_comparison"]["v1_4_r1_actual"] == derived

    previous_artifacts = previous.document["artifacts"]
    current_artifacts = design.document["artifacts"]
    for name in p4_news_eval._V1_2_CREATE_ONLY_ARTIFACTS:
        assert current_artifacts[name]["path"] != previous_artifacts[name]["path"]
        assert "v1.4" in current_artifacts[name]["path"]
    assert design.document["prediction_contract_freeze"][
        "required_evidence_span_match_mode"
    ] == "unicode_whitespace_elided_contiguous_substring_v1"


def test_v1_4_historical_artifact_verifier_fails_closed_on_byte_drift(
    tmp_path: Path,
) -> None:
    entries: dict[str, dict[str, str]] = {}
    for name, relative_path in p4_news_eval._V1_4_HISTORICAL_ARTIFACT_PATHS.items():
        payload = f"fixture:{name}".encode()
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        entries[name] = {
            "path": relative_path.as_posix(),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    derived = {"artifacts": entries}

    p4_news_eval._verify_v1_4_historical_artifacts(tmp_path, derived)
    drifted_path = (
        tmp_path / p4_news_eval._V1_4_HISTORICAL_ARTIFACT_PATHS["blocker"]
    )
    drifted_path.write_bytes(b"drifted")

    with pytest.raises(EventEvaluationDesignError, match="frozen SHA-256"):
        p4_news_eval._verify_v1_4_historical_artifacts(tmp_path, derived)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (
            ("extends_design", "sha256"),
            "0" * 64,
            "inheritance",
        ),
        (
            ("active_prediction_contract", "sha256"),
            "0" * 64,
            "active prediction contract identity",
        ),
        (
            (
                "prediction_contract_freeze",
                "required_evidence_span_match_mode",
            ),
            "exact_contiguous_substring_v1",
            "prediction freeze",
        ),
        (
            (
                "historical_comparison",
                "whitespace_normalized_counterfactual",
                "normalization_recovered_ids",
            ),
            [250, 258, 287, 304, 306, 358],
            "historical comparison",
        ),
        (
            ("evaluation", "required_report_fields_append"),
            ["prediction_contract.evidence_span_match_mode"],
            "report comparison",
        ),
    ],
)
def test_v1_3_rejects_inheritance_and_comparison_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    variant = _v1_3_variant(
        tmp_path,
        monkeypatch,
        lambda document: _set_nested(document, path, value),
    )

    with pytest.raises(EventEvaluationDesignError, match=message):
        p4_news_eval._load_v1_3_event_evaluation_design(
            variant,
            project_root=p4_news_eval.PROJECT_ROOT,
        )


def test_base_event_extract_contract_remains_byte_frozen() -> None:
    payload = (p4_news_eval.PROJECT_ROOT / "config/p4_event_extract_eval_v1.yaml").read_bytes()

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
