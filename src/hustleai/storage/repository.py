"""Organization-scoped persistence shared by the explicit SQL backends.

Only fixed internal table/column names enter SQL strings. User values always
use driver parameters. Transactions, JSON and timestamp adapters are supplied
by each backend; business workflows never issue SQL themselves.
"""
import json
import time


class WorkflowRepository:
    """Store operations and versioned reviews; one connection per service call."""

    prefix = ''
    param = '?'
    org_column = 'org'
    created_column = 'created'
    for_update = ''

    def table(self, name):
        """Return a qualified internal table name (never accepts external input)."""
        if name not in ('operations', 'invoice_reviews', 'invoice_approvals', 'phone_mappings',
                        'customer_order_cycles', 'reorder_forecasts'):
            raise ValueError('Unknown storage table.')
        return self.prefix + name

    def insert(self, table, record):
        """Insert a canonical record; serialize JSON/time with the backend adapter."""
        values = []
        columns = []
        for key, value in record.items():
            columns.append({'org': self.org_column, 'created': self.created_column}.get(key, key))
            if key in ('payload', 'preview', 'result', 'invoice') and value is not None:
                value = self.encode_json(value)
            elif key == 'created':
                value = self.encode_time(value)
            values.append(value)
        self.connection.execute(f"INSERT INTO {self.table(table)} ({','.join(columns)}) VALUES ({','.join([self.param] * len(values))})", values)

    def row(self, cursor):
        """Decode one row to canonical org/created names and Python JSON values."""
        values = cursor.fetchone()
        if values is None:
            return None
        result = dict(zip((d[0] for d in cursor.description), values))
        if self.org_column in result:
            result['org'] = result.pop(self.org_column)
        if self.created_column in result:
            result['created'] = self.decode_time(result.pop(self.created_column))
        for key in ('payload', 'preview', 'result', 'invoice'):
            if key in result and isinstance(result[key], str):
                result[key] = json.loads(result[key])
        return result

    def get(self, table, key, value):
        """Read a record only within this repository's organization."""
        return self.row(self.connection.execute(
            f'SELECT * FROM {self.table(table)} WHERE {key}={self.param} AND {self.org_column}={self.param}',
            (value, self.organization)))

    def create_operation(self, operation_id, kind, payload, preview):
        """Commit an immutable proposal before exposing its confirmation ID."""
        with self.transaction():
            self.insert('operations', dict(id=operation_id, org=self.organization, kind=kind,
                payload=payload, preview=preview, created=time.time(), status='pending', result=None))

    def claim_operation(self, operation_id):
        """Atomically return completed state or mark a fresh proposal attempted.

        The committed attempted state precedes every remote write. Uncertain
        outcomes remain blocked, including when the database goes down after a
        successful Zoho request. Competing callers cannot both claim the ID.
        """
        with self.transaction():
            operation = self.row(self.connection.execute(
                f'SELECT * FROM {self.table("operations")} WHERE id={self.param} AND {self.org_column}={self.param}{self.for_update}',
                (operation_id, self.organization)))
            if not operation:
                raise ValueError('Unknown operation for this organization.')
            if operation['status'] == 'done':
                return operation
            if operation['status'] != 'pending' or time.time() - operation['created'] > 1800:
                raise ValueError('Expired or already attempted operation. Check Zoho before preparing another; never blindly retry.')
            self.connection.execute(
                f"UPDATE {self.table('operations')} SET status='attempted' WHERE id={self.param} AND {self.org_column}={self.param}",
                (operation_id, self.organization))
            return operation

    def finish_operation(self, operation_id, result, mapping=None):
        """Commit the successful response and optional new-client index together.

        mapping is (contact_id, normalized_phone). PostgreSQL makes both writes
        atomic. The legacy Markdown backend cannot make its file update transactional.
        """
        with self.transaction():
            cursor = self.connection.execute(
                f"UPDATE {self.table('operations')} SET status='done',result={self.param} WHERE id={self.param} AND {self.org_column}={self.param} AND status='attempted'",
                (self.encode_json(result), operation_id, self.organization))
            if cursor.rowcount != 1:
                raise ValueError('Operation is not in the attempted state.')
            if mapping:
                self.add_mapping(*mapping)

    def invalidate_reviews(self, invoice_id):
        """Revoke both review and delivery permission in one transaction."""
        with self.transaction():
            for table in ('invoice_reviews', 'invoice_approvals'):
                self.connection.execute(
                    f"UPDATE {self.table(table)} SET status='invalid' WHERE {self.org_column}={self.param} AND invoice_id={self.param}",
                    (self.organization, str(invoice_id)))

    def save_review(self, record):
        """Persist a complete versioned PDF review under the caller's invoice lock."""
        with self.transaction():
            self.insert('invoice_reviews', dict(record, org=self.organization))

    def review(self, review_id):
        """Return an organization-owned review, or None if absent."""
        return self.get('invoice_reviews', 'review_id', review_id)

    def approval(self, approval_id):
        """Return an organization-owned approval, including its current status."""
        return self.get('invoice_approvals', 'approval_id', approval_id)

    def approve_review(self, review, approval_id):
        """Idempotently approve a validated review under an invoice lock.

        The workflow must validate live invoice content and the PDF first. An
        invalid existing approval is never revived by retrying the same review.
        """
        with self.transaction():
            existing = self.get('invoice_approvals', 'review_id', review['review_id'])
            if existing:
                if existing['status'] != 'approved':
                    raise ValueError('Approval is invalid; request a new review.')
                return existing['approval_id']
            self.insert('invoice_approvals', dict(approval_id=approval_id, review_id=review['review_id'],
                org=self.organization, invoice_id=review['invoice_id'], version=review['version'],
                recipient=review['phone'], created=time.time(), status='approved'))
            self.connection.execute(
                f"UPDATE {self.table('invoice_reviews')} SET status='approved' WHERE review_id={self.param} AND {self.org_column}={self.param}",
                (review['review_id'], self.organization))
            return approval_id

    def order_cycles(self):
        """Return owner forecast settings keyed by Zoho contact ID."""
        rows = self.connection.execute(
            f"SELECT contact_id,cycle_days,excluded,note FROM {self.table('customer_order_cycles')} WHERE {self.org_column}={self.param} ORDER BY contact_id",
            (self.organization,)).fetchall()
        return {str(cid): {'cycle_days': days, 'excluded': bool(excluded), 'note': note}
                for cid, days, excluded, note in rows}

    def set_order_cycle(self, contact_id, cycle_days, note=None):
        """Save an owner-set reorder cycle and include the customer again."""
        with self.transaction():
            self.connection.execute(
                f"""INSERT INTO {self.table('customer_order_cycles')} ({self.org_column},contact_id,cycle_days,excluded,note,updated_at)
                VALUES ({self.param},{self.param},{self.param},{self.param},{self.param},CURRENT_TIMESTAMP)
                ON CONFLICT ({self.org_column},contact_id) DO UPDATE SET cycle_days=excluded.cycle_days,
                excluded=excluded.excluded, note=excluded.note, updated_at=CURRENT_TIMESTAMP""",
                (self.organization, str(contact_id), int(cycle_days), False, note))

    def set_forecast_excluded(self, contact_id, excluded, note=None):
        """Exclude or re-include a customer, keeping any owner-set cycle."""
        table = self.table('customer_order_cycles')
        with self.transaction():
            if excluded:
                self.connection.execute(
                    f"""INSERT INTO {table} ({self.org_column},contact_id,cycle_days,excluded,note,updated_at)
                    VALUES ({self.param},{self.param},NULL,{self.param},{self.param},CURRENT_TIMESTAMP)
                    ON CONFLICT ({self.org_column},contact_id) DO UPDATE SET excluded=excluded.excluded,
                    note=excluded.note, updated_at=CURRENT_TIMESTAMP""",
                    (self.organization, str(contact_id), True, note))
                return
            key = (self.organization, str(contact_id))
            self.connection.execute(
                f"DELETE FROM {table} WHERE {self.org_column}={self.param} AND contact_id={self.param} AND cycle_days IS NULL", key)
            self.connection.execute(
                f"UPDATE {table} SET excluded={self.param}, updated_at=CURRENT_TIMESTAMP WHERE {self.org_column}={self.param} AND contact_id={self.param}",
                (False,) + key)

    def clear_order_cycle(self, contact_id):
        """Remove all owner forecast settings so history-based prediction applies."""
        with self.transaction():
            self.connection.execute(
                f"DELETE FROM {self.table('customer_order_cycles')} WHERE {self.org_column}={self.param} AND contact_id={self.param}",
                (self.organization, str(contact_id)))

    def save_forecast(self, run_date, window_start, window_end, content):
        """Save one report per run date; a rerun replaces that date's report."""
        with self.transaction():
            self.connection.execute(
                f"""INSERT INTO {self.table('reorder_forecasts')} ({self.org_column},run_date,window_start,window_end,content,created_at)
                VALUES ({self.param},{self.param},{self.param},{self.param},{self.param},CURRENT_TIMESTAMP)
                ON CONFLICT ({self.org_column},run_date) DO UPDATE SET window_start=excluded.window_start,
                window_end=excluded.window_end, content=excluded.content, created_at=CURRENT_TIMESTAMP""",
                (self.organization, self.encode_date(run_date), self.encode_date(window_start),
                 self.encode_date(window_end), self.encode_json(content)))

    def forecast(self, run_date=None):
        """Return the report for a run date, or the latest report, or None."""
        table = self.table('reorder_forecasts')
        if run_date is None:
            cursor = self.connection.execute(
                f'SELECT content FROM {table} WHERE {self.org_column}={self.param} ORDER BY run_date DESC LIMIT 1',
                (self.organization,))
        else:
            cursor = self.connection.execute(
                f'SELECT content FROM {table} WHERE {self.org_column}={self.param} AND run_date={self.param}',
                (self.organization, self.encode_date(run_date)))
        row = cursor.fetchone()
        if row is None:
            return None
        return json.loads(row[0]) if isinstance(row[0], str) else row[0]

    def close(self):
        """Release the connection; callers must use finally or a context manager."""
        self.connection.close()
