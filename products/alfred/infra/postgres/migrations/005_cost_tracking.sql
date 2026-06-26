-- =============================================================
-- Migration 005: Cost tracking por run y por step (S14)
-- =============================================================
-- Agrega cost_usd a agent_runs (total del run)
-- Agrega cost_usd + token counts a agent_steps (por agente)
-- =============================================================

-- Costo total del run (suma de todas las llamadas frontier)
ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10, 6) DEFAULT 0;

-- Detalle por step — preparado para S19 (por ahora solo cost_usd se popula)
ALTER TABLE agent_steps
    ADD COLUMN IF NOT EXISTS cost_usd       NUMERIC(10, 6) DEFAULT 0;

ALTER TABLE agent_steps
    ADD COLUMN IF NOT EXISTS input_tokens   INTEGER DEFAULT 0;

ALTER TABLE agent_steps
    ADD COLUMN IF NOT EXISTS output_tokens  INTEGER DEFAULT 0;

ALTER TABLE agent_steps
    ADD COLUMN IF NOT EXISTS model          VARCHAR(50);
