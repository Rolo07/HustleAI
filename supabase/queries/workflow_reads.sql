-- Reference prepared queries for the restricted backend connection.
-- The application already executes equivalent parameterized repository queries;
-- these statements are for a backend SQL session, not for Hermes/customer input.
-- PREPARE installs session-local statements only; it creates no tables or data.
-- Parameters always include the organization in addition to the record key.

-- $1: organization ID; $2: normalized E.164 phone. Preserve ambiguous matches.
PREPARE hustleai_find_contacts(text,text) AS
SELECT contact_id FROM hustle_private.phone_mappings
WHERE organization_id=$1 AND phone=$2 ORDER BY contact_id;

-- $1: organization ID; $2: operation ID. A read does NOT claim a remote write.
PREPARE hustleai_read_operation(text,text) AS
SELECT * FROM hustle_private.operations WHERE organization_id=$1 AND id=$2;

-- $1: organization ID; $2: review ID. Python still checks live Zoho/PDF content.
PREPARE hustleai_read_review(text,text) AS
SELECT * FROM hustle_private.invoice_reviews
WHERE organization_id=$1 AND review_id=$2;

-- $1: organization ID; $2: approval ID. A row alone is NOT delivery permission.
PREPARE hustleai_read_approval(text,text) AS
SELECT * FROM hustle_private.invoice_approvals
WHERE organization_id=$1 AND approval_id=$2;

-- Usage with privately supplied values in a backend SQL session:
-- EXECUTE hustleai_find_contacts('ORGANIZATION_ID', '+27XXXXXXXXX');
-- Do not put real customer records or credentials into source-controlled examples.
