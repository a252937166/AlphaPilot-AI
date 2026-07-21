from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from alphapilot.db.models import (
    AlertRecord,
    DailyBar,
    DomainEvent,
    ForecastSnapshot,
    ThesisTransition,
    WatchlistItem,
)
from alphapilot.services.events import emit

MODEL_VERSION = "thesis-drift-v1.0.0"
THESIS_STATES = ("strengthened", "unchanged", "weakened")
THESIS_STATE_LABELS = {
    "strengthened": "强化",
    "unchanged": "不变",
    "weakened": "转弱",
}
MARKET_CALENDAR_SYMBOL = "SH.000001"
DELTA_THRESHOLD = 0.08
DELTA_EPSILON = 1e-12
EVENT_LOOKBACK_DAYS = 3


@dataclass(frozen=True, slots=True)
class ForecastEvidence:
    latest: ForecastSnapshot
    baseline: ForecastSnapshot
    baseline_date: date
    latest_probability: float
    baseline_probability: float

    @property
    def delta(self) -> float:
        return self.latest_probability - self.baseline_probability


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(UTC)


def _finite_probability(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        probability = float(str(value))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        return None
    return probability


def _horizon_value(snapshot: ForecastSnapshot, field: str) -> float | None:
    horizon = snapshot.horizons.get("20d")
    if not isinstance(horizon, dict):
        return None
    return _finite_probability(horizon.get(field))


def _forecasts_by_date(
    session: Session,
    symbol: str,
    evaluated_at: datetime,
) -> dict[date, ForecastSnapshot]:
    """Collapse repeated same-session refreshes to the latest persisted row."""

    rows = session.scalars(
        select(ForecastSnapshot)
        .where(
            ForecastSnapshot.symbol == symbol,
            ForecastSnapshot.as_of <= evaluated_at,
            ForecastSnapshot.created_at <= evaluated_at,
        )
        .order_by(ForecastSnapshot.created_at.desc(), ForecastSnapshot.id.desc())
    ).all()
    by_date: dict[date, ForecastSnapshot] = {}
    for row in rows:
        by_date.setdefault(_utc(row.as_of).date(), row)
    return by_date


def _forecast_evidence(
    session: Session,
    symbol: str,
    evaluated_at: datetime,
) -> ForecastEvidence | None:
    by_date = _forecasts_by_date(session, symbol, evaluated_at)
    if not by_date:
        return None
    latest_date = max(by_date)
    latest = by_date[latest_date]
    trading_dates = list(
        session.scalars(
            select(DailyBar.trade_date)
            .where(
                DailyBar.symbol == MARKET_CALENDAR_SYMBOL,
                DailyBar.trade_date <= latest_date,
            )
            .distinct()
            .order_by(DailyBar.trade_date.desc())
            .limit(6)
        ).all()
    )
    if len(trading_dates) < 6 or trading_dates[0] != latest_date:
        return None
    baseline_date = trading_dates[5]
    baseline = by_date.get(baseline_date)
    if (
        baseline is None
        or baseline.model_version != latest.model_version
        or baseline.provider != latest.provider
    ):
        return None
    latest_probability = _horizon_value(latest, "p_up")
    baseline_probability = _horizon_value(baseline, "p_up")
    if latest_probability is None or baseline_probability is None:
        return None
    return ForecastEvidence(
        latest=latest,
        baseline=baseline,
        baseline_date=baseline_date,
        latest_probability=latest_probability,
        baseline_probability=baseline_probability,
    )


def _recent_negative_event(
    session: Session,
    symbol: str,
    now: datetime,
) -> DomainEvent | None:
    return session.scalars(
        select(DomainEvent)
        .where(
            DomainEvent.symbol == symbol,
            DomainEvent.event_type != "thesis_shift",
            DomainEvent.direction <= -0.5,
            DomainEvent.occurred_at >= now - timedelta(days=EVENT_LOOKBACK_DAYS),
            DomainEvent.occurred_at <= now,
            DomainEvent.ingested_at <= now,
        )
        .order_by(DomainEvent.occurred_at.desc(), DomainEvent.id.desc())
        .limit(1)
    ).first()


def _state_from_evidence(
    session: Session,
    symbol: str,
    now: datetime,
    *,
    event_only: bool,
) -> tuple[str, str, str, ForecastSnapshot | None] | None:
    event = _recent_negative_event(session, symbol, now)
    if event is not None:
        return (
            "weakened",
            f"近3日负向事件触发：{event.title}",
            f"event:{event.id}:weakened",
            None,
        )

    if event_only:
        return None
    evidence = _forecast_evidence(session, symbol, now)
    if evidence is None:
        return None
    delta = evidence.delta
    if delta > DELTA_THRESHOLD + DELTA_EPSILON:
        state = "strengthened"
        direction = "上升"
    elif delta < -DELTA_THRESHOLD - DELTA_EPSILON:
        state = "weakened"
        direction = "下降"
    else:
        state = "unchanged"
        direction = "变化"
    reason = (
        f"20日上涨概率较5个交易日前{direction} {delta:+.1%}"
        f"（{evidence.baseline_probability:.1%}→{evidence.latest_probability:.1%}）。"
    )
    trigger_ref = f"forecast:{evidence.latest.id}:{evidence.baseline.id}:{state}"
    return state, reason, trigger_ref, evidence.latest


def _transition_alert(
    *,
    symbol: str,
    state: str,
    reason: str,
    latest: ForecastSnapshot | None,
    now: datetime,
) -> AlertRecord:
    confidence = _horizon_value(latest, "confidence") if latest is not None else None
    return AlertRecord(
        symbol=symbol,
        action="REVIEW_REQUIRED",
        urgency="HIGH" if state == "weakened" else "MEDIUM",
        confidence=confidence if confidence is not None else 0.5,
        suggested_position_change=0.0,
        reasons=[reason],
        invalidation=f"完成人工复核 {symbol} 的投资逻辑与最新证据后关闭。",
        model_version=MODEL_VERSION,
        as_of=now,
        expires_at=now + timedelta(days=2),
    )


def evaluate(
    session: Session,
    symbol: str,
    *,
    evaluated_at: datetime | None = None,
    event_only: bool = False,
    created_alerts: list[AlertRecord] | None = None,
) -> tuple[str, str] | None:
    """Evaluate a thesis and atomically persist artifacts for a state change.

    ``None`` means that neither a recent negative event nor comparable forecast
    history is available. A successful evaluation still returns its decision
    when the stored state already matches; in that case it has no side effects.
    """

    normalized = symbol.strip().upper().split(".")[-1]
    item = session.get(WatchlistItem, normalized)
    if item is None:
        return None
    now = _utc(evaluated_at) if evaluated_at is not None else _now()
    decision = _state_from_evidence(
        session,
        normalized,
        now,
        event_only=event_only,
    )
    if decision is None:
        return None
    to_state, reason, evidence_ref, latest = decision
    if item.thesis_state == to_state:
        return to_state, reason

    from_state = item.thesis_state
    previous_transition_id = session.scalar(
        select(ThesisTransition.id)
        .where(ThesisTransition.symbol == normalized)
        .order_by(ThesisTransition.created_at.desc(), ThesisTransition.id.desc())
        .limit(1)
    )
    trigger_ref = f"{evidence_ref}:after:{previous_transition_id or 0}:{from_state}->{to_state}"
    existing = session.scalar(
        select(ThesisTransition).where(ThesisTransition.trigger_ref == trigger_ref).limit(1)
    )
    if existing is not None:
        return to_state, reason

    # Compare-and-swap prevents two concurrent refreshes from both emitting the
    # transition artifacts after reading the same previous state.
    result = cast(
        CursorResult[Any],
        session.execute(
            update(WatchlistItem)
            .where(
                WatchlistItem.symbol == normalized,
                WatchlistItem.thesis_state == from_state,
            )
            .values(thesis_state=to_state, updated_at=now)
            .execution_options(synchronize_session="fetch")
        ),
    )
    if result.rowcount != 1:
        return to_state, reason

    transition = ThesisTransition(
        symbol=normalized,
        from_state=from_state,
        to_state=to_state,
        reason=reason,
        trigger_ref=trigger_ref,
        model_version=MODEL_VERSION,
        created_at=now,
    )
    session.add(transition)
    session.flush()
    emit(
        session,
        symbol=normalized,
        event_type="thesis_shift",
        title=(
            f"{normalized} 投资逻辑由"
            f"{THESIS_STATE_LABELS.get(from_state, '未知')}转为"
            f"{THESIS_STATE_LABELS[to_state]}"
        ),
        direction={"strengthened": 0.5, "unchanged": 0.0, "weakened": -0.5}[to_state],
        strength=0.8,
        summary=reason,
        source_ref=f"thesis-transition:{transition.id}",
        occurred_at=now,
    )
    alert = _transition_alert(
        symbol=normalized,
        state=to_state,
        reason=reason,
        latest=latest,
        now=now,
    )
    session.add(alert)
    if created_alerts is not None:
        created_alerts.append(alert)
    return to_state, reason
