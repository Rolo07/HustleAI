"""Explicit demo SQLite or existing tenant PostgreSQL; never an outage fallback."""
from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

EMPTY = {'conversations': {}, 'events': {}, 'outbox': {}, 'activity': [], 'requests': {}}


class Journal:
    def __init__(self, root, organization='demo', demo=False):
        self.demo, self.organization = demo, organization
        self.mutex = threading.RLock()
        if demo:
            folder = Path(root) / 'var' / 'dashboard-demo'
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            folder.chmod(0o700)
            path = folder / 'journal.sqlite3'
            self.db = sqlite3.connect(path, check_same_thread=False)
            path.chmod(0o600)
            self.db.execute('CREATE TABLE IF NOT EXISTS dashboard (id INTEGER PRIMARY KEY, state TEXT)')
            self.db.execute('INSERT OR IGNORE INTO dashboard VALUES (1, ?)', (json.dumps(EMPTY),))
            self.db.commit()
        else:
            from hustleai.storage.postgres.repository import connect
            self.db = connect(Path(root))
            # Missing migration or inaccessible database fails closed.
            self.db.execute('INSERT INTO hustle_private.owner_dashboard (organization_id,state) VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING',
                            (organization, json.dumps(EMPTY)))
        self.lock_id = int.from_bytes(hashlib.sha256(('owner-dashboard:' + organization).encode()).digest()[:8], 'big', signed=True)

    @contextmanager
    def locked(self):
        with self.mutex:
            if not self.demo:
                self.db.execute('SELECT pg_advisory_lock(%s)', (self.lock_id,))
            try:
                yield
            finally:
                if not self.demo:
                    self.db.execute('SELECT pg_advisory_unlock(%s)', (self.lock_id,))

    def read(self):
        if self.demo:
            return json.loads(self.db.execute('SELECT state FROM dashboard WHERE id=1').fetchone()[0])
        row = self.db.execute('SELECT state FROM hustle_private.owner_dashboard WHERE organization_id=%s', (self.organization,)).fetchone()
        if not row:
            raise ValueError('Dashboard journal unavailable.')
        return copy.deepcopy(row[0])

    def write(self, state):
        encoded = json.dumps(state, allow_nan=False)
        if self.demo:
            self.db.execute('UPDATE dashboard SET state=? WHERE id=1', (encoded,))
            self.db.commit()
        else:
            self.db.execute('UPDATE hustle_private.owner_dashboard SET state=%s::jsonb, updated_at=now() WHERE organization_id=%s',
                            (encoded, self.organization))

    def close(self):
        self.db.close()
