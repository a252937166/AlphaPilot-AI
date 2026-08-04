from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from scripts import run_p4_2a_heldout_predictions as heldout
from scripts.run_p4_2a_offline_extract import (
    ChatJsonCallable,
    ExtractionSummary,
    _settings_from_project_env,
    _validate_runtime_contract,
    extract_records,
)

from alphapilot.core.config import Settings
from alphapilot.llm.p4_news_eval import (
    DEFAULT_EVALUATION_DESIGN_PATH,
    EventEvaluationDesign,
    load_event_evaluation_design,
)
from alphapilot.llm.p4_news_event import (
    EXACT_EVIDENCE_SPAN_MATCH_MODE,
    WHITESPACE_NORMALIZED_EVIDENCE_SPAN_MATCH_MODE,
    EventExtractContract,
    evidence_span_matches,
)

JsonObject = dict[str, Any]
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEV_LABELS_PATH = Path(
    "docs/phase4/eval/P4.2a-gold-inventory60-v1.labels-ai-drafted.jsonl"
)
DEV_LABELS_SHA256 = (
    "d1b9720dc06a4ff4989b65d2c1c302f614b0fbc427bedabde4ef87ad606a011d"
)
DEV_INPUT_SHA256 = (
    "81b3c0b27cd344fe4c2a735261e501dd2f60a0927c14b2c37e5b2a4879b4a2ba"
)
DEV_LABELER = "ChatGPT（GPT-5.6 Pro，受欧阳委托）"
BASELINE_FAILURE_IDS = (190,)
FLASH_BASELINE = {
    "model": "qwen3.6-flash",
    "materiality_positive": {
        "matches": 7,
        "denominator": 14,
        "agreement": 0.5,
    },
    "symbol_exact_set": {
        "matches": 58,
        "denominator": 59,
        "agreement": 58 / 59,
    },
    "success_count": 59,
    "failure_count": 1,
    "failure_ids": [190],
}
P4_1_CONFIG_PATH = Path("config/p4_news_poll_v1.yaml")
P4_1_CONFIG_SHA256 = (
    "d0dcd665472b50092a1b4fa7f65f7115778e1b89ac11aca0ed49dc70beaa790b"
)
MATERIALITY_TARGET = 0.80
SYMBOL_TARGET = 0.95
ROUND_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
V1_3_ACTUAL_FAILURE_IDS = (250, 258, 287, 304, 306, 336, 358)
V1_3_WHITESPACE_RECOVERED_IDS = (250, 258, 287, 306, 358)
V1_3_TRUE_SYNTHESIS_FAILURE_IDS = (304, 336)
AI_LABEL_DEFECT_IDS = (44,)
MODEL_OVER_ATTRIBUTION_IDS = (75, 210, 232, 393)
_V1_3_DEV_REPORT_FIELDS = frozenset(
    {
        "prediction_contract.evidence_span_match_mode",
        "evidence_validation.v1_3_actual",
        "evidence_validation.whitespace_normalized_counterfactual",
        "evidence_validation.v1_4_actual",
        "evidence_validation.v1_4_legacy_exact_shadow",
        "symbol_diagnostics.ai_label_defect_ids",
        "symbol_diagnostics.adjusted_exact_set",
    }
)
_V1_4_DEV_REPORT_FIELDS = frozenset(
    {
        "evidence_validation.v1_4_r1_actual",
        "evidence_validation.v1_5_actual",
        "evidence_validation.v1_5_legacy_exact_shadow",
        "symbol_diagnostics.v1_4_r1_actual",
    }
)
_V1_5_DEV_REPORT_FIELDS = frozenset(
    {
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
    }
)


class DevIterationError(RuntimeError):
    """A P4.2a dev-only iteration violated its frozen evidence contract."""


@dataclass(frozen=True, slots=True)
class DevIterationResult:
    summary: ExtractionSummary
    predictions_path: Path
    manifest_path: Path
    report_path: Path
    report: JsonObject


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DevIterationError(f"{name} must be a mapping")
    return cast(Mapping[str, Any], value)


def _utc_now(clock: Callable[[], datetime]) -> str:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise DevIterationError("clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DevIterationError(f"{name} must be a non-blank timestamp")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DevIterationError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DevIterationError(f"{name} must include a timezone")
    return parsed.astimezone(UTC)


def _artifact_paths(root: Path, round_id: str) -> tuple[Path, Path, Path]:
    if ROUND_ID.fullmatch(round_id) is None:
        raise DevIterationError("round_id must be a safe lowercase artifact identifier")
    directory = root / "docs/phase4/eval/dev-iterations"
    stem = f"P4.2a-dev60-{round_id}"
    return (
        directory / f"{stem}.predictions.jsonl",
        directory / f"{stem}.manifest.json",
        directory / f"{stem}.report.json",
    )


def _load_dev_labels(
    root: Path,
    input_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[int, JsonObject], Path]:
    path = (root / DEV_LABELS_PATH).resolve()
    eval_root = (root / "docs/phase4/eval").resolve()
    if (
        not path.is_relative_to(eval_root)
        or path.is_symlink()
        or not path.is_file()
        or heldout._sha256_file(path) != DEV_LABELS_SHA256
    ):
        raise DevIterationError("AI-drafted dev labels differ from their frozen SHA-256")
    rows = heldout._load_jsonl(path, "AI-drafted dev labels")
    if len(rows) != 60 or len(input_rows) != 60:
        raise DevIterationError("dev inputs and AI labels must each contain 60 rows")
    input_by_id = {
        cast(int, row["news_item_id"]): row
        for row in input_rows
        if isinstance(row.get("news_item_id"), int)
        and not isinstance(row.get("news_item_id"), bool)
    }
    if len(input_by_id) != 60:
        raise DevIterationError("frozen dev inputs contain invalid or duplicate IDs")

    labels: dict[int, JsonObject] = {}
    immutable_fields = (
        "news_item_id",
        "source",
        "ingested_symbol",
        "title",
        "original_text",
        "available_time",
        "published_at",
        "input_sha256",
        "text_sha256",
        "body_state",
    )
    forbidden = {
        "prediction",
        "model_prediction",
        "predicted_materiality",
        "selection_basis",
        "selection_rank",
    }
    for row in rows:
        identifier = row.get("news_item_id")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier not in input_by_id
            or identifier in labels
        ):
            raise DevIterationError("AI-drafted dev label IDs drifted")
        source = input_by_id[identifier]
        if any(row.get(field) != source.get(field) for field in immutable_fields):
            raise DevIterationError("AI-drafted dev labels changed frozen input fields")
        if forbidden.intersection(row):
            raise DevIterationError("AI-drafted dev labels contain prediction leakage")
        if (
            row.get("annotation_status") != "completed"
            or row.get("annotation_owner") != DEV_LABELER
        ):
            raise DevIterationError("AI-drafted dev label provenance drifted")
        gold = _mapping(row.get("gold"), "AI-drafted dev gold")
        symbols = gold.get("symbols")
        if (
            not isinstance(symbols, list)
            or not all(isinstance(item, str) for item in symbols)
            or symbols != sorted(set(symbols))
            or any(re.fullmatch(r"[0-9]{6}", item) is None for item in symbols)
            or gold.get("event_type") is None
            or gold.get("direction") not in (-1, 0, 1)
            or gold.get("materiality") not in (0, 1, 2, 3)
            or not isinstance(gold.get("evidence_span"), str)
            or not cast(str, gold["evidence_span"])
        ):
            raise DevIterationError("AI-drafted dev gold fields are incomplete")
        labels[identifier] = row
    if set(labels) != set(input_by_id):
        raise DevIterationError("AI-drafted dev labels do not cover the frozen dev set")
    return labels, path


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _score_predictions(
    prediction_rows: Sequence[Mapping[str, Any]],
    labels: Mapping[int, Mapping[str, Any]],
) -> JsonObject:
    confusion: Counter[str] = Counter()
    failure_ids: list[int] = []
    symbol_mismatch_ids: list[int] = []
    false_positive_ids: list[int] = []
    false_negative_ids: list[int] = []
    failed_reference_positive_ids: list[int] = []
    comparable = 0
    symbol_matches = 0
    source_counts: dict[str, Counter[str]] = {}

    for row in prediction_rows:
        identifier = row.get("news_item_id")
        if isinstance(identifier, bool) or not isinstance(identifier, int):
            raise DevIterationError("prediction row has an invalid news_item_id")
        label = labels.get(identifier)
        if label is None:
            raise DevIterationError("prediction row is outside frozen dev60")
        source = str(row.get("source"))
        source_counter = source_counts.setdefault(source, Counter())
        gold = _mapping(label.get("gold"), "AI-drafted dev gold")
        if row.get("status") != "ok":
            failure_ids.append(identifier)
            if cast(int, gold["materiality"]) >= 2:
                failed_reference_positive_ids.append(identifier)
            source_counter["failed"] += 1
            continue
        prediction = _mapping(row.get("prediction"), "dev prediction")
        predicted_positive = cast(int, prediction["materiality"]) >= 2
        reference_positive = cast(int, gold["materiality"]) >= 2
        bucket = (
            "tp"
            if predicted_positive and reference_positive
            else "fp"
            if predicted_positive
            else "fn"
            if reference_positive
            else "tn"
        )
        confusion[bucket] += 1
        source_counter[bucket] += 1
        if bucket == "fp":
            false_positive_ids.append(identifier)
        elif bucket == "fn":
            false_negative_ids.append(identifier)
        comparable += 1
        predicted_symbols = prediction.get("symbols")
        reference_symbols = gold.get("symbols")
        if predicted_symbols == reference_symbols:
            symbol_matches += 1
        else:
            symbol_mismatch_ids.append(identifier)

    tp = confusion["tp"]
    fp = confusion["fp"]
    fn = confusion["fn"]
    tn = confusion["tn"]
    positive_denominator = tp + fp
    reference_positive_denominator = tp + fn
    positive_agreement = _ratio(tp, positive_denominator)
    positive_capture = _ratio(tp, reference_positive_denominator)
    symbol_agreement = _ratio(symbol_matches, comparable)
    development_blockers: list[str] = []
    if failure_ids:
        development_blockers.append("active_failures_present")
    if failed_reference_positive_ids:
        development_blockers.append("failed_reference_positive_items")
    if positive_agreement is None or positive_agreement < MATERIALITY_TARGET:
        development_blockers.append("materiality_positive_agreement_below_target")
    if symbol_agreement is None or symbol_agreement < SYMBOL_TARGET:
        development_blockers.append("symbol_exact_set_agreement_below_target")
    return {
        "metric_semantics": "model_interagreement",
        "reference_annotation_type": "ai_drafted_dev_signal",
        "not_phase_gate": True,
        "sample_count": len(prediction_rows),
        "comparable_count": comparable,
        "active_failure_count": len(failure_ids),
        "active_failure_ids": sorted(failure_ids),
        "failed_reference_positive_count": len(failed_reference_positive_ids),
        "failed_reference_positive_ids": sorted(failed_reference_positive_ids),
        "baseline_v1_failure_ids": list(BASELINE_FAILURE_IDS),
        "development_ready_to_freeze": not development_blockers,
        "development_blockers": development_blockers,
        "materiality_positive": {
            "definition": "materiality_gte_2",
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "predicted_positive_count": positive_denominator,
            "reference_positive_count": reference_positive_denominator,
            "positive_agreement": positive_agreement,
            "comparable_positive_capture": positive_capture,
            "development_target": MATERIALITY_TARGET,
            "development_target_reached": (
                positive_agreement is not None
                and positive_agreement >= MATERIALITY_TARGET
            ),
            "false_positive_ids": sorted(false_positive_ids),
            "false_negative_ids": sorted(false_negative_ids),
        },
        "symbol_exact_set": {
            "matches": symbol_matches,
            "denominator": comparable,
            "agreement": symbol_agreement,
            "development_target": SYMBOL_TARGET,
            "development_target_reached": (
                symbol_agreement is not None and symbol_agreement >= SYMBOL_TARGET
            ),
            "mismatch_ids": sorted(symbol_mismatch_ids),
        },
        "by_source_confusion": {
            source: {
                key: counts[key]
                for key in ("tp", "fp", "fn", "tn", "failed")
            }
            for source, counts in sorted(source_counts.items())
        },
    }


def _summary_evidence(summary: ExtractionSummary) -> JsonObject:
    return {
        "expected_count": summary.expected_count,
        "success_count": summary.success_count,
        "failure_count": summary.failure_count,
        "newly_attempted_count": summary.newly_attempted_count,
        "retried_failure_count": summary.retried_failure_count,
        "skipped_exact_success_count": summary.skipped_exact_success_count,
        "skipped_failure_count": summary.skipped_failure_count,
        "output_line_count": summary.output_line_count,
        "failures_by_reason": summary.failures_by_reason,
        "failures_by_validation_field_and_constraint": (
            summary.failures_by_validation_field_and_constraint
        ),
        "isolated_audit_tables": list(summary.isolated_audit_tables),
        "isolated_audit_row_count": summary.isolated_audit_row_count,
        "checkpoint_audited_success_count": summary.checkpoint_audited_success_count,
    }


def _runtime_evidence(prediction_rows: Sequence[Mapping[str, Any]]) -> JsonObject:
    successful = [row for row in prediction_rows if row.get("status") == "ok"]
    latencies = [
        cast(int, row["latency_ms"])
        for row in successful
        if isinstance(row.get("latency_ms"), int)
        and not isinstance(row.get("latency_ms"), bool)
        and cast(int, row["latency_ms"]) >= 0
    ]
    prompt_tokens = 0
    completion_tokens = 0
    token_rows = 0
    for row in successful:
        tokens = row.get("tokens")
        if not isinstance(tokens, Mapping):
            continue
        prompt = tokens.get("prompt_tokens")
        completion = tokens.get("completion_tokens")
        if (
            isinstance(prompt, int)
            and not isinstance(prompt, bool)
            and prompt >= 0
            and isinstance(completion, int)
            and not isinstance(completion, bool)
            and completion >= 0
        ):
            prompt_tokens += prompt
            completion_tokens += completion
            token_rows += 1
    return {
        "successful_rows": len(successful),
        "latency_rows": len(latencies),
        "latency_ms_min": min(latencies) if latencies else None,
        "latency_ms_max": max(latencies) if latencies else None,
        "latency_ms_mean": (
            sum(latencies) / len(latencies) if latencies else None
        ),
        "token_rows": token_rows,
        "prompt_tokens_total": prompt_tokens,
        "completion_tokens_total": completion_tokens,
        "prompt_tokens_mean": (
            prompt_tokens / token_rows if token_rows else None
        ),
        "completion_tokens_mean": (
            completion_tokens / token_rows if token_rows else None
        ),
    }


def _ordered_input_hash_identity(
    prediction_rows: Sequence[Mapping[str, Any]],
    *,
    field: str,
) -> str:
    payload = b""
    prior_id = 0
    for row in prediction_rows:
        identifier = row.get("news_item_id")
        digest = row.get(field)
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier <= prior_id
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise DevIterationError(f"{field} identity fields are invalid")
        prior_id = identifier
        payload += f"{identifier}\0{digest}\n".encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _candidate_input_identity(
    prediction_rows: Sequence[Mapping[str, Any]],
) -> JsonObject:
    declared_by_id: dict[int, str] = {}
    active_by_id: dict[int, str] = {}
    distinct = 0
    for row in prediction_rows:
        identifier = row.get("news_item_id")
        declared = row.get("declared_input_sha256")
        active = row.get("input_sha256")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or not isinstance(declared, str)
            or re.fullmatch(r"[0-9a-f]{64}", declared) is None
            or not isinstance(active, str)
            or re.fullmatch(r"[0-9a-f]{64}", active) is None
            or identifier in declared_by_id
        ):
            raise DevIterationError(
                "candidate-selection rows require both input SHA-256 identities"
            )
        declared_by_id[identifier] = declared
        active_by_id[identifier] = active
        distinct += int(declared != active)
    if len(declared_by_id) != 60:
        raise DevIterationError(
            "candidate-selection dual input identity must cover dev60"
        )
    if distinct != 60:
        raise DevIterationError(
            "candidate-selection model and frozen input hashes must differ for dev60"
        )
    return {
        "declared_frozen_input_sha256": {
            "field": "declared_input_sha256",
            "representation": "legacy_eight_field_user_json_v1",
            "row_count": len(declared_by_id),
            "ordered_identity_sha256": _ordered_input_hash_identity(
                prediction_rows,
                field="declared_input_sha256",
            ),
        },
        "active_model_input_sha256": {
            "field": "input_sha256",
            "representation": (
                "canonical_ordered_evidence_candidates_user_json_v1"
            ),
            "row_count": len(active_by_id),
            "ordered_identity_sha256": _ordered_input_hash_identity(
                prediction_rows,
                field="input_sha256",
            ),
        },
        "dual_hash_identity": {
            "required": True,
            "rows_with_both": len(active_by_id),
            "distinct_hash_pair_count": distinct,
            "ordered_identity_sha256": heldout._dev_prediction_identity_sha256(
                prediction_rows
            ),
            "digest_components": [
                "news_item_id",
                "input_sha256",
                "declared_input_sha256",
                "text_sha256",
            ],
        },
    }


def _comparison_evidence(
    metrics: Mapping[str, Any],
    *,
    candidate_model: str,
    candidate_endpoint: str | None,
    candidate_prompt_sha256: object,
    candidate_contract_schema_version: object,
    candidate_evidence_span_match_mode: str,
    candidate_selection: bool,
) -> JsonObject:
    materiality = _mapping(
        metrics.get("materiality_positive"),
        "materiality metrics",
    )
    symbols = _mapping(metrics.get("symbol_exact_set"), "symbol metrics")
    materiality_agreement = materiality.get("positive_agreement")
    symbol_agreement = symbols.get("agreement")
    return {
        "baseline": FLASH_BASELINE,
        "candidate": {
            "model": candidate_model,
            "endpoint": candidate_endpoint,
            "prompt_sha256": candidate_prompt_sha256,
            "materiality_positive": {
                "matches": materiality.get("tp"),
                "denominator": materiality.get("predicted_positive_count"),
                "agreement": materiality_agreement,
            },
            "symbol_exact_set": {
                "matches": symbols.get("matches"),
                "denominator": symbols.get("denominator"),
                "agreement": symbol_agreement,
            },
            "success_count": metrics.get("comparable_count"),
            "failure_count": metrics.get("active_failure_count"),
        },
        "delta": {
            "materiality_positive_agreement": (
                cast(float, materiality_agreement) - 0.5
                if isinstance(materiality_agreement, (int, float))
                and not isinstance(materiality_agreement, bool)
                else None
            ),
            "symbol_exact_set_agreement": (
                cast(float, symbol_agreement) - (58 / 59)
                if isinstance(symbol_agreement, (int, float))
                and not isinstance(symbol_agreement, bool)
                else None
            ),
        },
        "interpretation": "indicative_comparison_not_single_variable_causality",
        "changed_dimensions": [
            dimension
            for dimension, changed in (
                ("model", candidate_model != "qwen3.6-flash"),
                ("endpoint", candidate_endpoint is not None),
                (
                    "prompt_version",
                    candidate_prompt_sha256
                    != "4474d61f17f6c8f9a6c909228423f17cc06083b5776f481c4044c0146efbde9d",
                ),
                (
                    "evidence_span_match_mode",
                    candidate_evidence_span_match_mode
                    != EXACT_EVIDENCE_SPAN_MATCH_MODE,
                ),
                (
                    "validation_contract",
                    candidate_contract_schema_version
                    != "p4.2a-event-extract-eval-v1",
                ),
                ("model_input_representation", candidate_selection),
                ("model_result_schema", candidate_selection),
                ("candidate_materialization", candidate_selection),
                ("input_identity_contract", candidate_selection),
            )
            if changed
        ],
    }


def _historical_comparison(
    design: EventEvaluationDesign,
) -> tuple[JsonObject, JsonObject, JsonObject]:
    historical = _mapping(
        design.document.get("historical_comparison"),
        "historical_comparison",
    )
    v1_3_actual = dict(
        _mapping(historical.get("v1_3_actual"), "historical v1.3 actual")
    )
    counterfactual = dict(
        _mapping(
            historical.get("whitespace_normalized_counterfactual"),
            "historical whitespace counterfactual",
        )
    )
    adjudication = dict(
        _mapping(
            historical.get("symbol_adjudication"),
            "historical symbol adjudication",
        )
    )
    if (
        v1_3_actual.get("success_count") != 53
        or v1_3_actual.get("failure_count") != 7
        or v1_3_actual.get("failure_ids") != list(V1_3_ACTUAL_FAILURE_IDS)
        or counterfactual.get("success_count") != 58
        or counterfactual.get("failure_count") != 2
        or counterfactual.get("normalization_recovered_ids")
        != list(V1_3_WHITESPACE_RECOVERED_IDS)
        or counterfactual.get("true_synthesis_failure_ids")
        != list(V1_3_TRUE_SYNTHESIS_FAILURE_IDS)
        or adjudication.get("ai_label_defect_ids") != list(AI_LABEL_DEFECT_IDS)
        or adjudication.get("model_over_attribution_ids")
        != list(MODEL_OVER_ATTRIBUTION_IDS)
        or adjudication.get("frozen_dev_labels_must_remain_unchanged") is not True
        or adjudication.get("raw_gate_uses_frozen_labels") is not True
        or adjudication.get("adjusted_diagnostic_excludes_label_defects") is not True
    ):
        raise DevIterationError("v1.4 historical comparison/adjudication drifted")
    return v1_3_actual, counterfactual, adjudication


def _v1_4_r1_anchor(design: EventEvaluationDesign) -> JsonObject:
    historical = _mapping(
        design.document.get("historical_comparison"),
        "historical_comparison",
    )
    derived = copy.deepcopy(
        dict(
            _mapping(
                historical.get("v1_4_r1_actual"),
                "historical v1.4-r1 actual",
            )
        )
    )
    extraction = _mapping(
        derived.get("extraction"),
        "historical v1.4-r1 extraction",
    )
    metrics = _mapping(
        derived.get("metrics"),
        "historical v1.4-r1 metrics",
    )
    symbol_metrics = _mapping(
        metrics.get("symbol_exact_set"),
        "historical v1.4-r1 symbol metrics",
    )
    if (
        derived.get("round_id") != "v1.4-r1"
        or derived.get("historical_round_immutable") is not True
        or derived.get("formal_dev_round_valid") is not False
        or derived.get("heldout_accessed") is not False
        or extraction.get("success_count") != 54
        or extraction.get("failure_count") != 6
        or extraction.get("failure_ids") != [253, 258, 280, 304, 336, 340]
        or symbol_metrics.get("mismatch_ids") != [28, 44, 67, 71, 96]
    ):
        raise DevIterationError("v1.4-r1 historical anchor drifted")
    return derived


def _v1_5_r1_anchor(design: EventEvaluationDesign) -> JsonObject:
    historical = _mapping(
        design.document.get("historical_comparison"),
        "historical_comparison",
    )
    derived = copy.deepcopy(
        dict(
            _mapping(
                historical.get("v1_5_r1_actual"),
                "historical v1.5-r1 actual",
            )
        )
    )
    extraction = _mapping(
        derived.get("extraction"),
        "historical v1.5-r1 extraction",
    )
    metrics = _mapping(
        derived.get("metrics"),
        "historical v1.5-r1 metrics",
    )
    symbol_metrics = _mapping(
        metrics.get("symbol_exact_set"),
        "historical v1.5-r1 symbol metrics",
    )
    if (
        derived.get("round_id") != "v1.5-r1"
        or derived.get("historical_round_immutable") is not True
        or derived.get("formal_dev_round_valid") is not False
        or derived.get("heldout_accessed") is not False
        or extraction.get("success_count") != 50
        or extraction.get("failure_count") != 10
        or extraction.get("failure_ids")
        != [9, 250, 272, 280, 303, 304, 306, 336, 340, 360]
        or extraction.get("gold_intersection_failure_ids")
        != [272, 304, 306, 336]
        or symbol_metrics.get("mismatch_ids") != [44, 393]
    ):
        raise DevIterationError("v1.5-r1 historical anchor drifted")
    return derived


def _v1_5_r1_report_layers(
    design: EventEvaluationDesign,
) -> tuple[JsonObject, JsonObject]:
    anchor = _v1_5_r1_anchor(design)
    artifacts = _mapping(anchor.get("artifacts"), "historical v1.5 artifacts")
    report_entry = _mapping(
        artifacts.get("report"),
        "historical v1.5 report artifact",
    )
    relative = report_entry.get("path")
    expected_sha256 = report_entry.get("sha256")
    if (
        not isinstance(relative, str)
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
        or not isinstance(expected_sha256, str)
    ):
        raise DevIterationError("historical v1.5 report identity drifted")
    root = design.path.parent.parent.resolve()
    report_path = (root / relative).resolve()
    eval_root = (root / "docs/phase4/eval").resolve()
    if (
        not report_path.is_relative_to(eval_root)
        or report_path.is_symlink()
        or not report_path.is_file()
        or heldout._sha256_file(report_path) != expected_sha256
    ):
        raise DevIterationError("historical v1.5 report bytes drifted")
    try:
        raw: object = json.loads(report_path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DevIterationError("historical v1.5 report is invalid JSON") from exc
    report = _mapping(raw, "historical v1.5 report")
    evidence = _mapping(
        report.get("evidence_validation"),
        "historical v1.5 evidence validation",
    )
    actual = copy.deepcopy(
        dict(_mapping(evidence.get("v1_5_actual"), "historical v1.5 actual"))
    )
    shadow = copy.deepcopy(
        dict(
            _mapping(
                evidence.get("v1_5_legacy_exact_shadow"),
                "historical v1.5 exact shadow",
            )
        )
    )
    if (
        actual.get("success_count") != 50
        or actual.get("failure_count") != 10
        or actual.get("failure_ids")
        != [9, 250, 272, 280, 303, 304, 306, 336, 340, 360]
        or shadow.get("comparable_count") != 50
        or shadow.get("match_count") != 38
        or shadow.get("mismatch_count") != 12
    ):
        raise DevIterationError("historical v1.5 report layers drifted")
    return actual, shadow


def _evidence_validation(
    *,
    design: EventEvaluationDesign,
    active_contract: EventExtractContract,
    summary: ExtractionSummary,
    prediction_rows: Sequence[Mapping[str, Any]],
    labels: Mapping[int, Mapping[str, Any]],
) -> JsonObject:
    design_version = design.document.get("schema_version")
    contract_version = active_contract.document.get("schema_version")
    version_pair = (design_version, contract_version)
    if version_pair not in {
        (
            "p4.2a-evaluation-design-v1.3",
            "p4.2a-event-extract-eval-v1.4",
        ),
        (
            "p4.2a-evaluation-design-v1.4",
            "p4.2a-event-extract-eval-v1.5",
        ),
        (
            "p4.2a-evaluation-design-v1.5",
            "p4.2a-event-extract-eval-v1.6",
        ),
    }:
        raise DevIterationError(
            "versioned evidence report design/contract pair drifted"
        )
    is_v1_5 = contract_version == "p4.2a-event-extract-eval-v1.5"
    is_v1_6 = contract_version == "p4.2a-event-extract-eval-v1.6"
    if (
        active_contract.evidence_span_match_mode
        != WHITESPACE_NORMALIZED_EVIDENCE_SPAN_MATCH_MODE
    ):
        raise DevIterationError(
            "versioned evidence report requires its frozen evidence matcher"
        )
    v1_3_actual, counterfactual, _ = _historical_comparison(design)
    exact_contract = replace(
        active_contract,
        evidence_span_match_mode=EXACT_EVIDENCE_SPAN_MATCH_MODE,
    )
    exact_comparable_ids: list[int] = []
    exact_match_ids: list[int] = []
    exact_mismatch_ids: list[int] = []
    normalized_match_ids: list[int] = []
    actual_failure_ids: list[int] = []
    for row in prediction_rows:
        identifier = row.get("news_item_id")
        if isinstance(identifier, bool) or not isinstance(identifier, int):
            raise DevIterationError("v1.4 evidence report found an invalid ID")
        if row.get("status") != "ok":
            actual_failure_ids.append(identifier)
            continue
        prediction = _mapping(row.get("prediction"), "v1.4 prediction")
        evidence = prediction.get("evidence_span")
        label = labels.get(identifier)
        if label is None:
            raise DevIterationError("v1.4 evidence report found an unknown ID")
        original_text = label.get("original_text")
        if not isinstance(evidence, str) or not isinstance(original_text, str):
            raise DevIterationError("v1.4 evidence report found invalid text")
        exact_comparable_ids.append(identifier)
        if evidence_span_matches(active_contract, evidence, original_text):
            normalized_match_ids.append(identifier)
        if evidence_span_matches(exact_contract, evidence, original_text):
            exact_match_ids.append(identifier)
        else:
            exact_mismatch_ids.append(identifier)

    v1_3_actual["historical_round_immutable"] = True
    v1_3_actual["persisted_failure_detail"] = {
        "reason": "post_validation_failed",
        "field": None,
        "constraint": None,
        "count": 7,
    }
    counterfactual["historical_round_immutable"] = True
    counterfactual["not_a_rewrite_of_v1_3"] = True
    counterfactual["reviewer_adjudicated_root_cause"] = {
        "evidence_source": "independent_reviewer_external_reproduction",
        "field": "evidence_span",
        "prior_constraint": "exact_contiguous_substring",
        "affected_count": 7,
    }
    actual = {
        "evidence_span_match_mode": active_contract.evidence_span_match_mode,
        "expected_count": summary.expected_count,
        "success_count": summary.success_count,
        "failure_count": summary.failure_count,
        "failure_ids": sorted(actual_failure_ids),
        "failures_by_validation_field_and_constraint": (
            summary.failures_by_validation_field_and_constraint
        ),
        "all_successes_pass_active_matcher": (
            len(normalized_match_ids) == summary.success_count
        ),
        "formal_round_valid": (
            summary.expected_count == 60
            and summary.success_count == 60
            and summary.failure_count == 0
        ),
    }
    legacy_exact_shadow: JsonObject = {
        "evidence_span_match_mode": EXACT_EVIDENCE_SPAN_MATCH_MODE,
        "diagnostic_only": True,
        "does_not_change_active_validation": True,
        "comparable_count": len(exact_comparable_ids),
        "match_count": len(exact_match_ids),
        "mismatch_count": len(exact_mismatch_ids),
        "mismatch_ids": sorted(exact_mismatch_ids),
        "whitespace_matcher_recovered_ids": sorted(
            set(normalized_match_ids).intersection(exact_mismatch_ids)
        ),
    }
    actual_key = (
        "v1_6_actual"
        if is_v1_6
        else "v1_5_actual"
        if is_v1_5
        else "v1_4_actual"
    )
    shadow_key = (
        "v1_6_legacy_exact_shadow"
        if is_v1_6
        else "v1_5_legacy_exact_shadow"
        if is_v1_5
        else "v1_4_legacy_exact_shadow"
    )
    result: JsonObject = {
        "comparison_semantics": (
            "immutable_actual_then_external_counterfactual_then_fresh_actual"
        ),
        "v1_3_actual": v1_3_actual,
        "whitespace_normalized_counterfactual": counterfactual,
        actual_key: actual,
        shadow_key: legacy_exact_shadow,
    }
    if is_v1_5:
        anchor = _v1_4_r1_anchor(design)
        historical_extraction = _mapping(
            anchor.get("extraction"),
            "historical v1.4-r1 extraction",
        )
        historical_shadow = copy.deepcopy(
            dict(
                _mapping(
                    historical_extraction.get("legacy_exact_shadow"),
                    "historical v1.4-r1 exact shadow",
                )
            )
        )
        historical_shadow.update(
            {
                "evidence_span_match_mode": EXACT_EVIDENCE_SPAN_MATCH_MODE,
                "diagnostic_only": True,
                "historical_round_immutable": True,
            }
        )
        result["v1_4_actual"] = copy.deepcopy(anchor)
        result["v1_4_r1_actual"] = copy.deepcopy(anchor)
        result["v1_4_legacy_exact_shadow"] = historical_shadow
    elif is_v1_6:
        v1_4_anchor = _v1_4_r1_anchor(design)
        v1_5_anchor = _v1_5_r1_anchor(design)
        v1_5_actual, v1_5_shadow = _v1_5_r1_report_layers(design)
        v1_4_extraction = _mapping(
            v1_4_anchor.get("extraction"),
            "historical v1.4-r1 extraction",
        )
        v1_4_shadow = copy.deepcopy(
            dict(
                _mapping(
                    v1_4_extraction.get("legacy_exact_shadow"),
                    "historical v1.4-r1 exact shadow",
                )
            )
        )
        v1_4_shadow.update(
            {
                "evidence_span_match_mode": EXACT_EVIDENCE_SPAN_MATCH_MODE,
                "diagnostic_only": True,
                "historical_round_immutable": True,
            }
        )
        result["v1_4_actual"] = copy.deepcopy(v1_4_anchor)
        result["v1_4_r1_actual"] = copy.deepcopy(v1_4_anchor)
        result["v1_4_legacy_exact_shadow"] = v1_4_shadow
        result["v1_5_actual"] = v1_5_actual
        result["v1_5_legacy_exact_shadow"] = v1_5_shadow
        result["v1_5_r1_actual"] = copy.deepcopy(v1_5_anchor)
    return result


def _symbol_diagnostics(
    *,
    design: EventEvaluationDesign,
    metrics: Mapping[str, Any],
    prediction_rows: Sequence[Mapping[str, Any]],
    labels: Mapping[int, Mapping[str, Any]],
) -> JsonObject:
    design_version = design.document.get("schema_version")
    if design_version not in {
        "p4.2a-evaluation-design-v1.3",
        "p4.2a-evaluation-design-v1.4",
        "p4.2a-evaluation-design-v1.5",
    }:
        raise DevIterationError("symbol diagnostic design version drifted")
    _, _, adjudication = _historical_comparison(design)
    defect_ids = {
        cast(int, identifier) for identifier in adjudication["ai_label_defect_ids"]
    }
    matches = 0
    mismatch_ids: list[int] = []
    excluded_ids: list[int] = []
    denominator = 0
    for row in prediction_rows:
        identifier = row.get("news_item_id")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or row.get("status") != "ok"
        ):
            continue
        if identifier in defect_ids:
            excluded_ids.append(identifier)
            continue
        label = labels.get(identifier)
        if label is None:
            raise DevIterationError("symbol diagnostics found an unknown ID")
        gold = _mapping(label.get("gold"), "AI-drafted dev gold")
        prediction = _mapping(row.get("prediction"), "dev prediction")
        denominator += 1
        if prediction.get("symbols") == gold.get("symbols"):
            matches += 1
        else:
            mismatch_ids.append(identifier)
    raw = dict(_mapping(metrics.get("symbol_exact_set"), "raw symbol metrics"))
    result: JsonObject = {
        "raw_gate": raw,
        "raw_gate_uses_frozen_ai_labels_unchanged": True,
        "ai_label_defect_ids": sorted(defect_ids),
        "adjusted_exact_set": {
            "diagnostic_only": True,
            "not_a_gate": True,
            "excluded_ai_label_defect_ids": sorted(excluded_ids),
            "matches": matches,
            "denominator": denominator,
            "agreement": _ratio(matches, denominator),
            "mismatch_ids": sorted(mismatch_ids),
        },
    }
    if design_version == "p4.2a-evaluation-design-v1.3":
        result["model_over_attribution_ids"] = list(
            MODEL_OVER_ATTRIBUTION_IDS
        )
    elif design_version == "p4.2a-evaluation-design-v1.4":
        anchor = _v1_4_r1_anchor(design)
        result["v1_4_r1_actual"] = copy.deepcopy(
            dict(
                _mapping(
                    _mapping(
                        anchor.get("metrics"),
                        "historical v1.4-r1 metrics",
                    ).get("symbol_adjudication"),
                    "historical v1.4-r1 symbol adjudication",
                )
            )
        )
    else:
        v1_4_anchor = _v1_4_r1_anchor(design)
        anchor = _v1_5_r1_anchor(design)
        result["v1_4_r1_actual"] = copy.deepcopy(
            dict(
                _mapping(
                    _mapping(
                        v1_4_anchor.get("metrics"),
                        "historical v1.4-r1 metrics",
                    ).get("symbol_adjudication"),
                    "historical v1.4-r1 symbol adjudication",
                )
            )
        )
        result["v1_5_r1_actual"] = copy.deepcopy(
            dict(
                _mapping(
                    _mapping(
                        anchor.get("metrics"),
                        "historical v1.5-r1 metrics",
                    ).get("symbol_adjudication"),
                    "historical v1.5-r1 symbol adjudication",
                )
            )
        )
    return result


def _validate_versioned_dev_report_fields(
    design: EventEvaluationDesign,
    report: Mapping[str, Any],
) -> None:
    evaluation = _mapping(design.document.get("evaluation"), "evaluation")
    required = evaluation.get("required_report_fields")
    if (
        not isinstance(required, list)
        or any(not isinstance(field, str) for field in required)
    ):
        raise DevIterationError("evaluation required report fields drifted")
    applicable = [
        field
        for field in required
        if field == "prediction_contract.evidence_span_match_mode"
        or field.startswith("evidence_validation.")
        or field.startswith("symbol_diagnostics.")
        or field in _V1_5_DEV_REPORT_FIELDS
    ]
    missing: list[str] = []
    for dotted_path in applicable:
        value: object = report
        for component in dotted_path.split("."):
            if not isinstance(value, Mapping) or component not in value:
                missing.append(dotted_path)
                break
            value = value[component]
    if missing:
        raise DevIterationError(
            f"dev report omits versioned required fields: {missing}"
        )


def _validate_versioned_dev_contract_preflight(
    design: EventEvaluationDesign,
    active_contract: EventExtractContract,
    round_id: str,
) -> None:
    design_version = design.document.get("schema_version")
    contract_version = active_contract.document.get("schema_version")
    expected_fields: frozenset[str]
    if design_version == "p4.2a-evaluation-design-v1.3":
        if contract_version != "p4.2a-event-extract-eval-v1.4":
            raise DevIterationError(
                "v1.3 evaluation design requires extraction contract v1.4"
            )
        expected_fields = _V1_3_DEV_REPORT_FIELDS
    elif design_version == "p4.2a-evaluation-design-v1.4":
        if contract_version != "p4.2a-event-extract-eval-v1.5":
            raise DevIterationError(
                "v1.4 evaluation design requires extraction contract v1.5"
            )
        if not round_id.startswith("v1.5-"):
            raise DevIterationError(
                "extraction contract v1.5 requires a v1.5-* round_id"
            )
        expected_fields = _V1_3_DEV_REPORT_FIELDS | _V1_4_DEV_REPORT_FIELDS
    elif design_version == "p4.2a-evaluation-design-v1.5":
        if contract_version != "p4.2a-event-extract-eval-v1.6":
            raise DevIterationError(
                "v1.5 evaluation design requires extraction contract v1.6"
            )
        if not round_id.startswith("v1.6-"):
            raise DevIterationError(
                "extraction contract v1.6 requires a v1.6-* round_id"
            )
        expected_fields = (
            _V1_3_DEV_REPORT_FIELDS
            | _V1_4_DEV_REPORT_FIELDS
            | _V1_5_DEV_REPORT_FIELDS
        )
    else:
        return

    evaluation = _mapping(design.document.get("evaluation"), "evaluation")
    required = evaluation.get("required_report_fields")
    if (
        not isinstance(required, list)
        or any(not isinstance(field, str) for field in required)
    ):
        raise DevIterationError("evaluation required report fields drifted")
    versioned = frozenset(
        field
        for field in required
        if field == "prediction_contract.evidence_span_match_mode"
        or field.startswith("evidence_validation.")
        or field.startswith("symbol_diagnostics.")
        or field in _V1_5_DEV_REPORT_FIELDS
    )
    if versioned != expected_fields:
        raise DevIterationError(
            "evaluation versioned required report fields are unrecognized"
        )


def run_dev_iteration(
    active_contract_path: Path,
    round_id: str,
    *,
    project_root: Path = PROJECT_ROOT,
    design: EventEvaluationDesign | None = None,
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
    chat_json_fn: ChatJsonCallable | None = None,
) -> DevIterationResult:
    """Run one immutable dev60-only prompt iteration without touching held-out."""

    root = project_root.resolve()
    active_clock = clock or (lambda: datetime.now(UTC))
    started_at = _utc_now(active_clock)
    active_design = design or load_event_evaluation_design(
        DEFAULT_EVALUATION_DESIGN_PATH,
        project_root=root,
    )
    active_contract = heldout._load_active_contract(
        active_design,
        root,
        active_contract_path,
    )
    _validate_versioned_dev_contract_preflight(
        active_design,
        active_contract,
        round_id,
    )
    heldout._ensure_dev_final_precedes_heldout(active_design, root)
    if _parse_utc(started_at, "started_at_utc") < _parse_utc(
        active_contract.document.get("pre_registered_at"),
        "active contract pre_registered_at",
    ):
        raise DevIterationError("dev iteration predates the active contract")
    input_rows, records = heldout._dev_final_inputs(
        active_design,
        active_contract,
        root,
    )
    dev_entry = _mapping(
        _mapping(active_design.document.get("artifacts"), "artifacts").get(
            "dev_60_frozen_jsonl"
        ),
        "dev60 artifact",
    )
    if dev_entry.get("sha256") != DEV_INPUT_SHA256:
        raise DevIterationError("frozen dev60 input SHA-256 drifted")
    labels, labels_path = _load_dev_labels(root, input_rows)
    p4_1_config = (root / P4_1_CONFIG_PATH).resolve()
    if (
        p4_1_config.is_symlink()
        or not p4_1_config.is_file()
        or heldout._sha256_file(p4_1_config) != P4_1_CONFIG_SHA256
    ):
        raise DevIterationError("P4.1 frozen config SHA-256 drifted")

    predictions_path, manifest_path, report_path = _artifact_paths(root, round_id)
    for path in (predictions_path, manifest_path, report_path):
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to overwrite create-only artifact: {path}")

    active_settings = settings or _settings_from_project_env(root)
    process_safety = heldout._settings_safety(active_settings)
    _validate_runtime_contract(active_contract, active_settings)
    universe, database, database_safety = heldout._load_dev_universe(
        active_contract,
        root,
    )
    if (
        database.sqlite_uri_mode != "ro"
        or database.pragma_query_only != 1
        or database.total_changes != 0
    ):
        raise DevIterationError("dev iteration database was not read-only")

    summary = extract_records(
        active_contract,
        records,
        output_path=predictions_path,
        eval_root=(root / "docs/phase4/eval").resolve(),
        universe_symbols=universe,
        settings=active_settings,
        retry_failures=False,
        chat_json_fn=chat_json_fn,
    )
    prediction_rows = heldout._load_jsonl(predictions_path, "dev iteration predictions")
    success_count, _, _ = heldout._validate_prediction_rows(
        active_contract,
        input_rows,
        records,
        prediction_rows,
        universe,
    )
    if (
        len(prediction_rows) != 60
        or summary.expected_count != 60
        or summary.output_line_count != 60
        or summary.success_count != success_count
        or summary.failure_count != 60 - success_count
    ):
        raise DevIterationError("dev iteration coverage drifted")
    completed_at = _utc_now(active_clock)
    if _parse_utc(completed_at, "completed_at_utc") < _parse_utc(
        started_at, "started_at_utc"
    ):
        raise DevIterationError("dev iteration completion clock moved backwards")

    contract_files = _mapping(active_contract.document.get("contract_files"), "files")
    prompt = _mapping(contract_files.get("prompt"), "prompt")
    schema = _mapping(contract_files.get("schema"), "schema")
    candidate_selection = active_contract.evidence_candidate_selection
    active_registration = (
        _mapping(
            active_design.document.get("active_prediction_contract"),
            "active prediction contract",
        )
        if candidate_selection
        else {}
    )
    materialized_schema = (
        _mapping(
            contract_files.get("materialized_schema"),
            "materialized result schema",
        )
        if candidate_selection
        else schema
    )
    input_identity = (
        _candidate_input_identity(prediction_rows)
        if candidate_selection
        else None
    )
    manifest: JsonObject = {
        "schema_version": "p4.2a-dev-iteration-manifest-v1",
        "round_id": round_id,
        "started_at_utc": started_at,
        "completed_at_utc": completed_at,
        "design_sha256": active_design.sha256,
        "active_contract_path": active_contract.path.relative_to(root).as_posix(),
        "active_contract_sha256": active_contract.sha256,
        "prompt_path": prompt.get("path"),
        "prompt_sha256": prompt.get("sha256"),
        "result_schema_path": schema.get("path"),
        "result_schema_sha256": schema.get("sha256"),
        "model": active_contract.model,
        "endpoint": active_contract.endpoint,
        "request_contract": {
            "temperature": active_contract.document["llm"]["temperature"],
            "enable_thinking": active_contract.document["llm"]["enable_thinking"],
            "max_output_tokens": active_contract.max_tokens,
            "total_deadline_seconds": active_contract.timeout,
            "max_retries": active_contract.max_retries,
            "explicit_cache": active_contract.document["llm"].get(
                "explicit_cache"
            ),
            "evidence_span_match_mode": active_contract.evidence_span_match_mode,
        },
        "p4_1_frozen_config": {
            "path": P4_1_CONFIG_PATH.as_posix(),
            "sha256": P4_1_CONFIG_SHA256,
        },
        "dev_inputs_sha256": DEV_INPUT_SHA256,
        "dev_labels_path": labels_path.relative_to(root).as_posix(),
        "dev_labels_sha256": DEV_LABELS_SHA256,
        "predictions_path": predictions_path.relative_to(root).as_posix(),
        "predictions_sha256": heldout._sha256_file(predictions_path),
        "ordered_identity_sha256": heldout._dev_prediction_identity_sha256(
            prediction_rows
        ),
        "extraction": _summary_evidence(summary),
        "process_safety": process_safety,
        "database_evidence": {
            "relative_path": database.relative_path,
            "sqlite_uri_mode": database.sqlite_uri_mode,
            "pragma_query_only": database.pragma_query_only,
            "total_changes": database.total_changes,
            "required_tables_found": list(database.required_tables_found),
            **database_safety,
        },
        "heldout_accessed": False,
        "production_writes": 0,
    }
    if candidate_selection:
        manifest["input_representation"] = copy.deepcopy(
            dict(
                _mapping(
                    active_registration.get("input_representation"),
                    "registered input representation",
                )
            )
        )
        manifest["model_result_schema"] = dict(schema)
        manifest["materialized_result_schema"] = dict(materialized_schema)
        manifest["candidate_materialization"] = copy.deepcopy(
            dict(
                _mapping(
                    active_registration.get("candidate_materialization"),
                    "registered candidate materialization",
                )
            )
        )
        manifest["input_identity"] = input_identity
    eval_root = (root / "docs/phase4/eval").resolve()
    heldout._create_only_bytes(
        manifest_path,
        heldout._canonical_json_bytes(manifest),
        eval_root,
    )
    metrics = _score_predictions(prediction_rows, labels)
    is_versioned_evidence_design = active_design.document.get(
        "schema_version"
    ) in {
        "p4.2a-evaluation-design-v1.3",
        "p4.2a-evaluation-design-v1.4",
        "p4.2a-evaluation-design-v1.5",
    }
    report: JsonObject = {
        "schema_version": "p4.2a-dev-model-interagreement-report-v1",
        "round_id": round_id,
        "recorded_at_utc": completed_at,
        "manifest_path": manifest_path.relative_to(root).as_posix(),
        "manifest_sha256": heldout._sha256_file(manifest_path),
        "predictions_sha256": manifest["predictions_sha256"],
        "dev_inputs_sha256": DEV_INPUT_SHA256,
        "dev_labels_sha256": DEV_LABELS_SHA256,
        "dev_labeler": DEV_LABELER,
        "prediction_contract": {
            "path": active_contract.path.relative_to(root).as_posix(),
            "sha256": active_contract.sha256,
            "model": active_contract.model,
            "endpoint": active_contract.endpoint,
            "prompt_path": prompt.get("path"),
            "prompt_sha256": prompt.get("sha256"),
            "evidence_span_match_mode": active_contract.evidence_span_match_mode,
        },
        "metrics": metrics,
        "formal_dev_round_valid": (
            summary.success_count == 60 and summary.failure_count == 0
        ),
        "runtime_evidence": _runtime_evidence(prediction_rows),
        "flash_baseline_comparison": _comparison_evidence(
            metrics,
            candidate_model=active_contract.model,
            candidate_endpoint=active_contract.endpoint,
            candidate_prompt_sha256=prompt.get("sha256"),
            candidate_contract_schema_version=active_contract.document.get(
                "schema_version"
            ),
            candidate_evidence_span_match_mode=(
                active_contract.evidence_span_match_mode
            ),
            candidate_selection=candidate_selection,
        ),
        "heldout_accessed": False,
        "heldout_phase_unlocked": False,
    }
    if candidate_selection:
        prediction_contract_report = cast(
            JsonObject,
            report["prediction_contract"],
        )
        prediction_contract_report["input_representation"] = copy.deepcopy(
            dict(
                _mapping(
                    active_registration.get("input_representation"),
                    "registered input representation",
                )
            )
        )
        prediction_contract_report["model_result_schema"] = dict(schema)
        prediction_contract_report["materialized_result_schema"] = dict(
            materialized_schema
        )
        prediction_contract_report["candidate_materialization"] = copy.deepcopy(
            dict(
                _mapping(
                    active_registration.get("candidate_materialization"),
                    "registered candidate materialization",
                )
            )
        )
        report["input_identity"] = input_identity
        comparison = _mapping(
            report["flash_baseline_comparison"],
            "flash baseline comparison",
        )
        report["comparison_disclosure"] = {
            "changed_dimensions": list(
                cast(Sequence[str], comparison.get("changed_dimensions"))
            ),
            "causal_reading_forbidden": True,
            "interpretation": (
                "indicative_only_not_single_variable_causality"
            ),
        }
    if is_versioned_evidence_design:
        report["evidence_validation"] = _evidence_validation(
            design=active_design,
            active_contract=active_contract,
            summary=summary,
            prediction_rows=prediction_rows,
            labels=labels,
        )
        report["symbol_diagnostics"] = _symbol_diagnostics(
            design=active_design,
            metrics=metrics,
            prediction_rows=prediction_rows,
            labels=labels,
        )
        _validate_versioned_dev_report_fields(active_design, report)
    heldout._create_only_bytes(
        report_path,
        heldout._canonical_json_bytes(report),
        eval_root,
    )
    return DevIterationResult(
        summary=summary,
        predictions_path=predictions_path,
        manifest_path=manifest_path,
        report_path=report_path,
        report=report,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one immutable P4.2a dev60-only prompt iteration."
    )
    parser.add_argument("--active-contract", type=Path, required=True)
    parser.add_argument(
        "--evaluation-design",
        type=Path,
        default=DEFAULT_EVALUATION_DESIGN_PATH,
    )
    parser.add_argument("--round-id", required=True)
    return parser


def _safe_error(error: BaseException) -> str:
    if isinstance(error, FileExistsError):
        return "create_only_artifact_exists"
    if isinstance(error, DevIterationError):
        return str(error)
    return type(error).__name__


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        design = load_event_evaluation_design(
            cast(Path, arguments.evaluation_design),
            project_root=PROJECT_ROOT,
        )
        result = run_dev_iteration(
            cast(Path, arguments.active_contract),
            cast(str, arguments.round_id),
            design=design,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        print(
            json.dumps(
                {"status": "error", "error": _safe_error(error)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    metrics = cast(Mapping[str, Any], result.report["metrics"])
    print(
        json.dumps(
            {
                "status": "completed",
                "development_ready_to_freeze": metrics.get(
                    "development_ready_to_freeze"
                ),
                "predictions_path": result.predictions_path.as_posix(),
                "manifest_path": result.manifest_path.as_posix(),
                "report_path": result.report_path.as_posix(),
                "metrics": metrics,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
