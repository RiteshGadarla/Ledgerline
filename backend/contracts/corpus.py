from dataclasses import dataclass

from contracts.models import BankLine, Invoice, Payment, Settlement


@dataclass(frozen=True)
class Corpus:
    invoices: list[Invoice]
    payments: list[Payment]
    settlements: list[Settlement]
    bank_lines: list[BankLine]
