from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock
from typing import Any

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from alphapilot.api.routes.jobs import list_runs
from alphapilot.core.config import Settings
from alphapilot.db.migrate import (
    drop_redundant_index,
    ensure_column,
    ensure_index,
    run_migrations,
)
from alphapilot.db.models import (
    Base,
    Disclosure,
    DomainEvent,
    FactorValue,
    FinancialIndicator,
    JobRun,
)
from alphapilot.jobs import event_backfill
from alphapilot.jobs import scheduler as scheduler_module
from alphapilot.jobs.registry import (
    JOBS,
    JobExecutionError,
    JobSpec,
    register,
    run_job,
)
from alphapilot.services.event_extract import DisclosureExtraction


def test_ensure_column_is_idempotent(tmp_path: Any) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sample (id INTEGER PRIMARY KEY)"))

    assert ensure_column(engine, "sample", "label", "TEXT") is True
    assert ensure_column(engine, "sample", "label", "TEXT") is False
    assert {column["name"] for column in inspect(engine).get_columns("sample")} == {
        "id",
        "label",
    }


def test_daily_bar_trade_date_index_migration_is_existing_safe_and_idempotent(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'daily-bar-index.db'}")
    Base.metadata.create_all(engine)
    expected = ("trade_date", "symbol")

    indexes = {
        str(item["name"]): tuple(str(column) for column in item["column_names"])
        for item in inspect(engine).get_indexes("daily_bars")
    }
    assert indexes["ix_daily_bars_trade_date_symbol"] == expected
    assert (
        ensure_index(
            engine,
            "daily_bars",
            "ix_daily_bars_trade_date_symbol",
            expected,
        )
        is False
    )

    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_daily_bars_trade_date_symbol"))
    assert "daily_bars.ix_daily_bars_trade_date_symbol" in run_migrations(engine)
    assert run_migrations(engine) == []

    with engine.connect() as connection:
        plan = connection.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT count(DISTINCT symbol) FROM daily_bars "
                "WHERE trade_date = :trade_date AND length(symbol) = 6 "
                "AND source != 'mock' AND close > 0"
            ),
            {"trade_date": "2026-07-21"},
        ).all()
    assert any(
        "ix_daily_bars_trade_date_symbol" in str(row[-1]) and "SEARCH" in str(row[-1])
        for row in plan
    )


def test_valuation_trade_date_index_migration_is_existing_safe_and_idempotent(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'valuation-index.db'}")
    Base.metadata.create_all(engine)
    expected = ("trade_date", "symbol")

    indexes = {
        str(item["name"]): tuple(str(column) for column in item["column_names"])
        for item in inspect(engine).get_indexes("valuation_daily")
    }
    assert indexes["ix_valuation_trade_date_symbol"] == expected

    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_valuation_trade_date_symbol"))
    assert "valuation_daily.ix_valuation_trade_date_symbol" in run_migrations(engine)
    assert run_migrations(engine) == []

    with engine.connect() as connection:
        plan = connection.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT symbol, pe_ttm, pb_mrq "
                "FROM valuation_daily WHERE trade_date = :trade_date"
            ),
            {"trade_date": "2026-07-21"},
        ).all()
    assert any(
        "ix_valuation_trade_date_symbol" in str(row[-1]) and "SEARCH" in str(row[-1])
        for row in plan
    )


def test_financial_pit_index_migration_is_existing_safe_and_idempotent(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'financial-pit-index.db'}")
    Base.metadata.create_all(engine)
    index_name = "ix_financial_pit_symbol_metric_period"
    expected = ("symbol", "metric", "report_period")

    indexes = {
        str(item["name"]): tuple(str(column) for column in item["column_names"])
        for item in inspect(engine).get_indexes("financial_indicators")
    }
    assert indexes[index_name] == expected
    with Session(engine) as session:
        session.add(
            FinancialIndicator(
                symbol="600519",
                report_period="2025Q4",
                metric="roe",
                value=0.25,
                source="test",
                available_time=datetime(2026, 4, 30, tzinfo=UTC),
            )
        )
        session.commit()

    with engine.begin() as connection:
        connection.execute(text(f"DROP INDEX {index_name}"))
    assert f"financial_indicators.{index_name}" in run_migrations(engine)
    assert run_migrations(engine) == []

    with engine.connect() as connection:
        row_count = connection.scalar(
            text("SELECT COUNT(*) FROM financial_indicators")
        )
        plan = connection.execute(
            text(
                "EXPLAIN QUERY PLAN "
                "SELECT symbol, metric, max(report_period) "
                "FROM financial_indicators "
                "WHERE symbol = :symbol AND metric = :metric "
                "AND value IS NOT NULL AND available_time <= :cutoff "
                "GROUP BY symbol, metric"
            ),
            {
                "symbol": "600519",
                "metric": "roe",
                "cutoff": "2026-07-21 11:30:00",
            },
        ).all()

    details = "\n".join(str(row[-1]) for row in plan)
    assert row_count == 1
    assert index_name in details
    assert "SEARCH financial_indicators" in details
    assert "USE TEMP B-TREE FOR GROUP BY" not in details


def test_factor_value_trade_date_index_migration_is_existing_safe_and_idempotent(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'factor-value-date-index.db'}")
    Base.metadata.create_all(engine)
    index_name = "ix_factor_values_trade_date_symbol_factor"
    expected = ("trade_date", "symbol", "factor")

    indexes = {
        str(item["name"]): tuple(str(column) for column in item["column_names"])
        for item in inspect(engine).get_indexes("factor_values")
    }
    assert indexes[index_name] == expected
    with Session(engine) as session:
        session.add(
            FactorValue(
                symbol="600519",
                trade_date=datetime(2026, 7, 21, tzinfo=UTC).date(),
                factor="volatility_20d",
                raw=0.1,
                zscore=0.2,
                model_version="factor-v1.0.0",
            )
        )
        session.commit()

    with engine.begin() as connection:
        connection.execute(text(f"DROP INDEX {index_name}"))
    assert f"factor_values.{index_name}" in run_migrations(engine)
    assert run_migrations(engine) == []

    with engine.connect() as connection:
        row_count = connection.scalar(text("SELECT COUNT(*) FROM factor_values"))
        plan = connection.execute(
            text(
                "EXPLAIN QUERY PLAN "
                "SELECT symbol, factor, raw, zscore, model_version "
                "FROM factor_values "
                "WHERE trade_date = :trade_date "
                "AND factor IN ('net_profit_yoy', 'volatility_20d', "
                "'pe_percentile', 'pb_percentile') "
                "ORDER BY symbol, factor"
            ),
            {"trade_date": "2026-07-21"},
        ).all()

    details = "\n".join(str(row[-1]) for row in plan)
    assert row_count == 1
    assert index_name in details
    assert "SEARCH factor_values" in details
    assert "USE TEMP B-TREE FOR ORDER BY" not in details


def test_adj_factor_trade_date_index_migration_is_existing_safe_and_idempotent(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'adj-factor-index.db'}")
    Base.metadata.create_all(engine)
    expected = ("trade_date", "symbol")

    indexes = {
        str(item["name"]): tuple(str(column) for column in item["column_names"])
        for item in inspect(engine).get_indexes("adj_factors")
    }
    assert indexes["ix_adj_trade_date_symbol"] == expected

    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_adj_trade_date_symbol"))
    assert "adj_factors.ix_adj_trade_date_symbol" in run_migrations(engine)
    assert run_migrations(engine) == []

    with engine.connect() as connection:
        plan = connection.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT count(DISTINCT symbol) FROM adj_factors "
                "WHERE trade_date = :trade_date AND adj_factor > 0"
            ),
            {"trade_date": "2026-07-21"},
        ).all()
    assert any(
        "ix_adj_trade_date_symbol" in str(row[-1]) and "SEARCH" in str(row[-1])
        for row in plan
    )


def test_ensure_index_rejects_an_existing_name_with_wrong_columns(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'wrong-index.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sample (first TEXT, second TEXT)"))
        connection.execute(text("CREATE INDEX ix_sample_pair ON sample (second, first)"))

    with pytest.raises(ValueError, match="already exists with columns"):
        ensure_index(engine, "sample", "ix_sample_pair", ("first", "second"))


def test_ensure_index_accepts_equivalent_columns_under_an_existing_name(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'equivalent-index.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sample (first TEXT, second TEXT)"))
        connection.execute(text("CREATE INDEX ix_legacy_pair ON sample (first, second)"))

    assert ensure_index(engine, "sample", "ix_new_pair", ("first", "second")) is False
    assert {str(item["name"]) for item in inspect(engine).get_indexes("sample")} == {
        "ix_legacy_pair"
    }


def test_ensure_index_does_not_treat_a_partial_index_as_equivalent(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'partial-index.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sample (first TEXT, second TEXT)"))
        connection.execute(
            text(
                "CREATE INDEX ix_partial_pair ON sample (first, second) "
                "WHERE first IS NOT NULL"
            )
        )

    assert ensure_index(engine, "sample", "ix_full_pair", ("first", "second")) is True
    assert {str(item["name"]) for item in inspect(engine).get_indexes("sample")} == {
        "ix_full_pair",
        "ix_partial_pair",
    }


@pytest.mark.parametrize(
    ("table", "index_name", "columns"),
    [
        ("daily_bars", "ix_daily_bars_symbol_date", ("symbol", "trade_date")),
        ("adj_factors", "ix_adj_symbol_date", ("symbol", "trade_date")),
        ("adj_factors", "ix_adj_factors_symbol", ("symbol",)),
        ("valuation_daily", "ix_valuation_symbol_date", ("symbol", "trade_date")),
        ("valuation_daily", "ix_valuation_daily_symbol", ("symbol",)),
        ("financial_indicators", "ix_financial_indicators_symbol", ("symbol",)),
        ("factor_values", "ix_factor_values_symbol", ("symbol",)),
        ("composite_scores", "ix_composite_scores_symbol", ("symbol",)),
        ("stock_scores", "ix_stock_scores_symbol", ("symbol",)),
    ],
)
def test_redundant_index_migrations_require_a_covering_key_and_are_idempotent(
    tmp_path: Path,
    table: str,
    index_name: str,
    columns: tuple[str, ...],
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / f'{index_name}.db'}")
    Base.metadata.create_all(engine)
    column_sql = ", ".join(columns)
    with engine.begin() as connection:
        connection.execute(text(f"CREATE INDEX {index_name} ON {table} ({column_sql})"))

    applied = run_migrations(engine)
    remaining = {str(item["name"]) for item in inspect(engine).get_indexes(table)}

    assert f"{table}.{index_name}:removed" in applied
    assert index_name not in remaining
    assert run_migrations(engine) == []


def test_drop_redundant_index_fails_closed_without_a_covering_key(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'unsafe-index-drop.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sample (first TEXT, second TEXT)"))
        connection.execute(text("CREATE INDEX ix_sample_first ON sample (first)"))

    with pytest.raises(ValueError, match="no non-partial key covers prefix"):
        drop_redundant_index(engine, "sample", "ix_sample_first", ("first",))

    assert {str(item["name"]) for item in inspect(engine).get_indexes("sample")} == {
        "ix_sample_first"
    }


def test_run_job_records_stats() -> None:
    name = "test_phase2_audit"

    def task() -> dict[str, Any]:
        return {"processed": 3}

    register(JobSpec(name=name, func=task, trigger=IntervalTrigger(hours=1)))
    try:
        record = run_job(name)
    finally:
        JOBS.pop(name, None)

    assert record.status == "ok"
    assert record.stats == {"processed": 3}
    assert record.finished_at is not None
    assert record.error is None


def test_run_job_passes_explicit_kwargs() -> None:
    name = "test_phase2_force"

    def task(*, force: bool = False) -> dict[str, Any]:
        return {"force": force}

    register(JobSpec(name=name, func=task, trigger=IntervalTrigger(hours=1)))
    try:
        record = run_job(name, force=True)
    finally:
        JOBS.pop(name, None)

    assert record.status == "ok"
    assert record.stats == {"force": True}


def test_run_job_persists_partial_stats_from_failure() -> None:
    name = "test_phase2_failure_stats"
    partial = {
        "total": 100,
        "processed": 20,
        "done": 0,
        "skipped": 0,
        "not_published": 0,
        "failed": [{"symbol": "600019", "error": "offline"}],
        "failed_count": 20,
        "rows_inserted": 0,
    }

    def task() -> dict[str, Any]:
        raise JobExecutionError("stopped after 20 failures", stats=partial)

    register(JobSpec(name=name, func=task, trigger=IntervalTrigger(hours=1)))
    try:
        record = run_job(name)
    finally:
        JOBS.pop(name, None)

    assert record.status == "failed"
    assert record.stats == partial
    assert record.finished_at is not None
    assert record.error == "JobExecutionError: stopped after 20 failures"


def test_run_job_serializes_concurrent_executions_of_the_same_name() -> None:
    name = "test_phase2_serialized_job"
    first_started = Event()
    second_invoked = Event()
    second_started = Event()
    release_first = Event()
    state_lock = Lock()
    calls = 0
    active = 0
    max_active = 0

    def task() -> dict[str, Any]:
        nonlocal calls, active, max_active
        with state_lock:
            index = calls
            calls += 1
            active += 1
            max_active = max(max_active, active)
        if index == 0:
            first_started.set()
            assert release_first.wait(timeout=5)
        else:
            second_started.set()
        with state_lock:
            active -= 1
        return {"index": index}

    def run_second() -> JobRun:
        second_invoked.set()
        return run_job(name)

    register(JobSpec(name=name, func=task, trigger=IntervalTrigger(hours=1)))
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(run_job, name)
            assert first_started.wait(timeout=5)
            second = executor.submit(run_second)
            assert second_invoked.wait(timeout=5)
            try:
                assert not second_started.wait(timeout=0.2)
            finally:
                release_first.set()
            records = [first.result(timeout=5), second.result(timeout=5)]
    finally:
        JOBS.pop(name, None)

    assert second_started.is_set()
    assert max_active == 1
    assert [record.status for record in records] == ["ok", "ok"]
    assert [record.stats for record in records] == [{"index": 0}, {"index": 1}]


def test_job_runs_window_keeps_latest_audit_for_each_registered_job(tmp_path: Any) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'job-coverage.db'}")
    Base.metadata.create_all(engine)
    names = ["coverage_job_a", "coverage_job_b", "coverage_job_c"]

    def task() -> dict[str, Any]:
        return {}

    for name in names:
        register(JobSpec(name=name, func=task, trigger=IntervalTrigger(hours=1)))
    try:
        started = datetime(2026, 7, 21, tzinfo=UTC)
        with Session(engine) as session:
            session.add_all(
                JobRun(
                    job_name=name,
                    started_at=started + timedelta(minutes=index),
                    status="ok",
                    stats={},
                )
                for index, name in enumerate(names)
            )
            session.add_all(
                JobRun(
                    job_name="coverage_job_a",
                    started_at=started + timedelta(hours=1, minutes=index),
                    status="ok",
                    stats={"index": index},
                )
                for index in range(20)
            )
            session.commit()
            payload = list_runs(limit=10, session=session)
    finally:
        for name in names:
            JOBS.pop(name, None)

    assert len(payload["runs"]) == 10
    assert set(names) <= {row["job_name"] for row in payload["runs"]}


def test_backfill_events_processes_only_missing_or_legacy_rows(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'event-backfill.db'}")
    Base.metadata.create_all(engine)
    published_at = datetime(2026, 7, 22, 1, tzinfo=UTC)
    with Session(engine) as session:
        missing = Disclosure(
            symbol="600519",
            title="重大项目中标公告",
            url="https://example.test/missing.pdf",
            published_at=published_at,
        )
        legacy = Disclosure(
            symbol="600519",
            title="交易所问询函公告",
            url="https://example.test/legacy.pdf",
            published_at=published_at,
        )
        normalized = Disclosure(
            symbol="600519",
            title="股份回购公告",
            url="https://example.test/normalized.pdf",
            published_at=published_at,
        )
        session.add_all([missing, legacy, normalized])
        session.flush()
        session.add_all(
            [
                DomainEvent(
                    symbol=legacy.symbol,
                    event_type="disclosure",
                    title=legacy.title,
                    direction=0.0,
                    strength=0.5,
                    source_ref=f"disclosure:{legacy.id}",
                    occurred_at=published_at,
                ),
                DomainEvent(
                    symbol=normalized.symbol,
                    event_type="disclosure",
                    title=normalized.title,
                    direction=0.5,
                    strength=0.5,
                    summary="buyback｜规则识别回购公告",
                    source_ref=f"disclosure:{normalized.id}",
                    occurred_at=published_at,
                ),
            ]
        )
        session.commit()
    active_sessions = 0

    @contextmanager
    def local_session() -> Iterator[Session]:
        nonlocal active_sessions
        active_sessions += 1
        session = Session(engine)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            active_sessions -= 1

    calls: list[str] = []

    def fake_classify(title: str) -> DisclosureExtraction:
        # The row snapshot session must be closed before an LLM wait starts.
        assert active_sessions == 0
        calls.append(title)
        is_contract = "中标" in title
        return DisclosureExtraction(
            subtype="contract" if is_contract else "regulation",
            direction=0.5 if is_contract else -0.5,
            strength=0.5,
            summary="重大项目中标" if is_contract else "交易所问询",
            source_quote="中标" if is_contract else "问询",
            source="llm" if is_contract else "rule",
        )

    monkeypatch.setattr(event_backfill, "get_session", local_session)
    monkeypatch.setattr(event_backfill, "classify_disclosure", fake_classify)

    first = event_backfill.backfill_events()
    second = event_backfill.backfill_events()

    assert first == {
        "total": 3,
        "pending": 2,
        "scanned": 2,
        "extracted": 2,
        "llm": 1,
        "fallback": 1,
        "failed": 0,
        "skipped": 1,
        "failures": [],
        "duration_seconds": first["duration_seconds"],
    }
    assert second["pending"] == 0
    assert second["scanned"] == 0
    assert second["skipped"] == 3
    assert calls == ["重大项目中标公告", "交易所问询函公告"]


def test_backfill_events_raises_with_partial_stats_when_any_row_fails(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'event-backfill-failed.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Disclosure(
                symbol="600519",
                title="重大项目中标公告",
                url="https://example.test/failed.pdf",
                published_at=datetime(2026, 7, 22, 1, tzinfo=UTC),
            )
        )
        session.commit()

    opened_sessions = 0

    @contextmanager
    def local_session() -> Iterator[Session]:
        nonlocal opened_sessions
        opened_sessions += 1
        current_session = opened_sessions
        with Session(engine) as session:
            try:
                yield session
                if current_session == 3:
                    session.rollback()
                    raise RuntimeError("commit failed")
                session.commit()
            except Exception:
                session.rollback()
                raise

    def fake_classification(title: str) -> DisclosureExtraction:
        return DisclosureExtraction(
            subtype="contract",
            direction=0.5,
            strength=0.5,
            summary=f"测试分类：{title}",
            source_quote="中标",
            source="llm",
        )

    monkeypatch.setattr(event_backfill, "get_session", local_session)
    monkeypatch.setattr(event_backfill, "classify_disclosure", fake_classification)

    with pytest.raises(JobExecutionError, match="公告事件回填失败 1 条") as caught:
        event_backfill.backfill_events()

    assert caught.value.stats["pending"] == 1
    assert caught.value.stats["scanned"] == 1
    assert caught.value.stats["extracted"] == 0
    assert caught.value.stats["llm"] == 0
    assert caught.value.stats["fallback"] == 0
    assert caught.value.stats["failed"] == 1
    assert caught.value.stats["failures"] == [
        {
            "disclosure_id": 1,
            "error": "RuntimeError: commit failed",
        }
    ]


def test_event_backfill_job_is_registered_as_manual_only() -> None:
    previous = JOBS.get("backfill_events")
    try:
        event_backfill.register_event_backfill_job()
        spec = JOBS["backfill_events"]
        assert spec.func is event_backfill.backfill_events
        assert spec.trigger is None
    finally:
        if previous is None:
            JOBS.pop("backfill_events", None)
        else:
            JOBS["backfill_events"] = previous


def test_scheduler_skips_manual_only_jobs() -> None:
    original_jobs = dict(JOBS)

    def task() -> dict[str, Any]:
        return {}

    JOBS.clear()
    register(JobSpec(name="scheduled", func=task, trigger=IntervalTrigger(hours=1)))
    register(JobSpec(name="manual", func=task, trigger=None))
    try:
        scheduler = scheduler_module.start_scheduler(Settings(scheduler_enabled=True))
        assert scheduler is not None
        assert {job.id for job in scheduler.get_jobs()} == {"scheduled"}
    finally:
        scheduler_module.shutdown_scheduler()
        JOBS.clear()
        JOBS.update(original_jobs)
