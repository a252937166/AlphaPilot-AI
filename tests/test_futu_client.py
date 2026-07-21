from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from alphapilot.core.config import Settings
from alphapilot.futu.client import (
    FutuClient,
    FutuFeatureDisabledError,
    FutuMethodNotAllowedError,
)


class FakeSimpleFilter:
    def __init__(self) -> None:
        self.stock_field = "N/A"
        self.filter_min: float | None = None
        self.filter_max: float | None = None


class FakeStockQuoteHandlerBase:
    def on_recv_rsp(self, response: Any) -> Any:
        return response


class FakeQuoteContext:
    last_instance: FakeQuoteContext | None = None

    def __init__(self, **_: Any) -> None:
        self.closed = False
        self.filter_list: list[FakeSimpleFilter] = []
        self.handlers: list[Any] = []
        FakeQuoteContext.last_instance = self

    def close(self) -> None:
        self.closed = True

    def set_handler(self, handler: Any) -> None:
        self.handlers.append(handler)

    def get_global_state(self) -> tuple[int, dict[str, Any]]:
        return 0, {"qot_logined": True, "trd_logined": True, "server_ver": "1009"}

    def get_market_snapshot(self, codes: list[str]) -> tuple[int, pd.DataFrame]:
        return 0, pd.DataFrame(
            [{"code": codes[0], "last_price": 123.45, "change_rate": float("nan")}]
        )

    def get_stock_filter(
        self, market: str, filter_list: list[FakeSimpleFilter]
    ) -> tuple[int, pd.DataFrame]:
        del market
        self.filter_list = filter_list
        return 0, pd.DataFrame([{"code": "HK.00700"}])

    def subscribe(self, code_list: list[str], subtype_list: list[str]) -> tuple[int, str]:
        return 0, f"subscribed {len(code_list)}:{len(subtype_list)}"


class FakeTradeContext:
    last_instance: FakeTradeContext | None = None

    def __init__(self, **_: Any) -> None:
        self.closed = False
        self.last_environment: str | None = None
        FakeTradeContext.last_instance = self

    def close(self) -> None:
        self.closed = True

    def get_acc_list(self) -> tuple[int, pd.DataFrame]:
        return 0, pd.DataFrame([{"trd_env": "SIMULATE"}])

    def place_order(
        self,
        price: float,
        qty: float,
        code: str,
        trd_side: str,
        trd_env: str = "REAL",
    ) -> tuple[int, pd.DataFrame]:
        del price, qty, code, trd_side
        self.last_environment = trd_env
        return 0, pd.DataFrame([{"order_id": "paper-1", "trd_env": trd_env}])

    def unlock_trade(self, password: str | None = None) -> tuple[int, str]:
        del password
        return 0, "should never be called"


class FakeFutuModule:
    RET_OK = 0
    __version__ = "10.9.test"
    OpenQuoteContext = FakeQuoteContext
    OpenSecTradeContext = FakeTradeContext
    OpenFutureTradeContext = FakeTradeContext
    OpenCryptoTradeContext = FakeTradeContext
    SimpleFilter = FakeSimpleFilter
    StockQuoteHandlerBase = FakeStockQuoteHandlerBase


def test_quote_call_serializes_dataframe_and_nan() -> None:
    client = FutuClient(Settings(), sdk_module=FakeFutuModule)
    result = client.quote_call("get_market_snapshot", args=[["HK.00700"]])

    assert result["data"]["row_count"] == 1
    assert result["data"]["records"][0]["code"] == "HK.00700"
    assert result["data"]["records"][0]["change_rate"] is None


def test_complex_filter_object_is_constructed_from_json() -> None:
    client = FutuClient(Settings(), sdk_module=FakeFutuModule)
    request_filter = {
        "__futu_type__": "SimpleFilter",
        "attributes": {"stock_field": "CUR_PRICE", "filter_min": 10.0},
    }

    client.quote_call("get_stock_filter", args=["HK", [request_filter]])

    context = FakeQuoteContext.last_instance
    assert context is not None
    assert context.filter_list[0].stock_field == "CUR_PRICE"
    assert context.filter_list[0].filter_min == 10.0


def test_quote_push_is_forwarded_to_event_queue() -> None:
    client = FutuClient(Settings(), sdk_module=FakeFutuModule)
    event_queue = client.subscribe_events()
    context = FakeQuoteContext.last_instance
    assert context is not None
    assert len(context.handlers) == 1

    context.handlers[0].on_recv_rsp((0, pd.DataFrame([{"code": "HK.00700"}])))
    event = event_queue.get_nowait()

    assert event["type"] == "stock_quote"
    assert event["data"]["records"][0]["code"] == "HK.00700"
    client.unsubscribe_events(event_queue)


def test_internal_quote_method_is_not_exposed() -> None:
    client = FutuClient(Settings(), sdk_module=FakeFutuModule)

    with pytest.raises(FutuMethodNotAllowedError):
        client.quote_call("test_cmd", args=["help", {}])


def test_account_mutation_is_disabled_by_default() -> None:
    client = FutuClient(Settings(), sdk_module=FakeFutuModule)

    with pytest.raises(FutuFeatureDisabledError):
        client.quote_call("modify_user_security", args=["group", "ADD", ["HK.00700"]])


def test_trade_queries_are_a_separate_opt_in() -> None:
    client = FutuClient(Settings(), sdk_module=FakeFutuModule)

    with pytest.raises(FutuFeatureDisabledError):
        client.trade_call("security", "get_acc_list")


def test_simulated_order_can_run_only_after_trade_opt_in() -> None:
    settings = Settings(futu_enable_trade=True)
    client = FutuClient(settings, sdk_module=FakeFutuModule)

    result = client.trade_call(
        "security",
        "place_order",
        args=[100.0, 1, "HK.00700", "BUY"],
        environment="SIMULATE",
    )

    assert result["environment"] == "SIMULATE"
    assert result["data"]["records"][0]["order_id"] == "paper-1"
    context = FakeTradeContext.last_instance
    assert context is not None
    assert context.last_environment == "SIMULATE"
    assert context.closed is True


def test_real_order_requires_live_flag_and_unlock_is_never_exposed() -> None:
    client = FutuClient(
        Settings(futu_enable_trade=True, live_trading_enabled=False),
        sdk_module=FakeFutuModule,
    )

    with pytest.raises(FutuFeatureDisabledError):
        client.trade_call(
            "security",
            "place_order",
            args=[100.0, 1, "HK.00700", "BUY"],
            environment="REAL",
            confirmation="SUBMIT_REAL_ORDER",
        )
    with pytest.raises(FutuMethodNotAllowedError):
        client.trade_call("security", "unlock_trade")
