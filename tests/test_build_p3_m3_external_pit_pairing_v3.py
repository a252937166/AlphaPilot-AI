from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts/build_p3_m3_external_pit_pairing_v3.py"
_REAL_SOURCE = Path("/tmp/alphapilot-p3-s6-pairing-v3-source-bundle-rebuilt-v3-final-20260731")
_REAL_DB = _ROOT / "data/alphapilot.db"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pairing_v3_builder", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_script()


def _real_bundle_ready() -> bool:
    manifest_path = _REAL_SOURCE / "SOURCE-MANIFEST.json"
    if not manifest_path.is_file() or not _REAL_DB.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return False
    rule_kinds = {
        "official_exchange_price_tick_rule",
        "official_exchange_rule",
    }
    return (
        len(
            [
                artifact
                for artifact in artifacts
                if isinstance(artifact, dict) and artifact.get("source_kind") in rule_kinds
            ]
        )
        == 3
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _write_root_checksum_closure(root: Path) -> None:
    checksum_path = root / builder.GENERAL_SOURCE_CHECKSUM_NAME
    paths = sorted(path for path in root.rglob("*") if path.is_file() and path != checksum_path)
    checksum_path.write_text(
        "".join(f"{_sha256(path)}  {path.relative_to(root).as_posix()}\n" for path in paths),
        encoding="utf-8",
    )


def _rewrite_manifest(root: Path, manifest: dict[str, Any]) -> None:
    _write_json(root / builder.SOURCE_MANIFEST_NAME, manifest)
    _write_root_checksum_closure(root)


def _artifact(
    root: Path,
    *,
    path: str,
    source_kind: str,
    source_identity: str,
    request: dict[str, Any],
    actual_fields: dict[str, Any],
    body: object,
    missing_status: str = "none",
    routing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = root / path
    if isinstance(body, bytes):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    else:
        _write_json(target, body)
    result: dict[str, Any] = {
        "relative_path": path,
        "sha256": _sha256(target),
        "bytes": target.stat().st_size,
        "source_identity": source_identity,
        "source_kind": source_kind,
        "request": request,
        "retrieved_at": "2026-07-31T14:00:00+08:00",
        "parser_version": "fixture-v1",
        "actual_fields": actual_fields,
        "missing_state": {
            "status": missing_status,
            "details": None,
        },
    }
    if routing is not None:
        result["routing"] = routing
    return result


def _price_tick_artifacts(root: Path) -> list[dict[str, Any]]:
    specs = (
        ("SZSE", "1990-01-01", None, "rules/szse.json"),
        ("SSE", "1990-01-01", "2026-07-05", "rules/sse-pre.json"),
        ("SSE", "2026-07-06", None, "rules/sse-current.json"),
    )
    return [
        _artifact(
            root,
            path=path,
            source_kind="official_exchange_price_tick_rule",
            source_identity=f"{market} official trading rules",
            request={
                "method": "GET",
                "url": (
                    f"https://www.szse.cn/{path}"
                    if market == "SZSE"
                    else f"https://www.sse.com.cn/{path}"
                ),
                "params": {},
            },
            actual_fields={
                "market": f"{market}-A",
                "security_type": "A_share",
                "currency": "CNY",
                "price_tick": 0.01,
                "effective_from": effective_from,
                "effective_to": effective_to,
            },
            body={
                "market": market,
                "price_tick": 0.01,
                "effective_from": effective_from,
                "effective_to": effective_to,
            },
        )
        for market, effective_from, effective_to, path in specs
    ]


def _action_artifacts(root: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for (symbol, _trade_date), events in builder._DAILY_EVENTS.items():
        for event in events:
            ex_date = str(event["ex_date"])
            source_host = "static.sse.com.cn" if symbol.startswith("6") else "disc.static.szse.cn"
            artifacts.append(
                _artifact(
                    root,
                    path=f"actions/{symbol}-{ex_date}.pdf",
                    source_kind="official_exchange_corporate_action_pdf",
                    source_identity="official exchange disclosure",
                    request={
                        "method": "GET",
                        "url": (f"https://{source_host}/official/{symbol}-{ex_date}.pdf"),
                        "params": None,
                    },
                    actual_fields={
                        "symbol": symbol,
                        "ex_date": ex_date,
                        "cash_dividend_per_share": event["cash_dividend_per_share"],
                        "bonus_share_per_share": 0,
                        "transfer_share_per_share": 0,
                    },
                    body=f"official action {symbol} {ex_date}".encode(),
                )
            )
    return artifacts


def _inventory_artifacts(root: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for symbol, trade_date in builder._DAILY_KEYS:
        events = builder._DAILY_EVENTS[(symbol, trade_date)]
        action_count = len(events)
        params: dict[str, Any]
        body: dict[str, Any]
        if symbol.startswith("6"):
            params = {
                "productId": symbol,
                "beginDate": trade_date,
                "endDate": "2026-07-30",
                "reportType": "ALL",
                "reportType2": "",
                "keyWord": "",
                "pageHelp.pageNo": 1,
                "pageHelp.beginPage": 1,
                "pageHelp.pageSize": 100,
            }
            body = {
                "pageHelp": {
                    "total": action_count,
                    "data": [
                        {
                            "SECURITY_CODE": symbol,
                            "TITLE": f"{symbol}：权益分派实施公告",
                            "URL": (f"/official/{symbol}-{event['ex_date']}.pdf"),
                            "SSEDATE": event["ex_date"],
                        }
                        for event in events
                    ],
                }
            }
            source_identity = "SSE official announcement inventory"
            method = "GET"
            url = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
        else:
            params = {
                "stock": [symbol],
                "channelCode": ["listedNotice_disc"],
                "seDate": [trade_date, "2026-07-30"],
                "searchKey": "",
                "pageSize": 50,
                "pageNum": 1,
            }
            body = {
                "announceCount": action_count,
                "data": [
                    {
                        "annId": f"{symbol}-{event['ex_date']}",
                        "secCode": [symbol],
                        "title": f"{symbol}：权益分派实施公告",
                        "attachPath": (f"/official/{symbol}-{event['ex_date']}.pdf"),
                        "publishTime": f"{event['ex_date']} 08:00:00",
                    }
                    for event in events
                ],
            }
            source_identity = "SZSE official announcement inventory"
            method = "POST"
            url = "https://www.szse.cn/api/disc/announcement/annList"
        artifacts.append(
            _artifact(
                root,
                path=f"inventory/{symbol}-p001.json",
                source_kind="complete_unfiltered_announcement_inventory",
                source_identity=source_identity,
                request={
                    "method": method,
                    "url": url,
                    "params": params,
                },
                actual_fields={
                    "symbol": symbol,
                    "page_number": 1,
                    "official_total": action_count,
                    "row_count": action_count,
                    "window_start": trade_date,
                    "window_end": "2026-07-30",
                    "unfiltered": True,
                    "taxonomy_version": (builder.ADJUSTMENT_EVENT_TAXONOMY_VERSION),
                    "classification_counts": {
                        "implemented_adjustment_event": action_count,
                        "not_factor_adjustment": 0,
                        "not_adjustment_related": 0,
                        "unknown_adjustment_candidate": 0,
                    },
                },
                body=body,
                routing={
                    "pairing_candidate_use": True,
                    "authoritative_exact_window": True,
                },
            )
        )
    return artifacts


def _reference_price_artifact(root: Path) -> dict[str, Any]:
    return _artifact(
        root,
        path="reference/sse-600782-20260605.json",
        source_kind="official_exchange_ex_reference_price_response",
        source_identity="SSE official dividend and reference-price response",
        request={
            "method": "GET",
            "url": "https://query.sse.com.cn/commonSoaQuery.do",
            "params": {
                "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_LRFP_FH_L",
                "COMPANY_CODE": "600782",
            },
        },
        actual_fields={
            "symbol": "600782",
            "ex_date": "2026-06-05",
            "pre_close_price": 2.64,
            "A_BEFR_TAX_DIV": 0.135,
        },
        body={
            "result": [
                {
                    "A_STOCK_CODE": "600782",
                    "A_DIV_DATE": "20260605",
                    "PRE_CLOSE_PRICE": 2.64,
                    "A_BEFR_TAX_DIV": 0.135,
                }
            ]
        },
    )


def _szse_reference_price_artifact(root: Path) -> dict[str, Any]:
    path = root / "reference/szse-001260-20260527.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            "<table><tr>"
            "<th>Code</th><th>Securities</th><th>Bonus</th><th>BPS</th>"
            "<th>Cash Div.</th><th>DPS</th><th>Rts</th><th>RPS</th>"
            "<th>Pla. Pri.</th><th>Funds</th><th>Ex-Date</th>"
            "<th>Reg. Date</th><th>Ex-Price</th><th>Pre-Closing</th>"
            "</tr><tr>"
            "<td>001260</td><td>坤泰股份</td><td>0</td><td>0.000</td>"
            "<td>24,725,000</td><td>0.215</td><td></td><td></td>"
            "<td></td><td></td><td>2026/05/27</td>"
            "<td>2026/05/26</td><td>20.290</td><td>20.500</td>"
            "</tr></table>"
        ).encode("gb18030")
    )
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "source_identity": ("SZSE official May 2026 dividend, bonus and rights table"),
        "source_kind": "official_exchange_ex_reference_price_response",
        "request": {
            "method": "GET",
            "url": (
                "https://docs.static.szse.cn/www/market/periodical/month/"
                "W020260605534753848014.html"
            ),
            "params": {},
        },
        "retrieved_at": "2026-07-31T14:00:00+08:00",
        "parser_version": "szse-monthly-dividend-html-gb18030-v1",
        "actual_fields": {
            "symbol": "001260",
            "security_name": "坤泰股份",
            "ex_date": "2026-05-27",
            "registration_date": "2026-05-26",
            "ex_reference_price": 20.29,
            "pre_closing_price": 20.50,
            "cash_dividend_per_share": 0.215,
        },
        "missing_state": {"status": "none", "details": None},
    }


def _contract_artifact(root: Path) -> dict[str, Any]:
    contract_source = _ROOT / "docs/P3.3-S6-external-pit-adjudication-v1.contract.json"
    assert _sha256(contract_source) == builder.ADJUDICATION_CONTRACT_SHA256
    target = root / "contract/P3.3-S6-external-pit-adjudication-v1.contract.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(contract_source.read_bytes())
    return {
        "relative_path": target.relative_to(root).as_posix(),
        "sha256": _sha256(target),
        "bytes": target.stat().st_size,
        "source_identity": "frozen architect adjudication contract",
        "source_kind": "adjudication_contract",
        "request": {
            "method": "LOCAL_COPY",
            "url": contract_source.as_uri(),
            "params": None,
        },
        "retrieved_at": "2026-07-31T14:00:00+08:00",
        "parser_version": "fixture-v1",
        "actual_fields": {
            "contract_version": builder.ADJUDICATION_CONTRACT_VERSION,
        },
        "missing_state": {"status": "none", "details": None},
    }


def _financial_artifacts(root: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for symbol, report_period, metric in builder._FINANCIAL_KEYS:
        key = (symbol, report_period, metric)
        formula = builder._FINANCIAL_FORMULAS.get(key)
        if formula is None:
            actual_fields: dict[str, Any] = {
                "sample_key": {
                    "symbol": symbol,
                    "report_period": report_period,
                    "metric": metric,
                },
                "strict_labels": {"主营业务收入": 0, "主营营业收入": 0},
                "approximate_labels_rejected": {"营业收入": 2, "营业总收入": 1},
                "local_value": None,
            }
            missing_status = "expected_unavailable_candidate_only"
        elif metric == "net_profit_yoy":
            actual_fields = {
                "sample_key": {
                    "symbol": symbol,
                    "report_period": report_period,
                    "metric": metric,
                },
                "line_items": {
                    "consolidated_net_profit_current": formula["operands"][0][1],
                    "consolidated_net_profit_prior": formula["operands"][1][1],
                },
            }
            missing_status = "none"
        else:
            actual_fields = {
                "sample_key": {
                    "symbol": symbol,
                    "report_period": report_period,
                    "metric": metric,
                },
                "line_items": {
                    "parent_net_profit_current": formula["operands"][0][1],
                    "parent_equity_opening": formula["operands"][1][1],
                    "parent_equity_closing": formula["operands"][2][1],
                },
            }
            missing_status = "none"
        artifacts.append(
            _artifact(
                root,
                path=f"financial/{symbol}-{report_period}-{metric}.pdf",
                source_kind="official_financial_report_pdf",
                source_identity="CNInfo official report",
                request={
                    "method": "GET",
                    "url": (f"https://static.cninfo.com.cn/{symbol}/{report_period}.pdf"),
                    "params": None,
                },
                actual_fields=actual_fields,
                body=f"official report {symbol} {report_period}".encode(),
                missing_status=missing_status,
            )
        )
    return artifacts


def _valuation_artifacts(root: Path) -> list[dict[str, Any]]:
    preflight = json.loads(builder.DEFAULT_PREFLIGHT.read_text(encoding="utf-8"))["pit_samples"][
        "valuation_daily"
    ]
    values = {(str(row["symbol"]), str(row["trade_date"])): row for row in preflight}
    artifacts: list[dict[str, Any]] = []
    for symbol, trade_date in builder._VALUATION_KEYS:
        row = values[(symbol, trade_date)]
        target = {
            "symbol": symbol,
            "trade_date": trade_date,
            "pe_ttm": row["pe_ttm"],
            "pb_mrq": row["pb_mrq"],
            "ps_ttm": row["ps_ttm"],
        }
        artifacts.append(
            _artifact(
                root,
                path=f"valuation/{symbol}.json",
                source_kind="valuation_raw_json_response",
                source_identity="Eastmoney valuation API",
                request={
                    "method": "GET",
                    "url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
                    "params": {"symbol": symbol},
                },
                actual_fields={"target": target},
                body={
                    "success": True,
                    "result": {
                        "data": [
                            {
                                "SECURITY_CODE": symbol,
                                "TRADE_DATE": trade_date,
                                "PE_TTM": row["pe_ttm"],
                                "PB_MRQ": row["pb_mrq"],
                                "PS_TTM": row["ps_ttm"],
                            }
                        ]
                    },
                },
            )
        )
    return artifacts


def _source_bundle(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    artifacts = [
        _contract_artifact(source),
        *_price_tick_artifacts(source),
        _reference_price_artifact(source),
        _szse_reference_price_artifact(source),
        *_action_artifacts(source),
        *_inventory_artifacts(source),
        *_financial_artifacts(source),
        *_valuation_artifacts(source),
    ]
    manifest = {
        "schema_version": builder.GENERAL_SOURCE_MANIFEST_SCHEMA_VERSION,
        "generated_at": "2026-07-31T14:00:00+08:00",
        "approval_state": {
            "approved": False,
            "signed": False,
            "s6_done_claimed": False,
        },
        "frozen_bindings": {
            "local_manifest_sha256": builder.FROZEN_MANIFEST_SHA256,
            "final_trial_sha256": builder.FROZEN_FINAL_TRIAL_SHA256,
            "seed": builder.FROZEN_SEED,
            "sample_count": 15,
        },
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    _rewrite_manifest(source, manifest)
    return source


def _database(path: Path) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE daily_bars (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                close REAL NOT NULL,
                source TEXT NOT NULL
            )
            """
        )
        rows = (
            ("001260", "2026-05-26", 20.50, "baostock"),
            ("600648", "2026-07-10", 8.54, "baostock"),
            ("600782", "2026-06-04", 2.77, "baostock"),
            ("000831", "2023-06-19", 30.61, "baostock"),
            ("000831", "2024-06-24", 24.69, "baostock"),
            ("000831", "2026-07-02", 53.20, "baostock"),
            ("001205", "2024-06-03", 17.70, "baostock"),
            ("001205", "2025-06-05", 16.20, "baostock"),
            ("001205", "2026-05-25", 14.69, "baostock"),
        )
        connection.executemany("INSERT INTO daily_bars VALUES (?, ?, ?, ?)", rows)
        connection.commit()
    finally:
        connection.close()
    return path


def test_builder_creates_only_an_unsigned_machine_validated_15_sample_bundle(
    tmp_path: Path,
) -> None:
    source = _source_bundle(tmp_path)
    database = _database(tmp_path / "production.db")
    before_db_sha256 = _sha256(database)
    output = tmp_path / "candidate"

    validation = builder.build_pairing_v3_bundle(
        source_bundle=source,
        output=output,
        db=database,
    )

    assert _sha256(database) == before_db_sha256
    assert validation["approved"] is False
    assert validation["reviewer_role"] == "pending"
    assert validation["reviewed_at"] is None
    assert validation["validation"] == {
        "schema_version": builder.PAIRING_V3_SCHEMA_VERSION,
        "adjudication_contract": builder.ADJUDICATION_CONTRACT_VERSION,
            "final_trial_sha256": builder.FROZEN_FINAL_TRIAL_SHA256,
            "reviewer_type": "pending",
            "reviewer_role": "pending",
        "reviewed_at": "",
        "sample_count": 15,
        "numeric_match": 5,
        "formula_match": 8,
        "expected_unavailable": 2,
        "signature_status": "unsigned_candidate",
    }
    candidate = json.loads((output / "pairing-v3-candidate.json").read_text(encoding="utf-8"))
    assert validation["candidate_canonical_sha256"] == builder.canonical_sha256(candidate)
    assert validation["candidate_file_sha256"] == _sha256(output / "pairing-v3-candidate.json")
    assert candidate["approved"] is False
    assert candidate["summary"]["unresolved"] == 0
    assert (output / "final-trial.json").is_file()
    assert (output / "frozen-local-preclose.json").is_file()
    copied_contract = (
        output / "raw-source" / "contract" / "P3.3-S6-external-pit-adjudication-v1.contract.json"
    )
    assert _sha256(copied_contract) == builder.ADJUDICATION_CONTRACT_SHA256
    assert (output / "raw-source" / "inventory" / "001260-p001.json").is_file()
    preclose = json.loads((output / "frozen-local-preclose.json").read_text(encoding="utf-8"))
    assert preclose["database_open_mode"] == "ro"
    assert preclose["query_only"] is True
    assert preclose["row_count"] == 9
    assert {row["source"] for row in preclose["rows"]} == {"baostock"}

    daily = [sample for sample in candidate["samples"] if sample["table"] == "daily_bars"]
    assert sum(len(sample["formula_proof"]["operands"]) for sample in daily) == 9
    assert all(
        "price_tick_evidence_id" in operand["event"]
        for sample in daily
        for operand in sample["formula_proof"]["operands"]
    )
    artifact_by_id = {artifact["id"]: artifact for artifact in candidate["artifacts"]}
    assert all(
        artifact_by_id[operand["event"]["price_tick_evidence_id"]]["source_kind"]
        == "official_exchange_rule"
        for sample in daily
        for operand in sample["formula_proof"]["operands"]
    )
    assert all(
        "price_tick"
        not in artifact_by_id[operand["event"]["announcement_evidence_id"]]["actual_fields"]
        for sample in daily
        for operand in sample["formula_proof"]["operands"]
    )
    daily_001260 = next(
        sample
        for sample in daily
        if sample["key"] == {"symbol": "001260", "trade_date": "2025-10-29"}
    )
    event_001260 = daily_001260["formula_proof"]["operands"][0]["event"]
    assert event_001260["event_formula_id"] == ("official_reference_price_ratio_v1")
    assert event_001260["ex_reference_price"] == 20.29
    assert event_001260["rounding_provenance"] == (
        "exchange_published_reference_price_local_rounding_none"
    )
    assert event_001260["reference_price_evidence_id"]

    yoy = [
        sample for sample in candidate["samples"] if sample["key"].get("metric") == "net_profit_yoy"
    ]
    assert {
        operand["line_item"] for sample in yoy for operand in sample["formula_proof"]["operands"]
    } == {"净利润（本期累计）", "净利润（上年同期累计）"}
    assert all(
        "归属于母公司" not in operand["line_item"]
        for sample in yoy
        for operand in sample["formula_proof"]["operands"]
    )
    identities = {
        (
            sample["table"],
            tuple(sorted(sample["key"].items())),
        )
        for sample in candidate["samples"]
    }
    expected_identities = {
        *{
            ("daily_bars", tuple(sorted({"symbol": key[0], "trade_date": key[1]}.items())))
            for key in builder._DAILY_KEYS
        },
        *{
            (
                "financial_indicators",
                tuple(
                    sorted(
                        {
                            "symbol": key[0],
                            "report_period": key[1],
                            "metric": key[2],
                        }.items()
                    )
                ),
            )
            for key in builder._FINANCIAL_KEYS
        },
        *{
            (
                "valuation_daily",
                tuple(sorted({"symbol": key[0], "trade_date": key[1]}.items())),
            )
            for key in builder._VALUATION_KEYS
        },
    }
    assert identities == expected_identities
    exact_window = next(
        sample
        for sample in daily
        if sample["key"] == {"symbol": "001260", "trade_date": "2025-10-29"}
    )["formula_proof"]["event_window"]
    assert exact_window["page_count"] == 1
    assert [page["page_number"] for page in exact_window["pages"]] == [1]
    assert exact_window["taxonomy_version"] == (builder.ADJUSTMENT_EVENT_TAXONOMY_VERSION)
    assert exact_window["classification_summary"] == {
        "implemented_adjustment_event": 1,
        "not_factor_adjustment": 0,
        "not_adjustment_related": 0,
        "unknown_adjustment_candidate": 0,
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest: next(
                artifact
                for artifact in manifest["artifacts"]
                if artifact["source_kind"] == "complete_unfiltered_announcement_inventory"
                and artifact["actual_fields"]["symbol"] == "001260"
            )["request"]["params"]["seDate"].__setitem__(0, "2025-01-01"),
            "not the exact frozen window",
        ),
        (
            lambda manifest: next(
                artifact
                for artifact in manifest["artifacts"]
                if artifact["source_kind"] == "official_financial_report_pdf"
                and artifact["actual_fields"]["sample_key"]["metric"] == "net_profit_yoy"
            )["actual_fields"].__setitem__(
                "line_items",
                {
                    "parent_net_profit_current": "368514961.65",
                    "parent_net_profit_prior": "494218221.65",
                },
            ),
            "original line-item labels differ",
        ),
        (
            lambda manifest: next(
                artifact
                for artifact in manifest["artifacts"]
                if artifact["source_kind"] == "official_exchange_price_tick_rule"
                and artifact["actual_fields"]["market"] == "SZSE-A"
            )["actual_fields"].__setitem__("effective_from", "2026-07-04"),
            "price-tick rule coverage must be unique",
        ),
    ],
)
def test_builder_fails_closed_on_incomplete_window_wrong_profit_scope_or_rule_gap(
    tmp_path: Path,
    mutate: Any,
    message: str,
) -> None:
    source = _source_bundle(tmp_path)
    manifest_path = source / builder.SOURCE_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tampered = copy.deepcopy(manifest)
    mutate(tampered)
    _rewrite_manifest(source, tampered)
    database = _database(tmp_path / "production.db")

    with pytest.raises(ValueError, match=message):
        builder.build_pairing_v3_bundle(
            source_bundle=source,
            output=tmp_path / "candidate",
            db=database,
        )
    assert not (tmp_path / "candidate").exists()


def test_builder_rejects_tampered_szse_reference_raw_after_hash_rebinding(
    tmp_path: Path,
) -> None:
    source = _source_bundle(tmp_path)
    manifest_path = source / builder.SOURCE_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = next(
        item
        for item in manifest["artifacts"]
        if item["source_kind"] == "official_exchange_ex_reference_price_response"
        and item["actual_fields"]["symbol"] == "001260"
    )
    target = source / str(artifact["relative_path"])
    body = target.read_bytes().decode("gb18030")
    target.write_bytes(body.replace("20.290", "20.300", 1).encode("gb18030"))
    artifact["sha256"] = _sha256(target)
    artifact["bytes"] = target.stat().st_size
    _rewrite_manifest(source, manifest)
    database = _database(tmp_path / "production.db")

    with pytest.raises(
        ValueError,
        match="official ex-reference response target values differ",
    ):
        builder.build_pairing_v3_bundle(
            source_bundle=source,
            output=tmp_path / "candidate",
            db=database,
        )


def _inventory_artifact(
    manifest: dict[str, Any],
    symbol: str,
) -> dict[str, Any]:
    return next(
        artifact
        for artifact in manifest["artifacts"]
        if artifact["source_kind"] == "complete_unfiltered_announcement_inventory"
        and artifact["actual_fields"]["symbol"] == symbol
    )


def _rewrite_artifact_body(
    source: Path,
    manifest: dict[str, Any],
    artifact: dict[str, Any],
    body: dict[str, Any],
) -> None:
    target = source / str(artifact["relative_path"])
    _write_json(target, body)
    artifact["sha256"] = _sha256(target)
    artifact["bytes"] = target.stat().st_size
    _rewrite_manifest(source, manifest)


@pytest.mark.parametrize(
    ("symbol", "field", "value", "message"),
    [
        (
            "001260",
            "searchKey",
            "权益分派实施公告",
            "exact-window, unfiltered",
        ),
        (
            "600782",
            "keyWord",
            "权益分派实施公告",
            "exact-window, unfiltered",
        ),
        (
            "600782",
            "reportType2",
            "DQBG",
            "exact-window, unfiltered",
        ),
    ],
)
def test_builder_rejects_filtered_or_report_type_narrowed_inventory_requests(
    tmp_path: Path,
    symbol: str,
    field: str,
    value: str,
    message: str,
) -> None:
    source = _source_bundle(tmp_path)
    manifest = json.loads((source / builder.SOURCE_MANIFEST_NAME).read_text(encoding="utf-8"))
    _inventory_artifact(manifest, symbol)["request"]["params"][field] = value
    _rewrite_manifest(source, manifest)

    with pytest.raises(ValueError, match=message):
        builder.build_pairing_v3_bundle(
            source_bundle=source,
            output=tmp_path / "candidate",
            db=_database(tmp_path / "production.db"),
        )


def test_builder_rejects_unknown_high_risk_adjustment_title_with_valid_hashes(
    tmp_path: Path,
) -> None:
    source = _source_bundle(tmp_path)
    manifest = json.loads((source / builder.SOURCE_MANIFEST_NAME).read_text(encoding="utf-8"))
    artifact = _inventory_artifact(manifest, "600782")
    body_path = source / str(artifact["relative_path"])
    body = json.loads(body_path.read_text(encoding="utf-8"))
    body["pageHelp"]["data"][0]["TITLE"] = "股票价格调整实施公告"
    _rewrite_artifact_body(source, manifest, artifact, body)

    with pytest.raises(
        ValueError,
        match="unclassified adjustment candidates",
    ):
        builder.build_pairing_v3_bundle(
            source_bundle=source,
            output=tmp_path / "candidate",
            db=_database(tmp_path / "production.db"),
        )


def test_builder_rejects_missing_inventory_page_with_valid_hashes(
    tmp_path: Path,
) -> None:
    source = _source_bundle(tmp_path)
    manifest = json.loads((source / builder.SOURCE_MANIFEST_NAME).read_text(encoding="utf-8"))
    artifact = _inventory_artifact(manifest, "001260")
    body_path = source / str(artifact["relative_path"])
    body = json.loads(body_path.read_text(encoding="utf-8"))
    body["announceCount"] = 2
    artifact["actual_fields"]["official_total"] = 2
    _rewrite_artifact_body(source, manifest, artifact, body)

    with pytest.raises(ValueError, match="pagination is incomplete"):
        builder.build_pairing_v3_bundle(
            source_bundle=source,
            output=tmp_path / "candidate",
            db=_database(tmp_path / "production.db"),
        )


def test_builder_rejects_duplicate_inventory_identity_with_valid_hashes(
    tmp_path: Path,
) -> None:
    source = _source_bundle(tmp_path)
    manifest = json.loads((source / builder.SOURCE_MANIFEST_NAME).read_text(encoding="utf-8"))
    artifact = _inventory_artifact(manifest, "001260")
    body_path = source / str(artifact["relative_path"])
    body = json.loads(body_path.read_text(encoding="utf-8"))
    benign = {
        "annId": "001260-duplicate-benign",
        "secCode": ["001260"],
        "title": "其他公告",
        "attachPath": "/official/001260-other.pdf",
        "publishTime": "2026-01-02 08:00:00",
    }
    body["data"].extend([benign, copy.deepcopy(benign)])
    body["announceCount"] = 3
    artifact["actual_fields"]["official_total"] = 3
    artifact["actual_fields"]["row_count"] = 3
    _rewrite_artifact_body(source, manifest, artifact, body)

    with pytest.raises(ValueError, match=r"duplicate/missing rows|identity"):
        builder.build_pairing_v3_bundle(
            source_bundle=source,
            output=tmp_path / "candidate",
            db=_database(tmp_path / "production.db"),
        )


def test_builder_rejects_invalid_generated_at_after_checksum_refresh(
    tmp_path: Path,
) -> None:
    source = _source_bundle(tmp_path)
    manifest = json.loads((source / builder.SOURCE_MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["generated_at"] = "2026-07-31T14:58:56:z"
    _rewrite_manifest(source, manifest)

    with pytest.raises(ValueError, match="timezone-aware ISO-8601"):
        builder.build_pairing_v3_bundle(
            source_bundle=source,
            output=tmp_path / "candidate",
            db=_database(tmp_path / "production.db"),
        )


def test_builder_rejects_root_checksum_missing_one_real_file(
    tmp_path: Path,
) -> None:
    source = _source_bundle(tmp_path)
    checksum_path = source / builder.GENERAL_SOURCE_CHECKSUM_NAME
    rows = checksum_path.read_text(encoding="utf-8").splitlines()
    checksum_path.write_text(
        "\n".join(row for row in rows if not row.endswith("inventory/001260-p001.json")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="checksum closure differs"):
        builder.build_pairing_v3_bundle(
            source_bundle=source,
            output=tmp_path / "candidate",
            db=_database(tmp_path / "production.db"),
        )


def test_builder_exposes_no_network_or_signing_cli_surface() -> None:
    parsed = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(parsed)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {str(node.module) for node in ast.walk(parsed) if isinstance(node, ast.ImportFrom)}
    assert imported.isdisjoint({"aiohttp", "httpx", "requests", "socket", "urllib.request"})
    parser = builder._parser()
    options = {option for action in parser._actions for option in action.option_strings}
    assert "--approve" not in options
    assert "--reviewer-role" not in options
    assert "--reviewed-at" not in options
    assert "--sign" not in options
    assert "build_signed_evidence" not in _SCRIPT.read_text(encoding="utf-8")


@pytest.mark.skipif(
    not _real_bundle_ready(),
    reason="read-only real source bundle with three tick rules is unavailable",
)
def test_real_read_only_source_bundle_builds_unsigned_15_of_15(
    tmp_path: Path,
) -> None:
    before_db_sha256 = _sha256(_REAL_DB)
    validation = builder.build_pairing_v3_bundle(
        source_bundle=_REAL_SOURCE,
        output=tmp_path / "real-candidate",
        db=_REAL_DB,
    )

    assert _sha256(_REAL_DB) == before_db_sha256
    assert validation["validation"]["sample_count"] == 15
    assert validation["validation"]["numeric_match"] == 5
    assert validation["validation"]["formula_match"] == 8
    assert validation["validation"]["expected_unavailable"] == 2
    assert validation["validation"]["signature_status"] == "unsigned_candidate"
    assert validation["approved"] is False
