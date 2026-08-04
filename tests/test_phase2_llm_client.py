from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import select

from alphapilot.core.config import Settings
from alphapilot.db.engine import get_session
from alphapilot.db.models import LLMCall
from alphapilot.llm import client as llm_client
from alphapilot.llm.client import LLMUnavailable, chat_json
from alphapilot.services import ai_text

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["result"],
    "properties": {"result": {"type": "string"}},
    "additionalProperties": False,
}


def _configured_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "llm_base_url": "https://llm.example.test/compatible-mode/v1",
        "llm_api_key": "test-only-key",
        "llm_model": "qwen3.6-flash",
        "llm_purpose_models": {},
    }
    values.update(overrides)
    return Settings(**values)


def _response(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request(
            "POST",
            "https://llm.example.test/compatible-mode/v1/chat/completions",
        ),
    )


def _audit_rows(purpose: str) -> list[dict[str, Any]]:
    with get_session() as session:
        rows = list(
            session.scalars(select(LLMCall).where(LLMCall.purpose == purpose).order_by(LLMCall.id))
        )
        return [
            {
                "purpose": row.purpose,
                "model": row.model,
                "ok": row.ok,
                "latency_ms": row.latency_ms,
                "prompt_tokens": row.prompt_tokens,
                "completion_tokens": row.completion_tokens,
                "error": row.error,
            }
            for row in rows
        ]


def test_chat_json_unconfigured_raises_and_audits_failure() -> None:
    purpose = "test_unconfigured"
    settings = _configured_settings(llm_base_url=None, llm_api_key=None)

    with pytest.raises(LLMUnavailable):
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            settings=settings,
        )

    rows = _audit_rows(purpose)
    assert len(rows) == 1
    assert rows[0]["model"] == "qwen3.6-flash"
    assert rows[0]["ok"] is False
    assert rows[0]["latency_ms"] >= 0
    assert rows[0]["error"]


def test_chat_json_qwen_payload_models_and_tiered_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        observed.append({"url": url, **kwargs})
        return _response(
            {
                "choices": [{"message": {"content": '{"result":"ok"}'}}],
                "usage": {"prompt_tokens": 13, "completion_tokens": 5},
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    settings = _configured_settings(llm_purpose_models={"stock_insight": "qwen3.7-plus"})
    audit_offsets = {
        purpose: len(_audit_rows(purpose))
        for purpose in ("market_summary", "event_extract", "stock_insight")
    }

    for purpose in ("market_summary", "event_extract", "stock_insight"):
        assert chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            settings=settings,
        ) == {"result": "ok"}

    assert [request["timeout"] for request in observed] == [45.0, 30.0, 60.0]
    assert [request["json"]["model"] for request in observed] == [
        "qwen3.6-flash",
        "qwen3.6-flash",
        "qwen3.7-plus",
    ]
    for request in observed:
        assert request["url"].endswith("/chat/completions")
        assert request["json"]["enable_thinking"] is False
        assert request["json"]["response_format"] == {"type": "json_object"}
        assert request["headers"]["Authorization"] == "Bearer test-only-key"
        system_content = request["json"]["messages"][0]["content"]
        assert system_content.startswith("system")
        assert '"required":["result"]' in system_content
        assert '"additionalProperties":false' in system_content

    for purpose, expected_model in (
        ("market_summary", "qwen3.6-flash"),
        ("event_extract", "qwen3.6-flash"),
        ("stock_insight", "qwen3.7-plus"),
    ):
        rows = _audit_rows(purpose)[audit_offsets[purpose] :]
        assert len(rows) == 1
        assert rows[0]["model"] == expected_model
        assert rows[0]["ok"] is True
        assert rows[0]["prompt_tokens"] == 13
        assert rows[0]["completion_tokens"] == 5
        assert rows[0]["error"] is None


def test_chat_json_retries_schema_failure_and_writes_one_logical_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "test_schema_retry"
    responses = iter(
        [
            _response({"choices": [{"message": {"content": '{"unexpected":"value"}'}}]}),
            _response(
                {
                    "choices": [{"message": {"content": '{"result":"valid"}'}}],
                    "usage": {"prompt_tokens": 21, "completion_tokens": 8},
                }
            ),
        ]
    )
    attempts = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return next(responses)

    monkeypatch.setattr(httpx, "post", fake_post)

    result = chat_json(
        purpose,
        "system",
        "user",
        RESULT_SCHEMA,
        max_retries=1,
        settings=_configured_settings(),
    )

    assert result == {"result": "valid"}
    assert attempts == 2
    rows = _audit_rows(purpose)
    assert len(rows) == 1
    assert rows[0]["ok"] is True
    assert rows[0]["prompt_tokens"] == 21
    assert rows[0]["completion_tokens"] == 8


def test_chat_json_second_invalid_response_fails_with_one_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "test_schema_exhausted"
    attempts = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return _response({"choices": [{"message": {"content": '{"wrong":true}'}}]})

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(LLMUnavailable, match="schema_validation_failed"):
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=1,
            settings=_configured_settings(),
        )

    assert attempts == 2
    rows = _audit_rows(purpose)
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert rows[0]["error"] == "schema_validation_failed"


@pytest.mark.parametrize(
    ("case_name", "schema", "content", "expected_field", "expected_constraint"),
    [
        (
            "single_required",
            {
                "type": "object",
                "required": ["alpha"],
                "properties": {"alpha": {"type": "string"}},
                "additionalProperties": False,
            },
            "{}",
            "alpha",
            "json_schema_required",
        ),
        (
            "multiple_required",
            {
                "type": "object",
                "required": ["alpha", "beta"],
                "properties": {
                    "alpha": {"type": "string"},
                    "beta": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "{}",
            "result",
            "json_schema_required",
        ),
        (
            "known_field_type",
            {
                "type": "object",
                "required": ["alpha"],
                "properties": {"alpha": {"type": "integer"}},
                "additionalProperties": False,
            },
            '{"alpha":"secret-model-value"}',
            "alpha",
            "json_schema_type",
        ),
        (
            "additional_property",
            {
                "type": "object",
                "properties": {"alpha": {"type": "string"}},
                "additionalProperties": False,
            },
            '{"secret_model_key":"secret-model-value"}',
            "result",
            "json_schema_additional_properties",
        ),
        (
            "unknown_validator",
            {
                "type": "object",
                "minProperties": 2,
                "properties": {"alpha": {"type": "string"}},
                "additionalProperties": False,
            },
            "{}",
            "result",
            "json_schema_constraint",
        ),
    ],
)
def test_chat_json_schema_failure_exposes_only_safe_structured_metadata(
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    schema: dict[str, Any],
    content: str,
    expected_field: str,
    expected_constraint: str,
) -> None:
    purpose = f"test_safe_schema_{case_name}"
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **_kwargs: _response(
            {"choices": [{"message": {"content": content}}]}
        ),
    )

    with pytest.raises(LLMUnavailable) as caught:
        chat_json(
            purpose,
            "system",
            "user",
            schema,
            max_retries=0,
            settings=_configured_settings(),
        )

    assert caught.value.reason == "schema_validation_failed"
    assert caught.value.field == expected_field
    assert caught.value.constraint == expected_constraint
    assert str(caught.value) == "LLM request failed: schema_validation_failed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "secret_model_key" not in str(caught.value)
    assert "secret-model-value" not in str(caught.value)

    rows = _audit_rows(purpose)
    assert len(rows) == 1
    assert rows[0]["error"] == "schema_validation_failed"
    assert "secret_model_key" not in str(rows[0])
    assert "secret-model-value" not in str(rows[0])


def test_chat_json_non_utf8_response_retries_and_degrades_with_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "test_non_utf8_response"
    attempts = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            content=b"\xff\xfe",
            headers={"Content-Type": "application/json; charset=utf-8"},
            request=httpx.Request("POST", "https://llm.example.test/chat/completions"),
        )

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(LLMUnavailable, match="invalid_json"):
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=1,
            settings=_configured_settings(),
        )

    assert attempts == 2
    rows = _audit_rows(purpose)
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert rows[0]["error"] == "invalid_json"


def test_chat_json_rejects_non_finite_json_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "test_non_finite_json"
    attempts = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return _response({"choices": [{"message": {"content": '{"result":NaN}'}}]})

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(LLMUnavailable, match="invalid_json"):
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=1,
            settings=_configured_settings(),
        )

    assert attempts == 2
    rows = _audit_rows(purpose)
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert rows[0]["error"] == "invalid_json"


def test_chat_json_timeout_does_not_double_purpose_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "test_timeout_no_retry"
    attempts = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("secret-bearing upstream detail")

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(LLMUnavailable, match="request_timeout"):
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=1,
            settings=_configured_settings(),
        )

    assert attempts == 1
    rows = _audit_rows(purpose)
    assert len(rows) == 1
    assert rows[0]["error"] == "request_timeout"
    assert "secret-bearing" not in str(rows[0]["error"])


def test_chat_json_discards_success_when_audit_cannot_be_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **_kwargs: _response(
            {"choices": [{"message": {"content": '{"result":"ok"}'}}]}
        ),
    )

    def fail_audit(**_kwargs: Any) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(llm_client, "_record_call", fail_audit)

    with pytest.raises(LLMUnavailable, match="audit persistence failed"):
        chat_json(
            "test_audit_failure",
            "system",
            "user",
            RESULT_SCHEMA,
            settings=_configured_settings(),
        )


def test_compose_market_summary_falls_back_without_llm() -> None:
    ai_text._clear_market_summary_cache()
    settings = _configured_settings(llm_base_url=None, llm_api_key=None)

    result = ai_text.compose_market_summary(
        settings,
        {
            "regime": {"regime": "balanced", "confidence": 0.7},
            "indices": [],
            "sectors": [],
            "breadth": {},
        },
    )

    assert result["source"] == "template"
    assert result["text"].endswith("以上为规则模板生成的观察摘要，仅描述数据，不构成投资建议。")


def test_compose_market_summary_caches_by_context_for_ten_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert ai_text.MARKET_SUMMARY_CACHE_TTL_SECONDS == 600
    ai_text._clear_market_summary_cache()
    calls: list[dict[str, Any]] = []
    clock = [1_000.0]
    monkeypatch.setattr(ai_text.time, "monotonic", lambda: clock[0])

    def fake_chat_json(
        purpose: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        del system, user, schema, kwargs
        calls.append({"purpose": purpose})
        return {"text": f"LLM summary {len(calls)}"}

    monkeypatch.setattr(ai_text, "chat_json", fake_chat_json)
    settings = _configured_settings()
    context = {
        "regime": {"confidence": 0.76, "regime": "risk_on"},
        "indices": [{"symbol": "000001", "change_pct": 1.2}],
        "sectors": [],
        "breadth": {"advancers": 8, "decliners": 2},
    }
    reordered_context = {
        "breadth": {"decliners": 2, "advancers": 8},
        "sectors": [],
        "indices": [{"change_pct": 1.2, "symbol": "000001"}],
        "regime": {"regime": "risk_on", "confidence": 0.76},
    }

    first = ai_text.compose_market_summary(settings, context)
    clock[0] += 599
    second = ai_text.compose_market_summary(settings, reordered_context)
    clock[0] += 1
    third = ai_text.compose_market_summary(
        settings,
        context,
    )
    fourth = ai_text.compose_market_summary(
        settings,
        {**context, "breadth": {"advancers": 7, "decliners": 3}},
    )

    assert first == second == {"text": "LLM summary 1", "source": "llm"}
    assert third == {"text": "LLM summary 2", "source": "llm"}
    assert fourth == {"text": "LLM summary 3", "source": "llm"}
    assert calls == [
        {"purpose": "market_summary"},
        {"purpose": "market_summary"},
        {"purpose": "market_summary"},
    ]


def test_market_summary_cache_does_not_cross_llm_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ai_text._clear_market_summary_cache()
    calls: list[bool] = []

    def fake_chat_json(
        _purpose: str,
        _system: str,
        _user: str,
        _schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        settings = kwargs["settings"]
        assert isinstance(settings, Settings)
        configured = bool(settings.llm_base_url and settings.llm_api_key)
        calls.append(configured)
        if not configured:
            raise LLMUnavailable("LLM is not configured")
        return {"text": "已配置模式摘要"}

    monkeypatch.setattr(ai_text, "chat_json", fake_chat_json)
    context = {"regime": {}, "indices": [], "sectors": [], "breadth": {}}

    live_result = ai_text.compose_market_summary(_configured_settings(), context)
    fallback_result = ai_text.compose_market_summary(
        _configured_settings(llm_base_url=None, llm_api_key=None),
        context,
    )

    assert live_result == {"text": "已配置模式摘要", "source": "llm"}
    assert fallback_result["source"] == "template"
    assert calls == [True, False]
