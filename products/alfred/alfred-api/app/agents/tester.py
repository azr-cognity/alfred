"""
Agente Tester — el verificador de Alfred (S7).

Responsabilidad:
  Genera y ejecuta tests pytest para el código producido por el Coder.
  Corre los tests en subprocess (mismo .venv) y retorna el resultado.

Flujo:
  1. Recibe la task y los archivos escritos por el Coder
  2. Llama al modelo para generar tests pytest
  3. Escribe los tests en tests/generated/<task_id>_test.py
  4. Ejecuta pytest en subprocess con timeout
  5. Retorna TesterResult con passed, output, y feedback para el Coder si falla
"""

import asyncio
import json
import re
import sys
from pathlib import Path

import structlog

from app.core.config import settings
from app.core.ollama import ollama
from app.schemas.runs import Task

from .coder_tools import PROJECT_ROOT, read_file

logger = structlog.get_logger()

MAX_RETRIES = 3
PYTEST_TIMEOUT = 60  # segundos máximo por ejecución

GENERATED_TESTS_DIR = PROJECT_ROOT / "tests" / "generated"

SYSTEM_PROMPT = """Eres el Tester de Alfred, un experto en testing de software.

Tu responsabilidad es generar tests pytest funcionales para el código producido
por el Coder.

## Reglas de testing
1. Usa pytest como framework — nunca unittest directamente
2. Cada función pública del módulo debe tener al menos un test
3. Incluye casos felices y casos de error (excepciones esperadas)
4. Mockea dependencias externas (DB, HTTP, filesystem) con pytest-mock o unittest.mock
5. Los tests deben ser independientes entre sí (sin estado compartido)
6. Usa fixtures de pytest para setup/teardown cuando aplique
7. Nombra los tests descriptivamente: test_<funcion>_<escenario>

## Stack
- Backend: FastAPI (Python 3.11) + SQLModel + Pydantic v2
- Test runner: pytest + pytest-asyncio para código async
- Mocking: unittest.mock (ya disponible en stdlib)

## Formato de respuesta
Responde ÚNICAMENTE con un objeto JSON válido:

{
  "test_file": "contenido completo del archivo de tests como string",
  "summary": "qué se testea y por qué estos casos"
}

Sin texto adicional, sin markdown, sin explicaciones.
"""


class TesterResult:
    """Resultado de la ejecución del Tester."""

    def __init__(
        self,
        passed: bool,
        feedback: str,
        task_id: str,
        test_file: str = "",
        pytest_output: str = "",
    ) -> None:
        self.passed = passed
        self.feedback = feedback
        self.task_id = task_id
        self.test_file = test_file
        self.pytest_output = pytest_output


def _extract_json(text: str) -> str:
    """Extrae JSON de la respuesta del modelo, filtrando bloques <think>."""
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return match.group(0).strip()
    return text.strip()


async def _run_pytest(test_path: Path) -> tuple[bool, str]:
    """Ejecuta pytest en subprocess y retorna (passed, output).

    Args:
        test_path: ruta absoluta al archivo de test generado

    Returns:
        (True, output) si todos los tests pasan
        (False, output) si algún test falla o hay error
    """
    cmd = [
        sys.executable, "-m", "pytest",
        str(test_path),
        "-v",
        "--tb=short",
        "--no-header",
        "-q",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
        )

        stdout, _ = await asyncio.wait_for(
            proc.communicate(),
            timeout=PYTEST_TIMEOUT,
        )

        output = stdout.decode("utf-8", errors="replace")
        passed = proc.returncode == 0
        return passed, output

    except asyncio.TimeoutError:
        return False, f"ERROR: pytest excedió el timeout de {PYTEST_TIMEOUT}s"
    except Exception as e:
        return False, f"ERROR al ejecutar pytest: {e}"


async def run_tester(
    task: Task,
    files_written: list[str],
    tester_feedback: str | None = None,
) -> TesterResult:
    """Punto de entrada del agente Tester.

    Args:
        task: la tarea del plan del Architect
        files_written: rutas de archivos escritos por el Coder
        tester_feedback: output de pytest del intento anterior (si es reintento)

    Returns:
        TesterResult con passed, feedback, pytest_output
    """
    log = logger.bind(agent="tester", task_id=task.id, task=task.title)
    is_retry = tester_feedback is not None
    log.info("tester.start", files=len(files_written), is_retry=is_retry)

    # ── Leer archivos a testear ────────────────────────────────────────────────
    file_contents: list[str] = []
    for path in files_written:
        content = await read_file(path)
        if not content.startswith("ERROR"):
            file_contents.append(f"## Archivo: {path}\n```python\n{content}\n```")

    if not file_contents:
        return TesterResult(
            passed=False,
            feedback="No hay archivos para testear.",
            task_id=task.id,
        )

    files_section = "\n\n".join(file_contents)

    user_prompt = f"""## Tarea implementada
ID: {task.id}
Título: {task.title}
Descripción: {task.description}

## Código a testear
{files_section}

Genera tests pytest completos y funcionales para este código.
"""

    if tester_feedback:
        user_prompt += f"""
## CORRECCIÓN REQUERIDA
El intento anterior falló con estos errores de pytest:

{tester_feedback}

Corrige los tests para que pasen. Revisa imports, mocks y assertions.
"""

    # ── Generar tests con el modelo ────────────────────────────────────────────
    last_error: Exception | None = None
    test_content = ""

    for attempt in range(1, MAX_RETRIES + 1):
        log.info("tester.generate_attempt", attempt=attempt)

        prompt = user_prompt
        if last_error and attempt > 1:
            prompt += f"\n\nNOTA: respuesta anterior inválida: {last_error}. Responde SOLO con JSON."

        try:
            response = await ollama.generate(
                prompt=prompt,
                system=SYSTEM_PROMPT,
                model=settings.ollama_model,
            )

            raw_json = _extract_json(response)
            data = json.loads(raw_json)

            test_content = data.get("test_file", "")
            if not test_content:
                raise ValueError("test_file vacío en la respuesta")

            break

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            last_error = e
            log.warning("tester.parse_error", attempt=attempt, error=str(e))
            continue

    if not test_content:
        return TesterResult(
            passed=False,
            feedback=f"No se pudo generar tests tras {MAX_RETRIES} intentos. Último error: {last_error}",
            task_id=task.id,
        )

    # ── Escribir archivo de test ───────────────────────────────────────────────
    GENERATED_TESTS_DIR.mkdir(parents=True, exist_ok=True)
    test_filename = f"{task.id}_test.py"
    test_path = GENERATED_TESTS_DIR / test_filename

    test_path.write_text(test_content, encoding="utf-8")
    log.info("tester.test_written", path=str(test_path))

    # ── Ejecutar pytest ────────────────────────────────────────────────────────
    passed, pytest_output = await _run_pytest(test_path)
    log.info("tester.pytest_done", passed=passed)

    if passed:
        return TesterResult(
            passed=True,
            feedback=f"Tests pasando. {pytest_output.strip().splitlines()[-1] if pytest_output.strip() else 'OK'}",
            task_id=task.id,
            test_file=str(test_path.relative_to(PROJECT_ROOT)),
            pytest_output=pytest_output,
        )

    # Construir feedback útil para el Coder
    feedback = (
        f"Los tests fallaron. Output de pytest:\n\n"
        f"{pytest_output[-3000:]}"  # últimos 3000 chars para no saturar el contexto
    )

    return TesterResult(
        passed=False,
        feedback=feedback,
        task_id=task.id,
        test_file=str(test_path.relative_to(PROJECT_ROOT)),
        pytest_output=pytest_output,
    )
