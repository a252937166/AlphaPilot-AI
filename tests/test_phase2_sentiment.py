from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alphapilot.api.routes.market import market_sentiment
from alphapilot.db.models import Base, DailyBar, MarketSentiment, MarketSnapshotAgg
from alphapilot.engines import sentiment as sentiment_engine
from alphapilot.engines.sentiment import (
    SnapshotObservation,
    _daily_close_rows,
    _percentile_rank,
    clear_sentiment_cache,
    compute,
    sentiment_label,
)

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


@pytest.fixture(autouse=True)
def _reset_sentiment_cache() -> None:
    clear_sentiment_cache()


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
    broken_boards: int = 2,
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


def _observation(snapshot_id: int, local_time: datetime) -> SnapshotObservation:
    local = local_time.astimezone(MARKET_TIMEZONE)
    seconds = local.hour * 3600 + local.minute * 60 + local.second
    return SnapshotObservation(
        snapshot_id=snapshot_id,
        ts=local.astimezone(UTC),
        trade_date=local.date(),
        seconds=float(seconds),
        breadth=0.5,
        limit_ecology=5.0,
        total_amount=100.0,
    )


def _daily_bar(
    *,
    symbol: str,
    trade_date: date,
    close: float,
    ingested_at: datetime,
) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
        amount=1.0,
        source="test",
        ingested_at=ingested_at,
    )


def _sentiment_row(
    *,
    source_snapshot_id: int,
    ts: datetime,
    score: float = 50.0,
    breadth_sub: float = 50.0,
    limitup_sub: float = 50.0,
    volume_sub: float = 50.0,
    volatility_sub: float = 50.0,
    label: str = "中性",
    details: dict[str, object] | None = None,
) -> MarketSentiment:
    return MarketSentiment(
        source_snapshot_id=source_snapshot_id,
        ts=ts,
        score=score,
        breadth_sub=breadth_sub,
        limitup_sub=limitup_sub,
        volume_sub=volume_sub,
        volatility_sub=volatility_sub,
        label=label,
        model_version="sentiment-v1.0.0",
        details=details or {},
    )


def test_percentile_rank_uses_endpoint_scaling_and_average_ties() -> None:
    values = [1.0, 2.0, 2.0, 4.0]

    assert _percentile_rank(1.0, values) == pytest.approx(0.0)
    assert _percentile_rank(2.0, values) == pytest.approx(50.0)
    assert _percentile_rank(4.0, values) == pytest.approx(100.0)
    assert _percentile_rank(2.0, list(reversed(values))) == pytest.approx(50.0)
    assert _percentile_rank(8.0, [8.0]) == pytest.approx(50.0)
    assert _percentile_rank(3.0, [3.0, 3.0, 3.0]) == pytest.approx(50.0)

    with pytest.raises(ValueError, match="必须包含"):
        _percentile_rank(3.0, [1.0, 2.0, 4.0])


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (29.999, "冰点"),
        (30.0, "偏弱"),
        (44.999, "偏弱"),
        (45.0, "中性"),
        (59.999, "中性"),
        (60.0, "偏强"),
        (75.0, "偏强"),
        (75.001, "过热"),
    ],
)
def test_sentiment_label_boundaries(score: float, expected: str) -> None:
    assert sentiment_label(score) == expected


def test_daily_close_selector_uses_last_row_in_shanghai_close_window() -> None:
    first_day = date(2026, 7, 20)
    second_day = date(2026, 7, 21)
    rows = [
        _observation(1, datetime(2026, 7, 20, 15, 0, tzinfo=MARKET_TIMEZONE)),
        _observation(2, datetime(2026, 7, 20, 15, 5, 59, tzinfo=MARKET_TIMEZONE)),
        _observation(3, datetime(2026, 7, 20, 15, 6, tzinfo=MARKET_TIMEZONE)),
        _observation(4, datetime(2026, 7, 20, 16, 0, tzinfo=MARKET_TIMEZONE)),
        _observation(5, datetime(2026, 7, 21, 14, 59, 59, tzinfo=MARKET_TIMEZONE)),
        _observation(6, datetime(2026, 7, 21, 15, 5, 30, tzinfo=MARKET_TIMEZONE)),
    ]

    selected = _daily_close_rows(rows, {first_day, second_day})

    assert [item.snapshot_id for item in selected] == [2, 6]
    assert [item.trade_date for item in selected] == [first_day, second_day]


def test_fixed_weight_score_keeps_neutral_slots_for_degraded_components(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fixed-weight.db'}")
    Base.metadata.create_all(engine)
    previous = _snapshot(
        ts=_utc_at(date(2026, 7, 20), 15, 5, 30),
        advancers=0,
        decliners=100,
        limit_up=10,
        broken_boards=2,
        total_amount=100.0,
    )
    current = _snapshot(
        ts=_utc_at(date(2026, 7, 21), 15, 5, 30),
        advancers=100,
        decliners=0,
        limit_up=10,
        broken_boards=2,
        total_amount=200.0,
    )

    with Session(engine) as session:
        session.add_all([previous, current])
        session.flush()
        payload = compute(session, current)

    assert payload["subs"] == pytest.approx(
        {
            "breadth": 100.0,
            "limitup": 50.0,
            "volume": 50.0,
            "volatility": 50.0,
        }
    )
    assert payload["score"] == pytest.approx(65.0)
    details = payload["details"]
    assert details["weights"] == {
        "breadth": 0.30,
        "limitup": 0.25,
        "volume": 0.25,
        "volatility": 0.20,
    }
    assert details["degraded"] is True
    assert set(details["degraded_components"]) == {"volume", "volatility"}
    assert details["components"]["volume"]["mode"] == "close_to_close"
    assert details["components"]["volume"]["available"] is False


def test_off_hours_snapshot_does_not_fabricate_volume_comparison(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'off-hours.db'}")
    Base.metadata.create_all(engine)
    previous = _snapshot(
        ts=_utc_at(date(2026, 7, 20), 15, 5, 30),
        total_amount=100.0,
    )
    current = _snapshot(
        ts=_utc_at(date(2026, 7, 21), 16, 0),
        total_amount=10_000.0,
    )

    with Session(engine) as session:
        session.add_all([previous, current])
        session.flush()
        payload = compute(session, current)

    volume = payload["details"]["components"]["volume"]
    assert payload["subs"]["volume"] == pytest.approx(50.0)
    assert volume["mode"] == "off_market_unavailable"
    assert volume["available"] is False
    assert "非交易时段" in volume["reason"]


def test_history_cache_has_five_minute_ttl_but_never_caches_current_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'history-cache.db'}")
    Base.metadata.create_all(engine)
    prior = _snapshot(
        ts=_utc_at(date(2026, 7, 20), 15, 5, 30),
        advancers=50,
        decliners=50,
    )
    first_current = _snapshot(
        ts=_utc_at(date(2026, 7, 21), 14, 1),
        advancers=20,
        decliners=80,
    )
    second_current = _snapshot(
        ts=_utc_at(date(2026, 7, 21), 14, 2),
        advancers=80,
        decliners=20,
    )
    clock = {"value": 0.0}
    calls = {"history": 0}
    original = sentiment_engine._snapshot_history

    def counted_history(session: Session, current_day: date) -> tuple[SnapshotObservation, ...]:
        calls["history"] += 1
        return original(session, current_day)

    monkeypatch.setattr(sentiment_engine, "monotonic", lambda: clock["value"])
    monkeypatch.setattr(sentiment_engine, "_snapshot_history", counted_history)

    with Session(engine) as session:
        session.add_all([prior, first_current, second_current])
        session.flush()
        first_payload = compute(session, first_current)
        clock["value"] = 299.0
        second_payload = compute(session, second_current)
        assert calls["history"] == 1
        assert first_payload["subs"]["breadth"] == pytest.approx(0.0)
        assert second_payload["subs"]["breadth"] == pytest.approx(100.0)

        clock["value"] = 301.0
        compute(session, second_current)
        assert calls["history"] == 2


def test_incomplete_and_future_index_bars_are_excluded(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'index-cutoff.db'}")
    Base.metadata.create_all(engine)
    current_day = date(2026, 1, 30)
    as_of = _utc_at(current_day, 15, 5, 30)
    current = _snapshot(ts=as_of)

    with Session(engine) as session:
        session.add(current)
        for offset in range(20):
            trade_date = date(2026, 1, 1) + timedelta(days=offset)
            ingested_at = _utc_at(trade_date, 18, 0)
            session.add(
                _daily_bar(
                    symbol="SH.000001",
                    trade_date=trade_date,
                    close=100.0 + offset + (offset % 3),
                    ingested_at=ingested_at,
                )
            )
            session.add(
                _daily_bar(
                    symbol="000001",
                    trade_date=trade_date,
                    close=10_000.0 if offset % 2 else 1.0,
                    ingested_at=ingested_at,
                )
            )
        session.add(
            _daily_bar(
                symbol="SH.000001",
                trade_date=current_day,
                close=1_000_000.0,
                ingested_at=as_of + timedelta(hours=1),
            )
        )
        session.flush()
        payload = compute(session, current)

    volatility = payload["details"]["components"]["volatility"]
    assert payload["subs"]["volatility"] == pytest.approx(50.0)
    assert volatility["index_symbol"] == "SH.000001"
    assert volatility["latest_complete_trade_date"] == "2026-01-20"
    assert volatility["available"] is False
    assert volatility["sample_size"] == 0
    assert "不足 21 根" in volatility["reason"]
    assert "volatility" in payload["details"]["missing_inputs"]


@pytest.mark.parametrize(
    "invalid_field",
    ["score", "breadth_sub", "limitup_sub", "volume_sub", "volatility_sub"],
)
def test_market_sentiment_database_rejects_out_of_range_scores(
    tmp_path: Path,
    invalid_field: str,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'constraints.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        snapshot = _snapshot(ts=datetime(2026, 7, 21, 7, 0, tzinfo=UTC))
        session.add(snapshot)
        session.flush()
        values = {
            "score": 50.0,
            "breadth_sub": 50.0,
            "limitup_sub": 50.0,
            "volume_sub": 50.0,
            "volatility_sub": 50.0,
        }
        values[invalid_field] = 100.001
        session.add(
            _sentiment_row(
                source_snapshot_id=snapshot.id,
                ts=snapshot.ts,
                **values,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_market_sentiment_database_rejects_duplicate_source_snapshot(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'unique-source.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        snapshot = _snapshot(ts=datetime(2026, 7, 21, 7, 0, tzinfo=UTC))
        session.add(snapshot)
        session.flush()
        session.add(
            _sentiment_row(
                source_snapshot_id=snapshot.id,
                ts=snapshot.ts,
            )
        )
        session.commit()
        session.add(
            _sentiment_row(
                source_snapshot_id=snapshot.id,
                ts=datetime(2026, 7, 21, 7, 1, tzinfo=UTC),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_market_sentiment_api_returns_latest_persisted_row_with_utc_timestamp(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sentiment-api.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        with pytest.raises(HTTPException) as raised:
            market_sentiment(session)
        assert raised.value.status_code == 404
        assert "运行全市场快照任务" in raised.value.detail

        older_snapshot = _snapshot(ts=datetime(2026, 7, 21, 7, 0, tzinfo=UTC))
        latest_snapshot = _snapshot(ts=datetime(2026, 7, 21, 7, 1, tzinfo=UTC))
        session.add_all([older_snapshot, latest_snapshot])
        session.flush()
        latest_snapshot_id = latest_snapshot.id
        session.add_all(
            [
                _sentiment_row(
                    source_snapshot_id=older_snapshot.id,
                    ts=older_snapshot.ts,
                    score=20.0,
                    label="冰点",
                ),
                _sentiment_row(
                    source_snapshot_id=latest_snapshot.id,
                    ts=latest_snapshot.ts,
                    score=72.5,
                    breadth_sub=80.0,
                    limitup_sub=70.0,
                    volume_sub=60.0,
                    volatility_sub=80.0,
                    label="偏强",
                    details={
                        "weights": {
                            "breadth": 0.30,
                            "limitup": 0.25,
                            "volume": 0.25,
                            "volatility": 0.20,
                        },
                        "components": {
                            "breadth": {"sample_size": 2, "historical_samples": 1},
                            "limitup": {"sample_size": 2, "historical_samples": 1},
                            "volume": {"sample_size": 1, "historical_samples": 0},
                            "volatility": {"sample_size": 3, "historical_samples": 2},
                        },
                        "degraded_components": ["volume"],
                        "missing_inputs": [],
                        "degradation_reason": "量能历史不足。",
                        "source": {"snapshot_id": latest_snapshot.id},
                    },
                ),
            ]
        )
        session.commit()

    with Session(engine) as session:
        payload = market_sentiment(session)

    assert payload["score"] == pytest.approx(72.5)
    assert payload["label"] == "偏强"
    assert payload["subs"] == {
        "breadth": 80.0,
        "limitup": 70.0,
        "volume": 60.0,
        "volatility": 80.0,
    }
    assert payload["as_of"] == "2026-07-21T07:01:00+00:00"
    assert payload["source_snapshot_id"] == latest_snapshot_id
    assert payload["history_samples"] == {
        "breadth": 1,
        "limitup": 1,
        "volume": 0,
        "volatility": 2,
    }
    assert payload["sample_sizes"] == {
        "breadth": 2,
        "limitup": 2,
        "volume": 1,
        "volatility": 3,
    }
    assert payload["degraded"] is True
    assert payload["degraded_components"] == ["volume"]
    assert payload["money_effect"] == "赚钱效应较强"
    assert payload["liquidity"] == "资金面偏强"


def test_market_sentiment_api_rejects_a_stale_row(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'sentiment-stale.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        scored_snapshot = _snapshot(ts=datetime(2026, 7, 21, 7, 0, tzinfo=UTC))
        unscored_latest = _snapshot(ts=datetime(2026, 7, 21, 7, 1, tzinfo=UTC))
        session.add_all([scored_snapshot, unscored_latest])
        session.flush()
        session.add(
            _sentiment_row(
                source_snapshot_id=scored_snapshot.id,
                ts=scored_snapshot.ts,
            )
        )
        session.commit()

    with Session(engine) as session, pytest.raises(HTTPException) as raised:
        market_sentiment(session)

    assert raised.value.status_code == 503
    assert "尚未与最新全市场快照同步" in raised.value.detail
