"""The BaoStock SOCKS5 egress can come from settings (.env) as well as the process environment."""

from __future__ import annotations

import pytest

from alphapilot.core.config import get_settings
from alphapilot.data import baostock_provider as provider


def test_socks5_endpoint_falls_back_to_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(provider._SOCKS5_PROXY_ENV, raising=False)
    monkeypatch.setattr(get_settings(), "baostock_socks5_proxy", None)
    assert provider._socks5_endpoint() is None
    monkeypatch.setattr(get_settings(), "baostock_socks5_proxy", "127.0.0.1:1081")
    assert provider._socks5_endpoint() == ("127.0.0.1", 1081)
    # the process variable still wins over the setting
    monkeypatch.setenv(provider._SOCKS5_PROXY_ENV, "10.0.0.2:1080")
    assert provider._socks5_endpoint() == ("10.0.0.2", 1080)
    monkeypatch.setenv(provider._SOCKS5_PROXY_ENV, "not-a-proxy")
    with pytest.raises(provider.DataProviderError):
        provider._socks5_endpoint()
