"""Keep a Supabase copy of Zoho invoices for the forecast and reorder lookups.

Zoho stays the source of truth. Invoices the app creates or updates are saved
immediately. A nightly sync, run before Zoho's daily request limit resets at
midnight South African time, lists every invoice (a few requests), saves new
and changed ones, flags deleted ones and reads missing details newest first
until the remaining daily quota reaches a reserve.
"""
from datetime import date, datetime, timezone
import time

from hustleai.domain.validation import identifier

SYNC_NAME = 'orders'
DEFAULT_RESERVE = 100      # Daily Zoho requests left untouched for other work.
CLIENT_CALL_CAP = 780      # Stay under the client's 800 GET cap per session.
STALE_HOURS = 48           # Older syncs are flagged in the forecast report.


def order_record(invoice):
    """Map a Zoho invoice summary or full invoice to a stored order.

    Returns (order, has_details). A summary has no line items or notes; a full
    invoice does. Only the fields needed for eligibility and forecasting are
    kept, not the whole Zoho document.
    """
    has_details = 'line_items' in invoice
    lines = None
    if has_details:
        lines = [{k: line.get(k) for k in ('item_id', 'name', 'description', 'quantity', 'rate')}
                 for line in invoice.get('line_items', [])]
    order = {'invoice_id': identifier(invoice['invoice_id']), 'customer_id': str(invoice['customer_id']),
             'customer_name': invoice.get('customer_name'), 'invoice_number': invoice.get('invoice_number'),
             'reference_number': invoice.get('reference_number'),
             'invoice_date': date.fromisoformat(invoice['date']), 'status': invoice.get('status') or 'unknown',
             'total': None if invoice.get('total') is None else str(invoice['total']),
             'last_modified_time': invoice.get('last_modified_time'),
             'notes': invoice.get('notes') if has_details else None, 'line_items': lines}
    return order, has_details


def as_invoice(order):
    """Present a stored order with Zoho's field names for shared rules."""
    invoice = {'invoice_id': order['invoice_id'], 'customer_id': order['customer_id'],
               'customer_name': order['customer_name'], 'invoice_number': order['invoice_number'] or '',
               'reference_number': order['reference_number'] or '', 'date': order['invoice_date'],
               'status': order['status'], 'total': order['total'] or 0}
    if order['details_synced']:
        invoice['notes'] = order['notes'] or ''
        invoice['line_items'] = order['line_items'] or []
    return invoice


class OrderSync:
    """Mixin using Service's API and storage."""

    def record_order(self, invoice, source='app'):
        """Save one invoice from Zoho's response; full invoices include details."""
        order, has_details = order_record(invoice)
        self.store.save_order(order, has_details, source)

    def record_order_quietly(self, invoice):
        """Best-effort save after a confirmed Zoho write.

        A failure never undoes or hides the Zoho result: the nightly sync
        repairs any missed copy. Returns True if the copy was saved.
        """
        try:
            self.record_order(invoice)
            return True
        except Exception:
            return False

    def quota_left(self, reserve):
        """True while this session may make another Zoho read."""
        remaining = getattr(self.api, 'rate_remaining', None)
        if remaining is not None and remaining <= reserve:
            return False
        return getattr(self.api, 'calls', 0) < CLIENT_CALL_CAP

    def sync_orders(self, reserve=DEFAULT_RESERVE):
        """Compare every Zoho invoice with the copy and repair differences.

        Args:
            reserve: Daily Zoho requests to leave unused.
        Returns:
            Counts of listed, new or changed, deleted and detail reads, plus
            details still pending for the next run.
        Raises:
            ValueError: A Zoho or database failure. The listing must finish
                before deletions are flagged, so a failed run changes nothing
                it cannot justify.

        Reads Zoho only. New and changed invoices are saved from the list;
        their details are read newest first until the quota reserve or the
        client's request cap is reached. Remaining details wait for the next run.
        """
        started = time.time()
        known = self.store.order_index()
        seen = set()
        changes = []
        for summary in self.api.pages('invoices', 'invoices'):
            invoice_id = identifier(summary['invoice_id'])
            seen.add(invoice_id)
            previous = known.get(invoice_id)
            if (previous is None or previous['deleted']
                    or previous['last_modified_time'] != summary.get('last_modified_time')):
                changes.append(order_record(summary))
        # One transaction for all list changes: far fewer database round trips.
        self.store.save_orders(changes, 'sync')
        changed = len(changes)
        deleted = [iid for iid, row in known.items() if iid not in seen and not row['deleted']]
        self.store.mark_orders_deleted(deleted)
        pending = self.store.orders_missing_details()
        fetched = 0
        for invoice_id in pending:
            if not self.quota_left(reserve):
                break
            self.record_order(self.api.get('invoices/' + invoice_id)['invoice'], source='sync')
            fetched += 1
        result = {'listed': len(seen), 'new_or_changed': changed, 'deleted': len(deleted),
                  'details_fetched': fetched, 'details_pending': len(pending) - fetched,
                  'zoho_requests_left': getattr(self.api, 'rate_remaining', None)}
        self.store.record_sync(SYNC_NAME, started, time.time(), result)
        return result

    def orders_freshness(self):
        """Return the last sync time as ISO text and whether it is stale.

        Raises ValueError if the orders copy has never been synced, because an
        empty copy would produce a misleading forecast.
        """
        sync = self.store.last_sync(SYNC_NAME)
        if not sync:
            raise ValueError('Orders have not been synced from Zoho yet. Run: hustleai-orders sync')
        finished = datetime.fromtimestamp(sync['finished'], timezone.utc)
        stale = time.time() - sync['finished'] > STALE_HOURS * 3600
        return finished.isoformat(timespec='seconds'), stale, sync['result']
