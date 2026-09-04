import redis.asyncio as redis

from llm.cache import ResponseCache
from llm.client import FakeClient, GeminiClient, LlmClient
from llm.gateway import LlmGateway
from llm.governor import Governor
from llm.keys import KeyPool
from llm.limits import load_model_limits, user_daily_quota


def build_gateway(redis_client: "redis.Redis", schema_version: str, keys: KeyPool) -> LlmGateway:
    """The one place a real (or, absent an API key, always-degraded) gateway
    is assembled from the governor/cache/client pieces -- used by the worker
    and by anything else (the ask agent, later) that needs a live gateway
    rather than a test double wired in by hand."""
    client: LlmClient = GeminiClient(keys) if keys else FakeClient({})
    limits = load_model_limits()
    governor = Governor(
        redis_client=redis_client,
        rpm_limits={model: limit.rpm for model, limit in limits.items()},
        rpd_limits={model: limit.rpd for model, limit in limits.items()},
        user_daily_quota=user_daily_quota(len(keys)),
        keys=keys,
    )
    return LlmGateway(
        client=client, governor=governor, cache=ResponseCache(redis_client), schema_version=schema_version
    )
