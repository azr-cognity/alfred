# Alfred — Convenciones del Proyecto

Este archivo es leído automáticamente por el agente Coder antes de generar código.
Seguir estas convenciones es obligatorio. Tienen prioridad sobre cualquier preferencia
del modelo.

---

## Stack

- Python 3.11
- FastAPI
- SQLModel + Pydantic v2
- SQLAlchemy (async) via `AsyncSession`
- pytest + httpx para tests
- structlog para logging (nunca `print()`)
- ruff para linting

---

## Imports correctos

```python
# Tipos — Python 3.11 usa | para unions, Optional sigue siendo válido
from typing import Optional
from datetime import datetime, timezone

# SQLModel
from sqlmodel import SQLModel, Field
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
import sqlalchemy as sa

# FastAPI
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import JSONResponse

# Database
from app.core.database import AsyncSessionLocal, get_session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
```

---

## Modelos SQLModel

```python
# CORRECTO — campos opcionales
class Project(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(max_length=255)
    description: str | None = Field(default=None)

    # CORRECTO — timestamps con timezone
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(sa.DateTime(timezone=True)),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(sa.DateTime(timezone=True)),
    )

# INCORRECTO — nunca usar datetime directamente como tipo de columna SA
# created_at: datetime = Field(sa_column=Column(datetime))  # ❌
```

---

## Queries SQL — regla crítica (ADR-007)

asyncpg no soporta `::type` con parámetros nombrados. Siempre usar `CAST`.

```python
# CORRECTO
await session.execute(
    text("SELECT * FROM projects WHERE id = CAST(:id AS uuid)"),
    {"id": str(project_id)},
)

# INCORRECTO
await session.execute(
    text("SELECT * FROM projects WHERE id = :id::uuid"),  # ❌
    {"id": str(project_id)},
)
```

---

## Schemas Pydantic v2

```python
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime

class ProjectCreate(BaseModel):
    name: str
    description: str | None = None

class ProjectResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
```

---

## Endpoints FastAPI

```python
router = APIRouter()

@router.get("/projects", tags=["Projects"])
async def list_projects(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Siempre incluir docstring."""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text("SELECT id, name FROM projects ORDER BY created_at DESC LIMIT :limit OFFSET :offset"),
            {"limit": limit, "offset": offset},
        )
        items = [dict(r) for r in rows.mappings()]

    # Serializar UUIDs y fechas antes de devolver
    for item in items:
        item["id"] = str(item["id"])

    return {"items": items}
```

---

## Tests pytest

```python
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch
from app.main import app

@pytest.mark.asyncio
async def test_list_projects():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/projects")
    assert response.status_code == 200
    assert "items" in response.json()
```

---

## Structlog (nunca print)

```python
import structlog
logger = structlog.get_logger()

# Uso
logger.info("projects.list", count=len(items))
logger.warning("projects.not_found", id=project_id)
logger.error("projects.db_error", error=str(e))
```

---

## Reglas generales

- Funciones máximo 50 líneas
- Archivos máximo 300 líneas
- Siempre type hints
- Endpoints siempre con `tags=` y docstring
- UUIDs siempre como `str(uuid)` al serializar
- Fechas siempre con `timezone=True` en la columna SA
- Nunca hardcodear paths — usar `settings` o constantes
