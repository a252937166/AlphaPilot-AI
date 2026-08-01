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
# Unit and API tests must never inherit the developer's real local LLM credentials.
os.environ["ALPHAPILOT_LLM_BASE_URL"] = ""
os.environ["ALPHAPILOT_LLM_API_KEY"] = ""
os.environ["ALPHAPILOT_LLM_MODEL"] = "qwen3.6-flash"
os.environ["ALPHAPILOT_LLM_PURPOSE_MODELS"] = "{}"

import pytest  # noqa: E402

from alphapilot.db.engine import init_db  # noqa: E402

init_db()


@pytest.fixture(autouse=True, scope="session")
def close_futu_client_after_tests() -> Iterator[None]:
    """The futu SDK spawns non-daemon threads; close the singleton so the
    pytest process can exit even when a local OpenD is running."""
    yield
    from alphapilot.futu.client import get_futu_client

    get_futu_client().close()
