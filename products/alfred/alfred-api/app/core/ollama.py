"""
Módulo: ollama
Propósito: Cliente HTTP singleton para Ollama con circuit breaker y soporte format:json.
           Toda inferencia del sistema pasa por este módulo.

Dependencias clave:
    - Ollama corriendo en host Windows (fuera de Docker): localhost:11434
    - app.core.config: Settings con ollama_base_url y ollama_timeout

Restricciones:
    - Singleton — una sola instancia en todo el proceso
    - Circuit breaker: 5 fallos consecutivos → abre por 60s
    - format="json" SIEMPRE en llamadas de agentes — elimina retries por JSONDecodeError
    - _strip_think_and_extract_json() sigue siendo necesario incluso con format=json
      (qwen3.5 emite <think> antes del JSON y Ollama no lo filtra automáticamente)

Consumido por: app/agents/*.py
Versión: 1.1 | S11 — agrega format:json | Junio 2026 | Owner: AZR
"""

import time
import json
import asyncio
import structlog
import httpx
from enum import Enum
from app.core.config import settings

logger = structlog.get_logger(__name__)


class OllamaUnavailableError(Exception):
    """Ollama no disponible — circuit breaker abierto o error de conexión."""
    pass


class CircuitState(Enum):
    CLOSED = "closed"       # normal
    OPEN = "open"           # bloqueado tras N fallos
    HALF_OPEN = "half_open" # primer intento de recuperación


class OllamaClient:
    """
    Cliente HTTP singleton para Ollama con circuit breaker integrado.

    Patrón de uso:
        client = OllamaClient.get_instance()
        raw = await client.generate(model="qwen3.5:35b-a3b", prompt="...", format="json")
    """

    _instance: "OllamaClient | None" = None

    # Circuit breaker
    FAILURE_THRESHOLD: int = 5
    RECOVERY_TIMEOUT: int = 60   # segundos antes de intentar half-open

    def __init__(self) -> None:
        self._state = CircuitState.CLOSED
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0
        self._base_url: str = settings.ollama_base_url.rstrip("/")
        self._timeout: float = float(settings.ollama_timeout)

    @classmethod
    def get_instance(cls) -> "OllamaClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def _check_circuit(self) -> None:
        """Verificar estado del circuit breaker antes de cada llamada."""
        if self._state == CircuitState.CLOSED:
            return
        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._last_failure_time
            if elapsed >= self.RECOVERY_TIMEOUT:
                self._state = CircuitState.HALF_OPEN
                logger.info("ollama.circuit_half_open", elapsed_s=round(elapsed, 1))
            else:
                raise OllamaUnavailableError(
                    f"Circuit breaker OPEN — esperar {self.RECOVERY_TIMEOUT - int(elapsed)}s"
                )

    def record_success(self) -> None:
        """Registrar éxito — cierra el circuit breaker si estaba half-open."""
        if self._state != CircuitState.CLOSED:
            logger.info("ollama.circuit_closed")
        self._state = CircuitState.CLOSED
        self._failure_count = 0

    def record_failure(self) -> None:
        """Registrar fallo — incrementa contador y abre el CB si supera el umbral."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self.FAILURE_THRESHOLD or self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.error(
                "ollama.circuit_open",
                failure_count=self._failure_count,
                threshold=self.FAILURE_THRESHOLD,
            )

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    async def _post(self, endpoint: str, payload: dict) -> dict:
        """Llamada HTTP POST a Ollama con circuit breaker y timeout."""
        self._check_circuit()
        url = f"{self._base_url}/{endpoint}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                self.record_success()
                return data
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            self.record_failure()
            raise OllamaUnavailableError(f"Ollama no responde: {e}") from e
        except httpx.HTTPStatusError as e:
            self.record_failure()
            raise OllamaUnavailableError(f"Ollama HTTP {e.response.status_code}") from e

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    async def generate(
        self,
        model: str,
        prompt: str,
        system: str | None = None,
        format: str | None = "json",   # "json" por defecto — SIEMPRE para agentes
        temperature: float = 0.2,
        num_predict: int = 4096,
        num_ctx: int = 16384,
        stream: bool = False,
    ) -> str:
        """
        Generar texto con un modelo Ollama.

        Args:
            model: Nombre del modelo (ej: "qwen3.5:35b-a3b", "qwen2.5-coder:7b").
            prompt: Prompt del usuario.
            system: System prompt del agente.
            format: "json" fuerza output JSON válido. None para texto libre.
                    USAR "json" EN TODOS LOS AGENTES — elimina retries por JSONDecodeError.
            temperature: 0.1–0.2 para código, 0.3–0.4 para análisis.
            num_predict: Tokens máximos de output.
            stream: False siempre (streaming manejado por SSE en otro nivel).

        Returns:
            Texto generado por el modelo. Siempre limpiar con
            _strip_think_and_extract_json() antes de json.loads().

        Raises:
            OllamaUnavailableError: Si el circuit breaker está abierto o Ollama no responde.
        """
        start = time.time()
        payload: dict = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": num_ctx,
            },
        }
        if system:
            payload["system"] = system
        if format:
            payload["format"] = format

        logger.debug(
            "ollama.generate.start",
            model=model,
            format=format,
            prompt_len=len(prompt),
        )

        data = await self._post("api/generate", payload)
        raw: str = data.get("response", "")
        latency = round((time.time() - start) * 1000, 1)

        logger.info(
            "ollama.generate.done",
            model=model,
            format=format,
            latency_ms=latency,
            response_len=len(raw),
            eval_count=data.get("eval_count"),
        )
        return raw

    async def embed(self, model: str, text: str) -> list[float]:
        """
        Generar embedding para un texto.

        Args:
            model: Modelo de embeddings (ej: "nomic-embed-text").
            text: Texto a embeddear.

        Returns:
            Lista de floats (768 dims para nomic-embed-text).
        """
        data = await self._post("api/embeddings", {"model": model, "prompt": text})
        return data["embedding"]

    async def health(self) -> bool:
        """Verificar que Ollama está respondiendo. Usado en /api/health."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base_url}/api/tags")
                return response.status_code == 200
        except Exception:
            return False


# Singleton de conveniencia
ollama_client = OllamaClient.get_instance()
ollama = ollama_client
