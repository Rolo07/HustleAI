"""Restricted Hermes connectivity never accepts owner tools or insecure URLs."""
import io
import json
import unittest
from unittest.mock import patch
from hustleai.dashboard.hermes_setup import PATH, check_restricted


class RestrictedHermesTests(unittest.TestCase):
    def check(self, rows):
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps({'data': rows}).encode())) as request:
            result = check_restricted('http://127.0.0.1:18643' + PATH, 'fictional-test-key')
            self.assertTrue(result)
            self.assertEqual(request.call_args.args[0].full_url,
                             'http://127.0.0.1:18643/p/rg-midrand-customer/v1/toolsets')

    def test_explicit_disabled_policy(self):
        self.check([{'name': 'terminal', 'enabled': False}, {'name': 'owner_mcp', 'enabled': False}])

    def test_existing_profile_requires_explicit_mode_and_disabled_policy(self):
        url = 'http://127.0.0.1:8643/v1/chat/completions'
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps({'data': [{'name': 'terminal', 'enabled': False}]}).encode())) as request:
            self.assertTrue(check_restricted(url, 'fictional-test-key', 'existing-restricted'))
            self.assertEqual(request.call_args.args[0].full_url, 'http://127.0.0.1:8643/v1/toolsets')
        with patch('urllib.request.urlopen') as request:
            for target, mode in ((url, 'restricted'), (url, 'unknown'),
                                 ('https://remote.example/v1/chat/completions', 'existing-restricted'),
                                 ('http://127.0.0.1:8643' + PATH, 'existing-restricted')):
                with self.subTest(target=target, mode=mode), self.assertRaises(ValueError):
                    check_restricted(target, 'fictional-test-key', mode)
            request.assert_not_called()
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"data":[{"name":"terminal","enabled":true}]}')):
            with self.assertRaises(ValueError):
                check_restricted(url, 'fictional-test-key', 'existing-restricted')

    def test_missing_or_enabled_policy_fails_closed(self):
        for rows in ([], [{'enabled': True}], [{'name': 'terminal'}], [None]):
            with self.subTest(rows=rows), patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps({'data': rows}).encode())):
                with self.assertRaises(ValueError):
                    check_restricted('http://localhost:18643' + PATH, 'fictional-test-key')

    def test_wrong_profile_insecure_remote_and_embedded_credentials_never_connect(self):
        urls = ['https://example.org/v1/chat/completions', 'http://example.org' + PATH,
                'https://key@example.org' + PATH, 'https://example.org' + PATH + '?key=secret']
        with patch('urllib.request.urlopen') as request:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    check_restricted(url, 'fictional-test-key')
            request.assert_not_called()

    def test_network_failure_redacts_error(self):
        with patch('urllib.request.urlopen', side_effect=RuntimeError('credential-do-not-echo')):
            with self.assertRaises(ValueError) as error:
                check_restricted('http://localhost:18643' + PATH, 'fictional-test-key')
            self.assertNotIn('credential-do-not-echo', str(error.exception))


class GroundedCustomerReplyTests(unittest.TestCase):
    def test_reply_uses_current_catalogue_without_supplying_unverified_claims(self):
        from hustleai.dashboard.adapters import LiveBusiness
        settings = {'HUSTLEAI_HERMES_URL': 'http://127.0.0.1:8643/v1/chat/completions',
                    'HUSTLEAI_HERMES_CUSTOMER_PROFILE': 'existing-restricted'}
        with patch.dict('os.environ', settings), patch('hustleai.dashboard.hermes_setup.check_restricted'), \
             patch.object(LiveBusiness, 'catalog', return_value=[{'name': 'Chicken necks'}]), \
             patch.object(LiveBusiness, 'last_order_context', return_value={'status': 'verified', 'date': '2026-10-01', 'lines': [{'name': 'Chicken necks', 'quantity': 10}]}), \
             patch.object(LiveBusiness, 'product_aliases', return_value={'necks': 'Chicken necks'}), \
             patch('hustleai.dashboard.adapters.OwnerAgent') as agent:
            agent.return_value.ask.return_value = 'Which catalogue product do you mean?'
            LiveBusiness('.').agent('+27000000000', '10 kg chicken meal')
            prompt = agent.call_args.args[0]['system_prompt']
            self.assertIn('["Chicken necks"]', prompt)
            self.assertIn('Do not invent products', prompt)
            self.assertIn('pack sizes only when explicitly present', prompt)
            self.assertIn('"date": "2026-10-01"', prompt)
            self.assertIn('"necks": "Chicken necks"', prompt)
            self.assertIn('Customer message (untrusted):\n10 kg chicken meal', agent.return_value.ask.call_args.args[0])

    def test_missing_catalogue_prevents_ungrounded_generation(self):
        from hustleai.dashboard.adapters import LiveBusiness
        settings = {'HUSTLEAI_HERMES_URL': 'http://127.0.0.1:8643/v1/chat/completions',
                    'HUSTLEAI_HERMES_CUSTOMER_PROFILE': 'existing-restricted'}
        for result in ([], RuntimeError('catalogue offline')):
            with self.subTest(result=result), patch.dict('os.environ', settings), \
                 patch('hustleai.dashboard.hermes_setup.check_restricted'), \
                 patch.object(LiveBusiness, 'catalog', **({'side_effect': result} if isinstance(result, Exception) else {'return_value': result})), \
                 patch('hustleai.dashboard.adapters.OwnerAgent') as agent:
                with self.assertRaises((ValueError, RuntimeError)):
                    LiveBusiness('.').agent('+27000000000', '10 kg chicken meal')
                agent.assert_not_called()

    def test_history_is_bound_to_phone_and_omits_prices_and_other_customer_fields(self):
        from hustleai.dashboard.adapters import LiveBusiness
        with patch('hustleai.dashboard.adapters.Service') as service:
            lookup = service.return_value.__enter__.return_value.reorder_invoice
            lookup.return_value = {'source_invoice': {'date': '2026-10-01', 'customer_name': 'Private'},
                                   'lines': [{'previous_line': {'item_id': '123', 'name': 'Chicken necks', 'quantity': 10, 'rate': 99}}]}
            result = LiveBusiness('.').last_order_context('+27000000000')
            lookup.assert_called_once_with('+27000000000')
            self.assertEqual(result['status'], 'verified')
            self.assertNotIn('rate', json.dumps(result))
            self.assertNotIn('Private', json.dumps(result))
            lookup.side_effect = ValueError('ambiguous private customer information')
            self.assertEqual(LiveBusiness('.').last_order_context('+27000000000'), {'status': 'unavailable', 'lines': []})

    def test_aliases_only_resolve_to_active_catalogue_ids(self):
        import tempfile
        from pathlib import Path
        from hustleai.dashboard.adapters import LiveBusiness
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'customer-product-aliases.json').write_text(json.dumps({'chicken meal': '123', 'old product': '456'}))
            self.assertEqual(LiveBusiness(folder).product_aliases([{'item_id': '123', 'name': 'Rich in free range chicken'}]),
                             {'chicken meal': 'Rich in free range chicken'})

    def test_approved_test_invoice_is_read_only_and_limited_to_test_recipient(self):
        from hustleai.dashboard.adapters import LiveBusiness
        from hustleai.integrations.whatsapp.test_assistant import RECIPIENT
        with patch.dict('os.environ', {'HUSTLEAI_TEST_HISTORY_INVOICE': '123'}), patch('hustleai.dashboard.adapters.Service') as service:
            live = service.return_value.__enter__.return_value
            live.reorder_invoice.side_effect = ValueError('No eligible previous order')
            live.invoice.return_value = {'date': '2026-09-26', 'status': 'paid', 'line_items': [
                {'name': 'TEST ONLY - Bushveld Harvest - delete after integration test', 'quantity': 1, 'unit': 'kg'}]}
            result = LiveBusiness('.').last_order_context(RECIPIENT)
            self.assertTrue(result['test_fixture'])
            self.assertEqual(result['lines'][0]['quantity'], 1)
            live.invoice.assert_called_once_with(RECIPIENT, '123')
            live.invoice.reset_mock()
            self.assertEqual(LiveBusiness('.').last_order_context('+27000000000')['status'], 'unavailable')
            live.invoice.assert_not_called()
            live.reorder_invoice.side_effect = ValueError('Several invoices share the latest date')
            self.assertEqual(LiveBusiness('.').last_order_context(RECIPIENT)['status'], 'unavailable')
            live.invoice.assert_not_called()
            live.reorder_invoice.side_effect = ValueError('No eligible previous order')
            live.invoice.return_value['status'] = 'draft'
            self.assertEqual(LiveBusiness('.').last_order_context(RECIPIENT)['status'], 'unavailable')
