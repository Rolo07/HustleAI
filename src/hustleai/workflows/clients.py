"""Clients business operations; provider HTTP stays in integrations."""
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from hustleai.domain.phones import normalize
from hustleai.domain.validation import identifier, positive

class ClientWorkflows:
    """Mixin using the service API, mapping and immutable proposal journal."""

    def phone(self, value):
        """Normalize a required cellphone using the configured country code.

        Args:
            value: International number or supported local phone representation.

        Returns:
            A plus-prefixed normalized number.

        Raises:
            ValueError: The format cannot be normalized; no number is guessed.
        """
        result = normalize(value, self.country)
        if not result:
            raise ValueError('Invalid cellphone; use international format or a local number starting with 0.')
        return result

    def matches(self, contact, phone):
        """Check whether a contact currently contains a normalized phone.

        Args:
            contact: Zoho contact dictionary, optionally with contact_persons.
            phone: Already normalized plus-prefixed number to compare.

        Returns:
            True if a contact-level or contact-person phone/mobile matches.

        This is a pure comparison; unsupported stored phone formats do not match.
        """
        return any(normalize(source.get(field, ''), self.country) == phone
                   for source in [contact] + contact.get('contact_persons', [])
                   for field in ('phone', 'mobile'))

    def clients(self, phone):
        """Resolve stored phone-index candidates against current Zoho records.

        Args:
            phone: Cellphone in a supported local or international format.

        Returns:
            List of live contact dictionaries; empty if no candidates still match.
            Shared numbers may return multiple clients and must be disambiguated.

        Raises:
            ValueError: The phone is invalid, mapping is missing/wrong-organization,
                or a Zoho request fails.
            OSError: The local mapping cannot be read.

        Only indexed IDs are checked. New or changed numbers require a mapping
        sync; an empty result does not prove the number is absent from Zoho.
        No remote records or stored mappings are changed.
        """
        phone = self.phone(phone)
        ids = self.store.find_contacts(phone)
        result = []
        for cid in sorted(ids):
            contact = self.api.get('contacts/' + cid)['contact']
            if self.matches(contact, phone):
                result.append(contact)
        return result

    def customer(self, phone, contact_id=''):
        """Select exactly one ZAR client from the cellphone lookup.

        Args:
            phone: Client cellphone accepted by clients().
            contact_id: Optional numeric ID selected after an ambiguous lookup.

        Returns:
            The single matching live contact dictionary.

        Raises:
            ValueError: No unique match exists, an ID is invalid, currency is not
                ZAR, or the underlying lookup fails.

        An explicit ID must still belong to the cellphone's current matches.
        """
        matches = self.clients(phone)
        if contact_id:
            matches = [c for c in matches if str(c['contact_id']) == identifier(contact_id)]
        if len(matches) != 1:
            raise ValueError('No unique client match. Refresh mapping or select a contact ID from lookup results.')
        if matches[0].get('currency_code') != 'ZAR':
            raise ValueError('Client currency must be ZAR.')
        return matches[0]

    def prepare_client(self, name, email, phone):
        """Find an existing client or prepare a seven-day ZAR client proposal.

        Args:
            name: Nonblank client display name, also used as contact first_name.
            email: Email checked for basic syntax and case-insensitive matching.
            phone: Supported cellphone representation.

        Returns:
            Either {existing_client: contact, created: False}, using the first
            phone/email match, or the standard confirmation proposal dictionary.

        Raises:
            ValueError: Required input is invalid, ZAR is unavailable, or API reads
                fail (including the inherited call budget).

        Reads current contact details until a match is found or all contacts have
        been inspected, avoiding reliance on a stale index. This can be expensive.
        Only the proposal is stored locally; no remote client is created here.
        The scan is not a uniqueness lock against other apps or later proposals.
        """
        phone = self.phone(phone)
        if not name.strip() or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise ValueError('Name and valid email are required.')
        # Fresh complete scan avoids duplicate creation from stale local mappings.
        for summary in self.api.pages('contacts?filter_by=Status.All', 'contacts'):
            c = self.api.get('contacts/' + identifier(summary['contact_id']))['contact']
            people = c.get('contact_persons', [])
            if self.matches(c, phone) or any(p.get('email', '').casefold() == email.casefold() for p in [c] + people):
                return {'existing_client': c, 'created': False}
        currencies = list(self.api.pages('settings/currencies', 'currencies'))
        zar = next((c for c in currencies if c['currency_code'] == 'ZAR'), None)
        if not zar:
            raise ValueError('ZAR is not configured in Zoho.')
        payload = {'contact_name': name.strip(), 'contact_type': 'customer',
                   'currency_id': str(zar['currency_id']), 'payment_terms': 7,
                   'contact_persons': [{'first_name': name.strip(), 'email': email,
                                        'mobile': phone, 'is_primary_contact': True}]}
        return self.proposal('client', payload, payload)
