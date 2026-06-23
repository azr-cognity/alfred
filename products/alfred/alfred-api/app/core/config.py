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

    # ── OPA ────────────────────────────────────────────────────────────────────
    opa_url: str = "http://localhost:8181"

    # ── Alfred ─────────────────────────────────────────────────────────────────
    alfred_env: str = "development"
    alfred_log_level: str = "INFO"
    alfred_max_agent_retries: int = 3
    alfred_min_coverage: int = 75


# Instancia global — importar desde aquí en todo el proyecto
settings = Settings()
