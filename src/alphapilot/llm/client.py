from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

import httpx
from jsonschema import ValidationError, validate
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings, get_settings
from alphapilot.db.engine import get_session
from alphapilot.llm import providers
from alphapilot.llm.providers import ProviderProfile

LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 20.0
PURPOSE_TIMEOUT_SECONDS: dict[str, float] = {
    "market_summary": 45.0,
    "event_extract": 30.0,
    "stock_insight": 60.0,
    "market_feed_polish": 30.0,
    "review_advice": 45.0,
}


@dataclass(frozen=True)
class RateLimitPolicy:
    """How to wait out a gateway rate-limit rejection before re-sending.

    This is a *transport* concern and is deliberately separate from
    ``max_retries``. ``max_retries`` governs malformed or schema-invalid model
    output, and the P4 extraction contract pins it at zero; a 429 is not model
    output at all -- the gateway refused to process the request -- so re-sending
    it produces one answer to one question, not a second sample of the same
    question. A caller that wants no waiting at all simply passes no policy,
    which is the default and leaves behaviour exactly as it was.
    """

    # Registered constants. per_candidate_resends bounds one record; the pass
    # caps bound the whole run so a degraded platform cannot burn the window.
    per_candidate_resends: int = 6
    per_pass_resend_cap: int = 2000
    per_pass_wall_clock_cap_seconds: float = 50_400.0
    honour_retry_after: bool = True

    def __post_init__(self) -> None:
        if self.per_candidate_resends < 0:
            raise ValueError("per_candidate_resends must be zero or greater")
        if self.per_pass_resend_cap < 0:
            raise ValueError("per_pass_resend_cap must be zero or greater")
        if self.per_pass_wall_clock_cap_seconds <= 0:
            raise ValueError("per_pass_wall_clock_cap_seconds must be greater than zero")

    @property
    def max_waits(self) -> int:
        """Backwards-compatible alias for the per-candidate resend guard."""
        return self.per_candidate_resends


@dataclass
class RequestAccounting:
    """Requests started versus answers served, itemised.

    The frozen contract counts *requests*, so a run that waits out rate limits
    has to be able to say ``served == expected`` and
    ``started == expected + rate_limit_waits``, with every wait itemised -- the
    same shape the CNInfo download budget already reports.
    """

    started: int = 0
    served: int = 0
    rate_limited: int = 0
    waits: list[float] = field(default_factory=list)
    # Monotonic instant the pass began, for the wall-clock cap. Defaulted at
    # construction rather than left unset: an accounting object's lifetime is
    # the pass's lifetime, and the cap is the only thing standing between a
    # throttled credential and a run nobody authorised. It must not be possible
    # to switch it off by forgetting a field.
    pass_started_at: float = field(default_factory=time.monotonic)

    @property
    def wait_seconds(self) -> float:
        return sum(self.waits)

    def as_evidence(self) -> dict[str, Any]:
        return {
            "requests_started": self.started,
            "answers_served": self.served,
            "rate_limited_responses": self.rate_limited,
            "rate_limit_waits": len(self.waits),
            "rate_limit_wait_seconds": round(self.wait_seconds, 3),
            "rate_limit_wait_items": [round(value, 3) for value in self.waits],
        }


class LLMUnavailable(RuntimeError):
    """Raised when the optional LLM path cannot return a validated result."""

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        field: str | None = None,
        constraint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.field = field
        self.constraint = constraint


class _InvalidLLMResponse(ValueError):
    """Internal marker for malformed OpenAI-compatible response envelopes."""


class _InvalidJSONResponse(ValueError):
    """Internal marker for non-standard JSON constants such as NaN/Infinity."""


class _OutputBlocked(ValueError):
    """Internal marker for upstream content-safety refusal. Never retryable."""


class _SchemaValidationFailure(ValueError):
    """Internal schema failure carrying only allowlisted, payload-free metadata."""

    def __init__(self, *, field: str, constraint: str) -> None:
        super().__init__("schema validation failed")
        self.field = field
        self.constraint = constraint


_SCHEMA_VALIDATOR_CONSTRAINTS: dict[str, str] = {
    "additionalProperties": "json_schema_additional_properties",
    "enum": "json_schema_enum",
    "maximum": "json_schema_maximum",
    "maxItems": "json_schema_max_items",
    "maxLength": "json_schema_max_length",
    "minimum": "json_schema_minimum",
    "minLength": "json_schema_min_length",
    "pattern": "json_schema_pattern",
    "required": "json_schema_required",
    "type": "json_schema_type",
    "uniqueItems": "json_schema_unique_items",
}


def _reject_json_constant(value: str) -> None:
    raise _InvalidJSONResponse(f"non-standard JSON constant: {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidJSONResponse(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _resolve_model(
    settings: Settings,
    purpose: str,
    default_model: str | None = None,
) -> str | None:
    """Per-purpose override first, then the active provider's default model."""
    overrides = getattr(settings, "llm_purpose_models", {}) or {}
    override = overrides.get(purpose) if isinstance(overrides, dict) else None
    if isinstance(override, str) and override.strip():
        return override.strip()
    default = settings.llm_model if default_model is None else default_model
    return default.strip() if isinstance(default, str) and default.strip() else None


def _resolve_timeout(
    purpose: str,
    timeout: float | None,
    profile: ProviderProfile | None = None,
) -> float:
    """Resolve the per-call budget: caller, then purpose table, then provider.

    An explicit caller timeout and the purpose table always win. Only the
    otherwise-unspecified default varies by provider, so switching platforms can
    never silently widen a governed budget.
    """
    if timeout is not None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        return float(timeout)
    purpose_timeout = PURPOSE_TIMEOUT_SECONDS.get(purpose)
    if purpose_timeout is not None:
        return purpose_timeout
    if profile is None:
        return DEFAULT_TIMEOUT_SECONDS
    return float(profile.read_timeout)


def _resolve_credentials(
    settings: Settings,
    profile: ProviderProfile,
    purpose: str,
) -> tuple[str, str, str, str | None]:
    """Return ``(base_url, completions_path, credential, model)`` for the provider.

    The internal-platform provider carries no address in code, so both halves of
    its endpoint come from settings and an empty one must fail closed.
    """
    if profile.name == providers.FRIDAY.name:
        base_url = getattr(settings, "llm_friday_base_url", None) or ""
        completions_path = getattr(settings, "llm_friday_completions_path", None) or ""
        credential = getattr(settings, "llm_friday_app_id", None) or ""
        default_model = getattr(settings, "llm_friday_model", None)
    else:
        base_url = settings.llm_base_url or ""
        completions_path = profile.completions_path or ""
        credential = settings.llm_api_key or ""
        default_model = None
    return (
        base_url.strip(),
        completions_path.strip(),
        credential.strip(),
        _resolve_model(settings, purpose, default_model),
    )


def _request_timeout(profile: ProviderProfile, timeout_seconds: float) -> Any:
    """Build the httpx timeout for this provider.

    A profile with no connect ceiling keeps the single float AlphaPilot has always
    sent, so the DashScope request is unchanged. Friday splits the phases: a short
    connect ceiling with the caller's full budget left for the read, because a
    total cap that also bounds the read is what turned normal slow responses into
    false failures upstream: an internal reference client measured a 45 s total cap
    pinning its own median latency at exactly 45.0 s, misclassifying half the
    calls as failures.
    """
    if profile.connect_timeout is None or profile.connect_timeout >= timeout_seconds:
        return timeout_seconds
    return httpx.Timeout(
        connect=profile.connect_timeout,
        read=timeout_seconds,
        write=timeout_seconds,
        pool=timeout_seconds,
    )


def _transport_options(profile: ProviderProfile) -> dict[str, Any]:
    """Per-provider httpx keyword arguments.

    Empty for DashScope so its request is byte-for-byte the one AlphaPilot has
    always sent. Friday must not read proxy settings from the environment (this
    host runs a system proxy) and must not follow redirects.
    """
    options: dict[str, Any] = {}
    if not profile.trust_env:
        options["trust_env"] = False
        options["follow_redirects"] = profile.follow_redirects
    return options


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
    if isinstance(error, _SchemaValidationFailure):
        return "schema_validation_failed"
    if isinstance(error, providers.MissingJSONObjectError):
        # Only reachable on a provider without JSON mode; a content-shaped
        # failure, so it keeps the existing invalid_json classification.
        return "invalid_json"
    if isinstance(error, (json.JSONDecodeError, UnicodeDecodeError, _InvalidJSONResponse)):
        return "invalid_json"
    if isinstance(error, providers.ThinkingNotDisabledError):
        return "thinking_not_disabled"
    if isinstance(error, _OutputBlocked):
        return "output_blocked"
    if isinstance(error, _InvalidLLMResponse):
        return "invalid_response"
    return f"unexpected_{type(error).__name__}"


def _safe_schema_violation(
    error: ValidationError,
    schema: dict[str, Any],
    *,
    candidate_keys: frozenset[str],
) -> tuple[str, str]:
    """Reduce jsonschema diagnostics to frozen top-level names and fixed codes."""
    properties = schema.get("properties")
    allowed_fields = (
        frozenset(key for key in properties if isinstance(key, str))
        if isinstance(properties, dict)
        else frozenset()
    )
    validator = str(error.validator)
    constraint = _SCHEMA_VALIDATOR_CONSTRAINTS.get(
        validator,
        "json_schema_constraint",
    )

    # An unexpected model-provided key is raw payload, so never expose it.
    if validator == "additionalProperties":
        return "result", constraint

    if validator == "required":
        required = schema.get("required")
        if not isinstance(required, list) or not all(
            isinstance(field, str) for field in required
        ):
            return "result", constraint
        missing = frozenset(required).difference(candidate_keys)
        if len(missing) == 1:
            field = next(iter(missing))
            if field in allowed_fields:
                return field, constraint
        return "result", constraint

    path = tuple(error.absolute_path)
    if path and isinstance(path[0], str) and path[0] in allowed_fields:
        return path[0], constraint
    return "result", constraint


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


def _strict_json_loads(text: str) -> Any:
    """The single JSON decoding policy: no NaN/Infinity, no duplicate keys."""
    return json.loads(
        text,
        parse_constant=_reject_json_constant,
        object_pairs_hook=_reject_duplicate_json_keys,
    )


def _validated_content(
    payload: Any,
    schema: dict[str, Any],
    profile: ProviderProfile = providers.DASHSCOPE,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _InvalidLLMResponse("response body must be an object")
    finish_reason = providers.finish_reason_of(payload)
    if providers.is_blocked_finish_reason(finish_reason):
        # Upstream content safety refused the output. Retrying the same prompt is
        # refused the same way, so this must not re-enter the attempt loop.
        raise _OutputBlocked("upstream blocked the model output")
    if providers.is_truncated_finish_reason(finish_reason):
        # Reasoning and answer share the max_tokens budget upstream. Say so, or
        # this shows up only as a mysteriously short answer.
        LOGGER.warning(
            "LLM output truncated by max_tokens: provider=%s reasoning_tokens=%s",
            profile.name,
            providers.reasoning_tokens_of(payload),
        )
    providers.assert_thinking_disabled(profile, payload)
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise _InvalidLLMResponse("missing assistant content") from error
    if not isinstance(content, str) or not content.strip():
        raise _InvalidLLMResponse("assistant content must be a non-empty string")
    parsed = providers.extract_json_object(profile, content, loads=_strict_json_loads)
    if not isinstance(parsed, dict):
        raise _InvalidLLMResponse("assistant JSON must be an object")
    try:
        validate(instance=parsed, schema=schema)
    except ValidationError as error:
        field, constraint = _safe_schema_violation(
            error,
            schema,
            candidate_keys=frozenset(parsed),
        )
        raise _SchemaValidationFailure(
            field=field,
            constraint=constraint,
        ) from None
    return parsed


def _post_once(
    endpoint: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: Any,
    transport_options: dict[str, Any],
    rate_limit: RateLimitPolicy | None,
    accounting: RequestAccounting | None,
) -> httpx.Response:
    """POST once, waiting out gateway rate limits when a policy allows it.

    Only HTTP 429 is waited on. Every other status, and every transport error,
    propagates on the first attempt exactly as before.

    Giving up is never a bare 429 handed back: each of the four give-up rules
    raises its own named error carrying the counters, so quota exhaustion and a
    transient rejection stay distinguishable in the evidence.
    """
    # The caps are evaluated against these counters, so they are never optional
    # once a policy is in force. A caller that wants no census still gets the
    # caps; it simply does not see the tally.
    ledger = accounting if accounting is not None else RequestAccounting()
    waits = 0
    while True:
        ledger.started += 1
        response = httpx.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=timeout,
            **transport_options,
        )
        if response.status_code != providers.RATE_LIMIT_STATUS:
            if 200 <= response.status_code < 300:
                ledger.served += 1
            return response
        ledger.rate_limited += 1
        if rate_limit is None:
            return response
        if waits >= rate_limit.per_candidate_resends:
            raise providers.RateLimitCandidateGuardExceeded(
                f"one candidate exhausted its {rate_limit.per_candidate_resends} resends",
                evidence=ledger.as_evidence(),
            )
        if len(ledger.waits) >= rate_limit.per_pass_resend_cap:
            raise providers.RateLimitPassResendCapExceeded(
                f"pass reached its {rate_limit.per_pass_resend_cap} resend cap",
                evidence=ledger.as_evidence(),
            )
        supplied = (
            providers.retry_after_seconds(response.headers)
            if rate_limit.honour_retry_after
            else None
        )
        try:
            delay = providers.accept_supplied_delay(supplied)
        except providers.RateLimitQuotaExhausted as error:
            # A delay past the ceiling is the platform saying the quota is gone,
            # not asking us to sleep longer.
            error.evidence = ledger.as_evidence()
            raise
        if delay is None:
            delay = providers.rate_limit_backoff_seconds(waits)
        # Checked against the instant we would wake, not the instant we go to
        # sleep, so a long wait cannot overrun the window unnoticed.
        projected_wake = time.monotonic() + delay - ledger.pass_started_at
        if projected_wake > rate_limit.per_pass_wall_clock_cap_seconds:
            raise providers.RateLimitWallClockCapExceeded(
                "next wait would wake past the pass wall-clock cap",
                evidence=ledger.as_evidence(),
            )
        ledger.waits.append(delay)
        LOGGER.info(
            "LLM rate limited: waiting %.1fs before re-sending (wait %d of %d)",
            delay,
            waits + 1,
            rate_limit.per_candidate_resends,
        )
        # Looked up on the module at call time so a caller can substitute a
        # clock; binding it as a default would freeze it at import.
        time.sleep(delay)
        waits += 1


def _log_upstream_trace(
    profile: ProviderProfile,
    response: Any,
    request_id: str,
) -> None:
    """Record the platform's own trace id against ours. Ids only, never content.

    Friday returns the id it filed the request under in the ``m-traceid``
    response header; keeping both sides of that pairing is what makes an upstream
    reconciliation possible later. The audit row shape is fixed, so this goes to
    the log rather than the database.
    """
    if "M-TraceId" not in profile.extra_headers:
        return
    try:
        upstream = response.headers.get("m-traceid")
    except Exception:  # pragma: no cover - a header map that cannot be read
        return
    LOGGER.info(
        "LLM call traced: provider=%s request_id=%s upstream_trace=%s",
        profile.name,
        request_id,
        upstream or "-",
    )


def chat_json(
    purpose: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    timeout: float | None = None,
    max_tokens: int | None = None,
    max_retries: int = 1,
    provider: str | None = None,
    rate_limit: RateLimitPolicy | None = None,
    accounting: RequestAccounting | None = None,
    settings: Settings | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Return schema-valid JSON from an OpenAI-compatible chat endpoint.

    The platform is chosen by the ``provider`` argument when the caller has a
    contract that pins one, and by ``settings.llm_provider`` otherwise;
    everything that differs between platforms lives in
    :mod:`alphapilot.llm.providers`. The model
    and timeout can vary by purpose. Every request disables the vendor's thinking
    mode, and all attempts belonging to this logical call are written as one
    ``llm_calls`` audit row. Prompt and response content are never stored.

    Retries remain reserved for malformed or schema-invalid model output. No
    transport failure is ever retried here: the P4 extraction contract asserts
    ``max_retries == 0`` and a transport error has to surface on attempt one.

    ``rate_limit`` is the one exception, and a different thing. A gateway 429 is
    not model output: the request was refused before it was processed, so waiting
    and re-sending yields one answer to one question rather than a second sample.
    It is opt-in and off by default. ``accounting`` collects requests started
    against answers served, so a caller can report ``served == expected`` and
    ``started == expected + rate_limit_waits`` with every wait itemised.
    """
    if max_retries < 0:
        raise ValueError("max_retries must be zero or greater")
    if max_tokens is not None:
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
            raise TypeError("max_tokens must be an integer")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be greater than zero")
    purpose = purpose.strip()
    if not purpose:
        raise ValueError("purpose must not be blank")

    resolved_settings = settings or get_settings()
    started_at = monotonic()
    # An explicit provider comes from the contract in force for this call and
    # wins over the process-wide default, so setting the global for one run
    # cannot drag another caller's contract onto the wrong platform.
    provider_name = (
        provider if provider is not None
        else getattr(resolved_settings, "llm_provider", None)
    )
    try:
        profile = providers.get_profile(provider_name)
    except providers.UnknownProviderError as error:
        _record_call_safely(
            purpose=purpose,
            model="unconfigured",
            latency_ms=int((monotonic() - started_at) * 1000),
            ok=False,
            prompt_tokens=None,
            completion_tokens=None,
            error="provider_unknown",
            session=session,
        )
        raise LLMUnavailable(str(error), reason="provider_unknown") from None

    base_url, completions_path, api_key, model = _resolve_credentials(
        resolved_settings, profile, purpose
    )
    if not base_url:
        base_url = (profile.default_base_url or "").strip()
    if not completions_path:
        completions_path = (profile.completions_path or "").strip()
    timeout_seconds = _resolve_timeout(purpose, timeout, profile)
    audit_model = model or "unconfigured"

    if not (base_url and completions_path and api_key and model):
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
        if profile is providers.DASHSCOPE:
            raise LLMUnavailable("LLM is not configured")
        # Fail closed and name the one variable that is missing, rather than
        # silently degrading to another platform's credentials.
        missing: str
        variable: str
        if not base_url:
            missing, variable = "base URL", profile.base_url_env_name
        elif not completions_path:
            missing = "completions path"
            variable = profile.completions_path_env_name or "the completions path"
        elif not model:
            missing, variable = "model", "ALPHAPILOT_LLM_FRIDAY_MODEL"
        else:
            missing, variable = "credential", profile.credential_env_name
        raise LLMUnavailable(
            f"LLM is not configured: provider {profile.name} has no {missing} "
            f"(set {variable})"
        )

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
    # One id per logical call: sent as M-TraceId (and echoed in the body's user
    # field) where the provider requires it, and used to reconcile against the
    # trace id the platform reports back.
    request_id = str(uuid.uuid4())
    request_payload = providers.build_payload(
        profile,
        model=model,
        messages=(
            {"role": "system", "content": structured_system},
            {"role": "user", "content": user},
        ),
        max_tokens=max_tokens,
        # Thinking mode adds unpredictable latency and must stay disabled; the
        # profile decides which vendor switch expresses that.
        temperature=0.2,
        request_id=request_id,
    )
    headers = providers.build_headers(
        profile,
        api_key=api_key,
        request_id=request_id,
    )
    endpoint = providers.build_endpoint(profile, base_url, completions_path)
    request_timeout = _request_timeout(profile, timeout_seconds)
    transport_options = _transport_options(profile)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    last_error = "unknown_failure"
    last_schema_field: str | None = None
    last_schema_constraint: str | None = None

    for _attempt in range(max_retries + 1):
        response_payload: Any = None
        try:
            response = _post_once(
                endpoint,
                payload=request_payload,
                headers=headers,
                timeout=request_timeout,
                transport_options=transport_options,
                rate_limit=rate_limit,
                accounting=accounting,
            )
            _log_upstream_trace(profile, response, request_id)
            response.raise_for_status()
            response_payload = response.json()
            prompt_tokens = _add_tokens(
                prompt_tokens, _token_count(response_payload, "prompt_tokens")
            )
            completion_tokens = _add_tokens(
                completion_tokens, _token_count(response_payload, "completion_tokens")
            )
            result = _validated_content(response_payload, schema, profile)
        except providers.RateLimitAbort as error:
            # Terminal by construction: the pass gave up on a named rule. Record
            # it under that rule's own reason so the census can tell quota
            # exhaustion from a transient rejection, then let it propagate.
            _record_call_safely(
                purpose=purpose,
                model=model,
                latency_ms=int((monotonic() - started_at) * 1000),
                ok=False,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                error=error.reason,
                session=session,
            )
            raise
        except (
            providers.ThinkingNotDisabledError,
            _OutputBlocked,
        ) as error:
            # Neither can be fixed by asking the same question again: the switch
            # was ignored upstream, or the output was refused on policy.
            last_error = _safe_error(error)
            last_schema_field = None
            last_schema_constraint = None
            break
        except (
            _SchemaValidationFailure,
            providers.MissingJSONObjectError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            _InvalidJSONResponse,
            _InvalidLLMResponse,
        ) as error:
            last_error = _safe_error(error)
            if isinstance(error, _SchemaValidationFailure):
                last_schema_field = error.field
                last_schema_constraint = error.constraint
            else:
                last_schema_field = None
                last_schema_constraint = None
            continue
        except httpx.HTTPError as error:
            # Retries are reserved for malformed/schema-invalid model output.
            # A network timeout must not silently double the purpose-level budget.
            last_error = _safe_error(error)
            last_schema_field = None
            last_schema_constraint = None
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
    raise LLMUnavailable(
        f"LLM request failed: {last_error}",
        reason=last_error,
        field=last_schema_field,
        constraint=last_schema_constraint,
    )
