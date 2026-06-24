"""
Agente Architect — el planificador de Alfred.

Responsabilidad:
  Recibe un prompt en lenguaje natural y produce un plan JSON estructurado
  con tareas atómicas, prioridades y dependencias.

Cómo funciona:
  1. Recibe el prompt del usuario
  2. Construye un system prompt con el contexto del proyecto (stack, constraints)
  3. Llama a qwen3.5:35b-a3b via Ollama
  4. Parsea la respuesta JSON en un objeto Plan validado por Pydantic
  5. Si el JSON es inválido, reintenta hasta MAX_RETRIES veces
"""

import json
import re

import structlog

from app.core.config import settings
from app.core.ollama import ollama
from app.schemas.runs import Plan, Task, TaskPriority

logger = structlog.get_logger()

MAX_RETRIES = 3

# ── System prompt ──────────────────────────────────────────────────────────────
# Este es el "cerebro" del Architect. Define su rol, restricciones y el
# formato exacto que debe producir. Es la pieza más importante del agente.

SYSTEM_PROMPT = """Eres el Architect de Alfred, un asistente de desarrollo de software.

Tu única responsabilidad es analizar un objetivo de desarrollo y producir un plan
estructurado de implementación en formato JSON.

## Stack canónico del proyecto
- Backend: FastAPI (Python 3.11) + SQLModel + Pydantic v2
- Frontend: Next.js 15 App Router + shadcn/ui + Tailwind
- Base de datos: Postgres + pgvector
- Tests: pytest (backend) + vitest (frontend)
- Linting: ruff + mypy

## Reglas de planificación
1. Descompone el objetivo en tareas ATÓMICAS — cada tarea debe poder completarse en <100 líneas de código
2. Cada tarea tiene exactamente UN agente responsable: "coder", "tester" o "reviewer"
3. Las dependencias deben formar un grafo acíclico (sin ciclos)
4. Prioriza las tareas de infraestructura (modelos, schemas) antes que la lógica de negocio
5. Nunca propongas tecnologías fuera del stack canónico sin justificación explícita
6. Identifica riesgos reales, no genéricos

## Formato de respuesta
Responde ÚNICAMENTE con un objeto JSON válido, sin texto adicional, sin markdown, sin explicaciones.
El JSON debe seguir exactamente esta estructura:

{
  "summary": "descripción breve de qué se va a implementar",
  "stack_notes": "notas específicas sobre decisiones de stack para esta tarea",
  "risks": ["riesgo 1", "riesgo 2"],
  "tasks": [
    {
      "id": "task_1",
      "title": "título corto de la tarea",
      "description": "descripción detallada de qué implementar y cómo",
      "agent": "coder",
      "priority": "high",
      "depends_on": [],
      "estimated_complexity": "low",
      "files_to_create": ["ruta/al/archivo.py"],
      "files_to_modify": []
    }
  ]
}

Valores válidos:
- agent: "coder" | "tester" | "reviewer"
- priority: "high" | "medium" | "low"
- estimated_complexity: "low" | "medium" | "high"
"""


def _extract_json(text: str) -> str:
    """
    Extrae el JSON de la respuesta del modelo.
    El modelo a veces incluye texto antes o después del JSON,
    o lo envuelve en ```json ... ```. Esta función lo limpia.
    """
    # Intentar extraer de bloque markdown
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()

    # Intentar encontrar el objeto JSON directamente
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return match.group(0).strip()

    return text.strip()


def _parse_plan(raw_json: str) -> Plan:
    """
    Parsea el JSON del modelo en un objeto Plan validado.
    Lanza ValueError si el JSON es inválido o no tiene la estructura esperada.
    """
    data = json.loads(raw_json)

    tasks = []
    for t in data.get("tasks", []):
        tasks.append(Task(
            id=t["id"],
            title=t["title"],
            description=t["description"],
            agent=t.get("agent", "coder"),
            priority=TaskPriority(t.get("priority", "medium")),
            depends_on=t.get("depends_on", []),
            estimated_complexity=t.get("estimated_complexity", "medium"),
            files_to_create=t.get("files_to_create", []),
            files_to_modify=t.get("files_to_modify", []),
        ))

    return Plan(
        summary=data["summary"],
        tasks=tasks,
        stack_notes=data.get("stack_notes", ""),
        risks=data.get("risks", []),
    )


async def run_architect(prompt: str) -> Plan:
    """
    Punto de entrada del agente Architect.

    Args:
        prompt: el objetivo del usuario en lenguaje natural

    Returns:
        Plan validado con tareas, prioridades y dependencias

    Raises:
        ValueError: si no puede producir un plan válido tras MAX_RETRIES intentos
    """
    log = logger.bind(agent="architect", prompt_len=len(prompt))
    log.info("architect.start")

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        log.info("architect.attempt", attempt=attempt)

        # Si es un reintento, añade el error anterior como contexto
        user_prompt = prompt
        if last_error and attempt > 1:
            user_prompt = (
                f"{prompt}\n\n"
                f"NOTA: Tu respuesta anterior falló con este error: {last_error}\n"
                f"Asegúrate de responder ÚNICAMENTE con JSON válido."
            )

        try:
            response = await ollama.generate(
                prompt=user_prompt,
                system=SYSTEM_PROMPT,
                model=settings.ollama_model,
            )

            raw_json = _extract_json(response)
            plan = _parse_plan(raw_json)

            log.info(
                "architect.done",
                tasks=len(plan.tasks),
                attempt=attempt,
            )
            return plan

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            last_error = e
            log.warning("architect.parse_error", attempt=attempt, error=str(e))
            continue

    raise ValueError(
        f"El Architect no pudo producir un plan válido tras {MAX_RETRIES} intentos. "
        f"Último error: {last_error}"
    )
