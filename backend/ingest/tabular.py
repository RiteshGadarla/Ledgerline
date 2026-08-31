import io
from dataclasses import dataclass

import polars as pl

from money.result import Err, Ok, Result

MAX_ROWS = 200_000


@dataclass(frozen=True)
class ParsedTable:
    headers: list[str]
    rows: list[dict[str, str]]  # kept as raw strings; typed parsing happens in ingest/validate.py


def sniff_content_type(content: bytes) -> str:
    """MIME sniffing by content, not the filename extension."""
    if content[:4] == b"PK\x03\x04":
        return "xlsx"
    if content[:8] == b"\x89PNG\r\n\x1a\n" or content[:3] == b"\xff\xd8\xff" or content[:6] in (b"GIF87a", b"GIF89a"):
        return "image"
    if content[:5] == b"%PDF-":
        return "pdf"
    return "csv"


def parse_table(content: bytes, filename: str) -> Result[ParsedTable]:
    """Never raises. Content type is sniffed from magic bytes, so a renamed
    image is rejected regardless of its .csv extension."""
    if not content:
        return Err(f"{filename}: file is empty")

    kind = sniff_content_type(content)
    if kind == "image":
        return Err(f"{filename}: file content is an image, not a table")
    if kind == "pdf":
        return Err(f"{filename}: this is a PDF; use the PDF ingestion path")

    try:
        if kind == "xlsx":
            frame = pl.read_excel(io.BytesIO(content), engine="calamine", infer_schema_length=0)
        else:
            text = content.decode("utf-8-sig")
            frame = pl.read_csv(io.BytesIO(text.encode()), infer_schema_length=0)
    except Exception as exc:
        return Err(f"{filename}: could not parse as {kind}: {exc}")

    if frame.width == 0:
        return Err(f"{filename}: no columns found")

    headers = [str(column) for column in frame.columns]
    rows = [
        {header: ("" if value is None else str(value)) for header, value in zip(headers, row, strict=True)}
        for row in frame.iter_rows()
    ]
    rows = _exclude_totals_row(rows)

    if len(rows) > MAX_ROWS:
        return Err(f"{filename}: {len(rows)} rows exceeds the {MAX_ROWS}-row cap")

    return Ok(ParsedTable(headers=headers, rows=rows))


def _exclude_totals_row(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """A trailing summary line -- mostly blank cells, one saying 'total' -- is
    not a record."""
    if not rows:
        return rows
    text_cells = [value.strip() for value in rows[-1].values() if value.strip()]
    if len(text_cells) <= 2 and any("total" in cell.lower() for cell in text_cells):
        return rows[:-1]
    return rows
