import json as json_lib
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.agents.architect import run_architect
from app.core.database import AsyncSessionLocal
from app.schemas.runs import CreateRunRequest, RunResponse, RunStatus

router = APIRouter()
logger = structlog.get_logger()


@router.post("/runs", response_model=RunResponse, tags=["Runs"], status_code=201)
async def create_run(body: CreateRunRequest) -> RunResponse:
    """
    Inicia un run de Alfred.

    Por ahora ejecuta solo el Architect y devuelve el plan.
    En semanas siguientes, el plan se pasará al Coder, Reviewer, etc.

    Ejemplo de request:
        POST /api/runs
        {"prompt": "crea un endpoint REST para registrar usuarios"}

    Ejemplo de response:
        {
            "id": "...",
            "status": "done",
            "plan": {
                "summary": "...",
                "tasks": [...]
            }
        }
    """
    run_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    log = logger.bind(run_id=str(run_id), prompt_len=len(body.prompt))

    log.info("run.start")

    try:
        # ── Paso 1: Architect planifica ────────────────────────────────────────
        plan = await run_architect(body.prompt)

        # ── Paso 2: Guardar en Postgres ────────────────────────────────────────
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    INSERT INTO agent_runs
                        (id, prompt, status, plan, current_agent, created_at, completed_at)
                    VALUES
                        (:id, :prompt, :status, CAST(:plan AS jsonb), :agent, :created_at, :completed_at)
                """),
                {
                    "id": str(run_id),
                    "prompt": body.prompt,
                    "status": RunStatus.DONE.value,
                    "plan": json_lib.dumps(plan.model_dump()),
                    "agent": "architect",
                    "created_at": created_at,
                    "completed_at": datetime.now(timezone.utc),
                }
            )
            await session.commit()

        log.info("run.done", tasks=len(plan.tasks))

        return RunResponse(
            id=run_id,
            status=RunStatus.DONE,
            prompt=body.prompt,
            plan=plan,
            current_agent="architect",
            created_at=created_at,
            completed_at=datetime.now(timezone.utc),
        )

    except ValueError as e:
        log.error("run.failed", error=str(e))
        raise HTTPException(status_code=422, detail=str(e))

    except Exception as e:
        log.error("run.error", error=str(e))
        raise HTTPException(status_code=500, detail=f"Error interno: {e}")
