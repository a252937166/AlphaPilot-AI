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
from alphapilot.jobs.registry import JOBS, JobOutcome

TARGET_DATE = date(2026, 7, 30)


@contextmanager
def _unused_session() -> Iterator[Any]:
    yield object()


def _state(
    *,
    factor_current: bool,
    style_current: bool,
    upstream_contract_ok: bool = True,
    live_input_reason: str | None = None,
    factor_input_reason: str | None = None,
) -> _PredictionState:
    return _PredictionState(
        target_date=TARGET_DATE,
        factor_current=factor_current,
        style_current=style_current,
        details={
            "date": TARGET_DATE.isoformat(),
            "factor_current": factor_current,
            "style_current": style_current,
            "upstream_contract_ok": upstream_contract_ok,
            "live_input_reason": live_input_reason,
            "factor_input_reason": factor_input_reason or live_input_reason,
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


def test_reconcile_defers_before_factor_when_upstream_is_not_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prediction_reconcile, "get_session", _unused_session)
    monkeypatch.setattr(
        prediction_reconcile,
        "_prediction_state",
        lambda _session: _state(
            factor_current=False,
            style_current=False,
            upstream_contract_ok=False,
            live_input_reason="daily_bars_not_final",
        ),
    )
    monkeypatch.setattr(
        prediction_reconcile,
        "run_job",
        lambda *_args, **_kwargs: pytest.fail(
            "upstream-not-final reconciliation must not spawn a child job"
        ),
    )

    result = prediction_reconcile.reconcile_prediction_outputs()

    assert isinstance(result, dict)
    assert result["skipped"] == "upstream_inputs_not_final"
    assert result["upstream_reason"] == "daily_bars_not_final"
    assert "等待上游恢复" in result["message"]


def test_reconcile_degrades_without_duplicating_factor_child_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_run_job(name: str, **kwargs: Any) -> SimpleNamespace:
        calls.append((name, kwargs))
        return SimpleNamespace(
            id=301,
            status="failed",
            error="JobExecutionError: 因子输入覆盖率低于 90% 安全阈值。",
            stats={"reason": "input_coverage_below_floor", "input_coverage": 0.8},
        )

    monkeypatch.setattr(prediction_reconcile, "get_session", _unused_session)
    monkeypatch.setattr(
        prediction_reconcile,
        "_prediction_state",
        lambda _session: _state(factor_current=False, style_current=False),
    )
    monkeypatch.setattr(prediction_reconcile, "run_job", fake_run_job)

    result = prediction_reconcile.reconcile_prediction_outputs()

    assert calls == [("compute_factors", {"allow_catchup": True})]
    assert isinstance(result, JobOutcome)
    assert result.status == "degraded"
    assert result.stats["skipped"] == "compute_factors_failed"
    assert result.stats["child_job_run_id"] == 301
    assert result.stats["child_status"] == "failed"
    assert result.stats["child_stats"] == {
        "reason": "input_coverage_below_floor",
        "input_coverage": 0.8,
    }


def test_reconcile_degrades_without_duplicating_style_child_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = iter(
        (
            _state(factor_current=True, style_current=False),
            _state(factor_current=True, style_current=False),
        )
    )
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_run_job(name: str, **kwargs: Any) -> SimpleNamespace:
        calls.append((name, kwargs))
        return SimpleNamespace(
            id=302,
            status="failed",
            error="RuntimeError: style engine unavailable",
            stats={"reason": "style_engine_unavailable"},
        )

    monkeypatch.setattr(prediction_reconcile, "get_session", _unused_session)
    monkeypatch.setattr(
        prediction_reconcile,
        "_prediction_state",
        lambda _session: next(states),
    )
    monkeypatch.setattr(prediction_reconcile, "run_job", fake_run_job)

    result = prediction_reconcile.reconcile_prediction_outputs()

    assert calls == [("compute_style_daily", {"trade_date": TARGET_DATE})]
    assert isinstance(result, JobOutcome)
    assert result.status == "degraded"
    assert result.stats["skipped"] == "compute_style_daily_failed"
    assert result.stats["child_job_run_id"] == 302
    assert result.stats["child_status"] == "failed"
    assert result.stats["child_stats"] == {"reason": "style_engine_unavailable"}


def test_reconcile_defers_when_style_child_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = iter(
        (
            _state(factor_current=True, style_current=False),
            _state(factor_current=True, style_current=False),
        )
    )

    monkeypatch.setattr(prediction_reconcile, "get_session", _unused_session)
    monkeypatch.setattr(
        prediction_reconcile,
        "_prediction_state",
        lambda _session: next(states),
    )
    monkeypatch.setattr(
        prediction_reconcile,
        "run_job",
        lambda name, **_kwargs: SimpleNamespace(
            id=303,
            status="ok",
            error=None,
            stats={"skipped": "compute_factors_running", "child": name},
        ),
    )

    result = prediction_reconcile.reconcile_prediction_outputs()

    assert isinstance(result, dict)
    assert result["skipped"] == "compute_factors_running"
    assert result["style_job_run_id"] == 303
    assert result["style_stats"] == {
        "skipped": "compute_factors_running",
        "child": "compute_style_daily",
    }


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
            "ix_factor_date" in str(row[-1]) and "SEARCH" in str(row[-1]) for row in factor_plan
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


def test_prediction_state_includes_market_coverage_in_upstream_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_time = datetime(2026, 7, 30, 11, 0, tzinfo=UTC)
    runs = {
        "sync_daily_bars": SimpleNamespace(
            id=401,
            status="ok",
            finished_at=base_time,
            stats={"latest_trade_date": TARGET_DATE.isoformat()},
        ),
        "sync_adj_factors": SimpleNamespace(
            id=402,
            status="ok",
            finished_at=base_time,
            stats={"coverage": 1.0},
        ),
        "sync_valuation_daily": SimpleNamespace(
            id=403,
            status="ok",
            finished_at=base_time,
            stats={"is_complete": True},
        ),
    }
    monkeypatch.setattr(prediction_reconcile, "_target_date", lambda _session: TARGET_DATE)
    monkeypatch.setattr(
        prediction_reconcile,
        "_market_coverage",
        lambda _session, _target: {
            "date": TARGET_DATE.isoformat(),
            "universe": 10,
            "eligible": 8,
            "input_coverage": 0.8,
        },
    )
    monkeypatch.setattr(
        prediction_reconcile,
        "_completed_run_for_date",
        lambda _session, _name, _target: None,
    )
    monkeypatch.setattr(
        prediction_reconcile,
        "_latest_run",
        lambda _session, name: runs[name],
    )
    monkeypatch.setattr(
        prediction_reconcile,
        "_live_input_contract",
        lambda _session, *, target_date, universe: (
            {
                "checked_target_date": target_date.isoformat(),
                "checked_universe": universe,
            },
            None,
        ),
    )
    monkeypatch.setattr(
        prediction_reconcile,
        "get_settings",
        lambda: SimpleNamespace(factor_weights_file="unused.yaml"),
    )
    monkeypatch.setattr(
        prediction_reconcile,
        "load_weights",
        lambda _path: SimpleNamespace(version="v1.0.0"),
    )

    with Session(create_engine("sqlite://")) as session:
        state = prediction_reconcile._prediction_state(session)

    assert state.factor_current is False
    assert state.style_current is False
    assert state.details["eligible"] == 8
    assert state.details["input_coverage"] == 0.8
    assert state.details["live_input_reason"] is None
    assert state.details["factor_input_reason"] == "input_coverage_below_floor"
    assert state.details["upstream_contract_ok"] is False


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
