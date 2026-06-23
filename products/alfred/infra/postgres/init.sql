-- Este archivo corre automáticamente cuando Postgres arranca por primera vez.
-- Crea las extensiones y tablas base que Alfred necesita desde el día 1.

-- ── EXTENSIONES ──────────────────────────────────────────────────────────────

-- pgvector: permite guardar vectores de alta dimensión (embeddings)
-- y hacer búsquedas de similitud semántica ("qué archivos se parecen a esto")
CREATE EXTENSION IF NOT EXISTS vector;

-- uuid-ossp: genera IDs únicos automáticamente para cada fila
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── TABLAS BASE ───────────────────────────────────────────────────────────────

-- projects: cada proyecto que Alfred conoce (tu repo de Cognity Stratum, etc.)
CREATE TABLE IF NOT EXISTS projects (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    repo_path   TEXT,                    -- ruta local al repo
    acd_path    TEXT,                    -- ruta al archivo .acd.yaml
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- code_files: cada archivo del repo indexado por Alfred
CREATE TABLE IF NOT EXISTS code_files (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id  UUID REFERENCES projects(id) ON DELETE CASCADE,
    file_path   TEXT NOT NULL,           -- ruta relativa al repo
    language    VARCHAR(50),             -- python, typescript, yaml, etc.
    content     TEXT,                    -- contenido del archivo
    hash        VARCHAR(64),             -- sha256 para detectar cambios
    indexed_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, file_path)
);

-- code_embeddings: los vectores de cada archivo para búsqueda semántica
-- 768 dimensiones = tamaño del modelo nomic-embed-text
CREATE TABLE IF NOT EXISTS code_embeddings (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    file_id     UUID REFERENCES code_files(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,        -- un archivo puede tener varios chunks
    chunk_text  TEXT NOT NULL,           -- el texto que se embedió
    embedding   vector(768) NOT NULL,    -- el vector numérico
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Índice HNSW: permite búsqueda semántica rápida (~50ms en miles de archivos)
-- HNSW = Hierarchical Navigable Small World — el algoritmo de búsqueda vectorial
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw
    ON code_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- agent_runs: cada vez que Alfred ejecuta un pipeline completo
CREATE TABLE IF NOT EXISTS agent_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID REFERENCES projects(id),
    prompt          TEXT NOT NULL,       -- lo que le pediste a Alfred
    status          VARCHAR(50) DEFAULT 'pending',  -- pending|running|done|failed
    current_agent   VARCHAR(50),         -- qué agente está activo ahora
    plan            JSONB,               -- el plan que generó el Architect
    result          JSONB,               -- el resultado final
    tokens_used     INTEGER DEFAULT 0,
    duration_ms     INTEGER,
    error           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

-- agent_steps: cada paso dentro de un run (un agente = un paso)
CREATE TABLE IF NOT EXISTS agent_steps (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id      UUID REFERENCES agent_runs(id) ON DELETE CASCADE,
    agent_name  VARCHAR(50) NOT NULL,    -- architect|coder|reviewer|tester|auditor
    status      VARCHAR(50) DEFAULT 'pending',
    input       JSONB,                   -- qué recibió el agente
    output      JSONB,                   -- qué produjo el agente
    policy_result JSONB,                 -- qué dijo OPA sobre su output
    tokens_used INTEGER DEFAULT 0,
    duration_ms INTEGER,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- standards: los estándares de desarrollo versionados
-- (lo que definimos en el ACD — aquí vive la versión activa)
CREATE TABLE IF NOT EXISTS standards (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        VARCHAR(255) NOT NULL,
    category    VARCHAR(100) NOT NULL,   -- naming|security|testing|architecture
    version     VARCHAR(20) NOT NULL,
    rules       JSONB NOT NULL,          -- las reglas en JSON
    active      BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── DATOS INICIALES ───────────────────────────────────────────────────────────

-- Proyecto de ejemplo — lo actualizas con tu repo real
INSERT INTO projects (name, description, repo_path)
VALUES (
    'cognity-stratum',
    'Plataforma de automatización con agentes IA',
    '/home/tu-usuario/cognity-stratum'  -- cambia esto a tu ruta real
)
ON CONFLICT (name) DO NOTHING;

-- Estándares iniciales basados en el ACD que diseñamos
INSERT INTO standards (name, category, version, rules) VALUES
(
    'Python naming conventions',
    'naming',
    '1.0.0',
    '{"snake_case": true, "max_function_lines": 50, "max_file_lines": 300}'
),
(
    'Security baseline',
    'security',
    '1.0.0',
    '{"no_hardcoded_secrets": true, "sql_parameterized": true, "rate_limiting_public": true}'
),
(
    'Testing requirements',
    'testing',
    '1.0.0',
    '{"min_coverage": 75, "mock_external_apis": true, "no_production_db_in_tests": true}'
)
ON CONFLICT DO NOTHING;
