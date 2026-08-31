from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import Pool


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str, poolclass: type[Pool] | None = None) -> AsyncEngine:
    if poolclass is None:
        return create_async_engine(database_url, pool_pre_ping=True)
    return create_async_engine(database_url, poolclass=poolclass)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
