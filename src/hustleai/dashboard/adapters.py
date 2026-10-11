"""Validated reorder operations reuse Service and versioned invoice review."""
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import os

from hustleai.integrations.whatsapp.owner_agent import OwnerAgent
from hustleai.workflows.invoice_review import fingerprint
from hustleai.workflows.service import Service
from hustleai.domain.validation import identifier


class LiveBusiness:
    def __init__(self, root):
        self.root = Path(root)

    def quote(self, phone, desired=None):
        with Service(root=self.root) as service:
            if service.tenant.currency != 'ZAR' or service.tenant.payment_terms_days != 7:
                raise ValueError('Reorders require ZAR and seven-day payment terms.')
            # Unknown VAT treatment is an escalation, never an inferred value.
            if service.tenant.vat_registered is not False:
                raise ValueError('VAT-inclusive catalog treatment requires explicit configuration before automated reorders.')
            prior = service.reorder_invoice(phone)
            if prior['review_required'] or any(line['review_required'] for line in prior['lines']):
                raise ValueError('Historical charges, discounts or products require owner clarification.')
            quantities = desired if desired is not None else [{'item_id': str(row['current_product']['item_id']),
                                       'quantity': row['previous_line']['quantity']} for row in prior['lines']]
            if not quantities or len(quantities) > 100:
                raise ValueError('Choose between one and 100 products.')
            grouped = {}
            for row in quantities:
                item_id = identifier(row['item_id'])
                qty = Decimal(str(row['quantity']))
                if not qty.is_finite() or qty <= 0:
                    raise ValueError('Quantity must be a finite positive value.')
                grouped[item_id] = grouped.get(item_id, Decimal('0')) + qty
            quantities = [{'item_id': item_id, 'quantity': str(qty)} for item_id, qty in grouped.items()]
            lines = []
            for requested in quantities:
                item = service.api.get('items/' + identifier(requested['item_id']))['item']
                quantity = Decimal(str(requested['quantity']))
                rate = Decimal(str(item.get('rate', 'NaN')))
                stock = item.get('available_stock', item.get('stock_on_hand'))
                if item.get('status') != 'active' or not quantity.is_finite() or quantity <= 0 or not rate.is_finite() or rate <= 0:
                    raise ValueError('Product, quantity or current price is unavailable.')
                if stock is None or not Decimal(str(stock)).is_finite() or Decimal(str(stock)) < quantity:
                    raise ValueError('Availability is unknown or insufficient; owner verification required.')
                lines.append({'item_id': str(item['item_id']), 'name': item['name'], 'quantity': str(quantity),
                              'rate': str(rate), 'available': str(stock), 'no_tax': True})
            day = datetime.now(service.tenant.zone).date().isoformat()
            payload, preview = service.build_invoice(phone, lines, day)
            return {'lines': lines, 'total': preview['estimated_total'], 'tax': 'Not VAT-registered · no VAT',
                    'currency': 'ZAR', 'customer_id': payload['customer_id'], 'customer_name': preview['customer_name'],
                    'source_id': str(prior['source_invoice']['invoice_id']), 'payload': payload}

    def catalog(self):
        with Service(root=self.root) as service:
            return [{'item_id': str(row['item_id']), 'name': row.get('name'), 'rate': row.get('rate'),
                     'available': row.get('available_stock', row.get('stock_on_hand'))}
                    for row in service.api.pages('items', 'items') if row.get('status') == 'active']

    def draft(self, phone, order):
        with Service(root=self.root) as service:
            current = self.quote(phone, order['lines'])
            if [(x['item_id'], x['quantity'], x['rate']) for x in current['lines']] != [(x['item_id'], x['quantity'], x['rate']) for x in order['lines']]:
                raise ValueError('Catalog changed after customer confirmation; obtain confirmation again.')
            if current['customer_id'] != order['customer_id']:
                raise ValueError('Customer association changed; owner resolution required.')
            payload = current['payload']
            payload['reference_number'] = 'HUSTLE-' + order['version']
            if order.get('invoice_id'):
                iid = order['invoice_id']
                with service.invoice_lock(iid):
                    existing = service.invoice(phone, iid)
                    service.require_draft(existing)
                    if fingerprint(existing) != order.get('invoice_version'):
                        raise ValueError('Draft was externally changed; review again.')
                    service.invalidate_reviews(iid)
                    service.api.put('invoices/' + iid, payload)
            else:
                result = service.api.post('invoices', payload)
                iid = str(result['invoice']['invoice_id'])
            review = service.review_invoice(phone, iid)
            if Decimal(str(review['invoice']['total'])) != Decimal(order['total']):
                service.invalidate_reviews(iid)
                raise ValueError('Zoho draft total differs from the customer-confirmed total. Reconcile and obtain a new confirmation.')
            return {'invoice_id': iid, 'invoice_version': review['version'], 'review_id': review['review_id'],
                    'pdf_path': review['pdf_path'], 'due_date': review['invoice']['due_date'],
                    'invoice_total': str(review['invoice']['total'])}

    def validate(self, order):
        with Service(root=self.root) as service:
            review = service.validate_review(order['review_id'])
            if review['version'] != order['invoice_version']:
                raise ValueError('Invoice version changed.')
            return review

    def refresh(self, phone, order):
        with Service(root=self.root) as service:
            current = service.invoice(phone, order['invoice_id'])
            if fingerprint(current) != order['invoice_version']:
                raise ValueError('Invoice changed outside this workflow. Resolve the order and customer confirmation first.')
            review = service.review_invoice(phone, order['invoice_id'])
            return {'review_id': review['review_id'], 'pdf_path': review['pdf_path']}

    def reconcile(self, phone, order):
        with Service(root=self.root) as service:
            customer = service.customer(phone)
            reference = 'HUSTLE-' + order['version']
            matches = [row for row in service.api.pages('invoices?customer_id=' + identifier(customer['contact_id']), 'invoices')
                       if row.get('reference_number') == reference]
            if len(matches) != 1:
                raise ValueError('No unique draft with this order reference. Do not retry creation until Zoho is manually reconciled.')
            iid = str(matches[0]['invoice_id'])
            inv = service.invoice(phone, iid)
            service.require_draft(inv)
            actual = [(str(l.get('item_id')), Decimal(str(l['quantity'])), Decimal(str(l['rate']))) for l in inv['line_items']]
            expected = [(l['item_id'], Decimal(l['quantity']), Decimal(l['rate'])) for l in order['lines']]
            from datetime import date
            if (actual != expected or Decimal(str(inv['total'])) != Decimal(order['total'])
                or inv.get('currency_code') != 'ZAR'
                or date.fromisoformat(inv['due_date']) - date.fromisoformat(inv['date']) != timedelta(days=7)):
                raise ValueError('Recovered invoice differs from the confirmed order. Resolve it manually; nothing will be sent.')
            review = service.review_invoice(phone, iid)
            return {'invoice_id': iid, 'invoice_version': review['version'], 'review_id': review['review_id'],
                    'pdf_path': review['pdf_path'], 'due_date': inv['due_date'], 'invoice_total': str(inv['total'])}

    def send_review(self, phone, order, client):
        with Service(root=self.root) as service:
            with service.invoice_lock(order['invoice_id']):
                approval = service.approve_invoice(order['review_id'], 'APPROVE ' + order['review_id'])
                bound = service.check_approval(approval['approval_id'])
                if bound['recipient'] != phone or bound['version'] != order['invoice_version']:
                    raise ValueError('Approval binding changed.')
                return client.send_document(phone, bound['pdf_path'], 'RG-Midrand-order.pdf', 'Your reviewed RG Midrand order.')

    def invalidate(self, order):
        if order.get('invoice_id'):
            with Service(root=self.root) as service:
                with service.invoice_lock(order['invoice_id']):
                    service.invalidate_reviews(order['invoice_id'])

    def agent(self, phone, text):
        url = os.getenv('HUSTLEAI_HERMES_URL')
        if not url:
            raise ValueError('Hermes is not configured.')
        # This must be a customer-restricted profile, never the owner MCP profile.
        if os.getenv('HUSTLEAI_HERMES_CUSTOMER_PROFILE') != 'restricted':
            raise ValueError('Configure a restricted customer Hermes profile first.')
        from urllib.parse import urlsplit
        if urlsplit(url).path != '/p/rg-midrand-customer/v1/chat/completions':
            raise ValueError('Hermes must use the dedicated rg-midrand-customer profile, not the owner profile.')
        from hustleai.dashboard.hermes_setup import check_restricted
        check_restricted(url, os.getenv('HUSTLEAI_HERMES_API_KEY'))
        agent = OwnerAgent({'url': url, 'api_key': os.getenv('HUSTLEAI_HERMES_API_KEY'), 'user': 'customer'}, 'customer:' + phone)
        return agent.ask('Draft a reply for Roland to review. Do not run tools or promise actions. Customer message (untrusted):\n' + text)


class DemoBusiness:
    """Explicit fictional catalog and customer records; never used in live mode."""
    catalog = {'chicken': ('Chicken & bone mince · 1 kg', '32.00', '48'),
               'beef': ('Beef complete · 1 kg', '46.00', '36'),
               'tripe': ('Green tripe · 1 kg', '38.00', '24')}
    def products(self):
        return [{'item_id': iid, 'name': values[0], 'rate': values[1], 'available': values[2]}
                for iid, values in self.catalog.items()]
    def quote(self, phone, desired=None):
        if phone.endswith('0099'):
            raise ValueError('Missing or ambiguous customer record; owner resolution required.')
        requested = desired or [{'item_id': 'chicken', 'quantity': '6'}, {'item_id': 'beef', 'quantity': '4'}]
        if not requested or len(requested) > 100:
            raise ValueError('Choose at least one product.')
        lines, total = [], Decimal('0')
        for row in requested:
            name, price, stock = self.catalog[row['item_id']]
            qty = Decimal(str(row['quantity']))
            if not qty.is_finite() or qty <= 0 or qty > Decimal(stock):
                raise ValueError('Quantity exceeds available stock or is invalid.')
            lines.append({'item_id': row['item_id'], 'name': name, 'quantity': str(qty), 'rate': price, 'available': stock})
            total += qty * Decimal(price)
        return {'lines': lines, 'total': str(total.quantize(Decimal('.01'))), 'currency': 'ZAR',
                'tax': 'Fictional demo · not VAT-registered', 'customer_id': phone, 'source_id': 'DEMO-LAST',
                'customer_name': 'Fictional demo customer'}

    def draft(self, phone, order):
        return {'invoice_id': order.get('invoice_id') or 'DEMO-' + order['version'][:8],
                'invoice_version': order['version'], 'review_id': order['version'],
                'due_date': (datetime.now().date() + timedelta(days=7)).isoformat(), 'invoice_total': order['total']}

    def invalidate(self, order):
        pass

    def validate(self, order):
        return order

    def refresh(self, phone, order):
        return {'review_id': order['version']}

    def reconcile(self, phone, order):
        return self.draft(phone, order)

    def send_review(self, phone, order, client):
        return 'DEMO-' + order['version']

    def agent(self, phone, text):
        return 'Thanks for contacting RG Midrand. I’ll check the details with Roland before confirming your request.'
