from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path("config/p4_news_poll_v1.yaml")
DEFAULT_DATABASE = Path("data/alphapilot.db")
SHANGHAI = ZoneInfo("Asia/Shanghai")
TERMINAL_JOB_STATUSES = frozenset({"ok", "failed"})
ENABLED_SOURCE_STATUSES = frozenset(
    {"ok", "empty", "degraded", "failed", "unavailable", "skipped_no_watchlist"}
)

JsonObject = dict[str, Any]


class AcceptanceNotReady(RuntimeError):
    """The frozen observation window has not reached its reporting boundary."""


@dataclass(frozen=True, slots=True)
class FrozenConfig:
    path: Path
    sha256: str
    document: JsonObject


@dataclass(frozen=True, slots=True)
class JobEvidence:
    run_id: int
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    stats: JsonObject
    poll_started_at: datetime | None
    poll_completed_at: datetime | None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: object) -> JsonObject | None:
    return dict(value) if isinstance(value, Mapping) else None


def _json_object(value: object) -> JsonObject | None:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if not isinstance(value, str):
        return None
    try:
        parsed: object = json.loads(value)
    except json.JSONDecodeError:
        return None
    return _mapping(parsed)


def _aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _sqlite_utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _nonnegative_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_config(path: Path) -> FrozenConfig:
    resolved = path.resolve()
    loaded: object = yaml.safe_load(resolved.read_bytes())
    document = _mapping(loaded)
    if document is None:
        raise ValueError("P4.1 news-poll config must be a mapping")

    _require(document.get("schema_version") == "p4.1-news-poll-v1", "unexpected schema_version")
    schedule = _mapping(document.get("schedule"))
    acceptance = _mapping(document.get("acceptance"))
    sources = _mapping(document.get("sources"))
    provenance = _mapping(document.get("provenance"))
    runtime = _mapping(document.get("runtime"))
    safety = _mapping(document.get("safety"))
    phase_gate = _mapping(document.get("phase_gate"))
    for name, value in (
        ("schedule", schedule),
        ("acceptance", acceptance),
        ("sources", sources),
        ("provenance", provenance),
        ("runtime", runtime),
        ("safety", safety),
        ("phase_gate", phase_gate),
    ):
        _require(value is not None, f"config.{name} must be a mapping")
    assert schedule is not None
    assert acceptance is not None
    assert sources is not None
    assert provenance is not None
    assert runtime is not None
    assert safety is not None
    assert phase_gate is not None

    _require(schedule.get("timezone") == "Asia/Shanghai", "schedule timezone must be Shanghai")
    _require(schedule.get("scheduler_tick_minutes") == 10, "scheduler tick must be 10 minutes")
    _require(schedule.get("off_session_poll_minutes") == 30, "off-session poll must be 30 minutes")
    _require(
        runtime.get("scheduler_enabled_env") == "ALPHAPILOT_NEWS_POLL_ENABLED",
        "news-poll scheduler env contract drifted",
    )
    _require(runtime.get("scheduler_enabled_default") is False, "news poll must default off")
    _require(
        runtime.get("dedicated_scheduler_launchd_value") is True,
        "dedicated scheduler must explicitly enable news poll",
    )
    _require(
        acceptance.get("trading_dates") == ["2026-08-03", "2026-08-04", "2026-08-05"],
        "acceptance trading dates are frozen",
    )
    _require(acceptance.get("expected_poll_slots_per_day") == 64, "expected slots must be 64")
    _require(acceptance.get("require_every_expected_slot") is True, "missing-slot gate disabled")
    _require(
        acceptance.get("allow_unexpected_extra_poll_runs") is False,
        "extra poll runs must remain forbidden",
    )

    enabled = {"cninfo", "sina_company_news", "akshare_ths"}
    for source_id in enabled:
        source = _mapping(sources.get(source_id))
        _require(source is not None, f"missing source {source_id}")
        assert source is not None
        _require(source.get("enabled") is True, f"{source_id} must remain enabled")
        _require(source.get("audited") is True, f"{source_id} must remain audited")
    cninfo = _mapping(sources.get("cninfo"))
    assert cninfo is not None
    _require(cninfo.get("critical") is True, "cninfo must remain critical")
    _require(cninfo.get("verify_tls") is True, "cninfo TLS verification must remain enabled")
    _require(cninfo.get("require_https") is True, "cninfo HTTPS must remain mandatory")

    disabled_contract = {
        "akshare_cls": "unavailable",
        "akshare_caixin": "excluded_missing_native_title",
        "futu_auxiliary": "pending_trading_day_latency_retest",
    }
    for source_id, status in disabled_contract.items():
        source = _mapping(sources.get(source_id))
        _require(source is not None, f"missing excluded source {source_id}")
        assert source is not None
        _require(source.get("enabled") is False, f"{source_id} must remain disabled")
        _require(source.get("frozen_status") == status, f"{source_id} status drifted")
        _require(
            source.get("max_requests_per_run") == 0,
            f"{source_id} request budget must be zero",
        )
        _require(
            source.get("max_attempts_per_request") == 0,
            f"{source_id} attempts must remain zero",
        )

    _require(
        provenance.get("audited_news_sources") == ["akshare_ths", "cninfo", "sina_company_news"],
        "audited news-source allowlist drifted",
    )
    _require(provenance.get("symbol_nullable") is True, "symbol must remain nullable")
    _require(provenance.get("published_at_nullable") is True, "published_at must remain nullable")
    _require(
        provenance.get("available_time_policy")
        == "write_locked_immediately_before_flush_utc",
        "PIT policy drifted",
    )
    _require(provenance.get("decision_visibility_operator") == "<", "PIT operator must be strict")

    _require(safety.get("required_trading_mode") == "research", "trading mode must be research")
    _require(safety.get("required_live_trading_enabled") is False, "live trading must be false")
    _require(
        safety.get("required_paper_auto_trading_enabled") is False,
        "paper auto trading must be false",
    )
    _require(
        safety.get("required_futu_account_mutation_enabled") is False,
        "Futu account mutation must be false",
    )
    _require(safety.get("required_unlock_trade_blocked") is True, "unlock_trade must be blocked")
    _require(phase_gate.get("p4_2_unlocked") is False, "P4.2 must remain locked")
    return FrozenConfig(path=resolved, sha256=_sha256(resolved), document=document)


def _parse_clock(value: object) -> time:
    if not isinstance(value, str):
        raise ValueError("trading-session clock must be text")
    return time.fromisoformat(value)


def _acceptance_dates(config: FrozenConfig) -> list[date]:
    acceptance = _mapping(config.document["acceptance"])
    assert acceptance is not None
    values = acceptance["trading_dates"]
    assert isinstance(values, list)
    return [date.fromisoformat(str(value)) for value in values]


def expected_poll_slots(config: FrozenConfig, target: date) -> list[datetime]:
    schedule = _mapping(config.document["schedule"])
    assert schedule is not None
    tick = int(schedule["scheduler_tick_minutes"])
    off_session = int(schedule["off_session_poll_minutes"])
    sessions = schedule["trading_sessions"]
    if not isinstance(sessions, list):
        raise ValueError("schedule.trading_sessions must be a list")

    result: list[datetime] = []
    current = datetime.combine(target, time.min, tzinfo=SHANGHAI)
    end = current + timedelta(days=1)
    while current < end:
        in_session = any(
            isinstance(item, Mapping)
            and _parse_clock(item.get("start")) <= current.time() <= _parse_clock(item.get("end"))
            for item in sessions
        )
        if in_session or current.minute % off_session == 0:
            result.append(current)
        current += timedelta(minutes=tick)
    return result


def _ready_at(config: FrozenConfig, scope: str, dates: Sequence[date]) -> datetime:
    acceptance = _mapping(config.document["acceptance"])
    assert acceptance is not None
    if scope == "final":
        ready = _aware_datetime(acceptance.get("final_report_ready_at"))
        if ready is None:
            raise ValueError("acceptance.final_report_ready_at must be timezone-aware")
        return ready
    clock = _parse_clock(acceptance.get("daily_report_ready_after_next_day"))
    return datetime.combine(dates[0] + timedelta(days=1), clock, tzinfo=SHANGHAI).astimezone(UTC)


def _connect_read_only(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _load_job_runs(connection: sqlite3.Connection) -> list[JobEvidence]:
    if not _table_exists(connection, "job_runs"):
        return []
    rows = connection.execute(
        """
        SELECT id, status, started_at, finished_at, error, stats
        FROM job_runs
        WHERE job_name='news_poll'
        ORDER BY id
        """
    ).fetchall()
    result: list[JobEvidence] = []
    for row in rows:
        stats = _json_object(row["stats"]) or {}
        result.append(
            JobEvidence(
                run_id=int(row["id"]),
                status=str(row["status"]),
                started_at=_sqlite_utc_datetime(row["started_at"]),
                finished_at=_sqlite_utc_datetime(row["finished_at"]),
                error=str(row["error"]) if row["error"] is not None else None,
                stats=stats,
                poll_started_at=_aware_datetime(stats.get("poll_started_at")),
                poll_completed_at=_aware_datetime(stats.get("poll_completed_at")),
            )
        )
    return result


def _selected_runs(runs: Iterable[JobEvidence], dates: set[date]) -> list[JobEvidence]:
    selected: list[JobEvidence] = []
    for run in runs:
        timestamp = run.poll_started_at or run.started_at
        if timestamp is not None and timestamp.astimezone(SHANGHAI).date() in dates:
            selected.append(run)
    return selected


def _cadence_audit(
    config: FrozenConfig,
    dates: Sequence[date],
    runs: Sequence[JobEvidence],
) -> JsonObject:
    schedule = _mapping(config.document["schedule"])
    assert schedule is not None
    tolerance = float(schedule["slot_tolerance_seconds"])
    expected = [slot for target in dates for slot in expected_poll_slots(config, target)]
    remaining = list(runs)
    matches: list[JsonObject] = []
    missing: list[str] = []
    for slot in expected:
        slot_utc = slot.astimezone(UTC)
        candidates = [
            (abs((run.poll_started_at - slot_utc).total_seconds()), run)
            for run in remaining
            if run.poll_started_at is not None
            and abs((run.poll_started_at - slot_utc).total_seconds()) <= tolerance
        ]
        if not candidates:
            missing.append(slot.isoformat())
            continue
        delta, matched = min(candidates, key=lambda item: (item[0], item[1].run_id))
        remaining.remove(matched)
        matches.append(
            {
                "slot": slot.isoformat(),
                "job_run_id": matched.run_id,
                "start_delta_seconds": round(delta, 6),
            }
        )
    return {
        "expected_slots": len(expected),
        "matched_slots": len(matches),
        "missing_slots": missing,
        "unexpected_run_ids": [run.run_id for run in remaining],
        "matches": matches,
    }


def _source_audit(config: FrozenConfig, runs: Sequence[JobEvidence]) -> JsonObject:
    sources = _mapping(config.document["sources"])
    assert sources is not None
    issues: list[str] = []
    critical_failures: list[JsonObject] = []
    run_results: list[JsonObject] = []
    totals: dict[str, dict[str, int]] = {
        source_id: {
            "request_count": 0,
            "retry_count": 0,
            "fetched": 0,
            "inserted": 0,
            "duplicate_url": 0,
            "duplicate_content_hash": 0,
            "failure_count": 0,
        }
        for source_id in sources
    }
    for run in runs:
        run_issues: list[str] = []
        stats_sources = _mapping(run.stats.get("sources"))
        if stats_sources is None:
            run_issues.append("stats.sources missing")
            stats_sources = {}
        for source_id, raw_contract in sources.items():
            contract = _mapping(raw_contract)
            observed = _mapping(stats_sources.get(source_id))
            if contract is None:
                run_issues.append(f"{source_id}: invalid config contract")
                continue
            if observed is None:
                run_issues.append(f"{source_id}: stats missing")
                continue
            counters: dict[str, int] = {}
            for field in (
                "request_count",
                "retry_count",
                "fetched",
                "inserted",
                "duplicate_url",
                "duplicate_content_hash",
                "failure_count",
            ):
                value = _nonnegative_integer(observed.get(field))
                if value is None:
                    run_issues.append(f"{source_id}.{field}: must be a non-negative integer")
                else:
                    counters[field] = value
                    totals[source_id][field] += value
            status = observed.get("status")
            if not isinstance(status, str) or not status:
                run_issues.append(f"{source_id}.status: missing")
                continue

            if contract.get("enabled") is False:
                expected_status = contract.get("frozen_status")
                if status != expected_status:
                    run_issues.append(
                        f"{source_id}.status={status!r}, expected {expected_status!r}"
                    )
                for field in counters:
                    if counters[field] != 0:
                        run_issues.append(f"{source_id}.{field}: disabled source must remain zero")
                if observed.get("attempted") is not False:
                    run_issues.append(f"{source_id}.attempted must be false")
                if source_id == "futu_auxiliary":
                    if observed.get("quote_methods_called") != []:
                        run_issues.append("futu_auxiliary.quote_methods_called must be empty")
                    if observed.get("trade_methods_called") != []:
                        run_issues.append("futu_auxiliary.trade_methods_called must be empty")
                continue

            if status not in ENABLED_SOURCE_STATUSES:
                run_issues.append(f"{source_id}.status={status!r}: unsupported")
            if source_id == "cninfo" and observed.get("tls_verification") is not True:
                run_issues.append("cninfo.tls_verification is not true")
            request_count = counters.get("request_count")
            retry_count = counters.get("retry_count")
            fetched = counters.get("fetched")
            inserted = counters.get("inserted")
            duplicate_url = counters.get("duplicate_url")
            duplicate_hash = counters.get("duplicate_content_hash")
            failure_count = counters.get("failure_count")
            max_requests = _nonnegative_integer(contract.get("max_requests_per_run"))
            if request_count == 0 and status != "skipped_no_watchlist":
                run_issues.append(f"{source_id}.request_count: enabled source was not attempted")
            if status == "skipped_no_watchlist" and request_count != 0:
                run_issues.append(f"{source_id}: skipped_no_watchlist must make zero requests")
            if (
                request_count is not None
                and max_requests is not None
                and request_count > max_requests
            ):
                run_issues.append(f"{source_id}.request_count exceeds frozen budget")
            if (
                retry_count is not None
                and request_count is not None
                and retry_count > request_count
            ):
                run_issues.append(f"{source_id}.retry_count exceeds request_count")
            if None not in (fetched, inserted, duplicate_url, duplicate_hash):
                assert fetched is not None
                assert inserted is not None
                assert duplicate_url is not None
                assert duplicate_hash is not None
                if inserted + duplicate_url + duplicate_hash > fetched:
                    run_issues.append(f"{source_id}: fetched rows do not reconcile")
            if failure_count is not None and failure_count:
                failures = observed.get("failures")
                if not isinstance(failures, list) or len(failures) != failure_count:
                    run_issues.append(f"{source_id}: failure_count lacks explicit failures")
                if contract.get("critical") is True:
                    critical_failures.append(
                        {
                            "job_run_id": run.run_id,
                            "source_id": source_id,
                            "failure_count": failure_count,
                            "status": status,
                        }
                    )
                    if run.status != "failed":
                        run_issues.append(f"{source_id}: critical failure did not fail JobRun")
            if status in {"failed", "unavailable"} and failure_count == 0:
                run_issues.append(f"{source_id}: failure status without failure_count")
        issues.extend(f"JobRun {run.run_id}: {issue}" for issue in run_issues)
        run_results.append(
            {
                "job_run_id": run.run_id,
                "job_status": run.status,
                "job_error": run.error,
                "observed_sources": stats_sources,
                "issues": run_issues,
            }
        )
    return {
        "issues": issues,
        "critical_failures": critical_failures,
        "runs": run_results,
        "totals": totals,
    }


def _jobrun_audit(config: FrozenConfig, runs: Sequence[JobEvidence]) -> JsonObject:
    issues: list[str] = []
    evidence: list[JsonObject] = []
    expected_settings = {
        "trading_mode": "research",
        "live_trading_enabled": False,
        "paper_auto_trading_enabled": False,
        "futu_enable_account_mutation": False,
        "unlock_trade_permanently_blocked": True,
    }
    for run in runs:
        prefix = f"JobRun {run.run_id}"
        if run.status not in TERMINAL_JOB_STATUSES:
            issues.append(f"{prefix}: non-terminal status {run.status!r}")
        if run.status == "failed" and not (run.error or "").strip():
            issues.append(f"{prefix}: failed row has no error")
        if run.stats.get("config_sha256") != config.sha256:
            issues.append(f"{prefix}: config_sha256 mismatch")
        if run.poll_started_at is None or run.poll_completed_at is None:
            issues.append(f"{prefix}: poll timestamps must be timezone-aware")
        elif run.poll_completed_at < run.poll_started_at:
            issues.append(f"{prefix}: poll_completed_at precedes poll_started_at")
        if run.finished_at is None:
            issues.append(f"{prefix}: finished_at missing")
        if (
            run.started_at is not None
            and run.finished_at is not None
            and run.poll_started_at is not None
            and run.poll_completed_at is not None
            and not (
                run.started_at
                <= run.poll_started_at
                <= run.poll_completed_at
                <= run.finished_at
            )
        ):
            issues.append(f"{prefix}: poll timestamps fall outside durable JobRun")
        if run.stats.get("safety_unchanged") is not True:
            issues.append(f"{prefix}: safety_unchanged is not true")
        safety_before = _mapping(run.stats.get("safety_before"))
        safety_after = _mapping(run.stats.get("safety_after"))
        if safety_before is None or safety_after is None:
            issues.append(f"{prefix}: safety_before/after evidence missing")
        else:
            if safety_before != safety_after:
                issues.append(f"{prefix}: safety_before/after differ")
            for label, snapshot in (("before", safety_before), ("after", safety_after)):
                observed_settings = _mapping(snapshot.get("settings"))
                if observed_settings is None:
                    issues.append(f"{prefix}: safety_{label}.settings missing")
                    continue
                for key, expected in expected_settings.items():
                    if observed_settings.get(key) != expected:
                        issues.append(
                            f"{prefix}: safety_{label}.{key}="
                            f"{observed_settings.get(key)!r}, expected {expected!r}"
                        )
                if snapshot.get("non_simulate_order_count") != 0:
                    issues.append(f"{prefix}: safety_{label} has non-SIMULATE orders")
                if not isinstance(snapshot.get("trade_proposal_ids"), list):
                    issues.append(f"{prefix}: safety_{label}.trade_proposal_ids missing")
                if not isinstance(snapshot.get("broker_order_ids"), list):
                    issues.append(f"{prefix}: safety_{label}.broker_order_ids missing")
        evidence.append(
            {
                "job_run_id": run.run_id,
                "status": run.status,
                "error": run.error,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "poll_started_at": (
                    run.poll_started_at.isoformat() if run.poll_started_at else None
                ),
                "poll_completed_at": (
                    run.poll_completed_at.isoformat() if run.poll_completed_at else None
                ),
                "stats": run.stats,
            }
        )
    return {"issues": issues, "evidence": evidence}


def _schema_audit(connection: sqlite3.Connection) -> JsonObject:
    if not _table_exists(connection, "news_items"):
        return {"exists": False, "issues": ["news_items table missing"]}
    columns = {
        str(row["name"]): {"not_null": bool(row["notnull"]), "type": str(row["type"])}
        for row in connection.execute("PRAGMA table_info(news_items)")
    }
    issues: list[str] = []
    expected_nullable = {"symbol", "published_at"}
    expected_not_null = {
        "source",
        "title",
        "url",
        "available_time",
        "content_hash",
        "raw_payload",
    }
    for name in expected_nullable:
        if name not in columns or columns[name]["not_null"]:
            issues.append(f"{name} must be nullable")
    for name in expected_not_null:
        if name not in columns or not columns[name]["not_null"]:
            issues.append(f"{name} must be NOT NULL")

    unique_keys: set[tuple[str, ...]] = set()
    for index in connection.execute("PRAGMA index_list(news_items)"):
        if not bool(index["unique"]):
            continue
        name = str(index["name"])
        key = tuple(str(row["name"]) for row in connection.execute(f'PRAGMA index_info("{name}")'))
        unique_keys.add(key)
    if ("url",) not in unique_keys:
        issues.append("url independent unique key missing")
    if ("content_hash",) not in unique_keys:
        issues.append("content_hash independent unique key missing")
    return {
        "exists": True,
        "columns": columns,
        "unique_keys": [list(key) for key in sorted(unique_keys)],
        "issues": issues,
    }


def _news_audit(
    config: FrozenConfig,
    connection: sqlite3.Connection,
    job_runs_by_id: Mapping[int, JobEvidence],
) -> JsonObject:
    if not _table_exists(connection, "news_items"):
        return {
            "row_count": 0,
            "duplicate_url_groups": -1,
            "duplicate_content_hash_groups": -1,
            "pit_anomalies": ["news_items table missing"],
            "source_anomalies": ["news_items table missing"],
        }
    duplicate_url = int(
        connection.execute(
            "SELECT COUNT(*) FROM (SELECT url FROM news_items GROUP BY url HAVING COUNT(*) > 1)"
        ).fetchone()[0]
    )
    duplicate_hash = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT content_hash FROM news_items GROUP BY content_hash HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
    )
    rows = connection.execute(
        """
        SELECT id, source, title, url, published_at, available_time, content_hash, raw_payload
        FROM news_items
        ORDER BY id
        """
    ).fetchall()
    provenance = _mapping(config.document["provenance"])
    assert provenance is not None
    audited_sources = {str(value) for value in provenance["audited_news_sources"]}
    audit_key = str(provenance["raw_payload_audit_key"])
    pit_anomalies: list[str] = []
    source_anomalies: list[str] = []
    date_counts: dict[str, int] = {}
    published_equals_available = 0
    for row in rows:
        row_id = int(row["id"])
        available = _sqlite_utc_datetime(row["available_time"])
        if available is None:
            pit_anomalies.append(f"news_item {row_id}: invalid available_time")
        else:
            local_date = available.astimezone(SHANGHAI).date().isoformat()
            date_counts[local_date] = date_counts.get(local_date, 0) + 1
        published = _sqlite_utc_datetime(row["published_at"])
        if published is not None and available is not None and published == available:
            published_equals_available += 1
            pit_anomalies.append(
                f"news_item {row_id}: published_at copied from available_time"
            )
        source = str(row["source"] or "")
        if source not in audited_sources:
            source_anomalies.append(f"news_item {row_id}: unaudited source {source!r}")
        content_hash = str(row["content_hash"] or "")
        if len(content_hash) != 64 or any(
            character not in "0123456789abcdef" for character in content_hash
        ):
            source_anomalies.append(f"news_item {row_id}: invalid content_hash")
        if not str(row["title"] or "").strip() or not str(row["url"] or "").strip():
            source_anomalies.append(f"news_item {row_id}: empty title or URL")

        raw_payload = _json_object(row["raw_payload"])
        ingestion = _mapping(raw_payload.get(audit_key)) if raw_payload is not None else None
        if ingestion is None:
            pit_anomalies.append(f"news_item {row_id}: ingestion audit metadata missing")
            continue
        if (
            ingestion.get("available_time_basis")
            != "write_locked_immediately_before_flush_utc"
        ):
            pit_anomalies.append(f"news_item {row_id}: available_time basis mismatch")
        fetched = _aware_datetime(ingestion.get("fetched_at_utc"))
        lock_acquired = _aware_datetime(ingestion.get("write_lock_acquired_at_utc"))
        assigned = _aware_datetime(ingestion.get("available_time_assigned_at_utc"))
        raw_job_run_id = ingestion.get("job_run_id")
        job_run_id = (
            raw_job_run_id
            if isinstance(raw_job_run_id, int) and not isinstance(raw_job_run_id, bool)
            else None
        )
        if fetched is None or lock_acquired is None or assigned is None or job_run_id is None:
            pit_anomalies.append(f"news_item {row_id}: invalid ingestion timestamps or JobRun id")
            continue
        if available is None:
            continue
        if not (fetched <= lock_acquired <= assigned):
            pit_anomalies.append(f"news_item {row_id}: fetch/write-lock ordering invalid")
        if assigned != available:
            pit_anomalies.append(f"news_item {row_id}: assigned time disagrees with available_time")
        job_run = job_runs_by_id.get(job_run_id)
        if job_run is None:
            pit_anomalies.append(f"news_item {row_id}: referenced news_poll JobRun missing")
            continue
        if job_run.poll_started_at is None or job_run.poll_completed_at is None:
            pit_anomalies.append(f"news_item {row_id}: referenced JobRun lacks poll timestamps")
            continue
        job_sources = _mapping(job_run.stats.get("sources"))
        source_stats = _mapping(job_sources.get(source)) if job_sources is not None else None
        flush_completed = (
            _aware_datetime(source_stats.get("db_flush_completed_at"))
            if source_stats is not None
            else None
        )
        commit_completed = (
            _aware_datetime(source_stats.get("db_commit_completed_at"))
            if source_stats is not None
            else None
        )
        if flush_completed is None or commit_completed is None:
            pit_anomalies.append(f"news_item {row_id}: JobRun write completion evidence missing")
            continue
        if not (
            job_run.poll_started_at
            <= fetched
            <= lock_acquired
            <= available
            <= flush_completed
            <= commit_completed
            <= job_run.poll_completed_at
        ):
            pit_anomalies.append(f"news_item {row_id}: PIT ordering is outside referenced JobRun")

    return {
        "row_count": len(rows),
        "rows_by_available_date_shanghai": dict(sorted(date_counts.items())),
        "duplicate_url_groups": duplicate_url,
        "duplicate_content_hash_groups": duplicate_hash,
        "published_at_equals_available_time": published_equals_available,
        "pit_anomalies": pit_anomalies,
        "source_anomalies": source_anomalies,
        "available_time_coverage": (
            round(
                (
                    len(rows)
                    - sum("invalid available_time" in item for item in pit_anomalies)
                )
                / len(rows),
                6,
            )
            if rows
            else 0.0
        ),
    }


def _created_in_window(value: object, start: datetime, end: datetime) -> bool:
    parsed = _sqlite_utc_datetime(value)
    return parsed is not None and start <= parsed < end


def _trading_audit(
    connection: sqlite3.Connection,
    dates: Sequence[date],
) -> JsonObject:
    start = datetime.combine(min(dates), time.min, tzinfo=SHANGHAI).astimezone(UTC)
    end = datetime.combine(
        max(dates) + timedelta(days=1),
        time.min,
        tzinfo=SHANGHAI,
    ).astimezone(UTC)
    issues: list[str] = []
    counts: dict[str, int] = {}
    for table in ("trade_proposals", "broker_orders"):
        if not _table_exists(connection, table):
            issues.append(f"{table} table missing")
            counts[f"{table}_created_in_window"] = -1
            continue
        rows = connection.execute(f"SELECT id, created_at FROM {table}").fetchall()
        counts[f"{table}_created_in_window"] = sum(
            _created_in_window(row["created_at"], start, end) for row in rows
        )
    if _table_exists(connection, "broker_orders"):
        counts["non_simulate_broker_orders"] = int(
            connection.execute(
                "SELECT COUNT(*) FROM broker_orders WHERE environment <> 'SIMULATE'"
            ).fetchone()[0]
        )
    else:
        counts["non_simulate_broker_orders"] = -1
    return {
        "window_start_utc": start.isoformat(),
        "window_end_utc": end.isoformat(),
        **counts,
        "issues": issues,
    }


def _gate(
    config: FrozenConfig,
    scope: str,
    dates: Sequence[date],
    cadence: JsonObject,
    jobrun: JsonObject,
    sources: JsonObject,
    schema: JsonObject,
    news: JsonObject,
    trading: JsonObject,
) -> JsonObject:
    acceptance = _mapping(config.document["acceptance"])
    phase_gate = _mapping(config.document["phase_gate"])
    assert acceptance is not None
    assert phase_gate is not None
    rows_by_date = news["rows_by_available_date_shanghai"]
    assert isinstance(rows_by_date, Mapping)
    accepted_news_rows = sum(int(rows_by_date.get(value.isoformat(), 0)) for value in dates)
    source_totals = sources["totals"]
    assert isinstance(source_totals, Mapping)
    inserted_total = sum(
        int(total.get("inserted", 0))
        for total in source_totals.values()
        if isinstance(total, Mapping)
    )
    values = {
        "reporting_scope_complete": scope == "daily" or dates == _acceptance_dates(config),
        "expected_slots_complete": not cadence["missing_slots"],
        "no_unexpected_poll_runs": not cadence["unexpected_run_ids"],
        "jobrun_contract_ok": not jobrun["issues"],
        "source_accounting_ok": not sources["issues"],
        "critical_source_failures_zero": not sources["critical_failures"],
        "news_schema_ok": bool(schema["exists"]) and not schema["issues"],
        "url_duplicates_zero": news["duplicate_url_groups"] == 0,
        "content_hash_duplicates_zero": news["duplicate_content_hash_groups"] == 0,
        "available_time_coverage_100pct": news["available_time_coverage"] == 1.0,
        "published_at_not_substituted": news["published_at_equals_available_time"] == 0,
        "news_items_present_each_date": all(
            int(rows_by_date.get(value.isoformat(), 0)) > 0 for value in dates
        ),
        "inserted_counts_reconcile": inserted_total == accepted_news_rows,
        "pit_ordering_ok": bool(news["row_count"]) and not news["pit_anomalies"],
        "audited_sources_only": not news["source_anomalies"],
        "no_trade_proposals_created": trading["trade_proposals_created_in_window"] == 0,
        "no_broker_orders_created": trading["broker_orders_created_in_window"] == 0,
        "non_simulate_broker_orders_zero": trading["non_simulate_broker_orders"] == 0,
        "p4_2_still_locked": phase_gate.get("p4_2_unlocked") is False,
    }
    return {**values, "all_pass": all(values.values())}


def evaluate_acceptance(
    *,
    database: Path,
    config: FrozenConfig,
    scope: str,
    target_date: date | None = None,
    now: datetime | None = None,
) -> JsonObject:
    all_dates = _acceptance_dates(config)
    if scope == "daily":
        if target_date is None or target_date not in all_dates:
            raise ValueError("daily scope requires one frozen acceptance date")
        dates = [target_date]
    elif scope == "final":
        if target_date is not None:
            raise ValueError("final scope does not accept --date")
        dates = all_dates
    else:
        raise ValueError("scope must be daily or final")
    observed_now = now or datetime.now(UTC)
    if observed_now.tzinfo is None or observed_now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    ready_at = _ready_at(config, scope, dates)
    if observed_now.astimezone(UTC) < ready_at:
        raise AcceptanceNotReady(
            f"{scope} evidence is frozen until {ready_at.astimezone(SHANGHAI).isoformat()}"
        )

    with _connect_read_only(database) as connection:
        all_runs = _load_job_runs(connection)
        runs_by_id = {run.run_id: run for run in all_runs}
        selected = _selected_runs(all_runs, set(dates))
        cadence = _cadence_audit(config, dates, selected)
        jobrun = _jobrun_audit(config, selected)
        source = _source_audit(config, selected)
        schema = _schema_audit(connection)
        news = _news_audit(config, connection, runs_by_id)
        trading = _trading_audit(connection, dates)
    gate = _gate(config, scope, dates, cadence, jobrun, source, schema, news, trading)
    return {
        "schema_version": "p4.1-news-acceptance-report-v1",
        "generated_at": observed_now.astimezone(UTC).isoformat(),
        "scope": scope,
        "dates": [value.isoformat() for value in dates],
        "database": str(database.resolve()),
        "read_only": True,
        "config": {
            "path": str(config.path),
            "sha256": config.sha256,
            "schema_version": config.document["schema_version"],
        },
        "ready_at": ready_at.isoformat(),
        "cadence": cadence,
        "jobrun": jobrun,
        "sources": source,
        "schema": schema,
        "news_items": news,
        "trading_safety": trading,
        "gate": gate,
    }


def _write_new_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite P4.1 acceptance evidence: {path}")
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
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


def _default_report(config: FrozenConfig, scope: str, target: date | None) -> Path:
    acceptance = _mapping(config.document["acceptance"])
    assert acceptance is not None
    if scope == "final":
        return PROJECT_DIR / str(acceptance["final_report"])
    assert target is not None
    template = str(acceptance["daily_report_template"])
    return PROJECT_DIR / template.format(date_compact=target.strftime("%Y%m%d"))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the read-only P4.1 daily or final three-trading-day acceptance gate."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--scope", choices=("daily", "final"), required=True)
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    os.chdir(PROJECT_DIR)
    config = load_config(PROJECT_DIR / arguments.config)
    report = evaluate_acceptance(
        database=PROJECT_DIR / arguments.db,
        config=config,
        scope=str(arguments.scope),
        target_date=arguments.date,
    )
    report_path = (
        (PROJECT_DIR / arguments.report).resolve()
        if arguments.report is not None
        else _default_report(config, str(arguments.scope), arguments.date).resolve()
    )
    _write_new_json(report_path, report)
    print(
        json.dumps(
            {
                "report": str(report_path),
                "sha256": _sha256(report_path),
                "all_pass": report["gate"]["all_pass"],
                "p4_2_unlocked": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report["gate"]["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
