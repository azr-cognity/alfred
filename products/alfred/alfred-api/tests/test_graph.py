"""
Tests del grafo LangGraph — S7 completo (Architect→Coder→OPA→Reviewer→Tester).

Cubre:
  - Happy path: pipeline completo, Tester aprueba a la primera
  - Tester falla una vez, Coder corrige, Tester aprueba en segundo intento
  - Tester agota reintentos -> run failed
  - Reviewer rechaza, Coder corrige, Reviewer aprueba, Tester aprueba
  - OPA bloquea -> run failed sin llegar al Reviewer ni al Tester
  - feedback del Tester llega al Coder en el reintento

Ejecutar desde alfred-api/:
  pytest tests/test_graph.py -v
"""

import unittest.mock as mock

import pytest

from app.agents.architect import Plan, Task
from app.agents.coder import CoderResult
from app.agents.reviewer import ReviewerResult
from app.agents.tester import TesterResult
from app.orchestrator.state import MAX_REVIEWER_RETRIES, MAX_TESTER_RETRIES

# --------------------------------------------------------------------------- #
# Helpers
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
        agent="other",
        priority="low",
        depends_on=depends_on or [],
        estimated_complexity="low",
        files_to_create=[],
        files_to_modify=[],
    )


def _fake_coder(task: Task, reviewer_feedback=None) -> CoderResult:
    return CoderResult(
        task_id=task.id,
        files_written=[f"app/{task.id}.py"],
        summary=f"Implementé {task.id}",
    )


def _reviewer_ok(task_id: str = "") -> ReviewerResult:
    return ReviewerResult(approved=True, feedback="OK", task_id=task_id)


def _tester_ok(task_id: str = "") -> TesterResult:
    return TesterResult(passed=True, feedback="Tests OK", task_id=task_id, test_file="tests/generated/x_test.py")


def _opa_pass():
    return type("R", (), {"passed": True, "violations": []})()


def _opa_fail(msg="violation"):
    return type("R", (), {"passed": False, "violations": [msg]})()


def _initial_state(prompt: str = "test") -> dict:
    from app.orchestrator.state import initial_state
    return initial_state(run_id="test-run", prompt=prompt)


def _patches(
    plan,
    coder_side_effect=None,
    reviewer_side_effect=None,
    tester_side_effect=None,
    opa_return=None,
):
    """Helper para construir el stack de patches común."""
    async def fake_architect(prompt):
        return plan

    async def default_coder(task, reviewer_feedback=None):
        return _fake_coder(task, reviewer_feedback)

    async def default_reviewer(task, files_written):
        return _reviewer_ok(task.id)

    async def default_tester(task, files_written, tester_feedback=None):
        return _tester_ok(task.id)

    return [
        mock.patch("app.orchestrator.nodes.run_architect", fake_architect),
        mock.patch("app.orchestrator.nodes.run_coder",
                   side_effect=coder_side_effect or default_coder),
        mock.patch("app.orchestrator.nodes.run_reviewer",
                   side_effect=reviewer_side_effect or default_reviewer),
        mock.patch("app.orchestrator.nodes.run_tester",
                   side_effect=tester_side_effect or default_tester),
        mock.patch("app.orchestrator.nodes.opa.evaluate",
                   return_value=opa_return or _opa_pass()),
        mock.patch("app.orchestrator.nodes.read_file", return_value="# código"),
    ]


# --------------------------------------------------------------------------- #
# Test 1: Happy path completo
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_happy_path_full_pipeline():
    """Pipeline completo: Architect→Coder→OPA→Reviewer→Tester. Todo aprueba."""
    plan = _make_plan([
        _coder_task("task_1"),
        _coder_task("task_2", depends_on=["task_1"]),
    ])

    patches = _patches(plan)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        from app.orchestrator.graph import compiled_graph
        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "done"
    assert set(final["completed"]) == {"task_1", "task_2"}
    assert final["error"] is None

    agents = {s.agent for s in final["steps"]}
    assert "coder" in agents
    assert "reviewer" in agents
    assert "tester" in agents


# --------------------------------------------------------------------------- #
# Test 2: Tester falla una vez, aprueba en segundo intento
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_tester_retry_approves_on_second_attempt():
    """Tester falla en intento 1, Coder corrige, Tester aprueba en intento 2."""
    plan = _make_plan([_coder_task("task_1")])
    tester_calls = {"n": 0}

    async def tester_fail_then_pass(task, files_written, tester_feedback=None):
        tester_calls["n"] += 1
        if tester_calls["n"] == 1:
            return TesterResult(
                passed=False,
                feedback="Tests fallaron:\nAssertionError: expected 200 got 404",
                task_id=task.id,
                pytest_output="FAILED tests/generated/task_1_test.py::test_endpoint - AssertionError",
            )
        return _tester_ok(task.id)

    patches = _patches(plan, tester_side_effect=tester_fail_then_pass)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        from app.orchestrator.graph import compiled_graph
        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "done"
    assert "task_1" in final["completed"]
    assert tester_calls["n"] == 2

    tester_steps = [s for s in final["steps"] if s.agent == "tester"]
    assert len(tester_steps) == 2
    assert tester_steps[0].status == "failed"
    assert tester_steps[1].status == "success"


# --------------------------------------------------------------------------- #
# Test 3: Tester agota reintentos -> run failed
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_tester_exhausts_retries_fails_run():
    """Tester siempre falla -> run failed tras MAX_TESTER_RETRIES+1 intentos."""
    plan = _make_plan([_coder_task("task_1")])

    async def tester_always_fails(task, files_written, tester_feedback=None):
        return TesterResult(
            passed=False,
            feedback="Tests siempre fallan",
            task_id=task.id,
            pytest_output="FAILED",
        )

    patches = _patches(plan, tester_side_effect=tester_always_fails)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        from app.orchestrator.graph import compiled_graph
        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "failed"
    assert "task_1" not in final["completed"]

    tester_steps = [s for s in final["steps"] if s.agent == "tester"]
    assert len(tester_steps) == MAX_TESTER_RETRIES + 1
    assert all(s.status == "failed" for s in tester_steps)


# --------------------------------------------------------------------------- #
# Test 4: Reviewer rechaza, Coder corrige, luego Tester aprueba
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_reviewer_retry_then_tester_passes():
    """Reviewer rechaza una vez, Coder corrige, Reviewer aprueba, Tester aprueba."""
    plan = _make_plan([_coder_task("task_1")])
    reviewer_calls = {"n": 0}

    async def reviewer_fail_then_pass(task, files_written):
        reviewer_calls["n"] += 1
        if reviewer_calls["n"] == 1:
            return ReviewerResult(approved=False, feedback="Falta manejo de errores", task_id=task.id)
        return ReviewerResult(approved=True, feedback="Correcto", task_id=task.id)

    patches = _patches(plan, reviewer_side_effect=reviewer_fail_then_pass)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        from app.orchestrator.graph import compiled_graph
        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "done"
    assert "task_1" in final["completed"]
    assert reviewer_calls["n"] == 2

    reviewer_steps = [s for s in final["steps"] if s.agent == "reviewer"]
    assert reviewer_steps[0].status == "failed"
    assert reviewer_steps[1].status == "success"

    tester_steps = [s for s in final["steps"] if s.agent == "tester"]
    assert len(tester_steps) == 1
    assert tester_steps[0].status == "success"


# --------------------------------------------------------------------------- #
# Test 5: OPA bloquea -> run failed, Reviewer y Tester no se llaman
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_opa_gate_blocks_reviewer_and_tester():
    """OPA falla -> run failed sin llamar al Reviewer ni al Tester."""
    plan = _make_plan([_coder_task("task_1")])

    reviewer_mock = mock.AsyncMock()
    tester_mock = mock.AsyncMock()

    patches = _patches(plan, opa_return=_opa_fail("hardcoded secret"))
    # Reemplazar reviewer y tester con mocks que no deben llamarse
    with patches[0], patches[1], \
         mock.patch("app.orchestrator.nodes.run_reviewer", reviewer_mock), \
         mock.patch("app.orchestrator.nodes.run_tester", tester_mock), \
         patches[4], patches[5]:
        from app.orchestrator.graph import compiled_graph
        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "failed"
    assert "hardcoded secret" in final["error"]
    reviewer_mock.assert_not_called()
    tester_mock.assert_not_called()


# --------------------------------------------------------------------------- #
# Test 6: feedback del Tester llega al Coder en el reintento
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_tester_feedback_passed_to_coder_on_retry():
    """El output de pytest del Tester llega como tester_feedback al Coder."""
    plan = _make_plan([_coder_task("task_1")])
    coder_calls: list[dict] = []

    async def coder_spy(task, reviewer_feedback=None):
        coder_calls.append({"task_id": task.id, "feedback": reviewer_feedback})
        return CoderResult(
            task_id=task.id,
            files_written=[f"app/{task.id}.py"],
            summary="ok",
        )

    tester_calls = {"n": 0}

    async def tester_fail_once(task, files_written, tester_feedback=None):
        tester_calls["n"] += 1
        if tester_calls["n"] == 1:
            return TesterResult(
                passed=False,
                feedback="Tests fallaron:\nAssertionError en test_create",
                task_id=task.id,
                pytest_output="FAILED - AssertionError en test_create",
            )
        return _tester_ok(task.id)

    patches = _patches(plan, coder_side_effect=coder_spy, tester_side_effect=tester_fail_once)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        from app.orchestrator.graph import compiled_graph
        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "done"
    assert len(coder_calls) == 2
    # Primer intento del Coder: sin feedback
    assert coder_calls[0]["feedback"] is None
    # Segundo intento: con feedback del Tester
    assert coder_calls[1]["feedback"] is not None
    assert "AssertionError" in coder_calls[1]["feedback"]