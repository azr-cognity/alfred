"""
Orquestador LangGraph — nodos y funciones de ruteo (S8).

Cambios respecto a S7:
  - coder_node: agrega files_written a all_files_written (acumulativo).
  - dispatcher_node: cuando todas las tasks están completas emite
    status=auditing en lugar de done. El Auditor es quien cierra el run.
  - auditor_node: nuevo nodo final. Corre Bandit+semgrep, abre PR si pasa.
    Si hay findings HIGH: status=failed. Si pasa: status=done.
  - route_after_dispatch: nuevo branch "auditor" para status=auditing.
  - route_after_auditor: auditor -> END siempre (es el nodo terminal).
"""

from langgraph.graph import END

from app.agents.architect import run_architect, Task
from app.agents.auditor import run_auditor, AuditorResult
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
        return {"plan": None, "status": "failed", "error": f"Plan inválido: {e}"}
    except Exception as e:
        return {"plan": None, "status": "failed", "error": f"Architect falló: {e}"}
    return {"plan": plan, "status": "dispatching"}


def dispatcher_node(state: GraphState) -> dict:
    """Elige la siguiente task lista, o decide auditing/failed.

    S8: cuando todas las tasks están completas emite status=auditing
    en lugar de done. El Auditor es el nodo terminal que emite done.
    """
    if state["status"] == "failed":
        return {"current_task_id": None}

    plan = state["plan"]
    assert plan is not None

    completed = state["completed"]
    if len(completed) == len(plan.tasks):
        # Todas completas → pasar al Auditor
        return {
            "current_task_id": None,
            "status": "auditing",
        }

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
    """Ejecuta UNA task con el Coder.

    S8: agrega files_written a all_files_written para que el Auditor
    tenga acceso a todos los archivos del run al final.
    """
    task_id = state["current_task_id"]
    assert task_id is not None
    task = _task_by_id(state, task_id)

    reviewer_feedback: str | None = state.get("reviewer_feedback")
    tester_feedback: str | None = state.get("tester_feedback")
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
        return {"steps": [step], "status": "failed", "error": f"task '{task_id}' falló: {e}"}

    if not result.success:
        step = AgentStep(
            task_id=task_id,
            agent=task.agent,
            status="failed",
            summary=result.summary or "el Coder no escribió archivos",
            files_written=result.files_written,
            error="success=False",
        )
        return {"steps": [step], "status": "failed", "error": f"task '{task_id}' no produjo archivos"}

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
    return {
        "steps": [step],
        "all_files_written": result.files_written,  # acumulativo via operator.add
    }


async def opa_gate_node(state: GraphState) -> dict:
    """Evalúa el output del Coder con OPA antes de pasar al Reviewer."""
    task_id = state["current_task_id"]
    assert task_id is not None

    coder_step = _last_coder_step(state, task_id)
    if coder_step is None:
        return {"status": "failed", "error": f"opa_gate: no hay step del Coder para task '{task_id}'"}

    files_payload: list[dict] = []
    for path in coder_step.files_written:
        content = await read_file(path)
        if content.startswith("ERROR"):
            continue
        files_payload.append({"path": path, "content": content, "language": _language_for_path(path)})

    result = await opa.evaluate("coder", {"files": files_payload})

    if not result.passed:
        feedback = "; ".join(result.violations)
        step = AgentStep(
            task_id=task_id, agent="coder", status="failed",
            summary="OPA bloqueó el output del Coder",
            files_written=coder_step.files_written, error=feedback,
        )
        return {"steps": [step], "status": "failed", "error": f"OPA violations en task '{task_id}': {feedback}"}

    return {"status": "dispatching"}


async def reviewer_node(state: GraphState) -> dict:
    """Ejecuta el Reviewer sobre la task actual."""
    task_id = state["current_task_id"]
    assert task_id is not None
    task = _task_by_id(state, task_id)

    coder_step = _last_coder_step(state, task_id)
    files_written = coder_step.files_written if coder_step else []

    result: ReviewerResult = await run_reviewer(task, files_written)

    if result.approved:
        step = AgentStep(
            task_id=task_id, agent="reviewer", status="success",
            summary=result.feedback, files_written=files_written,
        )
        return {"steps": [step], "status": "reviewing_passed", "reviewer_feedback": None}

    retry_counts: dict = dict(state.get("retry_counts") or {})
    used = retry_counts.get(task_id, 0)

    if used < MAX_REVIEWER_RETRIES:
        retry_counts[task_id] = used + 1
        step = AgentStep(
            task_id=task_id, agent="reviewer", status="failed",
            summary=f"Rechazado (reintento {used + 1}/{MAX_REVIEWER_RETRIES}): {result.feedback}",
            files_written=files_written, error=result.feedback,
        )
        return {"steps": [step], "retry_counts": retry_counts, "reviewer_feedback": result.feedback, "status": "retrying"}

    step = AgentStep(
        task_id=task_id, agent="reviewer", status="failed",
        summary=f"Rechazado tras {MAX_REVIEWER_RETRIES} reintentos: {result.feedback}",
        files_written=files_written, error=result.feedback,
    )
    return {
        "steps": [step], "status": "failed",
        "error": f"Reviewer rechazó task '{task_id}' tras {MAX_REVIEWER_RETRIES} reintentos: {result.feedback}",
    }


async def tester_node(state: GraphState) -> dict:
    """Genera y ejecuta tests pytest para el código aprobado por el Reviewer."""
    task_id = state["current_task_id"]
    assert task_id is not None
    task = _task_by_id(state, task_id)

    coder_step = _last_coder_step(state, task_id)
    files_written = coder_step.files_written if coder_step else []
    tester_feedback: str | None = state.get("tester_feedback")

    result: TesterResult = await run_tester(task, files_written, tester_feedback)

    if result.passed:
        step = AgentStep(
            task_id=task_id, agent="tester", status="success",
            summary=result.feedback,
            files_written=[result.test_file] if result.test_file else [],
        )
        return {"steps": [step], "completed": [task_id], "status": "dispatching", "tester_feedback": None}

    tester_retry_counts: dict = dict(state.get("tester_retry_counts") or {})
    used = tester_retry_counts.get(task_id, 0)

    if used < MAX_TESTER_RETRIES:
        tester_retry_counts[task_id] = used + 1
        step = AgentStep(
            task_id=task_id, agent="tester", status="failed",
            summary=f"Tests fallaron (reintento {used + 1}/{MAX_TESTER_RETRIES})",
            files_written=[result.test_file] if result.test_file else [],
            error=result.pytest_output[-500:] if result.pytest_output else result.feedback,
        )
        return {"steps": [step], "tester_retry_counts": tester_retry_counts, "tester_feedback": result.feedback, "status": "test_failing"}

    step = AgentStep(
        task_id=task_id, agent="tester", status="failed",
        summary=f"Tests fallaron tras {MAX_TESTER_RETRIES} reintentos",
        files_written=[result.test_file] if result.test_file else [],
        error=result.pytest_output[-500:] if result.pytest_output else result.feedback,
    )
    return {
        "steps": [step], "status": "failed",
        "error": f"Tester: tests fallaron para task '{task_id}' tras {MAX_TESTER_RETRIES} reintentos",
    }


async def auditor_node(state: GraphState) -> dict:
    """Corre Bandit+semgrep sobre todos los archivos del run y abre PR en GitHub.

    - findings HIGH  -> status=failed, feedback para el usuario
    - sin HIGH       -> status=done, PR abierta en GitHub
    """
    run_id = state["run_id"]
    plan = state["plan"]
    plan_summary = plan.summary if plan else "Run sin plan"
    all_files = list(dict.fromkeys(state.get("all_files_written") or []))  # dedup preservando orden

    log_step = AgentStep(task_id="__audit__", agent="auditor", status="running", summary="Iniciando auditoría")

    result: AuditorResult = await run_auditor(
        run_id=run_id,
        plan_summary=plan_summary,
        files_written=all_files,
    )

    if not result.passed:
        step = AgentStep(
            task_id="__audit__", agent="auditor", status="failed",
            summary=f"Audit falló: {len(result.high_findings)} finding(s) HIGH",
            error=result.feedback,
        )
        return {"steps": [step], "status": "failed", "error": result.feedback}

    step = AgentStep(
        task_id="__audit__", agent="auditor", status="success",
        summary=result.feedback,
    )
    return {"steps": [step], "status": "done"}


# --------------------------------------------------------------------------- #
# Ruteo
# --------------------------------------------------------------------------- #

def route_after_dispatch(state: GraphState) -> str:
    """dispatcher -> coder | auditor | skip | END"""
    status = state["status"]
    if status == "auditing":
        return "auditor"
    if status == "failed":
        return END
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


def route_after_auditor(state: GraphState) -> str:
    """auditor -> END siempre (es el nodo terminal del run)."""
    return END


def skip_node(state: GraphState) -> dict:
    task_id = state["current_task_id"]
    assert task_id is not None
    task = _task_by_id(state, task_id)
    step = AgentStep(
        task_id=task_id, agent=task.agent, status="skipped",
        summary=f"agente '{task.agent}' aún no implementado (skip)",
    )
    return {"steps": [step], "completed": [task_id], "status": "dispatching"}