"""Files as they actually arrive.

Every case here is a shape a real finance export produces, not a synthetic
corruption: a bank statement with its letterhead above the columns, a European
CSV, a Tally export in cp1252, a spreadsheet with a spacer column. The bar is
that the parser reads them, says what it repaired, and never silently loses a
row -- a reconciliation that skipped forty lines still balances, and still lies.
"""

import io

import openpyxl  # type: ignore[import-untyped]
import pytest

from ingest.mapper import MappingResponse
from ingest.tabular import parse_table
from ingest.validate import MAX_REPORTED_ERRORS, build_records
from money.result import Err, Ok


def _parsed(content: bytes, filename: str = "upload.csv"):  # type: ignore[no-untyped-def]
    result = parse_table(content, filename)
    assert isinstance(result, Ok), getattr(result, "reason", result)
    return result.value


def _mapping(pairs: dict[str, str]) -> MappingResponse:
    return MappingResponse.model_validate(
        {
            "fields": [
                {"source_header": header, "canonical_field": canonical, "confidence": 1.0}
                for header, canonical in pairs.items()
            ]
        }
    )


class TestMisformattedTables:
    def test_bank_statement_preamble_above_the_header_is_skipped(self) -> None:
        content = (
            b"Statement of Account,,,,\n"
            b"Account Number,50100123456789,,,\n"
            b"Account Name,ACME TRADERS PVT LTD,,,\n"
            b"Period,01-Apr-2024 to 30-Apr-2024,,,\n"
            b",,,,\n"
            b"Date,Narration,Withdrawal,Deposit,Balance\n"
            b"01/04/2024,NEFT CR UTR 1234567890123456 RAZORPAY,,10000.00,110000.00\n"
            b"02/04/2024,UPI CR UTR 6543210987654321 RAZORPAY,,25000.00,135000.00\n"
        )

        table = _parsed(content, "statement.csv")

        assert table.headers == ["Date", "Narration", "Withdrawal", "Deposit", "Balance"]
        assert len(table.rows) == 2
        assert table.rows[0]["Deposit"] == "10000.00"
        assert any("above the header" in note for note in table.notes)

    def test_a_preamble_line_that_looks_wordy_is_not_mistaken_for_the_header(self) -> None:
        """ "Account Name,ACME TRADERS" is two words and no numbers -- the only
        thing separating it from a header is that the body is five columns wide."""
        content = (
            b"Account Name,ACME TRADERS PVT LTD\n"
            b"Date,Narration,Withdrawal,Deposit,Balance\n"
            b"01/04/2024,NEFT CR,,10000.00,110000.00\n"
            b"02/04/2024,NEFT CR,,20000.00,130000.00\n"
        )

        table = _parsed(content, "statement.csv")

        assert table.headers[0] == "Date"
        assert len(table.rows) == 2

    def test_semicolon_delimited_export_is_detected(self) -> None:
        content = b"id;amount;issued_at\nINV1;100.00;01/04/2024\nINV2;200.00;02/04/2024\n"

        table = _parsed(content)

        assert table.headers == ["id", "amount", "issued_at"]
        assert table.rows[0]["amount"] == "100.00"
        assert any("separator" in note for note in table.notes)

    def test_tab_delimited_export_is_detected(self) -> None:
        content = b"id\tamount\nINV1\t100.00\nINV2\t200.00\n"

        table = _parsed(content)

        assert table.headers == ["id", "amount"]
        assert len(table.rows) == 2

    def test_a_semicolon_joined_column_does_not_hijack_the_delimiter(self) -> None:
        """The settlement export joins payment ids with `;`. That splits rows
        into more fields than the comma does -- but into a different number on
        every row, which is what disqualifies it."""
        content = b"id,payout,payment_ids\nSTL1,100.00,pay_a;pay_b;pay_c\nSTL2,200.00,pay_d;pay_e\n"

        table = _parsed(content)

        assert table.headers == ["id", "payout", "payment_ids"]
        assert table.rows[0]["payment_ids"] == "pay_a;pay_b;pay_c"

    def test_windows_encoded_file_is_read_not_rejected(self) -> None:
        content = "id,customer\nINV1,Café Ltd\nINV2,Naïve Foods\n".encode("cp1252")

        table = _parsed(content)

        assert table.rows[0]["customer"] == "Café Ltd"
        assert any("not UTF-8" in note for note in table.notes)

    def test_blank_spacer_rows_are_dropped_and_counted(self) -> None:
        content = b"id,amount\nINV1,100.00\n,\nINV2,200.00\n,,\n"

        table = _parsed(content)

        assert len(table.rows) == 2
        assert any("blank row" in note for note in table.notes)

    def test_totals_and_balance_lines_anywhere_are_dropped(self) -> None:
        content = b"id,amount\nINV1,100.00\nSub Total,100.00\nINV2,200.00\nClosing Balance,300.00\n"

        table = _parsed(content)

        assert [row["id"] for row in table.rows] == ["INV1", "INV2"]
        assert any("totals/balance" in note for note in table.notes)

    def test_a_transaction_whose_narration_says_total_is_kept(self) -> None:
        """The sparsity test is what stops the totals rule eating real rows."""
        content = (
            b"date,narration,credit,balance\n"
            b"01/04/2024,NEFT CR TOTAL SETTLEMENT RAZORPAY UTR 1234567890123456,500.00,1500.00\n"
        )

        table = _parsed(content)

        assert len(table.rows) == 1

    def test_unnamed_columns_are_named_rather_than_dropped(self) -> None:
        content = b"id,,amount\nINV1,x,100.00\n"

        table = _parsed(content)

        assert table.headers == ["id", "column_2", "amount"]
        assert table.rows[0]["column_2"] == "x"

    def test_an_empty_index_column_is_dropped(self) -> None:
        content = b",id,amount\n,INV1,100.00\n,INV2,200.00\n"

        table = _parsed(content)

        assert table.headers == ["id", "amount"]
        assert any("empty from top to bottom" in note for note in table.notes)

    def test_duplicate_headers_do_not_overwrite_each_other(self) -> None:
        content = b"id,amount,amount\nINV1,100.00,999.00\n"

        table = _parsed(content)

        assert table.headers == ["id", "amount", "amount_2"]
        assert table.rows[0]["amount"] == "100.00"
        assert table.rows[0]["amount_2"] == "999.00"

    def test_headers_with_padding_and_non_breaking_spaces_are_normalised(self) -> None:
        content = "  Invoice No  , Amount  \nINV1,100.00\n".encode()

        table = _parsed(content)

        assert table.headers == ["Invoice No", "Amount"]

    def test_ragged_rows_are_padded_and_truncated_not_rejected(self) -> None:
        content = b"id,amount,note\nINV1,100.00\nINV2,200.00,ok,extra\n"

        table = _parsed(content)

        assert table.rows[0]["note"] == ""
        assert table.rows[1]["note"] == "ok"
        assert any("over-long" in note for note in table.notes)

    def test_xlsx_with_a_banner_row_above_the_headers(self) -> None:
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["ACME TRADERS PVT LTD - SALES REGISTER"])
        sheet.append([])
        sheet.append(["id", "amount", "issued_at"])
        sheet.append(["INV1", "100.00", "01/04/2024"])
        sheet.append(["INV2", "200.00", "02/04/2024"])
        buffer = io.BytesIO()
        workbook.save(buffer)

        table = _parsed(buffer.getvalue(), "register.xlsx")

        assert table.headers == ["id", "amount", "issued_at"]
        assert len(table.rows) == 2


class TestMissingData:
    def test_a_bank_statement_with_no_id_column_still_yields_records(self) -> None:
        """Real statements have a date, a narration and an amount. None of
        them has an id column, and requiring one would reject every statement
        anyone has ever downloaded."""
        content = (
            b"Date,Narration,Deposit,Balance\n"
            b"01/04/2024,NEFT CR UTR 1234567890123456,10000.00,110000.00\n"
            b"02/04/2024,NEFT CR UTR 6543210987654321,25000.00,135000.00\n"
        )
        table = _parsed(content, "statement.csv")

        report = build_records(
            "bank",
            table,
            _mapping(
                {
                    "Date": "value_date",
                    "Narration": "narration",
                    "Deposit": "credit",
                    "Balance": "balance",
                }
            ),
        )

        assert len(report.valid_records) == 2
        assert report.errors == []
        assert len({r.id for r in report.valid_records}) == 2  # positional, but distinct
        assert any("no identifier" in note for note in report.notes)

    def test_rows_missing_a_required_field_are_reported_and_the_rest_survive(self) -> None:
        content = b"id,amount,issued_at\nINV1,100.00,01/04/2024\nINV2,,02/04/2024\nINV3,300.00,03/04/2024\n"
        table = _parsed(content)

        report = build_records("ledger", table, _mapping({"id": "id", "amount": "amount", "issued_at": "issued_at"}))

        assert len(report.valid_records) == 2
        assert [e.row_number for e in report.errors] == [2]
        assert "amount" in report.errors[0].reason

    def test_an_unparseable_amount_names_the_field_and_the_row(self) -> None:
        content = b"id,amount,issued_at\nINV1,not money,01/04/2024\n"
        table = _parsed(content)

        report = build_records("ledger", table, _mapping({"id": "id", "amount": "amount", "issued_at": "issued_at"}))

        assert report.valid_records == []
        assert report.errors[0].row_number == 1
        assert "amount" in report.errors[0].reason

    def test_a_signed_amount_column_reads_an_outgoing_as_a_debit(self) -> None:
        content = b"date,narration,amount\n01/04/2024,ATM WDL,-500.00\n01/04/2024,NEFT CR,750.00\n"
        table = _parsed(content)

        report = build_records(
            "bank", table, _mapping({"date": "value_date", "narration": "narration", "amount": "credit"})
        )

        outgoing, incoming = report.valid_records
        assert outgoing.credit == 0 and outgoing.debit == 50_000  # type: ignore[union-attr]
        assert incoming.credit == 75_000 and incoming.debit == 0  # type: ignore[union-attr]

    def test_a_wholly_wrong_file_caps_its_error_list(self) -> None:
        """One error per row for fifty thousand rows is not a report."""
        rows = "\n".join(f"INV{i},,," for i in range(MAX_REPORTED_ERRORS + 250))
        content = f"id,amount,issued_at,note\n{rows}\n".encode()
        table = _parsed(content)

        report = build_records("ledger", table, _mapping({"id": "id", "amount": "amount", "issued_at": "issued_at"}))

        assert len(report.errors) == MAX_REPORTED_ERRORS
        assert report.error_count == MAX_REPORTED_ERRORS + 250
        assert report.errors_truncated
        assert any("showing the first" in note for note in report.notes)

    def test_rows_with_nothing_mapped_are_skipped_silently_not_reported(self) -> None:
        content = b"id,amount,issued_at,junk\nINV1,100.00,01/04/2024,x\n,,,leftover\n"
        table = _parsed(content)

        report = build_records("ledger", table, _mapping({"id": "id", "amount": "amount", "issued_at": "issued_at"}))

        assert len(report.valid_records) == 1
        assert report.errors == []  # the trailing row mapped to nothing at all

    def test_parser_notes_reach_the_validation_report(self) -> None:
        content = b"Statement\n,\nid,amount,issued_at\nINV1,100.00,01/04/2024\n"
        table = _parsed(content)

        report = build_records("ledger", table, _mapping({"id": "id", "amount": "amount", "issued_at": "issued_at"}))

        assert report.notes  # whatever the parser repaired travels with the report


class TestStillRejectsWhatItShould:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            (b"", "empty"),
            (b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "image"),
            (b"%PDF-1.4\n%...", "PDF"),
        ],
    )
    def test_bad_uploads_come_back_as_typed_errors(self, content: bytes, expected: str) -> None:
        result = parse_table(content, "upload.csv")
        assert isinstance(result, Err)
        assert expected in result.reason
