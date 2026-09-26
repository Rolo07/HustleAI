"""Interactive initial Zoho Self Client authorization."""
import getpass
import json
import os
import ssl
from pathlib import Path
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from hustleai.config import ROOT
from hustleai.integrations.zoho.auth import tls_context

def main():
    """Interactively exchange a Self Client grant for private OAuth credentials.

    Reads client ID, client secret and a short-lived authorization code using
    hidden prompts. This setup targets accounts.zoho.com (US data center).

    Returns:
        None; prints progress and a completion message without token values.

    Raises:
        SystemExit: Credentials already exist, input is missing, or the token
            exchange fails. Existing credentials are never overwritten.
        OSError: Local credential-file creation or publication fails.
        KeyboardInterrupt: The user cancels (handled by the entry point).

    Side Effects:
        Consumes the grant code via an OAuth POST. Writes the refresh token,
        client credentials and endpoint metadata to a mode-0600 temporary
        file, then publishes it with a non-overwriting hard link. An exchange
        can succeed even if local saving fails; never blindly reuse its code.
    """
    destination = ROOT / '.zoho-credentials.json'
    if destination.exists():
        raise SystemExit('Credentials already exist; setup stopped to preserve them.')
    print('Zoho Invoice Self Client setup (US data center).')
    client_id = getpass.getpass('Client ID (hidden): ').strip()
    secret = getpass.getpass('Client secret (hidden): ').strip()
    print('Now generate a code with scope ZohoInvoice.contacts.READ and 10-minute duration.')
    code = getpass.getpass('Generated code (hidden): ').strip()
    if not all((client_id, secret, code)):
        raise SystemExit('All three values are required. No credentials saved.')
    body = urllib.parse.urlencode({
        'client_id': client_id, 'client_secret': secret,
        'code': code, 'grant_type': 'authorization_code',
    }).encode()
    request = urllib.request.Request(
        'https://accounts.zoho.com/oauth/v2/token', data=body, method='POST'
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=tls_context()) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(f'Zoho returned HTTP {error.code}. No credentials saved.') from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            message = 'HTTPS certificate verification failed. Python needs a working CA certificate bundle.'
        else:
            message = 'Could not connect to Zoho. Check your internet connection, proxy, or firewall.'
        raise SystemExit(message + ' No credentials saved.') from None
    except TimeoutError:
        raise SystemExit('Zoho connection timed out. No credentials saved; retry with a fresh code.') from None
    except ValueError:
        raise SystemExit('Zoho returned an unreadable response. No credentials saved; retry with a fresh code.') from None
    if not result.get('refresh_token'):
        raise SystemExit('Zoho did not return a refresh token. Check the client credentials and generate a fresh code.')
    credentials = {
        'client_id': client_id, 'client_secret': secret,
        'refresh_token': result['refresh_token'],
        'accounts_url': 'https://accounts.zoho.com',
        'api_domain': result.get('api_domain', 'https://www.zohoapis.com'),
    }
    # Create a private file, then publish it without overwriting existing credentials.
    fd, temporary = tempfile.mkstemp(prefix='.zoho-setup-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(credentials, output, indent=2)
            output.write('\n')
        os.link(temporary, destination)
    finally:
        os.unlink(temporary)
    print('Refresh token saved privately in .zoho-credentials.json.')
    print('Setup complete. You can now return to the chat; do not paste the credentials.')


def run():
    """Run setup while keeping cancellation messages free of credentials."""
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        raise SystemExit('\nSetup cancelled.') from None


if __name__ == '__main__':
    run()
