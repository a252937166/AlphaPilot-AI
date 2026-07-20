from datetime import date

from alphapilot.data.mock import MockMarketDataProvider
from alphapilot.prediction.baseline import BaselineForecastEngine


def test_forecast_contract() -> None:
    provider = MockMarketDataProvider()
    bars = provider.get_daily_bars("000001", date(2024, 1, 1), date(2026, 1, 1))
    forecast = BaselineForecastEngine().forecast("000001", bars, provider.name)
    assert set(forecast.horizons) == {"1d", "5d", "20d"}
    assert all(0 <= item.p_up <= 1 for item in forecast.horizons.values())
    assert forecast.horizons["20d"].q10 < forecast.horizons["20d"].q90
