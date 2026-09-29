"""Central runtime paths; private data is separate from importable source.

Existing checkouts retain their root data files. Set HUSTLEAI_DATA_DIR to an
absolute private directory on the VPS. No credentials are loaded on import.
"""
import os
from pathlib import Path

_CHECKOUT = Path(__file__).resolve().parents[2]
_DEFAULT = _CHECKOUT if (_CHECKOUT / 'pyproject.toml').exists() else Path.home() / '.local/share/hustleai'
DATA_DIR = Path(os.environ.get('HUSTLEAI_DATA_DIR', str(_DEFAULT))).expanduser().resolve()
ROOT = DATA_DIR  # Compatibility name used by the existing services.
CONFIG = DATA_DIR / '.zoho-local.json'
MAPPING = DATA_DIR / 'zoho-client-phone-map.md'
# Multi-tenant installs keep one private folder per tenant under this root.
TENANTS_ROOT = Path(os.environ.get('HUSTLEAI_TENANTS_ROOT', '/var/lib/hustleai/tenants')).expanduser().resolve()


def tenant_dir(slug):
    """Return the private folder for a tenant slug (lowercase letters, digits, hyphens)."""
    import re
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?', slug or ''):
        raise ValueError('Tenant slugs use lowercase letters, digits and hyphens, for example rg-midrand.')
    return TENANTS_ROOT / slug
