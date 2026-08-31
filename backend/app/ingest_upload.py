from fastapi import UploadFile

from app.errors import ValidationFailedError
from ingest.mapper import CANONICAL_FIELDS, SourceRole
from ingest.pdf import extract_pdf_table
from ingest.tabular import ParsedTable, parse_table, sniff_content_type
from money.result import Err


def require_role(role: str) -> SourceRole:
    if role not in CANONICAL_FIELDS:
        raise ValidationFailedError(f"unknown role {role!r}; expected one of {sorted(CANONICAL_FIELDS)}")
    return role


async def parse_upload(file: UploadFile) -> tuple[ParsedTable, bytes, str | None]:
    """Returns the parsed table alongside the raw bytes and content-type, so
    a caller that wants to persist the original upload (datasets router)
    doesn't have to re-read the file."""
    content = await file.read()
    kind = sniff_content_type(content)
    filename = file.filename or "upload"
    result = extract_pdf_table(content, filename) if kind == "pdf" else parse_table(content, filename)
    if isinstance(result, Err):
        raise ValidationFailedError(result.reason)
    return result.value, content, file.content_type
