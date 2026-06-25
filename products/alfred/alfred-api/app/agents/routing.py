"""
Módulo: routing
Propósito: Selección de modelo Ollama según la complejidad de una Task.
           Función pura y síncrona — sin I/O, sin efectos secundarios.
           Permite que el Coder use qwen2.5-coder:14b para tareas boilerplate
           (3x más rápido) y reserve qwen3.5:35b-a3b para lógica compleja.

Consumido por:
    app.orchestrator.nodes: coder_node llama select_coder_model()
    antes de invocar run_coder() para elegir el modelo correcto.

Restricciones:
    - Función síncrona y pura (sin await, sin DB, sin Ollama)
    - Sin print() — sin logs (función pura no necesita logging)
    - Type hints completos en toda función pública

Versión: 1.0 | Junio 2026 | Owner: AZR
"""

from app.core.config import settings
from app.schemas.runs import Task

# ---------------------------------------------------------------------------
# Constantes de clasificación
# ---------------------------------------------------------------------------

BOILERPLATE_KEYWORDS: frozenset[str] = frozenset({
    "schema",
    "pydantic",
    "crud",
    "test",
    "fixture",
    "mock",
    "response_model",
    "router basico",
    "endpoint simple",
})

BOILERPLATE_MAX_FILES: int = 1


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def select_coder_model(task: Task) -> str:
    """Selecciona el modelo Ollama para ejecutar una task del Coder.

    Lógica de clasificación (en orden de prioridad):
      1. Si estimated_complexity == 'high' → siempre modelo principal (35B)
      2. Si estimated_complexity == 'low' → modelo rápido (14B)
      3. Si el título o descripción contiene una keyword boilerplate
         Y files_to_create tiene exactamente 1 archivo → modelo rápido (14B)
      4. Caso contrario → modelo principal (35B)

    Args:
        task: Task del plan a evaluar.

    Returns:
        Nombre del modelo Ollama a usar (string). Uno de:
        - settings.ollama_model      (qwen3.5:35b-a3b  — razonamiento)
        - settings.ollama_model_fast (qwen2.5-coder:14b — boilerplate)
    """
    # Alta complejidad siempre usa el modelo principal
    if task.estimated_complexity == "high":
        return settings.ollama_model

    # Baja complejidad siempre usa el modelo rápido
    if task.estimated_complexity == "low":
        return settings.ollama_model_fast

    # Complejidad media: clasificar por keywords + número de archivos
    title_lower = task.title.lower()
    desc_lower = task.description.lower() if task.description else ""
    combined = f"{title_lower} {desc_lower}"

    has_keyword = any(kw in combined for kw in BOILERPLATE_KEYWORDS)
    single_file = len(task.files_to_create) <= BOILERPLATE_MAX_FILES

    if has_keyword and single_file:
        return settings.ollama_model_fast

    return settings.ollama_model
