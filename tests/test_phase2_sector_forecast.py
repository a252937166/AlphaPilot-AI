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
from sqlalchemy import create_engine, delete, func, inspect, select
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.db.models import (
    Base,
    DailyBar,
    JobRun,
    SectorConstituent,
    SectorConstituentSnapshot,
    SectorFlowDaily,
    SectorForecast,
    SectorSnapshot,
)
from alphapilot.engines.sector_forecast import (
    MODEL_VERSION,
    NO_FLOW_MODEL_VERSION,
    SectorForecastError,
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
                    source="baostock",
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
                            source="baostock",
                        )
                    )
                if with_flow and trade_day in dates[-84:]:
                    session.add(
                        SectorFlowDaily(
                            plate_code=plate_code,
                            trade_date=trade_day,
                            net_inflow=(plate_index + 1) * 1_000_000.0 * (1.0 + index / 100.0),
                            main_inflow=None,
                            source="futu-top5",
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


@pytest.mark.parametrize("source", ["test", "mock-fallback"])
def test_sector_forecast_rejects_untrusted_daily_bar_sources(
    tmp_path: Path,
    source: str,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / f'untrusted-{source}.db'}")
    target = _seed_toy_market(engine)
    with Session(engine) as session:
        for row in session.scalars(select(DailyBar)):
            row.source = source
        session.commit()
    clear_sector_index_cache()

    with Session(engine) as session, pytest.raises(SectorForecastError, match="交易日历只有 0 日"):
        compute_sector_forecasts(session, target)


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
        forecasts = {
            horizon: client.get(f"/v1/sectors/forecast?horizon={horizon}")
            for horizon in (5, 10, 20)
        }
        lifecycle = client.get("/v1/sectors/lifecycle")
        overbought = client.get("/v1/sectors/overbought")
        reversal = client.get("/v1/sectors/reversal")
        invalid = client.get("/v1/sectors/forecast?horizon=7")
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)

    assert all(response.status_code == 200 for response in forecasts.values())
    forecast = forecasts[5]
    assert forecast.json()["count"] == 3
    assert forecast.json()["model_version"] == NO_FLOW_MODEL_VERSION
    assert "未补零" in forecast.json()["degraded_reason"]
    assert all(
        row["win_rate"] is not None
        for response in forecasts.values()
        for row in response.json()["rows"]
    )
    assert len(
        {
            round(float(response.json()["rows"][0]["expected_excess"]), 8)
            for response in forecasts.values()
        }
    ) == 3
    assert all(
        row["net_inflow_5d"] is None and row["flow_coverage_days"] == 0
        for row in forecast.json()["rows"]
    )
    assert forecast.json()["input_trade_date"] == target.isoformat()
    assert forecast.json()["stale"] is False
    assert lifecycle.status_code == 200
    assert sum(lifecycle.json()["counts"].values()) == 3
    assert overbought.status_code == 200
    assert reversal.status_code == 200
    assert reversal.json()["available"] is False
    assert reversal.json()["rows"] == []
    assert "flow_turn_z" in reversal.json()["reason"]
    assert invalid.status_code == 422


def test_forecast_api_exposes_complete_flow_window_and_audited_leader(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-enriched.db'}")
    target = _seed_toy_market(engine, with_flow=True)
    local_session = _session_factory(engine)
    clear_sector_index_cache()
    monkeypatch.setattr(sector_job, "get_session", local_session)
    monkeypatch.setattr(sector_job, "_market_today", lambda: target)
    sector_job.compute_sector_forecast()
    snapshot_time = datetime(2026, 7, 10, 8, 0, tzinfo=UTC)
    with Session(engine) as session:
        session.add(
            SectorSnapshot(
                as_of=snapshot_time,
                source="futu",
                payload=[
                    {
                        "plate_code": "SH.LISTA",
                        "leader_code": "SH.600001",
                        "leader_name": "上行龙头",
                        "leader_change_pct": 2.5,
                    }
                ],
            )
        )
        session.commit()

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[db_session_dependency] = override_session
    try:
        response = TestClient(app).get("/v1/sectors/forecast?horizon=20")
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["flow_as_of"] == target.isoformat()
    assert payload["flow_window_days"] == 5
    assert len(payload["flow_window_dates"]) == 5
    assert payload["flow_window_dates"][-1] == target.isoformat()
    assert payload["strength_as_of"] == target.isoformat()
    assert payload["strength_source"] == "sector_forecasts"
    assert payload["leader_as_of"] == target.isoformat()
    assert payload["leader_source"] == "daily_bars"
    assert payload["model_expected_excess"] == pytest.approx(
        payload["rows"][0]["expected_excess"]
    )
    assert payload["model_expected_excess_scope"] == (
        "top-20pct-portfolio-historical-mean"
    )
    assert all(row["flow_coverage_days"] == 5 for row in payload["rows"])
    assert all(row["net_inflow_5d"] is not None for row in payload["rows"])
    assert all(row["flow_trade_date"] == target.isoformat() for row in payload["rows"])
    assert all(row["flow_missing_dates"] == [] for row in payload["rows"])
    assert {row["flow_source"] for row in payload["rows"]} == {"futu-top5"}
    enriched = next(row for row in payload["rows"] if row["plate_code"] == "SH.LISTA")
    assert enriched["leader_code"] == "600001"
    assert enriched["leader_name"] is None
    assert enriched["leader_change_pct"] == pytest.approx(0.6)
    assert enriched["leader_as_of"] == target.isoformat()
    assert enriched["leader_previous_trade_date"] == payload["flow_window_dates"][-2]
    assert enriched["leader_source"] == "daily_bars"
    assert enriched["leader_sources"] == ["baostock"]
    assert enriched["leader_membership_source"] == (
        "sector_constituents-visible-before-cutoff"
    )
    assert enriched["leader_coverage_ratio"] == pytest.approx(1.0)
    # The stale quote snapshot is deliberately contradictory and must be ignored.
    assert enriched["leader_change_pct"] != pytest.approx(2.5)


def test_forecast_leader_prefers_exact_pit_membership_for_forecast_date(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-pit-leader.db'}")
    target = _seed_toy_market(engine)
    local_session = _session_factory(engine)
    clear_sector_index_cache()
    monkeypatch.setattr(sector_job, "get_session", local_session)
    monkeypatch.setattr(sector_job, "_market_today", lambda: target)
    sector_job.compute_sector_forecast()
    with Session(engine) as session:
        session.add(
            SectorConstituentSnapshot(
                plate_code="SH.LISTA",
                symbol="SH.600002",
                as_of_date=target,
                available_time=datetime(2026, 7, 21, 7, 0, tzinfo=UTC),
            )
        )
        session.commit()

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[db_session_dependency] = override_session
    try:
        response = TestClient(app).get("/v1/sectors/forecast?horizon=5")
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)

    assert response.status_code == 200
    row = next(
        item for item in response.json()["rows"] if item["plate_code"] == "SH.LISTA"
    )
    assert row["leader_code"] == "600002"
    assert row["leader_as_of"] == target.isoformat()
    assert row["leader_membership_source"] == "sector_constituent_snapshots"
    assert row["leader_constituent_count"] == 1
    assert row["leader_eligible_members"] == 1


def test_forecast_leader_is_null_when_exact_daily_bar_coverage_is_insufficient(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-leader-gap.db'}")
    target = _seed_toy_market(engine)
    local_session = _session_factory(engine)
    clear_sector_index_cache()
    monkeypatch.setattr(sector_job, "get_session", local_session)
    monkeypatch.setattr(sector_job, "_market_today", lambda: target)
    sector_job.compute_sector_forecast()
    with Session(engine) as session:
        session.add_all(
            SectorConstituentSnapshot(
                plate_code="SH.LISTA",
                symbol=symbol,
                as_of_date=target,
                available_time=datetime(2026, 7, 21, 7, 0, tzinfo=UTC),
            )
            for symbol in ("SH.600001", "SH.600002")
        )
        session.execute(
            delete(DailyBar).where(
                DailyBar.symbol == "600002",
                DailyBar.trade_date == target,
            )
        )
        session.add(
            SectorSnapshot(
                as_of=datetime(2026, 7, 21, 8, 0, tzinfo=UTC),
                source="futu",
                payload=[
                    {
                        "plate_code": "SH.LISTA",
                        "leader_code": "SH.600002",
                        "leader_name": "不可采用的实时快照",
                        "leader_change_pct": 10.0,
                    }
                ],
            )
        )
        session.commit()

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[db_session_dependency] = override_session
    try:
        response = TestClient(app).get("/v1/sectors/forecast?horizon=5")
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)

    assert response.status_code == 200
    row = next(
        item for item in response.json()["rows"] if item["plate_code"] == "SH.LISTA"
    )
    assert row["leader_code"] is None
    assert row["leader_change_pct"] is None
    assert row["leader_as_of"] is None
    assert row["leader_source"] is None
    assert row["leader_eligible_members"] == 1
    assert row["leader_constituent_count"] == 2
    assert row["leader_coverage_ratio"] == pytest.approx(0.5)
    assert "低于 80% 门槛" in row["leader_unavailable_reason"]


def test_forecast_leader_does_not_backdate_future_constituent_refresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-future-membership.db'}")
    target = _seed_toy_market(engine)
    local_session = _session_factory(engine)
    clear_sector_index_cache()
    monkeypatch.setattr(sector_job, "get_session", local_session)
    monkeypatch.setattr(sector_job, "_market_today", lambda: target)
    sector_job.compute_sector_forecast()
    with Session(engine) as session:
        members = session.scalars(
            select(SectorConstituent).where(
                SectorConstituent.plate_code == "SH.LISTA"
            )
        ).all()
        for member in members:
            member.refreshed_at = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)
        session.add(
            SectorSnapshot(
                as_of=datetime(2026, 7, 21, 8, 0, tzinfo=UTC),
                source="futu",
                payload=[
                    {
                        "plate_code": "SH.LISTA",
                        "leader_code": "SH.600001",
                        "leader_name": "不可回填的快照龙头",
                        "leader_change_pct": 10.0,
                    }
                ],
            )
        )
        session.commit()

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[db_session_dependency] = override_session
    try:
        response = TestClient(app).get("/v1/sectors/forecast?horizon=5")
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)

    assert response.status_code == 200
    row = next(
        item for item in response.json()["rows"] if item["plate_code"] == "SH.LISTA"
    )
    assert row["leader_code"] is None
    assert row["leader_change_pct"] is None
    assert row["leader_membership_source"] == "unavailable"
    assert "没有可审计的板块成分" in row["leader_unavailable_reason"]


def test_forecast_api_serves_explicit_stale_snapshot_only_for_partial_latest_day(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-partial-latest.db'}")
    target = _seed_toy_market(engine)
    next_day = target + timedelta(days=1)
    local_session = _session_factory(engine)
    clear_sector_index_cache()
    monkeypatch.setattr(sector_job, "get_session", local_session)
    monkeypatch.setattr(sector_job, "_market_today", lambda: target)
    sector_job.compute_sector_forecast()
    with Session(engine) as session:
        session.add_all(
            [
                DailyBar(
                    symbol="SH.000001",
                    trade_date=next_day,
                    open=3_001.0,
                    high=3_001.0,
                    low=3_001.0,
                    close=3_001.0,
                    volume=1.0,
                    amount=1.0,
                    source="baostock",
                ),
                DailyBar(
                    symbol="600001",
                    trade_date=next_day,
                    open=101.0,
                    high=101.0,
                    low=101.0,
                    close=101.0,
                    volume=1.0,
                    amount=1.0,
                    source="baostock",
                ),
            ]
        )
        session.add_all(
            SectorForecast(
                plate_code="SH.LISTA",
                plate_name="坏截面",
                trade_date=next_day,
                horizon=horizon,
                score=99.0,
                model_version=NO_FLOW_MODEL_VERSION,
            )
            for horizon in (5, 10, 20)
        )
        session.commit()

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[db_session_dependency] = override_session
    try:
        client = TestClient(app)
        partial = client.get("/v1/sectors/forecast?horizon=5")
        with Session(engine) as session:
            session.execute(
                delete(SectorForecast).where(SectorForecast.trade_date == next_day)
            )
            for symbol in ("600002", "000001", "000002", "300001", "300002"):
                session.add(
                    DailyBar(
                        symbol=symbol,
                        trade_date=next_day,
                        open=101.0,
                        high=101.0,
                        low=101.0,
                        close=101.0,
                        volume=1.0,
                        amount=1.0,
                        source="baostock",
                    )
                )
            session.commit()
        complete = client.get("/v1/sectors/forecast?horizon=5")
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)

    assert partial.status_code == 200
    assert partial.json()["as_of"] == target.isoformat()
    assert partial.json()["input_trade_date"] == next_day.isoformat()
    assert partial.json()["stale"] is True
    assert partial.json()["ignored_forecast_dates"] == [next_day.isoformat()]
    assert partial.json()["input_coverage"] == {
        "latest_symbol_count": 1,
        "forecast_symbol_count": 6,
        "reference_trade_date": target.isoformat(),
        "reference_symbol_count": 6,
        "ratio": pytest.approx(1 / 6, abs=1e-6),
        "minimum_ratio": 0.8,
    }
    assert "未将部分截面当作新预测" in partial.json()["warning"]
    assert complete.status_code == 503
    assert "最新完整交易日日线" in complete.json()["detail"]


def test_sector_forecast_job_rejects_partial_same_day_before_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-partial-job.db'}")
    target = _seed_toy_market(engine)
    next_day = target + timedelta(days=1)
    with Session(engine) as session:
        session.add_all(
            [
                DailyBar(
                    symbol="SH.000001",
                    trade_date=next_day,
                    open=3_001.0,
                    high=3_001.0,
                    low=3_001.0,
                    close=3_001.0,
                    volume=1.0,
                    amount=1.0,
                    source="baostock",
                ),
                DailyBar(
                    symbol="600001",
                    trade_date=next_day,
                    open=101.0,
                    high=101.0,
                    low=101.0,
                    close=101.0,
                    volume=1.0,
                    amount=1.0,
                    source="baostock",
                ),
            ]
        )
        session.commit()
    local_session = _session_factory(engine)
    monkeypatch.setattr(sector_job, "get_session", local_session)
    monkeypatch.setattr(sector_job, "_market_today", lambda: next_day)

    with pytest.raises(JobExecutionError, match="覆盖不足 80%") as caught:
        sector_job.compute_sector_forecast()
    assert caught.value.stats["skipped"] == "incomplete_daily_bars"
    assert caught.value.stats["input_coverage"]["ratio"] == pytest.approx(1 / 6, abs=1e-6)
    with Session(engine) as session:
        assert session.scalar(
            select(func.count()).select_from(SectorForecast).where(
                SectorForecast.trade_date == next_day
            )
        ) == 0


def test_consecutive_partial_days_cannot_validate_each_other(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-consecutive-partial.db'}")
    complete_day = _seed_toy_market(engine)
    partial_days = [complete_day + timedelta(days=offset) for offset in range(1, 9)]
    final_partial = partial_days[-1]
    with Session(engine) as session:
        for trade_day in partial_days:
            session.add_all(
                [
                    DailyBar(
                        symbol="SH.000001",
                        trade_date=trade_day,
                        open=3_001.0,
                        high=3_001.0,
                        low=3_001.0,
                        close=3_001.0,
                        volume=1.0,
                        amount=1.0,
                        source="baostock",
                    ),
                    DailyBar(
                        symbol="600001",
                        trade_date=trade_day,
                        open=101.0,
                        high=101.0,
                        low=101.0,
                        close=101.0,
                        volume=1.0,
                        amount=1.0,
                        source="baostock",
                    ),
                ]
            )
        session.commit()

    local_session = _session_factory(engine)
    monkeypatch.setattr(sector_job, "get_session", local_session)
    monkeypatch.setattr(sector_job, "_market_today", lambda: final_partial)

    with pytest.raises(JobExecutionError, match="覆盖不足 80%") as caught:
        sector_job.compute_sector_forecast()

    coverage = caught.value.stats["input_coverage"]
    assert coverage["symbol_count"] == 1
    assert coverage["reference_trade_date"] == complete_day.isoformat()
    assert coverage["reference_symbol_count"] == 6
    assert coverage["ratio"] == pytest.approx(1 / 6, abs=1e-6)
    with Session(engine) as session:
        assert session.scalar(
            select(func.count()).select_from(SectorForecast).where(
                SectorForecast.trade_date == final_partial
            )
        ) == 0


def test_service_rejects_consecutive_partial_days_as_a_complete_forecast(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-partial-service.db'}")
    complete_day = _seed_toy_market(engine)
    local_session = _session_factory(engine)
    clear_sector_index_cache()
    monkeypatch.setattr(sector_job, "get_session", local_session)
    monkeypatch.setattr(sector_job, "_market_today", lambda: complete_day)
    sector_job.compute_sector_forecast()
    first_partial = complete_day + timedelta(days=1)
    second_partial = complete_day + timedelta(days=2)
    with Session(engine) as session:
        for trade_day in (first_partial, second_partial):
            session.add_all(
                [
                    DailyBar(
                        symbol="SH.000001",
                        trade_date=trade_day,
                        open=3_001.0,
                        high=3_001.0,
                        low=3_001.0,
                        close=3_001.0,
                        volume=1.0,
                        amount=1.0,
                        source="baostock",
                    ),
                    DailyBar(
                        symbol="600001",
                        trade_date=trade_day,
                        open=101.0,
                        high=101.0,
                        low=101.0,
                        close=101.0,
                        volume=1.0,
                        amount=1.0,
                        source="baostock",
                    ),
                ]
            )
        session.add_all(
            SectorForecast(
                plate_code="SH.LISTA",
                plate_name="连续残缺坏截面",
                trade_date=second_partial,
                horizon=horizon,
                score=99.0,
                model_version=NO_FLOW_MODEL_VERSION,
            )
            for horizon in (5, 10, 20)
        )
        session.commit()

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[db_session_dependency] = override_session
    try:
        response = TestClient(app).get("/v1/sectors/forecast?horizon=5")
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["as_of"] == complete_day.isoformat()
    assert payload["input_trade_date"] == second_partial.isoformat()
    assert payload["stale"] is True
    assert payload["ignored_forecast_dates"] == [second_partial.isoformat()]
    assert payload["input_coverage"]["reference_trade_date"] == complete_day.isoformat()
    assert payload["input_coverage"]["reference_symbol_count"] == 6
    assert payload["input_coverage"]["ratio"] == pytest.approx(1 / 6, abs=1e-6)


def test_sector_flow_window_uses_exact_recent_benchmark_sessions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-flow-window.db'}")
    target = _seed_toy_market(engine)
    local_session = _session_factory(engine)
    clear_sector_index_cache()
    monkeypatch.setattr(sector_job, "get_session", local_session)
    monkeypatch.setattr(sector_job, "_market_today", lambda: target)
    sector_job.compute_sector_forecast()
    dates = [value.date() for value in pd.bdate_range(end=target, periods=35)]
    with Session(engine) as session:
        for trade_day in (dates[0], dates[1], dates[2], dates[-2], dates[-1]):
            session.add(
                SectorFlowDaily(
                    plate_code="SH.LISTA",
                    trade_date=trade_day,
                    net_inflow=1_000.0,
                    source="futu-top5",
                )
            )
        for trade_day in dates[-5:]:
            session.add(
                SectorFlowDaily(
                    plate_code="SH.LISTB",
                    trade_date=trade_day,
                    net_inflow=9_999.0,
                    source="test",
                )
            )
        session.commit()

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[db_session_dependency] = override_session
    try:
        response = TestClient(app).get("/v1/sectors/forecast?horizon=5")
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)

    assert response.status_code == 200
    payload = response.json()
    row = next(item for item in payload["rows"] if item["plate_code"] == "SH.LISTA")
    assert row["flow_coverage_days"] == 2
    assert row["net_inflow_5d"] is None
    assert row["flow_trade_date"] == target.isoformat()
    assert row["flow_available_dates"] == payload["flow_window_dates"][-2:]
    assert row["flow_missing_dates"] == payload["flow_window_dates"][:3]
    rejected = next(
        item for item in payload["rows"] if item["plate_code"] == "SH.LISTB"
    )
    assert rejected["flow_coverage_days"] == 0
    assert rejected["net_inflow_5d"] is None
    assert rejected["flow_trade_date"] is None
    assert rejected["flow_available_dates"] == []
    assert rejected["flow_missing_dates"] == payload["flow_window_dates"]


def test_sector_leaders_use_forecast_cutoff_and_exclude_invalid_correlations(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-leaders.db'}")
    Base.metadata.create_all(engine)
    target = date(2026, 7, 21)
    dates = [value.date() for value in pd.bdate_range(end=target, periods=21)]
    symbols = ("600001", "600002", "600003", "600004", "600005", "600006")
    leader_returns = [0.008 + (index % 4) * 0.002 for index in range(20)]
    mean_leader = sum(leader_returns) / len(leader_returns)
    return_paths = {
        "600001": leader_returns,
        "600002": [0.004 + 0.7 * (value - mean_leader) for value in leader_returns],
        "600003": [0.002 - 0.3 * (value - mean_leader) for value in leader_returns],
        "600004": [0.0] * 20,
        "600005": [0.003 + 0.5 * (value - mean_leader) for value in leader_returns],
        "600006": [
            0.003 + 0.4 * (value - mean_leader) + (0.0005 if index % 2 else -0.0005)
            for index, value in enumerate(leader_returns)
        ],
    }
    with Session(engine) as session:
        session.add_all(
            SectorConstituent(
                plate_code="SH.BKTEST",
                plate_name="测试板块",
                symbol=f"SH.{symbol}",
                name=f"股票{symbol}",
                refreshed_at=datetime(2026, 7, 20, 8, 0, tzinfo=UTC),
            )
            for symbol in symbols
        )
        session.add_all(
            [
                SectorConstituent(
                    plate_code="SH.BKEMPTY",
                    plate_name="空预测板块",
                    symbol="SH.601001",
                    refreshed_at=datetime(2026, 7, 20, 8, 0, tzinfo=UTC),
                ),
                SectorConstituent(
                    plate_code="SH.BKEMPTY",
                    plate_name="空预测板块",
                    symbol="SH.601002",
                    refreshed_at=datetime(2026, 7, 20, 8, 0, tzinfo=UTC),
                ),
            ]
        )
        session.add_all(
            SectorForecast(
                plate_code="SH.BKTEST",
                plate_name="测试板块",
                trade_date=target,
                horizon=horizon,
                score=88.0,
                expected_excess=0.02,
                win_rate=0.65,
                model_version=MODEL_VERSION,
            )
            for horizon in (5, 10, 20)
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
                    source="baostock",
                )
            )
        for symbol in symbols:
            close = 100.0
            closes = [close]
            for daily_return in return_paths[symbol]:
                close *= 1.0 + daily_return
                closes.append(close)
            for index, (trade_day, value) in enumerate(zip(dates, closes, strict=True)):
                source = (
                    "mock-fallback" if symbol == "600005" and index == 10 else "baostock"
                )
                session.add(
                    DailyBar(
                        symbol=symbol,
                        trade_date=trade_day,
                        open=value,
                        high=value,
                        low=value,
                        close=value,
                        volume=1.0,
                        amount=1.0,
                        source=source,
                    )
                )
        future = target + timedelta(days=1)
        session.add_all(
            [
                DailyBar(
                    symbol="SH.000001",
                    trade_date=future,
                    open=3_999.0,
                    high=3_999.0,
                    low=3_999.0,
                    close=3_999.0,
                    volume=1.0,
                    amount=1.0,
                    source="baostock",
                ),
                DailyBar(
                    symbol="600003",
                    trade_date=future,
                    open=999.0,
                    high=999.0,
                    low=999.0,
                    close=999.0,
                    volume=1.0,
                    amount=1.0,
                    source="baostock",
                ),
            ]
        )
        session.commit()

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[db_session_dependency] = override_session
    try:
        client = TestClient(app)
        response = client.get("/v1/sectors/SH.BKTEST/leaders")
        unknown = client.get("/v1/sectors/SH.BKMISSING/leaders")
        unavailable = client.get("/v1/sectors/SH.BKEMPTY/leaders")
        invalid = client.get("/v1/sectors/not-a-plate/leaders")
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)

    assert response.status_code == 200
    payload = response.json()
    assert payload["as_of"] == target.isoformat()
    assert payload["lookback_sessions"] == 20
    assert payload["method"] == "pearson-daily-return"
    assert payload["source"] == "daily_bars"
    assert payload["sources"] == ["baostock"]
    assert payload["mock_excluded"] is True
    assert payload["leader"]["symbol"] == "600001"
    assert payload["rows"][0]["symbol"] == "600002"
    assert payload["rows"][0]["correlation"] == pytest.approx(1.0)
    assert all(row["observations"] == 20 for row in payload["rows"])
    assert {row["symbol"] for row in payload["rows"]}.isdisjoint({"600004", "600005"})
    assert unknown.status_code == 404
    assert unavailable.status_code == 503
    assert invalid.status_code == 422


def test_sector_forecast_stale_upstream_lease_is_reported_not_blocking(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sector-stale-upstream.db'}")
    target = _seed_toy_market(engine)
    fixed_now = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    local_session = _session_factory(engine)
    with Session(engine) as session:
        stale = JobRun(
            job_name="sync_sector_flows",
            status="running",
            stats={},
            started_at=fixed_now - timedelta(hours=6),
        )
        session.add(stale)
        session.commit()
        stale_id = stale.id
    clear_sector_index_cache()
    monkeypatch.setattr(sector_job, "get_session", local_session)
    monkeypatch.setattr(sector_job, "_market_today", lambda: target)
    monkeypatch.setattr(sector_job, "_job_now", lambda: fixed_now)

    stats = sector_job.compute_sector_forecast()

    assert stats["rows"] == 9
    assert stats["warning_count"] == 1
    assert stats["stale_upstream_runs"] == [
        {
            "id": stale_id,
            "job_name": "sync_sector_flows",
            "started_at": (fixed_now - timedelta(hours=6)).isoformat(),
            "age_seconds": 21_600.0,
        }
    ]
    with Session(engine) as session:
        session.add(
            JobRun(
                job_name="sync_sector_flows",
                status="running",
                stats={},
                started_at=fixed_now - timedelta(minutes=10),
            )
        )
        session.commit()
    with pytest.raises(JobExecutionError, match="板块预测已延后") as caught:
        sector_job.compute_sector_forecast()
    assert caught.value.stats["running_jobs"] == ["sync_sector_flows"]
    assert len(caught.value.stats["stale_upstream_runs"]) == 1


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
    monkeypatch.setattr(
        sectors_sync,
        "_market_now",
        lambda: datetime(2026, 7, 19, 15, 21, tzinfo=sectors_sync.MARKET_TIMEZONE),
    )

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


def test_sector_forecast_guards_all_sector_flow_writers() -> None:
    expected_leases = {
        "sync_daily_bars": timedelta(hours=2),
        "sync_sector_flows": timedelta(minutes=45),
        "repair_recent_sector_flow_gaps": timedelta(minutes=30),
        "backfill_sector_flows": timedelta(hours=2),
    }
    assert expected_leases == sector_job.UPSTREAM_LEASES
