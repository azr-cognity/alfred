"""
Indexador del codebase para Alfred.

Qué hace:
  Recorre el repo, lee cada archivo relevante, genera embeddings con
  nomic-embed-text via Ollama, y los guarda en Postgres (tabla code_embeddings).

  El Coder usa estos embeddings para buscar archivos relevantes antes de
  generar código nuevo — así conoce tus convenciones sin que se las expliques.

Uso:
  python -m app.indexer                    # indexa todo el repo
  python -m app.indexer --path alfred-api  # indexa solo una carpeta
"""

import argparse
import asyncio
import hashlib
from pathlib import Path

import structlog
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.ollama import ollama

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
}

# Tamaño de chunk en caracteres (~512 tokens aprox)
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 200


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Divide un texto largo en chunks con overlap.
    El overlap asegura que el contexto no se corta abruptamente entre chunks.
    """
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
    """Decide si un archivo debe ser indexado."""
    if any(excluded in path.parts for excluded in EXCLUDED_DIRS):
        return False
    if path.suffix not in SUPPORTED_EXTENSIONS:
        return False
    if path.stat().st_size > 500_000:  # skip archivos > 500KB
        return False
    return True


async def index_file(
    file_path: Path,
    repo_root: Path,
    project_id: str,
) -> int:
    """
    Indexa un archivo: genera embeddings y los guarda en Postgres.
    Retorna el número de chunks indexados.
    """
    relative_path = str(file_path.relative_to(repo_root))
    content = file_path.read_text(encoding="utf-8", errors="ignore")

    if not content.strip():
        return 0

    # Hash del contenido para detectar cambios
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    async with AsyncSessionLocal() as session:
        # Verificar si el archivo ya fue indexado con el mismo contenido
        existing = await session.execute(
            text("SELECT hash FROM code_files WHERE project_id = :pid AND file_path = :path"),
            {"pid": project_id, "path": relative_path}
        )
        row = existing.fetchone()

        if row and row[0] == content_hash:
            logger.debug("indexer.skip", path=relative_path, reason="no changes")
            return 0

        # Determinar lenguaje
        language = {
            ".py": "python", ".ts": "typescript", ".tsx": "typescript",
            ".js": "javascript", ".jsx": "javascript", ".sql": "sql",
            ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
            ".md": "markdown", ".json": "json", ".rego": "rego",
        }.get(file_path.suffix, "text")

        # Upsert en code_files
        await session.execute(
            text("""
                INSERT INTO code_files (project_id, file_path, language, content, hash)
                VALUES (:pid, :path, :lang, :content, :hash)
                ON CONFLICT (project_id, file_path)
                DO UPDATE SET content = :content, hash = :hash, indexed_at = NOW()
                RETURNING id
            """),
            {
                "pid": project_id,
                "path": relative_path,
                "lang": language,
                "content": content,
                "hash": content_hash,
            }
        )

        # Obtener el ID del archivo
        file_row = await session.execute(
            text("SELECT id FROM code_files WHERE project_id = :pid AND file_path = :path"),
            {"pid": project_id, "path": relative_path}
        )
        file_id = str(file_row.fetchone()[0])

        # Borrar embeddings anteriores
        await session.execute(
            text("DELETE FROM code_embeddings WHERE file_id = :fid"),
            {"fid": file_id}
        )

        # Generar y guardar embeddings por chunk
        chunks = _chunk_text(content)
        for i, chunk in enumerate(chunks):
            embedding = await ollama.embed(chunk)
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

            await session.execute(
                text("""
                    INSERT INTO code_embeddings (file_id, chunk_index, chunk_text, embedding)
                    VALUES (:fid, :idx, :text, CAST(:emb AS vector))
                """),
                {
                    "fid": file_id,
                    "idx": i,
                    "text": chunk,
                    "emb": embedding_str,
                }
            )

        await session.commit()

    return len(chunks)


async def index_repo(repo_path: str = ".") -> None:
    """Indexa todo el repo desde repo_path."""
    root = Path(repo_path).resolve()
    log = logger.bind(root=str(root))
    log.info("indexer.start")

    # Obtener project_id de la DB
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT id FROM projects LIMIT 1")
        )
        row = result.fetchone()
        if not row:
            log.error("indexer.no_project", msg="Crea un proyecto en la DB primero")
            return
        project_id = str(row[0])

    # Recolectar archivos
    files = [f for f in root.rglob("*") if f.is_file() and _should_index(f)]
    log.info("indexer.files_found", count=len(files))

    indexed = 0
    skipped = 0
    errors = 0

    for i, file_path in enumerate(files, 1):
        try:
            chunks = await index_file(file_path, root, project_id)
            if chunks > 0:
                indexed += 1
                log.info("indexer.file_done", file=str(file_path.relative_to(root)), chunks=chunks, progress=f"{i}/{len(files)}")
            else:
                skipped += 1
        except Exception as e:
            errors += 1
            log.warning("indexer.file_error", file=str(file_path), error=str(e))

    log.info("indexer.done", indexed=indexed, skipped=skipped, errors=errors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Indexa el codebase para Alfred")
    parser.add_argument("--path", default=".", help="Ruta al repo a indexar")
    args = parser.parse_args()

    asyncio.run(index_repo(args.path))
