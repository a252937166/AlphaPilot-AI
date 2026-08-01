from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

from alphapilot.backtest.external_pit_adjudication import (
    ADJUDICATION_CONTRACT_SHA256,
    ADJUDICATION_CONTRACT_VERSION,
    ADJUSTMENT_EVENT_TAXONOMY_VERSION,
    FROZEN_FINAL_TRIAL_SHA256,
    FROZEN_MANIFEST_SHA256,
    FROZEN_SAMPLE_SIZE_PER_TABLE,
    FROZEN_SEED,
    LOCAL_STORAGE_QUANTUM,
    PAIRING_V3_SCHEMA_VERSION,
    canonical_sha256,
    classify_adjustment_announcement_title,
    parse_official_inventory_page,
    validate_pairing_v3_candidate,
    validate_unfiltered_inventory_request,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREFLIGHT = ROOT / "docs/phase3/reports/P3.3-S6-final-preflight-20260731.json"
DEFAULT_FINAL_TRIAL = ROOT / "docs/phase3/reports/P3.3-S6-external-pit-final-trial-20260731.json"
DEFAULT_DB = ROOT / "data/alphapilot.db"
SOURCE_MANIFEST_NAME = "SOURCE-MANIFEST.json"
SOURCE_MANIFEST_SCHEMA_VERSION = "p3.3-s6-external-pit-source-bundle-v1"
GENERAL_SOURCE_MANIFEST_SCHEMA_VERSION = "p3.3-s6-pairing-v3-source-manifest-v1"
LOCAL_PRECLOSE_SCHEMA_VERSION = "p3.3-s6-frozen-local-preclose-v1"
MACHINE_VALIDATION_SCHEMA_VERSION = "p3.3-s6-pairing-v3-machine-validation-v1"
MAX_RAW_SOURCE_BYTES = 64 * 1024 * 1024
GENERAL_SOURCE_CHECKSUM_NAME = "SHA256SUMS"

_ARTIFACT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_FIELDS = {
    "id",
    "path",
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
}

_DAILY_KEYS = (
    ("001260", "2025-10-29"),
    ("600648", "2025-06-16"),
    ("600782", "2026-01-30"),
    ("000831", "2023-05-29"),
    ("001205", "2024-05-21"),
)
_FINANCIAL_KEYS = (
    ("000897", "2024Q3", "net_profit_yoy"),
    ("002012", "2018Q2", "net_profit_yoy"),
    ("601137", "2017Q1", "revenue_yoy"),
    ("300433", "2025Q3", "roe"),
    ("300662", "2026Q1", "revenue_yoy"),
)
_VALUATION_KEYS = (
    ("000565", "2022-11-21"),
    ("000690", "2019-12-25"),
    ("001914", "2026-03-05"),
    ("002186", "2024-09-06"),
    ("002191", "2025-09-03"),
)

# Architect-adjudicated company actions. The builder accepts no alternate event set.
_DAILY_EVENTS: dict[tuple[str, str], tuple[dict[str, Any], ...]] = {
    ("001260", "2025-10-29"): (
        {
            "ex_date": "2026-05-27",
            "event_type": "cash_dividend",
            "cash_dividend_per_share": 0.215,
            "share_distribution_per_share": 0.0,
            "price_tick": 0.01,
        },
    ),
    ("600648", "2025-06-16"): (
        {
            "ex_date": "2026-07-13",
            "event_type": "cash_dividend",
            "cash_dividend_per_share": 0.35,
            "share_distribution_per_share": 0.0,
            "price_tick": 0.01,
        },
    ),
    ("600782", "2026-01-30"): (
        {
            "ex_date": "2026-06-05",
            "event_type": "cash_dividend",
            "cash_dividend_per_share": 0.135,
            "share_distribution_per_share": 0.0,
            "price_tick": 0.01,
        },
    ),
    ("000831", "2023-05-29"): (
        {
            "ex_date": "2023-06-20",
            "event_type": "cash_dividend",
            "cash_dividend_per_share": 0.04,
            "share_distribution_per_share": 0.0,
            "price_tick": 0.01,
        },
        {
            "ex_date": "2024-06-25",
            "event_type": "cash_dividend",
            "cash_dividend_per_share": 0.08,
            "share_distribution_per_share": 0.0,
            "price_tick": 0.01,
        },
        {
            "ex_date": "2026-07-03",
            "event_type": "cash_dividend",
            "cash_dividend_per_share": 0.029,
            "share_distribution_per_share": 0.0,
            "price_tick": 0.01,
        },
    ),
    ("001205", "2024-05-21"): (
        {
            "ex_date": "2024-06-04",
            "event_type": "cash_dividend",
            "cash_dividend_per_share": 0.1186399,
            "share_distribution_per_share": 0.0,
            "price_tick": 0.01,
        },
        {
            "ex_date": "2025-06-06",
            "event_type": "cash_dividend",
            "cash_dividend_per_share": 0.1183647,
            "share_distribution_per_share": 0.0,
            "price_tick": 0.01,
        },
        {
            "ex_date": "2026-05-26",
            "event_type": "cash_dividend",
            "cash_dividend_per_share": 0.1479559,
            "share_distribution_per_share": 0.0,
            "price_tick": 0.01,
        },
    ),
}

# These are original issuer-report line items, not reconstructed target values.
_FINANCIAL_FORMULAS: dict[tuple[str, str, str], dict[str, Any]] = {
    ("000897", "2024Q3", "net_profit_yoy"): {
        "formula_id": "net_profit_yoy_v1",
        "expression": "(net_profit_t-net_profit_t_minus_4)/abs(net_profit_t_minus_4)",
        "operands": (
            ("net_profit_t", 368_514_961.65, "净利润（本期累计）"),
            (
                "net_profit_t_minus_4",
                494_218_221.65,
                "净利润（上年同期累计）",
            ),
        ),
    },
    ("002012", "2018Q2", "net_profit_yoy"): {
        "formula_id": "net_profit_yoy_v1",
        "expression": "(net_profit_t-net_profit_t_minus_4)/abs(net_profit_t_minus_4)",
        "operands": (
            ("net_profit_t", 15_888_115.75, "净利润（本期累计）"),
            (
                "net_profit_t_minus_4",
                26_571_199.10,
                "净利润（上年同期累计）",
            ),
        ),
    },
    ("300433", "2025Q3", "roe"): {
        "formula_id": "roe_average_parent_equity_v1",
        "expression": ("parent_net_profit_t/((opening_parent_equity+closing_parent_equity)/2)"),
        "operands": (
            ("parent_net_profit_t", 2_842_952_844.41, "归属于母公司所有者的净利润"),
            ("opening_parent_equity", 48_656_642_054.21, "归属于母公司所有者权益（期初）"),
            ("closing_parent_equity", 53_845_361_611.79, "归属于母公司所有者权益（期末）"),
        ),
    },
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build and machine-validate the frozen unsigned P3.3-S6 pairing-v3 "
            "candidate. This command has no network or signing path."
        )
    )
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--final-trial", type=Path, default=DEFAULT_FINAL_TRIAL)
    return parser


def _json_object(path: Path, *, label: str) -> dict[str, Any]:
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
        raise ValueError(f"{label} must be readable UTF-8 JSON: {path}") from exc
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


def _szse_dividend_table_target(
    path: Path,
    *,
    symbol: str,
    ex_date: str,
) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="gb18030")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("official SZSE ex-reference raw response encoding differs") from exc
    parser = _SZSEDividendTableParser()
    parser.feed(text)
    headers = " ".join(parser.headers)
    if "DPS" not in headers or "Ex-Price" not in headers or "Pre-Closing" not in headers:
        raise ValueError("official SZSE ex-reference table headers differ")
    slash_date = ex_date.replace("-", "/")
    matches = [
        row for row in parser.rows if len(row) == 14 and row[0] == symbol and row[10] == slash_date
    ]
    if len(matches) != 1:
        raise ValueError("official SZSE ex-reference response target row is not unique")
    row = matches[0]
    return {
        "symbol": row[0],
        "security_name": row[1],
        "cash_dividend_per_share": float(row[5]),
        "ex_date": row[10].replace("/", "-"),
        "registration_date": row[11].replace("/", "-"),
        "ex_reference_price": float(row[12]),
        "pre_closing_price": float(row[13]),
    }


def _pit_manifest_sha256(pit_samples: Mapping[str, Any]) -> str:
    payload = {
        "schema_version": str(pit_samples["manifest_schema_version"]),
        "selection": pit_samples["selection"],
        "seed": pit_samples["seed"],
        "sample_size_per_table": pit_samples["sample_size_per_table"],
        "daily_bars_with_adj": pit_samples["daily_bars_with_adj"],
        "financial_indicators": pit_samples["financial_indicators"],
        "valuation_daily": pit_samples["valuation_daily"],
    }
    return str(canonical_sha256(payload))


def _json_write(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_source_file(root: Path, relative_path: object) -> Path:
    pure = PurePosixPath(str(relative_path))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise ValueError("source artifact path must be bundle-relative")
    source = (root / Path(*pure.parts)).resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ValueError("source artifact path escapes source bundle") from exc
    if not source.is_file():
        raise ValueError(f"source artifact is not a regular file: {pure}")
    return source


def _strict_key_set(
    rows: object,
    *,
    fields: tuple[str, ...],
    expected: Sequence[tuple[str, ...]],
    label: str,
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError(f"{label} must be a list")
    typed: list[dict[str, Any]] = []
    observed: list[tuple[str, ...]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{label} entries must be objects")
        typed.append(row)
        observed.append(tuple(str(row.get(field) or "") for field in fields))
    if len(observed) != len(set(observed)):
        raise ValueError(f"{label} contains duplicate keys")
    if set(observed) != set(expected):
        raise ValueError(
            f"{label} must exactly cover the frozen keys: "
            f"expected={sorted(expected)}, observed={sorted(observed)}"
        )
    return typed


def _load_frozen_inputs(
    preflight_path: Path,
    final_trial_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if _sha256(final_trial_path) != FROZEN_FINAL_TRIAL_SHA256:
        raise ValueError("final trial does not match the frozen SHA-256")
    preflight = _json_object(preflight_path, label="preflight")
    pit_samples = preflight.get("pit_samples")
    if not isinstance(pit_samples, dict):
        raise ValueError("preflight.pit_samples must be an object")
    if (
        pit_samples.get("manifest_schema_version") != "p3.3-s6-local-pit-manifest-v1"
        or pit_samples.get("manifest_sha256") != FROZEN_MANIFEST_SHA256
        or pit_samples.get("seed") != FROZEN_SEED
        or pit_samples.get("sample_size_per_table") != FROZEN_SAMPLE_SIZE_PER_TABLE
    ):
        raise ValueError("preflight is not the frozen final PIT manifest")
    if _pit_manifest_sha256(pit_samples) != FROZEN_MANIFEST_SHA256:
        raise ValueError("preflight PIT manifest payload hash does not verify")
    _strict_key_set(
        pit_samples.get("daily_bars_with_adj"),
        fields=("symbol", "trade_date"),
        expected=_DAILY_KEYS,
        label="preflight daily samples",
    )
    _strict_key_set(
        pit_samples.get("financial_indicators"),
        fields=("symbol", "report_period", "metric"),
        expected=_FINANCIAL_KEYS,
        label="preflight financial samples",
    )
    _strict_key_set(
        pit_samples.get("valuation_daily"),
        fields=("symbol", "trade_date"),
        expected=_VALUATION_KEYS,
        label="preflight valuation samples",
    )
    trial = _json_object(final_trial_path, label="final trial")
    if (
        trial.get("pit_manifest_sha256") != FROZEN_MANIFEST_SHA256
        or trial.get("seed") != FROZEN_SEED
        or trial.get("sample_size_per_table") != FROZEN_SAMPLE_SIZE_PER_TABLE
    ):
        raise ValueError("final trial binding differs from the frozen manifest")
    trial_samples = trial.get("samples")
    if not isinstance(trial_samples, list) or len(trial_samples) != 15:
        raise ValueError("final trial must contain the frozen 15 samples")
    return preflight, pit_samples, trial


def _load_source_manifest(
    source_root: Path,
    *,
    pit_samples: Mapping[str, Any],
) -> dict[str, Any]:
    manifest_path = source_root / SOURCE_MANIFEST_NAME
    manifest = _json_object(manifest_path, label=SOURCE_MANIFEST_NAME)
    if manifest.get("schema_version") == GENERAL_SOURCE_MANIFEST_SCHEMA_VERSION:
        manifest = _adapt_general_source_manifest(
            source_root,
            manifest,
            pit_samples=pit_samples,
        )
    required = {
        "schema_version",
        "adjudication_contract",
        "adjudication_contract_sha256",
        "pit_manifest_sha256",
        "final_trial_sha256",
        "seed",
        "sample_size_per_table",
        "artifacts",
        "daily_samples",
        "financial_samples",
        "valuation_samples",
    }
    if set(manifest) != required:
        raise ValueError(f"{SOURCE_MANIFEST_NAME} fields differ from the frozen contract")
    expected_scalars = {
        "schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
        "adjudication_contract": ADJUDICATION_CONTRACT_VERSION,
        "adjudication_contract_sha256": ADJUDICATION_CONTRACT_SHA256,
        "pit_manifest_sha256": FROZEN_MANIFEST_SHA256,
        "final_trial_sha256": FROZEN_FINAL_TRIAL_SHA256,
        "seed": FROZEN_SEED,
        "sample_size_per_table": FROZEN_SAMPLE_SIZE_PER_TABLE,
    }
    for field, expected in expected_scalars.items():
        if manifest[field] != expected:
            raise ValueError(f"{SOURCE_MANIFEST_NAME}.{field} differs from the frozen contract")
    _strict_key_set(
        manifest["daily_samples"],
        fields=("symbol", "trade_date"),
        expected=_DAILY_KEYS,
        label="source daily samples",
    )
    _strict_key_set(
        manifest["financial_samples"],
        fields=("symbol", "report_period", "metric"),
        expected=_FINANCIAL_KEYS,
        label="source financial samples",
    )
    _strict_key_set(
        manifest["valuation_samples"],
        fields=("symbol", "trade_date"),
        expected=_VALUATION_KEYS,
        label="source valuation samples",
    )
    return manifest


def _general_artifacts_by_kind(
    manifest: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise ValueError("general source manifest artifacts must be a list")
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for raw in raw_artifacts:
        if not isinstance(raw, dict):
            raise ValueError("general source manifest artifacts must be objects")
        by_kind.setdefault(str(raw.get("source_kind") or ""), []).append(raw)
    return by_kind


def _general_actual_fields(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    actual = raw.get("actual_fields")
    if not isinstance(actual, dict):
        raise ValueError(
            f"general artifact actual_fields must be an object: {raw.get('relative_path')}"
        )
    return actual


def _general_artifact_id(prefix: str, *parts: str) -> str:
    normalized = "-".join(re.sub(r"[^a-z0-9]+", "-", part.casefold()).strip("-") for part in parts)
    artifact_id = f"{prefix}-{normalized}"
    if not _ARTIFACT_ID.fullmatch(artifact_id):
        raise ValueError(f"derived artifact id is invalid: {artifact_id}")
    return artifact_id


def _normalized_artifact(
    raw: Mapping[str, Any],
    *,
    artifact_id: str,
    source_kind: str,
    request_parameters: Mapping[str, Any],
    actual_fields: Sequence[str],
    missing_state: str = "present",
) -> dict[str, Any]:
    path = str(raw.get("relative_path") or "")
    sha256 = str(raw.get("sha256") or "")
    request = raw.get("request")
    if not isinstance(request, dict):
        raise ValueError(f"general artifact request must be an object: {path}")
    retrieved_at = str(raw.get("retrieved_at") or "")
    if not retrieved_at:
        raise ValueError(f"general artifact retrieved_at is absent: {path}")
    return {
        "id": artifact_id,
        "path": path,
        "sha256": sha256,
        "source_kind": source_kind,
        "source_identity": str(raw.get("source_identity") or ""),
        "request_parameters": {
            **dict(request_parameters),
            "source_url": str(request.get("url") or ""),
            "original_request": request,
        },
        "retrieved_at": retrieved_at,
        "timezone": "Asia/Shanghai",
        "parser_version": str(raw.get("parser_version") or ""),
        "actual_fields": list(actual_fields),
        "content_scope": "full_response_body",
        "first_success": True,
        "first_success_response_sha256": sha256,
        "fallback_reason": None,
        "prior_source_errors": [],
        "missing_state": missing_state,
    }


def _verify_general_source_inventory(
    source_root: Path,
    manifest: Mapping[str, Any],
) -> None:
    _require_timezone_aware_iso8601(
        manifest.get("generated_at"),
        label="general source manifest generated_at",
    )
    _verify_recursive_checksum_closure(source_root)
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise ValueError("general source manifest artifacts must be a list")
    if int(manifest.get("artifact_count") or -1) != len(raw_artifacts):
        raise ValueError("general source manifest artifact_count is inconsistent")
    seen: set[str] = set()
    for raw in raw_artifacts:
        if not isinstance(raw, dict):
            raise ValueError("general source artifacts must be objects")
        relative_path = str(raw.get("relative_path") or "")
        if relative_path in seen:
            raise ValueError(f"duplicate general source path: {relative_path}")
        seen.add(relative_path)
        source = _safe_source_file(source_root, relative_path)
        sha256 = str(raw.get("sha256") or "")
        if not _SHA256.fullmatch(sha256) or _sha256(source) != sha256:
            raise ValueError(f"general source artifact SHA-256 mismatch: {relative_path}")
        if int(raw.get("bytes") or -1) != source.stat().st_size:
            raise ValueError(f"general source artifact byte count mismatch: {relative_path}")
    contract_artifacts = [
        raw
        for raw in raw_artifacts
        if isinstance(raw, dict) and raw.get("source_kind") == "adjudication_contract"
    ]
    if len(contract_artifacts) != 1:
        raise ValueError("general source bundle must contain exactly one adjudication contract")
    contract = contract_artifacts[0]
    if str(contract.get("sha256") or "") != ADJUDICATION_CONTRACT_SHA256:
        raise ValueError("general source adjudication contract SHA-256 differs")


def _require_timezone_aware_iso8601(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value or value.endswith(":z"):
        raise ValueError(f"{label} must be a timezone-aware ISO-8601 timestamp")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} must be a timezone-aware ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include an explicit UTC offset")
    return parsed


def _checksum_entries(checksum_path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    try:
        raw_lines = checksum_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"source checksum file is unreadable: {checksum_path}") from exc
    for line_number, raw_line in enumerate(raw_lines, start=1):
        match = re.fullmatch(r"([0-9a-f]{64})  (?:\*)?(.+)", raw_line)
        if match is None:
            raise ValueError(f"invalid checksum row {checksum_path}:{line_number}")
        relative = match.group(2).removeprefix("./")
        pure = PurePosixPath(relative)
        if not relative or pure.is_absolute() or ".." in pure.parts or relative in entries:
            raise ValueError(f"unsafe/duplicate checksum path {checksum_path}:{relative}")
        entries[relative] = match.group(1)
    return entries


def _verify_checksum_closure(checksum_path: Path) -> None:
    entries = _checksum_entries(checksum_path)
    base = checksum_path.parent
    actual_paths = {
        candidate.relative_to(base).as_posix()
        for candidate in base.rglob("*")
        if candidate.is_file() and candidate != checksum_path
    }
    if set(entries) != actual_paths:
        missing = sorted(actual_paths - set(entries))
        stale = sorted(set(entries) - actual_paths)
        raise ValueError(
            f"checksum closure differs for {checksum_path}: "
            f"unregistered={missing[:5]} stale={stale[:5]}"
        )
    for relative, expected_sha256 in entries.items():
        candidate = _safe_source_file(base, relative)
        if _sha256(candidate) != expected_sha256:
            raise ValueError(
                f"source checksum mismatch: {checksum_path.relative_to(base)}:{relative}"
            )


def _verify_recursive_checksum_closure(source_root: Path) -> None:
    root_checksum = source_root / GENERAL_SOURCE_CHECKSUM_NAME
    if not root_checksum.is_file():
        raise ValueError("general source bundle root SHA256SUMS is absent")
    checksum_files = sorted(
        source_root.rglob(GENERAL_SOURCE_CHECKSUM_NAME),
        key=lambda path: path.relative_to(source_root).as_posix(),
    )
    if root_checksum not in checksum_files:
        raise ValueError("general source bundle checksum hierarchy is invalid")
    for checksum_path in checksum_files:
        _verify_checksum_closure(checksum_path)


def _inventory_window(
    raw: Mapping[str, Any],
) -> tuple[str, str, int]:
    request = raw.get("request")
    if not isinstance(request, dict) or not isinstance(request.get("params"), dict):
        raise ValueError("inventory artifact request params are absent")
    params = request["params"]
    if "seDate" in params:
        dates = params["seDate"]
        if not isinstance(dates, list) or len(dates) != 2:
            raise ValueError("SZSE inventory seDate is invalid")
        return str(dates[0]), str(dates[1]), int(params["pageNum"])
    return (
        str(params["beginDate"]),
        str(params["endDate"]),
        int(params["pageHelp.pageNo"]),
    )


def _adapt_general_source_manifest(
    source_root: Path,
    general: Mapping[str, Any],
    *,
    pit_samples: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize the frozen read-only source inventory into the builder contract."""

    bindings = general.get("frozen_bindings")
    approval = general.get("approval_state")
    if not isinstance(bindings, dict) or not isinstance(approval, dict):
        raise ValueError("general source manifest bindings/approval are absent")
    if (
        bindings.get("local_manifest_sha256") != FROZEN_MANIFEST_SHA256
        or bindings.get("final_trial_sha256") != FROZEN_FINAL_TRIAL_SHA256
        or bindings.get("seed") != FROZEN_SEED
        or bindings.get("sample_count") != 15
    ):
        raise ValueError("general source manifest frozen bindings differ")
    if (
        approval.get("approved") is not False
        or approval.get("signed") is not False
        or approval.get("s6_done_claimed") is not False
    ):
        raise ValueError("general source bundle must remain unsigned and unapproved")
    _verify_general_source_inventory(source_root, general)
    by_kind = _general_artifacts_by_kind(general)

    normalized_artifacts: list[dict[str, Any]] = []
    daily_samples: list[dict[str, Any]] = []
    rule_artifacts: list[tuple[str, str | None, str, str]] = []
    for raw in [
        *by_kind.get("official_exchange_price_tick_rule", []),
        *by_kind.get("official_exchange_rule", []),
    ]:
        fields = _general_actual_fields(raw)
        source_market = str(fields.get("market") or "")
        if (
            source_market not in {"SSE-A", "SZSE-A"}
            or fields.get("security_type") != "A_share"
            or fields.get("currency") != "CNY"
        ):
            raise ValueError("official price-tick rule must be an SSE-A/SZSE-A CNY A-share rule")
        market = source_market.removesuffix("-A")
        price_tick = float(fields.get("price_tick") or 0)
        effective_from = str(fields.get("effective_from") or "")
        effective_to_raw = fields.get("effective_to")
        effective_to = str(effective_to_raw) if effective_to_raw is not None else None
        if not math.isclose(
            price_tick,
            0.01,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("official price-tick rule market/value is invalid")
        artifact_id = _general_artifact_id(
            "price-tick",
            market,
            effective_from,
            effective_to or "open",
        )
        normalized_artifacts.append(
            _normalized_artifact(
                raw,
                artifact_id=artifact_id,
                source_kind="official_exchange_rule",
                request_parameters={
                    "market": market,
                    "effective_from": effective_from,
                    "effective_to": effective_to,
                    "price_tick": price_tick,
                },
                actual_fields=["price_tick"],
            )
        )
        rule_artifacts.append((effective_from, effective_to, market, artifact_id))
    if len(rule_artifacts) != 3:
        raise ValueError("general source bundle must contain three price-tick rules")

    reference_price_artifacts: dict[tuple[str, str], tuple[str, float]] = {}
    expected_references: dict[
        tuple[str, str],
        dict[str, float | str],
    ] = {
        ("001260", "2026-05-27"): {
            "reference_price": 20.29,
            "cash_dividend_per_share": 0.215,
            "pre_closing_price": 20.50,
            "security_name": "坤泰股份",
        },
        ("600782", "2026-06-05"): {
            "reference_price": 2.64,
            "cash_dividend_per_share": 0.135,
        },
    }
    for raw in by_kind.get(
        "official_exchange_ex_reference_price_response",
        [],
    ):
        fields = _general_actual_fields(raw)
        symbol = str(fields.get("symbol") or "")
        ex_date = str(fields.get("ex_date") or "")
        reference_price = float(
            (
                fields.get("pre_close_price")
                if symbol == "600782"
                else fields.get("ex_reference_price")
            )
            or 0
        )
        expected = expected_references.get((symbol, ex_date))
        cash_field = (
            fields.get("A_BEFR_TAX_DIV")
            if symbol == "600782"
            else fields.get("cash_dividend_per_share")
        )
        if (
            expected is None
            or not math.isclose(
                reference_price,
                float(expected["reference_price"]),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                float(cash_field or 0),
                float(expected["cash_dividend_per_share"]),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ):
            raise ValueError("official ex-reference metadata differs from the frozen event")
        request = raw.get("request")
        params = request.get("params") if isinstance(request, dict) else None
        if not isinstance(request, dict) or request.get("method") != "GET":
            raise ValueError("official ex-reference request contract differs")
        source_url = str(request.get("url") or "")
        source = urlparse(source_url)
        if symbol == "600782":
            if (
                source.hostname != "query.sse.com.cn"
                or not isinstance(params, dict)
                or params.get("sqlId") != "COMMON_SSE_CP_GPJCTPZ_GPLB_LRFP_FH_L"
                or str(params.get("COMPANY_CODE") or "") != symbol
            ):
                raise ValueError("official SSE ex-reference request contract differs")
        elif (
            source.hostname != "docs.static.szse.cn"
            or source.path != ("/www/market/periodical/month/W020260605534753848014.html")
            or not isinstance(params, dict)
            or params
        ):
            raise ValueError("official SZSE ex-reference request contract differs")
        response_path = _safe_source_file(
            source_root,
            str(raw.get("relative_path") or ""),
        )
        if symbol == "600782":
            response = _json_object(
                response_path,
                label="official 600782 ex-reference response",
            )
            response_rows = response.get("result")
            if not isinstance(response_rows, list):
                raise ValueError("official ex-reference response rows are absent")
            matching_rows = [
                row
                for row in response_rows
                if isinstance(row, dict)
                and str(row.get("A_STOCK_CODE") or "") == symbol
                and str(row.get("A_DIV_DATE") or "") == ex_date.replace("-", "")
            ]
            if len(matching_rows) != 1:
                raise ValueError("official ex-reference response target row is not unique")
            matching_row = matching_rows[0]
            raw_reference = float(matching_row.get("PRE_CLOSE_PRICE") or 0)
            raw_cash = float(matching_row.get("A_BEFR_TAX_DIV") or 0)
        else:
            matching_row = _szse_dividend_table_target(
                response_path,
                symbol=symbol,
                ex_date=ex_date,
            )
            raw_reference = float(matching_row["ex_reference_price"])
            raw_cash = float(matching_row["cash_dividend_per_share"])
            if matching_row["security_name"] != expected["security_name"] or not math.isclose(
                float(matching_row["pre_closing_price"]),
                float(expected["pre_closing_price"]),
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError("official SZSE ex-reference subject values differ")
        if not math.isclose(
            raw_reference,
            reference_price,
            rel_tol=0.0,
            abs_tol=1e-12,
        ) or not math.isclose(
            raw_cash,
            float(expected["cash_dividend_per_share"]),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("official ex-reference response target values differ")
        artifact_id = _general_artifact_id(
            "official-reference",
            symbol,
            ex_date,
        )
        if (symbol, ex_date) in reference_price_artifacts:
            raise ValueError("duplicate official ex-reference artifact")
        reference_price_artifacts[(symbol, ex_date)] = (
            artifact_id,
            reference_price,
        )
        normalized_artifacts.append(
            _normalized_artifact(
                raw,
                artifact_id=artifact_id,
                source_kind="audited_external_response",
                request_parameters={
                    "symbol": symbol,
                    "ex_date": ex_date,
                    "ex_reference_price": reference_price,
                    "cash_dividend_per_share": float(expected["cash_dividend_per_share"]),
                },
                actual_fields=[
                    "ex_reference_price",
                    "cash_dividend_per_share",
                ],
            )
        )
    if set(reference_price_artifacts) != set(expected_references):
        raise ValueError(
            "general source bundle must contain the frozen official "
            "001260 and 600782 ex-reference prices"
        )

    def price_tick_rule_id(symbol: str, event_date: str) -> str:
        market = "SSE" if symbol.startswith("6") else "SZSE"
        matches = [
            artifact_id
            for effective_from, effective_to, rule_market, artifact_id in rule_artifacts
            if rule_market == market
            and effective_from <= event_date
            and (effective_to is None or event_date <= effective_to)
        ]
        if len(matches) != 1:
            raise ValueError(f"price-tick rule coverage must be unique: {symbol} {event_date}")
        return matches[0]

    event_artifact_ids: dict[tuple[str, str], str] = {}
    for raw in by_kind.get("official_exchange_corporate_action_pdf", []):
        fields = _general_actual_fields(raw)
        symbol = str(fields.get("symbol") or "")
        ex_date = str(fields.get("ex_date") or "")
        expected = next(
            (
                event
                for sample_key, sample_events in _DAILY_EVENTS.items()
                for event in sample_events
                if sample_key[0] == symbol
                if event["ex_date"] == ex_date
            ),
            None,
        )
        if expected is None:
            raise ValueError(f"unexpected company-action artifact: {symbol} {ex_date}")
        if (
            not math.isclose(
                float(fields.get("cash_dividend_per_share") or -1),
                float(expected["cash_dividend_per_share"]),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or float(fields.get("bonus_share_per_share") or 0) != 0.0
            or float(fields.get("transfer_share_per_share") or 0) != 0.0
        ):
            raise ValueError(f"company-action parameters differ: {symbol} {ex_date}")
        artifact_id = _general_artifact_id("action", symbol, ex_date)
        event_artifact_ids[(symbol, ex_date)] = artifact_id
        normalized_artifacts.append(
            _normalized_artifact(
                raw,
                artifact_id=artifact_id,
                source_kind="official_exchange_disclosure",
                request_parameters={
                    "symbol": symbol,
                    "ex_date": ex_date,
                },
                actual_fields=[
                    "event_type",
                    "ex_date",
                    "cash_dividend_per_share",
                    "share_distribution_per_share",
                ],
            )
        )
    if set(event_artifact_ids) != {
        (key[0], str(event["ex_date"])) for key, events in _DAILY_EVENTS.items() for event in events
    }:
        raise ValueError("general source bundle does not contain the fixed nine actions")

    inventory_candidates = list(by_kind.get("complete_unfiltered_announcement_inventory", []))
    inventory_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for raw in inventory_candidates:
        fields = _general_actual_fields(raw)
        symbol = str(fields.get("symbol") or "")
        routing = raw.get("routing")
        if isinstance(routing, dict) and routing.get("pairing_candidate_use") is False:
            continue
        if (
            not isinstance(routing, dict)
            or routing.get("pairing_candidate_use") is not True
            or routing.get("authoritative_exact_window") is not True
        ):
            continue
        inventory_by_symbol.setdefault(symbol, []).append(raw)

    for symbol, trade_date in _DAILY_KEYS:
        daily_pit_rows = pit_samples.get("daily_bars_with_adj")
        if not isinstance(daily_pit_rows, list):
            raise ValueError("frozen daily PIT samples are absent")
        sample_current = next(
            row
            for row in daily_pit_rows
            if row["symbol"] == symbol and row["trade_date"] == trade_date
        )
        expected_end = str(sample_current["adj_anchor_date"])
        raw_pages = sorted(
            inventory_by_symbol.get(symbol, []),
            key=lambda raw: _inventory_window(raw)[2],
        )
        if not raw_pages:
            raise ValueError(f"general source inventory is absent for {symbol}")
        page_entries: list[dict[str, Any]] = []
        raw_total: int | None = None
        classification_totals = {
            "implemented_adjustment_event": 0,
            "not_factor_adjustment": 0,
            "not_adjustment_related": 0,
            "unknown_adjustment_candidate": 0,
        }
        classification_records: list[dict[str, Any]] = []
        for page_number, raw in enumerate(raw_pages, start=1):
            start_date, end_date, observed_page = _inventory_window(raw)
            if observed_page != page_number or start_date != trade_date or end_date != expected_end:
                raise ValueError(
                    f"general inventory is not the exact frozen window: {symbol} page {page_number}"
                )
            original_request = raw.get("request")
            if not isinstance(original_request, dict):
                raise ValueError(
                    f"general inventory request is absent: {symbol} page {page_number}"
                )
            validate_unfiltered_inventory_request(
                original_request,
                symbol=symbol,
                start_date=trade_date,
                end_date=expected_end,
                page_number=page_number,
            )
            fields = _general_actual_fields(raw)
            if fields.get("unfiltered") is not True:
                raise ValueError(f"general inventory is not declared unfiltered: {symbol}")
            response_path = _safe_source_file(
                source_root,
                str(raw.get("relative_path") or ""),
            )
            page_raw_total, inventory_records = parse_official_inventory_page(
                response_path,
                symbol=symbol,
            )
            if raw_total is None:
                raw_total = page_raw_total
            elif page_raw_total != raw_total:
                raise ValueError(f"inventory total differs across pages: {symbol}")
            raw_response_records = len(inventory_records)
            if (
                int(fields.get("official_total") or -1) != page_raw_total
                or int(fields.get("row_count") or -1) != raw_response_records
                or str(fields.get("window_start") or "") != trade_date
                or str(fields.get("window_end") or "") != expected_end
            ):
                raise ValueError(f"inventory manifest counts/window differ from raw: {symbol}")
            page_classifications = {
                "implemented_adjustment_event": 0,
                "not_factor_adjustment": 0,
                "not_adjustment_related": 0,
                "unknown_adjustment_candidate": 0,
            }
            for record_id, title, document_path, publish_date in inventory_records:
                if not trade_date <= publish_date <= expected_end:
                    raise ValueError(
                        f"inventory announcement lies outside frozen window: "
                        f"{symbol} {publish_date}"
                    )
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
            if page_classifications["unknown_adjustment_candidate"]:
                raise ValueError(
                    f"inventory contains unclassified adjustment candidates: "
                    f"{symbol} page {page_number}"
                )
            if sum(page_classifications.values()) != raw_response_records:
                raise ValueError(f"inventory classification does not cover every row: {symbol}")
            for classification, count in page_classifications.items():
                classification_totals[classification] += count
            reported_records = page_classifications["implemented_adjustment_event"]
            artifact_id = _general_artifact_id(
                "inventory",
                symbol,
                f"p{page_number:03d}",
            )
            normalized_artifacts.append(
                _normalized_artifact(
                    raw,
                    artifact_id=artifact_id,
                    source_kind="official_exchange_disclosure",
                    request_parameters={
                        "symbol": symbol,
                        "start_date": start_date,
                        "end_date": end_date,
                        "page_number": page_number,
                    },
                    actual_fields=["company_action_inventory_records"],
                )
            )
            page_entries.append(
                {
                    "page_number": page_number,
                    "evidence_id": artifact_id,
                    "reported_records": reported_records,
                    "raw_response_records": raw_response_records,
                    "not_factor_adjustment_records": page_classifications["not_factor_adjustment"],
                    "not_adjustment_related_records": page_classifications[
                        "not_adjustment_related"
                    ],
                    "unknown_adjustment_candidate_records": page_classifications[
                        "unknown_adjustment_candidate"
                    ],
                }
            )
        if (
            raw_total is None
            or sum(int(page["raw_response_records"]) for page in page_entries) != raw_total
        ):
            raise ValueError(f"general inventory pagination is incomplete: {symbol}")
        expected_event_count = len(_DAILY_EVENTS[(symbol, trade_date)])
        if (
            classification_totals["implemented_adjustment_event"] != expected_event_count
            or classification_totals["unknown_adjustment_candidate"] != 0
            or sum(classification_totals.values()) != raw_total
        ):
            raise ValueError(
                f"general inventory event taxonomy differs from frozen events: {symbol}"
            )
        events = [
            {
                **event,
                "announcement_evidence_id": event_artifact_ids[(symbol, str(event["ex_date"]))],
                "price_tick_evidence_id": price_tick_rule_id(
                    symbol,
                    str(event["ex_date"]),
                ),
                "reference_price_evidence_id": (
                    reference_price_artifacts[(symbol, str(event["ex_date"]))][0]
                    if (symbol, str(event["ex_date"])) in reference_price_artifacts
                    else None
                ),
            }
            for event in _DAILY_EVENTS[(symbol, trade_date)]
        ]
        daily_samples.append(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "event_window": {
                    "start_date": trade_date,
                    "end_date": expected_end,
                    "raw_total_records": raw_total,
                    "inventory_pages": page_entries,
                    "taxonomy_version": ADJUSTMENT_EVENT_TAXONOMY_VERSION,
                    "classification_summary": classification_totals,
                    "classification_sha256": canonical_sha256(classification_records),
                },
                "events": events,
            }
        )

    financial_samples: list[dict[str, Any]] = []
    seen_financial: set[tuple[str, str, str]] = set()
    for raw in by_kind.get("official_financial_report_pdf", []):
        fields = _general_actual_fields(raw)
        key_raw = fields.get("sample_key")
        if not isinstance(key_raw, dict):
            raise ValueError("financial artifact sample_key is absent")
        key = (
            str(key_raw.get("symbol") or ""),
            str(key_raw.get("report_period") or ""),
            str(key_raw.get("metric") or ""),
        )
        if key not in _FINANCIAL_KEYS or key in seen_financial:
            raise ValueError(f"unexpected/duplicate financial artifact: {key}")
        seen_financial.add(key)
        formula = _FINANCIAL_FORMULAS.get(key)
        if formula is None:
            strict = fields.get("strict_labels")
            rejected = fields.get("approximate_labels_rejected")
            if (
                strict != {"主营业务收入": 0, "主营营业收入": 0}
                or not isinstance(rejected, dict)
                or int(rejected.get("营业收入") or 0) <= 0
                or int(rejected.get("营业总收入") or 0) <= 0
            ):
                raise ValueError(
                    f"revenue unavailable proof does not reject approximate labels: {key}"
                )
            actual_fields = ["营业收入", "营业总收入"]
            missing_state = "field_absent"
        else:
            raw_line_items = fields.get("line_items")
            if not isinstance(raw_line_items, dict):
                raise ValueError(f"financial original line items are absent: {key}")
            source_line_item_order = (
                (
                    "consolidated_net_profit_current",
                    "consolidated_net_profit_prior",
                )
                if key[2] == "net_profit_yoy"
                else (
                    "parent_net_profit_current",
                    "parent_equity_opening",
                    "parent_equity_closing",
                )
            )
            if set(raw_line_items) != set(source_line_item_order):
                raise ValueError(f"financial original line-item labels differ: {key}")
            expected_values = [float(item[1]) for item in formula["operands"]]
            observed_values = [float(raw_line_items[name]) for name in source_line_item_order]
            if len(observed_values) != len(expected_values) or any(
                not math.isclose(observed, expected, rel_tol=0.0, abs_tol=0.005)
                for observed, expected in zip(
                    observed_values,
                    expected_values,
                    strict=True,
                )
            ):
                raise ValueError(f"financial original line-item values differ: {key}")
            actual_fields = [str(item[2]) for item in formula["operands"]]
            missing_state = "present"
        artifact_id = _general_artifact_id(
            "financial",
            key[0],
            key[1],
            key[2],
        )
        normalized_artifacts.append(
            _normalized_artifact(
                raw,
                artifact_id=artifact_id,
                source_kind="official_exchange_disclosure",
                request_parameters={
                    "symbol": key[0],
                    "report_period": key[1],
                    "metric": key[2],
                },
                actual_fields=actual_fields,
                missing_state=missing_state,
            )
        )
        financial_samples.append(
            {
                "symbol": key[0],
                "report_period": key[1],
                "metric": key[2],
                "evidence_ids": [artifact_id],
            }
        )
    if seen_financial != set(_FINANCIAL_KEYS):
        raise ValueError("general source bundle does not contain five financial reports")

    valuation_samples: list[dict[str, Any]] = []
    seen_valuation: set[tuple[str, str]] = set()
    for raw in by_kind.get("valuation_raw_json_response", []):
        fields = _general_actual_fields(raw)
        target = fields.get("target")
        if not isinstance(target, dict):
            raise ValueError("valuation target is absent")
        valuation_key = (
            str(target.get("symbol") or ""),
            str(target.get("trade_date") or ""),
        )
        if valuation_key not in _VALUATION_KEYS or valuation_key in seen_valuation:
            raise ValueError(f"unexpected/duplicate valuation artifact: {valuation_key}")
        seen_valuation.add(valuation_key)
        artifact_id = _general_artifact_id(
            "valuation",
            valuation_key[0],
            valuation_key[1],
        )
        normalized_artifacts.append(
            _normalized_artifact(
                raw,
                artifact_id=artifact_id,
                source_kind="audited_external_response",
                request_parameters={
                    "symbol": valuation_key[0],
                    "trade_date": valuation_key[1],
                },
                actual_fields=[
                    "pe_ttm",
                    "pb_mrq",
                    "ps_ttm",
                    "source",
                    "available_time",
                ],
            )
        )
        valuation_samples.append(
            {
                "symbol": valuation_key[0],
                "trade_date": valuation_key[1],
                "evidence_ids": [artifact_id],
            }
        )
    if seen_valuation != set(_VALUATION_KEYS):
        raise ValueError("general source bundle does not contain five valuation responses")

    return {
        "schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
        "adjudication_contract": ADJUDICATION_CONTRACT_VERSION,
        "adjudication_contract_sha256": ADJUDICATION_CONTRACT_SHA256,
        "pit_manifest_sha256": FROZEN_MANIFEST_SHA256,
        "final_trial_sha256": FROZEN_FINAL_TRIAL_SHA256,
        "seed": FROZEN_SEED,
        "sample_size_per_table": FROZEN_SAMPLE_SIZE_PER_TABLE,
        "artifacts": normalized_artifacts,
        "daily_samples": daily_samples,
        "financial_samples": financial_samples,
        "valuation_samples": valuation_samples,
    }


def _artifact_index(
    source_root: Path,
    source_manifest: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Path]]:
    raw_artifacts = source_manifest["artifacts"]
    if not isinstance(raw_artifacts, list):
        raise ValueError("SOURCE-MANIFEST.json.artifacts must be a list")
    artifacts: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    files: dict[str, Path] = {}
    for raw_artifact in raw_artifacts:
        if not isinstance(raw_artifact, dict) or set(raw_artifact) != _ARTIFACT_FIELDS:
            raise ValueError("source artifact fields differ from the v1 source contract")
        artifact_id = str(raw_artifact["id"])
        if not _ARTIFACT_ID.fullmatch(artifact_id):
            raise ValueError(f"invalid source artifact id: {artifact_id}")
        if artifact_id == "frozen-local-preclose":
            raise ValueError("source bundle cannot replace frozen local pre-close evidence")
        if artifact_id in by_id:
            raise ValueError(f"duplicate source artifact id: {artifact_id}")
        source = _safe_source_file(source_root, raw_artifact["path"])
        expected_sha256 = str(raw_artifact["sha256"])
        if not _SHA256.fullmatch(expected_sha256) or _sha256(source) != expected_sha256:
            raise ValueError(f"source artifact SHA-256 mismatch: {artifact_id}")
        if raw_artifact["first_success_response_sha256"] != expected_sha256:
            raise ValueError(f"source artifact is not first-success bound: {artifact_id}")
        if (
            "baostock" in str(raw_artifact["source_identity"]).casefold()
            or raw_artifact["source_kind"] == "frozen_local_manifest"
        ):
            raise ValueError(
                f"external source artifact is forbidden or not external: {artifact_id}"
            )
        destination_suffix = "".join(source.suffixes) or ".bin"
        artifact = {key: value for key, value in raw_artifact.items() if key != "path"}
        artifact["relative_path"] = f"artifacts/{artifact_id}{destination_suffix}"
        artifacts.append(artifact)
        by_id[artifact_id] = artifact
        files[artifact_id] = source
    return artifacts, by_id, files


def _trial_index(trial: Mapping[str, Any]) -> dict[tuple[str, tuple[str, ...]], dict[str, Any]]:
    result: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for sample in trial["samples"]:
        if not isinstance(sample, dict):
            raise ValueError("final trial samples must be objects")
        table = str(sample.get("table") or "")
        key = sample.get("key")
        if not isinstance(key, dict):
            raise ValueError("final trial sample key must be an object")
        fields = {
            "daily_bars": ("symbol", "trade_date"),
            "financial_indicators": ("symbol", "report_period", "metric"),
            "valuation_daily": ("symbol", "trade_date"),
        }.get(table)
        if fields is None:
            raise ValueError(f"unsupported final trial table: {table}")
        identity = (table, tuple(str(key.get(field) or "") for field in fields))
        if identity in result:
            raise ValueError("final trial contains duplicate keys")
        result[identity] = sample
    return result


def _pit_index(
    pit_samples: Mapping[str, Any],
) -> dict[tuple[str, tuple[str, ...]], dict[str, Any]]:
    result: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for table, source_key, fields in (
        ("daily_bars", "daily_bars_with_adj", ("symbol", "trade_date")),
        (
            "financial_indicators",
            "financial_indicators",
            ("symbol", "report_period", "metric"),
        ),
        ("valuation_daily", "valuation_daily", ("symbol", "trade_date")),
    ):
        rows = pit_samples[source_key]
        if not isinstance(rows, list):
            raise ValueError(f"preflight {source_key} must be a list")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"preflight {source_key} entries must be objects")
            identity = (table, tuple(str(row.get(field) or "") for field in fields))
            result[identity] = row
    return result


def _read_precloses(db_path: Path) -> list[dict[str, Any]]:
    resolved = db_path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"production DB is not a regular file: {resolved}")
    uri = f"file:{quote(str(resolved))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=15.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
        if query_only != 1:
            raise ValueError("SQLite query_only could not be enabled")
        rows: list[dict[str, Any]] = []
        for symbol, sample_date in _DAILY_KEYS:
            for event in _DAILY_EVENTS[(symbol, sample_date)]:
                row = connection.execute(
                    """
                    SELECT trade_date, close, source
                    FROM daily_bars
                    WHERE symbol = ? AND trade_date < ? AND source = 'baostock'
                    ORDER BY trade_date DESC
                    LIMIT 1
                    """,
                    (symbol, event["ex_date"]),
                ).fetchone()
                if row is None:
                    raise ValueError(f"no local pre-close row for {symbol} {event['ex_date']}")
                close = float(row["close"])
                source = str(row["source"] or "")
                if not math.isfinite(close) or close <= 0 or source != "baostock":
                    raise ValueError(f"invalid local pre-close row for {symbol} {event['ex_date']}")
                rows.append(
                    {
                        "symbol": symbol,
                        "sample_trade_date": sample_date,
                        "ex_date": event["ex_date"],
                        "pre_close_trade_date": str(row["trade_date"]),
                        "pre_close": close,
                        "source": source,
                    }
                )
    finally:
        connection.close()
    if len(rows) != 9:
        raise ValueError("frozen pre-close evidence must contain exactly nine events")
    identities = {(str(row["symbol"]), str(row["ex_date"])) for row in rows}
    if len(identities) != len(rows):
        raise ValueError("frozen pre-close evidence contains duplicate event keys")
    return rows


def _artifact_for_id(
    artifacts: Mapping[str, Mapping[str, Any]],
    artifact_id: object,
    *,
    label: str,
) -> Mapping[str, Any]:
    resolved = artifacts.get(str(artifact_id))
    if resolved is None:
        raise ValueError(f"{label} references unknown artifact: {artifact_id}")
    return resolved


def _require_artifact_fields(
    artifact: Mapping[str, Any],
    fields: set[str],
    *,
    label: str,
) -> None:
    actual = artifact.get("actual_fields")
    if not isinstance(actual, list) or not fields.issubset({str(field) for field in actual}):
        raise ValueError(f"{label} artifact does not expose {sorted(fields)}")


def _source_rows_by_key(
    rows: object,
    fields: tuple[str, ...],
) -> dict[tuple[str, ...], dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("source sample collection must be a list")
    return {
        tuple(str(row[field]) for field in fields): row for row in rows if isinstance(row, dict)
    }


def _operand(
    *,
    name: str,
    value: float,
    line_item: str,
    evidence_id: str,
    event: Mapping[str, Any] | None = None,
    quantum: float = 0.01,
    unit: str = "CNY",
) -> dict[str, Any]:
    return {
        "name": name,
        "value": value,
        "lower": value - quantum / 2.0,
        "upper": value + quantum / 2.0,
        "unit": unit,
        "line_item": line_item,
        "disclosure_precision": {
            "basis": "exact_machine_fact" if quantum == 0 else "disclosed_unit",
            "quantum": quantum,
        },
        "evidence_id": evidence_id,
        "event": dict(event) if event is not None else None,
    }


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


def _financial_result(
    formula_id: str,
    operands: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    by_name = {
        str(operand["name"]): (
            float(operand["value"]),
            float(operand["lower"]),
            float(operand["upper"]),
        )
        for operand in operands
    }
    if formula_id == "net_profit_yoy_v1":
        current = by_name["net_profit_t"]
        prior = by_name["net_profit_t_minus_4"]
        point = (current[0] - prior[0]) / abs(prior[0])
        numerator = (current[1] - prior[2], current[2] - prior[1])
        prior_abs = sorted((abs(prior[1]), abs(prior[2])))
        lower, upper = _div_interval(numerator, (prior_abs[0], prior_abs[1]))
    elif formula_id == "roe_average_parent_equity_v1":
        profit = by_name["parent_net_profit_t"]
        opening = by_name["opening_parent_equity"]
        closing = by_name["closing_parent_equity"]
        point_denominator = (opening[0] + closing[0]) / 2.0
        point = profit[0] / point_denominator
        denominator = (
            (opening[1] + closing[1]) / 2.0,
            (opening[2] + closing[2]) / 2.0,
        )
        lower, upper = _div_interval((profit[1], profit[2]), denominator)
    else:
        raise ValueError(f"unsupported financial formula: {formula_id}")
    return {"value": point, "lower": lower, "upper": upper}


def _daily_samples(
    *,
    source_manifest: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    source_files: Mapping[str, Path],
    pit_index: Mapping[tuple[str, tuple[str, ...]], Mapping[str, Any]],
    trial_index: Mapping[tuple[str, tuple[str, ...]], Mapping[str, Any]],
    precloses: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    source_rows = _source_rows_by_key(
        source_manifest["daily_samples"],
        ("symbol", "trade_date"),
    )
    preclose_by_event = {(str(row["symbol"]), str(row["ex_date"])): row for row in precloses}
    result: list[dict[str, Any]] = []
    for key in _DAILY_KEYS:
        symbol, trade_date = key
        identity = ("daily_bars", key)
        source = source_rows[key]
        expected_fields = {
            "symbol",
            "trade_date",
            "event_window",
            "events",
        }
        if set(source) != expected_fields:
            raise ValueError(f"source daily sample fields differ for {key}")
        current = pit_index[identity]
        trial = trial_index[identity]
        expected_events = _DAILY_EVENTS[key]
        raw_events = source["events"]
        if not isinstance(raw_events, list) or len(raw_events) != len(expected_events):
            raise ValueError(f"daily event count differs from adjudication for {key}")
        event_by_date: dict[str, dict[str, Any]] = {}
        for raw_event in raw_events:
            if not isinstance(raw_event, dict):
                raise ValueError(f"daily events must be objects for {key}")
            if set(raw_event) != {
                "ex_date",
                "event_type",
                "cash_dividend_per_share",
                "share_distribution_per_share",
                "price_tick",
                "announcement_evidence_id",
                "price_tick_evidence_id",
                "reference_price_evidence_id",
            }:
                raise ValueError(f"daily event fields differ for {key}")
            event_by_date[str(raw_event["ex_date"])] = raw_event
        if len(event_by_date) != len(raw_events):
            raise ValueError(f"daily events contain duplicate ex-dates for {key}")

        operands: list[dict[str, Any]] = []
        evidence_ids: set[str] = {"frozen-local-preclose"}
        action_document_paths: set[str] = set()
        product = 1.0
        for event_number, expected_event in enumerate(expected_events, start=1):
            ex_date = str(expected_event["ex_date"])
            raw_event = event_by_date.get(ex_date)
            if raw_event is None:
                raise ValueError(f"daily event {symbol} {ex_date} is absent")
            for field in (
                "event_type",
                "cash_dividend_per_share",
                "share_distribution_per_share",
                "price_tick",
            ):
                observed = raw_event[field]
                expected = expected_event[field]
                if isinstance(expected, float):
                    if not math.isclose(
                        float(observed),
                        expected,
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    ):
                        raise ValueError(f"daily event {symbol} {ex_date} {field} differs")
                elif observed != expected:
                    raise ValueError(f"daily event {symbol} {ex_date} {field} differs")
            announcement_id = str(raw_event["announcement_evidence_id"])
            announcement = _artifact_for_id(
                artifacts,
                announcement_id,
                label=f"daily event {symbol} {ex_date}",
            )
            if announcement["source_kind"] != "official_exchange_disclosure":
                raise ValueError(
                    f"daily event is not backed by an official disclosure: {symbol} {ex_date}"
                )
            _require_artifact_fields(
                announcement,
                {
                    "event_type",
                    "ex_date",
                    "cash_dividend_per_share",
                    "share_distribution_per_share",
                },
                label=f"daily event {symbol} {ex_date}",
            )
            action_source_path = urlparse(
                str(announcement["request_parameters"].get("source_url") or "")
            ).path
            if not action_source_path:
                raise ValueError(f"daily action source URL path is absent: {symbol} {ex_date}")
            action_document_paths.add(action_source_path)
            price_tick_id = str(raw_event["price_tick_evidence_id"])
            price_tick_artifact = _artifact_for_id(
                artifacts,
                price_tick_id,
                label=f"daily price-tick rule {symbol} {ex_date}",
            )
            if price_tick_artifact["source_kind"] != "official_exchange_rule":
                raise ValueError(
                    f"daily price tick is not backed by an exchange rule: {symbol} {ex_date}"
                )
            _require_artifact_fields(
                price_tick_artifact,
                {"price_tick"},
                label=f"daily price-tick rule {symbol} {ex_date}",
            )
            rule_parameters = price_tick_artifact["request_parameters"]
            expected_market = "SSE" if symbol.startswith("6") else "SZSE"
            effective_from = str(rule_parameters.get("effective_from") or "")
            effective_to_raw = rule_parameters.get("effective_to")
            effective_to = str(effective_to_raw) if effective_to_raw is not None else None
            if (
                str(rule_parameters.get("market") or "") != expected_market
                or not math.isclose(
                    float(rule_parameters.get("price_tick") or 0),
                    float(expected_event["price_tick"]),
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
                or not effective_from
                or effective_from > ex_date
                or (effective_to is not None and effective_to < ex_date)
            ):
                raise ValueError(f"daily price-tick rule does not cover {symbol} {ex_date}")
            preclose_row = preclose_by_event[(symbol, ex_date)]
            pre_close = Decimal(str(preclose_row["pre_close"]))
            cash = Decimal(str(expected_event["cash_dividend_per_share"]))
            shares = Decimal(str(expected_event["share_distribution_per_share"]))
            tick = Decimal(str(expected_event["price_tick"]))
            reference_price_id_raw = raw_event["reference_price_evidence_id"]
            if reference_price_id_raw is None:
                unrounded = (pre_close - cash) / (Decimal(1) + shares)
                reference = (unrounded / tick).quantize(
                    Decimal("1"),
                    rounding=ROUND_HALF_UP,
                ) * tick
                event_formula_id = "cash_share_price_grid_v1"
                rounding_provenance = "exchange_rule_round_half_up"
                reference_price_id: str | None = None
            else:
                reference_price_id = str(reference_price_id_raw)
                reference_artifact = _artifact_for_id(
                    artifacts,
                    reference_price_id,
                    label=f"daily official reference {symbol} {ex_date}",
                )
                if reference_artifact["source_kind"] != "audited_external_response":
                    raise ValueError(
                        f"daily official reference is not an exchange response: {symbol} {ex_date}"
                    )
                _require_artifact_fields(
                    reference_artifact,
                    {"ex_reference_price"},
                    label=f"daily official reference {symbol} {ex_date}",
                )
                reference = Decimal(
                    str(reference_artifact["request_parameters"]["ex_reference_price"])
                )
                event_formula_id = "official_reference_price_ratio_v1"
                rounding_provenance = "exchange_published_reference_price_local_rounding_none"
                evidence_ids.add(reference_price_id)
            multiplier = float(pre_close / reference)
            product *= multiplier
            event = {
                "event_type": expected_event["event_type"],
                "ex_date": ex_date,
                "event_formula_id": event_formula_id,
                "pre_close": float(pre_close),
                "cash_dividend_per_share": float(cash),
                "share_distribution_per_share": float(shares),
                "price_tick": float(tick),
                "ex_reference_price": float(reference),
                "event_multiplier": multiplier,
                "announcement_evidence_id": announcement_id,
                "pre_close_evidence_id": "frozen-local-preclose",
                "price_tick_evidence_id": price_tick_id,
                "reference_price_evidence_id": reference_price_id,
                "rounding_provenance": rounding_provenance,
            }
            operands.append(
                _operand(
                    name=f"event_multiplier_{event_number}",
                    value=multiplier,
                    line_item="derived_event_multiplier",
                    evidence_id=announcement_id,
                    event=event,
                    quantum=0.0,
                    unit="ratio",
                )
            )
            evidence_ids.add(announcement_id)
            evidence_ids.add(price_tick_id)

        window = source["event_window"]
        if not isinstance(window, dict) or set(window) != {
            "start_date",
            "end_date",
            "inventory_pages",
            "raw_total_records",
            "taxonomy_version",
            "classification_summary",
            "classification_sha256",
        }:
            raise ValueError(f"daily event window fields differ for {key}")
        expected_start = trade_date
        expected_end = str(current["adj_anchor_date"])
        if (
            str(window["start_date"]) != expected_start
            or str(window["end_date"]) != expected_end
            or window["taxonomy_version"] != ADJUSTMENT_EVENT_TAXONOMY_VERSION
        ):
            raise ValueError(f"daily inventory window does not cover frozen window for {key}")
        expected_classification_fields = {
            "implemented_adjustment_event",
            "not_factor_adjustment",
            "not_adjustment_related",
            "unknown_adjustment_candidate",
        }
        classification_summary = window["classification_summary"]
        if (
            not isinstance(classification_summary, dict)
            or set(classification_summary) != expected_classification_fields
        ):
            raise ValueError(f"daily inventory classification summary differs for {key}")
        pages = window["inventory_pages"]
        if not isinstance(pages, list) or not pages:
            raise ValueError(f"daily inventory pages are absent for {key}")
        page_records: list[dict[str, Any]] = []
        seen_announcement_ids: set[str] = set()
        classified_action_paths: set[str] = set()
        classification_records: list[dict[str, Any]] = []
        for expected_page_number, page in enumerate(pages, start=1):
            if not isinstance(page, dict) or set(page) != {
                "page_number",
                "evidence_id",
                "reported_records",
                "raw_response_records",
                "not_factor_adjustment_records",
                "not_adjustment_related_records",
                "unknown_adjustment_candidate_records",
            }:
                raise ValueError(f"daily inventory page fields differ for {key}")
            if int(page["page_number"]) != expected_page_number:
                raise ValueError(f"daily inventory pages are not consecutive for {key}")
            inventory_id = str(page["evidence_id"])
            inventory = _artifact_for_id(
                artifacts,
                inventory_id,
                label=f"daily inventory {key}",
            )
            if inventory["source_kind"] != "official_exchange_disclosure":
                raise ValueError(f"daily inventory is not official for {key}")
            _require_artifact_fields(
                inventory,
                {"company_action_inventory_records"},
                label=f"daily inventory {key}",
            )
            request = inventory["request_parameters"]
            original_request = request.get("original_request")
            if (
                str(request.get("symbol") or "") != symbol
                or str(request.get("start_date") or "") != expected_start
                or str(request.get("end_date") or "") != expected_end
                or int(request.get("page_number") or 0) != expected_page_number
                or not isinstance(original_request, dict)
            ):
                raise ValueError(
                    f"daily inventory request is not the exact frozen window "
                    f"for {key} page {expected_page_number}"
                )
            validate_unfiltered_inventory_request(
                original_request,
                symbol=symbol,
                start_date=expected_start,
                end_date=expected_end,
                page_number=expected_page_number,
            )
            raw_total_records, raw_records = parse_official_inventory_page(
                source_files[inventory_id],
                symbol=symbol,
            )
            page_classifications = {
                "implemented_adjustment_event": 0,
                "not_factor_adjustment": 0,
                "not_adjustment_related": 0,
                "unknown_adjustment_candidate": 0,
            }
            for record_id, title, document_path, publish_date in raw_records:
                if record_id in seen_announcement_ids:
                    raise ValueError(f"daily inventory identity/symbol/path is invalid for {key}")
                if not expected_start <= publish_date <= expected_end:
                    raise ValueError(f"daily inventory row lies outside frozen window for {key}")
                seen_announcement_ids.add(record_id)
                category, classification = classify_adjustment_announcement_title(title)
                page_classifications[classification] += 1
                classification_records.append(
                    {
                        "page_number": expected_page_number,
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
            if raw_total_records != int(window["raw_total_records"]) or len(raw_records) != int(
                page["raw_response_records"]
            ):
                raise ValueError(
                    f"daily inventory raw response counts differ for {key} "
                    f"page {expected_page_number}"
                )
            reported_records = int(page["reported_records"])
            if (
                reported_records != page_classifications["implemented_adjustment_event"]
                or int(page["not_factor_adjustment_records"])
                != page_classifications["not_factor_adjustment"]
                or int(page["not_adjustment_related_records"])
                != page_classifications["not_adjustment_related"]
                or int(page["unknown_adjustment_candidate_records"])
                != page_classifications["unknown_adjustment_candidate"]
                or page_classifications["unknown_adjustment_candidate"] != 0
            ):
                raise ValueError(f"daily inventory classified action count differs for {key}")
            page_records.append(
                {
                    "page_number": expected_page_number,
                    "evidence_id": inventory_id,
                    "reported_records": reported_records,
                    "raw_response_records": len(raw_records),
                    "not_factor_adjustment_records": page_classifications["not_factor_adjustment"],
                    "not_adjustment_related_records": page_classifications[
                        "not_adjustment_related"
                    ],
                    "unknown_adjustment_candidate_records": page_classifications[
                        "unknown_adjustment_candidate"
                    ],
                }
            )
            evidence_ids.add(inventory_id)
        if sum(int(page["reported_records"]) for page in page_records) != len(expected_events):
            raise ValueError(f"daily inventory action count differs from fixed events for {key}")
        if sum(int(page["raw_response_records"]) for page in pages) != int(
            window["raw_total_records"]
        ):
            raise ValueError(f"daily inventory pagination does not cover every raw row for {key}")
        if len(seen_announcement_ids) != int(window["raw_total_records"]):
            raise ValueError(f"daily inventory contains duplicate/missing rows for {key}")
        observed_classification_summary = {
            "implemented_adjustment_event": sum(
                int(page["reported_records"]) for page in page_records
            ),
            "not_factor_adjustment": sum(
                int(page["not_factor_adjustment_records"]) for page in page_records
            ),
            "not_adjustment_related": sum(
                int(page["not_adjustment_related_records"]) for page in page_records
            ),
            "unknown_adjustment_candidate": sum(
                int(page["unknown_adjustment_candidate_records"]) for page in page_records
            ),
        }
        if observed_classification_summary != classification_summary:
            raise ValueError(f"daily inventory classification totals differ for {key}")
        if canonical_sha256(classification_records) != str(window["classification_sha256"]):
            raise ValueError(f"daily inventory classification digest differs for {key}")
        if len(classified_action_paths) != len(expected_events):
            raise ValueError(f"daily inventory action classification differs for {key}")
        for action_path in action_document_paths:
            if not any(
                action_path.endswith(classified_path) or classified_path.endswith(action_path)
                for classified_path in classified_action_paths
            ):
                raise ValueError(f"daily action PDF is not bound to the inventory for {key}")
        event_window = {
            "start_date": expected_start,
            "end_date": expected_end,
            "complete": True,
            "inventory_evidence_ids": [str(page["evidence_id"]) for page in page_records],
            "symbol": symbol,
            "inventory_source": "official_exchange_full_pagination",
            "taxonomy_version": ADJUSTMENT_EVENT_TAXONOMY_VERSION,
            "classification_summary": observed_classification_summary,
            "classification_sha256": canonical_sha256(classification_records),
            "page_count": len(page_records),
            "pages": page_records,
            "total_reported_records": len(expected_events),
            "raw_total_records": int(window["raw_total_records"]),
        }
        result.append(
            {
                "table": "daily_bars",
                "key": {"symbol": symbol, "trade_date": trade_date},
                "verdict": "formula_match",
                "trial_sample_sha256": canonical_sha256(trial),
                "evidence_ids": sorted(evidence_ids),
                "checked_values": trial["checked_values"],
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
                    "event_window": event_window,
                },
            }
        )
    return result


def _financial_samples(
    *,
    source_manifest: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    pit_index: Mapping[tuple[str, tuple[str, ...]], Mapping[str, Any]],
    trial_index: Mapping[tuple[str, tuple[str, ...]], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    source_rows = _source_rows_by_key(
        source_manifest["financial_samples"],
        ("symbol", "report_period", "metric"),
    )
    result: list[dict[str, Any]] = []
    for key in _FINANCIAL_KEYS:
        identity = ("financial_indicators", key)
        source = source_rows[key]
        trial = trial_index[identity]
        current = pit_index[identity]
        evidence_ids = source.get("evidence_ids")
        if set(source) != {
            "symbol",
            "report_period",
            "metric",
            "evidence_ids",
        }:
            raise ValueError(f"source financial sample fields differ for {key}")
        if (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or len(evidence_ids) != len(set(map(str, evidence_ids)))
        ):
            raise ValueError(f"financial evidence IDs are invalid for {key}")
        artifact_ids = [str(item) for item in evidence_ids]
        sample_artifacts = [
            _artifact_for_id(artifacts, item, label=f"financial {key}") for item in artifact_ids
        ]
        if any(
            artifact["source_kind"] not in {"official_exchange_disclosure", "issuer_xbrl"}
            for artifact in sample_artifacts
        ):
            raise ValueError(f"financial evidence is not original disclosure for {key}")

        formula = _FINANCIAL_FORMULAS.get(key)
        if formula is not None:
            if len(artifact_ids) != 1:
                raise ValueError(f"financial formula requires one full report for {key}")
            artifact_id = artifact_ids[0]
            required_line_items = {str(operand[2]) for operand in formula["operands"]}
            _require_artifact_fields(
                sample_artifacts[0],
                required_line_items,
                label=f"financial {key}",
            )
            operands = [
                _operand(
                    name=str(name),
                    value=float(value),
                    line_item=str(line_item),
                    evidence_id=artifact_id,
                )
                for name, value, line_item in formula["operands"]
            ]
            result.append(
                {
                    "table": "financial_indicators",
                    "key": {
                        "symbol": key[0],
                        "report_period": key[1],
                        "metric": key[2],
                    },
                    "verdict": "formula_match",
                    "trial_sample_sha256": canonical_sha256(trial),
                    "evidence_ids": artifact_ids,
                    "checked_values": trial["checked_values"],
                    "formula_proof": {
                        "formula_id": formula["formula_id"],
                        "expression": formula["expression"],
                        "operands": operands,
                        "result": _financial_result(
                            str(formula["formula_id"]),
                            operands,
                        ),
                        "local_storage_quantum": LOCAL_STORAGE_QUANTUM,
                        "event_window": None,
                    },
                }
            )
            continue

        if key[2] != "revenue_yoy" or current.get("value") is not None:
            raise ValueError(f"unsupported expected-unavailable sample: {key}")
        for artifact in sample_artifacts:
            if artifact["missing_state"] not in {"field_absent", "field_null"}:
                raise ValueError(f"revenue evidence does not record exact-field absence for {key}")
            _require_artifact_fields(
                artifact,
                {"营业收入", "营业总收入"},
                label=f"financial unavailable {key}",
            )
        result.append(
            {
                "table": "financial_indicators",
                "key": {
                    "symbol": key[0],
                    "report_period": key[1],
                    "metric": key[2],
                },
                "verdict": "expected_unavailable",
                "trial_sample_sha256": canonical_sha256(trial),
                "evidence_ids": artifact_ids,
                "unavailable_proof": {
                    "cadence_contract": ("semiannual_q2_q4_from_baostock_mb_revenue"),
                    "expected_quarters": [2, 4],
                    "observed_quarter": int(key[1][-1]),
                    "local_value": None,
                    "payload_reason": "missing_current_revenue",
                    "mapping_status": "no_unique_exact_mbrevenue_line_item",
                    "approximate_substitute_used": False,
                    "request_status": "success",
                    "missing_state": str(sample_artifacts[0]["missing_state"]),
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
    return result


def _valuation_samples(
    *,
    source_manifest: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    source_files: Mapping[str, Path],
    trial_index: Mapping[tuple[str, tuple[str, ...]], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    source_rows = _source_rows_by_key(
        source_manifest["valuation_samples"],
        ("symbol", "trade_date"),
    )
    result: list[dict[str, Any]] = []
    for key in _VALUATION_KEYS:
        identity = ("valuation_daily", key)
        source = source_rows[key]
        if set(source) != {"symbol", "trade_date", "evidence_ids"}:
            raise ValueError(f"source valuation sample fields differ for {key}")
        evidence_ids = source["evidence_ids"]
        if not isinstance(evidence_ids, list) or len(evidence_ids) != 1:
            raise ValueError(f"valuation sample requires one raw response for {key}")
        artifact_id = str(evidence_ids[0])
        artifact = _artifact_for_id(
            artifacts,
            artifact_id,
            label=f"valuation {key}",
        )
        if artifact["source_kind"] != "audited_external_response":
            raise ValueError(f"valuation sample is not audited external data for {key}")
        _require_artifact_fields(
            artifact,
            {"pe_ttm", "pb_mrq", "ps_ttm", "source", "available_time"},
            label=f"valuation {key}",
        )
        request = artifact["request_parameters"]
        if (
            str(request.get("symbol") or "") != key[0]
            or str(request.get("trade_date") or "") != key[1]
        ):
            raise ValueError(f"valuation evidence request key differs for {key}")
        trial = trial_index[identity]
        response = _json_object(
            source_files[artifact_id],
            label=f"valuation raw response {key}",
        )
        response_result = response.get("result")
        raw_rows = response_result.get("data") if isinstance(response_result, dict) else None
        if not isinstance(raw_rows, list):
            raise ValueError(f"valuation raw response rows are absent for {key}")
        matching_rows = [
            row
            for row in raw_rows
            if isinstance(row, dict)
            and str(row.get("SECURITY_CODE") or "") == key[0]
            and str(row.get("TRADE_DATE") or "").startswith(key[1])
        ]
        if len(matching_rows) != 1:
            raise ValueError(f"valuation raw response does not uniquely contain {key}")
        raw_target = matching_rows[0]
        checked_values = trial.get("checked_values")
        if not isinstance(checked_values, dict):
            raise ValueError(f"valuation frozen checks are absent for {key}")
        for check_name, raw_name in (
            ("pe_ttm", "PE_TTM"),
            ("pb_mrq", "PB_MRQ"),
            ("ps_ttm", "PS_TTM"),
        ):
            check = checked_values.get(check_name)
            if not isinstance(check, dict) or not math.isclose(
                float(raw_target[raw_name]),
                float(check["external_value"]),
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError(f"valuation raw response {raw_name} differs for {key}")
        result.append(
            {
                "table": "valuation_daily",
                "key": {"symbol": key[0], "trade_date": key[1]},
                "verdict": "numeric_match",
                "trial_sample_sha256": canonical_sha256(trial),
                "evidence_ids": [artifact_id],
                "checked_values": trial["checked_values"],
            }
        )
    return result


def _copy_source_artifacts(
    *,
    staging: Path,
    artifacts: Sequence[Mapping[str, Any]],
    source_files: Mapping[str, Path],
) -> None:
    destination_root = staging / "artifacts"
    destination_root.mkdir()
    for artifact in artifacts:
        artifact_id = str(artifact["id"])
        destination = staging / str(artifact["relative_path"])
        shutil.copyfile(source_files[artifact_id], destination)
        if _sha256(destination) != artifact["sha256"]:
            raise ValueError(f"copied artifact hash changed: {artifact_id}")
        destination.chmod(0o444)


def _copy_raw_source_bundle(*, source_root: Path, staging: Path) -> None:
    destination_root = staging / "raw-source"
    destination_root.mkdir()
    total_bytes = 0
    for source in sorted(source_root.rglob("*")):
        if source.is_symlink():
            raise ValueError(f"source bundle must not contain symlinks: {source}")
        relative = source.relative_to(source_root)
        destination = destination_root / relative
        if source.is_dir():
            destination.mkdir(exist_ok=True)
            continue
        if not source.is_file():
            raise ValueError(f"unsupported source bundle entry: {source}")
        total_bytes += source.stat().st_size
        if total_bytes > MAX_RAW_SOURCE_BYTES:
            raise ValueError(f"raw source bundle exceeds {MAX_RAW_SOURCE_BYTES} bytes")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o444)


def build_pairing_v3_bundle(
    *,
    source_bundle: Path,
    output: Path,
    db: Path,
    preflight: Path = DEFAULT_PREFLIGHT,
    final_trial: Path = DEFAULT_FINAL_TRIAL,
) -> dict[str, Any]:
    source_root = source_bundle.expanduser().resolve()
    if not source_root.is_dir():
        raise ValueError(f"source bundle is not a directory: {source_root}")
    output_path = output.expanduser().resolve()
    try:
        output_path.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise ValueError("--output must not be inside --source-bundle")
    if output_path.exists():
        raise ValueError("--output must not already exist")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _preflight, pit_samples, trial = _load_frozen_inputs(
        preflight.expanduser().resolve(),
        final_trial.expanduser().resolve(),
    )
    raw_source_manifest = _json_object(
        source_root / SOURCE_MANIFEST_NAME,
        label=SOURCE_MANIFEST_NAME,
    )
    if raw_source_manifest.get("schema_version") == GENERAL_SOURCE_MANIFEST_SCHEMA_VERSION:
        generated_at = str(raw_source_manifest.get("generated_at") or "")
        _require_timezone_aware_iso8601(
            generated_at,
            label="general source manifest generated_at",
        )
        generated_timezone = str(raw_source_manifest.get("timezone") or "Asia/Shanghai")
    else:
        generated_at = datetime.now(UTC).isoformat()
        generated_timezone = "UTC"
    source_manifest = _load_source_manifest(
        source_root,
        pit_samples=pit_samples,
    )
    source_artifacts, artifacts_by_id, source_files = _artifact_index(
        source_root,
        source_manifest,
    )
    trial_by_key = _trial_index(trial)
    pit_by_key = _pit_index(pit_samples)
    precloses = _read_precloses(db)

    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output_path.name}.",
            dir=output_path.parent,
        )
    )
    try:
        _copy_raw_source_bundle(source_root=source_root, staging=temporary)
        _copy_source_artifacts(
            staging=temporary,
            artifacts=source_artifacts,
            source_files=source_files,
        )
        copied_source_manifest = temporary / SOURCE_MANIFEST_NAME
        shutil.copyfile(source_root / SOURCE_MANIFEST_NAME, copied_source_manifest)
        copied_source_manifest.chmod(0o444)
        copied_trial = temporary / "final-trial.json"
        shutil.copyfile(final_trial, copied_trial)
        if _sha256(copied_trial) != FROZEN_FINAL_TRIAL_SHA256:
            raise ValueError("copied final trial hash changed")
        copied_trial.chmod(0o444)

        preclose_document = {
            "schema_version": LOCAL_PRECLOSE_SCHEMA_VERSION,
            "pit_manifest_sha256": FROZEN_MANIFEST_SHA256,
            "database_open_mode": "ro",
            "query_only": True,
            "generated_at": generated_at,
            "row_count": 9,
            "rows": precloses,
        }
        preclose_path = temporary / "frozen-local-preclose.json"
        _json_write(preclose_path, preclose_document)
        preclose_sha256 = _sha256(preclose_path)
        preclose_artifact = {
            "id": "frozen-local-preclose",
            "relative_path": "frozen-local-preclose.json",
            "sha256": preclose_sha256,
            "source_kind": "frozen_local_manifest",
            "source_identity": "production-sqlite-daily-bars-prior-trading-day",
            "request_parameters": {
                "pit_manifest_sha256": FROZEN_MANIFEST_SHA256,
                "access_mode": "read_only",
            },
            "retrieved_at": generated_at,
            "timezone": generated_timezone,
            "parser_version": LOCAL_PRECLOSE_SCHEMA_VERSION,
            "actual_fields": [
                "symbol",
                "ex_date",
                "pre_close_trade_date",
                "pre_close",
                "source",
            ],
            "content_scope": "full_response_body",
            "first_success": True,
            "first_success_response_sha256": preclose_sha256,
            "fallback_reason": None,
            "prior_source_errors": [],
            "missing_state": "present",
        }
        all_artifacts = [*source_artifacts, preclose_artifact]
        all_artifacts_by_id = {
            **artifacts_by_id,
            "frozen-local-preclose": preclose_artifact,
        }

        daily_samples = _daily_samples(
            source_manifest=source_manifest,
            artifacts=all_artifacts_by_id,
            source_files=source_files,
            pit_index=pit_by_key,
            trial_index=trial_by_key,
            precloses=precloses,
        )
        financial_samples = _financial_samples(
            source_manifest=source_manifest,
            artifacts=all_artifacts_by_id,
            pit_index=pit_by_key,
            trial_index=trial_by_key,
        )
        valuation_samples = _valuation_samples(
            source_manifest=source_manifest,
            artifacts=all_artifacts_by_id,
            source_files=source_files,
            trial_index=trial_by_key,
        )
        samples = [*daily_samples, *financial_samples, *valuation_samples]
        candidate = {
            "schema_version": PAIRING_V3_SCHEMA_VERSION,
            "adjudication_contract": ADJUDICATION_CONTRACT_VERSION,
            "adjudication_contract_sha256": ADJUDICATION_CONTRACT_SHA256,
            "pit_manifest_schema_version": pit_samples["manifest_schema_version"],
            "pit_manifest_sha256": FROZEN_MANIFEST_SHA256,
            "final_trial": {
                "relative_path": "final-trial.json",
                "sha256": FROZEN_FINAL_TRIAL_SHA256,
            },
            "approved": False,
            "reviewed_at": None,
            "reviewer_role": "pending",
            "seed": FROZEN_SEED,
            "sample_size_per_table": FROZEN_SAMPLE_SIZE_PER_TABLE,
            "artifacts": all_artifacts,
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
        candidate_path = temporary / "pairing-v3-candidate.json"
        _json_write(candidate_path, candidate)
        validation = validate_pairing_v3_candidate(
            candidate,
            evidence_path=candidate_path,
            pit_samples=pit_samples,
        )
        validation_document = {
            "schema_version": MACHINE_VALIDATION_SCHEMA_VERSION,
            "validated_at": generated_at,
            "candidate_file_sha256": _sha256(candidate_path),
            "candidate_canonical_sha256": canonical_sha256(candidate),
            "source_manifest_sha256": _sha256(copied_source_manifest),
            "frozen_local_preclose_sha256": preclose_sha256,
            "preserved_frozen_daily_close_checks": 5,
            "approved": False,
            "reviewer_role": "pending",
            "reviewed_at": None,
            "validation": validation,
        }
        _json_write(temporary / "machine-validation.json", validation_document)
        os.rename(temporary, output_path)
        return validation_document
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = build_pairing_v3_bundle(
            source_bundle=arguments.source_bundle,
            output=arguments.output,
            db=arguments.db,
            preflight=arguments.preflight,
            final_trial=arguments.final_trial,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"pairing-v3 build failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
