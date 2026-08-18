from __future__ import annotations

import errno
import struct
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Event, Thread
from typing import ClassVar

import httpx
import pandas as pd
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from alphapilot.api.dependencies import baostock_session_dependency
from alphapilot.core.config import Settings
from alphapilot.data import baostock_provider
from alphapilot.data.akshare_provider import AKShareMarketDataProvider
from alphapilot.data.baostock_provider import (
    BaoStockMarketDataProvider,
    BaoStockRequestBudgetExceeded,
)
from alphapilot.data.base import DataProviderError, EmptyDailyBarsError
from alphapilot.data.mock import MockMarketDataProvider
from alphapilot.data.router import FailoverMarketDataProvider
from alphapilot.data.sina_provider import SinaDailyBarProvider


class BrokenProvider:
    name = "broken"

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        raise DataProviderError("upstream down")

    def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        raise DataProviderError("upstream down")


class ScopedBaoStockModule:
    login_calls = 0
    logout_calls = 0

    class Result:
        error_code = "0"
        error_msg = ""

    @classmethod
    def login(cls) -> Result:
        cls.login_calls += 1
        return cls.Result()

    @classmethod
    def logout(cls) -> Result:
        cls.logout_calls += 1
        return cls.Result()


def _prepare_scoped_baostock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> BaoStockMarketDataProvider:
    ScopedBaoStockModule.login_calls = 0
    ScopedBaoStockModule.logout_calls = 0
    monkeypatch.setattr(baostock_provider, "_logged_in", False)
    monkeypatch.setattr(baostock_provider, "_active_module", None)
    monkeypatch.setattr(baostock_provider, "_process_lock_handle", None)
    monkeypatch.setattr(baostock_provider, "_active_used_scopes", set())
    monkeypatch.setenv(
        "ALPHAPILOT_BAOSTOCK_LOCK_FILE",
        str(tmp_path / "scoped-baostock.lock"),
    )
    return BaoStockMarketDataProvider()


def _touch_scoped_baostock(provider: BaoStockMarketDataProvider) -> None:
    with baostock_provider._baostock_locked("test login"):
        provider._ensure_login(ScopedBaoStockModule)


def test_baostock_symbol_normalization() -> None:
    assert BaoStockMarketDataProvider._code("600000") == "sh.600000"
    assert BaoStockMarketDataProvider._code("000333") == "sz.000333"
    assert BaoStockMarketDataProvider._code("SH.000001") == "sh.000001"
    assert BaoStockMarketDataProvider._code("sz.399006".upper()) == "sz.399006"
    with pytest.raises(DataProviderError):
        BaoStockMarketDataProvider._code("HK.00700")


def test_baostock_distinguishes_empty_bars_from_query_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        error_code = "0"
        error_msg = ""

        def next(self) -> bool:
            return False

    class BaoStockModule:
        result = Result()

        @classmethod
        def query_history_k_data_plus(cls, *_args: object, **_kwargs: object) -> Result:
            return cls.result

    provider = BaoStockMarketDataProvider()
    monkeypatch.setattr("alphapilot.data.baostock_provider._logged_in", True)
    monkeypatch.setattr(provider, "_module", lambda: BaoStockModule)

    with pytest.raises(EmptyDailyBarsError, match="returned no daily bars"):
        provider.get_daily_bars("600000", date(2026, 7, 20), date(2026, 7, 20))

    BaoStockModule.result.error_code = "1"
    BaoStockModule.result.error_msg = "upstream unavailable"
    with pytest.raises(DataProviderError, match="query failed") as caught:
        provider.get_daily_bars("600000", date(2026, 7, 20), date(2026, 7, 20))
    assert not isinstance(caught.value, EmptyDailyBarsError)


def test_baostock_quarterly_financials_queries_all_datasets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        error_code = "0"
        error_msg = ""

        def __init__(self, fields: list[str], rows: list[list[str]]) -> None:
            self.fields = fields
            self._rows = iter(rows)
            self._current: list[str] = []

        def next(self) -> bool:
            try:
                self._current = next(self._rows)
            except StopIteration:
                return False
            return True

        def get_row_data(self) -> list[str]:
            return self._current

    class BaoStockModule:
        calls: ClassVar[list[tuple[str, dict[str, object]]]] = []

        @classmethod
        def _query(cls, dataset: str, kwargs: dict[str, object]) -> Result:
            cls.calls.append((dataset, kwargs))
            if dataset == "cash_flow":
                return Result(["code", "statDate", "CFOToNP"], [])
            return Result(
                ["code", "statDate", "value"],
                [["sh.600519", "2025-12-31", dataset]],
            )

        @classmethod
        def query_profit_data(cls, **kwargs: object) -> Result:
            return cls._query("profit", kwargs)

        @classmethod
        def query_growth_data(cls, **kwargs: object) -> Result:
            return cls._query("growth", kwargs)

        @classmethod
        def query_cash_flow_data(cls, **kwargs: object) -> Result:
            return cls._query("cash_flow", kwargs)

        @classmethod
        def query_balance_data(cls, **kwargs: object) -> Result:
            return cls._query("balance", kwargs)

    provider = BaoStockMarketDataProvider()
    monkeypatch.setattr("alphapilot.data.baostock_provider._logged_in", True)
    monkeypatch.setattr(provider, "_module", lambda: BaoStockModule)

    frames = provider.get_quarterly_financials("600519", 2025, 4)

    assert list(frames) == ["profit", "growth", "cash_flow", "balance"]
    assert frames["profit"].iloc[0]["value"] == "profit"
    assert frames["growth"].iloc[0]["value"] == "growth"
    assert frames["cash_flow"].empty
    assert list(frames["cash_flow"].columns) == ["code", "statDate", "CFOToNP"]
    assert frames["balance"].iloc[0]["value"] == "balance"
    assert BaoStockModule.calls == [
        (dataset, {"code": "sh.600519", "year": 2025, "quarter": 4})
        for dataset in ["profit", "growth", "cash_flow", "balance"]
    ]

    BaoStockModule.calls.clear()
    profit = provider.get_quarterly_profit("600519", 2024, 4)
    assert profit.iloc[0]["value"] == "profit"
    assert BaoStockModule.calls == [("profit", {"code": "sh.600519", "year": 2024, "quarter": 4})]


def test_baostock_quarterly_financials_surfaces_query_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        error_code = "1"
        error_msg = "upstream unavailable"
        fields: ClassVar[tuple[str, ...]] = ()

    class BaoStockModule:
        @staticmethod
        def query_profit_data(**_kwargs: object) -> Result:
            return Result()

    provider = BaoStockMarketDataProvider()
    monkeypatch.setattr("alphapilot.data.baostock_provider._logged_in", True)
    monkeypatch.setattr(provider, "_module", lambda: BaoStockModule)

    with pytest.raises(
        DataProviderError,
        match=r"profit query failed for sh\.600519/2025Q4: \(1\) upstream unavailable",
    ):
        provider.get_quarterly_financials("600519", 2025, 4)


def test_baostock_quarterly_financials_relogs_once_after_expired_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Result:
        fields: ClassVar[tuple[str, str]] = ("code", "statDate")

        def __init__(self, error_code: str = "0", error_msg: str = "") -> None:
            self.error_code = error_code
            self.error_msg = error_msg
            self._pending = error_code == "0"

        def next(self) -> bool:
            if self._pending:
                self._pending = False
                return True
            return False

        @staticmethod
        def get_row_data() -> list[str]:
            return ["sh.600519", "2025-12-31"]

    class BaoStockModule:
        login_calls = 0
        query_calls: ClassVar[dict[str, int]] = {
            "profit": 0,
            "growth": 0,
            "cash_flow": 0,
            "balance": 0,
        }

        @classmethod
        def login(cls) -> Result:
            cls.login_calls += 1
            return Result()

        @classmethod
        def _query(cls, dataset: str) -> Result:
            cls.query_calls[dataset] += 1
            if dataset == "profit" and cls.query_calls[dataset] == 1:
                return Result("10001001", "用户未登录")
            return Result()

        @classmethod
        def query_profit_data(cls, **_kwargs: object) -> Result:
            return cls._query("profit")

        @classmethod
        def query_growth_data(cls, **_kwargs: object) -> Result:
            return cls._query("growth")

        @classmethod
        def query_cash_flow_data(cls, **_kwargs: object) -> Result:
            return cls._query("cash_flow")

        @classmethod
        def query_balance_data(cls, **_kwargs: object) -> Result:
            return cls._query("balance")

    provider = BaoStockMarketDataProvider()
    monkeypatch.setattr("alphapilot.data.baostock_provider._logged_in", True)
    monkeypatch.setattr("alphapilot.data.baostock_provider._process_lock_handle", None)
    monkeypatch.setenv(
        "ALPHAPILOT_BAOSTOCK_LOCK_FILE",
        str(tmp_path / "baostock-relogin.lock"),
    )
    monkeypatch.setattr(provider, "_module", lambda: BaoStockModule)

    frames = provider.get_quarterly_financials("600519", 2025, 4)

    assert all(not frame.empty for frame in frames.values())
    assert BaoStockModule.login_calls == 1
    assert BaoStockModule.query_calls == {
        "profit": 2,
        "growth": 1,
        "cash_flow": 1,
        "balance": 1,
    }


def test_baostock_quarterly_financials_reconnects_once_after_transport_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Result:
        fields: ClassVar[tuple[str, str]] = ("code", "statDate")

        def __init__(self, error_code: str = "0", error_msg: str = "") -> None:
            self.error_code = error_code
            self.error_msg = error_msg
            self._pending = error_code == "0"

        def next(self) -> bool:
            if self._pending:
                self._pending = False
                return True
            return False

        @staticmethod
        def get_row_data() -> list[str]:
            return ["sh.600519", "2025-12-31"]

    class BaoStockModule:
        login_calls = 0
        query_calls: ClassVar[dict[str, int]] = {
            "profit": 0,
            "growth": 0,
            "cash_flow": 0,
            "balance": 0,
        }

        @classmethod
        def login(cls) -> Result:
            cls.login_calls += 1
            return Result()

        @classmethod
        def _query(cls, dataset: str) -> Result:
            cls.query_calls[dataset] += 1
            if dataset == "profit" and cls.query_calls[dataset] == 1:
                return Result("10002007", "网络接收错误")
            return Result()

        @classmethod
        def query_profit_data(cls, **_kwargs: object) -> Result:
            return cls._query("profit")

        @classmethod
        def query_growth_data(cls, **_kwargs: object) -> Result:
            return cls._query("growth")

        @classmethod
        def query_cash_flow_data(cls, **_kwargs: object) -> Result:
            return cls._query("cash_flow")

        @classmethod
        def query_balance_data(cls, **_kwargs: object) -> Result:
            return cls._query("balance")

    provider = BaoStockMarketDataProvider()
    monkeypatch.setattr("alphapilot.data.baostock_provider._logged_in", True)
    monkeypatch.setattr("alphapilot.data.baostock_provider._process_lock_handle", None)
    monkeypatch.setenv(
        "ALPHAPILOT_BAOSTOCK_LOCK_FILE",
        str(tmp_path / "baostock-reconnect.lock"),
    )
    monkeypatch.setattr(provider, "_module", lambda: BaoStockModule)

    frames = provider.get_quarterly_financials("600519", 2025, 4)

    assert all(not frame.empty for frame in frames.values())
    assert BaoStockModule.login_calls == 1
    assert BaoStockModule.query_calls == {
        "profit": 2,
        "growth": 1,
        "cash_flow": 1,
        "balance": 1,
    }


def test_baostock_quarterly_financials_validates_period_and_rejects_bse() -> None:
    provider = BaoStockMarketDataProvider()

    with pytest.raises(ValueError, match="year/quarter is out of range"):
        provider.get_quarterly_financials("600519", 2025, 5)
    with pytest.raises(ValueError, match="year/quarter is out of range"):
        provider.get_quarterly_financials("600519", 1989, 4)
    with pytest.raises(DataProviderError, match="do not support symbol: 920000"):
        provider.get_quarterly_financials("920000", 2025, 4)


def test_baostock_blacklist_login_fails_once_and_preserves_error_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Result:
        error_code = "10001011"
        error_msg = "黑名单用户，请与管理员联系"

    class BaoStockModule:
        login_calls = 0

        @classmethod
        def login(cls) -> Result:
            cls.login_calls += 1
            return Result()

    provider = BaoStockMarketDataProvider()
    monkeypatch.setattr("alphapilot.data.baostock_provider._logged_in", False)
    monkeypatch.setattr("alphapilot.data.baostock_provider._active_module", None)
    monkeypatch.setattr("alphapilot.data.baostock_provider._process_lock_handle", None)
    monkeypatch.setenv(
        "ALPHAPILOT_BAOSTOCK_LOCK_FILE",
        str(tmp_path / "baostock.lock"),
    )
    monkeypatch.setattr("alphapilot.data.baostock_provider.sleep", pytest.fail)

    with pytest.raises(DataProviderError, match=r"10001011.*黑名单用户"):
        provider._ensure_login(BaoStockModule)

    assert BaoStockModule.login_calls == 1
    assert baostock_provider._process_lock_handle is None


def test_baostock_process_lock_refuses_second_local_connection_before_login(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class BaoStockModule:
        login_calls = 0

        @classmethod
        def login(cls) -> object:
            cls.login_calls += 1
            raise AssertionError("login must not run while the host lock is held")

    def locked(*_args: object) -> None:
        raise BlockingIOError(errno.EAGAIN, "resource temporarily unavailable")

    monkeypatch.setattr(baostock_provider, "_logged_in", False)
    monkeypatch.setattr(baostock_provider, "_active_module", None)
    monkeypatch.setattr(baostock_provider, "_process_lock_handle", None)
    monkeypatch.setenv(
        "ALPHAPILOT_BAOSTOCK_LOCK_FILE",
        str(tmp_path / "baostock.lock"),
    )
    monkeypatch.setattr(baostock_provider.fcntl, "flock", locked)

    with pytest.raises(DataProviderError, match="held by another local process"):
        BaoStockMarketDataProvider()._ensure_login(BaoStockModule)

    assert BaoStockModule.login_calls == 0
    assert baostock_provider._process_lock_handle is None


def test_baostock_successful_login_retains_host_lock_until_explicit_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Result:
        error_code = "0"
        error_msg = ""

    class BaoStockModule:
        login_calls = 0
        logout_calls = 0

        @classmethod
        def login(cls) -> Result:
            cls.login_calls += 1
            return Result()

        @classmethod
        def logout(cls) -> Result:
            cls.logout_calls += 1
            return Result()

    lock_path = tmp_path / "baostock.lock"
    monkeypatch.setattr(baostock_provider, "_logged_in", False)
    monkeypatch.setattr(baostock_provider, "_active_module", None)
    monkeypatch.setattr(baostock_provider, "_process_lock_handle", None)
    monkeypatch.setenv("ALPHAPILOT_BAOSTOCK_LOCK_FILE", str(lock_path))

    try:
        BaoStockMarketDataProvider()._ensure_login(BaoStockModule)
        assert BaoStockModule.login_calls == 1
        assert baostock_provider._process_lock_handle is not None
        assert lock_path.exists()
    finally:
        baostock_provider.close_baostock_session()

    assert BaoStockModule.logout_calls == 1
    assert baostock_provider._process_lock_handle is None


def test_baostock_concurrent_used_scopes_close_only_after_last_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _prepare_scoped_baostock(monkeypatch, tmp_path)
    session_scope = baostock_provider.baostock_session_scope
    last_scope_ready = Event()
    allow_last_scope_exit = Event()

    def hold_last_scope() -> None:
        with session_scope():
            _touch_scoped_baostock(provider)
            last_scope_ready.set()
            assert allow_last_scope_exit.wait(timeout=5)

    def exit_first_scope() -> None:
        assert last_scope_ready.wait(timeout=5)
        with session_scope():
            _touch_scoped_baostock(provider)

    with ThreadPoolExecutor(max_workers=2) as executor:
        last = executor.submit(hold_last_scope)
        assert last_scope_ready.wait(timeout=5)
        first = executor.submit(exit_first_scope)
        first.result(timeout=5)

        assert baostock_provider._logged_in is True
        assert baostock_provider._process_lock_handle is not None
        assert ScopedBaoStockModule.logout_calls == 0

        allow_last_scope_exit.set()
        last.result(timeout=5)

    assert ScopedBaoStockModule.login_calls == 1
    assert ScopedBaoStockModule.logout_calls == 0
    assert baostock_provider._logged_in is False
    assert baostock_provider._process_lock_handle is None


def test_baostock_nested_used_scopes_close_only_after_outer_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _prepare_scoped_baostock(monkeypatch, tmp_path)
    session_scope = baostock_provider.baostock_session_scope

    with session_scope():
        _touch_scoped_baostock(provider)
        with session_scope():
            _touch_scoped_baostock(provider)

        assert baostock_provider._logged_in is True
        assert baostock_provider._process_lock_handle is not None
        assert ScopedBaoStockModule.logout_calls == 0

    assert ScopedBaoStockModule.login_calls == 1
    assert ScopedBaoStockModule.logout_calls == 0
    assert baostock_provider._logged_in is False
    assert baostock_provider._process_lock_handle is None


def test_baostock_unused_scope_does_not_close_an_active_used_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _prepare_scoped_baostock(monkeypatch, tmp_path)
    session_scope = baostock_provider.baostock_session_scope
    used_scope_ready = Event()
    allow_used_scope_exit = Event()

    def hold_used_scope() -> None:
        with session_scope():
            _touch_scoped_baostock(provider)
            used_scope_ready.set()
            assert allow_used_scope_exit.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        used = executor.submit(hold_used_scope)
        assert used_scope_ready.wait(timeout=5)

        with session_scope():
            pass

        assert baostock_provider._logged_in is True
        assert baostock_provider._process_lock_handle is not None
        assert ScopedBaoStockModule.logout_calls == 0

        allow_used_scope_exit.set()
        used.result(timeout=5)

    assert ScopedBaoStockModule.logout_calls == 0
    assert baostock_provider._logged_in is False
    assert baostock_provider._process_lock_handle is None


def test_baostock_async_dependency_tracks_sync_handler_and_closes_after_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _prepare_scoped_baostock(monkeypatch, tmp_path)
    observed: dict[str, object] = {}

    api = FastAPI(dependencies=[Depends(baostock_session_dependency)])

    @api.get("/touch-baostock")
    def touch_baostock() -> dict[str, bool]:
        marker = baostock_provider._current_session_scope.get()
        observed["marker_present"] = marker is not None
        _touch_scoped_baostock(provider)
        observed["marker_used"] = marker is not None and marker.used
        observed["lock_held"] = baostock_provider._process_lock_handle is not None
        return {"ok": True}

    with TestClient(api) as client:
        response = client.get("/touch-baostock")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert observed == {
        "marker_present": True,
        "marker_used": True,
        "lock_held": True,
    }
    assert ScopedBaoStockModule.login_calls == 1
    assert ScopedBaoStockModule.logout_calls == 0
    assert baostock_provider._logged_in is False
    assert baostock_provider._process_lock_handle is None


def test_baostock_async_dependency_finalizer_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _prepare_scoped_baostock(monkeypatch, tmp_path)
    start_holder = Event()
    lock_held = Event()
    release_lock = Event()
    scoped_result: dict[str, object] = {}
    ping_result: dict[str, object] = {}

    api = FastAPI(dependencies=[Depends(baostock_session_dependency)])

    @api.get("/scoped")
    def scoped() -> dict[str, bool]:
        _touch_scoped_baostock(provider)
        start_holder.set()
        assert lock_held.wait(timeout=5)
        return {"ok": True}

    @api.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    def hold_provider_lock() -> None:
        assert start_holder.wait(timeout=5)
        with baostock_provider._baostock_locked("held test operation"):
            lock_held.set()
            assert release_lock.wait(timeout=5)

    holder = Thread(target=hold_provider_lock)
    holder.start()
    try:
        with TestClient(api) as client:
            scoped_request = Thread(
                target=lambda: scoped_result.setdefault("response", client.get("/scoped"))
            )
            scoped_request.start()
            assert lock_held.wait(timeout=5)

            ping_request = Thread(
                target=lambda: ping_result.setdefault("response", client.get("/ping"))
            )
            ping_request.start()
            ping_request.join(timeout=0.5)

            assert not ping_request.is_alive()
            ping_response = ping_result["response"]
            assert isinstance(ping_response, httpx.Response)
            assert ping_response.status_code == 200
            assert ping_response.json() == {"ok": True}

            release_lock.set()
            scoped_request.join(timeout=5)
            assert not scoped_request.is_alive()
            scoped_response = scoped_result["response"]
            assert isinstance(scoped_response, httpx.Response)
            assert scoped_response.status_code == 200
    finally:
        release_lock.set()
        holder.join(timeout=5)

    assert not holder.is_alive()
    assert baostock_provider._logged_in is False
    assert baostock_provider._process_lock_handle is None


def test_baostock_lock_timeout_fails_over_without_exhausting_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = BaoStockMarketDataProvider()
    akshare = AKShareMarketDataProvider()
    fallback = MockMarketDataProvider()
    router = FailoverMarketDataProvider(
        bars_chain=[provider, akshare, fallback],
        snapshot_chain=[fallback],
    )
    lock_held = Event()
    release_lock = Event()

    def hold_provider_lock() -> None:
        with baostock_provider._baostock_locked("held test operation"):
            lock_held.set()
            assert release_lock.wait(timeout=5)

    class TimedOutAKShareModule:
        @staticmethod
        def stock_zh_a_hist(**kwargs: object) -> pd.DataFrame:
            assert kwargs["timeout"] == 3.0
            raise TimeoutError("Eastmoney timed out")

    monkeypatch.setenv("ALPHAPILOT_BAOSTOCK_LOCK_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setattr(akshare, "_module", lambda: TimedOutAKShareModule)
    holder = Thread(target=hold_provider_lock)
    holder.start()
    try:
        assert lock_held.wait(timeout=5)
        frame = router.get_daily_bars("600000", date(2026, 1, 1), date(2026, 7, 1))
    finally:
        release_lock.set()
        holder.join(timeout=5)

    assert not frame.empty
    assert router.last_bars_source == "mock"
    assert any("lock timed out" in error for error in router.last_errors)
    assert any("Eastmoney timed out" in error for error in router.last_errors)


def test_baostock_pagination_transport_error_invalidates_and_fails_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PartialResult:
        error_code = "0"
        error_msg = ""
        _step = 0

        @classmethod
        def next(cls) -> bool:
            cls._step += 1
            if cls._step == 1:
                return True
            cls.error_code = "10002007"
            cls.error_msg = "网络接收错误"
            return False

        @staticmethod
        def get_row_data() -> list[str]:
            return ["2026-07-20", "10", "11", "9", "10.5", "100", "1000"]

    class BaoStockModule:
        @staticmethod
        def query_history_k_data_plus(*_args: object, **_kwargs: object) -> PartialResult:
            return PartialResult()

    provider = BaoStockMarketDataProvider()
    fallback = MockMarketDataProvider()
    router = FailoverMarketDataProvider(
        bars_chain=[provider, fallback],
        snapshot_chain=[fallback],
    )
    monkeypatch.setattr(baostock_provider, "_logged_in", True)
    monkeypatch.setattr(baostock_provider, "_active_module", BaoStockModule)
    monkeypatch.setattr(provider, "_module", lambda: BaoStockModule)

    frame = router.get_daily_bars("600000", date(2026, 1, 1), date(2026, 7, 1))

    assert not frame.empty
    assert router.last_bars_source == "mock"
    assert any("pagination failed" in error for error in router.last_errors)
    assert baostock_provider._logged_in is False
    assert baostock_provider._active_module is None


def test_baostock_vendor_swallowed_eof_invalidates_and_fails_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ClosedSocket:
        def settimeout(self, _timeout: float) -> None:
            return None

        def recv(self, _size: int) -> bytes:
            return b""

        def shutdown(self, _how: int) -> None:
            return None

        def close(self) -> None:
            return None

    guarded = baostock_provider._EOFGuardSocket(
        ClosedSocket(),
        request_timeout=3.0,
    )

    class EmptyResult:
        error_code = "0"
        error_msg = ""

        @staticmethod
        def next() -> bool:
            try:
                guarded.recv(8192)
            except ConnectionError:
                # BaoStock's vendor layer prints and swallows this exception.
                return False
            raise AssertionError("closed socket must raise")

    class BaoStockModule:
        @staticmethod
        def query_history_k_data_plus(*_args: object, **_kwargs: object) -> EmptyResult:
            return EmptyResult()

    provider = BaoStockMarketDataProvider()
    fallback = MockMarketDataProvider()
    router = FailoverMarketDataProvider(
        bars_chain=[provider, fallback],
        snapshot_chain=[fallback],
    )
    monkeypatch.setattr(baostock_provider, "_logged_in", True)
    monkeypatch.setattr(baostock_provider, "_active_module", BaoStockModule)
    monkeypatch.setattr(provider, "_module", lambda: BaoStockModule)
    monkeypatch.setattr(baostock_provider, "_default_socket_failure", lambda: guarded.failure)

    frame = router.get_daily_bars("600000", date(2026, 1, 1), date(2026, 7, 1))

    assert guarded.failure is not None
    assert not frame.empty
    assert router.last_bars_source == "mock"
    assert any("transport failed" in error for error in router.last_errors)
    assert baostock_provider._logged_in is False
    assert baostock_provider._active_module is None
    assert baostock_provider._process_lock_handle is None


def test_baostock_lock_is_released_when_scope_cleanup_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        baostock_provider,
        "_finish_completed_session_scopes_locked",
        lambda: (_ for _ in ()).throw(OSError("cleanup failed")),
    )

    with (
        pytest.raises(OSError, match="cleanup failed"),
        baostock_provider._baostock_locked("cleanup failure test"),
    ):
        pass

    assert baostock_provider._baostock_lock.acquire(blocking=False)
    baostock_provider._baostock_lock.release()


def test_baostock_financial_probe_does_not_retry_failed_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Result:
        fields: ClassVar[tuple[str, ...]] = ()

        def __init__(self, error_code: str = "0", error_msg: str = "") -> None:
            self.error_code = error_code
            self.error_msg = error_msg

        @staticmethod
        def next() -> bool:
            return False

    class BaoStockModule:
        login_calls = 0
        query_calls = 0

        @classmethod
        def login(cls) -> Result:
            cls.login_calls += 1
            return Result()

        @classmethod
        def logout(cls) -> Result:
            return Result()

        @classmethod
        def query_profit_data(cls, **_kwargs: object) -> Result:
            cls.query_calls += 1
            return Result("10002007", "网络接收错误")

    provider = BaoStockMarketDataProvider()
    monkeypatch.setattr(baostock_provider, "_logged_in", False)
    monkeypatch.setattr(baostock_provider, "_active_module", None)
    monkeypatch.setattr(baostock_provider, "_process_lock_handle", None)
    monkeypatch.setenv(
        "ALPHAPILOT_BAOSTOCK_LOCK_FILE",
        str(tmp_path / "baostock.lock"),
    )
    monkeypatch.setattr(provider, "_module", lambda: BaoStockModule)

    try:
        with pytest.raises(DataProviderError, match=r"probe failed.*10002007"):
            provider.probe_financial_query()
    finally:
        baostock_provider.close_baostock_session()

    assert BaoStockModule.login_calls == 1
    assert BaoStockModule.query_calls == 1
    assert provider.financial_query_count == 1


def test_baostock_financial_query_hard_cap_blocks_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Result:
        fields: ClassVar[tuple[str, ...]] = ()
        error_code = "0"
        error_msg = ""

        @staticmethod
        def next() -> bool:
            return False

    class BaoStockModule:
        query_calls = 0

        @staticmethod
        def login() -> Result:
            return Result()

        @staticmethod
        def logout() -> Result:
            return Result()

        @classmethod
        def query_profit_data(cls, **_kwargs: object) -> Result:
            cls.query_calls += 1
            return Result()

    provider = BaoStockMarketDataProvider()
    provider.set_financial_query_limit(1)
    monkeypatch.setattr(baostock_provider, "_logged_in", False)
    monkeypatch.setattr(baostock_provider, "_active_module", None)
    monkeypatch.setattr(baostock_provider, "_process_lock_handle", None)
    monkeypatch.setenv(
        "ALPHAPILOT_BAOSTOCK_LOCK_FILE",
        str(tmp_path / "baostock.lock"),
    )
    monkeypatch.setattr(provider, "_module", lambda: BaoStockModule)

    try:
        assert provider.probe_financial_query() == 0
        with pytest.raises(BaoStockRequestBudgetExceeded, match="used=1, limit=1"):
            provider.probe_financial_query()
    finally:
        baostock_provider.close_baostock_session()

    assert BaoStockModule.query_calls == 1
    assert provider.financial_query_count == 1


def test_baostock_socks5_connector_negotiates_domain_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self.responses = bytearray(b"\x05\x00\x05\x00\x00\x01\x7f\x00\x00\x01\x12\x34")
            self.sent: list[bytes] = []
            self.timeout: float | None = None
            self.closed = False

        def settimeout(self, timeout: float) -> None:
            self.timeout = timeout

        def sendall(self, payload: bytes) -> None:
            self.sent.append(payload)

        def recv(self, size: int) -> bytes:
            payload = bytes(self.responses[:size])
            del self.responses[:size]
            return payload

        def close(self) -> None:
            self.closed = True

    connection = FakeSocket()
    captured: dict[str, object] = {}

    def create_connection(
        endpoint: tuple[str, int],
        *,
        timeout: float,
    ) -> FakeSocket:
        captured["endpoint"] = endpoint
        captured["timeout"] = timeout
        return connection

    monkeypatch.setattr(
        baostock_provider.socket,
        "create_connection",
        create_connection,
    )

    result = baostock_provider._open_socks5_connection(
        ("127.0.0.1", 51837),
        ("public-api.baostock.com", 10030),
        timeout=12.0,
    )

    encoded_host = b"public-api.baostock.com"
    assert result is connection
    assert captured == {"endpoint": ("127.0.0.1", 51837), "timeout": 12.0}
    assert connection.timeout == 12.0
    assert connection.sent == [
        b"\x05\x01\x00",
        b"\x05\x01\x00\x03" + bytes([len(encoded_host)]) + encoded_host + struct.pack("!H", 10030),
    ]
    assert connection.responses == b""
    assert connection.closed is False


def test_baostock_direct_connector_bounds_connect_and_receive_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self.timeout: float | None = None

        def settimeout(self, timeout: float) -> None:
            self.timeout = timeout

    connection = FakeSocket()
    captured: dict[str, object] = {}

    def create_connection(
        endpoint: tuple[str, int],
        *,
        timeout: float,
    ) -> FakeSocket:
        captured["endpoint"] = endpoint
        captured["connect_timeout"] = timeout
        return connection

    monkeypatch.setattr(
        baostock_provider.socket,
        "create_connection",
        create_connection,
    )

    result = baostock_provider._open_direct_connection(
        ("public-api.baostock.com", 10030),
        timeout=9.0,
    )

    assert result is connection
    assert captured == {
        "endpoint": ("public-api.baostock.com", 10030),
        "connect_timeout": 9.0,
    }
    assert connection.timeout == 9.0


def test_baostock_default_retry_budget_finishes_before_frontend_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[float] = []
    backoffs: list[float] = []

    class Result:
        error_code = "10002008"
        error_msg = "网络接收超时"

    class BaoStockModule:
        @staticmethod
        def login() -> Result:
            attempts.append(baostock_provider._socket_timeout())
            return Result()

    monkeypatch.delenv("ALPHAPILOT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(
        baostock_provider,
        "get_settings",
        lambda: Settings(_env_file=None),
    )
    monkeypatch.setattr(baostock_provider, "_logged_in", False)
    monkeypatch.setattr(baostock_provider, "_active_module", None)
    monkeypatch.setattr(baostock_provider, "_process_lock_handle", None)
    monkeypatch.setattr(
        baostock_provider,
        "_login_with_bounded_socket",
        lambda bs, _proxy: bs.login(),
    )
    monkeypatch.setattr(baostock_provider, "sleep", backoffs.append)

    with pytest.raises(DataProviderError, match="after 2 attempts"):
        BaoStockMarketDataProvider()._ensure_login(BaoStockModule)

    assert attempts == [2.0, 2.0]
    assert backoffs == [1.0]
    # Each attempt can spend one timeout connecting and one request deadline.
    assert 2 * sum(attempts) + sum(backoffs) == 9.0
    assert baostock_provider._logged_in is False
    assert baostock_provider._process_lock_handle is None


def test_baostock_timeouts_load_from_dotenv_via_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ALPHAPILOT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS=6\n"
        "ALPHAPILOT_BAOSTOCK_LOCK_TIMEOUT_SECONDS=0.75\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=env_file)
    monkeypatch.delenv("ALPHAPILOT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("ALPHAPILOT_BAOSTOCK_LOCK_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(baostock_provider, "get_settings", lambda: settings)

    assert baostock_provider._socket_timeout() == 6.0
    assert baostock_provider._lock_timeout() == 0.75


def test_baostock_guard_enforces_one_absolute_request_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DripSocket:
        def __init__(self) -> None:
            self.timeouts: list[float] = []
            self.recv_calls = 0

        def settimeout(self, timeout: float) -> None:
            self.timeouts.append(timeout)

        @staticmethod
        def send(payload: bytes) -> int:
            return len(payload)

        def recv(self, _size: int) -> bytes:
            self.recv_calls += 1
            return b"x"

    times = iter([100.0, 100.25, 101.0, 103.0])
    monkeypatch.setattr(baostock_provider, "monotonic", lambda: next(times))
    connection = DripSocket()
    guarded = baostock_provider._EOFGuardSocket(
        connection,
        request_timeout=3.0,
    )

    assert guarded.send(b"request") == len(b"request")
    assert guarded.recv(1) == b"x"
    with pytest.raises(TimeoutError, match="request deadline exceeded"):
        guarded.recv(1)

    assert connection.timeouts == pytest.approx([2.75, 2.0])
    assert connection.recv_calls == 1
    assert isinstance(guarded.failure, TimeoutError)


def test_baostock_login_socket_raises_after_eof_instead_of_spinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ClosedSocket:
        def __init__(self) -> None:
            self.recv_calls = 0
            self.timeout: float | None = None

        def settimeout(self, timeout: float) -> None:
            self.timeout = timeout

        def recv(self, _size: int) -> bytes:
            self.recv_calls += 1
            return b""

        def shutdown(self, _how: int) -> None:
            return None

        def close(self) -> None:
            return None

    connection = ClosedSocket()

    def create_connection(
        _endpoint: tuple[str, int],
        *,
        timeout: float,
    ) -> ClosedSocket:
        assert timeout == 7.0
        return connection

    class Result:
        error_code = "0"
        error_msg = ""

    class BaoStockModule:
        @staticmethod
        def login() -> Result:
            from baostock.common import context as baostock_context
            from baostock.util.socketutil import SocketUtil

            SocketUtil().connect()
            with pytest.raises(ConnectionError, match="closed"):
                baostock_context.default_socket.recv(8192)
            return Result()

    monkeypatch.setenv("ALPHAPILOT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS", "7")
    monkeypatch.setattr(baostock_provider.socket, "create_connection", create_connection)

    try:
        result = baostock_provider._login_with_bounded_socket(BaoStockModule, None)
    finally:
        baostock_provider._discard_default_socket()

    assert result.error_code == "0"
    assert connection.timeout == pytest.approx(7.0, abs=0.01)
    assert connection.recv_calls == 1


@pytest.mark.parametrize(
    ("configured", "message"),
    [
        ("missing-port", "host:port"),
        ("127.0.0.1:not-a-port", "host:port"),
        ("127.0.0.1:0", "out of range"),
        ("127.0.0.1:65536", "out of range"),
    ],
)
def test_baostock_rejects_invalid_socks5_proxy_configuration(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    message: str,
) -> None:
    monkeypatch.setenv("ALPHAPILOT_BAOSTOCK_SOCKS5_PROXY", configured)

    with pytest.raises(DataProviderError, match=message):
        baostock_provider._socks5_endpoint()


def test_sina_invalid_payload_is_not_classified_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AkShareModule:
        @staticmethod
        def stock_zh_a_daily(**_kwargs: object) -> dict[str, object]:
            return {"unexpected": "payload"}

    provider = SinaDailyBarProvider(min_interval_seconds=0)
    monkeypatch.setattr(provider, "_module", lambda: AkShareModule)
    monkeypatch.setattr("alphapilot.data.sina_provider.sleep", lambda _seconds: None)

    with pytest.raises(DataProviderError, match="failed") as caught:
        provider.get_daily_bars("920000", date(2026, 7, 20), date(2026, 7, 20))
    assert not isinstance(caught.value, EmptyDailyBarsError)


def test_sina_hfq_factors_use_bounded_direct_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    payload = (
        'var bj920079hfq={"total":3,"data":['
        '{"d":"2026-07-24","f":"1.2000000000000000"},'
        '{"d":"2026-07-22","f":"1.1000000000000000"},'
        '{"d":"1900-01-01","f":"1.0000000000000000"}]};'
    )

    class Client:
        def __init__(self, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str, **_kwargs: object) -> httpx.Response:
            captured["url"] = url
            return httpx.Response(
                200,
                text=payload,
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(httpx, "Client", Client)

    frame = SinaDailyBarProvider(min_interval_seconds=0).get_adjustment_factors(
        "920079",
        date(2026, 7, 23),
    )

    assert captured["client_kwargs"] == {
        "timeout": 10.0,
        "follow_redirects": True,
        "trust_env": False,
    }
    assert captured["url"] == ("https://finance.sina.com.cn/realstock/company/bj920079/hfq.js")
    assert frame.to_dict(orient="records") == [
        {"date": date(1900, 1, 1), "adj_factor": 1.0},
        {"date": date(2026, 7, 22), "adj_factor": 1.1},
    ]


def test_failover_uses_next_provider_and_records_errors() -> None:
    router = FailoverMarketDataProvider(
        bars_chain=[BrokenProvider(), MockMarketDataProvider()],
        snapshot_chain=[BrokenProvider(), MockMarketDataProvider()],
    )
    frame = router.get_daily_bars("600000", date(2026, 1, 1), date(2026, 7, 1))
    assert not frame.empty
    assert router.last_bars_source == "mock"
    assert any("broken" in item for item in router.last_errors)

    snapshot = router.get_snapshot(["600000"])
    assert not snapshot.empty
    assert router.last_snapshot_source == "mock"


def test_failover_raises_when_all_sources_fail() -> None:
    router = FailoverMarketDataProvider(
        bars_chain=[BrokenProvider()], snapshot_chain=[BrokenProvider()]
    )
    with pytest.raises(DataProviderError, match="All providers failed"):
        router.get_daily_bars("600000", date(2026, 1, 1), date(2026, 7, 1))


def test_sina_bse_snapshot_parses_date_and_market_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = (
        'var hq_str_bj920000="安徽凤凰,11.350,11.320,11.710,11.760,10.700,'
        "11.700,11.710,1056462,11863548.780,0,0,0,0,0,0,0,0,0,0,0,0,0,0,"
        '0,0,0,0,0,0,2026-07-21,15:30:02,00";\n'
    ).encode("gb18030")

    def fake_get(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            content=payload,
            request=httpx.Request("GET", "https://hq.sinajs.cn/"),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    frame = SinaDailyBarProvider(min_interval_seconds=0).get_snapshot(["920000"])

    assert frame.iloc[0]["symbol"] == "920000"
    assert frame.iloc[0]["last_price"] == pytest.approx(11.71)
    assert frame.iloc[0]["prev_close_price"] == pytest.approx(11.32)
    assert frame.iloc[0]["turnover"] == pytest.approx(11_863_548.78)
    assert frame.iloc[0]["as_of"].isoformat() == "2026-07-21T07:30:02+00:00"
