from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.db.migrate import run_migrations
from alphapilot.db.models import (
    Base,
    CompositeScore,
    DailyBar,
    FactorValue,
    Security,
)
from alphapilot.domain.models import ScreeningRequest
from alphapilot.main import app

TARGET_DATE = date(2026, 7, 21)


def test_screening_run_context_migration_upgrades_existing_schema(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-screens.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE securities (symbol TEXT PRIMARY KEY)"))
        connection.execute(
            text("CREATE TABLE screening_runs (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        )

    applied = run_migrations(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("screening_runs")}

    assert "screening_runs.universe" in applied
    assert "screening_runs.filters" in applied
    assert {"universe", "filters"}.issubset(columns)
    assert run_migrations(engine) == []


def _seed_screening_data(session: Session) -> None:
    trade_dates = [stamp.date() for stamp in pd.bdate_range(end=TARGET_DATE, periods=61)]
    securities = [
        ("600001", "测试一", "制造业", 100.0, 98.0, -1.0, 1.0),
        ("600002", "测试二", "制造业", 200.0, 88.0, 0.0, 0.0),
        ("600003", "测试三", "金融业", 300.0, 78.0, 1.0, -1.0),
    ]
    for index, (symbol, name, industry, market_cap, score, volatility, momentum) in enumerate(
        securities
    ):
        session.add(
            Security(
                symbol=symbol,
                market="CN",
                name=name,
                industry_csrc=industry,
                board="主板",
                is_st=False,
                list_status="listed",
                market_cap=market_cap,
            )
        )
        session.add(
            CompositeScore(
                symbol=symbol,
                trade_date=TARGET_DATE,
                score=score,
                win_rate_20d=None,
                factors={"momentum_20d": momentum, "volatility_20d": volatility},
                model_version="factor-score-v1.0.0",
            )
        )
        session.add(
            FactorValue(
                symbol=symbol,
                trade_date=TARGET_DATE,
                factor="volatility_20d",
                raw=0.2 + index * 0.1,
                zscore=volatility,
                model_version="factor-v1.0.0",
            )
        )
        for day_index, trade_day in enumerate(trade_dates):
            close = 10.0 + index + day_index * (0.01 + index * 0.001)
            session.add(
                DailyBar(
                    symbol=symbol,
                    trade_date=trade_day,
                    open=close,
                    high=close * 1.01,
                    low=close * 0.99,
                    close=close,
                    volume=1000.0 + day_index,
                    amount=close * (1000.0 + day_index),
                    source="test",
                )
            )


def test_screening_request_preserves_legacy_symbols_mode() -> None:
    symbols = [f"{600000 + index:06d}" for index in range(25)]
    request = ScreeningRequest.model_validate({"symbols": symbols})

    assert request.universe == "custom"
    assert request.symbols == symbols
    assert request.top_n == 20


def test_full_market_screen_api_persists_and_diffs(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'screens-api.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_screening_data(session)
        session.commit()

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[db_session_dependency] = override_session
    client = TestClient(app)
    try:
        first = client.post(
            "/v1/screens/run",
            json={"universe": "all", "top_n": 2, "industries": None},
        )
        first_diff = client.get("/v1/screens/diff")
        custom = client.post(
            "/v1/screens/run",
            json={
                "symbols": ["600001", "600002", "600003"],
                "top_n": 2,
                "provider": "mock",
            },
        )
        custom_filter_error = client.post(
            "/v1/screens/run",
            json={
                "universe": "custom",
                "symbols": ["600001"],
                "industries": ["制造业"],
                "provider": "mock",
            },
        )
        custom_style_error = client.post(
            "/v1/screens/run",
            json={
                "universe": "custom",
                "symbols": ["600001"],
                "style": "value",
                "provider": "mock",
            },
        )
        second = client.post(
            "/v1/screens/run",
            json={"universe": "all", "top_n": 2, "industries": None},
        )
        second_diff = client.get("/v1/screens/diff")
        style_error = client.post(
            "/v1/screens/run",
            json={"universe": "all", "top_n": 2, "style": "value"},
        )
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)

    assert first.status_code == 200
    assert second.status_code == 200
    body = first.json()
    assert body["requested"] == 3
    assert len(body["candidates"]) == 2
    assert [item["symbol"] for item in body["candidates"]] == ["600001", "600002"]
    assert all(item["p_up_20d"] is not None for item in body["candidates"])
    forecast_sources = [item["forecast_source"] for item in body["candidates"]]
    assert forecast_sources == ["daily_bars-cache", "daily_bars-cache"]

    assert first_diff.status_code == 200
    assert first_diff.json()["baseline_missing"] is True
    assert first_diff.json()["new"] == ["600001", "600002"]
    assert custom.status_code == 200
    assert custom_filter_error.status_code == 422
    assert custom_filter_error.json()["detail"].startswith("custom 兼容模式不支持 industries")
    assert custom_style_error.status_code == 422
    assert "P2.2-S4" in custom_style_error.json()["detail"]

    assert second_diff.status_code == 200
    assert second_diff.json()["baseline_missing"] is False
    assert second_diff.json()["previous_run_id"] == first_diff.json()["current_run_id"]
    assert second_diff.json()["new"] == []
    assert second_diff.json()["dropped"] == []
    assert second_diff.json()["stayed"] == 2

    assert style_error.status_code == 422
    assert "S4" in style_error.json()["detail"]
