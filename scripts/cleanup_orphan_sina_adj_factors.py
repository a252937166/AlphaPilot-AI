from __future__ import annotations

import argparse
from pathlib import Path

from alphapilot.db.data_hygiene import (
    cleanup_orphan_sina_adj_factors,
    write_orphan_adj_factor_cleanup_evidence,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Delete only Sina HFQ orphan adjustment factors whose keys exactly "
            "match the committed Task2B row-level evidence."
        )
    )
    parser.add_argument("--database", type=Path, default=Path("data/alphapilot.db"))
    parser.add_argument("--backup-directory", type=Path, default=Path("data/backups"))
    parser.add_argument(
        "--authority-evidence",
        type=Path,
        default=Path(
            "docs/phase3/reports/P3.3-task2b-sina-zero-price-repair.json"
        ),
    )
    parser.add_argument("--evidence-json", type=Path)
    parser.add_argument("--expected-count", type=int, default=111)
    parser.add_argument("--expected-symbol-count", type=int, default=30)
    parser.add_argument("--expected-min-date", default="2019-01-02")
    parser.add_argument("--expected-max-date", default="2020-11-16")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the guarded deletion. Without this flag the command is read-only.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = cleanup_orphan_sina_adj_factors(
        database_path=args.database,
        backup_directory=args.backup_directory,
        authority_evidence_path=args.authority_evidence,
        expected_count=args.expected_count,
        expected_symbol_count=args.expected_symbol_count,
        expected_min_date=args.expected_min_date,
        expected_max_date=args.expected_max_date,
        apply=args.apply,
    )
    if args.evidence_json is not None:
        write_orphan_adj_factor_cleanup_evidence(result, args.evidence_json)
    print(
        f"status={result.status} before={result.before_count} "
        f"deleted={result.deleted_count} after={result.after_count} "
        f"quick_check={result.database_quick_check}"
    )
    print(
        "key_gates="
        f"adj_duplicates:{result.adj_duplicate_groups_before}"
        f"->{result.adj_duplicate_groups_after},"
        f"daily_duplicates:{result.daily_bar_duplicate_groups_before}"
        f"->{result.daily_bar_duplicate_groups_after},"
        f"daily_without_adj:{result.daily_bars_without_adj_before}"
        f"->{result.daily_bars_without_adj_after}"
    )
    if result.backup_path is not None:
        print(f"backup={result.backup_path}")
        print(f"backup_sha256={result.backup_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
