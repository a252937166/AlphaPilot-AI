from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.api.routes import alerts as alert_routes
from alphapilot.api.routes import watchlist as watchlist_routes
from alphapilot.data.mock import MockMarketDataProvider
from alphapilot.db.models import Base, CalendarEvent, DomainEvent, Security, WatchlistItem
from alphapilot.services import watchlist as watchlist_service


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    database = create_engine(
        f"sqlite:///{tmp_path / 'watchlist-upgrade.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(database)
    return database


def _session_override(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def _event(
    symbol: str,
    event_type: str,
    title: str,
    occurred_at: datetime,
) -> DomainEvent:
    return DomainEvent(
        symbol=symbol,
        event_type=event_type,
        direction=0.2,
        strength=0.7,
        title=title,
        summary=f"{title}摘要",
        source_ref=f"fixture:{symbol}:{event_type}:{title}",
        occurred_at=occurred_at,
    )


def test_track_api_returns_top_three_events_and_audited_industry(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 22, 7, 0, tzinfo=UTC)
    with Session(engine) as session:
        session.add_all(
            [
                WatchlistItem(symbol="600519", display_name="贵州茅台"),
                WatchlistItem(symbol="300750", display_name="宁德时代"),
                Security(symbol="600519", industry_csrc="C15酒、饮料和精制茶制造业"),
                Security(symbol="300750", industry_csrc=None),
                _event("600519", "thesis_shift", "较早逻辑漂移", now - timedelta(hours=4)),
                _event("600519", "disclosure", "最新公告", now - timedelta(hours=3)),
                CalendarEvent(
                    symbol="600519",
                    event_type="dividend",
                    event_date=(now + timedelta(days=1)).date(),
                    title="分红日历",
                    source="baostock",
                    available_time=now - timedelta(hours=2),
                ),
                _event("600519", "capital_anomaly", "资金异动", now - timedelta(hours=1)),
                _event("300750", "thesis_shift", "逻辑状态变化", now),
            ]
        )
        session.commit()

    app = FastAPI()
    app.include_router(watchlist_routes.router)

    def override_session() -> Iterator[Session]:
        yield from _session_override(engine)

    app.dependency_overrides[db_session_dependency] = override_session
    monkeypatch.setattr(
        watchlist_routes,
        "get_provider",
        lambda _name=None: MockMarketDataProvider(),
    )

    with TestClient(app) as api:
        response = api.get("/v1/watchlist/track?provider=mock")

    assert response.status_code == 200
    rows = {row["symbol"]: row for row in response.json()["rows"]}
    maotai = rows["600519"]
    assert maotai["industry"] == "C15酒、饮料和精制茶制造业"
    assert [event["title"] for event in maotai["recent_events"]] == [
        "分红日历",
        "资金异动",
        "最新公告",
    ]
    assert [event["category"] for event in maotai["recent_events"]] == [
        "calendar",
        "capital",
        "announcement",
    ]
    assert all(event["occurred_at"].endswith("+00:00") for event in maotai["recent_events"])
    assert rows["300750"]["industry"] == "未分类"
    assert rows["300750"]["recent_events"] == []


def test_empty_scoped_refresh_is_a_noop(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(engine) as session:
        session.add(WatchlistItem(symbol="600519", display_name="贵州茅台"))
        session.commit()

        def unexpected_forecast(*_args: object, **_kwargs: object) -> Any:
            raise AssertionError("empty scoped refresh must not forecast any symbol")

        monkeypatch.setattr(watchlist_service, "forecast_for_symbol", unexpected_forecast)
        created = watchlist_service.refresh_alerts(
            session,
            MockMarketDataProvider(),
            symbols=[],
        )

    assert created == []


def test_refresh_api_scopes_selected_symbols_and_rejects_untracked(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(engine) as session:
        session.add_all(
            [
                WatchlistItem(symbol="600519", display_name="贵州茅台"),
                WatchlistItem(symbol="300750", display_name="宁德时代"),
            ]
        )
        session.commit()

    app = FastAPI()
    app.include_router(alert_routes.router)

    def override_session() -> Iterator[Session]:
        yield from _session_override(engine)

    app.dependency_overrides[db_session_dependency] = override_session
    provider_calls: list[str | None] = []

    def fake_provider(name: str | None = None) -> MockMarketDataProvider:
        provider_calls.append(name)
        return MockMarketDataProvider()

    monkeypatch.setattr(alert_routes, "get_provider", fake_provider)
    calls: list[list[str] | None] = []

    def fake_refresh(
        _session: Session,
        _provider: MockMarketDataProvider,
        *,
        symbols: list[str] | None = None,
    ) -> list[Any]:
        calls.append(symbols)
        return []

    monkeypatch.setattr(watchlist_service, "refresh_alerts", fake_refresh)

    with TestClient(app) as api:
        selected = api.post(
            "/v1/alerts/refresh?provider=mock",
            json={"symbols": ["SH.600519", "600519"]},
        )
        untracked = api.post(
            "/v1/alerts/refresh?provider=mock",
            json={"symbols": ["000001"]},
        )
        empty = api.post(
            "/v1/alerts/refresh?provider=mock",
            json={"symbols": []},
        )
        all_items = api.post("/v1/alerts/refresh?provider=mock")

    assert selected.status_code == 200
    assert untracked.status_code == 422
    assert "尚未加入自选" in untracked.json()["detail"]
    assert empty.status_code == 422
    assert all_items.status_code == 200
    assert calls == [["600519"], None]
    assert provider_calls == ["mock", "mock"]
