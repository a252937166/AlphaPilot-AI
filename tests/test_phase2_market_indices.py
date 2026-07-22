from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alphapilot.api.routes.market import market_indices
from alphapilot.core.config import Settings
from alphapilot.services import market_data


def _index_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2026-07-22"),
                "open": 3582.25,
                "high": 3598.42,
                "low": 3574.11,
                "close": 3592.77,
                "volume": 418_000_000.0,
                "amount": 521_000_000_000.0,
            }
        ]
    )


def test_market_indices_serializes_real_index_ohlcva_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        market_data,
        "index_quotes",
        lambda _settings: [{"symbol": "SH.000001", "last": 3592.77}],
    )
    monkeypatch.setattr(
        market_data,
        "index_history",
        lambda _session, _settings, days: {"SH.000001": _index_frame().tail(days)},
    )
    engine = create_engine("sqlite://")

    with Session(engine) as session:
        payload = market_indices(history_days=60, session=session, settings=Settings())

    assert payload["series"] == {
        "SH.000001": [
            {
                "date": "2026-07-22",
                "open": 3582.25,
                "high": 3598.42,
                "low": 3574.11,
                "close": 3592.77,
                "volume": 418_000_000.0,
                "amount": 521_000_000_000.0,
            }
        ]
    }
    assert payload["symbols"][0] == {"symbol": "SH.000001", "name": "上证指数"}


def test_index_history_keeps_exchange_prefixes_for_every_provider_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []
    provider = object()

    monkeypatch.setattr(market_data, "build_index_provider", lambda _settings: provider)

    def fake_get_bars_with_cache(
        _session: Session,
        selected_provider: object,
        symbol: str,
        _start: date,
        _end: date,
    ) -> market_data.BarsResult:
        assert selected_provider is provider
        requested.append(symbol)
        return {"frame": _index_frame(), "source": "test-index", "warnings": []}

    monkeypatch.setattr(market_data, "get_bars_with_cache", fake_get_bars_with_cache)
    engine = create_engine("sqlite://")

    with Session(engine) as session:
        result = market_data.index_history(session, Settings(), days=10)

    expected = [entry["symbol"] for entry in market_data.INDEX_SYMBOLS]
    assert requested == expected
    assert list(result) == expected
    assert "000001" not in requested
    assert "SH.000001" in requested


def test_market_indices_drops_incomplete_points_instead_of_inventing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incomplete = _index_frame()
    incomplete.loc[0, "open"] = float("nan")
    monkeypatch.setattr(market_data, "index_quotes", lambda _settings: [])
    monkeypatch.setattr(
        market_data,
        "index_history",
        lambda _session, _settings, days: {"SH.000001": incomplete.tail(days)},
    )
    engine = create_engine("sqlite://")

    with Session(engine) as session:
        payload = market_indices(history_days=60, session=session, settings=Settings())

    assert payload["series"] == {"SH.000001": []}
