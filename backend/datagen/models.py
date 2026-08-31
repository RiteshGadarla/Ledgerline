from dataclasses import dataclass, field

from contracts.models import BankLine, Invoice, Payment, Settlement
from datagen.difficulty import DifficultyClass

UNMATCHABLE = "unmatchable"


@dataclass(frozen=True)
class Corpus:
    invoices: list[Invoice]
    payments: list[Payment]
    settlements: list[Settlement]
    bank_lines: list[BankLine]


@dataclass(frozen=True)
class TruthGroup:
    id: str
    invoice_ids: list[str]
    payment_ids: list[str]
    settlement_id: str | None
    bank_line_id: str | None


@dataclass(frozen=True)
class Truth:
    groups: dict[str, TruthGroup] = field(default_factory=dict)
    # "{kind}:{id}" -> group id, or UNMATCHABLE
    record_group: dict[str, str] = field(default_factory=dict)
    # "{kind}:{id}" -> the difficulty class that record was generated to exercise
    record_difficulty: dict[str, DifficultyClass] = field(default_factory=dict)

    def class_counts(self) -> dict[DifficultyClass, int]:
        counts: dict[DifficultyClass, int] = {}
        for key, difficulty in self.record_difficulty.items():
            kind = key.split(":", 1)[0]
            if kind != "invoice" and not (kind == "bank_line" and difficulty == DifficultyClass.UNRELATED_CREDIT):
                continue
            counts[difficulty] = counts.get(difficulty, 0) + 1
        return counts
