from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alphapilot.api.routes.market import market_breadth_full
from alphapilot.db.models import Base, MarketSentiment, MarketSnapshotAgg, Security
from alphapilot.jobs import market_poll
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


def _futu_record(code: str) -> dict[str, Any]:
    symbol = code.rsplit(".", 1)[-1]
    values: dict[str, Any] = {
        "code": code,
        "last_price": 10.0,
        "prev_close_price": 10.0,
        "high_price": 10.0,
        "turnover": 1_000_000.0,
        "suspension": False,
        "total_market_val": 1_000_000_000.0,
        "circular_market_val": 800_000_000.0,
        "pe_ttm_ratio": 15.0,
        "pb_ratio": 2.0,
        "turnover_rate": 1.2,
    }
    if symbol == "600000":
        values.update(last_price=11.0, high_price=11.0)
    elif symbol == "600001":
        values.update(last_price=9.0, high_price=10.0)
    elif symbol == "600002":
        values.update(last_price=10.5, high_price=11.0)
    elif symbol == "300001":
        values.update(last_price=10.5, high_price=10.5)
    return values


def test_poll_market_snapshot_aggregates_and_updates_master(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'market-poll.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    securities = [
        Security(symbol="600000", board="主板", is_st=False),
        Security(symbol="600001", board="主板", is_st=False),
        Security(symbol="600002", board="主板", is_st=False),
        Security(symbol="300001", board="创业板", is_st=False),
        Security(symbol="920000", board="北交所", is_st=False),
    ]
    securities.extend(
        Security(symbol=f"{600100 + index:06d}", board="主板", is_st=False) for index in range(95)
    )
    with local_session() as session:
        session.add_all(securities)

    class FakeFutuClient:
        def quote_call_raw(
            self,
            method: str,
            args: list[Any] | None = None,
            kwargs: Any = None,
        ) -> pd.DataFrame:
            del kwargs
            assert method == "get_market_snapshot"
            assert args is not None
            return pd.DataFrame([_futu_record(str(code)) for code in args[0]])

    class FakeSinaProvider:
        def __init__(self, min_interval_seconds: float) -> None:
            assert min_interval_seconds == 0.2

        def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
            assert symbols == ["920000"]
            return pd.DataFrame(
                [
                    {
                        "symbol": "920000",
                        "last_price": 13.0,
                        "prev_close_price": 10.0,
                        "high_price": 13.0,
                        "turnover": 2_000_000.0,
                        "suspension": False,
                    }
                ]
            )

    monkeypatch.setattr(market_poll, "get_session", local_session)
    monkeypatch.setattr(market_poll, "get_futu_client", FakeFutuClient)
    monkeypatch.setattr(market_poll, "SinaDailyBarProvider", FakeSinaProvider)
    monkeypatch.setattr(market_poll, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        market_poll,
        "_load_eastmoney_limit_up_codes",
        lambda _trade_date: {"600000", "920000"},
    )

    stats = market_poll.poll_market_snapshot(force=True)

    assert stats["universe"] == 100
    assert stats["quoted"] == 100
    assert stats["excluded_symbols"] == []
    assert stats["futu_returned"] == 99
    assert stats["sina_returned"] == 1
    assert stats["zt_source"] == "eastmoney-pool"
    assert stats["sentiment_id"] is not None
    assert stats["sentiment"]["model_version"] == "sentiment-v1.0.0"
    assert stats["sentiment"]["source_snapshot_id"] == stats["aggregate_id"]
    with local_session() as session:
        aggregate = session.scalar(select(MarketSnapshotAgg))
        assert aggregate is not None
        assert aggregate.advancers == 4
        assert aggregate.decliners == 1
        assert aggregate.unchanged == 95
        assert aggregate.limit_up == 2
        assert aggregate.limit_down == 1
        assert aggregate.broken_boards == 1
        assert aggregate.up_gt4 == 4
        assert aggregate.down_gt4 == 1
        assert aggregate.source == "futu+sina"
        sentiment = session.scalar(select(MarketSentiment))
        assert sentiment is not None
        assert sentiment.source_snapshot_id == aggregate.id
        assert sentiment.ts == aggregate.ts
        assert sentiment.model_version == "sentiment-v1.0.0"
        assert sentiment.details["source"]["quoted"] == 100
        assert sentiment.details["source"]["universe"] == 100
        security = session.get(Security, "600000")
        assert security is not None
        assert security.market_cap == 1_000_000_000.0
        assert security.float_cap == 800_000_000.0
        assert security.pe_ttm == 15.0
        assert security.pb == 2.0
        assert security.turnover_rate == 1.2
        assert security.snapshot_at is not None


def test_poll_market_snapshot_skips_outside_market_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ClosedDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            value = datetime(2026, 7, 21, 16, 0, tzinfo=market_poll.MARKET_TIMEZONE)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(market_poll, "datetime", ClosedDatetime)
    assert market_poll.poll_market_snapshot() == {"skipped": "closed"}


def test_breadth_full_uses_nearest_prior_trading_day_time(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'breadth.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)

    def aggregate(ts: datetime, amount: float) -> MarketSnapshotAgg:
        return MarketSnapshotAgg(
            ts=ts,
            advancers=3000,
            decliners=2000,
            unchanged=100,
            limit_up=80,
            limit_down=5,
            broken_boards=20,
            up_gt4=300,
            down_gt4=100,
            total_amount=amount,
            avg_change_pct=0.5,
            median_change_pct=0.2,
            source="futu+sina",
        )

    with local_session() as session:
        session.add_all(
            [
                aggregate(datetime(2026, 7, 20, 2, 0, tzinfo=UTC), 100.0),
                aggregate(datetime(2026, 7, 20, 2, 1, tzinfo=UTC), 120.0),
                aggregate(datetime(2026, 7, 21, 2, 0, 40, tzinfo=UTC), 150.0),
            ]
        )

    with local_session() as session:
        payload = market_breadth_full(session)
    assert payload["prior_total_amount"] == 120.0
    assert payload["amount_delta"] == 30.0
    assert payload["amount_delta_pct"] == 25.0


def test_breadth_full_404_has_actionable_message(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'breadth-empty.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session, pytest.raises(HTTPException) as raised:
        market_breadth_full(session)
    assert raised.value.status_code == 404
    assert "运行全市场快照任务" in raised.value.detail


def test_market_poll_job_registration_is_opt_in_every_sixty_seconds() -> None:
    market_poll.register_market_poll_job()
    spec = JOBS["poll_market_snapshot"]
    assert spec.enabled_key == "market_poll_enabled"
    assert spec.trigger.interval.total_seconds() == 60


def _sentiment_payload(score: float, label: str) -> dict[str, Any]:
    return {
        "score": score,
        "label": label,
        "model_version": "sentiment-v1.0.0",
        "subs": {
            "breadth": score,
            "limitup": 50.0,
            "volume": 50.0,
            "volatility": 50.0,
        },
        "details": {
            "weights": {
                "breadth": 0.30,
                "limitup": 0.25,
                "volume": 0.25,
                "volatility": 0.20,
            },
            "components": {},
            "degraded_components": [],
            "missing_inputs": [],
        },
    }


def test_persist_sentiment_upserts_one_row_per_source_snapshot(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sentiment-upsert.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        snapshot = MarketSnapshotAgg(
            ts=datetime(2026, 7, 21, 7, 0, tzinfo=UTC),
            advancers=60,
            decliners=40,
            unchanged=0,
            limit_up=10,
            limit_down=0,
            broken_boards=2,
            up_gt4=0,
            down_gt4=0,
            total_amount=100.0,
            avg_change_pct=0.0,
            median_change_pct=0.0,
            source="test",
        )
        session.add(snapshot)
        session.flush()

        first = market_poll._persist_sentiment(
            session,
            snapshot,
            _sentiment_payload(50.0, "中性"),
        )
        second = market_poll._persist_sentiment(
            session,
            snapshot,
            _sentiment_payload(65.0, "偏强"),
        )

        assert first.id == second.id
        assert session.scalar(select(func.count()).select_from(MarketSentiment)) == 1
        assert second.score == pytest.approx(65.0)
        assert second.label == "偏强"
        assert second.breadth_sub == pytest.approx(65.0)


def test_poll_rolls_back_snapshot_when_sentiment_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'market-poll-rollback.db'}")
    Base.metadata.create_all(engine)
    local_session = _local_session(engine)
    with local_session() as session:
        session.add_all(
            Security(
                symbol=f"{600000 + index:06d}",
                board="主板",
                is_st=False,
            )
            for index in range(100)
        )

    class FakeFutuClient:
        def quote_call_raw(
            self,
            method: str,
            args: list[Any] | None = None,
            kwargs: Any = None,
        ) -> pd.DataFrame:
            del kwargs
            assert method == "get_market_snapshot"
            assert args is not None
            return pd.DataFrame([_futu_record(str(code)) for code in args[0]])

    def fail_sentiment(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("injected sentiment failure")

    monkeypatch.setattr(market_poll, "get_session", local_session)
    monkeypatch.setattr(market_poll, "get_futu_client", FakeFutuClient)
    monkeypatch.setattr(market_poll, "sleep", lambda _seconds: None)
    monkeypatch.setattr(market_poll, "_load_eastmoney_limit_up_codes", lambda _date: set())
    monkeypatch.setattr(market_poll, "compute_sentiment", fail_sentiment)

    with pytest.raises(RuntimeError, match="injected sentiment failure"):
        market_poll.poll_market_snapshot(force=True)

    with local_session() as session:
        assert session.scalar(select(func.count()).select_from(MarketSnapshotAgg)) == 0
        assert session.scalar(select(func.count()).select_from(MarketSentiment)) == 0
        securities = session.scalars(select(Security)).all()
        assert len(securities) == 100
        assert all(security.snapshot_at is None for security in securities)
