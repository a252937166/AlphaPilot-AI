from __future__ import annotations

import argparse
import json
import logging
from datetime import date

from alphapilot.jobs.factor_research_job import register_factor_research_job
from alphapilot.jobs.registry import run_job


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the audited seven-factor preliminary train-only IC research."
    )
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--train-ratio", type=float, default=0.7)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    register_factor_research_job()
    record = run_job(
        "research_preliminary_train_ic",
        start_date=arguments.start_date,
        end_date=arguments.end_date,
        train_ratio=arguments.train_ratio,
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
