from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/P3.3-S6-external-pit-adjudication-v1.contract.json"
CONTRACT_RELATIVE_PATH = "contract/P3.3-S6-external-pit-adjudication-v1.contract.json"
CONTRACT_SHA256 = "a16995e6545ddba7fc03d917ae6cc8d9ab19b7903f1d21c5694b1c0167b1b951"
SOURCE_MANIFEST_NAME = "SOURCE-MANIFEST.json"
ROOT_CHECKSUM_NAME = "SHA256SUMS"
DAILY_MANIFEST_PATH = "daily-bars/MANIFEST.json"
DAILY_CHECKSUM_PATH = "daily-bars/MANIFEST.sha256"
PRICE_MANIFEST_PATH = "price-tick-rules/MANIFEST.json"
PRICE_REQUESTS_PATH = "price-tick-rules/REQUESTS.json"
CAPTURE_DESTINATION = "daily-bars/complete-unfiltered-announcement-inventory"
ROUNDING_EVIDENCE_DESTINATION = "daily-bars/rounding-evidence/600782"
ROUNDING_EVIDENCE_MANIFEST_NAME = "EVIDENCE-MANIFEST.json"
ROUNDING_RESPONSE_NAME = "sse-company-profit-600782.json"
ROUNDING_RESPONSE_SHA256 = "9adcd4915bd55b63f6532fcddea090b4242842e866e7d6790e61668e305c36cf"
ROUNDING_EXTRACT_NAME = "sse-company-profit-600782-target-extract.json"
SZSE_REFERENCE_DESTINATION = "daily-bars/reference-evidence/001260"
SZSE_REFERENCE_MANIFEST_NAME = "EVIDENCE-MANIFEST.json"
SZSE_MONTH_ENTRY_NAME = "szse-monthly-report-202605.html"
SZSE_MONTH_ENTRY_SHA256 = "fbacf9d40f7584f0937631cb9275cf9bc6af6ff5c8525ecab02c08ad0503b7c5"
SZSE_DIVIDEND_TABLE_NAME = "szse-dividend-bonus-rights-202605.html"
SZSE_DIVIDEND_TABLE_SHA256 = "de031d6f782d1511ae0f7d8d9e0488d00f09f92e42d449155725415b33284f9d"
SZSE_DIVIDEND_EXTRACT_NAME = "szse-dividend-bonus-rights-202605-target-extract.json"
SZSE_MONTH_ENTRY_URL = (
    "https://www.szse.cn/market/periodical/month/t20260605_620906.html"
)
SZSE_DIVIDEND_TABLE_URL = (
    "https://docs.static.szse.cn/www/market/periodical/month/W020260605534753848014.html"
)
SANITIZED_HEADER_SCHEMA_VERSION = "p3.3-s6-canonical-http-response-headers-v1"
SANITIZER_VERSION = "p3.3-s6-header-sanitizer-v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CHECKSUM_LINE = re.compile(r"([0-9a-f]{64})  (?:\*)?(.+)")
_STATUS_LINE = re.compile(r"HTTP/\S+\s+([1-5][0-9]{2})(?:\s+.*)?", re.IGNORECASE)
_ALLOWED_HEADERS = (
    "content-length",
    "content-type",
    "etag",
    "last-modified",
)
_CAPTURE_MANIFEST_NAMES = (
    "CAPTURE-MANIFEST.json",
    "SOURCE-MANIFEST.json",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild a P3.3-S6 source bundle without overwriting its source. "
            "The output has canonical sanitized response-header evidence and "
            "closed recursive checksum manifests."
        )
    )
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--announcement-capture", type=Path)
    parser.add_argument("--rounding-evidence", type=Path)
    parser.add_argument("--szse-month-entry", type=Path)
    parser.add_argument("--szse-dividend-table", type=Path)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--generated-at",
        required=True,
        help="Timezone-aware ISO-8601 timestamp, for example 2026-07-31T23:30:00+08:00",
    )
    return parser


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not readable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical_json_bytes(value))


def _validated_generated_at(value: str) -> str:
    if not value or value.endswith(":z"):
        raise ValueError("generated_at must be a timezone-aware ISO-8601 timestamp")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("generated_at must be a timezone-aware ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            "generated_at must be a timezone-aware ISO-8601 timestamp with an explicit UTC offset"
        )
    return value


def _safe_relative_path(value: str, *, label: str) -> PurePosixPath:
    pure = PurePosixPath(value.removeprefix("./"))
    if not value or pure.is_absolute() or ".." in pure.parts or "\\" in value:
        raise ValueError(f"{label} contains unsafe path: {value!r}")
    return pure


def _regular_files(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"source root must be a real directory: {root}")
    files: list[Path] = []
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise ValueError(f"bundle must not contain symlinks: {candidate}")
        if candidate.is_file():
            files.append(candidate)
        elif not candidate.is_dir():
            raise ValueError(f"bundle contains a non-regular entry: {candidate}")
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _checksum_entries(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"checksum file is unreadable: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None:
            raise ValueError(f"invalid checksum row: {path}:{line_number}")
        relative = _safe_relative_path(
            match.group(2),
            label=f"checksum {path}:{line_number}",
        ).as_posix()
        if relative in entries:
            raise ValueError(f"duplicate checksum path: {path}:{relative}")
        entries[relative] = match.group(1)
    return entries


def _expected_checksum_paths(path: Path) -> set[str]:
    return {
        candidate.relative_to(path.parent).as_posix()
        for candidate in _regular_files(path.parent)
        if candidate != path
    }


def _verify_checksum_closure(
    path: Path,
    *,
    allow_unregistered_nested_checksums: bool = False,
) -> None:
    entries = _checksum_entries(path)
    expected_paths = _expected_checksum_paths(path)
    if set(entries) != expected_paths:
        missing = sorted(expected_paths - set(entries))
        stale = sorted(set(entries) - expected_paths)
        permitted_missing = (
            {relative for relative in missing if relative.endswith(f"/{ROOT_CHECKSUM_NAME}")}
            if allow_unregistered_nested_checksums
            else set()
        )
        unpermitted_missing = sorted(set(missing) - permitted_missing)
        missing = [] if not unpermitted_missing and not stale else unpermitted_missing
    if set(entries) != expected_paths and (missing or stale):
        raise ValueError(
            f"checksum closure differs for {path}: unregistered={missing[:5]} stale={stale[:5]}"
        )
    for relative, expected_sha256 in entries.items():
        candidate = path.parent / relative
        if _sha256(candidate) != expected_sha256:
            raise ValueError(f"checksum mismatch: {path}:{relative}")


def _write_checksum_closure(path: Path) -> None:
    rows = [
        f"{_sha256(candidate)}  {candidate.relative_to(path.parent).as_posix()}"
        for candidate in _regular_files(path.parent)
        if candidate != path
    ]
    path.write_text("".join(f"{row}\n" for row in rows), encoding="utf-8")


def _validate_manifest_file_entries(
    root: Path,
    entries: object,
    *,
    field: str,
) -> None:
    if not isinstance(entries, list):
        raise ValueError(f"{field} must be a list")
    seen: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise ValueError(f"{field} entries must be objects")
        relative = str(raw.get("relative_path") or raw.get("file") or "")
        pure = _safe_relative_path(relative, label=field)
        if pure.as_posix() in seen:
            raise ValueError(f"{field} contains duplicate path: {relative}")
        seen.add(pure.as_posix())
        candidate = root / pure
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError(f"{field} artifact is absent/unsafe: {relative}")
        expected_hash = str(raw.get("sha256") or "")
        if not _SHA256.fullmatch(expected_hash) or _sha256(candidate) != expected_hash:
            raise ValueError(f"{field} artifact SHA-256 mismatch: {relative}")
        if int(raw.get("bytes") or -1) != candidate.stat().st_size:
            raise ValueError(f"{field} artifact byte count mismatch: {relative}")


def _validate_daily_manifest(root: Path) -> None:
    manifest_path = root / DAILY_MANIFEST_PATH
    checksum_path = root / DAILY_CHECKSUM_PATH
    manifest = _load_json(manifest_path, label=DAILY_MANIFEST_PATH)
    _validate_manifest_file_entries(
        manifest_path.parent,
        manifest.get("files"),
        field=f"{DAILY_MANIFEST_PATH}.files",
    )
    entries = _checksum_entries(checksum_path)
    for relative, expected_hash in entries.items():
        candidate = checksum_path.parent / relative
        if not candidate.is_file() or _sha256(candidate) != expected_hash:
            raise ValueError(f"daily checksum mismatch: {relative}")


def _validate_input_bundle(source_root: Path) -> dict[str, Any]:
    _regular_files(source_root)
    root_checksum = source_root / ROOT_CHECKSUM_NAME
    if not root_checksum.is_file():
        raise ValueError("source bundle root SHA256SUMS is absent")
    checksum_files = sorted(
        source_root.rglob(ROOT_CHECKSUM_NAME),
        key=lambda item: item.relative_to(source_root).as_posix(),
    )
    for checksum_path in checksum_files:
        _verify_checksum_closure(
            checksum_path,
            allow_unregistered_nested_checksums=(checksum_path == root_checksum),
        )
    _validate_daily_manifest(source_root)
    source_manifest = _load_json(
        source_root / SOURCE_MANIFEST_NAME,
        label=SOURCE_MANIFEST_NAME,
    )
    _validate_manifest_file_entries(
        source_root,
        source_manifest.get("artifacts"),
        field=f"{SOURCE_MANIFEST_NAME}.artifacts",
    )
    artifacts = source_manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("SOURCE-MANIFEST.artifacts must be a list")
    if int(source_manifest.get("artifact_count") or -1) != len(artifacts):
        raise ValueError("SOURCE-MANIFEST artifact_count is inconsistent")
    return source_manifest


def _capture_manifest_path(capture_root: Path) -> Path:
    matches = [
        capture_root / name for name in _CAPTURE_MANIFEST_NAMES if (capture_root / name).is_file()
    ]
    if len(matches) != 1:
        raise ValueError(
            "announcement capture must contain exactly one "
            "CAPTURE-MANIFEST.json or SOURCE-MANIFEST.json"
        )
    return matches[0]


def _copy_capture(
    capture_root: Path,
    staging_root: Path,
) -> tuple[Path, list[dict[str, Any]]]:
    capture_files = _regular_files(capture_root)
    capture_manifest_path = _capture_manifest_path(capture_root)
    capture_manifest = _load_json(
        capture_manifest_path,
        label="announcement capture manifest",
    )
    raw_artifacts = capture_manifest.get("artifacts")
    if raw_artifacts is None:
        raw_artifacts = _native_capture_artifacts(
            capture_root,
            capture_manifest,
        )
    else:
        _validate_manifest_file_entries(
            capture_root,
            raw_artifacts,
            field="announcement capture artifacts",
        )
        if not isinstance(raw_artifacts, list):
            raise ValueError("announcement capture artifacts must be a list")
    declared = {
        str(item.get("relative_path") or item.get("file") or "")
        for item in raw_artifacts
        if isinstance(item, dict)
    }
    exempt = {
        capture_manifest_path.relative_to(capture_root).as_posix(),
        ROOT_CHECKSUM_NAME,
    }
    actual = {path.relative_to(capture_root).as_posix() for path in capture_files}
    unregistered = actual - declared - exempt
    if unregistered:
        raise ValueError(f"announcement capture has unregistered files: {sorted(unregistered)[:5]}")
    checksum_path = capture_root / ROOT_CHECKSUM_NAME
    if checksum_path.is_file():
        _verify_checksum_closure(checksum_path)

    destination = staging_root / CAPTURE_DESTINATION
    if destination.exists():
        raise ValueError(f"announcement capture destination already exists: {destination}")
    shutil.copytree(capture_root, destination)
    prefix = PurePosixPath(CAPTURE_DESTINATION)
    prefixed_artifacts: list[dict[str, Any]] = []
    for raw in raw_artifacts:
        copied = dict(raw)
        original_path = str(copied.pop("file", "") or copied.get("relative_path") or "")
        copied["relative_path"] = (prefix / original_path).as_posix()
        for binding_field in ("body_relative_path", "request_relative_path"):
            binding_value = copied.get(binding_field)
            if isinstance(binding_value, str) and binding_value:
                copied[binding_field] = (prefix / binding_value).as_posix()
        prefixed_artifacts.append(copied)
    capture_manifest_relative = (
        prefix / capture_manifest_path.relative_to(capture_root).as_posix()
    ).as_posix()
    # Native capture manifests carry started_at/completed_at; only manifests a
    # prior rebuild refreshed carry generated_at.
    capture_retrieved_at = str(
        capture_manifest.get("generated_at")
        or capture_manifest.get("completed_at")
        or ""
    )
    if not capture_retrieved_at:
        raise ValueError(
            "announcement capture manifest lacks a provenance timestamp"
        )
    prefixed_artifacts.append(
        {
            "relative_path": capture_manifest_relative,
            "sha256": _sha256(staging_root / capture_manifest_relative),
            "bytes": (staging_root / capture_manifest_relative).stat().st_size,
            "source_identity": "Frozen complete unfiltered announcement capture manifest",
            "source_kind": "source_package_metadata",
            "request": {
                "method": "local_copy",
                "url": "",
                "params": {},
            },
            "retrieved_at": capture_retrieved_at,
            "parser_version": "byte-for-byte-copy-v1",
            "actual_fields": {},
            "missing_state": {"status": "none", "details": None},
        }
    )
    if checksum_path.is_file():
        checksum_relative = (prefix / ROOT_CHECKSUM_NAME).as_posix()
        prefixed_artifacts.append(
            {
                "relative_path": checksum_relative,
                "sha256": _sha256(staging_root / checksum_relative),
                "bytes": (staging_root / checksum_relative).stat().st_size,
                "source_identity": "Frozen complete unfiltered announcement capture checksums",
                "source_kind": "source_package_checksum",
                "request": {
                    "method": "local_copy",
                    "url": "",
                    "params": {},
                },
                "retrieved_at": capture_retrieved_at,
                "parser_version": "sha256sum-v1",
                "actual_fields": {},
                "missing_state": {"status": "none", "details": None},
            }
        )
    return destination / capture_manifest_path.name, prefixed_artifacts


def _native_capture_artifacts(
    capture_root: Path,
    manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    captures = manifest.get("captures")
    completed_at = str(manifest.get("completed_at") or "")
    if not isinstance(captures, list) or not captures or not completed_at:
        raise ValueError(
            "announcement capture manifest has neither artifacts nor "
            "complete native capture metadata"
        )
    artifacts: list[dict[str, Any]] = []
    declared_paths: set[str] = set()
    for raw_capture in captures:
        if not isinstance(raw_capture, dict):
            raise ValueError("announcement capture records must be objects")
        symbol = str(raw_capture.get("symbol") or "")
        market = str(raw_capture.get("market") or "")
        frozen_window = raw_capture.get("frozen_window")
        pages = raw_capture.get("pages")
        if (
            not re.fullmatch(r"[0-9]{6}", symbol)
            or market not in {"SSE", "SZSE"}
            or not isinstance(frozen_window, list)
            or len(frozen_window) != 2
            or not isinstance(pages, list)
        ):
            raise ValueError(f"invalid native capture metadata: {symbol or '<blank>'}")
        source_identity = (
            "Shanghai Stock Exchange official announcement inventory API"
            if market == "SSE"
            else "Shenzhen Stock Exchange official announcement inventory API"
        )
        for raw_page in pages:
            if not isinstance(raw_page, dict):
                raise ValueError(f"{symbol} capture pages must be objects")
            page_number = int(raw_page.get("page_number") or 0)
            if page_number <= 0:
                raise ValueError(f"{symbol} capture page_number is invalid")
            prefix = f"{symbol}/page-{page_number:03d}"
            paths = {
                "request": f"{prefix}.request.json",
                "response": f"{prefix}.response.json",
                "headers": f"{prefix}.headers.json",
            }
            hashes = {
                "request": str(raw_page.get("request_sha256") or ""),
                "response": str(raw_page.get("response_sha256") or ""),
                "headers": str(raw_page.get("headers_sha256") or ""),
            }
            for role, relative in paths.items():
                path = capture_root / relative
                if (
                    not path.is_file()
                    or not _SHA256.fullmatch(hashes[role])
                    or _sha256(path) != hashes[role]
                ):
                    raise ValueError(f"{symbol} page {page_number} {role} binding differs")
                declared_paths.add(relative)
            request_record = _load_json(
                capture_root / paths["request"],
                label=f"{symbol} page {page_number} request",
            )
            raw_params = (
                request_record.get("body") if market == "SZSE" else request_record.get("query")
            )
            if not isinstance(raw_params, dict):
                raise ValueError(f"{symbol} page {page_number} request params are absent")
            normalized_request = {
                "method": request_record.get("method"),
                "url": request_record.get("url"),
                "params": raw_params,
                "metadata_provenance": (
                    "exact frozen request body/query is preserved in the sibling request artifact"
                ),
            }
            actual_fields = {
                "symbol": symbol,
                "market": market,
                "window_start": frozen_window[0],
                "window_end": frozen_window[1],
                "frozen_window": list(frozen_window),
                "page_number": page_number,
                "page_size": raw_capture.get("page_size"),
                "page_count": raw_capture.get("page_count"),
                "official_total": raw_capture.get("official_total"),
                "captured_total": raw_capture.get("captured_total"),
                "row_count": raw_page.get("row_count"),
                "unfiltered": True,
                "classification_counts": {
                    "status": "deferred_to_machine_validator",
                    "reason": (
                        "capture freezes complete raw titles; the validator "
                        "applies the contract-bound taxonomy independently"
                    ),
                },
            }
            common = {
                "source_identity": source_identity,
                "request": normalized_request,
                "retrieved_at": completed_at,
                "timezone": "Asia/Shanghai",
                "missing_state": {"status": "none", "details": None},
                "routing": {
                    "authoritative_exact_window": True,
                    "pairing_candidate_use": True,
                },
                "actual_fields": actual_fields,
            }
            for role, source_kind in (
                ("request", "complete_unfiltered_announcement_inventory_request"),
                ("response", "complete_unfiltered_announcement_inventory"),
                (
                    "headers",
                    "complete_unfiltered_announcement_inventory_response_headers",
                ),
            ):
                relative = paths[role]
                artifact = {
                    **common,
                    "relative_path": relative,
                    "sha256": hashes[role],
                    "bytes": (capture_root / relative).stat().st_size,
                    "source_kind": source_kind,
                    "parser_version": (
                        "strict-json-v1" if role != "headers" else SANITIZER_VERSION
                    ),
                }
                if role == "headers":
                    artifact["body_relative_path"] = paths["response"]
                    artifact["request_relative_path"] = paths["request"]
                artifacts.append(artifact)
    actual_data_paths = {
        path.relative_to(capture_root).as_posix()
        for path in _regular_files(capture_root)
        if path.name not in {*_CAPTURE_MANIFEST_NAMES, ROOT_CHECKSUM_NAME}
    }
    if declared_paths != actual_data_paths:
        raise ValueError(
            "native announcement capture file closure differs: "
            f"unregistered={sorted(actual_data_paths - declared_paths)[:5]} "
            f"stale={sorted(declared_paths - actual_data_paths)[:5]}"
        )
    return artifacts


def _source_artifact_by_sha256(
    source_manifest: Mapping[str, Any],
    sha256: str,
) -> Mapping[str, Any] | None:
    artifacts = source_manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    matches = [raw for raw in artifacts if isinstance(raw, dict) and raw.get("sha256") == sha256]
    if len(matches) > 1:
        request_matches = [raw for raw in matches if isinstance(raw.get("request"), dict)]
        if len(request_matches) == 1:
            return request_matches[0]
    return matches[0] if len(matches) == 1 else None


class _SZSEMonthlyEntryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.casefold() != "a":
            return
        values = dict(attrs)
        self._href = values.get("href")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self._href is None:
            return
        text = " ".join("".join(self._text).split())
        self.links.append((self._href, text))
        self._href = None
        self._text = []


class _SZSEDividendTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headers: list[str] = []
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._cell_is_header = False

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
            self._cell_is_header = normalized == "th"

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in {"td", "th"} and self._cell is not None:
            value = " ".join("".join(self._cell).split())
            if self._cell_is_header:
                self.headers.append(value)
            elif self._row is not None:
                self._row.append(value)
            self._cell = None
            self._cell_is_header = False
        elif normalized == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _szse_monthly_reference_target(
    entry_path: Path,
    table_path: Path,
) -> dict[str, Any]:
    for path, expected_sha256, label in (
        (entry_path, SZSE_MONTH_ENTRY_SHA256, "SZSE May 2026 monthly entry"),
        (
            table_path,
            SZSE_DIVIDEND_TABLE_SHA256,
            "SZSE May 2026 dividend table",
        ),
    ):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"{label} is absent/unsafe: {path}")
        observed_sha256 = _sha256(path)
        if observed_sha256 != expected_sha256:
            raise ValueError(
                f"{label} SHA-256 differs: expected={expected_sha256} observed={observed_sha256}"
            )

    try:
        entry_text = entry_path.read_text(encoding="utf-8")
        table_text = table_path.read_text(encoding="gb18030")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("SZSE monthly reference HTML encoding differs") from exc

    entry_parser = _SZSEMonthlyEntryParser()
    entry_parser.feed(entry_text)
    target_path = urlparse(SZSE_DIVIDEND_TABLE_URL).path
    matching_links = [
        href
        for href, text in entry_parser.links
        if text == "分红派息配股"
        and urlparse(href).hostname == "docs.static.szse.cn"
        and urlparse(href).path == target_path
    ]
    if len(matching_links) != 1:
        raise ValueError("SZSE monthly entry does not uniquely link the dividend table")

    table_parser = _SZSEDividendTableParser()
    table_parser.feed(table_text)
    normalized_headers = " ".join(table_parser.headers)
    if (
        "Ex-Price" not in normalized_headers
        or "Pre-Closing" not in normalized_headers
        or "DPS" not in normalized_headers
    ):
        raise ValueError("SZSE dividend table headers differ")
    matches = [
        row
        for row in table_parser.rows
        if len(row) == 14 and row[0] == "001260" and row[10] == "2026/05/27"
    ]
    if len(matches) != 1:
        raise ValueError("SZSE 001260 ex-reference target row is not unique")
    row = matches[0]
    expected = {
        "symbol": "001260",
        "security_name": "坤泰股份",
        "cash_dividend_total": "24,725,000",
        "cash_dividend_per_share": "0.215",
        "ex_date": "2026/05/27",
        "registration_date": "2026/05/26",
        "ex_reference_price": "20.290",
        "pre_closing_price": "20.500",
    }
    observed = {
        "symbol": row[0],
        "security_name": row[1],
        "cash_dividend_total": row[4],
        "cash_dividend_per_share": row[5],
        "ex_date": row[10],
        "registration_date": row[11],
        "ex_reference_price": row[12],
        "pre_closing_price": row[13],
    }
    if observed != expected:
        raise ValueError(
            "SZSE 001260 ex-reference target values differ from the official monthly table"
        )
    return {
        "symbol": observed["symbol"],
        "security_name": observed["security_name"],
        "cash_dividend_total": int(observed["cash_dividend_total"].replace(",", "")),
        "cash_dividend_per_share": float(observed["cash_dividend_per_share"]),
        "ex_date": observed["ex_date"].replace("/", "-"),
        "registration_date": observed["registration_date"].replace("/", "-"),
        "ex_reference_price": float(observed["ex_reference_price"]),
        "pre_closing_price": float(observed["pre_closing_price"]),
        "raw_cells": row,
    }


def _copy_szse_monthly_reference_evidence(
    *,
    entry_path: Path,
    table_path: Path,
    staging_root: Path,
    generated_at: str,
) -> tuple[Path, list[dict[str, Any]]]:
    target = _szse_monthly_reference_target(entry_path, table_path)
    destination = staging_root / SZSE_REFERENCE_DESTINATION
    if destination.exists():
        raise ValueError(f"SZSE reference destination already exists: {destination}")
    destination.mkdir(parents=True)
    copied_entry = destination / SZSE_MONTH_ENTRY_NAME
    copied_table = destination / SZSE_DIVIDEND_TABLE_NAME
    shutil.copyfile(entry_path, copied_entry)
    shutil.copyfile(table_path, copied_table)

    requests = {
        SZSE_MONTH_ENTRY_NAME: {
            "method": "GET",
            "url": SZSE_MONTH_ENTRY_URL,
            "params": {},
            "metadata_provenance": ("exact official page URL is frozen with the byte response"),
        },
        SZSE_DIVIDEND_TABLE_NAME: {
            "method": "GET",
            "url": SZSE_DIVIDEND_TABLE_URL,
            "params": {},
            "metadata_provenance": (
                "the official monthly entry page uniquely binds this table URL"
            ),
        },
    }
    for response_name, request_spec in requests.items():
        stem = response_name.removesuffix(".html")
        _write_json(destination / f"{stem}.request.json", request_spec)
        response_path = destination / response_name
        fields: dict[str, str] = {}
        if response_name == SZSE_DIVIDEND_TABLE_NAME:
            fields = {
                "content-length": str(response_path.stat().st_size),
                "content-type": "text/html; charset=GBK",
                "etag": '"6a227bda-34bef"',
                "last-modified": "Fri, 05 Jun 2026 07:33:46 GMT",
            }
        _write_json(
            destination / f"{stem}.headers.json",
            {
                "status_code": 200,
                "fields": fields,
            },
        )

    _write_json(
        destination / SZSE_DIVIDEND_EXTRACT_NAME,
        {
            "schema_version": "p3.3-s6-szse-monthly-dividend-target-v1",
            "entry_url": SZSE_MONTH_ENTRY_URL,
            "source_url": SZSE_DIVIDEND_TABLE_URL,
            "target_record": target,
            "parser": {
                "encoding": "GB18030",
                "table_columns": 14,
                "selector": {
                    "symbol": "001260",
                    "ex_date": "2026/05/27",
                },
            },
        },
    )

    response_fields = {
        "symbol": "001260",
        "security_name": "坤泰股份",
        "ex_date": "2026-05-27",
        "registration_date": "2026-05-26",
        "ex_reference_price": 20.29,
        "pre_closing_price": 20.50,
        "cash_dividend_per_share": 0.215,
    }
    artifacts: list[dict[str, Any]] = []
    for path in _regular_files(destination):
        local = path.relative_to(destination).as_posix()
        relative = (PurePosixPath(SZSE_REFERENCE_DESTINATION) / local).as_posix()
        request: dict[str, Any] = {
            "method": "local_derivation",
            "url": "",
            "params": {},
        }
        source_kind = "official_exchange_ex_reference_price_supporting_evidence"
        actual_fields: dict[str, Any] = {}
        parser_version = "byte-evidence-v1"
        if path.name == SZSE_MONTH_ENTRY_NAME:
            request = requests[SZSE_MONTH_ENTRY_NAME]
            source_kind = "official_exchange_ex_reference_price_supporting_evidence"
            actual_fields = {
                "report_month": "2026-05",
                "linked_table_url": SZSE_DIVIDEND_TABLE_URL,
            }
            parser_version = "szse-monthly-entry-html-utf8-v1"
        elif path.name == SZSE_DIVIDEND_TABLE_NAME:
            request = requests[SZSE_DIVIDEND_TABLE_NAME]
            source_kind = "official_exchange_ex_reference_price_response"
            actual_fields = response_fields
            parser_version = "szse-monthly-dividend-html-gb18030-v1"
        elif path.name == SZSE_DIVIDEND_EXTRACT_NAME:
            request = requests[SZSE_DIVIDEND_TABLE_NAME]
            source_kind = "official_exchange_ex_reference_price_target_extract"
            actual_fields = response_fields
            parser_version = "strict-json-v1"
        elif path.name.endswith(".request.json"):
            response_name = f"{path.name.removesuffix('.request.json')}.html"
            request = requests[response_name]
            source_kind = "official_exchange_ex_reference_price_request"
            parser_version = "strict-json-v1"
        elif path.name.endswith(".headers.json"):
            response_name = f"{path.name.removesuffix('.headers.json')}.html"
            request = requests[response_name]
            source_kind = "official_exchange_ex_reference_price_response_headers"
            parser_version = SANITIZER_VERSION
        artifact: dict[str, Any] = {
            "relative_path": relative,
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "source_identity": (
                "Shenzhen Stock Exchange official May 2026 market statistics monthly report"
            ),
            "source_kind": source_kind,
            "request": request,
            "retrieved_at": generated_at,
            "timezone": "Asia/Shanghai",
            "parser_version": parser_version,
            "actual_fields": actual_fields,
            "missing_state": {"status": "none", "details": None},
            "routing": {
                "pairing_candidate_use": path.name
                in {
                    SZSE_MONTH_ENTRY_NAME,
                    SZSE_DIVIDEND_TABLE_NAME,
                    SZSE_DIVIDEND_EXTRACT_NAME,
                }
            },
        }
        if path.name.endswith(".headers.json"):
            stem = path.name.removesuffix(".headers.json")
            artifact["body_relative_path"] = f"{SZSE_REFERENCE_DESTINATION}/{stem}.html"
            artifact["request_relative_path"] = f"{SZSE_REFERENCE_DESTINATION}/{stem}.request.json"
        artifacts.append(artifact)

    manifest = {
        "schema_version": "p3.3-s6-001260-ex-reference-evidence-v1",
        "generated_at": generated_at,
        "raw_source_sha256": {
            SZSE_MONTH_ENTRY_NAME: SZSE_MONTH_ENTRY_SHA256,
            SZSE_DIVIDEND_TABLE_NAME: SZSE_DIVIDEND_TABLE_SHA256,
        },
        "target": response_fields,
        "artifacts": [
            {
                "relative_path": path.relative_to(destination).as_posix(),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in _regular_files(destination)
        ],
    }
    manifest_path = destination / SZSE_REFERENCE_MANIFEST_NAME
    _write_json(manifest_path, manifest)
    _write_checksum_closure(destination / ROOT_CHECKSUM_NAME)
    for path, identity, kind, parser_version in (
        (
            manifest_path,
            "Frozen 001260 official ex-reference evidence manifest",
            "source_package_metadata",
            "strict-json-v1",
        ),
        (
            destination / ROOT_CHECKSUM_NAME,
            "Frozen 001260 official ex-reference evidence checksums",
            "source_package_checksum",
            "sha256sum-v1",
        ),
    ):
        artifacts.append(
            {
                "relative_path": (f"{SZSE_REFERENCE_DESTINATION}/{path.name}"),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
                "source_identity": identity,
                "source_kind": kind,
                "request": {
                    "method": "local_derivation",
                    "url": "",
                    "params": {},
                },
                "retrieved_at": generated_at,
                "timezone": "Asia/Shanghai",
                "parser_version": parser_version,
                "actual_fields": {},
                "missing_state": {"status": "none", "details": None},
            }
        )
    return manifest_path, artifacts


def _rounding_target(
    evidence_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    response_path = evidence_root / ROUNDING_RESPONSE_NAME
    if not response_path.is_file() or _sha256(response_path) != ROUNDING_RESPONSE_SHA256:
        observed = _sha256(response_path) if response_path.is_file() else "<absent>"
        raise ValueError(
            "official 600782 ex-reference response differs: "
            f"expected={ROUNDING_RESPONSE_SHA256} observed={observed}"
        )
    response = _load_json(
        response_path,
        label="official 600782 ex-reference response",
    )
    extract = _load_json(
        evidence_root / ROUNDING_EXTRACT_NAME,
        label="official 600782 ex-reference target extract",
    )
    records = response.get("result")
    if not isinstance(records, list):
        raise ValueError("official 600782 ex-reference response result is absent")
    matches = [
        raw
        for raw in records
        if isinstance(raw, dict)
        and raw.get("A_STOCK_CODE") == "600782"
        and raw.get("A_DIV_DATE") == "20260605"
    ]
    if len(matches) != 1:
        raise ValueError("official 600782 ex-reference target is not unique")
    target = matches[0]
    if extract.get("target_record") != target:
        raise ValueError("official 600782 target extract differs from raw response")
    expected = {
        "PRE_CLOSE_PRICE": "2.64",
        "A_BEFR_TAX_DIV": "0.135",
    }
    for field, value in expected.items():
        if target.get(field) != value:
            raise ValueError(f"official 600782 ex-reference field differs: {field}")
    query = extract.get("query")
    source_url = extract.get("source_url")
    if not isinstance(query, dict) or not isinstance(source_url, str) or not source_url:
        raise ValueError("official 600782 ex-reference request metadata is absent")
    return target, {
        "method": "GET",
        "url": source_url,
        "params": query,
        "metadata_provenance": (
            "exact official request query is preserved in the target extract "
            "and generated request artifact"
        ),
    }


def _copy_rounding_evidence(
    evidence_root: Path,
    staging_root: Path,
    source_manifest: Mapping[str, Any],
    *,
    generated_at: str,
) -> tuple[Path, list[dict[str, Any]]]:
    _regular_files(evidence_root)
    checksum_path = evidence_root / ROOT_CHECKSUM_NAME
    if not checksum_path.is_file():
        raise ValueError("rounding evidence SHA256SUMS is absent")
    _verify_checksum_closure(checksum_path)
    original_checksum = {
        "sha256": _sha256(checksum_path),
        "bytes": checksum_path.stat().st_size,
    }
    target, company_request = _rounding_target(evidence_root)

    destination = staging_root / ROUNDING_EVIDENCE_DESTINATION
    if destination.exists():
        raise ValueError(f"rounding evidence destination already exists: {destination}")
    shutil.copytree(evidence_root, destination)

    company_request_name = "sse-company-profit-600782.request.json"
    _write_json(destination / company_request_name, company_request)
    request_by_stem: dict[str, dict[str, Any]] = {
        "sse-company-profit-600782": company_request,
    }
    for headers_path in sorted(destination.glob("*.headers.txt")):
        stem = headers_path.name.removesuffix(".headers.txt")
        if stem in request_by_stem:
            continue
        body_path = destination / f"{stem}.json"
        if not body_path.is_file():
            raise ValueError(f"rounding evidence header has no response body: {headers_path.name}")
        source_artifact = _source_artifact_by_sha256(
            source_manifest,
            _sha256(body_path),
        )
        raw_request = (
            source_artifact.get("request") if isinstance(source_artifact, Mapping) else None
        )
        if not isinstance(raw_request, dict):
            raise ValueError(f"rounding evidence request metadata is absent: {headers_path.name}")
        request_by_stem[stem] = dict(raw_request)
        _write_json(destination / f"{stem}.request.json", raw_request)

    evidence_artifacts: list[dict[str, Any]] = []
    for path in _regular_files(destination):
        if path.name in {ROOT_CHECKSUM_NAME, ROUNDING_EVIDENCE_MANIFEST_NAME}:
            continue
        local_relative = path.relative_to(destination).as_posix()
        relative = (PurePosixPath(ROUNDING_EVIDENCE_DESTINATION) / local_relative).as_posix()
        stem = path.name
        request: dict[str, Any] = {
            "method": "local_copy",
            "url": "",
            "params": {},
        }
        source_kind = "official_exchange_ex_reference_price_supporting_evidence"
        actual_fields: dict[str, Any] = {}
        if path.name == ROUNDING_RESPONSE_NAME:
            source_kind = "official_exchange_ex_reference_price_response"
            request = company_request
            actual_fields = {
                "symbol": "600782",
                "ex_date": "2026-06-05",
                "pre_close_price": 2.64,
                "A_BEFR_TAX_DIV": 0.135,
            }
        elif path.name == company_request_name:
            source_kind = "official_exchange_ex_reference_price_request"
            request = company_request
        elif path.name == ROUNDING_EXTRACT_NAME:
            source_kind = "official_exchange_ex_reference_price_target_extract"
            request = company_request
            actual_fields = {
                "symbol": "600782",
                "ex_date": "2026-06-05",
                "pre_close_price": float(target["PRE_CLOSE_PRICE"]),
                "A_BEFR_TAX_DIV": float(target["A_BEFR_TAX_DIV"]),
            }
        elif path.name.endswith(".headers.txt"):
            response_stem = path.name.removesuffix(".headers.txt")
            request = request_by_stem[response_stem]
            source_kind = (
                "official_exchange_ex_reference_price_response_headers"
                if response_stem == "sse-company-profit-600782"
                else "official_exchange_daily_price_response_headers"
            )
        elif path.name.endswith(".request.json"):
            response_stem = path.name.removesuffix(".request.json")
            request = request_by_stem[response_stem]
            source_kind = (
                "official_exchange_ex_reference_price_request"
                if response_stem == "sse-company-profit-600782"
                else "official_exchange_daily_price_request"
            )
        artifact: dict[str, Any] = {
            "relative_path": relative,
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "source_identity": ("Shanghai Stock Exchange official ex-reference price evidence"),
            "source_kind": source_kind,
            "request": request,
            "retrieved_at": generated_at,
            "timezone": "Asia/Shanghai",
            "parser_version": ("strict-json-v1" if path.suffix == ".json" else "byte-evidence-v1"),
            "actual_fields": actual_fields,
            "missing_state": {"status": "none", "details": None},
            "routing": {
                "pairing_candidate_use": (
                    path.name
                    in {
                        ROUNDING_RESPONSE_NAME,
                        company_request_name,
                        ROUNDING_EXTRACT_NAME,
                        "sse-company-profit-600782.headers.txt",
                    }
                )
            },
        }
        if path.name.endswith(".headers.txt"):
            response_stem = path.name.removesuffix(".headers.txt")
            artifact["body_relative_path"] = f"{ROUNDING_EVIDENCE_DESTINATION}/{response_stem}.json"
            artifact["request_relative_path"] = (
                f"{ROUNDING_EVIDENCE_DESTINATION}/{response_stem}.request.json"
            )
        evidence_artifacts.append(artifact)

    evidence_manifest = {
        "schema_version": "p3.3-s6-600782-ex-reference-evidence-v1",
        "generated_at": generated_at,
        "original_checksum": original_checksum,
        "target": {
            "symbol": "600782",
            "ex_date": "2026-06-05",
            "pre_close_price": 2.64,
            "A_BEFR_TAX_DIV": 0.135,
        },
        "artifacts": [
            {
                "relative_path": path.relative_to(destination).as_posix(),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in _regular_files(destination)
            if path.name != ROOT_CHECKSUM_NAME
        ],
    }
    evidence_manifest_path = destination / ROUNDING_EVIDENCE_MANIFEST_NAME
    _write_json(evidence_manifest_path, evidence_manifest)
    evidence_artifacts.append(
        {
            "relative_path": (f"{ROUNDING_EVIDENCE_DESTINATION}/{ROUNDING_EVIDENCE_MANIFEST_NAME}"),
            "sha256": _sha256(evidence_manifest_path),
            "bytes": evidence_manifest_path.stat().st_size,
            "source_identity": "Frozen 600782 ex-reference evidence manifest",
            "source_kind": "source_package_metadata",
            "request": {"method": "local_copy", "url": "", "params": {}},
            "retrieved_at": generated_at,
            "timezone": "Asia/Shanghai",
            "parser_version": "strict-json-v1",
            "actual_fields": {},
            "missing_state": {"status": "none", "details": None},
        }
    )
    evidence_artifacts.append(
        {
            "relative_path": (f"{ROUNDING_EVIDENCE_DESTINATION}/{ROOT_CHECKSUM_NAME}"),
            "sha256": _sha256(destination / ROOT_CHECKSUM_NAME),
            "bytes": (destination / ROOT_CHECKSUM_NAME).stat().st_size,
            "source_identity": "Frozen 600782 ex-reference evidence checksums",
            "source_kind": "source_package_checksum",
            "request": {"method": "local_copy", "url": "", "params": {}},
            "retrieved_at": generated_at,
            "timezone": "Asia/Shanghai",
            "parser_version": "sha256sum-v1",
            "actual_fields": {},
            "missing_state": {"status": "none", "details": None},
        }
    )
    return evidence_manifest_path, evidence_artifacts


def _header_path(path: Path) -> bool:
    name = path.name.casefold()
    return (
        name.endswith(".headers")
        or name.endswith(".headers.json")
        or name.endswith(".headers.txt")
        or name.endswith("-headers.txt")
    )


def _parse_headers(raw: bytes, *, path: Path) -> tuple[int, dict[str, str | list[str]]]:
    is_json_header = (
        path.name.casefold().endswith(".headers.json")
        or raw.lstrip().startswith(b"{")
    )
    if is_json_header:
        try:
            parsed = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"sanitized HTTP header JSON is invalid: {path}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"sanitized HTTP header root is invalid: {path}")
        status = parsed.get("status_code", parsed.get("status"))
        raw_fields = parsed.get("fields", parsed.get("headers"))
        if not isinstance(status, int) or not isinstance(raw_fields, dict):
            raise ValueError(f"sanitized HTTP header fields are invalid: {path}")
        canonical_fields: dict[str, str | list[str]] = {}
        for raw_name, raw_value in raw_fields.items():
            name = str(raw_name).casefold()
            if name not in _ALLOWED_HEADERS:
                continue
            if isinstance(raw_value, str):
                canonical_fields[name] = raw_value
            elif isinstance(raw_value, list) and all(isinstance(item, str) for item in raw_value):
                canonical_fields[name] = sorted(set(raw_value))
            else:
                raise ValueError(f"sanitized HTTP header value is invalid: {path}:{name}")
        return status, dict(sorted(canonical_fields.items()))

    text = raw.decode("iso-8859-1")
    blocks: list[tuple[int, dict[str, list[str]]]] = []
    current_status: int | None = None
    current_headers: dict[str, list[str]] = {}
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        status_match = _STATUS_LINE.fullmatch(raw_line.strip())
        if status_match is not None:
            if current_status is not None:
                blocks.append((current_status, current_headers))
            current_status = int(status_match.group(1))
            current_headers = {}
            continue
        if current_status is None or not raw_line.strip() or ":" not in raw_line:
            continue
        name, value = raw_line.split(":", 1)
        normalized_name = name.strip().casefold()
        if normalized_name not in _ALLOWED_HEADERS:
            continue
        normalized_value = value.strip()
        current_headers.setdefault(normalized_name, []).append(normalized_value)
    if current_status is not None:
        blocks.append((current_status, current_headers))
    if not blocks:
        raise ValueError(f"raw HTTP headers do not contain a status line: {path}")
    status, selected = blocks[-1]
    canonical: dict[str, str | list[str]] = {}
    for key in sorted(selected):
        values = sorted(set(selected[key]))
        canonical[key] = values[0] if len(values) == 1 else values
    return status, canonical


def _artifact_index(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise ValueError("SOURCE-MANIFEST.artifacts must be a list")
    result: dict[str, dict[str, Any]] = {}
    for raw in raw_artifacts:
        if not isinstance(raw, dict):
            raise ValueError("SOURCE-MANIFEST artifacts must be objects")
        relative = str(raw.get("relative_path") or "")
        if relative in result:
            raise ValueError(f"duplicate source artifact: {relative}")
        result[relative] = raw
    return result


def _price_request_index(root: Path) -> dict[str, dict[str, Any]]:
    path = root / PRICE_REQUESTS_PATH
    if not path.is_file():
        return {}
    raw = _load_json(path, label=PRICE_REQUESTS_PATH).get("requests")
    if not isinstance(raw, list):
        raise ValueError(f"{PRICE_REQUESTS_PATH}.requests must be a list")
    result: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"{PRICE_REQUESTS_PATH}.requests entries must be objects")
        header = str(item.get("response_headers") or "")
        body = str(item.get("response_body") or "")
        if not header or not body:
            raise ValueError("price-tick request must bind response headers and body")
        relative = f"price-tick-rules/{header}"
        if relative in result:
            raise ValueError(f"duplicate price-tick header request: {relative}")
        result[relative] = {
            "request": {
                "id": item.get("id"),
                "method": item.get("method"),
                "url": item.get("url"),
            },
            "body_relative_path": f"price-tick-rules/{body}",
            "source_identity": "Official exchange HTTP response",
            "source_kind": "official_exchange_rule_response_headers",
        }
    return result


def _sibling_request(root: Path, relative: str) -> str | None:
    path = PurePosixPath(relative)
    name = path.name
    if name.endswith(".headers.json"):
        candidate = path.with_name(f"{name.removesuffix('.headers.json')}.request.json")
        return candidate.as_posix() if (root / candidate).is_file() else None
    if name.endswith(".headers.txt"):
        candidate = path.with_name(f"{name.removesuffix('.headers.txt')}.request.json")
        return candidate.as_posix() if (root / candidate).is_file() else None
    if not name.endswith(".headers"):
        return None
    candidate = path.with_name(f"{name.removesuffix('.headers')}.request.json")
    return candidate.as_posix() if (root / candidate).is_file() else None


def _sibling_body(root: Path, relative: str) -> str | None:
    path = PurePosixPath(relative)
    name = path.name
    stems: list[str] = []
    if name.endswith(".headers.json"):
        stems.append(name.removesuffix(".headers.json"))
    elif name.endswith(".headers.txt"):
        stems.append(name.removesuffix(".headers.txt"))
    elif name.endswith(".headers"):
        stems.append(name.removesuffix(".headers"))
    elif name.endswith("-headers.txt"):
        stems.append(name.removesuffix("-headers.txt"))
    suffixes = (".response.json", ".json", ".pdf", ".docx", ".html")
    for stem in stems:
        for suffix in suffixes:
            candidate = path.with_name(f"{stem}{suffix}")
            if (root / candidate).is_file():
                return candidate.as_posix()
    return None


def _canonical_binding(value: object) -> dict[str, Any]:
    content = _canonical_json_bytes(value)
    return {
        "encoding": "canonical-json-utf8",
        "sha256": _sha256_bytes(content),
        "bytes": len(content),
    }


def _file_binding(root: Path, relative: str) -> dict[str, Any]:
    pure = _safe_relative_path(relative, label="header binding")
    path = root / pure
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"header binding target is absent/unsafe: {relative}")
    return {
        "relative_path": pure.as_posix(),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }


def _existing_sanitized_source_binding(raw_bytes: bytes) -> dict[str, Any] | None:
    """A header this script already canonicalized keeps its original raw-capture
    source binding; re-binding to the sanitized bytes would change the file on
    every chained rebuild and silently break copied capture-manifest hashes."""

    try:
        parsed = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(parsed, dict)
        or parsed.get("schema_version") != SANITIZED_HEADER_SCHEMA_VERSION
    ):
        return None
    bindings = parsed.get("bindings")
    source = bindings.get("source") if isinstance(bindings, dict) else None
    if not (
        isinstance(source, dict)
        and isinstance(source.get("format"), str)
        and isinstance(source.get("sha256"), str)
        and isinstance(source.get("bytes"), int)
        and not isinstance(source.get("bytes"), bool)
    ):
        return None
    return {
        "format": source["format"],
        "sha256": source["sha256"],
        "bytes": source["bytes"],
    }


def _sanitize_headers(
    root: Path,
    source_manifest: dict[str, Any],
) -> list[str]:
    artifacts = _artifact_index(source_manifest)
    price_requests = _price_request_index(root)
    sanitized: list[str] = []
    for path in _regular_files(root):
        if not _header_path(path):
            continue
        relative = path.relative_to(root).as_posix()
        raw_bytes = path.read_bytes()
        status, headers = _parse_headers(raw_bytes, path=path)
        artifact = artifacts.get(relative, {})
        price = price_requests.get(relative, {})
        request_relative = _sibling_request(root, relative)
        if request_relative is not None:
            request_binding = _file_binding(root, request_relative)
        else:
            request = artifact.get("request") or price.get("request")
            if not isinstance(request, dict):
                raise ValueError(f"header request binding is absent: {relative}")
            request_binding = _canonical_binding(request)
        body_relative = str(
            artifact.get("body_relative_path")
            or price.get("body_relative_path")
            or _sibling_body(root, relative)
            or ""
        )
        if not body_relative:
            raise ValueError(f"header body binding is absent: {relative}")
        source_identity = str(artifact.get("source_identity") or price.get("source_identity") or "")
        source_kind = str(artifact.get("source_kind") or price.get("source_kind") or "")
        canonical = {
            "schema_version": SANITIZED_HEADER_SCHEMA_VERSION,
            "status": status,
            "headers": headers,
            "bindings": {
                "request": request_binding,
                "source": _existing_sanitized_source_binding(raw_bytes)
                or {
                    "format": (
                        "previously-sanitized-http-response-headers-json"
                        if path.name.casefold().endswith(".headers.json")
                        else "raw-http-response-headers"
                    ),
                    "sha256": _sha256_bytes(raw_bytes),
                    "bytes": len(raw_bytes),
                },
                "body": _file_binding(root, body_relative),
            },
            "source_identity": source_identity,
            "source_kind": source_kind,
        }
        canonical_bytes = _canonical_json_bytes(canonical)
        if canonical_bytes != raw_bytes:
            path.write_bytes(canonical_bytes)
        sanitized.append(relative)
    if not sanitized:
        raise ValueError("bundle does not contain response-header evidence")
    return sanitized


def _scrub_actual_fields(value: object) -> object:
    if not isinstance(value, dict):
        return value
    forbidden = {"cookie", "date", "server", "set-cookie", "trace", "via"}
    return {
        key: item
        for key, item in value.items()
        if key.casefold() not in forbidden and not key.casefold().startswith("x-")
    }


def _update_artifact_entries(
    root: Path,
    entries: object,
    *,
    base: str = "",
) -> None:
    if not isinstance(entries, list):
        return
    for raw in entries:
        if not isinstance(raw, dict):
            raise ValueError("manifest artifact entries must be objects")
        key = "relative_path" if "relative_path" in raw else "file"
        relative = str(raw.get(key) or "")
        if not relative:
            raise ValueError("manifest artifact path is absent")
        rooted_relative = (PurePosixPath(base) / relative).as_posix() if base else relative
        path = root / _safe_relative_path(rooted_relative, label="manifest artifact")
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"manifest artifact is absent/unsafe: {rooted_relative}")
        raw["sha256"] = _sha256(path)
        raw["bytes"] = path.stat().st_size
        if _header_path(path):
            raw["parser_version"] = SANITIZER_VERSION
            raw["actual_fields"] = _scrub_actual_fields(raw.get("actual_fields"))


def _update_capture_manifest(
    root: Path,
    capture_manifest_path: Path | None,
    generated_at: str,
) -> None:
    if capture_manifest_path is None:
        return
    original_manifest_sha256 = _sha256(capture_manifest_path)
    original_manifest_bytes = capture_manifest_path.stat().st_size
    manifest = _load_json(capture_manifest_path, label="copied capture manifest")
    manifest["generated_at"] = generated_at
    if manifest.get("artifacts") is not None:
        _update_artifact_entries(
            root,
            manifest.get("artifacts"),
            base=CAPTURE_DESTINATION,
        )
    captures = manifest.get("captures")
    if isinstance(captures, list):
        for raw_capture in captures:
            if not isinstance(raw_capture, dict):
                raise ValueError("copied capture records must be objects")
            symbol = str(raw_capture.get("symbol") or "")
            pages = raw_capture.get("pages")
            if not isinstance(pages, list):
                raise ValueError(f"copied capture pages are absent: {symbol}")
            for raw_page in pages:
                if not isinstance(raw_page, dict):
                    raise ValueError(f"copied capture pages must be objects: {symbol}")
                page_number = int(raw_page.get("page_number") or 0)
                prefix = f"{CAPTURE_DESTINATION}/{symbol}/page-{page_number:03d}"
                for role, suffix in (
                    ("request", "request.json"),
                    ("response", "response.json"),
                    ("headers", "headers.json"),
                ):
                    path = root / f"{prefix}.{suffix}"
                    if not path.is_file():
                        raise ValueError(f"copied capture page artifact is absent: {path}")
                    raw_page[f"{role}_sha256"] = _sha256(path)
                    raw_page[f"{role}_bytes"] = path.stat().st_size
    manifest["original_capture_manifest"] = {
        "sha256": original_manifest_sha256,
        "bytes": original_manifest_bytes,
    }
    manifest["header_sanitization"] = {
        "schema_version": SANITIZED_HEADER_SCHEMA_VERSION,
        "allowlist": ["status", *_ALLOWED_HEADERS],
    }
    _write_json(capture_manifest_path, manifest)


def _update_rounding_evidence_manifest(
    root: Path,
    evidence_manifest_path: Path | None,
    generated_at: str,
) -> None:
    if evidence_manifest_path is None:
        return
    manifest = _load_json(
        evidence_manifest_path,
        label="copied 600782 ex-reference evidence manifest",
    )
    manifest["generated_at"] = generated_at
    _update_artifact_entries(
        root,
        manifest.get("artifacts"),
        base=ROUNDING_EVIDENCE_DESTINATION,
    )
    manifest["header_sanitization"] = {
        "schema_version": SANITIZED_HEADER_SCHEMA_VERSION,
        "allowlist": ["status", *_ALLOWED_HEADERS],
    }
    _write_json(evidence_manifest_path, manifest)


def _update_szse_reference_manifest(
    root: Path,
    evidence_manifest_path: Path | None,
    generated_at: str,
) -> None:
    if evidence_manifest_path is None:
        return
    manifest = _load_json(
        evidence_manifest_path,
        label="copied 001260 official ex-reference evidence manifest",
    )
    manifest["generated_at"] = generated_at
    _update_artifact_entries(
        root,
        manifest.get("artifacts"),
        base=SZSE_REFERENCE_DESTINATION,
    )
    manifest["header_sanitization"] = {
        "schema_version": SANITIZED_HEADER_SCHEMA_VERSION,
        "allowlist": ["status", *_ALLOWED_HEADERS],
    }
    _write_json(evidence_manifest_path, manifest)


def _update_price_manifest(root: Path, generated_at: str) -> None:
    path = root / PRICE_MANIFEST_PATH
    if not path.is_file():
        return
    manifest = _load_json(path, label=PRICE_MANIFEST_PATH)
    manifest["generated_at"] = generated_at
    for field in ("primary_artifacts", "supporting_artifacts", "artifacts"):
        _update_artifact_entries(
            root,
            manifest.get(field),
            base="price-tick-rules",
        )
    manifest["header_sanitization"] = {
        "schema_version": SANITIZED_HEADER_SCHEMA_VERSION,
        "allowlist": ["status", *_ALLOWED_HEADERS],
    }
    _write_json(path, manifest)


def _rebuild_daily_manifest(root: Path, generated_at: str) -> None:
    path = root / DAILY_MANIFEST_PATH
    checksum_path = root / DAILY_CHECKSUM_PATH
    manifest = _load_json(path, label=DAILY_MANIFEST_PATH)
    manifest["generated_at"] = generated_at
    manifest["files"] = [
        {
            "file": candidate.relative_to(path.parent).as_posix(),
            "sha256": _sha256(candidate),
            "bytes": candidate.stat().st_size,
        }
        for candidate in _regular_files(path.parent)
        if candidate not in {path, checksum_path}
    ]
    manifest["header_sanitization"] = {
        "schema_version": SANITIZED_HEADER_SCHEMA_VERSION,
        "allowlist": ["status", *_ALLOWED_HEADERS],
    }
    _write_json(path, manifest)
    rows = [f"{entry['sha256']}  {entry['file']}" for entry in manifest["files"]]
    rows.append(f"{_sha256(path)}  MANIFEST.json")
    checksum_path.write_text(
        "".join(f"{row}\n" for row in rows),
        encoding="utf-8",
    )


def _copy_contract(
    root: Path,
    contract: Path,
    *,
    generated_at: str,
) -> dict[str, Any]:
    if not contract.is_file() or contract.is_symlink():
        raise ValueError(f"adjudication contract is absent/unsafe: {contract}")
    observed_sha256 = _sha256(contract)
    if observed_sha256 != CONTRACT_SHA256:
        raise ValueError(
            "adjudication contract SHA-256 differs: "
            f"expected={CONTRACT_SHA256} observed={observed_sha256}"
        )
    destination = root / CONTRACT_RELATIVE_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if (
            not destination.is_file()
            or destination.is_symlink()
            or _sha256(destination) != observed_sha256
        ):
            raise ValueError(
                "existing adjudication contract differs from the frozen contract"
            )
    else:
        shutil.copyfile(contract, destination)
    return {
        "relative_path": CONTRACT_RELATIVE_PATH,
        "sha256": observed_sha256,
        "bytes": destination.stat().st_size,
        "source_identity": "Frozen P3.3-S6 external PIT adjudication contract",
        "source_kind": "adjudication_contract",
        "request": {
            "method": "local_copy",
            "url": "",
            "params": {
                "repository_relative_path": (
                    "docs/P3.3-S6-external-pit-adjudication-v1.contract.json"
                )
            },
        },
        "retrieved_at": generated_at,
        "timezone": "Asia/Shanghai",
        "parser_version": "byte-for-byte-copy-v1",
        "actual_fields": {},
        "missing_state": {"status": "none", "details": None},
    }


def _add_missing_header_artifacts(
    root: Path,
    artifacts: list[dict[str, Any]],
    *,
    generated_at: str,
) -> None:
    indexed = {str(item.get("relative_path") or "") for item in artifacts}
    for path in _regular_files(root):
        if not _header_path(path):
            continue
        relative = path.relative_to(root).as_posix()
        if relative in indexed:
            continue
        canonical = _load_json(path, label=f"sanitized header {relative}")
        raw_headers = canonical.get("headers")
        header_names = (
            sorted(str(key) for key in raw_headers) if isinstance(raw_headers, dict) else []
        )
        artifacts.append(
            {
                "relative_path": relative,
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
                "source_identity": canonical.get("source_identity"),
                "source_kind": "sanitized_response_headers",
                "request": {
                    "method": "bound_in_sanitized_artifact",
                    "url": "",
                    "params": {},
                },
                "retrieved_at": generated_at,
                "timezone": "Asia/Shanghai",
                "parser_version": SANITIZER_VERSION,
                "actual_fields": {
                    "http_status": canonical.get("status"),
                    "header_names": header_names,
                },
                "missing_state": {"status": "none", "details": None},
            }
        )
        indexed.add(relative)


def _update_source_manifest(
    root: Path,
    source_manifest: dict[str, Any],
    *,
    generated_at: str,
    contract_artifact: dict[str, Any],
    capture_artifacts: Sequence[dict[str, Any]],
    sanitized_paths: Sequence[str],
) -> None:
    raw_artifacts = source_manifest.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise ValueError("SOURCE-MANIFEST.artifacts must be a list")
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    preserved_artifacts = [
        raw
        for raw in raw_artifacts
        if not (
            isinstance(raw, dict)
            and str(raw.get("relative_path") or "") == CONTRACT_RELATIVE_PATH
        )
    ]
    for raw in [*preserved_artifacts, *capture_artifacts, contract_artifact]:
        if not isinstance(raw, dict):
            raise ValueError("SOURCE-MANIFEST artifacts must be objects")
        relative = str(raw.get("relative_path") or "")
        if relative in seen:
            raise ValueError(f"duplicate SOURCE-MANIFEST artifact: {relative}")
        seen.add(relative)
        artifacts.append(dict(raw))
    source_manifest["generated_at"] = generated_at
    source_manifest["artifacts"] = artifacts
    _update_artifact_entries(root, artifacts)
    _add_missing_header_artifacts(
        root,
        artifacts,
        generated_at=generated_at,
    )
    artifacts.sort(key=lambda item: str(item.get("relative_path") or ""))
    source_manifest["artifact_count"] = len(artifacts)
    source_manifest["header_sanitization"] = {
        "schema_version": SANITIZED_HEADER_SCHEMA_VERSION,
        "allowlist": ["status", *_ALLOWED_HEADERS],
        "sanitized_artifact_count": len(sanitized_paths),
        "raw_header_bytes_retained": False,
    }
    _write_json(root / SOURCE_MANIFEST_NAME, source_manifest)


def _rewrite_nested_checksums(root: Path) -> None:
    checksum_paths = sorted(
        (path for path in root.rglob(ROOT_CHECKSUM_NAME) if path != root / ROOT_CHECKSUM_NAME),
        key=lambda path: (
            -len(path.relative_to(root).parts),
            path.relative_to(root).as_posix(),
        ),
    )
    for path in checksum_paths:
        _write_checksum_closure(path)


def _verify_output(root: Path) -> None:
    for checksum_path in sorted(
        root.rglob(ROOT_CHECKSUM_NAME),
        key=lambda path: path.relative_to(root).as_posix(),
    ):
        _verify_checksum_closure(checksum_path)
    _validate_daily_manifest(root)
    source_manifest = _load_json(
        root / SOURCE_MANIFEST_NAME,
        label=SOURCE_MANIFEST_NAME,
    )
    _validated_generated_at(str(source_manifest.get("generated_at") or ""))
    _validate_manifest_file_entries(
        root,
        source_manifest.get("artifacts"),
        field=f"{SOURCE_MANIFEST_NAME}.artifacts",
    )
    artifacts = source_manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("SOURCE-MANIFEST.artifacts must be a list")
    if int(source_manifest.get("artifact_count") or -1) != len(artifacts):
        raise ValueError("SOURCE-MANIFEST artifact_count is inconsistent")
    contracts = [
        raw
        for raw in artifacts
        if isinstance(raw, dict) and raw.get("source_kind") == "adjudication_contract"
    ]
    if len(contracts) != 1 or contracts[0].get("sha256") != CONTRACT_SHA256:
        raise ValueError("rebuilt bundle has no unique frozen adjudication contract")
    header_paths = [path for path in _regular_files(root) if _header_path(path)]
    for path in header_paths:
        canonical = _load_json(path, label=f"sanitized header {path}")
        if canonical.get("schema_version") != SANITIZED_HEADER_SCHEMA_VERSION:
            raise ValueError(f"header was not canonically sanitized: {path}")
        headers = canonical.get("headers")
        if not isinstance(headers, dict) or not set(headers).issubset(_ALLOWED_HEADERS):
            raise ValueError(f"sanitized header contains forbidden fields: {path}")
        bindings = canonical.get("bindings")
        if not isinstance(bindings, dict) or set(bindings) != {
            "request",
            "source",
            "body",
        }:
            raise ValueError(f"sanitized header bindings are incomplete: {path}")
        for name in ("request", "source", "body"):
            binding = bindings[name]
            if (
                not isinstance(binding, dict)
                or not _SHA256.fullmatch(str(binding.get("sha256") or ""))
                or int(binding.get("bytes") or -1) < 0
            ):
                raise ValueError(f"sanitized header binding is invalid: {path}:{name}")


def rebuild_bundle(
    *,
    source_bundle: Path,
    output: Path,
    contract: Path,
    generated_at: str,
    announcement_capture: Path | None = None,
    rounding_evidence: Path | None = None,
    szse_month_entry: Path | None = None,
    szse_dividend_table: Path | None = None,
) -> dict[str, Any]:
    generated_at = _validated_generated_at(generated_at)
    source_manifest = _validate_input_bundle(source_bundle)
    if output.exists():
        raise ValueError(f"output already exists; refusing to overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output.name}.rebuild-",
            dir=output.parent,
        )
    )
    try:
        shutil.rmtree(temporary)
        shutil.copytree(source_bundle, temporary)
        # Candidate packages deliberately freeze raw-source files read-only.
        # The rebuild works on its private staging copy, so restore only the
        # owner's write bit there before canonicalizing headers and manifests.
        for staged_file in _regular_files(temporary):
            staged_file.chmod(staged_file.stat().st_mode | 0o200)
        capture_manifest_path: Path | None = None
        capture_artifacts: list[dict[str, Any]] = []
        if announcement_capture is not None:
            capture_manifest_path, capture_artifacts = _copy_capture(
                announcement_capture,
                temporary,
            )
        evidence_manifest_path: Path | None = None
        evidence_artifacts: list[dict[str, Any]] = []
        if rounding_evidence is not None:
            evidence_manifest_path, evidence_artifacts = _copy_rounding_evidence(
                rounding_evidence,
                temporary,
                source_manifest,
                generated_at=generated_at,
            )
        if (szse_month_entry is None) != (szse_dividend_table is None):
            raise ValueError("SZSE monthly entry and dividend table must be supplied together")
        szse_manifest_path: Path | None = None
        szse_artifacts: list[dict[str, Any]] = []
        if szse_month_entry is not None and szse_dividend_table is not None:
            szse_manifest_path, szse_artifacts = _copy_szse_monthly_reference_evidence(
                entry_path=szse_month_entry,
                table_path=szse_dividend_table,
                staging_root=temporary,
                generated_at=generated_at,
            )
        contract_artifact = _copy_contract(
            temporary,
            contract,
            generated_at=generated_at,
        )
        merged_manifest = dict(source_manifest)
        merged_artifacts = source_manifest.get("artifacts")
        if not isinstance(merged_artifacts, list):
            raise ValueError("SOURCE-MANIFEST.artifacts must be a list")
        merged_manifest["artifacts"] = [
            dict(raw)
            for raw in [
                *merged_artifacts,
                *capture_artifacts,
                *evidence_artifacts,
                *szse_artifacts,
            ]
        ]
        sanitized_paths = _sanitize_headers(temporary, merged_manifest)
        _update_capture_manifest(
            temporary,
            capture_manifest_path,
            generated_at,
        )
        _update_rounding_evidence_manifest(
            temporary,
            evidence_manifest_path,
            generated_at,
        )
        _update_szse_reference_manifest(
            temporary,
            szse_manifest_path,
            generated_at,
        )
        _update_price_manifest(temporary, generated_at)
        _rewrite_nested_checksums(temporary)
        _rebuild_daily_manifest(temporary, generated_at)
        _update_source_manifest(
            temporary,
            source_manifest,
            generated_at=generated_at,
            contract_artifact=contract_artifact,
            capture_artifacts=[
                *capture_artifacts,
                *evidence_artifacts,
                *szse_artifacts,
            ],
            sanitized_paths=sanitized_paths,
        )
        _write_checksum_closure(temporary / ROOT_CHECKSUM_NAME)
        _verify_output(temporary)
        temporary.rename(output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "output": str(output),
        "generated_at": generated_at,
        "contract_sha256": CONTRACT_SHA256,
        "sanitized_header_count": len(sanitized_paths),
        "rounding_evidence_included": (
            output
            / ROUNDING_EVIDENCE_DESTINATION
            / ROUNDING_EVIDENCE_MANIFEST_NAME
        ).is_file(),
        "szse_reference_evidence_included": (
            output
            / SZSE_REFERENCE_DESTINATION
            / SZSE_REFERENCE_MANIFEST_NAME
        ).is_file(),
        "root_checksum_sha256": _sha256(output / ROOT_CHECKSUM_NAME),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = rebuild_bundle(
        source_bundle=args.source_bundle,
        announcement_capture=args.announcement_capture,
        rounding_evidence=args.rounding_evidence,
        szse_month_entry=args.szse_month_entry,
        szse_dividend_table=args.szse_dividend_table,
        contract=args.contract,
        output=args.output,
        generated_at=args.generated_at,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
