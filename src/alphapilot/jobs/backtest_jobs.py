from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from alphapilot.backtest.adjust import sync_adj_factors
from alphapilot.db.engine import get_session
from alphapilot.jobs.registry import JobSpec, register


def sync_adj_factors_job() -> dict[str, Any]:
    """Run the incremental, source-audited adjustment-factor sync."""

    with get_session() as session:
        return sync_adj_factors(session)


def register_backtest_jobs() -> None:
    register(
        JobSpec(
            name="sync_adj_factors",
            func=sync_adj_factors_job,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=18,
                minute=50,
                timezone=ZoneInfo("Asia/Shanghai"),
            ),
        )
    )
