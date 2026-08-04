from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import yaml
from scripts.build_p4_2a_gold_sample import (
    FrozenContract,
    FrozenEvaluationDesign,
    GoldSampleError,
    NewsRow,
    PdfFetcher,
    PdfTextExtractor,
    _heldout_candidate_rows,
    download_cninfo_pdf,
    extract_cninfo_pdf_text,
    materialize_heldout_candidate_inputs,
    validate_dev_final_prediction_freeze,
    validate_heldout_candidate_inputs,
)
from scripts.run_p4_2a_offline_extract import (
    ChatJsonCallable,
    ExtractionSummary,
    ExtractRecord,
    OfflineExtractError,
    ProductionDatabaseEvidence,
    _load_universe,
    _open_production_database,
    _prepare_records,
    _settings_from_project_env,
    _sqlite_tables,
    _validate_resumed_success,
    _validate_runtime_contract,
    _validation_failure_counts,
    extract_records,
)

from alphapilot.core.config import Settings
from alphapilot.futu.client import PERMANENTLY_BLOCKED_METHODS
from alphapilot.llm.p4_news_eval import (
    DEFAULT_EVALUATION_DESIGN_PATH,
    EVALUATION_DESIGN_V1_2_PATH,
    EVALUATION_DESIGN_V1_3_PATH,
    LEGACY_EVALUATION_DESIGN_PATH,
    EventEvaluationDesign,
    EventEvaluationDesignError,
    load_event_evaluation_design,
)
from alphapilot.llm.p4_news_event import (
    EventExtractContract,
    validate_event_extract_contract_controls,
    validate_event_result,
)

JsonObject = dict[str, Any]
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_JSONL_LINE_BYTES = 1_000_000
SHA256_LENGTH = 64
ACTIVE_CONTRACT_SCHEMA = re.compile(
    r"^p4\.2a-event-extract-eval-v(?P<version>[0-9]+(?:\.[0-9]+)*)$"
)
PROMPT_VERSION_MARKER = re.compile(r"\[P4_NEWS_EVENT_EXTRACT v(?P<version>[0-9]+(?:\.[0-9]+)*)\]")
GIT_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


class HeldoutPredictionError(RuntimeError):
    """A fail-closed P4.2a held-out inference gate rejected the run."""


class HeldoutPredictionNotReady(HeldoutPredictionError):
    """The pre-registered held-out observation window is not closed."""


@dataclass(frozen=True, slots=True)
class HeldoutPredictionResult:
    summary: ExtractionSummary
    candidate_inputs_path: Path
    predictions_path: Path
    manifest_path: Path
    state_path: Path
    positive_count: int
    positive_rate: float


@dataclass(frozen=True, slots=True)
class DevFinalPredictionResult:
    summary: ExtractionSummary
    predictions_path: Path
    manifest_path: Path


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HeldoutPredictionError(f"{name} must be a mapping")
    return cast(Mapping[str, Any], value)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _normalized_version(value: str) -> tuple[int, ...]:
    parts = [int(component) for component in value.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _utc_now(now: datetime | None = None) -> str:
    value = datetime.now(UTC) if now is None else now
    if value.tzinfo is None or value.utcoffset() is None:
        raise HeldoutPredictionError("clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise HeldoutPredictionError(f"{name} must be a non-blank ISO timestamp")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HeldoutPredictionError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(_canonical_json_bytes(row) for row in rows)


def _eval_root(design: EventEvaluationDesign, project_root: Path) -> Path:
    configured = design.document.get("artifact_root")
    if configured != "docs/phase4/eval":
        raise HeldoutPredictionError("evaluation artifact root drifted")
    root = (project_root / str(configured)).resolve()
    project = project_root.resolve()
    if not root.is_relative_to(project):
        raise HeldoutPredictionError("evaluation artifact root escapes the project")
    return root


def _artifact_path(
    design: EventEvaluationDesign,
    project_root: Path,
    name: str,
) -> Path:
    artifacts = _mapping(design.document.get("artifacts"), "artifacts")
    entry = _mapping(artifacts.get(name), f"artifacts.{name}")
    raw_path = entry.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise HeldoutPredictionError(f"artifacts.{name}.path is invalid")
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise HeldoutPredictionError(f"artifacts.{name} escapes the project")
    path = (project_root.resolve() / relative).resolve()
    root = _eval_root(design, project_root)
    if path == root or not path.is_relative_to(root):
        raise HeldoutPredictionError(f"artifacts.{name} escapes the eval root")
    if entry.get("create_only") is not True:
        raise HeldoutPredictionError(f"artifacts.{name} is not create-only")
    return path


def _ensure_artifact_parent(path: Path, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise HeldoutPredictionError("eval root must be one regular directory")
    current = root
    for part in path.parent.relative_to(root).parts:
        current = current / part
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                raise HeldoutPredictionError("artifact parent traverses a symlink")
        else:
            current.mkdir()
    if path.is_symlink():
        raise HeldoutPredictionError("artifact path must not be a symlink")


def _create_only_bytes(path: Path, payload: bytes, root: Path) -> None:
    _ensure_artifact_parent(path, root)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite create-only artifact: {path}")
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
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        parent_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _load_json(path: Path, name: str) -> JsonObject:
    if path.is_symlink() or not path.is_file():
        raise HeldoutPredictionError(f"{name} must be one regular file")
    try:
        value: object = json.loads(
            path.read_bytes(),
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_nonfinite_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HeldoutPredictionError(f"{name} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise HeldoutPredictionError(f"{name} must contain one JSON object")
    return cast(JsonObject, value)


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise HeldoutPredictionError(f"JSON contains duplicate key {key}")
        result[key] = value
    return result


def _reject_nonfinite_constant(value: str) -> object:
    raise HeldoutPredictionError(f"non-finite JSON constant is forbidden: {value}")


def _load_jsonl(path: Path, name: str) -> list[JsonObject]:
    if path.is_symlink() or not path.is_file():
        raise HeldoutPredictionError(f"{name} must be one regular JSONL file")
    rows: list[JsonObject] = []
    with path.open("rb") as stream:
        for line_number, line in enumerate(stream, start=1):
            if len(line) > MAX_JSONL_LINE_BYTES:
                raise HeldoutPredictionError(f"{name} line {line_number} is oversized")
            if not line.endswith(b"\n") or not line.strip():
                raise HeldoutPredictionError(f"{name} line {line_number} is incomplete")
            try:
                value: object = json.loads(
                    line,
                    object_pairs_hook=_reject_duplicates,
                    parse_constant=_reject_nonfinite_constant,
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HeldoutPredictionError(f"{name} line {line_number} is invalid JSON") from exc
            if not isinstance(value, dict):
                raise HeldoutPredictionError(f"{name} line {line_number} is not an object")
            rows.append(cast(JsonObject, value))
    if not rows:
        raise HeldoutPredictionError(f"{name} must not be empty")
    return rows


def _validate_settings_safety_snapshot(
    snapshot: Mapping[str, Any],
) -> JsonObject:
    expected: Mapping[str, object] = {
        "trading_mode": "research",
        "live_trading_enabled": False,
        "paper_auto_trading_enabled": False,
        "futu_enable_account_mutation": False,
        "futu_enable_trade": False,
        "unlock_trade_permanently_blocked": True,
    }
    if dict(snapshot) != dict(expected):
        raise HeldoutPredictionError("research and trading safety gate is not closed")
    return dict(snapshot)


def _settings_safety(settings: Settings) -> JsonObject:
    return _validate_settings_safety_snapshot(
        {
            "trading_mode": settings.trading_mode,
            "live_trading_enabled": settings.live_trading_enabled,
            "paper_auto_trading_enabled": settings.paper_auto_trading_enabled,
            "futu_enable_account_mutation": settings.futu_enable_account_mutation,
            "futu_enable_trade": settings.futu_enable_trade,
            "unlock_trade_permanently_blocked": ("unlock_trade" in PERMANENTLY_BLOCKED_METHODS),
        }
    )


def _selection_contract(
    design: EventEvaluationDesign,
) -> tuple[datetime, datetime, datetime, int, tuple[str, ...], tuple[str, ...]]:
    splits = _mapping(design.document.get("splits"), "splits")
    heldout = _mapping(splits.get("heldout_40"), "splits.heldout_40")
    batch = _mapping(heldout.get("candidate_batch"), "candidate_batch")
    if batch.get("timezone") != "Asia/Shanghai":
        raise HeldoutPredictionError("heldout candidate timezone drifted")
    sources = batch.get("sources")
    trading_dates = batch.get("trading_dates")
    if (
        not isinstance(sources, Sequence)
        or isinstance(sources, (str, bytes))
        or not all(isinstance(item, str) for item in sources)
        or not isinstance(trading_dates, Sequence)
        or isinstance(trading_dates, (str, bytes))
        or not all(isinstance(item, str) for item in trading_dates)
    ):
        raise HeldoutPredictionError("heldout candidate source/date contract is invalid")
    min_id = batch.get("min_news_item_id_exclusive")
    if isinstance(min_id, bool) or not isinstance(min_id, int):
        raise HeldoutPredictionError("heldout candidate minimum ID is invalid")
    return (
        _parse_timestamp(batch.get("window_start_inclusive"), "window_start_inclusive"),
        _parse_timestamp(batch.get("window_end_exclusive"), "window_end_exclusive"),
        _parse_timestamp(batch.get("selection_ready_after"), "selection_ready_after"),
        min_id,
        tuple(cast(Sequence[str], sources)),
        tuple(cast(Sequence[str], trading_dates)),
    )


def _require_ready(design: EventEvaluationDesign, now: datetime | None) -> None:
    current = datetime.now(UTC) if now is None else now
    if current.tzinfo is None or current.utcoffset() is None:
        raise HeldoutPredictionError("clock must be timezone-aware")
    *_, ready_after, _, _, _ = _selection_contract(design)
    if current.astimezone(UTC) < ready_after:
        unlock_time = ready_after.astimezone(SHANGHAI).isoformat()
        raise HeldoutPredictionNotReady(
            f"heldout candidate prediction is locked until {unlock_time}"
        )


def _project_file(project_root: Path, value: object, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise HeldoutPredictionError(f"{name} path is invalid")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise HeldoutPredictionError(f"{name} path escapes the project")
    root = project_root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
        raise HeldoutPredictionError(f"{name} must be one regular project file")
    return path


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader rejecting explicit and merge-expanded duplicates."""


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


def _load_strict_yaml_mapping(payload: bytes) -> JsonObject:
    try:
        loaded: object = yaml.load(payload, Loader=_UniqueKeySafeLoader)
    except yaml.constructor.ConstructorError as exc:
        raise HeldoutPredictionError("active contract YAML contains duplicate key") from exc
    except yaml.YAMLError as exc:
        raise HeldoutPredictionError("active contract is invalid YAML") from exc
    if not isinstance(loaded, dict):
        raise HeldoutPredictionError("active contract must be a mapping")
    return cast(JsonObject, loaded)


def _load_active_contract(
    design: EventEvaluationDesign,
    project_root: Path,
    path: Path,
) -> EventExtractContract:
    """Allow a dev-only prompt revision while every non-prompt control stays frozen."""

    root = project_root.resolve()
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve()
    config_root = (root / "config").resolve()
    if not resolved.is_relative_to(config_root) or resolved.is_symlink() or not resolved.is_file():
        raise HeldoutPredictionError("active contract must be one regular config file")
    payload = resolved.read_bytes()
    prediction_contract = design.prediction_contract
    versioned_runtime_contract = (
        prediction_contract.sha256 != design.base_contract.sha256
    )
    if versioned_runtime_contract:
        registered = _mapping(
            design.document.get("active_prediction_contract"),
            "evaluation design active prediction contract",
        )
        registered_value = registered.get("path")
        if not isinstance(registered_value, str) or not registered_value.strip():
            raise HeldoutPredictionError(
                "evaluation design prediction contract path is invalid"
            )
        registered_relative = Path(registered_value)
        if registered_relative.is_absolute() or ".." in registered_relative.parts:
            raise HeldoutPredictionError(
                "evaluation design prediction contract path escapes the project"
            )
        registered_path = (root / registered_relative).resolve()
        if (
            not registered_path.is_relative_to(root)
            or resolved != registered_path
            or _sha256_bytes(payload) != prediction_contract.sha256
        ):
            raise HeldoutPredictionError(
                "active contract differs from the evaluation design prediction contract"
            )
    document = _load_strict_yaml_mapping(payload)
    schema_version = document.get("schema_version")
    owner_commit = document.get("owner_spec_commit")
    schema_match = (
        ACTIVE_CONTRACT_SCHEMA.fullmatch(schema_version)
        if isinstance(schema_version, str)
        else None
    )
    if (
        not isinstance(schema_version, str)
        or schema_match is None
        or not isinstance(owner_commit, str)
        or GIT_COMMIT.fullmatch(owner_commit) is None
    ):
        raise HeldoutPredictionError("active contract version provenance is invalid")
    _parse_timestamp(document.get("pre_registered_at"), "active contract pre_registered_at")

    contract_files = _mapping(document.get("contract_files"), "active contract files")
    prompt_entry = _mapping(contract_files.get("prompt"), "active prompt")
    prompt_path = _project_file(project_root, prompt_entry.get("path"), "active prompt")
    prompt_relative = prompt_path.relative_to(root)
    if (
        not prompt_relative.is_relative_to(Path("config/prompts"))
        or prompt_path.suffix != ".txt"
        or not _valid_sha256(prompt_entry.get("sha256"))
        or _sha256_file(prompt_path) != prompt_entry.get("sha256")
    ):
        raise HeldoutPredictionError("active prompt path or SHA-256 is invalid")
    try:
        prompt = prompt_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HeldoutPredictionError("active prompt must be UTF-8") from exc
    prompt_version = PROMPT_VERSION_MARKER.search(prompt)
    contract_version = _normalized_version(schema_match.group("version"))
    if (
        not prompt.strip()
        or prompt_version is None
        or _normalized_version(prompt_version.group("version")) > contract_version
    ):
        raise HeldoutPredictionError("active prompt version marker drifted")

    try:
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
        ) = validate_event_extract_contract_controls(document)
    except ValueError as exc:
        raise HeldoutPredictionError("active LLM controls are invalid") from exc

    # The independent design freezes all deterministic, schema, taxonomy,
    # input, budget, isolation, and artifact semantics. Prompt bytes/path may
    # differ after dev60 iteration. Contract v1.3 additionally pre-registers
    # one model/endpoint change. Contract v1.4 additionally pre-registers one
    # evidence-span comparison mode; every other control stays byte-equivalent.
    normalized_active = copy.deepcopy(document)
    normalized_base = copy.deepcopy(design.base_contract.document)
    for key in ("schema_version", "owner_spec_commit", "pre_registered_at"):
        normalized_active[key] = normalized_base[key]
    active_files = cast(JsonObject, normalized_active["contract_files"])
    base_files = cast(JsonObject, normalized_base["contract_files"])
    active_files["prompt"] = copy.deepcopy(base_files["prompt"])
    active_llm = cast(JsonObject, normalized_active["llm"])
    base_llm = cast(JsonObject, normalized_base["llm"])
    active_input = cast(JsonObject, normalized_active["input"])
    base_input = cast(JsonObject, normalized_base["input"])
    model_changed = active_llm.get("model") != base_llm.get("model")
    endpoint_present = "endpoint" in active_llm
    cache_policy_present = "explicit_cache" in active_llm
    evidence_span_match_mode = active_input.get("evidence_span_match_mode")
    if contract_version < (1, 3) and (
        model_changed or endpoint_present or cache_policy_present
    ):
        raise HeldoutPredictionError(
            "model, endpoint, or cache policy requires active contract v1.3+"
        )
    if contract_version >= (1, 3):
        if (
            not versioned_runtime_contract
            or model != prediction_contract.model
            or endpoint != prediction_contract.endpoint
            or explicit_cache_enabled
            or active_llm.get("explicit_cache")
            != {"enabled": False, "cache_control": None}
        ):
            raise HeldoutPredictionError(
                "active v1.3 model, mainland endpoint, or cache policy drifted"
            )
        active_llm["model"] = base_llm["model"]
        active_llm.pop("endpoint", None)
        active_llm.pop("explicit_cache", None)
    if contract_version < (1, 4):
        if "evidence_span_match_mode" in active_input:
            raise HeldoutPredictionError(
                "evidence-span match mode requires active contract v1.4+"
            )
    else:
        if (
            not versioned_runtime_contract
            or evidence_span_match_mode
            != prediction_contract.evidence_span_match_mode
        ):
            raise HeldoutPredictionError(
                "active v1.4 evidence-span match mode drifted"
            )
        active_input.pop("evidence_span_match_mode", None)
        if active_input != base_input:
            raise HeldoutPredictionError(
                "active v1.4 changed input fields outside evidence-span match mode"
            )
        normalized_active["input"] = copy.deepcopy(base_input)
    if normalized_active != normalized_base:
        raise HeldoutPredictionError(
            "active contract changed fields outside approved version, prompt, and "
            "v1.3/v1.4 runtime provenance"
        )

    # The unchanged schema file is re-hashed against the trusted base contract.
    schema_entry = _mapping(contract_files.get("schema"), "active result schema")
    schema_path = _project_file(
        project_root,
        schema_entry.get("path"),
        "active result schema",
    )
    base_files_document = _mapping(
        design.base_contract.document.get("contract_files"),
        "base contract files",
    )
    base_schema = _mapping(base_files_document.get("schema"), "base result schema")
    if schema_entry != base_schema or _sha256_file(schema_path) != base_schema.get("sha256"):
        raise HeldoutPredictionError("active result schema drifted")
    return replace(
        design.base_contract,
        path=resolved,
        sha256=_sha256_bytes(payload),
        document=document,
        prompt=prompt,
        model=model,
        endpoint=endpoint,
        purpose=purpose,
        timeout=timeout,
        max_tokens=max_tokens,
        max_retries=max_retries,
        max_items_per_run=max_items,
        max_input_characters=max_input_characters,
        explicit_cache_enabled=explicit_cache_enabled,
        evidence_span_match_mode=prediction_contract.evidence_span_match_mode,
    )


def _dev_prediction_identity_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    payload = b""
    prior_id = 0
    for row in rows:
        identifier = row.get("news_item_id")
        input_sha256 = row.get("input_sha256")
        text_sha256 = row.get("text_sha256")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier <= prior_id
            or not _valid_sha256(input_sha256)
            or not _valid_sha256(text_sha256)
        ):
            raise HeldoutPredictionError("dev-final prediction identity fields are invalid")
        prior_id = identifier
        payload += (
            f"{identifier}\0{str(input_sha256).lower()}\0{str(text_sha256).lower()}\n"
        ).encode("ascii")
    return _sha256_bytes(payload)


def _eval_artifact_file(project_root: Path, path: Path, name: str) -> Path:
    root = (project_root.resolve() / "docs/phase4/eval").resolve()
    candidate = path if path.is_absolute() else project_root.resolve() / path
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or resolved.is_symlink() or not resolved.is_file():
        raise HeldoutPredictionError(f"{name} must be one regular eval artifact")
    return resolved


def _validate_dev_final_predictions(
    design: EventEvaluationDesign,
    active_contract: EventExtractContract,
    project_root: Path,
    predictions_path: Path,
    manifest_path: Path,
) -> JsonObject:
    artifacts = _mapping(design.document.get("artifacts"), "artifacts")
    dev_entry = _mapping(artifacts.get("dev_60_frozen_jsonl"), "dev60 artifact")
    dev_inputs_path = _project_file(
        project_root,
        dev_entry.get("path"),
        "frozen dev60 inputs",
    )
    if _sha256_file(dev_inputs_path) != dev_entry.get("sha256"):
        raise HeldoutPredictionError("frozen dev60 input bytes drifted")
    dev_inputs = _load_jsonl(dev_inputs_path, "frozen dev60 inputs")
    if len(dev_inputs) != 60:
        raise HeldoutPredictionError("frozen dev60 inputs must contain exactly 60 rows")

    predictions_file = _eval_artifact_file(
        project_root,
        predictions_path,
        "dev-final predictions",
    )
    manifest_file = _eval_artifact_file(
        project_root,
        manifest_path,
        "dev-final prediction manifest",
    )
    expected_predictions = _artifact_path(
        design,
        project_root,
        "dev_final_predictions_jsonl",
    )
    expected_manifest_path = _artifact_path(
        design,
        project_root,
        "dev_final_predictions_manifest_json",
    )
    if predictions_file != expected_predictions or manifest_file != expected_manifest_path:
        raise HeldoutPredictionError(
            "dev-final artifacts must use the pre-registered contract paths"
        )
    if predictions_file in (manifest_file, dev_inputs_path):
        raise HeldoutPredictionError("dev-final artifact paths must be distinct")
    predictions = _load_jsonl(predictions_file, "dev-final predictions")
    if len(predictions) != 60:
        raise HeldoutPredictionError("dev-final predictions must contain exactly 60 rows")

    success_count = 0
    failure_count = 0
    dev_by_id: dict[int, JsonObject] = {}
    for dev in dev_inputs:
        identifier = dev.get("news_item_id")
        if isinstance(identifier, bool) or not isinstance(identifier, int):
            raise HeldoutPredictionError("frozen dev60 news_item_id is invalid")
        if identifier in dev_by_id:
            raise HeldoutPredictionError("frozen dev60 news_item_id is duplicated")
        dev_by_id[identifier] = dev
    prediction_ids = [row.get("news_item_id") for row in predictions]
    if prediction_ids != sorted(dev_by_id):
        raise HeldoutPredictionError(
            "dev-final predictions must be ordered by frozen dev60 news_item_id"
        )
    for prediction_row in predictions:
        identifier = cast(int, prediction_row["news_item_id"])
        dev = dev_by_id[identifier]
        if (
            prediction_row.get("news_item_id") != identifier
            or prediction_row.get("input_sha256") != dev.get("input_sha256")
            or prediction_row.get("text_sha256") != dev.get("text_sha256")
            or prediction_row.get("contract_sha256") != active_contract.sha256
            or prediction_row.get("model") != active_contract.model
        ):
            raise HeldoutPredictionError("dev-final prediction binding drifted")
        status = prediction_row.get("status")
        if status == "ok":
            candidate = _mapping(
                prediction_row.get("prediction"),
                "dev-final prediction",
            )
            original_text = dev.get("original_text")
            ingested_symbol = dev.get("ingested_symbol")
            if not isinstance(original_text, str) or not original_text:
                raise HeldoutPredictionError("frozen dev60 original_text is invalid")
            universe = set(re.findall(r"(?<!\d)[0-9]{6}(?!\d)", original_text))
            if isinstance(ingested_symbol, str):
                universe.add(ingested_symbol)
            try:
                validate_event_result(
                    active_contract,
                    candidate,
                    original_text=original_text,
                    ingested_symbol=(ingested_symbol if isinstance(ingested_symbol, str) else None),
                    universe_symbols=universe,
                )
            except Exception as exc:
                raise HeldoutPredictionError(
                    "dev-final successful prediction failed post-validation"
                ) from exc
            success_count += 1
        elif status == "extract_failed":
            security = _mapping(
                prediction_row.get("security"),
                "dev-final prediction security",
            )
            failure = _mapping(
                prediction_row.get("extract_failed"),
                "dev-final extract_failed",
            )
            reason = prediction_row.get("error")
            if (
                prediction_row.get("prediction") is not None
                or not isinstance(reason, str)
                or failure.get("reason") != reason
                or security.get("raw_prompt_persisted") is not False
                or security.get("raw_transport_response_persisted") is not False
                or security.get("exception_detail_persisted") is not False
            ):
                raise HeldoutPredictionError("dev-final failure evidence is unsafe")
            failure_count += 1
        else:
            raise HeldoutPredictionError("dev-final prediction status is invalid")

    identity_sha256 = _dev_prediction_identity_sha256(predictions)
    predictions_sha256 = _sha256_file(predictions_file)
    manifest_sha256 = _sha256_file(manifest_file)
    manifest = _load_json(manifest_file, "dev-final prediction manifest")
    expected_manifest_fields: Mapping[str, object] = {
        "design_sha256": design.sha256,
        "contract_sha256": active_contract.sha256,
        "predictions_path": predictions_file.relative_to(project_root.resolve()).as_posix(),
        "predictions_sha256": predictions_sha256,
        "row_count": 60,
        "success_count": success_count,
        "failure_count": failure_count,
        "ordered_identity_sha256": identity_sha256,
    }
    if set(manifest) != {*expected_manifest_fields, "completed_at_utc"} or any(
        manifest.get(key) != expected for key, expected in expected_manifest_fields.items()
    ):
        raise HeldoutPredictionError("dev-final prediction manifest binding drifted")
    _parse_timestamp(
        manifest.get("completed_at_utc"),
        "dev-final prediction manifest completed_at_utc",
    )
    return {
        "dev_final_predictions_path": predictions_file.relative_to(
            project_root.resolve()
        ).as_posix(),
        "dev_final_predictions_sha256": predictions_sha256,
        "dev_final_predictions_manifest_path": manifest_file.relative_to(
            project_root.resolve()
        ).as_posix(),
        "dev_final_predictions_manifest_sha256": manifest_sha256,
        "dev_final_predictions_row_count": 60,
        "dev_final_predictions_success_count": success_count,
        "dev_final_predictions_failure_count": failure_count,
        "dev_final_predictions_identity_sha256": identity_sha256,
        "dev_final_predictions_contract_sha256": active_contract.sha256,
    }


def _freeze_receipt_payload(
    design: EventEvaluationDesign,
    project_root: Path,
    active_contract: EventExtractContract,
    dev_final_evidence: Mapping[str, Any],
    *,
    now: datetime | None,
) -> JsonObject:
    contract_files = _mapping(
        active_contract.document.get("contract_files"),
        "active contract files",
    )
    prompt = _mapping(contract_files.get("prompt"), "active prompt")
    schema = _mapping(contract_files.get("schema"), "active result schema")
    contract_path = active_contract.path
    prompt_path = _project_file(project_root, prompt.get("path"), "active prompt")
    schema_path = _project_file(project_root, schema.get("path"), "active result schema")
    hashes = {
        "contract_sha256": _sha256_file(contract_path),
        "prompt_sha256": _sha256_file(prompt_path),
        "result_schema_sha256": _sha256_file(schema_path),
    }
    if (
        hashes["contract_sha256"] != active_contract.sha256
        or hashes["prompt_sha256"] != prompt.get("sha256")
        or hashes["result_schema_sha256"] != schema.get("sha256")
    ):
        raise HeldoutPredictionError("active contract, prompt, or schema bytes drifted")
    freeze = _mapping(
        design.document.get("prediction_contract_freeze"),
        "prediction_contract_freeze",
    )
    frozen_at = _utc_now(now)
    if _parse_timestamp(frozen_at, "frozen_at_utc") < _parse_timestamp(
        active_contract.document.get("pre_registered_at"),
        "active contract pre_registered_at",
    ):
        raise HeldoutPredictionError("prediction receipt predates active contract")
    receipt: JsonObject = {
        "design_schema_version": design.document.get("schema_version"),
        "design_sha256": design.sha256,
        "frozen_at_utc": frozen_at,
        "contract_path": contract_path.relative_to(project_root.resolve()).as_posix(),
        "contract_sha256": hashes["contract_sha256"],
        "contract_schema_version": active_contract.document.get("schema_version"),
        "model": active_contract.model,
        "prompt_path": prompt_path.relative_to(project_root.resolve()).as_posix(),
        "prompt_sha256": hashes["prompt_sha256"],
        "result_schema_path": schema_path.relative_to(project_root.resolve()).as_posix(),
        "result_schema_sha256": hashes["result_schema_sha256"],
        "taxonomy_version": _mapping(
            active_contract.document.get("taxonomy"),
            "active taxonomy",
        ).get("version"),
        **dict(dev_final_evidence),
    }
    required = freeze.get("required_receipt_fields")
    if isinstance(required, Sequence) and not isinstance(required, (str, bytes)):
        if "endpoint" in required:
            receipt["endpoint"] = active_contract.endpoint
        if "explicit_cache_enabled" in required:
            receipt["explicit_cache_enabled"] = active_contract.explicit_cache_enabled
        if "evidence_span_match_mode" in required:
            receipt["evidence_span_match_mode"] = (
                active_contract.evidence_span_match_mode
            )
    if (
        not isinstance(required, Sequence)
        or isinstance(required, (str, bytes))
        or set(receipt) != set(required)
    ):
        raise HeldoutPredictionError("prediction freeze receipt fields drifted")
    return receipt


def _validate_dev_model_interagreement_gate(
    design: EventEvaluationDesign,
    active_contract: EventExtractContract,
    project_root: Path,
    predictions_path: Path,
) -> JsonObject:
    """Recompute the AI-dev development gate before any prompt freeze."""

    # Local import avoids a module cycle: the dev-only runner reuses this
    # module's frozen input, active-contract, and read-only safety helpers.
    from scripts import run_p4_2a_dev_iteration as dev_iteration

    input_rows, _ = _dev_final_inputs(design, active_contract, project_root)
    labels, _ = dev_iteration._load_dev_labels(project_root, input_rows)
    predictions = _load_jsonl(predictions_path, "dev-final predictions")
    metrics = dev_iteration._score_predictions(predictions, labels)
    materiality = _mapping(
        metrics.get("materiality_positive"),
        "dev materiality model interagreement",
    )
    symbols = _mapping(
        metrics.get("symbol_exact_set"),
        "dev symbol model interagreement",
    )
    comparable = metrics.get("comparable_count")
    failures = metrics.get("active_failure_count")
    positive_agreement = materiality.get("positive_agreement")
    symbol_agreement = symbols.get("agreement")
    if (
        metrics.get("metric_semantics") != "model_interagreement"
        or metrics.get("not_phase_gate") is not True
        or metrics.get("development_ready_to_freeze") is not True
        or isinstance(comparable, bool)
        or not isinstance(comparable, int)
        or comparable != 60
        or isinstance(failures, bool)
        or not isinstance(failures, int)
        or failures != 0
        or isinstance(positive_agreement, bool)
        or not isinstance(positive_agreement, (int, float))
        or float(positive_agreement) < 0.80
        or isinstance(symbol_agreement, bool)
        or not isinstance(symbol_agreement, (int, float))
        or float(symbol_agreement) < 0.95
    ):
        raise HeldoutPredictionError(
            "dev-final model interagreement development gate did not pass"
        )
    return dict(metrics)


def freeze_prediction_contract(
    active_contract_path: Path,
    dev_final_predictions_path: Path,
    dev_final_predictions_manifest_path: Path,
    *,
    project_root: Path = PROJECT_ROOT,
    design: EventEvaluationDesign | None = None,
    now: datetime | None = None,
) -> Path:
    """Create the one immutable prompt/schema/model receipt before held-out use."""

    root = project_root.resolve()
    active_design = design or load_event_evaluation_design(
        DEFAULT_EVALUATION_DESIGN_PATH,
        project_root=root,
    )
    active_contract = _load_active_contract(
        active_design,
        root,
        active_contract_path,
    )
    dev_final_evidence = _validate_dev_final_predictions(
        active_design,
        active_contract,
        root,
        dev_final_predictions_path,
        dev_final_predictions_manifest_path,
    )
    _validate_dev_model_interagreement_gate(
        active_design,
        active_contract,
        root,
        dev_final_predictions_path,
    )
    receipt = _freeze_receipt_payload(
        active_design,
        root,
        active_contract,
        dev_final_evidence,
        now=now,
    )
    try:
        validate_dev_final_prediction_freeze(
            _frozen_design(active_design),
            active_contract=active_contract,
            receipt=receipt,
            project_root=root,
        )
    except GoldSampleError as exc:
        raise HeldoutPredictionError("authoritative dev-final freeze validation failed") from exc
    path = _artifact_path(
        active_design,
        root,
        "prediction_contract_freeze_receipt_json",
    )
    _create_only_bytes(path, _canonical_json_bytes(receipt), _eval_root(active_design, root))
    validate_prediction_contract_freeze(active_design, root)
    return path


def validate_prediction_contract_freeze(
    design: EventEvaluationDesign,
    project_root: Path,
) -> tuple[JsonObject, str, EventExtractContract]:
    """Re-hash the receipt and every active contract byte before held-out use."""

    path = _artifact_path(
        design,
        project_root,
        "prediction_contract_freeze_receipt_json",
    )
    receipt = _load_json(path, "prediction contract freeze receipt")
    required = _mapping(
        design.document.get("prediction_contract_freeze"),
        "prediction_contract_freeze",
    ).get("required_receipt_fields")
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        raise HeldoutPredictionError("prediction freeze required fields are invalid")
    if set(receipt) != set(required):
        raise HeldoutPredictionError("prediction freeze receipt fields drifted")
    _parse_timestamp(receipt.get("frozen_at_utc"), "freeze receipt frozen_at_utc")
    contract_path = _project_file(
        project_root,
        receipt.get("contract_path"),
        "receipt active contract",
    )
    active_contract = _load_active_contract(
        design,
        project_root,
        contract_path,
    )
    dev_evidence = _validate_dev_final_predictions(
        design,
        active_contract,
        project_root,
        _project_file(
            project_root,
            receipt.get("dev_final_predictions_path"),
            "receipt dev-final predictions",
        ),
        _project_file(
            project_root,
            receipt.get("dev_final_predictions_manifest_path"),
            "receipt dev-final manifest",
        ),
    )
    _validate_dev_model_interagreement_gate(
        design,
        active_contract,
        project_root,
        _project_file(
            project_root,
            receipt.get("dev_final_predictions_path"),
            "receipt dev-final predictions",
        ),
    )
    expected_without_time = _freeze_receipt_payload(
        design,
        project_root,
        active_contract,
        dev_evidence,
        now=_parse_timestamp(
            receipt.get("frozen_at_utc"),
            "freeze receipt frozen_at_utc",
        ),
    )
    for key, expected in expected_without_time.items():
        if key != "frozen_at_utc" and receipt.get(key) != expected:
            raise HeldoutPredictionError(f"prediction freeze receipt {key} drifted")
    try:
        validate_dev_final_prediction_freeze(
            _frozen_design(design),
            active_contract=active_contract,
            receipt=receipt,
            project_root=project_root.resolve(),
        )
    except GoldSampleError as exc:
        raise HeldoutPredictionError("authoritative dev-final freeze validation failed") from exc
    return receipt, _sha256_file(path), active_contract


def _dev_final_inputs(
    design: EventEvaluationDesign,
    active_contract: EventExtractContract,
    project_root: Path,
) -> tuple[tuple[JsonObject, ...], list[ExtractRecord]]:
    artifacts = _mapping(design.document.get("artifacts"), "artifacts")
    entry = _mapping(artifacts.get("dev_60_frozen_jsonl"), "dev60 artifact")
    path = _project_file(
        project_root,
        entry.get("path"),
        "frozen dev60 inputs",
    )
    if _sha256_file(path) != entry.get("sha256"):
        raise HeldoutPredictionError("frozen dev60 input bytes drifted")
    rows = _load_jsonl(path, "frozen dev60 inputs")
    if len(rows) != 60:
        raise HeldoutPredictionError("frozen dev60 inputs must contain exactly 60 rows")
    forbidden = _mapping(
        design.document.get("owner_delivery"),
        "owner_delivery",
    ).get("forbidden_fields")
    if (
        not isinstance(forbidden, Sequence)
        or isinstance(forbidden, (str, bytes))
        or not all(isinstance(item, str) for item in forbidden)
    ):
        raise HeldoutPredictionError("owner-delivery forbidden fields drifted")
    forbidden_fields = frozenset(cast(Sequence[str], forbidden))
    ordered = sorted(rows, key=lambda row: int(row.get("news_item_id", 0)))
    records: list[ExtractRecord] = []
    prior_id = 0
    for row in ordered:
        identifier = row.get("news_item_id")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier <= prior_id
        ):
            raise HeldoutPredictionError("frozen dev60 IDs are invalid or duplicated")
        prior_id = identifier
        if forbidden_fields.intersection(row):
            raise HeldoutPredictionError("frozen dev60 input leaks model or selection fields")
        source = row.get("source")
        ingested_symbol = row.get("ingested_symbol")
        title = row.get("title")
        original_text = row.get("original_text")
        published_at = row.get("published_at")
        available_time = row.get("available_time")
        body_state = row.get("body_state")
        input_sha256 = row.get("input_sha256")
        text_sha256 = row.get("text_sha256")
        if (
            not isinstance(source, str)
            or not source
            or (ingested_symbol is not None and not isinstance(ingested_symbol, str))
            or not isinstance(title, str)
            or not title
            or not isinstance(original_text, str)
            or not original_text
            or (published_at is not None and not isinstance(published_at, str))
            or not isinstance(available_time, str)
            or not available_time
            or not isinstance(body_state, str)
            or not body_state
            or not _valid_sha256(input_sha256)
            or not _valid_sha256(text_sha256)
        ):
            raise HeldoutPredictionError("frozen dev60 input fields are invalid")
        records.append(
            ExtractRecord(
                news_item_id=identifier,
                source=source,
                ingested_symbol=ingested_symbol,
                title=title,
                original_text=original_text,
                published_at=published_at,
                available_time=available_time,
                body_state=body_state,
                declared_input_sha256=cast(str, input_sha256),
                declared_text_sha256=cast(str, text_sha256),
            )
        )
    # This proves the dev artifact still hashes to the same canonical model
    # input under the explicit active prompt contract.
    _prepare_records(active_contract, records)
    return tuple(ordered), records


def _load_dev_universe(
    active_contract: EventExtractContract,
    project_root: Path,
) -> tuple[frozenset[str], ProductionDatabaseEvidence, JsonObject]:
    input_contract = _mapping(active_contract.document.get("input"), "input")
    raw_database = input_contract.get("production_database")
    if raw_database != "data/alphapilot.db":
        raise HeldoutPredictionError("production database path drifted")
    database_path = (project_root.resolve() / str(raw_database)).resolve()
    required_tables = {
        "securities",
        "trade_proposals",
        "broker_orders",
    }
    with _open_production_database(database_path) as connection:
        connection.execute("BEGIN")
        tables = _sqlite_tables(connection)
        if not required_tables.issubset(tables):
            raise HeldoutPredictionError("production database lacks dev-final safety tables")
        universe = _load_universe(connection)
        trading_safety = _database_safety(connection)
        query_only_row = connection.execute("PRAGMA query_only").fetchone()
        query_only = int(query_only_row[0]) if query_only_row is not None else 0
        total_changes = int(connection.total_changes)
        connection.execute("COMMIT")
    evidence = ProductionDatabaseEvidence(
        relative_path="data/alphapilot.db",
        sqlite_uri_mode="ro",
        pragma_query_only=query_only,
        total_changes=total_changes,
        required_tables_found=tuple(sorted(required_tables.intersection(tables))),
    )
    if query_only != 1 or total_changes != 0:
        raise HeldoutPredictionError("dev-final database read-only evidence failed")
    return universe, evidence, trading_safety


def _ensure_dev_final_precedes_heldout(
    design: EventEvaluationDesign,
    project_root: Path,
) -> None:
    names = (
        "prediction_contract_freeze_receipt_json",
        "heldout_candidate_inputs_jsonl",
        "heldout_candidate_predictions_jsonl",
        "heldout_candidate_predictions_manifest_json",
        "heldout_inference_state_jsonl",
        "heldout_selection_manifest_json",
        "heldout_40_blind_sample_jsonl",
        "heldout_evaluation_state_jsonl",
        "heldout_40_owner_annotations_jsonl",
        "combined_100_annotations_jsonl",
        "owner_completion_manifest_json",
    )
    sealed_paths: dict[Path, str] = {}
    supported_designs = [design]
    for design_path in (
        LEGACY_EVALUATION_DESIGN_PATH,
        EVALUATION_DESIGN_V1_2_PATH,
        EVALUATION_DESIGN_V1_3_PATH,
    ):
        historical = load_event_evaluation_design(
            design_path,
            project_root=PROJECT_ROOT,
        )
        if historical.sha256 != design.sha256:
            supported_designs.append(historical)
    for supported in supported_designs:
        artifacts = _mapping(supported.document.get("artifacts"), "artifacts")
        version = str(supported.document.get("schema_version", "unknown")).removeprefix(
            "p4.2a-evaluation-design-"
        )
        for name in names:
            if name not in artifacts:
                continue
            path = _artifact_path(supported, project_root, name)
            sealed_paths.setdefault(path, f"{name}@{version}")
    for path, name in sealed_paths.items():
        if path.exists() or path.is_symlink():
            raise HeldoutPredictionError(
                f"dev-final mode is locked after heldout artifact creation: {name}"
            )


def _validate_dev_checkpoint_prefix(
    active_contract: EventExtractContract,
    input_rows: Sequence[Mapping[str, Any]],
    records: Sequence[ExtractRecord],
    predictions_path: Path,
    universe_symbols: Collection[str],
) -> None:
    if not predictions_path.exists() and not predictions_path.is_symlink():
        return
    if predictions_path.is_symlink() or not predictions_path.is_file():
        raise HeldoutPredictionError("dev-final predictions must be a regular file")
    if predictions_path.stat().st_size == 0:
        return
    rows = _load_jsonl(predictions_path, "dev-final prediction checkpoint")
    if len(rows) > len(input_rows):
        raise HeldoutPredictionError("dev-final prediction checkpoint has extra rows")
    prepared = {
        item.record.news_item_id: item for item in _prepare_records(active_contract, records)
    }
    seen: set[int] = set()
    for row in rows:
        identifier = row.get("news_item_id")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier in seen
            or identifier not in prepared
        ):
            raise HeldoutPredictionError("dev-final checkpoint identity drifted")
        seen.add(identifier)
        expected = prepared[identifier]
        if (
            row.get("input_sha256") != expected.input_sha256
            or row.get("text_sha256") != expected.text_sha256
            or row.get("contract_sha256") != active_contract.sha256
            or row.get("model") != active_contract.model
        ):
            raise HeldoutPredictionError("dev-final checkpoint binding drifted")
        if row.get("status") == "ok":
            try:
                _validate_resumed_success(
                    row,
                    expected,
                    active_contract,
                    universe_symbols,
                )
            except OfflineExtractError as exc:
                raise HeldoutPredictionError("dev-final successful checkpoint is invalid") from exc
        elif row.get("status") == "extract_failed":
            failure = _mapping(row.get("extract_failed"), "dev-final extract_failed")
            security = _mapping(row.get("security"), "dev-final security")
            reason = row.get("error")
            if (
                row.get("prediction") is not None
                or not isinstance(reason, str)
                or failure.get("reason") != reason
                or security.get("raw_prompt_persisted") is not False
                or security.get("raw_transport_response_persisted") is not False
                or security.get("exception_detail_persisted") is not False
            ):
                raise HeldoutPredictionError("dev-final failed checkpoint is unsafe")
        else:
            raise HeldoutPredictionError("dev-final checkpoint status is invalid")


def run_dev_final_predictions(
    active_contract_path: Path,
    *,
    project_root: Path = PROJECT_ROOT,
    design: EventEvaluationDesign | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
    chat_json_fn: ChatJsonCallable | None = None,
) -> DevFinalPredictionResult:
    """Run the final active contract on dev60 only, before heldout is unlocked."""

    root = project_root.resolve()
    active_design = design or load_event_evaluation_design(
        DEFAULT_EVALUATION_DESIGN_PATH,
        project_root=root,
    )
    active_contract = _load_active_contract(
        active_design,
        root,
        active_contract_path,
    )
    active_clock = clock or (lambda: datetime.now(UTC))
    started_at = _utc_now(now if now is not None else active_clock())
    if _parse_timestamp(
        started_at,
        "dev-final started_at_utc",
    ) < _parse_timestamp(
        active_contract.document.get("pre_registered_at"),
        "active contract pre_registered_at",
    ):
        raise HeldoutPredictionError("dev-final run predates active contract")
    active_settings = settings or _settings_from_project_env(root)
    _settings_safety(active_settings)
    _validate_runtime_contract(active_contract, active_settings)
    _ensure_dev_final_precedes_heldout(active_design, root)

    predictions_path = _artifact_path(
        active_design,
        root,
        "dev_final_predictions_jsonl",
    )
    manifest_path = _artifact_path(
        active_design,
        root,
        "dev_final_predictions_manifest_json",
    )
    if manifest_path.exists() or manifest_path.is_symlink():
        raise HeldoutPredictionError("create-only dev-final manifest already exists")
    input_rows, records = _dev_final_inputs(
        active_design,
        active_contract,
        root,
    )
    universe, database, _ = _load_dev_universe(active_contract, root)
    if (
        database.sqlite_uri_mode != "ro"
        or database.pragma_query_only != 1
        or database.total_changes != 0
    ):
        raise HeldoutPredictionError("dev-final database safety evidence drifted")
    _validate_dev_checkpoint_prefix(
        active_contract,
        input_rows,
        records,
        predictions_path,
        universe,
    )
    summary = extract_records(
        active_contract,
        records,
        output_path=predictions_path,
        eval_root=_eval_root(active_design, root),
        universe_symbols=universe,
        settings=active_settings,
        retry_failures=False,
        chat_json_fn=chat_json_fn,
    )
    prediction_rows = _load_jsonl(predictions_path, "dev-final predictions")
    success, _, _ = _validate_prediction_rows(
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
        or summary.success_count != success
        or summary.failure_count != 60 - success
    ):
        raise HeldoutPredictionError("dev-final prediction coverage drifted")
    completed_at = _utc_now(active_clock())
    if _parse_timestamp(
        completed_at,
        "dev-final completed_at_utc",
    ) < _parse_timestamp(started_at, "dev-final started_at_utc"):
        raise HeldoutPredictionError("dev-final completion clock moved backwards")
    manifest: JsonObject = {
        "design_sha256": active_design.sha256,
        "contract_sha256": active_contract.sha256,
        "predictions_path": predictions_path.relative_to(root).as_posix(),
        "predictions_sha256": _sha256_file(predictions_path),
        "row_count": 60,
        "success_count": success,
        "failure_count": 60 - success,
        "ordered_identity_sha256": _dev_prediction_identity_sha256(prediction_rows),
        "completed_at_utc": completed_at,
    }
    _create_only_bytes(
        manifest_path,
        _canonical_json_bytes(manifest),
        _eval_root(active_design, root),
    )
    _validate_dev_final_predictions(
        active_design,
        active_contract,
        root,
        predictions_path,
        manifest_path,
    )
    return DevFinalPredictionResult(
        summary=summary,
        predictions_path=predictions_path,
        manifest_path=manifest_path,
    )


def _database_safety(
    connection: sqlite3.Connection,
) -> JsonObject:
    tables = _sqlite_tables(connection)
    proposals = (
        int(connection.execute("SELECT COUNT(*) FROM trade_proposals").fetchone()[0])
        if "trade_proposals" in tables
        else 0
    )
    orders = (
        int(connection.execute("SELECT COUNT(*) FROM broker_orders").fetchone()[0])
        if "broker_orders" in tables
        else 0
    )
    non_simulate = (
        int(
            connection.execute(
                "SELECT COUNT(*) FROM broker_orders WHERE environment <> 'SIMULATE'"
            ).fetchone()[0]
        )
        if "broker_orders" in tables
        else 0
    )
    if non_simulate:
        raise HeldoutPredictionError("production database contains non-SIMULATE orders")
    return {
        "trade_proposal_count": proposals,
        "broker_order_count": orders,
        "non_simulate_order_count": non_simulate,
    }


def _frozen_design(design: EventEvaluationDesign) -> FrozenEvaluationDesign:
    return FrozenEvaluationDesign(
        path=design.path,
        sha256=design.sha256,
        document=design.document,
        base_contract=FrozenContract(
            path=design.base_contract.path,
            sha256=design.base_contract.sha256,
            document=design.base_contract.document,
        ),
    )


def _load_candidate_rows(
    design: EventEvaluationDesign,
    active_contract: EventExtractContract,
    project_root: Path,
) -> tuple[tuple[NewsRow, ...], frozenset[str], ProductionDatabaseEvidence, JsonObject]:
    input_contract = _mapping(active_contract.document.get("input"), "input")
    raw_database = input_contract.get("production_database")
    if raw_database != "data/alphapilot.db":
        raise HeldoutPredictionError("production database path drifted")
    database_path = (project_root.resolve() / str(raw_database)).resolve()
    with _open_production_database(database_path) as connection:
        connection.execute("BEGIN")
        tables = _sqlite_tables(connection)
        if not {"news_items", "securities"}.issubset(tables):
            raise HeldoutPredictionError("production database lacks candidate input tables")
        rows = tuple(_heldout_candidate_rows(connection, _frozen_design(design)))
        universe = _load_universe(connection)
        trading_safety = _database_safety(connection)
        query_only_row = connection.execute("PRAGMA query_only").fetchone()
        query_only = int(query_only_row[0]) if query_only_row is not None else 0
        total_changes = int(connection.total_changes)
        connection.execute("COMMIT")
    database = ProductionDatabaseEvidence(
        relative_path="data/alphapilot.db",
        sqlite_uri_mode="ro",
        pragma_query_only=query_only,
        total_changes=total_changes,
        required_tables_found=("news_items", "securities"),
    )
    if query_only != 1 or total_changes != 0:
        raise HeldoutPredictionError("production database read-only evidence failed")
    if not rows:
        raise HeldoutPredictionError("heldout candidate batch is empty")
    sampling = _mapping(
        _mapping(
            _mapping(design.document.get("splits"), "splits").get("heldout_40"),
            "heldout",
        ).get("sampling"),
        "sampling",
    )
    selected_count = sampling.get("selected_count")
    if (
        isinstance(selected_count, bool)
        or not isinstance(selected_count, int)
        or len(rows) < selected_count
    ):
        raise HeldoutPredictionError(
            "heldout raw candidate batch is too small for the registered sample"
        )
    return rows, universe, database, trading_safety


def _validate_candidate_inputs(
    design: EventEvaluationDesign,
    active_contract: EventExtractContract,
    rows: Sequence[Mapping[str, Any]],
    database_rows: Sequence[NewsRow],
) -> list[ExtractRecord]:
    heldout = _mapping(
        _mapping(design.document.get("splits"), "splits").get("heldout_40"),
        "heldout",
    )
    inputs = _mapping(heldout.get("prediction_inputs"), "prediction_inputs")
    required = inputs.get("required_record_fields")
    join_keys = inputs.get("prediction_annotation_join_keys")
    if (
        not isinstance(required, Sequence)
        or isinstance(required, (str, bytes))
        or not isinstance(join_keys, Sequence)
        or tuple(join_keys) != ("news_item_id", "input_sha256", "text_sha256")
    ):
        raise HeldoutPredictionError("candidate input/join contract drifted")
    if any(not set(cast(Sequence[str], required)).issubset(row) for row in rows):
        raise HeldoutPredictionError("candidate input fields drifted")
    try:
        validated = validate_heldout_candidate_inputs(
            [dict(row) for row in rows],
            rows=database_rows,
            design=_frozen_design(design),
            active_contract=active_contract,
        )
    except GoldSampleError as exc:
        raise HeldoutPredictionError("authoritative candidate input validation failed") from exc
    ordered_ids = [row.news_item_id for row in database_rows]
    if list(validated) != ordered_ids:
        raise HeldoutPredictionError("candidate input order differs from database batch")
    result = [
        ExtractRecord(
            news_item_id=identifier,
            source=cast(str, record["source"]),
            ingested_symbol=cast(str | None, record["ingested_symbol"]),
            title=cast(str, record["title"]),
            original_text=cast(str, record["original_text"]),
            published_at=cast(str | None, record["published_at"]),
            available_time=cast(str, record["available_time"]),
            body_state=cast(str, record["body_state"]),
            declared_input_sha256=cast(str, record["input_sha256"]),
            declared_text_sha256=cast(str, record["text_sha256"]),
        )
        for identifier, record in validated.items()
    ]
    sampling = _mapping(heldout.get("sampling"), "sampling")
    selected_count = sampling.get("selected_count")
    if (
        isinstance(selected_count, bool)
        or not isinstance(selected_count, int)
        or len(result) < selected_count
    ):
        raise HeldoutPredictionError(
            "candidate input artifact is too small for the registered sample"
        )
    return result


def _candidate_identity_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    payload = b""
    prior_id = 0
    for row in rows:
        identifier = row.get("news_item_id")
        input_sha256 = row.get("input_sha256")
        text_sha256 = row.get("text_sha256")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier <= prior_id
            or not _valid_sha256(input_sha256)
            or not _valid_sha256(text_sha256)
        ):
            raise HeldoutPredictionError("candidate identity fields are invalid")
        prior_id = identifier
        payload += (
            f"{identifier}\0{str(input_sha256).lower()}\0{str(text_sha256).lower()}\n"
        ).encode("ascii")
    return _sha256_bytes(payload)


def _prepare_or_validate_candidate_artifact(
    design: EventEvaluationDesign,
    active_contract: EventExtractContract,
    project_root: Path,
    *,
    pdf_fetcher: PdfFetcher,
    pdf_text_extractor: PdfTextExtractor,
) -> tuple[
    list[ExtractRecord],
    tuple[JsonObject, ...],
    frozenset[str],
    ProductionDatabaseEvidence,
    JsonObject,
    Path,
]:
    database_rows, universe, database, trading_safety = _load_candidate_rows(
        design,
        active_contract,
        project_root,
    )
    path = _artifact_path(design, project_root, "heldout_candidate_inputs_jsonl")
    root = _eval_root(design, project_root)
    if path.exists() or path.is_symlink():
        rows = tuple(_load_jsonl(path, "heldout candidate inputs"))
        database_ids = tuple(item.news_item_id for item in database_rows)
        artifact_ids = tuple(cast(int, row.get("news_item_id")) for row in rows)
        if artifact_ids != database_ids:
            raise HeldoutPredictionError(
                "frozen candidate inputs no longer cover the complete database batch"
            )
    else:
        try:
            rows = tuple(
                materialize_heldout_candidate_inputs(
                    database_rows,
                    _frozen_design(design),
                    active_contract,
                    pdf_fetcher=pdf_fetcher,
                    pdf_text_extractor=pdf_text_extractor,
                )
            )
        except GoldSampleError as exc:
            raise HeldoutPredictionError(
                "authoritative candidate input materialization failed"
            ) from exc
        _create_only_bytes(path, _jsonl_bytes(rows), root)
    records = _validate_candidate_inputs(
        design,
        active_contract,
        rows,
        database_rows,
    )
    if len(records) != len(database_rows):
        raise HeldoutPredictionError("candidate input count differs from database batch")
    return records, rows, universe, database, trading_safety, path


def _state_events(path: Path) -> list[JsonObject]:
    if not path.exists() and not path.is_symlink():
        return []
    return _load_jsonl(path, "heldout inference state")


def _start_inference_state(
    path: Path,
    root: Path,
    payload: Mapping[str, Any],
) -> None:
    event = {
        "schema_version": "p4.2a-heldout-inference-state-v1.1",
        "event": "inference_started",
        **dict(payload),
    }
    _create_only_bytes(path, _canonical_json_bytes(event), root)


def _append_terminal_state(path: Path, event: Mapping[str, Any]) -> None:
    if path.is_symlink() or not path.is_file():
        raise HeldoutPredictionError("heldout inference state is unavailable")
    payload = _canonical_json_bytes(event)
    flags = os.O_WRONLY | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise HeldoutPredictionError("heldout inference state is not a regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        events = _state_events(path)
        terminals = [
            item
            for item in events
            if item.get("event") in {"inference_completed", "inference_failed"}
        ]
        if len(events) != 1 or events[0].get("event") != "inference_started" or terminals:
            raise HeldoutPredictionError("heldout inference state cannot accept a terminal event")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise HeldoutPredictionError("failed to append heldout terminal state")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _safe_terminal_error(error: BaseException) -> str:
    if isinstance(error, HeldoutPredictionNotReady):
        return "heldout_not_ready"
    if isinstance(
        error,
        (EventEvaluationDesignError, GoldSampleError, HeldoutPredictionError),
    ):
        return "heldout_safety_gate_failed"
    if isinstance(error, OfflineExtractError):
        return "offline_extract_failed"
    if isinstance(error, OSError):
        return "artifact_io_failed"
    return "unexpected_failure"


def _validate_prediction_rows(
    active_contract: EventExtractContract,
    candidate_rows: Sequence[Mapping[str, Any]],
    records: Sequence[ExtractRecord],
    prediction_rows: Sequence[Mapping[str, Any]],
    universe_symbols: Collection[str],
) -> tuple[int, int, Counter[str]]:
    if len(prediction_rows) != len(candidate_rows):
        raise HeldoutPredictionError("candidate prediction output is incomplete")
    candidate_by_id = {int(row["news_item_id"]): row for row in candidate_rows}
    seen: set[int] = set()
    success = 0
    positive = 0
    failures: Counter[str] = Counter()
    prepared_by_id = {
        item.record.news_item_id: item for item in _prepare_records(active_contract, records)
    }
    for row in prediction_rows:
        identifier = row.get("news_item_id")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier in seen
            or identifier not in candidate_by_id
        ):
            raise HeldoutPredictionError("candidate prediction IDs are invalid")
        seen.add(identifier)
        candidate = candidate_by_id[identifier]
        if (
            row.get("input_sha256") != candidate.get("input_sha256")
            or row.get("text_sha256") != candidate.get("text_sha256")
            or row.get("contract_sha256") != active_contract.sha256
            or row.get("model") != active_contract.model
        ):
            raise HeldoutPredictionError("candidate prediction binding drifted")
        status = row.get("status")
        if status == "ok":
            _validate_resumed_success(
                row,
                prepared_by_id[identifier],
                active_contract,
                universe_symbols,
            )
            success += 1
            prediction = _mapping(row.get("prediction"), "candidate prediction")
            materiality = prediction.get("materiality")
            if isinstance(materiality, int) and not isinstance(materiality, bool):
                positive += int(materiality >= 2)
        elif status == "extract_failed":
            if row.get("prediction") is not None:
                raise HeldoutPredictionError("failed candidate persisted a model payload")
            reason = row.get("error")
            failure = _mapping(row.get("extract_failed"), "extract_failed")
            security = _mapping(row.get("security"), "prediction security")
            if (
                not isinstance(reason, str)
                or failure.get("reason") != reason
                or security.get("raw_prompt_persisted") is not False
                or security.get("raw_transport_response_persisted") is not False
                or security.get("exception_detail_persisted") is not False
            ):
                raise HeldoutPredictionError("failed candidate safety evidence drifted")
            failures[reason] += 1
        else:
            raise HeldoutPredictionError("candidate prediction status is invalid")
    return success, positive, failures


def _manifest_payload(
    design: EventEvaluationDesign,
    active_contract: EventExtractContract,
    project_root: Path,
    *,
    candidate_rows: Sequence[Mapping[str, Any]],
    records: Sequence[ExtractRecord],
    candidate_inputs_path: Path,
    prediction_rows: Sequence[Mapping[str, Any]],
    predictions_path: Path,
    receipt_sha256: str,
    database: ProductionDatabaseEvidence,
    trading_safety: Mapping[str, Any],
    settings_safety: Mapping[str, Any],
    universe_symbols: Collection[str],
    now: datetime | None,
) -> JsonObject:
    success, positive, failures = _validate_prediction_rows(
        active_contract,
        candidate_rows,
        records,
        prediction_rows,
        universe_symbols,
    )
    failure_count = len(prediction_rows) - success
    positive_rate = positive / success if success else 0.0
    contract_files = _mapping(
        active_contract.document.get("contract_files"),
        "active contract files",
    )
    prompt = _mapping(contract_files.get("prompt"), "active prompt")
    schema = _mapping(contract_files.get("schema"), "active result schema")
    candidate_ids = [int(row["news_item_id"]) for row in candidate_rows]
    return {
        "schema_version": "p4.2a-heldout-candidate-predictions-manifest-v1.1",
        "generated_at_utc": _utc_now(now),
        "design_sha256": design.sha256,
        "prediction_contract_sha256": active_contract.sha256,
        "freeze_receipt_sha256": receipt_sha256,
        "candidate_inputs_sha256": _sha256_file(candidate_inputs_path),
        "candidate_predictions_sha256": _sha256_file(predictions_path),
        "candidate_count": len(candidate_rows),
        "prediction_attempted_count": len(prediction_rows),
        "prediction_success_count": success,
        "prediction_failure_count": failure_count,
        "news_item_ids": candidate_ids,
        "design": {
            "schema_version": design.document.get("schema_version"),
            "sha256": design.sha256,
        },
        "prediction_contract": {
            "path": active_contract.path.relative_to(project_root.resolve()).as_posix(),
            "sha256": active_contract.sha256,
            "model": active_contract.model,
            "endpoint": active_contract.endpoint,
            "explicit_cache_enabled": active_contract.explicit_cache_enabled,
            "prompt_path": prompt.get("path"),
            "prompt_sha256": prompt.get("sha256"),
            "result_schema_path": schema.get("path"),
            "result_schema_sha256": schema.get("sha256"),
            "taxonomy_version": _mapping(
                active_contract.document.get("taxonomy"),
                "active taxonomy",
            ).get("version"),
            "freeze_receipt_sha256": receipt_sha256,
            "max_retries": active_contract.max_retries,
        },
        "candidate_inputs": {
            "path": candidate_inputs_path.relative_to(project_root.resolve()).as_posix(),
            "sha256": _sha256_file(candidate_inputs_path),
            "count": len(candidate_rows),
            "identity_sha256": _candidate_identity_sha256(candidate_rows),
            "identities": [
                {
                    "news_item_id": row["news_item_id"],
                    "input_sha256": row["input_sha256"],
                    "text_sha256": row["text_sha256"],
                }
                for row in candidate_rows
            ],
        },
        "predictions": {
            "path": predictions_path.relative_to(project_root.resolve()).as_posix(),
            "sha256": _sha256_file(predictions_path),
            "row_count": len(prediction_rows),
            "attempted_count": len(prediction_rows),
            "success_count": success,
            "failure_count": failure_count,
            "failures_by_safe_reason": dict(sorted(failures.items())),
            "failures_by_validation_field_and_constraint": (
                _validation_failure_counts(prediction_rows)
            ),
            "predicted_materiality_gte_2_count": positive,
            "predicted_materiality_gte_2_rate": positive_rate,
            "positive_rate_denominator": "successful_predictions",
            "raw_prompt_or_transport_payload_persisted": False,
        },
        "database_safety": {
            "path": database.relative_path,
            "sqlite_uri_mode": database.sqlite_uri_mode,
            "pragma_query_only": database.pragma_query_only,
            "connection_total_changes": database.total_changes,
        },
        "trading_safety": {
            "settings": dict(settings_safety),
            "database": dict(trading_safety),
            "proposals_or_orders_created": False,
        },
        "isolation": {
            "production_writes_allowed": False,
            "scheduler_changed": False,
            "jobs_registry_changed": False,
            "migrations_or_models_changed": False,
            "p4_1_changed": False,
            "p4_2b_unlocked": False,
        },
    }


def _validate_started_state(
    events: Sequence[Mapping[str, Any]],
    *,
    design: EventEvaluationDesign,
    active_contract: EventExtractContract,
    candidate_inputs_sha256: str,
    candidate_count: int,
    receipt_sha256: str,
) -> Mapping[str, Any]:
    if len(events) != 1 or events[0].get("event") != "inference_started":
        raise HeldoutPredictionError(
            "one-shot state is not one unterminated inference_started event"
        )
    started = events[0]
    if (
        started.get("design_sha256") != design.sha256
        or started.get("contract_sha256") != active_contract.sha256
        or started.get("candidate_inputs_sha256") != candidate_inputs_sha256
        or started.get("candidate_count") != candidate_count
        or started.get("freeze_receipt_sha256") != receipt_sha256
        or started.get("model_calls_started") != 0
    ):
        raise HeldoutPredictionError("one-shot started event binding drifted")
    _validate_settings_safety_snapshot(
        _mapping(
            started.get("settings_safety"),
            "inference started settings safety",
        )
    )
    _parse_timestamp(started.get("at_utc"), "inference started timestamp")
    return started


def _write_or_validate_manifest(
    design: EventEvaluationDesign,
    project_root: Path,
    payload: Mapping[str, Any],
) -> tuple[Path, str]:
    path = _artifact_path(
        design,
        project_root,
        "heldout_candidate_predictions_manifest_json",
    )
    encoded = _canonical_json_bytes(payload)
    if path.exists() or path.is_symlink():
        existing = path.read_bytes()
        if existing != encoded:
            raise HeldoutPredictionError("existing prediction manifest bytes drifted")
    else:
        _create_only_bytes(path, encoded, _eval_root(design, project_root))
    return path, _sha256_file(path)


def run_heldout_predictions(
    *,
    project_root: Path = PROJECT_ROOT,
    design: EventEvaluationDesign | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    pdf_fetcher: PdfFetcher = download_cninfo_pdf,
    pdf_text_extractor: PdfTextExtractor = extract_cninfo_pdf_text,
    chat_json_fn: ChatJsonCallable | None = None,
) -> HeldoutPredictionResult:
    """Run the whole registered 8/4-8/5 candidate batch exactly once."""

    root = project_root.resolve()
    active_design = design or load_event_evaluation_design(
        DEFAULT_EVALUATION_DESIGN_PATH,
        project_root=root,
    )
    _require_ready(active_design, now)
    _, receipt_sha256, active_contract = validate_prediction_contract_freeze(
        active_design,
        root,
    )
    active_settings = settings or _settings_from_project_env(root)
    settings_safety = _settings_safety(active_settings)
    _validate_runtime_contract(active_contract, active_settings)

    state_path = _artifact_path(
        active_design,
        root,
        "heldout_inference_state_jsonl",
    )
    predictions_path = _artifact_path(
        active_design,
        root,
        "heldout_candidate_predictions_jsonl",
    )
    manifest_path = _artifact_path(
        active_design,
        root,
        "heldout_candidate_predictions_manifest_json",
    )
    if _state_events(state_path):
        raise HeldoutPredictionError(
            "existing inference_started event permanently blocks another model run"
        )
    if predictions_path.exists() or predictions_path.is_symlink():
        raise HeldoutPredictionError("create-only heldout predictions already exist")
    if manifest_path.exists() or manifest_path.is_symlink():
        raise HeldoutPredictionError("create-only heldout prediction manifest already exists")

    (
        records,
        candidate_rows,
        universe,
        database,
        trading_safety,
        candidate_inputs_path,
    ) = _prepare_or_validate_candidate_artifact(
        active_design,
        active_contract,
        root,
        pdf_fetcher=pdf_fetcher,
        pdf_text_extractor=pdf_text_extractor,
    )
    prepared = _prepare_records(active_contract, records)
    if len(prepared) != len(candidate_rows):
        raise HeldoutPredictionError("candidate preparation count drifted")
    candidate_inputs_sha256 = _sha256_file(candidate_inputs_path)
    state_root = _eval_root(active_design, root)
    started_at = _utc_now(now)
    _start_inference_state(
        state_path,
        state_root,
        {
            "at_utc": started_at,
            "design_sha256": active_design.sha256,
            "contract_sha256": active_contract.sha256,
            "freeze_receipt_sha256": receipt_sha256,
            "candidate_inputs_sha256": candidate_inputs_sha256,
            "candidate_identity_sha256": _candidate_identity_sha256(candidate_rows),
            "candidate_count": len(candidate_rows),
            "database": {
                "sqlite_uri_mode": database.sqlite_uri_mode,
                "pragma_query_only": database.pragma_query_only,
                "connection_total_changes": database.total_changes,
            },
            "settings_safety": dict(settings_safety),
            "model_calls_started": 0,
        },
    )

    try:
        summary = extract_records(
            active_contract,
            records,
            output_path=predictions_path,
            eval_root=state_root,
            universe_symbols=universe,
            settings=active_settings,
            retry_failures=False,
            chat_json_fn=chat_json_fn,
        )
        prediction_rows = _load_jsonl(
            predictions_path,
            "heldout candidate predictions",
        )
        success, positive, _ = _validate_prediction_rows(
            active_contract,
            candidate_rows,
            records,
            prediction_rows,
            universe,
        )
        manifest = _manifest_payload(
            active_design,
            active_contract,
            root,
            candidate_rows=candidate_rows,
            records=records,
            candidate_inputs_path=candidate_inputs_path,
            prediction_rows=prediction_rows,
            predictions_path=predictions_path,
            receipt_sha256=receipt_sha256,
            database=database,
            trading_safety=trading_safety,
            settings_safety=settings_safety,
            universe_symbols=universe,
            now=now,
        )
        actual_manifest_path, _ = _write_or_validate_manifest(
            active_design,
            root,
            manifest,
        )
        _append_terminal_state(
            state_path,
            {
                "schema_version": "p4.2a-heldout-inference-state-v1.1",
                "event": "inference_completed",
                "at_utc": _utc_now(),
                "design_sha256": active_design.sha256,
                "contract_sha256": active_contract.sha256,
                "candidate_count": len(candidate_rows),
                "attempted_count": len(prediction_rows),
                "success_count": success,
                "failure_count": len(prediction_rows) - success,
                "prediction_manifest_sha256": _sha256_file(actual_manifest_path),
            },
        )
        return HeldoutPredictionResult(
            summary=summary,
            candidate_inputs_path=candidate_inputs_path,
            predictions_path=predictions_path,
            manifest_path=actual_manifest_path,
            state_path=state_path,
            positive_count=positive,
            positive_rate=positive / success if success else 0.0,
        )
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        with suppress(HeldoutPredictionError):
            _append_terminal_state(
                state_path,
                {
                    "schema_version": "p4.2a-heldout-inference-state-v1.1",
                    "event": "inference_failed",
                    "at_utc": _utc_now(),
                    "design_sha256": active_design.sha256,
                    "contract_sha256": active_contract.sha256,
                    "error": _safe_terminal_error(error),
                    "raw_exception_or_payload_persisted": False,
                },
            )
        raise


def finalize_existing_heldout_run(
    *,
    project_root: Path = PROJECT_ROOT,
    design: EventEvaluationDesign | None = None,
    now: datetime | None = None,
) -> HeldoutPredictionResult:
    """Recover only a fully written run after a process crash; never call the model."""

    root = project_root.resolve()
    active_design = design or load_event_evaluation_design(
        DEFAULT_EVALUATION_DESIGN_PATH,
        project_root=root,
    )
    _require_ready(active_design, now)
    _, receipt_sha256, active_contract = validate_prediction_contract_freeze(
        active_design,
        root,
    )
    inputs_path = _artifact_path(
        active_design,
        root,
        "heldout_candidate_inputs_jsonl",
    )
    predictions_path = _artifact_path(
        active_design,
        root,
        "heldout_candidate_predictions_jsonl",
    )
    state_path = _artifact_path(
        active_design,
        root,
        "heldout_inference_state_jsonl",
    )
    candidate_rows = _load_jsonl(inputs_path, "heldout candidate inputs")
    (
        database_rows,
        universe,
        database_evidence,
        trading_safety,
    ) = _load_candidate_rows(active_design, active_contract, root)
    records = _validate_candidate_inputs(
        active_design,
        active_contract,
        candidate_rows,
        database_rows,
    )
    events = _state_events(state_path)
    if len(events) not in {1, 2}:
        raise HeldoutPredictionError("one-shot state cannot be recovered")
    started = _validate_started_state(
        events[:1],
        design=active_design,
        active_contract=active_contract,
        candidate_inputs_sha256=_sha256_file(inputs_path),
        candidate_count=len(candidate_rows),
        receipt_sha256=receipt_sha256,
    )
    if started.get("candidate_identity_sha256") != _candidate_identity_sha256(candidate_rows):
        raise HeldoutPredictionError("one-shot candidate identity binding drifted")
    prediction_rows = _load_jsonl(predictions_path, "heldout candidate predictions")
    database = _mapping(started.get("database"), "inference started database")
    if (
        database.get("sqlite_uri_mode") != "ro"
        or database.get("pragma_query_only") != 1
        or database.get("connection_total_changes") != 0
    ):
        raise HeldoutPredictionError("inference started database evidence drifted")

    success, positive, failures = _validate_prediction_rows(
        active_contract,
        candidate_rows,
        records,
        prediction_rows,
        universe,
    )
    settings_safety = _validate_settings_safety_snapshot(
        _mapping(
            started.get("settings_safety"),
            "inference started settings safety",
        )
    )
    existing_manifest_path = _artifact_path(
        active_design,
        root,
        "heldout_candidate_predictions_manifest_json",
    )
    manifest_time = now
    if existing_manifest_path.exists() or existing_manifest_path.is_symlink():
        existing_manifest = _load_json(
            existing_manifest_path,
            "heldout candidate prediction manifest",
        )
        manifest_time = _parse_timestamp(
            existing_manifest.get("generated_at_utc"),
            "existing prediction manifest generated_at_utc",
        )
        existing_trading = _mapping(
            existing_manifest.get("trading_safety"),
            "existing manifest trading_safety",
        )
        existing_settings_safety = _mapping(
            existing_trading.get("settings"),
            "existing manifest settings safety",
        )
        if dict(existing_settings_safety) != settings_safety:
            raise HeldoutPredictionError(
                "existing manifest settings safety differs from started state"
            )
    manifest = _manifest_payload(
        active_design,
        active_contract,
        root,
        candidate_rows=candidate_rows,
        records=records,
        candidate_inputs_path=inputs_path,
        prediction_rows=prediction_rows,
        predictions_path=predictions_path,
        receipt_sha256=receipt_sha256,
        database=database_evidence,
        trading_safety=trading_safety,
        settings_safety=settings_safety,
        universe_symbols=universe,
        now=manifest_time,
    )
    expected_manifest_sha256 = _sha256_bytes(_canonical_json_bytes(manifest))
    if len(events) == 2:
        terminal = events[1]
        if (
            terminal.get("event") != "inference_completed"
            or terminal.get("design_sha256") != active_design.sha256
            or terminal.get("contract_sha256") != active_contract.sha256
            or terminal.get("candidate_count") != len(candidate_rows)
            or terminal.get("attempted_count") != len(prediction_rows)
            or terminal.get("success_count") != success
            or terminal.get("failure_count") != len(prediction_rows) - success
            or terminal.get("prediction_manifest_sha256") != expected_manifest_sha256
        ):
            raise HeldoutPredictionError("existing inference terminal state drifted")
        _parse_timestamp(terminal.get("at_utc"), "inference terminal timestamp")
    manifest_path, manifest_sha256 = _write_or_validate_manifest(
        active_design,
        root,
        manifest,
    )
    if len(events) == 1:
        _append_terminal_state(
            state_path,
            {
                "schema_version": "p4.2a-heldout-inference-state-v1.1",
                "event": "inference_completed",
                "at_utc": _utc_now(now),
                "design_sha256": active_design.sha256,
                "contract_sha256": active_contract.sha256,
                "candidate_count": len(candidate_rows),
                "attempted_count": len(prediction_rows),
                "success_count": success,
                "failure_count": len(prediction_rows) - success,
                "prediction_manifest_sha256": manifest_sha256,
                "terminal_recovery_without_model_calls": True,
                "model_calls": 0,
            },
        )
    summary = ExtractionSummary(
        expected_count=len(records),
        success_count=success,
        failure_count=len(records) - success,
        newly_attempted_count=0,
        retried_failure_count=0,
        skipped_exact_success_count=success,
        skipped_failure_count=len(records) - success,
        output_line_count=len(prediction_rows),
        failures_by_reason=dict(failures),
        failures_by_validation_field_and_constraint=_validation_failure_counts(
            prediction_rows
        ),
        isolated_audit_tables=("llm_calls",),
        isolated_audit_row_count=0,
        checkpoint_audited_success_count=success,
    )
    return HeldoutPredictionResult(
        summary=summary,
        candidate_inputs_path=inputs_path,
        predictions_path=predictions_path,
        manifest_path=manifest_path,
        state_path=state_path,
        positive_count=positive,
        positive_rate=positive / success if success else 0.0,
    )


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze or run one explicit P4.2a held-out evaluation design. "
            "The production database is always SQLite mode=ro + query_only."
        )
    )
    parser.add_argument(
        "--evaluation-design",
        type=Path,
        default=DEFAULT_EVALUATION_DESIGN_PATH,
        help="byte-frozen evaluation design; defaults to the legacy v1.1 design",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--freeze-contract",
        action="store_true",
        help="create the immutable active prompt/schema/model receipt, then stop",
    )
    mode.add_argument(
        "--run-dev-final",
        action="store_true",
        help=(
            "run the explicit active contract on frozen dev60 only and create "
            "the pre-registered predictions plus manifest"
        ),
    )
    parser.add_argument(
        "--active-contract",
        type=Path,
        help=(
            "explicit final versioned extraction contract for --run-dev-final "
            "or --freeze-contract; the base contract is allowed only when explicit"
        ),
    )
    parser.add_argument(
        "--dev-final-predictions",
        type=Path,
        help="the pre-registered 60-row active-contract dev-final predictions",
    )
    parser.add_argument(
        "--dev-final-predictions-manifest",
        type=Path,
        help="the create-only manifest for --dev-final-predictions",
    )
    mode.add_argument(
        "--finalize-existing",
        action="store_true",
        help=(
            "append terminal evidence only when a crashed one-shot already has one "
            "complete candidate prediction per frozen input; never calls the model"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        active_design = load_event_evaluation_design(
            cast(Path, arguments.evaluation_design),
            project_root=PROJECT_ROOT,
        )
        dev_artifact_arguments = (
            arguments.dev_final_predictions,
            arguments.dev_final_predictions_manifest,
        )
        if arguments.freeze_contract:
            if arguments.active_contract is None or any(
                value is None for value in dev_artifact_arguments
            ):
                raise HeldoutPredictionError(
                    "freeze requires explicit active contract and both dev-final artifacts"
                )
        elif arguments.run_dev_final:
            if arguments.active_contract is None:
                raise HeldoutPredictionError("dev-final run requires an explicit active contract")
            if any(value is not None for value in dev_artifact_arguments):
                raise HeldoutPredictionError("dev-final artifact path flags are freeze-only")
        elif arguments.active_contract is not None or any(
            value is not None for value in dev_artifact_arguments
        ):
            raise HeldoutPredictionError(
                "active contract and dev-final artifact flags require an explicit mode"
            )
        if arguments.freeze_contract:
            path = freeze_prediction_contract(
                cast(Path, arguments.active_contract),
                cast(Path, arguments.dev_final_predictions),
                cast(Path, arguments.dev_final_predictions_manifest),
                design=active_design,
            )
            payload: JsonObject = {
                "mode": "freeze_contract",
                "status": "ok",
                "path": path.relative_to(PROJECT_ROOT).as_posix(),
                "sha256": _sha256_file(path),
            }
        elif arguments.run_dev_final:
            dev_result = run_dev_final_predictions(
                cast(Path, arguments.active_contract),
                design=active_design,
            )
            payload = {
                "mode": "dev_final_predictions",
                "status": "ok",
                "row_count": dev_result.summary.expected_count,
                "success_count": dev_result.summary.success_count,
                "failure_count": dev_result.summary.failure_count,
                "predictions_path": dev_result.predictions_path.relative_to(
                    PROJECT_ROOT
                ).as_posix(),
                "manifest_path": dev_result.manifest_path.relative_to(PROJECT_ROOT).as_posix(),
            }
        elif arguments.finalize_existing:
            finalized = finalize_existing_heldout_run(design=active_design)
            payload = {
                "mode": "finalize_existing",
                "status": "ok",
                "candidate_count": finalized.summary.expected_count,
                "success_count": finalized.summary.success_count,
                "failure_count": finalized.summary.failure_count,
                "model_calls": 0,
            }
        else:
            heldout_result = run_heldout_predictions(design=active_design)
            payload = {
                "mode": "heldout_candidate_predictions",
                "status": "ok",
                "candidate_count": heldout_result.summary.expected_count,
                "success_count": heldout_result.summary.success_count,
                "failure_count": heldout_result.summary.failure_count,
                "positive_count": heldout_result.positive_count,
                "positive_rate": heldout_result.positive_rate,
            }
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": _safe_terminal_error(error),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
