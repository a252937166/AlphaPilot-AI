from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def _arguments() -> argparse.Namespace:
    today = date.today()
    parser = argparse.ArgumentParser(
        description="Run the audited one-year Futu DAY sector-flow backfill."
    )
    parser.add_argument("--start-date", type=_iso_date, default=today - timedelta(days=364))
    parser.add_argument("--end-date", type=_iso_date, default=today)
    parser.add_argument("--pause-seconds", type=float, default=1.05)
    return parser.parse_args()


def main() -> int:
    os.chdir(PROJECT_DIR)
    from alphapilot.db.engine import init_db
    from alphapilot.futu.client import get_futu_client
    from alphapilot.jobs.registry import run_job
    from alphapilot.jobs.sector_flow_backfill import register_sector_flow_backfill_job

    args = _arguments()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    init_db()
    register_sector_flow_backfill_job()
    try:
        record = run_job(
            "backfill_sector_flows",
            start_date=args.start_date,
            end_date=args.end_date,
            pause_seconds=args.pause_seconds,
        )
    finally:
        get_futu_client().close()
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
