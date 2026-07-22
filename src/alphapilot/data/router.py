from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pandas as pd

from alphapilot.data.base import (
    BarFrequency,
    DataProviderError,
    MarketDataProvider,
    PeriodicMarketDataProvider,
)


class FailoverMarketDataProvider:
    """Composite provider that walks a per-capability chain until one source answers.

    Mirrors the dataset routing policy in config/data_sources.example.yaml:
    daily bars prefer BaoStock (AKShare's eastmoney endpoints are unreliable),
    snapshots prefer Futu. Every failure is collected so the caller can see
    which upstream sources degraded instead of a silent switch.
    """

    name = "auto"

    def __init__(
        self,
        bars_chain: list[MarketDataProvider],
        snapshot_chain: list[MarketDataProvider],
    ):
        if not bars_chain or not snapshot_chain:
            raise DataProviderError("Failover provider requires at least one source per chain.")
        self.bars_chain = bars_chain
        self.snapshot_chain = snapshot_chain
        self.last_bars_source: str | None = None
        self.last_snapshot_source: str | None = None
        self.last_errors: list[str] = []

    def _walk(
        self,
        chain: list[MarketDataProvider],
        call: Callable[[MarketDataProvider], pd.DataFrame],
        describe: str,
    ) -> tuple[str, pd.DataFrame]:
        errors: list[str] = []
        for provider in chain:
            try:
                frame = call(provider)
                if frame is None or frame.empty:
                    errors.append(f"{provider.name}: empty result")
                    continue
                self.last_errors = errors
                return provider.name, frame
            except Exception as exc:  # a broken upstream must not kill the chain
                errors.append(f"{provider.name}: {exc}")
        self.last_errors = errors
        raise DataProviderError(f"All providers failed for {describe}: " + " | ".join(errors))

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        source, frame = self._walk(
            self.bars_chain,
            lambda provider: provider.get_daily_bars(symbol, start, end),
            f"daily bars {symbol}",
        )
        self.last_bars_source = source
        return frame

    def get_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        frequency: BarFrequency,
    ) -> pd.DataFrame:
        """Walk only providers with an audited native frequency implementation."""

        def fetch(provider: MarketDataProvider) -> pd.DataFrame:
            if not isinstance(provider, PeriodicMarketDataProvider):
                raise DataProviderError(
                    f"{provider.name} does not provide native {frequency} bars"
                )
            return provider.get_bars(symbol, start, end, frequency)

        source, frame = self._walk(
            self.bars_chain,
            fetch,
            f"{frequency} bars {symbol}",
        )
        self.last_bars_source = source
        return frame

    def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        source, frame = self._walk(
            self.snapshot_chain,
            lambda provider: provider.get_snapshot(symbols),
            f"snapshot of {len(symbols)} symbols",
        )
        self.last_snapshot_source = source
        return frame
