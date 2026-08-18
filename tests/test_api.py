from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from alphapilot.api.dependencies import (
    baostock_session_dependency,
    futu_client_dependency,
)
from alphapilot.futu.client import FutuUnavailableError
from alphapilot.main import app

client = TestClient(app)


class StubFutuClient:
    """Offline stand-in so API tests never open a socket to OpenD."""

    def status(self, *, max_age_seconds: float = 3.0) -> dict[str, object]:
        del max_age_seconds
        return {"healthy": True, "qot_logined": True, "enabled": True}

    def capabilities(self) -> dict[str, object]:
        return {"sdk_installed": True, "quote": [{"method": "get_market_snapshot"}]}

    def quote_call(
        self, method: str, args: list[object], kwargs: dict[str, object]
    ) -> dict[str, object]:
        return {"ok": True, "surface": "quote", "method": method, "data": [args, kwargs]}

    def quote_call_raw(self, method: str, args: Any = None, kwargs: Any = None) -> Any:
        raise FutuUnavailableError("stub client has no OpenD connection")


@pytest.fixture(autouse=True)
def stub_futu_dependency() -> Iterator[None]:
    app.dependency_overrides[futu_client_dependency] = StubFutuClient
    yield
    app.dependency_overrides.pop(futu_client_dependency, None)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["ok"] is True
    assert body["trading"]["order_submission_endpoint_exposed"] is True
    assert body["trading"]["unlock_trade_endpoint_exposed"] is False
    assert body["trading"]["paper_auto_trading_enabled"] is False
    assert body["trading"]["trading_mode"] == "research"


def test_api_globally_scopes_baostock_sessions() -> None:
    assert any(
        dependency.dependency is baostock_session_dependency
        for dependency in app.router.dependencies
    )


def test_mock_screen_api_persists_run() -> None:
    response = client.post(
        "/v1/screens/run",
        json={"symbols": ["600000", "000001", "000333"], "top_n": 2, "provider": "mock"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["candidates"]) == 2

    latest = client.get("/v1/screens/latest")
    assert latest.status_code == 200
    assert latest.json()["requested"] == 3


def test_futu_generic_quote_api() -> None:
    response = client.post(
        "/v1/futu/quote/get_market_snapshot",
        json={"args": [["HK.00700"]], "kwargs": {}},
    )
    assert response.status_code == 200
    assert response.json()["method"] == "get_market_snapshot"


def test_watchlist_crud_track_and_alerts() -> None:
    created = client.post(
        "/v1/watchlist",
        json={
            "symbol": "600000",
            "display_name": "浦发银行",
            "thesis": "测试逻辑",
            "cost_price": 8.0,
        },
    )
    assert created.status_code == 200
    assert created.json()["item"]["symbol"] == "600000"

    listed = client.get("/v1/watchlist")
    symbols = [item["symbol"] for item in listed.json()["items"]]
    assert "600000" in symbols

    tracked = client.get("/v1/watchlist/track", params={"provider": "mock"})
    assert tracked.status_code == 200
    rows = {row["symbol"]: row for row in tracked.json()["rows"]}
    assert rows["600000"]["last"] is not None

    refreshed = client.post("/v1/alerts/refresh", params={"provider": "mock"})
    assert refreshed.status_code == 200
    assert any(item["symbol"] == "600000" for item in refreshed.json()["created"])

    alerts = client.get("/v1/alerts")
    assert alerts.status_code == 200
    assert len(alerts.json()["alerts"]) >= 1

    first_alert = alerts.json()["alerts"][0]
    acked = client.post(f"/v1/alerts/{first_alert['id']}/acknowledge")
    assert acked.status_code == 200
    assert acked.json()["alert"]["acknowledged"] is True

    removed = client.delete("/v1/watchlist/600000")
    assert removed.status_code == 200


def test_stock_bars_endpoint() -> None:
    response = client.get("/v1/stocks/600519/bars", params={"provider": "mock", "days": 60})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "mock"
    assert len(body["bars"]) > 30
    assert {"date", "open", "close"} <= set(body["bars"][0])

    weekly = client.get(
        "/v1/stocks/600519/bars",
        params={"provider": "mock", "days": 60, "freq": "w"},
    )
    assert weekly.status_code == 200
    assert weekly.json()["frequency"] == "w"
    assert 1 < len(weekly.json()["bars"]) <= 60
    assert "该来源日线聚合" in weekly.json()["warnings"][0]

    invalid = client.get(
        "/v1/stocks/600519/bars",
        params={"provider": "mock", "days": 60, "freq": "quarter"},
    )
    assert invalid.status_code == 422


def test_dashboard_overview_degrades_without_futu() -> None:
    response = client.get("/v1/dashboard/overview", params={"provider": "mock"})
    assert response.status_code == 200
    body = response.json()
    assert body["ai_summary"]["source"] in {"template", "llm"}
    assert body["regime"] is not None
    assert isinstance(body["sectors"], list)


def test_daily_report_generate_and_get() -> None:
    client.post(
        "/v1/watchlist",
        json={"symbol": "300750", "display_name": "宁德时代"},
    )
    generated = client.post("/v1/reports/daily/generate", params={"provider": "mock"})
    assert generated.status_code == 200
    body = generated.json()
    assert body["ai_summary"]["text"]
    assert "forecast_hit_stats" in body
    assert "signal_attribution" in body

    fetched = client.get("/v1/reports/daily")
    assert fetched.status_code == 200
    assert fetched.json()["report_date"] == body["report_date"]


def test_trade_proposal_without_audited_alert_is_rejected_before_broker() -> None:
    payload = {
        "proposal": {
            "proposal_id": "test-prop-1",
            "idempotency_key": "test-key-1",
            "symbol": "600519",
            "side": "BUY",
            "quantity": 100,
            "estimated_notional": 150000,
            "confidence": 0.8,
            "market_data_as_of": "2026-07-20T07:00:00+00:00",
            "model_version": "test",
            "mode": "confirm_to_trade",
        },
        "portfolio": {
            "equity": 10_000_000,
            "cash": 5_000_000,
            "daily_pnl_pct": 0.0,
            "current_position_pct": 0.0,
            "sector_position_pct": 0.0,
            "open_orders_for_symbol": 0,
        },
    }
    created = client.post("/v1/trades/proposals", json=payload)
    assert created.status_code == 422
    assert "必须绑定一条可审计的方向性提醒" in created.json()["detail"]


def test_disclosure_status_endpoint() -> None:
    response = client.get("/v1/disclosures/status")
    assert response.status_code == 200
    assert "configured" in response.json()


def test_market_intraday_returns_normalized_index_points() -> None:
    class IntradayFutuClient(StubFutuClient):
        def quote_call_raw(self, method: str, args: Any = None, kwargs: Any = None) -> Any:
            del kwargs
            if method in {"subscribe", "unsubscribe"}:
                return None
            assert method == "get_rt_data"
            return pd.DataFrame(
                [
                    {
                        "time": "2026-07-21 09:30:00",
                        "cur_price": 3800.5,
                        "avg_price": 3800.5,
                        "volume": 1000,
                        "is_blank": False,
                        "code": args[0],
                    }
                ]
            )

    app.dependency_overrides[futu_client_dependency] = IntradayFutuClient
    response = client.get(
        "/v1/market/intraday",
        params={"symbols": "SH.000001,SZ.399001"},
    )

    assert response.status_code == 200
    assert response.json()["SH.000001"][0] == {
        "time": "2026-07-21 09:30:00",
        "price": 3800.5,
        "avg_price": 3800.5,
        "volume": 1000.0,
    }


def test_market_intraday_accepts_and_normalizes_an_a_share_symbol() -> None:
    class IntradayStockFutuClient(StubFutuClient):
        def __init__(self) -> None:
            self.subscribed: list[str] = []
            self.unsubscribed: list[str] = []

        def quote_call_raw(self, method: str, args: Any = None, kwargs: Any = None) -> Any:
            del kwargs
            if method == "subscribe":
                self.subscribed = list(args[0])
                return None
            if method == "unsubscribe":
                self.unsubscribed = list(args[0])
                return None
            assert method == "get_rt_data"
            assert args == ["SH.600519"]
            return pd.DataFrame(
                [
                    {
                        "time": "2026-07-22 09:30:00",
                        "cur_price": 1_300.0,
                        "avg_price": 1_300.0,
                        "volume": 100.0,
                        "is_blank": False,
                    }
                ]
            )

    intraday = IntradayStockFutuClient()
    app.dependency_overrides[futu_client_dependency] = lambda: intraday
    response = client.get("/v1/market/intraday", params={"symbols": "600519"})

    assert response.status_code == 200
    assert intraday.subscribed == ["SH.600519"]
    assert intraday.unsubscribed == ["SH.600519"]
    assert response.json()["SH.600519"][0]["price"] == pytest.approx(1_300.0)


def test_market_intraday_rejects_non_core_symbol_without_subscribing() -> None:
    response = client.get(
        "/v1/market/intraday",
        params={"symbols": "SH.000001,HK.00700"},
    )
    assert response.status_code == 422
    assert "仅支持" in response.json()["detail"]

    bse = client.get(
        "/v1/market/intraday",
        params={"symbols": "920000"},
    )
    assert bse.status_code == 422
    assert "仅支持" in bse.json()["detail"]


def test_market_intraday_explains_futu_unavailability() -> None:
    response = client.get(
        "/v1/market/intraday",
        params={"symbols": "SH.000001"},
    )
    assert response.status_code == 503
    assert "请确认 Futu OpenD" in response.json()["detail"]
