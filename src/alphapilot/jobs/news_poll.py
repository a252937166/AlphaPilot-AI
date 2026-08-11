from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from itertools import pairwise
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Literal, cast
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import httpx
import yaml
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select, text

from alphapilot.core.config import Settings, get_settings
from alphapilot.core.job_execution_context import current_job_run
from alphapilot.data.provenance import AUDITED_NEWS_SOURCES
from alphapilot.db.engine import get_session
from alphapilot.db.models import (
    BrokerOrder,
    JobRun,
    NewsItem,
    Security,
    TradeProposalRecord,
    WatchlistItem,
    utcnow,
)
from alphapilot.futu.client import PERMANENTLY_BLOCKED_METHODS
from alphapilot.jobs.registry import JobExecutionError, JobOutcome, JobSpec, register, run_job

JsonObject = dict[str, Any]
HttpClientFactory = Callable[[str], Any]

PROJECT_DIR = Path(__file__).resolve().parents[3]
V1_CONFIG_PATH = PROJECT_DIR / "config/p4_news_poll_v1.yaml"
V2_CONFIG_PATH = PROJECT_DIR / "config/p4_news_poll_v2.yaml"
V2_1_CONFIG_PATH = PROJECT_DIR / "config/p4_news_poll_v2_1.yaml"
DEFAULT_CONFIG_PATH = V2_1_CONFIG_PATH
# Filled after the versioned config is finalized. A different byte stream must
# ship as a reviewed config/code change before it can make network requests.
EXPECTED_CONFIG_SHA256 = "d0dcd665472b50092a1b4fa7f65f7115778e1b89ac11aca0ed49dc70beaa790b"
EXPECTED_V2_CONFIG_SHA256 = (
    "a76a1cd9f1afd021de4d343a6550a1eb05ddad1b14d8d39cbaae2659574a5834"
)
EXPECTED_V2_1_CONFIG_SHA256 = (
    "9d56e137baf10bd0858723a93aff02c57bf7b35f8705f1817b16a89ec615183f"
)
EXPECTED_CONFIG_SHA256_BY_VERSION = {
    "p4.1-news-poll-v1": EXPECTED_CONFIG_SHA256,
    "p4.1-news-poll-v2": EXPECTED_V2_CONFIG_SHA256,
    "p4.1-news-poll-v2.1": EXPECTED_V2_1_CONFIG_SHA256,
}
# The abandoned v2 contract remains permanently non-runnable. v2.1 manual
# entry points remain receipt-bound; its separately reviewed scheduler gate is
# activated here without changing the frozen v2.1 config bytes.
V2_IMPLEMENTATION_READY = False
V2_1_CODE_READY = True
V2_1_SCHEDULER_ACTIVATED = True
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
NEWS_POLL_ENABLED_ENV = "ALPHAPILOT_NEWS_POLL_ENABLED"
_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_KEYS = {"from", "spm", "track", "source"}


@dataclass(frozen=True, slots=True)
class NewsPollConfig:
    path: Path
    sha256: str
    document: JsonObject


@dataclass(frozen=True, slots=True)
class NewsCandidate:
    source: str
    symbol: str | None
    title: str
    url: str
    published_at: datetime | None
    content: str
    raw_payload: JsonObject


@dataclass(slots=True)
class SourceBatch:
    source_id: str
    status: str = "ok"
    candidates: list[NewsCandidate] = field(default_factory=list)
    request_count: int = 0
    retry_count: int = 0
    logical_request_count: int | None = None
    physical_attempt_count: int | None = None
    failures: list[JsonObject] = field(default_factory=list)
    details: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DailyCheckpointSeed:
    checkpoint_date_shanghai: date | None
    newest_observed_at_utc: datetime | None
    legacy_watermark_utc: datetime | None
    lineage: str


V2ExecutionMode = Literal[
    "scheduler",
    "initial_backlog_migration",
    "standard_incremental_validation",
]


class NewsSourceError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        blocked: bool = False,
        suppression: JsonObject | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.blocked = blocked
        self.suppression = dict(suppression) if suppression is not None else None


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)


def load_news_poll_config(path: Path = DEFAULT_CONFIG_PATH) -> NewsPollConfig:
    payload = path.read_bytes()
    digest = _sha256_bytes(payload)
    loaded: object = yaml.safe_load(payload)
    if not isinstance(loaded, dict):
        raise ValueError("P4.1 news-poll config must be a mapping")
    raw_document = cast(JsonObject, loaded)
    version = raw_document.get("schema_version")
    expected_digest = EXPECTED_CONFIG_SHA256_BY_VERSION.get(str(version))
    if expected_digest is None:
        raise ValueError("unsupported P4.1 news-poll config version")
    if digest != expected_digest:
        raise ValueError(
            "P4.1 news-poll config bytes differ from the pre-registered SHA-256"
        )
    document = raw_document
    if version == "p4.1-news-poll-v2.1":
        superseded = document.get("superseded_v2")
        if not isinstance(superseded, dict) or superseded != {
            "config": "config/p4_news_poll_v2.yaml",
            "config_sha256": EXPECTED_V2_CONFIG_SHA256,
            "status": "superseded_before_activation",
            "reason": (
                "dual-column-capacity-assumption-invalidated-by-controlled-probe"
            ),
            "activation_forbidden": True,
        }:
            raise ValueError("P4.1 v2.1 superseded-v2 binding drifted")
    runtime = document.get("runtime")
    if (
        not isinstance(runtime, dict)
        or runtime.get("scheduler_enabled_env") != NEWS_POLL_ENABLED_ENV
        or runtime.get("scheduler_enabled_default") is not False
        or runtime.get("dedicated_scheduler_launchd_value") is not True
    ):
        raise ValueError("P4.1 news-poll scheduler activation contract drifted")
    phase_gate = document.get("phase_gate")
    if not isinstance(phase_gate, dict):
        raise ValueError("P4 phase gate is missing")
    if version == "p4.1-news-poll-v1":
        if phase_gate.get("p4_2_unlocked") is not False:
            raise ValueError("P4.2 must remain locked during P4.1")
    elif (
        phase_gate.get("p4_2b_production_wiring_unlocked") is not False
        or phase_gate.get("p4_3_unlocked") is not False
    ):
        raise ValueError("P4.2b and P4.3 must remain locked during P4.1 v2")
    if version == "p4.1-news-poll-v2.1" and (
        phase_gate.get("p4_1_v2_1_code_ready") is not True
        or phase_gate.get("p4_1_v2_1_scheduler_activated") is not False
        or phase_gate.get("initial_backlog_migration_complete") is not False
        or phase_gate.get("standard_incremental_validation_complete") is not False
    ):
        raise ValueError("P4.1 v2.1 code/migration/scheduler phase gates drifted")

    sources = document.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("P4.1 source contract is missing")
    cninfo = sources.get("cninfo")
    if (
        not isinstance(cninfo, dict)
        or cninfo.get("enabled") is not True
        or cninfo.get("critical") is not True
        or cninfo.get("verify_tls") is not True
        or not str(cninfo.get("announcements_url", "")).startswith("https://")
        or not str(cninfo.get("static_url_prefix", "")).startswith("https://")
    ):
        raise ValueError("CNInfo must remain an enabled strict-TLS critical source")
    cls = sources.get("akshare_cls")
    cls_attempts_key = (
        "max_attempts_per_request"
        if version == "p4.1-news-poll-v1"
        else "max_attempts_per_logical_request"
    )
    if (
        not isinstance(cls, dict)
        or cls.get("enabled") is not False
        or cls.get("frozen_status") != "unavailable"
        or cls.get(cls_attempts_key) != 0
    ):
        raise ValueError("CLS must remain frozen unavailable with zero attempts")
    futu = sources.get("futu_auxiliary")
    if (
        not isinstance(futu, dict)
        or futu.get("enabled") is not False
        or futu.get("frozen_status") != "pending_trading_day_latency_retest"
        or futu.get("allowed_trade_methods") != []
    ):
        raise ValueError("Futu auxiliary must remain disabled pending latency review")

    acceptance = document.get("acceptance")
    if not isinstance(acceptance, dict):
        raise ValueError("P4.1 acceptance contract is missing")
    dates = acceptance.get("trading_dates")
    expected_dates = (
        ["2026-08-03", "2026-08-04", "2026-08-05"]
        if version == "p4.1-news-poll-v1"
        else ["2026-08-10", "2026-08-11", "2026-08-12"]
    )
    if dates != expected_dates:
        raise ValueError("P4.1 three-day window changed without a new config version")
    return NewsPollConfig(path=path, sha256=digest, document=document)


def _is_v2_config(config: NewsPollConfig) -> bool:
    return config.document.get("schema_version") in {
        "p4.1-news-poll-v2",
        "p4.1-news-poll-v2.1",
    }


def _v2_execution_gate_error(
    config: NewsPollConfig,
    *,
    code: str,
    message: str,
    execution_mode: V2ExecutionMode,
) -> JobExecutionError:
    gate_started_at = utcnow()
    return JobExecutionError(
        message,
        stats={
            "config_version": config.document["schema_version"],
            "config_path": str(config.path.relative_to(PROJECT_DIR)),
            "config_sha256": config.sha256,
            "execution_mode": execution_mode,
            "implementation_gate": code,
            "v2_code_ready": (
                V2_1_CODE_READY
                if config.document.get("schema_version") == "p4.1-news-poll-v2.1"
                else V2_IMPLEMENTATION_READY
            ),
            "v2_scheduler_activated": (
                V2_1_SCHEDULER_ACTIVATED
                if config.document.get("schema_version") == "p4.1-news-poll-v2.1"
                else False
            ),
            "fail_closed_before_job_context": True,
            "network_attempted": False,
            "fetch_started": False,
            "poll_started_at": gate_started_at.isoformat(),
            "poll_completed_at": utcnow().isoformat(),
            "run_mode": execution_mode,
            "coverage_gap": False,
            "safety_unchanged": True,
            "sources": {},
            "p4_2b_production_wiring_unlocked": False,
            "p4_3_unlocked": False,
            "terminal_diagnostics": _v2_terminal_diagnostic(
                code=code,
                source="news_poll",
                constraint="execution_gate",
                recoverable=False,
            ),
        },
    )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _load_v2_1_manual_authorization(
    path: Path,
    *,
    config: NewsPollConfig,
    execution_mode: V2ExecutionMode,
) -> JsonObject:
    try:
        payload = path.read_bytes()
        loaded: object = json.loads(payload, object_pairs_hook=_strict_json_object)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("v2.1 manual authorization receipt is unreadable") from exc
    if not isinstance(loaded, dict):
        raise ValueError("v2.1 manual authorization receipt must be an object")
    receipt = cast(JsonObject, loaded)
    if receipt.get("schema_version") != (
        "p4.1-news-poll-v2.1-manual-authorization-v1"
    ):
        raise ValueError("v2.1 manual authorization schema drifted")
    if (
        receipt.get("config_sha256") != config.sha256
        or receipt.get("execution_mode") != execution_mode
        or receipt.get("authorized") is not True
        or receipt.get("network_execution_authorized") is not True
        or receipt.get("scheduler_activated") is not False
    ):
        raise ValueError("v2.1 manual authorization contract is not satisfied")
    authorization_id = str(receipt.get("authorization_id", "")).strip()
    authorized_by = str(receipt.get("authorized_by", "")).strip()
    authorized_at_raw = receipt.get("authorized_at_utc")
    authorized_at: datetime | None = None
    if (
        isinstance(authorized_at_raw, str)
        and authorized_at_raw == authorized_at_raw.strip()
        and (
            authorized_at_raw.endswith("Z")
            or authorized_at_raw.endswith("+00:00")
        )
    ):
        try:
            parsed_authorized_at = datetime.fromisoformat(authorized_at_raw)
        except ValueError:
            pass
        else:
            if (
                parsed_authorized_at.tzinfo is not None
                and parsed_authorized_at.utcoffset() == timedelta(0)
            ):
                authorized_at = parsed_authorized_at.astimezone(UTC)
    if not authorization_id or not authorized_by or authorized_at is None:
        raise ValueError(
            "v2.1 manual authorization identity or explicit UTC timestamp is invalid"
        )
    migration_complete = receipt.get("initial_backlog_migration_complete")
    validation_complete = receipt.get("standard_incremental_validation_complete")
    if not isinstance(migration_complete, bool) or not isinstance(
        validation_complete, bool
    ):
        raise ValueError("v2.1 manual authorization gate states must be booleans")
    if (
        execution_mode == "standard_incremental_validation"
        and migration_complete is not True
    ):
        raise ValueError(
            "v2.1 incremental validation requires completed backlog migration"
        )
    return {
        "authorization_id": authorization_id,
        "authorization_receipt_sha256": _sha256_bytes(payload),
        "authorized_at_utc": authorized_at.isoformat(),
        "authorized_by": authorized_by,
        "execution_mode": execution_mode,
        "initial_backlog_migration_complete": migration_complete,
        "standard_incremental_validation_complete": validation_complete,
        "scheduler_activated": False,
    }


def _v2_execution_authorization(
    config: NewsPollConfig,
    *,
    execution_mode: V2ExecutionMode,
    authorization_receipt_path: Path | None,
) -> JsonObject:
    version = config.document.get("schema_version")
    if version == "p4.1-news-poll-v2":
        if execution_mode != "scheduler" or authorization_receipt_path is not None:
            raise _v2_execution_gate_error(
                config,
                code="superseded_v2_manual_execution_forbidden",
                message="superseded P4.1 v2 has no manual execution path",
                execution_mode=execution_mode,
            )
        if not V2_IMPLEMENTATION_READY:
            gate_started_at = utcnow()
            raise JobExecutionError(
                "P4.1 v2 implementation is not ready (contract is superseded)",
                stats={
                    "config_version": config.document["schema_version"],
                    "config_path": str(config.path.relative_to(PROJECT_DIR)),
                    "config_sha256": config.sha256,
                    "implementation_gate": "prereg_not_ready",
                    "v2_implementation_ready": False,
                    "fail_closed_before_job_context": True,
                    "network_attempted": False,
                    "fetch_started": False,
                    "poll_started_at": gate_started_at.isoformat(),
                    "poll_completed_at": utcnow().isoformat(),
                    "run_mode": "regular_incremental",
                    "coverage_gap": False,
                    "safety_unchanged": True,
                    "sources": {},
                    "p4_2b_production_wiring_unlocked": False,
                    "p4_3_unlocked": False,
                    "terminal_diagnostics": _v2_terminal_diagnostic(
                        code="v2_implementation_not_ready",
                        source="news_poll",
                        constraint="implementation_gate",
                        recoverable=False,
                    ),
                },
            )
        return {"execution_mode": execution_mode, "scheduler_activated": True}
    if version != "p4.1-news-poll-v2.1":
        return {"execution_mode": "scheduler", "scheduler_activated": True}
    if not V2_1_CODE_READY:
        raise _v2_execution_gate_error(
            config,
            code="v2_1_code_not_ready",
            message="P4.1 v2.1 code readiness gate is closed",
            execution_mode=execution_mode,
        )
    if execution_mode == "scheduler":
        if authorization_receipt_path is not None:
            raise _v2_execution_gate_error(
                config,
                code="v2_1_scheduler_receipt_forbidden",
                message="manual authorization cannot activate the scheduler",
                execution_mode=execution_mode,
            )
        if not V2_1_SCHEDULER_ACTIVATED:
            raise _v2_execution_gate_error(
                config,
                code="v2_1_scheduler_not_activated",
                message="P4.1 v2.1 scheduler activation gate is closed",
                execution_mode=execution_mode,
            )
        return {"execution_mode": execution_mode, "scheduler_activated": True}
    if authorization_receipt_path is None:
        raise _v2_execution_gate_error(
            config,
            code="v2_1_manual_authorization_missing",
            message="P4.1 v2.1 manual execution requires an authorization receipt",
            execution_mode=execution_mode,
        )
    try:
        return _load_v2_1_manual_authorization(
            authorization_receipt_path,
            config=config,
            execution_mode=execution_mode,
        )
    except ValueError as exc:
        raise _v2_execution_gate_error(
            config,
            code="v2_1_manual_authorization_invalid",
            message=str(exc),
            execution_mode=execution_mode,
        ) from exc


def _normalize_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_url(value: object) -> str:
    raw = _normalize_text(value)
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise NewsSourceError("invalid_url", "news item URL must be absolute HTTP(S)")
    host = parsed.hostname.lower().rstrip(".")
    port = f":{parsed.port}" if parsed.port else ""
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_QUERY_KEYS
        and not key.lower().startswith(_TRACKING_QUERY_PREFIXES)
    ]
    return urlunparse(
        (
            parsed.scheme.lower(),
            f"{host}{port}",
            re.sub(r"/{2,}", "/", parsed.path) or "/",
            "",
            urlencode(sorted(query)),
            "",
        )
    )


def _normalize_symbol(value: object) -> str | None:
    raw = _normalize_text(value)
    digits = "".join(character for character in raw if character.isdigit())
    return digits if len(digits) == 6 else None


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _published_day(value: datetime | None) -> str | None:
    normalized = _ensure_utc(value)
    return normalized.astimezone(MARKET_TIMEZONE).date().isoformat() if normalized else None


def content_hash(candidate: NewsCandidate) -> str:
    payload = {
        "title": _normalize_text(candidate.title).casefold(),
        "content": _normalize_text(candidate.content).casefold(),
        "symbol": candidate.symbol,
        "published_day_shanghai": _published_day(candidate.published_at),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _source_failure(exc: Exception) -> JsonObject:
    if isinstance(exc, NewsSourceError):
        result: JsonObject = {
            "code": exc.code,
            "blocked": exc.blocked,
            "error_type": type(exc).__name__,
            "message": str(exc)[:300],
        }
        if exc.suppression is not None:
            result["suppression"] = exc.suppression
        return result
    return {
        "code": "unexpected_error",
        "blocked": False,
        "error_type": type(exc).__name__,
        "message": str(exc)[:300],
    }


class _BoundedHttp:
    def __init__(
        self,
        *,
        source_id: str,
        client: Any,
        allowed_hosts: set[str],
        max_requests: int,
        max_attempts: int,
        min_interval_seconds: float,
        retry_backoff_seconds: list[float],
        max_logical_requests: int | None = None,
        max_physical_attempts: int | None = None,
        max_attempts_per_logical_request: int | None = None,
    ) -> None:
        self.source_id = source_id
        self.client = client
        self.allowed_hosts = allowed_hosts
        self.max_requests = max_requests
        self.max_attempts = max_attempts
        self.min_interval_seconds = min_interval_seconds
        self.retry_backoff_seconds = retry_backoff_seconds
        dual_values = (
            max_logical_requests,
            max_physical_attempts,
            max_attempts_per_logical_request,
        )
        if any(value is not None for value in dual_values) and not all(
            value is not None and value > 0 for value in dual_values
        ):
            raise ValueError("v2 HTTP budgets must all be positive")
        self.max_logical_requests = max_logical_requests
        self.max_physical_attempts = max_physical_attempts
        self.max_attempts_per_logical_request = max_attempts_per_logical_request
        self.uses_dual_budget = max_logical_requests is not None
        self.request_count = 0
        self.retry_count = 0
        self.logical_request_count = 0
        self.physical_attempt_count = 0
        self.requests: list[JsonObject] = []
        self._last_started: float | None = None

    def request(self, method: str, url: str, **kwargs: object) -> Any:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        if host not in self.allowed_hosts:
            raise NewsSourceError(
                "forbidden_upstream",
                f"{self.source_id} attempted non-audited host {host or '<missing>'}",
                blocked=True,
            )
        if self.uses_dual_budget:
            return self._request_v2(method, url, host, kwargs)
        return self._request_v1(method, url, host, kwargs)

    def _request_v1(
        self,
        method: str,
        url: str,
        host: str,
        kwargs: dict[str, object],
    ) -> Any:
        last_error: NewsSourceError | None = None
        for attempt in range(1, self.max_attempts + 1):
            if self.request_count >= self.max_requests:
                raise NewsSourceError(
                    "request_budget_exhausted",
                    f"{self.source_id} exceeded request budget {self.max_requests}",
                    blocked=True,
                )
            if self._last_started is not None:
                remaining = self.min_interval_seconds - (monotonic() - self._last_started)
                if remaining > 0:
                    sleep(remaining)
            if attempt > 1:
                self.retry_count += 1
                backoff_index = min(attempt - 2, len(self.retry_backoff_seconds) - 1)
                if backoff_index >= 0:
                    sleep(self.retry_backoff_seconds[backoff_index])

            requested_at = utcnow()
            started = monotonic()
            self._last_started = started
            self.request_count += 1
            evidence: JsonObject = {
                "attempt": attempt,
                "method": method.upper(),
                "host": host,
                "path": urlparse(url).path,
                "requested_at": requested_at.isoformat(),
                "received_at": None,
                "latency_ms": None,
                "http_status": None,
                "failure_code": None,
            }
            self.requests.append(evidence)
            try:
                response = self.client.request(method, url, **kwargs)
                read = getattr(response, "read", None)
                if callable(read):
                    read()
            except httpx.TimeoutException as exc:
                last_error = NewsSourceError("transport_timeout", type(exc).__name__)
            except httpx.TransportError as exc:
                last_error = NewsSourceError("transport_error", type(exc).__name__)
            except Exception as exc:
                last_error = NewsSourceError("client_error", type(exc).__name__)
            else:
                status = int(getattr(response, "status_code", 0))
                evidence["http_status"] = status
                if status in {403, 429}:
                    evidence["failure_code"] = (
                        "http_forbidden_or_antibot" if status == 403 else "http_rate_limited"
                    )
                    raise NewsSourceError(
                        str(evidence["failure_code"]),
                        f"HTTP {status}",
                        blocked=True,
                    )
                if status >= 500:
                    last_error = NewsSourceError("http_server_error", f"HTTP {status}")
                elif status >= 400:
                    evidence["failure_code"] = "http_client_error"
                    raise NewsSourceError("http_client_error", f"HTTP {status}")
                elif 300 <= status < 400:
                    evidence["failure_code"] = "redirect_not_followed"
                    raise NewsSourceError("redirect_not_followed", f"HTTP {status}")
                else:
                    evidence["received_at"] = utcnow().isoformat()
                    evidence["latency_ms"] = round((monotonic() - started) * 1000, 3)
                    return response

            assert last_error is not None
            evidence["received_at"] = utcnow().isoformat()
            evidence["latency_ms"] = round((monotonic() - started) * 1000, 3)
            evidence["failure_code"] = last_error.code
            if attempt >= self.max_attempts:
                raise last_error
        raise RuntimeError("unreachable request retry state")

    def _request_v2(
        self,
        method: str,
        url: str,
        host: str,
        kwargs: dict[str, object],
    ) -> Any:
        assert self.max_logical_requests is not None
        assert self.max_physical_attempts is not None
        assert self.max_attempts_per_logical_request is not None
        if self.logical_request_count >= self.max_logical_requests:
            raise NewsSourceError(
                "logical_request_budget_exhausted",
                (
                    f"{self.source_id} exceeded logical request budget "
                    f"{self.max_logical_requests}"
                ),
                blocked=True,
            )
        # A logical operation is only accepted when its first physical attempt
        # can cross the network boundary.  This keeps the frozen accounting
        # identity ``retry_count = physical_attempt_count - logical_request_count``
        # non-negative even at an exhausted physical-attempt boundary.
        if self.physical_attempt_count >= self.max_physical_attempts:
            raise NewsSourceError(
                "physical_attempt_budget_exhausted",
                (
                    f"{self.source_id} exceeded physical attempt budget "
                    f"{self.max_physical_attempts}"
                ),
                blocked=True,
            )
        self.logical_request_count += 1
        logical_request = self.logical_request_count
        last_error: NewsSourceError | None = None
        for attempt in range(1, self.max_attempts_per_logical_request + 1):
            if self.physical_attempt_count >= self.max_physical_attempts:
                if attempt > 1 and last_error is not None:
                    suppression: JsonObject = {
                        "code": "retry_suppressed_physical_attempt_budget",
                        "constraint": "max_physical_attempts_per_run",
                        "source_id": self.source_id,
                        "logical_request_count": self.logical_request_count,
                        "physical_attempt_count": self.physical_attempt_count,
                        "max_physical_attempts": self.max_physical_attempts,
                        "retry_suppressed": True,
                    }
                    if self.requests:
                        self.requests[-1]["retry_suppression"] = suppression
                    raise NewsSourceError(
                        last_error.code,
                        str(last_error),
                        blocked=last_error.blocked,
                        suppression=suppression,
                    )
                raise NewsSourceError(
                    "physical_attempt_budget_exhausted",
                    (
                        f"{self.source_id} exceeded physical attempt budget "
                        f"{self.max_physical_attempts}"
                    ),
                    blocked=True,
                )
            if self._last_started is not None:
                remaining = self.min_interval_seconds - (monotonic() - self._last_started)
                if remaining > 0:
                    sleep(remaining)
            if attempt > 1:
                backoff_index = min(attempt - 2, len(self.retry_backoff_seconds) - 1)
                if backoff_index >= 0:
                    sleep(self.retry_backoff_seconds[backoff_index])

            requested_at = utcnow()
            started = monotonic()
            self._last_started = started
            # Count only an attempt that is immediately about to cross the
            # network boundary.  A budget-suppressed retry never reaches here.
            self.physical_attempt_count += 1
            self.request_count += 1
            if attempt > 1:
                self.retry_count += 1
            evidence: JsonObject = {
                "logical_request": logical_request,
                "attempt": attempt,
                "method": method.upper(),
                "host": host,
                "path": urlparse(url).path,
                "requested_at": requested_at.isoformat(),
                "received_at": None,
                "latency_ms": None,
                "http_status": None,
                "failure_code": None,
            }
            self.requests.append(evidence)
            try:
                response = self.client.request(method, url, **kwargs)
                read = getattr(response, "read", None)
                if callable(read):
                    read()
            except httpx.TimeoutException as exc:
                last_error = NewsSourceError("transport_timeout", type(exc).__name__)
            except httpx.TransportError as exc:
                last_error = NewsSourceError("transport_error", type(exc).__name__)
            except Exception as exc:
                last_error = NewsSourceError("client_error", type(exc).__name__)
            else:
                status = int(getattr(response, "status_code", 0))
                evidence["http_status"] = status
                if status in {403, 429}:
                    evidence["failure_code"] = (
                        "http_forbidden_or_antibot" if status == 403 else "http_rate_limited"
                    )
                    raise NewsSourceError(
                        str(evidence["failure_code"]),
                        f"HTTP {status}",
                        blocked=True,
                    )
                if status >= 500:
                    last_error = NewsSourceError("http_server_error", f"HTTP {status}")
                elif status >= 400:
                    evidence["failure_code"] = "http_client_error"
                    raise NewsSourceError("http_client_error", f"HTTP {status}")
                elif 300 <= status < 400:
                    evidence["failure_code"] = "redirect_not_followed"
                    raise NewsSourceError("redirect_not_followed", f"HTTP {status}")
                else:
                    evidence["received_at"] = utcnow().isoformat()
                    evidence["latency_ms"] = round((monotonic() - started) * 1000, 3)
                    return response

            assert last_error is not None
            evidence["received_at"] = utcnow().isoformat()
            evidence["latency_ms"] = round((monotonic() - started) * 1000, 3)
            evidence["failure_code"] = last_error.code
            if attempt >= self.max_attempts_per_logical_request:
                raise last_error
        raise RuntimeError("unreachable v2 request retry state")


def _copy_transport_counts(batch: SourceBatch, transport: _BoundedHttp) -> None:
    batch.request_count = transport.request_count
    batch.retry_count = transport.retry_count
    if transport.uses_dual_budget:
        batch.logical_request_count = transport.logical_request_count
        batch.physical_attempt_count = transport.physical_attempt_count


def _zero_request_counter_stats(config: NewsPollConfig) -> JsonObject:
    result: JsonObject = {"request_count": 0, "retry_count": 0}
    if _is_v2_config(config):
        result.update({"logical_request_count": 0, "physical_attempt_count": 0})
    return result


def _new_http_client(config: NewsPollConfig) -> httpx.Client:
    network = cast(dict[str, Any], config.document["network"])
    timeout = httpx.Timeout(
        connect=float(network["connect_timeout_seconds"]),
        read=float(network["read_timeout_seconds"]),
        write=float(network["write_timeout_seconds"]),
        pool=float(network["pool_timeout_seconds"]),
    )
    return httpx.Client(
        timeout=timeout,
        trust_env=False,
        follow_redirects=False,
        verify=True,
        headers={"User-Agent": str(network["user_agent"])},
    )


def _transport(
    config: NewsPollConfig,
    source_id: str,
    source: Mapping[str, object],
    client: Any,
) -> _BoundedHttp:
    network = cast(dict[str, Any], config.document["network"])
    if _is_v2_config(config):
        max_logical_requests = source.get("max_logical_requests_per_run")
        max_physical_attempts = source.get("max_physical_attempts_per_run")
        max_attempts_per_logical_request = source.get(
            "max_attempts_per_logical_request"
        )
        if (
            max_logical_requests is None
            or max_physical_attempts is None
            or max_attempts_per_logical_request is None
        ):
            raise ValueError(f"{source_id} v2 request budget contract is missing")
        return _BoundedHttp(
            source_id=source_id,
            client=client,
            allowed_hosts={
                str(host).lower() for host in cast(list[str], source["allowed_hosts"])
            },
            # These compatibility attributes are not used for v2 decisions;
            # request_count continues to mirror physical attempts for callers
            # that have not yet migrated their aggregate totals.
            max_requests=int(str(max_physical_attempts)),
            max_attempts=int(str(max_attempts_per_logical_request)),
            max_logical_requests=int(str(max_logical_requests)),
            max_physical_attempts=int(str(max_physical_attempts)),
            max_attempts_per_logical_request=int(
                str(max_attempts_per_logical_request)
            ),
            min_interval_seconds=float(str(source["min_interval_seconds"])),
            retry_backoff_seconds=[
                float(str(item))
                for item in cast(
                    list[object],
                    source.get(
                        "retry_backoff_seconds", network["retry_backoff_seconds"]
                    ),
                )
            ],
        )
    return _BoundedHttp(
        source_id=source_id,
        client=client,
        allowed_hosts={str(host).lower() for host in cast(list[str], source["allowed_hosts"])},
        max_requests=int(str(source["max_requests_per_run"])),
        max_attempts=int(
            str(
                source.get(
                    "max_attempts_per_request",
                    network["max_attempts_per_request"],
                )
            )
        ),
        min_interval_seconds=float(str(source["min_interval_seconds"])),
        retry_backoff_seconds=[float(item) for item in network["retry_backoff_seconds"]],
    )


def _decode_json(response: Any) -> object:
    try:
        return response.json()
    except Exception as exc:
        raise NewsSourceError("decode_error", type(exc).__name__) from exc


def _last_successful_watermark(source_id: str) -> datetime | None:
    with get_session() as session:
        rows = session.scalars(
            select(JobRun)
            .where(JobRun.job_name == "news_poll", JobRun.status == "ok")
            .order_by(JobRun.id.desc())
            .limit(100)
        ).all()
    for row in rows:
        stats = row.stats if isinstance(row.stats, dict) else {}
        sources = stats.get("sources")
        source = sources.get(source_id) if isinstance(sources, dict) else None
        raw = source.get("watermark_after") if isinstance(source, dict) else None
        if not isinstance(raw, str):
            continue
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            continue
        return _ensure_utc(parsed)
    return None


def _parsed_utc_watermark(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _ensure_utc(parsed)


def _last_committed_column_watermarks(
    config: NewsPollConfig,
    source_id: str,
    columns: list[str],
) -> dict[str, datetime | None]:
    """Return the newest independently committed v2 checkpoint per column.

    A v2 checkpoint is trusted only from an ``ok``/``degraded`` JobRun and
    only when that column explicitly records ``checkpoint_committed=true``.
    The newest successful v1 global checkpoint may seed a column exactly once:
    it is considered only when no committed v2 checkpoint exists for that
    column.
    """

    if config.document.get("schema_version") != "p4.1-news-poll-v2":
        raise ValueError("per-column watermarks require the P4.1 v2 config")
    with get_session() as session:
        rows = session.scalars(
            select(JobRun)
            .where(JobRun.job_name == "news_poll")
            .order_by(JobRun.id.desc())
        ).all()

    resolved: dict[str, datetime | None] = dict.fromkeys(columns)
    legacy_global: datetime | None = None
    for row in rows:
        stats = row.stats if isinstance(row.stats, dict) else {}
        sources = stats.get("sources")
        source = sources.get(source_id) if isinstance(sources, dict) else None
        if not isinstance(source, dict):
            continue

        v2_lineage_matches = (
            stats.get("config_version") == "p4.1-news-poll-v2"
            and stats.get("config_sha256") == EXPECTED_V2_CONFIG_SHA256
        )
        if v2_lineage_matches and row.status in {"ok", "degraded"}:
            column_watermarks = source.get("column_watermarks")
            if isinstance(column_watermarks, dict):
                for column in columns:
                    if resolved[column] is not None:
                        continue
                    checkpoint = column_watermarks.get(column)
                    if (
                        not isinstance(checkpoint, dict)
                        or checkpoint.get("checkpoint_committed") is not True
                    ):
                        continue
                    parsed = _parsed_utc_watermark(
                        checkpoint.get("verified_watermark_after_utc")
                    )
                    if parsed is not None:
                        resolved[column] = parsed

        if (
            legacy_global is None
            and row.status == "ok"
            and stats.get("config_version") == "p4.1-news-poll-v1"
            and stats.get("config_sha256") == EXPECTED_CONFIG_SHA256
        ):
            legacy_global = _parsed_utc_watermark(source.get("watermark_after"))

        if all(value is not None for value in resolved.values()):
            break

    if legacy_global is not None:
        for column in columns:
            if resolved[column] is None:
                resolved[column] = legacy_global
    return resolved


def _last_committed_daily_checkpoint(
    config: NewsPollConfig,
    source_id: str,
) -> DailyCheckpointSeed:
    """Load only a hash-bound v2.1 daily checkpoint or the exact v1 seed."""

    if config.document.get("schema_version") != "p4.1-news-poll-v2.1":
        raise ValueError("daily checkpoints require the P4.1 v2.1 config")
    with get_session() as session:
        rows = session.scalars(
            select(JobRun)
            .where(JobRun.job_name == "news_poll")
            .order_by(JobRun.id.desc())
        ).all()

    legacy_watermark: datetime | None = None
    for row in rows:
        stats = row.stats if isinstance(row.stats, dict) else {}
        sources = stats.get("sources")
        source = sources.get(source_id) if isinstance(sources, dict) else None
        if not isinstance(source, dict):
            continue
        if (
            stats.get("config_version") == "p4.1-news-poll-v2.1"
            and stats.get("config_sha256") == EXPECTED_V2_1_CONFIG_SHA256
            and row.status in {"ok", "degraded"}
        ):
            checkpoint = source.get("daily_checkpoint")
            if isinstance(checkpoint, dict) and checkpoint.get("checkpoint_committed") is True:
                raw_date = checkpoint.get("verified_checkpoint_date_shanghai_after")
                observed = _parsed_utc_watermark(checkpoint.get("newest_observed_at_utc"))
                try:
                    parsed_date = (
                        date.fromisoformat(str(raw_date)) if raw_date is not None else None
                    )
                except ValueError:
                    continue
                if observed is None:
                    continue
                if (
                    parsed_date is not None
                    and observed.astimezone(MARKET_TIMEZONE).date() < parsed_date
                ):
                    # A current-day incremental run legitimately leaves the last
                    # closed checkpoint behind its observed high.  The inverse
                    # relationship is impossible and cannot seed a later run.
                    continue
                return DailyCheckpointSeed(
                    checkpoint_date_shanghai=parsed_date,
                    newest_observed_at_utc=observed,
                    legacy_watermark_utc=(observed if parsed_date is None else None),
                    lineage="v2.1_daily_checkpoint",
                )
        if (
            legacy_watermark is None
            and row.status == "ok"
            and stats.get("config_version") == "p4.1-news-poll-v1"
            and stats.get("config_sha256") == EXPECTED_CONFIG_SHA256
        ):
            legacy_watermark = _parsed_utc_watermark(source.get("watermark_after"))

    return DailyCheckpointSeed(
        checkpoint_date_shanghai=None,
        newest_observed_at_utc=legacy_watermark,
        legacy_watermark_utc=legacy_watermark,
        lineage=("legacy_v1_global_watermark" if legacy_watermark is not None else "missing"),
    )


def _parse_cninfo_timestamp(value: object) -> datetime | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _fetch_cninfo_v1(
    config: NewsPollConfig,
    now: datetime,
    client_factory: HttpClientFactory | None,
) -> SourceBatch:
    source = cast(dict[str, Any], cast(dict[str, Any], config.document["sources"])["cninfo"])
    source_id = "cninfo"
    client = client_factory(source_id) if client_factory else _new_http_client(config)
    transport = _transport(config, source_id, source, client)
    prior_watermark = _last_successful_watermark(source_id)
    if prior_watermark is None:
        prior_watermark = now - timedelta(minutes=int(source["bootstrap_lookback_minutes"]))
    floor = prior_watermark - timedelta(minutes=int(source["watermark_overlap_minutes"]))
    batch = SourceBatch(source_id=source_id)
    newest_seen = prior_watermark
    columns_complete: dict[str, bool] = {}
    try:
        for column in cast(list[str], source["columns"]):
            complete = False
            for page in range(1, int(source["max_pages_per_column"]) + 1):
                response = transport.request(
                    "POST",
                    str(source["announcements_url"]),
                    data={
                        "pageNum": page,
                        "pageSize": int(source["page_size"]),
                        "column": column,
                        "tabName": "fulltext",
                        "stock": "",
                        "seDate": f"{floor.date().isoformat()}~{now.date().isoformat()}",
                        "isHLtitle": "false",
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                payload = _decode_json(response)
                if not isinstance(payload, dict):
                    raise NewsSourceError("schema_changed", "CNInfo response is not an object")
                rows = payload.get("announcements") or []
                if not isinstance(rows, list):
                    raise NewsSourceError("schema_changed", "CNInfo announcements is not a list")
                page_times: list[datetime] = []
                for raw in rows:
                    if not isinstance(raw, dict):
                        continue
                    title = _normalize_text(raw.get("announcementTitle"))
                    adjunct = _normalize_text(raw.get("adjunctUrl"))
                    if not title or not adjunct:
                        continue
                    published_at = _parse_cninfo_timestamp(raw.get("announcementTime"))
                    if published_at is not None:
                        page_times.append(published_at)
                        newest_seen = max(newest_seen, published_at)
                        if published_at < floor:
                            continue
                    batch.candidates.append(
                        NewsCandidate(
                            source=source_id,
                            symbol=_normalize_symbol(raw.get("secCode")),
                            title=title,
                            url=urljoin(str(source["static_url_prefix"]), adjunct),
                            published_at=published_at,
                            content="",
                            raw_payload=cast(JsonObject, _json_safe(raw)),
                        )
                    )
                if not rows or payload.get("hasMore") is False:
                    complete = True
                    break
                if page_times and min(page_times) <= floor:
                    complete = True
                    break
            columns_complete[column] = complete
            if not complete:
                raise NewsSourceError(
                    "pagination_incomplete",
                    f"CNInfo {column} exceeded the pre-registered page cap",
                )
    except Exception as exc:
        failure = _source_failure(exc)
        batch.status = "unavailable"
        batch.failures.append(failure)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    _copy_transport_counts(batch, transport)
    batch.details = {
        "watermark_before": prior_watermark.isoformat(),
        "watermark_floor": floor.isoformat(),
        "watermark_after": newest_seen.isoformat(),
        "columns_complete": columns_complete,
        "requests": transport.requests,
        "tls_verification": True,
    }
    return batch


def _fetch_cninfo_v2(
    config: NewsPollConfig,
    now: datetime,
    client_factory: HttpClientFactory | None,
) -> SourceBatch:
    source = cast(dict[str, Any], cast(dict[str, Any], config.document["sources"])["cninfo"])
    source_id = "cninfo"
    client = client_factory(source_id) if client_factory else _new_http_client(config)
    transport = _transport(config, source_id, source, client)
    columns = [str(column) for column in cast(list[object], source["columns"])]
    prior_by_column = _last_committed_column_watermarks(
        config,
        source_id,
        columns,
    )
    poll_started_at_utc = _ensure_utc(now)
    assert poll_started_at_utc is not None
    market_date_at_poll = poll_started_at_utc.astimezone(MARKET_TIMEZONE).date()
    query_end_date_shanghai = market_date_at_poll.isoformat()
    bootstrap = poll_started_at_utc - timedelta(
        minutes=int(source["bootstrap_lookback_minutes"])
    )
    overlap = timedelta(minutes=int(source["watermark_overlap_minutes"]))
    batch = SourceBatch(source_id=source_id)
    column_watermarks: dict[str, JsonObject] = {}
    query_start_dates_shanghai: dict[str, str] = {}
    prior_critical_failure = False
    try:
        for column in columns:
            before = prior_by_column[column] or bootstrap
            floor = before - overlap
            query_start_date_shanghai = floor.astimezone(MARKET_TIMEZONE).date().isoformat()
            query_start_dates_shanghai[column] = query_start_date_shanghai
            if prior_critical_failure:
                column_watermarks[column] = {
                    "verified_watermark_before_utc": before.isoformat(),
                    "verified_watermark_floor_utc": floor.isoformat(),
                    "newest_observed_at_utc": before.isoformat(),
                    "verified_watermark_after_utc": before.isoformat(),
                    "pagination_complete": False,
                    "checkpoint_committed": False,
                    "page_cap_hit": False,
                    "attempted": False,
                    "skipped_due_to_prior_critical_failure": True,
                }
                continue
            newest_observed = before
            pagination_complete = False
            page_cap_hit = False
            column_failed = False
            try:
                for page in range(1, int(source["max_pages_per_column"]) + 1):
                    response = transport.request(
                        "POST",
                        str(source["announcements_url"]),
                        data={
                            "pageNum": page,
                            "pageSize": int(source["page_size"]),
                            "column": column,
                            "tabName": "fulltext",
                            "stock": "",
                            "seDate": (
                                f"{query_start_date_shanghai}~"
                                f"{query_end_date_shanghai}"
                            ),
                            "isHLtitle": "false",
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    payload = _decode_json(response)
                    if not isinstance(payload, dict):
                        raise NewsSourceError(
                            "schema_changed", "CNInfo response is not an object"
                        )
                    rows = payload.get("announcements") or []
                    if not isinstance(rows, list):
                        raise NewsSourceError(
                            "schema_changed", "CNInfo announcements is not a list"
                        )
                    page_times: list[datetime] = []
                    for raw in rows:
                        if not isinstance(raw, dict):
                            continue
                        title = _normalize_text(raw.get("announcementTitle"))
                        adjunct = _normalize_text(raw.get("adjunctUrl"))
                        if not title or not adjunct:
                            continue
                        published_at = _parse_cninfo_timestamp(raw.get("announcementTime"))
                        if published_at is not None:
                            page_times.append(published_at)
                            newest_observed = max(newest_observed, published_at)
                            if published_at < floor:
                                continue
                        batch.candidates.append(
                            NewsCandidate(
                                source=source_id,
                                symbol=_normalize_symbol(raw.get("secCode")),
                                title=title,
                                url=urljoin(str(source["static_url_prefix"]), adjunct),
                                published_at=published_at,
                                content="",
                                raw_payload=cast(JsonObject, _json_safe(raw)),
                            )
                        )
                    if not rows or payload.get("hasMore") is False:
                        pagination_complete = True
                        break
                    if page_times and min(page_times) <= floor:
                        pagination_complete = True
                        break
                else:
                    page_cap_hit = True
            except Exception as exc:
                column_failed = True
                prior_critical_failure = True
                failure = {**_source_failure(exc), "column": column}
                batch.failures.append(failure)
                batch.status = "unavailable"

            checkpoint_committed = pagination_complete and not column_failed
            after = newest_observed if checkpoint_committed else before
            column_watermarks[column] = {
                "verified_watermark_before_utc": before.isoformat(),
                "verified_watermark_floor_utc": floor.isoformat(),
                "newest_observed_at_utc": newest_observed.isoformat(),
                "verified_watermark_after_utc": after.isoformat(),
                "pagination_complete": pagination_complete,
                "checkpoint_committed": checkpoint_committed,
                "page_cap_hit": page_cap_hit,
                "attempted": True,
                "skipped_due_to_prior_critical_failure": False,
            }
            if page_cap_hit:
                batch.failures.append(
                    {
                        "code": "pagination_incomplete",
                        "blocked": False,
                        "error_type": "NewsSourceError",
                        "message": (
                            f"CNInfo {column} exceeded the pre-registered page cap"
                        ),
                        "column": column,
                    }
                )
                if batch.status == "ok":
                    batch.status = "degraded"
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    _copy_transport_counts(batch, transport)
    batch.details = {
        "column_watermarks": column_watermarks,
        "query_start_date_shanghai": query_start_dates_shanghai,
        "query_end_date_shanghai": query_end_date_shanghai,
        "market_date_at_poll": market_date_at_poll.isoformat(),
        "poll_started_at_utc": poll_started_at_utc.isoformat(),
        "requests": transport.requests,
        "tls_verification": True,
    }
    return batch


def _v2_1_slice_dates(
    seed: DailyCheckpointSeed,
    *,
    poll_started_at_utc: datetime,
    max_dates_per_run: int,
) -> list[date]:
    if max_dates_per_run <= 0:
        raise ValueError("v2.1 max_dates_per_run must be positive")
    market_date = poll_started_at_utc.astimezone(MARKET_TIMEZONE).date()
    if seed.checkpoint_date_shanghai is not None:
        first = seed.checkpoint_date_shanghai + timedelta(days=1)
    elif seed.legacy_watermark_utc is not None:
        first = seed.legacy_watermark_utc.astimezone(MARKET_TIMEZONE).date()
    else:
        raise NewsSourceError(
            "daily_checkpoint_unavailable",
            "CNInfo v2.1 has neither a verified daily checkpoint nor the v1 migration seed",
            blocked=True,
        )
    if first > market_date:
        raise NewsSourceError(
            "daily_checkpoint_ahead_of_market_date",
            "CNInfo v2.1 checkpoint is ahead of the Shanghai market date",
            blocked=True,
        )
    result: list[date] = []
    current = first
    while current <= market_date and len(result) < max_dates_per_run:
        result.append(current)
        current += timedelta(days=1)
    return result


def _fetch_cninfo_v2_1(
    config: NewsPollConfig,
    now: datetime,
    client_factory: HttpClientFactory | None,
) -> SourceBatch:
    """Fetch one canonical CNInfo column in bounded Shanghai natural-day slices."""

    source = cast(dict[str, Any], cast(dict[str, Any], config.document["sources"])["cninfo"])
    source_id = "cninfo"
    canonical_column = str(source.get("canonical_column"))
    if canonical_column != "szse" or source.get("columns") != ["szse"]:
        raise NewsSourceError(
            "canonical_column_contract_drifted",
            "CNInfo v2.1 must issue only the canonical szse query",
            blocked=True,
        )
    poll_started_at_utc = _ensure_utc(now)
    assert poll_started_at_utc is not None
    seed = _last_committed_daily_checkpoint(config, source_id)
    max_dates = int(source["max_dates_per_run"])
    slice_dates = _v2_1_slice_dates(
        seed,
        poll_started_at_utc=poll_started_at_utc,
        max_dates_per_run=max_dates,
    )
    market_date = poll_started_at_utc.astimezone(MARKET_TIMEZONE).date()
    client = client_factory(source_id) if client_factory else _new_http_client(config)
    transport = _transport(config, source_id, source, client)
    batch = SourceBatch(source_id=source_id)
    slices: list[JsonObject] = []
    checkpoint_before = seed.checkpoint_date_shanghai
    checkpoint_after = checkpoint_before
    committed_observed = seed.newest_observed_at_utc
    latest_attempt_observed = seed.newest_observed_at_utc
    any_checkpoint_committed = False
    try:
        for slice_date in slice_dates:
            logical_before = transport.logical_request_count
            physical_before = transport.physical_attempt_count
            candidates_before = len(batch.candidates)
            newest_in_slice: datetime | None = None
            incremental_floor = (
                committed_observed
                - timedelta(minutes=int(source["watermark_overlap_minutes"]))
                if slice_date == market_date and committed_observed is not None
                else None
            )
            pagination_complete = False
            page_cap_hit = False
            failure: JsonObject | None = None
            page_count = 0
            previous_page_min: datetime | None = None
            try:
                for page in range(1, int(source["max_pages_per_day"]) + 1):
                    response = transport.request(
                        "POST",
                        str(source["announcements_url"]),
                        data={
                            "pageNum": page,
                            "pageSize": int(source["page_size"]),
                            "column": canonical_column,
                            "tabName": "fulltext",
                            "stock": "",
                            "seDate": f"{slice_date.isoformat()}~{slice_date.isoformat()}",
                            "sortName": str(source["sort_name"]),
                            "sortType": str(source["sort_type"]),
                            "isHLtitle": "false",
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    page_count += 1
                    payload = _decode_json(response)
                    if not isinstance(payload, dict):
                        raise NewsSourceError(
                            "schema_changed", "CNInfo response is not an object"
                        )
                    rows = payload.get("announcements") or []
                    if not isinstance(rows, list):
                        raise NewsSourceError(
                            "schema_changed", "CNInfo announcements is not a list"
                        )
                    page_times: list[datetime] = []
                    page_candidates: list[NewsCandidate] = []
                    for raw in rows:
                        if not isinstance(raw, dict):
                            raise NewsSourceError(
                                "schema_changed",
                                "CNInfo v2.1 announcement row must be an object",
                            )
                        title = _normalize_text(raw.get("announcementTitle"))
                        adjunct = _normalize_text(raw.get("adjunctUrl"))
                        raw_published_at = raw.get("announcementTime")
                        published_at = _parse_cninfo_timestamp(raw_published_at)
                        if (
                            raw_published_at is not None
                            and raw_published_at != ""
                            and published_at is None
                        ):
                            raise NewsSourceError(
                                "schema_changed",
                                "CNInfo v2.1 non-null announcementTime must be parseable",
                            )
                        if (
                            published_at is not None
                            and published_at.astimezone(MARKET_TIMEZONE).date()
                            != slice_date
                        ):
                            raise NewsSourceError(
                                "cninfo_slice_date_contract_violated",
                                "CNInfo announcementTime falls outside the requested CST date",
                            )
                        if published_at is not None:
                            page_times.append(published_at)
                        if not title or not adjunct:
                            continue
                        page_candidates.append(
                            NewsCandidate(
                                source=source_id,
                                symbol=_normalize_symbol(raw.get("secCode")),
                                title=title,
                                url=urljoin(str(source["static_url_prefix"]), adjunct),
                                published_at=published_at,
                                content="",
                                raw_payload=cast(JsonObject, _json_safe(raw)),
                            )
                        )
                    if any(
                        later > earlier
                        for earlier, later in pairwise(page_times)
                    ):
                        raise NewsSourceError(
                            "cninfo_order_contract_violated",
                            "CNInfo v2.1 page is not timestamp-descending",
                        )
                    if (
                        previous_page_min is not None
                        and page_times
                        and max(page_times) > previous_page_min
                    ):
                        raise NewsSourceError(
                            "cninfo_order_contract_violated",
                            "CNInfo v2.1 cross-page timestamps are not descending",
                        )
                    if page_times:
                        previous_page_min = min(page_times)
                        page_newest = max(page_times)
                        newest_in_slice = (
                            page_newest
                            if newest_in_slice is None
                            else max(newest_in_slice, page_newest)
                        )
                        latest_attempt_observed = (
                            page_newest
                            if latest_attempt_observed is None
                            else max(latest_attempt_observed, page_newest)
                        )
                    batch.candidates.extend(page_candidates)
                    current_floor_reached = (
                        incremental_floor is not None
                        and bool(page_times)
                        and min(page_times) <= incremental_floor
                    )
                    if (
                        not rows
                        or payload.get("hasMore") is False
                        or len(rows) < int(source["page_size"])
                        or current_floor_reached
                    ):
                        pagination_complete = True
                        break
                else:
                    page_cap_hit = True
            except Exception as exc:
                failure = {**_source_failure(exc), "date_shanghai": slice_date.isoformat()}
                batch.failures.append(failure)
                batch.status = "unavailable"

            date_closed = slice_date < market_date
            checkpoint_committed = pagination_complete and failure is None
            if checkpoint_committed:
                if date_closed:
                    checkpoint_after = slice_date
                any_checkpoint_committed = True
                if newest_in_slice is not None:
                    committed_observed = (
                        newest_in_slice
                        if committed_observed is None
                        else max(committed_observed, newest_in_slice)
                    )
            slice_stats: JsonObject = {
                "date_shanghai": slice_date.isoformat(),
                "date_closed": date_closed,
                "mode": (
                    "closed_date_reconciliation"
                    if date_closed
                    else "current_date_incremental"
                ),
                "incremental_floor_utc": (
                    incremental_floor.isoformat()
                    if incremental_floor is not None
                    else None
                ),
                "attempted": True,
                "page_count": page_count,
                "logical_request_count": (
                    transport.logical_request_count - logical_before
                ),
                "physical_attempt_count": (
                    transport.physical_attempt_count - physical_before
                ),
                "fetched": len(batch.candidates) - candidates_before,
                "newest_observed_at_utc": (
                    newest_in_slice.isoformat() if newest_in_slice is not None else None
                ),
                "pagination_complete": pagination_complete,
                "coverage_proven": pagination_complete,
                "checkpoint_committed": checkpoint_committed,
                "page_cap_hit": page_cap_hit,
                "failure": (
                    _safe_v2_failure(failure) if failure is not None else None
                ),
            }
            slices.append(slice_stats)
            if page_cap_hit:
                batch.failures.append(
                    {
                        "code": "pagination_incomplete",
                        "blocked": False,
                        "error_type": "NewsSourceError",
                        "date_shanghai": slice_date.isoformat(),
                    }
                )
                if batch.status == "ok":
                    batch.status = "degraded"
            if failure is not None or page_cap_hit:
                break
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    _copy_transport_counts(batch, transport)
    batch.details = {
        "canonical_column": canonical_column,
        "slice_dates_shanghai": [item.isoformat() for item in slice_dates],
        "slices": slices,
        "request_budget": {
            "page_size": int(source["page_size"]),
            "max_pages_per_day": int(source["max_pages_per_day"]),
            "max_dates_per_run": max_dates,
            "max_logical_requests_per_run": int(source["max_logical_requests_per_run"]),
            "max_physical_attempts_per_run": int(
                source["max_physical_attempts_per_run"]
            ),
            "logical_request_count": transport.logical_request_count,
            "physical_attempt_count": transport.physical_attempt_count,
        },
        "daily_checkpoint": {
            "lineage_before": seed.lineage,
            "verified_checkpoint_date_shanghai_before": (
                checkpoint_before.isoformat() if checkpoint_before is not None else None
            ),
            "verified_checkpoint_date_shanghai_after": (
                checkpoint_after.isoformat() if checkpoint_after is not None else None
            ),
            "newest_observed_at_utc": (
                committed_observed.isoformat() if committed_observed is not None else None
            ),
            "latest_attempt_observed_at_utc": (
                latest_attempt_observed.isoformat()
                if latest_attempt_observed is not None
                else None
            ),
            "checkpoint_committed": any_checkpoint_committed,
            "partial_checkpoint": any(
                item["checkpoint_committed"] is not True for item in slices
            ),
            "initial_backlog_migration": seed.lineage == "legacy_v1_global_watermark",
        },
        "poll_started_at_utc": poll_started_at_utc.isoformat(),
        "market_date_at_poll": market_date.isoformat(),
        "requests": transport.requests,
        "tls_verification": True,
    }
    return batch


def _fetch_cninfo(
    config: NewsPollConfig,
    now: datetime,
    client_factory: HttpClientFactory | None,
) -> SourceBatch:
    if config.document.get("schema_version") == "p4.1-news-poll-v2.1":
        return _fetch_cninfo_v2_1(config, now, client_factory)
    if config.document.get("schema_version") == "p4.1-news-poll-v2":
        return _fetch_cninfo_v2(config, now, client_factory)
    return _fetch_cninfo_v1(config, now, client_factory)


def _parse_epoch(value: object) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(str(value)), tz=UTC)
    except (OverflowError, OSError, TypeError, ValueError):
        return None


def _load_security_names(*, watchlist_only: bool) -> list[tuple[str, str]]:
    with get_session() as session:
        if watchlist_only:
            rows = session.execute(
                select(WatchlistItem.symbol, Security.name)
                .outerjoin(Security, Security.symbol == WatchlistItem.symbol)
                .order_by(WatchlistItem.symbol)
            ).all()
        else:
            rows = session.execute(
                select(Security.symbol, Security.name).order_by(Security.symbol)
            ).all()
    return [
        (str(symbol), _normalize_text(name))
        for symbol, name in rows
        if _normalize_symbol(symbol) is not None
    ]


def _explicit_unique_symbol(text: str, securities: list[tuple[str, str]]) -> str | None:
    normalized = _normalize_text(text)
    matches: set[str] = set()
    explicit_codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", normalized))
    for symbol, name in securities:
        if symbol in explicit_codes or (len(name) >= 3 and name in normalized):
            matches.add(symbol)
            if len(matches) > 1:
                return None
    return next(iter(matches)) if len(matches) == 1 else None


def _fetch_ths_v1(
    config: NewsPollConfig,
    _now: datetime,
    client_factory: HttpClientFactory | None,
) -> SourceBatch:
    source = cast(
        dict[str, Any],
        cast(dict[str, Any], config.document["sources"])["akshare_ths"],
    )
    source_id = "akshare_ths"
    client = client_factory(source_id) if client_factory else _new_http_client(config)
    transport = _transport(config, source_id, source, client)
    batch = SourceBatch(source_id=source_id)
    securities = _load_security_names(watchlist_only=False)
    try:
        response = transport.request(
            "GET",
            str(source["url"]),
            params={"page": "1", "tag": "", "track": "website"},
        )
        payload = _decode_json(response)
        if not isinstance(payload, dict):
            raise NewsSourceError("schema_changed", "THS response is not an object")
        data = payload.get("data")
        rows = data.get("list") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise NewsSourceError("schema_changed", "THS response has no data.list")
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            title = _normalize_text(raw.get("title"))
            url = _normalize_text(raw.get("url"))
            if not title or not url:
                continue
            body = _normalize_text(raw.get("digest"))
            batch.candidates.append(
                NewsCandidate(
                    source=source_id,
                    symbol=_explicit_unique_symbol(f"{title} {body}", securities),
                    title=title,
                    url=url,
                    published_at=_parse_epoch(raw.get("rtime")),
                    content=body,
                    raw_payload=cast(JsonObject, _json_safe(raw)),
                )
            )
        batch.details = {"upstream_records": len(rows), "requests": transport.requests}
    except Exception as exc:
        failure = _source_failure(exc)
        batch.status = "unavailable"
        batch.failures.append(failure)
        batch.details = {"requests": transport.requests}
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    _copy_transport_counts(batch, transport)
    return batch


def _fetch_ths_v2(
    config: NewsPollConfig,
    _now: datetime,
    client_factory: HttpClientFactory | None,
) -> SourceBatch:
    source = cast(
        dict[str, Any],
        cast(dict[str, Any], config.document["sources"])["akshare_ths"],
    )
    source_id = "akshare_ths"
    client = client_factory(source_id) if client_factory else _new_http_client(config)
    transport = _transport(config, source_id, source, client)
    batch = SourceBatch(source_id=source_id)
    securities = _load_security_names(watchlist_only=False)
    first_page = int(str(source["first_page"]))
    max_pages = int(str(source["max_pages_per_run"]))
    page_parameter = str(source["page_parameter"])
    page_records: list[JsonObject] = []
    stop_reason: str | None = None
    try:
        for page in range(first_page, first_page + max_pages):
            response = transport.request(
                "GET",
                str(source["url"]),
                params={page_parameter: str(page), "tag": "", "track": "website"},
            )
            payload = _decode_json(response)
            if not isinstance(payload, dict):
                raise NewsSourceError("schema_changed", "THS response is not an object")
            data = payload.get("data")
            rows = data.get("list") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise NewsSourceError("schema_changed", "THS response has no data.list")
            page_records.append({"page": page, "upstream_records": len(rows)})
            if not rows:
                stop_reason = "empty_page"
                break
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                title = _normalize_text(raw.get("title"))
                url = _normalize_text(raw.get("url"))
                if not title or not url:
                    continue
                body = _normalize_text(raw.get("digest"))
                batch.candidates.append(
                    NewsCandidate(
                        source=source_id,
                        symbol=_explicit_unique_symbol(
                            f"{title} {body}", securities
                        ),
                        title=title,
                        url=url,
                        published_at=_parse_epoch(raw.get("rtime")),
                        content=body,
                        raw_payload=cast(JsonObject, _json_safe(raw)),
                    )
                )
        if stop_reason is None:
            batch.status = "degraded"
            batch.failures.append(
                {
                    "code": "catchup_incomplete",
                    "blocked": False,
                    "error_type": "NewsSourceError",
                    "message": "THS page cap reached before an empty page",
                    "constraint": "max_pages_per_run",
                }
            )
            stop_reason = "page_cap_open"
        batch.details = {
            "upstream_records": sum(
                int(record["upstream_records"]) for record in page_records
            ),
            "pages": page_records,
            "pages_requested": len(page_records),
            "pagination_stop_reason": stop_reason,
            "catchup_complete": stop_reason == "empty_page",
            "catchup_floor_applied": False,
            "catchup_floor_reason": "preregistered_overlap_not_numeric",
            "requests": transport.requests,
        }
    except Exception as exc:
        failure = _source_failure(exc)
        batch.status = "unavailable"
        batch.failures.append(failure)
        batch.details = {
            "pages": page_records,
            "pages_requested": len(page_records),
            "catchup_complete": False,
            "catchup_floor_applied": False,
            "catchup_floor_reason": "preregistered_overlap_not_numeric",
            "requests": transport.requests,
        }
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    _copy_transport_counts(batch, transport)
    return batch


def _fetch_ths(
    config: NewsPollConfig,
    now: datetime,
    client_factory: HttpClientFactory | None,
) -> SourceBatch:
    if config.document.get("schema_version") == "p4.1-news-poll-v2":
        return _fetch_ths_v2(config, now, client_factory)
    return _fetch_ths_v1(config, now, client_factory)


class _SinaNewsParser(HTMLParser):
    def __init__(self, required_class: str) -> None:
        super().__init__(convert_charrefs=True)
        self.required_class = required_class
        self._scope_depth = 0
        self._stack: list[tuple[str, bool]] = []
        self._href: str | None = None
        self._text: list[str] = []
        self.container_count = 0
        self.anchors: list[tuple[str, str]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        enters_scope = self.required_class in classes
        self._stack.append((tag.lower(), enters_scope))
        if enters_scope:
            self._scope_depth += 1
            self.container_count += 1
        if tag.lower() == "a" and self._scope_depth:
            self._href = values.get("href") or None
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized == "a" and self._href is not None:
            self.anchors.append((self._href, _normalize_text("".join(self._text))))
            self._href = None
            self._text = []
        match = next(
            (
                index
                for index in range(len(self._stack) - 1, -1, -1)
                if self._stack[index][0] == normalized
            ),
            None,
        )
        if match is None:
            return
        popped = self._stack[match:]
        del self._stack[match:]
        self._scope_depth -= sum(1 for _, enters in popped if enters)


def _fetch_sina(
    config: NewsPollConfig,
    _now: datetime,
    client_factory: HttpClientFactory | None,
) -> SourceBatch:
    source = cast(
        dict[str, Any],
        cast(dict[str, Any], config.document["sources"])["sina_company_news"],
    )
    source_id = "sina_company_news"
    client = client_factory(source_id) if client_factory else _new_http_client(config)
    transport = _transport(config, source_id, source, client)
    batch = SourceBatch(source_id=source_id)
    targets = _load_security_names(watchlist_only=True)[: int(source["max_symbols_per_run"])]
    if not targets:
        batch.status = "skipped_no_watchlist"
        batch.details = {"attempted": False}
        _copy_transport_counts(batch, transport)
        close = getattr(client, "close", None)
        if callable(close):
            close()
        return batch
    try:
        for symbol, name in targets:
            if symbol.startswith(("5", "6", "9")):
                prefix = "sh"
            elif symbol.startswith(("4", "8")):
                prefix = "bj"
            else:
                prefix = "sz"
            page_url = str(source["page_url_template"]).format(market_symbol=f"{prefix}{symbol}")
            try:
                response = transport.request("GET", page_url)
                parser = _SinaNewsParser(str(source["required_container_class"]))
                parser.feed(str(getattr(response, "text", "") or ""))
                if parser.container_count == 0:
                    raise NewsSourceError(
                        "schema_changed",
                        f"Sina news container missing for {symbol}",
                    )
                seen: set[str] = set()
                for href, title in parser.anchors:
                    absolute = urljoin(page_url, href)
                    parsed = urlparse(absolute)
                    if (
                        not title
                        or len(title) < int(source["minimum_title_length"])
                        or absolute in seen
                        or "/realstock/company/" in parsed.path
                        or not (parsed.hostname or "").lower().endswith("sina.com.cn")
                    ):
                        continue
                    seen.add(absolute)
                    # Page context is deliberately ignored. Only explicit title
                    # evidence may bind this item to the page's security.
                    explicit = _explicit_unique_symbol(title, [(symbol, name)])
                    batch.candidates.append(
                        NewsCandidate(
                            source=source_id,
                            symbol=explicit,
                            title=title,
                            url=absolute,
                            published_at=None,
                            content="",
                            raw_payload={
                                "href": href,
                                "page_symbol_context": symbol,
                                "page_name_context": name,
                                "symbol_binding": (
                                    "explicit_title_match" if explicit else "none"
                                ),
                            },
                        )
                    )
            except Exception as exc:
                batch.failures.append({"symbol": symbol, **_source_failure(exc)})
                if isinstance(exc, NewsSourceError) and exc.blocked:
                    break
        batch.status = "degraded" if batch.failures else "ok"
        batch.details = {
            "symbols_attempted": len(targets),
            "symbol_policy": "explicit_title_code_or_full_name_only",
            "requests": transport.requests,
        }
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    _copy_transport_counts(batch, transport)
    return batch


def _persist_candidates(
    candidates: list[NewsCandidate],
    fetch_completed_at: datetime,
    *,
    job_run_id: int,
    run_mode: str | None = None,
    preceded_by_coverage_gap: bool | None = None,
) -> JsonObject:
    if (run_mode is None) != (preceded_by_coverage_gap is None):
        raise ValueError("v2 ingestion context must be supplied as one complete pair")
    if run_mode is not None and run_mode not in {
        "regular_incremental",
        "coverage_gap_catchup",
    }:
        raise ValueError("unsupported news-poll run_mode")
    if run_mode is not None and (
        (run_mode == "coverage_gap_catchup") != preceded_by_coverage_gap
    ):
        raise ValueError("news-poll run_mode and coverage-gap marker disagree")
    prepared: list[tuple[int, NewsCandidate, str, str]] = []
    candidate_dispositions = ["filtered" for _ in candidates]
    filtered = 0
    for candidate_index, candidate in enumerate(candidates):
        if candidate.source not in AUDITED_NEWS_SOURCES:
            raise NewsSourceError(
                "source_not_audited",
                f"source {candidate.source!r} is not in AUDITED_NEWS_SOURCES",
                blocked=True,
            )
        title = _normalize_text(candidate.title)
        if not title:
            filtered += 1
            continue
        normalized_url = _normalize_url(candidate.url)
        normalized_candidate = NewsCandidate(
            source=candidate.source,
            symbol=_normalize_symbol(candidate.symbol),
            title=title,
            url=normalized_url,
            published_at=_ensure_utc(candidate.published_at),
            content=_normalize_text(candidate.content),
            raw_payload=cast(JsonObject, _json_safe(candidate.raw_payload)),
        )
        prepared.append(
            (
                candidate_index,
                normalized_candidate,
                normalized_url,
                content_hash(normalized_candidate),
            )
        )

    urls = [url for _, _, url, _ in prepared]
    hashes = [digest for _, _, _, digest in prepared]

    def existing_values(
        session: Any,
        column: Any,
        values: list[str],
    ) -> set[str]:
        result: set[str] = set()
        for offset in range(0, len(values), 500):
            result.update(
                str(item)
                for item in session.scalars(
                    select(column).where(column.in_(values[offset : offset + 500]))
                ).all()
            )
        return result

    with get_session() as session:
        # SQLite serializes writers. Acquire its write reservation before
        # assigning PIT timestamps so busy_timeout cannot move a historical
        # availability timestamp in front of a long lock wait.
        dialect_name = session.get_bind().dialect.name
        if dialect_name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        write_lock_acquired_at = utcnow()
        existing_urls = existing_values(session, NewsItem.url, urls)
        existing_hashes = existing_values(session, NewsItem.content_hash, hashes)
        duplicate_url = 0
        duplicate_content_hash = 0
        inserted = 0
        symbol_null = 0
        published_at_null = 0
        available_times: list[datetime] = []
        pending: list[tuple[int, NewsCandidate, str, str]] = []
        for candidate_index, candidate, url, digest in prepared:
            url_duplicate = url in existing_urls
            hash_duplicate = digest in existing_hashes
            if url_duplicate:
                duplicate_url += 1
                candidate_dispositions[candidate_index] = "duplicate_url"
                continue
            if hash_duplicate:
                duplicate_content_hash += 1
                candidate_dispositions[candidate_index] = "duplicate_content_hash"
                continue
            pending.append((candidate_index, candidate, url, digest))
            existing_urls.add(url)
            existing_hashes.add(digest)

        # All parsing, lock acquisition and duplicate reads are complete. The
        # timestamp is assigned immediately before the sole INSERT flush while
        # the write reservation is held. Commit completion is kept separately
        # in JobRun stats; the raw row does not mislabel this pre-flush instant
        # as a persisted/commit timestamp.
        available_time = utcnow()
        if available_time < fetch_completed_at:
            raise RuntimeError("available_time precedes fetch completion")
        for candidate_index, candidate, url, digest in pending:
            ingestion = {
                "job_run_id": job_run_id,
                "fetched_at_utc": fetch_completed_at.isoformat(),
                "write_lock_acquired_at_utc": write_lock_acquired_at.isoformat(),
                "available_time_assigned_at_utc": available_time.isoformat(),
                "available_time_basis": "write_locked_immediately_before_flush_utc",
            }
            if run_mode is not None:
                ingestion.update(
                    {
                        "run_mode": run_mode,
                        "preceded_by_coverage_gap": preceded_by_coverage_gap,
                    }
                )
            raw_payload = {
                **candidate.raw_payload,
                "_alphapilot_ingestion": ingestion,
            }
            session.add(
                NewsItem(
                    source=candidate.source,
                    symbol=candidate.symbol,
                    title=candidate.title,
                    url=url,
                    published_at=candidate.published_at,
                    available_time=available_time,
                    content_hash=digest,
                    raw_payload=raw_payload,
                )
            )
            inserted += 1
            candidate_dispositions[candidate_index] = "inserted"
            symbol_null += int(candidate.symbol is None)
            published_at_null += int(candidate.published_at is None)
            available_times.append(available_time)
        session.flush()
        flush_completed_at = utcnow()
        session.commit()
        commit_completed_at = utcnow()

    result: JsonObject = {
        "fetched": len(candidates),
        "prepared": len(prepared),
        "filtered": filtered,
        "inserted": inserted,
        "duplicate_url": duplicate_url,
        "duplicate_content_hash": duplicate_content_hash,
        "symbol_null": symbol_null,
        "published_at_null": published_at_null,
        "fetch_completed_at": fetch_completed_at.isoformat(),
        "db_write_lock_acquired_at": write_lock_acquired_at.isoformat(),
        "db_flush_completed_at": flush_completed_at.isoformat(),
        "db_commit_completed_at": commit_completed_at.isoformat(),
        "first_available_time": min(available_times).isoformat() if available_times else None,
        "last_available_time": max(available_times).isoformat() if available_times else None,
        "available_time_coverage": 1.0 if inserted else None,
        # Private in-process evidence used by _batch_stats to attribute the
        # unchanged dual-key disposition to the exact CNInfo request slice.
        # It is removed before JobRun serialization; only bounded counters are
        # persisted in slices[*].
        "_candidate_dispositions": candidate_dispositions,
    }
    if run_mode is not None:
        result["preceded_by_coverage_gap_inserted"] = (
            inserted if preceded_by_coverage_gap else 0
        )
    return result


def _safety_snapshot(settings: Settings) -> JsonObject:
    with get_session() as session:
        proposal_ids = [
            str(item)
            for item in session.scalars(
                select(TradeProposalRecord.proposal_id).order_by(
                    TradeProposalRecord.proposal_id
                )
            ).all()
        ]
        order_ids = [
            int(item)
            for item in session.scalars(select(BrokerOrder.id).order_by(BrokerOrder.id)).all()
        ]
        non_simulate = int(
            session.scalar(
                select(func.count())
                .select_from(BrokerOrder)
                .where(BrokerOrder.environment != "SIMULATE")
            )
            or 0
        )
    return {
        "settings": {
            "trading_mode": settings.trading_mode,
            "live_trading_enabled": settings.live_trading_enabled,
            "paper_trading_enabled": settings.paper_trading_enabled,
            "paper_auto_trading_enabled": settings.paper_auto_trading_enabled,
            "futu_enable_account_mutation": settings.futu_enable_account_mutation,
            "futu_enable_trade": settings.futu_enable_trade,
            "unlock_trade_permanently_blocked": "unlock_trade" in PERMANENTLY_BLOCKED_METHODS,
        },
        "trade_proposal_ids": proposal_ids,
        "broker_order_ids": order_ids,
        "non_simulate_order_count": non_simulate,
    }


def _safety_issues(snapshot: JsonObject) -> list[str]:
    settings = cast(dict[str, Any], snapshot["settings"])
    expected = {
        "trading_mode": "research",
        "live_trading_enabled": False,
        "paper_trading_enabled": False,
        "paper_auto_trading_enabled": False,
        "futu_enable_account_mutation": False,
        "futu_enable_trade": False,
        "unlock_trade_permanently_blocked": True,
    }
    issues = [
        f"{key}={settings.get(key)!r}, expected {value!r}"
        for key, value in expected.items()
        if settings.get(key) != value
    ]
    if int(snapshot["non_simulate_order_count"]) != 0:
        issues.append("broker_orders contains a non-SIMULATE row")
    return issues


def _attach_slice_persistence_stats(
    batch: SourceBatch,
    candidate_dispositions: object,
    source_persistence: Mapping[str, object],
) -> None:
    slices = batch.details.get("slices")
    if not isinstance(slices, list):
        return
    if not isinstance(candidate_dispositions, list) or not all(
        isinstance(item, str) for item in candidate_dispositions
    ):
        raise RuntimeError("candidate disposition evidence is malformed")
    if len(candidate_dispositions) != len(batch.candidates):
        raise RuntimeError("candidate disposition evidence length drifted")

    offset = 0
    allowed = {
        "filtered",
        "inserted",
        "duplicate_url",
        "duplicate_content_hash",
    }
    aggregate = {
        "inserted": 0,
        "duplicate_url": 0,
        "duplicate_content_hash": 0,
        "filtered": 0,
    }
    for raw_slice in slices:
        if not isinstance(raw_slice, dict):
            raise RuntimeError("CNInfo slice evidence is malformed")
        fetched = raw_slice.get("fetched")
        if not isinstance(fetched, int) or isinstance(fetched, bool) or fetched < 0:
            raise RuntimeError("CNInfo slice fetched count is malformed")
        slice_dispositions = candidate_dispositions[offset : offset + fetched]
        if len(slice_dispositions) != fetched or not set(slice_dispositions) <= allowed:
            raise RuntimeError("CNInfo slice disposition evidence is incomplete")
        offset += fetched
        counters = {
            key: slice_dispositions.count(key)
            for key in (
                "inserted",
                "duplicate_url",
                "duplicate_content_hash",
                "filtered",
            )
        }
        for key, value in counters.items():
            aggregate[key] += value
        raw_slice.update(
            {
                **counters,
                "disposition_total": sum(counters.values()),
                "disposition_identity_valid": sum(counters.values()) == fetched,
            }
        )
    if offset != len(candidate_dispositions):
        raise RuntimeError("CNInfo slice disposition ranges do not close")
    for key, value in aggregate.items():
        if source_persistence.get(key) != value:
            raise RuntimeError(
                f"CNInfo slice {key} does not match the source aggregate"
            )


def _batch_stats(batch: SourceBatch, persistence: JsonObject | None = None) -> JsonObject:
    public_persistence = dict(persistence) if persistence is not None else None
    if public_persistence is not None:
        dispositions = public_persistence.pop("_candidate_dispositions", None)
        _attach_slice_persistence_stats(batch, dispositions, public_persistence)
    result: JsonObject = {
        "status": batch.status,
        "request_count": batch.request_count,
        "retry_count": batch.retry_count,
        "failure_count": len(batch.failures),
        "failures": batch.failures[:20],
        **batch.details,
    }
    if batch.logical_request_count is not None:
        result["logical_request_count"] = batch.logical_request_count
    if batch.physical_attempt_count is not None:
        result["physical_attempt_count"] = batch.physical_attempt_count
    if public_persistence is not None:
        result.update(public_persistence)
    else:
        result.update(
            {
                "fetched": len(batch.candidates),
                "inserted": 0,
                "duplicate_url": 0,
                "duplicate_content_hash": 0,
            }
        )
    return result


def _safe_v2_failure(failure: Mapping[str, object]) -> JsonObject:
    """Keep only bounded machine-readable failure evidence for v2 stats."""

    result: JsonObject = {
        "code": str(failure.get("code") or "unexpected_error")[:80],
        "blocked": failure.get("blocked") is True,
        "error_type": str(failure.get("error_type") or "UnknownError")[:80],
    }
    column = failure.get("column")
    if isinstance(column, str):
        result["column"] = column[:32]
    date_shanghai = failure.get("date_shanghai")
    if isinstance(date_shanghai, str):
        result["date_shanghai"] = date_shanghai[:10]
    suppression = failure.get("suppression")
    if isinstance(suppression, Mapping):
        result["suppression"] = {
            key: value
            for key, value in suppression.items()
            if key
            in {
                "code",
                "constraint",
                "source_id",
                "logical_request_count",
                "physical_attempt_count",
                "max_physical_attempts",
                "retry_suppressed",
            }
            and isinstance(value, (str, int, float, bool, type(None)))
        }
    return result


def _v2_terminal_diagnostic(
    *,
    code: str,
    source: str,
    constraint: str,
    recoverable: bool,
    retry_suppressed: bool = False,
) -> JsonObject:
    return {
        "code": code,
        "source": source,
        "constraint": constraint,
        "recoverable": recoverable,
        "retry_suppressed": retry_suppressed,
    }


def _v2_cninfo_failure_diagnostic(source: Mapping[str, object]) -> JsonObject:
    failures = source.get("failures")
    first = (
        failures[0]
        if isinstance(failures, list) and failures and isinstance(failures[0], dict)
        else {}
    )
    code = str(first.get("code") or "cninfo_invalid_terminal_state")[:80]
    retry_suppressed = isinstance(first.get("suppression"), dict)
    if code in {
        "transport_timeout",
        "transport_error",
        "client_error",
        "http_server_error",
    }:
        constraint = "critical_transport"
        recoverable = True
    elif code in {
        "http_forbidden_or_antibot",
        "http_rate_limited",
        "http_client_error",
        "redirect_not_followed",
        "forbidden_upstream",
        "logical_request_budget_exhausted",
        "physical_attempt_budget_exhausted",
    }:
        constraint = "critical_transport_policy"
        recoverable = False
    elif code in {"decode_error", "schema_changed"}:
        constraint = "critical_schema"
        recoverable = False
    elif code in {"persistence_failed", "source_not_audited"}:
        constraint = "critical_persistence"
        recoverable = False
    elif str(source.get("status")) == "degraded":
        code = "cninfo_invalid_degraded_state"
        constraint = "degraded_cause_allowlist"
        recoverable = False
    else:
        constraint = "critical_unknown"
        recoverable = False
    return _v2_terminal_diagnostic(
        code=code,
        source="cninfo",
        constraint=constraint,
        recoverable=recoverable,
        retry_suppressed=retry_suppressed,
    )


def _v2_cninfo_page_cap_only(
    source: Mapping[str, object],
    *,
    expected_columns: list[str],
) -> bool:
    """Accept only the pre-registered page-cap incomplete shape as degraded."""

    if source.get("status") != "degraded" or source.get("tls_verification") is not True:
        return False
    failures = source.get("failures")
    if not isinstance(failures, list) or not failures:
        return False
    failure_columns: list[str] = []
    for failure in failures:
        if not isinstance(failure, dict) or failure.get("code") != "pagination_incomplete":
            return False
        column = failure.get("column")
        if not isinstance(column, str):
            return False
        failure_columns.append(column)

    checkpoints = source.get("column_watermarks")
    if not isinstance(checkpoints, dict) or set(checkpoints) != set(expected_columns):
        return False
    capped_columns: list[str] = []
    for column in expected_columns:
        checkpoint = checkpoints.get(column)
        if not isinstance(checkpoint, dict):
            return False
        if (
            checkpoint.get("attempted") is not True
            or checkpoint.get("skipped_due_to_prior_critical_failure") is not False
        ):
            return False
        if checkpoint.get("page_cap_hit") is True:
            if (
                checkpoint.get("pagination_complete") is not False
                or checkpoint.get("checkpoint_committed") is not False
            ):
                return False
            capped_columns.append(column)
        elif (
            checkpoint.get("pagination_complete") is not True
            or checkpoint.get("checkpoint_committed") is not True
        ):
            return False
    return bool(capped_columns) and sorted(failure_columns) == sorted(capped_columns)


def _v2_cninfo_complete(
    source: Mapping[str, object],
    *,
    expected_columns: list[str],
) -> bool:
    if (
        source.get("status") != "ok"
        or source.get("failure_count") != 0
        or source.get("tls_verification") is not True
    ):
        return False
    checkpoints = source.get("column_watermarks")
    if not isinstance(checkpoints, dict) or set(checkpoints) != set(expected_columns):
        return False
    return all(
        isinstance(checkpoints.get(column), dict)
        and checkpoints[column].get("attempted") is True
        and checkpoints[column].get("skipped_due_to_prior_critical_failure") is False
        and checkpoints[column].get("page_cap_hit") is False
        and checkpoints[column].get("pagination_complete") is True
        and checkpoints[column].get("checkpoint_committed") is True
        for column in expected_columns
    )


def _v2_run_context(config: NewsPollConfig, started_at: datetime) -> JsonObject:
    """Derive the frozen Monday recovery semantics from the actual poll time."""

    local = started_at.astimezone(MARKET_TIMEZONE)
    schedule = cast(dict[str, Any], config.document["schedule"])
    policy = cast(dict[str, Any], schedule["monday_host_gap_policy"])
    recovery_hour, recovery_minute = (
        int(part) for part in str(policy["recovery_catchup_slot_shanghai"]).split(":")
    )
    recovery_start = local.replace(
        hour=recovery_hour,
        minute=recovery_minute,
        second=0,
        microsecond=0,
    )
    recovery_end = recovery_start + timedelta(minutes=int(schedule["scheduler_tick_minutes"]))
    is_catchup = local.weekday() == 0 and recovery_start <= local < recovery_end
    if not is_catchup:
        return {
            "run_mode": "regular_incremental",
            "coverage_gap": False,
            "coverage_gap_details": None,
        }

    suppressed = [
        str(item)
        for item in cast(
            list[object],
            policy["intentionally_suppressed_slots_shanghai"],
        )
    ]
    suppressed_at: list[datetime] = []
    for item in suppressed:
        hour, minute = (int(part) for part in item.split(":"))
        suppressed_at.append(
            local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        )
    first_suppressed = min(suppressed_at)
    return {
        "run_mode": "coverage_gap_catchup",
        "coverage_gap": True,
        "coverage_gap_details": {
            "reason": "owner_confirmed_periodic_host_unavailability",
            "timezone": "Asia/Shanghai",
            "suppressed_slots_shanghai": [item.isoformat() for item in suppressed_at],
            "suppressed_slot_count": len(suppressed_at),
            "first_suppressed_slot_shanghai": first_suppressed.isoformat(),
            "recovery_poll_started_at_utc": started_at.isoformat(),
            "recovery_poll_started_at_shanghai": local.isoformat(),
            "span_seconds": int((local - first_suppressed).total_seconds()),
            "span_basis": "first_suppressed_slot_to_actual_poll_started_at",
        },
    }


def _v2_catchup_stats(
    *,
    started_at: datetime,
    source_results: Mapping[str, JsonObject],
) -> JsonObject:
    cninfo = source_results.get("cninfo", {})
    checkpoints = cninfo.get("column_watermarks")
    ranges: JsonObject = {}
    range_basis = "per_column_verified_watermark_minus_overlap_to_actual_poll"
    if isinstance(checkpoints, Mapping):
        for column, raw in checkpoints.items():
            if not isinstance(column, str) or not isinstance(raw, Mapping):
                continue
            start_raw = raw.get("verified_watermark_floor_utc")
            start = _parsed_utc_watermark(start_raw)
            ranges[column] = {
                "start_utc": start.isoformat() if start is not None else None,
                "end_utc": started_at.isoformat(),
                "span_seconds": (
                    max(0, int((started_at - start).total_seconds()))
                    if start is not None
                    else None
                ),
            }
    else:
        slices = cninfo.get("slices")
        canonical_column = cninfo.get("canonical_column")
        if isinstance(slices, list) and canonical_column == "szse":
            current_date = started_at.astimezone(MARKET_TIMEZONE).date().isoformat()
            current_slice = next(
                (
                    item
                    for item in slices
                    if isinstance(item, Mapping)
                    and item.get("date_shanghai") == current_date
                    and item.get("mode") == "current_date_incremental"
                ),
                None,
            )
            if isinstance(current_slice, Mapping):
                start = _parsed_utc_watermark(
                    current_slice.get("incremental_floor_utc")
                )
                ranges["szse"] = {
                    "start_utc": start.isoformat() if start is not None else None,
                    "end_utc": started_at.isoformat(),
                    "span_seconds": (
                        max(0, int((started_at - start).total_seconds()))
                        if start is not None
                        else None
                    ),
                }
                range_basis = (
                    "canonical_daily_verified_observed_minus_overlap_to_actual_poll"
                )

    by_source: JsonObject = {}
    for source_id, source in source_results.items():
        by_source[source_id] = {
            key: int(source.get(key, 0))
            for key in (
                "fetched",
                "inserted",
                "duplicate_url",
                "duplicate_content_hash",
                "preceded_by_coverage_gap_inserted",
            )
        }
    return {
        "range_basis": range_basis,
        "cninfo_column_ranges": ranges,
        "range_end_utc": started_at.isoformat(),
        "counts_by_source": by_source,
        "counts_all_sources": {
            key: sum(int(source.get(key, 0)) for source in source_results.values())
            for key in (
                "fetched",
                "inserted",
                "duplicate_url",
                "duplicate_content_hash",
                "preceded_by_coverage_gap_inserted",
            )
        },
        "available_time_policy": "actual_write_locked_ingestion_time_utc",
        "restores_completeness_not_timeliness": True,
    }


def run_news_poll(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    now: datetime | None = None,
    http_client_factory: HttpClientFactory | None = None,
    execution_mode: V2ExecutionMode = "scheduler",
    authorization_receipt_path: Path | None = None,
) -> JsonObject | JobOutcome:
    config = load_news_poll_config(config_path)
    is_v2 = _is_v2_config(config)
    execution_authorization = _v2_execution_authorization(
        config,
        execution_mode=execution_mode,
        authorization_receipt_path=authorization_receipt_path,
    )
    context = current_job_run()
    if context is None or context.job_name != "news_poll":
        raise JobExecutionError(
            "P4.1 news poll must run inside its durable JobRun",
            stats={
                "config_version": config.document["schema_version"],
                "config_sha256": config.sha256,
            },
        )
    started_at = _ensure_utc(now) if now is not None else utcnow()
    assert started_at is not None
    run_context = (
        _v2_run_context(config, started_at)
        if is_v2
        else {
            "run_mode": "regular_incremental",
            "coverage_gap": False,
            "coverage_gap_details": None,
        }
    )
    if is_v2 and execution_mode == "initial_backlog_migration":
        run_context = {
            "run_mode": "coverage_gap_catchup",
            "coverage_gap": True,
            "coverage_gap_details": {
                "reason": "initial_backlog_migration",
                "manual_execution": True,
                "authorization_receipt_sha256": execution_authorization.get(
                    "authorization_receipt_sha256"
                ),
            },
        }
    elif is_v2 and execution_mode == "standard_incremental_validation":
        run_context = {
            "run_mode": "regular_incremental",
            "coverage_gap": False,
            "coverage_gap_details": None,
        }
    settings = get_settings()
    safety_before = _safety_snapshot(settings)
    stats: JsonObject = {
        "config_version": config.document["schema_version"],
        "config_path": str(config.path.relative_to(PROJECT_DIR)),
        "config_sha256": config.sha256,
        "source_spike_report_sha256": cast(
            dict[str, Any], config.document["source_spike"]
        )["report_sha256"],
        "poll_started_at": started_at.isoformat(),
        "sources": {},
        "source_failures": [],
        "safety_before": safety_before,
        "p4_2_unlocked": False,
    }
    if is_v2:
        stats.update(
            {
                "execution_authorization": execution_authorization,
                "execution_mode": execution_mode,
                "run_mode": run_context["run_mode"],
                "coverage_gap": run_context["coverage_gap"],
                "coverage_gap_details": run_context["coverage_gap_details"],
                "catchup": None,
                "terminal_diagnostics": None,
            }
        )
    issues = _safety_issues(safety_before)
    if issues:
        stats["safety_issues"] = issues
        if is_v2:
            stats["safety_after"] = safety_before
            stats["safety_unchanged"] = True
            stats["poll_completed_at"] = utcnow().isoformat()
            stats["terminal_diagnostics"] = _v2_terminal_diagnostic(
                code="safety_preflight_failed",
                source="news_poll",
                constraint="trading_safety_invariants",
                recoverable=False,
            )
        raise JobExecutionError("P4.1 news poll safety preflight failed", stats=stats)

    source_contract = cast(dict[str, Any], config.document["sources"])
    source_results: dict[str, JsonObject] = {
        "akshare_cls": {
            "status": "unavailable",
            "attempted": False,
            **_zero_request_counter_stats(config),
            "failure_count": 0,
            "failures": [],
            "reason": str(source_contract["akshare_cls"]["reason"]),
            "fetched": 0,
            "inserted": 0,
            "duplicate_url": 0,
            "duplicate_content_hash": 0,
        },
        "akshare_caixin": {
            "status": "excluded_missing_native_title",
            "attempted": False,
            **_zero_request_counter_stats(config),
            "failure_count": 0,
            "failures": [],
            "fetched": 0,
            "inserted": 0,
            "duplicate_url": 0,
            "duplicate_content_hash": 0,
        },
        "futu_auxiliary": {
            "status": "pending_trading_day_latency_retest",
            "enabled": False,
            "attempted": False,
            "quote_methods_called": [],
            "trade_methods_called": [],
            **_zero_request_counter_stats(config),
            "failure_count": 0,
            "failures": [],
            "fetched": 0,
            "inserted": 0,
            "duplicate_url": 0,
            "duplicate_content_hash": 0,
        },
    }
    critical_failures: list[str] = []
    enabled_successes = 0
    fetchers: list[
        tuple[
            str,
            Callable[[NewsPollConfig, datetime, HttpClientFactory | None], SourceBatch],
        ]
    ] = [
        ("cninfo", _fetch_cninfo),
        ("akshare_ths", _fetch_ths),
        ("sina_company_news", _fetch_sina),
    ]
    for source_id, fetcher in fetchers:
        contract = cast(dict[str, Any], source_contract[source_id])
        if contract.get("enabled") is not True:
            source_results[source_id] = {
                "status": "disabled",
                "attempted": False,
                **_zero_request_counter_stats(config),
                "failure_count": 0,
                "failures": [],
                "fetched": 0,
                "inserted": 0,
                "duplicate_url": 0,
                "duplicate_content_hash": 0,
            }
            continue
        try:
            batch = fetcher(config, started_at, http_client_factory)
            fetch_completed_at = utcnow()
            try:
                persistence = _persist_candidates(
                    batch.candidates,
                    fetch_completed_at,
                    job_run_id=context.run_id,
                    run_mode=(str(run_context["run_mode"]) if is_v2 else None),
                    preceded_by_coverage_gap=(
                        bool(run_context["coverage_gap"]) if is_v2 else None
                    ),
                )
            except Exception as exc:
                failure = (
                    {
                        "code": "persistence_failed",
                        "blocked": True,
                        "error_type": type(exc).__name__[:80],
                    }
                    if is_v2
                    else _source_failure(exc)
                )
                batch.status = "unavailable"
                batch.failures.append(failure)
                persistence = None
            source_results[source_id] = _batch_stats(batch, persistence)
            if is_v2 and source_id == "cninfo":
                source_results[source_id]["failures"] = [
                    _safe_v2_failure(failure) for failure in batch.failures[:20]
                ]
            if batch.status in {"ok", "degraded", "skipped_no_watchlist"}:
                enabled_successes += 1
            elif contract.get("critical") is True:
                critical_failures.append(source_id)
            if batch.failures:
                stats["source_failures"] = [
                    *cast(list[JsonObject], stats["source_failures"]),
                    *(
                        {
                            "source_id": source_id,
                            **(
                                _safe_v2_failure(failure)
                                if is_v2 and source_id == "cninfo"
                                else failure
                            ),
                        }
                        for failure in batch.failures
                    ),
                ]
        except Exception as exc:
            failure = (
                _safe_v2_failure(_source_failure(exc))
                if is_v2 and source_id == "cninfo"
                else _source_failure(exc)
            )
            source_results[source_id] = {
                "status": "unavailable",
                "attempted": True,
                **_zero_request_counter_stats(config),
                "failure_count": 1,
                "failures": [failure],
                "fetched": 0,
                "inserted": 0,
                "duplicate_url": 0,
                "duplicate_content_hash": 0,
            }
            cast(list[JsonObject], stats["source_failures"]).append(
                {"source_id": source_id, **failure}
            )
            if contract.get("critical") is True:
                critical_failures.append(source_id)
        stats["sources"] = source_results

    safety_after = _safety_snapshot(settings)
    stats["safety_after"] = safety_after
    stats["safety_unchanged"] = safety_before == safety_after
    post_issues = _safety_issues(safety_after)
    if safety_before != safety_after or post_issues:
        stats["safety_issues"] = post_issues or ["proposal/order identity changed"]
        stats["poll_completed_at"] = utcnow().isoformat()
        if is_v2:
            stats["terminal_diagnostics"] = _v2_terminal_diagnostic(
                code="safety_postflight_failed",
                source="news_poll",
                constraint="trading_safety_invariants",
                recoverable=False,
            )
        raise JobExecutionError("P4.1 news poll safety postflight failed", stats=stats)

    source_values = list(source_results.values())
    totals = {
        key: sum(int(item.get(key, 0)) for item in source_values)
        for key in (
            "request_count",
            "retry_count",
            "failure_count",
            "fetched",
            "inserted",
            "duplicate_url",
            "duplicate_content_hash",
        )
    }
    stats["totals"] = totals
    stats["pit"] = {
        "available_time_policy": "write_locked_immediately_before_flush_utc",
        "available_time_coverage": 1.0 if totals["inserted"] else None,
        "decision_visibility_operator": "<",
        "published_at_never_substitutes_available_time": True,
    }
    if is_v2 and run_context["coverage_gap"] is True:
        stats["catchup"] = _v2_catchup_stats(
            started_at=started_at,
            source_results=source_results,
        )
    stats["poll_completed_at"] = utcnow().isoformat()
    if is_v2:
        cninfo_result = source_results.get("cninfo", {})
        if config.document.get("schema_version") == "p4.1-news-poll-v2.1":
            slices = cninfo_result.get("slices")
            complete = (
                cninfo_result.get("status") == "ok"
                and cninfo_result.get("failure_count") == 0
                and cninfo_result.get("tls_verification") is True
                and isinstance(slices, list)
                and bool(slices)
                and all(
                    isinstance(item, dict)
                    and item.get("attempted") is True
                    and item.get("pagination_complete") is True
                    and item.get("page_cap_hit") is False
                    and item.get("failure") is None
                    for item in slices
                )
            )
            if complete:
                stats["terminal_diagnostics"] = None
                return JobOutcome(status="ok", stats=cast(JsonObject, _json_safe(stats)))
            page_cap_only = (
                cninfo_result.get("status") == "degraded"
                and isinstance(slices, list)
                and any(
                    isinstance(item, dict) and item.get("page_cap_hit") is True
                    for item in slices
                )
                and all(
                    isinstance(item, dict) and item.get("failure") is None
                    for item in slices
                )
            )
            if page_cap_only:
                stats["terminal_diagnostics"] = _v2_terminal_diagnostic(
                    code="cninfo_daily_slice_pagination_incomplete",
                    source="cninfo",
                    constraint="max_pages_per_day",
                    recoverable=True,
                )
                return JobOutcome(
                    status="degraded",
                    stats=cast(JsonObject, _json_safe(stats)),
                )
            diagnostic = _v2_cninfo_failure_diagnostic(cninfo_result)
            stats["terminal_diagnostics"] = diagnostic
            stats["critical_failures"] = ["cninfo"]
            raise JobExecutionError(
                f"P4.1 critical source failed: cninfo/{diagnostic['code']}",
                stats=stats,
            )
        columns = [str(item) for item in cast(list[object], source_contract["cninfo"]["columns"])]
        if _v2_cninfo_complete(cninfo_result, expected_columns=columns):
            stats["terminal_diagnostics"] = None
            return JobOutcome(status="ok", stats=cast(JsonObject, _json_safe(stats)))
        if _v2_cninfo_page_cap_only(cninfo_result, expected_columns=columns):
            catchup = run_context["coverage_gap"] is True
            stats["terminal_diagnostics"] = _v2_terminal_diagnostic(
                code=(
                    "recovery_catchup_incomplete"
                    if catchup
                    else "cninfo_column_pagination_incomplete"
                ),
                source="cninfo",
                constraint="max_pages_per_column",
                recoverable=True,
            )
            return JobOutcome(
                status="degraded",
                stats=cast(JsonObject, _json_safe(stats)),
            )
        diagnostic = _v2_cninfo_failure_diagnostic(cninfo_result)
        stats["terminal_diagnostics"] = diagnostic
        stats["critical_failures"] = ["cninfo"]
        raise JobExecutionError(
            f"P4.1 critical source failed: cninfo/{diagnostic['code']}",
            stats=stats,
        )
    if critical_failures or enabled_successes == 0:
        stats["critical_failures"] = critical_failures
        raise JobExecutionError("P4.1 critical news source failed", stats=stats)
    return cast(JsonObject, _json_safe(stats))


def run_news_poll_v2_1_initial_migration(
    *,
    authorization_receipt_path: Path,
    now: datetime | None = None,
    http_client_factory: HttpClientFactory | None = None,
) -> JobRun:
    """Manual-only v2.1 backlog migration entrypoint; never scheduler-registered."""

    return run_job(
        "news_poll",
        config_path=V2_1_CONFIG_PATH,
        now=now,
        http_client_factory=http_client_factory,
        execution_mode="initial_backlog_migration",
        authorization_receipt_path=authorization_receipt_path,
    )


def run_news_poll_v2_1_incremental_validation(
    *,
    authorization_receipt_path: Path,
    now: datetime | None = None,
    http_client_factory: HttpClientFactory | None = None,
) -> JobRun:
    """Manual-only post-migration validation entrypoint; never scheduler-registered."""

    return run_job(
        "news_poll",
        config_path=V2_1_CONFIG_PATH,
        now=now,
        http_client_factory=http_client_factory,
        execution_mode="standard_incremental_validation",
        authorization_receipt_path=authorization_receipt_path,
    )


def _news_poll_trigger_v1() -> OrTrigger:
    baseline = CronTrigger(minute="0,30", timezone=MARKET_TIMEZONE)
    trading_extras = [
        CronTrigger(day_of_week="mon-fri", hour=9, minute="40,50", timezone=MARKET_TIMEZONE),
        CronTrigger(
            day_of_week="mon-fri",
            hour=10,
            minute="10,20,40,50",
            timezone=MARKET_TIMEZONE,
        ),
        CronTrigger(day_of_week="mon-fri", hour=11, minute="10,20", timezone=MARKET_TIMEZONE),
        CronTrigger(
            day_of_week="mon-fri",
            hour="13-14",
            minute="10,20,40,50",
            timezone=MARKET_TIMEZONE,
        ),
    ]
    return OrTrigger([baseline, *trading_extras])


def _v2_shared_trading_extras() -> list[CronTrigger]:
    return [
        CronTrigger(
            day_of_week="mon-fri",
            hour=10,
            minute="10,20,40,50",
            timezone=MARKET_TIMEZONE,
        ),
        CronTrigger(
            day_of_week="mon-fri",
            hour=11,
            minute="10,20",
            timezone=MARKET_TIMEZONE,
        ),
        CronTrigger(
            day_of_week="mon-fri",
            hour="13-14",
            minute="10,20,40,50",
            timezone=MARKET_TIMEZONE,
        ),
    ]


def _news_poll_trigger_v2() -> OrTrigger:
    non_monday_baseline = CronTrigger(
        day_of_week="tue-sun",
        minute="0,30",
        timezone=MARKET_TIMEZONE,
    )
    monday_baseline = CronTrigger(
        day_of_week="mon",
        hour="0-8,10-23",
        minute="0,30",
        timezone=MARKET_TIMEZONE,
    )
    normal_opening_extras = CronTrigger(
        day_of_week="tue-fri",
        hour=9,
        minute="40,50",
        timezone=MARKET_TIMEZONE,
    )
    monday_recovery = CronTrigger(
        day_of_week="mon",
        hour=9,
        minute=50,
        timezone=MARKET_TIMEZONE,
    )
    return OrTrigger(
        [
            non_monday_baseline,
            monday_baseline,
            normal_opening_extras,
            monday_recovery,
            *_v2_shared_trading_extras(),
        ]
    )


def _news_poll_trigger(config_path: Path = DEFAULT_CONFIG_PATH) -> OrTrigger:
    if config_path == V1_CONFIG_PATH:
        return _news_poll_trigger_v1()
    if config_path in {V2_CONFIG_PATH, V2_1_CONFIG_PATH}:
        return _news_poll_trigger_v2()
    raise ValueError(f"unsupported P4.1 news-poll config path: {config_path}")


def _news_poll_scheduler_enabled() -> bool:
    raw = os.environ.get(NEWS_POLL_ENABLED_ENV, "false").strip().lower()
    if raw not in {"true", "false"}:
        raise ValueError(f"{NEWS_POLL_ENABLED_ENV} must be exactly true or false")
    return raw == "true"


def register_news_poll_job() -> None:
    register(
        JobSpec(
            name="news_poll",
            func=run_news_poll,
            trigger=_news_poll_trigger() if _news_poll_scheduler_enabled() else None,
        )
    )
