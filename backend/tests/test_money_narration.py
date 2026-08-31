import pytest

from money.narration import extract

# Each case checks only the fields relevant to what it demonstrates; other
# fields are left unchecked. `None` in an expected slot means "don't check".

RAIL_CASES = [
    ("NEFT UTR N12345678901 FROM ACME TRADERS", "NEFT"),
    ("neft utr n12345678901 from acme traders", "NEFT"),
    ("IMPS/UTR:412345678901/JOHN DOE", "IMPS"),
    ("imps/utr:412345678901/john doe", "IMPS"),
    ("UPI/RRN 123456789012/PAYTM/RAVI KUMAR", "UPI"),
    ("upi/rrn 123456789012/paytm/ravi kumar", "UPI"),
    ("RTGS UTR RTGS0001234567 XYZ PVT LTD", "RTGS"),
    ("rtgs utr rtgs0001234567 xyz pvt ltd", "RTGS"),
]


@pytest.mark.parametrize(("narration", "expected_rail"), RAIL_CASES)
def test_rail_detection(narration: str, expected_rail: str) -> None:
    assert extract(narration).rail == expected_rail


UTR_LABEL_CASES = [
    ("UTR:N12345678901 SETTLEMENT", ["N12345678901"]),
    ("UTR NO. N12345678901 SETTLEMENT", ["N12345678901"]),
    ("UTR NUMBER N12345678901 SETTLEMENT", ["N12345678901"]),
    ("UTR# N12345678901 SETTLEMENT", ["N12345678901"]),
    ("utr n12345678901 settlement", ["N12345678901"]),
]


@pytest.mark.parametrize(("narration", "expected_utrs"), UTR_LABEL_CASES)
def test_utr_label_variants(narration: str, expected_utrs: list[str]) -> None:
    assert extract(narration).utrs == expected_utrs


RRN_CASES = [
    ("RRN:123456789012 PAYTM", ["123456789012"]),
    ("RRN NO. 123456789012 PAYTM", ["123456789012"]),
    ("rrn 123456789012 paytm", ["123456789012"]),
]


@pytest.mark.parametrize(("narration", "expected_rrns"), RRN_CASES)
def test_rrn_variants(narration: str, expected_rrns: list[str]) -> None:
    assert extract(narration).rrns == expected_rrns


ID_CASES = [
    ("Settlement payout pay_Jd8x9KL2mnQoWe", "pay_ids", ["pay_Jd8x9KL2mnQoWe"]),
    ("Two ids pay_AAAAAAbbbbbb pay_CCCCCCdddddd", "pay_ids", ["pay_AAAAAAbbbbbb", "pay_CCCCCCdddddd"]),
    ("Order ref order_Jd8x9KL2mnQoWe captured", "order_ids", ["order_Jd8x9KL2mnQoWe"]),
    ("No ids in this narration at all", "pay_ids", []),
]


@pytest.mark.parametrize(("narration", "field", "expected"), ID_CASES)
def test_pay_and_order_ids(narration: str, field: str, expected: list[str]) -> None:
    assert getattr(extract(narration), field) == expected


INVOICE_CASES = [
    ("Payment for INV-2024-0091 received", ["INV-2024-0091"]),
    ("Payment for INV_2024_0091 received", ["INV_2024_0091"]),
    ("Payment for INV20240091 received", ["INV20240091"]),
    ("Payment for inv 20240091 received", ["INV 20240091"]),
    ("No invoice token here", []),
]


@pytest.mark.parametrize(("narration", "expected"), INVOICE_CASES)
def test_invoice_token_variants(narration: str, expected: list[str]) -> None:
    assert extract(narration).invoice_tokens == expected


def test_narration_with_two_utrs_returns_both() -> None:
    narration = "NEFT UTR N11111111111 REVERSAL OF UTR N22222222222 DOUBLE CREDIT"
    result = extract(narration)
    assert result.utrs == ["N11111111111", "N22222222222"]


def test_missing_utr_returns_empty_list_not_none() -> None:
    result = extract("UPI txn narration only ACME CORP no reference")
    assert result.utrs == []


def test_duplicate_utr_mentions_are_deduped() -> None:
    result = extract("UTR N12345678901 CONFIRMED UTR N12345678901 SETTLED")
    assert result.utrs == ["N12345678901"]


WHITESPACE_CASES = [
    "  double   spaced   narration   ACME   CORP  ",
    "tab\tseparated\tnarration\tACME\tCORP",
    "newline\nseparated\nnarration\nACME\nCORP",
]


@pytest.mark.parametrize("narration", WHITESPACE_CASES)
def test_irregular_whitespace_never_raises_and_collapses(narration: str) -> None:
    result = extract(narration)
    assert result.payer is not None


PAYER_CASES = [
    ("PAYMENT FROM JOHN D", "JOHN D"),
    ("TRANSFER BY RAVI KUMAR SETTLEMENT", "RAVI KUMAR"),
    ("UTR N12345678901", None),
    ("", None),
]


@pytest.mark.parametrize(("narration", "expected_payer"), PAYER_CASES)
def test_payer_candidate_extraction(narration: str, expected_payer: str | None) -> None:
    assert extract(narration).payer == expected_payer


MIXED_CASES = [
    "NEFT UTR N98765432109 FROM GLOBEX INDUSTRIES pay_Ab12Cd34Ef56 INV-88",
    "IMPS UTR 411122223333 RRN 555566667777 RAVI KUMAR order_XyZ123456789",
    "RTGS UTR RTGS9998887776 SETTLEMENT FOR INV_9001 ACME PVT LTD",
    "UPI RRN 999988887777 PAYTM PAYMENTS BANK SETTLEMENT",
    "no rail no utr no ids just a plain narration ACME LOGISTICS",
    "NEFT/UTR:N00000000001/UTR:N00000000002/BATCH REVERSAL",
]


@pytest.mark.parametrize("narration", MIXED_CASES)
def test_mixed_narrations_never_raise(narration: str) -> None:
    result = extract(narration)
    assert isinstance(result.utrs, list)
    assert isinstance(result.rrns, list)
    assert isinstance(result.pay_ids, list)
    assert isinstance(result.order_ids, list)
    assert isinstance(result.invoice_tokens, list)
