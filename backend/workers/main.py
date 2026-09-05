import logging
from typing import Any

import redis.asyncio as redis
from arq.connections import RedisSettings

from app.logging_config import configure_logging
from app.settings import get_settings
from db.base import make_engine, make_session_factory
from db.tenancy import fail_orphaned_runs
from llm.factory import build_gateway
from llm.keys import KeyPool
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
        ctx["redis_client"], schema_version="run-v1", keys=KeyPool.parse(settings.gemini_api_key)
    )

    # Anything still mid-flight belongs to a process that is gone -- killed,
    # OOMed, or taken down with the machine -- because a run only leaves a
    # non-terminal state from inside the job that owns it. Sweeping here is
    # what stops yesterday's abandoned run from streaming forever in the
    # console with nothing behind it.
    async with ctx["db_session_factory"]() as db:
        orphaned = await fail_orphaned_runs(db, "run abandoned: the worker stopped while it was in flight")
    if orphaned:
        logging.getLogger("ledgerline.worker").warning(
            "startup: failed %d run(s) left in flight by a previous worker", orphaned
        )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["db_engine"].dispose()
    await ctx["redis_client"].aclose()


class WorkerSettings:
    functions = [run_reconciliation]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url or "redis://localhost:6379")

    # arq's default job_timeout is 300s. Any run longer than five minutes was
    # being cancelled out from under itself, and because cancellation raises a
    # BaseException the job body's own error handling never saw it -- the run
    # row was simply left mid-flight. Generous enough now that only a genuinely
    # stuck run reaches it, and tasks.py records the failure when one does.
    job_timeout = 1800

    # A failed run is already recorded terminally by the job itself, so a retry
    # would re-run the whole pipeline against a row that already reads "failed"
    # and spend a second helping of model quota reaching the same answer.
    max_tries = 1

    # This box has 1.9 GB of RAM and no swap. Left at arq's default of 10, ten
    # concurrent corpora is precisely how the worker gets OOM-killed.
    max_jobs = 3
