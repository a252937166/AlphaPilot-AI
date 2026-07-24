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
        description="Run the audited Eastmoney valuation-history backfill."
    )
    parser.add_argument("--start-date", type=_iso_date, default=date(2019, 1, 1))
    parser.add_argument("--end-date", type=_iso_date)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        help="Limit the run to one exact six-digit symbol; repeat for more.",
    )
    return parser.parse_args()


def main() -> int:
    os.chdir(PROJECT_DIR)
    from alphapilot.db.engine import init_db
    from alphapilot.jobs.registry import run_job
    from alphapilot.jobs.valuation_sync import register_valuation_jobs

    args = _arguments()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    init_db()
    register_valuation_jobs()
    record = run_job(
        "backfill_valuation",
        start_date=args.start_date,
        end_date=args.end_date,
        symbols=args.symbols,
        batch_size=args.batch_size,
    )
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
