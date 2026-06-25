"""
Módulo: llm
Propósito: Abstracción de provider de LLM (ADR-011). Desacopla la lógica de agentes
           de Ollama y Claude API. Punto único de inferencia: los agentes nunca
           llaman a ollama.generate() ni al SDK anthropic directamente.

Dependencias clave:
    - app.core.ollama: instancia singleton OllamaClient (circuit breaker incluido)
    - app.core.config: settings.llm_mode, settings.anthropic_api_key, modelos
    - anthropic: SDK oficial (pip install anthropic)

Restricciones:
    - ADR-009: format=None en OllamaProvider — NUNCA format="json" con qwen3.5
    - ADR-012: AnthropicProvider pone system como bloque con cache_control="ephemeral"
    - Gotcha #27: NO aplicar _strip_think() en AnthropicProvider — corrompe el JSON
    - Gotcha #25: ANTHROPIC_API_KEY en .env antes del primer run en modo hybrid
    - Gotcha #29: en tests, mockear LLMProvider (ABC), no las implementaciones concretas

Consumido por: app/agents/architect.py, reviewer.py, coder.py, tester.py, auditor.py
Versión: 1.0 | Junio 2026 | Owner: AZR
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import anthropic
import structlog

from app.core.config import settings
from app.core.ollama import ollama as _ollama_client  # singleton con circuit breaker

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Precios de referencia — junio 2026, USD por millón de tokens
# ---------------------------------------------------------------------------
_PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0},
    "claude-opus-4-8":   {"input": 5.0,  "output": 25.0},
    "claude-haiku-4-5":  {"input": 1.0,  "output": 5.0},
}
# Cache read ≈ 10 % del costo de input (ADR-012)
_CACHE_READ_DISCOUNT: float = 0.10

# Parámetros de inferencia por agente para OllamaProvider (ADR-009)
_OLLAMA_AGENT_PARAMS: dict[str, dict[str, int]] = {
    "architect": {"num_ctx": 16384, "num_predict": 4096},
    "coder":     {"num_ctx": 32768, "num_predict": 8192},
    "reviewer":  {"num_ctx": 8192,  "num_predict": 2048},
    "tester":    {"num_ctx": 8192,  "num_predict": 4096},
    "auditor":   {"num_ctx": 4096,  "num_predict": 1024},
}
_DEFAULT_OLLAMA_PARAMS: dict[str, int] = {"num_ctx": 16384, "num_predict": 4096}

_THINK_PATTERN = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Tipos de datos
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    """Respuesta normalizada de cualquier provider de LLM.

    Attributes:
        content: Texto de salida del modelo, limpio de artefactos de razonamiento.
        input_tokens: Tokens de entrada (exacto para frontier, estimado para local).
        output_tokens: Tokens de salida (exacto para frontier, estimado para local).
        model: Identificador del modelo que generó la respuesta.
        cache_hit: True si el prefijo cacheado fue leído (solo AnthropicProvider).
    """

    content: str
    input_tokens: int
    output_tokens: int
    model: str
    cache_hit: bool = field(default=False)


# ---------------------------------------------------------------------------
# Interfaz ABC
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """Interfaz única de inferencia (ADR-011). Todos los providers la implementan."""

    @abstractmethod
    async def generate(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generar una respuesta a partir de system + user prompt.

        Args:
            system: Instrucciones de rol y restricciones del agente.
            user: Prompt con la tarea concreta del agente.
            temperature: Temperatura de muestreo (0.0 – 1.0).
            max_tokens: Límite de tokens de salida (usado por AnthropicProvider;
                        OllamaProvider usa su num_predict de instancia).

        Returns:
            LLMResponse con contenido, conteos de tokens y metadatos.
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _strip_think(raw: str) -> str:
    """Eliminar bloques <think> emitidos por qwen3.5 en modo razonamiento.

    Solo debe aplicarse en OllamaProvider. Gotcha #27: NO usar en AnthropicProvider.
    """
    return _THINK_PATTERN.sub("", raw).strip()


def _estimate_cost_usd(response: LLMResponse) -> float:
    """Calcular costo estimado en USD. Retorna 0.0 para modelos locales (sin cargo).

    Para tokens cacheados aplica _CACHE_READ_DISCOUNT sobre el costo de input.
    """
    prices = _PRICES.get(response.model)
    if not prices:
        return 0.0
    input_rate = prices["input"] * (_CACHE_READ_DISCOUNT if response.cache_hit else 1.0)
    input_cost = (response.input_tokens / 1_000_000) * input_rate
    output_cost = (response.output_tokens / 1_000_000) * prices["output"]
    return round(input_cost + output_cost, 6)


def _log_llm_call(agent: str, response: LLMResponse, latency_ms: float) -> None:
    """Emitir evento structlog 'llm.call_completed' y alerta de costo si aplica."""
    cost_usd = _estimate_cost_usd(response)
    logger.info(
        "llm.call_completed",
        agent=agent,
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_usd=cost_usd,
        cached=response.cache_hit,
        latency_ms=latency_ms,
    )
    if cost_usd > 0 and cost_usd > settings.run_cost_alert_usd:
        logger.warning(
            "run.cost_alert",
            agent=agent,
            cost_usd=cost_usd,
            threshold_usd=settings.run_cost_alert_usd,
        )


# ---------------------------------------------------------------------------
# OllamaProvider
# ---------------------------------------------------------------------------

class OllamaProvider(LLMProvider):
    """Provider local — wrappea OllamaClient con circuit breaker (ADR-001, ADR-009).

    Aplica _strip_think() centralizando la quirk de qwen3.5 (Gotcha #5).
    Los conteos de tokens son estimados (1 token ≈ 4 chars); el costo es $0.
    """

    def __init__(
        self,
        model: str,
        agent: str = "unknown",
        num_ctx: int = 16384,
        num_predict: int = 4096,
    ) -> None:
        self._model = model
        self._agent = agent
        self._num_ctx = num_ctx
        self._num_predict = num_predict

    async def generate(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,  # ignorado — usa self._num_predict (config por agente)
    ) -> LLMResponse:
        """Llamar a OllamaClient con los parámetros del agente."""
        start = time.perf_counter()

        # ADR-009: format=None obligatorio con qwen3.5 (format="json" → response_len=0)
        raw: str = await _ollama_client.generate(
            model=self._model,
            prompt=user,
            system=system,
            format=None,
            num_ctx=self._num_ctx,
            num_predict=self._num_predict,
            temperature=temperature,
        )

        # Gotcha #5: centralizar stripping aquí — los agentes ya no lo necesitan
        content = _strip_think(raw)
        latency_ms = round((time.perf_counter() - start) * 1000, 1)

        # Estimación de tokens (sin costo real para local)
        input_tokens = max(1, (len(system) + len(user)) // 4)
        output_tokens = max(1, len(content) // 4)

        response = LLMResponse(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=self._model,
        )
        _log_llm_call(self._agent, response, latency_ms)
        return response


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------

class AnthropicProvider(LLMProvider):
    """Provider frontier — Claude API vía SDK anthropic (ADR-008, ADR-012).

    El system prompt se envía como bloque con cache_control="ephemeral" para
    activar prompt caching. Gotcha #27: NO aplicar _strip_think() aquí.
    """

    def __init__(self, model: str, agent: str = "unknown") -> None:
        self._model = model
        self._agent = agent
        api_key = settings.anthropic_api_key or None
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def generate(
        self,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Llamar a Claude API con prompt caching en el system prompt (ADR-012)."""
        start = time.perf_counter()

        # ADR-012: system como bloque con cache_control para activar caching
        # El prefijo estático (CONVENTIONS.md, ALFRED_MISSION.md) debe ir en 'system'
        msg = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
        )

        # Gotcha #27: NO strip_think — frontier no emite <think> y stripear corrompe JSON
        content = msg.content[0].text if msg.content else ""
        latency_ms = round((time.perf_counter() - start) * 1000, 1)

        usage = msg.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_hit = cache_read > 0

        response = LLMResponse(
            content=content,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            model=self._model,
            cache_hit=cache_hit,
        )
        _log_llm_call(self._agent, response, latency_ms)
        return response


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _select_local_model(agent: str) -> str:
    """Elegir modelo local según el agente: 14B para tareas mecánicas, 35B para el resto."""
    _FAST_AGENTS = frozenset({"tester", "auditor"})
    return settings.ollama_model_fast if agent in _FAST_AGENTS else settings.ollama_model


def get_provider(agent: str, task_complexity: str = "medium") -> LLMProvider:
    """Retornar el provider correcto según agente, complejidad y settings.llm_mode.

    Modos:
        full_local:    Todo en Ollama — sin llamadas externas (soberanía de datos).
        full_frontier: Todo en Claude API — máxima calidad.
        hybrid:        Default. Architect + Reviewer en frontier; Coder/Tester/Auditor
                       en local salvo que task_complexity=="high" para Coder.

    Args:
        agent: Nombre del agente ("architect", "reviewer", "coder", "tester", "auditor").
        task_complexity: "low" | "medium" | "high" — afecta routing en modo hybrid.

    Returns:
        LLMProvider configurado para el agente y modo actuales.
    """
    mode = settings.llm_mode
    params = _OLLAMA_AGENT_PARAMS.get(agent, _DEFAULT_OLLAMA_PARAMS)

    # ── full_local ──────────────────────────────────────────────────────────
    if mode == "full_local":
        return OllamaProvider(
            model=settings.ollama_model, agent=agent, **params
        )

    # ── full_frontier ────────────────────────────────────────────────────────
    if mode == "full_frontier":
        model = (
            "claude-opus-4-8"
            if task_complexity == "high"
            else settings.frontier_architect
        )
        return AnthropicProvider(model=model, agent=agent)

    # ── hybrid (default) ─────────────────────────────────────────────────────
    _FRONTIER_AGENTS = frozenset({"architect", "reviewer"})

    if agent in _FRONTIER_AGENTS:
        # Architect en Opus para alta complejidad (Gotcha #28: fijar versión en config)
        model = (
            "claude-opus-4-8"
            if agent == "architect" and task_complexity == "high"
            else settings.frontier_architect
        )
        return AnthropicProvider(model=model, agent=agent)

    if agent == "coder" and task_complexity == "high":
        return AnthropicProvider(model=settings.frontier_coder, agent=agent)

    # Tester, Auditor y Coder no-high → local
    local_model = _select_local_model(agent)
    return OllamaProvider(model=local_model, agent=agent, **params)
