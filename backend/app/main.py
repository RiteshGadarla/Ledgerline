from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.arq_pool import dispose_arq, init_arq
from app.db import dispose_db, init_db
from app.errors import register_error_handlers
from app.logging_config import configure_logging
from app.redis_client import dispose_redis, init_redis
from app.routers import ask, auth, data, health, runs
from app.settings import get_settings

configure_logging()


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


app = FastAPI(title="Ledgerline API", lifespan=lifespan)
register_error_handlers(app)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(runs.router)
app.include_router(data.router)
app.include_router(ask.router)
