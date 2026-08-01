from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from jsonschema import Draft202012Validator

from alphapilot.backtest.data_health import (
    EXTERNAL_PAIRING_SCHEMA,
    EXTERNAL_PAIRING_SCHEMA_VERSION,
    PIT_ALLOWED_EXTERNAL_SOURCES_BY_TABLE,
    PIT_CHECKED_FIELDS,
    PIT_MANIFEST_SCHEMA_VERSION,
    PIT_NUMERIC_CHECKED_FIELDS,
    PIT_NUMERIC_TOLERANCE_POLICY,
)
from alphapilot.backtest.external_pit_adjudication import FROZEN_MANIFEST_SHA256

TRIAL_SCHEMA_VERSION = "p3.3-s6-external-pit-trial-v1"
MAX_MANIFEST_REPORT_BYTES = 4 * 1024 * 1024
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
_BAOSTOCK_MARKER = "baostock"
_TABLE_ORDER = ("daily_bars", "financial_indicators", "valuation_daily")
_REPORT_KEY = {
    "daily_bars": "daily_bars_with_adj",
    "financial_indicators": "financial_indicators",
    "valuation_daily": "valuation_daily",
}
_KEY_FIELDS = {
    "daily_bars": ("symbol", "trade_date"),
    "financial_indicators": ("symbol", "report_period", "metric"),
    "valuation_daily": ("symbol", "trade_date"),
}
_MANIFEST_ATTESTATION_FIELDS = frozenset({"source", "adj_source"})
_DAILY_NORMALIZATION_FIELDS = ("adj_anchor_date", "adj_anchor_factor")
_EM_FINANCIAL_URL = "https://datacenter.eastmoney.com/securities/api/data/get"
_TUSHARE_URL = "http://api.tushare.pro"


class ExternalPITError(RuntimeError):
    """Base error for fail-closed external PIT pairing."""


class ManifestValidationError(ExternalPITError):
    """The S6 local PIT manifest cannot be trusted."""


class ExternalSourceError(ExternalPITError):
    """An external source did not return an auditable observation."""


@dataclass(frozen=True, slots=True)
class Tolerance:
    absolute: float
    relative: float = 0.0

    def resolve(self, local_value: float, external_value: float) -> float:
        return max(
            self.absolute,
            self.relative * max(abs(local_value), abs(external_value)),
        )


DEFAULT_TOLERANCES: dict[str, Tolerance] = {
    field: Tolerance(absolute=absolute, relative=relative)
    for field, (absolute, relative) in PIT_NUMERIC_TOLERANCE_POLICY.items()
}


@dataclass(frozen=True, slots=True)
class PITManifest:
    selection: str
    seed: int
    sample_size_per_table: int
    manifest_schema_version: str
    manifest_sha256: str
    samples: dict[str, list[dict[str, Any]]]
    report_basename: str
    report_sha256: str

    @property
    def sample_count(self) -> int:
        return sum(len(self.samples[table]) for table in _TABLE_ORDER)


@dataclass(frozen=True, slots=True)
class ExternalObservation:
    external_source: str
    values: Mapping[str, object]


class ExternalPITSources(Protocol):
    def route_for(self, table: str, sample: Mapping[str, object]) -> str: ...

    def fetch(self, table: str, sample: Mapping[str, object]) -> ExternalObservation: ...


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ManifestValidationError(f"duplicate JSON key is not allowed: {key}")
        document[key] = value
    return document


def _load_strict_json(path: Path) -> tuple[dict[str, Any], bytes]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"S6 manifest report not found: {resolved}")
    size = resolved.stat().st_size
    if size <= 0:
        raise ManifestValidationError("S6 manifest report must not be empty")
    if size > MAX_MANIFEST_REPORT_BYTES:
        raise ManifestValidationError(
            f"S6 manifest report exceeds {MAX_MANIFEST_REPORT_BYTES} bytes"
        )
    payload = resolved.read_bytes()
    if len(payload) != size:
        raise ManifestValidationError("S6 manifest report changed while being read")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestValidationError("S6 manifest report must be UTF-8") from exc

    def reject_non_finite(value: str) -> None:
        raise ManifestValidationError(f"non-finite JSON number is not allowed: {value}")

    try:
        document = json.loads(
            text,
            object_pairs_hook=_strict_json_object,
            parse_constant=reject_non_finite,
        )
    except json.JSONDecodeError as exc:
        raise ManifestValidationError("S6 manifest report must be strict JSON") from exc
    if not isinstance(document, dict):
        raise ManifestValidationError("S6 manifest report root must be an object")
    return document, payload


def _canonical_manifest_payload(pit_samples: Mapping[str, Any]) -> dict[str, Any]:
    required = (
        "selection",
        "seed",
        "sample_size_per_table",
        "daily_bars_with_adj",
        "financial_indicators",
        "valuation_daily",
    )
    missing = [field for field in required if field not in pit_samples]
    if missing:
        raise ManifestValidationError(
            f"S6 PIT manifest is missing fields: {', '.join(missing)}"
        )
    return {
        "schema_version": PIT_MANIFEST_SCHEMA_VERSION,
        "selection": pit_samples["selection"],
        "seed": pit_samples["seed"],
        "sample_size_per_table": pit_samples["sample_size_per_table"],
        "daily_bars_with_adj": pit_samples["daily_bars_with_adj"],
        "financial_indicators": pit_samples["financial_indicators"],
        "valuation_daily": pit_samples["valuation_daily"],
    }


def _manifest_sha256(pit_samples: Mapping[str, Any]) -> str:
    try:
        canonical = json.dumps(
            _canonical_manifest_payload(pit_samples),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ManifestValidationError("S6 PIT manifest is not canonical JSON") from exc
    return hashlib.sha256(canonical).hexdigest()


def _sample_key(table: str, sample: Mapping[str, object]) -> dict[str, str]:
    try:
        fields = _KEY_FIELDS[table]
    except KeyError as exc:
        raise ManifestValidationError(f"unsupported PIT table: {table}") from exc
    key = {field: str(sample.get(field) or "").strip() for field in fields}
    if any(not value for value in key.values()):
        raise ManifestValidationError(f"{table} sample contains a blank business key")
    return key


def load_pit_manifest(report_path: Path) -> PITManifest:
    document, report_bytes = _load_strict_json(report_path)
    raw_samples = document.get("pit_samples")
    if not isinstance(raw_samples, dict):
        raise ManifestValidationError("S6 report pit_samples must be an object")
    schema_version = str(raw_samples.get("manifest_schema_version") or "")
    if schema_version != PIT_MANIFEST_SCHEMA_VERSION:
        raise ManifestValidationError(
            "S6 PIT manifest schema mismatch: "
            f"expected={PIT_MANIFEST_SCHEMA_VERSION}, observed={schema_version or '<blank>'}"
        )
    expected_sha256 = str(raw_samples.get("manifest_sha256") or "")
    observed_sha256 = _manifest_sha256(raw_samples)
    if expected_sha256 != observed_sha256:
        raise ManifestValidationError(
            "S6 PIT manifest SHA-256 does not match its selected local values"
        )
    sample_size = raw_samples.get("sample_size_per_table")
    seed = raw_samples.get("seed")
    if isinstance(sample_size, bool) or not isinstance(sample_size, int) or sample_size <= 0:
        raise ManifestValidationError("sample_size_per_table must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ManifestValidationError("seed must be an integer")

    samples: dict[str, list[dict[str, Any]]] = {}
    identities: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for table in _TABLE_ORDER:
        report_key = _REPORT_KEY[table]
        rows = raw_samples.get(report_key)
        if not isinstance(rows, list) or len(rows) != sample_size:
            raise ManifestValidationError(
                f"{report_key} must contain exactly {sample_size} samples"
            )
        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ManifestValidationError(f"{report_key} sample must be an object")
            key = _sample_key(table, row)
            identity = (table, tuple(sorted(key.items())))
            if identity in identities:
                raise ManifestValidationError(
                    f"S6 PIT manifest contains duplicate sample key: {table} {key}"
                )
            identities.add(identity)
            missing_fields = [
                field
                for field in PIT_CHECKED_FIELDS[table]
                if field not in row
            ]
            if table == "daily_bars":
                missing_fields.extend(
                    field
                    for field in _DAILY_NORMALIZATION_FIELDS
                    if field not in row
                )
            if missing_fields:
                raise ManifestValidationError(
                    f"{table} sample is missing required fields: {missing_fields}"
                )
            normalized.append(dict(row))
        samples[table] = normalized

    return PITManifest(
        selection=str(raw_samples["selection"]),
        seed=seed,
        sample_size_per_table=sample_size,
        manifest_schema_version=schema_version,
        manifest_sha256=observed_sha256,
        samples=samples,
        report_basename=report_path.name,
        report_sha256=hashlib.sha256(report_bytes).hexdigest(),
    )


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _numeric_check(
    field: str,
    local_value: object,
    external_value: object,
    tolerances: Mapping[str, Tolerance],
) -> dict[str, object]:
    local_number = _finite_number(local_value)
    external_number = _finite_number(external_value)
    if local_value is None and external_value is None:
        return {
            "local_value": None,
            "external_value": "N/A",
            "pass": True,
            "tolerance": 0.0,
        }
    if local_number is None or external_number is None:
        return {
            "local_value": local_number,
            "external_value": external_number if external_number is not None else "N/A",
            "pass": False,
            "tolerance": 0.0,
        }
    tolerance = tolerances[field].resolve(local_number, external_number)
    return {
        "local_value": local_number,
        "external_value": external_number,
        "pass": abs(local_number - external_number) <= tolerance,
        "tolerance": tolerance,
    }


def _string_check(local_value: object, external_value: object) -> dict[str, object]:
    local_text = str(local_value or "")
    external_text = str(external_value or "")
    return {
        "local_value": local_text,
        "external_value": external_text or "N/A",
        "pass": bool(local_text) and local_text == external_text,
    }


def _safe_external_source(source: object) -> str:
    text = str(source or "").strip()
    if not text:
        raise ExternalSourceError("external source identifier must not be blank")
    if _BAOSTOCK_MARKER in text.casefold():
        raise ExternalSourceError("BaoStock is forbidden for external PIT pairing")
    return text


def _failed_checks(
    table: str,
    sample: Mapping[str, object],
    tolerances: Mapping[str, Tolerance],
) -> dict[str, dict[str, object]]:
    checks: dict[str, dict[str, object]] = {}
    for field in PIT_CHECKED_FIELDS[table]:
        if field in PIT_NUMERIC_CHECKED_FIELDS:
            checks[field] = _numeric_check(
                field,
                sample.get(field),
                None,
                tolerances,
            )
        else:
            checks[field] = _string_check(sample.get(field), None)
    return checks


def _checked_values(
    table: str,
    sample: Mapping[str, object],
    observation: ExternalObservation,
    tolerances: Mapping[str, Tolerance],
) -> dict[str, dict[str, object]]:
    checks: dict[str, dict[str, object]] = {}
    for field in PIT_CHECKED_FIELDS[table]:
        local_value = sample.get(field)
        if field in PIT_NUMERIC_CHECKED_FIELDS:
            checks[field] = _numeric_check(
                field,
                local_value,
                observation.values.get(field),
                tolerances,
            )
            continue
        # source/adj_source attest the provenance frozen into the signed local
        # manifest. The independent provider identity is external_source.
        external_value = (
            local_value
            if field in _MANIFEST_ATTESTATION_FIELDS
            else observation.values.get(field)
        )
        checks[field] = _string_check(local_value, external_value)
    return checks


def build_trial_document(
    manifest: PITManifest,
    sources: ExternalPITSources,
    *,
    tolerances: Mapping[str, Tolerance] = DEFAULT_TOLERANCES,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    missing_tolerances = PIT_NUMERIC_CHECKED_FIELDS.difference(tolerances)
    if missing_tolerances:
        raise ValueError(f"missing numeric tolerances: {sorted(missing_tolerances)}")
    generated = generated_at or datetime.now(UTC)
    if generated.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")

    trial_samples: list[dict[str, Any]] = []
    for table in _TABLE_ORDER:
        for sample in manifest.samples[table]:
            key = _sample_key(table, sample)
            route = _safe_external_source(sources.route_for(table, sample))
            if route not in PIT_ALLOWED_EXTERNAL_SOURCES_BY_TABLE[table]:
                raise ExternalPITError(
                    f"external route is not audited for {table}: {route}"
                )
            try:
                observation = sources.fetch(table, sample)
                source_name = _safe_external_source(observation.external_source)
                if source_name not in PIT_ALLOWED_EXTERNAL_SOURCES_BY_TABLE[table]:
                    raise ExternalPITError(
                        f"external observation route is not audited for "
                        f"{table}: {source_name}"
                    )
                checks = _checked_values(
                    table,
                    sample,
                    observation,
                    tolerances,
                )
                passed = all(bool(check["pass"]) for check in checks.values())
                trial_samples.append(
                    {
                        "table": table,
                        "key": key,
                        "verdict": "match" if passed else "mismatch",
                        "external_source": source_name,
                        "checked_values": checks,
                    }
                )
            except Exception as exc:
                # Fail closed without serializing upstream error text, which may
                # contain credentials, query strings, or proxy details.
                trial_samples.append(
                    {
                        "table": table,
                        "key": key,
                        "verdict": "unavailable",
                        "external_source": route,
                        "checked_values": _failed_checks(
                            table,
                            sample,
                            tolerances,
                        ),
                        "error_type": type(exc).__name__,
                    }
                )

    matches = sum(sample["verdict"] == "match" for sample in trial_samples)
    mismatches = sum(sample["verdict"] == "mismatch" for sample in trial_samples)
    unavailable = sum(sample["verdict"] == "unavailable" for sample in trial_samples)
    eligible = matches == manifest.sample_count
    return {
        "schema_version": TRIAL_SCHEMA_VERSION,
        "mode": "trial",
        "approved": False,
        "generated_at": generated.isoformat(),
        "purpose": "architect_review_only_not_accepted_by_s6",
        "pit_manifest_schema_version": manifest.manifest_schema_version,
        "pit_manifest_sha256": manifest.manifest_sha256,
        "manifest_report": {
            "basename": manifest.report_basename,
            "sha256": manifest.report_sha256,
        },
        "seed": manifest.seed,
        "sample_size_per_table": manifest.sample_size_per_table,
        "metadata_semantics": {
            "source_fields": (
                "manifest_provenance_attestation_bound_by_manifest_sha256"
            ),
            "external_provider_identity": "external_source",
        },
        "secrets_included": False,
        "samples": trial_samples,
        "summary": {
            "sample_count": manifest.sample_count,
            "match": matches,
            "mismatch": mismatches,
            "unavailable": unavailable,
            "eligible_for_v2_signing": eligible,
        },
    }


def build_signed_evidence_v2(
    trial: Mapping[str, Any],
    *,
    reviewer_role: str,
    reviewed_at: datetime,
) -> dict[str, Any]:
    if str(trial.get("pit_manifest_sha256") or "") == FROZEN_MANIFEST_SHA256:
        raise ExternalPITError(
            "the frozen final S6 trial requires offline pairing-v3 adjudication"
        )
    role = reviewer_role.strip()
    if (
        not role
        or any(marker in role.casefold() for marker in ("pending", "trial", "automated"))
    ):
        raise ValueError("reviewer_role must identify a human reviewer role")
    if reviewed_at.utcoffset() is None:
        raise ValueError("reviewed_at must be timezone-aware")
    summary = trial.get("summary")
    if not isinstance(summary, dict) or summary.get("eligible_for_v2_signing") is not True:
        raise ExternalPITError("trial is not eligible for v2 signing")
    raw_samples = trial.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        raise ExternalPITError("trial samples must be a non-empty list")
    evidence_samples: list[dict[str, Any]] = []
    for sample in raw_samples:
        if not isinstance(sample, dict) or sample.get("verdict") != "match":
            raise ExternalPITError("every signed sample must have verdict=match")
        evidence_samples.append(
            {
                "table": sample["table"],
                "key": sample["key"],
                "verdict": "match",
                "external_source": sample["external_source"],
                "checked_values": sample["checked_values"],
            }
        )
    evidence = {
        "schema_version": EXTERNAL_PAIRING_SCHEMA_VERSION,
        "pit_manifest_schema_version": trial["pit_manifest_schema_version"],
        "pit_manifest_sha256": trial["pit_manifest_sha256"],
        "approved": True,
        "reviewed_at": reviewed_at.isoformat(),
        "reviewer_role": role,
        "seed": trial["seed"],
        "sample_size_per_table": trial["sample_size_per_table"],
        "samples": evidence_samples,
    }
    errors = sorted(
        Draft202012Validator(EXTERNAL_PAIRING_SCHEMA).iter_errors(evidence),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise ExternalPITError(
            f"generated evidence violates v2 schema at {location}: {errors[0].message}"
        )
    return evidence


def build_preflight_plan(
    manifest: PITManifest,
    *,
    financial_source: str,
    max_tushare_calls: int,
) -> dict[str, Any]:
    if financial_source not in {"em-f10", "tushare"}:
        raise ValueError("financial_source must be em-f10 or tushare")
    if max_tushare_calls < 0:
        raise ValueError("max_tushare_calls must be non-negative")
    daily_samples = manifest.samples["daily_bars"]
    financial_samples = manifest.samples["financial_indicators"]
    valuation_samples = manifest.samples["valuation_daily"]
    bse_daily = sum(
        str(sample["symbol"]).startswith(("4", "8", "92"))
        for sample in daily_samples
    )
    financial_symbols = {str(sample["symbol"]) for sample in financial_samples}
    tushare_calls = len(financial_symbols) if financial_source == "tushare" else 0
    blockers: list[str] = []
    if tushare_calls > max_tushare_calls:
        blockers.append(
            "TUSHARE_CALL_BUDGET_EXCEEDED:"
            f"required={tushare_calls},limit={max_tushare_calls}"
        )
    return {
        "schema_version": "p3.3-s6-external-pit-preflight-v1",
        "mode": "plan_only",
        "network_called": False,
        "approved": False,
        "pit_manifest_schema_version": manifest.manifest_schema_version,
        "pit_manifest_sha256": manifest.manifest_sha256,
        "sample_count": manifest.sample_count,
        "routes": {
            "daily_bars": {
                "sh_sz": (
                    "Futu unadjusted DAY + Futu HFQ DAY, normalized to "
                    "manifest adjustment anchor"
                ),
                "bse_or_fallback": (
                    "Sina unadjusted DAY + Sina HFQ DAY, normalized to "
                    "manifest adjustment anchor"
                ),
                "futu_planned_samples": len(daily_samples) - bse_daily,
                "sina_planned_samples": bse_daily,
            },
            "valuation_daily": {
                "source": "Eastmoney stock_value_em",
                "planned_symbol_calls": len(
                    {str(sample["symbol"]) for sample in valuation_samples}
                ),
            },
            "financial_indicators": {
                "source": (
                    "Eastmoney F10"
                    if financial_source == "em-f10"
                    else "Tushare fina_indicator"
                ),
                "planned_symbol_calls": len(financial_symbols),
                "tushare_call_limit": max_tushare_calls,
            },
        },
        "forbidden_sources": ["BaoStock"],
        "secrets_included": False,
        "blockers": blockers,
        "ready_for_trial": not blockers,
    }


def _symbol_digits(symbol: object) -> str:
    digits = "".join(character for character in str(symbol) if character.isdigit())
    if len(digits) != 6:
        raise ExternalSourceError(f"unsupported A-share symbol: {symbol!r}")
    return digits


def _market_symbol(symbol: object, *, separator: str = ".") -> str:
    digits = _symbol_digits(symbol)
    if digits.startswith(("4", "8", "92")):
        market = "BJ"
    elif digits.startswith(("5", "6", "9")):
        market = "SH"
    else:
        market = "SZ"
    return f"{market}{separator}{digits}" if separator else f"{market}{digits}"


def _parse_date(value: object, *, label: str) -> date:
    parsed = pd.to_datetime(str(value), errors="coerce")
    if pd.isna(parsed):
        raise ExternalSourceError(f"invalid {label}")
    return pd.Timestamp(parsed).date()


def _utc_naive_text(value: datetime) -> str:
    return value.astimezone(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


def _quarter_end(report_period: object) -> date:
    text = str(report_period)
    if len(text) != 6 or text[4] != "Q" or not text[:4].isdigit() or text[5] not in "1234":
        raise ExternalSourceError("invalid financial report_period")
    year = int(text[:4])
    month_day = {
        "1": (3, 31),
        "2": (6, 30),
        "3": (9, 30),
        "4": (12, 31),
    }[text[5]]
    return date(year, *month_day)


def _direct_get(url: str, **kwargs: Any) -> httpx.Response:
    with httpx.Client(trust_env=False, follow_redirects=True) as client:
        return client.get(url, **kwargs)


def _direct_post(url: str, **kwargs: Any) -> httpx.Response:
    with httpx.Client(trust_env=False, follow_redirects=True) as client:
        return client.post(url, **kwargs)


class LiveExternalPITSources:
    """Non-BaoStock external routes used only by an explicit trial/sign command."""

    def __init__(
        self,
        *,
        financial_source: str = "em-f10",
        tushare_token: str | None = None,
        max_tushare_calls: int = 1,
        futu_client: Any | None = None,
        http_get: Callable[..., Any] = _direct_get,
        http_post: Callable[..., Any] = _direct_post,
        valuation_fetcher: Callable[..., pd.DataFrame] | None = None,
        sina_frame_fetcher: Callable[[str, date, str], pd.DataFrame] | None = None,
    ) -> None:
        if financial_source not in {"em-f10", "tushare"}:
            raise ValueError("financial_source must be em-f10 or tushare")
        if max_tushare_calls < 0:
            raise ValueError("max_tushare_calls must be non-negative")
        self.financial_source = financial_source
        self._tushare_token = (tushare_token or "").strip()
        self._max_tushare_calls = max_tushare_calls
        self._tushare_calls = 0
        self._futu_client = futu_client
        self._http_get = http_get
        self._http_post = http_post
        self._valuation_fetcher = valuation_fetcher
        self._sina_frame_fetcher = sina_frame_fetcher
        self._valuation_cache: dict[tuple[str, date], pd.DataFrame] = {}
        self._financial_cache: dict[str, list[dict[str, Any]]] = {}

    def route_for(self, table: str, sample: Mapping[str, object]) -> str:
        if table == "daily_bars":
            digits = _symbol_digits(sample.get("symbol"))
            return (
                "sina-unadjusted-day+sina-hfq-day"
                if digits.startswith(("4", "8", "92"))
                else "futu-unadjusted-day+futu-hfq-day"
            )
        if table == "valuation_daily":
            return "eastmoney-stock-value-em"
        if table == "financial_indicators":
            return (
                "eastmoney-f10-main-financial"
                if self.financial_source == "em-f10"
                else "tushare-fina-indicator"
            )
        raise ExternalSourceError(f"unsupported external PIT table: {table}")

    def fetch(self, table: str, sample: Mapping[str, object]) -> ExternalObservation:
        if table == "daily_bars":
            return self._fetch_daily(sample)
        if table == "valuation_daily":
            return self._fetch_valuation(sample)
        if table == "financial_indicators":
            return self._fetch_financial(sample)
        raise ExternalSourceError(f"unsupported external PIT table: {table}")

    def _client(self) -> Any:
        if self._futu_client is None:
            from alphapilot.futu.client import get_futu_client

            self._futu_client = get_futu_client()
        return self._futu_client

    def close(self) -> None:
        """Release the lazily opened Futu context after a one-shot trial."""

        client = self._futu_client
        self._futu_client = None
        if client is None:
            return
        close = getattr(client, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _exact_close(frame: pd.DataFrame, target: date, *, date_column: str) -> float:
        if date_column not in frame.columns or "close" not in frame.columns:
            raise ExternalSourceError("external daily frame is missing date/close")
        dates = pd.to_datetime(frame[date_column], errors="coerce").dt.date
        selected = pd.to_numeric(
            frame.loc[dates == target, "close"],
            errors="coerce",
        ).dropna()
        if len(selected) != 1:
            raise ExternalSourceError("external daily frame has no unique target row")
        value = float(selected.iloc[0])
        if not math.isfinite(value) or value <= 0:
            raise ExternalSourceError("external daily close must be positive and finite")
        return value

    def _futu_close(self, symbol: str, target: date, autype: str) -> float:
        result = self._client().quote_call_raw(
            "request_history_kline",
            kwargs={
                "code": _market_symbol(symbol),
                "start": target.isoformat(),
                "end": target.isoformat(),
                "ktype": "K_DAY",
                "autype": autype,
                "max_count": 10,
                "page_req_key": None,
            },
        )
        if not isinstance(result, tuple) or len(result) != 2:
            raise ExternalSourceError("Futu history returned an invalid payload")
        frame = result[0]
        if not isinstance(frame, pd.DataFrame):
            raise ExternalSourceError("Futu history did not return a data frame")
        return self._exact_close(frame, target, date_column="time_key")

    def _sina_frame(self, symbol: str, target: date, adjust: str) -> pd.DataFrame:
        if self._sina_frame_fetcher is not None:
            return self._sina_frame_fetcher(symbol, target, adjust)
        try:
            import akshare as ak
        except ImportError as exc:
            raise ExternalSourceError("AKShare is required for Sina PIT pairing") from exc
        from alphapilot.data.sina_provider import _call_akshare_daily_direct

        digits = _symbol_digits(symbol)
        market = _market_symbol(digits, separator="").lower()
        frame = _call_akshare_daily_direct(
            ak.stock_zh_a_daily,
            symbol=market,
            start_date=target.strftime("%Y%m%d"),
            end_date=target.strftime("%Y%m%d"),
            adjust=adjust,
        )
        if not isinstance(frame, pd.DataFrame):
            raise ExternalSourceError("Sina did not return a data frame")
        return frame

    def _fetch_daily(self, sample: Mapping[str, object]) -> ExternalObservation:
        symbol = _symbol_digits(sample.get("symbol"))
        target = _parse_date(sample.get("trade_date"), label="trade_date")
        anchor = _parse_date(sample.get("adj_anchor_date"), label="adj_anchor_date")
        anchor_factor = _finite_number(sample.get("adj_anchor_factor"))
        if anchor <= target:
            raise ExternalSourceError(
                "adjustment normalization anchor must be after target date"
            )
        if anchor_factor is None or anchor_factor <= 0:
            raise ExternalSourceError(
                "local adjustment anchor must be positive and finite"
            )
        if not symbol.startswith(("4", "8", "92")):
            try:
                raw = self._futu_close(symbol, target, "None")
                hfq = self._futu_close(symbol, target, "hfq")
                anchor_raw = self._futu_close(symbol, anchor, "None")
                anchor_hfq = self._futu_close(symbol, anchor, "hfq")
                return ExternalObservation(
                    external_source="futu-unadjusted-day+futu-hfq-day",
                    values={
                        "close": raw,
                        "adj_factor": (hfq / raw) / (anchor_hfq / anchor_raw)
                        * anchor_factor,
                    },
                )
            except Exception:
                # The independent fallback remains non-BaoStock and its identity
                # is preserved in the evidence instead of silently claiming Futu.
                pass
        raw_frame = self._sina_frame(symbol, target, "")
        hfq_frame = self._sina_frame(symbol, target, "hfq")
        anchor_raw_frame = self._sina_frame(symbol, anchor, "")
        anchor_hfq_frame = self._sina_frame(symbol, anchor, "hfq")
        raw = self._exact_close(raw_frame, target, date_column="date")
        hfq = self._exact_close(hfq_frame, target, date_column="date")
        anchor_raw = self._exact_close(
            anchor_raw_frame,
            anchor,
            date_column="date",
        )
        anchor_hfq = self._exact_close(
            anchor_hfq_frame,
            anchor,
            date_column="date",
        )
        return ExternalObservation(
            external_source="sina-unadjusted-day+sina-hfq-day",
            values={
                "close": raw,
                "adj_factor": (hfq / raw) / (anchor_hfq / anchor_raw)
                * anchor_factor,
            },
        )

    def _fetch_valuation(self, sample: Mapping[str, object]) -> ExternalObservation:
        symbol = _symbol_digits(sample.get("symbol"))
        target = _parse_date(sample.get("trade_date"), label="trade_date")
        cache_key = (symbol, target)
        frame = self._valuation_cache.get(cache_key)
        if frame is None:
            fetcher = self._valuation_fetcher
            if fetcher is None:
                from alphapilot.jobs.valuation_sync import fetch_valuation_em

                frame = fetch_valuation_em(
                    symbol,
                    start_date=target,
                    end_date=target,
                    http_get=self._http_get,
                )
            else:
                frame = fetcher(symbol, start_date=target, end_date=target)
            if not isinstance(frame, pd.DataFrame):
                raise ExternalSourceError("Eastmoney valuation did not return a data frame")
            self._valuation_cache[cache_key] = frame
        if frame.empty or "trade_date" not in frame.columns:
            raise ExternalSourceError("Eastmoney valuation has no target row")
        dates = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
        selected = frame.loc[dates == target]
        if len(selected) != 1:
            raise ExternalSourceError("Eastmoney valuation has no unique target row")
        row = selected.iloc[0]
        available = datetime.combine(
            target,
            time(hour=15),
            tzinfo=MARKET_TIMEZONE,
        )
        return ExternalObservation(
            external_source="eastmoney-stock-value-em",
            values={
                "pe_ttm": _finite_number(row.get("pe_ttm")),
                "pb_mrq": _finite_number(row.get("pb_mrq")),
                "ps_ttm": _finite_number(row.get("ps_ttm")),
                "available_time": _utc_naive_text(available),
            },
        )

    def _eastmoney_financial_records(self, symbol: str) -> list[dict[str, Any]]:
        cached = self._financial_cache.get(symbol)
        if cached is not None:
            return cached
        response = self._http_get(
            _EM_FINANCIAL_URL,
            params={
                "type": "RPT_F10_FINANCE_MAINFINADATA",
                "sty": "APP_F10_MAINFINADATA",
                "quoteColumns": "",
                "filter": f'(SECUCODE="{_market_symbol(symbol)[3:]}.{_market_symbol(symbol)[:2]}")',
                "p": "1",
                "ps": "200",
                "sr": "-1",
                "st": "REPORT_DATE",
                "source": "HSF10",
                "client": "PC",
            },
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExternalSourceError("Eastmoney F10 returned a non-object payload")
        result = payload.get("result")
        records = result.get("data") if isinstance(result, dict) else None
        if not isinstance(records, list):
            raise ExternalSourceError("Eastmoney F10 payload is missing result.data")
        normalized = [dict(record) for record in records if isinstance(record, dict)]
        self._financial_cache[symbol] = normalized
        return normalized

    def _tushare_financial_records(self, symbol: str) -> list[dict[str, Any]]:
        cached = self._financial_cache.get(symbol)
        if cached is not None:
            return cached
        if not self._tushare_token:
            raise ExternalSourceError("Tushare token is not configured")
        if self._tushare_calls >= self._max_tushare_calls:
            raise ExternalSourceError("Tushare trial call budget is exhausted")
        self._tushare_calls += 1
        market_symbol = _market_symbol(symbol)
        response = self._http_post(
            _TUSHARE_URL,
            json={
                "api_name": "fina_indicator",
                "token": self._tushare_token,
                "params": {
                    "ts_code": f"{market_symbol[3:]}.{market_symbol[:2]}"
                },
                "fields": (
                    "ts_code,ann_date,end_date,roe,netprofit_yoy,"
                    "debt_to_assets,or_yoy"
                ),
            },
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise ExternalSourceError("Tushare fina_indicator returned a business error")
        data = payload.get("data")
        fields = data.get("fields") if isinstance(data, dict) else None
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(fields, list) or not isinstance(items, list):
            raise ExternalSourceError("Tushare fina_indicator payload is incomplete")
        normalized = [
            dict(zip((str(field) for field in fields), item, strict=True))
            for item in items
            if isinstance(item, list) and len(item) == len(fields)
        ]
        self._financial_cache[symbol] = normalized
        return normalized

    @staticmethod
    def _financial_value(
        record: Mapping[str, object],
        metric: str,
        *,
        source: str,
    ) -> float | None:
        eastmoney_fields = {
            "roe": ("ROEJQ", "ROE_WEIGHT", "WEIGHTAVG_ROE", "ROE"),
            "net_profit_yoy": (
                "PARENTNETPROFITTZ",
                "PARENT_NETPROFIT_YOY",
                "NETPROFIT_YOY",
            ),
            "ocf_to_profit": (
                "NCO_NETPROFIT",
                "OCF_TO_PROFIT",
                "NETCASH_OPERATE_TO_NETPROFIT",
            ),
            "debt_ratio": ("ZCFZL", "DEBT_ASSET_RATIO", "DEBT_TO_ASSETS"),
        }
        tushare_fields = {
            "roe": ("roe",),
            "net_profit_yoy": ("netprofit_yoy",),
            "debt_ratio": ("debt_to_assets",),
        }
        fields = eastmoney_fields if source == "em-f10" else tushare_fields
        aliases = fields.get(metric)
        if aliases is None:
            raise ExternalSourceError(
                f"{source} has no audited exact field mapping for metric={metric}"
            )
        raw: object | None = None
        for field in aliases:
            if field in record and record[field] not in (None, ""):
                raw = record[field]
                break
        if raw is None:
            return None
        try:
            if isinstance(raw, bool):
                raise TypeError
            number = float(str(raw))
        except (TypeError, ValueError) as exc:
            raise ExternalSourceError("financial metric is not numeric") from exc
        if not math.isfinite(number):
            raise ExternalSourceError("financial metric is not finite")
        # Eastmoney's NCO_NETPROFIT is already an unscaled ratio. Its other
        # mapped metrics and all currently mapped Tushare metrics are
        # percentage points, while AlphaPilot stores ratio decimals.
        if source == "em-f10" and metric == "ocf_to_profit":
            return number
        return number / 100.0

    def _fetch_financial(self, sample: Mapping[str, object]) -> ExternalObservation:
        symbol = _symbol_digits(sample.get("symbol"))
        target_period = _quarter_end(sample.get("report_period"))
        metric = str(sample.get("metric") or "")
        publication_fields: tuple[str, ...]
        if self.financial_source == "em-f10":
            records = self._eastmoney_financial_records(symbol)
            date_field = "REPORT_DATE"
            publication_fields = ("NOTICE_DATE", "ANNOUNCEMENT_DATE")
            source_name = "eastmoney-f10-main-financial"
        else:
            records = self._tushare_financial_records(symbol)
            date_field = "end_date"
            publication_fields = ("ann_date",)
            source_name = "tushare-fina-indicator"
        selected = [
            record
            for record in records
            if _parse_date(record.get(date_field), label=date_field) == target_period
        ]
        if len(selected) != 1:
            raise ExternalSourceError("financial source has no unique target-period row")
        record = selected[0]
        publication: date | None = None
        for field in publication_fields:
            value = record.get(field)
            if value not in (None, ""):
                publication = _parse_date(value, label=field)
                break
        if publication is None:
            raise ExternalSourceError("financial source has no publication date")
        available = datetime.combine(
            publication + timedelta(days=1),
            time.min,
            tzinfo=MARKET_TIMEZONE,
        )
        return ExternalObservation(
            external_source=source_name,
            values={
                "value": self._financial_value(
                    record,
                    metric,
                    source=self.financial_source,
                ),
                "available_time": _utc_naive_text(available),
            },
        )
