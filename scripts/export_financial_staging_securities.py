from __future__ import annotations

import argparse
import json
from pathlib import Path

from alphapilot.jobs.financial_security_transfer import (
    export_staging_security_metadata,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export explicitly allowlisted Security rows from a read-only production "
            "SQLite DB into a new, metadata-only SQLite DB without overwriting files."
        )
    )
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--output-db", type=Path, required=True)
    parser.add_argument(
        "--symbol",
        action="append",
        required=True,
        help="Explicit six-digit security symbol; repeat for each allowed row.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    result = export_staging_security_metadata(
        arguments.source_db,
        arguments.output_db,
        symbols=arguments.symbol,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
