"""Orders copy: nightly sync and immediate copies after app writes. No live Zoho."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hustleai.workflows.service import Service
from tests.unit.test_forecast import ForecastAPI


class SyncAPI(ForecastAPI):
    """Fake Zoho with a daily quota counter and invoice creation."""

    def __init__(self):
        super().__init__()
        self.rate_remaining = None
        self.calls = 0
        self.posts = []

    def get(self, path):
        self.calls += 1
        if self.rate_remaining is not None:
            self.rate_remaining -= 1
        return super().get(path)

    def post(self, path, payload):
        self.posts.append(path)
        invoice = {'invoice_id': '900', 'invoice_number': 'INV-900', 'customer_id': payload['customer_id'],
                   'customer_name': 'Alpha Store', 'date': payload['date'], 'status': 'draft', 'total': 111,
                   'notes': '', 'last_modified_time': '2026-10-05T09:00:00+0200',
                   'line_items': copy.deepcopy(payload['line_items'])}
        self.invoices['900'] = copy.deepcopy(invoice)
        return {'invoice': invoice}


class OrderSyncTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config = self.root / 'config.json'
        config.write_text(json.dumps({'organization_id': '123', 'country_code': '27', 'vat_registered': False}))
        self.config_patch = patch('hustleai.workflows.service.CONFIG', config)
        self.config_patch.start()
        rows = ''.join(f'| {n} | +2782000000{n} |\n' for n in range(1, 7))
        (self.root / 'zoho-client-phone-map.md').write_text('Organization: 123\n' + rows)
        self.api = SyncAPI()
        self.s = Service(self.api, self.root)

    def tearDown(self):
        self.s.close()
        self.config_patch.stop()
        self.temp.cleanup()

    def orders(self):
        return {o['invoice_id']: o for o in self.s.store.orders()}

    def test_first_sync_copies_everything_with_details(self):
        result = self.s.sync_orders()
        count = len(self.api.invoices)
        self.assertEqual((result['listed'], result['new_or_changed'], result['details_fetched'], result['details_pending']),
                         (count, count, count, 0))
        orders = self.orders()
        self.assertEqual(orders['104']['line_items'][0]['quantity'], 4)
        self.assertEqual((orders['104']['total'], orders['104']['invoice_date']), ('140', '2026-09-28'))
        self.assertTrue(all(o['details_synced'] for o in orders.values()))
        self.assertIsNotNone(self.s.store.last_sync('orders'))

    def test_unchanged_invoices_cost_no_detail_reads(self):
        self.s.sync_orders()
        self.api.gets.clear()
        result = self.s.sync_orders()
        self.assertEqual((result['new_or_changed'], result['details_fetched']), (0, 0))
        self.assertEqual(self.api.gets, ['invoices'])

    def test_changed_invoice_is_refreshed(self):
        self.s.sync_orders()
        self.api.invoices['104'].update(status='void', last_modified_time='2026-10-01T08:00:00+0200')
        self.api.gets.clear()
        result = self.s.sync_orders()
        self.assertEqual((result['new_or_changed'], result['details_fetched']), (1, 1))
        self.assertEqual(self.api.gets, ['invoices', 'invoices/104'])
        self.assertEqual(self.orders()['104']['status'], 'void')

    def test_deleted_invoice_is_flagged_and_can_return(self):
        self.s.sync_orders()
        removed = self.api.invoices.pop('301')
        self.assertEqual(self.s.sync_orders()['deleted'], 1)
        self.assertNotIn('301', self.orders())
        self.api.invoices['301'] = removed
        self.s.sync_orders()
        self.assertIn('301', self.orders())

    def test_quota_reserve_stops_detail_reads_and_next_run_resumes(self):
        self.api.rate_remaining = 105
        result = self.s.sync_orders(reserve=100)
        self.assertEqual(result['details_fetched'], 5)  # five reads leave exactly the 100 in reserve
        pending = result['details_pending']
        self.assertGreater(pending, 0)
        # Newest invoices are fetched first.
        self.assertEqual(sorted(i for i, o in self.orders().items() if o['details_synced']), ['105', '106', '107', '504', '601'])
        self.api.rate_remaining = 1000
        self.assertEqual(self.s.sync_orders()['details_fetched'], pending)

    def test_app_created_invoice_is_copied_immediately(self):
        proposal = self.s.prepare_invoice('0820000001', [{'description': 'Honey', 'rate': '111'}], '2026-10-05')
        oid = proposal['operation_id']
        self.s.confirm(oid, 'CONFIRM ' + oid)
        order = self.orders()['900']
        self.assertEqual((order['status'], order['details_synced']), ('draft', True))
        self.assertEqual(order['line_items'][0]['name'], 'Honey')
        source = self.s.db.execute("SELECT source FROM orders WHERE invoice_id='900'").fetchone()[0]
        self.assertEqual(source, 'app')
        # The nightly sync sees the same modification time and leaves it alone.
        self.api.gets.clear()
        self.s.sync_orders()
        self.assertNotIn('invoices/900', self.api.gets)

    def test_copy_failure_never_breaks_a_confirmed_write(self):
        self.assertFalse(self.s.record_order_quietly({'invoice_id': 'not-a-number'}))
        with patch.object(self.s.store, 'save_order', side_effect=RuntimeError('database down')):
            proposal = self.s.prepare_invoice('0820000001', [{'description': 'Honey', 'rate': '111'}], '2026-10-05')
            oid = proposal['operation_id']
            result = self.s.confirm(oid, 'CONFIRM ' + oid)
        self.assertEqual(result['invoice']['invoice_id'], '900')
        self.assertEqual(self.api.posts, ['invoices'])


if __name__ == '__main__':
    unittest.main()
