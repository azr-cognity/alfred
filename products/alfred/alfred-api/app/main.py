"""
Alfred API — entry point (S11).
"""

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.core.telemetry import init_telemetry
from app.api.routes.health import router as health_router
from app.api.routes.runs import router as runs_router
from app.api.routes.projects import router as projects_router

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_telemetry()
    logger.info("alfred.startup", env=settings.alfred_env, model=settings.ollama_model)
    await init_db()
    logger.info("alfred.startup.done")
    yield


app = FastAPI(title="Alfred API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3002", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/api")
app.include_router(runs_router, prefix="/api/v1")
app.include_router(projects_router, prefix="/api/v1")