import hashlib
import json
from dataclasses import dataclass

from contracts.corpus import Corpus
from contracts.enums import ExceptionCode, PassId
from contracts.models import MatchGroup
from contracts.money import Paise
from engine.index import build_index
from engine.passes import CONFIDENCE, p1_bank_line_to_settlement, p2_verify_batch_algebra, p3_invoice_to_payment


@dataclass(frozen=True)
class ResidueItem:
    kind: str
    id: str
    code: ExceptionCode
    note: str


@dataclass(frozen=True)
class MatchResult:
    groups: list[MatchGroup]
    residue: list[ResidueItem]
    output_hash: str


def match(corpus: Corpus) -> MatchResult:
    """Run P1-P3 over the corpus and assemble verified MatchGroups.

    Anything that cannot be tied out exactly becomes a residue item rather
    than a lower-confidence match: a false match costs more than an open item.
    """
    index = build_index(corpus)
    p1 = p1_bank_line_to_settlement(index)
    bank_line_by_settlement = {settlement_id: bank_line_id for settlement_id, bank_line_id, _ in p1.matched}
    p1_evidence_by_settlement = {settlement_id: evidence for settlement_id, _, evidence in p1.matched}

    residue: list[ResidueItem] = []
    for settlement_id, candidates, code in p1.ambiguous:
        residue.append(ResidueItem("settlement", settlement_id, code, f"tied bank lines: {candidates}"))
    for settlement_id in p1.unmatched:
        residue.append(
            ResidueItem(
                "settlement", settlement_id, ExceptionCode.MISSING_IN_BANK, "no bank credit found for UTR/amount"
            )
        )

    groups: list[MatchGroup] = []
    resolved_invoice_ids: set[str] = set()
    resolved_payment_ids: set[str] = set()
    resolved_bank_line_ids: set[str] = set()

    for settlement in index.settlements_by_id.values():
        bank_line_id = bank_line_by_settlement.get(settlement.id)
        if bank_line_id is None:
            continue

        ties_out, batch_residual = p2_verify_batch_algebra(index, settlement.id)
        if not ties_out:
            residue.append(
                ResidueItem(
                    "settlement",
                    settlement.id,
                    ExceptionCode.AMT_MISMATCH_UNEXPLAINED,
                    f"batch algebra residual {batch_residual} paise",
                )
            )
            continue

        invoice_ids: list[str] = []
        pass_used = PassId.P2
        for payment_id in settlement.payment_ids:
            result = p3_invoice_to_payment(index, payment_id)
            if result is None:
                continue
            invoice_id, pass_id, _ = result
            if invoice_id not in invoice_ids:
                invoice_ids.append(invoice_id)
            resolved_invoice_ids.add(invoice_id)
            if CONFIDENCE[pass_id] < CONFIDENCE[pass_used]:
                pass_used = pass_id

        group = MatchGroup(
            id=settlement.id,
            invoice_ids=invoice_ids,
            payment_ids=list(settlement.payment_ids),
            settlement_id=settlement.id,
            bank_line_id=bank_line_id,
            status="auto",
            pass_id=pass_used,
            confidence=CONFIDENCE[pass_used],
            residual=Paise(0),
            evidence=p1_evidence_by_settlement.get(settlement.id, []),
        )
        groups.append(group)
        resolved_bank_line_ids.add(bank_line_id)
        resolved_payment_ids.update(settlement.payment_ids)

    for invoice_id in index.invoices_by_id:
        if invoice_id not in resolved_invoice_ids:
            residue.append(
                ResidueItem(
                    "invoice", invoice_id, ExceptionCode.MISSING_IN_BANK, "no settled payment resolved to this invoice"
                )
            )
    for payment_id in index.payments_by_id:
        if payment_id not in resolved_payment_ids:
            residue.append(
                ResidueItem(
                    "payment", payment_id, ExceptionCode.MISSING_IN_LEDGER, "not part of any verified settlement batch"
                )
            )
    for bank_line_id in index.bank_lines_by_id:
        if bank_line_id not in resolved_bank_line_ids:
            residue.append(
                ResidueItem("bank_line", bank_line_id, ExceptionCode.UNIDENTIFIED_CREDIT, "no settlement tie found")
            )

    return MatchResult(groups=groups, residue=residue, output_hash=_hash_groups(groups))


def _hash_groups(groups: list[MatchGroup]) -> str:
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
