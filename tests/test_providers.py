from __future__ import annotations

from datetime import date

import httpx
import pandas as pd
import pytest

from alphapilot.data.baostock_provider import BaoStockMarketDataProvider
from alphapilot.data.base import DataProviderError, EmptyDailyBarsError
from alphapilot.data.mock import MockMarketDataProvider
from alphapilot.data.router import FailoverMarketDataProvider
from alphapilot.data.sina_provider import SinaDailyBarProvider


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


def test_baostock_distinguishes_empty_bars_from_query_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        error_code = "0"
        error_msg = ""

        def next(self) -> bool:
            return False

    class BaoStockModule:
        result = Result()

        @classmethod
        def query_history_k_data_plus(cls, *_args: object, **_kwargs: object) -> Result:
            return cls.result

    provider = BaoStockMarketDataProvider()
    monkeypatch.setattr("alphapilot.data.baostock_provider._logged_in", True)
    monkeypatch.setattr(provider, "_module", lambda: BaoStockModule)

    with pytest.raises(EmptyDailyBarsError, match="returned no daily bars"):
        provider.get_daily_bars("600000", date(2026, 7, 20), date(2026, 7, 20))

    BaoStockModule.result.error_code = "1"
    BaoStockModule.result.error_msg = "upstream unavailable"
    with pytest.raises(DataProviderError, match="query failed") as caught:
        provider.get_daily_bars("600000", date(2026, 7, 20), date(2026, 7, 20))
    assert not isinstance(caught.value, EmptyDailyBarsError)


def test_sina_invalid_payload_is_not_classified_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AkShareModule:
        @staticmethod
        def stock_zh_a_daily(**_kwargs: object) -> dict[str, object]:
            return {"unexpected": "payload"}

    provider = SinaDailyBarProvider(min_interval_seconds=0)
    monkeypatch.setattr(provider, "_module", lambda: AkShareModule)
    monkeypatch.setattr("alphapilot.data.sina_provider.sleep", lambda _seconds: None)

    with pytest.raises(DataProviderError, match="failed") as caught:
        provider.get_daily_bars("920000", date(2026, 7, 20), date(2026, 7, 20))
    assert not isinstance(caught.value, EmptyDailyBarsError)


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


def test_sina_bse_snapshot_parses_date_and_market_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = (
        'var hq_str_bj920000="安徽凤凰,11.350,11.320,11.710,11.760,10.700,'
        "11.700,11.710,1056462,11863548.780,0,0,0,0,0,0,0,0,0,0,0,0,0,0,"
        '0,0,0,0,0,0,2026-07-21,15:30:02,00";\n'
    ).encode("gb18030")

    def fake_get(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            content=payload,
            request=httpx.Request("GET", "https://hq.sinajs.cn/"),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    frame = SinaDailyBarProvider(min_interval_seconds=0).get_snapshot(["920000"])

    assert frame.iloc[0]["symbol"] == "920000"
    assert frame.iloc[0]["last_price"] == pytest.approx(11.71)
    assert frame.iloc[0]["prev_close_price"] == pytest.approx(11.32)
    assert frame.iloc[0]["turnover"] == pytest.approx(11_863_548.78)
    assert frame.iloc[0]["as_of"].isoformat() == "2026-07-21T07:30:02+00:00"
