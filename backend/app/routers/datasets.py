import json
import random
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.corpus import build_generated_dataset
from app.db import get_db
from app.deps import get_current_user
from app.errors import NotFoundError, ValidationFailedError
from app.ingest_upload import parse_upload, require_role
from db.tenancy import (
    REQUIRED_DATASET_ROLES,
    DatasetRecord,
    UserRecord,
    count_runs_for_dataset,
    create_dataset,
    dataset_name_taken,
    delete_dataset_file,
    delete_dataset_for_user,
    get_dataset_file_raw,
    get_dataset_for_user,
    list_dataset_files,
    list_datasets_for_user,
    recompute_dataset_status,
    upsert_dataset_file,
)
from ingest.dataset_records import records_to_json
from ingest.mapper import MappingResponse
from ingest.validate import build_records

router = APIRouter(prefix="/datasets", tags=["datasets"])

# Sized so each of the ten difficulty classes carries enough records to be
# scored on: at 150 several classes held a single record, and a recall computed
# from one record is 0% or 100% and nothing in between.
DEFAULT_GENERATED_SIZE = 400
# The ceiling is the README's documented 5,000; the floor only has to stop a
# size that isn't a size at all. A small corpus is a legitimate thing to ask
# for (the test suite runs on 30) -- a negative one is not.
MIN_GENERATED_SIZE = 1
MAX_GENERATED_SIZE = 5_000


class DatasetFileOut(BaseModel):
    role: str
    raw_filename: str | None
    has_raw: bool
    row_count: int
    valid_count: int


class DatasetOut(BaseModel):
    id: str
    name: str
    source: str
    seed: int | None
    size: int | None
    status: str
    created_at: datetime
    files: list[DatasetFileOut]
    # So a delete can say what it will cost before it is confirmed.
    run_count: int = 0


class DatasetCreate(BaseModel):
    name: str
    source: Literal["generated", "uploaded"]
    seed: int | None = None
    # Bounded because the corpus is generated and persisted inside this
    # request: a negative size silently produced a nonsense corpus rather
    # than an error, and an unbounded one would hold the event loop for as
    # long as it took to build (~3s and ~106k rows at size 50,000).
    size: int | None = Field(default=None, ge=MIN_GENERATED_SIZE, le=MAX_GENERATED_SIZE)


class RowErrorOut(BaseModel):
    row_number: int
    reason: str


class DatasetFileUploadOut(BaseModel):
    dataset: DatasetOut
    total_rows: int
    valid_count: int
    errors: list[RowErrorOut]
    # How many rows failed in total (errors above is capped), and what the
    # parser had to repair to read the file at all. Both are shown to the
    # uploader: a repair nobody is told about is indistinguishable from a bug.
    error_count: int = 0
    notes: list[str] = []


class DatasetRecordsOut(BaseModel):
    role: str
    total: int
    offset: int
    limit: int
    records: list[dict[str, object]]


async def _get_owned_dataset_or_404(db: AsyncSession, dataset_id: str, user_id: str) -> DatasetRecord:
    dataset = await get_dataset_for_user(db, dataset_id, user_id)
    if dataset is None:
        raise NotFoundError(f"dataset {dataset_id!r} not found")
    return dataset


async def _dataset_out(db: AsyncSession, dataset: DatasetRecord) -> DatasetOut:
    files = await list_dataset_files(db, dataset.id)
    run_count = await count_runs_for_dataset(db, dataset.id, dataset.user_id)
    by_role = {f.role: f for f in files}
    return DatasetOut(
        id=dataset.id,
        name=dataset.name,
        source=dataset.source,
        seed=dataset.seed,
        size=dataset.size,
        status=dataset.status,
        created_at=dataset.created_at,
        files=[
            DatasetFileOut(
                role=role,
                raw_filename=(by_role[role].raw_filename if role in by_role else None),
                has_raw=(role in by_role and by_role[role].raw_content_type is not None),
                row_count=(by_role[role].row_count if role in by_role else 0),
                valid_count=(by_role[role].valid_count if role in by_role else 0),
            )
            for role in REQUIRED_DATASET_ROLES
        ],
        run_count=run_count,
    )


@router.post("", response_model=DatasetOut, status_code=201)
async def create_dataset_endpoint(
    payload: DatasetCreate, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> DatasetOut:
    """A "generated" dataset is built and persisted synchronously (it's a
    pure, fast, in-memory computation) and comes back already "ready". An
    "uploaded" dataset starts empty -- the caller adds each role's file via
    POST /datasets/{id}/files, and it becomes "ready" once all four are in."""
    name = payload.name.strip()
    if not name:
        raise ValidationFailedError("A dataset needs a name.")
    if await dataset_name_taken(db, user.id, name):
        raise ValidationFailedError(f"You already have a dataset named {name!r}. Pick a different name.")
    if payload.source == "generated":
        seed = payload.seed if payload.seed is not None else random.randint(1, 1_000_000)
        size = payload.size or DEFAULT_GENERATED_SIZE
        dataset = await build_generated_dataset(db, user.id, name, seed, size)
    else:
        dataset = await create_dataset(db, user.id, name, "uploaded")

    refreshed = await get_dataset_for_user(db, dataset.id, user.id)
    assert refreshed is not None
    return await _dataset_out(db, refreshed)


@router.get("", response_model=list[DatasetOut])
async def list_datasets_endpoint(
    user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[DatasetOut]:
    """Lists the caller's datasets, newest first."""
    datasets = await list_datasets_for_user(db, user.id)
    return [await _dataset_out(db, dataset) for dataset in datasets]


@router.get("/{dataset_id}", response_model=DatasetOut)
async def get_dataset_endpoint(
    dataset_id: str, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> DatasetOut:
    """Fetches one dataset, including per-role file status."""
    dataset = await _get_owned_dataset_or_404(db, dataset_id, user.id)
    return await _dataset_out(db, dataset)


@router.post("/{dataset_id}/files", response_model=DatasetFileUploadOut)
async def upload_dataset_file_endpoint(
    dataset_id: str,
    role: str = Form(..., description="One of 'ledger', 'gateway', 'settlement', 'bank'."),
    mapping: str = Form(..., description="JSON-encoded list of {source_header, canonical_field, confidence}"),
    file: UploadFile = File(...),
    user: UserRecord = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DatasetFileUploadOut:
    """Applies a confirmed mapping (from /data/preview) and persists both the
    original file and the validated canonical records for this role, then
    recomputes whether the dataset is ready to run."""
    dataset = await _get_owned_dataset_or_404(db, dataset_id, user.id)
    validated_role = require_role(role)
    table, raw_content, raw_content_type = await parse_upload(file)

    try:
        mapping_payload = json.loads(mapping)
    except json.JSONDecodeError as exc:
        raise ValidationFailedError(f"invalid mapping payload: {exc}") from exc
    mapping_response = MappingResponse.model_validate({"fields": mapping_payload})

    report = build_records(validated_role, table, mapping_response)
    await upsert_dataset_file(
        db,
        dataset.id,
        validated_role,
        raw_filename=file.filename,
        raw_content_type=raw_content_type or "application/octet-stream",
        raw_content=raw_content,
        records_json=records_to_json(validated_role, report.valid_records),
        row_count=len(table.rows),
        valid_count=len(report.valid_records),
    )
    await recompute_dataset_status(db, dataset.id)

    refreshed = await get_dataset_for_user(db, dataset.id, user.id)
    assert refreshed is not None
    return DatasetFileUploadOut(
        dataset=await _dataset_out(db, refreshed),
        total_rows=len(table.rows),
        valid_count=len(report.valid_records),
        errors=[RowErrorOut(row_number=e.row_number, reason=e.reason) for e in report.errors],
        error_count=report.error_count,
        notes=report.notes,
    )


@router.get("/{dataset_id}/files/{role}/records", response_model=DatasetRecordsOut)
async def get_dataset_file_records_endpoint(
    dataset_id: str,
    role: str,
    offset: int = 0,
    limit: int = 50,
    user: UserRecord = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DatasetRecordsOut:
    """Pages through a dataset file's canonical (post-mapping) records."""
    await _get_owned_dataset_or_404(db, dataset_id, user.id)
    validated_role = require_role(role)
    files = await list_dataset_files(db, dataset_id)
    file = next((f for f in files if f.role == validated_role), None)
    if file is None:
        raise NotFoundError(f"dataset {dataset_id!r} has no {validated_role!r} file yet")

    all_records = json.loads(file.records_json)
    capped_limit = max(1, min(limit, 200))
    return DatasetRecordsOut(
        role=validated_role,
        total=len(all_records),
        offset=offset,
        limit=capped_limit,
        records=all_records[offset : offset + capped_limit],
    )


@router.get(
    "/{dataset_id}/files/{role}/raw",
    response_class=StreamingResponse,
    response_description="The original uploaded file, byte-for-byte, as an attachment download.",
)
async def get_dataset_file_raw_endpoint(
    dataset_id: str, role: str, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
    """Downloads the original file as uploaded for this role -- not the canonical records derived from it."""
    await _get_owned_dataset_or_404(db, dataset_id, user.id)
    validated_role = require_role(role)
    raw = await get_dataset_file_raw(db, dataset_id, validated_role)
    if raw is None or raw.raw_content is None:
        raise NotFoundError(f"dataset {dataset_id!r} has no raw file for role {validated_role!r}")

    filename = raw.raw_filename or f"{validated_role}.csv"
    return StreamingResponse(
        iter([raw.raw_content]),
        media_type=raw.raw_content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class DatasetDeletedOut(BaseModel):
    """What a delete removed, stated rather than implied: a caller that asked
    to remove one dataset is entitled to know how many runs went with it."""

    dataset_id: str
    runs_deleted: int


@router.delete("/{dataset_id}", response_model=DatasetDeletedOut)
async def delete_dataset_endpoint(
    dataset_id: str, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> DatasetDeletedOut:
    """Deletes a dataset, every file in it, and every run made from it.

    The runs go too, deliberately. A run's scoreboard cites records by id and
    its exceptions quote evidence out of them; once the dataset is gone none
    of that can be re-derived, and a reconciliation you cannot re-derive is
    not evidence of anything. Better to remove it than to leave a figure on
    screen that nothing backs any more.

    A run still in flight goes too, which cancels it: the worker looks its run
    up before doing any work and stops when it finds nothing. Refusing instead
    would let a worker that died holding a queued run block its dataset from
    ever being deleted.
    """
    dataset = await _get_owned_dataset_or_404(db, dataset_id, user.id)
    runs_deleted = await delete_dataset_for_user(db, dataset.id, user.id)
    return DatasetDeletedOut(dataset_id=dataset.id, runs_deleted=runs_deleted)


@router.delete("/{dataset_id}/files/{role}", response_model=DatasetOut)
async def delete_dataset_file_endpoint(
    dataset_id: str, role: str, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> DatasetOut:
    """Removes one role's file. The dataset survives one role lighter, and
    drops out of "ready" because a run needs all four.

    Existing runs are left alone: they were scored against the records as they
    stood, and rewriting history to match a later edit would make every
    scoreboard provisional.
    """
    dataset = await _get_owned_dataset_or_404(db, dataset_id, user.id)
    validated_role = require_role(role)
    if not await delete_dataset_file(db, dataset.id, validated_role):
        raise NotFoundError(f"dataset {dataset_id!r} has no {validated_role!r} file to delete")
    await recompute_dataset_status(db, dataset.id)
    refreshed = await get_dataset_for_user(db, dataset.id, user.id)
    assert refreshed is not None
    return await _dataset_out(db, refreshed)
