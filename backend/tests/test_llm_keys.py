import time

import redis.asyncio as redis
from pydantic import BaseModel

from llm.cache import ResponseCache
from llm.client import LlmResponse
from llm.gateway import LlmGateway
from llm.governor import Governor
from llm.keys import UNKEYED, ApiKey, KeyPool
from llm.models import BACKUP_MODEL, PRIMARY_MODEL
from money.result import Err, Ok


class Proposal(BaseModel):
    invoice_id: str
    confidence: float


def _governor(redis_client: "redis.Redis", keys: KeyPool, *, rpm: int, rpd: int) -> Governor:
    return Governor(
        redis_client=redis_client,
        rpm_limits={PRIMARY_MODEL: rpm, BACKUP_MODEL: rpm},
        rpd_limits={PRIMARY_MODEL: rpd, BACKUP_MODEL: rpd},
        user_daily_quota=1000,
        keys=keys,
        rpm_window_seconds=0.3,
        rpm_max_wait_seconds=0.05,
    )


def test_a_comma_separated_key_list_becomes_a_pool_of_that_size() -> None:
    pool = KeyPool.parse("key-one,key-two,key-three")
    assert len(pool) == 3
    assert [k.value for k in pool.keys] == ["key-one", "key-two", "key-three"]


def test_pool_parsing_survives_the_ways_an_env_var_gets_typed() -> None:
    # Spaces around the commas, a trailing comma, and a pasted duplicate must
    # not invent an empty key or double-count quota that does not exist.
    pool = KeyPool.parse(" key-one , key-two ,, key-one,")
    assert [k.value for k in pool.keys] == ["key-one", "key-two"]

    assert len(KeyPool.parse(None)) == 0
    assert len(KeyPool.parse("")) == 0
    assert len(KeyPool.parse("   ")) == 0


def test_a_single_key_still_parses_as_one_key() -> None:
    pool = KeyPool.parse("only-key")
    assert len(pool) == 1
    assert pool.next_key() == ApiKey(value="only-key", id=pool.keys[0].id)


def test_key_ids_are_stable_digests_and_never_expose_the_secret() -> None:
    first = KeyPool.parse("alpha,beta")
    reordered = KeyPool.parse("beta,alpha")
    ids = {k.value: k.id for k in first.keys}
    # Same key, same counter id, whatever order the env var lists them in --
    # otherwise a deploy would silently hand an exhausted key a fresh day.
    assert {k.value: k.id for k in reordered.keys} == ids
    assert "alpha" not in repr(first.keys[0])
    assert ids["alpha"] in repr(first.keys[0])


def test_rotation_hands_out_a_different_key_each_turn() -> None:
    pool = KeyPool.parse("a,b,c")
    assert [pool.next_key().value for _ in range(7)] == ["a", "b", "c", "a", "b", "c", "a"]  # type: ignore[union-attr]


def test_rotation_of_an_empty_pool_is_the_unkeyed_slot() -> None:
    pool = KeyPool.parse(None)
    assert pool.rotation() == (UNKEYED,)
    assert pool.next_key() is None


async def test_two_keys_give_twice_the_daily_ceiling(redis_client: redis.Redis) -> None:
    """The whole point of the pool: RPD is charged per key, so N keys are N
    days' worth of free tier rather than one that empties N times faster."""
    governor = _governor(redis_client, KeyPool.parse("key-a,key-b"), rpm=100, rpd=1)

    first = await governor.check_and_reserve(PRIMARY_MODEL, user_id="u1")
    second = await governor.check_and_reserve(PRIMARY_MODEL, user_id="u1")
    third = await governor.check_and_reserve(PRIMARY_MODEL, user_id="u1")

    assert isinstance(first, Ok)
    assert isinstance(second, Ok)
    assert {first.value.value, second.value.value} == {"key-a", "key-b"}, "the two calls must spend different keys"
    assert isinstance(third, Err)
    assert "daily request quota" in third.reason


async def test_a_key_that_is_out_of_quota_is_stepped_over_not_failed_on(redis_client: redis.Redis) -> None:
    pool = KeyPool.parse("key-a,key-b")
    governor = _governor(redis_client, pool, rpm=100, rpd=2)
    exhausted = pool.keys[0]

    # Spend key-a's whole day directly, then ask for calls whose rotation
    # starts on key-a.
    await redis_client.set(f"llm:rpd:{PRIMARY_MODEL}:{exhausted.id}", 2)

    reservations = [await governor.check_and_reserve(PRIMARY_MODEL, user_id="u1") for _ in range(2)]
    assert all(isinstance(r, Ok) for r in reservations)
    served = [r.value.value for r in reservations if isinstance(r, Ok)]
    assert served == ["key-b", "key-b"], "the live key should serve both calls while the spent one is skipped"


async def test_a_full_minute_window_on_one_key_falls_through_without_waiting(redis_client: redis.Redis) -> None:
    """A second key is a better answer to a full RPM window than a wait: the
    fallthrough must be immediate, not the single-key stall."""
    pool = KeyPool.parse("key-a,key-b")
    governor = _governor(redis_client, pool, rpm=1, rpd=100)
    governor.rpm_max_wait_seconds = 2.0

    # Fill key-a's window without touching key-b, so the next call starts its
    # rotation on a key it cannot use.
    await redis_client.set(f"llm:rpm:{PRIMARY_MODEL}:{pool.keys[0].id}", 1)

    started = time.monotonic()
    reservation = await governor.check_and_reserve(PRIMARY_MODEL, user_id="u1")
    elapsed = time.monotonic() - started

    assert isinstance(reservation, Ok)
    assert reservation.value.value == "key-b"
    assert elapsed < 0.5, f"falling through to the second key took {elapsed:.2f}s -- it waited on the first"


async def test_an_rpm_full_key_does_not_burn_the_day_quota_it_never_spent(redis_client: redis.Redis) -> None:
    """RPD is the scarce resource. Probing a minute-limited key must not cost
    it a request it never made, or a saturated key would drain its whole day
    without serving anything."""
    pool = KeyPool.parse("key-a,key-b")
    governor = _governor(redis_client, pool, rpm=1, rpd=100)

    # Two calls fill both keys' minute windows; the third finds every key
    # rate-limited and is refused.
    assert isinstance(await governor.check_and_reserve(PRIMARY_MODEL, user_id="u1"), Ok)
    assert isinstance(await governor.check_and_reserve(PRIMARY_MODEL, user_id="u1"), Ok)
    refused = await governor.check_and_reserve(PRIMARY_MODEL, user_id="u1")
    assert isinstance(refused, Err)
    assert "per-minute" in refused.reason

    spent = [int(await redis_client.get(f"llm:rpd:{PRIMARY_MODEL}:{key.id}") or 0) for key in pool.keys]
    assert sum(spent) == 2, f"2 served calls charged {sum(spent)} day-slots across the pool: {spent}"


class _KeyRecordingClient:
    """Records which credential each call was actually made with."""

    def __init__(self) -> None:
        self.keys_used: list[str] = []

    async def generate(
        self, *, model: str, prompt: str, response_schema: type[BaseModel], api_key: ApiKey | None = None
    ) -> LlmResponse:
        self.keys_used.append(api_key.value if api_key else "")
        return LlmResponse(raw_json='{"invoice_id": "INV1", "confidence": 0.9}', input_tokens=1, output_tokens=1)


async def test_the_gateway_calls_with_the_key_the_governor_reserved(redis_client: redis.Redis) -> None:
    """Rotation only means anything if the credential the counters were
    charged against is the one that goes out on the wire."""
    client = _KeyRecordingClient()
    gateway = LlmGateway(
        client=client,
        governor=_governor(redis_client, KeyPool.parse("key-a,key-b"), rpm=100, rpd=100),
        cache=ResponseCache(redis_client),
        schema_version="1",
    )

    for index in range(4):
        result = await gateway.generate(
            model=PRIMARY_MODEL,
            prompt=f"prompt-{index}",  # distinct prompts: a cache hit makes no call at all
            response_schema=Proposal,
            user_id="u1",
        )
        assert isinstance(result, Ok)

    assert client.keys_used == ["key-a", "key-b", "key-a", "key-b"]
