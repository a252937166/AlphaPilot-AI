from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from alphapilot.core.config import get_settings
from alphapilot.db.listed_date_backfill import backfill_security_listed_dates
from alphapilot.futu.client import get_futu_client


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fail-closed Futu SH/SZ + Tushare BSE Security.listed_date "
            "backfill. The default mode is read-only dry-run."
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
        "--as-of-date",
        type=date.fromisoformat,
        help="Shanghai evidence date in YYYY-MM-DD form; defaults to today.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Apply missing-only updates after a verified full database backup; "
            "without this flag no database row is changed."
        ),
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    tushare_token = (get_settings().tushare_token or "").strip()
    client = get_futu_client()
    try:
        result = backfill_security_listed_dates(
            database_path=arguments.db,
            backup_directory=arguments.backup_dir,
            evidence_path=arguments.evidence,
            client=client,
            tushare_token=tushare_token,
            apply=arguments.apply,
            as_of_date=arguments.as_of_date,
        )
    finally:
        client.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
