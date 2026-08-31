import io
import re

import pdfplumber

from ingest.tabular import ParsedTable
from money.result import Err, Ok, Result

_LINE_REGEX = re.compile(
    r"^(?P<date>\d{1,2}[/-][A-Za-z0-9]{2,4}[/-]\d{2,4})\s+"
    r"(?P<narration>.+?)\s+"
    r"(?P<amount>[\d,]+\.\d{2})\s*(?P<sign>CR|DR)?\s*$"
)

_TABLE_SETTINGS = {"vertical_strategy": "text", "horizontal_strategy": "text"}


def extract_pdf_table(content: bytes, filename: str) -> Result[ParsedTable]:
    """Extract a table from a bank statement PDF.

    Primary path leans on pdfplumber's own gap-based column detection (real
    statements rarely have ruling lines pdfplumber's line-strategy needs), on
    the assumption the first extracted row is a header. Falls back to a
    per-line date/narration/amount regex for layouts pdfplumber can't table-ize
    at all. Only validated here against synthetic fixtures generated in this
    repo (fpdf2); no real bank statement samples were available to test
    against, so a genuinely unusual real-world layout may still need the
    Flash-Lite layout-hint path the spec describes, which is not implemented.
    """
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            tables: list[list[list[str | None]]] = []
            texts: list[str] = []
            for page in pdf.pages:
                table = page.extract_table(_TABLE_SETTINGS)
                if table:
                    tables.append(table)
                texts.append(page.extract_text() or "")
    except Exception as exc:
        return Err(f"{filename}: could not open PDF: {exc}")

    from_tables = _tables_to_parsed(tables)
    if from_tables is not None:
        return Ok(from_tables)

    from_lines = _lines_to_parsed(texts)
    if from_lines is not None:
        return Ok(from_lines)

    return Err(f"{filename}: could not extract a table from this PDF layout")


def _tables_to_parsed(tables: list[list[list[str | None]]]) -> ParsedTable | None:
    """A single-column "table" is really just paragraph text that the
    gap-based clustering happened to notice one boundary in -- it carries no
    real column structure, so it is rejected in favour of the line-regex
    fallback rather than corrupting downstream field mapping."""
    headers: list[str] | None = None
    all_rows: list[list[str | None]] = []
    for table in tables:
        if not table or len(table[0]) < 2:
            continue
        if headers is None:
            headers = [(cell.strip() if cell else f"col_{i}") for i, cell in enumerate(table[0])]
            all_rows.extend(table[1:])
        else:
            all_rows.extend(table)

    if headers is None or not all_rows:
        return None

    rows = [
        {headers[i]: (cell.strip() if cell else "") for i, cell in enumerate(row) if i < len(headers)}
        for row in all_rows
    ]
    # Gap-based row detection sometimes splits a single line's worth of
    # vertical whitespace into an extra, entirely blank band -- not a record.
    rows = [row for row in rows if any(value for value in row.values())]
    if not rows:
        return None
    return ParsedTable(headers=headers, rows=rows)


def _lines_to_parsed(texts: list[str]) -> ParsedTable | None:
    headers = ["date", "narration", "amount", "sign"]
    rows: list[dict[str, str]] = []
    for text in texts:
        for line in text.splitlines():
            match = _LINE_REGEX.match(line.strip())
            if match:
                rows.append(
                    {
                        "date": match.group("date"),
                        "narration": match.group("narration").strip(),
                        "amount": match.group("amount"),
                        "sign": match.group("sign") or "",
                    }
                )
    if not rows:
        return None
    return ParsedTable(headers=headers, rows=rows)
