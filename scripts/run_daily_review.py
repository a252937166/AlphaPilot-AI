import json

from alphapilot.data.mock import MockMarketDataProvider
from alphapilot.domain.models import ScreeningRequest
from alphapilot.prediction.baseline import BaselineForecastEngine
from alphapilot.screening.service import ScreeningService


def main() -> None:
    request = ScreeningRequest(
        symbols=["600000", "000001", "000333", "600519", "300750", "601318"],
        top_n=5,
    )
    response = ScreeningService(
        MockMarketDataProvider(), BaselineForecastEngine()
    ).run(request)
    print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
