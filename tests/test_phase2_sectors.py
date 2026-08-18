from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
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
    SectorSnapshot,
)
from alphapilot.futu.client import FutuSDKError
from alphapilot.jobs import sectors_sync
from alphapilot.jobs.registry import JOBS, JobOutcome
from alphapilot.services import sectors as sector_service
from alphapilot.services.sectors import compute_sector_strength, get_sector_strength


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
                        "update_time": "2026-07-21 15:00:00",
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
        "_market_now",
        lambda: datetime(2026, 7, 21, 15, 21, tzinfo=sectors_sync.MARKET_TIMEZONE),
    )
    monkeypatch.setattr(sectors_sync, "_cn_trade_day", lambda _client, target: target)
    monkeypatch.setattr(
        sectors_sync,
        "_eastmoney_sector_flows",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Eastmoney must not be called")
        ),
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
                        "capital_flow_item_time": "2026-07-21 15:00:00",
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
        "_market_now",
        lambda: datetime(2026, 7, 21, 15, 21, tzinfo=sectors_sync.MARKET_TIMEZONE),
    )
    monkeypatch.setattr(sectors_sync, "_cn_trade_day", lambda _client, target: target)
    monkeypatch.setattr(
        sectors_sync,
        "_eastmoney_sector_flows",
        lambda **_kwargs: (_ for _ in ()).throw(DataProviderError("offline")),
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


def test_sync_sector_flows_uses_futu_only_for_eastmoney_gaps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-partial-em.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    now = datetime.now(UTC)
    plate_rows = [
        ("SH.LIST0001", "板块一", "SH.600001"),
        ("SH.LIST0002", "板块二", "SH.600002"),
        ("SH.LIST0003", "板块三", "SH.600003"),
    ]
    with local_session() as session:
        session.add_all(
            SectorConstituent(
                plate_code=plate_code,
                plate_name=plate_name,
                symbol=symbol,
                refreshed_at=now,
            )
            for plate_code, plate_name, symbol in plate_rows
        )

    class PartialFlowClient:
        def __init__(self) -> None:
            self.flow_calls: list[str] = []

        def quote_call_raw(
            self, method: str, args: list[Any] | None = None, kwargs: Any = None
        ) -> pd.DataFrame:
            del kwargs
            if method == "get_market_snapshot":
                return pd.DataFrame(
                    [
                        {"code": code, "total_market_val": 1_000.0}
                        for code in (args[0] if args else [])
                    ]
                )
            assert method == "get_capital_flow"
            code = str(args[0]) if args else ""
            self.flow_calls.append(code)
            return pd.DataFrame(
                [
                    {
                        "in_flow": 20.0,
                        "main_in_flow": 10.0,
                        "capital_flow_item_time": "2026-07-21 15:00:00",
                    }
                ]
            )

    client = PartialFlowClient()
    monkeypatch.setattr(sectors_sync, "get_session", local_session)
    monkeypatch.setattr(sectors_sync, "get_futu_client", lambda: client)
    monkeypatch.setattr(
        sectors_sync,
        "_market_now",
        lambda: datetime(2026, 7, 21, 15, 21, tzinfo=sectors_sync.MARKET_TIMEZONE),
    )
    monkeypatch.setattr(sectors_sync, "_cn_trade_day", lambda _client, target: target)
    monkeypatch.setattr(
        sectors_sync,
        "_eastmoney_sector_flows",
        lambda **_kwargs: {"板块一": 100.0},
    )

    stats = sectors_sync.sync_sector_flows(pause_seconds=0)

    assert not isinstance(stats, JobOutcome)
    assert stats["source"] == "mixed"
    assert stats["source_counts"] == {"em": 1, "futu-top5": 2}
    assert stats["coverage"] == 1.0
    assert stats["is_complete"] is True
    assert set(client.flow_calls) == {"SH.600002", "SH.600003"}


def test_sync_sector_flows_degrades_without_partial_top5_plate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-degraded-flow.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    now = datetime.now(UTC)
    with local_session() as session:
        session.add_all(
            [
                SectorConstituent(
                    plate_code="SH.LIST0001",
                    plate_name="板块一",
                    symbol="SH.600001",
                    refreshed_at=now,
                )
            ]
            + [
                SectorConstituent(
                    plate_code="SH.LIST0002",
                    plate_name="板块二",
                    symbol=symbol,
                    refreshed_at=now,
                )
                for symbol in ("SH.600002", "SH.600003")
            ]
        )

    class FailingMemberClient:
        def quote_call_raw(
            self, method: str, args: list[Any] | None = None, kwargs: Any = None
        ) -> pd.DataFrame:
            del kwargs
            if method == "get_market_snapshot":
                return pd.DataFrame(
                    [
                        {"code": code, "total_market_val": 1_000.0}
                        for code in (args[0] if args else [])
                    ]
                )
            code = str(args[0]) if args else ""
            if code == "SH.600003":
                raise RuntimeError("not entitled")
            return pd.DataFrame(
                [
                    {
                        "in_flow": 20.0,
                        "capital_flow_item_time": "2026-07-21 15:00:00",
                    }
                ]
            )

    monkeypatch.setattr(sectors_sync, "get_session", local_session)
    monkeypatch.setattr(sectors_sync, "get_futu_client", FailingMemberClient)
    monkeypatch.setattr(
        sectors_sync,
        "_market_now",
        lambda: datetime(2026, 7, 21, 15, 21, tzinfo=sectors_sync.MARKET_TIMEZONE),
    )
    monkeypatch.setattr(sectors_sync, "_cn_trade_day", lambda _client, target: target)
    monkeypatch.setattr(
        sectors_sync,
        "_eastmoney_sector_flows",
        lambda **_kwargs: {"板块一": 10.0},
    )

    result = sectors_sync.sync_sector_flows(pause_seconds=0)

    assert isinstance(result, JobOutcome)
    assert result.status == "degraded"
    assert result.stats["rows"] == 1
    assert result.stats["missing_plate_codes"] == ["SH.LIST0002"]
    with local_session() as session:
        rows = session.scalars(select(SectorFlowDaily)).all()
        assert [(row.plate_code, row.source) for row in rows] == [("SH.LIST0001", "em")]


def test_snapshot_batches_recursively_isolates_bad_symbol() -> None:
    failures: list[dict[str, str]] = []

    class SnapshotClient:
        def quote_call_raw(
            self, method: str, args: list[Any] | None = None, kwargs: Any = None
        ) -> pd.DataFrame:
            del method, kwargs
            codes = list(args[0] if args else [])
            if "SH.600002" in codes:
                raise RuntimeError("not entitled")
            return pd.DataFrame([{"code": code} for code in codes])

    result = sectors_sync._snapshot_batches(
        SnapshotClient(),  # type: ignore[arg-type]
        ["SH.600001", "SH.600002", "SH.600003"],
        failures=failures,
    )

    assert set(result["code"]) == {"SH.600001", "SH.600003"}
    assert failures == [
        {
            "symbol": "SH.600002",
            "stage": "snapshot",
            "error": "RuntimeError: not entitled",
        }
    ]


def test_snapshot_batches_does_not_split_systemic_sdk_error() -> None:
    class SnapshotClient:
        def __init__(self) -> None:
            self.calls = 0

        def quote_call_raw(
            self, method: str, args: list[Any] | None = None, kwargs: Any = None
        ) -> pd.DataFrame:
            del method, args, kwargs
            self.calls += 1
            raise FutuSDKError("quote.get_market_snapshot failed: OpenD internal error")

    client = SnapshotClient()
    with pytest.raises(FutuSDKError, match="OpenD internal error"):
        sectors_sync._snapshot_batches(  # type: ignore[arg-type]
            client,
            ["SH.600001", "SH.600002", "SH.600003"],
        )

    assert client.calls == 1


def test_snapshot_batches_isolates_explicit_sdk_entitlement_error() -> None:
    failures: list[dict[str, str]] = []

    class SnapshotClient:
        def quote_call_raw(
            self, method: str, args: list[Any] | None = None, kwargs: Any = None
        ) -> pd.DataFrame:
            del method, kwargs
            codes = list(args[0] if args else [])
            if "SH.600002" in codes:
                raise FutuSDKError("permission denied for SH.600002")
            return pd.DataFrame([{"code": code} for code in codes])

    result = sectors_sync._snapshot_batches(
        SnapshotClient(),  # type: ignore[arg-type]
        ["SH.600001", "SH.600002", "SH.600003"],
        failures=failures,
    )

    assert set(result["code"]) == {"SH.600001", "SH.600003"}
    assert [item["symbol"] for item in failures] == ["SH.600002"]


def test_eastmoney_sector_flow_requires_close_time_and_matching_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = date(2026, 7, 21)
    provider_timestamp = int(
        datetime(2026, 7, 21, 15, 20, tzinfo=sectors_sync.MARKET_TIMEZONE).timestamp()
    )

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "data": {
                    "diff": [
                        {
                            "f12": "BK0001",
                            "f14": "板块一",
                            "f62": 123.0,
                            "f124": provider_timestamp,
                        }
                    ]
                }
            }

    calls = 0

    def fake_get(*_args: Any, **_kwargs: Any) -> Response:
        nonlocal calls
        calls += 1
        return Response()

    monkeypatch.setattr(sectors_sync.httpx, "get", fake_get)

    with pytest.raises(DataProviderError, match="before the 15:20 close job"):
        sectors_sync._eastmoney_sector_flows(
            expected_trade_date=target,
            observed_at=datetime(
                2026,
                7,
                21,
                14,
                59,
                tzinfo=sectors_sync.MARKET_TIMEZONE,
            ),
        )
    assert calls == 0

    flows = sectors_sync._eastmoney_sector_flows(
        expected_trade_date=target,
        observed_at=datetime(
            2026,
            7,
            21,
            15,
            21,
            tzinfo=sectors_sync.MARKET_TIMEZONE,
        ),
    )
    assert flows == {"板块一": 123.0}

    with pytest.raises(DataProviderError, match="timestamp does not match"):
        sectors_sync._eastmoney_sector_flows(
            expected_trade_date=target + timedelta(days=1),
            observed_at=datetime(
                2026,
                7,
                22,
                15,
                21,
                tzinfo=sectors_sync.MARKET_TIMEZONE,
            ),
        )


def test_sector_flow_rejects_stale_provider_rows_and_sorts_latest() -> None:
    trade_day = date(2026, 7, 21)
    stale = pd.DataFrame(
        [
            {
                "in_flow": 10.0,
                "capital_flow_item_time": "2026-07-18 15:00:00",
            }
        ]
    )
    with pytest.raises(DataProviderError, match="has no rows for 2026-07-21"):
        sectors_sync._latest_capital_flow(stale, trade_day)

    unsorted = pd.DataFrame(
        [
            {
                "in_flow": 20.0,
                "main_in_flow": 12.0,
                "capital_flow_item_time": "2026-07-21 15:00:00",
            },
            {
                "in_flow": 10.0,
                "main_in_flow": 6.0,
                "capital_flow_item_time": "2026-07-21 14:59:00",
            },
        ]
    )
    assert sectors_sync._latest_capital_flow(unsorted, trade_day) == (20.0, 12.0)

    rows = sectors_sync._snapshot_flow_rows(
        {"SH.LIST0001": {"constituents": ["SH.600000"]}},
        {
            "SH.600000": {
                "update_time": "2026-07-18 15:00:00",
                "net_inflow": 100.0,
            }
        },
        "net_inflow",
        None,
        trade_day,
    )
    assert rows == []


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


def test_sector_strength_enriches_latest_real_flow_without_mutating_cache(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-strength-flow.db'}")
    Base.metadata.create_all(engine)
    cached_payload = [
        {"plate_code": "SH.LIST0001", "plate_name": "板块一", "strength": 8.0},
        {"plate_code": "SH.LIST0002", "plate_name": "板块二", "strength": 4.0},
    ]
    with Session(engine) as session:
        session.add(
            SectorSnapshot(
                as_of=datetime.now(UTC),
                payload=cached_payload,
                source="test",
            )
        )
        session.add_all(
            [
                SectorFlowDaily(
                    plate_code="SH.LIST0001",
                    trade_date=date(2026, 7, 20),
                    net_inflow=10.0,
                    main_inflow=5.0,
                    source="futu-top5",
                ),
                SectorFlowDaily(
                    plate_code="SH.LIST0001",
                    trade_date=date(2026, 7, 21),
                    net_inflow=20.0,
                    main_inflow=None,
                    source="futu-top5",
                ),
                SectorFlowDaily(
                    plate_code="SH.LIST0002",
                    trade_date=date(2026, 7, 20),
                    net_inflow=99.0,
                    main_inflow=88.0,
                    source="futu-top5",
                ),
            ]
        )
        session.commit()

        class NoCallClient:
            def quote_call_raw(self, *_args: Any, **_kwargs: Any) -> pd.DataFrame:
                raise AssertionError("fresh cache must not call Futu")

        payload = get_sector_strength(session, NoCallClient())  # type: ignore[arg-type]
        persisted = session.scalar(select(SectorSnapshot))

    first, second = payload["sectors"]
    assert first["net_inflow"] == 20.0
    assert first["main_inflow"] is None
    assert first["flow_trade_date"] == "2026-07-21"
    assert first["flow_source"] == "futu-top5"
    assert second["net_inflow"] is None
    assert second["main_inflow"] is None
    assert second["flow_trade_date"] is None
    assert second["flow_source"] is None
    assert persisted is not None
    assert persisted.payload == cached_payload


def test_sector_strength_enriches_stale_fallback_without_mutating_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-strength-stale.db'}")
    Base.metadata.create_all(engine)
    cached_payload = [{"plate_code": "SH.LIST0001", "plate_name": "板块一", "strength": 7.0}]
    with Session(engine) as session:
        session.add(
            SectorSnapshot(
                as_of=datetime.now(UTC) - timedelta(minutes=10),
                payload=cached_payload,
                source="test",
            )
        )
        session.add(
            SectorFlowDaily(
                plate_code="SH.LIST0001",
                trade_date=date(2026, 7, 21),
                net_inflow=-12.0,
                main_inflow=-4.0,
                source="futu-top5",
            )
        )
        session.commit()
        monkeypatch.setattr(
            sector_service,
            "compute_sector_strength",
            lambda *_args: (_ for _ in ()).throw(sector_service.SectorServiceError("offline")),
        )

        payload = get_sector_strength(session, object())  # type: ignore[arg-type]
        persisted = session.scalar(select(SectorSnapshot))

    assert payload["stale"] is True
    assert payload["sectors"][0]["net_inflow"] == -12.0
    assert payload["sectors"][0]["flow_trade_date"] == "2026-07-21"
    assert persisted is not None
    assert persisted.payload == cached_payload


def test_sector_strength_enriches_new_compute_but_persists_raw_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-strength-new.db'}")
    Base.metadata.create_all(engine)
    computed_payload = [{"plate_code": "SH.LIST0001", "plate_name": "板块一", "strength": 9.0}]
    with Session(engine) as session:
        session.add(
            SectorFlowDaily(
                plate_code="SH.LIST0001",
                trade_date=date(2026, 7, 21),
                net_inflow=42.0,
                main_inflow=21.0,
                source="futu-top5",
            )
        )
        session.commit()
        monkeypatch.setattr(
            sector_service,
            "compute_sector_strength",
            lambda *_args: computed_payload,
        )

        payload = get_sector_strength(session, object())  # type: ignore[arg-type]
        session.flush()
        persisted = session.scalar(select(SectorSnapshot))

    assert payload["cached"] is False
    assert payload["sectors"][0]["net_inflow"] == 42.0
    assert payload["sectors"][0]["main_inflow"] == 21.0
    assert persisted is not None
    assert persisted.payload == computed_payload


def test_sector_jobs_are_registered_with_expected_schedules() -> None:
    sectors_sync.register_sector_jobs()
    constituents = JOBS["sync_sector_constituents"]
    flows = JOBS["sync_sector_flows"]
    assert "day_of_week='sun'" in str(constituents.trigger)
    assert "hour='9'" in str(constituents.trigger)
    assert "day_of_week='mon-fri'" in str(flows.trigger)
    assert "hour='15'" in str(flows.trigger)
    assert "minute='20'" in str(flows.trigger)
