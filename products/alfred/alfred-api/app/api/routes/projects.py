"""
Router de proyectos — S11.

GET  /api/v1/projects        → lista todos los proyectos
POST /api/v1/projects        → crea un proyecto
GET  /api/v1/projects/{id}   → detalle de un proyecto
"""

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.core.database import AsyncSessionLocal

router = APIRouter()
logger = structlog.get_logger()


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #

class CreateProjectRequest(BaseModel):
    name: str
    description: str | None = None
    repo_path: str | None = None
    acd_path: str | None = None


# --------------------------------------------------------------------------- #
# GET /api/v1/projects
# --------------------------------------------------------------------------- #

@router.get("/projects", tags=["Projects"])
async def list_projects() -> dict:
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text("""
                SELECT id, name, description, repo_path, acd_path, created_at, updated_at
                FROM projects
                ORDER BY created_at DESC
            """)
        )
        projects = [dict(r) for r in rows.mappings()]

    for p in projects:
        p["id"] = str(p["id"])
        p["created_at"] = p["created_at"].isoformat() if p.get("created_at") else None
        p["updated_at"] = p["updated_at"].isoformat() if p.get("updated_at") else None

    return {"projects": projects}


# --------------------------------------------------------------------------- #
# POST /api/v1/projects
# --------------------------------------------------------------------------- #

@router.post("/projects", tags=["Projects"], status_code=201)
async def create_project(body: CreateProjectRequest) -> dict:
    project_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO projects (id, name, description, repo_path, acd_path, created_at, updated_at)
                VALUES (CAST(:id AS uuid), :name, :description, :repo_path, :acd_path, :created_at, :updated_at)
            """),
            {
                "id": str(project_id),
                "name": body.name,
                "description": body.description,
                "repo_path": body.repo_path,
                "acd_path": body.acd_path,
                "created_at": now,
                "updated_at": now,
            },
        )
        await session.commit()

    return {
        "id": str(project_id),
        "name": body.name,
        "description": body.description,
        "repo_path": body.repo_path,
        "acd_path": body.acd_path,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


# --------------------------------------------------------------------------- #
# GET /api/v1/projects/{project_id}
# --------------------------------------------------------------------------- #

@router.get("/projects/{project_id}", tags=["Projects"])
async def get_project(project_id: str) -> dict:
    async with AsyncSessionLocal() as session:
        row = await session.execute(
            text("""
                SELECT id, name, description, repo_path, acd_path, created_at, updated_at
                FROM projects
                WHERE id = CAST(:id AS uuid)
            """),
            {"id": project_id},
        )
        project = row.mappings().first()

    if not project:
        raise HTTPException(status_code=404, detail=f"Proyecto '{project_id}' no encontrado")

    data = dict(project)
    data["id"] = str(data["id"])
    data["created_at"] = data["created_at"].isoformat() if data.get("created_at") else None
    data["updated_at"] = data["updated_at"].isoformat() if data.get("updated_at") else None

    return data
