import hashlib
import json
from dataclasses import dataclass

import redis.asyncio as redis

_DEFAULT_TTL_SECONDS = 30 * 24 * 3600


@dataclass(frozen=True)
class CachedResponse:
    """A cached answer and what it cost to obtain the first time.

    The token counts are stored because a run's scoreboard reports what the
    work cost, not what went over the wire on this particular attempt. A
    cache hit that reported zero tokens made a run served entirely from
    Redis read as "4 model requests, 0 tokens" -- which looks like broken
    instrumentation rather than a saving.
    """

    raw_json: str
    input_tokens: int
    output_tokens: int


def _estimate_tokens(text: str) -> int:
    """Same rough shape llm.client uses when a provider reports no usage."""
    return max(1, len(text) // 4)


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

    async def get(self, model: str, prompt: str, schema_version: str) -> CachedResponse | None:
        value = await self.redis_client.get(self.cache_key(model, prompt, schema_version))
        if value is None:
            return None
        stored = value.decode() if isinstance(value, bytes) else value
        return _decode(stored, prompt)

    async def set(
        self, model: str, prompt: str, schema_version: str, raw_json: str, input_tokens: int, output_tokens: int
    ) -> None:
        envelope = json.dumps(
            {"v": 1, "raw_json": raw_json, "input_tokens": input_tokens, "output_tokens": output_tokens}
        )
        await self.redis_client.set(self.cache_key(model, prompt, schema_version), envelope, ex=self.ttl_seconds)


def _decode(stored: str, prompt: str) -> CachedResponse:
    """Read either shape.

    Entries written before token counts were kept are bare response JSON, and
    there are live ones with a month of TTL still to run. Rather than orphan
    them -- which would silently re-spend quota that has already been paid --
    they are read back with their cost estimated the same way an unreported
    usage count is. The envelope is distinguishable because it always carries
    a "v", which no response schema in this system uses.
    """
    try:
        loaded = json.loads(stored)
    except json.JSONDecodeError:
        return CachedResponse(stored, _estimate_tokens(prompt), _estimate_tokens(stored))

    if isinstance(loaded, dict) and "v" in loaded and "raw_json" in loaded:
        return CachedResponse(
            raw_json=str(loaded["raw_json"]),
            input_tokens=int(loaded.get("input_tokens", 0)),
            output_tokens=int(loaded.get("output_tokens", 0)),
        )
    return CachedResponse(stored, _estimate_tokens(prompt), _estimate_tokens(stored))


__all__ = ["CachedResponse", "ResponseCache"]
