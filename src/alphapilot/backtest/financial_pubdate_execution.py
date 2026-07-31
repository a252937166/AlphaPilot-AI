from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from alphapilot.backtest.financial_acceptance import PUBDATE_PLAN_SCHEMA_VERSION

EXECUTION_SCHEMA_VERSION = "p3.3-s2-pubdate-execution-v1"
REQUIRED_SAMPLE_COUNT = 5
BLACKLIST_ERROR_CODE = "10001011"
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
_REPORT_PERIOD_PATTERN = re.compile(r"^(?P<year>[0-9]{4})Q(?P<quarter>[1-4])$")


class BaoStockResult(Protocol):
    error_code: str
    error_msg: str
    fields: Sequence[str]

    def next(self) -> bool: ...

    def get_row_data(self) -> Sequence[str]: ...


class BaoStockClient(Protocol):
    def login(self) -> BaoStockResult: ...

    def query_profit_data(
        self,
        *,
        code: str,
        year: int,
        quarter: int,
    ) -> BaoStockResult: ...

    def logout(self) -> BaoStockResult: ...


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_date(raw: object) -> date | None:
    try:
        return date.fromisoformat(str(raw or "").strip())
    except ValueError:
        return None


def _parse_datetime(raw: object) -> datetime | None:
    text = str(raw or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _available_time_from_pub_date(pub_date: date) -> datetime:
    local = datetime.combine(
        pub_date + timedelta(days=1),
        time.min,
        tzinfo=MARKET_TIMEZONE,
    )
    return local.astimezone(UTC)


def _safe_provider_message(raw: object) -> str:
    text = " ".join(str(raw or "").split())
    return text[:300]


def _provider_code(symbol: str) -> str:
    market = "sh" if symbol.startswith("6") else "sz"
    return f"{market}.{symbol}"


def _report_period_from_stat_date(stat_date: date) -> str | None:
    if stat_date.month not in {3, 6, 9, 12}:
        return None
    return f"{stat_date.year}Q{stat_date.month // 3}"


def _extract_plan(payload: object) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(payload, Mapping):
        return None, "root"
    root = dict(payload)
    nested = root.get("pubdate_plan")
    if isinstance(nested, Mapping):
        return dict(nested), "acceptance_report.pubdate_plan"
    return root, "pubdate_plan"


def _validate_plan(plan: Mapping[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    blockers: list[str] = []
    if plan.get("schema_version") != PUBDATE_PLAN_SCHEMA_VERSION:
        blockers.append("schema_version must be p3.3-s2-pubdate-plan-v1")
    if plan.get("mode") != "plan_only":
        blockers.append("mode must be plan_only")
    if plan.get("network_called") is not False:
        blockers.append("plan network_called must be false")
    if plan.get("ready_for_authorized_execution") is not True:
        blockers.append("plan is not ready_for_authorized_execution")
    if plan.get("sample_size") != REQUIRED_SAMPLE_COUNT:
        blockers.append("sample_size must be exactly 5")
    if plan.get("planned_provider_queries") != REQUIRED_SAMPLE_COUNT:
        blockers.append("planned_provider_queries must be exactly 5")
    if plan.get("hard_provider_query_cap") != REQUIRED_SAMPLE_COUNT:
        blockers.append("hard_provider_query_cap must be exactly 5")
    if plan.get("blockers") not in ([], ()):
        blockers.append("plan blockers must be empty")

    raw_samples = plan.get("samples")
    if not isinstance(raw_samples, list):
        blockers.append("samples must be a JSON array")
        return blockers, []
    if len(raw_samples) != REQUIRED_SAMPLE_COUNT:
        blockers.append("samples must contain exactly 5 fixed entries")

    samples: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    for index, raw_sample in enumerate(raw_samples):
        prefix = f"samples[{index}]"
        if not isinstance(raw_sample, Mapping):
            blockers.append(f"{prefix} must be an object")
            continue
        sample = dict(raw_sample)
        symbol = str(sample.get("symbol") or "")
        period = str(sample.get("report_period") or "")
        match = _REPORT_PERIOD_PATTERN.fullmatch(period)
        if len(symbol) != 6 or not symbol.isdigit():
            blockers.append(f"{prefix}.symbol must be six digits")
        if symbol in seen_symbols:
            blockers.append(f"{prefix}.symbol is duplicated")
        seen_symbols.add(symbol)
        if match is None:
            blockers.append(f"{prefix}.report_period is invalid")
        else:
            expected_year = int(match.group("year"))
            expected_quarter = int(match.group("quarter"))
            if sample.get("year") != expected_year:
                blockers.append(f"{prefix}.year disagrees with report_period")
            if sample.get("quarter") != expected_quarter:
                blockers.append(f"{prefix}.quarter disagrees with report_period")

        stat_date = _parse_date(sample.get("local_stat_date"))
        pub_date = _parse_date(sample.get("local_pub_date"))
        local_available_time = _parse_datetime(sample.get("local_available_time"))
        if stat_date is None:
            blockers.append(f"{prefix}.local_stat_date is invalid")
        elif match is not None and _report_period_from_stat_date(stat_date) != period:
            blockers.append(f"{prefix}.local_stat_date disagrees with report_period")
        if pub_date is None:
            blockers.append(f"{prefix}.local_pub_date is invalid")
        if local_available_time is None:
            blockers.append(f"{prefix}.local_available_time must be timezone-aware")
        elif pub_date is not None and (
            abs(
                (
                    local_available_time - _available_time_from_pub_date(pub_date)
                ).total_seconds()
            )
            > 1
        ):
            blockers.append(
                f"{prefix}.local_available_time disagrees with local_pub_date + 1 day"
            )
        samples.append(sample)
    return blockers, samples


def _read_provider_row(result: BaoStockResult) -> tuple[int, dict[str, str] | None]:
    fields = [str(field) for field in result.fields]
    if len(fields) != len(set(fields)):
        raise ValueError("provider returned duplicate fields")
    required = {"code", "statDate", "pubDate"}
    if not required.issubset(fields):
        raise ValueError("provider result is missing code/statDate/pubDate")
    row_count = 0
    first_row: dict[str, str] | None = None
    while result.next():
        values = [str(value) for value in result.get_row_data()]
        if len(values) != len(fields):
            raise ValueError("provider row length disagrees with fields")
        row_count += 1
        if row_count == 1:
            first_row = dict(zip(fields, values, strict=True))
    return row_count, first_row


def _sample_evidence(
    *,
    index: int,
    sample: Mapping[str, Any],
    provider_error_code: str,
    provider_error_message: str,
    row_count: int,
    row: Mapping[str, str] | None,
) -> dict[str, Any]:
    symbol = str(sample["symbol"])
    period = str(sample["report_period"])
    local_stat_date = _parse_date(sample["local_stat_date"])
    local_pub_date = _parse_date(sample["local_pub_date"])
    local_available_time = _parse_datetime(sample["local_available_time"])
    assert local_stat_date is not None
    assert local_pub_date is not None
    assert local_available_time is not None

    provider_stat_date = _parse_date(row.get("statDate")) if row is not None else None
    provider_pub_date = _parse_date(row.get("pubDate")) if row is not None else None
    recomputed = (
        _available_time_from_pub_date(provider_pub_date)
        if provider_pub_date is not None
        else None
    )
    checks = {
        "query_error_code_zero": provider_error_code == "0",
        "row_count_exactly_one": row_count == 1,
        "symbol_matches": (
            row is not None and str(row.get("code") or "").lower() == _provider_code(symbol)
        ),
        "report_period_matches": (
            provider_stat_date is not None
            and _report_period_from_stat_date(provider_stat_date) == period
        ),
        "stat_date_matches": provider_stat_date == local_stat_date,
        "pub_date_matches": provider_pub_date == local_pub_date,
        "available_time_matches": (
            recomputed is not None
            and abs((recomputed - local_available_time).total_seconds()) <= 1
        ),
    }
    return {
        "sample_index": index,
        "symbol": symbol,
        "provider_code": _provider_code(symbol),
        "report_period": period,
        "year": int(sample["year"]),
        "quarter": int(sample["quarter"]),
        "expected": {
            "stat_date": local_stat_date.isoformat(),
            "pub_date": local_pub_date.isoformat(),
            "available_time_utc": local_available_time.isoformat(),
        },
        "provider": {
            "error_code": provider_error_code,
            "error_message": provider_error_message,
            "row_count": row_count,
            "symbol": row.get("code") if row is not None else None,
            "stat_date": row.get("statDate") if row is not None else None,
            "pub_date": row.get("pubDate") if row is not None else None,
            "recomputed_available_time_utc": (
                recomputed.isoformat() if recomputed is not None else None
            ),
        },
        "checks": checks,
        "matched": all(checks.values()),
    }


def execute_pubdate_audit(
    plan_payload: object,
    *,
    client: BaoStockClient,
) -> dict[str, Any]:
    """Execute the fixed five-sample pubDate audit with no retries or resampling."""

    plan, plan_location = _extract_plan(plan_payload)
    validation_blockers: list[str]
    samples: list[dict[str, Any]]
    if plan is None:
        validation_blockers = ["plan input must be a JSON object"]
        samples = []
    else:
        validation_blockers, samples = _validate_plan(plan)

    report: dict[str, Any] = {
        "schema_version": EXECUTION_SCHEMA_VERSION,
        "mode": "authorized_fixed_plan_execution",
        "generated_at": datetime.now(UTC).isoformat(),
        "plan": {
            "location": plan_location,
            "schema_version": plan.get("schema_version") if plan is not None else None,
            "sha256": _canonical_sha256(plan) if plan is not None else None,
            "sample_count": len(samples),
        },
        "invariants": {
            "single_process": True,
            "single_login": True,
            "hard_query_cap": REQUIRED_SAMPLE_COUNT,
            "retry_count": 0,
            "resampling_allowed": False,
        },
        "network_called": False,
        "login": {"attempted": False, "error_code": None, "error_message": ""},
        "queries_attempted": 0,
        "samples": [],
        "stopped_early": False,
        "stop_reason": None,
        "logout": {"attempted": False, "error_code": None, "error_message": ""},
        "gate": {
            "passed": False,
            "matched_count": 0,
            "required_matches": REQUIRED_SAMPLE_COUNT,
            "blockers": list(validation_blockers),
        },
    }
    if validation_blockers:
        report["stop_reason"] = {
            "code": "invalid_fixed_plan",
            "detail": "fixed plan validation failed before provider login",
        }
        return report

    login_attempted = False
    execution_blocker: dict[str, Any] | None = None
    try:
        login_attempted = True
        report["network_called"] = True
        report["login"]["attempted"] = True
        try:
            login_result = client.login()
            login_code = str(login_result.error_code)
            login_message = _safe_provider_message(login_result.error_msg)
        except Exception as exc:
            execution_blocker = {
                "code": "login_exception",
                "detail": f"client raised {type(exc).__name__}",
            }
        else:
            report["login"]["error_code"] = login_code
            report["login"]["error_message"] = login_message
            if login_code != "0":
                execution_blocker = {
                    "code": (
                        "provider_blacklisted"
                        if login_code == BLACKLIST_ERROR_CODE
                        else "login_error"
                    ),
                    "detail": f"BaoStock login returned error_code={login_code}",
                }

        if execution_blocker is None:
            for index, sample in enumerate(samples):
                if int(report["queries_attempted"]) >= REQUIRED_SAMPLE_COUNT:
                    execution_blocker = {
                        "code": "query_cap_exceeded",
                        "detail": "hard query cap reached before fixed plan completed",
                    }
                    break
                report["queries_attempted"] = int(report["queries_attempted"]) + 1
                try:
                    result = client.query_profit_data(
                        code=_provider_code(str(sample["symbol"])),
                        year=int(sample["year"]),
                        quarter=int(sample["quarter"]),
                    )
                    error_code = str(result.error_code)
                    error_message = _safe_provider_message(result.error_msg)
                except Exception as exc:
                    execution_blocker = {
                        "code": "query_exception",
                        "detail": f"client raised {type(exc).__name__}",
                        "sample_index": index,
                    }
                    break
                if error_code != "0":
                    execution_blocker = {
                        "code": (
                            "provider_blacklisted"
                            if error_code == BLACKLIST_ERROR_CODE
                            else "query_error"
                        ),
                        "detail": f"BaoStock query returned error_code={error_code}",
                        "sample_index": index,
                    }
                    break
                try:
                    row_count, row = _read_provider_row(result)
                except Exception as exc:
                    execution_blocker = {
                        "code": "query_result_error",
                        "detail": f"provider result raised {type(exc).__name__}",
                        "sample_index": index,
                    }
                    break
                report["samples"].append(
                    _sample_evidence(
                        index=index,
                        sample=sample,
                        provider_error_code=error_code,
                        provider_error_message=error_message,
                        row_count=row_count,
                        row=row,
                    )
                )
    finally:
        if login_attempted:
            report["logout"]["attempted"] = True
            try:
                logout_result = client.logout()
                logout_code = str(logout_result.error_code)
                logout_message = _safe_provider_message(logout_result.error_msg)
            except Exception as exc:
                report["logout"]["error_code"] = "exception"
                report["logout"]["error_message"] = type(exc).__name__
                if execution_blocker is None:
                    execution_blocker = {
                        "code": "logout_exception",
                        "detail": f"client raised {type(exc).__name__}",
                    }
            else:
                report["logout"]["error_code"] = logout_code
                report["logout"]["error_message"] = logout_message
                if logout_code != "0" and execution_blocker is None:
                    execution_blocker = {
                        "code": "logout_error",
                        "detail": f"BaoStock logout returned error_code={logout_code}",
                    }

    matched_count = sum(bool(sample["matched"]) for sample in report["samples"])
    report["gate"]["matched_count"] = matched_count
    if execution_blocker is not None:
        report["stopped_early"] = (
            int(report["queries_attempted"]) < REQUIRED_SAMPLE_COUNT
        )
        report["stop_reason"] = execution_blocker
        report["gate"]["blockers"].append(execution_blocker)
    if len(report["samples"]) != REQUIRED_SAMPLE_COUNT:
        report["gate"]["blockers"].append(
            {
                "code": "incomplete_fixed_plan",
                "detail": (
                    f"completed_evidence={len(report['samples'])}, "
                    f"required={REQUIRED_SAMPLE_COUNT}"
                ),
            }
        )
    mismatched = [
        int(sample["sample_index"]) for sample in report["samples"] if not sample["matched"]
    ]
    if mismatched:
        report["gate"]["blockers"].append(
            {
                "code": "pubdate_mismatch",
                "detail": f"mismatched fixed sample indexes={mismatched}",
            }
        )
    report["gate"]["passed"] = (
        not report["gate"]["blockers"]
        and int(report["queries_attempted"]) == REQUIRED_SAMPLE_COUNT
        and matched_count == REQUIRED_SAMPLE_COUNT
        and report["logout"]["error_code"] == "0"
    )
    return report
