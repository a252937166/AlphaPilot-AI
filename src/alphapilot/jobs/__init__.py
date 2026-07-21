"""Scheduled data and analytics jobs."""

from __future__ import annotations


def register_builtin_jobs() -> None:
    """Register built-in jobs explicitly without network work at import time."""

    from alphapilot.jobs.calendar_sync import register_calendar_job
    from alphapilot.jobs.daily_bars import register_daily_bars_job
    from alphapilot.jobs.market_poll import register_market_poll_job
    from alphapilot.jobs.sectors_sync import register_sector_jobs
    from alphapilot.jobs.universe import register_universe_job

    register_universe_job()
    register_calendar_job()
    register_daily_bars_job()
    register_market_poll_job()
    register_sector_jobs()
