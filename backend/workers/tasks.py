import json
import logging
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from contracts.corpus import Corpus
from datagen.generator import generate_corpus
from datagen.models import Truth
from datagen.serialize import truth_from_dict
from db.tenancy import (
    REQUIRED_DATASET_ROLES,
    complete_run,
    fail_run,
    get_dataset_for_user,
    get_run_for_user,
    list_dataset_files,
    transition_run_state,
)
from db.tenancy import RunState as DbRunState
from engine.pipeline import serialize_match_result
from ingest.dataset_records import build_corpus
from workers.forecast import build_forecast
from workers.pipeline import RunState, run_pipeline

logger = logging.getLogger("ledgerline.worker")

DEFAULT_DEMO_SEED = 1001
DEFAULT_DEMO_SIZE = 150


async def _load_dataset_corpus(db: AsyncSession, dataset_id: str, user_id: str) -> tuple[Corpus, Truth | None] | None:
    """None means the dataset doesn't exist for this user or isn't ready --
    the caller turns that into a failed run rather than a partial one."""
    dataset = await get_dataset_for_user(db, dataset_id, user_id)
    if dataset is None or dataset.status != "ready":
        return None

    files = await list_dataset_files(db, dataset_id)
    by_role = {f.role: f for f in files}
    if not all(role in by_role for role in REQUIRED_DATASET_ROLES):
        return None

    corpus = build_corpus({role: by_role[role].records_json for role in REQUIRED_DATASET_ROLES})
    truth = truth_from_dict(json.loads(dataset.truth_json)) if dataset.truth_json else None
    return corpus, truth


async def run_reconciliation(ctx: dict[str, Any], run_id: str, user_id: str) -> None:
    """The arq job body: queued -> normalising -> matching -> triaging ->
    explaining -> scoring -> complete|failed. State is persisted to Postgres
    on every transition and published to Redis pub/sub, so any API replica
    (reading from the same Postgres row plus the same pub/sub channel) can
    serve this run's stream regardless of which replica the client connects
    to, and a fresh connection after the fact still sees the terminal state
    from the row even though it missed the pub/sub message.
    """
    session_factory = ctx["db_session_factory"]
    redis_client = ctx["redis_client"]
    gateway_factory = ctx["gateway_factory"]

    async def publish_state(state: RunState) -> None:
        async with session_factory() as db:
            await transition_run_state(db, run_id, cast(DbRunState, state))
        await redis_client.publish(f"run:{run_id}", json.dumps({"state": state}))

    async with session_factory() as db:
        run = await get_run_for_user(db, run_id, user_id)
    if run is None:
        logger.warning("run_reconciliation: run %s not found for user %s", run_id, user_id)
        return

    corpus: Corpus
    truth: Truth | None
    if run.source == "demo":
        corpus, truth = generate_corpus(run.seed or DEFAULT_DEMO_SEED, run.size or DEFAULT_DEMO_SIZE)
    else:
        async with session_factory() as db:
            loaded = await _load_dataset_corpus(db, cast(str, run.dataset_id), user_id)
        if loaded is None:
            error = f"dataset {run.dataset_id!r} not found or not ready"
            async with session_factory() as db:
                await fail_run(db, run_id, error)
            await redis_client.publish(f"run:{run_id}", json.dumps({"state": "failed", "error": error}))
            return
        corpus, truth = loaded

    try:
        gateway = gateway_factory(user_id)
        outcome = await run_pipeline(corpus, truth, gateway, user_id, publish_state)
    except Exception as exc:  # an engine/pipeline error must fail the run, never present partial results
        logger.exception("run_reconciliation: run %s failed", run_id)
        async with session_factory() as db:
            await fail_run(db, run_id, str(exc))
        await redis_client.publish(f"run:{run_id}", json.dumps({"state": "failed", "error": str(exc)}))
        return

    result_json = serialize_match_result(outcome.result)
    metrics_json = outcome.metrics.model_dump_json()
    forecast_json = build_forecast(corpus, outcome.result).model_dump_json()
    async with session_factory() as db:
        await complete_run(db, run_id, result_json, metrics_json, forecast_json)
    await redis_client.publish(f"run:{run_id}", json.dumps({"state": "complete"}))
