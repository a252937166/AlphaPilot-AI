from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from alphapilot.db.models import (
    AdjFactor,
    Base,
    CompositeScore,
    DailyBar,
    FactorValue,
    JobRun,
    Security,
    StockScore,
    StyleDaily,
    ValuationDaily,
)
from alphapilot.engines.factors import FACTOR_SET
from alphapilot.jobs import prediction_reconcile
from alphapilot.jobs.prediction_reconcile import _PredictionState
from alphapilot.jobs.registry import JOBS

TARGET_DATE = date(2026, 7, 30)


@contextmanager
def _unused_session() -> Iterator[Any]:
    yield object()


def _state(*, factor_current: bool, style_current: bool) -> _PredictionState:
    return _PredictionState(
        target_date=TARGET_DATE,
        factor_current=factor_current,
        style_current=style_current,
        details={
            "date": TARGET_DATE.isoformat(),
            "factor_current": factor_current,
            "style_current": style_current,
        },
    )


def test_reconcile_runs_factor_then_style_and_converges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = iter(
        (
            _state(factor_current=False, style_current=False),
            _state(factor_current=True, style_current=False),
            _state(factor_current=True, style_current=True),
        )
    )
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_run_job(name: str, **kwargs: Any) -> SimpleNamespace:
        calls.append((name, kwargs))
        if name == "compute_factors":
            return SimpleNamespace(
                id=101,
                status="ok",
                error=None,
                stats={"date": TARGET_DATE.isoformat()},
            )
        return SimpleNamespace(
            id=102,
            status="ok",
            error=None,
            stats={"date": TARGET_DATE.isoformat(), "skipped": None},
        )

    monkeypatch.setattr(prediction_reconcile, "get_session", _unused_session)
    monkeypatch.setattr(
        prediction_reconcile,
        "_prediction_state",
        lambda _session: next(states),
    )
    monkeypatch.setattr(prediction_reconcile, "run_job", fake_run_job)

    stats = prediction_reconcile.reconcile_prediction_outputs()

    assert calls == [
        ("compute_factors", {"allow_catchup": True}),
        ("compute_style_daily", {"trade_date": TARGET_DATE}),
    ]
    assert stats["skipped"] is None
    assert stats["factor_job_run_id"] == 101
    assert stats["style_job_run_id"] == 102


def test_reconcile_waits_without_running_style_when_factor_is_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_run_job(name: str, **_kwargs: Any) -> SimpleNamespace:
        calls.append(name)
        return SimpleNamespace(
            id=201,
            status="ok",
            error=None,
            stats={
                "date": TARGET_DATE.isoformat(),
                "skipped": "valuation_sync_running",
            },
        )

    monkeypatch.setattr(prediction_reconcile, "get_session", _unused_session)
    monkeypatch.setattr(
        prediction_reconcile,
        "_prediction_state",
        lambda _session: _state(factor_current=False, style_current=False),
    )
    monkeypatch.setattr(prediction_reconcile, "run_job", fake_run_job)

    stats = prediction_reconcile.reconcile_prediction_outputs()

    assert calls == ["compute_factors"]
    assert stats["skipped"] == "valuation_sync_running"
    assert stats["factor_job_run_id"] == 201


def test_reconcile_is_a_noop_when_both_outputs_are_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prediction_reconcile, "get_session", _unused_session)
    monkeypatch.setattr(
        prediction_reconcile,
        "_prediction_state",
        lambda _session: _state(factor_current=True, style_current=True),
    )
    monkeypatch.setattr(
        prediction_reconcile,
        "run_job",
        lambda *_args, **_kwargs: pytest.fail("current outputs must not be recomputed"),
    )

    stats = prediction_reconcile.reconcile_prediction_outputs()

    assert stats["skipped"] == "already_current"


def test_prediction_state_requires_outputs_newer_than_the_latest_eod_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'prediction-state.db'}")
    Base.metadata.create_all(engine)
    weights_path = tmp_path / "weights.yaml"
    weights_path.write_text(
        """\
version: v1.0.0
profile: test
weights:
  momentum_20d: 1.0
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        prediction_reconcile,
        "get_settings",
        lambda: SimpleNamespace(factor_weights_file=str(weights_path)),
    )
    base_time = datetime(2026, 7, 30, 11, 0, tzinfo=UTC)
    symbol = "600000"
    with Session(engine) as session:
        session.add(Security(symbol=symbol, market="CN", list_status="listed"))
        session.add(
            DailyBar(
                symbol=symbol,
                trade_date=TARGET_DATE,
                open=10.0,
                high=10.0,
                low=10.0,
                close=10.0,
                volume=100.0,
                amount=1000.0,
                source="baostock",
            )
        )
        session.add(
            AdjFactor(
                symbol=symbol,
                trade_date=TARGET_DATE,
                adj_factor=1.0,
                source="test",
            )
        )
        session.add(
            ValuationDaily(
                symbol=symbol,
                trade_date=TARGET_DATE,
                pe_ttm=10.0,
                pb_mrq=1.0,
                ps_ttm=2.0,
                source="em",
                available_time=datetime(2026, 7, 30, 7, 0, tzinfo=UTC),
            )
        )
        session.add_all(
            FactorValue(
                symbol=symbol,
                trade_date=TARGET_DATE,
                factor=factor,
                raw=1.0,
                zscore=0.0,
                model_version="factor-v1.0.0",
            )
            for factor in FACTOR_SET
        )
        session.add(
            CompositeScore(
                symbol=symbol,
                trade_date=TARGET_DATE,
                score=50.0,
                factors={},
                model_version="factor-score-v1.0.0",
            )
        )
        session.add(
            StockScore(
                symbol=symbol,
                trade_date=TARGET_DATE,
                tech=5.0,
                capital=5.0,
                fundamental=5.0,
                valuation=5.0,
                sentiment=5.0,
                composite=5.0,
                model_version="stock-score-v1.0.0",
            )
        )
        session.add(
            StyleDaily(
                trade_date=TARGET_DATE,
                growth_pct=0.25,
                value_pct=0.25,
                defensive_pct=0.25,
                balanced_pct=0.25,
                model_version="style-v1.0.0",
                source_fingerprint="a" * 64,
            )
        )
        session.add_all(
            [
                JobRun(
                    job_name="sync_daily_bars",
                    status="ok",
                    started_at=base_time,
                    finished_at=base_time + timedelta(minutes=1),
                    stats={"latest_trade_date": TARGET_DATE.isoformat()},
                ),
                JobRun(
                    job_name="sync_adj_factors",
                    status="ok",
                    started_at=base_time,
                    finished_at=base_time + timedelta(minutes=2),
                    stats={"coverage": 1.0},
                ),
                JobRun(
                    job_name="sync_valuation_daily",
                    status="ok",
                    started_at=base_time,
                    finished_at=base_time + timedelta(minutes=3),
                    stats={
                        "end_date": TARGET_DATE.isoformat(),
                        "is_complete": True,
                        "symbols_total": 1,
                        "symbols_no_data": 0,
                        "symbols_failed": 0,
                        "failures": [],
                    },
                ),
                JobRun(
                    job_name="compute_factors",
                    status="ok",
                    started_at=base_time + timedelta(minutes=4),
                    finished_at=base_time + timedelta(minutes=5),
                    stats={"date": TARGET_DATE.isoformat(), "skipped": None},
                ),
                JobRun(
                    job_name="compute_style_daily",
                    status="ok",
                    started_at=base_time + timedelta(minutes=6),
                    finished_at=base_time + timedelta(minutes=7),
                    stats={"date": TARGET_DATE.isoformat(), "skipped": None},
                ),
            ]
        )
        session.commit()

        current = prediction_reconcile._prediction_state(session)
        assert current.factor_current is True
        assert current.style_current is True
        factor_plan = session.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT factor, count(*) FROM factor_values "
                "WHERE factor IN ('momentum_20d', 'pe_percentile') "
                "AND trade_date = :trade_date AND model_version = :model_version "
                "GROUP BY factor"
            ),
            {
                "trade_date": TARGET_DATE.isoformat(),
                "model_version": "factor-v1.0.0",
            },
        ).all()
        assert any(
            "ix_factor_date" in str(row[-1]) and "SEARCH" in str(row[-1])
            for row in factor_plan
        )

        session.add(
            JobRun(
                job_name="sync_valuation_daily",
                status="ok",
                started_at=base_time + timedelta(minutes=8),
                finished_at=base_time + timedelta(minutes=9),
                stats={
                    "end_date": TARGET_DATE.isoformat(),
                    "is_complete": True,
                    "symbols_total": 1,
                    "symbols_no_data": 0,
                    "symbols_failed": 0,
                    "failures": [],
                },
            )
        )
        session.commit()

        stale_after_upstream_refresh = prediction_reconcile._prediction_state(session)
        assert stale_after_upstream_refresh.factor_current is False
        assert stale_after_upstream_refresh.style_current is False


def test_reconcile_cron_retries_every_fifteen_minutes_with_safe_offset() -> None:
    prediction_reconcile.register_prediction_reconcile_job()
    try:
        trigger = JOBS["reconcile_prediction_outputs"].trigger
        assert str(trigger.fields[4]) == "mon-sat"
        assert str(trigger.fields[5]) == "0-1,7-9,19-23"
        assert str(trigger.fields[6]) == "7,22,37,52"
        assert str(trigger.timezone) == "Asia/Shanghai"
    finally:
        JOBS.pop("reconcile_prediction_outputs", None)
