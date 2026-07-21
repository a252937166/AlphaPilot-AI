from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alphapilot.data.base import DataProviderError
from alphapilot.db.models import Base, FinancialIndicator, Security
from alphapilot.jobs import financials
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


def _quarter_frames(
    *,
    stat_date: str,
    pub_date: str,
    revenue: float,
    roe: float,
    net_profit_yoy: float,
    ocf_to_profit: float,
    debt_ratio: float,
) -> dict[str, pd.DataFrame]:
    common = {"code": "sh.600519", "statDate": stat_date, "pubDate": pub_date}
    return {
        "profit": pd.DataFrame([{**common, "roeAvg": str(roe), "MBRevenue": str(revenue)}]),
        "growth": pd.DataFrame([{**common, "YOYNI": str(net_profit_yoy)}]),
        "cash_flow": pd.DataFrame([{**common, "CFOToNP": str(ocf_to_profit)}]),
        "balance": pd.DataFrame([{**common, "liabilityToAsset": str(debt_ratio)}]),
    }


def _utc(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive DateTime round-trip for assertions."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def test_sync_financials_maps_metrics_derives_revenue_and_resumes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'financials.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add_all(
            [
                Security(symbol="600519", board="主板"),
                Security(symbol="920000", board="北交所"),
            ]
        )

    calls: list[tuple[str, int, int]] = []
    profit_calls: list[tuple[str, int, int]] = []
    samples = {
        (2025, 1): _quarter_frames(
            stat_date="2025-03-31",
            pub_date="2025-04-25",
            revenue=100.0,
            roe=0.10,
            net_profit_yoy=-0.02,
            ocf_to_profit=0.80,
            debt_ratio=0.20,
        ),
        (2026, 1): _quarter_frames(
            stat_date="2026-03-31",
            pub_date="2026-04-25",
            revenue=120.0,
            roe=0.105687,
            net_profit_yoy=0.013653,
            ocf_to_profit=0.955816,
            debt_ratio=0.121227,
        ),
    }

    class FakeBaoStock:
        def get_quarterly_financials(
            self, symbol: str, year: int, quarter: int
        ) -> dict[str, pd.DataFrame]:
            calls.append((symbol, year, quarter))
            return samples[(year, quarter)]

        def get_quarterly_profit(self, symbol: str, year: int, quarter: int) -> pd.DataFrame:
            profit_calls.append((symbol, year, quarter))
            assert (year, quarter) == (2024, 1)
            return pd.DataFrame(
                [
                    {
                        "code": "sh.600519",
                        "statDate": "2024-03-31",
                        "pubDate": "2024-04-27",
                        "MBRevenue": "80.0",
                    }
                ]
            )

    monkeypatch.setattr(financials, "get_session", local_session)
    monkeypatch.setattr(financials, "BaoStockMarketDataProvider", FakeBaoStock)
    monkeypatch.setattr(
        financials,
        "_completed_quarters",
        lambda _count: [(2025, 1), (2026, 1)],
    )

    first = financials.sync_financials(quarters=2, batch_size=1)
    first_calls = list(calls)
    first_profit_calls = list(profit_calls)
    second = financials.sync_financials(quarters=2, batch_size=1)

    assert first["symbols_total"] == 2
    assert first["symbols_done"] == 1
    assert first["symbols_with_data"] == 1
    assert first["symbols_unsupported"] == 1
    assert first["quarters_done"] == 2
    assert first["metrics_inserted"] == 10
    assert first["symbols_failed"] == 0
    assert first_calls == [("600519", 2025, 1), ("600519", 2026, 1)]
    assert first_profit_calls == [("600519", 2024, 1)]

    assert second["symbols_skipped"] == 1
    assert second["symbols_unsupported"] == 1
    assert second["metrics_inserted"] == 0
    assert calls == first_calls
    assert profit_calls == first_profit_calls

    with local_session() as session:
        assert session.scalar(select(func.count()).select_from(FinancialIndicator)) == 10
        assert (
            session.scalar(
                select(func.count())
                .select_from(FinancialIndicator)
                .where(FinancialIndicator.symbol == "920000")
            )
            == 0
        )
        latest = {
            row.metric: row
            for row in session.scalars(
                select(FinancialIndicator).where(
                    FinancialIndicator.symbol == "600519",
                    FinancialIndicator.report_period == "2026Q1",
                )
            )
        }
        oldest_revenue_yoy = session.scalar(
            select(FinancialIndicator).where(
                FinancialIndicator.symbol == "600519",
                FinancialIndicator.report_period == "2025Q1",
                FinancialIndicator.metric == "revenue_yoy",
            )
        )

    assert set(latest) == financials.FINANCIAL_METRICS
    assert latest["roe"].value == pytest.approx(0.105687)
    assert latest["net_profit_yoy"].value == pytest.approx(0.013653)
    assert latest["ocf_to_profit"].value == pytest.approx(0.955816)
    assert latest["debt_ratio"].value == pytest.approx(0.121227)
    assert latest["revenue_yoy"].value == pytest.approx(0.20)
    assert oldest_revenue_yoy is not None
    assert oldest_revenue_yoy.value == pytest.approx(0.25)
    assert _utc(latest["roe"].available_time) == datetime(2026, 4, 25, 16, tzinfo=UTC)
    assert latest["roe"].payload["available_time_basis"] == ("provider_pub_date_end_of_day")
    assert latest["roe"].payload["approx"] is False
    assert latest["revenue_yoy"].payload["prior_report_period"] == "2025Q1"
    assert latest["revenue_yoy"].payload["prior_main_business_revenue"] == 100.0
    assert latest["revenue_yoy"].payload["value_unit"] == "ratio_decimal"


def test_normalize_financials_falls_back_to_approximate_availability() -> None:
    frames = _quarter_frames(
        stat_date="2025-12-31",
        pub_date="",
        revenue=120.0,
        roe=0.30,
        net_profit_yoy=0.10,
        ocf_to_profit=0.90,
        debt_ratio=0.15,
    )

    observations, revenue = financials._normalize_quarter(
        frames,
        requested_year=2025,
        requested_quarter=4,
        prior_revenue=None,
    )

    assert revenue == 120.0
    assert len(observations) == 5
    assert {item.metric for item in observations} == financials.FINANCIAL_METRICS
    assert all(
        item.available_time == datetime(2026, 2, 13, 16, tzinfo=UTC) for item in observations
    )
    assert all(item.payload["approx"] is True for item in observations)
    assert all(
        item.payload["available_time_basis"] == "stat_date_plus_45_days" for item in observations
    )
    revenue_yoy = next(item for item in observations if item.metric == "revenue_yoy")
    assert revenue_yoy.value is None
    assert revenue_yoy.payload["unavailable_reason"] == "missing_prior_year_revenue"


def test_normalize_financials_uses_each_metric_source_pub_date() -> None:
    frames = _quarter_frames(
        stat_date="2026-03-31",
        pub_date="2026-04-25",
        revenue=120.0,
        roe=0.105687,
        net_profit_yoy=0.013653,
        ocf_to_profit=0.955816,
        debt_ratio=0.121227,
    )
    frames["profit"].loc[0, "pubDate"] = ""

    observations, _revenue = financials._normalize_quarter(
        frames,
        requested_year=2026,
        requested_quarter=1,
        prior_revenue=100.0,
    )
    by_metric = {item.metric: item for item in observations}

    for metric in {"roe", "revenue_yoy"}:
        assert by_metric[metric].payload["approx"] is True
        assert by_metric[metric].payload["pub_dates"] == []
        assert by_metric[metric].available_time == datetime(2026, 5, 14, 16, tzinfo=UTC)
    for metric in {"net_profit_yoy", "ocf_to_profit", "debt_ratio"}:
        assert by_metric[metric].payload["approx"] is False
        assert by_metric[metric].payload["pub_dates"] == ["2026-04-25"]
        assert by_metric[metric].available_time == datetime(2026, 4, 25, 16, tzinfo=UTC)


def test_sync_financials_treats_empty_quarter_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add(Security(symbol="600519", board="主板"))

    class EmptyBaoStock:
        def get_quarterly_financials(
            self, _symbol: str, _year: int, _quarter: int
        ) -> dict[str, pd.DataFrame]:
            return {
                "profit": pd.DataFrame(),
                "growth": pd.DataFrame(),
                "cash_flow": pd.DataFrame(),
                "balance": pd.DataFrame(),
            }

    monkeypatch.setattr(financials, "get_session", local_session)
    monkeypatch.setattr(financials, "BaoStockMarketDataProvider", EmptyBaoStock)
    monkeypatch.setattr(financials, "_completed_quarters", lambda _count: [(2026, 1)])

    stats = financials.sync_financials(quarters=1)

    assert stats["symbols_done"] == 1
    assert stats["symbols_with_data"] == 0
    assert stats["quarters_unavailable"] == 1
    assert stats["symbols_failed"] == 0
    assert stats["metrics_inserted"] == 0


def test_sync_financials_stops_after_twenty_consecutive_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'failures.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add_all(
            [Security(symbol=f"{600000 + index:06d}", board="主板") for index in range(20)]
        )

    calls: list[str] = []

    class FailingBaoStock:
        def get_quarterly_financials(
            self, symbol: str, _year: int, _quarter: int
        ) -> dict[str, pd.DataFrame]:
            calls.append(symbol)
            raise DataProviderError(f"offline: {symbol}")

    monkeypatch.setattr(financials, "get_session", local_session)
    monkeypatch.setattr(financials, "BaoStockMarketDataProvider", FailingBaoStock)
    monkeypatch.setattr(financials, "_completed_quarters", lambda _count: [(2026, 1)])

    with pytest.raises(JobExecutionError, match="20 consecutive symbol failures") as caught:
        financials.sync_financials(quarters=1, batch_size=5)

    assert caught.value.stats["symbols_processed"] == 20
    assert caught.value.stats["symbols_failed"] == 20
    assert caught.value.stats["symbols_done"] == 0
    assert caught.value.stats["metrics_inserted"] == 0
    assert caught.value.stats["last_symbol"] == "600019"
    assert len(caught.value.stats["failures"]) == 20
    assert len(calls) == 20


def test_financials_cron_runs_saturday_at_1000() -> None:
    financials.register_financials_job()
    try:
        trigger = JOBS["sync_financials"].trigger
        assert "day_of_week='sat'" in str(trigger)
        assert "hour='10'" in str(trigger)
        assert "minute='0'" in str(trigger)
    finally:
        JOBS.pop("sync_financials", None)
