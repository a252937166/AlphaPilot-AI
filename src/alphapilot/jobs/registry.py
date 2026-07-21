from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from alphapilot.db.engine import get_session
from alphapilot.db.models import JobRun, utcnow


@dataclass(frozen=True, slots=True)
class JobSpec:
    name: str
    func: Callable[..., dict[str, Any]]
    trigger: CronTrigger | IntervalTrigger
    enabled_key: str | None = None


JOBS: dict[str, JobSpec] = {}


def register(spec: JobSpec) -> None:
    """Register or replace a job definition by its stable name."""

    JOBS[spec.name] = spec


def run_job(name: str, **kwargs: Any) -> JobRun:
    """Run a registered job and persist its full success or failure audit."""

    spec = JOBS.get(name)
    if spec is None:
        raise KeyError(name)

    with get_session() as session:
        record = JobRun(job_name=name, status="running", stats={})
        session.add(record)
        session.flush()
        run_id = record.id

    try:
        stats = spec.func(**kwargs)
    except Exception as exc:  # the audit row is the scheduler's failure boundary
        with get_session() as session:
            failed = session.get(JobRun, run_id)
            if failed is None:
                raise RuntimeError(f"job audit row disappeared: {run_id}") from exc
            failed.status = "failed"
            failed.finished_at = utcnow()
            failed.error = f"{type(exc).__name__}: {exc}"[:4000]
        return failed

    with get_session() as session:
        completed = session.get(JobRun, run_id)
        if completed is None:
            raise RuntimeError(f"job audit row disappeared: {run_id}")
        completed.status = "ok"
        completed.finished_at = utcnow()
        completed.stats = stats
    return completed
