from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alphapilot.api.dependencies import futu_client_dependency
from alphapilot.api.routes import futu, portfolio
from alphapilot.core.config import Settings
from alphapilot.futu.client import FutuClient, FutuFeatureDisabledError
from alphapilot.services.broker import (
    BrokerError,
    fetch_account_funds,
    fetch_positions,
    get_simulate_account,
)


class StubBrokerClient:
    def __init__(
        self,
        *,
        accounts: list[dict[str, Any]] | None = None,
        positions: list[dict[str, Any]] | None = None,
        funds: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.accounts = accounts or []
        self.positions = positions or []
        self.funds = funds or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def trade_call(
        self,
        context_kind: str,
        method: str,
        *,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        market: str = "HK",
        environment: str = "SIMULATE",
        confirmation: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "context_kind": context_kind,
                "method": method,
                "args": args,
                "kwargs": kwargs,
                "market": market,
                "environment": environment,
                "confirmation": confirmation,
            }
        )
        if self.error is not None:
            raise self.error
        records = {
            "get_acc_list": self.accounts,
            "position_list_query": self.positions,
            "accinfo_query": self.funds,
        }[method]
        return {"ok": True, "data": {"records": records}}


def _account(
    account_id: int = 101,
    *,
    environment: str = "SIMULATE",
    account_type: str = "STOCK",
    status: str = "ACTIVE",
    markets: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "acc_id": account_id,
        "trd_env": environment,
        "sim_acc_type": account_type,
        "acc_status": status,
        "trdmarket_auth": markets or ["CN"],
    }


def _as_futu(client: StubBrokerClient) -> FutuClient:
    return cast(FutuClient, client)


def _api(client: StubBrokerClient) -> TestClient:
    app = FastAPI()
    app.include_router(portfolio.router)
    app.dependency_overrides[futu_client_dependency] = lambda: _as_futu(client)
    return TestClient(app)


def _futu_api(client: StubBrokerClient) -> TestClient:
    app = FastAPI()
    app.include_router(futu.router)
    app.dependency_overrides[futu_client_dependency] = lambda: _as_futu(client)
    return TestClient(app)


def test_settings_add_disabled_paper_defaults_and_positive_demo_equity() -> None:
    settings = Settings(
        paper_trading_enabled=False,
        trading_halted=False,
        demo_equity=1_000_000.0,
    )

    assert settings.paper_trading_enabled is False
    assert settings.trading_halted is False
    assert settings.demo_equity == pytest.approx(1_000_000.0)


def test_simulate_account_uses_all_real_selection_fields_and_is_cached() -> None:
    client = StubBrokerClient(
        accounts=[
            _account(1, environment="REAL"),
            _account(2, account_type="OPTION"),
            _account(3, status="DISABLED"),
            _account(4, markets=["HK"]),
            _account(5),
        ]
    )

    first = get_simulate_account(_as_futu(client))
    second = get_simulate_account(_as_futu(client), "cn")

    assert first == {"acc_id": 5, "market": "CN", "environment": "SIMULATE"}
    assert second == first
    assert [call["method"] for call in client.calls] == ["get_acc_list"]
    assert client.calls[0]["market"] == "CN"
    assert client.calls[0]["environment"] == "SIMULATE"


@pytest.mark.parametrize(
    ("accounts", "message"),
    [
        ([], "无可用"),
        ([_account(1), _account(2)], "多个"),
        ([{**_account(), "acc_id": "101"}], "标识格式异常"),
    ],
)
def test_simulate_account_rejects_missing_ambiguous_or_non_integer_ids(
    accounts: list[dict[str, Any]],
    message: str,
) -> None:
    client = StubBrokerClient(accounts=accounts)

    with pytest.raises(BrokerError, match=message):
        get_simulate_account(_as_futu(client))


def test_positions_and_funds_are_normalized_without_demo_fallback() -> None:
    client = StubBrokerClient(
        accounts=[_account(77)],
        positions=[
            {
                "code": "SH.600000",
                "qty": 200,
                "cost_price": 10.5,
                "cost_price_valid": True,
                "market_val": 2200,
                "pl_ratio": 4.75,
                "pl_ratio_valid": True,
            },
            {
                "code": "SZ.000001",
                "qty": 100,
                "cost_price": "N/A",
                "cost_price_valid": False,
                "market_val": 1000,
                "pl_ratio": "N/A",
                "pl_ratio_valid": False,
            },
        ],
        funds=[{"total_assets": 1_000_000, "cash": 996_800, "market_val": 3_200}],
    )

    funds = fetch_account_funds(_as_futu(client))
    positions = fetch_positions(_as_futu(client))

    assert funds == {"total_assets": 1_000_000.0, "cash": 996_800.0, "market_val": 3_200.0}
    assert positions == [
        {
            "symbol": "000001",
            "qty": 100.0,
            "cost_price": None,
            "market_val": 1_000.0,
            "pnl_ratio": None,
        },
        {
            "symbol": "600000",
            "qty": 200.0,
            "cost_price": 10.5,
            "market_val": 2_200.0,
            "pnl_ratio": pytest.approx(0.0475),
        },
    ]
    assert [call["method"] for call in client.calls] == [
        "get_acc_list",
        "accinfo_query",
        "position_list_query",
    ]
    for call in client.calls:
        assert call["market"] == "CN"
        assert call["environment"] == "SIMULATE"
    assert client.calls[1]["kwargs"] == {"acc_id": 77, "refresh_cache": True}
    assert client.calls[2]["kwargs"] == {"acc_id": 77, "refresh_cache": True}


def test_broker_rejects_non_finite_funds_instead_of_returning_invalid_json() -> None:
    client = StubBrokerClient(
        accounts=[_account()],
        funds=[{"total_assets": float("nan"), "cash": 100.0, "market_val": 0.0}],
    )

    with pytest.raises(BrokerError, match="有限数值"):
        fetch_account_funds(_as_futu(client))


def test_portfolio_account_api_returns_funds_and_truthful_empty_positions() -> None:
    client = StubBrokerClient(
        accounts=[_account()],
        positions=[],
        funds=[{"total_assets": 1_000_000, "cash": 1_000_000, "market_val": 0}],
    )

    with _api(client) as api:
        response = api.get("/v1/portfolio/account")

    assert response.status_code == 200
    assert response.json() == {
        "market": "CN",
        "environment": "SIMULATE",
        "total_assets": 1_000_000.0,
        "cash": 1_000_000.0,
        "market_val": 0.0,
        "positions": [],
    }
    assert "acc_id" not in response.text


def test_portfolio_account_api_returns_chinese_503_when_query_is_disabled() -> None:
    client = StubBrokerClient(error=FutuFeatureDisabledError("disabled by configuration"))

    with _api(client) as api:
        response = api.get("/v1/portfolio/account")

    assert response.status_code == 503
    assert response.json() == {"detail": "富途模拟账户只读查询未启用。"}


@pytest.mark.parametrize("method", ["get_acc_list", "position_list_query"])
def test_generic_futu_http_route_keeps_account_queries_private(method: str) -> None:
    client = StubBrokerClient(accounts=[_account(987654321)])

    with _futu_api(client) as api:
        response = api.post(
            f"/v1/futu/trade/security/{method}",
            json={"market": "CN", "environment": "SIMULATE"},
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "富途账户与持仓查询仅供内部使用，请改用 /v1/portfolio/account。"
    }
    assert client.calls == []


@pytest.mark.parametrize("method", ["place_order", "change_order", "cancel_all_order"])
def test_generic_futu_http_route_blocks_trade_mutations_before_client_call(method: str) -> None:
    client = StubBrokerClient()

    with _futu_api(client) as api:
        response = api.post(
            f"/v1/futu/trade/security/{method}",
            json={"market": "CN", "environment": "SIMULATE"},
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "通用富途 HTTP 路由禁止交易写操作，请使用受控的模拟交易执行接口。"
    }
    assert client.calls == []
