"""Pre-registered weekly stock-pick candidates and their forward-test scoring.

Every number in this module is frozen by evidence files kept outside the
repository (see ``FROZEN_BY``): the signal formulas, the equal-rank combination,
the industry-neutral ranking rule, the universe, the decile definitions and the
scoring mechanics. Changing any of them is a new amendment with its own forward
window, never an edit in place.

Timing: a list "as of" session t uses data through t only. Entry is the open of
the first session after t, exit the close of the h-th session after t. A member
whose entry open is limit-up (or that has no entry bar) is unfillable and is
left out of that list's metrics. Excess is the member's return minus the median
return of the list's fillable members, so a list is judged against its own
market, not against a cash yardstick.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.db.models import DailyBar, DomainEvent, Security, ValuationDaily
from alphapilot.services.severe_disclosure import SEVERE_EVENT_TYPE

LIST_SCHEMA = "alphapilot.stock-picks.list.v1"
SCORE_SCHEMA = "alphapilot.stock-picks.score.v1"
FROZEN_BY: dict[str, str] = {
    "preregistration": (
        "preregistration-signal-census-20260911.json sha256 "
        "b2396af74bd54f8c31433cae70399a13c306259bf2c18639d16cda0ca211073f"
    ),
    "record": (
        "phase2-record-and-forward-test-preregistration-20260911.json sha256 "
        "4b8b915469a10618b3474ff857eb1c6d6d40cb2af9b207b639304f79faf195fc"
    ),
    "correction": (
        "phase2-universe-correction-record-20260911.json sha256 "
        "7fe906de523b8d91b6ccabe00e7e05f0530d7e5deb03ff5cd5bb7617f2be525b"
    ),
    "amendment": (
        "forward-test-amendment-A1-multicandidate-20260911.json sha256 "
        "57d10c4e3f58f56291d6d059fd134c19b46e35dc89752ba737fa2e4e93961962"
    ),
}
INDEX_SYMBOL = "SH.000001"
HORIZONS: tuple[int, ...] = (5, 20)
MIN_LISTED_BARS = 60
SEVERE_LOOKBACK_DAYS = 7
MIN_INDUSTRY_GROUP = 8
HISTORY_CALENDAR_DAYS = 420
MIN_HISTORY_SESSIONS = 250
MIN_VALUATION_COVERAGE = 0.5
VERDICT_LISTS = 8
SCREEN_HIT_RATE = 0.55
CONFIRM_T_STAT = 2.0

A_WEIGHTS: dict[str, int] = {"lowvol20": 1, "value_pb": 1, "value_pe": 1}
B_WEIGHTS: dict[str, int] = {
    "rev5": 1,
    "rev20": 1,
    "mom60": -1,
    "lowvol20": 1,
    "amt_ratio_5_20": -1,
    "vol_surge": -1,
    "high250_dist": -1,
    "liq_size": 1,
    "value_pb": 1,
    "value_pe": 1,
}


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    name: str
    weights: dict[str, int]
    industry_neutral: bool


CANDIDATES: dict[str, CandidateSpec] = {
    "A": CandidateSpec("A", A_WEIGHTS, False),
    "A_ind": CandidateSpec("A_ind", A_WEIGHTS, True),
    "B": CandidateSpec("B", B_WEIGHTS, False),
    "B_ind": CandidateSpec("B_ind", B_WEIGHTS, True),
}


def is_stock_symbol(symbol: str) -> bool:
    """Six ASCII digits: index rows such as SH.000001 never enter a universe."""

    return len(symbol) == 6 and symbol.isdigit()


def limit_pct(symbol: str) -> float:
    if symbol.startswith(("30", "68")):
        return 0.20
    if symbol.startswith(("4", "8", "92")):
        return 0.30
    return 0.10


def iso_week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def industry_group(industry: str | None) -> str | None:
    """CSRC section letter plus two-digit class, e.g. ``E48`` from ``E48土木工程建筑业``."""

    if not isinstance(industry, str) or not industry:
        return None
    code = industry.strip()[:3]
    return code or None


def write_json_create_only(path: Path, document: dict[str, Any]) -> str:
    """Write a new JSON file (never overwrite) and return its SHA-256."""

    import hashlib

    raw = (json.dumps(document, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    return hashlib.sha256(raw).hexdigest()


@dataclass(slots=True)
class Panel:
    """Wide price/valuation frames through ``as_of`` plus the static facts a list needs."""

    as_of: date
    dates: pd.DatetimeIndex
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    amount: pd.DataFrame
    pb: pd.Series
    pe: pd.Series
    securities: pd.DataFrame
    bar_counts: pd.Series
    severe_symbols: set[str]
    severe_floor: datetime


def latest_session(session: Session, on_or_before: date) -> date | None:
    return session.scalar(
        select(func.max(DailyBar.trade_date)).where(DailyBar.trade_date <= on_or_before)
    )


def _wide(
    frame: pd.DataFrame, column: str, dates: pd.DatetimeIndex, symbols: list[str]
) -> pd.DataFrame:
    wide = frame.pivot(index="trade_date", columns="symbol", values=column)
    return wide.reindex(index=dates, columns=symbols).astype("float64")


def load_panel(session: Session, as_of: date, *, now: datetime | None = None) -> Panel:
    """Load everything a list as of ``as_of`` needs, with data through ``as_of`` only."""

    current = now or datetime.now(UTC)
    start = as_of - timedelta(days=HISTORY_CALENDAR_DAYS)
    connection = session.connection()
    bars = pd.read_sql_query(
        select(
            DailyBar.symbol,
            DailyBar.trade_date,
            DailyBar.open,
            DailyBar.high,
            DailyBar.low,
            DailyBar.close,
            DailyBar.amount,
        ).where(DailyBar.trade_date >= start, DailyBar.trade_date <= as_of),
        connection,
    )
    if bars.empty:
        raise ValueError(f"no daily bars between {start} and {as_of}")
    bars["trade_date"] = pd.to_datetime(bars["trade_date"])
    dates = pd.DatetimeIndex(sorted(bars["trade_date"].unique()))
    if dates[-1].date() != as_of:
        raise ValueError(f"{as_of} is not a session with bars (latest is {dates[-1].date()})")
    if len(dates) < MIN_HISTORY_SESSIONS:
        raise ValueError(f"only {len(dates)} sessions loaded; {MIN_HISTORY_SESSIONS} required")
    symbols = sorted(bars["symbol"].unique())
    frames = {
        column: _wide(bars, column, dates, symbols)
        for column in ("open", "high", "low", "close", "amount")
    }
    valuation = (
        pd.read_sql_query(
            select(ValuationDaily.symbol, ValuationDaily.pb_mrq, ValuationDaily.pe_ttm).where(
                ValuationDaily.trade_date == as_of
            ),
            connection,
        )
        .drop_duplicates("symbol")
        .set_index("symbol")
    )
    pb = valuation["pb_mrq"].reindex(symbols).astype("float64")
    pe = valuation["pe_ttm"].reindex(symbols).astype("float64")
    securities = (
        pd.read_sql_query(
            select(
                Security.symbol, Security.name, Security.board, Security.industry, Security.is_st
            ),
            connection,
        )
        .drop_duplicates("symbol")
        .set_index("symbol")
        .reindex(symbols)
    )
    counts = (
        pd.read_sql_query(
            select(DailyBar.symbol, func.count().label("bars"))
            .where(DailyBar.trade_date <= as_of)
            .group_by(DailyBar.symbol),
            connection,
        )
        .set_index("symbol")["bars"]
        .reindex(symbols)
        .fillna(0)
        .astype("int64")
    )
    floor = current - timedelta(days=SEVERE_LOOKBACK_DAYS)
    severe = set(
        session.scalars(
            select(DomainEvent.symbol)
            .where(DomainEvent.event_type == SEVERE_EVENT_TYPE, DomainEvent.occurred_at >= floor)
            .distinct()
        ).all()
    )
    severe.discard(None)
    return Panel(
        as_of=as_of,
        dates=dates,
        open=frames["open"],
        high=frames["high"],
        low=frames["low"],
        close=frames["close"],
        amount=frames["amount"],
        pb=pb,
        pe=pe,
        securities=securities,
        bar_counts=counts,
        severe_symbols={str(symbol) for symbol in severe},
        severe_floor=floor,
    )


def compute_signals(panel: Panel) -> dict[str, pd.Series]:
    """The census signal formulas, evaluated on the as-of session (data through t only)."""

    close, amount = panel.close, panel.amount
    returns = close.ffill().pct_change(fill_method=None)
    amt20 = amount.rolling(20, min_periods=15).mean()
    amt5 = amount.rolling(5, min_periods=4).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        frames = {
            "rev5": -(close / close.shift(5) - 1),
            "rev20": -(close / close.shift(20) - 1),
            "mom60": close / close.shift(60) - 1,
            "lowvol20": -returns.rolling(20, min_periods=15).std(),
            "amt_ratio_5_20": amt5 / amt20 - 1,
            "vol_surge": amount / amt20 - 1,
            "high250_dist": close / close.rolling(250, min_periods=120).max(),
            "liq_size": -np.log(amt20.where(amt20 > 0)),
        }
    last = panel.dates[-1]
    signals = {name: frame.loc[last] for name, frame in frames.items()}
    signals["value_pb"] = -panel.pb.where(panel.pb > 0)
    signals["value_pe"] = -panel.pe.where(panel.pe > 0)
    return signals


def universe_mask(panel: Panel) -> tuple[pd.Series, dict[str, int]]:
    """Frozen universe: stocks only, listed >= 60 bars, not ST, no severe event, has a close."""

    symbols = list(panel.close.columns)
    index = pd.Index(symbols)
    is_stock = pd.Series([is_stock_symbol(s) for s in symbols], index=index)
    listed = panel.bar_counts.reindex(index).fillna(0) >= MIN_LISTED_BARS
    names = panel.securities["name"].reindex(index).fillna("").astype(str)
    st_flag = panel.securities["is_st"].reindex(index).astype("boolean").fillna(False).astype(bool)
    st = st_flag | names.str.startswith(("ST", "*ST"))
    severe = pd.Series([s in panel.severe_symbols for s in symbols], index=index)
    has_close = panel.close.iloc[-1].reindex(index).notna()
    exclusions = {
        "not_stock": int((~is_stock).sum()),
        "young_listing": int((is_stock & ~listed).sum()),
        "st": int((is_stock & listed & st).sum()),
        "severe_event": int((is_stock & listed & ~st & severe).sum()),
        "no_close": int((is_stock & listed & ~st & ~severe & ~has_close).sum()),
    }
    return is_stock & listed & ~st & ~severe & has_close, exclusions


def industry_groups(panel: Panel) -> pd.Series:
    industries = panel.securities["industry"].reindex(panel.close.columns)
    return pd.Series(
        [industry_group(v) for v in industries], index=industries.index, dtype="object"
    )


def candidate_scores(
    panel: Panel, spec: CandidateSpec, signals: dict[str, pd.Series] | None = None
) -> pd.Series:
    """Mean over the candidate's signals of sign x pct-rank, industry-neutral when specified."""

    signals = signals or compute_signals(panel)
    ok, _ = universe_mask(panel)
    groups = industry_groups(panel) if spec.industry_neutral else None
    parts = []
    for name, sign in spec.weights.items():
        value = signals[name].reindex(ok.index).where(ok)
        rank = value.rank(pct=True)
        if groups is not None:
            grouped = value.groupby(groups).rank(pct=True)
            size = value.notna().groupby(groups).transform("sum")
            use_group = grouped.notna() & (size >= MIN_INDUSTRY_GROUP)
            rank = rank.where(~use_group, grouped)
        parts.append(sign * rank)
    score = pd.concat(parts, axis=1).mean(axis=1, skipna=True)
    return score[ok & score.notna()]


def build_pick_list(
    panel: Panel,
    spec: CandidateSpec,
    *,
    generated_at: datetime,
    signals: dict[str, pd.Series] | None = None,
) -> dict[str, Any]:
    """The full ranked universe for one candidate as of the panel's session."""

    score = candidate_scores(panel, spec, signals)
    _, exclusions = universe_mask(panel)
    ordered = (
        pd.DataFrame({"symbol": score.index, "score": score.values})
        .sort_values(["score", "symbol"], ascending=[False, True], kind="mergesort")
        .reset_index(drop=True)
    )
    n = len(ordered)
    if n < 10:
        raise ValueError(f"universe too small for deciles: {n}")
    decile = pd.qcut(ordered["score"].rank(method="first"), 10, labels=False)
    last_close = panel.close.iloc[-1]
    members = [
        {
            "symbol": row.symbol,
            "rank": int(i + 1),
            "score": round(float(row.score), 6),
            "decile": int(decile.iloc[i]),
            "close": round(float(last_close[row.symbol]), 4),
        }
        for i, row in enumerate(ordered.itertuples(index=False))
    ]
    top_n = max(1, n // 10)
    securities = panel.securities
    top20 = []
    for member in members[:20]:
        symbol = member["symbol"]
        top20.append(
            {
                **member,
                "name": securities["name"].get(symbol),
                "board": securities["board"].get(symbol),
                "industry": securities["industry"].get(symbol),
            }
        )
    valued = int((panel.pb.reindex(score.index) > 0).sum())
    if valued / n < MIN_VALUATION_COVERAGE:
        raise ValueError(
            f"valuation covers only {valued}/{n} universe members as of {panel.as_of}; "
            "the list would silently lose its value signals"
        )
    return {
        "schema": LIST_SCHEMA,
        "candidate": spec.name,
        "as_of": panel.as_of.isoformat(),
        "iso_week": iso_week_key(panel.as_of),
        "generated_at_utc": generated_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entry": "open of the first session after as_of",
        "frozen_by": FROZEN_BY,
        "rule": {
            "weights": dict(spec.weights),
            "industry_neutral": spec.industry_neutral,
            "score": "mean of sign x pct-rank over the candidate's signals",
            "industry_group": "first three characters of securities.industry; groups under "
            f"{MIN_INDUSTRY_GROUP} members and missing codes fall back to market-wide ranks",
        },
        "history": {
            "sessions_loaded": len(panel.dates),
            "first_session": panel.dates[0].date().isoformat(),
            "valuation_coverage": round(valued / n, 4),
            "severe_floor_utc": panel.severe_floor.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "universe_n": n,
        "exclusions": exclusions,
        "severe_excluded": sorted(panel.severe_symbols),
        "top_decile_n": top_n,
        "top_decile_symbols": [m["symbol"] for m in members[:top_n]],
        "top20": top20,
        "members": members,
    }


def _sessions_after(session: Session, as_of: date, count: int) -> list[date]:
    """Market sessions after ``as_of``: dates carrying at least a fifth of the as-of bar count."""

    on_as_of = session.scalar(select(func.count()).where(DailyBar.trade_date == as_of)) or 0
    floor = max(1, on_as_of // 5)
    rows = session.scalars(
        select(DailyBar.trade_date)
        .where(DailyBar.trade_date > as_of)
        .group_by(DailyBar.trade_date)
        .having(func.count() >= floor)
        .order_by(DailyBar.trade_date)
        .limit(count)
    ).all()
    return list(rows)


def _spearman(x: np.ndarray, y: np.ndarray) -> float | None:
    if len(x) < 10:
        return None
    xr = pd.Series(x).rank().values
    yr = pd.Series(y).rank().values
    if xr.std() == 0 or yr.std() == 0:
        return None
    return round(float(np.corrcoef(xr, yr)[0, 1]), 4)


def score_pick_list(
    session: Session, pick_list: dict[str, Any], horizon: int
) -> dict[str, Any] | None:
    """Score one list at one horizon; ``None`` until the h-th session after as_of has bars."""

    as_of = date.fromisoformat(pick_list["as_of"])
    sessions = _sessions_after(session, as_of, horizon)
    if len(sessions) < horizon:
        return None
    entry_date, exit_date = sessions[0], sessions[horizon - 1]
    members = pick_list["members"]
    symbols = [m["symbol"] for m in members]
    bars = pd.read_sql_query(
        select(
            DailyBar.symbol, DailyBar.trade_date, DailyBar.open, DailyBar.low, DailyBar.close
        ).where(
            DailyBar.trade_date.in_([entry_date, exit_date]),
            DailyBar.symbol.in_([*symbols, INDEX_SYMBOL]),
        ),
        session.connection(),
    )
    bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.date
    entry = bars[bars["trade_date"] == entry_date].drop_duplicates("symbol").set_index("symbol")
    exit_ = bars[bars["trade_date"] == exit_date].drop_duplicates("symbol").set_index("symbol")
    counts = {"members": len(members), "no_entry_bar": 0, "limit_up_open": 0, "no_exit_bar": 0}
    scored: list[dict[str, Any]] = []
    for member in members:
        symbol = member["symbol"]
        if symbol not in entry.index or pd.isna(entry.at[symbol, "open"]):
            counts["no_entry_bar"] += 1
            continue
        open_ = float(entry.at[symbol, "open"])
        low = float(entry.at[symbol, "low"])
        cap = round(member["close"] * (1 + limit_pct(symbol)), 2) - 0.005
        if open_ >= cap and low >= open_ - 1e-6:
            counts["limit_up_open"] += 1
            continue
        if symbol not in exit_.index or pd.isna(exit_.at[symbol, "close"]):
            counts["no_exit_bar"] += 1
            continue
        scored.append(
            {
                "symbol": symbol,
                "rank": member["rank"],
                "score": member["score"],
                "return": float(exit_.at[symbol, "close"]) / open_ - 1,
            }
        )
    counts["fillable"] = len(scored)
    if len(scored) < 10:
        raise ValueError(
            f"too few fillable members to score {pick_list['candidate']} {as_of}: {len(scored)}"
        )
    frame = pd.DataFrame(scored)
    median = float(frame["return"].median())
    frame["excess"] = frame["return"] - median
    frame["bin"] = pd.qcut(frame["score"].rank(method="first"), 10, labels=False)
    top = frame[frame["bin"] == 9]
    bottom = frame[frame["bin"] == 0]
    list_top = frame[frame["rank"] <= pick_list["top_decile_n"]]
    index_return = None
    if INDEX_SYMBOL in entry.index and INDEX_SYMBOL in exit_.index:
        index_open = float(entry.at[INDEX_SYMBOL, "open"])
        if index_open > 0:
            index_return = round(float(exit_.at[INDEX_SYMBOL, "close"]) / index_open - 1, 6)

    def block(part: pd.DataFrame) -> dict[str, Any]:
        return {
            "n": len(part),
            "hit_rate": round(float((part["excess"] > 0).mean()), 4),
            "mean_excess": round(float(part["excess"].mean()), 6),
            "median_excess": round(float(part["excess"].median()), 6),
            "mean_return": round(float(part["return"].mean()), 6),
        }

    return {
        "schema": SCORE_SCHEMA,
        "candidate": pick_list["candidate"],
        "as_of": pick_list["as_of"],
        "iso_week": pick_list["iso_week"],
        "horizon": horizon,
        "entry_date": entry_date.isoformat(),
        "exit_date": exit_date.isoformat(),
        "scored_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": counts,
        "median_return": round(median, 6),
        "top_bin": block(top),
        "bottom_bin": block(bottom),
        "spread": round(float(top["excess"].mean() - bottom["excess"].mean()), 6),
        "ic": _spearman(frame["score"].values, frame["excess"].values),
        "list_top_decile": block(list_top),
        "benchmarks": {
            "equal_weight_universe_return": round(float(frame["return"].mean()), 6),
            "index_symbol": INDEX_SYMBOL,
            "index_return": index_return,
        },
    }


def forward_test_tally(scores_h5: list[dict[str, Any]]) -> dict[str, Any]:
    """Frozen verdict over the first ``VERDICT_LISTS`` matured lists (5-session horizon)."""

    ordered = sorted(scores_h5, key=lambda s: s["as_of"])
    judged = ordered[:VERDICT_LISTS]
    hits = [s["top_bin"]["hit_rate"] for s in judged]
    excess = [s["top_bin"]["mean_excess"] for s in judged]
    tally: dict[str, Any] = {
        "lists_scored": len(ordered),
        "lists_judged": len(judged),
        "mean_hit_rate": round(float(np.mean(hits)), 4) if hits else None,
        "cumulative_excess": round(float(np.sum(excess)), 6) if excess else None,
        "t_stat": None,
        "status": "pending",
        "weekly": [
            {
                "as_of": s["as_of"],
                "hit_rate": s["top_bin"]["hit_rate"],
                "mean_excess": s["top_bin"]["mean_excess"],
            }
            for s in ordered
        ],
    }
    if len(excess) >= 2:
        std = float(np.std(excess, ddof=1))
        if std > 0:
            tally["t_stat"] = round(float(np.mean(excess) / (std / math.sqrt(len(excess)))), 3)
    if len(judged) >= VERDICT_LISTS:
        screen = tally["mean_hit_rate"] >= SCREEN_HIT_RATE and tally["cumulative_excess"] > 0
        confirmed = screen and tally["t_stat"] is not None and tally["t_stat"] >= CONFIRM_T_STAT
        if confirmed:
            tally["status"] = "confirmed"
        else:
            tally["status"] = "screen_only" if screen else "not_confirmed"
    return tally
