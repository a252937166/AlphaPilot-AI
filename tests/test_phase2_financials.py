from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError
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
    assert stats["symbols_skipped"] == 1
    assert stats["quarters_unavailable"] == 1
    assert stats["symbols_failed"] == 0
    assert stats["metrics_inserted"] == 0


def test_cold_financial_sync_checkpoints_empty_quarters_for_idempotent_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'empty-checkpoint.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add(Security(symbol="600519", board="主板", profile={"preserved": True}))

    calls: list[tuple[str, int, int]] = []

    class EmptyBaoStock:
        def get_quarterly_financials(
            self,
            symbol: str,
            year: int,
            quarter: int,
        ) -> dict[str, pd.DataFrame]:
            calls.append((symbol, year, quarter))
            return {
                "profit": pd.DataFrame(),
                "growth": pd.DataFrame(),
                "cash_flow": pd.DataFrame(),
                "balance": pd.DataFrame(),
            }

    monkeypatch.setattr(financials, "get_session", local_session)
    monkeypatch.setattr(financials, "BaoStockMarketDataProvider", EmptyBaoStock)
    monkeypatch.setattr(financials, "_completed_quarters", lambda _count: [(2026, 2)])

    first = financials.sync_financials(
        quarters=1,
        use_unavailable_checkpoints=True,
    )
    second = financials.sync_financials(
        quarters=1,
        use_unavailable_checkpoints=True,
    )

    assert calls == [("600519", 2026, 2)]
    assert first["quarters_unavailable"] == 1
    assert first["unavailable_checkpoints_added"] == 1
    assert second["financial_quarters_queried"] == 0
    assert second["quarters_skipped_unavailable_checkpoint"] == 1
    assert second["symbols_skipped"] == 1
    with local_session() as session:
        profile = session.scalar(select(Security.profile).where(Security.symbol == "600519"))
    assert profile == {
        "preserved": True,
        "financial_no_data_periods": ["2026Q2"],
    }


def test_financial_write_retries_sqlite_lock_without_refetching(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'financial-lock.db'}")
    Base.metadata.create_all(engine)
    commit_calls = 0
    delays: list[float] = []

    @contextmanager
    def flaky_session() -> Iterator[Session]:
        nonlocal commit_calls
        with Session(engine, expire_on_commit=False) as session:
            real_commit = session.commit

            def commit() -> None:
                nonlocal commit_calls
                commit_calls += 1
                if commit_calls == 1:
                    raise OperationalError(
                        "INSERT INTO financial_indicators (...) VALUES (...)",
                        {},
                        RuntimeError("database is locked"),
                    )
                real_commit()

            session.commit = commit  # type: ignore[method-assign]
            try:
                yield session
            except Exception:
                session.rollback()
                raise

    observation = financials._MetricValue(
        metric="roe",
        value=0.12,
        report_period="2025Q4",
        available_time=datetime(2026, 4, 1, tzinfo=UTC),
        payload={
            "available_time_basis": "provider_pub_date_end_of_day",
            "main_business_revenue": 100.0,
        },
    )
    existing_keys: set[tuple[str, str, str]] = set()
    existing_metrics: dict[tuple[str, str], set[str]] = {}
    monkeypatch.setattr(financials, "get_session", flaky_session)
    monkeypatch.setattr(financials, "sleep", delays.append)

    inserted, updated = financials._save_observations_with_lock_retry(
        "600519",
        [observation],
        existing_keys,
        existing_metrics,
    )

    assert (inserted, updated) == (1, 0)
    assert commit_calls == 2
    assert delays == [0.5]
    assert existing_keys == {("600519", "2025Q4", "roe")}
    with Session(engine) as session:
        row = session.scalar(select(FinancialIndicator))
    assert row is not None
    assert row.value == pytest.approx(0.12)


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


def test_sync_financials_propagates_baostock_blacklist_after_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'blacklist.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add_all(
            [Security(symbol=f"{600000 + index:06d}", board="主板") for index in range(20)]
        )

    calls: list[str] = []

    class BlacklistedBaoStock:
        def get_quarterly_financials(
            self, symbol: str, _year: int, _quarter: int
        ) -> dict[str, pd.DataFrame]:
            calls.append(symbol)
            raise DataProviderError("BaoStock login failed (10001011): 黑名单用户，请与管理员联系")

    monkeypatch.setattr(financials, "get_session", local_session)
    monkeypatch.setattr(financials, "BaoStockMarketDataProvider", BlacklistedBaoStock)
    monkeypatch.setattr(financials, "_completed_quarters", lambda _count: [(2026, 1)])

    with pytest.raises(DataProviderError, match="10001011"):
        financials.sync_financials(quarters=1, batch_size=5)

    assert calls == ["600000"]


def test_sync_financials_propagates_login_transport_failure_after_one_symbol(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'login-transport.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add_all(
            [Security(symbol=f"{600000 + index:06d}", board="主板") for index in range(20)]
        )

    calls: list[str] = []

    class OfflineBaoStock:
        def get_quarterly_financials(
            self, symbol: str, _year: int, _quarter: int
        ) -> dict[str, pd.DataFrame]:
            calls.append(symbol)
            raise DataProviderError(
                "BaoStock login failed after 3 attempts (10002007): 网络接收错误。"
            )

    monkeypatch.setattr(financials, "get_session", local_session)
    monkeypatch.setattr(financials, "BaoStockMarketDataProvider", OfflineBaoStock)
    monkeypatch.setattr(financials, "_completed_quarters", lambda _count: [(2026, 1)])

    with pytest.raises(DataProviderError, match="login failed after 3 attempts"):
        financials.sync_financials(quarters=1, batch_size=5)

    assert calls == ["600000"]


def test_sync_financials_probe_failure_stops_before_shard_and_keeps_stats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'probe-failure.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add(Security(symbol="600519", board="主板"))

    class ProbeFailureBaoStock:
        financial_query_count = 0

        def set_financial_query_limit(self, limit: int | None) -> None:
            assert limit == 40_000

        def probe_financial_query(self) -> int:
            self.financial_query_count = 1
            raise DataProviderError(
                "BaoStock financial query probe failed "
                "(10002007): 网络接收错误"
            )

        def get_quarterly_financials(
            self,
            _symbol: str,
            _year: int,
            _quarter: int,
        ) -> dict[str, pd.DataFrame]:
            raise AssertionError("the shard must not start after a failed probe")

    monkeypatch.setattr(financials, "get_session", local_session)
    monkeypatch.setattr(
        financials,
        "BaoStockMarketDataProvider",
        ProbeFailureBaoStock,
    )
    monkeypatch.setattr(financials, "_completed_quarters", lambda _count: [(2026, 1)])

    with pytest.raises(JobExecutionError, match=r"startup query probe failed.*10002007") as caught:
        financials.sync_financials(
            quarters=1,
            max_provider_requests=40_000,
            probe_before_run=True,
        )

    assert caught.value.stats["provider_probe_requests"] == 1
    assert caught.value.stats["provider_requests_estimated"] == 1
    assert caught.value.stats["symbols_processed"] == 0


def test_sync_financials_successful_probe_counts_against_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'probe-success.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add(Security(symbol="600519", board="主板"))

    class ProbeBaoStock:
        financial_query_count = 0

        def set_financial_query_limit(self, limit: int | None) -> None:
            assert limit == 5

        def probe_financial_query(self) -> int:
            self.financial_query_count = 1
            return 1

        def get_quarterly_financials(
            self,
            _symbol: str,
            _year: int,
            _quarter: int,
        ) -> dict[str, pd.DataFrame]:
            raise AssertionError("four remaining calls cannot reserve a five-call bundle")

    monkeypatch.setattr(financials, "get_session", local_session)
    monkeypatch.setattr(financials, "BaoStockMarketDataProvider", ProbeBaoStock)
    monkeypatch.setattr(financials, "_completed_quarters", lambda _count: [(2026, 1)])

    stats = financials.sync_financials(
        quarters=1,
        max_provider_requests=5,
        probe_before_run=True,
    )

    assert stats["provider_probe_requests"] == 1
    assert stats["provider_probe_rows"] == 1
    assert stats["provider_requests_estimated"] == 1
    assert stats["stopped_for_request_budget"] is True
    assert stats["resume_symbol"] == "600519"


def test_sync_financials_stops_at_daily_request_budget_and_resumes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'request-budget.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add(Security(symbol="600519", board="主板"))

    calls: list[tuple[str, int, int]] = []
    prior_calls: list[tuple[str, int, int]] = []
    samples = {
        (2025, 1): _quarter_frames(
            stat_date="2025-03-31",
            pub_date="2025-04-25",
            revenue=100.0,
            roe=0.10,
            net_profit_yoy=0.02,
            ocf_to_profit=0.80,
            debt_ratio=0.20,
        ),
        (2026, 1): _quarter_frames(
            stat_date="2026-03-31",
            pub_date="2026-04-25",
            revenue=120.0,
            roe=0.12,
            net_profit_yoy=0.03,
            ocf_to_profit=0.90,
            debt_ratio=0.18,
        ),
    }

    class FakeBaoStock:
        def get_quarterly_financials(
            self, symbol: str, year: int, quarter: int
        ) -> dict[str, pd.DataFrame]:
            calls.append((symbol, year, quarter))
            return samples[(year, quarter)]

        def get_quarterly_profit(self, symbol: str, year: int, quarter: int) -> pd.DataFrame:
            prior_calls.append((symbol, year, quarter))
            return pd.DataFrame(
                [
                    {
                        "code": "sh.600519",
                        "statDate": "2024-03-31",
                        "pubDate": "2024-04-25",
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

    first = financials.sync_financials(quarters=2, max_provider_requests=5)

    assert first["is_complete"] is False
    assert first["stopped_for_request_budget"] is True
    assert first["provider_request_budget"] == 5
    assert first["provider_requests_estimated"] == 5
    assert first["resume_symbol"] == "600519"
    assert first["symbols_done"] == 0
    assert first["symbols_with_data"] == 1
    assert first["metrics_inserted"] == 5
    assert calls == [("600519", 2025, 1)]
    assert prior_calls == [("600519", 2024, 1)]

    second = financials.sync_financials(quarters=2, max_provider_requests=5)

    assert second["is_complete"] is True
    assert second["stopped_for_request_budget"] is False
    assert second["provider_requests_estimated"] == 4
    assert second["resume_symbol"] is None
    assert second["symbols_done"] == 1
    assert second["metrics_inserted"] == 5
    assert calls == [("600519", 2025, 1), ("600519", 2026, 1)]
    assert prior_calls == [("600519", 2024, 1)]

    with local_session() as session:
        assert session.scalar(select(func.count()).select_from(FinancialIndicator)) == 10


def test_sync_financials_applies_disjoint_numeric_symbol_ranges(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'symbol-ranges.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add_all(
            [
                Security(symbol="200000", board="主板"),
                Security(symbol="300000", board="创业板"),
                Security(symbol="600519", board="主板"),
            ]
        )

    calls: list[str] = []

    class EmptyBaoStock:
        def get_quarterly_financials(
            self,
            symbol: str,
            _year: int,
            _quarter: int,
        ) -> dict[str, pd.DataFrame]:
            calls.append(symbol)
            return {
                "profit": pd.DataFrame(),
                "growth": pd.DataFrame(),
                "cash_flow": pd.DataFrame(),
                "balance": pd.DataFrame(),
            }

    monkeypatch.setattr(financials, "get_session", local_session)
    monkeypatch.setattr(financials, "BaoStockMarketDataProvider", EmptyBaoStock)
    monkeypatch.setattr(financials, "_completed_quarters", lambda _count: [(2026, 1)])

    high = financials.sync_financials(quarters=1, symbol_min=300_000)
    assert high["symbol_min"] == 300_000
    assert high["symbol_max_exclusive"] is None
    assert high["symbols_total"] == 2
    assert calls == ["300000", "600519"]

    calls.clear()
    low = financials.sync_financials(
        quarters=1,
        symbol_max_exclusive=300_000,
    )
    assert low["symbol_min"] is None
    assert low["symbol_max_exclusive"] == 300_000
    assert low["symbols_total"] == 1
    assert calls == ["200000"]


@pytest.mark.parametrize(
    ("symbol_min", "symbol_max_exclusive", "message"),
    [
        (-1, None, "symbol_min"),
        (1_000_000, None, "symbol_min"),
        (None, 0, "symbol_max_exclusive"),
        (None, 1_000_001, "symbol_max_exclusive"),
        (300_000, 300_000, "less than"),
        (400_000, 300_000, "less than"),
    ],
)
def test_sync_financials_rejects_invalid_symbol_ranges(
    symbol_min: int | None,
    symbol_max_exclusive: int | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        financials.sync_financials(
            quarters=1,
            symbol_min=symbol_min,
            symbol_max_exclusive=symbol_max_exclusive,
        )


def test_sync_financials_handles_universe_larger_than_sqlite_variable_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'large-universe.db'}")

    @event.listens_for(engine, "connect")
    def lower_sqlite_variable_limit(
        dbapi_connection: sqlite3.Connection,
        _connection_record: object,
    ) -> None:
        dbapi_connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add_all(
            [Security(symbol=f"{600000 + index:06d}", board="主板") for index in range(1_005)]
        )

    calls: list[str] = []

    class FakeBaoStock:
        def get_quarterly_financials(
            self,
            symbol: str,
            _year: int,
            _quarter: int,
        ) -> dict[str, pd.DataFrame]:
            calls.append(symbol)
            return _quarter_frames(
                stat_date="2026-03-31",
                pub_date="2026-04-25",
                revenue=120.0,
                roe=0.12,
                net_profit_yoy=0.03,
                ocf_to_profit=0.90,
                debt_ratio=0.18,
            )

        def get_quarterly_profit(
            self,
            _symbol: str,
            _year: int,
            _quarter: int,
        ) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "code": "sh.600000",
                        "statDate": "2025-03-31",
                        "pubDate": "2025-04-25",
                        "MBRevenue": "100.0",
                    }
                ]
            )

    monkeypatch.setattr(financials, "get_session", local_session)
    monkeypatch.setattr(financials, "BaoStockMarketDataProvider", FakeBaoStock)
    monkeypatch.setattr(financials, "_completed_quarters", lambda _count: [(2026, 1)])

    stats = financials.sync_financials(quarters=1, max_provider_requests=5)

    assert stats["symbols_total"] == 1_005
    assert stats["stopped_for_request_budget"] is True
    assert stats["metrics_inserted"] == 5
    assert calls == ["600000"]


def test_financials_cron_runs_saturday_at_1000() -> None:
    financials.register_financials_job()
    try:
        trigger = JOBS["sync_financials"].trigger
        assert "day_of_week='sat'" in str(trigger)
        assert "hour='10'" in str(trigger)
        assert "minute='0'" in str(trigger)
    finally:
        JOBS.pop("sync_financials", None)
