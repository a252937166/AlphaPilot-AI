from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphapilot.data.provenance import AUDITED_DAILY_BAR_SOURCES
from alphapilot.db.models import AdjFactor, DailyBar, Security
from alphapilot.engines.factors import MARKET_TIMEZONE

_BPS_DENOMINATOR = 10_000.0
_PRICE_TICK = Decimal("0.01")
_MARKET_OPEN = time(9, 30)


@dataclass(frozen=True, slots=True)
class CostModel:
    commission_bps: float = 2.5
    commission_min: float = 5.0
    stamp_duty_bps: float = 10.0
    transfer_bps: float = 0.2
    slippage_bps: float = 5.0

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field.name} must be numeric")
            if not isfinite(float(value)) or float(value) < 0:
                raise ValueError(f"{field.name} must not be negative")


def _side(side: str) -> str:
    normalized = str(side).strip().lower()
    if normalized not in {"buy", "sell"}:
        raise ValueError("side must be 'buy' or 'sell'")
    return normalized


def trade_cost_components(
    notional: float,
    side: str,
    model: CostModel = CostModel(),
) -> dict[str, float]:
    """Return an explicit, auditable cost breakdown for one fill."""

    if isinstance(notional, bool) or not isinstance(notional, (int, float)):
        raise ValueError("notional must be numeric")
    value = float(notional)
    if not isfinite(value) or value < 0:
        raise ValueError("notional must not be negative")
    normalized_side = _side(side)
    if value == 0:
        return {
            "commission": 0.0,
            "stamp_duty": 0.0,
            "transfer_fee": 0.0,
            "slippage": 0.0,
        }

    commission = max(
        value * float(model.commission_bps) / _BPS_DENOMINATOR,
        float(model.commission_min),
    )
    stamp_duty = (
        value * float(model.stamp_duty_bps) / _BPS_DENOMINATOR
        if normalized_side == "sell"
        else 0.0
    )
    transfer_fee = value * float(model.transfer_bps) / _BPS_DENOMINATOR
    slippage = value * float(model.slippage_bps) / _BPS_DENOMINATOR
    return {
        "commission": commission,
        "stamp_duty": stamp_duty,
        "transfer_fee": transfer_fee,
        "slippage": slippage,
    }


def trade_cost(
    notional: float,
    side: str,
    model: CostModel = CostModel(),
) -> float:
    """Return commission, tax, transfer fee, and slippage for one fill."""

    return float(sum(trade_cost_components(notional, side, model).values()))


def _price_limit_ratio(security: Security, trade_date: date) -> float:
    snapshot_at = security.snapshot_at
    st_status_known = False
    if isinstance(snapshot_at, datetime):
        aware = (
            snapshot_at.replace(tzinfo=UTC).astimezone(MARKET_TIMEZONE)
            if snapshot_at.tzinfo is None
            else snapshot_at.astimezone(MARKET_TIMEZONE)
        )
        st_status_known = (
            aware.date() == trade_date
            and aware.time().replace(tzinfo=None) <= _MARKET_OPEN
        )
    if not st_status_known or security.is_st:
        return 0.05

    board = str(security.board or "").strip().lower()
    if security.symbol.startswith(("4", "8", "92")) or any(
        label in board for label in ("北交", "北证", "bse")
    ):
        return 0.30
    if security.symbol.startswith(("300", "301", "688", "689")) or any(
        label in board for label in ("创业", "科创", "chinext", "star")
    ):
        return 0.20
    return 0.10


def _limit_price(reference: float, ratio: float, *, upper: bool) -> float:
    multiplier = Decimal("1") + Decimal(str(ratio))
    if not upper:
        multiplier = Decimal("1") - Decimal(str(ratio))
    value = Decimal(str(reference)) * multiplier
    return float(value.quantize(_PRICE_TICK, rounding=ROUND_HALF_UP))


def tradable_at_open(
    session: Session,
    symbol: str,
    trade_date: date,
    side: str,
) -> bool:
    """Apply suspension and one-price-limit feasibility at the target open."""

    normalized_side = _side(side)
    security = session.get(Security, symbol)
    if security is None:
        return False
    current = session.execute(
        select(
            DailyBar.open,
            DailyBar.volume,
            AdjFactor.adj_factor,
        )
        .outerjoin(
            AdjFactor,
            (AdjFactor.symbol == DailyBar.symbol)
            & (AdjFactor.trade_date == DailyBar.trade_date),
        )
        .where(
            DailyBar.symbol == symbol,
            DailyBar.trade_date == trade_date,
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
        )
        .limit(1)
    ).first()
    if current is None:
        return False
    open_price, volume, current_factor = current
    current_values = (open_price, volume, current_factor)
    if any(
        value is None
        or not isfinite(float(value))
        or float(value) <= 0
        for value in current_values
    ):
        return False

    previous = session.execute(
        select(
            DailyBar.close,
            AdjFactor.adj_factor,
        )
        .outerjoin(
            AdjFactor,
            (AdjFactor.symbol == DailyBar.symbol)
            & (AdjFactor.trade_date == DailyBar.trade_date),
        )
        .where(
            DailyBar.symbol == symbol,
            DailyBar.trade_date < trade_date,
            DailyBar.source.in_(AUDITED_DAILY_BAR_SOURCES),
        )
        .order_by(DailyBar.trade_date.desc())
        .limit(1)
    ).first()
    if previous is None:
        return False
    previous_close, previous_factor = previous
    previous_values = (previous_close, previous_factor)
    if any(
        value is None
        or not isfinite(float(value))
        or float(value) <= 0
        for value in previous_values
    ):
        return False

    reference = (
        float(previous_close) * float(previous_factor) / float(current_factor)
    )
    ratio = _price_limit_ratio(security, trade_date)
    if normalized_side == "buy":
        return float(open_price) < _limit_price(reference, ratio, upper=True)
    return float(open_price) > _limit_price(reference, ratio, upper=False)


def t_plus_one_sellable(acquired_on: date, trade_date: date) -> bool:
    """A position acquired today cannot be sold until a later trading day."""

    return trade_date > acquired_on
