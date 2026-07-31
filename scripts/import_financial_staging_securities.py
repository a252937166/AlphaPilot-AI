from __future__ import annotations

import argparse
import json
from pathlib import Path

from alphapilot.jobs.financial_security_transfer import (
    import_missing_staging_securities,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Insert only explicitly allowlisted, currently missing Security rows "
            "from a read-only source SQLite DB into an existing S2 staging SQLite DB."
        )
    )
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--target-db", type=Path, required=True)
    parser.add_argument(
        "--symbol",
        action="append",
        required=True,
        help="Explicit six-digit security symbol; repeat for each allowed row.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    result = import_missing_staging_securities(
        arguments.source_db,
        arguments.target_db,
        symbols=arguments.symbol,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
