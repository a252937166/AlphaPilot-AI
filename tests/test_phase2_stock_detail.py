from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alphapilot.api.routes import stocks as stock_routes
from alphapilot.core.config import Settings
from alphapilot.data.baostock_provider import BaoStockMarketDataProvider
from alphapilot.data.base import DataProviderError
from alphapilot.data.futu_provider import FutuMarketDataProvider
from alphapilot.db.models import AlertRecord, Base, DailyBar, ForecastSnapshot, Security
from alphapilot.services.market_data import (
    get_bars_with_cache,
    get_period_bars,
    save_bars,
)


def _daily_frame(start: date, periods: int = 10) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=periods)
    return pd.DataFrame(
        {
            "date": dates,
            "open": [10.0 + index for index in range(periods)],
            "high": [11.0 + index for index in range(periods)],
            "low": [9.0 + index for index in range(periods)],
            "close": [10.5 + index for index in range(periods)],
            "volume": [100.0] * periods,
            "amount": [1_000.0] * periods,
        }
    )


def test_native_weekly_bars_never_write_the_daily_cache(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'native-period.db'}")
    Base.metadata.create_all(engine)

    class NativeProvider:
        name = "native"

        def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
            raise AssertionError("the weekly path must not request daily bars")

        def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
            raise AssertionError("snapshot is not part of this test")

        def get_bars(
            self,
            symbol: str,
            start: date,
            end: date,
            frequency: str,
        ) -> pd.DataFrame:
            del symbol, start, end
            assert frequency == "w"
            return _daily_frame(date(2026, 7, 6), periods=2)

    with Session(engine) as session:
        result = get_period_bars(
            session,
            NativeProvider(),
            "600519",
            date(2026, 7, 1),
            date(2026, 7, 22),
            "w",
        )
        session.flush()
        cached = session.scalar(select(func.count()).select_from(DailyBar))

    assert result["source"] == "native"
    assert len(result["frame"]) == 2
    assert cached == 0


def test_daily_only_provider_is_resampled_without_cache_pollution(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'resampled-period.db'}")
    Base.metadata.create_all(engine)

    class DailyOnlyProvider:
        name = "daily-only"

        def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
            del symbol, start, end
            return _daily_frame(date(2026, 7, 6))

        def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
            raise AssertionError("snapshot is not part of this test")

    with Session(engine) as session:
        result = get_period_bars(
            session,
            DailyOnlyProvider(),
            "600519",
            date(2026, 7, 1),
            date(2026, 7, 22),
            "w",
        )
        session.flush()
        cached = session.scalar(select(func.count()).select_from(DailyBar))

    frame = result["frame"]
    assert list(frame["date"].dt.date) == [date(2026, 7, 10), date(2026, 7, 17)]
    assert frame.iloc[0]["open"] == pytest.approx(10.0)
    assert frame.iloc[0]["high"] == pytest.approx(15.0)
    assert frame.iloc[0]["low"] == pytest.approx(9.0)
    assert frame.iloc[0]["close"] == pytest.approx(14.5)
    assert frame.iloc[0]["volume"] == pytest.approx(500.0)
    assert frame.iloc[0]["amount"] == pytest.approx(5_000.0)
    assert "该来源日线聚合" in result["warnings"][0]
    assert cached == 0


def test_daily_cache_skips_mock_writes_and_repairs_existing_mock_rows(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'daily-cache-provenance.db'}")
    Base.metadata.create_all(engine)
    frame = _daily_frame(date(2026, 7, 20), periods=1)

    with Session(engine) as session:
        assert save_bars(session, "600519", frame, "mock") == 0
        assert session.scalar(select(func.count()).select_from(DailyBar)) == 0
        session.add(
            DailyBar(
                symbol="600519",
                trade_date=date(2026, 7, 20),
                open=99,
                high=99,
                low=99,
                close=99,
                volume=99,
                amount=99,
                source="mock",
            )
        )
        session.flush()

        assert save_bars(session, "600519", frame, "baostock") == 1
        repaired = session.scalar(select(DailyBar))
        assert repaired is not None
        assert repaired.source == "baostock"
        assert repaired.close == pytest.approx(10.5)


def test_daily_cache_fallback_rejects_mixed_mock_provenance(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'daily-mock-cache.db'}")
    Base.metadata.create_all(engine)

    class BrokenDailyProvider:
        name = "broken-daily"

        def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
            del symbol, start, end
            raise DataProviderError("daily unavailable")

        def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
            raise AssertionError(f"snapshot is not part of this test: {symbols}")

    with Session(engine) as session:
        session.add_all(
            [
                DailyBar(
                    symbol="600519",
                    trade_date=date(2026, 7, 20),
                    open=10,
                    high=11,
                    low=9,
                    close=10.5,
                    volume=100,
                    amount=1_000,
                    source="baostock",
                ),
                DailyBar(
                    symbol="600519",
                    trade_date=date(2026, 7, 21),
                    open=10.5,
                    high=12,
                    low=10,
                    close=11.5,
                    volume=100,
                    amount=1_100,
                    source="mock",
                ),
            ]
        )
        session.commit()

        with pytest.raises(DataProviderError, match=r"缓存降级也被拒绝.*mock"):
            get_bars_with_cache(
                session,
                BrokenDailyProvider(),
                "600519",
                date(2026, 7, 1),
                date(2026, 7, 22),
            )


def _alert(
    *,
    action: str,
    as_of: datetime,
    created_at: datetime,
    suggested_notional: float,
) -> AlertRecord:
    return AlertRecord(
        symbol="600519",
        action=action,
        urgency="MEDIUM",
        confidence=0.8,
        suggested_position_change=0.1,
        target_low=100.0,
        target_high=120.0,
        suggested_notional=suggested_notional,
        reasons=[f"{action} reason"],
        invalidation="失效条件",
        model_version="test-v1.0.0",
        as_of=as_of,
        expires_at=as_of + timedelta(days=2),
        created_at=created_at,
    )


def _forecast_for(
    alert: AlertRecord,
    *,
    provider: str = "baostock",
) -> ForecastSnapshot:
    assert alert.as_of is not None
    assert alert.model_version is not None
    return ForecastSnapshot(
        symbol=alert.symbol,
        as_of=alert.as_of,
        provider=provider,
        model_version=alert.model_version,
        horizons={},
        features={},
        created_at=alert.created_at,
    )


def test_stock_signals_maps_only_directional_alerts_inside_window(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'signals.db'}")
    Base.metadata.create_all(engine)
    buy_date = date(2026, 7, 10)
    sell_date = date(2026, 7, 15)
    with Session(engine) as session:
        buy = _alert(
            action="BUY_CANDIDATE",
            as_of=datetime(2026, 7, 10, tzinfo=UTC),
            created_at=datetime(2026, 7, 10, 8, tzinfo=UTC),
            suggested_notional=10_000.0,
        )
        hold = _alert(
            action="HOLD",
            as_of=datetime(2026, 7, 11, tzinfo=UTC),
            created_at=datetime(2026, 7, 11, 8, tzinfo=UTC),
            suggested_notional=0.0,
        )
        sell = _alert(
            action="REDUCE",
            as_of=datetime(2026, 7, 15, tzinfo=UTC),
            created_at=datetime(2026, 7, 15, 8, tzinfo=UTC),
            suggested_notional=-10_000.0,
        )
        mock_add = _alert(
            action="ADD",
            as_of=datetime(2026, 7, 17, tzinfo=UTC),
            created_at=datetime(2026, 7, 17, 8, tzinfo=UTC),
            suggested_notional=10_000.0,
        )
        outside = _alert(
            action="ADD",
            as_of=datetime(2026, 6, 1, tzinfo=UTC),
            created_at=datetime(2026, 6, 1, 8, tzinfo=UTC),
            suggested_notional=10_000.0,
        )
        session.add_all(
            [
                _forecast_for(buy),
                buy,
                hold,
                _forecast_for(sell),
                sell,
                _forecast_for(mock_add, provider="mock"),
                mock_add,
                _forecast_for(outside),
                outside,
                DailyBar(
                    symbol="600519",
                    trade_date=buy_date,
                    open=100.0,
                    high=112.0,
                    low=99.0,
                    close=110.0,
                    volume=1_000,
                    amount=110_000,
                    source="baostock",
                ),
                DailyBar(
                    symbol="600519",
                    trade_date=date(2026, 7, 17),
                    open=105.0,
                    high=106.0,
                    low=102.0,
                    close=103.0,
                    volume=1_000,
                    amount=103_000,
                    source="mock",
                ),
                DailyBar(
                    symbol="600519",
                    trade_date=sell_date,
                    open=111.0,
                    high=112.0,
                    low=104.0,
                    close=105.0,
                    volume=1_000,
                    amount=105_000,
                    source="baostock",
                ),
            ]
        )
        session.commit()

        payload = stock_routes.stock_signals(
            "SH.600519",
            start=date(2026, 7, 1),
            end=date(2026, 7, 31),
            session=session,
        )

        with pytest.raises(HTTPException, match="开始日期"):
            stock_routes.stock_signals(
                "600519",
                start=date(2026, 8, 1),
                end=date(2026, 7, 1),
                session=session,
            )

    assert payload["count"] == 2
    assert payload["excluded_count"] == 1
    assert payload["warnings"] == ["1 条方向提醒因行情来源不可审计而未展示。"]
    assert [item["marker"] for item in payload["signals"]] == ["B", "S"]
    assert [item["trade_date"] for item in payload["signals"]] == [
        buy_date.isoformat(),
        sell_date.isoformat(),
    ]
    assert [item["close"] for item in payload["signals"]] == [110.0, 105.0]
    assert [item["close_source"] for item in payload["signals"]] == [
        "baostock",
        "baostock",
    ]
    assert [item["trade_eligible"] for item in payload["signals"]] == [False, False]
    assert all(item["forecast_provider"] == "baostock" for item in payload["signals"])
    assert payload["signals"][0]["model_version"] == "test-v1.0.0"
    assert payload["signals"][0]["as_of"].endswith("+00:00")


def test_stock_overview_sanitizes_quote_and_uses_labeled_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'overview.db'}")
    Base.metadata.create_all(engine)
    snapshot_at = datetime(2026, 7, 22, 7, tzinfo=UTC)
    with Session(engine) as session:
        session.add_all(
            [
                Security(
                    symbol="600519",
                    name="贵州茅台",
                    turnover_rate=0.5,
                    pe_ttm=19.7,
                    market_cap=1_600_000_000_000.0,
                    float_cap=1_500_000_000_000.0,
                    pb=7.0,
                    snapshot_at=snapshot_at,
                ),
                DailyBar(
                    symbol="600519",
                    trade_date=date(2026, 7, 21),
                    open=1_300,
                    high=1_320,
                    low=1_280,
                    close=1_310,
                    volume=1_000,
                    amount=1_310_000,
                    source="baostock",
                ),
                DailyBar(
                    symbol="600519",
                    trade_date=date(2026, 7, 22),
                    open=99_999,
                    high=99_999,
                    low=99_999,
                    close=99_999,
                    volume=1_000,
                    amount=99_999_000,
                    source="mock",
                ),
            ]
        )
        session.commit()

    class Provider:
        name = "quote-stub"

        def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
            del symbol, start, end
            return _daily_frame(date(2025, 9, 1), periods=220)

        def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
            assert symbols == ["600519"]
            return pd.DataFrame(
                [
                    {
                        "symbol": "SH.600519",
                        "last": 1305.0,
                        "change_pct": -0.23,
                        "open": None,
                        "high": None,
                        "low": None,
                        "volume": float("nan"),
                        "amount": 8_400_000_000.0,
                        "turnover_rate": float("nan"),
                        "pe_ttm": None,
                        "market_cap": None,
                        "float_cap": None,
                        "pb": None,
                        "as_of": datetime(2026, 7, 22, 7, 5, tzinfo=UTC),
                    }
                ]
            )

    monkeypatch.setattr(stock_routes, "get_provider", lambda _name: Provider())
    with Session(engine) as session:
        payload = stock_routes.stock_overview(
            "600519",
            provider=None,
            session=session,
            cninfo=Any,
        )

    quote = payload["quote"]
    assert quote["volume"] is None
    assert quote["turnover_rate"] == pytest.approx(0.5)
    assert quote["pe_ttm"] == pytest.approx(19.7)
    assert quote["market_cap"] == pytest.approx(1_600_000_000_000.0)
    assert quote["fundamentals_as_of"].endswith("+00:00")
    assert quote["ohlc_source"] == "unavailable"
    assert quote["ohlc_trade_date"] is None
    assert [quote[field] for field in ("open", "high", "low")] == [None, None, None]
    assert quote["as_of"].endswith("+00:00")


def test_periodic_provider_failure_without_cache_stays_explicit(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'period-error.db'}")
    Base.metadata.create_all(engine)

    class BrokenPeriodicProvider:
        name = "broken-periodic"

        def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
            raise DataProviderError("daily unavailable")

        def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
            raise DataProviderError("snapshot unavailable")

        def get_bars(
            self,
            symbol: str,
            start: date,
            end: date,
            frequency: str,
        ) -> pd.DataFrame:
            raise DataProviderError(f"{frequency} unavailable")

    with Session(engine) as session, pytest.raises(DataProviderError, match="w unavailable"):
        get_period_bars(
            session,
            BrokenPeriodicProvider(),
            "600519",
            date(2026, 7, 1),
            date(2026, 7, 22),
            "w",
        )


def test_periodic_cache_fallback_rejects_any_mock_provenance(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'period-mock-cache.db'}")
    Base.metadata.create_all(engine)

    class BrokenPeriodicProvider:
        name = "broken-periodic"

        def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
            raise DataProviderError("daily unavailable")

        def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
            raise DataProviderError("snapshot unavailable")

        def get_bars(
            self,
            symbol: str,
            start: date,
            end: date,
            frequency: str,
        ) -> pd.DataFrame:
            raise DataProviderError(f"{frequency} unavailable")

    with Session(engine) as session:
        session.add_all(
            [
                DailyBar(
                    symbol="600519",
                    trade_date=date(2026, 7, 20),
                    open=10,
                    high=11,
                    low=9,
                    close=10.5,
                    volume=100,
                    amount=1_000,
                    source="baostock",
                ),
                DailyBar(
                    symbol="600519",
                    trade_date=date(2026, 7, 21),
                    open=10.5,
                    high=12,
                    low=10,
                    close=11.5,
                    volume=100,
                    amount=1_100,
                    source="mock",
                ),
            ]
        )
        session.commit()

        with pytest.raises(DataProviderError, match=r"不可用于真实行情展示.*mock"):
            get_period_bars(
                session,
                BrokenPeriodicProvider(),
                "600519",
                date(2026, 7, 1),
                date(2026, 7, 22),
                "w",
            )


def test_baostock_passes_weekly_frequency_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        error_code = "0"
        error_msg = ""

        def __init__(self) -> None:
            self.pending = True

        def next(self) -> bool:
            if not self.pending:
                return False
            self.pending = False
            return True

        @staticmethod
        def get_row_data() -> list[str]:
            return ["2026-07-17", "10", "12", "9", "11", "100", "1100"]

    class Module:
        kwargs: ClassVar[dict[str, object]] = {}

        @classmethod
        def query_history_k_data_plus(
            cls,
            *_args: object,
            **kwargs: object,
        ) -> Result:
            cls.kwargs = kwargs
            return Result()

    provider = BaoStockMarketDataProvider()
    monkeypatch.setattr("alphapilot.data.baostock_provider._logged_in", True)
    monkeypatch.setattr(provider, "_module", lambda: Module)

    frame = provider.get_bars(
        "600519",
        date(2026, 7, 1),
        date(2026, 7, 22),
        "w",
    )

    assert Module.kwargs["frequency"] == "w"
    assert frame.iloc[0]["close"] == pytest.approx(11.0)


def test_futu_maps_monthly_frequency_and_exposes_full_quote_fields() -> None:
    class Client:
        calls: list[tuple[str, Any, Any]]

        def __init__(self) -> None:
            self.calls = []

        def quote_call_raw(
            self,
            method: str,
            args: Any = None,
            kwargs: Any = None,
        ) -> Any:
            self.calls.append((method, args, kwargs))
            if method == "request_history_kline":
                return (
                    pd.DataFrame(
                        [
                            {
                                "time_key": "2026-06-30",
                                "open": 10,
                                "high": 12,
                                "low": 9,
                                "close": 11,
                                "volume": 100,
                                "turnover": 1_100,
                            }
                        ]
                    ),
                    None,
                )
            if method == "get_market_snapshot":
                return pd.DataFrame(
                    [
                        {
                            "code": "SH.600519",
                            "update_time": "2026-07-22 15:05:06",
                            "last_price": 1_305,
                            "prev_close_price": 1_308,
                            "open_price": 1_300,
                            "high_price": 1_308,
                            "low_price": 1_283.24,
                            "volume": 6_500_000,
                            "turnover": 8_400_000_000,
                            "turnover_rate": 0.521,
                            "pe_ratio": 19.8,
                            "pe_ttm_ratio": 19.7,
                            "total_market_val": 1_600_000_000_000,
                            "circular_market_val": 1_500_000_000_000,
                            "pb_ratio": 7.0,
                        }
                    ]
                )
            raise AssertionError(f"unexpected method {method}")

    client = Client()
    provider = FutuMarketDataProvider(Settings(), client=client)  # type: ignore[arg-type]

    bars = provider.get_bars(
        "600519",
        date(2026, 1, 1),
        date(2026, 7, 22),
        "m",
    )
    quote = provider.get_snapshot(["600519"]).iloc[0]

    assert client.calls[0][2]["ktype"] == "K_MON"
    assert bars.iloc[0]["amount"] == pytest.approx(1_100)
    assert quote["open"] == pytest.approx(1_300)
    assert quote["high"] == pytest.approx(1_308)
    assert quote["low"] == pytest.approx(1_283.24)
    assert quote["turnover_rate"] == pytest.approx(0.521)
    assert quote["pe_ttm"] == pytest.approx(19.7)
    assert quote["market_cap"] == pytest.approx(1_600_000_000_000)
    assert quote["float_cap"] == pytest.approx(1_500_000_000_000)
    assert quote["as_of"].isoformat() == "2026-07-22T07:05:06+00:00"
