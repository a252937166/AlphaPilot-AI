"""Test bootstrap: isolated SQLite database, mock provider, no live Futu sockets.

The environment must be set before any alphapilot import because get_settings()
is cached and main.py resolves settings at import time.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

_tmpdir = tempfile.mkdtemp(prefix="alphapilot-tests-")
os.environ["ALPHAPILOT_DATABASE_URL"] = f"sqlite:///{_tmpdir}/test.db"
os.environ["ALPHAPILOT_PROCESS_LOCK_DIR"] = f"{_tmpdir}/process-locks"
os.environ["ALPHAPILOT_DEFAULT_DATA_PROVIDER"] = "mock"
os.environ["ALPHAPILOT_SCHEDULER_ENABLED"] = "false"
os.environ["ALPHAPILOT_MARKET_POLL_ENABLED"] = "false"
os.environ["ALPHAPILOT_NEWS_POLL_ENABLED"] = "false"
# Tests must never inherit any locally enabled paper or live execution switches.
os.environ["ALPHAPILOT_FUTU_ENABLE_TRADE_QUERY"] = "false"
os.environ["ALPHAPILOT_FUTU_ENABLE_TRADE"] = "false"
os.environ["ALPHAPILOT_PAPER_TRADING_ENABLED"] = "false"
os.environ["ALPHAPILOT_PAPER_AUTO_TRADING_ENABLED"] = "false"
os.environ["ALPHAPILOT_TRADING_MODE"] = "research"
os.environ["ALPHAPILOT_LIVE_TRADING_ENABLED"] = "false"
# Unit and API tests must never inherit the developer's real local LLM credentials,
# nor a locally switched chat platform: ALPHAPILOT_LLM_PROVIDER=friday in a .env
# would otherwise silently move every LLM test onto the other provider profile.
os.environ["ALPHAPILOT_LLM_PROVIDER"] = "dashscope"
os.environ["ALPHAPILOT_LLM_FRIDAY_APP_ID"] = ""
os.environ["ALPHAPILOT_LLM_BASE_URL"] = ""
os.environ["ALPHAPILOT_LLM_API_KEY"] = ""
os.environ["ALPHAPILOT_LLM_MODEL"] = "qwen3.6-flash"
os.environ["ALPHAPILOT_LLM_PURPOSE_MODELS"] = "{}"
# BaoStock egress must not inherit the developer's tunnel from .env: tests assume a
# single direct path unless they configure a proxy themselves.
os.environ["ALPHAPILOT_BAOSTOCK_SOCKS5_PROXY"] = ""
os.environ["ALPHAPILOT_BAOSTOCK_EGRESS"] = "auto"
# Tests never reach TypeSafe with the developer's key.
os.environ["ALPHAPILOT_JEV_API_KEY"] = ""
# The host-wide BaoStock lock must not collide with the developer's running scheduler.
os.environ["ALPHAPILOT_BAOSTOCK_LOCK_FILE"] = f"{_tmpdir}/baostock.lock"

import pytest  # noqa: E402

from alphapilot.db.engine import init_db  # noqa: E402

init_db()


@pytest.fixture(autouse=True)
def clear_baostock_egress_state() -> Iterator[None]:
    """Egress health markers and budget counters persist on disk; no test inherits another's."""

    from pathlib import Path

    for path in Path(_tmpdir).glob("alphapilot-baostock-*"):
        path.unlink(missing_ok=True)
    yield


@pytest.fixture(autouse=True, scope="session")
def close_futu_client_after_tests() -> Iterator[None]:
    """The futu SDK spawns non-daemon threads; close the singleton so the
    pytest process can exit even when a local OpenD is running."""
    yield
    from alphapilot.futu.client import get_futu_client

    get_futu_client().close()
