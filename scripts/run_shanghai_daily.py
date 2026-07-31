from __future__ import annotations

import argparse
import fcntl
import os
import re
import subprocess
import tempfile
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a command once per Shanghai calendar day at or after a target time."
        )
    )
    parser.add_argument("--at", required=True, help="Shanghai wall time in HH:MM")
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser.parse_args()


def _parse_target(value: str) -> time:
    if re.fullmatch(r"\d{2}:\d{2}", value) is None:
        raise ValueError(f"invalid Shanghai target time: {value!r}")
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError(f"invalid Shanghai target time: {value!r}") from exc
    return parsed.time()


def _read_last_success(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def _should_run(
    *,
    now: datetime,
    target: time,
    last_success_date: str | None,
) -> bool:
    shanghai_now = now.astimezone(_SHANGHAI)
    if shanghai_now.time().replace(tzinfo=None) < target:
        return False
    return last_success_date != shanghai_now.date().isoformat()


def _write_success(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"{value}\n")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    arguments = _arguments()
    command = list(arguments.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("a command is required after --")

    target = _parse_target(arguments.at)
    state_file = arguments.state_file.expanduser().resolve()
    state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_file = state_file.with_name(f"{state_file.name}.lock")
    with lock_file.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

        now = datetime.now(tz=_SHANGHAI)
        if not _should_run(
            now=now,
            target=target,
            last_success_date=_read_last_success(state_file),
        ):
            return 0

        completed = subprocess.run(command, check=False)
        if completed.returncode == 0:
            _write_success(state_file, now.date().isoformat())
        return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
