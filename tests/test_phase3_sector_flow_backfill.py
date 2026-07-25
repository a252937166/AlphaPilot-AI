from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
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
from alphapilot.jobs.registry import JOBS, JobExecutionError


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
    assert first["lookahead_bias"] == (
        "current top5 fixed for history; accepted M3 limitation"
    )
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


def test_sector_flow_backfill_never_persists_partial_member_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-flow-failure.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    client = _FlowClient(fail_symbol="SH.600002")
    monkeypatch.setattr(sector_flow_backfill, "get_session", local_session)
    monkeypatch.setattr(sector_flow_backfill, "get_futu_client", lambda: client)
    monkeypatch.setattr(sector_flow_backfill, "_cached_plates", _plates)
    monkeypatch.setattr(sector_flow_backfill, "sleep", lambda _seconds: None)

    with pytest.raises(JobExecutionError) as exc_info:
        sector_flow_backfill.backfill_sector_flows(
            start_date=date(2025, 1, 2),
            end_date=date(2025, 1, 6),
            pause_seconds=0,
        )
    assert exc_info.value.stats["failure_count"] == 1
    assert exc_info.value.stats["is_complete"] is False
    with local_session() as session:
        assert session.scalar(select(func.count()).select_from(SectorFlowDaily)) == 0


def test_register_sector_flow_backfill_is_manual_only() -> None:
    sector_flow_backfill.register_sector_flow_backfill_job()
    assert JOBS["backfill_sector_flows"].trigger is None


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
