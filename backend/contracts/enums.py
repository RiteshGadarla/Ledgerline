from dataclasses import dataclass
from enum import StrEnum


class PassId(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"
    LLM = "LLM"


class MutationKind(StrEnum):
    """The adversarial corruptions a corpus can be put through.

    Each one models a way real books go wrong -- a payment posted twice, a
    payout the bank never credited, a narration the bank's own formatter
    mangled -- rather than random noise, because a corruption that could not
    happen in production proves nothing about a reconciler that survives it.
    """

    DUPLICATE_PAYMENT = "duplicate_payment"
    SHIFT_DATE = "shift_date"
    ALTER_AMOUNT = "alter_amount"
    DELETE_BANK_LINE = "delete_bank_line"
    INJECT_UNRELATED_CREDIT = "inject_unrelated_credit"
    SCRAMBLE_NARRATION = "scramble_narration"
    SPLIT_PAYMENT = "split_payment"


class ExceptionCode(StrEnum):
    AMT_MISMATCH_UNEXPLAINED = "AMT_MISMATCH_UNEXPLAINED"
    FEE_GST_DELTA_UNCONFIRMED = "FEE_GST_DELTA_UNCONFIRMED"
    DATE_OUTSIDE_WINDOW = "DATE_OUTSIDE_WINDOW"
    MISSING_IN_BANK = "MISSING_IN_BANK"
    MISSING_IN_LEDGER = "MISSING_IN_LEDGER"
    DUPLICATE_CANDIDATE = "DUPLICATE_CANDIDATE"
    PARTIAL_SETTLEMENT_OPEN = "PARTIAL_SETTLEMENT_OPEN"
    REFUND_UNLINKED = "REFUND_UNLINKED"
    CHARGEBACK_UNLINKED = "CHARGEBACK_UNLINKED"
    UNIDENTIFIED_CREDIT = "UNIDENTIFIED_CREDIT"
    AMBIGUOUS_MULTI_CANDIDATE = "AMBIGUOUS_MULTI_CANDIDATE"
    LLM_PROPOSAL_FAILED_VERIFY = "LLM_PROPOSAL_FAILED_VERIFY"
    SOURCE_PARSE_INCOMPLETE = "SOURCE_PARSE_INCOMPLETE"


@dataclass(frozen=True)
class ExceptionMeta:
    label: str
    severity: int
    suggested_action: str


EXCEPTION_METADATA: dict[ExceptionCode, ExceptionMeta] = {
    ExceptionCode.AMT_MISMATCH_UNEXPLAINED: ExceptionMeta(
        label="Amount mismatch, unexplained",
        severity=1,
        suggested_action="Compare the ledger and gateway amounts line by line and confirm the delta with finance.",
    ),
    ExceptionCode.FEE_GST_DELTA_UNCONFIRMED: ExceptionMeta(
        label="Fee/GST delta unconfirmed",
        severity=2,
        suggested_action="Recompute fee and GST at the current rate card and confirm against the gateway invoice.",
    ),
    ExceptionCode.DATE_OUTSIDE_WINDOW: ExceptionMeta(
        label="Date outside expected window",
        severity=2,
        suggested_action="Check for a settlement delay or holiday shift and extend the matching window if legitimate.",
    ),
    ExceptionCode.MISSING_IN_BANK: ExceptionMeta(
        label="Missing in bank statement",
        severity=1,
        suggested_action="Confirm the payout was initiated and check for a delayed or failed bank credit.",
    ),
    ExceptionCode.MISSING_IN_LEDGER: ExceptionMeta(
        label="Missing in ledger",
        severity=1,
        suggested_action="Search the ledger for an unlinked invoice or raise one if the sale was never recorded.",
    ),
    ExceptionCode.DUPLICATE_CANDIDATE: ExceptionMeta(
        label="Duplicate candidate",
        severity=2,
        suggested_action="Confirm which record is the duplicate and void or merge it.",
    ),
    ExceptionCode.PARTIAL_SETTLEMENT_OPEN: ExceptionMeta(
        label="Partial settlement still open",
        severity=2,
        suggested_action="Wait for the remaining settlement batch or confirm a short payout with the gateway.",
    ),
    ExceptionCode.REFUND_UNLINKED: ExceptionMeta(
        label="Refund unlinked to original payment",
        severity=2,
        suggested_action="Locate the original captured payment for this refund and link the two records.",
    ),
    ExceptionCode.CHARGEBACK_UNLINKED: ExceptionMeta(
        label="Chargeback unlinked to original payment",
        severity=1,
        suggested_action="Locate the original captured payment for this chargeback and escalate to disputes.",
    ),
    ExceptionCode.UNIDENTIFIED_CREDIT: ExceptionMeta(
        label="Unidentified bank credit",
        severity=2,
        suggested_action="Trace the narration and UTR against all open sources before treating it as unrelated income.",
    ),
    ExceptionCode.AMBIGUOUS_MULTI_CANDIDATE: ExceptionMeta(
        label="Ambiguous, multiple tied candidates",
        severity=2,
        suggested_action="Review the tied candidates manually and pick the correct match by evidence.",
    ),
    ExceptionCode.LLM_PROPOSAL_FAILED_VERIFY: ExceptionMeta(
        label="Proposed match failed verification",
        severity=2,
        suggested_action="Review the rejected proposal and the failed check, then resolve manually.",
    ),
    ExceptionCode.SOURCE_PARSE_INCOMPLETE: ExceptionMeta(
        label="Source file parsed incompletely",
        severity=3,
        suggested_action="Review the skipped rows and re-upload a corrected file if needed.",
    ),
}
