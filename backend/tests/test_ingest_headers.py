"""The deterministic half of schema mapping.

Every case here is taken from a real four-file upload that reconciled nothing
because the model was asked questions it did not need to be asked.
"""

from ingest.headers import claimed_fields, deterministic_mapping, normalise


def test_a_real_ledger_export_resolves_its_identifier_to_the_id() -> None:
    # The failure this was written for: `invoice_id` mapped to `number`, which
    # left the invoice with a synthesised positional id that no payment cites.
    mapping = deterministic_mapping(
        "ledger", ["invoice_id", "customer_name", "invoice_date", "amount_due", "currency", "status", "due_date"]
    )
    assert mapping["invoice_id"] == "id"
    assert mapping["amount_due"] == "amount"
    assert mapping["invoice_date"] == "issued_at"
    assert mapping["currency"] is None


def test_a_settlement_payout_is_the_net_not_the_gross() -> None:
    # The bank credits the net. Mapping `gross_amount` onto the payout makes
    # every bank comparison miss by exactly the fee.
    mapping = deterministic_mapping(
        "settlement",
        [
            "settlement_id",
            "payment_ref",
            "gross_amount",
            "gateway_fee",
            "gst_on_fee",
            "net_payout",
            "settlement_date",
            "utr_number",
        ],
    )
    assert mapping["net_payout"] == "payout"
    assert mapping["utr_number"] == "utr"
    assert mapping["payment_ref"] == "payment_ids"
    assert mapping["gateway_fee"] == "fees"
    # Left for the model to answer for, and refused if it says "payout".
    assert "gross_amount" not in mapping


def test_a_bank_amount_column_is_never_left_unmapped() -> None:
    # Unmapped, every credit reads as zero and nothing can tie out.
    mapping = deterministic_mapping(
        "bank", ["txn_id", "value_date", "description", "reference_no", "type", "amount", "balance"]
    )
    assert mapping["amount"] == "credit"
    assert mapping["description"] == "narration"
    assert mapping["value_date"] == "value_date"
    assert mapping["type"] is None


def test_a_field_is_claimed_once_and_the_richer_name_wins() -> None:
    # Both are plausible narrations; the descriptive one is the useful one.
    mapping = deterministic_mapping("bank", ["Date", "Narration", "Reference No", "Credit", "Balance"])
    assert mapping["Narration"] == "narration"
    assert mapping.get("Reference No") is None or "Reference No" not in mapping
    assert list(mapping.values()).count("narration") == 1


def test_separate_credit_and_debit_columns_both_land() -> None:
    mapping = deterministic_mapping("bank", ["Value Date", "Particulars", "Withdrawal", "Deposit", "Balance"])
    assert mapping["Deposit"] == "credit"
    assert mapping["Withdrawal"] == "debit"
    assert mapping["Particulars"] == "narration"


def test_normalise_ignores_case_punctuation_and_spacing() -> None:
    assert normalise("  UTR_Number ") == "utr number"
    assert normalise("Net-Payout") == "net payout"
    assert normalise("Amount (INR)") == "amount inr"


def test_claimed_fields_ignores_the_unmapped() -> None:
    assert claimed_fields({"a": "id", "b": None, "c": "amount"}) == {"id", "amount"}
