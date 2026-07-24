from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.data.base import DataProviderError, EmptyDailyBarsError
from alphapilot.db.engine import _build_engine
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


def _locked_error() -> OperationalError:
    return OperationalError(
        "INSERT INTO daily_bars (...) VALUES (...)",
        {},
        RuntimeError("database is locked"),
    )


def test_sqlite_engine_waits_for_concurrent_writers(tmp_path: Path) -> None:
    engine = _build_engine(
        Settings(database_url=f"sqlite:///{tmp_path / 'busy-timeout.db'}")
    )
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 15000
    finally:
        engine.dispose()


def test_sync_daily_bars_retries_sqlite_lock_without_refetching_or_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'lock-retry.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    opened_sessions: list[Session] = []

    @contextmanager
    def tracked_session() -> Iterator[Session]:
        with local_session() as session:
            opened_sessions.append(session)
            yield session

    target = date(2026, 7, 22)
    with local_session() as session:
        session.add(Security(symbol="600000", board="主板"))

    provider_calls = 0
    save_calls = 0
    delays: list[float] = []

    class Provider:
        name = "baostock"

        def get_daily_bars(self, _symbol: str, _start: date, finish: date) -> pd.DataFrame:
            nonlocal provider_calls
            provider_calls += 1
            return _frame(finish, 10)

    real_save_bars = daily_bars.save_bars

    def locked_once(
        session: Session, symbol: str, frame: pd.DataFrame, source: str
    ) -> int:
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            raise _locked_error()
        return real_save_bars(session, symbol, frame, source)

    monkeypatch.setattr(daily_bars, "get_session", tracked_session)
    monkeypatch.setattr(daily_bars, "latest_trade_date", lambda _session: target)
    monkeypatch.setattr(
        daily_bars,
        "_probe_provider_trade_window",
        lambda _provider, _benchmark, requested_end: daily_bars._ProviderTradeWindow(
            probed_from=requested_end - timedelta(days=10),
            latest=requested_end,
            available_dates=frozenset({requested_end}),
        ),
    )
    monkeypatch.setattr(daily_bars, "BaoStockMarketDataProvider", Provider)
    monkeypatch.setattr(daily_bars, "save_bars", locked_once)
    monkeypatch.setattr(daily_bars, "sleep", delays.append)

    stats = daily_bars.sync_daily_bars(lookback_days=10, batch_size=1)

    assert stats["done"] == 1
    assert stats["failed_count"] == 0
    assert stats["rows_inserted"] == 1
    assert provider_calls == 1
    assert save_calls == 2
    assert delays == [0.5]
    assert len(opened_sessions) == 3
    assert len({id(session) for session in opened_sessions}) == 3


def test_sqlite_lock_failures_do_not_trip_data_provider_circuit_breaker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'lock-no-breaker.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    target = date(2026, 7, 22)
    with local_session() as session:
        session.add_all(
            [Security(symbol=f"{600000 + index:06d}", board="主板") for index in range(39)]
        )

    class Provider:
        name = "baostock"

        def get_daily_bars(self, symbol: str, _start: date, finish: date) -> pd.DataFrame:
            if symbol != "600019":
                raise DataProviderError(f"offline: {symbol}")
            return _frame(finish, 10)

    save_calls = 0
    delays: list[float] = []

    def permanently_locked(*_args: object) -> int:
        nonlocal save_calls
        save_calls += 1
        raise _locked_error()

    monkeypatch.setattr(daily_bars, "get_session", local_session)
    monkeypatch.setattr(daily_bars, "latest_trade_date", lambda _session: target)
    monkeypatch.setattr(
        daily_bars,
        "_probe_provider_trade_window",
        lambda _provider, _benchmark, requested_end: daily_bars._ProviderTradeWindow(
            probed_from=requested_end - timedelta(days=10),
            latest=requested_end,
            available_dates=frozenset({requested_end}),
        ),
    )
    monkeypatch.setattr(daily_bars, "BaoStockMarketDataProvider", Provider)
    monkeypatch.setattr(daily_bars, "save_bars", permanently_locked)
    monkeypatch.setattr(daily_bars, "sleep", delays.append)

    stats = daily_bars.sync_daily_bars(lookback_days=10, batch_size=5)

    assert stats["processed"] == 39
    assert stats["failed_count"] == 39
    assert stats["done"] == 0
    assert "database is locked" in stats["failed"][19]["error"]
    assert save_calls == 4
    assert delays == [0.5, 1.5, 3.0]


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
                    source="baostock",
                ),
                DailyBar(
                    symbol="600001",
                    trade_date=baostock_end,
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=1,
                    amount=1,
                    source="mock",
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
        assert no_trade.source == "baostock"
        assert no_trade.close == pytest.approx(10.5)
        assert no_trade.volume == 0
        assert no_trade.amount == 0


def test_sync_daily_bars_backfills_before_existing_history_and_resumes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'daily-backfill.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    requested_start = date(2026, 7, 18)
    first_existing = date(2026, 7, 20)
    provider_end = date(2026, 7, 21)
    with local_session() as session:
        session.add(Security(symbol="600000", board="主板"))
        session.add_all(
            [
                DailyBar(
                    symbol="600000",
                    trade_date=trade_date,
                    open=10,
                    high=11,
                    low=9,
                    close=10.5,
                    volume=1000,
                    amount=10000,
                    source="baostock",
                )
                for trade_date in (first_existing, provider_end)
            ]
        )

    calls: list[tuple[date, date]] = []

    class Provider:
        name = "baostock"

        def get_daily_bars(
            self,
            _symbol: str,
            start: date,
            finish: date,
        ) -> pd.DataFrame:
            calls.append((start, finish))
            return pd.concat(
                [_frame(start, 8), _frame(finish, 9)],
                ignore_index=True,
            )

    monkeypatch.setattr(daily_bars, "get_session", local_session)
    monkeypatch.setattr(
        daily_bars,
        "latest_trade_date",
        lambda _session: provider_end,
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
    monkeypatch.setattr(daily_bars, "BaoStockMarketDataProvider", Provider)

    first = daily_bars.sync_daily_bars(
        start_date=requested_start,
        batch_size=1,
    )
    second = daily_bars.sync_daily_bars(
        start_date=requested_start,
        batch_size=1,
    )

    assert calls == [(requested_start, first_existing - timedelta(days=1))]
    assert first["requested_start_date"] == requested_start.isoformat()
    assert first["historical_backfill_symbols"] == 1
    assert first["rows_inserted"] == 2
    assert first["failed_count"] == 0
    assert second["rows_inserted"] == 0
    assert second["skipped"] == 1
    with local_session() as session:
        dates = session.scalars(
            select(DailyBar.trade_date)
            .where(DailyBar.symbol == "600000")
            .order_by(DailyBar.trade_date)
        ).all()
        profile = session.scalar(
            select(Security.profile).where(Security.symbol == "600000")
        )
    assert dates == [
        requested_start,
        first_existing - timedelta(days=1),
        first_existing,
        provider_end,
    ]
    assert profile["daily_bars_backfill_start"] == requested_start.isoformat()


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
                    source="baostock",
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
                source="baostock",
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
