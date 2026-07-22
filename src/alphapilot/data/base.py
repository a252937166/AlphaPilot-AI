from __future__ import annotations

from datetime import date
from typing import Literal, Protocol, runtime_checkable

import pandas as pd

BarFrequency = Literal["d", "w", "m"]


class DataProviderError(RuntimeError):
    """Raised when a market-data provider cannot satisfy a request."""


class EmptyDailyBarsError(DataProviderError):
    """Raised when a valid daily-bar request returns no usable rows."""


class MarketDataProvider(Protocol):
    name: str

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Return normalized columns: date, open, high, low, close, volume, amount."""

    def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        """Return normalized columns: symbol, last, change_pct, volume, amount, as_of."""


@runtime_checkable
class PeriodicMarketDataProvider(Protocol):
    """Optional native weekly/monthly history capability.

    The primary provider protocol deliberately remains backward compatible with
    daily-only test doubles and auxiliary providers. Callers must check this
    protocol before requesting a non-daily frequency.
    """

    def get_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        frequency: BarFrequency,
    ) -> pd.DataFrame:
        """Return normalized OHLCVA bars at the requested native frequency."""
