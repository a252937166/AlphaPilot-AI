"""Status view of the weekly stock-pick forward test.

Read-only: it shows what the frozen lists have done since their entry session
(open of the first session after the as-of date), how they moved today, whether
any list member has since been flagged by the severe-disclosure screen, and the
tallies of matured scores. Nothing here feeds the verdict; the scoring job does
that from bars alone. Intraday prices come from a caller-supplied quote function
(the Futu snapshot by default); when no live quote is available the latest bars
are used instead and the view says so.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.db.models import DailyBar, DomainEvent, Security
from alphapilot.services.severe_disclosure import SEVERE_EVENT_TYPE
from alphapilot.services.stock_picks import CANDIDATES, forward_test_tally, is_stock_symbol

QuoteFn = Callable[[list[str]], pd.DataFrame]


@dataclass(slots=True)
class Quotes:
    """Per-symbol ``last`` and ``prev_close`` prices plus where they came from."""

    frame: pd.DataFrame
    source: str
    note: str


def latest_lists(root: Path) -> dict[str, dict[str, Any]]:
    """The most recent list document per candidate (by ISO week, then as-of)."""

    found: dict[str, dict[str, Any]] = {}
    for name in CANDIDATES:
        paths = sorted((root / "lists" / name).glob(f"{name}-*.json"))
        if paths:
            found[name] = json.loads(paths[-1].read_bytes())
    return found


def matured_tallies(root: Path) -> dict[str, dict[str, Any]]:
    tallies: dict[str, dict[str, Any]] = {}
    for name in CANDIDATES:
        scores = [
            json.loads(path.read_bytes())
            for path in sorted((root / "scores" / name).glob(f"{name}-*-h5.json"))
        ]
        tallies[name] = forward_test_tally(scores)
    return tallies


def entry_session(session: Session, as_of: date) -> date | None:
    return session.scalar(select(func.min(DailyBar.trade_date)).where(DailyBar.trade_date > as_of))


def entry_opens(session: Session, entry_date: date, symbols: list[str]) -> pd.Series:
    frame = pd.read_sql_query(
        select(DailyBar.symbol, DailyBar.open).where(
            DailyBar.trade_date == entry_date, DailyBar.symbol.in_(symbols)
        ),
        session.connection(),
    )
    return frame.drop_duplicates("symbol").set_index("symbol")["open"].astype("float64")


def bars_quotes(session: Session, symbols: list[str]) -> Quotes:
    """Fallback quotes from the last two sessions of daily bars."""

    dates = session.scalars(
        select(DailyBar.trade_date)
        .group_by(DailyBar.trade_date)
        .order_by(DailyBar.trade_date.desc())
        .limit(2)
    ).all()
    if len(dates) < 2:
        return Quotes(pd.DataFrame(columns=["last", "prev_close"]), "bars", "no bars")
    frame = pd.read_sql_query(
        select(DailyBar.symbol, DailyBar.trade_date, DailyBar.close).where(
            DailyBar.trade_date.in_(dates), DailyBar.symbol.in_(symbols)
        ),
        session.connection(),
    )
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    wide = frame.pivot_table(index="symbol", columns="trade_date", values="close", aggfunc="last")
    out = pd.DataFrame({"last": wide.get(dates[0]), "prev_close": wide.get(dates[1])}).dropna()
    return Quotes(out, "bars", f"closes of {dates[0].isoformat()} vs {dates[1].isoformat()}")


def futu_quotes(symbols: list[str]) -> pd.DataFrame:
    """Live snapshot for SH/SZ symbols through the repository's Futu client."""

    from alphapilot.futu.client import get_futu_client
    from alphapilot.jobs.market_poll import _fetch_futu_snapshot, _futu_code

    codes = [_futu_code(s) for s in symbols if not s.startswith(("4", "8", "92"))]
    if not codes:
        return pd.DataFrame(columns=["last", "prev_close"])
    client = get_futu_client()
    try:
        frame, _requests, _failures = _fetch_futu_snapshot(client, codes)
    finally:
        client.close()
    frame = (
        frame.assign(symbol=frame["code"].str[-6:]).drop_duplicates("symbol").set_index("symbol")
    )
    out = pd.DataFrame(
        {
            "last": pd.to_numeric(frame["last_price"], errors="coerce"),
            "prev_close": pd.to_numeric(frame["prev_close_price"], errors="coerce"),
        }
    )
    return out[(out["last"] > 0) & (out["prev_close"] > 0)]


def severe_flags(session: Session, symbols: list[str], since: date) -> dict[str, str]:
    floor = datetime(since.year, since.month, since.day, tzinfo=UTC)
    rows = session.execute(
        select(DomainEvent.symbol, DomainEvent.title)
        .where(
            DomainEvent.event_type == SEVERE_EVENT_TYPE,
            DomainEvent.occurred_at > floor,
            DomainEvent.symbol.in_(symbols),
        )
        .order_by(DomainEvent.occurred_at)
    ).all()
    return {str(symbol): str(title)[:40] for symbol, title in rows}


def _block(part: pd.DataFrame, market_today: float, market_since: float) -> dict[str, Any]:
    if part.empty:
        return {"n": 0}
    return {
        "n": len(part),
        "today_mean": round(float(part["today"].mean()), 6),
        "today_above_market": round(float((part["today"] > market_today).mean()), 4),
        "since_mean": round(float(part["since"].mean()), 6),
        "since_median": round(float(part["since"].median()), 6),
        "since_above_market": round(float((part["since"] > market_since).mean()), 4),
    }


def build_status(
    session: Session, root: Path, *, quote_fn: QuoteFn | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """Assemble the status document for every candidate's latest list."""

    current = (now or datetime.now(UTC)).astimezone(UTC)
    lists = latest_lists(root)
    status: dict[str, Any] = {
        "generated_at_utc": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "candidates": {},
        "tallies": matured_tallies(root),
    }
    if not lists:
        status["note"] = "no lists yet"
        return status
    universe_doc = lists.get("A") or next(iter(lists.values()))
    universe = [m["symbol"] for m in universe_doc["members"] if is_stock_symbol(m["symbol"])]
    as_of = date.fromisoformat(universe_doc["as_of"])
    entry_date = entry_session(session, as_of)
    quotes: Quotes | None = None
    if quote_fn is not None:
        try:
            frame = quote_fn(universe)
            if frame is not None and not frame.empty:
                quotes = Quotes(frame, "live", "intraday snapshot")
        except Exception as exc:  # a dead OpenD must not hide the bars-based view
            status["quote_error"] = f"{type(exc).__name__}: {exc}"[:200]
    if quotes is None:
        quotes = bars_quotes(session, universe)
    names = (
        pd.read_sql_query(
            select(Security.symbol, Security.name, Security.industry).where(
                Security.symbol.in_(universe)
            ),
            session.connection(),
        )
        .drop_duplicates("symbol")
        .set_index("symbol")
    )
    frame = quotes.frame.reindex(universe).dropna()
    frame["today"] = frame["last"] / frame["prev_close"] - 1
    if entry_date is not None:
        opens = entry_opens(session, entry_date, universe)
        frame["since"] = frame["last"] / opens.reindex(frame.index) - 1
    else:
        frame["since"] = np.nan
    market_today = float(frame["today"].median()) if len(frame) else float("nan")
    market_since = float(frame["since"].median()) if frame["since"].notna().any() else float("nan")
    status["market"] = {
        "as_of_list": as_of.isoformat(),
        "entry_date": entry_date.isoformat() if entry_date else None,
        "quote_source": quotes.source,
        "quote_note": quotes.note,
        "n": len(frame),
        "today_median": round(market_today, 6) if market_today == market_today else None,
        "since_median": round(market_since, 6) if market_since == market_since else None,
    }
    for name, doc in lists.items():
        top = frame.reindex([s for s in doc["top_decile_symbols"] if s in frame.index])
        top20_symbols = [m["symbol"] for m in doc["top20"]]
        top20 = frame.reindex([s for s in top20_symbols if s in frame.index])
        flagged = severe_flags(session, doc["top_decile_symbols"], date.fromisoformat(doc["as_of"]))
        rows = []
        for m in doc["top20"]:
            s = m["symbol"]
            rows.append(
                {
                    "rank": m["rank"],
                    "symbol": s,
                    "name": names["name"].get(s) if s in names.index else None,
                    "industry": (names["industry"].get(s) or "")[:8] if s in names.index else "",
                    "today": round(float(frame.at[s, "today"]), 6) if s in frame.index else None,
                    "since": round(float(frame.at[s, "since"]), 6)
                    if s in frame.index and frame.at[s, "since"] == frame.at[s, "since"]
                    else None,
                    "flagged": flagged.get(s),
                }
            )
        status["candidates"][name] = {
            "as_of": doc["as_of"],
            "iso_week": doc["iso_week"],
            "top_decile_n": doc["top_decile_n"],
            "unquoted": int(len(doc["top_decile_symbols"]) - len(top)),
            "top_decile": _block(top, market_today, market_since),
            "top20": _block(top20, market_today, market_since),
            "flagged": flagged,
            "rows": rows,
        }
    return status


def _pct(value: float | None) -> str:
    return "     —" if value is None else f"{value * 100:+6.2f}%"


def render(status: dict[str, Any]) -> str:
    """Plain-text rendering for the terminal."""

    lines: list[str] = []
    market = status.get("market")
    if not market:
        return status.get("note", "no lists yet")
    lines.append(
        f"名单 as of {market['as_of_list']}，入场 {market['entry_date'] or '未入场'}，"
        f"报价来源 {market['quote_source']}（{market['quote_note']}），有报价 {market['n']} 只"
    )
    lines.append(
        f"全市场中位：今天 {_pct(market['today_median'])}，入场以来 {_pct(market['since_median'])}"
    )
    lines.append(
        f"{'候选':8s} {'前10%今天':>10s} {'跑赢':>5s} {'入场以来均值':>10s}"
        f" {'中位':>8s} {'跑赢':>5s} | {'前20入场以来':>10s} {'跑赢':>5s} 未报价 标记"
    )
    for name, block in status["candidates"].items():
        top, t20 = block["top_decile"], block["top20"]
        if top.get("n", 0) == 0:
            lines.append(f"{name:8s} 无报价")
            continue
        lines.append(
            f"{name:8s} {_pct(top['today_mean']):>10s} {top['today_above_market']:5.0%}"
            f" {_pct(top['since_mean']):>10s} {_pct(top['since_median']):>8s}"
            f" {top['since_above_market']:5.0%}"
            f" | {_pct(t20.get('since_mean')):>10s} {t20.get('since_above_market', 0):5.0%}"
            f" {block['unquoted']:5d} {len(block['flagged']):3d}"
        )
    for name, block in status["candidates"].items():
        lines.append(f"\n[{name}] 前 20：今天 / 入场以来")
        for r in block["rows"]:
            flag = f"  ⚠ {r['flagged']}" if r["flagged"] else ""
            lines.append(
                f"  {r['rank']:2d} {r['symbol']} {(r['name'] or '?'):8s} {r['industry']:8s}"
                f" {_pct(r['today'])} / {_pct(r['since'])}{flag}"
            )
    lines.append("")
    for name, tally in status["tallies"].items():
        lines.append(
            f"{name:8s} 已评分 {tally['lists_scored']} 份，状态 {tally['status']}"
            + (
                f"，平均命中 {tally['mean_hit_rate']:.0%}"
                f"，累计超额 {tally['cumulative_excess'] * 100:+.2f}%"
                if tally["lists_scored"]
                else ""
            )
        )
    return "\n".join(lines)
