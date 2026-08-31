from fpdf import FPDF

from ingest.pdf import extract_pdf_table
from money.result import Err, Ok

_COLUMN_WIDTHS = [30, 60, 25, 25, 25]


def _tabular_pdf() -> bytes:
    """Columns aligned by fixed x-position with no ruling lines at all --
    the layout most real bank-statement exports actually use, and the reason
    extract_pdf_table leans on gap-based ("text" strategy) clustering rather
    than pdfplumber's line-based table detection."""
    pdf = FPDF()
    pdf.set_font("Helvetica", size=10)
    pdf.add_page()
    rows = [
        ["Date", "Narration", "Credit", "Debit", "Balance"],
        ["01-Jan-24", "NEFT INV1001 Acme", "1000.00", "0.00", "1000.00"],
        ["02-Jan-24", "UPI INV1002 Beta", "2000.00", "0.00", "3000.00"],
        ["03-Jan-24", "CHQ RETURN FEE", "0.00", "50.00", "2950.00"],
    ]
    for data_row in rows:
        for i, cell in enumerate(data_row):
            last = i == len(data_row) - 1
            pdf.cell(
                _COLUMN_WIDTHS[i],
                6,
                text=cell,
                border=0,
                new_x="LMARGIN" if last else "RIGHT",
                new_y="NEXT" if last else "TOP",
            )
    return bytes(pdf.output())


def _line_style_pdf() -> bytes:
    """Plain running text, one statement line per row, with no column
    structure at all -- extract_table finds nothing, forcing the per-line
    date/narration/amount regex fallback."""
    pdf = FPDF()
    pdf.set_font("Courier", size=10)
    pdf.add_page()
    lines = [
        "Statement of Account",
        "",
        "01/01/24 NEFT-INV1001-ACME TRADERS 1,000.00 CR",
        "02/01/24 UPI-INV1002-BETA CORP 2,000.00 CR",
        "03/01/24 CHEQUE RETURN CHARGES 50.00 DR",
    ]
    for line in lines:
        pdf.cell(0, 6, text=line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def test_tabular_pdf_extracts_via_table_strategy() -> None:
    result = extract_pdf_table(_tabular_pdf(), "statement.pdf")
    assert isinstance(result, Ok), result
    table = result.value
    assert len(table.rows) == 3
    assert table.rows[0]["Date"] == "01-Jan-24"
    assert table.rows[1]["Narration"] == "UPI INV1002 Beta"


def test_line_style_pdf_falls_back_to_regex() -> None:
    result = extract_pdf_table(_line_style_pdf(), "statement.pdf")
    assert isinstance(result, Ok), result
    table = result.value
    assert table.headers == ["date", "narration", "amount", "sign"]
    assert len(table.rows) == 3
    assert table.rows[0]["sign"] == "CR"
    assert table.rows[2]["sign"] == "DR"
    assert "INV1001" in table.rows[0]["narration"]


def test_corrupt_pdf_is_an_error() -> None:
    result = extract_pdf_table(b"%PDF-1.4\nnot really a pdf, no xref table", "broken.pdf")
    assert isinstance(result, Err)
