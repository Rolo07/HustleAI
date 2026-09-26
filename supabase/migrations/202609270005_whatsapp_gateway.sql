-- WhatsApp gateway state: verified webhook events, an idempotent outbox and
-- 24-hour conversation windows. webhook_events and delivery_attempts were
-- created by the initial migration without runtime access; this grants it.
-- Additive upgrade applied by hustleai.storage.postgres.upgrade.

ALTER TABLE hustle_private.delivery_attempts
    ADD COLUMN message jsonb,
    ADD COLUMN error text,
    ADD COLUMN attempts integer NOT NULL DEFAULT 0;
ALTER TABLE hustle_private.delivery_attempts
    ADD CONSTRAINT delivery_status_known CHECK (status IN
        ('pending','sent','delivered','read','failed','waiting_window'));
CREATE INDEX delivery_attempts_waiting
    ON hustle_private.delivery_attempts (organization_id, recipient, status, created_at);
COMMENT ON TABLE hustle_private.delivery_attempts IS
'Outbox: one row per logical message (unique idempotency key). A message is sent at most once; failed and waiting messages keep their content for review or retry.';

CREATE TABLE hustle_private.conversation_windows (
    organization_id text NOT NULL,
    phone text NOT NULL CHECK (phone ~ '^\+[0-9]{8,15}$'),
    last_inbound_at timestamptz NOT NULL,
    PRIMARY KEY (organization_id, phone)
);
COMMENT ON TABLE hustle_private.conversation_windows IS
'Time of the latest inbound message per number. WhatsApp allows free-form replies for 24 hours after it.';

ALTER TABLE hustle_private.conversation_windows ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON hustle_private.conversation_windows FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON hustle_private.webhook_events TO hustleai_runtime;
GRANT SELECT,INSERT,UPDATE ON hustle_private.delivery_attempts TO hustleai_runtime;
GRANT SELECT,INSERT,UPDATE ON hustle_private.conversation_windows TO hustleai_runtime;

-- Copy the runtime role's organization condition, as in migration 0003.
DO $$
DECLARE
    condition text;
    target text;
BEGIN
    SELECT qual INTO condition FROM pg_policies
    WHERE schemaname = 'hustle_private' AND tablename = 'operations'
      AND 'hustleai_runtime' = ANY (roles)
    ORDER BY policyname LIMIT 1;
    IF condition IS NOT NULL THEN
        FOREACH target IN ARRAY ARRAY['webhook_events','delivery_attempts','conversation_windows'] LOOP
            EXECUTE format('CREATE POLICY runtime_organization ON hustle_private.%I TO hustleai_runtime USING (%s) WITH CHECK (%s)', target, condition, condition);
        END LOOP;
    END IF;
END;
$$;
