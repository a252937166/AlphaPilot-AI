from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from math import isfinite

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.data.provenance import AUDITED_DAILY_BAR_SOURCES
from alphapilot.db.models import AdjFactor, DailyBar, Security
from alphapilot.engines.factors import (
    FACTOR_DECISION_TIME,
    MARKET_TIMEZONE,
    FactorWeightConfig,
    composite,
    compute_factors_for_date,
    zscore_cross_section,
)

logger = logging.getLogger(__name__)

MIN_LISTING_AGE_DAYS = 60


def _parse_listed_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def eligible_universe(session: Session, as_of: date) -> pd.DataFrame:
    """Return the auditable, non-suspended current-survivor universe at ``as_of``."""

    first_bar = (
        select(
            DailyBar.symbol.label("symbol"),
            func.min(DailyBar.trade_date).label("first_observed_date"),
        )
        .where(DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES))
        .group_by(DailyBar.symbol)
        .subquery()
    )
    rows = session.execute(
        select(
            Security.symbol,
            Security.name,
            Security.board,
            Security.is_st,
            Security.snapshot_at,
            Security.listed_date,
            first_bar.c.first_observed_date,
            DailyBar.source,
        )
        .join(
            DailyBar,
            (DailyBar.symbol == Security.symbol)
            & (DailyBar.trade_date == as_of),
        )
        .join(first_bar, first_bar.c.symbol == Security.symbol)
        .where(
            Security.market == "CN",
            Security.list_status == "listed",
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
            DailyBar.close > 0,
            DailyBar.volume > 0,
        )
        .order_by(Security.symbol)
    ).all()
    frame = pd.DataFrame(
        rows,
        columns=[
            "symbol",
            "name",
            "board",
            "is_st",
            "snapshot_at",
            "listed_date",
            "first_observed_date",
            "bar_source",
        ],
    )
    if frame.empty:
        frame["listing_age_basis"] = pd.Series(dtype=str)
        frame["st_status_known"] = pd.Series(dtype=bool)
        frame.attrs = {
            "as_of": as_of.isoformat(),
            "has_survivorship_bias": True,
            "survivorship_bias_warning": "当前证券主表不含历史退市股。",
            "st_history_warning": (
                "历史 ST 状态不可用；仅同日决策时点前的证券快照可标记 is_st。"
            ),
            "min_listing_age_days": MIN_LISTING_AGE_DAYS,
            "st_status_known": 0,
        }
        return frame

    listing_cutoff = as_of - timedelta(days=MIN_LISTING_AGE_DAYS)
    accepted: list[bool] = []
    bases: list[str] = []
    for listed_value, first_observed in zip(
        frame["listed_date"],
        frame["first_observed_date"],
        strict=True,
    ):
        listed = _parse_listed_date(listed_value)
        if listed is not None:
            accepted.append(listed <= listing_cutoff)
            bases.append("security_master")
        elif isinstance(first_observed, date):
            accepted.append(first_observed <= listing_cutoff)
            bases.append("first_audited_bar")
        else:
            accepted.append(False)
            bases.append("unknown")
    frame["listing_age_basis"] = bases
    frame = frame.loc[accepted].reset_index(drop=True)
    frame["symbol"] = frame["symbol"].astype(str)
    decision_cutoff = pd.Timestamp.combine(
        as_of,
        FACTOR_DECISION_TIME,
    ).tz_localize(MARKET_TIMEZONE).tz_convert("UTC")
    snapshot_at = pd.to_datetime(frame["snapshot_at"], errors="coerce", utc=True)
    local_snapshot_date = snapshot_at.dt.tz_convert(MARKET_TIMEZONE).dt.date
    frame["st_status_known"] = local_snapshot_date.eq(as_of) & (
        snapshot_at <= decision_cutoff
    )
    current_st = frame["is_st"].fillna(False).astype(bool)
    frame["is_st"] = current_st.astype("boolean").where(frame["st_status_known"])
    frame.attrs = {
        "as_of": as_of.isoformat(),
        "has_survivorship_bias": True,
        "survivorship_bias_warning": "当前证券主表不含历史退市股。",
        "st_history_warning": (
            "历史 ST 状态不可用；仅同日决策时点前的证券快照可标记 is_st。"
        ),
        "min_listing_age_days": MIN_LISTING_AGE_DAYS,
        "st_status_known": int(frame["st_status_known"].sum()),
    }
    return frame


def _weight_mapping(
    weights: Mapping[str, float] | FactorWeightConfig,
) -> dict[str, float]:
    source = weights.weights if isinstance(weights, FactorWeightConfig) else weights
    resolved: dict[str, float] = {}
    for name, raw_value in source.items():
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError(f"因子权重必须是数值：{name}")
        value = float(raw_value)
        if not isfinite(value):
            raise ValueError(f"因子权重必须是有限数值：{name}")
        resolved[str(name)] = value
    return resolved


def factor_zscores(session: Session, as_of: date) -> pd.DataFrame:
    """Return strict PIT factor z-scores without imputing missing observations."""

    universe = eligible_universe(session, as_of)
    raw = compute_factors_for_date(session, as_of)
    eligible_symbols = set(universe.get("symbol", pd.Series(dtype=str)).astype(str))
    raw = raw.loc[raw.index.astype(str).isin(eligible_symbols)].copy()
    factor_attrs = dict(raw.attrs)
    if raw.empty:
        raw.attrs = {
            "as_of": as_of.isoformat(),
            "has_survivorship_bias": True,
            "survivorship_bias_warning": universe.attrs["survivorship_bias_warning"],
            "st_history_warning": universe.attrs["st_history_warning"],
            "eligible": len(universe),
            "scored": 0,
            "adjustment_factor_missing_rows": int(
                factor_attrs.get("adjustment_factor_missing_rows", 0)
            ),
            "factor_attrs": factor_attrs,
        }
        return raw

    standardized = zscore_cross_section(raw)
    standardized.attrs = {
        "as_of": as_of.isoformat(),
        "has_survivorship_bias": True,
        "survivorship_bias_warning": universe.attrs["survivorship_bias_warning"],
        "st_history_warning": universe.attrs["st_history_warning"],
        "eligible": len(universe),
        "scored": len(standardized),
        "adjustment_factor_missing_rows": int(
            factor_attrs.get("adjustment_factor_missing_rows", 0)
        ),
        "factor_attrs": factor_attrs,
    }
    return standardized


def signal_scores(
    session: Session,
    as_of: date,
    weights: Mapping[str, float] | FactorWeightConfig,
) -> pd.Series:
    """Replay the composite signal using only inputs available by ``as_of``."""

    standardized = factor_zscores(session, as_of)
    if standardized.empty:
        result = pd.Series(dtype=float, name="score")
        result.attrs = dict(standardized.attrs)
        return result

    result = composite(standardized, _weight_mapping(weights))
    composite_attrs = dict(result.attrs)
    result.attrs = {
        **composite_attrs,
        **dict(standardized.attrs),
        "scored": len(result),
    }
    return result


def forward_return(
    session: Session,
    symbols: Sequence[str],
    entry_date: date,
    exit_date: date,
) -> pd.Series:
    """Return endpoint-equivalent cumulative adjusted returns for exact dates."""

    if exit_date < entry_date:
        raise ValueError("exit_date must not be earlier than entry_date")
    requested = list(
        dict.fromkeys(
            str(symbol).strip()
            for symbol in symbols
            if str(symbol).strip()
        )
    )
    if not requested:
        result = pd.Series(dtype=float, name="forward_return")
        result.attrs = {
            "entry_date": entry_date.isoformat(),
            "exit_date": exit_date.isoformat(),
            "degraded": False,
            "missing_adjustment_rows": 0,
            "missing_endpoint_symbols": 0,
        }
        return result

    target_dates = [entry_date] if entry_date == exit_date else [entry_date, exit_date]
    rows = session.execute(
        select(
            DailyBar.symbol,
            DailyBar.trade_date,
            DailyBar.close,
            AdjFactor.adj_factor,
        )
        .outerjoin(
            AdjFactor,
            (AdjFactor.symbol == DailyBar.symbol)
            & (AdjFactor.trade_date == DailyBar.trade_date),
        )
        .where(
            DailyBar.symbol.in_(requested),
            DailyBar.trade_date.in_(target_dates),
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
        )
    ).all()
    frame = pd.DataFrame(
        rows,
        columns=["symbol", "trade_date", "close", "adj_factor"],
    )
    if frame.empty:
        result = pd.Series(float("nan"), index=requested, name="forward_return")
        result.attrs = {
            "entry_date": entry_date.isoformat(),
            "exit_date": exit_date.isoformat(),
            "degraded": False,
            "missing_adjustment_rows": 0,
            "missing_endpoint_symbols": len(requested),
        }
        return result

    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["adj_factor"] = pd.to_numeric(frame["adj_factor"], errors="coerce")
    missing_adjustments = int(frame["adj_factor"].isna().sum())
    if missing_adjustments:
        logger.warning(
            "前向收益有 %s 行缺少复权因子，按 S1 契约以 1.0 降级。",
            missing_adjustments,
        )
    frame["adj_close"] = frame["close"] * frame["adj_factor"].fillna(1.0)
    prices = frame.pivot(
        index="symbol",
        columns="trade_date",
        values="adj_close",
    ).reindex(index=requested)
    if entry_date == exit_date:
        result = prices[entry_date].where(prices[entry_date] > 0)
        result = result.where(result.isna(), 0.0)
    else:
        prices = prices.reindex(columns=[entry_date, exit_date])
        valid = (prices[entry_date] > 0) & (prices[exit_date] > 0)
        result = prices[exit_date].divide(prices[entry_date]).subtract(1.0).where(valid)
    result = result.astype(float).rename("forward_return")
    result.attrs = {
        "entry_date": entry_date.isoformat(),
        "exit_date": exit_date.isoformat(),
        "degraded": missing_adjustments > 0,
        "missing_adjustment_rows": missing_adjustments,
        "missing_endpoint_symbols": int(result.isna().sum()),
    }
    return result
