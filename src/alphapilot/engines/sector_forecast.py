from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date
from threading import Lock
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.db.models import DailyBar, SectorConstituent, SectorFlowDaily

HORIZONS = (5, 10, 20)
INDEX_SESSIONS = 120
BAR_SESSIONS = INDEX_SESSIONS + 1
VALIDATION_ORIGINS = 60
MIN_ACTIVE_MEMBERS = 2
MIN_MEMBER_COVERAGE = 0.80
FULL_FLOW_SESSIONS = 84
MODEL_VERSION = "sector-fc-v1.0.0"
NO_FLOW_MODEL_VERSION = "sector-fc-v1.0.0-no-flow"


class SectorForecastError(RuntimeError):
    """The persisted inputs cannot produce an auditable sector forecast."""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    win_rate: float
    expected_excess: float
    origins: int
    samples: int
    first_origin: date
    last_origin: date


@dataclass(frozen=True, slots=True)
class ForecastResult:
    rows: list[dict[str, Any]]
    stats: dict[str, Any]


_index_cache_lock = Lock()
_index_cache: OrderedDict[tuple[Any, ...], pd.DataFrame] = OrderedDict()
_INDEX_CACHE_LIMIT = 2


def clear_sector_index_cache() -> None:
    with _index_cache_lock:
        _index_cache.clear()


def normalize_constituent_symbol(value: object) -> str | None:
    """Convert a Futu A-share code at the provider boundary to the DB symbol."""

    raw = str(value).strip().upper()
    if "." not in raw:
        return raw if len(raw) == 6 and raw.isdigit() else None
    market, symbol = raw.split(".", 1)
    if market not in {"SH", "SZ"} or len(symbol) != 6 or not symbol.isdigit():
        return None
    return symbol


def _finite_float(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _benchmark_dates(session: Session, target_date: date) -> list[date]:
    dates = list(
        session.scalars(
            select(DailyBar.trade_date)
            .where(
                DailyBar.symbol == "SH.000001",
                DailyBar.trade_date <= target_date,
            )
            .distinct()
            .order_by(DailyBar.trade_date.desc())
            .limit(BAR_SESSIONS)
        ).all()
    )
    dates.reverse()
    if len(dates) < BAR_SESSIONS:
        raise SectorForecastError(
            f"上证指数交易日历只有 {len(dates)} 日，板块预测至少需要 {BAR_SESSIONS} 日。"
        )
    if dates[-1] != target_date:
        raise SectorForecastError(
            f"上证指数交易日历最新日期为 {dates[-1].isoformat()}，"
            f"未覆盖目标日 {target_date.isoformat()}。"
        )
    return dates


def _input_signature(session: Session, dates: list[date]) -> tuple[Any, ...]:
    member_count, member_refresh = session.execute(
        select(
            func.count(SectorConstituent.id),
            func.max(SectorConstituent.refreshed_at),
        )
    ).one()
    bar_count, bar_ingested = session.execute(
        select(
            func.count(DailyBar.id),
            func.max(DailyBar.ingested_at),
        ).where(
            DailyBar.trade_date.in_(dates),
            func.length(DailyBar.symbol) == 6,
        )
    ).one()
    bind = session.get_bind()
    return (
        str(bind.engine.url),
        dates[0],
        dates[-1],
        int(member_count or 0),
        member_refresh,
        int(bar_count or 0),
        bar_ingested,
    )


def _load_members(session: Session) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for plate_code, plate_name, raw_symbol in session.execute(
        select(
            SectorConstituent.plate_code,
            SectorConstituent.plate_name,
            SectorConstituent.symbol,
        ).order_by(SectorConstituent.plate_code, SectorConstituent.symbol)
    ):
        symbol = normalize_constituent_symbol(raw_symbol)
        if symbol is None:
            continue
        records.append(
            {
                "plate_code": str(plate_code),
                "plate_name": str(plate_name),
                "symbol": symbol,
            }
        )
    members = pd.DataFrame.from_records(records)
    if members.empty:
        raise SectorForecastError("板块成分股缓存为空或证券代码无法识别。")
    members = members.drop_duplicates(subset=["plate_code", "symbol"], keep="last")
    return members


def _load_bars(session: Session, dates: list[date]) -> pd.DataFrame:
    statement = (
        select(
            DailyBar.symbol,
            DailyBar.trade_date,
            DailyBar.close,
            DailyBar.amount,
        )
        .where(
            DailyBar.trade_date.in_(dates),
            func.length(DailyBar.symbol) == 6,
        )
        .order_by(DailyBar.symbol, DailyBar.trade_date)
    )
    bars = pd.read_sql(statement, session.connection())
    if bars.empty:
        raise SectorForecastError("目标区间没有六位 A 股日线。")
    bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.date
    bars["close"] = pd.to_numeric(bars["close"], errors="coerce")
    bars["amount"] = pd.to_numeric(bars["amount"], errors="coerce")
    bars.loc[~np.isfinite(bars["close"]) | (bars["close"] <= 0), "close"] = np.nan
    bars = bars.sort_values(["symbol", "trade_date"], kind="stable")
    bars["stock_return"] = bars.groupby("symbol", sort=False)["close"].pct_change(fill_method=None)
    bars["is_up"] = (bars["stock_return"] > 0).where(bars["stock_return"].notna())
    return bars


def _rsi14(returns: pd.Series, groups: pd.Series) -> pd.Series:
    gains = returns.clip(lower=0)
    losses = -returns.clip(upper=0)
    average_gain = gains.groupby(groups, sort=False).transform(
        lambda values: values.rolling(14, min_periods=14).mean()
    )
    average_loss = losses.groupby(groups, sort=False).transform(
        lambda values: values.rolling(14, min_periods=14).mean()
    )
    relative_strength = average_gain / average_loss.replace(0.0, np.nan)
    result = 100.0 - 100.0 / (1.0 + relative_strength)
    result = result.mask((average_loss == 0) & (average_gain > 0), 100.0)
    return result.mask((average_loss == 0) & (average_gain == 0), 50.0)


def _build_sector_index_uncached(session: Session, dates: list[date]) -> pd.DataFrame:
    members = _load_members(session)
    bars = _load_bars(session, dates)
    joined = members.merge(bars, how="inner", on="symbol", validate="many_to_many")
    if joined.empty:
        raise SectorForecastError("板块成分股与日线代码未能关联。")

    member_counts = members.groupby("plate_code", sort=False)["symbol"].nunique()
    plate_names = members.groupby("plate_code", sort=False)["plate_name"].last()
    grouped = (
        joined.groupby(["plate_code", "trade_date"], sort=False)
        .agg(
            sector_return=("stock_return", "mean"),
            breadth=("is_up", "mean"),
            active_members=("stock_return", "count"),
            amount=("amount", "sum"),
        )
        .reset_index()
    )

    index_dates = dates[1:]
    plate_codes = sorted(str(value) for value in member_counts.index)
    complete_index = pd.MultiIndex.from_product(
        [plate_codes, index_dates], names=["plate_code", "trade_date"]
    )
    panel = grouped.set_index(["plate_code", "trade_date"]).reindex(complete_index).reset_index()
    panel["plate_name"] = panel["plate_code"].map(plate_names)
    panel["members"] = panel["plate_code"].map(member_counts).astype(float)
    panel["member_coverage"] = panel["active_members"] / panel["members"]
    panel["valid"] = (
        (panel["active_members"] >= MIN_ACTIVE_MEMBERS)
        & (panel["member_coverage"] >= MIN_MEMBER_COVERAGE)
        & panel["sector_return"].map(lambda value: _finite_float(value) is not None)
    )
    eligible = panel.groupby("plate_code", sort=False)["valid"].all()
    eligible_codes = set(str(value) for value in eligible[eligible].index)
    panel = panel[panel["plate_code"].isin(eligible_codes)].copy()
    if panel.empty:
        raise SectorForecastError("没有板块同时满足 120 日、至少 2 只及 80% 成员覆盖率。")

    panel = panel.sort_values(["plate_code", "trade_date"], kind="stable").reset_index(drop=True)
    groups = panel["plate_code"]
    panel["sector_index"] = (
        100.0 * (1.0 + panel["sector_return"]).groupby(groups, sort=False).cumprod()
    )
    for horizon in HORIZONS:
        panel[f"mom_{horizon}"] = panel.groupby("plate_code", sort=False)[
            "sector_index"
        ].pct_change(horizon, fill_method=None)
    panel["amount_change_5d"] = panel.groupby("plate_code", sort=False)["amount"].pct_change(
        5, fill_method=None
    )
    panel["rsi14"] = _rsi14(panel["sector_return"], groups)
    panel["rs"] = panel["mom_20"] - panel.groupby("trade_date", sort=False)["mom_20"].transform(
        "median"
    )
    panel.attrs = {
        "dates": index_dates,
        "eligible_plates": len(eligible_codes),
        "membership_rows": len(members),
        "joined_rows": len(joined),
        "minimum_coverage": float(panel["member_coverage"].min()),
    }
    return panel


def build_sector_index(session: Session, target_date: date) -> pd.DataFrame:
    """Build and process-cache the audited 120-session equal-weight sector index."""

    dates = _benchmark_dates(session, target_date)
    key = _input_signature(session, dates)
    with _index_cache_lock:
        cached = _index_cache.get(key)
        if cached is not None:
            _index_cache.move_to_end(key)
            return cached.copy(deep=True)

    panel = _build_sector_index_uncached(session, dates)
    with _index_cache_lock:
        _index_cache[key] = panel.copy(deep=True)
        _index_cache.move_to_end(key)
        while len(_index_cache) > _INDEX_CACHE_LIMIT:
            _index_cache.popitem(last=False)
    return panel


def _cross_section_zscore(values: pd.Series, dates: pd.Series) -> pd.Series:
    means = values.groupby(dates, sort=False).transform("mean")
    standard_deviations = values.groupby(dates, sort=False).transform(
        lambda sample: sample.std(ddof=0)
    )
    normalized = (values - means) / standard_deviations.replace(0.0, np.nan)
    return normalized.mask(values.notna() & standard_deviations.eq(0.0), 0.0)


def _attach_flows(session: Session, panel: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    panel_attrs = dict(panel.attrs)
    first_date = min(panel["trade_date"])
    last_date = max(panel["trade_date"])
    statement = (
        select(
            SectorFlowDaily.plate_code,
            SectorFlowDaily.trade_date,
            SectorFlowDaily.net_inflow,
            SectorFlowDaily.source,
        )
        .where(
            SectorFlowDaily.trade_date >= first_date,
            SectorFlowDaily.trade_date <= last_date,
        )
        .order_by(SectorFlowDaily.plate_code, SectorFlowDaily.trade_date)
    )
    flows = pd.read_sql(statement, session.connection())
    if flows.empty:
        result = panel.copy()
        result["net_inflow"] = np.nan
        result["flow_5d"] = np.nan
        result["flow_turn"] = np.nan
        result.attrs = panel_attrs
        return result, {"complete_days": 0, "sources": [], "full_model": False}

    flows["trade_date"] = pd.to_datetime(flows["trade_date"]).dt.date
    flows["net_inflow"] = pd.to_numeric(flows["net_inflow"], errors="coerce")
    selected = flows[["plate_code", "trade_date", "net_inflow"]]
    result = panel.merge(
        selected,
        how="left",
        on=["plate_code", "trade_date"],
        validate="one_to_one",
    )
    result = result.sort_values(["plate_code", "trade_date"], kind="stable").reset_index(drop=True)
    result["flow_5d"] = result.groupby("plate_code", sort=False)["net_inflow"].transform(
        lambda values: values.rolling(5, min_periods=5).sum()
    )
    result["flow_turn"] = result.groupby("plate_code", sort=False)["flow_5d"].diff()

    expected_plates = int(result["plate_code"].nunique())
    coverage_by_date = result.groupby("trade_date", sort=False)["net_inflow"].count()
    complete_dates: list[date] = []
    for value, count in coverage_by_date.items():
        if int(count) == expected_plates:
            complete_dates.append(date.fromisoformat(str(value)))
    panel_dates = sorted(result["trade_date"].unique())
    required_dates = panel_dates[-FULL_FLOW_SESSIONS:]
    full_model = len(required_dates) == FULL_FLOW_SESSIONS and set(required_dates).issubset(
        complete_dates
    )
    sources = sorted(str(value) for value in flows["source"].dropna().unique())
    result.attrs = panel_attrs
    return result, {
        "complete_days": len(complete_dates),
        "first_complete_day": min(complete_dates).isoformat() if complete_dates else None,
        "last_complete_day": max(complete_dates).isoformat() if complete_dates else None,
        "sources": sources,
        "full_model": full_model,
    }


def score_sector_panel(panel: pd.DataFrame, horizon: int, *, use_flow: bool) -> pd.DataFrame:
    if horizon not in HORIZONS:
        raise ValueError(f"unsupported sector forecast horizon: {horizon}")
    result = panel.copy()
    result["breadth_z"] = _cross_section_zscore(result["breadth"], result["trade_date"])
    result["rs_z"] = _cross_section_zscore(result["rs"], result["trade_date"])
    components = 0.35 * result[f"mom_{horizon}"]
    components = components + 0.20 * result["breadth_z"] + 0.20 * result["rs_z"]
    denominator = 0.75
    if use_flow:
        result["flow_z"] = _cross_section_zscore(result["flow_5d"], result["trade_date"])
        components = components + 0.25 * result["flow_z"]
        denominator = 1.0
    else:
        result["flow_z"] = np.nan
    result["score_input"] = components / denominator
    result["score"] = (
        result["score_input"]
        .groupby(result["trade_date"], sort=False)
        .rank(method="average", pct=True)
        * 100.0
    )
    result["score_trend"] = result.groupby("plate_code", sort=False)["score"].diff(5)
    return result


def rolling_validate(
    scored: pd.DataFrame,
    horizon: int,
    *,
    origins: int = VALIDATION_ORIGINS,
) -> ValidationResult:
    if origins <= 0:
        raise ValueError("validation origins must be positive")
    frame = scored.copy()
    frame["future_return"] = (
        frame.groupby("plate_code", sort=False)["sector_index"].shift(-horizon)
        / frame["sector_index"]
        - 1.0
    )
    frame["future_median"] = frame.groupby("trade_date", sort=False)["future_return"].transform(
        "median"
    )
    valid = frame.dropna(subset=["score", "future_return", "future_median"]).copy()
    complete_dates = sorted(valid["trade_date"].unique())
    if len(complete_dates) < origins:
        raise SectorForecastError(
            f"{horizon} 日验证只有 {len(complete_dates)} 个完整起点，要求 {origins} 个。"
        )
    selected_dates = complete_dates[-origins:]
    valid = valid[valid["trade_date"].isin(selected_dates)]

    top_frames: list[pd.DataFrame] = []
    for _, cross_section in valid.groupby("trade_date", sort=True):
        top_count = max(1, math.ceil(len(cross_section) * 0.20))
        ordered = cross_section.sort_values(
            ["score", "plate_code"], ascending=[False, True], kind="stable"
        )
        top_frames.append(ordered.head(top_count))
    selected = pd.concat(top_frames, ignore_index=True)
    won = selected["future_return"] > selected["future_median"]
    excess = selected["future_return"] - selected["future_median"]
    return ValidationResult(
        win_rate=float(won.mean()),
        expected_excess=float(excess.mean()),
        origins=len(selected_dates),
        samples=len(selected),
        first_origin=pd.Timestamp(selected_dates[0]).date(),
        last_origin=pd.Timestamp(selected_dates[-1]).date(),
    )


def classify_lifecycle(
    *,
    score: float | None,
    score_trend: float | None,
    rsi14: float | None,
    flow_5d: float | None,
) -> str | None:
    if score is None or score_trend is None:
        return None
    if score > 80 and rsi14 is not None and rsi14 > 70:
        return "boom"
    if score > 60 and score_trend > 0:
        return "rising"
    if score_trend < 0 and score < 60:
        return "decline"
    if score < 30 and score_trend >= 0:
        return "bottoming"
    if 30 <= score <= 60 and score_trend > 0 and flow_5d is not None and flow_5d > 0:
        return "recovery"
    return None


def _reversal_scores(current: pd.DataFrame, *, use_flow: bool) -> pd.Series:
    if not use_flow:
        return pd.Series(np.nan, index=current.index, dtype=float)
    flow_turn_z = _cross_section_zscore(current["flow_turn"], current["trade_date"])
    oversold_rsi = (35.0 - current["rsi14"]).clip(lower=0.0)
    raw = (100.0 - current["score"]) * 0.40 + flow_turn_z * 0.40 + oversold_rsi * 0.20
    return raw.rank(method="average", pct=True) * 100.0


def compute_sector_forecasts(session: Session, target_date: date) -> ForecastResult:
    panel = build_sector_index(session, target_date)
    panel, flow_stats = _attach_flows(session, panel)
    use_flow = bool(flow_stats["full_model"])
    model_version = MODEL_VERSION if use_flow else NO_FLOW_MODEL_VERSION

    rows: list[dict[str, Any]] = []
    validation_stats: dict[str, dict[str, Any]] = {}
    lifecycle_counts: dict[str, int] = {}
    expected_plates = int(panel["plate_code"].nunique())
    for horizon in HORIZONS:
        scored = score_sector_panel(panel, horizon, use_flow=use_flow)
        validation = rolling_validate(scored, horizon)
        validation_stats[str(horizon)] = {
            "win_rate": validation.win_rate,
            "expected_excess": validation.expected_excess,
            "origins": validation.origins,
            "samples": validation.samples,
            "first_origin": validation.first_origin.isoformat(),
            "last_origin": validation.last_origin.isoformat(),
        }
        current = scored[scored["trade_date"] == target_date].copy()
        if len(current) != expected_plates or current["score"].isna().any():
            raise SectorForecastError(
                f"{horizon} 日目标截面不完整：expected={expected_plates}, actual={len(current)}。"
            )
        current["reversal_score"] = _reversal_scores(current, use_flow=use_flow)
        for record in current.to_dict(orient="records"):
            item = {str(key): value for key, value in record.items()}
            score = _finite_float(item.get("score"))
            score_trend = _finite_float(item.get("score_trend"))
            rsi14 = _finite_float(item.get("rsi14"))
            flow_5d = _finite_float(item.get("flow_5d")) if use_flow else None
            lifecycle = classify_lifecycle(
                score=score,
                score_trend=score_trend,
                rsi14=rsi14,
                flow_5d=flow_5d,
            )
            lifecycle_key = lifecycle or "unclassified"
            if horizon == 20:
                lifecycle_counts[lifecycle_key] = lifecycle_counts.get(lifecycle_key, 0) + 1
            if score is None:
                raise SectorForecastError("目标截面出现非有限 score。")
            rows.append(
                {
                    "plate_code": str(item["plate_code"]),
                    "plate_name": str(item["plate_name"]),
                    "trade_date": target_date,
                    "horizon": horizon,
                    "score": score,
                    "expected_excess": validation.expected_excess,
                    "win_rate": validation.win_rate,
                    "lifecycle": lifecycle,
                    "rsi14": rsi14,
                    "reversal_score": _finite_float(item.get("reversal_score")),
                    "model_version": model_version,
                }
            )

    panel_stats = dict(panel.attrs)
    return ForecastResult(
        rows=rows,
        stats={
            "date": target_date.isoformat(),
            "plates": expected_plates,
            "rows": len(rows),
            "horizons": list(HORIZONS),
            "model_version": model_version,
            "flow_mode": "full" if use_flow else "no-flow",
            "flow": flow_stats,
            "validation": validation_stats,
            "lifecycle_h20": lifecycle_counts,
            "index": {
                "sessions": len(panel_stats.get("dates", [])),
                "membership_rows": int(panel_stats.get("membership_rows", 0)),
                "joined_rows": int(panel_stats.get("joined_rows", 0)),
                "minimum_coverage": float(panel_stats.get("minimum_coverage", 0.0)),
                "turnover_proxy": "daily_bars.amount 5-session change; not used in score",
                "membership_history": "current-snapshot-fixed-universe",
            },
        },
    )
