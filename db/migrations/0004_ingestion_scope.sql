-- 0004: record exactly what each ingestion run covered.
-- A run that loaded one store of M5 and a run that loaded all ten both say
-- "succeeded"; without scope, a partial load could be mistaken for a full one.
ALTER TABLE ingestion_run
    ADD COLUMN scope JSONB NOT NULL DEFAULT '{"partial": false}'::jsonb;

COMMENT ON COLUMN ingestion_run.scope IS
    'What this run covered, e.g. {"partial": true, "stores": ["CA_1"]}. partial=false means the whole source file.';

-- Grants from 0003 covered tables that existed then; keep read access consistent.
GRANT SELECT ON ingestion_run TO cpgos_agent, cpgos_reviewer;
