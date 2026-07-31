from __future__ import annotations

import argparse
import json
from pathlib import Path

from alphapilot.jobs.financial_transfer import import_financial_snapshot


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely merge missing S2 financial rows from a remote snapshot."
    )
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--target-db", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    result = import_financial_snapshot(arguments.source_db, arguments.target_db)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
