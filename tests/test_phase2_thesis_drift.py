from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.data.base import DataProviderError
from alphapilot.data.mock import MockMarketDataProvider
from alphapilot.db.models import (
    AlertRecord,
    Base,
    DailyBar,
    DomainEvent,
    ForecastSnapshot,
    ThesisTransition,
    WatchlistItem,
)
from alphapilot.domain.models import HorizonForecast, StockForecast
from alphapilot.engines import thesis_drift
from alphapilot.engines.thesis_drift import evaluate
from alphapilot.main import app
from alphapilot.services import watchlist as watchlist_service

SYMBOL = "600519"
MARKET_CALENDAR_SYMBOL = "SH.000001"
NOW = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)
SUMMARY_NOW = datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
TRADING_DATES = (
    date(2026, 7, 9),
    date(2026, 7, 10),
    date(2026, 7, 13),
    date(2026, 7, 14),
    date(2026, 7, 15),
    date(2026, 7, 16),
)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    database = create_engine(f"sqlite:///{tmp_path / 'thesis-drift.db'}")
    Base.metadata.create_all(database)
    return database


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


@pytest.fixture(autouse=True)
def fixed_engine_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(thesis_drift, "_now", lambda: NOW)


def _seed_watchlist(session: Session, *, state: str = "unchanged", symbol: str = SYMBOL) -> None:
    session.add(
        WatchlistItem(
            symbol=symbol,
            display_name="贵州茅台",
            thesis="测试投资逻辑",
            thesis_state=state,
        )
    )


def _seed_daily_bars(
    session: Session,
    *,
    symbol: str,
    trading_dates: tuple[date, ...],
) -> None:
    for index, trade_date in enumerate(trading_dates, start=1):
        close = 100.0 + index
        session.add(
            DailyBar(
                symbol=symbol,
                trade_date=trade_date,
                open=close - 1.0,
                high=close + 1.0,
                low=close - 2.0,
                close=close,
                volume=1_000_000.0,
                amount=close * 1_000_000.0,
                source="fixture",
            )
        )


def _seed_market_calendar(session: Session) -> None:
    _seed_daily_bars(
        session,
        symbol=MARKET_CALENDAR_SYMBOL,
        trading_dates=TRADING_DATES,
    )


def _seed_forecast(
    session: Session,
    *,
    trade_date: date,
    p_up: float,
    provider: str = "fixture",
    model_version: str = "forecast-v1",
    confidence: float = 0.72,
    symbol: str = SYMBOL,
    created_at: datetime | None = None,
) -> ForecastSnapshot:
    as_of = datetime.combine(trade_date, datetime.min.time(), tzinfo=UTC)
    snapshot = ForecastSnapshot(
        symbol=symbol,
        as_of=as_of,
        provider=provider,
        model_version=model_version,
        horizons={
            "20d": {
                "horizon_days": 20,
                "p_up": p_up,
                "expected_return": 0.02,
                "q10": -0.08,
                "q50": 0.02,
                "q90": 0.12,
                "confidence": confidence,
            }
        },
        features={},
        created_at=created_at or as_of + timedelta(hours=12),
    )
    session.add(snapshot)
    return snapshot


def _seed_forecast_pair(
    session: Session,
    *,
    baseline: float,
    latest: float,
    baseline_provider: str = "fixture",
    latest_provider: str = "fixture",
    baseline_model: str = "forecast-v1",
    latest_model: str = "forecast-v1",
) -> None:
    _seed_market_calendar(session)
    _seed_forecast(
        session,
        trade_date=TRADING_DATES[0],
        p_up=baseline,
        provider=baseline_provider,
        model_version=baseline_model,
    )
    _seed_forecast(
        session,
        trade_date=TRADING_DATES[-1],
        p_up=latest,
        provider=latest_provider,
        model_version=latest_model,
    )


def _latest_stock_forecast(*, p_up_20d: float = 0.70) -> StockForecast:
    def horizon(days: int, p_up: float) -> HorizonForecast:
        return HorizonForecast(
            horizon_days=days,
            p_up=p_up,
            expected_return=0.03,
            q10=-0.06,
            q50=0.03,
            q90=0.14,
            confidence=0.72,
        )

    return StockForecast(
        symbol=SYMBOL,
        as_of=datetime.combine(TRADING_DATES[-1], datetime.min.time(), tzinfo=UTC),
        provider="fixture",
        model_version="forecast-v1",
        data_points=220,
        features={"volatility_20d": 0.20},
        horizons={
            "1d": horizon(1, 0.58),
            "5d": horizon(5, 0.62),
            "20d": horizon(20, p_up_20d),
        },
        warnings=[],
    )


def _seed_event(
    session: Session,
    *,
    title: str,
    occurred_at: datetime,
    direction: float = -0.5,
    event_type: str = "disclosure",
    ingested_at: datetime | None = None,
) -> DomainEvent:
    event = DomainEvent(
        symbol=SYMBOL,
        event_type=event_type,
        direction=direction,
        strength=0.8,
        title=title,
        summary=title,
        source_ref=f"fixture:{event_type}:{title}",
        occurred_at=occurred_at,
        ingested_at=ingested_at or NOW,
    )
    session.add(event)
    return event


def _artifact_counts(session: Session) -> tuple[int, int, int]:
    return (
        session.scalar(select(func.count()).select_from(ThesisTransition)) or 0,
        session.scalar(
            select(func.count())
            .select_from(DomainEvent)
            .where(DomainEvent.event_type == "thesis_shift")
        )
        or 0,
        session.scalar(
            select(func.count())
            .select_from(AlertRecord)
            .where(AlertRecord.action == "REVIEW_REQUIRED")
        )
        or 0,
    )


@pytest.mark.parametrize(
    ("from_state", "baseline", "latest", "expected_state"),
    [
        ("unchanged", 0.40, 0.49, "strengthened"),
        ("unchanged", 0.60, 0.51, "weakened"),
        ("strengthened", 0.50, 0.53, "unchanged"),
    ],
)
def test_forecast_delta_drives_all_three_state_migrations(
    session: Session,
    from_state: str,
    baseline: float,
    latest: float,
    expected_state: str,
) -> None:
    _seed_watchlist(session, state=from_state)
    _seed_forecast_pair(session, baseline=baseline, latest=latest)

    decision = evaluate(session, SYMBOL)
    session.flush()

    assert decision is not None
    assert decision[0] == expected_state
    assert session.get(WatchlistItem, SYMBOL).thesis_state == expected_state  # type: ignore[union-attr]
    transition = session.scalar(select(ThesisTransition))
    assert transition is not None
    assert (transition.from_state, transition.to_state) == (from_state, expected_state)
    assert _artifact_counts(session) == (1, 1, 1)


@pytest.mark.parametrize(
    ("from_state", "baseline", "latest"),
    [
        ("strengthened", 0.50, 0.58),
        ("weakened", 0.50, 0.42),
    ],
)
def test_exact_eight_point_boundaries_migrate_to_unchanged(
    session: Session,
    from_state: str,
    baseline: float,
    latest: float,
) -> None:
    _seed_watchlist(session, state=from_state)
    _seed_forecast_pair(session, baseline=baseline, latest=latest)

    decision = evaluate(session, SYMBOL)
    session.flush()

    assert decision is not None
    assert decision[0] == "unchanged"
    transition = session.scalar(select(ThesisTransition))
    assert transition is not None
    assert transition.to_state == "unchanged"


def test_same_date_snapshots_are_deduplicated_to_latest_row(session: Session) -> None:
    _seed_watchlist(session)
    _seed_market_calendar(session)
    _seed_forecast(session, trade_date=TRADING_DATES[0], p_up=0.90)
    _seed_forecast(session, trade_date=TRADING_DATES[0], p_up=0.40)
    _seed_forecast(session, trade_date=TRADING_DATES[-1], p_up=0.10)
    _seed_forecast(session, trade_date=TRADING_DATES[-1], p_up=0.50)

    decision = evaluate(session, SYMBOL)
    session.flush()

    assert decision is not None
    assert decision[0] == "strengthened"
    assert "40.0%" in decision[1]
    assert "50.0%" in decision[1]
    assert _artifact_counts(session) == (1, 1, 1)


def test_fifth_session_uses_market_calendar_when_stock_has_a_halt(session: Session) -> None:
    _seed_watchlist(session)
    _seed_market_calendar(session)
    _seed_daily_bars(
        session,
        symbol=SYMBOL,
        trading_dates=(
            date(2026, 7, 8),
            date(2026, 7, 9),
            date(2026, 7, 10),
            date(2026, 7, 14),
            date(2026, 7, 15),
            date(2026, 7, 16),
        ),
    )
    _seed_forecast(session, trade_date=date(2026, 7, 8), p_up=0.90)
    _seed_forecast(session, trade_date=TRADING_DATES[0], p_up=0.40)
    _seed_forecast(session, trade_date=TRADING_DATES[-1], p_up=0.50)

    decision = evaluate(session, SYMBOL, evaluated_at=NOW)
    session.flush()

    assert decision is not None
    assert decision[0] == "strengthened"
    assert "40.0%" in decision[1]
    assert "50.0%" in decision[1]


def test_future_snapshot_as_of_or_created_at_is_excluded(session: Session) -> None:
    _seed_watchlist(session)
    _seed_market_calendar(session)
    _seed_forecast(session, trade_date=TRADING_DATES[0], p_up=0.40)
    _seed_forecast(session, trade_date=TRADING_DATES[-1], p_up=0.50)
    _seed_forecast(
        session,
        trade_date=date(2026, 7, 17),
        p_up=0.05,
        created_at=NOW - timedelta(hours=2),
    )
    _seed_forecast(
        session,
        trade_date=TRADING_DATES[-1],
        p_up=0.10,
        created_at=NOW + timedelta(seconds=1),
    )

    decision = evaluate(session, SYMBOL, evaluated_at=NOW)
    session.flush()

    assert decision is not None
    assert decision[0] == "strengthened"
    assert "40.0%" in decision[1]
    assert "50.0%" in decision[1]
    assert _artifact_counts(session) == (1, 1, 1)


@pytest.mark.parametrize(
    (
        "baseline_provider",
        "latest_provider",
        "baseline_model",
        "latest_model",
    ),
    [
        ("fixture", "fixture", "forecast-v1", "forecast-v2"),
        ("fixture-a", "fixture-b", "forecast-v1", "forecast-v1"),
    ],
)
def test_forecast_evidence_requires_same_model_and_provider(
    session: Session,
    baseline_provider: str,
    latest_provider: str,
    baseline_model: str,
    latest_model: str,
) -> None:
    _seed_watchlist(session)
    _seed_forecast_pair(
        session,
        baseline=0.40,
        latest=0.60,
        baseline_provider=baseline_provider,
        latest_provider=latest_provider,
        baseline_model=baseline_model,
        latest_model=latest_model,
    )

    assert evaluate(session, SYMBOL) is None
    assert session.get(WatchlistItem, SYMBOL).thesis_state == "unchanged"  # type: ignore[union-attr]
    assert _artifact_counts(session) == (0, 0, 0)


def test_missing_exact_fifth_trading_day_baseline_has_no_side_effects(
    session: Session,
) -> None:
    _seed_watchlist(session)
    _seed_market_calendar(session)
    _seed_forecast(session, trade_date=TRADING_DATES[1], p_up=0.30)
    _seed_forecast(session, trade_date=TRADING_DATES[-1], p_up=0.70)

    assert evaluate(session, SYMBOL) is None
    assert session.get(WatchlistItem, SYMBOL).thesis_state == "unchanged"  # type: ignore[union-attr]
    assert _artifact_counts(session) == (0, 0, 0)


def test_latest_eligible_negative_event_overrides_forecast_delta(session: Session) -> None:
    _seed_watchlist(session)
    _seed_forecast_pair(session, baseline=0.40, latest=0.60)
    _seed_event(
        session,
        title="旧公告今日补录",
        occurred_at=NOW - timedelta(days=6),
        ingested_at=NOW,
    )
    _seed_event(
        session,
        title="未来事件不得提前使用",
        occurred_at=NOW + timedelta(minutes=1),
    )
    _seed_event(
        session,
        title="引擎自身事件不得反馈",
        occurred_at=NOW - timedelta(minutes=1),
        event_type="thesis_shift",
    )
    _seed_event(
        session,
        title="较早但合格的负向事件",
        occurred_at=NOW - timedelta(hours=3),
    )
    _seed_event(
        session,
        title="未来才入库的事件不得提前使用",
        occurred_at=NOW - timedelta(minutes=30),
        ingested_at=NOW + timedelta(seconds=1),
    )
    _seed_event(
        session,
        title="最新合格负向事件",
        occurred_at=NOW - timedelta(hours=1),
    )

    decision = evaluate(session, SYMBOL)
    session.flush()

    assert decision is not None
    assert decision[0] == "weakened"
    assert "最新合格负向事件" in decision[1]
    assert "旧公告今日补录" not in decision[1]
    assert "未来才入库" not in decision[1]
    transition = session.scalar(select(ThesisTransition))
    assert transition is not None
    assert transition.to_state == "weakened"
    emitted = session.scalars(
        select(DomainEvent).where(
            DomainEvent.event_type == "thesis_shift",
            DomainEvent.source_ref.like("thesis-transition:%"),
        )
    ).all()
    assert len(emitted) == 1
    assert emitted[0].title == "600519 投资逻辑由不变转为转弱"
    assert all(state not in emitted[0].title for state in ("unchanged", "strengthened", "weakened"))


def test_repeated_event_expiry_cycles_create_each_real_transition(session: Session) -> None:
    _seed_watchlist(session)
    _seed_forecast_pair(session, baseline=0.40, latest=0.60)
    session.commit()

    first_event_time = NOW + timedelta(days=1)
    first_expiry_time = first_event_time + timedelta(days=3, seconds=1)
    second_event_time = first_expiry_time + timedelta(hours=1)
    second_expiry_time = second_event_time + timedelta(days=3, seconds=1)

    first = evaluate(session, SYMBOL, evaluated_at=NOW)
    session.commit()
    assert first is not None and first[0] == "strengthened"

    _seed_event(
        session,
        title="第一轮负向事件",
        occurred_at=first_event_time,
        ingested_at=first_event_time,
    )
    session.commit()
    second = evaluate(session, SYMBOL, evaluated_at=first_event_time)
    session.commit()
    assert second is not None and second[0] == "weakened"

    third = evaluate(session, SYMBOL, evaluated_at=first_expiry_time)
    session.commit()
    assert third is not None and third[0] == "strengthened"

    _seed_event(
        session,
        title="第二轮负向事件",
        occurred_at=second_event_time,
        ingested_at=second_event_time,
    )
    session.commit()
    fourth = evaluate(session, SYMBOL, evaluated_at=second_event_time)
    session.commit()
    assert fourth is not None and fourth[0] == "weakened"

    fifth = evaluate(session, SYMBOL, evaluated_at=second_expiry_time)
    session.commit()
    assert fifth is not None and fifth[0] == "strengthened"

    transitions = session.scalars(
        select(ThesisTransition).order_by(ThesisTransition.created_at, ThesisTransition.id)
    ).all()
    assert [(row.from_state, row.to_state) for row in transitions] == [
        ("unchanged", "strengthened"),
        ("strengthened", "weakened"),
        ("weakened", "strengthened"),
        ("strengthened", "weakened"),
        ("weakened", "strengthened"),
    ]
    assert len({row.trigger_ref for row in transitions}) == 5
    assert session.get(WatchlistItem, SYMBOL).thesis_state == "strengthened"  # type: ignore[union-attr]
    assert _artifact_counts(session) == (5, 5, 5)


def test_transition_artifacts_share_transaction_and_repeat_is_idempotent(
    session: Session,
) -> None:
    _seed_watchlist(session)
    _seed_forecast_pair(session, baseline=0.40, latest=0.60)
    session.commit()

    assert evaluate(session, SYMBOL) is not None
    session.flush()
    assert _artifact_counts(session) == (1, 1, 1)
    session.rollback()

    restored = session.get(WatchlistItem, SYMBOL)
    assert restored is not None
    assert restored.thesis_state == "unchanged"
    assert _artifact_counts(session) == (0, 0, 0)

    assert evaluate(session, SYMBOL) is not None
    session.commit()
    assert session.get(WatchlistItem, SYMBOL).thesis_state == "strengthened"  # type: ignore[union-attr]
    assert _artifact_counts(session) == (1, 1, 1)

    repeated = evaluate(session, SYMBOL)
    session.commit()
    assert repeated is not None
    assert repeated[0] == "strengthened"
    assert _artifact_counts(session) == (1, 1, 1)


def test_stale_second_session_cannot_duplicate_transition(engine: Engine) -> None:
    with Session(engine) as seed_session:
        _seed_watchlist(seed_session)
        _seed_forecast_pair(seed_session, baseline=0.40, latest=0.60)
        seed_session.commit()

    with (
        Session(engine, expire_on_commit=False) as first_session,
        Session(engine, expire_on_commit=False) as stale_session,
    ):
        stale_item = stale_session.get(WatchlistItem, SYMBOL)
        assert stale_item is not None
        assert stale_item.thesis_state == "unchanged"
        stale_session.commit()

        first_decision = evaluate(first_session, SYMBOL, evaluated_at=NOW)
        first_session.commit()
        assert first_decision is not None
        assert first_decision[0] == "strengthened"
        assert stale_item.thesis_state == "unchanged"

        stale_decision = evaluate(stale_session, SYMBOL, evaluated_at=NOW)
        stale_session.commit()
        assert stale_decision is not None
        assert stale_decision[0] == "strengthened"

    with Session(engine) as verification_session:
        item = verification_session.get(WatchlistItem, SYMBOL)
        assert item is not None
        assert item.thesis_state == "strengthened"
        assert _artifact_counts(verification_session) == (1, 1, 1)


def test_refresh_runs_event_only_drift_after_forecast_failure_and_returns_alert(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_watchlist(session)
    _seed_event(
        session,
        title="业绩预告显著下修",
        occurred_at=NOW - timedelta(hours=2),
    )
    session.commit()

    def fail_forecast(*_args: object, **_kwargs: object) -> None:
        raise DataProviderError("fixture forecast failure")

    monkeypatch.setattr(watchlist_service, "forecast_for_symbol", fail_forecast)

    created = watchlist_service.refresh_alerts(session, MockMarketDataProvider())
    session.flush()

    assert [record.action for record in created] == ["REVIEW_REQUIRED"]
    assert created[0].model_version == thesis_drift.MODEL_VERSION
    assert created[0].target_low is None
    assert created[0].target_high is None
    assert created[0].suggested_notional is None
    assert "业绩预告显著下修" in created[0].reasons[0]
    assert session.scalar(select(func.count()).select_from(ForecastSnapshot)) == 0
    assert _artifact_counts(session) == (1, 1, 1)


def test_refresh_failure_does_not_reuse_old_forecast_evidence_without_event(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_watchlist(session)
    _seed_forecast_pair(session, baseline=0.40, latest=0.60)
    session.commit()

    def fail_forecast(*_args: object, **_kwargs: object) -> None:
        raise DataProviderError("fixture forecast failure")

    monkeypatch.setattr(watchlist_service, "forecast_for_symbol", fail_forecast)

    created = watchlist_service.refresh_alerts(session, MockMarketDataProvider())
    session.flush()

    item = session.get(WatchlistItem, SYMBOL)
    assert item is not None
    assert item.thesis_state == "unchanged"
    assert created == []
    assert session.scalar(select(func.count()).select_from(ForecastSnapshot)) == 2
    assert _artifact_counts(session) == (0, 0, 0)


def test_successful_refresh_returns_regular_and_drift_alert_without_duplicate_drift(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_watchlist(session)
    _seed_market_calendar(session)
    _seed_forecast(session, trade_date=TRADING_DATES[0], p_up=0.40)
    session.commit()
    refresh_time = datetime.now(UTC) + timedelta(minutes=1)
    monkeypatch.setattr(thesis_drift, "_now", lambda: refresh_time)
    latest_forecast = _latest_stock_forecast()
    monkeypatch.setattr(
        watchlist_service,
        "forecast_for_symbol",
        lambda *_args, **_kwargs: latest_forecast,
    )

    first_created = watchlist_service.refresh_alerts(session, MockMarketDataProvider())
    session.flush()

    assert [record.action for record in first_created] == [
        "BUY_CANDIDATE",
        "REVIEW_REQUIRED",
    ]
    assert session.scalar(select(func.count()).select_from(ForecastSnapshot)) == 2
    assert _artifact_counts(session) == (1, 1, 1)
    session.commit()

    second_created = watchlist_service.refresh_alerts(session, MockMarketDataProvider())
    session.commit()

    assert [record.action for record in second_created] == ["BUY_CANDIDATE"]
    assert session.scalar(select(func.count()).select_from(ForecastSnapshot)) == 3
    assert _artifact_counts(session) == (1, 1, 1)
    assert session.scalar(select(func.count()).select_from(AlertRecord)) == 3


def test_summary_api_returns_current_counts_daily_transition_buckets_and_422(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_today = SUMMARY_NOW.astimezone(watchlist_service.MARKET_TIMEZONE).date()

    def market_noon(days_ago: int) -> datetime:
        local_date = local_today - timedelta(days=days_ago)
        return (
            datetime.combine(
                local_date,
                datetime.min.time(),
                tzinfo=watchlist_service.MARKET_TIMEZONE,
            )
            + timedelta(hours=12)
        ).astimezone(UTC)

    with Session(engine) as seed_session:
        for symbol, state in (
            ("600519", "strengthened"),
            ("300750", "unchanged"),
            ("002594", "weakened"),
        ):
            _seed_watchlist(seed_session, state=state, symbol=symbol)
        recent = ThesisTransition(
            symbol="600519",
            from_state="unchanged",
            to_state="strengthened",
            reason="20日上涨概率改善",
            trigger_ref="forecast:strengthened",
            model_version=thesis_drift.MODEL_VERSION,
            created_at=market_noon(6),
        )
        seed_session.add(recent)
        for transition in (
            ThesisTransition(
                symbol="300750",
                from_state="strengthened",
                to_state="unchanged",
                reason="概率恢复中性",
                trigger_ref="forecast:unchanged",
                model_version=thesis_drift.MODEL_VERSION,
                created_at=market_noon(2),
            ),
            ThesisTransition(
                symbol="002594",
                from_state="unchanged",
                to_state="weakened",
                reason="负向事件一",
                trigger_ref="event:weakened-1",
                model_version=thesis_drift.MODEL_VERSION,
                created_at=market_noon(0) - timedelta(hours=1),
            ),
            ThesisTransition(
                symbol="000333",
                from_state="strengthened",
                to_state="weakened",
                reason="负向事件二",
                trigger_ref="event:weakened-2",
                model_version=thesis_drift.MODEL_VERSION,
                created_at=market_noon(0),
            ),
            ThesisTransition(
                symbol="600000",
                from_state="unchanged",
                to_state="strengthened",
                reason="窗口外迁移",
                trigger_ref="forecast:too-old",
                model_version=thesis_drift.MODEL_VERSION,
                created_at=market_noon(7),
            ),
        ):
            seed_session.add(transition)
        seed_session.commit()

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as api_session:
            try:
                yield api_session
                api_session.commit()
            except Exception:
                api_session.rollback()
                raise

    summary_impl = watchlist_service.watchlist_summary

    def fixed_summary(api_session: Session) -> dict[str, object]:
        return summary_impl(api_session, now=SUMMARY_NOW)

    monkeypatch.setattr(watchlist_service, "watchlist_summary", fixed_summary)
    app.dependency_overrides[db_session_dependency] = override_session
    client = TestClient(app)
    try:
        response = client.get("/v1/watchlist/summary")
        manual_state = client.post(
            "/v1/watchlist",
            json={"symbol": "300750", "thesis_state": "strengthened"},
        )
        invalid = client.post(
            "/v1/watchlist",
            json={"symbol": "000333", "thesis_state": "conviction"},
        )
    finally:
        client.close()
        app.dependency_overrides.pop(db_session_dependency, None)

    assert response.status_code == 200
    expected_buckets = [
        {
            "date": (local_today - timedelta(days=days_ago)).isoformat(),
            "strengthened": 0,
            "unchanged": 0,
            "weakened": 0,
        }
        for days_ago in range(6, -1, -1)
    ]
    expected_buckets[0]["strengthened"] = 1
    expected_buckets[4]["unchanged"] = 1
    expected_buckets[6]["weakened"] = 2
    assert response.json() == {
        "strengthened": 1,
        "unchanged": 1,
        "weakened": 1,
        "transitions_7d": expected_buckets,
    }
    assert manual_state.status_code == 422
    assert "投资逻辑状态由漂移引擎自动维护" in manual_state.text
    assert invalid.status_code == 422
    with Session(engine) as verification_session:
        item = verification_session.get(WatchlistItem, "300750")
        assert item is not None
        assert item.thesis_state == "unchanged"
