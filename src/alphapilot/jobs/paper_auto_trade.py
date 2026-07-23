from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from math import floor, isfinite
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from alphapilot.core.config import Settings, get_settings
from alphapilot.data.base import DataProviderError, MarketDataProvider
from alphapilot.data.futu_provider import FutuMarketDataProvider
from alphapilot.data.providers import build_provider
from alphapilot.db.engine import get_session
from alphapilot.db.models import AlertRecord, BrokerOrder, TradeProposalRecord
from alphapilot.domain.models import TradeProposal, TradeSide, TradingMode
from alphapilot.futu.client import FutuClient, FutuClientError, get_futu_client
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register
from alphapilot.risk.guardrails import TradeGuardrails
from alphapilot.services import watchlist
from alphapilot.services.alert_outcomes import BUY_ACTIONS, SELL_ACTIONS
from alphapilot.services.alert_provenance import (
    AlertSourceError,
    validate_trade_alert_source,
)
from alphapilot.services.broker import (
    BrokerError,
    fetch_account_funds,
    fetch_risk_positions,
)
from alphapilot.services.executor import (
    ExecutionBlocked,
    ExecutionConflict,
    ExecutionRejected,
    ExecutionUnavailable,
    build_portfolio_state,
    execute_proposal,
)
from alphapilot.services.runtime_flags import trading_is_halted

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
TRADING_WINDOWS = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))
NON_COUNTING_ORDER_STATUSES = frozenset({"failed", "cancelled"})


def _market_now(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(MARKET_TIMEZONE)
    if value.tzinfo is None:
        return value.replace(tzinfo=MARKET_TIMEZONE)
    return value.astimezone(MARKET_TIMEZONE)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _in_trading_session(value: datetime) -> bool:
    local_time = value.time().replace(tzinfo=None)
    return any(start <= local_time < end for start, end in TRADING_WINDOWS)


def _base_stats(now: datetime) -> dict[str, Any]:
    return {
        "market_time": now.isoformat(),
        "trade_date": now.date().isoformat(),
        "skipped": None,
        "alerts_created": 0,
        "candidates": 0,
        "proposals_created": 0,
        "orders_submitted": 0,
        "proposal_ids": [],
        "order_ids": [],
        "rejected": [],
        "warnings": [],
    }


def _configuration_skip(settings: Settings) -> str | None:
    if not settings.paper_auto_trading_enabled:
        return "paper_auto_disabled"
    if settings.trading_mode.strip().lower() != TradingMode.PAPER_AUTO.value:
        return "trading_mode_not_paper_auto"
    if not settings.paper_trading_enabled:
        return "paper_trading_disabled"
    if not settings.futu_enable_trade_query:
        return "trade_query_disabled"
    if not settings.futu_enable_trade:
        return "trade_mutation_disabled"
    if settings.live_trading_enabled:
        return "live_trading_must_be_disabled"
    return None


def _cn_trade_day(client: FutuClient, target: date) -> bool:
    value = target.isoformat()
    calendar = client.quote_call_raw(
        "request_trading_days",
        args=["CN", value, value],
    )
    if not isinstance(calendar, list):
        raise FutuClientError("富途 A 股交易日历返回格式异常。")
    return any(
        isinstance(item, Mapping) and str(item.get("time") or "").strip() == value
        for item in calendar
    )


def _normalized_symbol(value: object) -> str:
    raw = str(value or "").strip().upper()
    if "." in raw:
        raw = raw.split(".", 1)[1]
    return raw


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _quote_map(
    frame: pd.DataFrame,
    *,
    now: datetime,
    max_age_seconds: int,
) -> tuple[dict[str, tuple[float, datetime]], list[str]]:
    quotes: dict[str, tuple[float, datetime]] = {}
    warnings: list[str] = []
    now_utc = now.astimezone(UTC)
    for raw in frame.to_dict(orient="records"):
        record = {str(key): value for key, value in raw.items()}
        symbol = _normalized_symbol(record.get("symbol"))
        last = _number(record.get("last"))
        raw_as_of = record.get("as_of")
        if not symbol or last is None or last <= 0 or not isinstance(raw_as_of, datetime):
            warnings.append(f"{symbol or 'unknown'}: 实时行情缺少有效价格或时间。")
            continue
        as_of = _as_utc(raw_as_of)
        age_seconds = (now_utc - as_of).total_seconds()
        if age_seconds < -5 or age_seconds > max_age_seconds:
            warnings.append(
                f"{symbol}: 实时行情时差 {age_seconds:.0f}s 超出 "
                f"{max_age_seconds}s，未自动下单。"
            )
            continue
        quotes[symbol] = (last, as_of)
    return quotes, warnings


def _latest_directional_alerts(records: list[AlertRecord]) -> list[AlertRecord]:
    latest: dict[str, AlertRecord] = {}
    for record in sorted(records, key=lambda item: item.id, reverse=True):
        if record.symbol in latest:
            continue
        if record.action not in BUY_ACTIONS | SELL_ACTIONS:
            continue
        latest[record.symbol] = record
    return list(latest.values())


def _market_day(value: datetime, target: date) -> bool:
    return _as_utc(value).astimezone(MARKET_TIMEZONE).date() == target


def _daily_auto_state(
    target: date,
) -> tuple[int, set[str], int]:
    with get_session() as session:
        proposals = session.scalars(
            select(TradeProposalRecord).where(
                TradeProposalRecord.mode == TradingMode.PAPER_AUTO.value
            )
        ).all()
        orders = session.scalars(select(BrokerOrder)).all()
    day_proposals = [record for record in proposals if _market_day(record.created_at, target)]
    submitted_orders = [
        order
        for order in orders
        if _market_day(order.created_at, target)
        and order.status not in NON_COUNTING_ORDER_STATUSES
    ]
    return (
        len(day_proposals),
        {record.symbol for record in day_proposals},
        len(submitted_orders),
    )


def _side(alert: AlertRecord) -> TradeSide | None:
    if alert.action in BUY_ACTIONS:
        return TradeSide.BUY
    if alert.action in SELL_ACTIONS:
        return TradeSide.SELL
    return None


def _quantity(
    *,
    alert: AlertRecord,
    side: TradeSide,
    last: float,
    equity: float,
    cash: float,
    positions: list[dict[str, Any]],
    settings: Settings,
) -> float:
    suggested = _number(alert.suggested_notional)
    if suggested is None or suggested == 0:
        return 0.0
    if (side == TradeSide.BUY and suggested < 0) or (
        side == TradeSide.SELL and suggested > 0
    ):
        return 0.0

    desired_notional = abs(suggested)
    budget = min(
        desired_notional,
        equity * settings.paper_auto_max_order_notional_pct,
    )
    position = next(
        (item for item in positions if str(item.get("symbol")) == alert.symbol),
        None,
    )
    if side == TradeSide.BUY:
        current_market_value = (
            _number(position.get("market_val")) if position is not None else 0.0
        ) or 0.0
        remaining_position_budget = max(
            0.0,
            equity * settings.max_single_position_pct - current_market_value,
        )
        budget = min(budget, cash, remaining_position_budget) * 0.98
        estimate_price = last * 1.01
        max_quantity = float("inf")
    else:
        estimate_price = last * 0.99
        held_quantity = (
            _number(position.get("qty")) if position is not None else 0.0
        ) or 0.0
        max_quantity = floor(held_quantity / 100.0) * 100.0

    if estimate_price <= 0 or budget <= 0:
        return 0.0
    sized = floor(budget / estimate_price / 100.0) * 100.0
    return float(min(sized, max_quantity))


def _proposal_for(
    alert: AlertRecord,
    *,
    side: TradeSide,
    quantity: float,
    last: float,
    quote_as_of: datetime,
    now: datetime,
) -> TradeProposal:
    proposal_id = f"paper-auto-{alert.id}-{side.value.lower()}"
    return TradeProposal(
        proposal_id=proposal_id,
        idempotency_key=proposal_id,
        symbol=alert.symbol,
        side=side,
        quantity=quantity,
        estimated_notional=round(last * quantity, 2),
        confidence=alert.confidence,
        market_data_as_of=quote_as_of,
        model_version=str(alert.model_version or ""),
        mode=TradingMode.PAPER_AUTO,
        source_alert_id=alert.id,
        metadata={
            "source": "paper-auto-scheduler",
            "evaluated_at": now.astimezone(UTC).isoformat(),
            "source_suggested_notional": alert.suggested_notional,
            "target_low": alert.target_low,
            "target_high": alert.target_high,
        },
    )


def _target_matches_quote(alert: AlertRecord, last: float) -> bool:
    target_low = _number(alert.target_low)
    target_high = _number(alert.target_high)
    return bool(
        target_low is not None
        and target_high is not None
        and 0 < target_low < target_high
        and target_high >= last * 0.5
        and target_low <= last * 1.5
    )


def _persist_and_execute(
    proposal: TradeProposal,
    *,
    client: FutuClient,
    settings: Settings,
    now: datetime,
) -> tuple[TradeProposalRecord | None, BrokerOrder | None, str | None]:
    with get_session() as session:
        existing = session.scalars(
            select(TradeProposalRecord).where(
                TradeProposalRecord.idempotency_key == proposal.idempotency_key
            )
        ).first()
        if existing is not None:
            return existing, None, "duplicate"
        try:
            evidence = validate_trade_alert_source(
                session,
                proposal,
                now=now.astimezone(UTC),
            )
            portfolio = build_portfolio_state(session, client, proposal)
            decision = TradeGuardrails(settings).evaluate(
                proposal,
                portfolio,
                now=now.astimezone(UTC),
            )
        except (AlertSourceError, ExecutionConflict, ExecutionUnavailable) as exc:
            return None, None, str(exc)

        record = TradeProposalRecord(
            proposal_id=proposal.proposal_id,
            idempotency_key=proposal.idempotency_key,
            symbol=proposal.symbol,
            side=proposal.side.value,
            quantity=proposal.quantity,
            estimated_notional=proposal.estimated_notional,
            confidence=proposal.confidence,
            mode=proposal.mode.value,
            status="approved" if decision.approved else "rejected_by_risk",
            proposal=proposal.model_dump(mode="json"),
            risk_decision=decision.model_dump(mode="json"),
            source_alert_id=evidence.alert.id,
            reviewed_at=now.astimezone(UTC),
        )
        session.add(record)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            return None, None, "duplicate"
        if not decision.approved:
            return record, None, "；".join(decision.reasons)
        session.commit()

        try:
            order = execute_proposal(
                session,
                client,
                record,
                now=now.astimezone(UTC),
            )
        except (
            ExecutionBlocked,
            ExecutionConflict,
            ExecutionRejected,
            ExecutionUnavailable,
        ) as exc:
            session.refresh(record)
            if record.status == "approved":
                record.status = "exec_failed"
            return record, None, str(exc)
        return record, order, None


def paper_auto_trade(
    client: FutuClient | None = None,
    provider: MarketDataProvider | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh audited alerts and execute bounded Futu SIMULATE orders only."""

    settings = get_settings()
    market_now = _market_now(now)
    stats = _base_stats(market_now)
    if reason := _configuration_skip(settings):
        stats["skipped"] = reason
        return stats
    if not _in_trading_session(market_now):
        stats["skipped"] = "outside_trading_session"
        return stats

    resolved_client = client or get_futu_client()
    with get_session() as session:
        if trading_is_halted(session, settings):
            stats["skipped"] = "trading_halted"
            return stats
    try:
        if not _cn_trade_day(resolved_client, market_now.date()):
            stats["skipped"] = "non_trading_day"
            return stats
    except FutuClientError as exc:
        raise JobExecutionError(
            "无法确认 A 股交易日，未运行模拟自动交易。",
            stats={**stats, "failure_type": type(exc).__name__},
        ) from exc

    resolved_provider = provider or build_provider(settings.default_data_provider, settings)
    try:
        with get_session() as session:
            created = watchlist.refresh_alerts(session, resolved_provider)
            alert_ids = [record.id for record in created]
        stats["alerts_created"] = len(alert_ids)
    except Exception as exc:
        raise JobExecutionError(
            "自动刷新提醒失败，未创建提案或订单。",
            stats={**stats, "failure_type": type(exc).__name__},
        ) from exc

    if not alert_ids:
        stats["skipped"] = "no_new_alerts"
        return stats
    with get_session() as session:
        records = list(
            session.scalars(
                select(AlertRecord)
                .where(AlertRecord.id.in_(alert_ids))
                .order_by(AlertRecord.id.desc())
            ).all()
        )
    candidates = _latest_directional_alerts(records)
    stats["candidates"] = len(candidates)
    if not candidates:
        stats["skipped"] = "no_directional_alerts"
        return stats

    proposal_count, attempted_symbols, submitted_count = _daily_auto_state(
        market_now.date()
    )
    stats["day_auto_proposals_before"] = proposal_count
    stats["day_orders_before"] = submitted_count
    if submitted_count >= settings.paper_auto_max_orders_per_day:
        stats["skipped"] = "daily_order_limit"
        return stats

    actionable = sorted(
        (
            alert
            for alert in candidates
            if alert.symbol not in attempted_symbols
            and alert.confidence >= settings.min_trade_confidence
        ),
        key=lambda alert: (alert.confidence, alert.id),
        reverse=True,
    )
    if not actionable:
        stats["skipped"] = "no_eligible_alerts"
        return stats

    try:
        quote_frame = FutuMarketDataProvider(
            settings,
            client=resolved_client,
        ).get_snapshot([alert.symbol for alert in actionable])
        quotes, quote_warnings = _quote_map(
            quote_frame,
            now=market_now,
            max_age_seconds=settings.max_market_data_age_seconds,
        )
        stats["warnings"].extend(quote_warnings)
        funds = fetch_account_funds(resolved_client)
        positions = fetch_risk_positions(resolved_client)
    except (BrokerError, DataProviderError, FutuClientError, ValueError) as exc:
        raise JobExecutionError(
            "模拟账户或实时行情不可用，未创建提案或订单。",
            stats={**stats, "failure_type": type(exc).__name__},
        ) from exc

    equity = _number(funds.get("total_assets"))
    cash = _number(funds.get("cash"))
    if equity is None or equity <= 0 or cash is None or cash < 0:
        raise JobExecutionError(
            "模拟账户资金字段无效，未创建提案或订单。",
            stats={**stats, "failure_type": "InvalidAccountFunds"},
    )

    for alert in actionable:
        if submitted_count >= settings.paper_auto_max_orders_per_day:
            break
        side = _side(alert)
        quote = quotes.get(alert.symbol)
        if side is None or quote is None:
            continue
        last, quote_as_of = quote
        if not _target_matches_quote(alert, last):
            stats["rejected"].append(
                {
                    "alert_id": alert.id,
                    "symbol": alert.symbol,
                    "reason": "目标区间与当前真实价格不在同一量级",
                }
            )
            continue
        quantity = _quantity(
            alert=alert,
            side=side,
            last=last,
            equity=equity,
            cash=cash,
            positions=positions,
            settings=settings,
        )
        if quantity < 100:
            stats["rejected"].append(
                {"alert_id": alert.id, "symbol": alert.symbol, "reason": "不足一手或无可卖持仓"}
            )
            continue
        proposal = _proposal_for(
            alert,
            side=side,
            quantity=quantity,
            last=last,
            quote_as_of=quote_as_of,
            now=market_now,
        )
        record, order, error = _persist_and_execute(
            proposal,
            client=resolved_client,
            settings=settings,
            now=market_now,
        )
        if record is not None and error != "duplicate":
            proposal_count += 1
            stats["proposals_created"] += 1
            stats["proposal_ids"].append(record.id)
        if order is not None:
            submitted_count += 1
            stats["orders_submitted"] += 1
            stats["order_ids"].append(order.id)
            break
        elif error and error != "duplicate":
            stats["rejected"].append(
                {"alert_id": alert.id, "symbol": alert.symbol, "reason": error}
            )

    if stats["proposals_created"] == 0 and stats["orders_submitted"] == 0:
        stats["skipped"] = "no_executable_alerts"
    return stats


def register_paper_auto_trade_job() -> None:
    register(
        JobSpec(
            name="paper_auto_trade",
            func=paper_auto_trade,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour="9,13,14",
                minute=35,
                timezone=MARKET_TIMEZONE,
            ),
            enabled_key="paper_auto_trading_enabled",
        )
    )


__all__ = ["paper_auto_trade", "register_paper_auto_trade_job"]
