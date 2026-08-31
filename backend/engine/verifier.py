from dataclasses import dataclass, field

from contracts.enums import PassId
from contracts.models import Evidence, MatchGroup
from contracts.money import Paise
from engine.index import CorpusIndex
from engine.passes import p2_verify_batch_algebra
from money.result import Err, Ok, Result


@dataclass(frozen=True)
class MatchProposal:
    invoice_ids: list[str]
    payment_ids: list[str]
    settlement_id: str | None
    bank_line_id: str | None
    pass_id: PassId
    confidence: float
    evidence: list[Evidence]


@dataclass(frozen=True)
class VerificationFailure:
    reason: str


@dataclass(frozen=True)
class UsedRecordIds:
    invoice_ids: frozenset[str] = field(default_factory=frozenset)
    payment_ids: frozenset[str] = field(default_factory=frozenset)
    settlement_ids: frozenset[str] = field(default_factory=frozenset)
    bank_line_ids: frozenset[str] = field(default_factory=frozenset)

    def with_group(self, group: MatchGroup) -> "UsedRecordIds":
        return UsedRecordIds(
            invoice_ids=self.invoice_ids | frozenset(group.invoice_ids),
            payment_ids=self.payment_ids | frozenset(group.payment_ids),
            settlement_ids=self.settlement_ids | frozenset({group.settlement_id} if group.settlement_id else set()),
            bank_line_ids=self.bank_line_ids | frozenset({group.bank_line_id} if group.bank_line_id else set()),
        )


def verify(
    proposal: MatchProposal, index: CorpusIndex, used: UsedRecordIds | None = None
) -> Result[MatchGroup]:
    """The only path that creates a MatchGroup. Recomputes every implied sum in ints.

    Both the deterministic engine and the future LLM-triage layer call this;
    there is no second path that writes a match.
    """
    used = used or UsedRecordIds()

    for invoice_id in proposal.invoice_ids:
        if invoice_id not in index.invoices_by_id:
            return Err(f"unknown invoice id: {invoice_id}")
        if invoice_id in used.invoice_ids:
            return Err(f"invoice {invoice_id} already claimed by another group")
    for payment_id in proposal.payment_ids:
        if payment_id not in index.payments_by_id:
            return Err(f"unknown payment id: {payment_id}")
        if payment_id in used.payment_ids:
            return Err(f"payment {payment_id} already claimed by another group")
    if proposal.settlement_id is not None:
        if proposal.settlement_id not in index.settlements_by_id:
            return Err(f"unknown settlement id: {proposal.settlement_id}")
        if proposal.settlement_id in used.settlement_ids:
            return Err(f"settlement {proposal.settlement_id} already claimed by another group")
    if proposal.bank_line_id is not None:
        if proposal.bank_line_id not in index.bank_lines_by_id:
            return Err(f"unknown bank_line id: {proposal.bank_line_id}")
        if proposal.bank_line_id in used.bank_line_ids:
            return Err(f"bank_line {proposal.bank_line_id} already claimed by another group")

    if proposal.settlement_id is not None:
        settlement = index.settlements_by_id[proposal.settlement_id]
        if set(settlement.payment_ids) != set(proposal.payment_ids):
            return Err("proposed payment set does not match settlement membership")

        ties_out, residual = p2_verify_batch_algebra(index, proposal.settlement_id)
        if not ties_out:
            return Err(f"batch algebra does not tie out: residual {residual} paise")

        if proposal.bank_line_id is not None:
            bank_line = index.bank_lines_by_id[proposal.bank_line_id]
            if bank_line.credit != settlement.payout:
                return Err(
                    f"bank credit {bank_line.credit} does not equal settlement payout {settlement.payout}"
                )

    group_id = proposal.settlement_id or proposal.bank_line_id or "-".join(sorted(proposal.payment_ids))
    return Ok(
        MatchGroup(
            id=group_id,
            invoice_ids=proposal.invoice_ids,
            payment_ids=proposal.payment_ids,
            settlement_id=proposal.settlement_id,
            bank_line_id=proposal.bank_line_id,
            status="auto",
            pass_id=proposal.pass_id,
            confidence=proposal.confidence,
            residual=Paise(0),
            evidence=proposal.evidence,
        )
    )
