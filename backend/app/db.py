from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ProblemDetailError
from db.base import make_engine, make_session_factory


def init_db(app: FastAPI, database_url: str) -> None:
    engine = make_engine(database_url)
    app.state.db_engine = engine
    app.state.db_session_factory = make_session_factory(engine)


async def dispose_db(app: FastAPI) -> None:
    await app.state.db_engine.dispose()


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    factory = getattr(request.app.state, "db_session_factory", None)
    if factory is None:
        raise ProblemDetailError("database is not configured", status_code=503)
    async with factory() as session:
        yield session
