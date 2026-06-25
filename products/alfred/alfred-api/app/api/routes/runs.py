"""
Router de runs — versión S11.

POST /api/runs              → persiste run con status=queued, encola en Redis, 202
GET  /api/runs              → lista de runs (filtro opcional project_id, paginación)
GET  /api/runs/{id}         → detalle de un run
GET  /api/runs/{id}/status  → SSE: snapshot Postgres + tail del canal Redis
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.schemas.runs import CreateRunRequest, RunResponse, RunStatus

router = APIRouter()
logger = structlog.get_logger()

STREAM_KEY = "alfred:runs"

async def _resolve_project_uuid(name_or_id: str, session) -> str:
    """Resuelve nombre o UUID de proyecto a UUID string.

    Si name_or_id ya es un UUID válido lo retorna directo.
    Si es un nombre, busca en la tabla projects.
    Lanza HTTPException 404 si no existe.
    """
    import uuid as _uuid
    try:
        _uuid.UUID(name_or_id)
        return name_or_id          # ya es UUID válido
    except ValueError:
        pass

    row = await session.execute(
        text("SELECT id FROM projects WHERE name = :name"),
        {"name": name_or_id},
    )
    result = row.mappings().first()
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"Proyecto '{name_or_id}' no encontrado",
        )
    return str(result["id"])

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
        project_uuid = None
        if getattr(body, "project_id", None):
            project_uuid = await _resolve_project_uuid(str(body.project_id), session)

        await session.execute(
            text("""
                INSERT INTO agent_runs
                    (id, prompt, status, current_agent, created_at, project_id)
                VALUES
                    (:id, :prompt, :status, :agent, :created_at, CAST(:project_id AS uuid))
            """),
            {
                "id": str(run_id),
                "prompt": body.prompt,
                "status": "queued",
                "agent": None,
                "created_at": created_at,
                "project_id": project_uuid,
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
# GET /api/runs  — lista paginada
# --------------------------------------------------------------------------- #
@router.get("/runs", tags=["Runs"])
async def list_runs(
    project_id: str | None = Query(default=None, description="Filtrar por proyecto"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """
    Devuelve la lista de runs ordenados por created_at DESC.
    Filtro opcional por project_id.
    """
    async with AsyncSessionLocal() as session:
        if project_id:
            rows = await session.execute(
                text("""
                    SELECT id, prompt, status, current_agent, plan, result,
                           error, created_at, completed_at, project_id, duration_ms
                    FROM agent_runs
                    WHERE project_id = CAST(:project_id AS uuid)
                    ORDER BY created_at DESC
                    LIMIT :limit OFFSET :offset
                """),
                {"project_id": project_id, "limit": limit, "offset": offset},
            )
        else:
            rows = await session.execute(
                text("""
                    SELECT id, prompt, status, current_agent, plan, result,
                           error, created_at, completed_at, project_id, duration_ms
                    FROM agent_runs
                    ORDER BY created_at DESC
                    LIMIT :limit OFFSET :offset
                """),
                {"limit": limit, "offset": offset},
            )

        runs = [dict(r) for r in rows.mappings()]

        # Total para paginación
        if project_id:
            count_row = await session.execute(
                text("SELECT COUNT(*) FROM agent_runs WHERE project_id = CAST(:project_id AS uuid)"),
                {"project_id": project_id},
            )
        else:
            count_row = await session.execute(text("SELECT COUNT(*) FROM agent_runs"))

        total = count_row.scalar()

    # Serializar UUIDs y fechas
    for r in runs:
        r["id"] = str(r["id"])
        r["project_id"] = str(r["project_id"]) if r.get("project_id") else None
        r["created_at"] = r["created_at"].isoformat() if r.get("created_at") else None
        r["completed_at"] = r["completed_at"].isoformat() if r.get("completed_at") else None

    return {"runs": runs, "total": total, "limit": limit, "offset": offset}


# --------------------------------------------------------------------------- #
# GET /api/runs/{run_id}  — detalle
# --------------------------------------------------------------------------- #
@router.get("/runs/{run_id}", tags=["Runs"])
async def get_run(run_id: str) -> dict:
    """
    Devuelve el detalle completo de un run, incluyendo plan y result (jsonb).
    """
    async with AsyncSessionLocal() as session:
        row = await session.execute(
            text("""
                SELECT id, prompt, status, current_agent, plan, result,
                       error, created_at, completed_at, project_id, duration_ms, tokens_used
                FROM agent_runs
                WHERE id = CAST(:id AS uuid)
            """),
            {"id": run_id},
        )
        run = row.mappings().first()

    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' no encontrado")

    data = dict(run)
    data["id"] = str(data["id"])
    data["project_id"] = str(data["project_id"]) if data.get("project_id") else None
    data["created_at"] = data["created_at"].isoformat() if data.get("created_at") else None
    data["completed_at"] = data["completed_at"].isoformat() if data.get("completed_at") else None

    return data


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
            text("SELECT status, current_agent, error FROM agent_runs WHERE id = CAST(:id AS uuid)"),
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

    try:
        while True:
            # Heartbeat: comentario SSE vacío para mantener conexión
            yield ": heartbeat\n\n"

            try:
                msg = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                continue

            if msg is None:
                continue

            payload = json.loads(msg["data"])
            yield _fmt(payload)

            if payload.get("event") == "run_finished":
                break

    finally:
        await pubsub.unsubscribe(f"run:{run_id}")
        await r.aclose()