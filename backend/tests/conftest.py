import os
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime

import pytest
import redis.asyncio as redis
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from contracts.models import BankLine, Invoice, Payment, Settlement
from contracts.money import Paise

REDIS_TEST_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/15")
DATABASE_TEST_URL = os.environ.get(
    "DATABASE_TEST_URL", "postgresql+asyncpg://ledgerline:ledgerline@localhost:5432/ledgerline_test"
)


@pytest.fixture
async def redis_client() -> AsyncIterator[redis.Redis]:
    client: redis.Redis = redis.from_url(REDIS_TEST_URL)  # type: ignore[no-untyped-call]
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


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    from sqlalchemy.pool import NullPool

    from db.base import Base, make_engine
    from db.models import Run, Session, User  # noqa: F401  (registers tables on Base.metadata)

    # NullPool: TestClient runs the ASGI app in its own event loop (a
    # different one from this fixture's), so an idle pooled connection
    # created here would be handed to that other loop and blow up with
    # "Future attached to a different loop". NullPool opens a fresh
    # connection per checkout instead of keeping any around to leak across
    # loops.
    engine = make_engine(DATABASE_TEST_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception:
        await engine.dispose()
        pytest.skip(
            f"Postgres not reachable at {DATABASE_TEST_URL}; "
            "start it with `docker compose -f docker/compose.yaml up -d`"
        )
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.fixture
async def db_session_factory(db_engine: AsyncEngine):  # type: ignore[no-untyped-def]
    from db.base import make_session_factory

    return make_session_factory(db_engine)


@pytest.fixture
def auth_client(db_engine: AsyncEngine, db_session_factory, redis_client: redis.Redis) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    from app.main import app

    # redis_client (above) only proves Redis is reachable and leaves it
    # flushed; the app itself gets its own never-yet-connected client so its
    # connections open lazily on whatever loop TestClient actually runs the
    # ASGI app in, rather than reusing one already bound to this fixture's loop.
    app_redis: redis.Redis = redis.from_url(REDIS_TEST_URL)  # type: ignore[no-untyped-call]
    app.state.db_engine = db_engine
    app.state.db_session_factory = db_session_factory
    app.state.redis_client = app_redis
    try:
        with TestClient(app) as client:
            yield client
    finally:
        import asyncio
        import contextlib

        # TestClient's background event loop is already gone by this point,
        # and app_redis's connections belong to it -- closing cleanly isn't
        # possible from here, so just drop the reference rather than raise
        # out of teardown for an already-finished test.
        app.dependency_overrides.clear()
        with contextlib.suppress(RuntimeError):
            asyncio.run(app_redis.aclose())


@pytest.fixture
def runs_client(db_engine: AsyncEngine, db_session_factory, redis_client: redis.Redis) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    """Like auth_client, but also wires an arq pool for the /runs endpoints.

    ArqRedis.from_url (inherited from redis.asyncio.Redis) never opens a
    connection at construction time, unlike arq.create_pool() which pings
    eagerly -- so the same "never-yet-connected, opens lazily on whichever
    loop TestClient actually runs requests on" trick used for app_redis
    above applies here too.
    """
    from arq.connections import ArqRedis

    from app.main import app

    app_redis: redis.Redis = redis.from_url(REDIS_TEST_URL)  # type: ignore[no-untyped-call]
    arq_pool = ArqRedis.from_url(REDIS_TEST_URL)
    app.state.db_engine = db_engine
    app.state.db_session_factory = db_session_factory
    app.state.redis_client = app_redis
    app.state.arq_pool = arq_pool
    try:
        with TestClient(app) as client:
            yield client
    finally:
        import asyncio
        import contextlib

        app.dependency_overrides.clear()
        with contextlib.suppress(RuntimeError):
            asyncio.run(app_redis.aclose())
        with contextlib.suppress(RuntimeError):
            asyncio.run(arq_pool.aclose())


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
