"""
Orquestador LangGraph — estado del grafo (S7).

Cambios respecto a S6:
  - retry_counts: dict[str, int]  — contador de reintentos Reviewer por task_id
  - reviewer_feedback: Optional[str]  — feedback del último rechazo del Reviewer
  - tester_retry_counts: dict[str, int]  — contador de reintentos Tester por task_id
  - tester_feedback: Optional[str]  — feedback del último fallo del Tester
  - initial_state(): función helper que el worker usa para arrancar el grafo
"""

import operator
from typing import Annotated, Optional

from pydantic import BaseModel

from app.schemas.runs import Plan, RunStatus

MAX_REVIEWER_RETRIES = 2
MAX_TESTER_RETRIES = 2


class DagError(Exception):
    """El plan del Architect tiene un DAG inválido."""


class AgentStep(BaseModel):
    """Registro de una ejecución de agente dentro de un run."""

    task_id: str
    agent: str
    status: str  # "success" | "failed" | "skipped"
    summary: str = ""
    files_written: list[str] = []
    error: Optional[str] = None


class GraphState(dict):
    """Estado compartido del grafo LangGraph.

    Campos acumulativos (Annotated con operator.add):
      - completed: lista de task_ids completados
      - steps: historial de pasos de cada agente

    Campos escalares (reemplazados en cada update):
      - run_id, prompt, plan, current_task_id, status, error
      - retry_counts: dict task_id -> nº de reintentos del Reviewer
      - reviewer_feedback: feedback del último rechazo del Reviewer
      - tester_retry_counts: dict task_id -> nº de reintentos del Tester
      - tester_feedback: output del último fallo del Tester
    """

    run_id: str
    prompt: str
    plan: Optional[Plan]
    current_task_id: Optional[str]
    completed: Annotated[list[str], operator.add]
    steps: Annotated[list[AgentStep], operator.add]
    status: RunStatus
    error: Optional[str]
    retry_counts: dict         # task_id -> int (Reviewer)
    reviewer_feedback: Optional[str]
    tester_retry_counts: dict  # task_id -> int (Tester)
    tester_feedback: Optional[str]


def initial_state(run_id: str, prompt: str) -> dict:
    """Construye el estado inicial del grafo para un nuevo run.

    Args:
        run_id: ID del run (UUID generado por la API)
        prompt: prompt del usuario

    Returns:
        dict compatible con GraphState para pasar a compiled_graph.astream()
    """
    return {
        "run_id": run_id,
        "prompt": prompt,
        "plan": None,
        "current_task_id": None,
        "completed": [],
        "steps": [],
        "status": "queued",
        "error": None,
        "retry_counts": {},
        "reviewer_feedback": None,
        "tester_retry_counts": {},
        "tester_feedback": None,
    }


# --------------------------------------------------------------------------- #
# Helpers de DAG
# --------------------------------------------------------------------------- #

def validate_dag(plan: Plan) -> None:
    """Valida que el plan no tenga ciclos ni dependencias rotas."""
    ids = {t.id for t in plan.tasks}
    for task in plan.tasks:
        for dep in task.depends_on:
            if dep not in ids:
                raise DagError(
                    f"task '{task.id}' depende de '{dep}' que no existe en el plan"
                )

    # Detección de ciclos con Kahn
    from collections import defaultdict, deque

    in_degree: dict[str, int] = {t.id: 0 for t in plan.tasks}
    adj: dict[str, list[str]] = defaultdict(list)
    for task in plan.tasks:
        for dep in task.depends_on:
            adj[dep].append(task.id)
            in_degree[task.id] += 1

    queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited != len(plan.tasks):
        raise DagError("el plan contiene un ciclo — imposible ordenar tasks")


def ready_tasks(plan: Plan, completed: list[str]) -> list:
    """Retorna las tasks cuyas dependencias ya están completas, en orden del plan."""
    done = set(completed)
    return [
        t for t in plan.tasks
        if t.id not in done and all(dep in done for dep in t.depends_on)
    ]
