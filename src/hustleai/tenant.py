"""Tenant settings: who the business is and how it invoices.

Each tenant has its own private data folder. Business settings live in
tenant.json in that folder; the older .zoho-local.json is still read, and
tenant.json wins where both define a key. Secrets never go in tenant.json.

Nothing here reads credentials or contacts Zoho, so it is safe to load at
startup, including by the MCP server with an empty data folder.
"""
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hustleai.domain.phones import normalize

TENANT_FILE = 'tenant.json'
LEGACY_FILE = '.zoho-local.json'
FEATURES = ('invoicing', 'forecast', 'orders_sync', 'whatsapp')
DEFAULTS = {'name': '', 'owner_name': 'the owner', 'owner_number': '', 'country_code': '27',
            'currency': 'ZAR', 'timezone': 'Africa/Johannesburg', 'payment_terms_days': 7,
            'features': list(FEATURES)}


def read_settings(root, legacy_path=None):
    """Merge legacy settings with tenant.json for one data folder.

    Args:
        root: The tenant's private data folder.
        legacy_path: Path of .zoho-local.json; defaults to root/.zoho-local.json.
    Returns: A plain dict. Missing files give an empty dict, never an error.
    Raises: ValueError if a file exists but is not valid JSON.
    """
    settings = {}
    for path in (Path(legacy_path) if legacy_path else Path(root) / LEGACY_FILE, Path(root) / TENANT_FILE):
        if path.is_file():
            try:
                settings.update(json.loads(path.read_text()))
            except json.JSONDecodeError:
                raise ValueError(f'{path.name} is not valid JSON.') from None
    return settings


@dataclass(frozen=True)
class Tenant:
    """Validated business settings for one tenant."""
    slug: str
    name: str
    owner_name: str
    owner_number: str
    organization_id: str
    country_code: str
    currency: str
    timezone: str
    payment_terms_days: int
    vat_registered: object          # True, False, or None when not decided
    forecast_start_date: object     # 'YYYY-MM-DD' or None
    test_invoice_ids: tuple = ()
    features: frozenset = field(default_factory=lambda: frozenset(FEATURES))

    @property
    def zone(self):
        """The tenant's timezone as a ZoneInfo."""
        return ZoneInfo(self.timezone)

    def has(self, feature):
        """True if a feature is enabled for this tenant."""
        return feature in self.features

    def require(self, feature):
        """Raise ValueError when a feature is switched off for this tenant."""
        if not self.has(feature):
            raise ValueError(f'The {feature} feature is not enabled for {self.name or "this business"}.')

    @classmethod
    def from_settings(cls, settings, slug=None):
        """Build and validate a Tenant from a settings dict (see read_settings)."""
        values = {**DEFAULTS, **{k: v for k, v in settings.items() if v is not None}}
        country = str(values['country_code'])
        if not re.fullmatch(r'[1-9]\d{0,2}', country):
            raise ValueError('country_code must be 1-3 digits, for example 27.')
        currency = str(values['currency']).upper()
        if not re.fullmatch(r'[A-Z]{3}', currency):
            raise ValueError('currency must be a three-letter code such as ZAR.')
        try:
            ZoneInfo(str(values['timezone']))
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('timezone must be an IANA name such as Africa/Johannesburg.') from None
        terms = values['payment_terms_days']
        if isinstance(terms, bool) or not str(terms).isdigit() or not 0 <= int(terms) <= 365:
            raise ValueError('payment_terms_days must be a whole number from 0 to 365.')
        features = frozenset(values['features'])
        unknown = features - set(FEATURES)
        if unknown:
            raise ValueError('Unknown features: ' + ', '.join(sorted(unknown)))
        owner = ''
        if values['owner_number']:
            owner = normalize(values['owner_number'], country) or ''
            if not owner:
                raise ValueError('owner_number is not a valid phone number.')
        vat = values.get('vat_registered')
        return cls(slug=slug or str(values.get('slug') or 'default'), name=str(values['name']),
                   owner_name=str(values['owner_name']) or DEFAULTS['owner_name'], owner_number=owner,
                   organization_id=str(values.get('organization_id') or ''), country_code=country,
                   currency=currency, timezone=str(values['timezone']), payment_terms_days=int(terms),
                   vat_registered=vat if isinstance(vat, bool) else None,
                   forecast_start_date=values.get('forecast_start_date') or None,
                   test_invoice_ids=tuple(map(str, values.get('test_invoice_ids') or ())), features=features)


def load_tenant(root, legacy_path=None, slug=None):
    """Read and validate the tenant for a data folder."""
    return Tenant.from_settings(read_settings(root, legacy_path), slug)
