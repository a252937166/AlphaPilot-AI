from __future__ import annotations

from threading import Lock
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import SchedulerNotRunningError

from alphapilot.core.config import Settings, get_settings
from alphapilot.jobs.registry import JOBS, run_job

_lock = Lock()
_scheduler: BackgroundScheduler | None = None


def _job_enabled(settings: Settings, enabled_key: str | None) -> bool:
    if enabled_key is None:
        return True
    value = getattr(settings, enabled_key, None)
    if not isinstance(value, bool):
        raise ValueError(f"job enabled_key is not a boolean setting: {enabled_key}")
    return value


def start_scheduler(settings: Settings | None = None) -> BackgroundScheduler | None:
    """Start the process-local scheduler when explicitly enabled."""

    global _scheduler
    resolved = settings or get_settings()
    if not resolved.scheduler_enabled:
        return None

    with _lock:
        if _scheduler is not None and _scheduler.running:
            return _scheduler

        scheduler = BackgroundScheduler(
            timezone=ZoneInfo("Asia/Shanghai"),
            job_defaults={"max_instances": 1, "coalesce": True},
        )
        for name, spec in JOBS.items():
            if spec.trigger is None or not _job_enabled(resolved, spec.enabled_key):
                continue
            job_options: dict[str, int] = {}
            if spec.misfire_grace_time is not None:
                job_options["misfire_grace_time"] = spec.misfire_grace_time
            scheduler.add_job(
                run_job,
                trigger=spec.trigger,
                args=[name],
                id=name,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                **job_options,
            )
        scheduler.start()
        _scheduler = scheduler
        return scheduler


def shutdown_scheduler(*, wait: bool = False) -> None:
    """Pause and stop the process-local scheduler.

    The API compatibility path keeps the historical non-blocking shutdown.
    The dedicated scheduler daemon requests a graceful wait after first
    ensuring deployment/restart happens outside an active job window.
    """

    global _scheduler
    with _lock:
        scheduler = _scheduler
        _scheduler = None
    if scheduler is None:
        return
    try:
        if scheduler.running:
            scheduler.pause()
        scheduler.shutdown(wait=wait)
    except SchedulerNotRunningError:
        return
