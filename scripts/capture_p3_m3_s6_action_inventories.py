from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

CAPTURE_SCHEMA_VERSION = "p3.3-s6-official-action-inventories-v1"
HEADER_SCHEMA_VERSION = "p3.3-s6-sanitized-response-headers-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
SZSE_URL = "https://www.szse.cn/api/disc/announcement/annList?random=0.1"
SSE_URL = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
_HEADER_ALLOWLIST = (
    "cache-control",
    "content-encoding",
    "content-language",
    "content-length",
    "content-type",
    "etag",
    "last-modified",
    "server",
)
_PROXY_ENV_NAMES = {
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
}


@dataclass(frozen=True)
class CaptureSpec:
    symbol: str
    market: str
    start_date: str
    end_date: str
    page_size: int


SPECS = (
    CaptureSpec("000831", "SZSE", "2023-05-29", "2026-07-30", 50),
    CaptureSpec("001205", "SZSE", "2024-05-21", "2026-07-30", 50),
    CaptureSpec("001260", "SZSE", "2025-10-29", "2026-07-30", 50),
    CaptureSpec("600648", "SSE", "2025-06-16", "2026-07-30", 100),
    CaptureSpec("600782", "SSE", "2026-01-30", "2026-07-30", 100),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze complete, unfiltered official announcement windows for the "
            "five architect-selected P3.3-S6 samples. Every page receives exactly "
            "one network attempt; this command has no retry or alternate-source path."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="A new, non-existing output directory (normally below /tmp).",
    )
    return parser


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _network_env() -> dict[str, str]:
    result = {
        key: value
        for key, value in os.environ.items()
        if key.lower() not in _PROXY_ENV_NAMES
    }
    result["NO_PROXY"] = "*"
    result["no_proxy"] = "*"
    return result


def sanitize_response_headers(raw_headers: bytes) -> dict[str, Any]:
    """Return only stable, non-secret response metadata from curl's header dump."""
    text = raw_headers.decode("iso-8859-1")
    blocks: list[tuple[int, dict[str, list[str]]]] = []
    status_code: int | None = None
    fields: dict[str, list[str]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip("\r")
        if line.startswith("HTTP/"):
            if status_code is not None:
                blocks.append((status_code, fields))
            parts = line.split()
            if len(parts) < 2 or not parts[1].isdigit():
                raise ValueError(f"invalid HTTP status line: {line!r}")
            status_code = int(parts[1])
            fields = {}
            continue
        if not line or status_code is None or ":" not in line:
            continue
        name, value = line.split(":", 1)
        normalized_name = name.strip().lower()
        fields.setdefault(normalized_name, []).append(value.strip())
    if status_code is not None:
        blocks.append((status_code, fields))
    if not blocks:
        raise ValueError("curl header dump contains no HTTP response")

    final_status, final_fields = blocks[-1]
    sanitized_fields = {
        name: ", ".join(final_fields[name])
        for name in _HEADER_ALLOWLIST
        if name in final_fields
    }
    return {
        "schema_version": HEADER_SCHEMA_VERSION,
        "status_code": final_status,
        "fields": sanitized_fields,
        "redaction_policy": (
            "allowlist-only; volatile dates, cookies, trace identifiers, "
            "proxy metadata, and connection headers omitted"
        ),
    }


def request_record(spec: CaptureSpec, page_number: int) -> dict[str, Any]:
    common: dict[str, Any] = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "symbol": spec.symbol,
        "market": spec.market,
        "frozen_window": [spec.start_date, spec.end_date],
        "page_number": page_number,
        "page_size": spec.page_size,
        "attempt_limit": 1,
        "retry_count": 0,
        "source_selection": "fixed official exchange endpoint; no fallback",
    }
    if spec.market == "SZSE":
        return {
            **common,
            "method": "POST",
            "url": SZSE_URL,
            "headers": {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Referer": "https://www.szse.cn/disclosure/listed/notice/index.html",
                "User-Agent": "Mozilla/5.0",
            },
            "body": {
                "stock": [spec.symbol],
                "channelCode": ["listedNotice_disc"],
                "seDate": [spec.start_date, spec.end_date],
                "pageSize": spec.page_size,
                "pageNum": page_number,
            },
        }
    if spec.market == "SSE":
        query: dict[str, str | int] = {
            "isPagination": "true",
            "productId": spec.symbol,
            "securityType": "0101,120100,020100,020200,120200",
            "reportType": "ALL",
            "beginDate": spec.start_date,
            "endDate": spec.end_date,
            "pageHelp.pageSize": spec.page_size,
            "pageHelp.pageCount": 50,
            "pageHelp.pageNo": page_number,
            "pageHelp.beginPage": page_number,
            "pageHelp.cacheSize": 1,
            "pageHelp.endPage": 5,
        }
        return {
            **common,
            "method": "GET",
            "url": SSE_URL,
            "headers": {
                "Accept": "application/json",
                "Referer": "https://www.sse.com.cn/",
                "User-Agent": "Mozilla/5.0",
            },
            "query": query,
        }
    raise ValueError(f"unsupported market: {spec.market}")


def _curl_command(request: Mapping[str, Any], header_path: Path) -> list[str]:
    command = [
        "curl",
        "--silent",
        "--show-error",
        "--fail-with-body",
        "--compressed",
        "--retry",
        "0",
        "--connect-timeout",
        "20",
        "--max-time",
        "90",
        "--max-redirs",
        "0",
        "--noproxy",
        "*",
        "--dump-header",
        str(header_path),
    ]
    headers = request.get("headers")
    if not isinstance(headers, Mapping):
        raise ValueError("request headers must be an object")
    for name in sorted(headers):
        command.extend(["--header", f"{name}: {headers[name]}"])
    method = request.get("method")
    url = request.get("url")
    if not isinstance(url, str):
        raise ValueError("request URL must be a string")
    if method == "POST":
        command.extend(["--request", "POST", "--data-binary", "@-", url])
    elif method == "GET":
        query = request.get("query")
        if not isinstance(query, Mapping):
            raise ValueError("GET query must be an object")
        command.append(f"{url}?{urlencode(list(query.items()))}")
    else:
        raise ValueError(f"unsupported method: {method!r}")
    return command


def _parse_json_object(value: bytes, *, symbol: str, page_number: int) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"{symbol} page {page_number}: official response is not strict JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{symbol} page {page_number}: response root is not an object")
    return parsed


def _require_date_in_window(
    raw_value: object,
    *,
    spec: CaptureSpec,
    page_number: int,
) -> None:
    value = str(raw_value)[:10]
    if not (spec.start_date <= value <= spec.end_date):
        raise ValueError(
            f"{spec.symbol} page {page_number}: announcement date {value!r} "
            f"outside frozen window {spec.start_date}..{spec.end_date}"
        )


def validate_page(
    spec: CaptureSpec,
    page_number: int,
    payload: Mapping[str, Any],
) -> tuple[int, int, list[str]]:
    """Validate one official page and return total, page-count and stable row IDs."""
    if spec.market == "SZSE":
        total = payload.get("announceCount")
        records = payload.get("data")
        if not isinstance(total, int) or total < 0:
            raise ValueError(f"{spec.symbol} page {page_number}: invalid announceCount")
        if not isinstance(records, list):
            raise ValueError(f"{spec.symbol} page {page_number}: data is not a list")
        page_count = max(1, math.ceil(total / spec.page_size))
        row_ids: list[str] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError(f"{spec.symbol} page {page_number}: invalid data row")
            sec_codes = record.get("secCode")
            if not isinstance(sec_codes, list) or spec.symbol not in sec_codes:
                raise ValueError(
                    f"{spec.symbol} page {page_number}: row symbol mismatch"
                )
            _require_date_in_window(
                record.get("publishTime"),
                spec=spec,
                page_number=page_number,
            )
            row_id = record.get("id")
            ann_id = record.get("annId")
            if not isinstance(row_id, str) or not row_id or ann_id in (None, ""):
                raise ValueError(
                    f"{spec.symbol} page {page_number}: row lacks stable id/annId"
                )
            row_ids.append(f"{row_id}:{ann_id}")
    elif spec.market == "SSE":
        if payload.get("productId") != spec.symbol:
            raise ValueError(f"{spec.symbol} page {page_number}: productId mismatch")
        if payload.get("beginDate") != spec.start_date:
            raise ValueError(f"{spec.symbol} page {page_number}: beginDate mismatch")
        if payload.get("endDate") != spec.end_date:
            raise ValueError(f"{spec.symbol} page {page_number}: endDate mismatch")
        if payload.get("keyWord") not in (None, ""):
            raise ValueError(
                f"{spec.symbol} page {page_number}: response reports a keyword filter"
            )
        page_help = payload.get("pageHelp")
        records = payload.get("result")
        if not isinstance(page_help, Mapping) or not isinstance(records, list):
            raise ValueError(f"{spec.symbol} page {page_number}: invalid page payload")
        if page_help.get("data") != records:
            raise ValueError(
                f"{spec.symbol} page {page_number}: result/pageHelp.data mismatch"
            )
        total = page_help.get("total")
        raw_page_count = page_help.get("pageCount")
        if not isinstance(total, int) or total < 0:
            raise ValueError(f"{spec.symbol} page {page_number}: invalid total")
        if (
            not isinstance(raw_page_count, int)
            or raw_page_count != max(1, math.ceil(total / spec.page_size))
        ):
            raise ValueError(f"{spec.symbol} page {page_number}: invalid pageCount")
        page_count = raw_page_count
        if page_help.get("pageNo") != page_number:
            raise ValueError(f"{spec.symbol} page {page_number}: pageNo mismatch")
        if page_help.get("pageSize") != spec.page_size:
            raise ValueError(f"{spec.symbol} page {page_number}: pageSize mismatch")
        row_ids = []
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError(f"{spec.symbol} page {page_number}: invalid result row")
            if record.get("SECURITY_CODE") != spec.symbol:
                raise ValueError(
                    f"{spec.symbol} page {page_number}: row symbol mismatch"
                )
            _require_date_in_window(
                record.get("SSEDATE"),
                spec=spec,
                page_number=page_number,
            )
            row_id = record.get("URL")
            if not isinstance(row_id, str) or not row_id:
                raise ValueError(
                    f"{spec.symbol} page {page_number}: row lacks stable URL"
                )
            row_ids.append(row_id)
    else:
        raise ValueError(f"unsupported market: {spec.market}")

    if page_number < page_count and len(records) != spec.page_size:
        raise ValueError(
            f"{spec.symbol} page {page_number}: non-final page has "
            f"{len(records)} rows, expected {spec.page_size}"
        )
    if page_number == page_count:
        expected_final_rows = total - spec.page_size * (page_count - 1)
        if total == 0:
            expected_final_rows = 0
        if len(records) != expected_final_rows:
            raise ValueError(
                f"{spec.symbol} page {page_number}: final page has "
                f"{len(records)} rows, expected {expected_final_rows}"
            )
    if page_number > page_count:
        raise ValueError(
            f"{spec.symbol} page {page_number}: exceeds pageCount {page_count}"
        )
    if len(set(row_ids)) != len(row_ids):
        raise ValueError(f"{spec.symbol} page {page_number}: duplicate row IDs")
    return total, page_count, row_ids


def _capture_page(
    spec: CaptureSpec,
    page_number: int,
    symbol_dir: Path,
) -> tuple[int, int, list[str], dict[str, str | int]]:
    request = request_record(spec, page_number)
    request_bytes = _json_bytes(request)
    with tempfile.TemporaryDirectory(prefix="alphapilot-s6-capture-") as temp_name:
        temp_dir = Path(temp_name)
        raw_header_path = temp_dir / "response.headers"
        command = _curl_command(request, raw_header_path)
        body_value = request.get("body")
        input_bytes = _json_bytes(body_value) if body_value is not None else None
        completed = subprocess.run(
            command,
            check=False,
            input=input_bytes,
            capture_output=True,
            env=_network_env(),
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode(errors="replace").strip()
            raise RuntimeError(
                f"{spec.symbol} page {page_number}: sole network attempt failed "
                f"(curl exit {completed.returncode}): {stderr}"
            )
        raw_headers = raw_header_path.read_bytes()
        sanitized_headers = sanitize_response_headers(raw_headers)
        if sanitized_headers["status_code"] != 200:
            raise RuntimeError(
                f"{spec.symbol} page {page_number}: sole response status was "
                f"{sanitized_headers['status_code']}"
            )

    payload = _parse_json_object(
        completed.stdout,
        symbol=spec.symbol,
        page_number=page_number,
    )
    total, page_count, row_ids = validate_page(spec, page_number, payload)
    prefix = f"page-{page_number:03d}"
    request_path = symbol_dir / f"{prefix}.request.json"
    response_path = symbol_dir / f"{prefix}.response.json"
    headers_path = symbol_dir / f"{prefix}.headers.json"
    request_path.write_bytes(request_bytes)
    response_path.write_bytes(completed.stdout)
    headers_path.write_bytes(_json_bytes(sanitized_headers))
    return (
        total,
        page_count,
        row_ids,
        {
            "page_number": page_number,
            "row_count": len(row_ids),
            "request_sha256": _sha256_bytes(request_bytes),
            "response_sha256": _sha256_bytes(completed.stdout),
            "headers_sha256": _sha256_path(headers_path),
        },
    )


def capture(output: Path) -> dict[str, Any]:
    resolved_output = output.expanduser().resolve()
    if resolved_output.exists():
        raise FileExistsError(
            f"output directory already exists; refusing overwrite: {resolved_output}"
        )
    resolved_output.mkdir(parents=True)
    started_at = datetime.now(SHANGHAI)
    captures: list[dict[str, Any]] = []
    try:
        for spec in SPECS:
            symbol_dir = resolved_output / spec.symbol
            symbol_dir.mkdir()
            expected_total: int | None = None
            expected_pages: int | None = None
            all_row_ids: list[str] = []
            pages: list[dict[str, str | int]] = []
            page_number = 1
            while expected_pages is None or page_number <= expected_pages:
                total, page_count, row_ids, page_evidence = _capture_page(
                    spec,
                    page_number,
                    symbol_dir,
                )
                if expected_total is None:
                    expected_total = total
                    expected_pages = page_count
                elif total != expected_total or page_count != expected_pages:
                    raise RuntimeError(
                        f"{spec.symbol} page {page_number}: pagination changed "
                        "during the one-pass capture"
                    )
                all_row_ids.extend(row_ids)
                pages.append(page_evidence)
                page_number += 1
            assert expected_total is not None
            assert expected_pages is not None
            if len(all_row_ids) != expected_total:
                raise RuntimeError(
                    f"{spec.symbol}: captured {len(all_row_ids)} rows, "
                    f"official total is {expected_total}"
                )
            if len(set(all_row_ids)) != len(all_row_ids):
                raise RuntimeError(f"{spec.symbol}: duplicate row IDs across pages")
            captures.append(
                {
                    "symbol": spec.symbol,
                    "market": spec.market,
                    "frozen_window": [spec.start_date, spec.end_date],
                    "page_size": spec.page_size,
                    "page_count": expected_pages,
                    "official_total": expected_total,
                    "captured_total": len(all_row_ids),
                    "unique_row_ids": len(set(all_row_ids)),
                    "pages": pages,
                }
            )

        completed_at = datetime.now(SHANGHAI)
        manifest = {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "started_at": started_at.isoformat(timespec="seconds"),
            "completed_at": completed_at.isoformat(timespec="seconds"),
            "timezone": "Asia/Shanghai",
            "policy": {
                "official_sources_only": True,
                "unfiltered_announcement_windows": True,
                "attempts_per_page": 1,
                "retries": 0,
                "fallback_sources": [],
                "login": False,
                "trading": False,
                "response_headers": HEADER_SCHEMA_VERSION,
            },
            "captures": captures,
            "totals": {
                "symbols": len(captures),
                "pages": sum(int(item["page_count"]) for item in captures),
                "announcements": sum(
                    int(item["captured_total"]) for item in captures
                ),
            },
        }
        manifest_path = resolved_output / "CAPTURE-MANIFEST.json"
        manifest_path.write_bytes(_json_bytes(manifest))
        checksum_rows = [
            f"{_sha256_path(path)}  {path.relative_to(resolved_output).as_posix()}"
            for path in sorted(resolved_output.rglob("*"))
            if path.is_file() and path.name != "SHA256SUMS"
        ]
        checksum_path = resolved_output / "SHA256SUMS"
        checksum_path.write_text("\n".join(checksum_rows) + "\n", encoding="utf-8")
        manifest["sha256sums_sha256"] = _sha256_path(checksum_path)
        return manifest
    except BaseException:
        failure_path = resolved_output / "CAPTURE-FAILED.txt"
        failure_path.write_text(
            "Capture aborted. No retry or alternate source was attempted.\n",
            encoding="utf-8",
        )
        raise


def main() -> int:
    args = _parser().parse_args()
    manifest = capture(args.output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
