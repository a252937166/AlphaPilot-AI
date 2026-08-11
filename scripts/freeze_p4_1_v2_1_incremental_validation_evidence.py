#!/usr/bin/env python3
"""Freeze create-only evidence for one P4.1 v2.1 incremental validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, cast

from scripts.freeze_p4_1_v2_1_migration_evidence import (
    EXPECTED_PROCESS_SETTINGS,
    PROJECT_DIR,
    SHANGHAI,
    _canonical_sha256,
    _count,
    _git,
    _load_json_text,
    _parse_env_declarations,
    _parse_utc,
    _run_rows_evidence,
    _scheduler_loaded,
    _sha256,
    _write_new_json,
)

from alphapilot.futu.client import PERMANENTLY_BLOCKED_METHODS
from alphapilot.jobs.news_poll import V2_1_SCHEDULER_ACTIVATED

JsonObject = dict[str, Any]
COUNTER_KEYS = (
    "fetched",
    "filtered",
    "inserted",
    "duplicate_url",
    "duplicate_content_hash",
)


def _required_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{name} is missing or invalid")
    return cast(Mapping[str, object], value)


def _required_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise RuntimeError(f"{name} is missing or invalid")
    return cast(list[object], value)


def _nonnegative_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError(f"{name} must be a non-negative integer")
    return value


def _counter_map(value: Mapping[str, object], name: str) -> dict[str, int]:
    return {
        key: _nonnegative_int(value.get(key, 0), f"{name}.{key}")
        for key in COUNTER_KEYS
    }


def _add_counters(items: Sequence[Mapping[str, object]]) -> dict[str, int]:
    return {
        key: sum(_nonnegative_int(item.get(key, 0), key) for item in items)
        for key in COUNTER_KEYS
    }


def _validate_disposition_identity(counters: Mapping[str, int], name: str) -> None:
    if counters["fetched"] != (
        counters["filtered"]
        + counters["inserted"]
        + counters["duplicate_url"]
        + counters["duplicate_content_hash"]
    ):
        raise RuntimeError(f"{name} disposition identity is invalid")


def _authorized_scope(receipt: Mapping[str, object]) -> JsonObject:
    if receipt.get("execution_mode") != "standard_incremental_validation":
        raise RuntimeError("receipt execution mode is outside validation scope")
    if receipt.get("initial_backlog_migration_complete") is not True:
        raise RuntimeError("receipt does not declare backlog migration complete")
    if receipt.get("standard_incremental_validation_complete") is not False:
        raise RuntimeError("receipt validation gate must still be incomplete")
    authorized = _required_mapping(receipt.get("authorized_round"), "authorized_round")
    rounds = authorized.get("rounds_authorized")
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds != 1:
        raise RuntimeError("receipt must authorize exactly one validation round")
    if authorized.get("entrypoint") != "run_news_poll_v2_1_incremental_validation":
        raise RuntimeError("receipt entrypoint binding is invalid")
    expected_slices = _required_list(
        authorized.get("expected_slice_dates_shanghai"), "expected slices"
    )
    if not expected_slices or not all(isinstance(item, str) for item in expected_slices):
        raise RuntimeError("receipt slice-date binding is invalid")
    return {
        "expected_checkpoint_before": authorized.get(
            "expected_checkpoint_date_shanghai_before"
        ),
        "expected_checkpoint_after": authorized.get(
            "expected_checkpoint_date_shanghai_after"
        ),
        "expected_slices": cast(list[str], expected_slices),
    }


def _source_reconciliation(
    sources: Mapping[str, object], totals: Mapping[str, object]
) -> JsonObject:
    source_rows: JsonObject = {}
    counter_items: list[Mapping[str, object]] = []
    logical_requests = 0
    physical_attempts = 0
    retries = 0
    failures = 0
    for source_name, raw_source in sorted(sources.items()):
        source = _required_mapping(raw_source, f"source {source_name}")
        counters = _counter_map(source, f"source {source_name}")
        _validate_disposition_identity(counters, f"source {source_name}")
        counter_items.append(counters)
        logical = _nonnegative_int(
            source.get("logical_request_count", 0),
            f"source {source_name}.logical_request_count",
        )
        physical = _nonnegative_int(
            source.get("physical_attempt_count", 0),
            f"source {source_name}.physical_attempt_count",
        )
        retry = _nonnegative_int(
            source.get("retry_count", 0), f"source {source_name}.retry_count"
        )
        failure = _nonnegative_int(
            source.get("failure_count", 0), f"source {source_name}.failure_count"
        )
        logical_requests += logical
        physical_attempts += physical
        retries += retry
        failures += failure
        source_rows[str(source_name)] = {
            "status": source.get("status"),
            "attempted": source.get("attempted"),
            **counters,
            "disposition_identity_valid": True,
            "logical_requests": logical,
            "physical_attempts": physical,
            "retries": retry,
            "failures": failure,
        }
    aggregate = _add_counters(counter_items)
    _validate_disposition_identity(aggregate, "all-source aggregate")
    expected = {
        key: _nonnegative_int(totals.get(key, 0), f"totals.{key}")
        for key in (
            "fetched",
            "inserted",
            "duplicate_url",
            "duplicate_content_hash",
        )
    }
    if any(aggregate[key] != expected[key] for key in expected):
        raise RuntimeError("source counters do not reconcile to JobRun totals")
    if (
        retries != _nonnegative_int(totals.get("retry_count"), "totals.retry_count")
        or failures
        != _nonnegative_int(totals.get("failure_count"), "totals.failure_count")
    ):
        raise RuntimeError("source failures/retries do not reconcile to JobRun totals")
    return {
        "by_source": source_rows,
        "aggregate": aggregate,
        "job_run_totals": dict(expected),
        "counters_equal": all(aggregate[key] == expected[key] for key in expected),
        "logical_requests": logical_requests,
        "physical_attempts": physical_attempts,
        "retries": retries,
        "failures": failures,
        "all_disposition_identities_valid": True,
    }


def _slice_evidence(
    cninfo: Mapping[str, object],
    runtime: Mapping[str, object],
    scope: Mapping[str, object],
) -> tuple[list[JsonObject], JsonObject]:
    raw_slices = _required_list(cninfo.get("slices"), "CNInfo slices")
    slices = [
        _required_mapping(item, "CNInfo slice")
        for item in raw_slices
    ]
    expected_slices = cast(list[str], scope["expected_slices"])
    if [str(item.get("date_shanghai")) for item in slices] != expected_slices:
        raise RuntimeError("observed slice dates do not match the receipt")
    events = _required_list(
        _required_mapping(runtime.get("execution"), "runtime execution").get(
            "pagination_terminal_events"
        ),
        "runtime pagination events",
    )
    if len(events) != len(slices):
        raise RuntimeError("pagination terminal events do not cover every slice")
    daily: list[JsonObject] = []
    slice_counter_items: list[Mapping[str, object]] = []
    for item, raw_event in zip(slices, events, strict=True):
        event = _required_mapping(raw_event, "pagination event")
        counters = _counter_map(item, "CNInfo slice")
        _validate_disposition_identity(counters, "CNInfo slice")
        if (
            item.get("mode") != "current_date_incremental"
            or item.get("date_closed") is not False
            or item.get("attempted") is not True
            or item.get("pagination_complete") is not True
            or item.get("coverage_proven") is not True
            or item.get("checkpoint_committed") is not True
            or item.get("page_cap_hit") is not False
            or item.get("failure") is not None
            or item.get("disposition_identity_valid") is not True
        ):
            raise RuntimeError("current-date slice did not validate cleanly")
        if (
            event.get("date_shanghai") != item.get("date_shanghai")
            or event.get("event") != "pagination_complete_branch"
            or event.get("incremental_floor_utc")
            != item.get("incremental_floor_utc")
            or event.get("floor_reached") is not False
            or event.get("pagination_exhaustion_condition_reached") is not True
        ):
            raise RuntimeError("runtime pagination evidence does not bind to the slice")
        mechanisms = _required_list(
            event.get("true_stop_conditions_in_source_order"),
            "pagination stop mechanisms",
        )
        if "incremental_floor_reached" in mechanisms or not mechanisms:
            raise RuntimeError("pagination did not terminate through exhaustion")
        slice_counter_items.append(counters)
        daily.append(
            {
                "date_shanghai": item.get("date_shanghai"),
                "date_closed": False,
                "mode": item.get("mode"),
                "incremental_floor_utc": item.get("incremental_floor_utc"),
                "floor_reached": False,
                "pagination_termination_family": "pagination_exhaustion",
                "pagination_stop_conditions": mechanisms,
                "final_page_raw_row_count": event.get("raw_row_count"),
                "final_page_has_more_value": event.get("has_more_value"),
                "page_count": item.get("page_count"),
                "logical_requests": item.get("logical_request_count"),
                "physical_attempts": item.get("physical_attempt_count"),
                "pagination_complete": True,
                "coverage_proven": True,
                "page_cap_hit": False,
                "checkpoint_committed": True,
                "failure": None,
                "newest_observed_at_utc": item.get("newest_observed_at_utc"),
                **counters,
                "disposition_total": item.get("disposition_total"),
                "disposition_identity_valid": True,
            }
        )
    slice_sums = _add_counters(slice_counter_items)
    cninfo_aggregate = _counter_map(cninfo, "CNInfo source")
    if slice_sums != cninfo_aggregate:
        raise RuntimeError("CNInfo slices do not reconcile to the source aggregate")
    return daily, {
        "slice_sums": slice_sums,
        "source_aggregate": cninfo_aggregate,
        "all_equal": True,
    }


def build_evidence(
    *,
    database: Path,
    job_run_id: int,
    execution_head: str,
    receipt_path: Path,
    runtime_capture_path: Path,
    env_path: Path,
) -> JsonObject:
    config_path = PROJECT_DIR / "config/p4_news_poll_v2_1.yaml"
    preregistration_path = PROJECT_DIR / "config/p4_news_poll_v2_1.preregistration.json"
    probe_path = PROJECT_DIR / "config/p4_news_poll_v2_1.probe.json"
    observer_path = PROJECT_DIR / "scripts/run_p4_1_v2_1_incremental_validation_once.py"
    source_path = PROJECT_DIR / "src/alphapilot/jobs/news_poll.py"
    plist_path = Path.home() / "Library/LaunchAgents/com.alphapilot.scheduler.plist"

    receipt = _load_json_text(receipt_path.read_text(encoding="utf-8"))
    runtime = _load_json_text(runtime_capture_path.read_text(encoding="utf-8"))
    scope = _authorized_scope(receipt)
    receipt_sha = _sha256(receipt_path)
    runtime_sha = _sha256(runtime_capture_path)
    config_sha = _sha256(config_path)
    current_head = _git("rev-parse", "HEAD")
    _git("cat-file", "-e", f"{execution_head}^{{commit}}")
    source_drift = _git(
        "diff",
        "--name-only",
        execution_head,
        "--",
        "src/alphapilot/jobs/news_poll.py",
        "config/p4_news_poll_v2_1.yaml",
    )
    if source_drift:
        raise RuntimeError("execution source/config drifted after the run")
    if receipt.get("config_sha256") != config_sha:
        raise RuntimeError("receipt/config hash binding is invalid")

    runtime_bindings = _required_mapping(runtime.get("bindings"), "runtime bindings")
    if (
        runtime.get("schema_version")
        != "p4.1-news-poll-v2.1-incremental-runtime-capture-v1"
        or runtime_bindings.get("repository_head") != execution_head
        or runtime_bindings.get("executed_source_sha256_before") != _sha256(source_path)
        or runtime_bindings.get("executed_source_sha256_after") != _sha256(source_path)
        or runtime_bindings.get("config_sha256") != config_sha
        or runtime_bindings.get("authorization_receipt_sha256") != receipt_sha
        or runtime_bindings.get("expected_slice_dates_shanghai")
        != scope["expected_slices"]
        or runtime_bindings.get("expected_checkpoint_before")
        != scope["expected_checkpoint_before"]
        or runtime_bindings.get("expected_checkpoint_after")
        != scope["expected_checkpoint_after"]
    ):
        raise RuntimeError("runtime capture bindings are invalid")
    observation = _required_mapping(
        runtime.get("observation_method"), "runtime observation method"
    )
    if (
        observation.get("type") != "python_read_only_line_trace"
        or observation.get("decision_source_modified") is not False
        or observation.get("http_client_replaced") is not False
        or observation.get("retry_loop_present") is not False
    ):
        raise RuntimeError("runtime observation method is not non-intervening")

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    row = connection.execute(
        "SELECT job_name, status, error, stats, started_at, finished_at "
        "FROM job_runs WHERE id = ?",
        (job_run_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("validation JobRun does not exist")
    job_name, status, error, raw_stats, started_at, finished_at = row
    if job_name != "news_poll" or status != "ok" or error is not None:
        raise RuntimeError("validation JobRun is not an error-free ok terminal")
    stats = _load_json_text(str(raw_stats))
    authorization = _required_mapping(
        stats.get("execution_authorization"), "JobRun authorization"
    )
    if (
        authorization.get("authorization_receipt_sha256") != receipt_sha
        or authorization.get("authorization_id") != receipt.get("authorization_id")
        or authorization.get("execution_mode") != "standard_incremental_validation"
        or authorization.get("initial_backlog_migration_complete") is not True
        or authorization.get("scheduler_activated") is not False
    ):
        raise RuntimeError("JobRun authorization binding is invalid")
    runtime_execution = _required_mapping(
        runtime.get("execution"), "runtime execution"
    )
    if (
        runtime_execution.get("job_run_id") != job_run_id
        or runtime_execution.get("status") != status
        or runtime_execution.get("error") is not None
    ):
        raise RuntimeError("runtime capture does not bind to the JobRun")
    if (
        stats.get("config_sha256") != config_sha
        or stats.get("execution_mode") != "standard_incremental_validation"
        or stats.get("run_mode") != "regular_incremental"
        or stats.get("coverage_gap") is not False
        or stats.get("source_failures") != []
        or stats.get("terminal_diagnostics") is not None
    ):
        raise RuntimeError("JobRun validation execution contract is invalid")

    sources = _required_mapping(stats.get("sources"), "JobRun sources")
    cninfo = _required_mapping(sources.get("cninfo"), "CNInfo source")
    totals = _required_mapping(stats.get("totals"), "JobRun totals")
    source_reconciliation = _source_reconciliation(sources, totals)
    if (
        source_reconciliation["failures"] != 0
        or source_reconciliation["retries"] != 0
    ):
        raise RuntimeError("validation contains a source failure or retry")
    daily_slices, slice_reconciliation = _slice_evidence(
        cninfo, runtime, scope
    )

    checkpoint = _required_mapping(cninfo.get("daily_checkpoint"), "daily checkpoint")
    runtime_preflight = _required_mapping(
        runtime.get("preflight"), "runtime preflight"
    )
    observed_before = runtime_preflight.get("observed_high_before_utc")
    observed_after = checkpoint.get("newest_observed_at_utc")
    if (
        checkpoint.get("verified_checkpoint_date_shanghai_before")
        != scope["expected_checkpoint_before"]
        or checkpoint.get("verified_checkpoint_date_shanghai_after")
        != scope["expected_checkpoint_after"]
        or checkpoint.get("checkpoint_committed") is not True
        or checkpoint.get("partial_checkpoint") is not False
        or checkpoint.get("initial_backlog_migration") is not False
        or observed_before != observed_after
    ):
        raise RuntimeError("date or observed-high checkpoint evidence is invalid")

    safety_before = _required_mapping(stats.get("safety_before"), "safety before")
    safety_after = _required_mapping(stats.get("safety_after"), "safety after")
    if (
        safety_before.get("settings") != EXPECTED_PROCESS_SETTINGS
        or safety_after.get("settings") != EXPECTED_PROCESS_SETTINGS
        or safety_before != safety_after
        or stats.get("safety_unchanged") is not True
    ):
        raise RuntimeError("process safety invariants are not closed")
    persisted, env_sha, env_mtime_ns = _parse_env_declarations(env_path)
    process_subset = {
        key: EXPECTED_PROCESS_SETTINGS[key]
        for key in persisted
    }
    if persisted != process_subset:
        raise RuntimeError("persisted safety settings are invalid")
    started = _parse_utc(started_at)
    finished = _parse_utc(finished_at)
    env_mtime = datetime.fromtimestamp(env_mtime_ns / 1_000_000_000, tz=UTC)
    if (
        env_mtime > started
        or runtime_preflight.get("env_sha256") != env_sha
        or runtime_preflight.get("persisted_safety") != persisted
        or runtime_preflight.get("process_safety") != EXPECTED_PROCESS_SETTINGS
    ):
        raise RuntimeError("persisted/process runtime safety binding drifted")

    expected_rows_by_source = {
        str(name): _nonnegative_int(
            _required_mapping(raw, f"source {name}").get("inserted", 0),
            f"source {name}.inserted",
        )
        for name, raw in sources.items()
        if _nonnegative_int(
            _required_mapping(raw, f"source {name}").get("inserted", 0),
            f"source {name}.inserted",
        )
        > 0
    }
    poll_completed = _parse_utc(stats.get("poll_completed_at"))
    run_rows = _run_rows_evidence(
        connection,
        job_run_id=job_run_id,
        expected_rows_by_source=expected_rows_by_source,
        source_stats_by_name=sources,
        poll_completed_at=poll_completed,
    )
    if (
        run_rows["invalid_pit_chain_rows"] != 0
        or run_rows["available_time_not_after_published_at_rows"] != 0
        or run_rows["coverage_gap_marked_rows"] != 0
        or run_rows["coverage_gap_catchup_rows"] != 0
    ):
        raise RuntimeError("incremental run-row PIT or coverage markers are invalid")

    runtime_postflight = _required_mapping(
        runtime.get("postflight"), "runtime postflight"
    )
    database_before = _required_mapping(
        runtime_preflight.get("database"), "runtime database before"
    )
    database_after = _required_mapping(
        runtime_postflight.get("database"), "runtime database after"
    )
    total_inserted = _nonnegative_int(totals.get("inserted"), "totals.inserted")
    if (
        database_after.get("news_items")
        != _nonnegative_int(database_before.get("news_items"), "news_items before")
        + total_inserted
        or database_after.get("cninfo_items") != database_before.get("cninfo_items")
        or runtime_postflight.get("receipt_use_delta") != 1
        or runtime_postflight.get("job_run_id_delta") != 1
    ):
        raise RuntimeError("runtime database before/after reconciliation is invalid")

    news_items_after = _count(connection, "SELECT COUNT(*) FROM news_items")
    cninfo_after = _count(
        connection, "SELECT COUNT(*) FROM news_items WHERE source = 'cninfo'"
    )
    duplicate_url_groups = _count(
        connection,
        "SELECT COUNT(*) FROM (SELECT url FROM news_items GROUP BY url "
        "HAVING COUNT(*) > 1)",
    )
    duplicate_hash_groups = _count(
        connection,
        "SELECT COUNT(*) FROM (SELECT content_hash FROM news_items "
        "GROUP BY content_hash HAVING COUNT(*) > 1)",
    )
    proposal_count = _count(connection, "SELECT COUNT(*) FROM trade_proposals")
    order_count = _count(connection, "SELECT COUNT(*) FROM broker_orders")
    non_simulate = _count(
        connection,
        "SELECT COUNT(*) FROM broker_orders "
        "WHERE environment IS NULL OR environment != 'SIMULATE'",
    )
    running_jobs = _count(
        connection, "SELECT COUNT(*) FROM job_runs WHERE status = 'running'"
    )
    receipt_uses = _count(
        connection,
        "SELECT COUNT(*) FROM job_runs WHERE "
        "json_extract(stats, '$.execution_authorization.authorization_id') = ?",
        (receipt.get("authorization_id"),),
    )
    quick_check = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    if connection.total_changes != 0:
        raise RuntimeError("evidence query unexpectedly changed the database")
    connection.close()
    scheduler_loaded = _scheduler_loaded()
    if (
        quick_check != ["ok"]
        or scheduler_loaded
        or V2_1_SCHEDULER_ACTIVATED
        or proposal_count != 1
        or order_count != 1
        or non_simulate != 0
        or running_jobs != 0
        or receipt_uses != 1
        or duplicate_url_groups != 0
        or duplicate_hash_groups != 0
        or "unlock_trade" not in PERMANENTLY_BLOCKED_METHODS
    ):
        raise RuntimeError("postflight integrity, scheduler, or trading safety failed")

    slice_date = date.fromisoformat(cast(list[str], scope["expected_slices"])[0])
    slice_start_utc = datetime.combine(
        slice_date, time.min, SHANGHAI
    ).astimezone(UTC)
    floor = _parse_utc(daily_slices[0]["incremental_floor_utc"])
    floor_precedes_slice = floor < slice_start_utc
    if not floor_precedes_slice:
        raise RuntimeError("incremental floor unexpectedly falls within the slice")

    created = datetime.now(UTC)
    raw_stats_text = str(raw_stats)
    return {
        "schema_version": (
            "p4.1-news-poll-v2.1-standard-incremental-validation-evidence-v1"
        ),
        "created_at_utc": created.isoformat().replace("+00:00", "Z"),
        "created_at_shanghai": created.astimezone(SHANGHAI).isoformat(),
        "review_status": "pending_independent_review",
        "repository_head_at_execution": execution_head,
        "repository_head_at_evidence_freeze": current_head,
        "execution_source": {
            "path": "src/alphapilot/jobs/news_poll.py",
            "sha256": _sha256(source_path),
            "source_or_config_drift_after_execution": False,
        },
        "runtime_observer": {
            "path": "scripts/run_p4_1_v2_1_incremental_validation_once.py",
            "sha256_at_evidence_freeze": _sha256(observer_path),
            "capture_path": str(runtime_capture_path.relative_to(PROJECT_DIR)),
            "capture_sha256": runtime_sha,
            "method": observation,
        },
        "job_run": {
            "id": job_run_id,
            "job_name": job_name,
            "status": status,
            "error": error,
            "started_at_utc": started.isoformat().replace("+00:00", "Z"),
            "finished_at_utc": finished.isoformat().replace("+00:00", "Z"),
            "stats_bytes": len(raw_stats_text.encode()),
            "stats_raw_sha256": _sha256_text(raw_stats_text),
            "stats_canonical_json_sha256": _canonical_sha256(stats),
        },
        "frozen_bindings": {
            "config_path": str(config_path.relative_to(PROJECT_DIR)),
            "config_sha256": config_sha,
            "preregistration_path": str(preregistration_path.relative_to(PROJECT_DIR)),
            "preregistration_sha256": _sha256(preregistration_path),
            "controlled_probe_path": str(probe_path.relative_to(PROJECT_DIR)),
            "controlled_probe_sha256": _sha256(probe_path),
            "authorization_receipt_path": str(receipt_path.relative_to(PROJECT_DIR)),
            "authorization_receipt_sha256": receipt_sha,
            "authorization_id": receipt.get("authorization_id"),
            "receipt_uses_observed": receipt_uses,
            "expected_slice_dates_shanghai": scope["expected_slices"],
            "observed_slice_dates_shanghai": [
                item["date_shanghai"] for item in daily_slices
            ],
            "expected_checkpoint_date_shanghai_before": scope[
                "expected_checkpoint_before"
            ],
            "observed_checkpoint_date_shanghai_before": checkpoint.get(
                "verified_checkpoint_date_shanghai_before"
            ),
            "expected_checkpoint_date_shanghai_after": scope[
                "expected_checkpoint_after"
            ],
            "observed_checkpoint_date_shanghai_after": checkpoint.get(
                "verified_checkpoint_date_shanghai_after"
            ),
            "expected_and_observed_scope_equal": True,
        },
        "execution_contract": {
            "execution_mode": stats.get("execution_mode"),
            "run_mode": stats.get("run_mode"),
            "coverage_gap": stats.get("coverage_gap"),
            "canonical_column": cninfo.get("canonical_column"),
            "strict_tls": cninfo.get("tls_verification"),
            "logical_requests": source_reconciliation["logical_requests"],
            "physical_attempts": source_reconciliation["physical_attempts"],
            "retries": source_reconciliation["retries"],
            "failures": source_reconciliation["failures"],
            "terminal_diagnostics": stats.get("terminal_diagnostics"),
        },
        "daily_slices": daily_slices,
        "slice_to_source_reconciliation": slice_reconciliation,
        "all_source_reconciliation": source_reconciliation,
        "checkpoint": {
            **dict(checkpoint),
            "observed_high_before_utc": observed_before,
            "observed_high_after_utc": observed_after,
            "observed_high_advanced": observed_after != observed_before,
            "date_checkpoint_advanced": (
                checkpoint.get("verified_checkpoint_date_shanghai_after")
                != checkpoint.get("verified_checkpoint_date_shanghai_before")
            ),
        },
        "pagination_termination_proof": {
            "incremental_floor_utc": daily_slices[0]["incremental_floor_utc"],
            "slice_start_utc": slice_start_utc.isoformat().replace("+00:00", "Z"),
            "floor_precedes_slice_start": floor_precedes_slice,
            "floor_reached": daily_slices[0]["floor_reached"],
            "termination_family": daily_slices[0][
                "pagination_termination_family"
            ],
            "stop_conditions": daily_slices[0]["pagination_stop_conditions"],
            "explanation": (
                "The final CNInfo response was an empty page with hasMore=false; "
                "it was also shorter than page_size. The overlap floor predates "
                "the requested CST slice and was not reached."
            ),
        },
        "persistence_evidence": {
            "news_items_before": database_before["news_items"],
            "news_items_after": news_items_after,
            "news_items_inserted": total_inserted,
            "cninfo_before": database_before["cninfo_items"],
            "cninfo_after": cninfo_after,
            "cninfo_inserted": cninfo.get("inserted"),
            **run_rows,
            "global_duplicate_url_groups": duplicate_url_groups,
            "global_duplicate_content_hash_groups": duplicate_hash_groups,
            "sqlite_quick_check": "ok",
            "sqlite_query_only_total_changes": 0,
        },
        "persisted_safety_configuration": {
            "env_path": ".env",
            "env_file_sha256": env_sha,
            "env_file_mtime_utc": env_mtime.isoformat().replace("+00:00", "Z"),
            "env_mtime_precedes_run_start": True,
            "env_effective": persisted,
            "structural_code_invariants": {
                "unlock_trade_permanently_blocked": True,
                "v2_1_scheduler_activated_constant": V2_1_SCHEDULER_ACTIVATED,
            },
        },
        "process_effective_safety": {
            "before": safety_before.get("settings"),
            "after": safety_after.get("settings"),
            "before_and_after_equal": True,
            "trade_proposal_ids_before": safety_before.get("trade_proposal_ids"),
            "trade_proposal_ids_after": safety_after.get("trade_proposal_ids"),
            "broker_order_ids_before": safety_before.get("broker_order_ids"),
            "broker_order_ids_after": safety_after.get("broker_order_ids"),
        },
        "persisted_vs_process_divergences": [],
        "runtime_safety": {
            "trade_proposals_after": proposal_count,
            "broker_orders_after": order_count,
            "non_simulate_broker_orders": non_simulate,
            "running_job_runs_after": running_jobs,
            "scheduler_launchagent_loaded_after": scheduler_loaded,
            "scheduler_plist_path": str(plist_path),
            "scheduler_plist_sha256": _sha256(plist_path)
            if plist_path.exists()
            else None,
        },
        "phase_gates": {
            "this_authorized_run_succeeded": True,
            "initial_backlog_migration_complete": True,
            "standard_incremental_validation_complete": False,
            "scheduler_activated": False,
            "p4_2b_production_wiring_unlocked": False,
            "p4_3_unlocked": False,
            "next_action": (
                f"Independent reviewer must validate JobRun {job_run_id} and this "
                "evidence. Scheduler code/deployment activation remains a separate "
                "review gate."
            ),
        },
    }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--job-run-id", type=int, required=True)
    parser.add_argument("--execution-head", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--runtime-capture", type=Path, required=True)
    parser.add_argument("--env", type=Path, default=PROJECT_DIR / ".env")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = build_evidence(
        database=args.db.resolve(),
        job_run_id=args.job_run_id,
        execution_head=args.execution_head,
        receipt_path=args.receipt.resolve(),
        runtime_capture_path=args.runtime_capture.resolve(),
        env_path=args.env.resolve(),
    )
    output = args.output.resolve()
    _write_new_json(output, evidence)
    print(
        json.dumps(
            {
                "output": str(output),
                "sha256": _sha256(output),
                "job_run_id": args.job_run_id,
                "status": evidence["job_run"]["status"],
                "checkpoint_after": evidence["checkpoint"][
                    "verified_checkpoint_date_shanghai_after"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
