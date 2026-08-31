from dataclasses import dataclass
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
    errors: list[RowError]


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
    return BankLine(
        id=fields["id"],
        value_date=_date(fields, "value_date"),
        narration=fields.get("narration", ""),
        credit=_amount(fields, "credit") if fields.get("credit") else Paise(0),
        debit=_amount(fields, "debit") if fields.get("debit") else Paise(0),
        balance=_amount(fields, "balance") if fields.get("balance") else Paise(0),
    )


_BUILDERS: dict[SourceRole, Any] = {
    "ledger": _build_invoice,
    "gateway": _build_payment,
    "settlement": _build_settlement,
    "bank": _build_bank_line,
}

_REQUIRED_FIELDS: dict[SourceRole, list[str]] = {
    "ledger": ["id", "amount", "issued_at"],
    "gateway": ["id", "gross", "captured_at"],
    "settlement": ["id", "payout", "settled_at"],
    "bank": ["id", "value_date"],
}


def build_records(role: SourceRole, table: ParsedTable, mapping: MappingResponse) -> ValidationReport:
    """Validate every row through Pydantic and money/. A bad row is recorded
    with its row number and reason; the run proceeds on the valid remainder
    rather than failing the whole upload."""
    builder = _BUILDERS[role]
    required = _REQUIRED_FIELDS[role]
    valid_records: list[Record] = []
    errors: list[RowError] = []

    for row_number, row in enumerate(table.rows, start=1):
        fields = _remap_row(row, table, mapping)
        missing = [field for field in required if not fields.get(field)]
        if missing:
            errors.append(RowError(row_number, f"missing required field(s): {', '.join(missing)}"))
            continue
        try:
            record = builder(fields)
        except (ValueError, KeyError) as exc:
            errors.append(RowError(row_number, str(exc)))
            continue
        except Exception as exc:  # pydantic ValidationError et al.
            errors.append(RowError(row_number, f"validation failed: {exc}"))
            continue
        valid_records.append(record)

    return ValidationReport(role=role, valid_records=valid_records, errors=errors)
