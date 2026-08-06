from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from scripts import run_p4_1_acceptance as acceptance

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config/p4_news_poll_v1.yaml"
V2_CONFIG_PATH = PROJECT_DIR / "config/p4_news_poll_v2.yaml"
V2_1_CONFIG_PATH = PROJECT_DIR / "config/p4_news_poll_v2_1.yaml"


def _create_database(path: Path, *, enforce_unique: bool = True) -> None:
    url_key = " UNIQUE" if enforce_unique else ""
    hash_key = " UNIQUE" if enforce_unique else ""
    with sqlite3.connect(path) as connection:
        connection.executescript(
            f"""
            CREATE TABLE job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                stats TEXT NOT NULL,
                error TEXT
            );
            CREATE TABLE news_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                symbol TEXT,
                title TEXT NOT NULL,
                url TEXT NOT NULL{url_key},
                published_at TEXT,
                available_time TEXT NOT NULL,
                content_hash TEXT NOT NULL{hash_key},
                raw_payload TEXT NOT NULL
            );
            CREATE TABLE trade_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE broker_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                environment TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def _source_stats(
    *,
    inserted: int = 0,
    fetched: int = 0,
    write_lock: datetime,
    flush_completed: datetime,
    commit_completed: datetime,
) -> dict[str, Any]:
    common: dict[str, Any] = {
        "request_count": 1,
        "retry_count": 0,
        "fetched": fetched,
        "inserted": inserted,
        "duplicate_url": 0,
        "duplicate_content_hash": 0,
        "failure_count": 0,
        "status": "ok" if fetched else "empty",
        "failures": [],
        "db_write_lock_acquired_at": write_lock.isoformat(),
        "db_flush_completed_at": flush_completed.isoformat(),
        "db_commit_completed_at": commit_completed.isoformat(),
    }
    return {
        "cninfo": {
            **common,
            "tls_verification": True,
            "watermark_before": "2026-08-03T13:37:29+00:00",
            "watermark_floor": "2026-08-03T13:07:29+00:00",
            "watermark_after": "2026-08-03T13:37:29+00:00",
            "columns_complete": {"sse": True, "szse": True},
        },
        "sina_company_news": {**common, "request_count": 1, "inserted": 0},
        "akshare_ths": {**common, "request_count": 1, "inserted": 0},
        "akshare_cls": {
            "request_count": 0,
            "retry_count": 0,
            "fetched": 0,
            "inserted": 0,
            "duplicate_url": 0,
            "duplicate_content_hash": 0,
            "failure_count": 0,
            "status": "unavailable",
            "failures": [],
            "attempted": False,
        },
        "akshare_caixin": {
            "request_count": 0,
            "retry_count": 0,
            "fetched": 0,
            "inserted": 0,
            "duplicate_url": 0,
            "duplicate_content_hash": 0,
            "failure_count": 0,
            "status": "excluded_missing_native_title",
            "failures": [],
            "attempted": False,
        },
        "futu_auxiliary": {
            "request_count": 0,
            "retry_count": 0,
            "fetched": 0,
            "inserted": 0,
            "duplicate_url": 0,
            "duplicate_content_hash": 0,
            "failure_count": 0,
            "status": "pending_trading_day_latency_retest",
            "failures": [],
            "attempted": False,
            "quote_methods_called": [],
            "trade_methods_called": [],
        },
    }


def _seed_complete_evidence(path: Path, config: acceptance.FrozenConfig) -> None:
    all_slots = [
        slot
        for target in (date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5))
        for slot in acceptance.expected_poll_slots(config, target)
    ]
    first_run_by_date: dict[date, tuple[int, datetime, datetime]] = {}
    with sqlite3.connect(path) as connection:
        for slot in all_slots:
            started = slot.astimezone(UTC) + timedelta(seconds=10)
            completed = started + timedelta(seconds=30)
            fetched_at = started + timedelta(seconds=5)
            write_lock = fetched_at + timedelta(seconds=1)
            available = write_lock + timedelta(seconds=1)
            flush_completed = available + timedelta(seconds=1)
            commit_completed = flush_completed + timedelta(seconds=1)
            first_for_day = slot.date() not in first_run_by_date
            safety = {
                "settings": {
                    "trading_mode": "research",
                    "live_trading_enabled": False,
                    "paper_auto_trading_enabled": False,
                    "futu_enable_account_mutation": False,
                    "unlock_trade_permanently_blocked": True,
                },
                "trade_proposal_ids": [],
                "broker_order_ids": [],
                "non_simulate_order_count": 0,
            }
            stats = {
                "config_sha256": config.sha256,
                "poll_started_at": started.isoformat(),
                "poll_completed_at": completed.isoformat(),
                "safety_unchanged": True,
                "safety_before": safety,
                "safety_after": safety,
                "sources": _source_stats(
                    inserted=1 if first_for_day else 0,
                    fetched=1 if first_for_day else 0,
                    write_lock=write_lock,
                    flush_completed=flush_completed,
                    commit_completed=commit_completed,
                ),
            }
            cursor = connection.execute(
                """
                INSERT INTO job_runs (
                    job_name, started_at, finished_at, status, stats, error
                ) VALUES ('news_poll', ?, ?, 'ok', ?, NULL)
                """,
                (
                    started.replace(tzinfo=None).isoformat(" "),
                    (completed + timedelta(seconds=1)).replace(tzinfo=None).isoformat(" "),
                    json.dumps(stats),
                ),
            )
            if first_for_day:
                run_id = cursor.lastrowid
                assert run_id is not None
                first_run_by_date[slot.date()] = (run_id, fetched_at, available)

        for index, target in enumerate(sorted(first_run_by_date), start=1):
            run_id, fetched, available = first_run_by_date[target]
            write_lock = fetched + timedelta(seconds=1)
            raw_payload = {
                "announcement_id": f"fixture-{index}",
                "_alphapilot_ingestion": {
                    "job_run_id": run_id,
                    "fetched_at_utc": fetched.isoformat(),
                    "write_lock_acquired_at_utc": write_lock.isoformat(),
                    "available_time_assigned_at_utc": available.isoformat(),
                    "available_time_basis": "write_locked_immediately_before_flush_utc",
                },
            }
            connection.execute(
                """
                INSERT INTO news_items (
                    source, symbol, title, url, published_at, available_time,
                    content_hash, raw_payload
                ) VALUES ('cninfo', NULL, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    f"第 {index} 日全市场公告",
                    f"https://example.test/news/{index}",
                    available.replace(tzinfo=None).isoformat(" "),
                    f"{index:064x}",
                    json.dumps(raw_payload),
                ),
            )


def _ready_time() -> datetime:
    return datetime(2026, 8, 5, 16, 15, tzinfo=UTC)


def _fixture_context(*, expected_continuity_rows: int = 991) -> acceptance.ObservationContext:
    config = acceptance.load_config(CONFIG_PATH)
    context = acceptance.load_observation_context(
        PROJECT_DIR / acceptance.DEFAULT_OBSERVATION_CONTEXT,
        config,
    )
    document = deepcopy(context.document)
    document["publication_continuity_attestation"]["expected_local_strict_row_count"] = (
        expected_continuity_rows
    )
    return acceptance.ObservationContext(
        path=context.path,
        sha256="fixture-context-sha256",
        document=document,
    )


def test_frozen_config_has_64_slots_and_fail_closed_source_contract() -> None:
    config = acceptance.load_config(CONFIG_PATH)

    assert len(acceptance.expected_poll_slots(config, date(2026, 8, 3))) == 64
    sources = config.document["sources"]
    assert sources["cninfo"]["critical"] is True
    assert sources["cninfo"]["verify_tls"] is True
    assert sources["akshare_cls"]["frozen_status"] == "unavailable"
    assert sources["akshare_cls"]["max_attempts_per_request"] == 0
    assert sources["akshare_caixin"]["enabled"] is False
    assert sources["futu_auxiliary"]["enabled"] is False
    assert config.document["acceptance"]["require_cninfo_inserted_each_trading_date"] is True
    assert config.document["phase_gate"]["p4_2_unlocked"] is False


def test_final_acceptance_passes_complete_read_only_fixture(tmp_path: Path) -> None:
    config = acceptance.load_config(CONFIG_PATH)
    database = tmp_path / "complete.db"
    _create_database(database)
    _seed_complete_evidence(database, config)

    report = acceptance.evaluate_acceptance(
        database=database,
        config=config,
        scope="final",
        now=_ready_time(),
    )

    assert report["read_only"] is True
    assert report["cadence"]["expected_slots"] == 192
    assert report["cadence"]["matched_slots"] == 192
    assert report["schema_version"] == "p4.1-news-acceptance-report-v2"
    assert report["observation_context"]["sha256"]
    assert report["coverage"]["timezone_basis"] == "Asia/Shanghai"
    for target in ("2026-08-03", "2026-08-04", "2026-08-05"):
        day = report["coverage"]["days"][target]
        assert day["expected_slots"] == 64
        assert day["matched_slots"] == 64
        assert day["successful_slots"] == 64
        assert day["failed_slots"] == 0
        assert day["missing_slots"] == 0
        assert day["operational_coverage_status"] == "pass"
        assert day["window_start"]["shanghai"].startswith(target)
        assert day["window_start"]["utc"].endswith("+00:00")
    assert report["coverage"]["operational_all_success"] is True
    assert report["external_evidence"]["does_not_override_gate"] is True
    assert (
        report["external_evidence"]["runner_network_policy"][
            "network_calls_performed_by_acceptance_runner"
        ]
        is False
    )
    assert report["news_items"]["row_count"] == 3
    assert report["news_items"]["available_time_coverage"] == 1.0
    assert report["sources"]["issues"] == []
    assert report["sources"]["cninfo_inserted_by_trading_date"] == {
        "2026-08-03": 1,
        "2026-08-04": 1,
        "2026-08-05": 1,
    }
    assert len(report["jobrun"]["evidence"]) == 192
    assert report["jobrun"]["evidence"][0]["stats"]["sources"]["cninfo"]["tls_verification"] is True
    assert (
        report["sources"]["runs"][0]["observed_sources"]["futu_auxiliary"]["trade_methods_called"]
        == []
    )
    assert (
        report["watermark_semantics_0730"]["non_advance_consistent_with_current_semantics"] is True
    )
    assert report["watermark_semantics_0730"]["query_window_root_cause"] == {
        "classification": "utc_cst_query_window_date_defect",
        "query_end_date_expression": "now.date()",
        "runtime_now_timezone": "UTC",
        "expected_market_timezone": "Asia/Shanghai",
        "query_end_date_used": "2026-08-03",
        "market_date_at_poll": "2026-08-04",
        "date_mismatch_observed": True,
        "effect": (
            "07:30 CST poll queried only through the prior UTC date, so same-day "
            "Shanghai announcements were outside the request window"
        ),
        "implementation_reference": (
            "src/alphapilot/jobs/news_poll.py:run_news_poll/"
            "_last_successful_watermark/_fetch_cninfo"
        ),
        "v2_fix_required": True,
    }
    assert report["gate"]["all_pass"] is True
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0] == 192


def test_gate_requires_cninfo_inserted_on_every_trading_date(tmp_path: Path) -> None:
    config = acceptance.load_config(CONFIG_PATH)
    database = tmp_path / "cninfo-daily-insertion-gap.db"
    _create_database(database)
    _seed_complete_evidence(database, config)
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT id, stats FROM job_runs ORDER BY id").fetchall()
        for run_id, raw_stats in rows:
            stats = json.loads(raw_stats)
            trading_date = (
                datetime.fromisoformat(stats["poll_started_at"])
                .astimezone(acceptance.SHANGHAI)
                .date()
            )
            if trading_date == date(2026, 8, 4) and stats["sources"]["cninfo"]["inserted"] > 0:
                stats["sources"]["cninfo"]["inserted"] = 0
                stats["sources"]["akshare_ths"]["inserted"] = 1
                connection.execute(
                    "UPDATE job_runs SET stats=? WHERE id=?",
                    (json.dumps(stats), run_id),
                )
                break
        else:
            raise AssertionError("fixture has no 2026-08-04 CNInfo insertion")

    report = acceptance.evaluate_acceptance(
        database=database,
        config=config,
        scope="final",
        now=_ready_time(),
    )

    assert report["sources"]["cninfo_inserted_by_trading_date"] == {
        "2026-08-03": 1,
        "2026-08-04": 0,
        "2026-08-05": 1,
    }
    assert report["gate"]["cninfo_inserted_each_trading_date"] is False
    assert report["gate"]["inserted_counts_reconcile"] is True
    assert report["gate"]["all_pass"] is False


def test_gate_reports_missing_slot_unaccounted_failure_and_disabled_source_call(
    tmp_path: Path,
) -> None:
    config = acceptance.load_config(CONFIG_PATH)
    database = tmp_path / "blocked.db"
    _create_database(database)
    _seed_complete_evidence(database, config)
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM job_runs WHERE id=2")
        row = connection.execute("SELECT stats FROM job_runs WHERE id=1").fetchone()
        stats = json.loads(row[0])
        stats["sources"]["akshare_cls"]["request_count"] = 1
        stats["sources"]["cninfo"].update(
            {
                "failure_count": 1,
                "status": "failed",
                "failures": [],
            }
        )
        connection.execute(
            "UPDATE job_runs SET status='failed', error=NULL, stats=? WHERE id=1",
            (json.dumps(stats),),
        )
        connection.execute(
            "INSERT INTO broker_orders (environment, created_at) VALUES ('REAL', ?)",
            ("2026-08-03 02:00:00",),
        )

    report = acceptance.evaluate_acceptance(
        database=database,
        config=config,
        scope="final",
        now=_ready_time(),
    )

    assert report["gate"]["all_pass"] is False
    assert report["gate"]["expected_slots_complete"] is False
    assert report["gate"]["source_accounting_ok"] is False
    assert report["gate"]["critical_source_failures_zero"] is False
    assert report["gate"]["non_simulate_broker_orders_zero"] is False
    assert any("failed row has no error" in issue for issue in report["jobrun"]["issues"])
    assert any("akshare_cls.request_count" in issue for issue in report["sources"]["issues"])
    assert any(
        "failure_count lacks explicit failures" in issue for issue in report["sources"]["issues"]
    )


def test_gate_detects_duplicate_url_and_content_hash_without_mutating_database(
    tmp_path: Path,
) -> None:
    config = acceptance.load_config(CONFIG_PATH)
    database = tmp_path / "duplicates.db"
    _create_database(database, enforce_unique=False)
    _seed_complete_evidence(database, config)
    with sqlite3.connect(database) as connection:
        original = connection.execute(
            """
            SELECT source, symbol, title, url, published_at, available_time,
                   content_hash, raw_payload
            FROM news_items ORDER BY id LIMIT 1
            """
        ).fetchone()
        connection.execute(
            """
            INSERT INTO news_items (
                source, symbol, title, url, published_at, available_time,
                content_hash, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(original),
        )

    report = acceptance.evaluate_acceptance(
        database=database,
        config=config,
        scope="final",
        now=_ready_time(),
    )

    assert report["news_items"]["duplicate_url_groups"] == 1
    assert report["news_items"]["duplicate_content_hash_groups"] == 1
    assert report["gate"]["url_duplicates_zero"] is False
    assert report["gate"]["content_hash_duplicates_zero"] is False
    assert report["gate"]["news_schema_ok"] is False
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] == 4


def test_gate_requires_full_safety_tls_and_futu_zero_call_evidence(tmp_path: Path) -> None:
    config = acceptance.load_config(CONFIG_PATH)
    database = tmp_path / "missing-contract-evidence.db"
    _create_database(database)
    _seed_complete_evidence(database, config)
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT stats FROM job_runs WHERE id=1").fetchone()
        stats = json.loads(row[0])
        stats["safety_before"]["settings"]["futu_enable_account_mutation"] = True
        stats["sources"]["cninfo"].pop("tls_verification")
        stats["sources"]["futu_auxiliary"]["attempted"] = True
        stats["sources"]["futu_auxiliary"]["quote_methods_called"] = ["get_market_snapshot"]
        connection.execute(
            "UPDATE job_runs SET stats=? WHERE id=1",
            (json.dumps(stats),),
        )

    report = acceptance.evaluate_acceptance(
        database=database,
        config=config,
        scope="final",
        now=_ready_time(),
    )

    assert report["gate"]["all_pass"] is False
    assert report["gate"]["jobrun_contract_ok"] is False
    assert report["gate"]["source_accounting_ok"] is False
    assert any("futu_enable_account_mutation" in issue for issue in report["jobrun"]["issues"])
    assert any("cninfo.tls_verification" in issue for issue in report["sources"]["issues"])
    assert any("futu_auxiliary.attempted" in issue for issue in report["sources"]["issues"])
    assert any("quote_methods_called" in issue for issue in report["sources"]["issues"])


def test_gate_rejects_published_at_copied_from_available_time(tmp_path: Path) -> None:
    config = acceptance.load_config(CONFIG_PATH)
    database = tmp_path / "publication-time-substitution.db"
    _create_database(database)
    _seed_complete_evidence(database, config)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE news_items SET published_at=available_time WHERE id=1")

    report = acceptance.evaluate_acceptance(
        database=database,
        config=config,
        scope="final",
        now=_ready_time(),
    )

    assert report["news_items"]["published_at_equals_available_time"] == 1
    assert report["gate"]["published_at_not_substituted"] is False
    assert report["gate"]["pit_ordering_ok"] is False


def test_daily_gate_refuses_premature_evidence_and_writer_is_create_only(
    tmp_path: Path,
) -> None:
    config = acceptance.load_config(CONFIG_PATH)
    database = tmp_path / "not-ready.db"
    _create_database(database)

    with pytest.raises(acceptance.AcceptanceNotReady, match="frozen until"):
        acceptance.evaluate_acceptance(
            database=database,
            config=config,
            scope="daily",
            target_date=date(2026, 8, 3),
            now=datetime(2026, 8, 3, 15, 59, tzinfo=UTC),
        )

    report_path = tmp_path / "evidence.json"
    acceptance._write_new_json(report_path, {"gate": {"all_pass": False}})
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        acceptance._write_new_json(report_path, {"gate": {"all_pass": True}})
    assert json.loads(report_path.read_text(encoding="utf-8"))["gate"]["all_pass"] is False


def test_coverage_reports_dual_timezone_and_multilabel_root_causes(tmp_path: Path) -> None:
    config = acceptance.load_config(CONFIG_PATH)
    database = tmp_path / "coverage-root-causes.db"
    _create_database(database)
    _seed_complete_evidence(database, config)
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT id, stats FROM job_runs ORDER BY id").fetchall()
        ids_by_slot: dict[str, int] = {}
        stats_by_id: dict[int, dict[str, Any]] = {}
        for run_id, raw_stats in rows:
            stats = json.loads(raw_stats)
            local = datetime.fromisoformat(stats["poll_started_at"]).astimezone(acceptance.SHANGHAI)
            ids_by_slot[local.isoformat(timespec="minutes")] = int(run_id)
            stats_by_id[int(run_id)] = stats
        missing_id = ids_by_slot["2026-08-03T09:30+08:00"]
        failed_id = ids_by_slot["2026-08-03T09:50+08:00"]
        connection.execute("DELETE FROM job_runs WHERE id=?", (missing_id,))
        failed_stats = stats_by_id[failed_id]
        failed_stats["sources"]["cninfo"].update(
            {
                "status": "unavailable",
                "failure_count": 1,
                "failures": [
                    {
                        "code": "transport_error",
                        "blocked": False,
                        "error_type": "NewsSourceError",
                        "message": "ConnectError",
                    }
                ],
            }
        )
        failed_stats["sources"]["akshare_ths"].update(
            {
                "status": "unavailable",
                "failure_count": 1,
                "failures": [
                    {
                        "code": "request_budget_exhausted",
                        "blocked": True,
                        "error_type": "NewsSourceError",
                        "message": "akshare_ths exceeded request budget 1",
                    }
                ],
                "requests": [{"attempt": 1, "failure_code": "transport_error"}],
            }
        )
        connection.execute(
            "UPDATE job_runs SET status='failed', error=?, stats=? WHERE id=?",
            (
                "JobExecutionError: P4.1 critical news source failed",
                json.dumps(failed_stats),
                failed_id,
            ),
        )

    report = acceptance.evaluate_acceptance(
        database=database,
        config=config,
        scope="daily",
        target_date=date(2026, 8, 3),
        now=_ready_time(),
    )

    day = report["coverage"]["days"]["2026-08-03"]
    assert day["expected_slots"] == 64
    assert day["matched_slots"] == 63
    assert day["successful_slots"] == 62
    assert day["failed_slots"] == 1
    assert day["missing_slots"] == 1
    assert day["operational_coverage_status"] == "fail"
    missing = day["trading_window"]["missing_slot_details"]
    assert missing[0]["slot_shanghai"] == "2026-08-03T09:30:00+08:00"
    assert missing[0]["slot_utc"] == "2026-08-03T01:30:00+00:00"
    assert {cause["classification"] for cause in missing[0]["root_causes"]} == {"host_unreachable"}
    failure = day["trading_window"]["failed_slot_details"][0]
    assert {cause["classification"] for cause in failure["root_causes"]} == {
        "retry_budget_semantics_defect",
        "upstream_unavailable",
    }
    assert report["gate"]["expected_slots_complete"] is False
    assert report["gate"]["critical_source_failures_zero"] is False
    assert report["gate"]["all_pass"] is False


def test_all_slots_matched_but_pagination_failure_remains_operationally_red(
    tmp_path: Path,
) -> None:
    config = acceptance.load_config(CONFIG_PATH)
    database = tmp_path / "pagination-red.db"
    _create_database(database)
    _seed_complete_evidence(database, config)
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT id, stats FROM job_runs ORDER BY id").fetchall()
        for run_id, raw_stats in rows:
            stats = json.loads(raw_stats)
            local = datetime.fromisoformat(stats["poll_started_at"]).astimezone(acceptance.SHANGHAI)
            if local.isoformat(timespec="minutes") != "2026-08-05T09:30+08:00":
                continue
            stats["sources"]["cninfo"].update(
                {
                    "status": "unavailable",
                    "failure_count": 1,
                    "failures": [
                        {
                            "code": "pagination_incomplete",
                            "blocked": False,
                            "error_type": "NewsSourceError",
                            "message": "CNInfo exceeded page cap",
                        }
                    ],
                }
            )
            connection.execute(
                "UPDATE job_runs SET status='failed', error=?, stats=? WHERE id=?",
                (
                    "JobExecutionError: P4.1 critical news source failed",
                    json.dumps(stats),
                    run_id,
                ),
            )
            break
        else:
            raise AssertionError("fixture lacks the 2026-08-05 09:30 slot")

    report = acceptance.evaluate_acceptance(
        database=database,
        config=config,
        scope="daily",
        target_date=date(2026, 8, 5),
        now=_ready_time(),
    )

    day = report["coverage"]["days"]["2026-08-05"]
    assert day["matched_slots"] == 64
    assert day["successful_slots"] == 63
    assert day["failed_slots"] == 1
    assert day["operational_coverage_status"] == "fail"
    assert report["coverage"]["root_cause_counts"] == {"pagination_capacity_watermark_deadlock": 1}
    assert report["gate"]["expected_slots_complete"] is True
    assert report["gate"]["critical_source_failures_zero"] is False
    assert report["external_evidence"]["direct_reachability_attestation"]["http_status"] == 200
    assert report["external_evidence"]["does_not_override_gate"] is True
    assert report["gate"]["all_pass"] is False


def test_publication_continuity_uses_strict_parsed_datetime_bounds(tmp_path: Path) -> None:
    database = tmp_path / "publication-continuity.db"
    _create_database(database)
    context = _fixture_context(expected_continuity_rows=1)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        for index, published in enumerate(
            (
                "2026-08-03 13:37:29.000000",
                "2026-08-03 13:37:29.000001",
            ),
            start=1,
        ):
            connection.execute(
                """
                INSERT INTO news_items (
                    source, symbol, title, url, published_at, available_time,
                    content_hash, raw_payload
                ) VALUES ('cninfo', NULL, ?, ?, ?, ?, ?, '{}')
                """,
                (
                    f"公告 {index}",
                    f"https://example.test/continuity/{index}",
                    published,
                    "2026-08-04 00:00:00.000000",
                    f"{index:064x}",
                ),
            )
        report = acceptance._publication_continuity_audit(connection, context)

    assert report["window"]["start_operator"] == ">"
    assert report["window"]["end_operator"] == "<"
    assert report["local_strict_row_count"] == 1
    assert report["local_count_matches_attestation"] is True
    assert report["external_attestation_reproduced_by_local_count"] is True
    assert report["local_rows_alone_prove_upstream_completeness"] is False
    assert report["does_not_override_jobrun_failures"] is True


def test_0730_duplicate_only_success_explains_non_advancing_watermark() -> None:
    config = acceptance.load_config(CONFIG_PATH)
    context = _fixture_context()
    target = datetime(2026, 8, 3, 23, 30, tzinfo=UTC)
    run = acceptance.JobEvidence(
        run_id=55487,
        status="ok",
        started_at=target,
        finished_at=target + timedelta(seconds=7),
        error=None,
        stats={
            "sources": {
                "cninfo": {
                    "status": "ok",
                    "watermark_before": "2026-08-03T13:37:29+00:00",
                    "watermark_floor": "2026-08-03T13:07:29+00:00",
                    "watermark_after": "2026-08-03T13:37:29+00:00",
                    "columns_complete": {"sse": True, "szse": True},
                    "fetched": 4,
                    "inserted": 0,
                    "duplicate_url": 4,
                    "duplicate_content_hash": 0,
                    "failure_count": 0,
                }
            }
        },
        poll_started_at=target,
        poll_completed_at=target + timedelta(seconds=6),
    )

    report = acceptance._watermark_semantics_audit(config, [run], context)

    assert report["matched_job_run_id"] == 55487
    assert report["target_slot"]["shanghai"] == "2026-08-04T07:30:00+08:00"
    assert report["duplicate_only_batch"] is True
    assert report["watermark_did_not_advance"] is True
    assert report["non_advance_consistent_with_current_semantics"] is True
    root_cause = report["query_window_root_cause"]
    assert root_cause["query_end_date_used"] == "2026-08-03"
    assert root_cause["market_date_at_poll"] == "2026-08-04"
    assert root_cause["date_mismatch_observed"] is True
    assert root_cause["classification"] == "utc_cst_query_window_date_defect"
    assert report["interpretation"].startswith("query_end_date_used_utc_date")


def test_final_time_gate_is_exact_and_context_is_honest_about_direct_200(
    tmp_path: Path,
) -> None:
    config = acceptance.load_config(CONFIG_PATH)
    context = acceptance.load_observation_context(
        PROJECT_DIR / acceptance.DEFAULT_OBSERVATION_CONTEXT,
        config,
    )
    database = tmp_path / "final-time-gate.db"
    _create_database(database)

    with pytest.raises(acceptance.AcceptanceNotReady, match="frozen until"):
        acceptance.evaluate_acceptance(
            database=database,
            config=config,
            scope="final",
            now=datetime(2026, 8, 5, 16, 9, 59, tzinfo=UTC),
            observation_context=context,
        )
    report = acceptance.evaluate_acceptance(
        database=database,
        config=config,
        scope="final",
        now=datetime(2026, 8, 5, 16, 10, tzinfo=UTC),
        observation_context=context,
    )

    direct = report["external_evidence"]["direct_reachability_attestation"]
    assert direct["host"] == "www.cninfo.com.cn"
    assert direct["method"] is None
    assert direct["path"] is None
    assert direct["exact_request_details_supplied"] is False
    assert direct["does_not_override_jobrun_failures"] is True
    assert report["gate"]["all_pass"] is False


def _v2_context(config: acceptance.FrozenConfig, tmp_path: Path) -> acceptance.ObservationContext:
    document: dict[str, Any] = {
        "schema_version": "p4.1-v2-observation-context-v1",
        "frozen_config_sha256": config.sha256,
        "observation_start_utc": "2026-08-09T16:00:00Z",
        "observation_end_utc": "2026-08-12T16:00:00Z",
        "reporter_network_policy": {
            "network_calls_performed_by_acceptance_runner": False,
            "network_calls_permitted_during_report_generation": False,
        },
        "gate_policy": {
            "does_not_override_expected_slots_complete": True,
            "does_not_override_critical_source_failures_zero": True,
            "does_not_turn_operational_failures_green": True,
        },
        "host_unavailability_intervals": [],
        "v1_external_attestations_reused": False,
    }
    path = tmp_path / "P4.1-v2-observation-context-20260813.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return acceptance.load_observation_context(path, config)


def _v2_safety() -> dict[str, Any]:
    return {
        "settings": {
            "trading_mode": "research",
            "live_trading_enabled": False,
            "paper_auto_trading_enabled": False,
            "futu_enable_account_mutation": False,
            "unlock_trade_permanently_blocked": True,
        },
        "trade_proposal_ids": [],
        "broker_order_ids": [],
        "non_simulate_order_count": 0,
    }


def _v2_source_stats(
    *,
    started: datetime,
    inserted: int,
    write_lock: datetime,
    flush_completed: datetime,
    commit_completed: datetime,
    preceded_by_gap: bool,
) -> dict[str, Any]:
    before = started - timedelta(hours=2)
    floor = before - timedelta(minutes=30)
    checkpoint = {
        "verified_watermark_before_utc": before.isoformat(),
        "verified_watermark_floor_utc": floor.isoformat(),
        "newest_observed_at_utc": started.isoformat(),
        "verified_watermark_after_utc": started.isoformat(),
        "pagination_complete": True,
        "checkpoint_committed": True,
        "page_cap_hit": False,
        "attempted": True,
        "skipped_due_to_prior_critical_failure": False,
    }
    common = {
        "status": "ok",
        "request_count": 1,
        "logical_request_count": 1,
        "physical_attempt_count": 1,
        "retry_count": 0,
        "fetched": 0,
        "inserted": 0,
        "duplicate_url": 0,
        "duplicate_content_hash": 0,
        "failure_count": 0,
        "failures": [],
        "preceded_by_coverage_gap_inserted": 0,
        "db_write_lock_acquired_at": write_lock.isoformat(),
        "db_flush_completed_at": flush_completed.isoformat(),
        "db_commit_completed_at": commit_completed.isoformat(),
    }
    sources: dict[str, Any] = {
        "cninfo": {
            **common,
            "request_count": 2,
            "logical_request_count": 2,
            "physical_attempt_count": 2,
            "fetched": inserted,
            "inserted": inserted,
            "preceded_by_coverage_gap_inserted": inserted if preceded_by_gap else 0,
            "tls_verification": True,
            "column_watermarks": {"sse": checkpoint, "szse": checkpoint},
            "query_start_date_shanghai": {
                "sse": floor.astimezone(acceptance.SHANGHAI).date().isoformat(),
                "szse": floor.astimezone(acceptance.SHANGHAI).date().isoformat(),
            },
            "query_end_date_shanghai": (started.astimezone(acceptance.SHANGHAI).date().isoformat()),
            "market_date_at_poll": (started.astimezone(acceptance.SHANGHAI).date().isoformat()),
            "poll_started_at_utc": started.isoformat(),
        },
        "sina_company_news": dict(common),
        "akshare_ths": dict(common),
    }
    for source_id, status in (
        ("akshare_cls", "unavailable"),
        ("akshare_caixin", "excluded_missing_native_title"),
        ("futu_auxiliary", "pending_trading_day_latency_retest"),
    ):
        sources[source_id] = {
            "status": status,
            "attempted": False,
            "request_count": 0,
            "logical_request_count": 0,
            "physical_attempt_count": 0,
            "retry_count": 0,
            "fetched": 0,
            "inserted": 0,
            "duplicate_url": 0,
            "duplicate_content_hash": 0,
            "failure_count": 0,
            "failures": [],
        }
    sources["futu_auxiliary"].update({"quote_methods_called": [], "trade_methods_called": []})
    return sources


def _seed_complete_v2_evidence(path: Path, config: acceptance.FrozenConfig) -> None:
    slots = [
        slot
        for target in acceptance._acceptance_dates(config)
        for slot in acceptance.expected_poll_slots(config, target)
    ]
    inserted_dates: set[date] = set()
    with sqlite3.connect(path) as connection:
        for slot in slots:
            started = slot.astimezone(UTC) + timedelta(seconds=10)
            completed = started + timedelta(seconds=30)
            fetched = started + timedelta(seconds=5)
            write_lock = fetched + timedelta(seconds=1)
            available = write_lock + timedelta(seconds=1)
            flush_completed = available + timedelta(seconds=1)
            commit_completed = flush_completed + timedelta(seconds=1)
            local = slot.astimezone(acceptance.SHANGHAI)
            catchup = (
                local.weekday() == 0 and local.time() == datetime.strptime("09:50", "%H:%M").time()
            )
            insert_row = catchup if local.weekday() == 0 else local.date() not in inserted_dates
            if insert_row:
                inserted_dates.add(local.date())
            sources = _v2_source_stats(
                started=started,
                inserted=int(insert_row),
                write_lock=write_lock,
                flush_completed=flush_completed,
                commit_completed=commit_completed,
                preceded_by_gap=catchup,
            )
            safety = _v2_safety()
            coverage_details = (
                {
                    "reason": "owner_confirmed_periodic_host_unavailability",
                    "timezone": "Asia/Shanghai",
                    "suppressed_slots_shanghai": [
                        local.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()
                        for hour, minute in ((9, 0), (9, 30), (9, 40))
                    ],
                    "suppressed_slot_count": 3,
                    "first_suppressed_slot_shanghai": local.replace(
                        hour=9, minute=0, second=0, microsecond=0
                    ).isoformat(),
                    "recovery_poll_started_at_utc": started.isoformat(),
                    "recovery_poll_started_at_shanghai": started.astimezone(
                        acceptance.SHANGHAI
                    ).isoformat(),
                    "span_seconds": int(
                        (
                            started.astimezone(acceptance.SHANGHAI)
                            - local.replace(hour=9, minute=0, second=0, microsecond=0)
                        ).total_seconds()
                    ),
                    "span_basis": "first_suppressed_slot_to_actual_poll_started_at",
                }
                if catchup
                else None
            )
            cninfo = sources["cninfo"]
            checkpoints = cninfo["column_watermarks"]
            counts_by_source = {
                source_id: {
                    key: int(source.get(key, 0))
                    for key in (
                        "fetched",
                        "inserted",
                        "duplicate_url",
                        "duplicate_content_hash",
                        "preceded_by_coverage_gap_inserted",
                    )
                }
                for source_id, source in sources.items()
            }
            catchup_stats = (
                {
                    "range_basis": ("per_column_verified_watermark_minus_overlap_to_actual_poll"),
                    "cninfo_column_ranges": {
                        column: {
                            "start_utc": checkpoint["verified_watermark_floor_utc"],
                            "end_utc": started.isoformat(),
                            "span_seconds": int(
                                (
                                    started
                                    - datetime.fromisoformat(
                                        checkpoint["verified_watermark_floor_utc"]
                                    )
                                ).total_seconds()
                            ),
                        }
                        for column, checkpoint in checkpoints.items()
                    },
                    "range_end_utc": started.isoformat(),
                    "counts_by_source": counts_by_source,
                    "counts_all_sources": {
                        key: sum(value[key] for value in counts_by_source.values())
                        for key in (
                            "fetched",
                            "inserted",
                            "duplicate_url",
                            "duplicate_content_hash",
                            "preceded_by_coverage_gap_inserted",
                        )
                    },
                    "available_time_policy": "actual_write_locked_ingestion_time_utc",
                    "restores_completeness_not_timeliness": True,
                }
                if catchup
                else None
            )
            stats = {
                "config_sha256": config.sha256,
                "poll_started_at": started.isoformat(),
                "poll_completed_at": completed.isoformat(),
                "run_mode": "coverage_gap_catchup" if catchup else "regular_incremental",
                "coverage_gap": catchup,
                "coverage_gap_details": coverage_details,
                "catchup": catchup_stats,
                "safety_unchanged": True,
                "safety_before": safety,
                "safety_after": safety,
                "sources": sources,
                "terminal_diagnostics": None,
            }
            cursor = connection.execute(
                """
                INSERT INTO job_runs (
                    job_name, started_at, finished_at, status, stats, error
                ) VALUES ('news_poll', ?, ?, 'ok', ?, NULL)
                """,
                (
                    started.replace(tzinfo=None).isoformat(" "),
                    (completed + timedelta(seconds=1)).replace(tzinfo=None).isoformat(" "),
                    json.dumps(stats),
                ),
            )
            if not insert_row:
                continue
            run_id = cursor.lastrowid
            assert run_id is not None
            ingestion = {
                "job_run_id": run_id,
                "run_mode": stats["run_mode"],
                "preceded_by_coverage_gap": catchup,
                "fetched_at_utc": fetched.isoformat(),
                "write_lock_acquired_at_utc": write_lock.isoformat(),
                "available_time_assigned_at_utc": available.isoformat(),
                "available_time_basis": "write_locked_immediately_before_flush_utc",
            }
            connection.execute(
                """
                INSERT INTO news_items (
                    source, symbol, title, url, published_at, available_time,
                    content_hash, raw_payload
                ) VALUES ('cninfo', NULL, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    f"{local.date().isoformat()} v2 公告",
                    f"https://example.test/v2/{local.date().isoformat()}",
                    available.replace(tzinfo=None).isoformat(" "),
                    f"{len(inserted_dates) + 100:064x}",
                    json.dumps({"_alphapilot_ingestion": ingestion}),
                ),
            )


def _v2_ready_time() -> datetime:
    return datetime(2026, 8, 12, 16, 15, tzinfo=UTC)


def test_v2_config_uses_preregistered_hash_dynamic_slots_and_distinct_paths() -> None:
    config = acceptance.load_config(V2_CONFIG_PATH)

    assert config.sha256 == "a76a1cd9f1afd021de4d343a6550a1eb05ddad1b14d8d39cbaae2659574a5834"
    assert [
        len(acceptance.expected_poll_slots(config, target))
        for target in acceptance._acceptance_dates(config)
    ] == [61, 64, 64]
    assert acceptance._default_observation_context(config) == Path(
        "docs/phase4/reports/P4.1-v2-observation-context-20260813.json"
    )
    assert acceptance._default_report(config, "daily", date(2026, 8, 10)).name == (
        "P4.1-news-v2-day-20260810.json"
    )
    assert acceptance._default_report(config, "final", None).name == (
        "P4.1-news-v2-acceptance-20260812.json"
    )
    assert (
        acceptance._default_report(acceptance.load_config(CONFIG_PATH), "final", None).name
        == "P4.1-news-acceptance-20260805.json"
    )


def test_v2_final_acceptance_passes_complete_fixture_and_recomputes_catchup_markers(
    tmp_path: Path,
) -> None:
    config = acceptance.load_config(V2_CONFIG_PATH)
    context = _v2_context(config, tmp_path)
    database = tmp_path / "v2-complete.db"
    _create_database(database)
    _seed_complete_v2_evidence(database, config)

    report = acceptance.evaluate_acceptance(
        database=database,
        config=config,
        scope="final",
        now=_v2_ready_time(),
        observation_context=context,
    )

    assert report["schema_version"] == "p4.1-news-v2-acceptance-report-v1"
    assert report["cadence"]["expected_slots"] == 189
    assert report["jobrun"]["status_counts"] == {"degraded": 0, "failed": 0, "ok": 189}
    assert [
        report["coverage"]["days"][target]["expected_slots"]
        for target in ("2026-08-10", "2026-08-11", "2026-08-12")
    ] == [61, 64, 64]
    assert report["coverage"]["degraded_is_terminal"] is True
    assert report["coverage"]["degraded_is_operational_success"] is False
    assert report["sources"]["cninfo_inserted_by_trading_date"] == {
        "2026-08-10": 1,
        "2026-08-11": 1,
        "2026-08-12": 1,
    }
    assert all(item["identity_ok"] is True for item in report["sources"]["request_accounting"])
    assert report["coverage_gap_catchup"]["row_marker_recomputation"] == {
        "total_recomputed_preceded_by_coverage_gap": 1,
        "total_reported_preceded_by_coverage_gap": 1,
        "counts_match": True,
    }
    assert report["coverage_gap_catchup"]["phase_locks_ok"] is True
    assert report["database_read_only_evidence"] == {
        "sqlite_uri_mode": "ro",
        "pragma_query_only": 1,
        "total_changes_before": 0,
        "total_changes_after": 0,
    }
    assert report["gate"]["database_opened_read_only"] is True
    assert report["gate"]["all_pass"] is True


def test_v2_degraded_is_terminal_but_fails_operational_gate(tmp_path: Path) -> None:
    config = acceptance.load_config(V2_CONFIG_PATH)
    context = _v2_context(config, tmp_path)
    database = tmp_path / "v2-degraded.db"
    _create_database(database)
    _seed_complete_v2_evidence(database, config)
    with sqlite3.connect(database) as connection:
        run_id, raw_stats = connection.execute(
            "SELECT id, stats FROM job_runs ORDER BY id LIMIT 1 OFFSET 1"
        ).fetchone()
        stats = json.loads(raw_stats)
        checkpoint = stats["sources"]["cninfo"]["column_watermarks"]["sse"]
        checkpoint.update(
            {
                "verified_watermark_after_utc": checkpoint["verified_watermark_before_utc"],
                "pagination_complete": False,
                "checkpoint_committed": False,
                "page_cap_hit": True,
            }
        )
        stats["sources"]["cninfo"].update(
            {
                "status": "degraded",
                "failure_count": 1,
                "failures": [
                    {
                        "code": "pagination_incomplete",
                        "blocked": False,
                        "error_type": "NewsSourceError",
                        "column": "sse",
                    }
                ],
            }
        )
        stats["terminal_diagnostics"] = {
            "code": "cninfo_column_pagination_incomplete",
            "source": "cninfo",
            "constraint": "max_pages_per_column",
            "recoverable": True,
            "retry_suppressed": False,
        }
        connection.execute(
            "UPDATE job_runs SET status='degraded', error=NULL, stats=? WHERE id=?",
            (json.dumps(stats), run_id),
        )

    report = acceptance.evaluate_acceptance(
        database=database,
        config=config,
        scope="final",
        now=_v2_ready_time(),
        observation_context=context,
    )

    assert report["jobrun"]["issues"] == []
    assert report["jobrun"]["all_terminal"] is True
    assert report["jobrun"]["status_counts"]["degraded"] == 1
    assert report["coverage"]["days"]["2026-08-10"]["degraded_slots"] == 1
    assert report["gate"]["degraded_slots_zero"] is False
    assert report["gate"]["every_matched_slot_status_ok"] is False
    assert report["gate"]["all_pass"] is False


def test_v2_catchup_marker_mismatch_fails_closed(tmp_path: Path) -> None:
    config = acceptance.load_config(V2_CONFIG_PATH)
    context = _v2_context(config, tmp_path)
    database = tmp_path / "v2-marker-mismatch.db"
    _create_database(database)
    _seed_complete_v2_evidence(database, config)
    with sqlite3.connect(database) as connection:
        row_id, raw_payload = connection.execute(
            "SELECT id, raw_payload FROM news_items ORDER BY id LIMIT 1"
        ).fetchone()
        payload = json.loads(raw_payload)
        payload["_alphapilot_ingestion"]["preceded_by_coverage_gap"] = False
        connection.execute(
            "UPDATE news_items SET raw_payload=? WHERE id=?",
            (json.dumps(payload), row_id),
        )

    report = acceptance.evaluate_acceptance(
        database=database,
        config=config,
        scope="final",
        now=_v2_ready_time(),
        observation_context=context,
    )

    assert report["coverage_gap_catchup"]["issues"]
    assert report["gate"]["coverage_gap_catchup_accounting_ok"] is False
    assert report["gate"]["coverage_gap_row_markers_reconcile"] is False
    assert report["gate"]["all_pass"] is False


def _v2_1_safety(*, paper_trading_enabled: bool = False) -> dict[str, Any]:
    safety = _v2_safety()
    safety["settings"].update(
        {
            "paper_trading_enabled": paper_trading_enabled,
            "futu_enable_trade": False,
        }
    )
    return safety


def _v2_1_sources(
    *,
    started: datetime,
    prior_observed: datetime,
    newest_observed: datetime,
    checkpoint_before: str = "2026-08-09",
    checkpoint_after: str = "2026-08-09",
    cninfo_status: str = "ok",
) -> dict[str, Any]:
    market_date = started.astimezone(acceptance.SHANGHAI).date().isoformat()
    floor = prior_observed - timedelta(minutes=30)
    common = {
        "status": "ok",
        "request_count": 1,
        "logical_request_count": 1,
        "physical_attempt_count": 1,
        "retry_count": 0,
        "fetched": 0,
        "inserted": 0,
        "duplicate_url": 0,
        "duplicate_content_hash": 0,
        "failure_count": 0,
        "failures": [],
    }
    sources: dict[str, Any] = {
        "cninfo": {
            **common,
            "status": cninfo_status,
            "fetched": 1,
            "inserted": 1,
            "canonical_column": "szse",
            "slice_dates_shanghai": [market_date],
            "slices": [
                {
                    "date_shanghai": market_date,
                    "date_closed": False,
                    "mode": "current_date_incremental",
                    "incremental_floor_utc": floor.isoformat(),
                    "attempted": True,
                    "page_count": 1,
                    "logical_request_count": 1,
                    "physical_attempt_count": 1,
                    "fetched": 1,
                    "newest_observed_at_utc": newest_observed.isoformat(),
                    "pagination_complete": True,
                    "coverage_proven": True,
                    "checkpoint_committed": True,
                    "page_cap_hit": False,
                    "failure": None,
                }
            ],
            "request_budget": {
                "page_size": 30,
                "max_pages_per_day": 80,
                "max_dates_per_run": 2,
                "max_logical_requests_per_run": 160,
                "max_physical_attempts_per_run": 320,
                "logical_request_count": 1,
                "physical_attempt_count": 1,
            },
            "daily_checkpoint": {
                "lineage_before": "v2.1_daily_checkpoint",
                "verified_checkpoint_date_shanghai_before": checkpoint_before,
                "verified_checkpoint_date_shanghai_after": checkpoint_after,
                "newest_observed_at_utc": newest_observed.isoformat(),
                "latest_attempt_observed_at_utc": newest_observed.isoformat(),
                "checkpoint_committed": True,
                "partial_checkpoint": False,
                "initial_backlog_migration": False,
            },
            "poll_started_at_utc": started.isoformat(),
            "market_date_at_poll": market_date,
            "tls_verification": True,
        },
        "sina_company_news": dict(common),
        "akshare_ths": dict(common),
    }
    for source_id, status in (
        ("akshare_cls", "unavailable"),
        ("akshare_caixin", "excluded_missing_native_title"),
        ("futu_auxiliary", "pending_trading_day_latency_retest"),
    ):
        sources[source_id] = {
            "status": status,
            "attempted": False,
            "request_count": 0,
            "logical_request_count": 0,
            "physical_attempt_count": 0,
            "retry_count": 0,
            "fetched": 0,
            "inserted": 0,
            "duplicate_url": 0,
            "duplicate_content_hash": 0,
            "failure_count": 0,
            "failures": [],
        }
    sources["futu_auxiliary"].update({"quote_methods_called": [], "trade_methods_called": []})
    return sources


def _v2_1_run(
    config: acceptance.FrozenConfig,
    *,
    run_id: int,
    started: datetime,
    prior_observed: datetime,
    newest_observed: datetime,
    checkpoint_before: str = "2026-08-09",
    checkpoint_after: str = "2026-08-09",
    cninfo_status: str = "ok",
    paper_trading_enabled: bool = False,
) -> acceptance.JobEvidence:
    safety = _v2_1_safety(paper_trading_enabled=paper_trading_enabled)
    completed = started + timedelta(seconds=20)
    stats = {
        "config_version": config.document["schema_version"],
        "config_sha256": config.sha256,
        "poll_started_at": started.isoformat(),
        "poll_completed_at": completed.isoformat(),
        "run_mode": "regular_incremental",
        "coverage_gap": False,
        "safety_unchanged": True,
        "safety_before": safety,
        "safety_after": deepcopy(safety),
        "sources": _v2_1_sources(
            started=started,
            prior_observed=prior_observed,
            newest_observed=newest_observed,
            checkpoint_before=checkpoint_before,
            checkpoint_after=checkpoint_after,
            cninfo_status=cninfo_status,
        ),
        "terminal_diagnostics": None,
    }
    return acceptance.JobEvidence(
        run_id=run_id,
        status="ok",
        started_at=started - timedelta(seconds=1),
        finished_at=completed + timedelta(seconds=1),
        error=None,
        stats=stats,
        poll_started_at=started,
        poll_completed_at=completed,
    )


def _v2_1_seed_run(
    config: acceptance.FrozenConfig,
    *,
    checkpoint_after: str = "2026-08-09",
    newest_observed: datetime | None = None,
) -> acceptance.JobEvidence:
    started = datetime(2026, 8, 9, 15, 50, tzinfo=UTC)
    newest = newest_observed or started - timedelta(minutes=5)
    return _v2_1_run(
        config,
        run_id=900,
        started=started,
        prior_observed=newest - timedelta(minutes=10),
        newest_observed=newest,
        checkpoint_before=checkpoint_after,
        checkpoint_after=checkpoint_after,
    )


def test_v2_1_config_receipt_and_frozen_report_path_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = acceptance.load_config(V2_1_CONFIG_PATH)

    assert config.sha256 == "9d56e137baf10bd0858723a93aff02c57bf7b35f8705f1817b16a89ec615183f"
    assert config.receipt_sha256 == (
        "485f710398698c1692d8afd8de6cd06c71ddf2fbdf42713c6bd4defc4bdfd84b"
    )
    assert acceptance._resolved_report_path(config, "final", None, None) == (
        acceptance._default_report(config, "final", None).resolve()
    )
    with pytest.raises(ValueError, match="frozen standard output path"):
        acceptance._resolved_report_path(config, "final", None, tmp_path / "custom.json")

    monkeypatch.setitem(
        acceptance.EXPECTED_V2_RECEIPT_SHA256,
        "p4.1-news-poll-v2.1",
        "0" * 64,
    )
    with pytest.raises(ValueError, match="receipt SHA-256 drifted"):
        acceptance.load_config(V2_1_CONFIG_PATH)


def test_v2_1_jobrun_rejects_dangerous_safety_and_sensitive_diagnostics() -> None:
    config = acceptance.load_config(V2_1_CONFIG_PATH)
    started = datetime(2026, 8, 10, 1, 50, tzinfo=UTC)
    run = _v2_1_run(
        config,
        run_id=901,
        started=started,
        prior_observed=started - timedelta(minutes=10),
        newest_observed=started - timedelta(minutes=1),
        paper_trading_enabled=True,
    )

    audit = acceptance._v2_jobrun_audit(config, [run])
    assert any("paper_trading_enabled=True" in issue for issue in audit["issues"])

    stats = deepcopy(run.stats)
    stats["terminal_diagnostics"] = {
        "code": "transport_error",
        "source": "cninfo",
        "constraint": "network",
        "message": "Authorization token was rejected",
    }
    failed = acceptance.JobEvidence(
        run_id=2,
        status="failed",
        started_at=run.started_at,
        finished_at=run.finished_at,
        error="password must never be persisted",
        stats=stats,
        poll_started_at=run.poll_started_at,
        poll_completed_at=run.poll_completed_at,
    )
    failed_audit = acceptance._v2_jobrun_audit(config, [failed])
    assert any("error contains sensitive evidence" in issue for issue in failed_audit["issues"])
    assert any(
        "terminal_diagnostics contains sensitive evidence" in issue
        for issue in failed_audit["issues"]
    )


def test_v2_1_daily_slice_chain_accepts_good_runs_and_rejects_broken_chain() -> None:
    config = acceptance.load_config(V2_1_CONFIG_PATH)
    seed = _v2_1_seed_run(config)
    seed_newest = acceptance._aware_datetime(
        seed.stats["sources"]["cninfo"]["daily_checkpoint"]["newest_observed_at_utc"]
    )
    assert seed_newest is not None
    started = datetime(2026, 8, 10, 1, 50, tzinfo=UTC)
    first_newest = started - timedelta(minutes=2)
    second_started = started + timedelta(minutes=10)
    second_newest = second_started - timedelta(minutes=1)
    first = _v2_1_run(
        config,
        run_id=901,
        started=started,
        prior_observed=seed_newest,
        newest_observed=first_newest,
    )
    second = _v2_1_run(
        config,
        run_id=902,
        started=second_started,
        prior_observed=first_newest,
        newest_observed=second_newest,
    )

    window_start = datetime(2026, 8, 9, 16, tzinfo=UTC)
    good = acceptance._v2_source_audit(
        config,
        [date(2026, 8, 10)],
        [first, second],
        all_runs=[seed, first, second],
        window_start=window_start,
    )
    assert good["issues"] == []
    assert good["cninfo_checkpoint_chain_issues"] == []
    assert len(good["cninfo_daily_slice_checkpoints"]) == 2

    broken = deepcopy(second.stats)
    broken["sources"]["cninfo"]["daily_checkpoint"]["verified_checkpoint_date_shanghai_before"] = (
        "2026-08-08"
    )
    broken_run = acceptance.JobEvidence(
        run_id=902,
        status=second.status,
        started_at=second.started_at,
        finished_at=second.finished_at,
        error=second.error,
        stats=broken,
        poll_started_at=second.poll_started_at,
        poll_completed_at=second.poll_completed_at,
    )
    audit = acceptance._v2_source_audit(
        config,
        [date(2026, 8, 10)],
        [first, broken_run],
        all_runs=[seed, first, broken_run],
        window_start=window_start,
    )
    assert any("breaks prior after chain" in issue for issue in audit["issues"])

    tampered_stats = deepcopy(first.stats)
    invented_newest = started + timedelta(minutes=1)
    tampered_checkpoint = tampered_stats["sources"]["cninfo"]["daily_checkpoint"]
    tampered_checkpoint["newest_observed_at_utc"] = invented_newest.isoformat()
    tampered_checkpoint["latest_attempt_observed_at_utc"] = invented_newest.isoformat()
    tampered_first = acceptance.JobEvidence(
        run_id=first.run_id,
        status=first.status,
        started_at=first.started_at,
        finished_at=first.finished_at,
        error=first.error,
        stats=tampered_stats,
        poll_started_at=first.poll_started_at,
        poll_completed_at=first.poll_completed_at,
    )
    tampered_audit = acceptance._v2_source_audit(
        config,
        [date(2026, 8, 10)],
        [tampered_first],
        all_runs=[seed, tampered_first],
        window_start=window_start,
    )
    assert any(
        "first committed observed high breaks slice lineage" in issue
        for issue in tampered_audit["issues"]
    )


def test_v2_1_top_level_ok_with_cninfo_degraded_fails_source_and_gate() -> None:
    config = acceptance.load_config(V2_1_CONFIG_PATH)
    seed = _v2_1_seed_run(config)
    seed_newest = acceptance._aware_datetime(
        seed.stats["sources"]["cninfo"]["daily_checkpoint"]["newest_observed_at_utc"]
    )
    assert seed_newest is not None
    started = datetime(2026, 8, 10, 1, 50, tzinfo=UTC)
    run = _v2_1_run(
        config,
        run_id=901,
        started=started,
        prior_observed=seed_newest,
        newest_observed=started - timedelta(minutes=1),
        cninfo_status="degraded",
    )
    sources = acceptance._v2_source_audit(
        config,
        [date(2026, 8, 10)],
        [run],
        all_runs=[seed, run],
        window_start=datetime(2026, 8, 9, 16, tzinfo=UTC),
    )
    assert sources["issues"]
    assert sources["critical_failures"]

    gate = acceptance._v2_gate(
        config,
        "daily",
        [date(2026, 8, 10)],
        {"missing_slots": [], "unexpected_run_ids": []},
        {"operational_all_success": True, "root_cause_accounting_complete": True},
        {"status_counts": {"ok": 1, "degraded": 0, "failed": 0}, "issues": []},
        sources,
        {"exists": True, "issues": []},
        {
            "rows_by_available_date_shanghai": {"2026-08-10": 1},
            "duplicate_url_groups": 0,
            "duplicate_content_hash_groups": 0,
            "available_time_coverage": 1.0,
            "published_at_equals_available_time": 0,
            "row_count": 1,
            "pit_anomalies": [],
            "source_anomalies": [],
        },
        {
            "issues": [],
            "row_marker_recomputation": {"counts_match": True},
            "phase_locks": {
                "p4_1_v2_done_before_three_day_acceptance": False,
                "p4_2b_production_wiring_unlocked": False,
                "p4_3_unlocked": False,
            },
        },
        {
            "trade_proposals_created_in_window": 0,
            "broker_orders_created_in_window": 0,
            "non_simulate_broker_orders": 0,
        },
        {
            "sqlite_uri_mode": "ro",
            "pragma_query_only": 1,
            "total_changes_before": 0,
            "total_changes_after": 0,
        },
    )
    assert gate["source_accounting_ok"] is False
    assert gate["critical_source_failures_zero"] is False
    assert gate["all_pass"] is False


def test_v2_1_seed_rejects_failed_run_and_accepts_cross_day_floor_lineage() -> None:
    config = acceptance.load_config(V2_1_CONFIG_PATH)
    seed_newest = datetime(2026, 8, 9, 15, 40, tzinfo=UTC)
    seed = _v2_1_seed_run(
        config,
        checkpoint_after="2026-08-08",
        newest_observed=seed_newest,
    )
    started = datetime(2026, 8, 10, 1, 50, tzinfo=UTC)
    current_newest = started - timedelta(minutes=1)
    run = _v2_1_run(
        config,
        run_id=901,
        started=started,
        prior_observed=seed_newest,
        newest_observed=current_newest,
        checkpoint_before="2026-08-08",
        checkpoint_after="2026-08-09",
    )
    cninfo = run.stats["sources"]["cninfo"]
    closed_newest = datetime(2026, 8, 9, 15, 50, tzinfo=UTC)
    current_slice = cninfo["slices"][0]
    current_slice["incremental_floor_utc"] = (closed_newest - timedelta(minutes=30)).isoformat()
    cninfo["slices"] = [
        {
            **current_slice,
            "date_shanghai": "2026-08-09",
            "date_closed": True,
            "mode": "closed_date_reconciliation",
            "incremental_floor_utc": None,
            "newest_observed_at_utc": closed_newest.isoformat(),
        },
        current_slice,
    ]
    cninfo["slice_dates_shanghai"] = ["2026-08-09", "2026-08-10"]
    cninfo.update(
        {
            "request_count": 2,
            "logical_request_count": 2,
            "physical_attempt_count": 2,
            "fetched": 2,
            "inserted": 2,
        }
    )
    cninfo["request_budget"].update({"logical_request_count": 2, "physical_attempt_count": 2})
    window_start = datetime(2026, 8, 9, 16, tzinfo=UTC)
    accepted = acceptance._v2_source_audit(
        config,
        [date(2026, 8, 10)],
        [run],
        all_runs=[seed, run],
        window_start=window_start,
    )
    assert accepted["cninfo_checkpoint_chain_issues"] == []

    failed_seed = acceptance.JobEvidence(
        run_id=seed.run_id,
        status="failed",
        started_at=seed.started_at,
        finished_at=seed.finished_at,
        error="fixture failure",
        stats=seed.stats,
        poll_started_at=seed.poll_started_at,
        poll_completed_at=seed.poll_completed_at,
    )
    rejected = acceptance._v2_source_audit(
        config,
        [date(2026, 8, 10)],
        [run],
        all_runs=[failed_seed, run],
        window_start=window_start,
    )
    assert any(
        "no trusted pre-window checkpoint seed" in issue
        for issue in rejected["cninfo_checkpoint_chain_issues"]
    )

    v1_sha256 = config.document["superseded_v1"]["config_sha256"]
    legacy = acceptance.JobEvidence(
        run_id=899,
        status="ok",
        started_at=seed.started_at,
        finished_at=seed.finished_at,
        error=None,
        stats={
            "config_version": acceptance.V1_SCHEMA_VERSION,
            "config_sha256": v1_sha256,
            "sources": {"cninfo": {"watermark_after": seed_newest.isoformat()}},
        },
        poll_started_at=seed.poll_started_at,
        poll_completed_at=seed.poll_completed_at,
    )
    legacy_seed, legacy_issues = acceptance._v2_1_pre_window_seed(config, [legacy], 901)
    assert legacy_issues == []
    assert legacy_seed is not None
    assert legacy_seed["lineage"] == "legacy_v1_global_watermark"


def test_v2_report_redacts_sensitive_error_and_diagnostic_values(tmp_path: Path) -> None:
    config = acceptance.load_config(V2_CONFIG_PATH)
    context = _v2_context(config, tmp_path)
    database = tmp_path / "v2-sensitive.db"
    _create_database(database)
    _seed_complete_v2_evidence(database, config)
    with sqlite3.connect(database) as connection:
        run_id, raw_stats = connection.execute(
            "SELECT id, stats FROM job_runs ORDER BY id LIMIT 1"
        ).fetchone()
        stats = json.loads(raw_stats)
        stats["terminal_diagnostics"] = {
            "code": "transport_error",
            "source": "cninfo",
            "constraint": "network",
            "message": "P4_TOKEN_SENTINEL",
            "nested": {
                "password_value": "P4_PASSWORD_SENTINEL",
                "api_key": "P4_API_KEY_SENTINEL",
                "cookie": "P4_COOKIE_SENTINEL",
                "credential": "P4_CREDENTIAL_SENTINEL",
                "accessKey": "P4_ACCESS_KEY_SENTINEL",
                "privateKey": "P4_PRIVATE_KEY_SENTINEL",
                "sessionCookie": "P4_SESSION_COOKIE_SENTINEL",
                "aws_access_key_id": "P4_AWS_ACCESS_KEY_SENTINEL",
                "client_credentials": "P4_CLIENT_CREDENTIALS_SENTINEL",
                "checkpoint_key": "BENIGN_CHECKPOINT_KEY",
            },
        }
        connection.execute(
            "UPDATE job_runs SET status='failed', error=?, stats=? WHERE id=?",
            ("P4_PASSWORD_SENTINEL P4_TOKEN_SENTINEL", json.dumps(stats), run_id),
        )

    report = acceptance.evaluate_acceptance(
        database=database,
        config=config,
        scope="final",
        now=_v2_ready_time(),
        observation_context=context,
    )
    serialized = json.dumps(report, ensure_ascii=False)

    assert "P4_PASSWORD_SENTINEL" not in serialized
    assert "P4_TOKEN_SENTINEL" not in serialized
    assert "P4_API_KEY_SENTINEL" not in serialized
    assert "P4_COOKIE_SENTINEL" not in serialized
    assert "P4_CREDENTIAL_SENTINEL" not in serialized
    assert "P4_ACCESS_KEY_SENTINEL" not in serialized
    assert "P4_PRIVATE_KEY_SENTINEL" not in serialized
    assert "P4_SESSION_COOKIE_SENTINEL" not in serialized
    assert "P4_AWS_ACCESS_KEY_SENTINEL" not in serialized
    assert "P4_CLIENT_CREDENTIALS_SENTINEL" not in serialized
    assert "BENIGN_CHECKPOINT_KEY" in serialized
    assert "[REDACTED]" in serialized
    assert report["gate"]["jobrun_contract_ok"] is False
    assert report["gate"]["all_pass"] is False
