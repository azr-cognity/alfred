"""
Router de proyectos — CRUD completo.

GET    /api/v1/projects        → lista todos los proyectos (paginación)
POST   /api/v1/projects        → crea un nuevo proyecto
GET    /api/v1/projects/{id}   → detalle por UUID
PATCH  /api/v1/projects/{id}   → actualiza un proyecto existente
"""

import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.database import AsyncSessionLocal

router = APIRouter()
logger = structlog.get_logger(__name__)


class CreateProjectRequest(BaseModel):
    """Solicitud para crear un proyecto."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    repo_path: Optional[str] = None
    acd_path: Optional[str] = None


class UpdateProjectRequest(BaseModel):
    """Solicitud para actualizar un proyecto."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    repo_path: Optional[str] = None
    acd_path: Optional[str] = None


class ProjectResponse(BaseModel):
    """Respuesta de proyecto serializado."""
    id: str
    name: str
    description: Optional[str]
    repo_path: Optional[str]
    acd_path: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


def _serialize_project(row: dict) -> ProjectResponse:
    """
    Serializa una fila de la base de datos a un modelo Pydantic.

    Convierte UUIDs a strings y asegura que las fechas tengan timezone.
    Lanza HTTPException 404 si el proyecto no existe (row es None).
    """
    if row is None:
        return None

    serialized = {
        "id": str(row.get("id", _uuid.uuid4())),
        "name": row.get("name"),
        "description": row.get("description"),
        "repo_path": row.get("repo_path"),
        "acd_path": row.get("acd_path"),
        "created_at": row.get("created_at", datetime.now(timezone.utc)),
    }

    updated = row.get("updated_at")
    if updated is not None:
        serialized["updated_at"] = (
            updated.replace(tzinfo=timezone.utc)
            if hasattr(updated, "tzinfo") and updated.tzinfo is None
            else updated
        )

    return ProjectResponse(**serialized)


@router.get("/projects", tags=["Projects"])
async def list_projects(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """
    Lista todos los proyectos con paginación.

    Args:
        limit: Máximo de resultados (default 20, máximo 100)
        offset: Número de registros a saltar (paginación)

    Returns:
        Dict con lista de proyectos serializados y metadatos de paginación
    """
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text("""
                SELECT id, name, description, repo_path, acd_path,
                       created_at, updated_at
                FROM projects
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"limit": limit, "offset": offset},
        )
        results = [dict(r) for r in rows.mappings()]

    items = [_serialize_project(dict(row)) for row in results if _serialize_project(dict(row))]  # type: ignore[assignment]

    logger.info("projects.list", count=len(items), limit=limit, offset=offset)

    return {
        "items": [item.model_dump() for item in items],
        "pagination": {"total": len(results), "limit": limit, "offset": offset},
    }


@router.get("/projects/{project_id}", tags=["Projects"])
async def get_project(project_id: str) -> ProjectResponse:
    """
    Obtiene un proyecto específico por su UUID.

    Args:
        project_id: El identificador único del proyecto (UUID en formato string)

    Returns:
        Proyecto serializado con todos sus campos

    Raises:
        HTTPException 404 si el proyecto no existe
    """
    async with AsyncSessionLocal() as session:
        row = await session.execute(
            text("SELECT id, name, description, repo_path, acd_path, created_at, updated_at FROM projects WHERE id = CAST(:id AS uuid)"),
            {"id": project_id},
        )
        result = row.mappings().first()

    if not result:
        logger.warning("projects.not_found", project_id=project_id)
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    serialized = _serialize_project(dict(result))  # type: ignore[arg-type]
    logger.info("projects.get", project_id=project_id)

    return serialized


@router.post("/projects", tags=["Projects"])
async def create_project(request: CreateProjectRequest) -> ProjectResponse:
    """
    Crea un nuevo proyecto en la base de datos.

    Args:
        request: Datos del proyecto a crear (name requerido, otros opcionales)

    Returns:
        Proyecto creado con su UUID generado y timestamps

    Raises:
        HTTPException 409 si ya existe un proyecto con el mismo nombre
    """
    async with AsyncSessionLocal() as session:
        project_id = _uuid.uuid4()
        now = datetime.now(timezone.utc)

        await session.execute(
            text("""
                INSERT INTO projects (id, name, description, repo_path, acd_path, created_at, updated_at)
                VALUES (
                    CAST(:project_id AS uuid),
                    :name,
                    :description,
                    :repo_path,
                    :acd_path,
                    :created_at,
                    :updated_at
                )
            """),
            {
                "project_id": str(project_id),
                "name": request.name,
                "description": request.description,
                "repo_path": request.repo_path,
                "acd_path": request.acd_path,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
        )

    logger.info("projects.created", project_id=str(project_id), name=request.name)

    return ProjectResponse(
        id=str(project_id),
        name=request.name,
        description=request.description,
        repo_path=request.repo_path,
        acd_path=request.acd_path,
        created_at=now,
        updated_at=now,
    )


@router.patch("/projects/{project_id}", tags=["Projects"])
async def update_project(project_id: str