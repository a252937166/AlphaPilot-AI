"""Frozen weekly stock-pick candidates: universe, ranks, list files, scoring and verdict tiers."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from apscheduler.triggers.combining import OrTrigger
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alphapilot.db.models import Base, DailyBar, DomainEvent, Security, ValuationDaily
from alphapilot.jobs import stock_pick_forward_test as job
from alphapilot.jobs.registry import JOBS
from alphapilot.services import stock_picks as sp

AS_OF = date(2026, 9, 10)  # a Thursday
NOW = datetime(2026, 9, 11, 8, 0, tzinfo=UTC)  # Friday 16:00 CST, before the day's bars land
FORWARD_SESSIONS = 12  # enough for h=5 to mature, not for h=20
GROUPS = {
    "C39计算机、通信和其他电子设备制造业": [f"6001{i:02d}" for i in range(10)],
    "J66货币金融服务": [f"6002{i:02d}" for i in range(10)],
    "E48土木工程建筑业": [f"0001{i:02d}" for i in range(10)],
    "K70房地产业": [f"3001{i:02d}" for i in range(10)],
    "Z99三家小组": ["600901", "600902", "600903"],
}
SEVERE = "600100"  # negative regulatory event yesterday
LIMIT_UP = "600200"  # entry open at the 10% cap: unfillable
NO_ENTRY = "600201"  # suspended on the entry session
NO_EXIT = "600202"  # suspended on the h=5 exit session
NEGATIVE_PE = "600203"  # loss-making: value_pe missing, the other signals still count
ST_FLAG = "600099"
ST_NAME = "600098"
YOUNG = "301999"  # thirty bars only
INDEX = sp.INDEX_SYMBOL


def _hist() -> pd.DatetimeIndex:
    return pd.bdate_range(end=AS_OF, periods=280)


def _fwd() -> pd.DatetimeIndex:
    return pd.bdate_range(start=AS_OF + timedelta(days=1), periods=FORWARD_SESSIONS)


def _bars(
    symbol: str, dates: pd.DatetimeIndex, rng: np.random.Generator, level: float, amount: float
) -> list[dict[str, object]]:
    closes = level * np.cumprod(1 + rng.normal(0, 0.02, len(dates)))
    rows = []
    prev = float(closes[0])
    for day, close in zip(dates, closes, strict=True):
        open_ = prev * (1 + rng.normal(0, 0.004))
        high = max(open_, close) * (1 + abs(rng.normal(0, 0.003)))
        low = min(open_, close) * (1 - abs(rng.normal(0, 0.003)))
        rows.append(
            {
                "symbol": symbol,
                "trade_date": day.date(),
                "open": round(float(open_), 2),
                "high": round(float(high), 2),
                "low": round(float(low), 2),
                "close": round(float(close), 2),
                "volume": 1_000_000.0,
                "amount": float(rng.lognormal(np.log(amount), 0.3)),
                "source": "test",
                "ingested_at": NOW,
            }
        )
        prev = float(close)
    return rows


def _valuation(symbol: str, rng: np.random.Generator) -> list[ValuationDaily]:
    """Valuation rows for the as-of session and the next one (a weekend run lists the Friday)."""

    pb = float(rng.uniform(0.5, 8))
    pe = -12.0 if symbol == NEGATIVE_PE else float(rng.uniform(5, 80))
    return [
        ValuationDaily(
            symbol=symbol, trade_date=day, pb_mrq=pb, pe_ttm=pe, source="test", available_time=NOW
        )
        for day in (AS_OF, _fwd()[0].date())
    ]


def _populate(engine: Engine) -> None:
    rng = np.random.default_rng(7)
    all_dates = _hist().append(_fwd())
    bars: list[dict[str, object]] = []
    securities: list[Security] = []
    valuations: list[ValuationDaily] = []
    for industry, symbols in GROUPS.items():
        for symbol in symbols:
            bars += _bars(symbol, all_dates, rng, rng.uniform(5, 50), rng.uniform(1e7, 1e9))
            board = "创业板" if symbol.startswith("30") else "主板"
            securities.append(
                Security(symbol=symbol, name=f"股{symbol[-3:]}", board=board, industry=industry)
            )
            valuations.extend(_valuation(symbol, rng))
    for symbol, name, is_st in ((ST_FLAG, "股099", True), (ST_NAME, "*ST股098", False)):
        bars += _bars(symbol, all_dates, rng, 10.0, 1e8)
        securities.append(
            Security(symbol=symbol, name=name, board="主板", industry="C39计算机", is_st=is_st)
        )
        valuations.extend(_valuation(symbol, rng))
    bars += _bars(YOUNG, all_dates[-(30 + FORWARD_SESSIONS) :], rng, 20.0, 1e8)
    securities.append(Security(symbol=YOUNG, name="新股", board="创业板", industry=None))
    valuations.extend(_valuation(YOUNG, rng))
    bars += _bars(INDEX, all_dates, rng, 3000.0, 1e11)
    entry, exit5 = _fwd()[0].date(), _fwd()[4].date()
    close_as_of = {row["symbol"]: row["close"] for row in bars if row["trade_date"] == AS_OF}
    kept = []
    for row in bars:
        if row["symbol"] == NO_ENTRY and row["trade_date"] == entry:
            continue
        if row["symbol"] == NO_EXIT and row["trade_date"] == exit5:
            continue
        if row["symbol"] == LIMIT_UP and row["trade_date"] == entry:
            cap = round(float(close_as_of[LIMIT_UP]) * 1.10, 2)
            row = {**row, "open": cap, "high": cap, "low": cap, "close": cap}
        kept.append(row)
    with Session(engine) as session:
        session.execute(insert(DailyBar), kept)
        session.add_all(securities)
        session.add_all(valuations)
        session.add(
            DomainEvent(
                symbol=SEVERE,
                event_type=sp.SEVERE_EVENT_TYPE,
                direction=-1.0,
                strength=0.9,
                title="关于收到中国证券监督管理委员会立案告知书的公告",
                source_ref="news:1",
                occurred_at=NOW - timedelta(days=1),
            )
        )
        session.commit()


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{tmp_path / 'picks.db'}")
    Base.metadata.create_all(engine)
    _populate(engine)
    return engine


@pytest.fixture
def patched_session(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def local_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session
            session.commit()

    monkeypatch.setattr(job, "get_session", local_session)


def _panel(engine: Engine) -> sp.Panel:
    with Session(engine) as session:
        return sp.load_panel(session, AS_OF, now=NOW)


def test_helpers() -> None:
    assert sp.is_stock_symbol("600519") and not sp.is_stock_symbol("SH.000001")
    assert sp.limit_pct("300815") == 0.20 and sp.limit_pct("920218") == 0.30
    assert sp.limit_pct("688496") == 0.20 and sp.limit_pct("000498") == 0.10
    assert sp.iso_week_key(date(2026, 9, 10)) == "2026-W37"
    assert sp.iso_week_key(date(2026, 9, 13)) == "2026-W37"  # Sunday still closes week 37
    assert sp.industry_group("E48土木工程建筑业") == "E48"
    assert sp.industry_group(None) is None and sp.industry_group(float("nan")) is None


def test_universe_rules(engine: Engine) -> None:
    ok, exclusions = sp.universe_mask(_panel(engine))
    included = set(ok[ok].index)
    assert {INDEX, YOUNG, ST_FLAG, ST_NAME, SEVERE}.isdisjoint(included)
    assert len(included) == 42
    assert exclusions == {
        "not_stock": 1,
        "young_listing": 1,
        "st": 2,
        "severe_event": 1,
        "no_close": 0,
    }


def test_scores_follow_the_equal_rank_rule(engine: Engine) -> None:
    panel = _panel(engine)
    signals = sp.compute_signals(panel)
    ok, _ = sp.universe_mask(panel)
    scores = sp.candidate_scores(panel, sp.CANDIDATES["A"], signals)
    assert set(scores.index) == set(ok[ok].index)
    assert ((scores > 0) & (scores <= 1)).all()
    ranks = [signals[name].where(ok).rank(pct=True) for name in sp.A_WEIGHTS]
    expected = pd.concat(ranks, axis=1).mean(axis=1)[ok]
    pd.testing.assert_series_equal(scores.sort_index(), expected.sort_index(), check_names=False)
    assert np.isnan(signals["value_pe"][NEGATIVE_PE]) and not np.isnan(scores[NEGATIVE_PE])
    signed = sp.candidate_scores(panel, sp.CANDIDATES["B"], signals)
    assert set(signed.index) == set(scores.index)


def test_industry_neutral_ranks_are_uniform_inside_large_groups(engine: Engine) -> None:
    panel = _panel(engine)
    neutral = sp.candidate_scores(panel, sp.CandidateSpec("pb_only", {"value_pb": 1}, True))
    market = sp.candidate_scores(panel, sp.CandidateSpec("pb_market", {"value_pb": 1}, False))
    banks = GROUPS["J66货币金融服务"]
    assert sorted(round(v, 6) for v in neutral[banks]) == [round(k / 10, 6) for k in range(1, 11)]
    assert any(abs(neutral[s] - market[s]) > 1e-9 for s in banks)
    for symbol in GROUPS["Z99三家小组"]:  # below the group floor: market-wide rank
        assert neutral[symbol] == pytest.approx(market[symbol])


def test_list_document_is_ranked_and_frozen(engine: Engine) -> None:
    doc = sp.build_pick_list(_panel(engine), sp.CANDIDATES["A_ind"], generated_at=NOW)
    assert doc["schema"] == sp.LIST_SCHEMA
    assert doc["as_of"] == "2026-09-10" and doc["iso_week"] == "2026-W37"
    assert set(doc["frozen_by"]) == {"preregistration", "record", "correction", "amendment"}
    assert doc["universe_n"] == 42 and doc["top_decile_n"] == 4
    scores = [m["score"] for m in doc["members"]]
    assert scores == sorted(scores, reverse=True)
    assert [m["rank"] for m in doc["members"]] == list(range(1, 43))
    assert doc["top_decile_symbols"] == [m["symbol"] for m in doc["members"][:4]]
    assert all(m["decile"] == 9 for m in doc["members"][:4])
    assert doc["members"][-1]["decile"] == 0
    assert doc["top20"][0]["name"] and doc["top20"][0]["industry"]
    assert doc["severe_excluded"] == [SEVERE]
    assert doc["rule"]["industry_neutral"] is True and doc["history"]["valuation_coverage"] == 1.0


def test_job_generates_once_per_week_and_only_on_weekends(
    patched_session: None, tmp_path: Path
) -> None:
    out = tmp_path / "picks"
    midweek = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)  # Wednesday 20:00 CST
    first = job.run_stock_pick_forward_test(now=midweek, output_dir=out)
    assert first["generated"] == {} and first["generate_skipped"]
    assert not (out / "lists").exists()
    saturday = datetime(2026, 9, 12, 1, 30, tzinfo=UTC)  # 09:30 CST
    second = job.run_stock_pick_forward_test(now=saturday, output_dir=out)
    assert second["as_of"] == "2026-09-11" and second["iso_week"] == "2026-W37"
    assert {k: v["status"] for k, v in second["generated"].items()} == dict.fromkeys(
        sp.CANDIDATES, "written"
    )
    files = sorted(p.name for p in (out / "lists").rglob("*.json"))
    assert files == sorted(f"{name}-2026-W37-20260911.json" for name in sp.CANDIDATES)
    assert Path(second["summary_path"]).exists()
    sunday = job.run_stock_pick_forward_test(now=saturday + timedelta(days=1), output_dir=out)
    assert {k: v["status"] for k, v in sunday["generated"].items()} == dict.fromkeys(
        sp.CANDIDATES, "exists"
    )
    assert "summary_path" not in sunday
    forced = job.run_stock_pick_forward_test(now=midweek, as_of=AS_OF, output_dir=out)
    assert all(v["status"] == "exists" for v in forced["generated"].values())
    assert sorted(p.name for p in (out / "lists").rglob("*.json")) == files


def test_scoring_mechanics(patched_session: None, tmp_path: Path, engine: Engine) -> None:
    out = tmp_path / "picks"
    run = job.run_stock_pick_forward_test(now=NOW, as_of=AS_OF, output_dir=out)
    assert all(v["status"] == "written" for v in run["generated"].values())
    assert sorted((s["candidate"], s["horizon"]) for s in run["scored"]) == sorted(
        (name, 5) for name in sp.CANDIDATES
    )
    assert not list((out / "scores").rglob("*-h20.json"))
    fwd = _fwd()
    entry, exit5 = fwd[0].date(), fwd[4].date()
    score = json.loads((out / "scores" / "A" / "A-2026-W37-20260910-h5.json").read_bytes())
    assert score["schema"] == sp.SCORE_SCHEMA and score["horizon"] == 5
    assert score["entry_date"] == entry.isoformat() and score["exit_date"] == exit5.isoformat()
    assert score["counts"] == {
        "members": 42,
        "no_entry_bar": 1,
        "limit_up_open": 1,
        "no_exit_bar": 1,
        "fillable": 39,
    }
    listed = json.loads((out / "lists" / "A" / "A-2026-W37-20260910.json").read_bytes())
    fillable = [
        m["symbol"] for m in listed["members"] if m["symbol"] not in {NO_ENTRY, NO_EXIT, LIMIT_UP}
    ]
    with Session(engine) as session:
        bars = pd.read_sql_query(
            select(DailyBar.symbol, DailyBar.trade_date, DailyBar.open, DailyBar.close).where(
                DailyBar.trade_date.in_([entry, exit5])
            ),
            session.connection(),
        )
    bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.date
    opens = bars[bars["trade_date"] == entry].set_index("symbol")["open"]
    closes = bars[bars["trade_date"] == exit5].set_index("symbol")["close"]
    returns = pd.Series({s: closes[s] / opens[s] - 1 for s in fillable})
    assert score["median_return"] == pytest.approx(float(returns.median()), abs=1e-6)
    assert score["top_bin"]["n"] == 4 and 0 <= score["top_bin"]["hit_rate"] <= 1
    assert score["bottom_bin"]["n"] == 4
    assert score["spread"] == pytest.approx(
        score["top_bin"]["mean_excess"] - score["bottom_bin"]["mean_excess"], abs=1e-6
    )
    assert 1 <= score["list_top_decile"]["n"] <= 4
    assert score["benchmarks"]["index_return"] == pytest.approx(
        closes[INDEX] / opens[INDEX] - 1, abs=1e-6
    )
    assert score["benchmarks"]["equal_weight_universe_return"] == pytest.approx(
        float(returns.mean()), abs=1e-6
    )
    again = job.run_stock_pick_forward_test(now=NOW, generate=False, output_dir=out)
    assert again["scored"] == [] and again["generate_skipped"]
    assert again["tallies"]["A"]["lists_scored"] == 1
    assert again["tallies"]["A"]["status"] == "pending"


def test_missing_valuation_fails_closed(patched_session: None, tmp_path: Path) -> None:
    out = tmp_path / "picks"
    no_valuation_day = _fwd()[1].date()
    with pytest.raises(ValueError, match="valuation covers only 0/"):
        job.run_stock_pick_forward_test(now=NOW, as_of=no_valuation_day, output_dir=out)
    assert not list(out.rglob("*.json"))


def _h5(as_of: str, hit: float, excess: float) -> dict[str, object]:
    return {"as_of": as_of, "top_bin": {"hit_rate": hit, "mean_excess": excess}}


def test_forward_test_tally_tiers() -> None:
    weeks = [f"2026-10-{10 + i:02d}" for i in range(8)]
    assert sp.forward_test_tally([])["status"] == "pending"
    assert sp.forward_test_tally([_h5(w, 0.6, 0.01) for w in weeks[:7]])["status"] == "pending"
    flat = sp.forward_test_tally([_h5(w, 0.6, 0.01) for w in weeks])
    assert flat["status"] == "screen_only" and flat["t_stat"] is None
    steady_rows = [_h5(w, 0.6, 0.01 + 0.001 * (i % 2)) for i, w in enumerate(weeks)]
    steady = sp.forward_test_tally(steady_rows)
    assert steady["status"] == "confirmed" and steady["t_stat"] > 2
    assert steady["mean_hit_rate"] == pytest.approx(0.6)
    assert sp.forward_test_tally([_h5(w, 0.5, 0.01) for w in weeks])["status"] == "not_confirmed"
    assert sp.forward_test_tally([_h5(w, 0.6, -0.01) for w in weeks])["status"] == "not_confirmed"
    later = sp.forward_test_tally([*steady_rows, _h5("2026-12-31", 0.0, -0.5)])
    assert later["status"] == "confirmed"
    assert later["lists_scored"] == 9 and later["lists_judged"] == 8


def test_job_is_registered() -> None:
    from alphapilot.jobs import register_builtin_jobs

    register_builtin_jobs()
    spec = JOBS[job.JOB_NAME]
    assert spec.enabled_key == "stock_pick_forward_test_enabled"
    assert isinstance(spec.trigger, OrTrigger) and spec.misfire_grace_time == 3600
