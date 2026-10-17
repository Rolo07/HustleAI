"""Persistent conversation, version-bound reorder and one-time sending state machine."""
from datetime import datetime, timezone
import hashlib
import json
import logging
import time
import uuid

from hustleai.integrations.whatsapp.test_assistant import RECIPIENT
from hustleai.integrations.whatsapp.webhook import parse_events, signature_valid, subscription_challenge

LOG = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def audit(state, cid, action, detail=''):
    state['activity'].append({'id': uid(), 'conversation': cid, 'action': action, 'detail': detail, 'at': now()})


def conversation(state, phone, name='Customer'):
    if phone not in state['conversations']:
        state['conversations'][phone] = {'id': phone, 'name': name, 'phone': phone, 'mode': 'hermes', 'epoch': 0,
                                        'unread': 0, 'messages': [], 'order': None, 'escalation': '', 'suggestion': None}
    return state['conversations'][phone]


def message(c, actor, text, status='received', key=None):
    record = {'id': key or uid(), 'actor': actor, 'text': text, 'status': status, 'at': now()}
    c['messages'].append(record)
    return record


def public_order(quote, previous=None):
    order = {**quote, 'version': uid(), 'status': 'awaiting_customer', 'confirmed_version': None, 'approved_version': None}
    if previous:
        for key in ('invoice_id', 'invoice_version', 'review_id', 'pdf_path', 'due_date'):
            if key in previous:
                order[key] = previous[key]
    return order


def proposal_text(order):
    lines = ['Sure, here are your order details:', *[
        f"{line['name']}: {line['quantity']} × R{line['rate']}" for line in order['lines']],
        *([f"Delivery: R{order['shipping_charge']}"] if float(order.get('shipping_charge', 0)) else []),
        f"Total: R{order['total']} ZAR", order['tax'], 'Payment is due seven days from the invoice date.',
        'If everything looks right, reply CONFIRM ' + order['version'] + ' so we can go ahead. If you’d like a change, just let us know.']
    return '\n'.join(lines)


class Engine:
    def __init__(self, journal, business, client=None, settings=None, demo=False):
        self.journal, self.business, self.client = journal, business, client
        self.settings, self.demo = settings or {}, demo
        self.connected = {'backend': 'Connected', 'storage': 'Demo SQLite' if demo else 'Tenant PostgreSQL',
                          'whatsapp': 'Simulated · no sends' if demo else 'Configured · not yet probed',
                          'hermes': 'Simulated' if demo else 'Not configured'}
        self.errors = []

    def snapshot(self):
        with self.journal.locked():
            state = self.journal.read()
            # Server-only provider payloads, PDF paths and credentials are excluded.
            conversations = []
            for c in state['conversations'].values():
                view = json.loads(json.dumps(c))
                if view['order']:
                    view['order'].pop('payload', None)
                    view['order'].pop('pdf_path', None)
                conversations.append(view)
            return {'demo': self.demo, 'connections': self.connected, 'conversations': conversations,
                    'activity': state['activity'][-150:][::-1], 'errors': self.errors[-5:]}

    def queue(self, state, c, text, actor='hermes', kind='text', key=None):
        key = key or uid()
        if key in state['outbox']:
            return
        m = message(c, actor, text, 'queued', key)
        order = c.get('order')
        state['outbox'][key] = {'id': key, 'conversation': c['id'], 'text': text, 'actor': actor,
                               'kind': kind, 'state': 'queued', 'epoch': c['epoch'],
                               'version': order['version'] if order else None, 'message': m['id']}
        audit(state, c['id'], 'Message queued', 'Reviewed PDF' if kind == 'document' else actor)

    def accept(self, body, signature, slug=None):
        if slug not in (None, ''):
            return 404
        if not signature_valid(self.settings.get('app_secret', ''), body, signature):
            return 403
        try:
            payload = json.loads(body)
            if payload.get('object') != 'whatsapp_business_account':
                return 200
            events = parse_events(payload, self.settings['phone_number_id'])
        except (ValueError, TypeError, AttributeError, KeyError):
            return 400
        with self.journal.locked():
            state = self.journal.read()
            for event in events:
                if event['id'] in state['events']:
                    continue
                # Only this user's verified test recipient may enter this deployment.
                if event['kind'] == 'message' and event['from'] != RECIPIENT:
                    continue
                state['events'][event['id']] = {'event': event, 'state': 'queued'}
            self.journal.write(state)
        return 200

    def challenge(self, slug, params):
        return subscription_challenge(params, self.settings.get('verify_token', '')) if slug in (None, '') else None

    def seed(self):
        if not self.demo:
            raise ValueError('Fictional records are permitted only in explicit demo mode.')
        with self.journal.locked():
            state = self.journal.read()
            if state['conversations']:
                return
            for phone, name, text in [('+27820000001', 'Maya Botha', 'Hi! Can we repeat our last order for Luna?'),
                                       ('+27820000002', 'Daniel Jacobs', 'Can I change the chicken to 8 packs, please?'),
                                       ('+27820000003', 'Thandi Mokoena', 'Thanks Roland. Could you confirm collection for Friday?'),
                                       ('+27820000099', 'New customer', 'Please repeat my usual order.')]:
                c = conversation(state, phone, name)
                message(c, 'customer', text)
                c['unread'] = 1
                audit(state, phone, 'Customer message received', 'Fictional demo record')
            c = state['conversations']['+27820000001']
            c['order'] = public_order(self.business.quote(c['phone']))
            c['order'].update(self.business.draft(c['phone'], c['order']))
            c['order']['confirmed_version'] = c['order']['version']
            c['order']['status'] = 'needs_approval'
            c['escalation'] = 'Customer confirmed. Draft PDF ready for Roland’s review.'
            message(c, 'hermes', proposal_text(c['order']), 'delivered')
            message(c, 'customer', 'CONFIRM ' + c['order']['version'])
            message(c, 'note', 'Fictional demo: Luna prefers chicken. Confirm collection before dispatch.')
            c = state['conversations']['+27820000002']
            c['order'] = public_order(self.business.quote(c['phone']))
            message(c, 'hermes', proposal_text(c['order']), 'read')
            c = state['conversations']['+27820000003']
            c['mode'] = 'human'
            message(c, 'owner', 'I’m checking collection times for you.', 'api_accepted')
            c = state['conversations']['+27820000099']
            c['escalation'] = 'Missing or ambiguous customer record. No order information disclosed.'
            self.journal.write(state)

    def action(self, cid, action, data, request_id):
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 128:
            raise ValueError('A unique request ID is required.')
        with self.journal.locked():
            state = self.journal.read()
            digest = hashlib.sha256(json.dumps([cid, action, data], sort_keys=True).encode()).hexdigest()
            if request_id in state['requests']:
                if state['requests'][request_id] != digest:
                    raise ValueError('Request ID reused with different content.')
                return
            c = state['conversations'].get(cid)
            if not c:
                raise ValueError('Unknown conversation.')
            order = c.get('order')
            if action == 'read':
                c['unread'] = 0
            elif action in ('takeover', 'resume'):
                desired_mode = 'human' if action == 'takeover' else 'hermes'
                if c['mode'] != desired_mode:
                    c['epoch'] += 1
                c['mode'] = desired_mode
                if action == 'takeover':
                    for row in state['outbox'].values():
                        if row['conversation'] == cid and row['actor'] == 'hermes' and row['state'] == 'queued':
                            self.cancel(state, row, 'Human takeover')
                audit(state, cid, 'Human takeover' if action == 'takeover' else 'Hermes resumed')
            elif action == 'note':
                text = self.text(data)
                message(c, 'note', text)
                audit(state, cid, 'Internal note added')
            elif action in ('reply', 'clarify'):
                if action == 'reply' and c['mode'] != 'human':
                    raise ValueError('Take over the conversation before replying manually.')
                text = self.text(data)
                self.queue(state, c, text, 'owner')
                if action == 'clarify':
                    c['escalation'] = 'Clarification requested; customer response pending.'
                    audit(state, cid, 'Clarification requested')
            elif action in ('prepare_reorder', 'reorder', 'edit'):
                if order and order['status'] in ('sending', 'sent', 'draft_creating', 'draft_uncertain', 'delivery_uncertain'):
                    raise ValueError('An attempted draft or delivery must be reconciled before editing.')
                if action == 'edit' and (not order or data.get('version') != order['version']):
                    raise ValueError('Order version changed; refresh before editing.')
                desired = data.get('lines') if action == 'edit' else None
                if action == 'prepare_reorder':
                    quote = getattr(self.business, 'prepare_quote', self.business.quote)(c['phone'])
                elif data.get('stock_confirmations'):
                    quote = self.business.quote(c['phone'], desired, stock_confirmations=data['stock_confirmations'])
                else:
                    quote = self.business.quote(c['phone'], desired)
                if order:
                    self.business.invalidate(order)
                    self.cancel_order_messages(state, cid)
                c['order'] = public_order(quote, order)
                c['order']['prepared_only'] = action == 'prepare_reorder' or bool(order and order.get('prepared_only'))
                c['escalation'] = ''
                if c['order']['prepared_only']:
                    c['escalation'] = 'Order prepared, not sent. Review quantities and confirm stock in Edit, then send the proposal.'
                elif c['mode'] == 'hermes':
                    self.queue(state, c, proposal_text(c['order']), key='proposal:' + c['order']['version'])
                else:
                    c['escalation'] = 'Updated proposal is ready. Resume Hermes before sending it.'
                audit(state, cid, 'Order edited' if action == 'edit' else 'Reorder proposed', 'Customer confirmation required')
            elif action == 'send_proposal':
                if not order or order['status'] != 'awaiting_customer' or c['mode'] != 'hermes':
                    raise ValueError('Resume Hermes and prepare a current proposal first.')
                if any(not line.get('stock_verified', True) for line in order['lines']):
                    raise ValueError('Edit the order and confirm physical stock before sending the proposal.')
                if order.get('owner_stock') and time.time() - order.get('owner_stock_at', 0) > 1800:
                    raise ValueError('Physical stock confirmation expired. Edit and confirm stock again.')
                self.queue(state, c, proposal_text(order), key='proposal:' + order['version'])
            elif action == 'reject':
                if not order or data.get('version') != order['version'] or order['status'] not in ('needs_approval', 'awaiting_customer'):
                    raise ValueError('This version cannot be rejected; refresh the order.')
                reason = self.text(data)
                self.business.invalidate(order)
                self.cancel_order_messages(state, cid)
                order.update(status='rejected', approved_version=None)
                c['escalation'] = 'Rejected: ' + reason
                audit(state, cid, 'Order rejected', reason)
            elif action == 'approve':
                if not order or data.get('version') != order['version']:
                    raise ValueError('Approval must reference the currently reviewed version.')
                if order['status'] in ('approved', 'sending', 'sent') and order['approved_version'] == order['version']:
                    pass
                else:
                    if order['status'] != 'needs_approval' or order['confirmed_version'] != order['version']:
                        raise ValueError('Customer must confirm this exact version before owner approval.')
                    self.business.validate(order)
                    order.update(status='approved', approved_version=order['version'])
                    self.queue(state, c, 'Reviewed invoice · ' + order['invoice_id'], 'owner', 'document', 'invoice:' + order['version'])
                    audit(state, cid, 'Order approved', 'Exact customer, invoice and PDF version')
                    c['escalation'] = ''
            elif action == 'approve_reply':
                suggestion = c.get('suggestion')
                if not suggestion or suggestion['id'] != data.get('suggestion_id'):
                    raise ValueError('Hermes suggestion changed.')
                if c['mode'] != 'hermes':
                    raise ValueError('Hermes is paused for this conversation.')
                self.queue(state, c, suggestion['text'], key='suggestion:' + suggestion['id'])
                c['suggestion'] = None
                c['escalation'] = ''
                audit(state, cid, 'Hermes reply approved')
            elif action == 'reject_reply':
                if not c.get('suggestion') or c['suggestion']['id'] != data.get('suggestion_id'):
                    raise ValueError('Hermes suggestion changed.')
                c['suggestion'] = None
                c['escalation'] = 'Hermes reply rejected; owner follow-up required.'
                audit(state, cid, 'Hermes reply rejected', self.text(data))
            elif action == 'edit_reply':
                if not c.get('suggestion') or c['suggestion']['id'] != data.get('suggestion_id'):
                    raise ValueError('Hermes suggestion changed.')
                c['suggestion'] = {'id': uid(), 'text': self.text(data), 'epoch': c['epoch']}
                audit(state, cid, 'Hermes reply edited', 'Fresh owner approval required')
            elif action == 'reconcile':
                if not order or order['status'] != 'draft_uncertain' or data.get('version') != order['version']:
                    raise ValueError('Only an uncertain draft can be reconciled here.')
                recovered = self.business.reconcile(c['phone'], order)
                order.update(recovered, status='needs_approval', approved_version=None)
                c['escalation'] = 'Draft recovered without creating another invoice. Review the fresh PDF.'
                audit(state, cid, 'Draft reconciled', order['invoice_id'])
            elif action == 'refresh_review':
                if not order or order['status'] != 'needs_approval' or data.get('version') != order['version']:
                    raise ValueError('Refresh only the current unsent draft awaiting review.')
                order.update(self.business.refresh(c['phone'], order), approved_version=None)
                audit(state, cid, 'Draft PDF review refreshed')
            else:
                raise ValueError('Unknown action.')
            state['requests'][request_id] = digest
            self.journal.write(state)

    @staticmethod
    def text(data):
        text = str(data.get('text', '')).strip()
        if not text or len(text) > 3500:
            raise ValueError('Enter between 1 and 3500 characters.')
        return text

    def cancel(self, state, row, reason):
        row['state'] = 'cancelled'
        c = state['conversations'][row['conversation']]
        for m in c['messages']:
            if m['id'] == row['message']:
                m['status'] = 'cancelled'
        audit(state, c['id'], 'Queued message cancelled', reason)

    def cancel_order_messages(self, state, cid):
        for row in state['outbox'].values():
            if row['conversation'] == cid and row['state'] == 'queued' and row.get('version'):
                self.cancel(state, row, 'Order changed; previous version invalid')

    def demo_inbound(self, cid, text, event_id):
        if not self.demo:
            raise ValueError('Simulated customer events are disabled in live mode.')
        with self.journal.locked():
            state = self.journal.read()
            if cid not in state['conversations']:
                raise ValueError('Unknown demo conversation.')
            state['events'].setdefault(event_id, {'state': 'queued', 'event': {'id': event_id, 'kind': 'message',
                'type': 'text', 'from': cid, 'text': text, 'timestamp': str(int(time.time()))}})
            self.journal.write(state)

    def handle_event(self, event_id):
        with self.journal.locked():
            state = self.journal.read()
            row = state['events'][event_id]
            if row['state'] != 'queued':
                return
            event = row['event']
            row['state'] = 'processed'
            if event['kind'] == 'status':
                ranking = {'queued': 0, 'sending': 1, 'api_accepted': 2, 'sent': 3, 'delivered': 4, 'read': 5}
                for outbound in state['outbox'].values():
                    if outbound.get('provider_id') == event['message_id']:
                        status = event['status']
                        if status not in ('sent', 'delivered', 'read', 'failed'):
                            continue
                        if status == 'failed' and outbound['state'] in ('delivered', 'read'):
                            continue
                        if status != 'failed' and ranking.get(status, 0) <= ranking.get(outbound['state'], 0):
                            continue
                        outbound['state'] = status
                        c = state['conversations'][outbound['conversation']]
                        for m in c['messages']:
                            if m['id'] == outbound['message']:
                                m['status'] = status
                        audit(state, c['id'], 'Delivery ' + status)
                self.journal.write(state)
                return
            c = conversation(state, event['from'], event.get('name') or 'Verified test recipient')
            c['unread'] += 1
            c['last_inbound'] = time.time()
            supported = event.get('type') == 'text' and isinstance(event.get('text'), str)
            text = event['text'] if supported else '[Unsupported message type]'
            message(c, 'customer', text, key=event_id)
            audit(state, c['id'], 'Customer message received', event.get('type', 'unknown'))
            self.journal.write(state)
            if c['mode'] == 'human':
                return
            if not supported:
                c['escalation'] = 'Unsupported message; human review required.'
                self.journal.write(state)
                return
            order = c.get('order')
            if text.startswith('CONFIRM '):
                version = text[8:].strip()
                if not order or version != order['version'] or order['status'] != 'awaiting_customer':
                    c['escalation'] = 'Confirmation is stale or unclear; no new draft created.'
                    self.journal.write(state)
                    return
                if (any(not line.get('stock_verified', True) for line in order['lines']) or
                        (order.get('owner_stock') and time.time() - order.get('owner_stock_at', 0) > 1800)):
                    c['escalation'] = 'Physical stock must be confirmed before an invoice can be prepared.'
                    self.journal.write(state)
                    return
                # Persist before provider mutation; uncertain outcomes never retried.
                order.update(confirmed_version=version, status='draft_creating')
                audit(state, c['id'], 'Customer confirmed order', version)
                self.journal.write(state)
                try:
                    draft = self.business.draft(c['phone'], order)
                except Exception as error:
                    order['status'] = 'draft_uncertain'
                    c['escalation'] = 'Draft outcome requires reconciliation. No automatic retry (' + type(error).__name__ + ').'
                    audit(state, c['id'], 'Draft creation needs reconciliation', type(error).__name__)
                else:
                    order.update(draft, status='needs_approval', approved_version=None)
                    c['escalation'] = 'Customer confirmed; Roland must review the exact draft PDF.'
                    audit(state, c['id'], 'Unsent draft prepared', order['invoice_id'])
                self.journal.write(state)
                return
            if 'reorder' in text.lower() or 'repeat' in text.lower():
                if order and order['status'] not in ('rejected', 'sent'):
                    c['escalation'] = 'Reorder already exists; review the current version.'
                else:
                    try:
                        c['order'] = public_order(self.business.quote(c['phone']))
                        self.queue(state, c, proposal_text(c['order']), key='proposal:' + c['order']['version'])
                        c['escalation'] = ''
                        audit(state, c['id'], 'Reorder proposed')
                    except Exception as error:
                        c['escalation'] = 'Customer, catalog or tax lookup requires owner resolution (' + type(error).__name__ + '). No details disclosed.'
                self.journal.write(state)
                return
            # Free-text changes cannot be guessed. Hermes proposes wording only.
            epoch = c['epoch']
            # Only customer messages and provider-confirmed outbound messages are
            # conversation evidence. Unsent proposals, notes and failed sends
            # must never be treated as something the customer has seen.
            transcript = [{'actor': m['actor'], 'text': m['text'][:3500]}
                          for m in c['messages'][:-1]
                          if m['actor'] == 'customer' or
                          (m['actor'] in ('hermes', 'owner') and m.get('status') in ('sent', 'delivered', 'read'))][-20:]
        try:
            reply = self.business.agent(event['from'], text, transcript)
        except Exception as error:
            reply, failure = None, type(error).__name__
            self.connected['hermes'] = 'Unavailable (' + failure + ')'
        with self.journal.locked():
            state = self.journal.read()
            c = state['conversations'][event['from']]
            if c['mode'] != 'hermes' or c['epoch'] != epoch or c['messages'][-1]['id'] != event_id:
                audit(state, c['id'], 'Hermes result discarded', 'Conversation taken over during generation')
            elif reply:
                self.connected['hermes'] = 'Responding · proposals require approval' if not self.demo else 'Simulated'
                c['suggestion'] = {'id': uid(), 'text': reply[:3500], 'epoch': epoch}
                c['escalation'] = 'Hermes reply requires Roland’s approval. Order changes require re-pricing and new confirmation.'
                audit(state, c['id'], 'Hermes reply proposed')
            else:
                c['escalation'] = 'Hermes unavailable (' + failure + '); owner follow-up required.'
            self.journal.write(state)

    def dispatch(self, key):
        # This lock covers the final state check and remote send. Takeover shares
        # it, so once takeover returns no automated send can begin afterward.
        with self.journal.locked():
            state = self.journal.read()
            row = state['outbox'][key]
            if row['state'] != 'queued':
                return
            c = state['conversations'][row['conversation']]
            order = c.get('order')
            if row['actor'] == 'hermes' and (c['mode'] != 'hermes' or c['epoch'] != row['epoch']):
                self.cancel(state, row, 'Hermes paused or conversation changed')
                self.journal.write(state)
                return
            if row['version'] and (not order or order['version'] != row['version']):
                self.cancel(state, row, 'Order version changed')
                self.journal.write(state)
                return
            if row['kind'] == 'document' and (not order or order['approved_version'] != order['version'] or order['confirmed_version'] != order['version'] or order['status'] != 'approved'):
                self.cancel(state, row, 'Current owner and customer approvals required')
                self.journal.write(state)
                return
            if not self.demo and c['phone'] != RECIPIENT:
                self.cancel(state, row, 'Only the verified Meta test recipient is permitted')
                self.journal.write(state)
                return
            if not self.demo and time.time() - c.get('last_inbound', 0) > 23.5 * 3600:
                row['hold_reason'] = 'Waiting for the customer to open the 24-hour messaging window.'
                if not row.get('hold_logged'):
                    audit(state, c['id'], 'Message held', row['hold_reason'])
                    row['hold_logged'] = True
                    self.journal.write(state)
                return
            row['state'] = 'sending'
            if row['kind'] == 'document':
                order['status'] = 'sending'
            self.journal.write(state)  # durable no-replay boundary
            try:
                if self.demo:
                    provider_id = 'demo:' + key
                elif row['kind'] == 'document':
                    provider_id = self.business.send_review(c['phone'], order, self.client)
                else:
                    provider_id = self.client.send_text(c['phone'], row['text'])
            except Exception as error:
                row.update(state='failed', outcome='Uncertain provider outcome; reconcile before retry', error=type(error).__name__)
                if row['kind'] == 'document':
                    order['status'] = 'delivery_uncertain'
                audit(state, c['id'], 'Message failed / outcome uncertain', type(error).__name__)
                if not self.demo:
                    self.connected['whatsapp'] = 'Send failed / uncertain (' + type(error).__name__ + ')'
            else:
                row.update(state='api_accepted', provider_id=provider_id)
                if row['kind'] == 'document':
                    order['status'] = 'sent'
                audit(state, c['id'], 'API accepted message', 'Delivery unconfirmed' if not self.demo else 'Simulated only')
                if not self.demo:
                    self.connected['whatsapp'] = 'API reachable · last send accepted, delivery unconfirmed'
            for m in c['messages']:
                if m['id'] == row['message']:
                    m['status'] = row['state']
            self.journal.write(state)

    def recover(self):
        with self.journal.locked():
            state = self.journal.read()
            for c in state['conversations'].values():
                if c.get('order') and c['order']['status'] == 'draft_creating':
                    c['order']['status'] = 'draft_uncertain'
                    c['escalation'] = 'Interrupted draft creation; reconcile with Zoho before retrying.'
            for row in state['outbox'].values():
                if row['state'] == 'sending':
                    row['state'] = 'failed'
                    row['outcome'] = 'Interrupted send; reconcile provider outcome before retrying.'
                    c = state['conversations'][row['conversation']]
                    if row['kind'] == 'document' and c.get('order'):
                        c['order']['status'] = 'delivery_uncertain'
                    for m in c['messages']:
                        if m['id'] == row['message']:
                            m['status'] = 'failed'
                    audit(state, c['id'], 'Interrupted send requires reconciliation')
            self.journal.write(state)

    def tick(self):
        with self.journal.locked():
            state = self.journal.read()
            events = [key for key, row in state['events'].items() if row['state'] == 'queued']
        for key in events:
            self.handle_event(key)
        with self.journal.locked():
            keys = [key for key, row in self.journal.read()['outbox'].items() if row['state'] == 'queued']
        for key in keys:
            self.dispatch(key)

    def run_worker(self):
        while True:
            try:
                self.tick()
            except Exception as error:
                LOG.error('Dashboard worker unavailable: %s', type(error).__name__)
                self.errors = ['Background processing unavailable; no fallback used (' + type(error).__name__ + ').']
            time.sleep(.5)
