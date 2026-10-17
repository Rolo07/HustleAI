"""Paid delivery is carried once, without repricing it as stocked merchandise."""
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
from hustleai.dashboard.adapters import LiveBusiness, paid_delivery
from hustleai.dashboard.engine import proposal_text


class DeliveryTests(unittest.TestCase):
    def prior(self, shipping='0', delivery=False, status='paid'):
        rows = [{'previous_line': {'item_id': '1', 'name': 'Bushveld Harvest', 'quantity': '2', 'rate': '50'},
                 'current_product': {'item_id': '1'}, 'review_required': []}]
        if delivery:
            rows.append({'previous_line': {'item_id': '2', 'name': 'Delivery 2', 'quantity': '1', 'rate': '40'},
                         'current_product': {'item_id': '2'}, 'review_required': []})
        return {'source_invoice': {'invoice_id': '123', 'status': status, 'shipping_charge': shipping},
                'lines': rows, 'review_required': ['Review historical shipping_charge; it is not carried forward automatically.'] if Decimal(shipping) else []}

    def quote(self, prior, desired=None):
        with patch('hustleai.dashboard.adapters.Service') as service:
            s = service.return_value.__enter__.return_value
            s.tenant = SimpleNamespace(currency='ZAR', payment_terms_days=7, vat_registered=False, zone=ZoneInfo('UTC'))
            s.reorder_invoice.return_value = prior
            items = {'1': {'item_id': '1', 'name': 'Bushveld Harvest', 'status': 'active', 'rate': '50', 'available_stock': '100'},
                     '2': {'item_id': '2', 'name': 'Delivery 2', 'status': 'active', 'rate': '99'}}
            s.api.get.side_effect = lambda path: {'item': items[path.split('/')[-1]]}
            def build(phone, lines, day):
                total = sum(Decimal(x['rate']) * Decimal(x['quantity']) for x in lines)
                return {'customer_id': '123', 'line_items': lines}, {'customer_name': 'Test', 'estimated_total': str(total)}
            s.build_invoice.side_effect = build
            result = LiveBusiness('.').quote('+27000000000', desired)
            s.api.post.assert_not_called()
            s.api.put.assert_not_called()
            return result

    def test_paid_shipping_is_added_to_payload_total_and_confirmation(self):
        result = self.quote(self.prior(shipping='50'))
        self.assertEqual(result['total'], '150.00')
        self.assertEqual(result['payload']['shipping_charge'], 50)
        self.assertIn('Delivery: R50', proposal_text({**result, 'version': 'test'}))

    def test_delivery_item_keeps_paid_rate_not_current_rate_and_is_added_once(self):
        result = self.quote(self.prior(delivery=True), [{'item_id': '1', 'quantity': '10'}])
        self.assertEqual(result['total'], '540.00')
        self.assertEqual(result['lines'][1]['rate'], '40')
        again = self.quote(self.prior(delivery=True), result['lines'])
        self.assertEqual(again['lines'], result['lines'])
        self.assertEqual(again['total'], '540.00')

    def test_unpaid_duplicate_or_discounted_delivery_is_not_guessed(self):
        for prior in (self.prior(shipping='50', status='unpaid'), self.prior(shipping='50', delivery=True),
                      self.prior(delivery=True, status='partially_paid')):
            with self.subTest(prior=prior), self.assertRaises(ValueError):
                paid_delivery(prior)
        prior = self.prior(delivery=True)
        prior['lines'][1]['previous_line']['discount_amount'] = 5
        with self.assertRaises(ValueError):
            paid_delivery(prior)

    def test_no_delivery_adds_nothing(self):
        result = self.quote(self.prior())
        self.assertEqual(result['total'], '100.00')
        self.assertNotIn('shipping_charge', result['payload'])

    def test_changed_delivery_total_blocks_draft_before_any_provider_write(self):
        with patch('hustleai.dashboard.adapters.Service') as service, patch.object(LiveBusiness, 'quote', return_value={
                'lines': [], 'total': '120.00', 'shipping_charge': '20'}):
            with self.assertRaisesRegex(ValueError, 'Delivery charge or total changed'):
                LiveBusiness('.').draft('+27000000000', {'lines': [], 'total': '110.00', 'shipping_charge': '10'})
            service.return_value.__enter__.return_value.api.post.assert_not_called()
