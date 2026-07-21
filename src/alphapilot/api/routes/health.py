from fastapi import APIRouter, Depends
from sqlalchemy import text

from alphapilot import __version__
from alphapilot.api.dependencies import futu_client_dependency, settings_dependency
from alphapilot.core.config import Settings
from alphapilot.db.engine import get_engine
from alphapilot.futu.client import FutuClient
from alphapilot.trade.futu_gateway import FutuTradeGateway

router = APIRouter(tags=["system"])


def _database_status(settings: Settings) -> dict[str, object]:
    url = settings.database_url
    redacted = url.split("@")[-1] if "@" in url else url
    try:
        with get_engine(settings).connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"ok": True, "target": redacted}
    except Exception as exc:  # health must not raise
        return {"ok": False, "target": redacted, "error": str(exc)}


@router.get("/health")
def health(
    settings: Settings = Depends(settings_dependency),
    futu_client: FutuClient = Depends(futu_client_dependency),
) -> dict[str, object]:
    gateway = FutuTradeGateway(settings)
    return {
        "status": "ok",
        "version": __version__,
        "environment": settings.app_env,
        "default_data_provider": settings.default_data_provider,
        "database": _database_status(settings),
        "cninfo_configured": bool(
            settings.cninfo_access_key and settings.cninfo_access_secret
        ),
        "futu": futu_client.status(),
        "trading": gateway.execution_status(),
    }
