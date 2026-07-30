from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Any, Literal

from sqlalchemy import and_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from alphapilot.db.models import CompositeScore, DailyBar, FactorValue, Security, StyleDaily

MODEL_VERSION = "style-v1.0.0"

StyleTag = Literal["growth", "value", "defensive", "balanced"]
STYLE_TAGS: tuple[StyleTag, ...] = ("growth", "value", "defensive", "balanced")

# The security master stores the complete CSRC code-prefixed industry value.  Exact
# matching is intentional: loose substring matching can incorrectly classify adjacent
# CSRC industries as defensive.
DEFENSIVE_INDUSTRIES = frozenset(
    {
        "C13农副食品加工业",
        "C14食品制造业",
        "C15酒、饮料和精制茶制造业",
        "D44电力、热力生产和供应业",
        "D45燃气生产和供应业",
        "D46水的生产和供应业",
        "J66货币金融服务",
    }
)

_REQUIRED_FACTORS = (
    "net_profit_yoy",
    "volatility_20d",
    "pe_percentile",
    "pb_percentile",
)
_ZSCORE_FACTORS = frozenset({"net_profit_yoy", "volatility_20d"})


class StyleAggregationError(ValueError):
    """A style snapshot cannot be computed from the real target-day inputs."""


@dataclass(frozen=True, slots=True)
class FactorObservation:
    """Both representations are retained so classification cannot mix their semantics."""

    raw: float | None
    zscore: float | None


@dataclass(frozen=True, slots=True)
class StyleInputRow:
    """One target-day security and its point-in-time style inputs."""

    symbol: str
    industry_csrc: str | None
    amount: float
    factor_values: dict[str, FactorObservation]


@dataclass(frozen=True, slots=True)
class StyleInputStats:
    """Auditable source and exclusion counts for a daily style snapshot."""

    composite_symbols: int
    eligible_symbols: int
    excluded_symbols: int
    missing_security_symbols: int
    missing_or_nonpositive_amount_symbols: int
    factor_coverage: dict[str, int]


@dataclass(frozen=True, slots=True)
class StyleDailySnapshot:
    """Job-facing classification and turnover aggregation for one real factor date."""

    trade_date: date
    symbol_tags: dict[str, StyleTag]
    amount_totals: dict[StyleTag, float]
    amount_weights: dict[StyleTag, float]
    tag_counts: dict[StyleTag, int]
    input_stats: StyleInputStats
    total_amount: float
    source_fingerprint: str
    model_version: str = MODEL_VERSION


def _finite(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _factor_value(row: StyleInputRow, name: str) -> float | None:
    observation = row.factor_values.get(name)
    if observation is None:
        return None
    value = observation.zscore if name in _ZSCORE_FACTORS else observation.raw
    return _finite(value)


def classify(row: StyleInputRow) -> StyleTag:
    """Classify one security using strict thresholds and deterministic priority.

    Financial growth and volatility use cross-sectional z-scores.  PE and PB
    percentiles deliberately use their raw percentile values.  Missing and
    non-finite values never satisfy a numeric rule.
    """

    net_profit_yoy = _factor_value(row, "net_profit_yoy")
    volatility_20d = _factor_value(row, "volatility_20d")
    pe_percentile = _factor_value(row, "pe_percentile")
    pb_percentile = _factor_value(row, "pb_percentile")

    if row.industry_csrc in DEFENSIVE_INDUSTRIES or (
        volatility_20d is not None and volatility_20d < -0.5
    ):
        return "defensive"
    if (
        pe_percentile is not None
        and pb_percentile is not None
        and pe_percentile < 0.4
        and pb_percentile < 0.4
    ):
        return "value"
    if (
        net_profit_yoy is not None
        and pe_percentile is not None
        and net_profit_yoy > 0.5
        and pe_percentile > 0.5
    ):
        return "growth"
    return "balanced"


def _empty_amounts() -> dict[StyleTag, float]:
    return {tag: 0.0 for tag in STYLE_TAGS}


def _empty_counts() -> dict[StyleTag, int]:
    return {tag: 0 for tag in STYLE_TAGS}


def _style_base_statement(trade_date: date) -> Select[Any]:
    return (
        select(
            CompositeScore.symbol,
            CompositeScore.model_version,
            Security.symbol.label("security_symbol"),
            Security.industry_csrc,
            DailyBar.symbol.label("bar_symbol"),
            DailyBar.amount,
        )
        .outerjoin(Security, Security.symbol == CompositeScore.symbol)
        .outerjoin(
            DailyBar,
            and_(
                DailyBar.symbol == CompositeScore.symbol,
                DailyBar.trade_date == trade_date,
            ),
        )
        .where(CompositeScore.trade_date == trade_date)
        .order_by(CompositeScore.symbol)
    )


def _style_factor_statement(trade_date: date) -> Select[Any]:
    return (
        select(
            FactorValue.symbol,
            FactorValue.factor,
            FactorValue.raw,
            FactorValue.zscore,
            FactorValue.model_version,
        )
        .join(
            CompositeScore,
            and_(
                CompositeScore.symbol == FactorValue.symbol,
                CompositeScore.trade_date == FactorValue.trade_date,
            ),
        )
        .where(
            FactorValue.trade_date == trade_date,
            FactorValue.factor.in_(_REQUIRED_FACTORS),
        )
        .order_by(FactorValue.symbol, FactorValue.factor)
    )


def _style_source_fingerprint_from_rows(
    trade_date: date,
    base_rows: Sequence[Any],
    factor_rows: Sequence[Any],
) -> str:
    digest = hashlib.sha256()
    digest.update(f"{MODEL_VERSION}|{trade_date.isoformat()}\n".encode())
    for base_row in base_rows:
        payload = [
            str(base_row.symbol),
            str(base_row.model_version),
            str(base_row.security_symbol) if base_row.security_symbol is not None else None,
            str(base_row.industry_csrc) if base_row.industry_csrc is not None else None,
            str(base_row.bar_symbol) if base_row.bar_symbol is not None else None,
            _finite(base_row.amount),
        ]
        digest.update(
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        )

    for factor_row in factor_rows:
        payload = [
            str(factor_row.symbol),
            str(factor_row.factor),
            _finite(factor_row.raw),
            _finite(factor_row.zscore),
            str(factor_row.model_version),
        ]
        digest.update(
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        )
    return digest.hexdigest()


def style_source_fingerprint(session: Session, trade_date: date) -> str:
    """Hash every target-day input that can affect tags or turnover weights.

    The hash is deliberately per-symbol rather than aggregate-only: swapping two
    securities' factors or amounts must invalidate persisted tags even when counts
    and market totals remain unchanged.
    """

    base_rows = session.execute(_style_base_statement(trade_date)).all()
    factor_rows = session.execute(_style_factor_statement(trade_date)).all()
    return _style_source_fingerprint_from_rows(trade_date, base_rows, factor_rows)


def compute_style_snapshot(session: Session, trade_date: date) -> StyleDailySnapshot:
    """Build a style snapshot from the target day's composite-score universe only.

    Securities without a master row or a finite, positive target-day amount are
    excluded and reported.  Missing factors remain visible through coverage counts
    and naturally classify as balanced; no history or factor value is synthesized.
    """

    base_rows = session.execute(_style_base_statement(trade_date)).all()
    composite_symbols = len(base_rows)
    if composite_symbols == 0:
        raise StyleAggregationError(
            f"目标交易日 {trade_date.isoformat()} 没有综合评分；请先运行 compute_factors。"
        )

    input_rows: dict[str, StyleInputRow] = {}
    missing_security_symbols = 0
    missing_or_nonpositive_amount_symbols = 0
    for row in base_rows:
        if row.security_symbol is None:
            missing_security_symbols += 1
            continue
        amount = _finite(row.amount)
        if row.bar_symbol is None or amount is None or amount <= 0.0:
            missing_or_nonpositive_amount_symbols += 1
            continue
        symbol = str(row.symbol)
        input_rows[symbol] = StyleInputRow(
            symbol=symbol,
            industry_csrc=(str(row.industry_csrc) if row.industry_csrc is not None else None),
            amount=amount,
            factor_values={},
        )

    if not input_rows:
        raise StyleAggregationError(
            f"目标交易日 {trade_date.isoformat()} 没有可用于风格聚合的正成交额证券；"
            "请检查 daily_bars，并先运行 sync_daily_bars 与 compute_factors。"
        )

    factor_rows = session.execute(_style_factor_statement(trade_date)).all()
    for factor_row in factor_rows:
        target = input_rows.get(str(factor_row.symbol))
        if target is None:
            continue
        target.factor_values[str(factor_row.factor)] = FactorObservation(
            raw=_finite(factor_row.raw),
            zscore=_finite(factor_row.zscore),
        )

    factor_coverage = {
        factor: sum(_factor_value(row, factor) is not None for row in input_rows.values())
        for factor in _REQUIRED_FACTORS
    }
    symbol_tags: dict[str, StyleTag] = {}
    amount_totals = _empty_amounts()
    tag_counts = _empty_counts()
    for symbol, input_row in input_rows.items():
        tag = classify(input_row)
        symbol_tags[symbol] = tag
        amount_totals[tag] += input_row.amount
        tag_counts[tag] += 1

    total_amount = sum(amount_totals.values())
    if not isfinite(total_amount) or total_amount <= 0.0:
        raise StyleAggregationError(
            f"目标交易日 {trade_date.isoformat()} 的可用成交额合计非正；"
            "请检查 daily_bars.amount 数据质量。"
        )
    amount_weights = {tag: amount_totals[tag] / total_amount for tag in STYLE_TAGS}
    eligible_symbols = len(input_rows)
    return StyleDailySnapshot(
        trade_date=trade_date,
        symbol_tags=symbol_tags,
        amount_totals=amount_totals,
        amount_weights=amount_weights,
        tag_counts=tag_counts,
        input_stats=StyleInputStats(
            composite_symbols=composite_symbols,
            eligible_symbols=eligible_symbols,
            excluded_symbols=composite_symbols - eligible_symbols,
            missing_security_symbols=missing_security_symbols,
            missing_or_nonpositive_amount_symbols=missing_or_nonpositive_amount_symbols,
            factor_coverage=factor_coverage,
        ),
        total_amount=total_amount,
        source_fingerprint=_style_source_fingerprint_from_rows(
            trade_date,
            base_rows,
            factor_rows,
        ),
    )


def aggregate_daily(session: Session, trade_date: date) -> StyleDaily:
    """Return the ORM aggregate for the target date without persisting it."""

    snapshot = compute_style_snapshot(session, trade_date)
    return StyleDaily(
        trade_date=trade_date,
        growth_pct=snapshot.amount_weights["growth"],
        value_pct=snapshot.amount_weights["value"],
        defensive_pct=snapshot.amount_weights["defensive"],
        balanced_pct=snapshot.amount_weights["balanced"],
        model_version=snapshot.model_version,
    )
