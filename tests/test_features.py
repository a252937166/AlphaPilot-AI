from datetime import date

from alphapilot.data.mock import MockMarketDataProvider
from alphapilot.features.technical import compute_technical_features


def test_features_are_finite() -> None:
    bars = MockMarketDataProvider().get_daily_bars("600000", date(2025, 1, 1), date(2026, 1, 1))
    features = compute_technical_features(bars)
    assert 0 <= features["data_completeness"] <= 1
    assert features["volatility_20d"] >= 0
    assert 0 <= features["price_position_60d"] <= 1
