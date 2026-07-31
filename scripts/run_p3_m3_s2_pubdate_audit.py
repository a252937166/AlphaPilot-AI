from __future__ import annotations

import argparse
import importlib
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, cast

from alphapilot.backtest.financial_pubdate_execution import (
    EXECUTION_SCHEMA_VERSION,
    BaoStockClient,
    execute_pubdate_audit,
)


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Execute exactly five fixed BaoStock pubDate comparisons from an approved "
            "P3.3-S2 plan JSON. This command does not open the production database."
        )
    )
    parser.add_argument(
        "--plan",
        type=Path,
        required=True,
        help=(
            "JSON file containing either pubdate_plan or a complete acceptance report "
            "with a pubdate_plan field."
        ),
    )
    return parser.parse_args(argv)


def _input_failure(code: str, detail: str) -> dict[str, Any]:
    return {
        "schema_version": EXECUTION_SCHEMA_VERSION,
        "mode": "authorized_fixed_plan_execution",
        "network_called": False,
        "queries_attempted": 0,
        "gate": {
            "passed": False,
            "matched_count": 0,
            "required_matches": 5,
            "blockers": [{"code": code, "detail": detail}],
        },
    }


def main(
    argv: list[str] | None = None,
    *,
    client: BaoStockClient | None = None,
) -> int:
    arguments = _arguments(argv)
    try:
        payload: object = json.loads(arguments.plan.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        report = _input_failure(
            "plan_read_error",
            f"unable to read plan JSON: {type(exc).__name__}",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    resolved_client = client
    if resolved_client is None:
        try:
            resolved_client = cast(
                BaoStockClient,
                importlib.import_module("baostock"),
            )
        except ImportError:
            report = _input_failure(
                "client_import_error",
                "baostock module is not installed",
            )
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 1

    # BaoStock may write informational text to stdout. Suppress it so stdout remains
    # exactly one machine-auditable JSON document; provider error codes/messages are
    # preserved structurally by the executor.
    provider_console = io.StringIO()
    with redirect_stdout(provider_console), redirect_stderr(provider_console):
        report = execute_pubdate_audit(payload, client=resolved_client)
    report["provider_console_output_suppressed"] = bool(provider_console.getvalue())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["gate"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
