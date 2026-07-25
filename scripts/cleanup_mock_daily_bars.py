from __future__ import annotations

import argparse
from pathlib import Path

from alphapilot.db.data_hygiene import cleanup_mock_daily_bars, write_cleanup_evidence


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely remove bootstrap mock rows from daily_bars."
    )
    parser.add_argument("--database", type=Path, default=Path("data/alphapilot.db"))
    parser.add_argument("--backup-directory", type=Path, default=Path("data/backups"))
    parser.add_argument("--evidence-json", type=Path)
    parser.add_argument("--expected-count", type=int, default=95)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the guarded deletion. Without this flag the command is read-only.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = cleanup_mock_daily_bars(
        database_path=args.database,
        backup_directory=args.backup_directory,
        expected_count=args.expected_count,
        apply=args.apply,
    )
    if args.evidence_json is not None:
        write_cleanup_evidence(result, args.evidence_json)
    print(
        f"status={result.status} before={result.before_count} "
        f"deleted={result.deleted_count} after={result.after_count} "
        f"quick_check={result.database_quick_check}"
    )
    if result.backup_path is not None:
        print(f"backup={result.backup_path}")
        print(f"backup_sha256={result.backup_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
