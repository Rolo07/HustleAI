"""Set up, check and run the WhatsApp gateway.

  hustleai-whatsapp setup        Enter the business number, Meta IDs and secrets
  hustleai-whatsapp check        Confirm Meta's number matches the settings
  hustleai-whatsapp send-test    Send a test message to the owner
  hustleai-whatsapp serve        Run the webhook server

Settings live in .whatsapp.json in the private data directory (mode 0600,
excluded from Git). Secrets are typed at hidden prompts and never printed.
"""
import argparse
from getpass import getpass
import json
import secrets
import time

from hustleai.config import CONFIG, ROOT
from hustleai.domain.phones import normalize
from hustleai.integrations.whatsapp import config as wa_config
from hustleai.integrations.whatsapp.client import WhatsAppClient
from hustleai.storage.backend import open_repository
from hustleai.storage.files import private_write


def ask(label, current='', secret=False):
    """Prompt for a value; Enter keeps the current one."""
    shown = ' [saved]' if secret and current else (f' [{current}]' if current else '')
    value = (getpass if secret else input)(f'{label}{shown}: ').strip()
    return value or current


def setup(args):
    """Create or update .whatsapp.json, prompting for anything not given."""
    path = wa_config.config_path(ROOT)
    settings = json.loads(path.read_text()) if path.exists() else {}
    settings['business_number'] = args.business_number or ask('Business WhatsApp number (+27...)', settings.get('business_number', ''))
    settings['phone_number_id'] = args.phone_number_id or ask('Meta phone number ID (digits)', settings.get('phone_number_id', ''))
    settings['owner_number'] = args.owner_number or settings.get('owner_number') or wa_config.DEFAULT_OWNER
    settings['access_token'] = ask('Permanent access token', settings.get('access_token', ''), secret=True)
    settings['app_secret'] = ask('Meta app secret', settings.get('app_secret', ''), secret=True)
    settings['verify_token'] = settings.get('verify_token') or secrets.token_urlsafe(24)
    if args.template:
        settings['notify_template'] = {'name': args.template, 'language': args.template_language}
    if args.agent_url:
        settings['owner_agent'] = dict(settings.get('owner_agent') or {}, url=args.agent_url)
    settings.setdefault('graph_version', wa_config.DEFAULT_GRAPH_VERSION)
    private_write(path, json.dumps(settings, indent=2) + '\n')
    loaded = wa_config.load(ROOT)
    print(f"Saved. Business number {loaded['business_number']}, owner {loaded['owner_number']}.")
    print(f"Webhook verify token (enter it in the Meta app): {loaded['verify_token']}")
    print('Next: hustleai-whatsapp check')


def store():
    """Open the configured storage for the Zoho organization."""
    return open_repository(json.loads(CONFIG.read_text()), ROOT)


def check(_args):
    """Compare Meta's number with the settings and test storage access."""
    settings = wa_config.load(ROOT)
    info = WhatsAppClient(settings).number_info()
    display = normalize('+' + ''.join(ch for ch in info.get('display_phone_number', '') if ch.isdigit()), '27')
    if display != settings['business_number']:
        raise ValueError(f"Meta reports {display or 'no number'} for this phone number ID, "
                         f"but the settings say {settings['business_number']}.")
    session = store()
    try:
        session.last_inbound(settings['owner_number'])
    finally:
        session.close()
    print(f"OK: {display} ({info.get('verified_name', 'no verified name')}), quality {info.get('quality_rating', 'unknown')}. Storage reachable.")


def send_test(_args):
    """Send a test text to the owner through the outbox."""
    from hustleai.workflows.outbox import Outbox
    settings = wa_config.load(ROOT)
    session = store()
    try:
        rows = Outbox(session, WhatsAppClient(settings), settings).send_text(
            'test', f'test:{int(time.time())}', settings['owner_number'], 'HustleAI gateway test message.')
    finally:
        session.close()
    status = rows[0]['status']
    if status == 'waiting_window':
        print('Held: the owner has not messaged the business number in the last 24 hours. '
              'Send it any message, then the test goes out.')
    else:
        print(f'Test message {status}.' + (f" Error: {rows[0]['error']}" if rows[0].get('error') else ''))


def build_gateway(settings):
    """Wire the webhook server to storage, the outbox and the router."""
    from hustleai.integrations.whatsapp.owner_agent import OwnerAgent
    from hustleai.integrations.whatsapp.server import Gateway
    from hustleai.workflows.messaging import MessageRouter
    from hustleai.workflows.outbox import Outbox
    from hustleai.workflows.service import Service
    client = WhatsAppClient(settings)
    agent = OwnerAgent(settings['owner_agent']) if settings['owner_agent'].get('url') else None

    def process(session, event):
        MessageRouter(session, Outbox(session, client, settings), settings, Service, agent).handle(event)

    return Gateway(settings, store, process)


def serve(_args):
    """Run the gateway until stopped."""
    from hustleai.integrations.whatsapp.server import serve as run_server
    settings = wa_config.load(ROOT)
    run_server(build_gateway(settings), settings['host'], settings['port'])


def main(argv=None):
    """Dispatch a gateway command."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    configure = sub.add_parser('setup', help='Enter or update WhatsApp settings')
    configure.add_argument('--business-number', help='Business number, e.g. +27821234567')
    configure.add_argument('--phone-number-id', help="Meta's numeric phone number ID")
    configure.add_argument('--owner-number', help=f'Owner number (default {wa_config.DEFAULT_OWNER})')
    configure.add_argument('--template', help='Approved template for messages outside the 24-hour window')
    configure.add_argument('--template-language', default='en', help='Template language code (default en)')
    configure.add_argument('--agent-url', help='Optional OpenAI-compatible chat URL for owner free text')
    sub.add_parser('check', help='Confirm Meta number and storage')
    sub.add_parser('send-test', help='Send a test message to the owner')
    sub.add_parser('serve', help='Run the webhook server')
    args = parser.parse_args(argv)
    {'setup': setup, 'check': check, 'send-test': send_test, 'serve': serve}[args.command](args)


def run():
    """Run the CLI and convert expected errors to concise terminal messages."""
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
    except (KeyboardInterrupt, EOFError):
        raise SystemExit('\nStopped.') from None


if __name__ == '__main__':
    run()
