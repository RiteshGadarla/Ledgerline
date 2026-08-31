import json
import logging
from typing import Any, cast

from contracts.models import Exception_, MatchGroup
from datagen.generator import generate_corpus
from db.tenancy import RunState as DbRunState
from db.tenancy import complete_run, fail_run, get_run_for_user, transition_run_state
from engine.pipeline import MatchResult
from workers.forecast import build_forecast
from workers.pipeline import RunState, run_pipeline

logger = logging.getLogger("ledgerline.worker")

DEFAULT_DEMO_SEED = 1001
DEFAULT_DEMO_SIZE = 150


def serialize_match_result(result: MatchResult) -> str:
    return json.dumps(
        {
            "groups": [g.model_dump(mode="json") for g in result.groups],
            "exceptions": [e.model_dump(mode="json") for e in result.exceptions],
            "output_hash": result.output_hash,
        }
    )


def deserialize_match_result(raw: str) -> MatchResult:
    payload: dict[str, Any] = json.loads(raw)
    return MatchResult(
        groups=[MatchGroup.model_validate(g) for g in payload["groups"]],
        exceptions=[Exception_.model_validate(e) for e in payload["exceptions"]],
        output_hash=payload["output_hash"],
    )


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

    if run.source != "demo":
        # Dataset-backed runs need persisted dataset storage, which doesn't
        # exist yet (Phase 8's ingest/ only parses in-memory) -- documented
        # gap rather than a silent no-op.
        error = f"source {run.source!r} is not yet supported"
        async with session_factory() as db:
            await fail_run(db, run_id, error)
        await redis_client.publish(f"run:{run_id}", json.dumps({"state": "failed", "error": error}))
        return

    try:
        corpus, truth = generate_corpus(run.seed or DEFAULT_DEMO_SEED, run.size or DEFAULT_DEMO_SIZE)
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
