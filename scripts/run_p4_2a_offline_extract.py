from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
from collections import Counter
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic_ns
from typing import Any, Protocol, cast

from sqlalchemy import Table, create_engine, func, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from alphapilot.core.config import Settings
from alphapilot.db.models import LLMCall
from alphapilot.llm.client import LLMUnavailable, chat_json
from alphapilot.llm.p4_news_event import (
    DEFAULT_CONTRACT_PATH,
    EventExtractContract,
    EventExtractContractError,
    EventExtractValidationError,
    build_event_extract_user_input,
    event_extract_input_sha256,
    load_event_extract_contract,
    normalize_llm_endpoint,
    validate_event_result,
    validate_materialized_event_result,
)

JsonObject = dict[str, Any]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = Path("data/alphapilot.db")
EVAL_ROOT = Path("docs/phase4/eval")
FROZEN_MAX_NEWS_ITEM_ID = 423
FROZEN_EXPECTED_COUNT = 423
EXPECTED_PURPOSE = "p4_news_event_extract"
EXPECTED_TIMEOUT_SECONDS = 20.0
EXPECTED_MAX_RETRIES = 0
EXPECTED_MAX_TOKENS = 2_000
SUPPORTED_SOURCES = frozenset({"akshare_ths", "cninfo", "sina_company_news"})
MAX_JSONL_LINE_BYTES = 1_000_000
DECLARED_INPUT_ACTIVE = "active_model_user_json"
DECLARED_INPUT_LEGACY_V1 = "legacy_eight_field_user_json_v1"


class OfflineExtractError(RuntimeError):
    """Raised when the bounded offline trial cannot run safely."""


class OfflineExtractDeadlineExceeded(TimeoutError):
    """Raised when one logical extraction exceeds the frozen wall-clock budget."""


class OfflineExtractAuditEvidenceError(RuntimeError):
    """Raised when a successful result lacks one isolated audit record."""


class ChatJsonCallable(Protocol):
    def __call__(
        self,
        purpose: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        timeout: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 1,
        settings: Settings | None = None,
        session: Session | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ExtractRecord:
    news_item_id: int
    source: str
    ingested_symbol: str | None
    title: str
    original_text: str
    published_at: str | None
    available_time: str
    body_state: str
    declared_input_sha256: str | None = None
    declared_text_sha256: str | None = None
    declared_input_representation: str = DECLARED_INPUT_ACTIVE


@dataclass(frozen=True, slots=True)
class PreparedRecord:
    record: ExtractRecord
    user_json: str
    input_sha256: str
    text_sha256: str
    declared_input_sha256: str | None


@dataclass(frozen=True, slots=True)
class ProductionDatabaseEvidence:
    relative_path: str
    sqlite_uri_mode: str
    pragma_query_only: int
    total_changes: int
    required_tables_found: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuditEvidence:
    row_count: int
    ok: bool | None
    latency_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    error: str | None


@dataclass(slots=True)
class CheckpointIndex:
    latest: dict[tuple[int, str, str, str], JsonObject]
    line_count: int = 0


@dataclass(frozen=True, slots=True)
class ExtractionSummary:
    expected_count: int
    success_count: int
    failure_count: int
    newly_attempted_count: int
    retried_failure_count: int
    skipped_exact_success_count: int
    skipped_failure_count: int
    output_line_count: int
    failures_by_reason: dict[str, int]
    failures_by_validation_field_and_constraint: dict[str, dict[str, int]]
    isolated_audit_tables: tuple[str, ...]
    isolated_audit_row_count: int
    checkpoint_audited_success_count: int


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OfflineExtractError(f"{name} must be a mapping")
    return cast(Mapping[str, Any], value)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_relative(path: Path, root: Path, name: str) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise OfflineExtractError(f"{name} escapes the project root") from exc


def _resolved_contract_path(
    project_root: Path,
    value: object,
    *,
    name: str,
    eval_root: Path,
) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise OfflineExtractError(f"{name} must be a non-blank relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise OfflineExtractError(f"{name} escapes the eval artifact root")
    candidate = (project_root / relative).resolve()
    try:
        candidate.relative_to(eval_root)
    except ValueError as exc:
        raise OfflineExtractError(f"{name} escapes the eval artifact root") from exc
    if candidate == eval_root:
        raise OfflineExtractError(f"{name} must identify a file")
    return candidate


@dataclass(frozen=True, slots=True)
class ArtifactPaths:
    eval_root: Path
    offline_output: Path
    offline_report: Path
    gold_input: Path
    gold_output: Path


def _artifact_paths(contract: EventExtractContract, project_root: Path) -> ArtifactPaths:
    if contract.document.get("artifact_root") != EVAL_ROOT.as_posix():
        raise OfflineExtractError("contract artifact_root is not docs/phase4/eval")
    eval_root = (project_root / EVAL_ROOT).resolve()
    offline = _mapping(contract.document.get("offline_trial"), "offline_trial")
    gold = _mapping(contract.document.get("gold_sample"), "gold_sample")
    paths = ArtifactPaths(
        eval_root=eval_root,
        offline_output=_resolved_contract_path(
            project_root,
            offline.get("output_jsonl"),
            name="offline_trial.output_jsonl",
            eval_root=eval_root,
        ),
        offline_report=_resolved_contract_path(
            project_root,
            offline.get("report_json"),
            name="offline_trial.report_json",
            eval_root=eval_root,
        ),
        gold_input=_resolved_contract_path(
            project_root,
            gold.get("final_output_jsonl"),
            name="gold_sample.final_output_jsonl",
            eval_root=eval_root,
        ),
        gold_output=_resolved_contract_path(
            project_root,
            gold.get("predictions_output_jsonl"),
            name="gold_sample.predictions_output_jsonl",
            eval_root=eval_root,
        ),
    )
    if (
        len(
            {
                paths.offline_output,
                paths.offline_report,
                paths.gold_input,
                paths.gold_output,
            }
        )
        != 4
    ):
        raise OfflineExtractError("contract input and output artifact paths must be distinct")
    return paths


def _ensure_safe_parent(path: Path, eval_root: Path) -> None:
    eval_root.mkdir(parents=True, exist_ok=True)
    if eval_root.is_symlink() or not eval_root.is_dir():
        raise OfflineExtractError("eval artifact root must be a regular directory")
    try:
        relative_parent = path.parent.relative_to(eval_root)
    except ValueError as exc:
        raise OfflineExtractError("artifact path escapes the eval artifact root") from exc
    current = eval_root
    for part in relative_parent.parts:
        current = current / part
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                raise OfflineExtractError("artifact parent must not traverse a symlink")
        else:
            current.mkdir()
    if path.is_symlink():
        raise OfflineExtractError("artifact path must not be a symlink")
    if path.exists() and not path.is_file():
        raise OfflineExtractError("artifact path must be a regular file")


def _resolve_settings_model(settings: Settings, purpose: str) -> str | None:
    override = settings.llm_purpose_models.get(purpose)
    if isinstance(override, str) and override.strip():
        return override.strip()
    model = settings.llm_model
    return model.strip() if isinstance(model, str) and model.strip() else None


def _validate_runtime_contract(contract: EventExtractContract, settings: Settings) -> None:
    if (
        contract.purpose != EXPECTED_PURPOSE
        or contract.timeout != EXPECTED_TIMEOUT_SECONDS
        or contract.max_tokens != EXPECTED_MAX_TOKENS
        or contract.max_retries != EXPECTED_MAX_RETRIES
        or contract.explicit_cache_enabled
    ):
        raise OfflineExtractError("frozen LLM purpose, model, or budget drifted")
    if _resolve_settings_model(settings, contract.purpose) != contract.model:
        raise OfflineExtractError(
            "Settings .env purpose model differs from the frozen contract"
        )
    raw_endpoint = (settings.llm_base_url or "").strip()
    if contract.endpoint is not None:
        try:
            resolved_endpoint = normalize_llm_endpoint(
                raw_endpoint,
                name="Settings.llm_base_url",
            )
        except EventExtractContractError as exc:
            raise OfflineExtractError(
                "Settings .env LLM endpoint is invalid"
            ) from exc
        if resolved_endpoint != contract.endpoint:
            raise OfflineExtractError(
                "Settings .env LLM endpoint differs from the frozen contract"
            )
    if not raw_endpoint or not (settings.llm_api_key or "").strip():
        raise OfflineExtractError("Settings .env does not contain a complete LLM configuration")


def _settings_from_project_env(project_root: Path) -> Settings:
    return Settings(_env_file=project_root / ".env")


def _database_path(contract: EventExtractContract, project_root: Path) -> Path:
    input_contract = _mapping(contract.document.get("input"), "input")
    if (
        input_contract.get("production_database") != DEFAULT_DATABASE.as_posix()
        or input_contract.get("open_mode") != "read_only_query_only"
    ):
        raise OfflineExtractError("production database contract drifted")
    expected = (project_root / DEFAULT_DATABASE).resolve()
    lexical = project_root / DEFAULT_DATABASE
    if lexical.is_symlink() or not expected.is_file():
        raise OfflineExtractError("production database must be one regular, non-symlink file")
    return expected


def _open_production_database(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{database.as_uri()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    query_only = connection.execute("PRAGMA query_only").fetchone()
    if query_only is None or int(query_only[0]) != 1:
        connection.close()
        raise OfflineExtractError("failed to enable SQLite query_only on production database")
    database_list = connection.execute("PRAGMA database_list").fetchall()
    main_paths = [
        Path(str(row[2])).resolve()
        for row in database_list
        if str(row[1]) == "main" and str(row[2])
    ]
    if main_paths != [database]:
        connection.close()
        raise OfflineExtractError("SQLite read-only connection resolved to an unexpected database")
    return connection


def _sqlite_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    }


def _raw_payload(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, Any], value)
    if not isinstance(value, (str, bytes)):
        return {}
    try:
        loaded: object = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return cast(Mapping[str, Any], loaded) if isinstance(loaded, Mapping) else {}


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OfflineExtractError(f"news_items.{field} must be non-blank text")
    return value


def _nullable_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise OfflineExtractError(f"news_items.{field} must be null or non-blank text")
    return value


def _text_at_contract_path(
    title: str,
    raw_payload: Mapping[str, Any],
    field_path: str,
) -> str | None:
    if field_path == "title":
        return title
    if not field_path.startswith("raw_payload."):
        raise OfflineExtractError(f"unsupported original_text field: {field_path}")
    value: object = raw_payload
    for component in field_path.split(".")[1:]:
        if not isinstance(value, Mapping):
            return None
        value = value.get(component)
    return value if isinstance(value, str) and value.strip() else None


def _offline_original_text(
    contract: EventExtractContract,
    source: str,
    title: str,
    raw_payload: Mapping[str, Any],
) -> tuple[str, str]:
    input_contract = _mapping(contract.document.get("input"), "input")
    fields_by_source = _mapping(
        input_contract.get("original_text_fields"),
        "input.original_text_fields",
    )
    fields = fields_by_source.get(source)
    if (
        not isinstance(fields, Sequence)
        or isinstance(fields, (str, bytes))
        or not fields
        or any(not isinstance(field, str) for field in fields)
    ):
        raise OfflineExtractError(f"no valid original_text field contract for {source}")

    values: list[str] = []
    contributing_fields: list[str] = []
    seen_values: set[str] = set()
    for raw_field in fields:
        field = cast(str, raw_field)
        value = _text_at_contract_path(title, raw_payload, field)
        if value is None or value in seen_values:
            continue
        seen_values.add(value)
        values.append(value)
        contributing_fields.append(field.removeprefix("raw_payload."))
    if not values:
        raise OfflineExtractError(f"news source {source} has no original_text")
    body_state = "_".join(contributing_fields)
    if body_state == "title":
        body_state = "title_only"
    return "\n".join(values), body_state


def _parse_utc_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise OfflineExtractError(f"{name} must be a non-blank timestamp")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise OfflineExtractError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _load_universe(connection: sqlite3.Connection) -> frozenset[str]:
    symbols = frozenset(
        str(row[0])
        for row in connection.execute(
            "SELECT symbol FROM securities "
            "WHERE symbol GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' "
            "ORDER BY symbol"
        )
    )
    if not symbols:
        raise OfflineExtractError("security universe is empty")
    return symbols


def _load_frozen_inventory(
    connection: sqlite3.Connection,
    contract: EventExtractContract,
) -> list[ExtractRecord]:
    input_contract = _mapping(contract.document.get("input"), "input")
    snapshot = _mapping(input_contract.get("inventory_snapshot"), "input.inventory_snapshot")
    if (
        snapshot.get("max_news_item_id") != FROZEN_MAX_NEWS_ITEM_ID
        or snapshot.get("expected_row_count") != FROZEN_EXPECTED_COUNT
    ):
        raise OfflineExtractError("frozen news inventory contract drifted")
    rows = connection.execute(
        "SELECT id, source, symbol, title, published_at, available_time, raw_payload "
        "FROM news_items WHERE id <= ? ORDER BY id",
        (FROZEN_MAX_NEWS_ITEM_ID,),
    ).fetchall()
    if len(rows) != FROZEN_EXPECTED_COUNT:
        raise OfflineExtractError(
            f"frozen inventory expected {FROZEN_EXPECTED_COUNT} rows, found {len(rows)}"
        )
    ids = [int(row["id"]) for row in rows]
    if ids != list(range(1, FROZEN_MAX_NEWS_ITEM_ID + 1)):
        raise OfflineExtractError("frozen inventory IDs must be exactly 1 through 423 in order")
    expected_max_available_time = _parse_utc_timestamp(
        snapshot.get("max_available_time_utc"),
        "input.inventory_snapshot.max_available_time_utc",
    )
    observed_max_available_time = max(
        _parse_utc_timestamp(row["available_time"], "news_items.available_time")
        for row in rows
    )
    if observed_max_available_time != expected_max_available_time:
        raise OfflineExtractError("frozen inventory max available_time drifted")

    result: list[ExtractRecord] = []
    for row in rows:
        source = _required_text(row["source"], "source")
        if source not in SUPPORTED_SOURCES:
            raise OfflineExtractError(f"unsupported source in frozen inventory: {source}")
        title = _required_text(row["title"], "title")
        original_text, body_state = _offline_original_text(
            contract,
            source,
            title,
            _raw_payload(row["raw_payload"]),
        )
        result.append(
            ExtractRecord(
                news_item_id=int(row["id"]),
                source=source,
                ingested_symbol=_nullable_text(row["symbol"], "symbol"),
                title=title,
                original_text=original_text,
                published_at=_nullable_text(row["published_at"], "published_at"),
                available_time=_required_text(row["available_time"], "available_time"),
                body_state=body_state,
            )
        )
    return result


def _read_production_inputs(
    contract: EventExtractContract,
    project_root: Path,
    *,
    include_inventory: bool,
) -> tuple[list[ExtractRecord], frozenset[str], ProductionDatabaseEvidence]:
    database = _database_path(contract, project_root)
    with _open_production_database(database) as connection:
        connection.execute("BEGIN")
        tables = _sqlite_tables(connection)
        required_tables = {"news_items", "securities"}
        if not required_tables.issubset(tables):
            raise OfflineExtractError("production database lacks required read-only tables")
        universe = _load_universe(connection)
        records = _load_frozen_inventory(connection, contract) if include_inventory else []
        query_only_row = connection.execute("PRAGMA query_only").fetchone()
        query_only = int(query_only_row[0]) if query_only_row is not None else 0
        total_changes = int(connection.total_changes)
        connection.execute("COMMIT")
    evidence = ProductionDatabaseEvidence(
        relative_path=_safe_relative(database, project_root, "production database"),
        sqlite_uri_mode="ro",
        pragma_query_only=query_only,
        total_changes=total_changes,
        required_tables_found=tuple(sorted(required_tables.intersection(tables))),
    )
    if evidence.pragma_query_only != 1 or evidence.total_changes != 0:
        raise OfflineExtractError("production database read-only safety evidence failed")
    return records, universe, evidence


def _strict_json_object(payload: bytes, name: str) -> JsonObject:
    def reject_constant(value: str) -> None:
        raise OfflineExtractError(f"{name} contains non-standard JSON constant {value}")

    def reject_duplicate(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise OfflineExtractError(f"{name} contains duplicate key {key}")
            result[key] = value
        return result

    try:
        value: object = json.loads(
            payload,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfflineExtractError(f"{name} must contain strict UTF-8 JSON objects") from exc
    if not isinstance(value, dict):
        raise OfflineExtractError(f"{name} line must be a JSON object")
    return cast(JsonObject, value)


def _gold_string(row: Mapping[str, Any], field: str, *, nullable: bool = False) -> str | None:
    value = row.get(field)
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise OfflineExtractError(f"gold input {field} must be non-blank text")
    return value


def _load_gold_records(path: Path, expected_count: int) -> list[ExtractRecord]:
    if path.is_symlink() or not path.is_file():
        raise OfflineExtractError("gold input must be one regular, non-symlink JSONL file")
    records: list[ExtractRecord] = []
    seen_ids: set[int] = set()
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if len(raw_line) > MAX_JSONL_LINE_BYTES:
                raise OfflineExtractError(f"gold input line {line_number} is too large")
            if not raw_line.endswith(b"\n"):
                raise OfflineExtractError("gold input must end every JSON object with a newline")
            if not raw_line.strip():
                raise OfflineExtractError(f"gold input line {line_number} is blank")
            row = _strict_json_object(raw_line, f"gold input line {line_number}")
            if "prediction" in row or "model_prediction" in row:
                raise OfflineExtractError("gold input must not contain model predictions")
            raw_id = row.get("news_item_id")
            if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id <= 0:
                raise OfflineExtractError("gold input news_item_id must be a positive integer")
            if raw_id in seen_ids:
                raise OfflineExtractError("gold input news_item_id values must be unique")
            seen_ids.add(raw_id)
            source = _gold_string(row, "source")
            assert source is not None
            if source not in SUPPORTED_SOURCES:
                raise OfflineExtractError(f"unsupported gold input source: {source}")
            records.append(
                ExtractRecord(
                    news_item_id=raw_id,
                    source=source,
                    ingested_symbol=_gold_string(row, "ingested_symbol", nullable=True),
                    title=cast(str, _gold_string(row, "title")),
                    original_text=cast(str, _gold_string(row, "original_text")),
                    published_at=_gold_string(row, "published_at", nullable=True),
                    available_time=cast(str, _gold_string(row, "available_time")),
                    body_state=cast(str, _gold_string(row, "body_state")),
                    declared_input_sha256=cast(str, _gold_string(row, "input_sha256")),
                    declared_text_sha256=cast(str, _gold_string(row, "text_sha256")),
                )
            )
    if len(records) != expected_count:
        raise OfflineExtractError(
            f"gold prediction mode requires exactly {expected_count} rows, found {len(records)}"
        )
    return records


def _prepare_records(
    contract: EventExtractContract,
    records: Sequence[ExtractRecord],
) -> list[PreparedRecord]:
    if len(records) > contract.max_items_per_run:
        raise OfflineExtractError("record count exceeds the frozen per-run LLM budget")
    seen_ids: set[int] = set()
    prepared: list[PreparedRecord] = []
    for record in records:
        if record.news_item_id in seen_ids:
            raise OfflineExtractError("extraction records must have unique news_item_id values")
        seen_ids.add(record.news_item_id)
        user_json = build_event_extract_user_input(
            contract,
            news_item_id=record.news_item_id,
            source=record.source,
            ingested_symbol=record.ingested_symbol,
            title=record.title,
            original_text=record.original_text,
            published_at=record.published_at,
            available_time=record.available_time,
            body_state=record.body_state,
        )
        input_sha256 = event_extract_input_sha256(user_json)
        text_sha256 = _sha256_text(record.original_text)
        if record.declared_input_representation == DECLARED_INPUT_ACTIVE:
            declared_input_sha256 = input_sha256
        elif record.declared_input_representation == DECLARED_INPUT_LEGACY_V1:
            materialized_schema = contract.materialized_schema
            if materialized_schema is None:
                raise OfflineExtractError(
                    "legacy input identity requires a materialized result schema"
                )
            legacy_contract = replace(
                contract,
                schema=materialized_schema,
                evidence_candidate_selection=False,
            )
            legacy_user_json = build_event_extract_user_input(
                legacy_contract,
                news_item_id=record.news_item_id,
                source=record.source,
                ingested_symbol=record.ingested_symbol,
                title=record.title,
                original_text=record.original_text,
                published_at=record.published_at,
                available_time=record.available_time,
                body_state=record.body_state,
            )
            declared_input_sha256 = event_extract_input_sha256(legacy_user_json)
        else:
            raise OfflineExtractError("unsupported declared input representation")
        if (
            record.declared_input_sha256 is not None
            and record.declared_input_sha256 != declared_input_sha256
        ):
            raise OfflineExtractError(
                "gold input input_sha256 does not match its declared representation"
            )
        if record.declared_text_sha256 is not None and record.declared_text_sha256 != text_sha256:
            raise OfflineExtractError("gold input text_sha256 does not match original_text")
        prepared.append(
            PreparedRecord(
                record=record,
                user_json=user_json,
                input_sha256=input_sha256,
                text_sha256=text_sha256,
                declared_input_sha256=record.declared_input_sha256,
            )
        )
    return prepared


def _checkpoint_key(
    prepared: PreparedRecord,
    contract: EventExtractContract,
) -> tuple[int, str, str, str]:
    return (
        prepared.record.news_item_id,
        prepared.input_sha256,
        contract.sha256,
        contract.model,
    )


def _row_checkpoint_key(row: Mapping[str, Any], line_number: int) -> tuple[int, str, str, str]:
    news_item_id = row.get("news_item_id")
    input_sha256 = row.get("input_sha256")
    contract_sha256 = row.get("contract_sha256")
    model = row.get("model")
    if (
        isinstance(news_item_id, bool)
        or not isinstance(news_item_id, int)
        or news_item_id <= 0
        or not isinstance(input_sha256, str)
        or len(input_sha256) != 64
        or not isinstance(contract_sha256, str)
        or len(contract_sha256) != 64
        or not isinstance(model, str)
        or not model
    ):
        raise OfflineExtractError(f"checkpoint line {line_number} has an invalid key")
    return news_item_id, input_sha256, contract_sha256, model


def _load_checkpoints(stream: Any) -> CheckpointIndex:
    stream.seek(0)
    index = CheckpointIndex(latest={})
    for line_number, raw_line in enumerate(stream, start=1):
        if len(raw_line) > MAX_JSONL_LINE_BYTES:
            raise OfflineExtractError(f"checkpoint line {line_number} is too large")
        if not raw_line.endswith(b"\n"):
            raise OfflineExtractError(
                "checkpoint has an incomplete trailing line; refusing unsafe append"
            )
        if not raw_line.strip():
            raise OfflineExtractError(f"checkpoint line {line_number} is blank")
        row = _strict_json_object(raw_line, f"checkpoint line {line_number}")
        status_value = row.get("status")
        if status_value not in {"ok", "extract_failed"}:
            raise OfflineExtractError(f"checkpoint line {line_number} has invalid status")
        key = _row_checkpoint_key(row, line_number)
        prior = index.latest.get(key)
        if prior is not None and prior.get("status") == "ok" and status_value != "ok":
            raise OfflineExtractError("checkpoint regresses an exact success to failure")
        index.latest[key] = row
        index.line_count += 1
    return index


@dataclass(slots=True)
class JsonlSink:
    file_descriptor: int
    checkpoint: CheckpointIndex

    def append(self, row: Mapping[str, Any]) -> None:
        payload = (
            json.dumps(
                dict(row),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        if len(payload) > MAX_JSONL_LINE_BYTES:
            raise OfflineExtractError("refusing to append an oversized extraction row")
        view = memoryview(payload)
        while view:
            written = os.write(self.file_descriptor, view)
            if written <= 0:
                raise OfflineExtractError("failed to append extraction checkpoint")
            view = view[written:]
        os.fsync(self.file_descriptor)
        key = _row_checkpoint_key(row, self.checkpoint.line_count + 1)
        self.checkpoint.latest[key] = dict(row)
        self.checkpoint.line_count += 1


@contextmanager
def _locked_jsonl(path: Path, eval_root: Path) -> Iterator[JsonlSink]:
    _ensure_safe_parent(path, eval_root)
    existed = path.exists()
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o644)
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            raise OfflineExtractError("checkpoint output must be a regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        with os.fdopen(os.dup(descriptor), "rb", closefd=True) as reader:
            checkpoint = _load_checkpoints(reader)
        if not existed:
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        yield JsonlSink(descriptor, checkpoint)
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@contextmanager
def _isolated_audit_session() -> Iterator[tuple[Session, Any]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    llm_call_table = cast(Table, LLMCall.__table__)
    llm_call_table.create(bind=engine)
    session = Session(engine, expire_on_commit=False)
    try:
        yield session, engine
    finally:
        session.close()
        engine.dispose()


def _latest_audit_id(session: Session) -> int:
    return int(session.scalar(select(func.max(LLMCall.id))) or 0)


def _safe_audit_error(value: str | None) -> str | None:
    if value is None:
        return None
    allowed = {
        "not_configured",
        "request_timeout",
        "http_error",
        "schema_validation_failed",
        "invalid_json",
        "invalid_response",
        "unknown_failure",
    }
    if value in allowed:
        return value
    if value.startswith("http_status_") and value.removeprefix("http_status_").isdigit():
        return value
    return "redacted_llm_failure"


def _audit_evidence(session: Session, after_id: int) -> AuditEvidence:
    rows = list(session.scalars(select(LLMCall).where(LLMCall.id > after_id).order_by(LLMCall.id)))
    if len(rows) != 1:
        return AuditEvidence(
            row_count=len(rows),
            ok=None,
            latency_ms=None,
            prompt_tokens=None,
            completion_tokens=None,
            error=None,
        )
    row = rows[0]
    return AuditEvidence(
        row_count=1,
        ok=bool(row.ok),
        latency_ms=int(row.latency_ms),
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        error=_safe_audit_error(row.error),
    )


def _safe_failure_reason(error: BaseException, audit: AuditEvidence) -> str:
    if isinstance(error, OfflineExtractDeadlineExceeded):
        return "total_deadline_exceeded"
    if isinstance(error, OfflineExtractAuditEvidenceError):
        return "audit_evidence_missing"
    if isinstance(error, EventExtractValidationError):
        return "post_validation_failed"
    if isinstance(error, EventExtractContractError):
        return "event_contract_failed"
    if isinstance(error, LLMUnavailable):
        if audit.error is not None:
            return audit.error
        message = str(error)
        if message == "LLM audit persistence failed":
            return "audit_persistence_failed"
        if message == "LLM is not configured":
            return "not_configured"
        return "llm_unavailable"
    if isinstance(error, TimeoutError):
        return "request_timeout"
    return "unexpected_failure"


def _retryable_failure(reason: str) -> bool:
    return reason in {
        "request_timeout",
        "http_error",
        "llm_unavailable",
        "audit_persistence_failed",
        "redacted_llm_failure",
        "total_deadline_exceeded",
    } or reason.startswith("http_status_5")


_SAFE_VALIDATION_FIELDS = frozenset(
    {
        "available_time",
        "body_state",
        "confidence",
        "direction",
        "evidence_candidate_id",
        "evidence_span",
        "event_type",
        "ingested_symbol",
        "materiality",
        "news_item_id",
        "original_text",
        "published_at",
        "result",
        "source",
        "summary",
        "symbols",
        "title",
        "universe_symbols",
    }
)
_SAFE_SCHEMA_VALIDATION_FIELDS = frozenset(
    {
        "confidence",
        "direction",
        "evidence_candidate_id",
        "evidence_span",
        "event_type",
        "materiality",
        "summary",
        "symbols",
    }
)
_SAFE_VALIDATION_CONSTRAINTS = frozenset(
    {
        "contains_chinese_text",
        "exact_contiguous_substring",
        "json_schema_additional_properties",
        "json_schema_constraint",
        "json_schema_enum",
        "json_schema_max_items",
        "json_schema_max_length",
        "json_schema_maximum",
        "json_schema_min_length",
        "json_schema_minimum",
        "json_schema_pattern",
        "json_schema_required",
        "json_schema_type",
        "json_schema_unique_items",
        "non_blank_string",
        "non_empty_string",
        "nullable_datetime_or_non_blank_string",
        "nullable_six_digit_symbol",
        "object",
        "original_text_or_ingested_symbol_grounding",
        "registered_candidate_id",
        "positive_integer",
        "serialized_input_character_budget",
        "six_digit_symbol_collection",
        "sorted_unique",
        "whitespace_normalized_contiguous_substring",
    }
)
_SAFE_SCHEMA_VALIDATION_CONSTRAINTS = frozenset(
    {
        "json_schema_additional_properties",
        "json_schema_constraint",
        "json_schema_enum",
        "json_schema_max_items",
        "json_schema_max_length",
        "json_schema_maximum",
        "json_schema_min_length",
        "json_schema_minimum",
        "json_schema_pattern",
        "json_schema_required",
        "json_schema_type",
        "json_schema_unique_items",
    }
)


def _safe_validation_details(error: EventExtractValidationError) -> JsonObject:
    field = error.field if error.field in _SAFE_VALIDATION_FIELDS else "result"
    constraint = (
        error.constraint
        if error.constraint in _SAFE_VALIDATION_CONSTRAINTS
        else "unknown_constraint"
    )
    return {
        "field": field,
        "constraint": constraint,
    }


def _safe_llm_schema_validation_details(error: LLMUnavailable) -> JsonObject:
    if error.reason != "schema_validation_failed":
        return {}
    field = (
        error.field
        if error.field in _SAFE_SCHEMA_VALIDATION_FIELDS
        else "result"
    )
    constraint = (
        error.constraint
        if error.constraint in _SAFE_SCHEMA_VALIDATION_CONSTRAINTS
        else "json_schema_constraint"
    )
    return {
        "field": field,
        "constraint": constraint,
    }


def _validation_failure_counts(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = {}
    for row in rows:
        failure = row.get("extract_failed")
        if not isinstance(failure, Mapping):
            continue
        field = failure.get("field")
        constraint = failure.get("constraint")
        if (
            field not in _SAFE_VALIDATION_FIELDS
            or constraint not in _SAFE_VALIDATION_CONSTRAINTS
        ):
            continue
        counts.setdefault(cast(str, field), Counter())[cast(str, constraint)] += 1
    return {
        field: dict(sorted(constraints.items()))
        for field, constraints in sorted(counts.items())
    }


def _security_record(audit: AuditEvidence) -> JsonObject:
    return {
        "credentials_persisted": False,
        "exception_detail_persisted": False,
        "llm_audit_storage": "isolated_in_memory",
        "llm_audit_status": "recorded" if audit.row_count == 1 else "not_recorded",
        "production_database_access": "sqlite_uri_mode_ro_query_only",
        "raw_prompt_persisted": False,
        "raw_transport_response_persisted": False,
        "redaction_status": "passed",
    }


def _base_output_row(
    prepared: PreparedRecord,
    contract: EventExtractContract,
    *,
    elapsed_ms: int,
    audit: AuditEvidence,
) -> JsonObject:
    row: JsonObject = {
        "schema_version": "p4.2a-offline-extract-row-v1",
        "recorded_at_utc": _utc_now(),
        "news_item_id": prepared.record.news_item_id,
        "source": prepared.record.source,
        "input_sha256": prepared.input_sha256,
        "text_sha256": prepared.text_sha256,
        "contract_sha256": contract.sha256,
        "model": contract.model,
        "latency_ms": max(0, elapsed_ms),
        "llm_audit_latency_ms": audit.latency_ms,
        "tokens": {
            "prompt_tokens": audit.prompt_tokens,
            "completion_tokens": audit.completion_tokens,
        },
        "security": _security_record(audit),
    }
    if contract.evidence_candidate_selection:
        row["declared_input_sha256"] = prepared.declared_input_sha256
    return row


def _validate_resumed_success(
    row: Mapping[str, Any],
    prepared: PreparedRecord,
    contract: EventExtractContract,
    universe_symbols: Collection[str],
) -> None:
    expected_scalars: Mapping[str, object] = {
        "schema_version": "p4.2a-offline-extract-row-v1",
        "status": "ok",
        "news_item_id": prepared.record.news_item_id,
        "source": prepared.record.source,
        "input_sha256": prepared.input_sha256,
        "text_sha256": prepared.text_sha256,
        "contract_sha256": contract.sha256,
        "model": contract.model,
    }
    if contract.evidence_candidate_selection:
        expected_scalars = {
            **expected_scalars,
            "declared_input_sha256": prepared.declared_input_sha256,
        }
    for field, expected in expected_scalars.items():
        if row.get(field) != expected:
            raise OfflineExtractError(
                f"checkpoint exact success has an invalid {field}"
            )
    recorded_at = row.get("recorded_at_utc")
    _parse_utc_timestamp(recorded_at, "checkpoint.recorded_at_utc")
    for field in ("latency_ms", "llm_audit_latency_ms"):
        value = row.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise OfflineExtractError(
                f"checkpoint exact success has an invalid {field}"
            )
        if field == "latency_ms" and value > int(contract.timeout * 1_000):
            raise OfflineExtractError(
                "checkpoint exact success exceeds the frozen total deadline"
            )
    tokens = row.get("tokens")
    if not isinstance(tokens, Mapping):
        raise OfflineExtractError("checkpoint exact success lacks token evidence")
    if set(tokens) != {"prompt_tokens", "completion_tokens"}:
        raise OfflineExtractError("checkpoint exact success token evidence drifted")
    for field in ("prompt_tokens", "completion_tokens"):
        value = tokens.get(field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise OfflineExtractError(
                f"checkpoint exact success has an invalid token count: {field}"
            )
    expected_security: Mapping[str, object] = {
        "credentials_persisted": False,
        "exception_detail_persisted": False,
        "llm_audit_storage": "isolated_in_memory",
        "llm_audit_status": "recorded",
        "production_database_access": "sqlite_uri_mode_ro_query_only",
        "raw_prompt_persisted": False,
        "raw_transport_response_persisted": False,
        "redaction_status": "passed",
    }
    security = row.get("security")
    if not isinstance(security, Mapping) or dict(security) != dict(expected_security):
        raise OfflineExtractError(
            "checkpoint exact success lacks strict in-memory audit evidence"
        )
    if "error" in row or "extract_failed" in row:
        raise OfflineExtractError("checkpoint exact success contains failure fields")
    prediction = row.get("prediction")
    if not isinstance(prediction, Mapping):
        raise OfflineExtractError("checkpoint exact success lacks a prediction object")
    validate_materialized_event_result(
        contract,
        cast(Mapping[str, Any], prediction),
        original_text=prepared.record.original_text,
        ingested_symbol=prepared.record.ingested_symbol,
        universe_symbols=universe_symbols,
    )


def extract_records(
    contract: EventExtractContract,
    records: Sequence[ExtractRecord],
    *,
    output_path: Path,
    eval_root: Path,
    universe_symbols: Collection[str],
    settings: Settings,
    retry_failures: bool,
    chat_json_fn: ChatJsonCallable | None = None,
) -> ExtractionSummary:
    """Extract a fixed record set into an append-only, resumable eval JSONL."""
    _validate_runtime_contract(contract, settings)
    prepared_records = _prepare_records(contract, records)
    llm_call = chat_json if chat_json_fn is None else chat_json_fn
    newly_attempted = 0
    retried_failures = 0
    skipped_successes = 0
    skipped_failures = 0

    with (
        _locked_jsonl(output_path, eval_root) as sink,
        _isolated_audit_session() as (audit_session, audit_engine),
    ):
        for prepared in prepared_records:
            key = _checkpoint_key(prepared, contract)
            prior = sink.checkpoint.latest.get(key)
            if prior is not None and prior.get("status") == "ok":
                _validate_resumed_success(prior, prepared, contract, universe_symbols)
                skipped_successes += 1
                continue
            if prior is not None and prior.get("status") == "extract_failed":
                if not retry_failures:
                    skipped_failures += 1
                    continue
                retried_failures += 1
            else:
                newly_attempted += 1

            audit_after_id = _latest_audit_id(audit_session)
            started = monotonic_ns()
            prediction: JsonObject | None = None
            caught: BaseException | None = None
            try:
                candidate = llm_call(
                    contract.purpose,
                    contract.prompt,
                    prepared.user_json,
                    contract.schema,
                    timeout=contract.timeout,
                    max_tokens=contract.max_tokens,
                    max_retries=contract.max_retries,
                    settings=settings,
                    session=audit_session,
                )
                prediction = validate_event_result(
                    contract,
                    candidate,
                    original_text=prepared.record.original_text,
                    ingested_symbol=prepared.record.ingested_symbol,
                    universe_symbols=universe_symbols,
                )
                elapsed_seconds = (monotonic_ns() - started) / 1_000_000_000
                if elapsed_seconds > contract.timeout:
                    raise OfflineExtractDeadlineExceeded(
                        "logical extraction exceeded the frozen total deadline"
                    )
            except BaseException as error:
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                caught = error
            elapsed_ms = (monotonic_ns() - started) // 1_000_000
            audit = _audit_evidence(audit_session, audit_after_id)
            if caught is None and (audit.row_count != 1 or audit.ok is not True):
                caught = OfflineExtractAuditEvidenceError(
                    "successful extraction lacks one successful in-memory audit row"
                )
            base = _base_output_row(
                prepared,
                contract,
                elapsed_ms=elapsed_ms,
                audit=audit,
            )
            if caught is None and prediction is not None:
                base.update(
                    {
                        "status": "ok",
                        "prediction": prediction,
                    }
                )
            else:
                assert caught is not None
                reason = _safe_failure_reason(caught, audit)
                failure: JsonObject = {
                    "reason": reason,
                    "retryable": _retryable_failure(reason),
                }
                if isinstance(caught, EventExtractValidationError):
                    failure.update(_safe_validation_details(caught))
                elif isinstance(caught, LLMUnavailable):
                    failure.update(_safe_llm_schema_validation_details(caught))
                base.update(
                    {
                        "status": "extract_failed",
                        "prediction": None,
                        "error": reason,
                        "extract_failed": failure,
                    }
                )
            sink.append(base)
            audit_session.commit()

        final_rows: list[JsonObject] = []
        for prepared in prepared_records:
            row = sink.checkpoint.latest.get(_checkpoint_key(prepared, contract))
            if row is None:
                raise OfflineExtractError("checkpoint lost a frozen extraction record")
            final_rows.append(row)
        success_count = sum(row.get("status") == "ok" for row in final_rows)
        failure_count = len(final_rows) - success_count
        failures = Counter(
            str(row.get("error") or "unknown_failure")
            for row in final_rows
            if row.get("status") == "extract_failed"
        )
        checkpoint_audited_success_count = 0
        for prepared, row in zip(prepared_records, final_rows, strict=True):
            if row.get("status") != "ok":
                continue
            _validate_resumed_success(
                row,
                prepared,
                contract,
                universe_symbols,
            )
            checkpoint_audited_success_count += 1
        audit_tables = tuple(sorted(inspect(audit_engine).get_table_names()))
        audit_row_count = int(audit_session.scalar(select(func.count()).select_from(LLMCall)) or 0)
        if audit_tables != ("llm_calls",):
            raise OfflineExtractError("isolated audit database must contain only llm_calls")
        return ExtractionSummary(
            expected_count=len(prepared_records),
            success_count=success_count,
            failure_count=failure_count,
            newly_attempted_count=newly_attempted,
            retried_failure_count=retried_failures,
            skipped_exact_success_count=skipped_successes,
            skipped_failure_count=skipped_failures,
            output_line_count=sink.checkpoint.line_count,
            failures_by_reason=dict(sorted(failures.items())),
            failures_by_validation_field_and_constraint=_validation_failure_counts(
                final_rows
            ),
            isolated_audit_tables=audit_tables,
            isolated_audit_row_count=audit_row_count,
            checkpoint_audited_success_count=checkpoint_audited_success_count,
        )


def _write_new_json(path: Path, payload: Mapping[str, Any], eval_root: Path) -> None:
    _ensure_safe_parent(path, eval_root)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite create-only report: {path}")
    encoded = (
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _offline_report(
    contract: EventExtractContract,
    paths: ArtifactPaths,
    project_root: Path,
    summary: ExtractionSummary,
    database: ProductionDatabaseEvidence,
) -> JsonObject:
    recorded_count = summary.success_count + summary.failure_count
    coverage = recorded_count / summary.expected_count if summary.expected_count else 0.0
    success_coverage = (
        summary.success_count / summary.expected_count if summary.expected_count else 0.0
    )
    database_check_passed = (
        database.sqlite_uri_mode == "ro"
        and database.pragma_query_only == 1
        and database.total_changes == 0
        and set(database.required_tables_found) == {"news_items", "securities"}
    )
    checkpoint_audit_check_passed = (
        summary.checkpoint_audited_success_count == summary.success_count
    )
    audit_check_passed = (
        summary.isolated_audit_tables == ("llm_calls",)
        and checkpoint_audit_check_passed
    )
    return {
        "schema_version": "p4.2a-offline-trial-report-v1",
        "generated_at_utc": _utc_now(),
        "trial_outcome": (
            "completed" if summary.failure_count == 0 else "completed_with_failures"
        ),
        "contract": {
            "path": _safe_relative(contract.path, project_root, "contract"),
            "sha256": contract.sha256,
            "purpose": contract.purpose,
            "model": contract.model,
            "timeout_seconds": contract.timeout,
            "max_retries": contract.max_retries,
            "max_tokens": contract.max_tokens,
        },
        "inventory": {
            "cutoff_news_item_id": FROZEN_MAX_NEWS_ITEM_ID,
            "expected_count": FROZEN_EXPECTED_COUNT,
            "processed_in_id_order": True,
            "order": "news_items.id ASC",
        },
        "coverage": {
            "recorded_count": recorded_count,
            "expected_count": summary.expected_count,
            "coverage": coverage,
            "success_count": summary.success_count,
            "success_coverage": success_coverage,
            "failure_count": summary.failure_count,
            "newly_attempted_count": summary.newly_attempted_count,
            "retried_failure_count": summary.retried_failure_count,
            "skipped_exact_success_count": summary.skipped_exact_success_count,
            "skipped_failure_count": summary.skipped_failure_count,
            "output_line_count": summary.output_line_count,
        },
        "failures": {
            "count": summary.failure_count,
            "by_safe_reason": summary.failures_by_reason,
            "by_validation_field_and_constraint": (
                summary.failures_by_validation_field_and_constraint
            ),
            "raw_exception_or_transport_payload_persisted": False,
        },
        "database_safety_table": {
            "passed": 1 if database_check_passed else 0,
            "total": 1,
            "checks": [
                {
                    "name": "production_database_read_only_query_only",
                    "passed": database_check_passed,
                    "database": database.relative_path,
                    "sqlite_uri_mode": database.sqlite_uri_mode,
                    "pragma_query_only": database.pragma_query_only,
                    "connection_total_changes": database.total_changes,
                    "required_tables_found": list(database.required_tables_found),
                }
            ],
        },
        "isolated_llm_audit": {
            "database": ":memory:",
            "created_tables": list(summary.isolated_audit_tables),
            "table_count": len(summary.isolated_audit_tables),
            "expected_table_count": 1,
            "table_check": "1/1" if audit_check_passed else "0/1",
            "current_process_llm_call_rows": summary.isolated_audit_row_count,
            "checkpoint_success_rows_with_recorded_audit": (
                summary.checkpoint_audited_success_count
            ),
            "checkpoint_success_rows": summary.success_count,
            "checkpoint_success_evidence_check": (
                f"{summary.checkpoint_audited_success_count}/{summary.success_count}"
            ),
            "production_llm_calls_written": 0,
        },
        "artifacts": {
            "output_jsonl": _safe_relative(paths.offline_output, project_root, "output"),
            "report_json": _safe_relative(paths.offline_report, project_root, "report"),
            "output_mode": "append_only_resume",
            "report_mode": "create_only_atomic",
        },
        "isolation": {
            "production_writes_allowed": False,
            "production_tables_created": [],
            "forbidden_production_table": "news_events",
            "scheduler_changed": False,
            "jobs_registry_changed": False,
            "api_routes_changed": False,
            "migrations_or_orm_models_changed": False,
            "proposals_or_orders_created": False,
            "p4_2b_unlocked": False,
        },
    }


def run_offline_extract(
    *,
    project_root: Path = PROJECT_ROOT,
    contract: EventExtractContract | None = None,
    settings: Settings | None = None,
    retry_failures: bool = False,
    finalize_report_with_failures: bool = False,
    chat_json_fn: ChatJsonCallable | None = None,
) -> ExtractionSummary:
    root = project_root.resolve()
    active_contract = contract or load_event_extract_contract(
        DEFAULT_CONTRACT_PATH,
        project_root=root,
    )
    active_settings = settings or _settings_from_project_env(root)
    paths = _artifact_paths(active_contract, root)
    _ensure_safe_parent(paths.offline_report, paths.eval_root)
    if paths.offline_report.exists() or paths.offline_report.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite create-only offline report: {paths.offline_report}"
        )
    records, universe, database = _read_production_inputs(
        active_contract,
        root,
        include_inventory=True,
    )
    summary = extract_records(
        active_contract,
        records,
        output_path=paths.offline_output,
        eval_root=paths.eval_root,
        universe_symbols=universe,
        settings=active_settings,
        retry_failures=retry_failures,
        chat_json_fn=chat_json_fn,
    )
    report = _offline_report(
        active_contract,
        paths,
        root,
        summary,
        database,
    )
    if summary.failure_count == 0 or finalize_report_with_failures:
        _write_new_json(paths.offline_report, report, paths.eval_root)
    return summary


def run_gold_predictions(
    gold_input: Path,
    *,
    project_root: Path = PROJECT_ROOT,
    contract: EventExtractContract | None = None,
    settings: Settings | None = None,
    retry_failures: bool = False,
    chat_json_fn: ChatJsonCallable | None = None,
) -> ExtractionSummary:
    root = project_root.resolve()
    active_contract = contract or load_event_extract_contract(
        DEFAULT_CONTRACT_PATH,
        project_root=root,
    )
    active_settings = settings or _settings_from_project_env(root)
    paths = _artifact_paths(active_contract, root)
    candidate = gold_input if gold_input.is_absolute() else root / gold_input
    if candidate.resolve() != paths.gold_input:
        raise OfflineExtractError(
            "--gold-input must be exactly the contract final_output_jsonl path"
        )
    evaluation = _mapping(active_contract.document.get("evaluation"), "evaluation")
    sample_count = evaluation.get("sample_count")
    if isinstance(sample_count, bool) or not isinstance(sample_count, int):
        raise OfflineExtractError("evaluation.sample_count must be an integer")
    records = _load_gold_records(paths.gold_input, sample_count)
    _, universe, database = _read_production_inputs(
        active_contract,
        root,
        include_inventory=False,
    )
    if (
        database.sqlite_uri_mode != "ro"
        or database.pragma_query_only != 1
        or database.total_changes != 0
    ):
        raise OfflineExtractError("gold prediction database safety check failed")
    return extract_records(
        active_contract,
        records,
        output_path=paths.gold_output,
        eval_root=paths.eval_root,
        universe_symbols=universe,
        settings=active_settings,
        retry_failures=retry_failures,
        chat_json_fn=chat_json_fn,
    )


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the P4.2a read-only offline event extraction trial. Production "
            "news is opened with SQLite mode=ro and PRAGMA query_only; all LLM "
            "audit rows stay in an explicit in-memory database."
        )
    )
    parser.add_argument(
        "--retry-failures",
        action="store_true",
        help="retry prior exact failed checkpoints once; successes are always skipped",
    )
    parser.add_argument(
        "--finalize-report-with-failures",
        action="store_true",
        help=(
            "write the create-only terminal report from existing checkpoints even "
            "when non-retryable failures remain; never combines with a retry"
        ),
    )
    parser.add_argument(
        "--gold-input",
        type=Path,
        help=(
            "run predictions for the contract final gold JSONL; any other input "
            "path is rejected and labels are never sent to the model"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        if arguments.finalize_report_with_failures and (
            arguments.retry_failures or arguments.gold_input is not None
        ):
            raise OfflineExtractError(
                "terminal failure finalization cannot retry or run gold predictions"
            )
        if arguments.gold_input is None:
            summary = run_offline_extract(
                retry_failures=bool(arguments.retry_failures),
                finalize_report_with_failures=bool(
                    arguments.finalize_report_with_failures
                ),
            )
            mode = "frozen_inventory"
        else:
            summary = run_gold_predictions(
                cast(Path, arguments.gold_input),
                retry_failures=bool(arguments.retry_failures),
            )
            mode = "gold_predictions"
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "offline_extract_safety_gate_failed",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "mode": mode,
                "success_count": summary.success_count,
                "failure_count": summary.failure_count,
                "skipped_exact_success_count": summary.skipped_exact_success_count,
                "skipped_failure_count": summary.skipped_failure_count,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if summary.failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
