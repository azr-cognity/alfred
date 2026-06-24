"""
Cliente Ollama con circuit breaker (S9).

Circuit breaker protege contra fallos en cascada cuando Ollama no responde:
  - CLOSED (normal): peticiones pasan normalmente
  - OPEN: tras FAILURE_THRESHOLD fallos consecutivos, falla inmediato sin llamar a Ollama
  - HALF_OPEN: tras RECOVERY_TIMEOUT segundos, deja pasar una petición de prueba

Threshold: 5 fallos → open por 60s.
"""

import asyncio
import time

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()

# Circuit breaker config
FAILURE_THRESHOLD = 5
RECOVERY_TIMEOUT = 60  # segundos


class CircuitOpenError(Exception):
    """El circuit breaker está abierto — Ollama no está respondiendo."""


class CircuitBreaker:
    """Circuit breaker simple en memoria para llamadas a Ollama."""

    def __init__(self) -> None:
        self._failures = 0
        self._state = "closed"   # closed | open | half_open
        self._opened_at: float = 0.0

    def _try_recover(self) -> None:
        if self._state == "open" and (time.monotonic() - self._opened_at) >= RECOVERY_TIMEOUT:
            self._state = "half_open"
            logger.info("circuit_breaker.half_open")

    def record_success(self) -> None:
        if self._state == "half_open":
            logger.info("circuit_breaker.closed")
        self._failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= FAILURE_THRESHOLD:
            if self._state != "open":
                self._opened_at = time.monotonic()
                logger.warning("circuit_breaker.open", failures=self._failures)
            self._state = "open"

    def allow_request(self) -> bool:
        self._try_recover()
        if self._state == "closed":
            return True
        if self._state == "half_open":
            return True   # deja pasar una petición de prueba
        return False      # open — rechaza


class OllamaClient:
    """Cliente async para Ollama con circuit breaker."""

    def __init__(self) -> None:
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_model
        self.embed_model = settings.ollama_embed_model
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(120.0),
        )
        self._cb = CircuitBreaker()

    async def _post(self, path: str, payload: dict) -> dict:
        """Wrapper con circuit breaker para todas las llamadas POST."""
        if not self._cb.allow_request():
            raise CircuitOpenError(
                f"Circuit breaker abierto — Ollama no responde "
                f"(recovery en {RECOVERY_TIMEOUT}s)"
            )
        try:
            response = await self._client.post(path, json=payload)
            response.raise_for_status()
            self._cb.record_success()
            return response.json()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            self._cb.record_failure()
            raise

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        stream: bool = False,
    ) -> str:
        """Genera texto con el modelo configurado."""
        payload: dict = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": stream,
        }
        if system:
            payload["system"] = system

        log = logger.bind(model=payload["model"], prompt_len=len(prompt))
        log.info("ollama.generate.start")

        result = await self._post("/api/generate", payload)
        log.info("ollama.generate.done", tokens=result.get("eval_count", 0))
        return result["response"]

    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        """Chat completions — formato OpenAI compatible."""
        payload: dict = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
        }
        if system:
            if not any(m.get("role") == "system" for m in messages):
                payload["messages"] = [{"role": "system", "content": system}] + messages

        result = await self._post("/api/chat", payload)
        return result["message"]["content"]

    async def embed(self, text: str) -> list[float]:
        """Genera embedding con nomic-embed-text."""
        result = await self._post(
            "/api/embeddings",
            {"model": self.embed_model, "prompt": text},
        )
        return result["embedding"]

    async def health(self) -> bool:
        """Verifica que Ollama está corriendo y el modelo está disponible."""
        try:
            response = await self._client.get("/api/tags")
            response.raise_for_status()
            models = [m["name"] for m in response.json().get("models", [])]
            return self.model in models
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()


# Instancia global
ollama = OllamaClient()
