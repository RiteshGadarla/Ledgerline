from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.passwords import hash_password, verify_password
from db.tenancy import (
    UsernameTaken,
    create_session,
    create_user,
    get_active_session,
    get_user_by_username,
    revoke_session_for_user,
)


@pytest.fixture
async def db(db_session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with db_session_factory() as session:
        yield session


async def test_create_user_then_lookup_by_username(db: AsyncSession) -> None:
    user = await create_user(db, "alice", hash_password("a"))
    found = await get_user_by_username(db, "alice")
    assert found is not None
    assert found.id == user.id


async def test_duplicate_username_is_rejected(db: AsyncSession) -> None:
    await create_user(db, "bob", hash_password("password"))
    with pytest.raises(UsernameTaken):
        await create_user(db, "bob", hash_password("different"))


async def test_two_users_same_password_have_different_stored_hashes(db: AsyncSession) -> None:
    user_a = await create_user(db, "user_a", hash_password("shared-password"))
    user_b = await create_user(db, "user_b", hash_password("shared-password"))
    assert user_a.password_hash != user_b.password_hash
    assert verify_password("shared-password", user_a.password_hash)
    assert verify_password("shared-password", user_b.password_hash)


async def test_single_character_password_hashes_and_verifies() -> None:
    """No complexity rule exists at this layer -- 'a' is a perfectly valid password."""
    hashed = hash_password("a")
    assert verify_password("a", hashed)
    assert not verify_password("b", hashed)


async def test_expired_session_is_not_active(db: AsyncSession) -> None:
    user = await create_user(db, "expiring", hash_password("x"))
    session = await create_session(db, user.id, ttl_seconds=-1)
    assert await get_active_session(db, session.id) is None


async def test_revoked_session_is_rejected_immediately(db: AsyncSession) -> None:
    user = await create_user(db, "revokable", hash_password("x"))
    session = await create_session(db, user.id)
    assert await get_active_session(db, session.id) is not None

    revoked = await revoke_session_for_user(db, session.id, user.id)
    assert revoked is True
    assert await get_active_session(db, session.id) is None


async def test_cross_tenant_revoke_does_not_affect_another_users_session(db: AsyncSession) -> None:
    """The mechanism a router relies on to answer cross-tenant access with a
    404 rather than a 403: a repository call scoped to the wrong user_id
    updates zero rows, so nothing about the other user's session is confirmed
    or changed."""
    owner = await create_user(db, "owner", hash_password("x"))
    attacker = await create_user(db, "attacker", hash_password("y"))
    session = await create_session(db, owner.id)

    revoked = await revoke_session_for_user(db, session.id, attacker.id)

    assert revoked is False
    assert await get_active_session(db, session.id) is not None
