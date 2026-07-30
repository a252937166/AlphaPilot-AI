from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import cast

import pytest

from alphapilot.db.models import JobRun
from alphapilot.jobs import registry
from alphapilot.jobs.registry import JobSpec


def test_run_job_enters_database_scoped_process_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    name = "process-lock-contract-test"
    spec = JobSpec(name=name, func=lambda: {}, trigger=None)
    completed = cast(JobRun, object())

    @contextmanager
    def process_lock(database_url: str, job_name: str) -> Iterator[None]:
        calls.append((database_url, job_name))
        yield

    previous = registry.JOBS.get(name)
    registry.JOBS[name] = spec
    monkeypatch.setattr(
        registry,
        "get_settings",
        lambda: SimpleNamespace(database_url="sqlite:////tmp/registry-lock.db"),
    )
    monkeypatch.setattr(registry, "job_process_lock", process_lock)
    monkeypatch.setattr(
        registry,
        "_run_job_locked",
        lambda _name, _spec, _kwargs: completed,
    )
    try:
        assert registry.run_job(name) is completed
    finally:
        if previous is None:
            registry.JOBS.pop(name, None)
        else:
            registry.JOBS[name] = previous

    assert calls == [("sqlite:////tmp/registry-lock.db", name)]
