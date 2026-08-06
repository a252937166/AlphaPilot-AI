from __future__ import annotations

import copy
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
LEGACY_EVALUATION_DESIGN_PATH = PROJECT_ROOT / "config/p4_event_evaluation_v1_1.yaml"
EVALUATION_DESIGN_V1_2_PATH = PROJECT_ROOT / "config/p4_event_evaluation_v1_2.yaml"
EVALUATION_DESIGN_V1_3_PATH = PROJECT_ROOT / "config/p4_event_evaluation_v1_3.yaml"
EVALUATION_DESIGN_V1_4_PATH = PROJECT_ROOT / "config/p4_event_evaluation_v1_4.yaml"
EVALUATION_DESIGN_V1_5_PATH = PROJECT_ROOT / "config/p4_event_evaluation_v1_5.yaml"
EVALUATION_DESIGN_V1_6_PATH = PROJECT_ROOT / "config/p4_event_evaluation_v1_6.yaml"
EVALUATION_DESIGN_V1_7_PATH = PROJECT_ROOT / "config/p4_event_evaluation_v1_7.yaml"
DEFAULT_EVALUATION_DESIGN_PATH = LEGACY_EVALUATION_DESIGN_PATH
EXPECTED_EVALUATION_DESIGN_SHA256 = (
    "8e9c1d107ef235f9c017dbfb679fa01e52e0ff966f01d9efad625110588ebf97"
)
EXPECTED_EVALUATION_DESIGN_V1_2_SHA256 = (
    "1f4e5f6f65a609842c0074735174a23de582c2aff1053b42716eb7ed8434b780"
)
EXPECTED_EVALUATION_DESIGN_V1_3_SHA256 = (
    "79b58de72d797ee9a9e93ec0f37d5f1c1b0b0d86ca49db1736ecdb5cdc314aac"
)
EXPECTED_EVALUATION_DESIGN_V1_4_SHA256 = (
    "3a392a6c834cdde219f54e149f22e235b0316765d0bd69501bb1f312d7ee0e33"
)
EXPECTED_EVALUATION_DESIGN_V1_5_SHA256 = (
    "6a8193828df380a94b36fd7b0bc995930e64909339cd009c834d3487d3ae3c05"
)
EXPECTED_EVALUATION_DESIGN_V1_6_SHA256 = (
    "57e04f99a8ee91b64fe0aa4dc479e95c3de65fd31f04ac6c5879d4f81e5539c9"
)
EXPECTED_EVALUATION_DESIGN_V1_7_SHA256 = (
    "4c7964ad547820f5672631939af93978f11cb9f91e5921087770ac7d0d79bec1"
)
EXPECTED_EVALUATION_SCHEMA_VERSION = "p4.2a-evaluation-design-v1.1"
EXPECTED_EVALUATION_V1_2_SCHEMA_VERSION = "p4.2a-evaluation-design-v1.2"
EXPECTED_EVALUATION_V1_3_SCHEMA_VERSION = "p4.2a-evaluation-design-v1.3"
EXPECTED_EVALUATION_V1_4_SCHEMA_VERSION = "p4.2a-evaluation-design-v1.4"
EXPECTED_EVALUATION_V1_5_SCHEMA_VERSION = "p4.2a-evaluation-design-v1.5"
EXPECTED_EVALUATION_V1_6_SCHEMA_VERSION = "p4.2a-evaluation-design-v1.6"
EXPECTED_EVALUATION_V1_7_SCHEMA_VERSION = "p4.2a-evaluation-design-v1.7"
EXPECTED_BASE_CONTRACT_SHA256 = "b3eb24c63816043edf0ef728d8d9778cd9083d720649d6fff3ae6289bba74300"

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
_V1_3_HISTORICAL_ARTIFACT_PATHS = {
    "predictions_sha256": Path(
        "docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.3-r1.predictions.jsonl"
    ),
    "manifest_sha256": Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.3-r1.manifest.json"),
    "report_sha256": Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.3-r1.report.json"),
    "blocker_sha256": Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.3-r1.blocker.json"),
}
_V1_4_HISTORICAL_ARTIFACT_PATHS = {
    "predictions": Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.4-r1.predictions.jsonl"),
    "manifest": Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.4-r1.manifest.json"),
    "report": Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.4-r1.report.json"),
    "blocker": Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.4-r1.blocker.json"),
}
_V1_5_HISTORICAL_ARTIFACT_PATHS = {
    "predictions": Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.5-r1.predictions.jsonl"),
    "manifest": Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.5-r1.manifest.json"),
    "report": Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.5-r1.report.json"),
    "blocker": Path("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.5-r1.blocker.json"),
}


class EventEvaluationDesignError(ValueError):
    """Raised when the pre-registered P4.2a v1.1 design cannot be trusted."""


@dataclass(frozen=True, slots=True)
class EventEvaluationDesign:
    path: Path
    sha256: str
    document: JsonObject
    base_contract: EventExtractContract
    prediction_contract: EventExtractContract


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


def _verify_v1_3_historical_artifacts(
    project_root: Path,
    v1_3_actual: Mapping[str, Any],
) -> None:
    root = project_root.resolve()
    for sha_field, relative_path in _V1_3_HISTORICAL_ARTIFACT_PATHS.items():
        expected_sha256 = v1_3_actual.get(sha_field)
        path = root / relative_path
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != _SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in expected_sha256)
            or path.is_symlink()
            or not path.is_file()
        ):
            raise EventEvaluationDesignError(f"historical v1.3 {sha_field} artifact is unavailable")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise EventEvaluationDesignError(
                f"historical v1.3 {sha_field} artifact is unavailable"
            ) from exc
        if _sha256_bytes(payload) != expected_sha256:
            raise EventEvaluationDesignError(
                f"historical v1.3 {sha_field} artifact differs from its frozen SHA-256"
            )


def _verify_v1_4_historical_artifacts(
    project_root: Path,
    derived: Mapping[str, Any],
) -> None:
    artifacts = _mapping(
        derived.get("artifacts"),
        "derived_after_failed_round.artifacts",
    )
    if set(artifacts) != set(_V1_4_HISTORICAL_ARTIFACT_PATHS):
        raise EventEvaluationDesignError("historical v1.4 artifact set drifted")
    root = project_root.resolve()
    for name, expected_relative in _V1_4_HISTORICAL_ARTIFACT_PATHS.items():
        entry = _mapping(
            artifacts.get(name),
            f"derived_after_failed_round.artifacts.{name}",
        )
        path = _artifact_path(
            project_root,
            entry.get("path"),
            label=f"derived_after_failed_round.artifacts.{name}.path",
        )
        expected_sha256 = entry.get("sha256")
        if (
            path != root / expected_relative
            or not isinstance(expected_sha256, str)
            or len(expected_sha256) != _SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in expected_sha256)
            or path.is_symlink()
            or not path.is_file()
        ):
            raise EventEvaluationDesignError(f"historical v1.4 {name} artifact is unavailable")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise EventEvaluationDesignError(
                f"historical v1.4 {name} artifact is unavailable"
            ) from exc
        if _sha256_bytes(payload) != expected_sha256:
            raise EventEvaluationDesignError(
                f"historical v1.4 {name} artifact differs from its frozen SHA-256"
            )


def _verify_v1_5_historical_artifacts(
    project_root: Path,
    derived: Mapping[str, Any],
) -> None:
    artifacts = _mapping(
        derived.get("artifacts"),
        "derived_after_failed_round.artifacts",
    )
    if set(artifacts) != set(_V1_5_HISTORICAL_ARTIFACT_PATHS):
        raise EventEvaluationDesignError("historical v1.5 artifact set drifted")
    root = project_root.resolve()
    for name, expected_relative in _V1_5_HISTORICAL_ARTIFACT_PATHS.items():
        entry = _mapping(
            artifacts.get(name),
            f"derived_after_failed_round.artifacts.{name}",
        )
        path = _artifact_path(
            project_root,
            entry.get("path"),
            label=f"derived_after_failed_round.artifacts.{name}.path",
        )
        expected_sha256 = entry.get("sha256")
        if (
            path != root / expected_relative
            or not isinstance(expected_sha256, str)
            or len(expected_sha256) != _SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in expected_sha256)
            or path.is_symlink()
            or not path.is_file()
        ):
            raise EventEvaluationDesignError(f"historical v1.5 {name} artifact is unavailable")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise EventEvaluationDesignError(
                f"historical v1.5 {name} artifact is unavailable"
            ) from exc
        if _sha256_bytes(payload) != expected_sha256:
            raise EventEvaluationDesignError(
                f"historical v1.5 {name} artifact differs from its frozen SHA-256"
            )


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
        or dev.get("owner_completed_annotation_artifact") != "dev_60_owner_annotations_jsonl"
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
        or heldout.get("owner_blind_sample_artifact") != "heldout_40_blind_sample_jsonl"
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
        or sampling.get("deterministic_seed") != "alphapilot-p4.2a-heldout40-v1.1-20260803"
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
        or one_shot.get("invalidation_policy") != "register_new_heldout_sample_and_design_version"
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
        or combined.get("renumbering_rule") != "dev_preserve_1_60_then_heldout_add_60_to_61_100"
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


def _load_v1_1_event_evaluation_design(
    path: Path = LEGACY_EVALUATION_DESIGN_PATH,
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
        or document.get("owner_spec_commit") != "8e8b9c343f817a6c9c0b204cc1d3d49c9f277a0f"
        or document.get("production_writes_allowed") is not False
        or document.get("artifact_root") != _ARTIFACT_ROOT.as_posix()
        or _string_sequence(document.get("supersedes"), "supersedes") != EXPECTED_SUPERSEDED_FIELDS
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
        prediction_contract=base_contract,
    )


_V1_2_CREATE_ONLY_ARTIFACTS = frozenset(
    {
        "dev_final_predictions_jsonl",
        "dev_final_predictions_manifest_json",
        "prediction_contract_freeze_receipt_json",
        "heldout_candidate_inputs_jsonl",
        "heldout_candidate_predictions_jsonl",
        "heldout_candidate_predictions_manifest_json",
        "heldout_inference_state_jsonl",
        "heldout_selection_manifest_json",
        "heldout_evaluation_state_jsonl",
        "heldout_40_blind_sample_jsonl",
        "heldout_40_owner_annotations_jsonl",
        "combined_100_annotations_jsonl",
        "owner_completion_manifest_json",
        "evaluation_report_directory",
    }
)
_V1_2_PROVENANCE_FIELDS = (
    "annotation_type",
    "drafter_id",
    "adjudicator_id",
)


def _load_v1_2_event_evaluation_design(
    path: Path,
    *,
    project_root: Path,
) -> EventEvaluationDesign:
    """Load the v1.2 overlay while preserving the frozen v1.1 sampling design."""

    try:
        payload = path.resolve().read_bytes()
    except OSError as exc:
        raise EventEvaluationDesignError("P4.2a v1.2 design is unavailable") from exc
    digest = _sha256_bytes(payload)
    if digest != EXPECTED_EVALUATION_DESIGN_V1_2_SHA256:
        raise EventEvaluationDesignError("P4.2a v1.2 design differs from its frozen SHA-256")
    try:
        loaded: object = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise EventEvaluationDesignError("P4.2a v1.2 design is invalid YAML") from exc
    if not isinstance(loaded, dict):
        raise EventEvaluationDesignError("P4.2a v1.2 design must be a mapping")
    overlay = cast(JsonObject, loaded)
    expected_top_level = {
        "schema_version",
        "owner_spec_commit",
        "pre_registered_at",
        "production_writes_allowed",
        "artifact_root",
        "extends_design",
        "active_prediction_contract",
        "artifact_overrides",
        "frozen_artifact_overrides",
        "prediction_contract_freeze",
        "heldout_annotation_provenance",
        "owner_annotation_completion",
        "evaluation",
        "isolation",
    }
    if set(overlay) != expected_top_level:
        raise EventEvaluationDesignError("P4.2a v1.2 top-level contract drifted")
    if (
        overlay.get("schema_version") != EXPECTED_EVALUATION_V1_2_SCHEMA_VERSION
        or overlay.get("owner_spec_commit") != "5a7225b5e47c84949eb159d85703180f33a40cc7"
        or overlay.get("production_writes_allowed") is not False
        or overlay.get("artifact_root") != _ARTIFACT_ROOT.as_posix()
    ):
        raise EventEvaluationDesignError("P4.2a v1.2 identity/isolation binding drifted")

    extends = _mapping(overlay.get("extends_design"), "extends_design")
    inheritance = _mapping(extends.get("inheritance"), "extends_design.inheritance")
    if (
        extends.get("path") != "config/p4_event_evaluation_v1_1.yaml"
        or extends.get("sha256") != EXPECTED_EVALUATION_DESIGN_SHA256
        or extends.get("schema_version") != EXPECTED_EVALUATION_SCHEMA_VERSION
        or dict(inheritance)
        != {
            "sample_identity": "byte_frozen",
            "heldout_candidate_window": "byte_frozen",
            "heldout_selection_seed": "byte_frozen",
            "evaluation_thresholds": "byte_frozen",
        }
    ):
        raise EventEvaluationDesignError("P4.2a v1.2 inheritance contract drifted")
    base_design = _load_v1_1_event_evaluation_design(
        project_root / "config/p4_event_evaluation_v1_1.yaml",
        project_root=project_root,
    )

    active = _mapping(
        overlay.get("active_prediction_contract"),
        "active_prediction_contract",
    )
    active_path = _artifact_path(
        project_root,
        active.get("path"),
        label="active_prediction_contract.path",
    )
    active_sha256 = active.get("sha256")
    if not isinstance(active_sha256, str) or len(active_sha256) != _SHA256_LENGTH:
        raise EventEvaluationDesignError("active_prediction_contract.sha256 is invalid")
    try:
        active_bytes = active_path.read_bytes()
    except OSError as exc:
        raise EventEvaluationDesignError("active prediction contract is unavailable") from exc
    if _sha256_bytes(active_bytes) != active_sha256:
        raise EventEvaluationDesignError(
            "active prediction contract differs from its frozen SHA-256"
        )
    try:
        prediction_contract = load_event_extract_contract(
            active_path,
            project_root=project_root,
        )
    except (EventExtractContractError, OSError) as exc:
        raise EventEvaluationDesignError("active prediction contract validation failed") from exc
    active_prompt = _mapping(
        active.get("prompt"),
        "active_prediction_contract.prompt",
    )
    active_schema = _mapping(
        active.get("result_schema"),
        "active_prediction_contract.result_schema",
    )
    contract_files = _mapping(
        prediction_contract.document.get("contract_files"),
        "active prediction contract files",
    )
    contract_prompt = _mapping(
        contract_files.get("prompt"),
        "active prediction contract prompt",
    )
    contract_schema = _mapping(
        contract_files.get("schema"),
        "active prediction contract schema",
    )
    taxonomy = _mapping(
        prediction_contract.document.get("taxonomy"),
        "active prediction contract taxonomy",
    )
    explicit_cache = _mapping(
        active.get("explicit_cache"),
        "active_prediction_contract.explicit_cache",
    )
    if (
        active.get("schema_version") != prediction_contract.document.get("schema_version")
        or active.get("model") != prediction_contract.model
        or active.get("endpoint") != prediction_contract.endpoint
        or dict(active_prompt) != dict(contract_prompt)
        or dict(active_schema) != dict(contract_schema)
        or active.get("taxonomy_version") != taxonomy.get("version")
        or dict(explicit_cache) != {"enabled": False, "cache_control": None}
        or prediction_contract.explicit_cache_enabled is not False
    ):
        raise EventEvaluationDesignError("active prediction contract binding drifted")

    artifacts = copy.deepcopy(cast(JsonObject, base_design.document["artifacts"]))
    artifact_overrides = _mapping(
        overlay.get("artifact_overrides"),
        "artifact_overrides",
    )
    if set(artifact_overrides) != _V1_2_CREATE_ONLY_ARTIFACTS:
        raise EventEvaluationDesignError("P4.2a v1.2 create-only artifact set drifted")
    seen_paths: set[Path] = set()
    eval_root = (project_root / _ARTIFACT_ROOT).resolve()
    for name, raw_entry in artifact_overrides.items():
        entry = _mapping(raw_entry, f"artifact_overrides.{name}")
        path_value = _artifact_path(
            project_root,
            entry.get("path"),
            label=f"artifact_overrides.{name}.path",
        )
        if path_value != eval_root and not path_value.is_relative_to(eval_root):
            raise EventEvaluationDesignError(f"artifact_overrides.{name} escapes the eval root")
        if "/v1.2" not in path_value.as_posix() and "v1.2" not in path_value.name:
            raise EventEvaluationDesignError(f"artifact_overrides.{name} is not v1.2 namespaced")
        if path_value in seen_paths:
            raise EventEvaluationDesignError("P4.2a v1.2 artifact paths must be unique")
        seen_paths.add(path_value)
        create_only = (
            entry.get("create_only_reports")
            if name == "evaluation_report_directory"
            else entry.get("create_only")
        )
        if create_only is not True:
            raise EventEvaluationDesignError(f"artifact_overrides.{name} must be create-only")
        artifacts[name] = dict(entry)

    frozen_overrides = _mapping(
        overlay.get("frozen_artifact_overrides"),
        "frozen_artifact_overrides",
    )
    if set(frozen_overrides) != {"dev_60_owner_annotations_jsonl"}:
        raise EventEvaluationDesignError("P4.2a v1.2 frozen artifact set drifted")
    dev_labels = _mapping(
        frozen_overrides.get("dev_60_owner_annotations_jsonl"),
        "frozen_artifact_overrides.dev_60_owner_annotations_jsonl",
    )
    if (
        dev_labels.get("annotation_type") != "ai_drafted"
        or dev_labels.get("metric_semantics") != "model_interagreement"
        or dev_labels.get("human_gold_claim_allowed") is not False
    ):
        raise EventEvaluationDesignError("P4.2a v1.2 dev annotation semantics drifted")
    _verify_frozen_artifact(
        project_root,
        dev_labels,
        label="frozen_artifact_overrides.dev_60_owner_annotations_jsonl",
    )
    artifacts["dev_60_owner_annotations_jsonl"] = dict(dev_labels)

    freeze_overlay = _mapping(
        overlay.get("prediction_contract_freeze"),
        "prediction_contract_freeze",
    )
    expected_freeze_keys = {
        "required_contract_artifact",
        "required_model",
        "required_endpoint",
        "required_prompt_sha256",
        "required_result_schema_sha256",
        "required_taxonomy_version",
        "required_explicit_cache_enabled",
        "required_receipt_fields_append",
    }
    if (
        set(freeze_overlay) != expected_freeze_keys
        or freeze_overlay.get("required_contract_artifact") != "active_prediction_contract"
        or freeze_overlay.get("required_model") != prediction_contract.model
        or freeze_overlay.get("required_endpoint") != prediction_contract.endpoint
        or freeze_overlay.get("required_prompt_sha256") != contract_prompt.get("sha256")
        or freeze_overlay.get("required_result_schema_sha256") != contract_schema.get("sha256")
        or freeze_overlay.get("required_taxonomy_version") != taxonomy.get("version")
        or freeze_overlay.get("required_explicit_cache_enabled")
        is not prediction_contract.explicit_cache_enabled
        or freeze_overlay.get("required_receipt_fields_append")
        != ["endpoint", "explicit_cache_enabled"]
    ):
        raise EventEvaluationDesignError("P4.2a v1.2 prediction freeze binding drifted")

    provenance = _mapping(
        overlay.get("heldout_annotation_provenance"),
        "heldout_annotation_provenance",
    )
    if (
        provenance.get("annotation_type") != "ai_drafted_human_adjudicated"
        or _string_sequence(
            provenance.get("required_record_fields"),
            "heldout annotation provenance fields",
        )
        != _V1_2_PROVENANCE_FIELDS
        or provenance.get("drafter_id_required") is not True
        or provenance.get("adjudicator_id_required") is not True
        or provenance.get("drafter_and_adjudicator_must_differ") is not True
        or provenance.get("annotation_owner_must_equal") != "adjudicator_id"
        or provenance.get("pure_ai_annotation_policy") != "fail_closed"
        or provenance.get("accepted_metric_semantics") != "human_adjudicated_gold"
    ):
        raise EventEvaluationDesignError("P4.2a v1.2 heldout provenance contract drifted")

    completion_overlay = _mapping(
        overlay.get("owner_annotation_completion"),
        "owner_annotation_completion",
    )
    if dict(completion_overlay) != {
        "schema_version": "p4.2a-owner-completion-v1.2",
        "dev_annotation_semantics": "model_interagreement_only",
        "heldout_annotation_semantics": "human_adjudicated_gold",
        "required_manifest_fields_append": [
            "heldout_annotation_type",
            "heldout_drafter_ids",
            "heldout_adjudicator_ids",
            "heldout_human_adjudication_validated",
        ],
    }:
        raise EventEvaluationDesignError("P4.2a v1.2 owner completion semantics drifted")
    isolation = _mapping(overlay.get("isolation"), "isolation")
    if dict(isolation) != dict(base_design.document["isolation"]):
        raise EventEvaluationDesignError("P4.2a v1.2 runtime isolation drifted")

    document = copy.deepcopy(base_design.document)
    document["schema_version"] = overlay["schema_version"]
    document["owner_spec_commit"] = overlay["owner_spec_commit"]
    document["pre_registered_at"] = overlay["pre_registered_at"]
    document["artifacts"] = artifacts
    document["active_prediction_contract"] = dict(active)
    document["heldout_annotation_provenance"] = dict(provenance)
    freeze = cast(JsonObject, document["prediction_contract_freeze"])
    freeze.update(dict(freeze_overlay))
    freeze["required_receipt_fields"] = [
        *cast(list[str], freeze["required_receipt_fields"]),
        *cast(list[str], freeze_overlay["required_receipt_fields_append"]),
    ]
    completion = cast(JsonObject, document["owner_annotation_completion"])
    completion.update(dict(completion_overlay))
    completion["required_manifest_fields"] = [
        *cast(list[str], completion["required_manifest_fields"]),
        *cast(list[str], completion_overlay["required_manifest_fields_append"]),
    ]
    evaluation_overlay = _mapping(overlay.get("evaluation"), "evaluation")
    expected_report_fields = [
        "prediction_contract.endpoint",
        "prediction_contract.explicit_cache_enabled",
        "owner_completion.heldout_annotation_type",
        "owner_completion.heldout_drafter_ids",
        "owner_completion.heldout_adjudicator_ids",
        "owner_completion.heldout_human_adjudication_validated",
    ]
    if dict(evaluation_overlay) != {"required_report_fields_append": expected_report_fields}:
        raise EventEvaluationDesignError("P4.2a v1.2 report provenance contract drifted")
    evaluation = cast(JsonObject, document["evaluation"])
    evaluation["required_report_fields"] = [
        *cast(list[str], evaluation["required_report_fields"]),
        *expected_report_fields,
    ]
    heldout = cast(JsonObject, cast(JsonObject, document["splits"])["heldout_40"])
    heldout["annotation_provenance"] = dict(provenance)
    return EventEvaluationDesign(
        path=path.resolve(),
        sha256=digest,
        document=document,
        base_contract=base_design.base_contract,
        prediction_contract=prediction_contract,
    )


def _load_v1_3_event_evaluation_design(
    path: Path,
    *,
    project_root: Path,
) -> EventEvaluationDesign:
    """Load the v1.3 overlay while preserving the frozen v1.2 design."""

    try:
        payload = path.resolve().read_bytes()
    except OSError as exc:
        raise EventEvaluationDesignError("P4.2a v1.3 design is unavailable") from exc
    digest = _sha256_bytes(payload)
    if digest != EXPECTED_EVALUATION_DESIGN_V1_3_SHA256:
        raise EventEvaluationDesignError("P4.2a v1.3 design differs from its frozen SHA-256")
    try:
        loaded: object = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise EventEvaluationDesignError("P4.2a v1.3 design is invalid YAML") from exc
    if not isinstance(loaded, dict):
        raise EventEvaluationDesignError("P4.2a v1.3 design must be a mapping")
    overlay = cast(JsonObject, loaded)
    expected_top_level = {
        "schema_version",
        "owner_spec_commit",
        "pre_registered_at",
        "production_writes_allowed",
        "artifact_root",
        "extends_design",
        "active_prediction_contract",
        "artifact_overrides",
        "prediction_contract_freeze",
        "historical_comparison",
        "evaluation",
        "isolation",
    }
    if set(overlay) != expected_top_level:
        raise EventEvaluationDesignError("P4.2a v1.3 top-level contract drifted")
    if (
        overlay.get("schema_version") != EXPECTED_EVALUATION_V1_3_SCHEMA_VERSION
        or overlay.get("owner_spec_commit") != "c7510e75bd5a5fb2ad8f1d4f2409f4e0e67db359"
        or overlay.get("production_writes_allowed") is not False
        or overlay.get("artifact_root") != _ARTIFACT_ROOT.as_posix()
    ):
        raise EventEvaluationDesignError("P4.2a v1.3 identity/isolation binding drifted")

    extends = _mapping(overlay.get("extends_design"), "extends_design")
    inheritance = _mapping(extends.get("inheritance"), "extends_design.inheritance")
    if (
        extends.get("path") != "config/p4_event_evaluation_v1_2.yaml"
        or extends.get("sha256") != EXPECTED_EVALUATION_DESIGN_V1_2_SHA256
        or extends.get("schema_version") != EXPECTED_EVALUATION_V1_2_SCHEMA_VERSION
        or dict(inheritance)
        != {
            "sample_identity": "byte_frozen",
            "heldout_candidate_window": "byte_frozen",
            "heldout_selection_seed": "byte_frozen",
            "evaluation_thresholds": "byte_frozen",
            "dev_annotation_bytes": "byte_frozen",
            "heldout_annotation_provenance": "byte_frozen",
        }
    ):
        raise EventEvaluationDesignError("P4.2a v1.3 inheritance contract drifted")
    base_design = _load_v1_2_event_evaluation_design(
        project_root / "config/p4_event_evaluation_v1_2.yaml",
        project_root=project_root,
    )

    active = _mapping(
        overlay.get("active_prediction_contract"),
        "active_prediction_contract",
    )
    expected_active_keys = {
        "path",
        "sha256",
        "schema_version",
        "model",
        "endpoint",
        "prompt",
        "result_schema",
        "taxonomy_version",
        "evidence_span_match_mode",
        "explicit_cache",
    }
    active_path = _artifact_path(
        project_root,
        active.get("path"),
        label="active_prediction_contract.path",
    )
    active_sha256 = active.get("sha256")
    if (
        set(active) != expected_active_keys
        or active.get("path") != "config/p4_event_extract_eval_v1_4.yaml"
        or active.get("sha256")
        != "e6d3e7db08e2d226c850092f0f794d7194eaf1935a56cbfe267a86e1297f37fc"
        or active.get("schema_version") != "p4.2a-event-extract-eval-v1.4"
        or active.get("model") != "qwen3.6-plus"
        or active.get("endpoint") != "https://dashscope.aliyuncs.com/compatible-mode/v1"
        or active.get("evidence_span_match_mode")
        != "unicode_whitespace_elided_contiguous_substring_v1"
        or not isinstance(active_sha256, str)
        or len(active_sha256) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in active_sha256)
    ):
        raise EventEvaluationDesignError("P4.2a v1.3 active prediction contract identity drifted")
    try:
        active_bytes = active_path.read_bytes()
    except OSError as exc:
        raise EventEvaluationDesignError("active prediction contract is unavailable") from exc
    if _sha256_bytes(active_bytes) != active_sha256:
        raise EventEvaluationDesignError(
            "active prediction contract differs from its frozen SHA-256"
        )
    try:
        prediction_contract = load_event_extract_contract(
            active_path,
            project_root=project_root,
        )
    except (EventExtractContractError, OSError) as exc:
        raise EventEvaluationDesignError("active prediction contract validation failed") from exc
    active_prompt = _mapping(
        active.get("prompt"),
        "active_prediction_contract.prompt",
    )
    active_schema = _mapping(
        active.get("result_schema"),
        "active_prediction_contract.result_schema",
    )
    contract_files = _mapping(
        prediction_contract.document.get("contract_files"),
        "active prediction contract files",
    )
    contract_prompt = _mapping(
        contract_files.get("prompt"),
        "active prediction contract prompt",
    )
    contract_schema = _mapping(
        contract_files.get("schema"),
        "active prediction contract schema",
    )
    taxonomy = _mapping(
        prediction_contract.document.get("taxonomy"),
        "active prediction contract taxonomy",
    )
    explicit_cache = _mapping(
        active.get("explicit_cache"),
        "active_prediction_contract.explicit_cache",
    )
    if (
        active.get("schema_version") != prediction_contract.document.get("schema_version")
        or active.get("model") != prediction_contract.model
        or active.get("endpoint") != prediction_contract.endpoint
        or dict(active_prompt) != dict(contract_prompt)
        or dict(active_schema) != dict(contract_schema)
        or active.get("taxonomy_version") != taxonomy.get("version")
        or active.get("evidence_span_match_mode") != prediction_contract.evidence_span_match_mode
        or dict(explicit_cache) != {"enabled": False, "cache_control": None}
        or prediction_contract.explicit_cache_enabled is not False
    ):
        raise EventEvaluationDesignError("P4.2a v1.3 active prediction contract binding drifted")

    artifacts = copy.deepcopy(cast(JsonObject, base_design.document["artifacts"]))
    artifact_overrides = _mapping(
        overlay.get("artifact_overrides"),
        "artifact_overrides",
    )
    if set(artifact_overrides) != _V1_2_CREATE_ONLY_ARTIFACTS:
        raise EventEvaluationDesignError("P4.2a v1.3 create-only artifact set drifted")
    seen_paths: set[Path] = set()
    eval_root = (project_root / _ARTIFACT_ROOT).resolve()
    for name, raw_entry in artifact_overrides.items():
        entry = _mapping(raw_entry, f"artifact_overrides.{name}")
        path_value = _artifact_path(
            project_root,
            entry.get("path"),
            label=f"artifact_overrides.{name}.path",
        )
        if path_value != eval_root and not path_value.is_relative_to(eval_root):
            raise EventEvaluationDesignError(f"artifact_overrides.{name} escapes the eval root")
        if "/v1.3" not in path_value.as_posix() and "v1.3" not in path_value.name:
            raise EventEvaluationDesignError(f"artifact_overrides.{name} is not v1.3 namespaced")
        if path_value in seen_paths:
            raise EventEvaluationDesignError("P4.2a v1.3 artifact paths must be unique")
        seen_paths.add(path_value)
        create_only = (
            entry.get("create_only_reports")
            if name == "evaluation_report_directory"
            else entry.get("create_only")
        )
        if create_only is not True:
            raise EventEvaluationDesignError(f"artifact_overrides.{name} must be create-only")
        artifacts[name] = dict(entry)

    freeze_overlay = _mapping(
        overlay.get("prediction_contract_freeze"),
        "prediction_contract_freeze",
    )
    expected_freeze_keys = {
        "required_contract_artifact",
        "required_model",
        "required_endpoint",
        "required_prompt_sha256",
        "required_result_schema_sha256",
        "required_taxonomy_version",
        "required_explicit_cache_enabled",
        "required_evidence_span_match_mode",
        "required_receipt_fields_append",
    }
    if (
        set(freeze_overlay) != expected_freeze_keys
        or freeze_overlay.get("required_contract_artifact") != "active_prediction_contract"
        or freeze_overlay.get("required_model") != prediction_contract.model
        or freeze_overlay.get("required_endpoint") != prediction_contract.endpoint
        or freeze_overlay.get("required_prompt_sha256") != contract_prompt.get("sha256")
        or freeze_overlay.get("required_result_schema_sha256") != contract_schema.get("sha256")
        or freeze_overlay.get("required_taxonomy_version") != taxonomy.get("version")
        or freeze_overlay.get("required_explicit_cache_enabled")
        is not prediction_contract.explicit_cache_enabled
        or freeze_overlay.get("required_evidence_span_match_mode")
        != prediction_contract.evidence_span_match_mode
        or freeze_overlay.get("required_receipt_fields_append") != ["evidence_span_match_mode"]
    ):
        raise EventEvaluationDesignError("P4.2a v1.3 prediction freeze binding drifted")

    historical = _mapping(
        overlay.get("historical_comparison"),
        "historical_comparison",
    )
    expected_historical: JsonObject = {
        "v1_3_actual": {
            "evidence_source": "immutable_local_artifacts",
            "success_count": 53,
            "failure_count": 7,
            "failure_ids": [250, 258, 287, 304, 306, 336, 358],
            "predictions_sha256": (
                "b882a5cdad7025f8499eae75b617e189174ef866ab949749dd58c4a193229134"
            ),
            "manifest_sha256": ("4eb7f05e8196ac5dd4d646bb1b8be7a56b93123e4866b0ba4a79121ffb370262"),
            "report_sha256": ("781f6b7f30d97b9a43978feccec6891fa9b959aca0572a53784527a7e0e926e9"),
            "blocker_sha256": ("6efed45a618a9892fdcd321dd43db2232b3ddf1a4eb2ada7d371cb0da9a3dc3d"),
        },
        "whitespace_normalized_counterfactual": {
            "evidence_source": "independent_reviewer_external_reproduction",
            "locally_recomputed_from_persisted_raw_payload": False,
            "success_count": 58,
            "failure_count": 2,
            "normalization_recovered_ids": [250, 258, 287, 306, 358],
            "true_synthesis_failure_ids": [304, 336],
        },
        "v1_4_required": {
            "success_count": 60,
            "failure_count": 0,
            "materiality_positive_agreement_minimum": 0.80,
            "symbol_exact_set_agreement_minimum": 0.95,
        },
        "symbol_adjudication": {
            "ai_label_defect_ids": [44],
            "model_over_attribution_ids": [75, 210, 232, 393],
            "frozen_dev_labels_must_remain_unchanged": True,
            "raw_gate_uses_frozen_labels": True,
            "adjusted_diagnostic_excludes_label_defects": True,
        },
    }
    if dict(historical) != expected_historical:
        raise EventEvaluationDesignError("P4.2a v1.3 historical comparison contract drifted")
    _verify_v1_3_historical_artifacts(
        project_root,
        _mapping(historical.get("v1_3_actual"), "historical_comparison.v1_3_actual"),
    )

    evaluation_overlay = _mapping(overlay.get("evaluation"), "evaluation")
    expected_report_fields = [
        "prediction_contract.evidence_span_match_mode",
        "evidence_validation.v1_3_actual",
        "evidence_validation.whitespace_normalized_counterfactual",
        "evidence_validation.v1_4_actual",
        "evidence_validation.v1_4_legacy_exact_shadow",
        "symbol_diagnostics.ai_label_defect_ids",
        "symbol_diagnostics.adjusted_exact_set",
    ]
    if dict(evaluation_overlay) != {"required_report_fields_append": expected_report_fields}:
        raise EventEvaluationDesignError("P4.2a v1.3 report comparison contract drifted")
    isolation = _mapping(overlay.get("isolation"), "isolation")
    if dict(isolation) != dict(base_design.document["isolation"]):
        raise EventEvaluationDesignError("P4.2a v1.3 runtime isolation drifted")

    document = copy.deepcopy(base_design.document)
    document["schema_version"] = overlay["schema_version"]
    document["owner_spec_commit"] = overlay["owner_spec_commit"]
    document["pre_registered_at"] = overlay["pre_registered_at"]
    document["artifacts"] = artifacts
    document["active_prediction_contract"] = dict(active)
    document["historical_comparison"] = copy.deepcopy(dict(historical))
    freeze = cast(JsonObject, document["prediction_contract_freeze"])
    freeze.update(dict(freeze_overlay))
    freeze["required_receipt_fields"] = [
        *cast(list[str], freeze["required_receipt_fields"]),
        *cast(list[str], freeze_overlay["required_receipt_fields_append"]),
    ]
    evaluation = cast(JsonObject, document["evaluation"])
    evaluation["required_report_fields"] = [
        *cast(list[str], evaluation["required_report_fields"]),
        *expected_report_fields,
    ]
    return EventEvaluationDesign(
        path=path.resolve(),
        sha256=digest,
        document=document,
        base_contract=base_design.base_contract,
        prediction_contract=prediction_contract,
    )


def _load_v1_4_event_evaluation_design(
    path: Path,
    *,
    project_root: Path,
) -> EventEvaluationDesign:
    """Load the v1.4 overlay and bind the immutable failed v1.4-r1 evidence."""

    try:
        payload = path.resolve().read_bytes()
    except OSError as exc:
        raise EventEvaluationDesignError("P4.2a v1.4 design is unavailable") from exc
    digest = _sha256_bytes(payload)
    if digest != EXPECTED_EVALUATION_DESIGN_V1_4_SHA256:
        raise EventEvaluationDesignError("P4.2a v1.4 design differs from its frozen SHA-256")
    try:
        loaded: object = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise EventEvaluationDesignError("P4.2a v1.4 design is invalid YAML") from exc
    if not isinstance(loaded, dict):
        raise EventEvaluationDesignError("P4.2a v1.4 design must be a mapping")
    overlay = cast(JsonObject, loaded)
    if set(overlay) != {
        "schema_version",
        "owner_spec_commit",
        "pre_registered_at",
        "production_writes_allowed",
        "artifact_root",
        "extends_design",
        "active_prediction_contract",
        "artifact_overrides",
        "prediction_contract_freeze",
        "derived_after_failed_round",
        "evaluation",
        "isolation",
    }:
        raise EventEvaluationDesignError("P4.2a v1.4 top-level contract drifted")
    if (
        overlay.get("schema_version") != EXPECTED_EVALUATION_V1_4_SCHEMA_VERSION
        or overlay.get("owner_spec_commit") != "c7510e75bd5a5fb2ad8f1d4f2409f4e0e67db359"
        or overlay.get("production_writes_allowed") is not False
        or overlay.get("artifact_root") != _ARTIFACT_ROOT.as_posix()
    ):
        raise EventEvaluationDesignError("P4.2a v1.4 identity/isolation binding drifted")

    extends = _mapping(overlay.get("extends_design"), "extends_design")
    inheritance = _mapping(extends.get("inheritance"), "extends_design.inheritance")
    if (
        extends.get("path") != "config/p4_event_evaluation_v1_3.yaml"
        or extends.get("sha256") != EXPECTED_EVALUATION_DESIGN_V1_3_SHA256
        or extends.get("schema_version") != EXPECTED_EVALUATION_V1_3_SCHEMA_VERSION
        or dict(inheritance)
        != {
            "sample_identity": "byte_frozen",
            "heldout_candidate_window": "byte_frozen",
            "heldout_selection_seed": "byte_frozen",
            "evaluation_thresholds": "byte_frozen",
            "dev_annotation_bytes": "byte_frozen",
            "heldout_annotation_provenance": "byte_frozen",
            "historical_v1_3_evidence": "byte_frozen",
        }
    ):
        raise EventEvaluationDesignError("P4.2a v1.4 inheritance contract drifted")
    base_design = _load_v1_3_event_evaluation_design(
        project_root / "config/p4_event_evaluation_v1_3.yaml",
        project_root=project_root,
    )

    active = _mapping(
        overlay.get("active_prediction_contract"),
        "active_prediction_contract",
    )
    active_path = _artifact_path(
        project_root,
        active.get("path"),
        label="active_prediction_contract.path",
    )
    active_sha256 = active.get("sha256")
    if (
        set(active)
        != {
            "path",
            "sha256",
            "schema_version",
            "model",
            "endpoint",
            "prompt",
            "result_schema",
            "taxonomy_version",
            "evidence_span_match_mode",
            "explicit_cache",
        }
        or active.get("path") != "config/p4_event_extract_eval_v1_5.yaml"
        or active.get("sha256")
        != "a07f9f37e0877bd06ce3dc9e8a0e03c51bbb92fdc3ba6738b6932d7679aca560"
        or active.get("schema_version") != "p4.2a-event-extract-eval-v1.5"
        or active.get("model") != "qwen3.6-plus"
        or active.get("endpoint") != "https://dashscope.aliyuncs.com/compatible-mode/v1"
        or active.get("evidence_span_match_mode")
        != "unicode_whitespace_elided_contiguous_substring_v1"
        or not isinstance(active_sha256, str)
        or len(active_sha256) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in active_sha256)
    ):
        raise EventEvaluationDesignError("P4.2a v1.4 active prediction contract identity drifted")
    try:
        active_bytes = active_path.read_bytes()
    except OSError as exc:
        raise EventEvaluationDesignError("active prediction contract is unavailable") from exc
    if _sha256_bytes(active_bytes) != active_sha256:
        raise EventEvaluationDesignError(
            "active prediction contract differs from its frozen SHA-256"
        )
    try:
        prediction_contract = load_event_extract_contract(
            active_path,
            project_root=project_root,
        )
    except (EventExtractContractError, OSError) as exc:
        raise EventEvaluationDesignError("active prediction contract validation failed") from exc
    active_prompt = _mapping(active.get("prompt"), "active_prediction_contract.prompt")
    active_schema = _mapping(
        active.get("result_schema"),
        "active_prediction_contract.result_schema",
    )
    contract_files = _mapping(
        prediction_contract.document.get("contract_files"),
        "active prediction contract files",
    )
    contract_prompt = _mapping(
        contract_files.get("prompt"),
        "active prediction contract prompt",
    )
    contract_schema = _mapping(
        contract_files.get("schema"),
        "active prediction contract schema",
    )
    taxonomy = _mapping(
        prediction_contract.document.get("taxonomy"),
        "active prediction contract taxonomy",
    )
    explicit_cache = _mapping(
        active.get("explicit_cache"),
        "active_prediction_contract.explicit_cache",
    )
    if (
        active.get("schema_version") != prediction_contract.document.get("schema_version")
        or active.get("model") != prediction_contract.model
        or active.get("endpoint") != prediction_contract.endpoint
        or dict(active_prompt) != dict(contract_prompt)
        or dict(active_schema) != dict(contract_schema)
        or active.get("taxonomy_version") != taxonomy.get("version")
        or active.get("evidence_span_match_mode") != prediction_contract.evidence_span_match_mode
        or dict(explicit_cache) != {"enabled": False, "cache_control": None}
        or prediction_contract.explicit_cache_enabled is not False
    ):
        raise EventEvaluationDesignError("P4.2a v1.4 active prediction contract binding drifted")

    artifacts = copy.deepcopy(cast(JsonObject, base_design.document["artifacts"]))
    artifact_overrides = _mapping(
        overlay.get("artifact_overrides"),
        "artifact_overrides",
    )
    if set(artifact_overrides) != _V1_2_CREATE_ONLY_ARTIFACTS:
        raise EventEvaluationDesignError("P4.2a v1.4 create-only artifact set drifted")
    seen_paths: set[Path] = set()
    eval_root = (project_root / _ARTIFACT_ROOT).resolve()
    for name, raw_entry in artifact_overrides.items():
        entry = _mapping(raw_entry, f"artifact_overrides.{name}")
        path_value = _artifact_path(
            project_root,
            entry.get("path"),
            label=f"artifact_overrides.{name}.path",
        )
        if path_value != eval_root and not path_value.is_relative_to(eval_root):
            raise EventEvaluationDesignError(f"artifact_overrides.{name} escapes the eval root")
        if "/v1.4" not in path_value.as_posix() and "v1.4" not in path_value.name:
            raise EventEvaluationDesignError(f"artifact_overrides.{name} is not v1.4 namespaced")
        if path_value in seen_paths:
            raise EventEvaluationDesignError("P4.2a v1.4 artifact paths must be unique")
        seen_paths.add(path_value)
        create_only = (
            entry.get("create_only_reports")
            if name == "evaluation_report_directory"
            else entry.get("create_only")
        )
        if create_only is not True:
            raise EventEvaluationDesignError(f"artifact_overrides.{name} must be create-only")
        artifacts[name] = dict(entry)

    freeze_overlay = _mapping(
        overlay.get("prediction_contract_freeze"),
        "prediction_contract_freeze",
    )
    if (
        set(freeze_overlay)
        != {
            "required_contract_artifact",
            "required_model",
            "required_endpoint",
            "required_prompt_sha256",
            "required_result_schema_sha256",
            "required_taxonomy_version",
            "required_explicit_cache_enabled",
            "required_evidence_span_match_mode",
        }
        or freeze_overlay.get("required_contract_artifact") != "active_prediction_contract"
        or freeze_overlay.get("required_model") != prediction_contract.model
        or freeze_overlay.get("required_endpoint") != prediction_contract.endpoint
        or freeze_overlay.get("required_prompt_sha256") != contract_prompt.get("sha256")
        or freeze_overlay.get("required_result_schema_sha256") != contract_schema.get("sha256")
        or freeze_overlay.get("required_taxonomy_version") != taxonomy.get("version")
        or freeze_overlay.get("required_explicit_cache_enabled")
        is not prediction_contract.explicit_cache_enabled
        or freeze_overlay.get("required_evidence_span_match_mode")
        != prediction_contract.evidence_span_match_mode
    ):
        raise EventEvaluationDesignError("P4.2a v1.4 prediction freeze binding drifted")

    derived = _mapping(
        overlay.get("derived_after_failed_round"),
        "derived_after_failed_round",
    )
    expected_derived: JsonObject = {
        "round_id": "v1.4-r1",
        "historical_round_immutable": True,
        "formal_dev_round_valid": False,
        "heldout_accessed": False,
        "active_contract": {
            "path": "config/p4_event_extract_eval_v1_4.yaml",
            "sha256": "e6d3e7db08e2d226c850092f0f794d7194eaf1935a56cbfe267a86e1297f37fc",
        },
        "evaluation_design": {
            "path": "config/p4_event_evaluation_v1_3.yaml",
            "sha256": "79b58de72d797ee9a9e93ec0f37d5f1c1b0b0d86ca49db1736ecdb5cdc314aac",
        },
        "prompt": {
            "path": "config/prompts/p4_news_event_extract_v1_3.txt",
            "sha256": "6110b39381789d9c2d2f6442e517c2b89a8a474a8f97c6a037a32f26f629aeee",
        },
        "artifacts": {
            "predictions": {
                "path": ("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.4-r1.predictions.jsonl"),
                "sha256": ("5aaa4deded34dc858bc7e90b4db5dc2b2cf656f4d4dd673e5a9980d3152257b2"),
            },
            "manifest": {
                "path": ("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.4-r1.manifest.json"),
                "sha256": ("fa56b4f167530c7da1d0887f0163b13aa69b0a73de8e48a75cc6335cbbb1904a"),
            },
            "report": {
                "path": ("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.4-r1.report.json"),
                "sha256": ("5cf0722fb8851122720ea59c53cb355be9095f1b2a4d1658b827cf48a2dbf969"),
            },
            "blocker": {
                "path": ("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.4-r1.blocker.json"),
                "sha256": ("3d0038515e208bca37e096b280ec411da2086addd8696d2ee6e8afa36fad00f9"),
            },
        },
        "extraction": {
            "expected_count": 60,
            "success_count": 54,
            "failure_count": 6,
            "failure_ids": [253, 258, 280, 304, 336, 340],
            "failures_by_reason": {
                "post_validation_failed": 5,
                "schema_validation_failed": 1,
            },
            "post_validation": {
                "field": "evidence_span",
                "constraint": "whitespace_normalized_contiguous_substring",
                "failure_ids": [253, 258, 304, 336, 340],
                "root_cause": "unadjudicated_from_safe_artifacts",
            },
            "schema_validation": {
                "failure_ids": [280],
                "root_cause": "unproven",
            },
            "legacy_exact_shadow": {
                "comparable_count": 54,
                "match_count": 45,
                "mismatch_count": 9,
                "mismatch_ids": [
                    250,
                    255,
                    260,
                    274,
                    287,
                    306,
                    352,
                    358,
                    360,
                ],
            },
        },
        "metrics": {
            "materiality_positive_agreement": 0.8461538461538461,
            "symbol_exact_set": {
                "matches": 49,
                "denominator": 54,
                "agreement": 0.9074074074074074,
                "mismatch_ids": [28, 44, 67, 71, 96],
            },
            "symbol_adjudication": {
                "ai_label_defect_ids": [44],
                "current_model_under_attribution_ids": [28, 67, 71, 96],
                "historical_v1_3_over_attribution_ids_now_correct": [
                    75,
                    210,
                    232,
                    393,
                ],
            },
        },
        "report_integrity_disclosure": {
            "immutable_report_preserved": True,
            "flash_baseline_changed_dimensions_omitted": [
                "evidence_span_match_mode",
                "validation_contract",
            ],
            "baseline_comparable_count": 59,
            "candidate_comparable_count": 54,
            "causal_reading_forbidden": True,
        },
    }
    if dict(derived) != expected_derived:
        raise EventEvaluationDesignError("P4.2a v1.4 failed-round derivation drifted")
    _verify_v1_4_historical_artifacts(project_root, derived)

    evaluation_overlay = _mapping(overlay.get("evaluation"), "evaluation")
    expected_report_fields = [
        "evidence_validation.v1_4_r1_actual",
        "evidence_validation.v1_5_actual",
        "evidence_validation.v1_5_legacy_exact_shadow",
        "symbol_diagnostics.v1_4_r1_actual",
    ]
    if dict(evaluation_overlay) != {"required_report_fields_append": expected_report_fields}:
        raise EventEvaluationDesignError("P4.2a v1.4 report comparison contract drifted")
    isolation = _mapping(overlay.get("isolation"), "isolation")
    if dict(isolation) != dict(base_design.document["isolation"]):
        raise EventEvaluationDesignError("P4.2a v1.4 runtime isolation drifted")

    document = copy.deepcopy(base_design.document)
    document["schema_version"] = overlay["schema_version"]
    document["owner_spec_commit"] = overlay["owner_spec_commit"]
    document["pre_registered_at"] = overlay["pre_registered_at"]
    document["artifacts"] = artifacts
    document["active_prediction_contract"] = dict(active)
    document["derived_after_failed_round"] = copy.deepcopy(dict(derived))
    historical = cast(JsonObject, document["historical_comparison"])
    historical["v1_4_r1_actual"] = copy.deepcopy(dict(derived))
    freeze = cast(JsonObject, document["prediction_contract_freeze"])
    freeze.update(dict(freeze_overlay))
    evaluation = cast(JsonObject, document["evaluation"])
    evaluation["required_report_fields"] = [
        *cast(list[str], evaluation["required_report_fields"]),
        *expected_report_fields,
    ]
    return EventEvaluationDesign(
        path=path.resolve(),
        sha256=digest,
        document=document,
        base_contract=base_design.base_contract,
        prediction_contract=prediction_contract,
    )


def _load_v1_5_event_evaluation_design(
    path: Path,
    *,
    project_root: Path,
) -> EventEvaluationDesign:
    """Load the v1.5 overlay and bind candidate-selection v1.6 exactly."""

    try:
        payload = path.resolve().read_bytes()
    except OSError as exc:
        raise EventEvaluationDesignError("P4.2a v1.5 design is unavailable") from exc
    digest = _sha256_bytes(payload)
    if digest != EXPECTED_EVALUATION_DESIGN_V1_5_SHA256:
        raise EventEvaluationDesignError("P4.2a v1.5 design differs from its frozen SHA-256")
    try:
        loaded: object = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise EventEvaluationDesignError("P4.2a v1.5 design is invalid YAML") from exc
    if not isinstance(loaded, dict):
        raise EventEvaluationDesignError("P4.2a v1.5 design must be a mapping")
    overlay = cast(JsonObject, loaded)
    if set(overlay) != {
        "schema_version",
        "owner_spec_commit",
        "pre_registered_at",
        "production_writes_allowed",
        "artifact_root",
        "extends_design",
        "active_prediction_contract",
        "artifact_overrides",
        "prediction_contract_freeze",
        "derived_after_failed_round",
        "evaluation",
        "isolation",
    }:
        raise EventEvaluationDesignError("P4.2a v1.5 top-level contract drifted")
    if (
        overlay.get("schema_version") != EXPECTED_EVALUATION_V1_5_SCHEMA_VERSION
        or overlay.get("owner_spec_commit") != "c7510e75bd5a5fb2ad8f1d4f2409f4e0e67db359"
        or overlay.get("production_writes_allowed") is not False
        or overlay.get("artifact_root") != _ARTIFACT_ROOT.as_posix()
    ):
        raise EventEvaluationDesignError("P4.2a v1.5 identity/isolation binding drifted")

    extends = _mapping(overlay.get("extends_design"), "extends_design")
    inheritance = _mapping(extends.get("inheritance"), "extends_design.inheritance")
    if (
        extends.get("path") != "config/p4_event_evaluation_v1_4.yaml"
        or extends.get("sha256") != EXPECTED_EVALUATION_DESIGN_V1_4_SHA256
        or extends.get("schema_version") != EXPECTED_EVALUATION_V1_4_SCHEMA_VERSION
        or dict(inheritance)
        != {
            "sample_identity": "byte_frozen",
            "heldout_candidate_window": "byte_frozen",
            "heldout_selection_seed": "byte_frozen",
            "evaluation_thresholds": "byte_frozen",
            "dev_annotation_bytes": "byte_frozen",
            "heldout_annotation_provenance": "byte_frozen",
            "historical_v1_3_evidence": "byte_frozen",
            "historical_v1_4_evidence": "byte_frozen",
        }
    ):
        raise EventEvaluationDesignError("P4.2a v1.5 inheritance contract drifted")
    base_design = _load_v1_4_event_evaluation_design(
        project_root / "config/p4_event_evaluation_v1_4.yaml",
        project_root=project_root,
    )

    active = _mapping(
        overlay.get("active_prediction_contract"),
        "active_prediction_contract",
    )
    expected_active: JsonObject = {
        "path": "config/p4_event_extract_eval_v1_6.yaml",
        "sha256": "4e88990d2ee7671db316794aabd0a476f798b5e542f00bbb8ffbd3f7fd423269",
        "schema_version": "p4.2a-event-extract-eval-v1.6",
        "model": "qwen3.6-plus",
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "prompt": {
            "path": "config/prompts/p4_news_event_extract_v1_5.txt",
            "sha256": ("4b44ed5efe281b68664b415865b758b75b30ace6eda2617952de66a87596c204"),
        },
        "model_result_schema": {
            "path": "config/schemas/p4_news_event_candidate_v1.schema.json",
            "sha256": ("c106cd15bd974de19ecc01d6e99e8f39c39fbf14df3a3b4dc74ee9b08ff6dd66"),
        },
        "materialized_result_schema": {
            "path": "config/schemas/p4_news_event_v1.schema.json",
            "sha256": ("0ac68654ce23ecd4e537d849d695e092c76dcb9de0fb03793e65ae62b181947f"),
        },
        "taxonomy_version": "p4-news-event-taxonomy-v1",
        "input_representation": {
            "name": "ordered_evidence_candidates_v1",
            "original_text_in_model_message": False,
            "declared_input_identity": "legacy_eight_field_user_json_v1",
            "active_model_input_identity": ("canonical_ordered_evidence_candidates_user_json_v1"),
            "dual_hash_identity_required": True,
        },
        "candidate_materialization": {
            "candidate_field": "evidence_candidate_id",
            "final_field": "evidence_span",
            "algorithm_version": ("ordered-raw-partition-unicode-whitespace-display-v1"),
            "materialization": "exact_raw_slice_from_registered_start_end",
            "invalid_candidate_policy": "fail_closed_without_repair",
            "matcher_revalidation_required": True,
        },
        "evidence_span_match_mode": ("unicode_whitespace_elided_contiguous_substring_v1"),
        "explicit_cache": {"enabled": False, "cache_control": None},
    }
    if dict(active) != expected_active:
        raise EventEvaluationDesignError("P4.2a v1.5 active prediction contract identity drifted")
    active_path = _artifact_path(
        project_root,
        active.get("path"),
        label="active_prediction_contract.path",
    )
    try:
        active_bytes = active_path.read_bytes()
    except OSError as exc:
        raise EventEvaluationDesignError("active prediction contract is unavailable") from exc
    if _sha256_bytes(active_bytes) != active.get("sha256"):
        raise EventEvaluationDesignError(
            "active prediction contract differs from its frozen SHA-256"
        )
    try:
        prediction_contract = load_event_extract_contract(
            active_path,
            project_root=project_root,
        )
    except (EventExtractContractError, OSError) as exc:
        raise EventEvaluationDesignError("active prediction contract validation failed") from exc
    contract_files = _mapping(
        prediction_contract.document.get("contract_files"),
        "active prediction contract files",
    )
    contract_prompt = _mapping(
        contract_files.get("prompt"),
        "active prediction contract prompt",
    )
    contract_schema = _mapping(
        contract_files.get("schema"),
        "active prediction contract model schema",
    )
    contract_materialized_schema = _mapping(
        contract_files.get("materialized_schema"),
        "active prediction contract materialized schema",
    )
    taxonomy = _mapping(
        prediction_contract.document.get("taxonomy"),
        "active prediction contract taxonomy",
    )
    input_contract = _mapping(
        prediction_contract.document.get("input"),
        "active prediction contract input",
    )
    output_contract = _mapping(
        prediction_contract.document.get("output"),
        "active prediction contract output",
    )
    if (
        prediction_contract.document.get("schema_version") != active.get("schema_version")
        or prediction_contract.model != active.get("model")
        or prediction_contract.endpoint != active.get("endpoint")
        or dict(contract_prompt) != active.get("prompt")
        or dict(contract_schema) != active.get("model_result_schema")
        or dict(contract_materialized_schema) != active.get("materialized_result_schema")
        or taxonomy.get("version") != active.get("taxonomy_version")
        or input_contract.get("model_message_representation")
        != _mapping(
            active.get("input_representation"),
            "active input representation",
        ).get("name")
        or input_contract.get("original_text_in_model_message") is not False
        or output_contract.get("model_field")
        != _mapping(
            active.get("candidate_materialization"),
            "active candidate materialization",
        ).get("candidate_field")
        or output_contract.get("final_prediction_field")
        != _mapping(
            active.get("candidate_materialization"),
            "active candidate materialization",
        ).get("final_field")
        or output_contract.get("materialization")
        != _mapping(
            active.get("candidate_materialization"),
            "active candidate materialization",
        ).get("materialization")
        or prediction_contract.evidence_candidate_selection is not True
        or prediction_contract.materialized_schema is None
        or prediction_contract.evidence_span_match_mode != active.get("evidence_span_match_mode")
        or prediction_contract.explicit_cache_enabled is not False
    ):
        raise EventEvaluationDesignError("P4.2a v1.5 active prediction contract binding drifted")

    artifacts = copy.deepcopy(cast(JsonObject, base_design.document["artifacts"]))
    artifact_overrides = _mapping(
        overlay.get("artifact_overrides"),
        "artifact_overrides",
    )
    if set(artifact_overrides) != _V1_2_CREATE_ONLY_ARTIFACTS:
        raise EventEvaluationDesignError("P4.2a v1.5 create-only artifact set drifted")
    seen_paths: set[Path] = set()
    eval_root = (project_root / _ARTIFACT_ROOT).resolve()
    for name, raw_entry in artifact_overrides.items():
        entry = _mapping(raw_entry, f"artifact_overrides.{name}")
        path_value = _artifact_path(
            project_root,
            entry.get("path"),
            label=f"artifact_overrides.{name}.path",
        )
        if path_value != eval_root and not path_value.is_relative_to(eval_root):
            raise EventEvaluationDesignError(f"artifact_overrides.{name} escapes the eval root")
        if "/v1.5" not in path_value.as_posix() and "v1.5" not in path_value.name:
            raise EventEvaluationDesignError(f"artifact_overrides.{name} is not v1.5 namespaced")
        if path_value in seen_paths:
            raise EventEvaluationDesignError("P4.2a v1.5 artifact paths must be unique")
        seen_paths.add(path_value)
        create_only = (
            entry.get("create_only_reports")
            if name == "evaluation_report_directory"
            else entry.get("create_only")
        )
        if create_only is not True:
            raise EventEvaluationDesignError(f"artifact_overrides.{name} must be create-only")
        artifacts[name] = dict(entry)

    freeze_overlay = _mapping(
        overlay.get("prediction_contract_freeze"),
        "prediction_contract_freeze",
    )
    expected_freeze: JsonObject = {
        "required_contract_artifact": "active_prediction_contract",
        "required_model": "qwen3.6-plus",
        "required_endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "required_prompt_sha256": (
            "4b44ed5efe281b68664b415865b758b75b30ace6eda2617952de66a87596c204"
        ),
        "required_result_schema_sha256": (
            "0ac68654ce23ecd4e537d849d695e092c76dcb9de0fb03793e65ae62b181947f"
        ),
        "required_model_result_schema_sha256": (
            "c106cd15bd974de19ecc01d6e99e8f39c39fbf14df3a3b4dc74ee9b08ff6dd66"
        ),
        "required_taxonomy_version": "p4-news-event-taxonomy-v1",
        "required_explicit_cache_enabled": False,
        "required_evidence_span_match_mode": ("unicode_whitespace_elided_contiguous_substring_v1"),
        "required_input_representation": "ordered_evidence_candidates_v1",
        "required_dual_hash_identity": True,
    }
    if dict(freeze_overlay) != expected_freeze:
        raise EventEvaluationDesignError("P4.2a v1.5 prediction freeze binding drifted")

    derived = _mapping(
        overlay.get("derived_after_failed_round"),
        "derived_after_failed_round",
    )
    expected_artifacts: JsonObject = {
        "predictions": {
            "path": ("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.5-r1.predictions.jsonl"),
            "sha256": ("e9642568d50e0c4d9c1fe7726a117cb90269ed61ce116cd8665a989eb24dc297"),
        },
        "manifest": {
            "path": ("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.5-r1.manifest.json"),
            "sha256": ("211212ba610a876dd107986c06e12501db08d7f03e9c87a1f5007d306f47f51e"),
        },
        "report": {
            "path": ("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.5-r1.report.json"),
            "sha256": ("250545184031919e798a69dfd00dae9b8a06eb1b4168d628c0e5c48c00b7f845"),
        },
        "blocker": {
            "path": ("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.5-r1.blocker.json"),
            "sha256": ("856cdd30972b4b287b2c944930c06531b7fd14b2897fa230c71faa9549bfc4a0"),
        },
    }
    extraction = _mapping(
        derived.get("extraction"),
        "derived_after_failed_round.extraction",
    )
    metrics = _mapping(
        derived.get("metrics"),
        "derived_after_failed_round.metrics",
    )
    disclosure = _mapping(
        derived.get("report_integrity_disclosure"),
        "derived_after_failed_round.report_integrity_disclosure",
    )
    if (
        set(derived)
        != {
            "round_id",
            "historical_round_immutable",
            "formal_dev_round_valid",
            "heldout_accessed",
            "active_contract",
            "evaluation_design",
            "prompt",
            "artifacts",
            "extraction",
            "metrics",
            "report_integrity_disclosure",
        }
        or derived.get("round_id") != "v1.5-r1"
        or derived.get("historical_round_immutable") is not True
        or derived.get("formal_dev_round_valid") is not False
        or derived.get("heldout_accessed") is not False
        or derived.get("active_contract")
        != {
            "path": "config/p4_event_extract_eval_v1_5.yaml",
            "sha256": ("a07f9f37e0877bd06ce3dc9e8a0e03c51bbb92fdc3ba6738b6932d7679aca560"),
        }
        or derived.get("evaluation_design")
        != {
            "path": "config/p4_event_evaluation_v1_4.yaml",
            "sha256": EXPECTED_EVALUATION_DESIGN_V1_4_SHA256,
        }
        or derived.get("prompt")
        != {
            "path": "config/prompts/p4_news_event_extract_v1_4.txt",
            "sha256": ("ff42e6905e009e8a7a3a0ae7b7fedce043cbf73f55f3551cf76bbfdcfef33f2b"),
        }
        or derived.get("artifacts") != expected_artifacts
        or dict(extraction)
        != {
            "expected_count": 60,
            "success_count": 50,
            "failure_count": 10,
            "failure_ids": [9, 250, 272, 280, 303, 304, 306, 336, 340, 360],
            "failures_by_reason": {
                "post_validation_failed": 9,
                "schema_validation_failed": 1,
            },
            "post_validation": {
                "field": "evidence_span",
                "constraint": "whitespace_normalized_contiguous_substring",
                "failure_ids": [9, 250, 272, 303, 304, 306, 336, 340, 360],
                "root_cause": "unadjudicated_from_safe_artifacts",
            },
            "schema_validation": {
                "failure_ids": [280],
                "root_cause": "unproven",
            },
            "gold_intersection_failure_ids": [272, 304, 306, 336],
        }
        or dict(metrics)
        != {
            "materiality_positive_agreement": {
                "matches": 11,
                "denominator": 12,
                "agreement": 0.9166666666666666,
                "formal_gate_usable": False,
            },
            "symbol_exact_set": {
                "matches": 48,
                "denominator": 50,
                "agreement": 0.96,
                "mismatch_ids": [44, 393],
                "formal_gate_usable": False,
            },
            "symbol_adjudication": {
                "ai_label_defect_ids": [44],
                "current_model_over_attribution_ids": [393],
                "frozen_dev_labels_remain_unchanged": True,
                "symbol_rule_relaxation_allowed": False,
            },
        }
        or dict(disclosure)
        != {
            "immutable_report_preserved": True,
            "changed_dimensions": [
                "model",
                "endpoint",
                "prompt_version",
                "evidence_span_match_mode",
                "validation_contract",
            ],
            "baseline_comparable_count": 59,
            "candidate_comparable_count": 50,
            "causal_reading_forbidden": True,
        }
    ):
        raise EventEvaluationDesignError("P4.2a v1.5 failed-round derivation drifted")
    _verify_v1_5_historical_artifacts(project_root, derived)

    evaluation_overlay = _mapping(overlay.get("evaluation"), "evaluation")
    expected_report_fields = [
        "prediction_contract.input_representation",
        "prediction_contract.model_result_schema",
        "prediction_contract.materialized_result_schema",
        "prediction_contract.candidate_materialization",
        "input_identity.declared_frozen_input_sha256",
        "input_identity.active_model_input_sha256",
        "input_identity.dual_hash_identity",
        "evidence_validation.v1_5_r1_actual",
        "evidence_validation.v1_6_actual",
        "symbol_diagnostics.v1_5_r1_actual",
        "comparison_disclosure.changed_dimensions",
        "comparison_disclosure.causal_reading_forbidden",
    ]
    if dict(evaluation_overlay) != {"required_report_fields_append": expected_report_fields}:
        raise EventEvaluationDesignError("P4.2a v1.5 report comparison contract drifted")
    isolation = _mapping(overlay.get("isolation"), "isolation")
    if dict(isolation) != dict(base_design.document["isolation"]):
        raise EventEvaluationDesignError("P4.2a v1.5 runtime isolation drifted")

    document = copy.deepcopy(base_design.document)
    document["schema_version"] = overlay["schema_version"]
    document["owner_spec_commit"] = overlay["owner_spec_commit"]
    document["pre_registered_at"] = overlay["pre_registered_at"]
    document["artifacts"] = artifacts
    document["active_prediction_contract"] = dict(active)
    document["derived_after_failed_round"] = copy.deepcopy(dict(derived))
    historical = cast(JsonObject, document["historical_comparison"])
    historical["v1_5_r1_actual"] = copy.deepcopy(dict(derived))
    freeze = cast(JsonObject, document["prediction_contract_freeze"])
    freeze.update(dict(freeze_overlay))
    evaluation = cast(JsonObject, document["evaluation"])
    evaluation["required_report_fields"] = [
        *cast(list[str], evaluation["required_report_fields"]),
        *expected_report_fields,
    ]
    return EventEvaluationDesign(
        path=path.resolve(),
        sha256=digest,
        document=document,
        base_contract=base_design.base_contract,
        prediction_contract=prediction_contract,
    )


def _load_v1_6_event_evaluation_design(
    path: Path,
    *,
    project_root: Path,
) -> EventEvaluationDesign:
    """Load the single-round qwen3.7-flash selection overlay fail closed."""

    try:
        payload = path.resolve().read_bytes()
    except OSError as exc:
        raise EventEvaluationDesignError("P4.2a v1.6 design is unavailable") from exc
    digest = _sha256_bytes(payload)
    if digest != EXPECTED_EVALUATION_DESIGN_V1_6_SHA256:
        raise EventEvaluationDesignError("P4.2a v1.6 design differs from its frozen SHA-256")
    try:
        loaded: object = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise EventEvaluationDesignError("P4.2a v1.6 design is invalid YAML") from exc
    if not isinstance(loaded, dict):
        raise EventEvaluationDesignError("P4.2a v1.6 design must be a mapping")
    overlay = cast(JsonObject, loaded)
    if set(overlay) != {
        "schema_version",
        "owner_spec_commit",
        "pre_registered_at",
        "production_writes_allowed",
        "artifact_root",
        "extends_design",
        "active_prediction_contract",
        "artifact_overrides",
        "prediction_contract_freeze",
        "model_selection",
        "evaluation",
        "isolation",
    }:
        raise EventEvaluationDesignError("P4.2a v1.6 top-level contract drifted")
    if (
        overlay.get("schema_version") != EXPECTED_EVALUATION_V1_6_SCHEMA_VERSION
        or overlay.get("owner_spec_commit") != "9c60ba7b5c2c912a77fdd99785302d76e4e3d7ca"
        or overlay.get("pre_registered_at") != "2026-08-04T13:32:53Z"
        or overlay.get("production_writes_allowed") is not False
        or overlay.get("artifact_root") != _ARTIFACT_ROOT.as_posix()
    ):
        raise EventEvaluationDesignError("P4.2a v1.6 identity/isolation binding drifted")

    extends = _mapping(overlay.get("extends_design"), "extends_design")
    inheritance = _mapping(extends.get("inheritance"), "extends_design.inheritance")
    if (
        extends.get("path") != "config/p4_event_evaluation_v1_5.yaml"
        or extends.get("sha256") != EXPECTED_EVALUATION_DESIGN_V1_5_SHA256
        or extends.get("schema_version") != EXPECTED_EVALUATION_V1_5_SCHEMA_VERSION
        or dict(inheritance)
        != {
            "sample_identity": "byte_frozen",
            "heldout_candidate_window": "byte_frozen",
            "heldout_selection_seed": "byte_frozen",
            "evaluation_thresholds": "byte_frozen",
            "dev_annotation_bytes": "byte_frozen",
            "heldout_annotation_provenance": "byte_frozen",
            "prompt": "byte_frozen",
            "model_result_schema": "byte_frozen",
            "materialized_result_schema": "byte_frozen",
            "candidate_slicing": "byte_frozen",
            "llm_parameters_except_model": "byte_frozen",
        }
    ):
        raise EventEvaluationDesignError("P4.2a v1.6 inheritance contract drifted")
    base_design = _load_v1_5_event_evaluation_design(
        project_root / "config/p4_event_evaluation_v1_5.yaml",
        project_root=project_root,
    )

    active = _mapping(
        overlay.get("active_prediction_contract"),
        "active_prediction_contract",
    )
    expected_active: JsonObject = {
        "path": "config/p4_event_extract_eval_v1_7.yaml",
        "sha256": "68474e4bd4fd5c9c88711dd5e102898ad1ed75a0fb984045efbd14e51a6db701",
        "schema_version": "p4.2a-event-extract-eval-v1.7",
        "model": "qwen3.7-flash",
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "prompt": {
            "path": "config/prompts/p4_news_event_extract_v1_5.txt",
            "sha256": ("4b44ed5efe281b68664b415865b758b75b30ace6eda2617952de66a87596c204"),
        },
        "model_result_schema": {
            "path": "config/schemas/p4_news_event_candidate_v1.schema.json",
            "sha256": ("c106cd15bd974de19ecc01d6e99e8f39c39fbf14df3a3b4dc74ee9b08ff6dd66"),
        },
        "materialized_result_schema": {
            "path": "config/schemas/p4_news_event_v1.schema.json",
            "sha256": ("0ac68654ce23ecd4e537d849d695e092c76dcb9de0fb03793e65ae62b181947f"),
        },
        "taxonomy_version": "p4-news-event-taxonomy-v1",
        "input_representation": {
            "name": "ordered_evidence_candidates_v1",
            "original_text_in_model_message": False,
            "declared_input_identity": "legacy_eight_field_user_json_v1",
            "active_model_input_identity": ("canonical_ordered_evidence_candidates_user_json_v1"),
            "dual_hash_identity_required": True,
        },
        "candidate_materialization": {
            "candidate_field": "evidence_candidate_id",
            "final_field": "evidence_span",
            "algorithm_version": ("ordered-raw-partition-unicode-whitespace-display-v1"),
            "materialization": "exact_raw_slice_from_registered_start_end",
            "invalid_candidate_policy": "fail_closed_without_repair",
            "matcher_revalidation_required": True,
        },
        "evidence_span_match_mode": ("unicode_whitespace_elided_contiguous_substring_v1"),
        "explicit_cache": {"enabled": False, "cache_control": None},
    }
    if dict(active) != expected_active:
        raise EventEvaluationDesignError("P4.2a v1.6 active prediction contract identity drifted")
    active_path = _verify_frozen_artifact(
        project_root,
        active,
        label="active prediction contract",
    )
    try:
        prediction_contract = load_event_extract_contract(
            active_path,
            project_root=project_root,
        )
    except (EventExtractContractError, OSError) as exc:
        raise EventEvaluationDesignError("active prediction contract validation failed") from exc
    if (
        prediction_contract.document.get("schema_version") != active.get("schema_version")
        or prediction_contract.model != active.get("model")
        or prediction_contract.endpoint != active.get("endpoint")
        or prediction_contract.evidence_candidate_selection is not True
        or prediction_contract.materialized_schema is None
        or prediction_contract.evidence_span_match_mode != active.get("evidence_span_match_mode")
        or prediction_contract.explicit_cache_enabled is not False
    ):
        raise EventEvaluationDesignError("P4.2a v1.6 active prediction contract binding drifted")

    candidate_document = copy.deepcopy(prediction_contract.document)
    incumbent_document = copy.deepcopy(base_design.prediction_contract.document)
    candidate_document["schema_version"] = incumbent_document["schema_version"]
    candidate_document["owner_spec_commit"] = incumbent_document["owner_spec_commit"]
    candidate_document["pre_registered_at"] = incumbent_document["pre_registered_at"]
    candidate_llm = _mapping(candidate_document.get("llm"), "candidate contract llm")
    incumbent_llm = _mapping(
        incumbent_document.get("llm"),
        "incumbent contract llm",
    )
    candidate_llm_mutable = cast(JsonObject, candidate_llm)
    candidate_llm_mutable["model"] = incumbent_llm.get("model")
    if candidate_document != incumbent_document:
        raise EventEvaluationDesignError(
            "P4.2a v1.7 contract changed more than model and identity metadata"
        )

    artifacts = copy.deepcopy(cast(JsonObject, base_design.document["artifacts"]))
    artifact_overrides = _mapping(
        overlay.get("artifact_overrides"),
        "artifact_overrides",
    )
    expected_artifact_names = _V1_2_CREATE_ONLY_ARTIFACTS | {"model_selection_outcome_receipt_json"}
    if set(artifact_overrides) != expected_artifact_names:
        raise EventEvaluationDesignError("P4.2a v1.6 create-only artifact set drifted")
    seen_paths: set[Path] = set()
    eval_root = (project_root / _ARTIFACT_ROOT).resolve()
    for name, raw_entry in artifact_overrides.items():
        entry = _mapping(raw_entry, f"artifact_overrides.{name}")
        path_value = _artifact_path(
            project_root,
            entry.get("path"),
            label=f"artifact_overrides.{name}.path",
        )
        if path_value != eval_root and not path_value.is_relative_to(eval_root):
            raise EventEvaluationDesignError(f"artifact_overrides.{name} escapes the eval root")
        if name == "model_selection_outcome_receipt_json":
            if path_value.as_posix() != (eval_root / "P4.2a-model-selection-v1.7.json").as_posix():
                raise EventEvaluationDesignError("model-selection outcome receipt path drifted")
        elif "/v1.6" not in path_value.as_posix() and "v1.6" not in path_value.name:
            raise EventEvaluationDesignError(f"artifact_overrides.{name} is not v1.6 namespaced")
        if path_value in seen_paths:
            raise EventEvaluationDesignError("P4.2a v1.6 artifact paths must be unique")
        seen_paths.add(path_value)
        create_only = (
            entry.get("create_only_reports")
            if name == "evaluation_report_directory"
            else entry.get("create_only")
        )
        if create_only is not True:
            raise EventEvaluationDesignError(f"artifact_overrides.{name} must be create-only")
        artifacts[name] = dict(entry)

    freeze_overlay = _mapping(
        overlay.get("prediction_contract_freeze"),
        "prediction_contract_freeze",
    )
    expected_freeze = {
        **cast(JsonObject, base_design.document["prediction_contract_freeze"]),
        "required_model": "qwen3.7-flash",
    }
    for key, value in freeze_overlay.items():
        if expected_freeze.get(key) != value:
            raise EventEvaluationDesignError("P4.2a v1.6 prediction freeze binding drifted")
    expected_freeze_overlay_keys = {
        "required_contract_artifact",
        "required_model",
        "required_endpoint",
        "required_prompt_sha256",
        "required_result_schema_sha256",
        "required_model_result_schema_sha256",
        "required_taxonomy_version",
        "required_explicit_cache_enabled",
        "required_evidence_span_match_mode",
        "required_input_representation",
        "required_dual_hash_identity",
    }
    if set(freeze_overlay) != expected_freeze_overlay_keys:
        raise EventEvaluationDesignError("P4.2a v1.6 prediction freeze field set drifted")

    model_selection = _mapping(
        overlay.get("model_selection"),
        "model_selection",
    )
    expected_incumbent: JsonObject = {
        "design": {
            "path": "config/p4_event_evaluation_v1_5.yaml",
            "sha256": EXPECTED_EVALUATION_DESIGN_V1_5_SHA256,
        },
        "contract": {
            "path": "config/p4_event_extract_eval_v1_6.yaml",
            "sha256": ("4e88990d2ee7671db316794aabd0a476f798b5e542f00bbb8ffbd3f7fd423269"),
        },
        "dev_predictions": {
            "path": ("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.6-r1.predictions.jsonl"),
            "sha256": ("44f09a0e20d51d392461980d0b3dce886dbaa9699448ca24d2a8ce1f27839e10"),
        },
        "dev_manifest": {
            "path": ("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.6-r1.manifest.json"),
            "sha256": ("ef11067c94652262535c928140dd60d721dfb7638a7ac41ea05108cbda844dbd"),
        },
        "dev_report": {
            "path": ("docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.6-r1.report.json"),
            "sha256": ("6aa0fdaa3f105e87f4ac40975628b9dcc7d26117a07c945421fa899c7190afa4"),
        },
        "dev_final_predictions": {
            "path": "docs/phase4/eval/P4.2a-dev60-final-predictions-v1.5.jsonl",
            "sha256": ("e419c06a0e1112753490b66cfb3709dd9fb311361d0a4c392c5e78029e193685"),
        },
        "dev_final_manifest": {
            "path": ("docs/phase4/eval/P4.2a-dev60-final-predictions-v1.5.manifest.json"),
            "sha256": ("1ef00b10876c3f4ca8cbb0e02efaeeac17d057b7758297b9decaab89e501c79f"),
        },
        "freeze_receipt": {
            "path": ("docs/phase4/eval/P4.2a-heldout-prediction-contract-freeze-v1.5.json"),
            "sha256": ("9adab49b5b5e8d0bf942a591878c1718fc3d158f5638144db7c5cb80b1e63f68"),
        },
    }
    expected_selection: JsonObject = {
        "official_round_id": "v1.7-r1",
        "deadline_utc": "2026-08-05T16:10:00Z",
        "single_formal_round": True,
        "third_model_forbidden": True,
        "selection_rule": ("candidate_if_all_absolute_gates_pass_else_retain_incumbent"),
        "relative_score_selection_forbidden": True,
        "incumbent": expected_incumbent,
        "candidate": {
            "contract": {
                "path": "config/p4_event_extract_eval_v1_7.yaml",
                "sha256": ("68474e4bd4fd5c9c88711dd5e102898ad1ed75a0fb984045efbd14e51a6db701"),
            },
            "model": "qwen3.7-flash",
        },
        "gates": {
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
        },
        "pricing_cny_per_million": {
            "plus": {"input": 2.0, "output": 12.0},
            "flash": {"input": 0.2, "output": 0.8},
        },
        "monthly_calls": 15000,
        "outcome_receipt_artifact": "model_selection_outcome_receipt_json",
    }
    if dict(model_selection) != expected_selection:
        raise EventEvaluationDesignError("P4.2a v1.7 model-selection preregistration drifted")
    for name, entry in expected_incumbent.items():
        _verify_frozen_artifact(
            project_root,
            cast(Mapping[str, Any], entry),
            label=f"model_selection.incumbent.{name}",
        )
    candidate = _mapping(
        model_selection.get("candidate"),
        "model_selection.candidate",
    )
    if candidate.get("contract") != {
        "path": active.get("path"),
        "sha256": active.get("sha256"),
    } or candidate.get("model") != active.get("model"):
        raise EventEvaluationDesignError("P4.2a v1.7 model-selection candidate binding drifted")

    evaluation_overlay = _mapping(overlay.get("evaluation"), "evaluation")
    if dict(evaluation_overlay) != {"required_report_fields_append": []}:
        raise EventEvaluationDesignError("P4.2a v1.6 evaluation contract drifted")
    isolation = _mapping(overlay.get("isolation"), "isolation")
    if dict(isolation) != dict(base_design.document["isolation"]):
        raise EventEvaluationDesignError("P4.2a v1.6 runtime isolation drifted")

    document = copy.deepcopy(base_design.document)
    document["schema_version"] = overlay["schema_version"]
    document["owner_spec_commit"] = overlay["owner_spec_commit"]
    document["pre_registered_at"] = overlay["pre_registered_at"]
    document["artifacts"] = artifacts
    document["active_prediction_contract"] = dict(active)
    document["model_selection"] = copy.deepcopy(dict(model_selection))
    freeze = cast(JsonObject, document["prediction_contract_freeze"])
    freeze.update(dict(freeze_overlay))
    return EventEvaluationDesign(
        path=path.resolve(),
        sha256=digest,
        document=document,
        base_contract=base_design.base_contract,
        prediction_contract=prediction_contract,
    )


def _load_v1_7_event_evaluation_design(
    path: Path,
    *,
    project_root: Path,
) -> EventEvaluationDesign:
    """Load the deterministic held-out materialization-eligibility successor."""

    try:
        payload = path.resolve().read_bytes()
    except OSError as exc:
        raise EventEvaluationDesignError("P4.2a v1.7 design is unavailable") from exc
    digest = _sha256_bytes(payload)
    if digest != EXPECTED_EVALUATION_DESIGN_V1_7_SHA256:
        raise EventEvaluationDesignError("P4.2a v1.7 design differs from its frozen SHA-256")
    try:
        loaded: object = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise EventEvaluationDesignError("P4.2a v1.7 design is invalid YAML") from exc
    if not isinstance(loaded, dict):
        raise EventEvaluationDesignError("P4.2a v1.7 design must be a mapping")
    overlay = cast(JsonObject, loaded)
    if set(overlay) != {
        "schema_version",
        "owner_spec_commit",
        "pre_registered_at",
        "production_writes_allowed",
        "artifact_root",
        "extends_design",
        "candidate_eligibility",
        "materialization_rounds",
        "artifact_overrides",
        "isolation",
    }:
        raise EventEvaluationDesignError("P4.2a v1.7 top-level contract drifted")
    if (
        overlay.get("schema_version") != EXPECTED_EVALUATION_V1_7_SCHEMA_VERSION
        or overlay.get("owner_spec_commit")
        != "3e21bff982156ef973c2e5badfe39b06d9596237"
        or overlay.get("pre_registered_at") != "2026-08-06T08:00:00Z"
        or overlay.get("production_writes_allowed") is not False
        or overlay.get("artifact_root") != _ARTIFACT_ROOT.as_posix()
    ):
        raise EventEvaluationDesignError("P4.2a v1.7 identity/isolation binding drifted")

    extends = _mapping(overlay.get("extends_design"), "extends_design")
    inheritance = _mapping(extends.get("inheritance"), "extends_design.inheritance")
    expected_inheritance = {
        "active_prediction_contract": "byte_frozen",
        "prediction_contract_freeze_receipt": "byte_frozen",
        "model_selection_outcome": "byte_frozen",
        "heldout_candidate_window": "byte_frozen",
        "heldout_selection_seed": "byte_frozen",
        "heldout_selected_count": "byte_frozen",
        "evaluation_thresholds": "byte_frozen",
        "dev_annotation_bytes": "byte_frozen",
        "heldout_annotation_provenance": "byte_frozen",
        "prompt": "byte_frozen",
        "model": "byte_frozen",
        "model_result_schema": "byte_frozen",
        "materialized_result_schema": "byte_frozen",
        "candidate_slicing": "byte_frozen",
        "llm_parameters": "byte_frozen",
    }
    if (
        extends.get("path") != "config/p4_event_evaluation_v1_6.yaml"
        or extends.get("sha256") != EXPECTED_EVALUATION_DESIGN_V1_6_SHA256
        or extends.get("schema_version") != EXPECTED_EVALUATION_V1_6_SCHEMA_VERSION
        or dict(inheritance) != expected_inheritance
    ):
        raise EventEvaluationDesignError("P4.2a v1.7 inheritance contract drifted")
    base_design = _load_v1_6_event_evaluation_design(
        project_root / "config/p4_event_evaluation_v1_6.yaml",
        project_root=project_root,
    )

    candidate_eligibility = _mapping(
        overlay.get("candidate_eligibility"),
        "candidate_eligibility",
    )
    expected_eligibility: JsonObject = {
        "schema_version": "p4.2a-heldout-candidate-eligibility-v1",
        "deterministic_document_ineligible_reasons": [
            "pdf_text_below_min_char_gate",
            "pdf_exceeds_size_bound",
        ],
        "minimum_extracted_characters": 80,
        "max_pdf_bytes": 8 * 1024 * 1024,
        "transient_download_failures_fail_closed": True,
        "sample_only_from_eligible_pool": True,
        "insufficient_stratum_policy": "fail_without_substitution",
    }
    if dict(candidate_eligibility) != expected_eligibility:
        raise EventEvaluationDesignError("P4.2a v1.7 candidate eligibility drifted")

    materialization_rounds = _mapping(
        overlay.get("materialization_rounds"),
        "materialization_rounds",
    )
    expected_rounds: JsonObject = {
        "failed_origin": {
            "round_id": "heldout-v1.6-r1",
            "status": "materialization_failed_no_inference",
            "record": {
                "path": (
                    "docs/phase4/eval/"
                    "P4.2a-heldout-materialization-failure-v1.6-r1.json"
                ),
                "sha256": (
                    "e14650690f3a1efbe6b455cc0a2d043c08c296ff88433cf14395f40f001ea6db"
                ),
            },
            "inference_started": False,
            "model_calls": 0,
        },
        "authorized_successor": {
            "round_id": "heldout-v1.7-r1",
            "exactly_one_one_shot": True,
            "predecessor_must_remain_failed": True,
            "model": "qwen3.7-flash",
            "prediction_contract": {
                "path": "config/p4_event_extract_eval_v1_7.yaml",
                "sha256": (
                    "68474e4bd4fd5c9c88711dd5e102898ad1ed75a0fb984045efbd14e51a6db701"
                ),
            },
            "prompt": {
                "path": "config/prompts/p4_news_event_extract_v1_5.txt",
                "sha256": (
                    "4b44ed5efe281b68664b415865b758b75b30ace6eda2617952de66a87596c204"
                ),
            },
            "freeze_receipt": {
                "path": (
                    "docs/phase4/eval/"
                    "P4.2a-heldout-prediction-contract-freeze-v1.6.json"
                ),
                "sha256": (
                    "1b2aae88d075dbe1d93660b76f9463f35157f7dbd00a6dc7f73d61370b4231a1"
                ),
            },
            "model_selection_outcome": {
                "path": "docs/phase4/eval/P4.2a-model-selection-v1.7.json",
                "sha256": (
                    "38f20f4eebe4da1da48fa14d3b09bb2a042d27eed5efc4a533a63718659059e0"
                ),
            },
            "automatic_retries": 0,
            "failed_candidate_retries": 0,
            "candidate_order": "frozen_database_id_ascending",
        },
    }
    if dict(materialization_rounds) != expected_rounds:
        raise EventEvaluationDesignError("P4.2a v1.7 materialization rounds drifted")
    failed_origin = _mapping(materialization_rounds.get("failed_origin"), "failed_origin")
    _verify_frozen_artifact(
        project_root,
        _mapping(failed_origin.get("record"), "failed_origin.record"),
        label="failed materialization round record",
    )
    successor = _mapping(
        materialization_rounds.get("authorized_successor"),
        "authorized_successor",
    )
    for name in (
        "prediction_contract",
        "prompt",
        "freeze_receipt",
        "model_selection_outcome",
    ):
        _verify_frozen_artifact(
            project_root,
            _mapping(successor.get(name), f"authorized_successor.{name}"),
            label=f"authorized successor {name}",
        )

    artifact_overrides = _mapping(overlay.get("artifact_overrides"), "artifact_overrides")
    expected_artifact_overrides: JsonObject = {
        "heldout_candidate_inputs_jsonl": {
            "path": (
                "docs/phase4/eval/P4.2a-heldout-materialization-v1.7/"
                "candidate-inputs.jsonl"
            ),
            "create_only": True,
        },
        "heldout_candidate_materialization_manifest_json": {
            "path": (
                "docs/phase4/eval/P4.2a-heldout-materialization-v1.7/manifest.json"
            ),
            "create_only": True,
        },
        "heldout_candidate_predictions_jsonl": {
            "path": "docs/phase4/eval/P4.2a-heldout-candidate-predictions-v1.7.jsonl",
            "create_only": True,
        },
        "heldout_candidate_predictions_manifest_json": {
            "path": (
                "docs/phase4/eval/P4.2a-heldout-candidate-predictions-v1.7.manifest.json"
            ),
            "create_only": True,
        },
        "heldout_inference_state_jsonl": {
            "path": (
                "docs/phase4/eval/P4.2a-heldout-inference-one-shot-v1.7.state.jsonl"
            ),
            "create_only": True,
        },
        "heldout_selection_manifest_json": {
            "path": "docs/phase4/eval/P4.2a-heldout40-selection-v1.7.manifest.json",
            "create_only": True,
        },
        "heldout_evaluation_state_jsonl": {
            "path": (
                "docs/phase4/eval/P4.2a-heldout-evaluation-one-shot-v1.7.state.jsonl"
            ),
            "create_only": True,
        },
        "heldout_40_blind_sample_jsonl": {
            "path": "docs/phase4/eval/P4.2a-gold-heldout40-blind-sample-v1.7.jsonl",
            "create_only": True,
        },
        "heldout_40_owner_annotations_jsonl": {
            "path": (
                "docs/phase4/eval/P4.2a-gold-heldout40-human-adjudicated-v1.7.jsonl"
            ),
            "create_only": True,
        },
        "combined_100_annotations_jsonl": {
            "path": "docs/phase4/eval/P4.2a-gold-annotation100-v1.7.jsonl",
            "create_only": True,
        },
        "owner_completion_manifest_json": {
            "path": "docs/phase4/eval/P4.2a-owner-completion-v1.7.manifest.json",
            "create_only": True,
        },
        "evaluation_report_directory": {
            "path": "docs/phase4/eval/reports/v1.7",
            "create_only_reports": True,
            "append_only_failed_rounds": True,
        },
    }
    if set(artifact_overrides) != set(expected_artifact_overrides):
        raise EventEvaluationDesignError("P4.2a v1.7 create-only artifact set drifted")
    artifacts = copy.deepcopy(cast(JsonObject, base_design.document["artifacts"]))
    seen_paths: set[Path] = set()
    eval_root = (project_root / _ARTIFACT_ROOT).resolve()
    for name, raw_entry in artifact_overrides.items():
        entry = _mapping(raw_entry, f"artifact_overrides.{name}")
        resolved = _artifact_path(
            project_root,
            entry.get("path"),
            label=f"artifact_overrides.{name}.path",
        )
        if resolved != eval_root and not resolved.is_relative_to(eval_root):
            raise EventEvaluationDesignError(f"artifact_overrides.{name} escapes eval root")
        if "v1.7" not in resolved.as_posix():
            raise EventEvaluationDesignError(f"artifact_overrides.{name} is not v1.7 namespaced")
        if resolved in seen_paths:
            raise EventEvaluationDesignError("P4.2a v1.7 artifact paths must be unique")
        seen_paths.add(resolved)
        create_only = (
            entry.get("create_only_reports")
            if name == "evaluation_report_directory"
            else entry.get("create_only")
        )
        if create_only is not True:
            raise EventEvaluationDesignError(f"artifact_overrides.{name} must be create-only")
        artifacts[name] = dict(entry)
    if dict(artifact_overrides) != expected_artifact_overrides:
        raise EventEvaluationDesignError("P4.2a v1.7 create-only artifact bytes drifted")

    isolation = _mapping(overlay.get("isolation"), "isolation")
    if dict(isolation) != dict(base_design.document["isolation"]):
        raise EventEvaluationDesignError("P4.2a v1.7 runtime isolation drifted")

    document = copy.deepcopy(base_design.document)
    document["schema_version"] = overlay["schema_version"]
    document["owner_spec_commit"] = overlay["owner_spec_commit"]
    document["pre_registered_at"] = overlay["pre_registered_at"]
    document["artifacts"] = artifacts
    document["candidate_eligibility"] = copy.deepcopy(dict(candidate_eligibility))
    document["materialization_rounds"] = copy.deepcopy(dict(materialization_rounds))
    return EventEvaluationDesign(
        path=path.resolve(),
        sha256=digest,
        document=document,
        base_contract=base_design.base_contract,
        prediction_contract=base_design.prediction_contract,
    )


def validate_heldout_annotation_provenance(
    record: Mapping[str, object],
    design: EventEvaluationDesign,
) -> JsonObject:
    """Fail closed unless one held-out label has distinct AI and human identities."""

    raw = design.document.get("heldout_annotation_provenance")
    if raw is None:
        return {}
    provenance = _mapping(raw, "heldout_annotation_provenance")
    expected_type = provenance.get("annotation_type")
    annotation_type = record.get("annotation_type")
    drafter_id = record.get("drafter_id")
    adjudicator_id = record.get("adjudicator_id")
    annotation_owner = record.get("annotation_owner")
    if annotation_type != expected_type:
        raise EventEvaluationDesignError(
            "heldout annotation is not AI-drafted and human-adjudicated"
        )
    if not isinstance(drafter_id, str) or not drafter_id.strip():
        raise EventEvaluationDesignError("heldout annotation drafter_id is missing")
    if not isinstance(adjudicator_id, str) or not adjudicator_id.strip():
        raise EventEvaluationDesignError("heldout annotation adjudicator_id is missing")
    normalized_drafter = drafter_id.strip()
    normalized_adjudicator = adjudicator_id.strip()
    if normalized_drafter.casefold() == normalized_adjudicator.casefold():
        raise EventEvaluationDesignError("heldout AI drafter and human adjudicator must differ")
    if annotation_owner != normalized_adjudicator:
        raise EventEvaluationDesignError("heldout annotation_owner must equal adjudicator_id")
    return {
        "annotation_type": annotation_type,
        "drafter_id": normalized_drafter,
        "adjudicator_id": normalized_adjudicator,
    }


def load_event_evaluation_design(
    path: Path = DEFAULT_EVALUATION_DESIGN_PATH,
    *,
    project_root: Path = PROJECT_ROOT,
) -> EventEvaluationDesign:
    """Load one supported byte-frozen P4.2a evaluation design."""

    resolved = path.resolve()
    if resolved == (project_root / "config/p4_event_evaluation_v1_7.yaml").resolve():
        return _load_v1_7_event_evaluation_design(
            resolved,
            project_root=project_root,
        )
    if resolved == (project_root / "config/p4_event_evaluation_v1_6.yaml").resolve():
        return _load_v1_6_event_evaluation_design(
            resolved,
            project_root=project_root,
        )
    if resolved == (project_root / "config/p4_event_evaluation_v1_5.yaml").resolve():
        return _load_v1_5_event_evaluation_design(
            resolved,
            project_root=project_root,
        )
    if resolved == (project_root / "config/p4_event_evaluation_v1_4.yaml").resolve():
        return _load_v1_4_event_evaluation_design(
            resolved,
            project_root=project_root,
        )
    if resolved == (project_root / "config/p4_event_evaluation_v1_3.yaml").resolve():
        return _load_v1_3_event_evaluation_design(
            resolved,
            project_root=project_root,
        )
    if resolved == (project_root / "config/p4_event_evaluation_v1_2.yaml").resolve():
        return _load_v1_2_event_evaluation_design(
            resolved,
            project_root=project_root,
        )
    if resolved == LEGACY_EVALUATION_DESIGN_PATH.resolve() or (
        resolved.name == LEGACY_EVALUATION_DESIGN_PATH.name
    ):
        return _load_v1_1_event_evaluation_design(
            resolved,
            project_root=project_root,
        )
    raise EventEvaluationDesignError("unsupported P4.2a evaluation design version")


__all__ = [
    "DEFAULT_EVALUATION_DESIGN_PATH",
    "EVALUATION_DESIGN_V1_2_PATH",
    "EVALUATION_DESIGN_V1_3_PATH",
    "EVALUATION_DESIGN_V1_4_PATH",
    "EVALUATION_DESIGN_V1_5_PATH",
    "EVALUATION_DESIGN_V1_6_PATH",
    "EVALUATION_DESIGN_V1_7_PATH",
    "EXPECTED_BASE_CONTRACT_SHA256",
    "EXPECTED_EVALUATION_DESIGN_SHA256",
    "EXPECTED_EVALUATION_DESIGN_V1_2_SHA256",
    "EXPECTED_EVALUATION_DESIGN_V1_3_SHA256",
    "EXPECTED_EVALUATION_DESIGN_V1_4_SHA256",
    "EXPECTED_EVALUATION_DESIGN_V1_5_SHA256",
    "EXPECTED_EVALUATION_DESIGN_V1_6_SHA256",
    "EXPECTED_EVALUATION_DESIGN_V1_7_SHA256",
    "EXPECTED_EVALUATION_SCHEMA_VERSION",
    "EXPECTED_EVALUATION_V1_2_SCHEMA_VERSION",
    "EXPECTED_EVALUATION_V1_3_SCHEMA_VERSION",
    "EXPECTED_EVALUATION_V1_4_SCHEMA_VERSION",
    "EXPECTED_EVALUATION_V1_5_SCHEMA_VERSION",
    "EXPECTED_EVALUATION_V1_6_SCHEMA_VERSION",
    "EXPECTED_EVALUATION_V1_7_SCHEMA_VERSION",
    "EXPECTED_OWNER_COMPLETION_MANIFEST_FIELDS",
    "EXPECTED_OWNER_FORBIDDEN_FIELDS",
    "EXPECTED_OWNER_REQUIRED_FIELDS",
    "EXPECTED_RECEIPT_FIELDS",
    "EXPECTED_REPORT_FIELDS",
    "EventEvaluationDesign",
    "EventEvaluationDesignError",
    "load_event_evaluation_design",
    "validate_heldout_annotation_provenance",
]
