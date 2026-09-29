"""Owner-side reorder reads, draft revisions and version-bound delivery approvals.

This module adds capabilities to Service without exposing a customer-authenticated
endpoint. A trusted gateway must still authenticate the owner. Approval records do
not send WhatsApp messages or mark invoices sent.
"""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid


def fingerprint(invoice):
    """Hash invoice content canonically, excluding the transient portal URL.

    Args:
        invoice: JSON-compatible full Zoho invoice dictionary.
    Returns:
        SHA-256 hex digest; object key order does not affect the result.
    Raises:
        TypeError: The response cannot be JSON serialized.

    The portal invoice_url can change between otherwise identical reads and
    is excluded. Every other field, including modification metadata, remains
    versioned. This is a conservative content hash, not a Zoho revision/ETag.
    """
    content = {key: value for key, value in invoice.items() if key != 'invoice_url'}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


ORDER_STATUSES = ('sent', 'overdue', 'paid', 'partially_paid', 'unpaid')


def eligible_order(invoice, excluded_ids=()):
    """Return True if an invoice counts as a real customer order.

    Args:
        invoice: Zoho invoice summary or full invoice dictionary.
        excluded_ids: Configured test invoice IDs to ignore.
    Returns:
        False for drafts, voids, unissued statuses, configured test IDs and
        explicit TEST markers in the reference, notes, number or line text.

    Shared by reorder retrieval and the weekly forecast so both use one rule.
    A summary lacks notes and line items; callers that need the full marker
    check must repeat it on the full invoice.
    """
    if str(invoice.get('invoice_id')) in set(map(str, excluded_ids)):
        return False
    if invoice.get('status') not in ORDER_STATUSES:
        return False
    text = ' '.join(str(invoice.get(k, '')) for k in ('reference_number', 'notes', 'invoice_number'))
    text += ' ' + ' '.join(str(line.get(k, '')) for line in invoice.get('line_items', [])
                          for k in ('name', 'description'))
    return not re.search(r'\bTEST(?:\b|[_-])', text, re.I)


class InvoiceWorkflows:
    """Mixin using Service's API, organization-scoped journal and phone lookup."""

    @contextmanager
    def invoice_lock(self, invoice_id):
        """Validate the ID and hold the configured repository's invoice lock.

        PostgreSQL locks coordinate all app sessions; legacy file locks protect
        one installation. Live Zoho content is still rechecked for external edits.
        """
        if not str(invoice_id).isascii() or not str(invoice_id).isdigit():
            raise ValueError('Expected an ASCII numeric invoice ID.')
        with self.store.invoice_lock(str(invoice_id)):
            yield

    @staticmethod
    def require_draft(invoice):
        """Reject invoices outside the unsent, unpaid draft workflow.

        Args: invoice: Current full Zoho invoice.
        Returns: None on success.
        Raises: ValueError for non-drafts, sent history or applied funds.
        """
        if invoice.get('status') != 'draft' or invoice.get('is_emailed'):
            raise ValueError('Only unsent draft invoices can be reviewed or updated.')
        if invoice.get('last_sent_time') or invoice.get('last_reminder_sent_date'):
            raise ValueError('Invoice has sending history; review it manually in Zoho.')
        for field in ('payment_made', 'credits_applied', 'write_off_amount'):
            if Decimal(str(invoice.get(field) or 0)) != 0:
                raise ValueError('Invoice has applied payments/credits; review it manually.')

    def invalidate_reviews(self, invoice_id):
        """Invalidate all review/approval records for an invoice and commit.

        Args: invoice_id: Invoice whose content or recipient has changed.
        Returns: None.
        Raises: DatabaseError if journal persistence fails.

        Called before PUT so even an uncertain remote update revokes approval.
        """
        self.store.invalidate_reviews(invoice_id)

    def reorder_invoice(self, phone, contact_id='', source_invoice_id=''):
        """Retrieve an eligible prior invoice and current catalog details for review.

        Args:
            phone: Customer cellphone; must select one ZAR contact.
            contact_id: Optional client selection for shared phone numbers.
            source_invoice_id: Explicit historical choice when automatic latest
                selection is ambiguous; must still be an eligible owned invoice.
        Returns:
            Historical invoice, current product data per line, pricing/tax
            blockers and a content version. No ready-to-submit prices are guessed.
        Raises:
            ValueError: No eligible order, tied latest dates, ownership mismatch
                or API failure. Never falls back to another customer's invoice.

        Reads all invoice pages; chooses the greatest invoice date. Excludes
        void/voided/draft invoices, configured test IDs and explicit TEST markers.
        Latest-order definition is surfaced in the result. No local proposal or
        remote write is made. Historical totals are not current order quotes.
        """
        customer = self.customer(phone, contact_id)
        excluded = set(self.tenant.test_invoice_ids)
        candidates = []
        summaries = self.api.pages('invoices?customer_id=' + str(customer['contact_id']), 'invoices')
        for summary in summaries:
            iid = str(summary['invoice_id'])
            if source_invoice_id and iid != str(source_invoice_id):
                continue
            if iid in excluded or summary.get('status') in ('void', 'voided', 'draft'):
                continue
            inv = self.invoice(phone, iid, str(customer['contact_id']))
            if not eligible_order(inv, excluded):
                continue
            candidates.append((date.fromisoformat(inv['date']), inv))
        if not candidates:
            raise ValueError(f'No eligible previous order. Ask {self.tenant.owner_name}; drafts, void and known tests are excluded.')
        latest_date = max(d for d, _ in candidates)
        latest = [inv for d, inv in candidates if d == latest_date]
        if len(latest) != 1:
            raise ValueError(f'Several invoices share the latest date. Ask {self.tenant.owner_name} to select source_invoice_id.')
        inv = latest[0]
        lines = []
        blockers = []
        for line in inv.get('line_items', []):
            item_id = str(line.get('item_id') or '')
            item = self.api.get('items/' + item_id)['item'] if item_id.isascii() and item_id.isdigit() else None
            line_issues = []
            if item is None:
                line_issues.append('No catalog item; owner must supply current product/price.')
            else:
                if item.get('status') != 'active':
                    line_issues.append('Product is not active.')
                if self.tenant.vat_registered is not False:
                    if not item.get('tax_id'):
                        line_issues.append('No configured product tax; resolve tax treatment explicitly.')
                    line_issues.append('Confirm whether catalog rate includes VAT before using it.')
            if line.get('discount') or line.get('discount_amount'):
                line_issues.append('Historical discount requires explicit review.')
            lines.append({'previous_line': line, 'current_product': item, 'review_required': line_issues})
        for field in ('shipping_charge', 'adjustment', 'discount_total', 'discount'):
            if inv.get(field):
                blockers.append(f'Review historical {field}; it is not carried forward automatically.')
        return {'source_invoice': inv, 'source_version': fingerprint(inv), 'lines': lines,
                'review_required': blockers, 'customer_confirmation_required': True,
                'selection_policy': 'Latest dated issued invoice; excludes drafts, voids and known tests. Ties require explicit choice.'}

    def prepare_draft_update(self, phone, invoice_id, lines, expected_version, contact_id=''):
        """Prepare replacement invoice lines guarded by the owner's reviewed version.

        Args:
            phone: Customer cellphone.
            invoice_id: Existing numeric draft ID; no new invoice is created.
            lines: Complete desired line list using create_invoice's schema.
                Omitted old lines will be deleted; this is NOT a partial patch.
            expected_version: Fingerprint returned by review_invoice or read.
            contact_id: Optional shared-phone disambiguation.
        Returns:
            Immutable confirmation proposal with before/after content.
        Raises:
            ValueError: Stale/non-draft invoice or invalid line/tax/price input.

        Preserves invoice date and existing due date. Header discounts, shipping,
        adjustments and other header fields are not overwritten by this update.
        Proposal alone does not revoke approval or mutate Zoho.
        """
        with self.invoice_lock(invoice_id):
            current = self.invoice(phone, invoice_id, contact_id)
            self.require_draft(current)
            if fingerprint(current) != expected_version:
                raise ValueError('Invoice changed. Read/review it again before preparing an update.')
            payload, preview = self.build_invoice(phone, lines, current['date'], contact_id)
            payload.pop('payment_terms', None)
            payload['due_date'] = current['due_date']
            # Any existing line discounts must be included deliberately in a
            # replacement design; this first version refuses to erase them silently.
            if any(l.get('discount') or l.get('discount_amount') for l in current.get('line_items', [])):
                raise ValueError('Draft has line discounts; edit those in Zoho until supported here.')
            preview['estimated_total_note'] = 'Line subtotal only; existing header charges/discounts remain. Zoho computes final total.'
            return self.proposal('draft_update', {'invoice_id': str(invoice_id), 'phone': self.phone(phone),
                'contact_id': str(current['customer_id']), 'expected_version': expected_version, 'body': payload},
                {'action': 'Replace all draft lines', 'before': current, 'after': preview})

    def review_invoice(self, phone, invoice_id, contact_id=''):
        """Save a versioned owner PDF preview without marking the invoice sent.

        Args: phone/invoice_id/contact_id: Owned draft selection.
        Returns: Review ID, fingerprint, immutable PDF path/hash, invoice details
            and exact owner approval phrase. Review expires after 30 minutes.
        Raises: ValueError for non-draft/content changes; OSError for file errors.

        Reads invoice before and after PDF generation. Local versioned copies
        are never overwritten by subsequent preview downloads. No message sent.
        """
        with self.invoice_lock(invoice_id):
            inv = self.invoice(phone, invoice_id, contact_id)
            self.require_draft(inv)
            version = fingerprint(inv)
            downloaded = Path(self.api.pdf(invoice_id)).read_bytes()
            if not downloaded.startswith(b'%PDF-'):
                raise ValueError('Invalid PDF; no approval preview saved.')
            after = self.invoice(phone, invoice_id, contact_id)
            if fingerprint(after) != version:
                self.invalidate_reviews(invoice_id)
                raise ValueError('Invoice changed during PDF generation; request a new review.')
            review_id = uuid.uuid4().hex
            directory = self.pdf_dir / 'reviews'
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = directory / f'{review_id}.pdf'
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(downloaded)
            self.invalidate_reviews(invoice_id)
            digest = hashlib.sha256(downloaded).hexdigest()
            self.store.save_review(dict(review_id=review_id, invoice_id=str(invoice_id),
                customer_id=str(inv['customer_id']), phone=self.phone(phone), version=version,
                invoice=inv, pdf_path=str(path), pdf_hash=digest, created=time.time(), status='pending'))
            return {'review_id': review_id, 'version': version, 'invoice': inv, 'pdf_path': str(path),
                    'pdf_hash': digest, 'recipient': self.phone(phone), 'expires_in_seconds': 1800,
                    'approval_required': 'APPROVE ' + review_id, 'delivery': 'Owner preview only; invoice remains unsent.'}

    def validate_review(self, review_id):
        """Check saved review against live draft, recipient mapping and PDF hash.

        Args: review_id: Previously created owner review ID.
        Returns: Saved review as a dict if still pending/approved and current.
        Raises: ValueError for expiry, supersession, file or content changes.
        Side effects: Invalidates the invoice's reviews on live version mismatch.
        """
        review = self.store.review(review_id)
        if not review:
            raise ValueError('Unknown invoice review.')
        if review['status'] not in ('pending', 'approved') or time.time() - review['created'] > 1800:
            raise ValueError('Review expired or was superseded. Generate a fresh owner preview.')
        inv = self.invoice(review['phone'], review['invoice_id'], review['customer_id'])
        self.require_draft(inv)
        if fingerprint(inv) != review['version']:
            self.invalidate_reviews(review['invoice_id'])
            raise ValueError('Invoice changed since preview. Approval is invalid; review the new version.')
        try:
            digest = hashlib.sha256(Path(review['pdf_path']).read_bytes()).hexdigest()
        except OSError:
            raise ValueError('Review PDF is missing; generate a fresh preview.') from None
        if digest != review['pdf_hash']:
            self.invalidate_reviews(review['invoice_id'])
            raise ValueError('Review PDF changed; approval is invalid.')
        return review

    def approve_invoice(self, review_id, owner_confirmation):
        """Persist owner approval for one exact invoice/PDF/recipient version.

        Args:
            review_id: ID returned with the PDF the owner reviewed.
            owner_confirmation: Exact 'APPROVE <review_id>' reply from the owner.
        Returns: Validated local approval record. No WhatsApp send or Zoho write.
        Raises: ValueError if text, ownership, expiry or version checks fail.

        Owner authentication belongs to the gateway. Do not expose this method
        or the owner MCP server to customer conversations.
        """
        if owner_confirmation.strip() != 'APPROVE ' + review_id:
            raise ValueError(f'Relay {self.tenant.owner_name}’s exact APPROVE reply for the current review.')
        saved = self.store.review(review_id)
        if not saved:
            raise ValueError('Unknown review.')
        with self.invoice_lock(saved['invoice_id']):
            review = self.validate_review(review_id)
            approval_id = self.store.approve_review(review, uuid.uuid4().hex)
            return {'approval_id': approval_id, 'invoice_id': review['invoice_id'], 'version': review['version'],
                    'recipient': review['phone'], 'pdf_path': review['pdf_path'], 'pdf_hash': review['pdf_hash'],
                    'status': 'approved', 'sent': False}

    def check_approval(self, approval_id):
        """Revalidate authorization immediately before a future delivery adapter acts.

        Args: approval_id: Stored approval identifier, not an arbitrary invoice ID.
        Returns: Bound invoice/version/recipient/PDF details; nothing is sent.
        Raises: ValueError if unknown, invalidated, expired or remotely changed.

        This check is a point-in-time read, not a send lock or one-time delivery
        claim. The future gateway must add atomic delivery state and reconcile
        provider outcomes before using this as a sending workflow.
        """
        approval = self.store.approval(approval_id)
        if not approval or approval['status'] != 'approved':
            raise ValueError('No active approval.')
        with self.invoice_lock(approval['invoice_id']):
            # Recheck status after waiting for a competing update/review lock.
            approval = self.store.approval(approval_id)
            if not approval or approval['status'] != 'approved':
                raise ValueError('No active approval.')
            review = self.validate_review(approval['review_id'])
            return {'approval_id': approval_id, 'invoice_id': approval['invoice_id'], 'version': review['version'],
                    'recipient': review['phone'], 'pdf_path': review['pdf_path'], 'pdf_hash': review['pdf_hash'],
                    'status': 'approved', 'sent': False}

    def execute_draft_update(self, payload):
        """Execute a confirmed update with live preconditions and approval revocation.

        Args: payload: Internal immutable draft_update proposal body.
        Returns: Zoho update response plus new version and review-required flag.
        Raises: ValueError on stale/non-draft state or API failure.

        Called only by Service.confirm after its durable attempt marker. A
        failed/uncertain PUT revokes approvals and is never blindly retried.
        """
        iid = payload['invoice_id']
        with self.invoice_lock(iid):
            inv = self.invoice(payload['phone'], iid, payload['contact_id'])
            self.require_draft(inv)
            if fingerprint(inv) != payload['expected_version']:
                self.invalidate_reviews(iid)
                raise ValueError('Draft changed since update preview. No update submitted.')
            self.invalidate_reviews(iid)
            result = self.api.put('invoices/' + iid, payload['body'])
            current = self.invoice(payload['phone'], iid, payload['contact_id'])
            self.require_draft(current)
            return {**result, 'version': fingerprint(current), 'owner_review_required': True,
                    'next_step': 'Call review_invoice for a fresh PDF. Nothing was sent.'}
