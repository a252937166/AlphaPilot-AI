"""Model routing: choice work goes to jev, text and reasoning go to the Codex gateway.

Owner's rule (2026-09-24): anything that can be phrased as a classification or a choice
is asked of jev (TypeSafe, typed answers with probabilities, fast and cheap); everything
else, such as writing, reading long text, extraction and open reasoning, goes to the
Codex gateway, which runs gpt-6-luna at medium effort on the DogYun server and is reached
through the local SSH tunnel. Purposes not listed here keep the existing provider path
(``llm.client.chat_json``), so the sealed P4 contracts and their provider profiles are
untouched.

The Codex gateway is shared with other projects, runs one request at a time and spends
subscription quota, so this client sends a deadline (the gateway refuses a request that
cannot finish in time before Codex runs) and never retries on its own.
"""

from __future__ import annotations

import copy
import json
from time import monotonic
from typing import Any

import httpx
from jsonschema import ValidationError, validate
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings, get_settings
from alphapilot.db.engine import get_session
from alphapilot.db.models import LLMCall
from alphapilot.llm import client
from alphapilot.llm.client import LLMUnavailable
from alphapilot.llm.typesafe import JevClient

CODEX_MODEL = "gpt-6-luna"
DEFAULT_ROUTES: dict[str, str] = {
    "market_summary": "codex",
    "stock_insight": "codex",
    "review_advice": "codex",
    "market_feed_polish": "codex",
    "event_extract": "jev",
}
_CODEX_UNSUPPORTED = frozenset(
    {
        "maxLength",
        "minLength",
        "pattern",
        "format",
        "maxItems",
        "minItems",
        "uniqueItems",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "maxProperties",
        "minProperties",
        "default",
        "examples",
        "$schema",
        "title",
    }
)
_TRANSPORT: httpx.BaseTransport | None = None  # tests inject a mock transport


def route_for(purpose: str, settings: Settings | None = None) -> str:
    """``codex``, ``jev`` or ``default`` (the existing provider path)."""

    settings = settings or get_settings()
    routes = {**DEFAULT_ROUTES, **(settings.llm_routes or {})}
    return routes.get(purpose, "default")


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema Codex is asked to follow: every object closed and fully required.

    Keywords that structured output may reject (lengths, bounds, formats) are dropped here
    and enforced locally after the answer returns.
    """

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items() if k not in _CODEX_UNSUPPORTED}
            if isinstance(out.get("properties"), dict):
                out["type"] = out.get("type", "object")
                out["additionalProperties"] = False
                out["required"] = list(out["properties"])
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    strict: dict[str, Any] = walk(copy.deepcopy(schema))
    return strict


def repair(value: Any, schema: dict[str, Any]) -> Any:
    """Trim over-long strings and arrays and clamp numbers to the schema's own limits."""

    if isinstance(value, dict) and isinstance(schema.get("properties"), dict):
        return {k: repair(v, schema["properties"].get(k, {})) for k, v in value.items()}
    if isinstance(value, list):
        items = schema.get("items", {})
        trimmed = value[: schema["maxItems"]] if "maxItems" in schema else value
        return [repair(v, items if isinstance(items, dict) else {}) for v in trimmed]
    if isinstance(value, str) and "maxLength" in schema:
        return value[: schema["maxLength"]]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema:
            value = max(schema["minimum"], value)
        if "maximum" in schema:
            value = min(schema["maximum"], value)
    return value


def _record(
    session: Session | None,
    *,
    purpose: str,
    model: str,
    ok: bool,
    started: float,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    error: str | None = None,
) -> None:
    row = LLMCall(
        purpose=purpose,
        model=model,
        ok=ok,
        latency_ms=int((monotonic() - started) * 1000),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        error=error[:2000] if error else None,
    )
    try:
        if session is not None:
            session.add(row)
            session.flush()
        else:
            with get_session() as own:
                own.add(row)
    except Exception:  # accounting must never break the caller
        pass


def codex_json(
    purpose: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    settings: Settings | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Ask the Codex gateway for a JSON answer that must satisfy ``schema``."""

    settings = settings or get_settings()
    if not (settings.codex_api_key and settings.codex_api_base_url):
        _record(
            session,
            purpose=purpose,
            model=CODEX_MODEL,
            ok=False,
            started=monotonic(),
            error="not_configured",
        )
        raise LLMUnavailable(
            "Codex gateway is not configured (ALPHAPILOT_CODEX_API_BASE_URL/KEY)",
            reason="codex_not_configured",
        )
    budget = int(settings.codex_api_timeout_seconds)
    body = {
        "prompt": f"{system}\n\n{user}",
        "output_schema": strict_schema(schema),
        "timeout_s": budget,
        "deadline_s": budget + 60,
    }
    started = monotonic()
    try:
        with httpx.Client(timeout=budget + 90, transport=_TRANSPORT) as http:
            response = http.post(
                settings.codex_api_base_url.rstrip("/") + "/v1/run",
                headers={"Authorization": f"Bearer {settings.codex_api_key}"},
                json=body,
            )
    except httpx.HTTPError as exc:
        _record(
            session,
            purpose=purpose,
            model=CODEX_MODEL,
            ok=False,
            started=started,
            error=type(exc).__name__,
        )
        raise LLMUnavailable(
            f"Codex gateway unreachable: {type(exc).__name__}", reason="codex_unreachable"
        ) from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    usage = data.get("usage") or {}
    tokens = {
        "prompt_tokens": usage.get("input_tokens"),
        "completion_tokens": usage.get("output_tokens"),
    }
    if response.status_code != 200:
        message = str((data.get("error") or {}).get("message") or f"http {response.status_code}")
        _record(
            session,
            purpose=purpose,
            model=CODEX_MODEL,
            ok=False,
            started=started,
            error=message,
            **tokens,
        )
        raise LLMUnavailable(
            f"Codex gateway {response.status_code}: {message[:300]}",
            reason=f"codex_http_{response.status_code}",
        )
    result = data.get("json")
    if result is None:
        try:
            result = json.loads(data.get("output") or "")
        except ValueError:
            result = None
    if not isinstance(result, dict):
        _record(
            session,
            purpose=purpose,
            model=CODEX_MODEL,
            ok=False,
            started=started,
            error="non-JSON output",
            **tokens,
        )
        raise LLMUnavailable("Codex returned non-JSON output", reason="invalid_json")
    answer: dict[str, Any] = repair(result, schema)
    try:
        validate(instance=answer, schema=schema)
    except ValidationError as exc:
        field = ".".join(str(p) for p in exc.absolute_path) or None
        _record(
            session,
            purpose=purpose,
            model=CODEX_MODEL,
            ok=False,
            started=started,
            error=f"schema: {exc.message[:200]}",
            **tokens,
        )
        raise LLMUnavailable(
            "Codex output failed the schema",
            reason="schema_violation",
            field=field,
            constraint=str(exc.validator),
        ) from exc
    _record(session, purpose=purpose, model=CODEX_MODEL, ok=True, started=started, **tokens)
    return answer


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
    rate_limit: client.RateLimitPolicy | None = None,
    accounting: client.RequestAccounting | None = None,
    settings: Settings | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Drop-in for ``llm.client.chat_json`` that honours the routing table.

    Codex ignores the caller's timeout (those were tuned for a seconds-fast provider) and
    uses ``codex_api_timeout_seconds``; a purpose routed to jev must use its typed
    questions instead and is refused here.
    """

    settings = settings or get_settings()
    route = route_for(purpose, settings)
    if route == "codex":
        return codex_json(purpose, system, user, schema, settings=settings, session=session)
    if route == "jev":
        raise LLMUnavailable(
            f"{purpose} is routed to jev; use its typed questions", reason="routed_to_jev"
        )
    return client.chat_json(
        purpose,
        system,
        user,
        schema,
        timeout=timeout,
        max_tokens=max_tokens,
        max_retries=max_retries,
        provider=provider,
        rate_limit=rate_limit,
        accounting=accounting,
        settings=settings,
        session=session,
    )


def jev_ask(
    purpose: str,
    state: dict[str, Any],
    questions: dict[str, Any],
    *,
    jev: Any = None,
    settings: Settings | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """One jev call with call accounting; returns the ``answers`` mapping."""

    settings = settings or get_settings()
    started = monotonic()
    if jev is None and not settings.jev_api_key:
        _record(
            session,
            purpose=purpose,
            model=settings.jev_model,
            ok=False,
            started=started,
            error="not_configured",
        )
        raise LLMUnavailable("jev key is not configured", reason="jev_not_configured")
    owned = jev is None
    asker = jev or JevClient(settings.jev_api_key, settings.jev_model)
    try:
        data = asker.ask(state, questions)
    except Exception as exc:
        _record(
            session,
            purpose=purpose,
            model=getattr(asker, "model", "jev"),
            ok=False,
            started=started,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise LLMUnavailable(f"jev failed: {type(exc).__name__}", reason="jev_failed") from exc
    finally:
        if owned:
            asker.close()
    answers = data.get("answers") or {}
    missing = [name for name in questions if name not in answers]
    usage = data.get("usage") or {}
    if missing:
        _record(
            session,
            purpose=purpose,
            model=asker.model,
            ok=False,
            started=started,
            error=f"missing answers: {missing}",
            prompt_tokens=usage.get("input_tokens"),
        )
        raise LLMUnavailable(f"jev did not answer {missing}", reason="jev_incomplete")
    _record(
        session,
        purpose=purpose,
        model=asker.model,
        ok=True,
        started=started,
        prompt_tokens=usage.get("input_tokens"),
        completion_tokens=usage.get("output_tokens"),
    )
    return answers


def score_mean(answer: dict[str, Any], levels: int) -> float:
    """Probability-weighted level of a jev score answer, scaled to 0..1."""

    probabilities = {int(k): float(v) for k, v in (answer.get("probabilities") or {}).items()}
    total = sum(probabilities.values())
    if levels < 2 or total <= 0:
        raise ValueError("invalid score answer")
    mean = sum(level * p for level, p in probabilities.items()) / total
    return max(0.0, min(1.0, mean / (levels - 1)))
