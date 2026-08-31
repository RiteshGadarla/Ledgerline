from typing import Any

import redis.asyncio as redis
from arq.connections import RedisSettings

from app.settings import get_settings
from db.base import make_engine, make_session_factory
from llm.factory import build_gateway
from workers.tasks import run_reconciliation


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be set for the worker")
    engine = make_engine(settings.database_url)
    ctx["db_engine"] = engine
    ctx["db_session_factory"] = make_session_factory(engine)
    redis_url = settings.redis_url or "redis://localhost:6379"
    ctx["redis_client"] = redis.from_url(redis_url)  # type: ignore[no-untyped-call]
    ctx["gateway_factory"] = lambda user_id: build_gateway(
        ctx["redis_client"], schema_version="run-v1", api_key=settings.gemini_api_key
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["db_engine"].dispose()
    await ctx["redis_client"].aclose()


class WorkerSettings:
    functions = [run_reconciliation]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379")
