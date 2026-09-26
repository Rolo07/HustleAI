"""Weekly reorder forecast tests with a fake Zoho; no credentials or writes."""
import copy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hustleai.workflows.forecast import classify, local_today, predict, render_markdown
from hustleai.workflows.service import Service

TODAY = date(2026, 10, 5)  # Monday; window is 2026-10-12 up to 2026-10-19.


def line(item_id, name, quantity):
    return {'item_id': item_id, 'name': name, 'description': name, 'quantity': quantity, 'rate': 50}


class ForecastAPI:
    """Fake Zoho invoices and contacts for six customers."""
    organization = '123'

    def __init__(self):
        self.gets = []
        self.invoices = {}
        self.contacts = {}
        durban = {'city': 'durban', 'street2': 'umhlanga'}
        self.contact('1', 'Alpha Store', '0820000001', durban)
        self.contact('2', 'Bravo Deli', '0820000002', {'city': 'Pretoria'})
        self.contact('3', 'Charlie Cafe', '0820000003', {})
        self.contact('4', 'Delta Market', '0820000004', {'city': 'Durban'})
        self.contact('5', 'Echo Foods', '0820000005', {'city': 'Durban'})
        self.contact('6', 'Foxtrot Farm', '0820000006', {})
        harvest = 'Bushveld Harvest'
        # Alpha: every 14 days; the 4th oldest order and non-orders are ignored.
        self.add('101', '1', '2026-08-01', 999, [line('10', harvest, 50)])
        self.add('102', '1', '2026-08-31', 120, [line('10', harvest, 2)])
        self.add('103', '1', '2026-09-14', 100, [line('10', harvest, 3)])
        self.add('104', '1', '2026-09-28', 140, [line('10', harvest, 4)])
        self.add('105', '1', '2026-10-04', 500, [line('10', harvest, 9)], status='draft')
        self.add('106', '1', '2026-10-03', 500, [line('10', harvest, 9)], status='void')
        self.add('107', '1', '2026-10-10', 500, [line('10', harvest, 9)])  # future-dated
        # Bravo: overdue since 2026-08-29.
        self.add('201', '2', '2026-08-01', 80, [line('10', harvest, 1)])
        self.add('202', '2', '2026-08-15', 80, [line('10', harvest, 1)])
        # Charlie: one order only.
        self.add('301', '3', '2026-09-01', 60, [line('10', harvest, 1)])
        # Delta: excluded by the owner.
        self.add('401', '4', '2026-09-21', 60, [line('10', harvest, 1)])
        self.add('402', '4', '2026-09-28', 60, [line('10', harvest, 1)])
        # Echo: the latest order is a test visible only in the full invoice.
        self.add('501', '5', '2026-08-24', 70, [line('10', harvest, 1)])
        self.add('502', '5', '2026-09-07', 70, [line('10', harvest, 1)])
        self.add('503', '5', '2026-09-21', 70, [line('10', harvest, 1)])
        self.add('504', '5', '2026-10-01', 70, [line('10', 'TEST ONLY - harvest', 1)])
        # Foxtrot: one order, owner cycle of 10 days, no address.
        self.add('601', '6', '2026-10-02', 90, [line('10', harvest, 1), line('11', 'Honey', 2)])

    def contact(self, cid, name, mobile, address):
        self.contacts[cid] = {'contact_id': cid, 'contact_name': name, 'currency_code': 'ZAR',
                              'shipping_address': address, 'contact_persons': [{'mobile': mobile}]}

    def add(self, iid, cid, day, total, lines, status='paid'):
        self.invoices[iid] = {'invoice_id': iid, 'invoice_number': 'INV-' + iid, 'customer_id': cid,
                              'customer_name': self.contacts[cid]['contact_name'], 'date': day,
                              'status': status, 'total': total, 'currency_code': 'ZAR', 'line_items': lines}

    def pages(self, path, key):
        assert (path, key) == ('invoices', 'invoices')
        self.gets.append(path)
        # Summaries carry no line items, like Zoho's list endpoint.
        return iter([{k: v for k, v in inv.items() if k != 'line_items'} for inv in self.invoices.values()])

    def get(self, path):
        self.gets.append(path)
        kind, key = path.split('/')
        if kind == 'contacts':
            return {'contact': copy.deepcopy(self.contacts[key])}
        if kind == 'invoices':
            return {'invoice': copy.deepcopy(self.invoices[key])}
        raise AssertionError(path)


class PredictionTests(unittest.TestCase):
    """Pure date arithmetic, confidence and window boundaries."""

    def test_three_orders_average_gaps(self):
        p = predict([date(2026, 9, 28), date(2026, 9, 14), date(2026, 8, 31), date(2026, 1, 1)])
        self.assertEqual((p['cycle_days'], p['next_date'], p['confidence']), (14, date(2026, 10, 12), 'normal'))
        self.assertEqual(len(p['dates_used']), 3)

    def test_two_orders_are_low_confidence(self):
        p = predict([date(2026, 9, 1), date(2026, 9, 11)])
        self.assertEqual((p['cycle_days'], p['confidence']), (10, 'low'))

    def test_uneven_gaps_are_low_confidence(self):
        self.assertEqual(predict([date(2026, 9, 1), date(2026, 9, 5), date(2026, 9, 25)])['confidence'], 'low')

    def test_single_order_needs_override(self):
        self.assertIsNone(predict([date(2026, 9, 1)]))
        self.assertIsNone(predict([date(2026, 9, 1), date(2026, 9, 1)]))  # same-day invoices are one order
        p = predict([date(2026, 9, 1)], 21)
        self.assertEqual((p['next_date'], p['basis']), (date(2026, 9, 22), 'owner'))

    def test_override_replaces_history(self):
        p = predict([date(2026, 9, 1), date(2026, 9, 8), date(2026, 9, 15)], 30)
        self.assertEqual((p['cycle_days'], p['next_date']), (30, date(2026, 10, 15)))

    def test_window_boundaries(self):
        def status(days):
            return classify({'next_date': TODAY.fromordinal(TODAY.toordinal() + days)}, TODAY)
        self.assertEqual([status(d) for d in (-1, 0, 6, 7, 13, 14)],
                         ['overdue', None, None, 'due', 'due', None])

    def test_today_uses_south_african_date(self):
        late_utc = datetime(2026, 10, 4, 22, 30, tzinfo=timezone.utc)
        self.assertEqual(local_today(late_utc), date(2026, 10, 5))


class ForecastTests(unittest.TestCase):
    """Report building against the fake Zoho with the isolated SQLite store."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config = self.root / 'config.json'
        config.write_text(json.dumps({'organization_id': '123', 'country_code': '27'}))
        self.config_patch = patch('hustleai.workflows.service.CONFIG', config)
        self.config_patch.start()
        rows = ''.join(f'| {n} | +2782000000{n} |\n' for n in range(1, 7))
        (self.root / 'zoho-client-phone-map.md').write_text('Organization: 123\n' + rows)
        self.api = ForecastAPI()
        self.s = Service(self.api, self.root)
        self.s.exclude_from_forecast('0820000004')
        self.s.set_order_cycle('0820000006', 10)
        self.api.gets.clear()

    def tearDown(self):
        self.s.close()
        self.config_patch.stop()
        self.temp.cleanup()

    def test_report_sections(self):
        report = self.s.build_forecast(TODAY)
        self.assertEqual((report['window_start'], report['window_end']), ('2026-10-12', '2026-10-19'))
        due = {c['customer_name']: c for c in report['due']}
        self.assertEqual(list(due), ['Alpha Store', 'Foxtrot Farm'])
        alpha = due['Alpha Store']
        self.assertEqual((alpha['predicted_date'], alpha['cycle_days'], alpha['confidence']), ('2026-10-12', 14, 'normal'))
        self.assertEqual((alpha['expected_value'], alpha['phone']), ('120.00', '+27820000001'))
        self.assertEqual(alpha['products'][0]['quantity'], '3')
        self.assertEqual((alpha['area'], alpha['suburb']), ('Durban', 'Umhlanga'))
        fox = due['Foxtrot Farm']
        self.assertEqual((fox['basis'], fox['predicted_date'], fox['area']), ('owner', '2026-10-12', 'No address'))
        self.assertEqual(report['products'], [
            {'item_id': '10', 'name': 'Bushveld Harvest', 'quantity': '4', 'customers': 2},
            {'item_id': '11', 'name': 'Honey', 'quantity': '2', 'customers': 1}])
        self.assertEqual([a['area'] for a in report['areas']], ['Durban', 'No address'])
        self.assertEqual(report['expected_value_total'], '210.00')
        self.assertEqual([(c['customer_name'], c['days_overdue']) for c in report['overdue']], [('Bravo Deli', 37)])
        self.assertEqual(report['unpredictable'], [{'contact_id': '3', 'customer_name': 'Charlie Cafe',
                                                    'last_order_date': '2026-09-01'}])
        self.assertEqual(report['excluded_customers'], 1)

    def test_line_level_test_invoice_is_dropped_and_prediction_redone(self):
        report = self.s.build_forecast(TODAY)
        names = [c['customer_name'] for c in report['due'] + report['overdue']]
        self.assertNotIn('Echo Foods', names)  # without the test order, Echo is due today
        self.assertIn('invoices/504', self.api.gets)

    def test_details_fetched_only_for_listed_customers(self):
        self.s.build_forecast(TODAY)
        contacts = sorted(p for p in self.api.gets if p.startswith('contacts/'))
        self.assertEqual(contacts, ['contacts/1', 'contacts/2', 'contacts/6'])
        self.assertNotIn('invoices/101', self.api.gets)  # 4th-oldest order is never read
        self.assertNotIn('invoices/401', self.api.gets)  # excluded customer is never read

    def test_saved_report_is_reused_within_the_week(self):
        first = self.s.reorder_forecast(today=TODAY)
        self.api.gets.clear()
        again = self.s.reorder_forecast(today=date(2026, 10, 7))
        self.assertEqual(first, again)
        self.assertEqual(self.api.gets, [])
        rebuilt = self.s.reorder_forecast(refresh=True, today=TODAY)
        self.assertEqual(rebuilt['due'], first['due'])
        count = self.s.db.execute('SELECT count(*) FROM reorder_forecasts').fetchone()[0]
        self.assertEqual(count, 1)
        self.s.reorder_forecast(today=date(2026, 10, 12))
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM reorder_forecasts').fetchone()[0], 2)

    def test_cycle_settings(self):
        with self.assertRaises(ValueError):
            self.s.set_order_cycle('0820000001', 0)
        with self.assertRaises(ValueError):
            self.s.set_order_cycle('0820000001', '2.5')
        self.s.set_order_cycle('0820000004', 30)  # setting a cycle includes Delta again
        self.assertEqual(self.s.store.order_cycles()['4'], {'cycle_days': 30, 'excluded': False, 'note': 'Delta Market'})
        self.s.exclude_from_forecast('0820000004')
        self.assertEqual(self.s.store.order_cycles()['4']['cycle_days'], 30)
        self.s.exclude_from_forecast('0820000004', False)
        self.assertEqual(self.s.store.order_cycles()['4']['excluded'], False)
        self.s.exclude_from_forecast('0820000003')
        self.s.exclude_from_forecast('0820000003', False)
        self.assertNotIn('3', self.s.store.order_cycles())
        self.s.clear_order_cycle('0820000006')
        self.assertNotIn('6', self.s.store.order_cycles())

    def test_markdown_report(self):
        text = render_markdown(self.s.build_forecast(TODAY))
        for heading in ('Expected to order (2)', 'Stock to order', 'Deliveries by area', 'Overdue (1)',
                        'Unable to predict (1)', 'Estimated total: R210.00'):
            self.assertIn(heading, text)
        self.assertIn('2026-10-12 to 2026-10-18', text)


if __name__ == '__main__':
    unittest.main()
