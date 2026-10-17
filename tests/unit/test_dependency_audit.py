"""An unavailable registry/advisory feed must never produce a clean audit."""
from pathlib import Path
import tempfile
import unittest
from scripts.audit_dependencies import OSV, audit, canonical, osv_findings, read_lock, runtime_status


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.package = {'name': 'example-package', 'version': '1.0.0'}

    def response(self, url, data=None):
        if url == OSV:
            return {'results': [{}]}
        return {'info': {'name': 'example_package', 'version': '1.0.0'}, 'urls': [{'yanked': False}], 'vulnerabilities': []}

    def test_clean_exact_version_and_normalized_name(self):
        self.assertEqual(canonical('Example_Package'), 'example-package')
        result = audit([self.package, self.package], self.response)
        self.assertTrue(result['ok']); self.assertEqual(len(result['packages']), 1)

    def test_osv_advisory_blocks_clean_result(self):
        def request(url, data=None):
            return {'results': [{'vulns': [{'id': 'GHSA-example'}]}]} if url == OSV else self.response(url)
        self.assertFalse(audit([self.package], request)['ok'])

    def test_pypi_advisory_and_yanked_release_fail(self):
        for change in ({'vulnerabilities': [{'id': 'PYSEC-example'}]}, {'urls': [{'yanked': True}]}):
            with self.subTest(change=change):
                def request(url, data=None):
                    return self.response(url) if url == OSV else {**self.response(url), **change}
                self.assertFalse(audit([self.package], request)['ok'])

    def test_withdrawn_pypi_advisory_is_not_active(self):
        def request(url, data=None):
            return self.response(url) if url == OSV else {**self.response(url), 'vulnerabilities': [{'id': 'withdrawn', 'withdrawn': '2026-01-01'}]}
        self.assertTrue(audit([self.package], request)['ok'])

    def test_missing_registry_data_fails_closed(self):
        def request(url, data=None):
            if url != OSV:raise ValueError('registry unavailable')
            return self.response(url)
        result = audit([self.package], request)
        self.assertFalse(result['ok']); self.assertTrue(result['errors'])

    def test_empty_or_wrong_release_metadata_is_not_clean(self):
        for change in ({'urls': []}, {'info': {'name': 'other', 'version': '1.0.0'}}, {'vulnerabilities': None}):
            with self.subTest(change=change):
                def request(url, data=None):
                    return self.response(url) if url == OSV else {**self.response(url), **change}
                self.assertFalse(audit([self.package], request)['ok'])

    def test_incomplete_osv_batch_rejected(self):
        with self.assertRaises(ValueError): audit([self.package], lambda *args: {'results': []})

    def test_osv_pagination_cannot_hide_findings(self):
        def request(url, data):
            if data['queries'][0].get('page_token'):
                return {'results': [{'vulns': [{'id': 'second-page-advisory'}]}]}
            return {'results': [{'next_page_token': 'next'}]}
        result = osv_findings([self.package], request)
        self.assertIn('second-page-advisory', result[('example-package', '1.0.0')])

    def test_floating_direct_url_and_empty_pins_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'requirements.txt'
            for content in ('example>=1', 'example @ https://example.invalid/pkg.whl', '', '-r another-file'):
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises(ValueError):read_lock(path)
            path.write_text('# Exact pin\nexample==1.0.0\n')
            self.assertEqual(read_lock(path), [{'name': 'example', 'version': '1.0.0'}])

    def test_runtime_security_floor_and_unknown_series(self):
        self.assertFalse(runtime_status((3, 14, 0))['ok'])
        self.assertTrue(runtime_status((3, 14, 8))['ok'])
        self.assertTrue(runtime_status((3, 11, 17))['ok'])
        self.assertFalse(runtime_status((3, 11, 16))['ok'])
        self.assertFalse(runtime_status((3, 99, 0))['ok'])
