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
                        'customer_order_cycles', 'reorder_forecasts', 'orders', 'sync_runs',
                        'webhook_events', 'delivery_attempts', 'conversation_windows'):
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

    ORDER_COLUMNS = ('invoice_id', 'customer_id', 'customer_name', 'invoice_number', 'reference_number',
                     'invoice_date', 'status', 'total', 'last_modified_time', 'notes', 'line_items')

    def save_order(self, order, details_synced, source):
        """Insert or replace one invoice copy; see save_orders."""
        self.save_orders([(order, details_synced)], source)

    def save_orders(self, orders, source):
        """Insert or replace invoice copies in one transaction.

        orders is a list of (order, details_synced) pairs. Each order holds
        ORDER_COLUMNS with invoice_date as a date and line_items as a list or
        None. A summary-only save clears old details so they are fetched again.
        A saved order is never marked deleted.
        """
        if not orders:
            return
        columns = self.ORDER_COLUMNS + ('details_synced', 'deleted', 'source')
        rows = [[self.organization] + [self.encode_date(order[c]) if c == 'invoice_date'
                 else self.encode_json(order[c]) if c == 'line_items' and order[c] is not None
                 else order[c] for c in self.ORDER_COLUMNS] + [bool(details), False, source]
                for order, details in orders]
        updates = ','.join(f'{c}=excluded.{c}' for c in columns[1:])
        with self.transaction():
            self.connection.cursor().executemany(
                f"""INSERT INTO {self.table('orders')} ({self.org_column},{','.join(columns)},updated_at)
                VALUES ({','.join([self.param] * (len(columns) + 1))},CURRENT_TIMESTAMP)
                ON CONFLICT ({self.org_column},invoice_id) DO UPDATE SET {updates},updated_at=CURRENT_TIMESTAMP""",
                rows)

    def order_index(self):
        """Return {invoice_id: {last_modified_time, deleted}} for change detection."""
        rows = self.connection.execute(
            f"SELECT invoice_id,last_modified_time,deleted FROM {self.table('orders')} WHERE {self.org_column}={self.param}",
            (self.organization,)).fetchall()
        return {str(iid): {'last_modified_time': modified, 'deleted': bool(deleted)} for iid, modified, deleted in rows}

    def mark_orders_deleted(self, invoice_ids):
        """Flag invoices that a complete Zoho listing no longer contains."""
        if not invoice_ids:
            return
        with self.transaction():
            self.connection.cursor().executemany(
                f"UPDATE {self.table('orders')} SET deleted={self.param},updated_at=CURRENT_TIMESTAMP WHERE {self.org_column}={self.param} AND invoice_id={self.param}",
                [(True, self.organization, str(invoice_id)) for invoice_id in invoice_ids])

    def orders(self):
        """Return all non-deleted invoice copies as dictionaries, newest first."""
        cursor = self.connection.execute(
            f"SELECT {','.join(self.ORDER_COLUMNS)},details_synced FROM {self.table('orders')} "
            f"WHERE {self.org_column}={self.param} AND deleted={self.param} ORDER BY invoice_date DESC,invoice_id",
            (self.organization, False))
        names = self.ORDER_COLUMNS + ('details_synced',)
        result = []
        for row in cursor.fetchall():
            order = dict(zip(names, row))
            order['invoice_date'] = str(order['invoice_date'])
            order['details_synced'] = bool(order['details_synced'])
            if isinstance(order['line_items'], str):
                order['line_items'] = json.loads(order['line_items'])
            result.append(order)
        return result

    def orders_missing_details(self):
        """Return invoice IDs still needing a detail read, newest first."""
        return [str(row[0]) for row in self.connection.execute(
            f"SELECT invoice_id FROM {self.table('orders')} WHERE {self.org_column}={self.param} "
            f"AND deleted={self.param} AND details_synced={self.param} ORDER BY invoice_date DESC,invoice_id",
            (self.organization, False, False)).fetchall()]

    def record_sync(self, name, started, finished, result):
        """Save the latest completed run of a sync job."""
        with self.transaction():
            self.connection.execute(
                f"""INSERT INTO {self.table('sync_runs')} ({self.org_column},name,started_at,finished_at,result)
                VALUES ({self.param},{self.param},{self.param},{self.param},{self.param})
                ON CONFLICT ({self.org_column},name) DO UPDATE SET started_at=excluded.started_at,
                finished_at=excluded.finished_at, result=excluded.result""",
                (self.organization, name, self.encode_time(started), self.encode_time(finished), self.encode_json(result)))

    def last_sync(self, name):
        """Return {started, finished, result} for a sync job, or None if it never ran."""
        row = self.connection.execute(
            f"SELECT started_at,finished_at,result FROM {self.table('sync_runs')} WHERE {self.org_column}={self.param} AND name={self.param}",
            (self.organization, name)).fetchone()
        if row is None:
            return None
        result = json.loads(row[2]) if isinstance(row[2], str) else row[2]
        return {'started': self.decode_time(row[0]), 'finished': self.decode_time(row[1]), 'result': result}

    # --- WhatsApp gateway -------------------------------------------------

    def claim_webhook_event(self, event_id, event_type, payload):
        """Store a verified event once. True if new, False for a redelivery."""
        with self.transaction():
            cursor = self.connection.execute(
                f"""INSERT INTO {self.table('webhook_events')} ({self.org_column},provider_event_id,event_type,payload,received_at)
                VALUES ({self.param},{self.param},{self.param},{self.param},{self.param})
                ON CONFLICT ({self.org_column},provider_event_id) DO NOTHING""",
                (self.organization, event_id, event_type, self.encode_json(payload), self.encode_time(time.time())))
            return cursor.rowcount == 1

    def finish_webhook_event(self, event_id):
        """Mark an event processed so startup recovery skips it."""
        with self.transaction():
            self.connection.execute(
                f"UPDATE {self.table('webhook_events')} SET processed_at={self.param} WHERE {self.org_column}={self.param} AND provider_event_id={self.param}",
                (self.encode_time(time.time()), self.organization, event_id))

    def unprocessed_webhook_events(self, limit=500):
        """Return stored but unprocessed event payloads, oldest first."""
        rows = self.connection.execute(
            f"SELECT payload FROM {self.table('webhook_events')} WHERE {self.org_column}={self.param} AND processed_at IS NULL ORDER BY received_at LIMIT {int(limit)}",
            (self.organization,)).fetchall()
        return [json.loads(r[0]) if isinstance(r[0], str) else r[0] for r in rows]

    def touch_conversation(self, phone):
        """Record an inbound message time, opening WhatsApp's 24-hour window."""
        with self.transaction():
            self.connection.execute(
                f"""INSERT INTO {self.table('conversation_windows')} ({self.org_column},phone,last_inbound_at)
                VALUES ({self.param},{self.param},{self.param})
                ON CONFLICT ({self.org_column},phone) DO UPDATE SET last_inbound_at=excluded.last_inbound_at""",
                (self.organization, phone, self.encode_time(time.time())))

    def last_inbound(self, phone):
        """Unix time of the latest inbound message from phone, or None."""
        row = self.connection.execute(
            f"SELECT last_inbound_at FROM {self.table('conversation_windows')} WHERE {self.org_column}={self.param} AND phone={self.param}",
            (self.organization, phone)).fetchone()
        return self.decode_time(row[0]) if row else None

    DELIVERY_COLUMNS = ('id', 'purpose', 'idempotency_key', 'recipient', 'provider_message_id',
                        'status', 'message', 'error', 'attempts')

    def delivery(self, key):
        """Return the outbox row for an idempotency key, or None."""
        row = self.connection.execute(
            f"SELECT {','.join(self.DELIVERY_COLUMNS)} FROM {self.table('delivery_attempts')} WHERE {self.org_column}={self.param} AND idempotency_key={self.param}",
            (self.organization, key)).fetchone()
        return self.delivery_row(row)

    def delivery_row(self, row):
        """Decode an outbox row selected with DELIVERY_COLUMNS."""
        if row is None:
            return None
        result = dict(zip(self.DELIVERY_COLUMNS, row))
        if isinstance(result['message'], str):
            result['message'] = json.loads(result['message'])
        return result

    def create_delivery(self, delivery_id, purpose, key, recipient, message):
        """Insert a pending outbox row once; return the stored row either way."""
        now = self.encode_time(time.time())
        with self.transaction():
            self.connection.execute(
                f"""INSERT INTO {self.table('delivery_attempts')} (id,{self.org_column},purpose,idempotency_key,recipient,status,message,attempts,created_at,updated_at)
                VALUES ({','.join([self.param] * 10)})
                ON CONFLICT ({self.org_column},idempotency_key) DO NOTHING""",
                (delivery_id, self.organization, purpose, key, recipient, 'pending', self.encode_json(message), 0, now, now))
        return self.delivery(key)

    def update_delivery(self, key, status, provider_message_id=None, error=None, attempted=False):
        """Set an outbox row's status, provider message ID and last error."""
        with self.transaction():
            self.connection.execute(
                f"""UPDATE {self.table('delivery_attempts')} SET status={self.param},
                provider_message_id=COALESCE({self.param},provider_message_id), error={self.param},
                attempts=attempts+{1 if attempted else 0}, updated_at={self.param}
                WHERE {self.org_column}={self.param} AND idempotency_key={self.param}""",
                (status, provider_message_id, error, self.encode_time(time.time()), self.organization, key))

    def apply_delivery_status(self, provider_message_id, status, error=None):
        """Apply a Meta status webhook without moving a message backwards."""
        rank = {'pending': 0, 'waiting_window': 0, 'sent': 1, 'delivered': 2, 'read': 3, 'failed': 4}
        if status not in rank:
            return
        with self.transaction():
            row = self.connection.execute(
                f"SELECT status FROM {self.table('delivery_attempts')} WHERE {self.org_column}={self.param} AND provider_message_id={self.param}",
                (self.organization, provider_message_id)).fetchone()
            if row is None or rank[status] <= rank.get(row[0], 0):
                return
            self.connection.execute(
                f"UPDATE {self.table('delivery_attempts')} SET status={self.param}, error=COALESCE({self.param},error), updated_at={self.param} WHERE {self.org_column}={self.param} AND provider_message_id={self.param}",
                (status, error, self.encode_time(time.time()), self.organization, provider_message_id))

    def waiting_deliveries(self, recipient):
        """Outbox rows held until the recipient writes again, oldest first."""
        rows = self.connection.execute(
            f"SELECT {','.join(self.DELIVERY_COLUMNS)} FROM {self.table('delivery_attempts')} WHERE {self.org_column}={self.param} AND recipient={self.param} AND status={self.param} ORDER BY created_at",
            (self.organization, recipient, 'waiting_window')).fetchall()
        return [self.delivery_row(r) for r in rows]

    def close(self):
        """Release the connection; callers must use finally or a context manager."""
        self.connection.close()
