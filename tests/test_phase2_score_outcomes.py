from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session

from alphapilot.db.models import (
    Base,
    CompositeScore,
    DailyBar,
    JobRun,
    ScoreOutcomeStat,
)
from alphapilot.engines.score_outcomes import (
    DECILES,
    OutcomeBucket,
    aggregate_outcomes,
    nondecreasing_rates,
    score_decile,
)
from alphapilot.jobs import score_outcomes as score_outcome_job
from alphapilot.jobs.registry import JOBS, JobExecutionError

TARGET_DATE = date(2026, 7, 21)
SCORE_MODEL_VERSION = "factor-score-v1.0.0"


def _local_session(engine: Any) -> Any:
    @contextmanager
    def local_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    return local_session


def _bar(
    symbol: str,
    trade_date: date,
    close: float,
    *,
    ingested_at: datetime | None = None,
    volume: float = 1_000.0,
) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        amount=close * volume,
        source="fixture",
        ingested_at=ingested_at or datetime(2026, 7, 22, tzinfo=UTC),
    )


def _score(
    symbol: str,
    trade_date: date,
    score: float,
    *,
    win_rate: float | None = None,
    model_version: str = SCORE_MODEL_VERSION,
) -> CompositeScore:
    return CompositeScore(
        symbol=symbol,
        trade_date=trade_date,
        score=score,
        win_rate_20d=win_rate,
        factors={},
        model_version=model_version,
    )


def _calendar_dates(periods: int) -> list[date]:
    return [stamp.date() for stamp in pd.bdate_range(end=TARGET_DATE, periods=periods)]


def _seed_calendar(session: Session, dates: list[date]) -> None:
    session.add_all(_bar("SH.000001", trade_day, 3_000.0) for trade_day in dates)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.0, 1),
        (9.999, 1),
        (10.0, 2),
        (49.9, 5),
        (90.0, 10),
        (100.0, 10),
    ],
)
def test_score_decile_uses_fixed_tie_stable_boundaries(
    score: float,
    expected: int,
) -> None:
    assert score_decile(score) == expected


@pytest.mark.parametrize("score", [None, True, -0.01, 100.01, "bad", float("nan")])
def test_score_decile_rejects_invalid_scores(score: object) -> None:
    with pytest.raises(ValueError, match="0-100"):
        score_decile(score)


def test_aggregate_outcomes_uses_exact_positive_raw_close_returns() -> None:
    result = aggregate_outcomes(
        [
            (5.0, 10.0, 11.0),
            (5.0, 10.0, 9.0),
            (15.0, None, 12.0),
            (25.0, 0.0, 12.0),
            ("bad", 10.0, 11.0),
            (95.0, 10.0, 10.0),
        ]
    )

    assert result.input_rows == 6
    assert result.evaluated_rows == 3
    assert result.missing_endpoint_rows == 2
    assert result.invalid_score_rows == 1
    assert result.buckets[0] == OutcomeBucket(
        decile=1,
        samples=2,
        positive_samples=1,
        win_rate=0.5,
    )
    assert result.buckets[9] == OutcomeBucket(
        decile=10,
        samples=1,
        positive_samples=0,
        win_rate=0.0,
    )


def _buckets(rates: list[float | None]) -> tuple[OutcomeBucket, ...]:
    assert len(rates) == len(DECILES)
    return tuple(
        OutcomeBucket(
            decile=decile,
            samples=0 if rate is None else 10,
            positive_samples=0 if rate is None else round(rate * 10),
            win_rate=rate,
        )
        for decile, rate in zip(DECILES, rates, strict=True)
    )


def test_empirical_monotonicity_requires_all_ten_nonempty_buckets() -> None:
    incomplete = _buckets([None, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    monotonic = _buckets([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
    nonmonotonic = _buckets([0.1, 0.2, 0.3, 0.4, 0.5, 0.45, 0.7, 0.8, 0.9, 1.0])

    assert nondecreasing_rates(incomplete) is None
    assert nondecreasing_rates(monotonic) is True
    assert nondecreasing_rates(nonmonotonic) is False


def test_constructed_ten_decile_outcomes_are_monotonic_without_smoothing() -> None:
    rows: list[tuple[float, float, float]] = []
    for decile in DECILES:
        score = decile * 10.0 - 5.0
        rows.extend((score, 10.0, 11.0) for _ in range(decile))
        rows.extend((score, 10.0, 9.0) for _ in range(10 - decile))

    aggregate = aggregate_outcomes(rows)

    assert [bucket.samples for bucket in aggregate.buckets] == [10] * 10
    assert [bucket.win_rate for bucket in aggregate.buckets] == pytest.approx(
        [decile / 10 for decile in DECILES]
    )
    assert nondecreasing_rates(aggregate.buckets) is True


def test_evaluate_scores_uses_exact_twenty_session_endpoints_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'score-outcomes.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    dates = _calendar_dates(22)
    origin, maturity, target = dates[0], dates[20], dates[21]
    with local_session() as session:
        _seed_calendar(session, dates)
        session.add_all(
            [
                _score("600001", origin, 5.0, win_rate=0.17),
                _score("600002", origin, 95.0, win_rate=0.23),
                _score("600003", origin, 55.0, win_rate=0.31),
                _score("600001", target, 5.0),
                _score("600002", target, 95.0),
                _score("600003", target, 55.0),
                _bar("600001", origin, 100.0),
                _bar("600001", maturity, 110.0),
                _bar("600002", origin, 100.0),
                _bar("600002", maturity, 90.0),
                _bar("600003", origin, 100.0),
                # A later bar must not be forward-filled into the exact maturity endpoint.
                _bar("600003", target, 130.0),
            ]
        )

    monkeypatch.setattr(score_outcome_job, "get_session", local_session)

    first = score_outcome_job.evaluate_scores(TARGET_DATE)
    second = score_outcome_job.evaluate_scores(TARGET_DATE)

    assert first["date"] == TARGET_DATE.isoformat()
    assert first["score_model_version"] == SCORE_MODEL_VERSION
    assert first["horizon"] == 20
    assert first["calendar_sessions"] == 22
    assert first["mature_score_dates"] == 1
    assert first["input_rows"] == 3
    assert first["evaluated_rows"] == 2
    assert first["missing_endpoint_rows"] == 1
    assert first["invalid_score_rows"] == 0
    assert first["current_rows"] == 3
    assert first["current_assigned_rows"] == 2
    assert first["nondecreasing"] is None
    assert first["buckets"][0] == {
        "decile": 1,
        "samples": 1,
        "positive_samples": 1,
        "win_rate": 1.0,
    }
    assert first["buckets"][9] == {
        "decile": 10,
        "samples": 1,
        "positive_samples": 0,
        "win_rate": 0.0,
    }
    assert {key: value for key, value in second.items() if key != "duration_seconds"} == {
        key: value for key, value in first.items() if key != "duration_seconds"
    }

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ScoreOutcomeStat)) == 10
        stats = {
            row.decile: row
            for row in session.scalars(
                select(ScoreOutcomeStat).order_by(ScoreOutcomeStat.decile)
            ).all()
        }
        assert stats[1].win_rate == pytest.approx(1.0)
        assert stats[10].win_rate == pytest.approx(0.0)
        assert stats[6].win_rate is None
        assert all(row.as_of_date == TARGET_DATE for row in stats.values())
        current = {
            row.symbol: row.win_rate_20d
            for row in session.scalars(
                select(CompositeScore).where(CompositeScore.trade_date == target)
            ).all()
        }
        historical = {
            row.symbol: row.win_rate_20d
            for row in session.scalars(
                select(CompositeScore).where(CompositeScore.trade_date == origin)
            ).all()
        }

    assert current == {"600001": 1.0, "600002": 0.0, "600003": None}
    assert historical == {"600001": 0.17, "600002": 0.23, "600003": 0.31}


def test_evaluate_scores_writes_ten_empty_buckets_without_backcasting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'score-outcomes-empty.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    dates = _calendar_dates(20)
    with local_session() as session:
        _seed_calendar(session, dates)
        session.add(_score("600001", TARGET_DATE, 85.0, win_rate=0.99))

    monkeypatch.setattr(score_outcome_job, "get_session", local_session)

    stats = score_outcome_job.evaluate_scores(TARGET_DATE)

    assert stats["mature_score_dates"] == 0
    assert stats["input_rows"] == 0
    assert stats["evaluated_rows"] == 0
    assert stats["current_rows"] == 1
    assert stats["current_assigned_rows"] == 0
    assert stats["nondecreasing"] is None
    assert [bucket["decile"] for bucket in stats["buckets"]] == list(DECILES)
    assert all(bucket["samples"] == 0 for bucket in stats["buckets"])
    assert all(bucket["win_rate"] is None for bucket in stats["buckets"])
    with Session(engine) as session:
        persisted = session.scalars(
            select(ScoreOutcomeStat).order_by(ScoreOutcomeStat.decile)
        ).all()
        current = session.scalar(
            select(CompositeScore).where(CompositeScore.trade_date == TARGET_DATE)
        )
    assert len(persisted) == 10
    assert all(row.win_rate is None for row in persisted)
    assert current is not None
    assert current.win_rate_20d is None


def test_evaluate_scores_rejects_mixed_current_score_model_versions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'score-mixed-models.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    dates = _calendar_dates(21)
    with local_session() as session:
        _seed_calendar(session, dates)
        session.add_all(
            [
                _score("600001", TARGET_DATE, 25.0, win_rate=0.25),
                _score(
                    "600002",
                    TARGET_DATE,
                    75.0,
                    win_rate=0.75,
                    model_version="factor-score-v2.0.0",
                ),
            ]
        )

    monkeypatch.setattr(score_outcome_job, "get_session", local_session)

    with pytest.raises(JobExecutionError, match="模型版本"):
        score_outcome_job.evaluate_scores(TARGET_DATE)

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ScoreOutcomeStat)) == 0
        current = {
            row.symbol: row.win_rate_20d
            for row in session.scalars(
                select(CompositeScore).where(CompositeScore.trade_date == TARGET_DATE)
            ).all()
        }
    assert current == {"600001": 0.25, "600002": 0.75}


def test_evaluate_scores_rejects_target_missing_from_index_calendar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'score-missing-target-calendar.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    calendar_dates = [
        stamp.date() for stamp in pd.bdate_range(end=TARGET_DATE - timedelta(days=1), periods=21)
    ]
    with local_session() as session:
        _seed_calendar(session, calendar_dates)
        session.add(_score("600001", TARGET_DATE, 75.0, win_rate=0.77))

    monkeypatch.setattr(score_outcome_job, "get_session", local_session)

    with pytest.raises(JobExecutionError) as caught:
        score_outcome_job.evaluate_scores(TARGET_DATE)

    assert caught.value.stats["reason"] == "target_missing_from_calendar"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ScoreOutcomeStat)) == 0
        current = session.scalar(
            select(CompositeScore).where(
                CompositeScore.symbol == "600001",
                CompositeScore.trade_date == TARGET_DATE,
            )
        )
    assert current is not None
    assert current.win_rate_20d == pytest.approx(0.77)


def test_historical_evaluation_never_overwrites_newer_outcome_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'score-newer-state.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    historical_target = TARGET_DATE - timedelta(days=1)
    calendar_dates = [stamp.date() for stamp in pd.bdate_range(end=historical_target, periods=21)]
    preserved_updated_at = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    with local_session() as session:
        _seed_calendar(session, calendar_dates)
        session.add(_score("600001", historical_target, 75.0, win_rate=0.42))
        session.add(
            ScoreOutcomeStat(
                decile=8,
                horizon=20,
                samples=10,
                positive_samples=8,
                win_rate=0.8,
                score_model_version=SCORE_MODEL_VERSION,
                model_version=score_outcome_job.MODEL_VERSION,
                as_of_date=TARGET_DATE,
                updated_at=preserved_updated_at,
            )
        )

    monkeypatch.setattr(score_outcome_job, "get_session", local_session)

    with pytest.raises(JobExecutionError) as caught:
        score_outcome_job.evaluate_scores(historical_target)

    assert caught.value.stats["reason"] == "outcome_state_newer_than_target"
    with Session(engine) as session:
        persisted = session.scalars(select(ScoreOutcomeStat)).all()
        current = session.scalar(
            select(CompositeScore).where(
                CompositeScore.symbol == "600001",
                CompositeScore.trade_date == historical_target,
            )
        )
    assert len(persisted) == 1
    assert persisted[0].as_of_date == TARGET_DATE
    assert persisted[0].updated_at == preserved_updated_at.replace(tzinfo=None)
    assert persisted[0].samples == 10
    assert persisted[0].positive_samples == 8
    assert persisted[0].win_rate == pytest.approx(0.8)
    assert current is not None
    assert current.win_rate_20d == pytest.approx(0.42)


@pytest.mark.parametrize("running_job", ["sync_daily_bars", "compute_factors"])
def test_evaluate_scores_blocks_while_upstream_job_is_running(
    running_job: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / f'score-guard-{running_job}.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    dates = _calendar_dates(21)
    with local_session() as session:
        _seed_calendar(session, dates)
        session.add(_score("600001", TARGET_DATE, 50.0))
        session.add(JobRun(job_name=running_job, status="running", stats={}))

    monkeypatch.setattr(score_outcome_job, "get_session", local_session)

    with pytest.raises(
        JobExecutionError,
        match="日线同步或因子计算任务仍在运行，胜率评估已延后。",
    ):
        score_outcome_job.evaluate_scores(TARGET_DATE)

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ScoreOutcomeStat)) == 0


def test_current_session_endpoint_requires_shanghai_1506_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'score-current-endpoint.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    dates = _calendar_dates(21)
    origin, target = dates[0], dates[20]
    incomplete_ingestion = datetime.combine(target, time(7, 5), tzinfo=UTC)
    complete_ingestion = datetime.combine(target, time(7, 6), tzinfo=UTC)
    with local_session() as session:
        _seed_calendar(session, dates)
        session.add_all(
            [
                _score("600001", origin, 75.0, win_rate=0.42),
                _score("600001", target, 75.0),
                _bar("600001", origin, 100.0),
                _bar(
                    "600001",
                    target,
                    110.0,
                    ingested_at=incomplete_ingestion,
                ),
            ]
        )

    monkeypatch.setattr(score_outcome_job, "get_session", local_session)

    incomplete = score_outcome_job.evaluate_scores(TARGET_DATE)
    with local_session() as session:
        session.execute(
            update(DailyBar)
            .where(
                DailyBar.symbol == "600001",
                DailyBar.trade_date == target,
            )
            .values(ingested_at=complete_ingestion)
        )
    complete = score_outcome_job.evaluate_scores(TARGET_DATE)

    assert incomplete["evaluated_rows"] == 0
    assert incomplete["missing_endpoint_rows"] == 1
    assert incomplete["current_assigned_rows"] == 0
    assert complete["evaluated_rows"] == 1
    assert complete["missing_endpoint_rows"] == 0
    assert complete["current_assigned_rows"] == 1
    with Session(engine) as session:
        historical = session.scalar(
            select(CompositeScore).where(
                CompositeScore.symbol == "600001",
                CompositeScore.trade_date == origin,
            )
        )
        current = session.scalar(
            select(CompositeScore).where(
                CompositeScore.symbol == "600001",
                CompositeScore.trade_date == target,
            )
        )
    assert historical is not None and historical.win_rate_20d == pytest.approx(0.42)
    assert current is not None and current.win_rate_20d == pytest.approx(1.0)


def test_origin_requires_close_completion_but_zero_volume_endpoint_is_valid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'score-origin-completion.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    dates = _calendar_dates(22)
    origin, maturity, target = dates[0], dates[20], dates[21]
    incomplete_origin = datetime.combine(origin, time(7, 5), tzinfo=UTC)
    complete_origin = datetime.combine(origin, time(7, 6), tzinfo=UTC)
    complete_maturity = datetime.combine(maturity, time(7, 6), tzinfo=UTC)
    with local_session() as session:
        _seed_calendar(session, dates)
        session.add_all(
            [
                _score("600001", origin, 65.0),
                _score("600001", target, 65.0),
                _bar(
                    "600001",
                    origin,
                    100.0,
                    ingested_at=incomplete_origin,
                ),
                _bar(
                    "600001",
                    maturity,
                    110.0,
                    ingested_at=complete_maturity,
                    volume=0.0,
                ),
            ]
        )

    monkeypatch.setattr(score_outcome_job, "get_session", local_session)

    incomplete = score_outcome_job.evaluate_scores(TARGET_DATE)
    with local_session() as session:
        session.execute(
            update(DailyBar)
            .where(
                DailyBar.symbol == "600001",
                DailyBar.trade_date == origin,
            )
            .values(ingested_at=complete_origin)
        )
    complete = score_outcome_job.evaluate_scores(TARGET_DATE)

    assert incomplete["evaluated_rows"] == 0
    assert incomplete["missing_endpoint_rows"] == 1
    assert complete["evaluated_rows"] == 1
    assert complete["missing_endpoint_rows"] == 0
    assert complete["current_assigned_rows"] == 1
    assert complete["buckets"][6]["win_rate"] == pytest.approx(1.0)


def test_score_outcome_job_runs_at_2000_after_existing_analytics_chain() -> None:
    score_outcome_job.register_score_outcomes_job()
    try:
        trigger = JOBS["evaluate_scores"].trigger
        assert str(trigger.fields[4]) == "mon-fri"
        assert str(trigger.fields[5]) == "20"
        assert str(trigger.fields[6]) == "0"
        assert str(trigger.timezone) == "Asia/Shanghai"
    finally:
        JOBS.pop("evaluate_scores", None)
