from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.arq_pool import dispose_arq, init_arq
from app.db import dispose_db, init_db
from app.errors import register_error_handlers
from app.etag import ETagMiddleware
from app.logging_config import configure_logging
from app.redis_client import dispose_redis, init_redis
from app.routers import ask, auth, data, datasets, health, runs
from app.settings import get_settings

configure_logging("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.database_url:
        init_db(app, settings.database_url)
    if settings.redis_url:
        init_redis(app, settings.redis_url)
        await init_arq(app, settings.redis_url)
    yield
    if settings.database_url:
        await dispose_db(app)
    if settings.redis_url:
        await dispose_redis(app)
        await dispose_arq(app)


app = FastAPI(
    title="Ledgerline API",
    description=(
        "Reconciliation engine for payment ledgers: upload or generate ledger, "
        "gateway, settlement and bank data, run matching against it, and review "
        "the resulting exceptions and cash forecast."
    ),
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "health", "description": "Liveness check."},
        {
            "name": "auth",
            "description": "Session-cookie authentication: register, log in, log out, and fetch the current user.",
        },
        {
            "name": "data",
            "description": "One-off upload preview and validation, without persisting a dataset.",
        },
        {
            "name": "datasets",
            "description": "Named, reusable input sets (generated or uploaded) that a run consumes.",
        },
        {
            "name": "runs",
            "description": "Reconciliation runs: create, poll, stream progress, and review results and exceptions.",
        },
        {
            "name": "ask",
            "description": "Ask a question about a run's results, answered by an LLM grounded in that run's data.",
        },
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://ledgerline.gadarlaritesh.me"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Added last, so it sits outermost and tags the bytes that actually go on the
# wire -- CORS headers and all -- rather than an intermediate body.
app.add_middleware(ETagMiddleware)
register_error_handlers(app)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(runs.router)
app.include_router(data.router)
app.include_router(datasets.router)
app.include_router(ask.router)
