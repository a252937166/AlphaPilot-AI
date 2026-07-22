from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from math import isclose, isfinite
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from alphapilot.db.engine import get_session
from alphapilot.db.models import BrokerOrder, TradeProposalRecord
from alphapilot.futu.client import FutuClient, FutuClientError, get_futu_client
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register
from alphapilot.services.broker import BrokerError, get_simulate_account
from alphapilot.services.executor import paper_execution_guard, proposal_remark

logger = logging.getLogger(__name__)
SCHEDULER_TIMEZONE = ZoneInfo("Asia/Shanghai")

# Futu has more transient states than the local audit model. TIMEOUT means the
# result is unknown, so it deliberately remains active and is polled again.
FUTU_ORDER_STATUS_MAP: dict[str, str] = {
    "UNSUBMITTED": "submitting",
    "WAITING_SUBMIT": "submitting",
    "SUBMITTING": "submitting",
    "TIMEOUT": "submitting",
    "SUBMITTED": "submitted",
    "CANCELLING_ALL": "submitted",
    "FILLED_PART": "partial",
    "CANCELLING_PART": "partial",
    "FILLED_ALL": "filled",
    "CANCELLED_PART": "cancelled",
    "CANCELLED_ALL": "cancelled",
    "SUBMIT_FAILED": "failed",
    "FAILED": "failed",
    "DISABLED": "failed",
    "DELETED": "failed",
    "FILL_CANCELLED": "failed",
}

_ACTIVE_LOCAL_STATUSES = ("submitting", "submitted", "partial")
_LOCAL_STATUS_RANK = {"submitting": 0, "submitted": 1, "partial": 2}


@dataclass(frozen=True, slots=True)
class OrderTarget:
    id: int
    proposal_id: str
    futu_order_id: str | None
    symbol: str
    side: str
    qty: float
    status: str
    filled_qty: float


def _empty_stats() -> dict[str, Any]:
    return {
        "active": 0,
        "queried": 0,
        "updated": 0,
        "unchanged": 0,
        "recovered": 0,
        "filled": 0,
        "partial": 0,
        "cancelled": 0,
        "failed": 0,
        "query_errors": 0,
        "warning_count": 0,
        "warnings": [],
    }


def _warning(stats: dict[str, Any], message: str) -> None:
    logger.warning(message)
    stats["warning_count"] = int(stats["warning_count"]) + 1
    warnings = stats["warnings"]
    assert isinstance(warnings, list)
    if len(warnings) < 100:
        warnings.append(message)


def _records(response: object) -> list[dict[str, Any]]:
    if not isinstance(response, Mapping):
        raise ValueError("富途模拟委托查询返回格式异常")
    data = response.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("富途模拟委托查询缺少 data")
    raw_records = data.get("records")
    if not isinstance(raw_records, list):
        raise ValueError("富途模拟委托查询缺少 records")
    records: list[dict[str, Any]] = []
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            raise ValueError("富途模拟委托查询包含无效记录")
        records.append({str(key): value for key, value in raw.items()})
    return records


def _number(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"富途委托字段 {field} 不是有效数值")
    try:
        number = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"富途委托字段 {field} 不是有效数值") from exc
    if not isfinite(number):
        raise ValueError(f"富途委托字段 {field} 不是有限数值")
    return number


def _optional_number(value: object, field: str) -> float | None:
    if value is None or str(value).strip().upper() in {"", "N/A", "NONE"}:
        return None
    return _number(value, field)


def _expected_code(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if normalized.startswith(("SH.", "SZ.")):
        return normalized
    market = "SH" if normalized.startswith(("5", "6", "9")) else "SZ"
    return f"{market}.{normalized}"


def _identity_error(
    target: OrderTarget,
    row: Mapping[str, Any],
    *,
    require_remark: bool,
) -> str | None:
    order_id = str(row.get("order_id") or "").strip()
    if not order_id:
        return "缺少 order_id"
    if target.futu_order_id is not None and order_id != target.futu_order_id:
        return "order_id 不匹配"

    environment = str(row.get("trd_env") or "").strip().upper()
    if environment and environment != "SIMULATE":
        return "交易环境不是 SIMULATE"
    code = str(row.get("code") or "").strip().upper()
    if code != _expected_code(target.symbol):
        return "股票代码不匹配或缺失"
    side = str(row.get("trd_side") or "").strip().upper()
    if side != target.side.strip().upper():
        return "买卖方向不匹配或缺失"
    try:
        quantity = _number(row.get("qty"), "qty")
    except ValueError as exc:
        return str(exc)
    if not isclose(quantity, target.qty, rel_tol=0.0, abs_tol=1e-9):
        return "委托数量不匹配"

    remark = str(row.get("remark") or "").strip()
    expected_remark = proposal_remark(target.proposal_id)
    if require_remark and remark != expected_remark:
        return "确定性 remark 不匹配或缺失"
    if remark and remark != expected_remark:
        return "remark 不匹配"
    return None


def _query_order(
    client: FutuClient,
    *,
    account_id: int,
    futu_order_id: str,
) -> list[dict[str, Any]]:
    response = client.trade_call(
        "security",
        "order_list_query",
        kwargs={
            "acc_id": account_id,
            "order_id": futu_order_id,
            "refresh_cache": True,
        },
        market="CN",
        environment="SIMULATE",
    )
    return _records(response)


def _query_reconciliation_pool(
    client: FutuClient,
    *,
    account_id: int,
) -> list[dict[str, Any]]:
    response = client.trade_call(
        "security",
        "order_list_query",
        kwargs={"acc_id": account_id, "refresh_cache": True},
        market="CN",
        environment="SIMULATE",
    )
    return _records(response)


def _unique_order_row(
    target: OrderTarget,
    rows: list[dict[str, Any]],
    *,
    recover_by_remark: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    if recover_by_remark:
        expected_remark = proposal_remark(target.proposal_id)
        candidates = [
            row for row in rows if str(row.get("remark") or "").strip() == expected_remark
        ]
    else:
        assert target.futu_order_id is not None
        candidates = [
            row for row in rows if str(row.get("order_id") or "").strip() == target.futu_order_id
        ]
    if len(candidates) != 1:
        return None, f"匹配到 {len(candidates)} 条富途委托，要求恰好 1 条"
    error = _identity_error(target, candidates[0], require_remark=recover_by_remark)
    if error is not None:
        return None, error
    return candidates[0], None


def _target(order: BrokerOrder) -> OrderTarget:
    if order.id is None:
        raise ValueError("broker order has no primary key")
    return OrderTarget(
        id=int(order.id),
        proposal_id=order.proposal_id,
        futu_order_id=order.futu_order_id,
        symbol=order.symbol,
        side=order.side,
        qty=float(order.qty),
        status=order.status,
        filled_qty=float(order.filled_qty),
    )


def _load_targets() -> list[OrderTarget]:
    with get_session() as session:
        orders = session.scalars(
            select(BrokerOrder)
            .where(BrokerOrder.status.in_(_ACTIVE_LOCAL_STATUSES))
            .order_by(BrokerOrder.id)
        ).all()
        return [_target(order) for order in orders]


def _validated_state(
    order: BrokerOrder,
    row: Mapping[str, Any],
) -> tuple[str, str, float, float | None]:
    raw_status = str(row.get("order_status") or "").strip().upper()
    mapped_status = FUTU_ORDER_STATUS_MAP.get(raw_status)
    if mapped_status is None:
        raise LookupError(raw_status or "<empty>")

    dealt_qty = _number(row.get("dealt_qty"), "dealt_qty")
    if dealt_qty < 0 or dealt_qty > order.qty:
        raise ValueError(f"成交数量越界: dealt_qty={dealt_qty}, qty={order.qty}")
    if dealt_qty + 1e-9 < order.filled_qty:
        raise ValueError(f"成交数量倒退: dealt_qty={dealt_qty}, local={order.filled_qty}")
    avg_price = _optional_number(row.get("dealt_avg_price"), "dealt_avg_price")
    if dealt_qty > 0 and (avg_price is None or avg_price <= 0):
        raise ValueError("已有成交但缺少有效 dealt_avg_price")

    if raw_status == "FILLED_ALL" and not isclose(dealt_qty, order.qty, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("FILLED_ALL 的成交数量不等于委托数量")
    if raw_status == "CANCELLED_PART" and not 0 < dealt_qty < order.qty:
        raise ValueError("CANCELLED_PART 没有有效的部分成交数量")
    if raw_status == "CANCELLED_ALL" and dealt_qty != 0:
        raise ValueError("CANCELLED_ALL 却返回非零成交数量")
    if mapped_status in {"submitting", "submitted"} and dealt_qty > 0:
        if raw_status == "CANCELLING_ALL":
            mapped_status = "partial"
        else:
            raise ValueError(f"{raw_status} 却返回非零成交数量")
    if mapped_status == "partial" and not 0 < dealt_qty < order.qty:
        raise ValueError("部分成交状态没有有效的部分成交数量")

    current_rank = _LOCAL_STATUS_RANK.get(order.status)
    mapped_rank = _LOCAL_STATUS_RANK.get(mapped_status)
    if current_rank is not None and mapped_rank is not None and mapped_rank < current_rank:
        raise ValueError(f"状态倒退: {order.status} -> {mapped_status}")
    return raw_status, mapped_status, dealt_qty, avg_price


def _apply_row(
    target: OrderTarget,
    row: Mapping[str, Any],
    stats: dict[str, Any],
) -> bool:
    with get_session() as session:
        order = session.get(BrokerOrder, target.id)
        if order is None or order.status not in _ACTIVE_LOCAL_STATUSES:
            _warning(stats, f"broker_order={target.id} 已被并发更新，跳过本轮回填")
            return False
        if target.futu_order_id is not None and order.futu_order_id != target.futu_order_id:
            _warning(stats, f"broker_order={target.id} 富途订单号已被并发修改，跳过本轮回填")
            return False
        if target.futu_order_id is None and order.futu_order_id is not None:
            _warning(stats, f"broker_order={target.id} 已由其他请求恢复订单号，跳过本轮回填")
            return False

        current_target = _target(order)
        identity_error = _identity_error(
            current_target,
            row,
            require_remark=target.futu_order_id is None,
        )
        if identity_error is not None:
            _warning(stats, f"broker_order={target.id} 身份校验失败：{identity_error}")
            return False
        try:
            raw_status, local_status, dealt_qty, avg_price = _validated_state(order, row)
        except LookupError as exc:
            _warning(stats, f"broker_order={target.id} 收到未知富途状态 {exc}，保留原状态")
            return False
        except ValueError as exc:
            _warning(stats, f"broker_order={target.id} 回填校验失败：{exc}")
            return False

        proposal = session.scalar(
            select(TradeProposalRecord).where(TradeProposalRecord.proposal_id == order.proposal_id)
        )
        if proposal is None:
            _warning(stats, f"broker_order={target.id} 缺少关联提案，保留原状态")
            return False

        if order.futu_order_id is None:
            order.futu_order_id = str(row["order_id"]).strip()
            stats["recovered"] = int(stats["recovered"]) + 1
        order.status = local_status
        order.filled_qty = dealt_qty
        order.avg_fill_price = avg_price if dealt_qty > 0 else None
        order.error = None
        if local_status == "filled" or raw_status == "CANCELLED_PART":
            proposal.status = "executed"
        elif local_status in {"cancelled", "failed"}:
            proposal.status = "exec_failed"
            order.error = f"富途模拟委托以 {raw_status} 终止。"
        else:
            proposal.status = "executing"

        stats["updated"] = int(stats["updated"]) + 1
        if local_status in {"filled", "partial", "cancelled", "failed"}:
            stats[local_status] = int(stats[local_status]) + 1
        return True


def _sync_orders(client: FutuClient | None = None) -> dict[str, Any]:
    stats = _empty_stats()
    targets = _load_targets()
    stats["active"] = len(targets)
    if not targets:
        return stats

    resolved_client = client or get_futu_client()
    try:
        account = get_simulate_account(resolved_client)
        account_id = int(account["acc_id"])
    except (BrokerError, FutuClientError, TypeError, ValueError) as exc:
        stats["query_errors"] = 1
        _warning(stats, f"模拟账户查询失败：{type(exc).__name__}: {exc}")
        raise JobExecutionError("无法读取富途模拟账户。", stats=stats) from exc

    unresolved = [target for target in targets if target.futu_order_id is None]
    unresolved_pool: list[dict[str, Any]] | None = None
    if unresolved:
        try:
            unresolved_pool = _query_reconciliation_pool(
                resolved_client,
                account_id=account_id,
            )
            stats["queried"] = int(stats["queried"]) + 1
        except (FutuClientError, ValueError) as exc:
            stats["query_errors"] = int(stats["query_errors"]) + len(unresolved)
            _warning(
                stats,
                f"{len(unresolved)} 个无订单号预约无法按 remark 对账：{type(exc).__name__}: {exc}",
            )

    for target in targets:
        row: dict[str, Any] | None = None
        if target.futu_order_id is None:
            if unresolved_pool is not None:
                row, error = _unique_order_row(
                    target,
                    unresolved_pool,
                    recover_by_remark=True,
                )
                if error is not None:
                    _warning(
                        stats,
                        f"broker_order={target.id} remark 对账失败：{error}；"
                        "保留 submitting 且禁止自动重下",
                    )
        else:
            try:
                rows = _query_order(
                    resolved_client,
                    account_id=account_id,
                    futu_order_id=target.futu_order_id,
                )
                stats["queried"] = int(stats["queried"]) + 1
                row, error = _unique_order_row(
                    target,
                    rows,
                    recover_by_remark=False,
                )
                if error is not None:
                    _warning(stats, f"broker_order={target.id} 富途查询结果无效：{error}")
            except (FutuClientError, ValueError) as exc:
                stats["query_errors"] = int(stats["query_errors"]) + 1
                _warning(
                    stats,
                    f"broker_order={target.id} 富途查询失败：{type(exc).__name__}: {exc}",
                )
        if row is None:
            stats["unchanged"] = int(stats["unchanged"]) + 1
            continue
        if not _apply_row(target, row, stats):
            stats["unchanged"] = int(stats["unchanged"]) + 1

    if int(stats["query_errors"]) > 0:
        raise JobExecutionError(
            f"{stats['query_errors']} 个模拟订单查询失败；其余订单已独立处理。",
            stats=stats,
        )
    return stats


def sync_orders(client: FutuClient | None = None) -> dict[str, Any]:
    """Reconcile active local orders from Futu SIMULATE queries without resubmission."""

    with paper_execution_guard():
        return _sync_orders(client)


def register_order_sync_job() -> None:
    register(
        JobSpec(
            name="sync_orders",
            func=sync_orders,
            trigger=IntervalTrigger(seconds=30, timezone=SCHEDULER_TIMEZONE),
            enabled_key="paper_trading_enabled",
        )
    )
