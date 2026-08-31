from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.errors import ProblemDetailError
from app.settings import get_settings
from db.tenancy import UserRecord, get_active_session, get_user_by_id


class UnauthorizedError(ProblemDetailError):
    status_code = 401
    title = "Unauthorized"


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> UserRecord:
    session_id = request.cookies.get(get_settings().session_cookie_name)
    if session_id is None:
        raise UnauthorizedError("no session cookie")

    session = await get_active_session(db, session_id)
    if session is None:
        raise UnauthorizedError("session expired or revoked")

    user = await get_user_by_id(db, session.user_id)
    if user is None:
        raise UnauthorizedError("session refers to a deleted user")
    return user
