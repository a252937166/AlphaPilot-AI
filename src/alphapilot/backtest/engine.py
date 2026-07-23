from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.backtest.costs import (
    CostModel,
    t_plus_one_sellable,
    tradable_at_open,
    trade_cost,
)
from alphapilot.backtest.metrics import (
    layered_returns,
    long_short,
    rank_ic,
)
from alphapilot.backtest.pit import signal_scores
from alphapilot.data.provenance import AUDITED_DAILY_BAR_SOURCES
from alphapilot.db.models import AdjFactor, BacktestDaily, BacktestRun, DailyBar
from alphapilot.engines.factors import load_weights

_SUPPORTED_REBALANCE_DAYS = frozenset({5, 10, 20})
_DEFAULT_INITIAL_CAPITAL = 1_000_000.0
_BENCHMARK_SYMBOL = "SH.000300"
_GROUP_COUNT = 10
_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Validated, serializable configuration for one deterministic M1 run."""

    start_date: date
    end_date: date
    name: str = "composite-v1 基线回测"
    signal_id: str = "composite-v1"
    rebalance_freq: str = "5d"
    top_pct: float = 0.1
    weights: Mapping[str, float] | None = None
    cost_model: CostModel = field(default_factory=CostModel)
    initial_capital: float = _DEFAULT_INITIAL_CAPITAL
    benchmark_symbol: str = _BENCHMARK_SYMBOL

    def __post_init__(self) -> None:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be earlier than start_date")
        if not self.name.strip():
            raise ValueError("name must not be empty")
        if self.signal_id != "composite-v1":
            raise ValueError("M1 only supports signal_id='composite-v1'")
        _rebalance_days(self.rebalance_freq)
        if not math.isfinite(self.top_pct) or not 0 < self.top_pct <= 1:
            raise ValueError("top_pct must be within (0, 1]")
        if (
            isinstance(self.initial_capital, bool)
            or not isinstance(self.initial_capital, (int, float))
            or not math.isfinite(float(self.initial_capital))
            or float(self.initial_capital) <= 0
        ):
            raise ValueError("initial_capital must be a positive finite number")
        if not self.benchmark_symbol.strip():
            raise ValueError("benchmark_symbol must not be empty")


@dataclass(frozen=True, slots=True)
class _Price:
    open: float
    close: float
    factor: float

    @property
    def adjusted_open(self) -> float:
        return self.open * self.factor

    @property
    def adjusted_close(self) -> float:
        return self.close * self.factor


@dataclass(slots=True)
class _Position:
    units: float
    acquired_on: date
    last_adjusted_close: float


@dataclass(slots=True)
class _Portfolio:
    cash: float
    positions: dict[str, _Position] = field(default_factory=dict)
    total_cost: float = 0.0
    total_traded: float = 0.0

    def close_value(self) -> float:
        return self.cash + sum(
            position.units * position.last_adjusted_close
            for position in self.positions.values()
        )


@dataclass(frozen=True, slots=True)
class _ExecutionResult:
    turnover: float
    traded_notional: float
    cost: float
    bought: int
    sold: int
    blocked_buy: int
    blocked_sell: int
    locked_sell: int


def _copy_portfolio(portfolio: _Portfolio) -> _Portfolio:
    return _Portfolio(
        cash=portfolio.cash,
        positions={
            symbol: _Position(
                units=position.units,
                acquired_on=position.acquired_on,
                last_adjusted_close=position.last_adjusted_close,
            )
            for symbol, position in portfolio.positions.items()
        },
        total_cost=portfolio.total_cost,
        total_traded=portfolio.total_traded,
    )


def _rebalance_days(value: str) -> int:
    normalized = str(value).strip().lower()
    if not normalized.endswith("d") or not normalized[:-1].isdigit():
        raise ValueError("rebalance_freq must be one of 5d/10d/20d")
    days = int(normalized[:-1])
    if days not in _SUPPORTED_REBALANCE_DAYS:
        raise ValueError("rebalance_freq must be one of 5d/10d/20d")
    return days


def _resolved_weights(cfg: BacktestConfig) -> tuple[dict[str, float], str]:
    if cfg.weights is None:
        loaded = load_weights()
        return dict(loaded.weights), loaded.version
    resolved: dict[str, float] = {}
    for raw_name, raw_value in cfg.weights.items():
        name = str(raw_name).strip()
        if not name:
            raise ValueError("factor weight name must not be empty")
        if (
            isinstance(raw_value, bool)
            or not isinstance(raw_value, (int, float))
            or not math.isfinite(float(raw_value))
        ):
            raise ValueError(f"factor weight must be finite: {name}")
        resolved[name] = float(raw_value)
    if not resolved or not any(value != 0.0 for value in resolved.values()):
        raise ValueError("factor weights must contain a non-zero value")
    return resolved, "inline"


def _trading_calendar(
    session: Session,
    start_date: date,
    end_date: date,
) -> list[date]:
    return list(
        session.scalars(
            select(DailyBar.trade_date)
            .where(
                DailyBar.trade_date >= start_date,
                DailyBar.trade_date <= end_date,
                DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
            )
            .distinct()
            .order_by(DailyBar.trade_date)
        )
    )


def _prices_for_date(session: Session, trade_date: date) -> dict[str, _Price]:
    rows = session.execute(
        select(
            DailyBar.symbol,
            DailyBar.open,
            DailyBar.close,
            AdjFactor.adj_factor,
        )
        .join(
            AdjFactor,
            (AdjFactor.symbol == DailyBar.symbol)
            & (AdjFactor.trade_date == DailyBar.trade_date),
        )
        .where(
            DailyBar.trade_date == trade_date,
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
            DailyBar.open > 0,
            DailyBar.close > 0,
            DailyBar.volume > 0,
            AdjFactor.adj_factor > 0,
        )
    ).all()
    result: dict[str, _Price] = {}
    for symbol, open_price, close, factor in rows:
        values = (open_price, close, factor)
        if not all(math.isfinite(float(value)) and float(value) > 0 for value in values):
            continue
        result[str(symbol)] = _Price(
            open=float(open_price),
            close=float(close),
            factor=float(factor),
        )
    return result


def _benchmark_close(
    session: Session,
    symbol: str,
    trade_date: date,
) -> float | None:
    row = session.execute(
        select(DailyBar.close, AdjFactor.adj_factor)
        .outerjoin(
            AdjFactor,
            (AdjFactor.symbol == DailyBar.symbol)
            & (AdjFactor.trade_date == DailyBar.trade_date),
        )
        .where(
            DailyBar.symbol == symbol,
            DailyBar.trade_date == trade_date,
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
            DailyBar.close > 0,
        )
        .limit(1)
    ).first()
    if row is None:
        return None
    close, factor = row
    adjustment = 1.0 if factor is None else float(factor)
    value = float(close) * adjustment
    return value if math.isfinite(value) and value > 0 else None


def _target_symbols(scores: pd.Series, top_pct: float) -> list[str]:
    clean = pd.to_numeric(scores, errors="coerce").dropna()
    clean = clean.loc[clean.map(math.isfinite)]
    if clean.empty:
        return []
    ordered = (
        clean.rename("score")
        .rename_axis("symbol")
        .reset_index()
        .assign(symbol=lambda frame: frame["symbol"].astype(str))
        .sort_values(["score", "symbol"], ascending=[False, True], kind="stable")
    )
    count = max(1, math.ceil(len(ordered) * top_pct))
    return ordered.head(count)["symbol"].tolist()


def _opening_equity(
    portfolio: _Portfolio,
    prices: Mapping[str, _Price],
) -> float:
    equity = portfolio.cash
    for symbol, position in portfolio.positions.items():
        price = prices.get(symbol)
        mark = (
            price.adjusted_open
            if price is not None
            else position.last_adjusted_close
        )
        equity += position.units * mark
    return equity


def _buy_cash_required(
    needs: Mapping[str, float],
    scale: float,
    cost_model: CostModel,
) -> float:
    return sum(
        notional * scale + trade_cost(notional * scale, "buy", cost_model)
        for notional in needs.values()
        if notional * scale > _EPSILON
    )


def _affordable_scale(
    needs: Mapping[str, float],
    cash: float,
    cost_model: CostModel,
) -> float:
    if not needs or cash <= 0:
        return 0.0
    if _buy_cash_required(needs, 1.0, cost_model) <= cash:
        return 1.0
    low = 0.0
    high = 1.0
    for _ in range(60):
        midpoint = (low + high) / 2
        if _buy_cash_required(needs, midpoint, cost_model) <= cash:
            low = midpoint
        else:
            high = midpoint
    return low


def _execute_rebalance(
    session: Session,
    trade_date: date,
    scores: pd.Series,
    top_pct: float,
    cost_model: CostModel,
    portfolio: _Portfolio,
    prices: Mapping[str, _Price],
) -> _ExecutionResult:
    opening_equity = _opening_equity(portfolio, prices)
    if opening_equity <= 0:
        raise ValueError("portfolio opening equity is not positive")
    selected = _target_symbols(scores, top_pct)
    target_weight = 1.0 / len(selected) if selected else 0.0
    target_values = {
        symbol: opening_equity * target_weight for symbol in selected
    }

    traded_notional = 0.0
    paid_cost = 0.0
    bought = 0
    sold = 0
    blocked_buy = 0
    blocked_sell = 0
    locked_sell = 0

    for symbol in sorted(tuple(portfolio.positions)):
        position = portfolio.positions[symbol]
        price = prices.get(symbol)
        if price is None:
            if target_values.get(symbol, 0.0) + _EPSILON < (
                position.units * position.last_adjusted_close
            ):
                blocked_sell += 1
            continue
        current_value = position.units * price.adjusted_open
        sell_notional = current_value - target_values.get(symbol, 0.0)
        if sell_notional <= _EPSILON:
            continue
        if not t_plus_one_sellable(position.acquired_on, trade_date):
            locked_sell += 1
            continue
        if not tradable_at_open(session, symbol, trade_date, "sell"):
            blocked_sell += 1
            continue
        quantity = min(position.units, sell_notional / price.adjusted_open)
        notional = quantity * price.adjusted_open
        cost = trade_cost(notional, "sell", cost_model)
        if notional <= cost:
            blocked_sell += 1
            continue
        position.units -= quantity
        portfolio.cash += notional - cost
        traded_notional += notional
        paid_cost += cost
        sold += 1
        if position.units <= _EPSILON:
            del portfolio.positions[symbol]

    buy_needs: dict[str, float] = {}
    for symbol in selected:
        price = prices.get(symbol)
        if price is None:
            blocked_buy += 1
            continue
        current = portfolio.positions.get(symbol)
        current_value = 0.0 if current is None else current.units * price.adjusted_open
        buy_notional = target_values[symbol] - current_value
        if buy_notional <= _EPSILON:
            continue
        if not tradable_at_open(session, symbol, trade_date, "buy"):
            blocked_buy += 1
            continue
        buy_needs[symbol] = buy_notional

    scale = _affordable_scale(buy_needs, portfolio.cash, cost_model)
    for symbol in sorted(buy_needs):
        notional = buy_needs[symbol] * scale
        if notional <= _EPSILON:
            blocked_buy += 1
            continue
        price = prices[symbol]
        cost = trade_cost(notional, "buy", cost_model)
        if notional + cost > portfolio.cash + _EPSILON:
            blocked_buy += 1
            continue
        quantity = notional / price.adjusted_open
        buy_position = portfolio.positions.get(symbol)
        if buy_position is None:
            portfolio.positions[symbol] = _Position(
                units=quantity,
                acquired_on=trade_date,
                last_adjusted_close=price.adjusted_open,
            )
        else:
            buy_position.units += quantity
            buy_position.acquired_on = trade_date
        portfolio.cash -= notional + cost
        traded_notional += notional
        paid_cost += cost
        bought += 1

    portfolio.total_traded += traded_notional
    portfolio.total_cost += paid_cost
    return _ExecutionResult(
        turnover=traded_notional / opening_equity,
        traded_notional=traded_notional,
        cost=paid_cost,
        bought=bought,
        sold=sold,
        blocked_buy=blocked_buy,
        blocked_sell=blocked_sell,
        locked_sell=locked_sell,
    )


def _mark_to_close(
    portfolio: _Portfolio,
    prices: Mapping[str, _Price],
) -> int:
    missing = 0
    for symbol, position in portfolio.positions.items():
        price = prices.get(symbol)
        if price is None:
            missing += 1
            continue
        position.last_adjusted_close = price.adjusted_close
    return missing


def _cross_section_returns(
    previous: Mapping[str, _Price],
    current: Mapping[str, _Price],
    symbols: Sequence[str] | None = None,
) -> pd.Series:
    requested = (
        set(previous).intersection(current)
        if symbols is None
        else {str(symbol) for symbol in symbols}
    )
    values: dict[str, float] = {}
    for symbol in requested:
        before = previous.get(symbol)
        after = current.get(symbol)
        if before is None or after is None or before.adjusted_close <= 0:
            continue
        value = after.adjusted_close / before.adjusted_close - 1.0
        if math.isfinite(value):
            values[symbol] = value
    return pd.Series(values, dtype=float, name="return")


def _signal_diagnostics(
    scores: pd.Series | None,
    realized: pd.Series,
) -> tuple[float | None, list[float | None], float | None]:
    if scores is None or scores.empty or realized.empty:
        return None, [], None
    score_values = pd.to_numeric(scores, errors="coerce")
    value = rank_ic(score_values, realized)
    groups = layered_returns(score_values, realized, n=_GROUP_COUNT)
    if all(not math.isfinite(group) for group in groups):
        return None, [], None
    stored_groups = [
        group if math.isfinite(group) else None for group in groups
    ]
    spread = long_short(groups)
    return (
        value if math.isfinite(value) else None,
        stored_groups,
        spread if math.isfinite(spread) else None,
    )


def _params_snapshot(
    cfg: BacktestConfig,
    weights: Mapping[str, float],
    weight_version: str,
) -> dict[str, Any]:
    return {
        "signal_id": cfg.signal_id,
        "start_date": cfg.start_date.isoformat(),
        "end_date": cfg.end_date.isoformat(),
        "rebalance_freq": cfg.rebalance_freq,
        "top_pct": cfg.top_pct,
        "weights": dict(weights),
        "weight_version": weight_version,
        "cost_model": asdict(cfg.cost_model),
        "initial_capital": float(cfg.initial_capital),
        "benchmark_symbol": cfg.benchmark_symbol,
        "market_benchmark": "audited adjusted equal-weight current-survivor universe",
        "execution": "decision T close; fill T+1 open",
        "survivorship_bias": True,
        "random_seed": None,
    }


def _create_backtest_run(
    session: Session,
    cfg: BacktestConfig,
    weights: Mapping[str, float],
    weight_version: str,
) -> int:
    run = BacktestRun(
        name=cfg.name.strip(),
        signal_id=cfg.signal_id,
        start_date=cfg.start_date,
        end_date=cfg.end_date,
        rebalance_freq=cfg.rebalance_freq,
        top_pct=cfg.top_pct,
        params=_params_snapshot(cfg, weights, weight_version),
        status="running",
        summary={},
    )
    session.add(run)
    session.commit()
    return int(run.id)


def create_backtest_run(session: Session, cfg: BacktestConfig) -> int:
    """Persist a pollable ``running`` row before asynchronous execution."""

    weights, weight_version = _resolved_weights(cfg)
    return _create_backtest_run(
        session,
        cfg,
        weights,
        weight_version,
    )


def run_backtest(
    session: Session,
    cfg: BacktestConfig,
    *,
    run_id: int | None = None,
) -> int:
    """Run a deterministic PIT walk-forward simulation and return its run id."""

    weights, weight_version = _resolved_weights(cfg)
    expected_params = _params_snapshot(cfg, weights, weight_version)
    if run_id is None:
        run_id = _create_backtest_run(
            session,
            cfg,
            weights,
            weight_version,
        )
    else:
        queued = session.get(BacktestRun, run_id)
        if queued is None:
            raise ValueError(f"backtest run not found: {run_id}")
        if queued.status != "running":
            raise ValueError(
                f"backtest run is not queued: id={run_id}, status={queued.status}"
            )
        if queued.params != expected_params:
            raise ValueError(f"backtest run parameters do not match: {run_id}")

    try:
        calendar = _trading_calendar(session, cfg.start_date, cfg.end_date)
        if len(calendar) < 2:
            raise ValueError("回测区间内至少需要两个审计交易日。")

        interval = _rebalance_days(cfg.rebalance_freq)
        portfolio = _Portfolio(cash=float(cfg.initial_capital))
        previous_prices: dict[str, _Price] = {}
        previous_benchmark: float | None = None
        previous_equity = float(cfg.initial_capital)
        benchmark_nav = 1.0
        market_nav = 1.0
        active_scores: pd.Series | None = None
        pending_scores: dict[date, pd.Series] = {}
        daily_rows: list[BacktestDaily] = []
        day_errors: list[dict[str, str]] = []
        execution_stats = {
            "rebalances": 0,
            "bought": 0,
            "sold": 0,
            "blocked_buy": 0,
            "blocked_sell": 0,
            "locked_sell": 0,
            "missing_holding_marks": 0,
            "missing_benchmark_days": 0,
        }

        for day_index, trade_date in enumerate(calendar):
            prices = _prices_for_date(session, trade_date)
            turnover: float | None = None
            execution = pending_scores.pop(trade_date, None)
            if execution is not None:
                active_scores = execution
                before_execution = _copy_portfolio(portfolio)
                try:
                    result = _execute_rebalance(
                        session,
                        trade_date,
                        execution,
                        cfg.top_pct,
                        cfg.cost_model,
                        portfolio,
                        prices,
                    )
                    turnover = result.turnover
                    execution_stats["rebalances"] += 1
                    for key in (
                        "bought",
                        "sold",
                        "blocked_buy",
                        "blocked_sell",
                        "locked_sell",
                    ):
                        execution_stats[key] += int(getattr(result, key))
                except Exception as exc:
                    portfolio = before_execution
                    day_errors.append(
                        {
                            "trade_date": trade_date.isoformat(),
                            "stage": "execution",
                            "error": f"{type(exc).__name__}: {exc}"[:500],
                        }
                    )

            execution_stats["missing_holding_marks"] += _mark_to_close(
                portfolio,
                prices,
            )
            equity = portfolio.close_value()
            long_return = (
                equity / previous_equity - 1.0
                if previous_equity > 0
                else None
            )
            nav = equity / float(cfg.initial_capital)

            benchmark_close = _benchmark_close(
                session,
                cfg.benchmark_symbol,
                trade_date,
            )
            if benchmark_close is not None and previous_benchmark is not None:
                benchmark_return = benchmark_close / previous_benchmark - 1.0
                if math.isfinite(benchmark_return):
                    benchmark_nav *= 1.0 + benchmark_return
            elif day_index > 0:
                execution_stats["missing_benchmark_days"] += 1
            if benchmark_close is not None:
                previous_benchmark = benchmark_close

            realized = _cross_section_returns(previous_prices, prices)
            if not realized.empty:
                market_return = float(realized.mean())
                if math.isfinite(market_return):
                    market_nav *= 1.0 + market_return
            rank_ic, group_returns, long_short = _signal_diagnostics(
                active_scores,
                realized,
            )

            daily_rows.append(
                BacktestDaily(
                    run_id=run_id,
                    trade_date=trade_date,
                    rank_ic=rank_ic,
                    long_ret=long_return,
                    ls_ret=long_short,
                    turnover=turnover,
                    nav=nav,
                    benchmark_nav=benchmark_nav,
                    market_nav=market_nav,
                    n_eligible=len(prices),
                    group_returns=group_returns,
                )
            )

            if day_index % interval == 0 and day_index + 1 < len(calendar):
                try:
                    decision_scores = signal_scores(session, trade_date, weights)
                    pending_scores[calendar[day_index + 1]] = decision_scores.copy()
                except Exception as exc:  # keep later rebalance dates usable
                    day_errors.append(
                        {
                            "trade_date": trade_date.isoformat(),
                            "stage": "signal",
                            "error": f"{type(exc).__name__}: {exc}"[:500],
                        }
                    )

            previous_prices = prices
            previous_equity = equity

        if not any(row.turnover is not None for row in daily_rows):
            raise ValueError("回测区间没有形成任何可执行的 T+1 调仓。")

        stored_run = session.get(BacktestRun, run_id)
        if stored_run is None:
            raise RuntimeError(f"backtest run disappeared: {run_id}")
        stored_run.status = "completed"
        stored_run.error = None
        stored_run.summary = {
            "trading_days": len(calendar),
            "first_trade_date": calendar[0].isoformat(),
            "last_trade_date": calendar[-1].isoformat(),
            "final_nav": daily_rows[-1].nav,
            "benchmark_nav": daily_rows[-1].benchmark_nav,
            "market_nav": daily_rows[-1].market_nav,
            "total_cost": portfolio.total_cost,
            "total_traded": portfolio.total_traded,
            "cost_to_initial_capital": portfolio.total_cost
            / float(cfg.initial_capital),
            "day_errors": day_errors,
            **execution_stats,
        }
        session.add_all(daily_rows)
        session.commit()
        return run_id
    except Exception as exc:
        session.rollback()
        failed = session.get(BacktestRun, run_id)
        if failed is not None:
            failed.status = "failed"
            failed.error = f"{type(exc).__name__}: {exc}"[:2000]
            failed.summary = {
                "failure_stage": "engine",
                "error": failed.error,
            }
            session.commit()
        return run_id
