#!/usr/bin/env python3
"""Fail-closed PyPI/OSV audit of exact pins, installed packages and pip plans.

Only public package names/versions are sent. No project files, environment
variables, credentials, statements or customer records are uploaded. This is a
known-advisory check, not proof that a package or its bundled native code is safe.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

OSV = 'https://api.osv.dev/v1/querybatch'
PYPI = 'https://pypi.org/pypi/'
LOCAL_PROJECT = 'hustleai'


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def package(name, version):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
        raise ValueError('Invalid package name.')
    if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+!-]*', version):
        raise ValueError('Invalid exact package version.')
    return {'name': canonical(name), 'version': version}


def read_lock(path):
    packages = []
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)', line)
        if not match:
            raise ValueError(f'{path}:{number}: audit requires exact name==version pins; unsupported lines cannot be skipped.')
        packages.append(package(*match.groups()))
    if not packages:
        raise ValueError('Dependency file is empty.')
    return packages


def read_plan(path):
    plan = json.loads(Path(path).read_text())
    if not isinstance(plan.get('install'), list) or not plan['install']:
        raise ValueError('Expected a nonempty pip --report install plan.')
    return [package(item['metadata']['name'], item['metadata']['version']) for item in plan['install']
            if canonical(item['metadata']['name']) != LOCAL_PROJECT]


def installed():
    return [package(dist.metadata['Name'], dist.version) for dist in importlib.metadata.distributions()
            if canonical(dist.metadata['Name']) != LOCAL_PROJECT]


def tls_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def request_json(url, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    headers = {'Accept': 'application/json', 'User-Agent': 'HustleAI-dependency-audit/1.0'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, body, headers), context=tls_context(), timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if status not in (429, 500, 502, 503, 504) or attempt == 2:
                raise ValueError('Advisory/registry request failed with HTTP ' + str(status)) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            if attempt == 2:
                raise ValueError('Advisory/registry data unavailable or invalid; audit is incomplete.') from None
        time.sleep(2 ** attempt)


def osv_findings(packages, request=request_json):
    findings = {}
    for offset in range(0, len(packages), 100):
        batch = packages[offset:offset + 100]
        active = list(enumerate(batch))
        while active:
            queries = []
            for _, row in active:
                query = {'package': {'ecosystem': 'PyPI', 'name': row['name']}, 'version': row['version']}
                if row.get('page_token'):
                    query['page_token'] = row['page_token']
                queries.append(query)
            response = request(OSV, {'queries': queries})
            results = response.get('results')
            if not isinstance(results, list) or len(results) != len(active):
                raise ValueError('OSV returned an incomplete batch response.')
            remaining = []
            for (index, row), result in zip(active, results):
                if not isinstance(result, dict) or result.get('error'):
                    raise ValueError('OSV could not audit a package.')
                vulns = result.get('vulns', [])
                if not isinstance(vulns, list) or any(not isinstance(v, dict) or not v.get('id') for v in vulns):
                    raise ValueError('Malformed OSV vulnerability response.')
                findings.setdefault((row['name'], row['version']), set()).update(v['id'] for v in vulns)
                token = result.get('next_page_token')
                if token:
                    if token == row.get('page_token'):
                        raise ValueError('OSV pagination did not advance.')
                    remaining.append((index, {**row, 'page_token': token}))
            active = remaining
    return findings


def registry_findings(row, request=request_json):
    url = PYPI + urllib.parse.quote(row['name'], safe='') + '/' + urllib.parse.quote(row['version'], safe='') + '/json'
    response = request(url)
    info = response.get('info', {})
    if canonical(info.get('name', '')) != row['name'] or info.get('version') != row['version']:
        raise ValueError('PyPI response does not identify the requested release.')
    artifacts = response.get('urls')
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError('PyPI has no release artifacts; release cannot be verified.')
    vulns = response.get('vulnerabilities')
    if not isinstance(vulns, list) or any(not isinstance(v, dict) or not v.get('id') for v in vulns):
        raise ValueError('PyPI vulnerability information is incomplete.')
    ids = [v['id'] for v in vulns if not v.get('withdrawn')]
    return ids, all(file.get('yanked') is True for file in artifacts)


def audit(packages, request=request_json):
    unique = {(p['name'], p['version']): p for p in packages}
    rows = sorted(unique.values(), key=lambda p: (p['name'], p['version']))
    if not rows:
        raise ValueError('No packages to audit.')
    osv = osv_findings(rows, request)
    results = []; errors = []
    def check(row):
        try:
            ids, yanked = registry_findings(row, request)
            return {**row, 'advisories': sorted(osv[(row['name'], row['version'])] | set(ids)), 'yanked': yanked}
        except Exception as error:
            return {**row, 'error': str(error)}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for result in pool.map(check, rows):
            if 'error' in result:
                errors.append(result)
            else:
                results.append(result)
    return {'checked_at': datetime.now(timezone.utc).isoformat(), 'sources': [OSV, 'https://pypi.org/pypi/{name}/{version}/json'],
            'packages': results, 'errors': errors, 'ok': not errors and not any(r['advisories'] or r['yanked'] for r in results)}


def runtime_status(version=None, policy=None):
    version = tuple(version or sys.version_info[:3])
    policy = policy or json.loads(Path(__file__).with_name('python_security_policy.json').read_text())
    series = '.'.join(map(str, version[:2]))
    minimum = policy['minimum_patches'].get(series)
    return {'version': '.'.join(map(str, version)), 'minimum': f'{series}.{minimum}' if minimum is not None else None,
            'ok': minimum is not None and version[2] >= minimum, 'policy_reviewed_on': policy['reviewed_on'], 'source': policy['source']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', action='append', default=[])
    parser.add_argument('--installed', action='store_true')
    parser.add_argument('--pip-report', action='append', default=[])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check-runtime', action='store_true')
    args = parser.parse_args()
    packages = []
    try:
        for file in args.lock:
            packages.extend(read_lock(file))
        for file in args.pip_report:
            packages.extend(read_plan(file))
        if args.installed:
            packages.extend(installed())
        result = audit(packages)
        if args.check_runtime:
            result['runtime'] = runtime_status()
            if not result['runtime']['ok']:
                result['ok'] = False
                result['errors'].append({'error': 'Python runtime below the reviewed security patch floor or outside reviewed series: ' + result['runtime']['version']})
    except Exception as error:
        result = {'ok': False, 'errors': [{'error': str(error)}], 'packages': [], 'checked_at': datetime.now(timezone.utc).isoformat()}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    for row in result['packages']:
        if row['advisories'] or row['yanked']:
            print(row['name'], row['version'], ' '.join(row['advisories']), 'YANKED' if row['yanked'] else '')
    for error in result['errors']:
        print('INCOMPLETE:', error.get('name', ''), error['error'])
    print(f"Audited {len(result['packages'])} exact package versions: " + ('no known advisories or yanked releases found.' if result['ok'] else 'FAILED; do not treat as a clean audit.'))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
