"""
Módulo: routing
Propósito: Routing de modelos para el agente Coder. Decide qué provider y modelo
           usar en función de la complejidad y el riesgo de la tarea (ADR-011 v2.4).

Dependencias clave:
    - app.core.config: settings.ollama_model, frontier_coder, llm_mode
    - app.schemas.runs: Task (campo estimated_complexity, title, description,
                        files_to_create)

Restricciones:
    - Función pura y síncrona — no hace I/O ni llama a ningún provider.
    - El resultado se pasa a get_provider() en coder.py para instanciar el provider.
    - En modo full_local, get_provider() ignorará el "frontier" del routing.

Consumido por: app/agents/coder.py → get_provider(agent="coder", task_complexity=...)
Versión: 2.0 | Junio 2026 | Owner: AZR
"""

from __future__ import annotations

from app.core.config import settings
from app.schemas.runs import Task

# ---------------------------------------------------------------------------
# Keyword sets para clasificación de tareas
# ---------------------------------------------------------------------------

# Señales de ALTA dificultad + ALTO costo de error → frontier
_HIGH_RISK_KEYWORDS: frozenset[str] = frozenset(
    {
        "conciliacion",
        "reconciliation",
        "matching",
        "motor",
        "rls",
        "multi-tenant",
        "tenant_isolation",
        "auth",
        "autenticacion",
        "seguridad",
        "security",
        "reintento",
        "retry",
        "idempotencia",
        "decimal",
        "monto",
        "financiero",
    }
)

# Señales de boilerplate → local rápido
_BOILERPLATE_KEYWORDS: frozenset[str] = frozenset(
    {
        "schema",
        "pydantic",
        "crud",
        "test",
        "fixture",
        "mock",
        "response_model",
        "router basico",
        "endpoint simple",
        "ping",
        "health",
        "migration simple",
    }
)


# ---------------------------------------------------------------------------
# Función pública
# ---------------------------------------------------------------------------

def select_coder_model(task: Task) -> tuple[str, str]:
    """Determinar el provider y modelo óptimo para una tarea del Coder.

    Retorna (provider_type, model_name) en lugar del str plano de v2.3.
    provider_type: "local" | "frontier"

    Lógica de decisión (en orden de prioridad):
        1. alta complejidad + keyword de riesgo → frontier
        2. alta complejidad sin riesgo          → local 35B (razonamiento local suficiente)
        3. baja complejidad                     → local 14B (boilerplate rápido)
        4. media + boilerplate score ≥ 2 + 1 archivo → local 14B
        5. media + keyword de riesgo            → frontier
        6. media sin señales                    → local 35B (default seguro)

    Args:
        task: Task del Architect con estimated_complexity, title, description,
              files_to_create.

    Returns:
        Tupla (provider_type, model_name). El Coder pasa esto a get_provider().
    """
    combined = (task.title + " " + task.description).lower()

    # ── 1. Alta complejidad ──────────────────────────────────────────────
    if task.estimated_complexity == "high":
        if any(kw in combined for kw in _HIGH_RISK_KEYWORDS):
            # Riesgo alto + complejidad alta → frontier (error costoso de escapar)
            return ("frontier", settings.frontier_coder)
        # Alta complejidad sin keywords de riesgo → 35B local (razonamiento suficiente)
        return ("local", settings.ollama_model)

    # ── 2. Baja complejidad → local rápido ──────────────────────────────
    if task.estimated_complexity == "low":
        return ("local", settings.ollama_model_fast)

    # ── 3. Complejidad media ─────────────────────────────────────────────
    boilerplate_score = sum(1 for kw in _BOILERPLATE_KEYWORDS if kw in combined)
    files_count = len(task.files_to_create) if task.files_to_create else 0

    # Boilerplate claro + archivo único → local rápido
    if boilerplate_score >= 2 and files_count <= 1:
        return ("local", settings.ollama_model_fast)

    # Media + riesgo → frontier
    if any(kw in combined for kw in _HIGH_RISK_KEYWORDS):
        return ("frontier", settings.frontier_coder)

    # Default medio → local principal (35B)
    return ("local", settings.ollama_model)
