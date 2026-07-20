from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from alphapilot.api.routes import health, market, scenarios, screens, stocks, trades
from alphapilot.core.config import get_settings
from alphapilot.core.logging import configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    yield


settings = get_settings()
app = FastAPI(
    title="AlphaPilot AI",
    version="0.1.0",
    description=(
        "Probabilistic stock research and trading-assistance foundation. "
        "Live order execution is disabled in the MVP."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(stocks.router)
app.include_router(screens.router)
app.include_router(market.router)
app.include_router(scenarios.router)
app.include_router(trades.router)
