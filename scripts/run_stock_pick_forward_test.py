from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the audited weekly stock-pick forward test (lists, scores, tallies)."
    )
    parser.add_argument(
        "--as-of", type=_iso_date, help="Generate lists as of this session (must have bars)."
    )
    parser.add_argument(
        "--no-generate", action="store_true", help="Only score matured lists and tally."
    )
    parser.add_argument("--output-dir", help="Override ALPHAPILOT_STOCK_PICK_FORWARD_TEST_DIR.")
    return parser.parse_args()


def main() -> int:
    os.chdir(PROJECT_DIR)
    from alphapilot.db.engine import init_db
    from alphapilot.jobs.registry import run_job
    from alphapilot.jobs.stock_pick_forward_test import (
        JOB_NAME,
        register_stock_pick_forward_test_job,
    )

    args = _arguments()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    register_stock_pick_forward_test_job()
    kwargs: dict[str, object] = {}
    if args.as_of is not None:
        kwargs["as_of"] = args.as_of
    if args.no_generate:
        kwargs["generate"] = False
    if args.output_dir:
        kwargs["output_dir"] = args.output_dir
    record = run_job(JOB_NAME, **kwargs)
    print(
        json.dumps(
            {
                "id": record.id,
                "status": record.status,
                "error": record.error,
                "stats": record.stats,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if record.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
