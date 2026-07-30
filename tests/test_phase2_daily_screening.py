from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session

from alphapilot.db.models import (
    Base,
    BrokerOrder,
    CompositeScore,
    RuntimeFlag,
    ScreeningRun,
    StyleDaily,
    TradeProposalRecord,
)
from alphapilot.domain.models import ScreeningCandidate, ScreeningResponse
from alphapilot.jobs import daily_screening
from alphapilot.jobs.registry import JOBS, JobExecutionError

TARGET_DATE = date(2026, 7, 30)


def _session_factory(engine: Engine) -> object:
    @contextmanager
    def sessions() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    return sessions


def _seed_source(session: Session, *, with_style: bool = True) -> None:
    session.add(
        CompositeScore(
            symbol="600001",
            trade_date=TARGET_DATE,
            score=98.0,
            factors={},
            model_version="factor-score-v1.0.0",
        )
    )
    if with_style:
        session.add(
            StyleDaily(
                trade_date=TARGET_DATE,
                growth_pct=0.25,
                value_pct=0.25,
                defensive_pct=0.25,
                balanced_pct=0.25,
                source_fingerprint="a" * 64,
            )
        )
    session.commit()


def _response() -> ScreeningResponse:
    return ScreeningResponse(
        generated_at=datetime(2026, 7, 30, 13, 0, tzinfo=UTC),
        provider="factor-db",
        model_version="factor-score-v1.0.0",
        requested=1,
        succeeded=1,
        failed={},
        candidates=[
            ScreeningCandidate(
                rank=1,
                symbol="600001",
                score=98.0,
                display_name="测试股票",
                style="balanced",
                trade_date=TARGET_DATE,
                reasons=["综合因子评分 98.00。"],
            )
        ],
    )


def test_daily_screening_persists_once_per_prediction_lineage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'daily-screening.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_source(session)
        session.add(RuntimeFlag(key="trading_halted", value=True))
        session.commit()

    monkeypatch.setattr(daily_screening, "get_session", _session_factory(engine))
    lineage = {"factor_job_run_id": 101, "style_job_run_id": 102}
    monkeypatch.setattr(
        daily_screening,
        "prediction_outputs_readiness",
        lambda _session: (
            TARGET_DATE,
            True,
            {
                "date": TARGET_DATE.isoformat(),
                "output_contract_ok": True,
                **lineage,
            },
        ),
    )
    monkeypatch.setattr(
        daily_screening,
        "run_factor_screen",
        lambda _session, _request: _response(),
    )

    first = daily_screening.generate_daily_screening()
    second = daily_screening.generate_daily_screening()
    lineage["factor_job_run_id"] = 103
    corrected = daily_screening.generate_daily_screening()

    assert first["skipped"] is None
    assert first["candidate_count"] == 1
    assert second["skipped"] == "already_current"
    assert second["screen_run_id"] == first["screen_run_id"]
    assert corrected["skipped"] is None
    assert corrected["screen_run_id"] != first["screen_run_id"]
    with Session(engine) as session:
        records = session.scalars(select(ScreeningRun).order_by(ScreeningRun.id)).all()
        assert len(records) == 2
        assert records[0].idempotency_key is not None
        assert records[1].idempotency_key != records[0].idempotency_key
        assert records[0].filters["top_n"] == 50
        assert records[0].filters["sort_by"] == "score"
        assert session.scalar(select(func.count()).select_from(TradeProposalRecord)) == 0
        assert session.scalar(select(func.count()).select_from(BrokerOrder)) == 0
        halted = session.get(RuntimeFlag, "trading_halted")
        assert halted is not None
        assert halted.value is True


def test_daily_screening_skips_without_complete_prediction_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'daily-screening-stale.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(daily_screening, "get_session", _session_factory(engine))
    monkeypatch.setattr(
        daily_screening,
        "prediction_outputs_readiness",
        lambda _session: (
            TARGET_DATE,
            False,
            {"date": TARGET_DATE.isoformat(), "output_contract_ok": False},
        ),
    )

    stats = daily_screening.generate_daily_screening()

    assert stats["skipped"] == "prediction_outputs_not_current"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ScreeningRun)) == 0


def test_daily_screening_rejects_missing_style_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'daily-screening-no-style.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_source(session, with_style=False)
    monkeypatch.setattr(daily_screening, "get_session", _session_factory(engine))
    monkeypatch.setattr(
        daily_screening,
        "prediction_outputs_readiness",
        lambda _session: (
            TARGET_DATE,
            True,
            {
                "date": TARGET_DATE.isoformat(),
                "output_contract_ok": True,
                "factor_job_run_id": 101,
                "style_job_run_id": 102,
            },
        ),
    )

    with pytest.raises(JobExecutionError, match="评分或风格截面缺失"):
        daily_screening.generate_daily_screening()


def test_daily_screening_discards_response_when_lineage_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'daily-screening-race.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_source(session)
    monkeypatch.setattr(daily_screening, "get_session", _session_factory(engine))
    states = iter(
        (
            (
                TARGET_DATE,
                True,
                {
                    "date": TARGET_DATE.isoformat(),
                    "output_contract_ok": True,
                    "factor_job_run_id": 101,
                    "style_job_run_id": 102,
                },
            ),
            (
                TARGET_DATE,
                True,
                {
                    "date": TARGET_DATE.isoformat(),
                    "output_contract_ok": True,
                    "factor_job_run_id": 103,
                    "style_job_run_id": 104,
                },
            ),
        )
    )
    monkeypatch.setattr(
        daily_screening,
        "prediction_outputs_readiness",
        lambda _session: next(states),
    )
    monkeypatch.setattr(
        daily_screening,
        "run_factor_screen",
        lambda _session, _request: _response(),
    )

    stats = daily_screening.generate_daily_screening()

    assert stats["skipped"] == "screening_inputs_changed"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ScreeningRun)) == 0


def test_daily_screening_cron_follows_reconcile_with_safe_offset() -> None:
    daily_screening.register_daily_screening_job()
    try:
        spec = JOBS["daily_screening"]
        trigger = spec.trigger
        assert spec.func is daily_screening.generate_daily_screening
        assert str(trigger.fields[4]) == "mon-sat"
        assert str(trigger.fields[5]) == "0-1,7-9,19-23"
        assert str(trigger.fields[6]) == "12,27,42,57"
        assert str(trigger.timezone) == "Asia/Shanghai"
    finally:
        JOBS.pop("daily_screening", None)
