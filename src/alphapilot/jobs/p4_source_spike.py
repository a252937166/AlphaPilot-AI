from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from time import monotonic, sleep
from types import FunctionType
from typing import Any, cast
from urllib.parse import urljoin, urlparse

import httpx
import pandas as pd
import yaml
from sqlalchemy import func, select

from alphapilot.core.config import Settings, get_settings
from alphapilot.core.job_execution_context import current_job_run
from alphapilot.db.engine import get_session
from alphapilot.db.models import BrokerOrder, JobRun, TradeProposalRecord
from alphapilot.futu.client import (
    PERMANENTLY_BLOCKED_METHODS,
    FutuClient,
)
from alphapilot.jobs.registry import JobExecutionError, JobSpec, register

HttpClientFactory = Callable[..., Any]
FutuClientFactory = Callable[[], Any]
JsonObject = dict[str, Any]

_EXPECTED_NETWORK: JsonObject = {
    "trust_env": False,
    "connect_timeout_seconds": 3.0,
    "read_timeout_seconds": 8.0,
    "write_timeout_seconds": 8.0,
    "pool_timeout_seconds": 3.0,
    "max_attempts_per_request": 1,
    "retry_backoff_seconds": [],
    "user_agent": "AlphaPilotAI/0.4 P4.1 source-feasibility-spike",
}
_EXPECTED_ASSESSMENT: JsonObject = {
    "native_primary_required_fields": ["title", "url"],
    "published_at_nullable": True,
    "available_time_policy": "jobrun_evidence_persisted_at_utc",
    "available_time_must_not_copy_published_at": True,
    "primary_status": "usable_primary",
    "auxiliary_status": "usable_auxiliary",
    "degraded_primary_status": "usable_primary_degraded",
    "degraded_auxiliary_status": "usable_auxiliary_degraded",
    "failure_status": "unavailable",
    "blocked_status": "blocked",
    "max_samples_per_probe": 3,
    "rate_limit_observation_scope": "single_bounded_spike_not_capacity_proof",
}
_EXPECTED_SOURCES: JsonObject = {
    "cninfo": {
        "enabled": True,
        "source_id": "cninfo",
        "upstream": "www.cninfo.com.cn",
        "min_interval_seconds": 1.0,
        "max_requests": 8,
        "lookback_days": 7,
        "page_size": 10,
        "verify_tls": False,
        "top_search_url": "https://www.cninfo.com.cn/new/information/topSearch/query",
        "announcements_url": "https://www.cninfo.com.cn/new/hisAnnouncement/query",
        "static_url_prefix": "https://static.cninfo.com.cn/",
        "symbol_probes": [
            {"symbol": "600519", "market": "SH", "column": "sse"},
            {"symbol": "000001", "market": "SZ", "column": "szse"},
            {"symbol": "920000", "market": "BSE", "column": "sse"},
        ],
        "global_columns": ["sse", "szse"],
    },
    "sina_company_news": {
        "enabled": True,
        "source_id": "sina_company_news",
        "upstream": "vip.stock.finance.sina.com.cn",
        "min_interval_seconds": 0.75,
        "max_requests": 3,
        "verify_tls": True,
        "page_url_template": (
            "https://vip.stock.finance.sina.com.cn/corp/go.php/"
            "vCB_AllNewsStock/symbol/{market_symbol}.phtml"
        ),
        "required_container_classes": ["datelist"],
        "forbidden_news_url_patterns": ["/realstock/company/"],
        "symbol_context_from_scoped_page": True,
        "symbol_probes": [
            {"symbol": "600519", "market_symbol": "sh600519"},
            {"symbol": "000001", "market_symbol": "sz000001"},
            {"symbol": "920000", "market_symbol": "bj920000"},
        ],
    },
    "akshare_non_eastmoney": {
        "enabled": True,
        "source_id": "akshare_non_eastmoney",
        "min_interval_seconds": 0.75,
        "max_requests": 3,
        "verify_tls": True,
        "function_probes": [
            {
                "function": "stock_info_global_ths",
                "upstream": "news.10jqka.com.cn",
                "expected_native_fields": ["标题", "内容", "发布时间", "链接"],
            },
            {
                "function": "stock_info_global_cls",
                "upstream": "www.cls.cn",
                "expected_native_fields": ["标题", "内容", "发布日期", "发布时间"],
            },
            {
                "function": "stock_news_main_cx",
                "upstream": "cxdata.caixin.com",
                "expected_native_fields": ["tag", "summary", "url"],
            },
        ],
    },
    "futu_auxiliary": {
        "enabled": True,
        "source_id": "futu_snapshot",
        "upstream": "local_futu_opend",
        "max_requests": 1,
        "allowed_quote_methods": ["get_market_snapshot"],
        "allowed_trade_methods": [],
        "required_signal_fields": [
            "code",
            "change_rate",
            "amplitude",
            "update_time",
        ],
        "symbols": ["SH.600519", "SZ.000001"],
    },
}
_EXPECTED_FORBIDDEN_UPSTREAMS = [
    "eastmoney.com",
    "eastmoney.com.cn",
    "push2.eastmoney.com",
    "push2ex.eastmoney.com",
    "np-weblist.eastmoney.com",
    "search-api-web.eastmoney.com",
]
_EXPECTED_SCOPE_EXCLUSIONS: JsonObject = {
    "eastmoney": {
        "status": "not_probed_by_owner_directed_scope",
        "reason": ("本轮只实测巨潮、新浪、AKShare 非东财上游与富途辅助信号；东财不进入请求路径。"),
    }
}
_EXPECTED_PRIOR_INVALID_EVIDENCE: JsonObject = {
    "report": "docs/phase4/reports/P4.1-source-spike-20260802-invalid-v1.json",
    "report_sha256": "d73673c0b70cab57270cc08f646598bcbaf247f3adfcd3db5b6757c0a46bf5cb",
    "job_run_id": 45453,
    "verdict": "invalid_for_source_feasibility_decision",
    "reason": (
        "新浪解析器把 realstock/company 个股行情页误判为新闻；原始证据保留但不得用于来源晋级。"
    ),
}
_EXPECTED_SAFETY: JsonObject = {
    "required_trading_mode": "research",
    "required_live_trading_enabled": False,
    "required_paper_auto_trading_enabled": False,
    "required_futu_trade_enabled": False,
    "required_futu_account_mutation_enabled": False,
    "required_unlock_trade_blocked": True,
    "allow_trade_proposal_creation": False,
    "allow_broker_order_creation": False,
    "existing_broker_orders_must_all_be_simulate": True,
}
_EXPECTED_DOCUMENT: JsonObject = {
    "schema_version": "p4.1-source-spike-v2",
    "baseline_commit": "e288be683deef67891ebea0b37b508f4eb59b37c",
    "probe_date_shanghai": "2026-08-02",
    "pre_registered_at": "2026-08-01T16:19:42Z",
    "prior_invalid_evidence": _EXPECTED_PRIOR_INVALID_EVIDENCE,
    "network": _EXPECTED_NETWORK,
    "assessment": _EXPECTED_ASSESSMENT,
    "forbidden_upstreams": _EXPECTED_FORBIDDEN_UPSTREAMS,
    "scope_exclusions": _EXPECTED_SCOPE_EXCLUSIONS,
    "sources": _EXPECTED_SOURCES,
    "safety": _EXPECTED_SAFETY,
}


@dataclass(frozen=True, slots=True)
class SourceSpikeConfig:
    path: Path
    sha256: str
    document: JsonObject


class ProbeFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, blocked: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.blocked = blocked


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(
        json.dumps(
            _json_safe(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if pd.notna(value) else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    missing = pd.isna(cast(Any, value))
    if not hasattr(missing, "__len__") and bool(missing):
        return None
    return str(value)


def _host_is_forbidden(host: str, forbidden: set[str]) -> bool:
    normalized = host.lower().rstrip(".")
    return any(normalized == banned or normalized.endswith(f".{banned}") for banned in forbidden)


def _iter_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        result: list[str] = []
        for item in value.values():
            result.extend(_iter_strings(item))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_iter_strings(item))
        return result
    return []


def load_source_spike_config(path: Path) -> SourceSpikeConfig:
    payload = path.read_bytes()
    loaded: object = yaml.safe_load(payload)
    if not isinstance(loaded, dict):
        raise ValueError("P4.1 source-spike config must be a mapping")
    document = cast(JsonObject, loaded)
    if document != _EXPECTED_DOCUMENT:
        raise ValueError("P4.1 v2 config does not match the fully frozen document")
    if document.get("schema_version") != "p4.1-source-spike-v2":
        raise ValueError("unsupported P4.1 source-spike config version")
    if document.get("baseline_commit") != "e288be683deef67891ebea0b37b508f4eb59b37c":
        raise ValueError("P4.1 phase baseline must remain e288be6")

    try:
        date.fromisoformat(str(document["probe_date_shanghai"]))
        registered_at = datetime.fromisoformat(str(document["pre_registered_at"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("P4.1 evidence date contract is invalid") from exc
    if registered_at.tzinfo is None or registered_at.utcoffset() != UTC.utcoffset(registered_at):
        raise ValueError("P4.1 pre-registration timestamp must be UTC")

    if document.get("network") != _EXPECTED_NETWORK:
        raise ValueError("P4.1 frozen network contract changed")
    if document.get("assessment") != _EXPECTED_ASSESSMENT:
        raise ValueError("P4.1 frozen assessment contract changed")
    if document.get("sources") != _EXPECTED_SOURCES:
        raise ValueError("P4.1 frozen source contract changed")

    safety = document.get("safety")
    if safety != _EXPECTED_SAFETY:
        raise ValueError("P4.1 safety contract was weakened")

    forbidden_raw = document.get("forbidden_upstreams")
    if not isinstance(forbidden_raw, list) or not forbidden_raw:
        raise ValueError("P4.1 forbidden upstream list is missing")
    forbidden = {str(item).lower() for item in forbidden_raw}
    for value in _iter_strings(document.get("sources")):
        host = (
            urlparse(value).hostname
            if value.startswith(("http://", "https://"))
            else value
            if "." in value and "/" not in value and " " not in value
            else ""
        ) or ""
        if _host_is_forbidden(host, forbidden):
            raise ValueError(f"forbidden Eastmoney upstream in config: {host}")

    sources = document.get("sources")
    futu_source = sources.get("futu_auxiliary") if isinstance(sources, dict) else None
    if (
        not isinstance(futu_source, dict)
        or futu_source.get("allowed_quote_methods") != ["get_market_snapshot"]
        or futu_source.get("allowed_trade_methods") != []
        or futu_source.get("max_requests") != 1
        or futu_source.get("required_signal_fields")
        != ["code", "change_rate", "amplitude", "update_time"]
    ):
        raise ValueError("P4.1 Futu contract must remain one quote-only snapshot call")

    scope_exclusions = document.get("scope_exclusions")
    eastmoney_exclusion = (
        scope_exclusions.get("eastmoney") if isinstance(scope_exclusions, dict) else None
    )
    if (
        not isinstance(eastmoney_exclusion, dict)
        or eastmoney_exclusion.get("status") != "not_probed_by_owner_directed_scope"
    ):
        raise ValueError("P4.1 Eastmoney scope exclusion must be explicit")

    return SourceSpikeConfig(
        path=path,
        sha256=_sha256_bytes(payload),
        document=document,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _looks_like_antibot(text: str) -> bool:
    lowered = text[:8000].lower()
    markers = (
        "captcha",
        "access denied",
        "verify you are human",
        "访问过于频繁",
        "安全验证",
        "请输入验证码",
    )
    return any(marker in lowered for marker in markers)


class _RequestBudget:
    def __init__(
        self,
        *,
        source_id: str,
        client: Any,
        max_requests: int,
        min_interval_seconds: float,
        forbidden_hosts: set[str],
    ) -> None:
        self.source_id = source_id
        self.client = client
        self.max_requests = max_requests
        self.min_interval_seconds = min_interval_seconds
        self.forbidden_hosts = forbidden_hosts
        self.request_count = 0
        self.last_started: float | None = None
        self.requests: list[JsonObject] = []

    def request(self, method: str, url: str, **kwargs: object) -> tuple[Any, JsonObject]:
        host = (urlparse(url).hostname or "").lower()
        if not host:
            raise ProbeFailure("url_invalid", f"{self.source_id} URL has no host")
        if _host_is_forbidden(host, self.forbidden_hosts):
            raise ProbeFailure(
                "forbidden_upstream",
                f"{self.source_id} attempted forbidden upstream {host}",
                blocked=True,
            )
        if self.request_count >= self.max_requests:
            raise ProbeFailure(
                "request_budget_exhausted",
                f"{self.source_id} exceeded request budget {self.max_requests}",
            )
        if self.last_started is not None:
            remaining = self.min_interval_seconds - (monotonic() - self.last_started)
            if remaining > 0:
                sleep(remaining)

        requested_at = _utc_now()
        started = monotonic()
        self.last_started = started
        self.request_count += 1
        evidence: JsonObject = {
            "request_number": self.request_count,
            "method": method.upper(),
            "host": host,
            "path": urlparse(url).path,
            "requested_at": _iso_utc(requested_at),
            "response_received_at": None,
            "parsed_at": None,
            "latency_ms": None,
            "http_status": None,
            "content_type": None,
            "failure_code": None,
        }
        self.requests.append(evidence)
        try:
            response = self.client.request(method, url, **kwargs)
            read = getattr(response, "read", None)
            if callable(read):
                read()
        except httpx.TimeoutException as exc:
            evidence["response_received_at"] = _iso_utc(_utc_now())
            evidence["latency_ms"] = round((monotonic() - started) * 1000, 3)
            evidence["failure_code"] = "transport_timeout"
            raise ProbeFailure("transport_timeout", type(exc).__name__) from exc
        except httpx.TransportError as exc:
            evidence["response_received_at"] = _iso_utc(_utc_now())
            evidence["latency_ms"] = round((monotonic() - started) * 1000, 3)
            evidence["failure_code"] = "transport_error"
            raise ProbeFailure("transport_error", type(exc).__name__) from exc
        except Exception as exc:
            evidence["response_received_at"] = _iso_utc(_utc_now())
            evidence["latency_ms"] = round((monotonic() - started) * 1000, 3)
            evidence["failure_code"] = "client_error"
            raise ProbeFailure("client_error", type(exc).__name__) from exc

        received_at = _utc_now()
        status_code = int(getattr(response, "status_code", 0))
        headers = getattr(response, "headers", {})
        evidence.update(
            {
                "response_received_at": _iso_utc(received_at),
                "latency_ms": round((monotonic() - started) * 1000, 3),
                "http_status": status_code,
                "content_type": str(headers.get("content-type") or ""),
                "retry_after": str(headers.get("retry-after") or "") or None,
            }
        )
        if status_code in {403, 429}:
            code = "http_forbidden_or_antibot" if status_code == 403 else "http_rate_limited"
            evidence["failure_code"] = code
            raise ProbeFailure(code, f"HTTP {status_code}", blocked=True)
        if status_code >= 500:
            evidence["failure_code"] = "http_server_error"
            raise ProbeFailure("http_server_error", f"HTTP {status_code}")
        if status_code >= 400:
            evidence["failure_code"] = "http_client_error"
            raise ProbeFailure("http_client_error", f"HTTP {status_code}")
        if 300 <= status_code < 400:
            location = str(headers.get("location") or "")
            redirect_host = (urlparse(location).hostname or "").lower()
            evidence["redirect_host"] = redirect_host or None
            code = (
                "forbidden_redirect"
                if redirect_host and _host_is_forbidden(redirect_host, self.forbidden_hosts)
                else "redirect_not_followed"
            )
            evidence["failure_code"] = code
            raise ProbeFailure(code, f"HTTP {status_code} redirect was not followed")
        text = str(getattr(response, "text", "") or "")
        if _looks_like_antibot(text):
            evidence["failure_code"] = "http_forbidden_or_antibot"
            raise ProbeFailure(
                "http_forbidden_or_antibot",
                "anti-bot response body detected",
                blocked=True,
            )
        return response, evidence

    @staticmethod
    def mark_observed(evidence: JsonObject) -> str:
        observed_at = _iso_utc(_utc_now())
        evidence["parsed_at"] = observed_at
        return observed_at


def _decode_json(response: Any, evidence: JsonObject) -> object:
    try:
        payload: object = response.json()
    except Exception as exc:
        evidence["failure_code"] = "decode_error"
        _RequestBudget.mark_observed(evidence)
        raise ProbeFailure("decode_error", type(exc).__name__) from exc
    _RequestBudget.mark_observed(evidence)
    return payload


def _sample(
    *,
    source: str,
    symbol: str | None,
    title: str | None,
    url: str | None,
    published_at: str | None,
    observed_at: str,
    raw: object,
) -> JsonObject:
    normalized = {
        "source": source,
        "symbol": symbol,
        "title": title,
        "url": url,
        "published_at": published_at,
    }
    return {
        **normalized,
        "observed_at": observed_at,
        "available_time": None,
        "content_hash": _canonical_sha256(normalized),
        "raw_payload_sha256": _canonical_sha256(raw),
    }


def _failure(exc: Exception) -> JsonObject:
    if isinstance(exc, ProbeFailure):
        return {
            "code": exc.code,
            "blocked": exc.blocked,
            "error_type": type(exc).__name__,
            "message": str(exc)[:300],
        }
    return {
        "code": "unexpected_error",
        "blocked": False,
        "error_type": type(exc).__name__,
        "message": str(exc)[:300],
    }


def _stop_source_after(exc: Exception) -> bool:
    return isinstance(exc, ProbeFailure) and (exc.blocked or exc.code == "request_budget_exhausted")


def _source_status(
    samples: list[JsonObject],
    failures: list[JsonObject],
    *,
    requires_primary_fields: bool = True,
) -> str:
    if samples:
        if not requires_primary_fields:
            base_status = "usable_auxiliary"
        elif any(item.get("title") and item.get("url") for item in samples):
            base_status = "usable_primary"
        else:
            base_status = "usable_auxiliary"
        return f"{base_status}_degraded" if failures else base_status
    if failures and all(bool(item.get("blocked")) for item in failures):
        return "blocked"
    return "unavailable"


def _default_http_client_factory(
    *,
    network: Mapping[str, object],
    verify_tls: bool,
) -> httpx.Client:
    timeout = httpx.Timeout(
        connect=float(str(network["connect_timeout_seconds"])),
        read=float(str(network["read_timeout_seconds"])),
        write=float(str(network["write_timeout_seconds"])),
        pool=float(str(network["pool_timeout_seconds"])),
    )
    return httpx.Client(
        timeout=timeout,
        trust_env=False,
        follow_redirects=False,
        verify=verify_tls,
        headers={"User-Agent": str(network["user_agent"])},
    )


def _close_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _probe_cninfo(
    config: SourceSpikeConfig,
    factory: HttpClientFactory | None,
    forbidden: set[str],
) -> JsonObject:
    sources = cast(dict[str, Any], config.document["sources"])
    source = cast(dict[str, Any], sources["cninfo"])
    network = cast(dict[str, object], config.document["network"])
    client = (
        factory(network=network, verify_tls=bool(source["verify_tls"]))
        if factory is not None
        else _default_http_client_factory(
            network=network,
            verify_tls=bool(source["verify_tls"]),
        )
    )
    budget = _RequestBudget(
        source_id=str(source["source_id"]),
        client=client,
        max_requests=int(source["max_requests"]),
        min_interval_seconds=float(source["min_interval_seconds"]),
        forbidden_hosts=forbidden,
    )
    failures: list[JsonObject] = []
    samples: list[JsonObject] = []
    observed_fields: set[str] = set()
    probes: list[JsonObject] = []
    max_samples = int(cast(dict[str, Any], config.document["assessment"])["max_samples_per_probe"])
    end_date = date.fromisoformat(str(config.document["probe_date_shanghai"]))
    start_date = end_date.fromordinal(end_date.toordinal() - int(source["lookback_days"]))

    def announcement_request(
        *,
        symbol: str | None,
        org_id: str | None,
        column: str,
        label: str,
    ) -> bool:
        try:
            response, evidence = budget.request(
                "POST",
                str(source["announcements_url"]),
                data={
                    "pageNum": 1,
                    "pageSize": int(source["page_size"]),
                    "column": column,
                    "tabName": "fulltext",
                    "stock": f"{symbol},{org_id}" if symbol and org_id else "",
                    "seDate": f"{start_date.isoformat()}~{end_date.isoformat()}",
                    "isHLtitle": "false",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            payload = _decode_json(response, evidence)
            if not isinstance(payload, dict):
                raise ProbeFailure("schema_changed", "cninfo response is not an object")
            rows = payload.get("announcements") or []
            if not isinstance(rows, list):
                raise ProbeFailure("schema_changed", "cninfo announcements is not a list")
            valid = 0
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                observed_fields.update(str(key) for key in raw)
                title = str(raw.get("announcementTitle") or "").strip()
                adjunct = str(raw.get("adjunctUrl") or "").strip()
                if not title or not adjunct:
                    continue
                timestamp = raw.get("announcementTime")
                published_at = (
                    _iso_utc(datetime.fromtimestamp(float(timestamp) / 1000, tz=UTC))
                    if isinstance(timestamp, (int, float))
                    else None
                )
                observed_at = str(evidence["parsed_at"])
                if valid < max_samples:
                    samples.append(
                        _sample(
                            source=str(source["source_id"]),
                            symbol=str(raw.get("secCode") or symbol or "") or None,
                            title=title,
                            url=urljoin(str(source["static_url_prefix"]), adjunct),
                            published_at=published_at,
                            observed_at=observed_at,
                            raw=raw,
                        )
                    )
                valid += 1
            probes.append(
                {
                    "probe": label,
                    "status": "ok",
                    "records": len(rows),
                    "valid_records": valid,
                    "total_announcement": payload.get("totalAnnouncement"),
                    "has_more": payload.get("hasMore"),
                }
            )
            return True
        except Exception as exc:
            failure = {**_failure(exc), "probe": label}
            failures.append(failure)
            probes.append({"probe": label, "status": "failed", "failure": failure})
            return not _stop_source_after(exc)

    try:
        source_can_continue = True
        for raw_probe in cast(list[dict[str, Any]], source["symbol_probes"]):
            symbol = str(raw_probe["symbol"])
            label = f"symbol:{symbol}"
            try:
                response, evidence = budget.request(
                    "POST",
                    str(source["top_search_url"]),
                    data={"keyWord": symbol, "maxNum": 10},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                payload = _decode_json(response, evidence)
                if not isinstance(payload, list):
                    raise ProbeFailure("schema_changed", "cninfo topSearch is not a list")
                org_id = next(
                    (
                        str(row["orgId"])
                        for row in payload
                        if isinstance(row, dict)
                        and str(row.get("code") or "") == symbol
                        and row.get("orgId")
                    ),
                    None,
                )
                if org_id is None:
                    raise ProbeFailure("org_not_found", f"cninfo orgId not found for {symbol}")
            except Exception as exc:
                failure = {**_failure(exc), "probe": f"{label}:top_search"}
                failures.append(failure)
                probes.append(
                    {"probe": f"{label}:top_search", "status": "failed", "failure": failure}
                )
                if _stop_source_after(exc):
                    source_can_continue = False
                    break
                continue
            source_can_continue = announcement_request(
                symbol=symbol,
                org_id=org_id,
                column=str(raw_probe["column"]),
                label=f"{label}:announcements",
            )
            if not source_can_continue:
                break
        if source_can_continue:
            for column in cast(list[str], source["global_columns"]):
                source_can_continue = announcement_request(
                    symbol=None,
                    org_id=None,
                    column=str(column),
                    label=f"global:{column}",
                )
                if not source_can_continue:
                    break
    finally:
        _close_client(client)

    return {
        "source_id": source["source_id"],
        "upstream": source["upstream"],
        "adapter": "bounded-direct-cninfo-public-two-step",
        "status": _source_status(samples, failures),
        "request_count": budget.request_count,
        "request_budget": budget.max_requests,
        "min_interval_seconds": budget.min_interval_seconds,
        "retry_count": 0,
        "tls_verification": bool(source["verify_tls"]),
        "records_sampled": len(samples),
        "observed_fields": sorted(observed_fields),
        "samples": samples,
        "probes": probes,
        "requests": budget.requests,
        "failures": failures,
        "limitations": [
            "TLS verification is disabled to match the existing cninfo client workaround.",
            "One bounded weekend spike does not establish production rate-limit capacity.",
        ],
    }


class _ScopedAnchorCollector(HTMLParser):
    def __init__(self, required_container_classes: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.required_container_classes = required_container_classes
        self._stack: list[tuple[str, bool]] = []
        self._scope_depth = 0
        self._href: str | None = None
        self._text: list[str] = []
        self.all_anchor_count = 0
        self.container_count = 0
        self.anchors: list[tuple[str, str]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.lower()
        values = dict(attrs)
        classes = {item for item in str(values.get("class") or "").split() if item}
        enters_scope = bool(classes.intersection(self.required_container_classes))
        self._stack.append((normalized_tag, enters_scope))
        if enters_scope:
            self._scope_depth += 1
            self.container_count += 1
        if normalized_tag != "a":
            return
        self.all_anchor_count += 1
        if self._scope_depth == 0:
            return
        self._href = values.get("href")
        self._text = []

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            self.anchors.append((self._href, text))
            self._href = None
            self._text = []
        matching_index = next(
            (
                index
                for index in range(len(self._stack) - 1, -1, -1)
                if self._stack[index][0] == normalized_tag
            ),
            None,
        )
        if matching_index is None:
            return
        popped = self._stack[matching_index:]
        del self._stack[matching_index:]
        self._scope_depth -= sum(1 for _, enters_scope in popped if enters_scope)


def _probe_sina(
    config: SourceSpikeConfig,
    factory: HttpClientFactory | None,
    forbidden: set[str],
) -> JsonObject:
    source = cast(
        dict[str, Any],
        cast(dict[str, Any], config.document["sources"])["sina_company_news"],
    )
    network = cast(dict[str, object], config.document["network"])
    client = (
        factory(network=network, verify_tls=bool(source["verify_tls"]))
        if factory is not None
        else _default_http_client_factory(
            network=network,
            verify_tls=bool(source["verify_tls"]),
        )
    )
    budget = _RequestBudget(
        source_id=str(source["source_id"]),
        client=client,
        max_requests=int(source["max_requests"]),
        min_interval_seconds=float(source["min_interval_seconds"]),
        forbidden_hosts=forbidden,
    )
    failures: list[JsonObject] = []
    samples: list[JsonObject] = []
    probes: list[JsonObject] = []
    max_samples = int(cast(dict[str, Any], config.document["assessment"])["max_samples_per_probe"])
    required_classes = {str(item) for item in cast(list[str], source["required_container_classes"])}
    forbidden_patterns = tuple(
        str(item) for item in cast(list[str], source["forbidden_news_url_patterns"])
    )
    try:
        for raw_probe in cast(list[dict[str, Any]], source["symbol_probes"]):
            symbol = str(raw_probe["symbol"])
            market_symbol = str(raw_probe["market_symbol"])
            url = str(source["page_url_template"]).format(market_symbol=market_symbol)
            try:
                response, evidence = budget.request("GET", url)
                parser = _ScopedAnchorCollector(required_classes)
                parser.feed(str(getattr(response, "text", "") or ""))
                observed_at = budget.mark_observed(evidence)
                if parser.container_count == 0:
                    raise ProbeFailure(
                        "schema_changed",
                        (f"Sina company-news container not found: {sorted(required_classes)}"),
                    )
                seen: set[str] = set()
                matched = 0
                for raw_href, title in parser.anchors:
                    absolute = urljoin(url, raw_href)
                    parsed = urlparse(absolute)
                    if (
                        absolute in seen
                        or not parsed.hostname
                        or not _host_is_forbidden(
                            parsed.hostname,
                            {"sina.com.cn"},
                        )
                        or absolute.rstrip("/") == url.rstrip("/")
                        or len(title) < 6
                        or any(pattern in parsed.path for pattern in forbidden_patterns)
                        or parsed.path.lower().endswith((".jpg", ".png", ".css", ".js"))
                    ):
                        continue
                    seen.add(absolute)
                    matched += 1
                    if matched <= max_samples:
                        samples.append(
                            _sample(
                                source=str(source["source_id"]),
                                symbol=symbol,
                                title=title,
                                url=absolute,
                                published_at=None,
                                observed_at=observed_at,
                                raw={
                                    "href": raw_href,
                                    "title": title,
                                    "symbol_context": market_symbol,
                                    "selector_classes": sorted(required_classes),
                                },
                            )
                        )
                probes.append(
                    {
                        "probe": f"symbol:{symbol}",
                        "status": "ok",
                        "all_anchors": parser.all_anchor_count,
                        "news_container_count": parser.container_count,
                        "anchors_in_news_container": len(parser.anchors),
                        "native_news_candidates": matched,
                    }
                )
            except Exception as exc:
                failure = {**_failure(exc), "probe": f"symbol:{symbol}"}
                failures.append(failure)
                probes.append({"probe": f"symbol:{symbol}", "status": "failed", "failure": failure})
                if _stop_source_after(exc):
                    break
    finally:
        _close_client(client)

    return {
        "source_id": source["source_id"],
        "upstream": source["upstream"],
        "adapter": "bounded-direct-sina-company-news-html",
        "status": _source_status(samples, failures),
        "request_count": budget.request_count,
        "request_budget": budget.max_requests,
        "min_interval_seconds": budget.min_interval_seconds,
        "retry_count": 0,
        "records_sampled": len(samples),
        "observed_fields": ["title", "url"],
        "samples": samples,
        "probes": probes,
        "requests": budget.requests,
        "failures": failures,
        "limitations": [
            "The current Sina page contract does not expose a normalized published_at field.",
            "BSE support is a feasibility observation, not assumed from SH/SZ behavior.",
        ],
    }


class _DirectRequestsFacade:
    def __init__(self, budget: _RequestBudget) -> None:
        self.budget = budget

    def get(self, url: str, **kwargs: object) -> Any:
        response, _ = self.budget.request("GET", url, **kwargs)
        return response

    def post(self, url: str, **kwargs: object) -> Any:
        response, _ = self.budget.request("POST", url, **kwargs)
        return response


def _akshare_samples(
    function_name: str,
    frame: pd.DataFrame,
    observed_at: str,
    max_samples: int,
) -> list[JsonObject]:
    samples: list[JsonObject] = []
    for raw in frame.head(max_samples).to_dict(orient="records"):
        record = cast(dict[str, object], raw)
        if function_name == "stock_info_global_ths":
            title = str(record.get("标题") or "").strip() or None
            url = str(record.get("链接") or "").strip() or None
            published_at = str(record.get("发布时间") or "").strip() or None
        elif function_name == "stock_info_global_cls":
            title = str(record.get("标题") or "").strip() or None
            url = None
            published_at = (
                " ".join(
                    part
                    for part in (
                        str(record.get("发布日期") or "").strip(),
                        str(record.get("发布时间") or "").strip(),
                    )
                    if part
                )
                or None
            )
        else:
            title = None
            url = str(record.get("url") or "").strip() or None
            published_at = None
        samples.append(
            _sample(
                source=f"akshare:{function_name}",
                symbol=None,
                title=title,
                url=url,
                published_at=published_at,
                observed_at=observed_at,
                raw=record,
            )
        )
    return samples


def _probe_akshare(
    config: SourceSpikeConfig,
    factory: HttpClientFactory | None,
    forbidden: set[str],
) -> JsonObject:
    source = cast(
        dict[str, Any],
        cast(dict[str, Any], config.document["sources"])["akshare_non_eastmoney"],
    )
    network = cast(dict[str, object], config.document["network"])
    client = (
        factory(network=network, verify_tls=bool(source["verify_tls"]))
        if factory is not None
        else _default_http_client_factory(
            network=network,
            verify_tls=bool(source["verify_tls"]),
        )
    )
    budget = _RequestBudget(
        source_id=str(source["source_id"]),
        client=client,
        max_requests=int(source["max_requests"]),
        min_interval_seconds=float(source["min_interval_seconds"]),
        forbidden_hosts=forbidden,
    )
    failures: list[JsonObject] = []
    children: list[JsonObject] = []
    all_samples: list[JsonObject] = []
    max_samples = int(cast(dict[str, Any], config.document["assessment"])["max_samples_per_probe"])
    try:
        import akshare as ak

        akshare_version = str(getattr(ak, "__version__", "unknown"))
        for probe in cast(list[dict[str, Any]], source["function_probes"]):
            function_name = str(probe["function"])
            stop_source = False
            try:
                function = getattr(ak, function_name)
                source_text = inspect.getsource(function)
                isolated_globals = dict(function.__globals__)
                facade = _DirectRequestsFacade(budget)
                isolated_globals["requests"] = facade

                if function_name == "stock_info_global_cls":

                    def one_shot_json(
                        url: str,
                        max_retries: int = 0,
                        headers: Mapping[str, str] | None = None,
                        **_: object,
                    ) -> object:
                        del max_retries
                        response, evidence = budget.request(
                            "GET",
                            url,
                            headers=dict(headers or {}),
                        )
                        return _decode_json(response, evidence)

                    isolated_globals["make_request_with_retry_json"] = one_shot_json

                isolated = FunctionType(
                    function.__code__,
                    isolated_globals,
                    name=function.__name__,
                    argdefs=function.__defaults__,
                    closure=function.__closure__,
                )
                isolated.__kwdefaults__ = function.__kwdefaults__
                before = len(budget.requests)
                raw_frame = isolated()
                new_requests = budget.requests[before:]
                if len(new_requests) != 1:
                    raise ProbeFailure(
                        "request_contract_changed",
                        (
                            f"{function_name} made {len(new_requests)} requests; "
                            "the frozen contract requires exactly one"
                        ),
                    )
                for evidence in new_requests:
                    if evidence.get("parsed_at") is None:
                        budget.mark_observed(evidence)
                if not isinstance(raw_frame, pd.DataFrame):
                    raise ProbeFailure("schema_changed", "AKShare result is not a DataFrame")
                observed_fields = [str(column) for column in raw_frame.columns]
                expected_fields = {
                    str(item) for item in cast(list[str], probe["expected_native_fields"])
                }
                missing_fields = sorted(expected_fields.difference(observed_fields))
                if missing_fields:
                    raise ProbeFailure(
                        "schema_changed",
                        f"{function_name} missing fields: {missing_fields}",
                    )
                observed_at = str(new_requests[0]["parsed_at"])
                child_samples = _akshare_samples(
                    function_name,
                    raw_frame,
                    observed_at,
                    max_samples,
                )
                all_samples.extend(child_samples)
                child = {
                    "function": function_name,
                    "upstream": probe["upstream"],
                    "akshare_source_sha256": _sha256_bytes(source_text.encode("utf-8")),
                    "status": _source_status(child_samples, []),
                    "records": len(raw_frame),
                    "observed_fields": observed_fields,
                    "samples": child_samples,
                    "failure": None,
                }
            except Exception as exc:
                for evidence in budget.requests:
                    if evidence.get("parsed_at") is None:
                        budget.mark_observed(evidence)
                failure = {**_failure(exc), "probe": function_name}
                stop_source = _stop_source_after(exc)
                failures.append(failure)
                child = {
                    "function": function_name,
                    "upstream": probe["upstream"],
                    "status": "blocked" if failure["blocked"] else "unavailable",
                    "records": 0,
                    "observed_fields": [],
                    "samples": [],
                    "failure": failure,
                }
            children.append(child)
            if stop_source:
                break
    except Exception as exc:
        akshare_version = "unavailable"
        failures.append({**_failure(exc), "probe": "akshare_import"})
    finally:
        _close_client(client)

    child_statuses = {str(child["status"]) for child in children}
    if child_statuses.intersection({"usable_primary", "usable_primary_degraded"}):
        status = "usable_primary_degraded" if failures else "usable_primary"
    elif child_statuses.intersection({"usable_auxiliary", "usable_auxiliary_degraded"}):
        status = "usable_auxiliary_degraded" if failures else "usable_auxiliary"
    elif children and all(child["status"] == "blocked" for child in children):
        status = "blocked"
    else:
        status = "unavailable"
    return {
        "source_id": source["source_id"],
        "upstream": [probe["upstream"] for probe in source["function_probes"]],
        "adapter": "akshare-function-with-bounded-direct-transport",
        "akshare_version": akshare_version,
        "status": status,
        "request_count": budget.request_count,
        "request_budget": budget.max_requests,
        "min_interval_seconds": budget.min_interval_seconds,
        "retry_count": 0,
        "records_sampled": len(all_samples),
        "children": children,
        "requests": budget.requests,
        "failures": failures,
        "limitations": [
            "Installed AKShare code is hashed because the package version may drift on rebuild.",
            "CLS built-in ten-retry helper is replaced by exactly one bounded direct GET.",
        ],
    }


def _probe_futu(
    config: SourceSpikeConfig,
    factory: FutuClientFactory | None,
) -> JsonObject:
    source = cast(
        dict[str, Any],
        cast(dict[str, Any], config.document["sources"])["futu_auxiliary"],
    )
    probe_date = date.fromisoformat(str(config.document["probe_date_shanghai"]))
    failures: list[JsonObject] = []
    samples: list[JsonObject] = []
    method = str(cast(list[str], source["allowed_quote_methods"])[0])
    client = factory() if factory is not None else FutuClient(get_settings())
    requested_at = _utc_now()
    started = monotonic()
    response_received_at: str | None = None
    observed_at: str | None = None
    observed_fields: list[str] = []
    push_event_types: list[str] = []
    try:
        raw = client.quote_call_raw(method, args=[list(source["symbols"])])
        response_received_at = _iso_utc(_utc_now())
        if not isinstance(raw, pd.DataFrame):
            raise ProbeFailure("schema_changed", "Futu snapshot is not a DataFrame")
        observed_fields = [str(column) for column in raw.columns]
        required_fields = [str(item) for item in cast(list[str], source["required_signal_fields"])]
        missing_fields = sorted(set(required_fields).difference(observed_fields))
        if missing_fields:
            raise ProbeFailure(
                "schema_changed",
                f"Futu snapshot missing auxiliary signal fields: {missing_fields}",
            )
        empty_fields = [field for field in required_fields if not bool(raw[field].notna().any())]
        if raw.empty or empty_fields:
            raise ProbeFailure(
                "empty_signal_values",
                f"Futu snapshot has no values for fields: {empty_fields}",
            )
        observed_at = _iso_utc(_utc_now())
        for record in raw.head(3).to_dict(orient="records"):
            code = str(record.get("code") or "")
            samples.append(
                _sample(
                    source=str(source["source_id"]),
                    symbol=code.split(".", 1)[-1] if code else None,
                    title=None,
                    url=None,
                    published_at=None,
                    observed_at=observed_at,
                    raw={
                        key: record.get(key)
                        for key in (
                            "code",
                            "name",
                            "last_price",
                            "change_rate",
                            "amplitude",
                            "volume",
                            "turnover",
                            "update_time",
                        )
                        if key in record
                    },
                )
            )
        capabilities = client.capabilities()
        push_event_types = [str(item) for item in capabilities.get("push_event_types", [])]
    except Exception as exc:
        failures.append(_failure(exc))
    finally:
        _close_client(client)
    latency_ms = round((monotonic() - started) * 1000, 3)
    if response_received_at is None:
        response_received_at = _iso_utc(_utc_now())
    return {
        "source_id": source["source_id"],
        "upstream": source["upstream"],
        "adapter": "audited-futu-opend-quote-bridge",
        "status": _source_status(samples, failures, requires_primary_fields=False),
        "quote_methods_called": [method],
        "trade_methods_called": [],
        "request_count": 1,
        "request_budget": int(source["max_requests"]),
        "records_sampled": len(samples),
        "observed_fields": observed_fields,
        "push_event_types_supported": push_event_types,
        "push_observed": False,
        "requests": [
            {
                "request_number": 1,
                "method": method,
                "requested_at": _iso_utc(requested_at),
                "response_received_at": response_received_at,
                "parsed_at": observed_at,
                "latency_ms": latency_ms,
                "failure_code": failures[0]["code"] if failures else None,
            }
        ],
        "samples": samples,
        "failures": failures,
        "limitations": [
            (
                f"{probe_date.isoformat()} is {probe_date.strftime('%A')}; push latency "
                "and trading-session freshness were not observable."
            ),
            "Futu contributes quote anomaly fields only and has no news-body evidence here.",
        ],
    }


def _safety_snapshot(settings: Settings) -> JsonObject:
    with get_session() as session:
        proposal_rows = session.execute(
            select(
                TradeProposalRecord.proposal_id,
                TradeProposalRecord.status,
                TradeProposalRecord.created_at,
            ).order_by(TradeProposalRecord.proposal_id)
        ).all()
        order_rows = session.execute(
            select(
                BrokerOrder.proposal_id,
                BrokerOrder.futu_order_id,
                BrokerOrder.status,
                BrokerOrder.environment,
                BrokerOrder.created_at,
            ).order_by(BrokerOrder.id)
        ).all()
        non_simulate = int(
            session.scalar(
                select(func.count())
                .select_from(BrokerOrder)
                .where(BrokerOrder.environment != "SIMULATE")
            )
            or 0
        )
    proposal_projection = [_json_safe(tuple(row)) for row in proposal_rows]
    order_projection = [_json_safe(tuple(row)) for row in order_rows]
    return {
        "settings": {
            "trading_mode": settings.trading_mode,
            "live_trading_enabled": settings.live_trading_enabled,
            "paper_auto_trading_enabled": settings.paper_auto_trading_enabled,
            "futu_enable_trade": settings.futu_enable_trade,
            "futu_enable_account_mutation": settings.futu_enable_account_mutation,
            "unlock_trade_permanently_blocked": ("unlock_trade" in PERMANENTLY_BLOCKED_METHODS),
        },
        "trade_proposals": {
            "count": len(proposal_rows),
            "identity_sha256": _canonical_sha256(proposal_projection),
        },
        "broker_orders": {
            "count": len(order_rows),
            "identity_sha256": _canonical_sha256(order_projection),
            "non_simulate_count": non_simulate,
        },
    }


def _safety_issues(snapshot: JsonObject) -> list[str]:
    settings = cast(dict[str, Any], snapshot["settings"])
    expected = {
        "trading_mode": "research",
        "live_trading_enabled": False,
        "paper_auto_trading_enabled": False,
        "futu_enable_trade": False,
        "futu_enable_account_mutation": False,
        "unlock_trade_permanently_blocked": True,
    }
    issues = [
        f"{key}={settings.get(key)!r}, expected {value!r}"
        for key, value in expected.items()
        if settings.get(key) != value
    ]
    orders = cast(dict[str, Any], snapshot["broker_orders"])
    if int(orders["non_simulate_count"]) != 0:
        issues.append("broker_orders contains non-SIMULATE rows")
    return issues


def _collect_samples(sources: Mapping[str, object]) -> list[JsonObject]:
    samples: list[JsonObject] = []
    for raw_source in sources.values():
        if not isinstance(raw_source, dict):
            continue
        for raw in raw_source.get("samples", []):
            if isinstance(raw, dict):
                samples.append(cast(JsonObject, raw))
        for child in raw_source.get("children", []):
            if not isinstance(child, dict):
                continue
            for raw in child.get("samples", []):
                if isinstance(raw, dict):
                    samples.append(cast(JsonObject, raw))
    return samples


def _stamp_source_samples(result: JsonObject, available_time: str) -> None:
    for sample in cast(list[JsonObject], result.get("samples", [])):
        sample["available_time"] = available_time
    for child in cast(list[JsonObject], result.get("children", [])):
        for sample in cast(list[JsonObject], child.get("samples", [])):
            sample["available_time"] = available_time


def _persist_source_evidence(
    stats: JsonObject,
    *,
    source_id: str,
    result: JsonObject,
) -> None:
    context = current_job_run()
    if context is None or context.job_name != "p4_source_spike":
        raise JobExecutionError(
            "P4.1 source evidence must run inside its durable JobRun",
            stats=stats,
        )
    persisted_at = _iso_utc(_utc_now())
    _stamp_source_samples(result, persisted_at)
    result["evidence_persisted_at"] = persisted_at
    result["persistence"] = {
        "job_run_id": context.run_id,
        "source_id": source_id,
        "available_time_policy": "jobrun_evidence_persisted_at_utc",
    }
    with get_session() as session:
        record = session.get(JobRun, context.run_id)
        if record is None or record.status != "running":
            raise JobExecutionError(
                "P4.1 durable JobRun is unavailable during source persistence",
                stats=stats,
            )
        record.stats = cast(JsonObject, _json_safe(stats))


def run_p4_source_spike(
    *,
    config_path: Path,
    expected_config_sha256: str,
    execution_commit: str,
    planned_report_path: str,
    http_client_factory: HttpClientFactory | None = None,
    futu_client_factory: FutuClientFactory | None = None,
) -> JsonObject:
    config = load_source_spike_config(config_path)
    stats: JsonObject = {
        "phase_baseline_commit": config.document["baseline_commit"],
        "execution_commit": execution_commit,
        "config_path": str(config_path),
        "config_sha256": config.sha256,
        "expected_config_sha256": expected_config_sha256,
        "planned_report_path": planned_report_path,
        "prior_invalid_evidence": _json_safe(config.document["prior_invalid_evidence"]),
        "started_at": _iso_utc(_utc_now()),
        "sources": {},
        "source_failures": [],
    }
    if config.sha256 != expected_config_sha256:
        raise JobExecutionError(
            "P4.1 config bytes changed before source requests",
            stats=stats,
        )

    settings = get_settings()
    safety_before = _safety_snapshot(settings)
    stats["safety_before"] = safety_before
    issues = _safety_issues(safety_before)
    if issues:
        stats["safety_issues"] = issues
        raise JobExecutionError(
            "P4.1 safety preflight failed",
            stats=stats,
        )

    forbidden = {
        str(item).lower() for item in cast(list[str], config.document["forbidden_upstreams"])
    }
    probe_functions: list[tuple[str, Callable[[], JsonObject]]] = [
        (
            "cninfo",
            lambda: _probe_cninfo(config, http_client_factory, forbidden),
        ),
        (
            "sina_company_news",
            lambda: _probe_sina(config, http_client_factory, forbidden),
        ),
        (
            "akshare_non_eastmoney",
            lambda: _probe_akshare(config, http_client_factory, forbidden),
        ),
        (
            "futu_auxiliary",
            lambda: _probe_futu(config, futu_client_factory),
        ),
    ]
    source_results: dict[str, JsonObject] = {}
    for source_id, probe in probe_functions:
        try:
            result = probe()
        except Exception as exc:
            result = {
                "source_id": source_id,
                "status": "blocked" if _failure(exc)["blocked"] else "unavailable",
                "samples": [],
                "failures": [_failure(exc)],
            }
        source_results[source_id] = result
        failure_codes = {
            str(failure.get("code"))
            for failure in cast(list[dict[str, Any]], result.get("failures", []))
        }
        result["rate_limited"] = bool(
            failure_codes.intersection(
                {
                    "http_rate_limited",
                    "http_forbidden_or_antibot",
                }
            )
        )
        sampled_urls = [
            str(sample["url"])
            for sample in _collect_samples({source_id: result})
            if sample.get("url")
        ]
        result["sample_url_audit"] = {
            "url_count": len(sampled_urls),
            "unique_url_count": len(set(sampled_urls)),
            "duplicate_url_instances": len(sampled_urls) - len(set(sampled_urls)),
            "scope": "bounded_samples_only",
        }
        stats["sources"] = source_results
        stats["source_failures"] = [
            {"source_id": current_source_id, **failure}
            for current_source_id, current_result in source_results.items()
            for failure in cast(
                list[dict[str, Any]],
                current_result.get("failures", []),
            )
        ]
        _persist_source_evidence(
            stats,
            source_id=source_id,
            result=result,
        )
    stats["sources"] = source_results
    stats["source_failures"] = [
        {"source_id": source_id, **failure}
        for source_id, result in source_results.items()
        for failure in cast(list[dict[str, Any]], result.get("failures", []))
    ]

    safety_after = _safety_snapshot(settings)
    stats["safety_after"] = safety_after
    stats["safety_unchanged"] = safety_before == safety_after
    post_issues = _safety_issues(safety_after)
    if safety_before != safety_after or post_issues:
        stats["safety_issues"] = post_issues or ["trade proposal/order identity changed"]
        raise JobExecutionError(
            "P4.1 safety postflight failed",
            stats=stats,
        )

    samples = _collect_samples(source_results)
    available_count = sum(
        1
        for item in samples
        if isinstance(item.get("available_time"), str)
        and str(item["available_time"]).endswith("+00:00")
    )
    copied_count = sum(
        1
        for item in samples
        if item.get("published_at") is not None
        and item.get("published_at") == item.get("available_time")
    )
    stats["pit_audit"] = {
        "sample_count": len(samples),
        "available_time_utc_count": available_count,
        "available_time_coverage": (round(available_count / len(samples), 6) if samples else None),
        "available_time_equals_published_at_count": copied_count,
        "policy": "jobrun_evidence_persisted_at_utc",
        "zero_sample_result": "not_applicable" if not samples else None,
    }
    probe_date = date.fromisoformat(str(config.document["probe_date_shanghai"]))
    stats["weekend_limitation"] = (
        f"{probe_date.isoformat()} is {probe_date.strftime('%A')}; "
        "connectivity/schema evidence does not establish trading-session latency, "
        "push freshness, or three-day stability."
    )
    stats["scope_exclusions"] = _json_safe(config.document["scope_exclusions"])
    stats["finished_at"] = _iso_utc(_utc_now())
    return stats


def register_p4_source_spike_job() -> None:
    register(
        JobSpec(
            name="p4_source_spike",
            func=run_p4_source_spike,
            trigger=None,
        )
    )
