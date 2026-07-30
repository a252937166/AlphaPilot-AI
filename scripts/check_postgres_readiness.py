#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from alphapilot.db.postgres_readiness import build_postgres_readiness_report


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Inspect PostgreSQL migration readiness without connecting to a database.")
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="AlphaPilot repository root (default: current working directory).",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit one-line JSON instead of indented JSON.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    report = build_postgres_readiness_report(arguments.project_root)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=None if arguments.compact else 2,
            sort_keys=True,
        )
    )
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
