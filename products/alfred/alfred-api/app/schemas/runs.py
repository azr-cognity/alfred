from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


# ── Enums ──────────────────────────────────────────────────────────────────────

class RunStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"


class TaskPriority(str, Enum):
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"


# ── Plan del Architect ─────────────────────────────────────────────────────────

class Task(BaseModel):
    """Una tarea atómica dentro del plan del Architect."""
    id: str                          # ej: "task_1"
    title: str                       # ej: "Crear endpoint POST /api/users"
    description: str                 # qué hay que hacer exactamente
    agent: str                       # qué agente la ejecuta: "coder" | "tester" etc.
    priority: TaskPriority
    depends_on: list[str] = []       # ids de tareas que deben completarse antes
    estimated_complexity: str        # "low" | "medium" | "high"
    files_to_create: list[str] = []  # rutas de archivos que se crearán
    files_to_modify: list[str] = []  # rutas de archivos que se modificarán


class Plan(BaseModel):
    """El plan completo producido por el Architect."""
    summary: str           # resumen en 1-2 oraciones de qué se va a hacer
    tasks: list[Task]      # lista ordenada de tareas
    stack_notes: str = ""  # notas sobre el stack elegido para esta tarea
    risks: list[str] = []  # riesgos o consideraciones identificadas


# ── Request / Response de la API ───────────────────────────────────────────────

class CreateRunRequest(BaseModel):
    """Lo que el usuario envía para iniciar un run."""
    prompt: str            # el objetivo en lenguaje natural
    project_id: str | None = None   # opcional por ahora


class RunResponse(BaseModel):
    """Lo que la API devuelve al crear un run."""
    id: UUID
    status: RunStatus
    prompt: str
    plan: Plan | None = None
    current_agent: str | None = None
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
