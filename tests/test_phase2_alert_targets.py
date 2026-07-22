from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alphapilot.api.routes.alerts import _alert_payload
from alphapilot.db.migrate import run_migrations
from alphapilot.db.models import AlertRecord, Base, DailyBar, Notification, WatchlistItem
from alphapilot.domain.models import HorizonForecast, StockForecast
from alphapilot.services import watchlist as watchlist_service

SYMBOL = "600519"
AS_OF_DATE = date(2026, 7, 21)
AS_OF = datetime(2026, 7, 21, 7, 0, tzinfo=UTC)


class SnapshotProvider:
    name = "fixture"

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        del symbols
        return pd.DataFrame(self.rows)

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        del symbol, start, end
        raise AssertionError("forecast_for_symbol is replaced by the offline fixture")


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    database = create_engine(f"sqlite:///{tmp_path / 'alert-targets.db'}")
    Base.metadata.create_all(database)
    return database


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


def _forecast(
    *,
    q10: float = -0.10,
    q90: float = 0.20,
    p_up_20d: float = 0.72,
    volatility: float | None = 0.60,
) -> StockForecast:
    def horizon(days: int, p_up: float) -> HorizonForecast:
        return HorizonForecast(
            horizon_days=days,
            p_up=p_up,
            expected_return=0.03,
            q10=q10,
            q50=0.03,
            q90=q90,
            confidence=0.75,
        )

    features = {} if volatility is None else {"volatility_20d": volatility}
    return StockForecast(
        symbol=SYMBOL,
        as_of=AS_OF,
        provider="fixture",
        model_version="forecast-v1.0.0",
        data_points=220,
        features=features,
        horizons={
            "1d": horizon(1, 0.60),
            "5d": horizon(5, 0.65 if p_up_20d > 0.5 else 0.30),
            "20d": horizon(20, p_up_20d),
        },
    )


def _daily_bar(symbol: str, trade_date: date, close: float) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000.0,
        amount=close * 1_000.0,
        source="fixture",
    )


def test_alert_column_migration_keeps_legacy_rows_nullable_and_is_idempotent(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-alerts.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE securities (symbol TEXT PRIMARY KEY)"))
        connection.execute(
            text("CREATE TABLE screening_runs (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        )
        connection.execute(text("CREATE TABLE style_daily (trade_date DATE PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE alerts ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, action TEXT)"
            )
        )
        connection.execute(text("INSERT INTO alerts (symbol, action) VALUES ('600519', 'WATCH')"))

    applied = run_migrations(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("alerts")}
    with engine.connect() as connection:
        legacy = connection.execute(
            text("SELECT target_low, target_high, suggested_notional FROM alerts WHERE id = 1")
        ).one()

    assert {
        "alerts.target_low",
        "alerts.target_high",
        "alerts.suggested_notional",
    }.issubset(applied)
    assert {"target_low", "target_high", "suggested_notional"}.issubset(columns)
    assert tuple(legacy) == (None, None, None)
    assert run_migrations(engine) == []


@pytest.mark.parametrize(
    ("position_change", "volatility", "expected_notional"),
    [
        (0.10, 0.60, 50_000.0),
        (-0.25, 0.15, -250_000.0),
        (0.0, None, 0.0),
        (0.10, 0.0, 100_000.0),
    ],
)
def test_alert_targets_and_signed_volatility_scaled_notional(
    position_change: float,
    volatility: float | None,
    expected_notional: float,
) -> None:
    target_low, target_high, notional, warnings = watchlist_service._alert_targets_and_notional(
        _forecast(volatility=volatility),
        last_price=100.0,
        position_change=position_change,
        demo_equity=1_000_000.0,
    )

    assert target_low == pytest.approx(90.0)
    assert target_high == pytest.approx(120.0)
    assert notional == pytest.approx(expected_notional)
    assert warnings == []


def test_alert_enrichment_discloses_invalid_inputs_without_fabricating_values() -> None:
    target_low, target_high, notional, warnings = watchlist_service._alert_targets_and_notional(
        _forecast(q10=-1.0, q90=-1.0, volatility=None),
        last_price=100.0,
        position_change=0.10,
        demo_equity=1_000_000.0,
    )

    assert target_low is None
    assert target_high is None
    assert notional is None
    assert warnings == [
        "缺少有效现价或20日分位区间，目标价暂不可用。",
        "缺少有效20日波动率，建议金额暂不可用。",
    ]


def test_refresh_prefers_snapshot_price_and_exposes_enrichment_in_api_payload(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session.add(WatchlistItem(symbol=SYMBOL, display_name="贵州茅台"))
    session.add(_daily_bar(SYMBOL, AS_OF_DATE, 80.0))
    session.commit()
    monkeypatch.setattr(
        watchlist_service,
        "forecast_for_symbol",
        lambda *_args, **_kwargs: _forecast(),
    )
    monkeypatch.setattr(
        watchlist_service,
        "get_settings",
        lambda: SimpleNamespace(demo_equity=2_000_000.0),
    )
    provider = SnapshotProvider([{"symbol": "SH.600519", "last": 120.0}])

    created = watchlist_service.refresh_alerts(session, provider)
    session.flush()

    assert len(created) == 1
    record = created[0]
    assert record.action == "BUY_CANDIDATE"
    assert record.target_low == pytest.approx(108.0)
    assert record.target_high == pytest.approx(144.0)
    assert record.suggested_notional == pytest.approx(100_000.0)
    notification = session.scalar(
        select(Notification).where(Notification.ref_id == f"alert:{record.id}")
    )
    assert notification is not None
    assert notification.kind == "alert"
    assert notification.title == "600519 · 买入候选"
    payload = _alert_payload(record)
    assert payload["target_low"] == pytest.approx(108.0)
    assert payload["target_high"] == pytest.approx(144.0)
    assert payload["suggested_notional"] == pytest.approx(100_000.0)


def test_refresh_falls_back_to_latest_nonfuture_close_and_preserves_sell_sign(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session.add(WatchlistItem(symbol=SYMBOL, display_name="贵州茅台"))
    session.add_all(
        [
            _daily_bar(SYMBOL, date(2026, 7, 18), 95.0),
            _daily_bar(SYMBOL, date(2026, 7, 22), 150.0),
        ]
    )
    session.commit()
    monkeypatch.setattr(
        watchlist_service,
        "forecast_for_symbol",
        lambda *_args, **_kwargs: _forecast(p_up_20d=0.30, volatility=0.15),
    )
    monkeypatch.setattr(
        watchlist_service,
        "get_settings",
        lambda: SimpleNamespace(demo_equity=1_000_000.0),
    )

    created = watchlist_service.refresh_alerts(session, SnapshotProvider([]))
    session.flush()

    regular = next(record for record in created if record.model_version == "forecast-v1.0.0")
    assert regular.action == "REDUCE"
    assert regular.target_low == pytest.approx(85.5)
    assert regular.target_high == pytest.approx(114.0)
    assert regular.suggested_notional == pytest.approx(-250_000.0)
    stored = session.scalar(select(AlertRecord).where(AlertRecord.id == regular.id))
    assert stored is regular
