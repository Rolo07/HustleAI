-- Per-tenant database isolation.
-- Every tenant has its own login role, mapped to one organization in
-- tenant_roles. Logins join the group role hustleai_tenant, which holds all
-- table and function grants. One policy per table lets a login see and write
-- only its own organization's rows, so an application bug cannot cross tenants.
-- Additive upgrade applied by hustleai.storage.postgres.upgrade.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hustleai_tenant') THEN
        CREATE ROLE hustleai_tenant NOLOGIN;
    END IF;
END;
$$;

CREATE TABLE hustle_private.tenant_roles (
    role_name name PRIMARY KEY,
    organization_id text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE hustle_private.tenant_roles IS
'Maps each tenant login role to its one organization. Policies on every tenant table compare against the row for current_user.';
ALTER TABLE hustle_private.tenant_roles ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON hustle_private.tenant_roles FROM PUBLIC;
CREATE POLICY tenant_self ON hustle_private.tenant_roles TO hustleai_tenant
    USING (role_name = current_user);

-- Grants live on the group role; tenant logins inherit them.
GRANT USAGE ON SCHEMA hustle_private TO hustleai_tenant;
GRANT SELECT ON hustle_private.tenant_roles TO hustleai_tenant;
GRANT SELECT,INSERT,UPDATE ON
    hustle_private.operations, hustle_private.phone_mappings, hustle_private.invoice_reviews,
    hustle_private.invoice_approvals, hustle_private.customer_order_cycles, hustle_private.reorder_forecasts,
    hustle_private.orders, hustle_private.sync_runs, hustle_private.webhook_events,
    hustle_private.delivery_attempts, hustle_private.conversation_windows
    TO hustleai_tenant;
GRANT DELETE ON hustle_private.phone_mappings, hustle_private.customer_order_cycles TO hustleai_tenant;
GRANT EXECUTE ON FUNCTION
    hustle_private.claim_operation(text,text),
    hustle_private.finish_operation(text,text,jsonb,text,text),
    hustle_private.invalidate_invoice_reviews(text,text),
    hustle_private.approve_invoice_review(text,text,text,text,text,text)
    TO hustleai_tenant;

-- One helper so future migrations add a tenant table with a single call.
CREATE FUNCTION hustle_private.apply_tenant_policy(target regclass)
RETURNS void LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE
    condition text := 'organization_id = (SELECT t.organization_id FROM hustle_private.tenant_roles t WHERE t.role_name = current_user)';
BEGIN
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %s', target);
    EXECUTE format('CREATE POLICY tenant_isolation ON %s TO hustleai_tenant USING (%s) WITH CHECK (%s)',
                   target, condition, condition);
END;
$$;
REVOKE ALL ON FUNCTION hustle_private.apply_tenant_policy(regclass) FROM PUBLIC;
COMMENT ON FUNCTION hustle_private.apply_tenant_policy(regclass) IS
'Admin helper: enables RLS and (re)creates the tenant_isolation policy on a table with organization_id.';

-- Map the existing single-tenant login to its organization, read from its
-- current policy so no organization ID is stored in source control. Then
-- replace the old per-organization policies with tenant_isolation.
DO $$
DECLARE
    literal text;
    target text;
    old record;
BEGIN
    SELECT substring(qual FROM '''([^'']+)''') INTO literal FROM pg_policies
    WHERE schemaname = 'hustle_private' AND tablename = 'operations'
      AND 'hustleai_runtime' = ANY (roles)
    ORDER BY policyname LIMIT 1;
    IF literal IS NOT NULL AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hustleai_runtime') THEN
        INSERT INTO hustle_private.tenant_roles (role_name, organization_id) VALUES ('hustleai_runtime', literal);
        -- Created NOINHERIT; group grants only apply to inheriting members.
        ALTER ROLE hustleai_runtime INHERIT;
        GRANT hustleai_tenant TO hustleai_runtime;
    END IF;
    FOREACH target IN ARRAY ARRAY['operations','phone_mappings','invoice_reviews','invoice_approvals',
            'customer_order_cycles','reorder_forecasts','orders','sync_runs','webhook_events',
            'delivery_attempts','conversation_windows'] LOOP
        FOR old IN SELECT policyname FROM pg_policies
                   WHERE schemaname = 'hustle_private' AND tablename = target AND policyname <> 'tenant_isolation' LOOP
            EXECUTE format('DROP POLICY %I ON hustle_private.%I', old.policyname, target);
        END LOOP;
        PERFORM hustle_private.apply_tenant_policy(format('hustle_private.%I', target)::regclass);
    END LOOP;
END;
$$;
