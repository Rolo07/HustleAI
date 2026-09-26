"""Collect Supabase Session pooler credentials privately; do not migrate data."""
import getpass
import json
import os
import tempfile
from urllib.parse import urlsplit, unquote
from hustleai.config import DATA_DIR


def connection_fields(uri, password):
    """Validate the copied URI and return separate PostgreSQL connection fields.

    Args:
        uri: Session pooler URI from the Supabase dashboard, with its password
            placeholder intact. Query parameters are intentionally not imported.
        password: Actual database password, collected in a separate hidden prompt.
    Returns:
        Connection fields; the password is preserved verbatim, including symbols.
    Raises:
        ValueError: Required URI fields, pooler host, port or password are invalid.

    No network calls are made and no credentials are interpolated into shell code.
    TLS verify-full is required by the saved configuration; connection testing
    must use a trusted CA bundle if the local client does not have one.
    """
    try:
        # Supabase's bracketed placeholder is not a valid URI password.
        # Discard URI password text; the separate hidden input is authoritative.
        scheme, remainder = uri.strip().split('://', 1)
        userinfo, address = remainder.rsplit('@', 1)
        username = userinfo.split(':', 1)[0]
        parsed = urlsplit(scheme + '://' + username + '@' + address)
        host = parsed.hostname or ''
        port = parsed.port
        user = unquote(parsed.username or '')
        database = unquote(parsed.path.lstrip('/'))
    except ValueError:
        raise ValueError('Invalid connection URI. Copy the Session pooler URI again.') from None
    if (parsed.scheme not in ('postgres', 'postgresql')
            or not host.endswith('.pooler.supabase.com')
            or port != 5432 or not user or not database or parsed.fragment):
        raise ValueError('Expected a Supabase Session pooler URI on port 5432, including username and database.')
    if not password or password.casefold() in ('[your-password]', 'your-password', '[your_password]'):
        raise ValueError('Enter your actual project database password.')
    return {'host': host, 'port': port, 'dbname': database, 'user': user,
            'password': password, 'sslmode': 'verify-full', 'connect_timeout': 15}


def main():
    """Prompt privately and save connection settings without touching databases.

    Returns: None; prints completion without displaying secret values.
    Raises: SystemExit for existing configuration or invalid input; OSError for
        local file failures. Existing credentials are never overwritten.
    """
    destination = DATA_DIR / '.supabase-credentials.json'
    if destination.exists():
        raise SystemExit('Supabase settings already exist; they have not been overwritten.')
    print('Copy the Session pooler URI with its password placeholder intact.')
    uri = getpass.getpass('Session pooler URI (hidden): ')
    password = getpass.getpass('Project database password (hidden): ')
    try:
        fields = connection_fields(uri, password)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.supabase-setup-', dir=DATA_DIR)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(fields, output, indent=2)
            output.write('\n')
        os.link(temporary, destination)
    finally:
        os.unlink(temporary)
    print('Supabase settings saved privately. Connection is not yet tested.')
    print('No SQL applied and no data migrated. Return to chat without pasting credentials.')


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        raise SystemExit('\nCancelled; no settings saved.') from None
