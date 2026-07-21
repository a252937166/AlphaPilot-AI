from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.db.models import (
    Base,
    DailyBar,
    SectorConstituent,
    SectorFlowDaily,
    SectorForecast,
)
from alphapilot.engines.sector_forecast import (
    MODEL_VERSION,
    NO_FLOW_MODEL_VERSION,
    build_sector_index,
    classify_lifecycle,
    clear_sector_index_cache,
    compute_sector_forecasts,
    normalize_constituent_symbol,
    rolling_validate,
    score_sector_panel,
)
from alphapilot.jobs import sector_forecast as sector_job
from alphapilot.jobs import sectors_sync
from alphapilot.jobs.registry import JOBS, JobExecutionError
from alphapilot.main import app


@contextmanager
def _local_session(engine: Any) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def _session_factory(engine: Any) -> Any:
    @contextmanager
    def factory() -> Iterator[Session]:
        with _local_session(engine) as session:
            yield session

    return factory


def _seed_toy_market(engine: Any, *, with_flow: bool = False) -> date:
    Base.metadata.create_all(engine)
    target = date(2026, 7, 21)
    dates = [value.date() for value in pd.bdate_range(end=target, periods=121)]
    plates = [
        ("SH.LISTA", "上行板块", ("SH.600001", "SH.600002"), "up"),
        ("SH.LISTB", "震荡板块", ("SZ.000001", "SZ.000002"), "wave"),
        ("SH.LISTC", "下行板块", ("SZ.300001", "SZ.300002"), "down"),
    ]
    with Session(engine) as session:
        for plate_code, plate_name, symbols, _ in plates:
            session.add_all(
                SectorConstituent(
                    plate_code=plate_code,
                    plate_name=plate_name,
                    symbol=symbol,
                    refreshed_at=datetime(2026, 7, 21, tzinfo=UTC),
                )
                for symbol in symbols
            )
        for index, trade_day in enumerate(dates):
            benchmark = 3_000.0 * 1.001**index
            session.add(
                DailyBar(
                    symbol="SH.000001",
                    trade_date=trade_day,
                    open=benchmark,
                    high=benchmark,
                    low=benchmark,
                    close=benchmark,
                    volume=1.0,
                    amount=1.0,
                    source="test",
                )
            )
            for plate_index, (plate_code, _, symbols, path) in enumerate(plates):
                for member_index, futu_symbol in enumerate(symbols):
                    if path == "up":
                        close = 100.0 * 1.006**index
                    elif path == "down":
                        close = 100.0 * 0.996**index
                    else:
                        close = 100.0 * (1.0 + 0.035 * math.sin(index / 5.0))
                    close *= 1.0 + member_index * 0.0001
                    session.add(
                        DailyBar(
                            symbol=futu_symbol.split(".", 1)[1],
                            trade_date=trade_day,
                            open=close,
                            high=close * 1.01,
                            low=close * 0.99,
                            close=close,
                            volume=1_000.0,
                            amount=close * 1_000.0,
                            source="test",
                        )
                    )
                if with_flow and trade_day in dates[-84:]:
                    session.add(
                        SectorFlowDaily(
                            plate_code=plate_code,
                            trade_date=trade_day,
                            net_inflow=(plate_index + 1) * 1_000_000.0 * (1.0 + index / 100.0),
                            main_inflow=None,
                            source="test",
                        )
                    )
        session.commit()
    return target


def test_sector_forecast_model_and_futu_symbol_normalization(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-model.db'}")
    Base.metadata.create_all(engine)

    assert inspect(engine).has_table("sector_forecasts")
    assert normalize_constituent_symbol("SH.600519") == "600519"
    assert normalize_constituent_symbol("SZ.000001") == "000001"
    assert normalize_constituent_symbol("600519") == "600519"
    assert normalize_constituent_symbol("HK.00700") is None
    assert normalize_constituent_symbol("SH.BAD") is None


def test_score_is_cross_sectional_and_cannot_see_future_rows() -> None:
    dates = [value.date() for value in pd.bdate_range("2026-06-01", periods=10)]
    records: list[dict[str, Any]] = []
    for plate_index, plate_code in enumerate(("A", "B", "C"), 1):
        for day_index, trade_day in enumerate(dates):
            records.append(
                {
                    "plate_code": plate_code,
                    "trade_date": trade_day,
                    "breadth": plate_index / 3 + day_index / 100,
                    "rs": float(plate_index),
                    "mom_5": plate_index / 100,
                }
            )
    original = pd.DataFrame.from_records(records)
    changed = original.copy()
    changed.loc[changed["trade_date"] > dates[5], ["breadth", "rs", "mom_5"]] = 999.0

    first = score_sector_panel(original, 5, use_flow=False)
    second = score_sector_panel(changed, 5, use_flow=False)
    first_scores = first[first["trade_date"] == dates[5]].sort_values("plate_code")["score"]
    second_scores = second[second["trade_date"] == dates[5]].sort_values("plate_code")["score"]

    assert first_scores.tolist() == second_scores.tolist()
    assert first_scores.tolist() == pytest.approx([100 / 3, 200 / 3, 100.0])


def test_rolling_validation_uses_exact_ceil_top20_and_strict_median_win() -> None:
    dates = [value.date() for value in pd.bdate_range("2026-01-01", periods=65)]
    rows: list[dict[str, Any]] = []
    for plate_code, daily_growth, score in (
        ("A", 0.0, 100.0),
        ("B", 0.02, 100.0),
        ("C", 0.01, 50.0),
        ("D", 0.0, 40.0),
        ("E", -0.01, 30.0),
    ):
        for index, trade_day in enumerate(dates):
            rows.append(
                {
                    "plate_code": plate_code,
                    "trade_date": trade_day,
                    "sector_index": 100.0 * (1.0 + daily_growth) ** index,
                    "score": score,
                }
            )
    result = rolling_validate(pd.DataFrame.from_records(rows), 5)

    assert result.origins == 60
    assert result.samples == 60
    assert result.win_rate == 0.0
    assert result.expected_excess == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        ({"score": 81.0, "score_trend": 1.0, "rsi14": 71.0, "flow_5d": 1.0}, "boom"),
        ({"score": 70.0, "score_trend": 1.0, "rsi14": 60.0, "flow_5d": None}, "rising"),
        ({"score": 50.0, "score_trend": -1.0, "rsi14": 50.0, "flow_5d": 1.0}, "decline"),
        ({"score": 20.0, "score_trend": 0.0, "rsi14": 40.0, "flow_5d": None}, "bottoming"),
        ({"score": 50.0, "score_trend": 1.0, "rsi14": 40.0, "flow_5d": 1.0}, "recovery"),
        ({"score": 50.0, "score_trend": 1.0, "rsi14": 40.0, "flow_5d": None}, None),
        ({"score": 70.0, "score_trend": 0.0, "rsi14": 80.0, "flow_5d": 1.0}, None),
    ],
)
def test_lifecycle_rules_keep_unmatched_states_null(
    inputs: dict[str, float | None], expected: str | None
) -> None:
    assert classify_lifecycle(**inputs) == expected  # type: ignore[arg-type]


def test_real_price_backtest_degrades_without_inventing_flow(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-no-flow.db'}")
    target = _seed_toy_market(engine)
    clear_sector_index_cache()

    with Session(engine) as session:
        panel = build_sector_index(session, target)
        result = compute_sector_forecasts(session, target)

    assert panel.attrs["eligible_plates"] == 3
    assert panel.attrs["membership_rows"] == 6
    assert panel.attrs["minimum_coverage"] == pytest.approx(1.0)
    assert result.stats["model_version"] == NO_FLOW_MODEL_VERSION
    assert result.stats["flow"]["complete_days"] == 0
    assert result.stats["index"]["sessions"] == 120
    assert len(result.rows) == 9
    assert {row["horizon"] for row in result.rows} == {5, 10, 20}
    assert all(row["reversal_score"] is None for row in result.rows)
    for horizon in (5, 10, 20):
        rows = [row for row in result.rows if row["horizon"] == horizon]
        assert len(rows) == 3
        assert max(rows, key=lambda row: float(row["score"]))["plate_code"] == "SH.LISTA"
        validation = result.stats["validation"][str(horizon)]
        assert validation["origins"] == 60
        assert validation["samples"] == 60
        assert 0.0 <= validation["win_rate"] <= 1.0


def test_full_flow_model_requires_complete_auditable_history(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-full-flow.db'}")
    target = _seed_toy_market(engine, with_flow=True)
    clear_sector_index_cache()

    with Session(engine) as session:
        result = compute_sector_forecasts(session, target)

    assert result.stats["model_version"] == MODEL_VERSION
    assert result.stats["flow"]["complete_days"] == 84
    assert result.stats["flow"]["full_model"] is True
    assert all(row["reversal_score"] is not None for row in result.rows)


def test_job_is_idempotent_and_api_exposes_truthful_degradation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-job.db'}")
    target = _seed_toy_market(engine)
    local_session = _session_factory(engine)
    clear_sector_index_cache()
    monkeypatch.setattr(sector_job, "get_session", local_session)
    monkeypatch.setattr(sector_job, "_market_today", lambda: target)

    first = sector_job.compute_sector_forecast()
    second = sector_job.compute_sector_forecast()

    assert first["rows"] == 9
    assert second["rows"] == 9
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SectorForecast)) == 9

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[db_session_dependency] = override_session
    try:
        client = TestClient(app)
        forecast = client.get("/v1/sectors/forecast?horizon=5")
        lifecycle = client.get("/v1/sectors/lifecycle")
        overbought = client.get("/v1/sectors/overbought")
        reversal = client.get("/v1/sectors/reversal")
        invalid = client.get("/v1/sectors/forecast?horizon=7")
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)

    assert forecast.status_code == 200
    assert forecast.json()["count"] == 3
    assert forecast.json()["model_version"] == NO_FLOW_MODEL_VERSION
    assert "未补零" in forecast.json()["degraded_reason"]
    assert lifecycle.status_code == 200
    assert sum(lifecycle.json()["counts"].values()) == 3
    assert overbought.status_code == 200
    assert reversal.status_code == 200
    assert reversal.json()["available"] is False
    assert reversal.json()["rows"] == []
    assert "flow_turn_z" in reversal.json()["reason"]
    assert invalid.status_code == 422


def test_job_skips_stale_bars_and_rejects_inputs_changed_during_compute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-job-guards.db'}")
    target = _seed_toy_market(engine)
    local_session = _session_factory(engine)
    clear_sector_index_cache()
    monkeypatch.setattr(sector_job, "get_session", local_session)
    monkeypatch.setattr(sector_job, "_market_today", lambda: target + timedelta(days=1))

    stale = sector_job.compute_sector_forecast()

    assert stale["skipped"] == "stale_daily_bars"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SectorForecast)) == 0

    monkeypatch.setattr(sector_job, "_market_today", lambda: target)
    fingerprints = iter(("a" * 64, "b" * 64))
    monkeypatch.setattr(sector_job, "_input_fingerprint", lambda *_args: next(fingerprints))
    with pytest.raises(JobExecutionError, match="输入发生变化"):
        sector_job.compute_sector_forecast()
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SectorForecast)) == 0


def test_sector_flow_uses_futu_calendar_and_skips_non_trading_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = date(2026, 7, 19)

    class CalendarClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, list[Any] | None]] = []

        def quote_call_raw(
            self,
            method: str,
            args: list[Any] | None = None,
            kwargs: Any = None,
        ) -> Any:
            del kwargs
            self.calls.append((method, args))
            return []

    client = CalendarClient()
    monkeypatch.setattr(sectors_sync, "get_futu_client", lambda: client)
    monkeypatch.setattr(sectors_sync, "_market_today", lambda: target)

    result = sectors_sync.sync_sector_flows(pause_seconds=0)

    assert result["skipped"] == "non_trading_day"
    assert result["trade_date"] is None
    assert client.calls == [
        ("request_trading_days", ["CN", target.isoformat(), target.isoformat()])
    ]


def test_sector_forecast_job_runs_after_daily_inputs() -> None:
    sector_job.register_sector_forecast_job()
    job = JOBS["sector_forecast"]
    assert "day_of_week='mon-fri'" in str(job.trigger)
    assert "hour='19'" in str(job.trigger)
    assert "minute='50'" in str(job.trigger)
