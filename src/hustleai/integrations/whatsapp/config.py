"""Private WhatsApp Cloud API settings, kept in .whatsapp.json in the data dir.

The business number is a setting, not code: fill it in with
`hustleai-whatsapp setup` once Meta assigns it. Secrets never appear in tool
results, logs or error messages.
"""
import json
from pathlib import Path

from hustleai.config import DATA_DIR
from hustleai.domain.phones import normalize
from hustleai.tenant import load_tenant

FILENAME = '.whatsapp.json'
DEFAULT_GRAPH_VERSION = 'v23.0'
REQUIRED = ('phone_number_id', 'business_number', 'owner_number', 'access_token', 'app_secret', 'verify_token')


def config_path(root=None):
    """Return the private settings path for a data directory."""
    return Path(root or DATA_DIR) / FILENAME


def configured(root=None):
    """True when a complete WhatsApp settings file exists."""
    try:
        load(root)
        return True
    except ValueError:
        return False


def international(value, field, country='27'):
    """Normalize a +country number; local 0-prefixed numbers are rejected."""
    number = normalize(value or '', country)
    if not number or not str(value).strip().startswith(('+', '00')):
        raise ValueError(f'{field} must be an international number such as +27821234567.')
    return number


def load(root=None):
    """Load and validate settings.

    Returns a dict with normalized owner_number and business_number, and
    defaults for graph_version, host, port, confirmations_only,
    notify_template and owner_agent. owner_number falls back to the
    tenant's owner number when .whatsapp.json does not set one.
    Raises ValueError naming the missing or invalid field, never its value.
    """
    path = config_path(root)
    tenant = load_tenant(Path(root or DATA_DIR))
    if not path.exists():
        raise ValueError('WhatsApp is not configured. Run: hustleai-whatsapp setup')
    try:
        settings = json.loads(path.read_text())
    except json.JSONDecodeError:
        raise ValueError(f'{FILENAME} is not valid JSON.') from None
    settings['owner_number'] = settings.get('owner_number') or tenant.owner_number
    missing = [key for key in REQUIRED if not str(settings.get(key) or '').strip()]
    if missing:
        raise ValueError('WhatsApp settings are incomplete; missing: ' + ', '.join(missing))
    if not str(settings['phone_number_id']).isdigit():
        raise ValueError('phone_number_id must be the numeric ID from Meta, not the phone number.')
    settings['owner_number'] = international(settings['owner_number'], 'owner_number', tenant.country_code)
    settings['business_number'] = international(settings['business_number'], 'business_number', tenant.country_code)
    if settings['owner_number'] == settings['business_number']:
        raise ValueError('The owner number must differ from the business number.')
    settings.setdefault('graph_version', DEFAULT_GRAPH_VERSION)
    settings.setdefault('host', '127.0.0.1')
    settings['port'] = int(settings.get('port') or 8085)
    settings['confirmations_only'] = settings.get('confirmations_only', True) is not False
    settings['notify_template'] = settings.get('notify_template') or {}
    settings['owner_agent'] = settings.get('owner_agent') or {}
    return settings
