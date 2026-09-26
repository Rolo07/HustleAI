-- Synced copy of Zoho invoices for the forecast and future reorder lookups.
-- Zoho stays the source of truth. The app writes invoices it creates or
-- updates immediately; a nightly sync compares every invoice and repairs drift.
-- Additive upgrade applied by hustleai.storage.postgres.upgrade.

CREATE TABLE hustle_private.orders (
    organization_id text NOT NULL,
    invoice_id text NOT NULL CHECK (invoice_id ~ '^[0-9]+$'),
    customer_id text NOT NULL,
    customer_name text,
    invoice_number text,
    reference_number text,
    invoice_date date NOT NULL,
    status text NOT NULL,
    total text,
    last_modified_time text,
    notes text,
    line_items jsonb,
    details_synced boolean NOT NULL DEFAULT false,
    deleted boolean NOT NULL DEFAULT false,
    source text NOT NULL CHECK (source IN ('app','sync')),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, invoice_id)
);
CREATE INDEX orders_customer_date ON hustle_private.orders (organization_id, customer_id, invoice_date DESC);
COMMENT ON TABLE hustle_private.orders IS
'Copy of Zoho invoices. Summary fields come from the invoice list; notes and line items need a detail read (details_synced). Invoices missing from a complete Zoho listing are marked deleted.';

CREATE TABLE hustle_private.sync_runs (
    organization_id text NOT NULL,
    name text NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    result jsonb NOT NULL,
    PRIMARY KEY (organization_id, name)
);
COMMENT ON TABLE hustle_private.sync_runs IS
'Latest completed run per sync job, used to show data freshness.';

ALTER TABLE hustle_private.orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE hustle_private.sync_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON hustle_private.orders FROM PUBLIC;
REVOKE ALL ON hustle_private.sync_runs FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON hustle_private.orders TO hustleai_runtime;
GRANT SELECT,INSERT,UPDATE ON hustle_private.sync_runs TO hustleai_runtime;

-- Copy the runtime role's organization condition, as in migration 0003.
DO $$
DECLARE
    condition text;
BEGIN
    SELECT qual INTO condition FROM pg_policies
    WHERE schemaname = 'hustle_private' AND tablename = 'operations'
      AND 'hustleai_runtime' = ANY (roles)
    ORDER BY policyname LIMIT 1;
    IF condition IS NOT NULL THEN
        EXECUTE format('CREATE POLICY runtime_organization ON hustle_private.orders TO hustleai_runtime USING (%s) WITH CHECK (%s)', condition, condition);
        EXECUTE format('CREATE POLICY runtime_organization ON hustle_private.sync_runs TO hustleai_runtime USING (%s) WITH CHECK (%s)', condition, condition);
    END IF;
END;
$$;
