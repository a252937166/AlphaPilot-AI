from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from math import floor, isfinite
from typing import Literal

import pandas as pd
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from alphapilot.db.models import (
    CompositeScore,
    DailyBar,
    FactorValue,
    ForecastSnapshot,
    Security,
    StyleDaily,
    WatchlistItem,
)
from alphapilot.domain.models import (
    ScreeningCandidate,
    ScreeningRequest,
    ScreeningResponse,
    StyleTag,
)
from alphapilot.engines.style import style_source_fingerprint
from alphapilot.prediction.baseline import BaselineForecastEngine

RiskLevel = Literal["low", "mid", "high"]


class ScreeningFilterError(ValueError):
    """The requested filter cannot be applied truthfully."""


class ScreeningUnavailableError(RuntimeError):
    """Required persisted screening inputs are not available yet."""


@dataclass(frozen=True, slots=True)
class _ScreenRow:
    symbol: str
    trade_date: date
    score: float
    win_rate_20d: float | None
    model_version: str
    display_name: str | None
    industry: str | None
    style: StyleTag | None
    market_cap: float | None
    volatility_z: float | None


def _finite_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _style_or_none(value: object) -> StyleTag | None:
    if value == "growth":
        return "growth"
    if value == "value":
        return "value"
    if value == "defensive":
        return "defensive"
    if value == "balanced":
        return "balanced"
    return None


def _quantile(values: list[float], proportion: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile requires at least one value")
    position = (len(ordered) - 1) * proportion
    lower = floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _risk_thresholds(session: Session, trade_date: date) -> tuple[float, float] | None:
    values = [
        number
        for value in session.scalars(
            select(FactorValue.zscore)
            .select_from(FactorValue)
            .join(
                CompositeScore,
                and_(
                    CompositeScore.symbol == FactorValue.symbol,
                    CompositeScore.trade_date == FactorValue.trade_date,
                ),
            )
            .where(
                FactorValue.trade_date == trade_date,
                FactorValue.factor == "volatility_20d",
                FactorValue.zscore.is_not(None),
            )
        ).all()
        if (number := _finite_or_none(value)) is not None
    ]
    if not values:
        return None
    return _quantile(values, 1 / 3), _quantile(values, 2 / 3)


def _risk_level(
    volatility_z: float | None,
    thresholds: tuple[float, float] | None,
) -> RiskLevel | None:
    if volatility_z is None or thresholds is None:
        return None
    low_max, mid_max = thresholds
    if volatility_z <= low_max:
        return "low"
    if volatility_z <= mid_max:
        return "mid"
    return "high"


def _watchlist_symbols(session: Session) -> list[str]:
    return list(
        dict.fromkeys(
            str(symbol)
            for symbol in session.scalars(
                select(WatchlistItem.symbol).order_by(WatchlistItem.created_at)
            ).all()
        )
    )


def _screen_rows(
    session: Session,
    request: ScreeningRequest,
    trade_date: date,
    thresholds: tuple[float, float] | None,
    watchlist_symbols: list[str] | None,
    *,
    style_is_current: bool,
) -> list[_ScreenRow]:
    statement = (
        select(
            CompositeScore.symbol,
            CompositeScore.trade_date,
            CompositeScore.score,
            CompositeScore.win_rate_20d,
            CompositeScore.model_version,
            Security.name,
            Security.industry_csrc,
            Security.style_tag,
            Security.market_cap,
            FactorValue.zscore,
        )
        .join(Security, Security.symbol == CompositeScore.symbol)
        .outerjoin(
            FactorValue,
            and_(
                FactorValue.symbol == CompositeScore.symbol,
                FactorValue.trade_date == CompositeScore.trade_date,
                FactorValue.factor == "volatility_20d",
            ),
        )
        .where(
            CompositeScore.trade_date == trade_date,
            Security.list_status == "listed",
            Security.is_st.is_(False),
        )
    )
    if watchlist_symbols is not None:
        statement = statement.where(CompositeScore.symbol.in_(watchlist_symbols))

    if request.industries is not None:
        industries = list(
            dict.fromkeys(item.strip() for item in request.industries if item.strip())
        )
        if not industries:
            raise ScreeningFilterError("行业筛选不能为空，请选择至少一个有效行业。")
        statement = statement.where(Security.industry_csrc.in_(industries))
    if request.min_market_cap is not None:
        statement = statement.where(Security.market_cap >= request.min_market_cap)
    if request.style is not None:
        statement = statement.where(Security.style_tag == request.style)
    if request.risk_level is not None:
        if thresholds is None:
            raise ScreeningUnavailableError(
                "暂无波动率因子，无法按风险等级筛选；请先运行 compute_factors。"
            )
        low_max, mid_max = thresholds
        if request.risk_level == "low":
            statement = statement.where(FactorValue.zscore <= low_max)
        elif request.risk_level == "mid":
            statement = statement.where(
                FactorValue.zscore > low_max,
                FactorValue.zscore <= mid_max,
            )
        else:
            statement = statement.where(FactorValue.zscore > mid_max)

    rows: list[_ScreenRow] = []
    for record in session.execute(statement).all():
        score = _finite_or_none(record.score)
        if score is None:
            continue
        rows.append(
            _ScreenRow(
                symbol=str(record.symbol),
                trade_date=record.trade_date,
                score=score,
                win_rate_20d=_finite_or_none(record.win_rate_20d),
                model_version=str(record.model_version),
                display_name=str(record.name) if record.name is not None else None,
                industry=(str(record.industry_csrc) if record.industry_csrc is not None else None),
                style=_style_or_none(record.style_tag) if style_is_current else None,
                market_cap=_finite_or_none(record.market_cap),
                volatility_z=_finite_or_none(record.zscore),
            )
        )
    return rows


def _latest_expected_returns(session: Session) -> dict[str, float]:
    latest = (
        select(
            ForecastSnapshot.symbol.label("symbol"),
            func.max(ForecastSnapshot.as_of).label("as_of"),
        )
        .group_by(ForecastSnapshot.symbol)
        .subquery()
    )
    values: dict[str, float] = {}
    records = session.execute(
        select(ForecastSnapshot.symbol, ForecastSnapshot.horizons).join(
            latest,
            and_(
                ForecastSnapshot.symbol == latest.c.symbol,
                ForecastSnapshot.as_of == latest.c.as_of,
            ),
        )
    ).all()
    for symbol, horizons in records:
        if not isinstance(horizons, Mapping):
            continue
        horizon = horizons.get("20d")
        if not isinstance(horizon, Mapping):
            continue
        expected_return = _finite_or_none(horizon.get("expected_return"))
        if expected_return is not None:
            values[str(symbol)] = expected_return
    return values


def _preselect(
    rows: list[_ScreenRow],
    request: ScreeningRequest,
    persisted_expected: Mapping[str, float],
) -> list[_ScreenRow]:
    if request.sort_by == "expected_return":
        ordered = sorted(
            rows,
            key=lambda row: (
                row.symbol in persisted_expected,
                persisted_expected.get(row.symbol, row.score / 100.0),
                row.score,
            ),
            reverse=True,
        )
    elif request.sort_by == "win_rate":
        ordered = sorted(
            rows,
            key=lambda row: (
                row.win_rate_20d is not None,
                row.win_rate_20d if row.win_rate_20d is not None else row.score / 100.0,
                row.score,
            ),
            reverse=True,
        )
    else:
        ordered = sorted(rows, key=lambda row: row.score, reverse=True)
    return ordered[: request.top_n]


def _load_local_bars(
    session: Session,
    symbols: list[str],
    trade_date: date,
    lookback_days: int,
) -> dict[str, pd.DataFrame]:
    if not symbols:
        return {}
    start = trade_date - timedelta(days=int(lookback_days * 1.7))
    grouped: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in symbols}
    records = session.execute(
        select(
            DailyBar.symbol,
            DailyBar.trade_date,
            DailyBar.open,
            DailyBar.high,
            DailyBar.low,
            DailyBar.close,
            DailyBar.volume,
            DailyBar.amount,
        )
        .where(
            DailyBar.symbol.in_(symbols),
            DailyBar.trade_date >= start,
            DailyBar.trade_date <= trade_date,
        )
        .order_by(DailyBar.symbol, DailyBar.trade_date)
    ).all()
    for record in records:
        symbol = str(record.symbol)
        grouped.setdefault(symbol, []).append(
            {
                "date": record.trade_date,
                "open": record.open,
                "high": record.high,
                "low": record.low,
                "close": record.close,
                "volume": record.volume,
                "amount": record.amount,
            }
        )
    return {
        symbol: pd.DataFrame(rows).tail(lookback_days) for symbol, rows in grouped.items() if rows
    }


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))


def _enrich_candidates(
    session: Session,
    selected: list[_ScreenRow],
    request: ScreeningRequest,
    thresholds: tuple[float, float] | None,
) -> tuple[list[ScreeningCandidate], dict[str, str]]:
    if not selected:
        return [], {}
    frames = _load_local_bars(
        session,
        [row.symbol for row in selected],
        selected[0].trade_date,
        request.lookback_days,
    )
    engine = BaselineForecastEngine()
    candidates: list[ScreeningCandidate] = []
    failures: dict[str, str] = {}
    for row in selected:
        reasons = [f"综合因子评分 {row.score:.2f}，评分日 {row.trade_date.isoformat()}。"]
        warnings: list[str] = []
        trend_score: float | None = None
        risk_score: float | None = None
        p_up_5d: float | None = None
        p_up_20d: float | None = None
        expected_return_20d: float | None = None
        confidence_20d: float | None = None
        forecast_source: str | None = None
        frame = frames.get(row.symbol)
        try:
            if frame is None or len(frame) < 30:
                raise ValueError("insufficient local bars")
            forecast = engine.forecast(row.symbol, frame, "daily_bars-cache")
            horizon_5d = forecast.horizons["5d"]
            horizon_20d = forecast.horizons["20d"]
            volatility = forecast.features["volatility_20d"]
            trend_score = _clamp_score(50 + (horizon_20d.p_up - 0.5) * 150)
            risk_score = _clamp_score(100 - volatility * 110)
            p_up_5d = horizon_5d.p_up
            p_up_20d = horizon_20d.p_up
            expected_return_20d = horizon_20d.expected_return
            confidence_20d = horizon_20d.confidence
            forecast_source = forecast.provider
            reasons.extend(
                [
                    f"20 日上涨概率为 {horizon_20d.p_up:.1%}。",
                    f"20 日预期收益为 {horizon_20d.expected_return:.2%}。",
                ]
            )
            warnings.extend(forecast.warnings)
        except (KeyError, TypeError, ValueError):
            message = "本地日线不足，未生成概率预测；请先运行 sync_daily_bars。"
            failures[row.symbol] = message
            warnings.append(message)

        candidates.append(
            ScreeningCandidate(
                rank=0,
                symbol=row.symbol,
                score=row.score,
                trend_score=trend_score,
                risk_score=risk_score,
                quality_placeholder_score=None,
                p_up_5d=p_up_5d,
                p_up_20d=p_up_20d,
                expected_return_20d=expected_return_20d,
                confidence_20d=confidence_20d,
                display_name=row.display_name,
                industry=row.industry,
                style=row.style,
                risk_level=_risk_level(row.volatility_z, thresholds),
                market_cap=row.market_cap,
                trade_date=row.trade_date,
                win_rate_20d=row.win_rate_20d,
                forecast_source=forecast_source,
                reasons=reasons,
                warnings=warnings,
            )
        )

    if request.sort_by == "expected_return":
        candidates.sort(
            key=lambda item: (
                item.expected_return_20d is not None,
                item.expected_return_20d if item.expected_return_20d is not None else float("-inf"),
                item.score,
            ),
            reverse=True,
        )
    return [
        candidate.model_copy(update={"rank": rank}) for rank, candidate in enumerate(candidates, 1)
    ], failures


def run_factor_screen(session: Session, request: ScreeningRequest) -> ScreeningResponse:
    """Screen the latest persisted factor cross-section without live provider calls."""

    if request.universe == "custom":
        raise ScreeningFilterError("自定义股票池请使用 custom 兼容筛选链路。")
    if request.symbols is not None:
        raise ScreeningFilterError(
            "all 或 watchlist 模式不接受 symbols；请移除该字段或改用 custom。"
        )
    if request.provider is not None:
        raise ScreeningFilterError(
            "all 或 watchlist 模式固定使用本地因子与日线缓存，请移除 provider。"
        )
    latest_date = session.scalar(select(func.max(CompositeScore.trade_date)))
    if not isinstance(latest_date, date):
        raise ScreeningUnavailableError("暂无综合评分，请先运行 compute_factors 任务后重试。")
    model_version = session.scalar(
        select(CompositeScore.model_version)
        .where(CompositeScore.trade_date == latest_date)
        .limit(1)
    )
    if not isinstance(model_version, str):
        raise ScreeningUnavailableError("最新综合评分缺少模型版本，请重新运行 compute_factors。")

    latest_style = session.scalars(
        select(StyleDaily).order_by(StyleDaily.trade_date.desc()).limit(1)
    ).first()
    current_source_fingerprint = style_source_fingerprint(session, latest_date)
    style_is_current = bool(
        latest_style is not None
        and latest_style.trade_date == latest_date
        and latest_style.source_fingerprint == current_source_fingerprint
    )
    if request.style is not None and not style_is_current:
        raise ScreeningUnavailableError(
            "风格数据尚未与最新综合评分同步，请先运行 compute_style_daily 任务后重试。"
        )

    watchlist_symbols: list[str] | None = None
    if request.universe == "watchlist":
        watchlist_symbols = _watchlist_symbols(session)
        if not watchlist_symbols:
            raise ScreeningFilterError("自选池为空，请先添加股票后再运行筛选。")
        requested = len(watchlist_symbols)
    else:
        requested = int(
            session.scalar(
                select(func.count()).select_from(Security).where(Security.list_status == "listed")
            )
            or 0
        )

    thresholds = _risk_thresholds(session, latest_date)
    rows = _screen_rows(
        session,
        request,
        latest_date,
        thresholds,
        watchlist_symbols,
        style_is_current=style_is_current,
    )
    persisted_expected = (
        _latest_expected_returns(session) if request.sort_by == "expected_return" else {}
    )
    selected = _preselect(rows, request, persisted_expected)
    candidates, failed = _enrich_candidates(session, selected, request, thresholds)
    if (
        style_is_current
        and latest_style is not None
        and style_source_fingerprint(session, latest_date) != latest_style.source_fingerprint
    ):
        raise ScreeningUnavailableError(
            "风格数据在筛选期间发生变化，请先运行 compute_style_daily 任务后重试。"
        )
    return ScreeningResponse(
        generated_at=datetime.now(UTC),
        provider="factor-db",
        model_version=model_version,
        requested=requested,
        succeeded=len(rows),
        failed=failed,
        candidates=candidates,
    )
