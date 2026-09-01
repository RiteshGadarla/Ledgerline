from dataclasses import dataclass, field
from typing import Any

from contracts.models import BankLine, Invoice, Payment, Settlement
from contracts.money import Paise
from ingest.mapper import MappingResponse, SourceRole
from ingest.tabular import ParsedTable
from money.dates import parse_date
from money.parse import parse_amount
from money.result import Err

Record = Invoice | Payment | Settlement | BankLine


@dataclass(frozen=True)
class RowError:
    row_number: int
    reason: str


@dataclass(frozen=True)
class ValidationReport:
    role: SourceRole
    valid_records: list[Record]
    # Capped: a wholly wrong file produces one error per row, and fifty
    # thousand identical reasons is not a report, it is a denial of service on
    # the person reading it.
    errors: list[RowError]
    total_rows: int = 0
    error_count: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def errors_truncated(self) -> bool:
        return self.error_count > len(self.errors)


MAX_REPORTED_ERRORS = 200

# Roles whose identifier is routinely absent from a real export. A bank
# statement has no id column at all -- it has a date, a narration and an
# amount -- so requiring one would reject every genuine statement ever
# downloaded. The synthesised id is positional and stable for a given file.
_ID_PREFIX: dict[SourceRole, str] = {
    "ledger": "INV",
    "gateway": "pay_row",
    "settlement": "STL",
    "bank": "BNK",
}


def _remap_row(row: dict[str, str], table: ParsedTable, mapping: MappingResponse) -> dict[str, str]:
    remapped: dict[str, str] = {}
    for header in table.headers:
        canonical = mapping.canonical_for(header)
        if canonical is not None:
            remapped[canonical] = row.get(header, "")
    return remapped


def _amount(fields: dict[str, str], key: str) -> Paise:
    result = parse_amount(fields.get(key, ""))
    if isinstance(result, Err):
        raise ValueError(f"{key}: {result.reason}")
    return result.value


def _date(fields: dict[str, str], key: str):  # type: ignore[no-untyped-def]
    result = parse_date(fields.get(key, ""))
    if isinstance(result, Err):
        raise ValueError(f"{key}: {result.reason}")
    return result.value


def _build_invoice(fields: dict[str, str]) -> Invoice:
    return Invoice(
        id=fields["id"],
        number=fields.get("number", fields["id"]),
        customer=fields.get("customer", ""),
        amount=_amount(fields, "amount"),
        issued_at=_date(fields, "issued_at"),
        ref=fields.get("ref") or None,
    )


def _build_payment(fields: dict[str, str]) -> Payment:
    from datetime import datetime

    gross = _amount(fields, "gross")
    fee = _amount(fields, "fee") if fields.get("fee") else Paise(0)
    tax = _amount(fields, "tax") if fields.get("tax") else Paise(0)
    net = _amount(fields, "net") if fields.get("net") else Paise(gross - fee - tax)
    captured_date = _date(fields, "captured_at")
    return Payment(
        id=fields["id"],
        order_id=fields.get("order_id") or None,
        invoice_ref=fields.get("invoice_ref") or None,
        gross=gross,
        fee=fee,
        tax=tax,
        net=net,
        status=fields.get("status", "captured"),  # type: ignore[arg-type]
        captured_at=datetime.combine(captured_date, datetime.min.time()),
        method=fields.get("method", "unknown"),
        settlement_id=fields.get("settlement_id") or None,
    )


def _build_settlement(fields: dict[str, str]) -> Settlement:
    payment_ids_raw = fields.get("payment_ids", "")
    payment_ids = [pid.strip() for pid in payment_ids_raw.split(";") if pid.strip()] if payment_ids_raw else []
    return Settlement(
        id=fields["id"],
        utr=fields.get("utr") or None,
        payout=_amount(fields, "payout"),
        fees=_amount(fields, "fees") if fields.get("fees") else Paise(0),
        tax=_amount(fields, "tax") if fields.get("tax") else Paise(0),
        adjustments=_amount(fields, "adjustments") if fields.get("adjustments") else Paise(0),
        settled_at=_date(fields, "settled_at"),
        payment_ids=payment_ids,
    )


def _build_bank_line(fields: dict[str, str]) -> BankLine:
    credit = _amount(fields, "credit") if fields.get("credit") else Paise(0)
    debit = _amount(fields, "debit") if fields.get("debit") else Paise(0)
    # Statements with a single signed Amount column are common; mapped onto
    # `credit`, an outgoing shows up as a negative one. Read it as what it is
    # rather than carrying a negative credit into the engine.
    if credit < 0:
        credit, debit = Paise(0), Paise(debit - credit)
    return BankLine(
        id=fields["id"],
        value_date=_date(fields, "value_date"),
        narration=fields.get("narration", ""),
        credit=credit,
        debit=debit,
        balance=_amount(fields, "balance") if fields.get("balance") else Paise(0),
    )


_BUILDERS: dict[SourceRole, Any] = {
    "ledger": _build_invoice,
    "gateway": _build_payment,
    "settlement": _build_settlement,
    "bank": _build_bank_line,
}

# `id` is deliberately absent from every list: it is synthesised when missing
# (see _ID_PREFIX). What remains is the data without which the row means
# nothing -- an amount with no date, or a date with no amount, is not a
# reconcilable record however politely it is formatted.
_REQUIRED_FIELDS: dict[SourceRole, list[str]] = {
    "ledger": ["amount", "issued_at"],
    "gateway": ["gross", "captured_at"],
    "settlement": ["payout", "settled_at"],
    "bank": ["value_date"],
}


def build_records(role: SourceRole, table: ParsedTable, mapping: MappingResponse) -> ValidationReport:
    """Validate every row through Pydantic and money/. A bad row is recorded
    with its row number and reason; the run proceeds on the valid remainder
    rather than failing the whole upload.

    Two accommodations for files that came out of a real system: a row with no
    identifier gets a positional one rather than being thrown away, and a row
    with nothing in it at all is skipped in silence rather than reported as a
    failure the uploader has to go and look at.
    """
    builder = _BUILDERS[role]
    required = _REQUIRED_FIELDS[role]
    valid_records: list[Record] = []
    errors: list[RowError] = []
    error_count = 0
    considered = 0
    synthesised_ids = 0

    for row_number, row in enumerate(table.rows, start=1):
        fields = _remap_row(row, table, mapping)
        if not any(value.strip() for value in fields.values()):
            continue  # nothing mapped landed in this row; it is not a record
        considered += 1

        if not fields.get("id"):
            fields["id"] = f"{_ID_PREFIX[role]}{row_number:06d}"
            synthesised_ids += 1

        missing = [name for name in required if not fields.get(name)]
        if missing:
            error_count += 1
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append(RowError(row_number, f"missing required field(s): {', '.join(missing)}"))
            continue
        try:
            record = builder(fields)
        except (ValueError, KeyError) as exc:
            error_count += 1
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append(RowError(row_number, str(exc)))
            continue
        except Exception as exc:  # pydantic ValidationError et al.
            error_count += 1
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append(RowError(row_number, f"validation failed: {exc}"))
            continue
        valid_records.append(record)

    notes = list(table.notes)
    if synthesised_ids:
        notes.append(f"Numbered {synthesised_ids} row(s) that carried no identifier of their own.")
    if error_count > len(errors):
        notes.append(f"{error_count} rows failed; showing the first {len(errors)}.")

    return ValidationReport(
        role=role,
        valid_records=valid_records,
        errors=errors,
        total_rows=considered,
        error_count=error_count,
        notes=notes,
    )
