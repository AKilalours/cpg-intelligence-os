-- Addition 11: risk-controlled straight-through processing. The engine may post cash with no person
-- only in a lane that has earned it: people reviewed a sample of that lane's suggestions, for this
-- engine version, and the 95% upper bound on its error rate is within the lane's budget. A closed
-- lane sends every suggestion to a person, and each of those reviews is more evidence. The database
-- decides, not the engine: the engine cannot read the sampling salt, cannot change the policy, and an
-- automatic posting the gate did not allow is refused here.

ALTER TABLE cash_application
    ADD COLUMN lane TEXT,
    ADD COLUMN engine_version TEXT,
    ADD COLUMN routed TEXT CHECK (routed IN ('auto', 'audit', 'review', 'legacy')),
    ADD COLUMN reversed_at TIMESTAMPTZ;

-- Rows written before the gate existed. Their lane follows the confidence the engine gave them.
UPDATE cash_application SET
    engine_version = match_explanation->>'engine',
    lane = CASE confidence WHEN 0.99 THEN 'exact_reference' WHEN 0.98 THEN 'explained_short_pay'
                           WHEN 0.95 THEN 'near_reference' END,
    routed = 'legacy';

CREATE TABLE stp_policy (
    lane            TEXT PRIMARY KEY,
    description     TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'off' CHECK (mode IN ('off', 'enforce')),
    max_error_rate  NUMERIC(6,5) NOT NULL CHECK (max_error_rate > 0 AND max_error_rate < 1),
    confidence      NUMERIC(4,3) NOT NULL DEFAULT 0.95 CHECK (confidence > 0.5 AND confidence < 1),
    audit_rate      NUMERIC(4,3) NOT NULL DEFAULT 0.05 CHECK (audit_rate > 0 AND audit_rate <= 1),
    window_size     INT NOT NULL DEFAULT 1000 CHECK (window_size >= 30),
    salt            TEXT NOT NULL DEFAULT md5(random()::text || clock_timestamp()::text),
    updated_by      TEXT NOT NULL DEFAULT session_user,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO stp_policy (lane, description, max_error_rate) VALUES
    ('exact_reference', 'reference names the invoice and the amount equals its open balance', 0.01),
    ('explained_short_pay', 'exact reference; the line shows invoice total, deduction and net', 0.01),
    ('near_reference', 'reference one keystroke from an invoice number, amount equals its balance', 0.01);

REVOKE ALL ON stp_policy FROM PUBLIC;
GRANT SELECT (lane, description, mode, max_error_rate, confidence, audit_rate, window_size, updated_by, updated_at)
    ON stp_policy TO cpgos_engine, cpgos_reviewer, cpgos_agent;
GRANT UPDATE (reversed_at) ON cash_application TO cpgos_reviewer;

-- Every change to a policy is an audited, attributed act (session_user is the login, not a role).
CREATE FUNCTION audit_stp_policy() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_by := session_user;
    NEW.updated_at := clock_timestamp();
    INSERT INTO audit_event (actor_type, actor_id, action, payload)
    VALUES ('human', session_user, 'stp_policy_change',
            jsonb_build_object('lane', NEW.lane,
                               'before', to_jsonb(OLD) - 'salt', 'after', to_jsonb(NEW) - 'salt'));
    RETURN NEW;
END $$;
CREATE TRIGGER stp_policy_audited BEFORE UPDATE ON stp_policy FOR EACH ROW EXECUTE FUNCTION audit_stp_policy();

-- One-sided Clopper-Pearson upper bound: the largest error rate still consistent, at this
-- confidence, with seeing k errors in n reviews. 0 errors in 299 reviews gives 0.99%.
CREATE FUNCTION stp_upper_bound(k INT, n INT, conf NUMERIC) RETURNS DOUBLE PRECISION
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE lo DOUBLE PRECISION := 0; hi DOUBLE PRECISION := 1; p DOUBLE PRECISION;
        term DOUBLE PRECISION; cdf DOUBLE PRECISION; i INT;
BEGIN
    IF n <= 0 THEN RETURN 1; END IF;
    IF k >= n THEN RETURN 1; END IF;
    FOR it IN 1..60 LOOP
        p := (lo + hi) / 2;
        term := power(1 - p, n);
        cdf := term;
        FOR i IN 0..k - 1 LOOP
            term := term * (n - i) / (i + 1) * p / (1 - p);
            cdf := cdf + term;
        END LOOP;
        IF cdf > 1 - conf THEN lo := p; ELSE hi := p; END IF;
    END LOOP;
    RETURN hi;
END $$;

-- The evidence for one lane and engine version: the most recent person-decided reviews
-- (approved = the suggestion was right, rejected = wrong), plus automatic postings a person
-- later reversed since the policy last changed. A reversal is a wrong posting that got through:
-- it stops the lane until someone deliberately changes the policy (which is audited).
CREATE FUNCTION stp_lane_status(p_lane TEXT, p_version TEXT)
RETURNS TABLE (lane TEXT, engine_version TEXT, mode TEXT, max_error_rate NUMERIC, reviews INT, errors INT,
               upper_bound DOUBLE PRECISION, reversals INT, is_open BOOLEAN)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
#variable_conflict use_column
DECLARE pol stp_policy; n INT; k INT; rev INT; ub DOUBLE PRECISION;
BEGIN
    SELECT * INTO pol FROM stp_policy s WHERE s.lane = p_lane;
    IF NOT FOUND THEN
        RETURN QUERY SELECT p_lane, p_version, 'missing'::TEXT, NULL::NUMERIC, 0, 0, 1::DOUBLE PRECISION, 0, FALSE;
        RETURN;
    END IF;
    SELECT count(*), count(*) FILTER (WHERE w.status = 'rejected') INTO n, k FROM (
        SELECT c.status FROM cash_application c
        WHERE c.lane = p_lane AND c.engine_version IS NOT DISTINCT FROM p_version
          AND c.routed = 'audit' AND c.status IN ('approved', 'rejected')
        ORDER BY c.cash_application_id DESC LIMIT pol.window_size) w;
    SELECT count(*) INTO rev FROM cash_application c
    WHERE c.lane = p_lane AND c.engine_version IS NOT DISTINCT FROM p_version
      AND c.routed = 'auto' AND c.status = 'reversed' AND c.reversed_at > pol.updated_at;
    ub := stp_upper_bound(k, n, pol.confidence);
    RETURN QUERY SELECT p_lane, p_version, pol.mode, pol.max_error_rate, n, k, ub, rev,
                        (n > 0 AND ub <= pol.max_error_rate AND rev = 0);
END $$;

-- Where one suggestion goes. 'auto': post it. 'audit': a person reviews it, and the review counts as
-- evidence. In an open lane a hidden, stable sample (audit_rate) is still audited, so the error rate
-- keeps being measured after autonomy is earned; a closed lane audits everything.
CREATE FUNCTION stp_route(p_lane TEXT, p_version TEXT, p_line BIGINT) RETURNS TEXT
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE pol stp_policy; st RECORD; u DOUBLE PRECISION;
BEGIN
    SELECT * INTO pol FROM stp_policy s WHERE s.lane = p_lane;
    IF NOT FOUND THEN RETURN 'audit'; END IF;
    IF pol.mode = 'off' THEN RETURN 'auto'; END IF;
    SELECT * INTO st FROM stp_lane_status(p_lane, p_version);
    IF NOT st.is_open THEN RETURN 'audit'; END IF;
    u := ('x' || substr(md5(pol.salt || ':' || p_lane || ':' || p_line::TEXT), 1, 8))::BIT(32)::BIGINT / 4294967296.0;
    RETURN CASE WHEN u < pol.audit_rate THEN 'audit' ELSE 'auto' END;
END $$;

-- The engine's inserts are checked against the gate. Runs as the inserting role (not a definer),
-- so current_user is the engine when the engine writes.
CREATE FUNCTION enforce_error_budget() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r TEXT;
BEGIN
    IF current_user <> 'cpgos_engine' THEN RETURN NEW; END IF;
    IF NEW.lane IS NULL THEN
        IF NEW.status = 'approved' THEN
            RAISE EXCEPTION 'error budget: an automatic posting needs a lane (line %)', NEW.remittance_line_id;
        END IF;
        NEW.routed := 'review';
        RETURN NEW;
    END IF;
    r := stp_route(NEW.lane, NEW.engine_version, NEW.remittance_line_id);
    IF NEW.status = 'approved' AND r <> 'auto' THEN
        RAISE EXCEPTION 'error budget: lane % must send line % to a person', NEW.lane, NEW.remittance_line_id;
    END IF;
    NEW.routed := CASE WHEN NEW.status = 'approved' THEN 'auto' WHEN r = 'audit' THEN 'audit' ELSE 'review' END;
    NEW.reversed_at := NULL;
    RETURN NEW;
END $$;
CREATE TRIGGER cash_application_error_budget BEFORE INSERT ON cash_application
    FOR EACH ROW EXECUTE FUNCTION enforce_error_budget();
