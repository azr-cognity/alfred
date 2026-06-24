"""
Orquestador LangGraph — nodos y funciones de ruteo (S7).

Cambios respecto a S6:
  - tester_node: genera y ejecuta tests pytest para el código aprobado por el Reviewer.
    Si los tests fallan, reencola al Coder con el output de pytest como feedback.
    Máximo MAX_TESTER_RETRIES reintentos por task.
  - dispatcher_node: limpia reviewer_feedback Y tester_feedback al despachar nueva task.
  - coder_node: consume tester_feedback además de reviewer_feedback.
  - route_after_tester: nuevo edge condicional tester -> dispatcher | coder | END.

Nodos:
  - architect_node: corre el Architect, valida el DAG, deja el plan en estado.
  - dispatcher_node: elige la siguiente task lista (orden topológico) o decide
    que el run terminó / falló. Es la ÚNICA autoridad de terminación.
  - coder_node: ejecuta una task con el Coder y registra el step.
  - opa_gate_node: evalúa el output del Coder con OPA antes del Reviewer.
  - reviewer_node: ejecuta el Reviewer sobre la task actual.
  - tester_node: genera y ejecuta tests pytest para el código aprobado.

Funciones de ruteo:
  - route_after_dispatch: dispatcher -> "coder" | END
  - route_after_coder: opa_gate -> "reviewer" | END
  - route_after_reviewer: reviewer -> "dispatcher" | "coder" | END
  - route_after_tester: tester -> "dispatcher" | "coder" | END
"""

from langgraph.graph import END

from app.agents.architect import run_architect, Task
from app.agents.coder import run_coder
from app.agents.coder_tools import read_file
from app.agents.reviewer import run_reviewer, ReviewerResult
from app.agents.tester import run_tester, TesterResult
from app.core.opa import opa

from app.orchestrator.state import (
    MAX_REVIEWER_RETRIES,
    MAX_TESTER_RETRIES,
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

    Limpia reviewer_feedback y tester_feedback al avanzar a nueva task
    para que no contaminen el Coder de la task siguiente.
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
        "reviewer_feedback": None,
        "tester_feedback": None,
    }


async def coder_node(state: GraphState) -> dict:
    """Ejecuta UNA task con el Coder y registra el step.

    S7: consume reviewer_feedback o tester_feedback según cuál esté presente.
    Si ambos están presentes (no debería ocurrir), reviewer_feedback tiene precedencia.
    """
    task_id = state["current_task_id"]
    assert task_id is not None
    task = _task_by_id(state, task_id)

    reviewer_feedback: str | None = state.get("reviewer_feedback")
    tester_feedback: str | None = state.get("tester_feedback")

    # reviewer_feedback tiene precedencia sobre tester_feedback
    active_feedback = reviewer_feedback or tester_feedback

    retry_counts: dict = state.get("retry_counts") or {}
    tester_retry_counts: dict = state.get("tester_retry_counts") or {}

    try:
        result = await run_coder(task, reviewer_feedback=active_feedback)
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

    r_retries = retry_counts.get(task_id, 0)
    t_retries = tester_retry_counts.get(task_id, 0)
    retry_label = ""
    if r_retries:
        retry_label = f" (corrección Reviewer #{r_retries})"
    elif t_retries:
        retry_label = f" (corrección Tester #{t_retries})"

    step = AgentStep(
        task_id=task_id,
        agent=task.agent,
        status="success",
        summary=result.summary + retry_label,
        files_written=result.files_written,
    )
    return {"steps": [step]}


async def opa_gate_node(state: GraphState) -> dict:
    """Evalúa el output del Coder con OPA antes de pasar al Reviewer."""
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

    approved=True  -> status=reviewing_passed (continúa al Tester)
    approved=False y reintentos < MAX -> status=retrying, feedback al Coder
    approved=False y reintentos agotados -> status=failed
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
            "status": "reviewing_passed",
            "reviewer_feedback": None,
        }

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


async def tester_node(state: GraphState) -> dict:
    """Genera y ejecuta tests pytest para el código aprobado por el Reviewer.

    passed=True  -> completed += [task_id], status=dispatching
    passed=False y reintentos < MAX -> status=test_failing, feedback al Coder
    passed=False y reintentos agotados -> status=failed
    """
    task_id = state["current_task_id"]
    assert task_id is not None
    task = _task_by_id(state, task_id)

    coder_step = _last_coder_step(state, task_id)
    files_written = coder_step.files_written if coder_step else []

    tester_feedback: str | None = state.get("tester_feedback")

    result: TesterResult = await run_tester(task, files_written, tester_feedback)

    if result.passed:
        step = AgentStep(
            task_id=task_id,
            agent="tester",
            status="success",
            summary=result.feedback,
            files_written=[result.test_file] if result.test_file else [],
        )
        return {
            "steps": [step],
            "completed": [task_id],
            "status": "dispatching",
            "tester_feedback": None,
        }

    tester_retry_counts: dict = dict(state.get("tester_retry_counts") or {})
    used = tester_retry_counts.get(task_id, 0)

    if used < MAX_TESTER_RETRIES:
        tester_retry_counts[task_id] = used + 1
        step = AgentStep(
            task_id=task_id,
            agent="tester",
            status="failed",
            summary=f"Tests fallaron (reintento {used + 1}/{MAX_TESTER_RETRIES})",
            files_written=[result.test_file] if result.test_file else [],
            error=result.pytest_output[-500:] if result.pytest_output else result.feedback,
        )
        return {
            "steps": [step],
            "tester_retry_counts": tester_retry_counts,
            "tester_feedback": result.feedback,
            "status": "test_failing",
        }

    step = AgentStep(
        task_id=task_id,
        agent="tester",
        status="failed",
        summary=f"Tests fallaron tras {MAX_TESTER_RETRIES} reintentos",
        files_written=[result.test_file] if result.test_file else [],
        error=result.pytest_output[-500:] if result.pytest_output else result.feedback,
    )
    return {
        "steps": [step],
        "status": "failed",
        "error": (
            f"Tester: tests fallaron para task '{task_id}' "
            f"tras {MAX_TESTER_RETRIES} reintentos"
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
    """opa_gate -> reviewer | END"""
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
    """reviewer -> tester | coder | END"""
    status = state["status"]
    if status == "failed":
        return END
    if status == "retrying":
        return "coder"
    if status == "reviewing_passed":
        return "tester"
    return END


def route_after_tester(state: GraphState) -> str:
    """tester -> dispatcher | coder | END"""
    status = state["status"]
    if status == "failed":
        return END
    if status == "test_failing":
        return "coder"
    if status == "dispatching":
        return "dispatcher"
    return END


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
