"""
Alfred API — entry point (S9).

Cambios respecto a S8:
  - init_telemetry() en lifespan (Sentry + PostHog)
"""

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.core.config import settings
from app.core.database import init_db
from app.core.telemetry import init_telemetry
from app.api.routes.health import router as health_router
from app.api.routes.runs import router as runs_router

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_telemetry()
    logger.info("alfred.startup", env=settings.alfred_env, model=settings.ollama_model)
    await init_db()
    logger.info("alfred.startup.done")
    yield


app = FastAPI(title="Alfred API", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
app.include_router(runs_router, prefix="/api/v1")
