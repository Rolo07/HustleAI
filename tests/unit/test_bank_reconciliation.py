import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hustleai.workflows.bank_reconciliation import parse_statement, reconcile, markdown_report
from hustleai.dashboard.bank import BankReconciliation
from hustleai.dashboard.engine import Engine
from hustleai.dashboard.storage import Journal
from hustleai.dashboard.adapters import DemoBusiness

OPTIONS = {'date_format': '%Y-%m-%d'}
CSV = 'Date,Name,Reference,Credit,Debit\n2026-10-07,Maya Botha,RG-1001,440,0\n2026-10-08,Unknown,Unclear,125,0\n2026-10-09,Fees,,0,15\n'


class BankTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = Journal(Path(self.temp.name), demo=True)
        self.engine = Engine(self.journal, DemoBusiness(), demo=True)
        self.bank = BankReconciliation(self.engine)
        self.invoices, self.payments = self.bank.ledger()
        self.data = dict(content=CSV, reviewed=True, currency='ZAR', account_label='Fictional account', options=OPTIONS, period_start='2026-10-01', period_end='2026-10-11')

    def tearDown(self):
        self.journal.close(); self.temp.cleanup()

    def report(self, content=CSV, invoices=None, payments=()):
        rows, issues = parse_statement(content, OPTIONS)
        return reconcile(rows, invoices or self.invoices, payments, '2026-10-01', '2026-10-11', issues)

    def test_reference_match_and_missing_payment_report(self):
        report = self.report()
        self.assertEqual([r['status'] for r in report['transactions']], ['suggested_match', 'unmatched', 'ignored'])
        self.assertEqual(report['outstanding'][0]['unexplained_balance'], '0')
        self.assertEqual(report['outstanding'][1]['unexplained_balance'], '376.00')
        self.assertIn('Daniel Jacobs', markdown_report(report))
        self.assertIn('not record payments', report['warning'])

    def test_exact_name_and_amount_match(self):
        report = self.report(CSV.replace('RG-1001', 'Received'))
        self.assertEqual(report['transactions'][0]['invoice_id'], '9001')

    def test_duplicate_names_never_disambiguated_by_amount_across_clients(self):
        invoices = [*self.invoices, {**self.invoices[0], 'customer_id': 'different', 'invoice_id': '9003', 'invoice_number': 'RG-1003', 'total': '600', 'balance': '600'}]
        report = self.report(CSV.replace('RG-1001', 'Received'), invoices)
        self.assertEqual(report['transactions'][0]['status'], 'ambiguous')

    def test_duplicate_rows_are_not_allocated(self):
        report = self.report(CSV + '2026-10-07,Maya Botha,RG-1001,440,0\n')
        self.assertEqual(report['transactions'][0]['status'], 'duplicate_review')
        self.assertEqual(report['transactions'][-1]['status'], 'duplicate_review')
        self.assertEqual(report['outstanding'][0]['unexplained_balance'], '440.00')

    def test_multiple_invoice_references_and_overpayments_need_review(self):
        self.assertEqual(self.report(CSV.replace('RG-1001', 'RG-1001 RG-1002'))['transactions'][0]['status'], 'ambiguous')
        self.assertEqual(self.report(CSV.replace(',440,', ',900,'))['transactions'][0]['status'], 'review')

    def test_existing_payment_is_evidence_not_new_allocation(self):
        payments = [{'customer_id': '8001', 'date': '2026-10-07', 'reference_number': 'RG-1001', 'amount': '440'}]
        report = self.report(payments=payments)
        self.assertEqual(report['transactions'][0]['status'], 'recorded_match')
        self.assertEqual(report['outstanding'][0]['suggested_bank_credits'], '0')

    def test_invalid_dates_and_currency_visible(self):
        report = self.report(CSV + '2026-99-99,Bad,,100,0\n')
        self.assertEqual(len(report['parse_issues']), 1)
        rows, issues = parse_statement('Date,Name,Amount,Currency\n2026-10-07,Maya,440,USD\n2026-10-08,Maya,440,ZAR\n', OPTIONS)
        self.assertEqual(len(rows), 1); self.assertEqual(len(issues), 1)

    def test_decimal_comma_and_negative_amounts(self):
        rows, issues = parse_statement('Date;Name;Amount\n07/10/2026;Maya Botha;1.234,56\n08/10/2026;Fee;(15,00)\n', {'date_format': '%d/%m/%Y', 'decimal_separator': ',', 'delimiter': ';'})
        self.assertEqual(rows[0]['amount'], '1234.56'); self.assertEqual(rows[1]['amount'], '-15.00')
        self.assertFalse(issues)

    def test_outside_period_and_preinvoice_credit_not_allocated(self):
        self.assertEqual(self.report(CSV.replace('2026-10-07', '2026-09-30'))['transactions'][0]['status'], 'out_of_period')
        inv = [{**self.invoices[0], 'date': '2026-10-08'}]
        self.assertEqual(self.report(invoices=inv)['transactions'][0]['status'], 'review')

    def test_import_and_notifications_idempotent(self):
        first = self.bank.import_csv(self.data); second = self.bank.import_csv(self.data)
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(self.bank.reports()['reports']), 1)
        self.bank.notify(first['id']); self.bank.notify(first['id'])
        self.assertEqual(len(self.journal.read()['outbox']), 1)
        self.engine.tick()
        self.assertIn('api_accepted', self.bank.notify(first['id'])['whatsapp'])
        self.assertIn('not confirmed delivery', self.bank.notify(first['id'])['whatsapp'])

    def test_owner_confirmation_required_and_repeat_does_not_write(self):
        report = self.bank.import_csv(self.data)
        preview = self.bank.prepare(report['id'], 2)
        self.assertEqual(self.bank.prepare(report['id'], 2)['key'], preview['key'])
        with self.assertRaises(ValueError): self.bank.confirm(preview['key'], 'yes')
        result = self.bank.confirm(preview['key'], 'CONFIRM ' + preview['operation_id'])
        self.assertEqual(result['state'], 'recorded')
        self.assertEqual(self.bank.confirm(preview['key'], 'CONFIRM ' + preview['operation_id'])['state'], 'recorded')

    def test_ambiguous_or_duplicate_cannot_prepare(self):
        report = self.bank.import_csv(self.data)
        with self.assertRaises(ValueError): self.bank.prepare(report['id'], 3)

    def test_owner_assignment_is_reviewed_and_balance_checked(self):
        report = self.bank.import_csv(self.data)
        updated = self.bank.assign(report['id'], 3, '9002')
        self.assertEqual(updated['transactions'][1]['status'], 'suggested_match')
        self.assertEqual(updated['outstanding'][1]['unexplained_balance'], '251.00')
        preview = self.bank.prepare(report['id'], 3)
        with self.assertRaises(ValueError): self.bank.assign(report['id'], 3, '9001')
        self.assertEqual(preview['preview']['payment']['amount'], '125')

    def test_ledger_failure_no_report_or_demo_fallback(self):
        with patch.object(self.bank, 'ledger', side_effect=RuntimeError('provider unavailable')):
            with self.assertRaises(RuntimeError): self.bank.import_csv(self.data)
        self.assertFalse(self.bank.reports()['reports'])

    def test_incomplete_import_blocks_payment_recording(self):
        report = self.bank.import_csv({**self.data, 'content': CSV + 'bad,Maya,,10,0\n'})
        with self.assertRaises(ValueError): self.bank.prepare(report['id'], 2)

    def test_cross_upload_transaction_deduplication(self):
        first = self.bank.import_csv(self.data); preview = self.bank.prepare(first['id'], 2)
        changed = self.bank.import_csv({**self.data, 'content': CSV.replace('Unknown', 'Unknown payer')})
        repeated = self.bank.prepare(changed['id'], 2)
        self.assertEqual(repeated['key'], preview['key'])

    def test_statement_review_and_currency_required(self):
        with self.assertRaises(ValueError): self.bank.import_csv({**self.data, 'reviewed': False})
        with self.assertRaises(ValueError): self.bank.import_csv({**self.data, 'currency': 'USD'})

    def test_references_cannot_match_inside_another_invoice_number(self):
        report = self.report(CSV.replace('RG-1001', 'RG-10012'))
        self.assertEqual(report['transactions'][0]['reason'].split(';')[0], 'Exact client name')

    def test_changed_description_or_account_label_cannot_record_same_receipt_twice(self):
        first = self.bank.import_csv(self.data); preview = self.bank.prepare(first['id'], 2)
        second = self.bank.import_csv({**self.data, 'account_label': 'Renamed account', 'content': CSV.replace('Maya Botha', 'Maya B')})
        self.assertEqual(self.bank.prepare(second['id'], 2)['key'], preview['key'])

    def test_live_payment_uses_existing_proposal_and_relays_exact_owner_confirmation(self):
        report = self.bank.import_csv(self.data)
        self.engine.demo = False
        self.engine.business.root = Path(self.temp.name)
        with patch('hustleai.dashboard.bank.Service') as service_class:
            service = service_class.return_value.__enter__.return_value
            service.api.get.return_value = {'invoice': self.invoices[0]}
            service.api.pages.return_value = []
            service.proposal.return_value = {'operation_id': 'operation-1', 'preview': {'currency': 'ZAR'}}
            preview = self.bank.prepare(report['id'], 2)
            service.confirm.assert_not_called()
            payload = service.proposal.call_args.args[1]
            self.assertEqual(payload['customer_id'], '8001')
            self.assertEqual(payload['invoices'], [{'invoice_id': '9001', 'amount_applied': 440.0}])
            with self.assertRaises(ValueError): self.bank.confirm(preview['key'], 'yes')
            service.confirm.assert_not_called()
            self.bank.confirm(preview['key'], 'CONFIRM operation-1')
            self.bank.confirm(preview['key'], 'CONFIRM operation-1')
            service.confirm.assert_called_once_with('operation-1', 'CONFIRM operation-1')

    def test_uncertain_provider_write_never_replays(self):
        report = self.bank.import_csv(self.data); preview = self.bank.prepare(report['id'], 2)
        self.engine.demo = False; self.engine.business.root = Path(self.temp.name)
        with patch('hustleai.dashboard.bank.Service') as service_class:
            service = service_class.return_value.__enter__.return_value
            service.api.get.return_value = {'invoice': self.invoices[0]}
            # Bind the test's demo preview to the customer before live execution.
            with self.journal.locked():
                state = self.journal.read(); state['bank_payments'][preview['key']]['customer_id'] = '8001'; self.journal.write(state)
            service.confirm.side_effect = TimeoutError('uncertain')
            with self.assertRaises(TimeoutError): self.bank.confirm(preview['key'], 'CONFIRM ' + preview['operation_id'])
            with self.assertRaises(ValueError): self.bank.confirm(preview['key'], 'CONFIRM ' + preview['operation_id'])
            self.assertEqual(service.confirm.call_count, 1)
            self.assertEqual(self.bank.reports()['payments'][preview['key']]['state'], 'uncertain')

    def test_changed_customer_or_balance_prevents_preparation(self):
        for changes in ({'customer_id': 'another'}, {'balance': '1'}):
            with self.subTest(changes=changes):
                report = self.bank.import_csv({**self.data, 'account_label': str(changes)})
                self.engine.demo = False; self.engine.business.root = Path(self.temp.name)
                with patch('hustleai.dashboard.bank.Service') as service_class:
                    service = service_class.return_value.__enter__.return_value
                    service.api.get.return_value = {'invoice': {**self.invoices[0], **changes}}
                    with self.assertRaises(ValueError): self.bank.prepare(report['id'], 2)
                    service.proposal.assert_not_called()
                self.engine.demo = True
                # Separate each failed receipt marker for this validation test.
                with self.journal.locked():
                    state = self.journal.read(); state['bank_payments'] = {}; self.journal.write(state)

    def test_refresh_updates_live_balances_and_keeps_recorded_receipt(self):
        original = self.bank.import_csv(self.data); preview = self.bank.prepare(original['id'], 2)
        self.bank.confirm(preview['key'], 'CONFIRM ' + preview['operation_id'])
        ledger = [{**self.invoices[0], 'balance': '0', 'status': 'paid'}, self.invoices[1]]
        with patch.object(self.bank, 'ledger', return_value=(ledger, [])):
            refreshed = self.bank.refresh(original['id'])
        self.assertEqual(refreshed['transactions'][0]['status'], 'recorded_match')
        self.assertEqual(len(refreshed['outstanding']), 1)
        self.assertEqual(refreshed['outstanding'][0]['invoice_number'], 'RG-1002')
        self.assertEqual(refreshed['revision'], 1)

    def test_http_bank_routes_require_owner_authentication_and_origin(self):
        from http.server import ThreadingHTTPServer
        import threading, urllib.request, urllib.error
        from hustleai.dashboard.server import handler
        self.engine.demo = False
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.engine, 'a-secure-owner-password'))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        def code(path, data=None, headers=None):
            req = urllib.request.Request(base + path, json.dumps(data).encode() if data is not None else None, headers or {})
            try:
                with urllib.request.urlopen(req) as response:return response.status
            except urllib.error.HTTPError as error:
                with error:return error.code
        try:
            self.assertEqual(code('/api/bank/reports'), 401)
            self.assertEqual(code('/api/bank/report?id=anything'), 401)
            for path in ('extract', 'import', 'prepare', 'confirm', 'assign', 'notify', 'refresh'):
                self.assertEqual(code('/api/bank/' + path, {}, {'Origin': base}), 401)
                self.assertEqual(code('/api/bank/' + path, {}), 403)
        finally:
            server.shutdown(); server.server_close()

    def test_restart_marks_interrupted_payments_and_email_uncertain(self):
        report = self.bank.import_csv(self.data); preview = self.bank.prepare(report['id'], 2)
        with self.journal.locked():
            state = self.journal.read(); state['bank_payments'][preview['key']]['state'] = 'attempting'
            state['bank_notifications'] = {'notice': {'email': 'attempting'}}
            self.journal.write(state)
        recovered = BankReconciliation(self.engine)
        self.assertEqual(recovered.reports()['payments'][preview['key']]['state'], 'uncertain')
        self.assertIn('failed_or_uncertain', self.journal.read()['bank_notifications']['notice']['email'])
        with self.assertRaises(ValueError): recovered.confirm(preview['key'], 'CONFIRM ' + preview['operation_id'])
