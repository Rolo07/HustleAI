"""Configure, sync or query the existing local contact index."""
import argparse
import json
import re
from hustleai.config import CONFIG, MAPPING, ROOT
from hustleai.storage.backend import open_repository
from contextlib import nullcontext
from hustleai.integrations.zoho.client import Client
from hustleai.storage.files import private_write
from hustleai.storage.legacy.phone_mapping import sync, lookup

def main():
    """Dispatch the local configure, sync, or lookup command-line action.

    Reads arguments from sys.argv. configure requires organization ID and
    country code, validates them, and saves private local settings. sync and
    lookup load those settings and obtain an OAuth access token; lookup may
    prompt for a number instead of taking it on the command line.

    Returns:
        None; command results are printed to stdout.

    Raises:
        SystemExit: argparse handles invalid arguments or --help.
        ValueError: Settings, required mapping, or an operation is invalid.
        OSError: File/network operations fail.

    The module entry point turns expected errors into concise terminal output.
    configure changes only local settings; none of these commands writes Zoho.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    setup = sub.add_parser('configure')
    setup.add_argument('--organization-id', required=True)
    setup.add_argument('--country-code', required=True, help='e.g. 27 for South Africa')
    sub.add_parser('sync')
    find = sub.add_parser('lookup')
    find.add_argument('phone', nargs='?', help='Omit to enter interactively')
    args = parser.parse_args()
    if args.command == 'configure':
        if not args.organization_id.isdigit() or not re.fullmatch(r'[1-9]\d{0,2}', args.country_code):
            raise ValueError('Organization ID must be digits; country code must be 1–3 digits.')
        config = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
        if config.get('storage_backend') == 'postgres' and config.get('organization_id') != args.organization_id:
            raise ValueError('Hosted organization changes require a separate migration and database role.')
        config.update(organization_id=args.organization_id, country_code=args.country_code)
        private_write(CONFIG, json.dumps(config, indent=2))
        print('Local settings saved. Next: python3 zoho_clients.py sync')
        return
    if not CONFIG.exists():
        raise ValueError('Configure your organization first: python3 zoho_clients.py configure --organization-id YOUR_ID --country-code 27')
    config = json.loads(CONFIG.read_text())
    store = open_repository(config, ROOT)
    try:
        client = Client(config['organization_id'])
        hosted = config.get('storage_backend') == 'postgres'
        if args.command == 'sync':
            lock = store.named_lock('mappings') if hosted else nullcontext()
            with lock:
                sync(client, config['country_code'], store if hosted else None)
        else:
            lookup(client, config['country_code'], args.phone or input('Cellphone number: '), store if hosted else None)
    finally:
        store.close()


def run():
    """Run the CLI and convert expected errors to concise terminal messages."""
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
    except (KeyboardInterrupt, EOFError):
        raise SystemExit('\nCancelled.') from None


if __name__ == '__main__':
    run()
