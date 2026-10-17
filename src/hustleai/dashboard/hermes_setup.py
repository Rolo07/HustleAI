"""Secure local Hermes settings and a tool-policy connectivity check."""
from getpass import getpass
import json
import os
from pathlib import Path
import urllib.request
from urllib.parse import urlsplit

from hustleai.cli.tenant import set_env
from hustleai.integrations.whatsapp.test_assistant import load_env
from hustleai.integrations.zoho.auth import tls_context

PATH = '/p/rg-midrand-customer/v1/chat/completions'


def check_restricted(url, key, profile_mode='restricted'):
    parsed = urlsplit(url)
    paths = {'restricted': PATH, 'existing-restricted': '/v1/chat/completions'}
    if profile_mode not in paths or parsed.path != paths[profile_mode] or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Use the explicitly configured restricted Hermes endpoint without embedded credentials.')
    if profile_mode == 'existing-restricted' and parsed.hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('The existing restricted Hermes API must be on localhost.')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')):
        raise ValueError('Use HTTPS, or localhost HTTP through a private SSH tunnel.')
    if not key:
        raise ValueError('Hermes API key is missing.')
    request = urllib.request.Request(url.rsplit('/chat/completions', 1)[0] + '/toolsets',
                                     headers={'Authorization': 'Bearer ' + key})
    try:
        with urllib.request.urlopen(request, timeout=15, context=tls_context()) as response:
            payload = json.load(response)
    except Exception:
        raise ValueError('Cannot verify Hermes tool policy. No customer message was sent to it.') from None
    rows = payload.get('data') if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) or row.get('enabled') is not False for row in rows):
        raise ValueError('Hermes profile is not verified tool-free; disable every API-server toolset before connecting.')
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('configure-local', 'check', 'create-vps-key'))
    args = parser.parse_args()
    if args.command == 'create-vps-key':
        import secrets
        profile = Path.home() / '.hermes' / 'profiles' / 'rg-midrand-customer'
        if not profile.is_dir():
            raise ValueError('Create the dedicated Hermes profile first.')
        path = profile / '.env'
        from hustleai.cli.tenant import read_env
        if read_env(path).get('API_SERVER_KEY'):
            raise ValueError('Customer profile key already exists; no credential was overwritten.')
        set_env(path, {'API_SERVER_ENABLED': 'true', 'API_SERVER_HOST': '127.0.0.1',
                       'API_SERVER_PORT': '8643', 'API_SERVER_KEY': secrets.token_urlsafe(40)})
        print('Created private customer API key in the profile environment. Nothing was printed or exposed publicly.')
        return
    load_env(Path('.env'))
    if args.command == 'configure-local':
        url = input('Customer Hermes URL [http://127.0.0.1:18643' + PATH + ']: ').strip() or 'http://127.0.0.1:18643' + PATH
        key = getpass('Customer profile API_SERVER_KEY (hidden; copied locally from VPS private file): ').strip()
        check_restricted(url, key)
        set_env(Path('.env'), {'HUSTLEAI_HERMES_URL': url, 'HUSTLEAI_HERMES_API_KEY': key,
                              'HUSTLEAI_HERMES_CUSTOMER_PROFILE': 'restricted'})
        print('Saved authenticated, tool-free customer profile connection privately.')
    else:
        check_restricted(os.getenv('HUSTLEAI_HERMES_URL', ''), os.getenv('HUSTLEAI_HERMES_API_KEY', ''),
                         os.getenv('HUSTLEAI_HERMES_CUSTOMER_PROFILE', 'restricted'))
        print('Customer Hermes profile reachable; API-server toolsets disabled.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
