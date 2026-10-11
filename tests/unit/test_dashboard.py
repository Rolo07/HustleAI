"""Owner dashboard safety tests using its actual engine and HTTP handler."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from hustleai.dashboard.adapters import DemoBusiness
from hustleai.dashboard.engine import Engine, conversation, public_order
from hustleai.dashboard.server import handler
from hustleai.dashboard.storage import Journal
from hustleai.integrations.whatsapp.test_assistant import PHONE_ID, RECIPIENT
from tests.unit.test_gateway import FakeWhatsApp, payload, sign, SETTINGS


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = Journal(Path(self.temp.name), demo=True)
        self.business = DemoBusiness()
        self.engine = Engine(self.journal, self.business, demo=True)
        self.engine.seed()
        self.cid = '+27820000002'

    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    def action(self, name, data=None, key=None):
        import uuid
        self.engine.action(self.cid, name, data or {}, key or uuid.uuid4().hex)

    def c(self):
        return self.journal.read()['conversations'][self.cid]

    def confirm(self, event_id='confirmed'):
        self.engine.demo_inbound(self.cid, 'CONFIRM ' + self.c()['order']['version'], event_id)
        self.engine.tick()

    def test_full_journey_and_repeated_approvals_send_once(self):
        self.confirm()
        c = self.c()
        self.assertEqual(c['order']['status'], 'needs_approval')
        self.assertEqual(c['order']['confirmed_version'], c['order']['version'])
        self.assertEqual(c['order']['currency'], 'ZAR')
        version = c['order']['version']
        self.action('approve', {'version': version})
        self.action('approve', {'version': version})
        self.engine.tick()
        self.assertEqual(self.c()['order']['status'], 'sent')
        docs = [r for r in self.journal.read()['outbox'].values() if r['kind'] == 'document']
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]['state'], 'api_accepted')

    def test_customer_confirmation_required_before_approval(self):
        with self.assertRaises(ValueError):
            self.action('approve', {'version': self.c()['order']['version']})
        self.assertFalse(self.journal.read()['outbox'])

    def test_stale_confirmation_and_edit_invalidate(self):
        old = self.c()['order']['version']
        self.action('edit', {'version': old, 'lines': [{'item_id': 'chicken', 'quantity': '8'}]})
        new = self.c()['order']
        self.assertNotEqual(old, new['version'])
        self.assertEqual(new['total'], '256.00')
        self.assertIsNone(new['confirmed_version'])
        self.engine.demo_inbound(self.cid, 'CONFIRM ' + old, 'stale')
        self.engine.tick()
        self.assertIsNone(self.c()['order'].get('invoice_id'))
        self.assertEqual(self.c()['order']['status'], 'awaiting_customer')

    def test_edit_after_approval_cancels_old_pdf(self):
        self.confirm()
        old = self.c()['order']['version']
        self.action('approve', {'version': old})
        self.action('edit', {'version': old, 'lines': [{'item_id': 'beef', 'quantity': '2'}]})
        self.engine.tick()
        rows = [r for r in self.journal.read()['outbox'].values() if r['kind'] == 'document']
        self.assertEqual(rows[0]['state'], 'cancelled')
        self.assertIsNone(self.c()['order']['approved_version'])
        self.assertEqual(self.c()['order']['status'], 'awaiting_customer')

    def test_duplicate_confirmation_creates_one_draft(self):
        with patch.object(self.business, 'draft', wraps=self.business.draft) as draft:
            self.confirm('same-event')
            self.confirm('same-event')
            self.confirm('different-event-same-version')
            self.assertEqual(draft.call_count, 1)

    def test_takeover_cancels_queued_hermes_and_requires_explicit_resume(self):
        self.action('send_proposal')
        self.action('takeover')
        self.engine.tick()
        rows = list(self.journal.read()['outbox'].values())
        self.assertEqual(rows[0]['state'], 'cancelled')
        self.engine.demo_inbound(self.cid, 'Please reorder', 'human-message')
        self.engine.tick()
        self.assertEqual(self.c()['mode'], 'human')
        self.action('reply', {'text': 'Roland here.'})
        self.engine.tick()
        self.assertEqual(self.c()['messages'][-1]['status'], 'api_accepted')
        self.action('resume')
        self.assertEqual(self.c()['mode'], 'hermes')

    def test_manual_reply_requires_takeover_and_note_never_queues(self):
        with self.assertRaises(ValueError):
            self.action('reply', {'text': 'Hello'})
        self.action('note', {'text': 'Private note'})
        self.assertFalse(self.journal.read()['outbox'])

    def test_hermes_generation_race_is_discarded(self):
        started, finish = threading.Event(), threading.Event()
        def agent(phone, text):
            started.set()
            finish.wait(2)
            return 'Proposed reply'
        self.business.agent = agent
        self.engine.demo_inbound(self.cid, 'Can you help?', 'race')
        thread = threading.Thread(target=self.engine.handle_event, args=('race',))
        thread.start()
        self.assertTrue(started.wait(2))
        self.action('takeover')
        finish.set()
        thread.join(3)
        self.assertIsNone(self.c()['suggestion'])
        self.assertFalse(self.journal.read()['outbox'])

    def test_rejection_keeps_draft_unsent_and_records_reason(self):
        self.confirm()
        self.action('reject', {'version': self.c()['order']['version'], 'text': 'Collection must be confirmed.'})
        self.assertEqual(self.c()['order']['status'], 'rejected')
        self.assertFalse([x for x in self.journal.read()['outbox'].values() if x['kind'] == 'document'])
        self.assertIn('Collection', self.journal.read()['activity'][-1]['detail'])

    def test_live_recipient_scope_signatures_and_duplicate_webhooks(self):
        self.engine.demo = False
        self.engine.settings = dict(SETTINGS, phone_number_id=PHONE_ID)
        raw = json.dumps(dict(payload(RECIPIENT, 'hello', 'live-id', PHONE_ID), object='whatsapp_business_account')).encode()
        self.assertEqual(self.engine.accept(raw, 'sha256=bad'), 403)
        self.assertEqual(self.engine.accept(raw, sign(raw)), 200)
        self.assertEqual(self.engine.accept(raw, sign(raw)), 200)
        self.assertEqual(len(self.journal.read()['events']), 1)
        other = json.dumps(dict(payload('+27820000009', 'hello', 'other', PHONE_ID), object='whatsapp_business_account')).encode()
        self.engine.accept(other, sign(other))
        self.assertEqual(len(self.journal.read()['events']), 1)

    def test_uncertain_send_not_retried_after_restart(self):
        self.engine.demo = False
        client = FakeWhatsApp()
        client.fail = True
        self.engine.client = client
        with self.journal.locked():
            state = self.journal.read()
            c = conversation(state, RECIPIENT)
            c['last_inbound'] = time.time()
            self.engine.queue(state, c, 'approved reply', actor='owner', key='uncertain')
            self.journal.write(state)
        self.engine.dispatch('uncertain')
        self.assertEqual(self.journal.read()['outbox']['uncertain']['state'], 'failed')
        client.fail = False
        self.engine.recover()
        self.engine.tick()
        self.assertEqual(client.sent, [])

    def test_interrupted_create_and_send_recovery(self):
        with self.journal.locked():
            state = self.journal.read()
            c = state['conversations'][self.cid]
            c['order']['status'] = 'draft_creating'
            self.engine.queue(state, c, 'reply', key='crashed')
            state['outbox']['crashed']['state'] = 'sending'
            self.journal.write(state)
        self.engine.recover()
        self.assertEqual(self.c()['order']['status'], 'draft_uncertain')
        self.assertEqual(self.journal.read()['outbox']['crashed']['state'], 'failed')

    def test_idempotency_key_content_binding(self):
        self.action('note', {'text': 'one'}, 'same-key-123')
        self.action('note', {'text': 'one'}, 'same-key-123')
        with self.assertRaises(ValueError):
            self.action('note', {'text': 'different'}, 'same-key-123')
        self.assertEqual(len([m for m in self.c()['messages'] if m['text'] == 'one']), 1)

    def test_delivery_status_monotonic_and_correlated(self):
        self.action('takeover')
        self.action('reply', {'text': 'Hello'})
        self.engine.tick()
        outbound = next(iter(self.journal.read()['outbox'].values()))
        with self.journal.locked():
            state = self.journal.read()
            for status in ('delivered', 'sent', 'read', 'failed'):
                state['events'][status] = {'state': 'queued', 'event': {'kind': 'status', 'message_id': outbound['provider_id'], 'status': status}}
            self.journal.write(state)
        self.engine.tick()
        self.assertEqual(self.c()['messages'][-1]['status'], 'read')

    def test_live_http_auth_and_csrf(self):
        self.engine.demo = False
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.engine, 'a-secure-owner-password'))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        def req(path, data=None, headers=None):
            try:
                request = urllib.request.Request(base + path, json.dumps(data).encode() if data else None, headers or {})
                with urllib.request.urlopen(request, timeout=3) as r:
                    return r.status, r.headers, r.read()
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, error.headers, error.read()
        try:
            self.assertEqual(req('/api/state')[0], 401)
            self.assertEqual(req('/api/login', {'password': 'a-secure-owner-password'})[0], 403)
            status, headers, _ = req('/api/login', {'password': 'a-secure-owner-password'}, {'Origin': base})
            self.assertEqual(status, 200)
            cookie = headers['Set-Cookie'].split(';')[0]
            self.assertIn('HttpOnly', headers['Set-Cookie'])
            self.assertEqual(req('/api/state', headers={'Cookie': cookie})[0], 200)
            status, _, body = req('/api/state', headers={'Cookie': cookie})
            self.assertNotIn(b'pdf_path', body)
            self.assertEqual(req('/api/demo/inbound', {'conversation': self.cid, 'text': 'hello'}, {'Origin': base, 'Cookie': cookie})[0], 409)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
