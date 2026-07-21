from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alphapilot.data.base import DataProviderError
from alphapilot.db.models import (
    Base,
    DailyBar,
    SectorConstituent,
    SectorFlowDaily,
)
from alphapilot.jobs import sectors_sync
from alphapilot.jobs.registry import JOBS
from alphapilot.services.sectors import compute_sector_strength


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


def _seed_trade_day(session: Session) -> None:
    session.add(
        DailyBar(
            symbol="SH.000001",
            trade_date=date(2026, 7, 21),
            open=3800,
            high=3900,
            low=3700,
            close=3850,
            volume=1,
            amount=1,
            source="test",
        )
    )


def test_sync_sector_constituents_replaces_cache_idempotently(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-members.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)

    class FakeClient:
        def quote_call_raw(
            self, method: str, args: list[Any] | None = None, kwargs: Any = None
        ) -> pd.DataFrame:
            del kwargs
            if method == "get_plate_list":
                return pd.DataFrame(
                    [
                        {"code": "SH.LIST0001", "plate_name": "白色家电"},
                        {"code": "SH.LIST0002", "plate_name": "黑色家电"},
                    ]
                )
            assert method == "get_plate_stock"
            plate = str(args[0]) if args else ""
            return pd.DataFrame(
                [
                    {"code": "SH.600000", "stock_name": f"{plate}-沪股"},
                    {"code": "SZ.000001", "stock_name": f"{plate}-深股"},
                    {"code": "HK.00700", "stock_name": "非A股"},
                ]
            )

    monkeypatch.setattr(sectors_sync, "get_session", local_session)
    monkeypatch.setattr(sectors_sync, "get_futu_client", FakeClient)

    first = sectors_sync.sync_sector_constituents(pause_seconds=0)
    second = sectors_sync.sync_sector_constituents(pause_seconds=0)

    assert first["plates"] == 2
    assert first["members"] == 4
    assert first["unique_symbols"] == 2
    assert second["members"] == 4
    with local_session() as session:
        assert session.scalar(select(func.count()).select_from(SectorConstituent)) == 4


def test_sync_sector_flows_uses_snapshot_field_when_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-snapshot-flow.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    now = datetime.now(UTC)
    with local_session() as session:
        _seed_trade_day(session)
        session.add_all(
            [
                SectorConstituent(
                    plate_code="SH.LIST0001",
                    plate_name="板块一",
                    symbol="SH.600000",
                    refreshed_at=now,
                ),
                SectorConstituent(
                    plate_code="SH.LIST0002",
                    plate_name="板块二",
                    symbol="SZ.000001",
                    refreshed_at=now,
                ),
            ]
        )

    class SnapshotFlowClient:
        def quote_call_raw(
            self, method: str, args: list[Any] | None = None, kwargs: Any = None
        ) -> pd.DataFrame:
            del kwargs
            assert method == "get_market_snapshot"
            return pd.DataFrame(
                [
                    {
                        "code": code,
                        "net_inflow": 100.0,
                        "main_inflow": 60.0,
                        "total_market_val": 1_000.0,
                    }
                    for code in (args[0] if args else [])
                ]
            )

    monkeypatch.setattr(sectors_sync, "get_session", local_session)
    monkeypatch.setattr(sectors_sync, "get_futu_client", SnapshotFlowClient)
    monkeypatch.setattr(
        sectors_sync,
        "_eastmoney_sector_flows",
        lambda: (_ for _ in ()).throw(AssertionError("Eastmoney must not be called")),
    )

    first = sectors_sync.sync_sector_flows(pause_seconds=0)
    second = sectors_sync.sync_sector_flows(pause_seconds=0)

    assert first["source"] == "futu-snapshot"
    assert first["rows"] == 2
    assert first["inserted"] == 2
    assert second["inserted"] == 0
    assert second["updated"] == 2
    with local_session() as session:
        flows = session.scalars(select(SectorFlowDaily)).all()
        assert len(flows) == 2
        assert {row.net_inflow for row in flows} == {100.0}
        assert {row.main_inflow for row in flows} == {60.0}


def test_sync_sector_flows_falls_back_to_deduplicated_futu_top5(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-top5-flow.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    codes = [f"SH.{600000 + index:06d}" for index in range(6)]
    now = datetime.now(UTC)
    with local_session() as session:
        _seed_trade_day(session)
        session.add_all(
            SectorConstituent(
                plate_code="SH.LIST0001",
                plate_name="板块一",
                symbol=code,
                refreshed_at=now,
            )
            for code in codes
        )

    class CapitalFlowClient:
        def __init__(self) -> None:
            self.flow_calls: list[str] = []

        def quote_call_raw(
            self, method: str, args: list[Any] | None = None, kwargs: Any = None
        ) -> pd.DataFrame:
            del kwargs
            if method == "get_market_snapshot":
                return pd.DataFrame(
                    [
                        {
                            "code": code,
                            "total_market_val": float(index + 1),
                        }
                        for index, code in enumerate(args[0] if args else [])
                    ]
                )
            assert method == "get_capital_flow"
            code = str(args[0]) if args else ""
            self.flow_calls.append(code)
            return pd.DataFrame(
                [
                    {
                        "in_flow": 10.0,
                        "main_in_flow": "N/A",
                        "super_in_flow": 2.0,
                        "big_in_flow": 3.0,
                    }
                ]
            )

    client = CapitalFlowClient()
    monkeypatch.setattr(sectors_sync, "get_session", local_session)
    monkeypatch.setattr(sectors_sync, "get_futu_client", lambda: client)
    monkeypatch.setattr(
        sectors_sync,
        "_eastmoney_sector_flows",
        lambda: (_ for _ in ()).throw(DataProviderError("offline")),
    )

    stats = sectors_sync.sync_sector_flows(pause_seconds=0)

    assert stats["source"] == "futu-top5"
    assert stats["capital_flow_symbols"] == 5
    assert set(client.flow_calls) == set(codes[1:])
    with local_session() as session:
        flow = session.scalar(select(SectorFlowDaily))
        assert flow is not None
        assert flow.net_inflow == 50.0
        assert flow.main_inflow == 25.0
        assert flow.source == "futu-top5"


def test_sector_strength_prefers_fresh_db_and_sorts_top30_by_turnover(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-strength.db'}")
    Base.metadata.create_all(engine)
    codes = [f"SH.{600100 + index:06d}" for index in range(401)]
    with Session(engine) as session:
        session.add_all(
            SectorConstituent(
                plate_code="SH.LIST0001",
                plate_name="测试板块",
                symbol=code,
                refreshed_at=datetime.now(UTC),
            )
            for code in codes
        )
        session.commit()

    class SnapshotClient:
        def __init__(self) -> None:
            self.snapshot_calls = 0

        def quote_call_raw(
            self, method: str, args: list[Any] | None = None, kwargs: Any = None
        ) -> pd.DataFrame:
            del kwargs
            assert method == "get_market_snapshot"
            self.snapshot_calls += 1
            records = []
            for code in args[0] if args else []:
                rank = int(str(code).rsplit(".", 1)[-1])
                records.append(
                    {
                        "code": code,
                        "name": code,
                        "last_price": 10.0 + rank / 1_000_000,
                        "prev_close_price": 10.0,
                        "turnover": float(rank),
                    }
                )
            return pd.DataFrame(records)

    client = SnapshotClient()
    with Session(engine) as session:
        result = compute_sector_strength(client, session)  # type: ignore[arg-type]

    assert client.snapshot_calls == 2
    assert len(result) == 1
    assert result[0]["sampled"] == 30
    assert result[0]["leader_code"] == codes[-1]


def test_sector_jobs_are_registered_with_expected_schedules() -> None:
    sectors_sync.register_sector_jobs()
    constituents = JOBS["sync_sector_constituents"]
    flows = JOBS["sync_sector_flows"]
    assert "day_of_week='sun'" in str(constituents.trigger)
    assert "hour='9'" in str(constituents.trigger)
    assert "day_of_week='mon-fri'" in str(flows.trigger)
    assert "hour='15'" in str(flows.trigger)
    assert "minute='20'" in str(flows.trigger)
