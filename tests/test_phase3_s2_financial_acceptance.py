from __future__ import annotations

import ast
import json
import sqlite3
from datetime import date
from pathlib import Path

from alphapilot.backtest.financial_acceptance import (
    REQUIRED_METRICS,
    SHARD_CONTRACTS,
    build_s2_financial_acceptance_report,
)

_ROOT = Path(__file__).resolve().parent.parent


def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE securities (
            symbol TEXT PRIMARY KEY,
            market TEXT NOT NULL,
            board TEXT,
            list_status TEXT,
            profile TEXT
        );
        CREATE TABLE financial_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            report_period TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL,
            source TEXT NOT NULL,
            available_time TEXT NOT NULL,
            payload TEXT NOT NULL,
            UNIQUE(symbol, report_period, metric)
        );
        CREATE TABLE job_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            stats TEXT,
            error TEXT
        );
        """
    )


def _periods() -> list[str]:
    periods: list[str] = []
    year = 2026
    quarter = 3
    for _ in range(40):
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
        periods.append(f"{year}Q{quarter}")
    periods.reverse()
    return periods


def _payload(period: str) -> dict[str, object]:
    year = int(period[:4])
    quarter = int(period[-1])
    month = quarter * 3
    stat_date = date(year, month, 28)
    pub_date = stat_date.replace(day=28)
    return {
        "available_time_basis": "provider_pub_date_end_of_day",
        "pub_dates": [pub_date.isoformat()],
        "stat_date": stat_date.isoformat(),
        "source_field": "profit.roeAvg",
    }


def _available_time(payload: dict[str, object]) -> str:
    pub_date = date.fromisoformat(str(payload["pub_dates"][0]))  # type: ignore[index]
    return f"{pub_date.isoformat()} 16:00:00.000000"


def _insert_complete_symbol(
    connection: sqlite3.Connection,
    symbol: str,
    *,
    negative_periods: set[str] | None = None,
) -> None:
    negative = set(negative_periods or set())
    profile = {"financial_no_data_periods": sorted(negative)}
    connection.execute(
        """
        INSERT INTO securities(symbol, market, board, list_status, profile)
        VALUES (?, 'CN', '主板', 'listed', ?)
        """,
        (symbol, json.dumps(profile)),
    )
    for period in _periods():
        if period in negative:
            continue
        payload = _payload(period)
        for metric in REQUIRED_METRICS:
            connection.execute(
                """
                INSERT INTO financial_indicators(
                    symbol, report_period, metric, value, source,
                    available_time, payload
                )
                VALUES (?, ?, ?, 0.1, 'baostock', ?, ?)
                """,
                (
                    symbol,
                    period,
                    metric,
                    _available_time(payload),
                    json.dumps(payload),
                ),
            )


def _empty_run_stats(
    *,
    name: str,
    symbols_total: int,
) -> dict[str, object]:
    contract = SHARD_CONTRACTS[name]
    return {
        "symbols_total": symbols_total,
        "symbol_min": contract.symbol_min,
        "symbol_max_exclusive": contract.symbol_max_exclusive,
        "symbols_processed": symbols_total,
        "symbols_done": 0,
        "symbols_with_data": 0,
        "symbols_skipped": symbols_total,
        "symbols_unsupported": 0,
        "symbols_failed": 0,
        "quarters_requested": 40,
        "provider_request_budget": contract.provider_request_budget,
        "provider_requests_estimated": 1,
        "provider_probe_requests": 1,
        "provider_probe_rows": 1,
        "financial_quarters_queried": 0,
        "prior_revenue_queries": 0,
        "quarters_done": 0,
        "quarters_unavailable": 0,
        "quarters_skipped_unavailable_checkpoint": 0,
        "unavailable_checkpoints_added": 0,
        "metrics_inserted": 0,
        "metrics_updated": 0,
        "last_symbol": None,
        "resume_symbol": None,
        "stopped_for_request_budget": False,
        "is_complete": True,
        "failures": [],
        "duration_seconds": 0.1,
    }


def _shard_database(path: Path, name: str) -> Path:
    contract = SHARD_CONTRACTS[name]
    symbol = {
        "aliyun": "000001",
        "dogcloud": "300500",
        "us38": "600235",
        "ussea": "603730",
    }[name]
    with sqlite3.connect(path) as connection:
        _schema(connection)
        connection.execute(
            """
            INSERT INTO securities(symbol, market, board, list_status, profile)
            VALUES (?, 'CN', '主板', 'listed', '{}')
            """,
            (symbol,),
        )
        connection.execute(
            """
            INSERT INTO job_runs(
                job_name, started_at, finished_at, status, stats, error
            )
            VALUES ('sync_financials', '2026-08-01T00:00:00+08:00',
                    '2026-08-01T00:00:01+08:00', 'ok', ?, NULL)
            """,
            (json.dumps(_empty_run_stats(name=name, symbols_total=1)),),
        )
    assert contract.name == name
    return path


def _local_database(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        _schema(connection)
        for index in range(5):
            _insert_complete_symbol(
                connection,
                f"{600100 + index:06d}",
                negative_periods={_periods()[0]} if index == 0 else None,
            )
        connection.execute(
            """
            INSERT INTO securities(symbol, market, board, list_status, profile)
            VALUES ('920001', 'CN', '北交所', 'listed', '{}')
            """
        )
    return path


def test_final_acceptance_closes_positive_and_negative_periods_without_network(
    tmp_path: Path,
) -> None:
    local = _local_database(tmp_path / "local.db")
    shards = {name: _shard_database(tmp_path / f"{name}.db", name) for name in SHARD_CONTRACTS}

    report = build_s2_financial_acceptance_report(
        local,
        as_of_date=date(2026, 7, 26),
        shard_databases=shards,
        minimum_covered_symbols=5,
    )

    assert report["network_called"] is False
    assert report["baostock_imported"] is False
    assert report["database"]["open_mode"] == "ro"
    assert report["database"]["query_only"] is True
    assert report["universe"]["provider_supported_symbols"] == 5
    assert report["universe"]["provider_unsupported_symbols"] == 1
    closure = report["checkpoint_closure"]
    assert closure["target_symbol_periods"] == 200
    assert closure["positive_complete_pairs"] == 199
    assert closure["negative_checkpoint_pairs"] == 1
    assert closure["unresolved_pairs"] == 0
    assert closure["partial_pair_count"] == 0
    assert closure["positive_negative_overlaps"] == 0
    assert all(shard["idempotent_empty_run_passed"] for shard in report["shards"].values())
    assert all(shard["query_only"] is True for shard in report["shards"].values())
    plan = report["pubdate_plan"]
    assert plan["mode"] == "plan_only"
    assert plan["network_called"] is False
    assert plan["provider_imported"] is False
    assert plan["planned_provider_queries"] == 5
    assert len(plan["samples"]) == 5
    assert all(sample["local_stat_date"] for sample in plan["samples"])
    assert all(
        str(sample["local_available_time"]).endswith("+00:00")
        for sample in plan["samples"]
    )
    assert report["gate"]["local_checks_passed"] is True
    assert report["gate"]["ready_for_authorized_pubdate_execution"] is True
    assert report["gate"]["ready_for_s2_signoff"] is False


def test_acceptance_blocks_partial_bundle_and_nonempty_empty_run(
    tmp_path: Path,
) -> None:
    local = _local_database(tmp_path / "local.db")
    with sqlite3.connect(local) as connection:
        connection.execute(
            """
            DELETE FROM financial_indicators
            WHERE symbol = '600100'
              AND report_period = ?
              AND metric = 'roe'
            """,
            (_periods()[-1],),
        )
    shards = {name: _shard_database(tmp_path / f"{name}.db", name) for name in SHARD_CONTRACTS}
    with sqlite3.connect(shards["ussea"]) as connection:
        stats = _empty_run_stats(name="ussea", symbols_total=1)
        stats["metrics_inserted"] = 5
        connection.execute(
            "UPDATE job_runs SET stats = ? WHERE id = 1",
            (json.dumps(stats),),
        )

    report = build_s2_financial_acceptance_report(
        local,
        as_of_date=date(2026, 7, 26),
        shard_databases=shards,
        minimum_covered_symbols=5,
    )

    assert report["checkpoint_closure"]["partial_pair_count"] == 1
    assert report["checkpoint_closure"]["unresolved_pairs"] == 1
    assert report["shards"]["ussea"]["idempotent_empty_run_passed"] is False
    codes = {blocker["code"] for blocker in report["gate"]["blockers"]}
    assert "FINANCIAL_CHECKPOINT_PARTIAL_PAIR_COUNT" in codes
    assert "FINANCIAL_CHECKPOINT_UNRESOLVED_PAIRS" in codes
    assert "IDEMPOTENT_EMPTY_RUN_USSEA" in codes
    assert report["gate"]["local_checks_passed"] is False


def test_missing_shard_evidence_blocks_without_touching_database(
    tmp_path: Path,
) -> None:
    local = _local_database(tmp_path / "local.db")
    before = local.stat()

    report = build_s2_financial_acceptance_report(
        local,
        as_of_date=date(2026, 7, 26),
        minimum_covered_symbols=5,
    )

    after = local.stat()
    assert before.st_size == after.st_size
    assert before.st_mtime_ns == after.st_mtime_ns
    assert {blocker["code"] for blocker in report["gate"]["blockers"]} == {
        "IDEMPOTENT_EMPTY_RUN_EVIDENCE_MISSING"
    }


def test_acceptance_sources_do_not_import_or_call_network_clients() -> None:
    paths = (
        _ROOT / "src/alphapilot/backtest/financial_acceptance.py",
        _ROOT / "scripts/run_p3_m3_s2_acceptance.py",
    )
    forbidden = {
        "baostock",
        "socket",
        "httpx",
        "requests",
        "alphapilot.data.baostock_provider",
        "urllib.request",
    }
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
        assert imported.isdisjoint(forbidden)
