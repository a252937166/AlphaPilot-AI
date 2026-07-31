from __future__ import annotations

import argparse
import json
import logging

from alphapilot.jobs.financials import register_financials_job
from alphapilot.jobs.registry import run_job


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the audited sync_financials JobSpec for a cold historical backfill."
    )
    parser.add_argument("--quarters", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument(
        "--max-provider-requests",
        type=int,
        default=40_000,
        help="Stop cleanly before this estimated BaoStock request count (default: 40000).",
    )
    parser.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        help="Limit the audited run to one symbol; repeat for multiple symbols.",
    )
    parser.add_argument(
        "--symbol-min",
        type=int,
        help="Include only six-digit symbols whose numeric value is at least this value.",
    )
    parser.add_argument(
        "--symbol-max-exclusive",
        type=int,
        help="Include only symbols whose numeric value is below this value.",
    )
    parser.add_argument(
        "--checkpoint-unavailable",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Persist no-data quarter checkpoints for this cold backfill "
            "(default: enabled; regular scheduled sync is unaffected)."
        ),
    )
    parser.add_argument(
        "--probe-before-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Require one successful real BaoStock financial query before the shard starts "
            "(default: enabled; the probe counts against the request budget)."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Register only this read-only data-ingestion job. The cold runner must not
    # start or even expose unrelated scheduler/trading jobs on a staging host.
    register_financials_job()
    record = run_job(
        "sync_financials",
        quarters=args.quarters,
        batch_size=args.batch_size,
        max_provider_requests=args.max_provider_requests,
        symbols=args.symbols,
        symbol_min=args.symbol_min,
        symbol_max_exclusive=args.symbol_max_exclusive,
        use_unavailable_checkpoints=args.checkpoint_unavailable,
        probe_before_run=args.probe_before_run,
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
