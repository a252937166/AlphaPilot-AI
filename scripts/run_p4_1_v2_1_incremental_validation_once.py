#!/usr/bin/env python3
"""Run the single authorized P4.1 v2.1 validation with read-only tracing.

The trace observes which already-reviewed branch terminates CNInfo pagination. It
does not replace the HTTP client, alter runner decisions, or retry the JobRun.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Any, cast
from zoneinfo import ZoneInfo

from scripts.freeze_p4_1_v2_1_migration_evidence import (
    EXPECTED_PROCESS_SETTINGS,
    _parse_env_declarations,
)

from alphapilot.core.config import get_settings
from alphapilot.futu.client import PERMANENTLY_BLOCKED_METHODS
from alphapilot.jobs import news_poll

JsonObject = dict[str, Any]
PROJECT_DIR = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")
RECEIPT_PATH = (
    PROJECT_DIR
    / "docs/phase4/reports/"
    "P4.1-v2.1-standard-incremental-validation-authorization-20260809.json"
)
OUTPUT_PATH = (
    PROJECT_DIR
    / "docs/phase4/reports/"
    "P4.1-v2.1-standard-incremental-validation-runtime-capture-20260809.json"
)
EXPECTED_RECEIPT_SHA256 = (
    "b605dd90fbd9a47439d9e1415c02d02a97f3f859c8f07ea0e8ab9d7a319372f1"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strict_json_object(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _load_receipt() -> JsonObject:
    loaded: object = json.loads(
        RECEIPT_PATH.read_bytes(), object_pairs_hook=_strict_json_object
    )
    if not isinstance(loaded, dict):
        raise RuntimeError("authorization receipt must be an object")
    return cast(JsonObject, loaded)


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, (date, datetime)) else None


class PaginationTrace:
    """Capture bounded locals only when the reviewed terminal lines execute."""

    def __init__(self) -> None:
        target = news_poll._fetch_cninfo_v2_1
        lines, start_line = inspect.getsourcelines(target)
        complete_lines = [
            start_line + offset
            for offset, line in enumerate(lines)
            if line.strip() == "pagination_complete = True"
        ]
        cap_lines = [
            start_line + offset
            for offset, line in enumerate(lines)
            if line.strip() == "page_cap_hit = True"
        ]
        if len(complete_lines) != 1 or len(cap_lines) != 1:
            raise RuntimeError("reviewed pagination terminal-line shape drifted")
        self._target_code = target.__code__
        self._complete_line = complete_lines[0]
        self._cap_line = cap_lines[0]
        self.events: list[JsonObject] = []

    def global_trace(self, frame: FrameType, event: str, _arg: object) -> Any:
        if event == "call" and frame.f_code is self._target_code:
            return self.local_trace
        return None

    def local_trace(self, frame: FrameType, event: str, _arg: object) -> Any:
        if event != "line":
            return self.local_trace
        if frame.f_lineno == self._complete_line:
            local = frame.f_locals
            rows = local.get("rows")
            payload = local.get("payload")
            page_times = local.get("page_times")
            source = local.get("source")
            row_count = len(rows) if isinstance(rows, list) else None
            page_size = (
                int(source["page_size"])
                if isinstance(source, Mapping) and "page_size" in source
                else None
            )
            has_more = payload.get("hasMore") if isinstance(payload, Mapping) else None
            floor_reached = local.get("current_floor_reached") is True
            conditions = {
                "empty_page": row_count == 0,
                "has_more_false": has_more is False,
                "short_page": (
                    row_count is not None
                    and page_size is not None
                    and row_count < page_size
                ),
                "incremental_floor_reached": floor_reached,
            }
            times = (
                [item for item in page_times if isinstance(item, datetime)]
                if isinstance(page_times, list)
                else []
            )
            self.events.append(
                {
                    "event": "pagination_complete_branch",
                    "date_shanghai": _iso(local.get("slice_date")),
                    "page": local.get("page"),
                    "page_count": local.get("page_count"),
                    "raw_row_count": row_count,
                    "page_size": page_size,
                    "has_more_value": (
                        has_more
                        if isinstance(has_more, (str, int, float, bool, type(None)))
                        else type(has_more).__name__
                    ),
                    "incremental_floor_utc": _iso(
                        local.get("incremental_floor")
                    ),
                    "floor_reached": floor_reached,
                    "final_page_min_utc": _iso(min(times)) if times else None,
                    "final_page_max_utc": _iso(max(times)) if times else None,
                    "true_stop_conditions_in_source_order": [
                        key for key, value in conditions.items() if value
                    ],
                    "pagination_exhaustion_condition_reached": any(
                        conditions[key]
                        for key in ("empty_page", "has_more_false", "short_page")
                    ),
                    "source_line": frame.f_lineno,
                }
            )
        elif frame.f_lineno == self._cap_line:
            self.events.append(
                {
                    "event": "page_cap_branch",
                    "date_shanghai": _iso(frame.f_locals.get("slice_date")),
                    "page": frame.f_locals.get("page"),
                    "page_count": frame.f_locals.get("page_count"),
                    "source_line": frame.f_lineno,
                }
            )
        return self.local_trace


def _database_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise RuntimeError("single-run wrapper requires the reviewed SQLite database")
    path = Path(database_url.removeprefix(prefix))
    return path.resolve() if path.is_absolute() else (PROJECT_DIR / path).resolve()


def _read_only_database_preflight(
    database: Path, authorization_id: str
) -> JsonObject:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    running = [
        dict(row)
        for row in connection.execute(
            "SELECT id, job_name, status, started_at FROM job_runs "
            "WHERE status = 'running' ORDER BY id"
        )
    ]
    receipt_uses = int(
        connection.execute(
            "SELECT COUNT(*) FROM job_runs WHERE "
            "json_extract(stats, "
            "'$.execution_authorization.authorization_id') = ?",
            (authorization_id,),
        ).fetchone()[0]
    )
    result: JsonObject = {
        "max_job_run_id": connection.execute(
            "SELECT MAX(id) FROM job_runs"
        ).fetchone()[0],
        "running_job_runs": running,
        "receipt_uses": receipt_uses,
        "news_items": connection.execute(
            "SELECT COUNT(*) FROM news_items"
        ).fetchone()[0],
        "cninfo_items": connection.execute(
            "SELECT COUNT(*) FROM news_items WHERE source = 'cninfo'"
        ).fetchone()[0],
        "trade_proposal_ids": [
            row[0]
            for row in connection.execute(
                "SELECT id FROM trade_proposals ORDER BY id"
            )
        ],
        "broker_order_ids": [
            row[0]
            for row in connection.execute("SELECT id FROM broker_orders ORDER BY id")
        ],
        "non_simulate_broker_orders": connection.execute(
            "SELECT COUNT(*) FROM broker_orders "
            "WHERE environment IS NULL OR environment != 'SIMULATE'"
        ).fetchone()[0],
        "sqlite_query_only": connection.execute("PRAGMA query_only").fetchone()[0],
        "sqlite_total_changes": connection.total_changes,
    }
    connection.close()
    return result


def _scheduler_unloaded() -> bool:
    result = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/com.alphapilot.scheduler"],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.returncode != 0 and "Could not find service" in (
        result.stdout + result.stderr
    )


def _process_safety() -> JsonObject:
    settings = get_settings()
    return {
        "trading_mode": settings.trading_mode,
        "live_trading_enabled": settings.live_trading_enabled,
        "paper_trading_enabled": settings.paper_trading_enabled,
        "paper_auto_trading_enabled": settings.paper_auto_trading_enabled,
        "futu_enable_account_mutation": settings.futu_enable_account_mutation,
        "futu_enable_trade": settings.futu_enable_trade,
        "unlock_trade_permanently_blocked": (
            "unlock_trade" in PERMANENTLY_BLOCKED_METHODS
        ),
    }


def _write_new_json(path: Path, value: Mapping[str, object]) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)


def _expected_slices(receipt: Mapping[str, object]) -> Sequence[str]:
    authorized_round = receipt.get("authorized_round")
    if not isinstance(authorized_round, Mapping):
        raise RuntimeError("receipt authorized_round binding is missing")
    slices = authorized_round.get("expected_slice_dates_shanghai")
    if not isinstance(slices, list) or not all(isinstance(item, str) for item in slices):
        raise RuntimeError("receipt expected slices are invalid")
    return cast(list[str], slices)


def main() -> None:
    if OUTPUT_PATH.exists() or OUTPUT_PATH.is_symlink():
        raise RuntimeError("create-only runtime capture path already exists")
    receipt = _load_receipt()
    receipt_sha = _sha256(RECEIPT_PATH)
    if receipt_sha != EXPECTED_RECEIPT_SHA256:
        raise RuntimeError("authorization receipt SHA-256 drifted")
    config = news_poll.load_news_poll_config(news_poll.V2_1_CONFIG_PATH)
    source_path = PROJECT_DIR / "src/alphapilot/jobs/news_poll.py"
    source_sha = _sha256(source_path)
    review_basis = receipt.get("review_basis")
    if not isinstance(review_basis, Mapping) or source_sha != review_basis.get(
        "validated_executed_source_sha256"
    ):
        raise RuntimeError("reviewed execution source SHA-256 drifted")
    if config.sha256 != receipt.get("config_sha256"):
        raise RuntimeError("receipt/config SHA-256 binding drifted")
    news_poll._load_v2_1_manual_authorization(
        RECEIPT_PATH,
        config=config,
        execution_mode="standard_incremental_validation",
    )
    if news_poll.V2_1_SCHEDULER_ACTIVATED:
        raise RuntimeError("v2.1 scheduler gate is unexpectedly active")
    if not _scheduler_unloaded():
        raise RuntimeError("scheduler LaunchAgent is not provably unloaded")

    settings = get_settings()
    database = _database_path(settings.database_url)
    persisted, env_sha, env_mtime_ns = _parse_env_declarations(PROJECT_DIR / ".env")
    process_safety = _process_safety()
    if persisted != {
        key: EXPECTED_PROCESS_SETTINGS[key]
        for key in persisted
    } or process_safety != EXPECTED_PROCESS_SETTINGS:
        raise RuntimeError("persisted or process safety preflight failed")
    db_before = _read_only_database_preflight(
        database, str(receipt.get("authorization_id"))
    )
    if db_before["running_job_runs"] or db_before["receipt_uses"] != 0:
        raise RuntimeError("running JobRun or reused authorization receipt detected")
    if (
        len(cast(list[object], db_before["trade_proposal_ids"])) != 1
        or len(cast(list[object], db_before["broker_order_ids"])) != 1
        or db_before["non_simulate_broker_orders"] != 0
    ):
        raise RuntimeError("trading table safety preflight failed")

    preflight_at = datetime.now(UTC)
    seed = news_poll._last_committed_daily_checkpoint(config, "cninfo")
    expected_slices = list(_expected_slices(receipt))
    source = cast(dict[str, Any], config.document["sources"])["cninfo"]
    derived = news_poll._v2_1_slice_dates(
        seed,
        poll_started_at_utc=preflight_at,
        max_dates_per_run=int(source["max_dates_per_run"]),
    )
    authorized_round = cast(Mapping[str, object], receipt["authorized_round"])
    checkpoint_before = (
        seed.checkpoint_date_shanghai.isoformat()
        if seed.checkpoint_date_shanghai is not None
        else None
    )
    computed_floor = (
        seed.newest_observed_at_utc
        - timedelta(minutes=int(source["watermark_overlap_minutes"]))
        if seed.newest_observed_at_utc is not None
        else None
    )
    if (
        preflight_at.astimezone(SHANGHAI).date().isoformat() != "2026-08-09"
        or [item.isoformat() for item in derived] != expected_slices
        or checkpoint_before
        != authorized_round.get("expected_checkpoint_date_shanghai_before")
    ):
        raise RuntimeError("CST deadline, slice, or checkpoint binding drifted")

    tracer = PaginationTrace()
    execution_started_at = datetime.now(UTC)
    news_poll.register_news_poll_job()
    sys.settrace(tracer.global_trace)
    try:
        row = news_poll.run_news_poll_v2_1_incremental_validation(
            authorization_receipt_path=RECEIPT_PATH
        )
    finally:
        sys.settrace(None)
    execution_finished_at = datetime.now(UTC)

    stats = row.stats if isinstance(row.stats, dict) else {}
    sources = stats.get("sources")
    cninfo = sources.get("cninfo") if isinstance(sources, dict) else None
    db_after = _read_only_database_preflight(
        database, str(receipt.get("authorization_id"))
    )
    capture: JsonObject = {
        "schema_version": "p4.1-news-poll-v2.1-incremental-runtime-capture-v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "observation_method": {
            "type": "python_read_only_line_trace",
            "decision_source_modified": False,
            "http_client_replaced": False,
            "retry_loop_present": False,
            "captured_source_lines": {
                "pagination_complete": tracer._complete_line,
                "page_cap_hit": tracer._cap_line,
            },
        },
        "bindings": {
            "repository_head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=PROJECT_DIR, text=True
            ).strip(),
            "executed_source_path": "src/alphapilot/jobs/news_poll.py",
            "executed_source_sha256_before": source_sha,
            "executed_source_sha256_after": _sha256(source_path),
            "config_path": "config/p4_news_poll_v2_1.yaml",
            "config_sha256": config.sha256,
            "authorization_receipt_path": str(RECEIPT_PATH.relative_to(PROJECT_DIR)),
            "authorization_receipt_sha256": receipt_sha,
            "authorization_id": receipt.get("authorization_id"),
            "expected_slice_dates_shanghai": expected_slices,
            "observed_slice_dates_shanghai": (
                cninfo.get("slice_dates_shanghai")
                if isinstance(cninfo, dict)
                else None
            ),
            "expected_checkpoint_before": authorized_round.get(
                "expected_checkpoint_date_shanghai_before"
            ),
            "expected_checkpoint_after": authorized_round.get(
                "expected_checkpoint_date_shanghai_after"
            ),
        },
        "preflight": {
            "at_utc": preflight_at.isoformat(),
            "at_shanghai": preflight_at.astimezone(SHANGHAI).isoformat(),
            "checkpoint_before": checkpoint_before,
            "observed_high_before_utc": _iso(seed.newest_observed_at_utc),
            "computed_incremental_floor_utc": _iso(computed_floor),
            "derived_slice_dates_shanghai": [item.isoformat() for item in derived],
            "persisted_safety": persisted,
            "process_safety": process_safety,
            "env_sha256": env_sha,
            "env_mtime_ns": env_mtime_ns,
            "scheduler_launchagent_loaded": False,
            "database": db_before,
        },
        "execution": {
            "started_at_utc": execution_started_at.isoformat(),
            "finished_at_utc": execution_finished_at.isoformat(),
            "job_run_id": row.id,
            "status": row.status,
            "error": row.error,
            "execution_mode": stats.get("execution_mode"),
            "source_failures": stats.get("source_failures"),
            "terminal_diagnostics": stats.get("terminal_diagnostics"),
            "pagination_terminal_events": tracer.events,
            "cninfo_slices": (
                cninfo.get("slices") if isinstance(cninfo, dict) else None
            ),
            "cninfo_daily_checkpoint": (
                cninfo.get("daily_checkpoint") if isinstance(cninfo, dict) else None
            ),
            "totals": stats.get("totals"),
            "safety_before": stats.get("safety_before"),
            "safety_after": stats.get("safety_after"),
            "safety_unchanged": stats.get("safety_unchanged"),
        },
        "postflight": {
            "database": db_after,
            "receipt_use_delta": db_after["receipt_uses"] - db_before["receipt_uses"],
            "job_run_id_delta": row.id - int(db_before["max_job_run_id"]),
        },
    }
    _write_new_json(OUTPUT_PATH, capture)
    print(
        json.dumps(
            {
                "capture_path": str(OUTPUT_PATH),
                "capture_sha256": _sha256(OUTPUT_PATH),
                "job_run_id": row.id,
                "status": row.status,
                "error": row.error,
                "pagination_terminal_events": tracer.events,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    raise SystemExit(0 if row.status == "ok" else 1)


if __name__ == "__main__":
    main()
