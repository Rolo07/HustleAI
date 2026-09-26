"""Explicit legacy backend retained for isolated tests and migration source reads."""
from contextlib import contextmanager
import fcntl
import json
import os
import re
from hustleai.storage.repository import WorkflowRepository
from hustleai.storage.legacy.sqlite import connect_operations
from hustleai.storage.files import private_write


class SQLiteRepository(WorkflowRepository):
    """Use the existing journal layout and organization-checked Markdown index."""

    encode_json = staticmethod(json.dumps)
    encode_time = staticmethod(float)
    decode_time = staticmethod(float)
    encode_date = staticmethod(lambda value: value.isoformat())

    def __init__(self, root, organization):
        """Open and initialize a legacy journal; never used as an outage fallback."""
        self.root = root
        self.organization = str(organization)
        self.mapping = root / 'zoho-client-phone-map.md'
        self.connection = connect_operations(root)
        self.connection.execute('''CREATE TABLE IF NOT EXISTS invoice_reviews (
            review_id TEXT PRIMARY KEY, org TEXT, invoice_id TEXT, customer_id TEXT,
            phone TEXT, version TEXT, invoice TEXT, pdf_path TEXT, pdf_hash TEXT,
            created REAL, status TEXT)''')
        self.connection.execute('''CREATE TABLE IF NOT EXISTS invoice_approvals (
            approval_id TEXT PRIMARY KEY, review_id TEXT UNIQUE, org TEXT,
            invoice_id TEXT, version TEXT, recipient TEXT, created REAL, status TEXT)''')
        self.connection.execute('''CREATE TABLE IF NOT EXISTS customer_order_cycles (
            org TEXT NOT NULL, contact_id TEXT NOT NULL, cycle_days INTEGER,
            excluded INTEGER NOT NULL DEFAULT 0, note TEXT, updated_at TEXT,
            PRIMARY KEY (org, contact_id))''')
        self.connection.execute('''CREATE TABLE IF NOT EXISTS reorder_forecasts (
            org TEXT NOT NULL, run_date TEXT NOT NULL, window_start TEXT, window_end TEXT,
            content TEXT NOT NULL, created_at TEXT, PRIMARY KEY (org, run_date))''')
        self.connection.execute('''CREATE TABLE IF NOT EXISTS orders (
            org TEXT NOT NULL, invoice_id TEXT NOT NULL, customer_id TEXT NOT NULL,
            customer_name TEXT, invoice_number TEXT, reference_number TEXT,
            invoice_date TEXT NOT NULL, status TEXT NOT NULL, total TEXT,
            last_modified_time TEXT, notes TEXT, line_items TEXT,
            details_synced INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL, updated_at TEXT, PRIMARY KEY (org, invoice_id))''')
        self.connection.execute('''CREATE TABLE IF NOT EXISTS sync_runs (
            org TEXT NOT NULL, name TEXT NOT NULL, started_at REAL, finished_at REAL,
            result TEXT NOT NULL, PRIMARY KEY (org, name))''')
        self.connection.commit()

    @contextmanager
    def transaction(self):
        """Serialize writers and roll back the complete unit on any exception."""
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def mapping_rows(self):
        """Read unique ID/phone pairs only after checking organization metadata."""
        if not self.mapping.exists():
            raise ValueError('Sync the local phone mapping first.')
        content = self.mapping.read_text()
        if f'Organization: {self.organization}\n' not in content:
            raise ValueError('Wrong organization in phone mapping; sync first.')
        return sorted(set(re.findall(r'^\| (\d+) \| (\+\d+) \|$', content, re.M)))

    def find_contacts(self, phone):
        """Return all candidate IDs, preserving ambiguity for shared numbers."""
        return sorted({cid for cid, number in self.mapping_rows() if number == phone})

    def add_mapping(self, contact_id, phone):
        """Append a legacy mapping only when the organization's file exists."""
        if self.mapping.exists():
            content = self.mapping.read_text()
            if f'Organization: {self.organization}\n' in content:
                private_write(self.mapping, content + f'| {contact_id} | {phone} |\n')

    @contextmanager
    def invoice_lock(self, invoice_id):
        """Coordinate local legacy processes; external Zoho edits still need checks."""
        folder = self.root / '.zoho-locks'
        folder.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(folder / f'{self.organization}-{invoice_id}.lock', os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
