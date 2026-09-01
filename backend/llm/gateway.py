from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from llm.backoff import LlmTransientError, with_backoff
from llm.cache import ResponseCache
from llm.client import LlmClient, LlmResponse, LlmUnavailable
from llm.governor import Governor
from money.result import Err, Ok, Result


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
            if cached is not None and _is_valid(cached, response_schema):
                return Ok(LlmResponse(raw_json=cached, input_tokens=0, output_tokens=0))

        last_reason = "no model available"
        for candidate in chain:
            reservation = await self.governor.check_and_reserve(candidate, user_id)
            if isinstance(reservation, Err):
                last_reason = f"{candidate}: {reservation.reason}"
                continue

            async def _call(name: str = candidate) -> LlmResponse:
                return await self.client.generate(model=name, prompt=prompt, response_schema=response_schema)

            try:
                response = await with_backoff(_call, max_attempts=3)
            except (LlmTransientError, LlmUnavailable) as exc:
                last_reason = f"{candidate}: llm unavailable after retries: {exc}"
                continue

            if not _is_valid(response.raw_json, response_schema):
                last_reason = f"{candidate}: schema violation: model output did not match response_schema"
                continue

            await self.cache.set(candidate, prompt, self.schema_version, response.raw_json)
            return Ok(response)

        return Err(last_reason)


def _is_valid(raw_json: str, response_schema: type[BaseModel]) -> bool:
    try:
        response_schema.model_validate_json(raw_json)
    except Exception:
        return False
    return True
