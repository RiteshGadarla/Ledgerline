from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.errors import ProblemDetailError, ValidationFailedError
from app.ratelimit import IpRateLimiter
from app.redis_client import get_redis
from app.settings import get_settings
from db.passwords import hash_password
from db.tenancy import (
    DEFAULT_SESSION_TTL_SECONDS,
    UsernameTaken,
    UserRecord,
    authenticate,
    create_session,
    create_user,
    revoke_session_for_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])

REGISTER_LOGIN_LIMIT = 10
REGISTER_LOGIN_WINDOW_SECONDS = 60.0


class UsernameTakenError(ProblemDetailError):
    status_code = 409
    title = "Username taken"


class InvalidCredentialsError(ProblemDetailError):
    status_code = 401
    title = "Invalid credentials"


class Credentials(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: str
    username: str


def _validate_credentials(credentials: Credentials) -> None:
    # By design, the only rejections are emptiness -- no length, character,
    # or complexity rules (Phase 9 spec).
    if not credentials.username:
        raise ValidationFailedError("username must not be empty")
    if not credentials.password:
        raise ValidationFailedError("password must not be empty")


def _set_session_cookie(response: Response, session_id: str, expires_at_seconds: int) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        max_age=expires_at_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.env != "dev",
        path="/",
    )


async def _rate_limit(request: Request, scope: str) -> None:
    client_ip = request.client.host if request.client else "unknown"
    limiter = IpRateLimiter(
        get_redis(request), limit=REGISTER_LOGIN_LIMIT, window_seconds=REGISTER_LOGIN_WINDOW_SECONDS
    )
    await limiter.check(scope, client_ip)


@router.post("/register", response_model=UserOut, status_code=201)
async def register(
    credentials: Credentials, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> UserOut:
    """Creates a user and immediately logs them in, setting the session cookie."""
    await _rate_limit(request, "register")
    _validate_credentials(credentials)

    try:
        user = await create_user(db, credentials.username, hash_password(credentials.password))
    except UsernameTaken as exc:
        raise UsernameTakenError(f"username {credentials.username!r} is already taken") from exc

    session = await create_session(db, user.id)
    _set_session_cookie(response, session.id, DEFAULT_SESSION_TTL_SECONDS)
    return UserOut(id=user.id, username=user.username)


@router.post("/login", response_model=UserOut)
async def login(
    credentials: Credentials, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> UserOut:
    """Verifies credentials and starts a new session, setting the session cookie."""
    await _rate_limit(request, "login")
    _validate_credentials(credentials)

    user = await authenticate(db, credentials.username, credentials.password)
    if user is None:
        raise InvalidCredentialsError("username or password is incorrect")

    session = await create_session(db, user.id)
    _set_session_cookie(response, session.id, DEFAULT_SESSION_TTL_SECONDS)
    return UserOut(id=user.id, username=user.username)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    user: UserRecord = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revokes the caller's current session and clears the session cookie."""
    settings = get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    if session_id is not None:
        await revoke_session_for_user(db, session_id, user.id)
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: UserRecord = Depends(get_current_user)) -> UserOut:
    """Returns the user identified by the request's session cookie."""
    return UserOut(id=user.id, username=user.username)
