from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from contracts.enums import ExceptionCode, PassId
from contracts.models import Evidence
from contracts.money import Paise
from engine.index import CorpusIndex
from money.business_days import add_business_days

CONFIDENCE = {
    PassId.P1: 1.00,
    PassId.P2: 0.99,
    PassId.P3: 0.95,
    PassId.P4: 0.90,
}

PAYER_SIMILARITY_THRESHOLD = 0.88
_FEE_BAND_BPS = 300  # payment gross may differ from invoice amount by up to 3% and still be "in band"
_DATE_WINDOW_DAYS = 5
# How far a bank credit may sit from the settlement date it claims to be. A
# real credit posts on the settlement date or a day or two after it; one that
# is weeks adrift is a timing exception even when the UTR and the amount agree,
# because those two alone cannot tell a late credit from a coincidence.
_BANK_CREDIT_WINDOW_DAYS = 5
_SUBSET_CANDIDATE_CAP = 8
_SUBSET_NODE_LIMIT = 5000


@dataclass(frozen=True)
class LinkResult:
    matched: list[tuple[str, str, list[Evidence]]]  # (left_id, right_id, evidence)
    ambiguous: list[tuple[str, list[str], ExceptionCode]]  # (left_id, tied_right_ids, code)
    unmatched: list[str]
    # A single candidate that failed a check -- distinct from ambiguity, where
    # the problem is too many candidates rather than one that does not hold up.
    rejected: list[tuple[str, str, ExceptionCode]] = field(default_factory=list)


def p1_bank_line_to_settlement(index: CorpusIndex) -> LinkResult:
    """Narration UTR equals settlement UTR and amount equal. Confidence 1.00.

    A unique candidate must also have landed near the settlement date. UTR plus
    an exact amount is strong evidence, but it is not a licence to ignore a
    credit that posted six weeks late -- that is a real reconciliation break,
    and tying it silently would hide it.
    """
    matched: list[tuple[str, str, list[Evidence]]] = []
    ambiguous: list[tuple[str, list[str], ExceptionCode]] = []
    rejected: list[tuple[str, str, ExceptionCode]] = []
    unmatched: list[str] = []

    for settlement in index.settlements_by_id.values():
        if not settlement.utr:
            unmatched.append(settlement.id)
            continue
        candidates = [
            bank_line_id
            for bank_line_id in index.bank_lines_by_utr.get(settlement.utr, [])
            if index.bank_lines_by_id[bank_line_id].credit == settlement.payout
        ]
        if len(candidates) == 1:
            bank_line_id = candidates[0]
            bank_line = index.bank_lines_by_id[bank_line_id]
            if abs((bank_line.value_date - settlement.settled_at).days) > _BANK_CREDIT_WINDOW_DAYS:
                rejected.append((settlement.id, bank_line_id, ExceptionCode.DATE_OUTSIDE_WINDOW))
                continue
            evidence = [
                Evidence(field="utr", value=settlement.utr, source_id=settlement.id),
                Evidence(field="credit", value=str(settlement.payout), source_id=bank_line_id),
            ]
            matched.append((settlement.id, bank_line_id, evidence))
        elif len(candidates) > 1:
            ambiguous.append((settlement.id, candidates, ExceptionCode.AMBIGUOUS_MULTI_CANDIDATE))
        else:
            unmatched.append(settlement.id)

    return LinkResult(matched=matched, ambiguous=ambiguous, unmatched=unmatched, rejected=rejected)


def p2_verify_batch_algebra(index: CorpusIndex, settlement_id: str) -> tuple[bool, Paise]:
    """Recompute payout from member payments in integer paise. Returns (ties_out, residual)."""
    settlement = index.settlements_by_id[settlement_id]
    members = [index.payments_by_id[pid] for pid in settlement.payment_ids]
    gross_total = Paise(sum(p.gross for p in members))
    refunds_total = Paise(sum(p.gross for p in members if p.status in ("refunded", "disputed")))
    expected_payout = Paise(
        gross_total - settlement.fees - settlement.tax - refunds_total + settlement.adjustments
    )
    residual = Paise(settlement.payout - expected_payout)
    return residual == 0, residual


def p3_invoice_to_payment(index: CorpusIndex, payment_id: str) -> tuple[str, PassId, float] | None:
    """Exact ref token match, else a unique amount+date-window fallback.

    The frozen Payment contract carries no payer-identifying field, so the
    payer-similarity comparison described in the spec (see `payer_similarity`
    below) cannot be wired into this decision live; the fallback below relies
    on amount and date proximity only, which are real fields.
    """
    payment = index.payments_by_id[payment_id]
    if payment.invoice_ref:
        candidates = index.invoices_by_ref.get(payment.invoice_ref, [])
        if len(candidates) == 1:
            return candidates[0], PassId.P3, CONFIDENCE[PassId.P3]
        if len(candidates) > 1:
            return None

    tied: list[str] = []
    for invoice in index.invoices_by_id.values():
        if not _within_fee_band(invoice.amount, payment.gross):
            continue
        if not _within_date_window(invoice.issued_at, payment.captured_at.date()):
            continue
        tied.append(invoice.id)

    if len(tied) == 1:
        return tied[0], PassId.P3, CONFIDENCE[PassId.P3]
    return None


def payer_similarity(a: str, b: str) -> float:
    """Fuzzy name similarity in [0, 1], for use once a real payer field exists upstream."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _within_fee_band(invoice_amount: Paise, payment_gross: Paise) -> bool:
    if invoice_amount == 0:
        return payment_gross == 0
    delta_bps = abs(invoice_amount - payment_gross) * 10000 // invoice_amount
    return delta_bps <= _FEE_BAND_BPS


def _within_date_window(issued_at: date, captured_at: date, window_days: int = _DATE_WINDOW_DAYS) -> bool:
    lower = add_business_days(issued_at, -window_days)
    upper = add_business_days(issued_at, window_days)
    return lower <= captured_at <= upper


def p4_subset_sum(
    candidate_payment_ids: list[str],
    index: CorpusIndex,
    target: Paise,
) -> tuple[list[str] | None, bool]:
    """Bounded subset-sum over candidates. Returns (matching subset or None, node_limit_exceeded)."""
    candidates = candidate_payment_ids[:_SUBSET_CANDIDATE_CAP]
    amounts = [index.payments_by_id[pid].gross for pid in candidates]

    nodes = 0
    solutions: list[tuple[int, ...]] = []

    def search(i: int, remaining: Paise, chosen: tuple[int, ...]) -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > _SUBSET_NODE_LIMIT:
            return True  # signal: limit exceeded
        if remaining == 0 and chosen:
            solutions.append(chosen)
            return False
        if i >= len(amounts) or remaining < 0:
            return False
        if search(i + 1, Paise(remaining - amounts[i]), (*chosen, i)):
            return True
        return search(i + 1, remaining, chosen)

    exceeded = search(0, target, ())
    if exceeded:
        return None, True
    if len(solutions) == 1:
        return [candidates[i] for i in solutions[0]], False
    return None, False
