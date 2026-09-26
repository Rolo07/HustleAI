"""Supabase PostgreSQL storage. No Data API, public schema, or silent fallback."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
import time

from hustleai.storage.repository import WorkflowRepository


def connect(root, filename='.supabase-runtime.json'):
    """Open one private TLS-verified connection using saved backend settings.

    Credentials never enter tool results or command arguments. Connection
    failures expose only a generic message. Session pooling is required because
    invoice coordination uses session advisory locks; transaction pooling is
    intentionally rejected. Administrative credentials are used only by setup.
    """
    import psycopg
    settings = json.loads((root / filename).read_text())
    if settings.get('sslmode') != 'verify-full' or not settings.get('sslrootcert'):
        raise ValueError('Supabase requires verify-full TLS and a saved CA certificate path.')
    if int(settings.get('port', 0)) != 5432:
        raise ValueError('Use the Supabase session pooler on port 5432.')
    try:
        return psycopg.connect(**settings, autocommit=True, options='-c statement_timeout=30000 -c lock_timeout=10000')
    except psycopg.Error:
        raise ValueError('Supabase connection failed. Check private settings, CA certificate and network; no local fallback was used.') from None


class PostgresRepository(WorkflowRepository):
    """Own one session; row claims and invoice locks work across VPS processes."""

    param = '%s'
    org_column = 'organization_id'
    created_column = 'created_at'
    for_update = ' FOR UPDATE'

    def __init__(self, root, organization, connection=None, schema='hustle_private'):
        """Connect without DDL; migrations must have completed before startup.

        connection/schema are dependency-injection points for isolated database
        tests. Runtime uses only the private schema and restricted saved role.
        A database failure stops initialization before any Zoho OAuth/API call.
        """
        if not re.fullmatch(r'[a-z_][a-z0-9_]*', schema):
            raise ValueError('Invalid internal schema name.')
        self.prefix = schema + '.'
        self.organization = str(organization)
        self.connection = connection if connection is not None else connect(root)
        try:
            # Also tests role grants/RLS and schema availability before work.
            self.connection.execute(f'SELECT id FROM {self.table("operations")} WHERE organization_id=%s LIMIT 1', (self.organization,))
        except BaseException:
            self.connection.close()
            raise

    @staticmethod
    def encode_json(value):
        """Adapt native JSON-compatible data as PostgreSQL JSONB."""
        from psycopg.types.json import Jsonb
        return Jsonb(value)

    @staticmethod
    def encode_time(value):
        """Convert legacy Unix seconds to an aware UTC timestamp."""
        return datetime.fromtimestamp(value, timezone.utc)

    @staticmethod
    def encode_date(value):
        """Pass calendar dates to PostgreSQL date columns unchanged."""
        return value

    @staticmethod
    def decode_time(value):
        """Expose UTC database timestamps as Unix seconds to expiry checks."""
        return value.timestamp()

    def transaction(self):
        """Return an atomic transaction/savepoint on this autocommit connection."""
        return self.connection.transaction()

    def _function(self, name, values):
        """Call an allowlisted internal function with bound driver parameters.

        Domain rejections use SQLSTATE P0001 and become ValueError, preserving
        the workflow API. Constraint, connection and permission failures remain
        database errors; uncertain results must never trigger remote retries.
        """
        from psycopg.errors import RaiseException
        if name not in ('claim_operation', 'finish_operation',
                        'invalidate_invoice_reviews', 'approve_invoice_review'):
            raise ValueError('Unknown workflow function.')
        try:
            return self.connection.execute(
                f'SELECT * FROM {self.prefix}{name}({",".join(["%s"] * len(values))})', values)
        except RaiseException as error:
            raise ValueError(error.diag.message_primary) from None

    def claim_operation(self, operation_id):
        """Claim exactly once using the database clock and row lock.

        This method must own the outer transaction: attempted state must commit
        before the caller performs a Zoho mutation. Nesting a claim in another
        transaction is rejected, since rolling it back could permit a replay.
        """
        from psycopg.pq import TransactionStatus
        if self.connection.info.transaction_status != TransactionStatus.IDLE:
            raise ValueError('Operation claims require an independent committed transaction.')
        with self.transaction():
            return self.row(self._function('claim_operation', (self.organization, operation_id)))

    def finish_operation(self, operation_id, result, mapping=None):
        """Save the successful response and optional client index atomically."""
        contact, phone = mapping if mapping is not None else (None, None)
        with self.transaction():
            self._function('finish_operation', (self.organization, operation_id,
                self.encode_json(result), contact, phone))

    def invalidate_reviews(self, invoice_id):
        """Commit joint review/approval revocation before any remote draft edit."""
        with self.transaction():
            self._function('invalidate_invoice_reviews', (self.organization, str(invoice_id)))

    def approve_review(self, review, approval_id):
        """Persist an exact validated binding; the database rechecks expiry/state.

        Caller must hold the invoice lock and have authenticated the owner and
        checked current Zoho content/PDF hash. Existing approvals replay their ID;
        revoked approvals cannot be revived. Nothing is sent or marked sent.
        """
        with self.transaction():
            return self._function('approve_invoice_review', (self.organization,
                review['review_id'], approval_id, review['version'], review['phone'],
                review['pdf_hash'])).fetchone()[0]

    def find_contacts(self, phone):
        """Find all organization-owned candidates; Zoho verifies them afterward."""
        return [row[0] for row in self.connection.execute(
            f'SELECT contact_id FROM {self.table("phone_mappings")} WHERE organization_id=%s AND phone=%s ORDER BY contact_id',
            (self.organization, phone))]

    def mapping_rows(self):
        """Return the current index for private exports and migration verification."""
        return self.connection.execute(f'SELECT contact_id,phone FROM {self.table("phone_mappings")} WHERE organization_id=%s ORDER BY contact_id,phone',
            (self.organization,)).fetchall()

    def add_mapping(self, contact_id, phone):
        """Upsert an ID/phone pair, participating in the caller's transaction."""
        self.connection.execute(f'''INSERT INTO {self.table('phone_mappings')}
            (organization_id,contact_id,phone) VALUES (%s,%s,%s)
            ON CONFLICT (organization_id,contact_id,phone) DO NOTHING''',
            (self.organization, str(contact_id), phone))

    def replace_mappings(self, rows):
        """Atomically replace this organization's index after a complete Zoho scan.

        Caller holds the organization mapping lock throughout the scan so client
        creations cannot be dropped by a concurrent snapshot replacement.
        """
        with self.transaction():
            self.connection.execute(f'DELETE FROM {self.table("phone_mappings")} WHERE organization_id=%s', (self.organization,))
            for contact_id, phone in sorted(set(rows)):
                self.add_mapping(contact_id, phone)
            self.connection.execute(f'UPDATE {self.table("phone_mappings")} SET verified_at=now() WHERE organization_id=%s', (self.organization,))

    @contextmanager
    def named_lock(self, name, timeout=10):
        """Hold a bounded session advisory lock; release even on workflow failure.

        Deterministic signed 64-bit keys include schema, organization and entity.
        These coordinate cooperating app processes only, never the Zoho UI.
        A dropped connection releases the lock; remote outcomes remain uncertain
        and operation claims must not be reset or automatically retried.
        """
        key = int.from_bytes(hashlib.sha256(f'{self.prefix}:{self.organization}:{name}'.encode()).digest()[:8], 'big', signed=True)
        deadline = time.monotonic() + timeout
        while not self.connection.execute('SELECT pg_try_advisory_lock(%s)', (key,)).fetchone()[0]:
            if time.monotonic() >= deadline:
                raise ValueError('Another workflow is using this record; try again after it finishes.')
            time.sleep(0.1)
        try:
            yield
        finally:
            if not self.connection.closed:
                self.connection.execute('SELECT pg_advisory_unlock(%s)', (key,))

    def invoice_lock(self, invoice_id):
        """Serialize review/update/approval actions for one organization invoice."""
        return self.named_lock('invoice:' + str(invoice_id))
