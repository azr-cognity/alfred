"""
Herramientas del agente Coder.

El Coder usa estas funciones para interactuar con el filesystem y el codebase.
Cada herramienta es una función async que el agente puede llamar durante su ejecución.

Herramientas disponibles:
  - search_codebase: busca archivos relevantes por similitud semántica
  - read_file: lee el contenido de un archivo
  - write_file: escribe código en un archivo (crea directorios si no existen)
  - list_files: lista archivos en una carpeta
"""

from pathlib import Path

import structlog
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.ollama import ollama

logger = structlog.get_logger()

# Raíz del proyecto — el Coder escribe archivos aquí
PROJECT_ROOT = Path("C:/Cognity/products/alfred/alfred-api")


async def search_codebase(query: str, limit: int = 5) -> list[dict]:
    """
    Busca archivos relevantes en el codebase usando similitud semántica.

    El Coder usa esto antes de generar código nuevo para entender:
    - Cómo están estructurados los archivos existentes
    - Qué convenciones de naming usa el proyecto
    - Si ya existe algo parecido a lo que va a crear

    Args:
        query: descripción de lo que busca (ej: "endpoint FastAPI con SQLModel")
        limit: cuántos archivos retornar

    Returns:
        Lista de dicts con {file_path, chunk_text, similarity}
    """
    # Generar embedding de la query
    query_embedding = await ollama.embed(query)
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT
                    cf.file_path,
                    ce.chunk_text,
                    1 - (ce.embedding <=> CAST(:emb AS vector)) AS similarity
                FROM code_embeddings ce
                JOIN code_files cf ON ce.file_id = cf.id
                ORDER BY ce.embedding <=> CAST(:emb AS vector)
                LIMIT :limit
            """),
            {"emb": embedding_str, "limit": limit}
        )
        rows = result.fetchall()

    results = [
        {
            "file_path": row[0],
            "chunk_text": row[1],
            "similarity": round(float(row[2]), 3),
        }
        for row in rows
    ]

    logger.info("search_codebase.done", query=query[:50], results=len(results))
    return results


async def read_file(file_path: str) -> str:
    """
    Lee el contenido de un archivo del proyecto.

    Args:
        file_path: ruta relativa al PROJECT_ROOT

    Returns:
        Contenido del archivo como string
    """
    full_path = PROJECT_ROOT / file_path

    if not full_path.exists():
        return f"ERROR: archivo no encontrado: {file_path}"

    if not full_path.is_file():
        return f"ERROR: {file_path} no es un archivo"

    content = full_path.read_text(encoding="utf-8", errors="ignore")
    logger.debug("read_file.done", path=file_path, size=len(content))
    return content


async def write_file(file_path: str, content: str) -> str:
    """
    Escribe código en un archivo del proyecto.
    Crea los directorios intermedios si no existen.

    Args:
        file_path: ruta relativa al PROJECT_ROOT
        content: contenido a escribir

    Returns:
        Mensaje de confirmación o error
    """
    full_path = PROJECT_ROOT / file_path

    # Crear directorios si no existen
    full_path.parent.mkdir(parents=True, exist_ok=True)

    full_path.write_text(content, encoding="utf-8")
    logger.info("write_file.done", path=file_path, size=len(content))
    return f"OK: archivo escrito en {file_path} ({len(content)} caracteres)"


async def list_files(folder_path: str = ".") -> list[str]:
    """
    Lista los archivos en una carpeta del proyecto.

    Args:
        folder_path: ruta relativa al PROJECT_ROOT

    Returns:
        Lista de rutas relativas de los archivos
    """
    full_path = PROJECT_ROOT / folder_path

    if not full_path.exists():
        return [f"ERROR: carpeta no encontrada: {folder_path}"]

    files = [
        str(f.relative_to(PROJECT_ROOT))
        for f in full_path.rglob("*")
        if f.is_file()
        and not any(exc in f.parts for exc in {".venv", "__pycache__", ".git"})
    ]

    return sorted(files)
