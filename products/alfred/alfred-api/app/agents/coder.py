"""
Agente Coder — el implementador de Alfred (S7).

Cambios respecto a S6:
  - run_coder acepta reviewer_feedback: str | None = None
  - Si hay feedback, se agrega al user_prompt como sección de corrección
    para que el Coder entienda qué rechazó el Reviewer en el intento anterior.

Responsabilidad:
  Recibe una tarea del plan del Architect y genera el código correspondiente.
  Usa búsqueda semántica para entender las convenciones del proyecto antes
  de escribir una sola línea.

Flujo:
  1. Recibe la tarea (Task) del Architect
  2. Busca archivos relevantes en el codebase (search_codebase)
  3. Lee los archivos más relevantes para entender convenciones (read_file)
  4. Genera el código con el modelo local
  5. Escribe los archivos (write_file)
  6. Retorna un CoderResult con los archivos creados/modificados
"""

import json
import re

import structlog

from app.core.config import settings
from app.core.ollama import ollama
from app.schemas.runs import Task

from .coder_tools import list_files, read_file, search_codebase, write_file

logger = structlog.get_logger()

MAX_RETRIES = 3


SYSTEM_PROMPT = """Eres el Coder de Alfred, un experto en desarrollo de software.

Tu responsabilidad es implementar código real, funcional y coherente con el
codebase existente del proyecto.

## Proceso de trabajo
1. Analiza la tarea recibida
2. Revisa el contexto del codebase proporcionado
3. Genera código completo (no snippets, no ejemplos parciales)
4. Asegúrate de que el código sea coherente con las convenciones existentes

## Stack del proyecto
- Backend: FastAPI (Python 3.11) + SQLModel + Pydantic v2
- Tests: pytest + httpx
- Linting: ruff

## Reglas de código
- Funciones de máximo 50 líneas
- Archivos de máximo 300 líneas
- Siempre usar type hints
- Nunca usar print() — usar structlog
- Los endpoints FastAPI siempre tienen response_model
- Todo código tiene docstring

## Formato de respuesta
Responde con un JSON que contenga los archivos a crear o modificar:

{
  "files": [
    {
      "path": "ruta/relativa/al/archivo.py",
      "content": "contenido completo del archivo",
      "action": "create"
    }
  ],
  "summary": "qué se implementó y por qué se tomaron estas decisiones"
}

Responde ÚNICAMENTE con el JSON. Sin texto adicional, sin markdown.
"""


class CoderResult:
    """Resultado de la ejecución del Coder."""

    def __init__(
        self,
        files_written: list[str],
        summary: str,
        task_id: str,
    ) -> None:
        self.files_written = files_written
        self.summary = summary
        self.task_id = task_id
        self.success = len(files_written) > 0


def _extract_json(text: str) -> str:
    """Extrae JSON de la respuesta del modelo."""
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return match.group(0).strip()
    return text.strip()


async def run_coder(
    task: Task,
    reviewer_feedback: str | None = None,
) -> CoderResult:
    """
    Ejecuta el agente Coder para una tarea específica.

    Args:
        task: la tarea del plan del Architect
        reviewer_feedback: feedback del Reviewer si esta es una corrección
                           (None en el primer intento)

    Returns:
        CoderResult con los archivos escritos y el resumen
    """
    log = logger.bind(agent="coder", task_id=task.id, task=task.title)
    is_retry = reviewer_feedback is not None
    log.info("coder.start", is_retry=is_retry)

    # ── Paso 1: Buscar contexto relevante ─────────────────────────────────────
    search_query = f"{task.title} {task.description}"
    relevant_files = await search_codebase(search_query, limit=5)

    # ── Paso 2: Leer archivos más relevantes ──────────────────────────────────
    context_parts = []

    all_files = await list_files("app")
    context_parts.append(f"## Estructura del proyecto\n{chr(10).join(all_files)}")

    for result in relevant_files[:3]:
        if result["similarity"] > 0.3:
            content = await read_file(result["file_path"])
            if not content.startswith("ERROR"):
                context_parts.append(
                    f"## Archivo existente: {result['file_path']}\n"
                    f"(similitud: {result['similarity']})\n"
                    f"```python\n{content[:1500]}\n```"
                )

    context = "\n\n".join(context_parts)

    # ── Paso 3: Construir prompt ───────────────────────────────────────────────
    user_prompt = f"""## Tarea a implementar
ID: {task.id}
Título: {task.title}
Descripción: {task.description}
Archivos a crear: {task.files_to_create}
Archivos a modificar: {task.files_to_modify}
Complejidad estimada: {task.estimated_complexity}

## Contexto del codebase
{context}

Implementa la tarea completa siguiendo las convenciones del proyecto.
"""

    # S7: inyectar feedback del Reviewer si es un reintento
    if reviewer_feedback:
        user_prompt += f"""
## CORRECCIÓN REQUERIDA
El Reviewer rechazó tu implementación anterior con el siguiente feedback:

{reviewer_feedback}

Corrige específicamente el problema señalado. Mantén todo lo que estaba correcto
y enfócate en resolver lo indicado arriba.
"""

    # ── Paso 4: Generar código ─────────────────────────────────────────────────
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        log.info("coder.attempt", attempt=attempt)

        try:
            response = await ollama.generate(
                prompt=user_prompt,
                system=SYSTEM_PROMPT,
                model=settings.ollama_model,
            )

            raw_json = _extract_json(response)
            data = json.loads(raw_json)

            files = data.get("files", [])
            summary = data.get("summary", "")

            if not files:
                raise ValueError("El modelo no generó ningún archivo")

            # ── Paso 5: Escribir archivos ──────────────────────────────────────
            files_written = []
            for file_info in files:
                path = file_info.get("path", "")
                content = file_info.get("content", "")

                if not path or not content:
                    continue

                result = await write_file(path, content)
                if result.startswith("OK"):
                    files_written.append(path)
                    log.info("coder.file_written", path=path)

            log.info("coder.done", files=len(files_written), attempt=attempt)

            return CoderResult(
                files_written=files_written,
                summary=summary,
                task_id=task.id,
            )

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            last_error = e
            log.warning("coder.parse_error", attempt=attempt, error=str(e))
            user_prompt += f"\n\nNOTA: intento anterior falló: {e}. Responde SOLO con JSON válido."
            continue

    raise ValueError(
        f"El Coder no pudo implementar la tarea '{task.title}' "
        f"tras {MAX_RETRIES} intentos. Último error: {last_error}"
    )
