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
os.environ["ALPHAPILOT_DEFAULT_DATA_PROVIDER"] = "mock"

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
