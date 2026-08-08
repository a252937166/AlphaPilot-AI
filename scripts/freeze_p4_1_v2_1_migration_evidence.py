#!/usr/bin/env python3
"""Freeze create-only evidence for one authorized P4.1 v2.1 migration run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from alphapilot.futu.client import PERMANENTLY_BLOCKED_METHODS
from alphapilot.jobs.news_poll import V2_1_SCHEDULER_ACTIVATED

PROJECT_DIR = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")
JsonObject = dict[str, Any]

ENV_TO_SETTING = {
    "ALPHAPILOT_TRADING_MODE": "trading_mode",
    "ALPHAPILOT_LIVE_TRADING_ENABLED": "live_trading_enabled",
    "ALPHAPILOT_PAPER_TRADING_ENABLED": "paper_trading_enabled",
    "ALPHAPILOT_PAPER_AUTO_TRADING_ENABLED": "paper_auto_trading_enabled",
    "ALPHAPILOT_FUTU_ENABLE_ACCOUNT_MUTATION": "futu_enable_account_mutation",
    "ALPHAPILOT_FUTU_ENABLE_TRADE": "futu_enable_trade",
}
EXPECTED_PROCESS_SETTINGS: JsonObject = {
    "trading_mode": "research",
    "live_trading_enabled": False,
    "paper_trading_enabled": False,
    "paper_auto_trading_enabled": False,
    "futu_enable_account_mutation": False,
    "futu_enable_trade": False,
    "unlock_trade_permanently_blocked": True,
}
SLICE_COUNTER_KEYS = (
    "fetched",
    "filtered",
    "inserted",
    "duplicate_url",
    "duplicate_content_hash",
)


def _strict_json_object(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _load_json_text(value: str) -> JsonObject:
    loaded: object = json.loads(value, object_pairs_hook=_strict_json_object)
    if not isinstance(loaded, dict):
        raise ValueError("expected a JSON object")
    return cast(JsonObject, loaded)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Mapping[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _parse_utc(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _utc_iso(value: object) -> str:
    return _parse_utc(value).isoformat().replace("+00:00", "Z")


def _parse_env_declarations(path: Path) -> tuple[JsonObject, str, int]:
    selected: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip().upper()
        if key not in ENV_TO_SETTING:
            continue
        if key in selected:
            raise ValueError(f"duplicate safety setting in .env: {key}")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        selected[key] = value.strip()
    missing = sorted(set(ENV_TO_SETTING) - set(selected))
    if missing:
        raise ValueError(f"missing persisted safety settings: {missing}")

    effective: JsonObject = {}
    for env_key, setting_key in ENV_TO_SETTING.items():
        raw_value = selected[env_key]
        if setting_key == "trading_mode":
            effective[setting_key] = raw_value
            continue
        normalized = raw_value.lower()
        if normalized not in {"true", "false"}:
            raise ValueError(f"{env_key} must be explicitly true or false")
        effective[setting_key] = normalized == "true"
    return effective, _sha256(path), path.stat().st_mtime_ns


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_DIR,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _scheduler_loaded() -> bool:
    result = subprocess.run(
        [
            "/bin/launchctl",
            "print",
            f"gui/{os.getuid()}/com.alphapilot.scheduler",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _write_new_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite migration evidence: {path}")
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode()
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
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _count(conn: sqlite3.Connection, query: str, parameters: Sequence[object] = ()) -> int:
    row = conn.execute(query, parameters).fetchone()
    if row is None:
        raise RuntimeError("count query returned no row")
    return int(row[0])


def _run_rows_evidence(
    conn: sqlite3.Connection,
    *,
    job_run_id: int,
    expected_inserted: int,
    source_stats: Mapping[str, object],
    poll_completed_at: datetime,
) -> JsonObject:
    rows = conn.execute(
        """
        SELECT source, published_at, available_time, raw_payload
        FROM news_items
        WHERE json_extract(raw_payload, '$._alphapilot_ingestion.job_run_id') = ?
        ORDER BY id
        """,
        (job_run_id,),
    ).fetchall()
    if len(rows) != expected_inserted:
        raise RuntimeError("JobRun marker row count does not match inserted")
    available_times: list[datetime] = []
    published_not_before_available = 0
    invalid_pit_chains = 0
    coverage_gap_rows = 0
    catchup_rows = 0
    sources: set[str] = set()
    for source, published_at, available_time, raw_payload in rows:
        sources.add(str(source))
        payload = _load_json_text(str(raw_payload))
        ingestion = payload.get("_alphapilot_ingestion")
        if not isinstance(ingestion, dict):
            raise RuntimeError("run row has no ingestion marker")
        fetched = _parse_utc(ingestion.get("fetched_at_utc"))
        write_lock = _parse_utc(ingestion.get("write_lock_acquired_at_utc"))
        assigned = _parse_utc(ingestion.get("available_time_assigned_at_utc"))
        stored_available = _parse_utc(available_time)
        if not (fetched <= write_lock <= assigned == stored_available):
            invalid_pit_chains += 1
        available_times.append(stored_available)
        if published_at is not None and stored_available <= _parse_utc(published_at):
            published_not_before_available += 1
        if ingestion.get("preceded_by_coverage_gap") is True:
            coverage_gap_rows += 1
        if ingestion.get("run_mode") == "coverage_gap_catchup":
            catchup_rows += 1

    flush = _parse_utc(source_stats.get("db_flush_completed_at"))
    commit = _parse_utc(source_stats.get("db_commit_completed_at"))
    last_available = max(available_times) if available_times else None
    if last_available is not None and not (
        last_available <= flush <= commit <= poll_completed_at
    ):
        raise RuntimeError("PIT flush/commit/poll chain is invalid")
    return {
        "run_marker_rows": len(rows),
        "sources": sorted(sources),
        "coverage_gap_marked_rows": coverage_gap_rows,
        "coverage_gap_catchup_rows": catchup_rows,
        "invalid_pit_chain_rows": invalid_pit_chains,
        "available_time_not_after_published_at_rows": published_not_before_available,
        "first_available_time_utc": (
            min(available_times).isoformat().replace("+00:00", "Z")
            if available_times
            else None
        ),
        "last_available_time_utc": (
            max(available_times).isoformat().replace("+00:00", "Z")
            if available_times
            else None
        ),
        "db_flush_completed_at_utc": flush.isoformat().replace("+00:00", "Z"),
        "db_commit_completed_at_utc": commit.isoformat().replace("+00:00", "Z"),
        "poll_completed_at_utc": poll_completed_at.isoformat().replace("+00:00", "Z"),
    }


def build_evidence(
    *,
    database: Path,
    job_run_id: int,
    execution_head: str,
    receipt_path: Path,
    expected_slices: Sequence[str],
    expected_checkpoint_before: str,
    expected_checkpoint_after: str,
    env_path: Path,
) -> JsonObject:
    config_path = PROJECT_DIR / "config/p4_news_poll_v2_1.yaml"
    preregistration_path = PROJECT_DIR / "config/p4_news_poll_v2_1.preregistration.json"
    probe_path = PROJECT_DIR / "config/p4_news_poll_v2_1.probe.json"
    plist_path = (
        Path.home()
        / "Library/LaunchAgents/com.alphapilot.scheduler.plist"
    )
    receipt = _load_json_text(receipt_path.read_text(encoding="utf-8"))
    receipt_sha = _sha256(receipt_path)
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
        raise RuntimeError("execution source/config drifted after the authorized run")
    if receipt.get("config_sha256") != config_sha:
        raise RuntimeError("receipt/config hash binding is invalid")

    conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    row = conn.execute(
        """
        SELECT job_name, status, error, stats, started_at, finished_at
        FROM job_runs WHERE id = ?
        """,
        (job_run_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("JobRun does not exist")
    job_name, status, error, raw_stats, started_at, finished_at = row
    if job_name != "news_poll" or status != "ok" or error is not None:
        raise RuntimeError("authorized migration JobRun is not an error-free ok terminal")
    stats = _load_json_text(str(raw_stats))
    authorization = stats.get("execution_authorization")
    if not isinstance(authorization, dict) or (
        authorization.get("authorization_receipt_sha256") != receipt_sha
        or authorization.get("authorization_id") != receipt.get("authorization_id")
        or authorization.get("execution_mode") != "initial_backlog_migration"
    ):
        raise RuntimeError("JobRun authorization binding is invalid")
    if stats.get("config_sha256") != config_sha:
        raise RuntimeError("JobRun config hash binding is invalid")

    sources = stats.get("sources")
    if not isinstance(sources, dict):
        raise RuntimeError("JobRun source stats are missing")
    cninfo = sources.get("cninfo")
    if not isinstance(cninfo, dict):
        raise RuntimeError("CNInfo source stats are missing")
    raw_slices = cninfo.get("slices")
    if not isinstance(raw_slices, list) or not all(
        isinstance(item, dict) for item in raw_slices
    ):
        raise RuntimeError("CNInfo slice stats are missing")
    slices = cast(list[JsonObject], raw_slices)
    observed_dates = [str(item.get("date_shanghai")) for item in slices]
    if observed_dates != list(expected_slices):
        raise RuntimeError("observed slice dates do not match the authorization")

    slice_sums = {key: 0 for key in SLICE_COUNTER_KEYS}
    daily_slices: list[JsonObject] = []
    for item in slices:
        if (
            item.get("attempted") is not True
            or item.get("date_closed") is not True
            or item.get("pagination_complete") is not True
            or item.get("coverage_proven") is not True
            or item.get("checkpoint_committed") is not True
            or item.get("page_cap_hit") is not False
            or item.get("failure") is not None
            or item.get("disposition_identity_valid") is not True
        ):
            raise RuntimeError("CNInfo slice did not close cleanly")
        counters: JsonObject = {}
        for key in SLICE_COUNTER_KEYS:
            value = item.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RuntimeError(f"CNInfo slice {key} is invalid")
            counters[key] = value
            slice_sums[key] += value
        if item.get("disposition_total") != counters["fetched"] or (
            counters["inserted"]
            + counters["duplicate_url"]
            + counters["duplicate_content_hash"]
            + counters["filtered"]
            != counters["fetched"]
        ):
            raise RuntimeError("CNInfo slice disposition identity is invalid")
        daily_slices.append(
            {
                "date_shanghai": item["date_shanghai"],
                "mode": item.get("mode"),
                **counters,
                "disposition_total": item["disposition_total"],
                "disposition_identity_valid": True,
                "page_count": item.get("page_count"),
                "logical_requests": item.get("logical_request_count"),
                "physical_attempts": item.get("physical_attempt_count"),
                "pagination_complete": True,
                "coverage_proven": True,
                "checkpoint_committed": True,
                "page_cap_hit": False,
                "failure": None,
                "newest_observed_at_utc": _utc_iso(
                    item.get("newest_observed_at_utc")
                ),
            }
        )
    source_aggregate = {key: int(cninfo.get(key, -1)) for key in SLICE_COUNTER_KEYS}
    if source_aggregate != slice_sums:
        raise RuntimeError("CNInfo slice counters do not match source aggregate")

    checkpoint = cninfo.get("daily_checkpoint")
    if not isinstance(checkpoint, dict) or (
        checkpoint.get("verified_checkpoint_date_shanghai_before")
        != expected_checkpoint_before
        or checkpoint.get("verified_checkpoint_date_shanghai_after")
        != expected_checkpoint_after
        or checkpoint.get("checkpoint_committed") is not True
        or checkpoint.get("partial_checkpoint") is not False
    ):
        raise RuntimeError("daily checkpoint does not match the authorized round")

    safety_before = stats.get("safety_before")
    safety_after = stats.get("safety_after")
    if not isinstance(safety_before, dict) or not isinstance(safety_after, dict):
        raise RuntimeError("process safety snapshots are missing")
    before_settings = safety_before.get("settings")
    after_settings = safety_after.get("settings")
    if (
        before_settings != EXPECTED_PROCESS_SETTINGS
        or after_settings != EXPECTED_PROCESS_SETTINGS
        or safety_before != safety_after
        or stats.get("safety_unchanged") is not True
    ):
        raise RuntimeError("process safety invariants are not closed")

    persisted_settings, env_sha, env_mtime_ns = _parse_env_declarations(env_path)
    divergences = [
        {
            "setting": setting,
            "persisted_env_effective": persisted_settings.get(setting),
            "process_effective": cast(dict[str, object], before_settings).get(setting),
        }
        for setting in ENV_TO_SETTING.values()
        if persisted_settings.get(setting)
        != cast(dict[str, object], before_settings).get(setting)
    ]
    started = _parse_utc(started_at)
    finished = _parse_utc(finished_at)
    env_mtime = datetime.fromtimestamp(env_mtime_ns / 1_000_000_000, tz=UTC)
    if env_mtime > started:
        raise RuntimeError(".env changed after the authorized run started")
    poll_completed = _parse_utc(stats.get("poll_completed_at"))
    run_rows = _run_rows_evidence(
        conn,
        job_run_id=job_run_id,
        expected_inserted=source_aggregate["inserted"],
        source_stats=cninfo,
        poll_completed_at=poll_completed,
    )
    if (
        run_rows["invalid_pit_chain_rows"] != 0
        or run_rows["available_time_not_after_published_at_rows"] != 0
        or run_rows["coverage_gap_marked_rows"] != source_aggregate["inserted"]
        or run_rows["coverage_gap_catchup_rows"] != source_aggregate["inserted"]
    ):
        raise RuntimeError("run-row PIT or coverage-gap evidence is invalid")

    news_items_after = _count(conn, "SELECT COUNT(*) FROM news_items")
    cninfo_after = _count(
        conn, "SELECT COUNT(*) FROM news_items WHERE source = 'cninfo'"
    )
    duplicate_url_groups = _count(
        conn,
        "SELECT COUNT(*) FROM (SELECT url FROM news_items GROUP BY url HAVING COUNT(*) > 1)",
    )
    duplicate_hash_groups = _count(
        conn,
        "SELECT COUNT(*) FROM ("
        "SELECT content_hash FROM news_items "
        "GROUP BY content_hash HAVING COUNT(*) > 1)",
    )
    proposal_count = _count(conn, "SELECT COUNT(*) FROM trade_proposals")
    order_count = _count(conn, "SELECT COUNT(*) FROM broker_orders")
    non_simulate_count = _count(
        conn,
        "SELECT COUNT(*) FROM broker_orders WHERE environment IS NULL OR environment != 'SIMULATE'",
    )
    running_jobs = _count(
        conn, "SELECT COUNT(*) FROM job_runs WHERE status = 'running'"
    )
    receipt_uses = _count(
        conn,
        "SELECT COUNT(*) FROM job_runs "
        "WHERE json_extract(stats, '$.execution_authorization.authorization_id') = ?",
        (receipt.get("authorization_id"),),
    )
    quick_check_rows = [str(item[0]) for item in conn.execute("PRAGMA quick_check")]
    if quick_check_rows != ["ok"]:
        raise RuntimeError("SQLite quick_check failed")
    if conn.total_changes != 0:
        raise RuntimeError("evidence query unexpectedly changed the database")
    conn.close()

    scheduler_loaded = _scheduler_loaded()
    if scheduler_loaded or V2_1_SCHEDULER_ACTIVATED:
        raise RuntimeError("scheduler activation gate is not closed")
    if proposal_count != 1 or order_count != 1 or non_simulate_count != 0:
        raise RuntimeError("trading table safety counts changed")
    if running_jobs != 0 or receipt_uses != 1:
        raise RuntimeError("JobRun terminality or receipt single-use evidence failed")
    if duplicate_url_groups != 0 or duplicate_hash_groups != 0:
        raise RuntimeError("global news-item deduplication is not closed")
    if "unlock_trade" not in PERMANENTLY_BLOCKED_METHODS:
        raise RuntimeError("unlock_trade is no longer permanently blocked")

    created = datetime.now(UTC)
    raw_stats_text = str(raw_stats)
    execution_source_path = PROJECT_DIR / "src/alphapilot/jobs/news_poll.py"
    evidence: JsonObject = {
        "schema_version": "p4.1-news-poll-v2.1-initial-migration-round-evidence-v2",
        "created_at_utc": created.isoformat().replace("+00:00", "Z"),
        "created_at_shanghai": created.astimezone(SHANGHAI).isoformat(),
        "review_status": "pending_independent_review",
        "repository_head_at_execution": execution_head,
        "repository_head_at_evidence_freeze": current_head,
        "execution_source": {
            "path": "src/alphapilot/jobs/news_poll.py",
            "sha256": _sha256(execution_source_path),
            "source_or_config_drift_after_execution": False,
        },
        "job_run": {
            "id": job_run_id,
            "job_name": job_name,
            "status": status,
            "error": error,
            "started_at_utc": started.isoformat().replace("+00:00", "Z"),
            "finished_at_utc": finished.isoformat().replace("+00:00", "Z"),
            "stats_bytes": len(raw_stats_text.encode()),
            "stats_raw_sha256": hashlib.sha256(raw_stats_text.encode()).hexdigest(),
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
            "authorization_id": receipt["authorization_id"],
            "expected_checkpoint_date_shanghai_before": expected_checkpoint_before,
            "expected_slice_dates_shanghai": list(expected_slices),
            "receipt_uses_observed": receipt_uses,
        },
        "execution_contract": {
            "execution_mode": stats.get("execution_mode"),
            "run_mode": stats.get("run_mode"),
            "coverage_gap": stats.get("coverage_gap"),
            "canonical_column": cninfo.get("canonical_column"),
            "strict_tls": cninfo.get("tls_verification"),
            "logical_requests": cast(dict[str, object], cninfo["request_budget"]).get(
                "logical_request_count"
            ),
            "physical_attempts": cast(dict[str, object], cninfo["request_budget"]).get(
                "physical_attempt_count"
            ),
            "retries": cninfo.get("retry_count"),
            "failures": cninfo.get("failure_count"),
            "request_budget": cninfo.get("request_budget"),
            "terminal_diagnostics": stats.get("terminal_diagnostics"),
        },
        "daily_slices": daily_slices,
        "slice_to_source_reconciliation": {
            "slice_sums": slice_sums,
            "source_aggregate": source_aggregate,
            "all_equal": slice_sums == source_aggregate,
        },
        "checkpoint": checkpoint,
        "persistence_evidence": {
            "news_items_before": news_items_after - source_aggregate["inserted"],
            "news_items_after": news_items_after,
            "cninfo_before": cninfo_after - source_aggregate["inserted"],
            "cninfo_after": cninfo_after,
            "cninfo_inserted": source_aggregate["inserted"],
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
            "env_mtime_precedes_run_start": env_mtime <= started,
            "env_effective": persisted_settings,
            "structural_code_invariants": {
                "unlock_trade_permanently_blocked": True,
                "v2_1_scheduler_activated_constant": V2_1_SCHEDULER_ACTIVATED,
            },
        },
        "process_effective_safety": {
            "before": before_settings,
            "after": after_settings,
            "before_and_after_equal": safety_before == safety_after,
            "trade_proposal_ids_before": safety_before.get("trade_proposal_ids"),
            "trade_proposal_ids_after": safety_after.get("trade_proposal_ids"),
            "broker_order_ids_before": safety_before.get("broker_order_ids"),
            "broker_order_ids_after": safety_after.get("broker_order_ids"),
        },
        "persisted_vs_process_divergences": divergences,
        "runtime_safety": {
            "trade_proposals_after": proposal_count,
            "broker_orders_after": order_count,
            "non_simulate_broker_orders": non_simulate_count,
            "running_job_runs_after": running_jobs,
            "scheduler_launchagent_loaded_after": scheduler_loaded,
            "scheduler_plist_path": str(plist_path),
            "scheduler_plist_sha256": _sha256(plist_path) if plist_path.exists() else None,
        },
        "phase_gates": {
            "this_authorized_run_succeeded": True,
            "initial_backlog_migration_complete": False,
            "standard_incremental_validation_complete": False,
            "scheduler_activated": False,
            "p4_2b_production_wiring_unlocked": False,
            "p4_3_unlocked": False,
            "next_action": (
                "Independent reviewer must validate JobRun 76932 and issue a new "
                "single-round receipt before the 2026-08-07/08 migration."
            ),
        },
    }
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--job-run-id", type=int, required=True)
    parser.add_argument("--execution-head", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--expected-slice", action="append", required=True)
    parser.add_argument("--checkpoint-before", required=True)
    parser.add_argument("--checkpoint-after", required=True)
    parser.add_argument("--env", type=Path, default=PROJECT_DIR / ".env")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = build_evidence(
        database=args.db.resolve(),
        job_run_id=args.job_run_id,
        execution_head=args.execution_head,
        receipt_path=args.receipt.resolve(),
        expected_slices=args.expected_slice,
        expected_checkpoint_before=args.checkpoint_before,
        expected_checkpoint_after=args.checkpoint_after,
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
