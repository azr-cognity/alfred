"""
Módulo: indexer
Propósito: Indexador incremental del codebase para Alfred.
           Recorre el repo, genera embeddings con nomic-embed-text via Ollama,
           y los persiste en Postgres (tablas code_files + code_embeddings).

           Optimización S12: bulk hash prefetch al inicio → 1 sola query para
           detectar archivos sin cambios, en lugar de 1 query por archivo.
           También detecta y limpia archivos borrados del filesystem.

Uso:
  python -m app.indexer                              # indexa todo con proyecto 'alfred'
  python -m app.indexer --path alfred-api            # indexa solo una carpeta
  python -m app.indexer --project-id mi-proyecto    # proyecto alternativo

Dependencias:
  - app.core.database: AsyncSessionLocal
  - app.core.ollama: ollama (singleton OllamaClient)
  - app.indexer_core: chunk_text, should_index, get_language

Restricciones:
  - asyncpg: CAST(:x AS type) nunca ::type (ADR-007)
  - Sin print() — solo structlog
  - No modifica code_embeddings sin actualizar code_files primero

Consumido por: CLI directo (python -m app.indexer) y scripts de CI
Versión: 1.1 | Junio 2026 | Owner: AZR
"""

import argparse
import asyncio
import hashlib
from pathlib import Path

import structlog
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.ollama import ollama
from app.indexer_core import chunk_text, get_language, should_index

logger = structlog.get_logger()


async def _resolve_project_uuid(project_name: str) -> str | None:
    """Resuelve nombre de proyecto a UUID consultando la DB.

    Args:
        project_name: Nombre del proyecto (e.g. 'alfred').

    Returns:
        UUID como string, o None si el proyecto no existe.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT id FROM projects WHERE name = :name"),
            {"name": project_name},
        )
        row = result.fetchone()
        if not row:
            logger.error("indexer.project_not_found", project_name=project_name)
            return None
        return str(row[0])


async def _fetch_known_hashes(project_id: str) -> dict[str, str]:
    """Obtiene todos los hashes indexados del proyecto en una sola query.

    Args:
        project_id: UUID del proyecto.

    Returns:
        Dict {file_path_relativo: sha256_hash}.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT file_path, hash FROM code_files WHERE project_id = :pid"),
            {"pid": project_id},
        )
        return {row[0]: row[1] for row in result.fetchall()}


async def _delete_stale_files(project_id: str, stale_paths: list[str]) -> None:
    """Elimina embeddings y registros de archivos borrados del filesystem.

    Args:
        project_id: UUID del proyecto.
        stale_paths: Rutas relativas presentes en DB pero no en el filesystem.
    """
    if not stale_paths:
        return

    async with AsyncSessionLocal() as session:
        for rel_path in stale_paths:
            file_row = await session.execute(
                text("SELECT id FROM code_files WHERE project_id = :pid AND file_path = :path"),
                {"pid": project_id, "path": rel_path},
            )
            row = file_row.fetchone()
            if row:
                await session.execute(
                    text("DELETE FROM code_embeddings WHERE file_id = :fid"),
                    {"fid": str(row[0])},
                )
            await session.execute(
                text("DELETE FROM code_files WHERE project_id = :pid AND file_path = :path"),
                {"pid": project_id, "path": rel_path},
            )
        await session.commit()

    logger.info("indexer.deleted", count=len(stale_paths), paths=stale_paths[:5])


async def index_file(
    file_path: Path,
    repo_root: Path,
    project_id: str,
    known_hash: str | None = None,
) -> int:
    """Indexa un archivo: genera embeddings y los guarda en Postgres.

    Args:
        file_path: Ruta absoluta al archivo.
        repo_root: Raíz del repo para calcular la ruta relativa.
        project_id: UUID del proyecto en Postgres.
        known_hash: Hash pre-calculado en index_repo(). Si coincide con el
                    contenido actual, se hace skip sin abrir sesión de DB.

    Returns:
        Número de chunks indexados. 0 si se hizo skip por hash idéntico.
    """
    relative_path = str(file_path.relative_to(repo_root))
    content = file_path.read_text(encoding="utf-8", errors="ignore")

    if not content.strip():
        return 0

    content_hash = hashlib.sha256(content.encode()).hexdigest()

    # Early skip — no abre DB si el hash prefetched ya coincide
    if known_hash is not None and content_hash == known_hash:
        logger.debug("indexer.skip", path=relative_path, reason="no changes")
        return 0

    language = get_language(file_path.suffix)

    async with AsyncSessionLocal() as session:
        # Upsert en code_files
        await session.execute(
            text("""
                INSERT INTO code_files (project_id, file_path, language, content, hash)
                VALUES (:pid, :path, :lang, :content, :hash)
                ON CONFLICT (project_id, file_path)
                DO UPDATE SET content = :content, hash = :hash, indexed_at = NOW()
            """),
            {
                "pid": project_id,
                "path": relative_path,
                "lang": language,
                "content": content,
                "hash": content_hash,
            },
        )

        # Obtener file_id (necesario como FK en code_embeddings)
        file_row = await session.execute(
            text("SELECT id FROM code_files WHERE project_id = :pid AND file_path = :path"),
            {"pid": project_id, "path": relative_path},
        )
        file_id = str(file_row.fetchone()[0])

        # Borrar embeddings anteriores del archivo
        await session.execute(
            text("DELETE FROM code_embeddings WHERE file_id = :fid"),
            {"fid": file_id},
        )

        # Generar y persistir embeddings por chunk
        chunks = chunk_text(content)
        for i, chunk in enumerate(chunks):
            embedding: list[float] = await ollama.embed(chunk)
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

            await session.execute(
                text("""
                    INSERT INTO code_embeddings (file_id, chunk_index, chunk_text, embedding)
                    VALUES (:fid, :idx, :text, CAST(:emb AS vector))
                """),
                {"fid": file_id, "idx": i, "text": chunk, "emb": embedding_str},
            )

        await session.commit()

    return len(chunks)


async def index_repo(repo_path: str, project_name: str) -> None:
    """Indexa el repo completo con bulk hash prefetch y detección de borrados.

    Args:
        repo_path: Ruta al directorio raíz del repo a indexar.
        project_name: Nombre del proyecto (se resuelve a UUID).
    """
    root = Path(repo_path).resolve()
    log = logger.bind(root=str(root), project=project_name)
    log.info("indexer.start")

    project_id = await _resolve_project_uuid(project_name)
    if not project_id:
        return

    # 1 sola query para todos los hashes conocidos
    known_hashes = await _fetch_known_hashes(project_id)
    log.info("indexer.known_files", count=len(known_hashes))

    files = [f for f in root.rglob("*") if f.is_file() and should_index(f)]
    log.info("indexer.files_found", count=len(files))

    # Detectar archivos borrados del filesystem
    fs_paths = {str(f.relative_to(root)) for f in files}
    stale = [p for p in known_hashes if p not in fs_paths]
    if stale:
        await _delete_stale_files(project_id, stale)

    indexed = skipped = errors = 0

    for i, file_path in enumerate(files, 1):
        rel = str(file_path.relative_to(root))
        try:
            chunks = await index_file(
                file_path, root, project_id, known_hash=known_hashes.get(rel)
            )
            if chunks > 0:
                indexed += 1
                log.info("indexer.file_done", file=rel, chunks=chunks, progress=f"{i}/{len(files)}")
            else:
                skipped += 1
        except Exception as e:
            errors += 1
            log.warning("indexer.file_error", file=rel, error=str(e))

    log.info("indexer.done", indexed=indexed, skipped=skipped, errors=errors)


def main() -> None:
    """Punto de entrada CLI del indexer."""
    parser = argparse.ArgumentParser(description="Indexa el codebase para Alfred")
    parser.add_argument("--path", default=".", help="Ruta al repo a indexar")
    parser.add_argument(
        "--project-id",
        default="alfred",
        help="Nombre del proyecto en la DB (default: alfred)",
    )
    args = parser.parse_args()
    asyncio.run(index_repo(args.path, args.project_id))


if __name__ == "__main__":
    main()