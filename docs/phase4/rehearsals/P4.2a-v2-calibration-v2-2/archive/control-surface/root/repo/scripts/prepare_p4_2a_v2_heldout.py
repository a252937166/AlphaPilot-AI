from __future__ import annotations

import argparse
import copy
import fcntl
import json
import math
import os
import re
import shutil
import stat
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn, cast
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if __package__ in {None, ""}:
    sys.path[:0] = [str(PROJECT_ROOT), str(PROJECT_ROOT / "src")]

import yaml  # noqa: E402
from jsonschema import Draft202012Validator, FormatChecker  # noqa: E402
from scripts import build_p4_2a_gold_sample as gold_builder  # noqa: E402
from scripts import p4_2a_v2_dev_common as common  # noqa: E402
from scripts import run_p4_2a_offline_extract as offline_extract  # noqa: E402
from scripts import run_p4_2a_v2_dev_calibration as dev_runner  # noqa: E402
from scripts.run_p4_2a_heldout_predictions import HeldoutPredictionError  # noqa: E402
from scripts.run_p4_2a_offline_extract import (  # noqa: E402
    DECLARED_INPUT_LEGACY_V1,
    ChatJsonCallable,
    ExtractRecord,
    MonotonicNsClock,
    RecordedAtClock,
    _settings_from_project_env,
    extract_records,
)

from alphapilot.core.config import Settings  # noqa: E402
from alphapilot.db import backup as database_backup  # noqa: E402
from alphapilot.llm.p4_news_eval import load_event_evaluation_design  # noqa: E402
from alphapilot.llm.p4_news_event import (  # noqa: E402
    EventExtractContract,
    EventExtractValidationError,
)

JsonObject = dict[str, Any]
Clock = Callable[[], datetime]
ExecutionIdFactory = Callable[[], str]
MonotonicClock = Callable[[], float]
Sleeper = Callable[[float], None]


def _system_clock() -> datetime:
    return datetime.now(UTC)


def _random_execution_id() -> str:
    return str(uuid.uuid4())

PREREGISTRATION_PATH = Path("docs/phase4/reports/P4.2a-v2-heldout-preregistration-20260810.json")
PREREGISTRATION_SHA256 = "ccecbf5ca7b48b16e445318b8c94a08927432f92c7e8c12f8ab40f2916578705"
DESIGN_PATH = Path("config/p4_event_evaluation_v2.yaml")
DESIGN_SHA256 = "18a2428a4ec04bfea6e4f4d70692f38ea82fbaee5a223f30f2465b895b238e21"
HELDOUT_CONTRACT_PATH = Path("config/p4_event_extract_eval_v2-heldout-qwen3.6-plus.yaml")
HELDOUT_CONTRACT_SHA256 = "26be1765204b122908e7bd09cac857c33bd3140233df47dc3358bc590e020199"
ROUND3_CONTRACT_PATH = Path("config/p4_event_extract_eval_v2-r3-qwen3.6-plus.yaml")
ROUND3_CONTRACT_SHA256 = "fa75a6cf33065745d02f74fe39e4f102723da43f37ac549058bb34fa8256a181"
ROUND3_PROMPT_PATH = Path("config/prompts/p4_news_event_extract_v2-r3.txt")
ROUND3_PROMPT_SHA256 = "0291dc882aac42878ba00c4ed3970da72f19508308cd39211467b4fd92294f44"
SELECTION_OUTCOME_SHA256 = "36b5a004b294f012b4ab1dab659d1b3d5d98320d794ad2fe90960a617f554da1"
SELECTED_FREEZE_SHA256 = "0ebc5362055af7ef6409155befc5e09d345cd4f2d8d128ea0791a0c293f66f75"
REHEARSAL_V1_INCIDENT_PATH = Path(
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v1-incident-20260810.json"
)
REHEARSAL_V1_INCIDENT_SHA256 = (
    "c3224b288f5181131351ae711a673ce94ec603375925d0cc968cef85d103e785"
)

SUCCESSOR_V2_1_PREREGISTRATION_PATH = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-1-preregistration-20260810.json"
)
SUCCESSOR_V2_1_PREREGISTRATION_SHA256 = (
    "c303cfb13a42ecbb7e0acaec04de12a9e9169b89cf9e93ea79d0f120d1439d3e"
)
SUCCESSOR_V2_1_PREREGISTRATION_COMMIT = (
    "b302d5889f01296568340bcc15041cc554ceb2c7"
)
SUCCESSOR_V2_1_TIMING_PREREGISTRATION_PATH = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-1-prediction-timing-seam-preregistration-20260810.json"
)
SUCCESSOR_V2_1_TIMING_PREREGISTRATION_SHA256 = (
    "1052c7a33268572fc794517844dae4b6c1ea504121712ad2f55ec814a7446f9a"
)
SUCCESSOR_V2_1_TIMING_PREREGISTRATION_COMMIT = (
    "b3c2d2216c1feffd9949f181fa6766f8357ff683"
)
SUCCESSOR_V2_1_TIMING_SOURCE_PATH = Path("scripts/run_p4_2a_offline_extract.py")
SUCCESSOR_V2_1_TIMING_TEST_PATH = Path("tests/test_p4_2a_offline_extract.py")
SUCCESSOR_V2_1_SCOPE_CORRECTION_RULING_PATH = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-1-scope-correction-owner-ruling-20260810.json"
)
SUCCESSOR_V2_1_SCOPE_CORRECTION_RULING_SHA256 = (
    "36a3baea9ce5e4c28c7e6aff9e77c09691024a870513f49f2094b07963f3582e"
)
SUCCESSOR_V2_1_SCOPE_CORRECTION_RULING_COMMIT = (
    "88690ef488925f9de922569f961ec4ff1a23bb78"
)
SUCCESSOR_V2_1_CONTROL_PLANE_AUTHORIZATION_PATH = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-1-control-plane-registry-expansion-authorization-20260811.json"
)
SUCCESSOR_V2_1_CONTROL_PLANE_AUTHORIZATION_SHA256 = (
    "ab85a0ddd90728c7d41051e640b59f7dc777f2f2aec3c8290286206979251796"
)
SUCCESSOR_V2_1_CONTROL_PLANE_AUTHORIZATION_COMMIT = (
    "d37040be87644977ddaad60b2590ac2e62b2aeed"
)
SUCCESSOR_V2_1_FINALIZER_TEST_PATH = Path(
    "tests/test_p4_2a_v2_heldout_finalizer.py"
)
SUCCESSOR_V2_1_PREEXPANSION_SURFACE_COUNT = 14
SUCCESSOR_V2_1_EXPANDED_SURFACE_COUNT = 15
SUCCESSOR_V2_1_TIMING_SOURCE_PREIMPLEMENTATION_SHA256 = (
    "5889341f1336f9d75891793b06c689e7b5bfb7180e135a6ff361cdf83e4ef21b"
)
SUCCESSOR_V2_1_TIMING_TEST_PREIMPLEMENTATION_SHA256 = (
    "76570178cf5de80dbed29ffc1317574daf75db863cd06da5e49cad3b2681886d"
)
SUCCESSOR_V2_1_BUNDLE_SCHEMA_PATH = Path(
    "config/schemas/p4_2a_v2_1_heldout_rehearsal_bundle.schema.json"
)
SUCCESSOR_V2_1_BUNDLE_SCHEMA_SHA256 = (
    "ed827e29ce853f07a9110d44c98793a4cc3ef0634a12fe7e8bc64c7290d7d716"
)
SUCCESSOR_V2_1_RELEASE_SCHEMA_PATH = Path(
    "config/schemas/p4_2a_v2_1_heldout_release_authorization.schema.json"
)
SUCCESSOR_V2_1_RELEASE_SCHEMA_SHA256 = (
    "c5a4ecfe8c5bf3e3ebea2d4470337a67dde3a8e9dbe6fc3df68b1c4e16241c51"
)
SUCCESSOR_V2_1_BUNDLE_PATH = Path(
    "docs/phase4/rehearsals/P4.2a-v2-calibration-v2-1/bundle.json"
)
SUCCESSOR_V2_1_RELEASE_PATH = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-1-release-authorization-20260810.json"
)
SUCCESSOR_V2_1_VALIDATOR_PATH = Path(
    "scripts/validate_p4_2a_v2_1_heldout_rehearsal_bundle.py"
)
SUCCESSOR_V2_1_VALIDATOR_RESULT_SCHEMA = (
    "p4.2a-v2-heldout-validator-result-v2.1"
)
SUCCESSOR_V2_1_VALIDATOR_RESULT_STATUS = (
    "PASS_REHEARSAL_V2_1_AWAITING_OWNER_REVIEW"
)
_LOCKED_PYTHON_EXECUTABLE_RELATIVE = Path(".venv/bin/python")
_LOCKED_PYTHON_EXECUTABLE_SHA256 = (
    "f4cd716d4b54f205398bec6932cc59361b087494ca2ddb157a5e8631d4d6f863"
)
_VALIDATOR_ENVIRONMENT_MARKER = "ALPHAPILOT_P42A_REHEARSAL_V2_1_ENV_LOCKED"
FRAME_AUTHORITY_PATH = Path(
    "docs/phase4/reports/"
    "P4.2a-rehearsal-v2-approval-and-frame-authority-ruling-20260810.json"
)
FRAME_AUTHORITY_SHA256 = (
    "8605dd30dd6bffa9621b6efe5e01c4c9cead615c9639259c2e79e14fcbbc3421"
)
SUCCESSOR_CODE_GATE_AUTHORITY_PATH = Path(
    "docs/phase4/reports/P4.2a-successor-v2-1-code-gate-authorization-20260810.json"
)
SUCCESSOR_CODE_GATE_AUTHORITY_SHA256 = (
    "e28db692dc150983f86f6760fb1a95584d8607658e8a78a0de35cf3fc81940cd"
)
SUCCESSOR_V2_1_RELEASE_VERDICT = (
    "APPROVE_SUCCESSOR_V2_1_REAL_HELDOUT_PREPARATION"
)
MATERIALIZATION_MANIFEST_V2_SCHEMA = "p4.2a-v2-heldout-materialization-manifest-v2"
CNINFO_MIN_START_TO_START_SECONDS = 1.0
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_REAL_ALLOWED_STAGES = frozenset(
    {
        "materialize",
        "infer",
        "select-blind",
        "seal-draft",
        "build-adjudication-ui",
    }
)
_REAL_STAGE_ENVIRONMENT_MARKER = "ALPHAPILOT_P42A_REAL_STAGE_ENV_LOCKED"
_REAL_STAGE_ENTRYPOINTS = {
    "materialize": Path("scripts/prepare_p4_2a_v2_heldout.py"),
    "infer": Path("scripts/prepare_p4_2a_v2_heldout.py"),
    "select-blind": Path("scripts/prepare_p4_2a_v2_heldout.py"),
    "seal-draft": Path("scripts/seal_p4_2a_v2_heldout_draft.py"),
    "build-adjudication-ui": Path(
        "scripts/build_p4_2a_v2_heldout_adjudication_ui.py"
    ),
}
_OFFLINE_ALLOWED_STAGES = _REAL_ALLOWED_STAGES | {
    "finalize-owner-adjudication",
    "evaluation",
}
_OFFLINE_CAPABILITY_NONCE = object()
_PREVALIDATED_STAGE_AUTHORITY_NONCE = object()

FRAME_ID = "p4.2a-heldout-frame-v2"
MODEL = "qwen3.6-plus"
WINDOW_START_UTC = "2026-08-05T16:00:00Z"
WINDOW_END_UTC = "2026-08-08T16:00:00Z"
SQLITE_WINDOW_START_UTC = "2026-08-05 16:00:00"
SQLITE_WINDOW_END_UTC = "2026-08-08 16:00:00"
EXPECTED_RAW_COUNT = 4048
EXPECTED_BY_SOURCE = {
    "akshare_ths": 1021,
    "cninfo": 2824,
    "sina_company_news": 203,
}
SEED = "alphapilot-p4.2a-heldout-frame-v2-20260809-r1"
POSITIVE_SELECTED = 40
NEGATIVE_SELECTED = 20

FULL_REHEARSAL_RECEIPT_SCHEMA = (
    "p4.2a-v2-heldout-full-path-rehearsal-pass-receipt-v1"
)
FULL_REHEARSAL_CONTRACT_SCHEMA = "p4.2a-v2-heldout-full-path-rehearsal-contract-v1"
FULL_REHEARSAL_EXPECTED_SCHEMA = "p4.2a-v2-heldout-full-path-rehearsal-expected-v1"
FULL_REHEARSAL_PUBLISHED_ARTIFACTS = frozenset(
    {"contract.json", "inputs.jsonl", "expected.json"}
)
FULL_REHEARSAL_TESTED_CODE_PATHS = frozenset(
    {
        "scripts/rehearse_p4_2a_v2_heldout_full_path.py",
        "scripts/prepare_p4_2a_v2_heldout.py",
        "scripts/build_p4_2a_gold_sample.py",
        "scripts/run_p4_2a_offline_extract.py",
        "scripts/seal_p4_2a_v2_heldout_draft.py",
        "scripts/seal_p4_2a_v2_ai_draft.py",
        "scripts/build_p4_2a_v2_heldout_adjudication_ui.py",
        "scripts/build_p4_2a_v2_adjudication_ui.py",
        "scripts/finalize_p4_2a_v2_heldout_adjudication.py",
        "scripts/evaluate_p4_2a_v2_heldout.py",
    }
)
_LOWER_HEX = frozenset("0123456789abcdef")
_SIX_DIGIT_SYMBOL_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_INFERENCE_SETTINGS_SAFETY: JsonObject = {
    "trading_mode": "research",
    "live_trading_enabled": False,
    "paper_trading_enabled": False,
    "paper_auto_trading_enabled": False,
    "futu_enable_account_mutation": False,
    "futu_enable_trade": False,
    "unlock_trade_permanently_blocked": True,
}
_SNAPSHOT_FIELDS = {
    "sqlite_uri_mode",
    "pragma_query_only",
    "connection_total_changes",
    "llm_call_count",
    "llm_call_max_id",
    "trade_proposal_count",
    "broker_order_count",
    "non_simulate_order_count",
    "news_events_table_exists",
    "universe_symbol_count",
}


class HeldoutPreparationError(RuntimeError):
    """The frozen v2 held-out preparation contract was violated."""


@dataclass(frozen=True, slots=True)
class HeldoutBinding:
    root: Path
    preregistration: JsonObject
    design: JsonObject
    contract: EventExtractContract
    artifacts: Mapping[str, Path]
    retired_ids: frozenset[int]


@dataclass(frozen=True, slots=True)
class SelectionResult:
    manifest: JsonObject
    blind_rows: tuple[JsonObject, ...]


@dataclass(frozen=True, slots=True)
class SelectionExecutionBinding:
    materialization_manifest_sha256: str
    inference_state_sha256: str
    prediction_manifest_sha256: str
    execution_id: str
    eligible_candidate_count: int
    prediction_count: int
    status_ok_count: int
    status_failed_count: int
    started_at_utc: str
    completed_at_utc: str


@dataclass(frozen=True, slots=True)
class OperatorTimingAttestation:
    """Explicit operator decision required at the real materialization boundary."""

    attester_identity: str
    cninfo_midnight_batch_assessment: str
    p4_1_dense_poll_slot_assessment: str


@dataclass(frozen=True, slots=True)
class V21ReleaseAuthorization:
    """Recomputed successor release facts; never trusted from a caller alone."""

    project_root: Path
    receipt_path: Path
    receipt_sha256: str
    receipt_creating_commit: str
    preregistration_commit: str
    implementation_commit: str
    rehearsal_evidence_commit: str
    bundle_path: Path
    bundle_sha256: str
    bundle_root_sha256: str


@dataclass(frozen=True, slots=True)
class _OfflineRehearsalCapability:
    """Identity-bound authority for the registered noncanonical offline replay."""

    _nonce: object
    project_root: Path
    database: Path
    artifact_paths: tuple[Path, ...]
    pdf_fetcher: gold_builder.PdfFetcher
    pdf_text_extractor: gold_builder.PdfTextExtractor
    monotonic: MonotonicClock
    sleep: Sleeper
    inference_settings: Settings
    chat_json_fn: ChatJsonCallable
    snapshot_loader: ProductionSnapshotLoader
    wall_clock: Clock
    execution_id_factory: ExecutionIdFactory
    prediction_recorded_at_clock: RecordedAtClock
    prediction_monotonic_ns_clock: MonotonicNsClock
    preregistration_commit: str
    implementation_commit: str

    def __reduce__(self) -> NoReturn:
        raise TypeError("offline rehearsal capability is not serializable")


ExecutionContext = V21ReleaseAuthorization | _OfflineRehearsalCapability | None


@dataclass(frozen=True, slots=True)
class _CanonicalRuntimeSnapshot:
    environment: Mapping[str, str]
    runtime_paths: tuple[str, ...]
    executable: str
    version: tuple[int, int]
    hash_randomization: int
    no_site: int
    no_user_site: int
    safe_path: bool
    dont_write_bytecode: bool
    pycache_prefix: str | None
    ignore_environment: int
    isolated: int
    optimize: int
    main_file: str | None
    original_arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PrevalidatedStageAuthority:
    """Identity-bound delegation for pure checks within one validated stage."""

    _nonce: object
    project_root: Path
    validated_stage: str
    authorization: V21ReleaseAuthorization | _OfflineRehearsalCapability

    def __reduce__(self) -> NoReturn:
        raise TypeError("prevalidated stage authority is not serializable")


ProductionSnapshotLoader = Callable[[Path], dev_runner.ProductionSnapshot]


def _mapping(value: object, label: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise HeldoutPreparationError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise HeldoutPreparationError(f"{label} contains a non-string key")
    return dict(value)


def _require_sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _LOWER_HEX for character in value)
    ):
        raise HeldoutPreparationError(f"{label} must be a lowercase SHA-256")
    return value


def _strict_json_loads(payload: str, label: str) -> object:
    def object_pairs(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise HeldoutPreparationError(f"{label} contains duplicate key {key!r}")
            result[key] = value
        return result

    def finite_float(raw: str) -> float:
        value = float(raw)
        if not math.isfinite(value):
            raise HeldoutPreparationError(f"{label} contains non-finite number {raw!r}")
        return value

    def reject_constant(raw: str) -> NoReturn:
        raise HeldoutPreparationError(f"{label} contains non-finite number {raw!r}")

    try:
        return json.loads(
            payload,
            object_pairs_hook=object_pairs,
            parse_float=finite_float,
            parse_constant=reject_constant,
        )
    except HeldoutPreparationError:
        raise
    except (TypeError, ValueError) as exc:
        raise HeldoutPreparationError(f"{label} is invalid JSON") from exc


def _load_json(path: Path, label: str) -> JsonObject:
    try:
        payload = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise HeldoutPreparationError(f"{label} is unavailable") from exc
    value = _strict_json_loads(payload, label)
    return _mapping(value, label)


def _load_jsonl(path: Path, label: str) -> list[JsonObject]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise HeldoutPreparationError(f"{label} is unavailable") from exc
    rows: list[JsonObject] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            raise HeldoutPreparationError(f"{label} line {number} is blank")
        value = _strict_json_loads(line, f"{label} line {number}")
        rows.append(_mapping(value, f"{label} line {number}"))
    return rows


def _resolve(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise HeldoutPreparationError(f"{label} path is invalid")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise HeldoutPreparationError(f"{label} escapes the project root")
    return path


def _verify_file(root: Path, ref: object, label: str) -> Path:
    item = _mapping(ref, label)
    path = _resolve(root, item.get("path"), label)
    expected = item.get("sha256")
    if (
        not isinstance(expected, str)
        or path.is_symlink()
        or not path.is_file()
        or common.sha256_file(path) != expected
    ):
        raise HeldoutPreparationError(f"{label} bytes drifted")
    return path


def _require_exact_regular_file(
    root: Path,
    relative: Path,
    expected_sha256: str,
    label: str,
) -> Path:
    path = root / relative
    if (
        path != path.resolve()
        or not path.resolve().is_relative_to(root)
        or path.is_symlink()
        or not path.is_file()
        or common.sha256_file(path) != expected_sha256
    ):
        raise HeldoutPreparationError(f"{label} bytes drifted")
    return path


def _git_environment() -> dict[str, str]:
    """Return the fixed, non-ambient environment for every authority Git proof."""

    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "PAGER": "cat",
    }


def _validate_git_metadata_authority(root: Path) -> None:
    git_directory = root / ".git"
    try:
        metadata = git_directory.lstat()
    except OSError as exc:
        raise HeldoutPreparationError("successor Git metadata directory is unavailable") from exc
    if (
        git_directory.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or git_directory.resolve(strict=True) != git_directory
    ):
        raise HeldoutPreparationError(
            "successor Git metadata must be an exact nonsymlink directory"
        )
    grafts = git_directory / "info/grafts"
    if grafts.exists() or grafts.is_symlink():
        raise HeldoutPreparationError("successor Git grafts are forbidden")


def _git(root: Path, *arguments: str, check: bool = True) -> str:
    _validate_git_metadata_authority(root)
    completed = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.commitGraph=false",
            "-c",
            "gc.auto=0",
            "-C",
            str(root),
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=_git_environment(),
    )
    if check and completed.returncode != 0:
        raise HeldoutPreparationError(
            f"successor release git proof failed: {' '.join(arguments)}"
        )
    if completed.returncode == 0 and completed.stderr:
        raise HeldoutPreparationError("successor release git proof emitted stderr")
    return completed.stdout


def _require_git_commit(root: Path, value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in _LOWER_HEX for character in value)
    ):
        raise HeldoutPreparationError(f"{label} must be a full lowercase Git commit")
    _git(root, "cat-file", "-e", f"{value}^{{commit}}")
    return value


def _require_git_ancestor(root: Path, ancestor: str, descendant: str, label: str) -> None:
    _validate_git_metadata_authority(root)
    completed = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.commitGraph=false",
            "-c",
            "gc.auto=0",
            "-C",
            str(root),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        check=False,
        capture_output=True,
        env=_git_environment(),
    )
    if completed.returncode != 0 or completed.stderr:
        raise HeldoutPreparationError(f"{label} ancestry proof failed")


def _git_blob(root: Path, commit: str, relative: Path, label: str) -> bytes:
    _validate_git_metadata_authority(root)
    completed = subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.commitGraph=false",
            "-c",
            "gc.auto=0",
            "-C",
            str(root),
            "show",
            f"{commit}:{relative.as_posix()}",
        ],
        check=False,
        capture_output=True,
        env=_git_environment(),
    )
    if completed.returncode != 0 or completed.stderr:
        raise HeldoutPreparationError(f"{label} Git blob is unavailable")
    return completed.stdout


def _unique_added_path_commit(root: Path, relative: Path) -> str:
    status = _git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        relative.as_posix(),
    )
    if status:
        raise HeldoutPreparationError("successor release receipt is untracked or dirty")
    history = _git(
        root,
        "log",
        "--first-parent",
        "--diff-merges=first-parent",
        "--format=@@%H",
        "--name-status",
        "--find-renames",
        "--find-copies",
        "--",
        relative.as_posix(),
    )
    touches: list[tuple[str, str, tuple[str, ...]]] = []
    commit: str | None = None
    for raw_line in history.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("@@"):
            commit = line[2:]
            continue
        if commit is None:
            raise HeldoutPreparationError("successor release receipt history is malformed")
        fields = tuple(line.split("\t"))
        if len(fields) < 2:
            raise HeldoutPreparationError("successor release receipt history is malformed")
        touches.append((commit, fields[0], fields[1:]))
    if len(touches) != 1:
        raise HeldoutPreparationError(
            "successor release receipt must have exactly one first-parent Git touch"
        )
    creation_commit, status_code, paths = touches[0]
    if status_code != "A" or paths != (relative.as_posix(),):
        raise HeldoutPreparationError(
            "successor release receipt unique Git touch must be status A"
        )
    return _require_git_commit(root, creation_commit, "release receipt creating commit")


def _validate_prediction_timing_preregistration_history(
    root: Path,
    implementation_commit: str,
) -> None:
    """Prove the timing preregistration was committed before either target changed."""

    preregistration_path = _require_exact_regular_file(
        root,
        SUCCESSOR_V2_1_TIMING_PREREGISTRATION_PATH,
        SUCCESSOR_V2_1_TIMING_PREREGISTRATION_SHA256,
        "successor timing preregistration",
    )
    preregistration = _load_json(
        preregistration_path,
        "successor timing preregistration",
    )
    if preregistration.get("status") != "PREREGISTERED_BEFORE_TIMING_SEAM_IMPLEMENTATION":
        raise HeldoutPreparationError("successor timing preregistration status drifted")
    scope = _mapping(
        preregistration.get("prospective_scope_extension"),
        "successor timing preregistration scope",
    )
    registered = scope.get("paths")
    expected_targets = (
        (
            SUCCESSOR_V2_1_TIMING_SOURCE_PATH,
            SUCCESSOR_V2_1_TIMING_SOURCE_PREIMPLEMENTATION_SHA256,
        ),
        (
            SUCCESSOR_V2_1_TIMING_TEST_PATH,
            SUCCESSOR_V2_1_TIMING_TEST_PREIMPLEMENTATION_SHA256,
        ),
    )
    if not isinstance(registered, list) or len(registered) != len(expected_targets):
        raise HeldoutPreparationError("successor timing preregistration registry drifted")
    for value, (relative, expected_sha256) in zip(registered, expected_targets, strict=True):
        item = _mapping(value, "successor timing preregistration target")
        if (
            item.get("path") != relative.as_posix()
            or item.get("current_sha256") != expected_sha256
            or item.get("current_diff_against_head") != "none"
        ):
            raise HeldoutPreparationError(
                "successor timing preregistration target binding drifted"
            )

    creation_commit = _unique_added_path_commit(
        root,
        SUCCESSOR_V2_1_TIMING_PREREGISTRATION_PATH,
    )
    if creation_commit != SUCCESSOR_V2_1_TIMING_PREREGISTRATION_COMMIT:
        raise HeldoutPreparationError("successor timing preregistration history drifted")
    if (
        _git_blob(
            root,
            creation_commit,
            SUCCESSOR_V2_1_TIMING_PREREGISTRATION_PATH,
            "successor timing preregistration",
        )
        != preregistration_path.read_bytes()
    ):
        raise HeldoutPreparationError(
            "successor timing preregistration differs from its creation blob"
        )
    _require_git_ancestor(
        root,
        creation_commit,
        implementation_commit,
        "implementation/timing preregistration",
    )
    for relative, expected_sha256 in expected_targets:
        preimplementation = _git_blob(
            root,
            creation_commit,
            relative,
            f"preimplementation timing target {relative}",
        )
        if common.sha256_bytes(preimplementation) != expected_sha256:
            raise HeldoutPreparationError(
                f"timing target was not clean at preregistration creation: {relative}"
            )
        implemented = _git_blob(
            root,
            implementation_commit,
            relative,
            f"implemented timing target {relative}",
        )
        if implemented == preimplementation:
            raise HeldoutPreparationError(
                f"timing target was not changed by the implementation: {relative}"
            )
    _validate_successor_implementation_commit_surface(root, implementation_commit)


def _validate_json_schema(instance: object, schema_path: Path, label: str) -> None:
    schema = _load_json(schema_path, f"{label} schema")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise HeldoutPreparationError(f"{label} schema is invalid") from exc
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise HeldoutPreparationError(
            f"{label} failed registered schema at {location}: {errors[0].message}"
        )


def _regular_nonsymlink_bytes(path: Path, label: str) -> bytes:
    """Read one stable regular file without following a final-component symlink."""

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        before = path.lstat()
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise HeldoutPreparationError(f"{label} is unavailable") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            path.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise HeldoutPreparationError(f"{label} is not a stable regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_open = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        after_path = path.lstat()
    except OSError as exc:
        raise HeldoutPreparationError(f"{label} changed while being read") from exc
    stable_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    if any(
        getattr(before, field) != getattr(after_open, field)
        or getattr(before, field) != getattr(after_path, field)
        for field in stable_fields
    ):
        raise HeldoutPreparationError(f"{label} changed while being read")
    return b"".join(chunks)


def _validator_child_environment() -> dict[str, str]:
    return {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "OPENBLAS_MAIN_FREE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPYCACHEPREFIX": "/dev/null",
        "PYTHONSAFEPATH": "1",
        "TZ": "UTC",
        "__CF_USER_TEXT_ENCODING": f"0x{os.getuid():X}:0x0:0x0",
        "PATH": "/usr/bin:/bin",
        _VALIDATOR_ENVIRONMENT_MARKER: "1",
    }


def _fixed_locked_python(root: Path, label: str) -> Path:
    launcher = (root / _LOCKED_PYTHON_EXECUTABLE_RELATIVE).absolute()
    try:
        target = launcher.resolve(strict=True)
        metadata = target.lstat()
    except OSError as exc:
        raise HeldoutPreparationError(f"{label} is unavailable") from exc
    if (
        target.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or common.sha256_file(target) != _LOCKED_PYTHON_EXECUTABLE_SHA256
    ):
        raise HeldoutPreparationError(f"{label} drifted")
    return launcher


def _validate_locked_validator_result(
    completed: subprocess.CompletedProcess[bytes],
    expected_result: Mapping[str, Any],
) -> None:
    if (
        completed.returncode != 0
        or completed.stderr != b""
        or completed.stdout != common.canonical_json_bytes(dict(expected_result))
    ):
        raise HeldoutPreparationError(
            "successor rehearsal bundle failed locked independent validation"
        )


def _canonical_real_stage_environment(root: Path) -> dict[str, str]:
    return {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "OPENBLAS_MAIN_FREE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPYCACHEPREFIX": "/dev/null",
        "PYTHONSAFEPATH": "1",
        "TZ": "UTC",
        "__CF_USER_TEXT_ENCODING": f"0x{os.getuid():X}:0x0:0x0",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        _REAL_STAGE_ENVIRONMENT_MARKER: "1",
    }


def _canonical_real_stage_runtime_paths(root: Path) -> tuple[str, ...]:
    stdlib = (
        Path(sys.base_prefix)
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
    ).absolute()
    candidates = (
        stdlib.parent / f"python{sys.version_info.major}{sys.version_info.minor}.zip",
        stdlib,
        stdlib / "lib-dynload",
        root / ".venv/lib/python3.12/site-packages",
        root,
        root / "src",
    )
    result: list[str] = []
    for candidate in candidates:
        value = candidate.absolute().as_posix()
        if value not in result:
            result.append(value)
    return tuple(result)


def _canonical_real_stage_bootstrap(root: Path, stage: str) -> str:
    module_name = _REAL_STAGE_ENTRYPOINTS[stage].with_suffix("").as_posix().replace(
        "/", "."
    )
    runtime_paths = repr(list(_canonical_real_stage_runtime_paths(root)))
    entrypoint = repr((root / _REAL_STAGE_ENTRYPOINTS[stage]).as_posix())
    return (
        f"import sys;sys.path[:]={runtime_paths};"
        f"sys.modules['__main__'].__file__={entrypoint};"
        f"from {module_name} import main;main()"
    )


def _capture_canonical_runtime_snapshot(stage: str) -> _CanonicalRuntimeSnapshot:
    module_name = _REAL_STAGE_ENTRYPOINTS[stage].with_suffix("").as_posix().replace(
        "/", "."
    )
    entrypoint_module = sys.modules.get(module_name)
    raw_main_file = getattr(entrypoint_module, "__file__", None)
    return _CanonicalRuntimeSnapshot(
        environment=dict(os.environ),
        runtime_paths=tuple(sys.path),
        executable=sys.executable,
        version=sys.version_info[:2],
        hash_randomization=sys.flags.hash_randomization,
        no_site=sys.flags.no_site,
        no_user_site=sys.flags.no_user_site,
        safe_path=sys.flags.safe_path,
        dont_write_bytecode=sys.dont_write_bytecode,
        pycache_prefix=sys.pycache_prefix,
        ignore_environment=sys.flags.ignore_environment,
        isolated=sys.flags.isolated,
        optimize=sys.flags.optimize,
        main_file=raw_main_file if isinstance(raw_main_file, str) else None,
        original_arguments=tuple(sys.orig_argv),
    )


def _canonical_runtime_snapshot_drift(
    root: Path,
    *,
    stage: str,
    snapshot: _CanonicalRuntimeSnapshot,
) -> str | None:
    expected_entrypoint = root / _REAL_STAGE_ENTRYPOINTS[stage]
    if dict(snapshot.environment) != _canonical_real_stage_environment(root):
        return "environment"
    if snapshot.version != (3, 12):
        return "Python version"
    if snapshot.runtime_paths != _canonical_real_stage_runtime_paths(root):
        return "sys.path"
    if snapshot.main_file is None:
        return "entrypoint"
    try:
        main_file = Path(snapshot.main_file).resolve(strict=True)
    except OSError:
        return "entrypoint"
    if main_file != expected_entrypoint:
        return "entrypoint"
    if snapshot.original_arguments[1:6] != (
        "-S",
        "-P",
        "-B",
        "-c",
        _canonical_real_stage_bootstrap(root, stage),
    ):
        return "first-exec command"
    try:
        fixed_python = _fixed_locked_python(root, "real-stage Python")
    except HeldoutPreparationError:
        return "Python executable"
    if Path(snapshot.executable).absolute() != fixed_python:
        return "Python executable"
    expected_flags = (
        0,
        1,
        1,
        True,
        True,
        "/dev/null",
        0,
        0,
        0,
    )
    observed_flags = (
        snapshot.hash_randomization,
        snapshot.no_site,
        snapshot.no_user_site,
        snapshot.safe_path,
        snapshot.dont_write_bytecode,
        snapshot.pycache_prefix,
        snapshot.ignore_environment,
        snapshot.isolated,
        snapshot.optimize,
    )
    if observed_flags != expected_flags:
        return "interpreter flags"
    return None


def _validate_canonical_runtime_environment(
    binding: HeldoutBinding,
    *,
    stage: str,
) -> None:
    """Require the operator's first OS exec to use the frozen real-stage runtime."""

    root = binding.root.resolve()
    drift = _canonical_runtime_snapshot_drift(
        root,
        stage=stage,
        snapshot=_capture_canonical_runtime_snapshot(stage),
    )
    if drift is not None:
        raise HeldoutPreparationError(
            f"canonical real stage requires the exact first-exec runtime: {drift} drifted"
        )
    if "sitecustomize" in sys.modules or "usercustomize" in sys.modules:
        raise HeldoutPreparationError(
            "canonical real stage imported an ambient customization module"
        )


def _validate_canonical_runtime_module_origins(
    binding: HeldoutBinding,
    *,
    stage: str,
    authorization: V21ReleaseAuthorization,
) -> None:
    """Classify origins only after the locked child validated committed source."""

    root = binding.root.resolve()
    for relative in _registered_successor_implementation_paths(root):
        current = _regular_nonsymlink_bytes(
            root / relative, f"real-stage implementation {relative}"
        )
        committed = _git_blob(
            root,
            authorization.implementation_commit,
            relative,
            f"real-stage implementation {relative}",
        )
        if current != committed:
            raise HeldoutPreparationError(
                f"real-stage implementation differs from release commit: {relative}"
            )
    try:
        from scripts import rehearse_p4_2a_v2_1_heldout_full_path as successor_runner

        stdlib = (
            Path(sys.base_prefix)
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
        )
        repository_paths = successor_runner._classify_loaded_module_origins(
            modules=sys.modules,
            repository_root=root,
            runner_path=root / _REAL_STAGE_ENTRYPOINTS[stage],
            site_root=root / ".venv/lib/python3.12/site-packages",
            stdlib_roots=(stdlib,),
        )
    except (ImportError, OSError, RuntimeError) as exc:
        raise HeldoutPreparationError(
            "canonical real stage module origin classification failed"
        ) from exc
    required_repository_paths = {
        _REAL_STAGE_ENTRYPOINTS[stage].as_posix(),
        Path("scripts/prepare_p4_2a_v2_heldout.py").as_posix(),
    }
    if not required_repository_paths <= repository_paths:
        raise HeldoutPreparationError(
            "canonical real stage required module origins are incomplete"
        )


def _validate_successor_bundle_locked_child(
    root: Path,
    bundle_path: Path,
    *,
    expected_bundle_sha256: str,
    expected_implementation_commit: str,
) -> JsonObject:
    """Validate the canonical bundle only in the registered locked child."""

    if root != PROJECT_ROOT.resolve():
        raise HeldoutPreparationError("locked successor validator requires canonical root")
    if bundle_path != root / SUCCESSOR_V2_1_BUNDLE_PATH:
        raise HeldoutPreparationError("locked successor validator bundle path drifted")
    validator_path = root / SUCCESSOR_V2_1_VALIDATOR_PATH
    validator_payload = _regular_nonsymlink_bytes(
        validator_path, "registered successor validator"
    )
    if validator_payload != _git_blob(
        root,
        expected_implementation_commit,
        SUCCESSOR_V2_1_VALIDATOR_PATH,
        "registered successor validator",
    ):
        raise HeldoutPreparationError(
            "registered successor validator differs from the implementation commit"
        )
    before_payload = _regular_nonsymlink_bytes(
        bundle_path, "registered successor rehearsal bundle"
    )
    if common.sha256_bytes(before_payload) != expected_bundle_sha256:
        raise HeldoutPreparationError("successor rehearsal bundle SHA binding drifted")
    try:
        before_text = before_payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HeldoutPreparationError(
            "registered successor rehearsal bundle is not UTF-8"
        ) from exc
    before_bundle = _mapping(
        _strict_json_loads(before_text, "registered successor rehearsal bundle"),
        "registered successor rehearsal bundle",
    )
    expected_bundle_root = _require_sha256(
        _mapping(before_bundle.get("merkle"), "successor bundle.merkle").get(
            "bundle_root_sha256"
        ),
        "successor bundle root",
    )
    python = _fixed_locked_python(root, "registered successor validator Python")
    try:
        completed = subprocess.run(
            [str(python), "-S", "-P", "-B", str(validator_path)],
            cwd=root,
            env=_validator_child_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise HeldoutPreparationError(
            "registered successor validator child could not start"
        ) from exc
    expected_result = {
        "schema_version": SUCCESSOR_V2_1_VALIDATOR_RESULT_SCHEMA,
        "status": SUCCESSOR_V2_1_VALIDATOR_RESULT_STATUS,
        "bundle_path": SUCCESSOR_V2_1_BUNDLE_PATH.as_posix(),
        "bundle_sha256": expected_bundle_sha256,
        "bundle_root_sha256": expected_bundle_root,
        "implementation_commit": expected_implementation_commit,
        "real_heldout_materialization_unlocked": False,
        "heldout_metric_evaluation_unlocked": False,
    }
    _validate_locked_validator_result(completed, expected_result)
    if _regular_nonsymlink_bytes(
        validator_path, "registered successor validator"
    ) != validator_payload:
        raise HeldoutPreparationError(
            "registered successor validator changed during independent validation"
        )
    after_payload = _regular_nonsymlink_bytes(
        bundle_path, "registered successor rehearsal bundle"
    )
    if after_payload != before_payload:
        raise HeldoutPreparationError(
            "successor rehearsal bundle changed during independent validation"
        )
    return before_bundle


def _validate_successor_bundle(
    root: Path,
    bundle_path: Path,
    *,
    expected_bundle_sha256: str,
    expected_implementation_commit: str,
) -> JsonObject:
    if root == PROJECT_ROOT.resolve():
        return _validate_successor_bundle_locked_child(
            root,
            bundle_path,
            expected_bundle_sha256=expected_bundle_sha256,
            expected_implementation_commit=expected_implementation_commit,
        )
    try:
        from scripts import (
            validate_p4_2a_v2_1_heldout_rehearsal_bundle as bundle_validator,
        )
    except ImportError as exc:
        raise HeldoutPreparationError(
            "registered successor rehearsal bundle validator is unavailable"
        ) from exc
    validate = getattr(bundle_validator, "validate_bundle", None)
    if not callable(validate):
        raise HeldoutPreparationError(
            "registered successor rehearsal bundle validator API is unavailable"
        )
    try:
        result = validate(project_root=root, bundle_path=bundle_path)
    except Exception as exc:
        raise HeldoutPreparationError(
            "successor rehearsal bundle failed full independent validation"
        ) from exc
    bundle = _mapping(result, "validated successor rehearsal bundle")
    if common.sha256_file(bundle_path) != expected_bundle_sha256:
        raise HeldoutPreparationError("successor rehearsal bundle SHA binding drifted")
    if (
        _mapping(bundle.get("lineage"), "successor bundle.lineage").get(
            "implementation_commit"
        )
        != expected_implementation_commit
    ):
        raise HeldoutPreparationError("successor bundle implementation binding drifted")
    return bundle


def _validate_release_file_ref(
    root: Path,
    lineage: Mapping[str, Any],
    name: str,
    relative: Path,
    *,
    expected_sha256: str | None = None,
) -> tuple[Path, str]:
    reference = _mapping(lineage.get(name), f"release lineage.{name}")
    if set(reference) != {"path", "sha256"} or reference.get("path") != relative.as_posix():
        raise HeldoutPreparationError(f"release lineage.{name} path/schema drifted")
    digest = _require_sha256(reference.get("sha256"), f"release lineage.{name} SHA")
    if expected_sha256 is not None and digest != expected_sha256:
        raise HeldoutPreparationError(f"release lineage.{name} SHA binding drifted")
    path = root / relative
    if (
        path != path.resolve()
        or not path.resolve().is_relative_to(root)
        or path.is_symlink()
        or not path.is_file()
        or common.sha256_file(path) != digest
    ):
        raise HeldoutPreparationError(f"release lineage.{name} bytes drifted")
    return path, digest


def validate_v2_1_release_authorization(
    project_root: Path = PROJECT_ROOT,
) -> V21ReleaseAuthorization:
    """Validate the fixed successor receipt, unique-A Git history and bundle."""

    root = project_root.resolve()
    _require_exact_regular_file(
        root,
        SUCCESSOR_V2_1_PREREGISTRATION_PATH,
        SUCCESSOR_V2_1_PREREGISTRATION_SHA256,
        "successor preregistration",
    )
    _require_exact_regular_file(
        root,
        SUCCESSOR_V2_1_TIMING_PREREGISTRATION_PATH,
        SUCCESSOR_V2_1_TIMING_PREREGISTRATION_SHA256,
        "successor timing preregistration",
    )
    _require_exact_regular_file(
        root,
        SUCCESSOR_V2_1_BUNDLE_SCHEMA_PATH,
        SUCCESSOR_V2_1_BUNDLE_SCHEMA_SHA256,
        "successor bundle schema",
    )
    release_schema_path = _require_exact_regular_file(
        root,
        SUCCESSOR_V2_1_RELEASE_SCHEMA_PATH,
        SUCCESSOR_V2_1_RELEASE_SCHEMA_SHA256,
        "successor release schema",
    )
    _require_exact_regular_file(
        root,
        FRAME_AUTHORITY_PATH,
        FRAME_AUTHORITY_SHA256,
        "frame authority",
    )
    _require_exact_regular_file(
        root,
        SUCCESSOR_CODE_GATE_AUTHORITY_PATH,
        SUCCESSOR_CODE_GATE_AUTHORITY_SHA256,
        "successor code-gate authority",
    )
    receipt_path = root / SUCCESSOR_V2_1_RELEASE_PATH
    if (
        receipt_path != receipt_path.resolve()
        or not receipt_path.resolve().is_relative_to(root)
        or receipt_path.is_symlink()
        or not receipt_path.is_file()
    ):
        raise HeldoutPreparationError(
            "BLOCKED_PENDING_SUCCESSOR_V2_1_OWNER_RELEASE: fixed receipt unavailable"
        )
    receipt = _load_json(receipt_path, "successor release receipt")
    _validate_json_schema(receipt, release_schema_path, "successor release receipt")
    if receipt.get("verdict") != SUCCESSOR_V2_1_RELEASE_VERDICT:
        raise HeldoutPreparationError("successor release verdict drifted")
    lineage = _mapping(receipt.get("lineage"), "successor release lineage")
    _validate_release_file_ref(
        root,
        lineage,
        "preregistration",
        SUCCESSOR_V2_1_PREREGISTRATION_PATH,
        expected_sha256=SUCCESSOR_V2_1_PREREGISTRATION_SHA256,
    )
    _validate_release_file_ref(
        root,
        lineage,
        "bundle_schema",
        SUCCESSOR_V2_1_BUNDLE_SCHEMA_PATH,
        expected_sha256=SUCCESSOR_V2_1_BUNDLE_SCHEMA_SHA256,
    )
    _validate_release_file_ref(
        root,
        lineage,
        "release_schema",
        SUCCESSOR_V2_1_RELEASE_SCHEMA_PATH,
        expected_sha256=SUCCESSOR_V2_1_RELEASE_SCHEMA_SHA256,
    )
    bundle_path, bundle_sha256 = _validate_release_file_ref(
        root,
        lineage,
        "bundle",
        SUCCESSOR_V2_1_BUNDLE_PATH,
    )
    _validate_release_file_ref(
        root,
        lineage,
        "frame_authority_ruling",
        FRAME_AUTHORITY_PATH,
        expected_sha256=FRAME_AUTHORITY_SHA256,
    )
    _validate_release_file_ref(
        root,
        lineage,
        "successor_v2_1_authorization",
        SUCCESSOR_CODE_GATE_AUTHORITY_PATH,
        expected_sha256=SUCCESSOR_CODE_GATE_AUTHORITY_SHA256,
    )
    review_path, _review_sha = _validate_release_file_ref(
        root,
        lineage,
        "review_request",
        Path(
            "docs/phase4/reports/"
            "P4.2a-v2-heldout-rehearsal-v2-1-implementation-and-execution-review-request-20260810.json"
        ),
    )
    review_request = _load_json(review_path, "successor implementation review request")
    timing_review_binding = _mapping(
        review_request.get("prediction_timing_preregistration"),
        "successor review prediction timing preregistration",
    )
    if timing_review_binding != {
        "path": SUCCESSOR_V2_1_TIMING_PREREGISTRATION_PATH.as_posix(),
        "sha256": SUCCESSOR_V2_1_TIMING_PREREGISTRATION_SHA256,
        "creation_commit": SUCCESSOR_V2_1_TIMING_PREREGISTRATION_COMMIT,
    }:
        raise HeldoutPreparationError(
            "successor review prediction timing preregistration binding drifted"
        )
    implementation_commit = _require_git_commit(
        root, lineage.get("implementation_commit"), "release implementation commit"
    )
    bundle = _validate_successor_bundle(
        root,
        bundle_path,
        expected_bundle_sha256=bundle_sha256,
        expected_implementation_commit=implementation_commit,
    )
    bundle_root = _require_sha256(
        _mapping(bundle.get("merkle"), "successor bundle.merkle").get(
            "bundle_root_sha256"
        ),
        "successor bundle root",
    )
    if lineage.get("bundle_root_sha256") != bundle_root:
        raise HeldoutPreparationError("release bundle root binding drifted")
    bundle_lineage = _mapping(bundle.get("lineage"), "successor bundle.lineage")
    if (
        bundle_lineage.get("preregistration_commit")
        != lineage.get("preregistration_commit")
        or bundle_lineage.get("implementation_commit")
        != lineage.get("implementation_commit")
    ):
        raise HeldoutPreparationError("release/bundle commit lineage drifted")

    preregistration_commit = _require_git_commit(
        root, lineage.get("preregistration_commit"), "release preregistration commit"
    )
    if preregistration_commit != SUCCESSOR_V2_1_PREREGISTRATION_COMMIT:
        raise HeldoutPreparationError("release preregistration commit drifted")
    rehearsal_evidence_commit = _require_git_commit(
        root,
        lineage.get("rehearsal_evidence_commit"),
        "release rehearsal evidence commit",
    )
    reviewed_head = _require_git_commit(
        root, receipt.get("reviewed_repository_head"), "release reviewed repository head"
    )
    creation_commit = _unique_added_path_commit(root, SUCCESSOR_V2_1_RELEASE_PATH)
    head = _require_git_commit(root, _git(root, "rev-parse", "HEAD").strip(), "HEAD")
    for ancestor, descendant, label in (
        (preregistration_commit, implementation_commit, "implementation/preregistration"),
        (
            SUCCESSOR_V2_1_TIMING_PREREGISTRATION_COMMIT,
            implementation_commit,
            "implementation/timing preregistration",
        ),
        (implementation_commit, rehearsal_evidence_commit, "rehearsal/implementation"),
        (rehearsal_evidence_commit, creation_commit, "receipt/rehearsal"),
        (reviewed_head, creation_commit, "receipt/reviewed head"),
        (preregistration_commit, creation_commit, "receipt/preregistration"),
        (implementation_commit, creation_commit, "receipt/implementation"),
        (creation_commit, head, "HEAD/receipt"),
    ):
        _require_git_ancestor(root, ancestor, descendant, label)
    _validate_prediction_timing_preregistration_history(root, implementation_commit)
    current_receipt_bytes = receipt_path.read_bytes()
    if _git_blob(
        root, creation_commit, SUCCESSOR_V2_1_RELEASE_PATH, "release receipt"
    ) != current_receipt_bytes:
        raise HeldoutPreparationError("release receipt bytes differ from unique creation blob")
    if _git_blob(
        root, rehearsal_evidence_commit, SUCCESSOR_V2_1_BUNDLE_PATH, "rehearsal bundle"
    ) != bundle_path.read_bytes():
        raise HeldoutPreparationError("rehearsal bundle differs from evidence commit blob")
    review_relative = review_path.relative_to(root)
    if (
        _git_blob(root, reviewed_head, review_relative, "review request")
        != review_path.read_bytes()
    ):
        raise HeldoutPreparationError("review request differs from reviewed-head blob")
    return V21ReleaseAuthorization(
        project_root=root,
        receipt_path=receipt_path,
        receipt_sha256=common.sha256_file(receipt_path),
        receipt_creating_commit=creation_commit,
        preregistration_commit=preregistration_commit,
        implementation_commit=implementation_commit,
        rehearsal_evidence_commit=rehearsal_evidence_commit,
        bundle_path=bundle_path,
        bundle_sha256=bundle_sha256,
        bundle_root_sha256=bundle_root,
    )


def _validate_control_plane_registry_expansion(root: Path) -> Path:
    ruling_path = _require_exact_regular_file(
        root,
        SUCCESSOR_V2_1_SCOPE_CORRECTION_RULING_PATH,
        SUCCESSOR_V2_1_SCOPE_CORRECTION_RULING_SHA256,
        "successor scope-correction owner ruling",
    )
    authorization_path = _require_exact_regular_file(
        root,
        SUCCESSOR_V2_1_CONTROL_PLANE_AUTHORIZATION_PATH,
        SUCCESSOR_V2_1_CONTROL_PLANE_AUTHORIZATION_SHA256,
        "successor control-plane registry authorization",
    )
    ruling = _load_json(ruling_path, "successor scope-correction owner ruling")
    authorization = _load_json(
        authorization_path,
        "successor control-plane registry authorization",
    )
    ruling_binding = _mapping(
        ruling.get("part_1_bindings_required_by_the_disclosure"),
        "successor scope-correction ruling bindings",
    )
    authorized_scope = _mapping(
        authorization.get("part_2_authorised_scope"),
        "successor control-plane authorized scope",
    )
    modifiable_paths = authorized_scope.get("modifiable_paths_exhaustive")
    permitted_effects = authorized_scope.get("permitted_effects_exhaustive")
    expected_modifiable_paths = [
        "scripts/prepare_p4_2a_v2_heldout.py",
        "scripts/rehearse_p4_2a_v2_1_heldout_full_path.py",
        "scripts/validate_p4_2a_v2_1_heldout_rehearsal_bundle.py",
    ]
    if (
        ruling.get("schema_version")
        != "p4.2a-v2-heldout-rehearsal-v2-1-scope-correction-owner-ruling-v1"
        or ruling.get("verdict") != "ACCEPT"
        or ruling_binding.get("explicit_verdict")
        != (
            "ACCEPT the retrospective registration of "
            "tests/test_p4_2a_v2_heldout_finalizer.py into the registered "
            "existing-test-update scope"
        )
        or authorization.get("schema_version")
        != "p4.2a-v2-1-control-plane-registry-expansion-authorization-v1"
        or authorization.get("verdict")
        != "AUTHORIZE_NARROW_CONTROL_PLANE_REGISTRY_EXPANSION"
        or authorization.get("repository_head_at_authorization")
        != SUCCESSOR_V2_1_SCOPE_CORRECTION_RULING_COMMIT
        or modifiable_paths != expected_modifiable_paths
        or not isinstance(permitted_effects, list)
        or len(permitted_effects) != 3
    ):
        raise HeldoutPreparationError(
            "successor control-plane registry authorization contract drifted"
        )
    expansion_text = permitted_effects[1]
    prefix = (
        "expand the registered path set from 14 to exactly 15 by appending "
    )
    suffix = " to registered_existing_test_updates"
    if (
        not isinstance(expansion_text, str)
        or not expansion_text.startswith(prefix)
        or not expansion_text.endswith(suffix)
    ):
        raise HeldoutPreparationError(
            "successor control-plane registry expansion declaration drifted"
        )
    appended = Path(expansion_text[len(prefix) : -len(suffix)])
    if appended != SUCCESSOR_V2_1_FINALIZER_TEST_PATH:
        raise HeldoutPreparationError(
            "successor control-plane appended registry path drifted"
        )
    ruling_commit = _unique_added_path_commit(
        root,
        SUCCESSOR_V2_1_SCOPE_CORRECTION_RULING_PATH,
    )
    authorization_commit = _unique_added_path_commit(
        root,
        SUCCESSOR_V2_1_CONTROL_PLANE_AUTHORIZATION_PATH,
    )
    if (
        ruling_commit != SUCCESSOR_V2_1_SCOPE_CORRECTION_RULING_COMMIT
        or authorization_commit != SUCCESSOR_V2_1_CONTROL_PLANE_AUTHORIZATION_COMMIT
        or _git_blob(
            root,
            ruling_commit,
            SUCCESSOR_V2_1_SCOPE_CORRECTION_RULING_PATH,
            "successor scope-correction owner ruling",
        )
        != ruling_path.read_bytes()
        or _git_blob(
            root,
            authorization_commit,
            SUCCESSOR_V2_1_CONTROL_PLANE_AUTHORIZATION_PATH,
            "successor control-plane registry authorization",
        )
        != authorization_path.read_bytes()
    ):
        raise HeldoutPreparationError(
            "successor control-plane registry authorization history drifted"
        )
    parents = _git(
        root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        authorization_commit,
    ).strip().split()
    if parents != [authorization_commit, ruling_commit]:
        raise HeldoutPreparationError(
            "successor control-plane authorization is not directly based on the ruling"
        )
    return appended


def _registered_successor_implementation_paths(root: Path) -> tuple[Path, ...]:
    _require_exact_regular_file(
        root,
        SUCCESSOR_V2_1_PREREGISTRATION_PATH,
        SUCCESSOR_V2_1_PREREGISTRATION_SHA256,
        "successor preregistration",
    )
    _require_exact_regular_file(
        root,
        SUCCESSOR_V2_1_TIMING_PREREGISTRATION_PATH,
        SUCCESSOR_V2_1_TIMING_PREREGISTRATION_SHA256,
        "successor timing preregistration",
    )
    preregistration = _load_json(
        root / SUCCESSOR_V2_1_PREREGISTRATION_PATH,
        "successor preregistration",
    )
    contract = _mapping(
        preregistration.get("implementation_contract"),
        "successor implementation contract",
    )
    fields = (
        "registered_modified_consumers",
        "registered_new_files",
        "registered_existing_test_updates",
    )
    paths: list[Path] = []
    for field in fields:
        values = contract.get(field)
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value for value in values
        ):
            raise HeldoutPreparationError(f"successor {field} registry drifted")
        paths.extend(Path(cast(str, value)) for value in values)
    paths.extend(
        (
            SUCCESSOR_V2_1_TIMING_PREREGISTRATION_PATH,
            SUCCESSOR_V2_1_TIMING_SOURCE_PATH,
            SUCCESSOR_V2_1_TIMING_TEST_PATH,
        )
    )
    appended = _validate_control_plane_registry_expansion(root)
    if len(paths) - 1 != SUCCESSOR_V2_1_PREEXPANSION_SURFACE_COUNT:
        raise HeldoutPreparationError(
            "successor pre-expansion implementation registry count drifted"
        )
    paths.append(appended)
    if len(paths) - 1 != SUCCESSOR_V2_1_EXPANDED_SURFACE_COUNT:
        raise HeldoutPreparationError(
            "successor expanded implementation registry count drifted"
        )
    if len(paths) != len(set(paths)):
        raise HeldoutPreparationError("successor implementation registry has duplicates")
    return tuple(paths)


def _validate_successor_implementation_commit_surface(
    root: Path,
    implementation_commit: str,
) -> None:
    """Require a non-merge implementation commit and exact post-prereg A/M surface."""

    preregistration_path = _require_exact_regular_file(
        root,
        SUCCESSOR_V2_1_PREREGISTRATION_PATH,
        SUCCESSOR_V2_1_PREREGISTRATION_SHA256,
        "successor preregistration",
    )
    preregistration = _load_json(
        preregistration_path,
        "successor preregistration",
    )
    contract = _mapping(
        preregistration.get("implementation_contract"),
        "successor implementation contract",
    )
    expected: dict[str, str] = {}
    for field, status in (
        ("registered_modified_consumers", "M"),
        ("registered_new_files", "A"),
        ("registered_existing_test_updates", "M"),
    ):
        values = contract.get(field)
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value for value in values
        ):
            raise HeldoutPreparationError(f"successor {field} registry drifted")
        for value in values:
            relative = cast(str, value)
            if relative in expected:
                raise HeldoutPreparationError(
                    "successor implementation registry has duplicates"
                )
            expected[relative] = status
    for timing_relative in (
        SUCCESSOR_V2_1_TIMING_SOURCE_PATH,
        SUCCESSOR_V2_1_TIMING_TEST_PATH,
    ):
        if timing_relative.as_posix() in expected:
            raise HeldoutPreparationError(
                "successor timing implementation registry has duplicates"
            )
        expected[timing_relative.as_posix()] = "M"
    if len(expected) != SUCCESSOR_V2_1_PREEXPANSION_SURFACE_COUNT:
        raise HeldoutPreparationError(
            "successor pre-expansion implementation surface count drifted"
        )
    appended = _validate_control_plane_registry_expansion(root).as_posix()
    if appended in expected:
        raise HeldoutPreparationError(
            "successor control-plane implementation registry has duplicates"
        )
    expected[appended] = "M"
    if len(expected) != SUCCESSOR_V2_1_EXPANDED_SURFACE_COUNT:
        raise HeldoutPreparationError(
            "successor expanded implementation surface count drifted"
        )

    parents = _git(
        root,
        "rev-list",
        "--parents",
        "-n",
        "1",
        implementation_commit,
    ).strip().split()
    if parents != [
        implementation_commit,
        SUCCESSOR_V2_1_CONTROL_PLANE_AUTHORIZATION_COMMIT,
    ]:
        raise HeldoutPreparationError(
            "successor implementation commit must directly follow the control-plane authorization"
        )
    observed: dict[str, str] = {}
    for raw_line in _git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--name-status",
        "--no-renames",
        SUCCESSOR_V2_1_CONTROL_PLANE_AUTHORIZATION_COMMIT,
        implementation_commit,
        "--",
    ).splitlines():
        fields = raw_line.split("\t")
        if len(fields) != 2 or fields[0] not in {"A", "M"} or not fields[1]:
            raise HeldoutPreparationError(
                "successor implementation commit contains a forbidden path operation"
            )
        if fields[1] in observed:
            raise HeldoutPreparationError(
                "successor implementation commit contains duplicate path operations"
            )
        observed[fields[1]] = fields[0]
    if observed != expected:
        raise HeldoutPreparationError(
            "successor implementation commit path/status surface drifted"
        )


def _validate_offline_implementation_binding(
    binding: HeldoutBinding,
    implementation_commit: str,
) -> None:
    commit = _require_git_commit(
        PROJECT_ROOT, implementation_commit, "offline implementation commit"
    )
    _require_git_ancestor(
        PROJECT_ROOT,
        SUCCESSOR_V2_1_PREREGISTRATION_COMMIT,
        commit,
        "offline implementation/preregistration",
    )
    _require_git_ancestor(
        PROJECT_ROOT,
        SUCCESSOR_V2_1_TIMING_PREREGISTRATION_COMMIT,
        commit,
        "offline implementation/timing preregistration",
    )
    _validate_prediction_timing_preregistration_history(PROJECT_ROOT, commit)
    for relative in _registered_successor_implementation_paths(PROJECT_ROOT):
        canonical = PROJECT_ROOT / relative
        isolated = binding.root / relative
        if (
            canonical.is_symlink()
            or not canonical.is_file()
            or isolated.is_symlink()
            or not isolated.is_file()
        ):
            raise HeldoutPreparationError(
                f"offline registered implementation file is unavailable: {relative}"
            )
        committed = _git_blob(
            PROJECT_ROOT, commit, relative, f"offline implementation {relative}"
        )
        if canonical.read_bytes() != committed or isolated.read_bytes() != committed:
            raise HeldoutPreparationError(
                f"offline implementation bytes differ from registered commit: {relative}"
            )


def _mint_v2_1_offline_rehearsal_capability_impl(
    binding: HeldoutBinding,
    *,
    database: Path,
    pdf_fetcher: gold_builder.PdfFetcher,
    pdf_text_extractor: gold_builder.PdfTextExtractor,
    monotonic: MonotonicClock,
    sleep: Sleeper,
    inference_settings: Settings,
    chat_json_fn: ChatJsonCallable,
    snapshot_loader: ProductionSnapshotLoader,
    wall_clock: Clock,
    execution_id_factory: ExecutionIdFactory,
    prediction_recorded_at_clock: RecordedAtClock,
    prediction_monotonic_ns_clock: MonotonicNsClock,
    implementation_commit: str,
) -> _OfflineRehearsalCapability:
    root = binding.root.resolve()
    canonical = PROJECT_ROOT.resolve()
    if (
        root == canonical
        or root.is_relative_to(canonical)
        or canonical.is_relative_to(root)
    ):
        raise HeldoutPreparationError(
            "offline rehearsal root must be distinct and outside the canonical repository"
        )
    if binding.root.absolute() != root or binding.root.is_symlink() or not root.is_dir():
        raise HeldoutPreparationError("offline rehearsal root is unavailable")
    database_path = database.resolve()
    if (
        database.absolute() != database_path
        or database.is_symlink()
        or not database_path.is_relative_to(root)
        or not database_path.is_file()
    ):
        raise HeldoutPreparationError(
            "offline rehearsal database must be a regular file inside the temporary root"
        )
    raw_artifact_paths = tuple(binding.artifacts.values())
    artifact_paths = tuple(sorted(path.resolve() for path in raw_artifact_paths))
    if (
        any(path.absolute() != path.resolve() for path in raw_artifact_paths)
        or any(path.is_symlink() for path in raw_artifact_paths)
        or any(not path.is_relative_to(root) for path in artifact_paths)
        or len({path.as_posix().casefold() for path in artifact_paths}) != len(artifact_paths)
    ):
        raise HeldoutPreparationError("offline rehearsal artifact paths escape or alias")
    _require_exact_regular_file(
        root,
        SUCCESSOR_V2_1_PREREGISTRATION_PATH,
        SUCCESSOR_V2_1_PREREGISTRATION_SHA256,
        "offline successor preregistration",
    )
    _require_exact_regular_file(
        root,
        SUCCESSOR_V2_1_BUNDLE_SCHEMA_PATH,
        SUCCESSOR_V2_1_BUNDLE_SCHEMA_SHA256,
        "offline successor bundle schema",
    )
    _require_exact_regular_file(
        root,
        SUCCESSOR_V2_1_RELEASE_SCHEMA_PATH,
        SUCCESSOR_V2_1_RELEASE_SCHEMA_SHA256,
        "offline successor release schema",
    )
    _require_exact_regular_file(
        root, FRAME_AUTHORITY_PATH, FRAME_AUTHORITY_SHA256, "offline frame authority"
    )
    _require_exact_regular_file(
        root,
        SUCCESSOR_CODE_GATE_AUTHORITY_PATH,
        SUCCESSOR_CODE_GATE_AUTHORITY_SHA256,
        "offline successor code-gate authority",
    )
    for callable_value, label in (
        (pdf_fetcher, "offline PDF fetcher"),
        (pdf_text_extractor, "offline PDF extractor"),
        (monotonic, "offline monotonic clock"),
        (sleep, "offline sleeper"),
        (chat_json_fn, "offline inference model"),
        (snapshot_loader, "offline production snapshot loader"),
        (wall_clock, "offline wall clock"),
        (execution_id_factory, "offline execution id factory"),
        (prediction_recorded_at_clock, "offline prediction recorded-at clock"),
        (prediction_monotonic_ns_clock, "offline prediction monotonic-ns clock"),
    ):
        if not callable(callable_value):
            raise HeldoutPreparationError(f"{label} must be callable")
    if not isinstance(inference_settings, Settings):
        raise HeldoutPreparationError("offline inference settings must be Settings")
    _validate_offline_implementation_binding(binding, implementation_commit)
    return _OfflineRehearsalCapability(
        _nonce=_OFFLINE_CAPABILITY_NONCE,
        project_root=root,
        database=database_path,
        artifact_paths=artifact_paths,
        pdf_fetcher=pdf_fetcher,
        pdf_text_extractor=pdf_text_extractor,
        monotonic=monotonic,
        sleep=sleep,
        inference_settings=inference_settings,
        chat_json_fn=chat_json_fn,
        snapshot_loader=snapshot_loader,
        wall_clock=wall_clock,
        execution_id_factory=execution_id_factory,
        prediction_recorded_at_clock=prediction_recorded_at_clock,
        prediction_monotonic_ns_clock=prediction_monotonic_ns_clock,
        preregistration_commit=SUCCESSOR_V2_1_PREREGISTRATION_COMMIT,
        implementation_commit=implementation_commit,
    )


def _offline_capability_identity_api() -> tuple[
    Callable[..., _OfflineRehearsalCapability],
    Callable[[_OfflineRehearsalCapability], bool],
]:
    minted: dict[int, _OfflineRehearsalCapability] = {}

    def mint(
        binding: HeldoutBinding,
        *,
        database: Path,
        pdf_fetcher: gold_builder.PdfFetcher,
        pdf_text_extractor: gold_builder.PdfTextExtractor,
        monotonic: MonotonicClock,
        sleep: Sleeper,
        inference_settings: Settings,
        chat_json_fn: ChatJsonCallable,
        snapshot_loader: ProductionSnapshotLoader,
        wall_clock: Clock,
        execution_id_factory: ExecutionIdFactory,
        prediction_recorded_at_clock: RecordedAtClock,
        prediction_monotonic_ns_clock: MonotonicNsClock,
        implementation_commit: str,
    ) -> _OfflineRehearsalCapability:
        capability = _mint_v2_1_offline_rehearsal_capability_impl(
            binding,
            database=database,
            pdf_fetcher=pdf_fetcher,
            pdf_text_extractor=pdf_text_extractor,
            monotonic=monotonic,
            sleep=sleep,
            inference_settings=inference_settings,
            chat_json_fn=chat_json_fn,
            snapshot_loader=snapshot_loader,
            wall_clock=wall_clock,
            execution_id_factory=execution_id_factory,
            prediction_recorded_at_clock=prediction_recorded_at_clock,
            prediction_monotonic_ns_clock=prediction_monotonic_ns_clock,
            implementation_commit=implementation_commit,
        )
        minted[id(capability)] = capability
        return capability

    def is_minted(capability: _OfflineRehearsalCapability) -> bool:
        return minted.get(id(capability)) is capability

    return mint, is_minted


(
    _mint_v2_1_offline_rehearsal_capability,
    _is_minted_v2_1_offline_rehearsal_capability,
) = _offline_capability_identity_api()
del _offline_capability_identity_api


def _validate_v2_1_offline_capability(
    binding: HeldoutBinding,
    capability: _OfflineRehearsalCapability,
) -> _OfflineRehearsalCapability:
    if (
        capability._nonce is not _OFFLINE_CAPABILITY_NONCE
        or not _is_minted_v2_1_offline_rehearsal_capability(capability)
        or capability.project_root != binding.root.resolve()
        or capability.preregistration_commit != SUCCESSOR_V2_1_PREREGISTRATION_COMMIT
        or capability.artifact_paths
        != tuple(sorted(path.resolve() for path in binding.artifacts.values()))
        or capability.project_root == PROJECT_ROOT.resolve()
        or capability.project_root.is_relative_to(PROJECT_ROOT.resolve())
        or PROJECT_ROOT.resolve().is_relative_to(capability.project_root)
        or capability.database.is_symlink()
        or not capability.database.is_file()
        or not capability.database.is_relative_to(capability.project_root)
        or any(
            path.is_symlink() or path.absolute() != path.resolve()
            for path in binding.artifacts.values()
        )
        or not callable(capability.pdf_fetcher)
        or not callable(capability.pdf_text_extractor)
        or not callable(capability.monotonic)
        or not callable(capability.sleep)
        or not isinstance(capability.inference_settings, Settings)
        or not callable(capability.chat_json_fn)
        or not callable(capability.snapshot_loader)
        or not callable(capability.wall_clock)
        or not callable(capability.execution_id_factory)
        or not callable(capability.prediction_recorded_at_clock)
        or not callable(capability.prediction_monotonic_ns_clock)
    ):
        raise HeldoutPreparationError("offline rehearsal capability is forged or drifted")
    _require_exact_regular_file(
        binding.root,
        SUCCESSOR_V2_1_PREREGISTRATION_PATH,
        SUCCESSOR_V2_1_PREREGISTRATION_SHA256,
        "offline successor preregistration",
    )
    _require_exact_regular_file(
        binding.root,
        SUCCESSOR_V2_1_BUNDLE_SCHEMA_PATH,
        SUCCESSOR_V2_1_BUNDLE_SCHEMA_SHA256,
        "offline successor bundle schema",
    )
    _require_exact_regular_file(
        binding.root,
        SUCCESSOR_V2_1_RELEASE_SCHEMA_PATH,
        SUCCESSOR_V2_1_RELEASE_SCHEMA_SHA256,
        "offline successor release schema",
    )
    _require_exact_regular_file(
        binding.root,
        FRAME_AUTHORITY_PATH,
        FRAME_AUTHORITY_SHA256,
        "offline frame authority",
    )
    _require_exact_regular_file(
        binding.root,
        SUCCESSOR_CODE_GATE_AUTHORITY_PATH,
        SUCCESSOR_CODE_GATE_AUTHORITY_SHA256,
        "offline successor code-gate authority",
    )
    _validate_offline_implementation_binding(binding, capability.implementation_commit)
    return capability


def validate_v2_1_stage_authorization(
    binding: HeldoutBinding,
    *,
    stage: str,
    execution_context: ExecutionContext = None,
) -> V21ReleaseAuthorization | _OfflineRehearsalCapability:
    """Fail closed at every successor mutating stage before business input reads."""

    if stage not in _OFFLINE_ALLOWED_STAGES:
        raise HeldoutPreparationError(f"unknown successor stage authorization: {stage}")
    if isinstance(execution_context, _OfflineRehearsalCapability):
        capability = _validate_v2_1_offline_capability(binding, execution_context)
        if stage not in _OFFLINE_ALLOWED_STAGES:
            raise HeldoutPreparationError(f"offline successor stage is not authorized: {stage}")
        return capability
    if stage not in _REAL_ALLOWED_STAGES:
        if stage == "finalize-owner-adjudication":
            raise HeldoutPreparationError(
                "REJECTED_PENDING_OWNER_ADJUDICATION_AUTHORITY"
            )
        raise HeldoutPreparationError("held-out evaluation remains locked")
    if binding.root.resolve() != PROJECT_ROOT.resolve():
        raise HeldoutPreparationError(
            "synthetic successor receipt cannot unlock a noncanonical real stage"
        )
    _validate_canonical_runtime_environment(binding, stage=stage)
    observed = validate_v2_1_release_authorization(binding.root)
    _validate_canonical_runtime_module_origins(
        binding,
        stage=stage,
        authorization=observed,
    )
    if execution_context is not None:
        if not isinstance(execution_context, V21ReleaseAuthorization):
            raise HeldoutPreparationError("successor execution context is forged")
        if execution_context != observed:
            raise HeldoutPreparationError("successor release context drifted")
    return observed


def _prevalidated_stage_authority_identity_api() -> tuple[
    Callable[..., _PrevalidatedStageAuthority],
    Callable[
        [HeldoutBinding, _PrevalidatedStageAuthority, str],
        V21ReleaseAuthorization | _OfflineRehearsalCapability,
    ],
]:
    minted: dict[int, _PrevalidatedStageAuthority] = {}

    def prevalidate(
        binding: HeldoutBinding,
        *,
        stage: str,
        execution_context: ExecutionContext = None,
    ) -> _PrevalidatedStageAuthority:
        authorization = validate_v2_1_stage_authorization(
            binding,
            stage=stage,
            execution_context=execution_context,
        )
        delegated = _PrevalidatedStageAuthority(
            _nonce=_PREVALIDATED_STAGE_AUTHORITY_NONCE,
            project_root=binding.root.resolve(),
            validated_stage=stage,
            authorization=authorization,
        )
        minted[id(delegated)] = delegated
        return delegated

    def consume(
        binding: HeldoutBinding,
        delegated: _PrevalidatedStageAuthority,
        validated_stage: str,
    ) -> V21ReleaseAuthorization | _OfflineRehearsalCapability:
        if (
            delegated._nonce is not _PREVALIDATED_STAGE_AUTHORITY_NONCE
            or minted.get(id(delegated)) is not delegated
            or delegated.project_root != binding.root.resolve()
            or delegated.validated_stage != validated_stage
            or validated_stage not in _OFFLINE_ALLOWED_STAGES
            or not isinstance(
                delegated.authorization,
                (V21ReleaseAuthorization, _OfflineRehearsalCapability),
            )
        ):
            raise HeldoutPreparationError(
                "prevalidated stage authority is forged, cross-stage, or drifted"
            )
        observed = validate_v2_1_stage_authorization(
            binding,
            stage=validated_stage,
            execution_context=delegated.authorization,
        )
        if observed != delegated.authorization:
            raise HeldoutPreparationError(
                "prevalidated stage authority changed during current-stage revalidation"
            )
        return observed

    return prevalidate, consume


(
    _prevalidate_v2_1_stage_authorization,
    _consume_prevalidated_v2_1_stage_authorization,
) = _prevalidated_stage_authority_identity_api()
del _prevalidated_stage_authority_identity_api


def _pure_revalidation_authority(
    binding: HeldoutBinding,
    *,
    validated_stage: str,
    execution_context: ExecutionContext,
    prevalidated_authority: _PrevalidatedStageAuthority | None,
) -> tuple[
    V21ReleaseAuthorization | _OfflineRehearsalCapability,
    _PrevalidatedStageAuthority,
]:
    if prevalidated_authority is not None:
        if execution_context is not None:
            raise HeldoutPreparationError(
                "prevalidated authority requires one explicit stage and no second context"
            )
        return (
            _consume_prevalidated_v2_1_stage_authorization(
                binding,
                prevalidated_authority,
                validated_stage,
            ),
            prevalidated_authority,
        )
    delegated = _prevalidate_v2_1_stage_authorization(
        binding,
        stage=validated_stage,
        execution_context=execution_context,
    )
    return (
        _consume_prevalidated_v2_1_stage_authorization(
            binding,
            delegated,
            validated_stage,
        ),
        delegated,
    )


class _CninfoStartPacer:
    def __init__(self, monotonic: MonotonicClock, sleep: Sleeper) -> None:
        self._monotonic = monotonic
        self._sleep = sleep
        self._starts: list[float] = []

    @staticmethod
    def _valid_clock_value(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise HeldoutPreparationError("monotonic clock returned a non-number")
        observed = float(value)
        if not math.isfinite(observed):
            raise HeldoutPreparationError("monotonic clock returned a non-finite value")
        return observed

    def before_fetch(self) -> None:
        observed = self._valid_clock_value(self._monotonic())
        if self._starts and observed < self._starts[-1]:
            raise HeldoutPreparationError("monotonic clock reversed before CNInfo fetch")
        while self._starts and observed - self._starts[-1] < CNINFO_MIN_START_TO_START_SECONDS:
            remaining = CNINFO_MIN_START_TO_START_SECONDS - (observed - self._starts[-1])
            self._sleep(remaining)
            advanced = self._valid_clock_value(self._monotonic())
            if advanced < observed:
                raise HeldoutPreparationError("monotonic clock reversed after CNInfo pacing")
            if advanced == observed:
                raise HeldoutPreparationError(
                    "monotonic clock did not advance after CNInfo pacing sleep"
                )
            observed = advanced
        if self._starts and observed - self._starts[-1] < CNINFO_MIN_START_TO_START_SECONDS:
            raise HeldoutPreparationError("CNInfo pacing floor was not reached")
        self._starts.append(observed)

    def evidence(self) -> JsonObject:
        gaps = [
            right - left
            for left, right in zip(self._starts, self._starts[1:], strict=False)
        ]
        violations = sum(gap < CNINFO_MIN_START_TO_START_SECONDS for gap in gaps)
        return {
            "host": "static.cninfo.com.cn",
            "policy": "minimum_start_to_start",
            "configured_min_start_to_start_seconds": CNINFO_MIN_START_TO_START_SECONDS,
            "clock": "monotonic",
            "first_request_delayed": False,
            "request_start_count": len(self._starts),
            "observed_gap_count": len(gaps),
            "minimum_observed_start_to_start_seconds": min(gaps) if gaps else None,
            "median_observed_start_to_start_seconds": statistics.median(gaps)
            if gaps
            else None,
            "violation_count": violations,
            "retry_count": 0,
        }


def _paced_pdf_boundaries(
    pacer: _CninfoStartPacer,
    pdf_fetcher: gold_builder.PdfFetcher,
    pdf_text_extractor: gold_builder.PdfTextExtractor,
) -> tuple[gold_builder.PdfFetcher, gold_builder.PdfTextExtractor]:
    def paced_fetch(
        url: str,
        policy: gold_builder.AnnouncementBodyPolicy,
    ) -> bytes:
        pacer.before_fetch()
        try:
            payload: bytes = pdf_fetcher(url, policy)
            return payload
        except gold_builder.CandidateDocumentIneligible as exc:
            if exc.reason != "pdf_exceeds_size_bound":
                raise HeldoutPreparationError(
                    f"unknown deterministic candidate reason: {exc.reason}"
                ) from exc
            raise

    def checked_extract(
        pdf_bytes: bytes,
        policy: gold_builder.AnnouncementBodyPolicy,
    ) -> gold_builder.ExtractedPdfText:
        try:
            return pdf_text_extractor(pdf_bytes, policy)
        except gold_builder.CandidateDocumentIneligible as exc:
            if exc.reason != "pdf_text_below_min_char_gate":
                raise HeldoutPreparationError(
                    f"unknown deterministic candidate reason: {exc.reason}"
                ) from exc
            raise

    return paced_fetch, checked_extract


def _operator_attestation_evidence(
    attestation: OperatorTimingAttestation | None,
    *,
    observed_start_shanghai: datetime,
) -> JsonObject:
    if (
        observed_start_shanghai.tzinfo is None
        or observed_start_shanghai.utcoffset() is None
        or observed_start_shanghai.utcoffset()
        != _SHANGHAI.utcoffset(observed_start_shanghai)
    ):
        raise HeldoutPreparationError(
            "operator timing attestation start must be an Asia/Shanghai instant"
        )
    if not isinstance(attestation, OperatorTimingAttestation):
        raise HeldoutPreparationError(
            "real materialization requires an explicitly supplied operator timing attestation"
        )
    identity = attestation.attester_identity.strip()
    if not identity:
        raise HeldoutPreparationError("operator timing attester identity must be nonempty")
    if (
        attestation.cninfo_midnight_batch_assessment != "clear_for_start"
        or attestation.p4_1_dense_poll_slot_assessment != "clear_for_start"
    ):
        raise HeldoutPreparationError(
            "both operator timing assessments must explicitly be clear_for_start"
        )
    return {
        "observed_start_cst": observed_start_shanghai.isoformat(),
        "attester_identity": identity,
        "explicitly_supplied": True,
        "input_channel": (
            "required_real_CLI_flags_or_required_typed_run_materialize_argument_no_default"
        ),
        "cninfo_midnight_batch_assessment": "clear_for_start",
        "p4_1_dense_poll_slot_assessment": "clear_for_start",
        "decision": (
            "launched_outside_owner_identified_CNInfo_midnight_and_dense_P4_1_slots"
        ),
        "automatic_blackout_verification": False,
        "authority_path": SUCCESSOR_CODE_GATE_AUTHORITY_PATH.as_posix(),
        "authority_sha256": SUCCESSOR_CODE_GATE_AUTHORITY_SHA256,
    }


def _database_backup_runtime_directory() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "AlphaPilot-AI"
        / "database-backup"
    )


def _launchctl_print(target: str) -> str:
    completed = subprocess.run(
        ["/bin/launchctl", "print", target],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise HeldoutPreparationError("database backup LaunchAgent is not loaded")
    return completed.stdout


def _parse_launchagent_evidence(*, label: str, target: str, output: str) -> JsonObject:
    if label != "com.alphapilot.database-backup" or target != f"gui/{os.getuid()}/{label}":
        raise HeldoutPreparationError("database backup LaunchAgent identity drifted")
    states = re.findall(r"(?m)^\s*state = (.+?)\s*$", output)
    exit_codes = re.findall(r"(?m)^\s*last exit code = (-?\d+)\s*$", output)
    if len(states) != 1 or len(exit_codes) != 1:
        raise HeldoutPreparationError("database backup LaunchAgent evidence is incomplete")
    state = states[0]
    last_exit_code = int(exit_codes[0])
    if state != "not running" or last_exit_code != 0:
        raise HeldoutPreparationError(
            "database backup LaunchAgent must be not running with last exit code 0"
        )
    return {
        "label": label,
        "target": target,
        "loaded": True,
        "state": state,
        "last_exit_code": last_exit_code,
    }


def _launchagent_evidence() -> JsonObject:
    label = "com.alphapilot.database-backup"
    target = f"gui/{os.getuid()}/{label}"
    return _parse_launchagent_evidence(
        label=label,
        target=target,
        output=_launchctl_print(target),
    )


def _backup_stamp_evidence(runtime_directory: Path, shanghai_date: str) -> JsonObject:
    stamp = runtime_directory / "last-success-shanghai-date"
    try:
        metadata = stamp.lstat()
    except OSError as exc:
        raise HeldoutPreparationError("database backup success stamp is unavailable") from exc
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        stamp.absolute() != stamp.resolve()
        or not stat.S_ISREG(metadata.st_mode)
        or stamp.is_symlink()
        or mode != 0o600
    ):
        raise HeldoutPreparationError(
            "database backup success stamp must be a regular non-symlink mode 0600 file"
        )
    expected = f"{shanghai_date}\n".encode()
    try:
        payload = stamp.read_bytes()
    except OSError as exc:
        raise HeldoutPreparationError("database backup success stamp is unreadable") from exc
    if payload != expected:
        raise HeldoutPreparationError("database backup success stamp is stale or malformed")
    return {
        "path": str(stamp),
        "expected_shanghai_date": shanghai_date,
        "observed_value": shanghai_date,
        "regular_file": True,
        "symlink": False,
        "mode": "0600",
    }


def _backup_lock_evidence(runtime_directory: Path) -> JsonObject:
    lock_path = runtime_directory / ".daily-backup.lock"
    try:
        metadata = lock_path.lstat()
    except OSError as exc:
        raise HeldoutPreparationError("daily database backup lock file is unavailable") from exc
    if (
        lock_path.absolute() != lock_path.resolve()
        or not stat.S_ISREG(metadata.st_mode)
        or lock_path.is_symlink()
    ):
        raise HeldoutPreparationError("daily database backup lock must be a regular file")
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags)
    except OSError as exc:
        raise HeldoutPreparationError("daily database backup lock cannot be opened") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise HeldoutPreparationError("daily database backup lock is held") from exc
        finally:
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
    return {
        "path": str(lock_path),
        "nonblocking_exclusive_flock_acquired": True,
        "held": False,
    }


def _parse_aware_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise HeldoutPreparationError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HeldoutPreparationError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HeldoutPreparationError(f"{label} must be timezone-aware")
    return parsed


def _verified_backup_evidence(binding: HeldoutBinding, observed_at: datetime) -> JsonObject:
    directory = binding.root / "data/backups"
    if (
        directory.absolute() != directory.resolve()
        or directory.is_symlink()
        or not directory.is_dir()
    ):
        raise HeldoutPreparationError("database backup directory is unavailable")
    candidates: list[tuple[datetime, Path, JsonObject]] = []
    for manifest_path in directory.glob("alphapilot-full-*.manifest.json"):
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise HeldoutPreparationError(
                "database backup manifest inventory contains a non-regular entry"
            )
        manifest = _load_json(manifest_path, "database backup manifest")
        created_at = _parse_aware_timestamp(
            manifest.get("created_at"), "database backup created_at"
        )
        created_shanghai = created_at.astimezone(_SHANGHAI)
        observed_shanghai = observed_at.astimezone(_SHANGHAI)
        if (
            created_shanghai.date() != observed_shanghai.date()
            or created_shanghai.hour < 22
        ):
            continue
        candidates.append((created_at, manifest_path, manifest))
    if not candidates:
        raise HeldoutPreparationError(
            "no current Shanghai-date post-22:00 database backup manifest is available"
        )
    created_at, manifest_path, manifest = max(candidates, key=lambda item: item[0])
    if (
        manifest.get("format_version") != database_backup.BACKUP_FORMAT_VERSION
        or manifest.get("managed_by") != database_backup.BACKUP_MANAGED_BY
    ):
        raise HeldoutPreparationError(
            "latest database backup manifest format or authority drifted"
        )
    if created_at.astimezone(UTC) > observed_at.astimezone(UTC):
        raise HeldoutPreparationError(
            "latest database backup manifest is future-dated"
        )
    backup = _mapping(manifest.get("backup"), "database backup manifest.backup")
    filename = backup.get("filename")
    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
    ):
        raise HeldoutPreparationError("database backup manifest filename is invalid")
    backup_path = directory / filename
    if (
        manifest_path != database_backup.manifest_path_for(backup_path)
        or backup_path.is_symlink()
        or not backup_path.is_file()
    ):
        raise HeldoutPreparationError("verified database backup file is unavailable")
    try:
        verified = database_backup.verify_database_backup(backup_path, manifest_path)
    except Exception as exc:
        raise HeldoutPreparationError("latest database backup failed verification") from exc
    if (
        verified.get("verified") is not True
        or verified.get("quick_check") != "ok"
        or verified.get("sha256") != backup.get("sha256")
    ):
        raise HeldoutPreparationError("latest database backup verification evidence drifted")
    return {
        "manifest_path": str(manifest_path),
        "manifest_sha256": common.sha256_file(manifest_path),
        "backup_path": str(backup_path),
        "backup_sha256": _require_sha256(verified.get("sha256"), "backup SHA"),
        "created_at_utc": created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "created_at_shanghai": created_at.astimezone(_SHANGHAI).isoformat(),
        "quick_check": "ok",
        "verify_database_backup_passed": True,
    }


def _real_runtime_start_preflight(
    binding: HeldoutBinding,
    attestation: OperatorTimingAttestation | None,
) -> JsonObject:
    observed_at = _system_clock()
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise HeldoutPreparationError("runtime start clock must be timezone-aware")
    observed_at = observed_at.astimezone(UTC)
    observed_shanghai = observed_at.astimezone(_SHANGHAI)
    operator = _operator_attestation_evidence(
        attestation, observed_start_shanghai=observed_shanghai
    )
    runtime_directory = _database_backup_runtime_directory()
    return {
        "mode": "real",
        "observed_at_utc": observed_at.isoformat().replace("+00:00", "Z"),
        "observed_at_shanghai": observed_shanghai.isoformat(),
        "backup_stamp": _backup_stamp_evidence(
            runtime_directory, observed_shanghai.date().isoformat()
        ),
        "database_backup_launchagent": _launchagent_evidence(),
        "database_backup_lock": _backup_lock_evidence(runtime_directory),
        "verified_backup": _verified_backup_evidence(binding, observed_at),
        "operator_timing_attestation": operator,
    }


def _offline_runtime_start_preflight() -> JsonObject:
    return {
        "mode": "offline_rehearsal",
        "host_probe_performed": False,
        "reason": "not_applicable_offline_rehearsal",
    }


def _execution_authority_evidence(
    context: V21ReleaseAuthorization | _OfflineRehearsalCapability,
) -> JsonObject:
    common_fields: JsonObject = {
        "frame_authority": {
            "path": FRAME_AUTHORITY_PATH.as_posix(),
            "sha256": FRAME_AUTHORITY_SHA256,
        },
        "successor_code_gate_authority": {
            "path": SUCCESSOR_CODE_GATE_AUTHORITY_PATH.as_posix(),
            "sha256": SUCCESSOR_CODE_GATE_AUTHORITY_SHA256,
        },
        "successor_preregistration": {
            "path": SUCCESSOR_V2_1_PREREGISTRATION_PATH.as_posix(),
            "sha256": SUCCESSOR_V2_1_PREREGISTRATION_SHA256,
        },
        "preregistration_commit": SUCCESSOR_V2_1_PREREGISTRATION_COMMIT,
        "implementation_commit": context.implementation_commit,
    }
    if isinstance(context, _OfflineRehearsalCapability):
        return {
            "mode": "offline_rehearsal",
            **common_fields,
            "rehearsal_bundle": None,
            "release_authorization": None,
        }
    return {
        "mode": "real_owner_released",
        **common_fields,
        "rehearsal_bundle": {
            "path": SUCCESSOR_V2_1_BUNDLE_PATH.as_posix(),
            "sha256": context.bundle_sha256,
            "bundle_root_sha256": context.bundle_root_sha256,
        },
        "release_authorization": {
            "path": SUCCESSOR_V2_1_RELEASE_PATH.as_posix(),
            "sha256": context.receipt_sha256,
            "receipt_creating_commit": context.receipt_creating_commit,
            "verdict": SUCCESSOR_V2_1_RELEASE_VERDICT,
        },
    }


def _assert_exact(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise HeldoutPreparationError(f"{label} drifted")


def _artifact_map(root: Path, prereg: Mapping[str, Any]) -> dict[str, Path]:
    raw = _mapping(prereg.get("artifacts"), "preregistration.artifacts")
    expected = {
        "synthetic_rehearsal",
        "materialized_inputs",
        "materialization_manifest",
        "inference_state",
        "predictions",
        "prediction_manifest",
        "private_selection",
        "owner_blind",
        "ai_draft",
        "adjudication_ui",
        "owner_export",
        "human_adjudicated",
        "owner_completion",
        "evaluation_state",
        "report_directory",
    }
    if set(raw) != expected:
        raise HeldoutPreparationError("preregistration artifact registry drifted")
    return {name: _resolve(root, value, f"artifact.{name}") for name, value in raw.items()}


def _verified_source_lineage(binding: HeldoutBinding) -> JsonObject:
    frames = _mapping(binding.design.get("frames"), "design.frames")
    heldout = _mapping(frames.get("heldout_frame_v2"), "design heldout frame")
    design_lineage = _mapping(heldout.get("source_lineage"), "design source lineage")
    prereg_source = _mapping(binding.preregistration.get("source_frame"), "source frame")
    prereg_lineage = _mapping(prereg_source.get("source_lineage"), "prereg source lineage")
    reference_names = {
        "round3_evidence": "round_3_evidence_sha256",
        "round3_independent_review": "round_3_independent_review_sha256",
        "incremental_evidence": "incremental_evidence_sha256",
        "incremental_independent_review": "incremental_independent_review_sha256",
    }
    evidence: JsonObject = {}
    for design_name, prereg_name in reference_names.items():
        ref = _mapping(design_lineage.get(design_name), f"source lineage {design_name}")
        path = _verify_file(binding.root, ref, f"source lineage {design_name}")
        _assert_exact(ref.get("sha256"), prereg_lineage.get(prereg_name), prereg_name)
        evidence[design_name] = {
            "path": path.relative_to(binding.root).as_posix(),
            "sha256": ref["sha256"],
        }
    required_dates = design_lineage.get("required_closed_dates_shanghai")
    checkpoint = design_lineage.get("verified_checkpoint_date_shanghai")
    job_runs = design_lineage.get("migration_job_run_ids")
    _assert_exact(
        required_dates, prereg_lineage.get("required_closed_dates_shanghai"), "closed dates"
    )
    _assert_exact(
        checkpoint, prereg_lineage.get("verified_checkpoint_date_shanghai"), "checkpoint date"
    )
    _assert_exact(job_runs, prereg_lineage.get("migration_job_run_ids"), "migration JobRuns")
    if required_dates != ["2026-08-06", "2026-08-07", "2026-08-08"]:
        raise HeldoutPreparationError("closed-date source lineage drifted")
    if checkpoint != "2026-08-08" or job_runs != [76932, 76933]:
        raise HeldoutPreparationError("checkpoint source lineage drifted")
    return {
        "required_closed_dates_shanghai": required_dates,
        "verified_checkpoint_date_shanghai": checkpoint,
        "migration_job_run_ids": job_runs,
        "evidence": evidence,
    }


def _load_selected_contract(root: Path) -> EventExtractContract:
    design = dev_runner._load_design(root)
    prereg, round_binding = dev_runner._load_preregistration(
        root,
        design,
        round_number=3,
        preregistration_path=dev_runner.ROUND_3_PREREGISTRATION_PATH,
        preregistration_sha256=cast(str, dev_runner.ROUND_3_PREREGISTRATION_SHA256),
    )
    bindings = dev_runner._load_contracts(root, prereg, round_binding)
    selected = next((item.contract for item in bindings if item.model_slug == MODEL), None)
    if selected is None or selected.path != (root / ROUND3_CONTRACT_PATH).resolve():
        raise HeldoutPreparationError("Round 3 selected contract is unavailable")
    if selected.sha256 != ROUND3_CONTRACT_SHA256:
        raise HeldoutPreparationError("Round 3 selected contract bytes drifted")
    heldout_path = root / HELDOUT_CONTRACT_PATH
    if (
        heldout_path.is_symlink()
        or not heldout_path.is_file()
        or common.sha256_file(heldout_path) != HELDOUT_CONTRACT_SHA256
    ):
        raise HeldoutPreparationError("held-out execution contract bytes drifted")
    try:
        wrapper: object = yaml.safe_load(heldout_path.read_bytes())
    except yaml.YAMLError as exc:
        raise HeldoutPreparationError("held-out execution contract is invalid") from exc
    document = _mapping(wrapper, "held-out execution contract")
    _assert_exact(document.get("heldout_access_allowed"), True, "heldout access")
    _assert_exact(document.get("production_writes_allowed"), False, "production writes")
    _assert_exact(
        document.get("artifact_root"), "docs/phase4/eval/v2-calibration/heldout", "artifact root"
    )
    _assert_exact(
        document.get("selected_development_contract"),
        {
            "path": ROUND3_CONTRACT_PATH.as_posix(),
            "sha256": ROUND3_CONTRACT_SHA256,
            "schema_version": "p4.2a-development-event-extract-contract-v2-r3",
            "inheritance": "inference_semantics_byte_frozen",
        },
        "selected development contract",
    )
    llm = _mapping(document.get("llm"), "held-out llm")
    _assert_exact(
        {
            key: llm.get(key)
            for key in (
                "model",
                "endpoint",
                "temperature",
                "enable_thinking",
                "max_output_tokens",
                "total_deadline_seconds",
                "max_retries",
                "response_format",
            )
        },
        {
            "model": MODEL,
            "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "temperature": 0.2,
            "enable_thinking": False,
            "max_output_tokens": 2000,
            "total_deadline_seconds": 20.0,
            "max_retries": 0,
            "response_format": "json_object",
        },
        "held-out LLM controls",
    )
    request = _mapping(document.get("request_shape"), "request_shape")
    for key in ("one_news_item_per_request", "one_request_per_eligible_candidate"):
        _assert_exact(request.get(key), True, key)
    _assert_exact(request.get("failed_candidate_retries"), 0, "failed candidate retries")
    _assert_exact(request.get("automatic_retries"), 0, "automatic retries")
    return replace(
        selected,
        path=heldout_path.resolve(),
        sha256=HELDOUT_CONTRACT_SHA256,
        max_items_per_run=EXPECTED_RAW_COUNT,
    )


def _retired_ids(root: Path, prereg: Mapping[str, Any]) -> frozenset[int]:
    eligibility = _mapping(prereg.get("eligibility_and_sampling"), "eligibility_and_sampling")
    retired = _mapping(eligibility.get("retired_selection"), "retired_selection")
    path = _verify_file(root, retired, "retired_selection")
    manifest = _load_json(path, "retired selection")
    selection = _mapping(manifest.get("selection"), "retired selection.selection")
    rows = selection.get("selected")
    if not isinstance(rows, list):
        raise HeldoutPreparationError("retired selection rows are invalid")
    identifiers: list[int] = []
    for raw in rows:
        item = _mapping(raw, "retired selection item")
        identifier = item.get("news_item_id")
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
            raise HeldoutPreparationError("retired selection contains an invalid id")
        identifiers.append(identifier)
    if len(identifiers) != len(set(identifiers)):
        raise HeldoutPreparationError("retired selection contains duplicate ids")
    _assert_exact(len(identifiers), retired.get("count"), "retired count")
    compact_ids = json.dumps(sorted(identifiers), separators=(",", ":")).encode("ascii")
    digest = common.sha256_bytes(compact_ids)
    _assert_exact(digest, retired.get("sorted_ids_compact_json_sha256"), "retired id digest")
    return frozenset(identifiers)


def load_binding(project_root: Path = PROJECT_ROOT) -> HeldoutBinding:
    root = project_root.resolve()
    prereg_path = root / PREREGISTRATION_PATH
    if (
        prereg_path.is_symlink()
        or not prereg_path.is_file()
        or common.sha256_file(prereg_path) != PREREGISTRATION_SHA256
    ):
        raise HeldoutPreparationError("held-out preregistration bytes drifted")
    prereg = _load_json(prereg_path, "held-out preregistration")
    _assert_exact(
        prereg.get("schema_version"),
        "p4.2a-v2-heldout-preregistration-v1",
        "preregistration schema",
    )
    _assert_exact(
        prereg.get("status"),
        "PREREGISTERED_BEFORE_SYNTHETIC_REHEARSAL_AND_ANY_HELDOUT_ARTIFACT",
        "preregistration status",
    )
    design_ref = _mapping(prereg.get("design"), "design")
    _assert_exact(design_ref.get("path"), DESIGN_PATH.as_posix(), "design path")
    _assert_exact(design_ref.get("sha256"), DESIGN_SHA256, "design SHA")
    design_path = _verify_file(root, design_ref, "evaluation design")
    design = load_event_evaluation_design(design_path, project_root=root)
    _assert_exact(
        design.document.get("schema_version"), "p4.2a-evaluation-design-v2", "design schema"
    )
    authorities = _mapping(prereg.get("authorities"), "authorities")
    for name in authorities:
        _verify_file(root, authorities[name], f"authority.{name}")
    _assert_exact(
        _mapping(authorities.get("selection_outcome"), "selection outcome").get("sha256"),
        SELECTION_OUTCOME_SHA256,
        "selection outcome SHA",
    )
    _assert_exact(
        _mapping(authorities.get("selected_contract_freeze"), "selected freeze").get("sha256"),
        SELECTED_FREEZE_SHA256,
        "selected freeze SHA",
    )
    selected = _mapping(prereg.get("selected_extractor"), "selected_extractor")
    _assert_exact(selected.get("model"), MODEL, "selected model")
    _assert_exact(
        selected.get("round_3_prompt"),
        {
            "path": ROUND3_PROMPT_PATH.as_posix(),
            "sha256": ROUND3_PROMPT_SHA256,
            "bytes_must_remain_unchanged": True,
        },
        "Round 3 prompt binding",
    )
    _assert_exact(
        selected.get("round_3_contract"),
        {
            "path": ROUND3_CONTRACT_PATH.as_posix(),
            "sha256": ROUND3_CONTRACT_SHA256,
            "inference_semantics_must_remain_unchanged": True,
        },
        "Round 3 contract binding",
    )
    _assert_exact(
        selected.get("heldout_execution_contract"),
        {"path": HELDOUT_CONTRACT_PATH.as_posix(), "sha256": HELDOUT_CONTRACT_SHA256},
        "held-out contract binding",
    )
    source = _mapping(prereg.get("source_frame"), "source_frame")
    _assert_exact(source.get("frame_id"), FRAME_ID, "frame id")
    _assert_exact(source.get("utc_start_inclusive"), WINDOW_START_UTC, "window start")
    _assert_exact(source.get("utc_end_exclusive"), WINDOW_END_UTC, "window end")
    _assert_exact(source.get("expected_raw_candidate_count"), EXPECTED_RAW_COUNT, "raw count")
    _assert_exact(
        source.get("expected_raw_candidates_by_source"), EXPECTED_BY_SOURCE, "source counts"
    )
    request = _mapping(prereg.get("request_contract"), "request_contract")
    _assert_exact(request.get("one_news_item_per_request"), True, "one item per request")
    _assert_exact(
        request.get("one_request_per_eligible_candidate"),
        True,
        "one request per eligible candidate",
    )
    _assert_exact(
        request.get("candidate_order"),
        "ascending_news_item_id_without_gaps_or_reordering",
        "candidate order",
    )
    _assert_exact(
        request.get("any_candidate_failure"),
        "terminal_inference_failed_no_sampling_no_retry",
        "failure policy",
    )
    safety = _mapping(prereg.get("safety"), "safety")
    required_safety = {
        "production_writes_allowed": False,
        "scheduler_changes_allowed": False,
        "p4_1_observation_window_mutation_allowed": False,
        "proposals_or_orders_allowed": False,
        "trading_mode": "research",
        "live_trading_enabled": False,
        "paper_trading_enabled": False,
        "paper_auto_trading_enabled": False,
        "futu_enable_trade": False,
        "futu_enable_account_mutation": False,
        "unlock_trade_permanently_blocked": True,
        "p4_2a_done": False,
        "p4_2b_unlocked": False,
        "p4_3_unlocked": False,
    }
    _assert_exact(safety, required_safety, "safety locks")
    artifacts = _artifact_map(root, prereg)
    design_artifacts = _mapping(design.document.get("artifacts"), "design.artifacts")
    design_names = {
        "synthetic_rehearsal_directory": "synthetic_rehearsal",
        "heldout_materialized_inputs_jsonl": "materialized_inputs",
        "heldout_materialization_manifest": "materialization_manifest",
        "heldout_inference_state_jsonl": "inference_state",
        "heldout_predictions_jsonl": "predictions",
        "heldout_predictions_manifest": "prediction_manifest",
        "heldout_private_selection_manifest": "private_selection",
        "heldout_owner_blind_jsonl": "owner_blind",
    }
    for design_name, prereg_name in design_names.items():
        item = _mapping(design_artifacts.get(design_name), f"design.artifacts.{design_name}")
        _assert_exact(
            _resolve(root, item.get("path"), design_name),
            artifacts[prereg_name],
            f"artifact {design_name}",
        )
    return HeldoutBinding(
        root=root,
        preregistration=prereg,
        design=design.document,
        contract=_load_selected_contract(root),
        artifacts=artifacts,
        retired_ids=_retired_ids(root, prereg),
    )


def _publish_create_only(payloads: Sequence[tuple[Path, bytes]]) -> None:
    paths = [path for path, _payload in payloads]
    if not paths or len(paths) != len(set(paths)):
        raise HeldoutPreparationError("create-only publication paths are invalid")
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to overwrite held-out artifact: {path}")
    staged: list[tuple[Path, Path]] = []
    created: list[Path] = []
    try:
        for target, payload in payloads:
            with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            staged.append((temporary, target))
        for temporary, target in staged:
            os.link(temporary, target)
            created.append(target)
        for directory in {target.parent for _temporary, target in staged}:
            _fsync_directory(directory)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
            _fsync_directory(path.parent)
        raise
    finally:
        for temporary, _target in staged:
            temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@lru_cache(maxsize=1)
def _project_synthetic_contract() -> EventExtractContract:
    return _load_selected_contract(PROJECT_ROOT)


def _recomputed_candidate_input_hashes(
    row: Mapping[str, Any],
    contract: EventExtractContract,
) -> tuple[str, str]:
    """Rebuild active and frozen-legacy identities with the production serializer."""

    try:
        active = gold_builder._input_sha256(row, contract)
        declared = gold_builder._declared_input_sha256(row, contract)
    except (gold_builder.GoldSampleError, EventExtractValidationError) as exc:
        raise HeldoutPreparationError("candidate input identity cannot be rederived") from exc
    if active == declared:
        raise HeldoutPreparationError("candidate input identities are not selector-distinct")
    return active, declared


def _validate_candidate_input_hashes(
    rows: Sequence[Mapping[str, Any]],
    contract: EventExtractContract,
) -> None:
    for index, row in enumerate(rows, 1):
        if set(row) != common.CANDIDATE_KEYS:
            raise HeldoutPreparationError(f"candidate row {index} schema drifted")
        original_text = row.get("original_text")
        source = row.get("source")
        symbol = row.get("ingested_symbol")
        available_time = row.get("available_time")
        published_at = row.get("published_at")
        if (
            row.get("schema_version") != "p4.2a-heldout-candidate-input-v1.1"
            or source not in EXPECTED_BY_SOURCE
            or row.get("design_sha256") != DESIGN_SHA256
            or row.get("contract_sha256") != HELDOUT_CONTRACT_SHA256
            or row.get("model") != MODEL
            or not isinstance(row.get("url"), str)
            or not row["url"]
            or not isinstance(row.get("title"), str)
            or not row["title"]
            or not isinstance(original_text, str)
            or not original_text
            or row.get("text_sha256")
            != common.sha256_bytes(original_text.encode("utf-8"))
            or (
                symbol is not None
                and (
                    not isinstance(symbol, str)
                    or len(symbol) != 6
                    or not symbol.isdigit()
                )
            )
        ):
            raise HeldoutPreparationError(f"candidate row {index} identity/schema drifted")
        for field in ("content_hash", "text_sha256", "input_sha256", "declared_input_sha256"):
            _require_sha256(row.get(field), f"candidate row {index} {field}")
        for field, value, nullable in (
            ("available_time", available_time, False),
            ("published_at", published_at, True),
        ):
            if nullable and value is None:
                continue
            if not isinstance(value, str) or not value:
                raise HeldoutPreparationError(
                    f"candidate row {index} {field} is not timezone-aware"
                )
            try:
                timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HeldoutPreparationError(
                    f"candidate row {index} {field} is not timezone-aware"
                ) from exc
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise HeldoutPreparationError(
                    f"candidate row {index} {field} is not timezone-aware"
                )
        try:
            gold_builder.validate_body_evidence(row, label=f"candidate row {index}")
        except gold_builder.GoldSampleError as exc:
            raise HeldoutPreparationError(
                f"candidate row {index} body evidence drifted"
            ) from exc
        evidence = _mapping(row.get("body_evidence"), f"candidate row {index} body evidence")
        if source == "cninfo":
            if (
                row.get("body_state") != "announcement_body"
                or evidence.get("required") is not True
                or evidence.get("source") != "cninfo_pdf"
                or evidence.get("url") != row.get("url")
                or evidence.get("pdf_persisted") is not False
                or not isinstance(evidence.get("pdf_sha256"), str)
                or not isinstance(evidence.get("full_text_sha256"), str)
            ):
                raise HeldoutPreparationError(
                    f"candidate row {index} lacks the frozen CNInfo announcement body"
                )
        elif source in {"akshare_ths", "sina_company_news"}:
            expected_body_state = (
                "title_digest_short" if source == "akshare_ths" else "title_only"
            )
            if row.get("body_state") != expected_body_state or dict(evidence) != {
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
                raise HeldoutPreparationError(
                    f"candidate row {index} non-CNInfo body discipline drifted"
                )
        else:
            raise HeldoutPreparationError(f"candidate row {index} source drifted")
        active, declared = _recomputed_candidate_input_hashes(row, contract)
        if row.get("input_sha256") != active or row.get("declared_input_sha256") != declared:
            raise HeldoutPreparationError(
                f"candidate row {index} input identities drifted from the frozen contract"
            )


def _synthetic_input(
    identifier: int,
    *,
    contract: EventExtractContract | None = None,
    source: str = "sina_company_news",
) -> JsonObject:
    if source not in EXPECTED_BY_SOURCE:
        raise HeldoutPreparationError("synthetic candidate source is outside the frozen frame")
    text = f"synthetic held-out item {identifier}"
    text_sha = common.sha256_bytes(text.encode())
    url = (
        f"https://static.cninfo.com.cn/synthetic/{identifier}.PDF"
        if source == "cninfo"
        else f"https://example.invalid/{identifier}"
    )
    if source == "cninfo":
        body_state = "announcement_body"
        body_evidence: JsonObject = {
            "required": True,
            "source": "cninfo_pdf",
            "url": url,
            "pdf_sha256": common.sha256_bytes(f"pdf-{identifier}".encode()),
            "full_text_sha256": text_sha,
            "full_text_character_count": len(text),
            "annotation_text_character_count": len(text),
            "body_characters_in_original_text": len(text),
            "text_truncated": False,
            "pdf_persisted": False,
        }
    else:
        body_state = "title_digest_short" if source == "akshare_ths" else "title_only"
        body_evidence = {
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
        }
    row: JsonObject = {
        "schema_version": "p4.2a-heldout-candidate-input-v1.1",
        "news_item_id": identifier,
        "source": source,
        "url": url,
        "title": text,
        "ingested_symbol": f"{identifier:06d}",
        "published_at": "2026-08-06T00:00:00Z",
        "available_time": "2026-08-06T00:01:00Z",
        "original_text": text,
        "body_state": body_state,
        "body_evidence": body_evidence,
        "content_hash": common.sha256_bytes(f"content-{identifier}".encode()),
        "text_sha256": text_sha,
        "contract_sha256": HELDOUT_CONTRACT_SHA256,
        "design_sha256": DESIGN_SHA256,
        "model": MODEL,
    }
    active, declared = _recomputed_candidate_input_hashes(
        row,
        contract or _project_synthetic_contract(),
    )
    row["input_sha256"] = active
    row["declared_input_sha256"] = declared
    return row


def _synthetic_prediction(
    row: Mapping[str, Any],
    *,
    materiality: int,
    recorded_at_utc: str = "2026-08-10T00:00:30Z",
) -> JsonObject:
    return {
        "schema_version": "p4.2a-offline-extract-row-v1",
        "recorded_at_utc": recorded_at_utc,
        "news_item_id": row["news_item_id"],
        "source": row["source"],
        "input_sha256": row["input_sha256"],
        "declared_input_sha256": row["declared_input_sha256"],
        "text_sha256": row["text_sha256"],
        "contract_sha256": HELDOUT_CONTRACT_SHA256,
        "model": MODEL,
        "latency_ms": 0,
        "llm_audit_latency_ms": 0,
        "tokens": {"prompt_tokens": 0, "completion_tokens": 0},
        "security": {
            "credentials_persisted": False,
            "exception_detail_persisted": False,
            "llm_audit_storage": "isolated_in_memory",
            "llm_audit_status": "recorded",
            "production_database_access": "sqlite_uri_mode_ro_query_only",
            "raw_prompt_persisted": False,
            "raw_transport_response_persisted": False,
            "redaction_status": "passed",
        },
        "status": "ok",
        "prediction": {
            "symbols": [row["ingested_symbol"]],
            "event_type": "other",
            "direction": 0,
            "materiality": materiality,
            "summary": "合成持出集事件。",
            "confidence": 1.0,
            "evidence_span": row["original_text"],
        },
    }


def _synthetic_production_materialization_fixture(
    binding: HeldoutBinding,
    *,
    execution_context: ExecutionContext = None,
) -> tuple[list[JsonObject], JsonObject]:
    """Build a legal production-shaped offline fixture for deep validator tests."""

    authorized = validate_v2_1_stage_authorization(
        binding,
        stage="materialize",
        execution_context=execution_context,
    )
    _ensure_synthetic_control_surface(binding)

    sources = [
        *(["akshare_ths"] * EXPECTED_BY_SOURCE["akshare_ths"]),
        *(["sina_company_news"] * EXPECTED_BY_SOURCE["sina_company_news"]),
        *(["cninfo"] * 80),
    ]
    candidates = [
        _synthetic_input(
            900_001 + offset,
            contract=binding.contract,
            source=source,
        )
        for offset, source in enumerate(sources)
    ]
    ineligible_count = EXPECTED_BY_SOURCE["cninfo"] - 80
    ineligible: list[JsonObject] = []
    ineligible_all: list[JsonObject] = []
    v17_document = _mapping(
        yaml.safe_load((PROJECT_ROOT / "config/p4_event_evaluation_v1_7.yaml").read_bytes()),
        "v1.7 synthetic fixture design",
    )
    eligibility = _mapping(
        v17_document.get("candidate_eligibility"),
        "v1.7 synthetic fixture eligibility",
    )
    minimum_characters = eligibility.get("minimum_extracted_characters")
    if (
        isinstance(minimum_characters, bool)
        or not isinstance(minimum_characters, int)
        or minimum_characters <= 0
    ):
        raise HeldoutPreparationError("synthetic fixture minimum character gate drifted")
    for offset in range(ineligible_count):
        identifier = 2_000_001 + offset
        url = f"https://static.cninfo.com.cn/synthetic/{identifier}.PDF"
        ineligible_all.append(
            {
                "news_item_id": identifier,
                "source": "cninfo",
                "url": url,
                "content_hash": common.sha256_bytes(
                    f"synthetic-ineligible-content-{identifier}".encode()
                ),
            }
        )
        ineligible.append(
            {
                "news_item_id": identifier,
                "url": url,
                "reason": "pdf_text_below_min_char_gate",
                "measured_value": 0,
                "gate_value": minimum_characters,
                "pdf_sha256": common.sha256_bytes(
                    f"synthetic-ineligible-pdf-{identifier}".encode()
                ),
            }
        )
    input_payload = common.canonical_jsonl_bytes(candidates)
    retired = _mapping(
        _mapping(
            binding.preregistration.get("eligibility_and_sampling"),
            "eligibility_and_sampling",
        ).get("retired_selection"),
        "retired_selection",
    )
    manifest: JsonObject = {
        "schema_version": MATERIALIZATION_MANIFEST_V2_SCHEMA,
        "frame_id": FRAME_ID,
        "lineage": {
            "preregistration": {
                "path": PREREGISTRATION_PATH.as_posix(),
                "sha256": PREREGISTRATION_SHA256,
            },
            "design": {"path": DESIGN_PATH.as_posix(), "sha256": DESIGN_SHA256},
            "contract": {
                "path": HELDOUT_CONTRACT_PATH.as_posix(),
                "sha256": HELDOUT_CONTRACT_SHA256,
                "model": MODEL,
            },
            "source_window": {
                "start_inclusive_utc": WINDOW_START_UTC,
                "end_exclusive_utc": WINDOW_END_UTC,
            },
            "source_lineage": _verified_source_lineage(binding),
            "retired_selection_sha256": retired["sha256"],
        },
        "artifacts": {
            "eligible_inputs_jsonl": {
                "path": _relative_artifact_path(binding, "materialized_inputs"),
                "sha256": common.sha256_bytes(input_payload),
                "create_only": True,
            },
            "manifest": {
                "path": _relative_artifact_path(binding, "materialization_manifest"),
                "create_only": True,
            },
        },
        "counts": {
            "raw_source_window": EXPECTED_RAW_COUNT,
            "retired_excluded_before_materialization": 0,
            "all_candidates_after_retirement": EXPECTED_RAW_COUNT,
            "eligible_candidates": len(candidates),
            "ineligible_candidates": len(ineligible),
            "ineligible_by_reason": {
                "pdf_text_below_min_char_gate": len(ineligible)
            },
        },
        "layers": {
            "all_candidates": [
                {
                    "news_item_id": row["news_item_id"],
                    "source": row["source"],
                    "url": row["url"],
                    "content_hash": row["content_hash"],
                }
                for row in candidates
            ]
            + ineligible_all,
            "eligible_candidates": [
                {
                    key: row[key]
                    for key in (
                        "news_item_id",
                        "source",
                        "input_sha256",
                        "declared_input_sha256",
                        "text_sha256",
                    )
                }
                for row in candidates
            ],
            "ineligible_candidates": ineligible,
        },
        "production_database": {"mode": "ro", "pragma_query_only": 1, "writes": 0},
        "execution_authority": _execution_authority_evidence(authorized),
        "request_pacing": {
            "cninfo_pdf": {
                "host": "static.cninfo.com.cn",
                "policy": "minimum_start_to_start",
                "configured_min_start_to_start_seconds": 1.0,
                "clock": "monotonic",
                "first_request_delayed": False,
                "request_start_count": EXPECTED_BY_SOURCE["cninfo"],
                "observed_gap_count": EXPECTED_BY_SOURCE["cninfo"] - 1,
                "minimum_observed_start_to_start_seconds": 1.0,
                "median_observed_start_to_start_seconds": 1.0,
                "violation_count": 0,
                "retry_count": 0,
            },
            "akshare_ths": "not_applicable_no_external_document_fetch",
            "sina_company_news": "not_applicable_no_external_document_fetch",
        },
        "runtime_start_preflight": _offline_runtime_start_preflight(),
    }
    return candidates, manifest


def _ensure_synthetic_control_surface(binding: HeldoutBinding) -> None:
    """Copy every byte-frozen control needed by a temporary full-chain fixture."""

    if binding.root.resolve() == PROJECT_ROOT.resolve():
        return
    shutil.copytree(
        PROJECT_ROOT / "config",
        binding.root / "config",
        dirs_exist_ok=True,
    )

    def copy_bound_files(value: object) -> None:
        if isinstance(value, Mapping):
            path_value = value.get("path")
            digest = value.get("sha256")
            if isinstance(path_value, str) and isinstance(digest, str):
                source = (PROJECT_ROOT / path_value).resolve()
                if source.is_file() and source.is_relative_to(PROJECT_ROOT):
                    target = binding.root / path_value
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
            for nested in value.values():
                copy_bound_files(nested)
        elif isinstance(value, list):
            for nested in value:
                copy_bound_files(nested)

    copy_bound_files(binding.preregistration)
    copy_bound_files(binding.design)


def _write_synthetic_production_execution_fixture(
    binding: HeldoutBinding,
    *,
    execution_context: ExecutionContext = None,
    started_at_utc: str = "2026-08-10T04:00:00Z",
    recorded_at_utc: str = "2026-08-10T04:00:30Z",
    completed_at_utc: str = "2026-08-10T04:01:00Z",
    failed_status: bool = False,
    terminal_predictions_sha256: str | None = None,
) -> tuple[list[JsonObject], list[JsonObject], str]:
    """Write one production-shaped execution fixture outside the real repository."""

    if binding.root.resolve() == PROJECT_ROOT.resolve():
        raise HeldoutPreparationError(
            "synthetic execution fixture cannot target production artifacts"
        )
    candidates, materialization = _synthetic_production_materialization_fixture(
        binding,
        execution_context=execution_context,
    )
    predictions = [
        _synthetic_prediction(
            row,
            materiality=2 if index <= 50 else 1,
            recorded_at_utc=recorded_at_utc,
        )
        for index, row in enumerate(candidates, 1)
    ]
    if failed_status:
        predictions[0] = {**predictions[0], "status": "extract_failed", "prediction": None}
    for name in (
        "materialized_inputs",
        "materialization_manifest",
        "inference_state",
        "predictions",
        "prediction_manifest",
    ):
        binding.artifacts[name].parent.mkdir(parents=True, exist_ok=True)
    input_payload = common.canonical_jsonl_bytes(candidates)
    binding.artifacts["materialized_inputs"].write_bytes(input_payload)
    binding.artifacts["materialization_manifest"].write_bytes(
        common.canonical_json_bytes(materialization)
    )
    materialization_sha = common.sha256_file(binding.artifacts["materialization_manifest"])
    prediction_payload = common.canonical_jsonl_bytes(predictions)
    binding.artifacts["predictions"].write_bytes(prediction_payload)
    prediction_sha = common.sha256_bytes(prediction_payload)
    execution_id = "67ce8592-82af-41c2-80d4-8d1b0f21bd35"
    snapshot: JsonObject = {
        "sqlite_uri_mode": "ro",
        "pragma_query_only": 1,
        "connection_total_changes": 0,
        "llm_call_count": 0,
        "llm_call_max_id": None,
        "trade_proposal_count": 0,
        "broker_order_count": 0,
        "non_simulate_order_count": 0,
        "news_events_table_exists": False,
        "universe_symbol_count": len(candidates),
    }
    prediction_manifest: JsonObject = {
        "schema_version": "p4.2a-v2-heldout-prediction-manifest-v1",
        "frame_id": FRAME_ID,
        "execution_id": execution_id,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "materialization_manifest_sha256": materialization_sha,
        "contract_sha256": HELDOUT_CONTRACT_SHA256,
        "model": MODEL,
        "candidate_count": len(candidates),
        "prediction_count": len(predictions),
        "status_ok_count": len(predictions),
        "status_failed_count": 0,
        "one_news_item_per_request": True,
        "one_request_per_eligible_candidate": True,
        "automatic_retries": 0,
        "failed_candidate_retries": 0,
        "production_snapshot_unchanged": True,
        "production_snapshot_before": snapshot,
        "production_snapshot_after": snapshot,
        "settings_safety": _INFERENCE_SETTINGS_SAFETY,
        "predictions": {
            "path": _relative_artifact_path(binding, "predictions"),
            "sha256": prediction_sha,
        },
    }
    binding.artifacts["prediction_manifest"].write_bytes(
        common.canonical_json_bytes(prediction_manifest)
    )
    prediction_manifest_sha = common.sha256_file(binding.artifacts["prediction_manifest"])
    states: list[JsonObject] = [
        {
            "schema_version": "p4.2a-v2-heldout-inference-state-v1",
            "status": "inference_started",
            "execution_id": execution_id,
            "started_at_utc": started_at_utc,
            "eligible_candidate_count": len(candidates),
            "candidate_order": "ascending_news_item_id",
            "preregistration_sha256": PREREGISTRATION_SHA256,
            "materialization_manifest_sha256": materialization_sha,
            "contract_sha256": HELDOUT_CONTRACT_SHA256,
            "model": MODEL,
            "automatic_retries": 0,
            "failed_candidate_retries": 0,
            "settings_safety": _INFERENCE_SETTINGS_SAFETY,
            "production_snapshot_before": snapshot,
        },
        {
            "schema_version": "p4.2a-v2-heldout-inference-state-v1",
            "status": "completed_all_eligible_candidates_once",
            "execution_id": execution_id,
            "preregistration_sha256": PREREGISTRATION_SHA256,
            "materialization_manifest_sha256": materialization_sha,
            "completed_at_utc": completed_at_utc,
            "prediction_count": len(predictions),
            "predictions_sha256": terminal_predictions_sha256 or prediction_sha,
            "prediction_manifest_sha256": prediction_manifest_sha,
            "production_snapshot_unchanged": True,
            "production_snapshot_before": snapshot,
            "production_snapshot_after": snapshot,
        },
    ]
    binding.artifacts["inference_state"].write_bytes(common.canonical_jsonl_bytes(states))
    return candidates, predictions, execution_id


def _positive_news_item_id(row: Mapping[str, Any], label: str) -> int:
    identifier = row.get("news_item_id")
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0:
        raise HeldoutPreparationError(f"{label} news_item_id is invalid")
    return identifier


def select_and_blind(
    binding: HeldoutBinding,
    candidates: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    *,
    execution_binding: SelectionExecutionBinding | None = None,
    execution_context: ExecutionContext = None,
) -> SelectionResult:
    candidate_rows = [
        _mapping(row, f"candidate row {index}") for index, row in enumerate(candidates, start=1)
    ]
    candidate_ids = [
        _positive_news_item_id(row, f"candidate row {index}")
        for index, row in enumerate(candidate_rows, start=1)
    ]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise HeldoutPreparationError("candidate ids are not unique")
    inputs = dict(zip(candidate_ids, candidate_rows, strict=True))

    prediction_rows = [
        _mapping(row, f"prediction row {index}")
        for index, row in enumerate(predictions, start=1)
    ]
    prediction_ids = [
        _positive_news_item_id(row, f"prediction row {index}")
        for index, row in enumerate(prediction_rows, start=1)
    ]
    if len(prediction_ids) != len(set(prediction_ids)):
        raise HeldoutPreparationError("prediction ids are not unique")
    if set(prediction_ids) != set(candidate_ids):
        raise HeldoutPreparationError(
            "prediction ids do not exactly match eligible candidate ids"
        )
    if execution_binding is None and all(
        binding.artifacts[name].is_file() and not binding.artifacts[name].is_symlink()
        for name in (
            "materialized_inputs",
            "materialization_manifest",
            "inference_state",
            "predictions",
            "prediction_manifest",
        )
    ):
        execution_binding = _selection_execution_binding_from_artifacts(
            binding,
            candidate_rows,
            prediction_rows,
            validated_stage="select-blind",
            execution_context=execution_context,
        )

    joined: list[tuple[JsonObject, JsonObject, str]] = []
    failures = 0
    for identifier, prediction in zip(prediction_ids, prediction_rows, strict=True):
        candidate = inputs[identifier]
        for field in common.JOIN_FIELDS:
            if prediction.get(field) != candidate.get(field):
                raise HeldoutPreparationError(f"prediction join field drifted: {field}")
        if prediction.get("status") != "ok":
            failures += 1
            continue
        result = _mapping(prediction.get("prediction"), "prediction")
        materiality = result.get("materiality")
        if (
            isinstance(materiality, bool)
            or not isinstance(materiality, int)
            or materiality not in (0, 1, 2, 3)
        ):
            raise HeldoutPreparationError("prediction materiality is invalid")
        stratum = "predicted_positive" if materiality >= 2 else "predicted_negative"
        joined.append((candidate, prediction, stratum))
    if len(joined) + failures != len(inputs):
        raise HeldoutPreparationError("full eligible pool was not inferred exactly once")
    if failures != 0:
        raise HeldoutPreparationError(
            "full eligible inference contains failures; sampling is forbidden"
        )
    by_stratum: dict[str, list[tuple[JsonObject, JsonObject, str]]] = {
        "predicted_positive": [],
        "predicted_negative": [],
    }
    for item in joined:
        by_stratum[item[2]].append(item)
    selected: list[tuple[JsonObject, JsonObject, str, str]] = []
    for stratum, count in (
        ("predicted_positive", POSITIVE_SELECTED),
        ("predicted_negative", NEGATIVE_SELECTED),
    ):
        ranked = sorted(
            by_stratum[stratum],
            key=lambda item: common.selection_rank(
                seed=SEED,
                sampling_stratum=stratum,
                news_item_id=int(item[0]["news_item_id"]),
                input_sha256=str(item[0]["input_sha256"]),
            ),
        )
        if len(ranked) < count:
            raise HeldoutPreparationError(f"insufficient {stratum} stratum")
        for candidate, prediction, actual_stratum in ranked[:count]:
            rank = common.selection_rank(
                seed=SEED,
                sampling_stratum=actual_stratum,
                news_item_id=int(candidate["news_item_id"]),
                input_sha256=str(candidate["input_sha256"]),
            )
            selected.append((candidate, prediction, actual_stratum, rank))
    selected.sort(
        key=lambda item: common.owner_order_rank(
            design_sha256=DESIGN_SHA256,
            news_item_id=int(item[0]["news_item_id"]),
            input_sha256=str(item[0]["input_sha256"]),
        )
    )
    selection_rows: list[JsonObject] = []
    blind_rows: list[JsonObject] = []
    selected_prediction_bindings: list[JsonObject] = []
    for index, (candidate, prediction, stratum, rank) in enumerate(selected, start=1):
        owner_rank = common.owner_order_rank(
            design_sha256=DESIGN_SHA256,
            news_item_id=int(candidate["news_item_id"]),
            input_sha256=str(candidate["input_sha256"]),
        )
        selection_rows.append(
            {
                "sample_index": index,
                "news_item_id": candidate["news_item_id"],
                "source": candidate["source"],
                "input_sha256": candidate["input_sha256"],
                "declared_input_sha256": candidate["declared_input_sha256"],
                "text_sha256": candidate["text_sha256"],
                "contract_sha256": candidate["contract_sha256"],
                "model": candidate["model"],
                "sampling_stratum": stratum,
                "selection_rank_sha256": rank,
                "owner_order_sha256": owner_rank,
            }
        )
        selected_prediction_bindings.append(
            {
                "sample_index": index,
                "news_item_id": candidate["news_item_id"],
                "prediction_row_sha256": common.sha256_bytes(
                    common.canonical_json_bytes(prediction)
                ),
            }
        )
        blind = {
            "schema_version": "p4.2a-v2-heldout-owner-blind-item-v1",
            "design": {"path": DESIGN_PATH.as_posix(), "sha256": DESIGN_SHA256},
            "frame_id": FRAME_ID,
            "sample_index": index,
            "news_item_id": candidate["news_item_id"],
            "source": candidate["source"],
            "url": candidate["url"],
            "title": candidate["title"],
            "ingested_symbol": candidate["ingested_symbol"],
            "published_at": candidate["published_at"],
            "available_time": candidate["available_time"],
            "original_text": candidate["original_text"],
            "input_sha256": candidate["input_sha256"],
            "text_sha256": candidate["text_sha256"],
            "body_state": candidate["body_state"],
            "body_evidence": copy.deepcopy(candidate["body_evidence"]),
            "gold": {},
        }
        common.validate_blind_row(blind)
        blind_rows.append(blind)
    blind_payload = common.canonical_jsonl_bytes(blind_rows)
    materialized_inputs_sha256 = common.sha256_bytes(
        common.canonical_jsonl_bytes(candidate_rows)
    )
    predictions_sha256 = common.sha256_bytes(common.canonical_jsonl_bytes(prediction_rows))
    source_lineage: JsonObject = {
        "binding_scope": (
            "registered_full_execution"
            if execution_binding is not None
            else "payload_only_synthetic_or_unit_helper"
        ),
        "preregistration": {
            "path": PREREGISTRATION_PATH.as_posix(),
            "sha256": PREREGISTRATION_SHA256,
        },
        "design": {"path": DESIGN_PATH.as_posix(), "sha256": DESIGN_SHA256},
        "materialized_inputs": {
            "path": binding.artifacts["materialized_inputs"]
            .relative_to(binding.root)
            .as_posix(),
            "sha256": materialized_inputs_sha256,
            "row_count": len(candidate_rows),
        },
        "predictions": {
            "path": binding.artifacts["predictions"].relative_to(binding.root).as_posix(),
            "sha256": predictions_sha256,
            "row_count": len(prediction_rows),
        },
        "heldout_execution_contract": {
            "path": HELDOUT_CONTRACT_PATH.as_posix(),
            "sha256": HELDOUT_CONTRACT_SHA256,
            "model": MODEL,
        },
        "selected_predictions": {
            "binding": "sha256_of_canonical_complete_prediction_row",
            "count": len(selected_prediction_bindings),
            "bindings_sha256": common.sha256_bytes(
                common.canonical_json_bytes(selected_prediction_bindings)
            ),
            "bindings": selected_prediction_bindings,
        },
    }
    if execution_binding is not None:
        for label, digest in (
            (
                "materialization manifest SHA",
                execution_binding.materialization_manifest_sha256,
            ),
            ("inference state SHA", execution_binding.inference_state_sha256),
            ("prediction manifest SHA", execution_binding.prediction_manifest_sha256),
        ):
            _require_sha256(digest, label)
        if (
            not execution_binding.execution_id
            or execution_binding.eligible_candidate_count != len(candidate_rows)
            or execution_binding.prediction_count != len(prediction_rows)
            or execution_binding.status_ok_count != len(prediction_rows)
            or execution_binding.status_failed_count != 0
        ):
            raise HeldoutPreparationError("selection execution binding counts drifted")
        source_lineage.update(
            {
                "materialization_manifest": {
                    "path": binding.artifacts["materialization_manifest"]
                    .relative_to(binding.root)
                    .as_posix(),
                    "sha256": execution_binding.materialization_manifest_sha256,
                },
                "inference_state": {
                    "path": binding.artifacts["inference_state"]
                    .relative_to(binding.root)
                    .as_posix(),
                    "sha256": execution_binding.inference_state_sha256,
                    "event_count": 2,
                },
                "prediction_manifest": {
                    "path": binding.artifacts["prediction_manifest"]
                    .relative_to(binding.root)
                    .as_posix(),
                    "sha256": execution_binding.prediction_manifest_sha256,
                },
                "execution": {
                    "execution_id": execution_binding.execution_id,
                    "eligible_candidate_count": execution_binding.eligible_candidate_count,
                    "prediction_count": execution_binding.prediction_count,
                    "status_ok_count": execution_binding.status_ok_count,
                    "status_failed_count": execution_binding.status_failed_count,
                    "automatic_retries": 0,
                    "failed_candidate_retries": 0,
                    "terminal_status": "completed_all_eligible_candidates_once",
                },
            }
        )
    manifest: JsonObject = {
        "schema_version": "p4.2a-v2-heldout-selection-manifest-v1",
        "frame_id": FRAME_ID,
        "design": {"path": DESIGN_PATH.as_posix(), "sha256": DESIGN_SHA256},
        "source_lineage": source_lineage,
        "selection": {
            "algorithm": "sha256_rank_without_replacement_per_stratum_v1",
            "seed": SEED,
            "without_replacement": True,
            "selected_counts": {
                "predicted_positive": POSITIVE_SELECTED,
                "predicted_negative": NEGATIVE_SELECTED,
                "extract_failed": 0,
                "total": POSITIVE_SELECTED + NEGATIVE_SELECTED,
            },
            "selected": selection_rows,
        },
        "audit": {
            "eligible_candidate_count": len(candidates),
            "successful_prediction_count": len(joined),
            "extract_failed_count": failures,
            "available_by_stratum": {key: len(value) for key, value in by_stratum.items()},
            "retired_selected_intersection_count": len(
                {int(row["news_item_id"]) for row in selection_rows} & binding.retired_ids
            ),
            "input_prediction_identity_match": True,
        },
        "owner_delivery": {
            "path": binding.artifacts["owner_blind"].relative_to(binding.root).as_posix(),
            "sha256": common.sha256_bytes(blind_payload),
            "row_count": 60,
            "prediction_visible": False,
            "sampling_stratum_visible": False,
            "selection_rank_visible": False,
            "gold_state": "empty_object_pending_ai_draft_and_human_adjudication",
        },
        "production_writes": False,
    }
    if manifest["audit"]["retired_selected_intersection_count"] != 0:
        raise HeldoutPreparationError("retired held-out id was selected")
    return SelectionResult(manifest=manifest, blind_rows=tuple(blind_rows))


def run_synthetic_rehearsal(binding: HeldoutBinding) -> Path:
    # This helper intentionally does not occupy the four registered full-path
    # rehearsal artifacts.  The integrated draft/UI/evaluator rehearsal owns
    # those create-only paths and is the only component allowed to unlock
    # materialization.
    directory = binding.artifacts["synthetic_rehearsal"] / "preparation-helper"
    inputs = [_synthetic_input(identifier) for identifier in range(1, 81)]
    predictions = [
        _synthetic_prediction(row, materiality=2 if index <= 50 else 1)
        for index, row in enumerate(inputs, start=1)
    ]
    result = select_and_blind(binding, inputs, predictions)
    contract = {
        "schema_version": "p4.2a-v2-heldout-synthetic-rehearsal-contract-v1",
        "preregistration": {
            "path": PREREGISTRATION_PATH.as_posix(),
            "sha256": PREREGISTRATION_SHA256,
        },
        "real_database_read": False,
        "real_model_calls": 0,
        "selection_counts": {"predicted_positive": 40, "predicted_negative": 20},
    }
    expected = {
        "schema_version": "p4.2a-v2-heldout-synthetic-rehearsal-expected-v1",
        "selected_count": 60,
        "blind_rows": 60,
        "forbidden_blind_paths": 0,
        "retired_intersection": 0,
    }
    payloads = {
        directory / "contract.json": common.canonical_json_bytes(contract),
        directory / "inputs.jsonl": common.canonical_jsonl_bytes(inputs),
        directory / "expected.json": common.canonical_json_bytes(expected),
    }
    receipt = {
        "schema_version": "p4.2a-v2-heldout-preparation-helper-receipt-v1",
        "status": "preparation_helper_passed_not_full_path",
        "full_path_covered": False,
        "materialization_gate_unlock": False,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "design_sha256": DESIGN_SHA256,
        "heldout_contract_sha256": HELDOUT_CONTRACT_SHA256,
        "inputs_sha256": common.sha256_bytes(payloads[directory / "inputs.jsonl"]),
        "selection_manifest_sha256": common.sha256_bytes(
            common.canonical_json_bytes(result.manifest)
        ),
        "blind_sha256": common.sha256_bytes(common.canonical_jsonl_bytes(result.blind_rows)),
        "real_database_read": False,
        "real_model_calls": 0,
        "production_writes": False,
    }
    payloads[directory / "preparation-helper-receipt.json"] = common.canonical_json_bytes(receipt)
    _publish_create_only(tuple(payloads.items()))
    return directory / "preparation-helper-receipt.json"


def _materialization_design(binding: HeldoutBinding) -> gold_builder.FrozenEvaluationDesign:
    v17_path = binding.root / "config/p4_event_evaluation_v1_7.yaml"
    if binding.root == PROJECT_ROOT:
        v17_design = gold_builder.load_evaluation_design(v17_path)
    else:
        isolated_design = load_event_evaluation_design(
            v17_path,
            project_root=binding.root,
        )
        v17_design = gold_builder.FrozenEvaluationDesign(
            path=isolated_design.path,
            sha256=isolated_design.sha256,
            document=copy.deepcopy(isolated_design.document),
            base_contract=gold_builder.FrozenContract(
                path=isolated_design.base_contract.path,
                sha256=isolated_design.base_contract.sha256,
                document=copy.deepcopy(isolated_design.base_contract.document),
            ),
            ancestor_designs=isolated_design.ancestor_designs,
        )
    document = copy.deepcopy(binding.design)
    document["candidate_eligibility"] = copy.deepcopy(
        v17_design.document.get("candidate_eligibility")
    )
    return gold_builder.FrozenEvaluationDesign(
        path=binding.root / DESIGN_PATH,
        sha256=DESIGN_SHA256,
        document=document,
        base_contract=v17_design.base_contract,
    )


def _window_rows(binding: HeldoutBinding, database: Path) -> list[gold_builder.NewsRow]:
    query = """
    SELECT id, source, symbol, title, url, published_at, available_time, content_hash, raw_payload
    FROM news_items
    WHERE available_time >= ? AND available_time < ?
      AND source IN ('cninfo', 'akshare_ths', 'sina_company_news')
    ORDER BY id
    """
    with gold_builder.open_read_only_database(database) as connection:
        raw = connection.execute(
            query,
            (SQLITE_WINDOW_START_UTC, SQLITE_WINDOW_END_UTC),
        ).fetchall()
        if connection.total_changes != 0:
            raise HeldoutPreparationError("production database was modified")
    rows = [gold_builder._news_row(row) for row in raw]
    if len(rows) != EXPECTED_RAW_COUNT:
        raise HeldoutPreparationError(f"raw candidate count drifted: {len(rows)}")
    counts = dict(sorted(Counter(row.source for row in rows).items()))
    if counts != EXPECTED_BY_SOURCE:
        raise HeldoutPreparationError(f"raw source counts drifted: {counts}")
    if [row.news_item_id for row in rows] != sorted(row.news_item_id for row in rows):
        raise HeldoutPreparationError("raw candidates are not ordered by id")
    return [row for row in rows if row.news_item_id not in binding.retired_ids]


def _reject_retired_rehearsal_v1(binding: HeldoutBinding) -> None:
    incident_path = binding.root / REHEARSAL_V1_INCIDENT_PATH
    incident_reference = (
        f"incident={REHEARSAL_V1_INCIDENT_PATH.as_posix()} "
        f"incident_sha256={REHEARSAL_V1_INCIDENT_SHA256}"
    )
    if (
        incident_path.is_symlink()
        or not incident_path.is_file()
        or common.sha256_file(incident_path) != REHEARSAL_V1_INCIDENT_SHA256
    ):
        raise HeldoutPreparationError(
            "rehearsal v1 is permanently retired and its incident binding is unavailable "
            f"or drifted; {incident_reference}"
        )
    raise HeldoutPreparationError(
        "rehearsal v1 is permanently retired and cannot unlock materialization; "
        f"{incident_reference}"
    )


def _validate_full_path_rehearsal_gate(binding: HeldoutBinding) -> JsonObject:
    # The v1 receipt is immutable historical evidence, but B1/B2 make it
    # permanently ineligible to unlock a real database or network boundary.
    # Keep its former validator below for forensic readability only; this
    # unconditional incident-bound guard must remain first until a separately
    # preregistered replacement schema is implemented.
    _reject_retired_rehearsal_v1(binding)
    directory = binding.artifacts["synthetic_rehearsal"]
    receipt_path = directory / "pass-receipt.json"
    expected_names = FULL_REHEARSAL_PUBLISHED_ARTIFACTS | {receipt_path.name}
    if directory.is_symlink() or not directory.is_dir():
        raise HeldoutPreparationError("full-path synthetic rehearsal directory is unavailable")
    children = list(directory.iterdir())
    if (
        {path.name for path in children} != expected_names
        or any(path.is_symlink() or not path.is_file() for path in children)
    ):
        raise HeldoutPreparationError(
            "full-path synthetic rehearsal artifact registry drifted"
        )

    contract_path = directory / "contract.json"
    inputs_path = directory / "inputs.jsonl"
    expected_path = directory / "expected.json"
    receipt = _load_json(receipt_path, "synthetic rehearsal receipt")
    contract = _load_json(contract_path, "synthetic rehearsal contract")
    inputs = _load_jsonl(inputs_path, "synthetic rehearsal inputs")
    expected = _load_json(expected_path, "synthetic rehearsal expected result")

    receipt_fields = {
        "schema_version",
        "status",
        "full_path_covered",
        "materialization_gate_unlock",
        "preregistration_sha256",
        "design_sha256",
        "heldout_contract_sha256",
        "published_artifact_sha256",
        "tested_code_sha256",
        "internal_artifact_sha256",
        "materialized_candidate_count",
        "inference_candidate_count",
        "one_item_model_call_count",
        "mock_model_calls",
        "selection_counts",
        "owner_chain_count",
        "formal_state_events",
        "synthetic_report_status",
        "production_writes",
        "production_heldout_artifacts_changed",
        "real_database_reads",
        "real_network_calls",
        "real_model_calls",
        "real_heldout_metrics_computed",
        "real_metrics_disclosed",
        "temporary_workspace_removed",
    }
    contract_fields = {
        "schema_version",
        "preregistration_sha256",
        "design_sha256",
        "heldout_contract_sha256",
        "fixture",
        "request_contract",
        "selection_counts",
        "workspace_policy",
        "network_allowed",
        "production_database_allowed",
        "production_heldout_artifact_writes_allowed",
        "real_model_calls_allowed",
        "real_heldout_metrics_allowed",
        "tested_code_sha256",
    }
    expected_fields = {
        "schema_version",
        "materialized_candidate_count",
        "inference_candidate_count",
        "mock_model_call_count",
        "one_item_model_call_count",
        "selection_counts",
        "blind_row_count",
        "draft_row_count",
        "owner_chain_count",
        "formal_state_events",
        "synthetic_report_status",
        "real_heldout_metrics_computed",
        "production_writes",
        "real_database_reads",
        "real_network_calls",
        "real_model_calls",
    }
    if (
        set(receipt) != receipt_fields
        or set(contract) != contract_fields
        or set(expected) != expected_fields
    ):
        raise HeldoutPreparationError("full-path synthetic rehearsal schema drifted")

    published_hashes = _mapping(
        receipt.get("published_artifact_sha256"), "rehearsal published hashes"
    )
    if set(published_hashes) != FULL_REHEARSAL_PUBLISHED_ARTIFACTS:
        raise HeldoutPreparationError("rehearsal published artifact registry drifted")
    for name in FULL_REHEARSAL_PUBLISHED_ARTIFACTS:
        digest = _require_sha256(published_hashes.get(name), f"rehearsal {name} SHA")
        if common.sha256_file(directory / name) != digest:
            raise HeldoutPreparationError(f"rehearsal {name} bytes drifted")

    tested_code = _mapping(receipt.get("tested_code_sha256"), "rehearsal tested code")
    contract_tested_code = _mapping(
        contract.get("tested_code_sha256"), "rehearsal contract tested code"
    )
    if (
        set(tested_code) != FULL_REHEARSAL_TESTED_CODE_PATHS
        or contract_tested_code != tested_code
    ):
        raise HeldoutPreparationError("rehearsal tested-code registry drifted")
    for relative, raw_digest in tested_code.items():
        digest = _require_sha256(raw_digest, f"tested code {relative} SHA")
        path = _resolve(binding.root, relative, f"tested code {relative}")
        if path.is_symlink() or not path.is_file() or common.sha256_file(path) != digest:
            raise HeldoutPreparationError(f"tested code bytes drifted: {relative}")

    internal_hashes = _mapping(
        receipt.get("internal_artifact_sha256"), "rehearsal internal hashes"
    )
    expected_internal_names = {
        "materialized_inputs",
        "materialization_manifest",
        "inference_state",
        "predictions",
        "prediction_manifest",
        "private_selection",
        "owner_blind",
        "ai_draft",
        "adjudication_ui",
        "owner_export",
        "human_adjudicated",
        "owner_completion",
        "evaluation_state",
        "synthetic_report",
    }
    if set(internal_hashes) != expected_internal_names:
        raise HeldoutPreparationError("rehearsal internal artifact registry drifted")
    for name, digest in internal_hashes.items():
        _require_sha256(digest, f"rehearsal internal artifact {name} SHA")

    selection_counts = {
        "predicted_positive": POSITIVE_SELECTED,
        "predicted_negative": NEGATIVE_SELECTED,
        "total": POSITIVE_SELECTED + NEGATIVE_SELECTED,
    }
    expected_result = {
        "schema_version": FULL_REHEARSAL_EXPECTED_SCHEMA,
        "materialized_candidate_count": 80,
        "inference_candidate_count": 80,
        "mock_model_call_count": 80,
        "one_item_model_call_count": 80,
        "selection_counts": selection_counts,
        "blind_row_count": 60,
        "draft_row_count": 60,
        "owner_chain_count": 60,
        "formal_state_events": ["evaluation_started", "evaluation_completed"],
        "synthetic_report_status": "synthetic_rehearsal",
        "real_heldout_metrics_computed": False,
        "production_writes": False,
        "real_database_reads": 0,
        "real_network_calls": 0,
        "real_model_calls": 0,
    }
    if expected != expected_result:
        raise HeldoutPreparationError("synthetic rehearsal expected-result contract drifted")
    if contract != {
        "schema_version": FULL_REHEARSAL_CONTRACT_SCHEMA,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "design_sha256": DESIGN_SHA256,
        "heldout_contract_sha256": HELDOUT_CONTRACT_SHA256,
        "fixture": {
            "synthetic_candidate_count": 80,
            "predicted_positive_pool_count": 50,
            "predicted_negative_pool_count": 30,
        },
        "request_contract": {
            "one_news_item_per_request": True,
            "one_request_per_eligible_candidate": True,
            "automatic_retries": 0,
        },
        "selection_counts": selection_counts,
        "workspace_policy": "temporary_and_outside_registered_artifact_roots",
        "network_allowed": False,
        "production_database_allowed": False,
        "production_heldout_artifact_writes_allowed": False,
        "real_model_calls_allowed": 0,
        "real_heldout_metrics_allowed": False,
        "tested_code_sha256": tested_code,
    }:
        raise HeldoutPreparationError("synthetic rehearsal registered contract drifted")

    input_ids = [
        _positive_news_item_id(row, f"rehearsal input row {index}")
        for index, row in enumerate(inputs, start=1)
    ]
    if (
        len(inputs) != 80
        or input_ids != sorted(input_ids)
        or len(input_ids) != len(set(input_ids))
        or any(
            row.get("schema_version") != "p4.2a-heldout-candidate-input-v1.1"
            or row.get("contract_sha256") != HELDOUT_CONTRACT_SHA256
            or row.get("model") != MODEL
            for row in inputs
        )
    ):
        raise HeldoutPreparationError("synthetic rehearsal inputs drifted")

    receipt_expected = {
        "schema_version": FULL_REHEARSAL_RECEIPT_SCHEMA,
        "status": "passed",
        "full_path_covered": True,
        "materialization_gate_unlock": True,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "design_sha256": DESIGN_SHA256,
        "heldout_contract_sha256": HELDOUT_CONTRACT_SHA256,
        "published_artifact_sha256": published_hashes,
        "tested_code_sha256": tested_code,
        "internal_artifact_sha256": internal_hashes,
        "materialized_candidate_count": 80,
        "inference_candidate_count": 80,
        "one_item_model_call_count": 80,
        "mock_model_calls": 80,
        "selection_counts": selection_counts,
        "owner_chain_count": 60,
        "formal_state_events": ["evaluation_started", "evaluation_completed"],
        "synthetic_report_status": "synthetic_rehearsal",
        "production_writes": False,
        "production_heldout_artifacts_changed": False,
        "real_database_reads": 0,
        "real_network_calls": 0,
        "real_model_calls": 0,
        "real_heldout_metrics_computed": False,
        "real_metrics_disclosed": False,
        "temporary_workspace_removed": True,
    }
    if receipt != receipt_expected:
        raise HeldoutPreparationError("full-path synthetic rehearsal receipt drifted")
    return receipt


def run_materialize(
    binding: HeldoutBinding,
    *,
    operator_timing_attestation: OperatorTimingAttestation | None,
    database: Path | None = None,
    pdf_fetcher: gold_builder.PdfFetcher = gold_builder.download_cninfo_pdf,
    pdf_text_extractor: gold_builder.PdfTextExtractor = gold_builder.extract_cninfo_pdf_text,
    execution_context: ExecutionContext = None,
    monotonic: MonotonicClock = time.monotonic,
    sleep: Sleeper = time.sleep,
) -> tuple[Path, Path]:
    authorized = validate_v2_1_stage_authorization(
        binding,
        stage="materialize",
        execution_context=execution_context,
    )
    canonical_database = (binding.root / "data/alphapilot.db").resolve()
    if isinstance(authorized, _OfflineRehearsalCapability):
        if operator_timing_attestation is not None:
            raise HeldoutPreparationError(
                "offline rehearsal cannot carry a real operator timing attestation"
            )
        if (
            database is None
            or database.resolve() != authorized.database
            or pdf_fetcher is not authorized.pdf_fetcher
            or pdf_text_extractor is not authorized.pdf_text_extractor
            or monotonic is not authorized.monotonic
            or sleep is not authorized.sleep
        ):
            raise HeldoutPreparationError(
                "offline materialization boundaries differ from the minted capability"
            )
        db = authorized.database
        runtime_start_preflight = _offline_runtime_start_preflight()
    else:
        if (
            (database is not None and database.resolve() != canonical_database)
            or pdf_fetcher is not gold_builder.download_cninfo_pdf
            or pdf_text_extractor is not gold_builder.extract_cninfo_pdf_text
            or monotonic is not time.monotonic
            or sleep is not time.sleep
        ):
            raise HeldoutPreparationError(
                "real materialization rejects custom database, fetcher, clock, or sleeper"
            )
        db = canonical_database
        runtime_start_preflight = _real_runtime_start_preflight(
            binding, operator_timing_attestation
        )
    for key in ("materialized_inputs", "materialization_manifest"):
        target = binding.artifacts[key]
        if target.exists() or target.is_symlink():
            raise FileExistsError(
                f"refusing to reuse held-out materialization artifact: {target}"
            )
    rows = _window_rows(binding, db)
    if {row.news_item_id for row in rows} & binding.retired_ids:
        raise HeldoutPreparationError("retired ids were not excluded before materialization")
    pacer = _CninfoStartPacer(monotonic, sleep)
    paced_fetcher, checked_extractor = _paced_pdf_boundaries(
        pacer, pdf_fetcher, pdf_text_extractor
    )
    materialized = gold_builder.materialize_heldout_candidate_inputs(
        rows,
        _materialization_design(binding),
        binding.contract,
        pdf_fetcher=paced_fetcher,
        pdf_text_extractor=checked_extractor,
    )
    allowed_ineligible_reasons = {
        "pdf_text_below_min_char_gate",
        "pdf_exceeds_size_bound",
    }
    observed_reasons = set(materialized.reason_counts)
    if not observed_reasons <= allowed_ineligible_reasons or any(
        row.get("reason") not in allowed_ineligible_reasons
        for row in materialized.ineligible_candidates
    ):
        raise HeldoutPreparationError("unknown deterministic ineligible reason")
    input_payload = common.canonical_jsonl_bytes(materialized.eligible_records)
    manifest = {
        "schema_version": MATERIALIZATION_MANIFEST_V2_SCHEMA,
        "frame_id": FRAME_ID,
        "lineage": {
            "preregistration": {
                "path": PREREGISTRATION_PATH.as_posix(),
                "sha256": PREREGISTRATION_SHA256,
            },
            "design": {"path": DESIGN_PATH.as_posix(), "sha256": DESIGN_SHA256},
            "contract": {
                "path": HELDOUT_CONTRACT_PATH.as_posix(),
                "sha256": HELDOUT_CONTRACT_SHA256,
                "model": MODEL,
            },
            "source_window": {
                "start_inclusive_utc": WINDOW_START_UTC,
                "end_exclusive_utc": WINDOW_END_UTC,
            },
            "source_lineage": _verified_source_lineage(binding),
            "retired_selection_sha256": _mapping(
                _mapping(binding.preregistration["eligibility_and_sampling"], "eligibility")[
                    "retired_selection"
                ],
                "retired",
            )["sha256"],
        },
        "artifacts": {
            "eligible_inputs_jsonl": {
                "path": binding.artifacts["materialized_inputs"]
                .relative_to(binding.root)
                .as_posix(),
                "sha256": common.sha256_bytes(input_payload),
                "create_only": True,
            },
            "manifest": {
                "path": binding.artifacts["materialization_manifest"]
                .relative_to(binding.root)
                .as_posix(),
                "create_only": True,
            },
        },
        "counts": {
            "raw_source_window": EXPECTED_RAW_COUNT,
            "retired_excluded_before_materialization": EXPECTED_RAW_COUNT - len(rows),
            "all_candidates_after_retirement": len(materialized.all_candidates),
            "eligible_candidates": len(materialized.eligible_records),
            "ineligible_candidates": len(materialized.ineligible_candidates),
            "ineligible_by_reason": dict(materialized.reason_counts),
        },
        "layers": {
            "all_candidates": list(materialized.all_candidates),
            "eligible_candidates": [
                {
                    "news_item_id": row["news_item_id"],
                    "source": row["source"],
                    "input_sha256": row["input_sha256"],
                    "declared_input_sha256": row["declared_input_sha256"],
                    "text_sha256": row["text_sha256"],
                }
                for row in materialized.eligible_records
            ],
            "ineligible_candidates": list(materialized.ineligible_candidates),
        },
        "production_database": {"mode": "ro", "pragma_query_only": 1, "writes": 0},
        "execution_authority": _execution_authority_evidence(authorized),
        "request_pacing": {
            "cninfo_pdf": pacer.evidence(),
            "akshare_ths": "not_applicable_no_external_document_fetch",
            "sina_company_news": "not_applicable_no_external_document_fetch",
        },
        "runtime_start_preflight": runtime_start_preflight,
    }
    _publish_create_only(
        (
            (binding.artifacts["materialized_inputs"], input_payload),
            (binding.artifacts["materialization_manifest"], common.canonical_json_bytes(manifest)),
        )
    )
    return binding.artifacts["materialized_inputs"], binding.artifacts["materialization_manifest"]


def _extract_record(row: Mapping[str, Any]) -> ExtractRecord:
    return ExtractRecord(
        news_item_id=int(row["news_item_id"]),
        source=str(row["source"]),
        ingested_symbol=cast(str | None, row.get("ingested_symbol")),
        title=str(row["title"]),
        original_text=str(row["original_text"]),
        published_at=cast(str | None, row.get("published_at")),
        available_time=str(row["available_time"]),
        body_state=str(row["body_state"]),
        declared_input_sha256=str(row["declared_input_sha256"]),
        declared_text_sha256=str(row["text_sha256"]),
        declared_input_representation=DECLARED_INPUT_LEGACY_V1,
    )


def _append_state_descriptor(descriptor: int, row: Mapping[str, Any]) -> None:
    remaining = memoryview(common.canonical_json_bytes(row))
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("held-out inference state write made no progress")
        remaining = remaining[written:]
    os.fsync(descriptor)


@contextmanager
def _exclusive_inference_state(path: Path) -> Iterator[int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        _fsync_directory(path.parent)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield descriptor
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _validate_v2_1_inference_seams(
    authorized: V21ReleaseAuthorization | _OfflineRehearsalCapability,
    *,
    settings: Settings | None,
    chat_json_fn: ChatJsonCallable | None,
    snapshot_loader: ProductionSnapshotLoader,
    clock: Clock,
    execution_id_factory: ExecutionIdFactory,
    prediction_recorded_at_clock: RecordedAtClock | None,
    prediction_monotonic_ns_clock: MonotonicNsClock | None,
) -> None:
    if isinstance(authorized, V21ReleaseAuthorization):
        if (
            settings is not None
            or chat_json_fn is not None
            or snapshot_loader is not dev_runner._production_snapshot
            or clock is not _system_clock
            or execution_id_factory is not _random_execution_id
            or prediction_recorded_at_clock is not None
            or prediction_monotonic_ns_clock is not None
        ):
            raise HeldoutPreparationError(
                "real held-out inference forbids injected settings, model, snapshot, "
                "clock, or id seams"
            )
        return
    if (
        settings is not authorized.inference_settings
        or chat_json_fn is not authorized.chat_json_fn
        or snapshot_loader is not authorized.snapshot_loader
        or clock is not authorized.wall_clock
        or execution_id_factory is not authorized.execution_id_factory
        or prediction_recorded_at_clock is not authorized.prediction_recorded_at_clock
        or prediction_monotonic_ns_clock is not authorized.prediction_monotonic_ns_clock
    ):
        raise HeldoutPreparationError(
            "offline held-out inference seams differ from the minted capability"
        )


def run_infer(
    binding: HeldoutBinding,
    *,
    execution_context: ExecutionContext = None,
    settings: Settings | None = None,
    chat_json_fn: ChatJsonCallable | None = None,
    snapshot_loader: ProductionSnapshotLoader = dev_runner._production_snapshot,
    clock: Clock = _system_clock,
    execution_id_factory: ExecutionIdFactory = _random_execution_id,
    prediction_recorded_at_clock: RecordedAtClock | None = None,
    prediction_monotonic_ns_clock: MonotonicNsClock | None = None,
) -> tuple[Path, Path]:
    stage_authority = _prevalidate_v2_1_stage_authorization(
        binding,
        stage="infer",
        execution_context=execution_context,
    )
    authorized = _consume_prevalidated_v2_1_stage_authorization(
        binding,
        stage_authority,
        "infer",
    )
    _validate_v2_1_inference_seams(
        authorized,
        settings=settings,
        chat_json_fn=chat_json_fn,
        snapshot_loader=snapshot_loader,
        clock=clock,
        execution_id_factory=execution_id_factory,
        prediction_recorded_at_clock=prediction_recorded_at_clock,
        prediction_monotonic_ns_clock=prediction_monotonic_ns_clock,
    )
    candidates = _load_jsonl(binding.artifacts["materialized_inputs"], "held-out inputs")
    manifest = _load_json(binding.artifacts["materialization_manifest"], "materialization manifest")
    inputs_payload = common.canonical_jsonl_bytes(candidates)
    if binding.artifacts["materialized_inputs"].read_bytes() != inputs_payload:
        raise HeldoutPreparationError("materialized inputs are not canonical JSONL bytes")
    inputs_sha256 = common.sha256_bytes(inputs_payload)
    validate_v2_1_materialization_manifest(
        binding,
        manifest,
        candidates,
        inputs_sha256=inputs_sha256,
        prevalidated_authority=stage_authority,
        validated_stage="infer",
    )
    if _mapping(manifest.get("artifacts"), "manifest artifacts")["eligible_inputs_jsonl"][
        "sha256"
    ] != inputs_sha256:
        raise HeldoutPreparationError("materialized input digest drifted")
    ids = [int(row["news_item_id"]) for row in candidates]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise HeldoutPreparationError("eligible candidates are not unique ascending ids")
    for path in (
        binding.artifacts["inference_state"],
        binding.artifacts["predictions"],
        binding.artifacts["prediction_manifest"],
    ):
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to reuse held-out inference artifact: {path}")
    base_settings = settings or _settings_from_project_env(binding.root)
    try:
        active_settings = dev_runner._model_settings(base_settings, binding.contract)
        settings_safety = dev_runner._settings_safety(active_settings)
    except (
        dev_runner.CalibrationRoundError,
        HeldoutPredictionError,
    ) as exc:
        raise HeldoutPreparationError(
            "held-out inference settings failed the pre-state safety gate"
        ) from exc
    production_before = snapshot_loader(binding.root)
    materialization_sha256 = common.sha256_file(binding.artifacts["materialization_manifest"])
    execution_id = execution_id_factory()
    try:
        uuid.UUID(execution_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise HeldoutPreparationError("held-out inference execution id is not a UUID") from exc
    state = binding.artifacts["inference_state"]
    active_candidate_index: int | None = None
    active_candidate_id: int | None = None
    with _exclusive_inference_state(state) as state_descriptor:
        _append_state_descriptor(
            state_descriptor,
            {
                "schema_version": "p4.2a-v2-heldout-inference-state-v1",
                "status": "inference_started",
                "execution_id": execution_id,
                "started_at_utc": clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "eligible_candidate_count": len(candidates),
                "candidate_order": "ascending_news_item_id",
                "preregistration_sha256": PREREGISTRATION_SHA256,
                "materialization_manifest_sha256": materialization_sha256,
                "contract_sha256": HELDOUT_CONTRACT_SHA256,
                "model": MODEL,
                "automatic_retries": 0,
                "failed_candidate_retries": 0,
                "settings_safety": settings_safety,
                "production_snapshot_before": dev_runner._snapshot_evidence(
                    production_before
                ),
            },
        )
        try:
            for index, candidate in enumerate(candidates, start=1):
                active_candidate_index = index
                active_candidate_id = _positive_news_item_id(
                    candidate, f"eligible candidate {index}"
                )
                summary = extract_records(
                    binding.contract,
                    [_extract_record(candidate)],
                    output_path=binding.artifacts["predictions"],
                    eval_root=binding.artifacts["predictions"].parent,
                    universe_symbols=production_before.universe_symbols,
                    settings=active_settings,
                    retry_failures=False,
                    chat_json_fn=chat_json_fn,
                    recorded_at_clock=prediction_recorded_at_clock,
                    monotonic_ns_clock=prediction_monotonic_ns_clock,
                )
                if (
                    summary.newly_attempted_count != 1
                    or summary.success_count != 1
                    or summary.failure_count != 0
                ):
                    raise HeldoutPreparationError(
                        f"candidate {candidate['news_item_id']} failed; inference is terminal"
                    )
                if summary.retried_failure_count != 0:
                    raise HeldoutPreparationError("a held-out candidate was retried")
                if index != summary.output_line_count:
                    raise HeldoutPreparationError("prediction append-only line count drifted")
            production_after = snapshot_loader(binding.root)
            if production_after != production_before:
                raise HeldoutPreparationError("production database safety snapshot changed")
            predictions = _load_jsonl(binding.artifacts["predictions"], "held-out predictions")
            prediction_manifest = {
                "schema_version": "p4.2a-v2-heldout-prediction-manifest-v1",
                "frame_id": FRAME_ID,
                "execution_id": execution_id,
                "preregistration_sha256": PREREGISTRATION_SHA256,
                "materialization_manifest_sha256": materialization_sha256,
                "contract_sha256": HELDOUT_CONTRACT_SHA256,
                "model": MODEL,
                "candidate_count": len(candidates),
                "prediction_count": len(predictions),
                "status_ok_count": sum(row.get("status") == "ok" for row in predictions),
                "status_failed_count": sum(row.get("status") != "ok" for row in predictions),
                "one_news_item_per_request": True,
                "one_request_per_eligible_candidate": True,
                "automatic_retries": 0,
                "failed_candidate_retries": 0,
                "production_snapshot_unchanged": True,
                "production_snapshot_before": dev_runner._snapshot_evidence(
                    production_before
                ),
                "production_snapshot_after": dev_runner._snapshot_evidence(
                    production_after
                ),
                "settings_safety": settings_safety,
                "predictions": {
                    "path": binding.artifacts["predictions"].relative_to(binding.root).as_posix(),
                    "sha256": common.sha256_file(binding.artifacts["predictions"]),
                },
            }
            _publish_create_only(
                (
                    (
                        binding.artifacts["prediction_manifest"],
                        common.canonical_json_bytes(prediction_manifest),
                    ),
                )
            )
            _append_state_descriptor(
                state_descriptor,
                {
                    "schema_version": "p4.2a-v2-heldout-inference-state-v1",
                    "status": "completed_all_eligible_candidates_once",
                    "execution_id": execution_id,
                    "preregistration_sha256": PREREGISTRATION_SHA256,
                    "materialization_manifest_sha256": materialization_sha256,
                    "completed_at_utc": clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
                    "prediction_count": len(predictions),
                    "predictions_sha256": common.sha256_file(binding.artifacts["predictions"]),
                    "prediction_manifest_sha256": common.sha256_file(
                        binding.artifacts["prediction_manifest"]
                    ),
                    "production_snapshot_unchanged": True,
                    "production_snapshot_before": dev_runner._snapshot_evidence(
                        production_before
                    ),
                    "production_snapshot_after": dev_runner._snapshot_evidence(
                        production_after
                    ),
                },
            )
        except BaseException as exc:
            _append_state_descriptor(
                state_descriptor,
                {
                    "schema_version": "p4.2a-v2-heldout-inference-state-v1",
                    "status": "terminal_failed_no_sampling_no_retry",
                    "execution_id": execution_id,
                    "preregistration_sha256": PREREGISTRATION_SHA256,
                    "materialization_manifest_sha256": materialization_sha256,
                    "failed_at_utc": clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
                    "error_type": type(exc).__name__,
                    "failed_candidate_index": active_candidate_index,
                    "failed_news_item_id": active_candidate_id,
                    "automatic_retries": 0,
                    "failed_candidate_retries": 0,
                    "sampling_allowed": False,
                },
            )
            raise
    return binding.artifacts["predictions"], binding.artifacts["prediction_manifest"]


def _require_regular_artifact(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise HeldoutPreparationError(f"{label} is unavailable or not a regular file")


def _relative_artifact_path(binding: HeldoutBinding, name: str) -> str:
    return binding.artifacts[name].relative_to(binding.root).as_posix()


def _validate_request_pacing_evidence(
    value: object,
    *,
    expected_cninfo_requests: int,
) -> None:
    pacing = _mapping(value, "materialization request_pacing")
    if set(pacing) != {"cninfo_pdf", "akshare_ths", "sina_company_news"}:
        raise HeldoutPreparationError("materialization request_pacing schema drifted")
    if (
        pacing.get("akshare_ths") != "not_applicable_no_external_document_fetch"
        or pacing.get("sina_company_news")
        != "not_applicable_no_external_document_fetch"
    ):
        raise HeldoutPreparationError("non-CNInfo request pacing evidence drifted")
    cninfo = _mapping(pacing.get("cninfo_pdf"), "materialization CNInfo pacing")
    fields = {
        "host",
        "policy",
        "configured_min_start_to_start_seconds",
        "clock",
        "first_request_delayed",
        "request_start_count",
        "observed_gap_count",
        "minimum_observed_start_to_start_seconds",
        "median_observed_start_to_start_seconds",
        "violation_count",
        "retry_count",
    }
    if set(cninfo) != fields:
        raise HeldoutPreparationError("materialization CNInfo pacing schema drifted")
    request_count = cninfo.get("request_start_count")
    gap_count = cninfo.get("observed_gap_count")
    violations = cninfo.get("violation_count")
    retry_count = cninfo.get("retry_count")
    if any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0
        for item in (request_count, gap_count, violations, retry_count)
    ):
        raise HeldoutPreparationError("materialization CNInfo pacing counts are invalid")
    assert isinstance(request_count, int)
    assert isinstance(gap_count, int)
    configured_floor = cninfo.get("configured_min_start_to_start_seconds")
    minimum = cninfo.get("minimum_observed_start_to_start_seconds")
    median = cninfo.get("median_observed_start_to_start_seconds")
    if gap_count == 0:
        valid_statistics = minimum is None and median is None
    else:
        valid_statistics = (
            not isinstance(minimum, bool)
            and isinstance(minimum, (int, float))
            and math.isfinite(float(minimum))
            and float(minimum) >= CNINFO_MIN_START_TO_START_SECONDS
            and not isinstance(median, bool)
            and isinstance(median, (int, float))
            and math.isfinite(float(median))
            and float(median) >= float(minimum)
        )
    if (
        cninfo.get("host") != "static.cninfo.com.cn"
        or cninfo.get("policy") != "minimum_start_to_start"
        or isinstance(configured_floor, bool)
        or not isinstance(configured_floor, (int, float))
        or float(configured_floor) != CNINFO_MIN_START_TO_START_SECONDS
        or cninfo.get("clock") != "monotonic"
        or cninfo.get("first_request_delayed") is not False
        or request_count != expected_cninfo_requests
        or gap_count != max(request_count - 1, 0)
        or violations != 0
        or retry_count != 0
        or not valid_statistics
    ):
        raise HeldoutPreparationError("materialization CNInfo pacing evidence drifted")


def _validate_runtime_preflight_evidence(
    binding: HeldoutBinding,
    value: object,
    context: V21ReleaseAuthorization | _OfflineRehearsalCapability,
) -> None:
    evidence = _mapping(value, "materialization runtime_start_preflight")
    if isinstance(context, _OfflineRehearsalCapability):
        if evidence != _offline_runtime_start_preflight():
            raise HeldoutPreparationError("offline runtime preflight evidence drifted")
        return
    fields = {
        "mode",
        "observed_at_utc",
        "observed_at_shanghai",
        "backup_stamp",
        "database_backup_launchagent",
        "database_backup_lock",
        "verified_backup",
        "operator_timing_attestation",
    }
    if set(evidence) != fields or evidence.get("mode") != "real":
        raise HeldoutPreparationError("real runtime preflight schema drifted")
    observed_utc = _parse_aware_timestamp(
        evidence.get("observed_at_utc"), "runtime observed_at_utc"
    )
    observed_shanghai = _parse_aware_timestamp(
        evidence.get("observed_at_shanghai"), "runtime observed_at_shanghai"
    )
    if (
        observed_utc.utcoffset() != UTC.utcoffset(observed_utc)
        or observed_shanghai.utcoffset() != _SHANGHAI.utcoffset(observed_shanghai)
        or observed_utc.astimezone(UTC) != observed_shanghai.astimezone(UTC)
    ):
        raise HeldoutPreparationError("runtime observed timestamps drifted")
    stamp = _mapping(evidence.get("backup_stamp"), "runtime backup_stamp")
    launchagent = _mapping(
        evidence.get("database_backup_launchagent"), "runtime backup LaunchAgent"
    )
    lock = _mapping(evidence.get("database_backup_lock"), "runtime backup lock")
    verified = _mapping(evidence.get("verified_backup"), "runtime verified backup")
    operator = _mapping(
        evidence.get("operator_timing_attestation"), "runtime operator attestation"
    )
    runtime_directory = _database_backup_runtime_directory()
    if (
        set(stamp)
        != {"path", "expected_shanghai_date", "observed_value", "regular_file", "symlink", "mode"}
        or stamp.get("path")
        != str(runtime_directory / "last-success-shanghai-date")
        or stamp.get("expected_shanghai_date") != observed_shanghai.date().isoformat()
        or stamp.get("observed_value") != stamp.get("expected_shanghai_date")
        or stamp.get("regular_file") is not True
        or stamp.get("symlink") is not False
        or stamp.get("mode") != "0600"
    ):
        raise HeldoutPreparationError("runtime backup stamp evidence drifted")
    expected_target = f"gui/{os.getuid()}/com.alphapilot.database-backup"
    if (
        launchagent
        != {
            "label": "com.alphapilot.database-backup",
            "target": expected_target,
            "loaded": True,
            "state": "not running",
            "last_exit_code": 0,
        }
        or isinstance(launchagent.get("last_exit_code"), bool)
    ):
        raise HeldoutPreparationError("runtime backup LaunchAgent evidence drifted")
    if (
        set(lock)
        != {"path", "nonblocking_exclusive_flock_acquired", "held"}
        or lock.get("path") != str(runtime_directory / ".daily-backup.lock")
        or lock.get("nonblocking_exclusive_flock_acquired") is not True
        or lock.get("held") is not False
    ):
        raise HeldoutPreparationError("runtime backup lock evidence drifted")
    if set(verified) != {
        "manifest_path",
        "manifest_sha256",
        "backup_path",
        "backup_sha256",
        "created_at_utc",
        "created_at_shanghai",
        "quick_check",
        "verify_database_backup_passed",
    }:
        raise HeldoutPreparationError("runtime verified backup schema drifted")
    backup_created_utc = _parse_aware_timestamp(
        verified.get("created_at_utc"), "verified backup created_at_utc"
    )
    backup_created_shanghai = _parse_aware_timestamp(
        verified.get("created_at_shanghai"), "verified backup created_at_shanghai"
    )
    raw_backup_directory = binding.root / "data/backups"
    backup_directory = raw_backup_directory.resolve()
    manifest_path_raw = verified.get("manifest_path")
    backup_path_raw = verified.get("backup_path")
    if not isinstance(manifest_path_raw, str) or not isinstance(backup_path_raw, str):
        raise HeldoutPreparationError("runtime verified backup paths are invalid")
    manifest_path = Path(manifest_path_raw)
    backup_path = Path(backup_path_raw)
    if (
        raw_backup_directory.absolute() != backup_directory
        or raw_backup_directory.is_symlink()
        or manifest_path != manifest_path.resolve()
        or backup_path != backup_path.resolve()
        or not manifest_path.is_relative_to(backup_directory)
        or not backup_path.is_relative_to(backup_directory)
        or manifest_path.parent != backup_directory
        or backup_path.parent != backup_directory
        or not Path(manifest_path.name).match("alphapilot-full-*.manifest.json")
        or manifest_path != database_backup.manifest_path_for(backup_path)
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
        or backup_path.is_symlink()
        or not backup_path.is_file()
    ):
        raise HeldoutPreparationError("runtime verified backup paths escaped or drifted")
    recorded_manifest_sha = _require_sha256(
        verified.get("manifest_sha256"), "backup manifest SHA"
    )
    recorded_backup_sha = _require_sha256(verified.get("backup_sha256"), "backup SHA")
    if common.sha256_file(manifest_path) != recorded_manifest_sha:
        raise HeldoutPreparationError("runtime backup manifest bytes drifted")
    backup_manifest = _load_json(manifest_path, "runtime verified backup manifest")
    backup_manifest_evidence = _mapping(
        backup_manifest.get("backup"), "runtime verified backup manifest.backup"
    )
    if (
        backup_manifest.get("format_version") != database_backup.BACKUP_FORMAT_VERSION
        or backup_manifest.get("managed_by") != database_backup.BACKUP_MANAGED_BY
        or backup_manifest.get("created_at")
        != verified.get("created_at_utc")
        or backup_manifest_evidence.get("filename") != backup_path.name
        or backup_manifest_evidence.get("sha256") != recorded_backup_sha
        or backup_created_utc.astimezone(UTC)
        != backup_created_shanghai.astimezone(UTC)
        or backup_created_shanghai.astimezone(_SHANGHAI).date()
        != observed_shanghai.date()
        or backup_created_shanghai.astimezone(_SHANGHAI).hour < 22
        or backup_created_utc.astimezone(UTC) > observed_utc.astimezone(UTC)
        or verified.get("quick_check") != "ok"
        or verified.get("verify_database_backup_passed") is not True
    ):
        raise HeldoutPreparationError("runtime verified backup evidence drifted")
    expected_operator_fields = {
        "observed_start_cst",
        "attester_identity",
        "explicitly_supplied",
        "input_channel",
        "cninfo_midnight_batch_assessment",
        "p4_1_dense_poll_slot_assessment",
        "decision",
        "automatic_blackout_verification",
        "authority_path",
        "authority_sha256",
    }
    operator_start = _parse_aware_timestamp(
        operator.get("observed_start_cst"), "operator observed_start_cst"
    )
    if (
        set(operator) != expected_operator_fields
        or operator_start.astimezone(UTC) != observed_utc.astimezone(UTC)
        or operator_start.utcoffset() != _SHANGHAI.utcoffset(operator_start)
        or not isinstance(operator.get("attester_identity"), str)
        or not str(operator.get("attester_identity")).strip()
        or operator.get("explicitly_supplied") is not True
        or operator.get("input_channel")
        != "required_real_CLI_flags_or_required_typed_run_materialize_argument_no_default"
        or operator.get("cninfo_midnight_batch_assessment") != "clear_for_start"
        or operator.get("p4_1_dense_poll_slot_assessment") != "clear_for_start"
        or operator.get("decision")
        != "launched_outside_owner_identified_CNInfo_midnight_and_dense_P4_1_slots"
        or operator.get("automatic_blackout_verification") is not False
        or operator.get("authority_path")
        != SUCCESSOR_CODE_GATE_AUTHORITY_PATH.as_posix()
        or operator.get("authority_sha256") != SUCCESSOR_CODE_GATE_AUTHORITY_SHA256
    ):
        raise HeldoutPreparationError("runtime operator timing attestation drifted")


def validate_v2_1_materialization_manifest(
    binding: HeldoutBinding,
    manifest: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    inputs_sha256: str,
    validated_stage: str,
    execution_context: ExecutionContext = None,
    prevalidated_authority: _PrevalidatedStageAuthority | None = None,
) -> None:
    """Validate v2 legacy semantics plus exact successor control/evidence sections."""

    authorized, _delegated = _pure_revalidation_authority(
        binding,
        execution_context=execution_context,
        prevalidated_authority=prevalidated_authority,
        validated_stage=validated_stage,
    )
    expected_top_level = {
        "schema_version",
        "frame_id",
        "lineage",
        "artifacts",
        "counts",
        "layers",
        "production_database",
        "execution_authority",
        "request_pacing",
        "runtime_start_preflight",
    }
    if (
        set(manifest) != expected_top_level
        or manifest.get("schema_version") != MATERIALIZATION_MANIFEST_V2_SCHEMA
    ):
        raise HeldoutPreparationError("materialization manifest v2 schema drifted")
    if manifest.get("execution_authority") != _execution_authority_evidence(authorized):
        raise HeldoutPreparationError("materialization execution_authority drifted")
    layers = _mapping(manifest.get("layers"), "materialization layers")
    all_candidates = layers.get("all_candidates")
    if not isinstance(all_candidates, list):
        raise HeldoutPreparationError("materialization all-candidate layer is invalid")
    expected_cninfo_requests = sum(
        isinstance(row, Mapping) and row.get("source") == "cninfo"
        for row in all_candidates
    )
    _validate_request_pacing_evidence(
        manifest.get("request_pacing"),
        expected_cninfo_requests=expected_cninfo_requests,
    )
    _validate_runtime_preflight_evidence(
        binding,
        manifest.get("runtime_start_preflight"),
        authorized,
    )
    legacy_projection = copy.deepcopy(dict(manifest))
    for field in ("execution_authority", "request_pacing", "runtime_start_preflight"):
        legacy_projection.pop(field)
    legacy_projection["schema_version"] = "p4.2a-v2-heldout-materialization-manifest-v1"
    _validate_materialization_for_selection(
        binding,
        legacy_projection,
        candidates,
        inputs_sha256=inputs_sha256,
    )


def _validate_materialization_for_selection(
    binding: HeldoutBinding,
    manifest: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    inputs_sha256: str,
) -> None:
    if manifest.get("synthetic_rehearsal") is True:
        if binding.root.resolve() == PROJECT_ROOT.resolve():
            raise HeldoutPreparationError(
                "synthetic materialization cannot enter the production owner chain"
            )
        synthetic_fields = {
            "schema_version",
            "frame_id",
            "synthetic_rehearsal",
            "lineage",
            "artifacts",
            "counts",
            "production_database",
        }
        artifacts = _mapping(manifest.get("artifacts"), "synthetic materialization artifacts")
        if (
            set(manifest) != synthetic_fields
            or manifest.get("schema_version")
            != "p4.2a-v2-heldout-materialization-manifest-v1"
            or manifest.get("frame_id") != FRAME_ID
            or manifest.get("lineage")
            != {
                "preregistration_sha256": PREREGISTRATION_SHA256,
                "design_sha256": DESIGN_SHA256,
                "heldout_contract_sha256": HELDOUT_CONTRACT_SHA256,
            }
            or artifacts
            != {
                "eligible_inputs_jsonl": {
                    "path": _relative_artifact_path(binding, "materialized_inputs"),
                    "sha256": inputs_sha256,
                    "create_only": True,
                }
            }
            or manifest.get("counts")
            != {
                "all_candidates": len(candidates),
                "eligible_candidates": len(candidates),
                "ineligible_candidates": 0,
            }
            or manifest.get("production_database")
            != {"opened": False, "reads": 0, "writes": 0}
        ):
            raise HeldoutPreparationError("synthetic materialization manifest drifted")
        return
    expected_top_level = {
        "schema_version",
        "frame_id",
        "lineage",
        "artifacts",
        "counts",
        "layers",
        "production_database",
    }
    if set(manifest) != expected_top_level:
        raise HeldoutPreparationError("materialization manifest schema drifted")
    lineage = _mapping(manifest.get("lineage"), "materialization lineage")
    if set(lineage) != {
        "preregistration",
        "design",
        "contract",
        "source_window",
        "source_lineage",
        "retired_selection_sha256",
    }:
        raise HeldoutPreparationError("materialization lineage schema drifted")
    artifacts = _mapping(manifest.get("artifacts"), "materialization artifacts")
    if set(artifacts) != {"eligible_inputs_jsonl", "manifest"}:
        raise HeldoutPreparationError("materialization artifact schema drifted")
    inputs_ref = _mapping(artifacts.get("eligible_inputs_jsonl"), "materialized inputs ref")
    manifest_ref = _mapping(artifacts.get("manifest"), "materialization manifest ref")
    counts = _mapping(manifest.get("counts"), "materialization counts")
    if set(counts) != {
        "raw_source_window",
        "retired_excluded_before_materialization",
        "all_candidates_after_retirement",
        "eligible_candidates",
        "ineligible_candidates",
        "ineligible_by_reason",
    }:
        raise HeldoutPreparationError("materialization count schema drifted")
    layers = _mapping(manifest.get("layers"), "materialization layers")
    if set(layers) != {"all_candidates", "eligible_candidates", "ineligible_candidates"}:
        raise HeldoutPreparationError("materialization layer schema drifted")
    all_layer = layers.get("all_candidates")
    eligible_layer = layers.get("eligible_candidates")
    ineligible_layer = layers.get("ineligible_candidates")
    if not all(isinstance(value, list) for value in (all_layer, eligible_layer, ineligible_layer)):
        raise HeldoutPreparationError("materialization layers are invalid")
    all_rows = cast(list[Any], all_layer)
    eligible_rows = cast(list[Any], eligible_layer)
    ineligible_rows = cast(list[Any], ineligible_layer)
    _validate_candidate_input_hashes(candidates, binding.contract)
    window_start = datetime.fromisoformat(WINDOW_START_UTC.replace("Z", "+00:00"))
    window_end = datetime.fromisoformat(WINDOW_END_UTC.replace("Z", "+00:00"))
    candidate_ids: list[int] = []
    for index, candidate in enumerate(candidates, 1):
        identifier = _positive_news_item_id(candidate, f"candidate row {index}")
        available_raw = candidate.get("available_time")
        if not isinstance(available_raw, str) or not available_raw:
            raise HeldoutPreparationError(
                f"candidate row {index} available_time is invalid"
            )
        try:
            available = datetime.fromisoformat(available_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HeldoutPreparationError(
                f"candidate row {index} available_time is invalid"
            ) from exc
        if (
            available.tzinfo is None
            or available.utcoffset() is None
            or not window_start <= available.astimezone(UTC) < window_end
            or candidate.get("source") not in EXPECTED_BY_SOURCE
            or identifier in binding.retired_ids
        ):
            raise HeldoutPreparationError(
                f"candidate row {index} is outside the frozen materialization frame"
            )
        candidate_ids.append(identifier)
    if candidate_ids != sorted(candidate_ids) or len(candidate_ids) != len(set(candidate_ids)):
        raise HeldoutPreparationError("materialized candidate ids are not unique ascending")
    expected_eligible_layer = [
        {
            "news_item_id": row.get("news_item_id"),
            "source": row.get("source"),
            "input_sha256": row.get("input_sha256"),
            "declared_input_sha256": row.get("declared_input_sha256"),
            "text_sha256": row.get("text_sha256"),
        }
        for row in candidates
    ]
    contract_ref = _mapping(lineage.get("contract"), "materialization contract")
    eligibility = _mapping(
        binding.preregistration.get("eligibility_and_sampling"),
        "eligibility_and_sampling",
    )
    retired = _mapping(eligibility.get("retired_selection"), "retired_selection")
    production_database = _mapping(
        manifest.get("production_database"), "materialization production database"
    )
    if (
        set(production_database) != {"mode", "pragma_query_only", "writes"}
        or production_database.get("mode") != "ro"
        or isinstance(production_database.get("pragma_query_only"), bool)
        or production_database.get("pragma_query_only") != 1
        or isinstance(production_database.get("writes"), bool)
        or production_database.get("writes") != 0
    ):
        raise HeldoutPreparationError("materialization production database safety drifted")
    if (
        manifest.get("schema_version") != "p4.2a-v2-heldout-materialization-manifest-v1"
        or manifest.get("frame_id") != FRAME_ID
        or lineage.get("preregistration")
        != {"path": PREREGISTRATION_PATH.as_posix(), "sha256": PREREGISTRATION_SHA256}
        or lineage.get("design")
        != {"path": DESIGN_PATH.as_posix(), "sha256": DESIGN_SHA256}
        or contract_ref
        != {
            "path": HELDOUT_CONTRACT_PATH.as_posix(),
            "sha256": HELDOUT_CONTRACT_SHA256,
            "model": MODEL,
        }
        or lineage.get("source_window")
        != {
            "start_inclusive_utc": WINDOW_START_UTC,
            "end_exclusive_utc": WINDOW_END_UTC,
        }
        or lineage.get("source_lineage") != _verified_source_lineage(binding)
        or lineage.get("retired_selection_sha256") != retired.get("sha256")
        or inputs_ref
        != {
            "path": _relative_artifact_path(binding, "materialized_inputs"),
            "sha256": inputs_sha256,
            "create_only": True,
        }
        or manifest_ref
        != {
            "path": _relative_artifact_path(binding, "materialization_manifest"),
            "create_only": True,
        }
        or eligible_rows != expected_eligible_layer
    ):
        raise HeldoutPreparationError("materialization manifest lineage or counts drifted")

    count_values: dict[str, int] = {}
    for field in (
        "raw_source_window",
        "retired_excluded_before_materialization",
        "all_candidates_after_retirement",
        "eligible_candidates",
        "ineligible_candidates",
    ):
        value = counts.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HeldoutPreparationError(f"materialization count {field} is invalid")
        count_values[field] = value
    if (
        count_values["raw_source_window"] != EXPECTED_RAW_COUNT
        or count_values["retired_excluded_before_materialization"] != 0
        or count_values["all_candidates_after_retirement"] != EXPECTED_RAW_COUNT
        or count_values["eligible_candidates"] != len(candidates)
        or count_values["ineligible_candidates"] != len(ineligible_rows)
        or len(all_rows) != EXPECTED_RAW_COUNT
        or len(candidates) + len(ineligible_rows) != EXPECTED_RAW_COUNT
    ):
        raise HeldoutPreparationError("materialization production counts drifted")

    all_ids: list[int] = []
    all_by_id: dict[int, Mapping[str, Any]] = {}
    source_counts: Counter[str] = Counter()
    for index, raw in enumerate(all_rows, 1):
        row = _mapping(raw, f"materialization all candidate {index}")
        if set(row) != {"news_item_id", "source", "url", "content_hash"}:
            raise HeldoutPreparationError("materialization all-candidate schema drifted")
        identifier = _positive_news_item_id(row, f"materialization all candidate {index}")
        source = row.get("source")
        url = row.get("url")
        content_hash = row.get("content_hash")
        if (
            source not in EXPECTED_BY_SOURCE
            or not isinstance(url, str)
            or not url
            or not isinstance(content_hash, str)
            or len(content_hash) != 64
            or any(character not in _LOWER_HEX for character in content_hash)
        ):
            raise HeldoutPreparationError("materialization all-candidate identity drifted")
        all_ids.append(identifier)
        all_by_id[identifier] = row
        source_counts[cast(str, source)] += 1
    if (
        all_ids != sorted(all_ids)
        or len(all_ids) != len(set(all_ids))
        or set(all_ids).intersection(binding.retired_ids)
        or dict(sorted(source_counts.items())) != dict(sorted(EXPECTED_BY_SOURCE.items()))
    ):
        raise HeldoutPreparationError("materialization raw source composition drifted")
    for candidate in candidates:
        identifier = cast(int, candidate["news_item_id"])
        identity = all_by_id.get(identifier)
        if identity is None or any(
            identity.get(field) != candidate.get(field)
            for field in ("source", "url", "content_hash")
        ):
            raise HeldoutPreparationError(
                "materialization eligible identity differs from all-candidate layer"
            )

    eligibility_document = _mapping(
        yaml.safe_load(
            (binding.root / "config/p4_event_evaluation_v1_7.yaml").read_bytes()
        ),
        "v1.7 eligibility design",
    )
    eligibility_policy = _mapping(
        eligibility_document.get("candidate_eligibility"),
        "v1.7 candidate eligibility",
    )
    minimum_characters = eligibility_policy.get("minimum_extracted_characters")
    maximum_pdf_bytes = eligibility_policy.get("max_pdf_bytes")
    if (
        isinstance(minimum_characters, bool)
        or not isinstance(minimum_characters, int)
        or minimum_characters <= 0
        or isinstance(maximum_pdf_bytes, bool)
        or not isinstance(maximum_pdf_bytes, int)
        or maximum_pdf_bytes <= 0
    ):
        raise HeldoutPreparationError("frozen PDF eligibility gates drifted")
    ineligible_ids: list[int] = []
    reason_counts: Counter[str] = Counter()
    for index, raw in enumerate(ineligible_rows, 1):
        row = _mapping(raw, f"materialization ineligible candidate {index}")
        if set(row) != {
            "news_item_id",
            "url",
            "reason",
            "measured_value",
            "gate_value",
            "pdf_sha256",
        }:
            raise HeldoutPreparationError("materialization ineligible schema drifted")
        identifier = _positive_news_item_id(
            row, f"materialization ineligible candidate {index}"
        )
        reason = row.get("reason")
        measured = row.get("measured_value")
        gate = row.get("gate_value")
        pdf_sha = row.get("pdf_sha256")
        identity = all_by_id.get(identifier)
        url_value = row.get("url")
        parsed_url = urlparse(url_value) if isinstance(url_value, str) else None
        if (
            reason not in {"pdf_text_below_min_char_gate", "pdf_exceeds_size_bound"}
            or isinstance(measured, bool)
            or not isinstance(measured, int)
            or measured < 0
            or isinstance(gate, bool)
            or not isinstance(gate, int)
            or gate <= 0
            or identity is None
            or identity.get("source") != "cninfo"
            or url_value != identity.get("url")
            or parsed_url is None
            or parsed_url.scheme != "https"
            or parsed_url.hostname != "static.cninfo.com.cn"
            or not parsed_url.path.casefold().endswith(".pdf")
            or (
                pdf_sha is not None
                and (
                    not isinstance(pdf_sha, str)
                    or len(pdf_sha) != 64
                    or any(character not in _LOWER_HEX for character in pdf_sha)
                )
            )
            or (
                reason == "pdf_text_below_min_char_gate"
                and (
                    gate != minimum_characters
                    or measured >= gate
                    or pdf_sha is None
                )
            )
            or (
                reason == "pdf_exceeds_size_bound"
                and (gate != maximum_pdf_bytes or measured <= gate)
            )
        ):
            raise HeldoutPreparationError("materialization ineligible evidence drifted")
        ineligible_ids.append(identifier)
        reason_counts[cast(str, reason)] += 1
    if (
        len(ineligible_ids) != len(set(ineligible_ids))
        or set(candidate_ids).intersection(ineligible_ids)
        or set(all_ids) != set(candidate_ids).union(ineligible_ids)
        or counts.get("ineligible_by_reason") != dict(sorted(reason_counts.items()))
    ):
        raise HeldoutPreparationError("materialization layer coverage drifted")


def _aware_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise HeldoutPreparationError(f"{label} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HeldoutPreparationError(
            f"{label} must be a timezone-aware timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HeldoutPreparationError(f"{label} must be a timezone-aware timestamp")
    return parsed


def _validate_snapshot(value: object, label: str) -> JsonObject:
    snapshot = _mapping(value, label)
    if set(snapshot) != _SNAPSHOT_FIELDS:
        raise HeldoutPreparationError(f"{label} schema drifted")
    for field in (
        "pragma_query_only",
        "connection_total_changes",
        "llm_call_count",
        "trade_proposal_count",
        "broker_order_count",
        "non_simulate_order_count",
        "universe_symbol_count",
    ):
        item = snapshot.get(field)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise HeldoutPreparationError(f"{label}.{field} is invalid")
    maximum_id = snapshot.get("llm_call_max_id")
    if maximum_id is not None and (
        isinstance(maximum_id, bool) or not isinstance(maximum_id, int) or maximum_id <= 0
    ):
        raise HeldoutPreparationError(f"{label}.llm_call_max_id is invalid")
    if (
        snapshot.get("sqlite_uri_mode") != "ro"
        or snapshot.get("pragma_query_only") != 1
        or snapshot.get("connection_total_changes") != 0
        or not isinstance(snapshot.get("news_events_table_exists"), bool)
        or snapshot.get("non_simulate_order_count") != 0
    ):
        raise HeldoutPreparationError(f"{label} does not prove read-only safety")
    return snapshot


def _validate_candidate_prediction_rows(
    binding: HeldoutBinding,
    candidates: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
) -> None:
    """Revalidate every inference input/output, including unselected rows."""

    if len(candidates) != len(predictions):
        raise HeldoutPreparationError("candidate/prediction coverage drifted")
    _validate_candidate_input_hashes(candidates, binding.contract)
    universe_symbols: set[str] = set()
    for candidate in candidates:
        symbol = candidate.get("ingested_symbol")
        if isinstance(symbol, str):
            universe_symbols.add(symbol)
        original_text = candidate.get("original_text")
        if isinstance(original_text, str):
            universe_symbols.update(_SIX_DIGIT_SYMBOL_RE.findall(original_text))
    for index, (candidate, prediction) in enumerate(
        zip(candidates, predictions, strict=True), 1
    ):
        if set(prediction) != common.PREDICTION_KEYS:
            raise HeldoutPreparationError(f"prediction row {index} schema drifted")
        try:
            prepared = offline_extract._prepare_records(
                binding.contract,
                [_extract_record(candidate)],
            )[0]
            offline_extract._validate_resumed_success(
                prediction,
                prepared,
                binding.contract,
                universe_symbols,
            )
        except (
            offline_extract.OfflineExtractError,
            EventExtractValidationError,
        ) as exc:
            raise HeldoutPreparationError(
                f"prediction row {index} contract/security/result drifted"
            ) from exc


def _validate_completed_inference(
    binding: HeldoutBinding,
    *,
    states: Sequence[Mapping[str, Any]],
    prediction_manifest: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    materialization_sha256: str,
    predictions_sha256: str,
    prediction_manifest_sha256: str,
) -> SelectionExecutionBinding:
    if len(states) != 2:
        raise HeldoutPreparationError("inference state must contain exactly two events")
    started, completed = states
    started_fields = {
        "schema_version",
        "status",
        "execution_id",
        "started_at_utc",
        "eligible_candidate_count",
        "candidate_order",
        "preregistration_sha256",
        "materialization_manifest_sha256",
        "contract_sha256",
        "model",
        "automatic_retries",
        "failed_candidate_retries",
        "settings_safety",
        "production_snapshot_before",
    }
    completed_fields = {
        "schema_version",
        "status",
        "execution_id",
        "preregistration_sha256",
        "materialization_manifest_sha256",
        "completed_at_utc",
        "prediction_count",
        "predictions_sha256",
        "prediction_manifest_sha256",
        "production_snapshot_unchanged",
        "production_snapshot_before",
        "production_snapshot_after",
    }
    manifest_fields = {
        "schema_version",
        "frame_id",
        "execution_id",
        "preregistration_sha256",
        "materialization_manifest_sha256",
        "contract_sha256",
        "model",
        "candidate_count",
        "prediction_count",
        "status_ok_count",
        "status_failed_count",
        "one_news_item_per_request",
        "one_request_per_eligible_candidate",
        "automatic_retries",
        "failed_candidate_retries",
        "production_snapshot_unchanged",
        "production_snapshot_before",
        "production_snapshot_after",
        "settings_safety",
        "predictions",
    }
    if (
        set(started) != started_fields
        or set(completed) != completed_fields
        or set(prediction_manifest) != manifest_fields
    ):
        raise HeldoutPreparationError("inference state or prediction manifest schema drifted")
    execution_id = started.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        raise HeldoutPreparationError("inference execution id is invalid")
    try:
        uuid.UUID(execution_id)
    except ValueError as exc:
        raise HeldoutPreparationError("inference execution id is invalid") from exc
    started_at = _aware_datetime(started.get("started_at_utc"), "inference start")
    completed_at = _aware_datetime(completed.get("completed_at_utc"), "inference completion")
    preregistered_at = _aware_datetime(
        binding.preregistration.get("created_at_utc"),
        "held-out preregistration created_at_utc",
    )
    source_window_end = _aware_datetime(WINDOW_END_UTC, "held-out source window end")
    if started_at < preregistered_at or started_at < source_window_end:
        raise HeldoutPreparationError(
            "inference start precedes preregistration or source-window closure"
        )
    if completed_at < started_at:
        raise HeldoutPreparationError("inference completion precedes its start")
    if any(row.get("status") != "ok" for row in predictions):
        raise HeldoutPreparationError("not every eligible prediction has status ok")
    _validate_candidate_prediction_rows(binding, candidates, predictions)
    previous_recorded_at = started_at
    for index, prediction in enumerate(predictions, 1):
        recorded_at = _aware_datetime(
            prediction.get("recorded_at_utc"),
            f"prediction row {index} recorded_at",
        )
        if (
            recorded_at < started_at
            or recorded_at > completed_at
            or recorded_at < previous_recorded_at
        ):
            raise HeldoutPreparationError(
                "prediction recorded_at timeline is outside or reorders inference"
            )
        previous_recorded_at = recorded_at
    before = _validate_snapshot(
        started.get("production_snapshot_before"), "production snapshot before"
    )
    completed_before = _validate_snapshot(
        completed.get("production_snapshot_before"), "completed snapshot before"
    )
    after = _validate_snapshot(
        completed.get("production_snapshot_after"), "production snapshot after"
    )
    manifest_before = _validate_snapshot(
        prediction_manifest.get("production_snapshot_before"),
        "manifest snapshot before",
    )
    manifest_after = _validate_snapshot(
        prediction_manifest.get("production_snapshot_after"),
        "manifest snapshot after",
    )
    for value, label in (
        (started.get("eligible_candidate_count"), "eligible_candidate_count"),
        (started.get("automatic_retries"), "started automatic_retries"),
        (started.get("failed_candidate_retries"), "started failed_candidate_retries"),
        (completed.get("prediction_count"), "completed prediction_count"),
        (prediction_manifest.get("candidate_count"), "manifest candidate_count"),
        (prediction_manifest.get("prediction_count"), "manifest prediction_count"),
        (prediction_manifest.get("status_ok_count"), "manifest status_ok_count"),
        (prediction_manifest.get("status_failed_count"), "manifest status_failed_count"),
        (prediction_manifest.get("automatic_retries"), "manifest automatic_retries"),
        (
            prediction_manifest.get("failed_candidate_retries"),
            "manifest failed_candidate_retries",
        ),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HeldoutPreparationError(f"{label} is invalid")
    predictions_ref = _mapping(
        prediction_manifest.get("predictions"), "prediction manifest predictions"
    )
    if (
        started.get("schema_version") != "p4.2a-v2-heldout-inference-state-v1"
        or started.get("status") != "inference_started"
        or started.get("eligible_candidate_count") != len(candidates)
        or started.get("candidate_order") != "ascending_news_item_id"
        or started.get("preregistration_sha256") != PREREGISTRATION_SHA256
        or started.get("materialization_manifest_sha256") != materialization_sha256
        or started.get("contract_sha256") != HELDOUT_CONTRACT_SHA256
        or started.get("model") != MODEL
        or started.get("automatic_retries") != 0
        or started.get("failed_candidate_retries") != 0
        or started.get("settings_safety") != _INFERENCE_SETTINGS_SAFETY
        or completed.get("schema_version") != "p4.2a-v2-heldout-inference-state-v1"
        or completed.get("status") != "completed_all_eligible_candidates_once"
        or completed.get("execution_id") != execution_id
        or completed.get("preregistration_sha256") != PREREGISTRATION_SHA256
        or completed.get("materialization_manifest_sha256") != materialization_sha256
        or completed.get("prediction_count") != len(predictions)
        or completed.get("predictions_sha256") != predictions_sha256
        or completed.get("prediction_manifest_sha256") != prediction_manifest_sha256
        or completed.get("production_snapshot_unchanged") is not True
        or completed.get("production_snapshot_before")
        != before
        or completed_before != before
        or after != before
        or prediction_manifest.get("schema_version")
        != "p4.2a-v2-heldout-prediction-manifest-v1"
        or prediction_manifest.get("frame_id") != FRAME_ID
        or prediction_manifest.get("execution_id") != execution_id
        or prediction_manifest.get("preregistration_sha256") != PREREGISTRATION_SHA256
        or prediction_manifest.get("materialization_manifest_sha256")
        != materialization_sha256
        or prediction_manifest.get("contract_sha256") != HELDOUT_CONTRACT_SHA256
        or prediction_manifest.get("model") != MODEL
        or prediction_manifest.get("candidate_count") != len(candidates)
        or prediction_manifest.get("prediction_count") != len(predictions)
        or prediction_manifest.get("status_ok_count") != len(predictions)
        or prediction_manifest.get("status_failed_count") != 0
        or prediction_manifest.get("one_news_item_per_request") is not True
        or prediction_manifest.get("one_request_per_eligible_candidate") is not True
        or prediction_manifest.get("automatic_retries") != 0
        or prediction_manifest.get("failed_candidate_retries") != 0
        or prediction_manifest.get("production_snapshot_unchanged") is not True
        or prediction_manifest.get("production_snapshot_before")
        != started.get("production_snapshot_before")
        or prediction_manifest.get("production_snapshot_after")
        != completed.get("production_snapshot_after")
        or prediction_manifest.get("settings_safety") != started.get("settings_safety")
        or manifest_before != before
        or manifest_after != after
        or predictions_ref
        != {
            "path": _relative_artifact_path(binding, "predictions"),
            "sha256": predictions_sha256,
        }
    ):
        raise HeldoutPreparationError("completed inference hash/count lineage drifted")
    candidate_ids = [
        _positive_news_item_id(row, f"candidate row {index}")
        for index, row in enumerate(candidates, start=1)
    ]
    prediction_ids = [
        _positive_news_item_id(row, f"prediction row {index}")
        for index, row in enumerate(predictions, start=1)
    ]
    if candidate_ids != sorted(candidate_ids) or len(candidate_ids) != len(set(candidate_ids)):
        raise HeldoutPreparationError("eligible candidate IDs are not unique ascending IDs")
    if prediction_ids != candidate_ids:
        raise HeldoutPreparationError(
            "prediction order and IDs do not exactly match eligible candidates"
        )
    return SelectionExecutionBinding(
        materialization_manifest_sha256=materialization_sha256,
        inference_state_sha256=common.sha256_file(binding.artifacts["inference_state"]),
        prediction_manifest_sha256=prediction_manifest_sha256,
        execution_id=execution_id,
        eligible_candidate_count=len(candidates),
        prediction_count=len(predictions),
        status_ok_count=len(predictions),
        status_failed_count=0,
        started_at_utc=cast(str, started["started_at_utc"]),
        completed_at_utc=cast(str, completed["completed_at_utc"]),
    )


def _selection_execution_binding_from_artifacts(
    binding: HeldoutBinding,
    candidates: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    *,
    validated_stage: str,
    execution_context: ExecutionContext = None,
    prevalidated_authority: _PrevalidatedStageAuthority | None = None,
) -> SelectionExecutionBinding:
    _authorized, delegated = _pure_revalidation_authority(
        binding,
        execution_context=execution_context,
        prevalidated_authority=prevalidated_authority,
        validated_stage=validated_stage,
    )
    for name in (
        "materialized_inputs",
        "materialization_manifest",
        "inference_state",
        "predictions",
        "prediction_manifest",
    ):
        _require_regular_artifact(binding.artifacts[name], name.replace("_", " "))
    inputs_payload = common.canonical_jsonl_bytes(candidates)
    predictions_payload = common.canonical_jsonl_bytes(predictions)
    if binding.artifacts["materialized_inputs"].read_bytes() != inputs_payload:
        raise HeldoutPreparationError("materialized inputs are not canonical JSONL bytes")
    if binding.artifacts["predictions"].read_bytes() != predictions_payload:
        raise HeldoutPreparationError("predictions are not canonical JSONL bytes")
    materialization_manifest = _load_json(
        binding.artifacts["materialization_manifest"], "materialization manifest"
    )
    states = _load_jsonl(binding.artifacts["inference_state"], "inference state")
    prediction_manifest = _load_json(
        binding.artifacts["prediction_manifest"], "prediction manifest"
    )
    inputs_sha256 = common.sha256_bytes(inputs_payload)
    predictions_sha256 = common.sha256_bytes(predictions_payload)
    materialization_sha256 = common.sha256_file(binding.artifacts["materialization_manifest"])
    prediction_manifest_sha256 = common.sha256_file(binding.artifacts["prediction_manifest"])
    validate_v2_1_materialization_manifest(
        binding,
        materialization_manifest,
        candidates,
        inputs_sha256=inputs_sha256,
        prevalidated_authority=delegated,
        validated_stage=delegated.validated_stage,
    )
    return _validate_completed_inference(
        binding,
        states=states,
        prediction_manifest=prediction_manifest,
        candidates=candidates,
        predictions=predictions,
        materialization_sha256=materialization_sha256,
        predictions_sha256=predictions_sha256,
        prediction_manifest_sha256=prediction_manifest_sha256,
    )


def run_select_blind(
    binding: HeldoutBinding,
    *,
    execution_context: ExecutionContext = None,
) -> tuple[Path, Path]:
    stage_authority = _prevalidate_v2_1_stage_authorization(
        binding,
        stage="select-blind",
        execution_context=execution_context,
    )
    source_names = (
        "materialized_inputs",
        "materialization_manifest",
        "inference_state",
        "predictions",
    )
    for name in source_names:
        _require_regular_artifact(binding.artifacts[name], name.replace("_", " "))
    states = _load_jsonl(binding.artifacts["inference_state"], "inference state")
    if (
        len(states) != 2
        or states[0].get("status") != "inference_started"
        or states[1].get("status") != "completed_all_eligible_candidates_once"
    ):
        raise HeldoutPreparationError("inference is not in the completed terminal state")
    _require_regular_artifact(
        binding.artifacts["prediction_manifest"], "prediction manifest"
    )
    candidates = _load_jsonl(binding.artifacts["materialized_inputs"], "held-out inputs")
    materialization_manifest = _load_json(
        binding.artifacts["materialization_manifest"], "materialization manifest"
    )
    predictions = _load_jsonl(binding.artifacts["predictions"], "held-out predictions")
    prediction_manifest = _load_json(
        binding.artifacts["prediction_manifest"], "prediction manifest"
    )
    inputs_payload = common.canonical_jsonl_bytes(candidates)
    predictions_payload = common.canonical_jsonl_bytes(predictions)
    if binding.artifacts["materialized_inputs"].read_bytes() != inputs_payload:
        raise HeldoutPreparationError("materialized inputs are not canonical JSONL bytes")
    if binding.artifacts["predictions"].read_bytes() != predictions_payload:
        raise HeldoutPreparationError("predictions are not canonical JSONL bytes")
    inputs_sha256 = common.sha256_bytes(inputs_payload)
    predictions_sha256 = common.sha256_bytes(predictions_payload)
    materialization_sha256 = common.sha256_file(binding.artifacts["materialization_manifest"])
    prediction_manifest_sha256 = common.sha256_file(binding.artifacts["prediction_manifest"])
    validate_v2_1_materialization_manifest(
        binding,
        materialization_manifest,
        candidates,
        inputs_sha256=inputs_sha256,
        prevalidated_authority=stage_authority,
        validated_stage="select-blind",
    )
    execution_binding = _validate_completed_inference(
        binding,
        states=states,
        prediction_manifest=prediction_manifest,
        candidates=candidates,
        predictions=predictions,
        materialization_sha256=materialization_sha256,
        predictions_sha256=predictions_sha256,
        prediction_manifest_sha256=prediction_manifest_sha256,
    )
    result = select_and_blind(
        binding,
        candidates,
        predictions,
        execution_binding=execution_binding,
    )
    _publish_create_only(
        (
            (binding.artifacts["private_selection"], common.canonical_json_bytes(result.manifest)),
            (binding.artifacts["owner_blind"], common.canonical_jsonl_bytes(result.blind_rows)),
        )
    )
    return binding.artifacts["private_selection"], binding.artifacts["owner_blind"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare the frozen P4.2a v2 held-out frame")
    parser.add_argument(
        "stage", choices=("validate", "synthetic-rehearsal", "materialize", "infer", "select-blind")
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--attester-identity")
    parser.add_argument(
        "--cninfo-midnight-batch-assessment",
        choices=("clear_for_start",),
    )
    parser.add_argument(
        "--p4-1-dense-poll-slot-assessment",
        choices=("clear_for_start",),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> NoReturn:
    parser = _parser()
    args = parser.parse_args(argv)
    binding = load_binding(args.project_root)
    if args.stage == "validate":
        try:
            release = validate_v2_1_release_authorization(binding.root)
        except HeldoutPreparationError as exc:
            result: object = {
                "status": "BLOCKED_PENDING_SUCCESSOR_V2_1_OWNER_RELEASE",
                "valid": False,
                "reason": str(exc),
                "successor_preregistration_sha256": (
                    SUCCESSOR_V2_1_PREREGISTRATION_SHA256
                ),
            }
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            raise SystemExit(2) from None
        result = {
            "status": "valid_successor_v2_1_owner_released",
            "valid": True,
            "release_receipt_sha256": release.receipt_sha256,
            "implementation_commit": release.implementation_commit,
        }
    elif args.stage == "synthetic-rehearsal":
        result = run_synthetic_rehearsal(binding).as_posix()
    elif args.stage == "materialize":
        if (
            args.attester_identity is None
            or args.cninfo_midnight_batch_assessment is None
            or args.p4_1_dense_poll_slot_assessment is None
        ):
            parser.error(
                "materialize requires --attester-identity and both explicit "
                "clear_for_start assessment flags"
            )
        attestation = OperatorTimingAttestation(
            attester_identity=args.attester_identity,
            cninfo_midnight_batch_assessment=args.cninfo_midnight_batch_assessment,
            p4_1_dense_poll_slot_assessment=args.p4_1_dense_poll_slot_assessment,
        )
        result = [
            path.as_posix()
            for path in run_materialize(
                binding,
                operator_timing_attestation=attestation,
            )
        ]
    elif args.stage == "infer":
        result = [path.as_posix() for path in run_infer(binding)]
    else:
        result = [path.as_posix() for path in run_select_blind(binding)]
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
