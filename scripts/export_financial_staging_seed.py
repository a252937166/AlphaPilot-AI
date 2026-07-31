from __future__ import annotations

import argparse
import json
from pathlib import Path

from alphapilot.jobs.financial_transfer import export_financial_staging_seed


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a slim S2 staging seed with securities/profile checkpoints and "
            "financial rows only."
        )
    )
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--output-db", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    result = export_financial_staging_seed(arguments.source_db, arguments.output_db)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
