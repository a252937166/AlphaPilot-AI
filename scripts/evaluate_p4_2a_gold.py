from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

PROJECT_DIR = Path(__file__).resolve().parent.parent
if __package__ in {None, ""}:
    # Direct ``python scripts/evaluate_p4_2a_gold.py`` execution places only the
    # scripts directory on sys.path. Anchor both namespace and src-layout
    # imports to this checkout rather than the caller's cwd or PYTHONPATH.
    sys.path[:0] = [str(PROJECT_DIR), str(PROJECT_DIR / "src")]

from scripts import build_p4_2a_gold_sample as gold_builder  # noqa: E402

from alphapilot.llm.p4_news_eval import (  # noqa: E402
    EventEvaluationDesign,
    EventEvaluationDesignError,
    load_event_evaluation_design,
    validate_heldout_annotation_provenance,
)
from alphapilot.llm.p4_news_event import (  # noqa: E402
    EXACT_EVIDENCE_SPAN_MATCH_MODE,
    WHITESPACE_NORMALIZED_EVIDENCE_SPAN_MATCH_MODE,
    EventExtractContract,
    evidence_span_matches,
    load_event_extract_contract,
)

DEFAULT_CONFIG = Path("config/p4_event_extract_eval_v1.yaml")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SIX_DIGIT_SYMBOL = re.compile(r"^[0-9]{6}$")
COMPLETED_ANNOTATION_STATUSES = frozenset({"annotated", "complete", "completed"})

JsonObject = dict[str, Any]


class GoldEvaluationError(RuntimeError):
    """The fixed sample, owner labels, predictions, or report path is invalid."""


@dataclass(frozen=True, slots=True)
class HeldoutEvaluationPreflight:
    """Validated, read-only inputs immediately before heldout scoring begins."""

    design: gold_builder.FrozenEvaluationDesign
    artifact_root: Path
    annotation_resolved: Path
    annotation_sha256: str
    annotations: dict[int, JsonObject]
    owner_completion: JsonObject
    adjudication_evidence: JsonObject | None
    active_contract: EventExtractContract
    receipt: JsonObject
    receipt_sha256: str
    dev_final: JsonObject
    inference: JsonObject
    selection_evidence: JsonObject
    materialization_binding: JsonObject | None
    dev_annotations: dict[int, JsonObject]
    dev_prediction_records: list[JsonObject]
    predictions: dict[int, JsonObject | None]
    state_path: Path


class _PreflightRunner:
    """Run a dependency-aware validation DAG in fail-fast or audit mode."""

    def __init__(self, *, collect_all: bool) -> None:
        self.collect_all = collect_all
        self.values: dict[str, Any] = {}
        self.stages: list[JsonObject] = []
        self._statuses: dict[str, str] = {}

    def run(
        self,
        name: str,
        dependencies: Sequence[str],
        action: Callable[[], Any],
    ) -> None:
        blocked_by = [
            dependency
            for dependency in dependencies
            if self._statuses.get(dependency) != "passed"
        ]
        if blocked_by:
            self._statuses[name] = "blocked"
            self.stages.append(
                {"name": name, "status": "blocked", "blocked_by": blocked_by}
            )
            return
        try:
            self.values[name] = action()
        except Exception as exc:
            if not self.collect_all:
                raise
            status = (
                "failed"
                if isinstance(
                    exc,
                    (
                        FileExistsError,
                        GoldEvaluationError,
                        gold_builder.GoldSampleError,
                        EventEvaluationDesignError,
                        OSError,
                        ValueError,
                    ),
                )
                else "internal_error"
            )
            self._statuses[name] = status
            stage: JsonObject = {
                "name": name,
                "status": status,
                "error_type": type(exc).__name__,
            }
            if status == "failed":
                stage["safe_message"] = str(exc)
            self.stages.append(stage)
            return
        self._statuses[name] = "passed"
        self.stages.append({"name": name, "status": "passed"})

    def passed(self, name: str) -> bool:
        return self._statuses.get(name) == "passed"


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON numeric constant is forbidden: {value}")


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
            value: object = json.loads(
                line,
                object_pairs_hook=reject_duplicates,
                parse_constant=_reject_non_finite_json,
            )
        except ValueError as exc:
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
        value: object = json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=_reject_non_finite_json,
        )
    except ValueError as exc:
        raise GoldEvaluationError(f"{label} is invalid UTF-8 JSON") from exc
    return _mapping(value, label=label), _sha256_bytes(payload)


def _annotation_owner(record: Mapping[str, object]) -> str | None:
    owner = record.get("annotation_owner")
    return owner.strip().casefold() if isinstance(owner, str) and owner.strip() else None


def _aware_iso_datetime(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise GoldEvaluationError(f"{label} must be a non-empty ISO datetime")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise GoldEvaluationError(f"{label} is not an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GoldEvaluationError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


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
    *,
    expected_count: int = 100,
    expected_start_index: int = 1,
    expected_sample_group: str | None = None,
    design: EventEvaluationDesign | None = None,
) -> dict[int, JsonObject]:
    """Validate owner labels and recompute every frozen text/input hash."""

    evaluation = _mapping(contract.document.get("evaluation"), label="evaluation")
    if expected_count <= 0 or expected_start_index <= 0:
        raise GoldEvaluationError("owner annotation expected range is invalid")
    if expected_count == 100 and evaluation.get("sample_count") != 100:
        raise GoldEvaluationError("base annotation sample count drifted")
    if len(records) != expected_count:
        raise GoldEvaluationError(
            "owner annotation sample must contain exactly "
            f"{expected_count} rows, observed {len(records)}"
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
    for expected_index, record in enumerate(records, start=expected_start_index):
        news_item_id = record.get("news_item_id")
        if isinstance(news_item_id, bool) or not isinstance(news_item_id, int) or news_item_id <= 0:
            raise GoldEvaluationError("annotation news_item_id must be a positive integer")
        if gold_builder.MODEL_PREDICTION_KEYS.intersection(record):
            raise GoldEvaluationError(
                f"annotation news item {news_item_id} contains model predictions"
            )
        heldout_record = (
            expected_sample_group == "heldout40"
            or record.get("sample_group") == "heldout40"
        )
        provenance_fields = (
            {"annotation_type", "drafter_id", "adjudicator_id"}
            if heldout_record
            and design is not None
            and "heldout_annotation_provenance" in design.document
            else set()
        )
        expected_fields = gold_builder.ANNOTATION_ITEM_FIELDS | provenance_fields
        unexpected_fields = set(record) - expected_fields
        missing_fields = expected_fields - set(record)
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
        if (
            expected_sample_group is not None
            and record.get("sample_group") != expected_sample_group
        ):
            raise GoldEvaluationError(
                f"annotation news item {news_item_id} sample group drifted"
            )
        if record.get("sample_version") != "p4.2a-gold-v1":
            raise GoldEvaluationError(f"annotation news item {news_item_id} version drifted")
        if record.get("contract_sha256") != contract.sha256:
            raise GoldEvaluationError(f"annotation news item {news_item_id} contract drifted")
        stratum = record.get("stratum")
        body_evidence = record.get("body_evidence")
        if (
            not isinstance(stratum, Mapping)
            or set(stratum) != gold_builder.STRATUM_FIELDS
            or not isinstance(body_evidence, Mapping)
            or set(body_evidence) != gold_builder.BODY_EVIDENCE_FIELDS
        ):
            raise GoldEvaluationError(
                f"annotation news item {news_item_id} nested fields drifted"
            )
        if (
            stratum.get("source") != record.get("source")
            or stratum.get("symbol_state")
            != ("null" if record.get("ingested_symbol") is None else "bound")
            or stratum.get("require_announcement_body") is not body_evidence.get("required")
        ):
            raise GoldEvaluationError(
                f"annotation news item {news_item_id} nested identity drifted"
            )
        try:
            gold_builder.validate_body_evidence(
                record,
                label=f"annotation news item {news_item_id}",
            )
        except gold_builder.GoldSampleError as exc:
            raise GoldEvaluationError(str(exc)) from exc
        if record.get("annotation_status") not in COMPLETED_ANNOTATION_STATUSES:
            raise GoldEvaluationError(f"annotation news item {news_item_id} is not completed")
        if provenance_fields:
            try:
                validate_heldout_annotation_provenance(record, design)
            except EventEvaluationDesignError as exc:
                raise GoldEvaluationError(
                    f"annotation news item {news_item_id} provenance failed: {exc}"
                ) from exc
        elif design is None or design.document.get("schema_version") == (
            "p4.2a-evaluation-design-v1.1"
        ):
            if _annotation_owner(record) != "owner":
                raise GoldEvaluationError(
                    f"annotation news item {news_item_id} is not owner-labelled"
                )
        elif _annotation_owner(record) is None:
            raise GoldEvaluationError(
                f"annotation news item {news_item_id} has no annotator identity"
            )
        _aware_iso_datetime(
            record.get("annotated_at"),
            label=f"annotation news item {news_item_id} annotated_at",
        )
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
    active_contract: EventExtractContract,
    expected_prediction_contract_sha256: str,
    expected_model: str,
) -> JsonObject | None:
    news_item_id = annotation["news_item_id"]
    if row.get("contract_sha256") != expected_prediction_contract_sha256:
        raise GoldEvaluationError(
            f"prediction news item {news_item_id} active contract hash differs"
        )
    if row.get("model") != expected_model:
        raise GoldEvaluationError(f"prediction news item {news_item_id} model differs")
    if active_contract.evidence_candidate_selection:
        active_input_sha256 = row.get("input_sha256")
        declared_input_sha256 = row.get("declared_input_sha256")
        if (
            not isinstance(active_input_sha256, str)
            or SHA256_PATTERN.fullmatch(active_input_sha256) is None
            or declared_input_sha256 != annotation.get("input_sha256")
            or active_input_sha256 == declared_input_sha256
        ):
            raise GoldEvaluationError(
                f"prediction news item {news_item_id} dual input hash differs"
            )
    elif row.get("input_sha256") != annotation.get("input_sha256"):
        raise GoldEvaluationError(
            f"prediction news item {news_item_id} input hash differs"
        )
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
        or not evidence_span_matches(active_contract, evidence, original_text)
    ):
        raise GoldEvaluationError(
            f"prediction evidence_span for news item {news_item_id} is not in frozen text"
        )
    return candidate


def join_predictions(
    prediction_records: Sequence[JsonObject],
    annotations: Mapping[int, JsonObject],
    contract: gold_builder.FrozenContract,
    *,
    active_contract: EventExtractContract | None = None,
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

    if active_contract is None:
        event_contract = load_event_extract_contract(contract.path)
    else:
        event_contract = active_contract
    schema = persisted_prediction_schema(event_contract)
    expected_contract_sha256 = getattr(event_contract, "sha256", None)
    if (
        not isinstance(expected_contract_sha256, str)
        or SHA256_PATTERN.fullmatch(expected_contract_sha256) is None
    ):
        raise GoldEvaluationError("active prediction contract is invalid")
    validator = Draft202012Validator(schema)
    joined = {
        news_item_id: _prediction_payload(
            predictions_by_id[news_item_id],
            _mapping(item["record"], label=f"annotation {news_item_id}"),
            validator=validator,
            active_contract=event_contract,
            expected_prediction_contract_sha256=expected_contract_sha256,
            expected_model=event_contract.model,
        )
        for news_item_id, item in annotations.items()
    }
    return joined, len(predictions_by_id) - len(annotations)


def persisted_prediction_schema(
    contract: EventExtractContract,
) -> Mapping[str, object]:
    """Select the schema for canonical predictions already stored on disk."""

    schema = (
        contract.materialized_schema
        if contract.evidence_candidate_selection
        else contract.schema
    )
    if not isinstance(schema, Mapping):
        representation = (
            "materialized" if contract.evidence_candidate_selection else "raw"
        )
        raise GoldEvaluationError(
            f"active prediction contract lacks {representation} persisted schema"
        )
    return schema


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


def _split_metrics(
    identifiers: Sequence[int],
    annotations: Mapping[int, JsonObject],
    predictions: Mapping[int, JsonObject | None],
) -> JsonObject:
    tp = fp = fn = tn = 0
    symbol_exact = symbol_bearing_exact = symbol_bearing_count = 0
    successful = predicted_positive_count = 0
    null_gold_count = null_gold_false_positive = 0
    prediction_failure_ids: list[int] = []
    for news_item_id in identifiers:
        item = annotations[news_item_id]
        gold = _mapping(item["gold"], label=f"gold {news_item_id}")
        prediction = predictions[news_item_id]
        gold_positive = int(gold["materiality"]) >= 2
        predicted_positive = prediction is not None and int(prediction["materiality"]) >= 2
        if predicted_positive:
            predicted_positive_count += 1
        if gold_positive and predicted_positive:
            tp += 1
        elif not gold_positive and predicted_positive:
            fp += 1
        elif gold_positive:
            fn += 1
        else:
            tn += 1

        gold_symbols = list(gold["symbols"])
        if gold_symbols:
            symbol_bearing_count += 1
        else:
            null_gold_count += 1
        if prediction is None:
            prediction_failure_ids.append(news_item_id)
            continue
        successful += 1
        predicted_symbols = list(prediction["symbols"])
        if predicted_symbols == gold_symbols:
            symbol_exact += 1
            if gold_symbols:
                symbol_bearing_exact += 1
        if not gold_symbols and predicted_symbols:
            null_gold_false_positive += 1
    precision_denominator = tp + fp
    recall_denominator = tp + fn
    return {
        "sample_count": len(identifiers),
        "prediction_success_count": successful,
        "prediction_failure_count": len(prediction_failure_ids),
        "prediction_failure_ids": prediction_failure_ids,
        "predicted_positive_count": predicted_positive_count,
        "predicted_positive_rate_of_successes": _ratio(
            predicted_positive_count,
            successful,
        ),
        "materiality_precision": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "denominator": precision_denominator,
            "value": _ratio(tp, precision_denominator),
        },
        "materiality_recall": {
            "tp": tp,
            "fn": fn,
            "denominator": recall_denominator,
            "value": _ratio(tp, recall_denominator),
        },
        "symbol_exact_set": {
            "matches": symbol_exact,
            "denominator": len(identifiers),
            "value": _ratio(symbol_exact, len(identifiers)),
        },
        "symbol_bearing_exact_set": {
            "matches": symbol_bearing_exact,
            "denominator": symbol_bearing_count,
            "value": _ratio(symbol_bearing_exact, symbol_bearing_count),
        },
        "null_gold_symbol_false_positive": {
            "count": null_gold_false_positive,
            "denominator": null_gold_count,
            "value": _ratio(null_gold_false_positive, null_gold_count),
        },
    }


def evaluate_split_records(
    annotations: Mapping[int, JsonObject],
    predictions: Mapping[int, JsonObject | None],
    design: gold_builder.FrozenEvaluationDesign,
) -> JsonObject:
    """Compute v1.1 diagnostics and apply gates only to their registered scopes."""

    if list(annotations) != list(predictions) or len(annotations) != 100:
        raise GoldEvaluationError("v1.1 prediction join is not the exact ordered 100 rows")
    identifiers = list(annotations)
    dev_ids = identifiers[:60]
    heldout_ids = identifiers[60:]
    for news_item_id in dev_ids:
        record = _mapping(annotations[news_item_id]["record"], label="dev annotation")
        if record.get("sample_index") not in range(1, 61):
            raise GoldEvaluationError("dev60 annotation split/order drifted")
    for news_item_id in heldout_ids:
        record = _mapping(annotations[news_item_id]["record"], label="heldout annotation")
        if record.get("sample_group") != "heldout40":
            raise GoldEvaluationError("heldout40 annotation split drifted")

    split_results = {
        "dev60": _split_metrics(dev_ids, annotations, predictions),
        "heldout40": _split_metrics(heldout_ids, annotations, predictions),
        "all100": _split_metrics(identifiers, annotations, predictions),
    }
    evaluation = _mapping(design.document.get("evaluation"), label="evaluation")
    heldout_precision = _mapping(
        split_results["heldout40"]["materiality_precision"],
        label="heldout materiality precision",
    )
    all_symbols = _mapping(
        split_results["all100"]["symbol_exact_set"],
        label="all100 symbol exact set",
    )
    all_symbol_bearing = _mapping(
        split_results["all100"]["symbol_bearing_exact_set"],
        label="all100 symbol-bearing exact set",
    )
    precision_value = heldout_precision["value"]
    symbol_value = all_symbols["value"]
    bearing_value = all_symbol_bearing["value"]
    precision_passed = isinstance(precision_value, (int, float)) and precision_value >= float(
        evaluation["materiality_precision_minimum"]
    )
    symbol_passed = isinstance(symbol_value, (int, float)) and symbol_value >= float(
        evaluation["symbol_mapping_accuracy_minimum"]
    )
    bearing_passed = isinstance(bearing_value, (int, float)) and bearing_value >= float(
        evaluation["symbol_bearing_exact_set_accuracy_minimum"]
    )
    heldout_precision["threshold"] = float(evaluation["materiality_precision_minimum"])
    heldout_precision["passed"] = precision_passed
    split_results["heldout40"]["materiality_precision"] = heldout_precision
    gates: JsonObject = {
        "materiality_precision_heldout40": precision_passed,
        "symbol_mapping_all100": symbol_passed,
        "symbol_bearing_exact_set_all100": bearing_passed,
        "owner_delivery_blind": True,
        "heldout_one_shot": True,
    }
    human_provenance = "heldout_annotation_provenance" in design.document
    if human_provenance:
        metrics: JsonObject = {
            "annotation_semantics": {
                "dev60": {
                    "annotation_type": "ai_drafted_dev_signal",
                    "metric_semantics": "model_interagreement",
                    "human_ground_truth": False,
                },
                "heldout40": {
                    "annotation_type": "ai_drafted_human_adjudicated",
                    "metric_semantics": "human_adjudicated_gold",
                    "human_ground_truth": True,
                },
                "all100": {
                    "annotation_type": "mixed_ai_dev_and_human_heldout",
                    "metric_semantics": "mixed_reference_diagnostic",
                    "human_ground_truth": False,
                },
            },
            "materiality_model_interagreement": {
                "dev60": split_results["dev60"]["materiality_precision"],
            },
            "materiality_precision": {
                "heldout40": split_results["heldout40"]["materiality_precision"],
            },
            "materiality_mixed_reference_diagnostic": {
                "all100": split_results["all100"]["materiality_precision"],
            },
            "materiality_model_interagreement_recall": {
                "dev60": split_results["dev60"]["materiality_recall"],
            },
            "materiality_recall": {
                "heldout40": split_results["heldout40"]["materiality_recall"],
            },
            "materiality_mixed_reference_recall_diagnostic": {
                "all100": split_results["all100"]["materiality_recall"],
            },
            "symbol_exact_set": {
                name: result["symbol_exact_set"]
                for name, result in split_results.items()
            },
            "symbol_bearing_exact_set": {
                name: result["symbol_bearing_exact_set"]
                for name, result in split_results.items()
            },
        }
    else:
        metrics = {
            "materiality_precision": {
                name: result["materiality_precision"]
                for name, result in split_results.items()
            },
            "materiality_recall": {
                name: result["materiality_recall"]
                for name, result in split_results.items()
            },
            "symbol_exact_set": {
                name: result["symbol_exact_set"]
                for name, result in split_results.items()
            },
            "symbol_bearing_exact_set": {
                name: result["symbol_bearing_exact_set"]
                for name, result in split_results.items()
            },
        }
    return {
        "splits": split_results,
        "metrics": metrics,
        "gates": gates,
        "passed": all(bool(value) for value in gates.values()),
    }


def _offline_trial_diagnostics(
    design: gold_builder.FrozenEvaluationDesign,
    gold_ids: set[int],
) -> JsonObject:
    path = gold_builder.evaluation_artifact_path(
        design,
        "offline_trial_predictions_jsonl",
    )
    rows, artifact_sha256 = _read_jsonl(path, label="frozen offline trial predictions")
    successes = [
        row
        for row in rows
        if row.get("status") == "ok" and isinstance(row.get("prediction"), Mapping)
    ]
    positives = [
        row
        for row in successes
        if int(_mapping(row["prediction"], label="offline prediction")["materiality"]) >= 2
    ]
    failures = sorted(
        int(row["news_item_id"])
        for row in rows
        if row.get("status") != "ok" and isinstance(row.get("news_item_id"), int)
    )
    intersection = sorted(gold_ids.intersection(failures))
    evaluation = _mapping(design.document.get("evaluation"), label="evaluation")
    frozen = _mapping(evaluation.get("frozen_diagnostics"), label="frozen_diagnostics")
    if intersection != frozen.get("offline_trial_gold_intersection_failure_ids"):
        raise GoldEvaluationError(
            "frozen offline-trial gold-intersection failures drifted"
        )
    return {
        "artifact_path": str(path.relative_to(PROJECT_DIR)),
        "artifact_sha256": artifact_sha256,
        "successful_prediction_count": len(successes),
        "predicted_materiality_gte_2_count": len(positives),
        "predicted_materiality_gte_2_rate": _ratio(len(positives), len(successes)),
        "all_failure_count": len(failures),
        "all_failure_ids": failures,
        "gold_intersection_failure_count": len(intersection),
        "gold_intersection_failure_ids": intersection,
        "failure_counts_for_recall_not_precision": True,
    }


def _new_report_path(path: Path, artifact_root: Path) -> Path:
    if artifact_root.is_symlink():
        raise GoldEvaluationError("eval artifact root must not be a symlink")
    root = artifact_root.resolve()
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


def _validate_report_path_readonly(path: Path, artifact_root: Path) -> Path:
    """Validate a prospective report path without creating any filesystem entry."""

    if artifact_root.is_symlink():
        raise GoldEvaluationError(
            "eval artifact root must be an existing non-symlink directory"
        )
    root = artifact_root.resolve()
    if not root.is_dir():
        raise GoldEvaluationError(
            "eval artifact root must be an existing non-symlink directory"
        )
    unresolved = path if path.is_absolute() else PROJECT_DIR / path
    current = unresolved
    while current != root and current != current.parent:
        if current.is_symlink():
            raise GoldEvaluationError(
                "evaluation report path must not traverse a symlink"
            )
        current = current.parent
    resolved = unresolved.resolve()
    if not resolved.is_relative_to(root):
        raise GoldEvaluationError("evaluation report must stay under docs/phase4/eval")
    if resolved.exists() or resolved.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite P4.2a evaluation report: {resolved}"
        )
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


def _one_shot_event_bytes(
    *,
    event: str,
    design_sha256: str,
    at_utc: str,
    details: Mapping[str, object] | None = None,
) -> bytes:
    payload: JsonObject = {
        "event": event,
        "design_sha256": design_sha256,
        "at_utc": at_utc,
    }
    if details:
        payload.update({str(key): value for key, value in details.items()})
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
    )


def _claim_evaluation_one_shot(
    path: Path,
    *,
    design_sha256: str,
    started_at_utc: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise GoldEvaluationError("evaluation one-shot directory must not be a symlink")
    payload = _one_shot_event_bytes(
        event="evaluation_started",
        design_sha256=design_sha256,
        at_utc=started_at_utc,
    )
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise GoldEvaluationError(
            "heldout evaluation already has a started event; reevaluation is forbidden"
        ) from exc
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _append_evaluation_terminal(
    path: Path,
    *,
    design_sha256: str,
    event: str,
    at_utc: str,
    details: Mapping[str, object] | None = None,
) -> None:
    if event not in {"evaluation_completed", "evaluation_failed"}:
        raise ValueError("invalid evaluation terminal event")
    if not path.is_file() or path.is_symlink():
        raise GoldEvaluationError("evaluation one-shot started state disappeared")
    payload = _one_shot_event_bytes(
        event=event,
        design_sha256=design_sha256,
        at_utc=at_utc,
        details=details,
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _materialization_binding_for_evaluation(
    *,
    design: gold_builder.FrozenEvaluationDesign,
    active_contract: EventExtractContract,
    receipt_sha256: str,
    candidate_rows: Sequence[gold_builder.NewsRow],
    candidate_records: Sequence[Mapping[str, object]],
) -> tuple[list[gold_builder.NewsRow], JsonObject | None, frozenset[int]]:
    """Validate v1.7's immutable partition and return only eligible DB rows.

    Earlier designs predate document-eligibility materialization, so their
    candidate batch remains the full database slice.  Once a design registers
    ``candidate_eligibility``, absence or drift of any materialization evidence
    is terminal: evaluation must not silently fall back to the raw pool.
    """

    if "candidate_eligibility" not in design.document:
        return list(candidate_rows), None, frozenset()

    manifest_path = gold_builder.evaluation_artifact_path(
        design,
        "heldout_candidate_materialization_manifest_json",
    )
    manifest, manifest_sha256 = _read_json(
        manifest_path,
        label="heldout candidate materialization manifest",
    )
    try:
        validated = gold_builder.validate_heldout_materialization_manifest(
            manifest,
            rows=candidate_rows,
            eligible_records=candidate_records,
            design=design,
            active_contract=active_contract,
            freeze_receipt_sha256=receipt_sha256,
            project_root=PROJECT_DIR,
        )
    except gold_builder.GoldSampleError as exc:
        raise GoldEvaluationError(
            "heldout candidate materialization manifest is invalid"
        ) from exc

    layers = _mapping(validated.get("layers"), label="materialization layers")
    raw_eligible = layers.get("eligible_candidates")
    raw_ineligible = layers.get("ineligible_candidates")
    if (
        not isinstance(raw_eligible, list)
        or not isinstance(raw_ineligible, list)
        or any(not isinstance(item, Mapping) for item in (*raw_eligible, *raw_ineligible))
    ):
        raise GoldEvaluationError("materialization candidate layers are invalid")
    raw_ineligible_ids = [
        _mapping(item, label="ineligible materialization candidate").get("news_item_id")
        for item in raw_ineligible
    ]
    ineligible_ids: list[int] = []
    for news_item_id in raw_ineligible_ids:
        if isinstance(news_item_id, bool) or not isinstance(news_item_id, int):
            raise GoldEvaluationError("materialization candidate IDs are invalid")
        ineligible_ids.append(news_item_id)
    try:
        eligible_rows = list(
            gold_builder.heldout_eligible_rows_from_materialization(
                candidate_rows,
                validated,
            )
        )
        binding = gold_builder.heldout_materialization_binding(
            validated,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            project_root=PROJECT_DIR,
        )
    except gold_builder.GoldSampleError as exc:
        raise GoldEvaluationError(
            "heldout materialization partition binding is invalid"
        ) from exc
    record_ids = [record.get("news_item_id") for record in candidate_records]
    if record_ids != [row.news_item_id for row in eligible_rows]:
        raise GoldEvaluationError(
            "heldout candidate inputs are not the exact eligible materialization layer"
        )

    counts = _mapping(validated.get("counts"), label="materialization counts")
    reason_counts = _mapping(
        counts.get("ineligible_by_reason"),
        label="materialization ineligible reason counts",
    )
    expected_counts = {
        "all_candidates": len(candidate_rows),
        "eligible_candidates": len(eligible_rows),
        "ineligible_candidates": len(ineligible_ids),
        "ineligible_by_reason": dict(reason_counts),
    }
    if counts != expected_counts or binding != {
        "manifest_path": str(manifest_path.relative_to(PROJECT_DIR)),
        "manifest_sha256": manifest_sha256,
        "raw_candidate_count": len(candidate_rows),
        "eligible_candidate_count": len(eligible_rows),
        "ineligible_candidate_count": len(ineligible_ids),
        "ineligible_by_reason": dict(reason_counts),
    }:
        raise GoldEvaluationError("materialization manifest counts drifted")
    return eligible_rows, binding, frozenset(ineligible_ids)


def _require_materialization_binding(
    container: Mapping[str, object],
    *,
    expected: Mapping[str, object] | None,
    label: str,
) -> None:
    actual = container.get("materialization")
    if expected is None:
        if actual is not None:
            raise GoldEvaluationError(f"{label} has unregistered materialization evidence")
        return
    if not isinstance(actual, Mapping) or dict(actual) != dict(expected):
        raise GoldEvaluationError(f"{label} materialization binding drifted")


def _validate_selection_manifest(
    manifest: JsonObject,
    *,
    manifest_sha256: str,
    design: gold_builder.FrozenEvaluationDesign,
    annotations: Mapping[int, JsonObject],
    active_contract: EventExtractContract,
    receipt_sha256: str,
    candidate_inputs_sha256: str,
    candidate_predictions_sha256: str,
    candidate_prediction_manifest_sha256: str,
    inference_state_sha256: str,
    materialization_binding: Mapping[str, object] | None = None,
    ineligible_ids: frozenset[int] = frozenset(),
) -> JsonObject:
    expected_schema = (
        "p4.2a-heldout-selection-manifest-v1.2"
        if materialization_binding is not None
        else "p4.2a-heldout-selection-manifest-v1.1"
    )
    if manifest.get("schema_version") != expected_schema:
        raise GoldEvaluationError("heldout selection manifest schema drifted")
    design_binding = _mapping(manifest.get("design"), label="selection manifest design")
    annotation_binding = _mapping(
        manifest.get("annotation_contract"),
        label="selection manifest annotation contract",
    )
    prediction_binding = _mapping(
        manifest.get("prediction_contract"),
        label="selection manifest prediction contract",
    )
    inference = _mapping(manifest.get("inference"), label="selection manifest inference")
    candidate_inputs = _mapping(
        manifest.get("candidate_inputs"),
        label="selection manifest candidate inputs",
    )
    candidate_predictions = _mapping(
        manifest.get("candidate_predictions"),
        label="selection manifest candidate predictions",
    )
    pool = _mapping(manifest.get("eligible_pool"), label="selection manifest eligible pool")
    selection = _mapping(manifest.get("selection"), label="selection manifest selection")
    owner = _mapping(manifest.get("owner_delivery"), label="selection manifest owner delivery")
    _require_materialization_binding(
        manifest,
        expected=materialization_binding,
        label="heldout selection manifest",
    )
    if (
        not gold_builder.design_identity_is_byte_frozen(
            design,
            actual_sha256=design_binding.get("sha256"),
            actual_schema_version=design_binding.get("schema_version"),
            required_scopes=(
                gold_builder.HELDOUT_EVALUATION_INPUT_DESIGN_SCOPES
            ),
        )
        or annotation_binding.get("sha256") != design.base_contract.sha256
        or prediction_binding.get("contract_sha256") != active_contract.sha256
        or prediction_binding.get("freeze_receipt_sha256") != receipt_sha256
        or inference.get("state_sha256") != inference_state_sha256
        or candidate_inputs.get("sha256") != candidate_inputs_sha256
        or candidate_predictions.get("sha256") != candidate_predictions_sha256
        or candidate_predictions.get("manifest_sha256")
        != candidate_prediction_manifest_sha256
        or candidate_inputs.get("cninfo_bodies_frozen_before_prediction") is not True
        or owner.get("predictions_visible") is not False
        or owner.get("selection_basis_visible") is not False
        or owner.get("forbidden_field_violation_count") != 0
    ):
        raise GoldEvaluationError("heldout selection manifest artifact bindings drifted")
    selected = selection.get("selected")
    if not isinstance(selected, list) or len(selected) != 40:
        raise GoldEvaluationError("heldout selection manifest must bind 40 selected rows")
    heldout_ids = list(annotations)[60:]
    selected_ids: list[int] = []
    seed = selection.get("seed")
    if not isinstance(seed, str):
        raise GoldEvaluationError("heldout selection seed is invalid")
    for raw in selected:
        item = _mapping(raw, label="selection manifest selected row")
        news_item_id = item.get("news_item_id")
        if not isinstance(news_item_id, int) or news_item_id not in annotations:
            raise GoldEvaluationError("heldout selection manifest contains unknown ID")
        if news_item_id in ineligible_ids:
            raise GoldEvaluationError(
                "heldout selection manifest contains a materialization-ineligible ID"
            )
        record = _mapping(annotations[news_item_id]["record"], label="heldout annotation")
        selection_input_sha256 = item.get("input_sha256")
        declared_input_matches = (
            item.get("declared_input_sha256") == record.get("input_sha256")
            and selection_input_sha256 != item.get("declared_input_sha256")
            if active_contract.evidence_candidate_selection
            else selection_input_sha256 == record.get("input_sha256")
        )
        if (
            not isinstance(selection_input_sha256, str)
            or SHA256_PATTERN.fullmatch(selection_input_sha256) is None
            or not declared_input_matches
            or item.get("text_sha256") != record.get("text_sha256")
            or item.get("selection_rank_sha256")
            != gold_builder.heldout_prediction_rank(
                seed=seed,
                news_item_id=news_item_id,
                input_sha256=selection_input_sha256,
            )
        ):
            raise GoldEvaluationError(
                f"heldout selection manifest identity/rank drifted for {news_item_id}"
            )
        selected_ids.append(news_item_id)
    if selected_ids != heldout_ids or len(set(selected_ids)) != 40:
        raise GoldEvaluationError("heldout annotation order differs from frozen selection")
    success_count = candidate_predictions.get("success_count")
    pool_count = pool.get("count")
    expected_rate = (
        pool_count / success_count
        if isinstance(pool_count, int)
        and isinstance(success_count, int)
        and success_count > 0
        else None
    )
    if (
        selection.get("selected_count") != 40
        or pool.get("positive_rate_denominator") != "successful_predictions"
        or pool.get("positive_rate") != expected_rate
    ):
        raise GoldEvaluationError("heldout positive-pool statistics drifted")
    evidence: JsonObject = {
        "manifest_sha256": manifest_sha256,
        "selection_manifest_sha256": manifest_sha256,
        "candidate_batch_count": candidate_inputs.get("count"),
        "candidate_inputs_sha256": candidate_inputs_sha256,
        "candidate_predictions_sha256": candidate_predictions_sha256,
        "prediction_attempted_count": candidate_predictions.get("attempted_count"),
        "prediction_success_count": success_count,
        "prediction_failure_count": candidate_predictions.get("failure_count"),
        "predicted_positive_pool_count": pool_count,
        "predicted_positive_pool_rate": pool.get("positive_rate"),
        "selected_count": selection.get("selected_count"),
        "selection_algorithm": selection.get("algorithm"),
        "selection_seed": seed,
    }
    if materialization_binding is not None:
        if candidate_inputs.get("count") != materialization_binding.get(
            "eligible_candidate_count"
        ):
            raise GoldEvaluationError(
                "heldout selection candidate count differs from the eligible pool"
            )
        evidence["materialization"] = dict(materialization_binding)
    return evidence


def validate_owner_completion_manifest(
    design: gold_builder.FrozenEvaluationDesign,
    *,
    annotation_path: Path,
    annotation_records: Sequence[JsonObject],
    annotation_sha256: str,
    project_root: Path = PROJECT_DIR,
) -> JsonObject:
    """Verify owner completion bytes, counts, identity, and blindness before scoring."""

    root = project_root.resolve()
    completion = _mapping(
        design.document.get("owner_annotation_completion"),
        label="owner_annotation_completion",
    )
    required = completion.get("required_manifest_fields")
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise GoldEvaluationError("owner completion required fields are invalid")

    def artifact(name: str) -> Path:
        return gold_builder.evaluation_artifact_path(
            design,
            name,
            project_root=root,
        )

    dev_blind_path = artifact("dev_60_frozen_jsonl")
    dev_annotations_path = artifact("dev_60_owner_annotations_jsonl")
    heldout_blind_path = artifact("heldout_40_blind_sample_jsonl")
    heldout_selection_path = artifact("heldout_selection_manifest_json")
    heldout_annotations_path = artifact("heldout_40_owner_annotations_jsonl")
    combined_path = artifact("combined_100_annotations_jsonl")
    manifest_path = artifact("owner_completion_manifest_json")
    if annotation_path.resolve() != combined_path:
        raise GoldEvaluationError(
            "active v1.1 evaluation must use the registered combined owner artifact"
        )

    manifest, manifest_sha256 = _read_json(
        manifest_path,
        label="owner completion manifest",
    )
    if set(manifest) != set(required):
        raise GoldEvaluationError("owner completion manifest fields drifted")
    if (
        manifest.get("schema_version") != completion.get("schema_version")
        or not gold_builder.design_sha256_is_byte_frozen(
            design,
            manifest.get("design_sha256"),
            required_scopes=(
                gold_builder.HELDOUT_EVALUATION_INPUT_DESIGN_SCOPES
            ),
        )
        or manifest.get("annotation_contract_sha256") != design.base_contract.sha256
    ):
        raise GoldEvaluationError("owner completion contract binding drifted")
    _aware_iso_datetime(
        manifest.get("completed_at_utc"),
        label="owner completion completed_at_utc",
    )

    dev_blind, dev_blind_sha256 = _read_jsonl(
        dev_blind_path,
        label="frozen dev60 blind sample",
    )
    dev_annotations, dev_annotations_sha256 = _read_jsonl(
        dev_annotations_path,
        label="completed dev60 owner annotations",
    )
    heldout_blind, heldout_blind_sha256 = _read_jsonl(
        heldout_blind_path,
        label="frozen heldout40 blind sample",
    )
    heldout_selection, heldout_selection_sha256 = _read_json(
        heldout_selection_path,
        label="heldout selection manifest",
    )
    heldout_annotations, heldout_annotations_sha256 = _read_jsonl(
        heldout_annotations_path,
        label="completed heldout40 owner annotations",
    )
    combined, combined_sha256 = _read_jsonl(
        combined_path,
        label="combined owner annotations",
    )
    if (
        combined_sha256 != annotation_sha256
        or combined != list(annotation_records)
    ):
        raise GoldEvaluationError("combined annotation bytes differ from evaluation input")

    artifacts = _mapping(design.document.get("artifacts"), label="artifacts")
    dev_frozen = _mapping(
        artifacts.get("dev_60_frozen_jsonl"),
        label="artifacts.dev_60_frozen_jsonl",
    )
    if dev_frozen.get("sha256") != dev_blind_sha256:
        raise GoldEvaluationError("frozen dev60 bytes differ from the evaluation design")
    dev_annotation_artifact = _mapping(
        artifacts.get("dev_60_owner_annotations_jsonl"),
        label="artifacts.dev_60_owner_annotations_jsonl",
    )
    expected_dev_annotation_sha256 = dev_annotation_artifact.get("sha256")
    if (
        expected_dev_annotation_sha256 is not None
        and expected_dev_annotation_sha256 != dev_annotations_sha256
    ):
        raise GoldEvaluationError(
            "dev60 AI-drafted annotations differ from the evaluation design"
        )

    path_bindings: dict[str, Path] = {
        "dev_blind_sample_path": dev_blind_path,
        "dev_owner_annotations_path": dev_annotations_path,
        "heldout_blind_sample_path": heldout_blind_path,
        "heldout_selection_manifest_path": heldout_selection_path,
        "heldout_owner_annotations_path": heldout_annotations_path,
        "combined_annotations_path": combined_path,
    }
    for field, path in path_bindings.items():
        if manifest.get(field) != str(path.relative_to(root)):
            raise GoldEvaluationError(f"owner completion {field} drifted")

    combined_contract = _mapping(
        completion.get("combined"),
        label="owner_annotation_completion.combined",
    )
    renumbering_rule = combined_contract.get("renumbering_rule")
    expected_values: dict[str, object] = {
        "dev_blind_sample_sha256": dev_blind_sha256,
        "dev_owner_annotations_sha256": dev_annotations_sha256,
        "dev_owner_annotations_row_count": len(dev_annotations),
        "dev_completed_count": len(dev_annotations),
        "heldout_blind_sample_sha256": heldout_blind_sha256,
        "heldout_selection_manifest_sha256": heldout_selection_sha256,
        "heldout_owner_annotations_sha256": heldout_annotations_sha256,
        "heldout_owner_annotations_row_count": len(heldout_annotations),
        "heldout_completed_count": len(heldout_annotations),
        "combined_annotations_sha256": combined_sha256,
        "combined_annotations_row_count": len(combined),
        "combined_ordered_identity_sha256": (
            gold_builder._combined_ordered_identity_sha256(combined)
        ),
        "combined_renumbering_rule": renumbering_rule,
        "identity_validation_passed": True,
        "blindness_validation_passed": True,
    }
    if "heldout_annotation_provenance" in design.document:
        expected_values.update(
            {
                "heldout_annotation_type": "ai_drafted_human_adjudicated",
                "heldout_drafter_ids": sorted(
                    {
                        str(record.get("drafter_id")).strip()
                        for record in heldout_annotations
                    }
                ),
                "heldout_adjudicator_ids": sorted(
                    {
                        str(record.get("adjudicator_id")).strip()
                        for record in heldout_annotations
                    }
                ),
                "heldout_human_adjudication_validated": True,
            }
        )
    for field, expected in expected_values.items():
        if manifest.get(field) != expected:
            raise GoldEvaluationError(f"owner completion {field} drifted")
    if (
        len(dev_blind) != 60
        or len(dev_annotations) != 60
        or len(heldout_blind) != 40
        or len(heldout_annotations) != 40
        or len(combined) != 100
        or manifest.get("dev_completed_count") != 60
        or manifest.get("heldout_completed_count") != 40
    ):
        raise GoldEvaluationError("owner completion split counts drifted")

    for record in [*dev_blind, *heldout_blind]:
        try:
            gold_builder.validate_blind_record(record, design.base_contract)
        except gold_builder.GoldSampleError as exc:
            raise GoldEvaluationError(str(exc)) from exc
    try:
        gold_builder._validate_completed_identity_against_blind(
            blind_records=dev_blind,
            owner_records=dev_annotations,
            label="dev60",
        )
        gold_builder._validate_completed_identity_against_blind(
            blind_records=heldout_blind,
            owner_records=heldout_annotations,
            label="heldout40",
        )
    except gold_builder.GoldSampleError as exc:
        raise GoldEvaluationError(str(exc)) from exc
    validate_owner_annotations(
        dev_annotations,
        design.base_contract,
        expected_count=60,
        expected_start_index=1,
        expected_sample_group="inventory_60",
        design=design,
    )
    validate_owner_annotations(
        heldout_annotations,
        design.base_contract,
        expected_count=40,
        expected_start_index=1,
        expected_sample_group="heldout40",
        design=design,
    )
    if any(record.get("annotation_status") != "completed" for record in combined):
        raise GoldEvaluationError("canonical owner annotations must use completed status")

    expected_combined: list[JsonObject] = [dict(record) for record in dev_annotations]
    expected_combined.extend(
        {**record, "sample_index": index}
        for index, record in enumerate(heldout_annotations, start=61)
    )
    if combined != expected_combined:
        raise GoldEvaluationError("combined owner annotations violate the frozen renumbering")
    owner = _mapping(design.document.get("owner_delivery"), label="owner_delivery")
    forbidden = owner.get("forbidden_fields")
    if not isinstance(forbidden, list) or any(not isinstance(item, str) for item in forbidden):
        raise GoldEvaluationError("owner forbidden-field contract is invalid")
    forbidden_paths = gold_builder.owner_forbidden_field_paths(
        [*dev_annotations, *heldout_annotations, *combined],
        frozenset(forbidden),
    )
    if forbidden_paths:
        raise GoldEvaluationError(
            f"owner completion leaks prediction/selection fields: {forbidden_paths[:3]}"
        )
    selection_owner = _mapping(
        heldout_selection.get("owner_delivery"),
        label="heldout selection owner_delivery",
    )
    heldout_selection_design = _mapping(
        heldout_selection.get("design"),
        label="heldout selection design",
    )
    heldout_selection_schema = heldout_selection_design.get("schema_version")
    heldout_selection_design_matches = (
        gold_builder.design_sha256_is_byte_frozen(
            design,
            heldout_selection_design.get("sha256"),
            required_scopes=(
                gold_builder.HELDOUT_EVALUATION_INPUT_DESIGN_SCOPES
            ),
        )
        if heldout_selection_schema is None
        else gold_builder.design_identity_is_byte_frozen(
            design,
            actual_sha256=heldout_selection_design.get("sha256"),
            actual_schema_version=heldout_selection_schema,
            required_scopes=(
                gold_builder.HELDOUT_EVALUATION_INPUT_DESIGN_SCOPES
            ),
        )
    )
    if (
        not heldout_selection_design_matches
        or selection_owner.get("heldout_blind_sample_path")
        != str(heldout_blind_path.relative_to(root))
        or selection_owner.get("heldout_blind_sample_sha256") != heldout_blind_sha256
        or selection_owner.get("heldout_blind_sample_count") != 40
    ):
        raise GoldEvaluationError("heldout selection no longer binds its blind sample")

    return {
        "manifest_path": str(manifest_path.relative_to(root)),
        "manifest_sha256": manifest_sha256,
        "dev_blind_sample_sha256": dev_blind_sha256,
        "dev_annotations_sha256": dev_annotations_sha256,
        "dev_completed_count": 60,
        "heldout_blind_sample_sha256": heldout_blind_sha256,
        "heldout_selection_manifest_sha256": heldout_selection_sha256,
        "heldout_annotations_sha256": heldout_annotations_sha256,
        "heldout_completed_count": 40,
        "combined_annotations_sha256": combined_sha256,
        "combined_row_count": 100,
        "combined_ordered_identity_sha256": expected_values[
            "combined_ordered_identity_sha256"
        ],
        "combined_renumbering_rule": renumbering_rule,
        "identity_validation_passed": True,
        "blindness_validation_passed": True,
        **(
            {
                "heldout_annotation_type": manifest[
                    "heldout_annotation_type"
                ],
                "heldout_drafter_ids": manifest["heldout_drafter_ids"],
                "heldout_adjudicator_ids": manifest[
                    "heldout_adjudicator_ids"
                ],
                "heldout_human_adjudication_validated": manifest[
                    "heldout_human_adjudication_validated"
                ],
            }
            if "heldout_annotation_provenance" in design.document
            else {}
        ),
    }


def validate_heldout_adjudication_evidence(
    design: gold_builder.FrozenEvaluationDesign,
    *,
    adjudicated_path: Path,
    ai_draft_path: Path,
    project_root: Path = PROJECT_DIR,
) -> JsonObject:
    """Revalidate the UI audit export and bind it to canonical heldout gold."""

    if "heldout_annotation_provenance" not in design.document:
        raise GoldEvaluationError(
            "heldout adjudication evidence requires a human-provenance design"
        )
    root = project_root.resolve()
    artifact_root_value = design.document.get("artifact_root")
    if not isinstance(artifact_root_value, str) or not artifact_root_value:
        raise GoldEvaluationError("artifact_root must be a non-empty path")
    artifact_root_relative = Path(artifact_root_value)
    if artifact_root_relative.is_absolute() or ".." in artifact_root_relative.parts:
        raise GoldEvaluationError("artifact_root escapes project root")
    artifact_root = (root / artifact_root_relative).resolve()
    if not artifact_root.is_relative_to(root):
        raise GoldEvaluationError("artifact_root escapes project root")

    adjudicated_resolved = adjudicated_path.resolve()
    ai_draft_resolved = ai_draft_path.resolve()
    for label, path in (
        ("heldout adjudication export", adjudicated_resolved),
        ("heldout AI draft", ai_draft_resolved),
    ):
        if not path.is_relative_to(artifact_root):
            raise GoldEvaluationError(f"{label} must stay under docs/phase4/eval")
    if adjudicated_resolved == ai_draft_resolved:
        raise GoldEvaluationError("heldout adjudication export and AI draft must differ")

    blind_path = gold_builder.evaluation_artifact_path(
        design,
        "heldout_40_blind_sample_jsonl",
        project_root=root,
    )
    canonical_path = gold_builder.evaluation_artifact_path(
        design,
        "heldout_40_owner_annotations_jsonl",
        project_root=root,
    )
    blind_records, blind_sha256 = _read_jsonl(
        blind_path,
        label="frozen heldout40 blind sample",
    )
    ai_draft_records, ai_draft_sha256 = _read_jsonl(
        ai_draft_resolved,
        label="heldout40 explicit AI draft",
    )
    adjudicated_records, adjudicated_sha256 = _read_jsonl(
        adjudicated_resolved,
        label="heldout40 adjudication export",
    )
    canonical_records, canonical_sha256 = _read_jsonl(
        canonical_path,
        label="canonical heldout40 owner annotations",
    )
    try:
        reconstructed = gold_builder._normalize_heldout_adjudication_export(
            blind_records=blind_records,
            draft_records=ai_draft_records,
            adjudicated_records=adjudicated_records,
            design=design,
        )
    except gold_builder.GoldSampleError as exc:
        raise GoldEvaluationError(
            f"heldout adjudication evidence validation failed: {exc}"
        ) from exc
    if reconstructed != canonical_records:
        raise GoldEvaluationError(
            "heldout adjudication evidence does not reconstruct canonical gold"
        )

    changed_count = 0
    changed_fields: dict[str, int] = {}
    adjudicated_times: list[datetime] = []
    identities: list[JsonObject] = []
    for record in adjudicated_records:
        news_item_id = record.get("news_item_id")
        audit = _mapping(
            record.get("adjudication"),
            label=f"heldout adjudication news item {news_item_id}",
        )
        changed = audit.get("adjudicated_changed")
        fields = audit.get("changed_fields")
        if not isinstance(changed, bool) or not isinstance(fields, list) or any(
            not isinstance(field, str) for field in fields
        ):
            raise GoldEvaluationError(
                f"heldout adjudication news item {news_item_id} audit summary is invalid"
            )
        if changed:
            changed_count += 1
        for field in fields:
            changed_fields[field] = changed_fields.get(field, 0) + 1
        adjudicated_at = _aware_iso_datetime(
            audit.get("adjudicated_at"),
            label=f"heldout adjudication news item {news_item_id} adjudicated_at",
        )
        adjudicated_times.append(adjudicated_at)
        identities.append(
            {
                "news_item_id": news_item_id,
                "draft_annotator": audit.get("draft_annotator"),
                "adjudicator": audit.get("adjudicator"),
                "adjudicated_changed": changed,
                "changed_fields": fields,
                "adjudicated_at_utc": adjudicated_at.isoformat().replace(
                    "+00:00",
                    "Z",
                ),
            }
        )
    identity_sha256 = _sha256_bytes(
        json.dumps(
            identities,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return {
        "annotation_type": "ai_drafted_human_adjudicated",
        "adjudication_export_path": str(adjudicated_resolved.relative_to(root)),
        "adjudication_export_sha256": adjudicated_sha256,
        "ai_draft_path": str(ai_draft_resolved.relative_to(root)),
        "ai_draft_sha256": ai_draft_sha256,
        "blind_sample_sha256": blind_sha256,
        "canonical_heldout_annotations_sha256": canonical_sha256,
        "row_count": len(adjudicated_records),
        "confirmed_unchanged_count": len(adjudicated_records) - changed_count,
        "changed_count": changed_count,
        "changed_fields": dict(sorted(changed_fields.items())),
        "adjudication_identity_sha256": identity_sha256,
        "first_adjudicated_at_utc": min(adjudicated_times)
        .isoformat()
        .replace("+00:00", "Z"),
        "last_adjudicated_at_utc": max(adjudicated_times)
        .isoformat()
        .replace("+00:00", "Z"),
        "canonical_reconstruction_passed": True,
        "blindness_validation_passed": True,
        "human_adjudication_validated": True,
    }


def validate_required_report_fields(
    report: Mapping[str, object],
    design: gold_builder.FrozenEvaluationDesign,
) -> None:
    evaluation = _mapping(design.document.get("evaluation"), label="evaluation")
    required = evaluation.get("required_report_fields")
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise GoldEvaluationError("required report field contract is invalid")
    semantic_replacements = (
        {
            "metrics.materiality_precision.dev60": (
                "metrics.materiality_model_interagreement.dev60"
            ),
            "metrics.materiality_recall.dev60": (
                "metrics.materiality_model_interagreement_recall.dev60"
            ),
        }
        if "heldout_annotation_provenance" in design.document
        else {}
    )
    missing: list[str] = []
    for dotted_path in required:
        effective_path = dotted_path
        for inherited_prefix, replacement_prefix in semantic_replacements.items():
            if dotted_path == inherited_prefix or dotted_path.startswith(
                f"{inherited_prefix}."
            ):
                effective_path = dotted_path.replace(
                    inherited_prefix,
                    replacement_prefix,
                    1,
                )
                break
        value: object = report
        for component in effective_path.split("."):
            if not isinstance(value, Mapping) or component not in value:
                missing.append(effective_path)
                break
            value = value[component]
    if missing:
        raise GoldEvaluationError(f"evaluation report omits required fields: {missing}")


def _v1_3_report_extensions(
    *,
    design: gold_builder.FrozenEvaluationDesign,
    active_contract: EventExtractContract,
    dev_annotations: Mapping[int, JsonObject],
    dev_prediction_records: Sequence[JsonObject],
    annotations: Mapping[int, JsonObject],
    predictions: Mapping[int, JsonObject | None],
    result: Mapping[str, Any],
) -> JsonObject:
    design_version = design.document.get("schema_version")
    if active_contract.evidence_candidate_selection:
        return _v1_5_candidate_report_extensions(
            design=design,
            active_contract=active_contract,
            dev_annotations=dev_annotations,
            dev_prediction_records=dev_prediction_records,
        )
    if "historical_comparison" not in design.document:
        return {}
    is_v1_4_design = design_version == "p4.2a-evaluation-design-v1.4"
    if (
        active_contract.evidence_span_match_mode
        != WHITESPACE_NORMALIZED_EVIDENCE_SPAN_MATCH_MODE
    ):
        raise GoldEvaluationError(
            "versioned heldout evaluation requires the whitespace-safe matcher"
        )
    historical = _mapping(
        design.document.get("historical_comparison"),
        label="historical_comparison",
    )
    v1_3_actual = dict(
        _mapping(historical.get("v1_3_actual"), label="historical v1.3 actual")
    )
    counterfactual = dict(
        _mapping(
            historical.get("whitespace_normalized_counterfactual"),
            label="historical whitespace counterfactual",
        )
    )
    adjudication = _mapping(
        historical.get("symbol_adjudication"),
        label="historical symbol adjudication",
    )
    if (
        v1_3_actual.get("success_count") != 53
        or v1_3_actual.get("failure_count") != 7
        or v1_3_actual.get("failure_ids")
        != [250, 258, 287, 304, 306, 336, 358]
        or counterfactual.get("success_count") != 58
        or counterfactual.get("failure_count") != 2
        or counterfactual.get("normalization_recovered_ids")
        != [250, 258, 287, 306, 358]
        or counterfactual.get("true_synthesis_failure_ids") != [304, 336]
        or adjudication.get("ai_label_defect_ids") != [44]
        or adjudication.get("model_over_attribution_ids") != [75, 210, 232, 393]
    ):
        raise GoldEvaluationError("v1.3 historical comparison/adjudication drifted")
    v1_4_r1_actual: JsonObject | None = None
    if is_v1_4_design:
        derived = _mapping(
            historical.get("v1_4_r1_actual"),
            label="historical v1.4-r1 actual",
        )
        extraction = _mapping(
            derived.get("extraction"),
            label="historical v1.4-r1 extraction",
        )
        metrics = _mapping(
            derived.get("metrics"),
            label="historical v1.4-r1 metrics",
        )
        if (
            derived.get("round_id") != "v1.4-r1"
            or derived.get("historical_round_immutable") is not True
            or derived.get("formal_dev_round_valid") is not False
            or derived.get("heldout_accessed") is not False
            or extraction.get("success_count") != 54
            or extraction.get("failure_count") != 6
            or extraction.get("failure_ids") != [253, 258, 280, 304, 336, 340]
            or _mapping(
                metrics.get("symbol_exact_set"),
                label="historical v1.4-r1 symbol metrics",
            ).get("mismatch_ids")
            != [28, 44, 67, 71, 96]
        ):
            raise GoldEvaluationError("v1.4-r1 historical anchor drifted")
        v1_4_r1_actual = copy.deepcopy(dict(derived))

    exact_contract = replace(
        active_contract,
        evidence_span_match_mode=EXACT_EVIDENCE_SPAN_MATCH_MODE,
    )
    actual_failure_ids: list[int] = []
    exact_mismatch_ids: list[int] = []
    normalized_match_ids: list[int] = []
    exact_comparable = 0
    exact_matches = 0
    for row in dev_prediction_records:
        news_item_id = row.get("news_item_id")
        if isinstance(news_item_id, bool) or not isinstance(news_item_id, int):
            raise GoldEvaluationError("dev-final prediction contains an invalid ID")
        if row.get("status") != "ok":
            actual_failure_ids.append(news_item_id)
            continue
        prediction = _mapping(
            row.get("prediction"),
            label=f"dev-final prediction {news_item_id}",
        )
        annotation = dev_annotations.get(news_item_id)
        if annotation is None:
            raise GoldEvaluationError("dev-final prediction is outside dev60")
        record = _mapping(
            annotation.get("record"),
            label=f"dev-final annotation {news_item_id}",
        )
        evidence = prediction.get("evidence_span")
        original_text = record.get("original_text")
        if not isinstance(evidence, str) or not isinstance(original_text, str):
            raise GoldEvaluationError("dev-final evidence text is invalid")
        exact_comparable += 1
        if evidence_span_matches(active_contract, evidence, original_text):
            normalized_match_ids.append(news_item_id)
        if evidence_span_matches(exact_contract, evidence, original_text):
            exact_matches += 1
        else:
            exact_mismatch_ids.append(news_item_id)

    symbol_metrics = _mapping(result.get("metrics"), label="evaluation metrics")
    raw_symbol = _mapping(
        _mapping(
            symbol_metrics.get("symbol_exact_set"),
            label="symbol exact-set metrics",
        ).get("all100"),
        label="all100 symbol exact-set metrics",
    )
    defect_ids = {44}
    adjusted_matches = 0
    adjusted_mismatch_ids: list[int] = []
    adjusted_denominator = 0
    for news_item_id, annotation in annotations.items():
        if news_item_id in defect_ids:
            continue
        adjusted_denominator += 1
        gold = _mapping(annotation.get("gold"), label=f"gold {news_item_id}")
        scored_prediction = predictions[news_item_id]
        if (
            scored_prediction is not None
            and scored_prediction.get("symbols") == gold.get("symbols")
        ):
            adjusted_matches += 1
        else:
            adjusted_mismatch_ids.append(news_item_id)

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
    fresh_actual_key = "v1_5_actual" if is_v1_4_design else "v1_4_actual"
    fresh_shadow_key = (
        "v1_5_legacy_exact_shadow"
        if is_v1_4_design
        else "v1_4_legacy_exact_shadow"
    )
    fresh_actual = {
        "evidence_span_match_mode": active_contract.evidence_span_match_mode,
        "expected_count": len(dev_prediction_records),
        "success_count": len(dev_prediction_records) - len(actual_failure_ids),
        "failure_count": len(actual_failure_ids),
        "failure_ids": sorted(actual_failure_ids),
        "failures_by_validation_field_and_constraint": {},
        "all_successes_pass_active_matcher": (
            len(normalized_match_ids)
            == len(dev_prediction_records) - len(actual_failure_ids)
        ),
        "formal_round_valid": (
            len(dev_prediction_records) == 60 and not actual_failure_ids
        ),
    }
    fresh_shadow = {
        "evidence_span_match_mode": EXACT_EVIDENCE_SPAN_MATCH_MODE,
        "diagnostic_only": True,
        "does_not_change_active_validation": True,
        "comparable_count": exact_comparable,
        "match_count": exact_matches,
        "mismatch_count": len(exact_mismatch_ids),
        "mismatch_ids": sorted(exact_mismatch_ids),
        "whitespace_matcher_recovered_ids": sorted(
            set(normalized_match_ids).intersection(exact_mismatch_ids)
        ),
    }
    evidence_validation: JsonObject = {
        "v1_3_actual": v1_3_actual,
        "whitespace_normalized_counterfactual": counterfactual,
        fresh_actual_key: fresh_actual,
        fresh_shadow_key: fresh_shadow,
    }
    if v1_4_r1_actual is not None:
        historical_extraction = _mapping(
            v1_4_r1_actual.get("extraction"),
            label="historical v1.4-r1 extraction",
        )
        historical_shadow = dict(
            _mapping(
                historical_extraction.get("legacy_exact_shadow"),
                label="historical v1.4-r1 exact shadow",
            )
        )
        historical_shadow.update(
            {
                "evidence_span_match_mode": EXACT_EVIDENCE_SPAN_MATCH_MODE,
                "diagnostic_only": True,
                "historical_round_immutable": True,
            }
        )
        evidence_validation["v1_4_actual"] = copy.deepcopy(v1_4_r1_actual)
        evidence_validation["v1_4_r1_actual"] = copy.deepcopy(v1_4_r1_actual)
        evidence_validation["v1_4_legacy_exact_shadow"] = historical_shadow
    symbol_diagnostics = {
        "raw_gate": dict(raw_symbol),
        "raw_gate_uses_frozen_labels_unchanged": True,
        "ai_label_defect_ids": [44],
        "adjusted_exact_set": {
            "diagnostic_only": True,
            "not_a_gate": True,
            "excluded_ai_label_defect_ids": [44],
            "matches": adjusted_matches,
            "denominator": adjusted_denominator,
            "agreement": _ratio(adjusted_matches, adjusted_denominator),
            "mismatch_ids": adjusted_mismatch_ids,
        },
    }
    if v1_4_r1_actual is None:
        symbol_diagnostics["model_over_attribution_ids"] = [75, 210, 232, 393]
    else:
        symbol_diagnostics["v1_4_r1_actual"] = copy.deepcopy(
            _mapping(
                _mapping(
                    v1_4_r1_actual.get("metrics"),
                    label="historical v1.4-r1 metrics",
                ).get("symbol_adjudication"),
                label="historical v1.4-r1 symbol adjudication",
            )
        )
    return {
        "evidence_validation": evidence_validation,
        "symbol_diagnostics": symbol_diagnostics,
    }


def _run_heldout_preflight(
    annotation_path: Path,
    design_path: Path,
    *,
    heldout_adjudicated_path: Path | None,
    heldout_ai_draft_path: Path | None,
    now: datetime | None,
    collect_all: bool,
    prospective_output_path: Path | None = None,
) -> tuple[HeldoutEvaluationPreflight | None, list[JsonObject]]:
    """Validate every scoring prerequisite without computing a heldout metric."""

    runner = _PreflightRunner(collect_all=collect_all)

    def design_and_unlock() -> tuple[
        gold_builder.FrozenEvaluationDesign, Path, Path, Path
    ]:
        design = gold_builder.load_evaluation_design(design_path)
        current_time = now or datetime.now(UTC)
        gold_builder.require_heldout_ready(design, current_time)
        artifact_root = _project_path(
            design.document.get("artifact_root"), label="artifact_root"
        )
        annotation_resolved = annotation_path.resolve()
        if not annotation_resolved.is_relative_to(artifact_root):
            raise GoldEvaluationError(
                "annotation artifact must stay under docs/phase4/eval"
            )
        state_path = gold_builder.evaluation_artifact_path(
            design,
            "heldout_evaluation_state_jsonl",
        )
        return design, artifact_root, annotation_resolved, state_path

    runner.run("design_and_unlock", (), design_and_unlock)
    if prospective_output_path is not None:
        runner.run(
            "report_path_readonly",
            ("design_and_unlock",),
            lambda: _validate_report_path_readonly(
                prospective_output_path,
                runner.values["design_and_unlock"][1],
            ),
        )

    def evaluation_state_unclaimed() -> Path:
        state_path: Path = runner.values["design_and_unlock"][3]
        if state_path.exists() or state_path.is_symlink():
            raise GoldEvaluationError(
                "heldout evaluation already has a started event; reevaluation is forbidden"
            )
        return state_path

    runner.run(
        "evaluation_state_unclaimed",
        ("design_and_unlock",),
        evaluation_state_unclaimed,
    )

    def owner_annotations() -> tuple[
        list[JsonObject], str, dict[int, JsonObject]
    ]:
        design, _artifact_root, annotation_resolved, _state_path = runner.values[
            "design_and_unlock"
        ]
        annotation_records, annotation_sha256 = _read_jsonl(
            annotation_resolved,
            label="owner annotations",
        )
        annotations = validate_owner_annotations(
            annotation_records,
            design.base_contract,
            design=design,
        )
        owner = _mapping(
            design.document.get("owner_delivery"), label="owner_delivery"
        )
        forbidden = owner.get("forbidden_fields")
        if not isinstance(forbidden, list) or any(
            not isinstance(item, str) for item in forbidden
        ):
            raise GoldEvaluationError("owner forbidden-field contract is invalid")
        forbidden_paths = gold_builder.owner_forbidden_field_paths(
            annotation_records,
            frozenset(forbidden),
        )
        if forbidden_paths:
            raise GoldEvaluationError(
                "owner annotations leak prediction/selection fields: "
                f"{forbidden_paths[:3]}"
            )
        return annotation_records, annotation_sha256, annotations

    runner.run("owner_annotations", ("design_and_unlock",), owner_annotations)

    def owner_completion() -> JsonObject:
        design, _artifact_root, annotation_resolved, _state_path = runner.values[
            "design_and_unlock"
        ]
        annotation_records, annotation_sha256, _annotations = runner.values[
            "owner_annotations"
        ]
        return validate_owner_completion_manifest(
            design,
            annotation_path=annotation_resolved,
            annotation_records=annotation_records,
            annotation_sha256=annotation_sha256,
        )

    runner.run(
        "owner_completion",
        ("design_and_unlock", "owner_annotations"),
        owner_completion,
    )

    def adjudication_provenance() -> JsonObject | None:
        design = runner.values["design_and_unlock"][0]
        if "heldout_annotation_provenance" in design.document:
            if heldout_adjudicated_path is None or heldout_ai_draft_path is None:
                raise GoldEvaluationError(
                    "human-provenance heldout evaluation requires both "
                    "--heldout-adjudicated-export and --heldout-ai-draft"
                )
            return validate_heldout_adjudication_evidence(
                design,
                adjudicated_path=heldout_adjudicated_path,
                ai_draft_path=heldout_ai_draft_path,
            )
        if heldout_adjudicated_path is not None or heldout_ai_draft_path is not None:
            raise GoldEvaluationError(
                "heldout adjudication inputs require a human-provenance design"
            )
        return None

    runner.run(
        "adjudication_provenance",
        ("design_and_unlock",),
        adjudication_provenance,
    )

    def active_contract_and_receipt() -> tuple[EventExtractContract, JsonObject, str]:
        design = runner.values["design_and_unlock"][0]
        return gold_builder.load_active_prediction_contract(design)

    runner.run(
        "active_contract_and_receipt",
        ("design_and_unlock",),
        active_contract_and_receipt,
    )

    def dev_final_freeze() -> JsonObject:
        design = runner.values["design_and_unlock"][0]
        active_contract, receipt, _receipt_sha256 = runner.values[
            "active_contract_and_receipt"
        ]
        return gold_builder.validate_dev_final_prediction_freeze(
            design,
            active_contract=active_contract,
            receipt=receipt,
        )

    runner.run(
        "dev_final_freeze",
        ("design_and_unlock", "active_contract_and_receipt"),
        dev_final_freeze,
    )

    def inference_state() -> tuple[JsonObject, str]:
        design = runner.values["design_and_unlock"][0]
        return gold_builder.load_completed_one_shot_state(design, scope="inference")

    runner.run("inference_state", ("design_and_unlock",), inference_state)

    def candidate_artifacts() -> tuple[
        list[JsonObject], str, list[JsonObject], str, JsonObject, str
    ]:
        design = runner.values["design_and_unlock"][0]
        candidate_inputs_path = gold_builder.evaluation_artifact_path(
            design,
            "heldout_candidate_inputs_jsonl",
        )
        candidate_predictions_path = gold_builder.evaluation_artifact_path(
            design,
            "heldout_candidate_predictions_jsonl",
        )
        prediction_manifest_path = gold_builder.evaluation_artifact_path(
            design,
            "heldout_candidate_predictions_manifest_json",
        )
        candidate_input_records, candidate_inputs_sha256 = _read_jsonl(
            candidate_inputs_path,
            label="heldout candidate inputs",
        )
        candidate_predictions, candidate_predictions_sha256 = _read_jsonl(
            candidate_predictions_path,
            label="heldout candidate predictions",
        )
        prediction_manifest, prediction_manifest_sha256 = _read_json(
            prediction_manifest_path,
            label="heldout candidate prediction manifest",
        )
        return (
            candidate_input_records,
            candidate_inputs_sha256,
            candidate_predictions,
            candidate_predictions_sha256,
            prediction_manifest,
            prediction_manifest_sha256,
        )

    runner.run("candidate_artifacts", ("design_and_unlock",), candidate_artifacts)

    def readonly_db_candidate_rows() -> list[gold_builder.NewsRow]:
        design = runner.values["design_and_unlock"][0]
        database_path = gold_builder._database_path(design.base_contract, None)
        with gold_builder.open_read_only_database(database_path) as connection:
            return gold_builder._heldout_candidate_rows(connection, design)

    runner.run(
        "readonly_db_candidate_rows",
        ("design_and_unlock",),
        readonly_db_candidate_rows,
    )

    def materialization_partition() -> tuple[
        list[gold_builder.NewsRow], JsonObject | None, frozenset[int]
    ]:
        design = runner.values["design_and_unlock"][0]
        active_contract, _receipt, receipt_sha256 = runner.values[
            "active_contract_and_receipt"
        ]
        candidate_input_records = runner.values["candidate_artifacts"][0]
        raw_candidate_rows = runner.values["readonly_db_candidate_rows"]
        return _materialization_binding_for_evaluation(
            design=design,
            active_contract=active_contract,
            receipt_sha256=receipt_sha256,
            candidate_rows=raw_candidate_rows,
            candidate_records=candidate_input_records,
        )

    runner.run(
        "materialization_partition",
        (
            "design_and_unlock",
            "active_contract_and_receipt",
            "candidate_artifacts",
            "readonly_db_candidate_rows",
        ),
        materialization_partition,
    )

    def owner_materialization_eligibility() -> bool:
        _annotation_records, _annotation_sha256, annotations = runner.values[
            "owner_annotations"
        ]
        ineligible_ids = runner.values["materialization_partition"][2]
        if set(annotations).intersection(ineligible_ids):
            raise GoldEvaluationError(
                "owner gold contains a materialization-ineligible news item"
            )
        return True

    runner.run(
        "owner_materialization_eligibility",
        ("owner_annotations", "materialization_partition"),
        owner_materialization_eligibility,
    )

    def candidate_inputs() -> Any:
        design = runner.values["design_and_unlock"][0]
        active_contract = runner.values["active_contract_and_receipt"][0]
        candidate_input_records = runner.values["candidate_artifacts"][0]
        candidate_rows = runner.values["materialization_partition"][0]
        return gold_builder.validate_heldout_candidate_inputs(
            candidate_input_records,
            rows=candidate_rows,
            design=design,
            active_contract=active_contract,
        )

    runner.run(
        "candidate_inputs",
        (
            "design_and_unlock",
            "active_contract_and_receipt",
            "candidate_artifacts",
            "materialization_partition",
        ),
        candidate_inputs,
    )

    def candidate_predictions() -> tuple[Any, int, int]:
        active_contract = runner.values["active_contract_and_receipt"][0]
        candidate_prediction_records = runner.values["candidate_artifacts"][2]
        return gold_builder.validate_heldout_candidate_predictions(
            candidate_prediction_records,
            candidate_inputs=runner.values["candidate_inputs"],
            active_contract=active_contract,
        )

    runner.run(
        "candidate_predictions",
        ("active_contract_and_receipt", "candidate_artifacts", "candidate_inputs"),
        candidate_predictions,
    )

    def prediction_manifest_bindings() -> bool:
        design = runner.values["design_and_unlock"][0]
        active_contract, _receipt, receipt_sha256 = runner.values[
            "active_contract_and_receipt"
        ]
        (
            candidate_input_records,
            candidate_inputs_sha256,
            _candidate_predictions,
            candidate_predictions_sha256,
            prediction_manifest,
            _prediction_manifest_sha256,
        ) = runner.values["candidate_artifacts"]
        _predictions_by_id, success_count, failure_count = runner.values[
            "candidate_predictions"
        ]
        materialization_binding = runner.values["materialization_partition"][1]
        gold_builder._validate_prediction_manifest(
            prediction_manifest,
            design=design,
            active_contract=active_contract,
            receipt_sha256=receipt_sha256,
            inputs_sha256=candidate_inputs_sha256,
            predictions_sha256=candidate_predictions_sha256,
            candidate_records=candidate_input_records,
            success_count=success_count,
            failure_count=failure_count,
            materialization_binding=materialization_binding,
        )
        _require_materialization_binding(
            prediction_manifest,
            expected=materialization_binding,
            label="heldout prediction manifest",
        )
        return True

    runner.run(
        "prediction_manifest_bindings",
        (
            "design_and_unlock",
            "active_contract_and_receipt",
            "candidate_artifacts",
            "candidate_predictions",
            "materialization_partition",
        ),
        prediction_manifest_bindings,
    )

    def inference_completion_bindings() -> bool:
        design = runner.values["design_and_unlock"][0]
        active_contract, _receipt, receipt_sha256 = runner.values[
            "active_contract_and_receipt"
        ]
        inference, _inference_state_sha256 = runner.values["inference_state"]
        (
            candidate_input_records,
            candidate_inputs_sha256,
            candidate_prediction_records,
            _candidate_predictions_sha256,
            _prediction_manifest,
            prediction_manifest_sha256,
        ) = runner.values["candidate_artifacts"]
        _predictions_by_id, success_count, failure_count = runner.values[
            "candidate_predictions"
        ]
        materialization_binding = runner.values["materialization_partition"][1]
        gold_builder.validate_inference_completion_bindings(
            inference,
            design=design,
            active_contract=active_contract,
            receipt_sha256=receipt_sha256,
            candidate_records=candidate_input_records,
            candidate_inputs_sha256=candidate_inputs_sha256,
            prediction_manifest_sha256=prediction_manifest_sha256,
            attempted_count=len(candidate_prediction_records),
            success_count=success_count,
            failure_count=failure_count,
            materialization_binding=materialization_binding,
        )
        raw_inference_events = inference.get("events")
        if (
            not isinstance(raw_inference_events, Sequence)
            or isinstance(raw_inference_events, (str, bytes))
            or len(raw_inference_events) != 2
            or any(not isinstance(event, Mapping) for event in raw_inference_events)
        ):
            raise GoldEvaluationError(
                "inference state materialization events are invalid"
            )
        for index, event in enumerate(raw_inference_events):
            _require_materialization_binding(
                event,
                expected=materialization_binding,
                label=f"inference state event {index + 1}",
            )
        return True

    runner.run(
        "inference_completion_bindings",
        (
            "design_and_unlock",
            "active_contract_and_receipt",
            "inference_state",
            "candidate_artifacts",
            "candidate_predictions",
            "materialization_partition",
        ),
        inference_completion_bindings,
    )

    def selection_manifest() -> JsonObject:
        design = runner.values["design_and_unlock"][0]
        _annotation_records, _annotation_sha256, annotations = runner.values[
            "owner_annotations"
        ]
        active_contract, _receipt, receipt_sha256 = runner.values[
            "active_contract_and_receipt"
        ]
        inference, inference_state_sha256 = runner.values["inference_state"]
        del inference
        (
            _candidate_input_records,
            candidate_inputs_sha256,
            _candidate_predictions,
            candidate_predictions_sha256,
            _prediction_manifest,
            prediction_manifest_sha256,
        ) = runner.values["candidate_artifacts"]
        _candidate_rows, materialization_binding, ineligible_ids = runner.values[
            "materialization_partition"
        ]
        selection_manifest_path = gold_builder.evaluation_artifact_path(
            design,
            "heldout_selection_manifest_json",
        )
        selection, selection_sha256 = _read_json(
            selection_manifest_path,
            label="heldout selection manifest",
        )
        return _validate_selection_manifest(
            selection,
            manifest_sha256=selection_sha256,
            design=design,
            annotations=annotations,
            active_contract=active_contract,
            receipt_sha256=receipt_sha256,
            candidate_inputs_sha256=candidate_inputs_sha256,
            candidate_predictions_sha256=candidate_predictions_sha256,
            candidate_prediction_manifest_sha256=prediction_manifest_sha256,
            inference_state_sha256=inference_state_sha256,
            materialization_binding=materialization_binding,
            ineligible_ids=ineligible_ids,
        )

    runner.run(
        "selection_manifest",
        (
            "design_and_unlock",
            "owner_annotations",
            "active_contract_and_receipt",
            "inference_state",
            "candidate_artifacts",
            "materialization_partition",
            "owner_materialization_eligibility",
            "prediction_manifest_bindings",
            "inference_completion_bindings",
        ),
        selection_manifest,
    )

    def dev_prediction_join() -> tuple[
        dict[int, JsonObject], list[JsonObject], dict[int, JsonObject | None]
    ]:
        _annotation_records, _annotation_sha256, annotations = runner.values[
            "owner_annotations"
        ]
        active_contract = runner.values["active_contract_and_receipt"][0]
        dev_final = runner.values["dev_final_freeze"]
        dev_ids = list(annotations)[:60]
        dev_annotations = {
            news_item_id: annotations[news_item_id] for news_item_id in dev_ids
        }
        dev_prediction_records = dev_final.get("rows")
        if not isinstance(dev_prediction_records, list) or any(
            not isinstance(item, dict) for item in dev_prediction_records
        ):
            raise GoldEvaluationError(
                "dev-final prediction evidence has invalid rows"
            )
        dev_predictions, _dev_extras = join_predictions(
            dev_prediction_records,
            dev_annotations,
            runner.values["design_and_unlock"][0].base_contract,
            active_contract=active_contract,
        )
        return dev_annotations, dev_prediction_records, dev_predictions

    runner.run(
        "dev_prediction_join",
        (
            "design_and_unlock",
            "owner_annotations",
            "active_contract_and_receipt",
            "dev_final_freeze",
        ),
        dev_prediction_join,
    )

    def heldout_prediction_join() -> tuple[
        dict[int, JsonObject], dict[int, JsonObject | None]
    ]:
        _annotation_records, _annotation_sha256, annotations = runner.values[
            "owner_annotations"
        ]
        active_contract = runner.values["active_contract_and_receipt"][0]
        candidate_prediction_records = runner.values["candidate_artifacts"][2]
        heldout_ids = list(annotations)[60:]
        heldout_annotations = {
            news_item_id: annotations[news_item_id] for news_item_id in heldout_ids
        }
        heldout_predictions, _heldout_extras = join_predictions(
            candidate_prediction_records,
            heldout_annotations,
            runner.values["design_and_unlock"][0].base_contract,
            active_contract=active_contract,
        )
        return heldout_annotations, heldout_predictions

    runner.run(
        "heldout_prediction_join",
        (
            "design_and_unlock",
            "owner_annotations",
            "active_contract_and_receipt",
            "candidate_artifacts",
            "candidate_predictions",
            "selection_manifest",
        ),
        heldout_prediction_join,
    )

    required_stages: tuple[str, ...] = (
        "design_and_unlock",
        "evaluation_state_unclaimed",
        "owner_annotations",
        "owner_completion",
        "adjudication_provenance",
        "active_contract_and_receipt",
        "dev_final_freeze",
        "inference_state",
        "candidate_artifacts",
        "readonly_db_candidate_rows",
        "materialization_partition",
        "owner_materialization_eligibility",
        "candidate_inputs",
        "candidate_predictions",
        "prediction_manifest_bindings",
        "inference_completion_bindings",
        "selection_manifest",
        "dev_prediction_join",
        "heldout_prediction_join",
    )
    if prospective_output_path is not None:
        required_stages = (*required_stages, "report_path_readonly")
    if any(not runner.passed(stage) for stage in required_stages):
        if not collect_all:
            raise GoldEvaluationError("heldout evaluation preflight did not complete")
        return None, runner.stages

    design, artifact_root, annotation_resolved, state_path = runner.values[
        "design_and_unlock"
    ]
    annotation_records, annotation_sha256, annotations = runner.values[
        "owner_annotations"
    ]
    del annotation_records
    active_contract, receipt, receipt_sha256 = runner.values[
        "active_contract_and_receipt"
    ]
    inference, _inference_state_sha256 = runner.values["inference_state"]
    _candidate_rows, materialization_binding, _ineligible_ids = runner.values[
        "materialization_partition"
    ]
    dev_annotations, dev_prediction_records, dev_predictions = runner.values[
        "dev_prediction_join"
    ]
    _heldout_annotations, heldout_predictions = runner.values[
        "heldout_prediction_join"
    ]
    preflight = HeldoutEvaluationPreflight(
        design=design,
        artifact_root=artifact_root,
        annotation_resolved=annotation_resolved,
        annotation_sha256=annotation_sha256,
        annotations=annotations,
        owner_completion=runner.values["owner_completion"],
        adjudication_evidence=runner.values["adjudication_provenance"],
        active_contract=active_contract,
        receipt=receipt,
        receipt_sha256=receipt_sha256,
        dev_final=runner.values["dev_final_freeze"],
        inference=inference,
        selection_evidence=runner.values["selection_manifest"],
        materialization_binding=(
            dict(materialization_binding)
            if materialization_binding is not None
            else None
        ),
        dev_annotations=dev_annotations,
        dev_prediction_records=dev_prediction_records,
        predictions={**dev_predictions, **heldout_predictions},
        state_path=state_path,
    )
    return preflight, runner.stages


def dry_run_heldout_evaluation(
    annotation_path: Path,
    output_path: Path,
    design_path: Path = gold_builder.DEFAULT_EVALUATION_DESIGN,
    *,
    heldout_adjudicated_path: Path | None = None,
    heldout_ai_draft_path: Path | None = None,
    now: datetime | None = None,
) -> JsonObject:
    """Exercise the full report path with synthetic scores and no mutations."""

    preflight, stages = _run_heldout_preflight(
        annotation_path,
        design_path,
        heldout_adjudicated_path=heldout_adjudicated_path,
        heldout_ai_draft_path=heldout_ai_draft_path,
        now=now,
        collect_all=True,
        prospective_output_path=output_path,
    )
    post_values: dict[str, Any] = {}
    post_statuses: dict[str, str] = {}

    def validated_preflight() -> HeldoutEvaluationPreflight:
        if preflight is None:
            raise GoldEvaluationError("heldout evaluation preflight did not complete")
        return preflight

    def run_post_stage(
        name: str,
        dependencies: Sequence[str],
        action: Callable[[], Any],
    ) -> None:
        blocked_by = [
            dependency
            for dependency in dependencies
            if post_statuses.get(dependency) != "passed"
        ]
        if preflight is None:
            blocked_by = ["heldout_preflight"]
        if blocked_by:
            post_statuses[name] = "blocked"
            stages.append(
                {"name": name, "status": "blocked", "blocked_by": blocked_by}
            )
            return
        try:
            post_values[name] = action()
        except Exception as exc:
            status = (
                "failed"
                if isinstance(
                    exc,
                    (
                        FileExistsError,
                        GoldEvaluationError,
                        gold_builder.GoldSampleError,
                        EventEvaluationDesignError,
                        OSError,
                        ValueError,
                    ),
                )
                else "internal_error"
            )
            post_statuses[name] = status
            stage: JsonObject = {
                "name": name,
                "status": status,
                "error_type": type(exc).__name__,
            }
            if status == "failed":
                stage["safe_message"] = str(exc)
            stages.append(stage)
            return
        post_statuses[name] = "passed"
        stages.append({"name": name, "status": "passed"})

    run_post_stage(
        "synthetic_metric_inputs",
        (),
        lambda: _synthetic_metric_inputs(validated_preflight()),
    )
    run_post_stage(
        "synthetic_metric_assembly",
        ("synthetic_metric_inputs",),
        lambda: evaluate_split_records(
            post_values["synthetic_metric_inputs"][0],
            post_values["synthetic_metric_inputs"][1],
            validated_preflight().design,
        ),
    )
    run_post_stage(
        "report_extensions_dev_only",
        ("synthetic_metric_inputs", "synthetic_metric_assembly"),
        lambda: _v1_3_report_extensions(
            design=validated_preflight().design,
            active_contract=validated_preflight().active_contract,
            dev_annotations=validated_preflight().dev_annotations,
            dev_prediction_records=validated_preflight().dev_prediction_records,
            annotations=post_values["synthetic_metric_inputs"][0],
            predictions=post_values["synthetic_metric_inputs"][1],
            result=post_values["synthetic_metric_assembly"],
        ),
    )
    run_post_stage(
        "offline_diagnostics",
        ("synthetic_metric_inputs",),
        lambda: _offline_trial_diagnostics(
            validated_preflight().design,
            set(post_values["synthetic_metric_inputs"][0]),
        ),
    )
    dry_timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace(
        "+00:00", "Z"
    )
    prospective_report_path = (
        output_path if output_path.is_absolute() else PROJECT_DIR / output_path
    ).resolve()
    run_post_stage(
        "report_payload_assembly",
        (
            "synthetic_metric_inputs",
            "synthetic_metric_assembly",
            "report_extensions_dev_only",
            "offline_diagnostics",
        ),
        lambda: _assemble_heldout_report(
            validated_preflight(),
            post_values["synthetic_metric_assembly"],
            report_path=prospective_report_path,
            started_at=dry_timestamp,
            terminal_at=dry_timestamp,
            versioned_extensions=post_values["report_extensions_dev_only"],
            offline_diagnostics=post_values["offline_diagnostics"],
            annotations=post_values["synthetic_metric_inputs"][0],
            predictions=post_values["synthetic_metric_inputs"][1],
        ),
    )
    run_post_stage(
        "required_report_fields",
        ("report_payload_assembly",),
        lambda: validate_required_report_fields(
            post_values["report_payload_assembly"],
            validated_preflight().design,
        ),
    )
    run_post_stage(
        "canonical_serialization_in_memory",
        ("required_report_fields",),
        lambda: json.dumps(
            post_values["report_payload_assembly"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8"),
    )
    stages.append(
        {
            "name": "real_heldout_metrics",
            "status": "not_run_one_shot_protected",
        }
    )
    failure_count = sum(
        stage.get("status") in {"failed", "internal_error"} for stage in stages
    )
    blocked_count = sum(stage.get("status") == "blocked" for stage in stages)
    return {
        "schema_version": "p4.2a-heldout-evaluation-dry-run-v1",
        "status": (
            "passed"
            if preflight is not None
            and all(status == "passed" for status in post_statuses.values())
            else "failed"
        ),
        "design_sha256": (
            preflight.design.sha256 if preflight is not None else None
        ),
        "stages": stages,
        "failure_count": failure_count,
        "blocked_count": blocked_count,
        "validated_through": (
            "report_serialization_in_memory"
            if post_statuses.get("canonical_serialization_in_memory") == "passed"
            else "partial_preflight"
        ),
        "one_shot_consumed": False,
        "metrics_computed": False,
        "synthetic_metric_assembly": (
            post_statuses.get("synthetic_metric_assembly") == "passed"
        ),
        "evaluation_passed": None,
        "report_created": False,
        "filesystem_mutations": 0,
        "database_open_mode": "mode=ro+query_only",
        "network_or_llm_calls": 0,
    }


def _v1_5_candidate_report_extensions(
    *,
    design: gold_builder.FrozenEvaluationDesign,
    active_contract: EventExtractContract,
    dev_annotations: Mapping[int, JsonObject],
    dev_prediction_records: Sequence[JsonObject],
) -> JsonObject:
    """Recompute the registered candidate-selector evidence for v1.5+ designs."""

    from scripts import run_p4_2a_dev_iteration as dev_iteration
    from scripts.run_p4_2a_offline_extract import (
        ExtractionSummary,
        _validation_failure_counts,
    )

    if not active_contract.evidence_candidate_selection:
        raise GoldEvaluationError(
            "candidate-selector heldout evaluation requires the frozen selector"
        )
    try:
        full_design = load_event_evaluation_design(
            design.path,
            project_root=PROJECT_DIR,
        )
    except (EventEvaluationDesignError, OSError) as exc:
        raise GoldEvaluationError(
            "heldout evaluation design could not be reloaded"
        ) from exc
    if full_design.sha256 != design.sha256:
        raise GoldEvaluationError("heldout evaluation design hash drifted")

    labels: dict[int, JsonObject] = {}
    for news_item_id, annotation in dev_annotations.items():
        record = _mapping(
            annotation.get("record"),
            label=f"dev60 annotation record {news_item_id}",
        )
        gold = _mapping(
            annotation.get("gold"),
            label=f"dev60 annotation gold {news_item_id}",
        )
        labels[news_item_id] = {**record, "gold": gold}
    if len(labels) != 60 or len(dev_prediction_records) != 60:
        raise GoldEvaluationError(
            "dev-final evidence must cover exactly 60 rows"
        )

    success_count = sum(
        row.get("status") == "ok" for row in dev_prediction_records
    )
    failure_rows = [
        row for row in dev_prediction_records if row.get("status") != "ok"
    ]
    failures_by_reason: dict[str, int] = {}
    for row in failure_rows:
        reason = row.get("error")
        safe_reason = reason if isinstance(reason, str) else "unknown_failure"
        failures_by_reason[safe_reason] = failures_by_reason.get(safe_reason, 0) + 1
    summary = ExtractionSummary(
        expected_count=len(dev_prediction_records),
        success_count=success_count,
        failure_count=len(failure_rows),
        newly_attempted_count=0,
        retried_failure_count=0,
        skipped_exact_success_count=success_count,
        skipped_failure_count=len(failure_rows),
        output_line_count=len(dev_prediction_records),
        failures_by_reason=failures_by_reason,
        failures_by_validation_field_and_constraint=_validation_failure_counts(
            dev_prediction_records
        ),
        isolated_audit_tables=(),
        isolated_audit_row_count=0,
        checkpoint_audited_success_count=success_count,
    )
    metrics = dev_iteration._score_predictions(dev_prediction_records, labels)
    evidence_validation = dev_iteration._evidence_validation(
        design=full_design,
        active_contract=active_contract,
        summary=summary,
        prediction_rows=dev_prediction_records,
        labels=labels,
    )
    symbol_diagnostics = dev_iteration._symbol_diagnostics(
        design=full_design,
        metrics=metrics,
        prediction_rows=dev_prediction_records,
        labels=labels,
    )
    input_identity = dev_iteration._candidate_input_identity(dev_prediction_records)

    registration = _mapping(
        design.document.get("active_prediction_contract"),
        label="active prediction contract registration",
    )
    contract_files = _mapping(
        active_contract.document.get("contract_files"),
        label="active prediction contract files",
    )
    prompt = _mapping(contract_files.get("prompt"), label="active prediction prompt")
    model_schema = _mapping(
        contract_files.get("schema"),
        label="active model result schema",
    )
    materialized_schema = _mapping(
        contract_files.get("materialized_schema"),
        label="active materialized result schema",
    )
    comparison = dev_iteration._comparison_evidence(
        metrics,
        candidate_model=active_contract.model,
        candidate_endpoint=active_contract.endpoint,
        candidate_prompt_sha256=prompt.get("sha256"),
        candidate_contract_schema_version=active_contract.document.get(
            "schema_version"
        ),
        candidate_evidence_span_match_mode=active_contract.evidence_span_match_mode,
        candidate_selection=True,
    )
    changed_dimensions = comparison.get("changed_dimensions")
    if not isinstance(changed_dimensions, list) or any(
        not isinstance(item, str) for item in changed_dimensions
    ):
        raise GoldEvaluationError("comparison changed dimensions are invalid")
    return {
        "_prediction_contract_append": {
            "input_representation": copy.deepcopy(
                _mapping(
                    registration.get("input_representation"),
                    label="registered input representation",
                )
            ),
            "model_result_schema": model_schema,
            "materialized_result_schema": materialized_schema,
            "candidate_materialization": copy.deepcopy(
                _mapping(
                    registration.get("candidate_materialization"),
                    label="registered candidate materialization",
                )
            ),
        },
        "input_identity": input_identity,
        "evidence_validation": evidence_validation,
        "symbol_diagnostics": symbol_diagnostics,
        "comparison_disclosure": {
            "changed_dimensions": changed_dimensions,
            "causal_reading_forbidden": True,
            "interpretation": "indicative_only_not_single_variable_causality",
        },
    }


def _synthetic_metric_inputs(
    preflight: HeldoutEvaluationPreflight,
) -> tuple[dict[int, JsonObject], dict[int, JsonObject | None]]:
    """Build score-safe stand-ins without exposing frozen heldout outcomes.

    The item IDs and split metadata are retained so the report/diagnostic code
    sees the production shape.  Every gold label and prediction used by the
    scoring function is replaced with the same deterministic synthetic value.
    """

    annotations: dict[int, JsonObject] = {}
    predictions: dict[int, JsonObject | None] = {}
    for news_item_id, annotation in preflight.annotations.items():
        record = copy.deepcopy(
            _mapping(
                annotation.get("record"),
                label=f"synthetic annotation record {news_item_id}",
            )
        )
        synthetic_labels: JsonObject = {
            "symbols": [],
            "event_type": "other",
            "direction": "neutral",
            "materiality": 2,
            "evidence_span": "synthetic-dry-run-only",
        }
        annotations[news_item_id] = {
            "record": record,
            "gold": copy.deepcopy(synthetic_labels),
            "synthetic_metric_fixture": True,
        }
        predictions[news_item_id] = copy.deepcopy(synthetic_labels)
    return annotations, predictions


def _assemble_heldout_report(
    preflight: HeldoutEvaluationPreflight,
    result: Mapping[str, Any],
    *,
    report_path: Path,
    started_at: str,
    terminal_at: str,
    versioned_extensions: Mapping[str, object],
    offline_diagnostics: Mapping[str, object],
    annotations: Mapping[int, JsonObject],
    predictions: Mapping[int, JsonObject | None],
) -> JsonObject:
    """Assemble and validate one heldout report without writing any artifact."""

    design = preflight.design
    receipt = preflight.receipt
    dev_final = preflight.dev_final
    inference = preflight.inference
    materialization_binding = preflight.materialization_binding
    extensions = copy.deepcopy(dict(versioned_extensions))
    prediction_contract_append = _mapping(
        extensions.pop("_prediction_contract_append", {}),
        label="versioned prediction contract report fields",
    )
    active_failure_ids = sorted(
        news_item_id
        for news_item_id in annotations
        if predictions[news_item_id] is None
    )
    report: JsonObject = {
        "schema_version": "p4.2a-gold-evaluation-report-v1.1",
        "generated_at_utc": terminal_at,
        "design": {
            "schema_version": design.document["schema_version"],
            "path": str(design.path.relative_to(PROJECT_DIR)),
            "sha256": design.sha256,
        },
        "prediction_contract": {
            "contract_path": receipt["contract_path"],
            "contract_sha256": receipt["contract_sha256"],
            "model": receipt["model"],
            "prompt_path": receipt["prompt_path"],
            "prompt_sha256": receipt["prompt_sha256"],
            "result_schema_path": receipt["result_schema_path"],
            "result_schema_sha256": receipt["result_schema_sha256"],
            "taxonomy_version": receipt["taxonomy_version"],
            "freeze_receipt_sha256": preflight.receipt_sha256,
            "dev_final_predictions_path": dev_final["path"],
            "dev_final_predictions_sha256": dev_final["sha256"],
            "dev_final_predictions_manifest_path": dev_final["manifest_path"],
            "dev_final_predictions_manifest_sha256": dev_final["manifest_sha256"],
            "dev_final_predictions_row_count": dev_final["row_count"],
            "dev_final_predictions_success_count": dev_final["success_count"],
            "dev_final_predictions_failure_count": dev_final["failure_count"],
            "dev_final_predictions_identity_sha256": dev_final[
                "ordered_identity_sha256"
            ],
            "dev_final_predictions_contract_sha256": dev_final["contract_sha256"],
            **(
                {
                    "endpoint": receipt["endpoint"],
                    "explicit_cache_enabled": receipt["explicit_cache_enabled"],
                    **(
                        {
                            "evidence_span_match_mode": receipt[
                                "evidence_span_match_mode"
                            ]
                        }
                        if "evidence_span_match_mode" in receipt
                        else {}
                    ),
                }
                if "endpoint" in receipt
                else {}
            ),
        },
        "splits": {
            "dev60": {
                "sample_count": 60,
                "final_predictions_sha256": dev_final["sha256"],
                "final_predictions_manifest_sha256": dev_final["manifest_sha256"],
                "final_prediction_success_count": dev_final["success_count"],
                "final_prediction_failure_count": dev_final["failure_count"],
                "final_prediction_failure_ids": dev_final["failure_ids"],
                "final_prediction_identity_sha256": dev_final[
                    "ordered_identity_sha256"
                ],
                "final_prediction_contract_sha256": dev_final["contract_sha256"],
            },
            "heldout40": preflight.selection_evidence,
        },
        "one_shot": {
            "inference": {
                **{
                    key: inference[key]
                    for key in (
                        "started_at_utc",
                        "terminal_at_utc",
                        "status",
                        "started_event_count",
                    )
                },
                **(
                    {"materialization": dict(materialization_binding)}
                    if materialization_binding is not None
                    else {}
                ),
            },
            "evaluation": {
                "started_at_utc": started_at,
                "terminal_at_utc": terminal_at,
                "status": "completed",
                "started_event_count": 1,
            },
        },
        "owner_delivery": {
            "annotation_path": str(
                preflight.annotation_resolved.relative_to(PROJECT_DIR)
            ),
            "annotation_sha256": preflight.annotation_sha256,
            "forbidden_field_violation_count": 0,
            **(
                {"heldout_adjudication": preflight.adjudication_evidence}
                if preflight.adjudication_evidence is not None
                else {}
            ),
        },
        "owner_completion": preflight.owner_completion,
        **(
            {"candidate_materialization": dict(materialization_binding)}
            if materialization_binding is not None
            else {}
        ),
        "metrics": result["metrics"],
        "diagnostics": {
            "offline_trial": dict(offline_diagnostics),
            "active_prediction": {
                "gold_failure_count": len(active_failure_ids),
                "gold_failure_ids": active_failure_ids,
            },
        },
        "gates": result["gates"],
        "passed": result["passed"],
        **extensions,
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
    prediction_contract_report = _mapping(
        report.get("prediction_contract"),
        label="prediction contract report",
    )
    prediction_contract_report.update(prediction_contract_append)
    report["prediction_contract"] = prediction_contract_report
    return report


def evaluate_gold_sample_v1_1(
    annotation_path: Path,
    output_path: Path,
    design_path: Path = gold_builder.DEFAULT_EVALUATION_DESIGN,
    *,
    heldout_adjudicated_path: Path | None = None,
    heldout_ai_draft_path: Path | None = None,
    now: datetime | None = None,
) -> JsonObject:
    """Run the single pre-registered heldout evaluation and consume its one-shot."""

    preflight, _stages = _run_heldout_preflight(
        annotation_path,
        design_path,
        heldout_adjudicated_path=heldout_adjudicated_path,
        heldout_ai_draft_path=heldout_ai_draft_path,
        now=now,
        collect_all=False,
    )
    if preflight is None:  # pragma: no cover - fail-fast mode raises first
        raise GoldEvaluationError("heldout evaluation preflight did not complete")
    design = preflight.design
    annotations = preflight.annotations
    active_contract = preflight.active_contract
    dev_annotations = preflight.dev_annotations
    dev_prediction_records = preflight.dev_prediction_records
    predictions = preflight.predictions
    state_path = preflight.state_path
    report_path = _new_report_path(output_path, preflight.artifact_root)
    claimed = False
    try:
        # Shared preflight above is structural/hash/blindness/join-only. Claim
        # the one-shot immediately before the first metric computation.
        started_at = (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace(
            "+00:00",
            "Z",
        )
        _claim_evaluation_one_shot(
            state_path,
            design_sha256=design.sha256,
            started_at_utc=started_at,
        )
        claimed = True
        result = evaluate_split_records(annotations, predictions, design)
        v1_3_extensions = _v1_3_report_extensions(
            design=design,
            active_contract=active_contract,
            dev_annotations=dev_annotations,
            dev_prediction_records=dev_prediction_records,
            annotations=annotations,
            predictions=predictions,
            result=result,
        )
        offline_diagnostics = _offline_trial_diagnostics(design, set(annotations))
        terminal_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        report = _assemble_heldout_report(
            preflight,
            result,
            report_path=report_path,
            started_at=started_at,
            terminal_at=terminal_at,
            versioned_extensions=v1_3_extensions,
            offline_diagnostics=offline_diagnostics,
            annotations=annotations,
            predictions=predictions,
        )
        validate_required_report_fields(report, design)
        report_sha256 = _write_new_json(report_path, report)
        _append_evaluation_terminal(
            state_path,
            design_sha256=design.sha256,
            event="evaluation_completed",
            at_utc=terminal_at,
            details={
                "report_path": str(report_path.relative_to(PROJECT_DIR)),
                "report_sha256": report_sha256,
                "passed": bool(result["passed"]),
            },
        )
        report_artifact = _mapping(report["report_artifact"], label="report_artifact")
        report_artifact["sha256"] = report_sha256
        report["report_artifact"] = report_artifact
        return report
    except BaseException as exc:
        if claimed:
            failed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            with suppress(Exception):
                _append_evaluation_terminal(
                    state_path,
                    design_sha256=design.sha256,
                    event="evaluation_failed",
                    at_utc=failed_at,
                    details={"safe_reason": type(exc).__name__},
                )
        raise


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
    parser.add_argument(
        "--scope",
        choices=(
            "legacy-v1",
            "heldout-final-v1.1",
            "heldout-final-v1.2",
            "heldout-final-v1.3",
            "heldout-final-v1.4",
            "heldout-final-v1.5",
            "heldout-final-v1.6",
            "heldout-final-v1.7",
            "heldout-final-v1.8-replacement",
        ),
        default="heldout-final-v1.1",
        help=(
            "Heldout scopes require their matching explicit evaluation design; "
            "legacy-v1 is historical and must be requested explicitly."
        ),
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--evaluation-design",
        type=Path,
        default=gold_builder.DEFAULT_EVALUATION_DESIGN,
    )
    parser.add_argument("--annotations", type=Path, default=None)
    parser.add_argument("--predictions", type=Path, default=None)
    parser.add_argument(
        "--heldout-adjudicated-export",
        type=Path,
        default=None,
        help=(
            "Create-only .adjudicated.jsonl exported by the human adjudication UI; "
            "required by human-provenance heldout scopes."
        ),
    )
    parser.add_argument(
        "--heldout-ai-draft",
        type=Path,
        default=None,
        help=(
            "The exact blind AI draft used to prefill the adjudication UI; required "
            "with --heldout-adjudicated-export."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate every heldout input and the in-memory report pipeline with "
            "synthetic metrics, without scoring heldout gold or creating artifacts."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        if arguments.evaluation_design.expanduser().resolve() == (
            PROJECT_DIR / "config/p4_event_evaluation_v2.yaml"
        ).resolve():
            active_design = load_event_evaluation_design(
                arguments.evaluation_design.expanduser().resolve(),
                project_root=PROJECT_DIR,
            )
            if (
                active_design.document.get("schema_version")
                == "p4.2a-evaluation-design-v2"
            ):
                raise GoldEvaluationError(
                    "P4.2a v2 requires its dedicated dev45/heldout60 scorer; "
                    "the legacy evaluator is forbidden"
                )
        if arguments.scope in {
            "heldout-final-v1.1",
            "heldout-final-v1.2",
            "heldout-final-v1.3",
            "heldout-final-v1.4",
            "heldout-final-v1.5",
            "heldout-final-v1.6",
            "heldout-final-v1.7",
            "heldout-final-v1.8-replacement",
        }:
            design = gold_builder.load_evaluation_design(arguments.evaluation_design)
            expected_schema_version = {
                "heldout-final-v1.1": "p4.2a-evaluation-design-v1.1",
                "heldout-final-v1.2": "p4.2a-evaluation-design-v1.2",
                "heldout-final-v1.3": "p4.2a-evaluation-design-v1.3",
                "heldout-final-v1.4": "p4.2a-evaluation-design-v1.4",
                "heldout-final-v1.5": "p4.2a-evaluation-design-v1.5",
                "heldout-final-v1.6": "p4.2a-evaluation-design-v1.6",
                "heldout-final-v1.7": "p4.2a-evaluation-design-v1.7",
                "heldout-final-v1.8-replacement": (
                    "p4.2a-evaluation-design-v1.8"
                ),
            }[arguments.scope]
            if design.document.get("schema_version") != expected_schema_version:
                raise GoldEvaluationError(
                    "heldout evaluation scope/design version mismatch"
                )
            annotation_path = (
                arguments.annotations
                if arguments.annotations is not None
                else gold_builder.evaluation_artifact_path(
                    design,
                    "combined_100_annotations_jsonl",
                )
            )
            if arguments.dry_run:
                report = dry_run_heldout_evaluation(
                    annotation_path,
                    arguments.output,
                    arguments.evaluation_design,
                    heldout_adjudicated_path=arguments.heldout_adjudicated_export,
                    heldout_ai_draft_path=arguments.heldout_ai_draft,
                )
                report["scope"] = arguments.scope
                print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
                return 0 if report["status"] == "passed" else 1
            report = evaluate_gold_sample_v1_1(
                annotation_path,
                arguments.output,
                arguments.evaluation_design,
                heldout_adjudicated_path=arguments.heldout_adjudicated_export,
                heldout_ai_draft_path=arguments.heldout_ai_draft,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if report["passed"] is True else 2
        if arguments.dry_run:
            raise GoldEvaluationError("--dry-run is supported only for heldout scopes")
        if (
            arguments.heldout_adjudicated_export is not None
            or arguments.heldout_ai_draft is not None
        ):
            raise GoldEvaluationError(
                "heldout adjudication inputs are invalid for legacy-v1"
            )
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
