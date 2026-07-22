from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from alphapilot.api.routes import (
    alerts,
    dashboard,
    disclosures,
    events,
    factors,
    futu,
    health,
    jobs,
    market,
    portfolio,
    reports,
    scenarios,
    screens,
    sectors,
    stocks,
    style,
    trades,
    watchlist,
)
from alphapilot.core.config import get_settings
from alphapilot.core.logging import configure_logging
from alphapilot.db.engine import get_session, init_db
from alphapilot.futu.client import get_futu_client
from alphapilot.jobs import register_builtin_jobs
from alphapilot.jobs.scheduler import shutdown_scheduler, start_scheduler
from alphapilot.services.watchlist import seed_default_watchlist


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    init_db(settings)
    with get_session() as session:
        seed_default_watchlist(session)
    start_scheduler(settings)
    try:
        yield
    finally:
        shutdown_scheduler()
        get_futu_client().close()


register_builtin_jobs()
settings = get_settings()
app = FastAPI(
    title="AlphaPilot AI",
    version="0.2.0",
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
app.include_router(jobs.router)
app.include_router(dashboard.router)
app.include_router(factors.router)
app.include_router(stocks.router)
app.include_router(screens.router)
app.include_router(style.router)
app.include_router(market.router)
app.include_router(portfolio.router)
app.include_router(sectors.router)
app.include_router(watchlist.router)
app.include_router(alerts.router)
app.include_router(disclosures.router)
app.include_router(events.router)
app.include_router(reports.router)
app.include_router(scenarios.router)
app.include_router(trades.router)
app.include_router(trades.orders_router)
app.include_router(futu.router)
