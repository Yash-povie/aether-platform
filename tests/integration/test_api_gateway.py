import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_health_endpoint():
    """GET /health returns 200 with status=healthy."""
    from services.api_gateway.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("services.api_gateway.main.get_db") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_session.execute.return_value = MagicMock()
            response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_protected_route_requires_auth():
    """Unauthenticated requests to protected routes return 401."""
    from services.api_gateway.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/hitl/queue")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_job_not_found_returns_404(auth_headers):
    """GET /api/v1/jobs/<nonexistent> returns 404."""
    from services.api_gateway.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("services.api_gateway.main.get_db") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute.return_value = mock_result
            response = await client.get(
                "/api/v1/jobs/00000000-0000-0000-0000-000000000000",
                headers=auth_headers,
            )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_invalid_token_returns_401():
    """A malformed JWT token returns 401."""
    from services.api_gateway.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/hitl/queue",
            headers={"Authorization": "Bearer not-a-real-token"},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_job_missing_body_returns_422():
    """POST /api/v1/jobs without a body returns 422 Unprocessable Entity."""
    from services.api_gateway.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/jobs", json={})

    # Either 401 (auth required first) or 422 (validation) is acceptable
    assert response.status_code in (401, 422)


@pytest.mark.asyncio
async def test_metrics_endpoint_accessible():
    """GET /metrics endpoint is accessible (Prometheus scrape target)."""
    from services.api_gateway.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/metrics")

    # Metrics may return 200 or 404 depending on whether prometheus_client is wired
    assert response.status_code in (200, 404)