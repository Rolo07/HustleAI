import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hustleai.workflows.service import Service


class FakeAPI:
    organization = '782228241'
    def __init__(self):
        self.posts = []
        self.fail = False
    def get(self, path):
        if path.startswith('contacts/'):
            return {'contact': {'contact_id':'1', 'contact_name':'Test', 'currency_code':'ZAR', 'contact_persons':[{'mobile':'0837758811'}]}}
        if path == 'settings/taxes':
            return {'taxes':[{'tax_id':'3', 'tax_name':'VAT', 'tax_percentage':15}]}
        if path.startswith('items/'):
            return {'item':{'name':'Test item','tax_id':'3'}}
        if path.startswith('invoices/'):
            return {'invoice':{'invoice_id':'2','customer_id':'1','currency_code':'ZAR','balance':115}}
        raise AssertionError(path)
    def pages(self, path, key):
        return iter([])
    def post(self, path, payload):
        self.posts.append((path,payload))
        if self.fail:
            raise ValueError('Network failure after possible remote write')
        return {'invoice':{'invoice_id':'2'}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.api = FakeAPI()
        root = Path(self.temp.name)
        config = root / 'config.json'
        config.write_text(json.dumps({'organization_id': self.api.organization, 'country_code': '27'}))
        self.config_patch = patch('hustleai.workflows.service.CONFIG', config)
        self.config_patch.start()
        (root/'zoho-client-phone-map.md').write_text('Organization: 782228241\n| 1 | +27837758811 |\n')
        self.s = Service(self.api, root)
    def tearDown(self):
        self.s.db.close()
        self.config_patch.stop()
        self.temp.cleanup()
    def proposal(self):
        return self.s.prepare_invoice('0837758811',[{'item_id':'4','rate':'115'}],'2026-09-26')
    def test_inclusive_and_due_date(self):
        p=self.proposal()
        self.assertEqual(self.api.posts,[])
        self.assertEqual(p['preview']['invoice']['due_date'],'2026-10-03')
        self.assertTrue(p['preview']['invoice']['is_inclusive_tax'])
        self.assertFalse(p['preview']['invoice']['send'])
        self.assertEqual(p['preview']['estimated_total'],'115.00')
    def test_confirmation_and_replay(self):
        p=self.proposal(); oid=p['operation_id']
        with self.assertRaises(ValueError): self.s.confirm(oid,'yes')
        self.assertEqual(self.api.posts,[])
        a=self.s.confirm(oid,'CONFIRM '+oid)
        b=self.s.confirm(oid,'CONFIRM '+oid)
        self.assertEqual(a,b)
        self.assertEqual(len(self.api.posts),1)
    def test_uncertain_write_not_retried(self):
        p=self.proposal(); oid=p['operation_id']; self.api.fail=True
        with self.assertRaises(ValueError): self.s.confirm(oid,'CONFIRM '+oid)
        with self.assertRaises(ValueError): self.s.confirm(oid,'CONFIRM '+oid)
        self.assertEqual(len(self.api.posts),1)
    def test_expiry(self):
        p=self.proposal(); oid=p['operation_id']
        self.s.db.execute('UPDATE operations SET created=0'); self.s.db.commit()
        with self.assertRaises(ValueError): self.s.confirm(oid,'CONFIRM '+oid)
        self.assertEqual(self.api.posts,[])
    def test_wrong_customer_and_overpayment(self):
        with self.assertRaises(ValueError): self.s.invoice('0837758811','2','9')
        with self.assertRaises(ValueError): self.s.prepare_payment('0837758811','2','116','2026-09-26','cash','test')
    def test_explicit_no_tax(self):
        p=self.s.prepare_invoice('0837758811',[{'description':'Test', 'rate':'111', 'no_tax':True}],'2026-09-26')
        self.assertNotIn('tax_id',p['preview']['invoice']['line_items'][0])
        self.assertEqual(p['preview']['estimated_total'],'111.00')
        with self.assertRaises(ValueError):
            self.s.prepare_invoice('0837758811',[{'item_id':'4','rate':'111','no_tax':True}],'2026-09-26')
    def test_no_guessed_tax_or_nan(self):
        with self.assertRaises(ValueError): self.s.prepare_invoice('0837758811',[{'description':'x','rate':'115'}],'2026-09-26')
        with self.assertRaises(ValueError): self.s.prepare_invoice('0837758811',[{'item_id':'4','rate':'NaN'}],'2026-09-26')


if __name__ == '__main__': unittest.main()
