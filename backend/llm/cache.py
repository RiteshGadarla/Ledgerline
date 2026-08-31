import hashlib
from dataclasses import dataclass

import redis.asyncio as redis

_DEFAULT_TTL_SECONDS = 30 * 24 * 3600


@dataclass
class ResponseCache:
    """Response cache keyed by sha256(model + prompt + schema_version).

    The spec calls for this to live in Postgres; the db/ layer doesn't exist yet
    in this codebase (it lands with Phase 9's tenancy work), so this reuses the
    Redis connection already required for the governor. Swapping the backing
    store later is a one-line change since callers only see get()/set().
    """

    redis_client: "redis.Redis"
    ttl_seconds: int = _DEFAULT_TTL_SECONDS

    @staticmethod
    def cache_key(model: str, prompt: str, schema_version: str) -> str:
        digest = hashlib.sha256(f"{model}:{schema_version}:{prompt}".encode()).hexdigest()
        return f"llm:cache:{digest}"

    async def get(self, model: str, prompt: str, schema_version: str) -> str | None:
        value = await self.redis_client.get(self.cache_key(model, prompt, schema_version))
        return value.decode() if isinstance(value, bytes) else value

    async def set(self, model: str, prompt: str, schema_version: str, raw_json: str) -> None:
        await self.redis_client.set(self.cache_key(model, prompt, schema_version), raw_json, ex=self.ttl_seconds)
