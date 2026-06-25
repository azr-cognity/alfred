"""
Módulo: indexer_core
Propósito: Constantes y utilidades puras para el indexador de Alfred.
           Sin dependencias de DB ni Ollama — importable sin side effects.
           Separado de indexer.py para mantener ambos módulos bajo 300 líneas (OPA).

Consumido por: app.indexer
Versión: 1.1 | Junio 2026 | Owner: AZR
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS: set[str] = {
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".sql", ".yaml", ".yml", ".toml", ".md",
    ".json", ".env.example", ".rego",
}

EXCLUDED_DIRS: set[str] = {
    ".venv", "venv", "node_modules", ".git",
    "__pycache__", ".mypy_cache", ".ruff_cache",
    ".next", "dist", "build", ".pytest_cache",
    "tests/generated",
}

CHUNK_SIZE: int = 2000
CHUNK_OVERLAP: int = 200

_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".json": "json",
    ".rego": "rego",
}

# ---------------------------------------------------------------------------
# Helpers puros (sin I/O, sin async)
# ---------------------------------------------------------------------------


def chunk_text(
    text: str,
    size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """Divide un texto largo en chunks con overlap.

    Args:
        text: Texto a dividir.
        size: Tamaño máximo de cada chunk en caracteres.
        overlap: Caracteres solapados entre chunks consecutivos.

    Returns:
        Lista de strings. Un único elemento si el texto cabe en un chunk.
    """
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap

    return chunks


def should_index(path: Path) -> bool:
    """Decide si un archivo debe ser indexado.

    Args:
        path: Ruta al archivo a evaluar.

    Returns:
        True si el archivo tiene extensión soportada, no está en un
        directorio excluido, y no supera 500 KB.
    """
    if any(excluded in path.parts for excluded in EXCLUDED_DIRS):
        return False
    if path.suffix not in SUPPORTED_EXTENSIONS:
        return False
    try:
        if path.stat().st_size > 500_000:
            return False
    except OSError:
        return False
    return True


def get_language(suffix: str) -> str:
    """Retorna el nombre del lenguaje para una extensión de archivo.

    Args:
        suffix: Extensión incluyendo el punto (e.g. '.py').

    Returns:
        Nombre del lenguaje en minúsculas, o 'text' si no está mapeado.
    """
    return _LANGUAGE_MAP.get(suffix, "text")