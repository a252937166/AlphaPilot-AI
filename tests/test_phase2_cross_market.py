from __future__ import annotations

from typing import Any

import httpx
import pandas as pd
import pytest

from alphapilot.main import app
from alphapilot.services import cross_market


def test_safe_fx_parser_scales_official_hundred_usd_units() -> None:
    html = """
    <table>
      <tr><th>日期</th><th>美元</th><th>欧元</th></tr>
      <tr><td>2026-07-20</td><td>679.48</td><td>775.70</td></tr>
      <tr><td>2026-07-21</td><td>679.17</td><td>774.18</td></tr>
    </table>
    """

    result = cross_market._parse_safe_fx(html)

    assert result["value"] == 6.7917
    assert result["change_pct"] == pytest.approx(-0.0456)
    assert result["as_of"] == "2026-07-21"
    assert result["source"] == "safe-pboc"


def test_us_futures_uses_first_available_real_contract() -> None:
    class FakeFutu:
        def quote_call_raw(
            self,
            method: str,
            args: list[Any] | None = None,
            kwargs: Any = None,
        ) -> pd.DataFrame:
            del kwargs
            assert method == "get_market_snapshot"
            assert args == [["US.ESmain"]]
            return pd.DataFrame(
                [
                    {
                        "code": "US.ESmain",
                        "name": "E-mini S&P 500",
                        "last_price": 5293.0,
                        "prev_close_price": 5280.0,
                        "update_time": "2026-07-21 17:15:00",
                    }
                ]
            )

    result = cross_market.fetch_us_futures(FakeFutu())  # type: ignore[arg-type]

    assert result["contract"] == "US.ESmain"
    assert result["last"] == 5293.0
    assert result["change_pct"] == pytest.approx(0.2462)
    assert result["source"] == "futu"


def test_commodity_index_uses_bounded_ccidx_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "data": {
            "dateLineJson": [
                {
                    "tradeDate": "2026-07-20",
                    "closingPrice": 2157.08,
                    "dailyIncreaseAndDecreasePercentageClose": 1.11,
                },
                {
                    "tradeDate": "2026-07-21",
                    "closingPrice": 2168.51,
                    "dailyIncreaseAndDecreasePercentageClose": 0.53,
                },
            ]
        }
    }

    def fake_get(*_args: object, **kwargs: object) -> httpx.Response:
        assert kwargs["timeout"] == 10.0
        return httpx.Response(
            200,
            json=payload,
            request=httpx.Request("GET", cross_market.CCIDX_URL),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    result = cross_market.fetch_commodity_index()

    assert result == {
        "name": "中证商品期货指数",
        "last": 2168.51,
        "change_pct": 0.53,
        "as_of": "2026-07-21",
        "source": "ccidx",
    }


def test_cross_market_snapshot_isolates_each_unavailable_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cross_market,
        "fetch_fx_usdcny",
        lambda: {
            "value": 6.7917,
            "change_pct": -0.0456,
            "as_of": "2026-07-21",
            "source": "safe-pboc",
        },
    )
    monkeypatch.setattr(
        cross_market,
        "fetch_us_futures",
        lambda _client: (_ for _ in ()).throw(RuntimeError("行情权限不足")),
    )
    monkeypatch.setattr(
        cross_market,
        "fetch_commodity_index",
        lambda: (_ for _ in ()).throw(RuntimeError("source offline")),
    )

    result = cross_market.cross_market_snapshot(object())  # type: ignore[arg-type]

    assert set(result) == {"fx_usdcny", "us_futures", "commodities", "northbound"}
    assert result["fx_usdcny"]["value"] == 6.7917
    assert result["us_futures"]["last"] is None
    assert "权限不足" in result["us_futures"]["note"]
    assert result["commodities"]["last"] is None
    assert result["northbound"]["daily_balance"] is None


def test_cross_market_route_is_registered() -> None:
    assert "/v1/market/cross" in app.openapi()["paths"]
