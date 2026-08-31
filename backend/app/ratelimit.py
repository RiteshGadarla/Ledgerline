from dataclasses import dataclass

import redis.asyncio as redis

from app.errors import ProblemDetailError

# INCR + PEXPIRE-only-on-first-touch, same shape as llm/governor's window
# counter: the window starts on a key's first touch rather than an absolute
# clock boundary, so a caller's behaviour never depends on when during a
# real window it happens to start.
_WINDOW_COUNTER_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
return count
"""


class TooManyRequestsError(ProblemDetailError):
    status_code = 429
    title = "Too many requests"


@dataclass
class IpRateLimiter:
    redis_client: "redis.Redis"
    limit: int
    window_seconds: float = 60.0

    async def check(self, scope: str, ip: str) -> None:
        script = self.redis_client.register_script(_WINDOW_COUNTER_SCRIPT)
        count = await script(keys=[f"ratelimit:{scope}:{ip}"], args=[int(self.window_seconds * 1000)])
        if int(count) > self.limit:
            raise TooManyRequestsError(f"too many {scope} attempts from this address")
