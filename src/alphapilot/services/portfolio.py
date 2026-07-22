from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import date, datetime
from math import isfinite
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.db.models import DailyBar, PortfolioSnapshot, Security, utcnow

BENCHMARK_SYMBOL = "SH.000300"
UNKNOWN_INDUSTRY = "未分类"


class PortfolioServiceError(RuntimeError):
    """Persisted or broker portfolio data cannot be used without fabrication."""


def _number(
    value: object,
    field: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> float:
    if isinstance(value, bool):
        raise PortfolioServiceError(f"组合字段 {field} 不是有效数值。")
    try:
        number = float(str(value))
    except (TypeError, ValueError) as exc:
        raise PortfolioServiceError(f"组合字段 {field} 不是有效数值。") from exc
    if not isfinite(number):
        raise PortfolioServiceError(f"组合字段 {field} 不是有限数值。")
    if positive and number <= 0:
        raise PortfolioServiceError(f"组合字段 {field} 必须大于 0。")
    if nonnegative and number < 0:
        raise PortfolioServiceError(f"组合字段 {field} 不能为负数。")
    return number


def _symbol(value: object) -> str:
    symbol = str(value or "").strip()
    if len(symbol) != 6 or not symbol.isdigit():
        raise PortfolioServiceError("模拟持仓包含无效股票代码。")
    return symbol


def _position_industries(session: Session, symbols: list[str]) -> dict[str, str]:
    if not symbols:
        return {}
    securities = session.scalars(select(Security).where(Security.symbol.in_(symbols))).all()
    return {
        row.symbol: row.industry_csrc.strip()
        if row.industry_csrc and row.industry_csrc.strip()
        else UNKNOWN_INDUSTRY
        for row in securities
    }


def _snapshot_positions(
    session: Session,
    positions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    validated: list[tuple[str, float, float]] = []
    for position in positions:
        if not isinstance(position, Mapping):
            raise PortfolioServiceError("模拟持仓记录格式异常。")
        validated.append(
            (
                _symbol(position.get("symbol")),
                _number(position.get("qty"), "qty", nonnegative=True),
                _number(position.get("market_val"), "market_val", nonnegative=True),
            )
        )

    industries = _position_industries(session, sorted({item[0] for item in validated}))
    aggregated: dict[str, dict[str, Any]] = {}
    for symbol, quantity, market_value in validated:
        existing = aggregated.get(symbol)
        if existing is None:
            aggregated[symbol] = {
                "symbol": symbol,
                "qty": quantity,
                "mv": market_value,
                "industry": industries.get(symbol, UNKNOWN_INDUSTRY),
            }
        else:
            existing["qty"] = float(existing["qty"]) + quantity
            existing["mv"] = float(existing["mv"]) + market_value

    rows = [aggregated[symbol] for symbol in sorted(aggregated)]
    missing_industry_count = sum(1 for position in rows if position["industry"] == UNKNOWN_INDUSTRY)
    return rows, missing_industry_count


def _benchmark_closes(session: Session) -> dict[date, float]:
    rows = session.scalars(
        select(DailyBar).where(DailyBar.symbol == BENCHMARK_SYMBOL).order_by(DailyBar.trade_date)
    ).all()
    return {row.trade_date: _number(row.close, "benchmark_close", positive=True) for row in rows}


def _optional_metric(
    value: object,
    field: str,
    *,
    lower_exclusive: float | None = None,
    lower_inclusive: float | None = None,
    upper_inclusive: float | None = None,
) -> float | None:
    if value is None:
        return None
    number = _number(value, field)
    if lower_exclusive is not None and number <= lower_exclusive:
        raise PortfolioServiceError(f"组合快照字段 {field} 超出合法范围。")
    if lower_inclusive is not None and number < lower_inclusive:
        raise PortfolioServiceError(f"组合快照字段 {field} 超出合法范围。")
    if upper_inclusive is not None and number > upper_inclusive:
        raise PortfolioServiceError(f"组合快照字段 {field} 超出合法范围。")
    return number


def recompute_portfolio_metrics(session: Session) -> dict[str, int]:
    """Recompute all dependent metrics after an idempotent current-day upsert."""

    session.flush()
    snapshots = session.scalars(
        select(PortfolioSnapshot).order_by(PortfolioSnapshot.trade_date)
    ).all()
    closes = _benchmark_closes(session)
    previous: PortfolioSnapshot | None = None
    peak = 0.0
    benchmark_ready = 0
    for snapshot in snapshots:
        total_value = _number(snapshot.total_value, "total_value", positive=True)
        peak = max(peak, total_value)
        snapshot.drawdown = total_value / peak - 1.0
        snapshot.daily_return = None
        snapshot.benchmark_return = None
        snapshot.excess_return = None
        if previous is not None:
            previous_value = _number(previous.total_value, "previous_total_value", positive=True)
            snapshot.daily_return = total_value / previous_value - 1.0
            current_close = closes.get(snapshot.trade_date)
            previous_close = closes.get(previous.trade_date)
            if current_close is not None and previous_close is not None:
                snapshot.benchmark_return = current_close / previous_close - 1.0
                snapshot.excess_return = snapshot.daily_return - snapshot.benchmark_return
                benchmark_ready += 1
        previous = snapshot
    return {"snapshots": len(snapshots), "benchmark_ready": benchmark_ready}


def upsert_portfolio_snapshot(
    session: Session,
    trade_date: date,
    funds: Mapping[str, Any],
    positions: list[dict[str, Any]],
) -> tuple[PortfolioSnapshot, dict[str, Any]]:
    """Persist one real SIMULATE valuation and atomically refresh derived metrics."""

    total_value = _number(funds.get("total_assets"), "total_assets", positive=True)
    cash = _number(funds.get("cash"), "cash", nonnegative=True)
    broker_market_value = _number(
        funds.get("market_val"),
        "market_val",
        nonnegative=True,
    )
    if cash > total_value:
        raise PortfolioServiceError("组合现金超过总资产，已拒绝写入不一致快照。")
    account_value_gap = cash + broker_market_value - total_value
    account_gap_tolerance = max(1.0, broker_market_value * 0.001)
    if abs(account_value_gap) > account_gap_tolerance:
        raise PortfolioServiceError("账户总资产不等于现金与持仓市值之和，已拒绝写入快照。")
    snapshot_positions, missing_industry_count = _snapshot_positions(session, positions)
    positions_market_value = sum(float(position["mv"]) for position in snapshot_positions)
    market_value_gap = positions_market_value - broker_market_value
    material_gap = abs(market_value_gap) > max(1.0, broker_market_value * 0.001)
    if material_gap:
        raise PortfolioServiceError("账户市值与持仓明细汇总不一致，已拒绝写入快照。")
    existing = session.get(PortfolioSnapshot, trade_date)
    inserted = existing is None
    snapshot = existing or PortfolioSnapshot(trade_date=trade_date)
    if inserted:
        session.add(snapshot)
    snapshot.total_value = total_value
    snapshot.cash = cash
    snapshot.positions = snapshot_positions
    snapshot.source = "futu-sim"
    metric_stats = recompute_portfolio_metrics(session)
    return snapshot, {
        "inserted": int(inserted),
        "updated": int(not inserted),
        "positions": len(snapshot_positions),
        "missing_industry_count": missing_industry_count,
        "positions_market_value": positions_market_value,
        "broker_market_value": broker_market_value,
        "market_value_gap": market_value_gap,
        "market_value_gap_warning": material_gap,
        **metric_stats,
    }


def upsert_benchmark_close_bar(
    session: Session,
    trade_date: date,
    record: Mapping[str, Any],
    *,
    source: str,
) -> str:
    """Insert or correct only the audited close bar used by portfolio attribution."""

    raw_date = record.get("date")
    if isinstance(raw_date, datetime):
        record_date = raw_date.date()
    elif isinstance(raw_date, date):
        record_date = raw_date
    else:
        try:
            record_date = date.fromisoformat(str(raw_date)[:10])
        except ValueError as exc:
            raise PortfolioServiceError("沪深300收盘日期格式异常。") from exc
    if record_date != trade_date:
        raise PortfolioServiceError("沪深300收盘日期与组合快照日期不一致。")

    open_price = _number(record.get("open"), "benchmark_open", positive=True)
    high = _number(record.get("high"), "benchmark_high", positive=True)
    low = _number(record.get("low"), "benchmark_low", positive=True)
    close = _number(record.get("close"), "benchmark_close", positive=True)
    volume = _number(record.get("volume"), "benchmark_volume", nonnegative=True)
    amount = _number(record.get("amount"), "benchmark_amount", nonnegative=True)
    if low > min(open_price, close) or high < max(open_price, close) or low > high:
        raise PortfolioServiceError("沪深300收盘 OHLC 关系异常。")

    row = session.scalar(
        select(DailyBar).where(
            DailyBar.symbol == BENCHMARK_SYMBOL,
            DailyBar.trade_date == trade_date,
        )
    )
    action = "updated"
    if row is None:
        row = DailyBar(symbol=BENCHMARK_SYMBOL, trade_date=trade_date)
        session.add(row)
        action = "inserted"
    row.open = open_price
    row.high = high
    row.low = low
    row.close = close
    row.volume = volume
    row.amount = amount
    row.source = source
    row.ingested_at = utcnow()
    return action


def _stored_positions(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise PortfolioServiceError("组合快照持仓格式异常。")
    positions: list[dict[str, Any]] = []
    for raw in payload:
        if not isinstance(raw, Mapping):
            raise PortfolioServiceError("组合快照持仓记录格式异常。")
        industry = str(raw.get("industry") or "").strip() or UNKNOWN_INDUSTRY
        positions.append(
            {
                "symbol": _symbol(raw.get("symbol")),
                "qty": _number(raw.get("qty"), "qty", nonnegative=True),
                "mv": _number(raw.get("mv"), "mv", nonnegative=True),
                "industry": industry,
            }
        )
    positions.sort(key=lambda item: str(item["symbol"]))
    return positions


def get_portfolio_overview(session: Session) -> dict[str, Any]:
    snapshot = session.scalars(
        select(PortfolioSnapshot).order_by(PortfolioSnapshot.trade_date.desc()).limit(1)
    ).first()
    if snapshot is None:
        return {
            "available": False,
            "snapshot": None,
            "market_value": 0.0,
            "account_market_value": 0.0,
            "market_value_gap": 0.0,
            "industry_distribution": [],
            "missing_industry_count": 0,
            "warning": "尚无真实模拟组合快照，请等待收盘快照任务。",
        }

    positions = _stored_positions(snapshot.positions)
    by_industry: defaultdict[str, float] = defaultdict(float)
    for position in positions:
        by_industry[str(position["industry"])] += float(position["mv"])
    market_value = sum(by_industry.values())
    distribution = [
        {
            "industry": industry,
            "market_value": value,
            "weight": value / market_value if market_value > 0 else 0.0,
        }
        for industry, value in sorted(
            by_industry.items(),
            key=lambda item: (-item[1], item[0]),
        )
        if value > 0
    ]
    total_value = _number(snapshot.total_value, "total_value", positive=True)
    cash = _number(snapshot.cash, "cash", nonnegative=True)
    if cash > total_value:
        raise PortfolioServiceError("组合快照现金超过总资产。")
    implied_market_value = total_value - cash
    market_value_gap = market_value - implied_market_value
    material_gap = abs(market_value_gap) > max(1.0, implied_market_value * 0.001)
    missing_industry_count = sum(
        1 for position in positions if position["industry"] == UNKNOWN_INDUSTRY
    )
    warnings: list[str] = []
    if material_gap:
        warnings.append("账户权益隐含市值与持仓明细汇总不一致，展示保留真实原值。")
    if missing_industry_count:
        warnings.append(f"{missing_industry_count} 个持仓缺少证监会行业，已归入未分类。")
    if snapshot.source != "futu-sim":
        raise PortfolioServiceError("组合快照数据源不是 futu-sim。")
    return {
        "available": True,
        "snapshot": {
            "trade_date": snapshot.trade_date.isoformat(),
            "total_value": total_value,
            "cash": cash,
            "positions": positions,
            "daily_return": _optional_metric(
                snapshot.daily_return,
                "daily_return",
                lower_exclusive=-1.0,
            ),
            "benchmark_return": _optional_metric(
                snapshot.benchmark_return,
                "benchmark_return",
                lower_exclusive=-1.0,
            ),
            "excess_return": _optional_metric(snapshot.excess_return, "excess_return"),
            "drawdown": _optional_metric(
                snapshot.drawdown,
                "drawdown",
                lower_inclusive=-1.0,
                upper_inclusive=0.0,
            ),
            "source": snapshot.source,
        },
        "market_value": market_value,
        "account_market_value": implied_market_value,
        "market_value_gap": market_value_gap,
        "industry_distribution": distribution,
        "missing_industry_count": missing_industry_count,
        "warning": " ".join(warnings) if warnings else None,
    }


def _max_drawdown(series: list[float]) -> float | None:
    if not series:
        return None
    peak = series[0]
    worst = 0.0
    for value in series:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1.0)
    return worst


def get_portfolio_attribution(session: Session, days: int) -> dict[str, Any]:
    if days < 1 or days > 365:
        raise PortfolioServiceError("归因天数必须在 1 到 365 之间。")
    newest_first = session.scalars(
        select(PortfolioSnapshot).order_by(PortfolioSnapshot.trade_date.desc()).limit(days)
    ).all()
    snapshots = list(reversed(newest_first))
    if not snapshots:
        return {
            "available": False,
            "requested_days": days,
            "available_days": 0,
            "dates": [],
            "nav": [],
            "benchmark_nav": [],
            "excess_cum": None,
            "max_drawdown": None,
            "benchmark_drawdown": None,
            "benchmark_symbol": BENCHMARK_SYMBOL,
            "warning": "尚无真实模拟组合快照，无法计算收益归因。",
        }

    dates = [snapshot.trade_date for snapshot in snapshots]
    values = [_number(snapshot.total_value, "total_value", positive=True) for snapshot in snapshots]
    base_value = values[0]
    nav = [value / base_value for value in values]
    benchmark_rows = session.scalars(
        select(DailyBar).where(
            DailyBar.symbol == BENCHMARK_SYMBOL,
            DailyBar.trade_date.in_(dates),
        )
    ).all()
    benchmark_closes = {
        row.trade_date: _number(row.close, "benchmark_close", positive=True)
        for row in benchmark_rows
    }
    base_benchmark = benchmark_closes.get(dates[0])
    benchmark_nav: list[float | None] = []
    for trade_date in dates:
        close = benchmark_closes.get(trade_date)
        benchmark_nav.append(
            close / base_benchmark if base_benchmark is not None and close is not None else None
        )
    missing_dates = [
        trade_date.isoformat()
        for trade_date, value in zip(dates, benchmark_nav, strict=True)
        if value is None
    ]
    latest_benchmark_nav = benchmark_nav[-1]
    excess_cum = (
        nav[-1] / latest_benchmark_nav - 1.0
        if len(nav) >= 2 and latest_benchmark_nav is not None
        else None
    )
    valid_benchmark_nav = [value for value in benchmark_nav if value is not None]
    warning: str | None = None
    if len(snapshots) < days:
        warning = f"当前仅累积 {len(snapshots)} 个真实交易日快照。"
    if missing_dates:
        suffix = "、".join(missing_dates[:5])
        benchmark_warning = f"沪深300收盘数据缺失：{suffix}；缺口保留为 null。"
        warning = f"{warning} {benchmark_warning}" if warning else benchmark_warning
    return {
        "available": True,
        "requested_days": days,
        "available_days": len(snapshots),
        "dates": [trade_date.isoformat() for trade_date in dates],
        "nav": nav,
        "benchmark_nav": benchmark_nav,
        "excess_cum": excess_cum,
        "max_drawdown": _max_drawdown(nav),
        "benchmark_drawdown": None if missing_dates else _max_drawdown(valid_benchmark_nav),
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "warning": warning,
    }
