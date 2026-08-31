import asyncio
import random
from collections.abc import Awaitable, Callable


class LlmTransientError(Exception):
    """A retryable provider failure (429/503-equivalent)."""


async def with_backoff[T](
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.05,
) -> T:
    attempt = 0
    while True:
        try:
            return await fn()
        except LlmTransientError:
            attempt += 1
            if attempt >= max_attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1)) * (0.5 + random.random())
            await asyncio.sleep(delay)
