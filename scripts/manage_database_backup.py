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
    create_database_backup,
    verify_database_backup,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or verify an atomic online backup of the AlphaPilot SQLite DB."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="Create and retain a verified backup.")
    backup.add_argument(
        "--db",
        type=Path,
        default=Path("data/alphapilot.db"),
        help="Live SQLite database. It is opened mode=ro with query_only=ON.",
    )
    backup.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("data/backups"),
    )
    backup.add_argument("--retain", type=int, default=DEFAULT_RETENTION)
    backup.add_argument(
        "--minimum-free-bytes",
        type=int,
        default=DEFAULT_MINIMUM_FREE_BYTES,
        help="Free bytes that must remain in addition to one full source DB.",
    )

    verify = subparsers.add_parser("verify", help="Verify checksum and SQLite integrity.")
    verify.add_argument("backup_db", type=Path)
    verify.add_argument("--manifest", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    try:
        if arguments.command == "backup":
            result = create_database_backup(
                arguments.db,
                arguments.backup_dir,
                retain=arguments.retain,
                minimum_free_bytes=arguments.minimum_free_bytes,
            )
        elif arguments.command == "verify":
            result = verify_database_backup(
                arguments.backup_db,
                arguments.manifest,
            )
        else:
            raise AssertionError(f"unsupported command: {arguments.command}")
    except (
        DatabaseBackupError,
        FileNotFoundError,
        FileExistsError,
        OSError,
        sqlite3.Error,
        ValueError,
    ) as exc:
        print(
            f"database backup {arguments.command} failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
