import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Session, User
from db.passwords import verify_password

DEFAULT_SESSION_TTL_SECONDS = 14 * 24 * 3600


class UsernameTaken(Exception):
    """Raised by create_user when the username already exists."""


@dataclass(frozen=True)
class UserRecord:
    """A plain, non-ORM view of a user row. Everything above db/ (app/routers
    included) sees this instead of db.models.User -- the SQLAlchemy model
    stays confined to the repository layer, per the tenancy contract."""

    id: str
    username: str
    password_hash: str


@dataclass(frozen=True)
class SessionRecord:
    id: str
    user_id: str
    expires_at: datetime


def _to_user_record(user: User) -> UserRecord:
    return UserRecord(id=user.id, username=user.username, password_hash=user.password_hash)


def _to_session_record(session: Session) -> SessionRecord:
    return SessionRecord(id=session.id, user_id=session.user_id, expires_at=session.expires_at)


async def create_user(db: AsyncSession, username: str, password_hash: str) -> UserRecord:
    existing = await _get_user_row_by_username(db, username)
    if existing is not None:
        raise UsernameTaken(username)
    user = User(id=str(uuid.uuid4()), username=username, password_hash=password_hash, created_at=datetime.now(UTC))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _to_user_record(user)


async def _get_user_row_by_username(db: AsyncSession, username: str) -> User | None:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def get_user_by_username(db: AsyncSession, username: str) -> UserRecord | None:
    user = await _get_user_row_by_username(db, username)
    return _to_user_record(user) if user is not None else None


async def get_user_by_id(db: AsyncSession, user_id: str) -> UserRecord | None:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    return _to_user_record(user) if user is not None else None


async def authenticate(db: AsyncSession, username: str, password: str) -> UserRecord | None:
    """Looks up the user and verifies the password in one call, so nothing
    above this layer -- routers included -- ever needs to see a password hash."""
    user = await _get_user_row_by_username(db, username)
    if user is None or not verify_password(password, user.password_hash):
        return None
    return _to_user_record(user)


async def create_session(
    db: AsyncSession, user_id: str, ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS
) -> SessionRecord:
    now = datetime.now(UTC)
    session = Session(
        id=str(uuid.uuid4()),
        user_id=user_id,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        revoked_at=None,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return _to_session_record(session)


async def get_active_session(db: AsyncSession, session_id: str) -> SessionRecord | None:
    """The session must exist, be unrevoked, and unexpired. Used by the
    get_current_user dependency -- the only place a session id is resolved
    to a user without already knowing that user's id."""
    session = await db.get(Session, session_id)
    if session is None or session.revoked_at is not None:
        return None
    if session.expires_at <= datetime.now(UTC):
        return None
    return _to_session_record(session)


async def revoke_session_for_user(db: AsyncSession, session_id: str, user_id: str) -> bool:
    """Every tenant-scoped mutation filters by user_id explicitly, so a
    session id belonging to another user updates zero rows -- the caller
    turns that into a 404, never a 403, so ids are never confirmed to exist."""
    result = cast(
        CursorResult[Session],
        await db.execute(
            update(Session)
            .where(Session.id == session_id, Session.user_id == user_id, Session.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        ),
    )
    await db.commit()
    return bool(result.rowcount > 0)
