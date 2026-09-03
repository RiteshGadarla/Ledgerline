from typing import Any

import redis.asyncio as redis
from arq.connections import RedisSettings

from app.logging_config import configure_logging
from app.settings import get_settings
from db.base import make_engine, make_session_factory
from llm.factory import build_gateway
from workers.tasks import run_reconciliation

# At import, so anything logged while the worker is coming up lands in
# worker.log rather than nowhere. The worker previously configured no logging
# at all, which left workers/tasks.py's logger -- the one that reports a run
# failing -- writing an unformatted line to stderr at the root default.
configure_logging("worker")


async def startup(ctx: dict[str, Any]) -> None:
    # Again here, and this is the call that sticks: arq's CLI imports this
    # module to find WorkerSettings and only then runs its own dictConfig,
    # which hands the `arq` logger a handler in arq's format. Re-running the
    # configuration from a hook arq calls afterwards puts every job log back
    # into the same JSON stream as everything else.
    configure_logging("worker")
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
