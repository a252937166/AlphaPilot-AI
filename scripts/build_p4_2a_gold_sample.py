from __future__ import annotations

import argparse
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

from alphapilot.llm.p4_news_event import (
    EventExtractContract,
    build_event_extract_user_input,
    event_extract_input_sha256,
    load_event_extract_contract,
)

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path("config/p4_event_extract_eval_v1.yaml")
SHANGHAI = ZoneInfo("Asia/Shanghai")
PDF_MAGIC = b"%PDF-"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SIX_DIGIT_SYMBOL = re.compile(r"^[0-9]{6}$")
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

JsonObject = dict[str, Any]


class GoldSampleError(RuntimeError):
    """The frozen gold-sample contract could not be satisfied."""


class GoldSampleNotReady(GoldSampleError):
    """The future sample is still inside its pre-registered observation window."""


@dataclass(frozen=True, slots=True)
class FrozenContract:
    path: Path
    sha256: str
    document: JsonObject


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
    document = _mapping(yaml.safe_load(raw), label="P4.2a contract")

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
        parsed: object = json.loads(value)
    except json.JSONDecodeError as exc:
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
                if declared_size < 0 or declared_size > policy.max_pdf_bytes:
                    raise GoldSampleError("CNInfo PDF exceeds the 8 MiB bound")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > policy.max_pdf_bytes:
                    raise GoldSampleError("CNInfo PDF exceeds the 8 MiB bound")
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
        raise GoldSampleError(
            "pdftotext output is shorter than the minimum extracted-character gate"
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
        raise GoldSampleError("mocked CNInfo PDF exceeds the 8 MiB bound")
    if not pdf_bytes.startswith(policy.required_magic):
        raise GoldSampleError("CNInfo response does not start with %PDF-")
    extracted = pdf_text_extractor(pdf_bytes, policy)
    if extracted.full_character_count != len(extracted.text):
        raise GoldSampleError("pdftotext full character count is inconsistent")
    if extracted.text_sha256 != _sha256_bytes(extracted.text.encode("utf-8")):
        raise GoldSampleError("pdftotext full text SHA-256 is inconsistent")
    if len(extracted.text.strip()) < policy.minimum_extracted_characters:
        raise GoldSampleError(
            "pdftotext output is shorter than the minimum extracted-character gate"
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
        value: object = json.loads(path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
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
            value: object = json.loads(line, object_pairs_hook=reject_duplicates)
        except json.JSONDecodeError as exc:
            raise GoldSampleError(f"JSONL line {line_number} is invalid") from exc
        if not isinstance(value, dict):
            raise GoldSampleError(f"JSONL line {line_number} must be an object")
        records.append(value)
    return records


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
    parser.add_argument("--mode", choices=("inventory", "future"), required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="SQLite input override; it is still opened mode=ro + query_only (test/recovery only).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        if arguments.mode == "inventory":
            result = build_inventory_sample(arguments.config, arguments.database)
        else:
            result = build_future_sample(
                arguments.config,
                arguments.database,
                now=datetime.now(UTC),
            )
    except (FileExistsError, GoldSampleError, OSError, ValueError) as exc:
        print(f"P4.2a gold sample failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
