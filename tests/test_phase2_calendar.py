from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alphapilot.api.routes.stocks import stock_calendar
from alphapilot.db.models import Base, CalendarEvent, WatchlistItem
from alphapilot.jobs import calendar_sync
from alphapilot.jobs.registry import JOBS


def _local_session(engine: Any) -> Any:
    @contextmanager
    def local_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    return local_session


def test_sync_calendar_combines_sources_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'calendar.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add_all(
            [
                WatchlistItem(symbol="600519", display_name="贵州茅台"),
                WatchlistItem(symbol="000333", display_name="美的集团"),
            ]
        )

    class FakeBaoStock:
        def get_dividend_data(self, symbol: str, year: int) -> pd.DataFrame:
            if year != 2026:
                return pd.DataFrame()
            return pd.DataFrame(
                [
                    {
                        "code": symbol,
                        "dividPlanAnnounceDate": "2026-04-17",
                        "dividPayDate": "2026-06-26",
                        "dividCashStock": "10派100元",
                    }
                ]
            )

        def get_forecast_reports(self, symbol: str, start: date, end: date) -> pd.DataFrame:
            del start, end
            return pd.DataFrame(
                [
                    {
                        "code": symbol,
                        "profitForcastExpPubDate": "2026-01-03",
                        "profitForcastExpStatDate": "2025-12-31",
                        "profitForcastType": "略增",
                    }
                ]
            )

    class FakeCninfo:
        def announcements(
            self,
            symbol: str,
            start: date,
            end: date,
            *,
            page_size: int,
        ) -> list[dict[str, Any]]:
            del start, end
            assert page_size == 100
            return [
                {
                    "title": f"{symbol} 2025年年度报告",
                    "url": f"https://example.test/{symbol}",
                    "category": "年度报告",
                    "published_at": datetime(2026, 3, 30, 1, tzinfo=UTC),
                }
            ]

    def fake_unlock(symbol: str, now: datetime) -> list[calendar_sync.EventCandidate]:
        return [
            {
                "symbol": symbol,
                "event_type": "unlock",
                "event_date": date(2026, 7, 20),
                "title": "限售股解禁：股权激励限售股份",
                "payload": {"available_time_basis": "ingested_at"},
                "source": "eastmoney",
                "available_time": now,
            }
        ]

    monkeypatch.setattr(calendar_sync, "get_session", local_session)
    monkeypatch.setattr(calendar_sync, "BaoStockMarketDataProvider", FakeBaoStock)
    monkeypatch.setattr(calendar_sync, "get_cninfo_client", FakeCninfo)
    monkeypatch.setattr(calendar_sync, "_unlock_events", fake_unlock)

    first = calendar_sync.sync_calendar()
    second = calendar_sync.sync_calendar()

    assert first["symbols_total"] == 2
    assert first["symbols_with_events"] == 2
    assert first["events"] == 8
    assert first["inserted"] == 8
    assert second["inserted"] == 0
    assert second["updated"] == 8
    assert first["warning_count"] == 0
    with local_session() as session:
        assert session.scalar(select(func.count()).select_from(CalendarEvent)) == 8
        types = set(session.scalars(select(CalendarEvent.event_type)))
        assert types == {"dividend", "earnings_preview", "earnings_report", "unlock"}


def test_unlock_parser_preserves_akshare_units_and_ingestion_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_payload = {
        "result": {
            "data": [
                {
                    "SECURITY_CODE": "000333",
                    "FREE_DATE": "2026-07-20 00:00:00",
                    "CURRENT_FREE_SHARES": 439.5925,
                    "ABLE_FREE_SHARES": 439.5925,
                    "LIFT_MARKET_CAP": 37057.64775,
                    "NON_FREE_SHARES": 9309.4987,
                    "FREE_SHARES_TYPE": "股权激励限售股份",
                }
            ]
        }
    }

    def fake_get(*_args: object, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            json=response_payload,
            request=httpx.Request("GET", "https://datacenter-web.eastmoney.com/"),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    now = datetime(2026, 7, 21, 2, tzinfo=UTC)
    events = calendar_sync._unlock_events("000333", now)

    assert len(events) == 1
    assert events[0]["event_date"] == date(2026, 7, 20)
    assert events[0]["available_time"] == now
    assert events[0]["payload"]["CURRENT_FREE_SHARES"] == pytest.approx(4_395_925)
    assert events[0]["payload"]["LIFT_MARKET_CAP"] == pytest.approx(370_576_477.5)
    assert events[0]["payload"]["available_time_basis"] == "ingested_at"


def test_stock_calendar_returns_symmetric_sorted_window(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'calendar-api.db'}")
    Base.metadata.create_all(engine)
    today = date.today()
    with Session(engine) as session:
        session.add_all(
            [
                CalendarEvent(
                    symbol="600519",
                    event_type="unlock",
                    event_date=today + timedelta(days=10),
                    title="未来解禁",
                    payload={},
                    source="test",
                    available_time=datetime.now(UTC),
                ),
                CalendarEvent(
                    symbol="600519",
                    event_type="dividend",
                    event_date=today - timedelta(days=10),
                    title="最近分红",
                    payload={},
                    source="test",
                    available_time=datetime.now(UTC),
                ),
                CalendarEvent(
                    symbol="600519",
                    event_type="dividend",
                    event_date=today - timedelta(days=100),
                    title="窗口外分红",
                    payload={},
                    source="test",
                    available_time=datetime.now(UTC),
                ),
            ]
        )
        session.commit()
        payload = stock_calendar("SH.600519", days=90, session=session)

    assert payload["from"] == (today - timedelta(days=90)).isoformat()
    assert payload["to"] == (today + timedelta(days=90)).isoformat()
    assert [event["title"] for event in payload["events"]] == ["最近分红", "未来解禁"]
    assert {event["event_type"] for event in payload["events"]} == {
        "dividend",
        "unlock",
    }


def test_calendar_job_is_registered_for_0730() -> None:
    calendar_sync.register_calendar_job()
    spec = JOBS["sync_calendar"]
    assert "hour='7'" in str(spec.trigger)
    assert "minute='30'" in str(spec.trigger)
