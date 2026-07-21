from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alphapilot.api.routes.events import list_events
from alphapilot.db.models import (
    Base,
    Disclosure,
    DomainEvent,
    MarketRegimeState,
)
from alphapilot.jobs import market_poll
from alphapilot.services.dashboard import track_market_regime_change
from alphapilot.services.disclosures import sync_disclosures
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


def test_disclosure_sync_emits_once_for_each_new_announcement(tmp_path: Path) -> None:
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


def test_disclosure_sync_backfills_event_for_an_existing_announcement(tmp_path: Path) -> None:
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
