"""
Alfred Worker — Paso 3 del orquestador S5.
Fix S6: elimina ainvoke duplicado al final del astream.
Fix S6: reconnect Redis silencioso.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
import structlog
from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.orchestrator.graph import compiled_graph
from app.orchestrator.state import AgentStep, initial_state

logger = structlog.get_logger()

STREAM_KEY = "alfred:runs"
GROUP_NAME = "alfred-workers"
CONSUMER_NAME = f"worker-{uuid.uuid4().hex[:8]}"
BLOCK_MS = 2000


def _redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def _ensure_group(r: aioredis.Redis) -> None:
    try:
        await r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def _update_run_status(run_id: str, status: str, error: str | None = None) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                UPDATE agent_runs
                SET status       = :status,
                    error        = :error,
                    completed_at = :completed_at
                WHERE id = :id
            """),
            {
                "id": run_id,
                "status": status,
                "error": error,
                "completed_at": datetime.now(timezone.utc),
            },
        )
        await session.commit()


async def _insert_step(run_id: str, step: AgentStep) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO agent_steps
                    (id, run_id, agent_name, status, output, created_at, completed_at)
                VALUES
                    (:id, :run_id, :agent_name, :status, CAST(:output AS jsonb),
                     :created_at, :completed_at)
            """),
            {
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "agent_name": step.agent,
                "status": step.status,
                "output": json.dumps({
                    "task_id": step.task_id,
                    "summary": step.summary,
                    "files_written": step.files_written,
                    "error": step.error,
                }),
                "created_at": datetime.now(timezone.utc),
                "completed_at": datetime.now(timezone.utc),
            },
        )
        await session.commit()


async def _publish(r: aioredis.Redis, run_id: str, payload: dict) -> None:
    await r.publish(f"run:{run_id}", json.dumps(payload))


async def _execute_run(r: aioredis.Redis, run_id: str, prompt: str) -> None:
    log = logger.bind(run_id=run_id)
    log.info("worker.run.start")

    await _update_run_status(run_id, "running")
    await _publish(r, run_id, {"event": "run_started", "run_id": run_id})

    state = initial_state(run_id=run_id, prompt=prompt)
    seen_steps: set[str] = set()

    # Acumula status/error del último nodo que los emita —
    # SIN reinvocar el grafo al final (eso relanzaba todo el pipeline).
    final_status = "done"
    final_error: str | None = None

    try:
        async for event in compiled_graph.astream(state):
            node_name = list(event.keys())[0]
            node_out = event[node_name]

            if node_out.get("status"):
                final_status = node_out["status"]
            if node_out.get("error"):
                final_error = node_out["error"]

            await _publish(r, run_id, {
                "event": "node_update",
                "node": node_name,
                "status": node_out.get("status"),
                "current_task_id": node_out.get("current_task_id"),
            })

            for step in node_out.get("steps", []):
                key = f"{step.task_id}:{step.agent}"
                if key not in seen_steps:
                    seen_steps.add(key)
                    await _insert_step(run_id, step)
                    log.info("worker.step.saved", task_id=step.task_id, status=step.status)

        await _update_run_status(run_id, final_status, final_error)
        await _publish(r, run_id, {
            "event": "run_finished",
            "run_id": run_id,
            "status": final_status,
            "error": final_error,
        })
        log.info("worker.run.done", status=final_status)

    except Exception as e:
        log.error("worker.run.error", error=str(e))
        await _update_run_status(run_id, "failed", str(e))
        await _publish(r, run_id, {
            "event": "run_finished",
            "run_id": run_id,
            "status": "failed",
            "error": str(e),
        })


async def main() -> None:
    log = logger.bind(consumer=CONSUMER_NAME)
    log.info("worker.start", stream=STREAM_KEY, group=GROUP_NAME)

    r = _redis()
    await _ensure_group(r)
    log.info("worker.listening")

    while True:
        try:
            results = await r.xreadgroup(
                groupname=GROUP_NAME,
                consumername=CONSUMER_NAME,
                streams={STREAM_KEY: ">"},
                count=1,
                block=BLOCK_MS,
            )

            if not results:
                continue

            for _stream, messages in results:
                for msg_id, fields in messages:
                    run_id = fields.get("run_id")
                    prompt = fields.get("prompt")

                    if not run_id or not prompt:
                        log.warning("worker.bad_message", msg_id=msg_id)
                        await r.xack(STREAM_KEY, GROUP_NAME, msg_id)
                        continue

                    try:
                        await _execute_run(r, run_id, prompt)
                    except Exception as e:
                        log.error("worker.execute_error", run_id=run_id, error=str(e))
                    finally:
                        await r.xack(STREAM_KEY, GROUP_NAME, msg_id)
                        log.info("worker.ack", msg_id=msg_id)

        except asyncio.CancelledError:
            break

        except (aioredis.ConnectionError, aioredis.TimeoutError):
            log.debug("worker.reconnecting")
            await asyncio.sleep(1)

        except Exception as e:
            log.error("worker.loop_error", error=str(e))
            await asyncio.sleep(2)

    await r.aclose()
    log.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())