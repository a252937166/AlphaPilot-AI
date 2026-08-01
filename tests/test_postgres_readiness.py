from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts import check_postgres_readiness

from alphapilot.db.postgres_readiness import build_postgres_readiness_report

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_current_repository_reports_explicit_postgres_blockers() -> None:
    report = build_postgres_readiness_report(PROJECT_ROOT)
    checks = {str(item["check_id"]): item for item in report["checks"]}

    assert report["mode"] == "offline_static"
    assert report["ready"] is False
    assert report["guardrails"] == {
        "postgres_connection_attempted": False,
        "production_database_read": False,
        "production_database_written": False,
    }
    assert checks["PG_SCHEMA_COMPILES"]["status"] == "pass"
    assert checks["PG_SCHEMA_COMPILES"]["evidence"] == [
        "tables=41",
        "indexes=39",
    ]
    assert {
        "PG_VERSIONED_MIGRATIONS",
        "PG_STARTUP_DDL_OWNERSHIP",
        "PG_DIALECT_NEUTRAL_UPSERTS",
        "PG_SQLITE_ONLY_TOOLS",
        "PG_JSON_POLICY",
        "PG_UTC_SESSION_POLICY",
        "PG_POOL_TIMEOUT_POLICY",
        "PG_SEQUENCE_RESEED",
        "PG_INTEGRATION_TESTS",
        "PG_DATA_PARITY_SIGNOFF",
    } <= set(report["summary"]["blocker_ids"])


def test_readiness_cli_emits_machine_readable_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = check_postgres_readiness.main(["--project-root", str(PROJECT_ROOT), "--compact"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 2
    assert payload["ready"] is False
    assert payload["schema_version"] == 1
    assert payload["summary"]["blockers"] >= 1
