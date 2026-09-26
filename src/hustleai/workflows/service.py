"""Owner workflow facade and immutable operation dispatcher.

This facade composes focused workflow modules. Storage repositories own transactions and locks. Hosted database failures stop
work before remote writes; the legacy backend is never an automatic fallback.
"""
import json
from pathlib import Path
from decimal import Decimal
import uuid
from hustleai.config import ROOT, CONFIG, MAPPING
from hustleai.integrations.zoho.client import API
from hustleai.storage.backend import open_repository
from contextlib import nullcontext
from hustleai.workflows.clients import ClientWorkflows
from hustleai.workflows.invoices import InvoiceCreation
from hustleai.workflows.payments import PaymentWorkflows
from hustleai.workflows.invoice_review import InvoiceWorkflows


class Service(ClientWorkflows, InvoiceCreation, PaymentWorkflows, InvoiceWorkflows):
    """Compose owner-side operations while retaining the existing Service API."""

    def __init__(self, api=None, root=ROOT):
        """Initialize an organization-scoped service and its operation journal.

        Args:
            api: Optional API-compatible dependency, primarily for isolated tests.
                If omitted, construct API and refresh the saved OAuth access token.
            root: Private runtime directory containing backend credentials and PDFs.
                Configuration and credentials still use the module-level paths.

        Raises:
            OSError: Configuration or local files cannot be read/created.
            ValueError: Configuration decoding or OAuth initialization fails.
            DatabaseError: The operation journal cannot be initialized.

        Side Effects:
            Opens the configured repository before contacting Zoho. Each instance
            owns a database connection; use a context manager or close(). Payloads/results contain
            client information and must be protected like the credentials.
        """
        config = json.loads(CONFIG.read_text())
        self.country = config['country_code']
        self.workflow_config = config
        self.root = Path(root)
        self.mapping = self.root / MAPPING.name
        self.store = open_repository(config, self.root)
        self.db = self.store.connection  # Compatibility for existing local tests.
        try:
            self.api = api or API(config['organization_id'])
            if str(self.api.organization) != str(config['organization_id']):
                raise ValueError('API and storage organizations must match.')
        except BaseException:
            self.close()
            raise

    def close(self):
        """Release this service's storage session, including any session locks."""
        self.store.close()

    def __enter__(self):
        """Use a service as a context manager to guarantee connection cleanup."""
        return self

    def __exit__(self, exc_type, exc, traceback):
        """Close storage after successful calls and after API or validation errors."""
        self.close()

    def proposal(self, kind, payload, preview):
        """Persist an immutable write proposal without mutating Zoho.

        Args:
            kind: Internal kind: client, invoice, payment, or draft_update.
            payload: Validated JSON-ready request body prepared by this service.
            preview: JSON-ready details to show Roland before confirmation.

        Returns:
            Dictionary with operation_id, preview, expires_in_seconds (1800),
            and confirmation_required instructions.

        Raises:
            TypeError: The payload or preview cannot be serialized as JSON.
            DatabaseError: Persistence fails.

        Side Effects:
            Commits a pending journal row. The UUID identifies this proposal;
            it is not evidence of user consent. Validation belongs to prepare_*;
            this internal helper must not receive unvalidated agent payloads.
        """
        operation = uuid.uuid4().hex
        self.store.create_operation(operation, kind, payload, preview)
        return {'operation_id': operation, 'preview': preview, 'expires_in_seconds': 1800,
                'confirmation_required': f'Ask Roland to reply CONFIRM {operation}. Do not confirm on his behalf.'}

    def confirm(self, operation_id, user_confirmation):
        """Execute one confirmed proposal with durable protection against replay.

        Args:
            operation_id: UUID returned by a prepare_* proposal in this organization.
            user_confirmation: Roland's exact "CONFIRM <operation_id>" reply;
                surrounding whitespace is ignored. The caller must authenticate
                Roland and relay his reply, never synthesize consent.

        Returns:
            Zoho's response dictionary, or the saved response for a completed ID.

        Raises:
            ValueError: Confirmation/organization/state/expiry validation fails,
                a payment preflight check fails, or a Zoho request fails.
            DatabaseError: Journal access or persistence fails.
            OSError: Private file access fails. Database errors can occur after
                remote success; the attempted operation must be reconciled.

        Side Effects:
            Locks and marks a pending operation attempted before network work,
            submits at most one mutation for that ID, then saves its successful result.
            Client creation also adds its phone mapping in the same PostgreSQL
            transaction as the successful result. Payments recheck ownership, balance, and existing references.

        Pending proposals expire after 30 minutes. Completed results remain
        replayable. An attempted operation stays blocked even if a preflight or
        network call fails: inspect Zoho before preparing a new operation. This
        avoids blind retries after uncertain writes, but does not deduplicate
        separate proposals or concurrent writes by other applications. The string
        check is not authentication; the Hermes gateway enforces caller identity.
        """
        if user_confirmation.strip() != 'CONFIRM ' + operation_id:
            raise ValueError('Relay the exact confirmation supplied by Roland after showing the preview.')
        operation = self.store.claim_operation(operation_id)
        if operation['status'] == 'done':
            return operation['result']
        kind, payload = operation['kind'], operation['payload']
        if kind == 'payment':
            invoice = self.api.get('invoices/' + payload['invoices'][0]['invoice_id'])['invoice']
            if str(invoice['customer_id']) != payload['customer_id'] or Decimal(str(invoice['balance'])) < Decimal(str(payload['amount'])):
                raise ValueError('Invoice changed; payment not submitted. Prepare a new preview.')
            for payment in self.api.pages('customerpayments?customer_id=' + payload['customer_id'], 'customerpayments'):
                if payment.get('reference_number') == payload['reference_number']:
                    raise ValueError('Payment reference already exists; review it before proceeding.')
        # A complete mapping sync and new-client write must not race. The
        # durable attempted claim remains blocked if lock acquisition fails.
        lock = self.store.named_lock('mappings') if kind == 'client' and hasattr(self.store, 'named_lock') else nullcontext()
        with lock:
            if kind == 'draft_update':
                result = self.execute_draft_update(payload)
            else:
                path = {'client': 'contacts', 'invoice': 'invoices', 'payment': 'customerpayments'}[kind]
                result = self.api.post(path, payload)
            mapping = (str(result['contact']['contact_id']), payload['contact_persons'][0]['mobile']) if kind == 'client' else None
            self.store.finish_operation(operation_id, result, mapping)
        return result
