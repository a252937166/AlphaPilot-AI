from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from alphapilot.backtest.data_health import (
    build_data_health_report,
    render_data_health_markdown,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the read-only P3.3-S6 input-data gate."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/alphapilot.db"),
        help="Existing SQLite database (opened with mode=ro).",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Stdout representation.",
    )
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    parser.add_argument(
        "--external-pit-pairing-evidence",
        type=Path,
        help=(
            "Strict JSON architect sign-off covering exactly the selected PIT samples; "
            "only non-sensitive metadata and SHA-256 enter the report."
        ),
    )
    parser.add_argument(
        "--external-pit-ai-review-attestation",
        type=Path,
        help=(
            "Content-addressed independent AI review attestation required by "
            "the exact Claude Code reviewer profile."
        ),
    )
    parser.add_argument(
        "--external-pit-frozen-preflight",
        type=Path,
        help=(
            "Content-addressed frozen S6 preflight used to re-audit the exact "
            "candidate-bound business keys against the current database."
        ),
    )
    parser.add_argument("--minimum-market-coverage", type=float, default=0.90)
    parser.add_argument("--minimum-factor-cross-section", type=int, default=100)
    parser.add_argument("--minimum-sector-plates", type=int, default=100)
    parser.add_argument("--minimum-sector-dates", type=int, default=200)
    parser.add_argument("--sample-size", type=int, default=5)
    return parser.parse_args()


def _same_path_or_inode(left: Path, right: Path) -> bool:
    if left.expanduser().resolve() == right.expanduser().resolve():
        return True
    if left.exists() and right.exists():
        try:
            return left.samefile(right)
        except OSError:
            return False
    return False


def _validate_paths(
    database_path: Path,
    *,
    json_output: Path | None,
    markdown_output: Path | None,
    external_evidence: Path | None,
    ai_review_attestation: Path | None,
    frozen_preflight: Path | None,
) -> None:
    resolved_database = database_path.expanduser().resolve()
    if not resolved_database.is_file():
        raise ValueError(f"database does not exist: {resolved_database}")
    outputs = [
        ("--json-out", output)
        for output in (json_output,)
        if output is not None
    ] + [
        ("--markdown-out", output)
        for output in (markdown_output,)
        if output is not None
    ]
    for label, output in outputs:
        if output.exists() and output.is_dir():
            raise ValueError(f"{label} must be a file path")
        if _same_path_or_inode(resolved_database, output):
            raise ValueError(f"{label} must not resolve to the SQLite database")
    if external_evidence is not None:
        if not external_evidence.expanduser().resolve().is_file():
            raise ValueError("--external-pit-pairing-evidence must be an existing file")
        if _same_path_or_inode(resolved_database, external_evidence):
            raise ValueError(
                "--external-pit-pairing-evidence must not be the SQLite database"
            )
        for label, output in outputs:
            if _same_path_or_inode(external_evidence, output):
                raise ValueError(f"{label} must not overwrite external PIT evidence")
    if ai_review_attestation is not None:
        if not ai_review_attestation.expanduser().resolve().is_file():
            raise ValueError(
                "--external-pit-ai-review-attestation must be an existing file"
            )
        if external_evidence is None:
            raise ValueError(
                "--external-pit-ai-review-attestation requires "
                "--external-pit-pairing-evidence"
            )
        if _same_path_or_inode(resolved_database, ai_review_attestation):
            raise ValueError(
                "--external-pit-ai-review-attestation must not be the SQLite database"
            )
        if _same_path_or_inode(external_evidence, ai_review_attestation):
            raise ValueError(
                "AI review attestation and external PIT evidence must be different files"
            )
        for label, output in outputs:
            if _same_path_or_inode(ai_review_attestation, output):
                raise ValueError(f"{label} must not overwrite AI review attestation")
        if frozen_preflight is None:
            raise ValueError(
                "--external-pit-ai-review-attestation requires "
                "--external-pit-frozen-preflight"
            )
    if frozen_preflight is not None:
        if not frozen_preflight.expanduser().resolve().is_file():
            raise ValueError(
                "--external-pit-frozen-preflight must be an existing file"
            )
        if external_evidence is None:
            raise ValueError(
                "--external-pit-frozen-preflight requires "
                "--external-pit-pairing-evidence"
            )
        protected_inputs = [
            ("SQLite database", resolved_database),
            ("external PIT evidence", external_evidence),
            ("AI review attestation", ai_review_attestation),
        ]
        for label, protected in protected_inputs:
            if protected is not None and _same_path_or_inode(
                frozen_preflight,
                protected,
            ):
                raise ValueError(
                    "--external-pit-frozen-preflight must not resolve to "
                    f"the {label}"
                )
        for label, output in outputs:
            if _same_path_or_inode(frozen_preflight, output):
                raise ValueError(f"{label} must not overwrite frozen PIT preflight")
    if (
        json_output is not None
        and markdown_output is not None
        and _same_path_or_inode(json_output, markdown_output)
    ):
        raise ValueError("--json-out and --markdown-out must be different files")


def main() -> int:
    arguments = _arguments()
    try:
        _validate_paths(
            arguments.db,
            json_output=arguments.json_out,
            markdown_output=arguments.markdown_out,
            external_evidence=arguments.external_pit_pairing_evidence,
            ai_review_attestation=arguments.external_pit_ai_review_attestation,
            frozen_preflight=arguments.external_pit_frozen_preflight,
        )
    except ValueError as exc:
        print(f"data-health path validation failed: {exc}", file=sys.stderr)
        return 2
    report = build_data_health_report(
        arguments.db,
        external_pit_pairing_evidence=arguments.external_pit_pairing_evidence,
        external_pit_ai_review_attestation=(
            arguments.external_pit_ai_review_attestation
        ),
        external_pit_frozen_preflight=arguments.external_pit_frozen_preflight,
        minimum_market_coverage=arguments.minimum_market_coverage,
        minimum_factor_cross_section=arguments.minimum_factor_cross_section,
        minimum_sector_plates=arguments.minimum_sector_plates,
        minimum_sector_dates=arguments.minimum_sector_dates,
        sample_size=arguments.sample_size,
    )
    json_text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    markdown_text = render_data_health_markdown(report)
    if arguments.json_out is not None:
        arguments.json_out.parent.mkdir(parents=True, exist_ok=True)
        arguments.json_out.write_text(f"{json_text}\n", encoding="utf-8")
    if arguments.markdown_out is not None:
        arguments.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        arguments.markdown_out.write_text(markdown_text, encoding="utf-8")
    print(json_text if arguments.format == "json" else markdown_text)
    return 0 if report["gate"]["ready_for_s7"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
