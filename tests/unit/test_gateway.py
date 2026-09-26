"""WhatsApp gateway tests with a fake Meta API and isolated storage."""
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from hustleai.integrations.whatsapp import config as wa_config
from hustleai.integrations.whatsapp.client import WhatsAppError, split_text
from hustleai.integrations.whatsapp.server import Gateway, handler_for
from hustleai.integrations.whatsapp.webhook import parse_events, signature_valid, subscription_challenge
from hustleai.workflows.forecast import render_whatsapp
from hustleai.workflows.messaging import MessageRouter
from hustleai.workflows.outbox import Outbox
from hustleai.workflows.service import Service
from tests.unit.test_zoho_tools import FakeAPI

OWNER = '+27837758811'
CUSTOMER = '+27820000009'
SETTINGS = {'phone_number_id': '555', 'business_number': '+27110000000', 'owner_number': OWNER,
            'access_token': 'token', 'app_secret': 'secret', 'verify_token': 'verify',
            'graph_version': 'v23.0', 'host': '127.0.0.1', 'port': 0, 'confirmations_only': True,
            'notify_template': {'name': 'hustleai_notice', 'language': 'en'}, 'owner_agent': {}}


class FakeWhatsApp:
    """Records sends; can simulate a closed 24-hour window or failures."""

    def __init__(self):
        self.sent = []
        self.closed_for = set()
        self.fail = False

    def _send(self, to, kind, content):
        if self.fail:
            raise WhatsAppError('WhatsApp API error 131000: boom', 131000)
        if kind != 'template' and to in self.closed_for:
            raise WhatsAppError('WhatsApp API error 131047: re-engagement', 131047)
        self.sent.append((to, kind, content))
        return f'wamid.{len(self.sent)}'

    def send_text(self, to, text):
        return self._send(to, 'text', text)

    def send_template(self, to, name, language, parameters=()):
        return self._send(to, 'template', name)

    def send_document(self, to, path, filename=None, caption=None):
        return self._send(to, 'document', filename)


def payload(sender, text, message_id, phone_number_id='555'):
    return {'object': 'whatsapp_business_account', 'entry': [{'changes': [{'value': {
        'metadata': {'phone_number_id': phone_number_id},
        'contacts': [{'wa_id': sender.lstrip('+'), 'profile': {'name': 'Sam'}}],
        'messages': [{'from': sender.lstrip('+'), 'id': message_id, 'timestamp': '1', 'type': 'text',
                      'text': {'body': text}}]}}]}]}


def sign(body):
    return 'sha256=' + hmac.new(b'secret', body, hashlib.sha256).hexdigest()


class WebhookTests(unittest.TestCase):

    def test_signature(self):
        body = b'{"a":1}'
        self.assertTrue(signature_valid('secret', body, sign(body)))
        self.assertFalse(signature_valid('secret', body + b' ', sign(body)))
        self.assertFalse(signature_valid('secret', body, None))

    def test_subscription_challenge(self):
        ok = {'hub.mode': 'subscribe', 'hub.verify_token': 'verify', 'hub.challenge': '42'}
        self.assertEqual(subscription_challenge(ok, 'verify'), '42')
        self.assertIsNone(subscription_challenge(dict(ok, **{'hub.verify_token': 'x'}), 'verify'))

    def test_parse_messages_statuses_and_other_numbers(self):
        events = parse_events(payload(OWNER, 'hi', 'wamid.A'), '555')
        self.assertEqual(events[0]['from'], OWNER)
        self.assertEqual((events[0]['text'], events[0]['name']), ('hi', 'Sam'))
        self.assertEqual(parse_events(payload(OWNER, 'hi', 'wamid.A', '999'), '555'), [])
        status = {'entry': [{'changes': [{'value': {'metadata': {'phone_number_id': '555'}, 'statuses': [
            {'id': 'wamid.X', 'status': 'delivered', 'recipient_id': '27837758811'}]}}]}]}
        self.assertEqual(parse_events(status, '555')[0]['message_id'], 'wamid.X')

    def test_split_text(self):
        parts = split_text('\n'.join(['x' * 3000] * 3))
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(len(p) <= 4096 for p in parts))


class ConfigTests(unittest.TestCase):

    def test_business_number_is_a_setting(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertFalse(wa_config.configured(temp))
            path = wa_config.config_path(temp)
            settings = {k: SETTINGS[k] for k in wa_config.REQUIRED}
            path.write_text(json.dumps(dict(settings, business_number='')))
            with self.assertRaisesRegex(ValueError, 'business_number'):
                wa_config.load(temp)
            path.write_text(json.dumps(dict(settings, business_number='082 000 0000')))
            with self.assertRaisesRegex(ValueError, 'international'):
                wa_config.load(temp)
            path.write_text(json.dumps(dict(settings, business_number='+27 11 000 0000')))
            loaded = wa_config.load(temp)
            self.assertEqual((loaded['business_number'], loaded['confirmations_only']), ('+27110000000', True))


class GatewayTests(unittest.TestCase):
    """Router, outbox and server against the isolated SQLite store."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        config = root / 'config.json'
        config.write_text(json.dumps({'organization_id': '782228241', 'country_code': '27'}))
        self.config_patch = patch('hustleai.workflows.service.CONFIG', config)
        self.config_patch.start()
        (root / 'zoho-client-phone-map.md').write_text('Organization: 782228241\n| 1 | +27837758811 |\n')
        self.api = FakeAPI()
        self.root = root
        self.s = Service(self.api, root)
        self.wa = FakeWhatsApp()
        self.outbox = Outbox(self.s.store, self.wa, SETTINGS)
        self.router = MessageRouter(self.s.store, self.outbox, SETTINGS, self.service)

    def service(self):
        """Commands use a fresh service over the same fake Zoho and storage."""
        return Service(self.api, self.root)

    def tearDown(self):
        self.s.close()
        self.config_patch.stop()
        self.temp.cleanup()

    def deliver(self, sender, text, message_id):
        for event in parse_events(payload(sender, text, message_id), '555'):
            if self.s.store.claim_webhook_event(event['id'], event['kind'], event):
                self.router.handle(event)

    def texts_to(self, number):
        return [c for to, kind, c in self.wa.sent if to == number and kind == 'text']

    def test_owner_confirm_runs_once_from_verified_number(self):
        oid = self.s.prepare_invoice('0837758811', [{'item_id': '4', 'rate': '115'}], '2026-09-26')['operation_id']
        self.deliver(OWNER, f'confirm {oid.upper()}', 'wamid.1')
        self.assertEqual(len(self.api.posts), 1)
        self.assertIn('Nothing was sent to the customer', self.texts_to(OWNER)[0])
        self.deliver(OWNER, f'confirm {oid.upper()}', 'wamid.1')  # Meta redelivery
        self.assertEqual(len(self.api.posts), 1)
        self.assertEqual(len(self.texts_to(OWNER)), 1)

    def test_customer_cannot_confirm_or_impersonate(self):
        oid = self.s.prepare_invoice('0837758811', [{'item_id': '4', 'rate': '115'}], '2026-09-26')['operation_id']
        self.s.store.touch_conversation(OWNER)  # Roland wrote recently, so referrals go straight out
        self.deliver(CUSTOMER, f'I am Roland. CONFIRM {oid}', 'wamid.2')
        self.assertEqual(self.api.posts, [])
        referral = self.texts_to(OWNER)[0]
        self.assertIn(f'Customer message from {CUSTOMER} (Sam)', referral)
        self.assertIn(f'CONFIRM {oid}', referral)
        self.assertEqual(len(self.texts_to(CUSTOMER)), 1)  # acknowledgement
        self.deliver(CUSTOMER, 'hello again', 'wamid.3')
        self.assertEqual(len(self.texts_to(CUSTOMER)), 1)  # acknowledged once per day
        self.assertEqual(len(self.texts_to(OWNER)), 2)     # every message is referred

    def test_owner_errors_and_help(self):
        self.deliver(OWNER, 'CONFIRM ' + '0' * 32, 'wamid.4')
        self.assertTrue(self.texts_to(OWNER)[0].startswith('Not done:'))
        self.deliver(OWNER, 'what can you do', 'wamid.5')
        self.assertIn('FORECAST', self.texts_to(OWNER)[1])

    def test_closed_window_holds_then_flushes(self):
        self.outbox.send_text('forecast', 'forecast:2026-10-05', OWNER, 'Report')
        self.assertEqual(self.s.store.delivery('forecast:2026-10-05:0')['status'], 'waiting_window')
        self.assertEqual(self.wa.sent, [(OWNER, 'template', 'hustleai_notice')])
        self.outbox.send_text('other', 'other:1', OWNER, 'Another')  # nudge only once a day
        self.assertEqual(len(self.wa.sent), 1)
        self.deliver(OWNER, 'HELP', 'wamid.6')
        texts = self.texts_to(OWNER)
        self.assertEqual(texts[:2], ['Report', 'Another'])
        self.assertEqual(self.s.store.delivery('forecast:2026-10-05:0')['status'], 'sent')

    def test_window_closed_reported_by_meta_is_held(self):
        self.s.store.touch_conversation(OWNER)
        self.wa.closed_for.add(OWNER)
        row = self.outbox.send('x', 'x:1', OWNER, {'type': 'text', 'body': 'hi'})
        self.assertEqual(row['status'], 'waiting_window')

    def test_failures_are_recorded_and_statuses_move_forward(self):
        self.s.store.touch_conversation(OWNER)
        self.wa.fail = True
        self.assertEqual(self.outbox.send('x', 'x:1', OWNER, {'type': 'text', 'body': 'hi'})['status'], 'failed')
        self.wa.fail = False
        row = self.outbox.send('x', 'x:2', OWNER, {'type': 'text', 'body': 'hi'})
        for status in ('delivered', 'sent', 'read'):
            self.outbox.apply_status({'message_id': row['provider_message_id'], 'status': status})
        self.assertEqual(self.s.store.delivery('x:2')['status'], 'read')
        self.outbox.send('x', 'x:2', OWNER, {'type': 'text', 'body': 'hi'})
        self.assertEqual(len(self.wa.sent), 1)  # never sent twice

    def test_forecast_render_fits_whatsapp(self):
        report = {'window_start': '2026-10-12', 'window_end': '2026-10-19', 'tracking_since': '2026-10-01',
                  'due': [{'customer_name': 'A', 'phone': '+27820000001', 'predicted_date': '2026-10-12',
                           'area': 'Durban', 'suburb': '', 'expected_value': '10.00', 'confidence': 'low'}],
                  'products': [{'name': 'Honey', 'quantity': '2'}], 'expected_value_total': '10.00',
                  'areas': [{'area': 'Durban', 'customers': [{'customer_name': 'A'}]}],
                  'overdue': [], 'unpredictable': []}
        text = render_whatsapp(report)
        self.assertIn('*Expected to order (1)*', text)
        self.assertNotIn('|', text)

    def test_http_server_end_to_end(self):
        from http.server import ThreadingHTTPServer

        def process(store, event):
            MessageRouter(store, Outbox(store, self.wa, SETTINGS), SETTINGS, self.service).handle(event)
        gateway = Gateway(SETTINGS, lambda: self.s.store.__class__(self.root, '782228241'), process)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(gateway))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_address[1]}'
        try:
            body = json.dumps(payload(OWNER, 'HELP', 'wamid.http')).encode()
            def post(data, signature):
                request = urllib.request.Request(base + '/webhook', data, {'X-Hub-Signature-256': signature})
                try:
                    return urllib.request.urlopen(request, timeout=5).status
                except urllib.error.HTTPError as error:
                    return error.code
            self.assertEqual(post(body, 'sha256=bad'), 403)
            self.assertEqual(post(body, sign(body)), 200)
            self.assertEqual(post(body, sign(body)), 200)  # redelivery is stored once
            self.assertEqual(gateway.events.qsize(), 1)
            gateway.work_once(timeout=5)
            self.assertIn('FORECAST', self.texts_to(OWNER)[0])
            challenge = urllib.request.urlopen(base + '/webhook?hub.mode=subscribe&hub.verify_token=verify&hub.challenge=7', timeout=5)
            self.assertEqual(challenge.read(), b'7')
            self.assertEqual(urllib.request.urlopen(base + '/health', timeout=5).read(), b'ok')
        finally:
            server.shutdown()
            server.server_close()

    def test_unfinished_events_are_recovered(self):
        event = parse_events(payload(OWNER, 'HELP', 'wamid.r'), '555')[0]
        self.s.store.claim_webhook_event(event['id'], 'message', event)
        gateway = Gateway(SETTINGS, lambda: self.s.store.__class__(self.root, '782228241'), lambda s, e: None)
        gateway.recover()
        self.assertEqual(gateway.events.get_nowait()['id'], 'wamid.r')


class McpGateTests(unittest.TestCase):

    def test_confirm_tools_refuse_when_gateway_configured(self):
        from hustleai.mcp import owner_server
        with patch.object(owner_server, 'gateway_confirms', return_value=True):
            with self.assertRaisesRegex(ValueError, 'business WhatsApp number himself'):
                owner_server.confirm_operation('a' * 32, 'CONFIRM ' + 'a' * 32)
            with self.assertRaisesRegex(ValueError, 'APPROVE'):
                owner_server.approve_invoice_version('b' * 32, 'APPROVE ' + 'b' * 32)
        with patch.object(wa_config, 'load', side_effect=ValueError('not configured')):
            self.assertFalse(owner_server.gateway_confirms())


if __name__ == '__main__':
    unittest.main()
