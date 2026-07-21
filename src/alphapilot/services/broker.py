from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from threading import RLock
from typing import Any
from weakref import WeakKeyDictionary

from alphapilot.futu.client import FutuClient


class BrokerError(RuntimeError):
    """The simulated broker returned no safe, usable account data."""


_ACCOUNT_CACHE: WeakKeyDictionary[FutuClient, dict[str, dict[str, Any]]] = WeakKeyDictionary()
_ACCOUNT_CACHE_LOCK = RLock()


def _records(response: object, operation: str) -> list[dict[str, Any]]:
    if not isinstance(response, Mapping):
        raise BrokerError(f"富途{operation}返回格式异常。")
    data = response.get("data")
    if not isinstance(data, Mapping):
        raise BrokerError(f"富途{operation}缺少数据。")
    raw_records = data.get("records")
    if not isinstance(raw_records, list):
        raise BrokerError(f"富途{operation}缺少记录列表。")

    records: list[dict[str, Any]] = []
    for raw_record in raw_records:
        if not isinstance(raw_record, Mapping):
            raise BrokerError(f"富途{operation}包含无效记录。")
        records.append({str(key): value for key, value in raw_record.items()})
    return records


def _market_authorizations(value: object) -> set[str]:
    if isinstance(value, str):
        return {value.strip().upper()} if value.strip() else set()
    if isinstance(value, list):
        return {str(item).strip().upper() for item in value if str(item).strip()}
    return set()


def _required_number(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise BrokerError(f"富途模拟账户字段 {field} 不是有效数值。")
    try:
        number = float(str(value))
    except (TypeError, ValueError) as exc:
        raise BrokerError(f"富途模拟账户字段 {field} 不是有效数值。") from exc
    if not isfinite(number):
        raise BrokerError(f"富途模拟账户字段 {field} 不是有限数值。")
    return number


def _optional_number(value: object, field: str) -> float | None:
    if value is None or str(value).strip().upper() in {"", "N/A", "NONE"}:
        return None
    return _required_number(value, field)


def _symbol(value: object) -> str:
    raw = str(value).strip().upper()
    code = raw.split(".", 1)[-1] if "." in raw else raw
    if len(code) != 6 or not code.isdigit():
        raise BrokerError("富途模拟持仓包含无效股票代码。")
    return code


def get_simulate_account(client: FutuClient, market: str = "CN") -> dict[str, Any]:
    """Return the unique active simulated stock account authorized for a market."""

    market_code = market.strip().upper()
    if not market_code:
        raise BrokerError("模拟账户市场不能为空。")

    with _ACCOUNT_CACHE_LOCK:
        cached_by_market = _ACCOUNT_CACHE.get(client)
        if cached_by_market is not None and market_code in cached_by_market:
            return dict(cached_by_market[market_code])

        response = client.trade_call(
            "security",
            "get_acc_list",
            market=market_code,
            environment="SIMULATE",
        )
        matches = [
            row
            for row in _records(response, "模拟账户查询")
            if str(row.get("trd_env") or "").strip().upper() == "SIMULATE"
            and str(row.get("sim_acc_type") or "").strip().upper() == "STOCK"
            and str(row.get("acc_status") or "").strip().upper() == "ACTIVE"
            and market_code in _market_authorizations(row.get("trdmarket_auth"))
        ]
        if not matches:
            raise BrokerError(f"无可用的 {market_code} 模拟证券账户。")
        if len(matches) != 1:
            raise BrokerError(f"检测到多个 {market_code} 模拟证券账户，已拒绝自动选择。")

        account_id = matches[0].get("acc_id")
        if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id <= 0:
            raise BrokerError("富途模拟账户标识格式异常。")
        account = {
            "acc_id": account_id,
            "market": market_code,
            "environment": "SIMULATE",
        }
        if cached_by_market is None:
            cached_by_market = {}
            _ACCOUNT_CACHE[client] = cached_by_market
        cached_by_market[market_code] = account
        return dict(account)


def fetch_positions(client: FutuClient) -> list[dict[str, Any]]:
    """Return normalized positions from the CN simulated securities account."""

    account = get_simulate_account(client)
    response = client.trade_call(
        "security",
        "position_list_query",
        kwargs={"acc_id": account["acc_id"]},
        market="CN",
        environment="SIMULATE",
    )
    positions: list[dict[str, Any]] = []
    for row in _records(response, "模拟持仓查询"):
        cost_price = (
            _optional_number(row.get("cost_price"), "cost_price")
            if row.get("cost_price_valid") is not False
            else None
        )
        pnl_percent = (
            _optional_number(row.get("pl_ratio"), "pl_ratio")
            if row.get("pl_ratio_valid") is not False
            else None
        )
        positions.append(
            {
                "symbol": _symbol(row.get("code")),
                "qty": _required_number(row.get("qty"), "qty"),
                "cost_price": cost_price,
                "market_val": _required_number(row.get("market_val"), "market_val"),
                "pnl_ratio": pnl_percent / 100.0 if pnl_percent is not None else None,
            }
        )
    positions.sort(key=lambda item: str(item["symbol"]))
    return positions


def fetch_account_funds(client: FutuClient) -> dict[str, Any]:
    """Return cash and asset totals from the CN simulated securities account."""

    account = get_simulate_account(client)
    response = client.trade_call(
        "security",
        "accinfo_query",
        kwargs={"acc_id": account["acc_id"]},
        market="CN",
        environment="SIMULATE",
    )
    records = _records(response, "模拟账户资金查询")
    if len(records) != 1:
        raise BrokerError("富途模拟账户资金查询未返回唯一记录。")
    row = records[0]
    return {
        "total_assets": _required_number(row.get("total_assets"), "total_assets"),
        "cash": _required_number(row.get("cash"), "cash"),
        "market_val": _required_number(row.get("market_val"), "market_val"),
    }
