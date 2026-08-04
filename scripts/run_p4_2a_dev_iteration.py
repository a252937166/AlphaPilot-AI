from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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


def _comparison_evidence(
    metrics: Mapping[str, Any],
    *,
    candidate_model: str,
    candidate_endpoint: str | None,
    candidate_prompt_sha256: object,
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
            )
            if changed
        ],
    }


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
    eval_root = (root / "docs/phase4/eval").resolve()
    heldout._create_only_bytes(
        manifest_path,
        heldout._canonical_json_bytes(manifest),
        eval_root,
    )
    metrics = _score_predictions(prediction_rows, labels)
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
        ),
        "heldout_accessed": False,
        "heldout_phase_unlocked": False,
    }
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
