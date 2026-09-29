"""Idempotent WhatsApp outbox with 24-hour window handling.

Every outbound message has a logical idempotency key and is recorded before it
is sent, so a retry, restart or repeated webhook never sends it twice.
WhatsApp only allows free-form messages within 24 hours of the recipient's
last message. Outside that window the message is held, an approved template
nudge asks the recipient to reply, and held messages go out when they do.
"""
from datetime import datetime
import time
import uuid

from hustleai.integrations.whatsapp.client import WhatsAppError, split_text
from hustleai.workflows.forecast import TIMEZONE

WINDOW_SECONDS = 23.5 * 3600  # Margin under WhatsApp's 24-hour limit.
DONE = ('sent', 'delivered', 'read')


class Outbox:
    """Send through a WhatsApp client, recording each message in storage."""

    def __init__(self, store, client, settings, zone=None):
        self.store = store
        self.client = client
        self.template = settings.get('notify_template') or {}
        self.zone = zone or TIMEZONE  # The tenant's timezone, for once-a-day keys.

    def window_open(self, phone):
        """True if the recipient wrote within the last 23.5 hours."""
        last = self.store.last_inbound(phone)
        return last is not None and time.time() - last < WINDOW_SECONDS

    def deliver(self, to, message):
        """Send one stored message body and return Meta's message ID."""
        if message['type'] == 'text':
            return self.client.send_text(to, message['body'])
        if message['type'] == 'document':
            return self.client.send_document(to, message['path'], message.get('filename'), message.get('caption'))
        raise ValueError('Unknown outbox message type.')

    def send(self, purpose, key, to, message):
        """Send a message once per key; returns its outbox row.

        message is {'type': 'text', 'body': ...} or {'type': 'document',
        'path': ..., 'filename': ..., 'caption': ...}. A key already sent is not
        sent again. Failures are recorded rather than raised, so one failed
        message never blocks the rest of an event.
        """
        row = self.store.create_delivery(uuid.uuid4().hex, purpose, key, to, message)
        if row['status'] in DONE or row['status'] == 'waiting_window':
            return row
        if not self.window_open(to):
            return self.hold(key, to)
        return self.attempt(key, to, row['message'])

    def attempt(self, key, to, message):
        """Try one send and record the outcome."""
        try:
            message_id = self.deliver(to, message)
        except WhatsAppError as error:
            if error.window_closed:
                return self.hold(key, to)
            self.store.update_delivery(key, 'failed', error=str(error), attempted=True)
        except (OSError, ValueError) as error:
            self.store.update_delivery(key, 'failed', error=type(error).__name__ + ': ' + str(error)[:200], attempted=True)
        else:
            self.store.update_delivery(key, 'sent', provider_message_id=message_id, attempted=True)
        return self.store.delivery(key)

    def hold(self, key, to):
        """Hold a message until the recipient writes, and nudge them once a day."""
        self.store.update_delivery(key, 'waiting_window',
                                   error='Outside the 24-hour window; waiting for the recipient to write.')
        self.nudge(to)
        return self.store.delivery(key)

    def nudge(self, to):
        """Send the approved template at most once per recipient per day."""
        if not self.template.get('name'):
            return
        today = datetime.now(self.zone).date().isoformat()
        key = f'nudge:{to}:{today}'
        row = self.store.create_delivery(uuid.uuid4().hex, 'nudge', key, to, {'type': 'template'})
        if row['status'] in DONE:
            return
        try:
            message_id = self.client.send_template(to, self.template['name'], self.template.get('language', 'en'))
        except (WhatsAppError, OSError) as error:
            self.store.update_delivery(key, 'failed', error=str(error), attempted=True)
        else:
            self.store.update_delivery(key, 'sent', provider_message_id=message_id, attempted=True)

    def flush(self, to):
        """Send held messages now that the recipient has written."""
        for row in self.store.waiting_deliveries(to):
            self.attempt(row['idempotency_key'], to, row['message'])

    def send_text(self, purpose, key, to, text):
        """Send text of any length as numbered WhatsApp-sized parts."""
        rows = []
        for index, part in enumerate(split_text(text)):
            rows.append(self.send(purpose, f'{key}:{index}', to, {'type': 'text', 'body': part}))
        return rows

    def apply_status(self, event):
        """Record a Meta delivery status (sent, delivered, read or failed)."""
        self.store.apply_delivery_status(event['message_id'], event['status'], event.get('error'))
