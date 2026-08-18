from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Any, Literal

from apscheduler.triggers.base import BaseTrigger

from alphapilot.core.config import get_settings
from alphapilot.core.job_execution_context import bind_job_run
from alphapilot.data.baostock_provider import baostock_session_scope
from alphapilot.db.engine import get_session
from alphapilot.db.models import JobRun, utcnow
from alphapilot.jobs.process_lock import job_process_lock
from alphapilot.services.notifications import push_job_failure


@dataclass(frozen=True, slots=True)
class JobSpec:
    name: str
    func: Callable[..., dict[str, Any] | JobOutcome]
    trigger: BaseTrigger | None
    enabled_key: str | None = None
    misfire_grace_time: int | None = None

    def __post_init__(self) -> None:
        if self.misfire_grace_time is not None and (
            isinstance(self.misfire_grace_time, bool)
            or not isinstance(self.misfire_grace_time, int)
            or self.misfire_grace_time <= 0
        ):
            raise ValueError("job misfire_grace_time must be a positive integer")


@dataclass(frozen=True, slots=True)
class JobOutcome:
    """A successful terminal outcome whose status is explicit.

    Existing jobs may keep returning a plain stats mapping, which remains an
    ``ok`` outcome.  ``degraded`` is deliberately successful at the execution
    boundary: it is persisted without an exception or failure notification,
    while callers and acceptance gates can still distinguish it from ``ok``.
    """

    status: Literal["ok", "degraded"]
    stats: dict[str, Any]

    def __post_init__(self) -> None:
        if self.status not in {"ok", "degraded"}:
            raise ValueError("JobOutcome status must be ok or degraded")
        object.__setattr__(self, "stats", dict(self.stats))


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
        with (
            baostock_session_scope(),
            bind_job_run(run_id=run_id, job_name=name),
        ):
            result = spec.func(**kwargs)
    except Exception as exc:  # the audit row is the scheduler's failure boundary
        with get_session() as session:
            failed = session.get(JobRun, run_id)
            if failed is None:
                raise RuntimeError(f"job audit row disappeared: {run_id}") from exc
            failed.status = "failed"
            failed.finished_at = utcnow()
            failed.error = f"{type(exc).__name__}: {exc}"[:4000]
            failed.stats = dict(exc.stats) if isinstance(exc, JobExecutionError) else {}
            push_job_failure(session, failed)
        return failed
    with get_session() as session:
        completed = session.get(JobRun, run_id)
        if completed is None:
            raise RuntimeError(f"job audit row disappeared: {run_id}")
        if isinstance(result, JobOutcome):
            completed.status = result.status
            stats = result.stats
        else:
            completed.status = "ok"
            stats = result
        completed.finished_at = utcnow()
        completed.error = None
        completed.stats = stats
    return completed


def run_job(name: str, **kwargs: Any) -> JobRun:
    """Run and audit a job, serializing concurrent executions of the same name."""

    spec = JOBS.get(name)
    if spec is None:
        raise KeyError(name)

    with (
        _job_lock(name),
        job_process_lock(get_settings().database_url, name),
    ):
        return _run_job_locked(name, spec, kwargs)
