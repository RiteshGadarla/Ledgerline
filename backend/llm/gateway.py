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
        self, *, model: str, prompt: str, response_schema: type[BaseModel], user_id: str
    ) -> Result[LlmResponse]:
        cached = await self.cache.get(model, prompt, self.schema_version)
        if cached is not None and _is_valid(cached, response_schema):
            return Ok(LlmResponse(raw_json=cached, input_tokens=0, output_tokens=0))

        reservation = await self.governor.check_and_reserve(model, user_id)
        if isinstance(reservation, Err):
            return Err(reservation.reason)

        async def _call() -> LlmResponse:
            return await self.client.generate(model=model, prompt=prompt, response_schema=response_schema)

        try:
            response = await with_backoff(_call, max_attempts=3)
        except (LlmTransientError, LlmUnavailable) as exc:
            return Err(f"llm unavailable after retries: {exc}")

        if not _is_valid(response.raw_json, response_schema):
            return Err("schema violation: model output did not match response_schema")

        await self.cache.set(model, prompt, self.schema_version, response.raw_json)
        return Ok(response)


def _is_valid(raw_json: str, response_schema: type[BaseModel]) -> bool:
    try:
        response_schema.model_validate_json(raw_json)
    except Exception:
        return False
    return True
