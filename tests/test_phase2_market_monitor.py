from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.core.config import Settings
from alphapilot.db.models import (
    Base,
    DomainEvent,
    MarketSentiment,
    MarketSnapshotAgg,
    SectorSnapshot,
    WatchlistItem,
)
from alphapilot.llm.client import LLMUnavailable
from alphapilot.main import app
from alphapilot.services import market_monitor

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
TARGET_DATE = date(2026, 7, 22)
NOW = datetime(2026, 7, 22, 17, 0, tzinfo=MARKET_TIMEZONE)
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")


def _engine(tmp_path: Path, name: str) -> Any:
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return engine


def _utc_at(
    trade_date: date,
    hour: int,
    minute: int,
    second: int = 0,
) -> datetime:
    return datetime(
        trade_date.year,
        trade_date.month,
        trade_date.day,
        hour,
        minute,
        second,
        tzinfo=MARKET_TIMEZONE,
    ).astimezone(UTC)


def _snapshot(
    *,
    ts: datetime,
    advancers: int = 60,
    decliners: int = 40,
    limit_up: int = 10,
    broken_boards: int = 0,
    total_amount: float = 100.0,
) -> MarketSnapshotAgg:
    return MarketSnapshotAgg(
        ts=ts,
        advancers=advancers,
        decliners=decliners,
        unchanged=0,
        limit_up=limit_up,
        limit_down=0,
        broken_boards=broken_boards,
        up_gt4=0,
        down_gt4=0,
        total_amount=total_amount,
        avg_change_pct=0.0,
        median_change_pct=0.0,
        source="test",
    )


def _sentiment(
    snapshot: MarketSnapshotAgg,
    *,
    label: str,
) -> MarketSentiment:
    assert snapshot.id is not None
    return MarketSentiment(
        source_snapshot_id=snapshot.id,
        ts=snapshot.ts,
        score=50.0,
        breadth_sub=50.0,
        limitup_sub=50.0,
        volume_sub=50.0,
        volatility_sub=50.0,
        label=label,
        model_version="sentiment-v1.0.0",
        details={},
    )


def _capital_event(
    *,
    symbol: str,
    ts: datetime,
    suffix: str,
    summary: str = "成交额显著放大",
) -> DomainEvent:
    return DomainEvent(
        symbol=symbol,
        event_type="capital_anomaly",
        direction=0.5,
        strength=0.7,
        title=f"{symbol} 盘中资金异动",
        summary=summary,
        source_ref=f"test:{suffix}",
        occurred_at=ts,
        ingested_at=ts,
    )


def _assert_item_contract(items: list[dict[str, object]]) -> None:
    for item in items:
        assert set(item) == {"ts", "text", "level"}
        assert item["level"] in {"info", "warn"}
        assert isinstance(item["text"], str)
        assert CHINESE_RE.search(str(item["text"]))
        parsed = datetime.fromisoformat(str(item["ts"]))
        assert parsed.tzinfo is not None


@pytest.mark.parametrize("current_amount", [105.0, 95.0])
def test_volume_change_triggers_at_positive_and_negative_five_percent(
    tmp_path: Path,
    current_amount: float,
) -> None:
    engine = _engine(tmp_path, f"volume-{current_amount}.db")
    with Session(engine) as session:
        session.add_all(
            [
                _snapshot(ts=_utc_at(TARGET_DATE, 9, 30), total_amount=100.0),
                _snapshot(ts=_utc_at(TARGET_DATE, 9, 31), total_amount=current_amount),
            ]
        )
        session.commit()
        items = market_monitor.build_feed(session, now=NOW)

    assert len(items) == 1
    assert "5" in str(items[0]["text"])
    assert any(word in str(items[0]["text"]) for word in ("量能", "成交额"))
    _assert_item_contract(items)


@pytest.mark.parametrize("current_amount", [104.99, 95.01])
def test_volume_change_below_five_percent_does_not_trigger(
    tmp_path: Path,
    current_amount: float,
) -> None:
    engine = _engine(tmp_path, f"volume-below-{current_amount}.db")
    with Session(engine) as session:
        session.add_all(
            [
                _snapshot(ts=_utc_at(TARGET_DATE, 9, 30), total_amount=100.0),
                _snapshot(ts=_utc_at(TARGET_DATE, 9, 31), total_amount=current_amount),
            ]
        )
        session.commit()
        assert market_monitor.build_feed(session, now=NOW) == []


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        ((60, 40), (40, 60)),
        ((40, 60), (60, 40)),
    ],
)
def test_breadth_strict_sign_reversal_triggers_in_both_directions(
    tmp_path: Path,
    previous: tuple[int, int],
    current: tuple[int, int],
) -> None:
    engine = _engine(tmp_path, f"breadth-{previous[0]}.db")
    with Session(engine) as session:
        session.add_all(
            [
                _snapshot(
                    ts=_utc_at(TARGET_DATE, 10, 0),
                    advancers=previous[0],
                    decliners=previous[1],
                ),
                _snapshot(
                    ts=_utc_at(TARGET_DATE, 10, 1),
                    advancers=current[0],
                    decliners=current[1],
                ),
            ]
        )
        session.commit()
        items = market_monitor.build_feed(session, now=NOW)

    assert len(items) == 1
    assert "宽度" in str(items[0]["text"])


@pytest.mark.parametrize("middle", [(50, 50), (60, 40)])
def test_breadth_touching_zero_is_not_a_strict_reversal(
    tmp_path: Path,
    middle: tuple[int, int],
) -> None:
    engine = _engine(tmp_path, f"breadth-zero-{middle[0]}.db")
    with Session(engine) as session:
        session.add_all(
            [
                _snapshot(ts=_utc_at(TARGET_DATE, 10, 0), advancers=60, decliners=40),
                _snapshot(
                    ts=_utc_at(TARGET_DATE, 10, 1),
                    advancers=middle[0],
                    decliners=middle[1],
                ),
            ]
        )
        session.commit()
        assert market_monitor.build_feed(session, now=NOW) == []


@pytest.mark.parametrize(("delta", "expected"), [(5, 1), (-5, 1), (4, 0), (-4, 0)])
def test_limit_up_absolute_change_threshold(
    tmp_path: Path,
    delta: int,
    expected: int,
) -> None:
    engine = _engine(tmp_path, f"limit-up-{delta}.db")
    with Session(engine) as session:
        session.add_all(
            [
                _snapshot(ts=_utc_at(TARGET_DATE, 10, 10), limit_up=10),
                _snapshot(ts=_utc_at(TARGET_DATE, 10, 11), limit_up=10 + delta),
            ]
        )
        session.commit()
        items = market_monitor.build_feed(session, now=NOW)

    assert len(items) == expected
    if items:
        assert "涨停" in str(items[0]["text"])


def test_broken_board_rate_uses_full_attempt_denominator_and_only_upward_crossing(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "broken-board.db")
    with Session(engine) as session:
        session.add_all(
            [
                # 3 / (7 + 3) == 30%, the non-triggering boundary.
                _snapshot(
                    ts=_utc_at(TARGET_DATE, 10, 20),
                    limit_up=7,
                    broken_boards=3,
                ),
                # 4 / (10 + 4) == 28.57%; broken / limit_up would be 40%,
                # so this row distinguishes the intended denominator.
                _snapshot(
                    ts=_utc_at(TARGET_DATE, 10, 21),
                    limit_up=10,
                    broken_boards=4,
                ),
                # 4 / (6 + 4) == 40%, crossing upward from <= 30%.
                _snapshot(
                    ts=_utc_at(TARGET_DATE, 10, 22),
                    limit_up=6,
                    broken_boards=4,
                ),
                # Remains above 30%; must not create another minute-level item.
                _snapshot(
                    ts=_utc_at(TARGET_DATE, 10, 23),
                    limit_up=5,
                    broken_boards=5,
                ),
            ]
        )
        session.commit()
        items = market_monitor.build_feed(session, now=NOW)

    broken_items = [item for item in items if "炸板" in str(item["text"])]
    assert len(broken_items) == 1
    assert "40" in str(broken_items[0]["text"])
    assert broken_items[0]["level"] == "warn"


def test_sector_leader_change_uses_rank_then_strength(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "sector-leader.db")
    with Session(engine) as session:
        session.add_all(
            [
                SectorSnapshot(
                    as_of=_utc_at(TARGET_DATE, 10, 30),
                    payload=[
                        {"rank": 2, "plate_code": "SH.A", "plate_name": "甲", "strength": 9.9},
                        {"rank": 1, "plate_code": "SH.B", "plate_name": "乙", "strength": 7.0},
                        {"rank": 1, "plate_code": "SH.C", "plate_name": "丙", "strength": 8.0},
                    ],
                    source="test",
                ),
                SectorSnapshot(
                    as_of=_utc_at(TARGET_DATE, 10, 31),
                    payload=[
                        {"rank": 1, "plate_code": "SH.D", "plate_name": "丁", "strength": 8.5},
                        {"rank": 2, "plate_code": "SH.C", "plate_name": "丙", "strength": 9.5},
                    ],
                    source="test",
                ),
            ]
        )
        session.commit()
        items = market_monitor.build_feed(session, now=NOW)

    assert len(items) == 1
    assert "丙" in str(items[0]["text"])
    assert "丁" in str(items[0]["text"])
    assert items[0]["level"] == "info"


@pytest.mark.parametrize(
    ("before", "after", "expected_level"),
    [
        ("中性", "偏弱", "warn"),
        ("偏弱", "偏强", "info"),
    ],
)
def test_sentiment_label_shift_uses_directional_level(
    tmp_path: Path,
    before: str,
    after: str,
    expected_level: str,
) -> None:
    engine = _engine(tmp_path, f"sentiment-{expected_level}.db")
    with Session(engine) as session:
        first = _snapshot(ts=_utc_at(TARGET_DATE, 10, 40))
        second = _snapshot(ts=_utc_at(TARGET_DATE, 10, 41))
        session.add_all([first, second])
        session.flush()
        session.add_all(
            [
                _sentiment(first, label=before),
                _sentiment(second, label=after),
            ]
        )
        session.commit()
        items = market_monitor.build_feed(session, now=NOW)

    assert len(items) == 1
    assert before in str(items[0]["text"])
    assert after in str(items[0]["text"])
    assert items[0]["level"] == expected_level


def test_capital_anomaly_only_includes_current_day_watchlist_events(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "capital-anomaly.db")
    with Session(engine) as session:
        session.add(WatchlistItem(symbol="600519", display_name="贵州茅台"))
        session.add_all(
            [
                _capital_event(
                    symbol="600519",
                    ts=_utc_at(TARGET_DATE, 11, 0),
                    suffix="selected",
                ),
                _capital_event(
                    symbol="000001",
                    ts=_utc_at(TARGET_DATE, 11, 1),
                    suffix="not-watchlist",
                ),
                _capital_event(
                    symbol="600519",
                    ts=_utc_at(TARGET_DATE - timedelta(days=1), 11, 2),
                    suffix="yesterday",
                ),
                DomainEvent(
                    symbol="600519",
                    event_type="disclosure",
                    direction=0.0,
                    strength=0.5,
                    title="普通公告",
                    summary="不属于资金异动",
                    source_ref="test:disclosure",
                    occurred_at=_utc_at(TARGET_DATE, 11, 3),
                    ingested_at=_utc_at(TARGET_DATE, 11, 3),
                ),
            ]
        )
        session.commit()
        items = market_monitor.build_feed(session, now=NOW)

    assert len(items) == 1
    assert "600519" in str(items[0]["text"])
    assert "成交额显著放大" in str(items[0]["text"])
    assert items[0]["level"] == "warn"


def test_market_pairs_never_compare_across_shanghai_days(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "day-boundary.db")
    with Session(engine) as session:
        session.add_all(
            [
                _snapshot(
                    ts=_utc_at(TARGET_DATE - timedelta(days=1), 15, 0),
                    total_amount=100.0,
                    advancers=80,
                    decliners=20,
                    limit_up=5,
                ),
                _snapshot(
                    ts=_utc_at(TARGET_DATE, 9, 30),
                    total_amount=1_000.0,
                    advancers=20,
                    decliners=80,
                    limit_up=50,
                    broken_boards=30,
                ),
            ]
        )
        session.commit()
        assert market_monitor.build_feed(session, now=NOW) == []


def test_after_close_returns_current_day_stored_feed(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "after-close.db")
    with Session(engine) as session:
        session.add_all(
            [
                _snapshot(ts=_utc_at(TARGET_DATE, 15, 0), total_amount=100.0),
                _snapshot(ts=_utc_at(TARGET_DATE, 15, 1), total_amount=110.0),
            ]
        )
        session.commit()
        items = market_monitor.build_feed(session, now=NOW)

    assert len(items) == 1
    item_date = datetime.fromisoformat(str(items[0]["ts"])).astimezone(MARKET_TIMEZONE).date()
    assert item_date == TARGET_DATE


def test_feed_excludes_rows_later_than_observation_time(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "future-row.db")
    with Session(engine) as session:
        session.add(WatchlistItem(symbol="600519", display_name="贵州茅台"))
        session.add_all(
            [
                _capital_event(
                    symbol="600519",
                    ts=_utc_at(TARGET_DATE, 16, 59),
                    suffix="before-now",
                ),
                _capital_event(
                    symbol="600519",
                    ts=_utc_at(TARGET_DATE, 17, 1),
                    suffix="after-now",
                ),
            ]
        )
        session.commit()
        items = market_monitor.build_feed(session, now=NOW)

    assert len(items) == 1
    assert items[0]["ts"] == _utc_at(TARGET_DATE, 16, 59).isoformat()


def test_feed_is_newest_first_and_limit_applies_after_global_merge(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "limit.db")
    with Session(engine) as session:
        session.add(WatchlistItem(symbol="600519", display_name="贵州茅台"))
        session.add_all(
            [
                _capital_event(
                    symbol="600519",
                    ts=_utc_at(TARGET_DATE, 9, 31 + offset),
                    suffix=f"limit-{offset}",
                    summary=f"第{offset}条异动",
                )
                for offset in range(4)
            ]
        )
        session.commit()
        items = market_monitor.build_feed(session, limit=2, now=NOW)

    assert len(items) == 2
    timestamps = [datetime.fromisoformat(str(item["ts"])) for item in items]
    assert timestamps == sorted(timestamps, reverse=True)
    assert "第3条异动" in str(items[0]["text"])
    assert "第2条异动" in str(items[1]["text"])


def test_no_data_returns_empty_feed(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "empty.db")
    with Session(engine) as session:
        assert market_monitor.build_feed(session, now=NOW) == []


def test_monitor_feed_api_returns_envelope_and_validates_limit(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "api.db")
    with Session(engine) as session:
        session.add(WatchlistItem(symbol="600519", display_name="贵州茅台"))
        session.add(
            _capital_event(
                symbol="600519",
                ts=datetime.now(UTC),
                suffix="api",
            )
        )
        session.commit()

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[db_session_dependency] = override_session
    try:
        with TestClient(app) as client:
            response = client.get("/v1/market/monitor-feed?limit=1")
            zero = client.get("/v1/market/monitor-feed?limit=0")
            too_large = client.get("/v1/market/monitor-feed?limit=101")
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert len(response.json()["items"]) == 1
    assert zero.status_code == 422
    assert too_large.status_code == 422


def _polish_settings() -> Settings:
    return Settings(
        llm_polish_feed=True,
        llm_base_url="https://llm.invalid/v1",
        llm_api_key="test-only",
    )


def _seed_polish_events(session: Session) -> None:
    session.add(WatchlistItem(symbol="600519", display_name="贵州茅台"))
    session.add_all(
        [
            _capital_event(
                symbol="600519",
                ts=_utc_at(TARGET_DATE, 13, minute),
                suffix=f"polish-{minute}",
                summary=f"原始事实{minute}",
            )
            for minute in (1, 2, 3)
        ]
    )
    session.commit()


def test_optional_llm_polishes_all_items_in_one_batch_without_changing_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "polish.db")
    calls: list[dict[str, Any]] = []

    def fake_chat_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append({"args": args, "kwargs": kwargs})
        request = json.loads(str(args[2]))
        assert len(request["items"]) == 3
        return {
            "items": [
                {
                    "index": item["index"],
                    "text": str(item["text"]).replace("触发资金异动，", "资金异动："),
                }
                for item in request["items"]
            ]
        }

    monkeypatch.setattr(market_monitor, "chat_json", fake_chat_json)
    with Session(engine) as session:
        _seed_polish_events(session)
        raw = market_monitor.build_feed(
            session,
            now=NOW,
            settings=Settings(llm_polish_feed=False),
        )
        polished = market_monitor.build_feed(
            session,
            now=NOW,
            settings=_polish_settings(),
        )

    assert len(calls) == 1
    assert calls[0]["args"][0] == "market_feed_polish"
    assert [item["text"] for item in polished] == [
        str(item["text"]).replace("触发资金异动，", "资金异动：") for item in raw
    ]
    assert [item["ts"] for item in polished] == [item["ts"] for item in raw]
    assert [item["level"] for item in polished] == [item["level"] for item in raw]


@pytest.mark.parametrize(
    "invalid_result",
    [
        {
            "items": [
                {"index": 0, "text": "第一条事实"},
                {"index": 0, "text": "重复序号"},
                {"index": 2, "text": "第三条事实"},
            ]
        },
        {
            "items": [
                {"index": 0, "text": "English only"},
                {"index": 1, "text": "第二条事实"},
                {"index": 2, "text": "第三条事实"},
            ]
        },
        {"items": [{"index": 0, "text": "只有一条"}]},
        {
            "items": [
                {"index": 0, "text": "自选股 000001 资金异动：原始事实3。"},
                {"index": 1, "text": "自选股 600519 资金异动：原始事实2。"},
                {"index": 2, "text": "自选股 600519 资金异动：原始事实1。"},
            ]
        },
        {
            "items": [
                {"index": 99, "text": "自选股 600519 资金异动：原始事实3。"},
                {"index": 1, "text": "自选股 600519 资金异动：原始事实2。"},
                {"index": 2, "text": "自选股 600519 资金异动：原始事实1。"},
            ]
        },
        {
            "items": [
                {"index": 0, "text": "自选股 600519 资金异动：资金流向发生反转。"},
                {"index": 1, "text": "自选股 600519 资金异动：原始事实2。"},
                {"index": 2, "text": "自选股 600519 资金异动：原始事实1。"},
            ]
        },
    ],
)
def test_invalid_llm_batch_falls_back_to_the_entire_rule_feed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid_result: dict[str, Any],
) -> None:
    engine = _engine(tmp_path, "invalid-polish.db")
    calls = 0

    def invalid_chat_json(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return invalid_result

    monkeypatch.setattr(market_monitor, "chat_json", invalid_chat_json)
    with Session(engine) as session:
        _seed_polish_events(session)
        raw = market_monitor.build_feed(
            session,
            now=NOW,
            settings=Settings(llm_polish_feed=False),
        )
        result = market_monitor.build_feed(
            session,
            now=NOW,
            settings=_polish_settings(),
        )

    assert calls == 1
    assert result == raw


def test_llm_unavailable_falls_back_and_empty_feed_never_calls_llm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "unavailable-polish.db")
    calls = 0

    def unavailable(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise LLMUnavailable("offline")

    monkeypatch.setattr(market_monitor, "chat_json", unavailable)
    with Session(engine) as session:
        _seed_polish_events(session)
        raw = market_monitor.build_feed(
            session,
            now=NOW,
            settings=Settings(llm_polish_feed=False),
        )
        fallback = market_monitor.build_feed(
            session,
            now=NOW,
            settings=_polish_settings(),
        )

    assert calls == 1
    assert fallback == raw

    empty_engine = _engine(tmp_path, "empty-polish.db")
    with Session(empty_engine) as empty_session:
        assert (
            market_monitor.build_feed(
                empty_session,
                now=NOW,
                settings=_polish_settings(),
            )
            == []
        )
    assert calls == 1
