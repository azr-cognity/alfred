"""
Tests del grafo LangGraph — S7.

Cubre:
  - Happy path: 2 tasks coder + 1 skip, Reviewer aprueba a la primera
  - Retry único: Reviewer rechaza en intento 1, aprueba en intento 2
  - Reintentos agotados: Reviewer rechaza 3 veces (1 original + 2 reintentos) -> failed
  - OPA bloqueando: opa_gate falla -> run failed sin llegar al Reviewer

Ejecutar desde alfred-api/:
  pytest tests/test_graph.py -v
"""

import unittest.mock as mock

import pytest

from app.agents.architect import Plan, Task
from app.agents.coder import CoderResult
from app.agents.reviewer import ReviewerResult
from app.orchestrator.state import MAX_REVIEWER_RETRIES

# --------------------------------------------------------------------------- #
# Fixtures compartidos
# --------------------------------------------------------------------------- #

def _make_plan(tasks: list[Task]) -> Plan:
    return Plan(summary="Plan de prueba", stack_notes="FastAPI", risks=[], tasks=tasks)


def _coder_task(tid: str, depends_on: list[str] | None = None) -> Task:
    return Task(
        id=tid,
        title=f"Tarea {tid}",
        description=f"Implementar {tid}",
        agent="coder",
        priority="high",
        depends_on=depends_on or [],
        estimated_complexity="low",
        files_to_create=[f"app/{tid}.py"],
        files_to_modify=[],
    )


def _skip_task(tid: str, depends_on: list[str] | None = None) -> Task:
    return Task(
        id=tid,
        title=f"Tarea skip {tid}",
        description="agente no implementado",
        agent="tester",
        priority="low",
        depends_on=depends_on or [],
        estimated_complexity="low",
        files_to_create=[],
        files_to_modify=[],
    )


def _fake_coder_result(task: Task, reviewer_feedback=None) -> CoderResult:
    return CoderResult(
        task_id=task.id,
        files_written=[f"app/{task.id}.py"],
        summary=f"Implementé {task.id}",
    )


def _opa_pass():
    return type("OpaResult", (), {"passed": True, "violations": []})()


def _opa_fail(msg: str = "hardcoded secret"):
    return type("OpaResult", (), {"passed": False, "violations": [msg]})()


def _initial_state(run_id: str = "test-run", prompt: str = "test prompt") -> dict:
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
    }


# --------------------------------------------------------------------------- #
# Test 1: Happy path
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_happy_path_two_coder_tasks_and_skip():
    """Reviewer aprueba todo a la primera. 2 coder tasks + 1 skip."""
    plan = _make_plan([
        _coder_task("task_1"),
        _coder_task("task_2", depends_on=["task_1"]),
        _skip_task("task_3", depends_on=["task_2"]),
    ])

    async def fake_architect(prompt):
        return plan

    reviewer_approved = ReviewerResult(approved=True, feedback="OK", task_id="")

    with (
        mock.patch("app.orchestrator.nodes.run_architect", fake_architect),
        mock.patch("app.orchestrator.nodes.run_coder", side_effect=_fake_coder_result),
        mock.patch("app.orchestrator.nodes.run_reviewer", return_value=reviewer_approved),
        mock.patch("app.orchestrator.nodes.opa.evaluate", return_value=_opa_pass()),
        mock.patch("app.orchestrator.nodes.read_file", return_value="# código"),
    ):
        from app.orchestrator.graph import compiled_graph

        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "done"
    assert set(final["completed"]) == {"task_1", "task_2", "task_3"}
    assert final["error"] is None

    agent_names = [s.agent for s in final["steps"]]
    assert "coder" in agent_names
    assert "reviewer" in agent_names


# --------------------------------------------------------------------------- #
# Test 2: Retry único — Reviewer rechaza en intento 1, aprueba en intento 2
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_reviewer_retry_approves_on_second_attempt():
    """Reviewer rechaza una vez, el Coder corrige, y aprueba en el segundo intento."""
    plan = _make_plan([_coder_task("task_1")])

    async def fake_architect(prompt):
        return plan

    call_count = {"n": 0}

    async def reviewer_first_reject_then_approve(task, files_written):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return ReviewerResult(approved=False, feedback="Falta manejo de errores", task_id=task.id)
        return ReviewerResult(approved=True, feedback="Corregido correctamente", task_id=task.id)

    with (
        mock.patch("app.orchestrator.nodes.run_architect", fake_architect),
        mock.patch("app.orchestrator.nodes.run_coder", side_effect=_fake_coder_result),
        mock.patch("app.orchestrator.nodes.run_reviewer", side_effect=reviewer_first_reject_then_approve),
        mock.patch("app.orchestrator.nodes.opa.evaluate", return_value=_opa_pass()),
        mock.patch("app.orchestrator.nodes.read_file", return_value="# código"),
    ):
        from app.orchestrator.graph import compiled_graph

        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "done"
    assert "task_1" in final["completed"]
    assert call_count["n"] == 2  # un rechazo + una aprobación

    reviewer_steps = [s for s in final["steps"] if s.agent == "reviewer"]
    assert len(reviewer_steps) == 2
    assert reviewer_steps[0].status == "failed"
    assert reviewer_steps[1].status == "success"


# --------------------------------------------------------------------------- #
# Test 3: Reintentos agotados -> run failed
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_reviewer_exhausts_retries_fails_run():
    """Reviewer rechaza MAX_REVIEWER_RETRIES + 1 veces -> run failed."""
    plan = _make_plan([_coder_task("task_1")])

    async def fake_architect(prompt):
        return plan

    async def reviewer_always_rejects(task, files_written):
        return ReviewerResult(approved=False, feedback="Siempre falla", task_id=task.id)

    with (
        mock.patch("app.orchestrator.nodes.run_architect", fake_architect),
        mock.patch("app.orchestrator.nodes.run_coder", side_effect=_fake_coder_result),
        mock.patch("app.orchestrator.nodes.run_reviewer", side_effect=reviewer_always_rejects),
        mock.patch("app.orchestrator.nodes.opa.evaluate", return_value=_opa_pass()),
        mock.patch("app.orchestrator.nodes.read_file", return_value="# código"),
    ):
        from app.orchestrator.graph import compiled_graph

        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "failed"
    assert "task_1" not in final["completed"]
    assert "task_1" in final["error"]

    reviewer_steps = [s for s in final["steps"] if s.agent == "reviewer"]
    # 1 intento original + MAX_REVIEWER_RETRIES reintentos = MAX+1 pasos del reviewer
    assert len(reviewer_steps) == MAX_REVIEWER_RETRIES + 1
    assert all(s.status == "failed" for s in reviewer_steps)


# --------------------------------------------------------------------------- #
# Test 4: OPA bloquea -> run failed antes de llegar al Reviewer
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_opa_gate_blocks_run():
    """OPA detecta una violación y el run falla sin llamar al Reviewer."""
    plan = _make_plan([_coder_task("task_1")])

    async def fake_architect(prompt):
        return plan

    reviewer_mock = mock.AsyncMock()

    with (
        mock.patch("app.orchestrator.nodes.run_architect", fake_architect),
        mock.patch("app.orchestrator.nodes.run_coder", side_effect=_fake_coder_result),
        mock.patch("app.orchestrator.nodes.run_reviewer", reviewer_mock),
        mock.patch("app.orchestrator.nodes.opa.evaluate", return_value=_opa_fail("hardcoded secret")),
        mock.patch("app.orchestrator.nodes.read_file", return_value="SECRET='abc123'"),
    ):
        from app.orchestrator.graph import compiled_graph

        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "failed"
    assert "hardcoded secret" in final["error"]
    reviewer_mock.assert_not_called()


# --------------------------------------------------------------------------- #
# Test 5: feedback del Reviewer llega al Coder en el reintento
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_reviewer_feedback_passed_to_coder_on_retry():
    """El feedback del Reviewer se pasa como reviewer_feedback al Coder en el reintento."""
    plan = _make_plan([_coder_task("task_1")])

    async def fake_architect(prompt):
        return plan

    coder_calls: list[dict] = []

    async def coder_spy(task, reviewer_feedback=None):
        coder_calls.append({"task_id": task.id, "feedback": reviewer_feedback})
        return CoderResult(
            task_id=task.id,
            files_written=[f"app/{task.id}.py"],
            summary="ok",
        )

    call_count = {"n": 0}

    async def reviewer_reject_once(task, files_written):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return ReviewerResult(approved=False, feedback="Añade type hints", task_id=task.id)
        return ReviewerResult(approved=True, feedback="Perfecto", task_id=task.id)

    with (
        mock.patch("app.orchestrator.nodes.run_architect", fake_architect),
        mock.patch("app.orchestrator.nodes.run_coder", side_effect=coder_spy),
        mock.patch("app.orchestrator.nodes.run_reviewer", side_effect=reviewer_reject_once),
        mock.patch("app.orchestrator.nodes.opa.evaluate", return_value=_opa_pass()),
        mock.patch("app.orchestrator.nodes.read_file", return_value="# código"),
    ):
        from app.orchestrator.graph import compiled_graph

        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "done"
    assert len(coder_calls) == 2
    # Primer intento: sin feedback
    assert coder_calls[0]["feedback"] is None
    # Segundo intento: con feedback del Reviewer
    assert coder_calls[1]["feedback"] == "Añade type hints"