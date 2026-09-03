from contracts.models import BankLine, Invoice, Payment, Settlement
from ingest.mapper import MappingResponse
from ingest.tabular import ParsedTable
from ingest.validate import build_records


def _mapping(pairs: dict[str, str | None]) -> MappingResponse:
    return MappingResponse.model_validate(
        {
            "fields": [
                {"source_header": header, "canonical_field": canonical, "confidence": 0.9}
                for header, canonical in pairs.items()
            ]
        }
    )


def test_valid_ledger_rows_build_invoices() -> None:
    table = ParsedTable(
        headers=["Inv No", "Amt", "Date"],
        rows=[
            {"Inv No": "INV1001", "Amt": "1,000.00", "Date": "01-Jan-24"},
            {"Inv No": "INV1002", "Amt": "2,000.00", "Date": "02-Jan-24"},
        ],
    )
    mapping = _mapping({"Inv No": "id", "Amt": "amount", "Date": "issued_at"})

    report = build_records("ledger", table, mapping)

    assert report.errors == []
    assert len(report.valid_records) == 2
    assert all(isinstance(record, Invoice) for record in report.valid_records)
    invoice = report.valid_records[0]
    assert isinstance(invoice, Invoice)
    assert invoice.amount == 100_000


def test_bad_row_becomes_a_row_error_and_run_proceeds_on_the_remainder() -> None:
    table = ParsedTable(
        headers=["Inv No", "Amt", "Date"],
        rows=[
            {"Inv No": "INV1001", "Amt": "1,000.00", "Date": "01-Jan-24"},
            {"Inv No": "INV1002", "Amt": "not-a-number", "Date": "02-Jan-24"},
            {"Inv No": "INV1003", "Amt": "3,000.00", "Date": "03-Jan-24"},
        ],
    )
    mapping = _mapping({"Inv No": "id", "Amt": "amount", "Date": "issued_at"})

    report = build_records("ledger", table, mapping)

    assert len(report.valid_records) == 2
    assert len(report.errors) == 1
    assert report.errors[0].row_number == 2
    assert "amount" in report.errors[0].reason


def test_missing_required_field_is_reported_by_row_number() -> None:
    table = ParsedTable(
        headers=["Inv No", "Amt"],
        rows=[{"Inv No": "INV1001", "Amt": "1,000.00"}],
    )
    mapping = _mapping({"Inv No": "id", "Amt": "amount"})  # no issued_at mapping at all

    report = build_records("ledger", table, mapping)

    assert report.valid_records == []
    assert len(report.errors) == 1
    assert "issued_at" in report.errors[0].reason


def test_gateway_rows_build_payments_with_derived_net() -> None:
    table = ParsedTable(
        headers=["Payment Id", "Gross", "Captured"],
        rows=[{"Payment Id": "pay_1", "Gross": "100.00", "Captured": "01-Jan-24"}],
    )
    mapping = _mapping({"Payment Id": "id", "Gross": "gross", "Captured": "captured_at"})

    report = build_records("gateway", table, mapping)

    assert report.errors == []
    payment = report.valid_records[0]
    assert isinstance(payment, Payment)
    assert payment.net == payment.gross  # no fee/tax columns mapped => both default to zero


def test_settlement_rows_split_payment_ids() -> None:
    table = ParsedTable(
        headers=["Settlement Id", "Payout", "Settled", "Payments"],
        rows=[{"Settlement Id": "STL1", "Payout": "500.00", "Settled": "03-Jan-24", "Payments": "pay_1;pay_2"}],
    )
    mapping = _mapping({"Settlement Id": "id", "Payout": "payout", "Settled": "settled_at", "Payments": "payment_ids"})

    report = build_records("settlement", table, mapping)

    assert report.errors == []
    settlement = report.valid_records[0]
    assert isinstance(settlement, Settlement)
    assert settlement.payment_ids == ["pay_1", "pay_2"]


def test_bank_rows_build_bank_lines() -> None:
    table = ParsedTable(
        headers=["Line Id", "Value Date", "Narration", "Credit"],
        rows=[{"Line Id": "BNK1", "Value Date": "03-Jan-24", "Narration": "NEFT CREDIT", "Credit": "500.00"}],
    )
    mapping = _mapping({"Line Id": "id", "Value Date": "value_date", "Narration": "narration", "Credit": "credit"})

    report = build_records("bank", table, mapping)

    assert report.errors == []
    bank_line = report.valid_records[0]
    assert isinstance(bank_line, BankLine)
    assert bank_line.credit == 50_000


def test_gateway_rows_timestamped_to_the_minute_are_valid() -> None:
    # The shape of a real captured-payments export: a date column that is
    # actually a timestamp. Every row here is good; none may be rejected for
    # having said what time of day it happened.
    table = ParsedTable(
        headers=["payment_id", "amount_captured", "payment_date"],
        rows=[
            {"payment_id": "pay_G5051", "amount_captured": "22498.69", "payment_date": "2026-08-04 00:00"},
            {"payment_id": "pay_G5028", "amount_captured": "1428.52", "payment_date": "2026-07-15 10:23:45"},
            {"payment_id": "pay_G5048", "amount_captured": "19279.40", "payment_date": "2026-07-15T09:15:00+05:30"},
        ],
    )
    mapping = _mapping({"payment_id": "id", "amount_captured": "gross", "payment_date": "captured_at"})

    report = build_records("gateway", table, mapping)

    assert report.errors == []
    assert len(report.valid_records) == 3
    payment = report.valid_records[0]
    assert isinstance(payment, Payment)
    assert payment.gross == 2_249_869
    assert payment.captured_at.date().isoformat() == "2026-08-04"
