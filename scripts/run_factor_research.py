from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from alphapilot.jobs.factor_research_job import (
    FORMAL_RESEARCH_JOB_NAME,
    register_factor_research_job,
)
from alphapilot.jobs.registry import JOBS, run_job

_ROOT = Path(__file__).resolve().parents[1]
_LOCK_PATH = _ROOT / "data" / "p3-m3-factor-research.lock"
_RESEARCH_ENV = {
    "ALPHAPILOT_TRADING_MODE": "research",
    "ALPHAPILOT_LIVE_TRADING_ENABLED": "false",
    "ALPHAPILOT_PAPER_TRADING_ENABLED": "false",
    "ALPHAPILOT_PAPER_AUTO_TRADING_ENABLED": "false",
    "ALPHAPILOT_FUTU_ENABLE_ACCOUNT_MUTATION": "false",
    "ALPHAPILOT_FUTU_ENABLE_TRADE": "false",
    "ALPHAPILOT_SCHEDULER_ENABLED": "false",
    "ALPHAPILOT_MARKET_POLL_ENABLED": "false",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the S6-gated formal P3.3 factor research job."
    )
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--train-ratio", type=float, default=0.7)
    parser.add_argument(
        "--external-pit-evidence",
        type=Path,
        required=True,
        help="Final architect-signed S6 external PIT pairing evidence.",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run in this process. Omit to detach and write an unbuffered log.",
    )
    parser.add_argument(
        "--log-path",
        type=Path,
        default=Path("logs/p3-m3-factor-research.log"),
    )
    return parser


def _detached_command(arguments: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        "-u",
        str(Path(__file__).resolve()),
        "--foreground",
        "--start-date",
        arguments.start_date.isoformat(),
        "--end-date",
        arguments.end_date.isoformat(),
        "--train-ratio",
        str(arguments.train_ratio),
        "--external-pit-evidence",
        str(arguments.external_pit_evidence.expanduser().resolve()),
    ]
    return command


@contextmanager
def _research_host_lock() -> Iterator[None]:
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK_PATH.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                f"formal factor research is already running: {_LOCK_PATH}"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _detach(arguments: argparse.Namespace) -> int:
    log_path = arguments.log_path.expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab", buffering=0) as stream:
        process = subprocess.Popen(
            _detached_command(arguments),
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=_ROOT,
            env={**os.environ, **_RESEARCH_ENV},
        )
    print(json.dumps({"pid": process.pid, "log_path": str(log_path)}))
    return 0


def _foreground(arguments: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    os.environ["ALPHAPILOT_S6_EXTERNAL_PIT_EVIDENCE"] = str(
        arguments.external_pit_evidence.expanduser().resolve()
    )
    register_factor_research_job()
    with _research_host_lock():
        if FORMAL_RESEARCH_JOB_NAME not in JOBS:
            raise RuntimeError(f"job is not registered: {FORMAL_RESEARCH_JOB_NAME}")
        record = run_job(
            FORMAL_RESEARCH_JOB_NAME,
            start_date=arguments.start_date,
            end_date=arguments.end_date,
            train_ratio=arguments.train_ratio,
            do_rebuild=False,
            output_path=None,
        )
    print(
        json.dumps(
            {
                "job_run_id": record.id,
                "status": record.status,
                "error": record.error,
                "stats": record.stats,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if record.status == "ok" else 2


def main() -> int:
    arguments = _parser().parse_args()
    return _foreground(arguments) if arguments.foreground else _detach(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
