from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.data.mock import MockMarketDataProvider
from alphapilot.db.models import (
    AlertOutcome,
    AlertRecord,
    Base,
    DailyBar,
    DailyReport,
    JobRun,
)
from alphapilot.jobs import alert_outcomes as alert_outcome_job
from alphapilot.jobs.registry import JOBS, JobExecutionError
from alphapilot.services import reports as report_service
from alphapilot.services.alert_outcomes import (
    HORIZON_DAYS,
    MODEL_VERSION,
    AlertOutcomeError,
    build_signal_attribution,
    evaluate_mature_alerts,
)

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
SESSIONS = [
    date(2026, 7, 13),
    date(2026, 7, 14),
    date(2026, 7, 15),
    date(2026, 7, 16),
    date(2026, 7, 17),
    date(2026, 7, 20),
]
ORIGIN_DATE = SESSIONS[0]
MATURITY_DATE = SESSIONS[5]
EVALUATED_AT = datetime(2026, 7, 20, 20, 0, tzinfo=MARKET_TIMEZONE).astimezone(UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    database = create_engine(
        f"sqlite:///{tmp_path / 'alert-outcomes.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(database)
    return database


def _ingested_at(trade_date: date, at: time = time(18, 0)) -> datetime:
    return datetime.combine(trade_date, at, tzinfo=MARKET_TIMEZONE).astimezone(UTC)


def _bar(
    symbol: str,
    trade_date: date,
    close: float,
    *,
    ingested_at: datetime | None = None,
) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000.0,
        amount=close * 1_000.0,
        source="fixture",
        ingested_at=ingested_at or _ingested_at(trade_date),
    )


def _seed_calendar(session: Session, sessions: list[date] = SESSIONS) -> None:
    session.add_all([_bar("SH.000001", trade_day, 3_800.0) for trade_day in sessions])


def _alert(
    symbol: str,
    action: str,
    position_change: float,
    *,
    as_of: date | None = ORIGIN_DATE,
    created_at: datetime | None = None,
) -> AlertRecord:
    evidence = datetime.combine(as_of, time.min, tzinfo=UTC) if as_of else None
    return AlertRecord(
        symbol=symbol,
        action=action,
        urgency="MEDIUM",
        confidence=0.7,
        suggested_position_change=position_change,
        reasons=[],
        as_of=evidence,
        created_at=created_at or datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
    )


def _seed_action_matrix(session: Session) -> list[AlertRecord]:
    cases = [
        ("600001", "BUY_CANDIDATE", 0.10, 110.0),
        ("600002", "ADD", 0.10, 90.0),
        ("600003", "REDUCE", -0.25, 90.0),
        ("600004", "EXIT", -0.25, 110.0),
        ("600005", "STOP", -0.10, 100.0),
        ("600006", "WATCH", 0.00, 102.0),
        ("600007", "REVIEW_REQUIRED", 0.00, 98.0),
    ]
    alerts: list[AlertRecord] = []
    for symbol, action, position_change, maturity_close in cases:
        alert = _alert(symbol, action, position_change)
        alerts.append(alert)
        session.add(alert)
        session.add_all(
            [
                _bar(symbol, ORIGIN_DATE, 100.0),
                _bar(symbol, MATURITY_DATE, maturity_close),
            ]
        )
    return alerts


def _local_session(engine: Engine) -> Any:
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


def test_exact_fifth_session_actions_contribution_and_idempotency(engine: Engine) -> None:
    with Session(engine) as session:
        _seed_calendar(session)
        alerts = _seed_action_matrix(session)
        session.flush()
        alert_ids = [row.id for row in alerts]

        stats = evaluate_mature_alerts(session, as_of=EVALUATED_AT)
        session.commit()

        assert stats["created"] == 7
        assert stats["directional"] == 5
        assert stats["non_directional"] == 2
        assert stats["mature"] == 7
        outcomes = {
            row.alert_id: row
            for row in session.scalars(select(AlertOutcome).order_by(AlertOutcome.alert_id))
        }
        assert set(outcomes) == set(alert_ids)
        expected = [
            (True, 0.10, 0.01),
            (False, -0.10, -0.01),
            (True, -0.10, 0.025),
            (False, 0.10, -0.025),
            (False, 0.0, 0.0),
            (None, 0.02, 0.0),
            (None, -0.02, 0.0),
        ]
        for alert_id, (hit, realized, contribution) in zip(alert_ids, expected, strict=True):
            outcome = outcomes[alert_id]
            assert outcome.hit is hit
            assert outcome.realized_return == pytest.approx(realized)
            assert outcome.contribution == pytest.approx(contribution)
            assert outcome.origin_date == ORIGIN_DATE
            assert outcome.maturity_date == MATURITY_DATE
            assert outcome.horizon_days == HORIZON_DAYS
            assert outcome.model_version == MODEL_VERSION

        rerun = evaluate_mature_alerts(session, as_of=EVALUATED_AT)
        session.commit()
        assert rerun["created"] == 0
        assert rerun["unevaluated"] == 0
        assert session.scalar(select(func.count()).select_from(AlertOutcome)) == 7


def test_evidence_as_of_is_exact_origin_not_later_created_at(engine: Engine) -> None:
    with Session(engine) as session:
        _seed_calendar(session)
        alert = _alert(
            "600001",
            "BUY_CANDIDATE",
            0.1,
            created_at=datetime(2026, 7, 14, 2, 21, tzinfo=UTC),
        )
        session.add_all(
            [
                alert,
                _bar("600001", ORIGIN_DATE, 100.0),
                _bar("600001", MATURITY_DATE, 105.0),
            ]
        )
        stats = evaluate_mature_alerts(session, as_of=EVALUATED_AT)
        session.commit()

        outcome = session.get(AlertOutcome, alert.id)
        assert stats["created"] == 1
        assert outcome is not None
        assert outcome.origin_date == ORIGIN_DATE
        assert outcome.maturity_date == MATURITY_DATE


def test_stock_dates_fill_an_interior_index_calendar_gap(engine: Engine) -> None:
    missing_index_date = SESSIONS[3]
    with Session(engine) as session:
        _seed_calendar(session, [day for day in SESSIONS if day != missing_index_date])
        session.add_all(
            [
                _bar("000001", missing_index_date, 10.0),
                _bar("000002", missing_index_date, 20.0),
            ]
        )
        alert = _alert("600001", "BUY_CANDIDATE", 0.1)
        session.add_all(
            [
                alert,
                _bar("600001", ORIGIN_DATE, 100.0),
                _bar("600001", MATURITY_DATE, 105.0),
            ]
        )

        stats = evaluate_mature_alerts(session, as_of=EVALUATED_AT)
        session.commit()
        outcome = session.get(AlertOutcome, alert.id)
        assert stats["calendar_sessions"] == 6
        assert stats["created"] == 1
        assert outcome is not None
        assert outcome.maturity_date == MATURITY_DATE


def test_non_trading_evidence_date_fails_closed(engine: Engine) -> None:
    with Session(engine) as session:
        _seed_calendar(session)
        session.add(
            _alert(
                "600001",
                "BUY_CANDIDATE",
                0.1,
                as_of=date(2026, 7, 12),
            )
        )
        with pytest.raises(AlertOutcomeError, match="不在上证交易日历"):
            evaluate_mature_alerts(session, as_of=EVALUATED_AT)
        assert not session.new


def test_before_close_current_session_is_not_counted(engine: Engine) -> None:
    with Session(engine) as session:
        _seed_calendar(session)
        alert = _alert("600001", "BUY_CANDIDATE", 0.1)
        session.add_all(
            [
                alert,
                _bar("600001", ORIGIN_DATE, 100.0),
                _bar("600001", MATURITY_DATE, 105.0),
            ]
        )
        before_close = datetime(
            2026,
            7,
            20,
            15,
            5,
            59,
            tzinfo=MARKET_TIMEZONE,
        ).astimezone(UTC)
        skipped = evaluate_mature_alerts(session, as_of=before_close)
        assert skipped["created"] == 0
        assert skipped["immature"] == 1

        completed = evaluate_mature_alerts(session, as_of=EVALUATED_AT)
        session.commit()
        assert completed["created"] == 1
        assert session.get(AlertOutcome, alert.id) is not None


def test_missing_or_intraday_endpoint_retries_after_complete_bar(engine: Engine) -> None:
    with Session(engine) as session:
        _seed_calendar(session)
        missing_origin = _alert("600001", "BUY_CANDIDATE", 0.1)
        missing_maturity = _alert("600002", "BUY_CANDIDATE", 0.1)
        intraday = _alert("600003", "BUY_CANDIDATE", 0.1)
        early_bar = _bar(
            "600003",
            MATURITY_DATE,
            105.0,
            ingested_at=_ingested_at(MATURITY_DATE, time(9, 45)),
        )
        session.add_all(
            [
                missing_origin,
                _bar("600001", MATURITY_DATE, 105.0),
                missing_maturity,
                _bar("600002", ORIGIN_DATE, 100.0),
                intraday,
                _bar("600003", ORIGIN_DATE, 100.0),
                early_bar,
            ]
        )
        session.flush()

        first = evaluate_mature_alerts(session, as_of=EVALUATED_AT)
        session.commit()
        assert first["missing_bars"] == 3
        assert first["created"] == 0
        assert session.scalar(select(func.count()).select_from(AlertOutcome)) == 0

        session.add_all(
            [
                _bar("600001", ORIGIN_DATE, 100.0),
                _bar("600002", MATURITY_DATE, 105.0),
            ]
        )
        early_bar.ingested_at = _ingested_at(MATURITY_DATE)
        second = evaluate_mature_alerts(session, as_of=EVALUATED_AT)
        session.commit()
        assert second["created"] == 3
        assert second["missing_bars"] == 0


@pytest.mark.parametrize("action", ["UNKNOWN", "BUY"])
def test_unknown_action_rolls_back_without_partial_outcomes(
    engine: Engine,
    action: str,
) -> None:
    with Session(engine) as session:
        _seed_calendar(session)
        valid = _alert("600001", "BUY_CANDIDATE", 0.1)
        invalid = _alert("600002", action, 0.1)
        session.add_all(
            [
                valid,
                invalid,
                _bar("600001", ORIGIN_DATE, 100.0),
                _bar("600001", MATURITY_DATE, 105.0),
                _bar("600002", ORIGIN_DATE, 100.0),
                _bar("600002", MATURITY_DATE, 105.0),
            ]
        )
        with pytest.raises(AlertOutcomeError, match="无法归因"):
            evaluate_mature_alerts(session, as_of=EVALUATED_AT)
        assert not any(isinstance(row, AlertOutcome) for row in session.new)
        session.rollback()
        assert session.scalar(select(func.count()).select_from(AlertOutcome)) == 0


def test_report_attribution_sorting_by_action_and_previous_rate(engine: Engine) -> None:
    with Session(engine) as session:
        _seed_calendar(session)
        _seed_action_matrix(session)
        evaluate_mature_alerts(session, as_of=EVALUATED_AT)
        session.add(
            DailyReport(
                report_date="2026-07-17",
                kind="post_market",
                payload={"signal_attribution": {"hit_rate_directional": 0.25}},
            )
        )
        session.commit()

        result = build_signal_attribution(session, MATURITY_DATE)
        assert result["outcomes"] == 7
        assert result["directional_evaluated"] == 5
        assert result["hit_rate_directional"] == pytest.approx(0.4)
        assert result["previous_report_date"] == "2026-07-17"
        assert result["previous_hit_rate_directional"] == pytest.approx(0.25)
        assert result["hit_rate_change"] == pytest.approx(0.15)
        assert result["hit_rate_change_pp"] == pytest.approx(15.0)
        assert [row["action"] for row in result["top_hits"]] == [
            "REDUCE",
            "BUY_CANDIDATE",
        ]
        assert [row["action"] for row in result["top_misses"]] == [
            "EXIT",
            "ADD",
            "STOP",
        ]
        assert result["by_action"]["WATCH"] == {
            "outcomes": 1,
            "directional_evaluated": 0,
            "hits": 0,
            "hit_rate": None,
            "contribution_total": 0.0,
        }


def test_report_empty_state_and_legacy_previous_report_are_honest(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(
            DailyReport(
                report_date="2026-07-17",
                kind="post_market",
                payload={"forecast_hit_stats": {"hit_rate": 0.9}},
            )
        )
        session.commit()

        result = build_signal_attribution(session, MATURITY_DATE)
        assert result["outcomes"] == 0
        assert result["hit_rate_directional"] is None
        assert result["previous_report_date"] == "2026-07-17"
        assert result["previous_hit_rate_directional"] is None
        assert result["hit_rate_change_pp"] is None
        assert result["top_hits"] == []
        assert result["top_misses"] == []


def test_report_rejects_corrupted_contribution(engine: Engine) -> None:
    with Session(engine) as session:
        alert = _alert("600001", "BUY_CANDIDATE", 0.1)
        session.add(alert)
        session.flush()
        session.add(
            AlertOutcome(
                alert_id=alert.id,
                horizon_days=5,
                origin_date=ORIGIN_DATE,
                maturity_date=MATURITY_DATE,
                realized_return=0.1,
                hit=True,
                contribution=0.9,
                model_version=MODEL_VERSION,
                evaluated_at=EVALUATED_AT,
            )
        )
        session.commit()

        with pytest.raises(AlertOutcomeError, match="贡献收益"):
            build_signal_attribution(session, MATURITY_DATE)


def test_daily_report_uses_shanghai_day_bounds_and_includes_attribution(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = date(2026, 7, 20)

    def utc_at(local_day: date, local_time: time) -> datetime:
        return datetime.combine(
            local_day,
            local_time,
            tzinfo=MARKET_TIMEZONE,
        ).astimezone(UTC)

    with Session(engine) as session:
        included_early = _alert(
            "600001",
            "WATCH",
            0.0,
            created_at=utc_at(target, time(0, 30)),
        )
        included_late = _alert(
            "600002",
            "WATCH",
            0.0,
            created_at=utc_at(target, time(23, 59)),
        )
        excluded_next_day = _alert(
            "600003",
            "WATCH",
            0.0,
            created_at=utc_at(date(2026, 7, 21), time(0, 1)),
        )
        session.add_all([included_early, included_late, excluded_next_day])
        session.flush()

        monkeypatch.setattr(report_service.market_data, "index_quotes", lambda _settings: [])
        monkeypatch.setattr(report_service, "tracked_overview", lambda *_args: [])
        monkeypatch.setattr(report_service, "list_items", lambda _session: [])
        monkeypatch.setattr(report_service, "list_disclosures", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(
            report_service,
            "compose_market_summary",
            lambda *_args: {"text": "fixture", "source": "template"},
        )
        payload = report_service.generate_daily_report(
            session,
            Settings(),
            MockMarketDataProvider(),
            report_date=target,
        )

        assert {row["id"] for row in payload["alerts"]} == {
            included_early.id,
            included_late.id,
        }
        assert payload["signal_attribution"]["outcomes"] == 0
        assert payload["signal_attribution"]["top_hits"] == []


@pytest.mark.parametrize("running_job", ["sync_daily_bars", "compute_style_daily"])
def test_job_blocks_while_upstream_writer_is_running(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    running_job: str,
) -> None:
    local_session = _local_session(engine)
    with local_session() as session:
        session.add(JobRun(job_name=running_job, status="running", stats={}))
    monkeypatch.setattr(alert_outcome_job, "get_session", local_session)

    with pytest.raises(JobExecutionError, match="提醒归因已延后") as caught:
        alert_outcome_job.evaluate_alerts(MATURITY_DATE)
    assert caught.value.stats["reason"] == "upstream_job_running"
    assert caught.value.stats["running_jobs"] == [running_job]


def test_stale_upstream_audit_row_is_reported_but_does_not_block(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    local_session = _local_session(engine)
    with local_session() as session:
        stale = JobRun(
            job_name="sync_daily_bars",
            status="running",
            stats={},
            started_at=fixed_now - timedelta(hours=3),
        )
        session.add(stale)
        session.flush()
        stale_id = stale.id
    monkeypatch.setattr(alert_outcome_job, "get_session", local_session)
    monkeypatch.setattr(alert_outcome_job, "_job_now", lambda: fixed_now)

    stats = alert_outcome_job.evaluate_alerts(MATURITY_DATE)
    assert stats["created"] == 0
    assert stats["warning_count"] == 1
    assert stats["stale_upstream_runs"] == [
        {
            "id": stale_id,
            "job_name": "sync_daily_bars",
            "started_at": (fixed_now - timedelta(hours=3)).isoformat(),
            "age_seconds": 10_800.0,
        }
    ]


def test_alert_outcome_cron_avoids_1940_and_has_same_evening_retry() -> None:
    previous = JOBS.get("evaluate_alerts")
    alert_outcome_job.register_alert_outcomes_job()
    try:
        trigger = JOBS["evaluate_alerts"].trigger
        assert "hour='19,20'" in str(trigger)
        assert "minute='45'" in str(trigger)
        assert str(trigger.timezone) == "Asia/Shanghai"
    finally:
        if previous is None:
            JOBS.pop("evaluate_alerts", None)
        else:
            JOBS["evaluate_alerts"] = previous
