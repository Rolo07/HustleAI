"""Focused test-account HTTP, persistence and Cloud API transport tests."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from hustleai.integrations.whatsapp.client import WhatsAppClient
from hustleai.integrations.whatsapp.server import handler_for
from hustleai.integrations.whatsapp.test_assistant import (
    PHONE_ID, PREFIX, RECIPIENT, TestStore, build_test_gateway, settings_from_env)
from tests.unit.test_gateway import FakeWhatsApp, SETTINGS, payload, sign


class TestAssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.sqlite3'
        self.settings = dict(SETTINGS, phone_number_id=PHONE_ID)
        self.client = FakeWhatsApp()
        self.gateway = build_test_gateway(self.settings, self.path, self.client)

    def tearDown(self):
        self.temp.cleanup()

    def accept(self, value):
        body = json.dumps(value).encode()
        return self.gateway.accept(body, sign(body))

    def test_duplicate_survives_restart_and_text_is_removed(self):
        event = payload(RECIPIENT, 'hello', 'wamid.echo', PHONE_ID)
        self.assertEqual(self.accept(event), 200)
        self.gateway.work_once(timeout=1)
        again = build_test_gateway(self.settings, self.path, self.client)
        body = json.dumps(event).encode()
        self.assertEqual(again.accept(body, sign(body)), 200)
        self.assertEqual(again.events.qsize(), 0)
        self.assertEqual(self.client.sent, [(RECIPIENT, 'text', PREFIX + 'hello')])
        store = TestStore(self.path)
        try:
            self.assertEqual(store.db.execute('SELECT state,payload FROM events').fetchone(), ('accepted', None))
        finally:
            store.close()

    def test_other_recipient_and_unsupported_type_never_send(self):
        for value in (payload('+27820000009', 'hello', 'other', PHONE_ID),
                      payload(RECIPIENT, 'hello', 'image', PHONE_ID)):
            if value['entry'][0]['changes'][0]['value']['messages'][0]['id'] == 'image':
                value['entry'][0]['changes'][0]['value']['messages'][0]['type'] = 'image'
            self.accept(value)
            self.gateway.work_once(timeout=1)
        self.assertEqual(self.client.sent, [])

    def test_status_is_evidence_only(self):
        value = {'entry': [{'changes': [{'value': {'metadata': {'phone_number_id': PHONE_ID},
            'statuses': [{'id': 'wamid.reply', 'status': 'delivered'}]}}]}]}
        self.assertEqual(self.accept(value), 200)
        self.assertEqual(self.gateway.events.qsize(), 0)
        store = TestStore(self.path)
        try:
            self.assertEqual(store.evidence()['statuses'], {'delivered': 1})
        finally:
            store.close()
        self.assertEqual(self.client.sent, [])

    def test_remote_failure_is_not_blindly_retried(self):
        self.client.fail = True
        value = payload(RECIPIENT, 'hello', 'failed', PHONE_ID)
        self.accept(value)
        self.gateway.work_once(timeout=1)
        again = build_test_gateway(self.settings, self.path, self.client)
        again.recover()
        self.assertEqual(again.events.qsize(), 0)
        self.accept(value)
        self.assertEqual(self.gateway.events.qsize(), 0)
        store = TestStore(self.path)
        try:
            self.assertEqual(store.evidence()['events'], {'uncertain_or_failed': 1})
        finally:
            store.close()

    def test_outbound_request_construction(self):
        client = WhatsAppClient(self.settings)
        with patch.object(client, 'request', return_value={'messages': [{'id': 'wamid.out'}]}) as request:
            self.assertEqual(client.send_text(RECIPIENT, PREFIX + 'hello'), 'wamid.out')
        url, body = request.call_args.args
        self.assertEqual(url, f'https://graph.facebook.com/v23.0/{PHONE_ID}/messages')
        self.assertEqual(json.loads(body), {'messaging_product': 'whatsapp', 'recipient_type': 'individual',
            'to': RECIPIENT[1:], 'type': 'text', 'text': {'body': PREFIX + 'hello', 'preview_url': False}})

    def test_config_rejects_real_number(self):
        with patch.dict('os.environ', {'WHATSAPP_ACCESS_TOKEN': 'synthetic', 'META_APP_SECRET': 'synthetic',
                                      'WHATSAPP_VERIFY_TOKEN': 'synthetic', 'WHATSAPP_PHONE_NUMBER_ID': '999'}, clear=True):
            with self.assertRaises(ValueError):
                settings_from_env()

    def test_http_verification_signature_and_prompt_ack(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.gateway))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        def status(request):
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, error.read()
        try:
            self.assertEqual(status(base + '/health'), (200, b'ok'))
            self.assertEqual(status(base + '/webhook?hub.mode=subscribe&hub.verify_token=verify&hub.challenge=42'), (200, b'42'))
            self.assertEqual(status(base + '/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=42')[0], 403)
            body = json.dumps(payload(RECIPIENT, 'hello', 'http', PHONE_ID)).encode()
            for signature, expected in ((None, 403), ('sha256=bad', 403), (sign(body), 200)):
                headers = {'X-Hub-Signature-256': signature} if signature else {}
                self.assertEqual(status(urllib.request.Request(base + '/webhook', body, headers))[0], expected)
            self.assertEqual(self.client.sent, [])  # HTTP ack precedes remote send
            self.gateway.work_once(timeout=1)
            self.assertEqual(len(self.client.sent), 1)
        finally:
            server.shutdown()
            server.server_close()

    def test_malformed_payload_and_signature_are_rejected(self):
        for value in (None, [], {'entry': [None]}, {'entry': [{'changes': [None]}]}):
            body = json.dumps(value).encode()
            self.assertEqual(self.gateway.accept(body, sign(body)), 400)
        self.assertEqual(self.gateway.accept(b'{}', 'sha256=' + '\u00e9' * 64), 403)


if __name__ == '__main__':
    unittest.main()
