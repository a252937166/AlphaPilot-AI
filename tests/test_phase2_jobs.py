from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from alphapilot.api.routes.jobs import list_runs
from alphapilot.db.migrate import ensure_column
from alphapilot.db.models import Base, JobRun
from alphapilot.jobs.registry import JOBS, JobSpec, register, run_job


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
