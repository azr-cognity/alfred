"""
Alfred Worker (S9).

Cambios respecto a S8:
  - init_telemetry() al arrancar
  - track_event en run start/done/failed
  - Exponential backoff en reconnect Redis (1s → 2s → 4s → max 30s)
  - capture_exception en errores no manejados
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
from app.core.telemetry import capture_exception, init_telemetry, track_event
from app.core.llm import reset_run_cost, get_and_reset_run_cost
from app.orchestrator.graph import compiled_graph
from app.orchestrator.state import AgentStep, initial_state

logger = structlog.get_logger()

STREAM_KEY = "alfred:runs"
GROUP_NAME = "alfred-workers"
CONSUMER_NAME = f"worker-{uuid.uuid4().hex[:8]}"
BLOCK_MS = 2000

# Exponential backoff config
_BACKOFF_BASE = 1      # segundos
_BACKOFF_MAX = 30      # segundos máximo
_backoff_current = _BACKOFF_BASE


def _redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, decode_responses=True)


async def _ensure_group(r: aioredis.Redis) -> None:
    try:
        await r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def _update_run_status(run_id: str, status: str, error: str | None = None, cost_usd: float = 0.0) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                UPDATE agent_runs
                SET status       = :status,
                    error        = :error,
                    cost_usd     = :cost_usd,
                    completed_at = :completed_at
                WHERE id = :id
            """),
            {
                "id": run_id,
                "status": status,
                "error": error,
                "cost_usd": cost_usd,
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

    reset_run_cost()
    track_event("run.started", {"run_id": run_id, "prompt_len": len(prompt)})

    await _update_run_status(run_id, "running")
    await _publish(r, run_id, {"event": "run_started", "run_id": run_id})

    state = initial_state(run_id=run_id, prompt=prompt)
    seen_steps: set[str] = set()
    final_status = "done"
    final_error: str | None = None
    step_count = 0
    started_at = datetime.now(timezone.utc)

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
                    step_count += 1
                    log.info("worker.step.saved", task_id=step.task_id, status=step.status)

        duration_s = (datetime.now(timezone.utc) - started_at).total_seconds()
        total_cost = get_and_reset_run_cost()

        await _update_run_status(run_id, final_status, final_error, cost_usd=total_cost)
        await _publish(r, run_id, {
            "event": "run_finished",
            "run_id": run_id,
            "status": final_status,
            "error": final_error,
        })

        event_name = "run.completed" if final_status == "done" else "run.failed"
        track_event(event_name, {
            "run_id": run_id,
            "status": final_status,
            "steps": step_count,
            "duration_s": round(duration_s, 1),
            "error": final_error,
        })

        log.info("worker.run.done", status=final_status, steps=step_count, duration_s=round(duration_s, 1))

    except Exception as e:
        log.error("worker.run.error", error=str(e))
        capture_exception(e)
        track_event("run.failed", {"run_id": run_id, "error": str(e), "exception": True})
        await _update_run_status(run_id, "failed", str(e))
        await _publish(r, run_id, {
            "event": "run_finished",
            "run_id": run_id,
            "status": "failed",
            "error": str(e),
        })


async def main() -> None:
    global _backoff_current

    init_telemetry()

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

            # Reconexión exitosa — resetear backoff
            _backoff_current = _BACKOFF_BASE

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
                        capture_exception(e)
                    finally:
                        await r.xack(STREAM_KEY, GROUP_NAME, msg_id)
                        log.info("worker.ack", msg_id=msg_id)

        except asyncio.CancelledError:
            break

        except (aioredis.ConnectionError, aioredis.TimeoutError):
            log.warning("worker.reconnecting", backoff_s=_backoff_current)
            await asyncio.sleep(_backoff_current)
            _backoff_current = min(_backoff_current * 2, _BACKOFF_MAX)

        except Exception as e:
            log.error("worker.loop_error", error=str(e))
            capture_exception(e)
            await asyncio.sleep(_backoff_current)
            _backoff_current = min(_backoff_current * 2, _BACKOFF_MAX)

    await r.aclose()
    log.info("worker.stopped")


if __name__ == "__main__":
    asyncio.run(main())








