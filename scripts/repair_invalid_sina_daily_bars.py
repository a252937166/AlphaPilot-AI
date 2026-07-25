from __future__ import annotations

import argparse
from pathlib import Path

from alphapilot.data.sina_provider import SinaDailyBarProvider
from alphapilot.db.data_hygiene import (
    clear_proxy_environment,
    repair_invalid_sina_daily_bars,
    write_sina_repair_evidence,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refetch invalid BSE daily bars directly from Sina."
    )
    parser.add_argument("--database", type=Path, default=Path("data/alphapilot.db"))
    parser.add_argument("--backup-directory", type=Path, default=Path("data/backups"))
    parser.add_argument("--evidence-json", type=Path)
    parser.add_argument("--expected-count", type=int, default=111)
    parser.add_argument("--min-interval-seconds", type=float, default=0.5)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply guarded repairs/deletions. Without this flag the command is read-only.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    cleared_proxy_keys = clear_proxy_environment()
    result = repair_invalid_sina_daily_bars(
        database_path=args.database,
        backup_directory=args.backup_directory,
        expected_count=args.expected_count,
        fetcher=SinaDailyBarProvider(
            min_interval_seconds=args.min_interval_seconds
        ),
        apply=args.apply,
        cleared_proxy_keys=cleared_proxy_keys,
    )
    if args.evidence_json is not None:
        write_sina_repair_evidence(result, args.evidence_json)
    print(
        f"status={result.status} before={result.before_count} "
        f"repaired={result.repaired_count} deleted={result.deleted_count} "
        f"adj_deleted={result.deleted_adj_factor_count} "
        f"after={result.after_count} quick_check={result.database_quick_check}"
    )
    print(
        "proxy_environment="
        + (",".join(result.cleared_proxy_keys) if result.cleared_proxy_keys else "already_clear")
    )
    if result.backup_path is not None:
        print(f"backup={result.backup_path}")
        print(f"backup_sha256={result.backup_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
