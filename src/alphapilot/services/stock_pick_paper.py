"""Paper trading of the weekly stock-pick lists: explicit actions, positions and P&L.

Each candidate runs its own simulated account; nothing is sent to a broker. On the entry
session of a new list (the first session after its as-of date) the account sells, at the
open, every holding that is not in the list's top N, keeps the holdings that are, and buys
the new names with equal slices of the account's value at that open, rounded to board
lots. Costs: commission 0.025% with a 5 CNY minimum, transfer fee 0.001%, stamp duty
0.05% on sells, and a fixed slippage of 0.05% per side. A name whose open is at the upper
limit is not bought; a holding that cannot trade (no bar, or a one-price limit-down open)
is carried and retried at the next session's open. Holdings are marked at each close.
Beijing Stock Exchange names are skipped because the owner's account does not buy them
(decided 2026-09-26): the list is walked in rank order until N other names are found.

This view is derived from the frozen lists and daily bars. It does not feed the
forward-test verdict, which scores the top decile of each list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from alphapilot.services.stock_picks import limit_pct

INDEX_SYMBOL = "SH.000001"
SKIP_BEIJING = True  # owner, 2026-09-26: the account does not buy Beijing-exchange stocks


def is_beijing(symbol: str) -> bool:
    """Beijing Stock Exchange codes: 4xxxxx and 8xxxxx (moved from NEEQ) and 92xxxx."""

    return symbol.startswith(("4", "8", "92"))


def targets(
    members: list[dict[str, Any]], top_n: int, *, skip_beijing: bool = SKIP_BEIJING
) -> list[str]:
    """The first ``top_n`` names of a ranked list that the account may buy."""

    picked: list[str] = []
    for member in members:
        symbol = member["symbol"]
        if skip_beijing and is_beijing(symbol):
            continue
        picked.append(symbol)
        if len(picked) == top_n:
            break
    return picked


@dataclass(frozen=True, slots=True)
class Costs:
    commission: float = 0.00025
    commission_min: float = 5.0
    transfer: float = 0.00001
    stamp_sell: float = 0.0005
    slippage: float = 0.0005

    def buy_fee(self, amount: float) -> float:
        return max(self.commission_min, amount * self.commission) + amount * self.transfer

    def sell_fee(self, amount: float) -> float:
        return (
            max(self.commission_min, amount * self.commission)
            + amount * self.transfer
            + amount * self.stamp_sell
        )


@dataclass(slots=True)
class Bars:
    """Wide open/high/low/close frames indexed by session date, columns = symbols."""

    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame

    @property
    def sessions(self) -> list[date]:
        return list(self.close.index)

    def value(self, frame: pd.DataFrame, day: date, symbol: str) -> float | None:
        if symbol not in frame.columns or day not in frame.index:
            return None
        raw = frame.at[day, symbol]
        return None if pd.isna(raw) else float(raw)

    def last_close(self, day: date, symbol: str) -> float | None:
        if symbol not in self.close.columns:
            return None
        series = self.close.loc[:day, symbol].dropna()
        return None if series.empty else float(series.iloc[-1])


def lot_shares(symbol: str, budget: float, price: float) -> int:
    """Largest tradable quantity within the budget under the board's lot rules."""

    if price <= 0 or budget <= 0:
        return 0
    raw = int(budget // price)
    if symbol.startswith("688"):  # STAR: at least 200 shares, then single shares
        return raw if raw >= 200 else 0
    if is_beijing(symbol):  # Beijing: at least 100, then single shares
        return raw if raw >= 100 else 0
    return (raw // 100) * 100


@dataclass(slots=True)
class Holding:
    shares: int
    cost: float  # cash paid including fees
    since: date


@dataclass(slots=True)
class Account:
    cash: float
    holdings: dict[str, Holding] = field(default_factory=dict)
    trades: list[dict[str, Any]] = field(default_factory=list)
    curve: list[tuple[date, float]] = field(default_factory=list)
    pending_sells: set[str] = field(default_factory=set)
    skipped: list[dict[str, Any]] = field(default_factory=list)


def _prev_close(bars: Bars, day: date, symbol: str, fallback: float | None) -> float | None:
    sessions = bars.sessions
    index = sessions.index(day)
    if index > 0:
        previous = bars.value(bars.close, sessions[index - 1], symbol)
        if previous is not None:
            return previous
    return fallback


def _limit_up_open(bars: Bars, day: date, symbol: str, prev_close: float | None) -> bool:
    open_ = bars.value(bars.open, day, symbol)
    low = bars.value(bars.low, day, symbol)
    if open_ is None or low is None or prev_close is None:
        return False
    cap = round(prev_close * (1 + limit_pct(symbol)), 2) - 0.005
    return open_ >= cap and low >= open_ - 1e-6


def _one_price_limit_down(bars: Bars, day: date, symbol: str) -> bool:
    open_ = bars.value(bars.open, day, symbol)
    high = bars.value(bars.high, day, symbol)
    prev_close = _prev_close(bars, day, symbol, None)
    if open_ is None or high is None or prev_close is None:
        return False
    floor = round(prev_close * (1 - limit_pct(symbol)), 2) + 0.005
    return open_ <= floor and high <= open_ + 1e-6


def entry_session(sessions: list[date], as_of: date) -> date | None:
    later = [d for d in sessions if d > as_of]
    return later[0] if later else None


def _sell(account: Account, bars: Bars, day: date, symbol: str, costs: Costs) -> bool:
    open_ = bars.value(bars.open, day, symbol)
    if open_ is None or _one_price_limit_down(bars, day, symbol):
        return False
    holding = account.holdings.pop(symbol)
    price = open_ * (1 - costs.slippage)
    amount = holding.shares * price
    fee = costs.sell_fee(amount)
    account.cash += amount - fee
    account.trades.append(
        {
            "date": day.isoformat(),
            "side": "sell",
            "symbol": symbol,
            "shares": holding.shares,
            "price": round(price, 4),
            "fee": round(fee, 2),
            "pnl": round(amount - fee - holding.cost, 2),
            "pnl_pct": round((amount - fee) / holding.cost - 1, 6) if holding.cost else None,
        }
    )
    return True


def simulate(
    lists: list[dict[str, Any]],
    bars: Bars,
    *,
    capital: float,
    top_n: int,
    costs: Costs | None = None,
    skip_beijing: bool = SKIP_BEIJING,
) -> dict[str, Any]:
    """Run one candidate's account over its lists; returns the ledger and the next plan."""

    costs = costs or Costs()
    sessions = bars.sessions
    by_entry: dict[date, dict[str, Any]] = {}
    future: dict[str, Any] | None = None
    for doc in sorted(lists, key=lambda d: d["as_of"]):
        entry = entry_session(sessions, date.fromisoformat(doc["as_of"]))
        if entry is None:
            future = doc
        else:
            by_entry[entry] = doc
    account = Account(cash=float(capital))
    if not by_entry:
        return {
            "account": account,
            "plan": _plan(account, bars, future, None, top_n, costs, skip_beijing),
        }
    first = min(by_entry)
    current: dict[str, Any] | None = None
    for day in [d for d in sessions if d >= first]:
        doc = by_entry.get(day)
        target: list[str] = []
        if doc is not None:
            current = doc
            target = targets(doc["members"], top_n, skip_beijing=skip_beijing)
            for symbol in list(account.holdings):
                if symbol not in target:
                    account.pending_sells.add(symbol)
            account.pending_sells -= set(target)
        for symbol in sorted(account.pending_sells):
            if symbol not in account.holdings or _sell(account, bars, day, symbol, costs):
                account.pending_sells.discard(symbol)
        if doc is not None:
            _rotate_in(account, bars, day, doc, target, costs, top_n)
        value = account.cash + sum(
            h.shares * (bars.last_close(day, s) or 0.0) for s, h in account.holdings.items()
        )
        account.curve.append((day, value))
    return {
        "account": account,
        "current_list": current,
        "plan": _plan(account, bars, future, current, top_n, costs, skip_beijing),
    }


def _rotate_in(
    account: Account,
    bars: Bars,
    day: date,
    doc: dict[str, Any],
    target: list[str],
    costs: Costs,
    top_n: int,
) -> None:
    closes = {m["symbol"]: m["close"] for m in doc["members"]}
    equity = account.cash + sum(
        h.shares * (bars.value(bars.open, day, s) or bars.last_close(day, s) or 0.0)
        for s, h in account.holdings.items()
    )
    slice_ = equity / top_n
    for symbol in target:
        if symbol in account.holdings:
            continue
        open_ = bars.value(bars.open, day, symbol)
        if open_ is None:
            account.skipped.append({"date": day.isoformat(), "symbol": symbol, "reason": "no bar"})
            continue
        prev_close = _prev_close(bars, day, symbol, closes.get(symbol))
        if _limit_up_open(bars, day, symbol, prev_close):
            account.skipped.append(
                {"date": day.isoformat(), "symbol": symbol, "reason": "limit-up open"}
            )
            continue
        price = open_ * (1 + costs.slippage)
        budget = min(slice_, account.cash) - costs.commission_min
        shares = lot_shares(symbol, budget / (1 + costs.commission + costs.transfer), price)
        if shares <= 0:
            account.skipped.append(
                {"date": day.isoformat(), "symbol": symbol, "reason": "below one lot"}
            )
            continue
        amount = shares * price
        fee = costs.buy_fee(amount)
        account.cash -= amount + fee
        account.holdings[symbol] = Holding(shares=shares, cost=amount + fee, since=day)
        account.trades.append(
            {
                "date": day.isoformat(),
                "side": "buy",
                "symbol": symbol,
                "shares": shares,
                "price": round(price, 4),
                "fee": round(fee, 2),
            }
        )


def _plan(
    account: Account,
    bars: Bars,
    future: dict[str, Any] | None,
    current: dict[str, Any] | None,
    top_n: int,
    costs: Costs,
    skip_beijing: bool = SKIP_BEIJING,
) -> dict[str, Any] | None:
    """Orders for the next open when a list exists whose entry session has not traded yet."""

    if future is None:
        return None
    last = bars.sessions[-1] if bars.sessions else None
    closes = {m["symbol"]: m["close"] for m in future["members"]}
    target = targets(future["members"], top_n, skip_beijing=skip_beijing)
    value = account.cash + sum(
        h.shares * ((bars.last_close(last, s) if last else None) or 0.0)
        for s, h in account.holdings.items()
    )
    sells = [s for s in account.holdings if s not in target]
    keeps = [s for s in account.holdings if s in target]
    slice_ = value / top_n
    buys = []
    for symbol in target:
        if symbol in account.holdings:
            continue
        price = closes.get(symbol) or 0.0
        shares = lot_shares(symbol, slice_ / (1 + costs.slippage + costs.commission), price)
        buys.append({"symbol": symbol, "shares_est": shares, "ref_price": price})
    return {
        "list_as_of": future["as_of"],
        "iso_week": future.get("iso_week"),
        "sells": sorted(sells, key=lambda s: -account.holdings[s].shares),
        "keeps": keeps,
        "buys": buys,
        "account_value": round(value, 2),
    }


def benchmark_return(bars: Bars, start: date, end: date) -> float | None:
    open_ = bars.value(bars.open, start, INDEX_SYMBOL)
    close = bars.value(bars.close, end, INDEX_SYMBOL)
    if open_ is None or close is None or open_ <= 0:
        return None
    return close / open_ - 1
