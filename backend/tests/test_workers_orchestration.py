import asyncio
import json
from typing import Any

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from datagen.generator import generate_corpus
from datagen.serialize import truth_to_dict
from db.passwords import hash_password
from db.tenancy import (
    create_dataset,
    create_run,
    create_user,
    get_run_for_user,
    recompute_dataset_status,
    upsert_dataset_file,
)
from engine.pipeline import deserialize_match_result
from ingest.dataset_records import records_to_json
from llm.cache import ResponseCache
from llm.client import FakeClient
from llm.gateway import LlmGateway
from llm.governor import Governor
from llm.models import BACKUP_MODEL, PRIMARY_MODEL
from workers.tasks import run_reconciliation


def _gateway_factory(redis_client: redis.Redis) -> Any:
    def factory(user_id: str) -> LlmGateway:
        governor = Governor(
            redis_client=redis_client,
            rpm_limits={PRIMARY_MODEL: 1000, BACKUP_MODEL: 1000},
            rpd_limits={PRIMARY_MODEL: 1000, BACKUP_MODEL: 1000},
            user_daily_quota=1000,
        )
        return LlmGateway(
            client=FakeClient({}), governor=governor, cache=ResponseCache(redis_client), schema_version="run-v1"
        )

    return factory


def _ctx(session_factory: async_sessionmaker[AsyncSession], redis_client: redis.Redis) -> dict[str, Any]:
    return {
        "db_session_factory": session_factory,
        "redis_client": redis_client,
        "gateway_factory": _gateway_factory(redis_client),
    }


async def test_run_reconciliation_completes_a_demo_run(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: redis.Redis
) -> None:
    async with db_session_factory() as db:
        user = await create_user(db, "solo", hash_password("x"))
        run, _created = await create_run(db, user.id, "demo", seed=1001, size=50)

    await run_reconciliation(_ctx(db_session_factory, redis_client), run.id, user.id)

    async with db_session_factory() as db:
        completed = await get_run_for_user(db, run.id, user.id)
    assert completed is not None
    assert completed.state == "complete"
    assert completed.error is None
    assert completed.result_json is not None
    result = deserialize_match_result(completed.result_json)
    assert isinstance(result.output_hash, str) and result.output_hash


async def test_run_reconciliation_fails_cleanly_for_a_dataset_that_does_not_exist(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: redis.Redis
) -> None:
    """A run created directly against a dataset_id that was never persisted
    (or belongs to no dataset at all) must fail with a typed error rather
    than silently pretending to complete."""
    async with db_session_factory() as db:
        user = await create_user(db, "dataset-user", hash_password("x"))
        run, _created = await create_run(db, user.id, "dataset", dataset_id="ds_1")

    await run_reconciliation(_ctx(db_session_factory, redis_client), run.id, user.id)

    async with db_session_factory() as db:
        failed = await get_run_for_user(db, run.id, user.id)
    assert failed is not None
    assert failed.state == "failed"
    assert failed.error is not None
    assert failed.result_json is None  # partial results are never presented as complete


async def test_run_reconciliation_completes_a_dataset_run_and_preserves_truth_scoring(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: redis.Redis
) -> None:
    """A dataset-backed run rebuilds its Corpus from persisted DatasetFile
    rows rather than re-generating one -- and for a "generated" dataset, the
    ground truth saved alongside it must come back too, so precision/recall
    are still real numbers instead of degrading to None the way an uploaded
    (truth-less) dataset legitimately does."""
    corpus, truth = generate_corpus(1001, 30)
    role_records: dict[str, list[Any]] = {
        "ledger": corpus.invoices,
        "gateway": corpus.payments,
        "settlement": corpus.settlements,
        "bank": corpus.bank_lines,
    }
    async with db_session_factory() as db:
        user = await create_user(db, "dataset-runner", hash_password("x"))
        dataset = await create_dataset(
            db,
            user.id,
            "generated corpus",
            "generated",
            seed=1001,
            size=30,
            truth_json=json.dumps(truth_to_dict(truth)),
        )
        for role, records in role_records.items():
            await upsert_dataset_file(
                db,
                dataset.id,
                role,  # type: ignore[arg-type]
                raw_filename=None,
                raw_content_type=None,
                raw_content=None,
                records_json=records_to_json(role, records),  # type: ignore[arg-type]
                row_count=len(records),
                valid_count=len(records),
            )
        await recompute_dataset_status(db, dataset.id)
        run, _created = await create_run(db, user.id, "dataset", dataset_id=dataset.id)

    await run_reconciliation(_ctx(db_session_factory, redis_client), run.id, user.id)

    async with db_session_factory() as db:
        completed = await get_run_for_user(db, run.id, user.id)
    assert completed is not None
    assert completed.state == "complete"
    assert completed.error is None
    assert completed.result_json is not None
    assert completed.metrics_json is not None
    metrics = json.loads(completed.metrics_json)
    assert metrics["precision"] is not None
    assert metrics["recall"] is not None


async def test_ten_concurrent_runs_from_different_users_do_not_bleed_state(
    db_session_factory: async_sessionmaker[AsyncSession], redis_client: redis.Redis
) -> None:
    users_and_runs = []
    async with db_session_factory() as db:
        for i in range(10):
            user = await create_user(db, f"concurrent-{i}", hash_password("x"))
            run, _created = await create_run(db, user.id, "demo", seed=1000 + i, size=30)
            users_and_runs.append((user, run))

    ctx = _ctx(db_session_factory, redis_client)
    await asyncio.gather(*(run_reconciliation(ctx, run.id, user.id) for user, run in users_and_runs))

    async with db_session_factory() as db:
        completed = [await get_run_for_user(db, run.id, user.id) for user, run in users_and_runs]

    assert all(c is not None and c.state == "complete" for c in completed)
    # Each run used a distinct seed and size; a cross-run bleed would show up
    # as two runs sharing an output_hash despite the corpora differing.
    output_hashes = {deserialize_match_result(c.result_json).output_hash for c in completed if c and c.result_json}
    assert len(output_hashes) == 10


async def test_pubsub_channel_fans_out_to_multiple_subscribers(redis_client: redis.Redis) -> None:
    """The mechanism behind "any API replica can serve any run's stream":
    two independent subscribers to the same run channel both receive the
    same published event, exactly as two separate API replicas would."""
    channel = "run:fanout-test"
    subscriber_a = redis_client.pubsub()
    subscriber_b = redis_client.pubsub()
    await subscriber_a.subscribe(channel)
    await subscriber_b.subscribe(channel)

    await redis_client.publish(channel, json.dumps({"state": "complete"}))

    message_a = await _next_message(subscriber_a)
    message_b = await _next_message(subscriber_b)

    assert json.loads(message_a["data"]) == {"state": "complete"}
    assert json.loads(message_b["data"]) == {"state": "complete"}

    await subscriber_a.unsubscribe(channel)
    await subscriber_b.unsubscribe(channel)


async def _next_message(pubsub: Any) -> Any:
    async for message in pubsub.listen():
        if message["type"] == "message":
            return message
    raise AssertionError("pubsub closed without a message")
