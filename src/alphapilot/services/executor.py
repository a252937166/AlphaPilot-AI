from __future__ import annotations

import hashlib
from collections.abc import Mapping
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from math import floor, isclose, isfinite
from threading import RLock
from typing import Any

import pandas as pd
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings, get_settings
from alphapilot.db.models import BrokerOrder, Security, TradeProposalRecord
from alphapilot.domain.models import PortfolioState, TradeProposal, TradingMode
from alphapilot.futu.client import (
    FutuCallValidationError,
    FutuClient,
    FutuClientError,
    FutuFeatureDisabledError,
    FutuMethodNotAllowedError,
    FutuSDKError,
)
from alphapilot.risk.guardrails import TradeGuardrails
from alphapilot.services.broker import (
    BrokerError,
    fetch_account_funds,
    fetch_open_order_count,
    fetch_risk_positions,
    get_simulate_account,
)


class ExecutionBlocked(RuntimeError):
    """A runtime safety switch forbids simulated execution."""

    def __init__(self, message: str, *, halted: bool = False) -> None:
        super().__init__(message)
        self.halted = halted


class ExecutionConflict(RuntimeError):
    """The proposal state or execution-time risk decision forbids the order."""


class ExecutionUnavailable(RuntimeError):
    """Required live data or the broker result is unavailable or uncertain."""


class ExecutionRejected(RuntimeError):
    """Futu definitively rejected a syntactically valid simulated order."""


_EXECUTION_LOCK = RLock()
_ALLOWED_PAPER_MODES = frozenset({TradingMode.CONFIRM_TO_TRADE, TradingMode.PAPER_AUTO})


def _require_switches(settings: Settings) -> None:
    if not settings.paper_trading_enabled:
        raise ExecutionBlocked("模拟交易执行器未启用。")
    if not settings.futu_enable_trade:
        raise ExecutionBlocked("富途模拟交易写入开关未启用。")
    if not settings.futu_enable_trade_query:
        raise ExecutionBlocked("富途模拟账户只读查询未启用。")
    if settings.trading_halted:
        raise ExecutionBlocked("交易 Kill Switch 已开启，拒绝提交新单。", halted=True)


def _proposal(record: TradeProposalRecord) -> TradeProposal:
    try:
        proposal = TradeProposal.model_validate(record.proposal)
    except ValidationError as exc:
        raise ExecutionConflict("提案载荷无法通过执行前校验。") from exc

    if (
        proposal.proposal_id != record.proposal_id
        or proposal.symbol != record.symbol
        or proposal.side.value != record.side
        or not isclose(proposal.quantity, record.quantity, rel_tol=0.0, abs_tol=1e-9)
        or proposal.mode.value != record.mode
    ):
        raise ExecutionConflict("提案载荷与持久化索引字段不一致，已拒绝执行。")
    if proposal.mode not in _ALLOWED_PAPER_MODES:
        raise ExecutionConflict("本里程碑仅允许人工确认或纸上自动模式执行模拟单。")
    return proposal


def _symbol_code(symbol: str) -> tuple[str, str]:
    normalized = symbol.strip().upper()
    if normalized.startswith(("SH.", "SZ.")):
        market, digits = normalized.split(".", 1)
    else:
        digits = normalized
        market = "SH" if digits.startswith(("5", "6", "9")) else "SZ"
    if len(digits) != 6 or not digits.isdigit():
        raise ExecutionConflict("提案股票代码不是有效的 A 股代码。")
    expected_market = "SH" if digits.startswith(("5", "6", "9")) else "SZ"
    if market != expected_market:
        raise ExecutionConflict("提案股票代码的市场前缀不一致。")
    return digits, f"{market}.{digits}"


def _number(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ExecutionUnavailable(f"富途行情字段 {field} 不是有效数值。")
    try:
        number = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ExecutionUnavailable(f"富途行情字段 {field} 不是有效数值。") from exc
    if not isfinite(number):
        raise ExecutionUnavailable(f"富途行情字段 {field} 不是有限数值。")
    return number


def _snapshot_price(client: FutuClient, code: str) -> float:
    try:
        frame = client.quote_call_raw("get_market_snapshot", args=[[code]])
    except FutuClientError as exc:
        raise ExecutionUnavailable("富途实时行情暂不可用，未提交模拟单。") from exc
    if not isinstance(frame, pd.DataFrame) or len(frame.index) != 1:
        raise ExecutionUnavailable("富途实时行情没有返回唯一股票记录，未提交模拟单。")
    row = {str(key): value for key, value in frame.iloc[0].to_dict().items()}
    returned_code = str(row.get("code") or "").strip().upper()
    if returned_code and returned_code != code:
        raise ExecutionUnavailable("富途实时行情返回了不匹配的股票代码，未提交模拟单。")
    suspended = row.get("is_suspended", row.get("suspension"))
    if suspended is True or str(suspended).strip().upper() in {"1", "TRUE"}:
        raise ExecutionConflict("目标股票当前停牌，拒绝提交模拟单。")
    last = _number(row.get("last_price"), "last_price")
    if last <= 0:
        raise ExecutionUnavailable("富途实时行情价格无效，未提交模拟单。")
    return last


def _rounded_quantity(quantity: float) -> float:
    if not isfinite(quantity):
        raise ExecutionConflict("提案数量不是有限数值。")
    rounded = float(floor(quantity / 100.0) * 100)
    if rounded < 100:
        raise ExecutionConflict("提案数量不足一手（100 股），无法执行。")
    return rounded


def _limit_price(last: float, side: str) -> float:
    multiplier = Decimal("1.01") if side == "BUY" else Decimal("0.99")
    rounding = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    price = (Decimal(str(last)) * multiplier).quantize(Decimal("0.01"), rounding=rounding)
    if price <= 0:
        raise ExecutionConflict("模拟限价计算结果无效。")
    return float(price)


def _industry(security: Security | None) -> str | None:
    if security is None:
        return None
    for value in (security.industry_csrc, security.industry_futu, security.industry):
        if value and value.strip():
            return value.strip()
    return None


def _portfolio_state(
    session: Session,
    client: FutuClient,
    *,
    symbol: str,
    side: str,
    rounded_qty: float,
) -> PortfolioState:
    try:
        funds = fetch_account_funds(client)
        positions = fetch_risk_positions(client)
        open_orders = fetch_open_order_count(client, symbol)
    except BrokerError as exc:
        raise ExecutionUnavailable(str(exc)) from exc
    except FutuClientError as exc:
        raise ExecutionUnavailable("富途模拟账户实时状态暂不可用，未提交模拟单。") from exc

    equity = _number(funds.get("total_assets"), "total_assets")
    cash = _number(funds.get("cash"), "cash")
    if equity <= 0 or cash < 0:
        raise ExecutionUnavailable("富途模拟账户资金状态无效，未提交模拟单。")

    target_industry = _industry(session.get(Security, symbol))
    if target_industry is None:
        raise ExecutionUnavailable("证券主档缺少目标股票行业，无法完成执行前风控。")

    target_market_value = 0.0
    target_quantity = 0.0
    sector_market_value = 0.0
    today_pnl = 0.0
    for position in positions:
        position_symbol = str(position["symbol"])
        market_value = _number(position.get("market_val"), "market_val")
        quantity = _number(position.get("qty"), "qty")
        position_today_pnl = _number(position.get("today_pl_val"), "today_pl_val")
        if market_value < 0 or quantity < 0:
            raise ExecutionUnavailable("富途模拟持仓包含负数，无法完成执行前风控。")
        position_industry = _industry(session.get(Security, position_symbol))
        if position_industry is None:
            raise ExecutionUnavailable("模拟持仓存在行业未知股票，无法完成执行前风控。")
        if position_symbol == symbol:
            target_market_value += market_value
            target_quantity += quantity
        if position_industry == target_industry:
            sector_market_value += market_value
        today_pnl += position_today_pnl

    if side == "SELL" and rounded_qty > target_quantity + 1e-9:
        raise ExecutionConflict("卖出数量超过当前模拟持仓。")
    opening_equity = equity - today_pnl
    if opening_equity <= 0:
        raise ExecutionUnavailable("无法从模拟账户计算有效的当日起始权益。")
    return PortfolioState(
        equity=equity,
        cash=cash,
        daily_pnl_pct=today_pnl / opening_equity,
        current_position_pct=target_market_value / equity,
        sector_position_pct=sector_market_value / equity,
        open_orders_for_symbol=open_orders,
    )


def _response_records(response: object) -> list[dict[str, Any]]:
    if not isinstance(response, Mapping):
        raise ExecutionUnavailable("富途下单结果格式异常，订单状态待对账。")
    data = response.get("data")
    if not isinstance(data, Mapping):
        raise ExecutionUnavailable("富途下单结果缺少数据，订单状态待对账。")
    raw_records = data.get("records")
    if not isinstance(raw_records, list) or len(raw_records) != 1:
        raise ExecutionUnavailable("富途下单结果不是唯一记录，订单状态待对账。")
    raw = raw_records[0]
    if not isinstance(raw, Mapping):
        raise ExecutionUnavailable("富途下单结果包含无效记录，订单状态待对账。")
    return [{str(key): value for key, value in raw.items()}]


def _validated_futu_order_id(
    response: object,
    *,
    code: str,
    side: str,
) -> str:
    row = _response_records(response)[0]
    order_id = str(row.get("order_id") or "").strip()
    if not order_id:
        raise ExecutionUnavailable("富途下单结果缺少订单号，订单状态待对账。")
    environment = str(row.get("trd_env") or "").strip().upper()
    if environment and environment != "SIMULATE":
        raise ExecutionUnavailable("富途下单结果环境异常，订单状态待人工核对。")
    returned_code = str(row.get("code") or "").strip().upper()
    if returned_code and returned_code != code:
        raise ExecutionUnavailable("富途下单结果股票代码不匹配，订单状态待人工核对。")
    returned_side = str(row.get("trd_side") or "").strip().upper()
    if returned_side and returned_side != side:
        raise ExecutionUnavailable("富途下单结果方向不匹配，订单状态待人工核对。")
    return order_id


def _remark(proposal_id: str) -> str:
    digest = hashlib.sha256(proposal_id.encode("utf-8")).hexdigest()[:32]
    return f"alphapilot:{digest}"


def _existing_order(session: Session, proposal_id: str) -> BrokerOrder | None:
    return session.scalar(select(BrokerOrder).where(BrokerOrder.proposal_id == proposal_id))


def _risk_failure(
    session: Session,
    record: TradeProposalRecord,
    *,
    symbol: str,
    side: str,
    price: float,
    qty: float,
    reason: str,
) -> None:
    order = _existing_order(session, record.proposal_id)
    if order is None:
        order = BrokerOrder(
            proposal_id=record.proposal_id,
            symbol=symbol,
            side=side,
            price=price,
            qty=qty,
        )
        session.add(order)
    order.status = "failed"
    order.error = reason
    record.status = "exec_failed"
    session.commit()


def execute_proposal(
    session: Session,
    client: FutuClient,
    record: TradeProposalRecord,
) -> BrokerOrder:
    """Submit one guarded A-share order to Futu's SIMULATE environment only."""

    settings = get_settings()
    _require_switches(settings)

    with _EXECUTION_LOCK:
        existing = _existing_order(session, record.proposal_id)
        if existing is not None and existing.status != "failed":
            return existing
        if record.status != "approved":
            raise ExecutionConflict(f"提案状态为 {record.status}，不是可执行的 approved。")

        proposal = _proposal(record)
        symbol, code = _symbol_code(proposal.symbol)
        quantity = _rounded_quantity(proposal.quantity)
        try:
            account = get_simulate_account(client)
        except BrokerError as exc:
            raise ExecutionUnavailable(str(exc)) from exc
        except FutuClientError as exc:
            raise ExecutionUnavailable("富途模拟账户暂不可用，未提交模拟单。") from exc
        last = _snapshot_price(client, code)
        price = _limit_price(last, proposal.side.value)
        portfolio = _portfolio_state(
            session,
            client,
            symbol=symbol,
            side=proposal.side.value,
            rounded_qty=quantity,
        )
        execution_proposal = proposal.model_copy(
            update={
                "symbol": symbol,
                "quantity": quantity,
                "estimated_notional": price * quantity,
            }
        )
        decision = TradeGuardrails(settings).evaluate(execution_proposal, portfolio)
        risk_payload = dict(record.risk_decision or {})
        risk_payload["execution"] = decision.model_dump(mode="json")
        record.risk_decision = risk_payload
        if not decision.approved:
            reason = "；".join(decision.reasons)
            _risk_failure(
                session,
                record,
                symbol=symbol,
                side=proposal.side.value,
                price=price,
                qty=quantity,
                reason=reason,
            )
            raise ExecutionConflict(f"执行前风控未通过：{reason}")

        if existing is None:
            order = BrokerOrder(
                proposal_id=record.proposal_id,
                symbol=symbol,
                side=proposal.side.value,
                price=price,
                qty=quantity,
            )
            session.add(order)
        else:
            order = existing
            order.symbol = symbol
            order.side = proposal.side.value
            order.price = price
            order.qty = quantity
            order.status = "submitting"
            order.error = None
        record.status = "executing"
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            concurrent = _existing_order(session, record.proposal_id)
            if concurrent is not None:
                return concurrent
            raise ExecutionConflict("提案已被另一个执行请求占用。") from exc

        try:
            response = client.trade_call(
                "security",
                "place_order",
                kwargs={
                    "price": price,
                    "qty": quantity,
                    "code": code,
                    "trd_side": proposal.side.value,
                    "order_type": "NORMAL",
                    "acc_id": account["acc_id"],
                    "remark": _remark(record.proposal_id),
                },
                market="CN",
                environment="SIMULATE",
            )
        except (
            FutuCallValidationError,
            FutuFeatureDisabledError,
            FutuMethodNotAllowedError,
            FutuSDKError,
        ) as exc:
            order.status = "failed"
            order.error = str(exc)
            record.status = "exec_failed"
            session.commit()
            raise ExecutionRejected(f"富途拒绝模拟委托：{exc}") from exc
        except FutuClientError as exc:
            order.error = "下单结果不确定，禁止自动重试，等待订单对账。"
            session.commit()
            raise ExecutionUnavailable(order.error) from exc

        try:
            order.futu_order_id = _validated_futu_order_id(
                response,
                code=code,
                side=proposal.side.value,
            )
        except ExecutionUnavailable as exc:
            order.error = str(exc)
            session.commit()
            raise
        order.status = "submitted"
        order.error = None
        record.status = "executing"
        session.commit()
        return order
