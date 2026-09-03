import csv
import io
import json
from collections.abc import AsyncIterator, Awaitable
from datetime import datetime
from typing import Literal, cast

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.arq_pool import get_arq_pool
from app.db import get_db
from app.deps import get_current_user
from app.errors import NotFoundError, ValidationFailedError
from app.redis_client import get_redis
from app.routers.datasets import MAX_GENERATED_SIZE, MIN_GENERATED_SIZE
from contracts.models import CashForecast, Exception_, MatchGroup, RunMetrics
from datagen.mutations import format_mutation, parse_mutation
from db.tenancy import (
    Decision,
    ExceptionDecisionRecord,
    RunRecord,
    UserRecord,
    create_run,
    get_dataset_for_user,
    get_run_for_user,
    list_exception_decisions,
    list_runs_for_user,
    record_exception_decision,
)
from engine.pipeline import deserialize_match_result
from money.result import Err

router = APIRouter(prefix="/runs", tags=["runs"])

TERMINAL_STATES = {"complete", "failed"}


class RunCreate(BaseModel):
    source: Literal["demo", "dataset"]
    seed: int | None = None
    dataset_id: str | None = None
    # Same bounds a generated dataset is held to: a demo run generates its
    # corpus the same way, so an unbounded or negative size would land the
    # same nonsense here.
    size: int | None = Field(default=None, ge=MIN_GENERATED_SIZE, le=MAX_GENERATED_SIZE)
    mutations: list[str] | None = Field(
        default=None,
        description=(
            "Adversarial corruptions to apply to the corpus before matching, "
            "e.g. 'shift_date:60' or 'alter_amount:-250000'."
        ),
    )
    idempotency_key: str | None = Field(
        default=None, description="Replaying the same key returns the existing run instead of starting a new one."
    )


class RunOut(BaseModel):
    id: str
    source: str
    dataset_id: str | None
    # What this run was sabotaged with, if anything -- so a result is never
    # read without the corruption that produced it.
    mutations: list[str] | None
    state: str
    error: str | None
    metrics: RunMetrics | None
    forecast: CashForecast | None
    created_at: datetime
    updated_at: datetime


class RunResultOut(BaseModel):
    groups: list[MatchGroup]
    exceptions: list[Exception_]
    output_hash: str


def _run_out(run: RunRecord) -> RunOut:
    metrics = RunMetrics.model_validate_json(run.metrics_json) if run.metrics_json else None
    forecast = CashForecast.model_validate_json(run.forecast_json) if run.forecast_json else None
    return RunOut(
        id=run.id,
        source=run.source,
        dataset_id=run.dataset_id,
        mutations=run.mutations,
        state=run.state,
        error=run.error,
        metrics=metrics,
        forecast=forecast,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


async def _get_owned_run_or_404(db: AsyncSession, run_id: str, user_id: str) -> RunRecord:
    run = await get_run_for_user(db, run_id, user_id)
    if run is None:
        raise NotFoundError(f"run {run_id!r} not found")
    return run


@router.post("", response_model=RunOut, status_code=202)
async def create_run_endpoint(
    payload: RunCreate,
    user: UserRecord = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    arq_pool: ArqRedis = Depends(get_arq_pool),
) -> RunOut:
    """Enqueues a reconciliation run and returns immediately with it in
    'queued' state. Poll GET /runs/{id} or GET /runs/{id}/stream for progress.
    Reusing an idempotency_key returns the original run instead of enqueuing a
    second one."""
    # Normalised here rather than in the worker: a mutation the engine cannot
    # apply has to be a 422 on the request that asked for it, not a run that
    # fails ten seconds later with nothing to show for it.
    mutations: list[str] | None = None
    if payload.mutations:
        specs = []
        for raw in payload.mutations:
            parsed = parse_mutation(raw)
            if isinstance(parsed, Err):
                raise ValidationFailedError(parsed.reason)
            specs.append(parsed.value)
        mutations = [format_mutation(spec) for spec in specs]

    if payload.source == "dataset":
        if not payload.dataset_id:
            raise ValidationFailedError("dataset_id is required when source is 'dataset'")
        dataset = await get_dataset_for_user(db, payload.dataset_id, user.id)
        if dataset is None:
            raise NotFoundError(f"dataset {payload.dataset_id!r} not found")
        if dataset.status != "ready":
            raise ValidationFailedError(f"dataset {payload.dataset_id!r} is missing required files")

    run, created = await create_run(
        db,
        user.id,
        payload.source,
        seed=payload.seed,
        dataset_id=payload.dataset_id,
        size=payload.size,
        mutations=mutations,
        idempotency_key=payload.idempotency_key,
    )
    if created:
        # run_id doubles as the arq job id: even if the idempotency-key check
        # above raced, arq itself refuses to enqueue a second job under an
        # id already queued or running.
        await arq_pool.enqueue_job("run_reconciliation", run.id, user.id, _job_id=run.id)
    return _run_out(run)


@router.get("", response_model=list[RunOut])
async def list_runs_endpoint(
    user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[RunOut]:
    """Lists the caller's runs, newest first."""
    runs = await list_runs_for_user(db, user.id)
    return [_run_out(run) for run in runs]


@router.get("/{run_id}", response_model=RunOut)
async def get_run_endpoint(
    run_id: str, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> RunOut:
    """Fetches one run's current state, metrics and forecast (the last two are
    null until the run completes)."""
    run = await _get_owned_run_or_404(db, run_id, user.id)
    return _run_out(run)


@router.get("/{run_id}/result", response_model=RunResultOut)
async def get_run_result_endpoint(
    run_id: str, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> RunResultOut:
    """Fetches the full match result: matched groups and unmatched exceptions. 404 until the run completes."""
    run = await _get_owned_run_or_404(db, run_id, user.id)
    if run.result_json is None:
        raise NotFoundError(f"run {run_id!r} has no result yet")
    result = deserialize_match_result(run.result_json)
    return RunResultOut(groups=result.groups, exceptions=result.exceptions, output_hash=result.output_hash)


def _sse_event(payload: dict[str, object]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


@router.get(
    "/{run_id}/stream",
    response_class=StreamingResponse,
    response_description="text/event-stream of {state, error?} frames, replaying missed transitions on connect.",
)
async def stream_run_endpoint(
    run_id: str,
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Streams a run's state transitions as Server-Sent Events until it reaches a terminal state."""
    await _get_owned_run_or_404(db, run_id, user.id)
    redis_client = get_redis(request)

    async def event_source() -> AsyncIterator[bytes]:
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"run:{run_id}")
        try:
            # Everything below reads state only *after* subscribing, which
            # closes the race where the job moves on between an earlier read
            # and the subscribe call: anything published in that window is
            # buffered on the subscription and arrives in the listen loop,
            # and `seen` drops it if the replay already covered it. The
            # pipeline never re-enters a state, so a state name is a sound
            # identity to deduplicate on.
            seen: set[str] = set()

            # The trace is what a client that connected late, or reloaded
            # part-way through, has missed: every transition so far, stamped
            # with the moment the worker made it. Replaying it is what lets
            # the console time the early stages, which are over in
            # milliseconds and long gone before any browser is listening.
            # cast: redis-py types every command as sync-or-async on the
            # shared base class, so the async client's return needs narrowing.
            trace = await cast("Awaitable[list[bytes]]", redis_client.lrange(f"run:{run_id}:trace", 0, -1))
            for raw in trace:
                payload = json.loads(raw)
                state = payload.get("state")
                if state in seen:
                    continue
                seen.add(state)
                yield _sse_event(payload)
            if seen & TERMINAL_STATES:
                return

            # The row is both the fallback and the backstop: it covers a run
            # still sitting in the queue with nothing traced yet, and one
            # whose trace has expired, and it is the source that survives
            # Redis being flushed entirely.
            run = await get_run_for_user(db, run_id, user.id)
            if run is None:
                return
            if run.state not in seen:
                seen.add(run.state)
                yield _sse_event({"state": run.state, "error": run.error})
            if run.state in TERMINAL_STATES:
                return

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                payload = json.loads(message["data"])
                state = payload.get("state")
                if state in seen:
                    continue
                seen.add(state)
                yield _sse_event(payload)
                if state in TERMINAL_STATES:
                    break
        finally:
            await pubsub.unsubscribe(f"run:{run_id}")
            await pubsub.aclose()  # type: ignore[no-untyped-call]

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/{run_id}/export.csv",
    response_class=StreamingResponse,
    response_description="The run's exceptions as a CSV attachment.",
)
async def export_run_csv_endpoint(
    run_id: str, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
    """Exports a completed run's exceptions as a downloadable CSV. 404 until the run completes."""
    run = await _get_owned_run_or_404(db, run_id, user.id)
    if run.result_json is None:
        raise NotFoundError(f"run {run_id!r} has no result yet")

    result = deserialize_match_result(run.result_json)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["exception_id", "code", "severity", "amount_at_risk_paise", "record_ids", "suggested_action"])
    for exc in result.exceptions:
        writer.writerow(
            [
                exc.id,
                exc.code.value,
                exc.severity,
                int(exc.amount_at_risk),
                ";".join(f"{r.kind}:{r.id}" for r in exc.records),
                exc.suggested_action or "",
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="run-{run_id}-exceptions.csv"'},
    )


class DecisionIn(BaseModel):
    decision: Decision
    note: str | None = None


class DecisionOut(BaseModel):
    exception_id: str
    decision: Decision
    note: str | None
    created_at: datetime


def _decision_out(record: ExceptionDecisionRecord) -> DecisionOut:
    return DecisionOut(
        exception_id=record.exception_id, decision=record.decision, note=record.note, created_at=record.created_at
    )


@router.post("/{run_id}/exceptions/{exception_id}/decision", response_model=DecisionOut)
async def record_exception_decision_endpoint(
    run_id: str,
    exception_id: str,
    payload: DecisionIn,
    user: UserRecord = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DecisionOut:
    """Human decisions are recorded separately from the machine's own,
    immutable output -- approving or rejecting an exception never rewrites
    the run's result or metrics."""
    record = await record_exception_decision(db, run_id, user.id, exception_id, payload.decision, payload.note)
    if record is None:
        raise NotFoundError(f"run {run_id!r} not found")
    return _decision_out(record)


@router.get("/{run_id}/decisions", response_model=list[DecisionOut])
async def list_exception_decisions_endpoint(
    run_id: str, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[DecisionOut]:
    """Lists every human decision recorded against this run's exceptions."""
    records = await list_exception_decisions(db, run_id, user.id)
    if records is None:
        raise NotFoundError(f"run {run_id!r} not found")
    return [_decision_out(record) for record in records]
