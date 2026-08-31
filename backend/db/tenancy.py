import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Run, Session, User
from db.passwords import verify_password

RunSource = Literal["demo", "dataset"]
RunState = Literal["queued", "normalising", "matching", "triaging", "explaining", "scoring", "complete", "failed"]

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


@dataclass(frozen=True)
class RunRecord:
    id: str
    user_id: str
    source: RunSource
    seed: int | None
    dataset_id: str | None
    size: int | None
    state: RunState
    error: str | None
    result_json: str | None
    metrics_json: str | None
    created_at: datetime
    updated_at: datetime


def _to_run_record(run: Run) -> RunRecord:
    return RunRecord(
        id=run.id,
        user_id=run.user_id,
        source=cast("RunSource", run.source),
        seed=run.seed,
        dataset_id=run.dataset_id,
        size=run.size,
        state=cast("RunState", run.state),
        error=run.error,
        result_json=run.result_json,
        metrics_json=run.metrics_json,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


async def create_run(
    db: AsyncSession,
    user_id: str,
    source: RunSource,
    seed: int | None = None,
    dataset_id: str | None = None,
    size: int | None = None,
    mutations: list[str] | None = None,
    idempotency_key: str | None = None,
) -> tuple[RunRecord, bool]:
    """Idempotent by (user_id, idempotency_key): replaying the same POST with
    the same key returns the existing run instead of creating a second one.
    A null key never collides with anything. The bool is True only when a new
    row was actually created -- callers use it to decide whether to enqueue
    a job, since an idempotent hit must never be enqueued twice."""
    if idempotency_key is not None:
        existing = await get_run_by_idempotency_key(db, user_id, idempotency_key)
        if existing is not None:
            return existing, False

    now = datetime.now(UTC)
    run = Run(
        id=str(uuid.uuid4()),
        user_id=user_id,
        source=source,
        seed=seed,
        dataset_id=dataset_id,
        size=size,
        mutations=mutations,
        idempotency_key=idempotency_key,
        state="queued",
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    try:
        await db.commit()
    except IntegrityError:
        # Lost a race against a concurrent request with the same idempotency
        # key: fetch the row the other request just created rather than error.
        await db.rollback()
        existing = await get_run_by_idempotency_key(db, user_id, idempotency_key) if idempotency_key else None
        if existing is not None:
            return existing, False
        raise
    await db.refresh(run)
    return _to_run_record(run), True


async def get_run_by_idempotency_key(db: AsyncSession, user_id: str, idempotency_key: str) -> RunRecord | None:
    result = await db.execute(
        select(Run).where(Run.user_id == user_id, Run.idempotency_key == idempotency_key)
    )
    run = result.scalar_one_or_none()
    return _to_run_record(run) if run is not None else None


async def get_run_for_user(db: AsyncSession, run_id: str, user_id: str) -> RunRecord | None:
    """Scoped by user_id, same as every other tenant-scoped lookup: a run
    belonging to another user is indistinguishable from a nonexistent one."""
    result = await db.execute(select(Run).where(Run.id == run_id, Run.user_id == user_id))
    run = result.scalar_one_or_none()
    return _to_run_record(run) if run is not None else None


async def transition_run_state(db: AsyncSession, run_id: str, state: RunState) -> None:
    await db.execute(update(Run).where(Run.id == run_id).values(state=state, updated_at=datetime.now(UTC)))
    await db.commit()


async def complete_run(db: AsyncSession, run_id: str, result_json: str, metrics_json: str) -> None:
    await db.execute(
        update(Run)
        .where(Run.id == run_id)
        .values(state="complete", result_json=result_json, metrics_json=metrics_json, updated_at=datetime.now(UTC))
    )
    await db.commit()


async def fail_run(db: AsyncSession, run_id: str, error: str) -> None:
    await db.execute(
        update(Run).where(Run.id == run_id).values(state="failed", error=error, updated_at=datetime.now(UTC))
    )
    await db.commit()
