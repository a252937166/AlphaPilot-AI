from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from math import isfinite
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.core.timeutil import iso_utc
from alphapilot.data.provenance import (
    AUDITED_DAILY_BAR_SOURCES,
    AUDITED_SECTOR_FLOW_SOURCES,
    MIN_AUDITED_DAILY_BAR_COVERAGE,
)
from alphapilot.db.engine import get_session
from alphapilot.db.market_audit import (
    audited_daily_coverage,
    audited_daily_symbol_count,
)
from alphapilot.db.models import (
    DailyBar,
    SectorConstituent,
    SectorFlowDaily,
    SectorForecast,
    SectorSnapshot,
)
from alphapilot.engines.sector_forecast import normalize_constituent_symbol
from alphapilot.futu.client import FutuClient, FutuClientError

FALLBACK_MAX_PLATES = 10
MAX_CONSTITUENTS_PER_PLATE = 30
SNAPSHOT_BATCH_SIZE = 400
FRESH_CONSTITUENT_DAYS = 7
_plate_cache_lock = Lock()
_plate_constituents: dict[str, dict[str, Any]] = {}


class SectorServiceError(RuntimeError):
    pass


class SectorNotFoundError(SectorServiceError):
    pass


SECTOR_FORECAST_HORIZONS = (5, 10, 20)
SECTOR_LIFECYCLES = ("boom", "rising", "decline", "bottoming", "recovery")
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
FORECAST_INPUT_COVERAGE_FLOOR = MIN_AUDITED_DAILY_BAR_COVERAGE
FLOW_WINDOW_DAYS = 5
LEADER_LOOKBACK_SESSIONS = 20
LEADER_LINK_LIMIT = 5


def _utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(UTC)


def _number(value: object, default: float = 0.0) -> float:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return default
    return result if isfinite(result) else default


def _optional_number(value: object) -> float | None:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _forecast_row(row: SectorForecast, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "plate_code": row.plate_code,
        "plate_name": row.plate_name,
        "trade_date": row.trade_date.isoformat(),
        "horizon": row.horizon,
        "score": row.score,
        "expected_excess": row.expected_excess,
        "win_rate": row.win_rate,
        "lifecycle": row.lifecycle,
        "rsi14": row.rsi14,
        "reversal_score": row.reversal_score,
        "model_version": row.model_version,
    }


def _trusted_benchmark_dates(
    session: Session,
    *,
    end_date: date,
    limit: int,
) -> list[date]:
    rows = list(
        session.scalars(
            select(DailyBar.trade_date)
            .where(
                DailyBar.symbol == "SH.000001",
                DailyBar.trade_date <= end_date,
                DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
                DailyBar.close > 0,
            )
            .distinct()
            .order_by(DailyBar.trade_date.desc())
            .limit(limit)
        ).all()
    )
    rows.reverse()
    return rows


def _trade_date_coverage(session: Session, trade_date: date) -> dict[str, Any]:
    coverage = audited_daily_coverage(session, trade_date)
    if coverage.reference_trade_date is None:
        raise SectorServiceError(
            f"{trade_date.isoformat()} 之前没有可信基准交易日，无法校验全市场日线完整性。"
        )
    return {
        "trade_date": trade_date,
        "symbol_count": coverage.symbol_count,
        "reference_trade_date": coverage.reference_trade_date,
        "reference_symbol_count": coverage.reference_symbol_count,
        "ratio": coverage.ratio,
        "minimum_ratio": coverage.minimum_ratio,
        "complete": coverage.complete,
    }


def _latest_complete_forecast_date(session: Session) -> tuple[date, list[date]]:
    candidates = list(
        session.scalars(
            select(SectorForecast.trade_date)
            .distinct()
            .order_by(SectorForecast.trade_date.desc())
        ).all()
    )
    ignored: list[date] = []
    for candidate in candidates:
        forecast_rows = session.execute(
            select(
                SectorForecast.horizon,
                SectorForecast.plate_code,
                SectorForecast.model_version,
            ).where(SectorForecast.trade_date == candidate)
        ).all()
        plates_by_horizon: dict[int, set[str]] = {}
        model_versions: set[str] = set()
        for horizon, plate_code, model_version in forecast_rows:
            plates_by_horizon.setdefault(int(horizon), set()).add(str(plate_code))
            model_versions.add(str(model_version))
        plate_sets = {frozenset(plates) for plates in plates_by_horizon.values()}
        if (
            set(plates_by_horizon) != set(SECTOR_FORECAST_HORIZONS)
            or len(plate_sets) != 1
            or not next(iter(plate_sets), frozenset())
            or len(model_versions) != 1
        ):
            ignored.append(candidate)
            continue
        benchmark_exists = session.scalar(
            select(DailyBar.id)
            .where(
                DailyBar.symbol == "SH.000001",
                DailyBar.trade_date == candidate,
                DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
                DailyBar.close > 0,
            )
            .limit(1)
        )
        if benchmark_exists is None:
            ignored.append(candidate)
            continue
        try:
            coverage = _trade_date_coverage(session, candidate)
        except SectorServiceError:
            ignored.append(candidate)
            continue
        if bool(coverage["complete"]):
            return candidate, ignored
        ignored.append(candidate)
    raise SectorServiceError("没有通过可信来源与全市场覆盖校验的板块预测截面。")


def _forecast_input_state(
    session: Session,
    *,
    forecast_date: date,
    benchmark_date: date,
    ignored_forecast_dates: list[date],
) -> dict[str, Any]:
    state = _trade_date_coverage(session, benchmark_date)
    forecast_count = audited_daily_symbol_count(session, forecast_date)
    latest_count = int(state["symbol_count"])
    reference_date = state["reference_trade_date"]
    reference_count = int(state["reference_symbol_count"])
    ratio = float(state["ratio"])
    coverage = {
        "latest_symbol_count": latest_count,
        "forecast_symbol_count": forecast_count,
        "reference_trade_date": reference_date.isoformat(),
        "reference_symbol_count": reference_count,
        "ratio": round(ratio, 6),
        "minimum_ratio": FORECAST_INPUT_COVERAGE_FLOOR,
    }
    ignored_text = (
        " 已忽略未通过可信来源或覆盖校验的预测截面："
        + "、".join(value.isoformat() for value in ignored_forecast_dates)
        + "。"
        if ignored_forecast_dates
        else ""
    )
    if benchmark_date == forecast_date and bool(state["complete"]):
        return {
            "input_trade_date": benchmark_date.isoformat(),
            "input_coverage": coverage,
            "ignored_forecast_dates": [
                value.isoformat() for value in ignored_forecast_dates
            ],
            "stale": bool(ignored_forecast_dates),
            "warning": ignored_text.strip() or None,
        }
    if (
        benchmark_date > forecast_date
        and reference_count > 0
        and not bool(state["complete"])
    ):
        return {
            "input_trade_date": benchmark_date.isoformat(),
            "input_coverage": coverage,
            "ignored_forecast_dates": [
                value.isoformat() for value in ignored_forecast_dates
            ],
            "stale": True,
            "warning": (
                f"最新交易日 {benchmark_date.isoformat()} 的全市场日线覆盖仅 "
                f"{latest_count}/{reference_count}（{ratio:.1%}），"
                f"低于 {FORECAST_INPUT_COVERAGE_FLOOR:.0%} 完整性门槛；"
                f"当前明确展示 {forecast_date.isoformat()} 的最近完整预测，未将部分截面当作新预测。"
                f"{ignored_text}"
            ),
        }
    raise SectorServiceError(
        "板块预测尚未与最新完整交易日日线同步，请先运行 sector_forecast 任务。"
    )


def _forecast_flow_enrichment(
    session: Session,
    *,
    plate_codes: set[str],
    forecast_date: date,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    window_dates = _trusted_benchmark_dates(
        session,
        end_date=forecast_date,
        limit=FLOW_WINDOW_DAYS,
    )
    selected_dates = set(window_dates)
    rows = session.scalars(
        select(SectorFlowDaily)
        .where(
            SectorFlowDaily.plate_code.in_(plate_codes),
            SectorFlowDaily.trade_date.in_(selected_dates),
            SectorFlowDaily.source.in_(AUDITED_SECTOR_FLOW_SOURCES),
        )
        .order_by(SectorFlowDaily.trade_date.desc(), SectorFlowDaily.plate_code)
    ).all()
    by_plate: dict[str, dict[date, SectorFlowDaily]] = {}
    for row in rows:
        if row.trade_date not in selected_dates:
            continue
        by_plate.setdefault(row.plate_code, {})[row.trade_date] = row

    enrichment: dict[str, dict[str, Any]] = {}
    for plate_code in plate_codes:
        plate_rows = by_plate.get(plate_code, {})
        usable = [
            plate_rows[trade_day]
            for trade_day in window_dates
            if trade_day in plate_rows
            and _optional_number(plate_rows[trade_day].net_inflow) is not None
        ]
        latest = plate_rows.get(window_dates[-1]) if window_dates else None
        latest_flow = _optional_number(latest.net_inflow) if latest is not None else None
        complete_window = len(window_dates) == FLOW_WINDOW_DAYS and len(usable) == FLOW_WINDOW_DAYS
        sources = sorted({row.source for row in usable if row.source})
        enrichment[plate_code] = {
            "net_inflow": latest_flow,
            "net_inflow_5d": (
                sum(_optional_number(row.net_inflow) or 0.0 for row in usable)
                if complete_window
                else None
            ),
            "flow_coverage_days": len(usable),
            "flow_source": sources[0] if len(sources) == 1 else "mixed" if sources else None,
        }
    actual_dates = [row.trade_date for row in rows if row.net_inflow is not None]
    return enrichment, max(actual_dates).isoformat() if actual_dates else None


def _forecast_strength_enrichment(
    session: Session,
    *,
    plate_codes: set[str],
    forecast_date: date,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    snapshots = session.scalars(
        select(SectorSnapshot).order_by(SectorSnapshot.as_of.desc()).limit(200)
    ).all()
    snapshot = next(
        (
            row
            for row in snapshots
            if _utc(row.as_of).astimezone(MARKET_TIMEZONE).date() <= forecast_date
        ),
        None,
    )
    enrichment: dict[str, dict[str, Any]] = {
        plate_code: {
            "leader_code": None,
            "leader_name": None,
            "leader_change_pct": None,
        }
        for plate_code in plate_codes
    }
    if snapshot is None:
        return enrichment, None
    for raw in snapshot.payload:
        if not isinstance(raw, dict):
            continue
        plate_code = str(raw.get("plate_code") or "")
        if plate_code not in enrichment:
            continue
        enrichment[plate_code] = {
            "leader_code": normalize_constituent_symbol(raw.get("leader_code")),
            "leader_name": str(raw.get("leader_name") or "").strip() or None,
            "leader_change_pct": _optional_number(raw.get("leader_change_pct")),
        }
    return enrichment, iso_utc(snapshot.as_of)


def get_sector_forecast_view(
    session: Session,
    *,
    horizon: int,
    view: str = "forecast",
) -> dict[str, Any]:
    """Return a current, persisted sector forecast view without fabricating flow data."""

    if horizon not in SECTOR_FORECAST_HORIZONS:
        raise ValueError(f"unsupported sector forecast horizon: {horizon}")
    has_forecast = session.scalar(select(SectorForecast.id).limit(1))
    if has_forecast is None:
        raise SectorServiceError("板块预测尚未生成，请先运行 sector_forecast 任务。")
    latest_forecast, ignored_forecast_dates = _latest_complete_forecast_date(session)
    latest_benchmark = session.scalar(
        select(func.max(DailyBar.trade_date)).where(
            DailyBar.symbol == "SH.000001",
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
            DailyBar.close > 0,
        )
    )
    if latest_benchmark is None:
        raise SectorServiceError("上证指数交易日历为空，无法校验板块预测输入。")
    input_state = _forecast_input_state(
        session,
        forecast_date=latest_forecast,
        benchmark_date=latest_benchmark,
        ignored_forecast_dates=ignored_forecast_dates,
    )

    rows = session.scalars(
        select(SectorForecast)
        .where(
            SectorForecast.trade_date == latest_forecast,
            SectorForecast.horizon == horizon,
        )
        .order_by(SectorForecast.score.desc(), SectorForecast.plate_code)
    ).all()
    if not rows:
        raise SectorServiceError(f"板块预测缺少 {horizon} 日截面，请重新运行任务。")
    model_versions = {row.model_version for row in rows}
    if len(model_versions) != 1:
        raise SectorServiceError("板块预测截面混入多个模型版本，已拒绝展示。")
    model_version = next(iter(model_versions))
    plate_codes = {row.plate_code for row in rows}
    flow_by_plate, flow_as_of = _forecast_flow_enrichment(
        session,
        plate_codes=plate_codes,
        forecast_date=latest_forecast,
    )
    strength_by_plate, strength_as_of = _forecast_strength_enrichment(
        session,
        plate_codes=plate_codes,
        forecast_date=latest_forecast,
    )
    no_flow = model_version.endswith("-no-flow")
    fixed_universe_reason = (
        "历史成分有效期尚未落库，胜率为固定当前成分股宇宙回测，不代表无前视偏差的 PIT 结果。"
    )
    degraded_reason = fixed_universe_reason
    if no_flow:
        degraded_reason += " 板块资金流历史不足，当前使用无资金流模型；未补零或复制历史值。"

    selected = list(rows)
    available = True
    reason: str | None = None
    if view == "lifecycle":
        selected.sort(key=lambda row: (row.lifecycle is None, row.lifecycle or "", -row.score))
    elif view == "overbought":
        selected = [row for row in rows if row.rsi14 is not None and row.rsi14 > 70]
        selected.sort(key=lambda row: (-float(row.rsi14 or 0.0), row.plate_code))
    elif view == "reversal":
        selected = [row for row in rows if row.reversal_score is not None]
        selected.sort(key=lambda row: (-float(row.reversal_score or 0.0), row.plate_code))
        if no_flow:
            available = False
            reason = "资金流历史尚不足以计算 flow_turn_z，反转排行将在数据积累完成后开放。"
    elif view != "forecast":
        raise ValueError(f"unsupported sector forecast view: {view}")

    serialized_rows: list[dict[str, Any]] = []
    for rank, row in enumerate(selected, 1):
        serialized = _forecast_row(row, rank)
        serialized.update(flow_by_plate[row.plate_code])
        serialized.update(strength_by_plate[row.plate_code])
        serialized_rows.append(serialized)

    payload: dict[str, Any] = {
        "as_of": latest_forecast.isoformat(),
        "horizon": horizon,
        "model_version": model_version,
        "flow_mode": "no-flow" if no_flow else "full",
        "backtest_scope": "fixed-current-membership",
        "degraded_reason": degraded_reason,
        "available": available,
        "count": len(selected),
        "rows": serialized_rows,
        "flow_as_of": flow_as_of,
        "flow_window_days": FLOW_WINDOW_DAYS,
        "strength_as_of": strength_as_of,
        **input_state,
    }
    if reason is not None:
        payload["reason"] = reason
    if view == "lifecycle":
        counts = {name: 0 for name in SECTOR_LIFECYCLES}
        counts["unclassified"] = 0
        for row in rows:
            counts[row.lifecycle or "unclassified"] += 1
        payload["counts"] = counts
    return payload


def get_sector_leaders(session: Session, *, plate_code: str) -> dict[str, Any]:
    """Rank stocks by audited daily-return correlation with the 20-session leader."""

    members = session.scalars(
        select(SectorConstituent)
        .where(SectorConstituent.plate_code == plate_code)
        .order_by(SectorConstituent.symbol)
    ).all()
    if not members:
        raise SectorNotFoundError(f"未找到板块 {plate_code} 的成分股缓存。")

    names: dict[str, str | None] = {}
    for member in members:
        symbol = normalize_constituent_symbol(member.symbol)
        if symbol is not None:
            names[symbol] = member.name or None
    symbols = sorted(names)
    if len(symbols) < 2:
        raise SectorServiceError(f"板块 {plate_code} 可识别的 A 股成分不足 2 只。")

    forecast_date, _ = _latest_complete_forecast_date(session)
    has_forecast = session.scalar(
        select(SectorForecast.id)
        .where(
            SectorForecast.plate_code == plate_code,
            SectorForecast.trade_date == forecast_date,
        )
        .limit(1)
    )
    if has_forecast is None:
        raise SectorServiceError(f"板块 {plate_code} 尚无可审计预测截面。")
    benchmark_dates = list(
        session.scalars(
            select(DailyBar.trade_date)
            .where(
                DailyBar.symbol == "SH.000001",
                DailyBar.trade_date <= forecast_date,
                DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
                DailyBar.close > 0,
            )
            .distinct()
            .order_by(DailyBar.trade_date.desc())
            .limit(LEADER_LOOKBACK_SESSIONS + 1)
        ).all()
    )
    benchmark_dates.reverse()
    if len(benchmark_dates) < LEADER_LOOKBACK_SESSIONS + 1:
        raise SectorServiceError(
            f"板块联动计算仅有 {len(benchmark_dates) - 1} 个收益观测，"
            f"至少需要 {LEADER_LOOKBACK_SESSIONS} 个。"
        )

    records = [
        {
            "symbol": str(symbol),
            "trade_date": trade_day,
            "close": close,
            "source": str(source),
        }
        for symbol, trade_day, close, source in session.execute(
            select(
                DailyBar.symbol,
                DailyBar.trade_date,
                DailyBar.close,
                DailyBar.source,
            )
            .where(
                DailyBar.symbol.in_(symbols),
                DailyBar.trade_date.in_(benchmark_dates),
                DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
                DailyBar.close > 0,
            )
            .order_by(DailyBar.symbol, DailyBar.trade_date)
        )
    ]
    bars = pd.DataFrame.from_records(records)
    if bars.empty:
        raise SectorServiceError("板块成分股没有可信来源日线。")
    bars["close"] = pd.to_numeric(bars["close"], errors="coerce")
    bars = bars[np.isfinite(bars["close"]) & (bars["close"] > 0)].copy()
    closes = bars.pivot(index="trade_date", columns="symbol", values="close").reindex(
        index=benchmark_dates,
        columns=symbols,
    )
    complete_symbols = [
        str(symbol) for symbol in closes.columns if closes[symbol].notna().all()
    ]
    closes = closes[complete_symbols]
    if len(complete_symbols) < 2:
        raise SectorServiceError(
            f"截至 {forecast_date.isoformat()}，板块内不足 2 只股票具备对齐的 "
            f"{LEADER_LOOKBACK_SESSIONS + 1} 根可信来源日线。"
        )

    returns = closes.pct_change(fill_method=None).iloc[1:]
    standard_deviations = returns.std(ddof=0)
    eligible_symbols = [
        symbol
        for symbol in complete_symbols
        if _optional_number(standard_deviations.get(symbol)) is not None
        and float(standard_deviations[symbol]) > 1e-12
    ]
    if len(eligible_symbols) < 2:
        raise SectorServiceError("板块内非恒定收益股票不足 2 只，无法计算 Pearson 相关性。")

    cumulative_returns = closes.iloc[-1] / closes.iloc[0] - 1.0
    leader_symbol = sorted(
        eligible_symbols,
        key=lambda symbol: (-float(cumulative_returns[symbol]), symbol),
    )[0]
    leader_returns = returns[leader_symbol]
    candidates: list[dict[str, Any]] = []
    for symbol in eligible_symbols:
        if symbol == leader_symbol:
            continue
        correlation = _optional_number(returns[symbol].corr(leader_returns))
        return_20d = _optional_number(cumulative_returns[symbol])
        if correlation is None or return_20d is None:
            continue
        candidates.append(
            {
                "symbol": symbol,
                "name": names.get(symbol),
                "correlation": max(-1.0, min(1.0, correlation)),
                "return_20d": return_20d,
                "observations": int(
                    (returns[symbol].notna() & leader_returns.notna()).sum()
                ),
            }
        )
    candidates.sort(key=lambda row: (-float(row["correlation"]), str(row["symbol"])))
    selected = candidates[:LEADER_LINK_LIMIT]
    if not selected:
        raise SectorServiceError("板块内没有可与龙头计算有效相关性的股票。")
    rows = [
        {
            "rank": rank,
            "symbol": str(row["symbol"]),
            "name": row["name"],
            "correlation": round(float(row["correlation"]), 6),
            "return_20d": round(float(row["return_20d"]), 8),
            "observations": int(row["observations"]),
        }
        for rank, row in enumerate(selected, 1)
    ]
    used_symbols = {leader_symbol, *(str(row["symbol"]) for row in selected)}
    sources = sorted(
        {
            str(row["source"])
            for row in records
            if str(row["symbol"]) in used_symbols and str(row["source"])
        }
    )
    refreshed_at = max(member.refreshed_at for member in members)
    return {
        "plate_code": plate_code,
        "plate_name": members[0].plate_name,
        "as_of": forecast_date.isoformat(),
        "lookback_sessions": LEADER_LOOKBACK_SESSIONS,
        "method": "pearson-daily-return",
        "source": "daily_bars",
        "sources": sources,
        "mock_excluded": True,
        "membership_scope": "fixed-current-membership",
        "membership_refreshed_at": iso_utc(refreshed_at),
        "constituent_count": len(symbols),
        "eligible_members": len(eligible_symbols),
        "leader": {
            "symbol": leader_symbol,
            "name": names.get(leader_symbol),
            "return_20d": round(float(cumulative_returns[leader_symbol]), 8),
        },
        "count": len(rows),
        "rows": rows,
    }


def _db_plate_constituents(session: Session) -> dict[str, dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(days=FRESH_CONSTITUENT_DAYS)
    latest = session.scalar(select(func.max(SectorConstituent.refreshed_at)))
    if not isinstance(latest, datetime):
        return {}
    if _utc(latest) < cutoff:
        return {}
    rows = session.scalars(
        select(SectorConstituent)
        .where(SectorConstituent.refreshed_at >= cutoff)
        .order_by(SectorConstituent.plate_code, SectorConstituent.symbol)
    ).all()
    plates: dict[str, dict[str, Any]] = {}
    for row in rows:
        info = plates.setdefault(
            row.plate_code,
            {"plate_name": row.plate_name, "constituents": []},
        )
        info["constituents"].append({"code": row.symbol, "name": row.name or row.symbol})
    return plates


def _load_plate_constituents(
    client: FutuClient,
    session: Session | None = None,
) -> dict[str, dict[str, Any]]:
    if session is not None:
        persisted = _db_plate_constituents(session)
    else:
        with get_session() as local_session:
            persisted = _db_plate_constituents(local_session)
    if persisted:
        return persisted

    with _plate_cache_lock:
        if _plate_constituents:
            return _plate_constituents
        plates = client.quote_call_raw("get_plate_list", args=["SH", "INDUSTRY"])
        if not isinstance(plates, pd.DataFrame) or plates.empty:
            raise SectorServiceError("Futu returned no industry plate list.")
        selected = plates.head(FALLBACK_MAX_PLATES)
        for record in selected.to_dict(orient="records"):
            plate_code = str(record.get("code"))
            plate_name = str(record.get("plate_name"))
            try:
                stocks = client.quote_call_raw("get_plate_stock", args=[plate_code])
            except FutuClientError:
                continue
            if not isinstance(stocks, pd.DataFrame) or stocks.empty:
                continue
            constituents = [
                {"code": str(row.get("code")), "name": str(row.get("stock_name"))}
                for row in stocks.head(MAX_CONSTITUENTS_PER_PLATE).to_dict(orient="records")
            ]
            _plate_constituents[plate_code] = {
                "plate_name": plate_name,
                "constituents": constituents,
            }
        if not _plate_constituents:
            raise SectorServiceError("No plate constituents could be loaded from Futu.")
        return _plate_constituents


def _sample_snapshot(client: FutuClient, codes: list[str]) -> pd.DataFrame:
    """Fetch snapshot batches and derive change from last/previous close."""

    unique_codes = list(dict.fromkeys(codes))
    frames: list[pd.DataFrame] = []
    for offset in range(0, len(unique_codes), SNAPSHOT_BATCH_SIZE):
        snapshot = client.quote_call_raw(
            "get_market_snapshot",
            args=[unique_codes[offset : offset + SNAPSHOT_BATCH_SIZE]],
        )
        if not isinstance(snapshot, pd.DataFrame) or snapshot.empty:
            raise SectorServiceError("Futu snapshot returned no rows for sampling.")
        frames.append(snapshot)
    if not frames:
        raise SectorServiceError("Futu snapshot returned no rows for sampling.")
    snapshot = pd.concat(frames, ignore_index=True)
    snapshot = snapshot.copy()
    if "prev_close_price" in snapshot.columns:
        last = pd.to_numeric(snapshot["last_price"], errors="coerce")
        prev_close = pd.to_numeric(snapshot["prev_close_price"], errors="coerce")
        snapshot["change_pct"] = (last / prev_close - 1) * 100
    else:
        snapshot["change_pct"] = 0.0
    return snapshot


def compute_sector_strength(
    client: FutuClient,
    session: Session | None = None,
) -> list[dict[str, Any]]:
    """Rank all cached industry plates using their 30 most-traded members."""

    plates = _load_plate_constituents(client, session)
    all_codes: list[str] = []
    for info in plates.values():
        all_codes.extend(item["code"] for item in info["constituents"])
    all_codes = list(dict.fromkeys(all_codes))

    snapshot = _sample_snapshot(client, all_codes)
    quotes = {str(row.get("code")): row for row in snapshot.to_dict(orient="records")}

    results: list[dict[str, Any]] = []
    for plate_code, info in plates.items():
        rows = [quotes[item["code"]] for item in info["constituents"] if item["code"] in quotes]
        rows.sort(key=lambda row: _number(row.get("turnover")), reverse=True)
        rows = rows[:MAX_CONSTITUENTS_PER_PLATE]
        if not rows:
            continue
        changes = [_number(row.get("change_pct")) for row in rows]
        turnovers = [_number(row.get("turnover")) for row in rows]
        up_count = sum(1 for value in changes if value > 0)
        leader = max(rows, key=lambda row: _number(row.get("change_pct")))
        avg_change = sum(changes) / len(changes)
        breadth = up_count / len(changes)
        # 0-10 heuristic strength blending move and breadth.
        strength = max(0.0, min(10.0, 5 + avg_change * 1.2 + (breadth - 0.5) * 4))
        results.append(
            {
                "plate_code": plate_code,
                "plate_name": info["plate_name"],
                "sampled": len(rows),
                "avg_change_pct": round(avg_change, 3),
                "up_ratio": round(breadth, 3),
                "turnover": sum(turnovers),
                "strength": round(strength, 2),
                "leader_code": str(leader.get("code")),
                "leader_name": str(leader.get("name") or leader.get("stock_name") or ""),
                "leader_change_pct": round(_number(leader.get("change_pct")), 3),
            }
        )
    results.sort(key=lambda item: item["strength"], reverse=True)
    for rank, item in enumerate(results, 1):
        item["rank"] = rank
    return results


def market_breadth_from_sample(client: FutuClient) -> dict[str, Any]:
    """Advance/decline breadth computed over the sector sample universe."""
    plates = _load_plate_constituents(client)
    codes: list[str] = []
    for info in plates.values():
        codes.extend(item["code"] for item in info["constituents"])
    codes = list(dict.fromkeys(codes))[:SNAPSHOT_BATCH_SIZE]
    snapshot = _sample_snapshot(client, codes)
    changes = pd.to_numeric(snapshot["change_pct"], errors="coerce").dropna()
    return {
        "sample_size": len(changes),
        "advancers": int((changes > 0).sum()),
        "decliners": int((changes < 0).sum()),
        "unchanged": int((changes == 0).sum()),
        "avg_change_pct": round(float(changes.mean()), 3),
        "note": "样本宽度：基于板块抽样股票池，非全市场统计。",
    }


def _with_latest_sector_flows(
    session: Session,
    sectors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach the latest persisted flow per plate without mutating cached JSON."""

    plate_codes = {
        str(item.get("plate_code") or "").strip()
        for item in sectors
        if isinstance(item, dict)
    }
    plate_codes.discard("")
    latest_trade_date = session.scalar(
        select(func.max(SectorFlowDaily.trade_date)).where(
            SectorFlowDaily.plate_code.in_(plate_codes),
            SectorFlowDaily.source.in_(AUDITED_SECTOR_FLOW_SOURCES),
        )
    )
    rows = (
        session.scalars(
            select(SectorFlowDaily).where(
                SectorFlowDaily.plate_code.in_(plate_codes),
                SectorFlowDaily.trade_date == latest_trade_date,
                SectorFlowDaily.source.in_(AUDITED_SECTOR_FLOW_SOURCES),
            )
        ).all()
        if latest_trade_date is not None
        else []
    )
    by_plate = {row.plate_code: row for row in rows}
    enriched: list[dict[str, Any]] = []
    for item in sectors:
        result = dict(item)
        flow = by_plate.get(str(item.get("plate_code") or ""))
        result.update(
            {
                "net_inflow": flow.net_inflow if flow is not None else None,
                "main_inflow": flow.main_inflow if flow is not None else None,
                "flow_trade_date": flow.trade_date.isoformat() if flow is not None else None,
                "flow_source": flow.source if flow is not None else None,
            }
        )
        enriched.append(result)
    return enriched


def get_sector_strength(
    session: Session,
    client: FutuClient,
    *,
    max_age_seconds: int = 150,
    refresh: bool = False,
) -> dict[str, Any]:
    latest = session.scalars(
        select(SectorSnapshot).order_by(SectorSnapshot.as_of.desc()).limit(1)
    ).first()
    now = datetime.now(UTC)
    if (
        latest is not None
        and not refresh
        and latest.as_of.replace(tzinfo=latest.as_of.tzinfo or UTC)
        > now - timedelta(seconds=max_age_seconds)
    ):
        return {
            "as_of": iso_utc(latest.as_of),
            "cached": True,
            "sectors": _with_latest_sector_flows(session, latest.payload),
        }

    try:
        sectors = compute_sector_strength(client, session)
    except (FutuClientError, SectorServiceError) as exc:
        if latest is not None:
            return {
                "as_of": iso_utc(latest.as_of),
                "cached": True,
                "stale": True,
                "error": str(exc),
                "sectors": _with_latest_sector_flows(session, latest.payload),
            }
        raise SectorServiceError(f"Sector strength unavailable: {exc}") from exc

    session.add(SectorSnapshot(as_of=now, payload=sectors, source="futu"))
    return {
        "as_of": now.isoformat(),
        "cached": False,
        "sectors": _with_latest_sector_flows(session, sectors),
    }
