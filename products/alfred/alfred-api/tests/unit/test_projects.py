"""Tests unitarios para el router de proyectos."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture()
async def async_client() -> AsyncClient:
    """Cliente HTTP asíncrono para tests de endpoints."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


def _mock_session(rows=None, first=None):
    """Helper: construye un AsyncSessionLocal mockeado."""
    mock_result = MagicMock()
    mock_result.mappings.return_value.first.return_value = first

    if rows is not None:
        mock_mappings = MagicMock()
        mock_mappings.__iter__ = MagicMock(return_value=iter(rows))
        mock_result.mappings.return_value = mock_mappings
        mock_result.mappings.return_value.first.return_value = first

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    return mock_ctx, mock_session, mock_result


@pytest.mark.asyncio
async def test_list_projects_returns_200(async_client):
    """GET /api/v1/projects retorna 200 con lista vacía."""
    mock_ctx, mock_session, mock_result = _mock_session(rows=[])
    mock_result.mappings.return_value.__iter__ = MagicMock(return_value=iter([]))

    with patch("app.api.routes.projects.AsyncSessionLocal", return_value=mock_ctx):
        response = await async_client.get("/api/v1/projects")

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "pagination" in data


@pytest.mark.asyncio
async def test_get_project_not_found(async_client):
    """GET /api/v1/projects/{id} retorna 404 si no existe."""
    mock_ctx, mock_session, mock_result = _mock_session(first=None)

    with patch("app.api.routes.projects.AsyncSessionLocal", return_value=mock_ctx):
        response = await async_client.get(
            "/api/v1/projects/00000000-0000-0000-0000-000000000000"
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_project_returns_200(async_client):
    """GET /api/v1/projects/{id} retorna 200 si existe."""
    from datetime import datetime, timezone
    import uuid

    project_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    fake_row = {
        "id": project_id,
        "name": "alfred",
        "description": "test",
        "repo_path": None,
        "acd_path": None,
        "created_at": now,
        "updated_at": None,
    }

    mock_ctx, mock_session, mock_result = _mock_session(first=fake_row)

    with patch("app.api.routes.projects.AsyncSessionLocal", return_value=mock_ctx):
        response = await async_client.get(f"/api/v1/projects/{project_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "alfred"


@pytest.mark.asyncio
async def test_create_project_returns_200(async_client):
    """POST /api/v1/projects retorna 200 con el proyecto creado."""
    mock_ctx, mock_session, _ = _mock_session()

    with patch("app.api.routes.projects.AsyncSessionLocal", return_value=mock_ctx):
        response = await async_client.post(
            "/api/v1/projects",
            json={"name": "test-project", "description": "un proyecto de prueba"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test-project"
    assert "id" in data


@pytest.mark.asyncio
async def test_patch_project_not_found(async_client):
    """PATCH /api/v1/projects/{id} retorna 404 si no existe."""
    mock_ctx, mock_session, mock_result = _mock_session(first=None)

    with patch("app.api.routes.projects.AsyncSessionLocal", return_value=mock_ctx):
        response = await async_client.patch(
            "/api/v1/projects/00000000-0000-0000-0000-000000000000",
            json={"name": "nuevo nombre"},
        )

    assert response.status_code == 404