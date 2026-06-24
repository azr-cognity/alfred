"""
Tests del grafo LangGraph — S8 completo (con Auditor + PR).

Cubre:
  - Happy path: pipeline completo, Auditor aprueba y abre PR
  - Auditor encuentra findings HIGH -> run failed
  - Auditor encuentra solo MEDIUM/LOW -> aprueba igualmente
  - all_files_written acumula archivos de múltiples tasks
  - OPA bloquea -> Auditor no se llama
  - Pipeline completo 2 tasks: ambas llegan al Auditor

Ejecutar desde alfred-api/:
  pytest tests/test_graph.py -v
"""

import unittest.mock as mock

import pytest

from app.agents.architect import Plan, Task
from app.agents.auditor import AuditFinding, AuditorResult
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
        id=tid, title=f"Tarea {tid}", description=f"Implementar {tid}",
        agent="coder", priority="high", depends_on=depends_on or [],
        estimated_complexity="low", files_to_create=[f"app/{tid}.py"], files_to_modify=[],
    )


def _skip_task(tid: str, depends_on: list[str] | None = None) -> Task:
    return Task(
        id=tid, title=f"Skip {tid}", description="skip",
        agent="other", priority="low", depends_on=depends_on or [],
        estimated_complexity="low", files_to_create=[], files_to_modify=[],
    )


def _fake_coder(task: Task, reviewer_feedback=None) -> CoderResult:
    return CoderResult(task_id=task.id, files_written=[f"app/{task.id}.py"], summary=f"ok {task.id}")


def _reviewer_ok(task_id="") -> ReviewerResult:
    return ReviewerResult(approved=True, feedback="OK", task_id=task_id)


def _tester_ok(task_id="") -> TesterResult:
    return TesterResult(passed=True, feedback="Tests OK", task_id=task_id, test_file="tests/generated/x_test.py")


def _auditor_ok(pr_url="https://github.com/azr-cognity/alfred/pull/1") -> AuditorResult:
    return AuditorResult(passed=True, feedback=f"Audit limpio. PR: {pr_url}", findings=[], pr_url=pr_url)


def _auditor_high() -> AuditorResult:
    finding = AuditFinding(tool="bandit", severity="HIGH", message="hardcoded password", file_path="app/task_1.py", line=10)
    return AuditorResult(passed=False, feedback="1 finding HIGH", findings=[finding])


def _auditor_medium() -> AuditorResult:
    finding = AuditFinding(tool="bandit", severity="MEDIUM", message="use of assert", file_path="app/task_1.py", line=5)
    return AuditorResult(passed=True, feedback="1 finding MEDIUM. PR: https://github.com/azr-cognity/alfred/pull/2", findings=[finding], pr_url="https://github.com/azr-cognity/alfred/pull/2")


def _opa_pass():
    return type("R", (), {"passed": True, "violations": []})()


def _opa_fail(msg="violation"):
    return type("R", (), {"passed": False, "violations": [msg]})()


def _initial_state(prompt: str = "test") -> dict:
    from app.orchestrator.state import initial_state
    return initial_state(run_id="test-run-001", prompt=prompt)


def _base_patches(plan, coder_se=None, reviewer_se=None, tester_se=None, auditor_se=None, opa_ret=None):
    async def fake_architect(prompt):
        return plan

    async def default_coder(task, reviewer_feedback=None):
        return _fake_coder(task, reviewer_feedback)

    async def default_reviewer(task, files_written):
        return _reviewer_ok(task.id)

    async def default_tester(task, files_written, tester_feedback=None):
        return _tester_ok(task.id)

    async def default_auditor(run_id, plan_summary, files_written):
        return _auditor_ok()

    return [
        mock.patch("app.orchestrator.nodes.run_architect", fake_architect),
        mock.patch("app.orchestrator.nodes.run_coder", side_effect=coder_se or default_coder),
        mock.patch("app.orchestrator.nodes.run_reviewer", side_effect=reviewer_se or default_reviewer),
        mock.patch("app.orchestrator.nodes.run_tester", side_effect=tester_se or default_tester),
        mock.patch("app.orchestrator.nodes.run_auditor", side_effect=auditor_se or default_auditor),
        mock.patch("app.orchestrator.nodes.opa.evaluate", return_value=opa_ret or _opa_pass()),
        mock.patch("app.orchestrator.nodes.read_file", return_value="# código"),
    ]


def _apply(patches):
    return patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]


# --------------------------------------------------------------------------- #
# Test 1: Happy path — pipeline completo, Auditor abre PR
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_happy_path_auditor_opens_pr():
    """Pipeline completo: todo aprueba, Auditor abre PR, run=done."""
    plan = _make_plan([_coder_task("task_1"), _coder_task("task_2", depends_on=["task_1"])])
    patches = _base_patches(plan)

    with _apply(patches)[0], _apply(patches)[1], _apply(patches)[2], _apply(patches)[3], \
         _apply(patches)[4], _apply(patches)[5], _apply(patches)[6]:
        from app.orchestrator.graph import compiled_graph
        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "done"
    assert set(final["completed"]) == {"task_1", "task_2"}
    assert final["error"] is None

    agents = {s.agent for s in final["steps"]}
    assert "auditor" in agents
    auditor_step = next(s for s in final["steps"] if s.agent == "auditor")
    assert auditor_step.status == "success"


# --------------------------------------------------------------------------- #
# Test 2: Auditor encuentra findings HIGH -> run failed
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_auditor_high_findings_fails_run():
    """Auditor encuentra HIGH findings -> run=failed."""
    plan = _make_plan([_coder_task("task_1")])

    async def auditor_high(run_id, plan_summary, files_written):
        return _auditor_high()

    patches = _base_patches(plan, auditor_se=auditor_high)
    with _apply(patches)[0], _apply(patches)[1], _apply(patches)[2], _apply(patches)[3], \
         _apply(patches)[4], _apply(patches)[5], _apply(patches)[6]:
        from app.orchestrator.graph import compiled_graph
        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "failed"
    assert "HIGH" in final["error"]

    auditor_step = next(s for s in final["steps"] if s.agent == "auditor")
    assert auditor_step.status == "failed"


# --------------------------------------------------------------------------- #
# Test 3: Auditor solo MEDIUM/LOW -> aprueba
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_auditor_medium_findings_passes():
    """Auditor con findings MEDIUM/LOW -> run=done igual."""
    plan = _make_plan([_coder_task("task_1")])

    async def auditor_medium(run_id, plan_summary, files_written):
        return _auditor_medium()

    patches = _base_patches(plan, auditor_se=auditor_medium)
    with _apply(patches)[0], _apply(patches)[1], _apply(patches)[2], _apply(patches)[3], \
         _apply(patches)[4], _apply(patches)[5], _apply(patches)[6]:
        from app.orchestrator.graph import compiled_graph
        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "done"
    auditor_step = next(s for s in final["steps"] if s.agent == "auditor")
    assert auditor_step.status == "success"


# --------------------------------------------------------------------------- #
# Test 4: all_files_written acumula archivos de múltiples tasks
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_all_files_written_accumulates_across_tasks():
    """all_files_written tiene los archivos de todas las tasks al llegar al Auditor."""
    plan = _make_plan([
        _coder_task("task_1"),
        _coder_task("task_2", depends_on=["task_1"]),
        _coder_task("task_3", depends_on=["task_2"]),
    ])

    auditor_received: list[list[str]] = []

    async def auditor_spy(run_id, plan_summary, files_written):
        auditor_received.append(list(files_written))
        return _auditor_ok()

    patches = _base_patches(plan, auditor_se=auditor_spy)
    with _apply(patches)[0], _apply(patches)[1], _apply(patches)[2], _apply(patches)[3], \
         _apply(patches)[4], _apply(patches)[5], _apply(patches)[6]:
        from app.orchestrator.graph import compiled_graph
        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "done"
    assert len(auditor_received) == 1
    received = auditor_received[0]
    assert "app/task_1.py" in received
    assert "app/task_2.py" in received
    assert "app/task_3.py" in received


# --------------------------------------------------------------------------- #
# Test 5: OPA bloquea -> Auditor no se llama
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_opa_blocks_auditor_not_called():
    """OPA falla -> run failed sin llegar al Auditor."""
    plan = _make_plan([_coder_task("task_1")])
    auditor_mock = mock.AsyncMock()

    patches = _base_patches(plan, opa_ret=_opa_fail("hardcoded secret"))
    with _apply(patches)[0], _apply(patches)[1], _apply(patches)[2], _apply(patches)[3], \
         mock.patch("app.orchestrator.nodes.run_auditor", auditor_mock), \
         _apply(patches)[5], _apply(patches)[6]:
        from app.orchestrator.graph import compiled_graph
        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "failed"
    auditor_mock.assert_not_called()


# --------------------------------------------------------------------------- #
# Test 6: Reviewer rechaza, Coder corrige, Tester pasa, Auditor abre PR
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_reviewer_retry_then_full_pipeline_to_pr():
    """Reviewer rechaza una vez, pipeline continúa y Auditor abre PR."""
    plan = _make_plan([_coder_task("task_1")])
    reviewer_calls = {"n": 0}

    async def reviewer_fail_once(task, files_written):
        reviewer_calls["n"] += 1
        if reviewer_calls["n"] == 1:
            return ReviewerResult(approved=False, feedback="Falta validación", task_id=task.id)
        return _reviewer_ok(task.id)

    patches = _base_patches(plan, reviewer_se=reviewer_fail_once)
    with _apply(patches)[0], _apply(patches)[1], _apply(patches)[2], _apply(patches)[3], \
         _apply(patches)[4], _apply(patches)[5], _apply(patches)[6]:
        from app.orchestrator.graph import compiled_graph
        final = await compiled_graph.ainvoke(_initial_state())

    assert final["status"] == "done"
    assert "task_1" in final["completed"]
    assert reviewer_calls["n"] == 2

    agents_seq = [s.agent for s in final["steps"]]
    assert "auditor" in agents_seq
    assert agents_seq.index("auditor") > agents_seq.index("reviewer")