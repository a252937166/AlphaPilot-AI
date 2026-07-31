from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "run_financial_local_backfill.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "run_financial_local_backfill",
    _SCRIPT_PATH,
)
assert _SPEC is not None and _SPEC.loader is not None
run_financial_local_backfill = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = run_financial_local_backfill
_SPEC.loader.exec_module(run_financial_local_backfill)


def test_local_financial_runner_removes_every_proxy_path() -> None:
    source = {
        "PATH": "/usr/bin",
        "ALL_PROXY": "socks5://127.0.0.1:51837",
        "http_proxy": "http://127.0.0.1:58591",
        "HTTPS_PROXY": "http://127.0.0.1:58591",
        "NO_PROXY": "localhost",
        "ALPHAPILOT_BAOSTOCK_SOCKS5_PROXY": "127.0.0.1:51837",
        "ALPHAPILOT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS": "30",
    }

    environment = run_financial_local_backfill._direct_environment(source)

    assert environment["PATH"] == "/usr/bin"
    assert environment["PYTHONUNBUFFERED"] == "1"
    assert environment["ALPHAPILOT_SCHEDULER_ENABLED"] == "false"
    assert environment["ALPHAPILOT_BAOSTOCK_FINANCIAL_SYNC_ENABLED"] == "false"
    assert all(
        key not in environment
        for key in run_financial_local_backfill._PROXY_ENVIRONMENT_KEYS
    )


def test_local_financial_runner_is_retired_and_refuses_to_execute() -> None:
    with pytest.raises(SystemExit, match=r"Retired P3\.3-S2 local shard"):
        run_financial_local_backfill.main()


def test_retired_local_contract_remains_auditable_without_execution() -> None:
    executable, argv, environment = (
        run_financial_local_backfill._retired_runner_arguments()
    )

    assert "ALL_PROXY" not in environment
    assert argv[1] == "-u"
    assert argv[argv.index("--symbol-min") + 1] == "601121"
    assert argv[argv.index("--max-provider-requests") + 1] == "40000"
    assert "--symbol-max-exclusive" not in argv
    assert "--probe-before-run" in argv
    assert executable == os.fspath(_SCRIPT_PATH.parent.parent / ".venv/bin/python")
