from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

JsonObject = dict[str, Any]
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

CANDIDATE_KEYS = frozenset(
    {
        "available_time",
        "body_evidence",
        "body_state",
        "content_hash",
        "contract_sha256",
        "declared_input_sha256",
        "design_sha256",
        "ingested_symbol",
        "input_sha256",
        "model",
        "news_item_id",
        "original_text",
        "published_at",
        "schema_version",
        "source",
        "text_sha256",
        "title",
        "url",
    }
)
PREDICTION_KEYS = frozenset(
    {
        "contract_sha256",
        "declared_input_sha256",
        "input_sha256",
        "latency_ms",
        "llm_audit_latency_ms",
        "model",
        "news_item_id",
        "prediction",
        "recorded_at_utc",
        "schema_version",
        "security",
        "source",
        "status",
        "text_sha256",
        "tokens",
    }
)
FAILED_PREDICTION_KEYS = PREDICTION_KEYS | frozenset({"error", "extract_failed"})
EXTRACT_FAILED_KEYS = frozenset({"constraint", "field", "reason", "retryable"})
JOIN_FIELDS = (
    "news_item_id",
    "source",
    "input_sha256",
    "declared_input_sha256",
    "text_sha256",
    "contract_sha256",
    "model",
)
IDENTITY_FIELDS = (
    "news_item_id",
    "input_sha256",
    "declared_input_sha256",
    "text_sha256",
)
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


class DevelopmentFrameError(RuntimeError):
    """The pre-registered P4.2a v2 development frame cannot be built safely."""


@dataclass(frozen=True, slots=True)
class FrozenFile:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class DevelopmentFrameSpec:
    design_path: str
    design_sha256: str
    frame_id: str
    sampling_seed: str
    source_design_sha256: str
    source_contract_sha256: str
    source_model: str
    candidate_inputs: FrozenFile
    candidate_input_manifest: FrozenFile
    baseline_predictions: FrozenFile
    baseline_prediction_manifest: FrozenFile
    retired_selection: FrozenFile
    retired_count: int
    retired_sorted_ids_sha256: str
    input_count: int
    positive_count: int
    negative_count: int
    failed_count: int
    positive_available_after_retirement: int
    negative_available_after_retirement: int
    positive_selected: int
    negative_selected: int
    selection_manifest_path: str
    owner_blind_path: str
    artifact_root: str


@dataclass(frozen=True, slots=True)
class DevelopmentFrameResult:
    selection_manifest_path: Path
    owner_blind_path: Path
    selection_manifest_sha256: str
    owner_blind_sha256: str
    selected_count: int
    positive_selected: int
    negative_selected: int


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def canonical_jsonl_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(canonical_json_bytes(dict(row)) for row in rows)


def candidate_identity_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    payload = b"".join(
        (
            f"{row['news_item_id']}\0{row['input_sha256']}\0"
            f"{row['declared_input_sha256']}\0{row['text_sha256']}\n"
        ).encode("ascii")
        for row in rows
    )
    return sha256_bytes(payload)


def selection_rank(
    *, seed: str, sampling_stratum: str, news_item_id: int, input_sha256: str
) -> str:
    payload = (
        seed.encode("utf-8")
        + b"\0"
        + sampling_stratum.encode("utf-8")
        + b"\0"
        + str(news_item_id).encode("ascii")
        + b"\0"
        + input_sha256.encode("ascii")
    )
    return sha256_bytes(payload)


def owner_order_rank(*, design_sha256: str, news_item_id: int, input_sha256: str) -> str:
    payload = (
        b"owner-order-v1\0"
        + design_sha256.encode("ascii")
        + b"\0"
        + str(news_item_id).encode("ascii")
        + b"\0"
        + input_sha256.encode("ascii")
    )
    return sha256_bytes(payload)


def forbidden_blind_paths(value: object, *, prefix: str = "$") -> list[str]:
    violations: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = key.casefold().replace("-", "_")
            path = f"{prefix}.{key}"
            if (
                "stratum" in normalized
                or "prediction" in normalized
                or "rank" in normalized
                or "selection" in normalized
                or normalized.startswith("sampling")
                or normalized.startswith("eligible_")
            ):
                violations.append(path)
            violations.extend(forbidden_blind_paths(child, prefix=path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            violations.extend(forbidden_blind_paths(child, prefix=f"{prefix}[{index}]"))
    return violations


def validate_blind_row(row: Mapping[str, Any]) -> None:
    if set(row) != BLIND_FIELDS:
        raise DevelopmentFrameError("owner blind row fields drifted")
    design = _mapping(row.get("design"), "owner blind design")
    if set(design) != {"path", "sha256"}:
        raise DevelopmentFrameError("owner blind design binding drifted")
    if row.get("gold") != {}:
        raise DevelopmentFrameError("owner blind gold must be an exact empty object")
    violations = forbidden_blind_paths(row)
    if violations:
        raise DevelopmentFrameError(f"owner blind row leaks selection metadata at {violations[0]}")


def build_development_frame(
    project_root: Path,
    spec: DevelopmentFrameSpec,
    *,
    publish: bool = True,
) -> DevelopmentFrameResult:
    root = project_root.resolve()
    files = {
        "candidate inputs": spec.candidate_inputs,
        "candidate input manifest": spec.candidate_input_manifest,
        "baseline predictions": spec.baseline_predictions,
        "baseline prediction manifest": spec.baseline_prediction_manifest,
        "retired selection": spec.retired_selection,
    }
    resolved = {label: _verify_frozen_file(root, item, label) for label, item in files.items()}
    inputs = _load_jsonl(resolved["candidate inputs"], "candidate inputs")
    predictions = _load_jsonl(resolved["baseline predictions"], "baseline predictions")
    materialization_manifest = _load_json(
        resolved["candidate input manifest"], "candidate input manifest"
    )
    prediction_manifest = _load_json(
        resolved["baseline prediction manifest"], "baseline prediction manifest"
    )
    retired_manifest = _load_json(resolved["retired selection"], "retired selection")

    _validate_source_rows(inputs, predictions, spec)
    _validate_materialization_manifest(materialization_manifest, spec)
    _validate_prediction_manifest(prediction_manifest, inputs, predictions, spec)
    retired_identities = _validate_retired_manifest(retired_manifest, inputs, spec)

    positive: list[JsonObject] = []
    negative: list[JsonObject] = []
    failed: list[JsonObject] = []
    prediction_by_id = {cast(int, row["news_item_id"]): row for row in predictions}
    for row in inputs:
        prediction = prediction_by_id[cast(int, row["news_item_id"])]
        if prediction["status"] == "extract_failed":
            failed.append(row)
        elif cast(Mapping[str, Any], prediction["prediction"])["materiality"] >= 2:
            positive.append(row)
        else:
            negative.append(row)
    if (len(positive), len(negative), len(failed)) != (
        spec.positive_count,
        spec.negative_count,
        spec.failed_count,
    ):
        raise DevelopmentFrameError("audited baseline partition counts drifted")

    retired_set = set(retired_identities)
    positive_pool = [row for row in positive if _identity(row) not in retired_set]
    negative_pool = [row for row in negative if _identity(row) not in retired_set]
    if len(retired_set) != spec.retired_count or any(
        identity not in {_identity(row) for row in positive} for identity in retired_set
    ):
        raise DevelopmentFrameError("retired identities are not the frozen positive subset")
    if (
        len(positive_pool) != spec.positive_available_after_retirement
        or len(negative_pool) != spec.negative_available_after_retirement
    ):
        raise DevelopmentFrameError("post-retirement pool counts drifted")

    selected: list[tuple[str, str, JsonObject]] = []
    for stratum, pool, count in (
        ("predicted_positive", positive_pool, spec.positive_selected),
        ("predicted_negative", negative_pool, spec.negative_selected),
    ):
        ranked = sorted(
            (
                selection_rank(
                    seed=spec.sampling_seed,
                    sampling_stratum=stratum,
                    news_item_id=cast(int, row["news_item_id"]),
                    input_sha256=cast(str, row["input_sha256"]),
                ),
                row,
            )
            for row in pool
        )
        if len(ranked) < count:
            raise DevelopmentFrameError(f"insufficient rows in {stratum}")
        selected.extend((stratum, rank, row) for rank, row in ranked[:count])
    if any(_identity(row) in retired_set for _, _, row in selected):
        raise DevelopmentFrameError("retired identity reached the selected frame")

    ordered = sorted(
        (
            owner_order_rank(
                design_sha256=spec.design_sha256,
                news_item_id=cast(int, row["news_item_id"]),
                input_sha256=cast(str, row["input_sha256"]),
            ),
            stratum,
            selection_hash,
            row,
        )
        for stratum, selection_hash, row in selected
    )
    strata_in_owner_order = [item[1] for item in ordered]
    if (
        len(set(strata_in_owner_order)) > 1
        and sum(left != right for left, right in pairwise(strata_in_owner_order)) < 2
    ):
        raise DevelopmentFrameError("owner delivery order groups by sampling stratum")

    blind_rows: list[JsonObject] = []
    selected_manifest_rows: list[JsonObject] = []
    for index, (owner_hash, stratum, selection_hash, row) in enumerate(ordered, start=1):
        blind: JsonObject = {
            "schema_version": "p4.2a-v2-owner-blind-item-v1",
            "design": {"path": spec.design_path, "sha256": spec.design_sha256},
            "frame_id": spec.frame_id,
            "sample_index": index,
            "news_item_id": row["news_item_id"],
            "source": row["source"],
            "url": row["url"],
            "title": row["title"],
            "ingested_symbol": row["ingested_symbol"],
            "published_at": row["published_at"],
            "available_time": row["available_time"],
            "original_text": row["original_text"],
            "input_sha256": row["input_sha256"],
            "text_sha256": row["text_sha256"],
            "body_state": row["body_state"],
            "body_evidence": copy.deepcopy(row["body_evidence"]),
            "gold": {},
        }
        validate_blind_row(blind)
        blind_rows.append(blind)
        selected_manifest_rows.append(
            {
                "sample_index": index,
                "sampling_stratum": stratum,
                "selection_rank_sha256": selection_hash,
                "owner_order_sha256": owner_hash,
                **{field: row[field] for field in JOIN_FIELDS},
            }
        )

    blind_payload = canonical_jsonl_bytes(blind_rows)
    manifest: JsonObject = {
        "schema_version": "p4.2a-v2-development-selection-manifest-v1",
        "design": {"path": spec.design_path, "sha256": spec.design_sha256},
        "frame_id": spec.frame_id,
        "source_lineage": {
            "candidate_inputs": asdict(spec.candidate_inputs),
            "candidate_input_manifest": asdict(spec.candidate_input_manifest),
            "baseline_predictions": asdict(spec.baseline_predictions),
            "baseline_prediction_manifest": asdict(spec.baseline_prediction_manifest),
            "retired_selection": asdict(spec.retired_selection),
            "source_design_sha256": spec.source_design_sha256,
            "source_contract_sha256": spec.source_contract_sha256,
            "source_model": spec.source_model,
        },
        "audit": {
            "input_row_count": len(inputs),
            "identity_join_fields": list(JOIN_FIELDS),
            "input_prediction_identity_match": True,
            "partition_before_retirement": {
                "predicted_positive": len(positive),
                "predicted_negative": len(negative),
                "extract_failed": len(failed),
            },
            "retired_identity_count": len(retired_set),
            "available_after_retirement": {
                "predicted_positive": len(positive_pool),
                "predicted_negative": len(negative_pool),
                "extract_failed": len(failed),
            },
            "retired_selected_intersection_count": 0,
        },
        "selection": {
            "algorithm": "sha256_rank_without_replacement_per_stratum_v1",
            "seed": spec.sampling_seed,
            "rank_preimage": (
                "utf8(seed) || NUL || utf8(stratum) || NUL || "
                "ascii(news_item_id) || NUL || ascii(input_sha256)"
            ),
            "owner_order_algorithm": "sha256_rank_without_sampling_stratum_v1",
            "owner_order_preimage": (
                "utf8('owner-order-v1') || NUL || ascii(design_sha256) || NUL || "
                "ascii(news_item_id) || NUL || ascii(input_sha256)"
            ),
            "selected_counts": {
                "predicted_positive": spec.positive_selected,
                "predicted_negative": spec.negative_selected,
                "extract_failed": 0,
                "total": len(selected_manifest_rows),
            },
            "without_replacement": True,
            "selected": selected_manifest_rows,
        },
        "owner_delivery": {
            "path": spec.owner_blind_path,
            "sha256": sha256_bytes(blind_payload),
            "row_count": len(blind_rows),
            "sampling_stratum_visible": False,
            "prediction_visible": False,
            "selection_rank_visible": False,
            "gold_state": "empty_object_pending_human_adjudication",
        },
        "production_writes": False,
    }
    manifest_payload = canonical_json_bytes(manifest)
    manifest_path = _output_path(root, spec.selection_manifest_path, spec.artifact_root)
    blind_path = _output_path(root, spec.owner_blind_path, spec.artifact_root)
    if publish:
        publish_create_only_pair(((manifest_path, manifest_payload), (blind_path, blind_payload)))
    return DevelopmentFrameResult(
        selection_manifest_path=manifest_path,
        owner_blind_path=blind_path,
        selection_manifest_sha256=sha256_bytes(manifest_payload),
        owner_blind_sha256=sha256_bytes(blind_payload),
        selected_count=len(blind_rows),
        positive_selected=spec.positive_selected,
        negative_selected=spec.negative_selected,
    )


def publish_create_only_pair(payloads: Sequence[tuple[Path, bytes]]) -> None:
    if len(payloads) != 2 or payloads[0][0] == payloads[1][0]:
        raise DevelopmentFrameError("create-only publication requires two unique paths")
    for path, _ in payloads:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to overwrite development artifact: {path}")
    staged: list[tuple[Path, Path]] = []
    created: list[tuple[Path, os.stat_result]] = []
    try:
        for target, payload in payloads:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent, prefix=f".{target.name}.", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            staged.append((temporary, target))
        for temporary, target in staged:
            os.link(temporary, target)
            created.append((target, target.stat()))
        for directory in {target.parent for _, target in staged}:
            _fsync_directory(directory)
    except BaseException:
        for target, created_stat in reversed(created):
            current = target.stat() if target.exists() else None
            if current and (current.st_dev, current.st_ino) == (
                created_stat.st_dev,
                created_stat.st_ino,
            ):
                target.unlink()
                _fsync_directory(target.parent)
        raise
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def _validate_source_rows(
    inputs: Sequence[JsonObject],
    predictions: Sequence[JsonObject],
    spec: DevelopmentFrameSpec,
) -> None:
    if len(inputs) != spec.input_count or len(predictions) != spec.input_count:
        raise DevelopmentFrameError("candidate input/prediction row count drifted")
    prior_id = 0
    for candidate, prediction in zip(inputs, predictions, strict=True):
        if set(candidate) != CANDIDATE_KEYS:
            raise DevelopmentFrameError("frozen candidate input row schema drifted")
        expected_prediction_keys = (
            FAILED_PREDICTION_KEYS
            if prediction.get("status") == "extract_failed"
            else PREDICTION_KEYS
        )
        if set(prediction) != expected_prediction_keys:
            raise DevelopmentFrameError("frozen prediction row schema drifted")
        identifier = candidate.get("news_item_id")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier <= prior_id
        ):
            raise DevelopmentFrameError("candidate IDs must be unique and ascending")
        prior_id = identifier
        for field in ("input_sha256", "declared_input_sha256", "text_sha256"):
            _require_sha(candidate.get(field), f"candidate {field}")
        if candidate["input_sha256"] == candidate["declared_input_sha256"]:
            raise DevelopmentFrameError("candidate active and declared input hashes must differ")
        if any(candidate.get(field) != prediction.get(field) for field in JOIN_FIELDS):
            raise DevelopmentFrameError("candidate/prediction identity join drifted")
        if (
            candidate.get("design_sha256") != spec.source_design_sha256
            or candidate.get("contract_sha256") != spec.source_contract_sha256
            or candidate.get("model") != spec.source_model
        ):
            raise DevelopmentFrameError("candidate v1.7 source lineage drifted")
        if not isinstance(candidate.get("body_evidence"), Mapping):
            raise DevelopmentFrameError("candidate body_evidence must be a mapping")
        if prediction.get("status") == "ok":
            result = _mapping(prediction.get("prediction"), "successful prediction")
            materiality = result.get("materiality")
            if isinstance(materiality, bool) or materiality not in (0, 1, 2, 3):
                raise DevelopmentFrameError("prediction materiality is invalid")
        elif prediction.get("status") == "extract_failed":
            failure = _mapping(prediction.get("extract_failed"), "failed prediction detail")
            if (
                prediction.get("prediction") is not None
                or set(failure) != EXTRACT_FAILED_KEYS
                or failure.get("retryable") is not False
                or not isinstance(prediction.get("error"), str)
                or not prediction["error"]
            ):
                raise DevelopmentFrameError("failed prediction evidence drifted")
        else:
            raise DevelopmentFrameError("prediction status is invalid")


def _validate_materialization_manifest(
    manifest: Mapping[str, Any], spec: DevelopmentFrameSpec
) -> None:
    artifacts = _mapping(manifest.get("artifacts"), "materialization artifacts")
    inputs = _mapping(artifacts.get("eligible_inputs_jsonl"), "eligible inputs")
    counts = _mapping(manifest.get("counts"), "materialization counts")
    lineage = _mapping(manifest.get("lineage"), "materialization lineage")
    design = _mapping(lineage.get("evaluation_design"), "materialization design")
    contract = _mapping(lineage.get("prediction_contract"), "materialization contract")
    if (
        inputs.get("path") != spec.candidate_inputs.path
        or inputs.get("sha256") != spec.candidate_inputs.sha256
        or counts.get("eligible_candidates") != spec.input_count
        or design.get("sha256") != spec.source_design_sha256
        or contract.get("sha256") != spec.source_contract_sha256
        or contract.get("model") != spec.source_model
    ):
        raise DevelopmentFrameError("materialization manifest binding drifted")


def _validate_prediction_manifest(
    manifest: Mapping[str, Any],
    inputs: Sequence[JsonObject],
    predictions: Sequence[JsonObject],
    spec: DevelopmentFrameSpec,
) -> None:
    success = sum(row["status"] == "ok" for row in predictions)
    failure = len(predictions) - success
    identities = [{field: row[field] for field in IDENTITY_FIELDS} for row in inputs]
    candidate = _mapping(manifest.get("candidate_inputs"), "prediction candidate inputs")
    result = _mapping(manifest.get("predictions"), "prediction manifest results")
    design = _mapping(manifest.get("design"), "prediction manifest design")
    contract = _mapping(manifest.get("prediction_contract"), "prediction manifest contract")
    materialization = _mapping(manifest.get("materialization"), "prediction materialization")
    if (
        manifest.get("candidate_count") != spec.input_count
        or manifest.get("prediction_attempted_count") != spec.input_count
        or manifest.get("prediction_success_count") != success
        or manifest.get("prediction_failure_count") != failure
        or manifest.get("candidate_inputs_sha256") != spec.candidate_inputs.sha256
        or manifest.get("candidate_predictions_sha256") != spec.baseline_predictions.sha256
        or candidate.get("path") != spec.candidate_inputs.path
        or candidate.get("sha256") != spec.candidate_inputs.sha256
        or candidate.get("count") != spec.input_count
        or candidate.get("identity_sha256") != candidate_identity_sha256(inputs)
        or candidate.get("identities") != identities
        or result.get("path") != spec.baseline_predictions.path
        or result.get("sha256") != spec.baseline_predictions.sha256
        or result.get("row_count") != spec.input_count
        or result.get("attempted_count") != spec.input_count
        or result.get("success_count") != success
        or result.get("failure_count") != failure
        or design.get("sha256") != spec.source_design_sha256
        or contract.get("sha256") != spec.source_contract_sha256
        or contract.get("model") != spec.source_model
        or materialization.get("manifest_path") != spec.candidate_input_manifest.path
        or materialization.get("manifest_sha256") != spec.candidate_input_manifest.sha256
    ):
        raise DevelopmentFrameError("prediction manifest binding drifted")


def _validate_retired_manifest(
    manifest: Mapping[str, Any],
    inputs: Sequence[JsonObject],
    spec: DevelopmentFrameSpec,
) -> tuple[tuple[object, ...], ...]:
    candidate = _mapping(manifest.get("candidate_inputs"), "retired candidate inputs")
    predictions = _mapping(manifest.get("candidate_predictions"), "retired candidate predictions")
    selection = _mapping(manifest.get("selection"), "retired selection")
    selected = selection.get("selected")
    if not isinstance(selected, list):
        raise DevelopmentFrameError("retired selected identities must be a list")
    input_by_id = {cast(int, row["news_item_id"]): row for row in inputs}
    identities: list[tuple[object, ...]] = []
    ids: list[int] = []
    for item in selected:
        record = _mapping(item, "retired selected identity")
        identifier = record.get("news_item_id")
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier in ids:
            raise DevelopmentFrameError("retired selected IDs are invalid or duplicated")
        source = input_by_id.get(identifier)
        if source is None or any(
            record.get(field) != source.get(field) for field in IDENTITY_FIELDS
        ):
            raise DevelopmentFrameError("retired full selected identity drifted")
        _require_sha(record.get("selection_rank_sha256"), "retired selection rank")
        ids.append(identifier)
        identities.append(_identity(record))
    compact = json.dumps(sorted(ids), separators=(",", ":")).encode("ascii")
    if (
        len(selected) != spec.retired_count
        or selection.get("selected_count") != spec.retired_count
        or sha256_bytes(compact) != spec.retired_sorted_ids_sha256
        or candidate.get("path") != spec.candidate_inputs.path
        or candidate.get("sha256") != spec.candidate_inputs.sha256
        or candidate.get("count") != spec.input_count
        or predictions.get("path") != spec.baseline_predictions.path
        or predictions.get("sha256") != spec.baseline_predictions.sha256
        or predictions.get("manifest_path") != spec.baseline_prediction_manifest.path
        or predictions.get("manifest_sha256") != spec.baseline_prediction_manifest.sha256
    ):
        raise DevelopmentFrameError("retired selection manifest binding drifted")
    return tuple(identities)


def _identity(row: Mapping[str, Any]) -> tuple[object, ...]:
    return tuple(row[field] for field in IDENTITY_FIELDS)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DevelopmentFrameError(f"{label} must be a mapping")
    return cast(Mapping[str, Any], value)


def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise DevelopmentFrameError(f"{label} must be a lowercase SHA-256")
    return value


def _safe_path(root: Path, relative: str, label: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise DevelopmentFrameError(f"{label} path escapes project root")
    path = (root / raw).resolve()
    if not path.is_relative_to(root):
        raise DevelopmentFrameError(f"{label} path escapes project root")
    return path


def _verify_frozen_file(root: Path, artifact: FrozenFile, label: str) -> Path:
    _require_sha(artifact.sha256, f"{label} SHA-256")
    path = _safe_path(root, artifact.path, label)
    if path.is_symlink() or not path.is_file() or sha256_file(path) != artifact.sha256:
        raise DevelopmentFrameError(f"{label} differs from its frozen SHA-256")
    return path


def _output_path(root: Path, relative: str, artifact_root: str) -> Path:
    output = _safe_path(root, relative, "output")
    allowed = _safe_path(root, artifact_root, "artifact root")
    if not output.is_relative_to(allowed):
        raise DevelopmentFrameError("development output escapes its artifact root")
    return output


def _json_pairs(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise DevelopmentFrameError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _load_json(path: Path, label: str) -> JsonObject:
    try:
        value: object = json.loads(
            path.read_bytes(),
            object_pairs_hook=_json_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                DevelopmentFrameError(f"{label} contains non-finite {value}")
            ),
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DevelopmentFrameError(f"{label} is invalid JSON") from exc
    return dict(_mapping(value, label))


def _load_jsonl(path: Path, label: str) -> list[JsonObject]:
    rows: list[JsonObject] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise DevelopmentFrameError(f"{label} is not UTF-8") from exc
    if not lines or any(not line.strip() for line in lines):
        raise DevelopmentFrameError(f"{label} contains no rows or a blank row")
    for index, line in enumerate(lines, start=1):
        try:
            value: object = json.loads(
                line,
                object_pairs_hook=_json_pairs,
                parse_constant=_reject_non_finite,
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DevelopmentFrameError(f"{label} row {index} is invalid JSON") from exc
        rows.append(dict(_mapping(value, f"{label} row {index}")))
    return rows


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _reject_non_finite(value: str) -> None:
    raise DevelopmentFrameError(f"JSON contains non-finite numeric constant {value}")
