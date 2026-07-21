from __future__ import annotations

from datetime import date
from typing import ClassVar

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


def test_baostock_quarterly_financials_queries_all_datasets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        error_code = "0"
        error_msg = ""

        def __init__(self, fields: list[str], rows: list[list[str]]) -> None:
            self.fields = fields
            self._rows = iter(rows)
            self._current: list[str] = []

        def next(self) -> bool:
            try:
                self._current = next(self._rows)
            except StopIteration:
                return False
            return True

        def get_row_data(self) -> list[str]:
            return self._current

    class BaoStockModule:
        calls: ClassVar[list[tuple[str, dict[str, object]]]] = []

        @classmethod
        def _query(cls, dataset: str, kwargs: dict[str, object]) -> Result:
            cls.calls.append((dataset, kwargs))
            if dataset == "cash_flow":
                return Result(["code", "statDate", "CFOToNP"], [])
            return Result(
                ["code", "statDate", "value"],
                [["sh.600519", "2025-12-31", dataset]],
            )

        @classmethod
        def query_profit_data(cls, **kwargs: object) -> Result:
            return cls._query("profit", kwargs)

        @classmethod
        def query_growth_data(cls, **kwargs: object) -> Result:
            return cls._query("growth", kwargs)

        @classmethod
        def query_cash_flow_data(cls, **kwargs: object) -> Result:
            return cls._query("cash_flow", kwargs)

        @classmethod
        def query_balance_data(cls, **kwargs: object) -> Result:
            return cls._query("balance", kwargs)

    provider = BaoStockMarketDataProvider()
    monkeypatch.setattr("alphapilot.data.baostock_provider._logged_in", True)
    monkeypatch.setattr(provider, "_module", lambda: BaoStockModule)

    frames = provider.get_quarterly_financials("600519", 2025, 4)

    assert list(frames) == ["profit", "growth", "cash_flow", "balance"]
    assert frames["profit"].iloc[0]["value"] == "profit"
    assert frames["growth"].iloc[0]["value"] == "growth"
    assert frames["cash_flow"].empty
    assert list(frames["cash_flow"].columns) == ["code", "statDate", "CFOToNP"]
    assert frames["balance"].iloc[0]["value"] == "balance"
    assert BaoStockModule.calls == [
        (dataset, {"code": "sh.600519", "year": 2025, "quarter": 4})
        for dataset in ["profit", "growth", "cash_flow", "balance"]
    ]

    BaoStockModule.calls.clear()
    profit = provider.get_quarterly_profit("600519", 2024, 4)
    assert profit.iloc[0]["value"] == "profit"
    assert BaoStockModule.calls == [("profit", {"code": "sh.600519", "year": 2024, "quarter": 4})]


def test_baostock_quarterly_financials_surfaces_query_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        error_code = "1"
        error_msg = "upstream unavailable"
        fields: ClassVar[tuple[str, ...]] = ()

    class BaoStockModule:
        @staticmethod
        def query_profit_data(**_kwargs: object) -> Result:
            return Result()

    provider = BaoStockMarketDataProvider()
    monkeypatch.setattr("alphapilot.data.baostock_provider._logged_in", True)
    monkeypatch.setattr(provider, "_module", lambda: BaoStockModule)

    with pytest.raises(
        DataProviderError,
        match=r"profit query failed for sh\.600519/2025Q4: upstream unavailable",
    ):
        provider.get_quarterly_financials("600519", 2025, 4)


def test_baostock_quarterly_financials_relogs_once_after_expired_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        fields: ClassVar[tuple[str, str]] = ("code", "statDate")

        def __init__(self, error_code: str = "0", error_msg: str = "") -> None:
            self.error_code = error_code
            self.error_msg = error_msg
            self._pending = error_code == "0"

        def next(self) -> bool:
            if self._pending:
                self._pending = False
                return True
            return False

        @staticmethod
        def get_row_data() -> list[str]:
            return ["sh.600519", "2025-12-31"]

    class BaoStockModule:
        login_calls = 0
        query_calls: ClassVar[dict[str, int]] = {
            "profit": 0,
            "growth": 0,
            "cash_flow": 0,
            "balance": 0,
        }

        @classmethod
        def login(cls) -> Result:
            cls.login_calls += 1
            return Result()

        @classmethod
        def _query(cls, dataset: str) -> Result:
            cls.query_calls[dataset] += 1
            if dataset == "profit" and cls.query_calls[dataset] == 1:
                return Result("10001001", "用户未登录")
            return Result()

        @classmethod
        def query_profit_data(cls, **_kwargs: object) -> Result:
            return cls._query("profit")

        @classmethod
        def query_growth_data(cls, **_kwargs: object) -> Result:
            return cls._query("growth")

        @classmethod
        def query_cash_flow_data(cls, **_kwargs: object) -> Result:
            return cls._query("cash_flow")

        @classmethod
        def query_balance_data(cls, **_kwargs: object) -> Result:
            return cls._query("balance")

    provider = BaoStockMarketDataProvider()
    monkeypatch.setattr("alphapilot.data.baostock_provider._logged_in", True)
    monkeypatch.setattr(provider, "_module", lambda: BaoStockModule)

    frames = provider.get_quarterly_financials("600519", 2025, 4)

    assert all(not frame.empty for frame in frames.values())
    assert BaoStockModule.login_calls == 1
    assert BaoStockModule.query_calls == {
        "profit": 2,
        "growth": 1,
        "cash_flow": 1,
        "balance": 1,
    }


def test_baostock_quarterly_financials_validates_period_and_rejects_bse() -> None:
    provider = BaoStockMarketDataProvider()

    with pytest.raises(ValueError, match="year/quarter is out of range"):
        provider.get_quarterly_financials("600519", 2025, 5)
    with pytest.raises(ValueError, match="year/quarter is out of range"):
        provider.get_quarterly_financials("600519", 1989, 4)
    with pytest.raises(DataProviderError, match="do not support symbol: 920000"):
        provider.get_quarterly_financials("920000", 2025, 4)


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
