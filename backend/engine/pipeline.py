import hashlib
import json
from dataclasses import dataclass
from typing import Any

from contracts.corpus import Corpus
from contracts.enums import ExceptionCode, PassId
from contracts.models import Exception_, MatchGroup, Payment
from contracts.money import Paise
from engine.exceptions import build_exception
from engine.index import build_index
from engine.passes import CONFIDENCE, p1_bank_line_to_settlement, p3_invoice_to_payment
from engine.verifier import MatchProposal, UsedRecordIds, verify
from money.result import Ok


@dataclass(frozen=True)
class MatchResult:
    groups: list[MatchGroup]
    exceptions: list[Exception_]
    output_hash: str


def match(corpus: Corpus) -> MatchResult:
    """Run P1-P3 to build proposals, verify every one, and typed-except everything else.

    verify() is the only path that creates a MatchGroup: a false match costs
    more than an open item, so anything that cannot be tied out exactly
    becomes an exception instead of a lower-confidence guess.
    """
    index = build_index(corpus)
    p1 = p1_bank_line_to_settlement(index)
    bank_line_by_settlement = {settlement_id: bank_line_id for settlement_id, bank_line_id, _ in p1.matched}
    p1_evidence_by_settlement = {settlement_id: evidence for settlement_id, _, evidence in p1.matched}

    exceptions: list[Exception_] = []
    for settlement_id, _candidates, code in p1.ambiguous:
        exceptions.append(build_exception("settlement", settlement_id, code, index, attempted=["P1"]))
    for settlement_id, _bank_line_id, code in p1.rejected:
        exceptions.append(build_exception("settlement", settlement_id, code, index, attempted=["P1"]))
    for settlement_id in p1.unmatched:
        exceptions.append(
            build_exception("settlement", settlement_id, ExceptionCode.MISSING_IN_BANK, index, attempted=["P1"])
        )

    groups: list[MatchGroup] = []
    used = UsedRecordIds()
    resolved_invoice_ids: set[str] = set()

    for settlement in index.settlements_by_id.values():
        bank_line_id = bank_line_by_settlement.get(settlement.id)
        if bank_line_id is None:
            continue  # already exceptioned above

        invoice_ids: list[str] = []
        pass_used = PassId.P2
        for payment_id in settlement.payment_ids:
            result = p3_invoice_to_payment(index, payment_id)
            if result is None:
                continue
            invoice_id, pass_id, _ = result
            if invoice_id not in invoice_ids:
                invoice_ids.append(invoice_id)
            if CONFIDENCE[pass_id] < CONFIDENCE[pass_used]:
                pass_used = pass_id

        proposal = MatchProposal(
            invoice_ids=invoice_ids,
            payment_ids=list(settlement.payment_ids),
            settlement_id=settlement.id,
            bank_line_id=bank_line_id,
            pass_id=pass_used,
            confidence=CONFIDENCE[pass_used],
            evidence=p1_evidence_by_settlement.get(settlement.id, []),
        )
        outcome = verify(proposal, index, used)
        if isinstance(outcome, Ok):
            group = outcome.value
            groups.append(group)
            used = used.with_group(group)
            resolved_invoice_ids.update(group.invoice_ids)
        else:
            exceptions.append(
                build_exception(
                    "settlement",
                    settlement.id,
                    ExceptionCode.AMT_MISMATCH_UNEXPLAINED,
                    index,
                    attempted=["P1", "P2", "P3"],
                )
            )

    for invoice_id in index.invoices_by_id:
        if invoice_id not in resolved_invoice_ids:
            exceptions.append(
                build_exception("invoice", invoice_id, ExceptionCode.MISSING_IN_BANK, index, attempted=["P3"])
            )
    settled_signatures = {
        (index.payments_by_id[pid].gross, index.payments_by_id[pid].invoice_ref) for pid in used.payment_ids
    }
    for payment_id in index.payments_by_id:
        if payment_id not in used.payment_ids:
            code = _orphan_payment_code(index.payments_by_id[payment_id], settled_signatures)
            exceptions.append(build_exception("payment", payment_id, code, index, attempted=["P2"]))
    for bank_line_id in index.bank_lines_by_id:
        if bank_line_id not in used.bank_line_ids:
            exceptions.append(
                build_exception("bank_line", bank_line_id, ExceptionCode.UNIDENTIFIED_CREDIT, index, attempted=["P1"])
            )

    return MatchResult(groups=groups, exceptions=exceptions, output_hash=hash_groups(groups))


def _orphan_payment_code(
    payment: Payment, settled_signatures: set[tuple[Paise, str | None]]
) -> ExceptionCode:
    """Why a payment no settlement claimed is sitting open.

    "Missing in ledger" is the honest answer only when nothing else explains
    it. A capture whose amount and invoice reference already appear inside a
    settled group is far more likely a double-post than a sale nobody
    recorded, and a refund or chargeback left dangling is its own named
    problem with its own remedy -- filing all three under one generic code
    tells the person reading the list to go and re-derive what the engine
    already knew.
    """
    if payment.invoice_ref and (payment.gross, payment.invoice_ref) in settled_signatures:
        return ExceptionCode.DUPLICATE_CANDIDATE
    if payment.status == "refunded":
        return ExceptionCode.REFUND_UNLINKED
    if payment.status == "disputed":
        return ExceptionCode.CHARGEBACK_UNLINKED
    return ExceptionCode.MISSING_IN_LEDGER


def hash_groups(groups: list[MatchGroup]) -> str:
    canonical = sorted(
        (
            group.id,
            tuple(sorted(group.invoice_ids)),
            tuple(sorted(group.payment_ids)),
            group.settlement_id,
            group.bank_line_id,
            group.status,
            group.pass_id.value,
            group.confidence,
            int(group.residual),
        )
        for group in groups
    )
    payload = json.dumps(canonical, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def serialize_match_result(result: MatchResult) -> str:
    """The persisted-run-row shape: how a MatchResult round-trips through
    Run.result_json. Lives here (not in workers/) so any consumer -- the
    worker that writes it, the ask agent's tools that read it back -- can
    import it without workers depending on llm or vice versa.
    """
    return json.dumps(
        {
            "groups": [g.model_dump(mode="json") for g in result.groups],
            "exceptions": [e.model_dump(mode="json") for e in result.exceptions],
            "output_hash": result.output_hash,
        }
    )


def deserialize_match_result(raw: str) -> MatchResult:
    payload: dict[str, Any] = json.loads(raw)
    return MatchResult(
        groups=[MatchGroup.model_validate(g) for g in payload["groups"]],
        exceptions=[Exception_.model_validate(e) for e in payload["exceptions"]],
        output_hash=payload["output_hash"],
    )
