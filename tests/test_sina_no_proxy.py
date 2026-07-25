from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx
import pandas as pd
import pytest

from alphapilot.data.sina_provider import SinaDailyBarProvider


def _daily_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": date(2026, 7, 24),
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 1000,
                "amount": 10000,
            }
        ]
    )


def test_sina_daily_akshare_call_ignores_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            captured["client_kwargs"] = kwargs

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str, **kwargs: Any) -> httpx.Response:
            captured["url"] = url
            captured["get_kwargs"] = kwargs
            return httpx.Response(
                200,
                text="direct",
                request=httpx.Request("GET", url),
            )

    def stock_zh_a_daily(**_kwargs: str) -> pd.DataFrame:
        response = globals()["requests"].get(
            "https://finance.sina.com.cn/direct-test"
        )
        assert response.text == "direct"
        return _daily_frame()

    module = SimpleNamespace(stock_zh_a_daily=stock_zh_a_daily)

    provider = SinaDailyBarProvider(min_interval_seconds=0)
    monkeypatch.setattr(provider, "_module", lambda: module)
    monkeypatch.setattr(httpx, "Client", Client)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:58591")

    frame = provider.get_daily_bars(
        "920000",
        date(2026, 7, 24),
        date(2026, 7, 24),
    )

    assert captured["client_kwargs"] == {
        "timeout": 10.0,
        "follow_redirects": True,
        "trust_env": False,
    }
    assert captured["url"] == "https://finance.sina.com.cn/direct-test"
    assert captured["get_kwargs"] == {}
    assert frame.iloc[0]["date"].date() == date(2026, 7, 24)
    assert stock_zh_a_daily.__globals__.get("requests") is None


def test_sina_snapshot_ignores_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    payload = (
        'var hq_str_bj920000="安徽凤凰,11.350,11.320,11.710,11.760,10.700,'
        "11.700,11.710,1056462,11863548.780,0,0,0,0,0,0,0,0,0,0,0,0,0,0,"
        '0,0,0,0,0,0,2026-07-24,15:30:02,00";\n'
    ).encode("gb18030")

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return httpx.Response(
            200,
            content=payload,
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:58591")

    frame = SinaDailyBarProvider(min_interval_seconds=0).get_snapshot(["920000"])

    assert captured["url"].endswith("list=bj920000")
    assert captured["kwargs"]["timeout"] == 5.0
    assert captured["kwargs"]["trust_env"] is False
    assert frame.iloc[0]["symbol"] == "920000"
