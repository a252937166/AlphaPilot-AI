from __future__ import annotations

import json
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from alphapilot.backtest import adjust
from alphapilot.core.config import Settings
from alphapilot.db.models import AdjFactor, Base, DailyBar, Security
from alphapilot.jobs import backtest_jobs
from alphapilot.jobs.backtest_jobs import register_backtest_jobs
from alphapilot.jobs.registry import JOBS, JobExecutionError


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'adjust.db'}")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _bar(symbol: str, trade_date: date, close: float) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100,
        amount=close * 100,
        source="baostock",
    )


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._buffer = BytesIO(json.dumps(payload).encode())

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._buffer.read()


def test_tushare_call_posts_standard_payload_without_ambient_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Opener:
        def open(self, request: Any, timeout: float) -> _Response:
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode())
            return _Response(
                {
                    "code": 0,
                    "msg": None,
                    "data": {
                        "fields": ["ts_code", "trade_date", "adj_factor"],
                        "items": [["600519.SH", "20260722", 42.0]],
                    },
                }
            )

    def build_opener(*handlers: object) -> Opener:
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(adjust.request, "build_opener", build_opener)

    frame = adjust.tushare_call(
        "secret-token",
        "adj_factor",
        {"ts_code": "600519.SH"},
        "ts_code,trade_date,adj_factor",
    )

    assert frame.to_dict(orient="records") == [
        {"ts_code": "600519.SH", "trade_date": "20260722", "adj_factor": 42.0}
    ]
    assert captured["payload"] == {
        "api_name": "adj_factor",
        "token": "secret-token",
        "params": {"ts_code": "600519.SH"},
        "fields": "ts_code,trade_date,adj_factor",
    }
    assert captured["timeout"] == 30.0
    assert len(captured["handlers"]) == 1


def test_tushare_call_rejects_business_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Opener:
        def open(self, _request: Any, timeout: float) -> _Response:
            assert timeout == 30.0
            return _Response({"code": -2001, "msg": "permission denied", "data": None})

    monkeypatch.setattr(
        adjust.request,
        "build_opener",
        lambda *_handlers: Opener(),
    )

    with pytest.raises(adjust.TushareAPIError, match="code=-2001"):
        adjust.tushare_call("secret-token", "adj_factor", {})


def test_sync_adj_factors_upserts_and_resumes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    try:
        session.add(Security(symbol="600519", market="CN"))
        session.add_all(
            [
                _bar("600519", date(2026, 7, 21), 10.0),
                _bar("600519", date(2026, 7, 22), 10.2),
            ]
        )
        session.commit()
        calls: list[dict[str, Any]] = []

        def fake_call(
            _token: str,
            _api_name: str,
            params: dict[str, Any],
            _fields: str = "",
        ) -> pd.DataFrame:
            calls.append(params)
            return pd.DataFrame(
                [
                    {
                        "ts_code": "600519.SH",
                        "trade_date": "20260721",
                        "adj_factor": 1.0,
                    },
                    {
                        "ts_code": "600519.SH",
                        "trade_date": "20260722",
                        "adj_factor": 1.1,
                    },
                ]
            )

        monkeypatch.setattr(adjust, "tushare_call", fake_call)
        monkeypatch.setattr(adjust, "get_settings", lambda: Settings(tushare_token="token"))
        monkeypatch.setattr(adjust, "sleep", lambda _seconds: None)

        first = adjust.sync_adj_factors(session)
        second = adjust.sync_adj_factors(session)

        assert first["coverage"] == 1.0
        assert first["rows_inserted"] == 2
        assert first["failed_count"] == 0
        assert second["skipped"] == 1
        assert len(calls) == 1
        assert calls[0]["ts_code"] == "600519.SH"
        assert calls[0]["start_date"] == "20260721"
        assert calls[0]["end_date"] == "20260722"
        rows = session.scalars(select(AdjFactor).order_by(AdjFactor.trade_date)).all()
        assert [(row.trade_date, row.adj_factor) for row in rows] == [
            (date(2026, 7, 21), 1.0),
            (date(2026, 7, 22), 1.1),
        ]
    finally:
        session.close()


def test_sync_adj_factors_falls_back_after_real_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    try:
        session.add(Security(symbol="600519", market="CN"))
        session.add(_bar("600519", date(2026, 7, 22), 10.0))
        session.commit()

        def rate_limited(*_args: object, **_kwargs: object) -> pd.DataFrame:
            raise adjust.TushareAPIError(
                "Tushare adj_factor 返回错误 code=40203：频率超限",
                code=40203,
            )

        class BaoStock:
            def get_adjusted_closes(
                self,
                _symbol: str,
                _start: date,
                _end: date,
            ) -> pd.DataFrame:
                return pd.DataFrame(
                    [{"date": date(2026, 7, 22), "close": 20.0}]
                )

        monkeypatch.setattr(adjust, "tushare_call", rate_limited)
        monkeypatch.setattr(adjust, "get_settings", lambda: Settings(tushare_token="token"))
        monkeypatch.setattr(adjust, "BaoStockMarketDataProvider", BaoStock)
        monkeypatch.setattr(adjust, "sleep", lambda _seconds: None)

        stats = adjust.sync_adj_factors(session)

        factor = session.scalar(select(AdjFactor))
        assert factor is not None
        assert factor.adj_factor == pytest.approx(2.0)
        assert factor.source == "baostock-hfq"
        assert stats["tushare_rate_limited"] is True
        assert stats["source_counts"] == {"baostock-hfq": 1}
        assert stats["coverage"] == 1.0
    finally:
        session.close()


def test_sync_adj_factors_can_refresh_latest_bar_after_daily_sync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    target = date(2026, 7, 22)
    try:
        session.add(Security(symbol="600519", market="CN"))
        session.add(_bar("600519", target, 10.0))
        session.add(
            AdjFactor(
                symbol="600519",
                trade_date=target,
                adj_factor=1.0,
                source="tushare",
            )
        )
        session.commit()
        calls: list[dict[str, Any]] = []

        def fake_call(
            _token: str,
            _api_name: str,
            params: dict[str, Any],
            _fields: str = "",
        ) -> pd.DataFrame:
            calls.append(params)
            return pd.DataFrame(
                [
                    {
                        "ts_code": "600519.SH",
                        "trade_date": "20260722",
                        "adj_factor": 1.2,
                    }
                ]
            )

        monkeypatch.setattr(adjust, "tushare_call", fake_call)
        monkeypatch.setattr(
            adjust,
            "get_settings",
            lambda: Settings(tushare_token="token"),
        )
        monkeypatch.setattr(adjust, "sleep", lambda _seconds: None)

        stats = adjust.sync_adj_factors(session, refresh_latest=True)

        factor = session.get(AdjFactor, 1)
        assert factor is not None
        assert factor.adj_factor == pytest.approx(1.2)
        assert stats["refresh_latest"] is True
        assert stats["rows_updated"] == 1
        assert stats["skipped"] == 0
        assert calls[0]["start_date"] == "20260722"
        assert calls[0]["end_date"] == "20260722"
    finally:
        session.close()


def test_incremental_sync_preserves_each_symbols_factor_scale(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    first = date(2026, 7, 21)
    second = date(2026, 7, 22)
    try:
        session.add_all(
            [
                Security(symbol="600519", market="CN"),
                _bar("600519", first, 10.0),
                _bar("600519", second, 10.0),
                AdjFactor(
                    symbol="600519",
                    trade_date=first,
                    adj_factor=1.0,
                    source="baostock-hfq",
                ),
            ]
        )
        session.commit()

        class BaoStock:
            def get_adjusted_closes(
                self,
                symbol: str,
                start: date,
                end: date,
            ) -> pd.DataFrame:
                assert (symbol, start, end) == ("600519", second, second)
                return pd.DataFrame([{"date": second, "close": 20.0}])

        monkeypatch.setattr(adjust, "BaoStockMarketDataProvider", BaoStock)
        monkeypatch.setattr(
            adjust,
            "get_settings",
            lambda: Settings(tushare_token="token"),
        )
        monkeypatch.setattr(
            adjust,
            "tushare_call",
            lambda *_args, **_kwargs: pytest.fail(
                "已有 baostock-hfq 历史时不得切换 Tushare 标尺"
            ),
        )
        monkeypatch.setattr(adjust, "sleep", lambda _seconds: None)

        stats = adjust.sync_adj_factors(session)

        factors = {
            row.trade_date: (row.adj_factor, row.source)
            for row in session.query(AdjFactor).order_by(AdjFactor.trade_date)
        }
        assert factors[second][0] == pytest.approx(2.0)
        assert factors[second][1] == "baostock-hfq"
        assert stats["source_counts"] == {"baostock-hfq": 1}
    finally:
        session.close()


def test_tushare_history_rate_limit_never_crosses_factor_scale(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    first = date(2026, 7, 21)
    second = date(2026, 7, 22)
    try:
        session.add_all(
            [
                Security(symbol="600519", market="CN"),
                _bar("600519", first, 10.0),
                _bar("600519", second, 10.0),
                AdjFactor(
                    symbol="600519",
                    trade_date=first,
                    adj_factor=1.0,
                    source="tushare",
                ),
            ]
        )
        session.commit()

        def rate_limited(*_args: object, **_kwargs: object) -> pd.DataFrame:
            raise adjust.TushareAPIError(
                "Tushare adj_factor 返回错误 code=40203：频率超限",
                code=40203,
            )

        monkeypatch.setattr(adjust, "tushare_call", rate_limited)
        monkeypatch.setattr(
            adjust,
            "get_settings",
            lambda: Settings(tushare_token="token"),
        )
        monkeypatch.setattr(adjust, "sleep", lambda _seconds: None)

        stats = adjust.sync_adj_factors(session)

        rows = session.query(AdjFactor).order_by(AdjFactor.trade_date).all()
        assert [(row.trade_date, row.source) for row in rows] == [
            (first, "tushare")
        ]
        assert stats["failed_count"] == 1
        assert stats["tushare_rate_limited"] is True
        assert "TushareAPIError" in stats["failures"][0]["error"]
    finally:
        session.close()


def test_adjusted_returns_remove_split_jump_and_mark_fallback(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    try:
        session.add(Security(symbol="600519", market="CN"))
        session.add_all(
            [
                DailyBar(
                    symbol="600519",
                    trade_date=date(2026, 7, 19),
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=100,
                    amount=100,
                    source="mock",
                ),
                _bar("600519", date(2026, 7, 20), 10.0),
                _bar("600519", date(2026, 7, 21), 5.0),
                _bar("600519", date(2026, 7, 22), 5.5),
                AdjFactor(
                    symbol="600519",
                    trade_date=date(2026, 7, 20),
                    adj_factor=1.0,
                ),
                AdjFactor(
                    symbol="600519",
                    trade_date=date(2026, 7, 21),
                    adj_factor=2.0,
                ),
            ]
        )
        session.commit()

        frame = adjust.adjusted_close_frame(
            session,
            "600519",
            date(2026, 7, 19),
            date(2026, 7, 22),
        )
        returns = adjust.daily_returns(
            session,
            "600519",
            date(2026, 7, 19),
            date(2026, 7, 22),
        )

        assert frame["adj_close"].tolist() == [10.0, 10.0, 5.5]
        assert frame["degraded"].tolist() == [False, False, True]
        assert returns.loc[date(2026, 7, 21)] == pytest.approx(0.0)
        assert returns.attrs["degraded"] is True
        assert "缺少复权因子" in returns.attrs["warnings"][0]
    finally:
        session.close()


def test_adjustment_job_runs_at_1850() -> None:
    original = JOBS.get("sync_adj_factors")
    try:
        register_backtest_jobs()
        trigger = JOBS["sync_adj_factors"].trigger
        assert trigger is not None
        assert str(trigger).startswith("cron[day_of_week='mon-fri', hour='18', minute='50'")
    finally:
        if original is None:
            JOBS.pop("sync_adj_factors", None)
        else:
            JOBS["sync_adj_factors"] = original


def test_adjustment_job_waits_for_daily_bars_and_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = iter([True, True, False])
    clock = iter([0.0, 1.0, 2.0, 3.0])
    sleeps: list[float] = []
    monkeypatch.setattr(
        backtest_jobs,
        "_daily_bars_running",
        lambda: next(states),
    )
    monkeypatch.setattr(backtest_jobs, "monotonic", lambda: next(clock))
    monkeypatch.setattr(backtest_jobs, "sleep", sleeps.append)

    waited = backtest_jobs._wait_for_daily_bars()

    assert waited == pytest.approx(3.0)
    assert sleeps == [5.0, 5.0]

    monkeypatch.setattr(backtest_jobs, "_daily_bars_running", lambda: True)
    timeout_clock = iter([0.0, 1800.0])
    monkeypatch.setattr(backtest_jobs, "monotonic", lambda: next(timeout_clock))

    with pytest.raises(JobExecutionError, match="超过 30 分钟") as caught:
        backtest_jobs._wait_for_daily_bars()
    assert caught.value.stats["reason"] == "daily_bars_wait_timeout"
