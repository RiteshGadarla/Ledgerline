import asyncio
import time

import redis.asyncio as redis

from llm.governor import DailyCounter, Governor, RpmBucket
from money.result import Err, Ok


async def test_rpm_bucket_allows_up_to_limit_within_a_window(redis_client: redis.Redis) -> None:
    bucket = RpmBucket(redis_client, limit=10, window_seconds=0.3, max_wait_seconds=0.05)
    for _ in range(10):
        await bucket.acquire("test-model")
    # the 11th call in the same window should not be grantable within the tiny max_wait
    from llm.governor import RateLimited

    try:
        await bucket.acquire("test-model")
        raised = False
    except RateLimited:
        raised = True
    assert raised


async def test_20_concurrent_calls_against_10_rpm_complete_across_two_windows(redis_client: redis.Redis) -> None:
    bucket = RpmBucket(redis_client, limit=10, window_seconds=0.2, max_wait_seconds=2.0)

    start = time.monotonic()
    await asyncio.gather(*(bucket.acquire("burst-model") for _ in range(20)))
    elapsed = time.monotonic() - start

    # 20 requests at 10/window must span at least one extra window boundary
    assert elapsed >= 0.2 * 0.5


async def test_multi_replica_governors_share_the_same_bucket(redis_client: redis.Redis) -> None:
    bucket_a = RpmBucket(redis_client, limit=10, window_seconds=0.3, max_wait_seconds=0.05)
    bucket_b = RpmBucket(redis_client, limit=10, window_seconds=0.3, max_wait_seconds=0.05)

    for _ in range(5):
        await bucket_a.acquire("shared-model")
    for _ in range(5):
        await bucket_b.acquire("shared-model")

    from llm.governor import RateLimited

    try:
        await bucket_a.acquire("shared-model")
        exceeded = False
    except RateLimited:
        exceeded = True
    assert exceeded, "two replicas sharing Redis jointly exceeded the 10 RPM bucket without being throttled"


async def test_daily_counter_resets_are_isolated_per_key(redis_client: redis.Redis) -> None:
    counter = DailyCounter(redis_client, limit=3)
    assert await counter.increment_and_check("model-a") is True
    assert await counter.increment_and_check("model-a") is True
    assert await counter.increment_and_check("model-a") is True
    assert await counter.increment_and_check("model-a") is False
    # a different key is unaffected
    assert await counter.increment_and_check("model-b") is True


async def test_user_quota_is_checked_before_the_global_bucket(redis_client: redis.Redis) -> None:
    governor = Governor(
        redis_client=redis_client,
        rpm_limits={"gemini-2.5-flash": 100},
        rpd_limits={"gemini-2.5-flash": 100},
        user_daily_quota=1,
    )

    first = await governor.check_and_reserve("gemini-2.5-flash", user_id="user-1")
    assert isinstance(first, Ok)

    second = await governor.check_and_reserve("gemini-2.5-flash", user_id="user-1")
    assert isinstance(second, Err)
    assert "quota" in second.reason

    # a different user is unaffected by user-1's exhausted quota
    third = await governor.check_and_reserve("gemini-2.5-flash", user_id="user-2")
    assert isinstance(third, Ok)


async def test_rpd_limit_is_enforced_independently_of_rpm(redis_client: redis.Redis) -> None:
    governor = Governor(
        redis_client=redis_client,
        rpm_limits={"gemini-2.5-flash": 1000},
        rpd_limits={"gemini-2.5-flash": 2},
        user_daily_quota=1000,
    )

    assert isinstance(await governor.check_and_reserve("gemini-2.5-flash", user_id="u1"), Ok)
    assert isinstance(await governor.check_and_reserve("gemini-2.5-flash", user_id="u2"), Ok)
    third = await governor.check_and_reserve("gemini-2.5-flash", user_id="u3")
    assert isinstance(third, Err)
    assert "daily request quota" in third.reason
