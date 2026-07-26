from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

from alphapilot.backtest.financial_acceptance import (
    SHARD_CONTRACTS,
    build_s2_financial_acceptance_report,
    render_s2_financial_acceptance_markdown,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the read-only P3.3-S2 financial final-acceptance preflight. "
            "This command never imports or calls BaoStock."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/alphapilot.db"),
        help="Merged local SQLite database, opened with mode=ro/query_only.",
    )
    parser.add_argument(
        "--as-of-date",
        type=date.fromisoformat,
        required=True,
        help="Explicit Shanghai calendar date used to freeze the 40-quarter contract.",
    )
    parser.add_argument(
        "--shard-db",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help=(
            "Static final shard snapshot containing the empty-run JobRun. "
            f"Repeat for: {', '.join(SHARD_CONTRACTS)}."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
    )
    parser.add_argument("--minimum-covered-symbols", type=int, default=5_000)
    parser.add_argument("--minimum-provider-pub-date-ratio", type=float, default=0.95)
    parser.add_argument("--pubdate-seed", type=int, default=20260726)
    return parser.parse_args()


def _shard_paths(values: list[str]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path:
            raise ValueError("--shard-db must use NAME=PATH")
        if name not in SHARD_CONTRACTS:
            raise ValueError(f"unknown shard {name!r}; expected one of {sorted(SHARD_CONTRACTS)}")
        if name in paths:
            raise ValueError(f"duplicate --shard-db name: {name}")
        paths[name] = Path(raw_path)
    return paths


def main() -> int:
    arguments = _arguments()
    try:
        shard_paths = _shard_paths(arguments.shard_db)
        report = build_s2_financial_acceptance_report(
            arguments.db,
            as_of_date=arguments.as_of_date,
            shard_databases=shard_paths,
            minimum_covered_symbols=arguments.minimum_covered_symbols,
            minimum_provider_pub_date_ratio=(arguments.minimum_provider_pub_date_ratio),
            pubdate_seed=arguments.pubdate_seed,
        )
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        print(
            f"S2 acceptance preflight failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    if arguments.format == "markdown":
        print(render_s2_financial_acceptance_markdown(report), end="")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["gate"]["local_checks_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
