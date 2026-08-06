from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
import yaml
from jsonschema import Draft202012Validator

from alphapilot.llm.p4_news_eval import (
    EventEvaluationDesign,
    EventEvaluationDesignError,
    load_event_evaluation_design,
)
from alphapilot.llm.p4_news_event import (
    EXACT_EVIDENCE_SPAN_MATCH_MODE,
    EventExtractContract,
    EventExtractValidationError,
    build_declared_legacy_input_identity,
    build_event_extract_user_input,
    event_extract_input_sha256,
    load_event_extract_contract,
    validate_event_extract_contract_controls,
    validate_materialized_event_result,
)

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path("config/p4_event_extract_eval_v1.yaml")
DEFAULT_EVALUATION_DESIGN = Path("config/p4_event_evaluation_v1_1.yaml")
MATERIALIZATION_SUCCESSOR_ORIGIN_DESIGN = Path(
    "config/p4_event_evaluation_v1_6.yaml"
)
SHANGHAI = ZoneInfo("Asia/Shanghai")
PDF_MAGIC = b"%PDF-"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SIX_DIGIT_SYMBOL = re.compile(r"^[0-9]{6}$")
ACTIVE_CONTRACT_SCHEMA = re.compile(r"^p4\.2a-event-extract-eval-v[0-9]+(?:\.[0-9]+)*$")
PROMPT_VERSION_MARKER = re.compile(r"\[P4_NEWS_EVENT_EXTRACT v[0-9]+(?:\.[0-9]+)*\]")
MODEL_PREDICTION_KEYS = frozenset(
    {
        "prediction",
        "predictions",
        "model_prediction",
        "model_predictions",
        "model_output",
        "extract_result",
    }
)
ANNOTATION_MUTABLE_FIELDS = frozenset(
    {
        "annotation_status",
        "annotation_owner",
        "annotated_at",
        "gold",
    }
)
ANNOTATION_ITEM_FIELDS = frozenset(
    {
        "schema_version",
        "sample_version",
        "contract_sha256",
        "sample_index",
        "sample_group",
        "trading_date",
        "stratum",
        "rank_sha256",
        "news_item_id",
        "source",
        "url",
        "title",
        "ingested_symbol",
        "published_at",
        "available_time",
        "original_text",
        "body_state",
        "content_hash",
        "text_sha256",
        "body_evidence",
        "annotation_status",
        "annotation_owner",
        "annotated_at",
        "gold",
        "input_sha256",
    }
)
FROZEN_MANIFEST_DIRECT_FIELDS = (
    "schema_version",
    "sample_version",
    "contract_sha256",
    "sample_index",
    "sample_group",
    "trading_date",
    "stratum",
    "rank_sha256",
    "news_item_id",
    "source",
    "url",
    "title",
    "ingested_symbol",
    "published_at",
    "available_time",
    "body_state",
    "content_hash",
    "text_sha256",
    "input_sha256",
)
STRATUM_FIELDS = frozenset(
    {"source", "symbol_state", "require_announcement_body"}
)
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

JsonObject = dict[str, Any]


class GoldSampleError(RuntimeError):
    """The frozen gold-sample contract could not be satisfied."""


class GoldSampleNotReady(GoldSampleError):
    """The future sample is still inside its pre-registered observation window."""


class CandidateDocumentIneligible(GoldSampleError):
    """One deterministic document property makes a held-out candidate ineligible."""

    def __init__(
        self,
        *,
        reason: str,
        measured_value: int,
        gate_value: int,
        pdf_sha256: str | None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.measured_value = measured_value
        self.gate_value = gate_value
        self.pdf_sha256 = pdf_sha256


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON numeric constant is forbidden: {value}")


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys at every mapping depth."""


def _construct_unique_yaml_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicated = key in result
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicated:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_yaml_mapping,
)


def _strict_yaml_load(payload: bytes, *, label: str) -> object:
    try:
        return yaml.load(payload, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise GoldSampleError(f"{label} is invalid or contains duplicate keys") from exc


@dataclass(frozen=True, slots=True)
class FrozenContract:
    path: Path
    sha256: str
    document: JsonObject


@dataclass(frozen=True, slots=True)
class FrozenEvaluationDesign:
    path: Path
    sha256: str
    document: JsonObject
    base_contract: FrozenContract


@dataclass(frozen=True, slots=True)
class HeldoutPredictionEvidence:
    path: Path
    sha256: str
    row_count: int
    candidate_count: int
    successful_count: int
    eligible_count: int
    active_contract_sha256: str
    selected_machine_evidence: tuple[JsonObject, ...]


@dataclass(frozen=True, slots=True)
class AnnouncementBodyPolicy:
    allowed_scheme: str
    allowed_host: str
    follow_redirects: bool
    tls_verify: bool
    connect_timeout_seconds: float
    read_timeout_seconds: float
    max_pdf_bytes: int
    required_magic: bytes
    extractor_command: str
    extractor_timeout_seconds: float
    max_annotation_text_characters: int
    minimum_extracted_characters: int


@dataclass(frozen=True, slots=True)
class ExtractedPdfText:
    text: str
    text_sha256: str
    full_character_count: int


@dataclass(frozen=True, slots=True)
class HeldoutCandidateMaterialization:
    """Three-layer deterministic materialization result, before any prediction."""

    all_candidates: tuple[JsonObject, ...]
    eligible_records: tuple[JsonObject, ...]
    ineligible_candidates: tuple[JsonObject, ...]
    reason_counts: JsonObject


MATERIALIZATION_MANIFEST_SCHEMA_VERSION = (
    "p4.2a-heldout-candidate-materialization-manifest-v1"
)
MATERIALIZATION_INELIGIBLE_REASONS = (
    "pdf_text_below_min_char_gate",
    "pdf_exceeds_size_bound",
)


@dataclass(frozen=True, slots=True)
class NewsRow:
    news_item_id: int
    source: str
    ingested_symbol: str | None
    title: str
    url: str
    published_at: datetime | None
    available_time: datetime
    content_hash: str
    raw_payload: JsonObject

    @property
    def symbol_state(self) -> str:
        return "null" if self.ingested_symbol is None else "bound"


@dataclass(frozen=True, slots=True)
class Stratum:
    source: str
    symbol_state: str
    count: int
    require_announcement_body: bool


@dataclass(frozen=True, slots=True)
class SelectedNews:
    row: NewsRow
    sample_group: str
    trading_date: date | None
    stratum: Stratum
    rank_sha256: str


PdfFetcher = Callable[[str, AnnouncementBodyPolicy], bytes]
PdfTextExtractor = Callable[[bytes, AnnouncementBodyPolicy], ExtractedPdfText]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _mapping(value: object, *, label: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _positive_integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _positive_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{label} must be a positive number")
    return float(value)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _project_path(value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty path")
    path = Path(value)
    return (path if path.is_absolute() else PROJECT_DIR / path).resolve()


def _contract_strata(value: object, *, label: str) -> list[Stratum]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a sequence")
    strata: list[Stratum] = []
    for index, raw in enumerate(value):
        item = _mapping(raw, label=f"{label}[{index}]")
        source = item.get("source")
        raw_symbol_state = item.get("symbol_state")
        # YAML 1.1 parses the unquoted contract token `null` as None.
        symbol_state = "null" if raw_symbol_state is None else raw_symbol_state
        require_body = item.get("require_announcement_body")
        _require(
            source in {"cninfo", "akshare_ths", "sina_company_news"},
            f"{label}[{index}].source is unsupported",
        )
        _require(
            symbol_state in {"bound", "null"},
            f"{label}[{index}].symbol_state is unsupported",
        )
        _require(
            isinstance(require_body, bool),
            f"{label}[{index}].require_announcement_body must be boolean",
        )
        strata.append(
            Stratum(
                source=str(source),
                symbol_state=str(symbol_state),
                count=_positive_integer(item.get("count"), label=f"{label}[{index}].count"),
                require_announcement_body=bool(require_body),
            )
        )
    return strata


def _validate_contract_paths(document: JsonObject) -> None:
    artifact_root = _project_path(document.get("artifact_root"), label="artifact_root")
    expected_root = (PROJECT_DIR / "docs/phase4/eval").resolve()
    _require(artifact_root == expected_root, "artifact_root must remain docs/phase4/eval")

    contract_files = _mapping(document.get("contract_files"), label="contract_files")
    for name in ("prompt", "schema"):
        record = _mapping(contract_files.get(name), label=f"contract_files.{name}")
        path = _project_path(record.get("path"), label=f"contract_files.{name}.path")
        expected_sha256 = record.get("sha256")
        _require(
            isinstance(expected_sha256, str)
            and SHA256_PATTERN.fullmatch(expected_sha256) is not None,
            f"contract_files.{name}.sha256 must be lowercase SHA-256",
        )
        _require(path.is_file() and not path.is_symlink(), f"{name} contract file is unavailable")
        _require(
            _sha256_file(path) == expected_sha256,
            f"{name} contract file SHA-256 does not match the frozen contract",
        )

    gold = _mapping(document.get("gold_sample"), label="gold_sample")
    for name in (
        "inventory_output_jsonl",
        "final_output_jsonl",
        "predictions_output_jsonl",
        "manifest_json",
    ):
        path = _project_path(gold.get(name), label=f"gold_sample.{name}")
        _require(
            path.is_relative_to(artifact_root),
            f"gold_sample.{name} must stay under docs/phase4/eval",
        )


def load_contract(path: Path = DEFAULT_CONFIG) -> FrozenContract:
    """Load and fail closed on any drift in the P4.2a gold/evaluation contract."""

    resolved = (path if path.is_absolute() else PROJECT_DIR / path).resolve()
    raw = resolved.read_bytes()
    document = _mapping(
        _strict_yaml_load(raw, label="P4.2a contract"),
        label="P4.2a contract",
    )

    _require(
        document.get("schema_version") == "p4.2a-event-extract-eval-v1",
        "unexpected P4.2a schema_version",
    )
    _require(document.get("production_writes_allowed") is False, "production writes must be false")
    input_contract = _mapping(document.get("input"), label="input")
    _require(
        input_contract.get("production_database") == "data/alphapilot.db",
        "production database path drifted",
    )
    _require(
        input_contract.get("open_mode") == "read_only_query_only",
        "database must remain read_only_query_only",
    )
    _require(
        input_contract.get("original_text_fields")
        == {
            "akshare_ths": ["title", "raw_payload.digest", "raw_payload.short"],
            "cninfo": ["title"],
            "sina_company_news": ["title"],
        },
        "original_text_fields drifted",
    )
    _require(
        input_contract.get("evidence_span_must_be_contiguous_substring") is True,
        "evidence-span substring gate is disabled",
    )

    snapshot = _mapping(input_contract.get("inventory_snapshot"), label="input.inventory_snapshot")
    _require(snapshot.get("max_news_item_id") == 423, "inventory cutoff must remain 423")
    _require(snapshot.get("expected_row_count") == 423, "inventory row count must remain 423")

    gold = _mapping(document.get("gold_sample"), label="gold_sample")
    _require(gold.get("version") == "p4.2a-gold-v1", "gold sample version drifted")
    _require(
        gold.get("deterministic_seed") == "alphapilot-p4.2a-gold-v1-20260803",
        "gold deterministic seed drifted",
    )
    _require(gold.get("annotation_status_initial") == "pending", "initial status drifted")
    _require(gold.get("labels_must_be_null") is True, "blind-label gate is disabled")
    _require(
        gold.get("model_predictions_must_be_absent") is True,
        "blind-prediction gate is disabled",
    )
    _require(
        gold.get("no_substitution_after_id_freeze") is True,
        "frozen-ID substitution gate is disabled",
    )
    _require(
        gold.get("insufficient_stratum_policy") == "fail_without_substitution",
        "insufficient-stratum policy drifted",
    )

    inventory = _mapping(gold.get("inventory_60"), label="gold_sample.inventory_60")
    _require(inventory.get("cutoff_news_item_id") == 423, "inventory cutoff drifted")
    inventory_strata = _contract_strata(
        inventory.get("strata"), label="gold_sample.inventory_60.strata"
    )
    expected_inventory = [
        ("cninfo", "bound", 24, True),
        ("akshare_ths", "bound", 9, False),
        ("akshare_ths", "null", 9, False),
        ("sina_company_news", "bound", 9, False),
        ("sina_company_news", "null", 9, False),
    ]
    _require(
        [
            (item.source, item.symbol_state, item.count, item.require_announcement_body)
            for item in inventory_strata
        ]
        == expected_inventory,
        "inventory strata drifted",
    )
    _require(sum(item.count for item in inventory_strata) == 60, "inventory must total 60")

    future = _mapping(gold.get("future_40"), label="gold_sample.future_40")
    _require(
        future.get("ready_after") == "2026-08-06T00:10:00+08:00",
        "future ready boundary drifted",
    )
    _require(
        future.get("trading_dates") == ["2026-08-04", "2026-08-05"],
        "future trading dates drifted",
    )
    future_strata = _contract_strata(
        future.get("per_date_strata"), label="gold_sample.future_40.per_date_strata"
    )
    expected_future = [
        ("cninfo", "bound", 10, True),
        ("akshare_ths", "bound", 2, False),
        ("akshare_ths", "null", 3, False),
        ("sina_company_news", "bound", 2, False),
        ("sina_company_news", "null", 3, False),
    ]
    _require(
        [
            (item.source, item.symbol_state, item.count, item.require_announcement_body)
            for item in future_strata
        ]
        == expected_future,
        "future strata drifted",
    )
    _require(sum(item.count for item in future_strata) == 20, "future daily strata must total 20")

    body = _mapping(document.get("announcement_body"), label="announcement_body")
    expected_body_values: dict[str, object] = {
        "eval_only": True,
        "source": "cninfo_pdf",
        "allowed_scheme": "https",
        "allowed_host": "static.cninfo.com.cn",
        "follow_redirects": False,
        "tls_verify": True,
        "max_pdf_bytes": 8 * 1024 * 1024,
        "required_magic": "%PDF-",
        "extractor_command": "pdftotext",
        "persist_pdf": False,
        "record_pdf_sha256": True,
        "record_full_text_sha256": True,
        "failure_policy": "block_sample_without_replacement",
    }
    for name, expected in expected_body_values.items():
        _require(body.get(name) == expected, f"announcement_body.{name} drifted")
    _positive_number(body.get("connect_timeout_seconds"), label="connect_timeout_seconds")
    _positive_number(body.get("read_timeout_seconds"), label="read_timeout_seconds")
    _positive_number(body.get("extractor_timeout_seconds"), label="extractor_timeout_seconds")
    _positive_integer(
        body.get("max_annotation_text_characters"),
        label="max_annotation_text_characters",
    )
    _positive_integer(
        body.get("minimum_extracted_characters"),
        label="minimum_extracted_characters",
    )

    annotation = _mapping(document.get("annotation"), label="annotation")
    _require(
        annotation.get("required_gold_fields")
        == ["symbols", "event_type", "direction", "materiality", "evidence_span"],
        "required gold fields drifted",
    )
    _require(annotation.get("optional_gold_fields") == ["notes"], "optional gold fields drifted")
    _require(annotation.get("owner_labels_only") is True, "owner-label gate is disabled")

    isolation = _mapping(document.get("isolation"), label="isolation")
    _require(isolation.get("forbidden_production_tables") == ["news_events"], "isolation drifted")
    _require(isolation.get("p4_2b_unlocked") is False, "P4.2b must remain locked")
    _require(isolation.get("proposals_or_orders_allowed") is False, "trade writes are forbidden")
    _validate_contract_paths(document)
    return FrozenContract(path=resolved, sha256=_sha256_bytes(raw), document=document)


def load_evaluation_design(
    path: Path = DEFAULT_EVALUATION_DESIGN,
) -> FrozenEvaluationDesign:
    """Load the v1.1 amendment and reverify its immutable v1 annotation base."""

    resolved = (path if path.is_absolute() else PROJECT_DIR / path).resolve()
    try:
        design: EventEvaluationDesign = load_event_evaluation_design(
            resolved,
            project_root=PROJECT_DIR,
        )
    except (EventEvaluationDesignError, OSError) as exc:
        raise GoldSampleError("P4.2a v1.1 evaluation design is invalid") from exc
    return FrozenEvaluationDesign(
        path=design.path,
        sha256=design.sha256,
        document=design.document,
        base_contract=load_contract(design.base_contract.path),
    )


def announcement_body_policy(contract: FrozenContract) -> AnnouncementBodyPolicy:
    raw = _mapping(contract.document["announcement_body"], label="announcement_body")
    return AnnouncementBodyPolicy(
        allowed_scheme=str(raw["allowed_scheme"]),
        allowed_host=str(raw["allowed_host"]),
        follow_redirects=bool(raw["follow_redirects"]),
        tls_verify=bool(raw["tls_verify"]),
        connect_timeout_seconds=float(raw["connect_timeout_seconds"]),
        read_timeout_seconds=float(raw["read_timeout_seconds"]),
        max_pdf_bytes=int(raw["max_pdf_bytes"]),
        required_magic=str(raw["required_magic"]).encode("ascii"),
        extractor_command=str(raw["extractor_command"]),
        extractor_timeout_seconds=float(raw["extractor_timeout_seconds"]),
        max_annotation_text_characters=int(raw["max_annotation_text_characters"]),
        minimum_extracted_characters=int(raw["minimum_extracted_characters"]),
    )


def _candidate_eligibility_enabled(design: FrozenEvaluationDesign) -> bool:
    raw = design.document.get("candidate_eligibility")
    if raw is None:
        return False
    policy = _mapping(raw, label="candidate_eligibility")
    body = announcement_body_policy(design.base_contract)
    expected = {
        "schema_version": "p4.2a-heldout-candidate-eligibility-v1",
        "deterministic_document_ineligible_reasons": list(
            MATERIALIZATION_INELIGIBLE_REASONS
        ),
        "minimum_extracted_characters": body.minimum_extracted_characters,
        "max_pdf_bytes": body.max_pdf_bytes,
        "transient_download_failures_fail_closed": True,
        "sample_only_from_eligible_pool": True,
        "insufficient_stratum_policy": "fail_without_substitution",
    }
    if policy != expected:
        raise GoldSampleError("candidate eligibility contract drifted")
    return True


@contextmanager
def open_read_only_database(path: Path) -> Iterator[sqlite3.Connection]:
    """Open a SQLite snapshot in URI read-only mode with query_only asserted."""

    resolved = path.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise ValueError(f"database must be one regular non-symlink file: {resolved}")
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?mode=ro",
        uri=True,
        timeout=5.0,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        query_only = connection.execute("PRAGMA query_only").fetchone()
        if query_only is None or int(query_only[0]) != 1:
            raise GoldSampleError("SQLite query_only could not be enabled")
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(news_items)").fetchall()
        }
        required = {
            "id",
            "source",
            "symbol",
            "title",
            "url",
            "published_at",
            "available_time",
            "content_hash",
            "raw_payload",
        }
        if not required.issubset(columns):
            missing = ", ".join(sorted(required - columns))
            raise GoldSampleError(f"news_items schema is missing: {missing}")
        connection.execute("BEGIN")
        yield connection
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def _parse_datetime(value: object, *, label: str, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise GoldSampleError(f"{label} must be a non-empty datetime")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise GoldSampleError(f"{label} is not an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_object(value: object, *, label: str) -> JsonObject:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if not isinstance(value, str):
        raise GoldSampleError(f"{label} must be a JSON object")
    try:
        parsed: object = json.loads(value, parse_constant=_reject_non_finite_json)
    except ValueError as exc:
        raise GoldSampleError(f"{label} is invalid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise GoldSampleError(f"{label} must decode to a JSON object")
    return {str(key): item for key, item in parsed.items()}


def _news_row(row: sqlite3.Row) -> NewsRow:
    news_item_id = row["id"]
    if isinstance(news_item_id, bool) or not isinstance(news_item_id, int) or news_item_id <= 0:
        raise GoldSampleError("news_items.id must be a positive integer")
    source = row["source"]
    title = row["title"]
    url = row["url"]
    content_hash = row["content_hash"]
    symbol = row["symbol"]
    if not isinstance(source, str) or source not in {
        "cninfo",
        "akshare_ths",
        "sina_company_news",
    }:
        raise GoldSampleError(f"news item {news_item_id} has unsupported source")
    if not isinstance(title, str) or not title:
        raise GoldSampleError(f"news item {news_item_id} has no title")
    if not isinstance(url, str) or not url:
        raise GoldSampleError(f"news item {news_item_id} has no URL")
    if not isinstance(content_hash, str) or SHA256_PATTERN.fullmatch(content_hash) is None:
        raise GoldSampleError(f"news item {news_item_id} has invalid content_hash")
    if symbol is not None and (
        not isinstance(symbol, str) or SIX_DIGIT_SYMBOL.fullmatch(symbol) is None
    ):
        raise GoldSampleError(f"news item {news_item_id} has invalid bound symbol")
    available = _parse_datetime(
        row["available_time"], label=f"news item {news_item_id} available_time"
    )
    assert available is not None
    return NewsRow(
        news_item_id=news_item_id,
        source=source,
        ingested_symbol=symbol,
        title=title,
        url=url,
        published_at=_parse_datetime(
            row["published_at"],
            label=f"news item {news_item_id} published_at",
            nullable=True,
        ),
        available_time=available,
        content_hash=content_hash,
        raw_payload=_json_object(row["raw_payload"], label=f"news item {news_item_id} raw_payload"),
    )


NEWS_QUERY = """
SELECT id, source, symbol, title, url, published_at, available_time, content_hash, raw_payload
FROM news_items
WHERE id {operator} ?
  AND source IN ('cninfo', 'akshare_ths', 'sina_company_news')
ORDER BY id
"""


def _load_news_rows(
    connection: sqlite3.Connection, *, cutoff: int, after_cutoff: bool
) -> list[NewsRow]:
    operator = ">" if after_cutoff else "<="
    rows = connection.execute(NEWS_QUERY.format(operator=operator), (cutoff,)).fetchall()
    return [_news_row(row) for row in rows]


def inventory_database_snapshot(
    connection: sqlite3.Connection, contract: FrozenContract
) -> JsonObject:
    input_contract = _mapping(contract.document["input"], label="input")
    expected = _mapping(input_contract["inventory_snapshot"], label="input.inventory_snapshot")
    cutoff = int(expected["max_news_item_id"])
    row = connection.execute(
        """
        SELECT COUNT(*) AS row_count, MAX(id) AS max_id, MAX(available_time) AS max_available_time
        FROM news_items
        WHERE id <= ?
        """,
        (cutoff,),
    ).fetchone()
    if row is None:
        raise GoldSampleError("could not inspect inventory snapshot")
    observed_count = int(row["row_count"])
    observed_max_id = int(row["max_id"]) if row["max_id"] is not None else None
    observed_max_time = _parse_datetime(
        row["max_available_time"], label="inventory max_available_time"
    )
    expected_max_time = _parse_datetime(
        expected["max_available_time_utc"],
        label="contract inventory max_available_time_utc",
    )
    if observed_count != int(expected["expected_row_count"]):
        raise GoldSampleError(
            "inventory snapshot row count drifted: "
            f"expected {expected['expected_row_count']}, observed {observed_count}"
        )
    if observed_max_id != cutoff:
        raise GoldSampleError(
            f"inventory snapshot max id drifted: expected {cutoff}, observed {observed_max_id}"
        )
    if observed_max_time != expected_max_time:
        raise GoldSampleError("inventory snapshot max available_time drifted")
    return {
        "cutoff_news_item_id": cutoff,
        "expected_row_count": int(expected["expected_row_count"]),
        "observed_row_count": observed_count,
        "observed_max_news_item_id": observed_max_id,
        "observed_max_available_time_utc": _iso_utc(observed_max_time),
        "contract_observed_at_utc": expected["observed_at_utc"],
    }


def current_database_snapshot(connection: sqlite3.Connection) -> JsonObject:
    row = connection.execute(
        """
        SELECT COUNT(*) AS row_count, MAX(id) AS max_id, MAX(available_time) AS max_available_time
        FROM news_items
        """
    ).fetchone()
    if row is None:
        raise GoldSampleError("could not inspect current database snapshot")
    maximum = (
        _parse_datetime(row["max_available_time"], label="database max_available_time")
        if row["max_available_time"] is not None
        else None
    )
    return {
        "row_count": int(row["row_count"]),
        "max_news_item_id": int(row["max_id"]) if row["max_id"] is not None else None,
        "max_available_time_utc": _iso_utc(maximum),
        "open_mode": "mode=ro + PRAGMA query_only=ON",
    }


def deterministic_rank(
    *,
    seed: str,
    group: str,
    source: str,
    symbol_state: str,
    content_hash: str,
    news_item_id: int,
) -> str:
    """Return the frozen SHA-256 rank over the pre-registered ordered fields."""

    rank_input = "|".join((seed, group, source, symbol_state, content_hash, str(news_item_id)))
    return _sha256_bytes(rank_input.encode("utf-8"))


def heldout_prediction_rank(*, seed: str, news_item_id: int, input_sha256: str) -> str:
    """Return the pre-registered NUL-delimited heldout-v1.1 selection rank."""

    if not seed:
        raise GoldSampleError("heldout sampling seed must be non-empty")
    if news_item_id <= 0:
        raise GoldSampleError("heldout prediction news_item_id must be positive")
    if SHA256_PATTERN.fullmatch(input_sha256) is None:
        raise GoldSampleError("heldout prediction input_sha256 is invalid")
    rank_input = f"{seed}\0{news_item_id}\0{input_sha256}"
    return _sha256_bytes(rank_input.encode("utf-8"))


def select_heldout_positive_predictions(
    records: Sequence[JsonObject],
    *,
    seed: str,
    count: int,
) -> tuple[list[JsonObject], list[JsonObject]]:
    """Select exactly ``count`` successful predicted positives without replacement."""

    if count <= 0:
        raise GoldSampleError("heldout sample count must be positive")
    eligible: list[tuple[str, int, JsonObject]] = []
    seen: set[int] = set()
    for record in records:
        news_item_id = record.get("news_item_id")
        if (
            isinstance(news_item_id, bool)
            or not isinstance(news_item_id, int)
            or news_item_id <= 0
        ):
            raise GoldSampleError("heldout prediction has invalid news_item_id")
        if news_item_id in seen:
            raise GoldSampleError(f"heldout predictions duplicate news item {news_item_id}")
        seen.add(news_item_id)
        if record.get("status") != "ok":
            continue
        prediction = record.get("prediction")
        if not isinstance(prediction, Mapping):
            raise GoldSampleError(
                f"successful heldout prediction {news_item_id} has no prediction object"
            )
        materiality = prediction.get("materiality")
        if isinstance(materiality, bool) or not isinstance(materiality, int):
            raise GoldSampleError(
                f"heldout prediction {news_item_id} materiality is invalid"
            )
        if materiality < 2:
            continue
        input_sha256 = record.get("input_sha256")
        declared_input_sha256 = record.get("declared_input_sha256")
        text_sha256 = record.get("text_sha256")
        if (
            not isinstance(input_sha256, str)
            or SHA256_PATTERN.fullmatch(input_sha256) is None
            or not isinstance(text_sha256, str)
            or SHA256_PATTERN.fullmatch(text_sha256) is None
            or (
                declared_input_sha256 is not None
                and (
                    not isinstance(declared_input_sha256, str)
                    or SHA256_PATTERN.fullmatch(declared_input_sha256) is None
                    or declared_input_sha256 == input_sha256
                )
            )
        ):
            raise GoldSampleError(
                f"heldout prediction {news_item_id} input/text SHA-256 is invalid"
            )
        rank = heldout_prediction_rank(
            seed=seed,
            news_item_id=news_item_id,
            input_sha256=input_sha256,
        )
        eligible.append((rank, news_item_id, record))
    eligible.sort(key=lambda item: (item[0], item[1]))
    if len(eligible) < count:
        raise GoldSampleError(
            "heldout predicted-positive pool is insufficient without substitution: "
            f"required={count}, available={len(eligible)}"
        )
    selected_records = [record for _, _, record in eligible[:count]]
    selection_evidence = [
        {
            "news_item_id": news_item_id,
            "input_sha256": record["input_sha256"],
            **(
                {"declared_input_sha256": record["declared_input_sha256"]}
                if "declared_input_sha256" in record
                else {}
            ),
            "text_sha256": record["text_sha256"],
            "selection_rank_sha256": rank,
        }
        for rank, news_item_id, record in eligible[:count]
    ]
    return selected_records, selection_evidence


def select_stratified_rows(
    rows: Sequence[NewsRow],
    *,
    strata: Sequence[Stratum],
    seed: str,
    group: str,
    trading_date: date | None = None,
) -> list[SelectedNews]:
    """Select exact quotas; a short stratum aborts instead of borrowing or replacing."""

    selected: list[SelectedNews] = []
    for stratum in strata:
        candidates = [
            row
            for row in rows
            if row.source == stratum.source
            and row.symbol_state == stratum.symbol_state
            and (
                trading_date is None
                or row.available_time.astimezone(SHANGHAI).date() == trading_date
            )
        ]
        ranked = sorted(
            (
                (
                    deterministic_rank(
                        seed=seed,
                        group=group,
                        source=row.source,
                        symbol_state=row.symbol_state,
                        content_hash=row.content_hash,
                        news_item_id=row.news_item_id,
                    ),
                    row,
                )
                for row in candidates
            ),
            key=lambda item: (item[0], item[1].news_item_id),
        )
        if len(ranked) < stratum.count:
            date_suffix = f", trading_date={trading_date.isoformat()}" if trading_date else ""
            raise GoldSampleError(
                "insufficient frozen stratum without substitution: "
                f"source={stratum.source}, symbol_state={stratum.symbol_state}{date_suffix}, "
                f"required={stratum.count}, available={len(ranked)}"
            )
        selected.extend(
            SelectedNews(
                row=row,
                sample_group=group,
                trading_date=trading_date,
                stratum=stratum,
                rank_sha256=rank,
            )
            for rank, row in ranked[: stratum.count]
        )
    identifiers = [item.row.news_item_id for item in selected]
    if len(identifiers) != len(set(identifiers)):
        raise GoldSampleError("strata overlap produced duplicate frozen IDs")
    return selected


def _validate_pdf_url(url: str, policy: AnnouncementBodyPolicy) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme.lower() != policy.allowed_scheme
        or (parsed.hostname or "").lower() != policy.allowed_host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.fragment
        or not parsed.path.lower().endswith(".pdf")
    ):
        raise GoldSampleError(
            "CNInfo announcement body URL must be one HTTPS PDF on static.cninfo.com.cn"
        )


def download_cninfo_pdf(url: str, policy: AnnouncementBodyPolicy) -> bytes:
    """Bounded eval-only PDF download with TLS verification and no redirects."""

    _validate_pdf_url(url, policy)
    timeout = httpx.Timeout(
        connect=policy.connect_timeout_seconds,
        read=policy.read_timeout_seconds,
        write=policy.read_timeout_seconds,
        pool=policy.connect_timeout_seconds,
    )
    try:
        with (
            httpx.Client(
                verify=policy.tls_verify,
                follow_redirects=policy.follow_redirects,
                timeout=timeout,
            ) as client,
            client.stream("GET", url, headers={"Accept": "application/pdf"}) as response,
        ):
            if response.status_code != 200:
                raise GoldSampleError(
                    f"CNInfo PDF returned non-success HTTP {response.status_code}"
                )
            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError as exc:
                    raise GoldSampleError("CNInfo PDF Content-Length is invalid") from exc
                if declared_size < 0:
                    raise GoldSampleError("CNInfo PDF Content-Length must be non-negative")
                if declared_size > policy.max_pdf_bytes:
                    raise CandidateDocumentIneligible(
                        reason="pdf_exceeds_size_bound",
                        measured_value=declared_size,
                        gate_value=policy.max_pdf_bytes,
                        pdf_sha256=None,
                    )
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > policy.max_pdf_bytes:
                    raise CandidateDocumentIneligible(
                        reason="pdf_exceeds_size_bound",
                        measured_value=total,
                        gate_value=policy.max_pdf_bytes,
                        pdf_sha256=None,
                    )
                chunks.append(chunk)
    except GoldSampleError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise GoldSampleError(f"CNInfo PDF download failed: {type(exc).__name__}") from exc
    payload = b"".join(chunks)
    if not payload.startswith(policy.required_magic):
        raise GoldSampleError("CNInfo response does not start with %PDF-")
    return payload


def extract_cninfo_pdf_text(pdf_bytes: bytes, policy: AnnouncementBodyPolicy) -> ExtractedPdfText:
    """Run pdftotext only against temporary files and retain no PDF artifact."""

    if not pdf_bytes.startswith(policy.required_magic):
        raise GoldSampleError("CNInfo response does not start with %PDF-")
    with tempfile.TemporaryDirectory(prefix="alphapilot-p4.2a-pdf-") as temporary:
        temporary_dir = Path(temporary)
        pdf_path = temporary_dir / "announcement.pdf"
        text_path = temporary_dir / "announcement.txt"
        pdf_path.write_bytes(pdf_bytes)
        try:
            completed = subprocess.run(
                [
                    policy.extractor_command,
                    "-enc",
                    "UTF-8",
                    str(pdf_path),
                    str(text_path),
                ],
                check=False,
                capture_output=True,
                timeout=policy.extractor_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GoldSampleError(f"pdftotext failed: {type(exc).__name__}") from exc
        if completed.returncode != 0 or not text_path.is_file():
            raise GoldSampleError(f"pdftotext exited with status {completed.returncode}")
        text_bytes = text_path.read_bytes()
    try:
        text = text_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GoldSampleError("pdftotext output is not UTF-8") from exc
    if len(text.strip()) < policy.minimum_extracted_characters:
        raise CandidateDocumentIneligible(
            reason="pdf_text_below_min_char_gate",
            measured_value=len(text.strip()),
            gate_value=policy.minimum_extracted_characters,
            pdf_sha256=_sha256_bytes(pdf_bytes),
        )
    return ExtractedPdfText(
        text=text,
        text_sha256=_sha256_bytes(text_bytes),
        full_character_count=len(text),
    )


def extracted_pdf_text_fixture(text: str) -> ExtractedPdfText:
    """Construct deterministic mocked extraction evidence for offline tests."""

    encoded = text.encode("utf-8")
    return ExtractedPdfText(
        text=text,
        text_sha256=_sha256_bytes(encoded),
        full_character_count=len(text),
    )


def _text_at_path(row: NewsRow, field_path: str) -> str | None:
    if field_path == "title":
        return row.title
    if not field_path.startswith("raw_payload."):
        raise GoldSampleError(f"unsupported original_text field: {field_path}")
    value: object = row.raw_payload
    for component in field_path.split(".")[1:]:
        if not isinstance(value, Mapping):
            return None
        value = value.get(component)
    return value if isinstance(value, str) and value else None


def _base_original_text(row: NewsRow, contract: FrozenContract) -> str:
    input_contract = _mapping(contract.document["input"], label="input")
    fields_by_source = _mapping(
        input_contract["original_text_fields"], label="input.original_text_fields"
    )
    fields = fields_by_source.get(row.source)
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)):
        raise GoldSampleError(f"no original_text field contract for {row.source}")
    values = [
        value
        for field in fields
        if isinstance(field, str)
        for value in [_text_at_path(row, field)]
        if value is not None
    ]
    if not values:
        raise GoldSampleError(f"news item {row.news_item_id} has no original_text")
    return "\n".join(values)


def _empty_body_evidence(required: bool) -> JsonObject:
    return {
        "required": required,
        "source": None,
        "url": None,
        "pdf_sha256": None,
        "full_text_sha256": None,
        "full_text_character_count": None,
        "annotation_text_character_count": None,
        "body_characters_in_original_text": None,
        "text_truncated": False,
        "pdf_persisted": False,
    }


def _original_text_and_body_evidence(
    selected: SelectedNews,
    contract: FrozenContract,
    *,
    pdf_fetcher: PdfFetcher,
    pdf_text_extractor: PdfTextExtractor,
) -> tuple[str, JsonObject]:
    row = selected.row
    base_text = _base_original_text(row, contract)
    if not selected.stratum.require_announcement_body:
        return base_text, _empty_body_evidence(False)
    if row.source != "cninfo":
        raise GoldSampleError("announcement-body strata are restricted to CNInfo")

    policy = announcement_body_policy(contract)
    _validate_pdf_url(row.url, policy)
    pdf_bytes = pdf_fetcher(row.url, policy)
    if len(pdf_bytes) > policy.max_pdf_bytes:
        raise CandidateDocumentIneligible(
            reason="pdf_exceeds_size_bound",
            measured_value=len(pdf_bytes),
            gate_value=policy.max_pdf_bytes,
            pdf_sha256=_sha256_bytes(pdf_bytes),
        )
    if not pdf_bytes.startswith(policy.required_magic):
        raise GoldSampleError("CNInfo response does not start with %PDF-")
    extracted = pdf_text_extractor(pdf_bytes, policy)
    if extracted.full_character_count != len(extracted.text):
        raise GoldSampleError("pdftotext full character count is inconsistent")
    if extracted.text_sha256 != _sha256_bytes(extracted.text.encode("utf-8")):
        raise GoldSampleError("pdftotext full text SHA-256 is inconsistent")
    if len(extracted.text.strip()) < policy.minimum_extracted_characters:
        raise CandidateDocumentIneligible(
            reason="pdf_text_below_min_char_gate",
            measured_value=len(extracted.text.strip()),
            gate_value=policy.minimum_extracted_characters,
            pdf_sha256=_sha256_bytes(pdf_bytes),
        )

    # The annotation and model must see the same deterministic prefix of the
    # actual extracted body. The separately stored title is not prepended.
    original_text = extracted.text[: policy.max_annotation_text_characters]
    if not original_text.strip():
        raise GoldSampleError("CNInfo annotation text prefix is empty")
    evidence: JsonObject = {
        "required": True,
        "source": "cninfo_pdf",
        "url": row.url,
        "pdf_sha256": _sha256_bytes(pdf_bytes),
        "full_text_sha256": extracted.text_sha256,
        "full_text_character_count": extracted.full_character_count,
        "annotation_text_character_count": len(original_text),
        "body_characters_in_original_text": len(original_text),
        "text_truncated": extracted.full_character_count > len(original_text),
        "pdf_persisted": False,
    }
    return original_text, evidence


def _record_string(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise GoldSampleError(f"gold record {field} must be non-empty text")
    return value


def _record_optional_string(record: Mapping[str, object], field: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise GoldSampleError(f"gold record {field} must be null or non-empty text")
    return value


def _input_sha256(record: Mapping[str, object], event_contract: EventExtractContract) -> str:
    news_item_id = record.get("news_item_id")
    if isinstance(news_item_id, bool) or not isinstance(news_item_id, int):
        raise GoldSampleError("gold record news_item_id must be an integer")
    user_json = build_event_extract_user_input(
        event_contract,
        news_item_id=news_item_id,
        source=_record_string(record, "source"),
        ingested_symbol=_record_optional_string(record, "ingested_symbol"),
        title=_record_string(record, "title"),
        original_text=_record_string(record, "original_text"),
        published_at=_record_optional_string(record, "published_at"),
        available_time=_record_string(record, "available_time"),
        body_state=_record_string(record, "body_state"),
    )
    digest = event_extract_input_sha256(user_json)
    if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
        raise GoldSampleError("core event-extract input hash is invalid")
    return digest


def _declared_input_sha256(
    record: Mapping[str, object],
    event_contract: EventExtractContract,
) -> str:
    """Recompute the immutable input identity declared by a frozen record.

    The v1.6 selector protocol deliberately preserves the pre-selector
    eight-field hash in the frozen dev artifact while using a second hash for
    the candidate-based model request.  Older contracts have only one identity.
    """

    if not event_contract.evidence_candidate_selection:
        return _input_sha256(record, event_contract)
    user_json = build_declared_legacy_input_identity(
        event_contract,
        news_item_id=_positive_integer(
            record.get("news_item_id"), label="gold record news_item_id"
        ),
        source=_record_string(record, "source"),
        ingested_symbol=_record_optional_string(record, "ingested_symbol"),
        title=_record_string(record, "title"),
        original_text=_record_string(record, "original_text"),
        published_at=_record_optional_string(record, "published_at"),
        available_time=_record_string(record, "available_time"),
        body_state=_record_string(record, "body_state"),
    )
    digest = event_extract_input_sha256(user_json)
    if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
        raise GoldSampleError("declared event-extract input hash is invalid")
    return digest


def compute_input_sha256(record: Mapping[str, object], contract_path: Path = DEFAULT_CONFIG) -> str:
    """Hash the complete canonical eight-field user JSON built by the core."""

    event_contract = load_event_extract_contract(contract_path)
    return _input_sha256(record, event_contract)


def _body_state(row: NewsRow, require_announcement_body: bool) -> str:
    if require_announcement_body:
        return "announcement_body"
    if row.source == "akshare_ths":
        return "title_digest_short"
    return "title_only"


def _gold_null_template(contract: FrozenContract) -> JsonObject:
    annotation = _mapping(contract.document["annotation"], label="annotation")
    fields = list(annotation["required_gold_fields"]) + list(annotation["optional_gold_fields"])
    return {str(field): None for field in fields}


def materialize_selected_rows(
    selected: Sequence[SelectedNews],
    contract: FrozenContract,
    *,
    starting_index: int,
    pdf_fetcher: PdfFetcher = download_cninfo_pdf,
    pdf_text_extractor: PdfTextExtractor = extract_cninfo_pdf_text,
) -> list[JsonObject]:
    """Materialize already-frozen IDs; a body failure aborts without replacement."""

    gold = _mapping(contract.document["gold_sample"], label="gold_sample")
    event_contract = load_event_extract_contract(contract.path)
    records: list[JsonObject] = []
    for offset, item in enumerate(selected):
        try:
            original_text, body_evidence = _original_text_and_body_evidence(
                item,
                contract,
                pdf_fetcher=pdf_fetcher,
                pdf_text_extractor=pdf_text_extractor,
            )
        except Exception as exc:
            if isinstance(exc, GoldSampleError):
                raise GoldSampleError(
                    f"frozen news item {item.row.news_item_id} body extraction failed; "
                    "sample blocked without replacement"
                ) from exc
            raise
        text_sha256 = _sha256_bytes(original_text.encode("utf-8"))
        record: JsonObject = {
            "schema_version": "p4.2a-gold-annotation-item-v1",
            "sample_version": gold["version"],
            "contract_sha256": contract.sha256,
            "sample_index": starting_index + offset,
            "sample_group": item.sample_group,
            "trading_date": item.trading_date.isoformat() if item.trading_date else None,
            "stratum": {
                "source": item.stratum.source,
                "symbol_state": item.stratum.symbol_state,
                "require_announcement_body": item.stratum.require_announcement_body,
            },
            "rank_sha256": item.rank_sha256,
            "news_item_id": item.row.news_item_id,
            "source": item.row.source,
            "url": item.row.url,
            "title": item.row.title,
            "ingested_symbol": item.row.ingested_symbol,
            "published_at": _iso_utc(item.row.published_at),
            "available_time": _iso_utc(item.row.available_time),
            "original_text": original_text,
            "body_state": _body_state(item.row, item.stratum.require_announcement_body),
            "content_hash": item.row.content_hash,
            "text_sha256": text_sha256,
            "body_evidence": body_evidence,
            "annotation_status": gold["annotation_status_initial"],
            "annotation_owner": None,
            "annotated_at": None,
            "gold": _gold_null_template(contract),
        }
        record["input_sha256"] = _input_sha256(record, event_contract)
        validate_blind_record(record, contract, event_contract=event_contract)
        records.append(record)
    return records


def validate_body_evidence(
    record: Mapping[str, object],
    *,
    label: str,
) -> None:
    body_evidence = record.get("body_evidence")
    if not isinstance(body_evidence, Mapping) or set(body_evidence) != BODY_EVIDENCE_FIELDS:
        raise GoldSampleError(f"{label} body_evidence fields drifted")
    original_text = record.get("original_text")
    text_sha256 = record.get("text_sha256")
    if not isinstance(original_text, str) or not original_text:
        raise GoldSampleError(f"{label} has no original_text")
    required = body_evidence.get("required")
    if required is True:
        annotation_count = body_evidence.get("annotation_text_character_count")
        body_count = body_evidence.get("body_characters_in_original_text")
        full_count = body_evidence.get("full_text_character_count")
        truncated = body_evidence.get("text_truncated")
        pdf_sha256 = body_evidence.get("pdf_sha256")
        full_sha256 = body_evidence.get("full_text_sha256")
        if (
            record.get("source") != "cninfo"
            or record.get("body_state") != "announcement_body"
            or body_evidence.get("source") != "cninfo_pdf"
            or body_evidence.get("url") != record.get("url")
            or body_evidence.get("pdf_persisted") is not False
            or not isinstance(pdf_sha256, str)
            or SHA256_PATTERN.fullmatch(pdf_sha256) is None
            or not isinstance(full_sha256, str)
            or SHA256_PATTERN.fullmatch(full_sha256) is None
            or isinstance(annotation_count, bool)
            or not isinstance(annotation_count, int)
            or annotation_count != len(original_text)
            or body_count != len(original_text)
            or isinstance(full_count, bool)
            or not isinstance(full_count, int)
            or full_count < len(original_text)
            or not isinstance(truncated, bool)
            or truncated is not (full_count > len(original_text))
            or (not truncated and full_sha256 != text_sha256)
        ):
            raise GoldSampleError(f"{label} announcement body evidence drifted")
    elif required is False:
        if dict(body_evidence) != _empty_body_evidence(False):
            raise GoldSampleError(f"{label} non-body evidence drifted")
    else:
        raise GoldSampleError(f"{label} body_evidence.required is invalid")


def validate_blind_record(
    record: Mapping[str, object],
    contract: FrozenContract,
    *,
    event_contract: EventExtractContract | None = None,
) -> None:
    news_item_id = record.get("news_item_id")
    if isinstance(news_item_id, bool) or not isinstance(news_item_id, int) or news_item_id <= 0:
        raise GoldSampleError("blind record has invalid news_item_id")
    if MODEL_PREDICTION_KEYS.intersection(record):
        raise GoldSampleError(f"blind record {news_item_id} contains model predictions")
    unexpected_fields = set(record) - ANNOTATION_ITEM_FIELDS
    missing_fields = ANNOTATION_ITEM_FIELDS - set(record)
    if unexpected_fields or missing_fields:
        raise GoldSampleError(
            f"blind record {news_item_id} fields drifted: "
            f"unexpected={sorted(unexpected_fields)}, missing={sorted(missing_fields)}"
        )
    if record.get("schema_version") != "p4.2a-gold-annotation-item-v1":
        raise GoldSampleError(f"blind record {news_item_id} schema version drifted")
    if record.get("sample_version") != "p4.2a-gold-v1":
        raise GoldSampleError(f"blind record {news_item_id} sample version drifted")
    if record.get("contract_sha256") != contract.sha256:
        raise GoldSampleError(f"blind record {news_item_id} contract drifted")
    if record.get("annotation_status") != "pending":
        raise GoldSampleError(f"blind record {news_item_id} is not pending")
    if record.get("annotation_owner") is not None or record.get("annotated_at") is not None:
        raise GoldSampleError(f"blind record {news_item_id} has annotation provenance")
    stratum = record.get("stratum")
    body_evidence = record.get("body_evidence")
    if not isinstance(stratum, Mapping) or set(stratum) != STRATUM_FIELDS:
        raise GoldSampleError(f"blind record {news_item_id} stratum fields drifted")
    if not isinstance(body_evidence, Mapping) or set(body_evidence) != BODY_EVIDENCE_FIELDS:
        raise GoldSampleError(f"blind record {news_item_id} body_evidence fields drifted")
    if (
        stratum.get("source") != record.get("source")
        or stratum.get("symbol_state")
        != ("null" if record.get("ingested_symbol") is None else "bound")
        or stratum.get("require_announcement_body") is not body_evidence.get("required")
    ):
        raise GoldSampleError(f"blind record {news_item_id} nested identity drifted")
    validate_body_evidence(record, label=f"blind record {news_item_id}")
    gold = record.get("gold")
    expected_gold = _gold_null_template(contract)
    if not isinstance(gold, Mapping) or dict(gold) != expected_gold:
        raise GoldSampleError(f"blind record {news_item_id} must have only null gold labels")
    original_text = record.get("original_text")
    if not isinstance(original_text, str) or not original_text:
        raise GoldSampleError(f"blind record {news_item_id} has no original_text")
    if record.get("text_sha256") != _sha256_bytes(original_text.encode("utf-8")):
        raise GoldSampleError(f"blind record {news_item_id} text SHA-256 drifted")
    resolved_event_contract = event_contract or load_event_extract_contract(contract.path)
    if record.get("input_sha256") != _input_sha256(record, resolved_event_contract):
        raise GoldSampleError(f"blind record {news_item_id} input SHA-256 drifted")


def owner_forbidden_field_paths(
    value: object,
    forbidden_fields: frozenset[str],
    *,
    prefix: str = "$",
) -> list[str]:
    """Return recursively discovered model/selection keys in an owner payload."""

    violations: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            path = f"{prefix}.{key}"
            if key in forbidden_fields:
                violations.append(path)
            violations.extend(
                owner_forbidden_field_paths(item, forbidden_fields, prefix=path)
            )
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            violations.extend(
                owner_forbidden_field_paths(
                    item,
                    forbidden_fields,
                    prefix=f"{prefix}[{index}]",
                )
            )
    return violations


def _json_line_bytes(records: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(
        json.dumps(
            dict(record),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
        for record in records
    )


def _artifact_path(path: Path, artifact_root: Path) -> Path:
    root = artifact_root.resolve()
    if root.exists() and root.is_symlink():
        raise ValueError("artifact root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"artifact path escapes docs/phase4/eval: {path}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    if resolved.parent.is_symlink():
        raise ValueError("artifact directory must not be a symlink")
    return resolved


def _new_artifact_path(path: Path, artifact_root: Path) -> Path:
    resolved = _artifact_path(path, artifact_root)
    if resolved.exists() or resolved.is_symlink():
        raise FileExistsError(f"refusing to overwrite P4.2a gold artifact: {resolved}")
    return resolved


def _write_new_bytes(path: Path, payload: bytes) -> None:
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


def _write_create_only_bundle(payloads: Mapping[Path, bytes]) -> dict[Path, str]:
    """Publish an exact create-only artifact bundle with partial-run recovery."""

    if not payloads or len(set(payloads)) != len(payloads):
        raise ValueError("create-only bundle paths must be non-empty and unique")
    expected = {path: _sha256_bytes(payload) for path, payload in payloads.items()}
    for path, digest in expected.items():
        if (path.exists() or path.is_symlink()) and (
            _existing_file_sha256(path, label="bundle") != digest
        ):
            raise FileExistsError(
                f"refusing to overwrite mismatched P4.2a artifact: {path}"
            )
    created: list[tuple[Path, os.stat_result]] = []
    try:
        for path, payload in payloads.items():
            if path.exists():
                continue
            _write_new_bytes(path, payload)
            created.append((path, path.stat()))
    except BaseException:
        for path, created_stat in reversed(created):
            current = path.stat() if path.exists() else None
            if (
                current is not None
                and current.st_dev == created_stat.st_dev
                and current.st_ino == created_stat.st_ino
            ):
                path.unlink()
                _fsync_directory(path.parent)
        raise
    return expected


def _stage_payload_for_link(path: Path, payload: bytes) -> Path:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".staged",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary_path
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_manifest_for_recovery(path: Path) -> JsonObject:
    if not path.is_file() or path.is_symlink():
        raise FileExistsError(f"manifest recovery target is not a regular file: {path}")
    try:
        value: object = json.loads(
            path.read_bytes(),
            parse_constant=_reject_non_finite_json,
        )
    except ValueError as exc:
        raise FileExistsError(f"manifest recovery target is invalid JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise FileExistsError(f"manifest recovery target is not an object: {path}")
    return {str(key): item for key, item in value.items()}


def _manifest_recovery_identity(manifest: Mapping[str, object]) -> JsonObject:
    fields = (
        "schema_version",
        "sample_version",
        "contract",
        "artifacts",
        "frozen_news_item_ids",
        "frozen_items",
        "strata",
        "announcement_body",
        "blind_at_creation",
        "no_substitution_after_id_freeze",
    )
    return {field: manifest.get(field) for field in fields}


def _existing_file_sha256(path: Path, *, label: str) -> str:
    if not path.is_file() or path.is_symlink():
        raise FileExistsError(f"{label} recovery target is not a regular file: {path}")
    return _sha256_file(path)


def _manifest_matches_recovery_identity(
    path: Path,
    expected_manifest: Mapping[str, object],
) -> bool:
    try:
        existing = _read_manifest_for_recovery(path)
    except FileExistsError:
        return False
    return _manifest_recovery_identity(existing) == _manifest_recovery_identity(expected_manifest)


def _write_new_final_manifest_pair(
    *,
    final_path: Path,
    final_payload: bytes,
    manifest_path: Path,
    manifest_payload: bytes,
    expected_manifest: Mapping[str, object],
) -> tuple[str, str]:
    """Create or hash-recover the immutable final/manifest pair without overwrites."""

    if final_path == manifest_path:
        raise ValueError("final and manifest artifact paths must differ")
    expected_final_sha256 = _sha256_bytes(final_payload)
    final_exists = final_path.exists() or final_path.is_symlink()
    manifest_exists = manifest_path.exists() or manifest_path.is_symlink()

    if final_exists:
        observed_final_sha256 = _existing_file_sha256(final_path, label="final")
        if observed_final_sha256 != expected_final_sha256:
            raise FileExistsError(
                f"refusing to overwrite mismatched P4.2a final artifact: {final_path}"
            )
    if manifest_exists and not _manifest_matches_recovery_identity(
        manifest_path, expected_manifest
    ):
        raise FileExistsError(
            f"refusing to overwrite mismatched P4.2a manifest artifact: {manifest_path}"
        )

    if final_exists and manifest_exists:
        return expected_final_sha256, _sha256_file(manifest_path)
    if final_exists:
        try:
            _write_new_bytes(manifest_path, manifest_payload)
        except FileExistsError:
            if not _manifest_matches_recovery_identity(manifest_path, expected_manifest):
                raise
        return expected_final_sha256, _sha256_file(manifest_path)
    if manifest_exists:
        try:
            _write_new_bytes(final_path, final_payload)
        except FileExistsError:
            if _existing_file_sha256(final_path, label="final") != expected_final_sha256:
                raise
        return expected_final_sha256, _sha256_file(manifest_path)

    staged_final = _stage_payload_for_link(final_path, final_payload)
    try:
        staged_manifest = _stage_payload_for_link(manifest_path, manifest_payload)
    except BaseException:
        staged_final.unlink(missing_ok=True)
        raise
    created_final_stat: os.stat_result | None = None
    try:
        try:
            os.link(staged_final, final_path)
            created_final_stat = final_path.stat()
        except FileExistsError:
            if _existing_file_sha256(final_path, label="final") != expected_final_sha256:
                raise
        try:
            os.link(staged_manifest, manifest_path)
        except FileExistsError:
            if _manifest_matches_recovery_identity(manifest_path, expected_manifest):
                _fsync_directory(manifest_path.parent)
                return expected_final_sha256, _sha256_file(manifest_path)
            raise
        _fsync_directory(final_path.parent)
        if manifest_path.parent != final_path.parent:
            _fsync_directory(manifest_path.parent)
    except OSError:
        current_stat = final_path.stat() if final_path.exists() else None
        if (
            created_final_stat is not None
            and current_stat is not None
            and current_stat.st_dev == created_final_stat.st_dev
            and current_stat.st_ino == created_final_stat.st_ino
        ):
            final_path.unlink()
            _fsync_directory(final_path.parent)
        raise
    finally:
        staged_final.unlink(missing_ok=True)
        staged_manifest.unlink(missing_ok=True)
    return expected_final_sha256, _sha256_file(manifest_path)


def _load_jsonl(path: Path) -> list[JsonObject]:
    if not path.is_file() or path.is_symlink():
        raise GoldSampleError(f"JSONL artifact is unavailable: {path}")
    records: list[JsonObject] = []

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise GoldSampleError(f"JSONL contains duplicate key: {key}")
            result[key] = value
        return result

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            raise GoldSampleError(f"JSONL has blank line {line_number}")
        try:
            value: object = json.loads(
                line,
                object_pairs_hook=reject_duplicates,
                parse_constant=_reject_non_finite_json,
            )
        except ValueError as exc:
            raise GoldSampleError(f"JSONL line {line_number} is invalid") from exc
        if not isinstance(value, dict):
            raise GoldSampleError(f"JSONL line {line_number} must be an object")
        records.append(value)
    return records


def _load_jsonl_with_sha256(path: Path, *, label: str) -> tuple[list[JsonObject], str]:
    payload = path.read_bytes()
    records = _load_jsonl(path)
    return records, _sha256_bytes(payload)


def _load_json_with_sha256(path: Path, *, label: str) -> tuple[JsonObject, str]:
    if not path.is_file() or path.is_symlink():
        raise GoldSampleError(f"{label} is unavailable: {path}")
    payload = path.read_bytes()

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise GoldSampleError(f"{label} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value: object = json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=_reject_non_finite_json,
        )
    except ValueError as exc:
        raise GoldSampleError(f"{label} is invalid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise GoldSampleError(f"{label} must be a JSON object")
    return value, _sha256_bytes(payload)


def evaluation_artifact_path(
    design: FrozenEvaluationDesign,
    name: str,
    *,
    project_root: Path = PROJECT_DIR,
) -> Path:
    artifacts = _mapping(design.document.get("artifacts"), label="artifacts")
    artifact = _mapping(artifacts.get(name), label=f"artifacts.{name}")
    value = artifact.get("path")
    if not isinstance(value, str) or not value:
        raise GoldSampleError(f"artifacts.{name}.path must be non-empty")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise GoldSampleError(f"artifacts.{name}.path escapes project root")
    root = project_root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise GoldSampleError(f"artifacts.{name}.path escapes project root")
    return resolved


def load_prediction_contract_freeze_receipt(
    design: FrozenEvaluationDesign,
) -> tuple[JsonObject, str]:
    """Reverify the create-only active-prompt receipt and every referenced byte hash."""

    path = evaluation_artifact_path(design, "prediction_contract_freeze_receipt_json")
    receipt, receipt_sha256 = _load_json_with_sha256(
        path,
        label="prediction contract freeze receipt",
    )
    freeze = _mapping(
        design.document.get("prediction_contract_freeze"),
        label="prediction_contract_freeze",
    )
    required = freeze.get("required_receipt_fields")
    if not isinstance(required, list) or any(not isinstance(field, str) for field in required):
        raise GoldSampleError("prediction freeze required fields are invalid")
    if set(receipt) != set(required):
        raise GoldSampleError("prediction freeze receipt fields drifted")
    if (
        receipt.get("design_schema_version") != design.document.get("schema_version")
        or receipt.get("design_sha256") != design.sha256
        or not isinstance(receipt.get("contract_schema_version"), str)
        or ACTIVE_CONTRACT_SCHEMA.fullmatch(str(receipt.get("contract_schema_version"))) is None
        or receipt.get("model") != freeze.get("required_model")
        or receipt.get("result_schema_sha256")
        != freeze.get("required_result_schema_sha256")
        or receipt.get("taxonomy_version") != freeze.get("required_taxonomy_version")
    ):
        raise GoldSampleError("prediction freeze receipt contract values drifted")
    if "required_endpoint" in freeze and (
        receipt.get("endpoint") != freeze.get("required_endpoint")
        or receipt.get("explicit_cache_enabled")
        is not freeze.get("required_explicit_cache_enabled")
    ):
        raise GoldSampleError(
            "prediction freeze receipt endpoint/cache values drifted"
        )
    if "required_evidence_span_match_mode" in freeze and (
        receipt.get("evidence_span_match_mode")
        != freeze.get("required_evidence_span_match_mode")
    ):
        raise GoldSampleError(
            "prediction freeze receipt evidence-span match mode drifted"
        )
    for path_field, sha_field in (
        ("contract_path", "contract_sha256"),
        ("prompt_path", "prompt_sha256"),
        ("result_schema_path", "result_schema_sha256"),
    ):
        referenced_path = _project_path(receipt.get(path_field), label=path_field)
        expected_sha256 = receipt.get(sha_field)
        if (
            not isinstance(expected_sha256, str)
            or SHA256_PATTERN.fullmatch(expected_sha256) is None
            or not referenced_path.is_file()
            or referenced_path.is_symlink()
            or _sha256_file(referenced_path) != expected_sha256
        ):
            raise GoldSampleError(f"prediction freeze receipt {path_field} bytes drifted")
    frozen_at = _parse_datetime(receipt.get("frozen_at_utc"), label="receipt.frozen_at_utc")
    if frozen_at is None:
        raise GoldSampleError("prediction freeze receipt has no frozen_at_utc")
    return receipt, receipt_sha256


def prediction_contract_changes_are_prompt_only(
    base_document: Mapping[str, object],
    active_document: Mapping[str, object],
) -> bool:
    """Allow only version metadata and the versioned prompt path/hash to differ."""

    normalized_active: JsonObject = copy.deepcopy(dict(active_document))
    normalized_base: JsonObject = copy.deepcopy(dict(base_document))
    for normalized in (normalized_active, normalized_base):
        for identity_field in ("schema_version", "owner_spec_commit", "pre_registered_at"):
            normalized.pop(identity_field, None)
        normalized_files = _mapping(
            normalized.get("contract_files"),
            label="normalized contract_files",
        )
        normalized_files["prompt"] = {"versioned_prompt_binding": True}
        normalized["contract_files"] = normalized_files
    return normalized_active == normalized_base


def load_active_prediction_contract(
    design: FrozenEvaluationDesign,
) -> tuple[EventExtractContract, JsonObject, str]:
    """Load the receipt-bound active contract without assuming the v1 base hash.

    Prompt development may produce a new versioned contract. The annotation
    contract remains the immutable v1 base, while this loader closes every
    receipt-to-contract-to-prompt/schema binding used for heldout inference.
    """

    design_schema_version = design.document.get("schema_version")
    if design_schema_version == "p4.2a-evaluation-design-v1.7":
        origin = load_evaluation_design(MATERIALIZATION_SUCCESSOR_ORIGIN_DESIGN)
        active_contract, receipt, receipt_sha256 = load_active_prediction_contract(
            origin
        )
        rounds = _mapping(
            design.document.get("materialization_rounds"),
            label="materialization_rounds",
        )
        failed_origin = _mapping(
            rounds.get("failed_origin"),
            label="materialization_rounds.failed_origin",
        )
        successor = _mapping(
            rounds.get("authorized_successor"),
            label="materialization_rounds.authorized_successor",
        )
        frozen_bindings = {
            "failed materialization round": _mapping(
                failed_origin.get("record"),
                label="failed_origin.record",
            ),
            "prediction contract": _mapping(
                successor.get("prediction_contract"),
                label="authorized_successor.prediction_contract",
            ),
            "prompt": _mapping(
                successor.get("prompt"),
                label="authorized_successor.prompt",
            ),
            "freeze receipt": _mapping(
                successor.get("freeze_receipt"),
                label="authorized_successor.freeze_receipt",
            ),
            "model selection outcome": _mapping(
                successor.get("model_selection_outcome"),
                label="authorized_successor.model_selection_outcome",
            ),
        }
        for label, binding in frozen_bindings.items():
            path = _project_path(binding.get("path"), label=f"{label} path")
            expected_sha256 = binding.get("sha256")
            if (
                not isinstance(expected_sha256, str)
                or SHA256_PATTERN.fullmatch(expected_sha256) is None
                or not path.is_file()
                or path.is_symlink()
                or _sha256_file(path) != expected_sha256
            ):
                raise GoldSampleError(f"materialization successor {label} drifted")
        if (
            failed_origin.get("status") != "materialization_failed_no_inference"
            or failed_origin.get("inference_started") is not False
            or failed_origin.get("model_calls") != 0
            or successor.get("round_id") != "heldout-v1.7-r1"
            or successor.get("exactly_one_one_shot") is not True
            or successor.get("predecessor_must_remain_failed") is not True
            or successor.get("model") != active_contract.model
            or _mapping(
                successor.get("prediction_contract"),
                label="authorized_successor.prediction_contract",
            ).get("sha256")
            != active_contract.sha256
            or _mapping(
                successor.get("freeze_receipt"),
                label="authorized_successor.freeze_receipt",
            ).get("sha256")
            != receipt_sha256
            or successor.get("automatic_retries") != 0
            or successor.get("failed_candidate_retries") != 0
        ):
            raise GoldSampleError("materialization successor frozen lineage drifted")
        return active_contract, receipt, receipt_sha256

    receipt, receipt_sha256 = load_prediction_contract_freeze_receipt(design)
    contract_path = _project_path(receipt.get("contract_path"), label="contract_path")
    try:
        raw_contract = contract_path.read_bytes()
        document_value = _strict_yaml_load(
            raw_contract,
            label="active prediction contract",
        )
    except (OSError, GoldSampleError) as exc:
        raise GoldSampleError("active prediction contract is invalid YAML") from exc
    document = _mapping(document_value, label="active prediction contract")
    if (
        _sha256_bytes(raw_contract) != receipt.get("contract_sha256")
        or document.get("schema_version") != receipt.get("contract_schema_version")
        or document.get("production_writes_allowed") is not False
    ):
        raise GoldSampleError("active prediction contract identity drifted")

    contract_files = _mapping(
        document.get("contract_files"),
        label="active prediction contract.contract_files",
    )
    prompt_binding = _mapping(
        contract_files.get("prompt"),
        label="active prediction contract prompt",
    )
    schema_binding = _mapping(
        contract_files.get("schema"),
        label="active prediction contract model schema",
    )
    candidate_selector_contract = (
        design_schema_version
        in {
            "p4.2a-evaluation-design-v1.5",
            "p4.2a-evaluation-design-v1.6",
        }
    )
    result_schema_binding = (
        _mapping(
            contract_files.get("materialized_schema"),
            label="active prediction contract materialized schema",
        )
        if candidate_selector_contract
        else schema_binding
    )
    llm = _mapping(document.get("llm"), label="active prediction contract.llm")
    taxonomy = _mapping(
        document.get("taxonomy"),
        label="active prediction contract.taxonomy",
    )
    base_llm = _mapping(
        design.base_contract.document.get("llm"),
        label="base annotation contract.llm",
    )
    base_input = _mapping(
        design.base_contract.document.get("input"),
        label="base annotation contract.input",
    )
    base_taxonomy = _mapping(
        design.base_contract.document.get("taxonomy"),
        label="base annotation contract.taxonomy",
    )
    input_contract = _mapping(document.get("input"), label="active prediction contract.input")
    common_binding_drifted = (
        llm.get("purpose") != "p4_news_event_extract"
        or llm.get("model") != receipt.get("model")
        or llm.get("enable_thinking") is not False
        or llm.get("max_retries") != 0
        or taxonomy.get("version") != receipt.get("taxonomy_version")
        or prompt_binding.get("path") != receipt.get("prompt_path")
        or prompt_binding.get("sha256") != receipt.get("prompt_sha256")
        or result_schema_binding.get("path") != receipt.get("result_schema_path")
        or result_schema_binding.get("sha256")
        != receipt.get("result_schema_sha256")
        or dict(taxonomy) != dict(base_taxonomy)
    )
    if common_binding_drifted:
        raise GoldSampleError(
            "active prediction contract changed more than the versioned prompt binding"
        )
    registered_contract: EventExtractContract | None = None
    if design_schema_version in {
        "p4.2a-evaluation-design-v1.2",
        "p4.2a-evaluation-design-v1.3",
        "p4.2a-evaluation-design-v1.4",
        "p4.2a-evaluation-design-v1.5",
        "p4.2a-evaluation-design-v1.6",
    }:
        registered_design = load_event_evaluation_design(
            design.path,
            project_root=PROJECT_DIR,
        )
        registered_contract = registered_design.prediction_contract
        if (
            contract_path != registered_contract.path
            or receipt.get("contract_sha256") != registered_contract.sha256
            or document != registered_contract.document
            or receipt.get("endpoint") != registered_contract.endpoint
            or receipt.get("explicit_cache_enabled")
            is not registered_contract.explicit_cache_enabled
            or receipt.get(
                "evidence_span_match_mode",
                EXACT_EVIDENCE_SPAN_MATCH_MODE,
            )
            != registered_contract.evidence_span_match_mode
            or (
                candidate_selector_contract
                and (
                    registered_contract.evidence_candidate_selection is not True
                    or registered_contract.materialized_schema is None
                    or schema_binding.get("sha256")
                    != _mapping(
                        design.document.get("prediction_contract_freeze"),
                        label="prediction_contract_freeze",
                    ).get("required_model_result_schema_sha256")
                )
            )
        ):
            raise GoldSampleError(
                "active prediction contract differs from the registered design"
            )
    elif (
        dict(llm) != dict(base_llm)
        or dict(input_contract) != dict(base_input)
        or not prediction_contract_changes_are_prompt_only(
            design.base_contract.document,
            document,
        )
    ):
        raise GoldSampleError(
            "active prediction contract changed more than the versioned prompt binding"
        )
    prompt_path = _project_path(prompt_binding.get("path"), label="active prompt path")
    schema_path = _project_path(
        result_schema_binding.get("path"),
        label="active materialized schema path",
    )
    try:
        prompt_bytes = prompt_path.read_bytes()
        schema_bytes = schema_path.read_bytes()
        prompt = prompt_bytes.decode("utf-8")
        schema_value: object = json.loads(
            schema_bytes,
            parse_constant=_reject_non_finite_json,
        )
    except (OSError, ValueError) as exc:
        raise GoldSampleError("active prediction prompt/schema is unreadable") from exc
    schema = _mapping(schema_value, label="active prediction schema")
    if (
        not prompt.strip()
        or PROMPT_VERSION_MARKER.search(prompt) is None
        or _sha256_bytes(prompt_bytes) != receipt.get("prompt_sha256")
        or _sha256_bytes(schema_bytes) != receipt.get("result_schema_sha256")
    ):
        raise GoldSampleError("active prediction prompt/schema bytes drifted")
    base_files = _mapping(
        design.base_contract.document.get("contract_files"),
        label="base annotation contract.contract_files",
    )
    base_prompt = _mapping(base_files.get("prompt"), label="base prompt binding")
    prompt_changed = (
        receipt.get("prompt_path") != base_prompt.get("path")
        or receipt.get("prompt_sha256") != base_prompt.get("sha256")
    )
    if prompt_changed and (
        receipt.get("contract_sha256") == design.base_contract.sha256
        or receipt.get("contract_schema_version")
        == design.base_contract.document.get("schema_version")
    ):
        raise GoldSampleError("changed prompt lacks a new versioned prediction contract")
    required_schema_fields = {
        "symbols",
        "event_type",
        "direction",
        "materiality",
        "summary",
        "confidence",
        "evidence_span",
    }
    schema_properties = _mapping(schema.get("properties"), label="active schema.properties")
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or set(schema_properties) != required_schema_fields
        or set(schema.get("required", [])) != required_schema_fields
    ):
        raise GoldSampleError("active prediction result schema is not strict")
    taxonomy_values = taxonomy.get("values")
    event_type_schema = _mapping(
        schema_properties.get("event_type"),
        label="active schema event_type",
    )
    if (
        not isinstance(taxonomy_values, list)
        or any(not isinstance(value, str) for value in taxonomy_values)
        or event_type_schema.get("enum") != taxonomy_values
    ):
        raise GoldSampleError("active prediction taxonomy/schema binding drifted")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise GoldSampleError("active prediction result schema is invalid") from exc

    try:
        (
            purpose,
            contract_model,
            endpoint,
            timeout,
            max_tokens,
            max_retries,
            max_items,
            max_input,
            explicit_cache_enabled,
        ) = validate_event_extract_contract_controls(document)
    except ValueError as exc:
        raise GoldSampleError("active prediction contract controls drifted") from exc
    if (
        contract_model != receipt.get("model")
        or (
            "endpoint" in receipt
            and endpoint != receipt.get("endpoint")
        )
        or (
            "explicit_cache_enabled" in receipt
            and explicit_cache_enabled != receipt.get("explicit_cache_enabled")
        )
    ):
        raise GoldSampleError("active prediction contract budgets drifted")
    if candidate_selector_contract:
        if registered_contract is None:
            raise GoldSampleError(
                "candidate selector contract lacks a registered design binding"
            )
        active_contract = registered_contract
    else:
        active_contract = EventExtractContract(
            path=contract_path,
            sha256=str(receipt["contract_sha256"]),
            document=document,
            prompt=prompt,
            schema=schema,
            model=contract_model,
            endpoint=endpoint,
            purpose=purpose,
            timeout=timeout,
            max_tokens=max_tokens,
            max_retries=max_retries,
            max_items_per_run=max_items,
            max_input_characters=max_input,
            explicit_cache_enabled=explicit_cache_enabled,
            evidence_span_match_mode=str(
                input_contract.get(
                    "evidence_span_match_mode",
                    EXACT_EVIDENCE_SPAN_MATCH_MODE,
                )
            ),
        )
    validate_dev_final_prediction_freeze(
        design,
        active_contract=active_contract,
        receipt=receipt,
    )
    return active_contract, receipt, receipt_sha256


def _ordered_prediction_identity_sha256(records: Sequence[Mapping[str, object]]) -> str:
    digest = hashlib.sha256()
    declared_presence = {
        "declared_input_sha256" in record for record in records
    }
    if len(declared_presence) > 1:
        raise GoldSampleError(
            "prediction identity mixes single- and dual-hash rows"
        )
    dual_hash_identity = declared_presence == {True}
    for record in records:
        news_item_id = record.get("news_item_id")
        input_sha256 = record.get("input_sha256")
        declared_input_sha256 = record.get("declared_input_sha256")
        text_sha256 = record.get("text_sha256")
        if (
            isinstance(news_item_id, bool)
            or not isinstance(news_item_id, int)
            or not isinstance(input_sha256, str)
            or SHA256_PATTERN.fullmatch(input_sha256) is None
            or (
                dual_hash_identity
                and (
                    not isinstance(declared_input_sha256, str)
                    or SHA256_PATTERN.fullmatch(declared_input_sha256) is None
                )
            )
            or not isinstance(text_sha256, str)
            or SHA256_PATTERN.fullmatch(text_sha256) is None
        ):
            raise GoldSampleError("prediction identity tuple is invalid")
        if dual_hash_identity:
            digest.update(
                (
                    f"{news_item_id}\0{input_sha256}\0"
                    f"{declared_input_sha256}\0{text_sha256}\n"
                ).encode("ascii")
            )
        else:
            digest.update(
                f"{news_item_id}\0{input_sha256}\0{text_sha256}\n".encode(
                    "ascii"
                )
            )
    return digest.hexdigest()


def validate_dev_final_prediction_freeze(
    design: FrozenEvaluationDesign,
    *,
    active_contract: EventExtractContract,
    receipt: Mapping[str, object],
    project_root: Path = PROJECT_DIR,
) -> JsonObject:
    """Verify the receipt-bound final active-contract predictions over dev60."""

    root = project_root.resolve()
    predictions_path = evaluation_artifact_path(
        design,
        "dev_final_predictions_jsonl",
        project_root=root,
    )
    manifest_path = evaluation_artifact_path(
        design,
        "dev_final_predictions_manifest_json",
        project_root=root,
    )
    if (
        receipt.get("dev_final_predictions_path")
        != str(predictions_path.relative_to(root))
        or receipt.get("dev_final_predictions_manifest_path")
        != str(manifest_path.relative_to(root))
    ):
        raise GoldSampleError("dev-final prediction receipt paths drifted")
    rows, predictions_sha256 = _load_jsonl_with_sha256(
        predictions_path,
        label="dev-final predictions",
    )
    manifest, manifest_sha256 = _load_json_with_sha256(
        manifest_path,
        label="dev-final prediction manifest",
    )
    dev_path = evaluation_artifact_path(
        design,
        "dev_60_frozen_jsonl",
        project_root=root,
    )
    dev_records, _dev_sha256 = _load_jsonl_with_sha256(dev_path, label="frozen dev60")
    if len(dev_records) != 60 or len(rows) != 60:
        raise GoldSampleError("dev-final prediction freeze must contain exactly 60 rows")
    candidate_inputs: dict[int, JsonObject] = {}
    for record in dev_records:
        news_item_id = record.get("news_item_id")
        if not isinstance(news_item_id, int) or news_item_id in candidate_inputs:
            raise GoldSampleError("frozen dev60 IDs are invalid")
        declared_input_sha256 = _declared_input_sha256(
            record,
            active_contract,
        )
        if record.get("input_sha256") != declared_input_sha256:
            raise GoldSampleError(
                f"dev60 input {news_item_id} differs under active contract"
            )
        bound_record = dict(record)
        if active_contract.evidence_candidate_selection:
            bound_record["declared_input_sha256"] = declared_input_sha256
            bound_record["input_sha256"] = _input_sha256(
                record,
                active_contract,
            )
        candidate_inputs[news_item_id] = bound_record
    ordered_ids = [record.get("news_item_id") for record in rows]
    if ordered_ids != sorted(candidate_inputs):
        raise GoldSampleError("dev-final predictions are not ordered by news_item_id")
    predictions, success_count, failure_count = validate_heldout_candidate_predictions(
        rows,
        candidate_inputs=candidate_inputs,
        active_contract=active_contract,
    )
    ordered_identity = _ordered_prediction_identity_sha256(rows)
    freeze = _mapping(
        design.document.get("prediction_contract_freeze"),
        label="prediction_contract_freeze",
    )
    dev_contract = _mapping(
        freeze.get("dev_final_predictions"),
        label="prediction_contract_freeze.dev_final_predictions",
    )
    required_manifest_fields = dev_contract.get("manifest_required_fields")
    if (
        not isinstance(required_manifest_fields, list)
        or any(not isinstance(field, str) for field in required_manifest_fields)
        or set(manifest) != set(required_manifest_fields)
    ):
        raise GoldSampleError("dev-final prediction manifest fields drifted")
    expected_manifest: dict[str, object] = {
        "design_sha256": design.sha256,
        "contract_sha256": active_contract.sha256,
        "predictions_path": str(predictions_path.relative_to(root)),
        "predictions_sha256": predictions_sha256,
        "row_count": 60,
        "success_count": success_count,
        "failure_count": failure_count,
        "ordered_identity_sha256": ordered_identity,
    }
    for field, expected in expected_manifest.items():
        if manifest.get(field) != expected:
            raise GoldSampleError(f"dev-final prediction manifest {field} drifted")
    completed_at = _parse_datetime(
        manifest.get("completed_at_utc"),
        label="dev-final manifest completed_at_utc",
    )
    frozen_at = _parse_datetime(
        receipt.get("frozen_at_utc"),
        label="prediction freeze receipt frozen_at_utc",
    )
    if completed_at is None or frozen_at is None or completed_at > frozen_at:
        raise GoldSampleError("prediction receipt predates dev-final completion")
    expected_receipt: dict[str, object] = {
        "dev_final_predictions_sha256": predictions_sha256,
        "dev_final_predictions_manifest_sha256": manifest_sha256,
        "dev_final_predictions_row_count": 60,
        "dev_final_predictions_success_count": success_count,
        "dev_final_predictions_failure_count": failure_count,
        "dev_final_predictions_identity_sha256": ordered_identity,
        "dev_final_predictions_contract_sha256": active_contract.sha256,
    }
    for field, expected in expected_receipt.items():
        if receipt.get(field) != expected:
            raise GoldSampleError(f"dev-final prediction receipt {field} drifted")
    failure_ids = [
        news_item_id
        for news_item_id, record in predictions.items()
        if record.get("status") != "ok"
    ]
    return {
        "path": str(predictions_path.relative_to(root)),
        "sha256": predictions_sha256,
        "manifest_path": str(manifest_path.relative_to(root)),
        "manifest_sha256": manifest_sha256,
        "row_count": 60,
        "success_count": success_count,
        "failure_count": failure_count,
        "failure_ids": failure_ids,
        "ordered_identity_sha256": ordered_identity,
        "contract_sha256": active_contract.sha256,
        "rows": rows,
    }


def load_completed_one_shot_state(
    design: FrozenEvaluationDesign,
    *,
    scope: str,
    project_root: Path = PROJECT_DIR,
) -> tuple[JsonObject, str]:
    """Require exactly two ordered events and one successful terminal event."""

    one_shot = _mapping(design.document.get("one_shot"), label="one_shot")
    scope_contract = _mapping(one_shot.get(scope), label=f"one_shot.{scope}")
    artifact_name = scope_contract.get("state_artifact")
    if not isinstance(artifact_name, str):
        raise GoldSampleError(f"one_shot.{scope}.state_artifact is invalid")
    root = project_root.resolve()
    path = evaluation_artifact_path(
        design,
        artifact_name,
        project_root=root,
    )
    events, state_sha256 = _load_jsonl_with_sha256(path, label=f"{scope} one-shot state")
    started_event = scope_contract.get("started_event")
    terminal_events = scope_contract.get("terminal_events")
    if not isinstance(started_event, str) or not isinstance(terminal_events, list):
        raise GoldSampleError(f"one_shot.{scope} event contract is invalid")
    expected_completed = f"{scope}_completed"
    if len(events) != 2 or [event.get("event") for event in events] != [
        started_event,
        expected_completed,
    ]:
        raise GoldSampleError(
            f"{scope} one-shot state must be exactly "
            f"[{started_event}, {expected_completed}]"
        )
    started, terminal = events
    if terminal.get("event") not in terminal_events:
        raise GoldSampleError(f"{scope} completed event is outside terminal contract")
    timestamps: list[datetime] = []
    for event in events:
        if event.get("design_sha256") != design.sha256:
            raise GoldSampleError(f"{scope} one-shot state design hash drifted")
        raw_timestamp = event.get("at_utc")
        if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
            raise GoldSampleError(f"{scope} one-shot state timestamp is missing")
        try:
            timestamp = datetime.fromisoformat(
                raw_timestamp.strip().replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise GoldSampleError(
                f"{scope} one-shot state timestamp is invalid"
            ) from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise GoldSampleError(
                f"{scope} one-shot state timestamp must include a timezone"
            )
        timestamps.append(timestamp.astimezone(UTC))
    if timestamps[1] < timestamps[0]:
        raise GoldSampleError(f"{scope} one-shot terminal timestamp is earlier than started")
    return {
        "started_at_utc": started["at_utc"],
        "terminal_at_utc": terminal["at_utc"],
        "status": "completed",
        "started_event_count": 1,
        "events": events,
        "path": str(path.relative_to(root)),
    }, state_sha256


def _ordered_candidate_identity_sha256(
    records: Sequence[Mapping[str, object]],
) -> str:
    digest = hashlib.sha256()
    prior_news_item_id = 0
    declared_presence = {"declared_input_sha256" in record for record in records}
    if len(declared_presence) > 1:
        raise GoldSampleError("candidate identity mixes single- and dual-hash rows")
    dual_hash_identity = declared_presence == {True}
    for record in records:
        news_item_id = record.get("news_item_id")
        input_sha256 = record.get("input_sha256")
        declared_input_sha256 = record.get("declared_input_sha256")
        text_sha256 = record.get("text_sha256")
        if (
            isinstance(news_item_id, bool)
            or not isinstance(news_item_id, int)
            or news_item_id <= prior_news_item_id
            or not isinstance(input_sha256, str)
            or SHA256_PATTERN.fullmatch(input_sha256) is None
            or not isinstance(text_sha256, str)
            or SHA256_PATTERN.fullmatch(text_sha256) is None
            or (
                dual_hash_identity
                and (
                    not isinstance(declared_input_sha256, str)
                    or SHA256_PATTERN.fullmatch(declared_input_sha256) is None
                    or declared_input_sha256 == input_sha256
                )
            )
        ):
            raise GoldSampleError("candidate identity/order is invalid")
        prior_news_item_id = news_item_id
        identity = (
            f"{news_item_id}\0{input_sha256}\0{declared_input_sha256}"
            f"\0{text_sha256}\n"
            if dual_hash_identity
            else f"{news_item_id}\0{input_sha256}\0{text_sha256}\n"
        )
        digest.update(identity.encode("ascii"))
    return digest.hexdigest()


def validate_inference_completion_bindings(
    inference_state: Mapping[str, object],
    *,
    design: FrozenEvaluationDesign,
    active_contract: EventExtractContract,
    receipt_sha256: str,
    candidate_records: Sequence[Mapping[str, object]],
    candidate_inputs_sha256: str,
    prediction_manifest_sha256: str,
    attempted_count: int,
    success_count: int,
    failure_count: int,
    materialization_binding: Mapping[str, object] | None = None,
) -> None:
    """Independently bind one-shot state to frozen inputs and terminal manifest."""

    raw_events = inference_state.get("events")
    if (
        not isinstance(raw_events, Sequence)
        or isinstance(raw_events, (str, bytes))
        or len(raw_events) != 2
        or any(not isinstance(event, Mapping) for event in raw_events)
    ):
        raise GoldSampleError("inference state events are invalid")
    started = _mapping(raw_events[0], label="inference started state")
    terminal = _mapping(raw_events[1], label="inference completed state")
    candidate_count = len(candidate_records)
    candidate_identity_sha256 = _ordered_candidate_identity_sha256(candidate_records)
    expected_materialization = (
        dict(materialization_binding) if materialization_binding is not None else None
    )
    if (
        started.get("event") != "inference_started"
        or started.get("design_sha256") != design.sha256
        or started.get("contract_sha256") != active_contract.sha256
        or started.get("freeze_receipt_sha256") != receipt_sha256
        or started.get("candidate_inputs_sha256") != candidate_inputs_sha256
        or started.get("candidate_identity_sha256") != candidate_identity_sha256
        or started.get("candidate_count") != candidate_count
        or started.get("materialization") != expected_materialization
    ):
        raise GoldSampleError("inference started receipt/contract/candidate binding drifted")
    if (
        terminal.get("event") != "inference_completed"
        or terminal.get("design_sha256") != design.sha256
        or terminal.get("contract_sha256") != active_contract.sha256
        or terminal.get("candidate_count") != candidate_count
        or terminal.get("attempted_count") != attempted_count
        or terminal.get("success_count") != success_count
        or terminal.get("failure_count") != failure_count
        or terminal.get("prediction_manifest_sha256") != prediction_manifest_sha256
        or terminal.get("materialization") != expected_materialization
        or attempted_count != candidate_count
        or success_count + failure_count != attempted_count
    ):
        raise GoldSampleError("inference terminal manifest/count binding drifted")


def require_heldout_ready(design: FrozenEvaluationDesign, now: datetime) -> None:
    """Enforce the pre-registered end-of-window gate for candidate freezing/selection."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("heldout readiness time must be timezone-aware")
    splits = _mapping(design.document.get("splits"), label="splits")
    heldout = _mapping(splits.get("heldout_40"), label="splits.heldout_40")
    batch = _mapping(heldout.get("candidate_batch"), label="heldout candidate batch")
    ready = _parse_datetime(
        batch.get("selection_ready_after"),
        label="heldout selection_ready_after",
    )
    assert ready is not None
    if now.astimezone(UTC) < ready:
        raise GoldSampleNotReady(
            "heldout candidate artifacts are not ready before "
            f"{ready.astimezone(SHANGHAI).isoformat()}"
        )


def _heldout_candidate_rows(
    connection: sqlite3.Connection,
    design: FrozenEvaluationDesign,
) -> list[NewsRow]:
    splits = _mapping(design.document.get("splits"), label="splits")
    heldout = _mapping(splits.get("heldout_40"), label="splits.heldout_40")
    batch = _mapping(heldout.get("candidate_batch"), label="heldout candidate batch")
    cutoff = int(batch["min_news_item_id_exclusive"])
    start = _parse_datetime(
        batch.get("window_start_inclusive"),
        label="heldout window_start_inclusive",
    )
    end = _parse_datetime(
        batch.get("window_end_exclusive"),
        label="heldout window_end_exclusive",
    )
    assert start is not None and end is not None
    sources = batch.get("sources")
    if not isinstance(sources, list) or any(not isinstance(item, str) for item in sources):
        raise GoldSampleError("heldout candidate sources are invalid")
    rows = _load_news_rows(connection, cutoff=cutoff, after_cutoff=True)
    selected = [
        row
        for row in rows
        if row.source in sources and start <= row.available_time < end
    ]
    if not selected:
        raise GoldSampleError("heldout candidate batch is empty")
    return selected


def _candidate_input_from_row(
    row: NewsRow,
    *,
    base_contract: FrozenContract,
    active_contract: EventExtractContract,
    design: FrozenEvaluationDesign,
    pdf_fetcher: PdfFetcher,
    pdf_text_extractor: PdfTextExtractor,
) -> JsonObject:
    require_body = row.source == "cninfo"
    selected = SelectedNews(
        row=row,
        sample_group="heldout_candidate",
        trading_date=row.available_time.astimezone(SHANGHAI).date(),
        stratum=Stratum(
            source=row.source,
            symbol_state=row.symbol_state,
            count=1,
            require_announcement_body=require_body,
        ),
        rank_sha256=_sha256_bytes(
            f"heldout-candidate-input-v1.1\0{row.news_item_id}".encode()
        ),
    )
    original_text, body_evidence = _original_text_and_body_evidence(
        selected,
        base_contract,
        pdf_fetcher=pdf_fetcher,
        pdf_text_extractor=pdf_text_extractor,
    )
    record: JsonObject = {
        "schema_version": "p4.2a-heldout-candidate-input-v1.1",
        "design_sha256": design.sha256,
        "contract_sha256": active_contract.sha256,
        "model": active_contract.model,
        "news_item_id": row.news_item_id,
        "source": row.source,
        "url": row.url,
        "title": row.title,
        "ingested_symbol": row.ingested_symbol,
        "published_at": _iso_utc(row.published_at),
        "available_time": _iso_utc(row.available_time),
        "original_text": original_text,
        "body_state": _body_state(row, require_body),
        "content_hash": row.content_hash,
        "text_sha256": _sha256_bytes(original_text.encode("utf-8")),
        "body_evidence": body_evidence,
    }
    record, active_input_sha256 = _fit_candidate_record_to_input_budget(
        record,
        active_contract,
    )
    record["input_sha256"] = active_input_sha256
    if active_contract.evidence_candidate_selection:
        declared_input_sha256 = _declared_input_sha256(record, active_contract)
        if declared_input_sha256 == record["input_sha256"]:
            raise GoldSampleError("candidate selector input identities must be distinct")
        record["declared_input_sha256"] = declared_input_sha256
    return record


def _fit_candidate_record_to_input_budget(
    record: JsonObject,
    active_contract: EventExtractContract,
) -> tuple[JsonObject, str]:
    """Shorten only an over-budget body prefix while preserving frozen semantics.

    The announcement contract already defines both a maximum body prefix and a
    stricter serialized model-input ceiling. Candidate metadata adds bounded
    overhead, so a small number of otherwise eligible long PDFs need a shorter
    prefix to satisfy the latter. This is not an ineligibility reason and never
    changes the selector algorithm, fields, or input-character budget.
    """

    try:
        return record, _input_sha256(record, active_contract)
    except EventExtractValidationError as exc:
        if (
            not active_contract.evidence_candidate_selection
            or exc.field != "result"
            or exc.constraint != "serialized_input_character_budget"
            or record.get("source") != "cninfo"
        ):
            raise

    original_text = _record_string(record, "original_text")
    body_evidence = _mapping(
        record.get("body_evidence"),
        label="candidate budget body_evidence",
    )
    if body_evidence.get("required") is not True:
        raise GoldSampleError("over-budget candidate lacks required body evidence")
    full_count = body_evidence.get("full_text_character_count")
    if isinstance(full_count, bool) or not isinstance(full_count, int):
        raise GoldSampleError("over-budget candidate full-text count is invalid")

    candidate_text = original_text
    while candidate_text:
        candidate_text = candidate_text[:-1].rstrip()
        if not candidate_text:
            break
        candidate = copy.deepcopy(record)
        candidate["original_text"] = candidate_text
        candidate["text_sha256"] = _sha256_bytes(candidate_text.encode("utf-8"))
        candidate_evidence = _mapping(
            candidate.get("body_evidence"),
            label="candidate budget body_evidence",
        )
        candidate_evidence["annotation_text_character_count"] = len(candidate_text)
        candidate_evidence["body_characters_in_original_text"] = len(candidate_text)
        candidate_evidence["text_truncated"] = full_count > len(candidate_text)
        candidate["body_evidence"] = candidate_evidence
        try:
            digest = _input_sha256(candidate, active_contract)
        except EventExtractValidationError as exc:
            if (
                exc.field == "result"
                and exc.constraint == "serialized_input_character_budget"
            ):
                continue
            raise
        validate_body_evidence(candidate, label="budget-fitted candidate input")
        return candidate, digest
    raise GoldSampleError("eligible candidate cannot fit the serialized input budget")


def validate_heldout_candidate_inputs(
    records: Sequence[JsonObject],
    *,
    rows: Sequence[NewsRow],
    design: FrozenEvaluationDesign,
    active_contract: EventExtractContract,
) -> dict[int, JsonObject]:
    """Bind every candidate input to DB identity and its once-fetched frozen body."""

    rows_by_id = {row.news_item_id: row for row in rows}
    required = {
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
    }
    if active_contract.evidence_candidate_selection:
        required.add("declared_input_sha256")
    validated: dict[int, JsonObject] = {}
    for record in records:
        missing = required - set(record)
        if missing:
            raise GoldSampleError(f"heldout candidate input fields missing: {sorted(missing)}")
        news_item_id = record.get("news_item_id")
        if (
            isinstance(news_item_id, bool)
            or not isinstance(news_item_id, int)
            or news_item_id <= 0
        ):
            raise GoldSampleError("heldout candidate input has invalid news_item_id")
        if news_item_id in validated:
            raise GoldSampleError(f"heldout candidate inputs duplicate news item {news_item_id}")
        row = rows_by_id.get(news_item_id)
        if row is None:
            raise GoldSampleError(f"heldout candidate input {news_item_id} is outside frozen batch")
        expected_identity: dict[str, object] = {
            "source": row.source,
            "url": row.url,
            "title": row.title,
            "ingested_symbol": row.ingested_symbol,
            "published_at": _iso_utc(row.published_at),
            "available_time": _iso_utc(row.available_time),
        }
        for field, expected in expected_identity.items():
            if record.get(field) != expected:
                raise GoldSampleError(
                    f"heldout candidate input {news_item_id} changed DB field {field}"
                )
        if "content_hash" in record and record.get("content_hash") != row.content_hash:
            raise GoldSampleError(
                f"heldout candidate input {news_item_id} content_hash drifted"
            )
        original_text = record.get("original_text")
        if not isinstance(original_text, str) or not original_text:
            raise GoldSampleError(f"heldout candidate input {news_item_id} has no text")
        if record.get("text_sha256") != _sha256_bytes(original_text.encode("utf-8")):
            raise GoldSampleError(
                f"heldout candidate input {news_item_id} text SHA-256 drifted"
            )
        expected_active_input_sha256 = _input_sha256(record, active_contract)
        expected_declared_input_sha256 = _declared_input_sha256(
            record,
            active_contract,
        )
        if (
            record.get("contract_sha256") not in {None, active_contract.sha256}
            or record.get("model") not in {None, active_contract.model}
            or record.get("design_sha256") not in {None, design.sha256}
            or record.get("input_sha256") != expected_active_input_sha256
            or (
                active_contract.evidence_candidate_selection
                and (
                    record.get("declared_input_sha256")
                    != expected_declared_input_sha256
                    or expected_declared_input_sha256
                    == expected_active_input_sha256
                )
            )
        ):
            raise GoldSampleError(
                f"heldout candidate input {news_item_id} active contract binding drifted"
            )
        evidence = _mapping(
            record.get("body_evidence"),
            label=f"heldout candidate {news_item_id} body_evidence",
        )
        validate_body_evidence(
            record,
            label=f"heldout candidate input {news_item_id}",
        )
        if row.source == "cninfo" and (
            record.get("body_state") != "announcement_body"
            or evidence.get("required") is not True
            or evidence.get("source") != "cninfo_pdf"
            or evidence.get("url") != row.url
            or evidence.get("pdf_persisted") is not False
            or not isinstance(evidence.get("pdf_sha256"), str)
            or SHA256_PATTERN.fullmatch(str(evidence.get("pdf_sha256"))) is None
            or evidence.get("full_text_sha256") is None
        ):
            raise GoldSampleError(
                f"CNInfo heldout input {news_item_id} lacks frozen announcement body"
            )
        validated[news_item_id] = record
    if set(validated) != set(rows_by_id):
        missing_ids = sorted(set(rows_by_id) - set(validated))
        extra_ids = sorted(set(validated) - set(rows_by_id))
        raise GoldSampleError(
            "heldout candidate input coverage differs from DB batch: "
            f"missing={missing_ids[:5]}, extra={extra_ids[:5]}"
        )
    return validated


def materialize_heldout_candidate_inputs(
    rows: Sequence[NewsRow],
    design: FrozenEvaluationDesign,
    active_contract: EventExtractContract,
    *,
    pdf_fetcher: PdfFetcher = download_cninfo_pdf,
    pdf_text_extractor: PdfTextExtractor = extract_cninfo_pdf_text,
) -> HeldoutCandidateMaterialization:
    """Pure candidate materializer shared by the one-shot runner and tests."""

    eligibility_enabled = _candidate_eligibility_enabled(design)
    all_candidates: list[JsonObject] = []
    eligible_rows: list[NewsRow] = []
    eligible_records: list[JsonObject] = []
    ineligible_candidates: list[JsonObject] = []
    reason_counts: dict[str, int] = {}
    for row in rows:
        all_candidates.append(
            {
                "news_item_id": row.news_item_id,
                "source": row.source,
                "url": row.url,
                "content_hash": row.content_hash,
            }
        )
        try:
            record = _candidate_input_from_row(
                row,
                base_contract=design.base_contract,
                active_contract=active_contract,
                design=design,
                pdf_fetcher=pdf_fetcher,
                pdf_text_extractor=pdf_text_extractor,
            )
        except CandidateDocumentIneligible as exc:
            if not eligibility_enabled:
                raise GoldSampleError(
                    "pdftotext output is shorter than the minimum extracted-character gate"
                    if exc.reason == "pdf_text_below_min_char_gate"
                    else "CNInfo PDF exceeds the 8 MiB bound"
                ) from exc
            ineligible_candidates.append(
                {
                    "news_item_id": row.news_item_id,
                    "url": row.url,
                    "reason": exc.reason,
                    "measured_value": exc.measured_value,
                    "gate_value": exc.gate_value,
                    "pdf_sha256": exc.pdf_sha256,
                }
            )
            reason_counts[exc.reason] = reason_counts.get(exc.reason, 0) + 1
            continue
        eligible_rows.append(row)
        eligible_records.append(record)
    validate_heldout_candidate_inputs(
        eligible_records,
        rows=eligible_rows,
        design=design,
        active_contract=active_contract,
    )
    return HeldoutCandidateMaterialization(
        all_candidates=tuple(all_candidates),
        eligible_records=tuple(eligible_records),
        ineligible_candidates=tuple(ineligible_candidates),
        reason_counts=dict(sorted(reason_counts.items())),
    )


def _manifest_mapping(value: object, *, label: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise GoldSampleError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _manifest_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    *,
    label: str,
) -> None:
    observed = set(value)
    if observed != expected:
        raise GoldSampleError(
            f"{label} fields drifted: missing={sorted(expected - observed)}, "
            f"extra={sorted(observed - expected)}"
        )


def _manifest_project_relative(path: Path, *, project_root: Path, label: str) -> str:
    root = project_root.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise GoldSampleError(f"{label} escapes the project root")
    return resolved.relative_to(root).as_posix()


def _full_candidate_manifest_identity(candidate: Mapping[str, object]) -> JsonObject:
    item = _manifest_mapping(candidate, label="materialization all candidate")
    _manifest_exact_keys(
        item,
        {"news_item_id", "source", "url", "content_hash"},
        label="materialization all candidate",
    )
    news_item_id = item.get("news_item_id")
    if (
        isinstance(news_item_id, bool)
        or not isinstance(news_item_id, int)
        or news_item_id <= 0
        or not isinstance(item.get("source"), str)
        or not item["source"]
        or not isinstance(item.get("url"), str)
        or not item["url"]
        or not isinstance(item.get("content_hash"), str)
        or SHA256_PATTERN.fullmatch(str(item["content_hash"])) is None
    ):
        raise GoldSampleError("materialization all candidate identity is invalid")
    return item


def _eligible_candidate_manifest_identity(record: Mapping[str, object]) -> JsonObject:
    news_item_id = record.get("news_item_id")
    source = record.get("source")
    url = record.get("url")
    content_hash = record.get("content_hash")
    input_sha256 = record.get("input_sha256")
    declared_input_sha256 = record.get("declared_input_sha256")
    text_sha256 = record.get("text_sha256")
    body_evidence = _manifest_mapping(
        record.get("body_evidence"),
        label="eligible candidate body_evidence",
    )
    pdf_sha256 = body_evidence.get("pdf_sha256")
    if (
        isinstance(news_item_id, bool)
        or not isinstance(news_item_id, int)
        or news_item_id <= 0
        or not isinstance(source, str)
        or not source
        or not isinstance(url, str)
        or not url
        or not isinstance(content_hash, str)
        or SHA256_PATTERN.fullmatch(content_hash) is None
        or not isinstance(input_sha256, str)
        or SHA256_PATTERN.fullmatch(input_sha256) is None
        or (
            declared_input_sha256 is not None
            and (
                not isinstance(declared_input_sha256, str)
                or SHA256_PATTERN.fullmatch(declared_input_sha256) is None
            )
        )
        or not isinstance(text_sha256, str)
        or SHA256_PATTERN.fullmatch(text_sha256) is None
        or (
            pdf_sha256 is not None
            and (
                not isinstance(pdf_sha256, str)
                or SHA256_PATTERN.fullmatch(pdf_sha256) is None
            )
        )
    ):
        raise GoldSampleError("eligible materialization identity is invalid")
    return {
        "news_item_id": news_item_id,
        "source": source,
        "url": url,
        "content_hash": content_hash,
        "input_sha256": input_sha256,
        "declared_input_sha256": declared_input_sha256,
        "text_sha256": text_sha256,
        "pdf_sha256": pdf_sha256,
    }


def _ineligible_candidate_manifest_identity(
    candidate: Mapping[str, object],
    *,
    policy: AnnouncementBodyPolicy,
) -> JsonObject:
    item = _manifest_mapping(candidate, label="ineligible candidate")
    _manifest_exact_keys(
        item,
        {
            "news_item_id",
            "url",
            "reason",
            "measured_value",
            "gate_value",
            "pdf_sha256",
        },
        label="ineligible candidate",
    )
    news_item_id = item.get("news_item_id")
    url = item.get("url")
    reason = item.get("reason")
    measured_value = item.get("measured_value")
    gate_value = item.get("gate_value")
    pdf_sha256 = item.get("pdf_sha256")
    if (
        isinstance(news_item_id, bool)
        or not isinstance(news_item_id, int)
        or news_item_id <= 0
        or not isinstance(url, str)
        or not url
        or reason not in MATERIALIZATION_INELIGIBLE_REASONS
        or isinstance(measured_value, bool)
        or not isinstance(measured_value, int)
        or measured_value < 0
        or isinstance(gate_value, bool)
        or not isinstance(gate_value, int)
        or gate_value <= 0
        or (
            pdf_sha256 is not None
            and (
                not isinstance(pdf_sha256, str)
                or SHA256_PATTERN.fullmatch(pdf_sha256) is None
            )
        )
    ):
        raise GoldSampleError("ineligible materialization record is invalid")
    if reason == "pdf_text_below_min_char_gate":
        if (
            gate_value != policy.minimum_extracted_characters
            or measured_value >= gate_value
            or pdf_sha256 is None
        ):
            raise GoldSampleError("short-PDF ineligibility evidence is invalid")
    elif gate_value != policy.max_pdf_bytes or measured_value <= gate_value:
        raise GoldSampleError("oversized-PDF ineligibility evidence is invalid")
    return item


def _materialization_manifest_lineage(
    *,
    design: FrozenEvaluationDesign,
    active_contract: EventExtractContract,
    freeze_receipt_sha256: str,
    project_root: Path,
) -> JsonObject:
    if SHA256_PATTERN.fullmatch(freeze_receipt_sha256) is None:
        raise GoldSampleError("materialization freeze receipt SHA-256 is invalid")
    receipt_path = evaluation_artifact_path(
        design,
        "prediction_contract_freeze_receipt_json",
        project_root=project_root,
    )
    return {
        "evaluation_design": {
            "path": _manifest_project_relative(
                design.path,
                project_root=project_root,
                label="evaluation design",
            ),
            "schema_version": design.document.get("schema_version"),
            "sha256": design.sha256,
        },
        "prediction_contract": {
            "path": _manifest_project_relative(
                active_contract.path,
                project_root=project_root,
                label="prediction contract",
            ),
            "schema_version": active_contract.document.get("schema_version"),
            "sha256": active_contract.sha256,
            "model": active_contract.model,
        },
        "freeze_receipt": {
            "path": _manifest_project_relative(
                receipt_path,
                project_root=project_root,
                label="prediction freeze receipt",
            ),
            "sha256": freeze_receipt_sha256,
        },
    }


def _validate_materialization_manifest_document(
    manifest: Mapping[str, object],
    *,
    expected_all_candidates: Sequence[Mapping[str, object]],
    eligible_records: Sequence[Mapping[str, object]],
    design: FrozenEvaluationDesign,
    active_contract: EventExtractContract,
    freeze_receipt_sha256: str,
    project_root: Path,
) -> JsonObject:
    document = _manifest_mapping(manifest, label="materialization manifest")
    _manifest_exact_keys(
        document,
        {
            "schema_version",
            "lineage",
            "artifacts",
            "partition_contract",
            "counts",
            "layers",
        },
        label="materialization manifest",
    )
    if document.get("schema_version") != MATERIALIZATION_MANIFEST_SCHEMA_VERSION:
        raise GoldSampleError("materialization manifest schema version drifted")

    expected_lineage = _materialization_manifest_lineage(
        design=design,
        active_contract=active_contract,
        freeze_receipt_sha256=freeze_receipt_sha256,
        project_root=project_root,
    )
    if document.get("lineage") != expected_lineage:
        raise GoldSampleError("materialization manifest lineage drifted")

    inputs_path = evaluation_artifact_path(
        design,
        "heldout_candidate_inputs_jsonl",
        project_root=project_root,
    )
    manifest_path = evaluation_artifact_path(
        design,
        "heldout_candidate_materialization_manifest_json",
        project_root=project_root,
    )
    eligible_payload_sha256 = _sha256_bytes(
        _json_line_bytes([dict(record) for record in eligible_records])
    )
    expected_artifacts = {
        "eligible_inputs_jsonl": {
            "path": _manifest_project_relative(
                inputs_path,
                project_root=project_root,
                label="eligible inputs",
            ),
            "sha256": eligible_payload_sha256,
            "format": "canonical-jsonl-utf8-lf",
            "create_only": True,
        },
        "materialization_manifest_json": {
            "path": _manifest_project_relative(
                manifest_path,
                project_root=project_root,
                label="materialization manifest",
            ),
            "create_only": True,
        },
    }
    if document.get("artifacts") != expected_artifacts:
        raise GoldSampleError("materialization artifact path/SHA binding drifted")
    if document.get("partition_contract") != {
        "identity_key": "news_item_id",
        "relation": "all=eligible_disjoint_union_ineligible",
        "order": "frozen_database_id_ascending",
    }:
        raise GoldSampleError("materialization partition contract drifted")

    expected_all = [
        _full_candidate_manifest_identity(candidate)
        for candidate in expected_all_candidates
    ]
    expected_eligible = [
        _eligible_candidate_manifest_identity(record) for record in eligible_records
    ]
    if active_contract.evidence_candidate_selection and any(
        candidate["declared_input_sha256"] is None
        for candidate in expected_eligible
    ):
        raise GoldSampleError(
            "eligible materialization lacks candidate-selector declared input identity"
        )
    all_ids = [int(candidate["news_item_id"]) for candidate in expected_all]
    eligible_ids = [int(candidate["news_item_id"]) for candidate in expected_eligible]
    if all_ids != sorted(all_ids) or len(all_ids) != len(set(all_ids)):
        raise GoldSampleError("materialization full candidate order/identity is invalid")
    if len(eligible_ids) != len(set(eligible_ids)):
        raise GoldSampleError("materialization eligible candidate identity is duplicated")
    all_by_id = {int(candidate["news_item_id"]): candidate for candidate in expected_all}
    for eligible in expected_eligible:
        news_item_id = int(eligible["news_item_id"])
        full = all_by_id.get(news_item_id)
        if full is None or any(
            eligible[field] != full[field]
            for field in ("source", "url", "content_hash")
        ):
            raise GoldSampleError("eligible layer is not an identity-preserving subset")

    layers = _manifest_mapping(document.get("layers"), label="materialization layers")
    _manifest_exact_keys(
        layers,
        {"all_candidates", "eligible_candidates", "ineligible_candidates"},
        label="materialization layers",
    )
    if layers.get("all_candidates") != expected_all:
        raise GoldSampleError("materialization full layer drifted")
    if layers.get("eligible_candidates") != expected_eligible:
        raise GoldSampleError("materialization eligible layer drifted")
    raw_ineligible = layers.get("ineligible_candidates")
    if not isinstance(raw_ineligible, list):
        raise GoldSampleError("materialization ineligible layer must be a list")
    policy = announcement_body_policy(design.base_contract)
    ineligible = [
        _ineligible_candidate_manifest_identity(candidate, policy=policy)
        for candidate in raw_ineligible
    ]
    ineligible_ids = [int(candidate["news_item_id"]) for candidate in ineligible]
    if len(ineligible_ids) != len(set(ineligible_ids)):
        raise GoldSampleError("materialization ineligible identity is duplicated")
    if set(eligible_ids) & set(ineligible_ids):
        raise GoldSampleError("materialization eligible/ineligible layers overlap")
    if set(all_ids) != set(eligible_ids) | set(ineligible_ids):
        raise GoldSampleError("materialization layers do not close over the full pool")
    if eligible_ids != [item for item in all_ids if item in set(eligible_ids)]:
        raise GoldSampleError("materialization eligible order drifted")
    if ineligible_ids != [item for item in all_ids if item in set(ineligible_ids)]:
        raise GoldSampleError("materialization ineligible order drifted")
    for candidate in ineligible:
        full = all_by_id.get(int(candidate["news_item_id"]))
        if full is None or candidate["url"] != full["url"]:
            raise GoldSampleError("ineligible layer is not bound to the full pool")

    reason_counts = {reason: 0 for reason in MATERIALIZATION_INELIGIBLE_REASONS}
    for candidate in ineligible:
        reason_counts[str(candidate["reason"])] += 1
    expected_counts = {
        "all_candidates": len(expected_all),
        "eligible_candidates": len(expected_eligible),
        "ineligible_candidates": len(ineligible),
        "ineligible_by_reason": reason_counts,
    }
    if document.get("counts") != expected_counts:
        raise GoldSampleError("materialization counts/reasons drifted")
    return document


def heldout_materialization_manifest_payload(
    materialization: HeldoutCandidateMaterialization,
    *,
    design: FrozenEvaluationDesign,
    active_contract: EventExtractContract,
    freeze_receipt_sha256: str,
    project_root: Path = PROJECT_DIR,
) -> tuple[JsonObject, bytes]:
    """Build deterministic create-only manifest bytes for one materialization."""

    all_candidates = [dict(candidate) for candidate in materialization.all_candidates]
    eligible_records = [dict(record) for record in materialization.eligible_records]
    ineligible_candidates = [
        dict(candidate) for candidate in materialization.ineligible_candidates
    ]
    observed_reason_counts: dict[str, int] = {}
    for candidate in ineligible_candidates:
        reason = candidate.get("reason")
        if isinstance(reason, str):
            observed_reason_counts[reason] = observed_reason_counts.get(reason, 0) + 1
    if materialization.reason_counts != dict(sorted(observed_reason_counts.items())):
        raise GoldSampleError("materialization result reason counts drifted")
    expected_lineage = _materialization_manifest_lineage(
        design=design,
        active_contract=active_contract,
        freeze_receipt_sha256=freeze_receipt_sha256,
        project_root=project_root,
    )
    inputs_path = evaluation_artifact_path(
        design,
        "heldout_candidate_inputs_jsonl",
        project_root=project_root,
    )
    manifest_path = evaluation_artifact_path(
        design,
        "heldout_candidate_materialization_manifest_json",
        project_root=project_root,
    )
    inputs_sha256 = _sha256_bytes(_json_line_bytes(eligible_records))
    reason_counts = {reason: 0 for reason in MATERIALIZATION_INELIGIBLE_REASONS}
    reason_counts.update(observed_reason_counts)
    document: JsonObject = {
        "schema_version": MATERIALIZATION_MANIFEST_SCHEMA_VERSION,
        "lineage": expected_lineage,
        "artifacts": {
            "eligible_inputs_jsonl": {
                "path": _manifest_project_relative(
                    inputs_path,
                    project_root=project_root,
                    label="eligible inputs",
                ),
                "sha256": inputs_sha256,
                "format": "canonical-jsonl-utf8-lf",
                "create_only": True,
            },
            "materialization_manifest_json": {
                "path": _manifest_project_relative(
                    manifest_path,
                    project_root=project_root,
                    label="materialization manifest",
                ),
                "create_only": True,
            },
        },
        "partition_contract": {
            "identity_key": "news_item_id",
            "relation": "all=eligible_disjoint_union_ineligible",
            "order": "frozen_database_id_ascending",
        },
        "counts": {
            "all_candidates": len(all_candidates),
            "eligible_candidates": len(eligible_records),
            "ineligible_candidates": len(ineligible_candidates),
            "ineligible_by_reason": reason_counts,
        },
        "layers": {
            "all_candidates": all_candidates,
            "eligible_candidates": [
                _eligible_candidate_manifest_identity(record)
                for record in eligible_records
            ],
            "ineligible_candidates": ineligible_candidates,
        },
    }
    validated = _validate_materialization_manifest_document(
        document,
        expected_all_candidates=all_candidates,
        eligible_records=eligible_records,
        design=design,
        active_contract=active_contract,
        freeze_receipt_sha256=freeze_receipt_sha256,
        project_root=project_root,
    )
    payload = (
        json.dumps(
            validated,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    return validated, payload


def validate_heldout_materialization_manifest(
    manifest: Mapping[str, object],
    *,
    rows: Sequence[NewsRow],
    eligible_records: Sequence[Mapping[str, object]],
    design: FrozenEvaluationDesign,
    active_contract: EventExtractContract,
    freeze_receipt_sha256: str,
    project_root: Path = PROJECT_DIR,
) -> JsonObject:
    """Fail closed unless the immutable manifest proves an ordered partition."""

    expected_all = [
        {
            "news_item_id": row.news_item_id,
            "source": row.source,
            "url": row.url,
            "content_hash": row.content_hash,
        }
        for row in rows
    ]
    return _validate_materialization_manifest_document(
        manifest,
        expected_all_candidates=expected_all,
        eligible_records=eligible_records,
        design=design,
        active_contract=active_contract,
        freeze_receipt_sha256=freeze_receipt_sha256,
        project_root=project_root,
    )


def heldout_materialization_binding(
    manifest: Mapping[str, object],
    *,
    manifest_path: Path,
    manifest_sha256: str,
    project_root: Path = PROJECT_DIR,
) -> JsonObject:
    """Return the canonical evidence binding shared by every downstream artifact."""

    if SHA256_PATTERN.fullmatch(manifest_sha256) is None:
        raise GoldSampleError("materialization manifest SHA-256 is invalid")
    root = project_root.resolve()
    resolved_path = manifest_path.resolve()
    if resolved_path.is_symlink() or not resolved_path.is_file():
        raise GoldSampleError("materialization manifest file is missing or unsafe")
    if not resolved_path.is_relative_to(root):
        raise GoldSampleError("materialization manifest path escapes project root")
    if _sha256_file(resolved_path) != manifest_sha256:
        raise GoldSampleError("materialization manifest bytes drifted")
    counts = _manifest_mapping(
        manifest.get("counts"), label="materialization manifest counts"
    )
    reason_counts = _manifest_mapping(
        counts.get("ineligible_by_reason"),
        label="materialization ineligible reason counts",
    )
    expected_count_fields = {
        "all_candidates",
        "eligible_candidates",
        "ineligible_candidates",
        "ineligible_by_reason",
    }
    _manifest_exact_keys(
        counts,
        expected_count_fields,
        label="materialization manifest counts",
    )
    for field in (
        "all_candidates",
        "eligible_candidates",
        "ineligible_candidates",
    ):
        value = counts.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GoldSampleError(f"materialization manifest {field} is invalid")
    return {
        "manifest_path": resolved_path.relative_to(root).as_posix(),
        "manifest_sha256": manifest_sha256,
        "raw_candidate_count": counts["all_candidates"],
        "eligible_candidate_count": counts["eligible_candidates"],
        "ineligible_candidate_count": counts["ineligible_candidates"],
        "ineligible_by_reason": dict(reason_counts),
    }


def heldout_eligible_rows_from_materialization(
    rows: Sequence[NewsRow],
    manifest: Mapping[str, object],
) -> tuple[NewsRow, ...]:
    """Derive the only DB subset eligible for inference or owner selection."""

    layers = _manifest_mapping(manifest.get("layers"), label="materialization layers")
    raw_eligible = layers.get("eligible_candidates")
    raw_ineligible = layers.get("ineligible_candidates")
    if not isinstance(raw_eligible, list) or not isinstance(raw_ineligible, list):
        raise GoldSampleError("materialization eligible/ineligible layers are invalid")
    eligible_ids = [
        int(
            _manifest_mapping(item, label="materialization eligible candidate")[
                "news_item_id"
            ]
        )
        for item in raw_eligible
    ]
    ineligible_ids = {
        int(
            _manifest_mapping(item, label="materialization ineligible candidate")[
                "news_item_id"
            ]
        )
        for item in raw_ineligible
    }
    rows_by_id = {row.news_item_id: row for row in rows}
    if set(eligible_ids) & ineligible_ids:
        raise GoldSampleError("materialization eligible/ineligible IDs overlap")
    try:
        eligible_rows = tuple(rows_by_id[news_item_id] for news_item_id in eligible_ids)
    except KeyError as exc:
        raise GoldSampleError("materialization eligible ID is absent from DB pool") from exc
    if [row.news_item_id for row in eligible_rows] != eligible_ids:
        raise GoldSampleError("materialization eligible DB order drifted")
    return eligible_rows


def build_heldout_candidate_inputs(
    design_path: Path = DEFAULT_EVALUATION_DESIGN,
    database_path: Path | None = None,
    *,
    now: datetime | None = None,
    pdf_fetcher: PdfFetcher = download_cninfo_pdf,
    pdf_text_extractor: PdfTextExtractor = extract_cninfo_pdf_text,
) -> JsonObject:
    """Freeze the complete heldout batch once; this is the only CNInfo body fetch."""

    design = load_evaluation_design(design_path)
    current_time = now or datetime.now(UTC)
    require_heldout_ready(design, current_time)
    active_contract, receipt, receipt_sha256 = load_active_prediction_contract(design)
    materialization_enabled = (
        design.document.get("schema_version") == "p4.2a-evaluation-design-v1.7"
    )
    output = evaluation_artifact_path(design, "heldout_candidate_inputs_jsonl")
    artifact_root = _project_path(design.document["artifact_root"], label="artifact_root")
    output = (
        _artifact_path(output, artifact_root)
        if materialization_enabled
        else _new_artifact_path(output, artifact_root)
    )
    database = _database_path(design.base_contract, database_path)
    with open_read_only_database(database) as connection:
        rows = _heldout_candidate_rows(connection, design)
        materialization = materialize_heldout_candidate_inputs(
            rows,
            design,
            active_contract,
            pdf_fetcher=pdf_fetcher,
            pdf_text_extractor=pdf_text_extractor,
        )
    records = list(materialization.eligible_records)
    payload = _json_line_bytes(records)
    materialization_evidence: JsonObject = {}
    if materialization_enabled:
        materialization_manifest_path = _artifact_path(
            evaluation_artifact_path(
                design,
                "heldout_candidate_materialization_manifest_json",
            ),
            artifact_root,
        )
        manifest, manifest_payload = heldout_materialization_manifest_payload(
            materialization,
            design=design,
            active_contract=active_contract,
            freeze_receipt_sha256=receipt_sha256,
        )
        hashes = _write_create_only_bundle(
            {
                output: payload,
                materialization_manifest_path: manifest_payload,
            }
        )
        materialization_evidence = {
            "materialization_manifest": str(
                materialization_manifest_path.relative_to(PROJECT_DIR)
            ),
            "materialization_manifest_sha256": hashes[
                materialization_manifest_path
            ],
            "materialization_manifest_schema_version": manifest["schema_version"],
        }
        output_sha256 = hashes[output]
    else:
        _write_new_bytes(output, payload)
        output_sha256 = _sha256_bytes(payload)
    return {
        "mode": "heldout-inputs",
        "full_candidate_count": len(materialization.all_candidates),
        "row_count": len(records),
        "ineligible_count": len(materialization.ineligible_candidates),
        "ineligible_reason_counts": materialization.reason_counts,
        "cninfo_body_count": sum(record["source"] == "cninfo" for record in records),
        "output": str(output.relative_to(PROJECT_DIR)),
        "sha256": output_sha256,
        **materialization_evidence,
        "design_sha256": design.sha256,
        "prediction_contract_sha256": active_contract.sha256,
        "freeze_receipt_sha256": receipt_sha256,
        "freeze_receipt_frozen_at_utc": receipt["frozen_at_utc"],
        "database_open_mode": "mode=ro + PRAGMA query_only=ON",
    }


def validate_heldout_candidate_predictions(
    records: Sequence[JsonObject],
    *,
    candidate_inputs: Mapping[int, JsonObject],
    active_contract: EventExtractContract,
) -> tuple[dict[int, JsonObject], int, int]:
    """Validate complete one-shot predictions against exactly frozen candidate inputs."""

    if active_contract.materialized_schema is None:
        raise GoldSampleError("prediction contract lacks a materialized result schema")
    validator = Draft202012Validator(active_contract.materialized_schema)
    predictions: dict[int, JsonObject] = {}
    success_count = failure_count = 0
    for record in records:
        news_item_id = record.get("news_item_id")
        if (
            isinstance(news_item_id, bool)
            or not isinstance(news_item_id, int)
            or news_item_id <= 0
        ):
            raise GoldSampleError("heldout prediction has invalid news_item_id")
        if news_item_id in predictions:
            raise GoldSampleError(f"heldout predictions duplicate news item {news_item_id}")
        frozen = candidate_inputs.get(news_item_id)
        if frozen is None:
            raise GoldSampleError(f"heldout prediction {news_item_id} is outside candidate batch")
        expected_declared_input_sha256 = frozen.get(
            "declared_input_sha256",
            frozen.get("input_sha256"),
        )
        if (
            record.get("input_sha256") != frozen.get("input_sha256")
            or (
                active_contract.evidence_candidate_selection
                and record.get("declared_input_sha256")
                != expected_declared_input_sha256
            )
            or record.get("text_sha256") != frozen.get("text_sha256")
            or record.get("contract_sha256") != active_contract.sha256
            or record.get("model") != active_contract.model
        ):
            raise GoldSampleError(
                f"heldout prediction {news_item_id} frozen-input/contract binding drifted"
            )
        status = record.get("status")
        prediction = record.get("prediction")
        if status == "ok":
            if not isinstance(prediction, Mapping):
                raise GoldSampleError(
                    f"successful heldout prediction {news_item_id} has no prediction"
                )
            candidate = {str(key): value for key, value in prediction.items()}
            errors = sorted(validator.iter_errors(candidate), key=lambda item: list(item.path))
            if errors:
                raise GoldSampleError(
                    f"heldout prediction {news_item_id} violates schema: {errors[0].message}"
                )
            original_text = str(frozen["original_text"])
            ingested_symbol = frozen.get("ingested_symbol")
            universe_symbols = set(re.findall(r"(?<!\d)[0-9]{6}(?!\d)", original_text))
            if isinstance(ingested_symbol, str):
                universe_symbols.add(ingested_symbol)
            validate_materialized_event_result(
                active_contract,
                candidate,
                original_text=original_text,
                ingested_symbol=ingested_symbol if isinstance(ingested_symbol, str) else None,
                universe_symbols=universe_symbols,
            )
            success_count += 1
        else:
            if prediction is not None:
                raise GoldSampleError(
                    f"failed heldout prediction {news_item_id} must have prediction=null"
                )
            failure_count += 1
        predictions[news_item_id] = record
    if set(predictions) != set(candidate_inputs):
        raise GoldSampleError("heldout prediction coverage is not the exact candidate batch")
    return predictions, success_count, failure_count


def _validate_prediction_manifest(
    manifest: Mapping[str, object],
    *,
    design: FrozenEvaluationDesign,
    active_contract: EventExtractContract,
    receipt_sha256: str,
    inputs_sha256: str,
    predictions_sha256: str,
    candidate_records: Sequence[Mapping[str, object]],
    success_count: int,
    failure_count: int,
    materialization_binding: Mapping[str, object] | None = None,
) -> None:
    candidate_ids = [record.get("news_item_id") for record in candidate_records]
    if any(
        isinstance(news_item_id, bool) or not isinstance(news_item_id, int)
        for news_item_id in candidate_ids
    ):
        raise GoldSampleError("heldout prediction manifest candidate IDs are invalid")
    candidate_identity_sha256 = _ordered_candidate_identity_sha256(candidate_records)
    expected: dict[str, object] = {
        "design_sha256": design.sha256,
        "prediction_contract_sha256": active_contract.sha256,
        "freeze_receipt_sha256": receipt_sha256,
        "candidate_inputs_sha256": inputs_sha256,
        "candidate_predictions_sha256": predictions_sha256,
        "candidate_count": len(candidate_ids),
        "prediction_attempted_count": len(candidate_ids),
        "prediction_success_count": success_count,
        "prediction_failure_count": failure_count,
        "news_item_ids": list(candidate_ids),
    }
    expected_materialization = (
        dict(materialization_binding) if materialization_binding is not None else None
    )
    if manifest.get("materialization") != expected_materialization:
        raise GoldSampleError("heldout prediction manifest materialization binding drifted")
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise GoldSampleError(f"heldout prediction manifest {field} drifted")
    prediction_contract = _mapping(
        manifest.get("prediction_contract"),
        label="heldout prediction manifest prediction_contract",
    )
    candidate_inputs = _mapping(
        manifest.get("candidate_inputs"),
        label="heldout prediction manifest candidate_inputs",
    )
    predictions = _mapping(
        manifest.get("predictions"),
        label="heldout prediction manifest predictions",
    )
    expected_identities = []
    for record in candidate_records:
        identity = {
            "news_item_id": record["news_item_id"],
            "input_sha256": record["input_sha256"],
            "text_sha256": record["text_sha256"],
        }
        if active_contract.evidence_candidate_selection:
            identity["declared_input_sha256"] = record["declared_input_sha256"]
        expected_identities.append(identity)
    candidate_contract_bindings_valid = True
    if active_contract.evidence_candidate_selection:
        contract_files = _mapping(
            active_contract.document.get("contract_files"),
            label="active contract files",
        )
        registration = _mapping(
            design.document.get("active_prediction_contract"),
            label="registered active prediction contract",
        )
        candidate_contract_bindings_valid = (
            prediction_contract.get("input_representation")
            == registration.get("input_representation")
            and prediction_contract.get("model_result_schema")
            == contract_files.get("schema")
            and prediction_contract.get("materialized_result_schema")
            == contract_files.get("materialized_schema")
            and prediction_contract.get("candidate_materialization")
            == registration.get("candidate_materialization")
        )
    if (
        prediction_contract.get("sha256") != active_contract.sha256
        or prediction_contract.get("freeze_receipt_sha256") != receipt_sha256
        or not candidate_contract_bindings_valid
        or candidate_inputs.get("sha256") != inputs_sha256
        or candidate_inputs.get("count") != len(candidate_records)
        or candidate_inputs.get("identity_sha256") != candidate_identity_sha256
        or candidate_inputs.get("identities") != expected_identities
        or predictions.get("sha256") != predictions_sha256
        or predictions.get("row_count") != len(candidate_records)
        or predictions.get("attempted_count") != len(candidate_records)
        or predictions.get("success_count") != success_count
        or predictions.get("failure_count") != failure_count
    ):
        raise GoldSampleError("heldout prediction manifest nested binding drifted")


def _heldout_owner_record(
    *,
    candidate: Mapping[str, object],
    row: NewsRow,
    base_contract: FrozenContract,
    sample_index: int,
) -> JsonObject:
    original_text = _record_string(candidate, "original_text")
    active_input_sha256 = _record_string(candidate, "input_sha256")
    input_sha256 = (
        _record_string(candidate, "declared_input_sha256")
        if "declared_input_sha256" in candidate
        else active_input_sha256
    )
    text_sha256 = _record_string(candidate, "text_sha256")
    owner_rank = _sha256_bytes(
        (
            "blind-owner-record-v1.1\0"
            f"{row.news_item_id}\0{input_sha256}\0{text_sha256}"
        ).encode()
    )
    record: JsonObject = {
        "schema_version": "p4.2a-gold-annotation-item-v1",
        "sample_version": "p4.2a-gold-v1",
        "contract_sha256": base_contract.sha256,
        "sample_index": sample_index,
        "sample_group": "heldout40",
        "trading_date": row.available_time.astimezone(SHANGHAI).date().isoformat(),
        "stratum": {
            "source": row.source,
            "symbol_state": row.symbol_state,
            "require_announcement_body": row.source == "cninfo",
        },
        # This opaque row-identity hash is independent of model output and
        # selection order. The real selection rank exists only in the manifest.
        "rank_sha256": owner_rank,
        "news_item_id": row.news_item_id,
        "source": row.source,
        "url": row.url,
        "title": row.title,
        "ingested_symbol": row.ingested_symbol,
        "published_at": _iso_utc(row.published_at),
        "available_time": _iso_utc(row.available_time),
        "original_text": original_text,
        "body_state": _record_string(candidate, "body_state"),
        "content_hash": row.content_hash,
        "text_sha256": text_sha256,
        "body_evidence": candidate["body_evidence"],
        "annotation_status": "pending",
        "annotation_owner": None,
        "annotated_at": None,
        "gold": _gold_null_template(base_contract),
        "input_sha256": input_sha256,
    }
    validate_blind_record(record, base_contract)
    return record


def build_heldout_owner_sample(
    design_path: Path = DEFAULT_EVALUATION_DESIGN,
    database_path: Path | None = None,
    *,
    now: datetime | None = None,
) -> JsonObject:
    """Blind-select heldout40 from the frozen positive pool without refetching bodies."""

    design = load_evaluation_design(design_path)
    current_time = now or datetime.now(UTC)
    require_heldout_ready(design, current_time)
    active_contract, receipt, receipt_sha256 = load_active_prediction_contract(design)
    inference_state, inference_state_sha256 = load_completed_one_shot_state(
        design,
        scope="inference",
    )
    inputs_path = evaluation_artifact_path(design, "heldout_candidate_inputs_jsonl")
    prediction_path = evaluation_artifact_path(
        design,
        "heldout_candidate_predictions_jsonl",
    )
    prediction_manifest_path = evaluation_artifact_path(
        design,
        "heldout_candidate_predictions_manifest_json",
    )
    candidate_records, inputs_sha256 = _load_jsonl_with_sha256(
        inputs_path,
        label="heldout candidate inputs",
    )
    prediction_records, predictions_sha256 = _load_jsonl_with_sha256(
        prediction_path,
        label="heldout candidate predictions",
    )
    prediction_manifest, prediction_manifest_sha256 = _load_json_with_sha256(
        prediction_manifest_path,
        label="heldout candidate prediction manifest",
    )
    materialization_manifest: JsonObject | None = None
    materialization_manifest_path: Path | None = None
    materialization_manifest_sha256: str | None = None
    materialization_binding: JsonObject | None = None
    materialization_enabled = (
        design.document.get("schema_version") == "p4.2a-evaluation-design-v1.7"
    )
    if materialization_enabled:
        materialization_manifest_path = evaluation_artifact_path(
            design,
            "heldout_candidate_materialization_manifest_json",
        )
        materialization_manifest, materialization_manifest_sha256 = (
            _load_json_with_sha256(
                materialization_manifest_path,
                label="heldout candidate materialization manifest",
            )
        )
    database = _database_path(design.base_contract, database_path)
    with open_read_only_database(database) as connection:
        raw_candidate_rows = _heldout_candidate_rows(connection, design)
        if materialization_enabled:
            assert materialization_manifest is not None
            assert materialization_manifest_path is not None
            assert materialization_manifest_sha256 is not None
            materialization_manifest = validate_heldout_materialization_manifest(
                materialization_manifest,
                rows=raw_candidate_rows,
                eligible_records=candidate_records,
                design=design,
                active_contract=active_contract,
                freeze_receipt_sha256=receipt_sha256,
            )
            candidate_rows = list(
                heldout_eligible_rows_from_materialization(
                    raw_candidate_rows,
                    materialization_manifest,
                )
            )
            materialization_binding = heldout_materialization_binding(
                materialization_manifest,
                manifest_path=materialization_manifest_path,
                manifest_sha256=materialization_manifest_sha256,
            )
        else:
            candidate_rows = raw_candidate_rows
        candidate_inputs = validate_heldout_candidate_inputs(
            candidate_records,
            rows=candidate_rows,
            design=design,
            active_contract=active_contract,
        )
        predictions_by_id, success_count, failure_count = (
            validate_heldout_candidate_predictions(
                prediction_records,
                candidate_inputs=candidate_inputs,
                active_contract=active_contract,
            )
        )
    _validate_prediction_manifest(
        prediction_manifest,
        design=design,
        active_contract=active_contract,
        receipt_sha256=receipt_sha256,
        inputs_sha256=inputs_sha256,
        predictions_sha256=predictions_sha256,
        candidate_records=candidate_records,
        success_count=success_count,
        failure_count=failure_count,
        materialization_binding=materialization_binding,
    )
    validate_inference_completion_bindings(
        inference_state,
        design=design,
        active_contract=active_contract,
        receipt_sha256=receipt_sha256,
        candidate_records=candidate_records,
        candidate_inputs_sha256=inputs_sha256,
        prediction_manifest_sha256=prediction_manifest_sha256,
        attempted_count=len(prediction_records),
        success_count=success_count,
        failure_count=failure_count,
        materialization_binding=materialization_binding,
    )
    splits = _mapping(design.document.get("splits"), label="splits")
    heldout = _mapping(splits.get("heldout_40"), label="splits.heldout_40")
    sampling = _mapping(heldout.get("sampling"), label="heldout sampling")
    selected_predictions, selection_evidence = select_heldout_positive_predictions(
        list(predictions_by_id.values()),
        seed=str(sampling["deterministic_seed"]),
        count=int(sampling["selected_count"]),
    )
    selected_ids = [int(record["news_item_id"]) for record in selected_predictions]
    rows_by_id = {row.news_item_id: row for row in candidate_rows}
    heldout_records = [
        _heldout_owner_record(
            candidate=candidate_inputs[news_item_id],
            row=rows_by_id[news_item_id],
            base_contract=design.base_contract,
            sample_index=index,
        )
        for index, news_item_id in enumerate(selected_ids, start=1)
    ]

    dev_path = evaluation_artifact_path(design, "dev_60_frozen_jsonl")
    dev_records, dev_sha256 = _load_jsonl_with_sha256(dev_path, label="frozen dev60")
    if len(dev_records) != 60:
        raise GoldSampleError("frozen dev60 must contain exactly 60 rows")
    for index, record in enumerate(dev_records, start=1):
        validate_blind_record(record, design.base_contract)
        if record.get("sample_index") != index:
            raise GoldSampleError("frozen dev60 order drifted")
    forbidden = _mapping(
        design.document.get("owner_delivery"),
        label="owner_delivery",
    ).get("forbidden_fields")
    if not isinstance(forbidden, list) or any(not isinstance(item, str) for item in forbidden):
        raise GoldSampleError("owner-delivery forbidden fields are invalid")
    violations = owner_forbidden_field_paths(heldout_records, frozenset(forbidden))
    if violations:
        raise GoldSampleError(
            f"owner annotation payload leaks model/selection fields: {violations[:3]}"
        )

    heldout_payload = _json_line_bytes(heldout_records)
    eligible_count = sum(
        1
        for record in predictions_by_id.values()
        if record.get("status") == "ok"
        and isinstance(record.get("prediction"), Mapping)
        and int(_mapping(record["prediction"], label="prediction")["materiality"]) >= 2
    )
    selection_manifest: JsonObject = {
        "schema_version": (
            "p4.2a-heldout-selection-manifest-v1.2"
            if materialization_binding is not None
            else "p4.2a-heldout-selection-manifest-v1.1"
        ),
        "created_at_utc": inference_state["terminal_at_utc"],
        "design": {
            "path": str(design.path.relative_to(PROJECT_DIR)),
            "schema_version": design.document["schema_version"],
            "sha256": design.sha256,
        },
        "annotation_contract": {
            "path": str(design.base_contract.path.relative_to(PROJECT_DIR)),
            "sha256": design.base_contract.sha256,
        },
        "prediction_contract": {
            **receipt,
            "freeze_receipt_sha256": receipt_sha256,
        },
        "inference": {
            **inference_state,
            "state_sha256": inference_state_sha256,
        },
        "candidate_inputs": {
            "path": str(inputs_path.relative_to(PROJECT_DIR)),
            "sha256": inputs_sha256,
            "count": len(candidate_inputs),
            "cninfo_bodies_frozen_before_prediction": True,
        },
        **(
            {"materialization": materialization_binding}
            if materialization_binding is not None
            else {}
        ),
        "candidate_predictions": {
            "path": str(prediction_path.relative_to(PROJECT_DIR)),
            "sha256": predictions_sha256,
            "manifest_path": str(prediction_manifest_path.relative_to(PROJECT_DIR)),
            "manifest_sha256": prediction_manifest_sha256,
            "attempted_count": len(predictions_by_id),
            "success_count": success_count,
            "failure_count": failure_count,
        },
        "eligible_pool": {
            "definition": "status=ok and prediction.materiality>=2",
            "count": eligible_count,
            "positive_rate_denominator": "successful_predictions",
            "positive_rate": eligible_count / success_count if success_count else None,
        },
        "selection": {
            "algorithm": sampling["algorithm"],
            "seed": sampling["deterministic_seed"],
            "without_replacement": True,
            "selected_count": len(heldout_records),
            "selected": selection_evidence,
        },
        "owner_delivery": {
            "predictions_visible": False,
            "selection_basis_visible": False,
            "forbidden_field_violation_count": 0,
            "heldout_blind_sample_path": str(
                evaluation_artifact_path(
                    design,
                    "heldout_40_blind_sample_jsonl",
                ).relative_to(PROJECT_DIR)
            ),
            "heldout_blind_sample_sha256": _sha256_bytes(heldout_payload),
            "heldout_blind_sample_count": len(heldout_records),
            "combined_target_path": str(
                evaluation_artifact_path(
                    design,
                    "combined_100_annotations_jsonl",
                ).relative_to(PROJECT_DIR)
            ),
            "combined_created": False,
            "dev60_source_sha256": dev_sha256,
        },
    }
    manifest_payload = (
        json.dumps(
            selection_manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    artifact_root = _project_path(design.document["artifact_root"], label="artifact_root")
    heldout_path = _artifact_path(
        evaluation_artifact_path(design, "heldout_40_blind_sample_jsonl"),
        artifact_root,
    )
    manifest_path = _artifact_path(
        evaluation_artifact_path(design, "heldout_selection_manifest_json"),
        artifact_root,
    )
    hashes = _write_create_only_bundle(
        {
            heldout_path: heldout_payload,
            manifest_path: manifest_payload,
        }
    )
    return {
        "mode": "heldout",
        "candidate_count": len(candidate_inputs),
        "raw_candidate_count": len(raw_candidate_rows),
        "ineligible_candidate_count": (
            int(materialization_binding["ineligible_candidate_count"])
            if materialization_binding is not None
            else 0
        ),
        "prediction_success_count": success_count,
        "prediction_failure_count": failure_count,
        "predicted_positive_pool_count": eligible_count,
        "predicted_positive_pool_rate": (
            eligible_count / success_count if success_count else None
        ),
        "selected_count": len(heldout_records),
        "heldout_blind_sample": str(heldout_path.relative_to(PROJECT_DIR)),
        "heldout_blind_sample_sha256": hashes[heldout_path],
        "combined_created": False,
        "selection_manifest": str(manifest_path.relative_to(PROJECT_DIR)),
        "selection_manifest_sha256": hashes[manifest_path],
        "second_cninfo_body_fetch_count": 0,
    }


def _validate_completed_identity_against_blind(
    *,
    blind_records: Sequence[JsonObject],
    owner_records: Sequence[JsonObject],
    label: str,
) -> None:
    if len(blind_records) != len(owner_records):
        raise GoldSampleError(f"{label} owner/blind row counts differ")
    immutable_fields = ANNOTATION_ITEM_FIELDS - ANNOTATION_MUTABLE_FIELDS
    for blind, owner in zip(blind_records, owner_records, strict=True):
        news_item_id = blind.get("news_item_id")
        if owner.get("news_item_id") != news_item_id:
            raise GoldSampleError(f"{label} owner order/ID differs from blind source")
        for field in immutable_fields:
            if owner.get(field) != blind.get(field):
                raise GoldSampleError(
                    f"{label} owner news item {news_item_id} changed frozen field {field}"
                )


def _combined_ordered_identity_sha256(records: Sequence[Mapping[str, object]]) -> str:
    digest = hashlib.sha256()
    for record in records:
        sample_index = record.get("sample_index")
        news_item_id = record.get("news_item_id")
        input_sha256 = record.get("input_sha256")
        text_sha256 = record.get("text_sha256")
        if (
            isinstance(sample_index, bool)
            or not isinstance(sample_index, int)
            or isinstance(news_item_id, bool)
            or not isinstance(news_item_id, int)
            or not isinstance(input_sha256, str)
            or SHA256_PATTERN.fullmatch(input_sha256) is None
            or not isinstance(text_sha256, str)
            or SHA256_PATTERN.fullmatch(text_sha256) is None
        ):
            raise GoldSampleError("combined owner identity tuple is invalid")
        digest.update(
            (
                f"{sample_index}\0{news_item_id}\0"
                f"{input_sha256}\0{text_sha256}\n"
            ).encode("ascii")
        )
    return digest.hexdigest()


def _normalized_completed_records(records: Sequence[JsonObject]) -> list[JsonObject]:
    normalized = copy.deepcopy(list(records))
    for record in normalized:
        record["annotation_status"] = "completed"
    return normalized


def _owner_completion_time(
    records: Sequence[Mapping[str, object]],
) -> datetime:
    """Return a deterministic completion time from the owner-supplied labels."""

    completed: list[datetime] = []
    for record in records:
        news_item_id = record.get("news_item_id")
        value = record.get("annotated_at")
        if not isinstance(value, str) or not value.strip():
            raise GoldSampleError(
                f"owner annotation {news_item_id} must include annotated_at"
            )
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise GoldSampleError(
                f"owner annotation {news_item_id} has invalid annotated_at"
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise GoldSampleError(
                f"owner annotation {news_item_id} annotated_at must include a timezone"
            )
        completed.append(parsed.astimezone(UTC))
    if not completed:
        raise GoldSampleError("owner annotations are empty")
    return max(completed)


ADJUDICATION_EXPORT_FIELDS = frozenset(
    {
        "schema_version",
        "news_item_id",
        "gold",
        "draft_gold",
        "annotation_status",
        "adjudication",
    }
)
ADJUDICATION_AUDIT_FIELDS = frozenset(
    {
        "method",
        "draft_annotator",
        "adjudicator",
        "adjudicated_changed",
        "changed_fields",
        "adjudicated_at",
    }
)
ADJUDICATION_GOLD_FIELDS = (
    "symbols",
    "event_type",
    "direction",
    "materiality",
    "evidence_span",
    "notes",
)


def _strict_aware_datetime(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise GoldSampleError(f"{label} must be a timezone-aware ISO datetime")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise GoldSampleError(f"{label} is not an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GoldSampleError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _validated_adjudication_gold(
    value: object,
    *,
    original_text: str,
    taxonomy: frozenset[str],
    label: str,
) -> JsonObject:
    if not isinstance(value, Mapping) or set(value) != set(ADJUDICATION_GOLD_FIELDS):
        raise GoldSampleError(f"{label} fields drifted")
    symbols = value.get("symbols")
    if (
        not isinstance(symbols, Sequence)
        or isinstance(symbols, (str, bytes))
        or any(not isinstance(symbol, str) for symbol in symbols)
    ):
        raise GoldSampleError(f"{label}.symbols must be an array")
    normalized_symbols = [str(symbol) for symbol in symbols]
    if (
        any(SIX_DIGIT_SYMBOL.fullmatch(symbol) is None for symbol in normalized_symbols)
        or normalized_symbols != sorted(set(normalized_symbols))
    ):
        raise GoldSampleError(
            f"{label}.symbols must contain sorted unique six-digit symbols"
        )
    event_type = value.get("event_type")
    if not isinstance(event_type, str) or event_type not in taxonomy:
        raise GoldSampleError(f"{label}.event_type is invalid")
    direction = value.get("direction")
    if (
        isinstance(direction, bool)
        or not isinstance(direction, int)
        or direction not in {-1, 0, 1}
    ):
        raise GoldSampleError(f"{label}.direction is invalid")
    materiality = value.get("materiality")
    if (
        isinstance(materiality, bool)
        or not isinstance(materiality, int)
        or materiality not in {0, 1, 2, 3}
    ):
        raise GoldSampleError(f"{label}.materiality is invalid")
    evidence_span = value.get("evidence_span")
    if (
        not isinstance(evidence_span, str)
        or not evidence_span
        or evidence_span not in original_text
    ):
        raise GoldSampleError(f"{label}.evidence_span is not a contiguous substring")
    notes = value.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise GoldSampleError(f"{label}.notes is invalid")
    return {
        "symbols": normalized_symbols,
        "event_type": event_type,
        "direction": direction,
        "materiality": materiality,
        "evidence_span": evidence_span,
        "notes": notes,
    }


def _normalize_heldout_adjudication_export(
    *,
    blind_records: Sequence[JsonObject],
    draft_records: Sequence[JsonObject],
    adjudicated_records: Sequence[JsonObject],
    design: FrozenEvaluationDesign,
) -> list[JsonObject]:
    """Bind a compact adjudication audit export back to frozen canonical rows."""

    expected_count = len(blind_records)
    if (
        expected_count == 0
        or len(draft_records) != expected_count
        or len(adjudicated_records) != expected_count
    ):
        raise GoldSampleError(
            "heldout blind, AI draft, and adjudication export row counts differ"
        )
    taxonomy_record = _mapping(
        design.base_contract.document.get("taxonomy"),
        label="taxonomy",
    )
    taxonomy_values = taxonomy_record.get("values")
    if not isinstance(taxonomy_values, list) or any(
        not isinstance(item, str) for item in taxonomy_values
    ):
        raise GoldSampleError("heldout annotation taxonomy is invalid")
    taxonomy = frozenset(taxonomy_values)
    immutable_fields = ANNOTATION_ITEM_FIELDS - ANNOTATION_MUTABLE_FIELDS
    draft_annotator: str | None = None
    adjudicator: str | None = None
    canonical: list[JsonObject] = []

    for blind, draft, adjudicated in zip(
        blind_records,
        draft_records,
        adjudicated_records,
        strict=True,
    ):
        news_item_id = blind.get("news_item_id")
        if (
            draft.get("news_item_id") != news_item_id
            or adjudicated.get("news_item_id") != news_item_id
        ):
            raise GoldSampleError(
                "heldout AI draft/adjudication order or news_item_id differs from blind source"
            )
        if set(draft) != ANNOTATION_ITEM_FIELDS:
            raise GoldSampleError(f"AI draft news item {news_item_id} fields drifted")
        for field in immutable_fields:
            if draft.get(field) != blind.get(field):
                raise GoldSampleError(
                    f"AI draft news item {news_item_id} changed frozen field {field}"
                )
        if draft.get("annotation_status") not in {"annotated", "complete", "completed"}:
            raise GoldSampleError(f"AI draft news item {news_item_id} is not completed")
        raw_draft_annotator = draft.get("annotation_owner")
        if not isinstance(raw_draft_annotator, str) or not raw_draft_annotator.strip():
            raise GoldSampleError(
                f"AI draft news item {news_item_id} has no annotator identity"
            )
        row_draft_annotator = raw_draft_annotator.strip()
        if draft_annotator is None:
            draft_annotator = row_draft_annotator
        elif row_draft_annotator != draft_annotator:
            raise GoldSampleError("AI draft uses inconsistent annotator identities")
        _strict_aware_datetime(
            draft.get("annotated_at"),
            label=f"AI draft news item {news_item_id} annotated_at",
        )
        original_text = blind.get("original_text")
        if not isinstance(original_text, str) or not original_text:
            raise GoldSampleError(f"heldout blind news item {news_item_id} has no text")
        draft_gold = _validated_adjudication_gold(
            draft.get("gold"),
            original_text=original_text,
            taxonomy=taxonomy,
            label=f"AI draft news item {news_item_id} gold",
        )
        if draft_gold["notes"] is not None:
            raise GoldSampleError(
                f"AI draft news item {news_item_id} must keep notes empty"
            )

        if set(adjudicated) != ADJUDICATION_EXPORT_FIELDS:
            raise GoldSampleError(
                f"adjudication news item {news_item_id} fields drifted"
            )
        if (
            adjudicated.get("schema_version")
            != "p4.2a-heldout-adjudication-export-v1"
            or adjudicated.get("annotation_status") != "adjudicated"
        ):
            raise GoldSampleError(
                f"adjudication news item {news_item_id} schema/status drifted"
            )
        export_draft_gold = _validated_adjudication_gold(
            adjudicated.get("draft_gold"),
            original_text=original_text,
            taxonomy=taxonomy,
            label=f"adjudication news item {news_item_id} draft_gold",
        )
        if export_draft_gold != draft_gold:
            raise GoldSampleError(
                f"adjudication news item {news_item_id} draft_gold differs from explicit AI draft"
            )
        final_gold = _validated_adjudication_gold(
            adjudicated.get("gold"),
            original_text=original_text,
            taxonomy=taxonomy,
            label=f"adjudication news item {news_item_id} gold",
        )
        audit = _mapping(
            adjudicated.get("adjudication"),
            label=f"adjudication news item {news_item_id} audit",
        )
        if set(audit) != ADJUDICATION_AUDIT_FIELDS:
            raise GoldSampleError(
                f"adjudication news item {news_item_id} audit fields drifted"
            )
        if (
            audit.get("method") != "ai_drafted_human_adjudicated"
            or audit.get("draft_annotator") != row_draft_annotator
        ):
            raise GoldSampleError(
                f"adjudication news item {news_item_id} AI provenance drifted"
            )
        raw_adjudicator = audit.get("adjudicator")
        if not isinstance(raw_adjudicator, str) or not raw_adjudicator.strip():
            raise GoldSampleError(
                f"adjudication news item {news_item_id} has no human adjudicator"
            )
        row_adjudicator = raw_adjudicator.strip()
        if row_adjudicator.casefold() == row_draft_annotator.casefold():
            raise GoldSampleError(
                f"adjudication news item {news_item_id} AI and human identities match"
            )
        if adjudicator is None:
            adjudicator = row_adjudicator
        elif row_adjudicator != adjudicator:
            raise GoldSampleError("adjudication export uses inconsistent human identities")
        adjudicated_at = _strict_aware_datetime(
            audit.get("adjudicated_at"),
            label=f"adjudication news item {news_item_id} adjudicated_at",
        )
        changed_fields = [
            field
            for field in ADJUDICATION_GOLD_FIELDS
            if final_gold[field] != draft_gold[field]
        ]
        adjudicated_changed = bool(changed_fields)
        if (
            audit.get("adjudicated_changed") is not adjudicated_changed
            or audit.get("changed_fields") != changed_fields
        ):
            raise GoldSampleError(
                f"adjudication news item {news_item_id} changed_fields drifted"
            )
        record = copy.deepcopy(blind)
        record.update(
            {
                "annotation_status": "completed",
                "annotation_owner": row_adjudicator,
                "annotated_at": _iso_utc(adjudicated_at),
                "gold": final_gold,
                "annotation_type": "ai_drafted_human_adjudicated",
                "drafter_id": row_draft_annotator,
                "adjudicator_id": row_adjudicator,
            }
        )
        canonical.append(record)
    return canonical


def combine_owner_annotations(
    *,
    dev_owner_export: Path,
    heldout_owner_export: Path,
    heldout_ai_draft: Path | None = None,
    design_path: Path = DEFAULT_EVALUATION_DESIGN,
    now: datetime | None = None,
    project_root: Path = PROJECT_DIR,
) -> JsonObject:
    """Create canonical completed split/combined artifacts only after owner labels."""

    from scripts.evaluate_p4_2a_gold import (
        GoldEvaluationError,
        validate_owner_annotations,
    )

    root = project_root.resolve()
    design = load_evaluation_design(design_path)
    current_time = now or datetime.now(UTC)
    require_heldout_ready(design, current_time)
    dev_owner_records, dev_export_sha256 = _load_jsonl_with_sha256(
        dev_owner_export.resolve(),
        label="dev60 owner export",
    )
    heldout_export_records, _heldout_export_sha256 = _load_jsonl_with_sha256(
        heldout_owner_export.resolve(),
        label="heldout40 owner export",
    )
    dev_annotation_artifact = _mapping(
        _mapping(design.document.get("artifacts"), label="artifacts").get(
            "dev_60_owner_annotations_jsonl"
        ),
        label="artifacts.dev_60_owner_annotations_jsonl",
    )
    frozen_dev_sha256 = dev_annotation_artifact.get("sha256")
    if frozen_dev_sha256 is not None and dev_export_sha256 != frozen_dev_sha256:
        raise GoldSampleError(
            "dev60 AI-drafted annotation bytes differ from the frozen design"
        )
    dev_blind_path = evaluation_artifact_path(
        design,
        "dev_60_frozen_jsonl",
        project_root=root,
    )
    heldout_blind_path = evaluation_artifact_path(
        design,
        "heldout_40_blind_sample_jsonl",
        project_root=root,
    )
    selection_manifest_path = evaluation_artifact_path(
        design,
        "heldout_selection_manifest_json",
        project_root=root,
    )
    dev_blind, dev_blind_sha256 = _load_jsonl_with_sha256(
        dev_blind_path,
        label="frozen dev60 blind sample",
    )
    heldout_blind, heldout_blind_sha256 = _load_jsonl_with_sha256(
        heldout_blind_path,
        label="frozen heldout40 blind sample",
    )
    selection_manifest, selection_manifest_sha256 = _load_json_with_sha256(
        selection_manifest_path,
        label="heldout selection manifest",
    )
    if len(dev_blind) != 60 or len(heldout_blind) != 40:
        raise GoldSampleError("owner-completion blind split counts drifted")
    dev_artifact = _mapping(
        _mapping(design.document.get("artifacts"), label="artifacts").get(
            "dev_60_frozen_jsonl"
        ),
        label="artifacts.dev_60_frozen_jsonl",
    )
    if dev_artifact.get("sha256") != dev_blind_sha256:
        raise GoldSampleError("frozen dev60 bytes differ from the evaluation design")
    for blind in [*dev_blind, *heldout_blind]:
        validate_blind_record(blind, design.base_contract)

    compact_adjudication = any(
        "adjudication" in record or "draft_gold" in record
        for record in heldout_export_records
    )
    adjudication_source_evidence: JsonObject = {}
    if compact_adjudication:
        if "heldout_annotation_provenance" not in design.document:
            raise GoldSampleError(
                "compact adjudication export requires a human-provenance evaluation design"
            )
        if heldout_ai_draft is None:
            raise GoldSampleError(
                "compact adjudication export requires --heldout-ai-draft for explicit binding"
            )
        artifact_root_value = design.document.get("artifact_root")
        if not isinstance(artifact_root_value, str) or not artifact_root_value:
            raise GoldSampleError("artifact_root must be a non-empty path")
        adjudication_artifact_root = (root / artifact_root_value).resolve()
        heldout_export_resolved = heldout_owner_export.resolve()
        heldout_ai_draft_resolved = heldout_ai_draft.resolve()
        if (
            not heldout_export_resolved.is_relative_to(adjudication_artifact_root)
            or not heldout_ai_draft_resolved.is_relative_to(adjudication_artifact_root)
        ):
            raise GoldSampleError(
                "compact adjudication export and AI draft must stay under docs/phase4/eval"
            )
        if heldout_export_resolved == heldout_ai_draft_resolved:
            raise GoldSampleError(
                "compact adjudication export and AI draft must be distinct files"
            )
        draft_records, _draft_sha256 = _load_jsonl_with_sha256(
            heldout_ai_draft_resolved,
            label="heldout40 explicit AI draft",
        )
        heldout_owner_records = _normalize_heldout_adjudication_export(
            blind_records=heldout_blind,
            draft_records=draft_records,
            adjudicated_records=heldout_export_records,
            design=design,
        )
        adjudication_source_evidence = {
            "heldout_adjudication_export": str(
                heldout_export_resolved.relative_to(root)
            ),
            "heldout_adjudication_export_sha256": _heldout_export_sha256,
            "heldout_ai_draft": str(heldout_ai_draft_resolved.relative_to(root)),
            "heldout_ai_draft_sha256": _draft_sha256,
        }
    else:
        if heldout_ai_draft is not None:
            raise GoldSampleError(
                "--heldout-ai-draft is only valid with a compact adjudication export"
            )
        heldout_owner_records = heldout_export_records

    try:
        validate_owner_annotations(
            dev_owner_records,
            design.base_contract,
            expected_count=60,
            expected_start_index=1,
            expected_sample_group="inventory_60",
            design=design,
        )
        validate_owner_annotations(
            heldout_owner_records,
            design.base_contract,
            expected_count=40,
            expected_start_index=1,
            expected_sample_group="heldout40",
            design=design,
        )
    except GoldEvaluationError as exc:
        raise GoldSampleError(f"owner completion validation failed: {exc}") from exc
    _validate_completed_identity_against_blind(
        blind_records=dev_blind,
        owner_records=dev_owner_records,
        label="dev60",
    )
    _validate_completed_identity_against_blind(
        blind_records=heldout_blind,
        owner_records=heldout_owner_records,
        label="heldout40",
    )
    selection_owner = _mapping(
        selection_manifest.get("owner_delivery"),
        label="selection manifest owner_delivery",
    )
    if (
        _mapping(selection_manifest.get("design"), label="selection manifest design").get(
            "sha256"
        )
        != design.sha256
        or selection_owner.get("heldout_blind_sample_path")
        != str(heldout_blind_path.relative_to(root))
        or selection_owner.get("heldout_blind_sample_sha256") != heldout_blind_sha256
        or selection_owner.get("heldout_blind_sample_count") != 40
    ):
        raise GoldSampleError("selection manifest no longer binds heldout blind sample")

    normalized_dev = _normalized_completed_records(dev_owner_records)
    normalized_heldout = _normalized_completed_records(heldout_owner_records)
    combined_heldout = copy.deepcopy(normalized_heldout)
    for sample_index, record in enumerate(combined_heldout, start=61):
        record["sample_index"] = sample_index
    combined = [*normalized_dev, *combined_heldout]
    try:
        validate_owner_annotations(
            combined,
            design.base_contract,
            expected_count=100,
            expected_start_index=1,
            design=design,
        )
    except GoldEvaluationError as exc:
        raise GoldSampleError(f"combined owner validation failed: {exc}") from exc
    owner_contract = _mapping(
        design.document.get("owner_delivery"),
        label="owner_delivery",
    )
    forbidden = owner_contract.get("forbidden_fields")
    if not isinstance(forbidden, list) or any(not isinstance(item, str) for item in forbidden):
        raise GoldSampleError("owner forbidden field contract is invalid")
    violations = owner_forbidden_field_paths(combined, frozenset(forbidden))
    if violations:
        raise GoldSampleError(f"owner completion leaks blind-forbidden fields: {violations[:3]}")

    dev_payload = _json_line_bytes(normalized_dev)
    heldout_payload = _json_line_bytes(normalized_heldout)
    combined_payload = _json_line_bytes(combined)
    completion = _mapping(
        design.document.get("owner_annotation_completion"),
        label="owner_annotation_completion",
    )
    combined_contract = _mapping(
        completion.get("combined"),
        label="owner_annotation_completion.combined",
    )
    renumbering_rule = combined_contract.get("renumbering_rule")
    if renumbering_rule != "dev_preserve_1_60_then_heldout_add_60_to_61_100":
        raise GoldSampleError("combined owner renumbering rule drifted")
    dev_output = evaluation_artifact_path(
        design,
        "dev_60_owner_annotations_jsonl",
        project_root=root,
    )
    heldout_output = evaluation_artifact_path(
        design,
        "heldout_40_owner_annotations_jsonl",
        project_root=root,
    )
    combined_output = evaluation_artifact_path(
        design,
        "combined_100_annotations_jsonl",
        project_root=root,
    )
    completion_manifest_path = evaluation_artifact_path(
        design,
        "owner_completion_manifest_json",
        project_root=root,
    )
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise GoldSampleError("owner completion time must include a timezone")
    completed_at = (
        now.astimezone(UTC)
        if now is not None
        else _owner_completion_time([*normalized_dev, *normalized_heldout])
    )
    manifest: JsonObject = {
        "schema_version": completion["schema_version"],
        "design_sha256": design.sha256,
        "annotation_contract_sha256": design.base_contract.sha256,
        "dev_blind_sample_path": str(dev_blind_path.relative_to(root)),
        "dev_blind_sample_sha256": dev_blind_sha256,
        "dev_owner_annotations_path": str(dev_output.relative_to(root)),
        "dev_owner_annotations_sha256": _sha256_bytes(dev_payload),
        "dev_owner_annotations_row_count": 60,
        "dev_completed_count": 60,
        "heldout_blind_sample_path": str(heldout_blind_path.relative_to(root)),
        "heldout_blind_sample_sha256": heldout_blind_sha256,
        "heldout_selection_manifest_path": str(
            selection_manifest_path.relative_to(root)
        ),
        "heldout_selection_manifest_sha256": selection_manifest_sha256,
        "heldout_owner_annotations_path": str(heldout_output.relative_to(root)),
        "heldout_owner_annotations_sha256": _sha256_bytes(heldout_payload),
        "heldout_owner_annotations_row_count": 40,
        "heldout_completed_count": 40,
        "combined_annotations_path": str(combined_output.relative_to(root)),
        "combined_annotations_sha256": _sha256_bytes(combined_payload),
        "combined_annotations_row_count": 100,
        "combined_ordered_identity_sha256": _combined_ordered_identity_sha256(combined),
        "combined_renumbering_rule": renumbering_rule,
        "identity_validation_passed": True,
        "blindness_validation_passed": True,
        "completed_at_utc": _iso_utc(completed_at),
    }
    if "heldout_annotation_provenance" in design.document:
        heldout_drafter_ids = sorted(
            {
                str(record["drafter_id"]).strip()
                for record in normalized_heldout
            }
        )
        heldout_adjudicator_ids = sorted(
            {
                str(record["adjudicator_id"]).strip()
                for record in normalized_heldout
            }
        )
        manifest.update(
            {
                "heldout_annotation_type": "ai_drafted_human_adjudicated",
                "heldout_drafter_ids": heldout_drafter_ids,
                "heldout_adjudicator_ids": heldout_adjudicator_ids,
                "heldout_human_adjudication_validated": True,
            }
        )
    required_manifest_fields = completion.get("required_manifest_fields")
    if (
        not isinstance(required_manifest_fields, list)
        or any(not isinstance(field, str) for field in required_manifest_fields)
        or set(manifest) != set(required_manifest_fields)
    ):
        raise GoldSampleError("owner-completion manifest fields drifted")
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )
    artifact_root_value = design.document.get("artifact_root")
    if not isinstance(artifact_root_value, str) or not artifact_root_value:
        raise GoldSampleError("artifact_root must be a non-empty path")
    artifact_root_relative = Path(artifact_root_value)
    if artifact_root_relative.is_absolute() or ".." in artifact_root_relative.parts:
        raise GoldSampleError("artifact_root escapes project root")
    artifact_root = (root / artifact_root_relative).resolve()
    if not artifact_root.is_relative_to(root):
        raise GoldSampleError("artifact_root escapes project root")
    dev_output = _artifact_path(dev_output, artifact_root)
    heldout_output = _artifact_path(heldout_output, artifact_root)
    combined_output = _artifact_path(combined_output, artifact_root)
    completion_manifest_path = _artifact_path(
        completion_manifest_path,
        artifact_root,
    )
    for target in (
        dev_output,
        heldout_output,
        combined_output,
        completion_manifest_path,
    ):
        if target.exists() or target.is_symlink():
            raise FileExistsError(
                f"refusing to overwrite completed owner artifact: {target}"
            )
    outputs = {
        dev_output: dev_payload,
        heldout_output: heldout_payload,
        combined_output: combined_payload,
        completion_manifest_path: manifest_payload,
    }
    hashes = _write_create_only_bundle(outputs)
    return {
        "mode": "combine-owner",
        "dev_completed_count": 60,
        "heldout_completed_count": 40,
        "combined_row_count": 100,
        "dev_owner_annotations": str(dev_output.relative_to(root)),
        "dev_owner_annotations_sha256": hashes[dev_output],
        "heldout_owner_annotations": str(heldout_output.relative_to(root)),
        "heldout_owner_annotations_sha256": hashes[heldout_output],
        "combined_annotations": str(combined_output.relative_to(root)),
        "combined_annotations_sha256": hashes[combined_output],
        "owner_completion_manifest": str(
            completion_manifest_path.relative_to(root)
        ),
        "owner_completion_manifest_sha256": hashes[completion_manifest_path],
        "identity_validation_passed": True,
        "blindness_validation_passed": True,
        **adjudication_source_evidence,
    }


def _artifact_paths(contract: FrozenContract) -> tuple[Path, Path, Path, Path]:
    artifact_root = _project_path(contract.document["artifact_root"], label="artifact_root")
    gold = _mapping(contract.document["gold_sample"], label="gold_sample")
    inventory = _project_path(
        gold["inventory_output_jsonl"], label="gold_sample.inventory_output_jsonl"
    )
    final = _project_path(gold["final_output_jsonl"], label="gold_sample.final_output_jsonl")
    manifest = _project_path(gold["manifest_json"], label="gold_sample.manifest_json")
    return artifact_root, inventory, final, manifest


def _database_path(contract: FrozenContract, override: Path | None) -> Path:
    if override is not None:
        return override.resolve()
    input_contract = _mapping(contract.document["input"], label="input")
    return _project_path(input_contract["production_database"], label="input.production_database")


def build_inventory_records(
    contract: FrozenContract,
    database_path: Path,
    *,
    pdf_fetcher: PdfFetcher = download_cninfo_pdf,
    pdf_text_extractor: PdfTextExtractor = extract_cninfo_pdf_text,
) -> tuple[list[JsonObject], JsonObject]:
    gold = _mapping(contract.document["gold_sample"], label="gold_sample")
    inventory = _mapping(gold["inventory_60"], label="gold_sample.inventory_60")
    cutoff = int(inventory["cutoff_news_item_id"])
    strata = _contract_strata(inventory["strata"], label="gold_sample.inventory_60.strata")
    with open_read_only_database(database_path) as connection:
        snapshot = inventory_database_snapshot(connection, contract)
        rows = _load_news_rows(connection, cutoff=cutoff, after_cutoff=False)
        selected = select_stratified_rows(
            rows,
            strata=strata,
            seed=str(gold["deterministic_seed"]),
            group="inventory_60",
        )
        records = materialize_selected_rows(
            selected,
            contract,
            starting_index=1,
            pdf_fetcher=pdf_fetcher,
            pdf_text_extractor=pdf_text_extractor,
        )
    if len(records) != 60:
        raise GoldSampleError(f"inventory builder produced {len(records)} rows instead of 60")
    return records, snapshot


def build_inventory_sample(
    contract_path: Path = DEFAULT_CONFIG,
    database_path: Path | None = None,
    *,
    pdf_fetcher: PdfFetcher = download_cninfo_pdf,
    pdf_text_extractor: PdfTextExtractor = extract_cninfo_pdf_text,
) -> JsonObject:
    """Create the frozen 60-row blind inventory JSONL and no other artifact."""

    contract = load_contract(contract_path)
    artifact_root, inventory_path, _, _ = _artifact_paths(contract)
    inventory_output = _new_artifact_path(inventory_path, artifact_root)
    records, snapshot = build_inventory_records(
        contract,
        _database_path(contract, database_path),
        pdf_fetcher=pdf_fetcher,
        pdf_text_extractor=pdf_text_extractor,
    )
    payload = _json_line_bytes(records)
    _write_new_bytes(inventory_output, payload)
    return {
        "mode": "inventory",
        "row_count": len(records),
        "output": str(inventory_output.relative_to(PROJECT_DIR)),
        "sha256": _sha256_bytes(payload),
        "database_snapshot": snapshot,
        "frozen_news_item_ids": [record["news_item_id"] for record in records],
    }


def _validate_frozen_inventory(
    records: Sequence[JsonObject],
    contract: FrozenContract,
    rows_by_id: Mapping[int, NewsRow],
) -> None:
    if len(records) != 60:
        raise GoldSampleError(f"frozen inventory must contain 60 rows, observed {len(records)}")
    gold = _mapping(contract.document["gold_sample"], label="gold_sample")
    inventory = _mapping(gold["inventory_60"], label="gold_sample.inventory_60")
    expected_counts = {
        (item.source, item.symbol_state): item.count
        for item in _contract_strata(inventory["strata"], label="gold_sample.inventory_60.strata")
    }
    observed_counts: dict[tuple[str, str], int] = {}
    seen: set[int] = set()
    for expected_index, record in enumerate(records, start=1):
        validate_blind_record(record, contract)
        news_item_id = record.get("news_item_id")
        assert isinstance(news_item_id, int)
        if news_item_id in seen:
            raise GoldSampleError(f"frozen inventory duplicates news item {news_item_id}")
        seen.add(news_item_id)
        if news_item_id > int(inventory["cutoff_news_item_id"]):
            raise GoldSampleError(f"frozen inventory news item {news_item_id} exceeds cutoff")
        row = rows_by_id.get(news_item_id)
        if row is None:
            raise GoldSampleError(f"frozen inventory news item {news_item_id} disappeared")
        if record.get("sample_index") != expected_index:
            raise GoldSampleError("frozen inventory sample order drifted")
        if record.get("sample_group") != "inventory_60":
            raise GoldSampleError("frozen inventory sample group drifted")
        exact_identity = {
            "source": row.source,
            "url": row.url,
            "title": row.title,
            "ingested_symbol": row.ingested_symbol,
            "published_at": _iso_utc(row.published_at),
            "available_time": _iso_utc(row.available_time),
            "content_hash": row.content_hash,
        }
        for field, expected in exact_identity.items():
            if record.get(field) != expected:
                raise GoldSampleError(
                    f"frozen inventory news item {news_item_id} changed field {field}"
                )
        stratum = record.get("stratum")
        if not isinstance(stratum, Mapping):
            raise GoldSampleError(f"frozen inventory news item {news_item_id} has no stratum")
        key = (str(row.source), row.symbol_state)
        if stratum.get("source") != key[0] or stratum.get("symbol_state") != key[1]:
            raise GoldSampleError(f"frozen inventory news item {news_item_id} stratum drifted")
        observed_counts[key] = observed_counts.get(key, 0) + 1
    if observed_counts != expected_counts:
        raise GoldSampleError(
            "frozen inventory quotas drifted: "
            f"expected {expected_counts}, observed {observed_counts}"
        )


def _ready_after(contract: FrozenContract) -> datetime:
    gold = _mapping(contract.document["gold_sample"], label="gold_sample")
    future = _mapping(gold["future_40"], label="gold_sample.future_40")
    ready = _parse_datetime(future["ready_after"], label="gold_sample.future_40.ready_after")
    assert ready is not None
    return ready


def require_future_ready(contract: FrozenContract, now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("future readiness time must be timezone-aware")
    ready = _ready_after(contract)
    if now.astimezone(UTC) < ready:
        raise GoldSampleNotReady(
            f"future gold sample is not ready before {ready.astimezone(SHANGHAI).isoformat()}"
        )


def _manifest_strata(records: Sequence[Mapping[str, object]]) -> list[JsonObject]:
    grouped: dict[tuple[str, str, str], list[Mapping[str, object]]] = {}
    for record in records:
        stratum = record.get("stratum")
        if not isinstance(stratum, Mapping):
            raise GoldSampleError("sample record has no stratum")
        group = str(record.get("sample_group"))
        source = str(stratum.get("source"))
        symbol_state = str(stratum.get("symbol_state"))
        grouped.setdefault((group, source, symbol_state), []).append(record)
    return [
        {
            "group": group,
            "source": source,
            "symbol_state": symbol_state,
            "count": len(items),
            "news_item_ids": [item["news_item_id"] for item in items],
        }
        for (group, source, symbol_state), items in grouped.items()
    ]


def _manifest(
    *,
    contract: FrozenContract,
    inventory_records: Sequence[JsonObject],
    all_records: Sequence[JsonObject],
    inventory_bytes: bytes,
    final_bytes: bytes,
    inventory_snapshot: JsonObject,
    final_snapshot: JsonObject,
    generated_at: datetime,
    database_path: Path,
) -> JsonObject:
    artifact_root, inventory_path, final_path, manifest_path = _artifact_paths(contract)
    contract_files = _mapping(contract.document["contract_files"], label="contract_files")
    pdf_records = [
        {
            "news_item_id": record["news_item_id"],
            "url": record["url"],
            "pdf_sha256": _mapping(record["body_evidence"], label="body_evidence")["pdf_sha256"],
            "full_text_sha256": _mapping(record["body_evidence"], label="body_evidence")[
                "full_text_sha256"
            ],
            "full_text_character_count": _mapping(record["body_evidence"], label="body_evidence")[
                "full_text_character_count"
            ],
        }
        for record in all_records
        if _mapping(record["body_evidence"], label="body_evidence")["required"] is True
    ]
    frozen_items = []
    for record in all_records:
        identity = {field: record[field] for field in FROZEN_MANIFEST_DIRECT_FIELDS}
        identity["body_evidence_sha256"] = canonical_json_sha256(record["body_evidence"])
        frozen_items.append(identity)
    return {
        "schema_version": "p4.2a-gold-annotation-manifest-v1",
        "sample_version": "p4.2a-gold-v1",
        "generated_at_utc": _iso_utc(generated_at),
        "contract": {
            "path": str(contract.path.relative_to(PROJECT_DIR)),
            "sha256": contract.sha256,
            "owner_spec_commit": contract.document["owner_spec_commit"],
            "deterministic_seed": _mapping(contract.document["gold_sample"], label="gold_sample")[
                "deterministic_seed"
            ],
            "rank_input_format": "seed|group|source|symbol_state|content_hash|id",
            "prompt_sha256": _mapping(contract_files["prompt"], label="contract_files.prompt")[
                "sha256"
            ],
            "schema_sha256": _mapping(contract_files["schema"], label="contract_files.schema")[
                "sha256"
            ],
        },
        "database_snapshot": {
            "path": (
                str(database_path.resolve().relative_to(PROJECT_DIR))
                if database_path.resolve().is_relative_to(PROJECT_DIR)
                else str(database_path.resolve())
            ),
            "configured_production_path": _mapping(contract.document["input"], label="input")[
                "production_database"
            ],
            "open_mode": "mode=ro + PRAGMA query_only=ON",
            "inventory": inventory_snapshot,
            "final": final_snapshot,
        },
        "artifacts": {
            "artifact_root": str(artifact_root.relative_to(PROJECT_DIR)),
            "inventory_jsonl": {
                "path": str(inventory_path.relative_to(PROJECT_DIR)),
                "sha256": _sha256_bytes(inventory_bytes),
                "row_count": len(inventory_records),
            },
            "final_jsonl": {
                "path": str(final_path.relative_to(PROJECT_DIR)),
                "sha256": _sha256_bytes(final_bytes),
                "sha256_at_creation_before_owner_labels": _sha256_bytes(final_bytes),
                "row_count": len(all_records),
            },
            "manifest_json": {"path": str(manifest_path.relative_to(PROJECT_DIR))},
        },
        "frozen_news_item_ids": [record["news_item_id"] for record in all_records],
        "frozen_items": frozen_items,
        "strata": _manifest_strata(all_records),
        "announcement_body": {
            "selected_count": len(pdf_records),
            "evidence": pdf_records,
            "failures": [],
            "failure_policy": "block_sample_without_replacement",
            "pdfs_persisted": False,
        },
        "blind_at_creation": {
            "annotation_status": "pending",
            "gold_fields_null": True,
            "model_predictions_absent": True,
        },
        "no_substitution_after_id_freeze": True,
    }


def build_future_sample(
    contract_path: Path = DEFAULT_CONFIG,
    database_path: Path | None = None,
    *,
    now: datetime | None = None,
    pdf_fetcher: PdfFetcher = download_cninfo_pdf,
    pdf_text_extractor: PdfTextExtractor = extract_cninfo_pdf_text,
) -> JsonObject:
    """Append the two daily strata to frozen IDs and create final100 + manifest once."""

    contract = load_contract(contract_path)
    current_time = now or datetime.now(UTC)
    require_future_ready(contract, current_time)
    artifact_root, inventory_path, final_path, manifest_path = _artifact_paths(contract)
    final_output = _artifact_path(final_path, artifact_root)
    manifest_output = _artifact_path(manifest_path, artifact_root)
    if not inventory_path.is_file() or inventory_path.is_symlink():
        raise GoldSampleError("frozen inventory60 artifact must exist before future mode")
    inventory_bytes = inventory_path.read_bytes()
    inventory_records = _load_jsonl(inventory_path)

    gold = _mapping(contract.document["gold_sample"], label="gold_sample")
    inventory_contract = _mapping(gold["inventory_60"], label="gold_sample.inventory_60")
    future = _mapping(gold["future_40"], label="gold_sample.future_40")
    cutoff = int(inventory_contract["cutoff_news_item_id"])
    future_strata = _contract_strata(
        future["per_date_strata"], label="gold_sample.future_40.per_date_strata"
    )
    trading_dates = [date.fromisoformat(str(value)) for value in future["trading_dates"]]
    database = _database_path(contract, database_path)
    with open_read_only_database(database) as connection:
        inventory_snapshot = inventory_database_snapshot(connection, contract)
        inventory_rows = _load_news_rows(connection, cutoff=cutoff, after_cutoff=False)
        _validate_frozen_inventory(
            inventory_records,
            contract,
            {row.news_item_id: row for row in inventory_rows},
        )
        future_rows = _load_news_rows(connection, cutoff=cutoff, after_cutoff=True)
        selected: list[SelectedNews] = []
        for trading_day in trading_dates:
            group = f"future_40:{trading_day.isoformat()}"
            selected.extend(
                select_stratified_rows(
                    future_rows,
                    strata=future_strata,
                    seed=str(gold["deterministic_seed"]),
                    group=group,
                    trading_date=trading_day,
                )
            )
        frozen_ids = {int(record["news_item_id"]) for record in inventory_records}
        if any(item.row.news_item_id in frozen_ids for item in selected):
            raise GoldSampleError("future selection attempted to replace a frozen inventory ID")
        future_records = materialize_selected_rows(
            selected,
            contract,
            starting_index=61,
            pdf_fetcher=pdf_fetcher,
            pdf_text_extractor=pdf_text_extractor,
        )
        final_snapshot = current_database_snapshot(connection)

    if len(future_records) != 40:
        raise GoldSampleError(f"future builder produced {len(future_records)} rows instead of 40")
    all_records = [*inventory_records, *future_records]
    if len({int(record["news_item_id"]) for record in all_records}) != 100:
        raise GoldSampleError("final gold sample does not contain 100 unique fixed IDs")
    final_bytes = _json_line_bytes(all_records)
    manifest = _manifest(
        contract=contract,
        inventory_records=inventory_records,
        all_records=all_records,
        inventory_bytes=inventory_bytes,
        final_bytes=final_bytes,
        inventory_snapshot=inventory_snapshot,
        final_snapshot=final_snapshot,
        generated_at=current_time.astimezone(UTC),
        database_path=database,
    )
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    final_sha256, manifest_sha256 = _write_new_final_manifest_pair(
        final_path=final_output,
        final_payload=final_bytes,
        manifest_path=manifest_output,
        manifest_payload=manifest_bytes,
        expected_manifest=manifest,
    )
    return {
        "mode": "future",
        "row_count": len(all_records),
        "future_row_count": len(future_records),
        "final_output": str(final_output.relative_to(PROJECT_DIR)),
        "final_sha256": final_sha256,
        "manifest_output": str(manifest_output.relative_to(PROJECT_DIR)),
        "manifest_sha256": manifest_sha256,
        "frozen_news_item_ids": [record["news_item_id"] for record in all_records],
    }


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the blind P4.2a owner-annotation sample from a read-only production "
            "news_items snapshot. No database write path exists."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("inventory", "heldout", "combine-owner"),
        required=True,
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--evaluation-design",
        type=Path,
        default=DEFAULT_EVALUATION_DESIGN,
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help=(
            "SQLite input override for legacy inventory only; active heldout and "
            "combine-owner modes reject it."
        ),
    )
    parser.add_argument(
        "--dev-owner-export",
        type=Path,
        default=None,
        help="Completed owner-labelled dev60 JSONL; required by combine-owner.",
    )
    parser.add_argument(
        "--heldout-owner-export",
        type=Path,
        default=None,
        help="Completed owner-labelled heldout40 JSONL; required by combine-owner.",
    )
    parser.add_argument(
        "--heldout-ai-draft",
        type=Path,
        default=None,
        help=(
            "Explicit AI-drafted heldout40 JSONL required when the owner export "
            "is a compact .adjudicated.jsonl audit artifact."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        if (
            arguments.mode in {"heldout", "combine-owner"}
            and arguments.database is not None
        ):
            raise GoldSampleError(
                f"--database override is forbidden for active {arguments.mode} mode"
            )
        if arguments.mode == "inventory":
            result = build_inventory_sample(arguments.config, arguments.database)
        elif arguments.mode == "heldout":
            result = build_heldout_owner_sample(
                arguments.evaluation_design,
                arguments.database,
                now=datetime.now(UTC),
            )
        else:
            if (
                arguments.dev_owner_export is None
                or arguments.heldout_owner_export is None
            ):
                raise GoldSampleError(
                    "combine-owner requires --dev-owner-export and "
                    "--heldout-owner-export"
                )
            result = combine_owner_annotations(
                dev_owner_export=arguments.dev_owner_export,
                heldout_owner_export=arguments.heldout_owner_export,
                heldout_ai_draft=arguments.heldout_ai_draft,
                design_path=arguments.evaluation_design,
            )
    except (FileExistsError, GoldSampleError, OSError, ValueError) as exc:
        print(f"P4.2a gold sample failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
