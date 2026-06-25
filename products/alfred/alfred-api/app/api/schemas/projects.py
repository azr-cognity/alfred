"""
Schemas Pydantic v2 para proyectos.

Incluye:
- ProjectCreate: campos para crear un proyecto (name requerido, otros opcionales)
- ProjectUpdate: campos actualizables de forma parcial
- ProjectResponse: respuesta con id y timestamps

Siguen el patrón establecido en app.schemas.runs.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    """Schema para crear un nuevo proyecto."""

    name: str = Field(..., max_length=255)
    description: str | None = None
    repo_path: str | None = None
    acd_path: str | None = None


class ProjectUpdate(BaseModel):
    """Schema para actualizar un proyecto existente (todos los campos opcionales)."""

    name: str | None = Field(None, max_length=255)
    description: str | None = None
    repo_path: str | None = None
    acd_path: str | None = None


class ProjectResponse(BaseModel):
    """Schema de respuesta para un proyecto."""

    id: UUID
    name: str
    description: str | None = None
    repo_path: str | None = None
    acd_path: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}