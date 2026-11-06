-- Decision 95: the audit rate chosen on the main world by the rule written in advance (decision 94),
-- then confirmed once on 8 fresh worlds (report 40).
--
-- A policy change made by a migration is a reviewed code change, not one person's decision at a
-- console, so it is recorded as a system act that names the migration. The trigger reads the
-- transaction-local setting cpgos.actor; without it a change is a human act by the login, as before.
-- (Only the policy owner can update stp_policy at all, so the setting cannot hide anyone else.)
CREATE OR REPLACE FUNCTION audit_stp_policy() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE actor TEXT := nullif(current_setting('cpgos.actor', true), '');
BEGIN
    NEW.updated_by := coalesce(actor, session_user);
    NEW.updated_at := clock_timestamp();
    INSERT INTO audit_event (actor_type, actor_id, action, payload)
    VALUES (CASE WHEN actor IS NULL THEN 'human' ELSE 'system' END, coalesce(actor, session_user),
            'stp_policy_change',
            jsonb_build_object('lane', NEW.lane, 'login', session_user,
                               'before', to_jsonb(OLD) - 'salt', 'after', to_jsonb(NEW) - 'salt'));
    RETURN NEW;
END $$;

SET LOCAL cpgos.actor = 'migration 0022_audit_rate_chosen';
UPDATE stp_policy SET audit_rate = 0.20;
RESET cpgos.actor;
