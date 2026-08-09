from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if __package__ in {None, ""}:
    # Keep direct CLI imports anchored to this checkout, independent of cwd and
    # without requiring a caller-provided PYTHONPATH.
    sys.path[:0] = [str(PROJECT_ROOT), str(PROJECT_ROOT / "src")]

from scripts import run_p4_2a_dev_iteration as dev_runner  # noqa: E402
from scripts import run_p4_2a_heldout_predictions as heldout  # noqa: E402
from scripts.run_p4_2a_offline_extract import ChatJsonCallable  # noqa: E402

from alphapilot.core.config import Settings  # noqa: E402
from alphapilot.llm.p4_news_eval import (  # noqa: E402
    EventEvaluationDesign,
    load_event_evaluation_design,
)

JsonObject = dict[str, Any]
EVALUATION_DESIGN_PATH = Path("config/p4_event_evaluation_v1_6.yaml")
ACTIVE_CONTRACT_PATH = Path("config/p4_event_extract_eval_v1_7.yaml")
OFFICIAL_ROUND_ID = "v1.7-r1"
EXPECTED_DESIGN_SCHEMA = "p4.2a-evaluation-design-v1.6"
EXPECTED_CONTRACT_SCHEMA = "p4.2a-event-extract-eval-v1.7"
EXPECTED_CANDIDATE_MODEL = "qwen3.7-flash"
EXPECTED_INCUMBENT_MODEL = "qwen3.6-plus"
EXPECTED_DEADLINE_UTC = datetime(2026, 8, 5, 16, 10, tzinfo=UTC)
EXPECTED_INCUMBENT_RECEIPT_SHA256 = (
    "9adab49b5b5e8d0bf942a591878c1718fc3d158f5638144db7c5cb80b1e63f68"
)
OUTCOME_SCHEMA_VERSION = "p4.2a-model-selection-outcome-v1"
SHA256_LENGTH = 64


class ModelSelectionError(RuntimeError):
    """The pre-registered v1.7 one-shot model selection failed closed."""


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelSelectionError(f"{name} must be a mapping")
    return cast(Mapping[str, Any], value)


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ModelSelectionError(f"{name} must be a sequence")
    return cast(Sequence[object], value)


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ModelSelectionError(f"{name} must be a non-blank timestamp")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ModelSelectionError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ModelSelectionError(f"{name} must include a timezone")
    return parsed.astimezone(UTC)


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ModelSelectionError("clock must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _project_file(
    root: Path,
    value: object,
    name: str,
    *,
    must_exist: bool = True,
) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ModelSelectionError(f"{name} path must be non-blank")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ModelSelectionError(f"{name} path must remain project-relative")
    resolved_root = root.resolve()
    path = (resolved_root / relative).resolve()
    if not path.is_relative_to(resolved_root):
        raise ModelSelectionError(f"{name} path escapes the project root")
    if must_exist and (
        path.is_symlink() or not path.is_file()
    ):
        raise ModelSelectionError(f"{name} must be one regular file")
    return path


def _artifact_identity(
    root: Path,
    entry: Mapping[str, Any],
    name: str,
) -> JsonObject:
    path = _project_file(root, entry.get("path"), name)
    expected_sha256 = entry.get("sha256")
    if not _valid_sha256(expected_sha256):
        raise ModelSelectionError(f"{name} registered SHA-256 is invalid")
    actual_sha256 = _sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise ModelSelectionError(f"{name} differs from its registered SHA-256")
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": actual_sha256,
    }


def _output_path(
    design: EventEvaluationDesign,
    root: Path,
    artifact_name: str,
) -> Path:
    artifacts = _mapping(design.document.get("artifacts"), "artifacts")
    entry = _mapping(artifacts.get(artifact_name), artifact_name)
    if entry.get("create_only") is not True:
        raise ModelSelectionError(f"{artifact_name} must be create-only")
    return _project_file(
        root,
        entry.get("path"),
        artifact_name,
        must_exist=False,
    )


def _load_json(path: Path, name: str) -> JsonObject:
    try:
        value: object = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelSelectionError(f"{name} is not valid JSON") from exc
    return dict(_mapping(value, name))


def _load_jsonl(path: Path, name: str) -> list[JsonObject]:
    rows: list[JsonObject] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(dict(_mapping(json.loads(line), f"{name} row")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelSelectionError(f"{name} is not valid JSONL") from exc
    return rows


def _selection_preflight(
    design: EventEvaluationDesign,
    root: Path,
) -> None:
    helper = getattr(
        heldout,
        "_ensure_v1_7_model_selection_preparation_is_safe",
        None,
    )
    if not callable(helper):
        raise ModelSelectionError(
            "v1.7 narrow incumbent-receipt preflight is unavailable"
        )
    helper(design, root)


def _validate_design(
    design: EventEvaluationDesign,
    root: Path,
) -> tuple[Mapping[str, Any], JsonObject, JsonObject]:
    if (
        design.document.get("schema_version") != EXPECTED_DESIGN_SCHEMA
        or design.path.resolve() != (root / EVALUATION_DESIGN_PATH).resolve()
        or _sha256_file(design.path) != design.sha256
    ):
        raise ModelSelectionError("v1.7 evaluation design identity drifted")
    selection = _mapping(design.document.get("model_selection"), "model_selection")
    deadline = _parse_timestamp(selection.get("deadline_utc"), "selection deadline")
    if (
        selection.get("official_round_id") != OFFICIAL_ROUND_ID
        or deadline != EXPECTED_DEADLINE_UTC
        or selection.get("single_formal_round") is not True
        or selection.get("third_model_forbidden") is not True
        or selection.get("relative_score_selection_forbidden") is not True
        or selection.get("selection_rule")
        != "candidate_if_all_absolute_gates_pass_else_retain_incumbent"
        or selection.get("monthly_calls") != 15_000
    ):
        raise ModelSelectionError("v1.7 model-selection policy drifted")

    candidate = _mapping(selection.get("candidate"), "candidate")
    candidate_contract = _mapping(candidate.get("contract"), "candidate contract")
    active_registration = _mapping(
        design.document.get("active_prediction_contract"),
        "active_prediction_contract",
    )
    contract_identity = _artifact_identity(
        root,
        candidate_contract,
        "candidate contract",
    )
    if (
        candidate.get("model") != EXPECTED_CANDIDATE_MODEL
        or candidate_contract != _mapping(
            {
                "path": active_registration.get("path"),
                "sha256": active_registration.get("sha256"),
            },
            "active registration identity",
        )
        or active_registration.get("schema_version") != EXPECTED_CONTRACT_SCHEMA
        or active_registration.get("model") != EXPECTED_CANDIDATE_MODEL
        or contract_identity["path"] != ACTIVE_CONTRACT_PATH.as_posix()
    ):
        raise ModelSelectionError("candidate contract registration drifted")

    incumbent = _mapping(selection.get("incumbent"), "incumbent")
    incumbent_identities: JsonObject = {}
    for key in (
        "design",
        "contract",
        "dev_predictions",
        "dev_manifest",
        "dev_report",
        "dev_final_predictions",
        "dev_final_manifest",
        "freeze_receipt",
    ):
        incumbent_identities[key] = _artifact_identity(
            root,
            _mapping(incumbent.get(key), f"incumbent {key}"),
            f"incumbent {key}",
        )
    if (
        incumbent_identities["freeze_receipt"]["sha256"]
        != EXPECTED_INCUMBENT_RECEIPT_SHA256
    ):
        raise ModelSelectionError("incumbent plus freeze receipt drifted")

    gates = _mapping(selection.get("gates"), "selection gates")
    materiality = _mapping(gates.get("materiality"), "materiality gate")
    symbol = _mapping(gates.get("symbol"), "symbol gate")
    if (
        gates.get("success_count") != 60
        or gates.get("failure_count") != 0
        or gates.get("raw_unrounded") is not True
        or materiality.get("formula") != "tp_divided_by_tp_plus_fp"
        or float(cast(float, materiality.get("minimum"))) != 0.80
        or materiality.get("zero_predicted_positive_policy") != "fail"
        or materiality.get("failed_reference_positive_count") != 0
        or symbol.get("formula") != "exact_set_match_accuracy"
        or symbol.get("denominator") != 60
        or float(cast(float, symbol.get("minimum"))) != 0.95
    ):
        raise ModelSelectionError("v1.7 absolute gates drifted")

    pricing = _mapping(
        selection.get("pricing_cny_per_million"),
        "selection pricing",
    )
    plus = _mapping(pricing.get("plus"), "plus pricing")
    flash = _mapping(pricing.get("flash"), "flash pricing")
    if (
        plus != {"input": 2.0, "output": 12.0}
        or flash != {"input": 0.2, "output": 0.8}
    ):
        raise ModelSelectionError("v1.7 pricing registration drifted")
    return selection, contract_identity, incumbent_identities


def _failure(row: Mapping[str, Any]) -> JsonObject | None:
    if row.get("status") == "ok":
        return None
    failure = row.get("extract_failed")
    safe: JsonObject = {"reason": row.get("error")}
    if isinstance(failure, Mapping):
        for key in ("reason", "field", "constraint"):
            if key in failure:
                safe[key] = failure.get(key)
    return safe


def _runtime(row: Mapping[str, Any]) -> JsonObject:
    tokens = row.get("tokens")
    token_mapping = tokens if isinstance(tokens, Mapping) else {}
    return {
        "latency_ms": row.get("latency_ms"),
        "prompt_tokens": token_mapping.get("prompt_tokens"),
        "completion_tokens": token_mapping.get("completion_tokens"),
    }


def _model_item(row: Mapping[str, Any]) -> JsonObject:
    prediction = row.get("prediction")
    output: JsonObject | None = None
    if isinstance(prediction, Mapping):
        output = {
            "materiality": prediction.get("materiality"),
            "symbols": prediction.get("symbols"),
            "event_type": prediction.get("event_type"),
            "direction": prediction.get("direction"),
            "evidence_span": prediction.get("evidence_span"),
        }
    return {
        "status": row.get("status"),
        "failure": _failure(row),
        "input_identity": {
            "declared_frozen_input_sha256": row.get("declared_input_sha256"),
            "active_model_input_sha256": row.get("input_sha256"),
            "text_sha256": row.get("text_sha256"),
        },
        "output": output,
        "runtime": _runtime(row),
    }


def _gold_item(label: Mapping[str, Any]) -> JsonObject:
    gold = _mapping(label.get("gold"), "dev reference label")
    return {
        "materiality": gold.get("materiality"),
        "symbols": gold.get("symbols"),
        "event_type": gold.get("event_type"),
        "direction": gold.get("direction"),
        "evidence_span": gold.get("evidence_span"),
    }


def _without_unicode_whitespace(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return "".join(character for character in value if not character.isspace())


def _comparison_flags(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> JsonObject:
    left_output = left.get("output")
    right_output = right.get("output")
    if not isinstance(left_output, Mapping) or not isinstance(right_output, Mapping):
        return {
            "comparable": False,
            "materiality_exact": False,
            "symbols_exact_set": False,
            "event_type_exact": False,
            "direction_exact": False,
            "evidence_exact": False,
            "evidence_whitespace_normalized": False,
        }
    return {
        "comparable": True,
        "materiality_exact": left_output.get("materiality")
        == right_output.get("materiality"),
        "symbols_exact_set": left_output.get("symbols")
        == right_output.get("symbols"),
        "event_type_exact": left_output.get("event_type")
        == right_output.get("event_type"),
        "direction_exact": left_output.get("direction")
        == right_output.get("direction"),
        "evidence_exact": left_output.get("evidence_span")
        == right_output.get("evidence_span"),
        "evidence_whitespace_normalized": _without_unicode_whitespace(
            left_output.get("evidence_span")
        )
        == _without_unicode_whitespace(right_output.get("evidence_span")),
    }


def _reference_flags(
    model: Mapping[str, Any],
    reference: Mapping[str, Any],
) -> JsonObject:
    model_output = model.get("output")
    if not isinstance(model_output, Mapping):
        return {
            "comparable": False,
            "materiality_exact": False,
            "symbols_exact_set": False,
            "event_type_exact": False,
            "direction_exact": False,
            "evidence_exact": False,
            "evidence_whitespace_normalized": False,
        }
    wrapped_reference = {"output": reference}
    return _comparison_flags(model, wrapped_reference)


def _indexed_rows(
    rows: Sequence[Mapping[str, Any]],
    name: str,
    *,
    expected_model: str,
    expected_contract_sha256: str,
) -> dict[int, JsonObject]:
    indexed: dict[int, JsonObject] = {}
    for row in rows:
        identifier = row.get("news_item_id")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier in indexed
        ):
            raise ModelSelectionError(f"{name} has invalid or duplicate IDs")
        for field in (
            "input_sha256",
            "declared_input_sha256",
            "text_sha256",
        ):
            if not _valid_sha256(row.get(field)):
                raise ModelSelectionError(f"{name} row {identifier} has invalid {field}")
        if (
            row.get("model") != expected_model
            or row.get("contract_sha256") != expected_contract_sha256
            or row.get("status") not in {"ok", "extract_failed"}
        ):
            raise ModelSelectionError(
                f"{name} row {identifier} model, contract, or status drifted"
            )
        indexed[identifier] = dict(row)
    if len(indexed) != 60:
        raise ModelSelectionError(f"{name} must contain exactly 60 IDs")
    return indexed


def _validate_formal_bindings(
    *,
    design: EventEvaluationDesign,
    contract_identity: Mapping[str, Any],
    formal_identities: Mapping[str, Any],
    manifest: Mapping[str, Any],
    report: Mapping[str, Any],
) -> None:
    predictions_identity = _mapping(
        formal_identities.get("predictions"),
        "formal predictions identity",
    )
    manifest_identity = _mapping(
        formal_identities.get("manifest"),
        "formal manifest identity",
    )
    if (
        manifest.get("round_id") != OFFICIAL_ROUND_ID
        or manifest.get("design_sha256") != design.sha256
        or manifest.get("active_contract_path") != contract_identity.get("path")
        or manifest.get("active_contract_sha256") != contract_identity.get("sha256")
        or manifest.get("model") != EXPECTED_CANDIDATE_MODEL
        or manifest.get("predictions_sha256")
        != predictions_identity.get("sha256")
        or manifest.get("heldout_accessed") is not False
        or manifest.get("production_writes") != 0
    ):
        raise ModelSelectionError("formal candidate manifest binding drifted")
    if (
        report.get("round_id") != OFFICIAL_ROUND_ID
        or report.get("manifest_sha256") != manifest_identity.get("sha256")
        or report.get("predictions_sha256")
        != predictions_identity.get("sha256")
        or report.get("heldout_accessed") is not False
        or report.get("heldout_phase_unlocked") is not False
    ):
        raise ModelSelectionError("formal candidate report binding drifted")
    report_contract = _mapping(
        report.get("prediction_contract"),
        "formal report prediction contract",
    )
    if (
        report_contract.get("path") != contract_identity.get("path")
        or report_contract.get("sha256") != contract_identity.get("sha256")
        or report_contract.get("model") != EXPECTED_CANDIDATE_MODEL
    ):
        raise ModelSelectionError("formal candidate report contract drifted")


def _diagnostics(
    rows: Mapping[int, Mapping[str, Any]],
    labels: Mapping[int, Mapping[str, Any]],
) -> JsonObject:
    tp = fp = fn = tn = 0
    failures: list[int] = []
    failed_reference_positive: list[int] = []
    exact_counts = {
        "materiality": 0,
        "symbols": 0,
        "event_type": 0,
        "direction": 0,
        "evidence": 0,
        "evidence_whitespace_normalized": 0,
    }
    latency_values: list[int] = []
    prompt_total = 0
    completion_total = 0
    token_rows = 0
    for identifier in sorted(rows):
        row = rows[identifier]
        gold = _mapping(labels[identifier].get("gold"), "dev gold")
        latency = row.get("latency_ms")
        tokens = row.get("tokens")
        if isinstance(latency, int) and not isinstance(latency, bool):
            latency_values.append(latency)
        if isinstance(tokens, Mapping):
            prompt = tokens.get("prompt_tokens")
            completion = tokens.get("completion_tokens")
            if (
                isinstance(prompt, int)
                and not isinstance(prompt, bool)
                and isinstance(completion, int)
                and not isinstance(completion, bool)
            ):
                prompt_total += prompt
                completion_total += completion
                token_rows += 1
        if row.get("status") != "ok":
            failures.append(identifier)
            if cast(int, gold.get("materiality")) >= 2:
                failed_reference_positive.append(identifier)
            continue
        prediction = _mapping(row.get("prediction"), "successful prediction")
        predicted_positive = cast(int, prediction.get("materiality")) >= 2
        reference_positive = cast(int, gold.get("materiality")) >= 2
        if predicted_positive and reference_positive:
            tp += 1
        elif predicted_positive:
            fp += 1
        elif reference_positive:
            fn += 1
        else:
            tn += 1
        pairs = {
            "materiality": prediction.get("materiality") == gold.get("materiality"),
            "symbols": prediction.get("symbols") == gold.get("symbols"),
            "event_type": prediction.get("event_type") == gold.get("event_type"),
            "direction": prediction.get("direction") == gold.get("direction"),
            "evidence": prediction.get("evidence_span") == gold.get("evidence_span"),
            "evidence_whitespace_normalized": _without_unicode_whitespace(
                prediction.get("evidence_span")
            )
            == _without_unicode_whitespace(gold.get("evidence_span")),
        }
        for key, matches in pairs.items():
            if matches:
                exact_counts[key] += 1
    predicted_positive_count = tp + fp
    materiality_agreement = (
        tp / predicted_positive_count if predicted_positive_count else None
    )
    success_count = 60 - len(failures)
    return {
        "success_count": success_count,
        "failure_count": len(failures),
        "failure_ids": failures,
        "failed_reference_positive_count": len(failed_reference_positive),
        "failed_reference_positive_ids": failed_reference_positive,
        "materiality_positive": {
            "formula": "tp_divided_by_tp_plus_fp",
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "predicted_positive_count": predicted_positive_count,
            "agreement": materiality_agreement,
        },
        "symbol_exact_set": {
            "matches": exact_counts["symbols"],
            "denominator": 60,
            "agreement": exact_counts["symbols"] / 60,
        },
        "diagnostic_exact_agreement": {
            key: {
                "matches": value,
                "denominator": 60,
                "agreement": value / 60,
            }
            for key, value in exact_counts.items()
        },
        "runtime": {
            "latency_rows": len(latency_values),
            "latency_ms_min": min(latency_values) if latency_values else None,
            "latency_ms_max": max(latency_values) if latency_values else None,
            "latency_ms_mean": (
                sum(latency_values) / len(latency_values)
                if latency_values
                else None
            ),
            "token_rows": token_rows,
            "prompt_tokens_total": prompt_total,
            "completion_tokens_total": completion_total,
            "prompt_tokens_mean": (
                prompt_total / token_rows if token_rows else None
            ),
            "completion_tokens_mean": (
                completion_total / token_rows if token_rows else None
            ),
        },
    }


def _pairwise_aggregate(per_item: Sequence[Mapping[str, Any]]) -> JsonObject:
    keys = (
        "materiality_exact",
        "symbols_exact_set",
        "event_type_exact",
        "direction_exact",
        "evidence_exact",
        "evidence_whitespace_normalized",
    )
    comparable = 0
    matches = {key: 0 for key in keys}
    for item in per_item:
        flags = _mapping(item.get("candidate_vs_incumbent"), "pairwise flags")
        if flags.get("comparable") is True:
            comparable += 1
        for key in keys:
            if flags.get(key) is True:
                matches[key] += 1
    return {
        "comparable_count": comparable,
        **{
            key: {
                "matches": count,
                "denominator": 60,
                "agreement": count / 60,
            }
            for key, count in matches.items()
        },
    }


def _cost(
    diagnostics: Mapping[str, Any],
    rates: Mapping[str, Any],
    monthly_calls: int,
) -> JsonObject:
    runtime = _mapping(diagnostics.get("runtime"), "runtime")
    prompt_tokens = cast(int, runtime.get("prompt_tokens_total"))
    completion_tokens = cast(int, runtime.get("completion_tokens_total"))
    input_rate = float(cast(float, rates.get("input")))
    output_rate = float(cast(float, rates.get("output")))
    input_cost = prompt_tokens * input_rate / 1_000_000
    output_cost = completion_tokens * output_rate / 1_000_000
    round_cost = input_cost + output_cost
    return {
        "currency": "CNY",
        "rate_per_million_tokens": {
            "input": input_rate,
            "output": output_rate,
        },
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "input_cost": input_cost,
        "output_cost": output_cost,
        "round_cost": round_cost,
        "monthly_calls": monthly_calls,
        "projected_monthly_cost": round_cost * monthly_calls / 60,
    }


def _gate_evidence(
    candidate: Mapping[str, Any],
    selection: Mapping[str, Any],
    *,
    within_deadline: bool,
) -> JsonObject:
    registered = _mapping(selection.get("gates"), "selection gates")
    materiality_gate = _mapping(registered.get("materiality"), "materiality gate")
    symbol_gate = _mapping(registered.get("symbol"), "symbol gate")
    materiality = _mapping(
        candidate.get("materiality_positive"),
        "candidate materiality",
    )
    symbol = _mapping(candidate.get("symbol_exact_set"), "candidate symbols")
    agreement = materiality.get("agreement")
    predicted_positive_count = materiality.get("predicted_positive_count")
    materiality_passed = (
        isinstance(predicted_positive_count, int)
        and not isinstance(predicted_positive_count, bool)
        and predicted_positive_count > 0
        and isinstance(agreement, (int, float))
        and not isinstance(agreement, bool)
        and float(agreement) >= float(cast(float, materiality_gate.get("minimum")))
        and candidate.get("failed_reference_positive_count")
        == materiality_gate.get("failed_reference_positive_count")
    )
    symbol_agreement = symbol.get("agreement")
    symbol_passed = (
        symbol.get("denominator") == symbol_gate.get("denominator")
        and isinstance(symbol_agreement, (int, float))
        and not isinstance(symbol_agreement, bool)
        and float(symbol_agreement) >= float(cast(float, symbol_gate.get("minimum")))
    )
    coverage_passed = (
        candidate.get("success_count") == registered.get("success_count")
        and candidate.get("failure_count") == registered.get("failure_count")
    )
    return {
        "within_deadline": {
            "passed": within_deadline,
            "deadline_utc": selection.get("deadline_utc"),
        },
        "coverage": {
            "passed": coverage_passed,
            "required_success_count": registered.get("success_count"),
            "required_failure_count": registered.get("failure_count"),
            "actual_success_count": candidate.get("success_count"),
            "actual_failure_count": candidate.get("failure_count"),
        },
        "materiality_positive": {
            "passed": materiality_passed,
            "formula": materiality_gate.get("formula"),
            "minimum": materiality_gate.get("minimum"),
            "zero_predicted_positive_policy": materiality_gate.get(
                "zero_predicted_positive_policy"
            ),
            "actual": dict(materiality),
        },
        "symbol_exact_set": {
            "passed": symbol_passed,
            "formula": symbol_gate.get("formula"),
            "minimum": symbol_gate.get("minimum"),
            "actual": dict(symbol),
        },
        "all_passed": (
            within_deadline
            and coverage_passed
            and materiality_passed
            and symbol_passed
        ),
    }


def _promote_formal_predictions(
    *,
    design: EventEvaluationDesign,
    root: Path,
    formal_predictions_path: Path,
    formal_manifest_path: Path,
    contract_sha256: str,
) -> tuple[Path, Path]:
    predictions = _load_jsonl(formal_predictions_path, "formal candidate predictions")
    if len(predictions) != 60:
        raise ModelSelectionError("formal candidate predictions must contain 60 rows")
    formal_manifest = _load_json(formal_manifest_path, "formal candidate manifest")
    predictions_path = _output_path(
        design,
        root,
        "dev_final_predictions_jsonl",
    )
    manifest_path = _output_path(
        design,
        root,
        "dev_final_predictions_manifest_json",
    )
    if (
        predictions_path.exists()
        or predictions_path.is_symlink()
        or manifest_path.exists()
        or manifest_path.is_symlink()
    ):
        raise FileExistsError("candidate dev-final promotion is create-only")
    eval_root = (root / "docs/phase4/eval").resolve()
    heldout._create_only_bytes(
        predictions_path,
        formal_predictions_path.read_bytes(),
        eval_root,
    )
    success_count = sum(row.get("status") == "ok" for row in predictions)
    promoted_manifest: JsonObject = {
        "design_sha256": design.sha256,
        "contract_sha256": contract_sha256,
        "predictions_path": predictions_path.relative_to(root).as_posix(),
        "predictions_sha256": _sha256_file(predictions_path),
        "row_count": 60,
        "success_count": success_count,
        "failure_count": 60 - success_count,
        "ordered_identity_sha256": heldout._dev_prediction_identity_sha256(
            predictions
        ),
        "completed_at_utc": formal_manifest.get("completed_at_utc"),
    }
    _parse_timestamp(
        promoted_manifest["completed_at_utc"],
        "promoted manifest completed_at_utc",
    )
    heldout._create_only_bytes(
        manifest_path,
        heldout._canonical_json_bytes(promoted_manifest),
        eval_root,
    )
    return predictions_path, manifest_path


def run_v1_7_selection(
    *,
    project_root: Path = PROJECT_ROOT,
    design_path: Path = EVALUATION_DESIGN_PATH,
    contract_path: Path = ACTIVE_CONTRACT_PATH,
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
    chat_json_fn: ChatJsonCallable | None = None,
    load_design_fn: Callable[..., EventEvaluationDesign] | None = None,
    run_dev_iteration_fn: Callable[..., dev_runner.DevIterationResult] | None = None,
    freeze_prediction_contract_fn: Callable[..., Path] | None = None,
) -> tuple[Path, JsonObject]:
    """Execute the only registered v1.7 dev round and freeze exactly one winner."""

    root = project_root.resolve()
    active_clock = clock or (lambda: datetime.now(UTC))
    loader = load_design_fn or load_event_evaluation_design
    design = loader(design_path, project_root=root)
    selection, contract_identity, incumbent_identities = _validate_design(
        design,
        root,
    )
    _selection_preflight(design, root)
    started = _utc_now(active_clock)
    if started >= EXPECTED_DEADLINE_UTC:
        raise ModelSelectionError("v1.7 formal round deadline has passed")
    outcome_path = _output_path(
        design,
        root,
        "model_selection_outcome_receipt_json",
    )
    if outcome_path.exists() or outcome_path.is_symlink():
        raise FileExistsError("v1.7 model-selection outcome is create-only")

    dev_call = run_dev_iteration_fn or dev_runner.run_dev_iteration
    formal = dev_call(
        contract_path,
        OFFICIAL_ROUND_ID,
        project_root=root,
        design=design,
        settings=settings,
        clock=active_clock,
        chat_json_fn=chat_json_fn,
    )
    _selection_preflight(design, root)
    decision_time = _utc_now(active_clock)
    formal_manifest = _load_json(formal.manifest_path, "formal candidate manifest")
    completed_at = _parse_timestamp(
        formal_manifest.get("completed_at_utc"),
        "formal candidate completed_at_utc",
    )
    within_deadline = (
        started < EXPECTED_DEADLINE_UTC
        and completed_at < EXPECTED_DEADLINE_UTC
        and decision_time < EXPECTED_DEADLINE_UTC
    )

    formal_identities: JsonObject = {}
    expected_paths = {
        "predictions": formal.predictions_path,
        "manifest": formal.manifest_path,
        "report": formal.report_path,
    }
    expected_stem = f"docs/phase4/eval/dev-iterations/P4.2a-dev60-{OFFICIAL_ROUND_ID}"
    expected_suffixes = {
        "predictions": ".predictions.jsonl",
        "manifest": ".manifest.json",
        "report": ".report.json",
    }
    for key, path in expected_paths.items():
        resolved = path.resolve()
        expected = (root / f"{expected_stem}{expected_suffixes[key]}").resolve()
        if (
            resolved != expected
            or resolved.is_symlink()
            or not resolved.is_file()
        ):
            raise ModelSelectionError(f"formal candidate {key} path drifted")
        formal_identities[key] = {
            "path": resolved.relative_to(root).as_posix(),
            "sha256": _sha256_file(resolved),
        }
    candidate_report = _load_json(formal.report_path, "candidate dev report")
    _validate_formal_bindings(
        design=design,
        contract_identity=contract_identity,
        formal_identities=formal_identities,
        manifest=formal_manifest,
        report=candidate_report,
    )

    candidate_rows = _indexed_rows(
        _load_jsonl(formal.predictions_path, "candidate predictions"),
        "candidate predictions",
        expected_model=EXPECTED_CANDIDATE_MODEL,
        expected_contract_sha256=cast(str, contract_identity["sha256"]),
    )
    incumbent_rows = _indexed_rows(
        _load_jsonl(
            _project_file(
                root,
                incumbent_identities["dev_predictions"]["path"],
                "incumbent predictions",
            ),
            "incumbent predictions",
        ),
        "incumbent predictions",
        expected_model=EXPECTED_INCUMBENT_MODEL,
        expected_contract_sha256=cast(
            str,
            incumbent_identities["contract"]["sha256"],
        ),
    )
    input_rows, _ = heldout._dev_final_inputs(
        design,
        heldout._load_active_contract(design, root, contract_path),
        root,
    )
    labels, _ = dev_runner._load_dev_labels(root, input_rows)
    if set(candidate_rows) != set(incumbent_rows) or set(candidate_rows) != set(labels):
        raise ModelSelectionError("candidate/incumbent/dev-label ID sets drifted")
    for identifier in sorted(candidate_rows):
        candidate_row = candidate_rows[identifier]
        incumbent_row = incumbent_rows[identifier]
        for field in (
            "declared_input_sha256",
            "input_sha256",
            "text_sha256",
        ):
            if candidate_row.get(field) != incumbent_row.get(field):
                raise ModelSelectionError(
                    f"candidate/incumbent row {identifier} {field} differs"
                )

    candidate_diagnostics = _diagnostics(candidate_rows, labels)
    incumbent_diagnostics = _diagnostics(incumbent_rows, labels)
    gates = _gate_evidence(
        candidate_diagnostics,
        selection,
        within_deadline=within_deadline,
    )

    per_item: list[JsonObject] = []
    for identifier in sorted(candidate_rows):
        candidate_item = _model_item(candidate_rows[identifier])
        incumbent_item = _model_item(incumbent_rows[identifier])
        reference = _gold_item(labels[identifier])
        per_item.append(
            {
                "news_item_id": identifier,
                "reference": reference,
                "candidate": candidate_item,
                "incumbent": incumbent_item,
                "candidate_vs_incumbent": _comparison_flags(
                    candidate_item,
                    incumbent_item,
                ),
                "candidate_vs_reference": _reference_flags(
                    candidate_item,
                    reference,
                ),
                "incumbent_vs_reference": _reference_flags(
                    incumbent_item,
                    reference,
                ),
            }
        )
    pairwise = _pairwise_aggregate(per_item)

    pricing = _mapping(
        selection.get("pricing_cny_per_million"),
        "selection pricing",
    )
    monthly_calls = cast(int, selection.get("monthly_calls"))
    candidate_cost = _cost(
        candidate_diagnostics,
        _mapping(pricing.get("flash"), "flash pricing"),
        monthly_calls,
    )
    incumbent_cost = _cost(
        incumbent_diagnostics,
        _mapping(pricing.get("plus"), "plus pricing"),
        monthly_calls,
    )

    candidate_receipt_path = _output_path(
        design,
        root,
        "prediction_contract_freeze_receipt_json",
    )
    candidate_receipt: JsonObject = {
        "path": candidate_receipt_path.relative_to(root).as_posix(),
        "sha256": None,
        "created": False,
        "validated": False,
    }
    all_passed = gates.get("all_passed") is True
    operational_completion: JsonObject
    if all_passed:
        try:
            promotion_time = _utc_now(active_clock)
            if promotion_time >= EXPECTED_DEADLINE_UTC:
                raise ModelSelectionError(
                    "v1.7 freeze deadline passed during local promotion"
                )
            promoted_predictions, promoted_manifest = _promote_formal_predictions(
                design=design,
                root=root,
                formal_predictions_path=formal.predictions_path,
                formal_manifest_path=formal.manifest_path,
                contract_sha256=cast(str, contract_identity["sha256"]),
            )
            freeze_call = (
                freeze_prediction_contract_fn
                or heldout.freeze_prediction_contract
            )
            receipt_path = freeze_call(
                contract_path,
                promoted_predictions,
                promoted_manifest,
                project_root=root,
                design=design,
                now=promotion_time,
            )
            if (
                receipt_path.resolve() != candidate_receipt_path
                or receipt_path.is_symlink()
                or not receipt_path.is_file()
            ):
                raise ModelSelectionError("candidate freeze receipt path drifted")
            candidate_receipt = {
                "path": receipt_path.relative_to(root).as_posix(),
                "sha256": _sha256_file(receipt_path),
                "created": True,
                "validated": True,
            }
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as error:
            if candidate_receipt_path.is_file() and not candidate_receipt_path.is_symlink():
                candidate_receipt = {
                    "path": candidate_receipt_path.relative_to(root).as_posix(),
                    "sha256": _sha256_file(candidate_receipt_path),
                    "created": True,
                    "validated": False,
                }
            operational_completion = {
                "status": "blocked_candidate_freeze_failed",
                "error_code": _safe_error(error),
                "model_calls_retried": 0,
                "selected_incumbent_fail_closed": True,
            }
            decision = "retain_incumbent"
            selected_model = EXPECTED_INCUMBENT_MODEL
            selected_contract = incumbent_identities["contract"]
            selected_freeze = incumbent_identities["freeze_receipt"]
        else:
            operational_completion = {
                "status": "candidate_frozen",
                "error_code": None,
                "model_calls_retried": 0,
                "selected_incumbent_fail_closed": False,
            }
            decision = "select_candidate"
            selected_model = EXPECTED_CANDIDATE_MODEL
            selected_contract = contract_identity
            selected_freeze = {
                "path": candidate_receipt["path"],
                "sha256": candidate_receipt["sha256"],
            }
    else:
        operational_completion = {
            "status": "candidate_not_frozen_absolute_gates_failed",
            "error_code": None,
            "model_calls_retried": 0,
            "selected_incumbent_fail_closed": True,
        }
        decision = "retain_incumbent"
        selected_model = EXPECTED_INCUMBENT_MODEL
        selected_contract = incumbent_identities["contract"]
        selected_freeze = incumbent_identities["freeze_receipt"]

    recorded_at = _utc_now(active_clock)
    if decision == "select_candidate" and recorded_at >= EXPECTED_DEADLINE_UTC:
        operational_completion = {
            "status": "blocked_outcome_recorded_after_deadline",
            "error_code": "selection_deadline_elapsed",
            "model_calls_retried": 0,
            "selected_incumbent_fail_closed": True,
        }
        decision = "retain_incumbent"
        selected_model = EXPECTED_INCUMBENT_MODEL
        selected_contract = incumbent_identities["contract"]
        selected_freeze = incumbent_identities["freeze_receipt"]
    incumbent_report = _load_json(
        _project_file(
            root,
            incumbent_identities["dev_report"]["path"],
            "incumbent dev report",
        ),
        "incumbent dev report",
    )
    outcome: JsonObject = {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "official_round_id": OFFICIAL_ROUND_ID,
        "recorded_at_utc": _timestamp(recorded_at),
        "deadline_utc": _timestamp(EXPECTED_DEADLINE_UTC),
        "decision": decision,
        "selected_model": selected_model,
        "design": {
            "path": design.path.relative_to(root).as_posix(),
            "sha256": design.sha256,
        },
        "candidate": {
            "model": EXPECTED_CANDIDATE_MODEL,
            "contract": contract_identity,
            "formal_round": formal_identities,
            "input_identity": candidate_report.get("input_identity"),
            "metrics": candidate_diagnostics,
            "runtime_evidence": candidate_report.get("runtime_evidence"),
            "cost": candidate_cost,
            "freeze_receipt": candidate_receipt,
        },
        "incumbent": {
            "model": EXPECTED_INCUMBENT_MODEL,
            **incumbent_identities,
            "input_identity": incumbent_report.get("input_identity"),
            "metrics": incumbent_diagnostics,
            "runtime_evidence": incumbent_report.get("runtime_evidence"),
            "cost": incumbent_cost,
        },
        "gates": gates,
        "per_item": per_item,
        "candidate_vs_incumbent": pairwise,
        "cost_comparison": {
            "monthly_calls": monthly_calls,
            "incumbent": incumbent_cost,
            "candidate": candidate_cost,
        },
        "selected_contract": selected_contract,
        "selected_freeze_receipt": selected_freeze,
        "operational_completion": operational_completion,
        "heldout_accessed": False,
        "production_writes": 0,
        "third_model_run": False,
    }
    heldout._create_only_bytes(
        outcome_path,
        heldout._canonical_json_bytes(outcome),
        (root / "docs/phase4/eval").resolve(),
    )
    return outcome_path, outcome


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the one pre-registered P4.2a v1.7 model-selection round."
        )
    )
    parser.add_argument(
        "--evaluation-design",
        type=Path,
        default=EVALUATION_DESIGN_PATH,
    )
    parser.add_argument(
        "--active-contract",
        type=Path,
        default=ACTIVE_CONTRACT_PATH,
    )
    return parser


def _safe_error(error: BaseException) -> str:
    if isinstance(error, FileExistsError):
        return "create_only_artifact_exists"
    if isinstance(error, ModelSelectionError):
        return str(error)
    return type(error).__name__


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        path, outcome = run_v1_7_selection(
            design_path=cast(Path, arguments.evaluation_design),
            contract_path=cast(Path, arguments.active_contract),
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
    print(
        json.dumps(
            {
                "status": "completed",
                "decision": outcome["decision"],
                "selected_model": outcome["selected_model"],
                "outcome_path": path.as_posix(),
                "gates": outcome["gates"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
