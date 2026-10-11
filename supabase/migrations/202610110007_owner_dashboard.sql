-- Persistent owner-dashboard journal, isolated by the existing tenant login.
-- One organization row provides atomic transitions and a session advisory lock
-- serializes sends/takeover across dashboard workers. No public data API access.
CREATE TABLE hustle_private.owner_dashboard (
    organization_id text PRIMARY KEY,
    state jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON hustle_private.owner_dashboard FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON hustle_private.owner_dashboard TO hustleai_tenant;
SELECT hustle_private.apply_tenant_policy('hustle_private.owner_dashboard'::regclass);
