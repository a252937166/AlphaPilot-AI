from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from math import isfinite
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from alphapilot.data.provenance import (
    AUDITED_DAILY_BAR_SOURCES,
    AUDITED_SECTOR_FLOW_SOURCES,
)
from alphapilot.db.models import (
    AdjFactor,
    DailyBar,
    FinancialIndicator,
    SectorConstituent,
    SectorConstituentSnapshot,
    SectorFlowDaily,
    SectorSnapshot,
    Security,
    ValuationDaily,
)

logger = logging.getLogger(__name__)

FACTOR_SET = [
    "momentum_20d",
    "momentum_60d",
    "volatility_20d",
    "turnover_change_5d",
    "net_inflow_5d",
    "roe",
    "net_profit_yoy",
    "ocf_to_profit",
    "debt_ratio",
    "revenue_yoy",
    "pe_percentile",
    "pb_percentile",
    "sector_strength",
]
FINANCIAL_FACTORS = frozenset(
    {"roe", "net_profit_yoy", "ocf_to_profit", "debt_ratio", "revenue_yoy"}
)
FINANCIAL_SYMBOL_FILTER_LIMIT = 800
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
FACTOR_DECISION_TIME = time(19, 30)
PRICE_SESSION_COUNT = 90
_DEFAULT_WEIGHTS_FILE = Path(__file__).resolve().parents[3] / "config" / "factor_weights.yaml"


@dataclass(frozen=True, slots=True)
class FactorWeightConfig:
    """Validated, immutable factor-weight configuration."""

    version: str
    profile: str
    weights: dict[str, float]


def load_weights(path: str | Path | None = None) -> FactorWeightConfig:
    """Safely load a factor-weight YAML file and reject ineffective settings."""

    config_path = Path(path) if path is not None else _DEFAULT_WEIGHTS_FILE
    try:
        with config_path.open(encoding="utf-8") as stream:
            loaded: object = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"无法加载因子权重配置：{config_path}") from exc
    if not isinstance(loaded, Mapping):
        raise ValueError("因子权重配置必须是 YAML 映射。")

    version = loaded.get("version")
    profile = loaded.get("profile")
    raw_weights = loaded.get("weights")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("因子权重配置缺少有效的 version。")
    if not isinstance(profile, str) or not profile.strip():
        raise ValueError("因子权重配置缺少有效的 profile。")
    if not isinstance(raw_weights, Mapping) or not raw_weights:
        raise ValueError("因子权重配置缺少有效的 weights 映射。")

    weights: dict[str, float] = {}
    for raw_name, raw_value in raw_weights.items():
        if not isinstance(raw_name, str) or raw_name not in FACTOR_SET:
            raise ValueError(f"因子权重配置包含未知因子：{raw_name!r}")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError(f"因子权重必须是数值：{raw_name}")
        value = float(raw_value)
        if not isfinite(value):
            raise ValueError(f"因子权重必须是有限数值：{raw_name}")
        weights[raw_name] = value
    if not any(value != 0.0 for value in weights.values()):
        raise ValueError("因子权重不能全部为零。")
    return FactorWeightConfig(
        version=version.strip(),
        profile=profile.strip(),
        weights=weights,
    )


def _finite(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _decision_window(trade_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(trade_date, time.min, tzinfo=MARKET_TIMEZONE).astimezone(UTC)
    cutoff = datetime.combine(
        trade_date,
        FACTOR_DECISION_TIME,
        tzinfo=MARKET_TIMEZONE,
    ).astimezone(UTC)
    return start, cutoff


def _symbol_digits(value: object) -> str | None:
    raw = str(value).strip()
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    digits = "".join(character for character in raw if character.isdigit())
    return digits if len(digits) == 6 else None


def _trading_dates(session: Session, trade_date: date) -> list[date]:
    dates = list(
        session.scalars(
            select(DailyBar.trade_date)
            .where(
                DailyBar.symbol == "SH.000001",
                DailyBar.trade_date <= trade_date,
                DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
            )
            .order_by(DailyBar.trade_date.desc())
            .limit(PRICE_SESSION_COUNT)
        )
    )
    if trade_date not in dates or len(dates) < PRICE_SESSION_COUNT:
        fallback_dates = list(
            session.scalars(
                select(DailyBar.trade_date)
                .join(Security, Security.symbol == DailyBar.symbol)
                .where(
                    DailyBar.trade_date <= trade_date,
                    DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
                    Security.market == "CN",
                    Security.list_status == "listed",
                )
                .distinct()
                .order_by(DailyBar.trade_date.desc())
                .limit(PRICE_SESSION_COUNT)
            )
        )
        benchmark_stale = trade_date not in dates and trade_date in fallback_dates
        if benchmark_stale or len(fallback_dates) > len(dates):
            dates = fallback_dates
    return sorted(item for item in dates if isinstance(item, date))


def _eligible_securities(
    session: Session,
    trade_date: date,
    day_start: datetime,
    decision_cutoff: datetime,
) -> tuple[pd.DataFrame, int]:
    rows = session.execute(
        select(
            Security.symbol,
            Security.snapshot_at,
            Security.is_st,
        )
        .join(
            DailyBar,
            (DailyBar.symbol == Security.symbol) & (DailyBar.trade_date == trade_date),
        )
        .where(
            Security.market == "CN",
            Security.list_status == "listed",
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
            DailyBar.close > 0,
            DailyBar.volume > 0,
            DailyBar.amount > 0,
        )
        .order_by(Security.symbol)
    ).all()
    universe = int(
        session.scalar(
            select(func.count())
            .select_from(Security)
            .where(
                Security.market == "CN",
                Security.list_status == "listed",
            )
        )
        or 0
    )
    frame = pd.DataFrame(
        rows,
        columns=["symbol", "snapshot_at", "is_st"],
    )
    if not frame.empty:
        frame["symbol"] = frame["symbol"].astype(str)
        frame["st_status_known"] = frame["snapshot_at"].map(
            lambda value: (
                isinstance(value, datetime)
                and day_start <= _utc(value) <= decision_cutoff
            )
        )
        known_st = frame["st_status_known"] & frame["is_st"].fillna(False).astype(bool)
        frame = frame.loc[~known_st].copy()
    return frame, universe


def _load_bar_factors(
    session: Session,
    symbols: set[str],
    trading_dates: list[date],
) -> tuple[pd.DataFrame, int, int]:
    result = pd.DataFrame(
        index=pd.Index(sorted(symbols), name="symbol"),
        columns=[
            "momentum_20d",
            "momentum_60d",
            "volatility_20d",
            "turnover_change_5d",
        ],
        dtype=float,
    )
    if not symbols or not trading_dates:
        return result, 0, 0

    statement = select(
        DailyBar.symbol,
        DailyBar.trade_date,
        DailyBar.close,
        DailyBar.volume,
        DailyBar.amount,
        AdjFactor.adj_factor,
    ).outerjoin(
        AdjFactor,
        (AdjFactor.symbol == DailyBar.symbol)
        & (AdjFactor.trade_date == DailyBar.trade_date),
    ).where(
        DailyBar.trade_date.in_(trading_dates),
        DailyBar.symbol.in_(symbols),
        DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
    )
    bars = pd.read_sql_query(statement, session.connection())
    if bars.empty:
        return result, 0, 0
    bars["symbol"] = bars["symbol"].astype(str)
    bars["trade_date"] = pd.to_datetime(bars["trade_date"], errors="coerce").dt.date
    bars = bars.dropna(subset=["trade_date"])
    for column in ("close", "volume", "amount", "adj_factor"):
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    missing_adjustments = int(bars["adj_factor"].isna().sum())
    if missing_adjustments:
        logger.warning(
            "因子计算有 %s 行审计日线缺少复权因子，按 S1 契约以 1.0 降级。",
            missing_adjustments,
        )
    bars["adjusted_close"] = bars["close"] * bars["adj_factor"].fillna(1.0)

    calendar = pd.Index(trading_dates, name="trade_date")
    close_raw = bars.pivot(
        index="trade_date",
        columns="symbol",
        values="adjusted_close",
    ).reindex(calendar)
    close = close_raw.ffill()
    volume = (
        bars.pivot(index="trade_date", columns="symbol", values="volume")
        .reindex(calendar)
        .fillna(0.0)
    )
    amount = (
        bars.pivot(index="trade_date", columns="symbol", values="amount")
        .reindex(calendar)
        .fillna(0.0)
    )

    if len(calendar) >= 21:
        base_20 = close.iloc[-21]
        momentum_20 = close.iloc[-1].divide(base_20).subtract(1.0)
        result.loc[momentum_20.index, "momentum_20d"] = momentum_20
        returns_20 = close.pct_change(fill_method=None).tail(20)
        volatility = (returns_20.std(axis=0, ddof=0) * np.sqrt(252)).where(returns_20.count() == 20)
        result.loc[volatility.index, "volatility_20d"] = volatility
    if len(calendar) >= 61:
        base_60 = close.iloc[-61]
        momentum_60 = close.iloc[-1].divide(base_60).subtract(1.0)
        result.loc[momentum_60.index, "momentum_60d"] = momentum_60
    if len(calendar) >= 10:
        listed_for_window = close.iloc[-10].notna()
        current_amount = amount.iloc[-5:].mean(axis=0)
        previous_amount = amount.iloc[-10:-5].mean(axis=0)
        amount_change = current_amount.divide(previous_amount).subtract(1.0)
        amount_change = amount_change.where((previous_amount > 0) & listed_for_window)
        current_volume = volume.iloc[-5:].mean(axis=0)
        previous_volume = volume.iloc[-10:-5].mean(axis=0)
        volume_change = current_volume.divide(previous_volume).subtract(1.0)
        volume_change = volume_change.where((previous_volume > 0) & listed_for_window)
        turnover_change = amount_change.where(amount_change.notna(), volume_change)
        result.loc[turnover_change.index, "turnover_change_5d"] = turnover_change

    result = result.replace([np.inf, -np.inf], np.nan)
    return result, len(bars), missing_adjustments


def _load_financial_factors(
    session: Session,
    symbols: set[str],
    cutoff: datetime,
) -> tuple[dict[str, dict[str, float]], int]:
    if not symbols:
        return {}, 0

    latest_period_query = select(
        FinancialIndicator.symbol.label("symbol"),
        FinancialIndicator.metric.label("metric"),
        func.max(FinancialIndicator.report_period).label("report_period"),
    ).where(
        FinancialIndicator.metric.in_(FINANCIAL_FACTORS),
        FinancialIndicator.value.is_not(None),
        FinancialIndicator.available_time <= cutoff,
    )
    if len(symbols) <= FINANCIAL_SYMBOL_FILTER_LIMIT:
        latest_period_query = latest_period_query.where(
            FinancialIndicator.symbol.in_(sorted(symbols))
        )
    latest_periods = (
        latest_period_query.group_by(
            FinancialIndicator.symbol,
            FinancialIndicator.metric,
        )
        .correlate(None)
        .subquery("latest_financial_periods")
    )

    rows = session.execute(
        select(
            FinancialIndicator.symbol,
            FinancialIndicator.metric,
            FinancialIndicator.value,
            FinancialIndicator.available_time,
            FinancialIndicator.report_period,
            FinancialIndicator.id,
        )
        .join(
            latest_periods,
            and_(
                FinancialIndicator.symbol == latest_periods.c.symbol,
                FinancialIndicator.metric == latest_periods.c.metric,
                FinancialIndicator.report_period == latest_periods.c.report_period,
            ),
        )
        .where(
            FinancialIndicator.value.is_not(None),
            FinancialIndicator.available_time <= cutoff,
        )
    ).all()
    latest: dict[str, dict[str, float]] = {}
    selected = 0
    for symbol_value, metric_value, value, available_time, _period, _row_id in rows:
        symbol = str(symbol_value)
        metric = str(metric_value)
        if symbol not in symbols or metric in latest.get(symbol, {}):
            continue
        if not isinstance(available_time, datetime) or _utc(available_time) > cutoff:
            continue
        number = _finite(value)
        if number is None:
            continue
        latest.setdefault(symbol, {})[metric] = number
        selected += 1
    return latest, selected


def _load_valuation_factors(
    session: Session,
    symbols: set[str],
    trade_date: date,
    cutoff: datetime,
) -> tuple[dict[str, dict[str, float | None]], int]:
    """Load one decision day's PIT valuation cross-section without a history scan."""

    rows = session.execute(
        select(
            ValuationDaily.symbol,
            ValuationDaily.pe_ttm,
            ValuationDaily.pb_mrq,
            ValuationDaily.available_time,
        ).where(
            ValuationDaily.trade_date == trade_date,
            ValuationDaily.available_time <= cutoff,
        )
    ).all()
    latest: dict[str, dict[str, float | None]] = {}
    for symbol_value, pe_ttm, pb_mrq, available_time in rows:
        symbol = str(symbol_value)
        if symbol not in symbols:
            continue
        if not isinstance(available_time, datetime) or _utc(available_time) > cutoff:
            continue
        latest[symbol] = {
            "pe_ttm": _finite(pe_ttm),
            "pb_mrq": _finite(pb_mrq),
        }
    return latest, len(latest)


def _current_sector_memberships(
    session: Session,
    cutoff: datetime,
) -> dict[str, list[str]]:
    memberships: dict[str, list[str]] = {}
    rows = session.execute(
        select(
            SectorConstituent.symbol,
            SectorConstituent.plate_code,
        ).where(SectorConstituent.refreshed_at <= cutoff)
    ).all()
    for raw_symbol, raw_plate_code in rows:
        symbol = _symbol_digits(raw_symbol)
        plate_code = str(raw_plate_code).strip()
        if symbol is None or not plate_code:
            continue
        memberships.setdefault(symbol, []).append(plate_code)
    return memberships


def _pit_sector_memberships(
    session: Session,
    trade_date: date,
    cutoff: datetime,
) -> dict[str, list[str]]:
    """Load only the immutable membership snapshot visible at this decision."""

    memberships: dict[str, list[str]] = {}
    rows = session.execute(
        select(
            SectorConstituentSnapshot.symbol,
            SectorConstituentSnapshot.plate_code,
            SectorConstituentSnapshot.available_time,
        ).where(
            SectorConstituentSnapshot.as_of_date == trade_date,
            SectorConstituentSnapshot.available_time <= cutoff,
        )
    ).all()
    for raw_symbol, raw_plate_code, available_time in rows:
        if not isinstance(available_time, datetime) or _utc(available_time) > cutoff:
            continue
        symbol = _symbol_digits(raw_symbol)
        plate_code = str(raw_plate_code).strip()
        if symbol is None or not plate_code:
            continue
        memberships.setdefault(symbol, []).append(plate_code)
    return memberships


def _sector_flow_values(
    session: Session,
    required_dates: list[date],
) -> tuple[dict[str, float], int, list[str]]:
    if len(required_dates) != 5:
        return {}, 0, []
    rows = session.execute(
        select(
            SectorFlowDaily.plate_code,
            SectorFlowDaily.trade_date,
            SectorFlowDaily.net_inflow,
            SectorFlowDaily.source,
        ).where(
            SectorFlowDaily.trade_date.in_(required_dates),
            SectorFlowDaily.source.in_(AUDITED_SECTOR_FLOW_SOURCES),
        )
    ).all()
    if not rows:
        return {}, 0, []

    by_plate: dict[str, list[tuple[date, float, str]]] = {}
    observed_days: set[date] = set()
    for plate_code_value, flow_date, raw_inflow, source_value in rows:
        inflow = _finite(raw_inflow)
        source = str(source_value).strip()
        if not isinstance(flow_date, date) or inflow is None or not source:
            continue
        observed_days.add(flow_date)
        by_plate.setdefault(str(plate_code_value), []).append((flow_date, inflow, source))

    required_set = set(required_dates)
    values: dict[str, float] = {}
    sources: set[str] = set()
    for plate_code, observations in by_plate.items():
        dates = {item[0] for item in observations}
        plate_sources = {item[2] for item in observations}
        if dates != required_set or len(observations) != 5 or len(plate_sources) != 1:
            continue
        values[plate_code] = sum(item[1] for item in observations)
        sources.update(plate_sources)
    return values, len(observed_days.intersection(required_set)), sorted(sources)


def _sector_strength_values(
    session: Session,
    day_start: datetime,
    day_end: datetime,
) -> tuple[dict[str, float], str | None]:
    snapshot = session.scalars(
        select(SectorSnapshot)
        .where(
            SectorSnapshot.as_of >= day_start,
            SectorSnapshot.as_of <= day_end,
        )
        .order_by(SectorSnapshot.as_of.desc(), SectorSnapshot.id.desc())
        .limit(1)
    ).first()
    if snapshot is None:
        return {}, None
    values: dict[str, float] = {}
    for item in snapshot.payload:
        if not isinstance(item, Mapping):
            continue
        plate_code = str(item.get("plate_code") or "").strip()
        strength = _finite(item.get("strength"))
        if plate_code and strength is not None:
            values[plate_code] = strength
    return values, _utc(snapshot.as_of).isoformat()


def _map_sector_values(
    symbols: set[str],
    memberships: Mapping[str, list[str]],
    plate_values: Mapping[str, float],
) -> dict[str, float]:
    mapped: dict[str, float] = {}
    for symbol in symbols:
        values = [
            plate_values[plate] for plate in memberships.get(symbol, []) if plate in plate_values
        ]
        if values:
            mapped[symbol] = float(np.mean(values))
    return mapped


def compute_factors_for_date(session: Session, trade_date: date) -> pd.DataFrame:
    """Build a point-in-time factor-wide frame for eligible target-day securities."""

    day_start, decision_cutoff = _decision_window(trade_date)
    security_frame, universe = _eligible_securities(
        session,
        trade_date,
        day_start,
        decision_cutoff,
    )
    symbols = set(security_frame.get("symbol", pd.Series(dtype=str)).astype(str))
    frame = pd.DataFrame(
        index=pd.Index(sorted(symbols), name="symbol"),
        columns=FACTOR_SET,
        dtype=float,
    )
    trading_dates = _trading_dates(session, trade_date)
    bar_factors, price_rows, missing_adjustments = _load_bar_factors(
        session,
        symbols,
        trading_dates,
    )
    frame.update(bar_factors)

    financials, financial_selected = _load_financial_factors(
        session,
        symbols,
        decision_cutoff,
    )
    for symbol, metrics in financials.items():
        for metric, value in metrics.items():
            frame.at[symbol, metric] = value

    valuations, valuation_selected = _load_valuation_factors(
        session,
        symbols,
        trade_date,
        decision_cutoff,
    )
    for column, factor in (
        ("pe_ttm", "pe_percentile"),
        ("pb_mrq", "pb_percentile"),
    ):
        values = pd.to_numeric(
            pd.Series(
                {symbol: item[column] for symbol, item in valuations.items()},
                dtype=float,
            ),
            errors="coerce",
        )
        values = values.where(values > 0)
        percentiles = values.rank(method="average", pct=True)
        frame.loc[percentiles.index, factor] = percentiles

    flow_memberships = _pit_sector_memberships(
        session,
        trade_date,
        decision_cutoff,
    )
    required_flow_dates = trading_dates[-5:] if len(trading_dates) >= 5 else []
    plate_flows, sector_flow_days, sector_flow_sources = _sector_flow_values(
        session,
        required_flow_dates,
    )
    for symbol, value in _map_sector_values(
        symbols,
        flow_memberships,
        plate_flows,
    ).items():
        frame.at[symbol, "net_inflow_5d"] = value

    current_memberships = _current_sector_memberships(session, decision_cutoff)
    plate_strength, sector_snapshot_as_of = _sector_strength_values(
        session,
        day_start,
        decision_cutoff,
    )
    for symbol, value in _map_sector_values(
        symbols,
        current_memberships,
        plate_strength,
    ).items():
        frame.at[symbol, "sector_strength"] = value

    frame = frame.replace([np.inf, -np.inf], np.nan)
    factor_coverage = {factor: int(frame[factor].notna().sum()) for factor in FACTOR_SET}
    frame.attrs = {
        "model_version": "factor-v1.1.0",
        "trade_date": trade_date.isoformat(),
        "decision_cutoff": decision_cutoff.isoformat(),
        "universe": universe,
        "eligible": len(frame),
        "st_status_known": int(
            security_frame.get(
                "st_status_known",
                pd.Series(dtype=bool),
            ).sum()
        ),
        "eligibility_ratio": round(len(frame) / universe, 6) if universe else 0.0,
        "trading_sessions": len(trading_dates),
        "price_rows": price_rows,
        "adjustment_factor_missing_rows": missing_adjustments,
        "financial_values": financial_selected,
        "valuation_values": valuation_selected,
        "sector_membership_symbols": len(current_memberships),
        "sector_membership_pit_symbols": len(flow_memberships),
        "sector_flow_days": sector_flow_days,
        "sector_flow_sources": sector_flow_sources,
        "sector_membership_pit_rows": sum(
            len(plates) for plates in flow_memberships.values()
        ),
        "sector_snapshot_as_of": sector_snapshot_as_of,
        "coverage": factor_coverage,
    }
    return frame


def zscore_cross_section(frame: pd.DataFrame) -> pd.DataFrame:
    """Winsorize each factor at 2.5% tails, then population-zscore it."""

    normalized = pd.DataFrame(index=frame.index, columns=frame.columns, dtype=float)
    for column in frame.columns:
        values = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
        valid = values.dropna()
        if valid.empty:
            continue
        lower = float(valid.quantile(0.025))
        upper = float(valid.quantile(0.975))
        clipped = values.clip(lower=lower, upper=upper)
        std = float(clipped.dropna().std(ddof=0))
        if not isfinite(std) or np.isclose(std, 0.0):
            normalized[column] = clipped.where(clipped.isna(), 0.0)
        else:
            mean = float(clipped.dropna().mean())
            normalized[column] = (clipped - mean) / std
    normalized.attrs = dict(frame.attrs)
    normalized.attrs["normalization"] = "winsor_2.5pct_population_zscore"
    return normalized


def composite(frame_z: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Return a comparable 0-100 percentile score with globally fixed L1 weights."""

    unknown = sorted(set(weights).difference(FACTOR_SET))
    if unknown:
        raise ValueError(f"综合评分包含未知因子：{unknown}")
    invalid = [
        name
        for name, value in weights.items()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
    ]
    if invalid:
        raise ValueError(f"综合评分权重必须是有限数值：{sorted(invalid)}")
    active = [
        factor
        for factor, weight in weights.items()
        if weight != 0.0
        and factor in frame_z.columns
        and pd.to_numeric(frame_z[factor], errors="coerce").notna().any()
    ]
    denominator = sum(abs(float(weights[factor])) for factor in active)
    if not active or denominator == 0.0:
        raise ValueError("综合评分至少需要一个已启用的非零权重因子。")

    signal = pd.Series(0.0, index=frame_z.index, dtype=float)
    for factor in active:
        values = pd.to_numeric(frame_z[factor], errors="coerce").replace([np.inf, -np.inf], np.nan)
        signal = signal.add(values.fillna(0.0) * float(weights[factor]), fill_value=0.0)
    signal /= denominator

    if signal.empty:
        scores = pd.Series(index=signal.index, dtype=float, name="score")
    elif int(signal.nunique(dropna=True)) <= 1:
        scores = pd.Series(50.0, index=signal.index, dtype=float, name="score")
    else:
        ranks = signal.rank(method="average")
        scores = ((ranks - 1.0) / (len(signal) - 1.0) * 100.0).rename("score")
    scores.attrs = {
        "active_factors": active,
        "l1_denominator": denominator,
    }
    return scores
