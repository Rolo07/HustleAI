"""Create and manage tenants on a multi-tenant install, including Hermes setup.

  hustleai-tenant list
  hustleai-tenant create <slug> --name ... --owner-name ... --owner-number ... --organization-id ...
  hustleai-tenant import-legacy <slug> --from <old data folder> --name ... --owner-name ...
  hustleai-tenant hermes-install <slug> [--dry-run]
  hustleai-tenant run-job <slug> orders-sync|forecast
  hustleai-tenant bootstrap <slug>     ask for every setting and secret on this server

Each tenant is a private folder under HUSTLEAI_TENANTS_ROOT (default
/var/lib/hustleai/tenants) holding tenant.json and its own secrets.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

from hustleai.config import ROOT, TENANTS_ROOT, tenant_dir
from hustleai.storage.files import private_write
from hustleai.tenant import FEATURES, TENANT_FILE, Tenant, load_tenant, read_settings

HERMES_PACKAGE = Path(__file__).resolve().parents[1] / 'hermes'
JOBS = {'orders-sync': ('orders_sync', ['-m', 'hustleai.cli.orders', 'sync'], '0 22 * * *'),
        'forecast': ('forecast', ['-m', 'hustleai.cli.forecast', 'run', '--send'], '0 7 * * 1')}
DISABLED_TOOLSETS = ['terminal', 'file', 'code_execution', 'browser', 'delegation']
API_PORT = 8642


def vat_value(text):
    """Parse --vat-registered yes|no|unknown."""
    return {'yes': True, 'no': False, 'unknown': None}[text]


def make_private_folder(folder):
    """Create a tenant folder (and the tenants root) closed to other users."""
    folder.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.mkdir(mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)


def tenant_settings(args, base=None):
    """Build and validate tenant.json content from arguments over a base dict."""
    settings = dict(base or {})
    for key in ('name', 'owner_name', 'owner_number', 'organization_id', 'country_code', 'currency', 'timezone'):
        value = getattr(args, key, None)
        if value:
            settings[key] = value
    if getattr(args, 'payment_terms', None) is not None:
        settings['payment_terms_days'] = args.payment_terms
    if getattr(args, 'vat_registered', None):
        settings['vat_registered'] = vat_value(args.vat_registered)
    if getattr(args, 'features', None):
        settings['features'] = [f.strip() for f in args.features.split(',') if f.strip()]
    if base is None:  # New tenants use hosted storage; imports keep their backend.
        settings.setdefault('storage_backend', 'postgres')
    for required in ('name', 'owner_name', 'owner_number', 'organization_id'):
        if not settings.get(required):
            raise ValueError(f'--{required.replace("_", "-")} is required.')
    Tenant.from_settings(settings, slug=args.slug)  # Validate before writing anything.
    return settings


def list_command(_args):
    """Print every tenant and any folder that was skipped."""
    from hustleai.integrations.whatsapp import config as wa_config
    from hustleai.tenants import list_tenants
    tenants, skipped = list_tenants()
    if not tenants and not skipped:
        print(f'No tenants under {TENANTS_ROOT}.')
    for slug, (tenant, folder) in tenants.items():
        whatsapp = 'WhatsApp set up' if wa_config.configured(folder) else 'no WhatsApp'
        print(f"{slug}: {tenant.name} - owner {tenant.owner_name} {tenant.owner_number} - "
              f"{', '.join(sorted(tenant.features))} - {whatsapp}")
    for slug, problem in skipped.items():
        print(f'{slug}: skipped ({problem})')


def create_command(args):
    """Create a tenant folder, tenant.json and its own database login."""
    folder = tenant_dir(args.slug)
    if (folder / TENANT_FILE).exists():
        raise ValueError(f'Tenant {args.slug} already exists at {folder}.')
    settings = tenant_settings(args)
    make_private_folder(folder)
    private_write(folder / TENANT_FILE, json.dumps(settings, indent=2) + '\n')
    print(f'Created {folder}.')
    if args.no_database:
        print('Database login not created (--no-database).')
    else:
        from hustleai.storage.postgres.migrate import provision_tenant
        from hustleai.storage.postgres.repository import connect
        admin_root = Path(args.admin_root or ROOT)
        admin_settings = json.loads((admin_root / '.supabase-credentials.json').read_text())
        with connect(admin_root, '.supabase-credentials.json') as admin:
            role = provision_tenant(admin, admin_settings, folder, args.slug, settings['organization_id'])
        print(f'Database login {role} created and limited to organization {settings["organization_id"]}.')
    print('Next: connect Zoho with HUSTLEAI_DATA_DIR=' + str(folder) + ' hustleai-zoho-setup, '
          f'then hustleai-whatsapp setup --tenant {args.slug} and hustleai-tenant hermes-install {args.slug}.')


def import_legacy_command(args):
    """Copy a single-tenant data folder into a new tenant folder (tenant 1)."""
    source = Path(args.source).expanduser().resolve()
    legacy = read_settings(source)
    if not legacy.get('organization_id'):
        raise ValueError(f'No .zoho-local.json with an organization ID in {source}.')
    folder = tenant_dir(args.slug)
    if (folder / TENANT_FILE).exists():
        raise ValueError(f'Tenant {args.slug} already exists; nothing was copied.')
    settings = tenant_settings(args, base={k: v for k, v in legacy.items() if k != 'slug'})
    make_private_folder(folder)
    copied = []
    for name in ('.zoho-credentials.json', '.supabase-runtime.json', '.whatsapp.json'):
        if (source / name).is_file():
            shutil.copy2(source / name, folder / name)
            os.chmod(folder / name, 0o600)
            copied.append(name)
    for name in ('invoice-pdfs', 'reports'):
        if (source / name).is_dir():
            shutil.copytree(source / name, folder / name, dirs_exist_ok=True)
            copied.append(name + '/')
    private_write(folder / TENANT_FILE, json.dumps(settings, indent=2) + '\n')
    print(f"Imported into {folder}: {', '.join(copied) or 'no files'}. The source folder was not changed.")
    from hustleai.storage.backend import open_repository
    store = open_repository(read_settings(folder), folder)
    try:
        orders = len(store.orders()) if hasattr(store, 'orders') else 0
        sync = store.last_sync('orders') if hasattr(store, 'last_sync') else None
    finally:
        store.close()
    print(f'Verified through the tenant folder: {orders} orders visible'
          + (f", last sync {datetime.fromtimestamp(sync['finished']).isoformat(timespec='minutes')}." if sync else '.'))


def run_job(slug, job, root=None):
    """Run one scheduled job for one tenant in its own folder.

    Silent on success so Hermes cron stays quiet; prints a short error and
    returns 1 on failure so Hermes reports it. Each run is logged to the
    tenant's reports/jobs.log with the tenant's local time.
    """
    from hustleai.tenants import tenant_by_slug
    feature, module_args, _ = JOBS[job]
    tenant, folder = tenant_by_slug(slug, root)
    if not tenant.has(feature):
        return 0
    env = dict(os.environ, HUSTLEAI_DATA_DIR=str(folder))
    result = subprocess.run([sys.executable] + module_args, env=env, capture_output=True, text=True, timeout=3600)
    lines = (result.stdout + result.stderr).strip().splitlines()
    last = lines[-1] if lines else ''
    reports = folder / 'reports'
    reports.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(tenant.zone).isoformat(timespec='seconds')
    with open(reports / 'jobs.log', 'a') as log:
        log.write(f'{stamp} {job} exit={result.returncode} {last[:300]}\n')
    if result.returncode:
        print(f'{slug} {job} failed: {last[:300] or "no output"}')
        return 1
    return 0


def merge_yaml(path, update):
    """Merge nested settings into a YAML file, keeping everything else."""
    import yaml
    data = yaml.safe_load(path.read_text()) if path.exists() else None
    data = data if isinstance(data, dict) else {}

    def merge(target, source):
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = value
    merge(data, update)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def set_env(path, values):
    """Set KEY=value lines in a .env file, keeping other lines; mode 0600."""
    lines = path.read_text().splitlines() if path.exists() else []
    kept = [line for line in lines if line.split('=', 1)[0].strip() not in values]
    kept += [f'{key}={value}' for key, value in values.items()]
    private_write(path, '\n'.join(kept) + '\n')


def read_env(path):
    """Read KEY=value lines from a .env file."""
    if not path.exists():
        return {}
    return dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line and not line.startswith('#'))


def hermes_install(slug, hermes='hermes', hermes_root=None, tenants_root=None, dry_run=False, log=print):
    """Create or update the tenant's Hermes profile. Safe to run again.

    Profile: SOUL.md, the hustleai MCP server bound to the tenant folder,
    dangerous toolsets disabled, the API server with its own key, the
    tenant's timezone, and script-only cron jobs for the nightly orders sync
    and the Monday forecast. Returns the list of hermes commands run.
    """
    from hustleai.tenants import tenant_by_slug
    tenant, folder = tenant_by_slug(slug, tenants_root)
    hermes_root = Path(hermes_root or Path.home() / '.hermes')
    profile = hermes_root / 'profiles' / slug
    commands = []

    def run(command):
        commands.append(command)
        log('$ ' + ' '.join(command))
        if not dry_run:
            subprocess.run(command, check=True)

    def write(path, text, mode=0o600):
        log(f'write {path}')
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            private_write(path, text)
            os.chmod(path, mode)

    if not profile.exists():
        run([hermes, 'profile', 'create', slug])
    soul = (HERMES_PACKAGE / 'profile' / 'SOUL.md.tmpl').read_text().format(
        owner_name=tenant.owner_name, business_suffix=f' at {tenant.name}' if tenant.name else '')
    write(profile / 'SOUL.md', soul)
    log(f'merge {profile / "config.yaml"}')
    if not dry_run:
        profile.mkdir(parents=True, exist_ok=True)
        current = []
        try:
            import yaml
            existing = yaml.safe_load((profile / 'config.yaml').read_text()) if (profile / 'config.yaml').exists() else {}
            current = ((existing or {}).get('agent') or {}).get('disabled_toolsets') or []
        except OSError:
            pass
        merge_yaml(profile / 'config.yaml', {
            'mcp_servers': {'hustleai': {'command': sys.executable, 'args': ['-m', 'hustleai.mcp.owner_server'],
                                         'env': {'HUSTLEAI_DATA_DIR': str(folder)}}},
            'agent': {'disabled_toolsets': sorted(set(current) | set(DISABLED_TOOLSETS))}})
    env_path = profile / '.env'
    key = read_env(env_path).get('API_SERVER_KEY') or 'sk-' + secrets.token_urlsafe(32)
    log(f'set API_SERVER_ENABLED, API_SERVER_KEY, HERMES_TIMEZONE={tenant.timezone} in {env_path}')
    if not dry_run:
        set_env(env_path, {'API_SERVER_ENABLED': 'true', 'API_SERVER_KEY': key, 'HERMES_TIMEZONE': tenant.timezone})
    whatsapp = folder / '.whatsapp.json'
    url = f'http://127.0.0.1:{API_PORT}/p/{slug}/v1/chat/completions'
    if whatsapp.exists():
        log(f'point the gateway owner assistant at {url}')
        if not dry_run:
            settings = json.loads(whatsapp.read_text())
            settings['owner_agent'] = {'url': url, 'api_key': key, 'model': slug}
            private_write(whatsapp, json.dumps(settings, indent=2) + '\n')
    else:
        log(f'WhatsApp not set up for {slug}; rerun hermes-install after hustleai-whatsapp setup --tenant {slug}.')
    tenant_command = Path(sys.executable).parent / 'hustleai-tenant'
    tenants_root_text = str(Path(tenants_root or TENANTS_ROOT))
    template = (HERMES_PACKAGE / 'scripts' / 'job.sh.tmpl').read_text()
    marker = profile / 'hustleai-install.json'
    installed = json.loads(marker.read_text()) if marker.exists() else {'cron': []}
    for job, (feature, _, schedule) in JOBS.items():
        if not tenant.has(feature):
            continue
        script = f'hustleai-{job}.sh'
        write(profile / 'scripts' / script, template.format(slug=slug, job=job, tenants_root=tenants_root_text,
                                                            tenant_command=tenant_command), mode=0o700)
        if job not in installed['cron']:
            run([hermes, '-p', slug, 'cron', 'create', schedule, '--no-agent', '--script', script, '--deliver', 'local'])
            installed['cron'].append(job)
    # Hermes discovers directory plugins in ~/.hermes/plugins; HustleAI lives
    # in its own virtual environment, so link the plugin folder there.
    link = hermes_root / 'plugins' / 'hustleai'
    if not link.exists():
        log(f'link {link} -> {HERMES_PACKAGE}')
        if not dry_run:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(HERMES_PACKAGE, target_is_directory=True)
    run([hermes, 'plugins', 'enable', 'hustleai'])
    run([hermes, 'config', 'set', 'gateway.multiplex_profiles', 'true'])
    if not dry_run:
        private_write(marker, json.dumps(installed, indent=2) + '\n')
    return commands


REPO_ROOT = Path(__file__).resolve().parents[3]
ZOHO_REGIONS = {'com': 'https://accounts.zoho.com', 'eu': 'https://accounts.zoho.eu',
                'in': 'https://accounts.zoho.in', 'com.au': 'https://accounts.zoho.com.au',
                'jp': 'https://accounts.zoho.jp', 'ca': 'https://accounts.zohocloud.ca',
                'sa': 'https://accounts.zoho.sa'}
ZOHO_API = {'com': 'https://www.zohoapis.com', 'eu': 'https://www.zohoapis.eu', 'in': 'https://www.zohoapis.in',
            'com.au': 'https://www.zohoapis.com.au', 'jp': 'https://www.zohoapis.jp',
            'ca': 'https://www.zohoapis.ca', 'sa': 'https://www.zohoapis.sa'}


def prompt(label, default='', secret=False, required=True):
    """Ask one question; Enter accepts the default. Secrets are hidden."""
    from getpass import getpass
    shown = ' [saved]' if secret and default else (f' [{default}]' if default else '')
    while True:
        value = (getpass if secret else input)(f'{label}{shown}: ').strip() or default
        if value or not required:
            return value
        print('  This value is required.')


def agree(label, default=False):
    """Ask a yes/no question."""
    answer = input(f"{label} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not answer else answer.startswith('y')


def zoho_exchange(accounts_url, client_id, client_secret, code):
    """Exchange a Self Client grant code for tokens; raise ValueError on failure."""
    import urllib.error
    import urllib.parse
    import urllib.request
    from hustleai.integrations.zoho.auth import tls_context
    body = urllib.parse.urlencode({'client_id': client_id, 'client_secret': client_secret, 'code': code,
                                   'grant_type': 'authorization_code'}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(accounts_url + '/oauth/v2/token', data=body),
                                    context=tls_context(), timeout=30) as response:
            result = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise ValueError('Could not reach Zoho or read its reply. Generate a fresh code and try again.') from None
    if not result.get('refresh_token'):
        raise ValueError('Zoho gave no refresh token (' + str(result.get('error') or 'unknown error')
                         + '). Codes expire after 10 minutes and work once; generate a fresh one.')
    return result


def bootstrap_business(slug, folder):
    """Ask for the business settings and save tenant.json (step 1)."""
    current = read_settings(folder) if (folder / TENANT_FILE).exists() else {}
    if current and not agree('Business settings exist. Change them?'):
        return current
    defaults = {'name': 'RG Midrand', 'owner_name': 'Roland', 'owner_number': '+27837758811',
                'organization_id': '', 'currency': 'ZAR', 'timezone': 'Africa/Johannesburg',
                'payment_terms_days': 7, 'vat_registered': False, **current}
    print('\n1. Business details')
    settings = dict(current)
    settings['name'] = prompt('Business name', defaults['name'])
    settings['owner_name'] = prompt("Owner's first name (used in messages)", defaults['owner_name'])
    settings['owner_number'] = prompt("Owner's WhatsApp number", defaults['owner_number'])
    settings['organization_id'] = prompt('Zoho organization ID (Zoho Invoice > Settings > Organization profile)',
                                         defaults['organization_id'])
    settings['currency'] = prompt('Invoice currency', defaults['currency'])
    settings['timezone'] = prompt('Timezone', defaults['timezone'])
    settings['payment_terms_days'] = int(prompt('Days until invoices are due', str(defaults['payment_terms_days'])))
    settings['vat_registered'] = agree('Is the business VAT-registered?', defaults['vat_registered'] is True)
    settings.setdefault('storage_backend', 'postgres')
    settings.setdefault('features', list(FEATURES))
    Tenant.from_settings(settings, slug=slug)
    make_private_folder(folder)
    private_write(folder / TENANT_FILE, json.dumps(settings, indent=2) + '\n')
    print(f'  Saved {folder / TENANT_FILE}.')
    return settings


def bootstrap_database(slug, folder, organization, admin_dir, ca_cert):
    """Connect to Supabase as admin, apply upgrades and create the tenant login (step 2)."""
    from hustleai.cli.supabase_setup import connection_fields
    from hustleai.storage.postgres.migrate import organization_login, provision_tenant, tenant_role_name
    from hustleai.storage.postgres.repository import connect
    from hustleai.storage.postgres.upgrade import apply_upgrades
    if (folder / '.supabase-runtime.json').exists() and not agree('This business already has a database login. Recreate it?'):
        return
    (folder / '.supabase-runtime.json').unlink(missing_ok=True)
    print('\n2. Supabase database')
    print('  In Supabase: Project > Connect > Session pooler. Copy the URI (it has [YOUR-PASSWORD] in it).')
    admin_file = admin_dir / '.supabase-credentials.json'
    saved = json.loads(admin_file.read_text()) if admin_file.exists() else {}
    if saved and agree('Use the saved database admin login?', True):
        fields = saved
    else:
        uri = prompt('Session pooler URI', secret=True)
        password = prompt('Database password', secret=True)
        fields = connection_fields(uri, password)
    fields['sslrootcert'] = str(ca_cert)
    make_private_folder(admin_dir)
    private_write(admin_file, json.dumps(fields, indent=2) + '\n')
    try:
        admin = connect(admin_dir, '.supabase-credentials.json')
    except ValueError:
        admin_file.unlink(missing_ok=True)
        raise ValueError('Could not connect to Supabase with those details. Nothing was saved.') from None
    with admin:
        applied = apply_upgrades(admin, REPO_ROOT / 'supabase' / 'migrations')
        print('  Schema: ' + ('applied ' + ', '.join(applied) if applied else 'up to date') + '.')
        role = tenant_role_name(slug)
        owner = organization_login(admin, organization)
        reassign = False
        if owner and owner != role:
            print(f'  Organization {organization} is currently used by the database login "{owner}"'
                  ' (the old single-server install, e.g. the Mac).')
            if not agree('  Move it to this server? The old login will lose access to its data.'):
                raise ValueError('Stopped: the organization stays with the old login.')
            reassign = True
        provision_tenant(admin, fields, folder, slug, organization, reassign=reassign)
    print(f'  Database login {role} created; it can only see organization {organization}.')
    if not agree('Keep the database admin login on this server? (only needed to add businesses or apply upgrades)'):
        admin_file.unlink(missing_ok=True)
        print('  Admin login removed from this server.')


def bootstrap_zoho(folder, organization):
    """Connect Zoho with a fresh Self Client code and verify it (step 3)."""
    from hustleai.cli.upgrade import SCOPES
    from hustleai.integrations.zoho.client import Client
    path = folder / '.zoho-credentials.json'
    if path.exists() and not agree('Zoho is already connected. Reconnect it?'):
        return
    print('\n3. Zoho Invoice')
    print('  In a browser (a phone works): https://api-console.zoho.com > your Self Client.')
    print('  Client Secret tab: copy the Client ID and Client Secret.')
    print('  Generate Code tab: paste these scopes, choose 10 minutes, and create a code:')
    print('  ' + SCOPES)
    region = prompt('Zoho data centre (' + ', '.join(ZOHO_REGIONS) + ')', 'com')
    if region not in ZOHO_REGIONS:
        raise ValueError('Unknown Zoho data centre.')
    client_id = prompt('Client ID', secret=True)
    client_secret = prompt('Client Secret', secret=True)
    code = prompt('Generated code', secret=True)
    result = zoho_exchange(ZOHO_REGIONS[region], client_id, client_secret, code)
    credentials = {'client_id': client_id, 'client_secret': client_secret, 'refresh_token': result['refresh_token'],
                   'accounts_url': ZOHO_REGIONS[region], 'api_domain': result.get('api_domain', ZOHO_API[region])}
    private_write(path, json.dumps(credentials, indent=2) + '\n')
    try:
        Client(organization, folder).get('contacts?per_page=1')
    except ValueError as error:
        raise ValueError(f'Zoho connected, but reading organization {organization} failed: {error}') from None
    print('  Zoho connected and verified.')


def bootstrap_command(args, ca_cert=None, admin_dir=None):
    """Ask for everything a business needs on this server, verifying each part."""
    folder = tenant_dir(args.slug)
    make_private_folder(folder)
    settings = bootstrap_business(args.slug, folder)
    ca_cert = Path(ca_cert or REPO_ROOT / 'prod-ca-2021.crt')
    if not ca_cert.is_file():
        raise ValueError(f'Supabase CA certificate not found at {ca_cert}.')
    bootstrap_database(args.slug, folder, settings['organization_id'], Path(admin_dir or TENANTS_ROOT.parent / 'admin'), ca_cert)
    bootstrap_zoho(folder, settings['organization_id'])
    tenant = load_tenant(folder, slug=args.slug)
    if not tenant.forecast_start_date:
        from hustleai.workflows.forecast import local_today
        data = json.loads((folder / TENANT_FILE).read_text())
        data['forecast_start_date'] = local_today(zone=tenant.zone).isoformat()
        private_write(folder / TENANT_FILE, json.dumps(data, indent=2) + '\n')
        print(f"  Forecast counts orders from {data['forecast_start_date']}.")
    print(f'\n{tenant.name} is set up in {folder}.')


def main(argv=None):
    """Dispatch a tenant command."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list', help='List tenants')
    for name in ('create', 'import-legacy'):
        command = sub.add_parser(name, help='Create a tenant' if name == 'create' else 'Import a single-tenant folder')
        command.add_argument('slug', help='Lowercase name such as rg-midrand')
        if name == 'import-legacy':
            command.add_argument('--from', dest='source', required=True, help='Existing data folder to copy from')
        command.add_argument('--name', help='Business name')
        command.add_argument('--owner-name', help="Owner's first name, used in messages")
        command.add_argument('--owner-number', help="Owner's WhatsApp number, e.g. +27821234567")
        command.add_argument('--organization-id', help='Zoho organization ID')
        command.add_argument('--country-code', help='Phone country code (default 27)')
        command.add_argument('--currency', help='Invoice currency (default ZAR)')
        command.add_argument('--timezone', help='IANA timezone (default Africa/Johannesburg)')
        command.add_argument('--payment-terms', type=int, help='Days until invoices are due (default 7)')
        command.add_argument('--vat-registered', choices=('yes', 'no', 'unknown'))
        command.add_argument('--features', help='Comma list from: ' + ','.join(FEATURES))
        if name == 'create':
            command.add_argument('--admin-root', help='Folder with .supabase-credentials.json (default: current data folder)')
            command.add_argument('--no-database', action='store_true', help='Skip creating the database login')
    install = sub.add_parser('hermes-install', help="Create or update the tenant's Hermes profile")
    install.add_argument('slug')
    install.add_argument('--dry-run', action='store_true', help='Show what would change without changing anything')
    install.add_argument('--hermes', default='hermes', help='Path to the hermes command')
    boot = sub.add_parser('bootstrap', help='Ask for every setting and secret for a business on this server')
    boot.add_argument('slug')
    job = sub.add_parser('run-job', help='Run one scheduled job for one tenant')
    job.add_argument('slug')
    job.add_argument('job', choices=sorted(JOBS))
    args = parser.parse_args(argv)
    if args.command == 'list':
        list_command(args)
    elif args.command == 'create':
        create_command(args)
    elif args.command == 'import-legacy':
        import_legacy_command(args)
    elif args.command == 'bootstrap':
        bootstrap_command(args)
    elif args.command == 'hermes-install':
        hermes_install(args.slug, hermes=args.hermes, dry_run=args.dry_run)
    else:
        raise SystemExit(run_job(args.slug, args.job))


def run():
    """Run the CLI and convert expected errors to concise terminal messages."""
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
    except (KeyboardInterrupt, EOFError):
        raise SystemExit('\nCancelled.') from None


if __name__ == '__main__':
    run()
