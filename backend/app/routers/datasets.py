import json
import random
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.errors import NotFoundError, ValidationFailedError
from app.ingest_upload import parse_upload, require_role
from datagen.generator import generate_corpus
from datagen.serialize import truth_to_dict
from db.tenancy import (
    REQUIRED_DATASET_ROLES,
    DatasetRecord,
    DatasetRole,
    UserRecord,
    create_dataset,
    dataset_name_taken,
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

DEFAULT_GENERATED_SIZE = 150


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


class DatasetCreate(BaseModel):
    name: str
    source: Literal["generated", "uploaded"]
    seed: int | None = None
    size: int | None = None


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
        corpus, truth = generate_corpus(seed, size)
        dataset = await create_dataset(
            db, user.id, name, "generated", seed=seed, size=size, truth_json=json.dumps(truth_to_dict(truth))
        )
        role_records: dict[DatasetRole, list[Any]] = {
            "ledger": corpus.invoices,
            "gateway": corpus.payments,
            "settlement": corpus.settlements,
            "bank": corpus.bank_lines,
        }
        for role, records in role_records.items():
            await upsert_dataset_file(
                db,
                dataset.id,
                role,
                raw_filename=None,
                raw_content_type=None,
                raw_content=None,
                records_json=records_to_json(role, records),
                row_count=len(records),
                valid_count=len(records),
            )
        await recompute_dataset_status(db, dataset.id)
    else:
        dataset = await create_dataset(db, user.id, name, "uploaded")

    refreshed = await get_dataset_for_user(db, dataset.id, user.id)
    assert refreshed is not None
    return await _dataset_out(db, refreshed)


@router.get("", response_model=list[DatasetOut])
async def list_datasets_endpoint(
    user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[DatasetOut]:
    datasets = await list_datasets_for_user(db, user.id)
    return [await _dataset_out(db, dataset) for dataset in datasets]


@router.get("/{dataset_id}", response_model=DatasetOut)
async def get_dataset_endpoint(
    dataset_id: str, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> DatasetOut:
    dataset = await _get_owned_dataset_or_404(db, dataset_id, user.id)
    return await _dataset_out(db, dataset)


@router.post("/{dataset_id}/files", response_model=DatasetFileUploadOut)
async def upload_dataset_file_endpoint(
    dataset_id: str,
    role: str = Form(...),
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


@router.get("/{dataset_id}/files/{role}/raw")
async def get_dataset_file_raw_endpoint(
    dataset_id: str, role: str, user: UserRecord = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
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
