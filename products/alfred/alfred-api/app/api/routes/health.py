import time

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.database import AsyncSessionLocal
from app.core.ollama import ollama
from app.core.opa import opa

router = APIRouter()


class ServiceStatus(BaseModel):
    status: str          # "ok" | "error"
    latency_ms: float


class HealthResponse(BaseModel):
    status: str          # "ok" | "degraded" | "error"
    version: str
    services: dict[str, ServiceStatus]


@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check() -> HealthResponse:
    """
    Verifica que todos los servicios de Alfred están operativos.
    Llama a Postgres, Ollama y OPA en paralelo y reporta el estado de cada uno.
    """
    services: dict[str, ServiceStatus] = {}

    # ── Postgres ───────────────────────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(__import__("sqlalchemy").text("SELECT 1"))
        services["postgres"] = ServiceStatus(
            status="ok",
            latency_ms=round((time.monotonic() - t0) * 1000, 1),
        )
    except Exception as e:
        services["postgres"] = ServiceStatus(
            status=f"error: {e}",
            latency_ms=round((time.monotonic() - t0) * 1000, 1),
        )

    # ── Ollama ─────────────────────────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        ok = await ollama.health()
        services["ollama"] = ServiceStatus(
            status="ok" if ok else "error: model not found",
            latency_ms=round((time.monotonic() - t0) * 1000, 1),
        )
    except Exception as e:
        services["ollama"] = ServiceStatus(
            status=f"error: {e}",
            latency_ms=round((time.monotonic() - t0) * 1000, 1),
        )

    # ── OPA ────────────────────────────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        ok = await opa.health()
        services["opa"] = ServiceStatus(
            status="ok" if ok else "error: unreachable",
            latency_ms=round((time.monotonic() - t0) * 1000, 1),
        )
    except Exception as e:
        services["opa"] = ServiceStatus(
            status=f"error: {e}",
            latency_ms=round((time.monotonic() - t0) * 1000, 1),
        )

    # Estado global
    all_ok = all(s.status == "ok" for s in services.values())
    any_error = any(s.status.startswith("error") for s in services.values())

    return HealthResponse(
        status="ok" if all_ok else "degraded" if not any_error else "error",
        version="0.1.0",
        services=services,
    )
