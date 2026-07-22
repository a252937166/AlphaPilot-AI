from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from alphapilot.core.timeutil import iso_utc
from alphapilot.db.models import AlertRecord, DomainEvent, JobRun, Notification

NotificationKind = Literal["alert", "event", "job", "system"]
NotificationLevel = Literal["info", "warn", "error"]

_NOTIFICATION_KINDS = frozenset({"alert", "event", "job", "system"})
_NOTIFICATION_LEVELS = frozenset({"info", "warn", "error"})

_ACTION_LABELS = {
    "BUY_CANDIDATE": "买入候选",
    "ADD": "加仓",
    "REDUCE": "减仓",
    "EXIT": "退出",
    "STOP": "止损",
    "WATCH": "观察",
    "HOLD": "持有",
    "REVIEW": "复核",
    "REVIEW_REQUIRED": "需要复核",
}
_EVENT_TEXT_LABELS = {
    "risk_on": "风险偏好",
    "risk_off": "风险规避",
    "trend_up": "上涨趋势",
    "trend_down": "下跌趋势",
    "range": "区间震荡",
    "event_shock": "事件冲击",
}


def _required_text(value: object, field: str, *, max_length: int | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"通知字段 {field} 不能为空")
    if max_length is not None and len(text) > max_length:
        raise ValueError(f"通知字段 {field} 不能超过 {max_length} 个字符")
    return text


def _localized_event_text(value: str) -> str:
    localized = value
    for token, label in _EVENT_TEXT_LABELS.items():
        localized = localized.replace(token, label)
    return localized


def push(
    session: Session,
    *,
    kind: NotificationKind,
    ref_id: str | int,
    title: str,
    body: str,
    level: NotificationLevel = "info",
) -> Notification:
    """Idempotently persist one notification in the caller's transaction."""

    if kind not in _NOTIFICATION_KINDS:
        raise ValueError("通知 kind 不合法")
    if level not in _NOTIFICATION_LEVELS:
        raise ValueError("通知 level 不合法")
    normalized_ref = _required_text(ref_id, "ref_id", max_length=128)
    normalized_title = _required_text(title, "title")
    normalized_body = _required_text(body, "body")
    existing = session.scalar(
        select(Notification)
        .where(
            Notification.kind == kind,
            Notification.ref_id == normalized_ref,
        )
        .limit(1)
    )
    if existing is not None:
        return existing

    values = {
        "kind": kind,
        "ref_id": normalized_ref,
        "title": normalized_title,
        "body": normalized_body,
        "level": level,
    }
    if session.get_bind().dialect.name == "sqlite":
        statement = (
            sqlite_insert(Notification)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["kind", "ref_id"])
        )
        session.execute(statement)
        session.flush()
        row = session.scalar(
            select(Notification)
            .where(
                Notification.kind == kind,
                Notification.ref_id == normalized_ref,
            )
            .limit(1)
        )
        if row is None:
            raise RuntimeError("通知原子写入失败")
        return row

    row = Notification(**values)
    session.add(row)
    session.flush()
    return row


def push_alert(session: Session, alert: AlertRecord) -> Notification:
    if alert.id is None:
        session.flush()
    if alert.id is None:
        raise RuntimeError("提醒尚未持久化，无法创建通知")
    action = str(alert.action or "").strip().upper()
    action_label = _ACTION_LABELS.get(action, "新提醒")
    reasons = [str(item).strip() for item in (alert.reasons or []) if str(item).strip()]
    body = "；".join(reasons) or alert.invalidation or "请查看提醒详情与失效条件。"
    level: NotificationLevel = (
        "warn"
        if str(alert.urgency or "").upper() in {"HIGH", "CRITICAL"}
        or action in {"EXIT", "STOP", "REVIEW_REQUIRED"}
        else "info"
    )
    return push(
        session,
        kind="alert",
        ref_id=f"alert:{alert.id}",
        title=f"{alert.symbol} · {action_label}",
        body=body,
        level=level,
    )


def push_event(session: Session, event: DomainEvent) -> Notification | None:
    if event.strength < 0.6:
        return None
    if event.id is None:
        session.flush()
    if event.id is None:
        raise RuntimeError("事件尚未持久化，无法创建通知")
    return push(
        session,
        kind="event",
        ref_id=f"event:{event.id}",
        title=f"重要事件 · {_localized_event_text(event.title)}",
        body=_localized_event_text(event.summary or event.title),
        level="warn" if event.direction < 0 else "info",
    )


def push_job_failure(session: Session, job_run: JobRun) -> Notification | None:
    if job_run.status != "failed":
        return None
    if job_run.id is None:
        session.flush()
    if job_run.id is None:
        raise RuntimeError("任务审计尚未持久化，无法创建通知")
    return push(
        session,
        kind="job",
        ref_id=f"job:{job_run.id}",
        title=f"任务失败 · {job_run.job_name}",
        body="任务执行失败，请查看任务审计详情。",
        level="error",
    )


def list_notifications(
    session: Session,
    *,
    unread_only: bool = False,
    limit: int = 50,
) -> list[Notification]:
    if not 1 <= limit <= 200:
        raise ValueError("通知 limit 必须在 1 到 200 之间")
    query = select(Notification)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    return list(
        session.scalars(
            query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)
        ).all()
    )


def unread_count(session: Session) -> int:
    value = session.scalar(
        select(func.count()).select_from(Notification).where(Notification.read_at.is_(None))
    )
    return int(value or 0)


def mark_read(
    session: Session,
    *,
    ids: list[int] | None = None,
    all_notifications: bool = False,
    read_at: datetime | None = None,
) -> int:
    normalized_ids = sorted(set(ids or []))
    if any(value <= 0 for value in normalized_ids):
        raise ValueError("通知 id 必须为正整数")
    if all_notifications == bool(normalized_ids):
        raise ValueError("ids 与 all=true 必须且只能提供一种")
    timestamp = read_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    else:
        timestamp = timestamp.astimezone(UTC)
    statement = update(Notification).where(Notification.read_at.is_(None))
    if not all_notifications:
        statement = statement.where(Notification.id.in_(normalized_ids))
    result = cast(
        CursorResult[Any],
        session.execute(
            statement.values(read_at=timestamp).execution_options(synchronize_session=False)
        ),
    )
    return max(0, int(result.rowcount or 0))


def notification_payload(row: Notification) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "ref_id": row.ref_id,
        "title": row.title,
        "body": row.body,
        "level": row.level,
        "read_at": iso_utc(row.read_at),
        "created_at": iso_utc(row.created_at),
    }
