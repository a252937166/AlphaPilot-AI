"""Model routing: choice work to jev, text work to the Codex gateway, the rest unchanged."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from alphapilot.core.config import get_settings
from alphapilot.db.engine import get_session
from alphapilot.db.models import LLMCall
from alphapilot.llm import router
from alphapilot.llm.client import LLMUnavailable
from alphapilot.services import event_extract

SCHEMA = {
    "type": "object",
    "required": ["text", "tags", "score"],
    "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 10},
        "tags": {"type": "array", "maxItems": 2, "items": {"type": "string"}},
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "kind": {"enum": ["a", "b"]},
    },
    "additionalProperties": False,
}


@pytest.fixture
def codex(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    settings = get_settings()
    monkeypatch.setattr(settings, "codex_api_key", "cxk_test")
    monkeypatch.setattr(settings, "codex_api_base_url", "http://gateway.test")
    monkeypatch.setattr(settings, "codex_api_timeout_seconds", 120.0)
    return []


def _transport(
    seen: list[dict[str, Any]], status: int, body: dict[str, Any]
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "url": str(request.url),
                "auth": request.headers.get("authorization"),
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def test_routes_follow_the_owner_rule_and_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert [
        router.route_for(p)
        for p in ("market_summary", "stock_insight", "review_advice", "market_feed_polish")
    ] == ["codex"] * 4
    assert router.route_for("event_extract") == "jev"
    assert router.route_for("p4_news_event") == "default"
    monkeypatch.setattr(get_settings(), "llm_routes", {"stock_insight": "default"})
    assert router.route_for("stock_insight") == "default"


def test_strict_schema_closes_objects_and_drops_limits_that_are_checked_locally() -> None:
    strict = router.strict_schema(SCHEMA)
    assert (
        strict["required"] == ["text", "tags", "score", "kind"]
        and strict["additionalProperties"] is False
    )
    assert (
        "maxLength" not in strict["properties"]["text"]
        and "maxItems" not in strict["properties"]["tags"]
    )
    assert "minimum" not in strict["properties"]["score"] and strict["properties"]["kind"] == {
        "enum": ["a", "b"]
    }
    assert SCHEMA["properties"]["text"]["maxLength"] == 10  # the caller's schema is not mutated


def test_repair_trims_and_clamps_to_the_original_schema() -> None:
    fixed = router.repair(
        {"text": "0123456789ABC", "tags": ["x", "y", "z"], "score": 1.7, "kind": "a"}, SCHEMA
    )
    assert fixed == {"text": "0123456789", "tags": ["x", "y"], "score": 1, "kind": "a"}


def test_codex_json_success_sends_deadline_and_records_the_call(
    codex: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        router,
        "_TRANSPORT",
        _transport(
            codex,
            200,
            {
                "json": {
                    "text": "市净率很低的一段过长说明",
                    "tags": ["a"],
                    "score": 0.4,
                    "kind": "b",
                },
                "usage": {"input_tokens": 13000, "output_tokens": 30},
            },
        ),
    )
    before = _calls("router_test")
    out = router.codex_json("router_test", "系统", "用户", SCHEMA)
    assert out == {"text": "市净率很低的一段过长", "tags": ["a"], "score": 0.4, "kind": "b"}
    sent = codex[0]
    assert sent["url"] == "http://gateway.test/v1/run" and sent["auth"] == "Bearer cxk_test"
    assert (
        sent["body"]["prompt"] == "系统\n\n用户"
        and sent["body"]["timeout_s"] == 120
        and sent["body"]["deadline_s"] == 180
    )
    assert sent["body"]["output_schema"]["additionalProperties"] is False
    rows = _calls("router_test")
    assert (
        len(rows) == len(before) + 1
        and rows[-1].ok
        and rows[-1].prompt_tokens == 13000
        and rows[-1].model == "gpt-6-luna"
    )


def _calls(purpose: str) -> list[LLMCall]:
    with get_session() as session:
        return list(
            session.scalars(select(LLMCall).where(LLMCall.purpose == purpose).order_by(LLMCall.id))
        )


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (
            503,
            {"error": {"type": "deadline_unreachable", "message": "deadline cannot be met"}},
            "codex_http_503",
        ),
        (200, {"output": "not json"}, "invalid_json"),
        (200, {"json": {"text": "ok", "tags": [], "score": 0.1, "kind": "z"}}, "schema_violation"),
    ],
)
def test_codex_json_failures_raise_llm_unavailable(
    codex: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    body: dict[str, Any],
    reason: str,
) -> None:
    monkeypatch.setattr(router, "_TRANSPORT", _transport(codex, status, body))
    with pytest.raises(LLMUnavailable) as caught:
        router.codex_json("router_test_fail", "s", "u", SCHEMA)
    assert caught.value.reason == reason
    assert not _calls("router_test_fail")[-1].ok


def test_codex_json_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "codex_api_key", None)
    with pytest.raises(LLMUnavailable) as caught:
        router.codex_json("x", "s", "u", SCHEMA)
    assert caught.value.reason == "codex_not_configured"


def test_chat_json_dispatches_by_route(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        router, "codex_json", lambda purpose, *a, **k: calls.append(f"codex:{purpose}") or {"ok": 1}
    )
    monkeypatch.setattr(
        router.client,
        "chat_json",
        lambda purpose, *a, **k: calls.append(f"default:{purpose}") or {"ok": 2},
    )
    assert router.chat_json("stock_insight", "s", "u", {}) == {"ok": 1}
    assert router.chat_json("some_other_purpose", "s", "u", {}) == {"ok": 2}
    with pytest.raises(LLMUnavailable) as caught:
        router.chat_json("event_extract", "s", "u", {})
    assert caught.value.reason == "routed_to_jev"
    assert calls == ["codex:stock_insight", "default:some_other_purpose"]


class _FakeJev:
    model = "jev-1.13.0"

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.states: list[dict[str, Any]] = []

    def ask(self, state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
        self.states.append(state)
        return {
            "model": self.model,
            "answers": self.answers,
            "usage": {"input_tokens": 420, "output_tokens": 30},
        }

    def close(self) -> None:
        pass


JEV_ANSWERS = {
    "event_type": {
        "type": "choice",
        "choice": "regulation",
        "probabilities": {"regulation": 0.9},
        "confidence": 0.8,
    },
    "direction": {
        "type": "score",
        "probabilities": {"0": 0.8, "1": 0.2, "2": 0.0, "3": 0.0, "4": 0.0},
    },
    "strength": {"type": "score", "probabilities": {"0": 0.0, "1": 0.0, "2": 0.2, "3": 0.8}},
    "horizon": {"type": "choice", "choice": "20", "probabilities": {"20": 0.7}, "confidence": 0.5},
}


def test_score_mean_scales_levels_to_unit_interval() -> None:
    assert router.score_mean(JEV_ANSWERS["direction"], 5) == pytest.approx(0.05)
    assert router.score_mean(JEV_ANSWERS["strength"], 4) == pytest.approx((2 * 0.2 + 3 * 0.8) / 3)
    with pytest.raises(ValueError):
        router.score_mean({"probabilities": {}}, 4)


def test_jev_event_answers_in_the_event_schema_shape() -> None:
    title = "关于收到中国证券监督管理委员会立案告知书的公告"
    fake = _FakeJev(JEV_ANSWERS)
    out = event_extract.jev_event(title, jev=fake)
    assert out["event_type"] == "regulation" and out["horizon_days"] == 20
    assert out["direction"] == pytest.approx(-0.9) and out["strength"] == pytest.approx(
        0.933, abs=1e-3
    )
    assert out["source_quote"] == title and out["summary"].startswith("jev 判定为")
    assert fake.states[0]["announcement_title"] == title
    assert event_extract._validated_llm_result(out, title) is not None


def test_classify_disclosure_uses_jev_and_falls_back_to_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    title = "关于收到中国证券监督管理委员会立案告知书的公告"

    def answered(t: str, **k: Any) -> dict[str, Any]:
        return {
            "event_type": "regulation",
            "direction": -0.9,
            "strength": 0.9,
            "horizon_days": 20,
            "summary": f"jev 判定为监管：{t}"[:120],
            "source_quote": t,
        }

    monkeypatch.setattr(event_extract, "jev_event", answered)
    ok = event_extract.classify_disclosure(title)
    assert ok.subtype == "regulation" and ok.source == "llm" and ok.direction == pytest.approx(-0.9)

    def broken(t: str, **k: Any) -> dict[str, Any]:
        raise LLMUnavailable("jev down", reason="jev_failed")

    monkeypatch.setattr(event_extract, "jev_event", broken)
    fallback = event_extract.classify_disclosure(title)
    assert fallback.source == "rule"
