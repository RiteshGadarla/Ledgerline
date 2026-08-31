from collections import defaultdict
from dataclasses import dataclass, field

from contracts.corpus import Corpus
from contracts.models import BankLine, Invoice, Payment, Settlement
from money.narration import extract as extract_narration


@dataclass(frozen=True)
class CorpusIndex:
    invoices_by_id: dict[str, Invoice]
    payments_by_id: dict[str, Payment]
    settlements_by_id: dict[str, Settlement]
    bank_lines_by_id: dict[str, BankLine]
    invoices_by_ref: dict[str, list[str]] = field(default_factory=dict)
    bank_lines_by_utr: dict[str, list[str]] = field(default_factory=dict)


def build_index(corpus: Corpus) -> CorpusIndex:
    invoices_by_ref: dict[str, list[str]] = defaultdict(list)
    for invoice in corpus.invoices:
        if invoice.ref:
            invoices_by_ref[invoice.ref].append(invoice.id)

    bank_lines_by_utr: dict[str, list[str]] = defaultdict(list)
    for bank_line in corpus.bank_lines:
        for utr in extract_narration(bank_line.narration).utrs:
            bank_lines_by_utr[utr].append(bank_line.id)

    return CorpusIndex(
        invoices_by_id={i.id: i for i in corpus.invoices},
        payments_by_id={p.id: p for p in corpus.payments},
        settlements_by_id={s.id: s for s in corpus.settlements},
        bank_lines_by_id={b.id: b for b in corpus.bank_lines},
        invoices_by_ref=dict(invoices_by_ref),
        bank_lines_by_utr=dict(bank_lines_by_utr),
    )
