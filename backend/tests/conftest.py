import os
from collections.abc import AsyncIterator
from datetime import date, datetime

import pytest
import redis.asyncio as redis

from contracts.models import BankLine, Invoice, Payment, Settlement
from contracts.money import Paise

REDIS_TEST_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture
async def redis_client() -> AsyncIterator[redis.Redis]:
    client: redis.Redis = redis.from_url(REDIS_TEST_URL)
    try:
        await client.ping()
    except Exception:
        pytest.skip(
            f"Redis not reachable at {REDIS_TEST_URL}; start it with `docker compose -f docker/compose.yaml up -d`"
        )
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


def make_invoice(id_: str, amount: int, issued: date = date(2024, 1, 1), ref: str | None = None) -> Invoice:
    return Invoice(id=id_, number=id_, customer="Acme Traders", amount=Paise(amount), issued_at=issued, ref=ref)


def make_payment(
    id_: str,
    gross: int,
    invoice_ref: str | None = None,
    captured: date = date(2024, 1, 1),
    settlement_id: str | None = None,
    status: str = "captured",
) -> Payment:
    fee = Paise(gross * 2 // 100)
    tax = Paise(fee * 18 // 100)
    return Payment(
        id=id_,
        order_id=None,
        invoice_ref=invoice_ref,
        gross=Paise(gross),
        fee=fee,
        tax=tax,
        net=Paise(gross - fee - tax),
        status=status,  # type: ignore[arg-type]
        captured_at=datetime.combine(captured, datetime.min.time()),
        method="upi",
        settlement_id=settlement_id,
    )


def make_settlement(
    id_: str, payment_ids: list[str], payout: int, utr: str | None, fees: int = 0, tax: int = 0, adjustments: int = 0
) -> Settlement:
    return Settlement(
        id=id_,
        utr=utr,
        payout=Paise(payout),
        fees=Paise(fees),
        tax=Paise(tax),
        adjustments=Paise(adjustments),
        settled_at=date(2024, 1, 3),
        payment_ids=payment_ids,
    )


def make_bank_line(id_: str, narration: str, credit: int) -> BankLine:
    return BankLine(
        id=id_,
        value_date=date(2024, 1, 3),
        narration=narration,
        credit=Paise(credit),
        debit=Paise(0),
        balance=Paise(0),
    )
