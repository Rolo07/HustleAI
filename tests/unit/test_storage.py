"""Backend startup must fail closed before OAuth or any remote side effect."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hustleai.storage.backend import open_repository
from hustleai.workflows.service import Service


class StorageStartupTests(unittest.TestCase):
    """Exercise missing storage, maintenance and explicit backend selection."""

    def test_unknown_backend_cannot_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'Unknown storage_backend'):
                open_repository({'storage_backend': 'typo'}, root)
            self.assertEqual(list(root.iterdir()), [])

    def test_maintenance_blocks_before_opening_any_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.storage-maintenance').write_text('maintenance')
            with self.assertRaisesRegex(ValueError, 'maintenance'):
                open_repository({'storage_backend': 'sqlite', 'organization_id': '123'}, root)
            self.assertFalse((root / '.zoho-operations.sqlite3').exists())

    def test_hosted_failure_happens_before_zoho_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            config.write_text(json.dumps({'storage_backend': 'postgres', 'organization_id': '123', 'country_code': '27'}))
            with patch('hustleai.workflows.service.CONFIG', config), patch('hustleai.workflows.service.API') as api:
                with self.assertRaises(FileNotFoundError):
                    Service(root=root)
                api.assert_not_called()
            self.assertFalse((root / '.zoho-operations.sqlite3').exists())
