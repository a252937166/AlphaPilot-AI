from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PREREG_PATH = Path(
    "docs/phase4/reports/P4.2a-v2-heldout-preregistration-20260810.json"
)
PREREG_SHA256 = "ccecbf5ca7b48b16e445318b8c94a08927432f92c7e8c12f8ab40f2916578705"
WRAPPER_PATH = Path("config/p4_event_extract_eval_v2-heldout-qwen3.6-plus.yaml")
WRAPPER_SHA256 = "26be1765204b122908e7bd09cac857c33bd3140233df47dc3358bc590e020199"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_object,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )
    assert isinstance(value, dict)
    return value


def test_v2_heldout_preregistration_freezes_owner_selected_plus_contract() -> None:
    prereg_path = PROJECT_ROOT / PREREG_PATH
    wrapper_path = PROJECT_ROOT / WRAPPER_PATH
    assert _sha256(prereg_path) == PREREG_SHA256
    assert _sha256(wrapper_path) == WRAPPER_SHA256
    prereg = _json(prereg_path)
    wrapper = yaml.safe_load(wrapper_path.read_bytes())
    assert isinstance(wrapper, dict)

    assert prereg["status"] == (
        "PREREGISTERED_BEFORE_SYNTHETIC_REHEARSAL_AND_ANY_HELDOUT_ARTIFACT"
    )
    assert prereg["design"] == {
        "path": "config/p4_event_evaluation_v2.yaml",
        "sha256": "18a2428a4ec04bfea6e4f4d70692f38ea82fbaee5a223f30f2465b895b238e21",
        "schema_version": "p4.2a-evaluation-design-v2",
    }
    selected = prereg["selected_extractor"]
    assert selected["model"] == "qwen3.6-plus"
    assert selected["round_3_prompt"]["sha256"] == (
        "0291dc882aac42878ba00c4ed3970da72f19508308cd39211467b4fd92294f44"
    )
    assert selected["round_3_contract"]["sha256"] == (
        "fa75a6cf33065745d02f74fe39e4f102723da43f37ac549058bb34fa8256a181"
    )
    assert selected["heldout_execution_contract"] == {
        "path": WRAPPER_PATH.as_posix(),
        "sha256": WRAPPER_SHA256,
    }
    assert wrapper["selected_development_contract"]["sha256"] == (
        selected["round_3_contract"]["sha256"]
    )
    assert wrapper["contract_files"]["prompt"]["sha256"] == (
        selected["round_3_prompt"]["sha256"]
    )
    assert wrapper["llm"]["model"] == "qwen3.6-plus"
    assert wrapper["llm"]["max_retries"] == 0
    assert wrapper["llm"]["enable_thinking"] is False
    assert wrapper["llm"]["explicit_cache"]["enabled"] is False


def test_v2_heldout_preregistration_forbids_unmeasured_cost_optimisations() -> None:
    prereg = _json(PROJECT_ROOT / PREREG_PATH)
    request = prereg["request_contract"]
    assert request["one_news_item_per_request"] is True
    assert request["one_request_per_eligible_candidate"] is True
    assert request["multi_item_prompt_forbidden"] is True
    assert request["prefilter_forbidden"] is True
    assert request["additional_body_shortening_for_cost_forbidden"] is True
    assert request["body_representation"] == (
        "exact_round_3_frozen_contract_representation_including_its_existing_14000_character_bound"
    )
    assert request["any_candidate_failure"] == (
        "terminal_inference_failed_no_sampling_no_retry"
    )

    frame = prereg["source_frame"]
    assert frame["start_inclusive"] == "2026-08-06T00:00:00+08:00"
    assert frame["end_exclusive"] == "2026-08-09T00:00:00+08:00"
    assert frame["expected_raw_candidate_count"] == 4048
    assert sum(frame["expected_raw_candidates_by_source"].values()) == 4048
    sampling = prereg["eligibility_and_sampling"]
    assert sampling["strata"]["predicted_positive"]["selected_count"] == 40
    assert sampling["strata"]["predicted_negative"]["selected_count"] == 20
    assert sampling["strata"]["extract_failed"]["selected_count"] == 0
    assert sampling["total_selected_count"] == 60
    assert sampling["retired_selection"]["count"] == 40

    assert prereg["ordering"][0] == (
        "commit_this_preregistration_and_runner_tests_before_any_heldout_action"
    )
    assert prereg["ordering"][1] == (
        "complete_synthetic_full_path_rehearsal_and_freeze_pass_receipt"
    )
    authorization = prereg["authorization_boundary"]
    assert authorization["independent_review_required_before_one_shot_evaluation"] is True
    assert prereg["safety"]["p4_2a_done"] is False
    assert prereg["safety"]["p4_2b_unlocked"] is False
    assert prereg["safety"]["p4_3_unlocked"] is False


def test_v2_heldout_selection_and_contract_freeze_hash_chain_is_closed() -> None:
    prereg = _json(PROJECT_ROOT / PREREG_PATH)
    authorities = prereg["authorities"]
    for key in ("selection_outcome", "selected_contract_freeze"):
        binding = authorities[key]
        assert _sha256(PROJECT_ROOT / binding["path"]) == binding["sha256"]
    selection = _json(PROJECT_ROOT / authorities["selection_outcome"]["path"])
    freeze = _json(PROJECT_ROOT / authorities["selected_contract_freeze"]["path"])
    assert selection["development_round"]["original_rule_selected_model"] == (
        "qwen3.7-flash"
    )
    assert selection["selected_model"] == "qwen3.6-plus"
    assert selection["owner_amendment"]["sha256"] == (
        authorities["owner_model_selection_amendment"]["sha256"]
    )
    assert freeze["selection_outcome"]["sha256"] == (
        authorities["selection_outcome"]["sha256"]
    )
    assert freeze["heldout_execution_contract"]["sha256"] == WRAPPER_SHA256
    assert freeze["request_shape"]["one_news_item_per_request"] is True
    assert freeze["request_shape"]["prefilter_forbidden"] is True
