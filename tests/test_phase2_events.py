from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Barrier
from typing import Any

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from alphapilot.api.routes.events import list_events
from alphapilot.db.models import (
    Base,
    Disclosure,
    DomainEvent,
    MarketRegimeState,
)
from alphapilot.jobs import market_poll
from alphapilot.services import disclosures as disclosure_service
from alphapilot.services.dashboard import track_market_regime_change
from alphapilot.services.disclosures import sync_disclosures
from alphapilot.services.event_extract import DisclosureExtraction
from alphapilot.services.events import emit


def test_emit_deduplicates_source_ref_and_events_api_filters(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'events.db'}")
    Base.metadata.create_all(engine)
    occurred_at = datetime(2026, 7, 21, 2, tzinfo=UTC)
    with Session(engine, expire_on_commit=False) as session:
        first = emit(
            session,
            symbol="600519",
            event_type="disclosure",
            title="贵州茅台季度报告",
            source_ref="disclosure:1",
            occurred_at=occurred_at,
        )
        duplicate = emit(
            session,
            symbol="600519",
            event_type="disclosure",
            title="不应新增",
            source_ref="disclosure:1",
            occurred_at=occurred_at,
        )
        emit(
            session,
            symbol=None,
            event_type="market_regime_change",
            title="市场状态变化",
            source_ref="regime:1",
            occurred_at=occurred_at,
        )
        assert first.id == duplicate.id
        response = list_events(
            symbol="SH.600519",
            types="disclosure",
            limit=50,
            session=session,
        )

    assert response["symbol"] == "600519"
    assert response["types"] == ["disclosure"]
    assert len(response["events"]) == 1
    assert response["events"][0]["source_ref"] == "disclosure:1"


def test_disclosure_sync_emits_once_for_each_new_announcement(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'disclosures.db'}")
    Base.metadata.create_all(engine)

    class FakeCninfo:
        def announcements(
            self,
            symbol: str,
            start: date,
            end: date,
            *,
            page_size: int = 30,
        ) -> list[dict[str, Any]]:
            del symbol, start, end, page_size
            return [
                {
                    "title": "贵州茅台2026年第一季度报告",
                    "url": "https://example.test/600519-q1.pdf",
                    "category": "季度报告",
                    "published_at": datetime(2026, 4, 25, tzinfo=UTC),
                }
            ]

    classified_titles: list[str] = []

    def fake_classify(title: str) -> DisclosureExtraction:
        classified_titles.append(title)
        return DisclosureExtraction(
            subtype="other",
            direction=0.0,
            strength=0.5,
            summary=f"测试分类：{title}",
            source_quote=title,
            source="rule",
        )

    monkeypatch.setattr(disclosure_service, "classify_disclosure", fake_classify)

    with Session(engine) as session:
        first = sync_disclosures(session, FakeCninfo(), "600519")  # type: ignore[arg-type]
        second = sync_disclosures(session, FakeCninfo(), "600519")  # type: ignore[arg-type]
        assert first["inserted"] == 1
        assert second["inserted"] == 0
        assert session.scalar(select(func.count()).select_from(Disclosure)) == 1
        assert session.scalar(select(func.count()).select_from(DomainEvent)) == 1
        event = session.scalar(select(DomainEvent))
        disclosure = session.scalar(select(Disclosure))
        assert event is not None
        assert disclosure is not None
        assert event.source_ref == f"disclosure:{disclosure.id}"
        assert event.direction == 0.0
        assert classified_titles == ["贵州茅台2026年第一季度报告"]


def test_disclosure_sync_classifies_entire_batch_before_first_database_write(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'disclosure-batch.db'}")
    Base.metadata.create_all(engine)
    write_statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def track_writes(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            write_statements.append(statement)

    class FakeCninfo:
        def announcements(
            self,
            symbol: str,
            start: date,
            end: date,
            *,
            page_size: int = 30,
        ) -> list[dict[str, Any]]:
            del symbol, start, end, page_size
            published_at = datetime(2026, 7, 22, 1, tzinfo=UTC)
            return [
                {
                    "title": "重大项目中标公告",
                    "url": "https://example.test/batch-1.pdf",
                    "published_at": published_at,
                },
                {
                    "title": "交易所问询函公告",
                    "url": "https://example.test/batch-2.pdf",
                    "published_at": published_at,
                },
            ]

    classified_titles: list[str] = []

    def fake_classify(title: str) -> DisclosureExtraction:
        # The first classification must not be persisted before the second LLM
        # wait; otherwise SQLite's writer lock spans that network call.
        assert write_statements == []
        classified_titles.append(title)
        direction = 0.5 if "中标" in title else -0.5
        subtype = "contract" if direction > 0 else "regulation"
        return DisclosureExtraction(
            subtype=subtype,
            direction=direction,
            strength=0.5,
            summary=f"测试分类：{title}",
            source_quote=title,
            source="llm",
        )

    monkeypatch.setattr(disclosure_service, "classify_disclosure", fake_classify)

    with Session(engine) as session:
        result = sync_disclosures(
            session,
            FakeCninfo(),  # type: ignore[arg-type]
            "600519",
        )
        assert result["inserted"] == 2
        assert result["events_extracted"] == 2
        assert session.scalar(select(func.count()).select_from(Disclosure)) == 2
        assert session.scalar(select(func.count()).select_from(DomainEvent)) == 2

    assert classified_titles == ["重大项目中标公告", "交易所问询函公告"]
    assert write_statements


def test_concurrent_disclosure_sync_upserts_one_disclosure_and_event(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class FakeCninfo:
        def announcements(
            self,
            symbol: str,
            start: date,
            end: date,
            *,
            page_size: int = 30,
        ) -> list[dict[str, Any]]:
            del symbol, start, end, page_size
            return [
                {
                    "title": "重大项目中标公告",
                    "url": "https://example.test/concurrent.pdf",
                    "published_at": datetime(2026, 7, 22, 1, tzinfo=UTC),
                }
            ]

    def run_scenario(*, seed_disclosure: bool) -> None:
        suffix = "event" if seed_disclosure else "disclosure"
        engine = create_engine(
            f"sqlite:///{tmp_path / f'{suffix}-race.db'}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(engine)
        if seed_disclosure:
            with Session(engine) as session:
                session.add(
                    Disclosure(
                        symbol="600519",
                        title="重大项目中标公告",
                        url="https://example.test/concurrent.pdf",
                        published_at=datetime(2026, 7, 22, 1, tzinfo=UTC),
                    )
                )
                session.commit()

        classification_barrier = Barrier(2)

        def fake_classify(title: str) -> DisclosureExtraction:
            classification_barrier.wait(timeout=5)
            return DisclosureExtraction(
                subtype="contract",
                direction=0.5,
                strength=0.5,
                summary=f"测试分类：{title}",
                source_quote="中标",
                source="llm",
            )

        monkeypatch.setattr(disclosure_service, "classify_disclosure", fake_classify)

        def run_sync() -> dict[str, Any]:
            with Session(engine) as session:
                result = sync_disclosures(
                    session,
                    FakeCninfo(),  # type: ignore[arg-type]
                    "600519",
                )
                session.commit()
                return result

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: run_sync(), range(2)))

        with Session(engine) as session:
            disclosure_count = session.scalar(
                select(func.count()).select_from(Disclosure)
            )
            event_count = session.scalar(select(func.count()).select_from(DomainEvent))
            stored_event = session.scalar(select(DomainEvent))

        expected_inserts = [0, 0] if seed_disclosure else [0, 1]
        assert sorted(result["inserted"] for result in results) == expected_inserts
        assert disclosure_count == 1
        assert event_count == 1
        assert stored_event is not None
        assert stored_event.summary == "合同｜测试分类：重大项目中标公告"

    run_scenario(seed_disclosure=False)
    run_scenario(seed_disclosure=True)


def test_disclosure_sync_auto_heals_a_missing_event_without_reextracting(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'disclosure-backfill.db'}")
    Base.metadata.create_all(engine)
    published_at = datetime(2026, 4, 25, tzinfo=UTC)

    class FakeCninfo:
        def announcements(
            self,
            symbol: str,
            start: date,
            end: date,
            *,
            page_size: int = 30,
        ) -> list[dict[str, Any]]:
            del symbol, start, end, page_size
            return [
                {
                    "title": "贵州茅台2026年第一季度报告",
                    "url": "https://example.test/existing.pdf",
                    "category": "季度报告",
                    "published_at": published_at,
                }
            ]

    with Session(engine) as session:
        session.add(
            Disclosure(
                symbol="600519",
                title="贵州茅台2026年第一季度报告",
                url="https://example.test/existing.pdf",
                category="季度报告",
                published_at=published_at,
                source="cninfo",
            )
        )
        session.flush()
        result = sync_disclosures(
            session, FakeCninfo(), "600519"  # type: ignore[arg-type]
        )
        repeated = sync_disclosures(
            session, FakeCninfo(), "600519"  # type: ignore[arg-type]
        )
        assert result["inserted"] == 0
        assert repeated["inserted"] == 0
        assert result["events_extracted"] == 1
        assert repeated["events_extracted"] == 0
        assert session.scalar(select(func.count()).select_from(DomainEvent)) == 1


def test_market_poll_emits_deduplicated_watchlist_anomalies(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'capital-events.db'}")
    Base.metadata.create_all(engine)
    occurred_at = datetime(2026, 7, 21, 2, 30, tzinfo=UTC)
    quotes = {
        "600519": market_poll.NormalizedQuote(
            symbol="600519",
            source="futu",
            last=108.0,
            prev_close=100.0,
            high=108.0,
            amount=1_000_000.0,
            change_pct=8.0,
            market_cap=None,
            float_cap=None,
            pe_ttm=None,
            pb=None,
            turnover_rate=2.0,
        ),
        "300750": market_poll.NormalizedQuote(
            symbol="300750",
            source="futu",
            last=101.0,
            prev_close=100.0,
            high=101.0,
            amount=2_000_000.0,
            change_pct=1.0,
            market_cap=None,
            float_cap=None,
            pe_ttm=None,
            pb=None,
            turnover_rate=12.0,
        ),
    }
    with Session(engine) as session:
        first = market_poll._emit_capital_anomalies(
            session, quotes, set(quotes), occurred_at
        )
        second = market_poll._emit_capital_anomalies(
            session, quotes, set(quotes), occurred_at
        )
        assert first == 2
        assert second == 2
        events = session.scalars(select(DomainEvent).order_by(DomainEvent.symbol)).all()

    assert len(events) == 2
    assert {event.symbol for event in events} == {"300750", "600519"}
    assert all(event.event_type == "capital_anomaly" for event in events)
    assert next(event for event in events if event.symbol == "600519").direction == 0.8


def test_dashboard_emits_only_real_regime_transitions(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'regime-events.db'}")
    Base.metadata.create_all(engine)
    as_of = "2026-07-21T00:00:00+00:00"
    with Session(engine) as session:
        initial = track_market_regime_change(
            session,
            {"regime": "trend_up", "confidence": 0.8, "as_of": as_of},
        )
        changed = track_market_regime_change(
            session,
            {"regime": "risk_off", "confidence": 0.7, "as_of": as_of},
        )
        repeated = track_market_regime_change(
            session,
            {"regime": "risk_off", "confidence": 0.7, "as_of": as_of},
        )
        state = session.get(MarketRegimeState, "SH.000001")
        events = session.scalars(select(DomainEvent)).all()

    assert initial is None
    assert changed is not None
    assert repeated is None
    assert state is not None
    assert state.regime == "risk_off"
    assert len(events) == 1
    assert events[0].event_type == "market_regime_change"
    assert events[0].direction == -0.6
