"""Tenant-only consistent DB snapshot plus private files; public-key encrypted.

The private decryption key stays off the VPS. Database JSON is data, never SQL.
Restore into an isolated database with matching migrations before any cutover.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AAD = b'HustleAI backup v1'


def encrypt(data, public_key):
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    recipient = serialization.load_pem_public_key(public_key)
    wrapped = recipient.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=AAD))
    return json.dumps({'version': 1, 'key': base64.b64encode(wrapped).decode(),
                       'nonce': base64.b64encode(nonce).decode(),
                       'data': base64.b64encode(AESGCM(key).encrypt(nonce, data, AAD)).decode()}).encode()


def decrypt(data, private_key):
    envelope = json.loads(data)
    if envelope['version'] != 1:
        raise ValueError('Unsupported backup version.')
    recipient = serialization.load_pem_private_key(private_key, password=None)
    key = recipient.decrypt(base64.b64decode(envelope['key']), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=AAD))
    return AESGCM(key).decrypt(base64.b64decode(envelope['nonce']), base64.b64decode(envelope['data']), AAD)


def snapshot(root):
    from psycopg import sql
    from hustleai.storage.postgres.repository import connect
    from hustleai.tenant import load_tenant
    organization = load_tenant(root).organization_id
    database = {}
    with connect(root) as connection:
        with connection.transaction():
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            tables = connection.execute("SELECT DISTINCT table_name FROM information_schema.columns WHERE table_schema='hustle_private' AND column_name='organization_id' ORDER BY table_name").fetchall()
            for (table,) in tables:
                query = sql.SQL('SELECT to_jsonb(t) FROM {} AS t WHERE organization_id=%s').format(sql.Identifier('hustle_private', table))
                database[table] = [row[0] for row in connection.execute(query, (organization,))]
    if 'owner_dashboard' not in database or 'operations' not in database:
        raise ValueError('Required database tables missing from backup scope.')
    stream = io.BytesIO()
    manifest = {'version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                'organization_id': organization, 'database_counts': {k: len(v) for k, v in database.items()}, 'files': {}}
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        def add(name, data):
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(data), 0o600
            archive.addfile(item, io.BytesIO(data))
            manifest['files'][name] = hashlib.sha256(data).hexdigest()
        add('database.json', json.dumps(database, ensure_ascii=False).encode())
        for path in sorted(root.rglob('*')):
            if path.is_file() and not path.is_symlink() and not any(part in ('backups', '.zoho-locks', '__pycache__') for part in path.relative_to(root).parts):
                add('runtime/' + str(path.relative_to(root)), path.read_bytes())
        add('manifest.json', json.dumps(manifest, indent=2).encode())
    return stream.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/var/lib/hustleai-dashboard'))
    parser.add_argument('--public-key', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = encrypt(snapshot(args.root), args.public_key.read_bytes())
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    temporary = args.output / (stamp + '.tmp')
    temporary.write_bytes(data)
    temporary.replace(args.output / (stamp + '.hustleai-backup'))
    print('Encrypted tenant database and file backup saved.')


if __name__ == '__main__':
    main()
