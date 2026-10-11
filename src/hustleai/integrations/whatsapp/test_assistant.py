"""Isolated Meta test-account echo gateway; no Zoho or production storage."""
import argparse
from getpass import getpass
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3

from hustleai.integrations.whatsapp.client import WhatsAppClient, WhatsAppError
from hustleai.integrations.whatsapp.server import Gateway, Route, serve

LOG = logging.getLogger(__name__)
PHONE_ID = '1417138624811816'
RECIPIENT = '+27837758811'
PREFIX = 'Hi! This is RG Midrand’s HustleAI test assistant. You said: '


def load_env(path):
    """Load literal KEY=value entries without evaluating shell expressions."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key.strip()):
            raise ValueError('Invalid environment file entry.')
        os.environ.setdefault(key.strip(), value.strip())


def settings_from_env():
    settings = {key: os.getenv(env, '') for key, env in (
        ('access_token', 'WHATSAPP_ACCESS_TOKEN'), ('app_secret', 'META_APP_SECRET'),
        ('verify_token', 'WHATSAPP_VERIFY_TOKEN'))}
    missing = [key for key, value in settings.items() if not value]
    if missing:
        raise ValueError('Missing environment credentials: ' + ', '.join(missing))
    settings['phone_number_id'] = os.getenv('WHATSAPP_PHONE_NUMBER_ID', PHONE_ID)
    settings['owner_number'] = os.getenv('WHATSAPP_TEST_RECIPIENT', RECIPIENT)
    settings['graph_version'] = os.getenv('WHATSAPP_API_VERSION', 'v23.0')
    if settings['phone_number_id'] != PHONE_ID or settings['owner_number'] != RECIPIENT:
        raise ValueError('Test mode requires the designated Meta test number and verified recipient.')
    if not re.fullmatch(r'v\d+\.\d+', settings['graph_version']):
        raise ValueError('WHATSAPP_API_VERSION must look like v23.0.')
    return settings


class TestStore:
    """Private durable queue, duplicate suppression and delivery evidence."""
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, payload TEXT, state TEXT, outbound TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS statuses (id TEXT PRIMARY KEY, state TEXT)')
        self.db.commit()

    def close(self):
        self.db.close()

    def claim_webhook_event(self, event_id, kind, event):
        # Status notifications never enter the reply queue.
        if kind == 'status':
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO statuses VALUES (?, ?)', (event['id'], event['status']))
            LOG.info('delivery_status=%s', event['status'] if event['status'] in ('sent', 'delivered', 'read', 'failed') else 'other')
            return False
        with self.db:
            added = self.db.execute('INSERT OR IGNORE INTO events VALUES (?, ?, ?, NULL)',
                                    (event_id, json.dumps(event), 'queued')).rowcount
        LOG.info('incoming_event=%s', 'queued' if added else 'duplicate')
        return bool(added)

    def unprocessed_webhook_events(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM events WHERE state='queued'")]

    def transition(self, event_id, state, outbound=None):
        with self.db:
            self.db.execute('UPDATE events SET state=?, outbound=?, payload=NULL WHERE id=?', (state, outbound, event_id))

    def evidence(self):
        return {'events': dict(self.db.execute('SELECT state, count(*) FROM events GROUP BY state')),
                'statuses': dict(self.db.execute('SELECT state, count(*) FROM statuses GROUP BY state'))}


def build_test_gateway(settings, path, client=None):
    client = client or WhatsAppClient(settings)

    def process(store, event):
        if event.get('from') != RECIPIENT or event.get('type') != 'text' or not isinstance(event.get('text'), str):
            store.transition(event['id'], 'ignored')
            LOG.info('incoming_event=ignored')
            return
        # Claim before remote write. After a crash/timeout do not resend blindly:
        # Graph sends have no idempotency key and acceptance can be uncertain.
        store.transition(event['id'], 'sending')
        LOG.info('reply_attempt text_length=%d', len(event['text']))
        try:
            message_id = client.send_text(RECIPIENT, PREFIX + event['text'])
        except WhatsAppError as error:
            store.transition(event['id'], 'uncertain_or_failed')
            LOG.warning('reply_not_confirmed meta_code=%s', error.code)
            return
        store.transition(event['id'], 'accepted', message_id)
        LOG.info('reply_api_accepted delivery_unconfirmed')

    return Gateway(routes={'': Route(settings, lambda: TestStore(path), process)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('configure', 'serve', 'evidence', 'check'), default='serve', nargs='?')
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--port', type=int, default=8086)
    parser.add_argument('--state-dir', type=Path, default=Path('var/whatsapp-test'))
    args = parser.parse_args()
    if args.command == 'configure':
        if args.env_file.exists():
            raise ValueError('Environment file exists; edit it locally to preserve existing settings.')
        token = getpass('Meta test access token (hidden): ').strip()
        secret = getpass('Meta app secret (hidden): ').strip()
        if not token or not secret or any(c in token + secret for c in '\r\n'):
            raise ValueError('Both credentials are required and must be single-line values.')
        verify = secrets.token_urlsafe(32)
        content = f'WHATSAPP_ACCESS_TOKEN={token}\nMETA_APP_SECRET={secret}\nWHATSAPP_VERIFY_TOKEN={verify}\nWHATSAPP_PHONE_NUMBER_ID={PHONE_ID}\nWHATSAPP_TEST_RECIPIENT={RECIPIENT}\nWHATSAPP_API_VERSION=v23.0\n'
        fd = os.open(args.env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(content)
        print('Credentials saved privately. Copy WHATSAPP_VERIFY_TOKEN from the file into Meta; do not share credentials in chat.')
        return
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.state_dir.chmod(0o700)
    path = args.state_dir / 'events.sqlite3'
    initial = TestStore(path)
    initial.close()
    path.chmod(0o600)
    if args.command == 'evidence':
        store = TestStore(path)
        try:
            print(json.dumps(store.evidence(), indent=2))
        finally:
            store.close()
        return
    load_env(args.env_file)
    settings = settings_from_env()
    if args.command == 'check':
        client = WhatsAppClient(settings)
        info = client.number_info()
        print('Test phone metadata accessible:', bool(info.get('display_phone_number')))
        result = client.request(f"https://graph.facebook.com/{settings['graph_version']}/2319220545518996/subscribed_apps")
        apps = [str(item.get('whatsapp_business_api_data', {}).get('id')) for item in result.get('data', [])]
        print('HustleAI subscribed to test WABA:', '27773466375663340' in apps)
        return
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    serve(build_test_gateway(settings, path), '127.0.0.1', args.port)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        # Provider details may contain sensitive strings; expose type/code only.
        raise SystemExit('Test assistant stopped: ' + type(error).__name__) from None
