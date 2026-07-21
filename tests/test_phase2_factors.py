from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, settings_dependency
from alphapilot.db.models import (
    Base,
    CompositeScore,
    DailyBar,
    FactorValue,
    FinancialIndicator,
    JobRun,
    ScoreOutcomeStat,
    SectorConstituent,
    SectorFlowDaily,
    SectorSnapshot,
    Security,
)
from alphapilot.engines.factors import (
    FACTOR_SET,
    _trading_dates,
    composite,
    compute_factors_for_date,
    zscore_cross_section,
)
from alphapilot.engines.score_outcomes import (
    MODEL_VERSION as SCORE_OUTCOME_MODEL_VERSION,
)
from alphapilot.engines.score_outcomes import (
    score_decile,
)
from alphapilot.jobs import factors as factor_job
from alphapilot.jobs.registry import JOBS, JobExecutionError
from alphapilot.main import app

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


def _weights_file(path: Path) -> Path:
    path.write_text(
        """\
version: v1.0.0
profile: test
weights:
  net_profit_yoy: 0.20
  pe_percentile: -0.18
  revenue_yoy: 0.16
  net_inflow_5d: 0.14
  momentum_20d: 0.12
  sector_strength: 0.08
  ocf_to_profit: 0.07
  turnover_change_5d: 0.05
""",
        encoding="utf-8",
    )
    return path


def _trade_dates() -> list[date]:
    return [stamp.date() for stamp in pd.bdate_range(end=TARGET_DATE, periods=61)]


def _add_bars(
    session: Session,
    symbol: str,
    *,
    closes: list[float],
    amounts: list[float | None],
    volumes: list[float],
    dates: list[date] | None = None,
) -> None:
    selected_dates = dates or _trade_dates()
    assert len(selected_dates) == len(closes) == len(amounts) == len(volumes)
    session.add_all(
        DailyBar(
            symbol=symbol,
            trade_date=trade_day,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=volume,
            amount=amount,
            source="test",
        )
        for trade_day, close, amount, volume in zip(
            selected_dates, closes, amounts, volumes, strict=True
        )
    )


def _seed_engine_inputs(session: Session) -> None:
    dates = _trade_dates()
    snapshot_at = datetime(2026, 7, 21, 8, tzinfo=UTC)
    session.add_all(
        [
            Security(
                symbol="600000",
                board="主板",
                is_st=False,
                list_status="listed",
                pe_ttm=10.0,
                pb=1.0,
                snapshot_at=snapshot_at,
            ),
            Security(
                symbol="000001",
                board="主板",
                is_st=False,
                list_status="listed",
                pe_ttm=20.0,
                pb=2.0,
                snapshot_at=snapshot_at,
            ),
            Security(
                symbol="300001",
                board="创业板",
                is_st=False,
                list_status="listed",
                pe_ttm=-5.0,
                pb=0.0,
                snapshot_at=snapshot_at,
            ),
            Security(
                symbol="600999",
                board="主板",
                is_st=True,
                list_status="listed",
            ),
            Security(
                symbol="000999",
                board="主板",
                is_st=False,
                list_status="listed",
            ),
            Security(
                symbol="600888",
                board="主板",
                is_st=False,
                list_status="delisted",
            ),
        ]
    )

    rising = [float(100 + index) for index in range(61)]
    flat = [100.0] * 61
    falling = [float(160 - index) for index in range(61)]
    amount_up: list[float | None] = [100.0] * 56 + [200.0] * 5
    amount_flat: list[float | None] = [50.0] * 61
    amount_unusable: list[float | None] = [80.0] * 51 + [None] * 9 + [80.0]
    volume_up = [10.0] * 56 + [20.0] * 5
    _add_bars(
        session,
        "600000",
        closes=rising,
        amounts=amount_up,
        volumes=[10.0] * 61,
    )
    _add_bars(
        session,
        "000001",
        closes=flat,
        amounts=amount_flat,
        volumes=[10.0] * 61,
    )
    _add_bars(
        session,
        "300001",
        closes=falling,
        amounts=amount_unusable,
        volumes=volume_up,
    )
    _add_bars(
        session,
        "600999",
        closes=rising,
        amounts=amount_up,
        volumes=[10.0] * 61,
    )
    paused_volume = [10.0] * 60 + [0.0]
    paused_amount: list[float | None] = [100.0] * 60 + [0.0]
    _add_bars(
        session,
        "000999",
        closes=flat,
        amounts=paused_amount,
        volumes=paused_volume,
    )
    _add_bars(
        session,
        "600888",
        closes=rising,
        amounts=amount_up,
        volumes=[10.0] * 61,
    )

    session.add_all(
        [
            FinancialIndicator(
                symbol="600000",
                report_period="2025Q4",
                metric="roe",
                value=0.10,
                source="test",
                available_time=datetime(2026, 7, 1, tzinfo=UTC),
            ),
            FinancialIndicator(
                symbol="600000",
                report_period="2026Q1",
                metric="roe",
                value=None,
                source="test",
                available_time=datetime(2026, 7, 10, tzinfo=UTC),
            ),
            FinancialIndicator(
                symbol="600000",
                report_period="2026Q2",
                metric="roe",
                value=0.90,
                source="test",
                available_time=datetime(2026, 7, 22, tzinfo=UTC),
            ),
            SectorConstituent(
                plate_code="SH.LIST0001",
                plate_name="沪市行业",
                symbol="SH.600000",
                refreshed_at=snapshot_at,
            ),
            SectorConstituent(
                plate_code="SH.LIST0002",
                plate_name="深市行业",
                symbol="SZ.000001",
                refreshed_at=snapshot_at,
            ),
        ]
    )
    for offset, trade_day in enumerate(dates[-5:], start=1):
        session.add(
            SectorFlowDaily(
                plate_code="SH.LIST0001",
                trade_date=trade_day,
                net_inflow=float(offset),
                main_inflow=None,
                source="futu-top5",
            )
        )
        session.add(
            SectorFlowDaily(
                plate_code="SH.LIST0002",
                trade_date=trade_day,
                net_inflow=10.0,
                main_inflow=None,
                source="em" if offset == 5 else "futu-top5",
            )
        )
    session.add_all(
        [
            SectorSnapshot(
                as_of=datetime(2026, 7, 20, 8, tzinfo=UTC),
                payload=[
                    {"plate_code": "SH.LIST0001", "strength": 99.0},
                    {"plate_code": "SH.LIST0002", "strength": 99.0},
                ],
                source="test",
            ),
            SectorSnapshot(
                as_of=datetime(2026, 7, 21, 7, tzinfo=UTC),
                payload=[
                    {"plate_code": "SH.LIST0001", "strength": 4.0},
                    {"plate_code": "SH.LIST0002", "strength": 3.0},
                ],
                source="test",
            ),
            SectorSnapshot(
                as_of=datetime(2026, 7, 21, 9, tzinfo=UTC),
                payload=[
                    {"plate_code": "SH.LIST0001", "strength": 8.0},
                    {"plate_code": "SH.LIST0002", "strength": 6.0},
                ],
                source="test",
            ),
            SectorSnapshot(
                as_of=datetime(2026, 7, 22, 8, tzinfo=UTC),
                payload=[
                    {"plate_code": "SH.LIST0001", "strength": 100.0},
                    {"plate_code": "SH.LIST0002", "strength": 100.0},
                ],
                source="test",
            ),
        ]
    )


def test_zscore_winsorizes_population_and_preserves_missing_values() -> None:
    frame = pd.DataFrame(
        {
            "signal": [1.0, 2.0, 3.0],
            "constant": [5.0, 5.0, np.nan],
        },
        index=["A", "B", "C"],
    )

    result = zscore_cross_section(frame)

    assert result["signal"].tolist() == pytest.approx([-1.2247448714, 0.0, 1.2247448714])
    assert result.loc["A", "constant"] == 0.0
    assert result.loc["B", "constant"] == 0.0
    assert pd.isna(result.loc["C", "constant"])


def test_composite_uses_global_l1_weights_and_neutral_missing_values() -> None:
    standardized = pd.DataFrame(
        {
            "net_profit_yoy": [1.0, 0.5, 1.0, np.nan],
            "pe_percentile": [1.0, -1.0, np.nan, np.nan],
        },
        index=["A", "B", "C", "D"],
    )

    scores = composite(
        standardized,
        {"net_profit_yoy": 0.20, "pe_percentile": -0.18},
    )

    assert scores.loc["B"] == pytest.approx(100.0)
    assert scores.loc["C"] == pytest.approx(2 / 3 * 100)
    assert scores.loc["A"] == pytest.approx(1 / 3 * 100)
    assert scores.loc["D"] == pytest.approx(0.0)


def test_trading_dates_falls_back_when_benchmark_is_stale(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'factor-calendar.db'}")
    Base.metadata.create_all(engine)
    current_dates = [stamp.date() for stamp in pd.bdate_range(end=TARGET_DATE, periods=90)]
    stale_dates = [
        stamp.date() for stamp in pd.bdate_range(end=TARGET_DATE - timedelta(days=1), periods=90)
    ]
    with Session(engine) as session:
        session.add(
            Security(
                symbol="600000",
                market="CN",
                board="主板",
                is_st=False,
                list_status="listed",
            )
        )
        _add_bars(
            session,
            "600000",
            dates=current_dates,
            closes=[10.0] * 90,
            amounts=[100.0] * 90,
            volumes=[10.0] * 90,
        )
        _add_bars(
            session,
            "SH.000001",
            dates=stale_dates,
            closes=[3000.0] * 90,
            amounts=[1000.0] * 90,
            volumes=[100.0] * 90,
        )
        session.commit()

        result = _trading_dates(session, TARGET_DATE)

    assert len(result) == 90
    assert result[-1] == TARGET_DATE


def test_compute_factors_obeys_price_pit_valuation_and_sector_semantics(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'factor-engine.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_engine_inputs(session)
        session.commit()

        frame = compute_factors_for_date(session, TARGET_DATE)

    assert list(frame.index) == ["000001", "300001", "600000"]
    assert set(frame.columns) == set(FACTOR_SET)
    assert frame.loc["600000", "momentum_20d"] == pytest.approx(160 / 140 - 1)
    assert frame.loc["600000", "momentum_60d"] == pytest.approx(0.60)
    expected_volatility = pd.Series([float(100 + index) for index in range(61)]).pct_change(
        fill_method=None
    ).tail(20).std(ddof=0) * np.sqrt(252)
    assert frame.loc["600000", "volatility_20d"] == pytest.approx(expected_volatility)
    assert frame.loc["600000", "turnover_change_5d"] == pytest.approx(1.0)
    assert frame.loc["300001", "turnover_change_5d"] == pytest.approx(1.0)

    assert frame.loc["600000", "roe"] == pytest.approx(0.10)
    assert frame.loc["600000", "pe_percentile"] == pytest.approx(0.5)
    assert frame.loc["000001", "pe_percentile"] == pytest.approx(1.0)
    assert pd.isna(frame.loc["300001", "pe_percentile"])
    assert frame.loc["600000", "pb_percentile"] == pytest.approx(0.5)
    assert pd.isna(frame.loc["300001", "pb_percentile"])

    assert frame.loc["600000", "net_inflow_5d"] == pytest.approx(15.0)
    assert pd.isna(frame.loc["000001", "net_inflow_5d"])
    assert frame.attrs["sector_flow_days"] == 5
    assert frame.loc["600000", "sector_strength"] == pytest.approx(8.0)
    assert frame.loc["000001", "sector_strength"] == pytest.approx(6.0)


def test_factor_job_replaces_same_day_rows_and_reports_coverage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'factor-job.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    weights_path = _weights_file(tmp_path / "weights.yaml")
    with local_session() as session:
        _seed_engine_inputs(session)
        session.execute(
            # The job's readiness denominator is the listed universe. Keep this
            # integration fixture focused on the three eligible securities.
            Security.__table__.delete().where(Security.symbol.in_(["600999", "000999", "600888"]))
        )
        session.add_all(
            ScoreOutcomeStat(
                decile=decile,
                horizon=20,
                samples=10,
                positive_samples=decile,
                win_rate=decile / 10,
                score_model_version="factor-score-v1.0.0",
                model_version=SCORE_OUTCOME_MODEL_VERSION,
                as_of_date=TARGET_DATE,
                updated_at=datetime(2026, 7, 21, 11, 0, tzinfo=UTC),
            )
            for decile in range(1, 11)
        )

    monkeypatch.setattr(factor_job, "get_session", local_session)
    monkeypatch.setattr(
        factor_job,
        "get_settings",
        lambda: SimpleNamespace(factor_weights_file=str(weights_path)),
    )
    monkeypatch.setattr(
        factor_job,
        "_utc_now",
        lambda: datetime(2026, 7, 21, 14, 0, tzinfo=UTC),
    )

    first = factor_job.compute_factors(TARGET_DATE)
    with local_session() as session:
        session.add(
            CompositeScore(
                symbol="999999",
                trade_date=TARGET_DATE,
                score=999.0,
                factors={},
                model_version="stale",
            )
        )
        session.add(
            FactorValue(
                symbol="999999",
                trade_date=TARGET_DATE,
                factor="momentum_20d",
                raw=999.0,
                zscore=999.0,
                model_version="stale",
            )
        )
    second = factor_job.compute_factors(TARGET_DATE)

    assert first["date"] == TARGET_DATE.isoformat()
    assert first["universe"] == 3
    assert first["eligible"] == 3
    assert first["input_coverage"] == 1.0
    assert first["symbols"] == 3
    assert first["factor_rows"] == 3 * len(FACTOR_SET)
    assert first["composite_rows"] == 3
    assert first["coverage"]["momentum_20d"] == {"count": 3, "ratio": 1.0}
    assert first["sector_flow_days"] == 5
    assert first["outcome_win_rate_deciles"] == list(range(1, 11))
    assert second["symbols"] == 3
    assert second["outcome_win_rate_deciles"] == list(range(1, 11))

    with local_session() as session:
        assert session.scalar(select(func.count()).select_from(FactorValue)) == 3 * len(FACTOR_SET)
        assert session.scalar(select(func.count()).select_from(CompositeScore)) == 3
        current_scores = session.scalars(select(CompositeScore)).all()
        assert all(row.win_rate_20d is not None for row in current_scores)
        for row in current_scores:
            assert row.win_rate_20d == pytest.approx(score_decile(row.score) / 10)
        assert (
            session.scalar(
                select(func.count())
                .select_from(CompositeScore)
                .where(CompositeScore.symbol == "999999")
            )
            == 0
        )


def test_outcome_win_rates_enforces_engine_version_and_1930_pit_cutoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'factor-outcome-pit.db'}")
    Base.metadata.create_all(engine)
    historical_date = TARGET_DATE - timedelta(days=1)
    with Session(engine) as session:
        session.add_all(
            [
                ScoreOutcomeStat(
                    decile=1,
                    horizon=20,
                    samples=10,
                    positive_samples=1,
                    win_rate=0.1,
                    score_model_version="factor-score-v1.0.0",
                    model_version=SCORE_OUTCOME_MODEL_VERSION,
                    as_of_date=historical_date,
                    updated_at=datetime(2026, 7, 20, 11, 29, tzinfo=UTC),
                ),
                ScoreOutcomeStat(
                    decile=2,
                    horizon=20,
                    samples=10,
                    positive_samples=2,
                    win_rate=0.2,
                    score_model_version="factor-score-v1.0.0",
                    model_version=SCORE_OUTCOME_MODEL_VERSION,
                    as_of_date=historical_date,
                    updated_at=datetime(2026, 7, 20, 11, 31, tzinfo=UTC),
                ),
                ScoreOutcomeStat(
                    decile=3,
                    horizon=20,
                    samples=10,
                    positive_samples=3,
                    win_rate=0.3,
                    score_model_version="factor-score-v1.0.0",
                    model_version="score-outcome-v0.9.0",
                    as_of_date=historical_date,
                    updated_at=datetime(2026, 7, 20, 11, 0, tzinfo=UTC),
                ),
            ]
        )
        session.commit()

        monkeypatch.setattr(
            factor_job,
            "_utc_now",
            lambda: datetime(2026, 7, 21, 14, 0, tzinfo=UTC),
        )

        rates = factor_job._outcome_win_rates(
            session,
            trade_date=historical_date,
            score_model_version="factor-score-v1.0.0",
        )

    assert rates == {1: 0.1}


def test_outcome_win_rates_keep_same_day_2000_state_when_now_is_2200(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'factor-outcome-current.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                ScoreOutcomeStat(
                    decile=1,
                    horizon=20,
                    samples=10,
                    positive_samples=6,
                    win_rate=0.6,
                    score_model_version="factor-score-v1.0.0",
                    model_version=SCORE_OUTCOME_MODEL_VERSION,
                    as_of_date=TARGET_DATE,
                    updated_at=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
                ),
                ScoreOutcomeStat(
                    decile=2,
                    horizon=20,
                    samples=10,
                    positive_samples=7,
                    win_rate=0.7,
                    score_model_version="factor-score-v1.0.0",
                    model_version="score-outcome-v0.9.0",
                    as_of_date=TARGET_DATE,
                    updated_at=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
        monkeypatch.setattr(
            factor_job,
            "_utc_now",
            lambda: datetime(2026, 7, 21, 14, 0, tzinfo=UTC),
        )

        rates = factor_job._outcome_win_rates(
            session,
            trade_date=TARGET_DATE,
            score_model_version="factor-score-v1.0.0",
        )

    assert rates == {1: 0.6}


def test_factor_job_skips_implicit_stale_market_date_without_rewriting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'factor-stale-date.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    weights_path = _weights_file(tmp_path / "weights.yaml")
    stale_day = TARGET_DATE - timedelta(days=1)
    with local_session() as session:
        session.add(
            Security(
                symbol="600000",
                market="CN",
                board="主板",
                is_st=False,
                list_status="listed",
            )
        )
        _add_bars(
            session,
            "600000",
            dates=[stale_day],
            closes=[10.0],
            amounts=[100.0],
            volumes=[10.0],
        )
        session.add(
            CompositeScore(
                symbol="600000",
                trade_date=stale_day,
                score=77.0,
                factors={},
                model_version="existing",
            )
        )

    monkeypatch.setattr(factor_job, "get_session", local_session)
    monkeypatch.setattr(factor_job, "_market_today", lambda: TARGET_DATE)
    monkeypatch.setattr(
        factor_job,
        "get_settings",
        lambda: SimpleNamespace(factor_weights_file=str(weights_path)),
    )
    monkeypatch.setattr(
        factor_job,
        "compute_factors_for_date",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale implicit date must not reach the factor engine")
        ),
    )

    stats = factor_job.compute_factors()

    assert stats["date"] == stale_day.isoformat()
    assert stats["skipped"] == "stale_daily_bars"
    assert stats["expected_date"] == TARGET_DATE.isoformat()
    with local_session() as session:
        existing = session.scalar(select(CompositeScore))
        assert existing is not None
        assert existing.score == pytest.approx(77.0)
        assert existing.model_version == "existing"


def test_factor_job_rejects_partial_market_and_running_daily_sync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'factor-readiness.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    weights_path = _weights_file(tmp_path / "weights.yaml")
    with local_session() as session:
        for index in range(10):
            symbol = f"{600000 + index:06d}"
            session.add(
                Security(
                    symbol=symbol,
                    board="主板",
                    is_st=False,
                    list_status="listed",
                )
            )
            if index < 8:
                _add_bars(
                    session,
                    symbol,
                    dates=[TARGET_DATE],
                    closes=[10.0],
                    amounts=[100.0],
                    volumes=[10.0],
                )

    monkeypatch.setattr(factor_job, "get_session", local_session)
    monkeypatch.setattr(
        factor_job,
        "get_settings",
        lambda: SimpleNamespace(factor_weights_file=str(weights_path)),
    )
    monkeypatch.setattr(
        factor_job,
        "compute_factors_for_date",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("engine must not run below the readiness floor")
        ),
    )

    with pytest.raises(JobExecutionError) as below_floor:
        factor_job.compute_factors(TARGET_DATE)
    assert str(below_floor.value) == "因子输入覆盖率低于 90% 安全阈值。"
    assert below_floor.value.stats["reason"] == "input_coverage_below_floor"
    assert below_floor.value.stats["input_coverage"] == 0.8

    with local_session() as session:
        for index in range(8, 10):
            _add_bars(
                session,
                f"{600000 + index:06d}",
                dates=[TARGET_DATE],
                closes=[10.0],
                amounts=[100.0],
                volumes=[10.0],
            )
        session.add(JobRun(job_name="sync_daily_bars", status="running", stats={}))

    with pytest.raises(JobExecutionError) as running:
        factor_job.compute_factors(TARGET_DATE)
    assert str(running.value) == "日线同步任务仍在运行，因子计算已延后。"
    assert running.value.stats["reason"] == "daily_bars_running"
    assert running.value.stats["input_coverage"] == 1.0


def test_factor_job_cron_is_after_daily_bar_sync() -> None:
    factor_job.register_factor_job()
    try:
        trigger = JOBS["compute_factors"].trigger
        assert str(trigger.fields[5]) == "19"
        assert str(trigger.fields[6]) == "30"
        assert str(trigger.timezone) == "Asia/Shanghai"
    finally:
        JOBS.pop("compute_factors", None)


def test_factor_api_returns_weight_config_and_latest_factor_values(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'factor-api.db'}")
    Base.metadata.create_all(engine)
    weights_path = _weights_file(tmp_path / "weights.yaml")
    with Session(engine) as session:
        session.add(
            CompositeScore(
                symbol="600000",
                trade_date=TARGET_DATE,
                score=88.5,
                win_rate_20d=None,
                factors={"momentum_20d": 1.2},
                model_version="factor-score-v1.0.0",
            )
        )
        session.add_all(
            [
                FactorValue(
                    symbol="600000",
                    trade_date=TARGET_DATE,
                    factor="momentum_20d",
                    raw=0.12,
                    zscore=1.2,
                    model_version="factor-v1.0.0",
                ),
                FactorValue(
                    symbol="600000",
                    trade_date=TARGET_DATE,
                    factor="net_inflow_5d",
                    raw=None,
                    zscore=None,
                    model_version="factor-v1.0.0",
                ),
            ]
        )
        session.commit()

    def override_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[db_session_dependency] = override_session
    app.dependency_overrides[settings_dependency] = lambda: SimpleNamespace(
        factor_weights_file=str(weights_path)
    )
    try:
        with TestClient(app) as client:
            weights_response = client.get("/v1/factors/weights")
            factors_response = client.get("/v1/stocks/SH.600000/factors")
            invalid_response = client.get("/v1/stocks/not-a-stock/factors")
            missing_response = client.get("/v1/stocks/000001/factors")
            app.dependency_overrides[settings_dependency] = lambda: SimpleNamespace(
                factor_weights_file=str(tmp_path / "missing-weights.yaml")
            )
            weights_error_response = client.get("/v1/factors/weights")
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)
        app.dependency_overrides.pop(settings_dependency, None)

    assert weights_response.status_code == 200
    assert weights_response.json()["version"] == "v1.0.0"
    assert weights_response.json()["profile"] == "test"
    assert weights_response.json()["weights"]["pe_percentile"] == pytest.approx(-0.18)

    assert factors_response.status_code == 200
    assert factors_response.json() == {
        "symbol": "600000",
        "trade_date": TARGET_DATE.isoformat(),
        "score": 88.5,
        "win_rate_20d": None,
        "model_version": "factor-score-v1.0.0",
        "factors": {
            "momentum_20d": {"raw": 0.12, "zscore": 1.2},
            "net_inflow_5d": {"raw": None, "zscore": None},
        },
    }
    assert invalid_response.status_code == 422
    assert missing_response.status_code == 404
    assert weights_error_response.status_code == 503
    assert weights_error_response.json()["detail"].startswith(
        "因子权重配置不可用：无法加载因子权重配置："
    )
