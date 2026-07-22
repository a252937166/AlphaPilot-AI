from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, settings_dependency
from alphapilot.api.routes import reports as report_routes
from alphapilot.core.config import Settings
from alphapilot.data.mock import MockMarketDataProvider
from alphapilot.db.models import (
    AlertOutcome,
    AlertRecord,
    Base,
    DailyReport,
    DomainEvent,
    FactorValue,
    LLMCall,
    SectorConstituent,
    Security,
)
from alphapilot.llm import client as llm_client
from alphapilot.llm.client import LLMUnavailable
from alphapilot.main import app
from alphapilot.services import reports as report_service

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
TARGET_DATE = date(2026, 7, 22)
ORIGIN_DATE = date(2026, 7, 14)
MATURITY_DATE = date(2026, 7, 21)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    database = create_engine(
        f"sqlite:///{tmp_path / 'phase2-reports.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(database)
    return database


def _utc_at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=MARKET_TIMEZONE).astimezone(UTC)


def _add_outcome(
    session: Session,
    *,
    symbol: str,
    action: str,
    position_change: float,
    realized_return: float,
    hit: bool | None,
    evaluated_at: datetime | None = None,
) -> AlertRecord:
    alert = AlertRecord(
        symbol=symbol,
        action=action,
        urgency="MEDIUM",
        confidence=0.7,
        suggested_position_change=position_change,
        reasons=[],
        model_version="alert-v1.0.0",
        as_of=_utc_at(ORIGIN_DATE, 9, 30),
        created_at=_utc_at(ORIGIN_DATE, 10),
    )
    session.add(alert)
    session.flush()
    session.add(
        AlertOutcome(
            alert_id=alert.id,
            horizon_days=5,
            origin_date=ORIGIN_DATE,
            maturity_date=MATURITY_DATE,
            realized_return=realized_return,
            hit=hit,
            contribution=position_change * realized_return,
            model_version="signal-attribution-v1.0.0",
            evaluated_at=evaluated_at or _utc_at(TARGET_DATE, 9),
        )
    )
    return alert


def _seed_grouped_outcomes(session: Session) -> None:
    cases = [
        ("600001", "BUY_CANDIDATE", 0.10, 0.10, True),
        ("600002", "BUY_CANDIDATE", 0.10, -0.10, False),
        ("600003", "REDUCE", -0.10, -0.05, True),
        ("600004", "WATCH", 0.00, 0.02, None),
        ("600005", "ADD", 0.10, 0.02, True),
    ]
    for symbol, action, position_change, realized_return, hit in cases:
        _add_outcome(
            session,
            symbol=symbol,
            action=action,
            position_change=position_change,
            realized_return=realized_return,
            hit=hit,
        )

    # Exactly at the following Shanghai midnight is future information and must
    # not leak into a report for TARGET_DATE.
    _add_outcome(
        session,
        symbol="600099",
        action="EXIT",
        position_change=-0.10,
        realized_return=-0.10,
        hit=True,
        evaluated_at=_utc_at(TARGET_DATE + timedelta(days=1), 0),
    )

    # Current Futu membership is authoritative.  The legacy security industry is
    # deliberately contradictory so this fixture detects the wrong join.
    session.add_all(
        [
            Security(symbol="600001", name="甲", industry="旧行业"),
            Security(symbol="600002", name="乙", industry="旧行业"),
            Security(symbol="600003", name="丙", industry="旧行业"),
            SectorConstituent(
                plate_code="SH.BK0001",
                plate_name="银行",
                symbol="SH.600001",
                refreshed_at=_utc_at(TARGET_DATE, 8),
            ),
            SectorConstituent(
                plate_code="SH.BK0001",
                plate_name="银行",
                symbol="SH.600002",
                refreshed_at=_utc_at(TARGET_DATE, 8),
            ),
            SectorConstituent(
                plate_code="SH.BK0002",
                plate_name="科技",
                symbol="SH.600003",
                refreshed_at=_utc_at(TARGET_DATE, 8),
            ),
        ]
    )

    # Full-origin-date cross section is [-4..4].  Alert 600004 has z=-1 and is
    # therefore mid-tercile (full cross section), while an outcome-only tertile
    # calculation would incorrectly classify it as low.  raw deliberately points
    # in the opposite direction so the service must use zscore.
    factor_values = {
        "601001": (-4.0, 4.0),
        "600001": (-3.0, 3.0),
        "601002": (-2.0, 2.0),
        "600004": (-1.0, 1.0),
        "600002": (0.0, 0.0),
        "601003": (1.0, -1.0),
        "601004": (2.0, -2.0),
        "600003": (3.0, -3.0),
        "601005": (4.0, -4.0),
    }
    session.add_all(
        [
            FactorValue(
                symbol=symbol,
                trade_date=ORIGIN_DATE,
                factor="volatility_20d",
                raw=raw,
                zscore=zscore,
                model_version="factor-v1.0.0",
            )
            for symbol, (zscore, raw) in factor_values.items()
        ]
    )
    session.commit()


def _rows_by_ref(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["ref"]): row for row in payload["statistics"]}


def test_grouped_statistics_use_current_sector_and_origin_cross_section(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(engine) as session:
        _seed_grouped_outcomes(session)

        def unavailable(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise LLMUnavailable("offline")

        monkeypatch.setattr(report_service, "chat_json", unavailable)
        result = report_service.build_improvement_suggestions(
            session,
            Settings(),
            TARGET_DATE,
        )

    assert result["source"] == "statistics"
    assert result["sector_membership_basis"] == "current_snapshot"
    assert result["volatility_basis"] == "origin_date_cross_section_zscore"
    rows = _rows_by_ref(result)

    assert rows["action:BUY_CANDIDATE"] == {
        **rows["action:BUY_CANDIDATE"],
        "outcomes": 2,
        "directional_evaluated": 2,
        "hits": 1,
        "hit_rate": 0.5,
    }
    assert rows["sector:SH.BK0001"]["group_label"] == "银行"
    assert rows["sector:SH.BK0001"]["outcomes"] == 2
    assert "sector:旧行业" not in rows
    assert rows["volatility_tercile:low"]["outcomes"] == 1
    assert rows["volatility_tercile:mid"]["outcomes"] == 2
    assert rows["volatility_tercile:high"]["outcomes"] == 1
    assert rows["volatility_tercile:unavailable"]["outcomes"] == 1

    # Every evaluated outcome is represented once in every dimension; missing
    # membership/factor data becomes an explicit unknown bucket, never a drop.
    for dimension in ("action", "sector", "volatility_tercile"):
        assert (
            sum(int(row["outcomes"]) for row in rows.values() if row["dimension"] == dimension) == 5
        )
    sector_unknown = next(
        row
        for row in rows.values()
        if row["dimension"] == "sector" and row["group_label"] == "板块未知"
    )
    assert sector_unknown["outcomes"] == 2


def test_non_directional_action_has_null_hit_rate_and_future_outcome_is_excluded(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(engine) as session:
        _seed_grouped_outcomes(session)
        monkeypatch.setattr(
            report_service,
            "chat_json",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(LLMUnavailable("offline")),
        )
        result = report_service.build_improvement_suggestions(
            session,
            Settings(),
            TARGET_DATE,
        )

    rows = _rows_by_ref(result)
    watch = rows["action:WATCH"]
    assert watch["outcomes"] == 1
    assert watch["directional_evaluated"] == 0
    assert watch["hits"] == 0
    assert watch["hit_rate"] is None
    assert "action:EXIT" not in rows


def test_valid_llm_receives_only_aggregate_rows_and_keeps_statistics(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(engine) as session:
        _seed_grouped_outcomes(session)
        calls: list[dict[str, Any]] = []

        def valid_llm(*args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append({"args": args, "kwargs": kwargs})
            return {
                "suggestions": [
                    {
                        "title": title,
                        "text": text,
                        "basis_refs": ["action:BUY_CANDIDATE"],
                    }
                    for title, text in zip(
                        ("入场复核", "条件收紧", "命中关注", "证据保留"),
                        ("复核买入候选", "收紧入场条件", "关注命中变化", "保留统计证据"),
                        strict=True,
                    )
                ]
            }

        monkeypatch.setattr(report_service, "chat_json", valid_llm)
        result = report_service.build_improvement_suggestions(
            session,
            Settings(),
            TARGET_DATE,
        )

    assert len(calls) == 1
    assert calls[0]["args"][0] == "review_advice"
    schema = calls[0]["args"][3]
    suggestion_properties = schema["properties"]["suggestions"]["items"]["properties"]
    assert suggestion_properties["title"]["pattern"] == r"^[^0-9０-９%％]*$"
    assert suggestion_properties["text"]["pattern"] == r"^[^0-9０-９%％]*$"
    request = json.loads(str(calls[0]["args"][2]))
    assert set(request) == {"horizon_days", "grouping_basis", "statistics"}
    assert request["grouping_basis"] == {
        "sector_membership": "current_snapshot",
        "volatility_tercile": "origin_date_cross_section_zscore",
    }
    serialized = json.dumps(request, ensure_ascii=False)
    assert "600001" not in serialized
    assert "alert_id" not in serialized
    assert result["source"] == "llm"
    assert len(result["suggestions"]) == 4
    assert result["statistics"]
    assert all(item["basis"] for item in result["suggestions"])
    assert result["fallback_reason"] is None


@pytest.mark.parametrize(
    "llm_result",
    [
        LLMUnavailable("offline"),
        {
            "suggestions": [
                {
                    "title": "无效引用",
                    "text": "这条内容必须整体降级",
                    "basis_refs": ["action:NOT_REAL"],
                }
            ]
        },
        {
            "suggestions": [
                {
                    "title": "编造阈值",
                    "text": "建议将阈值调整为99%",
                    "basis_refs": ["action:BUY_CANDIDATE"],
                }
            ]
        },
        {
            "suggestions": [
                {
                    "title": "借用周期数字",
                    "text": "建议将仓位调整至5",
                    "basis_refs": ["action:BUY_CANDIDATE"],
                }
            ]
        },
        {
            "suggestions": [
                {
                    "title": "中文数量绕过",
                    "text": "建议将仓位调整至五成",
                    "basis_refs": ["action:BUY_CANDIDATE"],
                }
            ]
        },
    ],
)
def test_llm_unavailable_or_invalid_falls_back_as_one_unit(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    llm_result: object,
) -> None:
    with Session(engine) as session:
        _seed_grouped_outcomes(session)

        def llm(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            if isinstance(llm_result, Exception):
                raise llm_result
            assert isinstance(llm_result, dict)
            return llm_result

        monkeypatch.setattr(report_service, "chat_json", llm)
        result = report_service.build_improvement_suggestions(
            session,
            Settings(),
            TARGET_DATE,
        )

    assert result["source"] == "statistics"
    assert result["suggestions"] == []
    assert result["statistics"]
    assert result["fallback_reason"]
    assert result["empty_reason"] is None


def test_no_directional_samples_skip_llm_but_keep_null_rate_statistics(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def unexpected_llm(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise AssertionError("non-directional samples must not call the LLM")

    monkeypatch.setattr(report_service, "chat_json", unexpected_llm)
    with Session(engine) as session:
        _add_outcome(
            session,
            symbol="600010",
            action="WATCH",
            position_change=0.0,
            realized_return=0.03,
            hit=None,
        )
        session.commit()
        result = report_service.build_improvement_suggestions(
            session,
            Settings(),
            TARGET_DATE,
        )

    assert calls == 0
    assert result["source"] == "statistics"
    assert result["suggestions"] == []
    assert result["statistics"]
    assert result["fallback_reason"]
    assert _rows_by_ref(result)["action:WATCH"]["hit_rate"] is None


def test_no_outcomes_is_an_honest_empty_state_and_skips_llm(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        report_service,
        "chat_json",
        lambda *_args, **_kwargs: pytest.fail("empty reports must not call the LLM"),
    )
    with Session(engine) as session:
        result = report_service.build_improvement_suggestions(
            session,
            Settings(),
            TARGET_DATE,
        )

    assert result["source"] == "statistics"
    assert result["suggestions"] == []
    assert result["statistics"] == []
    assert result["empty_reason"]
    assert result["fallback_reason"] is None


def test_review_advice_real_client_uses_owned_audit_transaction(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []

    @contextmanager
    def owned_session() -> Iterator[Session]:
        with Session(engine) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        requests.append({"url": url, **kwargs})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "suggestions": [
                                        {
                                            "title": "复核入场条件",
                                            "text": "建议扩大回测覆盖并保留统计依据",
                                            "basis_refs": ["action:BUY_CANDIDATE"],
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 21, "completion_tokens": 9},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(llm_client, "get_session", owned_session)
    monkeypatch.setattr(httpx, "post", fake_post)
    settings = Settings(
        llm_base_url="https://llm.example.test/compatible-mode/v1",
        llm_api_key="test-only-key",
        llm_model="qwen3.6-flash",
    )
    with Session(engine) as session:
        _seed_grouped_outcomes(session)
        result = report_service.build_improvement_suggestions(
            session,
            settings,
            TARGET_DATE,
        )

    assert result["source"] == "llm"
    assert len(requests) == 1
    assert requests[0]["timeout"] == 45.0
    assert requests[0]["json"]["enable_thinking"] is False
    with Session(engine) as session:
        audits = list(
            session.scalars(
                select(LLMCall).where(LLMCall.purpose == "review_advice").order_by(LLMCall.id)
            )
        )
    assert len(audits) == 1
    assert audits[0].model == "qwen3.6-flash"
    assert audits[0].ok is True
    assert audits[0].prompt_tokens == 21
    assert audits[0].completion_tokens == 9
    assert audits[0].error is None


def _event(
    index: int,
    occurred_at: datetime,
    *,
    event_type: str,
    symbol: str | None = "600001",
    ingested_at: datetime | None = None,
) -> DomainEvent:
    return DomainEvent(
        symbol=symbol,
        event_type=event_type,
        direction=0.1,
        strength=0.7,
        title=f"事件{index}",
        summary=f"事实{index}",
        source_ref=f"report-test:{index}",
        occurred_at=occurred_at,
        ingested_at=ingested_at or occurred_at,
    )


def test_event_timeline_uses_shanghai_day_latest_ten_and_market_events(
    engine: Engine,
) -> None:
    event_types = [
        "disclosure",
        "capital_anomaly",
        "market_regime_change",
        "thesis_shift",
        "unrecognized",
    ]
    with Session(engine) as session:
        in_day = [
            _event(
                index,
                _utc_at(TARGET_DATE, 9) + timedelta(minutes=30 * index),
                event_type=event_types[index % len(event_types)],
                symbol=None if index == 11 else "600001",
            )
            for index in range(12)
        ]
        session.add_all(in_day)
        session.add_all(
            [
                _event(
                    20,
                    _utc_at(TARGET_DATE, 0) - timedelta(microseconds=1),
                    event_type="disclosure",
                ),
                _event(
                    21,
                    _utc_at(TARGET_DATE + timedelta(days=1), 0),
                    event_type="disclosure",
                ),
                _event(
                    22,
                    _utc_at(TARGET_DATE, 14, 45),
                    event_type="disclosure",
                    ingested_at=_utc_at(TARGET_DATE, 15, 31),
                ),
            ]
        )
        session.commit()

        result = report_service.build_event_timeline(
            session,
            TARGET_DATE,
            now=_utc_at(TARGET_DATE, 15, 30),
        )

    assert result["timezone"] == "Asia/Shanghai"
    assert result["empty_reason"] is None
    items = result["items"]
    assert len(items) == 10
    assert [item["title"] for item in items] == [f"事件{index}" for index in range(11, 1, -1)]
    assert items[0]["symbol"] is None
    assert all(str(item["occurred_at"]).endswith("+00:00") for item in items)
    assert [item["occurred_at"] for item in items] == sorted(
        (item["occurred_at"] for item in items),
        reverse=True,
    )
    styles = {item["event_type"]: (item["type_label"], item["type_color"]) for item in items}
    assert styles["disclosure"] == ("公告", "blue")
    assert styles["capital_anomaly"] == ("资金异动", "yellow")
    assert styles["market_regime_change"] == ("市场状态", "purple")
    assert styles["thesis_shift"] == ("逻辑变化", "red")
    assert styles["unrecognized"] == ("其他", "gray")


def test_event_timeline_empty_state(engine: Engine) -> None:
    with Session(engine) as session:
        result = report_service.build_event_timeline(
            session,
            TARGET_DATE,
            now=_utc_at(TARGET_DATE, 15, 30),
        )

    assert result == {
        "items": [],
        "empty_reason": "当日暂无已入库的重要事件。",
        "timezone": "Asia/Shanghai",
    }


def test_report_api_post_get_upserts_and_persists_s5_fields(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summaries = iter(("第一版复盘", "第二版复盘"))

    monkeypatch.setattr(report_routes, "get_provider", lambda _name=None: MockMarketDataProvider())
    monkeypatch.setattr(report_service.market_data, "index_quotes", lambda _settings: [])
    monkeypatch.setattr(report_service, "tracked_overview", lambda _session, _provider: [])
    monkeypatch.setattr(report_service, "list_items", lambda _session: [])
    monkeypatch.setattr(
        report_service,
        "compose_market_summary",
        lambda _settings, _context, _session: {
            "source": "template",
            "text": next(summaries),
        },
    )

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[db_session_dependency] = override_session
    app.dependency_overrides[settings_dependency] = lambda: Settings()
    try:
        with TestClient(app) as client:
            first = client.post(
                "/v1/reports/daily/generate",
                params={"provider": "mock", "report_date": TARGET_DATE.isoformat()},
            )
            second = client.post(
                "/v1/reports/daily/generate",
                params={"provider": "mock", "report_date": TARGET_DATE.isoformat()},
            )
            fetched = client.get(
                "/v1/reports/daily",
                params={"report_date": TARGET_DATE.isoformat()},
            )
    finally:
        app.dependency_overrides.pop(db_session_dependency, None)
        app.dependency_overrides.pop(settings_dependency, None)

    assert first.status_code == 200
    assert second.status_code == 200
    assert fetched.status_code == 200
    assert first.json()["ai_summary"]["text"] == "第一版复盘"
    assert second.json()["ai_summary"]["text"] == "第二版复盘"
    assert fetched.json() == second.json()
    assert set(fetched.json()["improvement_suggestions"]) >= {
        "source",
        "suggestions",
        "statistics",
        "empty_reason",
        "fallback_reason",
        "sector_membership_basis",
        "volatility_basis",
    }
    assert set(fetched.json()["event_timeline"]) == {
        "items",
        "empty_reason",
        "timezone",
    }
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DailyReport)) == 1
        persisted = session.get(
            DailyReport,
            {"report_date": TARGET_DATE.isoformat(), "kind": "post_market"},
        )
        assert persisted is not None
        assert persisted.payload == second.json()
