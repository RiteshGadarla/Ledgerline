from datetime import date, datetime

import pytest

from contracts.corpus import Corpus
from contracts.models import BankLine, Invoice, Payment, Settlement
from contracts.money import Paise
from engine import passes
from engine.index import build_index


def _invoice(id_: str, amount: int, issued: date = date(2024, 1, 1), ref: str | None = None) -> Invoice:
    return Invoice(id=id_, number=id_, customer="Acme Traders", amount=Paise(amount), issued_at=issued, ref=ref)


def _payment(
    id_: str,
    gross: int,
    invoice_ref: str | None = None,
    captured: date = date(2024, 1, 1),
    settlement_id: str | None = None,
    status: str = "captured",
) -> Payment:
    fee = Paise(gross * 2 // 100)
    tax = Paise(fee * 18 // 100)
    return Payment(
        id=id_,
        order_id=None,
        invoice_ref=invoice_ref,
        gross=Paise(gross),
        fee=fee,
        tax=tax,
        net=Paise(gross - fee - tax),
        status=status,  # type: ignore[arg-type]
        captured_at=datetime.combine(captured, datetime.min.time()),
        method="upi",
        settlement_id=settlement_id,
    )


def _settlement(
    id_: str, payment_ids: list[str], payout: int, utr: str | None, fees: int = 0, tax: int = 0
) -> Settlement:
    return Settlement(
        id=id_,
        utr=utr,
        payout=Paise(payout),
        fees=Paise(fees),
        tax=Paise(tax),
        adjustments=Paise(0),
        settled_at=date(2024, 1, 3),
        payment_ids=payment_ids,
    )


def _bank_line(id_: str, narration: str, credit: int) -> BankLine:
    return BankLine(
        id=id_, value_date=date(2024, 1, 3), narration=narration, credit=Paise(credit), debit=Paise(0), balance=Paise(0)
    )


class TestP1BankLineToSettlement:
    def test_matches_on_utr_and_amount(self) -> None:
        settlement = _settlement("STL1", [], 10000, utr="1234567890123456")
        bank_line = _bank_line("BNK1", "NEFT UTR 1234567890123456 SETTLEMENT PAYOUT", 10000)
        corpus = Corpus(invoices=[], payments=[], settlements=[settlement], bank_lines=[bank_line])
        index = build_index(corpus)

        result = passes.p1_bank_line_to_settlement(index)

        assert result.matched == [
            ("STL1", "BNK1", result.matched[0][2]),
        ]
        assert result.ambiguous == []
        assert result.unmatched == []

    def test_amount_mismatch_leaves_unmatched(self) -> None:
        settlement = _settlement("STL1", [], 10000, utr="1234567890123456")
        bank_line = _bank_line("BNK1", "NEFT UTR 1234567890123456 SETTLEMENT PAYOUT", 9999)
        corpus = Corpus(invoices=[], payments=[], settlements=[settlement], bank_lines=[bank_line])
        index = build_index(corpus)

        result = passes.p1_bank_line_to_settlement(index)

        assert result.matched == []
        assert result.unmatched == ["STL1"]

    def test_missing_utr_leaves_unmatched(self) -> None:
        settlement = _settlement("STL1", [], 10000, utr=None)
        corpus = Corpus(invoices=[], payments=[], settlements=[settlement], bank_lines=[])
        index = build_index(corpus)

        result = passes.p1_bank_line_to_settlement(index)

        assert result.unmatched == ["STL1"]

    def test_two_bank_lines_with_same_utr_and_amount_are_ambiguous(self) -> None:
        settlement = _settlement("STL1", [], 10000, utr="1234567890123456")
        bank_line_a = _bank_line("BNK1", "NEFT UTR 1234567890123456 PAYOUT", 10000)
        bank_line_b = _bank_line("BNK2", "NEFT UTR 1234567890123456 PAYOUT", 10000)
        corpus = Corpus(invoices=[], payments=[], settlements=[settlement], bank_lines=[bank_line_a, bank_line_b])
        index = build_index(corpus)

        result = passes.p1_bank_line_to_settlement(index)

        assert result.matched == []
        assert len(result.ambiguous) == 1
        settlement_id, candidates, code = result.ambiguous[0]
        assert settlement_id == "STL1"
        assert set(candidates) == {"BNK1", "BNK2"}
        from contracts.enums import ExceptionCode

        assert code == ExceptionCode.AMBIGUOUS_MULTI_CANDIDATE


class TestP2BatchAlgebra:
    def test_ties_out_exactly(self) -> None:
        payment = _payment("pay_1", 10000, settlement_id="STL1")
        payout = 10000 - payment.fee - payment.tax
        settlement = _settlement("STL1", ["pay_1"], payout=payout, utr=None, fees=payment.fee, tax=payment.tax)
        corpus = Corpus(invoices=[], payments=[payment], settlements=[settlement], bank_lines=[])
        index = build_index(corpus)

        ties_out, residual = passes.p2_verify_batch_algebra(index, "STL1")

        assert ties_out is True
        assert residual == 0

    def test_refund_reduces_expected_payout(self) -> None:
        captured = _payment("pay_1", 10000, settlement_id="STL1")
        refunded = _payment("pay_2", 5000, settlement_id="STL1", status="refunded")
        gross_total = captured.gross + refunded.gross
        fees = captured.fee + refunded.fee
        tax = captured.tax + refunded.tax
        payout = gross_total - fees - tax - refunded.gross
        settlement = _settlement("STL1", ["pay_1", "pay_2"], payout=payout, utr=None, fees=fees, tax=tax)
        corpus = Corpus(invoices=[], payments=[captured, refunded], settlements=[settlement], bank_lines=[])
        index = build_index(corpus)

        ties_out, residual = passes.p2_verify_batch_algebra(index, "STL1")

        assert ties_out is True
        assert residual == 0

    def test_mismatch_is_detected(self) -> None:
        payment = _payment("pay_1", 10000, settlement_id="STL1")
        settlement = _settlement("STL1", ["pay_1"], payout=999999, utr=None, fees=payment.fee, tax=payment.tax)
        corpus = Corpus(invoices=[], payments=[payment], settlements=[settlement], bank_lines=[])
        index = build_index(corpus)

        ties_out, residual = passes.p2_verify_batch_algebra(index, "STL1")

        assert ties_out is False
        assert residual != 0


class TestP3InvoiceToPayment:
    def test_exact_ref_match(self) -> None:
        invoice = _invoice("INV1", 10000, ref="INV1")
        payment = _payment("pay_1", 10000, invoice_ref="INV1")
        corpus = Corpus(invoices=[invoice], payments=[payment], settlements=[], bank_lines=[])
        index = build_index(corpus)

        result = passes.p3_invoice_to_payment(index, "pay_1")

        assert result is not None
        invoice_id, pass_id, confidence = result
        assert invoice_id == "INV1"
        from contracts.enums import PassId

        assert pass_id == PassId.P3
        assert confidence == passes.CONFIDENCE[PassId.P3]

    def test_no_ref_falls_back_to_unique_amount_and_date(self) -> None:
        invoice = _invoice("INV1", 10000, issued=date(2024, 1, 1))
        payment = _payment("pay_1", 10000, invoice_ref=None, captured=date(2024, 1, 2))
        corpus = Corpus(invoices=[invoice], payments=[payment], settlements=[], bank_lines=[])
        index = build_index(corpus)

        result = passes.p3_invoice_to_payment(index, "pay_1")

        assert result is not None
        assert result[0] == "INV1"

    def test_ambiguous_fallback_candidates_are_not_matched(self) -> None:
        invoice_a = _invoice("INV1", 10000, issued=date(2024, 1, 1))
        invoice_b = _invoice("INV2", 10000, issued=date(2024, 1, 1))
        payment = _payment("pay_1", 10000, invoice_ref=None, captured=date(2024, 1, 1))
        corpus = Corpus(invoices=[invoice_a, invoice_b], payments=[payment], settlements=[], bank_lines=[])
        index = build_index(corpus)

        result = passes.p3_invoice_to_payment(index, "pay_1")

        assert result is None

    def test_outside_date_window_is_not_matched(self) -> None:
        invoice = _invoice("INV1", 10000, issued=date(2024, 1, 1))
        payment = _payment("pay_1", 10000, invoice_ref=None, captured=date(2024, 2, 1))
        corpus = Corpus(invoices=[invoice], payments=[payment], settlements=[], bank_lines=[])
        index = build_index(corpus)

        assert passes.p3_invoice_to_payment(index, "pay_1") is None

    def test_outside_fee_band_is_not_matched(self) -> None:
        invoice = _invoice("INV1", 10000, issued=date(2024, 1, 1))
        payment = _payment("pay_1", 20000, invoice_ref=None, captured=date(2024, 1, 1))
        corpus = Corpus(invoices=[invoice], payments=[payment], settlements=[], bank_lines=[])
        index = build_index(corpus)

        assert passes.p3_invoice_to_payment(index, "pay_1") is None


def test_payer_similarity_utility() -> None:
    assert passes.payer_similarity("Acme Traders", "Acme Traders") == 1.0
    assert passes.payer_similarity("Acme Traders", "ACME TRADERS") == 1.0
    assert passes.payer_similarity("Acme Traders", "Totally Different Co") < passes.PAYER_SIMILARITY_THRESHOLD


class TestP4SubsetSum:
    def test_finds_unique_two_way_split(self) -> None:
        payments = [_payment("pay_1", 6000), _payment("pay_2", 4000), _payment("pay_3", 3000)]
        corpus = Corpus(invoices=[], payments=payments, settlements=[], bank_lines=[])
        index = build_index(corpus)

        subset, exceeded = passes.p4_subset_sum(["pay_1", "pay_2", "pay_3"], index, Paise(10000))

        assert exceeded is False
        assert subset is not None
        assert set(subset) == {"pay_1", "pay_2"}

    def test_no_solution_returns_none(self) -> None:
        payments = [_payment("pay_1", 6000), _payment("pay_2", 4000)]
        corpus = Corpus(invoices=[], payments=payments, settlements=[], bank_lines=[])
        index = build_index(corpus)

        subset, exceeded = passes.p4_subset_sum(["pay_1", "pay_2"], index, Paise(999))

        assert exceeded is False
        assert subset is None

    def test_ambiguous_multiple_solutions_returns_none(self) -> None:
        payments = [_payment("pay_1", 5000), _payment("pay_2", 5000), _payment("pay_3", 5000), _payment("pay_4", 5000)]
        corpus = Corpus(invoices=[], payments=payments, settlements=[], bank_lines=[])
        index = build_index(corpus)

        subset, exceeded = passes.p4_subset_sum(["pay_1", "pay_2", "pay_3", "pay_4"], index, Paise(10000))

        assert exceeded is False
        assert subset is None

    def test_candidate_cap_bounds_search_to_eight(self) -> None:
        payments = [_payment(f"pay_{i}", 1000) for i in range(50)]
        corpus = Corpus(invoices=[], payments=payments, settlements=[], bank_lines=[])
        index = build_index(corpus)

        subset, exceeded = passes.p4_subset_sum([p.id for p in payments], index, Paise(1_000_000))

        assert exceeded is False
        assert subset is None  # target unreachable within the first 8 candidates (max 8000 paise)

    def test_node_limit_exceeded_returns_gracefully_not_a_hang(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(passes, "_SUBSET_NODE_LIMIT", 2)
        payments = [_payment(f"pay_{i}", 1000 + i) for i in range(8)]
        corpus = Corpus(invoices=[], payments=payments, settlements=[], bank_lines=[])
        index = build_index(corpus)

        subset, exceeded = passes.p4_subset_sum([p.id for p in payments], index, Paise(999_999))

        assert exceeded is True
        assert subset is None
