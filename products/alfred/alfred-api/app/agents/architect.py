"""
Agente Architect — el planificador de Alfred (S12).

Cambios respecto a S11:
  - Lee ALFRED_MISSION.md antes de planificar (Paso 1).
  - format=None en ollama.generate (format=json causaba response_len=0).
  - SYSTEM_PROMPT simplificado — las reglas de planning van en ALFRED_MISSION.md.

Cambios v2.4 (ADR-008, ADR-011):
  - Migrado a get_provider("architect") — usa Claude API en modo hybrid.
  - _extract_json ya no necesita strip_think: OllamaProvider lo centraliza.
  - temperature=0.3 pasada explícitamente a provider.generate().
"""

import json
import re
from pathlib import Path

import structlog

from app.core.llm import get_provider
from app.schemas.runs import Plan, Task, TaskPriority

logger = structlog.get_logger()

MAX_RETRIES = 3
MISSION_PATH = "ALFRED_MISSION.md"

SYSTEM_PROMPT = """Eres el Architect de Alfred, un asistente de desarrollo con agentes IA.

Tu unica responsabilidad es analizar un objetivo de desarrollo y producir un plan
estructurado de implementacion con tasks atomicas.

Lee el archivo ALFRED_MISSION.md que se incluye en el prompt — contiene las reglas
obligatorias de planning que DEBES seguir sin excepcion.

Las reglas mas criticas (no las olvides):
- agent="coder" para CUALQUIER task que cree o modifique archivos, incluyendo tests.
- agent="tester" SOLO para verificar si codigo existente pasa pytest.
- files_to_create o files_to_modify SIEMPRE con rutas explicitas.
- Maximo 3 tasks por plan.

Responde UNICAMENTE con JSON valido, sin texto adicional, sin markdown.

{
  "summary": "descripcion breve de que se va a implementar",
  "stack_notes": "notas especificas sobre decisiones de stack para esta tarea",
  "risks": ["riesgo 1", "riesgo 2"],
  "tasks": [
    {
      "id": "task_1",
      "title": "Verbo en infinitivo + que se hace",
      "description": "descripcion tecnica con rutas de archivos, contratos esperados y patron de referencia",
      "agent": "coder",
      "priority": "high",
      "depends_on": [],
      "estimated_complexity": "low",
      "files_to_create": ["ruta/relativa/al/archivo.py"],
      "files_to_modify": []
    }
  ]
}

Valores validos:
- agent: "coder" | "tester" | "reviewer"
- priority: "high" | "medium" | "low"
- estimated_complexity: "low" | "medium" | "high"
/no_think
"""


def _extract_json(text: str) -> str:
    """Extrae JSON de la respuesta del modelo.

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


def _parse_plan(raw_json: str) -> Plan:
    """Parsea el JSON del modelo en un objeto Plan validado.

    Raises:
        ValueError: Si el JSON es invalido o no tiene la estructura esperada.
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


async def _read_mission() -> str:
    """Lee ALFRED_MISSION.md desde el PROJECT_ROOT del Coder.

    Si no existe, retorna string vacío — el Architect opera sin contexto de misión.
    """
    try:
        from app.agents.coder_tools import PROJECT_ROOT
        mission_path = PROJECT_ROOT / MISSION_PATH
        if mission_path.exists():
            content = mission_path.read_text(encoding="utf-8", errors="ignore")
            logger.info("architect.mission_loaded", size=len(content))
            return content
        else:
            logger.warning("architect.mission_not_found", path=str(mission_path))
            return ""
    except Exception as e:
        logger.warning("architect.mission_read_error", error=str(e))
        return ""


async def run_architect(prompt: str) -> Plan:
    """Punto de entrada del agente Architect.

    Lee ALFRED_MISSION.md antes de planificar. En modo hybrid usa Claude API
    (ADR-008); en full_local usa Ollama — transparente para este módulo (ADR-011).

    Args:
        prompt: el objetivo del usuario en lenguaje natural.

    Returns:
        Plan validado con tasks, prioridades y dependencias.

    Raises:
        ValueError: Si no puede producir un plan válido tras MAX_RETRIES intentos.
    """
    log = logger.bind(agent="architect", prompt_len=len(prompt))
    log.info("architect.start")

    # Paso 1: leer ALFRED_MISSION.md
    mission = await _read_mission()
    mission_section = ""
    if mission:
        mission_section = f"\n\n## ALFRED_MISSION.md — reglas obligatorias de planning\n{mission}"

    # ADR-011: get_provider() decide Ollama vs Claude API según settings.llm_mode
    # complexity="medium" → sonnet en hybrid (opus se activa con complexity="high")
    provider = get_provider("architect", task_complexity="medium")

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        log.info("architect.attempt", attempt=attempt)

        user_prompt = f"{prompt}{mission_section}"

        if last_error and attempt > 1:
            user_prompt += (
                f"\n\nNOTA: intento anterior falló con: {last_error}. "
                "Responde ÚNICAMENTE con JSON válido. "
                "Recuerda: agent='coder' para cualquier task que cree archivos."
            )

        try:
            # ADR-008: frontier en modo hybrid; ADR-011: interfaz única
            llm_response = await provider.generate(
                system=SYSTEM_PROMPT,
                user=user_prompt,
                temperature=0.3,
            )

            raw_json = _extract_json(llm_response.content)
            plan = _parse_plan(raw_json)

            # Advertir si hay tasks de coder sin archivos declarados
            for task in plan.tasks:
                if task.agent == "coder" and not task.files_to_create and not task.files_to_modify:
                    log.warning(
                        "architect.task_sin_archivos",
                        task_id=task.id,
                        title=task.title,
                    )

            log.info("architect.done", tasks=len(plan.tasks), attempt=attempt)
            return plan

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            last_error = e
            log.warning("architect.parse_error", attempt=attempt, error=str(e))
            continue

    raise ValueError(
        f"El Architect no pudo producir un plan válido tras {MAX_RETRIES} intentos. "
        f"Último error: {last_error}"
    )