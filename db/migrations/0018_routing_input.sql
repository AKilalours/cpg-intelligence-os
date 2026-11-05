-- Fix 4: a correction is only useful later if the rule can be replayed on what it saw. Store the
-- routing rule's inputs (each reader's values, labels printed on the page, values two models read
-- that the page scan missed) with every decision, so any future rule version can be re-checked
-- against every value a person confirmed, without calling a model again.
CREATE TABLE routing_input (
    source_document_id BIGINT NOT NULL REFERENCES source_document,
    policy_version  TEXT NOT NULL,
    inputs          JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_document_id, policy_version)
);
GRANT SELECT, INSERT ON routing_input TO cpgos_engine;
GRANT SELECT ON routing_input TO cpgos_agent, cpgos_reviewer;
