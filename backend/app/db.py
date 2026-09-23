"""Async engine and session factory. Correct under native uvicorn; see AGENTS.md > Async."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    # Replace a dead pooled connection after a database restart instead of erroring.
    pool_pre_ping=True,
)

SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """One session per request, always closed. Services and routes commit explicitly."""
    async with SessionFactory() as session:
        yield session
