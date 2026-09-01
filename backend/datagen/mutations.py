"""The adversarial mutation engine.

Corrupt a corpus the way real books get corrupted -- a payment posted twice, a
payout the bank never credited, a narration the bank's own formatter mangled --
and keep the truth file correct while doing it. Truth surviving the sabotage is
the entire point: it is what lets accuracy still be *measured* on a corrupted
corpus, which is the difference between an adversarial test and a demo that
merely breaks itself.

The rule that keeps truth honest is applied uniformly:

    Truth records which records belong together. A mutation that ADDS or
    REMOVES a record edits truth. A mutation that corrupts a value IN PLACE
    does not.

A scrambled narration does not change which settlement that money was; it only
hides it from the matcher. Marking it unmatchable in truth would be scoring the
engine against a lie, and the score would flatter us -- the engine would get
credit for leaving open something truth had already conceded.

Pure, like the rest of `datagen`: no I/O, no clock, and randomness only from
the seed the caller passes, so a run's recorded mutation list replays exactly.
"""

import random
import string
from dataclasses import dataclass, replace
from datetime import timedelta

from contracts.corpus import Corpus
from contracts.enums import MutationKind
from contracts.models import BankLine, Invoice, Payment, Settlement
from contracts.money import Paise
from datagen.difficulty import DifficultyClass
from datagen.models import UNMATCHABLE, Truth, TruthGroup
from money.narration import extract as extract_narration
from money.result import Err, Ok, Result

# Defaults chosen to be unmistakable rather than marginal: a mutation whose
# effect sits inside a tolerance proves nothing, and one that lands a hair
# outside it is a test of the tolerance, not of the engine.
DEFAULT_SHIFT_DAYS = 45
DEFAULT_AMOUNT_DELTA = Paise(-1_500_00)

_TOKEN_ALPHABET = string.ascii_uppercase + string.digits

_UNRELATED_NARRATIONS = [
    "IMPS INWARD REMITTANCE FROM SUNDRY DEBTOR REF {token}",
    "NEFT CR MISC RECEIPT NOT IDENTIFIED {token}",
    "RTGS INWARD {token} PURPOSE UNSPECIFIED",
    "UPI CR REVERSAL SUSPENSE {token}",
]


@dataclass(frozen=True)
class MutationSpec:
    """One corruption to apply. `amount` is paise and `days` is calendar days;
    each is read only by the kinds that take an argument."""

    kind: MutationKind
    amount: Paise = DEFAULT_AMOUNT_DELTA
    days: int = DEFAULT_SHIFT_DAYS


def parse_mutation(raw: str) -> Result[MutationSpec]:
    """`"shift_date"`, `"shift_date:60"`, `"alter_amount:-250000"`.

    Never raises: a run request carries these as free strings from the client,
    so a bad one has to come back as a typed error rather than a 500.
    """
    text = raw.strip()
    if not text:
        return Err("mutation is empty")

    kind_text, _, argument = text.partition(":")
    try:
        kind = MutationKind(kind_text.strip().lower())
    except ValueError:
        known = ", ".join(k.value for k in MutationKind)
        return Err(f"unknown mutation {kind_text.strip()!r}; expected one of: {known}")

    if not argument.strip():
        return Ok(MutationSpec(kind=kind))

    try:
        value = int(argument.strip())
    except ValueError:
        return Err(f"{kind.value}: argument {argument.strip()!r} is not a whole number")

    if kind == MutationKind.SHIFT_DATE:
        return Ok(MutationSpec(kind=kind, days=value))
    if kind == MutationKind.ALTER_AMOUNT:
        if value == 0:
            return Err("alter_amount: a delta of 0 would corrupt nothing")
        return Ok(MutationSpec(kind=kind, amount=Paise(value)))
    return Err(f"{kind.value} takes no argument")


def format_mutation(spec: MutationSpec) -> str:
    """The inverse of `parse_mutation`, so a run row round-trips to the exact
    corruption it was given."""
    if spec.kind == MutationKind.SHIFT_DATE:
        return f"{spec.kind.value}:{spec.days}"
    if spec.kind == MutationKind.ALTER_AMOUNT:
        return f"{spec.kind.value}:{int(spec.amount)}"
    return spec.kind.value


@dataclass
class _Working:
    """A mutable corpus plus its truth, so a chain of mutations composes
    without each one rebuilding frozen containers."""

    invoices: list[Invoice]
    payments: list[Payment]
    settlements: list[Settlement]
    bank_lines: list[BankLine]
    groups: dict[str, TruthGroup]
    record_group: dict[str, str]
    record_difficulty: dict[str, DifficultyClass]
    rng: random.Random
    counter: int = 0

    def next_token(self, length: int = 10) -> str:
        self.counter += 1
        return "".join(self.rng.choices(_TOKEN_ALPHABET, k=length))

    def group_of(self, key: str) -> TruthGroup | None:
        group_id = self.record_group.get(key)
        if group_id is None or group_id == UNMATCHABLE:
            return None
        return self.groups.get(group_id)


def _settled_payment_ids(work: _Working) -> list[str]:
    """Payments that a settlement actually claims, in a stable order. Anything
    already orphaned is a poor target -- corrupting it changes no outcome."""
    claimed = {pid for settlement in work.settlements for pid in settlement.payment_ids}
    return sorted(pid for pid in claimed if any(p.id == pid for p in work.payments))


def _grouped_bank_line_ids(work: _Working) -> list[str]:
    """Bank lines truth says belong to a settlement, so a mutation on one has
    a real answer to be wrong about."""
    return sorted(
        line.id
        for line in work.bank_lines
        if work.record_group.get(f"bank_line:{line.id}", UNMATCHABLE) != UNMATCHABLE
    )


def _pick(work: _Working, candidates: list[str]) -> str | None:
    return work.rng.choice(candidates) if candidates else None


def _replace_payment(work: _Working, payment_id: str, updated: Payment) -> None:
    work.payments = [updated if p.id == payment_id else p for p in work.payments]


def _replace_bank_line(work: _Working, bank_line_id: str, updated: BankLine) -> None:
    work.bank_lines = [updated if b.id == bank_line_id else b for b in work.bank_lines]


# --------------------------------------------------------------- mutations


def _duplicate_payment(work: _Working) -> None:
    """The same capture posted twice. The copy carries a fresh gateway id and
    no settlement, which is exactly how a double-post looks in a real export.

    Truth gains a record, so truth is edited: the copy is unmatchable, because
    there is no second sale for it to belong to.
    """
    payment_id = _pick(work, _settled_payment_ids(work))
    if payment_id is None:
        return
    original = next(p for p in work.payments if p.id == payment_id)
    copy = original.model_copy(
        update={"id": f"pay_{work.next_token(14)}", "settlement_id": None}
    )
    work.payments.append(copy)
    work.record_group[f"payment:{copy.id}"] = UNMATCHABLE
    work.record_difficulty[f"payment:{copy.id}"] = DifficultyClass.DUPLICATE_PAYMENT


def _shift_date(work: _Working, days: int) -> None:
    """The credit lands, but weeks off its settlement date. UTR and amount
    still agree, so only a date check can catch it -- which is the point.

    Corruption in place: the money is still that settlement's money, so truth
    does not move.
    """
    bank_line_id = _pick(work, _grouped_bank_line_ids(work))
    if bank_line_id is None:
        return
    line = next(b for b in work.bank_lines if b.id == bank_line_id)
    _replace_bank_line(
        work, bank_line_id, line.model_copy(update={"value_date": line.value_date + timedelta(days=days)})
    )


def _alter_amount(work: _Working, delta: Paise) -> None:
    """A gross amount edited after the fact, leaving the batch algebra short.
    Fee and tax are left alone deliberately: a corrected gross that also
    recomputed its own fee would still tie out, and tie-outs prove nothing.

    Corruption in place: truth does not move.
    """
    payment_id = _pick(work, _settled_payment_ids(work))
    if payment_id is None:
        return
    payment = next(p for p in work.payments if p.id == payment_id)
    altered = Paise(max(0, payment.gross + delta))
    if altered == payment.gross:
        return
    _replace_payment(work, payment_id, payment.model_copy(update={"gross": altered}))


def _delete_bank_line(work: _Working) -> None:
    """The payout was initiated and never credited.

    Truth loses a record, so truth is edited: the group keeps its invoices,
    payments and settlement -- they are still one commercial event -- but it no
    longer has a bank line, and nothing should ever tie one to it again.
    """
    bank_line_id = _pick(work, _grouped_bank_line_ids(work))
    if bank_line_id is None:
        return
    work.bank_lines = [b for b in work.bank_lines if b.id != bank_line_id]
    key = f"bank_line:{bank_line_id}"
    group = work.group_of(key)
    work.record_group.pop(key, None)
    work.record_difficulty.pop(key, None)
    if group is not None:
        work.groups[group.id] = replace(group, bank_line_id=None)


def _inject_unrelated_credit(work: _Working) -> None:
    """Money arrives that belongs to nobody in these books.

    Truth gains a record: unmatchable, and leaving it open is the correct
    answer rather than a failure to match.
    """
    latest = max((b.value_date for b in work.bank_lines), default=None)
    if latest is None:
        return
    credit = Paise(work.rng.randrange(25_00, 9_50_000, 25))
    balance = Paise(max((b.balance for b in work.bank_lines), default=Paise(0)) + credit)
    template = work.rng.choice(_UNRELATED_NARRATIONS)
    line = BankLine(
        id=f"BNKX{work.next_token(6)}",
        value_date=latest + timedelta(days=work.rng.randint(1, 4)),
        narration=template.format(token=work.next_token(8)),
        credit=credit,
        debit=Paise(0),
        balance=balance,
    )
    work.bank_lines.append(line)
    work.record_group[f"bank_line:{line.id}"] = UNMATCHABLE
    work.record_difficulty[f"bank_line:{line.id}"] = DifficultyClass.UNRELATED_CREDIT


def _scramble_narration(work: _Working) -> None:
    """The bank's formatter eats the reference. The credit is still the right
    credit for the right amount on the right day -- it just no longer says so.

    Corruption in place: truth does not move. The engine ought to leave this
    open rather than guess, and truth insisting the link exists is what makes
    that a measurable miss instead of a free pass.
    """
    candidates = sorted(
        line.id for line in work.bank_lines if extract_narration(line.narration).utrs
    )
    bank_line_id = _pick(work, candidates)
    if bank_line_id is None:
        return
    line = next(b for b in work.bank_lines if b.id == bank_line_id)
    scrambled = line.narration
    for utr in extract_narration(line.narration).utrs:
        scrambled = scrambled.replace(utr, "REF*UNREADABLE*")
    scrambled = f"{scrambled} /CHQ RTN ADV/ **"
    _replace_bank_line(work, bank_line_id, line.model_copy(update={"narration": scrambled}))
    work.record_difficulty[f"bank_line:{bank_line_id}"] = DifficultyClass.NARRATION_MISSING_UTR


def _split_payment(work: _Working) -> None:
    """One capture settles across two batches: 60% now, 40% still to come. The
    settlement keeps only the first half, so its payout no longer reconstructs.

    Truth is edited, because records were added and removed -- but both halves
    stay in the group they came from. That is the honest answer: the money is
    still owed against the same invoice, and an engine that can only close the
    settled half has genuinely not closed the group.
    """
    payment_id = _pick(work, _settled_payment_ids(work))
    if payment_id is None:
        return
    payment = next(p for p in work.payments if p.id == payment_id)
    if payment.gross < 2:
        return

    first_gross = Paise(payment.gross * 6 // 10)
    second_gross = Paise(payment.gross - first_gross)
    first_fee = Paise(payment.fee * 6 // 10)
    first_tax = Paise(payment.tax * 6 // 10)
    first = payment.model_copy(
        update={
            "id": f"pay_{work.next_token(14)}",
            "gross": first_gross,
            "fee": first_fee,
            "tax": first_tax,
            "net": Paise(first_gross - first_fee - first_tax),
        }
    )
    second_fee = Paise(payment.fee - first_fee)
    second_tax = Paise(payment.tax - first_tax)
    second = payment.model_copy(
        update={
            "id": f"pay_{work.next_token(14)}",
            "gross": second_gross,
            "fee": second_fee,
            "tax": second_tax,
            "net": Paise(second_gross - second_fee - second_tax),
            "settlement_id": None,
        }
    )

    work.payments = [p for p in work.payments if p.id != payment_id] + [first, second]
    work.settlements = [
        s.model_copy(update={"payment_ids": [first.id if pid == payment_id else pid for pid in s.payment_ids]})
        if payment_id in s.payment_ids
        else s
        for s in work.settlements
    ]

    key = f"payment:{payment_id}"
    group = work.group_of(key)
    difficulty = work.record_difficulty.get(key, DifficultyClass.PARTIAL_SPLIT)
    work.record_group.pop(key, None)
    work.record_difficulty.pop(key, None)
    for half in (first, second):
        work.record_group[f"payment:{half.id}"] = group.id if group else UNMATCHABLE
        work.record_difficulty[f"payment:{half.id}"] = DifficultyClass.PARTIAL_SPLIT
    if group is not None:
        payment_ids = [pid for pid in group.payment_ids if pid != payment_id] + [first.id, second.id]
        work.groups[group.id] = replace(group, payment_ids=payment_ids)
    else:
        _ = difficulty


_APPLIERS = {
    MutationKind.DUPLICATE_PAYMENT: lambda work, spec: _duplicate_payment(work),
    MutationKind.SHIFT_DATE: lambda work, spec: _shift_date(work, spec.days),
    MutationKind.ALTER_AMOUNT: lambda work, spec: _alter_amount(work, spec.amount),
    MutationKind.DELETE_BANK_LINE: lambda work, spec: _delete_bank_line(work),
    MutationKind.INJECT_UNRELATED_CREDIT: lambda work, spec: _inject_unrelated_credit(work),
    MutationKind.SCRAMBLE_NARRATION: lambda work, spec: _scramble_narration(work),
    MutationKind.SPLIT_PAYMENT: lambda work, spec: _split_payment(work),
}


def apply_mutations(
    corpus: Corpus, truth: Truth | None, specs: list[MutationSpec], seed: int = 0
) -> tuple[Corpus, Truth | None]:
    """Apply `specs` in order to a copy of the corpus, keeping truth in step.

    Deterministic in (corpus, specs, seed): the same run row replays the same
    corruption, which is what makes a mutated run's URL a reproducible test
    rather than an anecdote. An uploaded dataset has no truth to keep in step,
    and passing `None` corrupts the corpus alone.
    """
    if not specs:
        return corpus, truth

    work = _Working(
        invoices=list(corpus.invoices),
        payments=list(corpus.payments),
        settlements=list(corpus.settlements),
        bank_lines=list(corpus.bank_lines),
        groups=dict(truth.groups) if truth else {},
        record_group=dict(truth.record_group) if truth else {},
        record_difficulty=dict(truth.record_difficulty) if truth else {},
        rng=random.Random(seed),
    )

    for spec in specs:
        _APPLIERS[spec.kind](work, spec)

    mutated = Corpus(
        invoices=work.invoices,
        payments=work.payments,
        settlements=work.settlements,
        bank_lines=work.bank_lines,
    )
    if truth is None:
        return mutated, None
    return mutated, Truth(
        groups=work.groups,
        record_group=work.record_group,
        record_difficulty=work.record_difficulty,
    )
