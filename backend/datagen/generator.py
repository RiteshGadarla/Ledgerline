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
_FEE_BPS = 200
_GST_ON_FEE_BPS = 1800
_MIN_BATCH = 8
_MAX_BATCH = 25

_CUSTOMER_NAMES = [
    "Acme Traders", "Globex Industries", "Initech Retail", "Umbrella Foods",
    "Wayne Logistics", "Stark Components", "Wonka Confectionery", "Hooli Systems",
    "Soylent Distributors", "Vandelay Exports", "Pied Piper Textiles", "Aperture Hardware",
    "Cyberdyne Traders", "Gringotts Finance", "Oscorp Chemicals", "Massive Dynamic",
]
_ALT_TRADE_NAMES = [
    "AT Enterprises Pvt Ltd", "GI Holdings LLP", "IR Commerce Pvt Ltd",
    "UF Distribution Co", "WL Freight Pvt Ltd",
]
_METHODS = ["card", "upi", "netbanking", "wallet"]
_TOKEN_ALPHABET = string.ascii_letters + string.digits


def _token(rng: random.Random, length: int) -> str:
    return "".join(rng.choices(_TOKEN_ALPHABET, k=length))


def _utr(rng: random.Random) -> str:
    return "".join(rng.choices(string.digits, k=16))


def _round_bps(amount: int, bps: int) -> int:
    return (amount * bps + 5000) // 10000


@dataclass
class _Unit:
    difficulty: DifficultyClass
    invoice: Invoice
    payments: list[Payment]


def _build_class_plan(rng: random.Random, size: int) -> list[DifficultyClass]:
    classes = [c for c in DifficultyClass if c != DifficultyClass.CLEAN]
    plan: list[DifficultyClass] = list(classes[: min(len(classes), size)])
    while len(plan) < size:
        plan.append(DifficultyClass.CLEAN)
    rng.shuffle(plan)
    return plan


def _make_invoice(rng: random.Random, seq: int, issued_at: date, customer: str) -> Invoice:
    amount = Paise(rng.randint(50_00, 500_000_00))
    return Invoice(
        id=f"INV{seq:06d}",
        number=f"INV/2024/{seq:05d}",
        customer=customer,
        amount=amount,
        issued_at=issued_at,
        ref=f"INV{seq:06d}",
    )


def _make_payment(
    rng: random.Random,
    invoice: Invoice,
    gross: Paise,
    captured_at: date,
    status: str = "captured",
    fee_bps: int = _FEE_BPS,
    gst_bps: int = _GST_ON_FEE_BPS,
) -> Payment:
    fee = Paise(_round_bps(gross, fee_bps))
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
        captured_at=datetime.combine(
            captured_at, time(hour=rng.randint(9, 20), minute=rng.randint(0, 59))
        ),
        method=rng.choice(_METHODS),
        settlement_id=None,
    )


def _build_units(
    rng: random.Random, plan: list[DifficultyClass]
) -> tuple[list[_Unit], int, list[Payment]]:
    seq = 0
    current_date = _ANCHOR_DATE
    units: list[_Unit] = []
    unrelated_credit_count = 0
    orphan_duplicate_payments: list[Payment] = []

    for difficulty in plan:
        if difficulty == DifficultyClass.UNRELATED_CREDIT:
            unrelated_credit_count += 1
            continue

        seq += 1
        if seq % rng.randint(_MIN_BATCH, _MAX_BATCH) == 0:
            current_date = add_business_days(current_date, 1)

        customer = rng.choice(_CUSTOMER_NAMES)
        invoice = _make_invoice(rng, seq, current_date, customer)

        if difficulty == DifficultyClass.UNMATCHABLE:
            units.append(_Unit(difficulty, invoice, []))
            continue

        if difficulty == DifficultyClass.PARTIAL_SPLIT:
            first_gross = Paise(invoice.amount * 6 // 10)
            second_gross = Paise(invoice.amount - first_gross)
            payments = [
                _make_payment(rng, invoice, first_gross, current_date),
                _make_payment(rng, invoice, second_gross, current_date),
            ]
        elif difficulty == DifficultyClass.REFUND_IN_BATCH:
            payments = [_make_payment(rng, invoice, invoice.amount, current_date, status="refunded")]
        elif difficulty == DifficultyClass.CHARGEBACK:
            payments = [_make_payment(rng, invoice, invoice.amount, current_date, status="disputed")]
        elif difficulty == DifficultyClass.FEE_GST_DELTA:
            payments = [
                _make_payment(
                    rng, invoice, invoice.amount, current_date, fee_bps=_FEE_BPS + 25, gst_bps=_GST_ON_FEE_BPS
                )
            ]
        elif difficulty == DifficultyClass.DUPLICATE_PAYMENT:
            real = _make_payment(rng, invoice, invoice.amount, current_date)
            duplicate = _make_payment(rng, invoice, invoice.amount, current_date)
            units.append(_Unit(DifficultyClass.DUPLICATE_PAYMENT, invoice, [real]))
            orphan_duplicate_payments.append(duplicate)
            continue
        else:
            payments = [_make_payment(rng, invoice, invoice.amount, current_date)]

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
        window_days = 7 if DifficultyClass.DATE_OUTSIDE_WINDOW in difficulties else 2
        missing_utr = DifficultyClass.NARRATION_MISSING_UTR in difficulties
        payer_mismatch = DifficultyClass.PAYER_NAME_MISMATCH in difficulties

        batch_payments = [payment for unit in batch for payment in unit.payments]
        last_captured = max(payment.captured_at.date() for payment in batch_payments)
        settled_at = add_business_days(last_captured, window_days)

        gross_total = Paise(sum(payment.gross for payment in batch_payments))
        fees_total = Paise(sum(payment.fee for payment in batch_payments))
        tax_total = Paise(sum(payment.tax for payment in batch_payments))
        refunds_total = Paise(
            sum(payment.gross for payment in batch_payments if payment.status in ("refunded", "disputed"))
        )
        adjustments = Paise(rng.choice([0, 0, 0, -100, 100, -250, 250]))
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
        if missing_utr:
            narration = f"BANK CREDIT SETTLEMENT PAYOUT BATCH {batch_index} REF UNAVAILABLE"
        else:
            narration = f"NEFT UTR {utr} SETTLEMENT PAYOUT RAZORPAY BATCH {batch_index}"
        if payer_mismatch:
            narration += f" FAO {rng.choice(_ALT_TRADE_NAMES)}"

        running_balance = Paise(running_balance + payout)
        bank_lines.append(
            BankLine(
                id=bank_line_id,
                value_date=settled_at,
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
        credit = Paise(rng.randint(100_00, 50_000_00))
        running_balance = Paise(running_balance + credit)
        bank_lines.append(
            BankLine(
                id=bank_line_id,
                value_date=add_business_days(last_date, i + 1),
                narration=f"UNIDENTIFIED CREDIT MISC INWARD REMITTANCE {_token(rng, 8)}",
                credit=credit,
                debit=Paise(0),
                balance=running_balance,
            )
        )
        truth.record_group[f"bank_line:{bank_line_id}"] = UNMATCHABLE
        truth.record_difficulty[f"bank_line:{bank_line_id}"] = DifficultyClass.UNRELATED_CREDIT

    corpus = Corpus(invoices=invoices, payments=payments, settlements=settlements, bank_lines=bank_lines)
    return corpus, truth
