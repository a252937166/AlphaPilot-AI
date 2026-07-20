from __future__ import annotations

import httpx

from alphapilot.domain.models import ScenarioRequest, ScenarioResponse


class MiroFishFinanceBridge:
    """Adapter contract for a separately deployed finance-specific MiroFish service."""

    def __init__(self, base_url: str, api_key: str | None = None, timeout_seconds: float = 120):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def run(self, request: ScenarioRequest) -> ScenarioResponse:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/v1/finance/scenarios",
                json=request.model_dump(mode="json"),
                headers=headers,
            )
            response.raise_for_status()
            return ScenarioResponse.model_validate(response.json())
