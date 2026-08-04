from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from alphapilot.llm import p4_news_eval
from alphapilot.llm.p4_news_eval import (
    EventEvaluationDesignError,
    load_event_evaluation_design,
)


def test_v1_6_design_preregisters_single_flash_selection_round() -> None:
    incumbent = load_event_evaluation_design(p4_news_eval.EVALUATION_DESIGN_V1_5_PATH)
    design = load_event_evaluation_design(p4_news_eval.EVALUATION_DESIGN_V1_6_PATH)

    assert design.sha256 == p4_news_eval.EXPECTED_EVALUATION_DESIGN_V1_6_SHA256
    assert design.document["schema_version"] == "p4.2a-evaluation-design-v1.6"
    assert design.prediction_contract.sha256 == (
        "68474e4bd4fd5c9c88711dd5e102898ad1ed75a0fb984045efbd14e51a6db701"
    )
    assert design.prediction_contract.model == "qwen3.7-flash"
    assert design.document["splits"] == incumbent.document["splits"]
    assert design.document["evaluation"] == incumbent.document["evaluation"]
    assert (
        design.document["heldout_annotation_provenance"]
        == incumbent.document["heldout_annotation_provenance"]
    )

    selection = design.document["model_selection"]
    assert selection["official_round_id"] == "v1.7-r1"
    assert selection["deadline_utc"] == "2026-08-05T16:10:00Z"
    assert selection["single_formal_round"] is True
    assert selection["third_model_forbidden"] is True
    assert selection["relative_score_selection_forbidden"] is True
    assert selection["gates"] == {
        "success_count": 60,
        "failure_count": 0,
        "materiality": {
            "formula": "tp_divided_by_tp_plus_fp",
            "minimum": 0.8,
            "zero_predicted_positive_policy": "fail",
            "failed_reference_positive_count": 0,
        },
        "symbol": {
            "formula": "exact_set_match_accuracy",
            "denominator": 60,
            "minimum": 0.95,
        },
        "raw_unrounded": True,
    }
    assert selection["outcome_receipt_artifact"] == ("model_selection_outcome_receipt_json")
    assert design.document["artifacts"]["model_selection_outcome_receipt_json"] == {
        "path": "docs/phase4/eval/P4.2a-model-selection-v1.7.json",
        "create_only": True,
    }

    for name in p4_news_eval._V1_2_CREATE_ONLY_ARTIFACTS:
        path = design.document["artifacts"][name]["path"]
        assert path != incumbent.document["artifacts"][name]["path"]
        assert "v1.6" in path


def test_v1_5_design_binds_candidate_v1_6_and_failed_v1_5_round() -> None:
    previous = load_event_evaluation_design(p4_news_eval.EVALUATION_DESIGN_V1_4_PATH)
    design = load_event_evaluation_design(p4_news_eval.EVALUATION_DESIGN_V1_5_PATH)

    assert design.sha256 == p4_news_eval.EXPECTED_EVALUATION_DESIGN_V1_5_SHA256
    assert design.document["schema_version"] == "p4.2a-evaluation-design-v1.5"
    assert design.prediction_contract.sha256 == (
        "4e88990d2ee7671db316794aabd0a476f798b5e542f00bbb8ffbd3f7fd423269"
    )
    assert design.prediction_contract.evidence_candidate_selection is True
    assert design.prediction_contract.materialized_schema is not None
    assert design.document["splits"] == previous.document["splits"]

    active = design.document["active_prediction_contract"]
    assert active["input_representation"] == {
        "name": "ordered_evidence_candidates_v1",
        "original_text_in_model_message": False,
        "declared_input_identity": "legacy_eight_field_user_json_v1",
        "active_model_input_identity": ("canonical_ordered_evidence_candidates_user_json_v1"),
        "dual_hash_identity_required": True,
    }
    assert active["model_result_schema"]["sha256"] == (
        "c106cd15bd974de19ecc01d6e99e8f39c39fbf14df3a3b4dc74ee9b08ff6dd66"
    )
    assert active["materialized_result_schema"]["sha256"] == (
        "0ac68654ce23ecd4e537d849d695e092c76dcb9de0fb03793e65ae62b181947f"
    )

    derived = design.document["derived_after_failed_round"]
    assert derived["round_id"] == "v1.5-r1"
    assert derived["extraction"]["success_count"] == 50
    assert derived["extraction"]["failure_count"] == 10
    assert derived["extraction"]["failure_ids"] == [
        9,
        250,
        272,
        280,
        303,
        304,
        306,
        336,
        340,
        360,
    ]
    assert derived["extraction"]["gold_intersection_failure_ids"] == [
        272,
        304,
        306,
        336,
    ]
    assert derived["report_integrity_disclosure"]["causal_reading_forbidden"] is True
    assert design.document["historical_comparison"]["v1_5_r1_actual"] == derived

    for name in p4_news_eval._V1_2_CREATE_ONLY_ARTIFACTS:
        path = design.document["artifacts"][name]["path"]
        assert path != previous.document["artifacts"][name]["path"]
        assert "v1.5" in path


def test_v1_5_historical_artifact_verifier_fails_closed_on_byte_drift(
    tmp_path: Path,
) -> None:
    entries: dict[str, dict[str, str]] = {}
    for name, relative_path in p4_news_eval._V1_5_HISTORICAL_ARTIFACT_PATHS.items():
        payload = f"fixture:{name}".encode()
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        entries[name] = {
            "path": relative_path.as_posix(),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    derived = {"artifacts": entries}

    p4_news_eval._verify_v1_5_historical_artifacts(tmp_path, derived)
    drifted = tmp_path / p4_news_eval._V1_5_HISTORICAL_ARTIFACT_PATHS["report"]
    drifted.write_bytes(b"drifted")

    with pytest.raises(EventEvaluationDesignError, match="frozen SHA-256"):
        p4_news_eval._verify_v1_5_historical_artifacts(tmp_path, derived)
