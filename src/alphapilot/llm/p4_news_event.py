from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.llm.client import chat_json

JsonObject = dict[str, Any]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONTRACT_PATH = PROJECT_ROOT / "config/p4_event_extract_eval_v1.yaml"
EXPECTED_CONTRACT_SHA256 = (
    "b3eb24c63816043edf0ef728d8d9778cd9083d720649d6fff3ae6289bba74300"
)
V1_3_CONTRACT_SHA256 = (
    "1e465f600039a587c26e9686e82a229baf948f8db748b68a5731b23af08fefd6"
)

EXPECTED_SCHEMA_VERSION = "p4.2a-event-extract-eval-v1"
V1_3_SCHEMA_VERSION = "p4.2a-event-extract-eval-v1.3"
EXPECTED_TAXONOMY = (
    "earnings_preannounce",
    "major_contract",
    "buyback_or_holder_change",
    "regulatory_action",
    "halt_resume",
    "ma_restructure",
    "policy_sector",
    "dividend",
    "other",
)
EXPECTED_RESULT_FIELDS = (
    "symbols",
    "event_type",
    "direction",
    "materiality",
    "summary",
    "confidence",
    "evidence_span",
)
EXPECTED_FORBIDDEN_RUNTIME_CHANGES = frozenset(
    {
        "scheduler",
        "jobs_registry",
        "api_routes",
        "migrations",
        "orm_models",
        "p4_news_poll_config",
        "p4_1_acceptance",
    }
)

_PROMPT_PATH = "config/prompts/p4_news_event_extract_v1.txt"
_V1_3_PROMPT_PATH = "config/prompts/p4_news_event_extract_v1_2.txt"
_SCHEMA_PATH = "config/schemas/p4_news_event_v1.schema.json"
_ARTIFACT_ROOT = "docs/phase4/eval"
_SYMBOL = re.compile(r"^[0-9]{6}$")
_SYMBOL_IN_TEXT = re.compile(r"(?<!\d)([0-9]{6})(?!\d)")
_CHINESE = re.compile(r"[\u3400-\u9fff]")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")


class EventExtractContractError(ValueError):
    """Raised when the frozen P4.2a extraction contract cannot be trusted."""


class EventExtractValidationError(ValueError):
    """Raised with safe structured metadata for one contract violation."""

    def __init__(self, message: str, *, field: str, constraint: str) -> None:
        super().__init__(message)
        self.field = field
        self.constraint = constraint


# Descriptive aliases make the exception boundary discoverable to callers that
# use the phase-specific module name.
P4NewsEventContractError = EventExtractContractError
P4NewsEventValidationError = EventExtractValidationError


@dataclass(frozen=True, slots=True)
class EventExtractContract:
    path: Path
    sha256: str
    document: JsonObject
    prompt: str
    schema: JsonObject
    model: str
    endpoint: str | None
    purpose: str
    timeout: float
    max_tokens: int
    max_retries: int
    max_items_per_run: int
    max_input_characters: int
    explicit_cache_enabled: bool


P4NewsEventContract = EventExtractContract


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def event_extract_input_sha256(user_json: str) -> str:
    """Hash the exact canonical eight-field user message sent to the model."""
    return _sha256_bytes(user_json.encode("utf-8"))


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EventExtractContractError(f"{name} must be a mapping")
    return cast(Mapping[str, Any], value)


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EventExtractContractError(f"{name} must be a positive integer")
    return value


def _positive_number(value: object, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise EventExtractContractError(f"{name} must be greater than zero")
    return float(value)


def normalize_llm_endpoint(value: object, *, name: str = "llm.endpoint") -> str:
    """Return a canonical HTTPS OpenAI-compatible base URL or fail closed."""
    if not isinstance(value, str) or not value.strip():
        raise EventExtractContractError(f"{name} must be a non-blank HTTPS URL")
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/compatible-mode/v1"
    ):
        raise EventExtractContractError(
            f"{name} must be an HTTPS /compatible-mode/v1 base URL without credentials, "
            "port, query, or fragment"
        )
    normalized = f"https://{parsed.hostname.lower()}/compatible-mode/v1"
    return normalized


def _decode_json_object(payload: bytes, name: str) -> JsonObject:
    def reject_constant(value: str) -> None:
        raise EventExtractContractError(f"{name} contains non-standard JSON: {value}")

    def reject_duplicate(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise EventExtractContractError(f"{name} contains duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        loaded: object = json.loads(
            payload,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EventExtractContractError(f"{name} must be strict UTF-8 JSON") from exc
    if not isinstance(loaded, dict):
        raise EventExtractContractError(f"{name} root must be an object")
    return cast(JsonObject, loaded)


def _contract_artifact(
    *,
    root: Path,
    entry: object,
    expected_relative_path: str,
    name: str,
) -> bytes:
    artifact = _mapping(entry, f"contract_files.{name}")
    relative_value = artifact.get("path")
    expected_sha256 = artifact.get("sha256")
    if relative_value != expected_relative_path:
        raise EventExtractContractError(f"{name} contract path drifted")
    if not isinstance(expected_sha256, str) or _SHA256.fullmatch(expected_sha256) is None:
        raise EventExtractContractError(f"{name} contract SHA-256 is invalid")

    relative_path = Path(expected_relative_path)
    resolved_root = root.resolve()
    resolved_path = (resolved_root / relative_path).resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise EventExtractContractError(f"{name} contract path escapes the project root")
    try:
        payload = resolved_path.read_bytes()
    except OSError as exc:
        raise EventExtractContractError(f"{name} contract artifact is unavailable") from exc
    if _sha256_bytes(payload) != expected_sha256:
        raise EventExtractContractError(f"{name} bytes differ from the contract SHA-256")
    return payload


def _validate_result_schema(schema: JsonObject, taxonomy: tuple[str, ...]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise EventExtractContractError("event result JSON Schema is invalid") from exc

    properties = _mapping(schema.get("properties"), "schema.properties")
    required = schema.get("required")
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or not isinstance(required, list)
        or set(required) != set(EXPECTED_RESULT_FIELDS)
        or len(required) != len(EXPECTED_RESULT_FIELDS)
        or set(properties) != set(EXPECTED_RESULT_FIELDS)
    ):
        raise EventExtractContractError("event result schema is not strict")

    event_type = _mapping(properties.get("event_type"), "schema.properties.event_type")
    enum = event_type.get("enum")
    if not isinstance(enum, list) or tuple(enum) != taxonomy:
        raise EventExtractContractError("taxonomy and event_type schema enum differ")

    symbols = _mapping(properties.get("symbols"), "schema.properties.symbols")
    symbol_items = _mapping(symbols.get("items"), "schema.properties.symbols.items")
    if (
        symbols.get("type") != "array"
        or symbols.get("uniqueItems") is not True
        or symbols.get("maxItems") != 12
        or symbol_items.get("type") != "string"
        or symbol_items.get("pattern") != "^[0-9]{6}$"
    ):
        raise EventExtractContractError("symbols schema constraints drifted")

    direction = _mapping(properties.get("direction"), "schema.properties.direction")
    materiality = _mapping(properties.get("materiality"), "schema.properties.materiality")
    confidence = _mapping(properties.get("confidence"), "schema.properties.confidence")
    if direction.get("type") != "integer" or direction.get("enum") != [-1, 0, 1]:
        raise EventExtractContractError("direction schema constraints drifted")
    if (
        materiality.get("type") != "integer"
        or materiality.get("minimum") != 0
        or materiality.get("maximum") != 3
    ):
        raise EventExtractContractError("materiality schema constraints drifted")
    if (
        confidence.get("type") != "number"
        or confidence.get("minimum") != 0
        or confidence.get("maximum") != 1
    ):
        raise EventExtractContractError("confidence schema constraints drifted")

    for field, maximum in (("summary", 240), ("evidence_span", 500)):
        field_schema = _mapping(properties.get(field), f"schema.properties.{field}")
        if (
            field_schema.get("type") != "string"
            or field_schema.get("minLength") != 1
            or field_schema.get("maxLength") != maximum
        ):
            raise EventExtractContractError(f"{field} schema constraints drifted")


def _validate_budget_and_isolation(document: Mapping[str, Any]) -> tuple[
    str,
    str,
    str | None,
    float,
    int,
    int,
    int,
    int,
    bool,
]:
    if document.get("production_writes_allowed") is not False:
        raise EventExtractContractError("P4.2a production writes must remain forbidden")
    if document.get("artifact_root") != _ARTIFACT_ROOT:
        raise EventExtractContractError("P4.2a artifact root drifted")

    llm = _mapping(document.get("llm"), "llm")
    purpose = llm.get("purpose")
    model = llm.get("model")
    if (
        purpose != "p4_news_event_extract"
        or not isinstance(model, str)
        or _MODEL_NAME.fullmatch(model) is None
    ):
        raise EventExtractContractError("P4.2a purpose/model contract drifted")
    endpoint_raw = llm.get("endpoint")
    endpoint = (
        None
        if endpoint_raw is None
        else normalize_llm_endpoint(endpoint_raw, name="llm.endpoint")
    )
    if endpoint_raw is not None and endpoint != endpoint_raw:
        raise EventExtractContractError("P4.2a endpoint must use canonical URL bytes")
    explicit_cache_raw = llm.get("explicit_cache")
    explicit_cache_enabled = False
    if explicit_cache_raw is not None:
        explicit_cache = _mapping(explicit_cache_raw, "llm.explicit_cache")
        if (
            set(explicit_cache) != {"enabled", "cache_control"}
            or explicit_cache.get("enabled") is not False
            or explicit_cache.get("cache_control") is not None
        ):
            raise EventExtractContractError(
                "P4.2a explicit cache must remain disabled for this contract"
            )
    if (
        llm.get("temperature") != 0.2
        or llm.get("enable_thinking") is not False
        or llm.get("response_format") != "json_object"
    ):
        raise EventExtractContractError("P4.2a deterministic request settings drifted")
    max_tokens = _positive_int(llm.get("max_output_tokens"), "llm.max_output_tokens")
    timeout = _positive_number(
        llm.get("total_deadline_seconds"),
        "llm.total_deadline_seconds",
    )
    max_items = _positive_int(llm.get("max_items_per_run"), "llm.max_items_per_run")
    max_retries = llm.get("max_retries")
    if (
        max_tokens > 2_000
        or timeout > 20.0
        or max_items > 2_000
        or isinstance(max_retries, bool)
        or not isinstance(max_retries, int)
        or max_retries != 0
    ):
        raise EventExtractContractError("P4.2a LLM budget was weakened")

    input_contract = _mapping(document.get("input"), "input")
    max_input_characters = _positive_int(
        input_contract.get("max_llm_input_characters"),
        "input.max_llm_input_characters",
    )
    if (
        input_contract.get("production_database") != "data/alphapilot.db"
        or input_contract.get("open_mode") != "read_only_query_only"
        or max_input_characters > 16_000
        or input_contract.get("evidence_span_must_be_contiguous_substring") is not True
        or input_contract.get("symbols_must_be_sorted_unique") is not True
        or input_contract.get(
            "symbols_must_be_in_original_text_and_security_universe_or_ingested_symbol"
        )
        is not True
    ):
        raise EventExtractContractError("P4.2a read-only input contract drifted")

    isolation = _mapping(document.get("isolation"), "isolation")
    forbidden_tables = isolation.get("forbidden_production_tables")
    forbidden_runtime = isolation.get("forbidden_runtime_changes")
    valid_forbidden_runtime = (
        isinstance(forbidden_runtime, list)
        and all(isinstance(item, str) for item in forbidden_runtime)
        and len(forbidden_runtime) == len(EXPECTED_FORBIDDEN_RUNTIME_CHANGES)
        and frozenset(forbidden_runtime) == EXPECTED_FORBIDDEN_RUNTIME_CHANGES
    )
    if (
        forbidden_tables != ["news_events"]
        or not valid_forbidden_runtime
        or isolation.get("p4_2b_unlocked") is not False
        or isolation.get("proposals_or_orders_allowed") is not False
    ):
        raise EventExtractContractError("P4.2a isolation contract was weakened")

    offline_trial = _mapping(document.get("offline_trial"), "offline_trial")
    gold_sample = _mapping(document.get("gold_sample"), "gold_sample")
    artifact_paths = (
        offline_trial.get("output_jsonl"),
        offline_trial.get("report_json"),
        gold_sample.get("inventory_output_jsonl"),
        gold_sample.get("final_output_jsonl"),
        gold_sample.get("predictions_output_jsonl"),
        gold_sample.get("manifest_json"),
    )
    expected_prefix = f"{_ARTIFACT_ROOT}/"
    if any(
        not isinstance(path, str)
        or not path.startswith(expected_prefix)
        or ".." in Path(path).parts
        for path in artifact_paths
    ):
        raise EventExtractContractError("P4.2a output path escapes the eval artifact root")

    announcement = _mapping(document.get("announcement_body"), "announcement_body")
    max_annotation_characters = _positive_int(
        announcement.get("max_annotation_text_characters"),
        "announcement_body.max_annotation_text_characters",
    )
    if (
        announcement.get("eval_only") is not True
        or announcement.get("persist_pdf") is not False
        or announcement.get("allowed_scheme") != "https"
        or announcement.get("allowed_host") != "static.cninfo.com.cn"
        or max_annotation_characters > 14_000
        or max_annotation_characters > max_input_characters
    ):
        raise EventExtractContractError("P4.2a announcement isolation contract drifted")

    return (
        cast(str, purpose),
        model,
        endpoint,
        timeout,
        max_tokens,
        max_retries,
        max_items,
        max_input_characters,
        explicit_cache_enabled,
    )


def validate_event_extract_contract_controls(
    document: Mapping[str, Any],
) -> tuple[str, str, str | None, float, int, int, int, int, bool]:
    """Validate and return the contract-controlled runtime settings."""
    return _validate_budget_and_isolation(document)


def load_event_extract_contract(
    path: Path = DEFAULT_CONTRACT_PATH,
    *,
    project_root: Path = PROJECT_ROOT,
) -> EventExtractContract:
    """Load the byte-frozen P4.2a prompt/schema contract and fail on drift."""
    resolved_path = path.resolve()
    try:
        payload = resolved_path.read_bytes()
    except OSError as exc:
        raise EventExtractContractError("P4.2a event-extract contract is unavailable") from exc
    digest = _sha256_bytes(payload)
    v1_3_path = (project_root.resolve() / "config/p4_event_extract_eval_v1_3.yaml").resolve()
    is_v1_3 = resolved_path == v1_3_path
    expected_digest = V1_3_CONTRACT_SHA256 if is_v1_3 else EXPECTED_CONTRACT_SHA256
    expected_schema_version = (
        V1_3_SCHEMA_VERSION if is_v1_3 else EXPECTED_SCHEMA_VERSION
    )
    expected_prompt_path = _V1_3_PROMPT_PATH if is_v1_3 else _PROMPT_PATH
    expected_prompt_marker = (
        "[P4_NEWS_EVENT_EXTRACT v1.2.0]"
        if is_v1_3
        else "[P4_NEWS_EVENT_EXTRACT v1.0.0]"
    )
    if digest != expected_digest:
        raise EventExtractContractError(
            "P4.2a event-extract contract bytes differ from the pre-registered SHA-256"
        )

    try:
        loaded: object = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise EventExtractContractError("P4.2a event-extract contract is invalid YAML") from exc
    if not isinstance(loaded, dict):
        raise EventExtractContractError("P4.2a event-extract contract must be a mapping")
    document = cast(JsonObject, loaded)
    if document.get("schema_version") != expected_schema_version:
        raise EventExtractContractError("unsupported P4.2a event-extract contract version")

    taxonomy = _mapping(document.get("taxonomy"), "taxonomy")
    values = taxonomy.get("values")
    if taxonomy.get("version") != "p4-news-event-taxonomy-v1" or not isinstance(values, list):
        raise EventExtractContractError("P4.2a taxonomy contract is invalid")
    taxonomy_values = tuple(values)
    if taxonomy_values != EXPECTED_TAXONOMY:
        raise EventExtractContractError("P4.2a taxonomy values drifted")

    contract_files = _mapping(document.get("contract_files"), "contract_files")
    prompt_payload = _contract_artifact(
        root=project_root,
        entry=contract_files.get("prompt"),
        expected_relative_path=expected_prompt_path,
        name="prompt",
    )
    schema_payload = _contract_artifact(
        root=project_root,
        entry=contract_files.get("schema"),
        expected_relative_path=_SCHEMA_PATH,
        name="schema",
    )
    try:
        prompt = prompt_payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EventExtractContractError("prompt contract must be UTF-8") from exc
    if not prompt.strip() or expected_prompt_marker not in prompt:
        raise EventExtractContractError("prompt contract version is invalid")

    schema = _decode_json_object(schema_payload, "event result schema")
    _validate_result_schema(schema, taxonomy_values)
    (
        purpose,
        model,
        endpoint,
        timeout,
        max_tokens,
        max_retries,
        max_items,
        max_input_characters,
        explicit_cache_enabled,
    ) = _validate_budget_and_isolation(document)

    return EventExtractContract(
        path=resolved_path,
        sha256=digest,
        document=document,
        prompt=prompt,
        schema=schema,
        model=model,
        endpoint=endpoint,
        purpose=purpose,
        timeout=timeout,
        max_tokens=max_tokens,
        max_retries=max_retries,
        max_items_per_run=max_items,
        max_input_characters=max_input_characters,
        explicit_cache_enabled=explicit_cache_enabled,
    )


load_p4_news_event_contract = load_event_extract_contract


def _timestamp_json(value: datetime | str | None, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value
    raise EventExtractValidationError(
        f"{field} must be a datetime, non-blank string, or null",
        field=field,
        constraint="nullable_datetime_or_non_blank_string",
    )


def _ingested_symbol(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SYMBOL.fullmatch(value) is None:
        raise EventExtractValidationError(
            "ingested_symbol must be null or a six-digit symbol",
            field="ingested_symbol",
            constraint="nullable_six_digit_symbol",
        )
    return value


def build_event_extract_user_input(
    contract: EventExtractContract,
    *,
    news_item_id: int,
    source: str,
    ingested_symbol: str | None,
    title: str,
    original_text: str,
    published_at: datetime | str | None,
    available_time: datetime | str,
    body_state: str,
) -> str:
    """Serialize one news row as deterministic untrusted JSON model input."""
    if isinstance(news_item_id, bool) or not isinstance(news_item_id, int) or news_item_id <= 0:
        raise EventExtractValidationError(
            "news_item_id must be a positive integer",
            field="news_item_id",
            constraint="positive_integer",
        )
    if not isinstance(source, str) or not source.strip():
        raise EventExtractValidationError(
            "source must be a non-blank string",
            field="source",
            constraint="non_blank_string",
        )
    if not isinstance(title, str) or not title.strip():
        raise EventExtractValidationError(
            "title must be a non-blank string",
            field="title",
            constraint="non_blank_string",
        )
    if not isinstance(original_text, str) or not original_text.strip():
        raise EventExtractValidationError(
            "original_text must be a non-blank string",
            field="original_text",
            constraint="non_blank_string",
        )
    if not isinstance(body_state, str) or not body_state.strip():
        raise EventExtractValidationError(
            "body_state must be a non-blank string",
            field="body_state",
            constraint="non_blank_string",
        )

    payload = {
        "available_time": _timestamp_json(available_time, "available_time"),
        "body_state": body_state,
        "ingested_symbol": _ingested_symbol(ingested_symbol),
        "news_item_id": news_item_id,
        "original_text": original_text,
        "published_at": _timestamp_json(published_at, "published_at"),
        "source": source,
        "title": title,
    }
    user_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(user_json) > contract.max_input_characters:
        raise EventExtractValidationError(
            "serialized model input exceeds the contract budget",
            field="result",
            constraint="serialized_input_character_budget",
        )
    return user_json


build_p4_news_event_user_input = build_event_extract_user_input


def _allowed_symbols(
    universe_symbols: Collection[str],
    ingested_symbol: str | None,
    original_text: str,
) -> set[str]:
    if isinstance(universe_symbols, (str, bytes)):
        raise EventExtractValidationError(
            "universe_symbols must be a symbol collection",
            field="universe_symbols",
            constraint="six_digit_symbol_collection",
        )
    universe: set[str] = set()
    for symbol in universe_symbols:
        if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
            raise EventExtractValidationError(
                "universe_symbols must contain only six-digit symbols",
                field="universe_symbols",
                constraint="six_digit_symbol_collection",
            )
        universe.add(symbol)
    text_symbols = {match.group(1) for match in _SYMBOL_IN_TEXT.finditer(original_text)}
    allowed = universe.intersection(text_symbols)
    normalized_ingested = _ingested_symbol(ingested_symbol)
    if normalized_ingested is not None:
        allowed.add(normalized_ingested)
    return allowed


def validate_event_result(
    contract: EventExtractContract,
    result: Mapping[str, Any],
    *,
    original_text: str,
    ingested_symbol: str | None,
    universe_symbols: Collection[str],
) -> JsonObject:
    """Validate schema plus P4.2a grounding constraints without a fallback."""
    if not isinstance(result, Mapping):
        raise EventExtractValidationError(
            "model result must be an object",
            field="result",
            constraint="object",
        )
    candidate = dict(result)
    try:
        Draft202012Validator(contract.schema).validate(candidate)
    except ValidationError as exc:
        path = list(exc.absolute_path)
        field = (
            str(path[0])
            if path and isinstance(path[0], str) and path[0] in EXPECTED_RESULT_FIELDS
            else "result"
        )
        validator = {
            "additionalProperties": "additional_properties",
            "enum": "enum",
            "maximum": "maximum",
            "maxItems": "max_items",
            "maxLength": "max_length",
            "minimum": "minimum",
            "minLength": "min_length",
            "pattern": "pattern",
            "required": "required",
            "type": "type",
            "uniqueItems": "unique_items",
        }.get(str(exc.validator), "constraint")
        raise EventExtractValidationError(
            "model result failed the strict JSON Schema",
            field=field,
            constraint=f"json_schema_{validator}",
        ) from exc

    if not isinstance(original_text, str) or not original_text:
        raise EventExtractValidationError(
            "original_text must be a non-empty string",
            field="original_text",
            constraint="non_empty_string",
        )
    symbols = cast(list[str], candidate["symbols"])
    if symbols != sorted(set(symbols)):
        raise EventExtractValidationError(
            "symbols must be sorted and unique",
            field="symbols",
            constraint="sorted_unique",
        )
    allowed = _allowed_symbols(universe_symbols, ingested_symbol, original_text)
    if any(symbol not in allowed for symbol in symbols):
        raise EventExtractValidationError(
            "symbols must appear as a bounded six-digit code in original_text and belong "
            "to the security universe, or equal ingested_symbol",
            field="symbols",
            constraint="original_text_or_ingested_symbol_grounding",
        )

    summary = cast(str, candidate["summary"])
    if not summary.strip() or _CHINESE.search(summary) is None:
        raise EventExtractValidationError(
            "summary must contain Chinese text",
            field="summary",
            constraint="contains_chinese_text",
        )
    evidence_span = cast(str, candidate["evidence_span"])
    if not evidence_span.strip() or evidence_span not in original_text:
        raise EventExtractValidationError(
            "evidence_span must be a contiguous substring of original_text",
            field="evidence_span",
            constraint="exact_contiguous_substring",
        )

    return {
        "symbols": list(symbols),
        "event_type": cast(str, candidate["event_type"]),
        "direction": cast(int, candidate["direction"]),
        "materiality": cast(int, candidate["materiality"]),
        "summary": summary,
        "confidence": float(cast(int | float, candidate["confidence"])),
        "evidence_span": evidence_span,
    }


validate_p4_news_event_result = validate_event_result


def _settings_model(settings: Settings, purpose: str) -> str | None:
    override = settings.llm_purpose_models.get(purpose)
    if isinstance(override, str) and override.strip():
        return override.strip()
    model = settings.llm_model
    return model.strip() if isinstance(model, str) and model.strip() else None


def _settings_endpoint(settings: Settings) -> str | None:
    value = settings.llm_base_url
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return normalize_llm_endpoint(value, name="Settings.llm_base_url")
    except EventExtractContractError:
        return None


def extract_news_event(
    contract: EventExtractContract,
    *,
    news_item_id: int,
    source: str,
    ingested_symbol: str | None,
    title: str,
    original_text: str,
    published_at: datetime | str | None,
    available_time: datetime | str,
    body_state: str,
    universe_symbols: Collection[str],
    settings: Settings,
    session: Session,
) -> JsonObject:
    """Extract and strictly validate one event using an explicit audit session."""
    if _settings_model(settings, contract.purpose) != contract.model:
        raise EventExtractContractError("resolved purpose model differs from the frozen contract")
    if contract.endpoint is not None and _settings_endpoint(settings) != contract.endpoint:
        raise EventExtractContractError("resolved LLM endpoint differs from the frozen contract")
    if contract.explicit_cache_enabled:
        raise EventExtractContractError("explicit cache is not implemented by this evaluator")
    user_json = build_event_extract_user_input(
        contract,
        news_item_id=news_item_id,
        source=source,
        ingested_symbol=ingested_symbol,
        title=title,
        original_text=original_text,
        published_at=published_at,
        available_time=available_time,
        body_state=body_state,
    )
    result = chat_json(
        contract.purpose,
        contract.prompt,
        user_json,
        contract.schema,
        timeout=contract.timeout,
        max_tokens=contract.max_tokens,
        max_retries=contract.max_retries,
        settings=settings,
        session=session,
    )
    return validate_event_result(
        contract,
        result,
        original_text=original_text,
        ingested_symbol=ingested_symbol,
        universe_symbols=universe_symbols,
    )


__all__ = [
    "DEFAULT_CONTRACT_PATH",
    "EXPECTED_CONTRACT_SHA256",
    "EXPECTED_TAXONOMY",
    "EventExtractContract",
    "EventExtractContractError",
    "EventExtractValidationError",
    "P4NewsEventContract",
    "P4NewsEventContractError",
    "P4NewsEventValidationError",
    "build_event_extract_user_input",
    "build_p4_news_event_user_input",
    "event_extract_input_sha256",
    "extract_news_event",
    "load_event_extract_contract",
    "load_p4_news_event_contract",
    "normalize_llm_endpoint",
    "validate_event_extract_contract_controls",
    "validate_event_result",
    "validate_p4_news_event_result",
]
