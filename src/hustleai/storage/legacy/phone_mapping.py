"""Contact scan and display helpers, with optional hosted repository persistence.

Hosted sync and lookup use PostgreSQL only; Markdown is legacy-only.
"""
from datetime import datetime, timezone
import json
import re
import urllib.parse
from hustleai.config import MAPPING
from hustleai.domain.phones import normalize
from hustleai.storage.files import private_write

def sync(client, country_code, store=None):
    """Refresh the hosted phone index, or the legacy Markdown index, after a full scan.

    Args:
        client: Authenticated Client-compatible object for the organization.
        country_code: Calling code used to normalize local numbers.
        store: Optional PostgreSQL repository; caller must hold its mapping lock.

    Returns:
        None. Prints counts for inspected clients, mappings and skipped fields.

    Raises:
        ValueError: API requests fail or reach the client's call limit.
        OSError: The local mapping cannot be written.

    Reads every list page and each contact's detail, including contact-person
    phone/mobile fields. Unique ID/phone pairs preserve shared numbers. Only
    a successful full scan replaces MAPPING; failures preserve the old file.
    Unsupported nonempty fields are counted and skipped, not guessed.
    """
    rows = set()
    count = skipped = 0
    page = 1
    while True:
        response = client.get(f'contacts?page={page}&per_page=200&filter_by=Status.All')
        for summary in response['contacts']:
            contact_id = str(summary['contact_id'])
            contact = client.get('contacts/' + urllib.parse.quote(contact_id, safe=''))['contact']
            count += 1
            sources = [contact] + contact.get('contact_persons', [])
            for source in sources:
                for field in ('mobile', 'phone'):
                    raw = source.get(field, '')
                    phone = normalize(raw, country_code)
                    if phone:
                        rows.add((contact_id, phone))
                    elif raw:
                        skipped += 1
        if not response.get('page_context', {}).get('has_more_page', False):
            break
        page += 1
    if store is not None:
        store.replace_mappings(rows)
        print(f'Synced {count} clients and {len(rows)} phone mappings to Supabase. Unrecognized phone fields: {skipped}.')
        return
    header = ('# Zoho Invoice client phone mapping\n\n'
              f'Organization: {client.organization}\n\n'
              f'Default country calling code: {country_code}\n\n'
              f'Synced UTC: {datetime.now(timezone.utc).isoformat()}\n\n'
              'Generated from Zoho mobile and phone fields. Run sync after changes in Zoho.\n'
              'Shared numbers retain every matching client ID.\n\n'
              '| Zoho contact ID | Cellphone / phone |\n| --- | --- |\n')
    private_write(MAPPING, header + ''.join(f'| {cid} | {phone} |\n' for cid, phone in sorted(rows)))
    print(f'Synced {count} clients and {len(rows)} phone mappings. Unrecognized phone fields: {skipped}.')

def lookup(client, country_code, value, store=None):
    """Print current client details for a number found in the local index.

    Args:
        client: Authenticated Client-compatible object.
        country_code: Calling code that must match the mapping metadata.
        value: Cellphone in a supported local or international notation.
        store: Optional hosted repository; replaces Markdown lookup when supplied.

    Returns:
        None. Prints matching JSON records or a no-match/stale-index message.

    Raises:
        ValueError: Phone or mapping metadata is invalid, or an API read fails.
        OSError: MAPPING cannot be read.

    Rechecks each indexed match against current Zoho phone fields before
    printing personal information. It neither scans unindexed contacts nor
    refreshes the mapping. Output contains client data; do not send to logs.
    """
    phone = normalize(value, country_code)
    if not phone:
        raise ValueError('Enter a full international number, or a local number starting with 0.')
    if store is not None:
        ids = store.find_contacts(phone)
    else:
        content = MAPPING.read_text()
        if f'Organization: {client.organization}\n' not in content or f'Default country calling code: {country_code}\n' not in content:
            raise ValueError('Mapping belongs to different settings; run sync first.')
        ids = {cid for cid, mapped in re.findall(r'^\| (\d+) \| (\+\d+) \|$', content, re.M) if mapped == phone}
    if not ids:
        print('No match in the phone index. Run sync if contacts have changed in Zoho.')
        return
    for cid in sorted(ids):
        contact = client.get('contacts/' + cid)['contact']
        # Verify the number still belongs to this client before displaying details.
        sources = [contact] + contact.get('contact_persons', [])
        if not any(normalize(s.get(f, ''), country_code) == phone for s in sources for f in ('phone', 'mobile')):
            print(f'Stale mapping for contact {cid}; run sync to refresh.')
            continue
        print(json.dumps({k: contact.get(k) for k in (
            'contact_id', 'contact_name', 'company_name', 'status', 'email',
            'contact_persons', 'billing_address', 'outstanding_receivable_amount', 'currency_code'
        )}, indent=2, ensure_ascii=False))
