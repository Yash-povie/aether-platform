import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from shared.models.database import Base

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://aether:aether@postgres:5432/aether",
)

engine = create_async_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,       # discard stale connections silently
    pool_recycle=1800,        # recycle connections every 30 min
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an ``AsyncSession`` for the duration of a single request.

    Usage::

        @app.get("/example")
        async def handler(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(MyModel))
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise