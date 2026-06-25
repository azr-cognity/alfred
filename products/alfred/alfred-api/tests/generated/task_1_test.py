"""
Tests para los schemas Pydantic v2 de proyectos.

Cubrimos:
- Validación de campos requeridos y opcionales
- Restricciones de longitud (max_length)
- Serialización correcta con model_dump()
- Comportamiento from_attributes en ProjectResponse
"""

import pytest
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.api.schemas.projects import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
)


class TestProjectCreate:
    """Tests para el schema ProjectCreate."""

    def test_projectcreate_valid_minimal(self):
        """Caso feliz: crear proyecto con solo nombre requerido."""
        data = {"name": "Mi Proyecto"}
        project = ProjectCreate(**data)
        assert project.name == "Mi Proyecto"
        assert project.description is None
        assert project.repo_path is None
        assert project.acd_path is None

    def test_projectcreate_valid_all_fields(self):
        """Caso feliz: crear proyecto con todos los campos."""
        data = {
            "name": "Mi Proyecto Completo",
            "description": "Descripción del proyecto",
            "repo_path": "/path/to/repo",
            "acd_path": "/path/to/acd",
        }
        project = ProjectCreate(**data)
        assert project.name == data["name"]
        assert project.description == data["description"]
        assert project.repo_path == data["repo_path"]
        assert project.acd_path == data["acd_path"]

    def test_projectcreate_name_required(self):
        """Caso de error: nombre es campo requerido."""
        with pytest.raises(ValueError) as exc_info:
            ProjectCreate(**{})
        errors = str(exc_info.value)
        assert "name" in errors.lower() or "required" in errors.lower()

    def test_projectcreate_name_max_length(self):
        """Caso de error: nombre excede max_length=255."""
        long_name = "x" * 300
        with pytest.raises(ValueError) as exc_info:
            ProjectCreate(name=long_name)
        errors = str(exc_info.value)
        assert "string_too_long" in errors or "max_length" in errors.lower()

    def test_projectcreate_serialization(self):
        """Caso feliz: serialización correcta a dict."""
        data = {"name": "Test", "description": "Desc"}
        project = ProjectCreate(**data)
        result = project.model_dump()
        assert isinstance(result, dict)
        assert result["name"] == "Test"


class TestProjectUpdate:
    """Tests para el schema ProjectUpdate."""

    def test_projectupdate_valid_empty(self):
        """Caso feliz: actualizar con datos vacíos (todos opcionales)."""
        data = {}
        update = ProjectUpdate(**data)
        assert update.name is None
        assert update.description is None
        assert update.repo_path is None
        assert update.acd_path is None

    def test_projectupdate_partial_update(self):
        """Caso feliz: actualizar solo algunos campos."""
        data = {"name": "Nuevo Nombre", "description": "Nueva descripción"}
        update = ProjectUpdate(**data)
        assert update.name == "Nuevo Nombre"
        assert update.description == "Nueva descripción"
        assert update.repo_path is None
        assert update.acd_path is None

    def test_projectupdate_name_max_length(self):
        """Caso de error: nombre excede max_length=255."""
        long_name = "x" * 300
        with pytest.raises(ValueError) as exc_info:
            ProjectUpdate(name=long_name)
        errors = str(exc_info.value)
        assert "string_too_long" in errors or "max_length" in errors.lower()

    def test_projectupdate_serialization(self):
        """Caso feliz: serialización correcta a dict."""
        data = {"name": "Actualizado", "repo_path": "/new/path"}
        update = ProjectUpdate(**data)
        result = update.model_dump()
        assert isinstance(result, dict)
        assert result["name"] == "Actualizado"


class TestProjectResponse:
    """Tests para el schema ProjectResponse."""

    @pytest.fixture
    def sample_data(self):
        """Fixture con datos de ejemplo para ProjectResponse."""
        return {
            "id": uuid4(),
            "name": "Proyecto Respuesta",
            "description": "Descripción respuesta",
            "repo_path": "/path/repo",
            "acd_path": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

    def test_projectresponse_valid(self, sample_data):
        """Caso feliz: crear respuesta con todos los campos requeridos."""
        response = ProjectResponse(**sample_data)
        assert isinstance(response.id, UUID)
        assert response.name == sample_data["name"]
        assert response.description == sample_data["description"]

    def test_projectresponse_optional_fields(self):
        """Caso feliz: campos opcionales pueden ser None."""
        data = {
            "id": uuid4(),
            "name": "Test",
            "description": None,
            "repo_path": None,
            "acd_path": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": None,
        }
        response = ProjectResponse(**data)
        assert response.description is None
        assert response.repo_path is None
        assert response.acd_path is None
        assert response.updated_at is None

    def test_projectresponse_from_attributes(self):
        """Caso feliz: crear desde objeto con atributos (from_attributes=True)."""
        class MockObj:
            id = uuid4()
            name = "Mock Project"
            description = "Mock desc"
            repo_path = "/mock/repo"
            acd_path = None
            created_at = datetime.now(timezone.utc)
            updated_at = datetime.now(timezone.utc)

        response = ProjectResponse.model_validate(MockObj())
        assert isinstance(response.id, UUID)
        assert response.name == "Mock Project"
