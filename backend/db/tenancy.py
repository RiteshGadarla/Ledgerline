import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Dataset, DatasetFile, ExceptionDecision, Run, Session, User
from db.passwords import verify_password

RunSource = Literal["demo", "dataset"]
RunState = Literal["queued", "normalising", "matching", "triaging", "explaining", "scoring", "complete", "failed"]
Decision = Literal["approved", "rejected"]
DatasetSource = Literal["generated", "uploaded"]
DatasetStatus = Literal["incomplete", "ready"]
DatasetRole = Literal["ledger", "gateway", "settlement", "bank"]

REQUIRED_DATASET_ROLES: tuple[DatasetRole, ...] = ("ledger", "gateway", "settlement", "bank")

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
    # The adversarial corruptions this run was put through, normalised. Carried
    # on the record so the run's own URL reproduces exactly what was tested.
    mutations: list[str] | None
    state: RunState
    error: str | None
    result_json: str | None
    metrics_json: str | None
    forecast_json: str | None
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
        mutations=run.mutations,
        state=cast("RunState", run.state),
        error=run.error,
        result_json=run.result_json,
        metrics_json=run.metrics_json,
        forecast_json=run.forecast_json,
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


async def list_runs_for_user(db: AsyncSession, user_id: str, limit: int = 50) -> list[RunRecord]:
    result = await db.execute(
        select(Run).where(Run.user_id == user_id).order_by(Run.created_at.desc()).limit(limit)
    )
    return [_to_run_record(run) for run in result.scalars()]


async def transition_run_state(db: AsyncSession, run_id: str, state: RunState) -> None:
    await db.execute(update(Run).where(Run.id == run_id).values(state=state, updated_at=datetime.now(UTC)))
    await db.commit()


async def complete_run(
    db: AsyncSession, run_id: str, result_json: str, metrics_json: str, forecast_json: str | None = None
) -> None:
    await db.execute(
        update(Run)
        .where(Run.id == run_id)
        .values(
            state="complete",
            result_json=result_json,
            metrics_json=metrics_json,
            forecast_json=forecast_json,
            updated_at=datetime.now(UTC),
        )
    )
    await db.commit()


async def fail_run(db: AsyncSession, run_id: str, error: str) -> None:
    await db.execute(
        update(Run).where(Run.id == run_id).values(state="failed", error=error, updated_at=datetime.now(UTC))
    )
    await db.commit()


@dataclass(frozen=True)
class ExceptionDecisionRecord:
    exception_id: str
    decision: Decision
    note: str | None
    created_at: datetime


def _to_decision_record(row: ExceptionDecision) -> ExceptionDecisionRecord:
    return ExceptionDecisionRecord(
        exception_id=row.exception_id, decision=cast("Decision", row.decision), note=row.note, created_at=row.created_at
    )


async def record_exception_decision(
    db: AsyncSession, run_id: str, user_id: str, exception_id: str, decision: Decision, note: str | None = None
) -> ExceptionDecisionRecord | None:
    """None means the run doesn't exist for this user -- the router turns
    that into a 404, same as every other tenant-scoped lookup. A second
    decision on the same exception overwrites the first rather than stacking
    (approve-then-reject is a correction, not two separate facts)."""
    run = await get_run_for_user(db, run_id, user_id)
    if run is None:
        return None

    existing = await db.execute(
        select(ExceptionDecision).where(
            ExceptionDecision.run_id == run_id, ExceptionDecision.exception_id == exception_id
        )
    )
    row = existing.scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None:
        row = ExceptionDecision(
            id=str(uuid.uuid4()), run_id=run_id, exception_id=exception_id, decision=decision, note=note, created_at=now
        )
        db.add(row)
    else:
        row.decision = decision
        row.note = note
        row.created_at = now
    await db.commit()
    await db.refresh(row)
    return _to_decision_record(row)


async def list_exception_decisions(db: AsyncSession, run_id: str, user_id: str) -> list[ExceptionDecisionRecord] | None:
    run = await get_run_for_user(db, run_id, user_id)
    if run is None:
        return None
    result = await db.execute(select(ExceptionDecision).where(ExceptionDecision.run_id == run_id))
    return [_to_decision_record(row) for row in result.scalars()]


@dataclass(frozen=True)
class DatasetRecord:
    id: str
    user_id: str
    name: str
    source: DatasetSource
    seed: int | None
    size: int | None
    status: DatasetStatus
    truth_json: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class DatasetFileRecord:
    id: str
    dataset_id: str
    role: DatasetRole
    raw_filename: str | None
    raw_content_type: str | None
    records_json: str
    row_count: int
    valid_count: int
    created_at: datetime


def _to_dataset_record(row: Dataset) -> DatasetRecord:
    return DatasetRecord(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        source=cast("DatasetSource", row.source),
        seed=row.seed,
        size=row.size,
        status=cast("DatasetStatus", row.status),
        truth_json=row.truth_json,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_dataset_file_record(row: DatasetFile) -> DatasetFileRecord:
    return DatasetFileRecord(
        id=row.id,
        dataset_id=row.dataset_id,
        role=cast("DatasetRole", row.role),
        raw_filename=row.raw_filename,
        raw_content_type=row.raw_content_type,
        records_json=row.records_json,
        row_count=row.row_count,
        valid_count=row.valid_count,
        created_at=row.created_at,
    )


async def create_dataset(
    db: AsyncSession,
    user_id: str,
    name: str,
    source: DatasetSource,
    seed: int | None = None,
    size: int | None = None,
    truth_json: str | None = None,
) -> DatasetRecord:
    now = datetime.now(UTC)
    dataset = Dataset(
        id=str(uuid.uuid4()),
        user_id=user_id,
        name=name,
        source=source,
        seed=seed,
        size=size,
        status="incomplete",
        truth_json=truth_json,
        created_at=now,
        updated_at=now,
    )
    db.add(dataset)
    await db.commit()
    await db.refresh(dataset)
    return _to_dataset_record(dataset)


async def dataset_name_taken(db: AsyncSession, user_id: str, name: str) -> bool:
    """Names are unique per user (ix_datasets_user_name), so this is the
    friendly pre-check in front of that index -- the index is still what
    guarantees it under concurrent creates."""
    result = await db.execute(select(Dataset.id).where(Dataset.user_id == user_id, Dataset.name == name))
    return result.first() is not None


async def get_dataset_for_user(db: AsyncSession, dataset_id: str, user_id: str) -> DatasetRecord | None:
    """Scoped by user_id, same as every other tenant-scoped lookup: a dataset
    belonging to another user is indistinguishable from a nonexistent one."""
    result = await db.execute(select(Dataset).where(Dataset.id == dataset_id, Dataset.user_id == user_id))
    dataset = result.scalar_one_or_none()
    return _to_dataset_record(dataset) if dataset is not None else None


async def list_datasets_for_user(db: AsyncSession, user_id: str, limit: int = 50) -> list[DatasetRecord]:
    result = await db.execute(
        select(Dataset).where(Dataset.user_id == user_id).order_by(Dataset.created_at.desc()).limit(limit)
    )
    return [_to_dataset_record(row) for row in result.scalars()]


async def list_dataset_files(db: AsyncSession, dataset_id: str) -> list[DatasetFileRecord]:
    """Not itself tenant-scoped -- callers must first confirm ownership of
    dataset_id via get_dataset_for_user, exactly like list_exception_decisions
    relies on its caller having already resolved the parent run."""
    result = await db.execute(select(DatasetFile).where(DatasetFile.dataset_id == dataset_id))
    return [_to_dataset_file_record(row) for row in result.scalars()]


async def upsert_dataset_file(
    db: AsyncSession,
    dataset_id: str,
    role: DatasetRole,
    raw_filename: str | None,
    raw_content_type: str | None,
    raw_content: bytes | None,
    records_json: str,
    row_count: int,
    valid_count: int,
) -> DatasetFileRecord:
    """Re-uploading the same role overwrites the previous file rather than
    stacking, matching the unique (dataset_id, role) index."""
    existing = await db.execute(
        select(DatasetFile).where(DatasetFile.dataset_id == dataset_id, DatasetFile.role == role)
    )
    row = existing.scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None:
        row = DatasetFile(
            id=str(uuid.uuid4()),
            dataset_id=dataset_id,
            role=role,
            raw_filename=raw_filename,
            raw_content_type=raw_content_type,
            raw_content=raw_content,
            records_json=records_json,
            row_count=row_count,
            valid_count=valid_count,
            created_at=now,
        )
        db.add(row)
    else:
        row.raw_filename = raw_filename
        row.raw_content_type = raw_content_type
        row.raw_content = raw_content
        row.records_json = records_json
        row.row_count = row_count
        row.valid_count = valid_count
        row.created_at = now
    await db.commit()
    await db.refresh(row)
    return _to_dataset_file_record(row)


@dataclass(frozen=True)
class DatasetFileRawRecord:
    raw_filename: str | None
    raw_content_type: str | None
    raw_content: bytes | None


async def get_dataset_file_raw(db: AsyncSession, dataset_id: str, role: DatasetRole) -> DatasetFileRawRecord | None:
    """Callers must already have confirmed dataset ownership via
    get_dataset_for_user before calling this -- kept as its own lookup
    (rather than folded into DatasetFileRecord) so listing datasets never
    pulls raw file bytes into memory."""
    result = await db.execute(select(DatasetFile).where(DatasetFile.dataset_id == dataset_id, DatasetFile.role == role))
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return DatasetFileRawRecord(
        raw_filename=row.raw_filename, raw_content_type=row.raw_content_type, raw_content=row.raw_content
    )


async def recompute_dataset_status(db: AsyncSession, dataset_id: str) -> DatasetStatus:
    files = await list_dataset_files(db, dataset_id)
    by_role = {f.role: f for f in files}
    status: DatasetStatus = (
        "ready"
        if all(by_role.get(role) is not None and by_role[role].valid_count > 0 for role in REQUIRED_DATASET_ROLES)
        else "incomplete"
    )
    await db.execute(
        update(Dataset).where(Dataset.id == dataset_id).values(status=status, updated_at=datetime.now(UTC))
    )
    await db.commit()
    return status
