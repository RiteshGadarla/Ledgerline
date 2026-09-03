import json
import logging
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from contracts.corpus import Corpus
from datagen.generator import generate_corpus
from datagen.models import Truth
from datagen.mutations import apply_mutations, parse_mutation
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
from money.result import Ok
from workers.forecast import build_forecast
from workers.pipeline import RunState, run_pipeline

logger = logging.getLogger("ledgerline.worker")

DEFAULT_DEMO_SEED = 1001
DEFAULT_DEMO_SIZE = 150

# Long enough that a run opened the next morning still shows how it spent its
# time, short enough that the traces of a busy week don't accumulate.
TRACE_TTL_SECONDS = 24 * 60 * 60


async def _emit(redis_client: Any, run_id: str, payload: dict[str, Any]) -> None:
    """Record a transition, then announce it.

    The list is what a client reads when it connects late or reloads
    mid-run: it recovers the transitions it missed, stamped with the moment
    the worker actually made them. That timestamp is the point -- it lets the
    console time each pipeline stage from the worker's own clock instead of
    from whenever a browser happened to be listening, and the early stages of
    a run are over long before any browser is.

    The publish is the same event for whoever is already listening.
    """
    event = {**payload, "at": datetime.now(UTC).isoformat().replace("+00:00", "Z")}
    encoded = json.dumps(event)
    key = f"run:{run_id}:trace"
    await redis_client.rpush(key, encoded)
    await redis_client.expire(key, TRACE_TTL_SECONDS)
    await redis_client.publish(f"run:{run_id}", encoded)


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
    on every transition, appended to a Redis trace list, and published to
    Redis pub/sub, so any API replica (reading from the same Postgres row,
    the same list and the same channel) can serve this run's stream
    regardless of which replica the client connects to. A connection made
    part-way through replays the transitions it missed off the list, and one
    made after the fact still sees the terminal state from the row even if
    the trace has since expired.
    """
    session_factory = ctx["db_session_factory"]
    redis_client = ctx["redis_client"]
    gateway_factory = ctx["gateway_factory"]

    async def publish_state(state: RunState) -> None:
        async with session_factory() as db:
            await transition_run_state(db, run_id, cast(DbRunState, state))
        await _emit(redis_client, run_id, {"state": state})

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
            await _emit(redis_client, run_id, {"state": "failed", "error": error})
            return
        corpus, truth = loaded

    # Mutations are applied after the corpus is assembled and before anything
    # reads it, so the engine sees only the corrupted books -- it is never told
    # that a corruption happened, which is the whole point of the exercise.
    # The run row carries the normalised spec list, so replaying this run row
    # reproduces this exact corruption rather than a fresh random one.
    if run.mutations:
        specs = []
        for raw in run.mutations:
            parsed = parse_mutation(raw)
            if isinstance(parsed, Ok):
                specs.append(parsed.value)
        corpus, truth = apply_mutations(corpus, truth, specs, seed=run.seed or 0)

    try:
        gateway = gateway_factory(user_id)
        outcome = await run_pipeline(corpus, truth, gateway, user_id, publish_state)
    except Exception as exc:  # an engine/pipeline error must fail the run, never present partial results
        logger.exception("run_reconciliation: run %s failed", run_id)
        async with session_factory() as db:
            await fail_run(db, run_id, str(exc))
        await _emit(redis_client, run_id, {"state": "failed", "error": str(exc)})
        return

    result_json = serialize_match_result(outcome.result)
    metrics_json = outcome.metrics.model_dump_json()
    forecast_json = build_forecast(corpus, outcome.result).model_dump_json()
    async with session_factory() as db:
        await complete_run(db, run_id, result_json, metrics_json, forecast_json)
    await _emit(redis_client, run_id, {"state": "complete"})
