from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from math import nan

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alphapilot.db.models import Base, CompositeScore, DailyBar, FactorValue, Security
from alphapilot.engines.style import (
    DEFENSIVE_INDUSTRIES,
    STYLE_TAGS,
    FactorObservation,
    StyleAggregationError,
    StyleInputRow,
    aggregate_daily,
    classify,
    compute_style_snapshot,
    style_source_fingerprint,
)

TARGET_DATE = date(2026, 7, 21)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session
    engine.dispose()


def _observation(*, raw: float | None = None, zscore: float | None = None) -> FactorObservation:
    return FactorObservation(raw=raw, zscore=zscore)


def _input(
    *,
    industry: str | None = None,
    net_profit_yoy: FactorObservation | None = None,
    volatility_20d: FactorObservation | None = None,
    pe_percentile: FactorObservation | None = None,
    pb_percentile: FactorObservation | None = None,
) -> StyleInputRow:
    observations = {
        name: value
        for name, value in {
            "net_profit_yoy": net_profit_yoy,
            "volatility_20d": volatility_20d,
            "pe_percentile": pe_percentile,
            "pb_percentile": pb_percentile,
        }.items()
        if value is not None
    }
    return StyleInputRow(
        symbol="600000",
        industry_csrc=industry,
        amount=1.0,
        factor_values=observations,
    )


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            _input(
                net_profit_yoy=_observation(zscore=0.500001),
                pe_percentile=_observation(raw=0.500001),
            ),
            "growth",
        ),
        (
            _input(
                pe_percentile=_observation(raw=0.399999),
                pb_percentile=_observation(raw=0.399999),
            ),
            "value",
        ),
        (_input(volatility_20d=_observation(zscore=-0.500001)), "defensive"),
        (_input(), "balanced"),
    ],
)
def test_classify_rules(row: StyleInputRow, expected: str) -> None:
    assert classify(row) == expected


@pytest.mark.parametrize(
    "row",
    [
        _input(
            net_profit_yoy=_observation(zscore=0.5),
            pe_percentile=_observation(raw=0.9),
        ),
        _input(
            net_profit_yoy=_observation(zscore=0.9),
            pe_percentile=_observation(raw=0.5),
        ),
        _input(
            pe_percentile=_observation(raw=0.4),
            pb_percentile=_observation(raw=0.1),
        ),
        _input(
            pe_percentile=_observation(raw=0.1),
            pb_percentile=_observation(raw=0.4),
        ),
        _input(volatility_20d=_observation(zscore=-0.5)),
    ],
)
def test_classify_uses_strict_thresholds(row: StyleInputRow) -> None:
    assert classify(row) == "balanced"


def test_classify_uses_zscores_for_growth_and_volatility_but_raw_for_valuation() -> None:
    growth = _input(
        net_profit_yoy=_observation(raw=-99.0, zscore=0.7),
        volatility_20d=_observation(raw=-99.0, zscore=0.0),
        pe_percentile=_observation(raw=0.7, zscore=0.1),
        pb_percentile=_observation(raw=0.8, zscore=0.1),
    )
    value = _input(
        net_profit_yoy=_observation(raw=99.0, zscore=0.0),
        pe_percentile=_observation(raw=0.2, zscore=2.0),
        pb_percentile=_observation(raw=0.3, zscore=2.0),
    )

    assert classify(growth) == "growth"
    assert classify(value) == "value"


def test_none_and_nan_values_do_not_match_numeric_rules() -> None:
    row = _input(
        net_profit_yoy=_observation(raw=10.0, zscore=nan),
        volatility_20d=_observation(raw=-10.0, zscore=nan),
        pe_percentile=_observation(raw=nan, zscore=-10.0),
        pb_percentile=_observation(raw=None, zscore=-10.0),
    )

    assert classify(row) == "balanced"


@pytest.mark.parametrize("industry", sorted(DEFENSIVE_INDUSTRIES))
def test_real_code_prefixed_defensive_industries_match_exactly(industry: str) -> None:
    assert classify(_input(industry=industry)) == "defensive"


@pytest.mark.parametrize(
    "industry",
    [
        "电力、热力生产和供应业",
        "D44电力、热力生产和供应业附属",
        "J67资本市场服务",
    ],
)
def test_defensive_industries_do_not_use_substring_matching(industry: str) -> None:
    assert classify(_input(industry=industry)) == "balanced"


def test_defensive_priority_wins_over_value_and_growth_inputs() -> None:
    industry_value = _input(
        industry="C15酒、饮料和精制茶制造业",
        pe_percentile=_observation(raw=0.2),
        pb_percentile=_observation(raw=0.2),
    )
    volatility_growth = _input(
        net_profit_yoy=_observation(zscore=1.0),
        volatility_20d=_observation(zscore=-1.0),
        pe_percentile=_observation(raw=0.8),
    )

    assert classify(industry_value) == "defensive"
    assert classify(volatility_growth) == "defensive"


def _add_target_row(
    session: Session,
    symbol: str,
    *,
    amount: float | None,
    industry: str | None = None,
    factors: dict[str, tuple[float | None, float | None]] | None = None,
    with_composite: bool = True,
    with_bar: bool = True,
) -> None:
    session.add(
        Security(
            symbol=symbol,
            market="CN",
            industry_csrc=industry,
            list_status="listed",
            is_st=False,
        )
    )
    if with_composite:
        session.add(
            CompositeScore(
                symbol=symbol,
                trade_date=TARGET_DATE,
                score=50.0,
                factors={},
                model_version="factor-v1.0.0",
            )
        )
    if with_bar:
        session.add(
            DailyBar(
                symbol=symbol,
                trade_date=TARGET_DATE,
                open=10.0,
                high=10.0,
                low=10.0,
                close=10.0,
                volume=100.0,
                amount=amount,
                source="test",
            )
        )
    for factor, (raw, zscore) in (factors or {}).items():
        session.add(
            FactorValue(
                symbol=symbol,
                trade_date=TARGET_DATE,
                factor=factor,
                raw=raw,
                zscore=zscore,
                model_version="factor-v1.0.0",
            )
        )


def _neutral_factors() -> dict[str, tuple[float | None, float | None]]:
    return {
        "net_profit_yoy": (0.0, 0.0),
        "volatility_20d": (0.0, 0.0),
        "pe_percentile": (0.6, -10.0),
        "pb_percentile": (0.6, -10.0),
    }


def test_snapshot_uses_composite_universe_and_positive_amount_weights(session: Session) -> None:
    growth_factors = _neutral_factors() | {"net_profit_yoy": (0.0, 0.8)}
    value_factors = _neutral_factors() | {
        "pe_percentile": (0.2, 10.0),
        "pb_percentile": (0.3, 10.0),
    }
    defensive_factors = _neutral_factors() | {"volatility_20d": (99.0, -0.8)}
    _add_target_row(session, "000001", amount=100.0, factors=growth_factors)
    _add_target_row(session, "000002", amount=200.0, factors=value_factors)
    _add_target_row(session, "000003", amount=300.0, factors=defensive_factors)
    _add_target_row(session, "000004", amount=400.0, factors=_neutral_factors())
    _add_target_row(session, "000005", amount=0.0, factors=growth_factors)
    _add_target_row(session, "000006", amount=-100.0, factors=value_factors)
    _add_target_row(
        session,
        "000007",
        amount=None,
        factors=defensive_factors,
        with_bar=False,
    )
    # A liquid security with factors is not part of the mother set without CompositeScore.
    _add_target_row(
        session,
        "000008",
        amount=1_000_000.0,
        factors=defensive_factors,
        with_composite=False,
    )
    session.flush()

    snapshot = compute_style_snapshot(session, TARGET_DATE)
    aggregate = aggregate_daily(session, TARGET_DATE)

    assert snapshot.symbol_tags == {
        "000001": "growth",
        "000002": "value",
        "000003": "defensive",
        "000004": "balanced",
    }
    assert snapshot.amount_totals == {
        "growth": 100.0,
        "value": 200.0,
        "defensive": 300.0,
        "balanced": 400.0,
    }
    assert snapshot.amount_weights == pytest.approx(
        {"growth": 0.1, "value": 0.2, "defensive": 0.3, "balanced": 0.4}
    )
    assert snapshot.tag_counts == {
        "growth": 1,
        "value": 1,
        "defensive": 1,
        "balanced": 1,
    }
    assert set(snapshot.amount_weights) == set(STYLE_TAGS)
    assert sum(snapshot.amount_weights.values()) == pytest.approx(1.0)
    assert snapshot.total_amount == 1_000.0
    assert snapshot.input_stats.composite_symbols == 7
    assert snapshot.input_stats.eligible_symbols == 4
    assert snapshot.input_stats.excluded_symbols == 3
    assert snapshot.input_stats.missing_or_nonpositive_amount_symbols == 3
    assert snapshot.input_stats.factor_coverage == {
        "net_profit_yoy": 4,
        "volatility_20d": 4,
        "pe_percentile": 4,
        "pb_percentile": 4,
    }
    assert (
        aggregate.growth_pct,
        aggregate.value_pct,
        aggregate.defensive_pct,
        aggregate.balanced_pct,
    ) == pytest.approx((0.1, 0.2, 0.3, 0.4))


def test_missing_factors_are_balanced_and_report_zero_coverage(session: Session) -> None:
    _add_target_row(session, "600000", amount=50.0)
    session.flush()

    snapshot = compute_style_snapshot(session, TARGET_DATE)

    assert snapshot.symbol_tags == {"600000": "balanced"}
    assert snapshot.amount_weights["balanced"] == 1.0
    assert snapshot.input_stats.factor_coverage == dict.fromkeys(
        (
            "net_profit_yoy",
            "volatility_20d",
            "pe_percentile",
            "pb_percentile",
        ),
        0,
    )


def test_source_fingerprint_detects_per_symbol_factor_swaps_with_same_totals(
    session: Session,
) -> None:
    factors_a = _neutral_factors() | {"pe_percentile": (0.2, 10.0)}
    factors_b = _neutral_factors() | {"pe_percentile": (0.8, -10.0)}
    _add_target_row(session, "600001", amount=100.0, factors=factors_a)
    _add_target_row(session, "600002", amount=100.0, factors=factors_b)
    session.flush()
    before = style_source_fingerprint(session, TARGET_DATE)

    first = (
        session.query(FactorValue)
        .filter_by(symbol="600001", trade_date=TARGET_DATE, factor="pe_percentile")
        .one()
    )
    second = (
        session.query(FactorValue)
        .filter_by(symbol="600002", trade_date=TARGET_DATE, factor="pe_percentile")
        .one()
    )
    first.raw, second.raw = second.raw, first.raw
    session.flush()

    assert style_source_fingerprint(session, TARGET_DATE) != before


@pytest.mark.parametrize("amount", [0.0, -1.0, None])
def test_no_positive_amount_raises_actionable_chinese_error(
    session: Session, amount: float | None
) -> None:
    _add_target_row(session, "600000", amount=amount)
    session.flush()

    with pytest.raises(StyleAggregationError, match=r"正成交额证券.*daily_bars"):
        compute_style_snapshot(session, TARGET_DATE)


def test_missing_composite_snapshot_does_not_backfill_history(session: Session) -> None:
    _add_target_row(session, "600000", amount=50.0, with_composite=False)
    session.flush()

    with pytest.raises(StyleAggregationError, match=r"没有综合评分.*compute_factors"):
        compute_style_snapshot(session, TARGET_DATE)
