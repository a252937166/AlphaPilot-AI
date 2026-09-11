from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from alphapilot.db.models import Base, DomainEvent, NewsItem, Notification
from alphapilot.jobs import severe_disclosure_screen as screen
from alphapilot.services.severe_disclosure import (
    SEVERE_EVENT_TYPE,
    classify_severe_disclosure,
)

# Real CNInfo titles from 2026-08. The left column is the expected subtype.
SEVERE_TITLES = [
    ("investigation", "关于收到中国证券监督管理委员会立案告知书的公告"),
    ("investigation", "关于公司控股股东、实际控制人收到中国证券监督管理委员会立案告知书的公告"),
    ("investigation", "关于公司立案调查进展暨风险提示公告"),
    ("investigation", "关于公司被立案事项的说明公告"),
    (
        "investigation",
        "关于立案调查进展暨公司未在规定期限内披露定期报告暨股票可能被终止上市的第三次风险提示公告",
    ),
    ("penalty_notice", "关于公司及相关人员收到河北监管局行政处罚事先告知书的公告"),
    ("penalty_notice", "关于收到中国证券监督管理委员会浙江监管局行政处罚事先告知书的公告"),
    ("delisting_risk", "关于公司股票可能被实施重大违法强制退市的第十五次风险提示公告"),
    ("delisting_risk", "关于公司股票存在可能因市值被终止上市的第一次风险提示公告"),
    ("delisting_risk", "关于公司股票继续实施财务类退市风险警示并实施其他风险警示的公告"),
    (
        "delisting_risk",
        "清越科技关于公司股票存在可能因股价低于1元而终止上市的第二次风险提示"
        "暨可能被实施重大违法强制退市的风险提示公告",
    ),
    ("penalty_decision", "关于收到《行政处罚决定书》的公告"),
]
# Titles that mention the same words but are not the event.
BENIGN_TITLES = [
    "关于最近五年未被证券监管部门和证券交易所采取监管措施或处罚的公告",
    "关于最近五年被证券监管部门和交易所采取监管措施或处罚及整改情况的公告",
    "国浩律师(杭州)事务所关于浙江大立科技股份有限公司申请撤销退市风险警示的法律意见书",
    "关于公司股票交易撤销退市风险警示及其他风险警示暨停复牌的公告",
    "关于向深圳证券交易所申请撤销退市风险警示及其他风险警示的进展公告",
    "关于收到控股股东及实际控制人上诉案件受理立案暨诉讼进展的公告",
    "关于2025年年度报告的信息披露监管问询函的回复公告",
    "关于深圳市民德电子科技股份有限公司申请向特定对象发行股票的审核问询函之回复报告",
    "凯盛新能关于河南证监局行政监管措施决定书的整改报告的公告",
    "关于全资子公司增资扩股引入产业基金暨关联交易的公告",
    "",
]


@pytest.mark.parametrize(("subtype", "title"), SEVERE_TITLES)
def test_severe_titles_are_recognised(subtype: str, title: str) -> None:
    found = classify_severe_disclosure(title)
    assert found is not None, title
    assert found.subtype == subtype
    assert found.direction < 0
    assert 0 < found.strength <= 1


@pytest.mark.parametrize("title", BENIGN_TITLES)
def test_routine_and_lifted_titles_are_not_events(title: str) -> None:
    assert classify_severe_disclosure(title) is None


def test_severity_order_and_notification_threshold() -> None:
    investigation = classify_severe_disclosure("关于公司立案调查进展暨退市风险提示公告")
    decision = classify_severe_disclosure("关于收到《行政处罚决定书》的公告")
    assert investigation is not None and investigation.subtype == "investigation"
    assert decision is not None
    # A formal decision is priced at the advance notice: recorded, not notified.
    assert decision.strength < 0.6 < investigation.strength
    assert decision.direction > -0.5 <= investigation.direction + 1


def _news(news_id: int, symbol: str | None, title: str, available_time: datetime) -> NewsItem:
    return NewsItem(
        id=news_id,
        source="cninfo",
        symbol=symbol,
        title=title,
        url=f"https://static.cninfo.com.cn/finalpage/2026-08-10/{news_id}.PDF",
        published_at=available_time,
        available_time=available_time,
        content_hash=f"hash-{news_id}",
        raw_payload={"secCode": symbol},
    )


def test_screen_emits_pit_events_once_and_only_notifies_severe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'screen.db'}")
    Base.metadata.create_all(engine)

    @contextmanager
    def local_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session
            session.commit()

    monkeypatch.setattr(screen, "get_session", local_session)
    now = datetime(2026, 8, 11, 8, 0, tzinfo=UTC)
    fresh = now - timedelta(hours=3)
    stale = now - timedelta(days=30)
    with Session(engine) as session:
        session.add_all(
            [
                _news(1, "600363", "关于收到中国证券监督管理委员会立案告知书的公告", fresh),
                _news(2, "300716", "关于收到《行政处罚决定书》的公告", fresh),
                _news(
                    3,
                    "688683",
                    "关于公司最近五年未被证券监管部门和证券交易所采取监管措施或处罚的公告",
                    fresh,
                ),
                _news(4, None, "关于公司立案调查进展暨风险提示公告", fresh),
                _news(
                    5, "002731", "关于公司股票存在可能因市值被终止上市的第一次风险提示公告", stale
                ),
                # Backfilled by a poller catch-up: ingested now, published a month ago.
                NewsItem(
                    id=6,
                    source="cninfo",
                    symbol="688121",
                    title="关于公司立案调查进展暨退市风险提示公告",
                    url="https://static.cninfo.com.cn/finalpage/2026-07-12/6.PDF",
                    published_at=stale,
                    available_time=fresh,
                    content_hash="hash-6",
                    raw_payload={"secCode": "688121"},
                ),
            ]
        )
        session.commit()

    first = screen.run_severe_disclosure_screen(now=now)
    assert first["scanned"] == 5  # the stale-ingested row is outside the lookback
    assert first["matched"] == 4 and first["unsymboled"] == 1
    assert first["stale_skipped"] == 1  # backfilled old announcement is not a fresh warning
    assert first["emitted"] == 2 and first["existing"] == 0
    assert first["by_subtype"] == {"investigation": 3, "penalty_decision": 1}

    with Session(engine) as session:
        events = session.scalars(
            select(DomainEvent).where(DomainEvent.event_type == SEVERE_EVENT_TYPE)
        ).all()
        by_ref = {event.source_ref: event for event in events}
        assert set(by_ref) == {"news:1", "news:2"}
        investigation = by_ref["news:1"]
        assert investigation.symbol == "600363" and investigation.direction == -1.0
        # Point-in-time: the event happens when the announcement became available.
        assert investigation.occurred_at.replace(tzinfo=UTC) == fresh
        assert "立案告知书" in (investigation.summary or "")
        notifications = session.scalars(select(Notification)).all()
        assert [item.ref_id for item in notifications] == [f"event:{investigation.id}"]
        assert notifications[0].level == "warn"

    second = screen.run_severe_disclosure_screen(now=now)
    assert second["emitted"] == 0 and second["existing"] == 2
    with Session(engine) as session:
        assert session.scalar(select(DomainEvent).where(DomainEvent.id > 2)) is None


def test_job_is_registered_with_interval_and_setting() -> None:
    from alphapilot.core.config import Settings
    from alphapilot.jobs import register_builtin_jobs
    from alphapilot.jobs.registry import JOBS

    register_builtin_jobs()
    spec = JOBS["severe_disclosure_screen"]
    assert spec.enabled_key == "severe_disclosure_screen_enabled"
    assert spec.trigger is not None and spec.misfire_grace_time == 300
    assert Settings().severe_disclosure_screen_enabled is True
