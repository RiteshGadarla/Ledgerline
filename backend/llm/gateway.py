from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from llm.backoff import LlmTransientError, with_backoff
from llm.cache import ResponseCache
from llm.client import LlmClient, LlmResponse, LlmUnavailable
from llm.governor import Governor
from money.result import Err, Ok, Result

# Two in flight per credential: enough to overlap the round trips without
# turning a retry storm into a burst the provider answers with more 503s.
CALLS_PER_KEY = 2
MAX_CONCURRENCY = 8


class _GovernorRefused(Exception):
    """Quota or rate limit, as opposed to a provider failure. Deliberately not
    an `LlmTransientError`: `with_backoff` must not retry it."""


@dataclass
class LlmGateway:
    """The one place every LLM call in the system goes through.

    Degradation contract: any failure here -- quota, rate limit, a transient
    provider error surviving backoff, or a malformed response -- is an Err,
    never a raised exception. A caller (the residue-triage pipeline) turns an
    Err into a complete, deterministic run with assist_rate=0 and
    llm_degraded=True rather than a failed run.
    """

    client: LlmClient
    governor: Governor
    cache: ResponseCache
    schema_version: str

    def suggested_concurrency(self) -> int:
        """How many calls a caller may have in flight at once.

        Scaled to the pool because free-tier quota is per credential: two keys
        are two independent per-minute windows, and holding concurrency at one
        would leave the second idle. Capped because the ceiling here is not
        this process -- it is the provider, which answers a burst with the same
        503 that made this stage unreliable in the first place. The governor
        still refuses anything over quota underneath, so this is a throttle,
        not a permission.
        """
        return min(MAX_CONCURRENCY, max(1, len(self.governor.keys) * CALLS_PER_KEY))

    async def generate(
        self,
        *,
        model: str,
        prompt: str,
        response_schema: type[BaseModel],
        user_id: str,
        fallbacks: Sequence[str] = (),
    ) -> Result[LlmResponse]:
        """Serve one prompt, trying `model` and then each of `fallbacks`.

        A model is abandoned for the next when it cannot serve the request at
        all -- refused by the governor, unavailable after backoff, or
        answering with something that does not match the schema. The cache is
        consulted for every model in the chain first, so a prompt the backup
        already answered costs nothing to repeat.
        """
        chain = (model, *fallbacks)

        for candidate in chain:
            cached = await self.cache.get(candidate, prompt, self.schema_version)
            if cached is not None and _is_valid(cached.raw_json, response_schema):
                # The stored token counts, not zeroes. A run's scoreboard
                # reports what the work cost, and answering "how much did this
                # reconciliation take?" with 0 because yesterday's identical
                # run paid for it describes the cache, not the run.
                return Ok(
                    LlmResponse(
                        raw_json=cached.raw_json,
                        input_tokens=cached.input_tokens,
                        output_tokens=cached.output_tokens,
                    )
                )

        last_reason = "no model available"
        for candidate in chain:
            # Reserved per attempt rather than once per model. Two reasons: a
            # retry is a real request and has to be counted against a real
            # credential, which the old shape did not do -- three attempts
            # spent one reservation's worth of quota. And re-reserving rotates
            # to the next key in the pool, so a retry is not a second go at
            # whichever credential just failed.
            async def _call(name: str = candidate) -> LlmResponse:
                reservation = await self.governor.check_and_reserve(name, user_id)
                if isinstance(reservation, Err):
                    raise _GovernorRefused(reservation.reason)
                return await self.client.generate(
                    model=name, prompt=prompt, response_schema=response_schema, api_key=reservation.value
                )

            try:
                response = await with_backoff(_call)
            except _GovernorRefused as exc:
                # Out of quota is not a transient failure and retrying it just
                # burns the clock: move to the next model in the chain.
                last_reason = f"{candidate}: {exc}"
                continue
            except (LlmTransientError, LlmUnavailable) as exc:
                last_reason = f"{candidate}: llm unavailable after retries: {exc}"
                continue

            if not _is_valid(response.raw_json, response_schema):
                last_reason = f"{candidate}: schema violation: model output did not match response_schema"
                continue

            await self.cache.set(
                candidate,
                prompt,
                self.schema_version,
                response.raw_json,
                response.input_tokens,
                response.output_tokens,
            )
            return Ok(response)

        return Err(last_reason)


def _is_valid(raw_json: str, response_schema: type[BaseModel]) -> bool:
    try:
        response_schema.model_validate_json(raw_json)
    except Exception:
        return False
    return True
