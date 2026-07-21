from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alphapilot.db.models import (
    Base,
    CompositeScore,
    DailyBar,
    FactorValue,
    ForecastSnapshot,
    Security,
    WatchlistItem,
)
from alphapilot.domain.models import ScreeningRequest, StockForecast
from alphapilot.prediction.baseline import BaselineForecastEngine
from alphapilot.services.screening_v2 import (
    ScreeningFilterError,
    ScreeningUnavailableError,
    run_factor_screen,
)

TRADE_DATE = date(2026, 7, 21)


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'screening-v2.db'}")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed_symbol(
    session: Session,
    symbol: str,
    *,
    score: float,
    volatility_z: float | None,
    industry: str = "科技",
    market_cap: float = 100.0,
    daily_drift: float = 0.001,
    bars: int = 80,
    win_rate: float | None = None,
    watchlist: bool = False,
) -> None:
    session.add(
        Security(
            symbol=symbol,
            name=f"测试{symbol}",
            industry_csrc=industry,
            list_status="listed",
            is_st=False,
            market_cap=market_cap,
        )
    )
    session.add(
        CompositeScore(
            symbol=symbol,
            trade_date=TRADE_DATE,
            score=score,
            win_rate_20d=win_rate,
            factors={},
            model_version="factor-score-v1.0.0",
        )
    )
    if volatility_z is not None:
        session.add(
            FactorValue(
                symbol=symbol,
                trade_date=TRADE_DATE,
                factor="volatility_20d",
                raw=abs(volatility_z) + 0.1,
                zscore=volatility_z,
                model_version="factor-v1.0.0",
            )
        )
    if watchlist:
        session.add(WatchlistItem(symbol=symbol, display_name=f"自选{symbol}"))

    dates = pd.bdate_range(end=TRADE_DATE, periods=bars)
    close = 10.0
    for index, timestamp in enumerate(dates):
        if index:
            close *= 1.0 + daily_drift
        session.add(
            DailyBar(
                symbol=symbol,
                trade_date=timestamp.date(),
                open=close * 0.999,
                high=close * 1.01,
                low=close * 0.99,
                close=close,
                volume=1_000.0 + index,
                amount=close * (1_000.0 + index),
                source="test",
            )
        )


def test_all_filters_industry_market_cap_and_risk_tertile(tmp_path: Path) -> None:
    with _session(tmp_path) as session:
        _seed_symbol(session, "000001", score=90, volatility_z=-2.0, market_cap=150)
        _seed_symbol(session, "000002", score=80, volatility_z=-1.0, market_cap=200)
        _seed_symbol(session, "000003", score=99, volatility_z=-0.2, market_cap=50)
        _seed_symbol(
            session,
            "000004",
            score=98,
            volatility_z=0.2,
            industry="银行",
            market_cap=300,
        )
        _seed_symbol(session, "000005", score=70, volatility_z=1.0, market_cap=500)
        _seed_symbol(session, "000006", score=60, volatility_z=2.0, market_cap=600)
        session.commit()

        response = run_factor_screen(
            session,
            ScreeningRequest(
                universe="all",
                industries=["科技"],
                min_market_cap=100,
                risk_level="low",
                top_n=10,
            ),
        )

    assert response.requested == 6
    assert response.succeeded == 2
    assert [item.symbol for item in response.candidates] == ["000001", "000002"]
    assert all(item.risk_level == "low" for item in response.candidates)
    assert all(item.industry == "科技" for item in response.candidates)
    assert all(item.p_up_20d is not None for item in response.candidates)


def test_watchlist_win_rate_prefers_real_values_then_score(tmp_path: Path) -> None:
    with _session(tmp_path) as session:
        _seed_symbol(
            session,
            "000011",
            score=95,
            volatility_z=-1,
            watchlist=True,
        )
        _seed_symbol(
            session,
            "000012",
            score=60,
            volatility_z=0,
            win_rate=0.7,
            watchlist=True,
        )
        _seed_symbol(
            session,
            "000013",
            score=99,
            volatility_z=1,
            win_rate=0.9,
        )
        session.commit()

        response = run_factor_screen(
            session,
            ScreeningRequest(universe="watchlist", sort_by="win_rate", top_n=10),
        )

    assert response.requested == 2
    assert response.succeeded == 2
    assert [item.symbol for item in response.candidates] == ["000012", "000011"]
    assert response.candidates[0].win_rate_20d == 0.7
    assert response.candidates[1].win_rate_20d is None


def test_expected_return_reorders_only_score_preselected_top_n(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with _session(tmp_path) as session:
        _seed_symbol(
            session,
            "000021",
            score=100,
            volatility_z=-1,
            daily_drift=-0.003,
        )
        _seed_symbol(
            session,
            "000022",
            score=90,
            volatility_z=0,
            daily_drift=0.003,
        )
        _seed_symbol(
            session,
            "000023",
            score=80,
            volatility_z=1,
            daily_drift=0.006,
        )
        session.commit()

        calls: list[str] = []
        original = BaselineForecastEngine.forecast

        def tracked_forecast(
            engine: BaselineForecastEngine,
            symbol: str,
            bars: pd.DataFrame,
            provider: str,
        ) -> StockForecast:
            calls.append(symbol)
            return original(engine, symbol, bars, provider)

        monkeypatch.setattr(BaselineForecastEngine, "forecast", tracked_forecast)
        response = run_factor_screen(
            session,
            ScreeningRequest(universe="all", sort_by="expected_return", top_n=2),
        )

    assert set(calls) == {"000021", "000022"}
    assert "000023" not in calls
    assert [item.symbol for item in response.candidates] == ["000022", "000021"]
    assert all(item.forecast_source == "daily_bars-cache" for item in response.candidates)
    assert response.candidates[0].expected_return_20d is not None
    assert response.candidates[1].expected_return_20d is not None


def test_expected_return_preselection_uses_persisted_forecast_when_present(
    tmp_path: Path,
) -> None:
    with _session(tmp_path) as session:
        _seed_symbol(session, "000031", score=99, volatility_z=-1)
        _seed_symbol(session, "000032", score=10, volatility_z=1)
        session.add(
            ForecastSnapshot(
                symbol="000032",
                as_of=datetime(2026, 7, 20, tzinfo=UTC),
                provider="test",
                model_version="test",
                horizons={"20d": {"expected_return": -0.2}},
                features={},
            )
        )
        session.commit()

        response = run_factor_screen(
            session,
            ScreeningRequest(universe="all", sort_by="expected_return", top_n=1),
        )

    assert [item.symbol for item in response.candidates] == ["000032"]


def test_missing_local_bars_stays_null_and_never_invents_forecast(tmp_path: Path) -> None:
    with _session(tmp_path) as session:
        _seed_symbol(session, "000041", score=90, volatility_z=-1, bars=10)
        _seed_symbol(session, "000042", score=80, volatility_z=1, bars=80)
        session.commit()

        response = run_factor_screen(
            session,
            ScreeningRequest(universe="all", top_n=2),
        )

    missing = next(item for item in response.candidates if item.symbol == "000041")
    available = next(item for item in response.candidates if item.symbol == "000042")
    assert missing.p_up_5d is None
    assert missing.p_up_20d is None
    assert missing.expected_return_20d is None
    assert missing.confidence_20d is None
    assert missing.quality_placeholder_score is None
    assert missing.forecast_source is None
    assert missing.warnings == ["本地日线不足，未生成概率预测；请先运行 sync_daily_bars。"]
    assert response.failed["000041"] == missing.warnings[0]
    assert available.p_up_20d is not None


def test_style_and_missing_factor_inputs_raise_actionable_chinese_errors(
    tmp_path: Path,
) -> None:
    with _session(tmp_path) as session:
        with pytest.raises(ScreeningFilterError, match=r"P2\.2-S4"):
            run_factor_screen(
                session,
                ScreeningRequest(universe="all", style="growth"),
            )
        with pytest.raises(ScreeningUnavailableError, match="compute_factors"):
            run_factor_screen(session, ScreeningRequest(universe="all"))
