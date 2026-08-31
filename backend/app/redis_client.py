import redis.asyncio as redis
from fastapi import FastAPI, Request

from app.errors import ProblemDetailError


def init_redis(app: FastAPI, redis_url: str) -> None:
    app.state.redis_client = redis.from_url(redis_url)


async def dispose_redis(app: FastAPI) -> None:
    await app.state.redis_client.aclose()


def get_redis(request: Request) -> "redis.Redis":
    client = getattr(request.app.state, "redis_client", None)
    if client is None:
        raise ProblemDetailError("redis is not configured", status_code=503)
    return client  # type: ignore[no-any-return]
