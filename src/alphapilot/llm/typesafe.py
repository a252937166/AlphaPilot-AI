"""Minimal client for TypeSafe AI's System One endpoint (the ``jev`` models).

jev returns typed answers (choice, score, noul) with probabilities instead of text.
The model id is pinned: a response that reports another model is rejected, so a
silent upgrade cannot change a pre-registered experiment.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
RETRY_STATUS = frozenset({429, 500, 502, 503, 504, 529})


class JevError(RuntimeError):
    """jev could not return a valid answer."""


class JevClient:
    def __init__(
        self,
        api_key: str | None,
        model: str = "jev-1.13.0",
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise JevError("jev API key is not configured (ALPHAPILOT_JEV_API_KEY)")
        self.model = model
        self.max_retries = max_retries
        self._key = api_key
        self._sleep = sleep
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def ask(self, state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
        """One System One call; retries transient failures, validates the model id."""

        payload = {"model": self.model, "state": state, "questions": questions}
        last = "no attempt"
        for attempt in range(self.max_retries + 1):
            if attempt:
                self._sleep(float(2 * attempt - 1))
            try:
                response = self._client.post(
                    ENDPOINT, headers={"Authorization": f"Bearer {self._key}"}, json=payload
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = type(exc).__name__
                continue
            if response.status_code in RETRY_STATUS:
                last = f"http {response.status_code}"
                continue
            if response.status_code != 200:
                raise JevError(f"jev http {response.status_code}")
            try:
                data = response.json()
            except ValueError as exc:
                raise JevError("jev returned non-JSON") from exc
            if data.get("model") != self.model:
                raise JevError(f"jev model mismatch: {data.get('model')!r} != {self.model!r}")
            return data
        raise JevError(f"jev failed after {self.max_retries + 1} attempts: {last}")

    def close(self) -> None:
        self._client.close()
