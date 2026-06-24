"""
Orquestador LangGraph — nodos y funciones de ruteo (S7).

Cambios respecto a S6:
  - reviewer_node: en lugar de fail-fast, reencola al Coder si quedan reintentos
    (máximo MAX_REVIEWER_RETRIES por task). Pasa el feedback como reviewer_feedback.
  - coder_node: si existe reviewer_feedback en el estado, lo incluye en el prompt
    para que el Coder corrija en base al rechazo anterior.
  - route_after_reviewer: nuevo branch "coder" para el caso "retrying".

Nodos:
  - architect_node: corre el Architect, valida el DAG, deja el plan en estado.
  - dispatcher_node: elige la siguiente task lista (orden topológico) o decide
    que el run terminó / falló. Es la ÚNICA autoridad de terminación.
  - coder_node: ejecuta una task con el Coder y registra el step.
  - opa_gate_node: evalúa el output del Coder con OPA antes del Reviewer.
  - reviewer_node: ejecuta el Reviewer sobre la task actual.

Funciones de ruteo (para conditional edges, se cablean en graph.py):
  - route_after_dispatch: dispatcher -> "coder" | END
  - route_after_coder: opa_gate -> "reviewer" | END
  - route_after_reviewer: reviewer -> "dispatcher" | "coder" | END

Los nodos NO escriben en DB. La persistencia de agent_steps + el publish a
Redis viven en el worker que consume graph.astream(...). Así el grafo
se testea aislado, mockeando run_architect / run_coder / run_reviewer.
"""

from langgraph.graph import END

from app.agents.architect import run_architect, Task
from app.agents.coder import run_coder
from app.agents.coder_tools import read_file
from app.agents.reviewer import run_reviewer, ReviewerResult
from app.core.opa import opa

from app.orchestrator.state import (
    MAX_REVIEWER_RETRIES,
    AgentStep,
    DagError,
    GraphState,
    ready_tasks,
    validate_dag,
)


def _task_by_id(state: GraphState, task_id: str) -> Task:
    plan = state["plan"]
    assert plan is not None, "se esperaba un plan en el estado"
    for t in plan.tasks:
        if t.id == task_id:
            return t
    raise KeyError(f"task_id '{task_id}' no está en el plan")


def _language_for_path(path: str) -> str:
    if path.endswith(".py"):
        return "python"
    if path.endswith((".ts", ".tsx", ".js", ".jsx")):
        return "typescript"
    return "unknown"


def _last_coder_step(state: GraphState, task_id: str) -> AgentStep | None:
    for step in reversed(state["steps"]):
        if step.task_id == task_id and step.agent == "coder":
            return step
    return None


# --------------------------------------------------------------------------- #
# Nodos
# --------------------------------------------------------------------------- #

async def architect_node(state: GraphState) -> dict:
    """Planifica: prompt -> Plan. Valida el DAG antes de despachar nada."""
    try:
        plan = await run_architect(state["prompt"])
        validate_dag(plan)
    except DagError as e:
        return {
            "plan": None,
            "status": "failed",
            "error": f"Plan inválido del Architect: {e}",
        }
    except Exception as e:
        return {"plan": None, "status": "failed", "error": f"Architect falló: {e}"}

    return {"plan": plan, "status": "dispatching"}


def dispatcher_node(state: GraphState) -> dict:
    """Elige la siguiente task lista, o decide done/failed.

    Reglas:
      - Si el run ya está failed, no toca nada; el ruteo lo manda a END.
      - Si todas las tasks están completas -> done.
      - Si hay tasks listas -> toma la primera (orden del plan).
      - Si quedan tasks pero ninguna está lista -> deadlock -> failed.
    """
    if state["status"] == "failed":
        return {"current_task_id": None}

    plan = state["plan"]
    assert plan is not None

    completed = state["completed"]
    if len(completed) == len(plan.tasks):
        return {"current_task_id": None, "status": "done"}

    ready = ready_tasks(plan, completed)
    if not ready:
        pending = [t.id for t in plan.tasks if t.id not in set(completed)]
        return {
            "current_task_id": None,
            "status": "failed",
            "error": f"deadlock: tasks pendientes sin dependencias resueltas: {pending}",
        }

    return {
        "current_task_id": ready[0].id,
        "status": "coding",
        "reviewer_feedback": None,  # limpiar feedback de tasks anteriores
    }


async def coder_node(state: GraphState) -> dict:
    """Ejecuta UNA task con el Coder y registra el step.

    S7: si hay reviewer_feedback en el estado, lo inyecta en el prompt
    para que el Coder corrija en base al rechazo anterior del Reviewer.

    Fail-fast: si la task falla de forma irrecuperable, marca el run como failed.
    """
    task_id = state["current_task_id"]
    assert task_id is not None
    task = _task_by_id(state, task_id)

    retry_counts: dict = state.get("retry_counts") or {}
    reviewer_feedback: str | None = state.get("reviewer_feedback")

    try:
        result = await run_coder(task, reviewer_feedback=reviewer_feedback)
    except Exception as e:
        step = AgentStep(
            task_id=task_id,
            agent=task.agent,
            status="failed",
            summary=f"Excepción en el Coder: {e}",
            error=str(e),
        )
        return {
            "steps": [step],
            "status": "failed",
            "error": f"task '{task_id}' falló: {e}",
        }

    if not result.success:
        step = AgentStep(
            task_id=task_id,
            agent=task.agent,
            status="failed",
            summary=result.summary or "el Coder no escribió archivos",
            files_written=result.files_written,
            error="success=False (files_written vacío)",
        )
        return {
            "steps": [step],
            "status": "failed",
            "error": f"task '{task_id}' no produjo archivos",
        }

    attempt_label = f" (reintento {retry_counts.get(task_id, 0)})" if retry_counts.get(task_id) else ""
    step = AgentStep(
        task_id=task_id,
        agent=task.agent,
        status="success",
        summary=result.summary + attempt_label,
        files_written=result.files_written,
    )
    return {"steps": [step]}


async def opa_gate_node(state: GraphState) -> dict:
    """Evalúa el output del Coder con OPA antes de pasar al Reviewer.

    - Toma el último step del Coder para esta task.
    - Construye el payload OPA con los archivos escritos.
    - Si hay violations: status=failed.
    - Si pasa: status=dispatching para que el ruteo mande al Reviewer.
    """
    task_id = state["current_task_id"]
    assert task_id is not None

    coder_step = _last_coder_step(state, task_id)
    if coder_step is None:
        return {
            "status": "failed",
            "error": f"opa_gate: no hay step del Coder para task '{task_id}'",
        }

    files_payload: list[dict] = []
    for path in coder_step.files_written:
        content = await read_file(path)
        if content.startswith("ERROR"):
            continue
        files_payload.append({
            "path": path,
            "content": content,
            "language": _language_for_path(path),
        })

    result = await opa.evaluate("coder", {"files": files_payload})

    if not result.passed:
        feedback = "; ".join(result.violations)
        step = AgentStep(
            task_id=task_id,
            agent="coder",
            status="failed",
            summary="OPA bloqueó el output del Coder",
            files_written=coder_step.files_written,
            error=feedback,
        )
        return {
            "steps": [step],
            "status": "failed",
            "error": f"OPA violations en task '{task_id}': {feedback}",
        }

    return {"status": "dispatching"}


async def reviewer_node(state: GraphState) -> dict:
    """Ejecuta el Reviewer sobre la task actual.

    S7 — lógica de reintentos:
      - approved=True  → completed += [task_id], status=dispatching (igual que S6)
      - approved=False y reintentos < MAX_REVIEWER_RETRIES:
            incrementar retry_counts[task_id], pasar feedback al estado,
            status=retrying → route_after_reviewer manda de vuelta al Coder
      - approved=False y reintentos >= MAX_REVIEWER_RETRIES:
            status=failed (agotados los reintentos)
    """
    task_id = state["current_task_id"]
    assert task_id is not None
    task = _task_by_id(state, task_id)

    coder_step = _last_coder_step(state, task_id)
    files_written = coder_step.files_written if coder_step else []

    result: ReviewerResult = await run_reviewer(task, files_written)

    if result.approved:
        step = AgentStep(
            task_id=task_id,
            agent="reviewer",
            status="success",
            summary=result.feedback,
            files_written=files_written,
        )
        return {
            "steps": [step],
            "completed": [task_id],
            "status": "dispatching",
            "reviewer_feedback": None,
        }

    # Rechazado — verificar contador
    retry_counts: dict = dict(state.get("retry_counts") or {})
    used = retry_counts.get(task_id, 0)

    if used < MAX_REVIEWER_RETRIES:
        retry_counts[task_id] = used + 1
        step = AgentStep(
            task_id=task_id,
            agent="reviewer",
            status="failed",
            summary=f"Rechazado (reintento {used + 1}/{MAX_REVIEWER_RETRIES}): {result.feedback}",
            files_written=files_written,
            error=result.feedback,
        )
        return {
            "steps": [step],
            "retry_counts": retry_counts,
            "reviewer_feedback": result.feedback,
            "status": "retrying",
        }

    # Reintentos agotados
    step = AgentStep(
        task_id=task_id,
        agent="reviewer",
        status="failed",
        summary=f"Rechazado tras {MAX_REVIEWER_RETRIES} reintentos: {result.feedback}",
        files_written=files_written,
        error=result.feedback,
    )
    return {
        "steps": [step],
        "status": "failed",
        "error": (
            f"Reviewer rechazó task '{task_id}' tras {MAX_REVIEWER_RETRIES} "
            f"reintentos: {result.feedback}"
        ),
    }


# --------------------------------------------------------------------------- #
# Ruteo (conditional edges)
# --------------------------------------------------------------------------- #

def route_after_dispatch(state: GraphState) -> str:
    task_id = state["current_task_id"]
    if task_id is None:
        return END
    task = _task_by_id(state, task_id)
    if task.agent == "coder":
        return "coder"
    return "skip"


def route_after_coder(state: GraphState) -> str:
    """Conditional edge tras opa_gate.

    - OPA bloqueó (status=failed) -> END
    - OPA pasó y task.agent == "coder" -> "reviewer"
    """
    if state["status"] == "failed":
        return END

    task_id = state["current_task_id"]
    if task_id is None:
        return END

    task = _task_by_id(state, task_id)
    if state["status"] == "dispatching" and task.agent == "coder":
        return "reviewer"
    return END


def route_after_reviewer(state: GraphState) -> str:
    """Conditional edge tras reviewer_node.

    - failed (reintentos agotados) -> END
    - retrying (hay reintentos disponibles) -> "coder"
    - dispatching (aprobado) -> "dispatcher"
    """
    status = state["status"]
    if status == "failed":
        return END
    if status == "retrying":
        return "coder"
    return "dispatcher"


def skip_node(state: GraphState) -> dict:
    task_id = state["current_task_id"]
    assert task_id is not None
    task = _task_by_id(state, task_id)
    step = AgentStep(
        task_id=task_id,
        agent=task.agent,
        status="skipped",
        summary=f"agente '{task.agent}' aún no implementado (skip)",
    )
    return {"steps": [step], "completed": [task_id], "status": "dispatching"}
