from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alphapilot.db.models import Base, FinancialIndicator, Security
from alphapilot.jobs.financial_transfer import (
    export_financial_snapshot,
    export_financial_staging_seed,
    import_financial_snapshot,
    inspect_financial_snapshot,
)


def _indicator(
    *,
    symbol: str,
    period: str,
    metric: str,
    value: float,
) -> FinancialIndicator:
    return FinancialIndicator(
        symbol=symbol,
        report_period=period,
        metric=metric,
        value=value,
        source="baostock",
        available_time=datetime(2026, 4, 26, tzinfo=UTC),
        payload={
            "available_time_basis": "provider_pub_date_end_of_day",
            "approx": False,
            "main_business_revenue": 100.0,
        },
    )


def _database(
    path: Path,
    rows: list[FinancialIndicator],
    *,
    securities: list[Security] | None = None,
) -> None:
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(securities or [])
        session.add_all(rows)
        session.commit()
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    engine.dispose()


def test_export_and_import_financial_snapshot_are_idempotent(tmp_path: Path) -> None:
    live_remote = tmp_path / "remote-live.db"
    remote_snapshot = tmp_path / "remote-snapshot.db"
    local = tmp_path / "local.db"
    existing = _indicator(
        symbol="600000",
        period="2026Q1",
        metric="roe",
        value=0.10,
    )
    new = _indicator(
        symbol="600519",
        period="2026Q1",
        metric="roe",
        value=0.12,
    )
    _database(live_remote, [existing, new])
    _database(
        local,
        [
            _indicator(
                symbol="600000",
                period="2026Q1",
                metric="roe",
                value=0.10,
            )
        ],
    )

    exported = export_financial_snapshot(live_remote, remote_snapshot)
    inspected = inspect_financial_snapshot(remote_snapshot)
    first = import_financial_snapshot(remote_snapshot, local)
    second = import_financial_snapshot(remote_snapshot, local)

    assert exported["rows"] == 2
    assert inspected["sha256"] == exported["sha256"]
    assert first["import"]["inserted"] == 1
    assert first["import"]["conflicting_rows"] == 0
    assert first["import"]["busy_timeout_ms"] == 15_000
    assert first["import"]["trade_proposals_before"] == 0
    assert first["import"]["trade_proposals_after"] == 0
    assert second["import"]["inserted"] == 0
    assert second["import"]["already_present"] == 2

    engine = create_engine(f"sqlite:///{local}")
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(FinancialIndicator)) == 2
    engine.dispose()


def test_import_refuses_to_overwrite_conflicting_local_pit_row(tmp_path: Path) -> None:
    remote = tmp_path / "remote.db"
    local = tmp_path / "local.db"
    _database(
        remote,
        [
            _indicator(
                symbol="600519",
                period="2026Q1",
                metric="roe",
                value=0.12,
            )
        ],
    )
    _database(
        local,
        [
            _indicator(
                symbol="600519",
                period="2026Q1",
                metric="roe",
                value=0.99,
            )
        ],
    )

    with pytest.raises(ValueError, match="refusing to overwrite"):
        import_financial_snapshot(remote, local)

    engine = create_engine(f"sqlite:///{local}")
    with Session(engine) as session:
        row = session.scalar(select(FinancialIndicator))
        assert row is not None
        assert row.value == pytest.approx(0.99)
    engine.dispose()


def test_import_unions_financial_checkpoints_without_copying_other_profile_fields(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote.db"
    local = tmp_path / "local.db"
    _database(
        remote,
        [
            _indicator(
                symbol="600519",
                period="2026Q1",
                metric="roe",
                value=0.12,
            )
        ],
        securities=[
            Security(
                symbol="600519",
                profile={
                    "financial_no_data_periods": ["2026Q2"],
                    "remote_only": "ignored",
                    "shared": "remote",
                },
            )
        ],
    )
    _database(
        local,
        [],
        securities=[
            Security(
                symbol="600519",
                profile={
                    "financial_no_data_periods": ["2025Q4"],
                    "local_only": "preserved",
                    "shared": "local",
                },
            )
        ],
    )

    first = import_financial_snapshot(remote, local)
    second = import_financial_snapshot(remote, local)

    first_merge = first["import"]["security_profile_merge"]
    assert first_merge["incoming_checkpoint_symbols"] == 1
    assert first_merge["incoming_checkpoint_periods"] == 1
    assert first_merge["profiles_updated"] == 1
    assert first_merge["checkpoint_periods_added"] == 1
    assert first_merge["other_profile_key_conflicts"] == 1
    assert first_merge["other_profile_key_conflict_samples"] == [
        {"symbol": "600519", "key": "shared"}
    ]
    assert (
        first_merge["non_checkpoint_profile_sha256_before"]
        == first_merge["non_checkpoint_profile_sha256_after"]
    )
    assert first_merge["profile_sha256_before"] != first_merge["profile_sha256_after"]

    second_merge = second["import"]["security_profile_merge"]
    assert second_merge["profiles_updated"] == 0
    assert second_merge["checkpoint_periods_added"] == 0
    assert second_merge["profile_sha256_before"] == second_merge["profile_sha256_after"]

    engine = create_engine(f"sqlite:///{local}")
    with Session(engine) as session:
        profile = session.scalar(
            select(Security.profile).where(Security.symbol == "600519")
        )
        assert profile == {
            "financial_no_data_periods": ["2025Q4", "2026Q2"],
            "local_only": "preserved",
            "shared": "local",
        }
    engine.dispose()


def test_import_rejects_invalid_or_unmapped_financial_checkpoints(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote.db"
    local = tmp_path / "local.db"
    _database(
        remote,
        [],
        securities=[
            Security(
                symbol="600519",
                profile={"financial_no_data_periods": ["not-a-quarter"]},
            )
        ],
    )
    _database(local, [])

    with pytest.raises(ValueError, match="invalid financial_no_data_periods"):
        import_financial_snapshot(remote, local)

    unmapped_remote = tmp_path / "unmapped-remote.db"
    _database(
        unmapped_remote,
        [],
        securities=[
            Security(
                symbol="600519",
                profile={"financial_no_data_periods": ["2026Q2"]},
            )
        ],
    )
    with pytest.raises(ValueError, match="absent from target"):
        import_financial_snapshot(unmapped_remote, local)


def test_staging_seed_contains_only_security_checkpoints_and_financial_rows(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    seed = tmp_path / "seed.db"
    _database(
        source,
        [
            _indicator(
                symbol="600519",
                period="2026Q1",
                metric="roe",
                value=0.12,
            )
        ],
        securities=[
            Security(
                symbol="600519",
                profile={
                    "financial_no_data_periods": ["2026Q2"],
                    "daily_bars_backfill_start": "2019-01-01",
                },
            )
        ],
    )

    result = export_financial_staging_seed(source, seed)

    assert result["rows"] == 1
    assert result["symbols"] == 1
    assert result["security_profiles"] == 1
    assert result["financial_checkpoint_symbols"] == 1
    assert result["financial_checkpoint_periods"] == 1
    assert result["job_runs"] == 0
    assert result["trade_proposals"] == 0
    assert result["broker_orders"] == 0
    assert result["sha256"] == inspect_financial_snapshot(seed)["sha256"]

    engine = create_engine(f"sqlite:///{seed}")
    with Session(engine) as session:
        profile = session.scalar(
            select(Security.profile).where(Security.symbol == "600519")
        )
        assert profile == {
            "financial_no_data_periods": ["2026Q2"],
            "daily_bars_backfill_start": "2019-01-01",
        }
    engine.dispose()
