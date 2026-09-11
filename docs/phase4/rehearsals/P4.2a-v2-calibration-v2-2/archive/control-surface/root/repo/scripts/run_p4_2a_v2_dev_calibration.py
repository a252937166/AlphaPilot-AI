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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, cast

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if __package__ in {None, ""}:
    # Direct execution otherwise exposes only ``scripts/`` on sys.path. Resolve
    # imports from this file, never from the caller's cwd or PYTHONPATH.
    sys.path[:0] = [str(PROJECT_ROOT), str(PROJECT_ROOT / "src")]

import yaml  # noqa: E402
from scripts import run_p4_2a_heldout_predictions as heldout  # noqa: E402
from scripts.run_p4_2a_offline_extract import (  # noqa: E402
    ChatJsonCallable,
    ExtractionSummary,
    ExtractRecord,
    _prepare_records,
    _settings_from_project_env,
    _validate_runtime_contract,
    extract_records,
)

from alphapilot.core.config import Settings  # noqa: E402
from alphapilot.llm.p4_news_event import (  # noqa: E402
    EventExtractContract,
    load_event_extract_contract,
)

JsonObject = dict[str, Any]
DESIGN_PATH = Path("config/p4_event_evaluation_v2.yaml")
DESIGN_SHA256 = "18a2428a4ec04bfea6e4f4d70692f38ea82fbaee5a223f30f2465b895b238e21"
ROUND_PREREGISTRATION_PATH = Path(
    "docs/phase4/eval/v2-calibration/development/rounds/r1/round-preregistration.json"
)
ROUND_PREREGISTRATION_SHA256 = "f485a95271fc609fa84e623dee349e676d6e97e7bf27dd52d2ce7835c26e9c8d"
ROUND_1_OUTCOME_SHA256 = "d6970c45b2d797229de87ec30985c2696795cf173f8f4dbb9cb43156fcfc6649"
ROUND_1_STATE_PREFIX_SHA256 = "ca8355e357a214a0b06af945be240480e732bb7a1251c1670763acedb35d0bc4"
ROUND_1_ARTIFACT_SHA256 = {
    "round-preregistration.json": ROUND_PREREGISTRATION_SHA256,
    "round-outcome.json": ROUND_1_OUTCOME_SHA256,
    "qwen3.7-flash/predictions.jsonl": (
        "4e612d4918147354ccc45816953af458c1c5ac106c449b8f9f855e3501a5b052"
    ),
    "qwen3.7-flash/manifest.json": (
        "96c24c842e968896f921a3f55fda38e60747ca7c810d4b1b5c4d7b25a858da8e"
    ),
    "qwen3.7-flash/report.json": (
        "27bbeaa186bdf632a49b8ace6a69e9de45f745be2fb7b45a64d81ecd87a1a2a5"
    ),
    "qwen3.7-flash/terminal-state.jsonl": (
        "f65b6565d3fc4ddad419ad26c20cc7026e52584d847ae2a1c3504d8855bf718e"
    ),
    "qwen3.6-plus/predictions.jsonl": (
        "c08036514e92b85ec71354ccd127e71cd4ed011d22a229701e914173210c6e59"
    ),
    "qwen3.6-plus/manifest.json": (
        "4cc82c653c343cb208a0e0409c0c71bd7d13894c493dbcc8290063b071422a6f"
    ),
    "qwen3.6-plus/report.json": (
        "3253a5620bf493bc4d6394d164d6b85c46a53dacff4ad04ca1b97e54add62b28"
    ),
    "qwen3.6-plus/terminal-state.jsonl": (
        "c033090ca79a5b4ac3816b421af2055ac427033dcb6c4fe600d14a1d31dd7039"
    ),
}
ROUND_2_AUTHORIZATION_PATH = Path(
    "docs/phase4/reports/P4.2a-round1-adjudication-and-round2-conditions-20260809.json"
)
ROUND_2_AUTHORIZATION_SHA256 = (
    "2def5029003cbcb830a1cdabe0bbfa8e240dbe5e84083dbe96b82c15d46a4aa0"
)
ROUND_2_PREREGISTRATION_PATH = Path(
    "docs/phase4/eval/v2-calibration/development/rounds/r2/round-preregistration.json"
)
ROUND_2_PREREGISTRATION_SHA256 = (
    "a7c971ba59c2c7199e5becd95ac09fde9e5cd16a3eef225a8f244a934efc5812"
)
ROUND_2_OUTCOME_SHA256 = "49026fad5e14771cbe1a5a83aa3311107b30ff02d708c31cd4a0dad4c2384c66"
ROUND_2_STATE_PREFIX_SHA256 = (
    "e3d0e971db316b23915f38ca699e7f284edbc72e1c79ee844847ba1a5953f612"
)
ROUND_2_ARTIFACT_SHA256 = {
    "round-preregistration.json": ROUND_2_PREREGISTRATION_SHA256,
    "round-outcome.json": ROUND_2_OUTCOME_SHA256,
    "qwen3.7-flash/predictions.jsonl": (
        "ab976f24c0cbc9840873af84b5742085d58eb1941585a655096e06c6c2a37078"
    ),
    "qwen3.7-flash/manifest.json": (
        "c5c6b3d53d94fb837937a598d1f0b9b83fe68f7ae50f169ecb582a788cf49125"
    ),
    "qwen3.7-flash/report.json": (
        "1b5b4f3288902c09a7cf1ccc93777c293f104c1e326406784a17fffc51c58840"
    ),
    "qwen3.7-flash/terminal-state.jsonl": (
        "3b0481a77cb9493406ab15077d4de70fa62d1a667f1e3448ef38ba5e6599154b"
    ),
    "qwen3.6-plus/predictions.jsonl": (
        "9cea9213afda23d303cfa23eedea8b6c3d3ad30380606d87db31c20bc3ebbd00"
    ),
    "qwen3.6-plus/manifest.json": (
        "80ead2fe4ae5909d7152a957a78653cb17ede93b6287381747a1737c64765ffd"
    ),
    "qwen3.6-plus/report.json": (
        "a5f259e7ee8a086c314c716a0164e425e288e7b688d10ee9bc0a8d2f44748122"
    ),
    "qwen3.6-plus/terminal-state.jsonl": (
        "86c6a1de15528bcb483978373f2a1b1356ac5578f11f1a9d32d612fcda70af10"
    ),
}
ROUND_3_AUTHORIZATION_PATH = Path(
    "docs/phase4/reports/P4.2a-round2-adjudication-20260810.json"
)
ROUND_3_AUTHORIZATION_SHA256 = (
    "a4af80bcf6fe355c48db56b189f7f8a6b9949c7f9d99b67773c8dbeaa539eaf0"
)
ROUND_3_PREREGISTRATION_PATH = Path(
    "docs/phase4/eval/v2-calibration/development/rounds/r3/round-preregistration.json"
)
ROUND_3_PREREGISTRATION_SHA256: str | None = (
    "72517cdc546adedf543e9e9abffecdca3b18a2193f2865f7c476edde25512134"
)
ROUND_3_POST_ROUND_GOVERNANCE: Mapping[str, Any] = {
    "o5_trigger": "round_3_point_estimate_passes_but_adverse_flip_margin_fails",
    "action": "pause_round_consumption_and_return_frame_enlargement_decision_to_owner",
    "automatic_round_4_allowed": False,
}
BASE_CONTRACT_PATH = Path("config/p4_event_extract_eval_v1_7.yaml")
BASE_CONTRACT_SHA256 = "68474e4bd4fd5c9c88711dd5e102898ad1ed75a0fb984045efbd14e51a6db701"
MODEL_ORDER = ("qwen3.7-flash", "qwen3.6-plus")
EXPECTED_ROWS = 45
PRECISION_MINIMUM = 0.80
FALSE_OMISSION_RATE_MAXIMUM = 0.20
OWNER_EXPORT_SHA256 = "812ee8c86ab9f9ed269b1c6952046bddb11317b8624618d8444de5b03f612d31"
HUMAN_GOLD_SHA256 = "fc08abc2f78bcb693c4e048fcd43ddd4f37c9d6a7a70ac878467194efbacaaa8"
PROMPT_SHA256 = "9441690be1a21c7bbafc1f16ec5e6174220d5bcd7f4e89f0bf32e8ace068817a"
FAILURE_POLICY = "model_result_gate_fails_without_denominator_exclusion_or_replacement"
O3_BOUNDARY_POLICY = "sharpen_state_scale_and_decision_impact_not_global_threshold_raise"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CalibrationRoundError(RuntimeError):
    """A formal dev45 calibration round violated its frozen contract."""


@dataclass(frozen=True, slots=True)
class ContractBinding:
    model_slug: str
    contract: EventExtractContract
    wrapper: JsonObject


@dataclass(frozen=True, slots=True)
class RoundBinding:
    round_number: int
    preregistration_path: Path
    preregistration_sha256: str
    prompt_sha256: str
    margin_required: bool


@dataclass(frozen=True, slots=True)
class ProductionSnapshot:
    sqlite_uri_mode: str
    pragma_query_only: int
    connection_total_changes: int
    llm_call_count: int
    llm_call_max_id: int | None
    trade_proposal_count: int
    broker_order_count: int
    non_simulate_order_count: int
    news_events_table_exists: bool
    universe_symbols: frozenset[str]


@dataclass(frozen=True, slots=True)
class RoundResult:
    outcome_path: Path
    calibration_state_path: Path
    selected_model: str | None
    reports: Mapping[str, JsonObject]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _strict_object(pairs: Sequence[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise CalibrationRoundError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> NoReturn:
    raise CalibrationRoundError(f"non-finite JSON value is forbidden: {value}")


def _load_json(path: Path, name: str) -> JsonObject:
    try:
        value: object = json.loads(
            path.read_bytes(),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_nonfinite,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationRoundError(f"{name} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise CalibrationRoundError(f"{name} must be a JSON object")
    return cast(JsonObject, value)


def _load_jsonl(path: Path, name: str, *, allow_empty: bool = False) -> list[JsonObject]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise CalibrationRoundError(f"{name} is unavailable") from exc
    rows: list[JsonObject] = []
    for line_number, line in enumerate(payload.splitlines(), start=1):
        if not line.strip():
            raise CalibrationRoundError(f"{name} line {line_number} is blank")
        try:
            value: object = json.loads(
                line,
                object_pairs_hook=_strict_object,
                parse_constant=_reject_nonfinite,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CalibrationRoundError(f"{name} line {line_number} is invalid") from exc
        if not isinstance(value, dict):
            raise CalibrationRoundError(f"{name} line {line_number} is not an object")
        rows.append(cast(JsonObject, value))
    if not rows and not allow_empty:
        raise CalibrationRoundError(f"{name} must not be empty")
    return rows


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CalibrationRoundError(f"{name} must be a mapping")
    return cast(Mapping[str, Any], value)


def _exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise CalibrationRoundError(f"{name} keys drifted")


def _safe_relative(root: Path, raw: object, name: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise CalibrationRoundError(f"{name} path is invalid")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise CalibrationRoundError(f"{name} path escapes the project")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise CalibrationRoundError(f"{name} path escapes the project")
    return resolved


def _file_ref(root: Path, value: object, name: str) -> tuple[Path, str]:
    entry = _mapping(value, name)
    _exact_keys(entry, {"path", "sha256"}, name)
    path = _safe_relative(root, entry.get("path"), name)
    digest = entry.get("sha256")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise CalibrationRoundError(f"{name} SHA-256 is invalid")
    if path.is_symlink() or not path.is_file() or _sha256_file(path) != digest:
        raise CalibrationRoundError(f"{name} bytes differ from the registered SHA-256")
    return path, digest


def _utc_now(clock: Callable[[], datetime]) -> str:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise CalibrationRoundError("clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise CalibrationRoundError(f"{name} must be one UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalibrationRoundError(f"{name} is invalid") from exc
    offset = parsed.utcoffset()
    if parsed.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise CalibrationRoundError(f"{name} must be UTC")
    return parsed.astimezone(UTC)


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _ensure_parent(path: Path, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise CalibrationRoundError("artifact root must be one regular directory")
    if not path.resolve().is_relative_to(root.resolve()) or path.resolve() == root.resolve():
        raise CalibrationRoundError("artifact path escapes the calibration root")
    current = root
    for part in path.parent.resolve().relative_to(root.resolve()).parts:
        current = current / part
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                raise CalibrationRoundError("artifact parent traverses a symlink")
        else:
            current.mkdir()
    if path.is_symlink():
        raise CalibrationRoundError("artifact path must not be a symlink")


def _create_only(path: Path, payload: bytes, root: Path) -> None:
    _ensure_parent(path, root)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        raise FileExistsError(f"refusing to overwrite create-only artifact: {path}") from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise CalibrationRoundError("create-only artifact is not a regular file")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise CalibrationRoundError("failed to write create-only artifact")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _append_terminal(path: Path, event: Mapping[str, Any], *, expected_start: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise CalibrationRoundError("state artifact is unavailable")
    flags = os.O_WRONLY | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise CalibrationRoundError("state artifact is not a regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        events = _load_jsonl(path, "state artifact")
        if len(events) != 1 or events[0].get("event") != expected_start:
            raise CalibrationRoundError("state artifact cannot accept another terminal")
        payload = _canonical_json_bytes(event)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise CalibrationRoundError("failed to append terminal state")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _safe_error(error: BaseException) -> str:
    if isinstance(error, CalibrationRoundError):
        return type(error).__name__
    return type(error).__name__


def _load_design(root: Path) -> JsonObject:
    path = (root / DESIGN_PATH).resolve()
    if path.is_symlink() or not path.is_file() or _sha256_file(path) != DESIGN_SHA256:
        raise CalibrationRoundError("P4.2a v2 evaluation design bytes drifted")
    try:
        value: object = yaml.safe_load(path.read_bytes())
    except yaml.YAMLError as exc:
        raise CalibrationRoundError("P4.2a v2 evaluation design is invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != "p4.2a-evaluation-design-v2":
        raise CalibrationRoundError("P4.2a v2 evaluation design version drifted")
    document = cast(JsonObject, value)
    if document.get("production_writes_allowed") is not False:
        raise CalibrationRoundError("P4.2a v2 production-write lock drifted")
    artifacts = _mapping(document.get("artifacts"), "design.artifacts")
    for name in (
        "development_round_preregistration_pattern",
        "development_round_outcome_pattern",
        "development_model_result_files",
        "development_calibration_state_jsonl",
    ):
        if name not in artifacts:
            raise CalibrationRoundError(f"design artifact registration is missing: {name}")
    return document


def _validate_followup_round_bindings(
    root: Path,
    prereg: Mapping[str, Any],
    round_number: int,
    preregistration_sha256: str,
) -> None:
    del preregistration_sha256  # The caller already verifies the registered bytes.
    if round_number not in {2, 3}:
        raise CalibrationRoundError("follow-up round binding is not registered")
    prior_round = round_number - 1
    prior_preregistration_path = (
        ROUND_PREREGISTRATION_PATH
        if prior_round == 1
        else ROUND_2_PREREGISTRATION_PATH
    )
    prior_preregistration_sha256 = (
        ROUND_PREREGISTRATION_SHA256
        if prior_round == 1
        else ROUND_2_PREREGISTRATION_SHA256
    )
    prior_outcome_sha256 = (
        ROUND_1_OUTCOME_SHA256 if prior_round == 1 else ROUND_2_OUTCOME_SHA256
    )
    authorization_path = (
        ROUND_2_AUTHORIZATION_PATH
        if round_number == 2
        else ROUND_3_AUTHORIZATION_PATH
    )
    authorization_sha256 = (
        ROUND_2_AUTHORIZATION_SHA256
        if round_number == 2
        else ROUND_3_AUTHORIZATION_SHA256
    )
    prior = _mapping(prereg.get("prior_round"), "prior_round")
    _exact_keys(prior, {"round_number", "preregistration", "outcome"}, "prior_round")
    if prior.get("round_number") != prior_round:
        raise CalibrationRoundError(f"Round {round_number} prior-round number drifted")
    prior_prereg_path, prior_prereg_sha = _file_ref(
        root, prior.get("preregistration"), "prior_round.preregistration"
    )
    prior_outcome_path, prior_outcome_sha = _file_ref(
        root, prior.get("outcome"), "prior_round.outcome"
    )
    if (
        prior_prereg_path != (root / prior_preregistration_path).resolve()
        or prior_prereg_sha != prior_preregistration_sha256
        or prior_outcome_path
        != (
            root
            / "docs/phase4/eval/v2-calibration/development/rounds"
            / f"r{prior_round}"
            / "round-outcome.json"
        ).resolve()
        or prior_outcome_sha != prior_outcome_sha256
    ):
        raise CalibrationRoundError(f"Round {round_number} prior-round binding drifted")

    bound_authorization_path, bound_authorization_sha = _file_ref(
        root, prereg.get("round_authorization"), "round_authorization"
    )
    if (
        bound_authorization_path != (root / authorization_path).resolve()
        or bound_authorization_sha != authorization_sha256
    ):
        raise CalibrationRoundError(f"Round {round_number} authorization binding drifted")

    prompt_path, prompt_sha = _file_ref(root, prereg.get("prompt"), "preregistration.prompt")
    summaries = _mapping(
        prereg.get("candidate_prompt_summaries"), "candidate_prompt_summaries"
    )
    _exact_keys(summaries, set(MODEL_ORDER), "candidate_prompt_summaries")
    for model in MODEL_ORDER:
        summary = _mapping(summaries.get(model), f"candidate_prompt_summaries.{model}")
        _exact_keys(summary, {"prompt", "summary"}, f"candidate_prompt_summaries.{model}")
        bound_path, bound_sha = _file_ref(root, summary.get("prompt"), f"{model}.prompt")
        description = summary.get("summary")
        if (
            bound_path != prompt_path
            or bound_sha != prompt_sha
            or not isinstance(description, str)
            or not description.strip()
        ):
            raise CalibrationRoundError(
                f"Round {round_number} candidate prompt summary binding drifted"
            )

    artifacts = _mapping(prereg.get("artifacts"), "artifacts")
    _exact_keys(artifacts, {"round_directory", "round_outcome", "calibration_state"}, "artifacts")
    round_directory = _mapping(artifacts.get("round_directory"), "round_directory")
    round_outcome = _mapping(artifacts.get("round_outcome"), "round_outcome")
    calibration_state = _mapping(artifacts.get("calibration_state"), "calibration_state")
    if (
        dict(round_directory)
        != {
            "path": (
                "docs/phase4/eval/v2-calibration/development/rounds/"
                f"r{round_number}"
            )
        }
        or dict(round_outcome)
        != {
            "path": (
                "docs/phase4/eval/v2-calibration/development/rounds/"
                f"r{round_number}/round-outcome.json"
            ),
            "create_only": True,
        }
        or dict(calibration_state)
        != {
            "path": "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl",
            "append_only": True,
        }
    ):
        raise CalibrationRoundError(f"Round {round_number} artifact semantics drifted")


def _registered_round_identity(round_number: int) -> tuple[Path, str]:
    identities = {
        1: (ROUND_PREREGISTRATION_PATH, ROUND_PREREGISTRATION_SHA256),
        2: (ROUND_2_PREREGISTRATION_PATH, ROUND_2_PREREGISTRATION_SHA256),
        3: (ROUND_3_PREREGISTRATION_PATH, ROUND_3_PREREGISTRATION_SHA256),
    }
    try:
        path, digest = identities[round_number]
    except KeyError as exc:
        raise CalibrationRoundError(
            "round has no executable path-and-hash registration in this runner"
        ) from exc
    if digest is None or _SHA256.fullmatch(digest) is None:
        raise CalibrationRoundError(
            f"Round {round_number} has no executable preregistration SHA-256"
        )
    return path, digest


def _load_preregistration(
    root: Path,
    design: Mapping[str, Any],
    *,
    round_number: int,
    preregistration_path: Path,
    preregistration_sha256: str,
) -> tuple[JsonObject, RoundBinding]:
    expected_path, expected_sha256 = _registered_round_identity(round_number)
    if (
        preregistration_path != expected_path
        or preregistration_sha256 != expected_sha256
    ):
        raise CalibrationRoundError("round preregistration path or SHA-256 is not registered")
    if _SHA256.fullmatch(preregistration_sha256) is None:
        raise CalibrationRoundError("round preregistration SHA-256 is invalid")
    path = (root / preregistration_path).resolve()
    if path.is_symlink() or not path.is_file() or _sha256_file(path) != preregistration_sha256:
        raise CalibrationRoundError(f"Round {round_number} preregistration bytes drifted")
    prereg = _load_json(path, f"Round {round_number} preregistration")
    base_keys = {
        "schema_version",
        "round_number",
        "pre_registered_at",
        "status",
        "design",
        "parent_preregistration",
        "clarification",
        "owner_review",
        "development_frame",
        "prompt",
        "models",
        "execution",
        "gates",
        "artifacts",
    }
    followup_round_keys = {
        "prior_round",
        "round_authorization",
        "candidate_prompt_summaries",
    }
    round_three_keys = {"post_round_governance"}
    _exact_keys(
        prereg,
        base_keys
        | (followup_round_keys if round_number in {2, 3} else set())
        | (round_three_keys if round_number == 3 else set()),
        f"Round {round_number} preregistration",
    )
    if (
        prereg.get("schema_version") != "p4.2a-development-calibration-round-preregistration-v1"
        or prereg.get("round_number") != round_number
        or prereg.get("status") != "preregistered_before_model_calls"
    ):
        raise CalibrationRoundError(f"Round {round_number} preregistration identity drifted")
    _parse_utc_timestamp(prereg.get("pre_registered_at"), "pre_registered_at")
    for name in ("design", "parent_preregistration", "clarification", "owner_review", "prompt"):
        _file_ref(root, prereg.get(name), f"preregistration.{name}")
    design_ref = _mapping(prereg.get("design"), "preregistration.design")
    if design_ref.get("sha256") != DESIGN_SHA256:
        raise CalibrationRoundError(f"Round {round_number} design binding drifted")
    frame = _mapping(prereg.get("development_frame"), "development_frame")
    _exact_keys(
        frame,
        {"frame_id", "expected_rows", "owner_export", "human_gold", "owner_completion"},
        "development_frame",
    )
    if (
        frame.get("frame_id") != "p4.2a-development-frame-v2"
        or frame.get("expected_rows") != EXPECTED_ROWS
    ):
        raise CalibrationRoundError(f"Round {round_number} development frame drifted")
    for name in ("owner_export", "human_gold", "owner_completion"):
        _file_ref(root, frame.get(name), f"development_frame.{name}")
    execution = _mapping(prereg.get("execution"), "execution")
    if (
        execution.get("fixed_model_order") != list(MODEL_ORDER)
        or execution.get("expected_predictions_per_model") != EXPECTED_ROWS
        or execution.get("automatic_retries") != 0
        or execution.get("failed_candidate_retries") != 0
        or execution.get("technical_failure_policy")
        != "model_result_gate_fails_without_denominator_exclusion_or_replacement"
        or execution.get("terminal_result_policy") != "append_once_even_when_failed"
    ):
        raise CalibrationRoundError(f"Round {round_number} execution contract drifted")
    gates = _mapping(prereg.get("gates"), "gates")
    expected_gate_keys = {
        "precision_minimum",
        "false_omission_rate_maximum",
        "recall_policy",
        "selection_rule",
    }
    if round_number in {2, 3}:
        expected_gate_keys.add("adverse_flip_margin")
    _exact_keys(gates, expected_gate_keys, f"Round {round_number} gates")
    if (
        gates.get("precision_minimum") != PRECISION_MINIMUM
        or gates.get("false_omission_rate_maximum") != FALSE_OMISSION_RATE_MAXIMUM
        or gates.get("recall_policy")
        != "omit_or_emit_null_not_estimable_never_emit_naive_numeric_recall"
        or gates.get("selection_rule") != "flash_if_both_gates_else_plus_if_both_gates_else_none"
    ):
        raise CalibrationRoundError(f"Round {round_number} gate contract drifted")
    margin_required = round_number in {2, 3}
    if margin_required:
        margin = _mapping(gates.get("adverse_flip_margin"), "gates.adverse_flip_margin")
        if dict(margin) != {
            "applies_to": "development_gate_only",
            "required": True,
            "precision_adverse_flip": "one_true_positive_reclassified_as_false_positive",
            "false_omission_adverse_flip": "one_true_negative_reclassified_as_false_negative",
            "point_estimates_must_also_pass": True,
            "heldout_gate_unchanged": True,
        }:
            raise CalibrationRoundError(
                f"Round {round_number} adverse-flip margin contract drifted"
            )
        _validate_followup_round_bindings(
            root,
            prereg,
            round_number,
            preregistration_sha256,
        )
    if round_number == 3:
        governance = _mapping(
            prereg.get("post_round_governance"), "post_round_governance"
        )
        if dict(governance) != ROUND_3_POST_ROUND_GOVERNANCE:
            raise CalibrationRoundError("Round 3 O-5 governance contract drifted")
    formal = _mapping(design.get("formal_development_rounds"), "formal_development_rounds")
    if (
        formal.get("models_always_measured") != list(MODEL_ORDER)
        or formal.get("automatic_retries") != 0
        or formal.get("failed_candidate_retries") != 0
        or formal.get("expected_predictions_per_model") != EXPECTED_ROWS
    ):
        raise CalibrationRoundError("evaluation design execution semantics drifted")
    prompt_ref = _mapping(prereg.get("prompt"), "preregistration.prompt")
    prompt_sha = prompt_ref.get("sha256")
    if not isinstance(prompt_sha, str) or _SHA256.fullmatch(prompt_sha) is None:
        raise CalibrationRoundError("round prompt SHA-256 binding is invalid")
    return prereg, RoundBinding(
        round_number=round_number,
        preregistration_path=preregistration_path,
        preregistration_sha256=preregistration_sha256,
        prompt_sha256=prompt_sha,
        margin_required=margin_required,
    )


def _load_contracts(
    root: Path,
    prereg: Mapping[str, Any],
    round_binding: RoundBinding,
) -> tuple[ContractBinding, ...]:
    models = prereg.get("models")
    if not isinstance(models, list) or len(models) != 2:
        raise CalibrationRoundError(
            f"Round {round_binding.round_number} must register exactly two models"
        )
    base_path = (root / BASE_CONTRACT_PATH).resolve()
    if base_path.is_symlink() or _sha256_file(base_path) != BASE_CONTRACT_SHA256:
        raise CalibrationRoundError("base extraction contract bytes drifted")
    base = load_event_extract_contract(base_path, project_root=root)
    prompt_path, prompt_sha = _file_ref(root, prereg.get("prompt"), "preregistration.prompt")
    prompt = prompt_path.read_text(encoding="utf-8")
    prompt_marker = f"[P4_NEWS_EVENT_EXTRACT v2-r{round_binding.round_number}]"
    if prompt_marker not in prompt or prompt_sha != round_binding.prompt_sha256:
        raise CalibrationRoundError(
            f"Round {round_binding.round_number} prompt marker or SHA binding drifted"
        )
    wrappers: list[JsonObject] = []
    bindings: list[ContractBinding] = []
    for expected_model, entry_value in zip(MODEL_ORDER, models, strict=True):
        entry = _mapping(entry_value, "models[]")
        _exact_keys(entry, {"model_slug", "model", "contract"}, "models[]")
        if entry.get("model_slug") != expected_model or entry.get("model") != expected_model:
            raise CalibrationRoundError(f"Round {round_binding.round_number} model order drifted")
        wrapper_path, wrapper_sha = _file_ref(
            root, entry.get("contract"), f"contract.{expected_model}"
        )
        try:
            value: object = yaml.safe_load(wrapper_path.read_bytes())
        except yaml.YAMLError as exc:
            raise CalibrationRoundError(
                f"Round {round_binding.round_number} model contract is invalid"
            ) from exc
        if not isinstance(value, dict):
            raise CalibrationRoundError(
                f"Round {round_binding.round_number} model contract must be a mapping"
            )
        wrapper = cast(JsonObject, value)
        _exact_keys(
            wrapper,
            {
                "schema_version",
                "round_number",
                "owner_spec_commit",
                "pre_registered_at",
                "production_writes_allowed",
                "heldout_access_allowed",
                "artifact_root",
                "extends_contract",
                "contract_files",
                "llm",
                "development_frame",
                "isolation",
            },
            f"contract.{expected_model}",
        )
        if (
            wrapper.get("schema_version")
            != (
                "p4.2a-development-event-extract-contract-"
                f"v2-r{round_binding.round_number}"
            )
            or wrapper.get("round_number") != round_binding.round_number
            or wrapper.get("production_writes_allowed") is not False
            or wrapper.get("heldout_access_allowed") is not False
            or wrapper.get("artifact_root") != "docs/phase4/eval/v2-calibration"
            or wrapper.get("pre_registered_at") != prereg.get("pre_registered_at")
        ):
            raise CalibrationRoundError(
                f"Round {round_binding.round_number} wrapper contract identity drifted"
            )
        extends = _mapping(wrapper.get("extends_contract"), "extends_contract")
        if (
            extends.get("path") != BASE_CONTRACT_PATH.as_posix()
            or extends.get("sha256") != BASE_CONTRACT_SHA256
            or extends.get("schema_version") != "p4.2a-event-extract-eval-v1.7"
        ):
            raise CalibrationRoundError(
                f"Round {round_binding.round_number} base contract binding drifted"
            )
        files = _mapping(wrapper.get("contract_files"), "contract_files")
        wrapper_prompt_path, wrapper_prompt_sha = _file_ref(
            root, files.get("prompt"), "contract.prompt"
        )
        if wrapper_prompt_path != prompt_path or wrapper_prompt_sha != prompt_sha:
            raise CalibrationRoundError(
                f"Round {round_binding.round_number} prompt binding drifted"
            )
        schema_path, _ = _file_ref(root, files.get("schema"), "contract.schema")
        materialized_path, _ = _file_ref(
            root, files.get("materialized_schema"), "contract.materialized_schema"
        )
        if (
            schema_path
            != (root / "config/schemas/p4_news_event_candidate_v1.schema.json").resolve()
            or materialized_path != (root / "config/schemas/p4_news_event_v1.schema.json").resolve()
        ):
            raise CalibrationRoundError(
                f"Round {round_binding.round_number} result schema binding drifted"
            )
        llm = _mapping(wrapper.get("llm"), "llm")
        if (
            llm.get("purpose") != "p4_news_event_extract"
            or llm.get("model") != expected_model
            or llm.get("endpoint") != "https://dashscope.aliyuncs.com/compatible-mode/v1"
            or llm.get("temperature") != 0.2
            or llm.get("enable_thinking") is not False
            or llm.get("max_output_tokens") != 2000
            or llm.get("total_deadline_seconds") != 20.0
            or llm.get("max_retries") != 0
            or llm.get("max_items_per_run") != EXPECTED_ROWS
            or llm.get("response_format") != "json_object"
            or _mapping(llm.get("explicit_cache"), "explicit_cache").get("enabled") is not False
        ):
            raise CalibrationRoundError(
                f"Round {round_binding.round_number} LLM controls drifted"
            )
        isolation = _mapping(wrapper.get("isolation"), "isolation")
        if dict(isolation) != {
            "production_database": "data/alphapilot.db",
            "open_mode": "read_only_query_only",
            "production_llm_calls_write": False,
            "heldout_access": False,
            "p4_2b_unlocked": False,
            "p4_3_unlocked": False,
            "proposals_or_orders_allowed": False,
        }:
            raise CalibrationRoundError(
                f"Round {round_binding.round_number} isolation contract drifted"
            )
        contract = replace(
            base,
            path=wrapper_path,
            sha256=wrapper_sha,
            prompt=prompt,
            model=expected_model,
            endpoint=cast(str, llm["endpoint"]),
            timeout=20.0,
            max_tokens=2000,
            max_retries=0,
            max_items_per_run=EXPECTED_ROWS,
        )
        wrappers.append(copy.deepcopy(wrapper))
        bindings.append(ContractBinding(expected_model, contract, wrapper))
    comparable = copy.deepcopy(wrappers)
    for wrapper in comparable:
        cast(JsonObject, wrapper["llm"])["model"] = "__MODEL__"
    if comparable[0] != comparable[1]:
        raise CalibrationRoundError(
            f"Round {round_binding.round_number} model contracts differ beyond llm.model"
        )
    return tuple(bindings)


def _load_gold(
    root: Path, prereg: Mapping[str, Any]
) -> tuple[list[JsonObject], list[ExtractRecord], dict[int, str]]:
    frame = _mapping(prereg.get("development_frame"), "development_frame")
    _, export_sha = _file_ref(root, frame.get("owner_export"), "owner_export")
    gold_path, gold_sha = _file_ref(root, frame.get("human_gold"), "human_gold")
    completion_path, completion_sha = _file_ref(
        root, frame.get("owner_completion"), "owner_completion"
    )
    if export_sha != OWNER_EXPORT_SHA256:
        raise CalibrationRoundError("owner export digest binding drifted")
    completion = _load_json(completion_path, "owner completion")
    if (
        completion.get("schema_version") != "p4.2a-v2-owner-completion-manifest-v1"
        or completion.get("frame_id") != "p4.2a-development-frame-v2"
        or completion.get("heldout_touched") is not False
    ):
        raise CalibrationRoundError("owner completion semantics drifted")
    execution = _mapping(completion.get("model_execution"), "owner completion model_execution")
    if (
        execution.get("evaluated_model_calls_before_calibration") != 0
        or execution.get("heldout_model_calls") != 0
        or execution.get("drafting_ai_is_evaluated_model") is not False
    ):
        raise CalibrationRoundError("owner completion model-call baseline drifted")
    artifacts = _mapping(completion.get("artifacts"), "owner completion artifacts")
    if (
        _mapping(artifacts.get("owner_raw_export"), "owner_raw_export").get("sha256") != export_sha
        or _mapping(artifacts.get("human_adjudicated"), "human_adjudicated").get("sha256")
        != gold_sha
        or completion_sha != "51282ca598c1f99c2755d2954a8fe820b1b1f583205a0ff20676b30e613ca02b"
    ):
        raise CalibrationRoundError("owner completion artifact binding drifted")
    selection_path, _ = _file_ref(
        root,
        artifacts.get("private_selection"),
        "owner completion private selection",
    )
    selection = _load_json(selection_path, "development private selection")
    selected = _mapping(selection.get("selection"), "development selection").get("selected")
    if not isinstance(selected, list) or len(selected) != EXPECTED_ROWS:
        raise CalibrationRoundError("development selection rows drifted")
    strata: dict[int, str] = {}
    for item_value in selected:
        item = _mapping(item_value, "development selection item")
        identifier = item.get("news_item_id")
        stratum = item.get("sampling_stratum")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier in strata
            or stratum not in {"predicted_positive", "predicted_negative"}
        ):
            raise CalibrationRoundError("development selection strata drifted")
        strata[identifier] = cast(str, stratum)
    rows = _load_jsonl(gold_path, "human-adjudicated dev45")
    if len(rows) != EXPECTED_ROWS:
        raise CalibrationRoundError("human-adjudicated dev frame must contain 45 rows")
    seen: set[int] = set()
    records: list[ExtractRecord] = []
    for row in rows:
        identifier = row.get("news_item_id")
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier in seen:
            raise CalibrationRoundError("human-adjudicated dev IDs are invalid or duplicated")
        seen.add(identifier)
        gold = _mapping(row.get("gold"), "human gold")
        symbols = gold.get("symbols")
        evidence = gold.get("evidence_span")
        original_text = row.get("original_text")
        if (
            row.get("frame_id") != "p4.2a-development-frame-v2"
            or row.get("annotation_status") != "completed"
            or not isinstance(symbols, list)
            or symbols != sorted(set(symbols))
            or any(
                not isinstance(item, str) or re.fullmatch(r"[0-9]{6}", item) is None
                for item in symbols
            )
            or gold.get("event_type") is None
            or gold.get("direction") not in (-1, 0, 1)
            or gold.get("materiality") not in (0, 1, 2, 3)
            or not isinstance(evidence, str)
            or not evidence
            or not isinstance(original_text, str)
            or evidence not in original_text
        ):
            raise CalibrationRoundError("human-adjudicated gold fields drifted")
        required_text = (
            "source",
            "title",
            "available_time",
            "body_state",
            "input_sha256",
            "text_sha256",
        )
        if any(
            not isinstance(row.get(field), str) or not cast(str, row[field])
            for field in required_text
        ):
            raise CalibrationRoundError("human-adjudicated input fields drifted")
        records.append(
            ExtractRecord(
                news_item_id=identifier,
                source=cast(str, row["source"]),
                ingested_symbol=cast(str | None, row.get("ingested_symbol")),
                title=cast(str, row["title"]),
                original_text=original_text,
                published_at=cast(str | None, row.get("published_at")),
                available_time=cast(str, row["available_time"]),
                body_state=cast(str, row["body_state"]),
                declared_input_sha256=cast(str, row["input_sha256"]),
                declared_text_sha256=cast(str, row["text_sha256"]),
            )
        )
    if seen != set(strata):
        raise CalibrationRoundError("human gold and private selection IDs differ")
    return rows, records, strata


def _production_snapshot(root: Path) -> ProductionSnapshot:
    path = (root / "data/alphapilot.db").resolve()
    if path.is_symlink() or not path.is_file():
        raise CalibrationRoundError("production database is unavailable")
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=15.0)
    try:
        connection.execute("PRAGMA query_only=ON")
        query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = {"securities", "llm_calls", "trade_proposals", "broker_orders"}
        if query_only != 1 or not required.issubset(tables):
            raise CalibrationRoundError("production database read-only safety tables drifted")
        llm_row = connection.execute("SELECT COUNT(*), MAX(id) FROM llm_calls").fetchone()
        proposals = int(connection.execute("SELECT COUNT(*) FROM trade_proposals").fetchone()[0])
        orders = int(connection.execute("SELECT COUNT(*) FROM broker_orders").fetchone()[0])
        non_simulate = int(
            connection.execute(
                "SELECT COUNT(*) FROM broker_orders WHERE environment <> 'SIMULATE'"
            ).fetchone()[0]
        )
        universe = frozenset(
            str(row[0])
            for row in connection.execute(
                "SELECT symbol FROM securities WHERE symbol GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'"
            )
        )
        if non_simulate or not universe:
            raise CalibrationRoundError("production trading or universe safety gate failed")
        return ProductionSnapshot(
            sqlite_uri_mode="ro",
            pragma_query_only=query_only,
            connection_total_changes=int(connection.total_changes),
            llm_call_count=int(llm_row[0]),
            llm_call_max_id=int(llm_row[1]) if llm_row[1] is not None else None,
            trade_proposal_count=proposals,
            broker_order_count=orders,
            non_simulate_order_count=non_simulate,
            news_events_table_exists="news_events" in tables,
            universe_symbols=universe,
        )
    finally:
        connection.close()


def _settings_safety(settings: Settings) -> JsonObject:
    snapshot: JsonObject = {
        "trading_mode": settings.trading_mode,
        "live_trading_enabled": settings.live_trading_enabled,
        "paper_trading_enabled": settings.paper_trading_enabled,
        "paper_auto_trading_enabled": settings.paper_auto_trading_enabled,
        "futu_enable_account_mutation": settings.futu_enable_account_mutation,
        "futu_enable_trade": settings.futu_enable_trade,
        "unlock_trade_permanently_blocked": heldout._settings_safety(settings)[
            "unlock_trade_permanently_blocked"
        ],
    }
    if snapshot != {
        "trading_mode": "research",
        "live_trading_enabled": False,
        "paper_trading_enabled": False,
        "paper_auto_trading_enabled": False,
        "futu_enable_account_mutation": False,
        "futu_enable_trade": False,
        "unlock_trade_permanently_blocked": True,
    }:
        raise CalibrationRoundError("research and trading safety gate is not closed")
    return snapshot


def _model_settings(base: Settings, contract: EventExtractContract) -> Settings:
    purpose_models = dict(base.llm_purpose_models)
    purpose_models[contract.purpose] = contract.model
    result = base.model_copy(
        update={
            "llm_base_url": contract.endpoint,
            "llm_model": contract.model,
            "llm_purpose_models": purpose_models,
        }
    )
    _validate_runtime_contract(contract, result)
    return result


def _score(
    rows: Sequence[Mapping[str, Any]],
    gold_rows: Sequence[Mapping[str, Any]],
    baseline_strata: Mapping[int, str],
    *,
    adverse_flip_margin_required: bool = False,
) -> JsonObject:
    gold_by_id = {
        cast(int, row["news_item_id"]): _mapping(row.get("gold"), "gold") for row in gold_rows
    }
    seen: set[int] = set()
    tp = fp = fn = tn = symbol_matches = comparable = 0
    symbol_bearing_matches = symbol_bearing_denominator = 0
    weighted = {"tp": 0.0, "fp": 0.0, "fn": 0.0, "tn": 0.0}
    by_stratum = {
        "predicted_positive": {"tp": 0, "fp": 0, "fn": 0, "tn": 0},
        "predicted_negative": {"tp": 0, "fp": 0, "fn": 0, "tn": 0},
    }
    weights = {
        "predicted_positive": 854 / 30,
        "predicted_negative": 2097 / 15,
    }
    baseline_counts = {
        stratum: sum(value == stratum for value in baseline_strata.values()) for stratum in weights
    }
    if baseline_counts != {"predicted_positive": 30, "predicted_negative": 15}:
        raise CalibrationRoundError("registered development stratum sizes drifted")
    failure_ids: list[int] = []
    for row in rows:
        identifier = row.get("news_item_id")
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, int)
            or identifier in seen
            or identifier not in gold_by_id
        ):
            raise CalibrationRoundError("prediction IDs are invalid, duplicated, or outside dev45")
        seen.add(identifier)
        if row.get("status") != "ok":
            failure_ids.append(identifier)
            continue
        prediction = _mapping(row.get("prediction"), "prediction")
        gold = gold_by_id[identifier]
        stratum = baseline_strata.get(identifier)
        if stratum not in weights:
            raise CalibrationRoundError("prediction lacks a registered baseline stratum")
        weight = weights[stratum]
        predicted_positive = prediction.get("materiality") in (2, 3)
        gold_positive = gold.get("materiality") in (2, 3)
        if predicted_positive and gold_positive:
            tp += 1
            by_stratum[stratum]["tp"] += 1
            weighted["tp"] += weight
        elif predicted_positive:
            fp += 1
            by_stratum[stratum]["fp"] += 1
            weighted["fp"] += weight
        elif gold_positive:
            fn += 1
            by_stratum[stratum]["fn"] += 1
            weighted["fn"] += weight
        else:
            tn += 1
            by_stratum[stratum]["tn"] += 1
            weighted["tn"] += weight
        comparable += 1
        if prediction.get("symbols") == gold.get("symbols"):
            symbol_matches += 1
        prediction_symbols = prediction.get("symbols")
        gold_symbols = gold.get("symbols")
        if prediction_symbols or gold_symbols:
            symbol_bearing_denominator += 1
            if prediction_symbols == gold_symbols:
                symbol_bearing_matches += 1
    missing_ids = sorted(set(gold_by_id) - seen)
    failure_ids.extend(missing_ids)
    positive_denominator = tp + fp
    negative_denominator = fn + tn
    precision = tp / positive_denominator if positive_denominator else None
    false_omission_rate = fn / negative_denominator if negative_denominator else None
    technical_complete = (
        len(rows) == EXPECTED_ROWS and comparable == EXPECTED_ROWS and not failure_ids
    )
    precision_passed = precision is not None and precision >= PRECISION_MINIMUM
    for_passed = (
        false_omission_rate is not None and false_omission_rate <= FALSE_OMISSION_RATE_MAXIMUM
    )
    adverse_precision = (tp - 1) / positive_denominator if tp and positive_denominator else None
    adverse_for = (fn + 1) / negative_denominator if tn and negative_denominator else None
    adverse_precision_passed = (
        adverse_precision is not None and adverse_precision >= PRECISION_MINIMUM
    )
    adverse_for_passed = (
        adverse_for is not None and adverse_for <= FALSE_OMISSION_RATE_MAXIMUM
    )
    point_estimate_passed = technical_complete and precision_passed and for_passed
    margin_passed = (
        technical_complete and adverse_precision_passed and adverse_for_passed
        if adverse_flip_margin_required
        else point_estimate_passed
    )
    weighted_positive = weighted["tp"] + weighted["fp"]
    weighted_negative = weighted["fn"] + weighted["tn"]
    stratum_breakdown: JsonObject = {}
    for name, counts in by_stratum.items():
        positive_size = counts["tp"] + counts["fp"]
        negative_size = counts["fn"] + counts["tn"]
        stratum_precision = counts["tp"] / positive_size if positive_size else None
        stratum_for = counts["fn"] / negative_size if negative_size else None
        stratum_adverse_precision = (
            (counts["tp"] - 1) / positive_size
            if counts["tp"] and positive_size
            else None
        )
        stratum_adverse_for = (
            (counts["fn"] + 1) / negative_size
            if counts["tn"] and negative_size
            else None
        )
        stratum_breakdown[f"baseline_{name}"] = {
            "sampling_frame": "development_frame_v2_baseline_stratified_30_positive_15_negative",
            "sampling_stratum": f"baseline_{name}",
            "registered_stratum_size": baseline_counts[name],
            "confusion_matrix": dict(counts),
            "materiality_precision": {
                "denominator": positive_size,
                "formula": "tp / (tp + fp)",
                "threshold": PRECISION_MINIMUM,
                "value": stratum_precision,
                "passed": (
                    stratum_precision is not None and stratum_precision >= PRECISION_MINIMUM
                ),
                "gate_or_diagnostic": "sampling_stratum_diagnostic",
            },
            "materiality_false_omission_rate": {
                "denominator": negative_size,
                "formula": "fn / (fn + tn)",
                "threshold": FALSE_OMISSION_RATE_MAXIMUM,
                "value": stratum_for,
                "passed": (
                    stratum_for is not None
                    and stratum_for <= FALSE_OMISSION_RATE_MAXIMUM
                ),
                "gate_or_diagnostic": "sampling_stratum_diagnostic",
            },
            "materiality_recall": {
                "denominator": "not_estimated",
                "formula": "not_estimable",
                "value": None,
                "passed": None,
                "gate_or_diagnostic": "omitted_not_estimable",
            },
            "adverse_single_item_margin": {
                "applies_to": "development_sampling_stratum_diagnostic",
                "required_by_overall_gate": adverse_flip_margin_required,
                "precision": {
                    "transformation": (
                        "one_true_positive_reclassified_as_false_positive"
                    ),
                    "denominator": positive_size,
                    "value": stratum_adverse_precision,
                    "threshold": PRECISION_MINIMUM,
                    "passed": (
                        stratum_adverse_precision is not None
                        and stratum_adverse_precision >= PRECISION_MINIMUM
                    ),
                },
                "false_omission_rate": {
                    "transformation": (
                        "one_true_negative_reclassified_as_false_negative"
                    ),
                    "denominator": negative_size,
                    "value": stratum_adverse_for,
                    "threshold": FALSE_OMISSION_RATE_MAXIMUM,
                    "passed": (
                        stratum_adverse_for is not None
                        and stratum_adverse_for <= FALSE_OMISSION_RATE_MAXIMUM
                    ),
                },
                "gate_or_diagnostic": "sampling_stratum_diagnostic",
                "may_not_override_overall_development_gate": True,
            },
            "may_not_override_overall_development_gate": True,
        }
    return {
        "sampling_frame": {
            "frame_id": "development_frame_v2_baseline_stratified_30_positive_15_negative",
            "total_size": EXPECTED_ROWS,
            "registered_strata": {
                "baseline_predicted_positive": baseline_counts["predicted_positive"],
                "baseline_predicted_negative": baseline_counts["predicted_negative"],
            },
            "candidate_output_partition": {
                "predicted_positive": positive_denominator,
                "predicted_negative": negative_denominator,
            },
        },
        "confusion_matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "baseline_stratum_breakdown": stratum_breakdown,
        "technical_completion": {
            "expected": EXPECTED_ROWS,
            "recorded": len(rows),
            "successful": comparable,
            "failure_count": len(failure_ids),
            "failure_ids": sorted(failure_ids),
            "passed": technical_complete,
            "failure_policy": FAILURE_POLICY,
        },
        "materiality_precision": {
            "metric_partition": "candidate_round_predicted_positive",
            "sampling_frame": "development_frame_v2_baseline_stratified_30_positive_15_negative",
            "sampling_strata": ["baseline_predicted_positive", "baseline_predicted_negative"],
            "denominator": positive_denominator,
            "formula": "tp / (tp + fp)",
            "gate_or_diagnostic": "development_gate",
            "tp": tp,
            "fp": fp,
            "value": precision,
            "threshold": PRECISION_MINIMUM,
            "passed": technical_complete and precision_passed,
        },
        "materiality_false_omission_rate": {
            "metric_partition": "candidate_round_predicted_negative",
            "sampling_frame": "development_frame_v2_baseline_stratified_30_positive_15_negative",
            "sampling_strata": ["baseline_predicted_positive", "baseline_predicted_negative"],
            "denominator": negative_denominator,
            "formula": "fn / (fn + tn)",
            "gate_or_diagnostic": "development_gate",
            "fn": fn,
            "tn": tn,
            "value": false_omission_rate,
            "threshold": FALSE_OMISSION_RATE_MAXIMUM,
            "passed": technical_complete and for_passed,
            "coarse_guardrail_disclosure": {
                "registered_negative_stratum_size": baseline_counts["predicted_negative"],
                "one_item_step": 1 / baseline_counts["predicted_negative"],
                "maximum_allowed_false_omissions_at_size_15": int(
                    FALSE_OMISSION_RATE_MAXIMUM * baseline_counts["predicted_negative"]
                ),
                "limitation_id": "L-3",
            },
        },
        "materiality_recall": {
            "metric_partition": "omitted_in_favor_of_false_omission_rate",
            "sampling_frame": "development_frame_v2_baseline_stratified_30_positive_15_negative",
            "sampling_strata": ["baseline_predicted_positive", "baseline_predicted_negative"],
            "denominator": "not_estimated",
            "formula": "not_estimable",
            "gate_or_diagnostic": "omitted_not_estimable",
            "value": None,
            "passed": None,
        },
        "symbol_exact_set_accuracy": {
            "metric_partition": "all_successful_candidate_predictions",
            "sampling_frame": "development_frame_v2_baseline_stratified_30_positive_15_negative",
            "sampling_strata": ["baseline_predicted_positive", "baseline_predicted_negative"],
            "denominator": comparable,
            "formula": "exact_symbol_set_matches / comparable_predictions",
            "gate_or_diagnostic": "development_diagnostic",
            "matches": symbol_matches,
            "value": symbol_matches / comparable if comparable else None,
            "passed": None,
        },
        "symbol_bearing_exact_set_accuracy": {
            "metric_partition": "gold_or_prediction_symbol_bearing_items",
            "sampling_frame": "development_frame_v2_baseline_stratified_30_positive_15_negative",
            "sampling_strata": ["baseline_predicted_positive", "baseline_predicted_negative"],
            "denominator": symbol_bearing_denominator,
            "formula": ("exact_symbol_set_matches_on_symbol_bearing_items / symbol_bearing_items"),
            "gate_or_diagnostic": "development_diagnostic",
            "matches": symbol_bearing_matches,
            "value": (
                symbol_bearing_matches / symbol_bearing_denominator
                if symbol_bearing_denominator
                else None
            ),
            "passed": None,
        },
        "source_pool_weighted_diagnostics": {
            "metric_partition": "candidate_round_predicted_output_partition",
            "gate_or_diagnostic": "diagnostic",
            "sampling_frame": "development_source_candidate_pool_after_retirement",
            "sampling_strata": ["baseline_predicted_positive", "baseline_predicted_negative"],
            "denominator": "inverse_probability_weighted_partition_total",
            "formula": {
                "materiality_precision": "weighted_tp / (weighted_tp + weighted_fp)",
                "materiality_false_omission_rate": "weighted_fn / (weighted_fn + weighted_tn)",
            },
            "estimator": "inverse_probability_weighted_by_baseline_sampling_stratum",
            "weights": weights,
            "materiality_precision": {
                "formula": "weighted_tp / (weighted_tp + weighted_fp)",
                "denominator": weighted_positive,
                "weighted_tp": weighted["tp"],
                "weighted_fp": weighted["fp"],
                "value": weighted["tp"] / weighted_positive if weighted_positive else None,
            },
            "materiality_false_omission_rate": {
                "formula": "weighted_fn / (weighted_fn + weighted_tn)",
                "denominator": weighted_negative,
                "weighted_fn": weighted["fn"],
                "weighted_tn": weighted["tn"],
                "value": weighted["fn"] / weighted_negative if weighted_negative else None,
            },
            "may_not_override_development_gate": True,
        },
        "point_estimate_materiality_gates_passed": point_estimate_passed,
        "adverse_single_item_margin": {
            "applies_to": "development_gate_only",
            "required": adverse_flip_margin_required,
            "heldout_gate_unchanged": True,
            "precision": {
                "transformation": "one_true_positive_reclassified_as_false_positive",
                "tp": tp - 1 if tp else None,
                "fp": fp + 1 if tp else None,
                "denominator": positive_denominator,
                "value": adverse_precision,
                "threshold": PRECISION_MINIMUM,
                "passed": technical_complete and adverse_precision_passed,
            },
            "false_omission_rate": {
                "transformation": "one_true_negative_reclassified_as_false_negative",
                "fn": fn + 1 if tn else None,
                "tn": tn - 1 if tn else None,
                "denominator": negative_denominator,
                "value": adverse_for,
                "threshold": FALSE_OMISSION_RATE_MAXIMUM,
                "passed": technical_complete and adverse_for_passed,
            },
            "both_passed": margin_passed,
        },
        "both_materiality_gates_passed": margin_passed,
    }


def _summary_dict(summary: ExtractionSummary) -> JsonObject:
    return {
        "expected_count": summary.expected_count,
        "success_count": summary.success_count,
        "failure_count": summary.failure_count,
        "newly_attempted_count": summary.newly_attempted_count,
        "retried_failure_count": summary.retried_failure_count,
        "skipped_exact_success_count": summary.skipped_exact_success_count,
        "skipped_failure_count": summary.skipped_failure_count,
        "output_line_count": summary.output_line_count,
        "failures_by_reason": summary.failures_by_reason,
        "failures_by_validation_field_and_constraint": (
            summary.failures_by_validation_field_and_constraint
        ),
        "isolated_audit_tables": list(summary.isolated_audit_tables),
        "isolated_audit_row_count": summary.isolated_audit_row_count,
        "checkpoint_audited_success_count": summary.checkpoint_audited_success_count,
    }


def _snapshot_evidence(snapshot: ProductionSnapshot) -> JsonObject:
    return {
        "sqlite_uri_mode": snapshot.sqlite_uri_mode,
        "pragma_query_only": snapshot.pragma_query_only,
        "connection_total_changes": snapshot.connection_total_changes,
        "llm_call_count": snapshot.llm_call_count,
        "llm_call_max_id": snapshot.llm_call_max_id,
        "trade_proposal_count": snapshot.trade_proposal_count,
        "broker_order_count": snapshot.broker_order_count,
        "non_simulate_order_count": snapshot.non_simulate_order_count,
        "news_events_table_exists": snapshot.news_events_table_exists,
        "universe_symbol_count": len(snapshot.universe_symbols),
    }


def _round_three_governance_outcome(
    reports: Mapping[str, Mapping[str, Any]],
    *,
    round_valid: bool = True,
) -> JsonObject:
    triggered = False
    if round_valid:
        for report in reports.values():
            metrics = report.get("metrics")
            if not isinstance(metrics, Mapping):
                continue
            margin = metrics.get("adverse_single_item_margin")
            if (
                metrics.get("point_estimate_materiality_gates_passed") is True
                and isinstance(margin, Mapping)
                and margin.get("both_passed") is False
            ):
                triggered = True
                break
    return {
        **ROUND_3_POST_ROUND_GOVERNANCE,
        "triggered": triggered,
        "next_action": (
            "return_frame_enlargement_decision_to_owner"
            if triggered
            else "await_independent_round_adjudication"
        ),
    }


def _artifact_paths(root: Path, model_slug: str, round_number: int = 1) -> Mapping[str, Path]:
    directory = (
        root
        / "docs/phase4/eval/v2-calibration/development/rounds"
        / f"r{round_number}"
        / model_slug
    )
    return {
        "predictions": directory / "predictions.jsonl",
        "manifest": directory / "manifest.json",
        "report": directory / "report.json",
        "terminal_state": directory / "terminal-state.jsonl",
    }


def _round_snapshot(
    root: Path,
    *,
    round_number: int,
    preregistration_sha256: str,
    outcome_sha256: str,
    artifact_sha256: Mapping[str, str],
) -> dict[str, str]:
    round_dir = (
        root
        / "docs/phase4/eval/v2-calibration/development/rounds"
        / f"r{round_number}"
    )
    anchored = {
        relative: _sha256_file(round_dir / relative)
        for relative in artifact_sha256
        if (round_dir / relative).is_file() and not (round_dir / relative).is_symlink()
    }
    if anchored != artifact_sha256:
        raise CalibrationRoundError(
            f"Round {round_number} immutable artifact anchor drifted"
        )
    prereg = round_dir / "round-preregistration.json"
    outcome_path = round_dir / "round-outcome.json"
    if (
        prereg.is_symlink()
        or outcome_path.is_symlink()
        or _sha256_file(prereg) != preregistration_sha256
        or _sha256_file(outcome_path) != outcome_sha256
    ):
        raise CalibrationRoundError(
            f"Round {round_number} immutable root artifacts drifted"
        )
    outcome = _load_json(outcome_path, f"Round {round_number} outcome")
    reports = _mapping(
        outcome.get("model_reports"), f"Round {round_number} model reports"
    )
    if (
        outcome.get("round_number") != round_number
        or outcome.get("round_preregistration_sha256") != preregistration_sha256
        or outcome.get("status") != "recorded"
        or outcome.get("technical_failure") is not None
        or outcome.get("selected_model") is not None
        or outcome.get("development_gate_cleared") is not False
        or outcome.get("heldout_touched") is not False
        or outcome.get("production_writes") is not False
        or set(reports) != set(MODEL_ORDER)
    ):
        raise CalibrationRoundError(
            f"Round {round_number} immutable outcome semantics drifted"
        )
    paths = [prereg, outcome_path]
    for model in MODEL_ORDER:
        model_paths = _artifact_paths(root, model, round_number)
        report_ref = _mapping(
            reports.get(model), f"Round {round_number} report ref {model}"
        )
        report = model_paths["report"]
        manifest = model_paths["manifest"]
        predictions = model_paths["predictions"]
        terminal = model_paths["terminal_state"]
        if any(path.is_symlink() or not path.is_file() for path in model_paths.values()):
            raise CalibrationRoundError(
                f"Round {round_number} immutable model artifact type drifted"
            )
        if (
            report_ref.get("path") != report.relative_to(root).as_posix()
            or report_ref.get("sha256") != _sha256_file(report)
        ):
            raise CalibrationRoundError(
                f"Round {round_number} report hash closure drifted"
            )
        manifest_doc = _load_json(manifest, f"Round {round_number} manifest {model}")
        if (
            manifest_doc.get("predictions_path") != predictions.relative_to(root).as_posix()
            or manifest_doc.get("predictions_sha256") != _sha256_file(predictions)
        ):
            raise CalibrationRoundError(
                f"Round {round_number} prediction hash closure drifted"
            )
        terminal_rows = _load_jsonl(
            terminal, f"Round {round_number} terminal {model}"
        )
        if (
            len(terminal_rows) != 2
            or terminal_rows[0].get("event") != "model_started"
            or terminal_rows[1].get("event") not in {"model_completed", "model_failed"}
            or terminal_rows[1].get("report_sha256") != _sha256_file(report)
            or terminal_rows[1].get("manifest_sha256") != _sha256_file(manifest)
        ):
            raise CalibrationRoundError(
                f"Round {round_number} terminal hash closure drifted"
            )
        paths.extend(model_paths.values())
    return {path.relative_to(root).as_posix(): _sha256_file(path) for path in paths}


def _round_one_snapshot(root: Path) -> dict[str, str]:
    return _round_snapshot(
        root,
        round_number=1,
        preregistration_sha256=ROUND_PREREGISTRATION_SHA256,
        outcome_sha256=ROUND_1_OUTCOME_SHA256,
        artifact_sha256=ROUND_1_ARTIFACT_SHA256,
    )


def _completed_history_snapshot(root: Path, last_round: int) -> dict[str, str]:
    registrations = {
        1: (
            ROUND_PREREGISTRATION_SHA256,
            ROUND_1_OUTCOME_SHA256,
            ROUND_1_ARTIFACT_SHA256,
        ),
        2: (
            ROUND_2_PREREGISTRATION_SHA256,
            ROUND_2_OUTCOME_SHA256,
            ROUND_2_ARTIFACT_SHA256,
        ),
    }
    snapshot: dict[str, str] = {}
    for completed_round in range(1, last_round + 1):
        try:
            preregistration_sha256, outcome_sha256, artifacts = registrations[
                completed_round
            ]
        except KeyError as exc:
            raise CalibrationRoundError("completed round has no immutable anchor") from exc
        snapshot.update(
            _round_snapshot(
                root,
                round_number=completed_round,
                preregistration_sha256=preregistration_sha256,
                outcome_sha256=outcome_sha256,
                artifact_sha256=artifacts,
            )
        )
    return snapshot


def _validate_calibration_history(
    root: Path, state_path: Path, round_number: int
) -> dict[str, str]:
    if round_number == 1:
        if state_path.exists() or state_path.is_symlink():
            raise CalibrationRoundError("Round 1 create-only state already exists")
        return {}
    if round_number not in {2, 3} or state_path.is_symlink() or not state_path.is_file():
        raise CalibrationRoundError(
            "only a registered follow-up round may append to the calibration state"
        )
    completed_round = round_number - 1
    expected_state_sha256 = (
        ROUND_1_STATE_PREFIX_SHA256
        if completed_round == 1
        else ROUND_2_STATE_PREFIX_SHA256
    )
    if _sha256_file(state_path) != expected_state_sha256:
        raise CalibrationRoundError(
            f"Round {completed_round} append-only state prefix drifted"
        )
    events = _load_jsonl(state_path, "calibration state")
    expected_preregistrations = {
        1: ROUND_PREREGISTRATION_SHA256,
        2: ROUND_2_PREREGISTRATION_SHA256,
    }
    expected_outcomes = {1: ROUND_1_OUTCOME_SHA256, 2: ROUND_2_OUTCOME_SHA256}
    if len(events) != completed_round * 2:
        raise CalibrationRoundError(
            f"calibration state does not authorize Round {round_number}"
        )
    for index, prior_round in enumerate(range(1, completed_round + 1)):
        started = events[index * 2]
        terminal = events[index * 2 + 1]
        execution_id = started.get("execution_id")
        if (
            started.get("round_number") != prior_round
            or started.get("event") != "round_started"
            or started.get("round_preregistration_sha256")
            != expected_preregistrations[prior_round]
            or started.get("heldout_touched") is not False
            or started.get("production_writes") is not False
            or terminal.get("round_number") != prior_round
            or terminal.get("event") != "round_completed"
            or terminal.get("round_outcome_sha256") != expected_outcomes[prior_round]
            or terminal.get("selected_model") is not None
            or terminal.get("heldout_touched") is not False
            or terminal.get("production_writes") is not False
            or (
                prior_round > 1
                and (
                    not isinstance(execution_id, str)
                    or not execution_id
                    or terminal.get("execution_id") != execution_id
                )
            )
        ):
            raise CalibrationRoundError(
                f"calibration state does not authorize Round {round_number}"
            )
    return _completed_history_snapshot(root, completed_round)


def _append_calibration_event(
    path: Path,
    event: Mapping[str, Any],
    *,
    expected_event_count: int,
    expected_last_event: str,
    expected_prefix_sha256: str | None = None,
    expected_last_round_number: int | None = None,
    expected_last_execution_id: str | None = None,
    expected_last_preregistration_sha256: str | None = None,
) -> None:
    if path.is_symlink() or not path.is_file():
        raise CalibrationRoundError("append-only calibration state is unavailable")
    flags = os.O_WRONLY | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise CalibrationRoundError("append-only calibration state is not a regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        rows = _load_jsonl(path, "append-only calibration state")
        if (
            expected_prefix_sha256 is not None
            and _sha256_file(path) != expected_prefix_sha256
        ):
            raise CalibrationRoundError("calibration state prefix drifted under append lock")
        if len(rows) != expected_event_count or rows[-1].get("event") != expected_last_event:
            raise CalibrationRoundError("append-only calibration state transition is invalid")
        last = rows[-1]
        if (
            expected_last_round_number is not None
            and last.get("round_number") != expected_last_round_number
        ):
            raise CalibrationRoundError("append-only calibration round ownership drifted")
        if (
            expected_last_execution_id is not None
            and last.get("execution_id") != expected_last_execution_id
        ):
            raise CalibrationRoundError("append-only calibration execution ownership drifted")
        if (
            expected_last_preregistration_sha256 is not None
            and last.get("round_preregistration_sha256")
            != expected_last_preregistration_sha256
        ):
            raise CalibrationRoundError(
                "append-only calibration preregistration ownership drifted"
            )
        payload = _canonical_json_bytes(event)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise CalibrationRoundError("failed to append calibration state")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _run_round_once(
    *,
    execution_id: str,
    round_number: int = 1,
    round_preregistration: Path = ROUND_PREREGISTRATION_PATH,
    round_preregistration_sha256: str = ROUND_PREREGISTRATION_SHA256,
    project_root: Path = PROJECT_ROOT,
    settings: Settings | None = None,
    chat_json_fn: ChatJsonCallable | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RoundResult:
    """Run one explicitly registered dev45 round, always flash then plus, with zero retry."""

    root = project_root.resolve()
    active_clock = clock or (lambda: datetime.now(UTC))
    design = _load_design(root)
    prereg, round_binding = _load_preregistration(
        root,
        design,
        round_number=round_number,
        preregistration_path=round_preregistration,
        preregistration_sha256=round_preregistration_sha256,
    )
    contracts = _load_contracts(root, prereg, round_binding)
    gold_rows, records, baseline_strata = _load_gold(root, prereg)
    artifact_root = (root / "docs/phase4/eval/v2-calibration").resolve()
    heldout_root = artifact_root / "heldout"
    if heldout_root.exists() or heldout_root.is_symlink():
        raise CalibrationRoundError("v2 heldout root must remain absent during development")
    outcome_path = (
        root
        / "docs/phase4/eval/v2-calibration/development/rounds"
        / f"r{round_number}"
        / "round-outcome.json"
    )
    state_path = root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl"
    prior_snapshot = _validate_calibration_history(root, state_path, round_number)
    prior_event_count = (round_number - 1) * 2
    prior_state_sha256 = (
        None
        if round_number == 1
        else (
            ROUND_1_STATE_PREFIX_SHA256
            if round_number == 2
            else ROUND_2_STATE_PREFIX_SHA256
        )
    )
    all_paths = [outcome_path]
    for binding in contracts:
        all_paths.extend(_artifact_paths(root, binding.model_slug, round_number).values())
    if any(path.exists() or path.is_symlink() for path in all_paths):
        raise CalibrationRoundError(f"Round {round_number} create-only output already exists")

    base_settings = settings or _settings_from_project_env(root)
    settings_safety = _settings_safety(base_settings)
    if not (base_settings.llm_api_key or "").strip():
        raise CalibrationRoundError("LLM API key is unavailable")
    settings_by_model = {
        binding.model_slug: _model_settings(base_settings, binding.contract)
        for binding in contracts
    }
    for binding in contracts:
        prepared_records = _prepare_records(binding.contract, records)
        if len(prepared_records) != EXPECTED_ROWS:
            raise CalibrationRoundError("prepared development record count drifted")
    before = _production_snapshot(root)
    if before.news_events_table_exists or before.connection_total_changes != 0:
        raise CalibrationRoundError("production database isolation gate failed")
    env_path = root / ".env"
    env_sha_before = _sha256_file(env_path) if env_path.is_file() else None
    started_at = _utc_now(active_clock)
    if _parse_utc_timestamp(prereg.get("pre_registered_at"), "pre_registered_at") >= (
        _parse_utc_timestamp(started_at, "round_started_at")
    ):
        raise CalibrationRoundError("round preregistration must predate round execution")
    start_event = {
                "schema_version": "p4.2a-v2-development-calibration-state-v1",
                "event": "round_started",
                "execution_id": execution_id,
                "round_number": round_number,
                "at_utc": started_at,
                "design_sha256": DESIGN_SHA256,
                "round_preregistration_sha256": round_binding.preregistration_sha256,
                "owner_export_sha256": OWNER_EXPORT_SHA256,
                "fixed_model_order": list(MODEL_ORDER),
                "expected_predictions_per_model": EXPECTED_ROWS,
                "automatic_retries": 0,
                "heldout_touched": False,
                "production_writes": False,
                "settings_safety": settings_safety,
                "production_before": _snapshot_evidence(before),
            }
    if round_number == 1:
        _create_only(state_path, _canonical_json_bytes(start_event), artifact_root)
    else:
        _append_calibration_event(
            state_path,
            start_event,
            expected_event_count=prior_event_count,
            expected_last_event="round_completed",
            expected_prefix_sha256=prior_state_sha256,
            expected_last_round_number=round_number - 1,
        )

    reports: dict[str, JsonObject] = {}
    fatal: BaseException | None = None
    for binding in contracts:
        paths = _artifact_paths(root, binding.model_slug, round_number)
        model_started_at = _utc_now(active_clock)
        _create_only(
            paths["terminal_state"],
            _canonical_json_bytes(
                {
                    "schema_version": "p4.2a-v2-development-model-state-v1",
                    "event": "model_started",
                    "round_number": round_number,
                    "model": binding.model_slug,
                    "at_utc": model_started_at,
                    "contract_sha256": binding.contract.sha256,
                    "expected_count": EXPECTED_ROWS,
                    "max_retries": 0,
                }
            ),
            artifact_root,
        )
        try:
            model_settings = settings_by_model[binding.model_slug]
            summary = extract_records(
                binding.contract,
                records,
                output_path=paths["predictions"],
                eval_root=artifact_root,
                universe_symbols=before.universe_symbols,
                settings=model_settings,
                retry_failures=False,
                chat_json_fn=chat_json_fn,
            )
            prediction_rows = _load_jsonl(
                paths["predictions"], f"Round {round_number} predictions"
            )
            metrics = _score(
                prediction_rows,
                gold_rows,
                baseline_strata,
                adverse_flip_margin_required=round_binding.margin_required,
            )
            manifest: JsonObject = {
                "schema_version": "p4.2a-v2-development-model-manifest-v1",
                "round_number": round_number,
                "model": binding.model_slug,
                "design_sha256": DESIGN_SHA256,
                "round_preregistration_sha256": round_binding.preregistration_sha256,
                "owner_export_sha256": OWNER_EXPORT_SHA256,
                "human_gold_sha256": HUMAN_GOLD_SHA256,
                "contract_sha256": binding.contract.sha256,
                "prompt_sha256": round_binding.prompt_sha256,
                "predictions_path": paths["predictions"].relative_to(root).as_posix(),
                "predictions_sha256": _sha256_file(paths["predictions"]),
                "row_count": len(prediction_rows),
                "success_count": summary.success_count,
                "failure_count": summary.failure_count,
                "completed_at_utc": _utc_now(active_clock),
                "production_writes": False,
                "heldout_touched": False,
            }
            _create_only(paths["manifest"], _canonical_json_bytes(manifest), artifact_root)
            report: JsonObject = {
                "schema_version": "p4.2a-v2-development-model-report-v1",
                "round_number": round_number,
                "model": binding.model_slug,
                "status": "completed",
                "design_sha256": DESIGN_SHA256,
                "round_preregistration_sha256": round_binding.preregistration_sha256,
                "contract_sha256": binding.contract.sha256,
                "prompt_sha256": round_binding.prompt_sha256,
                "owner_export_sha256": OWNER_EXPORT_SHA256,
                "started_at_utc": model_started_at,
                "completed_at_utc": _utc_now(active_clock),
                "extraction": _summary_dict(summary),
                "metrics": metrics,
                "gate_passed": metrics["both_materiality_gates_passed"],
                "selection_policy": "absolute_gates_not_relative_model_ranking",
                "o3_boundary_policy": O3_BOUNDARY_POLICY,
                "heldout_touched": False,
                "production_writes": False,
            }
            _create_only(paths["report"], _canonical_json_bytes(report), artifact_root)
            reports[binding.model_slug] = report
            _append_terminal(
                paths["terminal_state"],
                {
                    "schema_version": "p4.2a-v2-development-model-state-v1",
                    "event": "model_completed",
                    "round_number": round_number,
                    "model": binding.model_slug,
                    "at_utc": _utc_now(active_clock),
                    "report_sha256": _sha256_file(paths["report"]),
                    "manifest_sha256": _sha256_file(paths["manifest"]),
                    "success_count": summary.success_count,
                    "failure_count": summary.failure_count,
                    "gate_passed": report["gate_passed"],
                },
                expected_start="model_started",
            )
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            fatal = fatal or error
            if not paths["predictions"].exists():
                _create_only(paths["predictions"], b"", artifact_root)
            rows = _load_jsonl(paths["predictions"], "failed model predictions", allow_empty=True)
            manifest = {
                "schema_version": "p4.2a-v2-development-model-manifest-v1",
                "round_number": round_number,
                "model": binding.model_slug,
                "design_sha256": DESIGN_SHA256,
                "round_preregistration_sha256": round_binding.preregistration_sha256,
                "contract_sha256": binding.contract.sha256,
                "predictions_path": paths["predictions"].relative_to(root).as_posix(),
                "predictions_sha256": _sha256_file(paths["predictions"]),
                "row_count": len(rows),
                "success_count": sum(row.get("status") == "ok" for row in rows),
                "failure_count": EXPECTED_ROWS,
                "technical_failure": _safe_error(error),
                "raw_exception_or_payload_persisted": False,
                "completed_at_utc": _utc_now(active_clock),
                "production_writes": False,
                "heldout_touched": False,
            }
            if not paths["manifest"].exists():
                _create_only(paths["manifest"], _canonical_json_bytes(manifest), artifact_root)
            report = {
                "schema_version": "p4.2a-v2-development-model-report-v1",
                "round_number": round_number,
                "model": binding.model_slug,
                "status": "technical_failed",
                "technical_failure": _safe_error(error),
                "raw_exception_or_payload_persisted": False,
                "metrics": {
                    "materiality_precision": {"value": None, "passed": False},
                    "materiality_false_omission_rate": {"value": None, "passed": False},
                    "materiality_recall": {
                        "value": None,
                        "formula": "not_estimable",
                        "passed": None,
                    },
                    "both_materiality_gates_passed": False,
                },
                "gate_passed": False,
                "failure_policy": FAILURE_POLICY,
                "heldout_touched": False,
                "production_writes": False,
            }
            if not paths["report"].exists():
                _create_only(paths["report"], _canonical_json_bytes(report), artifact_root)
            reports[binding.model_slug] = report
            _append_terminal(
                paths["terminal_state"],
                {
                    "schema_version": "p4.2a-v2-development-model-state-v1",
                    "event": "model_failed",
                    "round_number": round_number,
                    "model": binding.model_slug,
                    "at_utc": _utc_now(active_clock),
                    "error": _safe_error(error),
                    "raw_exception_or_payload_persisted": False,
                    "gate_passed": False,
                },
                expected_start="model_started",
            )

    selected: str | None = None
    if reports.get(MODEL_ORDER[0], {}).get("gate_passed") is True:
        selected = MODEL_ORDER[0]
    elif reports.get(MODEL_ORDER[1], {}).get("gate_passed") is True:
        selected = MODEL_ORDER[1]
    after = _production_snapshot(root)
    env_sha_after = _sha256_file(env_path) if env_path.is_file() else None
    if (
        after.llm_call_count != before.llm_call_count
        or after.llm_call_max_id != before.llm_call_max_id
        or after.trade_proposal_count != before.trade_proposal_count
        or after.broker_order_count != before.broker_order_count
        or after.non_simulate_order_count != before.non_simulate_order_count
        or after.news_events_table_exists
        or env_sha_after != env_sha_before
        or heldout_root.exists()
        or heldout_root.is_symlink()
    ):
        fatal = fatal or CalibrationRoundError("post-run production isolation evidence drifted")
        selected = None
    if (
        prior_snapshot
        and _completed_history_snapshot(root, round_number - 1) != prior_snapshot
    ):
        fatal = fatal or CalibrationRoundError(
            f"immutable artifacts changed during Round {round_number}"
        )
        selected = None
    outcome: JsonObject = {
        "schema_version": "p4.2a-v2-development-round-outcome-v1",
        "round_number": round_number,
        "execution_id": execution_id,
        "status": "technical_failed" if fatal is not None else "recorded",
        "started_at_utc": started_at,
        "completed_at_utc": _utc_now(active_clock),
        "design_sha256": DESIGN_SHA256,
        "round_preregistration_sha256": round_binding.preregistration_sha256,
        "owner_export_sha256": OWNER_EXPORT_SHA256,
        "fixed_model_order": list(MODEL_ORDER),
        "models_always_measured": list(reports),
        "model_reports": {
            model: {
                "path": _artifact_paths(root, model, round_number)["report"]
                .relative_to(root)
                .as_posix(),
                "sha256": _sha256_file(_artifact_paths(root, model, round_number)["report"]),
                "gate_passed": report.get("gate_passed"),
            }
            for model, report in reports.items()
        },
        "selection_rule": "flash_if_both_gates_else_plus_if_both_gates_else_none",
        "selected_model": selected,
        "development_gate_cleared": selected is not None and fatal is None,
        "materiality_recall_policy": "not_estimable_omitted_in_favor_of_false_omission_rate",
        "technical_failure": _safe_error(fatal) if fatal is not None else None,
        "raw_exception_or_payload_persisted": False,
        "production_before": _snapshot_evidence(before),
        "production_after": _snapshot_evidence(after),
        "production_llm_calls_delta": after.llm_call_count - before.llm_call_count,
        "environment_sha256_before": env_sha_before,
        "environment_sha256_after": env_sha_after,
        "settings_safety": settings_safety,
        "production_writes": False,
        "heldout_touched": False,
        "p4_2a_done": False,
        "p4_2b_unlocked": False,
        "p4_3_unlocked": False,
    }
    if round_number == 3:
        outcome["post_round_governance"] = _round_three_governance_outcome(
            reports,
            round_valid=fatal is None,
        )
    _create_only(outcome_path, _canonical_json_bytes(outcome), artifact_root)
    terminal_event = {
            "schema_version": "p4.2a-v2-development-calibration-state-v1",
            "event": "round_failed" if fatal is not None else "round_completed",
            "execution_id": execution_id,
            "round_number": round_number,
            "at_utc": _utc_now(active_clock),
            "round_outcome_sha256": _sha256_file(outcome_path),
            "selected_model": selected,
            "technical_failure": _safe_error(fatal) if fatal is not None else None,
            "raw_exception_or_payload_persisted": False,
            "heldout_touched": False,
            "production_writes": False,
        }
    if round_number == 1:
        _append_terminal(state_path, terminal_event, expected_start="round_started")
    else:
        _append_calibration_event(
            state_path,
            terminal_event,
            expected_event_count=prior_event_count + 1,
            expected_last_event="round_started",
            expected_last_round_number=round_number,
            expected_last_execution_id=execution_id,
            expected_last_preregistration_sha256=round_binding.preregistration_sha256,
        )
    result = RoundResult(outcome_path, state_path, selected, reports)
    if fatal is not None:
        raise CalibrationRoundError(
            f"Round {round_number} recorded a terminal technical failure"
        ) from fatal
    return result


def _recover_model_evidence(
    root: Path,
    *,
    round_number: int,
    preregistration_sha256: str,
) -> tuple[JsonObject, JsonObject]:
    complete: JsonObject = {}
    partial: JsonObject = {}
    for model in MODEL_ORDER:
        paths = _artifact_paths(root, model, round_number)
        existing = {
            name: {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256_file(path),
            }
            for name, path in paths.items()
            if path.is_file() and not path.is_symlink()
        }
        if not existing:
            continue
        if set(existing) != set(paths):
            partial[model] = {"closure": "partial", "artifacts": existing}
            continue
        try:
            report = _load_json(paths["report"], f"recovered report {model}")
            manifest = _load_json(paths["manifest"], f"recovered manifest {model}")
            terminal = _load_jsonl(paths["terminal_state"], f"recovered terminal {model}")
            if (
                report.get("round_number") != round_number
                or report.get("model") != model
                or report.get("round_preregistration_sha256") != preregistration_sha256
                or manifest.get("round_number") != round_number
                or manifest.get("model") != model
                or manifest.get("round_preregistration_sha256") != preregistration_sha256
                or manifest.get("predictions_path")
                != paths["predictions"].relative_to(root).as_posix()
                or manifest.get("predictions_sha256") != _sha256_file(paths["predictions"])
                or len(terminal) != 2
                or terminal[0].get("event") != "model_started"
                or terminal[1].get("event") not in {"model_completed", "model_failed"}
                or terminal[1].get("report_sha256") != _sha256_file(paths["report"])
                or terminal[1].get("manifest_sha256") != _sha256_file(paths["manifest"])
            ):
                raise CalibrationRoundError("recovered model hash closure drifted")
        except CalibrationRoundError:
            partial[model] = {"closure": "invalid", "artifacts": existing}
            continue
        complete[model] = {
            "path": paths["report"].relative_to(root).as_posix(),
            "sha256": _sha256_file(paths["report"]),
            "gate_passed": report.get("gate_passed"),
            "manifest_sha256": _sha256_file(paths["manifest"]),
            "predictions_sha256": _sha256_file(paths["predictions"]),
            "terminal_state_sha256": _sha256_file(paths["terminal_state"]),
            "closure": "complete",
        }
    return complete, partial


def _terminalize_interrupted_round(
    *,
    root: Path,
    round_number: int,
    preregistration_sha256: str,
    execution_id: str,
    error: BaseException,
) -> None:
    artifact_root = (root / "docs/phase4/eval/v2-calibration").resolve()
    state_path = artifact_root / "development/calibration.state.jsonl"
    if state_path.is_symlink() or not state_path.is_file():
        return
    events = _load_jsonl(state_path, "calibration state during fail-closed terminalization")
    if (
        not events
        or events[-1].get("round_number") != round_number
        or events[-1].get("event") != "round_started"
        or events[-1].get("execution_id") != execution_id
        or events[-1].get("round_preregistration_sha256") != preregistration_sha256
    ):
        return
    expected_count = (round_number - 1) * 2 + 1
    if len(events) != expected_count:
        raise CalibrationRoundError("cannot safely terminalize unexpected calibration state")
    outcome_path = (
        artifact_root
        / "development/rounds"
        / f"r{round_number}"
        / "round-outcome.json"
    )
    if not outcome_path.exists():
        model_reports, partial_model_artifacts = _recover_model_evidence(
            root,
            round_number=round_number,
            preregistration_sha256=preregistration_sha256,
        )
        terminal_outcome: JsonObject = {
            "schema_version": "p4.2a-v2-development-round-outcome-v1",
            "round_number": round_number,
            "execution_id": execution_id,
            "status": "technical_failed",
            "started_at_utc": events[-1].get("at_utc"),
            "completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "design_sha256": DESIGN_SHA256,
            "round_preregistration_sha256": preregistration_sha256,
            "fixed_model_order": list(MODEL_ORDER),
            "models_always_measured": list(model_reports),
            "model_reports": model_reports,
            "partial_model_artifacts": partial_model_artifacts,
            "selection_rule": "flash_if_both_gates_else_plus_if_both_gates_else_none",
            "selected_model": None,
            "development_gate_cleared": False,
            "technical_failure": _safe_error(error),
            "raw_exception_or_payload_persisted": False,
            "production_writes": False,
            "heldout_touched": False,
            "p4_2a_done": False,
            "p4_2b_unlocked": False,
            "p4_3_unlocked": False,
            "terminalization": "outer_fail_closed_guard",
        }
        if round_number == 3:
            terminal_outcome["post_round_governance"] = _round_three_governance_outcome(
                {},
                round_valid=False,
            )
        _create_only(
            outcome_path,
            _canonical_json_bytes(terminal_outcome),
            artifact_root,
        )
    if outcome_path.is_symlink() or not outcome_path.is_file():
        raise CalibrationRoundError("fail-closed outcome is not a regular file")
    outcome = _load_json(outcome_path, "outcome during terminal recovery")
    if (
        outcome.get("round_number") != round_number
        or outcome.get("execution_id") != execution_id
        or outcome.get("round_preregistration_sha256") != preregistration_sha256
        or outcome.get("status") not in {"recorded", "technical_failed"}
    ):
        raise CalibrationRoundError("existing outcome cannot authorize terminal recovery")
    completed = outcome.get("status") == "recorded"
    selected_model = outcome.get("selected_model") if completed else None
    if selected_model is not None and selected_model not in MODEL_ORDER:
        raise CalibrationRoundError("existing outcome selected-model identity drifted")
    terminal_event = {
        "schema_version": "p4.2a-v2-development-calibration-state-v1",
        "event": "round_completed" if completed else "round_failed",
        "execution_id": execution_id,
        "round_number": round_number,
        "at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "round_outcome_sha256": _sha256_file(outcome_path),
        "selected_model": selected_model,
        "technical_failure": None if completed else _safe_error(error),
        "raw_exception_or_payload_persisted": False,
        "heldout_touched": False,
        "production_writes": False,
        "terminalization": (
            "recovered_terminal_append_for_recorded_outcome"
            if completed
            else "outer_fail_closed_guard"
        ),
    }
    _append_calibration_event(
        state_path,
        terminal_event,
        expected_event_count=expected_count,
        expected_last_event="round_started",
        expected_last_round_number=round_number,
        expected_last_execution_id=execution_id,
        expected_last_preregistration_sha256=preregistration_sha256,
    )


def run_round(
    *,
    round_number: int = 1,
    round_preregistration: Path = ROUND_PREREGISTRATION_PATH,
    round_preregistration_sha256: str = ROUND_PREREGISTRATION_SHA256,
    project_root: Path = PROJECT_ROOT,
    settings: Settings | None = None,
    chat_json_fn: ChatJsonCallable | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RoundResult:
    """Run a registered round and fail-closed terminalize any consumed start event."""

    root = project_root.resolve()
    execution_id = hashlib.sha256(os.urandom(32)).hexdigest()
    try:
        return _run_round_once(
            execution_id=execution_id,
            round_number=round_number,
            round_preregistration=round_preregistration,
            round_preregistration_sha256=round_preregistration_sha256,
            project_root=root,
            settings=settings,
            chat_json_fn=chat_json_fn,
            clock=clock,
        )
    except BaseException as error:
        try:
            _terminalize_interrupted_round(
                root=root,
                round_number=round_number,
                preregistration_sha256=round_preregistration_sha256,
                execution_id=execution_id,
                error=error,
            )
        except BaseException as terminal_error:
            error.add_note(
                "fail-closed terminalization also failed: "
                f"{type(terminal_error).__name__}"
            )
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one registered P4.2a v2 dev45 round")
    parser.add_argument("--round-number", type=int, default=2)
    parser.add_argument(
        "--round-preregistration",
        type=Path,
        default=ROUND_2_PREREGISTRATION_PATH,
    )
    parser.add_argument(
        "--round-preregistration-sha256",
        default=ROUND_2_PREREGISTRATION_SHA256,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = run_round(
            round_number=arguments.round_number,
            round_preregistration=arguments.round_preregistration,
            round_preregistration_sha256=arguments.round_preregistration_sha256,
        )
    except (CalibrationRoundError, FileExistsError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "outcome_path": result.outcome_path.relative_to(PROJECT_ROOT).as_posix(),
                "state_path": result.calibration_state_path.relative_to(PROJECT_ROOT).as_posix(),
                "selected_model": result.selected_model,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
