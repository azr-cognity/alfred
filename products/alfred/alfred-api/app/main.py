import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health
from app.core.config import settings
from app.core.database import init_db
from app.core.ollama import ollama
from app.core.opa import opa

# ── Logger ─────────────────────────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer() if settings.alfred_env == "development"
        else structlog.processors.JSONRenderer(),
    ]
)

logger = structlog.get_logger()

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Alfred API",
    description="AI coding assistant — agentic pipeline backend",
    version="0.1.0",
    docs_url="/docs" if settings.alfred_env == "development" else None,
    redoc_url="/redoc" if settings.alfred_env == "development" else None,
)

# CORS — permite que Next.js en localhost:3000 llame a la API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(health.router, prefix="/api")
# Próximas semanas:
from app.api.routes import runs
app.include_router(runs.router, prefix="/api")
# app.include_router(projects.router, prefix="/api")

# ── Lifecycle ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup() -> None:
    logger.info("alfred.startup", env=settings.alfred_env, model=settings.ollama_model)
    await init_db()
    logger.info("alfred.startup.done")


@app.on_event("shutdown")
async def shutdown() -> None:
    logger.info("alfred.shutdown")
    await ollama.close()
    await opa.close()
