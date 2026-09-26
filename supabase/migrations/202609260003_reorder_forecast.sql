-- Weekly reorder forecast: per-customer cycle overrides and saved reports.
-- Additive upgrade applied by hustleai.storage.postgres.upgrade, which owns the
-- transaction. Neither table mirrors Zoho; forecasts never write to Zoho.

CREATE TABLE hustle_private.customer_order_cycles (
    organization_id text NOT NULL,
    contact_id text NOT NULL CHECK (contact_id ~ '^[0-9]+$'),
    cycle_days integer CHECK (cycle_days BETWEEN 1 AND 365),
    excluded boolean NOT NULL DEFAULT false,
    note text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, contact_id),
    CHECK (cycle_days IS NOT NULL OR excluded)
);
COMMENT ON TABLE hustle_private.customer_order_cycles IS
'Owner-set reorder cycle (days) or exclusion per Zoho contact. Replaces the history-based prediction for that customer.';

CREATE TABLE hustle_private.reorder_forecasts (
    organization_id text NOT NULL,
    run_date date NOT NULL,
    window_start date NOT NULL,
    window_end date NOT NULL,
    content jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, run_date),
    CHECK (window_end > window_start)
);
COMMENT ON TABLE hustle_private.reorder_forecasts IS
'One saved weekly forecast per organization and run date. Reruns replace the same row rather than adding duplicates.';

ALTER TABLE hustle_private.customer_order_cycles ENABLE ROW LEVEL SECURITY;
ALTER TABLE hustle_private.reorder_forecasts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON hustle_private.customer_order_cycles FROM PUBLIC;
REVOKE ALL ON hustle_private.reorder_forecasts FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE,DELETE ON hustle_private.customer_order_cycles TO hustleai_runtime;
GRANT SELECT,INSERT,UPDATE ON hustle_private.reorder_forecasts TO hustleai_runtime;

-- Reuse the runtime role's existing organization condition from operations so
-- the organization ID never appears in source control. Without such a policy,
-- RLS denies all runtime access, which is the safe default.
DO $$
DECLARE
    condition text;
BEGIN
    SELECT qual INTO condition FROM pg_policies
    WHERE schemaname = 'hustle_private' AND tablename = 'operations'
      AND 'hustleai_runtime' = ANY (roles)
    ORDER BY policyname LIMIT 1;
    IF condition IS NOT NULL THEN
        EXECUTE format('CREATE POLICY runtime_organization ON hustle_private.customer_order_cycles TO hustleai_runtime USING (%s) WITH CHECK (%s)', condition, condition);
        EXECUTE format('CREATE POLICY runtime_organization ON hustle_private.reorder_forecasts TO hustleai_runtime USING (%s) WITH CHECK (%s)', condition, condition);
    END IF;
END;
$$;
