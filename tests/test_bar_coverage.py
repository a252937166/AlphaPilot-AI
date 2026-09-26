"""Lists, scores and the action sheet wait for a complete daily-bar session."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session

from alphapilot.db.models import Base, DailyBar, JobRun
from alphapilot.services import bar_coverage as bc

D1, D2, D3 = date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24)
NOW = datetime(2026, 9, 24, 14, 0, tzinfo=UTC)  # 22:00 in Shanghai


def _rows(day: date, count: int) -> list[dict[str, Any]]:
    return [
        {
            "symbol": f"{600000 + i}",
            "trade_date": day,
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1.0,
            "amount": 1.0,
            "source": "test",
            "ingested_at": NOW,
        }
        for i in range(count)
    ]


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'bars.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        # A full day, a normal day with one failure, then a sync that stopped at 58%.
        s.execute(insert(DailyBar), _rows(D1, 100) + _rows(D2, 99) + _rows(D3, 58))
        s.commit()
        yield s


def test_a_half_synced_session_is_incomplete(session: Session) -> None:
    complete, details = bc.session_coverage(session, D2, now=NOW)
    assert complete and details["previous_bars"] == 100
    complete, details = bc.session_coverage(session, D3, now=NOW)
    assert not complete
    assert details == {
        "day": "2026-09-24",
        "bars": 58,
        "previous_session": "2026-09-23",
        "previous_bars": 99,
        "sync_running": False,
    }


def test_a_running_sync_blocks_but_a_stale_running_row_does_not(session: Session) -> None:
    naive_now = NOW.replace(tzinfo=None)
    session.add(
        JobRun(
            job_name="sync_daily_bars", status="running", started_at=naive_now - timedelta(hours=10)
        )
    )
    session.flush()
    assert bc.session_coverage(session, D2, now=NOW)[0]  # left behind by a killed process
    session.add(
        JobRun(
            job_name="sync_daily_bars", status="running", started_at=naive_now - timedelta(hours=1)
        )
    )
    session.flush()
    complete, details = bc.session_coverage(session, D2, now=NOW)
    assert not complete and details["sync_running"]


def test_horizon_ready_checks_the_sessions_the_scorer_would_use(session: Session) -> None:
    assert bc.horizon_ready(session, D1, 3, now=NOW) == (None, {})  # not matured yet
    assert bc.horizon_ready(session, D1, 1, now=NOW) == (True, {})
    ready, details = bc.horizon_ready(session, D1, 2, now=NOW)
    assert ready is False and details["day"] == "2026-09-24"
