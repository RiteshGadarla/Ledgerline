from contracts.corpus import Corpus
from contracts.enums import PassId
from contracts.models import BankLine, Invoice, Payment, Settlement
from contracts.money import Paise
from datagen.generator import generate_corpus
from engine.index import CorpusIndex, build_index
from engine.pipeline import match
from engine.verifier import MatchProposal, UsedRecordIds, verify
from money.result import Err, Ok
from tests.conftest import make_bank_line, make_payment, make_settlement


def _index_for(
    payment: Payment,
    settlement: Settlement,
    bank_line: BankLine | None = None,
    invoice: Invoice | None = None,
) -> CorpusIndex:
    corpus = Corpus(
        invoices=[invoice] if invoice else [],
        payments=[payment],
        settlements=[settlement],
        bank_lines=[bank_line] if bank_line else [],
    )
    return build_index(corpus)


def _base_proposal(
    invoice_ids: list[str] | None = None,
    payment_ids: list[str] | None = None,
    settlement_id: str | None = "STL1",
    bank_line_id: str | None = None,
) -> MatchProposal:
    return MatchProposal(
        invoice_ids=invoice_ids if invoice_ids is not None else [],
        payment_ids=payment_ids if payment_ids is not None else ["pay_1"],
        settlement_id=settlement_id,
        bank_line_id=bank_line_id,
        pass_id=PassId.P2,
        confidence=0.99,
        evidence=[],
    )


class TestAdversarialSuite:
    def test_off_by_one_paise_is_rejected(self) -> None:
        payment = make_payment("pay_1", 10000, settlement_id="STL1")
        settlement = make_settlement(
            "STL1", ["pay_1"], payout=10000 - payment.fee - payment.tax + 1, utr=None, fees=payment.fee, tax=payment.tax
        )
        index = _index_for(payment, settlement)

        result = verify(_base_proposal(), index)

        assert isinstance(result, Err)
        assert "residual" in result.reason

    def test_swapped_payment_id_is_rejected(self) -> None:
        payment_a = make_payment("pay_1", 10000, settlement_id="STL1")
        payment_b = make_payment("pay_2", 5000, settlement_id="STL2")
        payout = payment_a.gross - payment_a.fee - payment_a.tax
        settlement = make_settlement("STL1", ["pay_1"], payout=payout, utr=None, fees=payment_a.fee, tax=payment_a.tax)
        corpus = Corpus(invoices=[], payments=[payment_a, payment_b], settlements=[settlement], bank_lines=[])
        index = build_index(corpus)

        # proposal claims pay_2 is part of STL1, but the settlement's real membership is only pay_1
        result = verify(_base_proposal(payment_ids=["pay_2"]), index)

        assert isinstance(result, Err)
        assert "membership" in result.reason

    def test_reference_to_nonexistent_record_is_rejected(self) -> None:
        payment = make_payment("pay_1", 10000, settlement_id="STL1")
        settlement = make_settlement("STL1", ["pay_1"], payout=9800, utr=None)
        index = _index_for(payment, settlement)

        result = verify(_base_proposal(invoice_ids=["INV_GHOST"]), index)

        assert isinstance(result, Err)
        assert "unknown invoice id" in result.reason

    def test_group_overlapping_another_group_is_rejected(self) -> None:
        payment = make_payment("pay_1", 10000, settlement_id="STL1")
        settlement = make_settlement(
            "STL1", ["pay_1"], payout=10000 - payment.fee - payment.tax, utr=None, fees=payment.fee, tax=payment.tax
        )
        index = _index_for(payment, settlement)

        first = verify(_base_proposal(), index)
        assert isinstance(first, Ok)
        used = UsedRecordIds().with_group(first.value)

        second = verify(_base_proposal(), index, used)

        assert isinstance(second, Err)
        assert "already claimed" in second.reason

    def test_bank_credit_not_equal_to_payout_is_rejected(self) -> None:
        payment = make_payment("pay_1", 10000, settlement_id="STL1")
        settlement = make_settlement(
            "STL1", ["pay_1"], payout=10000 - payment.fee - payment.tax, utr="1" * 16, fees=payment.fee, tax=payment.tax
        )
        bank_line = make_bank_line("BNK1", "NEFT UTR " + "1" * 16, credit=99999)
        index = _index_for(payment, settlement, bank_line=bank_line)

        result = verify(_base_proposal(bank_line_id="BNK1"), index)

        assert isinstance(result, Err)
        assert "does not equal" in result.reason

    def test_valid_proposal_is_accepted(self) -> None:
        payment = make_payment("pay_1", 10000, settlement_id="STL1")
        settlement = make_settlement(
            "STL1", ["pay_1"], payout=10000 - payment.fee - payment.tax, utr=None, fees=payment.fee, tax=payment.tax
        )
        index = _index_for(payment, settlement)

        result = verify(_base_proposal(), index)

        assert isinstance(result, Ok)
        assert result.value.status == "auto"
        assert result.value.residual == 0


def test_total_accounting_amounts_reconcile_per_kind() -> None:
    corpus, _ = generate_corpus(1001, 150)
    result = match(corpus)
    index = build_index(corpus)

    matched_invoice_ids = {iid for g in result.groups for iid in g.invoice_ids}
    matched_payment_ids = {pid for g in result.groups for pid in g.payment_ids}
    matched_settlement_ids = {g.settlement_id for g in result.groups if g.settlement_id}
    matched_bank_line_ids = {g.bank_line_id for g in result.groups if g.bank_line_id}

    exceptioned_invoice_ids = {r.id for e in result.exceptions for r in e.records if r.kind == "invoice"}
    exceptioned_payment_ids = {r.id for e in result.exceptions for r in e.records if r.kind == "payment"}
    exceptioned_settlement_ids = {r.id for e in result.exceptions for r in e.records if r.kind == "settlement"}
    exceptioned_bank_line_ids = {r.id for e in result.exceptions for r in e.records if r.kind == "bank_line"}

    assert matched_invoice_ids.isdisjoint(exceptioned_invoice_ids)
    assert matched_payment_ids.isdisjoint(exceptioned_payment_ids)
    assert matched_settlement_ids.isdisjoint(exceptioned_settlement_ids)
    assert matched_bank_line_ids.isdisjoint(exceptioned_bank_line_ids)

    total_invoice_amount = Paise(sum(i.amount for i in corpus.invoices))
    matched_invoice_amount = Paise(sum(index.invoices_by_id[i].amount for i in matched_invoice_ids))
    at_risk_invoice_amount = Paise(sum(index.invoices_by_id[i].amount for i in exceptioned_invoice_ids))
    assert matched_invoice_amount + at_risk_invoice_amount == total_invoice_amount

    total_payment_amount = Paise(sum(p.gross for p in corpus.payments))
    matched_payment_amount = Paise(sum(index.payments_by_id[p].gross for p in matched_payment_ids))
    at_risk_payment_amount = Paise(sum(index.payments_by_id[p].gross for p in exceptioned_payment_ids))
    assert matched_payment_amount + at_risk_payment_amount == total_payment_amount

    all_invoice_ids = {i.id for i in corpus.invoices}
    all_payment_ids = {p.id for p in corpus.payments}
    all_settlement_ids = {s.id for s in corpus.settlements}
    all_bank_line_ids = {b.id for b in corpus.bank_lines}
    assert matched_invoice_ids | exceptioned_invoice_ids == all_invoice_ids
    assert matched_payment_ids | exceptioned_payment_ids == all_payment_ids
    assert matched_settlement_ids | exceptioned_settlement_ids == all_settlement_ids
    assert matched_bank_line_ids | exceptioned_bank_line_ids == all_bank_line_ids


def test_every_exception_has_a_label_severity_and_action() -> None:
    corpus, _ = generate_corpus(1002, 150)
    result = match(corpus)
    assert result.exceptions, "expected at least one exception in a realistic corpus"
    for exception in result.exceptions:
        assert exception.severity in (1, 2, 3)
        assert exception.suggested_action
        assert exception.records
