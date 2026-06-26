"""
Módulo: dependency_parser
Propósito: Extraer dependencias internas de archivos Python usando ast.
           Función pura — no toca la DB ni el filesystem más allá de resolver
           si un módulo existe en el repo.

Dependencias clave:
    - ast (stdlib) — parsear árbol sintáctico de Python
    - pathlib.Path — resolver rutas relativas

Restricciones:
    - Solo extrae imports de archivos .py — otros lenguajes pendiente S14+
    - Solo reporta dependencias INTERNAS al repo (ignora stdlib y third-party)
    - Función pura: extract_imports() no tiene side effects

Consumido por: app/indexer.py → index_file()
Versión: 1.0 | Junio 2026 | Owner: AZR
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# Labels LTREE no pueden tener guiones — reemplazar con underscore
_LTREE_INVALID = re.compile(r"[^A-Za-z0-9_.]")


def file_path_to_module_path(file_path: str) -> str:
    """Convertir ruta relativa de archivo a path LTREE.

    Ejemplos:
        app/utils/rut.py       → app.utils.rut
        app/utils/__init__.py  → app.utils.__init__
        tests/unit/test_llm.py → tests.unit.test_llm

    Args:
        file_path: Ruta relativa al repo (separador puede ser / o \\).

    Returns:
        String compatible con ltree (solo [A-Za-z0-9_.]).
    """
    path = Path(file_path)
    # Quitar extensión .py
    parts = list(path.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]  # quitar .py
    # Unir con punto y sanear caracteres inválidos
    raw = ".".join(parts)
    return _LTREE_INVALID.sub("_", raw)


def _module_name_to_path(module: str, repo_root: Path) -> str | None:
    """Convertir nombre de módulo Python a ruta de archivo relativa al repo.

    Solo retorna un valor si el módulo existe físicamente en el repo —
    así excluimos stdlib y dependencias third-party.

    Args:
        module: Nombre del módulo (ej: "app.utils.rut", "app.core.llm").
        repo_root: Raíz del repositorio donde buscar.

    Returns:
        Ruta relativa (ej: "app/utils/rut.py") o None si es externo.
    """
    parts = module.split(".")
    base = Path(*parts) if len(parts) > 1 else Path(parts[0])

    # Opción 1: módulo directo → app/utils/rut.py
    candidate = repo_root / base.with_suffix(".py")
    if candidate.exists():
        return str(candidate.relative_to(repo_root)).replace("\\", "/")

    # Opción 2: paquete con __init__ → app/utils/__init__.py
    candidate_init = repo_root / base / "__init__.py"
    if candidate_init.exists():
        return str(candidate_init.relative_to(repo_root)).replace("\\", "/")

    return None


def extract_imports(file_path: Path, repo_root: Path) -> list[str]:
    """Extraer rutas de archivos internos importados por un archivo Python.

    Parsea el AST del archivo y resuelve qué imports apuntan a módulos
    internos del repo (ignorando stdlib y third-party).

    Args:
        file_path: Ruta absoluta al archivo .py a analizar.
        repo_root: Raíz del repositorio para resolver módulos internos.

    Returns:
        Lista de rutas relativas importadas (ej: ["app/core/llm.py"]).
        Lista vacía si el archivo no es Python, tiene SyntaxError, o
        no importa nada interno.

    Examples:
        >>> # archivo: app/agents/architect.py
        >>> # contiene: from app.core.llm import get_provider
        >>> extract_imports(Path("app/agents/architect.py"), repo_root)
        ["app/core/llm.py"]
    """
    if file_path.suffix != ".py":
        return []

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(content)
    except (SyntaxError, OSError):
        return []

    found: set[str] = set()

    for node in ast.walk(tree):
        # import app.core.llm
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _module_name_to_path(alias.name, repo_root)
                if resolved:
                    found.add(resolved)

        # from app.core.llm import get_provider
        # from app.core import llm
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                resolved = _module_name_to_path(node.module, repo_root)
                if resolved:
                    found.add(resolved)
                # Caso: from app.core import llm → también probar app.core.llm
                if node.names:
                    for alias in node.names:
                        full = f"{node.module}.{alias.name}"
                        resolved_full = _module_name_to_path(full, repo_root)
                        if resolved_full:
                            found.add(resolved_full)

    return sorted(found)
