-- Phase 2b: layout routing. A trained router is stored as data, so the engine
-- can route documents without access to the labels it was trained from.

CREATE TABLE layout_model (
    layout_model_id BIGSERIAL PRIMARY KEY,
    version         TEXT NOT NULL,
    grid_w          INTEGER NOT NULL CHECK (grid_w > 0),
    grid_h          INTEGER NOT NULL CHECK (grid_h > 0),
    threshold       DOUBLE PRECISION NOT NULL,      -- farther than this from every known layout = new
    trained_on      JSONB NOT NULL,                 -- which splits chose what
    content_sha256  CHAR(64) NOT NULL UNIQUE,       -- same fingerprints + threshold = same model
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE layout_centroid (
    layout_model_id BIGINT NOT NULL REFERENCES layout_model,
    layout_key      TEXT NOT NULL,
    n_train         INTEGER NOT NULL,
    centroid        REAL[] NOT NULL,
    PRIMARY KEY (layout_model_id, layout_key)
);

-- The router's decision per document and model version. NULL layout_key = new layout.
CREATE TABLE document_route (
    source_document_id BIGINT NOT NULL REFERENCES source_document,
    layout_model_id BIGINT NOT NULL REFERENCES layout_model,
    layout_key      TEXT,
    distance        DOUBLE PRECISION NOT NULL,
    margin          DOUBLE PRECISION,               -- gap to the second-nearest layout
    routed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_document_id, layout_model_id)
);

GRANT SELECT ON layout_model, layout_centroid, document_route TO cpgos_engine, cpgos_agent, cpgos_reviewer;
GRANT INSERT ON document_route TO cpgos_engine;
