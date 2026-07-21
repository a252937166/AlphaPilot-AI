from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, func, inspect, select, text, update
from sqlalchemy.orm import Session

from alphapilot.db.migrate import run_migrations
from alphapilot.db.models import (
    Base,
    CompositeScore,
    DailyBar,
    FactorValue,
    JobRun,
    Security,
    StyleDaily,
)
from alphapilot.jobs import style as style_job
from alphapilot.jobs.registry import JOBS, JobExecutionError

TARGET_DATE = date(2026, 7, 21)


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


def _add_inputs(session: Session, trade_date: date, *, symbol: str = "600001") -> None:
    session.add(
        DailyBar(
            symbol=symbol,
            trade_date=trade_date,
            open=10.0,
            high=10.5,
            low=9.5,
            close=10.0,
            volume=100.0,
            amount=1000.0,
            source="test",
        )
    )
    session.add(
        CompositeScore(
            symbol=symbol,
            trade_date=trade_date,
            score=80.0,
            win_rate_20d=None,
            factors={},
            model_version="factor-score-v1.0.0",
        )
    )
    session.add(
        FactorValue(
            symbol=symbol,
            trade_date=trade_date,
            factor="volatility_20d",
            raw=0.1,
            zscore=0.0,
            model_version="factor-v1.0.0",
        )
    )


def _snapshot(
    *,
    trade_date: date = TARGET_DATE,
    symbol_tags: dict[str, str] | None = None,
) -> SimpleNamespace:
    tags = symbol_tags or {"600001": "growth", "600002": "value"}
    return SimpleNamespace(
        trade_date=trade_date,
        symbol_tags=tags,
        amount_weights={
            "growth": 0.4,
            "value": 0.3,
            "defensive": 0.2,
            "balanced": 0.1,
        },
        tag_counts={"growth": 1, "value": 1, "defensive": 0, "balanced": 0},
        input_stats=SimpleNamespace(
            composite_symbols=3,
            eligible_symbols=2,
            excluded_symbols=1,
            missing_security_symbols=0,
            missing_or_nonpositive_amount_symbols=1,
            factor_coverage={"pe_percentile": 2},
        ),
        total_amount=3000.0,
        model_version="style-v1.0.0",
    )


def test_style_models_and_migration_are_idempotent(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-style.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE securities (symbol TEXT PRIMARY KEY)"))
        connection.execute(
            text("CREATE TABLE screening_runs (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        )
        connection.execute(text("CREATE TABLE style_daily (trade_date DATE PRIMARY KEY)"))

    applied = run_migrations(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("securities")}

    assert "securities.style_tag" in applied
    assert "style_daily.source_fingerprint" in applied
    assert "style_tag" in columns
    assert run_migrations(engine) == []
    assert Security.__table__.c.style_tag.type.length == 16
    assert StyleDaily.__table__.primary_key.columns.keys() == ["trade_date"]
    assert StyleDaily().model_version is None  # SQLAlchemy applies the default on INSERT.
    assert StyleDaily.__table__.c.model_version.default.arg == "style-v1.0.0"
    assert StyleDaily.__table__.c.source_fingerprint.type.length == 64


def test_style_job_cron_runs_after_factors_at_1940() -> None:
    style_job.register_style_job()
    try:
        trigger = JOBS["compute_style_daily"].trigger
        assert str(trigger.fields[4]) == "mon-fri"
        assert str(trigger.fields[5]) == "19"
        assert str(trigger.fields[6]) == "40"
        assert str(trigger.timezone) == "Asia/Shanghai"
    finally:
        JOBS.pop("compute_style_daily", None)


def test_style_job_is_idempotent_and_fully_refreshes_current_tags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'style-job.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add_all(
            [
                Security(symbol="600001", list_status="listed", style_tag="balanced"),
                Security(symbol="600002", list_status="listed", style_tag=None),
                Security(symbol="600003", list_status="listed", style_tag="defensive"),
            ]
        )
        _add_inputs(session, TARGET_DATE)

    snapshot = _snapshot()
    monkeypatch.setattr(style_job, "get_session", local_session)
    monkeypatch.setattr(style_job, "_market_today", lambda: TARGET_DATE)
    monkeypatch.setattr(style_job, "compute_style_snapshot", lambda *_args: snapshot)

    first = style_job.compute_style_daily()
    with local_session() as session:
        session.execute(update(Security).values(style_tag="defensive"))
    second = style_job.compute_style_daily(TARGET_DATE)

    assert first == second
    assert first["date"] == TARGET_DATE.isoformat()
    assert first["eligible"] == 2
    assert first["excluded"] == 1
    assert first["counts"] == snapshot.tag_counts
    assert first["weights"] == snapshot.amount_weights
    assert first["skipped"] is None
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(StyleDaily)) == 1
        row = session.get(StyleDaily, TARGET_DATE)
        assert row is not None
        assert len(row.source_fingerprint) == 64
        weight_sum = row.growth_pct + row.value_pct + row.defensive_pct + row.balanced_pct
        assert weight_sum == pytest.approx(1.0)
        tags = dict(session.execute(select(Security.symbol, Security.style_tag)).all())
    assert tags == {"600001": "growth", "600002": "value", "600003": None}


def test_style_job_rejects_inputs_that_change_during_computation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'style-race.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add(Security(symbol="600001", list_status="listed", style_tag="value"))
        _add_inputs(session, TARGET_DATE)

    fingerprints = iter(("a" * 64, "b" * 64))
    monkeypatch.setattr(style_job, "get_session", local_session)
    monkeypatch.setattr(style_job, "_market_today", lambda: TARGET_DATE)
    monkeypatch.setattr(style_job, "compute_style_snapshot", lambda *_args: _snapshot())
    monkeypatch.setattr(
        style_job,
        "style_source_fingerprint",
        lambda *_args: next(fingerprints),
    )

    with pytest.raises(JobExecutionError, match="输入发生变化") as caught:
        style_job.compute_style_daily()

    assert caught.value.stats["skipped"] == "style_inputs_changed"
    with Session(engine) as session:
        assert session.get(StyleDaily, TARGET_DATE) is None
        security = session.get(Security, "600001")
        assert security is not None
        assert security.style_tag == "value"


def test_explicit_historical_run_never_changes_current_security_tags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'style-history.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    historical_date = TARGET_DATE - timedelta(days=1)
    with local_session() as session:
        session.add_all(
            [
                Security(symbol="600001", list_status="listed", style_tag="defensive"),
                Security(symbol="600002", list_status="listed", style_tag="balanced"),
            ]
        )
        _add_inputs(session, historical_date)
        _add_inputs(session, TARGET_DATE, symbol="600002")

    monkeypatch.setattr(style_job, "get_session", local_session)
    monkeypatch.setattr(
        style_job,
        "compute_style_snapshot",
        lambda *_args: _snapshot(
            trade_date=historical_date,
            symbol_tags={"600001": "growth", "600002": "value"},
        ),
    )

    stats = style_job.compute_style_daily(historical_date)

    assert stats["date"] == historical_date.isoformat()
    with Session(engine) as session:
        assert session.get(StyleDaily, historical_date) is not None
        tags = dict(session.execute(select(Security.symbol, Security.style_tag)).all())
    assert tags == {"600001": "defensive", "600002": "balanced"}


def test_style_job_blocks_while_factor_job_is_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'style-running.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        _add_inputs(session, TARGET_DATE)
        session.add(JobRun(job_name="compute_factors", status="running", stats={}))

    monkeypatch.setattr(style_job, "get_session", local_session)
    with pytest.raises(JobExecutionError, match="因子计算任务仍在运行") as caught:
        style_job.compute_style_daily()
    assert caught.value.stats["skipped"] == "compute_factors_running"


def test_style_job_safely_skips_unready_or_stale_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'style-readiness.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        _add_inputs(session, TARGET_DATE - timedelta(days=1))
        session.add(
            DailyBar(
                symbol="600002",
                trade_date=TARGET_DATE,
                open=10.0,
                high=10.5,
                low=9.5,
                close=10.0,
                volume=100.0,
                amount=1000.0,
                source="test",
            )
        )

    monkeypatch.setattr(style_job, "get_session", local_session)
    monkeypatch.setattr(style_job, "_market_today", lambda: TARGET_DATE)
    monkeypatch.setattr(
        style_job,
        "compute_style_snapshot",
        lambda *_args: (_ for _ in ()).throw(AssertionError("engine must not run")),
    )

    unready = style_job.compute_style_daily()
    assert unready["date"] == TARGET_DATE.isoformat()
    assert unready["skipped"] == "factor_inputs_not_ready"
    assert unready["input_dates"] == {
        "daily_bars": TARGET_DATE.isoformat(),
        "composite_scores": (TARGET_DATE - timedelta(days=1)).isoformat(),
        "factor_values": (TARGET_DATE - timedelta(days=1)).isoformat(),
    }

    with local_session() as session:
        session.add(
            CompositeScore(
                symbol="600002",
                trade_date=TARGET_DATE,
                score=80.0,
                win_rate_20d=None,
                factors={},
                model_version="factor-score-v1.0.0",
            )
        )
        session.add(
            FactorValue(
                symbol="600002",
                trade_date=TARGET_DATE,
                factor="volatility_20d",
                raw=0.1,
                zscore=0.0,
                model_version="factor-v1.0.0",
            )
        )
    monkeypatch.setattr(style_job, "_market_today", lambda: TARGET_DATE + timedelta(days=1))

    stale = style_job.compute_style_daily()
    assert stale["date"] == TARGET_DATE.isoformat()
    assert stale["skipped"] == "stale_inputs"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(StyleDaily)) == 0
