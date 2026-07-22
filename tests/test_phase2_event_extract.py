from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.db.models import Base, Disclosure, DomainEvent
from alphapilot.llm.client import LLMUnavailable
from alphapilot.llm.prompts import EVENT_EXTRACT
from alphapilot.services import event_extract
from alphapilot.services.event_extract import (
    EVENT_SCHEMA,
    EVENT_SUBTYPE_LABELS,
    extract_disclosure_event,
)


def _add_disclosure(session: Session, title: str, *, suffix: str = "1") -> Disclosure:
    disclosure = Disclosure(
        symbol="600519",
        title=title,
        url=f"https://example.test/{suffix}.pdf",
        category="测试公告",
        published_at=datetime(2026, 7, 22, 1, tzinfo=UTC),
        source="cninfo",
    )
    session.add(disclosure)
    session.flush()
    return disclosure


def test_valid_llm_result_upgrades_existing_neutral_event_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'llm-event.db'}")
    Base.metadata.create_all(engine)
    settings = Settings(
        llm_base_url="https://llm.example.test/v1",
        llm_api_key="test-only-key",
    )

    with Session(engine, expire_on_commit=False) as session:
        disclosure = _add_disclosure(session, "公司收到重大项目中标通知书")
        legacy = DomainEvent(
            symbol=disclosure.symbol,
            event_type="disclosure",
            direction=0.0,
            strength=0.5,
            title=disclosure.title,
            summary=disclosure.category,
            source_ref=f"disclosure:{disclosure.id}",
            occurred_at=disclosure.published_at,
        )
        session.add(legacy)
        session.flush()
        legacy_id = legacy.id

        calls = 0

        def fake_chat_json(
            purpose: str,
            system: str,
            user: str,
            schema: dict[str, Any],
            **kwargs: Any,
        ) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            assert purpose == "event_extract"
            assert system == EVENT_EXTRACT
            assert user == disclosure.title
            assert schema is EVENT_SCHEMA
            assert kwargs["settings"] is settings
            assert kwargs["session"] is session
            return {
                "event_type": "contract",
                "direction": 0.8,
                "strength": 0.75,
                "horizon_days": 30,
                "summary": "公司收到重大项目中标通知书。",
                "source_quote": "重大项目中标通知书",
            }

        monkeypatch.setattr(event_extract, "chat_json", fake_chat_json)

        first = extract_disclosure_event(session, disclosure, settings=settings)
        second = extract_disclosure_event(session, disclosure, settings=settings)

        assert first is not None
        assert second is not None
        assert first.id == second.id == legacy_id
        assert session.scalar(select(func.count()).select_from(DomainEvent)) == 1
        assert second.event_type == "disclosure"
        assert second.direction == pytest.approx(0.8)
        assert second.strength == pytest.approx(0.75)
        assert second.summary == "合同｜公司收到重大项目中标通知书。"
        assert second.source_ref == f"disclosure:{disclosure.id}"
        assert second._extraction_source == "llm"
        assert calls == 2


def test_new_disclosure_is_classified_before_its_first_flush(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'preflush-order.db'}")
    Base.metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as session:
        disclosure = Disclosure(
            symbol="600519",
            title="公司收到重大项目中标通知书",
            url="https://example.test/preflush.pdf",
            category="重大合同",
            published_at=datetime(2026, 7, 22, 1, tzinfo=UTC),
            source="cninfo",
        )
        session.add(disclosure)

        def fake_chat_json(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            assert disclosure.id is None
            return {
                "event_type": "contract",
                "direction": 0.7,
                "strength": 0.8,
                "summary": "公司收到重大项目中标通知书。",
                "source_quote": "重大项目中标通知书",
            }

        monkeypatch.setattr(event_extract, "chat_json", fake_chat_json)
        event = extract_disclosure_event(session, disclosure)

        assert disclosure.id is not None
        assert event is not None
        assert event.source_ref == f"disclosure:{disclosure.id}"
        assert event.summary == "合同｜公司收到重大项目中标通知书。"


def test_invalid_source_quote_uses_keyword_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'quote-fallback.db'}")
    Base.metadata.create_all(engine)

    monkeypatch.setattr(
        event_extract,
        "chat_json",
        lambda *_args, **_kwargs: {
            "event_type": "regulation",
            "direction": -0.9,
            "strength": 0.9,
            "summary": "模型引用了标题中不存在的事实。",
            "source_quote": "不存在的行政处罚",
        },
    )

    with Session(engine, expire_on_commit=False) as session:
        disclosure = _add_disclosure(session, "关于以集中竞价方式回购股份的公告")
        event = extract_disclosure_event(session, disclosure)

        assert event is not None
        assert event.event_type == "disclosure"
        assert event.direction == pytest.approx(0.5)
        assert event.strength == pytest.approx(0.5)
        assert event.summary is not None and event.summary.startswith("回购｜")
        assert "回购" in event.summary
        assert event._extraction_source == "rule"


def test_english_llm_summary_uses_chinese_rule_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'english-summary.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        event_extract,
        "chat_json",
        lambda *_args, **_kwargs: {
            "event_type": "buyback",
            "direction": 0.8,
            "strength": 0.8,
            "summary": "The company plans a share repurchase.",
            "source_quote": "回购",
        },
    )

    with Session(engine, expire_on_commit=False) as session:
        disclosure = _add_disclosure(session, "关于回购股份的公告")
        event = extract_disclosure_event(session, disclosure)

        assert event is not None
        assert event.summary is not None and event.summary.startswith("回购｜")
        assert event._extraction_source == "rule"


@pytest.mark.parametrize(
    ("title", "event_type", "direction"),
    [
        ("2026年度业绩预增公告", "earnings", 0.5),
        ("重大项目中标公告", "contract", 0.5),
        ("关于回购股份的公告", "buyback", 0.5),
        ("年度权益分红实施公告", "dividend", 0.5),
        ("2026年度业绩预减公告", "earnings", -0.5),
        ("收到交易所问询函的公告", "regulation", -0.5),
        ("收到行政处罚决定书", "regulation", -0.5),
        ("股东减持计划公告", "holder_change", -0.5),
        ("董事会会议决议公告", "other", 0.0),
    ],
)
def test_llm_unavailable_always_persists_deterministic_rule_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    title: str,
    event_type: str,
    direction: float,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / f'{event_type}-{direction}.db'}")
    Base.metadata.create_all(engine)

    def unavailable(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise LLMUnavailable("offline")

    monkeypatch.setattr(event_extract, "chat_json", unavailable)

    with Session(engine, expire_on_commit=False) as session:
        disclosure = _add_disclosure(session, title, suffix=title)
        event = extract_disclosure_event(session, disclosure)

        assert event is not None
        assert event.event_type == "disclosure"
        assert event.direction == pytest.approx(direction)
        assert event.strength == pytest.approx(0.5)
        label = EVENT_SUBTYPE_LABELS[event_type]
        assert event.summary is not None and event.summary.startswith(f"{label}｜")
        assert event.source_ref == f"disclosure:{disclosure.id}"
        assert event._extraction_source == "rule"
        assert session.scalar(select(func.count()).select_from(DomainEvent)) == 1
