"""
Agente Reviewer — el evaluador de Alfred (S7).

Cambios v2.4 (ADR-008, ADR-011):
  - Migrado a get_provider("reviewer") — usa Claude API en modo hybrid.
  - max_tokens=2048 (512 causaba parse_error con respuestas largas).
"""

import json
import re

import structlog

from app.core.llm import get_provider
from app.schemas.runs import Task

from .coder_tools import read_file

logger = structlog.get_logger()

MAX_RETRIES = 3

SYSTEM_PROMPT = """Eres el Reviewer de Alfred, un evaluador de codigo pragmatico y constructivo.

Tu unica responsabilidad es determinar si el codigo generado por el Coder
cumple funcionalmente el objetivo de la tarea asignada.

## Razones validas para rechazar
- El codigo no implementa la funcionalidad descrita en la tarea
- Hay logica incorrecta o rota que haria fallar el comportamiento esperado
- Faltan archivos explicitamente solicitados en files_to_create
- El codigo esta incompleto: funciones vacias, TODOs sin resolver, placeholders

## NO rechaces por estas razones
- Estilo de codigo, formato o convenciones menores
- Imports no utilizados o advertencias de linter
- Nombres de variables suboptimos pero funcionales
- Ausencia de comentarios o docstrings adicionales

## Criterio de aprobacion
Si el codigo implementa correctamente la funcionalidad y es funcional, apruebalo.

## Formato de respuesta
Responde UNICAMENTE con JSON valido, sin texto adicional, sin markdown.

Si el codigo cumple la tarea:
{"approved": true, "feedback": "breve confirmacion de que se verifico"}

Si el codigo NO cumple la tarea:
{"approved": false, "feedback": "instruccion concreta para que el Coder corrija"}
/no_think
"""


class ReviewerResult:
    def __init__(self, approved: bool, feedback: str, task_id: str) -> None:
        self.approved = approved
        self.feedback = feedback
        self.task_id = task_id


def _extract_json(text: str) -> str:
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return match.group(0).strip()
    return text.strip()


def _parse_reviewer_result(raw_json: str, task_id: str) -> ReviewerResult:
    data = json.loads(raw_json)
    approved = data.get("approved")
    if not isinstance(approved, bool):
        raise ValueError(f"'approved' debe ser boolean, recibido: {approved}")
    feedback = data.get("feedback", "")
    if not isinstance(feedback, str):
        raise ValueError(f"'feedback' debe ser string, recibido: {feedback}")
    return ReviewerResult(approved=approved, feedback=feedback, task_id=task_id)


async def run_reviewer(task: Task, files_written: list[str]) -> ReviewerResult:
    log = logger.bind(agent="reviewer", task_id=task.id, task=task.title)
    log.info("reviewer.start", files=len(files_written))

    file_contents: list[str] = []
    for path in files_written:
        content = await read_file(path)
        file_contents.append(f"## Archivo: {path}\n```\n{content}\n```")

    files_section = "\n\n".join(file_contents) if file_contents else "No se escribieron archivos."

    user_prompt = f"""## Tarea a evaluar
ID: {task.id}
Titulo: {task.title}
Descripcion: {task.description}
Archivos esperados a crear: {task.files_to_create}
Archivos esperados a modificar: {task.files_to_modify}

## Archivos escritos por el Coder
{files_section}

Evalua si el codigo cumple funcionalmente el objetivo de la tarea.
"""

    provider = get_provider("reviewer", task_complexity=task.estimated_complexity)
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        log.info("reviewer.attempt", attempt=attempt)

        prompt = user_prompt
        if last_error and attempt > 1:
            prompt = (
                f"{user_prompt}\n\n"
                f"NOTA: Tu respuesta anterior fallo con este error: {last_error}\n"
                f"Asegurate de responder UNICAMENTE con JSON valido."
            )

        try:
            llm_response = await provider.generate(
                system=SYSTEM_PROMPT,
                user=prompt,
                temperature=0.4,
                max_tokens=2048,
            )
            raw_json = _extract_json(llm_response.content)
            result = _parse_reviewer_result(raw_json, task.id)
            log.info("reviewer.done", approved=result.approved, attempt=attempt)
            return result

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            last_error = e
            log.warning("reviewer.parse_error", attempt=attempt, error=str(e))
            continue

    return ReviewerResult(
        approved=False,
        feedback=f"No se pudo evaluar el codigo tras {MAX_RETRIES} intentos. Ultimo error: {last_error}",
        task_id=task.id,
    )
