"""
Telemetría de Alfred — Sentry + PostHog (S9).

Expone:
  - init_telemetry(): llamar en lifespan de FastAPI y al inicio del worker
  - track_event(event, properties): enviar evento a PostHog
  - capture_exception(e): enviar excepción a Sentry manualmente
"""

import structlog

logger = structlog.get_logger()

# ── Sentry ─────────────────────────────────────────────────────────────────────

def _init_sentry(dsn: str, env: str) -> None:
    if not dsn:
        logger.info("telemetry.sentry.disabled")
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=dsn,
            environment=env,
            traces_sample_rate=0.2,
            integrations=[
                AsyncioIntegration(),
                FastApiIntegration(),
                SqlalchemyIntegration(),
            ],
        )
        logger.info("telemetry.sentry.ok", env=env)
    except ImportError:
        logger.warning("telemetry.sentry.not_installed")


def capture_exception(exc: Exception) -> None:
    """Envía una excepción a Sentry manualmente."""
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
    except Exception:
        pass


# ── PostHog ────────────────────────────────────────────────────────────────────

_posthog_client = None


def _init_posthog(api_key: str, host: str) -> None:
    global _posthog_client
    if not api_key:
        logger.info("telemetry.posthog.disabled")
        return
    try:
        from posthog import Posthog
        _posthog_client = Posthog(api_key=api_key, host=host)
        logger.info("telemetry.posthog.ok")
    except ImportError:
        logger.warning("telemetry.posthog.not_installed")


def track_event(event: str, properties: dict | None = None, distinct_id: str = "alfred") -> None:
    """Envía un evento de negocio a PostHog.

    Args:
        event: nombre del evento (ej: "run.completed")
        properties: propiedades adicionales del evento
        distinct_id: identificador del actor (default: "alfred" para eventos del sistema)
    """
    if _posthog_client is None:
        return
    try:
        _posthog_client.capture(
            distinct_id=distinct_id,
            event=event,
            properties=properties or {},
        )
    except Exception as e:
        logger.warning("telemetry.posthog.error", error=str(e))


# ── Init unificado ─────────────────────────────────────────────────────────────

def init_telemetry() -> None:
    """Inicializa Sentry y PostHog. Llamar una vez al arrancar la app/worker."""
    from app.core.config import settings
    _init_sentry(dsn=settings.sentry_dsn, env=settings.alfred_env)
    _init_posthog(api_key=settings.posthog_api_key, host=settings.posthog_host)
