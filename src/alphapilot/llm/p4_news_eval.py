from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from alphapilot.llm.p4_news_event import (
    EventExtractContract,
    EventExtractContractError,
    load_event_extract_contract,
)

JsonObject = dict[str, Any]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EVALUATION_DESIGN_PATH = PROJECT_ROOT / "config/p4_event_evaluation_v1_1.yaml"
EXPECTED_EVALUATION_DESIGN_SHA256 = (
    "8e9c1d107ef235f9c017dbfb679fa01e52e0ff966f01d9efad625110588ebf97"
)
EXPECTED_EVALUATION_SCHEMA_VERSION = "p4.2a-evaluation-design-v1.1"
EXPECTED_BASE_CONTRACT_SHA256 = (
    "b3eb24c63816043edf0ef728d8d9778cd9083d720649d6fff3ae6289bba74300"
)

EXPECTED_SUPERSEDED_FIELDS = (
    "base_annotation_contract.gold_sample.future_40",
    "base_annotation_contract.gold_sample.final_output_jsonl",
    "base_annotation_contract.gold_sample.predictions_output_jsonl",
    "base_annotation_contract.gold_sample.manifest_json",
    "base_annotation_contract.evaluation",
)

EXPECTED_RECEIPT_FIELDS = (
    "design_schema_version",
    "design_sha256",
    "frozen_at_utc",
    "contract_path",
    "contract_sha256",
    "contract_schema_version",
    "model",
    "prompt_path",
    "prompt_sha256",
    "result_schema_path",
    "result_schema_sha256",
    "taxonomy_version",
    "dev_final_predictions_path",
    "dev_final_predictions_sha256",
    "dev_final_predictions_manifest_path",
    "dev_final_predictions_manifest_sha256",
    "dev_final_predictions_row_count",
    "dev_final_predictions_success_count",
    "dev_final_predictions_failure_count",
    "dev_final_predictions_identity_sha256",
    "dev_final_predictions_contract_sha256",
)

EXPECTED_OWNER_FORBIDDEN_FIELDS = (
    "prediction",
    "model_prediction",
    "model_output",
    "predicted_materiality",
    "selection_basis",
    "selection_reason",
    "selection_rank",
    "selection_score",
    "eligible_pool",
    "eligible_pool_size",
    "candidate_pool_membership",
    "prediction_artifact",
)

EXPECTED_OWNER_REQUIRED_FIELDS = (
    "news_item_id",
    "source",
    "url",
    "title",
    "ingested_symbol",
    "published_at",
    "available_time",
    "original_text",
    "input_sha256",
    "text_sha256",
    "annotation_status",
    "gold",
)

EXPECTED_OWNER_COMPLETION_MANIFEST_FIELDS = (
    "schema_version",
    "design_sha256",
    "annotation_contract_sha256",
    "dev_blind_sample_path",
    "dev_blind_sample_sha256",
    "dev_owner_annotations_path",
    "dev_owner_annotations_sha256",
    "dev_owner_annotations_row_count",
    "dev_completed_count",
    "heldout_blind_sample_path",
    "heldout_blind_sample_sha256",
    "heldout_selection_manifest_path",
    "heldout_selection_manifest_sha256",
    "heldout_owner_annotations_path",
    "heldout_owner_annotations_sha256",
    "heldout_owner_annotations_row_count",
    "heldout_completed_count",
    "combined_annotations_path",
    "combined_annotations_sha256",
    "combined_annotations_row_count",
    "combined_ordered_identity_sha256",
    "combined_renumbering_rule",
    "identity_validation_passed",
    "blindness_validation_passed",
    "completed_at_utc",
)

EXPECTED_REPORT_FIELDS = (
    "design.schema_version",
    "design.sha256",
    "prediction_contract.contract_path",
    "prediction_contract.contract_sha256",
    "prediction_contract.model",
    "prediction_contract.prompt_path",
    "prediction_contract.prompt_sha256",
    "prediction_contract.result_schema_path",
    "prediction_contract.result_schema_sha256",
    "prediction_contract.taxonomy_version",
    "prediction_contract.dev_final_predictions_path",
    "prediction_contract.dev_final_predictions_sha256",
    "prediction_contract.dev_final_predictions_manifest_path",
    "prediction_contract.dev_final_predictions_manifest_sha256",
    "prediction_contract.dev_final_predictions_row_count",
    "prediction_contract.dev_final_predictions_success_count",
    "prediction_contract.dev_final_predictions_failure_count",
    "prediction_contract.dev_final_predictions_identity_sha256",
    "prediction_contract.dev_final_predictions_contract_sha256",
    "splits.dev60.sample_count",
    "splits.dev60.final_predictions_sha256",
    "splits.dev60.final_predictions_manifest_sha256",
    "splits.dev60.final_prediction_success_count",
    "splits.dev60.final_prediction_failure_count",
    "splits.dev60.final_prediction_failure_ids",
    "splits.dev60.final_prediction_identity_sha256",
    "splits.dev60.final_prediction_contract_sha256",
    "splits.heldout40.candidate_batch_count",
    "splits.heldout40.candidate_inputs_sha256",
    "splits.heldout40.candidate_predictions_sha256",
    "splits.heldout40.prediction_attempted_count",
    "splits.heldout40.prediction_success_count",
    "splits.heldout40.prediction_failure_count",
    "splits.heldout40.predicted_positive_pool_count",
    "splits.heldout40.predicted_positive_pool_rate",
    "splits.heldout40.selected_count",
    "splits.heldout40.selection_algorithm",
    "splits.heldout40.selection_seed",
    "splits.heldout40.selection_manifest_sha256",
    "one_shot.inference.started_at_utc",
    "one_shot.inference.terminal_at_utc",
    "one_shot.inference.status",
    "one_shot.inference.started_event_count",
    "one_shot.evaluation.started_at_utc",
    "one_shot.evaluation.terminal_at_utc",
    "one_shot.evaluation.status",
    "one_shot.evaluation.started_event_count",
    "owner_delivery.forbidden_field_violation_count",
    "owner_completion.manifest_path",
    "owner_completion.manifest_sha256",
    "owner_completion.dev_blind_sample_sha256",
    "owner_completion.dev_annotations_sha256",
    "owner_completion.dev_completed_count",
    "owner_completion.heldout_blind_sample_sha256",
    "owner_completion.heldout_selection_manifest_sha256",
    "owner_completion.heldout_annotations_sha256",
    "owner_completion.heldout_completed_count",
    "owner_completion.combined_annotations_sha256",
    "owner_completion.combined_row_count",
    "owner_completion.combined_ordered_identity_sha256",
    "owner_completion.combined_renumbering_rule",
    "owner_completion.identity_validation_passed",
    "owner_completion.blindness_validation_passed",
    "metrics.materiality_precision.heldout40.tp",
    "metrics.materiality_precision.heldout40.fp",
    "metrics.materiality_precision.heldout40.denominator",
    "metrics.materiality_precision.heldout40.value",
    "metrics.materiality_precision.heldout40.threshold",
    "metrics.materiality_precision.heldout40.passed",
    "metrics.materiality_precision.dev60.tp",
    "metrics.materiality_precision.dev60.fp",
    "metrics.materiality_precision.dev60.denominator",
    "metrics.materiality_precision.dev60.value",
    "metrics.materiality_recall.dev60.tp",
    "metrics.materiality_recall.dev60.fn",
    "metrics.materiality_recall.dev60.denominator",
    "metrics.materiality_recall.dev60.value",
    "metrics.symbol_exact_set.dev60",
    "metrics.symbol_exact_set.heldout40",
    "metrics.symbol_exact_set.all100",
    "metrics.symbol_bearing_exact_set.dev60",
    "metrics.symbol_bearing_exact_set.heldout40",
    "metrics.symbol_bearing_exact_set.all100",
    "diagnostics.offline_trial.predicted_materiality_gte_2_count",
    "diagnostics.offline_trial.successful_prediction_count",
    "diagnostics.offline_trial.predicted_materiality_gte_2_rate",
    "diagnostics.offline_trial.gold_intersection_failure_count",
    "diagnostics.offline_trial.gold_intersection_failure_ids",
    "diagnostics.active_prediction.gold_failure_count",
    "diagnostics.active_prediction.gold_failure_ids",
    "gates.materiality_precision_heldout40",
    "gates.symbol_mapping_all100",
    "gates.symbol_bearing_exact_set_all100",
    "gates.owner_delivery_blind",
    "gates.heldout_one_shot",
)

_SHA256_LENGTH = 64
_ARTIFACT_ROOT = Path("docs/phase4/eval")


class EventEvaluationDesignError(ValueError):
    """Raised when the pre-registered P4.2a v1.1 design cannot be trusted."""


@dataclass(frozen=True, slots=True)
class EventEvaluationDesign:
    path: Path
    sha256: str
    document: JsonObject
    base_contract: EventExtractContract


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EventEvaluationDesignError(f"{label} must be a mapping")
    return cast(Mapping[str, Any], value)


def _string_sequence(value: object, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not all(isinstance(item, str) for item in value)
    ):
        raise EventEvaluationDesignError(f"{label} must be a string sequence")
    return tuple(cast(Sequence[str], value))


def _artifact_path(
    project_root: Path,
    value: object,
    *,
    label: str,
) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise EventEvaluationDesignError(f"{label} must be a non-blank relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise EventEvaluationDesignError(f"{label} escapes the project root")
    root = project_root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise EventEvaluationDesignError(f"{label} escapes the project root")
    return resolved


def _verify_frozen_artifact(
    project_root: Path,
    entry: Mapping[str, Any],
    *,
    label: str,
) -> Path:
    path = _artifact_path(project_root, entry.get("path"), label=f"{label}.path")
    expected_sha256 = entry.get("sha256")
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise EventEvaluationDesignError(f"{label}.sha256 is invalid")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise EventEvaluationDesignError(f"{label} is unavailable") from exc
    if _sha256_bytes(payload) != expected_sha256:
        raise EventEvaluationDesignError(f"{label} differs from its frozen SHA-256")
    return path


def _validate_base_contract(
    document: Mapping[str, Any],
    project_root: Path,
) -> EventExtractContract:
    base = _mapping(document.get("base_annotation_contract"), "base_annotation_contract")
    if (
        base.get("path") != "config/p4_event_extract_eval_v1.yaml"
        or base.get("sha256") != EXPECTED_BASE_CONTRACT_SHA256
        or base.get("schema_version") != "p4.2a-event-extract-eval-v1"
        or base.get("model") != "qwen3.6-flash"
        or base.get("taxonomy_version") != "p4-news-event-taxonomy-v1"
    ):
        raise EventEvaluationDesignError("base annotation contract binding drifted")
    prompt = _mapping(base.get("prompt"), "base_annotation_contract.prompt")
    result_schema = _mapping(
        base.get("result_schema"),
        "base_annotation_contract.result_schema",
    )
    if (
        prompt.get("path") != "config/prompts/p4_news_event_extract_v1.txt"
        or prompt.get("sha256")
        != "4474d61f17f6c8f9a6c909228423f17cc06083b5776f481c4044c0146efbde9d"
        or result_schema.get("path") != "config/schemas/p4_news_event_v1.schema.json"
        or result_schema.get("sha256")
        != "0ac68654ce23ecd4e537d849d695e092c76dcb9de0fb03793e65ae62b181947f"
    ):
        raise EventEvaluationDesignError("base prompt/schema binding drifted")
    try:
        contract = load_event_extract_contract(
            project_root / "config/p4_event_extract_eval_v1.yaml",
            project_root=project_root,
        )
    except (EventExtractContractError, OSError) as exc:
        raise EventEvaluationDesignError("base annotation contract validation failed") from exc
    if contract.sha256 != EXPECTED_BASE_CONTRACT_SHA256:
        raise EventEvaluationDesignError("base annotation contract SHA-256 drifted")
    return contract


def _validate_artifacts(document: Mapping[str, Any], project_root: Path) -> None:
    artifacts = _mapping(document.get("artifacts"), "artifacts")
    expected_names = {
        "dev_60_frozen_jsonl",
        "dev_final_predictions_jsonl",
        "dev_final_predictions_manifest_json",
        "offline_trial_predictions_jsonl",
        "offline_trial_report_json",
        "prediction_contract_freeze_receipt_json",
        "heldout_candidate_inputs_jsonl",
        "heldout_candidate_predictions_jsonl",
        "heldout_candidate_predictions_manifest_json",
        "heldout_inference_state_jsonl",
        "heldout_selection_manifest_json",
        "heldout_evaluation_state_jsonl",
        "dev_60_owner_annotations_jsonl",
        "heldout_40_blind_sample_jsonl",
        "heldout_40_owner_annotations_jsonl",
        "combined_100_annotations_jsonl",
        "owner_completion_manifest_json",
        "evaluation_report_directory",
    }
    if set(artifacts) != expected_names:
        raise EventEvaluationDesignError("evaluation artifact set drifted")

    for name in (
        "dev_60_frozen_jsonl",
        "offline_trial_predictions_jsonl",
        "offline_trial_report_json",
    ):
        _verify_frozen_artifact(
            project_root,
            _mapping(artifacts.get(name), f"artifacts.{name}"),
            label=f"artifacts.{name}",
        )

    seen_paths: set[Path] = set()
    eval_root = (project_root / _ARTIFACT_ROOT).resolve()
    for name, raw_entry in artifacts.items():
        entry = _mapping(raw_entry, f"artifacts.{name}")
        path = _artifact_path(project_root, entry.get("path"), label=f"artifacts.{name}.path")
        if path != eval_root and not path.is_relative_to(eval_root):
            raise EventEvaluationDesignError(f"artifacts.{name} escapes the eval root")
        if path in seen_paths:
            raise EventEvaluationDesignError("evaluation artifact paths must be unique")
        seen_paths.add(path)
        if name not in {
            "dev_60_frozen_jsonl",
            "offline_trial_predictions_jsonl",
            "offline_trial_report_json",
        }:
            create_only = (
                entry.get("create_only_reports")
                if name == "evaluation_report_directory"
                else entry.get("create_only")
            )
            if create_only is not True:
                raise EventEvaluationDesignError(f"artifacts.{name} must be create-only")


def _validate_splits(document: Mapping[str, Any]) -> None:
    splits = _mapping(document.get("splits"), "splits")
    if set(splits) != {"dev_60", "heldout_40"}:
        raise EventEvaluationDesignError("evaluation split names drifted")
    dev = _mapping(splits.get("dev_60"), "splits.dev_60")
    if (
        dev.get("sample_tag") != "dev60"
        or dev.get("role") != "prompt_development"
        or dev.get("count") != 60
        or dev.get("source_artifact") != "dev_60_frozen_jsonl"
        or dev.get("owner_completed_annotation_artifact")
        != "dev_60_owner_annotations_jsonl"
        or dev.get("prompt_iteration_allowed") is not True
        or dev.get("heldout_data_access_allowed") is not False
        or dev.get("materiality_metrics_gate") is not False
    ):
        raise EventEvaluationDesignError("dev60 contract drifted")

    heldout = _mapping(splits.get("heldout_40"), "splits.heldout_40")
    if (
        heldout.get("sample_tag") != "heldout40"
        or heldout.get("role") != "heldout_test"
        or heldout.get("count") != 40
        or heldout.get("prompt_iteration_allowed") is not False
        or heldout.get("owner_blind_sample_artifact")
        != "heldout_40_blind_sample_jsonl"
        or heldout.get("owner_completed_annotation_artifact")
        != "heldout_40_owner_annotations_jsonl"
        or _string_sequence(
            heldout.get("selection_must_not_create"),
            "heldout selection forbidden create artifacts",
        )
        != (
            "heldout_40_owner_annotations_jsonl",
            "combined_100_annotations_jsonl",
            "owner_completion_manifest_json",
        )
    ):
        raise EventEvaluationDesignError("heldout40 role/count contract drifted")
    batch = _mapping(heldout.get("candidate_batch"), "splits.heldout_40.candidate_batch")
    if (
        batch.get("date_field") != "available_time"
        or batch.get("timezone") != "Asia/Shanghai"
        or batch.get("window_start_inclusive") != "2026-08-04T00:00:00+08:00"
        or batch.get("window_end_exclusive") != "2026-08-06T00:00:00+08:00"
        or batch.get("selection_ready_after") != "2026-08-06T00:10:00+08:00"
        or _string_sequence(batch.get("trading_dates"), "trading_dates")
        != ("2026-08-04", "2026-08-05")
        or batch.get("min_news_item_id_exclusive") != 423
        or _string_sequence(batch.get("sources"), "sources")
        != ("cninfo", "akshare_ths", "sina_company_news")
        or batch.get("prediction_scope") != "all_eligible_batch_rows"
    ):
        raise EventEvaluationDesignError("heldout40 candidate batch drifted")
    pool = _mapping(heldout.get("eligible_pool"), "splits.heldout_40.eligible_pool")
    prediction_inputs = _mapping(
        heldout.get("prediction_inputs"),
        "splits.heldout_40.prediction_inputs",
    )
    if (
        prediction_inputs.get("artifact") != "heldout_candidate_inputs_jsonl"
        or prediction_inputs.get("create_once_before_prediction") is not True
        or prediction_inputs.get("cninfo_announcement_body_required") is not True
        or prediction_inputs.get("second_body_fetch_for_owner_delivery_forbidden") is not True
        or _string_sequence(
            prediction_inputs.get("required_record_fields"),
            "heldout prediction input fields",
        )
        != (
            "news_item_id",
            "source",
            "url",
            "title",
            "ingested_symbol",
            "published_at",
            "available_time",
            "original_text",
            "body_state",
            "text_sha256",
            "input_sha256",
            "body_evidence",
        )
        or _string_sequence(
            prediction_inputs.get("prediction_annotation_join_keys"),
            "heldout prediction/annotation join keys",
        )
        != ("news_item_id", "input_sha256", "text_sha256")
    ):
        raise EventEvaluationDesignError("heldout40 frozen prediction-input contract drifted")
    if (
        pool.get("prediction_status") != "ok"
        or pool.get("materiality_minimum") != 2
        or pool.get("positive_rate_denominator") != "successful_predictions"
        or pool.get("failed_predictions_are_ineligible_and_reported") is not True
    ):
        raise EventEvaluationDesignError("heldout40 positive-pool rule drifted")
    sampling = _mapping(heldout.get("sampling"), "splits.heldout_40.sampling")
    if (
        sampling.get("algorithm") != "sha256_rank_without_replacement_v1"
        or sampling.get("deterministic_seed")
        != "alphapilot-p4.2a-heldout40-v1.1-20260803"
        or sampling.get("rank_input_format")
        != "utf8(seed) || 0x00 || ascii(news_item_id) || 0x00 || ascii(input_sha256)"
        or sampling.get("digest") != "sha256"
        or sampling.get("order") != "digest_ascending_then_news_item_id_ascending"
        or sampling.get("without_replacement") is not True
        or sampling.get("selected_count") != 40
        or sampling.get("insufficient_pool_policy") != "fail_without_substitution"
    ):
        raise EventEvaluationDesignError("heldout40 deterministic sampling contract drifted")


def _validate_freeze_and_one_shot(document: Mapping[str, Any]) -> None:
    freeze = _mapping(
        document.get("prediction_contract_freeze"),
        "prediction_contract_freeze",
    )
    if (
        freeze.get("development_source") != "dev60_only"
        or freeze.get("versioned_contract_required_after_prompt_change") is not True
        or freeze.get("receipt_artifact") != "prediction_contract_freeze_receipt_json"
        or freeze.get("receipt_created_before_heldout_candidate_prediction") is not True
        or freeze.get("receipt_created_after_dev_final_predictions") is not True
        or freeze.get("receipt_create_only") is not True
        or _string_sequence(freeze.get("required_receipt_fields"), "required_receipt_fields")
        != EXPECTED_RECEIPT_FIELDS
        or freeze.get("required_model") != "qwen3.6-flash"
        or freeze.get("required_result_schema_sha256")
        != "0ac68654ce23ecd4e537d849d695e092c76dcb9de0fb03793e65ae62b181947f"
        or freeze.get("required_taxonomy_version") != "p4-news-event-taxonomy-v1"
        or freeze.get("bytes_reverified_before_each_heldout_use") is not True
        or freeze.get("prompt_change_after_receipt_forbidden") is not True
        or freeze.get("heldout_inference_requires_valid_receipt") is not True
    ):
        raise EventEvaluationDesignError("prediction-contract freeze rule drifted")
    dev_final = _mapping(
        freeze.get("dev_final_predictions"),
        "prediction_contract_freeze.dev_final_predictions",
    )
    if (
        dev_final.get("predictions_artifact") != "dev_final_predictions_jsonl"
        or dev_final.get("manifest_artifact") != "dev_final_predictions_manifest_json"
        or dev_final.get("row_count") != 60
        or dev_final.get("required_identity_match_artifact") != "dev_60_frozen_jsonl"
        or dev_final.get("ordered_by") != "news_item_id_ascending"
        or _string_sequence(
            dev_final.get("ordered_identity_fields"),
            "dev final ordered identity fields",
        )
        != ("news_item_id", "input_sha256", "text_sha256")
        or dev_final.get("ordered_identity_digest_format")
        != "ascii(news_item_id) || 0x00 || lowercase_ascii(input_sha256) || 0x00 || "
        "lowercase_ascii(text_sha256) || 0x0A"
        or dev_final.get("ordered_identity_digest") != "sha256"
        or dev_final.get("unique_news_item_ids_required") is not True
        or dev_final.get("success_plus_failure_must_equal_row_count") is not True
        or dev_final.get("contract_sha256_must_equal_receipt_contract_sha256") is not True
        or _string_sequence(
            dev_final.get("manifest_required_fields"),
            "dev final manifest required fields",
        )
        != (
            "design_sha256",
            "contract_sha256",
            "predictions_path",
            "predictions_sha256",
            "row_count",
            "success_count",
            "failure_count",
            "ordered_identity_sha256",
            "completed_at_utc",
        )
    ):
        raise EventEvaluationDesignError("dev-final prediction freeze rule drifted")

    one_shot = _mapping(document.get("one_shot"), "one_shot")
    inference = _mapping(one_shot.get("inference"), "one_shot.inference")
    evaluation = _mapping(one_shot.get("evaluation"), "one_shot.evaluation")
    if (
        inference.get("state_artifact") != "heldout_inference_state_jsonl"
        or inference.get("format") != "append_only_jsonl"
        or inference.get("started_event") != "inference_started"
        or _string_sequence(inference.get("terminal_events"), "inference terminal events")
        != ("inference_completed", "inference_failed")
        or inference.get("started_fsynced_before_first_model_call") is not True
        or inference.get("maximum_started_events") != 1
        or inference.get("existing_started_event_blocks_rerun") is not True
        or _string_sequence(
            inference.get("required_preconditions"),
            "heldout inference preconditions",
        )
        != (
            "valid_prediction_contract_freeze_receipt",
            "dev_final_predictions_bytes_and_manifest_verified",
            "dev_final_predictions_contract_matches_active_contract",
        )
    ):
        raise EventEvaluationDesignError("heldout inference one-shot rule drifted")
    if (
        evaluation.get("state_artifact") != "heldout_evaluation_state_jsonl"
        or evaluation.get("format") != "append_only_jsonl"
        or evaluation.get("started_event") != "evaluation_started"
        or _string_sequence(evaluation.get("terminal_events"), "evaluation terminal events")
        != ("evaluation_completed", "evaluation_failed")
        or evaluation.get("started_fsynced_before_first_score_read") is not True
        or evaluation.get("maximum_started_events") != 1
        or evaluation.get("existing_started_event_blocks_reevaluation") is not True
        or _string_sequence(
            evaluation.get("required_preconditions"),
            "heldout evaluation preconditions",
        )
        != (
            "valid_owner_completion_manifest",
            "owner_completion_artifact_bytes_and_counts_verified",
            "combined_annotations_sha256_and_identity_match_manifest",
        )
        or one_shot.get("invalidation_policy")
        != "register_new_heldout_sample_and_design_version"
    ):
        raise EventEvaluationDesignError("heldout evaluation one-shot rule drifted")


def _validate_owner_completion(document: Mapping[str, Any]) -> None:
    completion = _mapping(
        document.get("owner_annotation_completion"),
        "owner_annotation_completion",
    )
    expected_keys = {
        "schema_version",
        "manifest_artifact",
        "manifest_create_only",
        "dev",
        "heldout",
        "combined",
        "preconditions_before_combined_create",
        "required_manifest_fields",
    }
    if (
        set(completion) != expected_keys
        or completion.get("schema_version") != "p4.2a-owner-completion-v1.1"
        or completion.get("manifest_artifact") != "owner_completion_manifest_json"
        or completion.get("manifest_create_only") is not True
    ):
        raise EventEvaluationDesignError("owner-completion identity contract drifted")

    dev = _mapping(completion.get("dev"), "owner_annotation_completion.dev")
    heldout = _mapping(
        completion.get("heldout"),
        "owner_annotation_completion.heldout",
    )
    combined = _mapping(
        completion.get("combined"),
        "owner_annotation_completion.combined",
    )
    if dict(dev) != {
        "blind_source_artifact": "dev_60_frozen_jsonl",
        "completed_annotation_artifact": "dev_60_owner_annotations_jsonl",
        "required_row_count": 60,
        "required_completed_count": 60,
    }:
        raise EventEvaluationDesignError("dev owner-completion contract drifted")
    if dict(heldout) != {
        "blind_source_artifact": "heldout_40_blind_sample_jsonl",
        "selection_manifest_artifact": "heldout_selection_manifest_json",
        "completed_annotation_artifact": "heldout_40_owner_annotations_jsonl",
        "required_row_count": 40,
        "required_completed_count": 40,
    }:
        raise EventEvaluationDesignError("heldout owner-completion contract drifted")
    if (
        set(combined)
        != {
            "artifact",
            "required_row_count",
            "create_only_after_preconditions",
            "order",
            "renumbering_rule",
            "ordered_identity_fields",
            "ordered_identity_digest_format",
            "ordered_identity_digest",
        }
        or combined.get("artifact") != "combined_100_annotations_jsonl"
        or combined.get("required_row_count") != 100
        or combined.get("create_only_after_preconditions") is not True
        or combined.get("order") != "dev_then_heldout"
        or combined.get("renumbering_rule")
        != "dev_preserve_1_60_then_heldout_add_60_to_61_100"
        or _string_sequence(
            combined.get("ordered_identity_fields"),
            "combined owner annotation identity fields",
        )
        != ("sample_index", "news_item_id", "input_sha256", "text_sha256")
        or combined.get("ordered_identity_digest_format")
        != "ascii(sample_index) || 0x00 || ascii(news_item_id) || 0x00 || "
        "lowercase_ascii(input_sha256) || 0x00 || lowercase_ascii(text_sha256) || 0x0A"
        or combined.get("ordered_identity_digest") != "sha256"
    ):
        raise EventEvaluationDesignError("combined owner-completion contract drifted")
    if (
        _string_sequence(
            completion.get("preconditions_before_combined_create"),
            "owner-completion preconditions",
        )
        != (
            "dev_owner_annotations_all_completed",
            "heldout_owner_annotations_all_completed",
            "dev_identity_and_hash_match_blind_source",
            "heldout_identity_and_hash_match_blind_source",
            "owner_outputs_pass_forbidden_field_blindness_check",
            "combined_target_and_completion_manifest_absent",
        )
        or _string_sequence(
            completion.get("required_manifest_fields"),
            "owner-completion manifest fields",
        )
        != EXPECTED_OWNER_COMPLETION_MANIFEST_FIELDS
    ):
        raise EventEvaluationDesignError("owner-completion proof contract drifted")


def _validate_owner_and_metrics(document: Mapping[str, Any]) -> None:
    owner = _mapping(document.get("owner_delivery"), "owner_delivery")
    if (
        owner.get("predictions_visible") is not False
        or owner.get("selection_basis_visible") is not False
        or _string_sequence(owner.get("forbidden_fields"), "owner forbidden fields")
        != EXPECTED_OWNER_FORBIDDEN_FIELDS
        or owner.get("forbidden_field_policy") != "fail_closed"
        or _string_sequence(owner.get("required_owner_fields"), "required owner fields")
        != EXPECTED_OWNER_REQUIRED_FIELDS
    ):
        raise EventEvaluationDesignError("blind owner-delivery contract drifted")

    _validate_owner_completion(document)
    evaluation = _mapping(document.get("evaluation"), "evaluation")
    split_counts = _mapping(evaluation.get("split_counts"), "evaluation.split_counts")
    frozen_diagnostics = _mapping(
        evaluation.get("frozen_diagnostics"),
        "evaluation.frozen_diagnostics",
    )
    if (
        evaluation.get("sample_count") != 100
        or dict(split_counts) != {"dev60": 60, "heldout40": 40}
        or evaluation.get("prompt_iteration_scope") != "dev60_only"
        or evaluation.get("heldout_test_runs_maximum") != 1
        or evaluation.get("materiality_positive_definition") != "materiality_gte_2"
        or evaluation.get("materiality_precision_formula") != "tp_divided_by_tp_plus_fp"
        or evaluation.get("materiality_zero_predicted_positive_policy") != "fail"
        or evaluation.get("materiality_precision_gate_scope") != "heldout40"
        or evaluation.get("materiality_precision_minimum") != 0.80
        or evaluation.get("symbol_mapping_formula") != "exact_set_match_accuracy"
        or evaluation.get("symbol_mapping_gate_scope") != "all100"
        or evaluation.get("symbol_mapping_accuracy_minimum") != 0.95
        or evaluation.get("symbol_bearing_exact_set_accuracy_minimum") != 0.95
        or _string_sequence(evaluation.get("report_split_metrics"), "report split metrics")
        != ("dev60", "heldout40", "all100")
        or evaluation.get("threshold_changes_forbidden") is not True
        or evaluation.get("failed_rounds_append_only") is not True
        or evaluation.get("heldout_rerun_forbidden") is not True
        or dict(frozen_diagnostics)
        != {
            "offline_trial_predictions_artifact": "offline_trial_predictions_jsonl",
            "offline_trial_report_artifact": "offline_trial_report_json",
            "offline_trial_gold_intersection_failure_ids": [190],
            "offline_trial_failure_counts_for_recall_not_precision": True,
            "active_prediction_failures_reported_separately": True,
        }
        or _string_sequence(evaluation.get("required_report_fields"), "required report fields")
        != EXPECTED_REPORT_FIELDS
    ):
        raise EventEvaluationDesignError("evaluation metrics/report contract drifted")


def load_event_evaluation_design(
    path: Path = DEFAULT_EVALUATION_DESIGN_PATH,
    *,
    project_root: Path = PROJECT_ROOT,
) -> EventEvaluationDesign:
    """Load the byte-frozen P4.2a v1.1 dev/heldout evaluation design."""
    try:
        payload = path.resolve().read_bytes()
    except OSError as exc:
        raise EventEvaluationDesignError("P4.2a v1.1 design is unavailable") from exc
    digest = _sha256_bytes(payload)
    if digest != EXPECTED_EVALUATION_DESIGN_SHA256:
        raise EventEvaluationDesignError("P4.2a v1.1 design differs from its frozen SHA-256")
    try:
        loaded: object = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise EventEvaluationDesignError("P4.2a v1.1 design is invalid YAML") from exc
    if not isinstance(loaded, dict):
        raise EventEvaluationDesignError("P4.2a v1.1 design must be a mapping")
    document = cast(JsonObject, loaded)
    expected_top_level = {
        "schema_version",
        "owner_spec_commit",
        "pre_registered_at",
        "production_writes_allowed",
        "artifact_root",
        "base_annotation_contract",
        "supersedes",
        "artifacts",
        "splits",
        "prediction_contract_freeze",
        "one_shot",
        "owner_delivery",
        "owner_annotation_completion",
        "evaluation",
        "isolation",
    }
    if set(document) != expected_top_level:
        raise EventEvaluationDesignError("P4.2a v1.1 top-level contract drifted")
    if (
        document.get("schema_version") != EXPECTED_EVALUATION_SCHEMA_VERSION
        or document.get("owner_spec_commit")
        != "8e8b9c343f817a6c9c0b204cc1d3d49c9f277a0f"
        or document.get("production_writes_allowed") is not False
        or document.get("artifact_root") != _ARTIFACT_ROOT.as_posix()
        or _string_sequence(document.get("supersedes"), "supersedes")
        != EXPECTED_SUPERSEDED_FIELDS
    ):
        raise EventEvaluationDesignError("P4.2a v1.1 identity/isolation binding drifted")

    base_contract = _validate_base_contract(document, project_root)
    _validate_artifacts(document, project_root)
    _validate_splits(document)
    _validate_freeze_and_one_shot(document)
    _validate_owner_and_metrics(document)
    isolation = _mapping(document.get("isolation"), "isolation")
    if dict(isolation) != {
        "p4_1_files_must_remain_unchanged": True,
        "p4_2b_unlocked": False,
        "production_database_mode": "read_only_query_only",
        "production_writes_allowed": False,
        "scheduler_changes_allowed": False,
        "proposals_or_orders_allowed": False,
    }:
        raise EventEvaluationDesignError("P4.2a v1.1 runtime isolation drifted")
    return EventEvaluationDesign(
        path=path.resolve(),
        sha256=digest,
        document=document,
        base_contract=base_contract,
    )


__all__ = [
    "DEFAULT_EVALUATION_DESIGN_PATH",
    "EXPECTED_BASE_CONTRACT_SHA256",
    "EXPECTED_EVALUATION_DESIGN_SHA256",
    "EXPECTED_EVALUATION_SCHEMA_VERSION",
    "EXPECTED_OWNER_COMPLETION_MANIFEST_FIELDS",
    "EXPECTED_OWNER_FORBIDDEN_FIELDS",
    "EXPECTED_OWNER_REQUIRED_FIELDS",
    "EXPECTED_RECEIPT_FIELDS",
    "EXPECTED_REPORT_FIELDS",
    "EventEvaluationDesign",
    "EventEvaluationDesignError",
    "load_event_evaluation_design",
]
