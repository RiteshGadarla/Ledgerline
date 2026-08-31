import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import redis.asyncio as redis

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


@dataclass
class Governor:
    """Gates every outbound LLM call: per-user quota first, then RPD, then RPM."""

    redis_client: "redis.Redis"
    rpm_limits: dict[str, int]
    rpd_limits: dict[str, int]
    user_daily_quota: int
    rpm_window_seconds: float = 60.0

    async def check_and_reserve(self, model: str, user_id: str) -> Result[None]:
        user_counter = DailyCounter(self.redis_client, self.user_daily_quota)
        if not await user_counter.increment_and_check(f"llm:user_quota:{user_id}"):
            return Err("user daily LLM quota exceeded")

        rpd_counter = DailyCounter(self.redis_client, self.rpd_limits[model])
        if not await rpd_counter.increment_and_check(f"llm:rpd:{model}"):
            return Err("model daily request quota exceeded")

        bucket = RpmBucket(self.redis_client, self.rpm_limits[model], self.rpm_window_seconds)
        try:
            await bucket.acquire(f"llm:rpm:{model}")
        except RateLimited:
            return Err("model per-minute rate limit exceeded")

        return Ok(None)
