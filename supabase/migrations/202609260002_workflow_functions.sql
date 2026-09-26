-- Atomic journal transitions used by the trusted Python workflow service.
-- Apply inside a transaction together with the migration checksum ledger.
-- SECURITY INVOKER preserves the runtime role's organization-specific RLS.
-- These functions neither authenticate owner consent nor call Zoho/WhatsApp.

CREATE FUNCTION hustle_private.claim_operation(p_organization text, p_operation text)
RETURNS SETOF hustle_private.operations
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE
    operation hustle_private.operations%ROWTYPE;
BEGIN
    SELECT * INTO operation FROM hustle_private.operations
    WHERE organization_id = p_organization AND id = p_operation FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Unknown operation for this organization.';
    END IF;
    IF operation.status = 'done' THEN
        RETURN NEXT operation;
        RETURN;
    END IF;
    IF operation.status <> 'pending' OR
       operation.created_at < clock_timestamp() - interval '30 minutes' THEN
        RAISE EXCEPTION 'Expired or already attempted operation. Check Zoho before preparing another; never blindly retry.';
    END IF;
    UPDATE hustle_private.operations SET status = 'attempted'
    WHERE organization_id = p_organization AND id = p_operation;
    operation.status := 'attempted';
    RETURN NEXT operation;
END;
$$;
COMMENT ON FUNCTION hustle_private.claim_operation(text,text) IS
'Locks and claims one unexpired pending operation, or returns its completed result. Caller must commit before any remote write; attempted outcomes are never automatically retried. Owner consent is checked by Python.';

CREATE FUNCTION hustle_private.finish_operation(
    p_organization text, p_operation text, p_result jsonb,
    p_contact text DEFAULT NULL, p_phone text DEFAULT NULL)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE
    operation hustle_private.operations%ROWTYPE;
BEGIN
    IF p_result IS NULL OR p_result = 'null'::jsonb THEN
        RAISE EXCEPTION 'A successful operation requires a saved result.';
    END IF;
    IF (p_contact IS NULL) <> (p_phone IS NULL) THEN
        RAISE EXCEPTION 'Supply both contact ID and phone, or neither.';
    END IF;
    SELECT * INTO operation FROM hustle_private.operations
    WHERE organization_id = p_organization AND id = p_operation FOR UPDATE;
    IF NOT FOUND OR operation.status <> 'attempted' THEN
        RAISE EXCEPTION 'Operation is not in the attempted state.';
    END IF;
    IF p_contact IS NOT NULL AND operation.kind <> 'client' THEN
        RAISE EXCEPTION 'Only client creation can attach a phone mapping.';
    END IF;
    UPDATE hustle_private.operations SET status = 'done', result = p_result
    WHERE organization_id = p_organization AND id = p_operation;
    IF p_contact IS NOT NULL THEN
        INSERT INTO hustle_private.phone_mappings (organization_id,contact_id,phone)
        VALUES (p_organization,p_contact,p_phone)
        ON CONFLICT (organization_id,contact_id,phone) DO NOTHING;
    END IF;
END;
$$;
COMMENT ON FUNCTION hustle_private.finish_operation(text,text,jsonb,text,text) IS
'Atomically saves a successful attempted operation and optional new-client mapping. Any failure rolls back both writes; Python must reconcile uncertain remote outcomes rather than resubmit.';

CREATE FUNCTION hustle_private.invalidate_invoice_reviews(p_organization text, p_invoice text)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
BEGIN
    UPDATE hustle_private.invoice_reviews SET status = 'invalid'
    WHERE organization_id = p_organization AND invoice_id = p_invoice;
    UPDATE hustle_private.invoice_approvals SET status = 'invalid'
    WHERE organization_id = p_organization AND invoice_id = p_invoice;
END;
$$;
COMMENT ON FUNCTION hustle_private.invalidate_invoice_reviews(text,text) IS
'Revokes invoice reviews and approvals together. Python holds the invoice advisory lock and commits this call before a remote draft update, including updates with uncertain outcomes.';

CREATE FUNCTION hustle_private.approve_invoice_review(
    p_organization text, p_review text, p_approval text,
    p_version text, p_phone text, p_pdf_hash text)
RETURNS text LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE
    review hustle_private.invoice_reviews%ROWTYPE;
    approval hustle_private.invoice_approvals%ROWTYPE;
BEGIN
    SELECT * INTO review FROM hustle_private.invoice_reviews
    WHERE organization_id = p_organization AND review_id = p_review FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Unknown invoice review.';
    END IF;
    IF review.status NOT IN ('pending','approved') OR
       review.created_at < clock_timestamp() - interval '30 minutes' THEN
        RAISE EXCEPTION 'Review expired or was superseded. Generate a fresh owner preview.';
    END IF;
    IF review.version IS DISTINCT FROM p_version OR review.phone IS DISTINCT FROM p_phone
       OR review.pdf_hash IS DISTINCT FROM p_pdf_hash THEN
        RAISE EXCEPTION 'Review binding changed; request a new owner preview.';
    END IF;
    SELECT * INTO approval FROM hustle_private.invoice_approvals
    WHERE organization_id = p_organization AND review_id = p_review;
    IF FOUND THEN
        IF approval.status <> 'approved' THEN
            RAISE EXCEPTION 'Approval is invalid; request a new review.';
        END IF;
        RETURN approval.approval_id;
    END IF;
    INSERT INTO hustle_private.invoice_approvals
        (approval_id,review_id,organization_id,invoice_id,version,recipient,created_at,status)
    VALUES (p_approval,p_review,p_organization,review.invoice_id,review.version,
            review.phone,clock_timestamp(),'approved');
    UPDATE hustle_private.invoice_reviews SET status = 'approved'
    WHERE organization_id = p_organization AND review_id = p_review;
    RETURN p_approval;
END;
$$;
COMMENT ON FUNCTION hustle_private.approve_invoice_review(text,text,text,text,text,text) IS
'Idempotently records one unexpired review approval bound to version, recipient and PDF hash. Python must authenticate owner consent and recheck live Zoho/PDF content while holding its invoice lock. This function does not authorize or perform delivery by itself.';

-- PostgreSQL gives PUBLIC execute by default: revoke in the same transaction.
REVOKE ALL ON FUNCTION hustle_private.claim_operation(text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION hustle_private.finish_operation(text,text,jsonb,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION hustle_private.invalidate_invoice_reviews(text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION hustle_private.approve_invoice_review(text,text,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hustle_private.claim_operation(text,text) TO hustleai_runtime;
GRANT EXECUTE ON FUNCTION hustle_private.finish_operation(text,text,jsonb,text,text) TO hustleai_runtime;
GRANT EXECUTE ON FUNCTION hustle_private.invalidate_invoice_reviews(text,text) TO hustleai_runtime;
GRANT EXECUTE ON FUNCTION hustle_private.approve_invoice_review(text,text,text,text,text,text) TO hustleai_runtime;
