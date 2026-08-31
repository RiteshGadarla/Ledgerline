from contracts.enums import EXCEPTION_METADATA, ExceptionCode
from contracts.models import Exception_, RecordRef
from contracts.money import Paise
from engine.index import CorpusIndex


def amount_at_risk(kind: str, record_id: str, index: CorpusIndex) -> Paise:
    if kind == "invoice":
        return index.invoices_by_id[record_id].amount
    if kind == "payment":
        return index.payments_by_id[record_id].gross
    if kind == "settlement":
        return index.settlements_by_id[record_id].payout
    if kind == "bank_line":
        return index.bank_lines_by_id[record_id].credit
    raise ValueError(f"unknown record kind: {kind}")


def build_exception(
    kind: str,
    record_id: str,
    code: ExceptionCode,
    index: CorpusIndex,
    attempted: list[str],
) -> Exception_:
    meta = EXCEPTION_METADATA[code]
    return Exception_(
        id=f"EXC-{kind}-{record_id}",
        code=code,
        severity=meta.severity,  # type: ignore[arg-type]
        amount_at_risk=amount_at_risk(kind, record_id, index),
        records=[RecordRef(kind=kind, id=record_id)],  # type: ignore[arg-type]
        attempted=attempted,
        explanation=None,
        suggested_action=meta.suggested_action,
        rejected_proposal=None,
    )
