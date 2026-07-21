from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
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
_JOB_LOCKS: dict[str, Lock] = {}
_JOB_LOCKS_GUARD = Lock()


class JobExecutionError(RuntimeError):
    """A job failure that carries its latest JSON-serializable progress stats."""

    def __init__(self, message: str, *, stats: dict[str, Any]) -> None:
        super().__init__(message)
        self.stats = dict(stats)


def register(spec: JobSpec) -> None:
    """Register or replace a job definition by its stable name."""

    JOBS[spec.name] = spec


def _job_lock(name: str) -> Lock:
    with _JOB_LOCKS_GUARD:
        lock = _JOB_LOCKS.get(name)
        if lock is None:
            lock = Lock()
            _JOB_LOCKS[name] = lock
        return lock


def _run_job_locked(name: str, spec: JobSpec, kwargs: dict[str, Any]) -> JobRun:
    """Execute after the caller has serialized this job name."""

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
            failed.stats = dict(exc.stats) if isinstance(exc, JobExecutionError) else {}
        return failed

    with get_session() as session:
        completed = session.get(JobRun, run_id)
        if completed is None:
            raise RuntimeError(f"job audit row disappeared: {run_id}")
        completed.status = "ok"
        completed.finished_at = utcnow()
        completed.stats = stats
    return completed


def run_job(name: str, **kwargs: Any) -> JobRun:
    """Run and audit a job, serializing concurrent executions of the same name."""

    spec = JOBS.get(name)
    if spec is None:
        raise KeyError(name)

    with _job_lock(name):
        return _run_job_locked(name, spec, kwargs)
