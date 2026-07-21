from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timedelta
from io import StringIO
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from alphapilot.futu.client import FutuClient

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
SAFE_URL = "https://www.safe.gov.cn/AppStructured/hlw/RMBQuery.do"
CCIDX_URL = "http://www.ccidx.com/CCI-ZZZS/index/getDateLine"
US_FUTURES = (
    ("US.ESmain", "标普500期指"),
    ("US.NQmain", "纳斯达克100期指"),
    ("US.YMmain", "道琼斯期指"),
    ("US.RTYmain", "罗素2000期指"),
)


class CrossMarketError(RuntimeError):
    """Raised when one independent cross-market source has no usable truth."""


def _finite_float(value: object) -> float | None:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _error_note(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def _parse_safe_fx(html: str) -> dict[str, Any]:
    frames = pd.read_html(StringIO(html))
    frame = next(
        (
            item
            for item in frames
            if "日期" in item.columns and "美元" in item.columns and len(item) > 0
        ),
        None,
    )
    if frame is None:
        raise CrossMarketError("SAFE response has no RMB central-parity table")

    points: list[tuple[pd.Timestamp, float]] = []
    for raw in frame.to_dict(orient="records"):
        date_value = raw.get("日期")
        if date_value is None:
            continue
        parsed_date = pd.to_datetime(str(date_value), errors="coerce")
        raw_value = _finite_float(raw.get("美元"))
        if pd.isna(parsed_date) or raw_value is None or raw_value <= 0:
            continue
        points.append((pd.Timestamp(parsed_date), raw_value / 100.0))
    points.sort(key=lambda item: item[0])
    if not points:
        raise CrossMarketError("SAFE response has no usable USD/CNY observations")

    latest_date, latest_value = points[-1]
    previous_value = points[-2][1] if len(points) > 1 else None
    change_pct = (
        (latest_value / previous_value - 1.0) * 100.0
        if previous_value is not None and previous_value > 0
        else None
    )
    return {
        "value": round(latest_value, 4),
        "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "as_of": latest_date.date().isoformat(),
        "source": "safe-pboc",
    }


def fetch_fx_usdcny() -> dict[str, Any]:
    today = datetime.now(MARKET_TIMEZONE).date()
    response = httpx.post(
        SAFE_URL,
        data={
            "startDate": (today - timedelta(days=40)).isoformat(),
            "endDate": today.isoformat(),
            "queryYN": "true",
        },
        headers={"User-Agent": "Mozilla/5.0 (compatible; AlphaPilot-AI/0.2)"},
        timeout=10.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    return _parse_safe_fx(response.text)


def fetch_us_futures(client: FutuClient) -> dict[str, Any]:
    errors: list[str] = []
    for code, display_name in US_FUTURES:
        try:
            frame = client.quote_call_raw("get_market_snapshot", args=[[code]])
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                raise CrossMarketError("empty snapshot")
            record = {str(key): value for key, value in frame.iloc[0].to_dict().items()}
            last = _finite_float(record.get("last_price"))
            previous = _finite_float(record.get("prev_close_price"))
            if last is None or last <= 0:
                raise CrossMarketError("snapshot has no positive last_price")
            change_pct = (
                (last / previous - 1.0) * 100.0
                if previous is not None and previous > 0
                else None
            )
            name = str(record.get("name") or display_name).strip()
            as_of = str(record.get("update_time") or "").strip() or None
            return {
                "name": name,
                "contract": code,
                "last": last,
                "change_pct": round(change_pct, 4) if change_pct is not None else None,
                "as_of": as_of,
                "source": "futu",
            }
        except Exception as exc:
            errors.append(f"{code}: {_error_note(exc)}")
    if errors and all("行情权限不足" in error for error in errors):
        contracts = "/".join(code for code, _name in US_FUTURES)
        raise CrossMarketError(f"{contracts} 均无行情权限")
    raise CrossMarketError("; ".join(errors))


def fetch_commodity_index() -> dict[str, Any]:
    response = httpx.get(
        CCIDX_URL,
        params={"indexId": "100001.CCI"},
        headers={"User-Agent": "Mozilla/5.0 (compatible; AlphaPilot-AI/0.2)"},
        timeout=10.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise CrossMarketError("CCIDX response is not an object")
    data = payload.get("data")
    if not isinstance(data, Mapping) or not isinstance(data.get("dateLineJson"), list):
        raise CrossMarketError("CCIDX response has no dateLineJson")

    points: list[tuple[pd.Timestamp, Mapping[str, Any]]] = []
    for raw in data["dateLineJson"]:
        if not isinstance(raw, Mapping):
            continue
        date_value = raw.get("tradeDate")
        if date_value is None:
            continue
        parsed_date = pd.to_datetime(str(date_value), errors="coerce")
        if pd.isna(parsed_date):
            continue
        points.append((pd.Timestamp(parsed_date), raw))
    points.sort(key=lambda item: item[0])
    if not points:
        raise CrossMarketError("CCIDX response has no usable observations")

    latest_date, latest = points[-1]
    last = _finite_float(latest.get("closingPrice"))
    change_pct = _finite_float(latest.get("dailyIncreaseAndDecreasePercentageClose"))
    if last is None or last <= 0:
        raise CrossMarketError("CCIDX latest observation has no closing price")
    return {
        "name": "中证商品期货指数",
        "last": last,
        "change_pct": change_pct,
        "as_of": latest_date.date().isoformat(),
        "source": "ccidx",
    }


def cross_market_snapshot(client: FutuClient) -> dict[str, Any]:
    """Build a complete response while isolating every external data failure."""

    try:
        fx_usdcny = fetch_fx_usdcny()
    except Exception as exc:
        fx_usdcny = {
            "value": None,
            "change_pct": None,
            "as_of": None,
            "source": None,
            "note": f"人民币中间价不可用：{_error_note(exc)}",
        }

    try:
        us_futures = fetch_us_futures(client)
    except Exception as exc:
        us_futures = {
            "name": "标普500期指",
            "contract": "US.ESmain",
            "last": None,
            "change_pct": None,
            "as_of": None,
            "source": None,
            "note": f"富途美期行情不可用：{_error_note(exc)}",
        }

    try:
        commodities = fetch_commodity_index()
    except Exception as exc:
        commodities = {
            "name": "中证商品期货指数",
            "last": None,
            "change_pct": None,
            "as_of": None,
            "source": None,
            "note": f"商品指数不可用：{_error_note(exc)}",
        }

    return {
        "fx_usdcny": fx_usdcny,
        "us_futures": us_futures,
        "commodities": commodities,
        "northbound": {
            "daily_balance": None,
            "as_of": None,
            "source": None,
            "note": "盘中数据官方已停发；当前没有已验证的可靠日度余额源。",
        },
    }
