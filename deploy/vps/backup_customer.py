"""Encrypt the selected Hermes profile and HTTPS recovery configuration.

Existing-profile mode reads the approved business profile's canonical auth store.
Does not copy live SQLite databases/session files or unrelated profile homes.
The dashboard's PostgreSQL backup remains the authoritative customer-message copy.
"""
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile

from backup import encrypt


def main():
    os.umask(0o077)
    existing = os.getenv('HUSTLEAI_HERMES_RECOVERY_MODE') == 'existing-restricted'
    profile = Path('/root/.hermes') if existing else Path('/var/lib/hustleai-customer/.hermes/profiles/rg-midrand-customer')
    output = Path('/var/backups/hustleai-dashboard/encrypted')
    stream = io.BytesIO()
    manifest = {'version': 1, 'kind': 'hermes-business-configuration' if existing else 'hermes-customer-configuration',
                'created_at': datetime.now(timezone.utc).isoformat(), 'files': {}, 'database_counts': {}}
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        def add(name, data):
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(data), 0o600
            archive.addfile(item, io.BytesIO(data))
            manifest['files'][name] = hashlib.sha256(data).hexdigest()
        add('database.json', b'{}')
        for name in ('profile.yaml', 'config.yaml', '.env', 'SOUL.md', '.no-bundled-skills', 'auth.json', '.codex_auth.json'):
            path = profile / name
            if path.is_symlink():
                raise ValueError('Profile configuration symlinks are not backed up.')
            if path.is_file():
                add('customer-profile/' + name, path.read_bytes())
        for required in (('config.yaml', '.env', 'auth.json') if existing else ('config.yaml', '.env', 'profile.yaml')):
            if 'customer-profile/' + required not in manifest['files']:
                raise ValueError('Required profile configuration missing.')
        tls_root = Path('/var/lib/hustleai-https')
        tls_files = {}
        if tls_root.exists():
            for path in sorted(tls_root.rglob('*')):
                if path.is_symlink():
                    raise ValueError('TLS recovery state must not contain symlinks.')
                if path.is_file():
                    tls_files[str(path.relative_to(tls_root))] = path.read_bytes()
            from cryptography import x509
            from cryptography.hazmat.primitives import serialization
            for name, data in tls_files.items():
                if name.endswith('.crt'):
                    key = serialization.load_pem_private_key(tls_files[name[:-4] + '.key'], password=None)
                    cert = x509.load_pem_x509_certificate(data)
                    encoding, form = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                    if key.public_key().public_bytes(encoding, form) != cert.public_key().public_bytes(encoding, form):
                        raise ValueError('TLS renewal changed the certificate/key during backup; retry later.')
            for name, data in tls_files.items():
                add('https-state/' + name, data)
            env = Path('/etc/hustleai-dashboard/https.env')
            if env.is_file():
                add('https-config/https.env', env.read_bytes())
        add('manifest.json', json.dumps(manifest, indent=2).encode())
    public_key = Path('/opt/hustleai-dashboard/backup-public.pem').read_bytes()
    data = encrypt(stream.getvalue(), public_key)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = output / (stamp + ('-hermes' if existing else '-customer') + '.hustleai-backup')
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)
    print('Encrypted isolated customer-profile recovery backup saved.')


if __name__ == '__main__':
    main()
