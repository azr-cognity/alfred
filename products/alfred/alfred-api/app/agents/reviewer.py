"""
Agente Reviewer — el evaluador de Alfred (S7).

Cambios respecto a S6:
  - SYSTEM_PROMPT ajustado: distingue entre razones válidas e inválidas para
    rechazar. Deja los detalles de estilo y formato para OPA.

Cambios v2.4 (ADR-008, ADR-011):
  - Migrado a get_provider("reviewer") — usa Claude API en modo hybrid.
  - _extract_json ya no necesita strip_think: OllamaProvider lo centraliza.
  - temperature=0.4 pasada explícitamente a provider.generate().
  - task.estimated_complexity pasada a get_provider() para routing futuro.

Responsabilidad:
  Evalúa si el código generado por el Coder cumple el objetivo de la task.

Cómo funciona:
  1. Recibe la task y la lista de archivos escritos por el Coder
  2. Lee el contenido real de los archivos
  3. Llama al provider via get_provider() (Claude API o Ollama según modo)
  4. Parsea la respuesta JSON en un ReviewerResult
  5. Si el JSON es inválido, reintenta hasta MAX_RETRIES veces
  6. Si no puede parsear tras MAX_RETRIES, retorna approved=False (no lanza excepción)
"""

import json
import re

import structlog

from app.core.llm import get_provider
from app.schemas.runs import Task

from .coder_tools import read_file

logger = structlog.get_logger()

MAX_RETRIES = 3

# ── System prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres el Reviewer de Alfred, un evaluador de código pragmático y constructivo.

Tu única responsabilidad es determinar si el código generado por el Coder
cumple funcionalmente el objetivo de la tarea asignada.

## Razones válidas para rechazar
- El código no implementa la funcionalidad descrita en la tarea
- Hay lógica incorrecta o rota que haría fallar el comportamiento esperado
- Faltan archivos explícitamente solicitados en files_to_create
- El código está incompleto: funciones vacías, TODOs sin resolver, placeholders

## NO rechaces por estas razones (las maneja OPA u otras capas)
- Estilo de código, formato o convenciones menores de escritura
- Imports no utilizados o advertencias de linter
- Nombres de variables subóptimos pero funcionales
- Ausencia de comentarios o docstrings adicionales
- Diferencias de opinión sobre la implementación si el resultado es funcionalmente correcto
- Pequeños detalles que no afectan el comportamiento del código

## Criterio de aprobación
Si el código implementa correctamente la funcionalidad descrita y es funcional,
apruébalo aunque tenga imperfecciones menores. El estándar es: ¿funciona y
cumple el objetivo? No: ¿es perfecto?

## Formato de respuesta
Responde ÚNICAMENTE con un objeto JSON válido, sin texto adicional, sin markdown, sin explicaciones.

Si el código cumple la tarea:
{"approved": true, "feedback": "breve confirmación de qué se verificó"}

Si el código NO cumple la tarea:
{"approved": false, "feedback": "instrucción concreta y específica para que el Coder corrija el problema funcional"}
/no_think
"""


class ReviewerResult:
    """Resultado de la evaluación del Reviewer."""

    def __init__(
        self,
        approved: bool,
        feedback: str,
        task_id: str,
    ) -> None:
        self.approved = approved
        self.feedback = feedback
        self.task_id = task_id


def _extract_json(text: str) -> str:
    """Extrae el JSON de la respuesta del modelo.

    No aplica strip_think: OllamaProvider ya lo centraliza (ADR-011).
    AnthropicProvider no emite <think> (Gotcha #27).
    """
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return match.group(0).strip()
    return text.strip()


def _parse_reviewer_result(raw_json: str, task_id: str) -> ReviewerResult:
    """Parsea el JSON del modelo en un ReviewerResult validado.

    Raises:
        ValueError: Si el JSON es inválido o no tiene la estructura esperada.
    """
    data = json.loads(raw_json)

    approved = data.get("approved")
    if not isinstance(approved, bool):
        raise ValueError(f"'approved' debe ser boolean, recibido: {approved}")

    feedback = data.get("feedback", "")
    if not isinstance(feedback, str):
        raise ValueError(f"'feedback' debe ser string, recibido: {feedback}")

    return ReviewerResult(
        approved=approved,
        feedback=feedback,
        task_id=task_id,
    )


async def run_reviewer(task: Task, files_written: list[str]) -> ReviewerResult:
    """Punto de entrada del agente Reviewer.

    En modo hybrid usa Claude API (ADR-008); en full_local usa Ollama —
    transparente para este módulo (ADR-011).

    Args:
        task: la tarea del plan del Architect.
        files_written: rutas de archivos escritos por el Coder.

    Returns:
        ReviewerResult con approved, feedback y task_id.
    """
    log = logger.bind(agent="reviewer", task_id=task.id, task=task.title)
    log.info("reviewer.start", files=len(files_written))

    # ── Leer contenido de los archivos escritos ──────────────────────────────
    file_contents: list[str] = []
    for path in files_written:
        content = await read_file(path)
        file_contents.append(f"## Archivo: {path}\n```\n{content}\n```")

    files_section = (
        "\n\n".join(file_contents)
        if file_contents
        else "No se escribieron archivos."
    )

    user_prompt = f"""## Tarea a evaluar
ID: {task.id}
Título: {task.title}
Descripción: {task.description}
Archivos esperados a crear: {task.files_to_create}
Archivos esperados a modificar: {task.files_to_modify}

## Archivos escritos por el Coder
{files_section}

Evalúa si el código cumple funcionalmente el objetivo de la tarea.
Aprueba si implementa la funcionalidad correctamente, aunque tenga imperfecciones menores de estilo.
"""

    # ADR-011: get_provider() decide Ollama vs Claude API según settings.llm_mode
    # Pasamos estimated_complexity para routing futuro (en hybrid, reviewer siempre es frontier)
    provider = get_provider("reviewer", task_complexity=task.estimated_complexity)

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        log.info("reviewer.attempt", attempt=attempt)

        prompt = user_prompt
        if last_error and attempt > 1:
            prompt = (
                f"{user_prompt}\n\n"
                f"NOTA: Tu respuesta anterior falló con este error: {last_error}\n"
                f"Asegúrate de responder ÚNICAMENTE con JSON válido."
            )

        try:
            # ADR-008: frontier en modo hybrid; ADR-011: interfaz única
            llm_response = await provider.generate(
                system=SYSTEM_PROMPT,
                user=prompt,
                temperature=0.4,
                max_tokens=512,   # respuesta corta: solo approved + feedback
            )

            raw_json = _extract_json(llm_response.content)
            result = _parse_reviewer_result(raw_json, task.id)

            log.info(
                "reviewer.done",
                approved=result.approved,
                attempt=attempt,
            )
            return result

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            last_error = e
            log.warning("reviewer.parse_error", attempt=attempt, error=str(e))
            continue

    return ReviewerResult(
        approved=False,
        feedback=(
            f"No se pudo evaluar el código tras {MAX_RETRIES} intentos. "
            f"Último error: {last_error}"
        ),
        task_id=task.id,
    )