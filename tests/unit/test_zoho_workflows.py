"""Isolated workflow tests: no live Zoho mutations or credentials required."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hustleai.workflows.service import Service
from hustleai.workflows.invoice_review import fingerprint

PHONE = '0837758811'


class WorkflowAPI:
    """Deterministic Zoho transport with mutable remote state and failure hooks."""
    organization = '123'

    def __init__(self, root):
        self.root = Path(root)
        self.puts = []
        self.fail_put = False
        self.on_pdf = None
        self.records = {'2': {
            'invoice_id': '2', 'invoice_number': 'INV-2', 'customer_id': '1',
            'currency_code': 'ZAR', 'status': 'draft', 'date': '2026-09-26',
            'due_date': '2026-10-03', 'is_emailed': False, 'is_inclusive_tax': True,
            'balance': 115, 'total': 115, 'shipping_charge': 5,
            'line_items': [{'line_item_id': '22', 'item_id': '4', 'name': 'Product',
                            'description': 'Product', 'quantity': 1, 'rate': 110, 'tax_id': '3'}]}}

    def get(self, path):
        if path.startswith('contacts/'):
            return {'contact': {'contact_id': '1', 'contact_name': 'Customer', 'currency_code': 'ZAR',
                                'contact_persons': [{'mobile': PHONE}]}}
        if path.startswith('invoices/'):
            return {'invoice': copy.deepcopy(self.records[path.split('/')[1]])}
        if path.startswith('items/'):
            return {'item': {'item_id': '4', 'name': 'Product', 'rate': 120,
                             'tax_id': '3', 'status': 'active'}}
        if path == 'settings/taxes':
            return {'taxes': [{'tax_id': '3', 'tax_name': 'VAT', 'tax_percentage': 15}]}
        raise AssertionError(path)

    def pages(self, path, key):
        assert key == 'invoices'
        return iter(copy.deepcopy(list(self.records.values())))

    def pdf(self, iid):
        path = self.root / 'download.pdf'
        path.write_bytes(b'%PDF-1.4\nTest invoice\n%%EOF')
        if self.on_pdf:
            self.on_pdf()
        return str(path)

    def put(self, path, payload):
        self.puts.append((path, copy.deepcopy(payload)))
        if self.fail_put:
            raise ValueError('Uncertain update outcome')
        invoice = self.records[path.split('/')[1]]
        invoice.update(copy.deepcopy(payload))
        invoice['total'] = sum(l['rate'] * l['quantity'] for l in payload['line_items']) + invoice['shipping_charge']
        invoice['balance'] = invoice['total']
        return {'invoice': copy.deepcopy(invoice)}


class WorkflowTests(unittest.TestCase):
    """Verify selection, version integrity, confirmed updates and replay behavior."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config = self.root / 'config.json'
        config.write_text(json.dumps({'organization_id': '123', 'country_code': '27'}))
        self.config_patch = patch('hustleai.workflows.service.CONFIG', config)
        self.config_patch.start()
        (self.root / 'zoho-client-phone-map.md').write_text('Organization: 123\n| 1 | +27837758811 |\n')
        self.api = WorkflowAPI(self.root)
        self.s = Service(self.api, self.root)

    def tearDown(self):
        self.s.db.close()
        self.config_patch.stop()
        self.temp.cleanup()

    def review(self):
        return self.s.review_invoice(PHONE, '2')

    def approval(self, review):
        return self.s.approve_invoice(review['review_id'], 'APPROVE ' + review['review_id'])

    def update(self, review):
        return self.s.prepare_draft_update(PHONE, '2', [{'item_id': '4', 'quantity': 2, 'rate': '120'}], review['version'])

    def historical(self, iid, day, status='paid', description='Normal product'):
        inv = copy.deepcopy(self.api.records['2'])
        inv.update(invoice_id=iid, date=day, status=status)
        inv['line_items'][0]['description'] = description
        self.api.records[iid] = inv

    def test_reorder_excludes_drafts_void_and_tests(self):
        self.historical('3', '2026-09-20')
        self.historical('4', '2026-09-25', 'void')
        self.historical('5', '2026-09-26', 'paid', 'TEST ONLY - product')
        self.historical('6', '2026-09-24')
        self.s.workflow_config['test_invoice_ids'] = ['6']
        result = self.s.reorder_invoice(PHONE)
        self.assertEqual(result['source_invoice']['invoice_id'], '3')
        self.assertEqual(result['lines'][0]['current_product']['rate'], 120)
        self.assertTrue(result['lines'][0]['review_required'])
        self.assertEqual(self.api.puts, [])

    def test_reorder_skips_vat_checks_when_not_registered(self):
        self.historical('3', '2026-09-20')
        self.s.workflow_config['vat_registered'] = False
        issues = self.s.reorder_invoice(PHONE)['lines'][0]['review_required']
        self.assertFalse(any('VAT' in i or 'tax' in i for i in issues))

    def test_reorder_tie_requires_selection(self):
        self.historical('3', '2026-09-20')
        self.historical('4', '2026-09-20')
        with self.assertRaisesRegex(ValueError, 'Several'):
            self.s.reorder_invoice(PHONE)
        self.assertEqual(self.s.reorder_invoice(PHONE, source_invoice_id='4')['source_invoice']['invoice_id'], '4')

    def test_no_eligible_order(self):
        with self.assertRaisesRegex(ValueError, 'No eligible'):
            self.s.reorder_invoice(PHONE)

    def test_confirmed_update_same_invoice_replay_and_invalidation(self):
        review = self.review()
        approval = self.approval(review)
        proposal = self.update(review)
        self.assertEqual(self.api.puts, [])
        oid = proposal['operation_id']
        result = self.s.confirm(oid, 'CONFIRM ' + oid)
        repeated = self.s.confirm(oid, 'CONFIRM ' + oid)
        self.assertEqual(result, repeated)
        self.assertEqual(len(self.api.puts), 1)
        self.assertEqual(self.api.puts[0][0], 'invoices/2')
        self.assertEqual(self.api.records['2']['due_date'], '2026-10-03')
        self.assertEqual(self.api.records['2']['shipping_charge'], 5)
        self.assertEqual(self.api.records['2']['status'], 'draft')
        self.assertFalse(self.api.records['2']['is_emailed'])
        with self.assertRaises(ValueError):
            self.s.check_approval(approval['approval_id'])
        fresh = self.review()
        self.assertNotEqual(review['version'], fresh['version'])
        self.assertNotEqual(review['pdf_path'], fresh['pdf_path'])

    def test_update_requires_confirmation(self):
        p = self.update(self.review())
        with self.assertRaises(ValueError):
            self.s.confirm(p['operation_id'], 'yes')
        self.assertEqual(self.api.puts, [])

    def test_stale_update_preparation(self):
        r = self.review()
        self.api.records['2']['notes'] = 'External change'
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.update(r)

    def test_stale_update_confirmation(self):
        p = self.update(self.review())
        self.api.records['2']['notes'] = 'External change'
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.s.confirm(p['operation_id'], 'CONFIRM ' + p['operation_id'])
        self.assertEqual(self.api.puts, [])

    def test_uncertain_update_revokes_and_never_retries(self):
        r = self.review(); a = self.approval(r); p = self.update(r)
        self.api.fail_put = True
        for _ in range(2):
            with self.assertRaises(ValueError):
                self.s.confirm(p['operation_id'], 'CONFIRM ' + p['operation_id'])
        self.assertEqual(len(self.api.puts), 1)
        with self.assertRaises(ValueError):
            self.s.check_approval(a['approval_id'])

    def test_sent_or_paid_invoice_cannot_be_reviewed(self):
        for status in ('sent', 'paid', 'void'):
            self.api.records['2']['status'] = status
            with self.assertRaises(ValueError): self.review()
        self.api.records['2']['status'] = 'draft'
        self.api.records['2']['payment_made'] = 1
        with self.assertRaises(ValueError): self.review()

    def test_pdf_changes_during_generation(self):
        self.api.on_pdf = lambda: self.api.records['2'].update(notes='Changed mid-download')
        with self.assertRaisesRegex(ValueError, 'during PDF'):
            self.review()
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM invoice_reviews').fetchone()[0], 0)

    def test_approval_requires_exact_phrase_and_replays(self):
        r = self.review()
        with self.assertRaises(ValueError): self.s.approve_invoice(r['review_id'], 'looks good')
        a = self.approval(r)
        self.assertEqual(a, self.approval(r))
        self.assertEqual(a['recipient'], '+27837758811')
        self.assertFalse(a['sent'])
        self.assertEqual(a, self.s.check_approval(a['approval_id']))

    def test_external_edits_revoke_approval(self):
        r = self.review(); a = self.approval(r)
        self.api.records['2']['billing_address'] = {'city': 'Changed'}
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.s.check_approval(a['approval_id'])

    def test_new_review_supersedes_previous(self):
        a = self.approval(self.review())
        self.review()
        with self.assertRaises(ValueError): self.s.check_approval(a['approval_id'])

    def test_expired_review(self):
        r = self.review()
        self.s.db.execute('UPDATE invoice_reviews SET created=0'); self.s.db.commit()
        with self.assertRaisesRegex(ValueError, 'expired'): self.approval(r)

    def test_pdf_tampering(self):
        r = self.review(); a = self.approval(r)
        Path(r['pdf_path']).write_bytes(b'%PDF-1.4\nDifferent')
        with self.assertRaisesRegex(ValueError, 'PDF changed'):
            self.s.check_approval(a['approval_id'])

    def test_wrong_customer_and_unknown_approval(self):
        with self.assertRaises(ValueError): self.s.review_invoice(PHONE, '2', '9')
        with self.assertRaises(ValueError): self.s.check_approval('unknown')
        with self.assertRaises(ValueError): self.s.approve_invoice('unknown', 'APPROVE unknown')

    def test_transient_invoice_url_does_not_invalidate_review(self):
        self.api.records['2']['invoice_url'] = 'https://example.invalid/first'
        r = self.review()
        self.api.records['2']['invoice_url'] = 'https://example.invalid/refreshed'
        a = self.approval(r)
        self.assertEqual(a['status'], 'approved')
        self.api.records['2']['notes'] = 'Material edit'
        with self.assertRaises(ValueError):
            self.s.check_approval(a['approval_id'])

    def test_fingerprint_ignores_key_order_not_content(self):
        self.assertEqual(fingerprint({'a': 1, 'b': 2}), fingerprint({'b': 2, 'a': 1}))
        self.assertNotEqual(fingerprint({'a': 1}), fingerprint({'a': 2}))


if __name__ == '__main__':
    unittest.main()
