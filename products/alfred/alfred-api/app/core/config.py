from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Base de datos ──────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://alfred:alfred_dev_password@localhost:5432/alfred_db"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "alfred"
    postgres_password: str = "alfred_dev_password"
    postgres_db: str = "alfred_db"

    # ── Redis ──────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Ollama ─────────────────────────────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:35b-a3b"
    ollama_model_fast: str = "qwen2.5-coder:14b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_timeout: float = 300.0        # segundos — modelos grandes necesitan >60s

    # ── Modo de operación del pipeline (ADR-011) ──────────────────────────
    # "hybrid"       → Architect + Reviewer en frontier; resto local (default)
    # "full_local"   → todo en Ollama, sin API externa (soberanía)
    # "full_frontier"→ todo en Claude API
    llm_mode: str = "hybrid"

    # ── API key de Anthropic (Gotcha #25) ────────────────────────────────
    # ANTHROPIC_API_KEY en .env — sin ella falla en el primer run en modo hybrid,
    # no al arrancar. Verificar con:
    # python -c "from app.core.config import settings; print(settings.anthropic_api_key[:8])"
    anthropic_api_key: str = ""

    # ── Modelos frontier por agente (ADR-008) ────────────────────────────
    # Gotcha #28: fijar versión explícitamente — NO rotar automático entre 4.6/4.7/4.8
    frontier_architect: str = "claude-sonnet-4-6"
    frontier_reviewer:  str = "claude-sonnet-4-6"
    frontier_coder:     str = "claude-sonnet-4-6"

    # ── Modelo local rápido (14B) para Tester y Auditor ──────────────────
    # ollama_model_fast ya existe en v2.3; verificar que esté en Settings.
    # Si no existe aún, agregarlo aquí:
    # ollama_model_fast: str = "qwen2.5-coder:14b"

    # ── Alerta de costo por run (structlog) ──────────────────────────────
    # Si el costo total de una llamada supera este umbral → log "run.cost_alert"
    run_cost_alert_usd: float = 0.50




    # ── OPA ────────────────────────────────────────────────────────────────────
    opa_url: str = "http://localhost:8181"

    # ── Alfred ─────────────────────────────────────────────────────────────────
    alfred_env: str = "development"
    alfred_log_level: str = "INFO"
    alfred_max_agent_retries: int = 3
    alfred_min_coverage: int = 75
    alfred_project_root: str = "C:/Cognity/products/alfred/alfred-api"

    # ── GitHub ─────────────────────────────────────────────────────────────────
    github_token: str = ""
    github_repo: str = "azr-cognity/alfred"
    github_base_branch: str = "main"

    # ── Observabilidad (S9) ────────────────────────────────────────────────────
    sentry_dsn: str = ""           # dejar vacío para deshabilitar
    posthog_api_key: str = ""      # dejar vacío para deshabilitar
    posthog_host: str = "https://app.posthog.com"


# Instancia global
settings = Settings()
