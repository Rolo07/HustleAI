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
