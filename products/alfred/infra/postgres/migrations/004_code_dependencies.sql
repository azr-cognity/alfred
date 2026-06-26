-- =============================================================
-- Migration 004: Grafo de dependencias de código (S13)
-- =============================================================
-- Activa LTREE para paths jerárquicos (app.utils.rut)
-- Agrega module_path a code_files
-- Crea tabla code_dependencies con edges importer → imported
-- =============================================================

-- Extensión LTREE (paths jerárquicos tipo app.utils.rut)
CREATE EXTENSION IF NOT EXISTS ltree;

-- Columna module_path en code_files
-- Ejemplo: app/utils/rut.py → app.utils.rut
--          app/utils/__init__.py → app.utils.__init__
ALTER TABLE code_files
    ADD COLUMN IF NOT EXISTS module_path ltree;

CREATE INDEX IF NOT EXISTS idx_code_files_module_path
    ON code_files USING gist (module_path);

-- Tabla de dependencias: un edge = "importer importa a imported"
CREATE TABLE IF NOT EXISTS code_dependencies (
    id          uuid        DEFAULT uuid_generate_v4() PRIMARY KEY,
    project_id  uuid        NOT NULL REFERENCES projects(id)    ON DELETE CASCADE,
    importer_id uuid        NOT NULL REFERENCES code_files(id)  ON DELETE CASCADE,
    imported_id uuid        NOT NULL REFERENCES code_files(id)  ON DELETE CASCADE,
    created_at  timestamptz DEFAULT now(),

    -- Un par (importer, imported) es único por proyecto
    UNIQUE (importer_id, imported_id)
);

-- Índices para las queries más frecuentes
-- "¿Quién importa este archivo?" → busca por imported_id
CREATE INDEX IF NOT EXISTS idx_deps_imported
    ON code_dependencies (imported_id);

-- "¿Qué importa este archivo?" → busca por importer_id
CREATE INDEX IF NOT EXISTS idx_deps_importer
    ON code_dependencies (importer_id);

-- Para limpiar dependencias de un proyecto entero
CREATE INDEX IF NOT EXISTS idx_deps_project
    ON code_dependencies (project_id);

-- Vista útil para debugging: edges con paths legibles
CREATE OR REPLACE VIEW v_code_dependencies AS
SELECT
    p.name                          AS project,
    cf_from.file_path               AS importer,
    cf_to.file_path                 AS imported,
    cd.created_at
FROM code_dependencies cd
JOIN code_files cf_from ON cd.importer_id = cf_from.id
JOIN code_files cf_to   ON cd.imported_id = cf_to.id
JOIN projects p         ON cd.project_id  = p.id
ORDER BY cf_from.file_path, cf_to.file_path;
