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
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path("config/p4_news_poll_v1.yaml")
DEFAULT_DATABASE = Path("data/alphapilot.db")
DEFAULT_OBSERVATION_CONTEXT = Path("docs/phase4/reports/P4.1-observation-context-20260806.json")
DEFAULT_V2_OBSERVATION_CONTEXT = Path(
    "docs/phase4/reports/P4.1-v2-observation-context-20260813.json"
)
V1_SCHEMA_VERSION = "p4.1-news-poll-v1"
V2_SCHEMA_VERSION = "p4.1-news-poll-v2"
EXPECTED_V2_RECEIPT_SHA256 = {
    "p4.1-news-poll-v2": "701555b89e65c830ebf9b6f00557e815654c766452ca2dcf9f16759626a55587",
    "p4.1-news-poll-v2.1": "485f710398698c1692d8afd8de6cd06c71ddf2fbdf42713c6bd4defc4bdfd84b",
}
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
    receipt_path: Path | None = None
    receipt_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ObservationContext:
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


def _dual_timestamp(value: datetime) -> JsonObject:
    observed = value.astimezone(UTC)
    return {
        "utc": observed.isoformat(),
        "shanghai": observed.astimezone(SHANGHAI).isoformat(),
    }


def _is_v2(config: FrozenConfig) -> bool:
    return str(config.document.get("schema_version", "")).startswith(V2_SCHEMA_VERSION)


def _is_v2_1(config: FrozenConfig) -> bool:
    return config.document.get("schema_version") == "p4.1-news-poll-v2.1"


def _default_observation_context(config: FrozenConfig) -> Path:
    if not _is_v2(config):
        return DEFAULT_OBSERVATION_CONTEXT
    acceptance = _mapping(config.document.get("acceptance")) or {}
    explicit = acceptance.get("observation_context")
    if isinstance(explicit, str) and explicit.strip():
        return Path(explicit)
    dates = _acceptance_dates(config)
    if not dates:
        return DEFAULT_V2_OBSERVATION_CONTEXT
    ready_date = max(dates) + timedelta(days=1)
    return Path(f"docs/phase4/reports/P4.1-v2-observation-context-{ready_date:%Y%m%d}.json")


def _preregistered_config_receipt(
    resolved: Path,
    document: JsonObject,
) -> tuple[str, Path, str]:
    receipt_path = resolved.with_suffix(".preregistration.json")
    _require(receipt_path.is_file(), "P4.1 v2 pre-registration receipt is missing")
    receipt_sha256 = _sha256(receipt_path)
    schema_version = str(document.get("schema_version"))
    expected_receipt_sha256 = EXPECTED_V2_RECEIPT_SHA256.get(schema_version)
    _require(
        expected_receipt_sha256 is not None and receipt_sha256 == expected_receipt_sha256,
        "P4.1 v2 pre-registration receipt SHA-256 drifted",
    )
    loaded: object = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt = _mapping(loaded)
    _require(receipt is not None, "P4.1 v2 pre-registration receipt must be an object")
    assert receipt is not None
    _require(
        receipt.get("schema_version") == f"{schema_version}-preregistration-v1",
        "P4.1 v2 receipt schema does not match config schema",
    )
    expected_path = receipt.get("config_path")
    _require(isinstance(expected_path, str), "P4.1 v2 receipt config path missing")
    assert isinstance(expected_path, str)
    _require(
        (PROJECT_DIR / expected_path).resolve() == resolved,
        "P4.1 v2 receipt points to a different config path",
    )
    expected_sha = receipt.get("config_sha256")
    _require(
        isinstance(expected_sha, str) and len(expected_sha) == 64,
        "P4.1 v2 receipt lacks a valid config SHA-256",
    )
    assert isinstance(expected_sha, str)
    if schema_version == "p4.1-news-poll-v2.1":
        controlled_probe = _mapping(document.get("controlled_probe")) or {}
        sources = _mapping(document.get("sources")) or {}
        cninfo = _mapping(sources.get("cninfo")) or {}
        probe_path_value = receipt.get("controlled_probe_path")
        probe_sha_value = receipt.get("controlled_probe_sha256")
        _require(isinstance(probe_path_value, str), "v2.1 receipt probe path missing")
        assert isinstance(probe_path_value, str)
        probe_path = (PROJECT_DIR / probe_path_value).resolve()
        _require(
            probe_path == (PROJECT_DIR / str(controlled_probe.get("path"))).resolve(),
            "v2.1 config/receipt probe paths disagree",
        )
        _require(
            probe_path.is_file()
            and isinstance(probe_sha_value, str)
            and _sha256(probe_path) == probe_sha_value == controlled_probe.get("sha256"),
            "v2.1 controlled probe SHA-256 drifted",
        )
        _require(
            receipt.get("canonical_column") == cninfo.get("canonical_column") == "szse",
            "v2.1 receipt/config canonical column disagrees",
        )
        receipt_locks = _mapping(receipt.get("phase_locks")) or {}
        phase_gate = _mapping(document.get("phase_gate")) or {}
        _require(
            receipt_locks.get("p4_2b_production_wiring_unlocked")
            == phase_gate.get("p4_2b_production_wiring_unlocked")
            is False
            and receipt_locks.get("p4_3_unlocked") == phase_gate.get("p4_3_unlocked") is False,
            "v2.1 receipt/config phase locks disagree",
        )
    return expected_sha, receipt_path, receipt_sha256


def _load_v2_config(resolved: Path, document: JsonObject) -> FrozenConfig:
    config_sha256 = _sha256(resolved)
    expected_sha256, receipt_path, receipt_sha256 = _preregistered_config_receipt(
        resolved,
        document,
    )
    _require(
        config_sha256 == expected_sha256,
        "P4.1 v2 config does not match the pre-registered SHA-256",
    )
    schedule = _mapping(document.get("schedule"))
    acceptance = _mapping(document.get("acceptance"))
    sources = _mapping(document.get("sources"))
    provenance = _mapping(document.get("provenance"))
    runtime = _mapping(document.get("runtime"))
    safety = _mapping(document.get("safety"))
    phase_gate = _mapping(document.get("phase_gate"))
    jobrun = _mapping(document.get("jobrun_contract"))
    for name, value in (
        ("schedule", schedule),
        ("acceptance", acceptance),
        ("sources", sources),
        ("provenance", provenance),
        ("runtime", runtime),
        ("safety", safety),
        ("phase_gate", phase_gate),
        ("jobrun_contract", jobrun),
    ):
        _require(value is not None, f"config.{name} must be a mapping")
    assert schedule is not None
    assert acceptance is not None
    assert sources is not None
    assert provenance is not None
    assert runtime is not None
    assert safety is not None
    assert phase_gate is not None
    assert jobrun is not None

    _require(schedule.get("timezone") == "Asia/Shanghai", "schedule timezone must be Shanghai")
    _require(schedule.get("scheduler_tick_minutes") == 10, "scheduler tick must be 10 minutes")
    _require(schedule.get("off_session_poll_minutes") == 30, "off-session poll must be 30 minutes")
    monday = _mapping(schedule.get("monday_host_gap_policy"))
    _require(monday is not None, "v2 Monday gap policy missing")
    assert monday is not None
    suppressed = monday.get("intentionally_suppressed_slots_shanghai")
    _require(isinstance(suppressed, list) and bool(suppressed), "v2 suppressed slots missing")
    assert isinstance(suppressed, list)
    for value in suppressed:
        _parse_clock(value)
    _parse_clock(monday.get("recovery_catchup_slot_shanghai"))
    _require(
        monday.get("missing_suppressed_slots_are_not_expected_slots") is True,
        "v2 suppressed slots must be removed from the cadence contract",
    )
    _require(
        runtime.get("scheduler_enabled_env") == "ALPHAPILOT_NEWS_POLL_ENABLED",
        "news-poll scheduler env contract drifted",
    )
    _require(runtime.get("scheduler_enabled_default") is False, "news poll must default off")
    _require(
        runtime.get("dedicated_scheduler_launchd_value") is True,
        "dedicated scheduler must explicitly enable news poll",
    )

    frozen_dates = acceptance.get("trading_dates")
    _require(isinstance(frozen_dates, list) and bool(frozen_dates), "v2 trading dates missing")
    assert isinstance(frozen_dates, list)
    parsed_dates = [date.fromisoformat(str(value)) for value in frozen_dates]
    _require(len(set(parsed_dates)) == len(parsed_dates), "v2 trading dates contain duplicates")
    expected_by_date = _mapping(acceptance.get("expected_poll_slots_by_trading_date"))
    _require(expected_by_date is not None, "v2 per-date slot contract missing")
    assert expected_by_date is not None
    _require(
        set(expected_by_date) == {value.isoformat() for value in parsed_dates},
        "v2 per-date slot contract does not match trading dates",
    )
    expected_total = 0
    for raw_count in expected_by_date.values():
        expected_count = _nonnegative_integer(raw_count)
        if expected_count is None:
            raise ValueError("v2 slot counts invalid")
        expected_total += expected_count
    _require(
        acceptance.get("expected_poll_slots_total") == expected_total,
        "v2 total slot contract does not reconcile",
    )
    acceptance_fields = [
        "require_every_expected_slot",
        "require_every_matched_slot_status_ok",
        "degraded_slot_fails_operational_gate",
        "require_cninfo_inserted_each_trading_date",
        "require_cninfo_query_end_date_matches_shanghai_market_date",
        "require_logical_and_physical_request_accounting",
    ]
    acceptance_fields.append(
        "require_cninfo_daily_slice_checkpoint_contract"
        if document.get("schema_version") == "p4.1-news-poll-v2.1"
        else "require_cninfo_per_column_checkpoint_contract"
    )
    for field in acceptance_fields:
        _require(acceptance.get(field) is True, f"v2 acceptance.{field} must remain true")
    _require(
        acceptance.get("allow_unexpected_extra_poll_runs") is False,
        "extra poll runs must remain forbidden",
    )
    daily_template = acceptance.get("daily_report_template")
    final_report = acceptance.get("final_report")
    _require(
        isinstance(daily_template, str) and "{date_compact}" in daily_template,
        "v2 daily report template invalid",
    )
    _require(isinstance(final_report, str) and bool(final_report), "v2 final report path invalid")
    _require(acceptance.get("reports_are_create_only") is True, "v2 reports must be create-only")
    _require(
        acceptance.get("prior_v1_reports_must_not_be_modified") is True,
        "v1 reports must remain immutable",
    )

    enabled = {"cninfo", "sina_company_news", "akshare_ths"}
    for source_id in enabled:
        source = _mapping(sources.get(source_id))
        _require(source is not None, f"missing source {source_id}")
        assert source is not None
        _require(source.get("enabled") is True, f"{source_id} must remain enabled")
        _require(source.get("audited") is True, f"{source_id} must remain audited")
        _require(
            _nonnegative_integer(source.get("max_logical_requests_per_run")) is not None,
            f"{source_id} logical budget missing",
        )
        _require(
            _nonnegative_integer(source.get("max_physical_attempts_per_run")) is not None,
            f"{source_id} physical budget missing",
        )
    cninfo = _mapping(sources.get("cninfo"))
    assert cninfo is not None
    _require(cninfo.get("critical") is True, "cninfo must remain critical")
    _require(cninfo.get("verify_tls") is True, "cninfo TLS verification must remain enabled")
    _require(cninfo.get("require_https") is True, "cninfo HTTPS must remain mandatory")
    columns = cninfo.get("columns")
    _require(
        isinstance(columns, list)
        and bool(columns)
        and len({str(value) for value in columns}) == len(columns),
        "cninfo columns invalid",
    )

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
            source.get("max_logical_requests_per_run") == 0,
            f"{source_id} logical budget must be zero",
        )
        _require(
            source.get("max_physical_attempts_per_run") == 0,
            f"{source_id} physical budget must be zero",
        )

    _require(
        provenance.get("audited_news_sources") == ["akshare_ths", "cninfo", "sina_company_news"],
        "audited news-source allowlist drifted",
    )
    _require(provenance.get("symbol_nullable") is True, "symbol must remain nullable")
    _require(provenance.get("published_at_nullable") is True, "published_at must remain nullable")
    _require(
        provenance.get("available_time_policy") == "write_locked_immediately_before_flush_utc",
        "PIT policy drifted",
    )
    _require(provenance.get("decision_visibility_operator") == "<", "PIT operator must be strict")
    _require(
        provenance.get("catchup_rows_keep_actual_available_time") is True,
        "catchup PIT drifted",
    )

    _require(
        jobrun.get("terminal_statuses") == ["ok", "degraded", "failed"],
        "v2 statuses drifted",
    )
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
    if document.get("schema_version") == "p4.1-news-poll-v2.1":
        _require(
            safety.get("required_paper_trading_enabled") is False,
            "paper trading must be disabled for v2.1",
        )
        _require(
            safety.get("required_futu_trade_enabled") is False,
            "Futu trade must be disabled for v2.1",
        )
    _require(phase_gate.get("p4_2b_production_wiring_unlocked") is False, "P4.2b unlocked")
    _require(phase_gate.get("p4_3_unlocked") is False, "P4.3 unlocked")
    return FrozenConfig(
        path=resolved,
        sha256=config_sha256,
        document=document,
        receipt_path=receipt_path,
        receipt_sha256=receipt_sha256,
    )


def load_config(path: Path) -> FrozenConfig:
    resolved = path.resolve()
    loaded: object = yaml.safe_load(resolved.read_bytes())
    document = _mapping(loaded)
    if document is None:
        raise ValueError("P4.1 news-poll config must be a mapping")

    if str(document.get("schema_version", "")).startswith(V2_SCHEMA_VERSION):
        return _load_v2_config(resolved, document)
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
        acceptance.get("require_cninfo_inserted_each_trading_date") is True,
        "daily CNInfo insertion gate disabled",
    )
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
        provenance.get("available_time_policy") == "write_locked_immediately_before_flush_utc",
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


def _validate_context_network_and_gate(document: JsonObject) -> None:
    network = _mapping(document.get("reporter_network_policy"))
    gate_policy = _mapping(document.get("gate_policy"))
    _require(network is not None, "observation context network policy missing")
    _require(gate_policy is not None, "observation context gate policy missing")
    assert network is not None
    assert gate_policy is not None
    _require(
        network.get("network_calls_performed_by_acceptance_runner") is False,
        "acceptance runner must not claim external network access",
    )
    _require(
        network.get("network_calls_permitted_during_report_generation") is False,
        "acceptance report generation must remain offline",
    )
    for field in (
        "does_not_override_expected_slots_complete",
        "does_not_override_critical_source_failures_zero",
        "does_not_turn_operational_failures_green",
    ):
        _require(gate_policy.get(field) is True, f"observation context gate policy {field} drifted")


def _validate_host_intervals(document: JsonObject) -> None:
    intervals = document.get("host_unavailability_intervals")
    _require(isinstance(intervals, list), "host-unavailability intervals must be a list")
    assert isinstance(intervals, list)
    for index, raw_interval in enumerate(intervals):
        interval = _mapping(raw_interval)
        _require(interval is not None, f"host-unavailability interval {index} must be an object")
        assert interval is not None
        start = _aware_datetime(interval.get("start_shanghai"))
        end = _aware_datetime(interval.get("end_shanghai"))
        _require(start is not None and end is not None and start <= end, "invalid host interval")
        _require(
            interval.get("classification") == "host_unreachable",
            "host interval classification drifted",
        )


def _load_v2_observation_context(
    resolved: Path,
    document: JsonObject,
    config: FrozenConfig,
) -> ObservationContext:
    _require(
        document.get("schema_version") == "p4.1-v2-observation-context-v1",
        "unexpected v2 observation-context schema_version",
    )
    _require(
        document.get("frozen_config_sha256") == config.sha256,
        "observation context is not bound to the frozen P4.1 v2 config",
    )
    _validate_context_network_and_gate(document)
    _validate_host_intervals(document)
    acceptance = _mapping(config.document.get("acceptance")) or {}
    _require(
        document.get("observation_start_utc") == acceptance.get("observation_start_utc")
        and document.get("observation_end_utc") == acceptance.get("observation_end_utc"),
        "v2 observation context window does not match the frozen config",
    )
    _require(
        document.get("v1_external_attestations_reused") is False,
        "v2 context must not reuse v1 external attestations",
    )
    return ObservationContext(path=resolved, sha256=_sha256(resolved), document=document)


def load_observation_context(path: Path, config: FrozenConfig) -> ObservationContext:
    resolved = path.resolve()
    loaded: object = json.loads(resolved.read_text(encoding="utf-8"))
    document = _mapping(loaded)
    if document is None:
        raise ValueError("P4.1 observation context must be a JSON object")
    if _is_v2(config):
        return _load_v2_observation_context(resolved, document, config)
    _require(
        document.get("schema_version") == "p4.1-observation-context-v1",
        "unexpected observation-context schema_version",
    )
    _require(
        document.get("frozen_config_sha256") == config.sha256,
        "observation context is not bound to the frozen P4.1 config",
    )
    direct = _mapping(document.get("direct_reachability_attestation"))
    continuity = _mapping(document.get("publication_continuity_attestation"))
    watermark = _mapping(document.get("watermark_semantics_target"))
    _require(direct is not None, "direct reachability attestation missing")
    _require(continuity is not None, "publication continuity attestation missing")
    _require(watermark is not None, "watermark semantics target missing")
    assert direct is not None
    assert continuity is not None
    assert watermark is not None
    _validate_context_network_and_gate(document)
    _validate_host_intervals(document)
    _require(direct.get("http_status") == 200, "direct reachability attestation must record 200")
    _require(
        direct.get("does_not_override_jobrun_failures") is True,
        "direct reachability attestation must not override JobRun failures",
    )
    _require(
        continuity.get("window_start_operator") == ">"
        and continuity.get("window_end_operator") == "<",
        "publication continuity window must remain strict-open",
    )
    _require(
        _aware_datetime(continuity.get("window_start_utc")) is not None
        and _aware_datetime(continuity.get("window_end_utc")) is not None,
        "publication continuity window timestamps must be timezone-aware",
    )
    _require(
        _nonnegative_integer(continuity.get("expected_local_strict_row_count")) is not None,
        "publication continuity expected count must be non-negative",
    )
    _require(
        continuity.get("local_rows_alone_prove_upstream_completeness") is False,
        "local rows must not claim to prove upstream completeness",
    )
    _require(
        continuity.get("does_not_override_jobrun_failures") is True,
        "continuity attestation must not override JobRun failures",
    )
    _require(
        _aware_datetime(watermark.get("target_slot_shanghai")) is not None,
        "watermark target slot must be timezone-aware",
    )
    _require(
        watermark.get("query_end_date_expression") == "now.date()"
        and watermark.get("runtime_now_timezone") == "UTC"
        and watermark.get("expected_market_timezone") == "Asia/Shanghai"
        and watermark.get("defect_classification") == "utc_cst_query_window_date_defect",
        "watermark query-date root-cause evidence drifted",
    )
    return ObservationContext(path=resolved, sha256=_sha256(resolved), document=document)


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
    if _is_v2(config):
        monday = _mapping(schedule.get("monday_host_gap_policy")) or {}
        if target.weekday() == 0:
            suppressed = {
                _parse_clock(value)
                for value in monday.get("intentionally_suppressed_slots_shanghai", [])
            }
            result = [slot for slot in result if slot.time() not in suppressed]
        acceptance = _mapping(config.document.get("acceptance")) or {}
        expected_by_date = _mapping(acceptance.get("expected_poll_slots_by_trading_date")) or {}
        expected_count = _nonnegative_integer(expected_by_date.get(target.isoformat()))
        if expected_count is None or len(result) != expected_count:
            raise ValueError(
                f"v2 expected slot derivation mismatch for {target.isoformat()}: "
                f"derived={len(result)}, frozen={expected_count!r}"
            )
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
        poll_started = matched.poll_started_at
        poll_completed = matched.poll_completed_at
        matches.append(
            {
                "slot": slot.isoformat(),
                "slot_utc": slot_utc.isoformat(),
                "job_run_id": matched.run_id,
                "job_status": matched.status,
                "job_error": matched.error,
                "poll_started_at_utc": (
                    poll_started.astimezone(UTC).isoformat() if poll_started is not None else None
                ),
                "poll_started_at_shanghai": (
                    poll_started.astimezone(SHANGHAI).isoformat()
                    if poll_started is not None
                    else None
                ),
                "poll_completed_at_utc": (
                    poll_completed.astimezone(UTC).isoformat()
                    if poll_completed is not None
                    else None
                ),
                "poll_completed_at_shanghai": (
                    poll_completed.astimezone(SHANGHAI).isoformat()
                    if poll_completed is not None
                    else None
                ),
                "start_delta_seconds": round(delta, 6),
            }
        )
    missing_details = [
        {
            "slot": value,
            "slot_utc": datetime.fromisoformat(value).astimezone(UTC).isoformat(),
        }
        for value in missing
    ]
    return {
        "expected_slots": len(expected),
        "matched_slots": len(matches),
        "missing_slots": missing,
        "missing_slot_details": missing_details,
        "unexpected_run_ids": [run.run_id for run in remaining],
        "matches": matches,
    }


def _host_context_causes(context: ObservationContext, slot: datetime) -> list[JsonObject]:
    raw_intervals = context.document.get("host_unavailability_intervals")
    intervals = raw_intervals if isinstance(raw_intervals, list) else []
    causes: list[JsonObject] = []
    slot_utc = slot.astimezone(UTC)
    for raw_interval in intervals:
        interval = _mapping(raw_interval)
        if interval is None:
            continue
        start = _aware_datetime(interval.get("start_shanghai"))
        end = _aware_datetime(interval.get("end_shanghai"))
        if start is None or end is None or not (start <= slot_utc <= end):
            continue
        causes.append(
            {
                "classification": "host_unreachable",
                "label_zh": "宿主不可达",
                "evidence_basis": "external_owner_pmset_interval",
                "evidence_id": interval.get("evidence_id"),
                "interval_start_utc": start.isoformat(),
                "interval_end_utc": end.isoformat(),
                "interval_start_shanghai": start.astimezone(SHANGHAI).isoformat(),
                "interval_end_shanghai": end.astimezone(SHANGHAI).isoformat(),
            }
        )
    return causes


def _failure_observations(run: JobEvidence) -> list[JsonObject]:
    raw_sources = _mapping(run.stats.get("sources")) or {}
    observations: list[JsonObject] = []
    for source_id, raw_source in sorted(raw_sources.items()):
        source = _mapping(raw_source)
        if source is None:
            continue
        raw_failures = source.get("failures")
        failures = raw_failures if isinstance(raw_failures, list) else []
        for raw_failure in failures:
            failure = _mapping(raw_failure)
            if failure is None:
                continue
            raw_requests = source.get("requests")
            requests = raw_requests if isinstance(raw_requests, list) else []
            observations.append(
                {
                    "source_id": source_id,
                    "source_status": source.get("status"),
                    "code": failure.get("code"),
                    "blocked": failure.get("blocked"),
                    "error_type": failure.get("error_type"),
                    "message": failure.get("message"),
                    "symbol": failure.get("symbol"),
                    "request_count": source.get("request_count"),
                    "retry_count": source.get("retry_count"),
                    "request_failure_codes": [
                        request.get("failure_code")
                        for raw_request in requests
                        if (request := _mapping(raw_request)) is not None
                    ],
                }
            )
    return observations


def _classified_failure_causes(
    config: FrozenConfig,
    failures: Sequence[JsonObject],
) -> list[JsonObject]:
    config_sources = _mapping(config.document.get("sources")) or {}
    grouped: dict[str, JsonObject] = {}
    labels = {
        "pagination_capacity_watermark_deadlock": "翻页容量/水位死锁",
        "retry_budget_semantics_defect": "重试预算缺陷",
        "upstream_unavailable": "上游不可用",
        "unclassified": "未分类",
    }
    for failure in failures:
        source_id = str(failure.get("source_id") or "")
        code = str(failure.get("code") or "")
        evidence_basis = "jobrun_explicit_failure"
        if code == "pagination_incomplete":
            classification = "pagination_capacity_watermark_deadlock"
        elif code == "request_budget_exhausted":
            source_contract = _mapping(config_sources.get(source_id)) or {}
            request_failure_codes = failure.get("request_failure_codes")
            has_transport_failure = isinstance(request_failure_codes, list) and (
                "transport_error" in request_failure_codes
            )
            incompatible_budget = (
                source_contract.get("max_requests_per_run") == 1
                and source_contract.get("max_attempts_per_request") == 2
            )
            if source_id == "akshare_ths" and has_transport_failure and incompatible_budget:
                classification = "retry_budget_semantics_defect"
                evidence_basis = "jobrun_request_trace_plus_frozen_budget_contract"
            else:
                classification = "unclassified"
        elif code == "transport_error":
            classification = "upstream_unavailable"
            evidence_basis = "client_observed_transport_unavailability"
        else:
            classification = "unclassified"
        item = grouped.setdefault(
            classification,
            {
                "classification": classification,
                "label_zh": labels[classification],
                "evidence_basis": evidence_basis,
                "source_ids": [],
                "failure_codes": [],
                "caveat": (
                    "client-observed transport failure does not independently prove "
                    "an upstream-wide outage"
                    if classification == "upstream_unavailable"
                    else None
                ),
            },
        )
        source_ids = item["source_ids"]
        failure_codes = item["failure_codes"]
        assert isinstance(source_ids, list)
        assert isinstance(failure_codes, list)
        if source_id and source_id not in source_ids:
            source_ids.append(source_id)
        if code and code not in failure_codes:
            failure_codes.append(code)
    return [grouped[key] for key in sorted(grouped)]


def _slot_detail(
    *,
    config: FrozenConfig,
    context: ObservationContext,
    slot: datetime,
    run: JobEvidence | None,
) -> JsonObject:
    failures = _failure_observations(run) if run is not None else []
    causes = _host_context_causes(context, slot)
    causes.extend(_classified_failure_causes(config, failures))
    if not causes:
        causes.append(
            {
                "classification": "unclassified",
                "label_zh": "未分类",
                "evidence_basis": "no_matching_external_or_jobrun_evidence",
            }
        )
    return {
        "slot_shanghai": slot.astimezone(SHANGHAI).isoformat(),
        "slot_utc": slot.astimezone(UTC).isoformat(),
        "job_run_id": run.run_id if run is not None else None,
        "job_status": run.status if run is not None else "missing",
        "job_error": run.error if run is not None else None,
        "poll_started_at": (
            _dual_timestamp(run.poll_started_at)
            if run is not None and run.poll_started_at is not None
            else None
        ),
        "source_failures": failures,
        "root_causes": causes,
    }


def _coverage_audit(
    config: FrozenConfig,
    dates: Sequence[date],
    runs: Sequence[JobEvidence],
    cadence: JsonObject,
    context: ObservationContext,
) -> JsonObject:
    runs_by_id = {run.run_id: run for run in runs}
    raw_matches = cadence.get("matches")
    matches = raw_matches if isinstance(raw_matches, list) else []
    match_by_slot: dict[str, JobEvidence] = {}
    for raw_match in matches:
        match = _mapping(raw_match)
        if match is None:
            continue
        run_id = match.get("job_run_id")
        slot = match.get("slot")
        if isinstance(run_id, int) and isinstance(slot, str) and run_id in runs_by_id:
            match_by_slot[slot] = runs_by_id[run_id]

    days: dict[str, JsonObject] = {}
    root_cause_counts: dict[str, int] = {}
    unclassified_slots: list[str] = []
    for target in dates:
        expected = expected_poll_slots(config, target)
        successful = 0
        failed = 0
        nonterminal = 0
        failed_details: list[JsonObject] = []
        missing_details: list[JsonObject] = []
        for slot in expected:
            run = match_by_slot.get(slot.isoformat())
            if run is None:
                detail = _slot_detail(config=config, context=context, slot=slot, run=None)
                missing_details.append(detail)
            elif run.status == "ok":
                successful += 1
                continue
            else:
                if run.status == "failed":
                    failed += 1
                else:
                    nonterminal += 1
                detail = _slot_detail(config=config, context=context, slot=slot, run=run)
                failed_details.append(detail)

            causes = detail["root_causes"]
            assert isinstance(causes, list)
            classifications = {
                str(cause.get("classification"))
                for raw_cause in causes
                if (cause := _mapping(raw_cause)) is not None
            }
            for classification in classifications:
                root_cause_counts[classification] = root_cause_counts.get(classification, 0) + 1
            if not classifications or "unclassified" in classifications:
                unclassified_slots.append(slot.isoformat())

        matched = len(expected) - len(missing_details)
        trading_failed = [
            detail
            for detail in failed_details
            if time(9, 25)
            <= datetime.fromisoformat(str(detail["slot_shanghai"])).time()
            <= time(15, 5)
        ]
        trading_missing = [
            detail
            for detail in missing_details
            if time(9, 25)
            <= datetime.fromisoformat(str(detail["slot_shanghai"])).time()
            <= time(15, 5)
        ]
        start = datetime.combine(target, time.min, tzinfo=SHANGHAI)
        end = start + timedelta(days=1)
        days[target.isoformat()] = {
            "window_start": _dual_timestamp(start),
            "window_end": _dual_timestamp(end),
            "expected_slots": len(expected),
            "matched_slots": matched,
            "successful_slots": successful,
            "failed_slots": failed,
            "nonterminal_slots": nonterminal,
            "missing_slots": len(missing_details),
            "execution_coverage": round(matched / len(expected), 6),
            "success_coverage": round(successful / len(expected), 6),
            "operational_coverage_status": (
                "pass" if matched == len(expected) and successful == len(expected) else "fail"
            ),
            "failed_slot_details": failed_details,
            "missing_slot_details": missing_details,
            "trading_window": {
                "definition": "09:25 <= expected slot <= 15:05 Asia/Shanghai",
                "failed_slot_details": trading_failed,
                "missing_slot_details": trading_missing,
            },
        }
    return {
        "timezone_basis": "Asia/Shanghai",
        "stored_timestamp_basis": "UTC",
        "days": days,
        "root_cause_counts": dict(sorted(root_cause_counts.items())),
        "unclassified_slots": unclassified_slots,
        "root_cause_accounting_complete": not unclassified_slots,
        "operational_all_success": all(
            day["operational_coverage_status"] == "pass" for day in days.values()
        ),
        "gate_separation": {
            "coverage_is_descriptive": True,
            "expected_slots_complete_gate_preserved": True,
            "critical_source_failures_zero_gate_preserved": True,
        },
    }


def _source_audit(
    config: FrozenConfig,
    dates: Sequence[date],
    runs: Sequence[JobEvidence],
) -> JsonObject:
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
    cninfo_inserted_by_trading_date = {value.isoformat(): 0 for value in dates}
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
            if source_id == "cninfo" and run.poll_started_at is not None:
                trading_date = run.poll_started_at.astimezone(SHANGHAI).date().isoformat()
                inserted = counters.get("inserted")
                if inserted is not None and trading_date in cninfo_inserted_by_trading_date:
                    cninfo_inserted_by_trading_date[trading_date] += inserted
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
        "cninfo_inserted_by_trading_date": cninfo_inserted_by_trading_date,
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
                run.started_at <= run.poll_started_at <= run.poll_completed_at <= run.finished_at
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


SENSITIVE_EVIDENCE_TOKENS = (
    "payload",
    "authorization",
    "token",
    "secret",
    "password",
)
SENSITIVE_EVIDENCE_EXACT_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_key",
        "credential",
        "cookie",
        "set_cookie",
        "session_cookie",
        "private_key",
    }
)


def _normalized_evidence_key(value: object) -> str:
    return "_".join(
        part
        for part in "".join(
            character.lower() if character.isalnum() else " " for character in str(value)
        ).split()
    )


def _is_sensitive_evidence_key(value: object) -> bool:
    normalized = _normalized_evidence_key(value)
    compact = normalized.replace("_", "")
    return (
        normalized in SENSITIVE_EVIDENCE_EXACT_KEYS
        or any(token in normalized for token in SENSITIVE_EVIDENCE_TOKENS)
        or any(token in compact for token in ("apikey", "accesskey", "privatekey", "sessioncookie"))
        or "credential" in compact
        or "cookie" in compact
    )


def _sensitive_evidence_paths(value: object, path: str = "$") -> list[str]:
    issues: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if _is_sensitive_evidence_key(key):
                issues.append(child)
            issues.extend(_sensitive_evidence_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            issues.extend(_sensitive_evidence_paths(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        lowered = value.lower()
        if any(token in lowered for token in SENSITIVE_EVIDENCE_TOKENS):
            issues.append(path)
    return issues


def _sanitize_v2_evidence(value: object) -> object:
    if isinstance(value, Mapping):
        sanitized: JsonObject = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_evidence_key(key_text):
                continue
            sanitized[key_text] = _sanitize_v2_evidence(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_v2_evidence(item) for item in value]
    if isinstance(value, str) and any(
        token in value.lower() for token in SENSITIVE_EVIDENCE_TOKENS
    ):
        return "[REDACTED]"
    return value


def _sanitize_v2_report(report: JsonObject) -> JsonObject:
    sanitized = _sanitize_v2_evidence(report)
    assert isinstance(sanitized, dict)
    return sanitized


def _v2_terminal_diagnostic_issues(run: JobEvidence) -> list[str]:
    prefix = f"JobRun {run.run_id}"
    diagnostic = _mapping(run.stats.get("terminal_diagnostics"))
    if run.status == "ok":
        return [] if diagnostic is None else [f"{prefix}: ok row has terminal_diagnostics"]
    if diagnostic is None:
        return [f"{prefix}: {run.status} row lacks terminal_diagnostics"]
    issues: list[str] = []
    for field in ("code", "source", "constraint"):
        value = diagnostic.get(field)
        if not isinstance(value, str) or not value.strip():
            issues.append(f"{prefix}: terminal_diagnostics.{field} missing")
    if _sensitive_evidence_paths(diagnostic):
        issues.append(f"{prefix}: terminal_diagnostics contains sensitive evidence")
    return issues


def _v2_jobrun_audit(config: FrozenConfig, runs: Sequence[JobEvidence]) -> JsonObject:
    contract = _mapping(config.document.get("jobrun_contract")) or {}
    terminal_statuses = {str(value) for value in contract.get("terminal_statuses", [])}
    required_stats = {str(value) for value in contract.get("required_top_level_stats", [])}
    expected_settings = {
        "trading_mode": "research",
        "live_trading_enabled": False,
        "paper_auto_trading_enabled": False,
        "futu_enable_account_mutation": False,
        "unlock_trade_permanently_blocked": True,
    }
    if _is_v2_1(config):
        expected_settings.update(
            {
                "paper_trading_enabled": False,
                "futu_enable_trade": False,
            }
        )
    issues: list[str] = []
    evidence: list[JsonObject] = []
    status_counts = {status: 0 for status in sorted(terminal_statuses)}
    for run in runs:
        prefix = f"JobRun {run.run_id}"
        if run.status not in terminal_statuses:
            issues.append(f"{prefix}: non-terminal status {run.status!r}")
        else:
            status_counts[run.status] += 1
        if run.status == "failed" and not (run.error or "").strip():
            issues.append(f"{prefix}: failed row has no error")
        if run.error is not None:
            if len(run.error) > 500:
                issues.append(f"{prefix}: error exceeds 500 characters")
            if _sensitive_evidence_paths(run.error, "$.error"):
                issues.append(f"{prefix}: error contains sensitive evidence")
        if run.status in {"ok", "degraded"} and run.error is not None:
            issues.append(f"{prefix}: {run.status} row must have NULL error")
        issues.extend(_v2_terminal_diagnostic_issues(run))
        if run.stats.get("config_sha256") != config.sha256:
            issues.append(f"{prefix}: config_sha256 mismatch")
        if _is_v2_1(config) and run.stats.get("config_version") != config.document.get(
            "schema_version"
        ):
            issues.append(f"{prefix}: config_version mismatch")
        for field in sorted(required_stats):
            if field not in run.stats:
                issues.append(f"{prefix}: required stats.{field} missing")
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
                run.started_at <= run.poll_started_at <= run.poll_completed_at <= run.finished_at
            )
        ):
            issues.append(f"{prefix}: poll timestamps fall outside durable JobRun")
        run_mode = run.stats.get("run_mode")
        coverage_gap = run.stats.get("coverage_gap")
        if run_mode not in {"regular_incremental", "coverage_gap_catchup"}:
            issues.append(f"{prefix}: unsupported run_mode {run_mode!r}")
        if not isinstance(coverage_gap, bool):
            issues.append(f"{prefix}: coverage_gap must be boolean")
        elif (run_mode == "coverage_gap_catchup") != coverage_gap:
            issues.append(f"{prefix}: run_mode and coverage_gap disagree")
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
                "terminal": run.status in terminal_statuses,
                "accepted_as_operational_success": run.status == "ok",
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
    return {
        "issues": issues,
        "terminal_statuses": sorted(terminal_statuses),
        "status_counts": status_counts,
        "all_terminal": all(run.status in terminal_statuses for run in runs),
        "all_operational_status_ok": all(run.status == "ok" for run in runs),
        "evidence": evidence,
    }


def _v2_checkpoint_issues(
    *,
    run: JobEvidence,
    column: str,
    checkpoint: JsonObject,
    watermark_contract: JsonObject,
) -> list[str]:
    prefix = f"JobRun {run.run_id}: cninfo.{column}"
    issues: list[str] = []
    before = _aware_datetime(checkpoint.get("verified_watermark_before_utc"))
    floor = _aware_datetime(checkpoint.get("verified_watermark_floor_utc"))
    newest = _aware_datetime(checkpoint.get("newest_observed_at_utc"))
    after = _aware_datetime(checkpoint.get("verified_watermark_after_utc"))
    if None in (before, floor, newest, after):
        issues.append(f"{prefix}: checkpoint timestamps must be timezone-aware")
        return issues
    assert before is not None
    assert floor is not None
    assert newest is not None
    assert after is not None
    if not (floor <= before <= newest):
        issues.append(f"{prefix}: floor/before/newest ordering invalid")
    complete = checkpoint.get("pagination_complete")
    committed = checkpoint.get("checkpoint_committed")
    page_cap_hit = checkpoint.get("page_cap_hit")
    for field, value in (
        ("pagination_complete", complete),
        ("checkpoint_committed", committed),
        ("page_cap_hit", page_cap_hit),
        ("attempted", checkpoint.get("attempted")),
        (
            "skipped_due_to_prior_critical_failure",
            checkpoint.get("skipped_due_to_prior_critical_failure"),
        ),
    ):
        if not isinstance(value, bool):
            issues.append(f"{prefix}: {field} must be boolean")
    complete_policy = watermark_contract.get("complete_column_advancement")
    incomplete_policy = watermark_contract.get("incomplete_column_advancement")
    if complete is True:
        if committed is not True or page_cap_hit is not False or after != newest:
            issues.append(f"{prefix}: complete checkpoint was not committed to newest_observed")
        if complete_policy != "newest_observed_at_utc":
            issues.append(f"{prefix}: unsupported complete advancement contract")
    elif incomplete_policy == "unchanged":
        if committed is True:
            issues.append(f"{prefix}: unchanged incomplete checkpoint must not be committed")
        if after != before:
            issues.append(f"{prefix}: unchanged incomplete checkpoint advanced")
    elif complete is False:
        boundary_field = watermark_contract.get("incomplete_verified_boundary_field")
        if not isinstance(boundary_field, str) and isinstance(incomplete_policy, str):
            boundary_field = incomplete_policy
        boundary = (
            _aware_datetime(checkpoint.get(boundary_field))
            if isinstance(boundary_field, str)
            else None
        )
        if boundary is None:
            issues.append(f"{prefix}: incomplete advancement lacks a verified boundary field")
        elif after != boundary:
            issues.append(f"{prefix}: checkpoint does not match verified partial boundary")
        expected_committed = watermark_contract.get("incomplete_checkpoint_committed")
        if isinstance(expected_committed, bool) and committed is not expected_committed:
            issues.append(f"{prefix}: partial checkpoint committed flag disagrees with config")
        if not (before <= after <= newest):
            issues.append(f"{prefix}: partial checkpoint is outside observed bounds")
    if after > newest:
        issues.append(f"{prefix}: checkpoint exceeds newest_observed")
    return issues


def _v2_1_cninfo_run_audit(
    config: FrozenConfig,
    run: JobEvidence,
    observed: JsonObject,
) -> tuple[list[str], JsonObject, JsonObject]:
    prefix = "cninfo"
    sources = _mapping(config.document.get("sources")) or {}
    contract = _mapping(sources.get("cninfo")) or {}
    jobrun_contract = _mapping(config.document.get("jobrun_contract")) or {}
    required_source = {
        str(value) for value in jobrun_contract.get("cninfo_required_source_stats", [])
    }
    required_slice = {str(value) for value in jobrun_contract.get("slice_required_fields", [])}
    issues: list[str] = []
    for field in sorted(required_source):
        if field not in observed:
            issues.append(f"{prefix}: required {field} missing")
    canonical = str(contract.get("canonical_column"))
    if canonical != "szse" or contract.get("columns") != ["szse"]:
        issues.append(f"{prefix}: frozen canonical column contract is not szse-only")
    if observed.get("canonical_column") != canonical:
        issues.append(f"{prefix}: canonical_column mismatch")
    if observed.get("status") != "ok":
        issues.append(f"{prefix}: source status {observed.get('status')!r} is not ok")
    if observed.get("tls_verification") is not True:
        issues.append(f"{prefix}: TLS verification is not true")
    market_date = (
        run.poll_started_at.astimezone(SHANGHAI).date() if run.poll_started_at is not None else None
    )
    market_field = observed.get("market_date_at_poll")
    if market_date is None or market_field != market_date.isoformat():
        issues.append(f"{prefix}: market_date_at_poll is not the Shanghai poll date")
    if _aware_datetime(observed.get("poll_started_at_utc")) != run.poll_started_at:
        issues.append(f"{prefix}: poll_started_at_utc mismatch")

    slices_raw = observed.get("slices")
    slices = slices_raw if isinstance(slices_raw, list) else []
    slice_dates_raw = observed.get("slice_dates_shanghai")
    slice_dates = slice_dates_raw if isinstance(slice_dates_raw, list) else []
    if not slices:
        issues.append(f"{prefix}: slices must be non-empty")
    parsed_dates: list[date] = []
    logical_sum = 0
    physical_sum = 0
    fetched_sum = 0
    slice_evidence: list[JsonObject] = []
    for index, raw_slice in enumerate(slices):
        item = _mapping(raw_slice)
        if item is None:
            issues.append(f"{prefix}: slice {index} is not an object")
            continue
        for field in sorted(required_slice):
            if field not in item:
                issues.append(f"{prefix}: slice {index}.{field} missing")
        try:
            slice_date = date.fromisoformat(str(item.get("date_shanghai")))
        except ValueError:
            issues.append(f"{prefix}: slice {index} date is invalid")
            continue
        parsed_dates.append(slice_date)
        date_closed = item.get("date_closed")
        expected_closed = market_date is not None and slice_date < market_date
        expected_mode = (
            "closed_date_reconciliation" if expected_closed else "current_date_incremental"
        )
        if date_closed is not expected_closed or item.get("mode") != expected_mode:
            issues.append(f"{prefix}: slice {slice_date} mode/date_closed mismatch")
        incremental_floor = _aware_datetime(item.get("incremental_floor_utc"))
        if expected_closed and item.get("incremental_floor_utc") is not None:
            issues.append(f"{prefix}: closed slice {slice_date} has an incremental floor")
        if not expected_closed and incremental_floor is None:
            issues.append(f"{prefix}: current slice {slice_date} lacks an incremental floor")
        page_count = _nonnegative_integer(item.get("page_count"))
        logical = _nonnegative_integer(item.get("logical_request_count"))
        physical = _nonnegative_integer(item.get("physical_attempt_count"))
        fetched = _nonnegative_integer(item.get("fetched"))
        if None in (page_count, logical, physical, fetched):
            issues.append(f"{prefix}: slice {slice_date} request counters invalid")
        else:
            assert page_count is not None
            assert logical is not None
            assert physical is not None
            assert fetched is not None
            logical_sum += logical
            physical_sum += physical
            fetched_sum += fetched
            if page_count != logical or physical < logical:
                issues.append(f"{prefix}: slice {slice_date} request accounting mismatch")
            if page_count > int(contract.get("max_pages_per_day", 0)):
                issues.append(f"{prefix}: slice {slice_date} exceeds max_pages_per_day")
        pagination_complete = item.get("pagination_complete")
        coverage_proven = item.get("coverage_proven")
        checkpoint_committed = item.get("checkpoint_committed")
        page_cap_hit = item.get("page_cap_hit")
        if (
            item.get("attempted") is not True
            or pagination_complete is not True
            or coverage_proven is not True
            or checkpoint_committed is not True
            or page_cap_hit is not False
            or item.get("failure") is not None
        ):
            issues.append(f"{prefix}: slice {slice_date} is incomplete or failed")
        newest = _aware_datetime(item.get("newest_observed_at_utc"))
        if fetched and newest is None:
            issues.append(f"{prefix}: slice {slice_date} fetched rows without observed high")
        if newest is not None and newest.astimezone(SHANGHAI).date() != slice_date:
            issues.append(f"{prefix}: slice {slice_date} observed high is outside its CST day")
        slice_evidence.append({**item, "issues": []})
    if [value.isoformat() for value in parsed_dates] != [str(value) for value in slice_dates]:
        issues.append(f"{prefix}: slice_dates_shanghai does not match slices")
    if parsed_dates != sorted(set(parsed_dates)):
        issues.append(f"{prefix}: slice dates are not unique ascending dates")
    if len(parsed_dates) > int(contract.get("max_dates_per_run", 0)):
        issues.append(f"{prefix}: too many date slices")
    for previous, current in pairwise(parsed_dates):
        if current != previous + timedelta(days=1):
            issues.append(f"{prefix}: slice dates are not contiguous natural days")

    request_budget = _mapping(observed.get("request_budget")) or {}
    budget_expected = {
        "page_size": contract.get("page_size"),
        "max_pages_per_day": contract.get("max_pages_per_day"),
        "max_dates_per_run": contract.get("max_dates_per_run"),
        "max_logical_requests_per_run": contract.get("max_logical_requests_per_run"),
        "max_physical_attempts_per_run": contract.get("max_physical_attempts_per_run"),
        "logical_request_count": observed.get("logical_request_count"),
        "physical_attempt_count": observed.get("physical_attempt_count"),
    }
    if any(request_budget.get(key) != value for key, value in budget_expected.items()):
        issues.append(f"{prefix}: request_budget does not match source stats/config")
    if logical_sum != observed.get("logical_request_count"):
        issues.append(f"{prefix}: per-slice logical requests do not reconcile")
    if physical_sum != observed.get("physical_attempt_count"):
        issues.append(f"{prefix}: per-slice physical attempts do not reconcile")
    if fetched_sum != observed.get("fetched"):
        issues.append(f"{prefix}: per-slice fetched count does not reconcile")

    checkpoint = _mapping(observed.get("daily_checkpoint")) or {}
    required_checkpoint_fields = {
        "lineage_before",
        "verified_checkpoint_date_shanghai_before",
        "verified_checkpoint_date_shanghai_after",
        "newest_observed_at_utc",
        "latest_attempt_observed_at_utc",
        "checkpoint_committed",
        "partial_checkpoint",
        "initial_backlog_migration",
    }
    for field in sorted(required_checkpoint_fields):
        if field not in checkpoint:
            issues.append(f"{prefix}: daily_checkpoint.{field} missing")
    before_raw = checkpoint.get("verified_checkpoint_date_shanghai_before")
    after_raw = checkpoint.get("verified_checkpoint_date_shanghai_after")
    try:
        before = date.fromisoformat(str(before_raw)) if before_raw is not None else None
    except ValueError:
        before = None
        issues.append(f"{prefix}: daily checkpoint before date is invalid")
    try:
        after = date.fromisoformat(str(after_raw)) if after_raw is not None else None
    except ValueError:
        after = None
        issues.append(f"{prefix}: daily checkpoint after date is invalid")
    newest = _aware_datetime(checkpoint.get("newest_observed_at_utc"))
    latest = _aware_datetime(checkpoint.get("latest_attempt_observed_at_utc"))
    if checkpoint.get("lineage_before") not in {
        "legacy_v1_global_watermark",
        "v2.1_daily_checkpoint",
    }:
        issues.append(f"{prefix}: daily checkpoint lineage is invalid")
    if not isinstance(checkpoint.get("initial_backlog_migration"), bool):
        issues.append(f"{prefix}: initial_backlog_migration must be boolean")
    if newest is None or latest is None:
        issues.append(f"{prefix}: daily checkpoint observed timestamps are invalid")
    if before is not None and after is not None and after < before:
        issues.append(f"{prefix}: daily checkpoint moved backwards")
    if after is not None and newest is not None and after > newest.astimezone(SHANGHAI).date():
        issues.append(f"{prefix}: daily checkpoint exceeds newest observed CST date")
    if newest is not None and latest is not None and latest < newest:
        issues.append(f"{prefix}: latest attempt observed precedes committed observed high")
    if checkpoint.get("checkpoint_committed") is not True:
        issues.append(f"{prefix}: daily checkpoint was not committed")
    if checkpoint.get("partial_checkpoint") is not False:
        issues.append(f"{prefix}: partial checkpoint is not acceptable in observation")
    closed_dates = [
        value
        for value, raw_slice in zip(parsed_dates, slices, strict=False)
        if isinstance(raw_slice, Mapping) and raw_slice.get("date_closed") is True
    ]
    after_candidates = [value for value in [before, *closed_dates] if value is not None]
    expected_after = max(after_candidates) if after_candidates else None
    if after != expected_after:
        issues.append(f"{prefix}: daily checkpoint does not equal latest closed complete slice")
    current_slice_present = market_date in parsed_dates if market_date is not None else False
    query_evidence = {
        "job_run_id": run.run_id,
        "canonical_column": observed.get("canonical_column"),
        "slice_dates_shanghai": slice_dates,
        "market_date_at_poll": market_field,
        "matches_shanghai_market_date": current_slice_present
        and not any(
            "mode/date_closed mismatch" in issue
            or "outside its CST day" in issue
            or "market_date_at_poll" in issue
            for issue in issues
        ),
    }
    checkpoint_evidence = {
        "job_run_id": run.run_id,
        "daily_checkpoint": checkpoint,
        "slices": slice_evidence,
        "issues": issues,
    }
    return issues, checkpoint_evidence, query_evidence


def _v2_1_pre_window_seed(
    config: FrozenConfig,
    all_runs: Sequence[JobEvidence],
    first_selected_run_id: int,
) -> tuple[JsonObject | None, list[str]]:
    v2_candidates: list[tuple[datetime, int, JobEvidence, JsonObject]] = []
    legacy_candidates: list[tuple[datetime, int, JobEvidence, datetime]] = []
    superseded_v1 = _mapping(config.document.get("superseded_v1")) or {}
    v1_sha256 = superseded_v1.get("config_sha256")
    for run in all_runs:
        if run.poll_started_at is None or run.run_id >= first_selected_run_id:
            continue
        sources = _mapping(run.stats.get("sources")) or {}
        cninfo = _mapping(sources.get("cninfo")) or {}
        if (
            run.status in {"ok", "degraded"}
            and run.stats.get("config_sha256") == config.sha256
            and run.stats.get("config_version") == config.document.get("schema_version")
        ):
            checkpoint = _mapping(cninfo.get("daily_checkpoint"))
            if checkpoint is not None and checkpoint.get("checkpoint_committed") is True:
                v2_candidates.append((run.poll_started_at, run.run_id, run, checkpoint))
        elif (
            not v2_candidates
            and run.status == "ok"
            and run.stats.get("config_version") == V1_SCHEMA_VERSION
            and isinstance(v1_sha256, str)
            and run.stats.get("config_sha256") == v1_sha256
        ):
            watermark = _aware_datetime(cninfo.get("watermark_after"))
            if watermark is not None:
                legacy_candidates.append((run.poll_started_at, run.run_id, run, watermark))
    if v2_candidates:
        _, _, run, checkpoint = max(v2_candidates, key=lambda item: item[1])
        after_raw = checkpoint.get("verified_checkpoint_date_shanghai_after")
        if after_raw is None:
            after = None
        else:
            try:
                after = date.fromisoformat(str(after_raw))
            except ValueError:
                return None, [f"JobRun {run.run_id}: trusted seed checkpoint date is invalid"]
        newest = _aware_datetime(checkpoint.get("newest_observed_at_utc"))
        if newest is None:
            return None, [f"JobRun {run.run_id}: trusted seed checkpoint fields are invalid"]
        if after is not None and after > newest.astimezone(SHANGHAI).date():
            return None, [f"JobRun {run.run_id}: trusted seed checkpoint exceeds observed high"]
        lineage = "v2.1_daily_checkpoint"
        after_value: str | None = after.isoformat() if after is not None else None
    elif legacy_candidates:
        _, _, run, newest = max(legacy_candidates, key=lambda item: item[1])
        lineage = "legacy_v1_global_watermark"
        after_value = None
    else:
        return None, ["v2.1 observation window has no trusted pre-window checkpoint seed"]
    assert run.poll_started_at is not None
    return (
        {
            "job_run_id": run.run_id,
            "status": run.status,
            "poll_started_at_utc": run.poll_started_at.isoformat(),
            "lineage": lineage,
            "verified_checkpoint_date_shanghai_after": after_value,
            "newest_observed_at_utc": newest.isoformat(),
            "checkpoint_committed": True,
        },
        [],
    )


def _v2_1_checkpoint_chain_issues(
    config: FrozenConfig,
    evidence: Sequence[JsonObject],
    seed: JsonObject | None,
) -> list[str]:
    source_contracts = _mapping(config.document.get("sources")) or {}
    cninfo_contract = _mapping(source_contracts.get("cninfo")) or {}
    overlap = timedelta(minutes=int(cninfo_contract.get("watermark_overlap_minutes", 0)))
    issues: list[str] = []
    previous_after: str | None = None
    previous_newest: datetime | None = None
    for index, item in enumerate(evidence):
        run_id = item.get("job_run_id")
        checkpoint = _mapping(item.get("daily_checkpoint")) or {}
        before = checkpoint.get("verified_checkpoint_date_shanghai_before")
        after = checkpoint.get("verified_checkpoint_date_shanghai_after")
        newest = _aware_datetime(checkpoint.get("newest_observed_at_utc"))
        lineage = checkpoint.get("lineage_before")
        if index == 0:
            if seed is None:
                issues.append(f"JobRun {run_id}: first observation run is not seed-anchored")
            else:
                seed_after = seed.get("verified_checkpoint_date_shanghai_after")
                seed_newest = _aware_datetime(seed.get("newest_observed_at_utc"))
                if lineage != seed.get("lineage"):
                    issues.append(f"JobRun {run_id}: first run seed lineage mismatch")
                if before != seed_after:
                    issues.append(f"JobRun {run_id}: first run before does not match seed after")
                raw_slices = item.get("slices")
                first_slices: list[object] = raw_slices if isinstance(raw_slices, list) else []
                running_seed = seed_newest
                current_slice_count = 0
                for raw_slice in first_slices:
                    slice_item = _mapping(raw_slice)
                    if slice_item is None:
                        continue
                    if slice_item.get("mode") == "current_date_incremental":
                        current_slice_count += 1
                        if (
                            running_seed is not None
                            and _aware_datetime(slice_item.get("incremental_floor_utc"))
                            != running_seed - overlap
                        ):
                            issues.append(
                                f"JobRun {run_id}: first run floor does not match seed lineage"
                            )
                    observed = _aware_datetime(slice_item.get("newest_observed_at_utc"))
                    if slice_item.get("checkpoint_committed") is True and observed is not None:
                        running_seed = (
                            observed if running_seed is None else max(running_seed, observed)
                        )
                if seed_newest is None or current_slice_count != 1:
                    issues.append(f"JobRun {run_id}: first run current slice seed is unavailable")
                if checkpoint.get("checkpoint_committed") is True and newest != running_seed:
                    issues.append(
                        f"JobRun {run_id}: first committed observed high breaks slice lineage"
                    )
        else:
            if lineage != "v2.1_daily_checkpoint":
                issues.append(f"JobRun {run_id}: checkpoint lineage did not use prior v2.1 run")
            if before != previous_after:
                issues.append(f"JobRun {run_id}: checkpoint before breaks prior after chain")
            if previous_newest is not None and (newest is None or newest < previous_newest):
                issues.append(f"JobRun {run_id}: committed observed high regressed")
            raw_slices = item.get("slices")
            slices: list[object] = raw_slices if isinstance(raw_slices, list) else []
            running_observed = previous_newest
            for raw_slice in slices:
                slice_item = _mapping(raw_slice)
                if slice_item is None:
                    continue
                if slice_item.get("mode") == "current_date_incremental" and running_observed:
                    expected_floor = running_observed - overlap
                    if _aware_datetime(slice_item.get("incremental_floor_utc")) != expected_floor:
                        issues.append(f"JobRun {run_id}: incremental floor breaks observed lineage")
                observed = _aware_datetime(slice_item.get("newest_observed_at_utc"))
                if slice_item.get("checkpoint_committed") is True and observed is not None:
                    running_observed = (
                        observed if running_observed is None else max(running_observed, observed)
                    )
            if checkpoint.get("checkpoint_committed") is True and newest != running_observed:
                issues.append(f"JobRun {run_id}: committed observed high breaks slice lineage")
        if checkpoint.get("checkpoint_committed") is True:
            previous_after = str(after) if after is not None else None
            previous_newest = newest
    return issues


def _v2_source_audit(
    config: FrozenConfig,
    dates: Sequence[date],
    runs: Sequence[JobEvidence],
    *,
    all_runs: Sequence[JobEvidence],
    window_start: datetime,
) -> JsonObject:
    sources = _mapping(config.document.get("sources")) or {}
    jobrun_contract = _mapping(config.document.get("jobrun_contract")) or {}
    required_source_fields = {
        str(value) for value in jobrun_contract.get("required_source_stats", [])
    }
    cninfo_contract = _mapping(sources.get("cninfo")) or {}
    watermark_contract = _mapping(cninfo_contract.get("watermark")) or {}
    expected_columns = [str(value) for value in cninfo_contract.get("columns", [])]
    issues: list[str] = []
    critical_failures: list[JsonObject] = []
    run_results: list[JsonObject] = []
    counter_fields = (
        "request_count",
        "logical_request_count",
        "physical_attempt_count",
        "retry_count",
        "fetched",
        "inserted",
        "duplicate_url",
        "duplicate_content_hash",
        "failure_count",
    )
    totals = {source_id: {field: 0 for field in counter_fields} for source_id in sources}
    cninfo_inserted_by_trading_date = {value.isoformat(): 0 for value in dates}
    request_accounting: list[JsonObject] = []
    checkpoint_evidence: list[JsonObject] = []
    daily_checkpoint_evidence: list[JsonObject] = []
    query_calendar_evidence: list[JsonObject] = []
    for run in runs:
        run_issues: list[str] = []
        stats_sources = _mapping(run.stats.get("sources")) or {}
        if not stats_sources:
            run_issues.append("stats.sources missing")
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
            for field in counter_fields:
                value = _nonnegative_integer(observed.get(field))
                if value is None:
                    run_issues.append(f"{source_id}.{field}: must be a non-negative integer")
                else:
                    counters[field] = value
                    totals[source_id][field] += value
            for field in required_source_fields:
                if field not in observed:
                    run_issues.append(f"{source_id}.{field}: required v2 field missing")
            status = observed.get("status")
            if not isinstance(status, str) or not status:
                run_issues.append(f"{source_id}.status: missing")
                continue
            if contract.get("enabled") is not False and status not in ENABLED_SOURCE_STATUSES:
                run_issues.append(f"{source_id}.status={status!r}: unsupported")
            if contract.get("enabled") is False:
                if status != contract.get("frozen_status"):
                    run_issues.append(
                        f"{source_id}.status={status!r}, expected {contract.get('frozen_status')!r}"
                    )
                for field, value in counters.items():
                    if value != 0:
                        run_issues.append(f"{source_id}.{field}: disabled source must remain zero")
                if observed.get("attempted") is not False:
                    run_issues.append(f"{source_id}.attempted must be false")
                if source_id == "futu_auxiliary":
                    if observed.get("quote_methods_called") != []:
                        run_issues.append("futu_auxiliary.quote_methods_called must be empty")
                    if observed.get("trade_methods_called") != []:
                        run_issues.append("futu_auxiliary.trade_methods_called must be empty")
                continue

            logical = counters.get("logical_request_count")
            physical = counters.get("physical_attempt_count")
            request_count = counters.get("request_count")
            retry_count = counters.get("retry_count")
            logical_max = _nonnegative_integer(contract.get("max_logical_requests_per_run"))
            physical_max = _nonnegative_integer(contract.get("max_physical_attempts_per_run"))
            identity_ok = (
                logical is not None
                and physical is not None
                and request_count == physical
                and retry_count == physical - logical
                and physical >= logical
            )
            if not identity_ok:
                run_issues.append(f"{source_id}: logical/physical/retry accounting identity failed")
            if logical is not None and logical_max is not None and logical > logical_max:
                run_issues.append(f"{source_id}.logical_request_count exceeds frozen budget")
            if physical is not None and physical_max is not None and physical > physical_max:
                run_issues.append(f"{source_id}.physical_attempt_count exceeds frozen budget")
            if request_count == 0 and status != "skipped_no_watchlist":
                run_issues.append(f"{source_id}.request_count: enabled source was not attempted")
            if status == "skipped_no_watchlist" and request_count != 0:
                run_issues.append(f"{source_id}: skipped_no_watchlist must make zero requests")
            request_accounting.append(
                {
                    "job_run_id": run.run_id,
                    "source_id": source_id,
                    "logical_request_count": logical,
                    "physical_attempt_count": physical,
                    "retry_count": retry_count,
                    "request_count": request_count,
                    "logical_budget": logical_max,
                    "physical_budget": physical_max,
                    "identity_ok": identity_ok,
                }
            )
            fetched = counters.get("fetched")
            inserted = counters.get("inserted")
            duplicate_url = counters.get("duplicate_url")
            duplicate_hash = counters.get("duplicate_content_hash")
            failure_count = counters.get("failure_count")
            if None not in (fetched, inserted, duplicate_url, duplicate_hash):
                assert fetched is not None
                assert inserted is not None
                assert duplicate_url is not None
                assert duplicate_hash is not None
                if inserted + duplicate_url + duplicate_hash > fetched:
                    run_issues.append(f"{source_id}: fetched rows do not reconcile")
            failures = observed.get("failures")
            if failure_count is not None and (
                not isinstance(failures, list) or len(failures) != failure_count
            ):
                run_issues.append(f"{source_id}: failure_count lacks explicit failures")
            if source_id == "cninfo":
                if run.poll_started_at is not None:
                    trading_date = run.poll_started_at.astimezone(SHANGHAI).date().isoformat()
                    if inserted is not None and trading_date in cninfo_inserted_by_trading_date:
                        cninfo_inserted_by_trading_date[trading_date] += inserted
                if _is_v2_1(config):
                    audit_issues, daily_evidence, query_evidence = _v2_1_cninfo_run_audit(
                        config,
                        run,
                        observed,
                    )
                    run_issues.extend(audit_issues)
                    daily_checkpoint_evidence.append(daily_evidence)
                    query_calendar_evidence.append(query_evidence)
                else:
                    if observed.get("tls_verification") is not True:
                        run_issues.append("cninfo.tls_verification is not true")
                    market_date = (
                        run.poll_started_at.astimezone(SHANGHAI).date().isoformat()
                        if run.poll_started_at is not None
                        else None
                    )
                    query_end = observed.get("query_end_date_shanghai")
                    market_field = observed.get("market_date_at_poll")
                    poll_field = _aware_datetime(observed.get("poll_started_at_utc"))
                    query_ok = (
                        query_end == market_date
                        and market_field == market_date
                        and poll_field == run.poll_started_at
                    )
                    if not query_ok:
                        run_issues.append("cninfo Shanghai query calendar evidence mismatch")
                    starts = _mapping(observed.get("query_start_date_shanghai")) or {}
                    if set(starts) != set(expected_columns) or any(
                        not isinstance(starts.get(column), str) for column in expected_columns
                    ):
                        run_issues.append("cninfo per-column query start dates missing")
                    query_calendar_evidence.append(
                        {
                            "job_run_id": run.run_id,
                            "query_start_date_shanghai": starts,
                            "query_end_date_shanghai": query_end,
                            "market_date_at_poll": market_field,
                            "poll_started_at_utc": observed.get("poll_started_at_utc"),
                            "matches_shanghai_market_date": query_ok,
                        }
                    )
                    checkpoints = _mapping(observed.get("column_watermarks")) or {}
                    if set(checkpoints) != set(expected_columns):
                        run_issues.append("cninfo column_watermarks do not match frozen columns")
                    for column in expected_columns:
                        checkpoint = _mapping(checkpoints.get(column))
                        if checkpoint is None:
                            run_issues.append(f"cninfo.{column}: checkpoint missing")
                            continue
                        checkpoint_issues = _v2_checkpoint_issues(
                            run=run,
                            column=column,
                            checkpoint=checkpoint,
                            watermark_contract=watermark_contract,
                        )
                        run_issues.extend(checkpoint_issues)
                        checkpoint_evidence.append(
                            {
                                "job_run_id": run.run_id,
                                "column": column,
                                **checkpoint,
                                "issues": checkpoint_issues,
                            }
                        )
                if failure_count or status != "ok":
                    critical_failures.append(
                        {
                            "job_run_id": run.run_id,
                            "source_id": source_id,
                            "failure_count": failure_count,
                            "status": status,
                        }
                    )
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
    seed: JsonObject | None = None
    seed_issues: list[str] = []
    if _is_v2_1(config):
        first_selected_run_id = min(
            (run.run_id for run in runs),
            default=max((run.run_id for run in all_runs), default=0) + 1,
        )
        seed, seed_issues = _v2_1_pre_window_seed(
            config,
            all_runs,
            first_selected_run_id,
        )
    chain_issues = (
        [*seed_issues, *_v2_1_checkpoint_chain_issues(config, daily_checkpoint_evidence, seed)]
        if _is_v2_1(config)
        else []
    )
    issues.extend(chain_issues)
    return {
        "issues": issues,
        "critical_failures": critical_failures,
        "cninfo_inserted_by_trading_date": cninfo_inserted_by_trading_date,
        "request_accounting": request_accounting,
        "cninfo_per_column_checkpoints": checkpoint_evidence,
        "cninfo_daily_slice_checkpoints": daily_checkpoint_evidence,
        "cninfo_checkpoint_chain_issues": chain_issues,
        "cninfo_observation_seed": seed,
        "observation_window_start_utc": window_start.isoformat(),
        "cninfo_query_calendar": query_calendar_evidence,
        "runs": run_results,
        "totals": totals,
    }


def _v2_slot_detail(slot: datetime, run: JobEvidence | None) -> JsonObject:
    diagnostic = _mapping(run.stats.get("terminal_diagnostics")) if run is not None else None
    return {
        "slot_shanghai": slot.astimezone(SHANGHAI).isoformat(),
        "slot_utc": slot.astimezone(UTC).isoformat(),
        "job_run_id": run.run_id if run is not None else None,
        "job_status": run.status if run is not None else "missing",
        "job_error": run.error if run is not None else None,
        "terminal_diagnostics": diagnostic,
        "poll_started_at": (
            _dual_timestamp(run.poll_started_at)
            if run is not None and run.poll_started_at is not None
            else None
        ),
    }


def _v2_coverage_audit(
    config: FrozenConfig,
    dates: Sequence[date],
    runs: Sequence[JobEvidence],
    cadence: JsonObject,
) -> JsonObject:
    runs_by_id = {run.run_id: run for run in runs}
    matches = cadence.get("matches") if isinstance(cadence.get("matches"), list) else []
    match_by_slot: dict[str, JobEvidence] = {}
    assert isinstance(matches, list)
    for raw_match in matches:
        match = _mapping(raw_match)
        if match is None:
            continue
        run_id = match.get("job_run_id")
        slot = match.get("slot")
        if isinstance(run_id, int) and isinstance(slot, str) and run_id in runs_by_id:
            match_by_slot[slot] = runs_by_id[run_id]
    days: dict[str, JsonObject] = {}
    terminal_diagnostic_counts: dict[str, int] = {}
    unclassified_slots: list[str] = []
    for target in dates:
        expected = expected_poll_slots(config, target)
        status_counts = {"ok": 0, "degraded": 0, "failed": 0, "nonterminal": 0}
        failed_details: list[JsonObject] = []
        missing_details: list[JsonObject] = []
        for slot in expected:
            run = match_by_slot.get(slot.isoformat())
            if run is None:
                missing_details.append(_v2_slot_detail(slot, None))
                unclassified_slots.append(slot.isoformat())
                continue
            if run.status in {"ok", "degraded", "failed"}:
                status_counts[run.status] += 1
            else:
                status_counts["nonterminal"] += 1
            if run.status != "ok":
                detail = _v2_slot_detail(slot, run)
                failed_details.append(detail)
                diagnostic = _mapping(run.stats.get("terminal_diagnostics"))
                code = str(diagnostic.get("code")) if diagnostic is not None else "missing"
                terminal_diagnostic_counts[code] = terminal_diagnostic_counts.get(code, 0) + 1
                if diagnostic is None:
                    unclassified_slots.append(slot.isoformat())
        matched = len(expected) - len(missing_details)
        operational_ok = matched == len(expected) and status_counts["ok"] == len(expected)
        start = datetime.combine(target, time.min, tzinfo=SHANGHAI)
        days[target.isoformat()] = {
            "window_start": _dual_timestamp(start),
            "window_end": _dual_timestamp(start + timedelta(days=1)),
            "expected_slots": len(expected),
            "matched_slots": matched,
            "terminal_slots": sum(status_counts[value] for value in ("ok", "degraded", "failed")),
            "successful_slots": status_counts["ok"],
            "degraded_slots": status_counts["degraded"],
            "failed_slots": status_counts["failed"],
            "nonterminal_slots": status_counts["nonterminal"],
            "missing_slots": len(missing_details),
            "execution_coverage": round(matched / len(expected), 6),
            "success_coverage": round(status_counts["ok"] / len(expected), 6),
            "operational_coverage_status": "pass" if operational_ok else "fail",
            "non_ok_slot_details": failed_details,
            "missing_slot_details": missing_details,
        }
    return {
        "timezone_basis": "Asia/Shanghai",
        "stored_timestamp_basis": "UTC",
        "days": days,
        "terminal_diagnostic_counts": dict(sorted(terminal_diagnostic_counts.items())),
        "unclassified_slots": unclassified_slots,
        "root_cause_accounting_complete": not unclassified_slots,
        "degraded_is_terminal": True,
        "degraded_is_operational_success": False,
        "operational_all_success": all(
            day["operational_coverage_status"] == "pass" for day in days.values()
        ),
    }


def _v2_ingestion_rows_by_job(
    connection: sqlite3.Connection,
    audit_key: str,
    selected_job_ids: set[int],
) -> tuple[dict[int, list[JsonObject]], list[str]]:
    rows_by_job: dict[int, list[JsonObject]] = {}
    issues: list[str] = []
    if not _table_exists(connection, "news_items"):
        return rows_by_job, ["news_items table missing"]
    rows = connection.execute(
        "SELECT id, source, available_time, raw_payload FROM news_items ORDER BY id"
    ).fetchall()
    for row in rows:
        raw_payload = _json_object(row["raw_payload"])
        ingestion = _mapping(raw_payload.get(audit_key)) if raw_payload is not None else None
        if ingestion is None:
            continue
        raw_job_id = ingestion.get("job_run_id")
        if not isinstance(raw_job_id, int) or isinstance(raw_job_id, bool):
            continue
        if raw_job_id not in selected_job_ids:
            continue
        run_mode = ingestion.get("run_mode")
        marker = ingestion.get("preceded_by_coverage_gap")
        if run_mode not in {"regular_incremental", "coverage_gap_catchup"}:
            issues.append(f"news_item {int(row['id'])}: invalid v2 run_mode marker")
        if not isinstance(marker, bool):
            issues.append(f"news_item {int(row['id'])}: coverage-gap marker is not boolean")
        rows_by_job.setdefault(raw_job_id, []).append(
            {
                "news_item_id": int(row["id"]),
                "source": str(row["source"]),
                "available_time": row["available_time"],
                "run_mode": run_mode,
                "preceded_by_coverage_gap": marker,
            }
        )
    return rows_by_job, issues


def _v2_catchup_audit(
    config: FrozenConfig,
    connection: sqlite3.Connection,
    runs: Sequence[JobEvidence],
) -> JsonObject:
    provenance = _mapping(config.document.get("provenance")) or {}
    audit_key = str(provenance.get("raw_payload_audit_key"))
    rows_by_job, issues = _v2_ingestion_rows_by_job(
        connection,
        audit_key,
        {run.run_id for run in runs},
    )
    run_evidence: list[JsonObject] = []
    schedule = _mapping(config.document.get("schedule")) or {}
    monday_policy = _mapping(schedule.get("monday_host_gap_policy")) or {}
    suppressed_slots = monday_policy.get("intentionally_suppressed_slots_shanghai")
    expected_suppressed_count = len(suppressed_slots) if isinstance(suppressed_slots, list) else 0
    total_recomputed_marked = 0
    total_reported_marked = 0
    for run in runs:
        prefix = f"JobRun {run.run_id}"
        rows = rows_by_job.get(run.run_id, [])
        run_mode = run.stats.get("run_mode")
        coverage_gap = run.stats.get("coverage_gap")
        expected_marker = run_mode == "coverage_gap_catchup"
        mismatched_rows = [
            int(row["news_item_id"])
            for row in rows
            if row.get("run_mode") != run_mode
            or row.get("preceded_by_coverage_gap") is not expected_marker
        ]
        if mismatched_rows:
            issues.append(f"{prefix}: ingestion row markers disagree: {mismatched_rows}")
        recomputed_by_source: dict[str, int] = {}
        recomputed_marked_by_source: dict[str, int] = {}
        for row in rows:
            source_id = str(row["source"])
            recomputed_by_source[source_id] = recomputed_by_source.get(source_id, 0) + 1
            if row.get("preceded_by_coverage_gap") is True:
                recomputed_marked_by_source[source_id] = (
                    recomputed_marked_by_source.get(source_id, 0) + 1
                )
        total_recomputed_marked += sum(recomputed_marked_by_source.values())
        raw_sources = _mapping(run.stats.get("sources")) or {}
        reported_marked_by_source = {
            source_id: int(source.get("preceded_by_coverage_gap_inserted", 0))
            for source_id, raw_source in raw_sources.items()
            if (source := _mapping(raw_source)) is not None
        }
        total_reported_marked += sum(reported_marked_by_source.values())
        for source_id, raw_source in raw_sources.items():
            source = _mapping(raw_source)
            if source is None:
                continue
            reported_inserted = _nonnegative_integer(source.get("inserted"))
            if (
                reported_inserted is not None
                and recomputed_by_source.get(source_id, 0) != reported_inserted
            ):
                issues.append(f"{prefix}: {source_id} inserted count does not match DB rows")
            if reported_marked_by_source.get(source_id, 0) != recomputed_marked_by_source.get(
                source_id, 0
            ):
                issues.append(f"{prefix}: {source_id} coverage-gap marker count mismatch")

        catchup = _mapping(run.stats.get("catchup"))
        gap_details = _mapping(run.stats.get("coverage_gap_details"))
        if expected_marker:
            if coverage_gap is not True or catchup is None or gap_details is None:
                issues.append(f"{prefix}: catchup metadata missing")
            else:
                if gap_details.get("suppressed_slot_count") != expected_suppressed_count:
                    issues.append(f"{prefix}: suppressed_slot_count does not match config")
                if gap_details.get("span_basis") != (
                    "first_suppressed_slot_to_actual_poll_started_at"
                ):
                    issues.append(f"{prefix}: coverage-gap span basis mismatch")
                range_end = _aware_datetime(catchup.get("range_end_utc"))
                if range_end != run.poll_started_at:
                    issues.append(f"{prefix}: catchup range_end does not match poll start")
                by_source = _mapping(catchup.get("counts_by_source")) or {}
                for source_id, raw_count in by_source.items():
                    count = _mapping(raw_count) or {}
                    if count.get("inserted") != recomputed_by_source.get(source_id, 0):
                        issues.append(f"{prefix}: catchup {source_id} inserted count mismatch")
                    if count.get("preceded_by_coverage_gap_inserted") != (
                        recomputed_marked_by_source.get(source_id, 0)
                    ):
                        issues.append(f"{prefix}: catchup {source_id} marker count mismatch")
                all_counts = _mapping(catchup.get("counts_all_sources")) or {}
                if all_counts.get("inserted") != sum(recomputed_by_source.values()):
                    issues.append(f"{prefix}: catchup aggregate inserted count mismatch")
                if all_counts.get("preceded_by_coverage_gap_inserted") != sum(
                    recomputed_marked_by_source.values()
                ):
                    issues.append(f"{prefix}: catchup aggregate marker count mismatch")
                column_ranges = _mapping(catchup.get("cninfo_column_ranges")) or {}
                cninfo = _mapping(raw_sources.get("cninfo")) or {}
                if _is_v2_1(config):
                    if catchup.get("range_basis") != (
                        "canonical_daily_verified_observed_minus_overlap_to_actual_poll"
                    ):
                        issues.append(f"{prefix}: canonical daily catchup range basis mismatch")
                    if set(column_ranges) != {"szse"}:
                        issues.append(f"{prefix}: canonical daily catchup must contain only szse")
                    current_date = (
                        run.poll_started_at.astimezone(SHANGHAI).date().isoformat()
                        if run.poll_started_at is not None
                        else None
                    )
                    raw_slices = cninfo.get("slices")
                    slices: list[object] = raw_slices if isinstance(raw_slices, list) else []
                    current_slice = next(
                        (
                            item
                            for item in slices
                            if isinstance(item, Mapping)
                            and item.get("date_shanghai") == current_date
                            and item.get("mode") == "current_date_incremental"
                        ),
                        None,
                    )
                    range_value = _mapping(column_ranges.get("szse")) or {}
                    if not isinstance(current_slice, Mapping) or range_value.get(
                        "start_utc"
                    ) != current_slice.get("incremental_floor_utc"):
                        issues.append(f"{prefix}: szse catchup start does not match daily floor")
                    if _aware_datetime(range_value.get("end_utc")) != run.poll_started_at:
                        issues.append(f"{prefix}: szse catchup end does not match poll start")
                else:
                    checkpoints = _mapping(cninfo.get("column_watermarks")) or {}
                    source_contracts = _mapping(config.document.get("sources")) or {}
                    cninfo_contract = _mapping(source_contracts.get("cninfo")) or {}
                    for column in [str(value) for value in cninfo_contract.get("columns", [])]:
                        range_value = _mapping(column_ranges.get(column)) or {}
                        checkpoint = _mapping(checkpoints.get(column)) or {}
                        if range_value.get("start_utc") != checkpoint.get(
                            "verified_watermark_floor_utc"
                        ):
                            issues.append(f"{prefix}: {column} catchup start does not match floor")
                        if _aware_datetime(range_value.get("end_utc")) != run.poll_started_at:
                            issues.append(
                                f"{prefix}: {column} catchup end does not match poll start"
                            )
        elif coverage_gap is not False or catchup is not None or gap_details is not None:
            issues.append(f"{prefix}: regular run carries catchup metadata")
        run_evidence.append(
            {
                "job_run_id": run.run_id,
                "run_mode": run_mode,
                "coverage_gap": coverage_gap,
                "database_inserted_by_source": dict(sorted(recomputed_by_source.items())),
                "database_marked_by_source": dict(sorted(recomputed_marked_by_source.items())),
                "reported_marked_by_source": dict(sorted(reported_marked_by_source.items())),
                "mismatched_news_item_ids": mismatched_rows,
            }
        )
    phase_gate = _mapping(config.document.get("phase_gate")) or {}
    phase_locks = {
        "p4_1_v2_done_before_three_day_acceptance": phase_gate.get(
            "p4_1_v2_done_before_three_day_acceptance"
        ),
        "p4_2a_offline_evaluation_unlocked": phase_gate.get("p4_2a_offline_evaluation_unlocked"),
        "p4_2b_production_wiring_unlocked": phase_gate.get("p4_2b_production_wiring_unlocked"),
        "p4_3_unlocked": phase_gate.get("p4_3_unlocked"),
    }
    phase_locks_ok = phase_locks == {
        "p4_1_v2_done_before_three_day_acceptance": False,
        "p4_2a_offline_evaluation_unlocked": True,
        "p4_2b_production_wiring_unlocked": False,
        "p4_3_unlocked": False,
    }
    if not phase_locks_ok:
        issues.append("v2 phase locks drifted")
    return {
        "issues": issues,
        "row_marker_recomputation": {
            "total_recomputed_preceded_by_coverage_gap": total_recomputed_marked,
            "total_reported_preceded_by_coverage_gap": total_reported_marked,
            "counts_match": total_recomputed_marked == total_reported_marked,
        },
        "runs": run_evidence,
        "phase_locks": phase_locks,
        "phase_locks_ok": phase_locks_ok,
    }


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
            pit_anomalies.append(f"news_item {row_id}: published_at copied from available_time")
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
        if ingestion.get("available_time_basis") != "write_locked_immediately_before_flush_utc":
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
                (len(rows) - sum("invalid available_time" in item for item in pit_anomalies))
                / len(rows),
                6,
            )
            if rows
            else 0.0
        ),
    }


def _publication_continuity_audit(
    connection: sqlite3.Connection,
    context: ObservationContext,
) -> JsonObject:
    raw_attestation = _mapping(context.document.get("publication_continuity_attestation"))
    assert raw_attestation is not None
    start = _aware_datetime(raw_attestation.get("window_start_utc"))
    end = _aware_datetime(raw_attestation.get("window_end_utc"))
    assert start is not None
    assert end is not None
    expected = _nonnegative_integer(raw_attestation.get("expected_local_strict_row_count"))
    assert expected is not None
    if not _table_exists(connection, "news_items"):
        return {
            "source": "cninfo",
            "local_strict_row_count": 0,
            "attested_expected_row_count": expected,
            "local_count_matches_attestation": False,
            "external_continuity_attested": True,
            "external_attestation_reproduced_by_local_count": False,
            "local_rows_alone_prove_upstream_completeness": False,
            "does_not_restore_decision_timeliness": True,
            "does_not_override_jobrun_failures": True,
            "issues": ["news_items table missing"],
        }
    rows = connection.execute(
        """
        SELECT id, published_at, available_time
        FROM news_items
        WHERE source='cninfo' AND published_at IS NOT NULL
        ORDER BY published_at, id
        """
    ).fetchall()
    selected: list[tuple[int, datetime, datetime | None]] = []
    for row in rows:
        published = _sqlite_utc_datetime(row["published_at"])
        if published is None or not (start < published < end):
            continue
        selected.append(
            (
                int(row["id"]),
                published,
                _sqlite_utc_datetime(row["available_time"]),
            )
        )
    hour_counts: dict[str, int] = {}
    delays: list[float] = []
    identities: list[str] = []
    for row_id, published, available in selected:
        local_hour = published.astimezone(SHANGHAI).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        key = local_hour.isoformat()
        hour_counts[key] = hour_counts.get(key, 0) + 1
        if available is not None:
            delays.append((available - published).total_seconds())
        identities.append(
            "|".join(
                (
                    str(row_id),
                    published.isoformat(),
                    available.isoformat() if available is not None else "null",
                )
            )
        )
    identity_sha256 = hashlib.sha256("\n".join(identities).encode("utf-8")).hexdigest()
    count_matches = len(selected) == expected
    externally_attested = (
        raw_attestation.get("conclusion")
        == "publication_intervals_observed_continuous_in_review_scope"
    )
    return {
        "source": "cninfo",
        "method": (
            "select source=cninfo; parse published_at as UTC; apply strict datetime "
            "bounds; group retained rows by Shanghai publication hour"
        ),
        "window": {
            "start_operator": ">",
            "start": _dual_timestamp(start),
            "end_operator": "<",
            "end": _dual_timestamp(end),
        },
        "local_strict_row_count": len(selected),
        "attested_expected_row_count": expected,
        "local_count_matches_attestation": count_matches,
        "local_identity_sha256": identity_sha256,
        "first_published_at": (_dual_timestamp(selected[0][1]) if selected else None),
        "last_published_at": (_dual_timestamp(selected[-1][1]) if selected else None),
        "publication_hour_counts_shanghai": dict(sorted(hour_counts.items())),
        "ingestion_delay_seconds": {
            "minimum": round(min(delays), 6) if delays else None,
            "maximum": round(max(delays), 6) if delays else None,
        },
        "external_continuity_attested": externally_attested,
        "external_attestation_reproduced_by_local_count": (externally_attested and count_matches),
        "local_rows_alone_prove_upstream_completeness": False,
        "does_not_restore_decision_timeliness": True,
        "does_not_override_jobrun_failures": True,
        "evidence_source": raw_attestation.get("evidence_source"),
        "issues": [],
    }


def _watermark_semantics_audit(
    config: FrozenConfig,
    runs: Sequence[JobEvidence],
    context: ObservationContext,
) -> JsonObject:
    raw_target = _mapping(context.document.get("watermark_semantics_target"))
    assert raw_target is not None
    target = _aware_datetime(raw_target.get("target_slot_shanghai"))
    assert target is not None
    schedule = _mapping(config.document.get("schedule")) or {}
    tolerance = float(schedule.get("slot_tolerance_seconds", 0))
    candidates = [
        run
        for run in runs
        if run.poll_started_at is not None
        and abs((run.poll_started_at - target).total_seconds()) <= tolerance
    ]
    candidates.sort(
        key=lambda run: (
            abs(((run.poll_started_at or target) - target).total_seconds()),
            run.run_id,
        )
    )
    if not candidates:
        return {
            "target_slot": _dual_timestamp(target),
            "matched_job_run_id": None,
            "issues": ["no news_poll JobRun matched the 07:30 Shanghai target"],
            "interpretation": "inconclusive",
        }
    run = candidates[0]
    sources = _mapping(run.stats.get("sources")) or {}
    source_id = str(raw_target.get("source"))
    source = _mapping(sources.get(source_id)) or {}
    before = _aware_datetime(source.get("watermark_before"))
    floor = _aware_datetime(source.get("watermark_floor"))
    after = _aware_datetime(source.get("watermark_after"))
    fetched = _nonnegative_integer(source.get("fetched"))
    inserted = _nonnegative_integer(source.get("inserted"))
    duplicate_url = _nonnegative_integer(source.get("duplicate_url"))
    duplicate_hash = _nonnegative_integer(source.get("duplicate_content_hash"))
    failure_count = _nonnegative_integer(source.get("failure_count"))
    columns_complete = _mapping(source.get("columns_complete"))
    duplicates = (
        duplicate_url + duplicate_hash
        if duplicate_url is not None and duplicate_hash is not None
        else None
    )
    duplicate_only = (
        fetched is not None and duplicates is not None and inserted == 0 and fetched == duplicates
    )
    non_advance = before is not None and after is not None and before == after
    complete = (
        bool(columns_complete) and all(value is True for value in columns_complete.values())
        if columns_complete is not None
        else False
    )
    consistent = (
        run.status == "ok" and failure_count == 0 and non_advance and duplicate_only and complete
    )
    poll_started = run.poll_started_at
    query_end_date_utc = poll_started.astimezone(UTC).date() if poll_started is not None else None
    query_end_date_shanghai = (
        poll_started.astimezone(SHANGHAI).date() if poll_started is not None else None
    )
    query_date_mismatch = (
        query_end_date_utc is not None
        and query_end_date_shanghai is not None
        and query_end_date_utc != query_end_date_shanghai
    )
    issues: list[str] = []
    if len(candidates) > 1:
        issues.append("multiple JobRuns matched the 07:30 target")
    if not consistent:
        issues.append("07:30 evidence does not satisfy the duplicate-only non-advance pattern")
    return {
        "target_slot": _dual_timestamp(target),
        "matched_job_run_id": run.run_id,
        "matched_poll_started_at": (
            _dual_timestamp(run.poll_started_at) if run.poll_started_at is not None else None
        ),
        "job_status": run.status,
        "job_error": run.error,
        "source_status": source.get("status"),
        "watermark_before": _dual_timestamp(before) if before is not None else None,
        "watermark_floor": _dual_timestamp(floor) if floor is not None else None,
        "watermark_after": _dual_timestamp(after) if after is not None else None,
        "fetched": fetched,
        "inserted": inserted,
        "duplicate_url": duplicate_url,
        "duplicate_content_hash": duplicate_hash,
        "columns_complete": columns_complete,
        "failure_count": failure_count,
        "duplicate_only_batch": duplicate_only,
        "watermark_did_not_advance": non_advance,
        "non_advance_consistent_with_current_semantics": consistent,
        "query_window_root_cause": {
            "classification": raw_target.get("defect_classification"),
            "query_end_date_expression": raw_target.get("query_end_date_expression"),
            "runtime_now_timezone": raw_target.get("runtime_now_timezone"),
            "expected_market_timezone": raw_target.get("expected_market_timezone"),
            "query_end_date_used": (
                query_end_date_utc.isoformat() if query_end_date_utc is not None else None
            ),
            "market_date_at_poll": (
                query_end_date_shanghai.isoformat() if query_end_date_shanghai is not None else None
            ),
            "date_mismatch_observed": query_date_mismatch,
            "effect": (
                "07:30 CST poll queried only through the prior UTC date, so same-day "
                "Shanghai announcements were outside the request window"
                if query_date_mismatch
                else "not_observed"
            ),
            "implementation_reference": raw_target.get("implementation_reference"),
            "v2_fix_required": True,
        },
        "interpretation": (
            "query_end_date_used_utc_date_before_shanghai_market_date; only duplicates "
            "were observed; successful poll time is not a watermark"
            if consistent
            else "inconclusive"
        ),
        "interpretation_contract": raw_target.get("interpretation_contract"),
        "implementation_reference": raw_target.get("implementation_reference"),
        "v2_partial_failure_watermark_work_remains": True,
        "issues": issues,
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
    cninfo_inserted_by_date = sources["cninfo_inserted_by_trading_date"]
    assert isinstance(cninfo_inserted_by_date, Mapping)
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
        "cninfo_inserted_each_trading_date": all(
            int(cninfo_inserted_by_date.get(value.isoformat(), 0)) > 0 for value in dates
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


def _v2_gate(
    config: FrozenConfig,
    scope: str,
    dates: Sequence[date],
    cadence: JsonObject,
    coverage: JsonObject,
    jobrun: JsonObject,
    sources: JsonObject,
    schema: JsonObject,
    news: JsonObject,
    catchup: JsonObject,
    trading: JsonObject,
    database_read_only: JsonObject,
) -> JsonObject:
    rows_by_date = news["rows_by_available_date_shanghai"]
    source_totals = sources["totals"]
    cninfo_inserted_by_date = sources["cninfo_inserted_by_trading_date"]
    assert isinstance(rows_by_date, Mapping)
    assert isinstance(source_totals, Mapping)
    assert isinstance(cninfo_inserted_by_date, Mapping)
    accepted_news_rows = sum(int(rows_by_date.get(value.isoformat(), 0)) for value in dates)
    inserted_total = sum(
        int(total.get("inserted", 0))
        for total in source_totals.values()
        if isinstance(total, Mapping)
    )
    phase_locks = _mapping(catchup.get("phase_locks")) or {}
    checkpoint_key = (
        "cninfo_daily_slice_checkpoints" if _is_v2_1(config) else "cninfo_per_column_checkpoints"
    )
    checkpoint_evidence = sources.get(checkpoint_key)
    checkpoint_items = checkpoint_evidence if isinstance(checkpoint_evidence, list) else []
    query_evidence = sources.get("cninfo_query_calendar")
    query_items = query_evidence if isinstance(query_evidence, list) else []
    request_evidence = sources.get("request_accounting")
    request_items = request_evidence if isinstance(request_evidence, list) else []
    values = {
        "reporting_scope_complete": scope == "daily" or dates == _acceptance_dates(config),
        "expected_slots_complete": not cadence["missing_slots"],
        "no_unexpected_poll_runs": not cadence["unexpected_run_ids"],
        "every_matched_slot_status_ok": bool(coverage["operational_all_success"]),
        "degraded_slots_zero": int(jobrun["status_counts"].get("degraded", 0)) == 0,
        "failed_slots_zero": int(jobrun["status_counts"].get("failed", 0)) == 0,
        "jobrun_contract_ok": not jobrun["issues"],
        "source_accounting_ok": not sources["issues"],
        "critical_source_failures_zero": not sources["critical_failures"],
        "logical_physical_request_accounting_ok": bool(request_items)
        and all(
            isinstance(item, Mapping) and item.get("identity_ok") is True for item in request_items
        ),
        "cninfo_checkpoint_contract_ok": bool(checkpoint_items)
        and not sources.get("cninfo_checkpoint_chain_issues")
        and all(isinstance(item, Mapping) and not item.get("issues") for item in checkpoint_items),
        "cninfo_query_calendar_ok": bool(query_items)
        and all(
            isinstance(item, Mapping) and item.get("matches_shanghai_market_date") is True
            for item in query_items
        ),
        "coverage_gap_catchup_accounting_ok": not catchup["issues"],
        "coverage_gap_row_markers_reconcile": bool(
            catchup["row_marker_recomputation"]["counts_match"]
        ),
        "root_cause_accounting_complete": bool(coverage["root_cause_accounting_complete"]),
        "news_schema_ok": bool(schema["exists"]) and not schema["issues"],
        "url_duplicates_zero": news["duplicate_url_groups"] == 0,
        "content_hash_duplicates_zero": news["duplicate_content_hash_groups"] == 0,
        "available_time_coverage_100pct": news["available_time_coverage"] == 1.0,
        "published_at_not_substituted": news["published_at_equals_available_time"] == 0,
        "news_items_present_each_date": all(
            int(rows_by_date.get(value.isoformat(), 0)) > 0 for value in dates
        ),
        "cninfo_inserted_each_trading_date": all(
            int(cninfo_inserted_by_date.get(value.isoformat(), 0)) > 0 for value in dates
        ),
        "inserted_counts_reconcile": inserted_total == accepted_news_rows,
        "pit_ordering_ok": bool(news["row_count"]) and not news["pit_anomalies"],
        "audited_sources_only": not news["source_anomalies"],
        "no_trade_proposals_created": trading["trade_proposals_created_in_window"] == 0,
        "no_broker_orders_created": trading["broker_orders_created_in_window"] == 0,
        "non_simulate_broker_orders_zero": trading["non_simulate_broker_orders"] == 0,
        "p4_1_v2_not_predeclared_done": phase_locks.get(
            "p4_1_v2_done_before_three_day_acceptance",
            False,
        )
        is False,
        "p4_2b_still_locked": phase_locks.get("p4_2b_production_wiring_unlocked") is False,
        "p4_3_still_locked": phase_locks.get("p4_3_unlocked") is False,
        "database_opened_read_only": database_read_only.get("sqlite_uri_mode") == "ro"
        and database_read_only.get("pragma_query_only") == 1
        and database_read_only.get("total_changes_before") == 0
        and database_read_only.get("total_changes_after") == 0,
    }
    values[
        (
            "cninfo_daily_slice_checkpoint_contract_ok"
            if _is_v2_1(config)
            else "cninfo_per_column_checkpoint_contract_ok"
        )
    ] = values["cninfo_checkpoint_contract_ok"]
    return {**values, "all_pass": all(values.values())}


def _evaluate_v2_acceptance(
    *,
    database: Path,
    config: FrozenConfig,
    scope: str,
    dates: Sequence[date],
    observed_now: datetime,
    ready_at: datetime,
    context: ObservationContext,
) -> JsonObject:
    with _connect_read_only(database) as connection:
        query_only_row = connection.execute("PRAGMA query_only").fetchone()
        total_changes_before = connection.total_changes
        all_runs = _load_job_runs(connection)
        runs_by_id = {run.run_id: run for run in all_runs}
        selected = _selected_runs(all_runs, set(dates))
        cadence = _cadence_audit(config, dates, selected)
        jobrun = _v2_jobrun_audit(config, selected)
        window_start = datetime.combine(min(dates), time.min, tzinfo=SHANGHAI).astimezone(UTC)
        source = _v2_source_audit(
            config,
            dates,
            selected,
            all_runs=all_runs,
            window_start=window_start,
        )
        schema = _schema_audit(connection)
        news = _news_audit(config, connection, runs_by_id)
        catchup = _v2_catchup_audit(config, connection, selected)
        trading = _trading_audit(connection, dates)
        database_read_only = {
            "sqlite_uri_mode": "ro",
            "pragma_query_only": int(query_only_row[0]) if query_only_row is not None else None,
            "total_changes_before": total_changes_before,
            "total_changes_after": connection.total_changes,
        }
    coverage = _v2_coverage_audit(config, dates, selected, cadence)
    gate = _v2_gate(
        config,
        scope,
        dates,
        cadence,
        coverage,
        jobrun,
        source,
        schema,
        news,
        catchup,
        trading,
        database_read_only,
    )
    report: JsonObject = {
        "schema_version": "p4.1-news-v2-acceptance-report-v1",
        "generated_at": observed_now.astimezone(UTC).isoformat(),
        "scope": scope,
        "dates": [value.isoformat() for value in dates],
        "database": str(database.resolve()),
        "read_only": True,
        "database_read_only_evidence": database_read_only,
        "config": {
            "path": str(config.path),
            "sha256": config.sha256,
            "schema_version": config.document["schema_version"],
            "preregistration_receipt_path": (
                str(config.receipt_path) if config.receipt_path is not None else None
            ),
            "preregistration_receipt_sha256": config.receipt_sha256,
        },
        "observation_context": {
            "path": str(context.path),
            "sha256": context.sha256,
            "schema_version": context.document["schema_version"],
            "frozen_config_sha256": context.document["frozen_config_sha256"],
        },
        "ready_at": ready_at.isoformat(),
        "cadence": cadence,
        "coverage": coverage,
        "jobrun": jobrun,
        "sources": source,
        "schema": schema,
        "news_items": news,
        "coverage_gap_catchup": catchup,
        "external_evidence": {
            "runner_network_policy": context.document["reporter_network_policy"],
            "gate_policy": context.document["gate_policy"],
            "v1_external_attestations_reused": context.document["v1_external_attestations_reused"],
            "does_not_override_gate": True,
        },
        "trading_safety": trading,
        "gate": gate,
    }
    return _sanitize_v2_report(report)


def evaluate_acceptance(
    *,
    database: Path,
    config: FrozenConfig,
    scope: str,
    target_date: date | None = None,
    now: datetime | None = None,
    observation_context: ObservationContext | None = None,
) -> JsonObject:
    context = observation_context or load_observation_context(
        PROJECT_DIR / _default_observation_context(config),
        config,
    )
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

    if _is_v2(config):
        return _evaluate_v2_acceptance(
            database=database,
            config=config,
            scope=scope,
            dates=dates,
            observed_now=observed_now,
            ready_at=ready_at,
            context=context,
        )

    with _connect_read_only(database) as connection:
        all_runs = _load_job_runs(connection)
        runs_by_id = {run.run_id: run for run in all_runs}
        selected = _selected_runs(all_runs, set(dates))
        cadence = _cadence_audit(config, dates, selected)
        jobrun = _jobrun_audit(config, selected)
        source = _source_audit(config, dates, selected)
        schema = _schema_audit(connection)
        news = _news_audit(config, connection, runs_by_id)
        publication_continuity = _publication_continuity_audit(connection, context)
        trading = _trading_audit(connection, dates)
    coverage = _coverage_audit(config, dates, selected, cadence, context)
    watermark_semantics = _watermark_semantics_audit(config, all_runs, context)
    gate = _gate(config, scope, dates, cadence, jobrun, source, schema, news, trading)
    return {
        "schema_version": "p4.1-news-acceptance-report-v2",
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
        "observation_context": {
            "path": str(context.path),
            "sha256": context.sha256,
            "schema_version": context.document["schema_version"],
            "frozen_config_sha256": context.document["frozen_config_sha256"],
        },
        "ready_at": ready_at.isoformat(),
        "cadence": cadence,
        "coverage": coverage,
        "jobrun": jobrun,
        "sources": source,
        "schema": schema,
        "news_items": news,
        "publication_continuity": publication_continuity,
        "watermark_semantics_0730": watermark_semantics,
        "external_evidence": {
            "runner_network_policy": context.document["reporter_network_policy"],
            "gate_policy": context.document["gate_policy"],
            "direct_reachability_attestation": context.document["direct_reachability_attestation"],
            "publication_continuity_attestation": context.document[
                "publication_continuity_attestation"
            ],
            "day3_owner_adjudication": context.document["day3_owner_adjudication"],
            "does_not_override_gate": True,
        },
        "trading_safety": trading,
        "gate": gate,
    }


def _write_new_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite P4.1 acceptance evidence: {path}")
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
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


def _resolved_report_path(
    config: FrozenConfig,
    scope: str,
    target: date | None,
    explicit: Path | None,
) -> Path:
    frozen = _default_report(config, scope, target).resolve()
    if explicit is None:
        return frozen
    resolved = (PROJECT_DIR / explicit).resolve()
    if _is_v2(config) and resolved != frozen:
        raise ValueError("P4.1 v2 reports must use the frozen standard output path")
    return resolved


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the read-only P4.1 daily or final three-trading-day acceptance gate."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--observation-context",
        type=Path,
        default=None,
    )
    parser.add_argument("--scope", choices=("daily", "final"), required=True)
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    os.chdir(PROJECT_DIR)
    config = load_config(PROJECT_DIR / arguments.config)
    context_path = arguments.observation_context or _default_observation_context(config)
    observation_context = load_observation_context(
        PROJECT_DIR / context_path,
        config,
    )
    report = evaluate_acceptance(
        database=PROJECT_DIR / arguments.db,
        config=config,
        scope=str(arguments.scope),
        target_date=arguments.date,
        observation_context=observation_context,
    )
    report_path = _resolved_report_path(
        config,
        str(arguments.scope),
        arguments.date,
        arguments.report,
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
