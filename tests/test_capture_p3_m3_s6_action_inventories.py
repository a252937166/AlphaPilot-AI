from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts/capture_p3_m3_s6_action_inventories.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("action_inventory_capture", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


capture = _load_script()


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_all_keys(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value), set())
    return set()


def test_frozen_specs_and_requests_are_complete_and_unfiltered() -> None:
    assert [
        (
            item.symbol,
            item.market,
            item.start_date,
            item.end_date,
            item.page_size,
        )
        for item in capture.SPECS
    ] == [
        ("000831", "SZSE", "2023-05-29", "2026-07-30", 50),
        ("001205", "SZSE", "2024-05-21", "2026-07-30", 50),
        ("001260", "SZSE", "2025-10-29", "2026-07-30", 50),
        ("600648", "SSE", "2025-06-16", "2026-07-30", 100),
        ("600782", "SSE", "2026-01-30", "2026-07-30", 100),
    ]
    for item in capture.SPECS:
        request = capture.request_record(item, 1)
        assert request["attempt_limit"] == 1
        assert request["retry_count"] == 0
        assert {"searchKey", "keyWord"}.isdisjoint(_all_keys(request))
        if item.market == "SSE":
            second_page = capture.request_record(item, 2)
            assert second_page["query"]["pageHelp.pageNo"] == 2
            assert second_page["query"]["pageHelp.beginPage"] == 2


def test_sanitize_response_headers_is_allowlist_only_and_deterministic() -> None:
    raw = (
        b"HTTP/1.1 200 OK\r\n"
        b"Date: Fri, 31 Jul 2026 10:27:33 GMT\r\n"
        b"Content-Type: application/json;charset=UTF-8\r\n"
        b"Content-Length: 123\r\n"
        b"Set-Cookie: secret=value\r\n"
        b"X-Napm-Traceid: volatile\r\n"
        b"Server: nginx\r\n\r\n"
    )
    expected = {
        "schema_version": capture.HEADER_SCHEMA_VERSION,
        "status_code": 200,
        "fields": {
            "content-length": "123",
            "content-type": "application/json;charset=UTF-8",
            "server": "nginx",
        },
        "redaction_policy": (
            "allowlist-only; volatile dates, cookies, trace identifiers, "
            "proxy metadata, and connection headers omitted"
        ),
    }
    assert capture.sanitize_response_headers(raw) == expected
    assert capture.sanitize_response_headers(raw) == expected


def test_validate_szse_page_enforces_symbol_window_and_unique_ids() -> None:
    spec = capture.CaptureSpec("001260", "SZSE", "2025-10-29", "2026-07-30", 2)
    payload: dict[str, Any] = {
        "announceCount": 3,
        "data": [
            {
                "id": "uuid-1",
                "annId": 1,
                "secCode": ["001260"],
                "publishTime": "2026-05-19 00:00:00",
            },
            {
                "id": "uuid-2",
                "annId": 2,
                "secCode": ["001260"],
                "publishTime": "2025-10-29 00:00:00",
            },
        ],
    }
    assert capture.validate_page(spec, 1, payload) == (
        3,
        2,
        ["uuid-1:1", "uuid-2:2"],
    )
    duplicate = {
        **payload,
        "data": [payload["data"][0], payload["data"][0]],
    }
    with pytest.raises(ValueError, match="duplicate row IDs"):
        capture.validate_page(spec, 1, duplicate)
    outside = {
        **payload,
        "data": [
            payload["data"][0],
            {**payload["data"][1], "publishTime": "2025-10-28 00:00:00"},
        ],
    }
    with pytest.raises(ValueError, match="outside frozen window"):
        capture.validate_page(spec, 1, outside)


def test_validate_sse_page_enforces_exact_echo_and_unfiltered_response() -> None:
    spec = capture.CaptureSpec("600782", "SSE", "2026-01-30", "2026-07-30", 2)
    rows = [
        {
            "SECURITY_CODE": "600782",
            "SSEDATE": "2026-07-24",
            "URL": "/a.pdf",
        },
        {
            "SECURITY_CODE": "600782",
            "SSEDATE": "2026-01-30",
            "URL": "/b.pdf",
        },
    ]
    payload: dict[str, Any] = {
        "productId": "600782",
        "beginDate": "2026-01-30",
        "endDate": "2026-07-30",
        "keyWord": "",
        "result": rows,
        "pageHelp": {
            "data": rows,
            "total": 3,
            "pageCount": 2,
            "pageNo": 1,
            "pageSize": 2,
        },
    }
    assert capture.validate_page(spec, 1, payload) == (3, 2, ["/a.pdf", "/b.pdf"])
    with pytest.raises(ValueError, match="keyword filter"):
        capture.validate_page(spec, 1, {**payload, "keyWord": "权益分派"})
    with pytest.raises(ValueError, match="beginDate mismatch"):
        capture.validate_page(spec, 1, {**payload, "beginDate": "2026-01-29"})


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "already-there"
    output.mkdir()
    with pytest.raises(FileExistsError, match="refusing overwrite"):
        capture.capture(output)
