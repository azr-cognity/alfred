"""
Módulo: dependency_query
Propósito: Queries async sobre el grafo de dependencias en Postgres.
           Punto de acceso único para consultar quién importa qué.

Dependencias clave:
    - app.core.database: AsyncSessionLocal
    - sqlalchemy.text: queries raw (ADR-007: CAST en lugar de ::type)
    - code_dependencies + code_files: tablas del grafo (Migration 004)

Restricciones:
    - ADR-007: CAST(:x AS uuid) nunca ::uuid
    - Todas las funciones son async — usar con await
    - get_dependency_context_str() retorna string listo para inyectar en prompt

Consumido por:
    - app/orchestrator/nodes.py → build_run_context_node
    - app/agents/architect.py → contexto de dependencias en planificación
Versión: 1.0 | Junio 2026 | Owner: AZR
"""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal

logger = structlog.get_logger(__name__)


async def get_reverse_deps(
    file_paths: list[str],
    project_id: str,
    db: AsyncSession | None = None,
) -> dict[str, list[str]]:
    """Retornar quién importa cada archivo (dependencias inversas).

    Para cada archivo en file_paths, busca qué otros archivos del proyecto
    lo importan. Útil para el Architect: si va a modificar app/core/llm.py,
    debe saber que app/agents/architect.py y reviewer.py también se ven afectados.

    Args:
        file_paths: Rutas relativas de los archivos a analizar.
        project_id: UUID del proyecto.
        db: Sesión de DB opcional — si None, abre una nueva.

    Returns:
        Dict {file_path: [lista de archivos que lo importan]}.
        Los archivos sin dependencias inversas no aparecen en el dict.

    Examples:
        >>> await get_reverse_deps(["app/core/llm.py"], project_id)
        {"app/core/llm.py": ["app/agents/architect.py", "app/agents/reviewer.py"]}
    """
    if not file_paths:
        return {}

    async def _query(session: AsyncSession) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for path in file_paths:
            rows = await session.execute(
                text("""
                    SELECT cf_from.file_path
                    FROM code_dependencies cd
                    JOIN code_files cf_imported ON cd.imported_id  = cf_imported.id
                    JOIN code_files cf_from     ON cd.importer_id  = cf_from.id
                    WHERE cf_imported.file_path = :path
                      AND cd.project_id = CAST(:pid AS uuid)
                    ORDER BY cf_from.file_path
                """),
                {"path": path, "pid": project_id},
            )
            importers = [r[0] for r in rows.fetchall()]
            if importers:
                result[path] = importers
        return result

    if db is not None:
        return await _query(db)

    async with AsyncSessionLocal() as session:
        return await _query(session)


async def get_direct_deps(
    file_path: str,
    project_id: str,
    db: AsyncSession | None = None,
) -> list[str]:
    """Retornar qué archivos importa directamente un archivo.

    Args:
        file_path: Ruta relativa del archivo origen.
        project_id: UUID del proyecto.
        db: Sesión de DB opcional.

    Returns:
        Lista de rutas de archivos importados.
    """
    async def _query(session: AsyncSession) -> list[str]:
        rows = await session.execute(
            text("""
                SELECT cf_to.file_path
                FROM code_dependencies cd
                JOIN code_files cf_from ON cd.importer_id = cf_from.id
                JOIN code_files cf_to   ON cd.imported_id = cf_to.id
                WHERE cf_from.file_path = :path
                  AND cd.project_id = CAST(:pid AS uuid)
                ORDER BY cf_to.file_path
            """),
            {"path": file_path, "pid": project_id},
        )
        return [r[0] for r in rows.fetchall()]

    if db is not None:
        return await _query(db)

    async with AsyncSessionLocal() as session:
        return await _query(session)


async def upsert_dependencies(
    importer_path: str,
    imported_paths: list[str],
    project_id: str,
    db: AsyncSession,
) -> int:
    """Insertar o actualizar las dependencias de un archivo.

    Borra las dependencias anteriores del importer y las reinserta.
    Se llama desde el indexer después de parsear los imports.

    Args:
        importer_path: Ruta del archivo que importa.
        imported_paths: Lista de rutas importadas por importer.
        project_id: UUID del proyecto.
        db: Sesión de DB activa (el caller hace commit).

    Returns:
        Número de edges insertados.
    """
    if not imported_paths:
        # Borrar dependencias anteriores y salir
        await db.execute(
            text("""
                DELETE FROM code_dependencies
                WHERE importer_id = (
                    SELECT id FROM code_files
                    WHERE file_path = :path AND project_id = CAST(:pid AS uuid)
                )
            """),
            {"path": importer_path, "pid": project_id},
        )
        return 0

    # Obtener file_id del importer
    row = await db.execute(
        text("SELECT id FROM code_files WHERE file_path = :path AND project_id = CAST(:pid AS uuid)"),
        {"path": importer_path, "pid": project_id},
    )
    importer_row = row.fetchone()
    if not importer_row:
        return 0
    importer_id = str(importer_row[0])

    # Borrar edges anteriores del importer
    await db.execute(
        text("DELETE FROM code_dependencies WHERE importer_id = CAST(:iid AS uuid)"),
        {"iid": importer_id},
    )

    # Insertar edges nuevos
    inserted = 0
    for imported_path in imported_paths:
        row = await db.execute(
            text("SELECT id FROM code_files WHERE file_path = :path AND project_id = CAST(:pid AS uuid)"),
            {"path": imported_path, "pid": project_id},
        )
        imported_row = row.fetchone()
        if not imported_row:
            continue  # archivo externo o no indexado aún
        imported_id = str(imported_row[0])

        await db.execute(
            text("""
                INSERT INTO code_dependencies (project_id, importer_id, imported_id)
                VALUES (CAST(:pid AS uuid), CAST(:from AS uuid), CAST(:to AS uuid))
                ON CONFLICT (importer_id, imported_id) DO NOTHING
            """),
            {"pid": project_id, "from": importer_id, "to": imported_id},
        )
        inserted += 1

    return inserted


async def get_dependency_context_str(
    file_paths: list[str],
    project_id: str,
    db: AsyncSession | None = None,
) -> str:
    """Construir string de contexto de dependencias para inyectar en el Architect.

    Para los archivos en file_paths (los que el plan va a tocar), retorna
    un bloque markdown con quién los importa — para que el Architect sepa
    qué otros archivos podrían necesitar cambios.

    Args:
        file_paths: Archivos que el plan va a crear o modificar.
        project_id: UUID del proyecto.
        db: Sesión de DB opcional.

    Returns:
        String markdown listo para incluir en el prompt del Architect.
        String vacío si no hay dependencias relevantes.
    """
    reverse = await get_reverse_deps(file_paths, project_id, db)
    if not reverse:
        return ""

    lines = ["## Archivos que dependen de los que vas a modificar"]
    lines.append("Considera si estos archivos también necesitan cambios:\n")
    for path, importers in sorted(reverse.items()):
        lines.append(f"**{path}** es importado por:")
        for imp in importers:
            lines.append(f"  - {imp}")
        lines.append("")

    return "\n".join(lines)
