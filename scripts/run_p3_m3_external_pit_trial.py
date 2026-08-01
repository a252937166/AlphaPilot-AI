from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from alphapilot.backtest.external_pit import (
    LiveExternalPITSources,
    build_preflight_plan,
    build_signed_evidence_v2,
    build_trial_document,
    load_pit_manifest,
)
from alphapilot.backtest.external_pit_adjudication import FROZEN_MANIFEST_SHA256
from alphapilot.core.config import get_settings

DEFAULT_MANIFEST = Path("docs/phase3/reports/P3.3-S6-preflight.json")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a fail-closed, non-BaoStock external PIT pairing plan, "
            "trial, or explicitly signed evidence-v2 document."
        )
    )
    parser.add_argument(
        "mode",
        choices=("plan", "trial", "sign"),
        help="plan is network-free; trial is unsigned; sign requires explicit approval",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--financial-source",
        choices=("em-f10", "tushare"),
        default="em-f10",
    )
    parser.add_argument("--max-tushare-calls", type=int, default=1)
    parser.add_argument("--reviewer-role")
    parser.add_argument("--reviewed-at")
    parser.add_argument(
        "--approve",
        action="store_true",
        help="required with sign; never implied by a successful trial",
    )
    return parser


def _same_path(left: Path, right: Path) -> bool:
    left_resolved = left.expanduser().resolve()
    right_resolved = right.expanduser().resolve()
    if left_resolved == right_resolved:
        return True
    if left_resolved.exists() and right_resolved.exists():
        return os.path.samefile(left_resolved, right_resolved)
    return False


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    serialized = (
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=resolved.parent,
            prefix=f".{resolved.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(resolved)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _reviewed_at(value: str | None) -> datetime:
    if value is None:
        raise ValueError("--reviewed-at is required with sign")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("--reviewed-at must include an explicit timezone")
    return parsed


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if _same_path(arguments.manifest, arguments.output):
        raise ValueError("--output must not overwrite the S6 manifest report")
    manifest = load_pit_manifest(arguments.manifest)
    if (
        arguments.mode == "sign"
        and manifest.manifest_sha256 == FROZEN_MANIFEST_SHA256
    ):
        raise ValueError(
            "the frozen final S6 trial requires offline pairing-v3 adjudication"
        )
    plan = build_preflight_plan(
        manifest,
        financial_source=arguments.financial_source,
        max_tushare_calls=arguments.max_tushare_calls,
    )
    if arguments.mode == "plan":
        _write_json_atomic(arguments.output, plan)
        return 0 if plan["ready_for_trial"] else 2
    if plan["ready_for_trial"] is not True:
        raise ValueError("external PIT preflight blocks network execution")
    if arguments.mode == "sign":
        if not arguments.approve:
            raise ValueError("--approve is required with sign")
        if not arguments.reviewer_role:
            raise ValueError("--reviewer-role is required with sign")
        reviewed_at = _reviewed_at(arguments.reviewed_at)
    else:
        reviewed_at = None

    settings = get_settings()
    sources = LiveExternalPITSources(
        financial_source=arguments.financial_source,
        tushare_token=(
            settings.tushare_token
            if arguments.financial_source == "tushare"
            else None
        ),
        max_tushare_calls=arguments.max_tushare_calls,
    )
    try:
        trial = build_trial_document(manifest, sources)
    finally:
        sources.close()
    if arguments.mode == "trial":
        _write_json_atomic(arguments.output, trial)
        return 0 if trial["summary"]["eligible_for_v2_signing"] else 2

    if reviewed_at is None:
        raise RuntimeError("signed evidence requires a reviewed_at timestamp")
    evidence = build_signed_evidence_v2(
        trial,
        reviewer_role=arguments.reviewer_role,
        reviewed_at=reviewed_at,
    )
    _write_json_atomic(arguments.output, evidence)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Do not echo exception text: upstream errors can contain credentials,
        # URLs, or proxy details. The exception type is enough for automation.
        print(
            f"external PIT pairing failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
