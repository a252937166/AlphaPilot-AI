"""BaoStock egress failover, health markers and the per-egress daily request budget."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alphapilot.core.config import get_settings
from alphapilot.data import baostock_provider as provider
from alphapilot.data.base import DataProviderError


class _Result:
    def __init__(self, code: str, message: str = "") -> None:
        self.error_code = code
        self.error_msg = message


class _Module:
    """Records which egress each login attempt used; query_* calls return a fixed result."""

    def __init__(self) -> None:
        self.logins: list[str] = []
        self.queries = 0

    def logout(self) -> _Result:
        return _Result("0")

    def query_probe(self, *_args: object, **_kwargs: object) -> _Result:
        self.queries += 1
        return _Result("0")


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Module:
    monkeypatch.setenv(provider._PROCESS_LOCK_ENV, str(tmp_path / "state" / "baostock.lock"))
    monkeypatch.setattr(provider, "_logged_in", False)
    monkeypatch.setattr(provider, "_active_module", None)
    monkeypatch.setattr(provider, "_process_lock_handle", None)
    monkeypatch.setattr(provider, "_active_used_scopes", set())
    monkeypatch.setattr(provider, "_current_egress", None)
    monkeypatch.setattr(provider, "sleep", lambda _seconds: None)
    settings = get_settings()
    monkeypatch.setattr(settings, "baostock_socks5_proxy", "127.0.0.1:1081")
    monkeypatch.setattr(settings, "baostock_egress", "auto")
    monkeypatch.setattr(settings, "baostock_daily_request_budget", 45000)
    monkeypatch.delenv(provider._SOCKS5_PROXY_ENV, raising=False)
    monkeypatch.setattr(provider, "_default_socket_failure", lambda: None)
    monkeypatch.setattr(provider, "_discard_default_socket", lambda: None)
    return _Module()


def _script(module: _Module, outcomes: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_login(bs: object, proxy: tuple[str, int] | None) -> _Result:
        egress = "proxy" if proxy is not None else "direct"
        module.logins.append(egress)
        code = outcomes[egress]
        return _Result(code, "黑名单用户，请与管理员联系" if code == "10001011" else "")

    monkeypatch.setattr(provider, "_login_with_bounded_socket", fake_login)


def test_egress_order_follows_settings(isolated: _Module, monkeypatch: pytest.MonkeyPatch) -> None:
    assert provider._egress_order() == ["direct", "proxy"]
    monkeypatch.setattr(get_settings(), "baostock_egress", "proxy")
    assert provider._egress_order() == ["proxy"]
    monkeypatch.setattr(get_settings(), "baostock_egress", "direct")
    assert provider._egress_order() == ["direct"]
    monkeypatch.setattr(get_settings(), "baostock_socks5_proxy", None)
    monkeypatch.setattr(get_settings(), "baostock_egress", "auto")
    assert provider._egress_order() == ["direct"]
    monkeypatch.setattr(get_settings(), "baostock_egress", "proxy")
    with pytest.raises(DataProviderError, match="no SOCKS5 proxy"):
        provider._egress_order()


def test_blacklisted_direct_falls_back_to_proxy_and_stays_there(
    isolated: _Module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = isolated
    _script(module, {"direct": "10001011", "proxy": "0"}, monkeypatch)
    api = provider.BaoStockMarketDataProvider()
    api._ensure_login(module)
    assert module.logins == ["direct", "proxy"]
    assert provider._current_egress == "proxy"
    marker = json.loads(provider._health_path("direct").read_text(encoding="utf-8"))
    assert "10001011" in marker["reason"]
    assert provider._egress_unhealthy("direct") is not None
    assert provider._egress_unhealthy("proxy") is None
    # a new session skips the sidelined direct path without trying it again
    provider._invalidate_baostock_session_locked()
    api._ensure_login(module)
    assert module.logins == ["direct", "proxy", "proxy"]
    provider._close_baostock_session_locked()


def test_all_egresses_failing_raises_with_both_reasons(
    isolated: _Module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = isolated
    _script(module, {"direct": "10002007", "proxy": "10002007"}, monkeypatch)
    api = provider.BaoStockMarketDataProvider()
    with pytest.raises(DataProviderError, match="every egress"):
        api._ensure_login(module)
    assert module.logins == ["direct", "direct", "proxy", "proxy"]  # two attempts each
    assert provider._logged_in is False


def test_transport_failure_sidelines_the_active_egress(isolated: _Module) -> None:
    provider._current_egress = "direct"
    with (
        pytest.raises(DataProviderError, match="transport failed"),
        provider._baostock_locked("probe"),
    ):
        raise TimeoutError("timed out")
    reason = provider._egress_unhealthy("direct")
    assert reason is not None and "timed out" in reason
    assert provider._egress_unhealthy("proxy") is None


def test_daily_budget_is_charged_per_egress_and_stops_at_the_limit(
    isolated: _Module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = isolated
    monkeypatch.setattr(get_settings(), "baostock_daily_request_budget", 2)
    provider._current_egress = "direct"
    counting = provider._CountingModule(module)
    assert counting.query_probe().error_code == "0"
    assert counting.query_probe().error_code == "0"
    assert provider._budget_used("direct") == 2 and provider._budget_used("proxy") == 0
    with pytest.raises(provider.BaoStockRequestBudgetExceeded, match="egress direct"):
        counting.query_probe()
    assert module.queries == 2  # the third request never reached BaoStock
    assert "budget" in (provider._egress_unhealthy("direct") or "")
    # login now skips the exhausted egress and uses the other one
    _script(module, {"direct": "0", "proxy": "0"}, monkeypatch)
    api = provider.BaoStockMarketDataProvider()
    api._ensure_login(module)
    assert module.logins == ["proxy"] and provider._current_egress == "proxy"
    provider._close_baostock_session_locked()


def test_blacklist_code_on_a_query_result_sidelines_the_egress(isolated: _Module) -> None:
    provider._current_egress = "proxy"
    provider._invalidate_failed_result_locked(_Result("10001011", "黑名单用户"))
    assert "blacklisted" in (provider._egress_unhealthy("proxy") or "")
    assert provider._logged_in is False


def test_local_network_errors_sideline_only_briefly(isolated: _Module) -> None:
    provider._mark_egress_unhealthy(
        "direct", "login gaierror: [Errno 8] nodename nor servname provided, or not known"
    )
    provider._mark_egress_unhealthy("proxy", "transport: timed out")
    direct = provider._egress_sideline("direct")
    proxy = provider._egress_sideline("proxy")
    assert direct is not None and direct[0] - provider.time() <= 121
    assert proxy is not None and proxy[0] - provider.time() >= 9 * 60


def test_login_waits_for_a_short_sideline_instead_of_failing(
    isolated: _Module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = isolated
    clock = {"now": 1_000_000.0}
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(provider, "time", lambda: clock["now"])
    monkeypatch.setattr(provider, "sleep", fake_sleep)
    provider._mark_egress_unhealthy("direct", "login gaierror: [Errno 8] nodename", 30)
    provider._mark_egress_unhealthy("proxy", "login ConnectionRefusedError: [Errno 61]", 60)
    _script(module, {"direct": "0", "proxy": "0"}, monkeypatch)
    api = provider.BaoStockMarketDataProvider()
    api._ensure_login(module)
    assert module.logins == ["direct"] and provider._current_egress == "direct"
    assert slept == [31.0]
    provider._close_baostock_session_locked()


def test_login_does_not_wait_for_a_long_sideline(
    isolated: _Module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = isolated
    slept: list[float] = []
    monkeypatch.setattr(provider, "sleep", slept.append)
    provider._mark_egress_unhealthy("direct", "transport: timed out")  # six hours
    provider._mark_egress_unhealthy("proxy", "transport: timed out", 20 * 60)  # beyond the cap
    _script(module, {"direct": "0", "proxy": "0"}, monkeypatch)
    api = provider.BaoStockMarketDataProvider()
    with pytest.raises(DataProviderError, match="sidelined"):
        api._ensure_login(module)
    assert module.logins == [] and slept == []


def test_login_waits_once_then_gives_up_when_the_sideline_persists(
    isolated: _Module, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = isolated
    slept: list[float] = []
    monkeypatch.setattr(provider, "sleep", slept.append)  # no time passes: markers stay active
    provider._mark_egress_unhealthy("direct", "transport: timed out")  # six hours
    provider._mark_egress_unhealthy("proxy", "transport: timed out")  # ten minutes
    _script(module, {"direct": "0", "proxy": "0"}, monkeypatch)
    api = provider.BaoStockMarketDataProvider()
    with pytest.raises(DataProviderError, match="sidelined"):
        api._ensure_login(module)
    assert module.logins == []
    assert len(slept) == 1 and 599 <= slept[0] <= 602
