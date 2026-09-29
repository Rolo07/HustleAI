"""Multi-tenant registry, gateway routing, jobs, import and Hermes install."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from hustleai.cli import tenant as tenant_cli
from hustleai.integrations.whatsapp.owner_agent import OwnerAgent
from hustleai.integrations.whatsapp.server import Gateway, Route
from hustleai.tenants import list_tenants, tenant_by_slug
from tests.unit.test_gateway import payload, sign


def make_tenant(root, slug, mode=0o700, **settings):
    folder = Path(root) / slug
    folder.mkdir(mode=mode)
    os.chmod(folder, mode)
    base = {'name': slug.upper(), 'owner_name': 'Owner ' + slug, 'owner_number': '+2782000000' + str(len(slug) % 10),
            'organization_id': '1' + str(len(slug)), 'features': ['invoicing', 'forecast', 'orders_sync', 'whatsapp']}
    (folder / 'tenant.json').write_text(json.dumps({**base, **settings}))
    return folder


class RegistryTests(unittest.TestCase):

    def test_only_private_folders_with_settings_are_used(self):
        with tempfile.TemporaryDirectory() as root:
            make_tenant(root, 'good')
            make_tenant(root, 'open', mode=0o755)
            (Path(root) / 'empty').mkdir(mode=0o700)
            os.symlink(Path(root) / 'good', Path(root) / 'alias')
            tenants, skipped = list_tenants(root)
            self.assertEqual(list(tenants), ['good'])
            self.assertEqual(tenants['good'][0].slug, 'good')
            self.assertIn('chmod 700', skipped['open'])
            self.assertIn('tenant.json', skipped['empty'])
            self.assertIn('not a real directory', skipped['alias'])
            with self.assertRaisesRegex(ValueError, 'open'):
                tenant_by_slug('open', root)


class MultiTenantGatewayTests(unittest.TestCase):
    """Each tenant is verified with its own secret and phone number ID."""

    class Store:
        def __init__(self, claimed):
            self.claimed = claimed
        def claim_webhook_event(self, event_id, kind, event):
            if event_id in self.claimed:
                return False
            self.claimed.add(event_id)
            return True
        def unprocessed_webhook_events(self):
            return []
        def close(self):
            pass

    def setUp(self):
        self.claimed = {'a': set(), 'b': set()}
        self.handled = []
        def route(slug, secret, phone_id):
            settings = {'app_secret': secret, 'phone_number_id': phone_id, 'verify_token': 'v-' + slug}
            return Route(settings, lambda: self.Store(self.claimed[slug]),
                         lambda store, event, slug=slug: self.handled.append((slug, event['text'])))
        self.gateway = Gateway(routes={'a': route('a', 'secret', '555'), 'b': route('b', 'other', '666')})

    def test_routes_and_isolation(self):
        body_a = json.dumps(payload('+27837758811', 'hello a', 'wamid.a', '555')).encode()
        self.assertEqual(self.gateway.accept(body_a, sign(body_a), 'a'), 200)
        self.assertEqual(self.gateway.accept(body_a, sign(body_a), 'b'), 403)   # a's signature at b's route
        self.assertEqual(self.gateway.accept(body_a, sign(body_a), 'zzz'), 404)  # unknown tenant
        self.assertEqual(self.gateway.accept(body_a, sign(body_a), None), 404)   # no single-tenant route
        import hmac, hashlib
        body_b = json.dumps(payload('+27837758811', 'wrong number', 'wamid.b', '555')).encode()
        signature_b = 'sha256=' + hmac.new(b'other', body_b, hashlib.sha256).hexdigest()
        self.assertEqual(self.gateway.accept(body_b, signature_b, 'b'), 200)     # valid, but for a's number
        self.assertEqual(self.gateway.events.qsize(), 1)
        self.gateway.work_once(timeout=1)
        self.assertEqual(self.handled, [('a', 'hello a')])
        self.assertEqual(self.gateway.challenge('b', {'hub.mode': 'subscribe', 'hub.verify_token': 'v-b',
                                                      'hub.challenge': '9'}), '9')
        self.assertIsNone(self.gateway.challenge('a', {'hub.mode': 'subscribe', 'hub.verify_token': 'v-b',
                                                       'hub.challenge': '9'}))


class OwnerAgentTests(unittest.TestCase):

    def test_hermes_session_key_and_bearer(self):
        seen = {}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, *a): return json.dumps({'choices': [{'message': {'content': 'Hi'}}]}).encode()
        def urlopen(request, timeout=None):
            seen.update(dict(request.header_items()), url=request.full_url)
            return Response()
        agent = OwnerAgent({'url': 'http://127.0.0.1:8642/p/rg-midrand/v1/chat/completions', 'api_key': 'sk-x'},
                           session_key='whatsapp:+27837758811')
        with patch('urllib.request.urlopen', urlopen):
            self.assertEqual(agent.ask('hello'), 'Hi')
        self.assertEqual(seen['X-hermes-session-key'], 'whatsapp:+27837758811')
        self.assertEqual(seen['Authorization'], 'Bearer sk-x')


class TenantCommandTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'tenants'
        self.patches = [patch('hustleai.config.TENANTS_ROOT', self.root), patch.object(tenant_cli, 'TENANTS_ROOT', self.root)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def args(self, **values):
        defaults = dict(name=None, owner_name=None, owner_number=None, organization_id=None, country_code=None,
                        currency=None, timezone=None, payment_terms=None, vat_registered=None, features=None)
        return type('Args', (), {**defaults, **values})()

    def test_create_without_database(self):
        tenant_cli.create_command(self.args(slug='shop-one', name='Shop One', owner_name='Thandi',
                                            owner_number='+27821112222', organization_id='42', currency='usd',
                                            no_database=True, admin_root=None))
        folder = self.root / 'shop-one'
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        saved = json.loads((folder / 'tenant.json').read_text())
        self.assertEqual((saved['currency'], saved['storage_backend']), ('usd', 'postgres'))
        self.assertEqual(tenant_by_slug('shop-one')[0].currency, 'USD')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            tenant_cli.create_command(self.args(slug='shop-one', name='x', owner_name='x', owner_number='+27821112222',
                                                organization_id='42', no_database=True, admin_root=None))
        with self.assertRaisesRegex(ValueError, 'owner-number'):
            tenant_cli.create_command(self.args(slug='shop-two', name='x', owner_name='x', organization_id='43',
                                                no_database=True, admin_root=None))

    def test_import_legacy_copies_without_changing_source(self):
        source = Path(self.temp.name) / 'old'
        source.mkdir()
        (source / '.zoho-local.json').write_text(json.dumps(
            {'organization_id': '782228241', 'country_code': '27', 'vat_registered': False}))
        (source / '.zoho-credentials.json').write_text('{"secret": true}')
        (source / 'invoice-pdfs').mkdir()
        (source / 'invoice-pdfs' / '1.pdf').write_bytes(b'%PDF-1.4')
        before = sorted(p.name for p in source.rglob('*'))
        tenant_cli.import_legacy_command(self.args(slug='rg-midrand', source=str(source), name='RG Midrand',
                                                   owner_name='Roland', owner_number='+27837758811'))
        folder = self.root / 'rg-midrand'
        tenant = tenant_by_slug('rg-midrand')[0]
        self.assertEqual((tenant.organization_id, tenant.vat_registered, tenant.owner_name), ('782228241', False, 'Roland'))
        self.assertEqual(stat.S_IMODE((folder / '.zoho-credentials.json').stat().st_mode), 0o600)
        self.assertTrue((folder / 'invoice-pdfs' / '1.pdf').is_file())
        self.assertEqual(sorted(p.name for p in source.rglob('*')), before)
        self.assertNotIn('storage_backend', json.loads((source / '.zoho-local.json').read_text()))

    def test_run_job_is_silent_on_success_and_logs(self):
        self.root.mkdir(mode=0o700)
        folder = make_tenant(self.root, 'shop', features=['invoicing', 'orders_sync'])
        ok = subprocess.CompletedProcess([], 0, stdout='654 invoices listed\n', stderr='')
        with patch('subprocess.run', return_value=ok) as run:
            self.assertEqual(tenant_cli.run_job('shop', 'orders-sync'), 0)
        self.assertEqual(run.call_args.kwargs['env']['HUSTLEAI_DATA_DIR'], str(folder))
        self.assertIn('orders-sync exit=0 654 invoices listed', (folder / 'reports' / 'jobs.log').read_text())
        with patch('subprocess.run') as run:
            self.assertEqual(tenant_cli.run_job('shop', 'forecast'), 0)  # feature off: nothing runs
            run.assert_not_called()
        failed = subprocess.CompletedProcess([], 1, stdout='', stderr='Supabase connection failed.\n')
        with patch('subprocess.run', return_value=failed):
            self.assertEqual(tenant_cli.run_job('shop', 'orders-sync'), 1)

    def test_hermes_install_with_fake_hermes(self):
        self.root.mkdir(mode=0o700)
        folder = make_tenant(self.root, 'rg-midrand', name='RG Midrand', owner_name='Roland', timezone='Africa/Johannesburg')
        (folder / '.whatsapp.json').write_text('{"phone_number_id": "1"}')
        hermes_root = Path(self.temp.name) / 'hermes'
        calls = Path(self.temp.name) / 'calls.log'
        fake = Path(self.temp.name) / 'hermes-fake'
        fake.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n'
                        f'if [ "$1" = profile ]; then mkdir -p {hermes_root}/profiles/$3; fi\n')
        fake.chmod(0o755)
        dry = tenant_cli.hermes_install('rg-midrand', hermes=str(fake), hermes_root=hermes_root, dry_run=True, log=lambda *_: None)
        self.assertFalse(calls.exists())
        self.assertFalse(hermes_root.exists())
        self.assertEqual(dry[0][1:], ['profile', 'create', 'rg-midrand'])
        tenant_cli.hermes_install('rg-midrand', hermes=str(fake), hermes_root=hermes_root, log=lambda *_: None)
        profile = hermes_root / 'profiles' / 'rg-midrand'
        self.assertIn('Roland at RG Midrand', (profile / 'SOUL.md').read_text())
        config = yaml.safe_load((profile / 'config.yaml').read_text())
        self.assertEqual(config['mcp_servers']['hustleai']['env']['HUSTLEAI_DATA_DIR'], str(folder))
        self.assertTrue({'terminal', 'file', 'code_execution'} <= set(config['agent']['disabled_toolsets']))
        env = tenant_cli.read_env(profile / '.env')
        self.assertEqual((env['API_SERVER_ENABLED'], env['HERMES_TIMEZONE']), ('true', 'Africa/Johannesburg'))
        agent = json.loads((folder / '.whatsapp.json').read_text())['owner_agent']
        self.assertEqual((agent['url'], agent['api_key']),
                         ('http://127.0.0.1:8642/p/rg-midrand/v1/chat/completions', env['API_SERVER_KEY']))
        plugin = hermes_root / 'plugins' / 'hustleai'
        self.assertTrue(plugin.is_symlink() and (plugin / 'plugin.yaml').is_file())
        script = profile / 'scripts' / 'hustleai-orders-sync.sh'
        self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o700)
        self.assertIn('run-job rg-midrand orders-sync', script.read_text())
        first = calls.read_text().splitlines()
        self.assertEqual(sum('cron create' in c for c in first), 2)
        tenant_cli.hermes_install('rg-midrand', hermes=str(fake), hermes_root=hermes_root, log=lambda *_: None)
        again = calls.read_text().splitlines()[len(first):]
        self.assertFalse(any('cron create' in c or 'profile create' in c for c in again))  # safe to rerun
        self.assertEqual(tenant_cli.read_env(profile / '.env')['API_SERVER_KEY'], env['API_SERVER_KEY'])


if __name__ == '__main__':
    unittest.main()
