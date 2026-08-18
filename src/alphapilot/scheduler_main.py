from __future__ import annotations

import logging
import signal
from threading import Event
from types import FrameType

from sqlalchemy import text

from alphapilot.core.config import Settings, get_settings
from alphapilot.core.logging import configure_logging
from alphapilot.data.baostock_provider import close_baostock_session
from alphapilot.db.engine import get_session
from alphapilot.futu.client import get_futu_client
from alphapilot.jobs import register_builtin_jobs
from alphapilot.jobs.process_lock import (
    ProcessLockUnavailable,
    scheduler_process_lock,
)
from alphapilot.jobs.scheduler import shutdown_scheduler, start_scheduler

logger = logging.getLogger(__name__)


class SchedulerConfigurationError(RuntimeError):
    """The daemon was asked to start outside its fail-closed safety contract."""


def validate_scheduler_settings(settings: Settings) -> None:
    violations: list[str] = []
    if not settings.scheduler_enabled:
        violations.append("scheduler_enabled must be true")
    if settings.trading_mode != "research":
        violations.append("trading_mode must be research")
    if settings.live_trading_enabled:
        violations.append("live_trading_enabled must be false")
    if settings.paper_trading_enabled:
        violations.append("paper_trading_enabled must be false")
    if settings.paper_auto_trading_enabled:
        violations.append("paper_auto_trading_enabled must be false")
    if settings.futu_enable_account_mutation:
        violations.append("futu_enable_account_mutation must be false")
    if settings.futu_enable_trade:
        violations.append("futu_enable_trade must be false")
    if bool(getattr(settings, "baostock_financial_sync_enabled", False)):
        violations.append("baostock_financial_sync_enabled must be false")
    if violations:
        raise SchedulerConfigurationError("; ".join(violations))


def assert_database_ready() -> None:
    """Read existing schema only; migrations remain owned by the API process."""

    with get_session() as session:
        session.execute(text("SELECT 1 FROM job_runs LIMIT 1")).first()
        session.execute(text("SELECT 1 FROM runtime_flags LIMIT 1")).first()


def run_scheduler_daemon(
    *,
    settings: Settings | None = None,
    stop_event: Event | None = None,
) -> None:
    resolved = settings or get_settings()
    validate_scheduler_settings(resolved)
    assert_database_ready()
    register_builtin_jobs()
    shutdown = stop_event or Event()

    with scheduler_process_lock(resolved.database_url):
        scheduler = start_scheduler(resolved)
        if scheduler is None:
            raise SchedulerConfigurationError("scheduler did not start")
        try:
            job_ids = sorted(job.id for job in scheduler.get_jobs())
            logger.info(
                "dedicated scheduler started jobs=%s count=%d",
                job_ids,
                len(job_ids),
            )
            shutdown.wait()
        finally:
            logger.info("dedicated scheduler stopping gracefully")
            shutdown_scheduler(wait=True)
            close_baostock_session()
            get_futu_client().close()


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop_event = Event()

    def request_stop(signum: int, _frame: FrameType | None) -> None:
        logger.info("scheduler received signal=%d", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        run_scheduler_daemon(settings=settings, stop_event=stop_event)
    except (ProcessLockUnavailable, SchedulerConfigurationError, RuntimeError) as exc:
        logger.error("dedicated scheduler failed: %s: %s", type(exc).__name__, exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
