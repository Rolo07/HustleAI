"""Tenant settings: loading, defaults, validation and use by the workflows."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hustleai.config import tenant_dir
from hustleai.tenant import Tenant, load_tenant, read_settings
from hustleai.workflows.service import Service
from tests.unit.test_zoho_tools import FakeAPI


class TenantSettingsTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_defaults_without_files(self):
        tenant = load_tenant(self.root)
        self.assertEqual((tenant.currency, tenant.timezone, tenant.payment_terms_days, tenant.owner_name),
                         ('ZAR', 'Africa/Johannesburg', 7, 'the owner'))
        self.assertTrue(all(tenant.has(f) for f in ('invoicing', 'forecast', 'orders_sync', 'whatsapp')))
        self.assertIsNone(tenant.vat_registered)

    def test_tenant_json_overrides_legacy_settings(self):
        (self.root / '.zoho-local.json').write_text(json.dumps(
            {'organization_id': '1', 'country_code': '27', 'vat_registered': False, 'test_invoice_ids': [5]}))
        (self.root / 'tenant.json').write_text(json.dumps(
            {'name': 'RG Midrand', 'owner_name': 'Roland', 'owner_number': '0837758811', 'payment_terms_days': 14}))
        tenant = load_tenant(self.root, slug='rg-midrand')
        self.assertEqual((tenant.slug, tenant.name, tenant.owner_number, tenant.organization_id),
                         ('rg-midrand', 'RG Midrand', '+27837758811', '1'))
        self.assertEqual((tenant.payment_terms_days, tenant.vat_registered, tenant.test_invoice_ids), (14, False, ('5',)))
        self.assertEqual(read_settings(self.root)['name'], 'RG Midrand')

    def test_validation(self):
        for bad in ({'currency': 'rand'}, {'timezone': 'Mars/Base'}, {'payment_terms_days': -1},
                    {'features': ['invoicing', 'telepathy']}, {'owner_number': 'call me'}, {'country_code': '0'}):
            with self.assertRaises(ValueError, msg=bad):
                Tenant.from_settings(bad)
        (self.root / 'tenant.json').write_text('{broken')
        with self.assertRaisesRegex(ValueError, 'not valid JSON'):
            load_tenant(self.root)

    def test_feature_switches(self):
        tenant = Tenant.from_settings({'features': ['invoicing'], 'name': 'Shop'})
        self.assertFalse(tenant.has('forecast'))
        with self.assertRaisesRegex(ValueError, 'forecast feature is not enabled for Shop'):
            tenant.require('forecast')

    def test_slugs(self):
        self.assertEqual(tenant_dir('rg-midrand').name, 'rg-midrand')
        for bad in ('RG', '../x', '-a', 'a_b', ''):
            with self.assertRaises(ValueError):
                tenant_dir(bad)


class TenantWorkflowTests(unittest.TestCase):
    """Currency, payment terms and owner name come from the tenant."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / '.zoho-local.json'
        self.config.write_text(json.dumps({'organization_id': '782228241', 'country_code': '27'}))
        self.patch = patch('hustleai.workflows.service.CONFIG', self.config)
        self.patch.start()
        (self.root / 'zoho-client-phone-map.md').write_text('Organization: 782228241\n| 1 | +27837758811 |\n')

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def service(self, **tenant):
        (self.root / 'tenant.json').write_text(json.dumps(tenant))
        return Service(FakeAPI(), self.root)

    def test_terms_currency_and_owner_name(self):
        s = self.service(owner_name='Thandi', payment_terms_days=30)
        try:
            proposal = s.prepare_invoice('0837758811', [{'item_id': '4', 'rate': '115'}], '2026-09-26')
            self.assertEqual(proposal['preview']['invoice']['due_date'], '2026-10-26')
            self.assertEqual(proposal['preview']['invoice']['payment_terms'], 30)
            self.assertIn('Ask Thandi to reply CONFIRM', proposal['confirmation_required'])
        finally:
            s.close()

    def test_other_currency_tenant_rejects_zar_clients(self):
        s = self.service(currency='USD')
        try:
            with self.assertRaisesRegex(ValueError, 'Client currency must be USD'):
                s.prepare_invoice('0837758811', [{'item_id': '4', 'rate': '115'}], '2026-09-26')
        finally:
            s.close()

    def test_missing_settings_is_a_clear_error(self):
        self.config.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'No tenant settings'):
            Service(FakeAPI(), self.root)


if __name__ == '__main__':
    unittest.main()
