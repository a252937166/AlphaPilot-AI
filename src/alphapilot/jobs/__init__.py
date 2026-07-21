"""Scheduled data and analytics jobs."""

from __future__ import annotations


def register_builtin_jobs() -> None:
    """Register built-in jobs explicitly without network work at import time."""

    from alphapilot.jobs.universe import register_universe_job

    register_universe_job()
