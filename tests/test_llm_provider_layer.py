"""Offline coverage for the switchable DashScope/Friday provider layer.

No test here touches the network. The Friday wire shape is proved by asserting
the request object that ``chat_json`` hands to ``httpx.post``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import pathlib
import re
from typing import Any, ClassVar

import httpx
import pytest
from sqlalchemy import select

from alphapilot.core.config import Settings
from alphapilot.db.engine import get_session
from alphapilot.db.models import LLMCall
from alphapilot.llm import client as llm_client
from alphapilot.llm import providers
from alphapilot.llm.client import LLMUnavailable, chat_json

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["result"],
    "properties": {"result": {"type": "string"}},
    "additionalProperties": False,
}

# Obviously fake. The internal platform's real address is configuration-only
# and must never appear in this repository.
FRIDAY_BASE_URL = "https://provider.invalid"
FRIDAY_PATH = "/v1/chat"
FRIDAY_ENDPOINT = FRIDAY_BASE_URL + FRIDAY_PATH


def _dashscope_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "llm_provider": "dashscope",
        "llm_base_url": "https://llm.example.test/compatible-mode/v1",
        "llm_api_key": "test-only-key",
        "llm_model": "qwen3.6-flash",
        "llm_purpose_models": {},
    }
    values.update(overrides)
    return Settings(**values)


def _friday_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "llm_provider": "friday",
        # Present but unused on the Friday path; both credential sets coexist.
        "llm_base_url": "https://llm.example.test/compatible-mode/v1",
        "llm_api_key": "test-only-key",
        "llm_model": "qwen3.6-flash",
        "llm_friday_base_url": FRIDAY_BASE_URL,
        "llm_friday_completions_path": FRIDAY_PATH,
        "llm_friday_app_id": "test-only-app-id",
        "llm_purpose_models": {},
    }
    values.update(overrides)
    return Settings(**values)


def _response(payload: dict[str, Any], **kwargs: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request("POST", FRIDAY_ENDPOINT),
        **kwargs,
    )


def _audit_rows(purpose: str) -> list[dict[str, Any]]:
    with get_session() as session:
        rows = list(
            session.scalars(
                select(LLMCall).where(LLMCall.purpose == purpose).order_by(LLMCall.id)
            )
        )
        return [
            {"model": row.model, "ok": row.ok, "error": row.error} for row in rows
        ]


def _capture(
    monkeypatch: pytest.MonkeyPatch,
    responder: Any,
) -> list[dict[str, Any]]:
    observed: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        observed.append({"url": url, **kwargs})
        return responder(len(observed) - 1)

    monkeypatch.setattr(httpx, "post", fake_post)
    return observed


# --------------------------------------------------------------------------
# Profile table
# --------------------------------------------------------------------------


def test_registry_holds_exactly_the_two_supported_platforms() -> None:
    assert providers.provider_names() == ("dashscope", "friday")
    assert providers.DEFAULT_PROVIDER == "dashscope"
    assert providers.get_profile(None) is providers.DASHSCOPE
    assert providers.get_profile("") is providers.DASHSCOPE
    assert providers.get_profile("  FRIDAY ") is providers.FRIDAY


def test_unknown_provider_name_is_rejected() -> None:
    with pytest.raises(providers.UnknownProviderError) as excinfo:
        providers.get_profile("openai")
    assert "dashscope" in str(excinfo.value)
    assert "friday" in str(excinfo.value)


def test_dashscope_profile_matches_the_shipped_behaviour() -> None:
    profile = providers.DASHSCOPE
    assert profile.completions_path == "/chat/completions"
    assert profile.default_base_url is None
    assert profile.auth_scheme == "Bearer"
    assert profile.supports_temperature is True
    assert profile.supports_response_format is True
    assert profile.thinking_off == providers.THINKING_OFF_QWEN_ENABLE_THINKING
    assert profile.json_extraction == providers.JSON_EXTRACTION_STRICT
    assert profile.trust_env is True
    assert profile.extra_headers == ()
    # No connect ceiling: one scalar timeout, exactly as before this layer.
    assert profile.connect_timeout is None
    assert profile.read_timeout == 20.0
    assert profile.default_max_tokens is None
    assert profile.max_tokens_limit is None
    assert profile.sends_stream_field is False
    assert profile.sends_user_field is False


def test_friday_profile_matches_the_internal_platform_contract() -> None:
    profile = providers.FRIDAY
    # The address is configuration-only: nothing about the internal platform's
    # host or path may be baked into a public repository.
    assert profile.default_base_url is None
    assert profile.completions_path is None
    assert profile.base_url_env_name == "ALPHAPILOT_LLM_FRIDAY_BASE_URL"
    assert profile.completions_path_env_name == "ALPHAPILOT_LLM_FRIDAY_COMPLETIONS_PATH"
    assert profile.auth_scheme == "Bearer"
    # The two gateway facts that change downstream behaviour.
    assert profile.supports_temperature is False
    assert profile.supports_response_format is False
    assert profile.thinking_off == providers.THINKING_OFF_FRIDAY_NATIVE
    assert profile.json_extraction == providers.JSON_EXTRACTION_BRACE_BALANCED
    # A proxy in the environment must not be picked up for this platform.
    assert profile.trust_env is False
    assert profile.follow_redirects is False
    assert profile.extra_headers == ("M-TraceId",)
    assert profile.connect_timeout == 10.0
    assert profile.read_timeout == 120.0
    assert profile.max_tokens_limit == 8192
    assert profile.credential_env_name == "ALPHAPILOT_LLM_FRIDAY_APP_ID"
    assert profile.sends_stream_field is True
    assert profile.sends_user_field is True


def test_profile_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        providers.FRIDAY.supports_temperature = True  # type: ignore[misc]


def test_profile_rejects_an_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="thinking_off"):
        providers.ProviderProfile(
            **{**providers.FRIDAY.__dict__, "thinking_off": "handwave"}
        )
    with pytest.raises(ValueError, match="json_extraction"):
        providers.ProviderProfile(
            **{**providers.FRIDAY.__dict__, "json_extraction": "handwave"}
        )


def test_endpoint_assembly_tolerates_trailing_slashes() -> None:
    assert (
        providers.build_endpoint(providers.DASHSCOPE, "https://x.test/v1/")
        == "https://x.test/v1/chat/completions"
    )
    assert (
        providers.build_endpoint(providers.FRIDAY, FRIDAY_BASE_URL + "/", FRIDAY_PATH)
        == FRIDAY_ENDPOINT
    )
    # A leading slash is optional in configuration but always present on the wire.
    assert (
        providers.build_endpoint(providers.FRIDAY, FRIDAY_BASE_URL, "v1/chat")
        == FRIDAY_ENDPOINT
    )
    with pytest.raises(providers.ProviderConfigurationError, match="no base URL"):
        providers.build_endpoint(providers.DASHSCOPE, None)


def test_friday_endpoint_fails_closed_without_configured_address() -> None:
    """No built-in fallback: an unset address is an error, not a default."""
    with pytest.raises(
        providers.ProviderConfigurationError,
        match="ALPHAPILOT_LLM_FRIDAY_BASE_URL",
    ):
        providers.build_endpoint(providers.FRIDAY, None, FRIDAY_PATH)
    with pytest.raises(
        providers.ProviderConfigurationError,
        match="ALPHAPILOT_LLM_FRIDAY_BASE_URL",
    ):
        providers.build_endpoint(providers.FRIDAY, "   ", FRIDAY_PATH)
    with pytest.raises(
        providers.ProviderConfigurationError,
        match="ALPHAPILOT_LLM_FRIDAY_COMPLETIONS_PATH",
    ):
        providers.build_endpoint(providers.FRIDAY, FRIDAY_BASE_URL, None)
    with pytest.raises(
        providers.ProviderConfigurationError,
        match="ALPHAPILOT_LLM_FRIDAY_COMPLETIONS_PATH",
    ):
        providers.build_endpoint(providers.FRIDAY, FRIDAY_BASE_URL, "  ")


# --------------------------------------------------------------------------
# Payload assembly
# --------------------------------------------------------------------------


def test_dashscope_payload_is_byte_for_byte_the_shipped_body() -> None:
    """The exact dict the client built before the provider layer existed."""
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "U"},
    ]
    payload = providers.build_payload(
        providers.DASHSCOPE,
        model="qwen3.6-flash",
        messages=messages,
        temperature=0.2,
        request_id="ignored-by-dashscope",
    )
    expected = {
        "model": "qwen3.6-flash",
        "messages": [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "U"},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "enable_thinking": False,
    }
    assert payload == expected
    # Key order too: an audited body must serialise identically across runs.
    assert list(payload) == list(expected)
    assert "max_tokens" not in payload
    assert "user" not in payload
    assert "stream" not in payload

    with_tokens = providers.build_payload(
        providers.DASHSCOPE,
        model="qwen3.6-flash",
        messages=messages,
        max_tokens=512,
        temperature=0.2,
    )
    assert with_tokens == {**expected, "max_tokens": 512}
    assert list(with_tokens) == [*expected, "max_tokens"]


def test_friday_payload_carries_no_temperature_and_no_response_format() -> None:
    payload = providers.build_payload(
        providers.FRIDAY,
        model="kimi-k3",
        messages=[
            {"role": "system", "content": "S"},
            {"role": "user", "content": "U"},
        ],
        max_tokens=512,
        temperature=0.2,
        request_id="trace-1",
    )
    # Sending temperature returns HTTP 400 from the gateway.
    assert "temperature" not in payload
    # Friday has no JSON mode.
    assert "response_format" not in payload
    assert payload == {
        "model": "kimi-k3",
        "messages": [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "U"},
        ],
        "stream": False,
        "thinking": {"type": "disabled"},
        "user": "trace-1",
        "max_tokens": 512,
    }
    # Only the documented field set, nothing else.
    assert set(payload) <= {
        "model",
        "stream",
        "max_tokens",
        "user",
        "thinking",
        "messages",
    }


def test_friday_thinking_switch_is_disabled_and_never_doubled() -> None:
    payload = providers.build_payload(
        providers.FRIDAY,
        model="kimi-k3",
        messages=[{"role": "user", "content": "U"}],
        request_id="trace-1",
    )
    assert payload["thinking"] == {"type": "disabled"}
    # The two strategies are mutually exclusive; sending both is rejected upstream.
    assert "chat_template_kwargs" not in payload

    qwen3 = providers.ProviderProfile(
        **{
            **providers.FRIDAY.__dict__,
            "name": "internal-qwen3",
            "thinking_off": providers.THINKING_OFF_QWEN3_CHAT_TEMPLATE,
        }
    )
    other = providers.build_payload(
        qwen3,
        model="model-d",
        messages=[{"role": "user", "content": "U"}],
        request_id="trace-1",
    )
    assert other["chat_template_kwargs"] == {"enable_thinking": False}
    assert "thinking" not in other

    omit = providers.ProviderProfile(
        **{
            **providers.FRIDAY.__dict__,
            "name": "friday-omit",
            "thinking_off": providers.THINKING_OFF_OMIT,
        }
    )
    omitted = providers.build_payload(
        omit,
        model="kimi-k3",
        messages=[{"role": "user", "content": "U"}],
        request_id="trace-1",
    )
    assert "thinking" not in omitted
    assert "chat_template_kwargs" not in omitted


def test_friday_max_tokens_is_clamped_and_defaulted() -> None:
    assert providers.clamp_max_tokens(providers.FRIDAY, 99_999) == 8192
    assert providers.clamp_max_tokens(providers.FRIDAY, 512) == 512
    assert providers.clamp_max_tokens(providers.FRIDAY, None) == 2048
    # DashScope keeps "send it only when the caller asked for it".
    assert providers.clamp_max_tokens(providers.DASHSCOPE, None) is None
    assert providers.clamp_max_tokens(providers.DASHSCOPE, 99_999) == 99_999

    payload = providers.build_payload(
        providers.FRIDAY,
        model="kimi-k3",
        messages=[{"role": "user", "content": "U"}],
        max_tokens=99_999,
        request_id="trace-1",
    )
    assert payload["max_tokens"] == 8192


def test_payload_copies_messages_rather_than_aliasing_them() -> None:
    messages = [{"role": "user", "content": "U"}]
    payload = providers.build_payload(
        providers.FRIDAY,
        model="kimi-k3",
        messages=messages,
        request_id="trace-1",
    )
    payload["messages"][0]["content"] = "mutated"
    assert messages[0]["content"] == "U"


# --------------------------------------------------------------------------
# Header assembly
# --------------------------------------------------------------------------


def test_headers_carry_m_traceid_only_where_the_platform_requires_it() -> None:
    friday = providers.build_headers(
        providers.FRIDAY, api_key="app-id", request_id="trace-1"
    )
    assert friday == {
        "Authorization": "Bearer app-id",
        "Content-Type": "application/json",
        "M-TraceId": "trace-1",
    }
    dashscope = providers.build_headers(
        providers.DASHSCOPE, api_key="key", request_id="trace-1"
    )
    assert dashscope == {
        "Authorization": "Bearer key",
        "Content-Type": "application/json",
    }
    assert "M-TraceId" not in dashscope


def test_friday_headers_require_a_request_id() -> None:
    with pytest.raises(ValueError, match="M-TraceId"):
        providers.build_headers(providers.FRIDAY, api_key="app-id", request_id=None)


# --------------------------------------------------------------------------
# Brace-balanced extraction
# --------------------------------------------------------------------------


def test_brace_balanced_extractor_beats_the_greedy_regex_counter_example() -> None:
    """The 2026-08-16 incident in one assertion.

    A model emits a correct object and then keeps talking, trailing another brace.
    A greedy ``\\{.*\\}`` runs to the last brace and declares the output garbage;
    the balanced scan returns the correct object.
    """
    content = '{"result":"ok"} and then some stray fragment "}" tail}'

    greedy = re.search(r"\{.*\}", content, re.DOTALL)
    assert greedy is not None
    assert greedy.group(0) != '{"result":"ok"}'
    with pytest.raises(ValueError):
        providers.extract_json_object(
            providers.ProviderProfile(
                **{**providers.FRIDAY.__dict__, "name": "greedy-simulation"}
            ),
            greedy.group(0),
            loads=lambda text: (_ for _ in ()).throw(ValueError("greedy mis-parse")),
        )

    assert providers.find_balanced_json_object(content) == '{"result":"ok"}'
    assert providers.extract_json_object(providers.FRIDAY, content) == {"result": "ok"}


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"a":1}', {"a": 1}),
        ('```json\n{"a":1}\n```', {"a": 1}),
        ('Here you go: {"a":1}. Hope that helps!', {"a": 1}),
        ('{"a":{"b":[1,2]},"c":"}"}', {"a": {"b": [1, 2]}, "c": "}"}),
        # Braces inside string literals must not close the object.
        ('{"a":"{ not a brace"}trailing', {"a": "{ not a brace"}),
        # Escaped quotes must not flip the in-string state.
        (r'{"a":"say \"hi\" }"}', {"a": 'say "hi" }'}),
        ('{"a":1}{"b":2}', {"a": 1}),
    ],
)
def test_brace_balanced_extraction_cases(content: str, expected: Any) -> None:
    assert providers.extract_json_object(providers.FRIDAY, content) == expected


def test_brace_balanced_extraction_reports_a_missing_object() -> None:
    with pytest.raises(providers.MissingJSONObjectError):
        providers.extract_json_object(providers.FRIDAY, "no object at all")
    with pytest.raises(providers.MissingJSONObjectError):
        providers.extract_json_object(providers.FRIDAY, '{"unclosed": 1')
    assert providers.find_balanced_json_object("nothing") is None


def test_strict_extraction_is_unchanged_for_dashscope() -> None:
    assert providers.json_source(providers.DASHSCOPE, ' {"a":1} ') == ' {"a":1} '
    # JSON mode returns bare JSON, so a wrapper is a real failure there.
    with pytest.raises(ValueError):
        providers.extract_json_object(providers.DASHSCOPE, 'text {"a":1}')


# --------------------------------------------------------------------------
# Response inspection
# --------------------------------------------------------------------------


def test_reasoning_tokens_must_be_zero_or_absent_when_thinking_is_disabled() -> None:
    absent = {"choices": [{"message": {"content": "{}"}}]}
    zero = {
        "choices": [{"message": {"content": "{}"}}],
        "usage": {"completion_tokens_details": {"reasoning_tokens": 0}},
    }
    providers.assert_thinking_disabled(providers.FRIDAY, absent)
    providers.assert_thinking_disabled(providers.FRIDAY, zero)
    assert providers.reasoning_tokens_of(absent) is None
    assert providers.reasoning_tokens_of(zero) == 0

    leaked = {
        "choices": [{"message": {"content": "{}"}}],
        "usage": {"completion_tokens_details": {"reasoning_tokens": 188}},
    }
    assert providers.reasoning_tokens_of(leaked) == 188
    with pytest.raises(providers.ThinkingNotDisabledError, match="188"):
        providers.assert_thinking_disabled(providers.FRIDAY, leaked)


def test_reasoning_tokens_are_read_from_either_usage_container() -> None:
    """A live internal-platform response used output_tokens_details, not the OpenAI key."""
    observed_shape = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 15,
            "total_tokens": 115,
            "output_tokens_details": None,
            "cached_tokens": 0,
        }
    }
    assert providers.reasoning_tokens_of(observed_shape) is None
    providers.assert_thinking_disabled(providers.FRIDAY, observed_shape)

    leaked = {"usage": {"output_tokens_details": {"reasoning_tokens": 42}}}
    assert providers.reasoning_tokens_of(leaked) == 42
    with pytest.raises(providers.ThinkingNotDisabledError, match="42"):
        providers.assert_thinking_disabled(providers.FRIDAY, leaked)

    # A profile that never sends a thinking switch cannot make this assertion.
    omit = providers.ProviderProfile(
        **{
            **providers.FRIDAY.__dict__,
            "name": "friday-omit-check",
            "thinking_off": providers.THINKING_OFF_OMIT,
        }
    )
    providers.assert_thinking_disabled(omit, leaked)


def test_finish_reason_classification() -> None:
    assert providers.finish_reason_of({"choices": [{"finish_reason": "stop"}]}) == "stop"
    assert providers.finish_reason_of({"choices": []}) == ""
    assert providers.finish_reason_of("not a dict") == ""
    assert providers.is_blocked_finish_reason("security:content_filter") is True
    assert providers.is_blocked_finish_reason("SECURITY:Input") is True
    assert providers.is_blocked_finish_reason("stop") is False
    assert providers.is_truncated_finish_reason("length") is True
    assert providers.is_truncated_finish_reason("stop") is False


def test_retryability_classification_matches_the_platform_rules() -> None:
    for status in (429, 500, 502, 503, 504):
        assert providers.is_retryable_status(status) is True
    for status in (400, 401, 403, 404, 422):
        assert providers.is_retryable_status(status) is False
    assert providers.is_retryable_status(200) is False


# --------------------------------------------------------------------------
# chat_json wiring
# --------------------------------------------------------------------------


def test_dashscope_request_is_unchanged_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "test_provider_dashscope_unchanged"
    observed = _capture(
        monkeypatch,
        lambda _index: _response(
            {
                "choices": [{"message": {"content": '{"result":"ok"}'}}],
                "usage": {"prompt_tokens": 13, "completion_tokens": 5},
            }
        ),
    )

    assert chat_json(
        purpose, "system", "user", RESULT_SCHEMA, settings=_dashscope_settings()
    ) == {"result": "ok"}

    (request,) = observed
    assert request["url"] == "https://llm.example.test/compatible-mode/v1/chat/completions"
    assert request["timeout"] == 20.0
    assert request["headers"] == {
        "Authorization": "Bearer test-only-key",
        "Content-Type": "application/json",
    }
    assert request["json"]["response_format"] == {"type": "json_object"}
    assert request["json"]["temperature"] == 0.2
    assert request["json"]["enable_thinking"] is False
    assert "thinking" not in request["json"]
    assert "user" not in request["json"]
    assert "stream" not in request["json"]
    # No transport keyword arguments were added on the unchanged path.
    assert set(request) == {"url", "json", "headers", "timeout"}


def test_friday_request_shape_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    purpose = "test_provider_friday_shape"
    observed = _capture(
        monkeypatch,
        lambda _index: _response(
            {
                "choices": [
                    {
                        "message": {"content": 'Sure!\n{"result":"ok"}\nDone.'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 21,
                    "completion_tokens": 9,
                    "completion_tokens_details": {"reasoning_tokens": 0},
                },
            },
            headers={"m-traceid": "upstream-trace-1"},
        ),
    )

    assert chat_json(
        purpose,
        "system",
        "user",
        RESULT_SCHEMA,
        max_tokens=512,
        settings=_friday_settings(),
    ) == {"result": "ok"}

    (request,) = observed
    assert request["url"] == FRIDAY_ENDPOINT
    body = request["json"]
    assert "temperature" not in body
    assert "response_format" not in body
    assert body["thinking"] == {"type": "disabled"}
    assert "chat_template_kwargs" not in body
    assert body["stream"] is False
    assert body["model"] == "kimi-k3"
    assert body["max_tokens"] == 512
    assert body["user"] == request["headers"]["M-TraceId"]
    assert set(body) == {"model", "messages", "stream", "thinking", "user", "max_tokens"}

    headers = request["headers"]
    assert headers["Authorization"] == "Bearer test-only-app-id"
    assert headers["Content-Type"] == "application/json"
    # A fresh uuid4 per logical call, echoed into the body's user field.
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        headers["M-TraceId"],
    )

    rows = _audit_rows(purpose)
    assert rows == [{"model": "kimi-k3", "ok": True, "error": None}]


def test_friday_client_does_not_trust_the_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A developer host may sit behind a system proxy; this path must go direct."""
    purpose = "test_provider_friday_trust_env"
    observed = _capture(
        monkeypatch,
        lambda _index: _response(
            {"choices": [{"message": {"content": '{"result":"ok"}'}}]}
        ),
    )

    chat_json(purpose, "system", "user", RESULT_SCHEMA, settings=_friday_settings())

    (request,) = observed
    assert request["trust_env"] is False
    assert request["follow_redirects"] is False


def test_friday_splits_connect_and_read_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "test_provider_friday_timeout"
    observed = _capture(
        monkeypatch,
        lambda _index: _response(
            {"choices": [{"message": {"content": '{"result":"ok"}'}}]}
        ),
    )

    chat_json(
        purpose,
        "system",
        "user",
        RESULT_SCHEMA,
        timeout=45.0,
        settings=_friday_settings(),
    )

    timeout = observed[0]["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 10.0
    # The caller's governed budget is the read ceiling; switching platforms must
    # never widen it.
    assert timeout.read == 45.0

    observed.clear()
    chat_json(
        purpose,
        "system",
        "user",
        RESULT_SCHEMA,
        timeout=5.0,
        settings=_friday_settings(),
    )
    # A budget tighter than the connect ceiling stays a single scalar.
    assert observed[0]["timeout"] == 5.0


def test_purpose_model_override_applies_to_the_active_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "stock_insight"
    observed = _capture(
        monkeypatch,
        lambda _index: _response(
            {"choices": [{"message": {"content": '{"result":"ok"}'}}]}
        ),
    )

    chat_json(
        purpose,
        "system",
        "user",
        RESULT_SCHEMA,
        settings=_friday_settings(llm_purpose_models={"stock_insight": "model-b"}),
    )
    assert observed[0]["json"]["model"] == "model-b"

    observed.clear()
    chat_json(
        purpose,
        "system",
        "user",
        RESULT_SCHEMA,
        settings=_friday_settings(llm_friday_model="model-c"),
    )
    assert observed[0]["json"]["model"] == "model-c"


def test_friday_without_an_app_id_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "test_provider_friday_unconfigured"
    called = False

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal called
        called = True
        raise AssertionError("no request may be made without a credential")

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(LLMUnavailable) as excinfo:
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            settings=_friday_settings(llm_friday_app_id=None),
        )

    assert called is False
    # It names the one variable to set, and does not fall back to DashScope.
    assert "ALPHAPILOT_LLM_FRIDAY_APP_ID" in str(excinfo.value)
    assert _audit_rows(purpose) == [
        {"model": "kimi-k3", "ok": False, "error": "not_configured"}
    ]


@pytest.mark.parametrize(
    ("override", "expected_variable"),
    [
        ({"llm_friday_base_url": None}, "ALPHAPILOT_LLM_FRIDAY_BASE_URL"),
        ({"llm_friday_base_url": "   "}, "ALPHAPILOT_LLM_FRIDAY_BASE_URL"),
        ({"llm_friday_completions_path": None}, "ALPHAPILOT_LLM_FRIDAY_COMPLETIONS_PATH"),
        ({"llm_friday_completions_path": ""}, "ALPHAPILOT_LLM_FRIDAY_COMPLETIONS_PATH"),
    ],
)
def test_friday_without_a_configured_address_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    override: dict[str, Any],
    expected_variable: str,
) -> None:
    """The address is configuration-only; an unset half is an error, not a default."""
    purpose = "test_provider_friday_no_address"

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        raise AssertionError("no request may be made without a configured address")

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(LLMUnavailable) as excinfo:
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            settings=_friday_settings(**override),
        )

    assert expected_variable in str(excinfo.value)
    assert _audit_rows(purpose)[-1] == {
        "model": "kimi-k3",
        "ok": False,
        "error": "not_configured",
    }


def test_unknown_provider_fails_closed_with_an_audit_row() -> None:
    purpose = "test_provider_unknown"
    with pytest.raises(LLMUnavailable, match="unknown LLM provider"):
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            settings=_dashscope_settings(llm_provider="openai"),
        )
    assert _audit_rows(purpose) == [
        {"model": "unconfigured", "ok": False, "error": "provider_unknown"}
    ]


def test_reasoning_tokens_leak_fails_the_call_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "test_provider_thinking_leak"
    observed = _capture(
        monkeypatch,
        lambda _index: _response(
            {
                "choices": [{"message": {"content": '{"result":"ok"}'}}],
                "usage": {"completion_tokens_details": {"reasoning_tokens": 188}},
            }
        ),
    )

    with pytest.raises(LLMUnavailable) as excinfo:
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=3,
            settings=_friday_settings(),
        )

    assert excinfo.value.reason == "thinking_not_disabled"
    # A switch the platform ignored is not fixed by asking again.
    assert len(observed) == 1
    assert _audit_rows(purpose) == [
        {"model": "kimi-k3", "ok": False, "error": "thinking_not_disabled"}
    ]


def test_security_finish_reason_fails_the_call_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "test_provider_output_blocked"
    observed = _capture(
        monkeypatch,
        lambda _index: _response(
            {
                "choices": [
                    {
                        "message": {"content": '{"result":"ok"}'},
                        "finish_reason": "security:output_filter",
                    }
                ]
            }
        ),
    )

    with pytest.raises(LLMUnavailable) as excinfo:
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=3,
            settings=_friday_settings(),
        )

    assert excinfo.value.reason == "output_blocked"
    assert len(observed) == 1
    assert _audit_rows(purpose) == [
        {"model": "kimi-k3", "ok": False, "error": "output_blocked"}
    ]


def test_truncated_output_is_reported_but_still_parsed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    purpose = "test_provider_truncated"
    _capture(
        monkeypatch,
        lambda _index: _response(
            {
                "choices": [
                    {
                        "message": {"content": '{"result":"ok"}'},
                        "finish_reason": "length",
                    }
                ]
            }
        ),
    )

    with caplog.at_level("WARNING", logger="alphapilot.llm.client"):
        assert chat_json(
            purpose, "system", "user", RESULT_SCHEMA, settings=_friday_settings()
        ) == {"result": "ok"}
    assert any("truncated" in record.message for record in caplog.records)


def test_friday_transport_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The held-out contract requires zero automatic transport retries."""
    purpose = "test_provider_friday_transport"
    attempts = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("connect timed out")

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(LLMUnavailable) as excinfo:
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=3,
            settings=_friday_settings(),
        )

    assert attempts == 1
    assert excinfo.value.reason == "request_timeout"


def test_friday_malformed_output_respects_max_retries_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "test_provider_friday_no_json"
    attempts = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return _response({"choices": [{"message": {"content": "I cannot comply."}}]})

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(LLMUnavailable) as excinfo:
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=0,
            settings=_friday_settings(),
        )

    assert attempts == 1
    assert excinfo.value.reason == "invalid_json"
    assert _audit_rows(purpose) == [
        {"model": "kimi-k3", "ok": False, "error": "invalid_json"}
    ]


def test_friday_keeps_the_hardened_json_decoding_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Brace-balanced extraction must not weaken NaN / duplicate-key rejection."""
    purpose = "test_provider_friday_hardened_json"
    responses = [
        '{"result": NaN}',
        '{"result":"a","result":"b"}',
    ]
    index = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal index
        content = responses[index]
        index += 1
        return _response({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(httpx, "post", fake_post)

    for _ in responses:
        with pytest.raises(LLMUnavailable) as excinfo:
            chat_json(
                purpose,
                "system",
                "user",
                RESULT_SCHEMA,
                max_retries=0,
                settings=_friday_settings(),
            )
        assert excinfo.value.reason == "invalid_json"


def test_friday_schema_failure_reports_field_and_constraint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The _SchemaValidationFailure metadata contract is provider-independent."""
    purpose = "test_provider_friday_schema"
    _capture(
        monkeypatch,
        lambda _index: _response(
            {"choices": [{"message": {"content": 'Here: {"result": 7}'}}]}
        ),
    )

    with pytest.raises(LLMUnavailable) as excinfo:
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=0,
            settings=_friday_settings(),
        )

    assert excinfo.value.reason == "schema_validation_failed"
    assert excinfo.value.field == "result"
    assert excinfo.value.constraint == "json_schema_type"


# --------------------------------------------------------------------------
# Endpoint binding digest
# --------------------------------------------------------------------------

# Synthetic throughout: neither the real address nor the real salt may appear in
# a fixture, so these prove the algorithm, not the deployment.
SYNTHETIC_SALT = "00112233445566778899aabbccddeeff" * 2


def _binding_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"llm_friday_endpoint_hmac_salt": SYNTHETIC_SALT}
    values.update(overrides)
    return _friday_settings(**values)


def test_endpoint_binding_digest_is_the_documented_hmac() -> None:
    """HMAC-SHA256(bytes.fromhex(salt), utf8(base.rstrip('/') + path))."""
    expected = hmac.new(
        bytes.fromhex(SYNTHETIC_SALT),
        (FRIDAY_BASE_URL.rstrip("/") + FRIDAY_PATH).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert providers.endpoint_binding_digest(_binding_settings()) == expected
    assert len(expected) == 64
    # A trailing slash on the base URL must not change the binding.
    assert (
        providers.endpoint_binding_digest(
            _binding_settings(llm_friday_base_url=FRIDAY_BASE_URL + "/")
        )
        == expected
    )


def test_endpoint_binding_digest_covers_the_url_actually_requested() -> None:
    """With a rooted path the bound URL is exactly the endpoint that is called."""
    settings = _binding_settings()
    endpoint = providers.build_endpoint(
        providers.FRIDAY,
        settings.llm_friday_base_url,
        settings.llm_friday_completions_path,
    )
    assert providers.endpoint_binding_digest(settings) == hmac.new(
        bytes.fromhex(SYNTHETIC_SALT), endpoint.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def test_endpoint_binding_changes_with_either_half() -> None:
    baseline = providers.endpoint_binding_digest(_binding_settings())
    other_host = providers.endpoint_binding_digest(
        _binding_settings(llm_friday_base_url="https://other.invalid")
    )
    other_path = providers.endpoint_binding_digest(
        _binding_settings(llm_friday_completions_path="/v2/chat")
    )
    other_salt = providers.endpoint_binding_digest(
        _binding_settings(llm_friday_endpoint_hmac_salt="ff" * 32)
    )
    assert len({baseline, other_host, other_path, other_salt}) == 4


@pytest.mark.parametrize(
    ("override", "expected_variable"),
    [
        ({"llm_friday_base_url": None}, "ALPHAPILOT_LLM_FRIDAY_BASE_URL"),
        ({"llm_friday_completions_path": None}, "ALPHAPILOT_LLM_FRIDAY_COMPLETIONS_PATH"),
        (
            {"llm_friday_endpoint_hmac_salt": None},
            "ALPHAPILOT_LLM_FRIDAY_ENDPOINT_HMAC_SALT",
        ),
    ],
)
def test_endpoint_binding_fails_closed_and_names_the_variable(
    override: dict[str, Any], expected_variable: str
) -> None:
    with pytest.raises(providers.ProviderConfigurationError, match=expected_variable):
        providers.endpoint_binding_digest(_binding_settings(**override))


def test_endpoint_binding_rejects_a_non_hex_salt_without_echoing_it() -> None:
    bad = "not-hexadecimal-salt"
    with pytest.raises(providers.ProviderConfigurationError) as excinfo:
        providers.endpoint_binding_digest(
            _binding_settings(llm_friday_endpoint_hmac_salt=bad)
        )
    message = str(excinfo.value)
    assert "ALPHAPILOT_LLM_FRIDAY_ENDPOINT_HMAC_SALT" in message
    # The salt is a secret: it must never reach an error message or a log.
    assert bad not in message


def test_endpoint_binding_never_reveals_the_url_or_the_salt() -> None:
    settings = _binding_settings()
    digest = providers.endpoint_binding_digest(settings)
    assert FRIDAY_BASE_URL not in digest
    assert FRIDAY_PATH not in digest
    assert SYNTHETIC_SALT not in digest
    assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_endpoint_binding_matches_uses_constant_time_comparison() -> None:
    settings = _binding_settings()
    digest = providers.endpoint_binding_digest(settings)
    assert providers.endpoint_binding_matches(settings, digest) is True
    assert providers.endpoint_binding_matches(settings, "  " + digest + " ") is True
    assert providers.endpoint_binding_matches(settings, "0" * 64) is False
    assert providers.endpoint_binding_matches(settings, "") is False
    assert providers.endpoint_binding_matches(settings, None) is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# ProviderConfigurationError message hygiene, and per-contract provider choice
# --------------------------------------------------------------------------

# Stand-ins for the five values that must never escape into a rendered error.
SECRET_BASE_URL = "https://secret-host.invalid"
SECRET_PATH = "/v9/secret/native/completions"
SECRET_SALT = "deadbeef" * 8
SECRET_APP_ID = "secret-app-id-value"


def _secret_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "llm_provider": "friday",
        "llm_friday_base_url": SECRET_BASE_URL,
        "llm_friday_completions_path": SECRET_PATH,
        "llm_friday_app_id": SECRET_APP_ID,
        "llm_friday_endpoint_hmac_salt": SECRET_SALT,
    }
    values.update(overrides)
    return Settings(**values)


def _every_configuration_failure() -> list[providers.ProviderConfigurationError]:
    """Construct the error from every path that can raise it."""
    raised: list[providers.ProviderConfigurationError] = []
    cases: list[Any] = [
        lambda: providers.build_endpoint(providers.FRIDAY, None, SECRET_PATH),
        lambda: providers.build_endpoint(providers.FRIDAY, SECRET_BASE_URL, None),
        lambda: providers.build_endpoint(providers.FRIDAY, "  ", "  "),
        lambda: providers.build_endpoint(providers.DASHSCOPE, None),
        lambda: providers.endpoint_binding_digest(
            _secret_settings(llm_friday_base_url=None)
        ),
        lambda: providers.endpoint_binding_digest(
            _secret_settings(llm_friday_completions_path=None)
        ),
        lambda: providers.endpoint_binding_digest(
            _secret_settings(llm_friday_endpoint_hmac_salt=None)
        ),
        lambda: providers.endpoint_binding_digest(
            _secret_settings(llm_friday_endpoint_hmac_salt="zz-not-hex")
        ),
        lambda: providers.endpoint_binding_digest(
            _secret_settings(llm_friday_endpoint_hmac_salt="")
        ),
    ]
    for case in cases:
        try:
            case()
        except providers.ProviderConfigurationError as error:
            raised.append(error)
        else:  # pragma: no cover - a case that stopped failing
            raise AssertionError("expected ProviderConfigurationError")
    return raised


def test_provider_configuration_error_never_renders_a_secret() -> None:
    """The other lane chains this into an admission failure that gets logged.

    Message hygiene must survive __cause__, so nothing sensitive may live in the
    message, the args, or any attribute a traceback would render.
    """
    digest = providers.endpoint_binding_digest(_secret_settings())
    forbidden = (
        SECRET_BASE_URL,
        SECRET_PATH,
        SECRET_SALT,
        SECRET_APP_ID,
        digest,
        # Also the bare host and the salt in any case-folded form.
        "secret-host.invalid",
        SECRET_SALT.upper(),
    )
    errors = _every_configuration_failure()
    assert len(errors) == 9
    for error in errors:
        rendered = [str(error), repr(error), repr(error.args)]
        rendered.extend(repr(getattr(error, name)) for name in dir(error)
                        if not name.startswith("__") and not callable(getattr(error, name)))
        blob = " ".join(rendered)
        for secret in forbidden:
            assert secret not in blob, f"{secret!r} leaked into {blob!r}"
        # It must still be actionable: it names the variable to set.
        assert "ALPHAPILOT_LLM" in blob or "no base URL" in blob


def test_provider_configuration_error_is_the_address_error() -> None:
    assert providers.MissingProviderAddressError is providers.ProviderConfigurationError
    assert issubclass(providers.ProviderConfigurationError, ValueError)


def test_active_provider_prefers_the_contract_over_the_global_default() -> None:
    """A global switch must not drag the production poller onto another platform."""
    dashscope_settings = _dashscope_settings()
    friday_settings = _friday_settings()

    # No contract: the global default decides.
    assert providers.active_provider(dashscope_settings) == "dashscope"
    assert providers.active_provider(friday_settings) == "friday"

    # A contract that pins a provider always wins, in both directions.
    friday_contract = {"llm": {"provider": "friday"}}
    vendor_contract = {"llm": {"provider": "dashscope"}}
    assert providers.active_provider(dashscope_settings, contract=friday_contract) == "friday"
    assert providers.active_provider(friday_settings, contract=vendor_contract) == "dashscope"

    # A contract that pins nothing falls through to the global default.
    silent_contract = {"llm": {"model": "qwen3.6-plus"}}
    assert providers.active_provider(dashscope_settings, contract=silent_contract) == "dashscope"
    assert providers.active_provider(friday_settings, contract=silent_contract) == "friday"
    assert providers.active_provider(dashscope_settings, contract=None) == "dashscope"


def test_active_provider_reads_a_loaded_contract_object() -> None:
    class LoadedContract:
        document: ClassVar[dict[str, Any]] = {
            "llm": {"provider": "friday", "model": "kimi-k3"}
        }

    assert providers.active_provider(_dashscope_settings(), contract=LoadedContract()) == "friday"
    assert providers.contract_provider(LoadedContract()) == "friday"
    assert providers.contract_provider({"llm": {}}) is None
    assert providers.contract_provider(None) is None
    # Case and padding are tolerated, exactly as for the environment variable.
    assert providers.contract_provider({"llm": {"provider": "  FRIDAY "}}) == "friday"


def test_active_provider_rejects_a_contract_naming_an_unknown_platform() -> None:
    with pytest.raises(providers.UnknownProviderError):
        providers.active_provider(_dashscope_settings(), contract={"llm": {"provider": "openai"}})


# --------------------------------------------------------------------------
# Contract endpoint binding must never fail open
# --------------------------------------------------------------------------


def _llm_block(**overrides: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "purpose": "p4_news_event_extract",
        "model": "qwen3.6-plus",
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "temperature": 0.2,
        "enable_thinking": False,
        "response_format": "json_object",
    }
    block.update(overrides)
    return {key: value for key, value in block.items() if value is not _ABSENT}


_ABSENT = object()
VENDOR_DIGEST = "b4" + "0" * 62


def test_contract_with_no_endpoint_binding_is_refused() -> None:
    """The fail-open this closes: no binding meant no check, silently."""
    from alphapilot.llm.p4_news_event import (
        EventExtractContractError,
        _validate_endpoint_binding,
    )

    with pytest.raises(EventExtractContractError, match=r"must pin llm\.endpoint"):
        _validate_endpoint_binding(_llm_block(endpoint=_ABSENT), None)


def test_contract_binding_variants() -> None:
    from alphapilot.llm.p4_news_event import (
        EventExtractContractError,
        _validate_endpoint_binding,
    )

    vendor = _llm_block()
    assert _validate_endpoint_binding(vendor, vendor["endpoint"]) == (None, None)

    platform = _llm_block(
        endpoint=_ABSENT, provider="friday", endpoint_hmac_sha256=VENDOR_DIGEST
    )
    assert _validate_endpoint_binding(platform, None) == ("friday", VENDOR_DIGEST)

    # Both halves at once is ambiguous and refused.
    both = _llm_block(provider="friday", endpoint_hmac_sha256=VENDOR_DIGEST)
    with pytest.raises(EventExtractContractError, match="exactly one endpoint binding"):
        _validate_endpoint_binding(both, both["endpoint"])

    # A half-declared platform binding is refused rather than half-checked.
    with pytest.raises(EventExtractContractError, match="provider"):
        _validate_endpoint_binding(
            _llm_block(endpoint=_ABSENT, endpoint_hmac_sha256=VENDOR_DIGEST), None
        )
    with pytest.raises(EventExtractContractError, match="endpoint_hmac_sha256"):
        _validate_endpoint_binding(_llm_block(endpoint=_ABSENT, provider="friday"), None)
    for bad in ("", "not-hex", VENDOR_DIGEST.upper(), VENDOR_DIGEST[:-1]):
        with pytest.raises(EventExtractContractError, match="endpoint_hmac_sha256"):
            _validate_endpoint_binding(
                _llm_block(
                    endpoint=_ABSENT, provider="friday", endpoint_hmac_sha256=bad
                ),
                None,
            )


def test_runtime_endpoint_assertion_fires_on_the_platform_arm() -> None:
    """The platform arm must assert, not skip, when the contract drops endpoint."""
    import dataclasses

    from alphapilot.llm.p4_news_event import (
        EventExtractContractError,
        _assert_settings_match_contract_endpoint,
        load_event_extract_contract,
    )

    base = load_event_extract_contract(
        pathlib.Path("config/p4_event_extract_eval_v1_3.yaml")
    )
    settings = _binding_settings(llm_purpose_models={base.purpose: base.model})
    correct = providers.endpoint_binding_digest(settings)

    pinned = dataclasses.replace(
        base, endpoint=None, provider="friday", endpoint_hmac_sha256=correct
    )
    _assert_settings_match_contract_endpoint(pinned, settings)

    wrong = dataclasses.replace(
        base, endpoint=None, provider="friday", endpoint_hmac_sha256="0" * 64
    )
    with pytest.raises(EventExtractContractError, match="endpoint binding differs"):
        _assert_settings_match_contract_endpoint(wrong, settings)

    # A binding naming a provider whose address cannot be verified is refused,
    # rather than being waved through.
    unsupported = dataclasses.replace(
        base, endpoint=None, provider="dashscope", endpoint_hmac_sha256=correct
    )
    with pytest.raises(EventExtractContractError, match="unsupported provider"):
        _assert_settings_match_contract_endpoint(unsupported, settings)

    # A half-declared binding is an error, not a skip.
    half = dataclasses.replace(
        base, endpoint=None, provider="friday", endpoint_hmac_sha256=None
    )
    with pytest.raises(EventExtractContractError, match="partial endpoint binding"):
        _assert_settings_match_contract_endpoint(half, settings)

    # An unconfigured binding is an error, and its cause leaks nothing.
    unconfigured = _friday_settings(llm_friday_endpoint_hmac_salt=None)
    with pytest.raises(EventExtractContractError) as excinfo:
        _assert_settings_match_contract_endpoint(pinned, unconfigured)
    assert SECRET_SALT not in str(excinfo.value.__cause__ or "")


# --------------------------------------------------------------------------
# Evaluation-design endpoint assertions are provider-aware
# --------------------------------------------------------------------------


def _contract_with(**overrides: Any) -> Any:
    import dataclasses

    from alphapilot.llm.p4_news_event import load_event_extract_contract

    base = load_event_extract_contract(
        pathlib.Path("config/p4_event_extract_eval_v1_3.yaml")
    )
    return dataclasses.replace(base, **overrides)


def test_eval_endpoint_binding_vendor_arm_keeps_its_literal() -> None:
    """Existing evidence must stay verifiable byte for byte."""
    from alphapilot.llm import p4_news_eval

    contract = _contract_with()
    assert contract.endpoint == p4_news_eval.VENDOR_ENDPOINT
    assert p4_news_eval._endpoint_binding_matches({"endpoint": contract.endpoint}, contract)
    # A vendor artefact may name the vendor explicitly, but nothing else.
    assert p4_news_eval._endpoint_binding_matches(
        {"endpoint": contract.endpoint, "provider": "dashscope"}, contract
    )
    assert not p4_news_eval._endpoint_binding_matches({"endpoint": "https://x.invalid"}, contract)
    assert not p4_news_eval._endpoint_binding_matches({}, contract)
    # A vendor contract must not be satisfied by a platform-shaped artefact.
    assert not p4_news_eval._endpoint_binding_matches(
        {"endpoint": contract.endpoint, "endpoint_hmac_sha256": "a" * 64}, contract
    )
    assert p4_news_eval._endpoint_binding_keys(contract) == {"endpoint"}


def test_eval_endpoint_binding_platform_arm_asserts_provider_and_digest() -> None:
    from alphapilot.llm import p4_news_eval

    digest = "b4" + "c" * 62
    contract = _contract_with(
        endpoint=None, provider="friday", endpoint_hmac_sha256=digest
    )
    assert p4_news_eval._endpoint_binding_matches(
        {"provider": "friday", "endpoint_hmac_sha256": digest}, contract
    )
    # Wrong digest, wrong provider, missing half, or a stray endpoint: all refused.
    assert not p4_news_eval._endpoint_binding_matches(
        {"provider": "friday", "endpoint_hmac_sha256": "0" * 64}, contract
    )
    assert not p4_news_eval._endpoint_binding_matches(
        {"provider": "dashscope", "endpoint_hmac_sha256": digest}, contract
    )
    assert not p4_news_eval._endpoint_binding_matches({"provider": "friday"}, contract)
    assert not p4_news_eval._endpoint_binding_matches(
        {"endpoint_hmac_sha256": digest}, contract
    )
    assert not p4_news_eval._endpoint_binding_matches(
        {
            "provider": "friday",
            "endpoint_hmac_sha256": digest,
            "endpoint": p4_news_eval.VENDOR_ENDPOINT,
        },
        contract,
    )
    # A non-string digest cannot reach compare_digest.
    assert not p4_news_eval._endpoint_binding_matches(
        {"provider": "friday", "endpoint_hmac_sha256": None}, contract
    )
    assert p4_news_eval._endpoint_binding_keys(contract) == {
        "provider",
        "endpoint_hmac_sha256",
    }


def test_eval_endpoint_binding_refuses_a_contract_pinning_nothing() -> None:
    """No binding must never read as "matches"; that was the fail-open."""
    from alphapilot.llm import p4_news_eval

    unpinned = _contract_with(endpoint=None, provider=None, endpoint_hmac_sha256=None)
    assert not p4_news_eval._endpoint_binding_matches({}, unpinned)
    assert not p4_news_eval._endpoint_binding_matches({"endpoint": None}, unpinned)
    assert not p4_news_eval._endpoint_binding_matches(
        {"provider": "friday", "endpoint_hmac_sha256": "a" * 64}, unpinned
    )


def test_eval_binding_key_shape_is_read_off_the_artefact() -> None:
    from alphapilot.llm import p4_news_eval

    assert p4_news_eval._observed_binding_keys({"endpoint": "x"}) == {"endpoint"}
    assert p4_news_eval._observed_binding_keys({"provider": "friday"}) == {
        "provider",
        "endpoint_hmac_sha256",
    }
    assert p4_news_eval._observed_binding_keys(
        {"required_endpoint": "x"}, endpoint_key="required_endpoint"
    ) == {"required_endpoint"}


def test_chat_json_provider_argument_overrides_the_global_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A contract-pinned provider must beat ALPHAPILOT_LLM_PROVIDER at call time."""
    purpose = "test_provider_argument"
    observed = _capture(
        monkeypatch,
        lambda _index: _response(
            {"choices": [{"message": {"content": '{"result":"ok"}'}}]}
        ),
    )

    # Global default says dashscope; the call names the platform explicitly.
    settings = _friday_settings(llm_provider="dashscope")
    chat_json(
        purpose, "system", "user", RESULT_SCHEMA, provider="friday", settings=settings
    )
    assert observed[0]["url"] == FRIDAY_ENDPOINT
    assert observed[0]["trust_env"] is False
    assert "temperature" not in observed[0]["json"]

    # And the reverse: global says friday, the call pins the vendor.
    observed.clear()
    chat_json(
        purpose,
        "system",
        "user",
        RESULT_SCHEMA,
        provider="dashscope",
        settings=_friday_settings(llm_provider="friday"),
    )
    assert observed[0]["url"].endswith("/chat/completions")
    assert observed[0]["json"]["temperature"] == 0.2
    assert "trust_env" not in observed[0]

    # Omitting it keeps today's behaviour exactly.
    observed.clear()
    chat_json(purpose, "system", "user", RESULT_SCHEMA, settings=_dashscope_settings())
    assert set(observed[0]) == {"url", "json", "headers", "timeout"}


# --------------------------------------------------------------------------
# Gateway rate limiting: wait, re-send, and account for it
# --------------------------------------------------------------------------


def test_retry_after_parsing() -> None:
    assert providers.retry_after_seconds({"Retry-After": "12"}) == 12.0
    assert providers.retry_after_seconds({"retry-after": " 0.5 "}) == 0.5
    assert providers.retry_after_seconds({"Retry-After": "-3"}) == 0.0
    assert providers.retry_after_seconds({}) is None
    assert providers.retry_after_seconds({"Retry-After": ""}) is None
    assert providers.retry_after_seconds({"Retry-After": "soon"}) is None
    assert providers.retry_after_seconds(None) is None
    # HTTP-date form resolves to a non-negative delay.
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    future = format_datetime(datetime.now(UTC) + timedelta(seconds=30))
    parsed = providers.retry_after_seconds({"Retry-After": future})
    assert parsed is not None and 0.0 <= parsed <= 31.0


def test_rate_limit_backoff_follows_the_registered_schedule() -> None:
    assert providers.RATE_LIMIT_BACKOFF_SCHEDULE == (5.0, 10.0, 20.0, 30.0, 45.0, 60.0)
    assert [providers.rate_limit_backoff_seconds(n) for n in range(8)] == [
        5.0, 10.0, 20.0, 30.0, 45.0, 60.0, 60.0, 60.0
    ]
    with pytest.raises(ValueError):
        providers.rate_limit_backoff_seconds(-1)


def test_supplied_delay_window() -> None:
    """Zero means resend now, not invalid; past the ceiling means the quota is gone."""
    # Nothing usable supplied -> caller falls back to the schedule.
    assert providers.accept_supplied_delay(None) is None
    assert providers.accept_supplied_delay(-1.0) is None
    # Zero is legal HTTP. Rejecting it would make a round unrunnable the first
    # time the platform used it; it is floored to one second instead.
    assert providers.accept_supplied_delay(0.0) == 1.0
    assert providers.accept_supplied_delay(0.4) == 1.0
    assert providers.accept_supplied_delay(30.0) == 30.0
    assert providers.accept_supplied_delay(120.0) == 120.0
    with pytest.raises(providers.RateLimitQuotaExhausted):
        providers.accept_supplied_delay(120.001)


def test_rate_limit_policy_carries_the_registered_constants() -> None:
    policy = llm_client.RateLimitPolicy()
    assert policy.per_candidate_resends == 6
    assert policy.per_pass_resend_cap == 2000
    assert policy.per_pass_wall_clock_cap_seconds == 50_400.0
    assert policy.max_waits == policy.per_candidate_resends


def _rate_limited(retry_after: str | None = None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return httpx.Response(
        429,
        json={"error": "rate limited"},
        headers=headers,
        request=httpx.Request("POST", FRIDAY_ENDPOINT),
    )


def test_rate_limit_is_not_retried_without_a_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default behaviour is unchanged: a 429 fails on the first attempt."""
    purpose = "test_rate_limit_default"
    attempts = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return _rate_limited("3")

    monkeypatch.setattr(httpx, "post", fake_post)
    accounting = llm_client.RequestAccounting()

    with pytest.raises(LLMUnavailable) as excinfo:
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=3,
            accounting=accounting,
            settings=_friday_settings(),
        )

    assert attempts == 1
    assert excinfo.value.reason == "http_status_429"
    assert accounting.as_evidence() == {
        "requests_started": 1,
        "answers_served": 0,
        "rate_limited_responses": 1,
        "rate_limit_waits": 0,
        "rate_limit_wait_seconds": 0.0,
        "rate_limit_wait_items": [],
    }


def test_rate_limit_policy_waits_then_resends_the_same_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "test_rate_limit_retry"
    sent: list[dict[str, Any]] = []
    slept: list[float] = []
    replies = [_rate_limited("2"), _rate_limited("4"), None]

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        sent.append({"url": url, "json": kwargs["json"], "headers": kwargs["headers"]})
        reply = replies[len(sent) - 1]
        if reply is not None:
            return reply
        return _response(
            {
                "choices": [{"message": {"content": '{"result":"ok"}'}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", slept.append)
    accounting = llm_client.RequestAccounting()

    result = chat_json(
        purpose,
        "system",
        "user",
        RESULT_SCHEMA,
        max_retries=0,
        rate_limit=llm_client.RateLimitPolicy(),
        accounting=accounting,
        settings=_friday_settings(),
    )

    assert result == {"result": "ok"}
    # Retry-After is honoured verbatim, not replaced by our own backoff.
    assert slept == [2.0, 4.0]
    # The SAME request is re-sent: identical body and identical trace id.
    assert len({json.dumps(entry["json"], sort_keys=True) for entry in sent}) == 1
    assert len({entry["headers"]["M-TraceId"] for entry in sent}) == 1
    # One question, one answer, two extra starts.
    assert accounting.as_evidence() == {
        "requests_started": 3,
        "answers_served": 1,
        "rate_limited_responses": 2,
        "rate_limit_waits": 2,
        "rate_limit_wait_seconds": 6.0,
        "rate_limit_wait_items": [2.0, 4.0],
    }
    # Exactly one audit row, and it records the successful call.
    rows = _audit_rows(purpose)
    assert rows == [{"model": "kimi-k3", "ok": True, "error": None}]


def test_rate_limit_falls_back_to_backoff_without_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "test_rate_limit_backoff"
    slept: list[float] = []
    calls = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 3:
            return _rate_limited()
        return _response({"choices": [{"message": {"content": '{"result":"ok"}'}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", slept.append)
    accounting = llm_client.RequestAccounting()

    chat_json(
        purpose,
        "system",
        "user",
        RESULT_SCHEMA,
        max_retries=0,
        rate_limit=llm_client.RateLimitPolicy(),
        accounting=accounting,
        settings=_friday_settings(),
    )

    assert slept == [5.0, 10.0, 20.0]  # registered schedule
    assert accounting.started == 4
    assert accounting.served == 1


def test_rate_limit_budget_is_finite(monkeypatch: pytest.MonkeyPatch) -> None:
    """A permanently rate-limited credential must fail, not wait forever."""
    purpose = "test_rate_limit_exhausted"
    slept: list[float] = []
    attempts = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return _rate_limited("1")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", slept.append)
    accounting = llm_client.RequestAccounting()

    with pytest.raises(providers.RateLimitCandidateGuardExceeded) as excinfo:
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=0,
            rate_limit=llm_client.RateLimitPolicy(per_candidate_resends=3),
            accounting=accounting,
            settings=_friday_settings(),
        )

    assert excinfo.value.reason == "rate_limit_candidate_guard_exceeded"
    # The counters travel with the error, for the census.
    assert excinfo.value.evidence["rate_limit_waits"] == 3
    assert excinfo.value.evidence["answers_served"] == 0
    assert attempts == 4  # the original plus three waits
    assert slept == [1.0, 1.0, 1.0]
    assert accounting.served == 0
    assert accounting.rate_limited == 4


def test_rate_limit_policy_never_waits_on_any_other_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """500 and transport errors must still surface on the first attempt."""
    purpose = "test_rate_limit_other_status"
    slept: list[float] = []

    for status in (500, 503, 400, 401):
        attempts = 0

        def fake_post(*_args: Any, _status: int = status, **_kwargs: Any) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                _status, json={}, request=httpx.Request("POST", FRIDAY_ENDPOINT)
            )

        monkeypatch.setattr(httpx, "post", fake_post)
        monkeypatch.setattr(llm_client.time, "sleep", slept.append)
        with pytest.raises(LLMUnavailable):
            chat_json(
                purpose,
                "system",
                "user",
                RESULT_SCHEMA,
                max_retries=0,
                rate_limit=llm_client.RateLimitPolicy(),
                settings=_friday_settings(),
            )
        assert attempts == 1, status
    assert slept == []


def test_rate_limit_policy_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        llm_client.RateLimitPolicy(per_candidate_resends=-1)
    with pytest.raises(ValueError):
        llm_client.RateLimitPolicy(per_pass_resend_cap=-1)
    with pytest.raises(ValueError):
        llm_client.RateLimitPolicy(per_pass_wall_clock_cap_seconds=0)


def test_supplied_delay_past_the_ceiling_stops_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A huge Retry-After means the quota is gone; sleeping it burns the window."""
    purpose = "test_rate_limit_ceiling"
    slept: list[float] = []
    attempts = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return _rate_limited("600")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", slept.append)

    with pytest.raises(providers.RateLimitQuotaExhausted) as excinfo:
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=0,
            rate_limit=llm_client.RateLimitPolicy(),
            settings=_friday_settings(),
        )

    assert excinfo.value.reason == "rate_limit_supplied_delay_above_ceiling"
    assert excinfo.value.evidence["rate_limited_responses"] == 1
    assert attempts == 1
    assert slept == []


def test_zero_retry_after_resends_after_the_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry-After: 0 must not be read as invalid, or a round becomes unrunnable."""
    purpose = "test_rate_limit_zero"
    slept: list[float] = []
    calls = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _rate_limited("0")
        return _response({"choices": [{"message": {"content": '{"result":"ok"}'}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", slept.append)

    assert chat_json(
        purpose,
        "system",
        "user",
        RESULT_SCHEMA,
        max_retries=0,
        rate_limit=llm_client.RateLimitPolicy(),
        settings=_friday_settings(),
    ) == {"result": "ok"}
    assert slept == [1.0]


def test_pass_wall_clock_cap_is_checked_against_the_projected_wake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap must bound when we would wake, not when we go to sleep."""
    purpose = "test_rate_limit_wall_clock"
    slept: list[float] = []

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        return _rate_limited()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(llm_client.time, "sleep", slept.append)

    import time as _time

    # A pass that began just inside the cap: the first 5s wait would land outside.
    accounting = llm_client.RequestAccounting(
        pass_started_at=_time.monotonic() - 50_398.0
    )
    with pytest.raises(providers.RateLimitWallClockCapExceeded):
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=0,
            rate_limit=llm_client.RateLimitPolicy(),
            accounting=accounting,
            settings=_friday_settings(),
        )
    assert slept == []
    assert accounting.waits == []


def test_pass_resend_cap_stops_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    purpose = "test_rate_limit_pass_cap"
    slept: list[float] = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _rate_limited())
    monkeypatch.setattr(llm_client.time, "sleep", slept.append)

    accounting = llm_client.RequestAccounting(waits=[5.0] * 3)
    with pytest.raises(providers.RateLimitPassResendCapExceeded):
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=0,
            rate_limit=llm_client.RateLimitPolicy(per_pass_resend_cap=3),
            accounting=accounting,
            settings=_friday_settings(),
        )
    assert slept == []
    assert len(accounting.waits) == 3


def test_named_give_ups_share_a_base_and_carry_distinct_reasons() -> None:
    """The census has to tell these four apart; a bare 429 could not."""
    reasons = {
        providers.RateLimitCandidateGuardExceeded: "rate_limit_candidate_guard_exceeded",
        providers.RateLimitPassResendCapExceeded: "rate_limit_pass_resend_cap_exceeded",
        providers.RateLimitWallClockCapExceeded: "rate_limit_wall_clock_cap_exceeded",
        providers.RateLimitQuotaExhausted: "rate_limit_supplied_delay_above_ceiling",
    }
    assert len(set(reasons.values())) == 4
    for cls, reason in reasons.items():
        assert issubclass(cls, providers.RateLimitAbort)
        error = cls("x", evidence={"answers_served": 1})
        assert error.reason == reason
        assert error.evidence == {"answers_served": 1}
    assert providers.RateLimitAbort("x").evidence == {}


def test_pass_clock_is_always_set() -> None:
    """A cap that can be switched off by forgetting a field is not a cap."""
    import time as _time

    before = _time.monotonic()
    accounting = llm_client.RequestAccounting()
    assert isinstance(accounting.pass_started_at, float)
    assert accounting.pass_started_at >= before


def test_wall_clock_cap_applies_even_without_caller_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting accounting must not silently disable the caps."""
    purpose = "test_rate_limit_no_accounting"
    slept: list[float] = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _rate_limited())
    monkeypatch.setattr(llm_client.time, "sleep", slept.append)

    # No accounting passed at all; the guard must still terminate the loop.
    with pytest.raises(providers.RateLimitCandidateGuardExceeded):
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=0,
            rate_limit=llm_client.RateLimitPolicy(per_candidate_resends=2),
            settings=_friday_settings(),
        )
    assert slept == [5.0, 10.0]


def test_named_give_up_still_writes_exactly_one_audit_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purpose = "test_rate_limit_give_up_audit"
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _rate_limited())
    monkeypatch.setattr(llm_client.time, "sleep", lambda _d: None)

    with pytest.raises(providers.RateLimitPassResendCapExceeded):
        chat_json(
            purpose,
            "system",
            "user",
            RESULT_SCHEMA,
            max_retries=0,
            rate_limit=llm_client.RateLimitPolicy(per_pass_resend_cap=0),
            accounting=llm_client.RequestAccounting(),
            settings=_friday_settings(),
        )
    assert _audit_rows(purpose) == [
        {"model": "kimi-k3", "ok": False, "error": "rate_limit_pass_resend_cap_exceeded"}
    ]


def test_extract_news_event_forwards_the_rate_limit_plumbing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deviation 3 is unreachable unless the production path forwards these."""
    import inspect

    from alphapilot.llm import p4_news_event

    signature = inspect.signature(p4_news_event.extract_news_event)
    assert "rate_limit" in signature.parameters
    assert "accounting" in signature.parameters
    # Not defaulted here: the registered caller owns the constants.
    assert signature.parameters["rate_limit"].default is None
    assert signature.parameters["accounting"].default is None

    seen: dict[str, Any] = {}

    def fake_chat_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        raise LLMUnavailable("stop here", reason="stub")

    monkeypatch.setattr(p4_news_event, "chat_json", fake_chat_json)
    contract = _contract_with()
    policy = llm_client.RateLimitPolicy()
    accounting = llm_client.RequestAccounting()

    with pytest.raises(LLMUnavailable):
        p4_news_event.extract_news_event(
            contract,
            news_item_id=1,
            source="cninfo",
            ingested_symbol="600519",
            title="标题",
            original_text="公司公告拟回购公司股份。",
            published_at="2026-09-01T00:00:00Z",
            available_time="2026-09-01T00:00:00Z",
            body_state="title_only",
            universe_symbols={"600519"},
            settings=_dashscope_settings(
                llm_base_url=contract.endpoint,
                llm_purpose_models={contract.purpose: contract.model},
            ),
            session=None,  # type: ignore[arg-type]
            rate_limit=policy,
            accounting=accounting,
        )

    assert seen["rate_limit"] is policy
    assert seen["accounting"] is accounting
