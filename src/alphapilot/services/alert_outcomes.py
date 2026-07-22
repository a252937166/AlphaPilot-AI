from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from datetime import UTC, date, datetime, time
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.core.timeutil import iso_utc
from alphapilot.db.models import AlertOutcome, AlertRecord, DailyBar, DailyReport

HORIZON_DAYS = 5
MODEL_VERSION = "signal-attribution-v1.0.0"
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
MARKET_CALENDAR_SYMBOL = "SH.000001"
MIN_STOCK_CALENDAR_SYMBOLS = 2
CLOSE_COMPLETE_TIME = time(15, 6)
BUY_ACTIONS = frozenset({"BUY_CANDIDATE", "ADD"})
SELL_ACTIONS = frozenset({"REDUCE", "EXIT", "STOP"})
NON_DIRECTIONAL_ACTIONS = frozenset({"WATCH", "HOLD", "REVIEW", "REVIEW_REQUIRED"})
KNOWN_ACTIONS = BUY_ACTIONS | SELL_ACTIONS | NON_DIRECTIONAL_ACTIONS


class AlertOutcomeError(RuntimeError):
    """Alert attribution cannot be computed without misleading or invalid data."""


def _as_utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(UTC)


def _number(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise AlertOutcomeError(f"提醒归因字段 {field} 不是有效数值。")
    try:
        number = float(str(value))
    except (TypeError, ValueError) as exc:
        raise AlertOutcomeError(f"提醒归因字段 {field} 不是有效数值。") from exc
    if not isfinite(number):
        raise AlertOutcomeError(f"提醒归因字段 {field} 不是有限数值。")
    return number


def _validated_close(
    row: DailyBar | None,
    *,
    expected_date: date,
    as_of: datetime,
) -> tuple[float | None, str | None]:
    if row is None or row.trade_date != expected_date:
        return None, "missing"
    try:
        close = _number(row.close, "close")
    except AlertOutcomeError:
        return None, "invalid"
    if close <= 0 or not isinstance(row.ingested_at, datetime):
        return None, "invalid"
    ingested_at = _as_utc(row.ingested_at)
    if ingested_at > as_of:
        return None, "future_ingestion"
    local_ingested_at = ingested_at.astimezone(MARKET_TIMEZONE)
    complete_same_day = (
        local_ingested_at.date() == row.trade_date
        and local_ingested_at.time() >= CLOSE_COMPLETE_TIME
    )
    if row.trade_date >= local_ingested_at.date() and not complete_same_day:
        return None, "incomplete"
    return close, None


def _trading_calendar(session: Session, *, as_of: datetime) -> list[date]:
    market_as_of = as_of.astimezone(MARKET_TIMEZONE)
    index_rows = session.execute(
        select(DailyBar.trade_date, DailyBar.ingested_at)
        .where(
            DailyBar.symbol == MARKET_CALENDAR_SYMBOL,
            DailyBar.trade_date <= market_as_of.date(),
        )
        .order_by(DailyBar.trade_date)
    ).all()
    stock_dates = session.execute(
        select(DailyBar.trade_date, func.count(func.distinct(DailyBar.symbol)))
        .where(
            func.length(DailyBar.symbol) == 6,
            DailyBar.trade_date <= market_as_of.date(),
            DailyBar.ingested_at <= as_of,
        )
        .group_by(DailyBar.trade_date)
        .having(func.count(func.distinct(DailyBar.symbol)) >= MIN_STOCK_CALENDAR_SYMBOLS)
    ).all()
    available_dates = {
        trade_date
        for trade_date, ingested_at in index_rows
        if isinstance(trade_date, date)
        and isinstance(ingested_at, datetime)
        and _as_utc(ingested_at) <= as_of
    }
    available_dates.update(
        trade_date
        for trade_date, symbol_count in stock_dates
        if isinstance(trade_date, date)
        and isinstance(symbol_count, int)
        and symbol_count >= MIN_STOCK_CALENDAR_SYMBOLS
    )
    return sorted(
        {
            trade_date
            for trade_date in available_dates
            if trade_date < market_as_of.date() or market_as_of.time() >= CLOSE_COMPLETE_TIME
        }
    )


def _maturity_pair(
    calendar: list[date],
    *,
    origin_date: date,
) -> tuple[date, date] | None:
    origin_index = bisect_left(calendar, origin_date)
    if origin_index >= len(calendar) or calendar[origin_index] != origin_date:
        return None
    maturity_index = origin_index + HORIZON_DAYS
    if maturity_index >= len(calendar):
        return None
    return calendar[origin_index], calendar[maturity_index]


def _signal_origin_date(alert: AlertRecord, calendar: list[date]) -> date | None:
    if alert.as_of is not None:
        if not isinstance(alert.as_of, datetime):
            raise AlertOutcomeError(f"提醒#{alert.id} 缺少有效 as_of。")
        evidence_date = _as_utc(alert.as_of).astimezone(MARKET_TIMEZONE).date()
        position = bisect_left(calendar, evidence_date)
        if position < len(calendar) and calendar[position] == evidence_date:
            return evidence_date
        if not calendar or evidence_date > calendar[-1]:
            return None
        raise AlertOutcomeError(f"提醒#{alert.id} 的 as_of 不在上证交易日历中。")

    if not isinstance(alert.created_at, datetime):
        raise AlertOutcomeError(f"提醒#{alert.id} 缺少有效 created_at。")
    created_local = _as_utc(alert.created_at).astimezone(MARKET_TIMEZONE)
    search_date = created_local.date()
    insertion = bisect_left(calendar, search_date)
    if insertion < len(calendar) and calendar[insertion] == search_date:
        if created_local.time() >= CLOSE_COMPLETE_TIME:
            return search_date
        insertion -= 1
    else:
        insertion -= 1
    return calendar[insertion] if insertion >= 0 else None


def _hit(action: str, realized_return: float) -> bool | None:
    if action in BUY_ACTIONS:
        return realized_return > 0
    if action in SELL_ACTIONS:
        return realized_return < 0
    if action in NON_DIRECTIONAL_ACTIONS:
        return None
    raise AlertOutcomeError(f"提醒 action={action!r} 不在信号归因白名单中。")


def evaluate_mature_alerts(session: Session, *, as_of: datetime) -> dict[str, Any]:
    """Persist exact five-session outcomes for all currently mature alerts."""

    evaluated_at = _as_utc(as_of)
    calendar = _trading_calendar(session, as_of=evaluated_at)
    latest_session = calendar[-1] if calendar else None
    alerts = session.scalars(
        select(AlertRecord)
        .outerjoin(AlertOutcome, AlertOutcome.alert_id == AlertRecord.id)
        .where(AlertOutcome.alert_id.is_(None))
        .order_by(AlertRecord.created_at, AlertRecord.id)
    ).all()

    candidates: list[tuple[AlertRecord, date, date]] = []
    future_alerts = 0
    immature = 0
    for alert in alerts:
        if not isinstance(alert.created_at, datetime):
            raise AlertOutcomeError(f"提醒#{alert.id} 缺少有效 created_at。")
        created_at = _as_utc(alert.created_at)
        if created_at > evaluated_at:
            future_alerts += 1
            continue
        origin_date = _signal_origin_date(alert, calendar)
        if origin_date is None:
            immature += 1
            continue
        pair = _maturity_pair(
            calendar,
            origin_date=origin_date,
        )
        if pair is None:
            immature += 1
            continue
        candidates.append((alert, pair[0], pair[1]))

    symbols = sorted({alert.symbol for alert, _, _ in candidates})
    dates = sorted({item for _, origin, maturity in candidates for item in (origin, maturity)})
    bars = session.scalars(
        select(DailyBar).where(
            DailyBar.symbol.in_(symbols),
            DailyBar.trade_date.in_(dates),
        )
    ).all()
    bars_by_key = {(row.symbol, row.trade_date): row for row in bars}

    created = 0
    directional = 0
    non_directional = 0
    missing_bars = 0
    missing_alert_ids: list[int] = []
    pending_by_reason: dict[str, int] = defaultdict(int)
    prepared: list[dict[str, Any]] = []
    for alert, origin_date, maturity_date in candidates:
        action = str(alert.action or "").strip().upper()
        if action not in KNOWN_ACTIONS:
            raise AlertOutcomeError(f"提醒#{alert.id} action={action!r} 无法归因。")
        origin_close, origin_reason = _validated_close(
            bars_by_key.get((alert.symbol, origin_date)),
            expected_date=origin_date,
            as_of=evaluated_at,
        )
        maturity_close, maturity_reason = _validated_close(
            bars_by_key.get((alert.symbol, maturity_date)),
            expected_date=maturity_date,
            as_of=evaluated_at,
        )
        if origin_close is None or maturity_close is None:
            missing_bars += 1
            if origin_reason is not None:
                pending_by_reason[f"origin_{origin_reason}"] += 1
            if maturity_reason is not None:
                pending_by_reason[f"maturity_{maturity_reason}"] += 1
            if len(missing_alert_ids) < 20:
                missing_alert_ids.append(alert.id)
            continue
        position_change = _number(
            alert.suggested_position_change,
            "suggested_position_change",
        )
        if position_change < -1 or position_change > 1:
            raise AlertOutcomeError(f"提醒#{alert.id} 建议仓位变化超出 [-1, 1]。")
        realized_return = maturity_close / origin_close - 1.0
        if realized_return <= -1 or not isfinite(realized_return):
            raise AlertOutcomeError(f"提醒#{alert.id} 的五日实际收益无效。")
        hit = _hit(action, realized_return)
        contribution = position_change * realized_return
        prepared.append(
            {
                "alert_id": alert.id,
                "origin_date": origin_date,
                "maturity_date": maturity_date,
                "realized_return": realized_return,
                "hit": hit,
                "contribution": contribution,
            }
        )
        if hit is None:
            non_directional += 1
        else:
            directional += 1

    for outcome in prepared:
        session.add(
            AlertOutcome(
                alert_id=int(outcome["alert_id"]),
                horizon_days=HORIZON_DAYS,
                origin_date=outcome["origin_date"],
                maturity_date=outcome["maturity_date"],
                realized_return=float(outcome["realized_return"]),
                hit=outcome["hit"],
                contribution=float(outcome["contribution"]),
                model_version=MODEL_VERSION,
                evaluated_at=evaluated_at,
            )
        )
        created += 1

    return {
        "as_of": evaluated_at.isoformat(),
        "calendar_symbol": MARKET_CALENDAR_SYMBOL,
        "calendar_sources": [MARKET_CALENDAR_SYMBOL, "six_digit_stock_dates"],
        "stock_calendar_quorum": MIN_STOCK_CALENDAR_SYMBOLS,
        "calendar_sessions": len(calendar),
        "latest_session": latest_session.isoformat() if latest_session else None,
        "unevaluated": len(alerts),
        "mature": len(candidates),
        "created": created,
        "directional": directional,
        "non_directional": non_directional,
        "immature": immature,
        "future_alerts": future_alerts,
        "missing_bars": missing_bars,
        "missing_alert_ids": missing_alert_ids,
        "pending_by_reason": dict(sorted(pending_by_reason.items())),
    }


def _outcome_payload(alert: AlertRecord, outcome: AlertOutcome) -> dict[str, Any]:
    realized_return = _number(outcome.realized_return, "realized_return")
    contribution = _number(outcome.contribution, "contribution")
    position_change = _number(alert.suggested_position_change, "suggested_position_change")
    if outcome.horizon_days != HORIZON_DAYS:
        raise AlertOutcomeError(f"提醒#{alert.id} 的归因周期不是 5 个交易日。")
    if outcome.model_version != MODEL_VERSION:
        raise AlertOutcomeError(f"提醒#{alert.id} 的归因模型版本不受支持。")
    if not isinstance(outcome.origin_date, date) or not isinstance(outcome.maturity_date, date):
        raise AlertOutcomeError(f"提醒#{alert.id} 缺少归因交易日。")
    if outcome.maturity_date <= outcome.origin_date:
        raise AlertOutcomeError(f"提醒#{alert.id} 的归因交易日顺序无效。")
    if not isinstance(alert.created_at, datetime) or not isinstance(outcome.evaluated_at, datetime):
        raise AlertOutcomeError(f"提醒#{alert.id} 缺少有效归因时间。")
    created_at = _as_utc(alert.created_at)
    evaluated_at = _as_utc(outcome.evaluated_at)
    if created_at > evaluated_at:
        raise AlertOutcomeError(f"提醒#{alert.id} 在生成前已被归因。")
    if outcome.maturity_date > evaluated_at.astimezone(MARKET_TIMEZONE).date():
        raise AlertOutcomeError(f"提醒#{alert.id} 在五日终点前已被归因。")
    if isinstance(alert.as_of, datetime):
        evidence_date = _as_utc(alert.as_of).astimezone(MARKET_TIMEZONE).date()
        if evidence_date != outcome.origin_date:
            raise AlertOutcomeError(f"提醒#{alert.id} 的证据日与归因起点不一致。")
    if realized_return <= -1:
        raise AlertOutcomeError(f"提醒#{alert.id} 的实际收益超出合法范围。")
    if not isfinite(position_change) or position_change < -1 or position_change > 1:
        raise AlertOutcomeError(f"提醒#{alert.id} 的建议仓位变化超出合法范围。")
    expected_contribution = position_change * realized_return
    if abs(contribution - expected_contribution) > 1e-10:
        raise AlertOutcomeError(f"提醒#{alert.id} 的贡献收益与原始信号不一致。")

    action = str(alert.action or "").strip().upper()
    expected_hit = _hit(action, realized_return)
    if outcome.hit is not expected_hit:
        raise AlertOutcomeError(f"提醒#{alert.id} 的命中标记与实际收益不一致。")
    return {
        "alert_id": alert.id,
        "symbol": alert.symbol,
        "action": action,
        "created_at": iso_utc(alert.created_at),
        "evidence_as_of": iso_utc(alert.as_of) if isinstance(alert.as_of, datetime) else None,
        "horizon_days": outcome.horizon_days,
        "origin_date": outcome.origin_date.isoformat(),
        "maturity_date": outcome.maturity_date.isoformat(),
        "realized_return": realized_return,
        "contribution": contribution,
        "hit": expected_hit,
        "evaluated_at": iso_utc(outcome.evaluated_at),
        "model_version": outcome.model_version,
    }


def _prior_hit_rate(session: Session, target_date: date) -> tuple[str | None, float | None]:
    previous = session.scalar(
        select(DailyReport)
        .where(
            DailyReport.kind == "post_market",
            DailyReport.report_date < target_date.isoformat(),
        )
        .order_by(DailyReport.report_date.desc())
        .limit(1)
    )
    if previous is None or not isinstance(previous.payload, dict):
        return None, None
    attribution = previous.payload.get("signal_attribution")
    if not isinstance(attribution, dict):
        return previous.report_date, None
    raw_rate = attribution.get("hit_rate_directional")
    if raw_rate is None:
        return previous.report_date, None
    rate = _number(raw_rate, "previous_hit_rate_directional")
    if rate < 0 or rate > 1:
        raise AlertOutcomeError("前一日报告的方向信号命中率超出 [0, 1]。")
    return previous.report_date, rate


def build_signal_attribution(session: Session, target_date: date) -> dict[str, Any]:
    """Build report-ready hit/miss attribution with no future-report leakage."""

    cutoff = datetime.combine(target_date, time.max, tzinfo=MARKET_TIMEZONE).astimezone(UTC)
    joined = session.execute(
        select(AlertRecord, AlertOutcome)
        .join(AlertOutcome, AlertOutcome.alert_id == AlertRecord.id)
        .order_by(AlertOutcome.evaluated_at, AlertOutcome.alert_id)
    ).all()
    rows = [
        _outcome_payload(alert, outcome)
        for alert, outcome in joined
        if isinstance(outcome.evaluated_at, datetime) and _as_utc(outcome.evaluated_at) <= cutoff
    ]
    directional = [row for row in rows if isinstance(row["hit"], bool)]
    hits = [row for row in directional if row["hit"] is True]
    misses = [row for row in directional if row["hit"] is False]
    hit_rate = round(len(hits) / len(directional), 4) if directional else None

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["action"])].append(row)
    by_action: dict[str, dict[str, Any]] = {}
    for action in sorted(grouped):
        action_rows = grouped[action]
        evaluated = [row for row in action_rows if isinstance(row["hit"], bool)]
        action_hits = sum(row["hit"] is True for row in evaluated)
        by_action[action] = {
            "outcomes": len(action_rows),
            "directional_evaluated": len(evaluated),
            "hits": action_hits,
            "hit_rate": round(action_hits / len(evaluated), 4) if evaluated else None,
            "contribution_total": round(
                sum(float(row["contribution"]) for row in action_rows),
                8,
            ),
        }

    previous_report_date, previous_hit_rate = _prior_hit_rate(session, target_date)
    hit_rate_change = (
        round(hit_rate - previous_hit_rate, 4)
        if hit_rate is not None and previous_hit_rate is not None
        else None
    )
    return {
        "horizon_days": HORIZON_DAYS,
        "model_version": MODEL_VERSION,
        "outcomes": len(rows),
        "directional_evaluated": len(directional),
        "hit_rate_directional": hit_rate,
        "previous_report_date": previous_report_date,
        "previous_hit_rate_directional": previous_hit_rate,
        "hit_rate_change": hit_rate_change,
        "hit_rate_change_pp": round(hit_rate_change * 100, 2)
        if hit_rate_change is not None
        else None,
        "top_hits": sorted(
            hits,
            key=lambda row: (-float(row["contribution"]), int(row["alert_id"])),
        )[:5],
        "top_misses": sorted(
            misses,
            key=lambda row: (float(row["contribution"]), int(row["alert_id"])),
        )[:5],
        "by_action": by_action,
    }
