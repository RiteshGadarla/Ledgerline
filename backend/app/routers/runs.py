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
from contracts.models import RunMetrics
from db.tenancy import RunRecord, UserRecord, create_run, get_run_for_user
from workers.tasks import deserialize_match_result

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
    state: str
    error: str | None
    metrics: RunMetrics | None
    created_at: datetime
    updated_at: datetime


def _run_out(run: RunRecord) -> RunOut:
    metrics = RunMetrics.model_validate_json(run.metrics_json) if run.metrics_json else None
    return RunOut(
        id=run.id,
        source=run.source,
        state=run.state,
        error=run.error,
        metrics=metrics,
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
    if payload.mutations:
        # The adversarial mutation engine (Phase 13) doesn't exist yet.
        raise ValidationFailedError("mutations are not yet supported")

    run, created = await create_run(
        db,
        user.id,
        payload.source,
        seed=payload.seed,
        dataset_id=payload.dataset_id,
        size=payload.size,
        mutations=payload.mutations,
        idempotency_key=payload.idempotency_key,
    )
    if created:
        # run_id doubles as the arq job id: even if the idempotency-key check
        # above raced, arq itself refuses to enqueue a second job under an
        # id already queued or running.
        await arq_pool.enqueue_job("run_reconciliation", run.id, user.id, _job_id=run.id)
    return _run_out(run)


@router.get("/{run_id}", response_model=RunOut)
async def get_run_endpoint(
    run_id: str, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> RunOut:
    run = await _get_owned_run_or_404(db, run_id, user.id)
    return _run_out(run)


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
