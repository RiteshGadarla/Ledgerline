import json

import redis.asyncio as redis
from pydantic import BaseModel

from llm.cache import ResponseCache
from llm.client import LlmResponse
from llm.gateway import LlmGateway
from llm.governor import Governor
from llm.keys import ApiKey, KeyPool
from llm.limits import user_daily_quota
from llm.models import BACKUP_MODEL, PRIMARY_MODEL
from money.result import Err, Ok


class Proposal(BaseModel):
    invoice_id: str
    confidence: float


_PAYLOAD = '{"invoice_id": "INV1", "confidence": 0.9}'


class _CountingClient:
    """Reports usage the way a real provider does, and counts wire calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(
        self, *, model: str, prompt: str, response_schema: type[BaseModel], api_key: ApiKey | None = None
    ) -> LlmResponse:
        self.calls += 1
        return LlmResponse(raw_json=_PAYLOAD, input_tokens=412, output_tokens=57)


def _gateway(redis_client: "redis.Redis", client: _CountingClient, keys: str = "key-a") -> LlmGateway:
    pool = KeyPool.parse(keys)
    governor = Governor(
        redis_client=redis_client,
        rpm_limits={PRIMARY_MODEL: 100, BACKUP_MODEL: 100},
        rpd_limits={PRIMARY_MODEL: 100, BACKUP_MODEL: 100},
        user_daily_quota=1000,
        keys=pool,
    )
    return LlmGateway(
        client=client, governor=governor, cache=ResponseCache(redis_client), schema_version="cost-v1"
    )


async def test_a_cached_answer_still_reports_what_the_work_cost(redis_client: redis.Redis) -> None:
    """The bug this covers: a run served entirely from cache read as "6 model
    requests, 0 tokens", which looks like broken instrumentation. The figure
    describes the work, so the second run must cost what the first one did."""
    client = _CountingClient()
    gateway = _gateway(redis_client, client)

    first = await gateway.generate(
        model=PRIMARY_MODEL, prompt="same question", response_schema=Proposal, user_id="u1"
    )
    second = await gateway.generate(
        model=PRIMARY_MODEL, prompt="same question", response_schema=Proposal, user_id="u1"
    )

    assert isinstance(first, Ok) and isinstance(second, Ok)
    assert client.calls == 1, "the second call must be served from cache, not the provider"
    assert (second.value.input_tokens, second.value.output_tokens) == (412, 57)
    assert second.value.raw_json == first.value.raw_json


async def test_cache_entries_written_before_costs_were_kept_are_still_read(redis_client: redis.Redis) -> None:
    """Live entries carry a month of TTL. Orphaning them on deploy would
    re-spend quota that has already been paid, so the bare-string shape is
    read back with its cost estimated rather than treated as a miss."""
    cache = ResponseCache(redis_client)
    await redis_client.set(cache.cache_key(PRIMARY_MODEL, "legacy prompt", "cost-v1"), _PAYLOAD)

    client = _CountingClient()
    result = await _gateway(redis_client, client).generate(
        model=PRIMARY_MODEL, prompt="legacy prompt", response_schema=Proposal, user_id="u1"
    )

    assert isinstance(result, Ok)
    assert client.calls == 0, "a legacy entry must still be a hit"
    assert result.value.raw_json == _PAYLOAD
    assert result.value.input_tokens > 0 and result.value.output_tokens > 0


async def test_a_stored_entry_is_an_envelope_not_the_bare_answer(redis_client: redis.Redis) -> None:
    client = _CountingClient()
    await _gateway(redis_client, client).generate(
        model=PRIMARY_MODEL, prompt="envelope", response_schema=Proposal, user_id="u1"
    )

    raw = await redis_client.get(ResponseCache.cache_key(PRIMARY_MODEL, "envelope", "cost-v1"))
    stored = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    assert stored["input_tokens"] == 412
    assert stored["output_tokens"] == 57
    assert json.loads(stored["raw_json"]) == json.loads(_PAYLOAD)


def test_the_per_user_daily_ceiling_widens_with_the_key_pool() -> None:
    """Every other limit here is charged per key and widens on its own. This
    one is keyed by user, so without scaling it becomes the binding limit the
    moment a second key is added -- 25 calls a day against 750 available."""
    one = user_daily_quota(1)
    assert user_daily_quota(3) == one * 3
    assert user_daily_quota(0) == one, "no keys configured is still one slot's worth, not zero"


def test_an_explicit_ceiling_is_honoured_as_an_absolute_figure(monkeypatch) -> None:
    monkeypatch.setenv("LLM_USER_DAILY_QUOTA", "40")
    import importlib

    import llm.limits as limits

    importlib.reload(limits)
    try:
        assert limits.user_daily_quota(3) == 40, "someone setting this names the ceiling, not a per-key rate"
    finally:
        monkeypatch.delenv("LLM_USER_DAILY_QUOTA", raising=False)
        importlib.reload(limits)


async def test_a_refusal_does_not_spend_the_user_quota_it_never_used(redis_client: redis.Redis) -> None:
    """A saturated pool used to eat a user's whole day without reaching the
    provider: the refusals themselves became the spend."""
    governor = Governor(
        redis_client=redis_client,
        rpm_limits={PRIMARY_MODEL: 100, BACKUP_MODEL: 100},
        rpd_limits={PRIMARY_MODEL: 0, BACKUP_MODEL: 0},  # every key is out of day quota
        user_daily_quota=10,
        keys=KeyPool.parse("key-a,key-b"),
    )

    for _ in range(5):
        assert isinstance(await governor.check_and_reserve(PRIMARY_MODEL, user_id="u9"), Err)

    spent = int(await redis_client.get("llm:user_quota:u9") or 0)
    assert spent == 0, f"5 refused reservations charged the user {spent} slots"
