"""Scheduled data and analytics jobs."""

from __future__ import annotations


def register_builtin_jobs() -> None:
    """Register built-in jobs explicitly without network work at import time."""

    from alphapilot.jobs.alert_outcomes import register_alert_outcomes_job
    from alphapilot.jobs.calendar_sync import register_calendar_job
    from alphapilot.jobs.daily_bars import register_daily_bars_job
    from alphapilot.jobs.event_backfill import register_event_backfill_job
    from alphapilot.jobs.factors import register_factor_job
    from alphapilot.jobs.financials import register_financials_job
    from alphapilot.jobs.market_poll import register_market_poll_job
    from alphapilot.jobs.order_sync import register_order_sync_job
    from alphapilot.jobs.portfolio_snapshot import register_portfolio_jobs
    from alphapilot.jobs.score_outcomes import register_score_outcomes_job
    from alphapilot.jobs.sector_forecast import register_sector_forecast_job
    from alphapilot.jobs.sectors_sync import register_sector_jobs
    from alphapilot.jobs.style import register_style_job
    from alphapilot.jobs.universe import register_universe_job

    register_universe_job()
    register_calendar_job()
    register_daily_bars_job()
    register_event_backfill_job()
    register_financials_job()
    register_factor_job()
    register_style_job()
    register_market_poll_job()
    register_order_sync_job()
    register_portfolio_jobs()
    register_alert_outcomes_job()
    register_sector_jobs()
    register_sector_forecast_job()
    register_score_outcomes_job()
