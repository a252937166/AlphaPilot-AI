"""Status view of the weekly stock-pick lists: since-entry moves, today's moves, severe flags."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alphapilot.db.models import Base, DailyBar, DomainEvent, Security
from alphapilot.services import stock_pick_status as status_mod
from alphapilot.services.severe_disclosure import SEVERE_EVENT_TYPE
from alphapilot.services.stock_picks import CANDIDATES

AS_OF = date(2026, 9, 10)
ENTRY = date(2026, 9, 11)
LATER = date(2026, 9, 14)
SYMBOLS = [f"6000{i:02d}" for i in range(20)]


def _bar(symbol: str, day: date, open_: float, close: float) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        trade_date=day,
        open=open_,
        high=max(open_, close),
        low=min(open_, close),
        close=close,
        volume=1.0,
        amount=1.0,
        source="test",
        ingested_at=datetime(2026, 9, 14, tzinfo=UTC),
    )


def _write_lists(root: Path) -> None:
    members = [
        {
            "symbol": s,
            "rank": i + 1,
            "score": 1 - i / 100,
            "decile": 9 if i < 2 else 5,
            "close": 10.0,
        }
        for i, s in enumerate(SYMBOLS)
    ]
    for name in CANDIDATES:
        doc = {
            "schema": "alphapilot.stock-picks.list.v1",
            "candidate": name,
            "as_of": AS_OF.isoformat(),
            "iso_week": "2026-W37",
            "universe_n": len(SYMBOLS),
            "top_decile_n": 2,
            "top_decile_symbols": SYMBOLS[:2],
            "top20": [{**m, "name": None, "board": None, "industry": None} for m in members],
            "members": members,
        }
        path = root / "lists" / name / f"{name}-2026-W37-20260910.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture
def engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'status.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for i, s in enumerate(SYMBOLS):
            # entry open 10, close on the later session 10 + i/100 (so symbol 0 is flat, others up)
            session.add_all(
                [
                    _bar(s, AS_OF, 9.9, 10.0),
                    _bar(s, ENTRY, 10.0, 10.0 + i / 200),
                    _bar(s, LATER, 10.0 + i / 200, 10.0 + i / 100),
                ]
            )
            session.add(Security(symbol=s, name=f"股{i:02d}", board="主板", industry="C39电子"))
        session.add(
            DomainEvent(
                symbol=SYMBOLS[1],
                event_type=SEVERE_EVENT_TYPE,
                direction=-1.0,
                strength=0.9,
                title="关于收到立案告知书的公告",
                source_ref="news:1",
                occurred_at=datetime(2026, 9, 12, 8, 0, tzinfo=UTC),
            )
        )
        session.commit()
    return engine


def test_status_from_bars_and_live_quotes(engine, tmp_path: Path) -> None:
    root = tmp_path / "picks"
    _write_lists(root)
    with Session(engine) as session:
        bars_view = status_mod.build_status(session, root, quote_fn=None)
    market = bars_view["market"]
    assert market["entry_date"] == ENTRY.isoformat() and market["quote_source"] == "bars"
    assert market["n"] == len(SYMBOLS)
    # symbol i: last close 10 + i/100 vs entry open 10 -> since = i/1000; median over 0..19 = 0.0095
    assert market["since_median"] == pytest.approx(0.0095, abs=1e-6)
    block = bars_view["candidates"]["A"]
    assert block["top_decile"]["n"] == 2 and block["top20"]["n"] == 20
    assert block["top_decile"]["since_mean"] == pytest.approx(0.0005, abs=1e-6)
    assert block["flagged"] == {SYMBOLS[1]: "关于收到立案告知书的公告"}
    assert block["rows"][1]["flagged"] and block["rows"][0]["flagged"] is None
    assert block["rows"][0]["name"] == "股00"

    def live(symbols: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            {"last": [11.0] * len(symbols), "prev_close": [10.0] * len(symbols)}, index=symbols
        )

    with Session(engine) as session:
        live_view = status_mod.build_status(session, root, quote_fn=live)
    assert live_view["market"]["quote_source"] == "live"
    assert live_view["market"]["today_median"] == pytest.approx(0.1)
    assert live_view["candidates"]["B"]["top_decile"]["since_mean"] == pytest.approx(0.1)

    def broken(symbols: list[str]) -> pd.DataFrame:
        raise RuntimeError("OpenD down")

    with Session(engine) as session:
        fallback = status_mod.build_status(session, root, quote_fn=broken)
    assert fallback["market"]["quote_source"] == "bars" and "OpenD down" in fallback["quote_error"]
    text = status_mod.render(fallback)
    assert "入场 2026-09-11" in text and "⚠" in text and "pending" in text


def test_status_without_lists(engine, tmp_path: Path) -> None:
    with Session(engine) as session:
        empty = status_mod.build_status(session, tmp_path / "none", quote_fn=None)
    assert empty["note"] == "no lists yet" and status_mod.render(empty) == "no lists yet"
