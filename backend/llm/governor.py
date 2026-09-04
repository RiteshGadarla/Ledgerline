import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import redis.asyncio as redis

from llm.keys import ApiKey, KeyPool
from money.result import Err, Ok, Result

# Atomic window counter: increments KEYS[1], sets its TTL (in ms, ARGV[1]) only
# on first touch so the window starts relative to first use rather than an
# absolute clock boundary, and reports whether the increment stayed within
# ARGV[2]. Single EVAL call, so it stays correct across every API and worker
# replica sharing Redis.
_WINDOW_COUNTER_SCRIPT = """
local current = redis.call("INCR", KEYS[1])
if current == 1 then
    redis.call("PEXPIRE", KEYS[1], ARGV[1])
end
if current > tonumber(ARGV[2]) then
    return 0
else
    return 1
end
"""


class RateLimited(Exception):
    pass


@dataclass(frozen=True)
class GovernorDenial:
    reason: str


@dataclass
class RpmBucket:
    """Fixed-window RPM limiter. Blocks and retries within the window rather than
    surfacing a 429 to the caller; only gives up once max_wait_seconds is spent."""

    redis_client: "redis.Redis"
    limit: int
    window_seconds: float = 60.0
    poll_interval: float = 0.02
    max_wait_seconds: float = 5.0

    async def acquire(self, key: str) -> None:
        # The window starts on the key's first touch (TTL set only then), not on an
        # absolute clock boundary -- so it doesn't matter when during a real minute
        # a caller happens to start, and it stays fast and deterministic in tests.
        script = self.redis_client.register_script(_WINDOW_COUNTER_SCRIPT)
        deadline = time.monotonic() + self.max_wait_seconds
        ttl_ms = max(1, round(self.window_seconds * 1000))
        while True:
            allowed = await script(keys=[key], args=[ttl_ms, self.limit])
            if allowed:
                return
            if time.monotonic() >= deadline:
                raise RateLimited(f"rate limit exceeded for {key}")
            await asyncio.sleep(self.poll_interval)


def _ms_until_next_utc_midnight() -> int:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1000, round((tomorrow - now).total_seconds() * 1000))


@dataclass
class DailyCounter:
    """Counter reset at the next UTC-midnight quota boundary, for RPD and per-user
    quotas. TTL is anchored to that boundary (not to first-touch + 24h) so a
    restart mid-day does not grant a fresh 24 hours; persisted quota use is
    honoured until the real reset."""

    redis_client: "redis.Redis"
    limit: int
    ttl_ms_override: int | None = None  # test seam; production always resets at UTC midnight

    async def increment_and_check(self, key: str) -> bool:
        script = self.redis_client.register_script(_WINDOW_COUNTER_SCRIPT)
        ttl_ms = self.ttl_ms_override if self.ttl_ms_override is not None else _ms_until_next_utc_midnight()
        allowed = await script(keys=[key], args=[ttl_ms, self.limit])
        return bool(allowed)

    async def is_exhausted(self, key: str) -> bool:
        """A read-only look at the counter.

        Lets a caller skip a credential that is already out of quota without
        spending a slot to discover it -- a day slot is not refunded until
        UTC midnight, so probing with an increment would make an exhausted key
        quietly eat quota from the keys still working. increment_and_check
        remains the authoritative, atomic gate; this only avoids the obvious
        waste.
        """
        current = await self.redis_client.get(key)
        return current is not None and int(current) >= self.limit


@dataclass
class Governor:
    """Gates every outbound LLM call: per-user quota first, then the per-key
    RPD and RPM buckets of whichever credential is due a turn.

    Quota on the free tier is charged per API key, so the counters are keyed
    per (model, key) and the pool is walked in rotation order until one key
    can serve the call. Two keys therefore mean twice the daily ceiling, not
    the same ceiling reached twice as fast -- and a key that runs out is
    stepped over instead of failing the request.
    """

    redis_client: "redis.Redis"
    rpm_limits: dict[str, int]
    rpd_limits: dict[str, int]
    user_daily_quota: int
    keys: KeyPool = field(default_factory=KeyPool)
    rpm_window_seconds: float = 60.0
    rpm_max_wait_seconds: float = 5.0

    async def check_and_reserve(self, model: str, user_id: str) -> Result[ApiKey]:
        """Reserve one call, returning the key it must be made with."""
        user_counter = DailyCounter(self.redis_client, self.user_daily_quota)
        if not await user_counter.increment_and_check(f"llm:user_quota:{user_id}"):
            return Err("user daily LLM quota exceeded")

        rpd_counter = DailyCounter(self.redis_client, self.rpd_limits[model])
        rotation = self.keys.rotation()
        denial = "model daily request quota exceeded"

        for position, key in enumerate(rotation):
            rpd_key = f"llm:rpd:{model}:{key.id}"
            if await rpd_counter.is_exhausted(rpd_key):
                denial = "model daily request quota exceeded"
                continue

            # Only the last candidate waits out the minute window: with another
            # key still to try, waiting on this one would be slower than simply
            # using the next credential, which has its own untouched window.
            is_last = position == len(rotation) - 1
            bucket = RpmBucket(
                self.redis_client,
                self.rpm_limits[model],
                self.rpm_window_seconds,
                max_wait_seconds=self.rpm_max_wait_seconds if is_last else 0.0,
            )
            try:
                await bucket.acquire(f"llm:rpm:{model}:{key.id}")
            except RateLimited:
                denial = "model per-minute rate limit exceeded"
                continue

            if not await rpd_counter.increment_and_check(rpd_key):
                denial = "model daily request quota exceeded"
                continue

            return Ok(key)

        # No key could serve this, so no call will be made -- give the user
        # back the slot that was charged up front. Charging first is what
        # makes the quota atomic under concurrent callers, but leaving it
        # charged meant a saturated pool ate a user's whole day without ever
        # reaching the provider: the refusals themselves became the spend.
        await self.redis_client.decr(f"llm:user_quota:{user_id}")
        return Err(denial)
