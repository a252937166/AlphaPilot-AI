from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from alphapilot.db.backup import (
    DEFAULT_MINIMUM_FREE_BYTES,
    DEFAULT_RETENTION,
    DatabaseBackupError,
)
from alphapilot.db.backup_schedule import (
    DEFAULT_WINDOW_START_HOUR,
    run_daily_database_backup,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the verified full-database backup once per Shanghai date."
    )
    parser.add_argument("--db", type=Path, default=Path("data/alphapilot.db"))
    parser.add_argument("--backup-dir", type=Path, default=Path("data/backups"))
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=Path.home()
        / "Library"
        / "Application Support"
        / "AlphaPilot-AI"
        / "database-backup",
    )
    parser.add_argument("--retain", type=int, default=DEFAULT_RETENTION)
    parser.add_argument(
        "--minimum-free-bytes",
        type=int,
        default=DEFAULT_MINIMUM_FREE_BYTES,
    )
    parser.add_argument(
        "--window-start-hour",
        type=int,
        default=DEFAULT_WINDOW_START_HOUR,
        help="Earliest Asia/Shanghai hour at which the daily backup may start.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    try:
        result = run_daily_database_backup(
            arguments.db,
            arguments.backup_dir,
            arguments.runtime_dir,
            retain=arguments.retain,
            minimum_free_bytes=arguments.minimum_free_bytes,
            window_start_hour=arguments.window_start_hour,
        )
    except (
        DatabaseBackupError,
        FileNotFoundError,
        FileExistsError,
        OSError,
        sqlite3.Error,
        ValueError,
    ) as exc:
        print(
            f"daily database backup failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    if result["status"] in {"backed_up", "reconciled"}:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
