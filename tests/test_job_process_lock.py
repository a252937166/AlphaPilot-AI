from __future__ import annotations

import multiprocessing
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from alphapilot.jobs.process_lock import (
    ProcessLockUnavailable,
    database_lock_key,
    job_process_lock,
    scheduler_process_lock,
)


def _timed_job_worker(
    database_url: str,
    lock_dir: str,
    label: str,
    ready: Any,
    start: Any,
    results: Any,
) -> None:
    ready.put(label)
    start.wait()
    with job_process_lock(database_url, "compute_factors", lock_dir=Path(lock_dir)):
        results.put((label, "enter", time.monotonic()))
        time.sleep(0.2)
        results.put((label, "exit", time.monotonic()))


def _holding_worker(
    database_url: str,
    lock_dir: str,
    acquired: Any,
    release: Any,
) -> None:
    with job_process_lock(database_url, "compute_factors", lock_dir=Path(lock_dir)):
        acquired.set()
        release.wait(timeout=10)


def _holding_scheduler_worker(
    database_url: str,
    lock_dir: str,
    acquired: Any,
    release: Any,
) -> None:
    with scheduler_process_lock(database_url, lock_dir=Path(lock_dir)):
        acquired.set()
        release.wait(timeout=10)


def _spawn_context() -> multiprocessing.context.BaseContext:
    return multiprocessing.get_context("spawn")


def test_database_key_ignores_credentials_but_separates_databases() -> None:
    first = database_lock_key("postgresql+psycopg://alpha:secret@db:5432/primary")
    other_role = database_lock_key("postgresql+psycopg://reader:different@db/primary")
    other_database = database_lock_key("postgresql+psycopg://alpha:secret@db/secondary")

    assert first == other_role
    assert first != other_database


def test_same_job_never_overlaps_between_processes(tmp_path: Path) -> None:
    context = _spawn_context()
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    database_url = f"sqlite:///{tmp_path / 'shared.db'}"
    lock_dir = tmp_path / "locks"
    workers = [
        context.Process(
            target=_timed_job_worker,
            args=(database_url, str(lock_dir), label, ready, start, results),
        )
        for label in ("first", "second")
    ]
    for worker in workers:
        worker.start()
    assert {ready.get(timeout=10), ready.get(timeout=10)} == {"first", "second"}
    start.set()

    events = [results.get(timeout=10) for _ in range(4)]
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0

    intervals: dict[str, dict[str, float]] = {}
    for label, event, timestamp in events:
        intervals.setdefault(label, {})[event] = timestamp
    ordered = sorted(intervals.values(), key=lambda item: item["enter"])
    assert ordered[0]["exit"] <= ordered[1]["enter"]


def test_scheduler_singleton_fails_fast_when_held(tmp_path: Path) -> None:
    context = _spawn_context()
    acquired = context.Event()
    release = context.Event()
    database_url = f"sqlite:///{tmp_path / 'scheduler.db'}"
    lock_dir = tmp_path / "locks"

    holder = context.Process(
        target=_holding_scheduler_worker,
        args=(database_url, str(lock_dir), acquired, release),
    )
    holder.start()
    assert acquired.wait(timeout=10)
    singleton = scheduler_process_lock(database_url, lock_dir=lock_dir)
    started = time.monotonic()
    with pytest.raises(ProcessLockUnavailable):
        singleton.acquire()
    assert time.monotonic() - started < 1.0
    release.set()
    holder.join(timeout=10)
    assert holder.exitcode == 0


def test_crashed_subprocess_releases_lock(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'crash.db'}"
    lock_dir = tmp_path / "locks"
    source_root = Path(__file__).resolve().parents[1] / "src"
    code = "\n".join(
        (
            "import os",
            "from pathlib import Path",
            "from alphapilot.jobs.process_lock import job_process_lock",
            f"lock = job_process_lock({database_url!r}, 'sync_daily_bars', "
            f"lock_dir=Path({str(lock_dir)!r}))",
            "lock.acquire()",
            "print('locked', flush=True)",
            "os._exit(23)",
        )
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(source_root), environment.get("PYTHONPATH", "")))
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "locked"
    _, stderr = child.communicate(timeout=10)
    assert child.returncode == 23, stderr

    with job_process_lock(
        database_url,
        "sync_daily_bars",
        lock_dir=lock_dir,
        blocking=False,
    ):
        pass


def test_same_job_for_different_databases_does_not_interlock(tmp_path: Path) -> None:
    context = _spawn_context()
    acquired = context.Event()
    release = context.Event()
    first_url = f"sqlite:///{tmp_path / 'first.db'}"
    second_url = f"sqlite:///{tmp_path / 'second.db'}"
    lock_dir = tmp_path / "locks"
    holder = context.Process(
        target=_holding_worker,
        args=(first_url, str(lock_dir), acquired, release),
    )
    holder.start()
    assert acquired.wait(timeout=10)

    with job_process_lock(
        second_url,
        "compute_factors",
        lock_dir=lock_dir,
        blocking=False,
    ) as second:
        assert second.locked

    release.set()
    holder.join(timeout=10)
    assert holder.exitcode == 0


def test_lock_permissions_are_private(tmp_path: Path) -> None:
    lock = job_process_lock(
        f"sqlite:///{tmp_path / 'permissions.db'}",
        "compute_factors",
        lock_dir=tmp_path / "locks",
    )
    with lock:
        assert stat.S_IMODE(lock.path.parent.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(lock.path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(lock.path.stat().st_mode) == 0o600
