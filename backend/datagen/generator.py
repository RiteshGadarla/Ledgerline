"""Seeded synthetic corpus with a ground-truth answer key.

The corpus is deliberately *uneven*. Uniform random amounts, one fee rate and
one narration format produce data a reconciler can pass without ever being
tested: every record looks like every other record, so any rule that works
once works everywhere. Real books are not like that. Invoice values are heavy
tailed and cluster on round rupee figures; UPI carries no MDR while cards
carry two percent; every bank writes its statement narration differently and
truncates the payer name at a different column; credits post on the settlement
date, or a day after it, or three days after it over a long weekend.

So the variance here is modelled, not sprinkled. Each source of spread below
is a property real Indian gateway data actually has, and each one is a
different way for a naive matcher to be wrong.

Two invariants survive all of it, and both are asserted in tests:

  * `sum(gross) - fees - tax - refunds + adjustments == payout`, exactly, in
    integer paise, for every settlement.
  * Same (seed, size) produces a byte-identical corpus and truth file.
"""

import random
import string
from dataclasses import dataclass
from datetime import date, datetime, time

from contracts.corpus import Corpus
from contracts.models import BankLine, Invoice, Payment, Settlement
from contracts.money import Paise
from datagen.difficulty import DifficultyClass
from datagen.models import UNMATCHABLE, Truth, TruthGroup
from money.business_days import add_business_days

_ANCHOR_DATE = date(2024, 1, 8)  # a fixed Monday; generation never touches the real clock
_GST_ON_FEE_BPS = 1800
_MIN_BATCH = 8
_MAX_BATCH = 25

# Indian gateway pricing, roughly as charged. UPI is zero-MDR for P2M, which
# is not a rounding detail: it means a large share of payments carry no fee and
# no GST at all, and a reconciler that assumes "there is always a fee" breaks
# on real data long before it breaks on a uniform corpus.
_METHOD_FEE_BPS = {"upi": 0, "card": 200, "netbanking": 150, "wallet": 190}
_METHODS = list(_METHOD_FEE_BPS)
_METHOD_WEIGHTS = [40, 30, 18, 12]

# Hour-of-day capture profile: a lunchtime peak, a heavier evening peak, a thin
# overnight tail. Retail payments are not uniform across the clock.
_HOUR_WEIGHTS = [
    1,
    1,
    1,
    1,
    1,
    2,
    4,
    8,
    14,
    22,
    30,
    38,  # 00:00 - 11:59
    40,
    34,
    28,
    26,
    28,
    34,
    44,
    52,
    48,
    34,
    18,
    6,  # 12:00 - 23:59
]

_CUSTOMER_NAMES = [
    "Acme Traders Pvt Ltd",
    "Globex Industries Ltd",
    "Initech Retail LLP",
    "Umbrella Foods Pvt Ltd",
    "Wayne Logistics Pvt Ltd",
    "Stark Components Ltd",
    "Wonka Confectionery Pvt Ltd",
    "Hooli Systems India Pvt Ltd",
    "Soylent Distributors LLP",
    "Vandelay Exports Pvt Ltd",
    "Pied Piper Textiles Pvt Ltd",
    "Aperture Hardware Pvt Ltd",
    "Cyberdyne Traders LLP",
    "Gringotts Finance Pvt Ltd",
    "Oscorp Chemicals Ltd",
    "Massive Dynamic India Pvt Ltd",
    "Sterling Cooper Advertising LLP",
    "Bluth Frozen Foods Pvt Ltd",
    "Dunder Mifflin Paper Co",
    "Prestige Worldwide Pvt Ltd",
    "Nakatomi Trading Pvt Ltd",
    "Tyrell Biosciences Ltd",
    "Weyland Metals Pvt Ltd",
    "Zorg Instruments LLP",
    "Monarch Solar Pvt Ltd",
    "Trivedi & Sons",
    "Rao Brothers Traders",
    "Krishna Enterprises",
    "Shree Balaji Agencies",
    "Ganesh Auto Spares",
    "Deccan Polymers Pvt Ltd",
    "Konkan Seafoods LLP",
    "Malabar Spice Exports Pvt Ltd",
    "Chandni Chowk Textiles",
    "Nilgiri Tea Estates Pvt Ltd",
    "Rajasthan Marble Depot",
    "Coromandel Packaging Ltd",
    "Vindhya Cement Traders",
    "Kaveri Irrigation Pvt Ltd",
    "Sundaram Fasteners Depot",
]

# What the *bank* calls them: truncated, abbreviated, sometimes just wrong.
_ALT_TRADE_NAMES = [
    "AT ENTERPRISES PVT LTD",
    "GI HOLDINGS LLP",
    "IR COMMERCE PVT LTD",
    "UF DISTRIBUTION CO",
    "WL FREIGHT PVT LTD",
    "S COMPONENTS INDIA",
    "PIED PIPER TEX",
    "M/S TRIVEDI AND SO",
    "SHREE BALAJI AGENC",
]

_IFSC_PREFIXES = ["HDFC", "ICIC", "SBIN", "UTIB", "KKBK", "YESB", "IDFB", "PUNB"]
_RAILS = ["NEFT", "IMPS", "RTGS"]

# One template per bank house style. Every one keeps the UTR labelled, because
# an unlabelled reference is its own difficulty class (narration_missing_utr)
# rather than the default case -- but the label, the separators, the ordering
# and the surrounding noise all move.
_NARRATION_TEMPLATES = [
    "{rail} CR-{ifsc}0{branch}-{payer}-UTR {utr}-RAZORPAY SETTLEMENT",
    "BY TRANSFER-{rail}*{ifsc}*UTR{utr}*RAZORPAY SOFTWARE PVT LTD",
    "{rail}/{ifsc}0{branch}/UTR NO {utr}/RAZORPAY PAYOUT {batch}",
    "{rail} INWARD UTR:{utr} FAV {payer} BATCH {batch}",
    "SETTLEMENT CREDIT RAZORPAY UTR-{utr} REF {batch} {rail}",
    "  {rail}  CR   UTR   {utr}   RAZORPAY   SETTLEMENT   BATCH {batch}  ",
]

# Bank statements that print the reference without ever naming it.
_MISSING_UTR_TEMPLATES = [
    "{rail} CR-{ifsc}0{branch}-RAZORPAY SETTLEMENT-REF UNAVAILABLE",
    "BY TRANSFER FROM RAZORPAY SOFTWARE PVT LTD BATCH {batch}",
    "SETTLEMENT PAYOUT CREDIT NARRATION TRUNCATED*{batch}*",
]

_UNIDENTIFIED_TEMPLATES = [
    "{rail} INWARD REMITTANCE FROM SUNDRY DEBTOR {token}",
    "MISC CREDIT NOT IDENTIFIED REF {token}",
    "{rail} CR-{ifsc}0{branch}-UNKNOWN REMITTER-{token}",
    "CASH DEPOSIT BRANCH COUNTER {token}",
]

_TOKEN_ALPHABET = string.ascii_letters + string.digits
_UTR_ALPHABET = string.ascii_uppercase + string.digits


def _token(rng: random.Random, length: int) -> str:
    return "".join(rng.choices(_TOKEN_ALPHABET, k=length))


def _utr(rng: random.Random) -> str:
    """Two real shapes: a bare 16-digit reference, and a bank-prefixed
    alphanumeric one. A matcher that assumes digits only fails on the second."""
    if rng.random() < 0.55:
        return "".join(rng.choices(string.digits, k=16))
    prefix = rng.choice(_IFSC_PREFIXES)
    return prefix + "".join(rng.choices(_UTR_ALPHABET, k=rng.choice([11, 12, 14])))


def _round_bps(amount: int, bps: int) -> int:
    return (amount * bps + 5000) // 10000


def _invoice_amount(rng: random.Random) -> Paise:
    """Heavy tailed and round-number biased, which is what real invoice books
    look like: a mass of four-figure sales, a thin tail of six-figure ones, and
    a strong pull toward values a human typed."""
    exponent = rng.choices([2, 3, 4, 5], weights=[12, 44, 34, 10])[0]
    rupees = rng.randint(10**exponent, 10 ** (exponent + 1) - 1)
    step = rng.choices([1, 5, 10, 100, 500], weights=[30, 12, 26, 22, 10])[0]
    rupees = max(step, (rupees // step) * step)
    paise = rupees * 100
    if step == 1 and rng.random() < 0.45:
        paise += rng.choice([25, 40, 50, 60, 75, 90, 99])
    return Paise(paise)


def _capture_time(rng: random.Random) -> time:
    hour = rng.choices(range(24), weights=_HOUR_WEIGHTS)[0]
    return time(hour=hour, minute=rng.randint(0, 59), second=rng.randint(0, 59))


@dataclass
class _Unit:
    difficulty: DifficultyClass
    invoice: Invoice
    payments: list[Payment]


# What share of a corpus is a hard case rather than a clean tie-out.
#
# The plan used to plant exactly one record per class whatever the size, which
# made every class recall either 0% or 100% and nothing in between: a corpus of
# 150 records scored ten difficulty classes off a single example each. A figure
# computed from one record is an anecdote, and this project's whole argument is
# that one cherry-picked match proves nothing.
#
# Fifteen percent is a deliberate over-representation: real books are not this
# difficult, and that is the point. The clean case is the one needing no
# evidence, so a generator that mostly emits it measures the easy path over and
# over. Enough hard cases to score, not so many that the corpus stops
# resembling a set of books.
HARD_SHARE = 0.15


def _build_class_plan(rng: random.Random, size: int) -> list[DifficultyClass]:
    """Which difficulty class each unit of the corpus is built to exercise.

    Every class scales with the corpus instead of appearing once, so a bigger
    dataset buys more of each hard case to be scored on rather than a longer
    tail of clean ones. Deterministic in `rng`: the same seed plans the same
    corpus, which is what makes a run reproducible from its URL.
    """
    classes: list[DifficultyClass] = [c for c in DifficultyClass if c != DifficultyClass.CLEAN]

    # Too small to carry one of each: take as many distinct classes as fit, so
    # a tiny corpus still exercises different code paths rather than repeating
    # the first one.
    if size <= len(classes):
        head: list[DifficultyClass] = list(classes[:size])
        rng.shuffle(head)
        return head

    # At least one clean tie-out survives however the share rounds.
    budget = min(size - 1, round(size * HARD_SHARE))
    base, remainder = divmod(budget, len(classes))
    counts = {difficulty: base for difficulty in classes}
    # The remainder goes to a seeded sample rather than to whichever classes
    # happen to be declared first, so no class is systematically the larger one
    # across every seed.
    for difficulty in rng.sample(classes, remainder):
        counts[difficulty] += 1
    # The floor is applied last, after the budget has been shared out. Applied
    # first it would compound with the remainder and hand a small corpus far
    # more hard cases than the share asks for.
    counts = {difficulty: max(1, count) for difficulty, count in counts.items()}

    plan: list[DifficultyClass] = [difficulty for difficulty, count in counts.items() for _ in range(count)]
    plan.extend([DifficultyClass.CLEAN] * (size - len(plan)))
    rng.shuffle(plan)
    return plan


def _make_invoice(rng: random.Random, seq: int, issued_at: date, customer: str) -> Invoice:
    # The printed invoice number carries gaps and a per-book series, the way a
    # real ledger does; `ref` stays the dense machine key the gateway echoes.
    series = rng.choice(["INV", "SL", "TAX", "GST"])
    financial_year = f"{issued_at.year}-{str(issued_at.year + 1)[2:]}"
    printed_seq = seq * rng.choice([1, 1, 1, 2]) + rng.randint(0, 3)
    printed = f"{series}/{financial_year}/{printed_seq:05d}"
    return Invoice(
        id=f"INV{seq:06d}",
        number=printed,
        customer=customer,
        amount=_invoice_amount(rng),
        issued_at=issued_at,
        ref=f"INV{seq:06d}",
    )


def _make_payment(
    rng: random.Random,
    invoice: Invoice,
    gross: Paise,
    captured_at: date,
    status: str = "captured",
    fee_bps: int | None = None,
    gst_bps: int = _GST_ON_FEE_BPS,
    method: str | None = None,
) -> Payment:
    method = method or rng.choices(_METHODS, weights=_METHOD_WEIGHTS)[0]
    fee = Paise(_round_bps(gross, _METHOD_FEE_BPS[method] if fee_bps is None else fee_bps))
    tax = Paise(_round_bps(fee, gst_bps))
    net = Paise(gross - fee - tax)
    return Payment(
        id=f"pay_{_token(rng, 14)}",
        order_id=f"order_{_token(rng, 14)}",
        invoice_ref=invoice.ref,
        gross=gross,
        fee=fee,
        tax=tax,
        net=net,
        status=status,  # type: ignore[arg-type]
        captured_at=datetime.combine(captured_at, _capture_time(rng)),
        method=method,
        settlement_id=None,
    )


def _build_units(rng: random.Random, plan: list[DifficultyClass]) -> tuple[list[_Unit], int, list[Payment]]:
    seq = 0
    current_date = _ANCHOR_DATE
    units: list[_Unit] = []
    unrelated_credit_count = 0
    orphan_duplicate_payments: list[Payment] = []
    invoices_on_current_date = 0
    invoices_before_advancing = rng.randint(2, 7)

    for difficulty in plan:
        if difficulty == DifficultyClass.UNRELATED_CREDIT:
            unrelated_credit_count += 1
            continue

        seq += 1
        # Trading days carry an uneven number of invoices and the ledger skips
        # weekends, so the corpus spans a realistic calendar rather than a
        # smooth one.
        invoices_on_current_date += 1
        if invoices_on_current_date >= invoices_before_advancing:
            current_date = add_business_days(current_date, rng.choices([1, 1, 1, 2, 3], weights=[50, 20, 15, 10, 5])[0])
            invoices_on_current_date = 0
            invoices_before_advancing = rng.randint(2, 7)

        customer = rng.choice(_CUSTOMER_NAMES)
        invoice = _make_invoice(rng, seq, current_date, customer)
        # Payment lands the same day or shortly after the invoice is raised.
        captured_on = add_business_days(current_date, rng.choices([0, 1, 2], weights=[64, 26, 10])[0])

        if difficulty == DifficultyClass.UNMATCHABLE:
            units.append(_Unit(difficulty, invoice, []))
            continue

        if difficulty == DifficultyClass.PARTIAL_SPLIT:
            share = rng.choice([5, 6, 7])
            first_gross = Paise(invoice.amount * share // 10)
            second_gross = Paise(invoice.amount - first_gross)
            payments = [
                _make_payment(rng, invoice, first_gross, captured_on),
                _make_payment(rng, invoice, second_gross, captured_on),
            ]
        elif difficulty == DifficultyClass.REFUND_IN_BATCH:
            payments = [_make_payment(rng, invoice, invoice.amount, captured_on, status="refunded")]
        elif difficulty == DifficultyClass.CHARGEBACK:
            payments = [_make_payment(rng, invoice, invoice.amount, captured_on, status="disputed")]
        elif difficulty == DifficultyClass.FEE_GST_DELTA:
            # A rate card that moved mid-month, or a surcharge nobody minuted.
            payments = [
                _make_payment(
                    rng,
                    invoice,
                    invoice.amount,
                    captured_on,
                    fee_bps=rng.choice([225, 235, 250, 175]),
                    method="card",
                )
            ]
        elif difficulty == DifficultyClass.DUPLICATE_PAYMENT:
            real = _make_payment(rng, invoice, invoice.amount, captured_on)
            duplicate = _make_payment(rng, invoice, invoice.amount, captured_on)
            units.append(_Unit(DifficultyClass.DUPLICATE_PAYMENT, invoice, [real]))
            orphan_duplicate_payments.append(duplicate)
            continue
        else:
            payments = [_make_payment(rng, invoice, invoice.amount, captured_on)]

        units.append(_Unit(difficulty, invoice, payments))

    return units, unrelated_credit_count, orphan_duplicate_payments


def _batch_units(rng: random.Random, units: list[_Unit]) -> list[list[_Unit]]:
    batches: list[list[_Unit]] = []
    current: list[_Unit] = []
    current_size = 0
    target = rng.randint(_MIN_BATCH, _MAX_BATCH)
    for unit in units:
        current.append(unit)
        current_size += len(unit.payments)
        if current_size >= target:
            batches.append(current)
            current = []
            current_size = 0
            target = rng.randint(_MIN_BATCH, _MAX_BATCH)
    if current:
        batches.append(current)
    return batches


def _adjustments(rng: random.Random, gross_total: Paise) -> Paise:
    """Most batches settle clean. The rest carry a TDS-style deduction on the
    gross, or a small rounding correction the gateway applied by hand."""
    roll = rng.random()
    if roll < 0.62:
        return Paise(0)
    if roll < 0.86:
        return Paise(-_round_bps(gross_total, 10))  # 0.1% TDS
    return Paise(rng.choice([-500, -250, -100, -50, 50, 100, 250, 500]))


def _narration(rng: random.Random, utr: str, batch_index: int, missing_utr: bool, payer_mismatch: bool) -> str:
    template = rng.choice(_MISSING_UTR_TEMPLATES if missing_utr else _NARRATION_TEMPLATES)
    narration = template.format(
        rail=rng.choice(_RAILS),
        ifsc=rng.choice(_IFSC_PREFIXES),
        branch=f"{rng.randint(0, 9999):04d}",
        payer=rng.choice(_ALT_TRADE_NAMES) if payer_mismatch else "RAZORPAY SOFTWARE PVT LTD",
        utr=utr,
        batch=batch_index,
    )
    if payer_mismatch and "FAV" not in narration and "{payer}" not in template:
        narration += f" FAO {rng.choice(_ALT_TRADE_NAMES)}"
    return narration


def generate_corpus(seed: int, size: int) -> tuple[Corpus, Truth]:
    """Generate a deterministic synthetic reconciliation corpus with a ground-truth answer key.

    Pure function of (seed, size): same inputs always produce byte-identical output.
    Settled units (everything but the genuinely unmatchable, duplicate-payment
    and unrelated-credit records) are grouped one settlement batch per truth
    group, since a bank credit and its settlement cover every payment in that
    batch together.
    """
    rng = random.Random(seed)
    plan = _build_class_plan(rng, size)
    units, unrelated_credit_count, orphan_duplicate_payments = _build_units(rng, plan)

    truth = Truth()
    invoices: list[Invoice] = []
    payments: list[Payment] = []
    settlements: list[Settlement] = []
    bank_lines: list[BankLine] = []
    running_balance = Paise(10_000_000_00)

    settleable: list[_Unit] = []
    for unit in units:
        invoices.append(unit.invoice)
        if unit.difficulty == DifficultyClass.UNMATCHABLE:
            truth.record_group[f"invoice:{unit.invoice.id}"] = UNMATCHABLE
            truth.record_difficulty[f"invoice:{unit.invoice.id}"] = unit.difficulty
            continue
        settleable.append(unit)

    for duplicate_payment in orphan_duplicate_payments:
        payments.append(duplicate_payment)
        truth.record_group[f"payment:{duplicate_payment.id}"] = UNMATCHABLE
        truth.record_difficulty[f"payment:{duplicate_payment.id}"] = DifficultyClass.DUPLICATE_PAYMENT

    batches = _batch_units(rng, settleable)

    for batch_index, batch in enumerate(batches, start=1):
        settlement_id = f"STL{batch_index:06d}"
        difficulties = {unit.difficulty for unit in batch}
        missing_utr = DifficultyClass.NARRATION_MISSING_UTR in difficulties
        payer_mismatch = DifficultyClass.PAYER_NAME_MISMATCH in difficulties
        # T+2 is the norm, T+1 and T+3 happen, and the batch tagged as the
        # late one drags out to well over a week.
        if DifficultyClass.DATE_OUTSIDE_WINDOW in difficulties:
            window_days = rng.choice([7, 8, 9])
        else:
            window_days = rng.choices([1, 2, 3], weights=[18, 66, 16])[0]

        batch_payments = [payment for unit in batch for payment in unit.payments]
        last_captured = max(payment.captured_at.date() for payment in batch_payments)
        settled_at = add_business_days(last_captured, window_days)

        gross_total = Paise(sum(payment.gross for payment in batch_payments))
        fees_total = Paise(sum(payment.fee for payment in batch_payments))
        tax_total = Paise(sum(payment.tax for payment in batch_payments))
        refunds_total = Paise(
            sum(payment.gross for payment in batch_payments if payment.status in ("refunded", "disputed"))
        )
        adjustments = _adjustments(rng, gross_total)
        payout = Paise(gross_total - fees_total - tax_total - refunds_total + adjustments)

        utr = _utr(rng)
        settled_payments = [p.model_copy(update={"settlement_id": settlement_id}) for p in batch_payments]
        payments.extend(settled_payments)
        settlements.append(
            Settlement(
                id=settlement_id,
                utr=utr,
                payout=payout,
                fees=fees_total,
                tax=tax_total,
                adjustments=adjustments,
                settled_at=settled_at,
                payment_ids=[p.id for p in settled_payments],
            )
        )

        bank_line_id = f"BNK{batch_index:06d}"
        narration = _narration(rng, utr, batch_index, missing_utr, payer_mismatch)

        # The credit posts on the settlement date or shortly after it -- banks
        # book value dates a day or two late over weekends. Always inside the
        # engine's credit window, so this is realism, not a hidden difficulty.
        value_date = settled_at
        for _ in range(rng.choices([0, 1, 2], weights=[70, 22, 8])[0]):
            value_date = add_business_days(value_date, 1)

        running_balance = Paise(running_balance + payout)
        bank_lines.append(
            BankLine(
                id=bank_line_id,
                value_date=value_date,
                narration=narration,
                credit=payout,
                debit=Paise(0),
                balance=running_balance,
            )
        )

        group_id = settlement_id
        truth.groups[group_id] = TruthGroup(
            id=group_id,
            invoice_ids=[unit.invoice.id for unit in batch],
            payment_ids=[p.id for p in settled_payments],
            settlement_id=settlement_id,
            bank_line_id=bank_line_id,
        )
        truth.record_group[f"settlement:{settlement_id}"] = group_id
        truth.record_group[f"bank_line:{bank_line_id}"] = group_id
        for unit in batch:
            truth.record_group[f"invoice:{unit.invoice.id}"] = group_id
            truth.record_difficulty[f"invoice:{unit.invoice.id}"] = unit.difficulty
            for payment in unit.payments:
                truth.record_group[f"payment:{payment.id}"] = group_id
                truth.record_difficulty[f"payment:{payment.id}"] = unit.difficulty

    last_date = settlements[-1].settled_at if settlements else _ANCHOR_DATE
    for i in range(unrelated_credit_count):
        bank_line_id = f"BNK{len(bank_lines) + 1:06d}"
        credit = _invoice_amount(rng)
        running_balance = Paise(running_balance + credit)
        bank_lines.append(
            BankLine(
                id=bank_line_id,
                value_date=add_business_days(last_date, i + 1),
                narration=rng.choice(_UNIDENTIFIED_TEMPLATES).format(
                    rail=rng.choice(_RAILS),
                    ifsc=rng.choice(_IFSC_PREFIXES),
                    branch=f"{rng.randint(0, 9999):04d}",
                    token=_token(rng, 8).upper(),
                ),
                credit=credit,
                debit=Paise(0),
                balance=running_balance,
            )
        )
        truth.record_group[f"bank_line:{bank_line_id}"] = UNMATCHABLE
        truth.record_difficulty[f"bank_line:{bank_line_id}"] = DifficultyClass.UNRELATED_CREDIT

    corpus = Corpus(invoices=invoices, payments=payments, settlements=settlements, bank_lines=bank_lines)
    return corpus, truth
