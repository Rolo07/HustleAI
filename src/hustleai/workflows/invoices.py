"""Invoices business operations; provider HTTP stays in integrations."""
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from hustleai.domain.phones import normalize
from hustleai.domain.validation import identifier, positive

class InvoiceCreation:
    """Mixin using the service API, mapping and immutable proposal journal."""

    def invoices(self, phone, contact_id=''):
        """List all invoice summaries belonging to one cellphone-selected client.

        Args:
            phone: Client cellphone.
            contact_id: Optional ID to disambiguate clients sharing the number.

        Returns:
            List of Zoho invoice summaries across all pages, without status filtering.

        Raises:
            ValueError: Client selection or an API request fails.

        This performs read-only requests; use invoice() for full invoice details.
        """
        customer = self.customer(phone, contact_id)
        return list(self.api.pages('invoices?customer_id=' + identifier(customer['contact_id']), 'invoices'))

    def invoice(self, phone, invoice_id, contact_id=''):
        """Read a full ZAR invoice and verify its customer matches the cellphone.

        Args:
            phone: Client cellphone.
            invoice_id: Numeric invoice ID, not the human-readable invoice number.
            contact_id: Optional client ID to resolve shared phone numbers.

        Returns:
            The complete invoice dictionary returned by Zoho.

        Raises:
            ValueError: Selection/ID is invalid, ownership or currency does not
                match, or a request fails.

        Use this ownership check before exposing invoice details or downloading
        its PDF. It verifies client association, not WhatsApp caller identity.
        """
        customer = self.customer(phone, contact_id)
        invoice = self.api.get('invoices/' + identifier(invoice_id))['invoice']
        if str(invoice['customer_id']) != str(customer['contact_id']):
            raise ValueError('Invoice does not belong to this client.')
        if invoice.get('currency_code') != 'ZAR':
            raise ValueError('Invoice currency must be ZAR.')
        return invoice

    def prepare_invoice(self, phone, lines, invoice_date, contact_id=''):
        """Prepare an unsent ZAR invoice with inclusive rates and seven-day terms.

        Args:
            phone: Client cellphone; must resolve to one ZAR client.
            lines: Between 1 and 100 dictionaries. Each requires a positive
                VAT-inclusive rate, positive quantity (default 1), and item_id or
                description. tax_id may inherit the product's configured tax.
                no_tax=True is allowed only with explicit user approval and no
                assigned tax; missing tax configuration is not implicit approval.
                When the config sets vat_registered to false, no line may carry a
                tax: rates are the final prices and no VAT is shown.
            invoice_date: ISO date (YYYY-MM-DD) used to calculate due date +7 days.
            contact_id: Optional client ID for a shared cellphone.

        Returns:
            Confirmation proposal including invoice payload, configured tax
            details, customer name, and an estimated two-decimal gross total.

        Raises:
            ValueError: Client/date/lines/prices/taxes are invalid, an item is
                inactive, or an API read fails.
            KeyError: A required line key (such as rate) or API field is missing.

        Reads client, product and tax settings and writes only the local proposal.
        Catalog rates are never silently assumed inclusive. Zoho computes the
        final total; local Decimal estimates can differ from its line rounding.
        The payload requests send=False and does not email or message anyone.
        """
        payload, preview = self.build_invoice(phone, lines, invoice_date, contact_id)
        return self.proposal('invoice', payload, preview)

    def build_invoice(self, phone, lines, invoice_date, contact_id=''):
        """Validate complete invoice lines and return a payload plus preview.

        Args: Same line, date and client inputs as prepare_invoice.
        Returns: (payload, preview) without persisting a pending operation.
        Raises: ValueError/KeyError for invalid inputs or failed API reads.

        Shared by new invoices and full-line draft replacements. Performs
        read-only API calls; does not change Zoho or create journal entries.
        """
        customer = self.customer(phone, contact_id)
        day = date.fromisoformat(invoice_date)
        if not lines or len(lines) > 100:
            raise ValueError('Supply 1–100 invoice lines.')
        # vat_registered False means the business may not show VAT at all.
        # Missing means unknown: keep requiring an explicit tax decision per line.
        unregistered = self.workflow_config.get('vat_registered') is False
        taxes = {} if unregistered else {str(t['tax_id']): t for t in self.api.get('settings/taxes')['taxes']}
        prepared = []
        total = Decimal('0')
        for line in lines:
            item_id = line.get('item_id')
            item = self.api.get('items/' + identifier(item_id))['item'] if item_id else {}
            if item.get('status') == 'inactive':
                raise ValueError('Cannot invoice an inactive product.')
            if unregistered and line.get('tax_id'):
                raise ValueError('The business is not VAT-registered; invoice lines cannot carry a tax.')
            # An unregistered business never inherits a product's tax.
            tax_id = '' if unregistered else str(line.get('tax_id') or item.get('tax_id') or '')
            no_tax = unregistered or line.get('no_tax') is True
            if no_tax and tax_id:
                raise ValueError('Cannot combine no_tax with an assigned tax.')
            if tax_id not in taxes and not no_tax:
                raise ValueError('Select a configured Zoho tax_id, or explicitly approve no_tax; VAT rate will not be guessed.')
            # Explicit inclusive rate prevents treating a catalog net price as gross.
            rate = positive(line['rate'])
            quantity = positive(line.get('quantity', 1))
            name = str(line.get('description') or item.get('name') or '').strip()
            if not name:
                raise ValueError('Each line needs an existing item or description.')
            record = {'name': name, 'description': name, 'rate': float(rate),
                      'quantity': float(quantity), 'tax_id': tax_id}
            if no_tax:
                record.pop('tax_id')
            if item_id:
                record['item_id'] = identifier(item_id)
            prepared.append(record)
            total += rate * quantity
        no_tax_note = ('Not VAT-registered; no VAT shown' if unregistered
                       else 'No tax applied; explicitly requested')
        payload = {'customer_id': str(customer['contact_id']), 'date': day.isoformat(),
                   'due_date': (day + timedelta(days=7)).isoformat(), 'payment_terms': 7,
                   'is_inclusive_tax': not unregistered, 'line_items': prepared,
                   'send': False}
        return payload, {'customer_name': customer['contact_name'],
                             'currency': 'ZAR', 'estimated_total': str(total.quantize(Decimal('.01'))),
                             'taxes': [taxes[l['tax_id']] if l.get('tax_id') else {'treatment': no_tax_note} for l in prepared], 'invoice': payload}
