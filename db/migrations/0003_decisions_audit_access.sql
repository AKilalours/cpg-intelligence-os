-- 0003: deterministic calculations, cases, append-only audit, least-privilege roles.

-- Every derived number (a discrepancy, a balance, a promo lift) is stored with
-- its inputs and formula version, so it can be recomputed and checked.
CREATE TABLE calculation (
    calculation_id  BIGSERIAL PRIMARY KEY,
    kind            TEXT NOT NULL,                  -- e.g. 'deduction_discrepancy', 'open_balance'
    formula_version TEXT NOT NULL,
    inputs          JSONB NOT NULL,                 -- record ids + evidence ids + values used
    result          NUMERIC(18,4) NOT NULL,
    currency        CHAR(3),
    computed_by     TEXT NOT NULL,                  -- code path, never an LLM
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE investigation_case (
    case_id         BIGSERIAL PRIMARY KEY,
    case_type       TEXT NOT NULL CHECK (case_type IN ('deduction', 'unmatched_cash', 'forecast_anomaly', 'promotion_review')),
    deduction_id    BIGINT REFERENCES deduction,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'awaiting_evidence', 'awaiting_approval', 'closed')),
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_event (
    audit_event_id  BIGSERIAL PRIMARY KEY,
    case_id         BIGINT REFERENCES investigation_case,
    actor_type      TEXT NOT NULL CHECK (actor_type IN ('system', 'agent', 'human')),
    actor_id        TEXT NOT NULL,
    action          TEXT NOT NULL,                  -- e.g. 'tool_call', 'recommendation', 'approve', 'reject'
    evidence_ids    BIGINT[] NOT NULL DEFAULT '{}',
    calculation_ids BIGINT[] NOT NULL DEFAULT '{}',
    payload         JSONB NOT NULL DEFAULT '{}',
    model_version   TEXT,
    prompt_version  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The audit log is append-only: history cannot be edited after the fact,
-- not even by the application.
CREATE FUNCTION forbid_audit_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only (% blocked)', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_event_append_only
    BEFORE UPDATE OR DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();

-- Same rule for calculations: a wrong number gets a new row, not an edit.
CREATE TRIGGER calculation_append_only
    BEFORE UPDATE OR DELETE ON calculation
    FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();

-- Least privilege. The agent role can READ the ledger and WRITE only to
-- evidence, cases, and the audit log. It physically cannot change an invoice,
-- payment, deduction amount, or cash application. "Never let an LLM silently
-- alter a ledger value" is enforced by the database, not by a prompt.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cpgos_agent') THEN
        CREATE ROLE cpgos_agent NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cpgos_reviewer') THEN
        CREATE ROLE cpgos_reviewer NOLOGIN;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO cpgos_agent, cpgos_reviewer;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO cpgos_agent, cpgos_reviewer;
GRANT INSERT ON evidence_reference, investigation_case, audit_event TO cpgos_agent;
GRANT UPDATE (status) ON investigation_case TO cpgos_agent;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO cpgos_agent, cpgos_reviewer;

-- Humans approve: only the reviewer role can change decision statuses.
GRANT INSERT ON audit_event TO cpgos_reviewer;
GRANT UPDATE (status) ON cash_application, deduction, investigation_case TO cpgos_reviewer;
