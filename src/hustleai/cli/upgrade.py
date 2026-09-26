"""Upgrade local OAuth grant without exposing or retyping client credentials."""
import getpass
import json
import urllib.parse
import urllib.request
from hustleai.config import ROOT
from hustleai.storage.files import private_write
from hustleai.integrations.zoho.auth import tls_context

SCOPES = 'ZohoInvoice.contacts.READ,ZohoInvoice.contacts.CREATE,ZohoInvoice.settings.READ,ZohoInvoice.invoices.READ,ZohoInvoice.invoices.CREATE,ZohoInvoice.invoices.UPDATE,ZohoInvoice.customerpayments.READ,ZohoInvoice.customerpayments.CREATE'


def main():
    """Replace the local refresh token after an expanded-scope authorization.

    Reads existing client credentials, prints the required SCOPES, and prompts
    for a fresh hidden grant code from the same Zoho Self Client. The user
    must authorize the displayed scopes in Zoho; this script cannot grant them.

    Returns:
        None; prints success after saving, without exposing credential values.

    Raises:
        SystemExit: No code is supplied or the token exchange is unsuccessful.
        OSError: Reading or atomically saving local credentials fails.
        KeyboardInterrupt: The user cancels (handled by the entry point).

    Side Effects:
        Exchanges the one-time code and atomically replaces saved credentials
        only after a refresh token is returned. Existing credentials survive
        exchange failure. Success indicates token exchange, not verification
        of every endpoint permission; those must be tested separately.
    """
    path = ROOT / '.zoho-credentials.json'
    credentials = json.loads(path.read_text())
    print('In the SAME Zoho Self Client, generate a 10-minute code with these scopes:')
    print(SCOPES)
    code = getpass.getpass('New generated code (hidden): ').strip()
    if not code:
        raise SystemExit('No code supplied. Existing credentials unchanged.')
    data = urllib.parse.urlencode({'client_id': credentials['client_id'],
                                  'client_secret': credentials['client_secret'],
                                  'grant_type': 'authorization_code', 'code': code}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(credentials['accounts_url'] + '/oauth/v2/token', data=data),
                                    context=tls_context(), timeout=30) as response:
            result = json.load(response)
        if not result.get('refresh_token'):
            raise ValueError('No refresh token')
    except Exception:
        raise SystemExit('Upgrade failed. Existing credentials unchanged. Check scopes and generate a fresh code.') from None
    credentials['refresh_token'] = result['refresh_token']
    credentials['api_domain'] = result.get('api_domain', credentials['api_domain'])
    private_write(path, json.dumps(credentials, indent=2))
    print('Permissions upgraded. Credentials saved privately.')


def run():
    """Run permission upgrade and handle interactive cancellation."""
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        raise SystemExit('\nCancelled; existing credentials unchanged.') from None


if __name__ == "__main__":
    run()
