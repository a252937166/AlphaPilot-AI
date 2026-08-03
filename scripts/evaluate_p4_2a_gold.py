from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

PROJECT_DIR = Path(__file__).resolve().parent.parent
if __package__ in {None, ""}:
    # Direct ``python scripts/evaluate_p4_2a_gold.py`` execution places only the
    # scripts directory on sys.path. Add the project root so the same canonical
    # package import is used by the CLI and by pytest/module execution.
    sys.path.insert(0, str(PROJECT_DIR))

from scripts import build_p4_2a_gold_sample as gold_builder  # noqa: E402

from alphapilot.llm.p4_news_event import load_event_extract_contract  # noqa: E402

DEFAULT_CONFIG = Path("config/p4_event_extract_eval_v1.yaml")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SIX_DIGIT_SYMBOL = re.compile(r"^[0-9]{6}$")
COMPLETED_ANNOTATION_STATUSES = frozenset({"annotated", "complete", "completed"})

JsonObject = dict[str, Any]


class GoldEvaluationError(RuntimeError):
    """The fixed sample, owner labels, predictions, or report path is invalid."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: object, *, label: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise GoldEvaluationError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _project_path(value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise GoldEvaluationError(f"{label} must be a non-empty path")
    path = Path(value)
    return (path if path.is_absolute() else PROJECT_DIR / path).resolve()


def _read_jsonl(path: Path, *, label: str) -> tuple[list[JsonObject], str]:
    resolved = path.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise GoldEvaluationError(f"{label} must be one regular non-symlink JSONL file")
    payload = resolved.read_bytes()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GoldEvaluationError(f"{label} must be UTF-8") from exc
    records: list[JsonObject] = []

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise GoldEvaluationError(f"{label} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise GoldEvaluationError(f"{label} has a blank line at {line_number}")
        try:
            value: object = json.loads(line, object_pairs_hook=reject_duplicates)
        except json.JSONDecodeError as exc:
            raise GoldEvaluationError(f"{label} line {line_number} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise GoldEvaluationError(f"{label} line {line_number} must be an object")
        records.append(value)
    if not records:
        raise GoldEvaluationError(f"{label} is empty")
    return records, _sha256_bytes(payload)


def _read_json(path: Path, *, label: str) -> tuple[JsonObject, str]:
    resolved = path.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise GoldEvaluationError(f"{label} must be one regular non-symlink JSON file")
    payload = resolved.read_bytes()

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise GoldEvaluationError(f"{label} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value: object = json.loads(payload, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoldEvaluationError(f"{label} is invalid UTF-8 JSON") from exc
    return _mapping(value, label=label), _sha256_bytes(payload)


def _annotation_owner(record: Mapping[str, object]) -> str | None:
    owner = record.get("annotation_owner")
    return owner.strip().casefold() if isinstance(owner, str) and owner.strip() else None


def _validate_symbol_list(value: object, *, label: str) -> list[str]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or any(not isinstance(item, str) for item in value)
    ):
        raise GoldEvaluationError(f"{label} must be a symbol array")
    symbols = [str(item) for item in value]
    if any(SIX_DIGIT_SYMBOL.fullmatch(item) is None for item in symbols):
        raise GoldEvaluationError(f"{label} must contain only six-digit symbols")
    if symbols != sorted(set(symbols)):
        raise GoldEvaluationError(f"{label} must be sorted and unique")
    return symbols


def _gold_labels(
    record: Mapping[str, object],
    *,
    taxonomy: frozenset[str],
) -> JsonObject:
    news_item_id = record.get("news_item_id")
    gold = _mapping(record.get("gold"), label=f"gold for news item {news_item_id}")
    required = {"symbols", "event_type", "direction", "materiality", "evidence_span"}
    allowed = required | {"notes"}
    if set(gold) != allowed:
        raise GoldEvaluationError(
            f"gold for news item {news_item_id} must contain exactly {sorted(allowed)}"
        )
    symbols = _validate_symbol_list(
        gold.get("symbols"), label=f"gold.symbols for news item {news_item_id}"
    )
    event_type = gold.get("event_type")
    if not isinstance(event_type, str) or event_type not in taxonomy:
        raise GoldEvaluationError(f"gold.event_type for news item {news_item_id} is invalid")
    direction = gold.get("direction")
    if isinstance(direction, bool) or not isinstance(direction, int) or direction not in {-1, 0, 1}:
        raise GoldEvaluationError(f"gold.direction for news item {news_item_id} is invalid")
    materiality = gold.get("materiality")
    if (
        isinstance(materiality, bool)
        or not isinstance(materiality, int)
        or materiality not in {0, 1, 2, 3}
    ):
        raise GoldEvaluationError(f"gold.materiality for news item {news_item_id} is invalid")
    evidence = gold.get("evidence_span")
    original_text = record.get("original_text")
    if (
        not isinstance(evidence, str)
        or not evidence
        or not isinstance(original_text, str)
        or evidence not in original_text
    ):
        raise GoldEvaluationError(
            f"gold.evidence_span for news item {news_item_id} is not a contiguous substring"
        )
    notes = gold.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise GoldEvaluationError(f"gold.notes for news item {news_item_id} is invalid")
    return {
        "symbols": symbols,
        "event_type": event_type,
        "direction": direction,
        "materiality": materiality,
        "evidence_span": evidence,
        "notes": notes,
    }


def validate_owner_annotations(
    records: Sequence[JsonObject],
    contract: gold_builder.FrozenContract,
) -> dict[int, JsonObject]:
    """Validate all 100 owner labels and recompute the frozen text/input hashes."""

    evaluation = _mapping(contract.document.get("evaluation"), label="evaluation")
    expected_count = evaluation.get("sample_count")
    if expected_count != 100 or len(records) != expected_count:
        raise GoldEvaluationError(
            f"owner annotation sample must contain exactly 100 rows, observed {len(records)}"
        )
    taxonomy_record = _mapping(contract.document.get("taxonomy"), label="taxonomy")
    taxonomy_values = taxonomy_record.get("values")
    if not isinstance(taxonomy_values, list) or any(
        not isinstance(item, str) for item in taxonomy_values
    ):
        raise GoldEvaluationError("taxonomy values are invalid")
    taxonomy = frozenset(taxonomy_values)
    event_contract = load_event_extract_contract(contract.path)

    validated: dict[int, JsonObject] = {}
    for expected_index, record in enumerate(records, start=1):
        news_item_id = record.get("news_item_id")
        if isinstance(news_item_id, bool) or not isinstance(news_item_id, int) or news_item_id <= 0:
            raise GoldEvaluationError("annotation news_item_id must be a positive integer")
        if gold_builder.MODEL_PREDICTION_KEYS.intersection(record):
            raise GoldEvaluationError(
                f"annotation news item {news_item_id} contains model predictions"
            )
        unexpected_fields = set(record) - gold_builder.ANNOTATION_ITEM_FIELDS
        missing_fields = gold_builder.ANNOTATION_ITEM_FIELDS - set(record)
        if unexpected_fields or missing_fields:
            raise GoldEvaluationError(
                f"annotation news item {news_item_id} fields drifted: "
                f"unexpected={sorted(unexpected_fields)}, missing={sorted(missing_fields)}"
            )
        if record.get("schema_version") != "p4.2a-gold-annotation-item-v1":
            raise GoldEvaluationError(f"annotation news item {news_item_id} schema drifted")
        if news_item_id in validated:
            raise GoldEvaluationError(f"annotation duplicates news item {news_item_id}")
        if record.get("sample_index") != expected_index:
            raise GoldEvaluationError("annotation sample order or sample_index drifted")
        if record.get("sample_version") != "p4.2a-gold-v1":
            raise GoldEvaluationError(f"annotation news item {news_item_id} version drifted")
        if record.get("contract_sha256") != contract.sha256:
            raise GoldEvaluationError(f"annotation news item {news_item_id} contract drifted")
        if record.get("annotation_status") not in COMPLETED_ANNOTATION_STATUSES:
            raise GoldEvaluationError(f"annotation news item {news_item_id} is not completed")
        if _annotation_owner(record) != "owner":
            raise GoldEvaluationError(f"annotation news item {news_item_id} is not owner-labelled")
        original_text = record.get("original_text")
        if not isinstance(original_text, str) or not original_text:
            raise GoldEvaluationError(f"annotation news item {news_item_id} has no text")
        text_sha256 = _sha256_bytes(original_text.encode("utf-8"))
        if record.get("text_sha256") != text_sha256:
            raise GoldEvaluationError(f"annotation news item {news_item_id} text hash drifted")
        expected_input_sha256 = gold_builder._input_sha256(record, event_contract)
        if record.get("input_sha256") != expected_input_sha256:
            raise GoldEvaluationError(f"annotation news item {news_item_id} input hash drifted")
        labels = _gold_labels(record, taxonomy=taxonomy)
        validated[news_item_id] = {"record": record, "gold": labels}
    return validated


def _validate_manifest(
    manifest: JsonObject,
    annotations: Mapping[int, JsonObject],
    contract: gold_builder.FrozenContract,
) -> None:
    if manifest.get("schema_version") != "p4.2a-gold-annotation-manifest-v1":
        raise GoldEvaluationError("gold manifest schema_version drifted")
    contract_record = _mapping(manifest.get("contract"), label="manifest.contract")
    if contract_record.get("sha256") != contract.sha256:
        raise GoldEvaluationError("gold manifest contract hash drifted")
    frozen_ids = manifest.get("frozen_news_item_ids")
    if not isinstance(frozen_ids, list) or frozen_ids != list(annotations):
        raise GoldEvaluationError("annotation IDs/order differ from the frozen manifest")
    frozen_items = manifest.get("frozen_items")
    if not isinstance(frozen_items, list) or len(frozen_items) != 100:
        raise GoldEvaluationError("gold manifest must bind exactly 100 frozen items")
    identities = [
        _mapping(raw_identity, label=f"manifest.frozen_items[{index}]")
        for index, raw_identity in enumerate(frozen_items)
    ]
    identity_ids = [identity.get("news_item_id") for identity in identities]
    if (
        identity_ids != frozen_ids
        or len(set(identity_ids)) != 100
        or any(not isinstance(news_item_id, int) for news_item_id in identity_ids)
    ):
        raise GoldEvaluationError(
            "manifest frozen_items IDs must be 100 unique ordered frozen_news_item_ids"
        )
    expected_identity_fields = set(gold_builder.FROZEN_MANIFEST_DIRECT_FIELDS) | {
        "body_evidence_sha256"
    }
    manifest_bound_annotation_fields = set(gold_builder.FROZEN_MANIFEST_DIRECT_FIELDS) | {
        "original_text",
        "body_evidence",
    }
    expected_immutable_fields = (
        gold_builder.ANNOTATION_ITEM_FIELDS - gold_builder.ANNOTATION_MUTABLE_FIELDS
    )
    if manifest_bound_annotation_fields != expected_immutable_fields:
        raise GoldEvaluationError(
            "manifest binding does not cover every immutable annotation field"
        )
    for expected_index, identity in enumerate(identities, start=1):
        if set(identity) != expected_identity_fields:
            raise GoldEvaluationError(f"manifest frozen item {expected_index} fields drifted")
        news_item_id = identity.get("news_item_id")
        if not isinstance(news_item_id, int) or news_item_id not in annotations:
            raise GoldEvaluationError("gold manifest contains an unknown frozen item")
        if identity.get("sample_index") != expected_index:
            raise GoldEvaluationError("manifest frozen_items sample_index/order drifted")
        annotation = _mapping(
            annotations[news_item_id]["record"],
            label=f"annotation news item {news_item_id}",
        )
        for field in gold_builder.FROZEN_MANIFEST_DIRECT_FIELDS:
            if identity.get(field) != annotation.get(field):
                raise GoldEvaluationError(
                    f"annotation news item {news_item_id} changed frozen field {field}"
                )
        if identity.get("body_evidence_sha256") != gold_builder.canonical_json_sha256(
            annotation.get("body_evidence")
        ):
            raise GoldEvaluationError(
                f"annotation news item {news_item_id} changed frozen body evidence"
            )


def _prediction_payload(
    row: JsonObject,
    annotation: JsonObject,
    *,
    validator: Draft202012Validator,
) -> JsonObject | None:
    news_item_id = annotation["news_item_id"]
    if row.get("contract_sha256") != annotation.get("contract_sha256"):
        raise GoldEvaluationError(f"prediction news item {news_item_id} contract hash differs")
    if row.get("input_sha256") != annotation.get("input_sha256"):
        raise GoldEvaluationError(f"prediction news item {news_item_id} input hash differs")
    if row.get("text_sha256") != annotation.get("text_sha256"):
        raise GoldEvaluationError(f"prediction news item {news_item_id} text hash differs")

    status = row.get("status")
    prediction = row.get("prediction")
    if status != "ok":
        if prediction is not None:
            raise GoldEvaluationError(
                f"failed prediction news item {news_item_id} must have prediction=null"
            )
        return None
    if not isinstance(prediction, Mapping):
        raise GoldEvaluationError(f"successful prediction news item {news_item_id} has no object")
    candidate = {str(key): value for key, value in prediction.items()}
    errors = sorted(validator.iter_errors(candidate), key=lambda item: list(item.path))
    if errors:
        raise GoldEvaluationError(
            f"successful prediction news item {news_item_id} violates schema: {errors[0].message}"
        )
    candidate["symbols"] = _validate_symbol_list(
        candidate.get("symbols"),
        label=f"prediction.symbols for news item {news_item_id}",
    )
    evidence = candidate.get("evidence_span")
    original_text = annotation.get("original_text")
    if (
        not isinstance(evidence, str)
        or not isinstance(original_text, str)
        or evidence not in original_text
    ):
        raise GoldEvaluationError(
            f"prediction evidence_span for news item {news_item_id} is not in frozen text"
        )
    return candidate


def join_predictions(
    prediction_records: Sequence[JsonObject],
    annotations: Mapping[int, JsonObject],
    contract: gold_builder.FrozenContract,
) -> tuple[dict[int, JsonObject | None], int]:
    """Join by fixed ID and exact hashes; extra non-sample rows are not scored."""

    predictions_by_id: dict[int, JsonObject] = {}
    for row in prediction_records:
        news_item_id = row.get("news_item_id")
        if isinstance(news_item_id, bool) or not isinstance(news_item_id, int) or news_item_id <= 0:
            raise GoldEvaluationError("prediction news_item_id must be a positive integer")
        if news_item_id in predictions_by_id:
            raise GoldEvaluationError(f"predictions duplicate news item {news_item_id}")
        predictions_by_id[news_item_id] = row
    missing = [
        news_item_id for news_item_id in annotations if news_item_id not in predictions_by_id
    ]
    if missing:
        raise GoldEvaluationError(
            f"predictions are missing {len(missing)} fixed sample rows; first={missing[0]}"
        )

    contract_files = _mapping(contract.document.get("contract_files"), label="contract_files")
    schema_record = _mapping(contract_files.get("schema"), label="contract_files.schema")
    schema_path = _project_path(schema_record.get("path"), label="contract_files.schema.path")
    schema, schema_sha256 = _read_json(schema_path, label="prediction schema")
    if schema_sha256 != schema_record.get("sha256"):
        raise GoldEvaluationError("prediction schema SHA-256 drifted")
    validator = Draft202012Validator(schema)
    joined = {
        news_item_id: _prediction_payload(
            predictions_by_id[news_item_id],
            _mapping(item["record"], label=f"annotation {news_item_id}"),
            validator=validator,
        )
        for news_item_id, item in annotations.items()
    }
    return joined, len(predictions_by_id) - len(annotations)


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def evaluate_records(
    annotations: Mapping[int, JsonObject],
    predictions: Mapping[int, JsonObject | None],
    contract: gold_builder.FrozenContract,
) -> JsonObject:
    """Compute frozen gates plus diagnostics over all 100 rows."""

    if list(annotations) != list(predictions):
        raise GoldEvaluationError("prediction join is not the exact fixed sample")
    evaluation = _mapping(contract.document.get("evaluation"), label="evaluation")
    if (
        evaluation.get("sample_count") != 100
        or evaluation.get("materiality_positive_definition") != "materiality_gte_2"
        or evaluation.get("materiality_zero_predicted_positive_policy") != "fail"
        or evaluation.get("materiality_precision_minimum") != 0.80
        or evaluation.get("symbol_mapping_accuracy_minimum") != 0.95
        or evaluation.get("symbol_bearing_exact_set_accuracy_minimum") != 0.95
        or evaluation.get("threshold_changes_forbidden") is not True
    ):
        raise GoldEvaluationError("evaluation thresholds or formulas drifted")

    tp = fp = fn = tn = 0
    symbol_exact = 0
    symbol_bearing_exact = 0
    symbol_bearing_count = 0
    null_gold_count = 0
    null_gold_false_positive = 0
    event_type_exact = 0
    direction_exact = 0
    materiality_exact = 0
    successful = 0
    event_type_confusion: dict[str, dict[str, int]] = {}
    direction_confusion: dict[str, dict[str, int]] = {}

    for news_item_id, item in annotations.items():
        labels = _mapping(item["gold"], label=f"gold {news_item_id}")
        prediction = predictions[news_item_id]
        gold_positive = int(labels["materiality"]) >= 2
        predicted_positive = prediction is not None and int(prediction["materiality"]) >= 2
        if gold_positive and predicted_positive:
            tp += 1
        elif not gold_positive and predicted_positive:
            fp += 1
        elif gold_positive:
            fn += 1
        else:
            tn += 1

        gold_symbols = list(labels["symbols"])
        if gold_symbols:
            symbol_bearing_count += 1
        else:
            null_gold_count += 1
        if prediction is None:
            continue
        successful += 1
        predicted_symbols = list(prediction["symbols"])
        symbols_match = predicted_symbols == gold_symbols
        if symbols_match:
            symbol_exact += 1
            if gold_symbols:
                symbol_bearing_exact += 1
        if not gold_symbols and predicted_symbols:
            null_gold_false_positive += 1
        if prediction["event_type"] == labels["event_type"]:
            event_type_exact += 1
        if prediction["direction"] == labels["direction"]:
            direction_exact += 1
        if prediction["materiality"] == labels["materiality"]:
            materiality_exact += 1
        gold_event = str(labels["event_type"])
        predicted_event = str(prediction["event_type"])
        event_type_confusion.setdefault(gold_event, {})
        event_type_confusion[gold_event][predicted_event] = (
            event_type_confusion[gold_event].get(predicted_event, 0) + 1
        )
        gold_direction = str(labels["direction"])
        predicted_direction = str(prediction["direction"])
        direction_confusion.setdefault(gold_direction, {})
        direction_confusion[gold_direction][predicted_direction] = (
            direction_confusion[gold_direction].get(predicted_direction, 0) + 1
        )

    sample_count = len(annotations)
    predicted_positives = tp + fp
    materiality_precision = _ratio(tp, predicted_positives)
    symbol_accuracy = _ratio(symbol_exact, sample_count)
    symbol_bearing_accuracy = _ratio(symbol_bearing_exact, symbol_bearing_count)
    materiality_pass = materiality_precision is not None and materiality_precision >= float(
        evaluation["materiality_precision_minimum"]
    )
    symbol_pass = symbol_accuracy is not None and symbol_accuracy >= float(
        evaluation["symbol_mapping_accuracy_minimum"]
    )
    symbol_bearing_pass = symbol_bearing_accuracy is not None and symbol_bearing_accuracy >= float(
        evaluation["symbol_bearing_exact_set_accuracy_minimum"]
    )
    gates: JsonObject = {
        "materiality_gte_2_precision": {
            "formula": "tp/(tp+fp)",
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "predicted_positive_count": predicted_positives,
            "value": materiality_precision,
            "minimum": 0.80,
            "zero_predicted_positive_policy": "fail",
            "passed": materiality_pass,
        },
        "symbol_all_exact_set_accuracy": {
            "formula": "exact_set_matches/all_100",
            "exact_set_matches": symbol_exact,
            "denominator": sample_count,
            "value": symbol_accuracy,
            "minimum": 0.95,
            "passed": symbol_pass,
        },
        "symbol_bearing_exact_set_accuracy": {
            "formula": "exact_set_matches/gold_symbol_bearing_rows",
            "exact_set_matches": symbol_bearing_exact,
            "denominator": symbol_bearing_count,
            "value": symbol_bearing_accuracy,
            "minimum": 0.95,
            "zero_denominator_policy": "fail",
            "passed": symbol_bearing_pass,
        },
    }
    diagnostics: JsonObject = {
        "prediction_success": {
            "count": successful,
            "failed_count": sample_count - successful,
            "rate": _ratio(successful, sample_count),
        },
        "null_gold_symbol_false_positive_rate": {
            "false_positive_count": null_gold_false_positive,
            "denominator": null_gold_count,
            "value": _ratio(null_gold_false_positive, null_gold_count),
        },
        "event_type_exact_match_accuracy": {
            "matches": event_type_exact,
            "denominator": sample_count,
            "value": _ratio(event_type_exact, sample_count),
            "confusion": event_type_confusion,
        },
        "direction_exact_match_accuracy": {
            "matches": direction_exact,
            "denominator": sample_count,
            "value": _ratio(direction_exact, sample_count),
            "confusion": direction_confusion,
        },
        "materiality_exact_match_accuracy": {
            "matches": materiality_exact,
            "denominator": sample_count,
            "value": _ratio(materiality_exact, sample_count),
        },
    }
    return {
        "sample_count": sample_count,
        "gates": gates,
        "diagnostics": diagnostics,
        "passed": all(bool(_mapping(gate, label="gate")["passed"]) for gate in gates.values()),
    }


def _new_report_path(path: Path, artifact_root: Path) -> Path:
    root = artifact_root.resolve()
    if root.exists() and root.is_symlink():
        raise GoldEvaluationError("eval artifact root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise GoldEvaluationError("evaluation report must stay under docs/phase4/eval")
    if resolved.exists() or resolved.is_symlink():
        raise FileExistsError(f"refusing to overwrite P4.2a evaluation report: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if resolved.parent.is_symlink():
        raise GoldEvaluationError("evaluation report directory must not be a symlink")
    return resolved


def _write_new_json(path: Path, report: JsonObject) -> str:
    payload = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return _sha256_bytes(payload)


def evaluate_gold_sample(
    annotation_path: Path,
    predictions_path: Path,
    contract_path: Path,
    output_path: Path,
) -> JsonObject:
    """Evaluate one fixed owner-labelled round and create one immutable report."""

    contract = gold_builder.load_contract(contract_path)
    artifact_root = _project_path(contract.document.get("artifact_root"), label="artifact_root")
    annotation_resolved = annotation_path.resolve()
    predictions_resolved = predictions_path.resolve()
    for label, path in (
        ("annotation", annotation_resolved),
        ("predictions", predictions_resolved),
    ):
        if not path.is_relative_to(artifact_root):
            raise GoldEvaluationError(f"{label} artifact must stay under docs/phase4/eval")
    report_path = _new_report_path(output_path, artifact_root)

    annotation_records, annotation_sha256 = _read_jsonl(
        annotation_resolved, label="owner annotations"
    )
    prediction_records, predictions_sha256 = _read_jsonl(predictions_resolved, label="predictions")
    annotations = validate_owner_annotations(annotation_records, contract)

    gold_sample = _mapping(contract.document.get("gold_sample"), label="gold_sample")
    manifest_path = _project_path(
        gold_sample.get("manifest_json"), label="gold_sample.manifest_json"
    )
    manifest, manifest_sha256 = _read_json(manifest_path, label="gold manifest")
    _validate_manifest(manifest, annotations, contract)

    predictions, extra_prediction_count = join_predictions(
        prediction_records, annotations, contract
    )
    result = evaluate_records(annotations, predictions, contract)
    report: JsonObject = {
        "schema_version": "p4.2a-gold-evaluation-report-v1",
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "contract": {
            "path": str(contract.path.relative_to(PROJECT_DIR)),
            "sha256": contract.sha256,
            "threshold_changes_forbidden": True,
        },
        "inputs": {
            "annotation_path": str(annotation_resolved.relative_to(PROJECT_DIR)),
            "annotation_sha256": annotation_sha256,
            "predictions_path": str(predictions_resolved.relative_to(PROJECT_DIR)),
            "predictions_sha256": predictions_sha256,
            "manifest_path": str(manifest_path.relative_to(PROJECT_DIR)),
            "manifest_sha256": manifest_sha256,
            "extra_prediction_rows_not_scored": extra_prediction_count,
            "identity_join": "news_item_id + input_sha256 + text_sha256 + contract_sha256",
        },
        **result,
        "phase_gate": {
            "p4_2a_evaluation_passed": result["passed"],
            "p4_2b_unlocked": False,
            "production_writes_performed": False,
            "proposals_or_orders_created": False,
        },
        "report_artifact": {
            "path": str(report_path.relative_to(PROJECT_DIR)),
            "create_only": True,
        },
    }
    report_sha256 = _write_new_json(report_path, report)
    report_artifact = _mapping(report["report_artifact"], label="report_artifact")
    report_artifact["sha256"] = report_sha256
    report["report_artifact"] = report_artifact
    return report


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the fixed 100-row P4.2a owner gold sample. Threshold failures "
            "create an immutable report and exit 2."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--annotations", type=Path, default=None)
    parser.add_argument("--predictions", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        contract = gold_builder.load_contract(arguments.config)
        gold_sample = _mapping(contract.document.get("gold_sample"), label="gold_sample")
        annotation_path = (
            arguments.annotations
            if arguments.annotations is not None
            else _project_path(
                gold_sample.get("final_output_jsonl"),
                label="gold_sample.final_output_jsonl",
            )
        )
        predictions_path = (
            arguments.predictions
            if arguments.predictions is not None
            else _project_path(
                gold_sample.get("predictions_output_jsonl"),
                label="gold_sample.predictions_output_jsonl",
            )
        )
        report = evaluate_gold_sample(
            annotation_path,
            predictions_path,
            arguments.config,
            arguments.output,
        )
    except (FileExistsError, GoldEvaluationError, OSError, ValueError) as exc:
        print(f"P4.2a evaluation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
