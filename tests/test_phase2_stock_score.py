from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.db.models import (
    Base,
    CompositeScore,
    DailyBar,
    FactorValue,
    Security,
    StockScore,
)
from alphapilot.engines.factors import FACTOR_SET
from alphapilot.engines.stock_score import (
    DIMENSION_ORDER,
    DIMENSION_WEIGHTS,
    MODEL_VERSION,
    REQUIRED_FACTORS,
    compute_stock_scores,
)
from alphapilot.jobs import factors as factor_job
from alphapilot.main import app

TARGET_DATE = date(2026, 7, 21)
OLDER_DATE = TARGET_DATE - timedelta(days=1)
SYMBOLS = ("600000", "600001", "600002")


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
profile: stock-score-test
weights:
  momentum_20d: 0.50
  pe_percentile: -0.50
""",
        encoding="utf-8",
    )
    return path


def _stock_score(
    symbol: str,
    trade_date: date,
    *,
    tech: float = 5.0,
    capital: float = 5.0,
    fundamental: float = 5.0,
    valuation: float = 5.0,
    sentiment: float = 5.0,
    composite: float = 5.0,
) -> StockScore:
    return StockScore(
        symbol=symbol,
        trade_date=trade_date,
        tech=tech,
        capital=capital,
        fundamental=fundamental,
        valuation=valuation,
        sentiment=sentiment,
        composite=composite,
        model_version=MODEL_VERSION,
    )


def _seed_job_universe(session: Session, trade_date: date) -> None:
    for index, symbol in enumerate(SYMBOLS, start=1):
        session.add(
            Security(
                symbol=symbol,
                market="CN",
                board="主板",
                is_st=False,
                list_status="listed",
            )
        )
        close = 10.0 + index
        session.add(
            DailyBar(
                symbol=symbol,
                trade_date=trade_date,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=100.0,
                amount=close * 100.0,
                source="baostock",
            )
        )


def _job_factor_frame() -> pd.DataFrame:
    data = {factor: [-1.0, 0.0, 1.0] for factor in FACTOR_SET}
    data["pe_percentile"] = [1.0, 0.0, -1.0]
    data["net_inflow_5d"] = [np.nan, np.nan, np.nan]
    frame = pd.DataFrame(data, index=list(SYMBOLS), dtype=float)
    frame.attrs["sector_flow_days"] = 0
    return frame


def _configure_factor_job(
    monkeypatch: pytest.MonkeyPatch,
    local_session: Any,
    weights_path: Path,
) -> None:
    monkeypatch.setattr(factor_job, "get_session", local_session)
    monkeypatch.setattr(
        factor_job,
        "get_settings",
        lambda: SimpleNamespace(factor_weights_file=str(weights_path)),
    )
    monkeypatch.setattr(
        factor_job,
        "compute_factors_for_date",
        lambda *_args, **_kwargs: _job_factor_frame(),
    )


def _score_fingerprint(rows: list[StockScore]) -> dict[str, tuple[float, ...]]:
    return {
        row.symbol: (
            row.tech,
            row.capital,
            row.fundamental,
            row.valuation,
            row.sentiment,
            row.composite,
        )
        for row in rows
    }


def test_engine_maps_mean_before_clip_inverts_valuation_and_uses_fixed_weights() -> None:
    frame = pd.DataFrame(
        {
            "momentum_20d": [4.0, 2.5],
            "momentum_60d": [0.0, 2.5],
            "net_inflow_5d": [-4.0, -2.5],
            "roe": [1.0, 2.5],
            "net_profit_yoy": [-1.0, 2.5],
            "pe_percentile": [3.0, -2.5],
            "turnover_change_5d": [3.0, -2.5],
        },
        index=["A", "B"],
    )

    result = compute_stock_scores(frame)

    assert tuple(DIMENSION_ORDER) == (
        "tech",
        "capital",
        "fundamental",
        "valuation",
        "sentiment",
    )
    assert dict(DIMENSION_WEIGHTS) == {
        "tech": 0.25,
        "capital": 0.20,
        "fundamental": 0.25,
        "valuation": 0.15,
        "sentiment": 0.15,
    }
    # tech proves aggregation precedes clipping: mean(4, 0)=2 -> 9,
    # whereas averaging individually clipped scores would incorrectly yield 7.5.
    assert result.loc["A", list(DIMENSION_ORDER)].tolist() == pytest.approx(
        [9.0, 0.0, 5.0, 0.0, 10.0]
    )
    assert result.loc["A", "composite"] == pytest.approx(5.0)
    # Exact +/-2.5 boundaries map to 10/0, and a cheap negative PE z-score
    # becomes a high valuation score.
    assert result.loc["B", list(DIMENSION_ORDER)].tolist() == pytest.approx(
        [10.0, 0.0, 10.0, 10.0, 0.0]
    )
    assert result.loc["B", "composite"] == pytest.approx(6.5)
    assert set(result["model_version"]) == {MODEL_VERSION}


def test_engine_uses_neutral_fixed_slots_for_missing_nan_and_inf_with_coverage() -> None:
    frame = pd.DataFrame(
        {
            "momentum_20d": [1.0, np.nan],
            "momentum_60d": [np.nan, np.inf],
            "net_inflow_5d": [np.nan, -np.inf],
            "roe": [1.0, np.nan],
            "net_profit_yoy": [np.nan, np.nan],
            # pe_percentile and turnover_change_5d are intentionally absent.
        },
        index=["partial", "empty"],
    )

    result = compute_stock_scores(frame)

    assert result.loc["partial", list(DIMENSION_ORDER)].tolist() == pytest.approx(
        [6.0, 5.0, 6.0, 5.0, 5.0]
    )
    assert result.loc["partial", "composite"] == pytest.approx(5.5)
    assert result.loc["empty", list(DIMENSION_ORDER)].tolist() == pytest.approx(
        [5.0, 5.0, 5.0, 5.0, 5.0]
    )
    assert result.loc["empty", "composite"] == pytest.approx(5.0)
    assert bool(np.isfinite(result[[*DIMENSION_ORDER, "composite"]].to_numpy(dtype=float)).all())
    assert result.attrs["factor_coverage"] == {
        "momentum_20d": {"count": 1, "ratio": 0.5},
        "momentum_60d": {"count": 0, "ratio": 0.0},
        "net_inflow_5d": {"count": 0, "ratio": 0.0},
        "roe": {"count": 1, "ratio": 0.5},
        "net_profit_yoy": {"count": 0, "ratio": 0.0},
        "pe_percentile": {"count": 0, "ratio": 0.0},
        "turnover_change_5d": {"count": 0, "ratio": 0.0},
    }
    assert result.attrs["dimension_complete"] == {
        "tech": 0,
        "capital": 0,
        "fundamental": 0,
        "valuation": 0,
        "sentiment": 0,
    }
    assert result.attrs["full_rows"] == 0
    assert result.attrs["neutral_rows"] == 2


def test_stock_score_unique_symbol_date_is_enforced(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'score-unique.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(_stock_score("600519", TARGET_DATE))
        session.commit()
        session.add(_stock_score("600519", TARGET_DATE, composite=6.0))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


@pytest.mark.parametrize(
    "field",
    ["tech", "capital", "fundamental", "valuation", "sentiment", "composite"],
)
def test_stock_score_range_constraints_are_enforced(field: str, tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / f'score-{field}.db'}")
    Base.metadata.create_all(engine)
    score = _stock_score("600519", TARGET_DATE)
    setattr(score, field, 10.01)
    with Session(engine) as session:
        session.add(score)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_factor_job_atomically_replaces_same_day_scores_and_preserves_other_dates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'score-job.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    weights_path = _weights_file(tmp_path / "weights.yaml")
    with local_session() as session:
        _seed_job_universe(session, TARGET_DATE)
        session.add(_stock_score("legacy", OLDER_DATE, composite=7.0))
    _configure_factor_job(monkeypatch, local_session, weights_path)

    first_stats = factor_job.compute_factors(TARGET_DATE)
    with local_session() as session:
        first_rows = list(
            session.scalars(
                select(StockScore)
                .where(StockScore.trade_date == TARGET_DATE)
                .order_by(StockScore.symbol)
            ).all()
        )
        first_fingerprint = _score_fingerprint(first_rows)
        corrupted = session.scalars(
            select(StockScore).where(
                StockScore.symbol == SYMBOLS[0],
                StockScore.trade_date == TARGET_DATE,
            )
        ).one()
        corrupted.tech = 0.0
        corrupted.composite = 0.0
        session.add(_stock_score("999999", TARGET_DATE, composite=9.0))

    second_stats = factor_job.compute_factors(TARGET_DATE)

    assert first_stats["stock_score_rows"] == len(SYMBOLS)
    assert first_stats["stock_score_model_versions"] == [MODEL_VERSION]
    assert first_stats["stock_score_full_rows"] == 0
    assert first_stats["stock_score_neutral_rows"] == len(SYMBOLS)
    assert first_stats["stock_score_factor_coverage"]["net_inflow_5d"] == {
        "count": 0,
        "ratio": 0.0,
    }
    assert second_stats["stock_score_rows"] == len(SYMBOLS)
    with local_session() as session:
        current_rows = list(
            session.scalars(
                select(StockScore)
                .where(StockScore.trade_date == TARGET_DATE)
                .order_by(StockScore.symbol)
            ).all()
        )
        assert [row.symbol for row in current_rows] == list(SYMBOLS)
        assert _score_fingerprint(current_rows) == pytest.approx(first_fingerprint)
        legacy = session.scalars(
            select(StockScore).where(
                StockScore.symbol == "legacy",
                StockScore.trade_date == OLDER_DATE,
            )
        ).one()
        assert legacy.composite == pytest.approx(7.0)


def test_factor_job_stale_implicit_date_does_not_touch_stock_scores(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'score-stale.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    weights_path = _weights_file(tmp_path / "weights.yaml")
    with local_session() as session:
        _seed_job_universe(session, OLDER_DATE)
        session.add(_stock_score("600000", OLDER_DATE, tech=7.0, composite=6.0))
    _configure_factor_job(monkeypatch, local_session, weights_path)
    monkeypatch.setattr(factor_job, "_market_today", lambda: TARGET_DATE)

    def must_not_run(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("stale implicit date must not reach a scoring engine")

    monkeypatch.setattr(factor_job, "compute_factors_for_date", must_not_run)
    monkeypatch.setattr(factor_job, "compute_stock_scores", must_not_run)

    stats = factor_job.compute_factors()

    assert stats["skipped"] == "stale_daily_bars"
    assert stats["date"] == OLDER_DATE.isoformat()
    assert stats["expected_date"] == TARGET_DATE.isoformat()
    with local_session() as session:
        rows = list(session.scalars(select(StockScore)).all())
        assert len(rows) == 1
        assert rows[0].trade_date == OLDER_DATE
        assert rows[0].tech == pytest.approx(7.0)
        assert rows[0].composite == pytest.approx(6.0)


def test_stock_score_insert_failure_rolls_back_all_three_factor_tables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'score-rollback.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    weights_path = _weights_file(tmp_path / "weights.yaml")
    with local_session() as session:
        _seed_job_universe(session, TARGET_DATE)
        session.add(
            FactorValue(
                symbol="999999",
                trade_date=TARGET_DATE,
                factor="momentum_20d",
                raw=9.0,
                zscore=9.0,
                model_version="old-factor",
            )
        )
        session.add(
            CompositeScore(
                symbol="999999",
                trade_date=TARGET_DATE,
                score=77.0,
                factors={},
                model_version="old-composite",
            )
        )
        session.add(_stock_score("999999", TARGET_DATE, tech=9.0, composite=9.0))
    _configure_factor_job(monkeypatch, local_session, weights_path)
    original_insert_batches = factor_job._insert_batches

    def fail_stock_score_insert(
        session: Session,
        model: type[FactorValue] | type[CompositeScore] | type[StockScore],
        records: list[dict[str, Any]],
    ) -> None:
        if model is StockScore:
            raise RuntimeError("injected stock score insert failure")
        original_insert_batches(session, model, records)

    monkeypatch.setattr(factor_job, "_insert_batches", fail_stock_score_insert)

    with pytest.raises(RuntimeError, match="injected stock score insert failure"):
        factor_job.compute_factors(TARGET_DATE)

    with local_session() as session:
        assert session.scalar(select(func.count()).select_from(FactorValue)) == 1
        assert session.scalar(select(func.count()).select_from(CompositeScore)) == 1
        assert session.scalar(select(func.count()).select_from(StockScore)) == 1
        factor = session.scalars(select(FactorValue)).one()
        composite = session.scalars(select(CompositeScore)).one()
        score = session.scalars(select(StockScore)).one()
        assert (factor.symbol, factor.raw, factor.model_version) == (
            "999999",
            9.0,
            "old-factor",
        )
        assert (composite.symbol, composite.score, composite.model_version) == (
            "999999",
            77.0,
            "old-composite",
        )
        assert (score.symbol, score.tech, score.composite) == ("999999", 9.0, 9.0)


def _seed_api_score(session: Session, trade_date: date) -> None:
    session.add(
        Security(
            symbol="600519",
            name="贵州茅台",
            market="CN",
            board="主板",
            is_st=False,
            list_status="listed",
        )
    )
    session.add(
        CompositeScore(
            symbol="600519",
            trade_date=trade_date,
            score=88.0,
            factors={},
            model_version="factor-score-v1.0.0",
        )
    )
    session.add(
        _stock_score(
            "600519",
            trade_date,
            tech=9.0,
            capital=5.0,
            fundamental=5.0,
            valuation=0.0,
            sentiment=10.0,
            composite=6.0,
        )
    )
    factor_values: dict[str, float | None] = {
        "momentum_20d": 4.0,
        "momentum_60d": 0.0,
        "net_inflow_5d": None,
        "roe": 1.0,
        "net_profit_yoy": -1.0,
        "pe_percentile": 3.0,
        "turnover_change_5d": 3.0,
    }
    for factor in REQUIRED_FACTORS:
        value = factor_values[factor]
        session.add(
            FactorValue(
                symbol="600519",
                trade_date=trade_date,
                factor=factor,
                raw=value,
                zscore=value,
                model_version="factor-v1.0.0",
            )
        )


def test_score_api_and_overview_share_payload_and_degrade_on_unsynced_latest_date(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'score-api.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_api_score(session, OLDER_DATE)
        session.commit()

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[db_session_dependency] = override_session
    try:
        with TestClient(app) as client:
            response = client.get("/v1/stocks/SH.600519/score")
            invalid = client.get("/v1/stocks/not-a-stock/score")
            missing = client.get("/v1/stocks/000001/score")

            assert response.status_code == 200
            payload = response.json()
            assert payload["symbol"] == "600519"
            assert payload["trade_date"] == OLDER_DATE.isoformat()
            assert [payload[key] for key in DIMENSION_ORDER] == pytest.approx(
                [9.0, 5.0, 5.0, 0.0, 10.0]
            )
            assert payload["composite"] == pytest.approx(6.0)
            assert payload["model_version"] == MODEL_VERSION
            assert payload["dimension_weights"] == {
                "tech": 0.25,
                "capital": 0.20,
                "fundamental": 0.25,
                "valuation": 0.15,
                "sentiment": 0.15,
            }
            assert [item["key"] for item in payload["radar"]] == list(DIMENSION_ORDER)
            assert [item["name"] for item in payload["radar"]] == [
                "技术",
                "资金",
                "基本面",
                "估值",
                "情绪",
            ]
            assert [item["value"] for item in payload["radar"]] == pytest.approx(
                [payload[key] for key in DIMENSION_ORDER]
            )
            assert all(item["max"] == 10.0 for item in payload["radar"])
            assert payload["radar"][0] | {"value": 0.0} == {
                "key": "tech",
                "name": "技术",
                "value": 0.0,
                "max": 10.0,
                "available_inputs": 2,
                "required_inputs": 2,
                "degraded": False,
            }
            assert payload["radar"][1] | {"value": 0.0} == {
                "key": "capital",
                "name": "资金",
                "value": 0.0,
                "max": 10.0,
                "available_inputs": 0,
                "required_inputs": 1,
                "degraded": True,
            }
            assert payload["missing_factors"] == ["net_inflow_5d"]
            assert payload["degraded_dimensions"] == ["capital"]
            assert payload["input_coverage"] == pytest.approx(6 / 7)
            assert payload["degraded"] is True
            assert "中性 z=0" in payload["degradation_reason"]
            assert payload["inputs"]["net_inflow_5d"] == {
                "raw": None,
                "zscore": None,
                "available": False,
                "model_version": "factor-v1.0.0",
            }
            assert invalid.status_code == 422
            assert invalid.json()["detail"] == "股票代码必须是 6 位数字。"
            assert missing.status_code == 404
            assert "compute_factors" in missing.json()["detail"]

            overview = client.get(
                "/v1/stocks/600519/overview",
                params={"provider": "mock"},
            )
            assert overview.status_code == 200
            assert overview.json()["score"] == payload
            assert overview.json()["score_error"] is None

            with Session(engine) as session:
                session.add(
                    CompositeScore(
                        symbol="600519",
                        trade_date=TARGET_DATE,
                        score=90.0,
                        factors={},
                        model_version="factor-score-v1.0.0",
                    )
                )
                session.commit()

            stale = client.get("/v1/stocks/600519/score")
            degraded_overview = client.get(
                "/v1/stocks/600519/overview",
                params={"provider": "mock"},
            )

            assert stale.status_code == 404
            assert "最新五维评分" in stale.json()["detail"]
            assert degraded_overview.status_code == 200
            assert degraded_overview.json()["score"] is None
            assert degraded_overview.json()["score_error"] == (
                "暂无最新五维评分，请先运行 compute_factors。"
            )
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)
