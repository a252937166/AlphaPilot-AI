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

from alphapilot.data.base import DataProviderError
from alphapilot.db.models import Base, DailyBar, Security
from alphapilot.jobs import daily_bars


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
        "_latest_provider_trade_date",
        lambda provider, _benchmark, _requested_end: (
            baostock_end if provider.name == "baostock" else requested_end
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
        "_latest_provider_trade_date",
        lambda _provider, _benchmark, requested_end: requested_end,
    )
    monkeypatch.setattr(daily_bars, "BaoStockMarketDataProvider", FailingProvider)

    with pytest.raises(DataProviderError, match="20 consecutive failures"):
        daily_bars.sync_daily_bars(lookback_days=10, batch_size=5)
