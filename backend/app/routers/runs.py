import csv
import io
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Literal

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.arq_pool import get_arq_pool
from app.db import get_db
from app.deps import get_current_user
from app.errors import NotFoundError, ValidationFailedError
from app.redis_client import get_redis
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
    size: int | None = None
    mutations: list[str] | None = None
    idempotency_key: str | None = None


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
    runs = await list_runs_for_user(db, user.id)
    return [_run_out(run) for run in runs]


@router.get("/{run_id}", response_model=RunOut)
async def get_run_endpoint(
    run_id: str, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> RunOut:
    run = await _get_owned_run_or_404(db, run_id, user.id)
    return _run_out(run)


@router.get("/{run_id}/result", response_model=RunResultOut)
async def get_run_result_endpoint(
    run_id: str, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> RunResultOut:
    run = await _get_owned_run_or_404(db, run_id, user.id)
    if run.result_json is None:
        raise NotFoundError(f"run {run_id!r} has no result yet")
    result = deserialize_match_result(run.result_json)
    return RunResultOut(groups=result.groups, exceptions=result.exceptions, output_hash=result.output_hash)


def _sse_event(payload: dict[str, object]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


@router.get("/{run_id}/stream")
async def stream_run_endpoint(
    run_id: str,
    request: Request,
    user: UserRecord = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    await _get_owned_run_or_404(db, run_id, user.id)
    redis_client = get_redis(request)

    async def event_source() -> AsyncIterator[bytes]:
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"run:{run_id}")
        try:
            # Re-read after subscribing (not before): this closes the race
            # where the job finishes between an earlier read and the
            # subscribe call. A client that disconnects and reconnects gets
            # the same treatment -- it just sees the row's current state,
            # never anything the job hasn't actually reached yet.
            run = await get_run_for_user(db, run_id, user.id)
            if run is None:
                return
            yield _sse_event({"state": run.state, "error": run.error})
            if run.state in TERMINAL_STATES:
                return

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                payload = json.loads(message["data"])
                yield _sse_event(payload)
                if payload.get("state") in TERMINAL_STATES:
                    break
        finally:
            await pubsub.unsubscribe(f"run:{run_id}")
            await pubsub.aclose()  # type: ignore[no-untyped-call]

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}/export.csv")
async def export_run_csv_endpoint(
    run_id: str, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
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
    records = await list_exception_decisions(db, run_id, user.id)
    if records is None:
        raise NotFoundError(f"run {run_id!r} not found")
    return [_decision_out(record) for record in records]
