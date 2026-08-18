from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alphapilot.data.provenance import AUDITED_SECTOR_FLOW_SOURCES
from alphapilot.db.models import Base, SectorFlowDaily
from alphapilot.engines.factors import _sector_flow_values
from alphapilot.jobs import sector_flow_backfill
from alphapilot.jobs.registry import JOBS, JobOutcome


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


def _plates() -> dict[str, dict[str, Any]]:
    return {
        "SH.LIST0001": {
            "plate_name": "板块一",
            "constituents": ["SH.600001", "SH.600002"],
        },
        "SH.LIST0002": {
            "plate_name": "板块二",
            "constituents": ["SH.600002", "SZ.000001"],
        },
    }


class _FlowClient:
    def __init__(self, *, fail_symbol: str | None = None) -> None:
        self.fail_symbol = fail_symbol
        self.flow_calls: list[str] = []

    def quote_call_raw(
        self,
        method: str,
        args: list[Any] | None = None,
        kwargs: Any = None,
    ) -> Any:
        if method == "request_trading_days":
            return [{"time": f"2025-01-{day:02d}"} for day in range(2, 7)]
        if method == "get_market_snapshot":
            codes = args[0] if args else []
            return pd.DataFrame(
                [
                    {
                        "code": code,
                        "total_market_val": float(index + 1),
                    }
                    for index, code in enumerate(codes)
                ]
            )
        assert method == "get_capital_flow"
        assert kwargs["period_type"] == {"__futu_constant__": "PeriodType.DAY"}
        code = str(kwargs["stock_code"])
        self.flow_calls.append(code)
        if code == self.fail_symbol:
            raise RuntimeError("OpenD unavailable")
        multiplier = float(int(code[-1]) + 1)
        return pd.DataFrame(
            [
                {
                    "capital_flow_item_time": f"2025-01-{day:02d}",
                    "in_flow": multiplier * day,
                    "main_in_flow": "N/A",
                    "super_in_flow": multiplier,
                    "big_in_flow": multiplier * 2,
                }
                for day in range(2, 7)
            ]
        )


def test_sector_flow_backfill_persists_daily_rows_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-flow.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add(
            SectorFlowDaily(
                plate_code="SH.LIST0001",
                trade_date=date(2025, 1, 2),
                net_inflow=1,
                main_inflow=1,
                source="futu-top5",
            )
        )
    client = _FlowClient()
    monkeypatch.setattr(sector_flow_backfill, "get_session", local_session)
    monkeypatch.setattr(sector_flow_backfill, "get_futu_client", lambda: client)
    monkeypatch.setattr(sector_flow_backfill, "_cached_plates", _plates)
    monkeypatch.setattr(sector_flow_backfill, "sleep", lambda _seconds: None)

    first = sector_flow_backfill.backfill_sector_flows(
        start_date=date(2025, 1, 2),
        end_date=date(2025, 1, 6),
        pause_seconds=0,
    )
    second = sector_flow_backfill.backfill_sector_flows(
        start_date=date(2025, 1, 2),
        end_date=date(2025, 1, 6),
        pause_seconds=0,
    )

    assert first["source"] == "futu-daily"
    assert first["selected_symbols"] == 3
    assert first["rows_aggregated"] == 10
    assert first["inserted"] == 9
    assert first["updated"] == 1
    assert first["coverage"]["rows"] == 10
    assert first["coverage"]["plates"] == 2
    assert first["coverage"]["trade_days"] == 5
    assert first["coverage"]["row_coverage"] == 1.0
    assert first["depth_limit"] == "Futu PeriodType.DAY <= 365 calendar days"
    assert first["basket_basis"] == "current total_market_val fixed top5"
    assert first["lookahead_bias"] == ("current top5 fixed for history; accepted M3 limitation")
    assert second["inserted"] == 0
    assert second["updated"] == 0
    assert second["skipped_existing"] == 10
    assert second["idempotent_skip"] is True
    assert second["member_calls"] == 0
    assert len(client.flow_calls) == 3
    assert "futu-daily" in AUDITED_SECTOR_FLOW_SOURCES

    with local_session() as session:
        assert session.scalar(select(func.count()).select_from(SectorFlowDaily)) == 10
        row = session.scalar(
            select(SectorFlowDaily).where(
                SectorFlowDaily.plate_code == "SH.LIST0001",
                SectorFlowDaily.trade_date == date(2025, 1, 2),
            )
        )
        assert row is not None
        assert row.source == "futu-daily"
        assert row.net_inflow == pytest.approx(10.0)
        assert row.main_inflow == pytest.approx(15.0)

        values, observed_days, sources = _sector_flow_values(
            session,
            [date(2025, 1, day) for day in range(2, 7)],
        )
        assert observed_days == 5
        assert sources == ["futu-daily"]
        assert set(values) == {"SH.LIST0001", "SH.LIST0002"}


def test_sector_flow_backfill_isolates_member_failures_without_partial_plates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-flow-failure.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    client = _FlowClient(fail_symbol="SH.600002")
    monkeypatch.setattr(sector_flow_backfill, "get_session", local_session)
    monkeypatch.setattr(sector_flow_backfill, "get_futu_client", lambda: client)
    plates = {
        **_plates(),
        "SH.LIST0003": {
            "plate_name": "板块三",
            "constituents": ["SH.600003"],
        },
    }
    monkeypatch.setattr(sector_flow_backfill, "_cached_plates", lambda: plates)
    monkeypatch.setattr(sector_flow_backfill, "sleep", lambda _seconds: None)

    result = sector_flow_backfill.backfill_sector_flows(
        start_date=date(2025, 1, 2),
        end_date=date(2025, 1, 6),
        pause_seconds=0,
    )

    assert isinstance(result, JobOutcome)
    assert result.status == "degraded"
    assert result.stats["failure_count"] == 1
    assert result.stats["is_complete"] is False
    assert result.stats["rows_aggregated"] == 5
    assert result.stats["coverage"]["row_coverage"] == pytest.approx(1 / 3)
    assert result.stats["missing_plate_days"] == 10
    with local_session() as session:
        rows = session.scalars(select(SectorFlowDaily)).all()
        assert len(rows) == 5
        assert {row.plate_code for row in rows} == {"SH.LIST0003"}


def test_register_sector_flow_backfill_is_manual_only() -> None:
    sector_flow_backfill.register_sector_flow_backfill_job()
    assert JOBS["backfill_sector_flows"].trigger is None
    repair = JOBS["repair_recent_sector_flow_gaps"]
    assert "day_of_week='mon-fri'" in str(repair.trigger)
    assert "hour='17'" in str(repair.trigger)
    assert "minute='30'" in str(repair.trigger)


def test_recent_sector_flow_repair_audits_five_days_and_fills_only_gap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-flow-repair.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    trading_dates = [date(2026, 8, 3) + timedelta(days=offset) for offset in range(5)]
    plates = _plates()
    missing_key = ("SH.LIST0002", trading_dates[2])
    with local_session() as session:
        session.add_all(
            SectorFlowDaily(
                plate_code=plate_code,
                trade_date=trade_date,
                net_inflow=(None if (plate_code, trade_date) == missing_key else 1.0),
                main_inflow=(None if (plate_code, trade_date) == missing_key else 0.5),
                source="em",
            )
            for trade_date in trading_dates
            for plate_code in plates
        )

    calls: list[dict[str, Any]] = []

    def fake_backfill(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        inserted, updated, skipped = sector_flow_backfill._persist_day(
            missing_key[1],
            [
                {
                    "plate_code": missing_key[0],
                    "net_inflow": 2.0,
                    "main_inflow": 1.0,
                }
            ],
            missing_only=True,
        )
        return {
            "is_complete": True,
            "inserted": inserted,
            "updated": updated,
            "skipped_existing": skipped,
        }

    monkeypatch.setattr(sector_flow_backfill, "get_session", local_session)
    monkeypatch.setattr(sector_flow_backfill, "get_futu_client", object)
    monkeypatch.setattr(sector_flow_backfill, "_market_today", lambda: trading_dates[-1])
    monkeypatch.setattr(sector_flow_backfill, "_cached_plates", lambda: plates)
    monkeypatch.setattr(
        sector_flow_backfill,
        "_cn_trading_dates",
        lambda *_args, **_kwargs: trading_dates,
    )
    monkeypatch.setattr(sector_flow_backfill, "backfill_sector_flows", fake_backfill)

    result = sector_flow_backfill.repair_recent_sector_flow_gaps(pause_seconds=0)

    assert not isinstance(result, JobOutcome)
    assert result["missing_before"] == 1
    assert result["missing_after"] == 0
    assert result["repaired"] == 1
    assert result["is_complete"] is True
    assert calls == [
        {
            "start_date": trading_dates[0],
            "end_date": trading_dates[-1],
            "pause_seconds": 0,
            "missing_only": True,
        }
    ]
    with local_session() as session:
        repaired = session.scalar(
            select(SectorFlowDaily).where(
                SectorFlowDaily.plate_code == missing_key[0],
                SectorFlowDaily.trade_date == missing_key[1],
            )
        )
        assert repaired is not None
        assert repaired.net_inflow == 2.0
        assert repaired.main_inflow == 1.0
        assert repaired.source == "futu-daily"


def test_missing_only_coverage_counts_only_current_audited_non_null_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-flow-coverage.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    target = date(2026, 8, 7)
    with local_session() as session:
        session.add_all(
            [
                SectorFlowDaily(
                    plate_code="SH.LIST0001",
                    trade_date=target,
                    net_inflow=1.0,
                    source="em",
                ),
                SectorFlowDaily(
                    plate_code="SH.LIST0002",
                    trade_date=target,
                    net_inflow=None,
                    source="futu-daily",
                ),
                SectorFlowDaily(
                    plate_code="SH.LIST9999",
                    trade_date=target,
                    net_inflow=9.0,
                    source="futu-daily",
                ),
            ]
        )
    monkeypatch.setattr(sector_flow_backfill, "get_session", local_session)

    coverage = sector_flow_backfill._coverage(
        start_date=target,
        end_date=target,
        plates_total=2,
        expected_trade_dates=[target],
        plate_codes={"SH.LIST0001", "SH.LIST0002"},
        accepted_sources=AUDITED_SECTOR_FLOW_SOURCES,
    )

    assert coverage["rows"] == 1
    assert coverage["plates"] == 1
    assert coverage["trade_days"] == 1
    assert coverage["expected_rows"] == 2
    assert coverage["row_coverage"] == 0.5


def test_sector_flow_backfill_rejects_more_than_one_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unexpected_client() -> None:
        raise AssertionError("window validation must run before opening Futu")

    monkeypatch.setattr(sector_flow_backfill, "get_futu_client", _unexpected_client)

    with pytest.raises(
        ValueError,
        match="Futu DAY capital-flow window must not exceed 365 days",
    ):
        sector_flow_backfill.backfill_sector_flows(
            start_date=date(2024, 1, 1),
            end_date=date(2025, 1, 2),
            pause_seconds=0,
        )
