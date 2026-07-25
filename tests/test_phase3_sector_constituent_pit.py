from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from alphapilot.db.migrate import run_migrations
from alphapilot.db.models import (
    Base,
    SectorConstituent,
    SectorConstituentSnapshot,
)
from alphapilot.engines.factors import _pit_sector_memberships
from alphapilot.jobs import sectors_sync
from alphapilot.jobs.registry import JOBS


def _local_session(engine: Any) -> Any:
    @contextmanager
    def local_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    return local_session


def test_sector_constituent_snapshot_migration_is_idempotent(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pit-migration.db'}")
    Base.metadata.create_all(engine)
    SectorConstituentSnapshot.__table__.drop(engine)

    assert run_migrations(engine) == ["sector_constituent_snapshots"]
    assert run_migrations(engine) == []
    columns = {
        str(item["name"])
        for item in inspect(engine).get_columns("sector_constituent_snapshots")
    }
    assert columns == {
        "id",
        "plate_code",
        "symbol",
        "as_of_date",
        "available_time",
    }
    unique_constraints = inspect(engine).get_unique_constraints(
        "sector_constituent_snapshots"
    )
    assert any(
        tuple(item["column_names"]) == ("plate_code", "symbol", "as_of_date")
        for item in unique_constraints
    )


def test_daily_snapshot_preserves_first_available_time_on_idempotent_rerun(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pit-snapshot.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    first_capture = datetime(2026, 7, 27, 7, 10, tzinfo=UTC)
    with local_session() as session:
        session.add_all(
            [
                SectorConstituent(
                    plate_code="SH.LIST0001",
                    plate_name="板块一",
                    symbol="SH.600000",
                    refreshed_at=datetime(2026, 7, 26, 1, tzinfo=UTC),
                ),
                SectorConstituent(
                    plate_code="SH.LIST0002",
                    plate_name="未来才可见",
                    symbol="SZ.000001",
                    refreshed_at=datetime(2026, 7, 27, 8, tzinfo=UTC),
                ),
            ]
        )

    monkeypatch.setattr(sectors_sync, "get_session", local_session)
    monkeypatch.setattr(sectors_sync, "_utc_now", lambda: first_capture)
    first = sectors_sync.snapshot_sector_constituents()
    monkeypatch.setattr(
        sectors_sync,
        "_utc_now",
        lambda: datetime(2026, 7, 27, 9, 0, tzinfo=UTC),
    )
    second = sectors_sync.snapshot_sector_constituents()

    assert first["inserted"] == 1
    assert first["visible_source_rows"] == 1
    assert second["inserted"] == 1
    assert second["skipped_existing"] == 1
    with local_session() as session:
        rows = list(
            session.scalars(
                select(SectorConstituentSnapshot).order_by(
                    SectorConstituentSnapshot.symbol
                )
            )
        )
    assert len(rows) == 2
    first_row = next(row for row in rows if row.symbol == "SH.600000")
    assert first_row.available_time == first_capture.replace(tzinfo=None)


def test_factor_membership_uses_exact_day_and_available_time_cutoff(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pit-factor.db'}")
    Base.metadata.create_all(engine)
    target = date(2026, 7, 27)
    cutoff = datetime(2026, 7, 27, 11, 30, tzinfo=UTC)
    with Session(engine) as session:
        session.add_all(
            [
                SectorConstituentSnapshot(
                    plate_code="VISIBLE",
                    symbol="SH.600000",
                    as_of_date=target,
                    available_time=datetime(2026, 7, 27, 11, 29, tzinfo=UTC),
                ),
                SectorConstituentSnapshot(
                    plate_code="FUTURE",
                    symbol="SH.600000",
                    as_of_date=target,
                    available_time=datetime(2026, 7, 27, 11, 31, tzinfo=UTC),
                ),
                SectorConstituentSnapshot(
                    plate_code="PRIOR_DAY",
                    symbol="SZ.000001",
                    as_of_date=date(2026, 7, 24),
                    available_time=datetime(2026, 7, 24, 7, 10, tzinfo=UTC),
                ),
            ]
        )
        session.commit()

        memberships = _pit_sector_memberships(session, target, cutoff)

    assert memberships == {"600000": ["VISIBLE"]}


def test_snapshot_job_is_registered_between_constituent_sync_and_flow() -> None:
    sectors_sync.register_sector_jobs()

    names = list(JOBS)
    assert names.index("sync_sector_constituents") < names.index(
        "snapshot_sector_constituents"
    )
    assert names.index("snapshot_sector_constituents") < names.index(
        "sync_sector_flows"
    )
    snapshot = JOBS["snapshot_sector_constituents"]
    assert "day_of_week='mon-fri'" in str(snapshot.trigger)
    assert "hour='15'" in str(snapshot.trigger)
    assert "minute='10'" in str(snapshot.trigger)
