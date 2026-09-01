import json

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel

from app.deps import get_current_user
from app.errors import ValidationFailedError
from app.ingest_upload import parse_upload, require_role
from app.redis_client import get_redis
from app.settings import get_settings
from db.tenancy import UserRecord
from ingest.mapper import CANONICAL_FIELDS, MappingCache, MappingResponse, map_schema
from ingest.validate import build_records
from llm.factory import build_gateway
from llm.gateway import LlmGateway
from money.result import Err

router = APIRouter(prefix="/data", tags=["data"])


class FieldMappingOut(BaseModel):
    source_header: str
    canonical_field: str | None
    confidence: float


class PreviewOut(BaseModel):
    headers: list[str]
    sample_rows: list[dict[str, str]]
    mapping: list[FieldMappingOut]
    canonical_fields: list[str]


class RowErrorOut(BaseModel):
    row_number: int
    reason: str


class ValidateOut(BaseModel):
    total_rows: int
    valid_count: int
    errors: list[RowErrorOut]
    # How many rows failed in total (errors above is capped), and what the
    # parser had to repair to read the file at all. Both are shown to the
    # uploader: a repair nobody is told about is indistinguishable from a bug.
    error_count: int = 0
    notes: list[str] = []


def get_mapper_gateway(request: Request) -> LlmGateway:
    """A dependency (rather than a plain function call) so tests can swap in
    a FakeClient-backed gateway via app.dependency_overrides instead of ever
    reaching the real Gemini API."""
    return build_gateway(get_redis(request), schema_version="mapper-v1", api_key=get_settings().gemini_api_key)


@router.post("/preview", response_model=PreviewOut)
async def preview_endpoint(
    request: Request,
    role: str = Form(...),
    file: UploadFile = File(...),
    user: UserRecord = Depends(get_current_user),
    gateway: LlmGateway = Depends(get_mapper_gateway),
) -> PreviewOut:
    """Parses the upload and asks the LLM to propose a header-to-canonical-
    field mapping. Nothing is persisted -- the caller confirms or overrides
    the mapping and re-submits the same file to /data/validate (or, to save
    it as a reusable dataset, to /datasets/{id}/files)."""
    validated_role = require_role(role)
    table, _content, _content_type = await parse_upload(file)

    redis_client = get_redis(request)
    cache = MappingCache(redis_client)
    result = await map_schema(validated_role, table, gateway, cache, user_id=user.id)
    if isinstance(result, Err):
        raise ValidationFailedError(f"could not map columns: {result.reason}")

    return PreviewOut(
        headers=table.headers,
        sample_rows=table.rows[:5],
        mapping=[
            FieldMappingOut(
                source_header=field.source_header, canonical_field=field.canonical_field, confidence=field.confidence
            )
            for field in result.value.fields
        ],
        canonical_fields=CANONICAL_FIELDS[validated_role],
    )


@router.post("/validate", response_model=ValidateOut)
async def validate_endpoint(
    role: str = Form(...),
    mapping: str = Form(..., description="JSON-encoded list of {source_header, canonical_field, confidence}"),
    file: UploadFile = File(...),
    user: UserRecord = Depends(get_current_user),
) -> ValidateOut:
    """Applies a confirmed (possibly user-overridden) mapping and reports a
    file health report: how many rows are usable and why the rest aren't."""
    validated_role = require_role(role)
    table, _content, _content_type = await parse_upload(file)

    try:
        mapping_payload = json.loads(mapping)
    except json.JSONDecodeError as exc:
        raise ValidationFailedError(f"invalid mapping payload: {exc}") from exc
    mapping_response = MappingResponse.model_validate({"fields": mapping_payload})

    report = build_records(validated_role, table, mapping_response)
    return ValidateOut(
        total_rows=len(table.rows),
        valid_count=len(report.valid_records),
        errors=[RowErrorOut(row_number=e.row_number, reason=e.reason) for e in report.errors],
        error_count=report.error_count,
        notes=report.notes,
    )
