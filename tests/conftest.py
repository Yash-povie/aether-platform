import pytest
import asyncio
import os
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from shared.models.database import Base

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://aether:aether@localhost:5432/aether_test"
)


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop scoped to the entire test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def test_engine():
    """Create test database engine and initialise schema."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(test_engine):
    """Yield a transactional session that rolls back after each test."""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def mock_user():
    """Return a fake JWT payload for use in tests."""
    return {
        "sub": "user-123",
        "org_id": "org-456",
        "role": "admin",
        "email": "test@example.com",
    }


@pytest.fixture
def auth_headers(mock_user):
    """Return Authorization headers with a valid test JWT."""
    from shared.auth.jwt_handler import create_access_token
    token = create_access_token(mock_user)
    return {"Authorization": f"Bearer {token}"}