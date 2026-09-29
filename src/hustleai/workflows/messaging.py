"""Route verified WhatsApp messages: owner commands and customer referrals.

Identity comes only from Meta's signed webhook: the sender number is checked
against the configured owner number, never against anything in the message.
Only the owner can confirm writes or approve invoices, and the gateway runs
those commands itself, so no AI model ever supplies a confirmation.

Customers get no tools yet. Every customer message is referred to the owner
immediately and acknowledged once a day, until the customer flows in the
PRDs are built.
"""
from datetime import datetime
import re

from hustleai.workflows.forecast import TIMEZONE, render_whatsapp

COMMAND = re.compile(r'(CONFIRM|APPROVE)\s+([0-9a-f]{32})', re.IGNORECASE)
HELP = ('HustleAI commands:\n'
        'CONFIRM <id> - carry out a proposal you were shown\n'
        'APPROVE <id> - approve a reviewed invoice version\n'
        'FORECAST - this week\'s reorder forecast\n'
        'HELP - this list')
CUSTOMER_ACK = ('Thank you for your message. It has been passed on, '
                'and we will get back to you as soon as possible.')


def describe_result(result):
    """Short owner-facing summary of a confirmed Zoho write."""
    if 'contact' in result:
        return f"Client created: {result['contact'].get('contact_name', '')}."
    if 'payment' in result:
        payment = result['payment']
        return f"Payment recorded: R{payment.get('amount', '')} ({payment.get('payment_number', '')})."
    if 'invoice' in result:
        invoice = result['invoice']
        note = ' Ask for a fresh review before approving.' if result.get('owner_review_required') else ''
        return f"Invoice {invoice.get('invoice_number', '')} saved as {invoice.get('status', 'draft')}. Nothing was sent to the customer.{note}"
    return 'Done.'


class MessageRouter:
    """Handle one verified event at a time. Dependencies are injected for tests."""

    def __init__(self, store, outbox, settings, service_factory, owner_agent=None, zone=None):
        self.store = store
        self.outbox = outbox
        self.owner = settings['owner_number']
        self.zone = zone or TIMEZONE
        self.service_factory = service_factory
        self.owner_agent = owner_agent

    def handle(self, event):
        """Process one claimed event, then mark it processed."""
        if event['kind'] == 'status':
            self.outbox.apply_status(event)
        elif event['kind'] == 'message':
            self.store.touch_conversation(event['from'])
            self.outbox.flush(event['from'])
            if event['from'] == self.owner:
                self.handle_owner(event)
            else:
                self.handle_customer(event)
        self.store.finish_webhook_event(event['id'])

    def reply(self, event, text, suffix='reply'):
        """Reply to the event's sender; keyed by event so it is sent once."""
        return self.outbox.send_text('reply', f"{suffix}:{event['id']}", event['from'], text)

    def handle_owner(self, event):
        """Run owner commands directly; forward other text to the owner agent."""
        text = (event.get('text') or '').strip()
        if not text:
            self.reply(event, 'Only text messages are supported. ' + HELP)
            return
        command = COMMAND.fullmatch(text)
        upper = text.upper()
        try:
            if command:
                self.reply(event, self.run_command(command.group(1).upper(), command.group(2).lower()))
            elif upper == 'FORECAST':
                with self.service_factory() as service:
                    report = service.reorder_forecast()
                self.reply(event, render_whatsapp(report))
            elif upper in ('HELP', '?'):
                self.reply(event, HELP)
            elif self.owner_agent:
                self.reply(event, self.owner_agent.ask(text))
            else:
                self.reply(event, HELP)
        except ValueError as error:
            self.reply(event, f'Not done: {error}')

    def run_command(self, keyword, identifier):
        """Execute a CONFIRM or APPROVE that came from the verified owner number."""
        with self.service_factory() as service:
            if keyword == 'CONFIRM':
                return describe_result(service.confirm(identifier, 'CONFIRM ' + identifier))
            approval = service.approve_invoice(identifier, 'APPROVE ' + identifier)
            return (f"Approved invoice version for {approval['recipient']}. "
                    f"Approval {approval['approval_id']}. Nothing was sent to the customer.")

    def handle_customer(self, event):
        """Refer the message to the owner now and acknowledge the customer once a day.

        Message text is data: commands, names or claims inside it have no
        effect. Customers get no tools until the customer flows are built.
        """
        who = event['from'] + (f" ({event['name']})" if event.get('name') else '')
        content = event.get('text') or f"[{event.get('type') or 'unsupported'} message]"
        self.outbox.send_text('referral', f"referral:{event['id']}", self.owner,
                              f'Customer message from {who}:\n{content}\n\n'
                              'No automatic reply was given beyond an acknowledgement.')
        today = datetime.now(self.zone).date().isoformat()
        self.outbox.send_text('customer_ack', f"ack:{event['from']}:{today}", event['from'], CUSTOMER_ACK)
