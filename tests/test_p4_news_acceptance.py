from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from scripts import run_p4_1_acceptance as acceptance

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config/p4_news_poll_v1.yaml"


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
        "cninfo": {**common, "tls_verification": True},
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
    assert config.document["acceptance"][
        "require_cninfo_inserted_each_trading_date"
    ] is True
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
    assert report["news_items"]["row_count"] == 3
    assert report["news_items"]["available_time_coverage"] == 1.0
    assert report["sources"]["issues"] == []
    assert report["sources"]["cninfo_inserted_by_trading_date"] == {
        "2026-08-03": 1,
        "2026-08-04": 1,
        "2026-08-05": 1,
    }
    assert len(report["jobrun"]["evidence"]) == 192
    assert report["jobrun"]["evidence"][0]["stats"]["sources"]["cninfo"][
        "tls_verification"
    ] is True
    assert report["sources"]["runs"][0]["observed_sources"]["futu_auxiliary"][
        "trade_methods_called"
    ] == []
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
            if (
                trading_date == date(2026, 8, 4)
                and stats["sources"]["cninfo"]["inserted"] > 0
            ):
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
        "failure_count lacks explicit failures" in issue
        for issue in report["sources"]["issues"]
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
        stats["sources"]["futu_auxiliary"]["quote_methods_called"] = [
            "get_market_snapshot"
        ]
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
        connection.execute(
            "UPDATE news_items SET published_at=available_time WHERE id=1"
        )

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
