from __future__ import annotations

import argparse
import json
from pathlib import Path

from alphapilot.db.calendar_hygiene import cleanup_s7_weekend_rows


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed cleanup for the two proven weekend rows that shift "
            "the formal P3.3-S7 research calendar."
        )
    )
    parser.add_argument("--db", type=Path, default=Path("data/alphapilot.db"))
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("data/backups"),
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply after a verified full online backup; default is read-only.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    result = cleanup_s7_weekend_rows(
        database_path=arguments.db,
        backup_directory=arguments.backup_dir,
        evidence_path=arguments.evidence,
        apply=arguments.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
