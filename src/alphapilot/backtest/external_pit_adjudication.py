from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator

ADJUDICATION_CONTRACT_VERSION = "p3.3-s6-external-pit-adjudication-v1"
ADJUDICATION_CONTRACT_SHA256 = "a16995e6545ddba7fc03d917ae6cc8d9ab19b7903f1d21c5694b1c0167b1b951"
PAIRING_V3_SCHEMA_VERSION = "p3.3-s6-external-pit-pairing-v3"
FROZEN_MANIFEST_SHA256 = "fb9c888e7a10f7b1ef28e7a447b0e2b53df739f6acdbedb1ba95d1d41cbfb0bd"
FROZEN_PREFLIGHT_SHA256 = (
    "2064586a9321c7ab8b321cd82ec5c3edcd5449984dc3a2822556a3f65eb620e3"
)
FROZEN_FINAL_TRIAL_SHA256 = "40f310a4c94e550cac33616fbe2d8ffc189cb48cd259a2913fe3379bae424099"
FROZEN_SEED = 20260725
FROZEN_SAMPLE_SIZE_PER_TABLE = 5
FROZEN_SAMPLE_COUNT = 15
FROZEN_PAIRING_V3_UNSIGNED_CANONICAL_SHA256 = (
    "45358d1508e7ac6e71a7df25990aba1a908fc7c5108dd4253cc1e05698d520ae"
)
FROZEN_PAIRING_V3_UNSIGNED_FILE_SHA256 = (
    "094c21eb7921a0c57c35ba97247a7b69faeada0421ff1929ca5d4701c388fd0c"
)
FROZEN_PAIRING_V3_MACHINE_VALIDATION_SHA256 = (
    "57f70704905ba84b371fc7f3432ce28967e9e67a5ae9b6517fc188da6109ef3d"
)
FROZEN_PAIRING_V3_PRESIGN_REPORT_SHA256 = (
    "1fee14d59bff82f951a620f67324540c6ad954baeef43c90cbd3c6385de12d89"
)
FROZEN_PAIRING_V3_INPUT_ZIP_SHA256 = (
    "2041448a87ef8548b0fa9d5aedf7e33aad4b549b8cc58a2705edc0fffe8bce01"
)
AI_REVIEW_GOVERNANCE_AMENDMENT_VERSION = (
    "p3.3-s6-ai-review-governance-amendment-v1"
)
AI_REVIEW_GOVERNANCE_AMENDMENT_SHA256 = (
    "87ee1a8793c218c12bfc18903c3efae1a295e8bdefc37061470a6d14f05db1cb"
)
AI_REVIEW_ATTESTATION_SCHEMA_VERSION = "p3.3-s6-independent-ai-review-v1"
AI_REVIEW_ATTESTATION_SHA256 = (
    "19466729872aedef21d6f530c0ac0bd7866b521910b640eb63901210f3c6b07f"
)
AI_REVIEWER_ROLE = "independent_ai_architect_claude_code"
AI_REVIEWER_PRODUCT = "Claude Code"
AI_REVIEWER_MODEL = "claude-fable-5"
HUMAN_REVIEWER_ROLE = "independent_data_architect"
INDEPENDENT_AI_SIGNATURE_STATUS = "independent_ai_approved"
INDEPENDENT_HUMAN_SIGNATURE_STATUS = "independent_human_approved"
APPROVED_REVIEWER_PROFILES: dict[str, tuple[str, str]] = {
    HUMAN_REVIEWER_ROLE: ("human", INDEPENDENT_HUMAN_SIGNATURE_STATUS),
    AI_REVIEWER_ROLE: ("ai", INDEPENDENT_AI_SIGNATURE_STATUS),
}
# Atomic release profile for the deterministic signed projection. A SHA alone
# must never classify an AI reviewer as human or detach the approval from its
# separately anchored governance amendment and review attestation.
FROZEN_PAIRING_V3_SIGNED_EVIDENCE_TRUST_ANCHOR: dict[str, str] | None = {
    "sha256": "1f1688a4a244e0e6b7e2b939b5b8d626f0e942665769f87708364294064614e7",
    "reviewer_role": AI_REVIEWER_ROLE,
    "signature_status": INDEPENDENT_AI_SIGNATURE_STATUS,
    "adjudication_contract": ADJUDICATION_CONTRACT_VERSION,
    "ai_review_attestation_sha256": AI_REVIEW_ATTESTATION_SHA256,
    "governance_amendment": AI_REVIEW_GOVERNANCE_AMENDMENT_VERSION,
    "governance_amendment_sha256": AI_REVIEW_GOVERNANCE_AMENDMENT_SHA256,
}
LOCAL_STORAGE_QUANTUM = 0.000001
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_TOTAL_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_AI_REVIEW_ATTESTATION_BYTES = 64 * 1024

_FORMULA_EXPRESSIONS = {
    "hfq_event_multiplier_product_v1": "product(event_multipliers)",
    "net_profit_yoy_v1": "(net_profit_t-net_profit_t_minus_4)/abs(net_profit_t_minus_4)",
    "roe_average_parent_equity_v1": (
        "parent_net_profit_t/((opening_parent_equity+closing_parent_equity)/2)"
    ),
    "mb_revenue_yoy_v1": "mb_revenue_t/mb_revenue_t_minus_4-1",
}
_FORMULA_OPERANDS = {
    "net_profit_yoy_v1": ("net_profit_t", "net_profit_t_minus_4"),
    "roe_average_parent_equity_v1": (
        "parent_net_profit_t",
        "opening_parent_equity",
        "closing_parent_equity",
    ),
    "mb_revenue_yoy_v1": ("mb_revenue_t", "mb_revenue_t_minus_4"),
}
_FROZEN_DAILY_EVENTS: dict[
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
_OFFICIAL_EX_REFERENCE_PRICES = {
    ("001260", "2026-05-27"): 20.29,
    ("600782", "2026-06-05"): 2.64,
}
_FROZEN_FINANCIAL_OPERANDS: dict[
    tuple[str, str, str],
    dict[str, tuple[float, str]],
] = {
    ("000897", "2024Q3", "net_profit_yoy"): {
        "net_profit_t": (368_514_961.65, "净利润（本期累计）"),
        "net_profit_t_minus_4": (494_218_221.65, "净利润（上年同期累计）"),
    },
    ("002012", "2018Q2", "net_profit_yoy"): {
        "net_profit_t": (15_888_115.75, "净利润（本期累计）"),
        "net_profit_t_minus_4": (26_571_199.10, "净利润（上年同期累计）"),
    },
    ("300433", "2025Q3", "roe"): {
        "parent_net_profit_t": (
            2_842_952_844.41,
            "归属于母公司所有者的净利润",
        ),
        "opening_parent_equity": (
            48_656_642_054.21,
            "归属于母公司所有者权益（期初）",
        ),
        "closing_parent_equity": (
            53_845_361_611.79,
            "归属于母公司所有者权益（期末）",
        ),
    },
}
_ALLOWED_SOURCE_HOSTS = frozenset(
    {
        "datacenter-web.eastmoney.com",
        "disc.static.szse.cn",
        "docs.static.szse.cn",
        "query.sse.com.cn",
        "static.cninfo.com.cn",
        "static.sse.com.cn",
        "www.sse.com.cn",
        "www.szse.cn",
    }
)
ADJUSTMENT_EVENT_TAXONOMY_VERSION = "cn-a-share-adjustment-title-v1"
_ADJUSTMENT_TITLE_TERMS = re.compile(
    r"权益分派|利润分配|现金分红|分红派息|派息|派现|送股|转增|"
    r"配股|供股|拆股|并股|股份拆细|合股|股权分置改革|股改|"
    r"除权|除息|资本公积"
)
_IMPLEMENTED_DISTRIBUTION = re.compile(
    r"(权益分派|利润分配|现金分红|分红派息|派息|派现|送股|转增|"
    r"资本公积).{0,16}(实施公告|实施方案|实施结果)"
)
_RIGHTS_ISSUE_IMPLEMENTED = re.compile(r"(配股|供股).{0,20}(实施|发行结果|上市|缴款|认购)")
_SPLIT_IMPLEMENTED = re.compile(r"(拆股|股份拆细).{0,20}(实施|完成|上市)")
_MERGE_IMPLEMENTED = re.compile(r"(并股|合股).{0,20}(实施|完成|上市)")
_SHARE_REFORM_IMPLEMENTED = re.compile(r"(股权分置改革|股改).{0,20}(实施|完成|上市)")
_OTHER_PRICE_ADJUSTMENT_IMPLEMENTED = re.compile(r"(除权|除息|价格调整).{0,20}(实施|公告|通知)")
_FORBIDDEN_VERDICTS = frozenset(
    {"explained_mismatch", "waived", "manual_override", "narrative_only_pass"}
)
_HEX_SHA256 = "^[0-9a-f]{64}$"


def _key_schema(*fields: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(fields),
        "properties": {field: {"type": "string", "minLength": 1} for field in fields},
    }


PAIRING_V3_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$defs": {
        "sha256": {"type": "string", "pattern": _HEX_SHA256},
        # checked_values deliberately has no shape def here: it must be
        # canonically byte-identical to the frozen trial's checked_values
        # (including mismatch entries with pass=false), which a passing-check
        # schema shape cannot describe.
        "artifact": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "id",
                "relative_path",
                "sha256",
                "source_kind",
                "source_identity",
                "request_parameters",
                "retrieved_at",
                "timezone",
                "parser_version",
                "actual_fields",
                "content_scope",
                "first_success",
                "first_success_response_sha256",
                "fallback_reason",
                "prior_source_errors",
                "missing_state",
            ],
            "properties": {
                "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._-]{0,63}$"},
                "relative_path": {"type": "string", "minLength": 1},
                "sha256": {"$ref": "#/$defs/sha256"},
                "source_kind": {
                    "enum": [
                        "official_exchange_disclosure",
                        "official_exchange_rule",
                        "issuer_xbrl",
                        "audited_external_response",
                        "frozen_local_manifest",
                    ]
                },
                "source_identity": {"type": "string", "minLength": 1},
                "request_parameters": {"type": "object"},
                "retrieved_at": {"type": "string", "minLength": 1},
                "timezone": {"type": "string", "minLength": 1},
                "parser_version": {"type": "string", "minLength": 1},
                "actual_fields": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1},
                },
                "content_scope": {"const": "full_response_body"},
                "first_success": {"const": True},
                "first_success_response_sha256": {"$ref": "#/$defs/sha256"},
                "fallback_reason": {"type": ["string", "null"]},
                "prior_source_errors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["source", "error_type"],
                        "properties": {
                            "source": {"type": "string", "minLength": 1},
                            "error_type": {"type": "string", "minLength": 1},
                        },
                    },
                },
                "missing_state": {"enum": ["present", "field_absent", "field_null"]},
            },
        },
        "operand": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "name",
                "value",
                "lower",
                "upper",
                "unit",
                "line_item",
                "disclosure_precision",
                "evidence_id",
                "event",
            ],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "value": {"type": "number"},
                "lower": {"type": "number"},
                "upper": {"type": "number"},
                "unit": {"type": "string", "minLength": 1},
                "line_item": {"type": "string", "minLength": 1},
                "disclosure_precision": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["basis", "quantum"],
                    "properties": {
                        "basis": {
                            "enum": [
                                "xbrl_decimals",
                                "disclosed_unit",
                                "exact_machine_fact",
                            ]
                        },
                        "quantum": {"type": "number", "minimum": 0},
                    },
                },
                "evidence_id": {"type": "string", "minLength": 1},
                "event": {
                    "oneOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "event_type",
                                "ex_date",
                                "event_formula_id",
                                "pre_close",
                                "cash_dividend_per_share",
                                "share_distribution_per_share",
                                "price_tick",
                                "ex_reference_price",
                                "event_multiplier",
                                "announcement_evidence_id",
                                "pre_close_evidence_id",
                                "price_tick_evidence_id",
                                "reference_price_evidence_id",
                                "rounding_provenance",
                            ],
                            "properties": {
                                "event_type": {
                                    "enum": [
                                        "cash_dividend",
                                        "share_distribution",
                                        "cash_and_share_distribution",
                                    ]
                                },
                                "ex_date": {"type": "string", "format": "date"},
                                "event_formula_id": {
                                    "enum": [
                                        "cash_share_price_grid_v1",
                                        "official_reference_price_ratio_v1",
                                    ]
                                },
                                "pre_close": {
                                    "type": "number",
                                    "exclusiveMinimum": 0,
                                },
                                "cash_dividend_per_share": {
                                    "type": "number",
                                    "minimum": 0,
                                },
                                "share_distribution_per_share": {
                                    "type": "number",
                                    "minimum": 0,
                                },
                                "price_tick": {
                                    "type": "number",
                                    "exclusiveMinimum": 0,
                                },
                                "ex_reference_price": {
                                    "type": "number",
                                    "exclusiveMinimum": 0,
                                },
                                "event_multiplier": {
                                    "type": "number",
                                    "exclusiveMinimum": 0,
                                },
                                "announcement_evidence_id": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                                "pre_close_evidence_id": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                                "price_tick_evidence_id": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                                "reference_price_evidence_id": {
                                    "type": ["string", "null"],
                                    "minLength": 1,
                                },
                                "rounding_provenance": {
                                    "enum": [
                                        "exchange_rule_round_half_up",
                                        "exchange_published_reference_price_local_rounding_none",
                                    ]
                                },
                            },
                        },
                    ]
                },
            },
        },
        "formula_proof": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "formula_id",
                "expression",
                "operands",
                "result",
                "local_storage_quantum",
                "event_window",
            ],
            "properties": {
                "formula_id": {"enum": sorted(_FORMULA_EXPRESSIONS)},
                "expression": {"type": "string", "minLength": 1},
                "operands": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"$ref": "#/$defs/operand"},
                },
                "result": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["value", "lower", "upper"],
                    "properties": {
                        "value": {"type": "number"},
                        "lower": {"type": "number"},
                        "upper": {"type": "number"},
                    },
                },
                "local_storage_quantum": {"const": LOCAL_STORAGE_QUANTUM},
                "event_window": {
                    "oneOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "start_date",
                                "end_date",
                                "complete",
                                "inventory_evidence_ids",
                                "symbol",
                                "inventory_source",
                                "page_count",
                                "pages",
                                "total_reported_records",
                                "raw_total_records",
                                "taxonomy_version",
                                "classification_summary",
                                "classification_sha256",
                            ],
                            "properties": {
                                "start_date": {"type": "string", "format": "date"},
                                "end_date": {"type": "string", "format": "date"},
                                "complete": {"const": True},
                                "inventory_evidence_ids": {
                                    "type": "array",
                                    "minItems": 1,
                                    "uniqueItems": True,
                                    "items": {"type": "string", "minLength": 1},
                                },
                                "symbol": {"type": "string", "minLength": 1},
                                "inventory_source": {"const": "official_exchange_full_pagination"},
                                "page_count": {
                                    "type": "integer",
                                    "minimum": 1,
                                },
                                "pages": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": [
                                            "page_number",
                                            "evidence_id",
                                            "reported_records",
                                            "raw_response_records",
                                            "not_factor_adjustment_records",
                                            "not_adjustment_related_records",
                                            "unknown_adjustment_candidate_records",
                                        ],
                                        "properties": {
                                            "page_number": {
                                                "type": "integer",
                                                "minimum": 1,
                                            },
                                            "evidence_id": {
                                                "type": "string",
                                                "minLength": 1,
                                            },
                                            "reported_records": {
                                                "type": "integer",
                                                "minimum": 0,
                                            },
                                            "raw_response_records": {
                                                "type": "integer",
                                                "minimum": 0,
                                            },
                                            "not_factor_adjustment_records": {
                                                "type": "integer",
                                                "minimum": 0,
                                            },
                                            "not_adjustment_related_records": {
                                                "type": "integer",
                                                "minimum": 0,
                                            },
                                            "unknown_adjustment_candidate_records": {
                                                "const": 0,
                                            },
                                        },
                                    },
                                },
                                "total_reported_records": {
                                    "type": "integer",
                                    "minimum": 1,
                                },
                                "raw_total_records": {
                                    "type": "integer",
                                    "minimum": 1,
                                },
                                "taxonomy_version": {
                                    "const": ADJUSTMENT_EVENT_TAXONOMY_VERSION,
                                },
                                "classification_summary": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "implemented_adjustment_event",
                                        "not_factor_adjustment",
                                        "not_adjustment_related",
                                        "unknown_adjustment_candidate",
                                    ],
                                    "properties": {
                                        "implemented_adjustment_event": {
                                            "type": "integer",
                                            "minimum": 1,
                                        },
                                        "not_factor_adjustment": {
                                            "type": "integer",
                                            "minimum": 0,
                                        },
                                        "not_adjustment_related": {
                                            "type": "integer",
                                            "minimum": 0,
                                        },
                                        "unknown_adjustment_candidate": {
                                            "const": 0,
                                        },
                                    },
                                },
                                "classification_sha256": {"$ref": "#/$defs/sha256"},
                            },
                        },
                    ]
                },
            },
        },
        "unavailable_proof": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "cadence_contract",
                "expected_quarters",
                "observed_quarter",
                "local_value",
                "payload_reason",
                "mapping_status",
                "approximate_substitute_used",
                "request_status",
                "missing_state",
                "examined_line_items",
                "exact_line_item_candidates",
            ],
            "properties": {
                "cadence_contract": {"const": "semiannual_q2_q4_from_baostock_mb_revenue"},
                "expected_quarters": {
                    "const": [2, 4],
                },
                "observed_quarter": {"type": "integer", "minimum": 1, "maximum": 4},
                "local_value": {"type": "null"},
                "payload_reason": {"const": "missing_current_revenue"},
                "mapping_status": {"const": "no_unique_exact_mbrevenue_line_item"},
                "approximate_substitute_used": {"const": False},
                "request_status": {"const": "success"},
                "missing_state": {"enum": ["field_absent", "field_null"]},
                "examined_line_items": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "mapping_decision"],
                        "properties": {
                            "name": {"type": "string", "minLength": 1},
                            "mapping_decision": {"enum": ["approximate", "not_same_metric"]},
                        },
                    },
                },
                "exact_line_item_candidates": {
                    "const": [
                        {"name": "主营业务收入", "match_count": 0},
                        {"name": "主营营业收入", "match_count": 0},
                    ]
                },
            },
        },
        "sample": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "table",
                "key",
                "verdict",
                "trial_sample_sha256",
                "evidence_ids",
            ],
            "properties": {
                "table": {
                    "enum": [
                        "daily_bars",
                        "financial_indicators",
                        "valuation_daily",
                    ]
                },
                "key": {
                    "oneOf": [
                        _key_schema("symbol", "trade_date"),
                        _key_schema("symbol", "report_period", "metric"),
                    ]
                },
                "verdict": {
                    "enum": [
                        "numeric_match",
                        "formula_match",
                        "expected_unavailable",
                    ]
                },
                "trial_sample_sha256": {"$ref": "#/$defs/sha256"},
                "evidence_ids": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1},
                },
                "checked_values": {"type": "object"},
                "formula_proof": {"$ref": "#/$defs/formula_proof"},
                "unavailable_proof": {"$ref": "#/$defs/unavailable_proof"},
            },
        },
    },
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "adjudication_contract",
        "adjudication_contract_sha256",
        "pit_manifest_schema_version",
        "pit_manifest_sha256",
        "final_trial",
        "approved",
        "reviewed_at",
        "reviewer_role",
        "seed",
        "sample_size_per_table",
        "artifacts",
        "samples",
        "summary",
    ],
    "properties": {
        "schema_version": {"const": PAIRING_V3_SCHEMA_VERSION},
        "adjudication_contract": {"const": ADJUDICATION_CONTRACT_VERSION},
        "adjudication_contract_sha256": {"const": ADJUDICATION_CONTRACT_SHA256},
        "pit_manifest_schema_version": {"const": "p3.3-s6-local-pit-manifest-v1"},
        "pit_manifest_sha256": {"const": FROZEN_MANIFEST_SHA256},
        "final_trial": {
            "type": "object",
            "additionalProperties": False,
            "required": ["relative_path", "sha256"],
            "properties": {
                "relative_path": {"type": "string", "minLength": 1},
                "sha256": {"const": FROZEN_FINAL_TRIAL_SHA256},
            },
        },
        "approved": {"type": "boolean"},
        "reviewed_at": {
            "oneOf": [
                {"type": "null"},
                {"type": "string", "minLength": 1},
            ]
        },
        "reviewer_role": {"type": "string", "minLength": 1},
        "seed": {"const": FROZEN_SEED},
        "sample_size_per_table": {"const": FROZEN_SAMPLE_SIZE_PER_TABLE},
        "artifacts": {
            "type": "array",
            "minItems": 1,
            "items": {"$ref": "#/$defs/artifact"},
        },
        "samples": {
            "type": "array",
            "minItems": FROZEN_SAMPLE_COUNT,
            "maxItems": FROZEN_SAMPLE_COUNT,
            "items": {"$ref": "#/$defs/sample"},
        },
        "summary": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "sample_count",
                "numeric_match",
                "formula_match",
                "expected_unavailable",
                "unresolved",
                "generic_unavailable",
                "ambiguous_mapping",
                "schema_hash_integrity_errors",
            ],
            "properties": {
                "sample_count": {"const": FROZEN_SAMPLE_COUNT},
                "numeric_match": {"type": "integer", "minimum": 0},
                "formula_match": {"type": "integer", "minimum": 0},
                "expected_unavailable": {"type": "integer", "minimum": 0},
                "unresolved": {"const": 0},
                "generic_unavailable": {"const": 0},
                "ambiguous_mapping": {"const": 0},
                "schema_hash_integrity_errors": {"const": 0},
            },
        },
    },
}


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_unsigned_candidate_v3(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached machine-verification candidate, never a sign-off."""

    candidate = deepcopy(dict(document))
    candidate["approved"] = False
    candidate["reviewer_role"] = "pending"
    candidate["reviewed_at"] = None
    return candidate


def normalized_unsigned_candidate_sha256(document: Mapping[str, Any]) -> str:
    """Hash the machine proof while excluding only human review metadata."""

    unsigned = deepcopy(dict(document))
    unsigned["approved"] = False
    unsigned["reviewer_role"] = "pending"
    unsigned["reviewed_at"] = None
    return canonical_sha256(unsigned)


def _finite_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _finite_external_number(value: object, *, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    if isinstance(value, str):
        if not value.strip():
            raise ValueError(f"{label} must be a finite number")
        try:
            result = float(value)
        except ValueError as exc:
            raise ValueError(f"{label} must be a finite number") from exc
    elif isinstance(value, (int, float)):
        result = float(value)
    else:
        raise ValueError(f"{label} must be a finite number")
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def classify_adjustment_announcement_title(title: str) -> tuple[str, str]:
    """Classify every official announcement title under the frozen event taxonomy.

    The first element is a stable category and the second is one of
    ``implemented_adjustment_event``, ``not_factor_adjustment``,
    ``not_adjustment_related`` or ``unknown_adjustment_candidate``.
    The ordering intentionally removes derivative/security-holder disclosures
    before matching distribution words.
    """

    normalized = re.sub(r"\s+", "", title)
    if not normalized:
        return "empty_title", "unknown_adjustment_candidate"
    if re.search(r"可转债|转债|转股价格|行权价格|暂停转股", normalized) and re.search(
        r"权益分派|利润分配|分红|派息|除权|除息",
        normalized,
    ):
        return "derivative_parameter_adjustment", "not_factor_adjustment"
    if re.search(r"权益变动|持股变动|股东权益", normalized):
        return "shareholding_change_disclosure", "not_factor_adjustment"
    if re.search(
        r"股权激励|限制性股票|股票期权|员工持股|股份回购|回购股份|注销股份",
        normalized,
    ):
        return "capital_or_incentive_disclosure", "not_factor_adjustment"
    if _IMPLEMENTED_DISTRIBUTION.search(normalized):
        return "distribution_implementation", "implemented_adjustment_event"
    if _RIGHTS_ISSUE_IMPLEMENTED.search(normalized):
        return "rights_issue_implementation", "implemented_adjustment_event"
    if _SPLIT_IMPLEMENTED.search(normalized):
        return "share_split_implementation", "implemented_adjustment_event"
    if _MERGE_IMPLEMENTED.search(normalized):
        return "share_merge_implementation", "implemented_adjustment_event"
    if _SHARE_REFORM_IMPLEMENTED.search(normalized):
        return "share_reform_implementation", "implemented_adjustment_event"
    if _OTHER_PRICE_ADJUSTMENT_IMPLEMENTED.search(normalized):
        return "other_price_adjustment_implementation", ("unknown_adjustment_candidate")
    if _ADJUSTMENT_TITLE_TERMS.search(normalized):
        if re.search(
            r"预案|方案|议案|股东会|董事会|监事会|年度报告|半年度报告|"
            r"季度报告|问询|回复|提示性公告|进展|取消|终止|不进行|"
            r"利润分配的公告|分红规划|征求意见",
            normalized,
        ):
            return "proposal_governance_or_status", "not_factor_adjustment"
        return "unclassified_adjustment_term", "unknown_adjustment_candidate"
    return "not_adjustment_related", "not_adjustment_related"


def validate_unfiltered_inventory_request(
    request: Mapping[str, Any],
    *,
    symbol: str,
    start_date: str,
    end_date: str,
    page_number: int,
) -> None:
    """Reject filtered, widened or ambiguously paged announcement requests."""

    params = request.get("params")
    if not isinstance(params, dict):
        raise ValueError("inventory original request params are absent")
    method = str(request.get("method") or "").upper()
    source_url = str(request.get("url") or "")
    source_host = (urlparse(source_url).hostname or "").casefold()
    if symbol.startswith("6"):
        if (
            method != "GET"
            or source_host != "query.sse.com.cn"
            or str(params.get("productId") or "") != symbol
            or str(params.get("beginDate") or "") != start_date
            or str(params.get("endDate") or "") != end_date
            or str(params.get("reportType") or "") != "ALL"
            or params.get("keyWord") not in (None, "", [])
            or params.get("reportType2") not in (None, "", [])
            or int(params.get("pageHelp.pageNo") or 0) != page_number
            or int(params.get("pageHelp.beginPage") or 0) != page_number
            or int(params.get("pageHelp.pageSize") or 0) <= 0
        ):
            raise ValueError(
                "SSE inventory request must be exact-window, unfiltered and unambiguously paged"
            )
        return
    stocks = params.get("stock")
    dates = params.get("seDate")
    if (
        method != "POST"
        or source_host != "www.szse.cn"
        or stocks != [symbol]
        or dates != [start_date, end_date]
        or params.get("searchKey") not in (None, "", [])
        or params.get("channelCode") != ["listedNotice_disc"]
        or int(params.get("pageNum") or 0) != page_number
        or int(params.get("pageSize") or 0) <= 0
    ):
        raise ValueError(
            "SZSE inventory request must be exact-window, unfiltered and unambiguously paged"
        )


def _same_number(left: float, right: float) -> bool:
    # Contract values already carry an explicit disclosure/storage interval.
    # A relative tolerance would grow with large CNY statement values (for
    # example, accepting several cents on a 50bn equity operand), silently
    # widening the separately declared 0.01-CNY precision.
    return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-12)


def _strict_datetime(value: object, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed


def _safe_bundle_file(
    bundle_root: Path,
    relative_path: object,
    *,
    label: str,
) -> Path:
    raw = str(relative_path)
    pure = PurePosixPath(raw)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise ValueError(f"{label} must be a bundle-relative path")
    root = bundle_root.resolve()
    resolved = (root / Path(*pure.parts)).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the evidence bundle") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} does not name a regular file")
    return resolved


def _file_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARTIFACT_BYTES:
                raise ValueError(f"external PIT artifact exceeds {MAX_ARTIFACT_BYTES} bytes")
            digest.update(chunk)
    return digest.hexdigest(), size


def _strict_json_object_file(path: Path, *, label: str) -> dict[str, Any]:
    def reject_duplicate_keys(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate JSON key: {key}")
            result[key] = item
        return result

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"{label} contains non-finite JSON number: {value}")

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be readable strict JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


class _SZSEDividendTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headers: list[str] = []
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._header = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized == "tr":
            self._row = []
        elif normalized in {"td", "th"}:
            self._cell = []
            self._header = normalized == "th"

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in {"td", "th"} and self._cell is not None:
            value = " ".join("".join(self._cell).split())
            if self._header:
                self.headers.append(value)
            elif self._row is not None:
                self._row.append(value)
            self._cell = None
            self._header = False
        elif normalized == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _strict_szse_dividend_target(
    path: Path,
    *,
    symbol: str,
    ex_date: str,
) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="gb18030")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("official SZSE ex-reference raw response must be GB18030 HTML") from exc
    parser = _SZSEDividendTableParser()
    parser.feed(text)
    headers = " ".join(parser.headers)
    if "DPS" not in headers or "Ex-Price" not in headers or "Pre-Closing" not in headers:
        raise ValueError("official SZSE ex-reference raw table headers differ")
    matches = [
        row
        for row in parser.rows
        if len(row) == 14 and row[0] == symbol and row[10] == ex_date.replace("-", "/")
    ]
    if len(matches) != 1:
        raise ValueError("official SZSE ex-reference raw target row is not unique")
    row = matches[0]
    return {
        "symbol": row[0],
        "security_name": row[1],
        "cash_dividend_per_share": _finite_external_number(
            row[5],
            label="official SZSE raw DPS",
        ),
        "ex_date": row[10].replace("/", "-"),
        "registration_date": row[11].replace("/", "-"),
        "ex_reference_price": _finite_external_number(
            row[12],
            label="official SZSE raw Ex-Price",
        ),
        "pre_closing_price": _finite_external_number(
            row[13],
            label="official SZSE raw Pre-Closing",
        ),
    }


def _inventory_page(
    path: Path,
    *,
    symbol: str,
) -> tuple[int, list[tuple[str, str, str, str]]]:
    response = _strict_json_object_file(path, label="HFQ inventory response")
    if "announceCount" in response and "data" in response:
        try:
            total = int(response["announceCount"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "HFQ inventory announceCount must be an integer"
            ) from exc
        raw_records = response["data"]
        market = "SZSE"
    elif isinstance(response.get("pageHelp"), dict):
        page_help = response["pageHelp"]
        try:
            total = int(page_help.get("total") or -1)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "HFQ inventory pageHelp total must be an integer"
            ) from exc
        raw_records = page_help.get("data")
        market = "SSE"
    else:
        raise ValueError("HFQ inventory response has an unsupported shape")
    if not isinstance(raw_records, list):
        raise ValueError("HFQ inventory response rows must be a list")
    records: list[tuple[str, str, str, str]] = []
    for raw_record in raw_records:
        if not isinstance(raw_record, dict):
            raise ValueError("HFQ inventory row must be an object")
        if market == "SZSE":
            record_id = str(raw_record.get("annId") or raw_record.get("id") or "")
            raw_symbols = raw_record.get("secCode")
            record_symbol = (
                str(raw_symbols[0]) if isinstance(raw_symbols, list) and raw_symbols else ""
            )
            title = str(raw_record.get("title") or "")
            document_path = str(raw_record.get("attachPath") or "")
            publish_date = str(raw_record.get("publishTime") or "")[:10]
        else:
            document_path = str(raw_record.get("URL") or "")
            record_id = document_path
            record_symbol = str(raw_record.get("SECURITY_CODE") or "")
            title = str(raw_record.get("TITLE") or "")
            publish_date = str(raw_record.get("SSEDATE") or raw_record.get("SSEDate") or "")[:10]
        try:
            datetime.fromisoformat(publish_date)
        except ValueError as exc:
            raise ValueError("HFQ inventory row publication date is invalid") from exc
        if not record_id or record_symbol != symbol or not document_path or not title:
            raise ValueError("HFQ inventory row identity/symbol/path is invalid")
        records.append((record_id, title, document_path, publish_date))
    return total, records


def parse_official_inventory_page(
    path: Path,
    *,
    symbol: str,
) -> tuple[int, list[tuple[str, str, str, str]]]:
    """Strictly parse one official SSE/SZSE announcement response page."""

    return _inventory_page(path, symbol=symbol)


def _validate_artifacts(
    document: Mapping[str, Any],
    *,
    bundle_root: Path,
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Path]]:
    artifacts = document["artifacts"]
    if not isinstance(artifacts, list):
        raise ValueError("pairing-v3 artifacts must be a list")
    by_id: dict[str, Mapping[str, Any]] = {}
    paths_by_id: dict[str, Path] = {}
    seen_relative_paths: set[str] = set()
    total_bytes = 0
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ValueError("pairing-v3 artifact must be an object")
        artifact_id = str(artifact["id"])
        if artifact_id in by_id:
            raise ValueError(f"pairing-v3 contains duplicate artifact id: {artifact_id}")
        source_kind = str(artifact["source_kind"])
        relative_path = str(artifact["relative_path"])
        if relative_path in seen_relative_paths:
            raise ValueError("pairing-v3 artifacts must not reuse one relative path")
        seen_relative_paths.add(relative_path)
        if (
            "baostock" in str(artifact["source_identity"]).casefold()
            and source_kind != "frozen_local_manifest"
        ):
            raise ValueError("pairing-v3 external artifacts must not use BaoStock")
        if source_kind == "frozen_local_manifest":
            request_parameters = artifact["request_parameters"]
            if (
                request_parameters.get("pit_manifest_sha256") != FROZEN_MANIFEST_SHA256
                or request_parameters.get("access_mode") != "read_only"
            ):
                raise ValueError(
                    "frozen-local evidence must bind the frozen manifest and read-only access mode"
                )
        else:
            request_parameters = artifact["request_parameters"]
            source_url = str(request_parameters.get("source_url") or "")
            parsed_source_url = urlparse(source_url)
            if (
                parsed_source_url.scheme != "https"
                or parsed_source_url.hostname not in _ALLOWED_SOURCE_HOSTS
            ):
                raise ValueError(
                    f"artifact {artifact_id} source_url is not on the "
                    "frozen official/audited host allowlist"
                )
        _strict_datetime(
            artifact["retrieved_at"],
            label=f"artifact {artifact_id} retrieved_at",
        )
        if not str(artifact["timezone"]).strip():
            raise ValueError(f"artifact {artifact_id} timezone must not be blank")
        artifact_path = _safe_bundle_file(
            bundle_root,
            relative_path,
            label=f"artifact {artifact_id} relative_path",
        )
        observed_sha256, artifact_bytes = _file_sha256(artifact_path)
        total_bytes += artifact_bytes
        if total_bytes > MAX_TOTAL_ARTIFACT_BYTES:
            raise ValueError(f"external PIT artifacts exceed {MAX_TOTAL_ARTIFACT_BYTES} bytes")
        if observed_sha256 != str(artifact["sha256"]):
            raise ValueError(f"artifact {artifact_id} SHA-256 mismatch")
        if str(artifact["first_success_response_sha256"]) != observed_sha256:
            raise ValueError(
                f"artifact {artifact_id} is not bound to its first successful response"
            )
        fallback_reason = artifact["fallback_reason"]
        prior_errors = artifact["prior_source_errors"]
        if bool(fallback_reason) != bool(prior_errors):
            raise ValueError(f"artifact {artifact_id} fallback reason/error trail is inconsistent")
        by_id[artifact_id] = artifact
        paths_by_id[artifact_id] = artifact_path
    return by_id, paths_by_id


def _operand_interval(operand: Mapping[str, Any]) -> tuple[float, float, float]:
    value = _finite_number(operand["value"], label="formula operand value")
    lower = _finite_number(operand["lower"], label="formula operand lower")
    upper = _finite_number(operand["upper"], label="formula operand upper")
    if lower > value or value > upper:
        raise ValueError("formula operand value must lie inside its interval")
    precision = operand["disclosure_precision"]
    if not isinstance(precision, dict):
        raise ValueError("formula operand disclosure_precision must be an object")
    quantum = _finite_number(
        precision["quantum"],
        label="formula operand disclosure quantum",
    )
    if quantum < 0:
        raise ValueError("formula operand disclosure quantum must be non-negative")
    expected_lower = value - quantum / 2.0
    expected_upper = value + quantum / 2.0
    if not _same_number(lower, expected_lower) or not _same_number(
        upper,
        expected_upper,
    ):
        raise ValueError("formula operand interval does not match its disclosed precision")
    return value, lower, upper


def _mul_interval(
    left: tuple[float, float],
    right: tuple[float, float],
) -> tuple[float, float]:
    products = (
        left[0] * right[0],
        left[0] * right[1],
        left[1] * right[0],
        left[1] * right[1],
    )
    return min(products), max(products)


def _div_interval(
    numerator: tuple[float, float],
    denominator: tuple[float, float],
) -> tuple[float, float]:
    if denominator[0] <= 0 <= denominator[1]:
        raise ValueError("formula denominator interval crosses zero")
    reciprocals = (1.0 / denominator[0], 1.0 / denominator[1])
    return _mul_interval(numerator, (min(reciprocals), max(reciprocals)))


def _formula_result(
    formula_id: str,
    operands: Mapping[str, tuple[float, float, float]],
) -> tuple[float, float, float]:
    if formula_id == "hfq_event_multiplier_product_v1":
        point = 1.0
        interval = (1.0, 1.0)
        for value, lower, upper in operands.values():
            if value <= 0 or lower <= 0:
                raise ValueError("HFQ event multipliers must be positive")
            point *= value
            interval = _mul_interval(interval, (lower, upper))
        return point, interval[0], interval[1]
    if formula_id == "net_profit_yoy_v1":
        current = operands["net_profit_t"]
        prior = operands["net_profit_t_minus_4"]
        if prior[1] <= 0 <= prior[2]:
            raise ValueError("net_profit_yoy prior interval crosses zero")
        point = (current[0] - prior[0]) / abs(prior[0])
        numerator = (current[1] - prior[2], current[2] - prior[1])
        abs_prior = sorted((abs(prior[1]), abs(prior[2])))
        lower, upper = _div_interval(numerator, (abs_prior[0], abs_prior[1]))
        return point, lower, upper
    if formula_id == "roe_average_parent_equity_v1":
        profit = operands["parent_net_profit_t"]
        opening = operands["opening_parent_equity"]
        closing = operands["closing_parent_equity"]
        point_denominator = (opening[0] + closing[0]) / 2.0
        denominator = (
            (opening[1] + closing[1]) / 2.0,
            (opening[2] + closing[2]) / 2.0,
        )
        point = profit[0] / point_denominator
        lower, upper = _div_interval((profit[1], profit[2]), denominator)
        return point, lower, upper
    if formula_id == "mb_revenue_yoy_v1":
        current = operands["mb_revenue_t"]
        prior = operands["mb_revenue_t_minus_4"]
        point = current[0] / prior[0] - 1.0
        lower, upper = _div_interval(
            (current[1], current[2]),
            (prior[1], prior[2]),
        )
        return point, lower - 1.0, upper - 1.0
    raise ValueError(f"unsupported pairing-v3 formula: {formula_id}")


def _expected_formula_for_sample(
    table: str,
    current_sample: Mapping[str, Any],
) -> str:
    if table == "daily_bars":
        return "hfq_event_multiplier_product_v1"
    metric = str(current_sample.get("metric") or "")
    try:
        return {
            "net_profit_yoy": "net_profit_yoy_v1",
            "roe": "roe_average_parent_equity_v1",
            "revenue_yoy": "mb_revenue_yoy_v1",
        }[metric]
    except KeyError as exc:
        raise ValueError(f"formula_match is not allowed for {table}.{metric or '<blank>'}") from exc


def _validate_event_formula(
    event: Mapping[str, Any],
    *,
    operand_value: float,
    event_window: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    artifact_paths: Mapping[str, Path],
) -> None:
    pre_close = _finite_number(event["pre_close"], label="event pre_close")
    cash = _finite_number(
        event["cash_dividend_per_share"],
        label="event cash_dividend_per_share",
    )
    shares = _finite_number(
        event["share_distribution_per_share"],
        label="event share_distribution_per_share",
    )
    tick = _finite_number(event["price_tick"], label="event price_tick")
    reference = _finite_number(
        event["ex_reference_price"],
        label="event ex_reference_price",
    )
    multiplier = _finite_number(
        event["event_multiplier"],
        label="event event_multiplier",
    )
    if min(pre_close, tick, reference, multiplier) <= 0 or min(cash, shares) < 0:
        raise ValueError("company-action event parameters are outside their domain")
    event_date = str(event["ex_date"])
    if not (str(event_window["start_date"]) < event_date <= str(event_window["end_date"])):
        raise ValueError("company-action event date is outside the frozen event window")
    formula_id = str(event["event_formula_id"])
    if formula_id == "cash_share_price_grid_v1":
        if (
            event["rounding_provenance"] != "exchange_rule_round_half_up"
            or event["reference_price_evidence_id"] is not None
        ):
            raise ValueError("price-grid event must declare ROUND_HALF_UP rule provenance")
        unrounded = (Decimal(str(pre_close)) - Decimal(str(cash))) / (
            Decimal(1) + Decimal(str(shares))
        )
        tick_decimal = Decimal(str(tick))
        expected_reference = (unrounded / tick_decimal).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        ) * tick_decimal
        if not _same_number(reference, float(expected_reference)):
            raise ValueError(
                "company-action ex-reference price was not quantized by the declared price grid"
            )
    elif formula_id == "official_reference_price_ratio_v1":
        if event["rounding_provenance"] != "exchange_published_reference_price_local_rounding_none":
            raise ValueError("official reference-price event must declare no local rounding")
        reference_id = str(event["reference_price_evidence_id"] or "")
        reference_artifact = artifacts.get(reference_id)
        if (
            not reference_id
            or reference_artifact is None
            or reference_artifact["source_kind"] != "audited_external_response"
            or reference_artifact["missing_state"] != "present"
            or "ex_reference_price" not in reference_artifact["actual_fields"]
        ):
            raise ValueError("official reference price requires present exchange response evidence")
        reference_parameters = reference_artifact["request_parameters"]
        expected_official_reference = _OFFICIAL_EX_REFERENCE_PRICES.get(
            (str(event_window["symbol"]), event_date)
        )
        if (
            expected_official_reference is None
            or str(reference_parameters.get("symbol") or "") != str(event_window["symbol"])
            or str(reference_parameters.get("ex_date") or "") != event_date
            or not _same_number(
                _finite_number(
                    reference_parameters.get("ex_reference_price"),
                    label="official event ex_reference_price",
                ),
                reference,
            )
            or not _same_number(reference, expected_official_reference)
        ):
            raise ValueError("official reference-price evidence differs from the event")
        symbol = str(event_window["symbol"])
        reference_path = artifact_paths[reference_id]
        source_url = str(reference_parameters.get("source_url") or "")
        source = urlparse(source_url)
        if symbol == "001260":
            if source.hostname != "docs.static.szse.cn" or source.path != (
                "/www/market/periodical/month/W020260605534753848014.html"
            ):
                raise ValueError("official SZSE ex-reference source URL differs")
            reference_row = _strict_szse_dividend_target(
                reference_path,
                symbol=symbol,
                ex_date=event_date,
            )
            if (
                reference_row["security_name"] != "坤泰股份"
                or reference_row["registration_date"] != "2026-05-26"
                or not _same_number(
                    float(reference_row["pre_closing_price"]),
                    pre_close,
                )
                or not _same_number(
                    float(reference_row["ex_reference_price"]),
                    reference,
                )
                or not _same_number(
                    float(reference_row["cash_dividend_per_share"]),
                    cash,
                )
            ):
                raise ValueError(
                    "official SZSE ex-reference raw response values differ from the event"
                )
        else:
            if source.hostname != "query.sse.com.cn":
                raise ValueError("official SSE ex-reference source URL differs")
            reference_response = _strict_json_object_file(
                reference_path,
                label="official ex-reference raw response",
            )
            reference_rows = reference_response.get("result")
            if not isinstance(reference_rows, list):
                raise ValueError("official ex-reference raw response rows are absent")
            reference_matches = [
                row
                for row in reference_rows
                if isinstance(row, dict)
                and str(row.get("A_STOCK_CODE") or "") == symbol
                and str(row.get("A_DIV_DATE") or "") == event_date.replace("-", "")
            ]
            if len(reference_matches) != 1:
                raise ValueError("official ex-reference raw response target row is not unique")
            reference_row = reference_matches[0]
            if not _same_number(
                _finite_external_number(
                    reference_row.get("PRE_CLOSE_PRICE"),
                    label="official raw PRE_CLOSE_PRICE",
                ),
                reference,
            ) or not _same_number(
                _finite_external_number(
                    reference_row.get("A_BEFR_TAX_DIV"),
                    label="official raw A_BEFR_TAX_DIV",
                ),
                cash,
            ):
                raise ValueError("official ex-reference raw response values differ from the event")
    else:
        raise ValueError(f"unsupported company-action formula: {formula_id}")
    expected_multiplier = pre_close / reference
    if not _same_number(multiplier, expected_multiplier):
        raise ValueError("company-action multiplier was not computed from pre-close/reference")
    if not _same_number(operand_value, multiplier):
        raise ValueError("company-action formula result does not match its event operand")
    announcement_id = str(event["announcement_evidence_id"])
    announcement = artifacts.get(announcement_id)
    if (
        announcement is None
        or announcement["source_kind"] != "official_exchange_disclosure"
        or announcement["missing_state"] != "present"
    ):
        raise ValueError("company-action parameters require present official announcement evidence")
    required_announcement_fields = {
        "event_type",
        "ex_date",
        "cash_dividend_per_share",
        "share_distribution_per_share",
    }
    if not required_announcement_fields.issubset(announcement["actual_fields"]):
        raise ValueError(
            "company-action announcement artifact does not expose all formula parameters"
        )
    pre_close_id = str(event["pre_close_evidence_id"])
    pre_close_artifact = artifacts.get(pre_close_id)
    if (
        pre_close_artifact is None
        or pre_close_artifact["source_kind"]
        not in {"audited_external_response", "frozen_local_manifest"}
        or pre_close_artifact["missing_state"] != "present"
        or "pre_close" not in pre_close_artifact["actual_fields"]
    ):
        raise ValueError(
            "company-action pre_close requires independent market-data or frozen-manifest evidence"
        )
    price_tick_id = str(event["price_tick_evidence_id"])
    price_tick_artifact = artifacts.get(price_tick_id)
    if (
        price_tick_artifact is None
        or price_tick_artifact["source_kind"] != "official_exchange_rule"
        or price_tick_artifact["missing_state"] != "present"
        or "price_tick" not in price_tick_artifact["actual_fields"]
    ):
        raise ValueError(
            "company-action price_tick requires present official exchange-rule evidence"
        )
    rule_parameters = price_tick_artifact["request_parameters"]
    declared_tick = _finite_number(
        rule_parameters.get("price_tick"),
        label="company-action exchange-rule price_tick",
    )
    if not _same_number(declared_tick, tick):
        raise ValueError("company-action event price_tick differs from its exchange rule")
    expected_market = "SSE" if str(event_window["symbol"]).startswith("6") else "SZSE"
    if str(rule_parameters.get("market") or "") != expected_market:
        raise ValueError("company-action price_tick rule market does not match the symbol")
    effective_from = str(rule_parameters.get("effective_from") or "")
    effective_to_raw = rule_parameters.get("effective_to")
    effective_to = str(effective_to_raw) if effective_to_raw is not None else None
    if (
        not effective_from
        or effective_from > event_date
        or (effective_to is not None and effective_to < event_date)
    ):
        raise ValueError("company-action price_tick rule is not effective on the event date")


def _validate_formula_proof(
    proof: Mapping[str, Any],
    *,
    table: str,
    current_sample: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    artifact_paths: Mapping[str, Path],
) -> set[str]:
    formula_id = str(proof["formula_id"])
    expected_formula = _expected_formula_for_sample(table, current_sample)
    if formula_id != expected_formula:
        raise ValueError(
            f"pairing-v3 formula mismatch: expected={expected_formula}, observed={formula_id}"
        )
    if str(proof["expression"]) != _FORMULA_EXPRESSIONS[formula_id]:
        raise ValueError("pairing-v3 formula expression does not match the contract")
    raw_operands = proof["operands"]
    if not isinstance(raw_operands, list):
        raise ValueError("pairing-v3 formula operands must be a list")
    operands: dict[str, tuple[float, float, float]] = {}
    for raw_operand in raw_operands:
        if not isinstance(raw_operand, dict):
            raise ValueError("pairing-v3 formula operand must be an object")
        name = str(raw_operand["name"])
        if name in operands:
            raise ValueError(f"pairing-v3 formula contains duplicate operand: {name}")
        evidence_id = str(raw_operand["evidence_id"])
        artifact = artifacts.get(evidence_id)
        if artifact is None:
            raise ValueError(f"pairing-v3 formula references unknown artifact: {evidence_id}")
        if artifact["source_kind"] not in {
            "official_exchange_disclosure",
            "issuer_xbrl",
        }:
            raise ValueError(
                f"pairing-v3 formula operand {name} requires original issuer disclosure evidence"
            )
        is_derived_event_multiplier = (
            formula_id == "hfq_event_multiplier_product_v1"
            and name.startswith("event_multiplier_")
            and str(raw_operand["line_item"]) == "derived_event_multiplier"
            and isinstance(raw_operand.get("event"), dict)
        )
        if (
            not is_derived_event_multiplier
            and str(raw_operand["line_item"]) not in artifact["actual_fields"]
        ):
            raise ValueError(f"pairing-v3 operand {name} line item is absent from its artifact")
        if artifact["missing_state"] != "present":
            raise ValueError(f"pairing-v3 operand {name} is not present")
        operands[name] = _operand_interval(raw_operand)
    expected_operands = _FORMULA_OPERANDS.get(formula_id)
    if expected_operands is not None and set(operands) != set(expected_operands):
        raise ValueError(
            f"pairing-v3 formula operands do not match {formula_id}: "
            f"expected={sorted(expected_operands)}, observed={sorted(operands)}"
        )
    if formula_id != "hfq_event_multiplier_product_v1":
        operand_units = {str(item["unit"]) for item in raw_operands}
        if len(operand_units) != 1:
            raise ValueError("financial formula operands must use one normalized monetary unit")
    if formula_id == "hfq_event_multiplier_product_v1":
        if not operands or any(not name.startswith("event_multiplier_") for name in operands):
            raise ValueError("HFQ proof requires named event_multiplier_N operands")
        event_window = proof["event_window"]
        if not isinstance(event_window, dict) or event_window.get("complete") is not True:
            raise ValueError("HFQ proof requires a complete company-action event window")
        if str(event_window["start_date"]) != str(current_sample["trade_date"]):
            raise ValueError("HFQ event window must start at the frozen sample date")
        if str(event_window["end_date"]) != str(current_sample["adj_anchor_date"]):
            raise ValueError("HFQ event window must end at the frozen anchor date")
        if event_window["taxonomy_version"] != ADJUSTMENT_EVENT_TAXONOMY_VERSION:
            raise ValueError("HFQ event taxonomy version differs")
        classification_summary = event_window["classification_summary"]
        if not isinstance(classification_summary, dict):
            raise ValueError("HFQ event classification summary is invalid")
        inventory_evidence_ids = {str(item) for item in event_window["inventory_evidence_ids"]}
        if str(event_window["symbol"]) != str(current_sample["symbol"]):
            raise ValueError("HFQ event inventory symbol does not match the frozen sample")
        raw_pages = event_window["pages"]
        if not isinstance(raw_pages, list):
            raise ValueError("HFQ event inventory pages must be a list")
        page_count = int(event_window["page_count"])
        page_numbers = [int(page["page_number"]) for page in raw_pages]
        if page_count != len(raw_pages) or page_numbers != list(range(1, page_count + 1)):
            raise ValueError("HFQ event inventory page numbers must be complete and consecutive")
        page_evidence_ids = {str(page["evidence_id"]) for page in raw_pages}
        if inventory_evidence_ids != page_evidence_ids:
            raise ValueError("HFQ event inventory evidence ids do not exactly cover all pages")
        total_reported_records = sum(int(page["reported_records"]) for page in raw_pages)
        if total_reported_records != int(
            event_window["total_reported_records"]
        ) or total_reported_records != len(raw_operands):
            raise ValueError("HFQ event inventory record totals do not match enumerated events")
        raw_total_records = int(event_window["raw_total_records"])
        if sum(int(page["raw_response_records"]) for page in raw_pages) != raw_total_records:
            raise ValueError(
                "HFQ event inventory raw page counts do not cover the reported full result set"
            )
        seen_inventory_record_ids: set[str] = set()
        classified_action_paths: set[str] = set()
        classification_records: list[dict[str, Any]] = []
        for page_metadata in raw_pages:
            evidence_id = str(page_metadata["evidence_id"])
            artifact = artifacts.get(evidence_id)
            if artifact is None:
                raise ValueError("HFQ event inventory references an unknown artifact")
            if (
                artifact["source_kind"] != "official_exchange_disclosure"
                or artifact["missing_state"] != "present"
            ):
                raise ValueError(
                    "HFQ event inventory requires present official-exchange disclosure artifacts"
                )
            request_parameters = artifact["request_parameters"]
            original_request = request_parameters.get("original_request")
            requested_symbol = str(request_parameters.get("symbol") or "")
            requested_start = str(request_parameters.get("start_date") or "")
            requested_end = str(request_parameters.get("end_date") or "")
            if (
                requested_symbol != str(current_sample["symbol"])
                or requested_start != str(event_window["start_date"])
                or requested_end != str(event_window["end_date"])
                or not isinstance(original_request, dict)
            ):
                raise ValueError(
                    "HFQ inventory artifact request is not the exact frozen symbol/window"
                )
            page_number = int(page_metadata["page_number"])
            if int(request_parameters.get("page_number", 0)) != page_number:
                raise ValueError(
                    "HFQ inventory artifact page parameter does not match page metadata"
                )
            validate_unfiltered_inventory_request(
                original_request,
                symbol=str(current_sample["symbol"]),
                start_date=str(event_window["start_date"]),
                end_date=str(event_window["end_date"]),
                page_number=page_number,
            )
            if "company_action_inventory_records" not in artifact["actual_fields"]:
                raise ValueError("HFQ inventory artifact does not expose its reported records")
            observed_total, inventory_records = _inventory_page(
                artifact_paths[evidence_id],
                symbol=str(current_sample["symbol"]),
            )
            if observed_total != raw_total_records or len(inventory_records) != int(
                page_metadata["raw_response_records"]
            ):
                raise ValueError("HFQ inventory raw response count differs from page metadata")
            page_classifications = {
                "implemented_adjustment_event": 0,
                "not_factor_adjustment": 0,
                "not_adjustment_related": 0,
                "unknown_adjustment_candidate": 0,
            }
            for record_id, title, document_path, publish_date in inventory_records:
                if record_id in seen_inventory_record_ids:
                    raise ValueError("HFQ inventory contains a duplicate announcement id")
                seen_inventory_record_ids.add(record_id)
                if not (
                    str(event_window["start_date"]) <= publish_date <= str(event_window["end_date"])
                ):
                    raise ValueError("HFQ inventory contains an out-of-window announcement")
                category, classification = classify_adjustment_announcement_title(title)
                page_classifications[classification] += 1
                classification_records.append(
                    {
                        "page_number": page_number,
                        "record_id": record_id,
                        "publish_date": publish_date,
                        "title": title,
                        "document_path": document_path,
                        "category": category,
                        "classification": classification,
                    }
                )
                if classification == "implemented_adjustment_event":
                    classified_action_paths.add(document_path)
            if (
                page_classifications["implemented_adjustment_event"]
                != int(page_metadata["reported_records"])
                or page_classifications["not_factor_adjustment"]
                != int(page_metadata["not_factor_adjustment_records"])
                or page_classifications["not_adjustment_related"]
                != int(page_metadata["not_adjustment_related_records"])
                or page_classifications["unknown_adjustment_candidate"]
                != int(page_metadata["unknown_adjustment_candidate_records"])
                or page_classifications["unknown_adjustment_candidate"] != 0
            ):
                raise ValueError("HFQ inventory classified action count differs from metadata")
        if len(seen_inventory_record_ids) != raw_total_records:
            raise ValueError("HFQ inventory pagination contains duplicate or missing rows")
        observed_classification_summary = {
            "implemented_adjustment_event": sum(
                int(page["reported_records"]) for page in raw_pages
            ),
            "not_factor_adjustment": sum(
                int(page["not_factor_adjustment_records"]) for page in raw_pages
            ),
            "not_adjustment_related": sum(
                int(page["not_adjustment_related_records"]) for page in raw_pages
            ),
            "unknown_adjustment_candidate": sum(
                int(page["unknown_adjustment_candidate_records"]) for page in raw_pages
            ),
        }
        if observed_classification_summary != classification_summary:
            raise ValueError("HFQ inventory classification summary differs from raw pages")
        if canonical_sha256(classification_records) != str(event_window["classification_sha256"]):
            raise ValueError("HFQ inventory classification digest differs from raw pages")
        pre_close_evidence_ids: set[str] = set()
        price_tick_evidence_ids: set[str] = set()
        reference_price_evidence_ids: set[str] = set()
        expected_event_contract = _FROZEN_DAILY_EVENTS.get(
            (
                str(current_sample.get("symbol") or ""),
                str(current_sample.get("trade_date") or ""),
            )
        )
        if expected_event_contract is None:
            raise ValueError("HFQ sample is not one of the frozen daily keys")
        if [str(item["name"]) for item in raw_operands] != [
            f"event_multiplier_{index}" for index in range(1, len(expected_event_contract) + 1)
        ]:
            raise ValueError("HFQ event operands do not exactly enumerate the frozen events")
        if len(raw_operands) != len(expected_event_contract):
            raise ValueError("HFQ proof event count differs from the frozen event contract")
        for raw_operand, expected_event in zip(
            raw_operands,
            expected_event_contract,
            strict=True,
        ):
            event = raw_operand["event"]
            if not isinstance(event, dict):
                raise ValueError("HFQ event multiplier requires structured event proof")
            if str(event["announcement_evidence_id"]) != str(raw_operand["evidence_id"]):
                raise ValueError("HFQ event operand must bind its official announcement artifact")
            _validate_event_formula(
                event,
                operand_value=operands[str(raw_operand["name"])][0],
                event_window=event_window,
                artifacts=artifacts,
                artifact_paths=artifact_paths,
            )
            pre_close_evidence_ids.add(str(event["pre_close_evidence_id"]))
            price_tick_evidence_ids.add(str(event["price_tick_evidence_id"]))
            if event["reference_price_evidence_id"] is not None:
                reference_price_evidence_ids.add(str(event["reference_price_evidence_id"]))
            announcement_artifact = artifacts[str(event["announcement_evidence_id"])]
            announcement_path = urlparse(
                str(announcement_artifact["request_parameters"].get("source_url") or "")
            ).path
            if not announcement_path or not any(
                announcement_path.endswith(classified_path)
                or classified_path.endswith(announcement_path)
                for classified_path in classified_action_paths
            ):
                raise ValueError("HFQ action PDF is not bound to the complete raw inventory")
            expected_date, expected_cash, expected_shares, expected_pre_close = expected_event
            expected_parameters = {
                "event_type": "cash_dividend",
                "ex_date": expected_date,
                "event_formula_id": (
                    "official_reference_price_ratio_v1"
                    if (
                        str(current_sample.get("symbol") or ""),
                        expected_date,
                    )
                    in _OFFICIAL_EX_REFERENCE_PRICES
                    else "cash_share_price_grid_v1"
                ),
                "pre_close": expected_pre_close,
                "cash_dividend_per_share": expected_cash,
                "share_distribution_per_share": expected_shares,
                "price_tick": 0.01,
            }
            for parameter_name, expected_value in expected_parameters.items():
                observed_value = event[parameter_name]
                if isinstance(expected_value, float):
                    if not _same_number(float(observed_value), expected_value):
                        raise ValueError(
                            f"HFQ event parameter {parameter_name} differs from the frozen contract"
                        )
                elif observed_value != expected_value:
                    raise ValueError(
                        f"HFQ event parameter {parameter_name} differs from the frozen contract"
                    )
        if len(classified_action_paths) != len(expected_event_contract):
            raise ValueError("HFQ raw inventory action set differs from the frozen events")
    elif proof["event_window"] is not None:
        raise ValueError("financial formula proof must not carry an event window")
    elif any(raw_operand["event"] is not None for raw_operand in raw_operands):
        raise ValueError("financial formula operands must not carry event proof")
    else:
        inventory_evidence_ids = set()

    if formula_id != "hfq_event_multiplier_product_v1":
        financial_key = (
            str(current_sample.get("symbol") or ""),
            str(current_sample.get("report_period") or ""),
            str(current_sample.get("metric") or ""),
        )
        expected_financial_operands = _FROZEN_FINANCIAL_OPERANDS.get(financial_key)
        if expected_financial_operands is None:
            raise ValueError("financial formula sample is not one of the frozen contracts")
        for raw_operand in raw_operands:
            operand_name = str(raw_operand["name"])
            expected_operand = expected_financial_operands.get(operand_name)
            if expected_operand is None:
                raise ValueError(
                    "financial formula contains an operand outside the frozen contract"
                )
            expected_value, expected_line_item = expected_operand
            if (
                not _same_number(float(raw_operand["value"]), expected_value)
                or str(raw_operand["line_item"]) != expected_line_item
                or not _same_number(
                    float(raw_operand["disclosure_precision"]["quantum"]),
                    0.01,
                )
            ):
                raise ValueError(
                    f"financial operand {operand_name} differs from the "
                    "frozen original-report contract"
                )

    point, lower, upper = _formula_result(formula_id, operands)
    supplied_result = proof["result"]
    supplied = (
        _finite_number(supplied_result["value"], label="formula result value"),
        _finite_number(supplied_result["lower"], label="formula result lower"),
        _finite_number(supplied_result["upper"], label="formula result upper"),
    )
    for observed, recomputed in zip(supplied, (point, lower, upper), strict=True):
        if not _same_number(observed, recomputed):
            raise ValueError("pairing-v3 formula result was not computed from operands")
    if (lower > point and not _same_number(lower, point)) or (
        point > upper and not _same_number(point, upper)
    ):
        raise ValueError("pairing-v3 formula result has an invalid interval")

    quantum = float(proof["local_storage_quantum"])
    if table == "daily_bars":
        target = _finite_number(
            current_sample["adj_factor"],
            label="frozen local adjustment factor",
        )
        anchor = _finite_number(
            current_sample["adj_anchor_factor"],
            label="frozen local adjustment anchor",
        )
        target_interval = (target - quantum / 2.0, target + quantum / 2.0)
        anchor_interval = (anchor - quantum / 2.0, anchor + quantum / 2.0)
        implied_lower, implied_upper = _div_interval(anchor_interval, target_interval)
        if not implied_lower <= point <= implied_upper:
            raise ValueError(
                "pairing-v3 HFQ event multiplier is outside the frozen six-decimal storage interval"
            )
        return (
            set(inventory_evidence_ids)
            | {str(operand["evidence_id"]) for operand in raw_operands}
            | pre_close_evidence_ids
            | price_tick_evidence_ids
            | reference_price_evidence_ids
        )

    local_value = current_sample.get("value")
    if local_value is None:
        raise ValueError("formula_match requires a non-null frozen local value")
    local = _finite_number(local_value, label="frozen local financial value")
    local_interval = (local - quantum / 2.0, local + quantum / 2.0)
    if upper < local_interval[0] or lower > local_interval[1]:
        raise ValueError(
            "pairing-v3 financial formula interval does not overlap the frozen "
            "six-decimal storage interval"
        )
    return {str(operand["evidence_id"]) for operand in raw_operands}


def _validate_expected_unavailable(
    proof: Mapping[str, Any],
    *,
    table: str,
    current_sample: Mapping[str, Any],
    evidence_ids: set[str],
    artifacts: Mapping[str, Mapping[str, Any]],
) -> None:
    if table != "financial_indicators" or current_sample.get("metric") != "revenue_yoy":
        raise ValueError("expected_unavailable is allowed only for revenue_yoy samples")
    report_period = str(current_sample.get("report_period") or "")
    if len(report_period) != 6 or report_period[4] != "Q":
        raise ValueError("expected_unavailable sample has an invalid report period")
    quarter = int(report_period[5])
    if quarter in (2, 4) or int(proof["observed_quarter"]) != quarter:
        raise ValueError("expected_unavailable contradicts the frozen Q2/Q4 cadence")
    if current_sample.get("value") is not None:
        raise ValueError("expected_unavailable requires a null frozen local value")
    try:
        payload = json.loads(str(current_sample.get("payload") or ""))
    except json.JSONDecodeError as exc:
        raise ValueError("expected_unavailable requires a valid frozen provider payload") from exc
    if not isinstance(payload, dict):
        raise ValueError("expected_unavailable provider payload must be an object")
    raw_source_value = payload.get("raw_source_value")
    if (
        payload.get("unavailable_reason") != "missing_current_revenue"
        or payload.get("source_field") != "derived.profit.MBRevenue_yoy"
        or payload.get("main_business_revenue") is not None
        or not isinstance(raw_source_value, dict)
        or raw_source_value.get("current_main_business_revenue") is not None
    ):
        raise ValueError("expected_unavailable does not match the frozen provider payload")
    if proof["missing_state"] not in {"field_absent", "field_null"}:
        raise ValueError("expected_unavailable must distinguish its missing state")
    examined = proof["examined_line_items"]
    if not isinstance(examined, list):
        raise ValueError("expected_unavailable examined_line_items must be a list")
    examined_names = {str(item["name"]) for item in examined if isinstance(item, dict)}
    if not {"营业收入", "营业总收入"}.issubset(examined_names):
        raise ValueError("expected_unavailable must explicitly reject 营业收入 and 营业总收入")
    if any(
        artifacts[evidence_id]["source_kind"] not in {"official_exchange_disclosure", "issuer_xbrl"}
        for evidence_id in evidence_ids
    ):
        raise ValueError("expected_unavailable requires original issuer disclosure evidence")
    if any(
        artifacts[evidence_id]["missing_state"] not in {"field_absent", "field_null"}
        for evidence_id in evidence_ids
    ):
        raise ValueError("expected_unavailable evidence cannot claim the exact field is present")
    artifact_fields = {
        str(field)
        for evidence_id in evidence_ids
        for field in artifacts[evidence_id]["actual_fields"]
    }
    if not examined_names.issubset(artifact_fields):
        raise ValueError(
            "expected_unavailable examined line items are not bound to the "
            "content-addressed full-report artifacts"
        )


def _validate_preserved_trial_checks(
    *,
    table: str,
    checked_values: object,
    trial_sample: Mapping[str, Any],
    current_sample: Mapping[str, Any],
) -> None:
    # canonical comparison: Python `==` would let bool/int JSON type swaps
    # (1 vs true) survive the preservation check.
    if canonical_sha256(checked_values) != canonical_sha256(
        trial_sample.get("checked_values")
    ):
        raise ValueError("formula_match must preserve the frozen trial checked_values")
    if not isinstance(checked_values, dict):
        raise ValueError("frozen trial checked_values must be an object")
    required_pass_fields = (
        ("close", "source", "adj_source") if table == "daily_bars" else ("source", "available_time")
    )
    for field in required_pass_fields:
        check = checked_values.get(field)
        if not isinstance(check, dict) or check.get("pass") is not True:
            raise ValueError(f"formula_match requires frozen trial {table}.{field} pass=true")
        if check.get("local_value") != current_sample.get(field):
            raise ValueError(f"formula_match frozen trial {table}.{field} local value changed")
    if table != "daily_bars":
        return
    close_check = checked_values["close"]
    local_close = _finite_number(
        close_check["local_value"],
        label="frozen trial close local_value",
    )
    external_close = _finite_number(
        close_check["external_value"],
        label="frozen trial close external_value",
    )
    expected_tolerance = max(
        0.01,
        0.0001 * max(abs(local_close), abs(external_close)),
    )
    observed_tolerance = _finite_number(
        close_check.get("tolerance"),
        label="frozen trial close tolerance",
    )
    if not _same_number(observed_tolerance, expected_tolerance):
        raise ValueError("formula_match changed the frozen close tolerance policy")
    if abs(local_close - external_close) > expected_tolerance:
        raise ValueError("formula_match frozen close values exceed tolerance")


def _validate_valuation_raw_response(
    sample: Mapping[str, Any],
    *,
    evidence_ids: set[str],
    artifacts: Mapping[str, Mapping[str, Any]],
    artifact_paths: Mapping[str, Path],
) -> None:
    if len(evidence_ids) != 1:
        raise ValueError("valuation numeric_match requires one raw response")
    evidence_id = next(iter(evidence_ids))
    artifact = artifacts[evidence_id]
    if artifact["source_kind"] != "audited_external_response":
        raise ValueError("valuation numeric_match requires audited external data")
    key = sample["key"]
    if not isinstance(key, dict):
        raise ValueError("valuation numeric_match key must be an object")
    symbol = str(key["symbol"])
    trade_date = str(key["trade_date"])
    request_parameters = artifact["request_parameters"]
    if (
        str(request_parameters.get("symbol") or "") != symbol
        or str(request_parameters.get("trade_date") or "") != trade_date
    ):
        raise ValueError("valuation response request key differs from the sample")
    response = _strict_json_object_file(
        artifact_paths[evidence_id],
        label="valuation raw response",
    )
    response_result = response.get("result")
    raw_rows = response_result.get("data") if isinstance(response_result, dict) else None
    if not isinstance(raw_rows, list):
        raise ValueError("valuation raw response rows are absent")
    matching_rows = [
        row
        for row in raw_rows
        if isinstance(row, dict)
        and str(row.get("SECURITY_CODE") or "") == symbol
        and str(row.get("TRADE_DATE") or "").startswith(trade_date)
    ]
    if len(matching_rows) != 1:
        raise ValueError("valuation raw response does not uniquely contain the frozen key")
    checked_values = sample["checked_values"]
    if not isinstance(checked_values, dict):
        raise ValueError("valuation checked_values must be an object")
    target_row = matching_rows[0]
    for check_name, raw_name in (
        ("pe_ttm", "PE_TTM"),
        ("pb_mrq", "PB_MRQ"),
        ("ps_ttm", "PS_TTM"),
    ):
        check = checked_values.get(check_name)
        if not isinstance(check, dict) or not _same_number(
            _finite_number(
                target_row.get(raw_name),
                label=f"valuation raw {raw_name}",
            ),
            _finite_number(
                check.get("external_value"),
                label=f"valuation checked {check_name}",
            ),
        ):
            raise ValueError(f"valuation raw {raw_name} differs from checked external value")


def validate_pairing_v3(
    document: Mapping[str, Any],
    *,
    evidence_path: Path,
    pit_samples: Mapping[str, Any],
    require_signature: bool = True,
) -> dict[str, Any]:
    """Validate a frozen, content-addressed pairing-v3 package without network I/O."""

    errors = sorted(
        Draft202012Validator(PAIRING_V3_SCHEMA).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise ValueError(
            f"external PIT pairing-v3 schema violation at {location}: {errors[0].message}"
        )
    if str(pit_samples.get("manifest_sha256")) != FROZEN_MANIFEST_SHA256:
        raise ValueError("pairing-v3 is bound to the frozen final S6 manifest")
    if int(pit_samples.get("seed", -1)) != FROZEN_SEED:
        raise ValueError("pairing-v3 seed does not match the frozen final manifest")
    if int(pit_samples.get("sample_size_per_table", -1)) != FROZEN_SAMPLE_SIZE_PER_TABLE:
        raise ValueError("pairing-v3 sample size does not match the frozen final manifest")

    bundle_root = evidence_path.expanduser().resolve().parent
    final_trial_spec = document["final_trial"]
    if not isinstance(final_trial_spec, dict):
        raise ValueError("pairing-v3 final_trial must be an object")
    trial_path = _safe_bundle_file(
        bundle_root,
        final_trial_spec["relative_path"],
        label="pairing-v3 final_trial relative_path",
    )
    observed_trial_sha256, _ = _file_sha256(trial_path)
    if observed_trial_sha256 != FROZEN_FINAL_TRIAL_SHA256:
        raise ValueError("pairing-v3 final trial SHA-256 mismatch")
    trial = _strict_json_object_file(
        trial_path,
        label="pairing-v3 final trial",
    )
    if (
        trial.get("pit_manifest_sha256") != FROZEN_MANIFEST_SHA256
        or trial.get("seed") != FROZEN_SEED
        or trial.get("sample_size_per_table") != FROZEN_SAMPLE_SIZE_PER_TABLE
    ):
        raise ValueError("pairing-v3 final trial does not match its frozen binding")
    trial_samples = trial.get("samples")
    if not isinstance(trial_samples, list) or len(trial_samples) != FROZEN_SAMPLE_COUNT:
        raise ValueError("pairing-v3 final trial must contain the frozen 15 samples")

    def identity(table: object, key: object) -> tuple[str, tuple[tuple[str, str], ...]]:
        table_text = str(table)
        if not isinstance(key, dict):
            raise ValueError("pairing-v3 sample key must be an object")
        fields = {
            "daily_bars": ("symbol", "trade_date"),
            "financial_indicators": ("symbol", "report_period", "metric"),
            "valuation_daily": ("symbol", "trade_date"),
        }.get(table_text)
        if fields is None:
            raise ValueError(f"unsupported pairing-v3 table: {table_text}")
        return table_text, tuple((field, str(key.get(field) or "")) for field in fields)

    trial_by_identity: dict[tuple[str, tuple[tuple[str, str], ...]], Mapping[str, Any]] = {}
    for trial_sample in trial_samples:
        if not isinstance(trial_sample, dict):
            raise ValueError("pairing-v3 final trial sample must be an object")
        sample_identity = identity(trial_sample.get("table"), trial_sample.get("key"))
        if sample_identity in trial_by_identity:
            raise ValueError("pairing-v3 final trial contains duplicate sample keys")
        trial_by_identity[sample_identity] = trial_sample

    current_by_identity: dict[tuple[str, tuple[tuple[str, str], ...]], Mapping[str, Any]] = {}
    for table, source_key in (
        ("daily_bars", "daily_bars_with_adj"),
        ("financial_indicators", "financial_indicators"),
        ("valuation_daily", "valuation_daily"),
    ):
        raw_current = pit_samples.get(source_key)
        if not isinstance(raw_current, list):
            raise ValueError(f"pairing-v3 local manifest {source_key} must be a list")
        for current_sample in raw_current:
            if not isinstance(current_sample, dict):
                raise ValueError("pairing-v3 local manifest sample must be an object")
            sample_identity = identity(table, current_sample)
            if sample_identity in current_by_identity:
                raise ValueError("pairing-v3 local manifest contains duplicate sample keys")
            current_by_identity[sample_identity] = current_sample
    if set(current_by_identity) != set(trial_by_identity):
        raise ValueError("pairing-v3 final trial does not exactly cover the frozen manifest keys")

    artifacts, artifact_paths = _validate_artifacts(
        document,
        bundle_root=bundle_root,
    )
    raw_samples = document["samples"]
    if not isinstance(raw_samples, list):
        raise ValueError("pairing-v3 samples must be a list")
    supplied: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    verdict_counts = {
        "numeric_match": 0,
        "formula_match": 0,
        "expected_unavailable": 0,
    }
    for sample in raw_samples:
        if not isinstance(sample, dict):
            raise ValueError("pairing-v3 sample must be an object")
        verdict = str(sample["verdict"])
        if verdict in _FORBIDDEN_VERDICTS:
            raise ValueError(f"pairing-v3 forbidden verdict: {verdict}")
        sample_identity = identity(sample["table"], sample["key"])
        if sample_identity in supplied:
            raise ValueError("pairing-v3 contains duplicate sample keys")
        supplied.add(sample_identity)
        trial_sample = trial_by_identity.get(sample_identity)
        current_sample = current_by_identity.get(sample_identity)
        if trial_sample is None or current_sample is None:
            raise ValueError("pairing-v3 contains a key outside the frozen 15 samples")
        if str(sample["trial_sample_sha256"]) != canonical_sha256(trial_sample):
            raise ValueError("pairing-v3 trial sample SHA-256 mismatch")
        evidence_ids = {str(item) for item in sample["evidence_ids"]}
        if any(evidence_id not in artifacts for evidence_id in evidence_ids):
            raise ValueError("pairing-v3 sample references an unknown artifact")

        table = sample_identity[0]
        trial_verdict = str(trial_sample.get("verdict") or "")
        if verdict == "numeric_match":
            if table != "valuation_daily" or trial_verdict != "match":
                raise ValueError("numeric_match is reserved for the five frozen valuation matches")
            if set(sample) != {
                "table",
                "key",
                "verdict",
                "trial_sample_sha256",
                "evidence_ids",
                "checked_values",
            }:
                raise ValueError("numeric_match contains incompatible proof fields")
            if canonical_sha256(sample["checked_values"]) != canonical_sha256(
                trial_sample.get("checked_values")
            ):
                raise ValueError("numeric_match must preserve the frozen trial checked_values")
            if any(artifacts[item]["missing_state"] != "present" for item in evidence_ids):
                raise ValueError("numeric_match evidence must contain present values")
            _validate_valuation_raw_response(
                sample,
                evidence_ids=evidence_ids,
                artifacts=artifacts,
                artifact_paths=artifact_paths,
            )
        elif verdict == "formula_match":
            if table == "valuation_daily" or trial_verdict not in {"match", "mismatch"}:
                raise ValueError("formula_match is incompatible with the frozen trial")
            if set(sample) != {
                "table",
                "key",
                "verdict",
                "trial_sample_sha256",
                "evidence_ids",
                "checked_values",
                "formula_proof",
            }:
                raise ValueError("formula_match contains incompatible proof fields")
            _validate_preserved_trial_checks(
                table=table,
                checked_values=sample["checked_values"],
                trial_sample=trial_sample,
                current_sample=current_sample,
            )
            proof_ids = _validate_formula_proof(
                sample["formula_proof"],
                table=table,
                current_sample=current_sample,
                artifacts=artifacts,
                artifact_paths=artifact_paths,
            )
            if proof_ids != evidence_ids:
                raise ValueError(
                    "formula_match evidence_ids must exactly cover its operands and event inventory"
                )
        elif verdict == "expected_unavailable":
            if trial_verdict != "unavailable":
                raise ValueError("expected_unavailable must bind a frozen unavailable trial sample")
            if set(sample) != {
                "table",
                "key",
                "verdict",
                "trial_sample_sha256",
                "evidence_ids",
                "unavailable_proof",
            }:
                raise ValueError("expected_unavailable contains incompatible proof fields")
            _validate_expected_unavailable(
                sample["unavailable_proof"],
                table=table,
                current_sample=current_sample,
                evidence_ids=evidence_ids,
                artifacts=artifacts,
            )
        else:
            raise ValueError(f"pairing-v3 unsupported verdict: {verdict}")
        verdict_counts[verdict] += 1

    if supplied != set(current_by_identity):
        raise ValueError("pairing-v3 does not exactly cover the frozen 15 sample keys")
    if verdict_counts != {
        "numeric_match": 5,
        "formula_match": 8,
        "expected_unavailable": 2,
    }:
        raise ValueError(
            "pairing-v3 verdict distribution must be "
            "5 numeric_match / 8 formula_match / 2 expected_unavailable"
        )
    summary = document["summary"]
    if not isinstance(summary, dict):
        raise ValueError("pairing-v3 summary must be an object")
    for verdict, count in verdict_counts.items():
        if int(summary[verdict]) != count:
            raise ValueError("pairing-v3 summary does not match sample verdicts")
    approved = document["approved"]
    reviewer_role = str(document["reviewer_role"])
    reviewed_at_value = document["reviewed_at"]
    if require_signature:
        if approved is not True:
            raise ValueError("pairing-v3 accepted evidence requires approved=true")
        if reviewer_role != reviewer_role.strip():
            raise ValueError(
                "pairing-v3 reviewer_role must not contain surrounding whitespace"
            )
        reviewer_profile = APPROVED_REVIEWER_PROFILES.get(reviewer_role)
        if reviewer_profile is None:
            raise ValueError(
                "pairing-v3 reviewer_role is not an exact approved reviewer profile"
            )
        reviewer_type, signature_status = reviewer_profile
        reviewed_at = str(reviewed_at_value).strip()
        _strict_datetime(reviewed_at, label="pairing-v3 reviewed_at")
    else:
        if approved is not False or reviewer_role != "pending" or reviewed_at_value is not None:
            raise ValueError(
                "pairing-v3 unsigned candidate requires approved=false, "
                "reviewer_role=pending, reviewed_at=null"
            )
        reviewed_at = ""
        reviewer_type = "pending"
        signature_status = "unsigned_candidate"
    return {
        "schema_version": PAIRING_V3_SCHEMA_VERSION,
        "adjudication_contract": ADJUDICATION_CONTRACT_VERSION,
        "final_trial_sha256": FROZEN_FINAL_TRIAL_SHA256,
        "reviewer_type": reviewer_type,
        "reviewer_role": reviewer_role,
        "reviewed_at": reviewed_at,
        "sample_count": FROZEN_SAMPLE_COUNT,
        "numeric_match": verdict_counts["numeric_match"],
        "formula_match": verdict_counts["formula_match"],
        "expected_unavailable": verdict_counts["expected_unavailable"],
        "signature_status": signature_status,
    }


def validate_pairing_v3_candidate(
    document: Mapping[str, Any],
    *,
    evidence_path: Path,
    pit_samples: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate 15/15 machine proofs while deliberately withholding acceptance."""

    return validate_pairing_v3(
        document,
        evidence_path=evidence_path,
        pit_samples=pit_samples,
        require_signature=False,
    )


def validate_ai_review_attestation(
    attestation_path: Path,
    *,
    signed_candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the separately anchored Claude Code review without human semantics."""

    resolved = attestation_path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"AI review attestation not found: {resolved}")
    digest, size = _file_sha256(resolved)
    if size > MAX_AI_REVIEW_ATTESTATION_BYTES:
        raise ValueError(
            "AI review attestation exceeds "
            f"{MAX_AI_REVIEW_ATTESTATION_BYTES} bytes"
        )
    if digest != AI_REVIEW_ATTESTATION_SHA256:
        raise ValueError(
            "AI review attestation does not match the frozen whole-file SHA-256"
        )
    document = _strict_json_object_file(
        resolved,
        label="AI review attestation",
    )
    required_keys = {
        "blockers",
        "candidate_canonical_sha256",
        "candidate_file_sha256",
        "checks",
        "checksum_layers",
        "contract_deviation",
        "decision",
        "final_trial_sha256",
        "final_verdict",
        "frozen_preflight_sha256",
        "independence_disclosure",
        "input_zip",
        "input_zip_sha256",
        "input_zip_sidecar_verified",
        "machine_validation_sha256",
        "non_blocking_observations",
        "pit_manifest_sha256",
        "presign_report_sha256",
        "reviewed_at",
        "reviewer_model",
        "reviewer_product",
        "reviewer_role",
        "reviewer_type",
        "schema_version",
        "state_assertions",
        "verdict_distribution",
    }
    if set(document) != required_keys:
        raise ValueError("AI review attestation top-level fields are not exact")
    exact_values = {
        "schema_version": AI_REVIEW_ATTESTATION_SCHEMA_VERSION,
        "decision": "approved",
        "final_verdict": "APPROVE_AS_INDEPENDENT_AI_REVIEWER",
        "reviewer_type": "ai",
        "reviewer_role": AI_REVIEWER_ROLE,
        "reviewer_product": AI_REVIEWER_PRODUCT,
        "reviewer_model": AI_REVIEWER_MODEL,
        "input_zip_sha256": FROZEN_PAIRING_V3_INPUT_ZIP_SHA256,
        "candidate_file_sha256": FROZEN_PAIRING_V3_UNSIGNED_FILE_SHA256,
        "candidate_canonical_sha256": (
            FROZEN_PAIRING_V3_UNSIGNED_CANONICAL_SHA256
        ),
        "machine_validation_sha256": (
            FROZEN_PAIRING_V3_MACHINE_VALIDATION_SHA256
        ),
        "presign_report_sha256": FROZEN_PAIRING_V3_PRESIGN_REPORT_SHA256,
        "frozen_preflight_sha256": FROZEN_PREFLIGHT_SHA256,
        "pit_manifest_sha256": FROZEN_MANIFEST_SHA256,
        "final_trial_sha256": FROZEN_FINAL_TRIAL_SHA256,
    }
    for field, expected in exact_values.items():
        if document[field] != expected:
            raise ValueError(f"AI review attestation {field} mismatch")
    if document["blockers"] != []:
        raise ValueError("AI review attestation must have zero blockers")
    expected_checks = {
        "candidate_remains_unsigned": True,
        "fixed_15_business_keys": True,
        "nested_checksums": True,
        "p0_1_official_reference": True,
        "p0_2_candidate_bound_presign": True,
        "p0_3_machine_invariants": True,
        "zip_integrity": True,
    }
    if document["checks"] != expected_checks:
        raise ValueError("AI review attestation checks are not an exact pass")
    if document["verdict_distribution"] != {
        "expected_unavailable": 2,
        "formula_match": 8,
        "numeric_match": 5,
    }:
        raise ValueError("AI review attestation verdict distribution mismatch")
    deviation = document["contract_deviation"]
    if deviation != {
        "authority": "explicit project owner instruction",
        "human_impersonation": False,
        "new_requirement": "independent AI architect review by Claude Code",
        "old_requirement": "independent human architect signature",
        "requires_release_gate_amendment": True,
    }:
        raise ValueError("AI review attestation governance deviation mismatch")
    state = document["state_assertions"]
    if state != {
        "candidate_modified": False,
        "candidate_top_level": {
            "approved": False,
            "reviewed_at": None,
            "reviewer_role": "pending",
        },
        "git_committed": False,
        "human_only_validator_modified_by_this_review": False,
        "production_trust_anchor_unset": True,
        "s6_status": "blocked",
        "s7_status": "not_started",
        "test_window": "sealed",
    }:
        raise ValueError("AI review attestation state assertions mismatch")
    if document["input_zip_sidecar_verified"] is not True:
        raise ValueError("AI review attestation did not verify the ZIP sidecar")
    if not str(document["independence_disclosure"]).strip():
        raise ValueError("AI review attestation must disclose reviewer independence")
    observations = document["non_blocking_observations"]
    if (
        not isinstance(observations, list)
        or not observations
        or any(not isinstance(item, str) or not item.strip() for item in observations)
    ):
        raise ValueError("AI review attestation observations must be non-empty text")
    expected_layers = {
        "SHA256SUMS": 279,
        "candidate/raw-source/SHA256SUMS": 205,
        (
            "candidate/raw-source/daily-bars/"
            "complete-unfiltered-announcement-inventory/SHA256SUMS"
        ): 64,
        (
            "candidate/raw-source/daily-bars/"
            "reference-evidence/001260/SHA256SUMS"
        ): 8,
        (
            "candidate/raw-source/daily-bars/"
            "rounding-evidence/600782/SHA256SUMS"
        ): 17,
        "candidate/raw-source/git-chain/SHA256SUMS": 23,
        "candidate/raw-source/price-tick-rules/SHA256SUMS": 29,
    }
    layers = document["checksum_layers"]
    if not isinstance(layers, dict) or set(layers) != set(expected_layers):
        raise ValueError("AI review attestation checksum layers are not exact")
    for layer_name, expected_entries in expected_layers.items():
        layer = layers[layer_name]
        if not isinstance(layer, dict):
            raise ValueError("AI review attestation checksum layer must be an object")
        if layer.get("entries") != expected_entries:
            raise ValueError("AI review attestation checksum entry count mismatch")
        for counter in ("missing", "mismatch", "duplicate"):
            if layer.get(counter) != 0:
                raise ValueError(
                    f"AI review attestation checksum {counter} must be zero"
                )
    root_layer = layers["SHA256SUMS"]
    if root_layer.get("uncovered_files") != 0 or root_layer.get("phantom_entries") != 0:
        raise ValueError("AI review attestation root checksum closure mismatch")
    reviewed_at = str(document["reviewed_at"])
    _strict_datetime(reviewed_at, label="AI review attestation reviewed_at")
    if signed_candidate.get("approved") is not True:
        raise ValueError("AI-reviewed signed candidate must set approved=true")
    if signed_candidate.get("reviewer_role") != AI_REVIEWER_ROLE:
        raise ValueError("AI-reviewed signed candidate reviewer_role mismatch")
    if signed_candidate.get("reviewed_at") != reviewed_at:
        raise ValueError("AI-reviewed signed candidate reviewed_at mismatch")
    if (
        normalized_unsigned_candidate_sha256(signed_candidate)
        != FROZEN_PAIRING_V3_UNSIGNED_CANONICAL_SHA256
    ):
        raise ValueError("AI-reviewed signed candidate is not the frozen candidate")
    return {
        "schema_version": AI_REVIEW_ATTESTATION_SCHEMA_VERSION,
        "sha256": digest,
        "bytes": size,
        "reviewer_type": "ai",
        "reviewer_role": AI_REVIEWER_ROLE,
        "reviewer_product": AI_REVIEWER_PRODUCT,
        "reviewer_model": AI_REVIEWER_MODEL,
        "reviewed_at": reviewed_at,
        "decision": "approved",
        "signature_status": INDEPENDENT_AI_SIGNATURE_STATUS,
        "governance_amendment": AI_REVIEW_GOVERNANCE_AMENDMENT_VERSION,
        "governance_amendment_sha256": (
            AI_REVIEW_GOVERNANCE_AMENDMENT_SHA256
        ),
    }
