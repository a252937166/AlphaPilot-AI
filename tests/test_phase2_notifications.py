from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from alphapilot.api.dependencies import db_session_dependency
from alphapilot.db.models import (
    AlertRecord,
    Base,
    Disclosure,
    DomainEvent,
    Notification,
    WatchlistItem,
)
from alphapilot.engines.thesis_drift import evaluate
from alphapilot.jobs import registry as job_registry
from alphapilot.jobs.registry import JOBS, JobSpec, register, run_job
from alphapilot.main import app
from alphapilot.services.event_extract import DisclosureExtraction, persist_disclosure_event
from alphapilot.services.events import emit
from alphapilot.services.notifications import push, push_alert

NOW = datetime(2026, 7, 22, 7, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    database = create_engine(
        f"sqlite:///{tmp_path / 'notifications.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(database)
    return database


@contextmanager
def _api_client(engine: Engine) -> Iterator[TestClient]:
    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[db_session_dependency] = override_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)


def _notification_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Notification)) or 0)


def test_notification_table_and_push_are_transactional_and_idempotent(engine: Engine) -> None:
    assert inspect(engine).has_table("notifications")

    with Session(engine, expire_on_commit=False) as session:
        first = push(
            session,
            kind="system",
            ref_id="system:daily-ready",
            title="日报已生成",
            body="今日复盘已可查看。",
        )
        duplicate = push(
            session,
            kind="system",
            ref_id="system:daily-ready",
            title="不会覆盖原通知",
            body="幂等重试不得产生第二条记录。",
        )
        assert duplicate.id == first.id
        assert duplicate.title == "日报已生成"
        assert _notification_count(session) == 1
        committed_id = first.id
        session.commit()

    with Session(engine) as session:
        persisted = push(
            session,
            kind="system",
            ref_id="system:daily-ready",
            title="仍然不会覆盖",
            body="跨事务重试也必须幂等。",
        )
        assert persisted.id == committed_id
        push(
            session,
            kind="system",
            ref_id="system:rolled-back",
            title="临时通知",
            body="此事务随后回滚。",
        )
        assert _notification_count(session) == 2
        session.rollback()

    with Session(engine) as session:
        assert _notification_count(session) == 1
        assert session.scalar(
            select(Notification).where(Notification.ref_id == "system:rolled-back")
        ) is None

        with pytest.raises(ValueError, match="kind"):
            push(
                session,
                kind="invalid",  # type: ignore[arg-type]
                ref_id="invalid:kind",
                title="非法通知",
                body="不应写入。",
            )
        with pytest.raises(ValueError, match="level"):
            push(
                session,
                kind="system",
                ref_id="invalid:level",
                title="非法通知",
                body="不应写入。",
                level="critical",  # type: ignore[arg-type]
            )


def test_alert_adapter_uses_namespaced_ref_and_user_facing_content(engine: Engine) -> None:
    with Session(engine) as session:
        alert = AlertRecord(
            symbol="600519",
            action="REVIEW_REQUIRED",
            urgency="HIGH",
            confidence=0.72,
            suggested_position_change=0.0,
            reasons=["投资逻辑出现变化", "请核对最新公告"],
            invalidation="人工复核后关闭",
            model_version="fixture-v1",
            as_of=NOW,
        )
        session.add(alert)
        session.flush()

        notification = push_alert(session, alert)
        duplicate = push_alert(session, alert)

        assert notification.id == duplicate.id
        assert notification.ref_id == f"alert:{alert.id}"
        assert notification.title == "600519 · 需要复核"
        assert notification.body == "投资逻辑出现变化；请核对最新公告"
        assert notification.level == "warn"
        assert _notification_count(session) == 1


def test_event_emit_notifies_only_at_threshold_and_deduplicates_source(engine: Engine) -> None:
    with Session(engine) as session:
        weak = emit(
            session,
            symbol="600519",
            event_type="market_regime_change",
            title="risk_off",
            strength=0.59,
            source_ref="fixture:weak-event",
            occurred_at=NOW,
        )
        strong = emit(
            session,
            symbol="600519",
            event_type="market_regime_change",
            title="risk_off event_shock",
            direction=-0.8,
            strength=0.6,
            source_ref="fixture:strong-event",
            occurred_at=NOW,
        )
        duplicate = emit(
            session,
            symbol="600519",
            event_type="market_regime_change",
            title="重复调用不得覆盖",
            direction=0.8,
            strength=0.9,
            source_ref="fixture:strong-event",
            occurred_at=NOW,
        )

        assert duplicate.id == strong.id
        notifications = list(session.scalars(select(Notification)).all())
        assert len(notifications) == 1
        assert notifications[0].ref_id == f"event:{strong.id}"
        assert notifications[0].level == "warn"
        assert notifications[0].title == "重要事件 · 风险规避 事件冲击"
        assert f"event:{weak.id}" not in {row.ref_id for row in notifications}


def test_disclosure_event_notifies_once_when_strength_crosses_threshold(engine: Engine) -> None:
    weak = DisclosureExtraction(
        subtype="contract",
        direction=0.2,
        strength=0.5,
        summary="合同影响仍待确认。",
        source_quote="合同",
        source="rule",
    )
    strong = DisclosureExtraction(
        subtype="contract",
        direction=0.8,
        strength=0.75,
        summary="合同影响已达到重要事件阈值。",
        source_quote="重大合同",
        source="llm",
    )

    with Session(engine) as session:
        disclosure = Disclosure(
            symbol="600519",
            title="公司签署重大合同公告",
            url="https://example.test/disclosure.pdf",
            category="重大合同",
            published_at=NOW,
            source="cninfo",
        )
        session.add(disclosure)
        session.flush()

        event = persist_disclosure_event(session, disclosure, weak)
        assert event is not None
        assert _notification_count(session) == 0

        upgraded = persist_disclosure_event(session, disclosure, strong)
        repeated = persist_disclosure_event(session, disclosure, strong)

        assert upgraded is not None
        assert repeated is not None
        assert repeated.id == upgraded.id == event.id
        rows = list(session.scalars(select(Notification)).all())
        assert len(rows) == 1
        assert rows[0].ref_id == f"event:{event.id}"

        new_disclosure = Disclosure(
            symbol="600519",
            title="公司新增重大合同公告",
            url="https://example.test/strong-disclosure.pdf",
            category="重大合同",
            published_at=NOW,
            source="cninfo",
        )
        session.add(new_disclosure)
        session.flush()
        new_event = persist_disclosure_event(session, new_disclosure, strong)
        assert new_event is not None
        assert _notification_count(session) == 2
        assert session.scalar(
            select(Notification).where(Notification.ref_id == f"event:{new_event.id}")
        ) is not None


def test_thesis_transition_creates_event_and_alert_notifications(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(
            WatchlistItem(
                symbol="600519",
                display_name="贵州茅台",
                thesis="测试投资逻辑",
                thesis_state="unchanged",
            )
        )
        session.add(
            DomainEvent(
                symbol="600519",
                event_type="disclosure",
                direction=-0.7,
                strength=0.5,
                title="重要风险公告",
                summary="重要风险公告",
                source_ref="fixture:negative-disclosure",
                occurred_at=NOW - timedelta(hours=1),
                ingested_at=NOW - timedelta(minutes=30),
            )
        )
        session.flush()

        decision = evaluate(session, "600519", evaluated_at=NOW, event_only=True)

        assert decision is not None
        assert decision[0] == "weakened"
        rows = list(session.scalars(select(Notification).order_by(Notification.id)).all())
        assert [row.kind for row in rows] == ["event", "alert"]
        assert all(row.ref_id.startswith(f"{row.kind}:") for row in rows)
        assert rows[0].level == "warn"
        assert rows[1].level == "warn"


def test_failed_job_persists_one_sanitized_error_notification(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def isolated_session() -> Iterator[Session]:
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(job_registry, "get_session", isolated_session)
    job_name = "test_notification_failure"
    secret = "https://user:private-token@example.test/job?api_key=do-not-expose"

    def failing_task() -> dict[str, Any]:
        raise RuntimeError(secret)

    register(JobSpec(name=job_name, func=failing_task, trigger=IntervalTrigger(hours=1)))
    try:
        record = run_job(job_name)
    finally:
        JOBS.pop(job_name, None)

    assert record.status == "failed"
    assert record.error is not None and secret in record.error
    with Session(engine) as session:
        rows = list(session.scalars(select(Notification)).all())
        assert len(rows) == 1
        notification = rows[0]
        assert notification.ref_id == f"job:{record.id}"
        assert notification.title == f"任务失败 · {job_name}"
        assert notification.body == "任务执行失败，请查看任务审计详情。"
        assert notification.level == "error"
        assert "private-token" not in notification.body
        assert "example.test" not in notification.body


def test_notification_api_lists_reads_and_serializes_utc(engine: Engine) -> None:
    created_ids: list[int] = []
    with Session(engine) as session:
        for index, kind in enumerate(("system", "alert", "job")):
            row = push(
                session,
                kind=kind,  # type: ignore[arg-type]
                ref_id=f"{kind}:api-{index}",
                title=f"测试通知 {index}",
                body="用于验证通知中心 API。",
                level="error" if kind == "job" else "info",
            )
            row.created_at = NOW + timedelta(minutes=index)
            created_ids.append(row.id)
        session.commit()

    with _api_client(engine) as client:
        count = client.get("/v1/notifications/unread-count")
        listed = client.get("/v1/notifications", params={"limit": 10})

        assert count.status_code == 200
        assert count.json() == {"unread_count": 3}
        assert listed.status_code == 200
        payload = listed.json()["notifications"]
        assert [row["id"] for row in payload] == list(reversed(created_ids))
        assert set(payload[0]) == {
            "id",
            "kind",
            "ref_id",
            "title",
            "body",
            "level",
            "read_at",
            "created_at",
        }
        assert all(row["read_at"] is None for row in payload)
        assert all(row["created_at"].endswith("+00:00") for row in payload)

        one = client.post(
            "/v1/notifications/read",
            json={"ids": [created_ids[0], created_ids[0], 999_999]},
        )
        assert one.status_code == 200
        assert one.json() == {"updated": 1, "unread_count": 2}
        repeated = client.post(
            "/v1/notifications/read",
            json={"ids": [created_ids[0]]},
        )
        assert repeated.status_code == 200
        assert repeated.json() == {"updated": 0, "unread_count": 2}

        unread = client.get(
            "/v1/notifications",
            params={"unread_only": True, "limit": 10},
        )
        assert unread.status_code == 200
        assert {row["id"] for row in unread.json()["notifications"]} == set(created_ids[1:])

        all_notifications = client.post("/v1/notifications/read", json={"all": True})
        assert all_notifications.status_code == 200
        assert all_notifications.json() == {"updated": 2, "unread_count": 0}
        assert client.get("/v1/notifications/unread-count").json() == {"unread_count": 0}

        read_rows = client.get("/v1/notifications", params={"limit": 10}).json()[
            "notifications"
        ]
        assert all(row["read_at"].endswith("+00:00") for row in read_rows)


def test_notification_read_api_rejects_ambiguous_and_coerced_inputs(engine: Engine) -> None:
    invalid_payloads: list[dict[str, Any]] = [
        {},
        {"ids": []},
        {"all": False},
        {"ids": [1], "all": True},
        {"ids": [0]},
        {"ids": [True]},
        {"ids": ["1"]},
        {"all": "true"},
        {"all_": True},
        {"all": True, "extra": "forbidden"},
        {"ids": list(range(1, 202))},
    ]

    with _api_client(engine) as client:
        for payload in invalid_payloads:
            response = client.post("/v1/notifications/read", json=payload)
            assert response.status_code == 422, (payload, response.text)

        assert client.get("/v1/notifications", params={"limit": 0}).status_code == 422
        assert client.get("/v1/notifications", params={"limit": 201}).status_code == 422
