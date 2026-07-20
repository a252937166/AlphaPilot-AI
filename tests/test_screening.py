from alphapilot.data.mock import MockMarketDataProvider
from alphapilot.domain.models import ScreeningRequest
from alphapilot.prediction.baseline import BaselineForecastEngine
from alphapilot.screening.service import ScreeningService


def test_screening_ranks_candidates() -> None:
    service = ScreeningService(MockMarketDataProvider(), BaselineForecastEngine())
    response = service.run(
        ScreeningRequest(symbols=["600000", "000001", "000333", "600519"], top_n=3)
    )
    assert len(response.candidates) == 3
    assert [candidate.rank for candidate in response.candidates] == [1, 2, 3]
    assert response.candidates[0].score >= response.candidates[-1].score
