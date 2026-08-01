from __future__ import annotations

import copy
import hashlib
import json
import shutil
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pytest

from alphapilot.backtest import data_health
from alphapilot.backtest import external_pit_adjudication as adjudication
from alphapilot.backtest.external_pit import ExternalPITError, build_signed_evidence_v2
from alphapilot.backtest.external_pit_adjudication import (
    ADJUDICATION_CONTRACT_SHA256,
    ADJUDICATION_CONTRACT_VERSION,
    ADJUSTMENT_EVENT_TAXONOMY_VERSION,
    AI_REVIEWER_ROLE,
    FROZEN_FINAL_TRIAL_SHA256,
    FROZEN_MANIFEST_SHA256,
    FROZEN_SAMPLE_SIZE_PER_TABLE,
    FROZEN_SEED,
    INDEPENDENT_AI_SIGNATURE_STATUS,
    INDEPENDENT_HUMAN_SIGNATURE_STATUS,
    LOCAL_STORAGE_QUANTUM,
    PAIRING_V3_SCHEMA_VERSION,
    build_unsigned_candidate_v3,
    canonical_sha256,
    classify_adjustment_announcement_title,
    normalized_unsigned_candidate_sha256,
    validate_ai_review_attestation,
    validate_pairing_v3,
    validate_pairing_v3_candidate,
)

_ROOT = Path(__file__).resolve().parents[1]
_FINAL_PREFLIGHT = _ROOT / "docs/phase3/reports/P3.3-S6-final-preflight-20260731.json"
_FINAL_TRIAL = _ROOT / "docs/phase3/reports/P3.3-S6-external-pit-final-trial-20260731.json"
_V4_UNSIGNED_CANDIDATE = (
    _ROOT
    / "docs/phase3/reports/"
    "AlphaPilot-P3.3-S6-pairing-v3-candidate-v4-unsigned-20260801.json"
)
_AI_REVIEW_ATTESTATION = (
    _ROOT
    / "docs/phase3/reports/"
    "AlphaPilot-P3.3-S6-Claude-Code-independent-ai-review-20260801.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(sample: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    table = str(sample["table"])
    key = sample["key"]
    assert isinstance(key, dict)
    fields = {
        "daily_bars": ("symbol", "trade_date"),
        "financial_indicators": ("symbol", "report_period", "metric"),
        "valuation_daily": ("symbol", "trade_date"),
    }[table]
    return table, tuple(str(key[field]) for field in fields)


def _artifact(
    tmp_path: Path,
    *,
    artifact_id: str,
    actual_fields: list[str],
    source_kind: str,
    missing_state: str = "present",
    request_parameters: dict[str, Any] | None = None,
    content: object | None = None,
    raw_bytes: bytes | None = None,
) -> dict[str, Any]:
    relative_path = (
        f"artifacts/{artifact_id}.html"
        if raw_bytes is not None
        else f"artifacts/{artifact_id}.json"
    )
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw_bytes is not None:
        path.write_bytes(raw_bytes)
    else:
        path.write_text(
            json.dumps(
                content
                if content is not None
                else {
                    "artifact_id": artifact_id,
                    "actual_fields": actual_fields,
                    "missing_state": missing_state,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    content_sha256 = _sha256(path)
    parameters = dict(request_parameters or {"business_key": artifact_id})
    if source_kind != "frozen_local_manifest":
        parameters.setdefault(
            "source_url",
            (
                "https://datacenter-web.eastmoney.com/api/data/v1/get"
                if source_kind == "audited_external_response"
                else "https://www.szse.cn/official-evidence"
            ),
        )
    return {
        "id": artifact_id,
        "relative_path": relative_path,
        "sha256": content_sha256,
        "source_kind": source_kind,
        "source_identity": (
            "frozen local manifest daily_bars source=baostock"
            if source_kind == "frozen_local_manifest"
            else "Shanghai/Shenzhen exchange trading rule"
            if source_kind == "official_exchange_rule"
            else "Shanghai/Shenzhen exchange issuer disclosure"
            if source_kind != "audited_external_response"
            else "Eastmoney stock_value_em frozen response"
        ),
        "request_parameters": parameters,
        "retrieved_at": "2026-07-31T12:00:00+08:00",
        "timezone": "Asia/Shanghai",
        "parser_version": "p3.3-s6-adjudication-parser-v1",
        "actual_fields": actual_fields,
        "content_scope": "full_response_body",
        "first_success": True,
        "first_success_response_sha256": content_sha256,
        "fallback_reason": None,
        "prior_source_errors": [],
        "missing_state": missing_state,
    }


def _operand(
    *,
    name: str,
    value: float,
    artifact_id: str,
    line_item: str,
    event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "lower": value,
        "upper": value,
        "unit": "ratio_decimal" if event is not None else "CNY",
        "line_item": line_item,
        "disclosure_precision": {
            "basis": "exact_machine_fact",
            "quantum": 0.0,
        },
        "evidence_id": artifact_id,
        "event": event,
    }


_DAILY_EVENT_FIXTURES: dict[
    tuple[str, str],
    tuple[tuple[str, float, float, float], ...],
] = {
    ("001260", "2025-10-29"): (("2026-05-27", 0.215, 0.0, 20.50),),
    ("600648", "2025-06-16"): (("2026-07-13", 0.35, 0.0, 8.54),),
    ("600782", "2026-01-30"): (("2026-06-05", 0.135, 0.0, 2.77),),
    ("000831", "2023-05-29"): (
        ("2023-06-20", 0.04, 0.0, 30.61),
        ("2024-06-25", 0.08, 0.0, 24.69),
        ("2026-07-03", 0.029, 0.0, 53.20),
    ),
    ("001205", "2024-05-21"): (
        ("2024-06-04", 0.1186399, 0.0, 17.70),
        ("2025-06-06", 0.1183647, 0.0, 16.20),
        ("2026-05-26", 0.1479559, 0.0, 14.69),
    ),
}

_FINANCIAL_FIXTURES: dict[
    tuple[str, str, str],
    tuple[str, tuple[tuple[str, float, str], ...]],
] = {
    ("000897", "2024Q3", "net_profit_yoy"): (
        "net_profit_yoy_v1",
        (
            ("net_profit_t", 368_514_961.65, "净利润（本期累计）"),
            ("net_profit_t_minus_4", 494_218_221.65, "净利润（上年同期累计）"),
        ),
    ),
    ("002012", "2018Q2", "net_profit_yoy"): (
        "net_profit_yoy_v1",
        (
            ("net_profit_t", 15_888_115.75, "净利润（本期累计）"),
            ("net_profit_t_minus_4", 26_571_199.10, "净利润（上年同期累计）"),
        ),
    ),
    ("300433", "2025Q3", "roe"): (
        "roe_average_parent_equity_v1",
        (
            ("parent_net_profit_t", 2_842_952_844.41, "归属于母公司所有者的净利润"),
            (
                "opening_parent_equity",
                48_656_642_054.21,
                "归属于母公司所有者权益（期初）",
            ),
            (
                "closing_parent_equity",
                53_845_361_611.79,
                "归属于母公司所有者权益（期末）",
            ),
        ),
    ),
}


def _financial_formula_sample(
    *,
    trial_sample: dict[str, Any],
    current_sample: dict[str, Any],
    artifact_id: str,
) -> dict[str, Any]:
    key = (
        str(current_sample["symbol"]),
        str(current_sample["report_period"]),
        str(current_sample["metric"]),
    )
    formula_id, raw_operands = _FINANCIAL_FIXTURES[key]
    operands = [
        {
            **_operand(
                name=name,
                value=value,
                artifact_id=artifact_id,
                line_item=line_item,
            ),
            "lower": value - 0.005,
            "upper": value + 0.005,
            "disclosure_precision": {
                "basis": "disclosed_unit",
                "quantum": 0.01,
            },
        }
        for name, value, line_item in raw_operands
    ]
    if formula_id == "net_profit_yoy_v1":
        current = raw_operands[0][1]
        prior = raw_operands[1][1]
        result_value = (current - prior) / abs(prior)
        numerator = (
            (current - 0.005) - (prior + 0.005),
            (current + 0.005) - (prior - 0.005),
        )
        reciprocals = (1.0 / (prior - 0.005), 1.0 / (prior + 0.005))
        interval_products = tuple(
            numerator_bound * reciprocal
            for numerator_bound in numerator
            for reciprocal in reciprocals
        )
        result_lower = min(interval_products)
        result_upper = max(interval_products)
        expression = "(net_profit_t-net_profit_t_minus_4)/abs(net_profit_t_minus_4)"
    else:
        profit = raw_operands[0][1]
        opening = raw_operands[1][1]
        closing = raw_operands[2][1]
        result_value = profit / ((opening + closing) / 2.0)
        result_lower = (profit - 0.005) / (((opening + 0.005) + (closing + 0.005)) / 2.0)
        result_upper = (profit + 0.005) / (((opening - 0.005) + (closing - 0.005)) / 2.0)
        expression = "parent_net_profit_t/((opening_parent_equity+closing_parent_equity)/2)"
    return {
        "table": "financial_indicators",
        "key": trial_sample["key"],
        "verdict": "formula_match",
        "trial_sample_sha256": canonical_sha256(trial_sample),
        "evidence_ids": [artifact_id],
        "checked_values": trial_sample["checked_values"],
        "formula_proof": {
            "formula_id": formula_id,
            "expression": expression,
            "operands": operands,
            "result": {
                "value": result_value,
                "lower": result_lower,
                "upper": result_upper,
            },
            "local_storage_quantum": LOCAL_STORAGE_QUANTUM,
            "event_window": None,
        },
    }


def _valid_v3_bundle(tmp_path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    assert _sha256(_FINAL_TRIAL) == FROZEN_FINAL_TRIAL_SHA256
    preflight = json.loads(_FINAL_PREFLIGHT.read_text(encoding="utf-8"))
    pit_samples = preflight["pit_samples"]
    assert pit_samples["manifest_sha256"] == FROZEN_MANIFEST_SHA256
    trial = json.loads(_FINAL_TRIAL.read_text(encoding="utf-8"))
    shutil.copyfile(_FINAL_TRIAL, tmp_path / "final-trial.json")

    current_by_identity: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for table, source_key in (
        ("daily_bars", "daily_bars_with_adj"),
        ("financial_indicators", "financial_indicators"),
        ("valuation_daily", "valuation_daily"),
    ):
        for current in pit_samples[source_key]:
            with_table = {"table": table, "key": current}
            current_by_identity[_identity(with_table)] = current

    artifacts: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    for index, trial_sample in enumerate(trial["samples"]):
        artifact_id = f"sample-{index:02d}"
        table = str(trial_sample["table"])
        current = current_by_identity[_identity(trial_sample)]
        if table == "valuation_daily":
            checked_values = trial_sample["checked_values"]
            artifacts.append(
                _artifact(
                    tmp_path,
                    artifact_id=artifact_id,
                    actual_fields=[
                        "pe_ttm",
                        "pb_mrq",
                        "ps_ttm",
                        "source",
                        "available_time",
                    ],
                    source_kind="audited_external_response",
                    request_parameters={
                        "symbol": current["symbol"],
                        "trade_date": current["trade_date"],
                    },
                    content={
                        "result": {
                            "data": [
                                {
                                    "SECURITY_CODE": current["symbol"],
                                    "TRADE_DATE": (f"{current['trade_date']} 00:00:00"),
                                    "PE_TTM": checked_values["pe_ttm"]["external_value"],
                                    "PB_MRQ": checked_values["pb_mrq"]["external_value"],
                                    "PS_TTM": checked_values["ps_ttm"]["external_value"],
                                }
                            ]
                        }
                    },
                )
            )
            samples.append(
                {
                    "table": table,
                    "key": trial_sample["key"],
                    "verdict": "numeric_match",
                    "trial_sample_sha256": canonical_sha256(trial_sample),
                    "evidence_ids": [artifact_id],
                    "checked_values": trial_sample["checked_values"],
                }
            )
            continue
        if table == "financial_indicators" and current["metric"] == "revenue_yoy":
            artifacts.append(
                _artifact(
                    tmp_path,
                    artifact_id=artifact_id,
                    actual_fields=[
                        "营业收入",
                        "营业总收入",
                        "主营业务收入_exact_mapping_absent",
                    ],
                    source_kind="issuer_xbrl",
                    missing_state="field_absent",
                )
            )
            samples.append(
                {
                    "table": table,
                    "key": trial_sample["key"],
                    "verdict": "expected_unavailable",
                    "trial_sample_sha256": canonical_sha256(trial_sample),
                    "evidence_ids": [artifact_id],
                    "unavailable_proof": {
                        "cadence_contract": ("semiannual_q2_q4_from_baostock_mb_revenue"),
                        "expected_quarters": [2, 4],
                        "observed_quarter": 1,
                        "local_value": None,
                        "payload_reason": "missing_current_revenue",
                        "mapping_status": ("no_unique_exact_mbrevenue_line_item"),
                        "approximate_substitute_used": False,
                        "request_status": "success",
                        "missing_state": "field_absent",
                        "examined_line_items": [
                            {
                                "name": "营业收入",
                                "mapping_decision": "not_same_metric",
                            },
                            {
                                "name": "营业总收入",
                                "mapping_decision": "not_same_metric",
                            },
                        ],
                        "exact_line_item_candidates": [
                            {"name": "主营业务收入", "match_count": 0},
                            {"name": "主营营业收入", "match_count": 0},
                        ],
                    },
                }
            )
            continue
        if table == "daily_bars":
            symbol = str(current["symbol"])
            daily_key = (symbol, str(current["trade_date"]))
            fixed_events = _DAILY_EVENT_FIXTURES[daily_key]
            inventory_artifact_id = f"{artifact_id}-inventory"
            inventory_rows: list[dict[str, Any]]
            inventory_content: dict[str, Any]
            original_inventory_request: dict[str, Any]
            if symbol.startswith("6"):
                inventory_rows = [
                    {
                        "SECURITY_CODE": symbol,
                        "TITLE": f"{symbol}：权益分派实施公告",
                        "URL": f"/official/{symbol}-{event[0]}.pdf",
                        "SSEDATE": event[0],
                    }
                    for event in fixed_events
                ]
                inventory_content = {
                    "pageHelp": {
                        "total": len(inventory_rows),
                        "data": inventory_rows,
                    }
                }
                original_inventory_request = {
                    "method": "GET",
                    "url": ("https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"),
                    "params": {
                        "productId": symbol,
                        "beginDate": current["trade_date"],
                        "endDate": current["adj_anchor_date"],
                        "reportType": "ALL",
                        "reportType2": "",
                        "keyWord": "",
                        "pageHelp.pageNo": 1,
                        "pageHelp.beginPage": 1,
                        "pageHelp.pageSize": 100,
                    },
                }
            else:
                inventory_rows = [
                    {
                        "annId": f"{symbol}-{event[0]}",
                        "title": f"{symbol}：权益分派实施公告",
                        "attachPath": f"/official/{symbol}-{event[0]}.pdf",
                        "secCode": [symbol],
                        "publishTime": f"{event[0]} 08:00:00",
                    }
                    for event in fixed_events
                ]
                inventory_content = {
                    "announceCount": len(inventory_rows),
                    "data": inventory_rows,
                }
                original_inventory_request = {
                    "method": "POST",
                    "url": "https://www.szse.cn/api/disc/announcement/annList",
                    "params": {
                        "stock": [symbol],
                        "channelCode": ["listedNotice_disc"],
                        "seDate": [
                            current["trade_date"],
                            current["adj_anchor_date"],
                        ],
                        "searchKey": "",
                        "pageNum": 1,
                        "pageSize": 50,
                    },
                }
            artifacts.append(
                _artifact(
                    tmp_path,
                    artifact_id=inventory_artifact_id,
                    actual_fields=["company_action_inventory_records"],
                    source_kind="official_exchange_disclosure",
                    request_parameters={
                        "symbol": symbol,
                        "start_date": current["trade_date"],
                        "end_date": current["adj_anchor_date"],
                        "page_number": 1,
                        "original_request": original_inventory_request,
                        "source_url": original_inventory_request["url"],
                    },
                    content=inventory_content,
                )
            )
            classification_records = []
            for raw_inventory_row in inventory_rows:
                if symbol.startswith("6"):
                    record_id = str(raw_inventory_row["URL"])
                    title = str(raw_inventory_row["TITLE"])
                    document_path = str(raw_inventory_row["URL"])
                    publish_date = str(raw_inventory_row["SSEDATE"])
                else:
                    record_id = str(raw_inventory_row["annId"])
                    title = str(raw_inventory_row["title"])
                    document_path = str(raw_inventory_row["attachPath"])
                    publish_date = str(raw_inventory_row["publishTime"])[:10]
                category, classification = classify_adjustment_announcement_title(title)
                classification_records.append(
                    {
                        "page_number": 1,
                        "record_id": record_id,
                        "publish_date": publish_date,
                        "title": title,
                        "document_path": document_path,
                        "category": category,
                        "classification": classification,
                    }
                )
            preclose_artifact_id = f"{artifact_id}-preclose"
            artifacts.append(
                _artifact(
                    tmp_path,
                    artifact_id=preclose_artifact_id,
                    actual_fields=["pre_close"],
                    source_kind="frozen_local_manifest",
                    request_parameters={
                        "pit_manifest_sha256": FROZEN_MANIFEST_SHA256,
                        "access_mode": "read_only",
                    },
                )
            )
            price_tick_artifact_id = f"{artifact_id}-price-tick"
            symbol_market = "SSE" if symbol.startswith("6") else "SZSE"
            artifacts.append(
                _artifact(
                    tmp_path,
                    artifact_id=price_tick_artifact_id,
                    actual_fields=["price_tick"],
                    source_kind="official_exchange_rule",
                    request_parameters={
                        "market": symbol_market,
                        "effective_from": "2023-04-10",
                        "effective_to": None,
                        "price_tick": 0.01,
                    },
                )
            )
            operands: list[dict[str, Any]] = []
            action_artifact_ids: list[str] = []
            product = 1.0
            official_reference_artifact_id: str | None = None
            official_reference_value: Decimal | None = None
            if symbol in {"001260", "600782"}:
                official_reference_artifact_id = f"{artifact_id}-official-reference"
                if symbol == "001260":
                    official_reference_value = Decimal("20.29")
                    artifacts.append(
                        _artifact(
                            tmp_path,
                            artifact_id=official_reference_artifact_id,
                            actual_fields=[
                                "ex_reference_price",
                                "cash_dividend_per_share",
                            ],
                            source_kind="audited_external_response",
                            request_parameters={
                                "symbol": "001260",
                                "ex_date": "2026-05-27",
                                "ex_reference_price": 20.29,
                                "source_url": (
                                    "https://docs.static.szse.cn/www/"
                                    "market/periodical/month/"
                                    "W020260605534753848014.html"
                                ),
                            },
                            raw_bytes=(
                                "<table><tr><th>Code</th>"
                                "<th>Securities</th><th>Bonus</th>"
                                "<th>BPS</th><th>Cash Div.</th>"
                                "<th>DPS</th><th>Rts</th><th>RPS</th>"
                                "<th>Pla.</th><th>Funds</th>"
                                "<th>Ex-Date</th><th>Reg. Date</th>"
                                "<th>Ex-Price</th><th>Pre-Closing</th>"
                                "</tr><tr><td>001260</td>"
                                "<td>坤泰股份</td><td>0</td>"
                                "<td>0.000</td><td>24,725,000</td>"
                                "<td>0.215</td><td></td><td></td>"
                                "<td></td><td></td>"
                                "<td>2026/05/27</td>"
                                "<td>2026/05/26</td>"
                                "<td>20.290</td><td>20.500</td>"
                                "</tr></table>"
                            ).encode("gb18030"),
                        )
                    )
                else:
                    official_reference_value = Decimal("2.64")
                    artifacts.append(
                        _artifact(
                            tmp_path,
                            artifact_id=official_reference_artifact_id,
                            actual_fields=[
                                "ex_reference_price",
                                "cash_dividend_per_share",
                            ],
                            source_kind="audited_external_response",
                            request_parameters={
                                "symbol": "600782",
                                "ex_date": "2026-06-05",
                                "ex_reference_price": 2.64,
                                "source_url": ("https://query.sse.com.cn/commonSoaQuery.do"),
                            },
                            content={
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
                    )
            for event_index, (
                ex_date,
                cash_value,
                share_value,
                pre_close_value,
            ) in enumerate(fixed_events, start=1):
                action_artifact_id = f"{artifact_id}-action-{event_index}"
                action_artifact_ids.append(action_artifact_id)
                action_source_url = f"https://www.szse.cn/official/{symbol}-{ex_date}.pdf"
                artifacts.append(
                    _artifact(
                        tmp_path,
                        artifact_id=action_artifact_id,
                        actual_fields=[
                            "event_type",
                            "ex_date",
                            "cash_dividend_per_share",
                            "share_distribution_per_share",
                        ],
                        source_kind="official_exchange_disclosure",
                        request_parameters={
                            "symbol": symbol,
                            "ex_date": ex_date,
                            "source_url": action_source_url,
                        },
                    )
                )
                pre_close = Decimal(str(pre_close_value))
                cash = Decimal(str(cash_value))
                shares = Decimal(str(share_value))
                tick = Decimal("0.01")
                unrounded = (pre_close - cash) / (Decimal(1) + shares)
                if official_reference_value is not None:
                    reference = official_reference_value
                    event_formula_id = "official_reference_price_ratio_v1"
                    rounding_provenance = "exchange_published_reference_price_local_rounding_none"
                    reference_evidence_id = official_reference_artifact_id
                else:
                    reference = (unrounded / tick).quantize(
                        Decimal("1"),
                        rounding=ROUND_HALF_UP,
                    ) * tick
                    event_formula_id = "cash_share_price_grid_v1"
                    rounding_provenance = "exchange_rule_round_half_up"
                    reference_evidence_id = None
                multiplier = float(pre_close / reference)
                product *= multiplier
                event = {
                    "event_type": "cash_dividend",
                    "ex_date": ex_date,
                    "event_formula_id": event_formula_id,
                    "pre_close": float(pre_close),
                    "cash_dividend_per_share": float(cash),
                    "share_distribution_per_share": float(shares),
                    "price_tick": float(tick),
                    "ex_reference_price": float(reference),
                    "event_multiplier": multiplier,
                    "announcement_evidence_id": action_artifact_id,
                    "pre_close_evidence_id": preclose_artifact_id,
                    "price_tick_evidence_id": price_tick_artifact_id,
                    "reference_price_evidence_id": reference_evidence_id,
                    "rounding_provenance": rounding_provenance,
                }
                operands.append(
                    _operand(
                        name=f"event_multiplier_{event_index}",
                        value=multiplier,
                        artifact_id=action_artifact_id,
                        line_item="derived_event_multiplier",
                        event=event,
                    )
                )
            samples.append(
                {
                    "table": "daily_bars",
                    "key": trial_sample["key"],
                    "verdict": "formula_match",
                    "trial_sample_sha256": canonical_sha256(trial_sample),
                    "evidence_ids": sorted(
                        [
                            *action_artifact_ids,
                            inventory_artifact_id,
                            preclose_artifact_id,
                            price_tick_artifact_id,
                            *(
                                [official_reference_artifact_id]
                                if official_reference_artifact_id
                                else []
                            ),
                        ]
                    ),
                    "checked_values": trial_sample["checked_values"],
                    "formula_proof": {
                        "formula_id": "hfq_event_multiplier_product_v1",
                        "expression": "product(event_multipliers)",
                        "operands": operands,
                        "result": {
                            "value": product,
                            "lower": product,
                            "upper": product,
                        },
                        "local_storage_quantum": LOCAL_STORAGE_QUANTUM,
                        "event_window": {
                            "start_date": current["trade_date"],
                            "end_date": current["adj_anchor_date"],
                            "complete": True,
                            "inventory_evidence_ids": [inventory_artifact_id],
                            "symbol": symbol,
                            "inventory_source": ("official_exchange_full_pagination"),
                            "page_count": 1,
                            "pages": [
                                {
                                    "page_number": 1,
                                    "evidence_id": inventory_artifact_id,
                                    "reported_records": len(fixed_events),
                                    "raw_response_records": len(fixed_events),
                                    "not_factor_adjustment_records": 0,
                                    "not_adjustment_related_records": 0,
                                    "unknown_adjustment_candidate_records": 0,
                                }
                            ],
                            "total_reported_records": len(fixed_events),
                            "raw_total_records": len(fixed_events),
                            "taxonomy_version": (ADJUSTMENT_EVENT_TAXONOMY_VERSION),
                            "classification_summary": {
                                "implemented_adjustment_event": len(fixed_events),
                                "not_factor_adjustment": 0,
                                "not_adjustment_related": 0,
                                "unknown_adjustment_candidate": 0,
                            },
                            "classification_sha256": canonical_sha256(classification_records),
                        },
                    },
                }
            )
            continue

        formula_id, fixture_operands = _FINANCIAL_FIXTURES[
            (
                str(current["symbol"]),
                str(current["report_period"]),
                str(current["metric"]),
            )
        ]
        del formula_id
        artifacts.append(
            _artifact(
                tmp_path,
                artifact_id=artifact_id,
                actual_fields=[line_item for _name, _value, line_item in fixture_operands],
                source_kind="official_exchange_disclosure",
            )
        )
        samples.append(
            _financial_formula_sample(
                trial_sample=trial_sample,
                current_sample=current,
                artifact_id=artifact_id,
            )
        )

    document = {
        "schema_version": PAIRING_V3_SCHEMA_VERSION,
        "adjudication_contract": ADJUDICATION_CONTRACT_VERSION,
        "adjudication_contract_sha256": ADJUDICATION_CONTRACT_SHA256,
        "pit_manifest_schema_version": pit_samples["manifest_schema_version"],
        "pit_manifest_sha256": FROZEN_MANIFEST_SHA256,
        "final_trial": {
            "relative_path": "final-trial.json",
            "sha256": FROZEN_FINAL_TRIAL_SHA256,
        },
        "approved": True,
        "reviewed_at": "2026-07-31T13:00:00+08:00",
        "reviewer_role": "independent_data_architect",
        "seed": FROZEN_SEED,
        "sample_size_per_table": FROZEN_SAMPLE_SIZE_PER_TABLE,
        "artifacts": artifacts,
        "samples": samples,
        "summary": {
            "sample_count": 15,
            "numeric_match": 5,
            "formula_match": 8,
            "expected_unavailable": 2,
            "unresolved": 0,
            "generic_unavailable": 0,
            "ambiguous_mapping": 0,
            "schema_hash_integrity_errors": 0,
        },
    }
    evidence_path = tmp_path / "pairing-v3.json"
    evidence_path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return evidence_path, document, pit_samples


def _daily_sample_and_inventory(
    document: dict[str, Any],
    symbol: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sample = next(
        item
        for item in document["samples"]
        if item["table"] == "daily_bars" and item["key"]["symbol"] == symbol
    )
    inventory_id = sample["formula_proof"]["event_window"]["inventory_evidence_ids"][0]
    artifact = next(item for item in document["artifacts"] if item["id"] == inventory_id)
    return sample, artifact


def _refresh_pairing_artifact_hash(
    tmp_path: Path,
    artifact: dict[str, Any],
) -> None:
    artifact_path = tmp_path / str(artifact["relative_path"])
    digest = _sha256(artifact_path)
    artifact["sha256"] = digest
    artifact["first_success_response_sha256"] = digest


def test_pairing_v3_accepts_only_the_frozen_content_addressed_15_sample_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    monkeypatch.setattr(
        data_health,
        "FROZEN_PAIRING_V3_UNSIGNED_CANONICAL_SHA256",
        normalized_unsigned_candidate_sha256(document),
    )
    monkeypatch.setattr(
        data_health,
        "FROZEN_PAIRING_V3_SIGNED_EVIDENCE_TRUST_ANCHOR",
        {
            "sha256": _sha256(evidence_path),
            "reviewer_role": "independent_data_architect",
            "signature_status": INDEPENDENT_HUMAN_SIGNATURE_STATUS,
            "adjudication_contract": ADJUDICATION_CONTRACT_VERSION,
        },
    )

    accepted = data_health._external_pairing_evidence(
        evidence_path,
        pit_samples=pit_samples,
    )

    assert accepted["accepted"] is True
    assert accepted["schema_version"] == PAIRING_V3_SCHEMA_VERSION
    assert accepted["adjudication_contract"] == ADJUDICATION_CONTRACT_VERSION
    assert accepted["final_trial_sha256"] == FROZEN_FINAL_TRIAL_SHA256
    assert accepted["sample_count"] == 15
    assert accepted["numeric_match"] == 5
    assert accepted["formula_match"] == 8
    assert accepted["expected_unavailable"] == 2
    daily_600782, _inventory = _daily_sample_and_inventory(
        document,
        "600782",
    )
    event = daily_600782["formula_proof"]["operands"][0]["event"]
    assert event["ex_reference_price"] == 2.64
    assert event["event_formula_id"] == "official_reference_price_ratio_v1"
    assert event["rounding_provenance"] == (
        "exchange_published_reference_price_local_rounding_none"
    )
    assert event["reference_price_evidence_id"]
    daily_001260, _inventory = _daily_sample_and_inventory(
        document,
        "001260",
    )
    event_001260 = daily_001260["formula_proof"]["operands"][0]["event"]
    assert event_001260["ex_reference_price"] == 20.29
    assert event_001260["event_formula_id"] == ("official_reference_price_ratio_v1")
    assert event_001260["rounding_provenance"] == (
        "exchange_published_reference_price_local_rounding_none"
    )
    assert event_001260["reference_price_evidence_id"]


def test_pairing_v3_ai_release_requires_attestation_and_atomic_profile_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    document["reviewer_role"] = AI_REVIEWER_ROLE
    document["reviewed_at"] = "2026-08-01T03:29:35.045445-04:00"
    evidence_path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        data_health,
        "FROZEN_PAIRING_V3_UNSIGNED_CANONICAL_SHA256",
        normalized_unsigned_candidate_sha256(document),
    )
    attestation_sha256 = "a" * 64
    monkeypatch.setattr(
        data_health,
        "FROZEN_PAIRING_V3_SIGNED_EVIDENCE_TRUST_ANCHOR",
        {
            "sha256": _sha256(evidence_path),
            "reviewer_role": AI_REVIEWER_ROLE,
            "signature_status": INDEPENDENT_AI_SIGNATURE_STATUS,
            "adjudication_contract": ADJUDICATION_CONTRACT_VERSION,
            "ai_review_attestation_sha256": attestation_sha256,
            "governance_amendment": (
                data_health.AI_REVIEW_GOVERNANCE_AMENDMENT_VERSION
            ),
            "governance_amendment_sha256": (
                data_health.AI_REVIEW_GOVERNANCE_AMENDMENT_SHA256
            ),
        },
    )
    with pytest.raises(ValueError, match="requires the frozen AI review attestation"):
        data_health._external_pairing_evidence(
            evidence_path,
            pit_samples=pit_samples,
        )

    attestation_path = tmp_path / "ai-review.json"
    attestation_path.write_text("{}", encoding="utf-8")

    def fake_attestation_validator(
        path: Path,
        *,
        signed_candidate: dict[str, Any],
    ) -> dict[str, Any]:
        assert path == attestation_path
        assert signed_candidate["reviewer_role"] == AI_REVIEWER_ROLE
        return {
            "schema_version": "p3.3-s6-independent-ai-review-v1",
            "sha256": attestation_sha256,
            "bytes": 2,
            "reviewer_type": "ai",
            "reviewer_role": AI_REVIEWER_ROLE,
            "reviewer_product": "Claude Code",
            "reviewer_model": "claude-fable-5",
            "reviewed_at": document["reviewed_at"],
            "decision": "approved",
            "signature_status": INDEPENDENT_AI_SIGNATURE_STATUS,
            "governance_amendment": (
                data_health.AI_REVIEW_GOVERNANCE_AMENDMENT_VERSION
            ),
            "governance_amendment_sha256": (
                data_health.AI_REVIEW_GOVERNANCE_AMENDMENT_SHA256
            ),
        }

    monkeypatch.setattr(
        data_health,
        "validate_ai_review_attestation",
        fake_attestation_validator,
    )
    accepted = data_health._external_pairing_evidence(
        evidence_path,
        ai_review_attestation_path=attestation_path,
        pit_samples=pit_samples,
    )

    assert accepted["accepted"] is True
    assert accepted["reviewer_type"] == "ai"
    assert accepted["reviewer_role"] == AI_REVIEWER_ROLE
    assert accepted["signature_status"] == INDEPENDENT_AI_SIGNATURE_STATUS
    assert accepted["ai_review_attestation"]["sha256"] == attestation_sha256


def test_pairing_v3_matching_evidence_stays_blocked_while_trust_anchor_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    monkeypatch.setattr(
        data_health,
        "FROZEN_PAIRING_V3_UNSIGNED_CANONICAL_SHA256",
        normalized_unsigned_candidate_sha256(document),
    )
    monkeypatch.setattr(
        data_health,
        "FROZEN_PAIRING_V3_SIGNED_EVIDENCE_TRUST_ANCHOR",
        None,
    )
    assert data_health.FROZEN_PAIRING_V3_SIGNED_EVIDENCE_TRUST_ANCHOR is None

    with pytest.raises(ValueError, match=r"trust anchor.*is pending"):
        data_health._external_pairing_evidence(
            evidence_path,
            pit_samples=pit_samples,
        )


def test_pairing_v3_rejects_integer_one_disguised_as_checked_values_pass_true(
    tmp_path: Path,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    sample = next(
        item for item in document["samples"] if item["verdict"] == "numeric_match"
    )
    field = next(iter(sample["checked_values"]))
    assert sample["checked_values"][field]["pass"] is True
    sample["checked_values"][field]["pass"] = 1
    evidence_path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="numeric_match must preserve the frozen trial checked_values",
    ):
        validate_pairing_v3(
            document,
            evidence_path=evidence_path,
            pit_samples=pit_samples,
        )


def test_pairing_v3_rejects_official_reference_raw_value_after_hash_rebinding(
    tmp_path: Path,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    daily_600782, _inventory = _daily_sample_and_inventory(
        document,
        "600782",
    )
    event = daily_600782["formula_proof"]["operands"][0]["event"]
    reference_id = str(event["reference_price_evidence_id"])
    reference_artifact = next(
        artifact for artifact in document["artifacts"] if artifact["id"] == reference_id
    )
    reference_path = tmp_path / str(reference_artifact["relative_path"])
    response = json.loads(reference_path.read_text(encoding="utf-8"))
    response["result"][0]["PRE_CLOSE_PRICE"] = 2.65
    reference_path.write_text(
        json.dumps(response, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    _refresh_pairing_artifact_hash(tmp_path, reference_artifact)
    evidence_path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="official ex-reference raw response values differ",
    ):
        validate_pairing_v3(
            document,
            evidence_path=evidence_path,
            pit_samples=pit_samples,
        )


def test_pairing_v3_rejects_szse_reference_html_after_hash_rebinding(
    tmp_path: Path,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    daily_001260, _inventory = _daily_sample_and_inventory(
        document,
        "001260",
    )
    event = daily_001260["formula_proof"]["operands"][0]["event"]
    reference_id = str(event["reference_price_evidence_id"])
    reference_artifact = next(
        artifact for artifact in document["artifacts"] if artifact["id"] == reference_id
    )
    reference_path = tmp_path / str(reference_artifact["relative_path"])
    raw = reference_path.read_bytes().decode("gb18030")
    reference_path.write_bytes(raw.replace("20.290", "20.300", 1).encode("gb18030"))
    _refresh_pairing_artifact_hash(tmp_path, reference_artifact)
    evidence_path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="official SZSE ex-reference raw response values differ",
    ):
        validate_pairing_v3(
            document,
            evidence_path=evidence_path,
            pit_samples=pit_samples,
        )


@pytest.mark.parametrize(
    ("symbol", "mutate", "message"),
    [
        (
            "001260",
            lambda params: params.__setitem__(
                "searchKey",
                "权益分派实施公告",
            ),
            "exact-window, unfiltered",
        ),
        (
            "600782",
            lambda params: params.__setitem__(
                "keyWord",
                "权益分派实施公告",
            ),
            "exact-window, unfiltered",
        ),
        (
            "600782",
            lambda params: params.__setitem__("reportType2", "DQBG"),
            "exact-window, unfiltered",
        ),
        (
            "001260",
            lambda params: params["seDate"].__setitem__(0, "2025-01-01"),
            "exact-window, unfiltered",
        ),
    ],
)
def test_pairing_v3_rejects_filtered_widened_or_report_type_inventory(
    tmp_path: Path,
    symbol: str,
    mutate: Any,
    message: str,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    _sample, inventory = _daily_sample_and_inventory(document, symbol)
    params = inventory["request_parameters"]["original_request"]["params"]
    mutate(params)
    evidence_path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        validate_pairing_v3(
            document,
            evidence_path=evidence_path,
            pit_samples=pit_samples,
        )


def test_pairing_v3_rejects_unknown_high_risk_inventory_title(
    tmp_path: Path,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    _sample, inventory = _daily_sample_and_inventory(document, "600782")
    inventory_path = tmp_path / str(inventory["relative_path"])
    body = json.loads(inventory_path.read_text(encoding="utf-8"))
    body["pageHelp"]["data"][0]["TITLE"] = "股票价格调整实施公告"
    inventory_path.write_text(
        json.dumps(body, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    _refresh_pairing_artifact_hash(tmp_path, inventory)
    evidence_path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="classified action count differs"):
        validate_pairing_v3(
            document,
            evidence_path=evidence_path,
            pit_samples=pit_samples,
        )


def test_pairing_v3_rejects_inventory_missing_page_metadata(
    tmp_path: Path,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    sample, _inventory = _daily_sample_and_inventory(document, "001260")
    window = sample["formula_proof"]["event_window"]
    window["raw_total_records"] = 2
    evidence_path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="raw page counts do not cover"):
        validate_pairing_v3(
            document,
            evidence_path=evidence_path,
            pit_samples=pit_samples,
        )


def test_pairing_v3_rejects_duplicate_inventory_ids_after_hash_rebinding(
    tmp_path: Path,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    sample, inventory = _daily_sample_and_inventory(document, "001260")
    inventory_path = tmp_path / str(inventory["relative_path"])
    body = json.loads(inventory_path.read_text(encoding="utf-8"))
    benign = {
        "annId": "001260-duplicate-benign",
        "title": "其他公告",
        "attachPath": "/official/001260-other.pdf",
        "secCode": ["001260"],
        "publishTime": "2026-01-02 08:00:00",
    }
    body["data"].extend([benign, copy.deepcopy(benign)])
    body["announceCount"] = 3
    inventory_path.write_text(
        json.dumps(body, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    _refresh_pairing_artifact_hash(tmp_path, inventory)
    window = sample["formula_proof"]["event_window"]
    page = window["pages"][0]
    page["raw_response_records"] = 3
    page["not_adjustment_related_records"] = 2
    window["raw_total_records"] = 3
    window["classification_summary"]["not_adjustment_related"] = 2
    classification_records = []
    for row in body["data"]:
        title = str(row["title"])
        category, classification = classify_adjustment_announcement_title(title)
        classification_records.append(
            {
                "page_number": 1,
                "record_id": str(row["annId"]),
                "publish_date": str(row["publishTime"])[:10],
                "title": title,
                "document_path": str(row["attachPath"]),
                "category": category,
                "classification": classification,
            }
        )
    window["classification_sha256"] = canonical_sha256(classification_records)
    evidence_path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate announcement id"):
        validate_pairing_v3(
            document,
            evidence_path=evidence_path,
            pit_samples=pit_samples,
        )


def test_pairing_v3_unsigned_candidate_proves_15_of_15_but_cannot_unlock_s6(
    tmp_path: Path,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    candidate = build_unsigned_candidate_v3(document)
    evidence_path.write_text(
        json.dumps(candidate, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    verified = validate_pairing_v3_candidate(
        candidate,
        evidence_path=evidence_path,
        pit_samples=pit_samples,
    )

    assert verified["sample_count"] == 15
    assert verified["signature_status"] == "unsigned_candidate"
    assert verified["numeric_match"] == 5
    assert verified["formula_match"] == 8
    assert verified["expected_unavailable"] == 2
    with pytest.raises(ValueError, match="frozen unsigned candidate"):
        data_health._external_pairing_evidence(
            evidence_path,
            pit_samples=pit_samples,
        )

    signed = copy.deepcopy(candidate)
    signed["approved"] = True
    signed["reviewer_role"] = "independent_data_architect"
    signed["reviewed_at"] = datetime(2026, 7, 31, 6, 0, tzinfo=UTC).isoformat()
    evidence_path.write_text(json.dumps(signed), encoding="utf-8")
    assert (
        validate_pairing_v3(
            signed,
            evidence_path=evidence_path,
            pit_samples=pit_samples,
        )["signature_status"]
        == INDEPENDENT_HUMAN_SIGNATURE_STATUS
    )
    assert normalized_unsigned_candidate_sha256(signed) == canonical_sha256(candidate)


def test_pairing_v3_uses_exact_ai_reviewer_profile_without_human_semantics(
    tmp_path: Path,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    candidate = build_unsigned_candidate_v3(document)
    signed = copy.deepcopy(candidate)
    signed["approved"] = True
    signed["reviewer_role"] = AI_REVIEWER_ROLE
    signed["reviewed_at"] = "2026-08-01T03:29:35.045445-04:00"
    evidence_path.write_text(
        json.dumps(signed, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    verified = validate_pairing_v3(
        signed,
        evidence_path=evidence_path,
        pit_samples=pit_samples,
    )

    assert verified["reviewer_type"] == "ai"
    assert verified["reviewer_role"] == AI_REVIEWER_ROLE
    assert verified["signature_status"] == INDEPENDENT_AI_SIGNATURE_STATUS
    assert normalized_unsigned_candidate_sha256(signed) == canonical_sha256(candidate)


@pytest.mark.parametrize(
    "reviewer_role",
    [
        " independent_ai_architect_claude_code",
        "independent_ai_architect_claude_code ",
        "Independent_AI_Architect_Claude_Code",
        "independent_ai_architect_claude_code_v2",
        "prefix_independent_ai_architect_claude_code",
        "independent_fake_architect",
        "automated_independent_architect",
        "independent_ai_architect_claude_cοde",
    ],
)
def test_pairing_v3_rejects_non_allowlisted_reviewer_profiles(
    tmp_path: Path,
    reviewer_role: str,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    document["reviewer_role"] = reviewer_role
    evidence_path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="reviewer_role"):
        validate_pairing_v3(
            document,
            evidence_path=evidence_path,
            pit_samples=pit_samples,
        )


def test_ai_review_attestation_is_whole_file_anchored_and_matches_signed_projection(
    tmp_path: Path,
) -> None:
    candidate = json.loads(_V4_UNSIGNED_CANDIDATE.read_text(encoding="utf-8"))
    signed = copy.deepcopy(candidate)
    signed["approved"] = True
    signed["reviewer_role"] = AI_REVIEWER_ROLE
    signed["reviewed_at"] = "2026-08-01T03:29:35.045445-04:00"

    verified = validate_ai_review_attestation(
        _AI_REVIEW_ATTESTATION,
        signed_candidate=signed,
    )

    assert verified["reviewer_type"] == "ai"
    assert verified["reviewer_role"] == AI_REVIEWER_ROLE
    assert verified["signature_status"] == INDEPENDENT_AI_SIGNATURE_STATUS
    assert verified["sha256"] == _sha256(_AI_REVIEW_ATTESTATION)

    tampered = tmp_path / "tampered-ai-review.json"
    review = json.loads(_AI_REVIEW_ATTESTATION.read_text(encoding="utf-8"))
    review["reviewer_model"] = "untrusted-model"
    tampered.write_text(
        json.dumps(review, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="whole-file SHA-256"):
        validate_ai_review_attestation(
            tampered,
            signed_candidate=signed,
        )


def test_ai_review_attestation_pins_frozen_preflight_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(_V4_UNSIGNED_CANDIDATE.read_text(encoding="utf-8"))
    signed = copy.deepcopy(candidate)
    signed["approved"] = True
    signed["reviewer_role"] = AI_REVIEWER_ROLE
    signed["reviewed_at"] = "2026-08-01T03:29:35.045445-04:00"
    review = json.loads(_AI_REVIEW_ATTESTATION.read_text(encoding="utf-8"))
    review["frozen_preflight_sha256"] = "0" * 64
    tampered = tmp_path / "wrong-preflight-ai-review.json"
    tampered.write_text(
        json.dumps(review, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        adjudication,
        "AI_REVIEW_ATTESTATION_SHA256",
        _sha256(tampered),
    )

    with pytest.raises(ValueError, match="frozen_preflight_sha256 mismatch"):
        validate_ai_review_attestation(
            tampered,
            signed_candidate=signed,
        )


def test_frozen_preflight_loader_is_content_addressed_and_rejects_symlink(
    tmp_path: Path,
) -> None:
    samples, identity = data_health._load_frozen_pit_samples(_FINAL_PREFLIGHT)

    assert identity["sha256"] == adjudication.FROZEN_PREFLIGHT_SHA256
    assert samples["manifest_sha256"] == FROZEN_MANIFEST_SHA256

    linked = tmp_path / "linked-preflight.json"
    linked.symlink_to(_FINAL_PREFLIGHT)
    with pytest.raises(ValueError, match="regular file"):
        data_health._load_frozen_pit_samples(linked)

    tampered = tmp_path / "tampered-preflight.json"
    tampered.write_bytes(_FINAL_PREFLIGHT.read_bytes() + b" ")
    with pytest.raises(ValueError, match="whole-file SHA-256"):
        data_health._load_frozen_pit_samples(tampered)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda document: document.__setitem__(
                "pit_manifest_sha256",
                "0" * 64,
            ),
            "schema violation",
        ),
        (
            lambda document: document["samples"][0].__setitem__(
                "trial_sample_sha256",
                "0" * 64,
            ),
            "trial sample SHA-256 mismatch",
        ),
        (
            lambda document: document["samples"][0]["formula_proof"]["result"].__setitem__(
                "value", 999.0
            ),
            "not computed from operands",
        ),
        (
            lambda document: document["samples"][0]["formula_proof"]["event_window"]["pages"][
                0
            ].__setitem__("page_number", 2),
            "complete and consecutive",
        ),
        (
            lambda document: document["artifacts"][0]["request_parameters"].__setitem__(
                "symbol",
                "000000",
            ),
            "not the exact frozen symbol/window",
        ),
        (
            lambda document: next(
                artifact
                for artifact in document["artifacts"]
                if artifact["id"] == "sample-00-preclose"
            )["request_parameters"].__setitem__(
                "pit_manifest_sha256",
                "0" * 64,
            ),
            "frozen-local evidence must bind",
        ),
        (
            lambda document: document["samples"][0]["checked_values"]["close"].__setitem__(
                "pass",
                False,
            ),
            "preserve the frozen trial checked_values",
        ),
        (
            lambda document: document["samples"][5].__setitem__(
                "verdict",
                "manual_override",
            ),
            "schema violation",
        ),
        (
            lambda document: document["samples"][7]["unavailable_proof"][
                "exact_line_item_candidates"
            ][0].__setitem__("match_count", 1),
            "schema violation",
        ),
        (
            lambda document: document["samples"][7]["unavailable_proof"].__setitem__(
                "approximate_substitute_used",
                True,
            ),
            "schema violation",
        ),
    ],
)
def test_pairing_v3_rejects_hash_formula_and_unavailable_contract_tampering(
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    tampered = copy.deepcopy(document)
    mutation(tampered)
    evidence_path.write_text(
        json.dumps(tampered, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        validate_pairing_v3(
            tampered,
            evidence_path=evidence_path,
            pit_samples=pit_samples,
        )


def test_pairing_v3_rejects_formula_operands_outside_the_frozen_contract(
    tmp_path: Path,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    financial_formula = document["samples"][5]["formula_proof"]
    operands = {operand["name"]: operand for operand in financial_formula["operands"]}
    operands["net_profit_t"]["value"] = 200.0
    operands["net_profit_t"]["lower"] = 199.995
    operands["net_profit_t"]["upper"] = 200.005
    financial_formula["result"] = {"value": 1.0, "lower": 1.0, "upper": 1.0}
    evidence_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="frozen original-report contract"):
        validate_pairing_v3(
            document,
            evidence_path=evidence_path,
            pit_samples=pit_samples,
        )


def test_pairing_v3_does_not_scale_tolerance_for_large_financial_operands(
    tmp_path: Path,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    roe_sample = next(
        sample
        for sample in document["samples"]
        if sample["table"] == "financial_indicators" and sample["key"]["symbol"] == "300433"
    )
    closing = next(
        operand
        for operand in roe_sample["formula_proof"]["operands"]
        if operand["name"] == "closing_parent_equity"
    )
    closing["value"] += 0.05
    closing["lower"] += 0.05
    closing["upper"] += 0.05
    evidence_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="frozen original-report contract"):
        validate_pairing_v3(
            document,
            evidence_path=evidence_path,
            pit_samples=pit_samples,
        )


def test_pairing_v3_rejects_artifact_hash_and_first_success_rebinding(
    tmp_path: Path,
) -> None:
    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    first_artifact = document["artifacts"][0]
    artifact_path = tmp_path / first_artifact["relative_path"]
    artifact_path.write_text("tampered after content addressing", encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        validate_pairing_v3(
            document,
            evidence_path=evidence_path,
            pit_samples=pit_samples,
        )

    evidence_path, document, pit_samples = _valid_v3_bundle(tmp_path)
    document["artifacts"][0]["first_success_response_sha256"] = "0" * 64
    evidence_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="first successful response"):
        validate_pairing_v3(
            document,
            evidence_path=evidence_path,
            pit_samples=pit_samples,
        )


def test_frozen_final_manifest_rejects_legacy_pairing_v2() -> None:
    pit_samples = json.loads(_FINAL_PREFLIGHT.read_text(encoding="utf-8"))["pit_samples"]
    legacy_path = _ROOT / "docs/phase3/reports/P3.3-S6-external-pit-trial.json"

    with pytest.raises(ValueError, match="requires pairing-v3"):
        data_health._external_pairing_evidence(
            legacy_path,
            pit_samples=pit_samples,
        )

    final_trial = json.loads(_FINAL_TRIAL.read_text(encoding="utf-8"))
    with pytest.raises(ExternalPITError, match="offline pairing-v3"):
        build_signed_evidence_v2(
            final_trial,
            reviewer_role="data_architect",
            reviewed_at=datetime.now(UTC),
        )
