-- Phase 4: a drift alarm on the cash stream sends every would-be automatic posting to a person until a
-- named person clears it with a reason. The monitor role raises alarms; it cannot clear them, post cash,
-- or change policy. The engine cannot touch alarms at all.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cpgos_monitor') THEN
        CREATE ROLE cpgos_monitor NOLOGIN;
    END IF;
END $$;

CREATE TABLE drift_alarm (
    drift_alarm_id  BIGSERIAL PRIMARY KEY,
    stream          TEXT NOT NULL CHECK (stream IN ('cash', 'deductions', 'prices')),
    feature         TEXT NOT NULL,
    checked_through DATE NOT NULL,               -- the data the check covered
    p_value         DOUBLE PRECISION NOT NULL,
    psi             DOUBLE PRECISION NOT NULL,
    moved           JSONB NOT NULL,              -- what changed, in plain values, for the person deciding
    monitor_version TEXT NOT NULL,
    raised_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    cleared_at      TIMESTAMPTZ,
    cleared_by      TEXT,
    clear_note      TEXT,
    CHECK ((cleared_at IS NULL) = (cleared_by IS NULL) AND (cleared_at IS NULL) = (clear_note IS NULL))
);
-- One open alarm per stream and feature: a monitor run never stacks duplicates.
CREATE UNIQUE INDEX drift_alarm_one_open ON drift_alarm (stream, feature) WHERE cleared_at IS NULL;

REVOKE ALL ON drift_alarm FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO cpgos_monitor;
GRANT SELECT ON drift_alarm TO cpgos_engine, cpgos_reviewer, cpgos_agent, cpgos_monitor;
GRANT INSERT ON drift_alarm, audit_event TO cpgos_monitor;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO cpgos_monitor;
GRANT UPDATE (cleared_at, cleared_by, clear_note) ON drift_alarm TO cpgos_reviewer;

-- The gate: same as 0021, plus one rule. In an enforced lane, any open cash alarm means 'audit'.
CREATE OR REPLACE FUNCTION stp_route(p_lane TEXT, p_version TEXT, p_line BIGINT) RETURNS TEXT
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE pol stp_policy; st RECORD; u DOUBLE PRECISION;
BEGIN
    SELECT * INTO pol FROM stp_policy s WHERE s.lane = p_lane;
    IF NOT FOUND THEN RETURN 'audit'; END IF;
    IF pol.mode = 'off' THEN RETURN 'auto'; END IF;
    IF EXISTS (SELECT 1 FROM drift_alarm a WHERE a.stream = 'cash' AND a.cleared_at IS NULL) THEN
        RETURN 'audit';
    END IF;
    SELECT * INTO st FROM stp_lane_status(p_lane, p_version);
    IF NOT st.is_open THEN RETURN 'audit'; END IF;
    u := ('x' || substr(md5(pol.salt || ':' || p_lane || ':' || p_line::TEXT), 1, 8))::BIT(32)::BIGINT / 4294967296.0;
    RETURN CASE WHEN u < pol.audit_rate THEN 'audit' ELSE 'auto' END;
END $$;
