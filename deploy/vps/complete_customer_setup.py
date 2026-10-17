"""Verify the isolated customer API and model before saving its dashboard link."""
import os
import json
import argparse
from pathlib import Path
import pwd
import urllib.request
import urllib.error

from hustleai.cli.tenant import read_env, set_env
from hustleai.dashboard.hermes_setup import check_restricted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--existing', action='store_true')
    args = parser.parse_args()
    profile = Path('/root/.hermes') if args.existing else Path('/var/lib/hustleai-customer/.hermes/profiles/rg-midrand-customer')
    mode = 'existing-restricted' if args.existing else 'restricted'
    if args.existing:
        import yaml
        config = yaml.safe_load((profile / 'config.yaml').read_text())
        if config.get('platform_toolsets', {}).get('api_server') != ['no_mcp']:
            raise ValueError('Existing API must explicitly exclude tools and MCP.')
        if any(config.get('memory', {}).get(k) is not False for k in ('memory_enabled', 'user_profile_enabled')):
            raise ValueError('Shared profile memory must be disabled.')
    settings = read_env(profile / '.env')
    key = settings.get('API_SERVER_KEY', '')
    base = 'http://127.0.0.1:8643'
    prefix = '' if args.existing else '/p/rg-midrand-customer'
    url = base + prefix + '/v1/chat/completions'
    check_restricted(url, key, mode)
    for path, headers, expected in (
        (prefix + '/v1/toolsets', {}, (401, 403)),
        (('/p/rg-midrand-customer' if args.existing else '/p/default') + '/v1/toolsets', {'Authorization': 'Bearer ' + key}, (404,)),
    ):
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=15):
                raise ValueError('Customer API isolation check failed.')
        except urllib.error.HTTPError as error:
            if error.code not in expected:
                raise ValueError('Customer API isolation check failed.') from None
    body = json.dumps({'model': 'default', 'stream': False, 'messages': [
        {'role': 'user', 'content': 'This is a fictional connectivity check. Reply with a short greeting. Do not run tools.'}]}).encode()
    request = urllib.request.Request(url, body, {'Authorization': 'Bearer ' + key,
        'Content-Type': 'application/json', 'X-Hermes-Session-Key': 'setup-smoke'})
    with urllib.request.urlopen(request, timeout=180) as response:
        choice = json.load(response)['choices'][0]
    if choice.get('finish_reason') != 'stop' or not str(choice.get('message', {}).get('content') or '').strip():
        raise ValueError('Customer model did not complete successfully.')
    path = Path('/var/lib/hustleai-dashboard/.env')
    set_env(path, {'HUSTLEAI_HERMES_URL': url, 'HUSTLEAI_HERMES_API_KEY': key,
                   'HUSTLEAI_HERMES_CUSTOMER_PROFILE': mode})
    account = pwd.getpwnam('hustleai-dashboard')
    os.chown(path, account.pw_uid, account.pw_gid)
    path.chmod(0o600)
    print('Customer model responded; API authentication, profile isolation and disabled tools verified.')
    print('Dashboard connection saved privately. No WhatsApp message was sent.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Customer connection setup did not complete; inspect the private customer service logs.') from None
