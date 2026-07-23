from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import alphapilot.backtest.engine as engine_module
from alphapilot.backtest.costs import CostModel
from alphapilot.backtest.engine import (
    BacktestConfig,
    _execute_rebalance,
    _Portfolio,
    _Position,
    _prices_for_date,
    run_backtest,
)
from alphapilot.db.models import (
    AdjFactor,
    BacktestDaily,
    BacktestRun,
    Base,
    DailyBar,
    Security,
)


def _session(tmp_path: Path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'engine.db'}")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _seed(
    session: Session,
    dates: list[date],
    closes: dict[str, list[float]],
    *,
    opens: dict[str, list[float]] | None = None,
) -> None:
    for symbol in closes:
        session.add(
            Security(
                symbol=symbol,
                market="CN",
                name=symbol,
                board="主板",
                is_st=False,
                list_status="listed",
                    listed_date="2020-01-01",
                    snapshot_at=datetime.combine(
                        dates[min(1, len(dates) - 1)],
                        datetime.min.time(),
                        tzinfo=UTC,
                    ).replace(hour=1, minute=29),
            )
        )
    for symbol, close_values in closes.items():
        open_values = close_values if opens is None else opens[symbol]
        for trade_date, open_price, close in zip(
            dates,
            open_values,
            close_values,
            strict=True,
        ):
            session.add(
                DailyBar(
                    symbol=symbol,
                    trade_date=trade_date,
                    open=open_price,
                    high=max(open_price, close),
                    low=min(open_price, close),
                    close=close,
                    volume=1_000.0,
                    amount=close * 1_000.0,
                    source="baostock",
                )
            )
            session.add(
                AdjFactor(
                    symbol=symbol,
                    trade_date=trade_date,
                    adj_factor=1.0,
                    source="test",
                )
            )
    for index, trade_date in enumerate(dates):
        level = 1_000.0 * (1.02**index)
        session.add(
            DailyBar(
                symbol="SH.000300",
                trade_date=trade_date,
                open=level,
                high=level,
                low=level,
                close=level,
                volume=1_000.0,
                amount=level * 1_000.0,
                source="futu",
            )
        )
    session.commit()


def _scores(as_of: date, first_date: date) -> pd.Series:
    if as_of == first_date:
        return pd.Series({"600001": 100.0, "600002": 50.0}, name="score")
    return pd.Series({"600002": 100.0, "600001": 50.0}, name="score")


def test_walk_forward_uses_next_open_and_persists_dual_benchmarks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = [stamp.date() for stamp in pd.bdate_range("2026-07-01", periods=7)]
    session = _session(tmp_path)
    try:
        _seed(
            session,
            dates,
            {
                "600001": [9.1, 11.0, 11.0, 11.0, 11.0, 11.0, 11.0],
                "600002": [10.0] * 7,
            },
            opens={
                "600001": [9.1, 10.0, 11.0, 11.0, 11.0, 11.0, 11.0],
                "600002": [10.0] * 7,
            },
        )
        monkeypatch.setattr(
            engine_module,
            "signal_scores",
            lambda _session, as_of, _weights: _scores(as_of, dates[0]),
        )
        zero_cost = CostModel(
            commission_bps=0,
            commission_min=0,
            stamp_duty_bps=0,
            transfer_bps=0,
            slippage_bps=0,
        )

        run_id = run_backtest(
            session,
            BacktestConfig(
                start_date=dates[0],
                end_date=dates[-1],
                rebalance_freq="5d",
                top_pct=0.5,
                weights={"momentum_20d": 1.0},
                cost_model=zero_cost,
                initial_capital=100_000.0,
            ),
        )
        run = session.get(BacktestRun, run_id)
        daily = list(
            session.scalars(
                select(BacktestDaily)
                .where(BacktestDaily.run_id == run_id)
                .order_by(BacktestDaily.trade_date)
            )
        )

        assert run is not None
        assert run.status == "completed"
        assert len(daily) == len(dates)
        assert daily[0].nav == pytest.approx(1.0)
        assert daily[0].turnover is None
        assert daily[1].turnover == pytest.approx(1.0)
        assert daily[1].nav == pytest.approx(1.1)
        assert daily[1].benchmark_nav == pytest.approx(1.02)
        assert daily[1].market_nav == pytest.approx(
            1.0 + (((11.0 / 9.1) - 1.0) + 0.0) / 2
        )
        assert len(daily[1].group_returns) == 10
        assert daily[1].group_returns[0] == pytest.approx(0.0)
        assert daily[1].group_returns[-1] == pytest.approx((11.0 / 9.1) - 1.0)
        assert daily[1].ls_ret == pytest.approx((11.0 / 9.1) - 1.0)
        assert run.params["execution"] == "decision T close; fill T+1 open"
        assert run.params["weights"] == {"momentum_20d": 1.0}
        assert run.summary["rebalances"] == 2
        assert run.summary["trading_days"] == 7
    finally:
        session.close()


def test_costs_reduce_net_nav(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dates = [stamp.date() for stamp in pd.bdate_range("2026-07-01", periods=2)]
    session = _session(tmp_path)
    try:
        _seed(
            session,
            dates,
            {"600001": [10.0, 10.0], "600002": [10.0, 10.0]},
        )
        monkeypatch.setattr(
            engine_module,
            "signal_scores",
            lambda _session, as_of, _weights: _scores(as_of, dates[0]),
        )

        run_id = run_backtest(
            session,
            BacktestConfig(
                start_date=dates[0],
                end_date=dates[-1],
                top_pct=0.5,
                weights={"momentum_20d": 1.0},
                initial_capital=100_000.0,
            ),
        )
        run = session.get(BacktestRun, run_id)
        last = session.scalar(
            select(BacktestDaily)
            .where(BacktestDaily.run_id == run_id)
            .order_by(BacktestDaily.trade_date.desc())
        )

        assert run is not None
        assert last is not None
        assert run.status == "completed"
        assert run.summary["total_cost"] > 0
        assert last.nav < 1.0
        assert last.long_ret == pytest.approx(last.nav - 1.0)
    finally:
        session.close()


def test_rebalance_refuses_same_day_sale_for_t_plus_one(tmp_path: Path) -> None:
    trade_date = date(2026, 7, 2)
    session = _session(tmp_path)
    try:
        _seed(
            session,
            [trade_date],
            {"600001": [10.0], "600002": [10.0]},
        )
        prices = _prices_for_date(session, trade_date)
        portfolio = _Portfolio(
            cash=0.0,
            positions={
                "600001": _Position(
                    units=10_000.0,
                    acquired_on=trade_date,
                    last_adjusted_close=10.0,
                )
            },
        )
        scores = pd.Series({"600002": 100.0}, name="score")

        result = _execute_rebalance(
            session,
            trade_date,
            scores,
            1.0,
            CostModel(
                commission_bps=0,
                commission_min=0,
                stamp_duty_bps=0,
                transfer_bps=0,
                slippage_bps=0,
            ),
            portfolio,
            prices,
        )

        assert result.locked_sell == 1
        assert portfolio.positions["600001"].units == 10_000.0
        assert "600002" not in portfolio.positions
    finally:
        session.close()


def test_run_records_severe_no_data_failure(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        run_id = run_backtest(
            session,
            BacktestConfig(
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 2),
                weights={"momentum_20d": 1.0},
            ),
        )
        run = session.get(BacktestRun, run_id)

        assert run is not None
        assert run.status == "failed"
        assert "至少需要两个" in str(run.error)
        assert session.scalar(
            select(BacktestDaily).where(BacktestDaily.run_id == run_id)
        ) is None
    finally:
        session.close()


def test_one_execution_failure_rolls_back_day_and_later_rebalance_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = [stamp.date() for stamp in pd.bdate_range("2026-07-01", periods=7)]
    session = _session(tmp_path)
    try:
        _seed(
            session,
            dates,
            {"600001": [10.0] * 7, "600002": [10.0] * 7},
        )
        monkeypatch.setattr(
            engine_module,
            "signal_scores",
            lambda _session, as_of, _weights: _scores(as_of, dates[0]),
        )
        original = engine_module._execute_rebalance
        calls = 0

        def flaky_execution(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            if calls == 1:
                portfolio = args[5]
                assert isinstance(portfolio, _Portfolio)
                portfolio.cash = 0.0
                raise RuntimeError("synthetic one-day failure")
            return original(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(engine_module, "_execute_rebalance", flaky_execution)
        run_id = run_backtest(
            session,
            BacktestConfig(
                start_date=dates[0],
                end_date=dates[-1],
                top_pct=0.5,
                weights={"momentum_20d": 1.0},
                initial_capital=100_000.0,
            ),
        )
        run = session.get(BacktestRun, run_id)

        assert run is not None
        assert run.status == "completed"
        assert run.summary["rebalances"] == 1
        assert run.summary["day_errors"][0]["stage"] == "execution"
        assert run.summary["final_nav"] < 1.0
        assert calls == 2
    finally:
        session.close()
