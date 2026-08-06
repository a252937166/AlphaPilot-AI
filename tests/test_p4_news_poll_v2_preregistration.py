from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, cast

import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
V1_CONFIG = PROJECT_DIR / "config/p4_news_poll_v1.yaml"
V2_CONFIG = PROJECT_DIR / "config/p4_news_poll_v2.yaml"
V2_RECEIPT = PROJECT_DIR / "config/p4_news_poll_v2.preregistration.json"
V1_SHA256 = "d0dcd665472b50092a1b4fa7f65f7115778e1b89ac11aca0ed49dc70beaa790b"


def _load_v2() -> dict[str, Any]:
    loaded: object = yaml.safe_load(V2_CONFIG.read_bytes())
    assert isinstance(loaded, dict)
    return cast(dict[str, Any], loaded)


def test_v2_preregistration_preserves_v1_evidence_and_fixed_order() -> None:
    config = _load_v2()
    receipt = json.loads(V2_RECEIPT.read_text(encoding="utf-8"))

    assert hashlib.sha256(V1_CONFIG.read_bytes()).hexdigest() == V1_SHA256
    assert config["schema_version"] == "p4.1-news-poll-v2"
    assert config["implementation_status"] == "preregistered_not_implemented"
    assert config["superseded_v1"] == {
        "config": "config/p4_news_poll_v1.yaml",
        "config_sha256": V1_SHA256,
        "final_report": "docs/phase4/reports/P4.1-news-acceptance-20260805.json",
        "final_report_sha256": (
            "268854fbc4fdd9dfb98d1a5e48999b06070b3ad85c6abe2e190effd0f69fede6"
        ),
        "final_result": "all_pass_false_immutable",
        "rerun_or_overwrite_to_make_v1_green": "forbidden",
    }
    assert config["remediation"]["implementation_order"] == [
        "cninfo_per_column_verified_watermark",
        "cninfo_flood_capacity_budget",
        "jobrun_terminal_status_tiers",
        "cninfo_shanghai_query_calendar",
        "ths_logical_and_physical_request_budgets",
        "monday_host_gap_and_recovery_catchup",
    ]
    assert receipt["config_sha256"] == hashlib.sha256(V2_CONFIG.read_bytes()).hexdigest()
    assert receipt["implementation_order"] == config["remediation"]["implementation_order"]
    assert receipt["observation_dates_shanghai"] == config["acceptance"]["trading_dates"]
    assert receipt["phase_locks"] == {
        "p4_2a_offline_evaluation_unlocked": True,
        "p4_2b_production_wiring_unlocked": False,
        "p4_3_unlocked": False,
    }


def test_v2_cninfo_capacity_and_per_column_checkpoint_semantics_are_frozen() -> None:
    config = _load_v2()
    cninfo = config["sources"]["cninfo"]
    capacity = cninfo["flood_capacity_contract"]
    watermark = cninfo["watermark"]

    rows_per_column = cninfo["page_size"] * cninfo["max_pages_per_column"]
    assert rows_per_column == capacity["configured_rows_per_column"] == 1200
    assert rows_per_column * len(cninfo["columns"]) == 2400
    assert cninfo["max_logical_requests_per_run"] == 80
    assert cninfo["max_physical_attempts_per_run"] == 160
    assert capacity["configured_rows_per_column"] > capacity["measured_single_batch_rows"]
    assert capacity["configured_rows_all_columns"] > capacity["measured_single_day_rows"]
    assert watermark["scope"] == "per_column"
    assert watermark["checkpoint_lookup_job_statuses"] == ["ok", "degraded"]
    assert watermark["checkpoint_lookup_requires_column_checkpoint_committed"] is True
    assert watermark["complete_column_advancement"] == "newest_observed_at_utc"
    assert watermark["incomplete_column_advancement"] == "unchanged"
    assert watermark["completed_columns_advance_independently"] is True
    assert watermark["candidate_persistence_does_not_imply_checkpoint_advancement"] is True
    assert watermark["failed_jobrun_never_supplies_checkpoint"] is True


def test_v2_uses_shanghai_query_dates_and_separate_request_budgets() -> None:
    config = _load_v2()
    network = config["network"]
    cninfo = config["sources"]["cninfo"]
    ths = config["sources"]["akshare_ths"]

    assert cninfo["query_window"] == {
        "timezone": "Asia/Shanghai",
        "query_start_date_expression": (
            "verified_watermark_floor_utc.astimezone(Asia/Shanghai).date()"
        ),
        "query_end_date_expression": (
            "poll_started_at_utc.astimezone(Asia/Shanghai).date()"
        ),
        "utc_now_date_expression_forbidden": True,
        "start_and_end_are_inclusive": True,
        "observation_trade_dates_must_be_exchange_open_dates": True,
        "stats_fields": [
            "query_start_date_shanghai",
            "query_end_date_shanghai",
            "market_date_at_poll",
            "poll_started_at_utc",
        ],
    }
    budget = network["budget_semantics"]
    assert budget["retry_counts_against_logical_budget"] is False
    assert budget["retry_counts_against_physical_budget"] is True
    assert budget["preserve_original_transport_or_http_error_when_retry_is_suppressed"] is True
    assert budget["never_replace_upstream_error_with_budget_error_after_a_sent_attempt"] is True
    assert ths["max_pages_per_run"] == ths["max_logical_requests_per_run"] == 3
    assert ths["max_physical_attempts_per_run"] == 6
    assert ths["original_error_must_survive_retry_budget_exhaustion"] is True


def test_v2_status_schedule_acceptance_and_phase_locks_are_fail_closed() -> None:
    config = _load_v2()
    statuses = config["jobrun_contract"]["status_semantics"]
    schedule = config["schedule"]["monday_host_gap_policy"]
    acceptance = config["acceptance"]
    safety = config["safety"]
    gates = config["phase_gate"]

    assert config["jobrun_contract"]["terminal_statuses"] == ["ok", "degraded", "failed"]
    assert statuses["ok"]["meaning"] == "critical_coverage_complete_and_safety_unchanged"
    assert statuses["ok"]["noncritical_source_failures_change_top_level_status"] is False
    assert statuses["degraded"]["terminal"] is True
    assert statuses["degraded"]["accepted_as_operational_success"] is False
    assert statuses["degraded"]["permitted_causes"] == [
        "cninfo_column_pagination_incomplete",
        "recovery_catchup_incomplete",
    ]
    assert statuses["degraded"]["error_column"] is None
    assert statuses["failed"]["may_supply_watermark_checkpoint"] is False
    assert statuses["failed"]["error_column"] == "bounded_exception_summary"
    assert config["jobrun_contract"]["terminal_diagnostics_contract"] == {
        "location": "stats.terminal_diagnostics",
        "ok_value": None,
        "required_for_statuses": ["degraded", "failed"],
        "required_fields": ["code", "source", "constraint"],
        "optional_fields": ["recoverable", "retry_suppressed", "message"],
        "raw_payload_forbidden": True,
        "secrets_forbidden": True,
    }
    assert schedule["intentionally_suppressed_slots_shanghai"] == [
        "09:00",
        "09:30",
        "09:40",
    ]
    assert schedule["recovery_catchup_slot_shanghai"] == "09:50"
    assert schedule["catchup_available_time_policy"] == (
        "actual_write_locked_ingestion_time_utc"
    )
    assert schedule["catchup_restores_completeness_not_timeliness"] is True
    assert [date.fromisoformat(value).weekday() for value in acceptance["trading_dates"]] == [
        0,
        1,
        2,
    ]
    assert acceptance["expected_poll_slots_by_trading_date"] == {
        "2026-08-10": 61,
        "2026-08-11": 64,
        "2026-08-12": 64,
    }
    assert acceptance["expected_poll_slots_total"] == 189
    assert acceptance["require_every_matched_slot_status_ok"] is True
    assert acceptance["degraded_slot_fails_operational_gate"] is True
    assert acceptance["require_cninfo_inserted_each_trading_date"] is True
    assert acceptance["require_pit_ordering"] is True
    assert safety["required_trading_mode"] == "research"
    assert safety["required_live_trading_enabled"] is False
    assert safety["required_paper_auto_trading_enabled"] is False
    assert safety["required_futu_account_mutation_enabled"] is False
    assert safety["required_unlock_trade_blocked"] is True
    assert safety["allow_trade_proposal_creation"] is False
    assert safety["allow_broker_order_creation"] is False
    assert gates["p4_2a_offline_evaluation_unlocked"] is True
    assert gates["p4_2b_production_wiring_unlocked"] is False
    assert gates["p4_3_unlocked"] is False
