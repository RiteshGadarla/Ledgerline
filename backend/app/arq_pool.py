from arq.connections import ArqRedis, RedisSettings, create_pool
from fastapi import FastAPI, Request

from app.errors import ProblemDetailError


async def init_arq(app: FastAPI, redis_url: str) -> None:
    app.state.arq_pool = await create_pool(RedisSettings.from_dsn(redis_url))


async def dispose_arq(app: FastAPI) -> None:
    await app.state.arq_pool.aclose()


def get_arq_pool(request: Request) -> ArqRedis:
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise ProblemDetailError("job queue is not configured", status_code=503)
    return pool  # type: ignore[no-any-return]
