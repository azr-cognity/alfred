"""
Módulo: indexer
Propósito: Indexar el codebase para Alfred — embeddings + grafo de dependencias.

Qué hace:
  Recorre el repo, lee cada archivo relevante, genera embeddings con
  nomic-embed-text via Ollama, y los guarda en Postgres (tabla code_embeddings).
  Además parsea imports de archivos Python y construye el grafo de dependencias
  (tabla code_dependencies — Migration 004, S13).

  El Coder usa los embeddings para buscar archivos relevantes antes de generar
  código nuevo. El Architect usa el grafo para saber qué archivos se ven
  afectados cuando se modifica uno.

Cambios S13:
  - Integración de dependency_parser: extrae imports Python con ast
  - Popula code_dependencies durante indexación
  - Actualiza module_path (ltree) en code_files

Uso:
  python -m app.indexer                    # indexa todo el repo
  python -m app.indexer --path alfred-api  # indexa solo una carpeta

Restricciones:
  - ADR-007: CAST(:x AS uuid) nunca ::uuid en queries
  - Solo parsea dependencias de archivos .py (S14+ para TS)
  - module_path LTREE: app/utils/rut.py → app.utils.rut

Versión: 2.0 | Junio 2026 | Owner: AZR
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from pathlib import Path

import structlog
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.config import settings
from app.core.ollama import ollama
from app.graph.dependency_parser import extract_imports, file_path_to_module_path
from app.graph.dependency_query import upsert_dependencies

logger = structlog.get_logger()

# Extensiones que Alfred entiende
SUPPORTED_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".sql", ".yaml", ".yml", ".toml", ".md",
    ".json", ".env.example", ".rego",
}

# Carpetas que no tienen sentido indexar
EXCLUDED_DIRS = {
    ".venv", "venv", "node_modules", ".git",
    "__pycache__", ".mypy_cache", ".ruff_cache",
    ".next", "dist", "build", ".pytest_cache",
    "tests/generated",
}

CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Dividir texto largo en chunks con overlap."""
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def _should_index(path: Path) -> bool:
    """Decidir si un archivo debe ser indexado."""
    if any(excluded in path.parts for excluded in EXCLUDED_DIRS):
        return False
    if path.suffix not in SUPPORTED_EXTENSIONS:
        return False
    if path.stat().st_size > 500_000:
        return False
    return True


async def index_file(
    file_path: Path,
    repo_root: Path,
    project_id: str,
) -> int:
    """Indexar un archivo: embeddings + dependencias.

    Genera embeddings del contenido y popula code_dependencies para
    archivos Python (parsea imports con ast).

    Args:
        file_path: Ruta absoluta al archivo.
        repo_root: Raíz del repositorio.
        project_id: UUID del proyecto en Postgres.

    Returns:
        Número de chunks indexados (0 si no hubo cambios).
    """
    relative_path = str(file_path.relative_to(repo_root)).replace("\\", "/")
    content = file_path.read_text(encoding="utf-8", errors="ignore")

    if not content.strip():
        return 0

    content_hash = hashlib.sha256(content.encode()).hexdigest()

    async with AsyncSessionLocal() as session:
        # Skip si no hubo cambios
        existing = await session.execute(
            text("SELECT hash FROM code_files WHERE project_id = CAST(:pid AS uuid) AND file_path = :path"),
            {"pid": project_id, "path": relative_path},
        )
        row = existing.fetchone()
        if row and row[0] == content_hash:
            logger.debug("indexer.skip", path=relative_path, reason="no changes")
            return 0

        language = {
            ".py": "python", ".ts": "typescript", ".tsx": "typescript",
            ".js": "javascript", ".jsx": "javascript", ".sql": "sql",
            ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
            ".md": "markdown", ".json": "json", ".rego": "rego",
        }.get(file_path.suffix, "text")

        # Calcular module_path LTREE para archivos Python
        module_path = (
            file_path_to_module_path(relative_path)
            if file_path.suffix == ".py"
            else None
        )

        # Upsert en code_files (con module_path)
        await session.execute(
            text("""
                INSERT INTO code_files (project_id, file_path, language, content, hash, module_path)
                VALUES (
                    CAST(:pid AS uuid), :path, :lang, :content, :hash,
                    CAST(:module_path AS ltree)
                )
                ON CONFLICT (project_id, file_path)
                DO UPDATE SET
                    content     = :content,
                    hash        = :hash,
                    module_path = CAST(:module_path AS ltree),
                    indexed_at  = NOW()
            """),
            {
                "pid": project_id,
                "path": relative_path,
                "lang": language,
                "content": content,
                "hash": content_hash,
                "module_path": module_path,
            },
        )

        # Obtener file_id
        file_row = await session.execute(
            text("SELECT id FROM code_files WHERE project_id = CAST(:pid AS uuid) AND file_path = :path"),
            {"pid": project_id, "path": relative_path},
        )
        file_id = str(file_row.fetchone()[0])

        # Borrar embeddings anteriores y regenerar
        await session.execute(
            text("DELETE FROM code_embeddings WHERE file_id = CAST(:fid AS uuid)"),
            {"fid": file_id},
        )

        chunks = _chunk_text(content)
        for i, chunk in enumerate(chunks):
            embedding = await ollama.embed(settings.ollama_embed_model, chunk)
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
            await session.execute(
                text("""
                    INSERT INTO code_embeddings (file_id, chunk_index, chunk_text, embedding)
                    VALUES (CAST(:fid AS uuid), :idx, :text, CAST(:emb AS vector))
                """),
                {"fid": file_id, "idx": i, "text": chunk, "emb": embedding_str},
            )

        # Parsear y guardar dependencias (solo Python)
        dep_count = 0
        if file_path.suffix == ".py":
            imported_paths = extract_imports(file_path, repo_root)
            dep_count = await upsert_dependencies(
                importer_path=relative_path,
                imported_paths=imported_paths,
                project_id=project_id,
                db=session,
            )

        await session.commit()

    if dep_count > 0:
        logger.debug("indexer.deps_updated", path=relative_path, edges=dep_count)

    return len(chunks)


async def _delete_stale_files(
    repo_root: Path,
    project_id: str,
    indexed_paths: set[str],
) -> None:
    """Eliminar de la DB archivos que ya no existen en disco."""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text("SELECT file_path FROM code_files WHERE project_id = CAST(:pid AS uuid)"),
            {"pid": project_id},
        )
        db_paths = {r[0] for r in rows.fetchall()}
        stale = db_paths - indexed_paths
        for path in stale:
            await session.execute(
                text("DELETE FROM code_files WHERE project_id = CAST(:pid AS uuid) AND file_path = :path"),
                {"pid": project_id, "path": path},
            )
            logger.info("indexer.stale_deleted", path=path)
        if stale:
            await session.commit()


async def index_repo(repo_path: str = ".") -> None:
    """Indexar todo el repo desde repo_path."""
    root = Path(repo_path).resolve()
    log = logger.bind(root=str(root))
    log.info("indexer.start")

    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT id FROM projects LIMIT 1"))
        row = result.fetchone()
        if not row:
            log.error("indexer.no_project", msg="Crea un proyecto en la DB primero")
            return
        project_id = str(row[0])

    files = [f for f in root.rglob("*") if f.is_file() and _should_index(f)]
    log.info("indexer.files_found", count=len(files))

    indexed = 0
    skipped = 0
    errors = 0
    indexed_paths: set[str] = set()

    for i, file_path in enumerate(files, 1):
        relative = str(file_path.relative_to(root)).replace("\\", "/")
        indexed_paths.add(relative)
        try:
            chunks = await index_file(file_path, root, project_id)
            if chunks > 0:
                indexed += 1
                log.info(
                    "indexer.file_done",
                    file=relative,
                    chunks=chunks,
                    progress=f"{i}/{len(files)}",
                )
            else:
                skipped += 1
        except Exception as e:
            errors += 1
            log.warning("indexer.file_error", file=relative, error=str(e))

    await _delete_stale_files(root, project_id, indexed_paths)
    log.info("indexer.done", indexed=indexed, skipped=skipped, errors=errors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Indexa el codebase para Alfred")
    parser.add_argument("--path", default=".", help="Ruta al repo a indexar")
    args = parser.parse_args()
    asyncio.run(index_repo(args.path))


