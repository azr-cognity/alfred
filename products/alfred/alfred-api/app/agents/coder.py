"""
Agente Coder — el implementador de Alfred (S12).

Cambios respecto a S11:
  - _parse_response() reemplaza _extract_json(): intenta json.loads normal,
    luego json-repair como fallback para docstrings Python no escapados (triple-comilla).
  - Raw log en coder.parse_error para diagnóstico del output real del modelo.
  - SYSTEM_PROMPT reforzado: regla explícita de escaping de triple-comilla.
"""

import json
import re

import structlog
from app.agents.routing import select_coder_model
from app.core.llm import OllamaProvider, AnthropicProvider
from json_repair import repair_json

from app.core.config import settings
from app.core.ollama import ollama
from app.schemas.runs import Task

from .coder_tools import list_files, read_file, search_codebase, write_file

logger = structlog.get_logger()

MAX_RETRIES = 3

CONVENTIONS_PATH = "CONVENTIONS.md"

SYSTEM_PROMPT = """Eres el Coder de Alfred, un experto en desarrollo de software.

Tu responsabilidad es implementar código real, funcional y coherente con el
codebase existente del proyecto.

## Proceso de trabajo
1. Analiza la tarea recibida
2. Revisa el archivo CONVENTIONS.md — sus reglas son obligatorias y tienen
   prioridad sobre cualquier preferencia del modelo
3. Revisa el contexto del codebase proporcionado
4. Genera código completo (no snippets, no ejemplos parciales)
5. Asegúrate de que el código sea coherente con las convenciones existentes

## Stack del proyecto
- Backend: FastAPI (Python 3.11) + SQLModel + Pydantic v2
- Tests: pytest + httpx
- Linting: ruff

## Reglas de código
- Funciones de máximo 50 líneas
- Archivos de máximo 300 líneas
- Siempre usar type hints
- Nunca usar print() — usar structlog
- Los endpoints FastAPI siempre tienen tags y docstring
- Todo código tiene docstring
- UUIDs siempre serializados como str()
- Fechas siempre con timezone=True en columnas SQLAlchemy
- CAST(:param AS type) en queries — nunca :param::type (ADR-007)

## REGLA CRÍTICA DE FORMATO JSON
El campo "content" de cada archivo es un string JSON. Las triples comillas
Python DEBEN estar escapadas como \\\"\\\"\\\" (barra-barra-barra-comilla x3).

CORRECTO   → "content": "def foo():\\n    \\\"\\\"\\\"Docstring.\\\"\\\"\\\"\\n    pass"
INCORRECTO → "content": "def foo():\\n    \"\"\"Docstring.\"\"\"\\n    pass"

Si no escapas las triples comillas el JSON es inválido. El parser rechazará
la respuesta y tendrás que reintentar. Escapa SIEMPRE.

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

Responde ÚNICAMENTE con el JSON. Sin texto adicional, sin markdown, sin <think>.
/no_think
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


def _parse_response(raw: str, log: structlog.BoundLogger) -> dict:
    """Extrae y parsea el JSON de la respuesta del modelo.

    Estrategia en tres capas:
    1. Limpiar <think> y extraer bloque JSON/código
    2. json.loads() directo — happy path
    3. json_repair() — fallback para triple-comillas no escapadas y otros
       errores comunes que el modelo introduce en el campo "content"

    Args:
        raw: texto crudo devuelto por ollama.generate()
        log: logger con contexto ya bindeado (agent, task_id, attempt)

    Returns:
        dict con al menos las claves "files" y "summary"

    Raises:
        json.JSONDecodeError: si ni json.loads ni json_repair logran parsear
    """
    # Capa 1: limpiar thinking tags y extraer bloque
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE).strip()

    # Intentar extraer bloque ```json ... ``` primero
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    candidate = match.group(1).strip() if match else cleaned

    # Si no tiene llaves, buscar el objeto JSON directamente
    if not candidate.startswith("{"):
        match = re.search(r"\{[\s\S]*\}", candidate)
        candidate = match.group(0).strip() if match else candidate

    # Capa 2: parse directo
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as direct_err:
        log.warning(
            "coder.json_loads_failed",
            error=str(direct_err),
            raw_snippet=candidate[:300],  # primeros 300 chars para diagnóstico
        )

    # Capa 3: json-repair fallback
    try:
        repaired = repair_json(candidate, return_objects=True)
        if isinstance(repaired, dict):
            log.info("coder.json_repair_success")
            return repaired
        # repair_json devolvió algo que no es dict — error real
        raise json.JSONDecodeError(
            f"json_repair devolvió {type(repaired).__name__}, se esperaba dict",
            candidate,
            0,
        )
    except Exception as repair_err:
        log.error(
            "coder.json_repair_failed",
            error=str(repair_err),
            raw_full=raw[:800],  # más contexto para el caso extremo
        )
        # Re-lanzar el error original de json.loads para mantener el tipo
        raise json.JSONDecodeError(
            f"Parse fallido con json.loads y json_repair: {repair_err}",
            candidate,
            0,
        ) from repair_err


async def run_coder(
    task: Task,
    run_context: str = "",
    reviewer_feedback: str | None = None,
) -> CoderResult:
    """Ejecuta el agente Coder para una tarea específica.

    Args:
        task: la tarea del plan del Architect.
        run_context: contexto del repo construido por build_run_context_node.
                     Si es "" (vacío), hace búsqueda pgvector propia (fallback).
        reviewer_feedback: feedback del Reviewer si esta es una corrección
                           (None en el primer intento).

    Returns:
        CoderResult con los archivos escritos y el resumen.

    Raises:
        ValueError: si el Coder no logra generar código válido tras MAX_RETRIES.
    """
    log = logger.bind(agent="coder", task_id=task.id, task=task.title)
    is_retry = reviewer_feedback is not None
    has_run_context = bool(run_context.strip())
    log.info("coder.start", is_retry=is_retry, has_run_context=has_run_context)

    # ── Paso 1: Leer CONVENTIONS.md ───────────────────────────────────────────
    conventions = await read_file(CONVENTIONS_PATH)
    if conventions.startswith("ERROR"):
        log.warning("coder.conventions_not_found", path=CONVENTIONS_PATH)
        conventions = "# Sin convenciones disponibles"
    else:
        log.info("coder.conventions_loaded", size=len(conventions))

    # ── Paso 2: Contexto del repo ──────────────────────────────────────────────
    context_parts = []
    all_files = await list_files("app")
    context_parts.append(f"## Estructura del proyecto\n{chr(10).join(all_files)}")

    if has_run_context:
        context_parts.append("## Contexto del repositorio (archivos relevantes)")
        context_parts.append(run_context)
        log.info("coder.context_from_run", context_chars=len(run_context))
    else:
        search_query = f"{task.title} {task.description}"
        relevant_files = await search_codebase(search_query, limit=5)
        log.info("coder.context_from_search", results=len(relevant_files))

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
    user_prompt = f"""## Convenciones obligatorias del proyecto
{conventions}

---

## Tarea a implementar
ID: {task.id}
Título: {task.title}
Descripción: {task.description}
Archivos a crear: {task.files_to_create}
Archivos a modificar: {task.files_to_modify}
Complejidad estimada: {task.estimated_complexity}

## Contexto del codebase
{context}

Implementa la tarea completa siguiendo ESTRICTAMENTE las convenciones del proyecto.
Recuerda: escapa las triples comillas en docstrings como \\\"\\\"\\\".
"""

    if reviewer_feedback:
        user_prompt += f"""
## CORRECCIÓN REQUERIDA
El Reviewer rechazó tu implementación anterior con el siguiente feedback:

{reviewer_feedback}

Corrige específicamente el problema señalado. Mantén todo lo que estaba correcto
y enfócate en resolver lo indicado arriba. Revisa las convenciones si el error
está relacionado con SQLModel, asyncpg, imports o tipos de datos.
"""

    # ── Paso 4: Generar código ─────────────────────────────────────────────────
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        log_attempt = log.bind(attempt=attempt)
        log_attempt.info("coder.attempt")
        try:
            _p_type, _p_model = select_coder_model(task)
            if _p_type == "frontier":
                from app.core.llm import AnthropicProvider
                _prov = AnthropicProvider(model=_p_model, agent="coder")
                _resp = await _prov.generate(system=SYSTEM_PROMPT, user=user_prompt, max_tokens=8192)
            else:
                from app.core.llm import OllamaProvider
                _prov = OllamaProvider(model=_p_model, agent="coder", num_ctx=32768, num_predict=8192)
                _resp = await _prov.generate(system=SYSTEM_PROMPT, user=user_prompt)
            response = _resp.content

            data = _parse_response(response, log_attempt)

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
                    log_attempt.info("coder.file_written", path=path)

            log_attempt.info("coder.done", files=len(files_written))

            return CoderResult(
                files_written=files_written,
                summary=summary,
                task_id=task.id,
            )

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            last_error = e
            log_attempt.warning("coder.parse_error", error=str(e))
            user_prompt += (
                f"\n\nNOTA: intento {attempt} falló con: {e}. "
                "Responde SOLO con JSON válido. "
                "Escapa las triples comillas: \\\"\\\"\\\" no \"\"\"."
            )
            continue

    raise ValueError(
        f"El Coder no pudo implementar la tarea '{task.title}' "
        f"tras {MAX_RETRIES} intentos. Último error: {last_error}"
    )
    




