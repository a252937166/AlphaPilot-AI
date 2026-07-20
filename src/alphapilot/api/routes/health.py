from fastapi import APIRouter

from alphapilot import __version__
from alphapilot.core.config import get_settings
from alphapilot.trade.futu_gateway import FutuTradeGateway

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, object]:
    settings = get_settings()
    gateway = FutuTradeGateway(settings)
    return {
        "status": "ok",
        "version": __version__,
        "environment": settings.app_env,
        "default_data_provider": settings.default_data_provider,
        "trading": gateway.execution_status(),
    }
