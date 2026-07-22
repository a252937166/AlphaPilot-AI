from __future__ import annotations

import logging
from typing import Any

import pytest

from alphapilot.cninfo.client import CninfoClient, CninfoError
from alphapilot.core.config import Settings
from alphapilot.core.logging import configure_logging


def test_configure_logging_suppresses_http_transport_info() -> None:
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")
    previous_httpx = httpx_logger.level
    previous_httpcore = httpcore_logger.level
    try:
        configure_logging("INFO")
        assert httpx_logger.level == logging.WARNING
        assert httpcore_logger.level == logging.WARNING
    finally:
        httpx_logger.setLevel(previous_httpx)
        httpcore_logger.setLevel(previous_httpcore)


def test_missing_cninfo_token_does_not_echo_oauth_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_marker = "must-not-appear"
    client = CninfoClient(
        Settings(
            cninfo_access_key="test-access-key",
            cninfo_access_secret="test-access-secret",
        )
    )

    class FakeResponse:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"refresh_token": secret_marker, "expires_in": 1800}

    class FakeHttpClient:
        def __enter__(self) -> FakeHttpClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, *_args: object, **_kwargs: object) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(client, "_client", lambda _base_url: FakeHttpClient())

    with pytest.raises(CninfoError, match="missing access_token") as caught:
        client._access_token()

    assert secret_marker not in str(caught.value)
