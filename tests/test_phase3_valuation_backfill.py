from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alphapilot.db.models import Base, DailyBar, Security, ValuationDaily
from alphapilot.jobs import valuation_sync
from alphapilot.jobs.registry import JOBS, JobExecutionError


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


def _seed_universe(session: Session) -> None:
    session.add_all(
        [
            Security(symbol="000001", name="平安银行", market="CN", profile={}),
            Security(symbol="600519", name="贵州茅台", market="CN", profile={}),
            DailyBar(
                symbol="600519",
                trade_date=date(2026, 7, 23),
                open=1,
                high=1,
                low=1,
                close=1,
                volume=1,
                amount=1,
                source="baostock",
            ),
        ]
    )


def test_fetch_valuation_em_maps_columns_and_preserves_six_digit_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_get(
        url: str,
        *,
        params: dict[str, str],
        timeout: httpx.Timeout,
    ) -> httpx.Response:
        captured.append({"url": url, "params": params, "timeout": timeout})
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "result": {
                    "data": [
                        {
                            "TRADE_DATE": "2025-01-02",
                            "PE_TTM": "22.60643568",
                            "PB_MRQ": "8.01876593",
                            "PS_TTM": "11.10207245",
                        },
                        {
                            "TRADE_DATE": "2018-12-31",
                            "PE_TTM": "20",
                            "PB_MRQ": "7",
                            "PS_TTM": "10",
                        },
                    ]
                }
            },
        )

    monkeypatch.setattr(valuation_sync.httpx, "get", fake_get)
    frame = valuation_sync.fetch_valuation_em(
        "600519",
        start_date=date(2019, 1, 1),
        end_date=date(2025, 1, 2),
    )

    assert len(captured) == 1
    assert captured[0]["params"]["filter"] == '(SECURITY_CODE="600519")'
    timeout = captured[0]["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == valuation_sync.EM_CONNECT_TIMEOUT_SECONDS
    assert timeout.read == valuation_sync.EM_READ_TIMEOUT_SECONDS
    assert list(frame.columns) == ["trade_date", "pe_ttm", "pb_mrq", "ps_ttm"]
    assert frame.to_dict(orient="records") == [
        {
            "trade_date": date(2025, 1, 2),
            "pe_ttm": 22.60643568,
            "pb_mrq": 8.01876593,
            "ps_ttm": 11.10207245,
        }
    ]
    with pytest.raises(ValueError, match="exact six-digit"):
        valuation_sync.fetch_valuation_em("SH.600519")


def test_fetch_valuation_em_converts_transport_timeout_to_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timed_out(*_args: object, **_kwargs: object) -> httpx.Response:
        raise httpx.ReadTimeout("internet disconnected")

    monkeypatch.setattr(valuation_sync.httpx, "get", timed_out)

    with pytest.raises(
        valuation_sync._EastmoneyNetworkError,
        match=r"timed out.*connect=5.0s, read=20.0s",
    ):
        valuation_sync.fetch_valuation_em("600519")


def test_backfill_valuation_pauses_cleanly_after_network_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'valuation-network.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        _seed_universe(session)

    def network_timeout(
        _symbol: str,
        *,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        del start_date, end_date
        raise valuation_sync._EastmoneyNetworkError("internet disconnected")

    monkeypatch.setattr(valuation_sync, "get_session", local_session)
    monkeypatch.setattr(valuation_sync, "fetch_valuation_em", network_timeout)
    monkeypatch.setattr(valuation_sync, "sleep", lambda _seconds: None)
    monkeypatch.setattr(valuation_sync, "uniform", lambda _low, _high: 0.0)
    monkeypatch.setattr(
        valuation_sync,
        "EM_HOST_LOCK_PATH",
        tmp_path / "valuation-em-network.lock",
    )

    with pytest.raises(JobExecutionError) as exc_info:
        valuation_sync.backfill_valuation(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 3),
            symbols=["600519"],
        )

    stats = exc_info.value.stats
    assert stats["network_unavailable"] is True
    assert stats["em_throttled"] is False
    assert stats["pause_reason"] == "network_unavailable"
    assert stats["provider_calls"] == 1
    assert stats["provider_attempts"] == 3
    assert stats["resume_symbol"] == "600519"
    assert stats["request_timeout_seconds"] == {"connect": 5.0, "read": 20.0}


def test_backfill_valuation_is_pit_correct_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'valuation.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        _seed_universe(session)

    calls: list[tuple[str, date, date]] = []

    def fake_fetch(
        symbol: str,
        *,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        calls.append((symbol, start_date, end_date))
        return pd.DataFrame(
            [
                {
                    "trade_date": date(2025, 1, 2),
                    "pe_ttm": 10.0 if symbol == "000001" else 22.60643568,
                    "pb_mrq": 1.0,
                    "ps_ttm": 2.0,
                },
                {
                    "trade_date": date(2025, 1, 3),
                    "pe_ttm": 11.0 if symbol == "000001" else 22.7,
                    "pb_mrq": 1.1,
                    "ps_ttm": 2.1,
                },
            ]
        )

    monkeypatch.setattr(valuation_sync, "get_session", local_session)
    monkeypatch.setattr(valuation_sync, "fetch_valuation_em", fake_fetch)
    monkeypatch.setattr(valuation_sync, "sleep", lambda _seconds: None)
    monkeypatch.setattr(valuation_sync, "uniform", lambda _low, _high: 0.0)
    monkeypatch.setattr(valuation_sync, "SQL_SYMBOL_CHUNK_SIZE", 1)
    monkeypatch.setattr(
        valuation_sync,
        "EM_HOST_LOCK_PATH",
        tmp_path / "valuation-em.lock",
    )

    first = valuation_sync.backfill_valuation(
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 3),
        batch_size=1,
    )
    second = valuation_sync.backfill_valuation(
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 3),
    )

    assert first["source"] == "em"
    assert first["rows_inserted"] == 4
    assert first["symbols_with_data"] == 2
    assert first["coverage"]["symbols"] == 2
    assert second["provider_calls"] == 0
    assert second["rows_inserted"] == 0
    assert second["symbols_skipped_complete"] == 2
    assert calls == [
        ("000001", date(2025, 1, 1), date(2025, 1, 3)),
        ("600519", date(2025, 1, 1), date(2025, 1, 3)),
    ]

    with local_session() as session:
        assert session.scalar(select(func.count()).select_from(ValuationDaily)) == 4
        row = session.scalar(
            select(ValuationDaily).where(
                ValuationDaily.symbol == "600519",
                ValuationDaily.trade_date == date(2025, 1, 2),
            )
        )
        assert row is not None
        available_time = row.available_time
        if available_time.tzinfo is None:
            available_time = available_time.replace(tzinfo=UTC)
        assert available_time == datetime(2025, 1, 2, 7, tzinfo=UTC)
        assert row.source == "em"
        assert row.pe_ttm == pytest.approx(22.60643568)


def test_backfill_valuation_distinguishes_no_data_from_throttling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'valuation-throttle.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        _seed_universe(session)

    monkeypatch.setattr(valuation_sync, "get_session", local_session)
    monkeypatch.setattr(valuation_sync, "sleep", lambda _seconds: None)
    monkeypatch.setattr(valuation_sync, "uniform", lambda _low, _high: 0.0)
    monkeypatch.setattr(
        valuation_sync,
        "EM_HOST_LOCK_PATH",
        tmp_path / "valuation-em-throttle.lock",
    )
    monkeypatch.setattr(
        valuation_sync,
        "fetch_valuation_em",
        lambda *_args, **_kwargs: pd.DataFrame(
            columns=["trade_date", "pe_ttm", "pb_mrq", "ps_ttm"]
        ),
    )
    no_data = valuation_sync.backfill_valuation(
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 3),
        symbols=["000001"],
    )
    assert no_data["symbols_no_data"] == 1
    assert no_data["symbols_failed"] == 0
    assert no_data["em_throttled"] is False

    def throttled(*_args: object, **_kwargs: object) -> pd.DataFrame:
        raise RuntimeError("HTTP 429 Too Many Requests")

    monkeypatch.setattr(valuation_sync, "fetch_valuation_em", throttled)
    with pytest.raises(JobExecutionError) as exc_info:
        valuation_sync.backfill_valuation(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 3),
            symbols=["600519"],
        )
    assert exc_info.value.stats["em_throttled"] is True
    assert exc_info.value.stats["is_complete"] is False
    assert exc_info.value.stats["provider_attempts"] == 1


def test_register_valuation_jobs_has_manual_backfill_and_post_close_increment() -> None:
    valuation_sync.register_valuation_jobs()

    assert JOBS["backfill_valuation"].trigger is None
    trigger = JOBS["sync_valuation_daily"].trigger
    assert trigger is not None
    assert JOBS["sync_valuation_daily"].enabled_key == "valuation_sync_enabled"
    assert str(trigger).find("hour='18'") >= 0
    assert str(trigger).find("minute='50'") >= 0
