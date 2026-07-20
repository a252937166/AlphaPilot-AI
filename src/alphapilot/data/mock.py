from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd


class MockMarketDataProvider:
    name = "mock"

    @staticmethod
    def _seed(symbol: str) -> int:
        digest = hashlib.sha256(symbol.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big")

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        index = pd.bdate_range(start=start, end=end)
        if len(index) < 2:
            index = pd.bdate_range(end=end, periods=120)

        rng = np.random.default_rng(self._seed(symbol))
        drift = ((self._seed(symbol) % 17) - 8) / 100_000
        returns = rng.normal(loc=drift, scale=0.016, size=len(index))
        close = 20 * np.exp(np.cumsum(returns))
        overnight = rng.normal(0, 0.003, size=len(index))
        open_price = close * (1 + overnight)
        spread = np.abs(rng.normal(0.008, 0.004, size=len(index)))
        high = np.maximum(open_price, close) * (1 + spread)
        low = np.minimum(open_price, close) * (1 - spread)
        volume = rng.lognormal(mean=15.5, sigma=0.35, size=len(index))
        amount = volume * close

        return pd.DataFrame(
            {
                "date": index,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "amount": amount,
            }
        )

    def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        now = datetime.now(UTC)
        rows: list[dict[str, object]] = []
        for symbol in symbols:
            bars = self.get_daily_bars(symbol, date(2025, 1, 1), now.date())
            last = float(bars.iloc[-1]["close"])
            previous = float(bars.iloc[-2]["close"])
            rows.append(
                {
                    "symbol": symbol,
                    "last": last,
                    "change_pct": (last / previous - 1) * 100,
                    "volume": float(bars.iloc[-1]["volume"]),
                    "amount": float(bars.iloc[-1]["amount"]),
                    "as_of": now,
                }
            )
        return pd.DataFrame(rows)
