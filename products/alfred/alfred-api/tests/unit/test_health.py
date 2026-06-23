import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_returns_200():
    """El endpoint /api/health debe responder 200 siempre."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "services" in data
    assert "version" in data


@pytest.mark.asyncio
async def test_health_has_required_services():
    """El health check debe reportar postgres, ollama y opa."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/health")

    services = response.json()["services"]
    assert "postgres" in services
    assert "ollama" in services
    assert "opa" in services
