"""Pull and authenticate encrypted VPS backups to the owner's Mac."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

from backup import decrypt


def verify(path, key):
    with tarfile.open(fileobj=io.BytesIO(decrypt(path.read_bytes(), key))) as archive:
        manifest = json.load(archive.extractfile('manifest.json'))
        for name, digest in manifest['files'].items():
            if hashlib.sha256(archive.extractfile(name).read()).hexdigest() != digest:
                raise ValueError('Backup archive integrity check failed.')
        database = json.load(archive.extractfile('database.json'))
        if not all(len(database[table]) == count for table, count in manifest['database_counts'].items()):
            raise ValueError('Backup database row counts differ.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*@[a-zA-Z0-9.-]+', args.host):
        raise ValueError('Invalid SSH destination.')
    os.umask(0o077)
    args.destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = (args.destination / 'backup-private.pem').read_bytes()
    ssh = ['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15', args.host]
    remote = '/var/backups/hustleai-dashboard/encrypted/'
    listing = subprocess.check_output(ssh + ["find " + remote + " -maxdepth 1 -type f -name '*.hustleai-backup' -printf '%f %s\\n'"], text=True, timeout=30)
    downloaded = 0
    for line in listing.splitlines():
        name, size = line.split()
        if not re.fullmatch(r'\d{8}T\d{6}Z(?:-customer|-hermes)?\.hustleai-backup', name):
            raise ValueError('Unexpected remote backup name.')
        target = args.destination / name
        if target.exists():
            if target.stat().st_size != int(size):
                raise ValueError('Existing backup size differs; review manually.')
            continue
        temporary = target.with_suffix('.partial')
        try:
            with temporary.open('wb') as output:
                subprocess.run(ssh + ['cat ' + remote + name], stdout=output, check=True, timeout=180)
            if temporary.stat().st_size != int(size):
                raise ValueError('Incomplete backup download.')
            verify(temporary, key)
            temporary.replace(target)
            downloaded += 1
        finally:
            temporary.unlink(missing_ok=True)
    print(f'Off-VPS backup pull complete: {downloaded} new authenticated archives.', flush=True)


if __name__ == '__main__':
    main()
