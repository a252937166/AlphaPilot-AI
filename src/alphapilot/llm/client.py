from __future__ import annotations

import json
from time import monotonic
from typing import Any

import httpx
from jsonschema import ValidationError, validate
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings, get_settings
from alphapilot.db.engine import get_session

DEFAULT_TIMEOUT_SECONDS = 20.0
PURPOSE_TIMEOUT_SECONDS: dict[str, float] = {
    "market_summary": 45.0,
    "event_extract": 30.0,
    "stock_insight": 60.0,
    "market_feed_polish": 30.0,
    "review_advice": 45.0,
}


class LLMUnavailable(RuntimeError):
    """Raised when the optional LLM path cannot return a validated result."""


class _InvalidLLMResponse(ValueError):
    """Internal marker for malformed OpenAI-compatible response envelopes."""


class _InvalidJSONResponse(ValueError):
    """Internal marker for non-standard JSON constants such as NaN/Infinity."""


def _reject_json_constant(value: str) -> None:
    raise _InvalidJSONResponse(f"non-standard JSON constant: {value}")


def _resolve_model(settings: Settings, purpose: str) -> str | None:
    overrides = getattr(settings, "llm_purpose_models", {}) or {}
    override = overrides.get(purpose) if isinstance(overrides, dict) else None
    if isinstance(override, str) and override.strip():
        return override.strip()
    default = settings.llm_model
    return default.strip() if isinstance(default, str) and default.strip() else None


def _resolve_timeout(purpose: str, timeout: float | None) -> float:
    if timeout is not None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        return float(timeout)
    return PURPOSE_TIMEOUT_SECONDS.get(purpose, DEFAULT_TIMEOUT_SECONDS)


def _token_count(payload: Any, key: str) -> int | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, int(value))


def _add_tokens(total: int | None, value: int | None) -> int | None:
    if value is None:
        return total
    return (total or 0) + value


def _safe_error(error: BaseException) -> str:
    """Return an audit-safe reason without request, response, URL, or credentials."""
    if isinstance(error, httpx.TimeoutException):
        return "request_timeout"
    if isinstance(error, httpx.HTTPStatusError):
        return f"http_status_{error.response.status_code}"
    if isinstance(error, httpx.HTTPError):
        return "http_error"
    if isinstance(error, ValidationError):
        return "schema_validation_failed"
    if isinstance(error, (json.JSONDecodeError, UnicodeDecodeError, _InvalidJSONResponse)):
        return "invalid_json"
    if isinstance(error, _InvalidLLMResponse):
        return "invalid_response"
    return f"unexpected_{type(error).__name__}"


def _record_call(
    *,
    purpose: str,
    model: str,
    latency_ms: int,
    ok: bool,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    error: str | None,
    session: Session | None,
) -> None:
    # Import lazily so application startup and migration tooling do not create
    # a circular dependency between the ORM model module and this client.
    from alphapilot.db.models import LLMCall

    record = LLMCall(
        purpose=purpose,
        model=model,
        latency_ms=max(0, latency_ms),
        ok=ok,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        error=error,
    )
    if session is not None:
        session.add(record)
        session.flush()
        return
    with get_session() as owned_session:
        owned_session.add(record)


def _record_call_safely(
    *,
    purpose: str,
    model: str,
    latency_ms: int,
    ok: bool,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    error: str | None,
    session: Session | None,
) -> bool:
    """Persist an audit row without letting DB failures expose request details."""
    try:
        _record_call(
            purpose=purpose,
            model=model,
            latency_ms=latency_ms,
            ok=ok,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            error=error,
            session=session,
        )
    except Exception:
        # A successful LLM result is discarded by the caller when it cannot be
        # audited. Failed optional calls still degrade without breaking the UI.
        return False
    return True


def _validated_content(payload: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _InvalidLLMResponse("response body must be an object")
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise _InvalidLLMResponse("missing assistant content") from error
    if not isinstance(content, str) or not content.strip():
        raise _InvalidLLMResponse("assistant content must be a non-empty string")
    parsed = json.loads(content, parse_constant=_reject_json_constant)
    if not isinstance(parsed, dict):
        raise _InvalidLLMResponse("assistant JSON must be an object")
    validate(instance=parsed, schema=schema)
    return parsed


def chat_json(
    purpose: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    timeout: float | None = None,
    max_retries: int = 1,
    settings: Settings | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Return schema-valid JSON from an OpenAI-compatible chat endpoint.

    The model and timeout can vary by purpose. Every request disables Qwen's
    thinking mode, and all attempts belonging to this logical call are written
    as one ``llm_calls`` audit row. Prompt and response content are never stored.
    """
    if max_retries < 0:
        raise ValueError("max_retries must be zero or greater")
    purpose = purpose.strip()
    if not purpose:
        raise ValueError("purpose must not be blank")

    resolved_settings = settings or get_settings()
    model = _resolve_model(resolved_settings, purpose)
    timeout_seconds = _resolve_timeout(purpose, timeout)
    started_at = monotonic()
    audit_model = model or "unconfigured"
    base_url = (resolved_settings.llm_base_url or "").strip()
    api_key = (resolved_settings.llm_api_key or "").strip()

    if not (base_url and api_key and model):
        _record_call_safely(
            purpose=purpose,
            model=audit_model,
            latency_ms=int((monotonic() - started_at) * 1000),
            ok=False,
            prompt_tokens=None,
            completion_tokens=None,
            error="not_configured",
            session=session,
        )
        raise LLMUnavailable("LLM is not configured")

    schema_json = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    structured_system = (
        f"{system.rstrip()}\n\n"
        "只返回一个严格符合以下 JSON Schema 的 JSON 对象，不得增加解释或 Markdown：\n"
        f"{schema_json}"
    )
    request_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": structured_system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        # Qwen3 thinking mode adds unpredictable latency and must stay disabled.
        "enable_thinking": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    endpoint = base_url.rstrip("/") + "/chat/completions"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    last_error = "unknown_failure"

    for _attempt in range(max_retries + 1):
        response_payload: Any = None
        try:
            response = httpx.post(
                endpoint,
                json=request_payload,
                headers=headers,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            response_payload = response.json()
            prompt_tokens = _add_tokens(
                prompt_tokens, _token_count(response_payload, "prompt_tokens")
            )
            completion_tokens = _add_tokens(
                completion_tokens, _token_count(response_payload, "completion_tokens")
            )
            result = _validated_content(response_payload, schema)
        except (
            ValidationError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            _InvalidJSONResponse,
            _InvalidLLMResponse,
        ) as error:
            last_error = _safe_error(error)
            continue
        except httpx.HTTPError as error:
            # Retries are reserved for malformed/schema-invalid model output.
            # A network timeout must not silently double the purpose-level budget.
            last_error = _safe_error(error)
            break

        audit_written = _record_call_safely(
            purpose=purpose,
            model=model,
            latency_ms=int((monotonic() - started_at) * 1000),
            ok=True,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            error=None,
            session=session,
        )
        if not audit_written:
            raise LLMUnavailable("LLM audit persistence failed")
        return result

    _record_call_safely(
        purpose=purpose,
        model=model,
        latency_ms=int((monotonic() - started_at) * 1000),
        ok=False,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        error=last_error,
        session=session,
    )
    raise LLMUnavailable(f"LLM request failed: {last_error}")
