-- Initial hosted PostgreSQL schema. NOT APPLIED by the current Python runtime.
-- Use a private schema through a dedicated backend DB connection, not an agent
-- SQL tool or public Data API. RLS is enabled; no anonymous/client policies exist.
BEGIN;
CREATE SCHEMA IF NOT EXISTS hustle_private;
REVOKE ALL ON SCHEMA hustle_private FROM PUBLIC;

CREATE TABLE hustle_private.phone_mappings (
    organization_id text NOT NULL,
    contact_id text NOT NULL,
    phone text NOT NULL CHECK (phone ~ '^\+[0-9]{8,15}$'),
    verified_at timestamptz,
    PRIMARY KEY (organization_id, contact_id, phone)
);
CREATE INDEX phone_mappings_lookup ON hustle_private.phone_mappings (organization_id, phone);

CREATE TABLE hustle_private.operations (
    id text PRIMARY KEY,
    organization_id text NOT NULL,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    preview jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','attempted','done')),
    result jsonb
);
CREATE INDEX operations_pending ON hustle_private.operations (organization_id, status, created_at);

CREATE TABLE hustle_private.invoice_reviews (
    review_id text PRIMARY KEY,
    organization_id text NOT NULL,
    invoice_id text NOT NULL,
    customer_id text NOT NULL,
    phone text NOT NULL,
    version text NOT NULL,
    invoice jsonb NOT NULL,
    pdf_path text NOT NULL,
    pdf_hash text NOT NULL,
    created_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','approved','invalid')),
    UNIQUE (organization_id, review_id, invoice_id, version, phone)
);
CREATE TABLE hustle_private.invoice_approvals (
    approval_id text PRIMARY KEY,
    review_id text NOT NULL UNIQUE,
    organization_id text NOT NULL,
    invoice_id text NOT NULL,
    version text NOT NULL,
    recipient text NOT NULL,
    created_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('approved','invalid')),
    FOREIGN KEY (organization_id, review_id, invoice_id, version, recipient)
        REFERENCES hustle_private.invoice_reviews
            (organization_id, review_id, invoice_id, version, phone)
);

-- Enquiries and delivery journals are future workflow data, not Zoho mirrors.
CREATE TABLE hustle_private.enquiries (
    id text PRIMARY KEY,
    organization_id text NOT NULL,
    sender_phone text NOT NULL,
    supplied_name text,
    state text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE hustle_private.webhook_events (
    organization_id text NOT NULL,
    provider_event_id text NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    PRIMARY KEY (organization_id, provider_event_id)
);
CREATE TABLE hustle_private.delivery_attempts (
    id text PRIMARY KEY,
    organization_id text NOT NULL,
    purpose text NOT NULL,
    idempotency_key text NOT NULL,
    approval_id text REFERENCES hustle_private.invoice_approvals (approval_id),
    recipient text NOT NULL,
    provider_message_id text,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, idempotency_key),
    UNIQUE (organization_id, provider_message_id)
);
CREATE TABLE hustle_private.daily_summaries (
    id text PRIMARY KEY,
    organization_id text NOT NULL,
    owner_phone text NOT NULL,
    period_start timestamptz NOT NULL,
    period_end timestamptz NOT NULL,
    content jsonb NOT NULL,
    status text NOT NULL,
    CHECK (period_end > period_start),
    UNIQUE (organization_id, owner_phone, period_end)
);

ALTER TABLE hustle_private.phone_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE hustle_private.operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE hustle_private.invoice_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE hustle_private.invoice_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE hustle_private.enquiries ENABLE ROW LEVEL SECURITY;
ALTER TABLE hustle_private.webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE hustle_private.delivery_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE hustle_private.daily_summaries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA hustle_private FROM PUBLIC;
COMMIT;
