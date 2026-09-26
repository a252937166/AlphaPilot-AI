"""Paper accounts for the weekly stock-pick lists: rotation, lots, costs, limits, plan, note."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, delete, insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alphapilot.db.models import Base, DailyBar, DomainEvent, Security
from alphapilot.jobs import stock_pick_actions as job
from alphapilot.jobs.registry import JOBS
from alphapilot.services import stock_pick_paper as paper
from alphapilot.services.severe_disclosure import SEVERE_EVENT_TYPE

S = [
    date(2026, 9, 10),
    date(2026, 9, 11),
    date(2026, 9, 14),
    date(2026, 9, 15),
    date(2026, 9, 16),
    date(2026, 9, 17),
    date(2026, 9, 18),
]
X, Y, Z, W = "600001", "600002", "600003", "600004"


def _doc(as_of: date, order: list[str], closes: dict[str, float] | None = None) -> dict:
    members = [
        {
            "symbol": s,
            "rank": i + 1,
            "score": 1 - i / 10,
            "decile": 9,
            "close": (closes or {}).get(s, 10.0),
        }
        for i, s in enumerate(order)
    ]
    return {
        "candidate": "A",
        "as_of": as_of.isoformat(),
        "iso_week": "W",
        "members": members,
        "top20": members,
        "top_decile_symbols": order[:1],
        "top_decile_n": 1,
    }


def _bars(
    overrides: dict[tuple[date, str], tuple[float, float, float, float]] | None = None,
) -> paper.Bars:
    rows = {}
    for day in S:
        for s in (X, Y, Z, W):
            rows[(day, s)] = (10.0, 10.0, 10.0, 10.0)  # open, high, low, close
    rows.update(overrides or {})
    frame = pd.DataFrame(
        [{"d": d, "s": s, "o": v[0], "h": v[1], "l": v[2], "c": v[3]} for (d, s), v in rows.items()]
    )

    def wide(col: str) -> pd.DataFrame:
        return frame.pivot(index="d", columns="s", values=col).reindex(S)

    return paper.Bars(open=wide("o"), high=wide("h"), low=wide("l"), close=wide("c"))


LISTS = [
    _doc(S[0], [X, Y, Z, W]),  # entry S[1]
    _doc(S[3], [Y, Z, X, W]),  # entry S[4]
    _doc(S[6], [Z, W, Y, X]),  # as of the last session: next open is the plan
]


def test_lot_rules() -> None:
    assert paper.lot_shares("600001", 50_000, 10.0) == 5_000
    assert paper.lot_shares("600001", 49_990, 10.0) == 4_900
    assert (
        paper.lot_shares("688001", 1_999, 10.0) == 0
        and paper.lot_shares("688001", 2_050, 10.0) == 205
    )
    assert (
        paper.lot_shares("920001", 999, 10.0) == 0
        and paper.lot_shares("920001", 1_234, 10.0) == 123
    )
    assert paper.lot_shares("600001", 100, 0.0) == 0


def test_weekly_rotation_keeps_overlap_and_charges_costs() -> None:
    out = paper.simulate(LISTS, _bars(), capital=100_000, top_n=2)
    acc = out["account"]
    trades = [(t["date"], t["side"], t["symbol"], t["shares"]) for t in acc.trades]
    assert trades[:2] == [("2026-09-11", "buy", X, 4_900), ("2026-09-11", "buy", Y, 4_900)]
    assert ("2026-09-16", "sell", X, 4_900) in trades and ("2026-09-16", "buy", Z, 4_900) in trades
    assert not any(t[2] == Y and t[1] == "sell" for t in trades)  # Y stayed in the list
    assert set(acc.holdings) == {Y, Z}
    buy = next(t for t in acc.trades if t["side"] == "buy")
    amount = 4_900 * 10.0 * 1.0005
    assert buy["fee"] == pytest.approx(round(max(5, amount * 0.00025) + amount * 0.00001, 2))
    sell = next(t for t in acc.trades if t["side"] == "sell")
    sold = 4_900 * 10.0 * 0.9995
    assert sell["fee"] == pytest.approx(
        round(max(5, sold * 0.00025) + sold * 0.00001 + sold * 0.0005, 2)
    )
    last_value = acc.curve[-1][1]
    assert last_value == pytest.approx(
        acc.cash + sum(h.shares * 10.0 for h in acc.holdings.values())
    )
    assert last_value < 100_000  # flat prices: only costs
    plan = out["plan"]
    assert plan["list_as_of"] == "2026-09-18"
    assert plan["sells"] == [Y] and plan["keeps"] == [Z]
    assert [b["symbol"] for b in plan["buys"]] == [W] and plan["buys"][0]["shares_est"] > 0


def test_limit_up_entry_is_skipped_and_limit_down_sale_is_retried() -> None:
    bars = _bars(
        {
            (S[4], Z): (11.0, 11.0, 11.0, 11.0),  # one-price limit-up on the rotation open
            (S[4], X): (9.0, 9.0, 9.0, 9.0),  # one-price limit-down: cannot sell
            (S[5], X): (8.6, 8.8, 8.5, 8.7),  # tradable the next session
        }
    )
    acc = paper.simulate(LISTS, bars, capital=100_000, top_n=2)["account"]
    assert {"date": "2026-09-16", "symbol": Z, "reason": "limit-up open"} in acc.skipped
    sells = [t for t in acc.trades if t["side"] == "sell"]
    assert [(t["date"], t["symbol"]) for t in sells] == [("2026-09-17", X)]
    assert sells[0]["price"] == pytest.approx(8.6 * 0.9995)
    assert Z not in acc.holdings and set(acc.holdings) == {Y}


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{tmp_path / 'paper.db'}")
    Base.metadata.create_all(engine)
    rows = [
        {
            "symbol": s,
            "trade_date": d,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0 + (0.5 if s == Y and d >= S[2] else 0.0),
            "volume": 1.0,
            "amount": 1.0,
            "source": "test",
            "ingested_at": datetime(2026, 9, 18, tzinfo=UTC),
        }
        for d in S
        for s in (X, Y, Z, W, paper.INDEX_SYMBOL)
    ]
    with Session(engine) as session:
        session.execute(insert(DailyBar), rows)
        session.add_all([Security(symbol=s, name=f"股{s[-1]}") for s in (X, Y, Z, W)])
        session.add(
            DomainEvent(
                symbol=Y,
                event_type=SEVERE_EVENT_TYPE,
                direction=-1.0,
                strength=0.9,
                title="关于收到立案告知书的公告",
                source_ref="news:9",
                occurred_at=datetime(2026, 9, 17, 8, tzinfo=UTC),
            )
        )
        session.commit()
    return engine


def test_job_writes_snapshot_and_note(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextmanager
    def local_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr(job, "get_session", local_session)
    root = tmp_path / "picks"
    for i, doc in enumerate(LISTS):
        path = root / "lists" / "A" / f"A-2026-W{37 + i}-{doc['as_of'].replace('-', '')}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
    notes = tmp_path / "vault"
    now = datetime(2026, 9, 19, 2, 0, tzinfo=UTC)  # Saturday 10:00 CST
    first = job.run_stock_pick_actions(
        now=now, output_dir=root, note_dir=notes, capital=100_000, top_n=2
    )
    assert first["accounts"]["A"]["holdings"] == 2 and first["accounts"]["A"]["plan"] is True
    assert first["accounts"]["A"]["flags"] == 1
    snapshot = json.loads(Path(first["json"]["path"]).read_bytes())
    assert snapshot["last_session"] == "2026-09-18" and snapshot["first_entry"] == "2026-09-11"
    text = (notes / "2026-09-19.md").read_text(encoding="utf-8")
    assert "# 交易动作 · 9 月 19 日周六" in text and "## 下一步动作" in text
    assert "卖出，开盘市价" in text and f"- {Y} 股2" in text and f"- {W} 股4 约" in text
    assert "立案告知书" in text and "规则本身不要求卖出" in text
    second = job.run_stock_pick_actions(
        now=now, output_dir=root, note_dir=notes, capital=100_000, top_n=2
    )
    assert second["json"]["status"] == "exists" and (notes / "2026-09-19.md").exists()


def test_job_is_registered() -> None:
    from alphapilot.jobs import register_builtin_jobs

    register_builtin_jobs()
    spec = JOBS[job.JOB_NAME]
    assert spec.enabled_key == "stock_pick_actions_enabled"
    assert "hour='20', minute='30'" in str(spec.trigger) and "hour='10', minute='0'" in str(
        spec.trigger
    )


def test_new_account_shows_its_first_orders(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextmanager
    def local_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr(job, "get_session", local_session)
    root = tmp_path / "picks"
    for name, doc in (("A", LISTS[0]), ("J", {**LISTS[2], "candidate": "J"})):
        path = root / "lists" / name / f"{name}-W-{doc['as_of'].replace('-', '')}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
    notes = tmp_path / "vault"
    stats = job.run_stock_pick_actions(
        now=datetime(2026, 9, 19, 2, tzinfo=UTC),
        output_dir=root,
        note_dir=notes,
        capital=100_000,
        top_n=2,
    )
    assert stats["accounts"]["J"] == {
        "value": 100_000.0,
        "return": 0.0,
        "holdings": 0,
        "plan": True,
        "flags": 0,
    }
    text = (notes / "2026-09-19.md").read_text(encoding="utf-8")
    assert (
        "| J jev 挑选 | 下次开盘建仓 |" in text and "### J jev 挑选：下一个交易日开盘执行" in text
    )


def test_beijing_names_are_skipped_and_the_list_is_walked_further() -> None:
    members = _doc(S[0], ["920001", X, "830002", Y, Z])["members"]
    assert paper.targets(members, 2) == [X, Y]
    assert paper.targets(members, 2, skip_beijing=False) == ["920001", X]
    assert all(paper.is_beijing(s) for s in ("920001", "830002", "430017"))
    assert not any(paper.is_beijing(s) for s in ("600001", "000001", "300750", "688981"))
    lists = [_doc(S[0], ["920001", X, Y, Z])]
    account = paper.simulate(lists, _bars(), capital=100_000, top_n=2)["account"]
    assert set(account.holdings) == {X, Y}
    plan = paper.simulate([_doc(S[6], ["920001", Z, W])], _bars(), capital=100_000, top_n=2)["plan"]
    assert plan is not None and [b["symbol"] for b in plan["buys"]] == [Z, W]


def test_job_withholds_the_sheet_when_the_last_session_is_half_synced(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Session(engine) as session:
        session.execute(
            delete(DailyBar).where(DailyBar.trade_date == S[-1], DailyBar.symbol.in_([Z, W]))
        )
        session.commit()

    @contextmanager
    def local_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr(job, "get_session", local_session)
    root = tmp_path / "picks"
    for i, doc in enumerate(LISTS):
        path = root / "lists" / "A" / f"A-2026-W{37 + i}-{doc['as_of'].replace('-', '')}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
    notes = tmp_path / "vault"
    now = datetime(2026, 9, 18, 12, 30, tzinfo=UTC)  # Friday 20:30 CST
    stats = job.run_stock_pick_actions(
        now=now, output_dir=root, note_dir=notes, capital=100_000, top_n=2
    )
    assert stats["withheld"]["bars"] == 3 and stats["withheld"]["previous_bars"] == 5
    assert "json" not in stats and not list((root / "paper").glob("*.json"))
    text = (notes / "2026-09-18.md").read_text(encoding="utf-8")
    assert "数据不全，暂不出动作单" in text and "只同步了 3 条" in text
