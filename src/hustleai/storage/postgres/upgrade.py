"""Apply additive hosted schema upgrades without importing local records.

Usage: python -m hustleai.storage.postgres.upgrade
Requires the source checkout and private administrative Supabase settings.
SQL files and their ledger entries commit together. No Zoho requests are made,
and no database exports or local client-mapping copies are created.
"""
import hashlib
import re
from pathlib import Path
from hustleai.config import ROOT
from hustleai.storage.postgres.repository import connect


def apply_upgrades(connection, directory, schema='hustle_private'):
    """Verify all recorded checksums and atomically apply unapplied SQL files.

    The original migration must already be tracked. Unknown ledger versions and
    changed applied files abort. A transaction advisory lock serializes competing
    upgrade processes. Each new file must omit BEGIN/COMMIT; this function owns
    the transaction, including function creation, revokes/grants and the ledger.
    Returns the filenames applied, or an empty list on a clean rerun.
    """
    if not re.fullmatch(r'[a-z_][a-z0-9_]*', schema):
        raise ValueError('Invalid migration schema name.')
    files = sorted(Path(directory).glob('*.sql'))
    scripts = {path.name: path.read_text() for path in files}
    checksums = {name: hashlib.sha256(script.encode()).hexdigest() for name, script in scripts.items()}
    with connection.transaction():
        connection.execute('SELECT pg_advisory_xact_lock(%s)', (728496310426,))
        recorded = dict(connection.execute(f'SELECT version,checksum FROM {schema}.schema_migrations').fetchall())
        if not files or files[0].name not in recorded:
            raise ValueError('Initial schema must be installed and tracked before upgrading.')
        for name, checksum in recorded.items():
            if checksums.get(name) != checksum:
                raise ValueError('Applied migration is missing or has a changed checksum: ' + name)
        applied = []
        for name, script in scripts.items():
            if name in recorded:
                continue
            connection.execute(script.replace('hustle_private', schema))
            connection.execute(f'INSERT INTO {schema}.schema_migrations (version,checksum) VALUES (%s,%s)',
                               (name, checksums[name]))
            applied.append(name)
    return applied


def main():
    """Run reviewed SQL upgrades and print version names without credentials."""
    directory = Path(__file__).resolve().parents[4] / 'supabase' / 'migrations'
    with connect(ROOT, '.supabase-credentials.json') as connection:
        applied = apply_upgrades(connection, directory)
    print('Applied: ' + ', '.join(applied) if applied else 'Schema is up to date; no changes applied.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Schema upgrade stopped (' + type(error).__name__ + '). No credentials printed; inspect the migration before retrying.')
        raise SystemExit(1) from None
