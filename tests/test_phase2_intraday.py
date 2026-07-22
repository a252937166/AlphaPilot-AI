from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from alphapilot.data.base import DataProviderError
from alphapilot.services import market_data
from alphapilot.services.market_data import index_intraday


class FakeIntradayClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Any] | None, Any]] = []

    def quote_call_raw(
        self,
        method: str,
        args: list[Any] | None = None,
        kwargs: Any = None,
    ) -> Any:
        self.calls.append((method, args, kwargs))
        if method in {"subscribe", "unsubscribe"}:
            return None
        assert method == "get_rt_data"
        symbol = str(args[0]) if args else ""
        return pd.DataFrame(
            [
                {
                    "code": symbol,
                    "time": "2026-07-21 09:30:00",
                    "cur_price": 3800.5,
                    "avg_price": 3800.5,
                    "volume": 1000,
                    "is_blank": False,
                },
                {
                    "code": symbol,
                    "time": "2026-07-21 09:31:00",
                    "cur_price": 0,
                    "avg_price": 0,
                    "volume": 0,
                    "is_blank": True,
                },
            ]
        )


def test_index_intraday_subscribes_once_and_normalizes_points() -> None:
    client = FakeIntradayClient()
    symbols = ["SH.000001", "SZ.399001"]

    result = index_intraday(client, symbols)  # type: ignore[arg-type]

    assert client.calls[0] == (
        "subscribe",
        [symbols, ["RT_DATA"]],
        {"is_first_push": False},
    )
    assert [call[0] for call in client.calls] == [
        "subscribe",
        "get_rt_data",
        "get_rt_data",
        "unsubscribe",
    ]
    assert result["SH.000001"] == [
        {
            "time": "2026-07-21 09:30:00",
            "price": 3800.5,
            "avg_price": 3800.5,
            "volume": 1000.0,
        }
    ]
    assert len(result["SZ.399001"]) == 1


def test_index_intraday_rejects_missing_provider_fields() -> None:
    class MissingFieldClient(FakeIntradayClient):
        def quote_call_raw(
            self,
            method: str,
            args: list[Any] | None = None,
            kwargs: Any = None,
        ) -> Any:
            if method in {"subscribe", "unsubscribe"}:
                return super().quote_call_raw(method, args, kwargs)
            self.calls.append((method, args, kwargs))
            return pd.DataFrame([{"time": "2026-07-21 09:30:00"}])

    client = MissingFieldClient()
    with pytest.raises(DataProviderError, match="missing fields"):
        index_intraday(client, ["SH.000001"])  # type: ignore[arg-type]
    assert [call[0] for call in client.calls] == [
        "subscribe",
        "get_rt_data",
        "unsubscribe",
    ]


def test_index_intraday_defers_cleanup_when_opend_enforces_minimum_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MinimumLeaseClient(FakeIntradayClient):
        def quote_call_raw(
            self,
            method: str,
            args: list[Any] | None = None,
            kwargs: Any = None,
        ) -> Any:
            if method == "unsubscribe":
                self.calls.append((method, args, kwargs))
                raise DataProviderError("RT订阅时间过短，至少需要订阅1分钟")
            return super().quote_call_raw(method, args, kwargs)

    scheduled: list[tuple[object, list[str]]] = []

    def record_cleanup(client: object, symbols: list[str]) -> None:
        scheduled.append((client, symbols))

    monkeypatch.setattr(market_data, "_schedule_intraday_unsubscribe", record_cleanup)
    client = MinimumLeaseClient()

    result = index_intraday(client, ["SH.600519"])  # type: ignore[arg-type]

    assert result["SH.600519"]
    assert [call[0] for call in client.calls] == [
        "subscribe",
        "get_rt_data",
        "unsubscribe",
    ]
    assert scheduled == [(client, ["SH.600519"])]
