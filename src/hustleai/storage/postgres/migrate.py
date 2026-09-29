"""One-time, resumable local-to-hosted cutover with a private recovery snapshot.

Run while Hermes and local workflow commands are stopped. A maintenance marker
blocks new service instances; an exclusive SQLite transaction freezes the source.
No Zoho calls are made. Credentials and record bodies never appear in output.
"""
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3

from hustleai.config import ROOT, CONFIG
from hustleai.storage.files import private_write
from hustleai.storage.legacy.repository import SQLiteRepository
from hustleai.storage.postgres.repository import PostgresRepository, connect

SCHEMA = 'hustle_private'
TABLES = ('operations', 'invoice_reviews', 'invoice_approvals')
ROLE = 'hustleai_runtime'


def canonical(value):
    """Serialize JSON deterministically for exact import/source comparisons."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(value):
    """Hash a canonical record set without exposing its personal information."""
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read_source(connection, organization):
    """Read the frozen SQLite journal, converting serialized JSON into objects.

    Rejects other organizations so a single-organization role never silently
    drops foreign history. Invalid/malformed source data aborts before import.
    """
    result = {}
    for table in TABLES:
        cursor = connection.execute(f'SELECT * FROM {table} ORDER BY 1')
        records = [dict(zip((d[0] for d in cursor.description), row)) for row in cursor.fetchall()]
        for record in records:
            if record['org'] != organization:
                raise ValueError('Source contains another organization; separate its migration first.')
            for key in ('payload', 'preview', 'result', 'invoice'):
                if key in record and record[key] is not None:
                    record[key] = json.loads(record[key])
        result[table] = records
    return result


def snapshot(root, connection):
    """Save a consistent SQLite image and private files with a checksum manifest.

    This is a local rollback snapshot, not an off-device disaster-recovery backup.
    All descendants are private; keep it after cutover for reconciliation only.
    """
    directory = root / 'backups' / datetime.now(timezone.utc).strftime('supabase-cutover-%Y%m%dT%H%M%S%fZ')
    directory.mkdir(mode=0o700, parents=True)
    os.chmod(directory.parent, 0o700)
    database = directory / '.zoho-operations.sqlite3'
    fd = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(connection.serialize())
        output.flush()
        os.fsync(output.fileno())
    for name in ('.zoho-local.json', '.zoho-credentials.json', '.supabase-credentials.json',
                 '.supabase-runtime.json', 'prod-ca-2021.crt', 'zoho-client-phone-map.md', 'invoice-pdfs'):
        source = root / name
        if source.is_dir():
            shutil.copytree(source, directory / name)
        elif source.is_file():
            shutil.copy2(source, directory / name)
    manifest = {}
    for path in directory.rglob('*'):
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
        if path.is_file():
            manifest[str(path.relative_to(directory))] = hashlib.sha256(path.read_bytes()).hexdigest()
    private_write(directory / 'manifest.json', json.dumps(manifest, indent=2) + '\n')
    # Verify the saved database image is readable before any hosted changes.
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as saved:
        if saved.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Source snapshot failed SQLite integrity verification.')
    return directory


def snapshot_runtime(root, directory, hosted_config):
    """Add the verified hosted profile to the recovery snapshot without replacing legacy state.

    The top-level configuration remains the original pre-cutover snapshot. The
    hosted-runtime subdirectory contains the new restricted login and selected
    backend for rebuilding this installation. This remains a local backup only.
    """
    target = directory / 'hosted-runtime'
    target.mkdir(mode=0o700, exist_ok=True)
    for name in ('.supabase-runtime.json', 'prod-ca-2021.crt'):
        private_write(target / name, (root / name).read_text())
    private_write(target / '.zoho-local.json', json.dumps(hosted_config, indent=2) + '\n')
    manifest = {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in directory.rglob('*') if path.is_file() and path != directory / 'manifest.json'}
    private_write(directory / 'manifest.json', json.dumps(manifest, indent=2) + '\n')


def apply_schema(admin, migration_path):
    """Apply an exact versioned schema once; reject untracked or changed schemas."""
    script = migration_path.read_text()
    checksum = hashlib.sha256(script.encode()).hexdigest()
    present = admin.execute('SELECT to_regnamespace(%s)', (SCHEMA,)).fetchone()[0]
    if present:
        ledger = admin.execute('SELECT to_regclass(%s)', (SCHEMA + '.schema_migrations',)).fetchone()[0]
        if not ledger:
            raise ValueError('Existing private schema is not tracked; reconcile it before migration.')
        row = admin.execute(f'SELECT checksum FROM {SCHEMA}.schema_migrations WHERE version=%s', (migration_path.name,)).fetchone()
        if not row or row[0] != checksum:
            raise ValueError('Schema migration checksum differs; no changes applied.')
        return
    # Remove outer transaction statements so ledger and schema commit together.
    script = script.replace('BEGIN;', '').replace('COMMIT;', '')
    with admin.transaction():
        admin.execute(script)
        admin.execute(f'CREATE TABLE {SCHEMA}.schema_migrations (version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())')
        admin.execute(f'CREATE TABLE {SCHEMA}.legacy_imports (organization_id text PRIMARY KEY, source_hash text NOT NULL, imported_at timestamptz NOT NULL DEFAULT now())')
        admin.execute(f'INSERT INTO {SCHEMA}.schema_migrations (version,checksum) VALUES (%s,%s)', (migration_path.name, checksum))


def provision_runtime(admin, root, organization):
    """Create a private login limited by grants and RLS to this organization.

    The app role cannot administer schemas or roles and receives no future
    WhatsApp-table grants. Admin settings remain separate for maintenance. A
    previously provisioned role is reused only when its private settings exist.
    """
    from psycopg import sql
    runtime_path = root / '.supabase-runtime.json'
    exists = admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (ROLE,)).fetchone()
    if exists and not runtime_path.exists():
        raise ValueError('Runtime role exists but local settings are missing; restore them before resuming.')
    settings = json.loads((root / '.supabase-credentials.json').read_text())
    if runtime_path.exists():
        runtime = json.loads(runtime_path.read_text())
    else:
        runtime = dict(settings, user=ROLE + '.' + settings['user'].split('.', 1)[1], password=secrets.token_urlsafe(40))
        # Persist first so interrupted provisioning can reuse the exact password.
        private_write(runtime_path, json.dumps(runtime, indent=2) + '\n')
    with admin.transaction():
        if not exists:
            admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS').format(sql.Identifier(ROLE), sql.Literal(runtime['password'])))
        attributes = admin.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=%s', (ROLE,)).fetchone()
        if any(attributes):
            raise ValueError('Runtime role has unexpected administrative privileges.')
        admin.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(SCHEMA), sql.Identifier(ROLE)))
        for table in ('phone_mappings',) + TABLES:
            target = sql.Identifier(SCHEMA, table)
            admin.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON {} TO {}').format(target, sql.Identifier(ROLE)))
            if table == 'phone_mappings':
                admin.execute(sql.SQL('GRANT DELETE ON {} TO {}').format(target, sql.Identifier(ROLE)))
            admin.execute(sql.SQL('DROP POLICY IF EXISTS runtime_organization ON {}').format(target))
            admin.execute(sql.SQL('CREATE POLICY runtime_organization ON {} TO {} USING (organization_id={}) WITH CHECK (organization_id={})').format(
                target, sql.Identifier(ROLE), sql.Literal(organization), sql.Literal(organization)))


def tenant_role_name(slug):
    """Database login name for a tenant slug, e.g. rg-midrand -> hustleai_rt_rg_midrand."""
    if not slug or not all(c.isalnum() or c == '-' for c in slug) or slug != slug.lower():
        raise ValueError('Tenant slugs use lowercase letters, digits and hyphens.')
    return 'hustleai_rt_' + slug.replace('-', '_')


def provision_tenant(admin, admin_settings, tenant_root, slug, organization, schema=SCHEMA):
    """Create or reuse a tenant's restricted login and write its private settings.

    The login inherits hustleai_tenant (all grants live there) and is mapped
    to exactly one organization in tenant_roles, which every table policy
    checks. The password is saved to tenant_root/.supabase-runtime.json
    before the role is created, so an interrupted run can resume. Requires
    migration 202609290006. Returns the login role name.
    """
    from psycopg import sql
    role = tenant_role_name(slug)
    runtime_path = Path(tenant_root) / '.supabase-runtime.json'
    exists = admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone()
    if exists and not runtime_path.exists():
        raise ValueError('Tenant login exists but its settings file is missing; restore it before resuming.')
    if runtime_path.exists():
        runtime = json.loads(runtime_path.read_text())
    else:
        project = admin_settings['user'].split('.', 1)[1] if '.' in admin_settings['user'] else ''
        runtime = dict(admin_settings, user=role + ('.' + project if project else ''),
                       password=secrets.token_urlsafe(40))
        private_write(runtime_path, json.dumps(runtime, indent=2) + '\n')
    with admin.transaction():
        owner = admin.execute(f'SELECT organization_id FROM {schema}.tenant_roles WHERE role_name=%s', (role,)).fetchone()
        if owner and owner[0] != str(organization):
            raise ValueError('This tenant login is already mapped to a different organization.')
        taken = admin.execute(f'SELECT role_name FROM {schema}.tenant_roles WHERE organization_id=%s', (str(organization),)).fetchone()
        if taken and taken[0] != role:
            raise ValueError('That organization already belongs to another tenant login.')
        if not exists:
            admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS').format(
                sql.Identifier(role), sql.Literal(runtime['password'])))
        attributes = admin.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=%s', (role,)).fetchone()
        if any(attributes):
            raise ValueError('Tenant login has unexpected administrative privileges.')
        admin.execute(sql.SQL('GRANT hustleai_tenant TO {}').format(sql.Identifier(role)))
        admin.execute(f'INSERT INTO {schema}.tenant_roles (role_name, organization_id) VALUES (%s,%s) ON CONFLICT (role_name) DO NOTHING',
                      (role, str(organization)))
    return role


def import_records(admin, organization, source, mappings):
    """Import history atomically, preserve attempted states and revoke approvals.

    A ledger prevents duplicate import. Existing hosted rows without a matching
    ledger abort instead of overwriting records. Mapping verification dates remain
    unknown until a fresh complete Zoho sync; importing is not live verification.
    """
    source_hash = digest(dict(source, mappings=mappings))
    expected = json.loads(json.dumps(source))
    for table in ('invoice_reviews', 'invoice_approvals'):
        for row in expected[table]:
            row['status'] = 'invalid'
    store = PostgresRepository(ROOT, organization, admin)
    with admin.transaction():
        prior = admin.execute(f'SELECT source_hash FROM {SCHEMA}.legacy_imports WHERE organization_id=%s', (organization,)).fetchone()
        if prior:
            if prior[0] != source_hash:
                raise ValueError('Source changed since import; reconcile before cutover.')
        else:
            for table in ('phone_mappings',) + TABLES:
                count = admin.execute(f'SELECT count(*) FROM {SCHEMA}.{table} WHERE organization_id=%s', (organization,)).fetchone()[0]
                if count:
                    raise ValueError('Hosted target contains untracked records; import will not overwrite them.')
            for table in TABLES:
                for record in expected[table]:
                    store.insert(table, record)
            for cid, phone in mappings:
                store.add_mapping(cid, phone)
            admin.execute(f'INSERT INTO {SCHEMA}.legacy_imports (organization_id,source_hash) VALUES (%s,%s)', (organization, source_hash))
        for table in TABLES:
            rows = []
            cursor = admin.execute(f'SELECT * FROM {SCHEMA}.{table} WHERE organization_id=%s ORDER BY 1', (organization,))
            while (record := store.row(cursor)) is not None:
                rows.append(record)
            # PostgreSQL timestamps retain microseconds; legacy floats may hold
            # sub-microsecond precision. Compare to the same normalized precision.
            wanted = expected[table]
            for record in wanted:
                record['created'] = store.decode_time(store.encode_time(record['created']))
            if digest(rows) != digest(wanted):
                raise ValueError('Imported record comparison failed for ' + table)
        if store.mapping_rows() != sorted(mappings):
            raise ValueError('Imported phone mappings differ from the source.')
    return {table: len(source[table]) for table in TABLES} | {'phone_mappings': len(mappings)}


def verify_runtime(root, organization, counts):
    """Check restricted-role reads, organization RLS and denied schema creation."""
    from psycopg.errors import InsufficientPrivilege
    with connect(root) as runtime:
        for table, count in counts.items():
            actual = runtime.execute(f'SELECT count(*) FROM {SCHEMA}.{table} WHERE organization_id=%s', (organization,)).fetchone()[0]
            if actual != count:
                raise ValueError('Runtime count verification failed for ' + table)
        # Always roll back synthetic permission probes, even if a grant is wrong.
        denied = False
        try:
            with runtime.transaction(force_rollback=True):
                runtime.execute(f'INSERT INTO {SCHEMA}.phone_mappings VALUES (%s,%s,%s,NULL)', ('other-organization', '1', '+27000000000'))
        except InsufficientPrivilege:
            denied = True
        if not denied:
            raise ValueError('Runtime RLS allowed another organization.')
        denied = False
        try:
            with runtime.transaction(force_rollback=True):
                runtime.execute(f'CREATE TABLE {SCHEMA}.unexpected_runtime_ddl (id text)')
        except InsufficientPrivilege:
            denied = True
        if not denied:
            raise ValueError('Runtime role unexpectedly allowed schema changes.')


def main():
    """Back up, migrate, verify and atomically select PostgreSQL; print counts only.

    A failure leaves the previous backend configured and retains the maintenance
    marker for review. Rerun this command to resume a matching import. Do not
    manually fall back to SQLite after hosted workflows have begun writing.
    """
    config = json.loads(CONFIG.read_text())
    if config.get('storage_backend') == 'postgres':
        marker = ROOT / '.storage-maintenance'
        if marker.exists():
            if not marker.read_text().startswith('Supabase migration in progress.'):
                raise ValueError('Unrelated storage maintenance is active; do not clear it with the migration command.')
            # Recover a crash between durable config replacement and marker
            # removal. No source import or approval restoration is performed.
            with connect(ROOT) as runtime:
                runtime.execute(f'SELECT id FROM {SCHEMA}.operations LIMIT 1')
            marker.unlink()
        print('PostgreSQL is already selected; no source import was repeated.')
        return
    organization = str(config['organization_id'])
    lock_path = ROOT / '.supabase-migration.lock'
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        marker = ROOT / '.storage-maintenance'
        private_write(marker, 'Supabase migration in progress. Stop all workflow processes before resuming.\n')
        source = sqlite3.connect(ROOT / '.zoho-operations.sqlite3', timeout=10)
        try:
            source.execute('BEGIN EXCLUSIVE')
            records = read_source(source, organization)
            # Read the legacy index without opening a second SQLite connection.
            mapping = object.__new__(SQLiteRepository)
            mapping.mapping = ROOT / 'zoho-client-phone-map.md'
            mapping.organization = organization
            mappings = mapping.mapping_rows()
            backup = snapshot(ROOT, source)
            with connect(ROOT, '.supabase-credentials.json') as admin:
                apply_schema(admin, Path(__file__).resolve().parents[4] / 'supabase/migrations/202609260001_workflow_storage.sql')
                provision_runtime(admin, ROOT, organization)
                from hustleai.storage.postgres.upgrade import apply_upgrades
                apply_upgrades(admin, Path(__file__).resolve().parents[4] / 'supabase/migrations')
                counts = import_records(admin, organization, records, mappings)
            verify_runtime(ROOT, organization, counts)
            config['storage_backend'] = 'postgres'
            snapshot_runtime(ROOT, backup, config)
            private_write(CONFIG, json.dumps(config, indent=2) + '\n')
            marker.unlink()
            print(json.dumps({'backend': 'postgres', 'verified_counts': counts, 'backup': str(backup.relative_to(ROOT)),
                'old_reviews_and_approvals': 'invalidated', 'zoho_writes': 0}, indent=2))
        finally:
            source.rollback()
            source.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Database exceptions can embed SQL values; do not echo them to the chat.
        print('Migration stopped (' + type(error).__name__ + '). Inspect configuration and maintenance state before resuming; no credentials were printed.')
        raise SystemExit(1) from None
