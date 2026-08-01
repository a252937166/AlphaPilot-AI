from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts/rebuild_p3_m3_s6_source_bundle.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("source_bundle_rebuilder", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rebuild = _load_script()


def _write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    _write(path, rebuild._canonical_json_bytes(value))


def _artifact(
    root: Path,
    relative: str,
    *,
    source_kind: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    path = root / relative
    actual_fields: dict[str, object] = {
        "http_status": 200,
        "content_type": "application/json",
    }
    if "header" in source_kind:
        actual_fields["server"] = "must-be-removed"
    return {
        "relative_path": relative,
        "sha256": rebuild._sha256(path),
        "bytes": path.stat().st_size,
        "source_identity": "Synthetic official source",
        "source_kind": source_kind,
        "request": request,
        "retrieved_at": "2026-07-31T23:00:00+08:00",
        "parser_version": "synthetic-v1",
        "actual_fields": actual_fields,
        "missing_state": {"status": "none", "details": None},
    }


def _write_daily_manifests(root: Path) -> None:
    directory = root / "daily-bars"
    manifest_path = directory / "MANIFEST.json"
    checksum_path = directory / "MANIFEST.sha256"
    files = [
        {
            "file": path.relative_to(directory).as_posix(),
            "sha256": rebuild._sha256(path),
            "bytes": path.stat().st_size,
        }
        for path in rebuild._regular_files(directory)
        if path not in {manifest_path, checksum_path}
    ]
    _write_json(
        manifest_path,
        {
            "schema_version": "synthetic-daily-v1",
            "generated_at": "2026-07-31T23:00:00+08:00",
            "files": files,
        },
    )
    rows = [f"{item['sha256']}  {item['file']}" for item in files]
    rows.append(f"{rebuild._sha256(manifest_path)}  MANIFEST.json")
    _write(checksum_path, "".join(f"{row}\n" for row in rows))


def _source_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    request = {"symbol": "001260", "page": 1}
    response = {"announceCount": 1, "data": [{"title": "fixture"}]}
    raw_headers = (
        "HTTP/1.1 100 Continue\r\n"
        "Server: proxy\r\n\r\n"
        "HTTP/1.1 200 OK\r\n"
        "Date: Fri, 31 Jul 2026 15:00:00 GMT\r\n"
        "Content-Type: application/json;charset=UTF-8\r\n"
        "Content-Length: 45\r\n"
        "Last-Modified: Fri, 31 Jul 2026 14:00:00 GMT\r\n"
        'ETag: "fixture"\r\n'
        "Server: secret-server\r\n"
        "Set-Cookie: JSESSIONID=secret\r\n"
        "X-Trace-Id: private-trace\r\n\r\n"
    )
    _write_json(root / "daily-bars/page-001.request.json", request)
    _write_json(root / "daily-bars/page-001.response.json", response)
    _write(root / "daily-bars/page-001.headers", raw_headers)
    _write_daily_manifests(root)

    _write(root / "price-tick-rules/raw/rule.html", "<html>rule</html>\n")
    _write(
        root / "price-tick-rules/meta/rule-headers.txt",
        (
            "HTTP/2 200\r\n"
            "content-type: text/html\r\n"
            "server: private-edge\r\n"
            "date: Fri, 31 Jul 2026 15:00:00 GMT\r\n\r\n"
        ),
    )
    _write_json(
        root / "price-tick-rules/REQUESTS.json",
        {
            "requests": [
                {
                    "id": "rule",
                    "method": "GET",
                    "url": "https://example.invalid/rule",
                    "response_body": "raw/rule.html",
                    "response_headers": "meta/rule-headers.txt",
                }
            ]
        },
    )
    price_header = root / "price-tick-rules/meta/rule-headers.txt"
    price_body = root / "price-tick-rules/raw/rule.html"
    _write_json(
        root / "price-tick-rules/MANIFEST.json",
        {
            "schema_version": "synthetic-price-v1",
            "generated_at": "2026-07-31T23:00:00+08:00",
            "primary_artifacts": [
                {
                    "relative_path": "raw/rule.html",
                    "sha256": rebuild._sha256(price_body),
                    "bytes": price_body.stat().st_size,
                    "source_identity": "Synthetic exchange",
                    "source_kind": "official_exchange_price_tick_rule",
                    "request": {
                        "method": "GET",
                        "url": "https://example.invalid/rule",
                        "params": {},
                        "response_headers_relative_path": "meta/rule-headers.txt",
                    },
                }
            ],
            "supporting_artifacts": [
                {
                    "relative_path": "meta/rule-headers.txt",
                    "sha256": rebuild._sha256(price_header),
                    "bytes": price_header.stat().st_size,
                }
            ],
            "artifacts": [],
        },
    )
    rebuild._write_checksum_closure(root / "price-tick-rules/SHA256SUMS")

    _write(root / "git-chain/evidence.txt", "synthetic git evidence\n")
    rebuild._write_checksum_closure(root / "git-chain/SHA256SUMS")

    header_request = {
        "method": "POST",
        "url": "https://example.invalid/announcements",
        "params": request,
    }
    source_artifacts = [
        _artifact(
            root,
            "daily-bars/page-001.headers",
            source_kind="authoritative_exact_window_response_headers",
            request=header_request,
        ),
        _artifact(
            root,
            "daily-bars/page-001.request.json",
            source_kind="authoritative_exact_window_request_body",
            request=header_request,
        ),
        _artifact(
            root,
            "daily-bars/page-001.response.json",
            source_kind="authoritative_exact_window_announcement_inventory",
            request=header_request,
        ),
    ]
    _write_json(
        root / rebuild.SOURCE_MANIFEST_NAME,
        {
            "schema_version": "p3.3-s6-pairing-v3-source-manifest-v1",
            "generated_at": "2026-07-31T14:58:56:z",
            "artifact_count": len(source_artifacts),
            "artifacts": source_artifacts,
        },
    )
    rebuild._write_checksum_closure(root / rebuild.ROOT_CHECKSUM_NAME)
    return root


def _capture_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "capture"
    _write_json(
        root / "600782/page-001.request.json",
        {
            "symbol": "600782",
            "market": "SSE",
            "method": "GET",
            "url": "https://example.invalid/sse",
            "query": {
                "productId": "600782",
                "beginDate": "2026-01-30",
                "endDate": "2026-07-30",
                "pageHelp.pageNo": 1,
            },
        },
    )
    _write_json(
        root / "600782/page-001.response.json",
        {"total": 1, "result": [{"TITLE": "普通公告"}]},
    )
    _write_json(
        root / "600782/page-001.headers.json",
        {
            "schema_version": "legacy-sanitized-response-headers-v1",
            "status_code": 200,
            "fields": {
                "content-type": "application/json",
                "server": "must-be-removed",
                "x-request-id": "must-be-removed",
            },
        },
    )
    request_path = root / "600782/page-001.request.json"
    response_path = root / "600782/page-001.response.json"
    headers_path = root / "600782/page-001.headers.json"
    _write_json(
        root / "CAPTURE-MANIFEST.json",
        {
            "schema_version": "synthetic-capture-v1",
            "started_at": "2026-07-31T22:59:00+08:00",
            "completed_at": "2026-07-31T23:00:00+08:00",
            "captures": [
                {
                    "symbol": "600782",
                    "market": "SSE",
                    "frozen_window": ["2026-01-30", "2026-07-30"],
                    "page_size": 100,
                    "page_count": 1,
                    "official_total": 1,
                    "captured_total": 1,
                    "unique_row_ids": 1,
                    "pages": [
                        {
                            "page_number": 1,
                            "row_count": 1,
                            "request_sha256": rebuild._sha256(request_path),
                            "response_sha256": rebuild._sha256(response_path),
                            "headers_sha256": rebuild._sha256(headers_path),
                        }
                    ],
                }
            ],
        },
    )
    rebuild._write_checksum_closure(root / rebuild.ROOT_CHECKSUM_NAME)
    return root


def _patch_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    contract = tmp_path / "contract.json"
    _write_json(contract, {"contract": "synthetic frozen fixture"})
    monkeypatch.setattr(rebuild, "CONTRACT_SHA256", rebuild._sha256(contract))
    return contract


def _rounding_evidence_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    root = tmp_path / "rounding-evidence"
    target = {
        "A_STOCK_CODE": "600782",
        "A_DIV_DATE": "20260605",
        "PRE_CLOSE_PRICE": "2.64",
        "A_BEFR_TAX_DIV": "0.135",
    }
    _write_json(
        root / rebuild.ROUNDING_RESPONSE_NAME,
        {"result": [target]},
    )
    _write_json(
        root / rebuild.ROUNDING_EXTRACT_NAME,
        {
            "source_url": "https://example.invalid/commonQuery.do",
            "query": {"COMPANY_CODE": "600782"},
            "target_record": target,
        },
    )
    _write(
        root / "sse-company-profit-600782.headers.txt",
        (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "Server: must-be-removed\r\n"
            "X-Trace: must-be-removed\r\n\r\n"
        ),
    )
    rebuild._write_checksum_closure(root / rebuild.ROOT_CHECKSUM_NAME)
    monkeypatch.setattr(
        rebuild,
        "ROUNDING_RESPONSE_SHA256",
        rebuild._sha256(root / rebuild.ROUNDING_RESPONSE_NAME),
    )
    return root


def _szse_reference_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    entry = tmp_path / "szse-month-202605.html"
    detail = tmp_path / "szse-dividend-202605.html"
    entry.write_text(
        (
            "<html><head><title>2026年05月 深圳证券交易所市场统计月报"
            '</title></head><body><a href="'
            f"{rebuild.SZSE_DIVIDEND_TABLE_URL}"
            '">分红派息配股</a></body></html>'
        ),
        encoding="utf-8",
    )
    detail.write_bytes(
        (
            "<table><tr><th>Code</th><th>Securities</th><th>Bonus</th>"
            "<th>BPS</th><th>Cash Div.</th><th>DPS</th><th>Rts</th>"
            "<th>RPS</th><th>Pla. Pri.</th><th>Funds</th>"
            "<th>Ex-Date</th><th>Reg. Date</th><th>Ex-Price</th>"
            "<th>Pre-Closing</th></tr><tr><td>001260</td>"
            "<td>坤泰股份</td><td>0</td><td>0.000</td>"
            "<td>24,725,000</td><td>0.215</td><td></td><td></td>"
            "<td></td><td></td><td>2026/05/27</td>"
            "<td>2026/05/26</td><td>20.290</td><td>20.500</td>"
            "</tr></table>"
        ).encode("gb18030")
    )
    monkeypatch.setattr(
        rebuild,
        "SZSE_MONTH_ENTRY_SHA256",
        rebuild._sha256(entry),
    )
    monkeypatch.setattr(
        rebuild,
        "SZSE_DIVIDEND_TABLE_SHA256",
        rebuild._sha256(detail),
    )
    return entry, detail


def test_rebuild_is_deterministic_sanitized_and_checksum_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_fixture(tmp_path)
    capture = _capture_fixture(tmp_path)
    rounding_evidence = _rounding_evidence_fixture(tmp_path, monkeypatch)
    szse_entry, szse_table = _szse_reference_fixture(tmp_path, monkeypatch)
    contract = _patch_contract(monkeypatch, tmp_path)
    original_source_hash = rebuild._sha256(source / "daily-bars/page-001.headers")
    original_source_bytes = (source / "daily-bars/page-001.headers").stat().st_size

    output_a = tmp_path / "output-a"
    output_b = tmp_path / "output-b"
    for output in (output_a, output_b):
        result = rebuild.rebuild_bundle(
            source_bundle=source,
            announcement_capture=capture,
            rounding_evidence=rounding_evidence,
            szse_month_entry=szse_entry,
            szse_dividend_table=szse_table,
            contract=contract,
            output=output,
            generated_at="2026-07-31T23:30:00+08:00",
        )
        assert result["sanitized_header_count"] == 6
        assert result["rounding_evidence_included"] is True
        assert result["szse_reference_evidence_included"] is True

    assert rebuild._sha256(source / "daily-bars/page-001.headers") == (original_source_hash)
    assert (output_a / rebuild.ROOT_CHECKSUM_NAME).read_bytes() == (
        output_b / rebuild.ROOT_CHECKSUM_NAME
    ).read_bytes()

    # A previously rebuilt source already contains the frozen contract. Rebuilds
    # must verify and replace its manifest descriptor instead of failing on the
    # existing directory or duplicating the contract artifact.
    for frozen_file in output_a.rglob("*"):
        if frozen_file.is_file():
            frozen_file.chmod(0o444)
    output_c = tmp_path / "output-c"
    repeated_result = rebuild.rebuild_bundle(
        source_bundle=output_a,
        contract=contract,
        output=output_c,
        generated_at="2026-07-31T23:31:00+08:00",
    )
    assert repeated_result["rounding_evidence_included"] is True
    assert repeated_result["szse_reference_evidence_included"] is True
    rebuilt_again_manifest = json.loads(
        (output_c / rebuild.SOURCE_MANIFEST_NAME).read_text(encoding="utf-8")
    )
    assert (
        len(
            [
                item
                for item in rebuilt_again_manifest["artifacts"]
                if item["source_kind"] == "adjudication_contract"
            ]
        )
        == 1
    )

    # A chained rebuild must keep already-canonical headers byte-stable so the
    # copied capture manifest's per-page content-address bindings stay true.
    capture_header_relative = (
        f"{rebuild.CAPTURE_DESTINATION}/600782/page-001.headers.json"
    )
    assert (output_c / capture_header_relative).read_bytes() == (
        output_a / capture_header_relative
    ).read_bytes()
    chained_capture_manifest = json.loads(
        (
            output_c / rebuild.CAPTURE_DESTINATION / "CAPTURE-MANIFEST.json"
        ).read_text(encoding="utf-8")
    )
    chained_page = chained_capture_manifest["captures"][0]["pages"][0]
    assert chained_page["headers_sha256"] == rebuild._sha256(
        output_c / capture_header_relative
    )
    assert chained_page["response_sha256"] == rebuild._sha256(
        output_c / f"{rebuild.CAPTURE_DESTINATION}/600782/page-001.response.json"
    )

    # Capture package metadata artifacts must carry the native capture's
    # provenance timestamp, never null.
    package_artifacts = [
        item
        for item in json.loads(
            (output_a / rebuild.SOURCE_MANIFEST_NAME).read_text(encoding="utf-8")
        )["artifacts"]
        if item["source_kind"]
        in {"source_package_metadata", "source_package_checksum"}
        and item["relative_path"].startswith(rebuild.CAPTURE_DESTINATION)
    ]
    assert len(package_artifacts) == 2
    assert all(
        item["retrieved_at"] == "2026-07-31T23:00:00+08:00"
        for item in package_artifacts
    )

    canonical = json.loads((output_a / "daily-bars/page-001.headers").read_text(encoding="utf-8"))
    assert canonical["status"] == 200
    assert canonical["headers"] == {
        "content-length": "45",
        "content-type": "application/json;charset=UTF-8",
        "etag": '"fixture"',
        "last-modified": "Fri, 31 Jul 2026 14:00:00 GMT",
    }
    assert canonical["bindings"]["source"] == {
        "format": "raw-http-response-headers",
        "sha256": original_source_hash,
        "bytes": original_source_bytes,
    }
    rendered = json.dumps(canonical, ensure_ascii=False).casefold()
    assert "secret" not in rendered
    assert "server" not in rendered
    assert "trace" not in rendered
    assert "fri, 31 jul 2026 15:00:00 gmt" not in rendered
    capture_header = json.loads(
        (
            output_a
            / ("daily-bars/complete-unfiltered-announcement-inventory/600782/page-001.headers.json")
        ).read_text(encoding="utf-8")
    )
    assert capture_header["bindings"]["source"]["format"] == (
        "previously-sanitized-http-response-headers-json"
    )
    assert "server" not in capture_header["headers"]

    source_manifest = json.loads(
        (output_a / rebuild.SOURCE_MANIFEST_NAME).read_text(encoding="utf-8")
    )
    assert source_manifest["generated_at"] == "2026-07-31T23:30:00+08:00"
    assert source_manifest["header_sanitization"] == {
        "allowlist": [
            "status",
            "content-length",
            "content-type",
            "etag",
            "last-modified",
        ],
        "raw_header_bytes_retained": False,
        "sanitized_artifact_count": 6,
        "schema_version": rebuild.SANITIZED_HEADER_SCHEMA_VERSION,
    }
    contract_artifacts = [
        item
        for item in source_manifest["artifacts"]
        if item["source_kind"] == "adjudication_contract"
    ]
    assert len(contract_artifacts) == 1
    assert contract_artifacts[0]["relative_path"] == rebuild.CONTRACT_RELATIVE_PATH
    assert (output_a / rebuild.CONTRACT_RELATIVE_PATH).read_bytes() == contract.read_bytes()
    assert all(
        item.get("actual_fields", {}).get("server") is None for item in source_manifest["artifacts"]
    )
    rounding_responses = [
        item
        for item in source_manifest["artifacts"]
        if item["source_kind"] == "official_exchange_ex_reference_price_response"
        and item["actual_fields"].get("symbol") == "600782"
    ]
    assert len(rounding_responses) == 1
    assert rounding_responses[0]["actual_fields"] == {
        "A_BEFR_TAX_DIV": 0.135,
        "ex_date": "2026-06-05",
        "pre_close_price": 2.64,
        "symbol": "600782",
    }
    szse_reference = next(
        item
        for item in source_manifest["artifacts"]
        if item["source_kind"] == "official_exchange_ex_reference_price_response"
        and item["actual_fields"].get("symbol") == "001260"
    )
    assert szse_reference["actual_fields"] == {
        "cash_dividend_per_share": 0.215,
        "ex_date": "2026-05-27",
        "ex_reference_price": 20.29,
        "pre_closing_price": 20.5,
        "registration_date": "2026-05-26",
        "security_name": "坤泰股份",
        "symbol": "001260",
    }
    szse_entry_request = json.loads(
        (
            output_a
            / rebuild.SZSE_REFERENCE_DESTINATION
            / "szse-monthly-report-202605.request.json"
        ).read_text(encoding="utf-8")
    )
    assert szse_entry_request == {
        "metadata_provenance": "exact official page URL is frozen with the byte response",
        "method": "GET",
        "params": {},
        "url": "https://www.szse.cn/market/periodical/month/t20260605_620906.html",
    }
    szse_extract = json.loads(
        (
            output_a
            / rebuild.SZSE_REFERENCE_DESTINATION
            / rebuild.SZSE_DIVIDEND_EXTRACT_NAME
        ).read_text(encoding="utf-8")
    )
    assert szse_extract["entry_url"] == szse_entry_request["url"]
    szse_header = json.loads(
        (
            output_a
            / rebuild.SZSE_REFERENCE_DESTINATION
            / "szse-dividend-bonus-rights-202605.headers.json"
        ).read_text(encoding="utf-8")
    )
    assert szse_header["status"] == 200
    assert szse_header["headers"] == {
        "content-length": str(szse_table.stat().st_size),
        "content-type": "text/html; charset=GBK",
        "etag": '"6a227bda-34bef"',
        "last-modified": "Fri, 05 Jun 2026 07:33:46 GMT",
    }

    for checksum in output_a.rglob(rebuild.ROOT_CHECKSUM_NAME):
        rebuild._verify_checksum_closure(checksum)
    rebuild._validate_daily_manifest(output_a)


def test_rebuild_fails_closed_on_tampered_nested_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_fixture(tmp_path)
    contract = _patch_contract(monkeypatch, tmp_path)
    _write(source / "git-chain/evidence.txt", "tampered\n")

    with pytest.raises(ValueError, match="checksum mismatch"):
        rebuild.rebuild_bundle(
            source_bundle=source,
            contract=contract,
            output=tmp_path / "output",
            generated_at="2026-07-31T23:30:00+08:00",
        )
    assert not (tmp_path / "output").exists()


def test_rebuild_rejects_tampered_szse_raw_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_fixture(tmp_path)
    contract = _patch_contract(monkeypatch, tmp_path)
    entry, table = _szse_reference_fixture(tmp_path, monkeypatch)
    raw = table.read_bytes().decode("gb18030")
    table.write_bytes(raw.replace("20.290", "20.300", 1).encode("gb18030"))

    with pytest.raises(ValueError, match="dividend table SHA-256 differs"):
        rebuild.rebuild_bundle(
            source_bundle=source,
            contract=contract,
            output=tmp_path / "output",
            generated_at="2026-07-31T23:30:00+08:00",
            szse_month_entry=entry,
            szse_dividend_table=table,
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "generated_at",
    (
        "2026-07-31T14:58:56:z",
        "2026-07-31T23:30:00",
        "not-a-date",
    ),
)
def test_rebuild_rejects_invalid_generated_at(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    generated_at: str,
) -> None:
    source = _source_fixture(tmp_path)
    contract = _patch_contract(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="timezone-aware ISO-8601"):
        rebuild.rebuild_bundle(
            source_bundle=source,
            contract=contract,
            output=tmp_path / "output",
            generated_at=generated_at,
        )


def test_rebuild_refuses_to_overwrite_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_fixture(tmp_path)
    contract = _patch_contract(monkeypatch, tmp_path)
    output = tmp_path / "output"
    output.mkdir()

    with pytest.raises(ValueError, match="refusing to overwrite"):
        rebuild.rebuild_bundle(
            source_bundle=source,
            contract=contract,
            output=output,
            generated_at="2026-07-31T23:30:00+08:00",
        )


def test_rebuild_requires_frozen_contract_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_fixture(tmp_path)
    contract = _patch_contract(monkeypatch, tmp_path)
    expected = rebuild.CONTRACT_SHA256
    _write(contract, b"changed")

    with pytest.raises(ValueError, match=f"expected={expected}"):
        rebuild.rebuild_bundle(
            source_bundle=source,
            contract=contract,
            output=tmp_path / "output",
            generated_at="2026-07-31T23:30:00+08:00",
        )


def test_canonical_json_does_not_mutate_manifest_input(
    tmp_path: Path,
) -> None:
    source = _source_fixture(tmp_path)
    manifest = json.loads((source / rebuild.SOURCE_MANIFEST_NAME).read_text(encoding="utf-8"))
    frozen = copy.deepcopy(manifest)
    rebuild._artifact_index(manifest)
    assert manifest == frozen


def test_raw_source_binding_hash_is_sha256() -> None:
    content = b"HTTP/1.1 200 OK\r\n\r\n"
    assert rebuild._sha256_bytes(content) == hashlib.sha256(content).hexdigest()
