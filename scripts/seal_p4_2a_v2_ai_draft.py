#!/usr/bin/env python3
"""Validate and create-only seal the P4.2a v2 development AI draft.

This command does not call a model.  It accepts a deliberately small candidate
JSONL produced by a non-evaluated drafting AI, binds every row to the registered
v2 design and blind input, and writes the canonical AI-draft artifact once.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from alphapilot.llm.p4_news_eval import (  # noqa: E402
    EVALUATION_DESIGN_V2_PATH,
    EventEvaluationDesign,
    EventEvaluationDesignError,
    load_event_evaluation_design,
)

DESIGN_SCHEMA = "p4.2a-evaluation-design-v2"
DESIGN_RELATIVE_PATH = "config/p4_event_evaluation_v2.yaml"
BLIND_SCHEMA = "p4.2a-v2-owner-blind-item-v1"
CANDIDATE_DRAFT_SCHEMA = "p4.2a-v2-ai-draft-candidate-item-v1"
SEALED_DRAFT_SCHEMA = "p4.2a-v2-ai-draft-item-v1"
SELECTION_MANIFEST_SCHEMA = "p4.2a-v2-development-selection-manifest-v1"
FRAME_ID = "p4.2a-development-frame-v2"
EXPECTED_COUNT = 45
EVALUATED_MODELS = frozenset({"qwen3.7-flash", "qwen3.6-plus"})
EVALUATED_MODEL_IDENTITY_KEYS = frozenset({"qwen37flash", "qwen36plus"})

LABEL_FIELDS = (
    "symbols",
    "event_type",
    "direction",
    "materiality",
    "evidence_span",
    "notes",
)
LABEL_FIELD_SET = frozenset(LABEL_FIELDS)
BLIND_FIELDS = frozenset(
    {
        "schema_version",
        "design",
        "frame_id",
        "sample_index",
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
        "body_state",
        "body_evidence",
        "gold",
    }
)
CANDIDATE_FIELDS = frozenset({"schema_version", "news_item_id", "draft_label"})
SEALED_FIELDS = frozenset(
    {
        "schema_version",
        "design",
        "frame_id",
        "sample_index",
        "news_item_id",
        "input_sha256",
        "drafter_id",
        "drafted_at",
        "draft_label",
    }
)
ARTIFACT_NAMES = (
    "development_private_selection_manifest",
    "development_owner_blind_jsonl",
    "development_ai_draft_jsonl",
    "development_adjudication_html",
    "development_owner_raw_export_jsonl",
    "development_human_adjudicated_jsonl",
    "development_owner_completion_manifest",
)
SELECTION_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "design",
        "frame_id",
        "source_lineage",
        "audit",
        "selection",
        "owner_delivery",
        "production_writes",
    }
)
SELECTION_FIELDS = frozenset(
    {
        "algorithm",
        "seed",
        "rank_preimage",
        "owner_order_algorithm",
        "owner_order_preimage",
        "selected_counts",
        "without_replacement",
        "selected",
    }
)
SELECTED_ITEM_FIELDS = frozenset(
    {
        "sample_index",
        "sampling_stratum",
        "selection_rank_sha256",
        "owner_order_sha256",
        "news_item_id",
        "source",
        "input_sha256",
        "declared_input_sha256",
        "text_sha256",
        "contract_sha256",
        "model",
    }
)
OWNER_DELIVERY_FIELDS = frozenset(
    {
        "path",
        "sha256",
        "row_count",
        "sampling_stratum_visible",
        "prediction_visible",
        "selection_rank_visible",
        "gold_state",
    }
)
_HEX64 = frozenset("0123456789abcdef")
BODY_EVIDENCE_FIELDS = frozenset(
    {
        "annotation_text_character_count",
        "body_characters_in_original_text",
        "full_text_character_count",
        "full_text_sha256",
        "pdf_persisted",
        "pdf_sha256",
        "required",
        "source",
        "text_truncated",
        "url",
    }
)


class V2AdjudicationError(ValueError):
    """A v2 blind/draft/adjudication integrity gate failed."""


@dataclass(frozen=True, slots=True)
class V2AdjudicationContract:
    """The small registered-design surface used by the owner workflow."""

    project_root: Path
    design_path: Path
    design_ref: dict[str, str]
    frame_id: str
    expected_count: int
    taxonomy: frozenset[str]
    artifacts: dict[str, Path]


def _strict_json_object(line: str, *, label: str, line_number: int) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise V2AdjudicationError(
                    f"{label} line {line_number} contains duplicate key {key!r}"
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise V2AdjudicationError(f"{label} line {line_number} contains non-finite number {value}")

    try:
        value: object = json.loads(
            line,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise V2AdjudicationError(f"{label} line {line_number} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise V2AdjudicationError(f"{label} line {line_number} must be an object")
    return value


def read_jsonl(path: Path, *, label: str) -> tuple[list[dict[str, Any]], bytes]:
    """Read one regular UTF-8 JSONL file with duplicate-key rejection."""

    if path.is_symlink() or not path.is_file():
        raise V2AdjudicationError(f"{label} must be one regular non-symlink JSONL file")
    payload = path.read_bytes()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise V2AdjudicationError(f"{label} must be UTF-8") from exc
    lines = text.splitlines()
    if not lines:
        raise V2AdjudicationError(f"{label} is empty")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            raise V2AdjudicationError(f"{label} line {number} is blank")
        rows.append(_strict_json_object(line, label=label, line_number=number))
    return rows, payload


def read_json_object(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    """Read one regular UTF-8 JSON object with duplicate-key rejection."""

    if path.is_symlink() or not path.is_file():
        raise V2AdjudicationError(f"{label} must be one regular non-symlink JSON file")
    payload = path.read_bytes()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise V2AdjudicationError(f"{label} must be UTF-8") from exc
    if not text.strip():
        raise V2AdjudicationError(f"{label} is empty")
    return _strict_json_object(text, label=label, line_number=1), payload


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()


def canonical_jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(dict(row)) for row in rows)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value).issubset(_HEX64)


def _aware_iso(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise V2AdjudicationError(f"{label} must be a timezone-aware ISO datetime")
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V2AdjudicationError(f"{label} must be a timezone-aware ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V2AdjudicationError(f"{label} must be a timezone-aware ISO datetime")
    return normalized


def _aware_datetime(value: object, *, label: str) -> datetime:
    normalized = _aware_iso(value, label=label)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _resolved_artifact_path(
    project_root: Path,
    raw_path: object,
    *,
    label: str,
) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise V2AdjudicationError(f"{label} must be a non-blank relative path")
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise V2AdjudicationError(f"{label} escapes the project root")
    root = project_root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise V2AdjudicationError(f"{label} escapes the project root")
    return resolved


def contract_from_design(
    design: EventEvaluationDesign,
    *,
    project_root: Path,
) -> V2AdjudicationContract:
    """Bind the adjudication workflow to the exact registered v2 design surface."""

    document = design.document
    if document.get("schema_version") != DESIGN_SCHEMA:
        raise V2AdjudicationError("the evaluation design is not P4.2a v2")
    expected_design_path = (project_root / DESIGN_RELATIVE_PATH).resolve()
    if design.path.resolve() != expected_design_path:
        raise V2AdjudicationError("evaluation design path is not the registered v2 path")
    if not _is_sha256(design.sha256):
        raise V2AdjudicationError("evaluation design SHA-256 is invalid")

    frames = document.get("frames")
    if not isinstance(frames, Mapping):
        raise V2AdjudicationError("v2 frame contract is missing")
    development = frames.get("development_frame_v2")
    if not isinstance(development, Mapping):
        raise V2AdjudicationError("v2 development-frame contract is missing")
    annotation = development.get("annotation")
    if (
        development.get("frame_id") != FRAME_ID
        or development.get("total_selected_count") != EXPECTED_COUNT
        or not isinstance(annotation, Mapping)
        or annotation.get("type") != "ai_drafted_human_adjudicated"
        or annotation.get("drafting_ai_must_not_be_evaluated_model") is not True
        or annotation.get("adjudicator_role") != "owner_human"
        or annotation.get("gold_is_final_only_after_human_adjudication") is not True
    ):
        raise V2AdjudicationError("v2 development annotation contract drifted")

    raw_artifacts = document.get("artifacts")
    if not isinstance(raw_artifacts, Mapping):
        raise V2AdjudicationError("v2 artifact contract is missing")
    artifacts: dict[str, Path] = {}
    for name in ARTIFACT_NAMES:
        raw_entry = raw_artifacts.get(name)
        if not isinstance(raw_entry, Mapping) or raw_entry.get("create_only") is not True:
            raise V2AdjudicationError(f"registered artifact {name} is not create-only")
        artifacts[name] = _resolved_artifact_path(
            project_root,
            raw_entry.get("path"),
            label=f"artifacts.{name}.path",
        )
    if len(set(artifacts.values())) != len(artifacts):
        raise V2AdjudicationError("registered adjudication artifact paths overlap")

    taxonomy_record = design.base_contract.document.get("taxonomy")
    if not isinstance(taxonomy_record, Mapping):
        raise V2AdjudicationError("base taxonomy is missing")
    taxonomy_values = taxonomy_record.get("values")
    if (
        not isinstance(taxonomy_values, list)
        or not taxonomy_values
        or any(not isinstance(item, str) or not item for item in taxonomy_values)
    ):
        raise V2AdjudicationError("base taxonomy values are invalid")

    return V2AdjudicationContract(
        project_root=project_root.resolve(),
        design_path=expected_design_path,
        design_ref={"path": DESIGN_RELATIVE_PATH, "sha256": design.sha256},
        frame_id=FRAME_ID,
        expected_count=EXPECTED_COUNT,
        taxonomy=frozenset(taxonomy_values),
        artifacts=artifacts,
    )


def load_registered_contract(
    design_path: Path = EVALUATION_DESIGN_V2_PATH,
    *,
    project_root: Path = PROJECT_ROOT,
) -> V2AdjudicationContract:
    expected = (project_root / DESIGN_RELATIVE_PATH).resolve()
    if design_path.expanduser().resolve() != expected:
        raise V2AdjudicationError("only the registered P4.2a v2 design may be used")
    design = load_event_evaluation_design(expected, project_root=project_root)
    return contract_from_design(design, project_root=project_root)


def _find_hidden_metadata(value: object, *, path: str = "$") -> str | None:
    """Find recursively hidden sampling/prediction metadata by key semantics."""

    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key).casefold().replace("-", "_")
            child = f"{path}.{raw_key}"
            if any(
                token in key
                for token in (
                    "stratum",
                    "sampling",
                    "selection",
                    "rank",
                    "prediction",
                    "predicted",
                    "model_output",
                )
            ):
                return child
            found = _find_hidden_metadata(nested, path=child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = _find_hidden_metadata(nested, path=f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _positive_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise V2AdjudicationError(f"{label} must be a positive integer")
    return value


def validate_blind_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    contract: V2AdjudicationContract,
) -> list[dict[str, Any]]:
    """Validate the exact, recursively blind, ordered dev45 delivery schema."""

    if len(rows) != contract.expected_count:
        raise V2AdjudicationError(
            f"blind JSONL must contain exactly {contract.expected_count} rows"
        )
    normalized: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for expected_index, raw_row in enumerate(rows, 1):
        row = dict(raw_row)
        if set(row) != BLIND_FIELDS:
            raise V2AdjudicationError(
                f"blind row {expected_index} fields drifted from {BLIND_SCHEMA}"
            )
        leaked = _find_hidden_metadata(row)
        if leaked is not None:
            raise V2AdjudicationError(f"blind row {expected_index} leaks metadata at {leaked}")
        news_item_id = _positive_int(
            row.get("news_item_id"), label=f"blind row {expected_index} news_item_id"
        )
        if news_item_id in seen_ids:
            raise V2AdjudicationError(f"blind duplicates news_item_id={news_item_id}")
        seen_ids.add(news_item_id)
        if (
            row.get("schema_version") != BLIND_SCHEMA
            or row.get("design") != contract.design_ref
            or row.get("frame_id") != contract.frame_id
            or row.get("sample_index") != expected_index
            or row.get("gold") != {}
        ):
            raise V2AdjudicationError(f"blind row {expected_index} identity/gold drifted")
        for field in ("source", "url", "title", "original_text", "body_state"):
            if not isinstance(row.get(field), str) or not row[field]:
                raise V2AdjudicationError(f"blind row {expected_index} {field} is invalid")
        ingested_symbol = row.get("ingested_symbol")
        if ingested_symbol is not None and (
            not isinstance(ingested_symbol, str)
            or len(ingested_symbol) != 6
            or not ingested_symbol.isdigit()
        ):
            raise V2AdjudicationError(f"blind row {expected_index} ingested_symbol is invalid")
        for field in ("published_at", "available_time"):
            value = row.get(field)
            if value is not None:
                _aware_iso(value, label=f"blind row {expected_index} {field}")
        for field in ("input_sha256", "text_sha256"):
            if not _is_sha256(row.get(field)):
                raise V2AdjudicationError(f"blind row {expected_index} {field} is invalid")
        original_text = str(row["original_text"])
        if row["text_sha256"] != sha256_bytes(original_text.encode()):
            raise V2AdjudicationError(f"blind row {expected_index} text SHA-256 drifted")
        _validate_body_evidence(row, label=f"blind row {expected_index}")
        normalized.append(row)
    return normalized


def validate_selection_manifest_binding(
    manifest: Mapping[str, Any],
    blind_rows: Sequence[Mapping[str, Any]],
    blind_payload: bytes,
    *,
    contract: V2AdjudicationContract,
) -> dict[str, Any]:
    """Bind the blind delivery bytes and ordered identities to the private selection."""

    blind = validate_blind_rows(blind_rows, contract=contract)
    normalized = dict(manifest)
    if set(normalized) != SELECTION_MANIFEST_FIELDS:
        raise V2AdjudicationError("private selection manifest fields drifted")
    if (
        normalized.get("schema_version") != SELECTION_MANIFEST_SCHEMA
        or normalized.get("design") != contract.design_ref
        or normalized.get("frame_id") != contract.frame_id
        or normalized.get("production_writes") is not False
    ):
        raise V2AdjudicationError("private selection manifest identity drifted")

    owner_delivery = normalized.get("owner_delivery")
    if not isinstance(owner_delivery, Mapping) or set(owner_delivery) != OWNER_DELIVERY_FIELDS:
        raise V2AdjudicationError("private selection owner_delivery fields drifted")
    blind_path = contract.artifacts["development_owner_blind_jsonl"]
    expected_blind_path = str(blind_path.relative_to(contract.project_root))
    if (
        owner_delivery.get("path") != expected_blind_path
        or owner_delivery.get("sha256") != sha256_bytes(blind_payload)
        or owner_delivery.get("row_count") != contract.expected_count
        or owner_delivery.get("sampling_stratum_visible") is not False
        or owner_delivery.get("prediction_visible") is not False
        or owner_delivery.get("selection_rank_visible") is not False
        or owner_delivery.get("gold_state") != "empty_object_pending_human_adjudication"
    ):
        raise V2AdjudicationError("private selection does not bind the exact blind delivery")

    selection = normalized.get("selection")
    if not isinstance(selection, Mapping) or set(selection) != SELECTION_FIELDS:
        raise V2AdjudicationError("private selection contract fields drifted")
    expected_counts = {
        "predicted_positive": 30,
        "predicted_negative": 15,
        "extract_failed": 0,
        "total": contract.expected_count,
    }
    selected = selection.get("selected")
    if (
        selection.get("algorithm") != "sha256_rank_without_replacement_per_stratum_v1"
        or selection.get("owner_order_algorithm")
        != "sha256_rank_without_sampling_stratum_v1"
        or selection.get("selected_counts") != expected_counts
        or selection.get("without_replacement") is not True
        or not isinstance(selected, list)
        or len(selected) != contract.expected_count
    ):
        raise V2AdjudicationError("private selection counts/order contract drifted")

    observed_counts = {"predicted_positive": 0, "predicted_negative": 0}
    seen_ids: set[int] = set()
    for index, (raw_selected, blind_row) in enumerate(zip(selected, blind, strict=True), 1):
        if not isinstance(raw_selected, Mapping) or set(raw_selected) != SELECTED_ITEM_FIELDS:
            raise V2AdjudicationError(f"private selection row {index} fields drifted")
        selected_row = dict(raw_selected)
        news_item_id = _positive_int(
            selected_row.get("news_item_id"),
            label=f"private selection row {index} news_item_id",
        )
        if news_item_id in seen_ids:
            raise V2AdjudicationError(f"private selection duplicates news_item_id={news_item_id}")
        seen_ids.add(news_item_id)
        stratum = selected_row.get("sampling_stratum")
        if not isinstance(stratum, str) or stratum not in observed_counts:
            raise V2AdjudicationError(f"private selection row {index} has invalid stratum")
        observed_counts[stratum] += 1
        if (
            selected_row.get("sample_index") != index
            or news_item_id != blind_row["news_item_id"]
            or selected_row.get("input_sha256") != blind_row["input_sha256"]
            or selected_row.get("source") != blind_row["source"]
            or selected_row.get("text_sha256") != blind_row["text_sha256"]
        ):
            raise V2AdjudicationError(
                "private selection IDs/order/input bindings do not match the blind JSONL"
            )
        for field in (
            "selection_rank_sha256",
            "owner_order_sha256",
            "input_sha256",
            "declared_input_sha256",
            "text_sha256",
            "contract_sha256",
        ):
            if not _is_sha256(selected_row.get(field)):
                raise V2AdjudicationError(
                    f"private selection row {index} {field} is invalid"
                )
        if not isinstance(selected_row.get("model"), str) or not selected_row["model"]:
            raise V2AdjudicationError(f"private selection row {index} model is invalid")
    if observed_counts != {"predicted_positive": 30, "predicted_negative": 15}:
        raise V2AdjudicationError("private selection observed stratum counts drifted")
    return normalized


def read_bound_blind_bundle(
    selection_manifest_path: Path,
    blind_path: Path,
    *,
    contract: V2AdjudicationContract,
) -> tuple[list[dict[str, Any]], bytes, bytes]:
    """Read and validate the registered private-manifest/blind pair."""

    manifest, manifest_payload = read_json_object(
        selection_manifest_path, label="private selection manifest"
    )
    blind_rows, blind_payload = read_jsonl(blind_path, label="blind")
    validate_selection_manifest_binding(
        manifest,
        blind_rows,
        blind_payload,
        contract=contract,
    )
    return blind_rows, blind_payload, manifest_payload


def _validate_body_evidence(record: Mapping[str, Any], *, label: str) -> None:
    evidence = record.get("body_evidence")
    if not isinstance(evidence, Mapping):
        raise V2AdjudicationError(f"{label} body_evidence must be a mapping")
    leaked = _find_hidden_metadata(evidence)
    if leaked is not None:
        raise V2AdjudicationError(f"{label} body_evidence leaks metadata at {leaked}")
    # v2 deliberately treats this source artifact as an opaque, byte-semantic
    # JSON value.  Apply the historical semantic checks when the historical
    # shape is present, but do not invent a new body-evidence schema here.
    if set(evidence) != BODY_EVIDENCE_FIELDS:
        return
    required = evidence.get("required")
    if required is False:
        if dict(evidence) != {
            "required": False,
            "source": None,
            "url": None,
            "pdf_sha256": None,
            "full_text_sha256": None,
            "full_text_character_count": None,
            "annotation_text_character_count": None,
            "body_characters_in_original_text": None,
            "text_truncated": False,
            "pdf_persisted": False,
        }:
            raise V2AdjudicationError(f"{label} non-body evidence drifted")
        return
    original_text = record["original_text"]
    full_count = evidence.get("full_text_character_count")
    annotation_count = evidence.get("annotation_text_character_count")
    body_count = evidence.get("body_characters_in_original_text")
    full_sha = evidence.get("full_text_sha256")
    pdf_sha = evidence.get("pdf_sha256")
    truncated = evidence.get("text_truncated")
    if (
        required is not True
        or record.get("source") != "cninfo"
        or record.get("body_state") != "announcement_body"
        or evidence.get("source") != "cninfo_pdf"
        or evidence.get("url") != record.get("url")
        or evidence.get("pdf_persisted") is not False
        or not _is_sha256(pdf_sha)
        or not _is_sha256(full_sha)
        or isinstance(full_count, bool)
        or not isinstance(full_count, int)
        or full_count < len(original_text)
        or annotation_count != len(original_text)
        or body_count != len(original_text)
        or not isinstance(truncated, bool)
        or truncated is not (full_count > len(original_text))
        or (not truncated and full_sha != record.get("text_sha256"))
    ):
        raise V2AdjudicationError(f"{label} announcement body evidence drifted")


def validate_label(
    value: object,
    *,
    original_text: str,
    taxonomy: frozenset[str],
    label: str,
    require_null_notes: bool,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != LABEL_FIELD_SET:
        raise V2AdjudicationError(f"{label} fields drifted")
    leaked = _find_hidden_metadata(value)
    if leaked is not None:
        raise V2AdjudicationError(f"{label} leaks metadata at {leaked}")
    symbols = value.get("symbols")
    if (
        not isinstance(symbols, list)
        or any(not isinstance(symbol, str) for symbol in symbols)
        or any(len(symbol) != 6 or not symbol.isdigit() for symbol in symbols)
        or symbols != sorted(set(symbols))
    ):
        raise V2AdjudicationError(f"{label}.symbols must be sorted unique 6-digit codes")
    event_type = value.get("event_type")
    if not isinstance(event_type, str) or event_type not in taxonomy:
        raise V2AdjudicationError(f"{label}.event_type is invalid")
    direction = value.get("direction")
    if isinstance(direction, bool) or not isinstance(direction, int) or direction not in {-1, 0, 1}:
        raise V2AdjudicationError(f"{label}.direction is invalid")
    materiality = value.get("materiality")
    if (
        isinstance(materiality, bool)
        or not isinstance(materiality, int)
        or materiality not in {0, 1, 2, 3}
    ):
        raise V2AdjudicationError(f"{label}.materiality is invalid")
    evidence_span = value.get("evidence_span")
    if (
        not isinstance(evidence_span, str)
        or not evidence_span
        or evidence_span not in original_text
    ):
        raise V2AdjudicationError(
            f"{label}.evidence_span must be a contiguous quote from original_text"
        )
    notes = value.get("notes")
    if require_null_notes and notes is not None:
        raise V2AdjudicationError(f"{label}.notes must be null for the AI draft")
    if notes is not None and (not isinstance(notes, str) or not notes.strip()):
        raise V2AdjudicationError(f"{label}.notes must be null or a non-blank string")
    return {
        "symbols": list(symbols),
        "event_type": event_type,
        "direction": direction,
        "materiality": materiality,
        "evidence_span": evidence_span,
        "notes": notes,
    }


def normalize_drafter_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise V2AdjudicationError("drafter_id must be non-blank")
    normalized = unicodedata.normalize("NFKC", value).strip()
    identity_key = actor_identity_key(normalized)
    if not identity_key:
        raise V2AdjudicationError("drafter_id must contain letters or digits")
    if any(model_key in identity_key for model_key in EVALUATED_MODEL_IDENTITY_KEYS):
        raise V2AdjudicationError("drafting AI must not be an evaluated model")
    return normalized


def actor_identity_key(value: str) -> str:
    """Canonical comparison key for human/AI actor identities and model aliases."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def seal_candidate_rows(
    blind_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    *,
    contract: V2AdjudicationContract,
    drafter_id: str,
    drafted_at: str,
) -> list[dict[str, Any]]:
    blind = validate_blind_rows(blind_rows, contract=contract)
    if len(candidate_rows) != contract.expected_count:
        raise V2AdjudicationError(
            f"candidate draft must contain exactly {contract.expected_count} rows"
        )
    drafter = normalize_drafter_id(drafter_id)
    timestamp = _aware_iso(drafted_at, label="drafted_at")
    sealed: list[dict[str, Any]] = []
    for index, (blind_row, raw_candidate) in enumerate(zip(blind, candidate_rows, strict=True), 1):
        candidate = dict(raw_candidate)
        if set(candidate) != CANDIDATE_FIELDS:
            raise V2AdjudicationError(f"candidate draft row {index} fields drifted")
        if "gold" in candidate:
            raise V2AdjudicationError("candidate draft must use draft_label, never gold")
        leaked = _find_hidden_metadata(candidate)
        if leaked is not None:
            raise V2AdjudicationError(f"candidate draft row {index} leaks metadata at {leaked}")
        if (
            candidate.get("schema_version") != CANDIDATE_DRAFT_SCHEMA
            or candidate.get("news_item_id") != blind_row["news_item_id"]
        ):
            raise V2AdjudicationError(
                "candidate draft IDs/order must exactly match the blind JSONL"
            )
        draft_label = validate_label(
            candidate.get("draft_label"),
            original_text=str(blind_row["original_text"]),
            taxonomy=contract.taxonomy,
            label=f"candidate draft row {index} draft_label",
            require_null_notes=True,
        )
        sealed.append(
            {
                "schema_version": SEALED_DRAFT_SCHEMA,
                "design": dict(contract.design_ref),
                "frame_id": contract.frame_id,
                "sample_index": index,
                "news_item_id": blind_row["news_item_id"],
                "input_sha256": blind_row["input_sha256"],
                "drafter_id": drafter,
                "drafted_at": timestamp,
                "draft_label": draft_label,
            }
        )
    return sealed


def validate_sealed_draft_rows(
    blind_rows: Sequence[Mapping[str, Any]],
    draft_rows: Sequence[Mapping[str, Any]],
    *,
    contract: V2AdjudicationContract,
) -> tuple[list[dict[str, Any]], str]:
    blind = validate_blind_rows(blind_rows, contract=contract)
    if len(draft_rows) != contract.expected_count:
        raise V2AdjudicationError(
            f"sealed draft must contain exactly {contract.expected_count} rows"
        )
    normalized: list[dict[str, Any]] = []
    one_drafter: str | None = None
    one_drafted_at: str | None = None
    for index, (blind_row, raw_draft) in enumerate(zip(blind, draft_rows, strict=True), 1):
        draft = dict(raw_draft)
        if set(draft) != SEALED_FIELDS:
            raise V2AdjudicationError(f"sealed draft row {index} fields drifted")
        leaked = _find_hidden_metadata(draft)
        if leaked is not None:
            raise V2AdjudicationError(f"sealed draft row {index} leaks metadata at {leaked}")
        if (
            draft.get("schema_version") != SEALED_DRAFT_SCHEMA
            or draft.get("design") != contract.design_ref
            or draft.get("frame_id") != contract.frame_id
            or draft.get("sample_index") != index
            or draft.get("news_item_id") != blind_row["news_item_id"]
            or draft.get("input_sha256") != blind_row["input_sha256"]
        ):
            raise V2AdjudicationError(
                "sealed draft IDs/order/bindings must exactly match the blind JSONL"
            )
        drafter = normalize_drafter_id(draft.get("drafter_id"))
        if one_drafter is None:
            one_drafter = drafter
        elif drafter != one_drafter:
            raise V2AdjudicationError("sealed draft must use one consistent drafter_id")
        drafted_at = _aware_iso(
            draft.get("drafted_at"), label=f"sealed draft row {index} drafted_at"
        )
        if one_drafted_at is None:
            one_drafted_at = drafted_at
        elif drafted_at != one_drafted_at:
            raise V2AdjudicationError("sealed draft must use one consistent drafted_at")
        label_value = validate_label(
            draft.get("draft_label"),
            original_text=str(blind_row["original_text"]),
            taxonomy=contract.taxonomy,
            label=f"sealed draft row {index} draft_label",
            require_null_notes=True,
        )
        normalized.append(
            {
                **draft,
                "drafter_id": drafter,
                "drafted_at": drafted_at,
                "draft_label": label_value,
            }
        )
    if one_drafter is None or one_drafted_at is None:
        raise V2AdjudicationError("sealed draft has no drafter identity")
    return normalized, one_drafter


def write_create_only(path: Path, payload: bytes) -> str:
    """Publish bytes without following symlinks or replacing an existing inode."""

    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite create-only artifact: {path}")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise V2AdjudicationError("output parent must be an existing regular directory")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    directory_fd = os.open(parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return sha256_bytes(payload)


def _bound_input(path: Path, expected: Path, *, label: str) -> Path:
    candidate = path.expanduser()
    if candidate.is_symlink():
        raise V2AdjudicationError(f"{label} must not be a symlink")
    resolved = candidate.resolve()
    if resolved != expected:
        raise V2AdjudicationError(f"{label} must be the artifact path registered by v2")
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-draft", type=Path, required=True)
    parser.add_argument("--drafter-id", required=True)
    parser.add_argument("--drafted-at", required=True)
    parser.add_argument("--selection-manifest", type=Path, default=None)
    parser.add_argument("--blind", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--evaluation-design",
        type=Path,
        default=EVALUATION_DESIGN_V2_PATH,
    )
    arguments = parser.parse_args(argv)
    try:
        contract = load_registered_contract(arguments.evaluation_design)
        selection_manifest_path = _bound_input(
            arguments.selection_manifest
            or contract.artifacts["development_private_selection_manifest"],
            contract.artifacts["development_private_selection_manifest"],
            label="selection manifest",
        )
        blind_path = _bound_input(
            arguments.blind or contract.artifacts["development_owner_blind_jsonl"],
            contract.artifacts["development_owner_blind_jsonl"],
            label="blind",
        )
        output_path = _bound_input(
            arguments.output or contract.artifacts["development_ai_draft_jsonl"],
            contract.artifacts["development_ai_draft_jsonl"],
            label="output",
        )
        candidate_path = arguments.candidate_draft.expanduser()
        if candidate_path.is_symlink():
            raise V2AdjudicationError("candidate draft must not be a symlink")
        candidate_path = candidate_path.resolve()
        blind_rows, _, _ = read_bound_blind_bundle(
            selection_manifest_path,
            blind_path,
            contract=contract,
        )
        candidate_rows, _ = read_jsonl(candidate_path, label="candidate draft")
        sealed = seal_candidate_rows(
            blind_rows,
            candidate_rows,
            contract=contract,
            drafter_id=arguments.drafter_id,
            drafted_at=arguments.drafted_at,
        )
        payload = canonical_jsonl_bytes(sealed)
        digest = write_create_only(output_path, payload)
    except (
        EventEvaluationDesignError,
        V2AdjudicationError,
        FileExistsError,
        OSError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "sealed",
                "output": str(output_path.relative_to(PROJECT_ROOT)),
                "sha256": digest,
                "row_count": len(sealed),
                "drafter_id": sealed[0]["drafter_id"],
                "drafting_ai_inference_occurred_before_sealing": True,
                "sealing_cli_model_called": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
