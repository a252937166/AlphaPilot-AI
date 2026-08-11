from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest
from scripts import freeze_p4_1_v2_1_migration_evidence as evidence


def test_optional_utc_iso_accepts_a_legitimate_empty_slice() -> None:
    assert evidence._optional_utc_iso(None) is None
    assert (
        evidence._optional_utc_iso("2026-08-08T12:34:56+00:00")
        == "2026-08-08T12:34:56Z"
    )


def test_optional_utc_iso_still_rejects_invalid_non_null_values() -> None:
    with pytest.raises(ValueError):
        evidence._optional_utc_iso("not-a-timestamp")


def test_round_three_gate_requests_incremental_validation_receipt() -> None:
    gates = evidence._migration_phase_gates(
        job_run_id=77001,
        checkpoint_after="2026-08-08",
        created_at=datetime(2026, 8, 9, 4, 0, tzinfo=UTC),
    )

    assert gates["all_closed_dates_through_previous_shanghai_day_reconciled"] is True
    assert gates["initial_backlog_migration_complete"] is False
    assert gates["standard_incremental_validation_complete"] is False
    assert gates["scheduler_activated"] is False
    assert "JobRun 77001" in str(gates["next_action"])
    assert "initial_backlog_migration_complete=true" in str(gates["next_action"])


def test_incomplete_round_gate_requests_another_single_round_receipt() -> None:
    gates = evidence._migration_phase_gates(
        job_run_id=77002,
        checkpoint_after="2026-08-06",
        created_at=datetime(2026, 8, 9, 4, 0, tzinfo=UTC),
    )

    assert gates["all_closed_dates_through_previous_shanghai_day_reconciled"] is False
    assert "JobRun 77002" in str(gates["next_action"])
    assert "next single-round initial-migration receipt" in str(
        gates["next_action"]
    )


def test_run_rows_rejects_cross_source_attribution_mismatch() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE news_items ("
        "id INTEGER PRIMARY KEY, source TEXT, published_at TEXT, "
        "available_time TEXT, raw_payload TEXT)"
    )
    ingestion = {
        "_alphapilot_ingestion": {
            "job_run_id": 42,
            "fetched_at_utc": "2026-08-09T00:00:00Z",
            "write_lock_acquired_at_utc": "2026-08-09T00:00:01Z",
            "available_time_assigned_at_utc": "2026-08-09T00:00:02Z",
            "preceded_by_coverage_gap": True,
            "run_mode": "coverage_gap_catchup",
        }
    }
    connection.execute(
        "INSERT INTO news_items "
        "(source, published_at, available_time, raw_payload) VALUES (?, ?, ?, ?)",
        (
            "sina_company_news",
            None,
            "2026-08-09T00:00:02Z",
            json.dumps(ingestion),
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="per-source run-marker row counts do not match inserted",
    ):
        evidence._run_rows_evidence(
            connection,
            job_run_id=42,
            expected_rows_by_source={"akshare_ths": 1},
            source_stats_by_name={},
            poll_completed_at=datetime(2026, 8, 9, 0, 1, tzinfo=UTC),
        )


def _round_three_receipt() -> dict[str, object]:
    return {
        "execution_mode": "initial_backlog_migration",
        "authorized_round": {
            "rounds_authorized": 1,
            "entrypoint": "run_news_poll_v2_1_initial_migration",
            "expected_checkpoint_date_shanghai_before": "2026-08-06",
            "expected_slice_dates_shanghai": ["2026-08-07", "2026-08-08"],
        },
    }


def test_authorized_round_scope_binds_cli_dates_to_receipt() -> None:
    evidence._validate_authorized_round_scope(
        receipt=_round_three_receipt(),
        expected_slices=["2026-08-07", "2026-08-08"],
        expected_checkpoint_before="2026-08-06",
        expected_checkpoint_after="2026-08-08",
    )


@pytest.mark.parametrize(
    ("slices", "checkpoint_before", "checkpoint_after"),
    [
        (["2026-08-08"], "2026-08-06", "2026-08-08"),
        (["2026-08-07", "2026-08-08"], "2026-08-05", "2026-08-08"),
        (["2026-08-07", "2026-08-08"], "2026-08-06", "2026-08-09"),
    ],
)
def test_authorized_round_scope_rejects_cli_mismatch(
    slices: list[str],
    checkpoint_before: str,
    checkpoint_after: str,
) -> None:
    with pytest.raises(RuntimeError):
        evidence._validate_authorized_round_scope(
            receipt=_round_three_receipt(),
            expected_slices=slices,
            expected_checkpoint_before=checkpoint_before,
            expected_checkpoint_after=checkpoint_after,
        )


def test_authorized_round_scope_rejects_boolean_round_count() -> None:
    receipt = _round_three_receipt()
    authorized_round = receipt["authorized_round"]
    assert isinstance(authorized_round, dict)
    authorized_round["rounds_authorized"] = True

    with pytest.raises(RuntimeError, match="exactly one migration round"):
        evidence._validate_authorized_round_scope(
            receipt=receipt,
            expected_slices=["2026-08-07", "2026-08-08"],
            expected_checkpoint_before="2026-08-06",
            expected_checkpoint_after="2026-08-08",
        )


@pytest.mark.parametrize("malformed", [True, "1", -1])
def test_source_counter_map_rejects_coercible_or_negative_values(
    malformed: object,
) -> None:
    source: dict[str, object] = {key: 0 for key in evidence.SLICE_COUNTER_KEYS}
    source["inserted"] = malformed

    with pytest.raises(RuntimeError, match="source aggregate inserted is invalid"):
        evidence._source_counter_map(source)
