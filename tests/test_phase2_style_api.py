from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.api.routes import style as style_route
from alphapilot.db.models import (
    Base,
    CompositeScore,
    DailyBar,
    FactorValue,
    ScreeningRun,
    Security,
    StyleDaily,
)
from alphapilot.engines.style import style_source_fingerprint
from alphapilot.main import app


@contextmanager
def _client(engine_url: str) -> Iterator[TestClient]:
    engine = create_engine(engine_url)
    Base.metadata.create_all(engine)

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[db_session_dependency] = override_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)


def test_style_daily_returns_only_available_points_oldest_first(tmp_path: Path) -> None:
    engine_url = f"sqlite:///{tmp_path / 'style-daily.db'}"
    engine = create_engine(engine_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for day, growth in ((1, 0.1), (2, 0.2), (3, 0.3)):
            session.add(
                StyleDaily(
                    trade_date=date(2026, 7, day),
                    growth_pct=growth,
                    value_pct=0.2,
                    defensive_pct=0.3,
                    balanced_pct=0.5 - growth,
                    model_version="style-v1.0.0",
                )
            )
        session.commit()

    with _client(engine_url) as client:
        response = client.get("/v1/style/daily?days=2")
        partial_history = client.get("/v1/style/daily?days=60")
        invalid_low = client.get("/v1/style/daily?days=0")
        invalid_high = client.get("/v1/style/daily?days=366")

    assert response.status_code == 200
    assert response.json() == {
        "requested_days": 2,
        "available_days": 2,
        "series": [
            {
                "trade_date": "2026-07-02",
                "growth_pct": 0.2,
                "value_pct": 0.2,
                "defensive_pct": 0.3,
                "balanced_pct": 0.3,
                "model_version": "style-v1.0.0",
            },
            {
                "trade_date": "2026-07-03",
                "growth_pct": 0.3,
                "value_pct": 0.2,
                "defensive_pct": 0.3,
                "balanced_pct": 0.2,
                "model_version": "style-v1.0.0",
            },
        ],
    }
    assert all(
        sum(point[key] for key in ("growth_pct", "value_pct", "defensive_pct", "balanced_pct"))
        == pytest.approx(1.0)
        for point in response.json()["series"]
    )
    assert invalid_low.status_code == 422
    assert invalid_high.status_code == 422
    assert partial_history.json()["requested_days"] == 60
    assert partial_history.json()["available_days"] == 3
    assert len(partial_history.json()["series"]) == 3


def test_style_exposure_uses_persisted_candidates_and_handles_legacy_runs(
    tmp_path: Path,
) -> None:
    engine_url = f"sqlite:///{tmp_path / 'style-exposure.db'}"
    engine = create_engine(engine_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        populated = ScreeningRun(
            universe="all",
            filters={},
            provider="factor-db",
            model_version="factor-score-v1.0.0",
            requested=5,
            succeeded=5,
            failed={},
            created_at=datetime(2026, 7, 1, tzinfo=UTC),
            candidates=[
                {"symbol": "000001", "style": "growth"},
                {"symbol": "000002", "style": "growth"},
                {"symbol": "000003", "style": "value"},
                {"symbol": "000004", "style": "defensive"},
                {"symbol": "000005", "style": "balanced"},
            ],
        )
        legacy = ScreeningRun(
            universe="all",
            filters={},
            provider="factor-db",
            model_version="factor-score-v1.0.0",
            requested=1,
            succeeded=1,
            failed={},
            created_at=datetime(2026, 7, 2, tzinfo=UTC),
            candidates=[{"symbol": "000006"}],
        )
        empty = ScreeningRun(
            universe="watchlist",
            filters={},
            provider="factor-db",
            model_version="factor-score-v1.0.0",
            requested=0,
            succeeded=0,
            failed={},
            created_at=datetime(2026, 7, 3, tzinfo=UTC),
            candidates=[],
        )
        session.add_all([populated, legacy, empty])
        session.commit()
        populated_id = populated.id
        legacy_id = legacy.id

    with _client(engine_url) as client:
        exposure = client.get(f"/v1/screens/style-exposure?run_id={populated_id}")
        legacy_response = client.get(f"/v1/screens/style-exposure?run_id={legacy_id}")
        latest_empty = client.get("/v1/screens/style-exposure")
        missing = client.get("/v1/screens/style-exposure?run_id=999999")

    assert exposure.status_code == 200
    assert exposure.json()["total_candidates"] == 5
    assert exposure.json()["exposure"] == [
        {"style": "growth", "count": 2, "pct": 0.4},
        {"style": "value", "count": 1, "pct": 0.2},
        {"style": "defensive", "count": 1, "pct": 0.2},
        {"style": "balanced", "count": 1, "pct": 0.2},
    ]

    assert legacy_response.status_code == 503
    assert "缺少候选风格快照" in legacy_response.json()["detail"]
    assert latest_empty.status_code == 200
    assert latest_empty.json()["total_candidates"] == 0
    assert latest_empty.json()["exposure"] == [
        {"style": "growth", "count": 0, "pct": 0.0},
        {"style": "value", "count": 0, "pct": 0.0},
        {"style": "defensive", "count": 0, "pct": 0.0},
        {"style": "balanced", "count": 0, "pct": 0.0},
    ]
    assert missing.status_code == 404
    assert "未找到选股运行记录" in missing.json()["detail"]


def test_style_daily_rejects_same_date_stale_factor_inputs(tmp_path: Path) -> None:
    engine_url = f"sqlite:///{tmp_path / 'style-stale.db'}"
    engine = create_engine(engine_url)
    Base.metadata.create_all(engine)
    trade_date = date(2026, 7, 21)
    with Session(engine) as session:
        session.add(Security(symbol="600001", industry_csrc="科技", list_status="listed"))
        session.add(
            CompositeScore(
                symbol="600001",
                trade_date=trade_date,
                score=80.0,
                factors={},
                model_version="factor-score-v1.0.0",
            )
        )
        session.add(
            DailyBar(
                symbol="600001",
                trade_date=trade_date,
                open=10.0,
                high=10.0,
                low=10.0,
                close=10.0,
                volume=100.0,
                amount=1000.0,
                source="test",
            )
        )
        factor = FactorValue(
            symbol="600001",
            trade_date=trade_date,
            factor="volatility_20d",
            raw=0.1,
            zscore=0.0,
            model_version="factor-v1.0.0",
        )
        session.add(factor)
        session.flush()
        session.add(
            StyleDaily(
                trade_date=trade_date,
                growth_pct=0.0,
                value_pct=0.0,
                defensive_pct=0.0,
                balanced_pct=1.0,
                source_fingerprint=style_source_fingerprint(session, trade_date),
            )
        )
        session.commit()

    with _client(engine_url) as client:
        current = client.get("/v1/style/daily")
        with Session(engine) as session:
            factor = (
                session.query(FactorValue)
                .filter_by(
                    symbol="600001",
                    trade_date=trade_date,
                    factor="volatility_20d",
                )
                .one()
            )
            factor.zscore = -1.0
            session.commit()
        stale = client.get("/v1/style/daily")

    assert current.status_code == 200
    assert stale.status_code == 503
    assert "compute_style_daily" in stale.json()["detail"]


def test_style_daily_rejects_inputs_changed_while_reading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine_url = f"sqlite:///{tmp_path / 'style-race-api.db'}"
    engine = create_engine(engine_url)
    Base.metadata.create_all(engine)
    trade_date = date(2026, 7, 21)
    fingerprint = "a" * 64
    with Session(engine) as session:
        session.add(
            CompositeScore(
                symbol="600001",
                trade_date=trade_date,
                score=80.0,
                factors={},
                model_version="factor-score-v1.0.0",
            )
        )
        session.add(
            StyleDaily(
                trade_date=trade_date,
                growth_pct=1.0,
                value_pct=0.0,
                defensive_pct=0.0,
                balanced_pct=0.0,
                source_fingerprint=fingerprint,
            )
        )
        session.commit()

    fingerprints = iter((fingerprint, "b" * 64))
    monkeypatch.setattr(
        style_route,
        "style_source_fingerprint",
        lambda *_args: next(fingerprints),
    )
    with _client(engine_url) as client:
        response = client.get("/v1/style/daily")

    assert response.status_code == 503
    assert "读取序列期间发生变化" in response.json()["detail"]
