"""
Router de runs — versión S5.

POST /api/runs       → persiste run con status=queued, encola en Redis, 202
GET  /api/runs/{id}/status → SSE: snapshot Postgres + tail del canal Redis
"""

import json
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.schemas.runs import CreateRunRequest, RunResponse, RunStatus

router = APIRouter()
logger = structlog.get_logger()

STREAM_KEY = "alfred:runs"


def _redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


# --------------------------------------------------------------------------- #
# POST /api/runs
# --------------------------------------------------------------------------- #
@router.post("/runs", response_model=RunResponse, tags=["Runs"], status_code=202)
async def create_run(body: CreateRunRequest) -> RunResponse:
    """
    Encola un run de Alfred. Responde 202 inmediatamente.

    El orquestador (worker) recoge el run de Redis y lo ejecuta en background.
    El progreso se puede seguir en GET /api/runs/{id}/status (SSE).
    """
    run_id = uuid.uuid4()
    created_at = datetime.now(timezone.utc)
    log = logger.bind(run_id=str(run_id))

    # ── Persistir con status=queued ────────────────────────────────────────────
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO agent_runs
                    (id, prompt, status, current_agent, created_at)
                VALUES
                    (:id, :prompt, :status, :agent, :created_at)
            """),
            {
                "id": str(run_id),
                "prompt": body.prompt,
                "status": "queued",
                "agent": None,
                "created_at": created_at,
            },
        )
        await session.commit()

    # ── Encolar en Redis stream ────────────────────────────────────────────────
    r = _redis()
    try:
        await r.xadd(STREAM_KEY, {"run_id": str(run_id), "prompt": body.prompt})
        log.info("run.queued")
    except Exception as e:
        log.error("run.enqueue_failed", error=str(e))
        raise HTTPException(status_code=503, detail=f"No se pudo encolar el run: {e}")
    finally:
        await r.aclose()

    return RunResponse(
        id=run_id,
        status=RunStatus.QUEUED,
        prompt=body.prompt,
        plan=None,
        current_agent=None,
        created_at=created_at,
        completed_at=None,
    )


# --------------------------------------------------------------------------- #
# GET /api/runs/{run_id}/status  — SSE
# --------------------------------------------------------------------------- #
@router.get("/runs/{run_id}/status", tags=["Runs"])
async def run_status(run_id: str) -> StreamingResponse:
    """
    Stream SSE del progreso de un run.

    - Emite primero un snapshot del estado actual desde Postgres.
    - Luego suscribe al canal Redis 'run:{id}' y reenvía eventos hasta
      recibir 'run_finished'.
    - Heartbeat cada 15s para mantener la conexión viva.
    """
    return StreamingResponse(
        _sse_generator(run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx: deshabilita buffering
        },
    )


async def _sse_generator(run_id: str):
    """Generador async que produce líneas SSE."""

    def _fmt(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    # ── Snapshot inicial desde Postgres ────────────────────────────────────────
    async with AsyncSessionLocal() as session:
        row = await session.execute(
            text("SELECT status, current_agent, error FROM agent_runs WHERE id = :id"),
            {"id": run_id},
        )
        run = row.mappings().first()

    if not run:
        yield _fmt({"event": "error", "detail": f"run '{run_id}' no encontrado"})
        return

    yield _fmt({
        "event": "snapshot",
        "run_id": run_id,
        "status": run["status"],
        "current_agent": run["current_agent"],
        "error": run["error"],
    })

    # Si ya terminó, no hay nada que escuchar
    if run["status"] in ("done", "failed"):
        yield _fmt({"event": "run_finished", "run_id": run_id, "status": run["status"]})
        return

    # ── Suscribir al canal Redis ───────────────────────────────────────────────
    r = _redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(f"run:{run_id}")

    import asyncio
    try:
        while True:
            # Heartbeat: comentario SSE vacío para mantener conexión
            yield ": heartbeat\n\n"

            try:
                msg = await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=True), timeout=15.0)
            except asyncio.TimeoutError:
                continue  # solo heartbeat, seguir esperando

            if msg is None:
                continue

            payload = json.loads(msg["data"])
            yield _fmt(payload)

            if payload.get("event") == "run_finished":
                break

    finally:
        await pubsub.unsubscribe(f"run:{run_id}")
        await r.aclose()
