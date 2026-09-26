"""Meta webhook verification and event parsing. No business rules here."""
import hashlib
import hmac

from hustleai.domain.phones import normalize


def signature_valid(app_secret, body, header):
    """Check X-Hub-Signature-256 (HMAC-SHA256 of the raw body with the app secret)."""
    if not header or not header.startswith('sha256='):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len('sha256='):])


def subscription_challenge(params, verify_token):
    """Return hub.challenge for a valid subscription request, else None."""
    if params.get('hub.mode') == 'subscribe' and params.get('hub.verify_token') == verify_token:
        return params.get('hub.challenge') or None
    return None


def message_text(message):
    """Extract readable text from supported message types, else None."""
    kind = message.get('type')
    if kind == 'text':
        return (message.get('text') or {}).get('body')
    if kind == 'button':
        return (message.get('button') or {}).get('text')
    if kind == 'interactive':
        interactive = message.get('interactive') or {}
        reply = interactive.get('button_reply') or interactive.get('list_reply') or {}
        return reply.get('title')
    return None


def parse_events(payload, phone_number_id):
    """Turn a webhook payload into message and status events for one number.

    Changes addressed to another phone number ID are ignored. Senders are
    normalized to +digits. Returns a list of dicts with kind 'message' or
    'status'; each has an 'id' suitable for de-duplication.
    """
    events = []
    for entry in payload.get('entry') or []:
        for change in entry.get('changes') or []:
            value = change.get('value') or {}
            if str((value.get('metadata') or {}).get('phone_number_id')) != str(phone_number_id):
                continue
            names = {c.get('wa_id'): (c.get('profile') or {}).get('name') for c in value.get('contacts') or []}
            for message in value.get('messages') or []:
                sender = normalize('+' + str(message.get('from', '')), '27')
                if not sender or not message.get('id'):
                    continue
                events.append({'kind': 'message', 'id': message['id'], 'from': sender,
                               'name': names.get(message.get('from')), 'type': message.get('type'),
                               'text': message_text(message), 'timestamp': message.get('timestamp')})
            for status in value.get('statuses') or []:
                if not status.get('id') or not status.get('status'):
                    continue
                errors = [e.get('title') or e.get('message') or str(e.get('code')) for e in status.get('errors') or []]
                events.append({'kind': 'status', 'id': f"status:{status['id']}:{status['status']}",
                               'message_id': status['id'], 'status': status['status'],
                               'recipient': normalize('+' + str(status.get('recipient_id', '')), '27'),
                               'error': '; '.join(errors) or None})
    return events
