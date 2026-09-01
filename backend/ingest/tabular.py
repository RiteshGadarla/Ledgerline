"""Turning a file someone actually exported into a table.

The happy path -- a clean CSV whose first line is the header -- is the rare
case. Real uploads arrive with the bank's letterhead above the columns, a
delimiter that is not a comma, a legacy Windows encoding, blank spacer rows,
a merged banner cell, an unnamed index column, and a totals line at the
bottom that is not a record. None of that is corrupt data; it is what the
export button produces, and rejecting it pushes the cleanup onto the person
who least wants to do it.

So parsing here is a pipeline of narrow, explainable repairs, and every repair
it makes is recorded in `notes` and shown to the user. Silently dropping rows
is worse than failing: a reconciliation that quietly skipped forty lines still
balances, and still lies.
"""

import csv
import io
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from money.result import Err, Ok, Result

MAX_ROWS = 200_000
# How far into the file to look for the header. A statement preamble is a
# handful of lines; anything deeper is a file we should not be guessing about.
HEADER_SCAN_LIMIT = 40

_DELIMITERS = [",", ";", "\t", "|"]
_ENCODINGS = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
_NBSP = " "

_SUMMARY_MARKERS = (
    "total",
    "closing balance",
    "opening balance",
    "brought forward",
    "carried forward",
    "grand tot",
)

_DATEISH = ("/", "-")

# A delimiter has to split nearly every sampled line into the same number of
# fields. Anything less agreeing is a character that merely occurs in the data.
_MIN_DELIMITER_AGREEMENT = 0.9

# How many rows below a candidate header to look at, and how many of them must
# share a width before that candidate is believed.
_BODY_SAMPLE = 12
_MIN_BODY_AGREEMENT = 0.6


@dataclass(frozen=True)
class ParsedTable:
    headers: list[str]
    rows: list[dict[str, str]]  # kept as raw strings; typed parsing happens in ingest/validate.py
    # Every repair the parser made, in the words the uploader needs to hear.
    notes: list[str] = field(default_factory=list)


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

    notes: list[str] = []
    try:
        grid = _read_xlsx(content) if kind == "xlsx" else _read_csv(content, notes)
    except Exception as exc:
        return Err(f"{filename}: could not parse as {kind}: {exc}")

    if not grid:
        return Err(f"{filename}: no rows found")
    if len(grid) > MAX_ROWS + HEADER_SCAN_LIMIT:
        return Err(f"{filename}: {len(grid)} rows exceeds the {MAX_ROWS}-row cap")

    return _shape(grid, filename, notes)


# ------------------------------------------------------------------ readers


def _decode(content: bytes, notes: list[str]) -> str:
    """Try the encodings a finance export actually uses, in the order it
    actually uses them. latin-1 cannot fail, so this always returns."""
    for encoding in _ENCODINGS:
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding not in ("utf-8-sig", "utf-8"):
            notes.append(f"Read as {encoding} -- the file is not UTF-8.")
        return text
    return content.decode("latin-1", errors="replace")


def _sniff_delimiter(text: str, notes: list[str]) -> str:
    """Pick the delimiter that yields the most columns, consistently.

    csv.Sniffer guesses from one sample and gets semicolon files wrong often
    enough to matter, so this scores candidates over the first lines instead.
    Agreement decides it, not width: a settlement export whose `payment_ids`
    column is semicolon-joined splits into *more* fields on `;` than on `,`,
    but into a different number on every row -- and the right delimiter is the
    one that gives the same shape line after line.
    """
    sample = [line for line in text.splitlines()[:HEADER_SCAN_LIMIT] if line.strip()][:12]
    if not sample:
        return ","

    best, best_score = ",", (0.0, 0)
    for delimiter in _DELIMITERS:
        widths = [len(next(csv.reader([line], delimiter=delimiter), [])) for line in sample]
        widths = [w for w in widths if w > 0]
        if not widths:
            continue
        modal, count = Counter(widths).most_common(1)[0]
        if modal < 2:
            continue
        agreement = count / len(widths)
        if agreement < _MIN_DELIMITER_AGREEMENT:
            continue
        if (agreement, modal) > best_score:
            best, best_score = delimiter, (agreement, modal)

    if best != ",":
        notes.append(f"Detected {'tab' if best == chr(9) else best!r} as the column separator.")
    return best


def _read_csv(content: bytes, notes: list[str]) -> list[list[str]]:
    text = _decode(content, notes)
    delimiter = _sniff_delimiter(text, notes)
    # csv.reader rather than polars for this stage: a statement preamble makes
    # the file ragged, and a frame reader has to commit to a column count
    # before it has seen the header it is looking for.
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    return [[cell for cell in row] for row in reader]


def _read_xlsx(content: bytes) -> list[list[str]]:
    frame: Any = pl.read_excel(
        io.BytesIO(content), engine="calamine", infer_schema_length=0, has_header=False
    )
    return [["" if cell is None else str(cell) for cell in row] for row in frame.iter_rows()]


# ------------------------------------------------------------------- shaping


def _clean(cell: str) -> str:
    return " ".join(cell.replace(_NBSP, " ").split())


def _is_blank(row: list[str]) -> bool:
    return not any(cell.strip() for cell in row)


def _numberish(value: str) -> bool:
    """True for anything a data row holds and a header does not: an amount, a
    date, a bare reference number."""
    text = value.strip().strip("()").replace(",", "").replace("₹", "").replace("%", "")
    text = text.removesuffix("Cr").removesuffix("Dr").strip()
    if not text:
        return False
    try:
        float(text)
        return True
    except ValueError:
        pass
    digits = sum(1 for c in value if c.isdigit())
    return digits >= 4 and any(sep in value for sep in _DATEISH)


def _looks_like_header(row: list[str]) -> bool:
    filled = [cell for cell in row if cell.strip()]
    if len(filled) < 2:
        return False
    numeric = sum(1 for cell in filled if _numberish(cell))
    return numeric * 3 <= len(filled)  # a header is mostly words


def _find_header_index(grid: list[list[str]]) -> int | None:
    """The header is the first word-shaped row that the rows *beneath it* agree
    with.

    Looking downward is the whole trick. "Account Name,ACME TRADERS PVT LTD"
    and "Date,Narration,Withdrawal,Deposit,Balance" are both two-or-more words
    with no numbers in them; nothing about either row in isolation says which
    is the header. What separates them is that only one of them is followed by
    a run of rows the same shape as itself.
    """
    first_candidate: int | None = None

    for index, row in enumerate(grid[:HEADER_SCAN_LIMIT]):
        if not _looks_like_header(row):
            continue
        if first_candidate is None:
            first_candidate = index

        following = [r for r in grid[index + 1 :] if not _is_blank(r)][:_BODY_SAMPLE]
        if not following:
            continue  # nothing underneath it; a header with no rows is a data row

        widths = Counter(len([c for c in r if c.strip()]) for r in following)
        modal, agreeing = widths.most_common(1)[0]
        if agreeing / len(following) < _MIN_BODY_AGREEMENT:
            continue  # the rows below disagree with each other, so this is still preamble
        # One short is allowed: a merged banner cell or an unnamed index column
        # leaves a real header narrower than its own body.
        if len([c for c in row if c.strip()]) >= modal - 1:
            return index

    return first_candidate


def _normalise_headers(row: list[str], notes: list[str]) -> list[str]:
    """Whitespace-collapsed, never blank, never duplicated. A blank header is
    an unnamed index column; a duplicated one silently overwrites its twin
    when the row becomes a dict, which is the kind of loss nobody notices."""
    headers: list[str] = []
    seen: Counter[str] = Counter()
    renamed = 0
    for position, cell in enumerate(row, start=1):
        name = _clean(cell)
        if not name:
            name = f"column_{position}"
            renamed += 1
        seen[name.lower()] += 1
        if seen[name.lower()] > 1:
            name = f"{name}_{seen[name.lower()]}"
        headers.append(name)
    if renamed:
        notes.append(f"Named {renamed} unnamed column(s) so nothing was dropped silently.")
    return headers


def _is_summary_row(values: list[str]) -> bool:
    """A totals or balance line: sparse, and says so. The sparsity test is what
    keeps a real transaction whose narration mentions "total" out of this."""
    filled = [v.strip() for v in values if v.strip()]
    if not filled or len(filled) > 3:
        return False
    return any(marker in v.lower() for v in filled for marker in _SUMMARY_MARKERS)


def _shape(grid: list[list[str]], filename: str, notes: list[str]) -> Result[ParsedTable]:
    header_index = _find_header_index(grid)
    if header_index is None:
        # No row looked like a header -- a merged banner cell, or an export
        # with no header at all. Using the first row keeps the file usable and
        # lets the mapping step (which the user confirms) sort it out; refusing
        # it outright would be a worse answer than a named guess.
        header_index = 0
        notes.append("No header row was obvious, so the first row was used as the header.")

    skipped_preamble = sum(1 for row in grid[:header_index] if not _is_blank(row))
    if skipped_preamble:
        notes.append(f"Skipped {skipped_preamble} line(s) above the header.")

    headers = _normalise_headers(grid[header_index], notes)
    if not headers:
        return Err(f"{filename}: no columns found")

    rows: list[dict[str, str]] = []
    blank_rows = 0
    summary_rows = 0
    truncated_rows = 0

    for raw in grid[header_index + 1 :]:
        if _is_blank(raw):
            blank_rows += 1
            continue
        if _is_summary_row(raw):
            summary_rows += 1
            continue
        if len(raw) > len(headers):
            truncated_rows += 1
        values = [_clean(cell) for cell in raw[: len(headers)]]
        values += [""] * (len(headers) - len(values))
        rows.append(dict(zip(headers, values, strict=True)))

    if blank_rows:
        notes.append(f"Dropped {blank_rows} blank row(s).")
    if summary_rows:
        notes.append(f"Dropped {summary_rows} totals/balance line(s) -- those are not records.")
    if truncated_rows:
        notes.append(f"Ignored extra cells on {truncated_rows} over-long row(s).")

    headers, rows, dropped_columns = _drop_empty_columns(headers, rows)
    if dropped_columns:
        notes.append(f"Dropped {dropped_columns} column(s) that were empty from top to bottom.")

    if len(rows) > MAX_ROWS:
        return Err(f"{filename}: {len(rows)} rows exceeds the {MAX_ROWS}-row cap")

    return Ok(ParsedTable(headers=headers, rows=rows, notes=notes))


def _drop_empty_columns(
    headers: list[str], rows: list[dict[str, str]]
) -> tuple[list[str], list[dict[str, str]], int]:
    """An unnamed column that is empty all the way down is an export artefact
    -- a spacer, or the spreadsheet's own row index. A named empty column is
    kept: the uploader may still want to map it, and its absence would be a
    surprise."""
    droppable = [
        header
        for header in headers
        if header.startswith("column_") and all(not row[header].strip() for row in rows)
    ]
    if not droppable:
        return headers, rows, 0
    keep = [header for header in headers if header not in droppable]
    return keep, [{header: row[header] for header in keep} for row in rows], len(droppable)
