from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

S6_RELEASE_JOB_NAME = "research_factors_m3"


@dataclass(frozen=True, slots=True)
class JobRunExecutionContext:
    """Identity of the durable JobRun currently executing in this context."""

    run_id: int
    job_name: str


_CURRENT_JOB_RUN: ContextVar[JobRunExecutionContext | None] = ContextVar(
    "alphapilot_current_job_run",
    default=None,
)
_S6_RELEASE_ALLOWANCE: ContextVar[str | None] = ContextVar(
    "alphapilot_s6_release_allowance",
    default=None,
)


@contextmanager
def bind_job_run(*, run_id: int, job_name: str) -> Iterator[None]:
    """Bind one exact durable audit row while its JobSpec is executing."""

    token = _CURRENT_JOB_RUN.set(
        JobRunExecutionContext(run_id=run_id, job_name=job_name)
    )
    try:
        yield
    finally:
        _CURRENT_JOB_RUN.reset(token)


@contextmanager
def allow_s6_release_for_current_job(*, job_name: str) -> Iterator[None]:
    """Authorize only the formal S7 runner to present its own audit row."""

    if job_name != S6_RELEASE_JOB_NAME:
        raise ValueError(
            "S6 release self-allowance is restricted to research_factors_m3"
        )
    token = _S6_RELEASE_ALLOWANCE.set(job_name)
    try:
        yield
    finally:
        _S6_RELEASE_ALLOWANCE.reset(token)


def current_job_run() -> JobRunExecutionContext | None:
    """Return the exact JobRun identity bound by the registry, if any."""

    return _CURRENT_JOB_RUN.get()


def authorized_s6_release_job_run() -> JobRunExecutionContext | None:
    """Return the current row only when the formal runner explicitly allowed it."""

    current = _CURRENT_JOB_RUN.get()
    allowed_name = _S6_RELEASE_ALLOWANCE.get()
    if (
        current is None
        or allowed_name != S6_RELEASE_JOB_NAME
        or current.job_name != S6_RELEASE_JOB_NAME
    ):
        return None
    return current
