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
from pathlib import Path
import secrets
import time

from hustleai.config import ROOT
from hustleai.domain.phones import normalize
from hustleai.integrations.whatsapp import config as wa_config
from hustleai.integrations.whatsapp.client import WhatsAppClient
from hustleai.storage.backend import open_repository
from hustleai.storage.files import private_write
from hustleai.tenant import load_tenant, read_settings


def ask(label, current='', secret=False):
    """Prompt for a value; Enter keeps the current one."""
    shown = ' [saved]' if secret and current else (f' [{current}]' if current else '')
    value = (getpass if secret else input)(f'{label}{shown}: ').strip()
    return value or current


def folder_for(args):
    """The data folder to use: --tenant <slug> on a multi-tenant install, else the default."""
    if getattr(args, 'tenant', None):
        from hustleai.tenants import tenant_by_slug
        return tenant_by_slug(args.tenant)[1]
    return ROOT


def setup(args):
    """Create or update .whatsapp.json, prompting for anything not given."""
    root = folder_for(args)
    path = wa_config.config_path(root)
    settings = json.loads(path.read_text()) if path.exists() else {}
    settings['business_number'] = args.business_number or ask('Business WhatsApp number (+27...)', settings.get('business_number', ''))
    settings['phone_number_id'] = args.phone_number_id or ask('Meta phone number ID (digits)', settings.get('phone_number_id', ''))
    settings['owner_number'] = args.owner_number or settings.get('owner_number') or ''
    settings['access_token'] = ask('Permanent access token', settings.get('access_token', ''), secret=True)
    settings['app_secret'] = ask('Meta app secret', settings.get('app_secret', ''), secret=True)
    settings['verify_token'] = settings.get('verify_token') or secrets.token_urlsafe(24)
    if args.template:
        settings['notify_template'] = {'name': args.template, 'language': args.template_language}
    if args.agent_url:
        settings['owner_agent'] = dict(settings.get('owner_agent') or {}, url=args.agent_url)
    settings.setdefault('graph_version', wa_config.DEFAULT_GRAPH_VERSION)
    private_write(path, json.dumps(settings, indent=2) + '\n')
    loaded = wa_config.load(root)
    print(f"Saved. Business number {loaded['business_number']}, owner {loaded['owner_number']}.")
    print(f"Webhook verify token (enter it in the Meta app): {loaded['verify_token']}")
    print('Next: hustleai-whatsapp check')


def store(root=None):
    """Open storage for a tenant folder with that tenant's own database login."""
    root = Path(root or ROOT)
    return open_repository(read_settings(root), root)


def check(args):
    """Compare Meta's number with the settings and test storage access."""
    root = folder_for(args)
    settings = wa_config.load(root)
    info = WhatsAppClient(settings).number_info()
    display = normalize('+' + ''.join(ch for ch in info.get('display_phone_number', '') if ch.isdigit()), '27')
    if display != settings['business_number']:
        raise ValueError(f"Meta reports {display or 'no number'} for this phone number ID, "
                         f"but the settings say {settings['business_number']}.")
    session = store(root)
    try:
        session.last_inbound(settings['owner_number'])
    finally:
        session.close()
    print(f"OK: {display} ({info.get('verified_name', 'no verified name')}), quality {info.get('quality_rating', 'unknown')}. Storage reachable.")


def send_test(args):
    """Send a test text to the owner through the outbox."""
    from hustleai.workflows.outbox import Outbox
    root = folder_for(args)
    settings = wa_config.load(root)
    session = store(root)
    try:
        rows = Outbox(session, WhatsAppClient(settings), settings, load_tenant(root).zone).send_text(
            'test', f'test:{int(time.time())}', settings['owner_number'], 'HustleAI gateway test message.')
    finally:
        session.close()
    status = rows[0]['status']
    if status == 'waiting_window':
        print('Held: the owner has not messaged the business number in the last 24 hours. '
              'Send it any message, then the test goes out.')
    else:
        print(f'Test message {status}.' + (f" Error: {rows[0]['error']}" if rows[0].get('error') else ''))


def build_route(root, tenant):
    """Wire one tenant's webhook route to its storage, outbox, router and assistant."""
    from hustleai.integrations.whatsapp.owner_agent import OwnerAgent
    from hustleai.integrations.whatsapp.server import Route
    from hustleai.workflows.messaging import MessageRouter
    from hustleai.workflows.outbox import Outbox
    from hustleai.workflows.service import Service
    root = Path(root)
    settings = wa_config.load(root)
    client = WhatsAppClient(settings)
    zone = tenant.zone
    agent = None
    if settings['owner_agent'].get('url'):
        agent = OwnerAgent(settings['owner_agent'], session_key='whatsapp:' + settings['owner_number'])

    def service():
        return Service(root=root)

    def process(session, event):
        MessageRouter(session, Outbox(session, client, settings, zone), settings, service, agent, zone).handle(event)

    return Route(settings, lambda: store(root), process)


def build_gateway(all_tenants=False):
    """One route for this folder, or one per registered tenant with WhatsApp enabled."""
    from hustleai.integrations.whatsapp.server import DEFAULT, Gateway
    if not all_tenants:
        tenant = load_tenant(ROOT)
        tenant.require('whatsapp')
        return Gateway(routes={DEFAULT: build_route(ROOT, tenant)})
    from hustleai.tenants import list_tenants
    tenants, skipped = list_tenants()
    for slug, problem in skipped.items():
        print(f'Skipping tenant {slug}: {problem}', flush=True)
    routes = {}
    for slug, (tenant, folder) in tenants.items():
        if not tenant.has('whatsapp') or not wa_config.configured(folder):
            print(f'Tenant {slug}: WhatsApp not enabled or not set up; no route.', flush=True)
            continue
        routes[slug] = build_route(folder, tenant)
        print(f'Tenant {slug}: /webhook/{slug}', flush=True)
    if not routes:
        raise ValueError('No tenant has WhatsApp set up. Run: hustleai-whatsapp setup --tenant <slug>')
    return Gateway(routes=routes)


def serve(args):
    """Run the gateway until stopped."""
    from hustleai.integrations.whatsapp.server import serve as run_server
    gateway = build_gateway(args.all_tenants)
    host, port = args.host, args.port
    if not args.all_tenants:
        settings = wa_config.load(ROOT)
        host, port = host or settings['host'], port or settings['port']
    run_server(gateway, host or '127.0.0.1', port or 8085)


def main(argv=None):
    """Dispatch a gateway command."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    configure = sub.add_parser('setup', help='Enter or update WhatsApp settings')
    configure.add_argument('--business-number', help='Business number, e.g. +27821234567')
    configure.add_argument('--phone-number-id', help="Meta's numeric phone number ID")
    configure.add_argument('--owner-number', help="Owner number (default: the tenant's owner_number)")
    configure.add_argument('--template', help='Approved template for messages outside the 24-hour window')
    configure.add_argument('--template-language', default='en', help='Template language code (default en)')
    configure.add_argument('--agent-url', help='Optional OpenAI-compatible chat URL for owner free text')
    configure.add_argument('--tenant', help='Tenant slug on a multi-tenant install')
    for name, text in (('check', 'Confirm Meta number and storage'), ('send-test', 'Send a test message to the owner')):
        sub.add_parser(name, help=text).add_argument('--tenant', help='Tenant slug on a multi-tenant install')
    server = sub.add_parser('serve', help='Run the webhook server')
    server.add_argument('--all-tenants', action='store_true', help='Serve every registered tenant at /webhook/<slug>')
    server.add_argument('--host', help='Listen address (default 127.0.0.1)')
    server.add_argument('--port', type=int, help='Listen port (default 8085)')
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
