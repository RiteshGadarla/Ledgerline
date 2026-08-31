import json

import redis.asyncio as redis
from pydantic import BaseModel

from llm.cache import ResponseCache
from llm.client import FakeClient, LlmResponse
from llm.gateway import LlmGateway
from llm.governor import Governor
from money.result import Err, Ok


class Proposal(BaseModel):
    invoice_id: str
    confidence: float


def _gateway(
    redis_client: redis.Redis, client: FakeClient, *, rpm: int = 100, rpd: int = 100, quota: int = 100
) -> LlmGateway:
    governor = Governor(
        redis_client=redis_client,
        rpm_limits={"gemini-2.5-flash": rpm},
        rpd_limits={"gemini-2.5-flash": rpd},
        user_daily_quota=quota,
    )
    return LlmGateway(client=client, governor=governor, cache=ResponseCache(redis_client), schema_version="1")


async def test_identical_request_twice_issues_one_upstream_call(redis_client: redis.Redis) -> None:
    fixture = json.dumps({"invoice_id": "INV1", "confidence": 0.95})
    client = FakeClient({"match these": fixture})
    gateway = _gateway(redis_client, client)

    first = await gateway.generate(
        model="gemini-2.5-flash", prompt="match these", response_schema=Proposal, user_id="u1"
    )
    second = await gateway.generate(
        model="gemini-2.5-flash", prompt="match these", response_schema=Proposal, user_id="u1"
    )

    assert isinstance(first, Ok)
    assert isinstance(second, Ok)
    assert first.value.raw_json == second.value.raw_json
    assert len(client.calls) == 1


async def test_governor_denial_surfaces_as_err_not_an_exception(redis_client: redis.Redis) -> None:
    client = FakeClient({"match these": json.dumps({"invoice_id": "INV1", "confidence": 0.95})})
    gateway = _gateway(redis_client, client, quota=0)

    result = await gateway.generate(
        model="gemini-2.5-flash", prompt="match these", response_schema=Proposal, user_id="u1"
    )

    assert isinstance(result, Err)
    assert len(client.calls) == 0


class _RawClient:
    """Returns raw text unvalidated, simulating a live model that produced malformed JSON."""

    def __init__(self, raw_json: str) -> None:
        self._raw_json = raw_json

    async def generate(self, *, model: str, prompt: str, response_schema: type[BaseModel]) -> LlmResponse:
        return LlmResponse(raw_json=self._raw_json, input_tokens=1, output_tokens=1)


async def test_schema_violation_is_rejected_never_partially_applied(redis_client: redis.Redis) -> None:
    client = _RawClient('{"invoice_id": "INV1"}')  # missing required "confidence"
    gateway = _gateway(redis_client, client)  # type: ignore[arg-type]

    result = await gateway.generate(
        model="gemini-2.5-flash", prompt="bad prompt", response_schema=Proposal, user_id="u1"
    )

    assert isinstance(result, Err)
    assert "schema" in result.reason
    cached = await ResponseCache(redis_client).get("gemini-2.5-flash", "bad prompt", "1")
    assert cached is None, "a malformed response must never be cached"


class _AlwaysTransientClient:
    """Simulates a 429/503 storm: every call fails with a retryable provider error."""

    def __init__(self) -> None:
        self.call_count = 0

    async def generate(self, *, model: str, prompt: str, response_schema: type[BaseModel]) -> LlmResponse:
        from llm.backoff import LlmTransientError

        self.call_count += 1
        raise LlmTransientError("simulated 429")


async def test_429_storm_degrades_to_err_after_retries_exhausted(redis_client: redis.Redis) -> None:
    client = _AlwaysTransientClient()
    gateway = _gateway(redis_client, client)  # type: ignore[arg-type]

    result = await gateway.generate(model="gemini-2.5-flash", prompt="anything", response_schema=Proposal, user_id="u1")

    assert isinstance(result, Err)
    assert client.call_count == 3  # max_attempts in with_backoff
