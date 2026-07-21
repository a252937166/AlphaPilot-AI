from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd


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
