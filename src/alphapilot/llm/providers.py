"""Switchable LLM provider profiles for the OpenAI-compatible chat client.

AlphaPilot talks to exactly one chat endpoint at a time. Which one is chosen by
``ALPHAPILOT_LLM_PROVIDER`` and nothing else: both credential sets may coexist in
``.env``, and flipping that single variable moves every ``chat_json`` call to the
other platform. This module is the whole platform-specific surface -- pure data
plus pure functions, no I/O, no network, no settings access -- so the transport in
``alphapilot.llm.client`` stays one code path.

The ``friday`` profile
----------------------
``friday`` is an internal-platform provider. Its address is **not** in this file
and must never be: the base URL and the completions path are required settings
(``ALPHAPILOT_LLM_FRIDAY_BASE_URL`` and ``ALPHAPILOT_LLM_FRIDAY_COMPLETIONS_PATH``),
supplied from the gitignored ``.env``, and the profile fails closed when either is
absent. What the profile encodes is only the *shape* of the exchange: an
OpenAI-compatible native completions path on the internal platform, selected by
configuration, reached over a single non-streaming POST.

Reimplemented from an internal reference client; provenance recorded in the
reviewer's local evidence, deliberately not in this repository.

Two behavioural differences that matter downstream
--------------------------------------------------
1. **The internal platform accepts no ``temperature``.** Sending it is rejected
   with HTTP 400. The P4 extraction contract pins ``temperature: 0.2`` in
   ``config/p4_event_extract_eval_v1_3.yaml``; on this profile that pin cannot be
   honoured on the wire and sampling temperature falls back to the platform
   default. Evidence produced through it is therefore not temperature-comparable
   with DashScope evidence.
2. **It has no JSON mode.** ``response_format`` is not supported, so structured
   output rests on the prompt plus brace-balanced extraction. Malformed JSON is
   strictly more likely than under DashScope's ``json_object`` mode, and the
   extraction path runs with ``max_retries == 0``, so one bad object is one
   ``extract_failed`` row rather than a retry.

Three more consequences worth stating:

* The credential is an App ID bound to a model family rather than a per-key API
  secret, so one credential serves several models on that platform.
* The platform must be reached directly. A developer host may sit behind a system
  proxy that httpx would otherwise pick up from the environment, so this profile
  sets ``trust_env=False`` and does not follow redirects.
* Reasoning and answer share the ``max_tokens`` budget, and omitting the thinking
  switch leaves reasoning ON upstream. The switch is therefore always sent, and a
  response that still reports reasoning tokens is treated as a failure.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Strategy names for switching the vendor's reasoning mode off. Thinking is never
# enabled by AlphaPilot: an omitted switch means the upstream default wins, and on
# Friday that default is ON.
THINKING_OFF_QWEN_ENABLE_THINKING = "qwen_enable_thinking"
THINKING_OFF_FRIDAY_NATIVE = "friday_native"
THINKING_OFF_QWEN3_CHAT_TEMPLATE = "qwen3_chat_template"
THINKING_OFF_OMIT = "omit"

_THINKING_OFF_STRATEGIES = frozenset(
    {
        THINKING_OFF_QWEN_ENABLE_THINKING,
        THINKING_OFF_FRIDAY_NATIVE,
        THINKING_OFF_QWEN3_CHAT_TEMPLATE,
        THINKING_OFF_OMIT,
    }
)

# How the assistant message is turned into a JSON object.
JSON_EXTRACTION_STRICT = "strict"
JSON_EXTRACTION_BRACE_BALANCED = "brace_balanced"

_JSON_EXTRACTION_MODES = frozenset(
    {JSON_EXTRACTION_STRICT, JSON_EXTRACTION_BRACE_BALANCED}
)

# Upstream blocked the output. Never retryable: the same prompt is blocked again.
SECURITY_FINISH_REASON_PREFIX = "security:"
# The response was cut off by max_tokens. Diagnostic only; the JSON parse fails
# on its own if the object is truncated.
TRUNCATED_FINISH_REASON = "length"

_RETRYABLE_STATUS_CODES = frozenset({429})
_NON_RETRYABLE_STATUS_CODES = frozenset({400, 401, 403, 404, 422})


class UnknownProviderError(ValueError):
    """Raised when ``ALPHAPILOT_LLM_PROVIDER`` names no registered profile."""


class ThinkingNotDisabledError(ValueError):
    """Raised when a response proves the vendor ignored the thinking switch."""


class ProviderConfigurationError(ValueError):
    """A provider setting is missing or malformed.

    Message hygiene is part of the contract, not a nicety. This error is chained
    into an admission failure that is rendered to logs and reports, so it must
    never carry the base URL, the completions path, the endpoint digest, the HMAC
    salt or the credential -- in its message, its ``args``, or any attribute a
    traceback would render. Constructors here name environment *variables* only.
    """

    __slots__ = ()


# Historical alias; the address errors are one kind of configuration error.
MissingProviderAddressError = ProviderConfigurationError


class MissingJSONObjectError(ValueError):
    """Raised when no brace-balanced JSON object can be found in the content."""


@dataclass(frozen=True)
class ProviderProfile:
    """Everything that differs between chat platforms, and nothing else.

    Frozen on purpose: a profile is read on every call and must never be mutated
    per request. Per-request values (model, messages, request id) are arguments to
    the pure builders below.
    """

    name: str
    # ``None`` for a provider whose address must come from configuration: the
    # internal-platform address is never carried in this repository.
    default_base_url: str | None
    completions_path: str | None
    auth_scheme: str
    supports_temperature: bool
    supports_response_format: bool
    thinking_off: str
    json_extraction: str
    trust_env: bool
    follow_redirects: bool
    extra_headers: tuple[str, ...]
    # ``None`` means the platform gets one scalar timeout covering every phase,
    # which is what AlphaPilot has always sent. A number splits the phases: a
    # short connect ceiling, with the caller's budget left for the read.
    connect_timeout: float | None
    read_timeout: float
    # ``None`` means "send max_tokens only when the caller asked for it", which is
    # AlphaPilot's historical behaviour. A number means the field is always sent.
    default_max_tokens: int | None
    max_tokens_limit: int | None
    # Names of the environment variables that supply this provider's credential
    # and address. Used only to build fail-closed messages; no value is read here.
    credential_env_name: str
    base_url_env_name: str
    completions_path_env_name: str | None
    # Send an explicit ``stream`` field? Friday selects streaming by body field, so
    # the non-streaming path has to say ``false`` out loud.
    sends_stream_field: bool
    # Echo the request id into the body's ``user`` field (Friday reconciliation).
    sends_user_field: bool

    def __post_init__(self) -> None:
        if self.thinking_off not in _THINKING_OFF_STRATEGIES:
            raise ValueError(f"unknown thinking_off strategy: {self.thinking_off}")
        if self.json_extraction not in _JSON_EXTRACTION_MODES:
            raise ValueError(f"unknown json_extraction mode: {self.json_extraction}")
        if self.connect_timeout is not None and self.connect_timeout <= 0:
            raise ValueError("connect_timeout must be greater than zero")
        if self.read_timeout <= 0:
            raise ValueError("read_timeout must be greater than zero")
        if self.max_tokens_limit is not None and self.max_tokens_limit <= 0:
            raise ValueError("max_tokens_limit must be greater than zero")
        if self.default_max_tokens is not None and self.default_max_tokens <= 0:
            raise ValueError("default_max_tokens must be greater than zero")


DASHSCOPE = ProviderProfile(
    name="dashscope",
    # DashScope has no baked-in default: ``ALPHAPILOT_LLM_BASE_URL`` has always
    # been required, and inventing one here would change the unconfigured path.
    default_base_url=None,
    completions_path="/chat/completions",
    auth_scheme="Bearer",
    supports_temperature=True,
    supports_response_format=True,
    thinking_off=THINKING_OFF_QWEN_ENABLE_THINKING,
    json_extraction=JSON_EXTRACTION_STRICT,
    trust_env=True,
    follow_redirects=False,
    extra_headers=(),
    # DashScope has always been given a single scalar timeout; keep it that way.
    connect_timeout=None,
    read_timeout=20.0,
    default_max_tokens=None,
    max_tokens_limit=None,
    credential_env_name="ALPHAPILOT_LLM_API_KEY",
    base_url_env_name="ALPHAPILOT_LLM_BASE_URL",
    # A public vendor path, fixed by the OpenAI-compatible standard.
    completions_path_env_name=None,
    sends_stream_field=False,
    sends_user_field=False,
)

FRIDAY = ProviderProfile(
    name="friday",
    # Deliberately absent. This repository is public; the internal platform's
    # address is supplied at runtime from the gitignored .env and nowhere else.
    default_base_url=None,
    completions_path=None,
    auth_scheme="Bearer",
    # Both of these are hard gateway facts, not preferences. See the module
    # docstring for where each was observed.
    supports_temperature=False,
    supports_response_format=False,
    thinking_off=THINKING_OFF_FRIDAY_NATIVE,
    json_extraction=JSON_EXTRACTION_BRACE_BALANCED,
    # A developer host may sit behind a system proxy that httpx would pick up from
    # the environment. This platform has to be reached directly.
    trust_env=False,
    follow_redirects=False,
    extra_headers=("M-TraceId",),
    connect_timeout=10.0,
    read_timeout=120.0,
    default_max_tokens=2048,
    max_tokens_limit=8192,
    credential_env_name="ALPHAPILOT_LLM_FRIDAY_APP_ID",
    base_url_env_name="ALPHAPILOT_LLM_FRIDAY_BASE_URL",
    completions_path_env_name="ALPHAPILOT_LLM_FRIDAY_COMPLETIONS_PATH",
    sends_stream_field=True,
    sends_user_field=True,
)

_PROFILES: dict[str, ProviderProfile] = {
    DASHSCOPE.name: DASHSCOPE,
    FRIDAY.name: FRIDAY,
}

DEFAULT_PROVIDER = DASHSCOPE.name


def provider_names() -> tuple[str, ...]:
    """Return the registered provider names in a stable order."""
    return tuple(sorted(_PROFILES))


def get_profile(name: str | None) -> ProviderProfile:
    """Look up a profile by name, case- and whitespace-insensitively."""
    key = (name or "").strip().lower()
    if not key:
        key = DEFAULT_PROVIDER
    profile = _PROFILES.get(key)
    if profile is None:
        raise UnknownProviderError(
            f"unknown LLM provider {key!r}; expected one of {', '.join(provider_names())}"
        )
    return profile


def build_endpoint(
    profile: ProviderProfile,
    base_url: str | None,
    completions_path: str | None = None,
) -> str:
    """Join the configured base URL with the completions path.

    Both halves may be configuration-only. A provider whose profile carries no
    default for either fails closed here, naming the variable to set, rather than
    falling back to a built-in address.
    """
    resolved_base = ((base_url or "").strip() or (profile.default_base_url or "")).strip()
    if not resolved_base:
        raise ProviderConfigurationError(
            f"provider {profile.name} has no base URL configured "
            f"(set {profile.base_url_env_name})"
        )
    resolved_path = (
        (completions_path or "").strip() or (profile.completions_path or "")
    ).strip()
    if not resolved_path:
        raise ProviderConfigurationError(
            f"provider {profile.name} has no completions path configured "
            f"(set {profile.completions_path_env_name})"
        )
    if not resolved_path.startswith("/"):
        resolved_path = "/" + resolved_path
    return resolved_base.rstrip("/") + resolved_path


ENDPOINT_BINDING_SALT_ENV_NAME = "ALPHAPILOT_LLM_FRIDAY_ENDPOINT_HMAC_SALT"


def _endpoint_binding_digest(
    *,
    base_url: str,
    completions_path: str,
    salt_hex: str,
) -> str:
    """Keyed digest of one request URL. Pure; returns only the digest.

    ``HMAC-SHA256(bytes.fromhex(salt), utf8(base_url.rstrip("/") + completions_path))``.
    The URL is concatenated exactly as configured, with no normalisation beyond
    the trailing-slash trim, so the digest is reproducible from the two settings
    alone.
    """
    if not base_url:
        raise ProviderConfigurationError(
            "endpoint binding needs a base URL "
            f"(set {FRIDAY.base_url_env_name})"
        )
    if not completions_path:
        raise ProviderConfigurationError(
            "endpoint binding needs a completions path "
            f"(set {FRIDAY.completions_path_env_name})"
        )
    if not salt_hex:
        raise ProviderConfigurationError(
            f"endpoint binding needs a salt (set {ENDPOINT_BINDING_SALT_ENV_NAME})"
        )
    try:
        key = bytes.fromhex(salt_hex)
    except ValueError:
        # Never echo the salt, not even the offending characters.
        raise ProviderConfigurationError(
            f"{ENDPOINT_BINDING_SALT_ENV_NAME} must be hexadecimal"
        ) from None
    if not key:
        raise ProviderConfigurationError(
            f"{ENDPOINT_BINDING_SALT_ENV_NAME} must not be empty"
        )
    url = base_url.rstrip("/") + completions_path
    return hmac.new(key, url.encode("utf-8"), hashlib.sha256).hexdigest()


def _default_settings() -> Any:
    # Imported lazily: this module must stay importable without the app config.
    from alphapilot.core.config import get_settings

    return get_settings()


def endpoint_binding_digest(settings: Any = None) -> str:
    """Return the keyed digest that binds the configured platform endpoint.

    The registered contract records provider identity plus this digest instead of
    the address itself, so a public repository can still prove which endpoint an
    artefact was produced against. Raises when the base URL, the completions path
    or the salt is missing; the URL and the salt are never returned or logged.
    """
    resolved = _default_settings() if settings is None else settings
    return _endpoint_binding_digest(
        base_url=(getattr(resolved, "llm_friday_base_url", None) or "").strip(),
        completions_path=(
            getattr(resolved, "llm_friday_completions_path", None) or ""
        ).strip(),
        salt_hex=(
            getattr(resolved, "llm_friday_endpoint_hmac_salt", None) or ""
        ).strip(),
    )


def contract_provider(contract: Any) -> str | None:
    """Read the provider a contract pins, if it pins one.

    Accepts a loaded contract object or the parsed document, so a caller does not
    have to know which it is holding.
    """
    if contract is None:
        return None
    direct = getattr(contract, "provider", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip().lower()
    document = getattr(contract, "document", None)
    if document is None and isinstance(contract, Mapping):
        document = contract
    if isinstance(document, Mapping):
        llm = document.get("llm")
        if isinstance(llm, Mapping):
            value = llm.get("provider")
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        value = document.get("provider")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def active_provider(settings: Any = None, *, contract: Any = None) -> str:
    """Resolve which platform a call goes to: contract first, global default second.

    A global switch alone would move every caller at once. The production news
    poller pins the vendor endpoint in its own contract, so it must keep going to
    the vendor even while a held-out pass runs on the internal platform. The
    contract in force therefore wins, and ``ALPHAPILOT_LLM_PROVIDER`` only decides
    for calls whose contract says nothing.
    """
    pinned = contract_provider(contract)
    if pinned is not None:
        # Validates the name and raises on an unknown one.
        return get_profile(pinned).name
    resolved = _default_settings() if settings is None else settings
    return get_profile(getattr(resolved, "llm_provider", None)).name


def endpoint_binding_matches(settings: Any, expected_digest: str) -> bool:
    """Constant-time comparison of the configured binding against a contract."""
    if not isinstance(expected_digest, str) or not expected_digest.strip():
        return False
    return hmac.compare_digest(endpoint_binding_digest(settings), expected_digest.strip())


def clamp_max_tokens(profile: ProviderProfile, max_tokens: int | None) -> int | None:
    """Apply the profile's ceiling, then its default when the caller gave none."""
    resolved = profile.default_max_tokens if max_tokens is None else max_tokens
    if resolved is None:
        return None
    if profile.max_tokens_limit is not None:
        return min(resolved, profile.max_tokens_limit)
    return resolved


def thinking_off_fields(profile: ProviderProfile) -> dict[str, Any]:
    """Return the body fields that switch the vendor's reasoning mode off.

    The three concrete strategies are mutually exclusive by construction, which is
    the point: Friday rejects a body carrying both its native ``thinking`` object
    and ``chat_template_kwargs``.
    """
    if profile.thinking_off == THINKING_OFF_QWEN_ENABLE_THINKING:
        return {"enable_thinking": False}
    if profile.thinking_off == THINKING_OFF_FRIDAY_NATIVE:
        return {"thinking": {"type": "disabled"}}
    if profile.thinking_off == THINKING_OFF_QWEN3_CHAT_TEMPLATE:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {}


def build_payload(
    profile: ProviderProfile,
    *,
    model: str,
    messages: Sequence[Mapping[str, str]],
    max_tokens: int | None = None,
    temperature: float | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Assemble the request body for one non-streaming chat completion.

    Field order is fixed so a serialised body is comparable across runs. The
    DashScope branch reproduces the body AlphaPilot has always sent, key for key.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [dict(message) for message in messages],
    }
    if profile.sends_stream_field:
        payload["stream"] = False
    if profile.supports_response_format:
        payload["response_format"] = {"type": "json_object"}
    if profile.supports_temperature and temperature is not None:
        payload["temperature"] = temperature
    payload.update(thinking_off_fields(profile))
    if profile.sends_user_field and request_id:
        payload["user"] = request_id
    resolved_max_tokens = clamp_max_tokens(profile, max_tokens)
    if resolved_max_tokens is not None:
        payload["max_tokens"] = resolved_max_tokens
    return payload


def build_headers(
    profile: ProviderProfile,
    *,
    api_key: str,
    request_id: str | None = None,
) -> dict[str, str]:
    """Assemble request headers, including any profile-mandated extras."""
    headers = {
        "Authorization": f"{profile.auth_scheme} {api_key}",
        "Content-Type": "application/json",
    }
    for header in profile.extra_headers:
        if header == "M-TraceId":
            if not request_id:
                raise ValueError(
                    f"provider {profile.name} requires a request id for M-TraceId"
                )
            headers[header] = request_id
        else:  # pragma: no cover - guards a future profile edit
            raise ValueError(f"provider {profile.name} declares unknown header {header}")
    return headers


def find_balanced_json_object(text: str) -> str | None:
    """Return the first brace-balanced ``{...}`` slice, honouring string literals.

    A greedy ``\\{.*\\}`` regex is wrong here and was measured to be wrong: when a
    model emits trailing fragments after a valid object, the greedy match runs to
    the last closing brace and turns a correct output into an unparseable one. An
    internal reference client measured this misreading 30/30 correct outputs as
    9/30 before the scan replaced it.
    """
    if not isinstance(text, str):
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            # Backslashes only escape inside string literals; outside one they
            # cannot legally appear, so tracking them unconditionally is safe and
            # keeps the scanner branch-free.
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def json_source(profile: ProviderProfile, content: str) -> str:
    """Return the substring that should be handed to a JSON decoder.

    ``strict`` returns the content untouched, which is what the DashScope path has
    always done. ``brace_balanced`` isolates the first balanced object first,
    because Friday has no JSON mode and the model may wrap or trail the object.
    """
    if profile.json_extraction == JSON_EXTRACTION_STRICT:
        return content
    candidate = find_balanced_json_object(content)
    if candidate is None:
        raise MissingJSONObjectError("no brace-balanced JSON object in assistant content")
    return candidate


def extract_json_object(
    profile: ProviderProfile,
    content: str,
    *,
    loads: Callable[[str], Any] = json.loads,
) -> Any:
    """Decode the assistant content into a Python object per the profile.

    ``loads`` is injected so the transport can keep its hardened decoder (which
    rejects NaN/Infinity and duplicate keys) as the single decoding policy for both
    providers, while this module stays importable and testable on its own.
    """
    return loads(json_source(profile, content))


def finish_reason_of(payload: Any) -> str:
    """Read ``choices[0].finish_reason``, tolerating any malformed envelope."""
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    reason = first.get("finish_reason")
    return reason if isinstance(reason, str) else ""


def is_blocked_finish_reason(finish_reason: str) -> bool:
    """True when upstream content safety blocked the output. Never retryable."""
    return finish_reason.strip().lower().startswith(SECURITY_FINISH_REASON_PREFIX)


def is_truncated_finish_reason(finish_reason: str) -> bool:
    """True when the response hit ``max_tokens``. Diagnostic, not fatal."""
    return finish_reason.strip().lower() == TRUNCATED_FINISH_REASON


# Where a vendor reports reasoning tokens. ``completion_tokens_details`` is the
# OpenAI-standard location; a live internal-platform call on 2026-09-09 returned
# ``output_tokens_details`` instead (null, thinking off), so both are checked --
# a leak must not hide behind the other key name.
_REASONING_TOKEN_CONTAINERS = ("completion_tokens_details", "output_tokens_details")


def reasoning_tokens_of(payload: Any) -> int | None:
    """Read the reported reasoning-token count, or ``None`` when absent."""
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    for container in _REASONING_TOKEN_CONTAINERS:
        details = usage.get(container)
        if not isinstance(details, dict):
            continue
        value = details.get("reasoning_tokens")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        return int(value)
    return None


def assert_thinking_disabled(profile: ProviderProfile, payload: Any) -> None:
    """Fail closed when a response shows the thinking switch was not honoured.

    Thinking is required to stay off. If the vendor reports reasoning tokens
    anyway, the switch did not take effect, latency and token accounting are both
    wrong, and on K3 the answer text may have been emptied into the reasoning
    channel. Treating that as a failure is deliberate: a silent fallback to
    thinking-on is exactly the state this layer exists to prevent.
    """
    if profile.thinking_off == THINKING_OFF_OMIT:
        return
    reasoning_tokens = reasoning_tokens_of(payload)
    if reasoning_tokens is not None and reasoning_tokens > 0:
        raise ThinkingNotDisabledError(
            f"provider {profile.name} reported {reasoning_tokens} reasoning tokens "
            "while thinking was disabled"
        )


RATE_LIMIT_STATUS = 429


def retry_after_seconds(headers: Mapping[str, str] | Any) -> float | None:
    """Read a ``Retry-After`` header, in either of its two legal forms.

    Returns ``None`` when the header is absent or unparseable, which is the
    caller's signal to fall back to its own backoff.
    """
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    raw = getter("Retry-After") or getter("retry-after")
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        from datetime import UTC, datetime
        from email.utils import parsedate_to_datetime

        return max(0.0, (parsedate_to_datetime(text) - datetime.now(UTC)).total_seconds())
    except Exception:
        return None


# Registered fallback backoff, by attempt. The platform sends no Retry-After, so
# this schedule governs every wait in practice.
RATE_LIMIT_BACKOFF_SCHEDULE: tuple[float, ...] = (5.0, 10.0, 20.0, 30.0, 45.0, 60.0)
# A supplied delay outside this range is not a wait instruction. Zero is legal
# HTTP and means "resend now", so it is floored rather than rejected; anything
# above the ceiling means the quota is gone, not that we should sleep that long.
SUPPLIED_DELAY_MIN_SECONDS = 0.0
SUPPLIED_DELAY_MAX_SECONDS = 120.0
SUPPLIED_DELAY_FLOOR_SECONDS = 1.0


def rate_limit_backoff_seconds(attempt: int) -> float:
    """The registered fallback wait for the nth rate-limit rejection. Pure."""
    if attempt < 0:
        raise ValueError("attempt must be zero or greater")
    index = min(attempt, len(RATE_LIMIT_BACKOFF_SCHEDULE) - 1)
    return RATE_LIMIT_BACKOFF_SCHEDULE[index]


class RateLimitAbort(RuntimeError):
    """The transport stopped waiting out rate limits, and why.

    Giving up is a named outcome, never a bare 429 handed back to the caller.
    Quota exhaustion and a transient blip both surface as HTTP 429, and a pass
    that dies as a generic transport error cannot tell them apart afterwards --
    which is precisely what the census exists to record. Each subclass names one
    give-up rule and carries the counters at the moment it fired.
    """

    reason = "rate_limit_abort"

    def __init__(self, message: str, *, evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.evidence: dict[str, Any] = dict(evidence or {})


class RateLimitQuotaExhausted(RateLimitAbort):
    """A supplied delay above the ceiling: the quota is gone, do not sleep."""

    reason = "rate_limit_supplied_delay_above_ceiling"


class RateLimitCandidateGuardExceeded(RateLimitAbort):
    """One candidate consumed its whole resend guard without being served."""

    reason = "rate_limit_candidate_guard_exceeded"


class RateLimitPassResendCapExceeded(RateLimitAbort):
    """The pass consumed its total resend budget."""

    reason = "rate_limit_pass_resend_cap_exceeded"


class RateLimitWallClockCapExceeded(RateLimitAbort):
    """The next wait would wake past the pass's wall-clock ceiling."""

    reason = "rate_limit_wall_clock_cap_exceeded"


def accept_supplied_delay(delay: float | None) -> float | None:
    """Validate a platform-supplied delay against the registered window.

    ``None`` means nothing usable was supplied and the caller should fall back to
    the schedule. A zero delay is legal HTTP meaning "resend immediately"; it is
    floored to one second rather than treated as invalid, because rejecting it
    would make a round unrunnable the first time the platform used it. A delay
    above the ceiling is not a longer wait, it is the quota being gone.
    """
    if delay is None:
        return None
    if delay < SUPPLIED_DELAY_MIN_SECONDS:
        return None
    if delay > SUPPLIED_DELAY_MAX_SECONDS:
        raise RateLimitQuotaExhausted(
            f"supplied retry delay exceeds the {SUPPLIED_DELAY_MAX_SECONDS:g}s ceiling"
        )
    return max(delay, SUPPLIED_DELAY_FLOOR_SECONDS)


def is_retryable_status(status_code: int) -> bool:
    """Classify an HTTP status for callers that own their own retry policy.

    Classification only. Nothing in this repository retries transport failures:
    the P4 extraction contract asserts ``max_retries == 0`` and a transport error
    has to surface on the first attempt.
    """
    if status_code in _NON_RETRYABLE_STATUS_CODES:
        return False
    if status_code in _RETRYABLE_STATUS_CODES:
        return True
    return status_code >= 500
