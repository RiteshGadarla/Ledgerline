import asyncio
import random
from collections.abc import Awaitable, Callable


class LlmTransientError(Exception):
    """A retryable provider failure (429/503-equivalent)."""


# Paced for what actually fails. The provider's transient failure is a 503
# "the service is currently unavailable" under load, and it clears in seconds,
# not milliseconds. At the old 50ms base a model was given three attempts
# inside a fifth of a second and then abandoned as unavailable -- which is how
# a run ended up reporting 42 failed calls out of 51 against a model that,
# measured directly, answers three times out of three.
#
# Bounded on the other side too: every attempt is time a run spends waiting on
# a provider that may simply be down, and the stage above this one carries its
# own deadline so a bad afternoon at the vendor cannot stretch a run without
# limit. Roughly a second of sleeping per model, two models in the chain.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 0.3


async def with_backoff[T](
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
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
