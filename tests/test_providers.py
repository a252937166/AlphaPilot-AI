from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from alphapilot.data.baostock_provider import BaoStockMarketDataProvider
from alphapilot.data.base import DataProviderError
from alphapilot.data.mock import MockMarketDataProvider
from alphapilot.data.router import FailoverMarketDataProvider


class BrokenProvider:
    name = "broken"

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        raise DataProviderError("upstream down")

    def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        raise DataProviderError("upstream down")


def test_baostock_symbol_normalization() -> None:
    assert BaoStockMarketDataProvider._code("600000") == "sh.600000"
    assert BaoStockMarketDataProvider._code("000333") == "sz.000333"
    assert BaoStockMarketDataProvider._code("SH.000001") == "sh.000001"
    assert BaoStockMarketDataProvider._code("sz.399006".upper()) == "sz.399006"
    with pytest.raises(DataProviderError):
        BaoStockMarketDataProvider._code("HK.00700")


def test_failover_uses_next_provider_and_records_errors() -> None:
    router = FailoverMarketDataProvider(
        bars_chain=[BrokenProvider(), MockMarketDataProvider()],
        snapshot_chain=[BrokenProvider(), MockMarketDataProvider()],
    )
    frame = router.get_daily_bars("600000", date(2026, 1, 1), date(2026, 7, 1))
    assert not frame.empty
    assert router.last_bars_source == "mock"
    assert any("broken" in item for item in router.last_errors)

    snapshot = router.get_snapshot(["600000"])
    assert not snapshot.empty
    assert router.last_snapshot_source == "mock"


def test_failover_raises_when_all_sources_fail() -> None:
    router = FailoverMarketDataProvider(
        bars_chain=[BrokenProvider()], snapshot_chain=[BrokenProvider()]
    )
    with pytest.raises(DataProviderError, match="All providers failed"):
        router.get_daily_bars("600000", date(2026, 1, 1), date(2026, 7, 1))
