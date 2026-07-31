from __future__ import annotations

import os
from pathlib import Path

_PROXY_ENVIRONMENT_KEYS = (
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "FTP_PROXY",
    "ftp_proxy",
    "NO_PROXY",
    "no_proxy",
    "ALPHAPILOT_BAOSTOCK_SOCKS5_PROXY",
    "ALPHAPILOT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS",
)


def _direct_environment(source: dict[str, str]) -> dict[str, str]:
    """Return an environment that cannot opt BaoStock into a proxy path."""

    environment = dict(source)
    for key in _PROXY_ENVIRONMENT_KEYS:
        environment.pop(key, None)
    environment["PYTHONUNBUFFERED"] = "1"
    environment["ALPHAPILOT_SCHEDULER_ENABLED"] = "false"
    environment["ALPHAPILOT_BAOSTOCK_FINANCIAL_SYNC_ENABLED"] = "false"
    return environment


def main() -> None:
    raise SystemExit(
        "Retired P3.3-S2 local shard: the final four-exit contract is owned by "
        "Aliyun, DogCloud, US38, and US-SEA. Refusing an overlapping BaoStock run."
    )


def _retired_runner_arguments() -> tuple[str, list[str], dict[str, str]]:
    """Keep the former local runner contract inspectable without executing it."""

    project_dir = Path(__file__).resolve().parent.parent
    venv_python = project_dir / ".venv/bin/python"
    runner_script = project_dir / "scripts/run_financial_backfill.py"
    environment = _direct_environment(dict(os.environ))
    argv = [
        str(venv_python),
        "-u",
        str(runner_script),
        "--quarters",
        "40",
        "--batch-size",
        "25",
        "--max-provider-requests",
        "40000",
        "--symbol-min",
        "601121",
        "--probe-before-run",
    ]
    return str(venv_python), argv, environment


if __name__ == "__main__":
    main()
