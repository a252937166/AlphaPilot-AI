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

from alphapilot.data.base import DataProviderError, EmptyDailyBarsError
from alphapilot.db.models import Base, DailyBar, Security
from alphapilot.jobs import daily_bars
from alphapilot.jobs.registry import JOBS, JobExecutionError


def _frame(
    trade_date: date, price: float, *, volume: float = 1000, amount: float = 10000
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": trade_date,
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price + 0.5,
                "volume": volume,
                "amount": amount,
            }
        ]
    )


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


def test_sync_daily_bars_routes_bse_and_resumes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'daily.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    requested_end = date(2026, 7, 21)
    baostock_end = date(2026, 7, 20)
    with local_session() as session:
        session.add_all(
            [
                Security(symbol="600000", board="主板"),
                Security(symbol="600001", board="主板"),
                Security(symbol="920000", board="北交所"),
                DailyBar(
                    symbol="600000",
                    trade_date=baostock_end,
                    open=10,
                    high=11,
                    low=9,
                    close=10.5,
                    volume=1000,
                    amount=10000,
                    source="seed",
                ),
            ]
        )

    calls: dict[str, list[str]] = {"baostock": [], "sina": []}

    class FakeBaoStock:
        name = "baostock"

        def get_daily_bars(self, symbol: str, _start: date, finish: date) -> pd.DataFrame:
            calls[self.name].append(symbol)
            return _frame(finish, 10, volume=float("nan"), amount=float("nan"))

    class FakeSina:
        name = "sina"

        def get_daily_bars(self, symbol: str, _start: date, finish: date) -> pd.DataFrame:
            calls[self.name].append(symbol)
            return _frame(finish, 20)

    monkeypatch.setattr(daily_bars, "get_session", local_session)
    monkeypatch.setattr(
        daily_bars, "latest_trade_date", lambda _session: requested_end
    )
    monkeypatch.setattr(
        daily_bars,
        "_probe_provider_trade_window",
        lambda provider, _benchmark, _requested_end: (
            daily_bars._ProviderTradeWindow(
                probed_from=requested_end - timedelta(days=10),
                latest=baostock_end if provider.name == "baostock" else requested_end,
                available_dates=frozenset(
                    {baostock_end if provider.name == "baostock" else requested_end}
                ),
            )
        ),
    )
    monkeypatch.setattr(daily_bars, "BaoStockMarketDataProvider", FakeBaoStock)
    monkeypatch.setattr(daily_bars, "SinaDailyBarProvider", FakeSina)

    first = daily_bars.sync_daily_bars(lookback_days=10, batch_size=1)
    second = daily_bars.sync_daily_bars(lookback_days=10, batch_size=1)

    assert first["done"] == 2
    assert first["skipped"] == 1
    assert first["rows_inserted"] == 2
    assert first["source_counts"] == {"baostock": 1, "sina": 1}
    assert first["provider_trade_dates"] == {
        "baostock": "2026-07-20",
        "sina": "2026-07-21",
    }
    assert second["done"] == 0
    assert second["skipped"] == 3
    assert second["rows_inserted"] == 0
    assert calls == {"baostock": ["600001"], "sina": ["920000"]}
    with local_session() as session:
        assert session.scalar(select(func.count()).select_from(DailyBar)) == 3
        no_trade = session.scalar(
            select(DailyBar).where(DailyBar.symbol == "600001")
        )
        assert no_trade is not None
        assert no_trade.volume == 0
        assert no_trade.amount == 0


def test_sync_daily_bars_stops_after_twenty_consecutive_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'failures.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add_all(
            [Security(symbol=f"{600000 + index:06d}", board="主板") for index in range(20)]
        )

    class FailingProvider:
        name = "baostock"

        def get_daily_bars(self, symbol: str, _start: date, _end: date) -> pd.DataFrame:
            raise DataProviderError(f"offline: {symbol}")

    monkeypatch.setattr(daily_bars, "get_session", local_session)
    monkeypatch.setattr(
        daily_bars, "latest_trade_date", lambda _session: date(2026, 7, 20)
    )
    monkeypatch.setattr(
        daily_bars,
        "_probe_provider_trade_window",
        lambda _provider, _benchmark, requested_end: daily_bars._ProviderTradeWindow(
            probed_from=requested_end - timedelta(days=10),
            latest=requested_end,
            available_dates=frozenset({requested_end}),
        ),
    )
    monkeypatch.setattr(daily_bars, "BaoStockMarketDataProvider", FailingProvider)

    with pytest.raises(JobExecutionError, match="20 consecutive failures") as caught:
        daily_bars.sync_daily_bars(lookback_days=10, batch_size=5)

    assert caught.value.stats["processed"] == 20
    assert caught.value.stats["failed_count"] == 20
    assert caught.value.stats["rows_inserted"] == 0
    assert len(caught.value.stats["failed"]) == 20
    assert caught.value.stats["not_published"] == 0


def test_latest_trade_day_empty_is_not_published_across_weekend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'not-published.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    previous_trade_day = date(2026, 7, 17)
    latest_trade_day = date(2026, 7, 20)
    with local_session() as session:
        for index in range(20):
            symbol = f"{600000 + index:06d}"
            session.add(Security(symbol=symbol, board="主板"))
            session.add(
                DailyBar(
                    symbol=symbol,
                    trade_date=previous_trade_day,
                    open=10,
                    high=11,
                    low=9,
                    close=10.5,
                    volume=1000,
                    amount=10000,
                    source="seed",
                )
            )

    class NotPublishedProvider:
        name = "baostock"

        def get_daily_bars(self, symbol: str, _start: date, _end: date) -> pd.DataFrame:
            raise EmptyDailyBarsError(f"not published: {symbol}")

    monkeypatch.setattr(daily_bars, "get_session", local_session)
    monkeypatch.setattr(
        daily_bars, "latest_trade_date", lambda _session: latest_trade_day
    )
    monkeypatch.setattr(
        daily_bars,
        "_probe_provider_trade_window",
        lambda _provider, _benchmark, _requested_end: daily_bars._ProviderTradeWindow(
            probed_from=date(2026, 7, 10),
            latest=latest_trade_day,
            available_dates=frozenset({previous_trade_day, latest_trade_day}),
        ),
    )
    monkeypatch.setattr(
        daily_bars, "BaoStockMarketDataProvider", NotPublishedProvider
    )

    stats = daily_bars.sync_daily_bars(lookback_days=10, batch_size=5)

    assert stats["processed"] == 20
    assert stats["not_published"] == 20
    assert stats["failed_count"] == 0
    assert stats["failed"] == []
    assert stats["done"] == 0
    assert stats["skipped"] == 0


def test_latest_trade_day_transport_error_remains_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'latest-transport-error.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    previous_trade_day = date(2026, 7, 17)
    latest_trade_day = date(2026, 7, 20)
    with local_session() as session:
        session.add(Security(symbol="600000", board="主板"))
        session.add(
            DailyBar(
                symbol="600000",
                trade_date=previous_trade_day,
                open=10,
                high=11,
                low=9,
                close=10.5,
                volume=1000,
                amount=10000,
                source="seed",
            )
        )

    class OfflineProvider:
        name = "baostock"

        def get_daily_bars(self, symbol: str, _start: date, _end: date) -> pd.DataFrame:
            raise DataProviderError(f"offline: {symbol}")

    monkeypatch.setattr(daily_bars, "get_session", local_session)
    monkeypatch.setattr(
        daily_bars, "latest_trade_date", lambda _session: latest_trade_day
    )
    monkeypatch.setattr(
        daily_bars,
        "_probe_provider_trade_window",
        lambda _provider, _benchmark, _requested_end: daily_bars._ProviderTradeWindow(
            probed_from=date(2026, 7, 10),
            latest=latest_trade_day,
            available_dates=frozenset({previous_trade_day, latest_trade_day}),
        ),
    )
    monkeypatch.setattr(daily_bars, "BaoStockMarketDataProvider", OfflineProvider)

    stats = daily_bars.sync_daily_bars(lookback_days=10, batch_size=1)

    assert stats["processed"] == 1
    assert stats["not_published"] == 0
    assert stats["failed_count"] == 1
    assert stats["failed"][0]["error"].startswith("DataProviderError: offline")


def test_historical_empty_ranges_remain_circuit_breaker_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'historical-empty.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    latest_trade_day = date(2026, 7, 20)
    with local_session() as session:
        session.add_all(
            [Security(symbol=f"{600000 + index:06d}", board="主板") for index in range(20)]
        )

    class EmptyHistoryProvider:
        name = "baostock"

        def get_daily_bars(self, symbol: str, _start: date, _end: date) -> pd.DataFrame:
            raise EmptyDailyBarsError(f"empty history: {symbol}")

    monkeypatch.setattr(daily_bars, "get_session", local_session)
    monkeypatch.setattr(
        daily_bars, "latest_trade_date", lambda _session: latest_trade_day
    )
    monkeypatch.setattr(
        daily_bars,
        "_probe_provider_trade_window",
        lambda _provider, _benchmark, _requested_end: daily_bars._ProviderTradeWindow(
            probed_from=date(2026, 7, 10),
            latest=latest_trade_day,
            available_dates=frozenset(
                {
                    date(2026, 7, 10),
                    date(2026, 7, 13),
                    date(2026, 7, 14),
                    date(2026, 7, 15),
                    date(2026, 7, 16),
                    date(2026, 7, 17),
                    latest_trade_day,
                }
            ),
        ),
    )
    monkeypatch.setattr(daily_bars, "BaoStockMarketDataProvider", EmptyHistoryProvider)

    with pytest.raises(JobExecutionError, match="20 consecutive failures") as caught:
        daily_bars.sync_daily_bars(lookback_days=10, batch_size=5)

    assert caught.value.stats["processed"] == 20
    assert caught.value.stats["failed_count"] == 20
    assert caught.value.stats["not_published"] == 0
    assert all(
        row["error"].startswith("EmptyDailyBarsError:")
        for row in caught.value.stats["failed"]
    )


def test_daily_bars_cron_runs_at_1840() -> None:
    daily_bars.register_daily_bars_job()
    try:
        trigger = JOBS["sync_daily_bars"].trigger
        assert "hour='18'" in str(trigger)
        assert "minute='40'" in str(trigger)
    finally:
        JOBS.pop("sync_daily_bars", None)
