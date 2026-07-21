from __future__ import annotations

from typing import Any

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import create_engine, inspect, text

from alphapilot.db.migrate import ensure_column
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
