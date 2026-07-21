from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event, Lock
from typing import Any

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from alphapilot.api.routes.jobs import list_runs
from alphapilot.db.migrate import ensure_column
from alphapilot.db.models import Base, JobRun
from alphapilot.jobs.registry import (
    JOBS,
    JobExecutionError,
    JobSpec,
    register,
    run_job,
)


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
