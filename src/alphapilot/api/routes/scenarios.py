from fastapi import APIRouter

from alphapilot.domain.models import ScenarioRequest, ScenarioResponse
from alphapilot.scenario.heuristic import HeuristicScenarioEngine

router = APIRouter(prefix="/v1/scenarios", tags=["scenarios"])


@router.post("/run", response_model=ScenarioResponse)
def run_scenario(request: ScenarioRequest) -> ScenarioResponse:
    return HeuristicScenarioEngine().run(request)
